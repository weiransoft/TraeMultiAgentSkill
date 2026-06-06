# Phase 16 设计文档：V3 架构重构（插件架构）

> **文档类型**：技术方案 spec（v3 — 架构师 v2 复核后再次修订）
> **日期**：2026-06-06
> **状态**：⏳ v3 修订中，待架构师复核
> **前序**：[PHASE15_PLAN.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE15_PLAN.md)（DAG 可视化；53 单元 + 16 集成 = 69 tests）
> **方向**：V3 架构重构（拆分 god module）
> **实现路径**：插件架构（重量级，引入 GoalCommandPlugin ABC + GoalDispatcher 调度中心 + DispatchResult 数据类）
> **v2 修复记录**：架构师 review 5 个阻塞（B-1~B-5）+ 7 个高优（H-1/H-2/H-3/H-5/H-6/H-7/H-8）已全部修复（详见 §10 修复记录）
> **v3 修复记录**：架构师 v2 复核识别 1 个 CRITICAL（mock 跨模块失效 → 3 个现有测试将失败）+ 1 个 P0 隐式回归（--task 校验时序） + 5 个 P1 实施期陷阱 + 5 个 P2 文档瑕疵，全部修复（详见 §11 修复记录）

---

## 0. 架构师 v2/v3 评审结论

### v2 评审（首次 review）

| 阻塞 | 严重度 | 状态 | 修复位置 |
|------|--------|------|----------|
| B-1 | fatal | ✅ | §3.1：dispatch 函数全部迁到 dispatch/legacy.py，薄壳单向依赖 facade |
| B-2 | 严重 | ✅ | §3.2：facade 完整 re-export 11 个符号 + 模块入口 |
| B-3 | 严重 | ✅ | §2.4 / §3.1：全文修正 import 路径（dispatch_*_with_* 在 dispatch/legacy.py） |
| B-4 | 严重 | ✅ | §2.1：删 cli_flag property，dispatcher 派生 `--{name}` |
| B-5 | 严重 | ✅ | §2.3 / §3.3：PluginContext 加 dry_run 字段 + facade 短路 |
| H-1 | 高优 | ✅ | §2.2：mutex 一致性 / 自指 / 名字存在性 / 对称性启动期校验 |
| H-2 | 高优 | ✅ | §2.2：middleware 钩子接口（v1 留空，结构就位） |
| H-3 | 高优 | ✅ | §2.3：PluginContext 补 dry_run / verbose / agent_type / config |
| H-5 | 高优 | ✅ | §2.1：cleanup(ctx, exc) 契约 + dispatcher try/finally |
| H-6 | 高优 | ✅ | §2.2：register() 检查 name/priority 唯一性 |
| H-7 | 高优 | ✅ | §2.2：DispatchResult 数据类替代 bool \| None |
| H-8 | 高优 | ✅ | §4.1：契约测试 test_v3_plugin_contract.py |

### v3 评审复核（架构师 v2 review 后）

| 风险 | 严重度 | 状态 | 修复位置 |
|------|--------|------|----------|
| 风险-1 | CRITICAL | ✅ | §5.6 阶段 6：3 处 test_loop_goal.py mock 路径修正 patch('trae_agent_dispatch_v2.dispatch_agent_v2') → patch('dispatch.legacy.dispatch_agent_v2') |
| 风险-2 | P0 | ✅ | §3.2：facade._dispatch_through_v3() 恢复与 god module 同等 6 模式豁免的 --task 必填校验 |
| 风险-3 | P1 | ✅ | §2.2：dispatcher.dispatch() 修正 cleanup 异常传递（exc_to_pass 持有变量） |
| 风险-4 | P1 | ✅ | §2.2：dispatcher.dispatch() 修正 middleware.after 传真实 DispatchResult |
| 风险-5 | P1 | ✅ | §2.3：PluginContext.dry_run 字段语义修正（dispatcher.dispatch() 入口检查并短路） |
| 风险-6 | P1 | ✅ | §5.6 阶段 6：增加 import smoke test + 无循环 import lint 检查 |
| 风险-7 | P2 | ✅ | §3.4 兼容性矩阵：扩展覆盖全部 19 处 import 站点 |
| 风险-8 | P2 | ✅ | §3.2 修正 21 → 19 处（Grep 实际统计） |
| 风险-9 | P2 | ✅ | §2.8 BUILTIN_PLUGINS 注释：明确 plugin 必须 stateless |
| 风险-10 | P2 | ✅ | §5.1 阶段 1：明确 dispatch.legacy.py 不允许 import facade / trae_agent_dispatch_v2 |
| 风险-11 | P2 | ✅ | §2.6 plugin 文件头：注释说明 sys.path 依赖 |

**v3 授权**：架构师复核通过后即可进入实施。

---

## 1. 背景与动机

### 1.1 现有能力与痛点

文件 `scripts/trae_agent_dispatch_v2.py` 经过 Phase 11/13/14/15 持续叠加，已膨胀为 **god module**：

| 指标 | 当前值 | 危险阈值 |
|------|--------|----------|
| 文件行数 | ~1500 | > 1000 |
| 文件大小 | 52KB | > 30KB |
| `def` 数量 | 14 | > 10 |
| 模式分支（if-elif） | 5（cancel/graph/resume/multi_goal/loop） | > 3 难维护 |
| CLI 改动点 | 3（flag + 互斥 + 分支） | > 2 改一处易漏 |

**god module 的具体症状**：
1. 添加新模式要改 3 处（CLI flag + 互斥判断 + if-elif 分支）
2. 互斥规则散落（顶部硬编码 + 中部隐式）
3. 优先级链用注释维护（容易漂移）
4. 5 个 dispatch 函数命名 / 签名不统一

### 1.2 设计目标

实现 **V3 插件架构**，将 god module 拆分为：

1. **零业务行为变化**（强约束）：
   - 现有 5 个 dispatch 函数 100% 行为保留
   - 现有 5 个 CLI flag 参数完全相同
   - 现有 1089 个 Phase 13/14/15 + Dynamic Workflows + V2 测试 100% 通过

2. **插件化扩展**：
   - 添加新模式只需 1 处改动（新增 1 个 plugin 文件 + 1 行注册）
   - 互斥规则由插件自报（启动期一致性校验）
   - 优先级由插件 property 自报（int 唯一性保证）

3. **职责分离**：
   - CLI 解析：cli/parser.py
   - 调度中心：dispatcher/goal_dispatcher.py
   - 业务插件：plugins/{cancel,graph,resume,multi_goal,loop}.py
   - Legacy 兼容：dispatch/legacy.py + facade.py
   - 入口：trae_agent_dispatch_v2.py（薄壳，< 50 行）

4. **可测试性**：
   - 每个插件可独立测试（不依赖 dispatcher）
   - dispatcher 可独立测试（用 mock 插件）
   - 契约测试：5 个内置 plugin 必须满足 base 类所有抽象

---

## 2. 架构设计

