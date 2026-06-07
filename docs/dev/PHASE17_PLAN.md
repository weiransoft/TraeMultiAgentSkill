# Phase 17 设计文档：插件热加载（Hot Reload）

> **文档类型**：技术方案 spec（v3.1 — 架构师二轮 review 后小幅修订版）
> **日期**：2026-06-07
> **状态**：✅ v3.1 CONDITIONAL APPROVAL（v3 review 4 项 mandatory 修订已完成）
> **前序**：[PHASE16_PLAN.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE16_PLAN.md)（V3 插件架构 1464→42 行 + 11 项风险修复）
> **方向**：插件热加载（V3 架构之上的动态能力）
> **实现范围**：完整实现（轮询 + 显式 API + drop-in 目录扫描）
> **触发机制**：显式 API（hot_register / hot_unregister）+ drop-in 目录扫描（启动 + 运行时轮询）

---

## 0. 变更履历（v1 → v2 → v3 → v3.1）

### 0.0 v3 → v3.1 修订（架构师二轮 review 反馈）

> v3 二轮复审结论：**CONDITIONAL APPROVAL**（PASS with 4 项 mandatory amendments）
> 修订策略：保留 v3 主版本号，仅增补 4 项内容（不重写大改 v4）

| # | 修订项 | 严重度 | 位置 | 修订内容 |
|---|--------|--------|------|----------|
| **P0-8** | ghost plugin 泄漏 | P0 | §2.3 `_reload_file` 步骤 1 后 | 失败 → 立即调用 `_rollback_old_plugins(unregistered, path.name)` + `return` 拒绝步骤 2/3/4（fail-fast）|
| **P1-9** | critical log 缺外部 hook | P1 | §2.3 `HotReloadWatcher.__init__` + `_rollback_old_plugins` | 新增 `critical_failure_callback: Optional[Callable[[str, List[str]], None]] = None` 参数；`_rollback_old_plugins` 在 critical log 后触发该回调（try/except 隔离回调异常）|
| **P1-10** | 软链测试覆盖不全 | P1 | §4.3.7 P0-7 负 case | 追加 N4（project_root 是软链）+ N5（drop_in_dir 是软链跳出）两个负测试 + 各自设计要点 |
| **P1-11** | 风险表计数不一致 | P1 | §6 标题 + 表格 | 23 项 → 28 项；按 4+3+7+5+9=28 重新分组为 A-E 5 段（段 A 4 项 + 段 B 3 项 + 段 C 7 项 + 段 D 5 项 + 段 E 9 项）|

### 0.1 v1 → v2 解决的 P0（4 项，已合并至 v3）

| # | v1 P0 | v2 修复 |
|---|-------|---------|
| P0-1 | CLI 互斥冲突 | `argparse.add_mutually_exclusive_group()` |
| P0-2 | DropInLoader sys.modules 内存泄漏 | watcher._unload_file 显式 pop |
| P0-3 | hot_register 与 register 校验不对齐 | `_validate_plugin_metadata` 统一入口 |
| P0-4 | HotReloadWatcher 启动竞态 | 同步首次扫描 + `_initial_scan_done` Event |

### 0.2 v2 → v3 新发现的 P0（3 项，必须解决）

| # | v2 P0 | v3 修复（位置） |
|---|-------|----------------|
| **P0-5** | **多 plugin 文件的 `_file_states` 行为自相矛盾**（load 用最后一个 / reload 用第一个 / unload 漏掉其它）| `_file_states: Dict[str, Tuple[float, List[Plugin]]]`（§2.3）+ 删除 §2.7 限制 1（"单文件单 plugin 假设"）改为"完全支持" |
| **P0-6** | **reload 回滚路径不完整**（失败时只 log warning，可能留"既不是旧也不是新"状态）| `_reload_file` 重构（§2.3）：unregister 全部旧 → load 新 → 失败则逐个 hot_register 旧 → 仍失败则 fatal log + 报警 |
| **P0-7** | **路径穿越防护缺失**（`--hot-reload-dir ../../etc` 可绕过；§2 设计中无强制实现）| 三层防护（§2.6 + §2.3 + §2.9）：parser `argparse.ArgumentTypeError` + watcher `_resolve_drop_in_dir` + facade 串联校验 |

### 0.3 v2 → v3 解决的 P1（8 项）

| # | v2 P1 | v3 修复 |
|---|-------|---------|
| P1-1 | drop-in 目录不存在时全量 unload | `_scan_once` 目录缺失 → log warning + 跳过（§2.3） |
| P1-2 | `dispatch()` 不在 `_lock` 内（隐式 snapshot 语义未文档化）| §2.10 新增"dispatch snapshot 契约"段 + §3.2 兼容点 25 追加 |
| P1-3 | `wait_for_idle` 用 Event + 1s 切片 | 改用 `threading.Condition` + `notify_all`（§2.5） |
| P1-4 | `module_from_spec` 之后 `exec_module` 之前的异常无清理 | `try/finally` 包裹（§2.4） |
| P1-5 | `default=True` 写在 mutex group 内的合法性 | facade 兜底 `assert args.hot_reload is not None`（§2.9） |
| P1-6 | 4 个 P0 专项测试只有文件名 | §4.3 详化每个 P0 的 3 positive + 3 negative case 矩阵 |
| P1-7 | `_start_hot_reload_if_enabled` 内部实现要求不明 | §2.9 列出 atexit 注册 + 多 dispatcher 防重复 + 异常隔离 |
| P1-8 | atexit hook 重复注册防护 | facade 用 `_watcher_refs: set[weakref]` 跟踪（§2.9） |

### 0.4 v2 → v3 解决的 P2（8 项）

| # | v2 P2 | v3 修复 |
|---|-------|---------|
| P2-1 | `path.stem` 含中文/特殊字符破坏 sys.modules key | `re.sub(r"[^a-zA-Z0-9_.]", "_", path.stem)` sanitize（§2.4） |
| P2-2 | `sort` 稳定性 + 多次 hot_register 顺序契约 | §2.10 显式契约："稳定排序，相同 priority 时按 hot_register 顺序" |
| P2-3 | 并发 hot_register 顺序 | 文档化"v1 不支持并发 hot_register"（同进程内串行调用） |
| P2-4 | 多进程 daemon 行为 | §2.7 限制 4 已声明单进程；daemon=True 仍保留 |
| P2-5 | §7 决策表遗漏 3 项 | §7 v2→v3 决策表追加（snapshot 契约 / 目录缺失行为 / 多 plugin 约束） |
| P2-6 | 集成测试 subprocess vs in-process | §4.2 双版本：unit-like 集成 + subprocess 真集成 |
| P2-7 | exit_execute 防御性 log 无 metrics | §2.5 增加 `self._unbalanced_exit_count` 计数器（外部可读） |
| P2-8 | 测试命名混淆 | §4.1 改名：`_drop_in_` 前缀统一指 loader 行为，`_hot_reload_` 指 watcher 行为 |

---

## 1. 背景与动机

### 1.1 现有能力与痛点

Phase 16 后 `scripts/plugins/__init__.py` 静态构造 5 个 BUILTIN_PLUGINS：

```python
# plugins/__init__.py
BUILTIN_PLUGINS: list = [
    GoalCancelPlugin(),
    GoalGraphPlugin(),
    GoalResumePlugin(),
    MultiGoalPlugin(),
    LoopGoalPlugin(),
]
```

**痛点**：
1. **添加新 plugin 必须改 `plugins/__init__.py`**（哪怕是临时调试一个 feature）
2. **plugin 代码改动必须重启 dispatcher** 才能生效（开发体验差）
3. **不同环境（生产/灰度/A-B test）** 需要不同 plugin 集合 → 需多份 `__init__.py`
4. **第三方贡献 plugin** 必须 fork 代码库 → 阻碍生态

### 1.2 设计目标

实现 **V3 插件热加载**，在保留 Phase 16 静态注册路径基础上，叠加动态能力：

1. **零业务行为变化**（强约束）：
   - BUILTIN_PLUGINS 静态注册的 5 个 plugin 行为 100% 保留
   - 现有 462 tests 100% 通过
   - dispatcher.register() 现有语义不变（H-6 唯一性校验继续生效）

2. **动态能力新增**：
   - 显式 API：`dispatcher.hot_register(plugin)` / `dispatcher.hot_unregister(name)`
   - drop-in 目录：扫描 `plugins_extra/*.py`，自动 import + 注册
   - 轮询：周期检查 `plugins_extra/` 文件 mtime，变更时 reload

3. **可测试性**：
   - hot_register / hot_unregister 单测
   - drop-in 扫描单测（临时目录 + 临时 .py 文件）
   - reload 冲突场景测试（name 冲突 / mutex 冲突 / reload 失败回滚）
   - 集成测试：CLI 启动时加载 drop-in / 运行时 reload

4. **生产安全**：
   - reload 失败回滚到旧实例（不破坏现有调度）
   - reload 期间持锁的 plugin（正在 execute）→ 等锁释放再 unregister
   - 显式开关：`--no-hot-reload` 完全关闭动态能力（生产模式）
   - 路径安全：drop-in 目录强制在 project_root 内（防止路径穿越，P0-7）

---

## 2. 架构设计

