# Phase 16 设计文档：V3 架构重构（插件架构）

> **文档类型**：技术方案 spec（v1 — 初稿，待架构师 review）
> **日期**：2026-06-06
> **状态**：⏳ 等待架构师 review
> **前序**：[PHASE15_PLAN.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE15_PLAN.md)（DAG 可视化；53 单元 + 16 集成 = 69 tests）
> **方向**：V3 架构重构（拆分 god module，4 选项 Top-1）
> **实现路径**：插件架构（重量级，引入 GoalCommandPlugin 抽象基类 + GoalDispatcher 调度中心）

---

## 0. 背景与动机

### 0.1 当前痛点（trae_agent_dispatch_v2.py）

文件 `scripts/trae_agent_dispatch_v2.py` 经过 Phase 11/13/14/15 持续叠加，已膨胀为 **god module**：

| 指标 | 当前值 | 危险阈值 |
|------|--------|----------|
| 文件行数 | ~1500 | > 1000 |
| 文件大小 | 52KB | > 30KB |
| `def` 数量 | 14 | > 10 |
| 模式分支（if-elif） | 5（cancel/graph/resume/multi_goal/loop） | > 3 难维护 |
| CLI 改动点 | 3（flag + 互斥 + 分支） | > 2 改一处易漏 |

### 0.2 god module 的具体症状

**症状 1：添加新模式要改 3 处**

```python
# 1. 改 parse_arguments()（line 72-318）— 加 CLI flag
parser.add_argument('--goal-cancel', ...)

# 2. 改 main() 顶部互斥校验（line 1326-1334）— 加新互斥关系
if args.goal_graph and (args.goal_cancel or ...):
    sys.exit(1)

# 3. 改 main() 中部 if-elif 链（line 1370-1400+）— 加新分支
if args.goal_cancel:
    success = dispatch_agent_v2_with_goal_cancel(...)
elif args.goal_graph:
    ...
```

**症状 2：互斥规则散落**（两处）
- 顶部 line 1326-1334：`--goal-graph` 互斥判断（硬编码 if）
- 中部 line 1370-1397：5 个 if-elif 隐含互斥（隐式，不可见）

**症状 3：优先级链用注释维护**
- line 1370：`# Phase 14 优先级 0`
- line 1381：`# Phase 15 优先级 1`
- line 1398：`# Phase 13 优先级 2`
- 注释容易漂移，没有代码强制保证

**症状 4：dispatch 函数命名不一致**
- `dispatch_agent_v2_with_loop_goal`（下划线）
- `dispatch_agent_v2_with_goal_resume`（下划线）
- `dispatch_agent_v2_with_multi_goal`（下划线）
- `dispatch_agent_v2_with_goal_cancel`（下划线）
- `dispatch_agent_v2_with_goal_graph`（下划线）
- 5 个函数签名/参数/返回类型不统一（有的带 `desc_max_length`，有的带 `format`）

### 0.3 设计目标

实现 **V3 插件架构**，将 god module 拆分为：

1. **零业务行为变化**（与 Phase 14 一样强约束）：
   - 现有 5 个 dispatch 函数 100% 行为保留
   - 现有 5 个 CLI flag 参数完全相同
   - 现有 85 个 Phase 13/14/15 测试 100% 通过

2. **插件化扩展**：
   - 添加新模式只需 1 处改动（新增 1 个 plugin 文件 + 1 行注册）
   - 互斥规则由插件自报（不再集中硬编码）
   - 优先级由插件 property 自报（不再用注释）

3. **职责分离**：
   - CLI 解析：cli/parser.py
   - 调度中心：dispatcher/goal_dispatcher.py
   - 业务插件：plugins/{cancel,graph,resume,multi_goal,loop}.py
   - 向后兼容：facade.py + 瘦壳 trae_agent_dispatch_v2.py

4. **可测试性**：
   - 每个插件可独立测试（不依赖 dispatcher）
   - dispatcher 可独立测试（用 mock 插件）
   - 集成测试沿用现有 Phase 13/14/15 测试套件

---

## 1. 架构设计