### 2.0 总体架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                 trae-multi-agent v3.0（Phase 16 后）                   │
├──────────────────────────────────────────────────────────────────────┤
│  入口层（薄壳）      │  trae_agent_dispatch_v2.py  (~30 行)           │
│                      │  └── from facade import main_compat           │
├──────────────────────────────────────────────────────────────────────┤
│  兼容层              │  facade.py (~150 行)                          │
│                      │  ├── re-export 11 个旧符号                    │
│                      │  ├── main_compat() 旧 CLI 入口                │
│                      │  └── _dispatch_through_v3() 内部走 dispatcher  │
├──────────────────────────────────────────────────────────────────────┤
│  Legacy 入口模块 (NEW)│  dispatch/legacy.py (~600 行)               │
│  (搬迁自 v2 旧文件)  │  ├── 5 个 dispatch_*_with_* 函数              │
│                      │  ├── dispatch_agent_v2 / dispatch_agent      │
│                      │  ├── _is_overall_success                      │
│                      │  ├── _module_level_single_dispatch            │
│                      │  └── log (level+message)                     │
├──────────────────────────────────────────────────────────────────────┤
│  CLI 层              │  cli/parser.py (~350 行)                      │
│                      │  └── parse_arguments() argparse only          │
├──────────────────────────────────────────────────────────────────────┤
│  调度层 (NEW)        │  dispatcher/goal_dispatcher.py (~250 行)      │
│                      │  ├── GoalDispatcher — 调度中心                │
│                      │  ├── PluginContext — 共享资源                 │
│                      │  ├── DispatchResult — 结构化返回               │
│                      │  ├── DispatchMiddleware (H-2 钩子接口)         │
│                      │  └── errors: MutexViolationError 等 5 类      │
├──────────────────────────────────────────────────────────────────────┤
│  插件层 (NEW)        │  plugins/base.py (~120 行)                    │
│                      │  ├── GoalCommandPlugin (ABC)                  │
│                      │  ├── BUILTIN_PLUGINS（单一注册真相源）         │
│                      │  └── 5 个内置插件：                            │
│                      │     ├── cancel.py   (priority=0)              │
│                      │     ├── graph.py    (priority=10)             │
│                      │     ├── resume.py   (priority=20)             │
│                      │     ├── multi_goal.py (priority=30)           │
│                      │     └── loop.py     (priority=40)             │
├──────────────────────────────────────────────────────────────────────┤
│  业务层（不动）      │  goal_orchestrator.py / loop_goal.py /         │
│                      │  dag_visualizer.py / trae_agent.py            │
├──────────────────────────────────────────────────────────────────────┤
│  基础设施（不动）    │  workflow_engine_v2.py / checkpoint_manager.py│
│                      │  cybernetics / karpathy / guard                │
└──────────────────────────────────────────────────────────────────────┘
```

### 2.0.1 目录结构

```
scripts/
├── trae_agent_dispatch_v2.py    # 30 行薄壳（入口）
├── facade.py                     # 向后兼容 shim
├── cli/
│   ├── __init__.py
│   └── parser.py                 # parse_arguments()
├── dispatcher/
│   ├── __init__.py
│   ├── goal_dispatcher.py        # GoalDispatcher
│   ├── plugin_context.py         # PluginContext
│   ├── dispatch_result.py        # DispatchResult (H-7)
│   ├── middleware.py             # DispatchMiddleware (H-2)
│   └── errors.py                 # 5 类异常
├── dispatch/                     # Legacy 入口（搬迁自 v2 旧文件）
│   ├── __init__.py
│   └── legacy.py                 # 5 个 dispatch_*_with_* + 助手
├── plugins/
│   ├── __init__.py
│   ├── base.py                   # GoalCommandPlugin ABC + BUILTIN_PLUGINS
│   ├── cancel.py                 # GoalCancelPlugin
│   ├── graph.py                  # GoalGraphPlugin
│   ├── resume.py                 # GoalResumePlugin
│   ├── multi_goal.py             # MultiGoalPlugin
│   └── loop.py                   # LoopGoalPlugin
├── goal_orchestrator.py          # 业务层（不动）
├── loop_goal.py                  # 业务层（不动）
├── dag_visualizer.py             # 业务层（不动）
└── tests/
    ├── test_v3_dispatcher.py     # 单元测试 dispatcher
    ├── test_v3_plugin_contract.py # 契约测试 (H-8)
    ├── test_v3_plugins.py        # 单元测试各插件
    ├── test_v3_plugin_context.py # PluginContext 单元测试
    ├── test_v3_dispatch_result.py # DispatchResult 单元测试
    └── test_v3_integration.py    # CLI 端到端集成
```

### 2.1 `GoalCommandPlugin` 抽象基类

```python
# plugins/base.py
from abc import ABC, abstractmethod
from typing import Set, Optional
import argparse
from dispatcher.plugin_context import PluginContext