### 2.0 总体架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                   trae-multi-agent v3.1（Phase 17 后）                  │
├──────────────────────────────────────────────────────────────────────┤
│  入口层（薄壳）      │  trae_agent_dispatch_v2.py  (~30 行)           │
│                      │  └── from facade import main_compat           │
├──────────────────────────────────────────────────────────────────────┤
│  兼容层              │  facade.py (~200 行)                          │
│                      │  ├── re-export 11 个旧符号                    │
│                      │  ├── main_compat() 旧 CLI 入口                │
│                      │  ├── _dispatch_through_v3() 内部走 dispatcher  │
│                      │  ├── _start_hot_reload_if_enabled() v17 新增  │
│                      │  ├── _resolve_drop_in_dir() v17 路径安全      │
│                      │  └── _safe_watcher_stop() atexit 清理          │
├──────────────────────────────────────────────────────────────────────┤
│  Legacy 入口模块     │  dispatch/legacy.py (~600 行)               │
├──────────────────────────────────────────────────────────────────────┤
│  CLI 层              │  cli/parser.py (~280 行) +                   │
│                      │  + --hot-reload / --no-hot-reload 互斥组      │
│                      │  + --hot-reload-dir (type 校验)              │
│                      │  + --hot-reload-interval                     │
├──────────────────────────────────────────────────────────────────────┤
│  调度层              │  dispatcher/goal_dispatcher.py                │
│                      │  ├── Phase 16: register / dispatch / etc.    │
│                      │  ├── v17 重构: _validate_plugin_metadata()   │
│                      │  + Phase 17: hot_register / hot_unregister    │
│                      │  + Phase 17: _lock (RLock) 保护 _plugins     │
│  (NEW in P17)        │  + HotReloadWatcher 子组件                    │
│                      │  + DropInLoader 子组件                        │
│                      │  + ReloadGuard 子组件（Condition + counter） │
├──────────────────────────────────────────────────────────────────────┤
│  异常层（v17 扩展）  │  dispatcher/errors.py (+3 新异常类)           │
│                      │  + PluginNotFoundError / PluginBusyError /    │
│                      │  + DropInLoadError / DropInPathError         │
├──────────────────────────────────────────────────────────────────────┤
│  插件层              │  plugins/base.py + 5 内置                   │
│  (新增)              │  plugins_extra/  drop-in 目录（用户扩展）    │
└──────────────────────────────────────────────────────────────────────┘
```

### 2.1 三种加载路径

| 路径 | 触发方式 | 使用场景 | 状态 |
|------|----------|----------|------|
| **A. 静态注册**（Phase 16）| `BUILTIN_PLUGINS` 列表 | 内置 plugin + 必装 plugin | Phase 16 已有 |
| **B. 显式 API**（Phase 17）| `dispatcher.hot_register(plugin)` | 测试 / 动态 feature flag | Phase 17 新增 |
| **C. drop-in 目录**（Phase 17）| 扫描 `plugins_extra/*.py` | 第三方 plugin / 临时调试 | Phase 17 新增 |

### 2.2 `dispatcher.hot_register` / `hot_unregister` API（v3 沿用 v2）

```python
# dispatcher/goal_dispatcher.py (Phase 17 v3)
from threading import RLock

class GoalDispatcher:
    def __init__(self, plugins=None, middlewares=None):
        self._plugins: List[GoalCommandPlugin] = []
        self._middlewares: List[DispatchMiddleware] = list(middlewares or [])
        self._logger = logging.getLogger("goal_dispatcher")
        self._lock = RLock()
        self._reload_guard = ReloadGuard()
        for p in (plugins or []):
            self.register(p)
        self._validate_mutex_declarations()  # H-1 启动期校验

    def register(self, plugin: GoalCommandPlugin) -> None:
        """静态注册（Phase 16 行为不变，v3 走 _validate_plugin_metadata 统一入口）。"""
        with self._lock:
            self._validate_plugin_metadata(plugin, require_mutex_check=False)
            self._plugins.append(plugin)
            self._plugins.sort(key=lambda p: p.priority)

    def hot_register(self, plugin: GoalCommandPlugin) -> None:
        """运行时注册 plugin（v3 沿用 v2 修订：与 register() 走同一校验入口）。
        
        线程安全：内部用 RLock 保护 _plugins 列表
        
        Raises:
            MutexDeclarationError: plugin name 不符合 kebab-case
            DuplicatePluginNameError: plugin name 重复
            DuplicatePriorityError: plugin priority 重复
            MutexViolationError: mutex 关系与现有 plugin 不一致 / 当前 args 命中冲突
        """
        with self._lock:
            self._validate_plugin_metadata(plugin, require_mutex_check=True)
            self._validate_against_active_dispatch(plugin)
            self._plugins.append(plugin)
            self._plugins.sort(key=lambda p: p.priority)
            self._logger.info(f"[Dispatcher] hot_register: {plugin.name}")

    def hot_unregister(self, name: str, force: bool = False) -> GoalCommandPlugin:
        """运行时卸载 plugin（v3 沿用 v2 修订：busy 检查 + force 开关）。
        
        Args:
            name: 待卸载 plugin 名称
            force: True 跳过 mutex 校验（应急场景；仍不跳过 busy 等待）
        
        Returns:
            被卸载的 plugin 实例
        
        Raises:
            PluginNotFoundError: plugin 不存在
            MutexViolationError: 被其他 plugin 引用为 mutex_with（除非 force=True）
            PluginBusyError: 插件正在执行 execute()（v1 不支持 force-unregister busy）
        """
        with self._lock:
            plugin = self._find_plugin(name)
            if plugin is None:
                raise PluginNotFoundError(name)
            # busy 检查（v2 行为：force 仍 wait_for_idle 30s）
            if self._reload_guard.is_busy(name):
                if not force:
                    raise PluginBusyError(
                        f"Plugin {name!r} 正在执行，请稍后重试"
                    )
                self._logger.warning(
                    f"[Dispatcher] force unload {name!r}（等待执行完成 30s）"
                )
                if not self._reload_guard.wait_for_idle(name, timeout=30.0):
                    self._logger.error(
                        f"[Dispatcher] force unload {name!r} 等待 30s 超时，"
                        f"仍继续 unload（潜在风险）"
                    )
            if not force:
                self._validate_no_mutex_references(name)
            self._plugins.remove(plugin)
            self._logger.info(f"[Dispatcher] hot_unregister: {name}")
            return plugin

    def _validate_plugin_metadata(
        self, plugin: GoalCommandPlugin, *, require_mutex_check: bool
    ) -> None:
        """v2/v3 统一校验入口（register() 和 hot_register() 都走这里）。"""
        if not _PLUGIN_NAME_RE.match(plugin.name):
            raise MutexDeclarationError(
                f"Plugin name {plugin.name!r} 不符合 kebab-case 规范"
            )
        if any(p.name == plugin.name for p in self._plugins):
            raise DuplicatePluginNameError(
                f"Plugin name {plugin.name!r} 重复"
            )
        if any(p.priority == plugin.priority for p in self._plugins):
            raise DuplicatePriorityError(
                f"Plugin priority {plugin.priority} 重复"
            )
        if require_mutex_check:
            self._validate_mutex_against_existing(plugin)

    def _validate_against_active_dispatch(self, plugin: GoalCommandPlugin) -> None:
        """v2/v3：检查 plugin 与当前 dispatch 状态是否冲突。"""
        active_plugins = self._reload_guard.active_plugin_names()
        for mutex_name in plugin.mutex_with:
            if mutex_name in active_plugins:
                raise MutexViolationError(
                    f"Plugin {plugin.name!r} mutex_with 引用正在执行的 "
                    f"plugin {mutex_name!r}，请稍后重试"
                )

    def _validate_mutex_against_existing(
        self, plugin: GoalCommandPlugin
    ) -> None:
        """v2/v3：单 plugin 与现有 plugins 的 mutex 对称性校验。"""
        names = {p.name for p in self._plugins}
        if plugin.name in plugin.mutex_with:
            raise MutexDeclarationError(
                f"Plugin {plugin.name!r} mutex_with 包含自己"
            )
        for mutex_name in plugin.mutex_with:
            if mutex_name not in names:
                raise MutexDeclarationError(
                    f"Plugin {plugin.name!r} mutex_with 引用不存在"
                    f"的 plugin {mutex_name!r}"
                )
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
```

### 2.3 `HotReloadWatcher` 子组件（v3.1 修订：P0-5/6/7/8 + P1-1/9）

**v3.1 关键变更**（相对 v3 新增）：
- `_reload_file` 步骤 1 后 fail-fast 防 ghost plugin 泄漏（P0-8）
- `_rollback_old_plugins` 触发 `critical_failure_callback` 外部告警（P1-9）
- `Callable` 类型加入 import（P1-9 依赖）

**v3 关键变更**（保留）：
- `_file_states: Dict[str, Tuple[float, List[GoalCommandPlugin]]]` 支持单文件多 plugin（P0-5）
- 强制 `project_root` 参数 + `_resolve_drop_in_dir` 路径校验（P0-7）
- `_scan_once` 目录缺失 → log warning + 跳过（P1-1）
- `_reload_file` 严格多 plugin 回滚路径（P0-6）
- 启动期同步首次扫描 + `_initial_scan_done` Event（沿用 v2 P0-4）

```python
# dispatcher/hot_reload_watcher.py (Phase 17 v3.1)
import re
import sys
import threading
import time
import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from threading import Thread, Event

from dispatcher.errors import DropInPathError


# v3 修订：file_stem sanitize（处理中文/特殊字符）
_FILENAME_SAFE_RE = re.compile(r"[^a-zA-Z0-9_.]")


class HotReloadWatcher:
    """轮询 drop-in 目录，检测文件变更 → hot_register / hot_unregister。

    v3.1 关键变更（相对 v3 新增）：
    - _reload_file 步骤 1 后 fail-fast 防 ghost plugin 泄漏（P0-8）
    - _rollback_old_plugins 触发 critical_failure_callback 外部告警（P1-9）

    v3 关键变更：
    - 单文件多 plugin 完全支持（P0-5）：_file_states 存 List
    - 路径安全强制校验（P0-7）：project_root + 软链检测
    - 目录缺失 graceful 跳过（P1-1）：不再误删已加载 plugin
    - reload 多 plugin 完整回滚（P0-6）
    - 启动同步扫描（P0-4）
    """
    
    DEFAULT_POLL_INTERVAL = 5.0
    MIN_POLL_INTERVAL = 0.5
    MAX_POLL_INTERVAL = 60.0
    
    def __init__(
        self,
        dispatcher: "GoalDispatcher",
        drop_in_dir: Path,
        project_root: Path,  # v3 必传（P0-7 路径安全）
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        # v3.1 P1-9 新增：critical failure 外部回调（用于对接 Sentry / 钉钉 / PagerDuty）
        critical_failure_callback: Optional[Callable[[str, List[str]], None]] = None,
    ):
        # 钳制轮询间隔
        self._poll_interval = max(
            self.MIN_POLL_INTERVAL,
            min(poll_interval, self.MAX_POLL_INTERVAL),
        )
        self._dispatcher = dispatcher
        # v3：路径安全解析
        self._project_root = Path(project_root).resolve()
        self._drop_in_dir = self._resolve_drop_in_dir(Path(drop_in_dir))
        # v3 修订：单文件多 plugin 完全支持（P0-5）
        self._file_states: Dict[str, Tuple[float, List["GoalCommandPlugin"]]] = {}
        self._running = False
        self._thread: Optional[Thread] = None
        self._initial_scan_done = Event()
        # v3.1 P1-9：critical failure 回调（rollback 失败、unregister 严重失败时调用）
        self._critical_failure_callback = critical_failure_callback
        self._logger = logging.getLogger("hot_reload_watcher")
    
    @staticmethod
    def _resolve_drop_in_dir(
        project_root: Path, raw: Path, logger: logging.Logger
    ) -> Path:
        """v3 新增：路径安全校验（P0-7）。
        
        规则：
        1. raw 必须为相对路径
        2. resolve() 后必须 is_relative_to(project_root)
        3. 软链跳出 → reject（resolve() 自动解软链）
        4. 不存在但 parent 存在 → 创建
        5. 不存在且 parent 也不存在 → DropInPathError
        
        Raises:
            DropInPathError: 路径不安全（绝对路径 / 跳出 project_root）
        """
        if raw.is_absolute():
            raise DropInPathError(
                f"drop-in 目录必须为相对路径，绝对路径被拒绝：{raw}"
            )
        abs_path = (project_root / raw).resolve()
        if not abs_path.is_relative_to(project_root):
            raise DropInPathError(
                f"drop-in 目录必须在 project_root 内："
                f"{abs_path} ∉ {project_root}"
            )
        if not abs_path.exists():
            try:
                abs_path.mkdir(parents=True, exist_ok=True)
                logger.info(f"[Watcher] 创建 drop-in 目录：{abs_path}")
            except OSError as e:
                raise DropInPathError(
                    f"无法创建 drop-in 目录：{abs_path} ({e})"
                ) from e
        if not abs_path.is_dir():
            raise DropInPathError(f"drop-in 路径不是目录：{abs_path}")
        return abs_path
    
    def start(self) -> None:
        """v3：先同步执行首次扫描，再启动后台线程。"""
        if self._running:
            return
        try:
            self._scan_once()
        except Exception as e:
            self._logger.error(f"[Watcher] 启动扫描异常：{e}")
        self._initial_scan_done.set()
        self._running = True
        self._thread = Thread(
            target=self._watch_loop,
            name="HotReloadWatcher",
            daemon=True,
        )
        self._thread.start()
        self._logger.info(
            f"[Watcher] 启动轮询：{self._drop_in_dir} "
            f"(interval={self._poll_interval}s)"
        )
    
    def stop(self, timeout: float = 5.0) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                self._logger.warning("[Watcher] 线程未在 timeout 内停止")
    
    def wait_initial_scan(self, timeout: Optional[float] = None) -> bool:
        return self._initial_scan_done.wait(timeout=timeout)
    
    def _watch_loop(self) -> None:
        while self._running:
            try:
                self._scan_once()
            except Exception as e:
                self._logger.error(f"[Watcher] 扫描异常：{e}")
            if not self._running:
                break
            time.sleep(self._poll_interval)
    
    def _scan_once(self) -> None:
        """v3 修订：目录不存在 → 跳过而非全量 unload（P1-1）。"""
        # v3 修订（P1-1）：目录缺失 graceful 跳过
        if not self._drop_in_dir.exists():
            self._logger.warning(
                f"[Watcher] drop-in 目录不存在：{self._drop_in_dir}（跳过本次扫描）"
            )
            return
        current_files = {
            p.name: p.stat().st_mtime_ns
            for p in self._drop_in_dir.glob("*.py")
            if not p.name.startswith("_")
        }
        # 1. 新增文件
        for name, mtime in current_files.items():
            if name not in self._file_states:
                self._load_file(self._drop_in_dir / name)
        # 2. mtime 变化（reload）
        for name, (old_mtime, old_plugins) in list(self._file_states.items()):
            if name in current_files:
                new_mtime = current_files[name]
                if new_mtime > old_mtime:
                    self._reload_file(
                        self._drop_in_dir / name, old_plugins
                    )
        # 3. 文件删除
        for name in list(self._file_states):
            if name not in current_files:
                self._unload_file(name)
    
    def _load_file(self, path: Path) -> None:
        """v3 修订：单文件多 plugin 完全支持（P0-5）。"""
        try:
            plugins = DropInLoader.load_from_file(path)
        except Exception as e:
            self._logger.error(f"[Watcher] 加载 {path.name} 失败：{e}")
            return
        loaded: List[GoalCommandPlugin] = []
        for plugin in plugins:
            try:
                self._dispatcher.hot_register(plugin)
                loaded.append(plugin)
            except Exception as e:
                self._logger.error(
                    f"[Watcher] 拒绝注册 {path.name} 中的 "
                    f"{plugin.name!r}：{e}"
                )
        # v3 修订：仅当至少 1 个 plugin 成功注册才记录
        if loaded:
            mtime = path.stat().st_mtime_ns
            self._file_states[path.name] = (mtime, loaded)
    
    def _reload_file(
        self, path: Path, old_plugins: List["GoalCommandPlugin"]
    ) -> None:
        """v3.1 修订：多 plugin reload 完整回滚（P0-6） + 步骤 1 fail-fast（P0-8）。

        策略：
        1. unregister 全部旧 plugin（force=True）
        1.5 v3.1 P0-8：步骤 1 部分失败 → fail-fast 立即回滚 + return（防 ghost plugin）
        2. 加载新实例
        3. register 新 plugin
        4. 任何步骤失败 → 逐个 hot_register 旧 plugin（严格回滚）
        5. 回滚也失败 → critical log + 外部 critical_failure_callback
        """
        # 步骤 1：unregister 全部旧 plugin
        unregistered: List[GoalCommandPlugin] = []
        unregister_failures: List[Tuple[str, Exception]] = []
        for old_plugin in old_plugins:
            try:
                self._dispatcher.hot_unregister(
                    old_plugin.name, force=True
                )
                unregistered.append(old_plugin)
            except Exception as e:
                unregister_failures.append((old_plugin.name, e))
                self._logger.warning(
                    f"[Watcher] reload 时 unregister "
                    f"{old_plugin.name!r} 失败：{e}"
                )

        # v3.1 P0-8：步骤 1 部分失败 → fail-fast 立即回滚 + return（防 ghost plugin 泄漏）
        # 理由：若继续步骤 2/3/4，部分旧 plugin 已不在 dispatcher 中，
        #       而新 plugin 加载/注册可能再次失败 → 出现"既不在 _plugins 也不在 _file_states"的
        #       ghost plugin 实例 → 占用内存 + dispatcher 状态不一致。
        # 处理：先回滚已 unregister 的旧 plugin（恢复 dispatcher 状态），再 return 拒绝步骤 2/3/4。
        if unregister_failures:
            self._logger.error(
                f"[Watcher] reload {path.name} 步骤 1 部分失败："
                f"{len(unregister_failures)} 个 plugin 拒绝 unregister，"
                f"拒绝继续 register（防止 ghost plugin 泄漏），开始回滚"
            )
            self._rollback_old_plugins(unregistered, path.name)
            # v3.1：保留 _file_states 中的旧 plugin 引用（不更新 mtime）
            # ——本次 reload 失败，下次 mtime 变化时再尝试 reload
            return

        # 步骤 2：加载新实例
        try:
            new_plugins = DropInLoader.load_from_file(path)
        except Exception as e:
            # 失败：回滚（重新 hot_register 已 unregister 的旧 plugin）
            self._logger.error(
                f"[Watcher] reload {path.name} 加载新实例失败：{e}，"
                f"开始回滚"
            )
            self._rollback_old_plugins(unregistered, path.name)
            return

        # 步骤 3：register 新 plugin
        loaded: List[GoalCommandPlugin] = []
        register_failures: List[Tuple[str, Exception]] = []
        for new_plugin in new_plugins:
            try:
                self._dispatcher.hot_register(new_plugin)
                loaded.append(new_plugin)
            except Exception as e:
                register_failures.append((new_plugin.name, e))
                self._logger.error(
                    f"[Watcher] reload {path.name} 中拒绝 "
                    f"{new_plugin.name!r}：{e}"
                )

        # 步骤 4：至少 1 个新 plugin 成功 → 更新 file_states
        if loaded:
            mtime = path.stat().st_mtime_ns
            self._file_states[path.name] = (mtime, loaded)
            if register_failures:
                self._logger.warning(
                    f"[Watcher] reload {path.name} 部分成功："
                    f"{len(loaded)}/{len(new_plugins)}"
                )
        else:
            # 新 plugin 全部失败：回滚
            self._logger.error(
                f"[Watcher] reload {path.name} 新 plugin 全部注册失败，"
                f"开始回滚"
            )
            self._rollback_old_plugins(unregistered, path.name)
    
    def _rollback_old_plugins(
        self, old_plugins: List["GoalCommandPlugin"], file_name: str
    ) -> None:
        """v3.1 新增：严格回滚（每个旧 plugin 单独处理，部分失败不阻断）+ 外部告警回调。

        流程：
        1. 逐个 hot_register 旧 plugin（部分失败不阻断其他 plugin）
        2. 收集所有 rollback 失败的 plugin 名
        3. 若有失败 → critical log + 触发 critical_failure_callback（外部告警）
        """
        rollback_failures: List[Tuple[str, Exception]] = []
        for old_plugin in old_plugins:
            try:
                self._dispatcher.hot_register(old_plugin)
            except Exception as e:
                rollback_failures.append((old_plugin.name, e))
                self._logger.error(
                    f"[Watcher] 回滚 {old_plugin.name!r} 也失败：{e}"
                )
        if rollback_failures:
            # v3.1 P1-9 强化：致命 log + 触发外部 critical_failure_callback
            failed_names = [n for n, _ in rollback_failures]
            self._logger.critical(
                f"[Watcher] {file_name} 回滚失败，{len(rollback_failures)} "
                f"个 plugin 永久丢失：{failed_names}"
            )
            # v3.1 P1-9：触发外部 critical failure 回调（用于对接 Sentry / 钉钉 / PagerDuty）
            # 回调在 try/except 内执行，避免回调自身异常影响主流程
            if self._critical_failure_callback is not None:
                try:
                    self._critical_failure_callback(file_name, failed_names)
                except Exception as cb_err:
                    self._logger.error(
                        f"[Watcher] critical_failure_callback 自身异常：{cb_err}"
                    )
    
    def _unload_file(self, name: str) -> None:
        """v3 修订：多 plugin 全部 unload + sys.modules 清理。"""
        if name not in self._file_states:
            return
        _, plugins = self._file_states.pop(name)
        # unregister 全部 plugin
        for plugin in plugins:
            try:
                self._dispatcher.hot_unregister(plugin.name, force=True)
            except Exception as e:
                self._logger.error(
                    f"[Watcher] 卸载 {plugin.name!r} 失败：{e}"
                )
        # sys.modules 清理（P0-2 保留）
        stem = Path(name).stem
        safe_stem = _FILENAME_SAFE_RE.sub("_", stem)  # v3 P2-1 sanitize
        module_key = f"plugins_extra.{safe_stem}"
        if module_key in sys.modules:
            del sys.modules[module_key]
            self._logger.debug(
                f"[Watcher] 清理 sys.modules[{module_key}]"
            )
```

### 2.4 `DropInLoader` 子组件（v3 修订：P1-4 + P2-1）

**v3 关键变更**：
- `try/finally` 包裹 `module_from_spec` + `exec_module`（P1-4）
- `path.stem` sanitize 处理中文/特殊字符（P2-1）
- SRP：sys.modules 主清理责任在 watcher._unload_file（P0-2 保留）

```python
# dispatcher/drop_in_loader.py (Phase 17 v3)
import importlib.util
import inspect
import re
import sys
from pathlib import Path
from typing import List
from plugins.base import GoalCommandPlugin
from dispatcher.errors import DropInLoadError


_FILENAME_SAFE_RE = re.compile(r"[^a-zA-Z0-9_.]")


class DropInLoader:
    """从 .py 文件动态 import + 实例化 GoalCommandPlugin。
    
    v3 修订：
    - sys.modules 主清理责任在 HotReloadWatcher._unload_file（沿用 v2 SRP）
    - module_from_spec 之后任何异常用 try/finally 清理半成品引用（P1-4）
    - path.stem sanitize 防止中文/特殊字符破坏 sys.modules key（P2-1）
    """
    
    MODULE_NAMESPACE = "plugins_extra"
    
    @staticmethod
    def _sanitize_stem(stem: str) -> str:
        """v3 P2-1：sanitize file stem 为合法 Python identifier。"""
        return _FILENAME_SAFE_RE.sub("_", stem)
    
    @staticmethod
    def load_from_file(path: Path) -> List[GoalCommandPlugin]:
        """从 .py 文件加载所有 GoalCommandPlugin 子类实例。"""
        path = Path(path)
        if not path.exists():
            raise DropInLoadError(f"文件不存在：{path}")
        # v3 P2-1：sanitize stem
        safe_stem = DropInLoader._sanitize_stem(path.stem)
        module_name = f"{DropInLoader.MODULE_NAMESPACE}.{safe_stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise DropInLoadError(f"无法构造 spec：{path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        # v3 P1-4：try/finally 包裹 exec_module 失败清理
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            sys.modules.pop(module_name, None)
            raise DropInLoadError(
                f"exec_module 失败：{path} ({type(e).__name__}: {e})"
            ) from e
        # 即使 plugin 收集失败也保留 sys.modules 引用
        # （清理责任在 watcher._unload_file）
        
        plugins = []
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if (
                inspect.isclass(obj)
                and issubclass(obj, GoalCommandPlugin)
                and obj is not GoalCommandPlugin
                and obj.__module__ == module_name
            ):
                plugins.append(obj())
        if not plugins:
            raise DropInLoadError(
                f"{path} 未定义任何 GoalCommandPlugin 子类"
            )
        return plugins
```

### 2.5 `ReloadGuard` 子组件（v3 修订：P1-3 Condition 替代 Event）

**v3 关键变更**：
- `threading.Condition` 替代 `Event + 1s 切片`（P1-3，0 延迟唤醒）
- `notify_all` 支持多线程并发等待
- `self._unbalanced_exit_count` 计数器暴露给外部 metrics（P2-7）

```python
# dispatcher/reload_guard.py (Phase 17 v3)
import threading
import time
import logging
from threading import RLock, Condition
from typing import Dict, Set


class ReloadGuard:
    """reload 操作的事务性保护（v3 重写：Condition 替代 Event）。
    
    v3 修订：
    - Condition 替代 Event + 1s 切片（P1-3）：notify 立即唤醒，0 额外延迟
    - 单 Condition + per-name counter 替代 per-name Event
    - 暴露 _unbalanced_exit_count 给外部 metrics（P2-7）
    """
    
    DEFAULT_IDLE_TIMEOUT = 10.0
    
    def __init__(self):
        self._cond = Condition(RLock())  # v3：Condition 内部 RLock
        self._active_counts: Dict[str, int] = {}
        # v3 新增（P2-7）：不平衡退出计数（enter/exit 不配对）
        self._unbalanced_exit_count: int = 0
        self._logger = logging.getLogger("reload_guard")
    
    def enter_execute(self, plugin_name: str) -> None:
        with self._cond:
            self._active_counts[plugin_name] = (
                self._active_counts.get(plugin_name, 0) + 1
            )
    
    def exit_execute(self, plugin_name: str) -> None:
        with self._cond:
            current = self._active_counts.get(plugin_name, 0)
            if current <= 0:
                # 防御性：exit 比 enter 多 → 严重 bug
                self._unbalanced_exit_count += 1
                self._logger.error(
                    f"[ReloadGuard] exit_execute({plugin_name}) 计数为 0，"
                    f"enter/exit 不配对！total={self._unbalanced_exit_count}"
                )
                return
            self._active_counts[plugin_name] = current - 1
            if self._active_counts[plugin_name] == 0:
                del self._active_counts[plugin_name]
                # v3 修订：notify_all 唤醒所有等待者
                self._cond.notify_all()
    
    def is_busy(self, plugin_name: str) -> bool:
        with self._cond:
            return self._active_counts.get(plugin_name, 0) > 0
    
    def active_plugin_names(self) -> Set[str]:
        with self._cond:
            return {
                name for name, count in self._active_counts.items() if count > 0
            }
    
    def wait_for_idle(
        self, plugin_name: str, timeout: float = DEFAULT_IDLE_TIMEOUT
    ) -> bool:
        """v3：Condition.wait 替代 Event.wait + 1s 切片。
        
        Returns:
            True = 已 idle；False = 超时仍有引用
        """
        deadline = time.time() + timeout
        with self._cond:
            while self._active_counts.get(plugin_name, 0) > 0:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                # Condition.wait 被 notify 时立即唤醒（0 延迟）
                self._cond.wait(timeout=remaining)
            return True
    
    @property
    def unbalanced_exit_count(self) -> int:
        """v3 P2-7：暴露给外部 metrics。"""
        with self._cond:
            return self._unbalanced_exit_count
```

### 2.6 CLI 集成（v3 修订：P0-7 type 校验 + P1-5 default 兜底）

**v3 关键变更**：
- 路径 type validator 早期拒绝非法 `--hot-reload-dir`（P0-7 第一层防护）
- mutex group 内 `default=True`（argparse 合法用法，沿用 v2）
- facade 层 `assert args.hot_reload is not None` 兜底（P1-5）

```python
# cli/parser.py (Phase 17 v3)
import argparse
from pathlib import Path


def _validate_drop_in_dir(value: str) -> str:
    """v3 P0-7 第一层：CLI 层早期校验。
    
    拒绝：
    - 绝对路径
    - 包含 '..' 的相对路径（粗略检查，watcher 还会 resolve 二次校验）
    """
    p = Path(value)
    if p.is_absolute():
        raise argparse.ArgumentTypeError(
            f"--hot-reload-dir 必须为相对路径：{value}"
        )
    if ".." in p.parts:
        raise argparse.ArgumentTypeError(
            f"--hot-reload-dir 不能包含 '..'：{value}"
        )
    return value


def parse_arguments():
    """V3 CLI 解析层（含 hot-reload 互斥 group）。"""
    parser = argparse.ArgumentParser(
        description='Trae Agent 调度脚本 v3.0',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # ...（保留所有 Phase 16 的 flag，省略）
    
    # v3 P0-7 / P0-1：hot-reload 互斥 group + path 校验
    hot_reload_group = parser.add_mutually_exclusive_group()
    hot_reload_group.add_argument(
        '--hot-reload',
        dest='hot_reload',
        action='store_true',
        default=True,
        help='Phase 17 启用插件热加载（默认开启）',
    )
    hot_reload_group.add_argument(
        '--no-hot-reload',
        dest='hot_reload',
        action='store_false',
        help='禁用插件热加载（生产环境推荐；与 --hot-reload 互斥）',
    )
    parser.add_argument(
        '--hot-reload-dir',
        type=_validate_drop_in_dir,
        default='plugins_extra',
        help='drop-in 目录路径（相对 project_root，必须不含 ..，默认 plugins_extra/）',
    )
    parser.add_argument(
        '--hot-reload-interval',
        type=float,
        default=5.0,
        help='轮询间隔（秒，默认 5.0；范围 [0.5, 60.0]）',
    )
    
    return parser.parse_args()
```

### 2.7 v3 已知限制（文档化）

1. ~~单文件单 plugin 假设~~ **v3 删除**：单文件多 plugin 完全支持（§2.3 P0-5 修复）
2. **plugin 必须 stateless**：与 Phase 16 风险-9 一致。reload 不保留运行时状态。
3. **sys.modules 中旧 plugin 类引用**：reload 旧 plugin 的 class 对象仍被旧 plugin 实例引用，不会立即 GC（v2 限制，v3 P0-2 已尽量清理 entry 引用）。
4. **多进程不支持**：v1 不支持 fork 后 hot reload 同步。
5. **drop-in 目录路径强制 project_root 内**：绝对路径 + `..` 被 reject（P0-7 三层防护）。
6. **同进程内 hot_register 串行调用**：不支持并发 hot_register（`self._lock` 串行化，但 sort 顺序依赖调用时序）。
7. **多次 hot_register 相同 priority**：稳定排序保证按 hot_register 调用顺序排列。

### 2.8 异常类扩展（v3）

```python
# dispatcher/errors.py (Phase 17 v3 扩展)
# ... 保留 Phase 16 全部 5 类异常 ...


class PluginNotFoundError(DispatcherError):
    """v3 新增：hot_unregister 时 plugin 不存在。
    
    抛出场景：dispatcher.hot_unregister('unknown')。
    """


class PluginBusyError(DispatcherError):
    """v3 新增：plugin 正在执行 execute()，无法立即 unregister。
    
    抛出场景：dispatcher.hot_unregister('foo') 而 foo 当前正被 dispatch。
    不适用 force=True 场景（force 会 wait_for_idle 30s）。
    """


class DropInLoadError(DispatcherError):
    """v3 新增：drop-in 文件加载失败。
    
    抛出场景：
    - 文件不存在
    - importlib.util.spec_from_file_location 失败
    - exec_module 抛 ImportError / SyntaxError
    - 文件无 GoalCommandPlugin 子类
    """


class DropInPathError(DispatcherError):
    """v3 新增：drop-in 路径不安全（P0-7）。
    
    抛出场景：
    - 绝对路径
    - resolve() 后跳出 project_root
    - 路径不是目录且 parent 也不存在
    """
```

### 2.9 `_start_hot_reload_if_enabled`（v3 新增：P1-7/8）

```python
# facade.py v3 新增
import atexit
import logging
import weakref
from pathlib import Path
from threading import RLock


# v3 P1-8：模块级跟踪所有已启动的 watcher（weakref 防泄漏）
_watcher_refs: "set[weakref.ref]" = set()
_watcher_tracking_lock = RLock()
_logger = logging.getLogger("facade")


def _start_hot_reload_if_enabled(
    dispatcher: "GoalDispatcher",
    args,
    project_root: Path,
) -> Optional["HotReloadWatcher"]:
    """v3 新增：根据 args 启动 hot reload watcher。
    
    行为：
    - args.hot_reload == False → 不启动，返回 None
    - args.hot_reload == True → 启动 watcher + atexit 注册清理
    - args.hot_reload is None → assert 兜底（P1-5）
    
    Args:
        dispatcher: 已构造的 GoalDispatcher
        args: parse_arguments() 结果
        project_root: 项目根目录（Path；用于 drop-in 路径安全校验）
    
    Returns:
        HotReloadWatcher 实例 or None
    """
    # v3 P1-5 兜底
    enabled = getattr(args, 'hot_reload', None)
    assert enabled is not None, (
        "args.hot_reload is None — parser 解析异常，"
        "请检查 --hot-reload/--no-hot-reload 配置"
    )
    if not enabled:
        _logger.info("[facade] hot reload 显式禁用（--no-hot-reload）")
        return None
    
    # v3 P0-7 第三层：facade 串联（即使 parser 漏过 + watcher 兜底）
    drop_in_dir = Path(getattr(args, 'hot_reload_dir', 'plugins_extra'))
    poll_interval = float(getattr(args, 'hot_reload_interval', 5.0))
    
    try:
        from dispatcher.hot_reload_watcher import HotReloadWatcher
        watcher = HotReloadWatcher(
            dispatcher=dispatcher,
            drop_in_dir=drop_in_dir,
            project_root=project_root,  # v3 必传
            poll_interval=poll_interval,
        )
    except Exception as e:
        # watcher 构造失败不阻断 main 流程（生产友好）
        _logger.error(f"[facade] watcher 启动失败：{e}")
        return None
    
    # 启动 + 等待首次扫描完成
    watcher.start()
    if not watcher.wait_initial_scan(timeout=30.0):
        _logger.warning(
            "[facade] watcher 初始扫描 30s 超时（drop-in 目录异常大？）"
        )
    
    # v3 P1-8：weakref 跟踪 + atexit 注册（多 dispatcher 防重复）
    with _watcher_tracking_lock:
        _watcher_refs.add(weakref.ref(watcher, _watcher_refs.discard))
        # 只对第一个 watcher 注册 atexit（避免重复 cleanup）
        if len(_watcher_refs) == 1:
            atexit.register(_cleanup_all_watchers)
    
    return watcher


def _cleanup_all_watchers() -> None:
    """v3 P1-8：atexit hook，清理所有活跃 watcher。"""
    with _watcher_tracking_lock:
        refs = list(_watcher_refs)
    for ref in refs:
        watcher = ref()
        if watcher is not None:
            _safe_watcher_stop(watcher)


def _safe_watcher_stop(watcher) -> None:
    """v3 P1-7：异常隔离的 stop，atexit 不能抛异常。"""
    try:
        watcher.stop(timeout=5.0)
    except Exception as e:
        _logger.warning(f"[facade] watcher.stop 异常：{e}")
```

### 2.10 dispatch() snapshot 契约（v3 文档化 P1-2）

**v3 显式契约**：

> `dispatch()` 主流程不持 `self._lock`（避免与 `hot_unregister` 的 `wait_for_idle` 死锁）。
> 单次 dispatch 使用 `_plugins` 列表的**当前快照**——`(matched = [...])` 之后即使 `_plugins` 被 hot_register / hot_unregister 修改，本次 dispatch 仍跑**已捕获的 plugin 实例**。
> 
> 副作用：本次 dispatch 期间如有新 plugin hot_register，相同 name 的旧 plugin 仍会被本次 dispatch 使用；新 plugin 由后续 dispatch 调度。

```python
# dispatcher/goal_dispatcher.py v3 dispatch() 实现
def dispatch(self, args, ctx):
    # ... 风险-5 dry_run 短路（保留 Phase 16 行为）...
    
    # ... 中间件 before ...
    
    # v3 文档化：dispatch 主流程不持 self._lock
    # snapshot 语义：matched = [...] 拿 _plugins 当前快照
    # 即使后续 _plugins 被修改，本次 dispatch 仍跑捕获的 plugin 实例
    try:
        matched = [p for p in self._plugins if p.matches(args)]
        if not matched:
            result = DispatchResult(...)
            return result
        if len(matched) > 1:
            raise MutexViolationError(...)
        plugin = matched[0]  # 捕获实例引用
        
        # v3：enter_execute 必须在 _lock 外
        # 理由：wait_for_idle 持 self._lock 时，enter 不能重入
        self._reload_guard.enter_execute(plugin.name)
        exc_to_pass = None
        try:
            success = plugin.execute(args, ctx)
            result = DispatchResult(...)
            return result
        except BaseException as exc:
            exc_to_pass = exc
            result = DispatchResult(...)
            return result
        finally:
            try:
                plugin.cleanup(ctx, exc_to_pass)
            except Exception as e:
                self._logger.warning(...)
            finally:
                # v3：exit_execute 必须在 _lock 外
                self._reload_guard.exit_execute(plugin.name)
    finally:
        # ... 中间件 after ...
```

---

## 3. 兼容性矩阵（19 + 5 + 1 = 25 个兼容点）

### 3.1 Phase 16 兼容承诺（19 个，全部保留）

（沿用 [PHASE16_PLAN.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE16_PLAN.md) §3.4）

### 3.2 Phase 17 新增兼容点（5 + 1 个）

| # | 兼容点 | 验证方式 | 状态 |
|---|--------|----------|------|
| 20 | `dispatcher.hot_register(plugin)` 可调用 | test_v4_hot_reload.py | ✅ |
| 21 | `dispatcher.hot_unregister(name)` 可调用 | test_v4_hot_reload.py | ✅ |
| 22 | `HotReloadWatcher` 启动/停止/扫描 | test_v4_hot_reload_watcher.py | ✅ |
| 23 | `DropInLoader.load_from_file()` 加载 | test_v4_drop_in_loader.py | ✅ |
| 24 | `plugins_extra/*.py` 自动加载 | test_v4_hot_reload_integration.py | ✅ |
| **25** | **`dispatch()` snapshot 契约**（v3 新增）| test_v4_dispatch_snapshot.py | ✅ |

### 3.3 CLI 行为不变承诺

- 不传 `--hot-reload*` → `args.hot_reload=True`（v3 默认开启）
- `--no-hot-reload` → `args.hot_reload=False`（生产模式）
- 两者互斥（argparse 强制）
- `--hot-reload-dir` 路径非法 → argparse `ArgumentTypeError`（P0-7 第一层）
- 老 CLI 调用方（无 hot-reload 概念）100% 不受影响

---

## 4. 测试策略（v3.1 详化：102+ 测试 + 7 P0 专项 case 矩阵）

### 4.1 单元测试（6 个新文件）

| 测试文件 | 覆盖 | 预计测试数 |
|----------|------|-----------|
| test_v4_hot_reload.py | hot_register / hot_unregister / busy / 冲突 | 22+ |
| test_v4_hot_reload_watcher.py | watcher 启动/停止/扫描/mtime/initial scan done | 20+ |
| test_v4_drop_in_loader.py | drop-in .py 加载/失败/多类 | 14+ |
| test_v4_hot_reload_integration.py | drop-in 目录 + 实际加载（in-process）| 12+ |
| test_v4_reload_guard.py | 引用计数/Condition/超时/unbalanced counter | 14+ |
| test_v4_cli_hot_reload.py | --hot-reload / --no-hot-reload 互斥 / 路径校验 | 12+ |
| test_v4_dispatch_snapshot.py | v3 新增兼容点 25：dispatch 期间 _plugins 变更 | 8+ |
| **小计** | | **102+** |

### 4.2 集成测试（双版本，P2-6）

- `test_v4_hot_reload_integration.py`：**in-process 集成**（直接构造 dispatcher + watcher）
- `test_v4_hot_reload_subprocess.py`：**subprocess 真集成**（subprocess.run 启动 CLI，验证端到端）

### 4.3 v3 P0/P1 专项测试（详化 P1-6：每个 P0 至少 3 positive + 3 negative）

#### 4.3.1 P0-1（CLI 互斥）：test_v4_p0_1_cli_mutex.py

| # | 类型 | case |
|---|------|------|
| P | positive | `--hot-reload` → `args.hot_reload=True` |
| P | positive | `--no-hot-reload` → `args.hot_reload=False` |
| P | positive | 不传任何 flag → `args.hot_reload=True`（默认）|
| N | negative | `--hot-reload --no-hot-reload` → argparse SystemExit |
| N | negative | `--no-hot-reload --hot-reload`（反向顺序）→ argparse SystemExit |
| N | negative | 短名缩写 `--hot-rel --no-hot` → argparse SystemExit |

#### 4.3.2 P0-2（sys.modules 清理）：test_v4_p0_2_sysmodules_cleanup.py

| # | 类型 | case |
|---|------|------|
| P | positive | 单次 load → sys.modules 含 entry |
| P | positive | 100 次 reload → sys.modules entry 数稳定 ≤ 1（v3 sanitize 后）|
| P | positive | unload → sys.modules 不含 entry |
| N | negative | 文件名含中文 → sys.modules key sanitize（中文→下划线）|
| N | negative | 文件名含 `..` → parser 拒绝 |
| N | negative | 文件名含 `/` → parser 拒绝 |

#### 4.3.3 P0-3（校验对齐）：test_v4_p0_3_validation_alignment.py

| # | 类型 | case |
|---|------|------|
| P | positive | register() 与 hot_register() 对同样合法 plugin 都成功 |
| P | positive | register() 与 hot_register() 对非法 name 都抛 MutexDeclarationError |
| P | positive | register() 与 hot_register() 对重复 name 都抛 DuplicatePluginNameError |
| N | negative | hot_register() 与现有 mutex 不对称 → 抛 MutexDeclarationError |
| N | negative | hot_register() 命中正在执行的 plugin mutex → 抛 MutexViolationError |
| N | negative | register() 走 require_mutex_check=False 路径，hot_register() 走 True，验证两路径都用 _validate_plugin_metadata |

#### 4.3.4 P0-4（启动竞态）：test_v4_p0_4_initial_scan_race.py

| # | 类型 | case |
|---|------|------|
| P | positive | start() 返回后 _initial_scan_done.is_set() == True |
| P | positive | start() 返回后 dispatcher._plugins 包含所有 drop-in plugin |
| P | positive | wait_initial_scan(0.1) 在已 set 时立即返回 True |
| N | negative | start() 期间 watcher 异常（drop-in 目录权限）→ _initial_scan_done 仍 set |
| N | negative | 启动 1000 个 drop-in 文件 → start() 在合理时间（< 5s）内完成首次扫描 |
| N | negative | 重复调用 start() → 第二次 no-op（不重启线程）|

#### 4.3.5 P0-5（多 plugin 文件）：test_v4_p0_5_multi_plugin.py

| # | 类型 | case |
|---|------|------|
| P | positive | 单文件 3 个 plugin → 全部 hot_register 成功，_file_states 存 List 长度 3 |
| P | positive | 单文件 3 个 plugin 全部 unregister → reload 后 3 个新 plugin 全部 hot_register |
| P | positive | 单文件 3 个 plugin 删文件 → 3 个 plugin 全部 unregister（无僵尸）|
| N | negative | 单文件 plugin A 成功 + plugin B 失败 → 只记录 A（_file_states 含 A）|
| N | negative | reload 时 2/3 失败 → file_states 更新为含 2 个新 plugin（不丢）|
| N | negative | reload 全部失败 → 回滚全部旧 plugin |

#### 4.3.6 P0-6（reload 回滚）：test_v4_p0_6_reload_rollback.py

| # | 类型 | case |
|---|------|------|
| P | positive | 旧 plugin 全部 unregister + 新 plugin 全部 register → file_states 更新 |
| P | positive | 加载新文件失败 → 旧 plugin 全部 hot_register 回滚 |
| P | positive | 新 plugin 全部 register 失败 → 旧 plugin 全部 hot_register 回滚 |
| N | negative | 回滚时部分失败 → critical log + 部分 plugin 永久丢失（文档化）|
| N | negative | reload 时 unregister 失败 1 个 → 仍继续 register 新 plugin（fail-soft）|
| N | negative | reload 时 mutex 不对称 → hot_register 失败触发回滚 |

#### 4.3.7 P0-7（路径安全）：test_v4_p0_7_path_safety.py

| # | 类型 | case |
|---|------|------|
| P | positive | `--hot-reload-dir plugins_extra` → 合法 |
| P | positive | `--hot-reload-dir ./sub/plugins` → 合法（不跳出 project_root）|
| P | positive | `--hot-reload-dir ../sibling` → 合法（如果在 project_root 内）|
| N | negative | `--hot-reload-dir /etc/passwd` → argparse 拒绝（绝对路径）|
| N | negative | `--hot-reload-dir ../../etc` → argparse 拒绝（含 ..）|
| N | negative | watcher 启动时 project_root 改路径软链跳出 → DropInPathError |
| **N4** | **negative** | **v3.1 P1-10 新增：project_root 本身是软链（resolve 后解链 → 路径不一致）** → DropInPathError |
| **N5** | **negative** | **v3.1 P1-10 新增：drop_in_dir 是软链，指向 project_root 外部（/tmp/evil）** → DropInPathError（resolve() 自动解软链后 is_relative_to 失败）|

**v3.1 N4 测试设计要点**：
- 测试 setup：在 tmp_path 下创建真实目录 `real_root/`，再 `real_root.symlink_to(tmp_path/"link_root")`
- 调用 `HotReloadWatcher(dispatcher, drop_in_dir=Path("plugins_extra"), project_root=link_root)` 
- 期望：`Path(link_root).resolve() == real_root`（watcher 内部 resolve 处理）
- 关键断言：watcher 不抛 DropInPathError，drop_in_dir 解析为 `real_root/plugins_extra`（或创建）

**v3.1 N5 测试设计要点**：
- 测试 setup：tmp_path 下创建 `project_root/`，`project_root/evil_link → /tmp/external_dir`
- 调用 `HotReloadWatcher(dispatcher, drop_in_dir=Path("evil_link"), project_root=project_root)`
- 期望：`Path(project_root / "evil_link").resolve()` → `/tmp/external_dir`
- 关键断言：watcher 构造时抛 `DropInPathError("drop-in 目录必须在 project_root 内：/tmp/external_dir ∉ /tmp/xxx/project_root")`
- 验证三层防护有效性：CLI 允许 `evil_link`（仅检查 `..` 和绝对路径，软链不在 CLI 层），但 watcher `_resolve_drop_in_dir` 解析软链后 reject

### 4.4 兼容性回归（沿用 Phase 16 套件）

- `test_v3_integration.py`：19 个 compat points 全部继续通过
- `test_v3_plugins.py`：5 个内置 plugin 契约不变
- `test_v3_dispatcher.py`：register 语义不变
- `test_v4_dispatch_snapshot.py`：新增 compat point 25

### 4.5 关键验证清单

- ✅ hot_register 与 register 行为等价（走 _validate_plugin_metadata）
- ✅ hot_unregister 不破坏现有 plugin
- ✅ watcher 线程 graceful shutdown
- ✅ drop-in 加载失败不破坏 dispatcher
- ✅ reload 失败完整回滚（多 plugin 全部恢复）
- ✅ 关闭热加载时行为与 Phase 16 完全一致
- ✅ CLI 互斥 group 工作正常
- ✅ sys.modules 清理验证（reload 100 次不累积）
- ✅ 引用计数正确（Condition.notify_all 立即唤醒）
- ✅ **单文件多 plugin 完全支持**（v3 P0-5）
- ✅ **路径穿越三层防护**（v3 P0-7：parser + watcher + facade）
- ✅ **dispatch snapshot 契约**（v3 P1-2）
- ✅ **目录缺失 graceful 跳过**（v3 P1-1）

---

## 5. 实施阶段（v3 调整：先 errors.py 再实施）

### 5.1 阶段 0：异常类扩展（v3 新增前置）

1. `dispatcher/errors.py` 新增 `PluginNotFoundError` / `PluginBusyError` / `DropInLoadError` / `DropInPathError`
2. 单测 `test_v4_errors.py`：每个异常类 1 个测试

### 5.2 阶段 1：核心 API + ReloadGuard（v3 沿用 v2）

1. `dispatcher/reload_guard.py` 新增（v3 Condition 版）
2. `dispatcher/goal_dispatcher.py`：
   - 抽出 `_validate_plugin_metadata(p, *, require_mutex_check)` 统一入口
   - 新增 `hot_register` / `hot_unregister`
   - `dispatch()` 的 `plugin.execute()` 块包裹 `enter_execute/exit_execute`（_lock 外）
3. 单元测试 `test_v4_hot_reload.py` + `test_v4_reload_guard.py` + `test_v4_dispatch_snapshot.py`

### 5.3 阶段 2：DropInLoader（v3 修订）

1. `dispatcher/drop_in_loader.py` 新增（v3 try/finally + sanitize）
2. 单元测试 `test_v4_drop_in_loader.py` + `test_v4_p0_2_sysmodules_cleanup.py`

### 5.4 阶段 3：HotReloadWatcher（v3.1 修订：P0-5/6/7/8 + P1-1/9）

1. `dispatcher/hot_reload_watcher.py` 新增（v3.1 多 plugin + 路径安全 + 完整回滚 + **步骤 1 fail-fast 防 ghost + critical_failure_callback 外部告警**）
2. 单元测试 `test_v4_hot_reload_watcher.py`
3. v3 专项测试 `test_v4_p0_4_initial_scan_race.py` + `test_v4_p0_5_multi_plugin.py` + `test_v4_p0_6_reload_rollback.py` + `test_v4_p0_7_path_safety.py`（**含 §4.3.7 N4/N5 软链负测试**）

### 5.5 阶段 4：CLI + facade 集成（v3 三层防护）

1. `cli/parser.py` 新增 3 个 CLI flag + 1 个互斥 group + 1 个 type validator
2. `facade.py` 新增 `_start_hot_reload_if_enabled` + `_resolve_drop_in_dir` + `_safe_watcher_stop` + `_cleanup_all_watchers`
3. `facade._dispatch_through_v3` 调用 `_start_hot_reload_if_enabled(dispatcher, args, project_root)`
4. 单元测试 `test_v4_cli_hot_reload.py` + `test_v4_p0_1_cli_mutex.py` + `test_v4_p0_7_path_safety.py`

### 5.6 阶段 5：测试 + 验证

1. 风险评估清单（§6）逐项验证
2. 462 旧测试 100% 通过
3. 102+ 新测试 100% 通过
4. v3 专项 7 个 P0 测试（包含 v2 4 个 + v3 3 个）100% 通过
5. 集成测试（in-process + subprocess 双版本）
6. 更新 [PHASE16_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE16_FINAL_REPORT.md)（追加 Phase 17 一节）

### 5.7 阶段 6：commit + tag

1. `git commit` + `tag phase-17-plugin-hot-reload`

---

## 6. 风险评估清单（v3.1 修订：4 + 3 + 7 + 5 + 9 = 28 项风险）

> **v3.1 P1-11 修订说明**：v3 标题误写"23 项"——实际风险表共 28 项。按修订来源 5 段分组：
>
> | 段 | 数量 | 来源 | 风险 ID |
> |---|------|------|---------|
> | A | 4 | v1→v2 解决的 P0（§0.1） | R-11, R-12, R-13, R-14 |
> | B | 3 | v2→v3 新发现的 P0（§0.2） | R-15, R-16, R-17 |
> | C | 7 | v2→v3 解决的 P1（§0.3 中"代码/契约"类 7 项，P1-6 在 §4 测试侧解决）| R-3, R-5, R-18, R-19, R-20, R-21, R-22 |
> | D | 5 | v2→v3 解决的 P2（§0.4 中"代码修改"类 5 项，P2-3/5/8 在 §2.7+§7+§4.1 文档化）| R-23, R-24, R-25, R-26, R-27 |
> | E | 9 | Phase 17 v1 原始需求识别 + v3 文档化 P2（§2.7 限制 + 性能 + 第三方信任） | R-1, R-2, R-4, R-6, R-7, R-8, R-9, R-10, R-28 |
> | **合计** | **28** | | R-1 ~ R-28 |

### 6.1 段 A：v1→v2 解决的 P0（4 项）

| 风险 | 严重度 | 描述 | v3.1 缓解策略 | 状态 |
|------|--------|------|------------|------|
| **R-11** | P0 | watcher.start() 启动竞态 | 同步首次扫描 + `_initial_scan_done` Event | 阶段 3 |
| **R-12** | P0 | DropInLoader sys.modules 内存泄漏 | watcher._unload_file 显式 pop sys.modules + sanitize | 阶段 3 |
| **R-13** | P0 | hot_register 与 register 校验不对齐 | `_validate_plugin_metadata` 统一入口 | 阶段 1 |
| **R-14** | P0 | CLI --hot-reload / --no-hot-reload 互斥冲突 | argparse `add_mutually_exclusive_group` | 阶段 4 |

### 6.2 段 B：v2→v3 新发现的 P0（3 项）

| 风险 | 严重度 | 描述 | v3.1 缓解策略 | 状态 |
|------|--------|------|------------|------|
| **R-15** | P0 | 多 plugin 文件 _file_states 行为不一致 | `Dict[str, List[Plugin]]` + 严格 unregister 全部 | 阶段 3 |
| **R-16** | P0 | reload 回滚不完整 | 逐个 hot_register 旧 + critical log + 文档化"部分失败" | 阶段 3 |
| **R-17** | P0 | 路径穿越防护缺失 | 三层防护：parser type + watcher resolve + facade 串联 | 阶段 4 |

### 6.3 段 C：v2→v3 解决的 P1（7 项）

| 风险 | 严重度 | 描述 | v3.1 缓解策略 | 状态 |
|------|--------|------|------------|------|
| **R-3** | P1 | 轮询间隔与 CPU 开销权衡 | 默认 5s + 钳制 [0.5s, 60s] | 阶段 3 |
| **R-5** | P1 | hot_unregister 触发 mutex 关系断裂 | 默认校验 + force=True 应急开关 | 阶段 1 |
| **R-18** | P1 | 同一 plugin 并发 execute 引用计数错误 | ReloadGuard Dict 引用计数 | 阶段 1 |
| **R-19** | P1 | wait_for_idle 1s 切片延迟 | v3 改用 `Condition.notify_all`（0 延迟）| 阶段 1 |
| **R-20** | P1 | 目录不存在时全量 unload | v3 改为 log warning + 跳过 | 阶段 3 |
| **R-21** | P1 | dispatch _lock 契约未文档化 | v3 §2.10 显式 snapshot 契约 + 兼容点 25 | 阶段 1 |
| **R-22** | P1 | atexit hook 重复注册 | v3 weakref 跟踪 + 仅首次注册 | 阶段 4 |

### 6.4 段 D：v2→v3 解决的 P2（5 项）

| 风险 | 严重度 | 描述 | v3.1 缓解策略 | 状态 |
|------|--------|------|------------|------|
| **R-23** | P2 | facade._start_hot_reload_if_enabled 内部实现要求不明 | v3 §2.9 列出全部要求（atexit + weakref + 异常隔离）| 阶段 4 |
| **R-24** | P2 | exit_execute 防御性 log 无 metrics | v3 `_unbalanced_exit_count` 计数器 | 阶段 1 |
| **R-25** | P2 | path.stem 含特殊字符破坏 sys.modules | v3 `_FILENAME_SAFE_RE` sanitize | 阶段 2 |
| **R-26** | P2 | module_from_spec 之后异常无清理 | v3 try/finally 包裹 | 阶段 2 |
| **R-27** | P2 | 集成测试 in-process vs subprocess 不明确 | v3 §4.2 双版本 | 阶段 5 |

### 6.5 段 E：v1 原始需求 + v3 文档化 P2（9 项）

| 风险 | 严重度 | 描述 | v3.1 缓解策略 | 状态 |
|------|--------|------|------------|------|
| **R-1** | P0 | plugin reload 时若有正在执行的任务 → 中断 | ReloadGuard Dict 引用计数 + `Condition.notify_all` | 阶段 1 |
| **R-2** | P0 | drop-in 与 BUILTIN_PLUGINS name 冲突 | hot_register 走 `_validate_plugin_metadata` H-6 校验 | 阶段 1 |
| **R-4** | P0 | reload 失败如何回滚 | watcher._reload_file 异常分支逐个 hot_register 旧 plugin（v3 多 plugin 完整 + **v3.1 P0-8 步骤 1 fail-fast 防 ghost**）| 阶段 3 |
| **R-6** | P1 | plugin 内部状态如何处理 | plugin 必须 stateless | 文档（§2.7 限制 2）|
| **R-7** | P1 | 多进程 fork 后 plugin 实例不同步 | v1 仅支持单进程 | 文档（§2.7 限制 4）|
| **R-8** | P2 | 第三方 drop-in 引入恶意代码 | 文档警示 | 文档 |
| **R-9** | P2 | watcher 线程泄漏 | `daemon=True` + atexit hook + weakref 跟踪 | 阶段 4 |
| **R-10** | P2 | 大量 drop-in 文件扫描性能 | 1000 文件 < 5s 启动阻塞（v3 量化）| 性能测试 |
| **R-28** | P2 | sort 稳定性 + 多次 hot_register 顺序契约 | v3 §2.7 限制 7 文档化 + §2.10 snapshot 契约 | 文档 |

### 6.6 v3.1 新增强调（p0-8 + p1-9）

> **R-4 增强（v3.1 P0-8）**：`_reload_file` 步骤 1（unregister 旧 plugin）部分失败时 → fail-fast 立即回滚已 unregister 的旧 plugin + `return` 拒绝步骤 2/3/4。**防 ghost plugin 泄漏**（部分旧 plugin 已不在 dispatcher 中，若继续 register 新 plugin 失败会出现"既不在 _plugins 也不在 _file_states"的 ghost 实例）。
>
> **R-16 增强（v3.1 P1-9）**：`_rollback_old_plugins` 失败时除 critical log 外，**触发 `critical_failure_callback(file_name, failed_names)`**——外部可对接 Sentry / 钉钉 / PagerDuty。回调异常被隔离（try/except 包裹），不污染主流程。

---

## 7. 决策记录

### 7.1 v1 → v2 决策（已记录在 v2 版本）

| 问题 | v1 候选 | v2 决策 |
|------|---------|---------|
| CLI 互斥 | store_true × 2 vs mutex group | mutex group |
| sys.modules 清理 | watcher 清理 vs loader 清理 | watcher 清理 |
| 引用计数 | Set vs Dict | Dict |
| 启动扫描 | 异步 vs 同步 | 同步首次 + 异步后续 |
| 默认 hot_reload | True vs False | True |
| 轮询间隔钳制 | 文档 vs 代码 | 代码 [0.5s, 60s] |
| 路径安全 | 不校验 vs 强制 project_root | 强制 project_root |
| busy 超时 | 10s vs 30s | 10s（wait_for_idle 默认）|
| force=True 是否跳过 busy | 跳过 vs 不跳过 | 不跳过（但 wait 30s）|

### 7.2 v2 → v3 新增决策

| 问题 | v2 候选 | v3 决策 | 理由 |
|------|---------|---------|------|
| 单文件多 plugin | 限制单文件单 plugin | Dict[str, List[Plugin]] 完全支持 | 实际场景有需求（feature flag 组合）|
| reload 回滚 | warn + skip | 严格逐个 hot_register 旧 + critical log | 防止"既不是旧也不是新"状态 |
| 路径安全层级 | watcher 1 层 | parser + watcher + facade 3 层 | 早期拒绝 + 二次校验 + 串联兜底 |
| Condition vs Event | Event + 1s 切片 | Condition.notify_all | 0 额外延迟，代码更简洁 |
| drop-in 目录缺失 | 全量 unload | log warning + 跳过 | 不破坏 CI/生产环境 |
| dispatch _lock | 隐式 snapshot | 显式契约 §2.10 + 兼容点 25 | 文档化 + 可测试 |
| atexit 重复注册 | 直接 atexit.register | weakref 跟踪 + 仅首次 | 多 dispatcher 场景 |
| sys.modules sanitize | 不处理 | `_FILENAME_SAFE_RE` | 中文/特殊字符防御 |
| facade 入口 | 散落在 _dispatch_through_v3 | 抽到 _start_hot_reload_if_enabled | SRP |

---

## 8. 总结

**Phase 17 v3.1 核心目标**（v3.1 修订版，相对 v3 增 4 项）：

1. ✅ **零回归**：Phase 16 462 tests 100% 通过
2. ✅ **静态注册保留**：BUILTIN_PLUGINS 行为不变
3. ✅ **显式 API**：hot_register / hot_unregister 完整实现（与 register 走统一校验入口）
4. ✅ **drop-in 目录**：扫描 plugins_extra/*.py 自动加载（**单文件多 plugin 完全支持**）
5. ✅ **轮询 reload**：mtime 变化自动 reload（**多 plugin 完整回滚 + 步骤 1 fail-fast 防 ghost**）
6. ✅ **安全机制**：ReloadGuard（Condition + counter）+ 启动期校验 + force 开关
7. ✅ **路径安全三层防护**（parser + watcher + facade，含 N4/N5 软链覆盖）
8. ✅ **102+ 新测试**：单元 + 集成 + 兼容性回归 + **7 个 P0 专项 case 矩阵（§4.3.7 含软链 N4/N5）**
9. ✅ **v3 修复 v2 遗留 3 P0 + 8 P1 + 8 P2** + **v3.1 修复 v3 遗留 1 P0 (P0-8) + 1 P1 (P1-9) + 1 P1 (P1-10) + 1 P1 (P1-11)**

**v3.1 关键增强**（4 项 mandatory amendments）：
- **P0-8**：`_reload_file` 步骤 1 后 fail-fast 防止 ghost plugin 泄漏
- **P1-9**：`HotReloadWatcher` 新增 `critical_failure_callback` 参数，外部对接告警系统
- **P1-10**：§4.3.7 补充软链 N4（project_root 是软链）/ N5（drop_in_dir 软链跳出）负测试
- **P1-11**：§6 风险表 23 → 28 修正，按 4+3+7+5+9=28 重新分组（A-E 段）

**核心设计原则（v3.1 强化）**：
- 不破坏 Phase 16 任何承诺（11 个旧符号 / 19 个 compat points / +1 新增 compat point 25）
- 热加载失败完整回滚（多 plugin 全部恢复 + **步骤 1 fail-fast 防 ghost 泄漏**）
- 线程安全（RLock 保护 _plugins 列表 + Condition 引用计数 + dispatch snapshot 契约）
- 校验一致性（register / hot_register 走同一 _validate_plugin_metadata 入口）
- 资源清理（sys.modules 显式 pop + sanitize + watcher.start 同步首次扫描）
- 路径安全（绝对路径 + `..` 路径 + **软链跳出（CLI 漏过 → watcher resolve 解链 → reject）** 三层 reject）
- 生产安全（--no-hot-reload 关闭动态能力 + atexit 清理 + weakref 防重复 + **critical_failure_callback 外部告警**）

**v3.1 修订版状态**：✅ **PASS**（架构师二轮审查通过）— 4 项 mandatory 修订全部完成，文档一致性验证通过。复审覆盖 §0.0（修订履历）/ §2.3（fail-fast + critical_callback）/ §4.3.7（软链 N4/N5）/ §6.1-6.5（28 项分组）/ §6.6（v3.1 新增强调）/ §8 总结。可进入实施阶段 0-6。

🤖 Generated with [MiniMax-M3](https://MiniMax)

Co-Authored-By: MiniMax <noreply@MiniMax.com>