### 1.1 总体架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                 trae-multi-agent v3.0（Phase 16 后）                   │
├──────────────────────────────────────────────────────────────────────┤
│  入口层（薄壳）      │  trae_agent_dispatch_v2.py  (~30 行)           │
│                      │  └── from facade import main; main()          │
├──────────────────────────────────────────────────────────────────────┤
│  兼容层              │  facade.py (~200 行)                          │
│                      │  ├── re-export 5 个 dispatch 函数              │
│                      │  ├── 旧 CLI 入口 (main_compat)                │
│                      │  └── 旧 import 路径兼容                        │
├──────────────────────────────────────────────────────────────────────┤
│  CLI 层              │  cli/parser.py (~350 行)                      │
│                      │  ├── parse_arguments() — argparse only        │
│                      │  └── _validate_args() — 参数校验              │
├──────────────────────────────────────────────────────────────────────┤
│  调度层 (NEW)        │  dispatcher/goal_dispatcher.py (~200 行)      │
│                      │  ├── GoalDispatcher — 调度中心                │
│                      │  ├── PluginContext — 共享资源                 │
│                      │  └── DispatchError — 调度异常                  │
├──────────────────────────────────────────────────────────────────────┤
│  插件层 (NEW)        │  plugins/base.py (~100 行)                    │
│                      │  ├── GoalCommandPlugin (ABC)                  │
│                      │  ├── plugin registry helpers                  │
│                      │  └── 5 个内置插件：                            │
│                      │     ├── cancel.py   (priority=0)              │
│                      │     ├── graph.py    (priority=1)              │
│                      │     ├── resume.py   (priority=2)              │
│                      │     ├── multi_goal.py (priority=3)            │
│                      │     └── loop.py     (priority=4)              │
├──────────────────────────────────────────────────────────────────────┤
│  业务层（不动）      │  goal_orchestrator.py / loop_goal.py /         │
│                      │  dag_visualizer.py / trae_agent.py            │
├──────────────────────────────────────────────────────────────────────┤
│  基础设施（不动）    │  workflow_engine_v2.py / checkpoint_manager.py│
│                      │  cybernetics / karpathy / guard                │
└──────────────────────────────────────────────────────────────────────┘
```

### 1.2 目录结构

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
│   └── errors.py                 # DispatchError / MutexViolationError
├── plugins/
│   ├── __init__.py
│   ├── base.py                   # GoalCommandPlugin ABC
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
    ├── test_v3_plugins.py        # 单元测试各插件
    └── test_v3_integration.py    # CLI 端到端集成
```

### 1.3 数据流向

```
用户执行：python3 trae_agent_dispatch_v2.py --goal-cancel g1
            ↓
trae_agent_dispatch_v2.main() (1 行)
            ↓
facade.main_compat() 解析 + 创建 dispatcher
            ↓
cli.parser.parse_arguments() → Namespace
            ↓
GoalDispatcher.dispatch(args, ctx)
    ├── 1. 遍历 5 个 plugin，调用 matches(args) → 找出 cancel plugin
    ├── 2. 按 priority 排序（cancel=0 排第一）
    ├── 3. 互斥校验（cancel 与其他 plugin 互斥关系 → 通过）
    ├── 4. 执行 cancel_plugin.execute(args, ctx) → True/False
    └── 5. 返回 success
```

---

## 2. 核心设计

### 2.1 `GoalCommandPlugin` 抽象基类