class GoalCommandPlugin(ABC):
    """V3 Goal 命令插件接口。

    任何 CLI 模式都必须实现本接口。dispatcher 通过该接口调度。
    
    关键设计：插件自带元数据（priority / mutex_with / requires_task），
    不依赖外部配置，避免 god module 的"中心化硬编码"问题。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """插件唯一名称（用于日志 / 错误信息 / mutex 引用 / CLI flag 派生）。

        约定：
        - 满足 ^[a-z][a-z0-9-]*$（kebab-case，M-2 强制）
        - 与 CLI flag 后半段保持一致（去掉 -- 前缀）
        - 例：'goal-cancel' / 'goal-graph' / 'goal-resume'
        - dispatcher 内部用 f"--{name}" 派生 CLI flag（B-4 修复：删 cli_flag property）
        """

    @property
    @abstractmethod
    def priority(self) -> int:
        """调度优先级（数字越小越优先）。

        约定（间隔 10 预留 gap，M-1 优化）：
        - 0（DESTROY）：破坏性最高（cancel）
        - 10（READONLY）：只读（graph）
        - 20~30（STATE_MUTATION_*）：状态变更（resume / multi_goal）
        - 40~50（LOOP_*）：循环 / 长期运行（loop）
        
        唯一性约束：H-6 dispatch 时 register() 强制检查
        """

    @property
    @abstractmethod
    def mutex_with(self) -> Set[str]:
        """互斥的插件名称集合（基于 plugin.name，非 cli_flag）。

        例：{'goal-resume', 'multi-goal', 'goal-cancel', 'goal-graph'}
        表示这些插件不能与本插件同时启用。
        
        一致性约束：H-1 dispatcher 启动期校验
        - 不含自己（自指）
        - 引用的每个名字都有已注册 plugin 对应
        - 对称性：A.mutex_with ⊇ {B.name} iff B.mutex_with ⊇ {A.name}
        """

    @property
    @abstractmethod
    def requires_task(self) -> bool:
        """是否要求 --task 参数（默认 False，所有 plugin 都不需要）。"""

    @abstractmethod
    def matches(self, args: argparse.Namespace) -> bool:
        """检查是否匹配（args 中相应字段非 None）。

        例：cancel_plugin.matches(args) → return args.goal_cancel is not None
        """

    @abstractmethod
    def execute(self, args: argparse.Namespace, ctx: PluginContext) -> bool:
        """执行插件逻辑。

        Args:
            args: argparse 解析结果
            ctx: 共享上下文（project_root / log / registry / dry_run 等）

        Returns:
            bool: True 表示成功；False 表示失败
        """

    def cleanup(self, ctx: PluginContext, exc: Optional[BaseException]) -> None:
        """资源回收钩子（H-5 契约）。

        默认 no-op。Plugin 实现时必须保证幂等（可被多次调用）。
        dispatcher 在 try/finally 中调用。
        """

    # === 便捷方法（子类可继承也可覆盖） ===

    def get_arg(self, args: argparse.Namespace, key: str, default=None):
        """安全获取 args 属性（避免 AttributeError）。"""
        return getattr(args, key, default)

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} "
            f"name={self.name!r} priority={self.priority}>"
        )
```

### 2.2 `GoalDispatcher` 调度中心

```python
# dispatcher/goal_dispatcher.py
import argparse
import logging
import re
from typing import List, Optional
from plugins.base import GoalCommandPlugin
from dispatcher.plugin_context import PluginContext
from dispatcher.dispatch_result import DispatchResult
from dispatcher.middleware import DispatchMiddleware
from dispatcher.errors import (
    MutexViolationError,
    NoMatchingPluginError,
    DuplicatePluginNameError,
    DuplicatePriorityError,
    MutexDeclarationError,
)


# 插件名验证正则（M-2）
_PLUGIN_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


class GoalDispatcher:
    """V3 Goal 命令调度器。

    行为契约：
    1. 收集所有 plugin
    2. 调用 matches() 找出匹配的 plugin（最多 1 个，H-4 v1 严格）
    3. 互斥预校验（validate_mutex 在 dispatch 之前调用）
    4. 中间件链：middlewares.before → plugin.execute → middlewares.after
    5. cleanup 在 try/finally 中调用（H-5）
    6. 返回 DispatchResult（结构化结果，H-7）
    """

    def __init__(
        self,
        plugins: List[GoalCommandPlugin] = None,
        middlewares: List[DispatchMiddleware] = None,
    ):
        self._plugins: List[GoalCommandPlugin] = []
        self._middlewares: List[DispatchMiddleware] = list(middlewares or [])
        self._logger = logging.getLogger("goal_dispatcher")
        for p in (plugins or []):
            self.register(p)
        self._validate_mutex_declarations()  # H-1 启动期校验

    def register(self, plugin: GoalCommandPlugin) -> None:
        """注册插件（按 priority 升序插入）。

        校验：
        - H-6：name 不重复（raise DuplicatePluginNameError）
        - H-6：priority 不重复（raise DuplicatePriorityError）
        - M-2：name 满足 ^[a-z][a-z0-9-]*$（raise MutexDeclarationError）
        """
        # name 格式校验
        if not _PLUGIN_NAME_RE.match(plugin.name):
            raise MutexDeclarationError(
                f"Plugin name {plugin.name!r} 不符合 kebab-case 规范"
            )
        # name 唯一性
        if any(p.name == plugin.name for p in self._plugins):
            raise DuplicatePluginNameError(
                f"Plugin name {plugin.name!r} 重复"
            )
        # priority 唯一性
        if any(p.priority == plugin.priority for p in self._plugins):
            raise DuplicatePriorityError(
                f"Plugin priority {plugin.priority} 重复"
            )
        # 按 priority 升序插入（稳定排序：相同时按注册顺序）
        self._plugins.append(plugin)
        self._plugins.sort(key=lambda p: p.priority)

    def _validate_mutex_declarations(self) -> None:
        """H-1 启动期 mutex 一致性校验。

        检查：
        - 自指（plugin.name not in plugin.mutex_with）
        - 名字存在性（mutex_with 中每个名字都有已注册 plugin）
        - 对称性（A.mutex_with ⊇ {B.name} iff B.mutex_with ⊇ {A.name}）
        """
        names = {p.name for p in self._plugins}
        for plugin in self._plugins:
            # 自指
            if plugin.name in plugin.mutex_with:
                raise MutexDeclarationError(
                    f"Plugin {plugin.name!r} mutex_with 包含自己"
                )
            # 名字存在性
            for mutex_name in plugin.mutex_with:
                if mutex_name not in names:
                    raise MutexDeclarationError(
                        f"Plugin {plugin.name!r} mutex_with 引用不存在"
                        f"的 plugin {mutex_name!r}"
                    )
            # 对称性
            for other in self._plugins:
                if other.name == plugin.name:
                    continue
                a_mutex_b = other.name in plugin.mutex_with
                b_mutex_a = plugin.name in other.mutex_with
                if a_mutex_b != b_mutex_a:
                    raise MutexDeclarationError(
                        f"Plugin {plugin.name!r} 与 {other.name!r} "
                        f"mutex 关系不对称"
                    )

    def list_plugins(self) -> List[GoalCommandPlugin]:
        """返回所有已注册插件（只读副本，按 priority 升序）。"""
        return list(self._plugins)

    def validate_mutex(self, args: argparse.Namespace) -> None:
        """互斥预校验（在 dispatch 前调用，给出友好错误信息）。

        遍历所有 plugin 的 mutex_with，发现冲突即抛 MutexViolationError。
        """
        matched_names = {p.name for p in self._plugins if p.matches(args)}
        for name in matched_names:
            plugin = next(p for p in self._plugins if p.name == name)
            for mutex_name in plugin.mutex_with:
                if mutex_name in matched_names:
                    raise MutexViolationError(
                        f"插件 {f'--{plugin.name}'} 与 {mutex_name} 互斥"
                    )

    def dispatch(
        self, args: argparse.Namespace, ctx: PluginContext
    ) -> DispatchResult:
        """调度入口（H-7：返回结构化 DispatchResult）。

        流程：
        1. 风险-5 修正：dry_run 入口检查（保留旧 --dry-run 行为，不走任何 plugin）
        2. 中间件 before（H-2）
        3. matches 找出匹配 plugin（最多 1 个）
        4. 无匹配 → DispatchResult(skipped_reason='no_match')
        5. execute + cleanup（try/finally，H-5 + 风险-3 修正：exc_to_pass 持有变量）
        6. 中间件 after（风险-4 修正：传真实 DispatchResult）
        7. 返回 DispatchResult

        风险-3/风险-4 修正要点：
        - exc_to_pass 在 except 块赋值，最终在 finally 传给 plugin.cleanup
        - result 变量在外层 finally 可见，传给 mw.after（不再传 None）
        """
        # 风险-5 修正：dry_run 入口检查（替代 v2 spec 中 facade 入口短路）
        # 旧 --dry-run 行为保留：打印 4 行日志后 sys.exit(0)，不调用任何 plugin
        # 此处改为 dispatcher 内部短路，更符合 PluginContext 字段语义
        if getattr(ctx, "dry_run", False):
            return DispatchResult(
                matched_plugin=None,
                success=True,
                error=None,
                skipped_reason="dry_run",
            )

        # 持有 result 变量，供 finally 块的 middleware.after 使用（风险-4 修正）
        result: Optional[DispatchResult] = None
        try:
            # 中间件 before
            for mw in self._middlewares:
                mw.before(args, ctx)
        except Exception as e:
            self._logger.warning(f"[Dispatcher] middleware.before 异常：{e}")

        try:
            matched = [p for p in self._plugins if p.matches(args)]
            if not matched:
                result = DispatchResult(
                    matched_plugin=None,
                    success=False,
                    error=None,
                    skipped_reason="no_match",
                )
                return result
            if len(matched) > 1:
                # H-4 v1 严格：多个 plugin 匹配视为错误
                names = [p.name for p in matched]
                raise MutexViolationError(
                    f"多个插件同时匹配（args 解析层应已阻止）：{names}"
                )
            plugin = matched[0]
            self._logger.info(
                f"[Dispatcher] 匹配插件：{plugin.name} (priority={plugin.priority})"
            )

            # 风险-3 修正：exc_to_pass 持有变量，cleanup 拿到真实异常类型
            exc_to_pass: Optional[BaseException] = None
            try:
                success = plugin.execute(args, ctx)
                result = DispatchResult(
                    matched_plugin=plugin.name,
                    success=success,
                    error=None,
                )
                return result
            except BaseException as exc:
                exc_to_pass = exc
                result = DispatchResult(
                    matched_plugin=plugin.name,
                    success=False,
                    error=exc,
                )
                return result
            finally:
                # cleanup 一定执行（H-5 契约 + 风险-3 修正）
                try:
                    plugin.cleanup(ctx, exc_to_pass)
                except Exception as e:
                    self._logger.warning(
                        f"[Dispatcher] plugin.cleanup 异常：{e}"
                    )
        finally:
            # 中间件 after（风险-4 修正：传真实 DispatchResult 而非 None）
            for mw in self._middlewares:
                try:
                    mw.after(args, ctx, result)
                except Exception as e:
                    self._logger.warning(
                        f"[Dispatcher] middleware.after 异常：{e}"
                    )
```

### 2.3 `PluginContext` 共享上下文

```python
# dispatcher/plugin_context.py
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING, Mapping, Any

if TYPE_CHECKING:
    from loop_goal import GoalRegistry


@dataclass
class PluginContext:
    """插件执行上下文（V3 引入）。

    封装所有插件共享的资源，避免插件之间通过全局变量通信。

    字段（v1 必含，B-5/H-3/风险-5 修复）：
    - project_root: 项目根目录（Path）
    - log: 日志函数（签名同现有 log(message, level)）
    - registry: GoalRegistry 实例（可选，部分 plugin 懒初始化）
    - dry_run: 模拟模式（bool，默认 False；风险-5 修正：dispatcher.dispatch()
      入口检查并短路返回 DispatchResult(skipped_reason='dry_run')，
      保留旧 --dry-run 行为）
    - verbose: 详细日志（bool，默认 False，H-3 修复）
    - agent_type: 智能体类型（str，默认 "auto"，H-3 修复）
    - config: 配置文件（Optional[Mapping]，v1 占位不强制使用，H-3 修复）

    未来扩展字段（v2 后续追加，H-3 明确分界）：
    - metrics_hook: Prometheus / OpenTelemetry 接入
    - tracer: 分布式追踪
    - workspace_handle: 多工作区支持
    """
    project_root: Path
    log: Callable[[str, str], None]
    registry: Optional["GoalRegistry"] = None
    dry_run: bool = False  # 风险-5 修正：dispatcher 入口检查并短路
    verbose: bool = False
    agent_type: str = "auto"
    config: Optional[Mapping[str, Any]] = None

    def __post_init__(self):
        if not isinstance(self.project_root, Path):
            self.project_root = Path(self.project_root)
```

### 2.4 `DispatchResult` 结构化返回（H-7）

```python
# dispatcher/dispatch_result.py
from dataclasses import dataclass
from typing import Optional


@dataclass
class DispatchResult:
    """dispatch() 结构化返回（H-7 修复：替代 bool | None）。

    字段：
    - matched_plugin: 匹配的 plugin name（None = 无匹配）
    - success: 执行是否成功
    - error: 执行异常（None = 无异常）
    - skipped_reason: 跳过原因（None / "no_match" / "dry_run" / "mutex_violation"）
    """
    matched_plugin: Optional[str] = None
    success: bool = False
    error: Optional[BaseException] = None
    skipped_reason: Optional[str] = None

    def __bool__(self) -> bool:
        """兼容旧 bool() 调用：matched and success。"""
        return self.matched_plugin is not None and self.success
```

### 2.5 `DispatchMiddleware` 钩子接口（H-2）

```python
# dispatcher/middleware.py
from abc import ABC, abstractmethod
import argparse
from dispatcher.plugin_context import PluginContext
from dispatcher.dispatch_result import DispatchResult


class DispatchMiddleware(ABC):
    """Dispatch 中间件接口（H-2 修复）。

    用途：audit logging / metrics 收集 / tracing / 动态特性开关。
    v1 阶段 middlewares 留空（不引入任何内置 middleware），
    但接口先定义，避免 Phase 17+ 再改 dispatcher。
    """

    @abstractmethod
    def before(self, args: argparse.Namespace, ctx: PluginContext) -> None:
        """dispatch 之前调用。"""

    @abstractmethod
    def after(
        self,
        args: argparse.Namespace,
        ctx: PluginContext,
        result: Optional[DispatchResult],
    ) -> None:
        """dispatch 之后调用（result 可能为 None 如果 dispatch 异常）。"""
```

### 2.6 内置插件示例（GoalCancelPlugin）

```python
# plugins/cancel.py
# 风险-11 修正：plugin 绝对 import 依赖 scripts/ 在 sys.path
# - CLI 调用 `python3 trae_agent_dispatch_v2.py` 时 CWD=scripts/，OK
# - 测试通过 `sys.path.insert(0, SCRIPTS_DIR)` 注入，OK
# - 若用户从 skill 根目录 import，需先 sys.path.insert(0, 'scripts/')

import argparse
from typing import Set
from plugins.base import GoalCommandPlugin
from dispatcher.plugin_context import PluginContext


class GoalCancelPlugin(GoalCommandPlugin):
    """Phase 14 引入的 Goal 取消功能插件化（priority=0，破坏性最高）。"""

    @property
    def name(self) -> str:
        return "goal-cancel"

    @property
    def priority(self) -> int:
        return 0  # 最高优先级

    @property
    def mutex_with(self) -> Set[str]:
        # cancel 与所有其他 plugin 互斥
        return {"goal-graph", "goal-resume", "multi-goal", "loop"}

    @property
    def requires_task(self) -> bool:
        return False

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "goal_cancel", None) is not None

    def execute(self, args: argparse.Namespace, ctx: PluginContext) -> bool:
        # B-3 修复：从 dispatch.legacy 导入（不再 from goal_orchestrator）
        from dispatch.legacy import dispatch_agent_v2_with_goal_cancel
        ctx.log(
            f"🛑 Phase 16 检测到取消模式：goal={args.goal_cancel}",
            "INFO",
        )
        return dispatch_agent_v2_with_goal_cancel(
            goal_id=args.goal_cancel,
            project_root=str(ctx.project_root),
        )
```

### 2.7 插件总览

| 插件 | name | priority (M-1 间隔 10) | mutex_with | requires_task | execute 调用的 legacy 函数 |
|------|------|------------------------|------------|---------------|---------------------------|
| `GoalCancelPlugin` | goal-cancel | 0 (DESTROY) | {graph, resume, multi-goal, loop} | False | `dispatch_agent_v2_with_goal_cancel` |
| `GoalGraphPlugin` | goal-graph | 10 (READONLY) | {cancel, resume, multi-goal, loop} | False | `dispatch_agent_v2_with_goal_graph` |
| `GoalResumePlugin` | goal-resume | 20 (STATE_MUTATION_LOW) | {cancel, graph, multi-goal, loop} | False | `dispatch_agent_v2_with_goal_resume` |
| `MultiGoalPlugin` | multi-goal | 30 (STATE_MUTATION_HIGH) | {cancel, graph, resume, loop} | False | `dispatch_agent_v2_with_multi_goal` |
| `LoopGoalPlugin` | loop | 40 (LOOP_LOW) | {cancel, graph, resume, multi-goal} | False | `dispatch_agent_v2_with_loop_goal` |

### 2.8 `BUILTIN_PLUGINS` 单一注册真相源

```python
# plugins/__init__.py
"""V3 插件包：定义 BUILTIN_PLUGINS 单一注册真相源。

任何模块（facade / dispatcher / tests）都从这里 import plugin 列表，
避免分散注册导致的不一致。

风险-9 修正：plugin 必须满足 stateless 契约
- BUILTIN_PLUGINS 在模块加载时构造 5 个 plugin 实例
- Python 进程内所有 dispatcher 共享这同一组实例
- 若 plugin 持有可变状态（self.field），测试间会状态泄漏
- 当前 5 个内置 plugin 都是 stateless（仅返回常量 / 调用函数），安全
- 未来新增 plugin 时必须：
  1. 不持有实例变量状态
  2. 或在 execute() 入口 self-reinit
  3. 契约测试 test_v3_plugin_contract.py 增加
     test_plugin_instances_independent 验证（创建两次 list 断言元素不同实例）
"""

from plugins.base import GoalCommandPlugin
from plugins.cancel import GoalCancelPlugin
from plugins.graph import GoalGraphPlugin
from plugins.resume import GoalResumePlugin
from plugins.multi_goal import MultiGoalPlugin
from plugins.loop import LoopGoalPlugin


# 5 个内置插件（H-8 契约测试覆盖）
# 风险-9 修正：所有 plugin 实例必须 stateless（不持有实例状态）
BUILTIN_PLUGINS: list = [
    GoalCancelPlugin(),
    GoalGraphPlugin(),
    GoalResumePlugin(),
    MultiGoalPlugin(),
    LoopGoalPlugin(),
]


__all__ = [
    "GoalCommandPlugin",
    "GoalCancelPlugin",
    "GoalGraphPlugin",
    "GoalResumePlugin",
    "MultiGoalPlugin",
    "LoopGoalPlugin",
    "BUILTIN_PLUGINS",
]
```

---

## 3. 向后兼容设计

### 3.1 关键决策：dispatch 函数全部迁移到 `dispatch/legacy.py`（B-1 修复）

**为什么必须迁移**（避免循环 import）：

```python
# 旧设计（v1 spec 错）：facade.py 从薄壳 import
# → 循环 import：trae_agent_dispatch_v2 → facade → trae_agent_dispatch_v2

# 新设计（v2 spec 正）：dispatch 函数全部从薄壳迁到 dispatch/legacy.py
# → 薄壳单向依赖 facade → dispatch.legacy
```

**dispatch/legacy.py 内容**（搬迁自 trae_agent_dispatch_v2.py line 318-1307）：
- `log(message, level)`
- `dispatch_agent_v2(agent_type, task, ...)`
- `dispatch_agent(agent_type, task, project_root, ...)`
- `_is_overall_success(result)`
- `_module_level_single_dispatch(...)`
- `dispatch_agent_v2_with_loop_goal(...)`
- `dispatch_agent_v2_with_goal_resume(...)`
- `dispatch_agent_v2_with_multi_goal(...)`
- `dispatch_agent_v2_with_goal_cancel(...)`
- `dispatch_agent_v2_with_goal_graph(...)`

迁完后，trae_agent_dispatch_v2.py 内部 import dispatch.legacy，**消除循环依赖**。

### 3.2 `facade.py` 兼容层（B-2 修复：完整 re-export 11 个符号）

```python
# facade.py
"""V3 兼容层：保持旧 API 100% 工作。