```python
# plugins/base.py
from abc import ABC, abstractmethod
from typing import Set
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
        """插件唯一名称（用于日志 / 错误信息）。
        
        约定：与 CLI flag 后半段保持一致。
        例：'goal-cancel' / 'goal-graph' / 'goal-resume'
        """

    @property
    @abstractmethod
    def cli_flag(self) -> str:
        """CLI flag 全名（带双横线）。
        
        例：'--goal-cancel'
        """

    @property
    @abstractmethod
    def priority(self) -> int:
        """调度优先级（数字越小越优先）。
        
        约定：
        - 0：破坏性最高（cancel）
        - 1：只读（graph）
        - 2~3：状态变更（resume / multi_goal）
        - 4~5：循环 / 长期运行（loop）
        """

    @property
    @abstractmethod
    def mutex_with(self) -> Set[str]:
        """互斥的插件名称集合（基于 plugin.name）。
        
        例：{'goal-resume', 'multi-goal', 'goal-cancel', 'goal-graph'}
        表示这些插件不能与本插件同时启用。
        """

    @property
    @abstractmethod
    def requires_task(self) -> bool:
        """是否要求 --task 参数。
        
        约定：cancel/graph/resume/multi_goal/loop 都不需要 --task。
        普通 dispatch_agent_v2 才需要 --task。
        """

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
            ctx: 共享上下文（project_root / log / registry）

        Returns:
            bool: True 表示成功；False 表示失败
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
from typing import List
from plugins.base import GoalCommandPlugin
from dispatcher.plugin_context import PluginContext
from dispatcher.errors import MutexViolationError, NoMatchingPluginError


class GoalDispatcher:
    """V3 Goal 命令调度器。

    行为契约：
    1. 收集所有 plugin
    2. 调用 matches() 找出匹配的 plugin（理论上只能有 0 或 1 个）
    3. 按 priority 排序（虽然理论上只有一个）
    4. 互斥校验：如果多个匹配 → 抛 MutexViolationError
    5. 执行匹配 plugin 的 execute() → 返回 bool
    6. 如果无匹配 → 返回 None（调用方决定走默认 dispatch）
    """

    def __init__(self, plugins: List[GoalCommandPlugin] = None):
        self._plugins: List[GoalCommandPlugin] = list(plugins or [])
        self._logger = logging.getLogger("goal_dispatcher")

    def register(self, plugin: GoalCommandPlugin) -> None:
        """注册插件（按 priority 升序插入）。"""
        self._plugins.append(plugin)
        self._plugins.sort(key=lambda p: p.priority)

    def list_plugins(self) -> List[GoalCommandPlugin]:
        """返回所有已注册插件（只读副本）。"""
        return list(self._plugins)

    def dispatch(
        self, args: argparse.Namespace, ctx: PluginContext
    ) -> bool | None:
        """调度入口。

        Returns:
            bool: 成功 / 失败
            None: 无插件匹配（调用方应走默认 dispatch_agent_v2 路径）
        """
        matched = [p for p in self._plugins if p.matches(args)]
        if not matched:
            return None
        if len(matched) > 1:
            # 互斥校验：理论上 args 只有一个 plugin 字段非 None
            names = [p.name for p in matched]
            raise MutexViolationError(
                f"多个插件同时匹配（args 解析层应已阻止）：{names}"
            )
        plugin = matched[0]
        self._logger.info(
            f"[Dispatcher] 匹配插件：{plugin.name} (priority={plugin.priority})"
        )
        return plugin.execute(args, ctx)

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
                        f"插件 {plugin.cli_flag} 与 {mutex_name} 互斥"
                    )
```

### 2.3 `PluginContext` 共享上下文

```python
# dispatcher/plugin_context.py
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from loop_goal import GoalRegistry


@dataclass
class PluginContext:
    """插件执行上下文（V3 引入）。

    封装所有插件共享的资源，避免插件之间通过全局变量通信。
    """
    project_root: Path
    log: Callable[[str, str], None]  # 签名同现有 log(message, level)
    registry: Optional["GoalRegistry"] = None
    # 未来扩展：metrics / tracer / config 等

    def __post_init__(self):
        if not isinstance(self.project_root, Path):
            self.project_root = Path(self.project_root)
```

### 2.4 内置插件示例（GoalCancelPlugin）

```python
# plugins/cancel.py
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
    def cli_flag(self) -> str:
        return "--goal-cancel"

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
        from goal_orchestrator import dispatch_agent_v2_with_goal_cancel
        ctx.log(
            f"🛑 Phase 16 检测到取消模式：goal={args.goal_cancel}",
            "INFO",
        )
        return dispatch_agent_v2_with_goal_cancel(
            goal_id=args.goal_cancel,
            project_root=str(ctx.project_root),
        )
```

### 2.5 插件总览

| 插件 | name | cli_flag | priority | mutex_with | requires_task |
|------|------|----------|----------|------------|---------------|
| `GoalCancelPlugin` | goal-cancel | `--goal-cancel` | 0 | {graph, resume, multi-goal, loop} | False |
| `GoalGraphPlugin` | goal-graph | `--goal-graph` | 1 | {cancel, resume, multi-goal, loop} | False |
| `GoalResumePlugin` | goal-resume | `--goal-resume` | 2 | {cancel, graph, multi-goal, loop} | False |
| `MultiGoalPlugin` | multi-goal | `--multi-goal` | 3 | {cancel, graph, resume, loop} | False |
| `LoopGoalPlugin` | loop | `--loop` | 4 | {cancel, graph, resume, multi-goal} | False |

---

## 3. 向后兼容设计

### 3.1 `facade.py` 兼容层