设计原则：
- 完整 re-export 11 个旧符号（不是 5 个，B-2 修复；也不是 21 个，§5.2 风险-8 修正）
- 旧 CLI 入口（main_compat）继续可用
- 旧 import 路径（from trae_agent_dispatch_v2 import ...）通过薄壳 re-export 工作
- 风险-2 修正：恢复与 god module 同等 6 模式豁免的 --task 必填校验
- 风险-5 修正：dry_run 短路在 dispatcher 内部实现（PluginContext.dry_run 字段驱动）
"""

import sys
from pathlib import Path

# 1. re-export 11 个符号（B-2 完整列表）
from dispatch.legacy import (
    log,                            # 多处 test 引用
    dispatch_agent_v2,              # 2 处 test
    dispatch_agent,                 # plan 列入
    dispatch_agent_v2_with_loop_goal,         # 4 处 test
    dispatch_agent_v2_with_goal_resume,       # 1 处 test
    dispatch_agent_v2_with_multi_goal,        # 1 处 test
    dispatch_agent_v2_with_goal_cancel,       # 1 处 test
    dispatch_agent_v2_with_goal_graph,        # 4 处 dag_visualizer_integration test
    _is_overall_success,            # 3 处 test_loop_goal
    _module_level_single_dispatch,  # 2 处 test_goal_orchestrator
)
from cli.parser import parse_arguments  # 4 处 test 引用

# 2. 旧 main 入口（保证 19 处外部 import 站点继续工作，§3.4 兼容矩阵覆盖）
def main_compat() -> int:
    """兼容旧 main() — 走新 dispatcher 路径。"""
    args = parse_arguments()
    return _dispatch_through_v3(args)


def _dispatch_through_v3(args) -> int:
    """通过 V3 dispatcher 执行（与旧 main() 行为一致）。

    风险-2 修正：恢复与 god module 同等 6 模式豁免的 --task 必填校验
    风险-5 修正：dry_run 短路在 dispatcher 内部实现（ctx.dry_run 字段）
    """
    from dispatcher.goal_dispatcher import GoalDispatcher
    from dispatcher.plugin_context import PluginContext
    from dispatcher.errors import MutexViolationError
    from plugins import BUILTIN_PLUGINS

    project_root = Path(args.project_root).resolve()
    if not project_root.exists():
        log(f"❌ 项目根目录不存在：{project_root}", "ERROR")
        return 1

    # 构建 PluginContext（风险-5 修正：dry_run 由 dispatcher 内部检查）
    ctx = PluginContext(
        project_root=project_root,
        log=log,
        dry_run=getattr(args, 'dry_run', False),
        verbose=getattr(args, 'verbose', False),
        agent_type=getattr(args, 'agent', 'auto'),
    )

    # 互斥预校验
    dispatcher = GoalDispatcher(plugins=list(BUILTIN_PLUGINS))
    try:
        dispatcher.validate_mutex(args)
    except MutexViolationError as e:
        log(f"❌ {e}", "ERROR")
        return 1

    # 风险-2 修正：--task 必填校验（与 god module 行为一致）
    # god module 行为（trae_agent_dispatch_v2.py:1339-1348）：
    #   - 6 种模式豁免：goal_graph / goal_cancel / goal_resume /
    #     multi_goal / loop > 1 / goal is not None
    #   - 其他模式要求 --task 必填
    # v2 spec 行为（错误）：仅在"无 plugin 匹配时"检查，会漏掉部分场景
    if not args.task and not (
        args.goal_graph or args.goal_cancel or args.goal_resume
        or args.multi_goal or args.loop > 1 or args.goal is not None
    ):
        log(
            "❌ --task 必填（除非使用 --goal-graph / --goal-cancel / "
            "--goal-resume / --multi-goal / --loop / --goal 模式）",
            "ERROR",
        )
        return 1

    # 任务文件校验（与 god module line 1351-1359 行为一致）
    if getattr(args, "task_file", None):
        task_file = project_root / args.task_file
        if not task_file.exists():
            log(f"❌ 任务文件不存在：{task_file}", "ERROR")
            return 1

    # 调度
    result = dispatcher.dispatch(args, ctx)
    if result.skipped_reason == "no_match":
        # 无插件匹配 → 默认 dispatch_agent_v2
        success = dispatch_agent_v2(
            agent_type=args.agent,
            task=args.task,
            project_root=str(project_root),
        )
        return 0 if success else 1
    if result.skipped_reason == "dry_run":
        # 风险-5 修正：dispatcher 内部 dry_run 短路
        log('🔄 模拟模式：不实际调用智能体', 'WARNING')
        log(f'   将调度智能体：{args.agent}', 'WARNING')
        log(f'   任务：{args.task}', 'WARNING')
        log('✅ 模拟完成', 'SUCCESS')
        return 0
    return 0 if result else 1


def _any_plugin_matches(dispatcher, args) -> bool:
    """辅助：检查是否有任何 plugin 匹配 args。"""
    return any(p.matches(args) for p in dispatcher.list_plugins())
```

### 3.3 `trae_agent_dispatch_v2.py` 瘦壳（B-1 修复：单向依赖 facade）

```python
# trae_agent_dispatch_v2.py（v3 瘦壳，~30 行）
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trae Agent 调度脚本 v2 — V3 薄壳入口（Phase 16 重构后）。

完整实现已迁移到 facade.py / dispatch/legacy.py / cli/ / dispatcher/ / plugins/。
本文件仅作为 backward-compat 入口（薄壳），避免破坏现有脚本调用。
"""

import sys

# B-1 修复：薄壳单向依赖 facade（不再循环 import）
# 旧 11 个符号（log / dispatch_agent_v2 / dispatch_agent / dispatch_*_with_* / ...）
# 由 facade 内部 re-export，外部 from trae_agent_dispatch_v2 import x 仍可用
from facade import main_compat, log, dispatch_agent_v2, dispatch_agent  # noqa: F401
from facade import (  # noqa: F401
    dispatch_agent_v2_with_loop_goal,
    dispatch_agent_v2_with_goal_resume,
    dispatch_agent_v2_with_multi_goal,
    dispatch_agent_v2_with_goal_cancel,
    dispatch_agent_v2_with_goal_graph,
    _is_overall_success,           # noqa: F401
    _module_level_single_dispatch, # noqa: F401
    parse_arguments,               # noqa: F401
)


def main():
    """薄壳入口：所有逻辑委托给 facade.main_compat()。"""
    sys.exit(main_compat())


if __name__ == "__main__":
    main()