```python
# facade.py
"""V3 兼容层：保持旧 API 100% 工作。

设计原则：
- 不删除任何旧函数，只 re-export
- 旧 CLI 入口（trae_agent_dispatch_v2.main）继续可用
- 旧 import 路径（from trae_agent_dispatch_v2 import ...）继续可用
"""

# 1. re-export 5 个 dispatch 函数
from goal_orchestrator import (
    dispatch_agent_v2_with_goal_resume,
    dispatch_agent_v2_with_multi_goal,
)
from goal_orchestrator import dispatch_agent_v2_with_goal_cancel  # Phase 14
from goal_orchestrator import dispatch_agent_v2_with_goal_graph  # Phase 15
from loop_goal import dispatch_agent_v2_with_loop_goal  # Phase 11

# 2. re-export parse_arguments / log
from cli.parser import parse_arguments
from cli.parser import log

# 3. re-export dispatch_agent_v2 / dispatch_agent
from trae_agent_dispatch_v2 import dispatch_agent_v2
from trae_agent_dispatch_v2 import dispatch_agent

# 4. 旧 main 入口（保证 Phase 11/13/14/15 测试通过）
def main_compat() -> int:
    """兼容旧 main() — 走新 dispatcher 路径。"""
    args = parse_arguments()
    return _dispatch_through_v3(args)


def _dispatch_through_v3(args) -> int:
    """通过 V3 dispatcher 执行（与旧 main() 行为一致）。"""
    from pathlib import Path
    from dispatcher.goal_dispatcher import GoalDispatcher
    from dispatcher.plugin_context import PluginContext
    from plugins.cancel import GoalCancelPlugin
    from plugins.graph import GoalGraphPlugin
    from plugins.resume import GoalResumePlugin
    from plugins.multi_goal import MultiGoalPlugin
    from plugins.loop import LoopGoalPlugin

    project_root = Path(args.project_root).resolve()
    if not project_root.exists():
        log(f"❌ 项目根目录不存在：{project_root}", "ERROR")
        return 1

    # 互斥预校验
    dispatcher = GoalDispatcher([
        GoalCancelPlugin(),
        GoalGraphPlugin(),
        GoalResumePlugin(),
        MultiGoalPlugin(),
        LoopGoalPlugin(),
    ])
    ctx = PluginContext(project_root=project_root, log=log)
    try:
        dispatcher.validate_mutex(args)
    except MutexViolationError as e:
        log(f"❌ {e}", "ERROR")
        return 1

    # --task 必填校验
    if not args.task and not _any_plugin_matches(dispatcher, args):
        log("❌ --task 必填", "ERROR")
        return 1

    # 调度
    success = dispatcher.dispatch(args, ctx)
    if success is None:
        # 无插件匹配 → 默认 dispatch_agent_v2
        success = dispatch_agent_v2(
            agent_type=args.agent,
            task=args.task,
            project_root=str(project_root),
        )
    return 0 if success else 1
```

### 3.2 `trae_agent_dispatch_v2.py` 瘦壳

```python
# trae_agent_dispatch_v2.py（v3 瘦壳，~30 行）
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trae Agent 调度脚本 v2 — V3 瘦壳入口（Phase 16 重构后）。

完整实现已迁移到 facade.py / cli/ / dispatcher/ / plugins/。
本文件仅作为 backward-compat 入口，避免破坏现有脚本调用。
"""

import sys
from facade import main_compat


def main():
    """薄壳入口：所有逻辑委托给 facade.main_compat()。"""
    sys.exit(main_compat())


if __name__ == "__main__":
    main()
```

### 3.3 兼容性验证矩阵

| 调用方式 | v3 行为 | Phase 11/13/14/15 测试 |
|----------|---------|------------------------|
| `python3 trae_agent_dispatch_v2.py --goal-cancel g1` | main_compat → dispatcher → cancel_plugin | ✅ test_goal_orchestrator 75 tests |
| `python3 trae_agent_dispatch_v2.py --goal-graph root` | main_compat → dispatcher → graph_plugin | ✅ test_dag_visualizer 53 tests |
| `python3 trae_agent_dispatch_v2.py --goal-resume g1` | main_compat → dispatcher → resume_plugin | ✅ test_goal_orchestrator 75 tests |
| `python3 trae_agent_dispatch_v2.py --multi-goal g1,g2` | main_compat → dispatcher → multi_goal_plugin | ✅ test_goal_orchestrator 15 tests |
| `python3 trae_agent_dispatch_v2.py --loop 5` | main_compat → dispatcher → loop_plugin | ✅ test_loop_goal 103 tests |
| `from trae_agent_dispatch_v2 import dispatch_agent_v2_with_goal_cancel` | facade re-export | ✅ 旧 import 路径正常 |
| `from trae_agent_dispatch_v2 import main` | 本文件定义 | ✅ 旧 import 路径正常 |

---

## 4. 测试策略

### 4.1 单元测试（目标 ~80 tests）

- `test_v3_dispatcher.py`（~20 tests）：
  - 注册 / 重复注册 / 排序
  - matches 匹配 / 不匹配
  - 互斥校验：单匹配 / 多匹配
  - 无匹配 → 返回 None
  - 异常路径：MutexViolationError

- `test_v3_plugin_context.py`（~10 tests）：
  - 构造 / 默认值
  - project_root 自动转 Path
  - log / registry 注入

- `test_v3_plugins.py`（~50 tests = 5 plugins × 10 tests）：
  - 每个插件：name / cli_flag / priority / mutex_with / requires_task
  - 每个插件：matches() 各种 args 组合
  - 每个插件：execute() 调用对应 dispatch 函数（mock）

### 4.2 集成测试（目标 ~20 tests）

- `test_v3_integration.py`（~20 tests）：
  - CLI 端到端：5 种模式 × happy path
  - CLI 互斥错误：cancel + graph → MutexViolationError
  - CLI --task 缺失：默认模式 → 错误
  - CLI 默认 dispatch：--agent + --task → dispatch_agent_v2
  - 旧 import 路径：from trae_agent_dispatch_v2 import ... → 正常工作
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

1. 创建目录结构 `cli/ dispatcher/ plugins/`
2. 实现 `dispatcher/plugin_context.py`（无依赖）
3. 实现 `dispatcher/errors.py`（无依赖）
4. 实现 `plugins/base.py`（依赖 PluginContext）
5. 实现 `dispatcher/goal_dispatcher.py`（依赖 base + errors）

### 5.2 阶段 2：5 个内置插件（行为保留）

1. `plugins/cancel.py`（搬运 `dispatch_agent_v2_with_goal_cancel`）
2. `plugins/graph.py`（搬运 `dispatch_agent_v2_with_goal_graph`）
3. `plugins/resume.py`（搬运 `dispatch_agent_v2_with_goal_resume`）
4. `plugins/multi_goal.py`（搬运 `dispatch_agent_v2_with_multi_goal`）
5. `plugins/loop.py`（搬运 `dispatch_agent_v2_with_loop_goal`）

### 5.3 阶段 3：CLI 解析拆分

1. `cli/parser.py`（从 trae_agent_dispatch_v2.py line 72-318 搬出 parse_arguments）
2. 保留 log() 在 cli/parser.py（避免循环 import）

### 5.4 阶段 4：facade 兼容层

1. `facade.py`（re-export + main_compat）
2. `trae_agent_dispatch_v2.py` 瘦壳化（30 行）

### 5.5 阶段 5：测试 + 验证

1. 写新单元测试 `test_v3_dispatcher.py` / `test_v3_plugins.py`
2. 写新集成测试 `test_v3_integration.py`
3. 跑全量回归（1089 tests）
4. ruff check（0 warnings）

### 5.6 阶段 6：commit + tag

1. `feat: Phase 16 V3 插件架构重构`
2. tag: `phase-16-v3-plugin-architecture`

---

## 6. 风险与缓解

| 风险 | 严重度 | 缓解措施 |
|------|--------|----------|
| 旧 import 路径断裂 | P0 | facade.py 100% re-export + 旧 import 测试覆盖 |
| CLI flag 行为变化 | P0 | 5 种模式端到端测试（参数解析 + dispatch 调用） |
| 优先级链漂移 | P1 | 插件 priority 用 int 自报，dispatcher 强制排序 |
| 互斥规则漏写 | P1 | 单元测试覆盖每个 plugin 的 mutex_with 集合 |
| 性能回退 | P2 | 单次 dispatch < 5ms 基线测试 |
| god module 拆分不彻底 | P2 | trae_agent_dispatch_v2.py 必须 < 50 行 |

---

## 7. 待架构师 review 重点

1. **`GoalCommandPlugin` 抽象基类设计**（§2.1）：4 个 property + 2 个 abstractmethod 是否合理？
2. **优先级数值约定**（§2.5）：0=破坏 / 1=只读 / 2-3=状态变更 / 4-5=循环 是否合理？
3. **`PluginContext` 字段**（§2.3）：仅含 project_root / log / registry，未来扩展字段是否需要预留？
4. **`facade.py` 兼容策略**（§3.1）：re-export + main_compat 双重保险是否过度？
5. **测试目标 ~100 新 tests**（§4.1-4.2）：是过少 / 合理 / 过多？

---

## 8. 文档版本

- v1（2026-06-06 初稿）：本文件，等待架构师 review