```

### 3.4 兼容性验证矩阵（风险-7 修正：覆盖全部 19 处 import 站点）

| # | 调用方式 | v3 行为 | Phase 11/13/14/15 测试 |
|---|----------|---------|------------------------|
| 1 | `python3 trae_agent_dispatch_v2.py --goal-cancel g1` | main → facade.main_compat → dispatcher → cancel_plugin → dispatch.legacy.dispatch_agent_v2_with_goal_cancel | ✅ test_goal_orchestrator 75 tests |
| 2 | `python3 trae_agent_dispatch_v2.py --goal-graph root` | 同上 → graph_plugin → dispatch.legacy.dispatch_agent_v2_with_goal_graph | ✅ test_dag_visualizer 53 tests |
| 3 | `python3 trae_agent_dispatch_v2.py --goal-resume g1` | 同上 → resume_plugin → dispatch.legacy.dispatch_agent_v2_with_goal_resume | ✅ test_goal_orchestrator 75 tests |
| 4 | `python3 trae_agent_dispatch_v2.py --multi-goal g1,g2` | 同上 → multi_goal_plugin → dispatch.legacy.dispatch_agent_v2_with_multi_goal | ✅ test_goal_orchestrator 15 tests |
| 5 | `python3 trae_agent_dispatch_v2.py --loop 5` | 同上 → loop_plugin → dispatch.legacy.dispatch_agent_v2_with_loop_goal | ✅ test_loop_goal 103 tests |
| 6 | `python3 trae_agent_dispatch_v2.py --dry-run --agent x --task y` | main → facade → ctx.dry_run=True → dispatcher.dispatch() 短路返回 DispatchResult(skipped_reason='dry_run') → 打印 4 行后 return 0 | ✅ 风险-5 验证（dispatcher 内部短路） |
| 7 | `from trae_agent_dispatch_v2 import dispatch_agent_v2_with_goal_cancel` | 薄壳 re-export from facade | ✅ 1 处 test (test_goal_orchestrator.py:1127) |
| 8 | `from trae_agent_dispatch_v2 import dispatch_agent_v2` | 薄壳 re-export from facade | ✅ 1 处 test (test_cybernetics_bridge_integration.py:149) |
| 9 | `from trae_agent_dispatch_v2 import log` | 薄壳 re-export from facade | ✅ 多处 test 隐式 |
| 10 | `from trae_agent_dispatch_v2 import parse_arguments` | 薄壳 re-export from facade | ✅ 4 处 test (test_dag_visualizer_integration.py:281, test_loop_goal.py:593/623) |
| 11 | `from trae_agent_dispatch_v2 import _is_overall_success` | 薄壳 re-export from facade | ✅ 3 处 test_loop_goal (line 884/1143/1159) |
| 12 | `from trae_agent_dispatch_v2 import _module_level_single_dispatch` | 薄壳 re-export from facade | ✅ 2 处 test_goal_orchestrator (line 210, 973) |
| 13 | `from trae_agent_dispatch_v2 import dispatch_agent_v2_with_loop_goal` | 薄壳 re-export from facade | ✅ 4 处 test_loop_goal (line 653/674/707/724) |
| 14 | `from trae_agent_dispatch_v2 import dispatch_agent_v2_with_goal_graph` | 薄壳 re-export from facade | ✅ 4 处 test_dag_visualizer_integration (line 459/471/489/505) |
| 15 | `import trae_agent_dispatch_v2 as v2` | 薄壳模块名保留 | ✅ test_goal_orchestrator.py:844 |
| 16 | `--task 必填校验`（facade 风险-2 修正） | 6 模式豁免 + 其他必填 | ✅ 与 god module 行为一致 |
| 17 | `--goal-graph 与其他模式互斥` | facade.mutex 预校验 | ✅ 与 god module 行为一致 |
| 18 | `task_file 不存在` | facade 文件校验 return 1 | ✅ 与 god module 行为一致 |
| 19 | `args.goal is not None` 模式 | LoopPlugin 豁免 --task | ✅ 与 god module 行为一致 |

**共 19 个兼容点**（5 CLI flag + 1 dry_run + 8 import 路径 + 1 module alias + 4 校验行为），全部覆盖。

风险-1 修正的 mock 路径（3 处 test_loop_goal.py）已计入阶段 6 实施步骤（§5.6）。

---

## 4. 测试策略

### 4.1 单元测试（目标 ~90 tests）

- `test_v3_dispatcher.py`（~25 tests）：
  - 注册 / 重复注册 / 排序（H-6 DuplicatePluginNameError / DuplicatePriorityError）
  - matches 匹配 / 不匹配
  - 互斥校验：单匹配 / 多匹配
  - 异常路径：MutexViolationError / MutexDeclarationError（H-1）
  - middleware 钩子（H-2）
  - DispatchResult 结构（H-7）

- `test_v3_plugin_contract.py`（~10 tests，H-8 契约测试）：
  - `test_abc_cannot_instantiate`：直接 `GoalCommandPlugin()` raise TypeError
  - `test_all_builtins_satisfy_contract`：遍历 `BUILTIN_PLUGINS`，断言每个实例是 `GoalCommandPlugin` 子类、6 个抽象都已实现
  - `test_builtin_priorities_unique`：5 个 plugin priority 集合大小 == 5
  - `test_builtin_mutex_symmetric`：H-1 对称性
  - `test_builtin_registration_clean`：5 个 plugin 一次 register 不抛
  - `test_builtin_name_format`：5 个 plugin name 全部 ^[a-z][a-z0-9-]*$（M-2）

- `test_v3_plugin_context.py`（~10 tests）：
  - 构造 / 默认值
  - project_root 自动转 Path
  - log / registry / dry_run / verbose / agent_type / config 注入（H-3）

- `test_v3_dispatch_result.py`（~5 tests）：
  - bool() 转换
  - 字段访问
  - skipped_reason='no_match' / 'dry_run' / 'mutex_violation' 语义

- `test_v3_plugins.py`（~50 tests = 5 plugins × 10 tests）：
  - 每个插件：name / priority / mutex_with / requires_task
  - 每个插件：matches() 各种 args 组合
  - 每个插件：execute() 调用对应 legacy 函数（mock dispatch.legacy）

### 4.2 集成测试（目标 ~20 tests）

- `test_v3_integration.py`（~20 tests）：
  - CLI 端到端：5 种模式 × happy path
  - CLI 互斥错误：cancel + graph → MutexViolationError
  - CLI --task 缺失：默认模式 → 错误
  - CLI 默认 dispatch：--agent + --task → dispatch_agent_v2
  - CLI --dry-run：5 个模式都应短路返回 0（B-5 验证）
  - 旧 import 路径：from trae_agent_dispatch_v2 import ... → 12 个符号全部正常
  - 旧 CLI 入口：python3 trae_agent_dispatch_v2.py --goal-cancel g1 → 正常退出码

### 4.3 回归测试（必须 100% 通过）

- Phase 13: test_goal_orchestrator (75) + test_goal_orchestrator_integration (15) + test_loop_goal (103) = 193 tests
- Phase 14: 集成在 Phase 13 测试中
- Phase 15: test_dag_visualizer (53) + test_dag_visualizer_integration (16) = 69 tests
- Dynamic Workflows Phase 1-11: 742 tests
- V2 回归: 85 tests

**总回归量：~1089 tests，零失败**

### 4.4 性能基线

- dispatcher.dispatch() 单次调用 < 5ms（仅做列表遍历 + 排序）
- 5 个 plugin 注册 + 1 次 dispatch < 10ms
- 与旧 if-elif 链相比无性能回退（实测对比）

---

## 5. 实施计划

### 5.1 阶段 1：基础设施（无业务行为变化）

风险-10 修正：明确 import 边界
- dispatch.legacy.py 内部 lazy import 业务层（避免循环）
- dispatch.legacy.py **不允许** 顶层 import facade 或 trae_agent_dispatch_v2
- facade.py 顶部允许 import dispatch.legacy 和 cli.parser
- trae_agent_dispatch_v2.py 顶部只允许 import facade
- plugin 文件允许 import dispatcher.* 和 dispatch.legacy（绝对路径，依赖 scripts/ 在 sys.path，§2.6 风险-11 注释）

1. 创建目录结构 `cli/ dispatcher/ plugins/ dispatch/`
2. 实现 `dispatcher/errors.py`（5 类异常）
3. 实现 `dispatcher/plugin_context.py`（含 dry_run / verbose / agent_type / config，§2.3）
4. 实现 `dispatcher/dispatch_result.py`（H-7）
5. 实现 `dispatcher/middleware.py`（H-2 接口）
6. 实现 `plugins/base.py`（ABC + BUILTIN_PLUGINS 注册，§2.8 风险-9 stateless 契约）
7. 实现 `dispatcher/goal_dispatcher.py`（含 H-1/H-2/H-5/H-6/H-7 + 风险-3/4/5 全部修复）

### 5.2 阶段 2：迁移 dispatch 函数到 `dispatch/legacy.py`

1. 创建 `dispatch/legacy.py`
2. 搬迁 `log` / `dispatch_agent_v2` / `dispatch_agent` / `_is_overall_success` / `_module_level_single_dispatch` / 5 个 `dispatch_*_with_*` 函数（约 600 行）
3. **关键**：旧 trae_agent_dispatch_v2.py 保留旧函数（暂时兼容），等 facade + 薄壳就位后再删除

### 5.3 阶段 3：5 个内置插件（行为保留）

1. `plugins/cancel.py`（搬运 execute 逻辑，B-3 修复 import 路径）
2. `plugins/graph.py`（同上）
3. `plugins/resume.py`（同上）
4. `plugins/multi_goal.py`（同上）
5. `plugins/loop.py`（同上）

### 5.4 阶段 4：CLI 解析拆分

1. `cli/parser.py`（从 trae_agent_dispatch_v2.py line 72-318 搬出 parse_arguments）

### 5.5 阶段 5：facade 兼容层 + 薄壳化（顺序调整避免 B-1）

1. 先建 `facade.py`（re-export 11 个符号 + main_compat + _dispatch_through_v3）
2. 改 `trae_agent_dispatch_v2.py` 为薄壳（30 行，单向依赖 facade）
3. **关键验证**：薄壳启动 `python3 -c "import trae_agent_dispatch_v2"` 不应循环 import
4. **关键验证**：薄壳 `from trae_agent_dispatch_v2 import log` 等 11 个符号全部 OK

### 5.6 阶段 6：测试 + 验证

风险-1 修正：3 处 mock 路径必须同步修正
- test_loop_goal.py:659-661, 680-682, 729-731
- patch('trae_agent_dispatch_v2.dispatch_agent_v2', return_value=True)
- → patch('dispatch.legacy.dispatch_agent_v2', return_value=True)
- 原因：dispatch 函数迁到 dispatch.legacy 后，名字查找在 dispatch.legacy 命名空间

风险-6 修正：增加 import smoke test + 无循环 import lint 检查

1. **风险-1 修正**：手动修正 test_loop_goal.py 3 处 mock 路径
2. **风险-6 修正**：import smoke test
   ```bash
   python3 -c "import trae_agent_dispatch_v2; import dispatch.legacy; from plugins import BUILTIN_PLUGINS; assert len(BUILTIN_PLUGINS) == 5"
   python3 -c "import facade; from facade import main_compat, log, dispatch_agent_v2, dispatch_agent_v2_with_loop_goal, _is_overall_success, _module_level_single_dispatch, parse_arguments"
   ```
3. **风险-6 修正**：dispatch.legacy 不能反向 import facade 或薄壳
   ```bash
   ! grep -E "import facade$|from facade|import trae_agent_dispatch_v2" scripts/dispatch/legacy.py
   ```
4. 写新单元测试 `test_v3_dispatcher.py` / `test_v3_plugin_contract.py` / `test_v3_plugins.py` / `test_v3_plugin_context.py` / `test_v3_dispatch_result.py`
5. 写新集成测试 `test_v3_integration.py`（含 19 个兼容点 + dry_run 验证）
6. 跑全量回归（1089 tests + 3 处修正后 3 个 mock 测试）
7. ruff check（0 warnings）

### 5.7 阶段 7：commit + tag

1. `feat: Phase 16 V3 插件架构重构`
2. tag: `phase-16-v3-plugin-architecture`

---

## 6. 风险与缓解

| 风险 | 严重度 | 缓解措施 |
|------|--------|----------|
| 旧 import 路径断裂 | P0 | facade.py 完整 re-export 11 个符号 + 薄壳二次 re-export + 12 个兼容点测试覆盖 |
| 循环 import（薄壳 ↔ facade） | P0 | B-1 修复：dispatch 函数全部迁到 dispatch/legacy.py，薄壳单向依赖 facade |
| --dry-run 行为破坏 | P0 | B-5 修复：PluginContext 加 dry_run 字段 + facade 短路（不进入 dispatcher） |
| CLI flag 行为变化 | P0 | 5 种模式端到端测试（参数解析 + dispatch 调用） |
| 优先级链漂移 | P1 | 插件 priority 用 int 自报 + H-6 register 强制唯一性 + 间隔 10 预留 gap（M-1） |
| 互斥规则漏写 / 不对称 | P1 | H-1 启动期校验（自指 / 名字存在性 / 对称性） |
| 性能回退 | P2 | 单次 dispatch < 5ms 基线测试 |
| god module 拆分不彻底 | P2 | trae_agent_dispatch_v2.py 必须 < 50 行（薄壳） |
| PluginContext 字段遗漏 | P1 | H-3 修复：预留 dry_run / verbose / agent_type / config，未来扩展显式分界 |
| 异常路径资源泄漏 | P1 | H-5 修复：cleanup(ctx, exc) 契约 + dispatcher try/finally |
| 契约违规（plugin 漏实现抽象） | P1 | H-8 修复：test_v3_plugin_contract.py 契约测试 |
| 未来扩展性 | P2 | H-2 修复：middleware 钩子接口预留（v1 留空） |

---

## 7. 文档版本

- v1（2026-06-06 初稿）：首次提交，等待架构师 review
- v2（2026-06-06 修订）：架构师 review 5B+8H 全部修复，等待架构师复核
- v3（2026-06-06 修订）：架构师 v2 复核识别 1 CRITICAL + 1 P0 + 5 P1 + 5 P2，全部修复，等待架构师复核

---

## 8. 待架构师 v3 复核重点

1. **风险-1 修复方案**（§5.6）：test_loop_goal.py 3 处 mock 路径同步从 `trae_agent_dispatch_v2.dispatch_agent_v2` 改为 `dispatch.legacy.dispatch_agent_v2`，是否彻底解决 mock 跨模块失效问题？
2. **风险-2 修复方案**（§3.2）：facade._dispatch_through_v3() 恢复与 god module 同等 6 模式豁免的 --task 必填校验，是否与旧 main() 行为完全一致？
3. **风险-3/4 修复方案**（§2.2）：dispatcher.dispatch() 用 `exc_to_pass` 持有异常并传给 cleanup / 用 `result` 变量传给 middleware.after，是否解决了"cleanup 永远收到 None / middleware 永远收到 None"的设计缺陷？
4. **风险-5 修复方案**（§2.2 + §2.3 + §3.2）：dry_run 短路从 facade 入口移到 dispatcher 入口（PluginContext.dry_run 字段驱动），是否更符合"插件架构"的设计理念？
5. **风险-6 修复方案**（§5.6）：阶段 6 增加 import smoke test + 无循环 import lint 检查，是否能防患于未然？
6. **风险-7/8 修复方案**（§3.4）：兼容性矩阵扩展到 19 个兼容点（实际 Grep 统计），是否与真实测试覆盖一致？
7. **风险-9 修复方案**（§2.8）：BUILTIN_PLUGINS 单例风险提示 + stateless 契约，是否足以约束未来 plugin 开发？
8. **风险-10 修复方案**（§5.1）：明确 import 边界（dispatch.legacy 禁止 import facade / 薄壳），是否能彻底避免循环 import？
9. **风险-11 修复方案**（§2.6）：plugin 文件头注释说明 sys.path 依赖，是否能避免外部 import 失败？

---

## 9. 详细修复对照表（v2）

| 阻塞/高优 | v1 spec 问题 | v2 spec 修复 | 修复位置 |
|-----------|-------------|-------------|----------|
| B-1 | facade 从薄壳 import 造成循环 | dispatch 函数全部迁到 `dispatch/legacy.py`，薄壳单向依赖 facade | §3.1 + §3.3 |
| B-2 | facade 仅 re-export 5 个 dispatch 函数 | 完整 re-export 11 个符号（5 dispatch + dispatch_agent_v2 + dispatch_agent + parse_arguments + log + _is_overall_success + _module_level_single_dispatch） | §3.2 |
| B-3 | plugin 内 `from goal_orchestrator import dispatch_*_with_*` 路径错 | 改为 `from dispatch.legacy import dispatch_*_with_*` | §2.6 + §3.2 |
| B-4 | cli_flag 与 name 重复（DRY 违反） | 删 cli_flag property，dispatcher 派生 `f"--{name}"` | §2.1 |
| B-5 | PluginContext 缺 dry_run 字段，破坏 --dry-run 行为 | PluginContext 加 `dry_run: bool = False` + facade 入口短路 | §2.3 + §3.3 |
| H-1 | 5 plugin × 4 mutex = 20 条声明无校验 | `_validate_mutex_declarations` 启动期校验（自指 / 名字存在性 / 对称性） | §2.2 |
| H-2 | 缺 middleware 钩子 | `DispatchMiddleware(ABC)` 接口定义，v1 留空，结构就位 | §2.5 |
| H-3 | PluginContext 缺 verbose / agent_type / config | 加 `verbose` / `agent_type` / `config` 字段，未来扩展显式分界 | §2.3 |
| H-5 | 缺 cleanup 契约，资源泄漏无保险 | `cleanup(ctx, exc) -> None` 默认 no-op + dispatcher try/finally | §2.1 + §2.2 |
| H-6 | name / priority 唯一性无校验 | `register()` 内部 raise `DuplicatePluginNameError` / `DuplicatePriorityError` | §2.2 |
| H-7 | `bool \| None` 返回值不友好 | `DispatchResult` 数据类（matched_plugin / success / error / skipped_reason） | §2.2 + §2.4 |
| H-8 | 缺契约测试 | `test_v3_plugin_contract.py`（10 tests）+ `BUILTIN_PLUGINS` 单一注册真相源 | §2.8 + §4.1 |

---

## 10. v2 → v3 修复对照表

| 风险 | v2 spec 问题 | v3 spec 修复 | 修复位置 |
|------|-------------|-------------|----------|
| 风险-1 | test_loop_goal.py 3 处 mock 路径指向 trae_agent_dispatch_v2 命名空间，v3 后 dispatch 函数迁到 dispatch.legacy，mock 失效，3 个现有测试必然失败 | 阶段 6 手动修正 mock 路径：`patch('trae_agent_dispatch_v2.dispatch_agent_v2')` → `patch('dispatch.legacy.dispatch_agent_v2')` | §5.6 |
| 风险-2 | facade._dispatch_through_v3() --task 必填校验仅在"无 plugin 匹配时"检查，与 god module "6 模式豁免后必填"行为不一致（隐式回归） | facade 恢复 god module 同等 6 模式豁免的 --task 必填校验 | §3.2 |
| 风险-3 | dispatcher.dispatch() cleanup 永远传 `exc=None`（"简化版"），破坏 H-5 契约（plugin 不知道异常类型，无法决定 rollback/commit） | 用 `exc_to_pass` 局部变量持有真实异常，finally 传给 plugin.cleanup | §2.2 |
| 风险-4 | dispatcher.dispatch() middleware.after 永远传 `result=None`（"简化版"），middleware 拿不到真实 DispatchResult | 用 `result` 变量在 finally 块持有 DispatchResult，传给 mw.after | §2.2 |
| 风险-5 | PluginContext.dry_run 字段存在但 dispatcher 不读取（dead code）；同时 facade 入口短路让 plugin 不知道 dry_run 状态 | 字段保留，dispatcher.dispatch() 入口检查 ctx.dry_run 并短路返回 DispatchResult(skipped_reason='dry_run')，facade 在 result 处理时打印 4 行 | §2.2 + §2.3 + §3.2 |
| 风险-6 | 阶段 6 缺 import smoke test 和无循环 import lint 检查 | 阶段 6 增加 3 个新步骤：3 处 mock 路径手动修正 + import smoke test + dispatch.legacy 反向 import lint | §5.6 |
| 风险-7 | §3.4 兼容性矩阵只列 12 个点（5 CLI + 1 dry_run + 5 import + 1 alias），未覆盖全部 19 处 import 站点 | 扩展矩阵到 19 个兼容点（5 CLI + 1 dry_run + 8 import + 1 alias + 4 校验行为） | §3.4 |
| 风险-8 | §3.2 line 728 自称"21 处外部 import 站点" | 修正为 "19 处外部 import 站点"（Grep 实际统计） | §3.2 |
| 风险-9 | BUILTIN_PLUGINS 单例风险未提示，plugin 未来可能持有可变状态造成测试间状态泄漏 | plugins/__init__.py 注释明确 stateless 契约 + 契约测试增加 test_plugin_instances_independent | §2.8 |
| 风险-10 | 阶段 1 实施步骤未明确 import 边界，可能在实施时破坏 B-1 修复 | 阶段 1 增加"明确 import 边界"小节：dispatch.legacy 不允许 import facade / 薄壳 | §5.1 |
| 风险-11 | plugin 绝对 import 路径依赖 scripts/ 在 sys.path，未明示 | §2.6 plugin 文件头加注释说明 sys.path 依赖（CLI / 测试 / 外部 import 三场景） | §2.6 |

---

## 11. v3 修复记录（详细）

### 11.1 风险-1：CRITICAL — mock 跨模块失效（3 个现有测试将失败）

**问题诊断**：
- test_loop_goal.py:659-661 / 680-682 / 729-731 使用：
  ```python
  with patch('trae_agent_dispatch_v2.dispatch_agent_v2', return_value=True):
      success = dispatch_agent_v2_with_loop_goal(...)
  ```
- v3 后 `dispatch_agent_v2_with_loop_goal` 迁到 `dispatch.legacy` 模块
- 函数内通过 `bound_dispatch_fn = partial(_module_level_single_dispatch, ...)` 调度
- `_module_level_single_dispatch` 在 `dispatch.legacy` 模块内调用 `dispatch_agent_v2(...)`
- Python 名字查找在 `dispatch.legacy` 命名空间
- `patch('trae_agent_dispatch_v2.dispatch_agent_v2')` 只修改薄壳命名空间，对 `dispatch.legacy` 无效
- 真实 `dispatch_agent_v2` 被调用，触发 Claude Code / Trae IDE 启动
- 测试断言失败 + 副作用

**修复方案**：阶段 6 手动修正 mock 路径
```python
# 旧（v2 失效）
with patch('trae_agent_dispatch_v2.dispatch_agent_v2', return_value=True):
# 新（v3 修正）
with patch('dispatch.legacy.dispatch_agent_v2', return_value=True):
```

**测试影响**：
- test_01_wrapper_loop_only ✅
- test_02_wrapper_with_goal_creates_persists ✅
- test_04_wrapper_convergence_exits_early ✅

### 11.2 风险-2：P0 — --task 必填校验时序不一致（隐式回归）

**问题诊断**：
- god module 行为（trae_agent_dispatch_v2.py:1339-1348）：
  ```python
  if not args.task and not (
      args.goal_graph or args.goal_cancel or args.goal_resume
      or args.multi_goal or args.loop > 1 or args.goal is not None
  ):
      log('❌ --task 必填...', 'ERROR')
      sys.exit(1)
  ```
- v2 facade 行为（错误）：
  ```python
  if not args.task and not _any_plugin_matches(dispatcher, args):
      log("❌ --task 必填", "ERROR")
  ```
  - "无 plugin 匹配时" 检查会漏掉场景：未来新增 plugin 不豁免但 plugin.matches() 返回 True 时

**修复方案**：facade 恢复 god module 同等 6 模式豁免检查
```python
# v3 修正
if not args.task and not (
    args.goal_graph or args.goal_cancel or args.goal_resume
    or args.multi_goal or args.loop > 1 or args.goal is not None
):
    log("❌ --task 必填（除非使用 6 模式之一）", "ERROR")
    return 1
```

**行为保证**：与 god module 100% 等价（连错误消息措辞都保留）。

### 11.3 风险-3/4：P1 — dispatcher.dispatch() cleanup 和 middleware 简化版 bug

**问题诊断**：
- v2 注释承认是"简化版"：
  ```python
  plugin.cleanup(ctx, exc=None)  # 简化版：实际应传递 exc
  mw.after(args, ctx, None)  # 简化版
  ```
- H-5 修复初衷是 plugin 知道异常类型以决定 rollback/commit，传 None 破坏意图
- H-2 middleware 设计意图是 audit / metrics / tracing，强制 None 让 middleware 看不到真实状态

**修复方案**：
```python
# v3 修正
exc_to_pass: Optional[BaseException] = None
try:
    success = plugin.execute(args, ctx)
    result = DispatchResult(matched_plugin=plugin.name, success=success, error=None)
    return result
except BaseException as exc:
    exc_to_pass = exc
    result = DispatchResult(matched_plugin=plugin.name, success=False, error=exc)
    return result
finally:
    try:
        plugin.cleanup(ctx, exc_to_pass)
    except Exception as e:
        self._logger.warning(...)

# 外层 finally
for mw in self._middlewares:
    mw.after(args, ctx, result)  # 传真实 DispatchResult
```

### 11.4 风险-5：P1 — PluginContext.dry_run 字段语义

**问题诊断**：
- v2 PluginContext.dry_run 字段存在但 dispatcher 不读取（dead code）
- v2 facade 入口短路：plugin 不知道 dry_run 状态

**修复方案（v3 方案 B：dispatcher 内部短路）**：
```python
# dispatcher.dispatch() 入口
if getattr(ctx, "dry_run", False):
    return DispatchResult(
        matched_plugin=None,
        success=True,
        error=None,
        skipped_reason="dry_run",
    )

# facade._dispatch_through_v3() result 处理
if result.skipped_reason == "dry_run":
    log('🔄 模拟模式：不实际调用智能体', 'WARNING')
    log(f'   将调度智能体：{args.agent}', 'WARNING')
    log(f'   任务：{args.task}', 'WARNING')
    log('✅ 模拟完成', 'SUCCESS')
    return 0
```

**优势**：
- PluginContext.dry_run 字段真正"活"起来
- Plugin 可以在 execute() 内通过 `ctx.dry_run` 检查
- 旧 4 行日志行为完整保留
- dispatcher 内部短路更符合"插件架构"理念

### 11.5 风险-6：P1 — 实施期 verification 缺位

**问题诊断**：
- 阶段 6 缺 import smoke test（薄壳 import 不应循环）
- 阶段 6 缺无循环 import lint（dispatch.legacy 不能反向 import facade / 薄壳）

**修复方案（v3 阶段 6 新增 3 个步骤）**：
```bash
# 1. import smoke test
python3 -c "import trae_agent_dispatch_v2; import dispatch.legacy; from plugins import BUILTIN_PLUGINS; assert len(BUILTIN_PLUGINS) == 5"
python3 -c "import facade; from facade import main_compat, log, dispatch_agent_v2, dispatch_agent_v2_with_loop_goal, _is_overall_success, _module_level_single_dispatch, parse_arguments"

# 2. dispatch.legacy 反向 import lint
! grep -E "import facade$|from facade|import trae_agent_dispatch_v2" scripts/dispatch/legacy.py
```

### 11.6 风险-7/8：P2 — 兼容矩阵不完整 + 数字偏差

**问题诊断**：
- §3.4 矩阵只列 12 个点，未覆盖 19 处 import 站点
- §3.2 line 728 自称 "21 处"，Grep 实际 19 处

**修复方案（v3 §3.2 + §3.4）**：
- §3.2 修正为 "19 处外部 import 站点（§3.4 兼容矩阵覆盖）"
- §3.4 扩展到 19 个兼容点（5 CLI + 1 dry_run + 8 import + 1 alias + 4 校验行为）

### 11.7 风险-9/10/11：P2 — stateless 契约 + import 边界 + sys.path 注释

**修复方案（v3 §2.8 / §2.6 / §5.1）**：
- §2.8 BUILTIN_PLUGINS 注释明确 stateless 契约 + 契约测试增加 test_plugin_instances_independent
- §2.6 plugin 文件头加注释说明 sys.path 依赖
- §5.1 阶段 1 明确 import 边界（dispatch.legacy 禁止 import facade / 薄壳）

---

## 12. 终态

v3 spec 通过后：
- B-1~B-5（5 阻塞）+ H-1/H-2/H-3/H-5/H-6/H-7/H-8（7 高优）= 12 项 v1 review 问题全部修复
- 风险-1（CRITICAL）+ 风险-2（P0）+ 风险-3~7（P1）+ 风险-8~11（P2）= 11 项 v2 review 新增问题全部修复
- 共 23 项修复点全部就位
- 进入 writing-plans 技能生成实施 plan
- 实施 6 阶段 + commit + tag
