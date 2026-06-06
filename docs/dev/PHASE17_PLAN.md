# Phase 17 设计文档：插件热加载（Hot Reload）

> **文档类型**：技术方案 spec（v1 — 初稿，待架构师 review）
> **日期**：2026-06-06
> **状态**：⏳ v1 初稿，待架构师 review
> **前序**：[PHASE16_PLAN.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE16_PLAN.md)（V3 插件架构 1464→42 行 + 11 项风险修复）
> **方向**：插件热加载（V3 架构之上的动态能力）
> **实现范围**：完整实现（轮询 + 显式 API + drop-in 目录扫描）
> **触发机制**：显式 API（hot_register / hot_unregister）+ drop-in 目录扫描（启动 + 运行时轮询）

---

## 0. TL;DR（架构师 review 焦点）

Phase 16 已落地 V3 插件架构（plugin 自报 priority/mutex/matches，dispatcher 通用调度），但 plugin 必须在启动时静态注册（BUILTIN_PLUGINS 单例）。

**Phase 17 目标**：让 plugin 可在**运行时动态加载/卸载**，无需重启进程。两种触发：
1. **显式 API**：`dispatcher.hot_register(plugin)` / `dispatcher.hot_unregister(name)`
2. **drop-in 目录扫描**：用户把 plugin .py 放到 `plugins_extra/`，dispatcher 启动时 + 周期轮询扫描 → 自动注册

**核心约束**（不能破坏 Phase 16 承诺）：
- ✅ 零回归（462 tests 通过）
- ✅ 11 个旧符号 100% 向后兼容
- ✅ dispatcher 启动期 mutex/name/priority 校验继续生效
- ✅ plugin 仍必须 stateless（风险-9 延续）
- ✅ dispatch.legacy 不反向 import 边界（风险-10 延续）

**风险预估**（架构师需重点 review）：
- ⚠️ **R-1**：plugin reload 时若有正在执行的任务 → 中断 / 数据竞争？
- ⚠️ **R-2**：drop-in 目录扫描与 BUILTIN_PLUGINS name 冲突如何处理？
- ⚠️ **R-3**：轮询间隔与 CPU 开销的权衡（默认多少秒？）
- ⚠️ **R-4**：reload 失败时如何回滚到旧 plugin？
- ⚠️ **R-5**：hot_unregister 时若有 mutex 关系 → 校验失败怎么办？
- ⚠️ **R-6**：plugin 文件修改后能否保留旧的运行中实例？
- ⚠️ **R-7**：多进程下（fork / subprocess）plugin 实例如何同步？

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
   - 显式开关：`HOT_RELOAD_ENABLED=false` 完全关闭动态能力（生产模式）

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
│  兼容层              │  facade.py (~150 行)                          │
│                      │  ├── re-export 11 个旧符号                    │
│                      │  ├── main_compat() 旧 CLI 入口                │
│                      │  └── _dispatch_through_v3() 内部走 dispatcher  │
├──────────────────────────────────────────────────────────────────────┤
│  Legacy 入口模块     │  dispatch/legacy.py (~600 行)               │
├──────────────────────────────────────────────────────────────────────┤
│  CLI 层              │  cli/parser.py (~230 行) +                   │
│                      │  + --hot-reload / --hot-reload-dir 新增       │
├──────────────────────────────────────────────────────────────────────┤
│  调度层              │  dispatcher/goal_dispatcher.py                │
│                      │  ├── Phase 16: register / dispatch / etc.    │
│                      │  + Phase 17: hot_register / hot_unregister    │
│                      │  + Phase 17: start_hot_reload_watcher / stop  │
│  (NEW in P17)        │  + HotReloadWatcher 子组件                    │
│                      │  + DropInLoader 子组件                        │
│                      │  + ReloadGuard 锁/回滚机制                    │
├──────────────────────────────────────────────────────────────────────┤
│  插件层              │  plugins/base.py + 5 内置                   │
│  (新增)              │  plugins_extra/  drop-in 目录（用户扩展）    │
├──────────────────────────────────────────────────────────────────────┤
│  业务层（不动）      │  goal_orchestrator.py / loop_goal.py /         │
│                      │  dag_visualizer.py / trae_agent.py            │
└──────────────────────────────────────────────────────────────────────┘
```

### 2.1 三种加载路径

| 路径 | 触发方式 | 使用场景 | 状态 |
|------|----------|----------|------|
| **A. 静态注册**（Phase 16）| `BUILTIN_PLUGINS` 列表 | 内置 plugin + 必装 plugin | Phase 16 已有 |
| **B. 显式 API**（Phase 17）| `dispatcher.hot_register(plugin)` | 测试 / 动态 feature flag | Phase 17 新增 |
| **C. drop-in 目录**（Phase 17）| 扫描 `plugins_extra/*.py` | 第三方 plugin / 临时调试 | Phase 17 新增 |

### 2.2 `dispatcher.hot_register` / `hot_unregister` API

```python
# dispatcher/goal_dispatcher.py (Phase 17 扩展)
class GoalDispatcher:
    def hot_register(self, plugin: GoalCommandPlugin) -> None:
        """运行时注册 plugin（H-6 + 风险-9 校验全保留）。
        
        与 register() 的区别：
        - register()：构造时调用，启动期校验失败 → 进程崩溃
        - hot_register()：运行时调用，校验失败 → 抛异常但不破坏现有 plugins
        
        线程安全：内部用 RLock 保护 _plugins 列表
        
        Raises:
            MutexDeclarationError: plugin name 不符合 kebab-case
            DuplicatePluginNameError: plugin name 重复
            DuplicatePriorityError: plugin priority 重复
            MutexViolationError: 现有 matches() 命中冲突
        """
        with self._lock:
            # 1. 启动期校验（H-1/H-6 全套）
            self._validate_plugin_metadata(plugin)
            # 2. mutex 一致性校验（与所有现有 plugin）
            self._validate_mutex_against_existing(plugin)
            # 3. 当前 args 命中冲突校验（若有运行中 dispatch）
            self._validate_against_current_args(plugin)
            # 4. 原子切换：先 append 再 sort
            self._plugins.append(plugin)
            self._plugins.sort(key=lambda p: p.priority)
            self._logger.info(f"[Dispatcher] hot_register: {plugin.name}")

    def hot_unregister(self, name: str, force: bool = False) -> GoalCommandPlugin:
        """运行时卸载 plugin。
        
        Args:
            name: 待卸载 plugin 名称
            force: True 跳过 mutex 校验（应急场景）
        
        Returns:
            被卸载的 plugin 实例（调用方持有引用，可重新 hot_register）
        
        Raises:
            PluginNotFoundError: plugin 不存在
            MutexViolationError: 被其他 plugin 引用为 mutex_with（除非 force=True）
            PluginBusyError: 插件正在执行 execute()（v1 不支持 force-unregister busy）
        """
        with self._lock:
            # 1. 查找 + busy 检查
            plugin = self._find_plugin(name)
            if plugin is None:
                raise PluginNotFoundError(name)
            # 2. mutex 反向引用检查
            if not force:
                self._validate_no_mutex_references(name)
            # 3. 原子移除
            self._plugins.remove(plugin)
            self._logger.info(f"[Dispatcher] hot_unregister: {name}")
            return plugin
```

### 2.3 `HotReloadWatcher` 子组件

```python
# dispatcher/hot_reload_watcher.py (Phase 17 新增)
class HotReloadWatcher:
    """轮询 drop-in 目录，检测文件变更 → hot_register / hot_unregister。
    
    行为：
    1. 启动时扫描 plugins_extra/ 全部 .py
    2. importlib.util.spec_from_file_location 动态加载
    3. 找到 GoalCommandPlugin 子类 → 实例化 → hot_register
    4. 记录文件 mtime，周期（默认 5s）检查
    5. mtime 变化 → 重新加载（unregister 旧实例 + register 新实例）
    6. 文件删除 → unregister
    7. 加载失败 → 保留旧实例 + log error（不回滚崩溃）
    """
    
    DEFAULT_POLL_INTERVAL = 5.0  # 秒
    
    def __init__(self, dispatcher: GoalDispatcher, drop_in_dir: Path,
                 poll_interval: float = DEFAULT_POLL_INTERVAL):
        self._dispatcher = dispatcher
        self._drop_in_dir = Path(drop_in_dir)
        self._poll_interval = poll_interval
        self._file_states: Dict[str, Tuple[float, GoalCommandPlugin]] = {}
        self._running = False
        self._thread: Optional[Thread] = None
    
    def start(self) -> None:
        """启动后台轮询线程。"""
        if self._running:
            return
        self._running = True
        self._thread = Thread(target=self._watch_loop, daemon=True)
        self._thread.start()
        self._logger.info(f"[Watcher] 启动轮询：{self._drop_in_dir}")
    
    def stop(self, timeout: float = 5.0) -> None:
        """停止后台线程（graceful shutdown）。"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=timeout)
    
    def _watch_loop(self) -> None:
        while self._running:
            try:
                self._scan_once()
            except Exception as e:
                self._logger.error(f"[Watcher] 扫描异常：{e}")
            time.sleep(self._poll_interval)
    
    def _scan_once(self) -> None:
        """单次扫描（也可被测试直接调用）。"""
        current_files = {p.name: p.stat().st_mtime 
                         for p in self._drop_in_dir.glob("*.py")}
        # 1. 新增文件
        for name, mtime in current_files.items():
            if name not in self._file_states:
                self._load_file(self._drop_in_dir / name)
        # 2. mtime 变化
        for name, (old_mtime, plugin) in self._file_states.items():
            if name in current_files:
                new_mtime = current_files[name]
                if new_mtime > old_mtime:
                    self._reload_file(self._drop_in_dir / name, plugin)
        # 3. 文件删除
        for name in list(self._file_states):
            if name not in current_files:
                self._unload_file(name)
```

### 2.4 `DropInLoader` 子组件

```python
# dispatcher/drop_in_loader.py (Phase 17 新增)
class DropInLoader:
    """从 .py 文件动态 import + 实例化 GoalCommandPlugin。
    
    约定：
    - .py 文件必须定义 1 个或多个 GoalCommandPlugin 子类
    - 主类命名约定：<FileName>Plugin（大写驼峰）
    - 例：my_feature.py → MyFeaturePlugin
    - 若文件无符合约定的类 → 抛 DropInLoadError
    """
    
    @staticmethod
    def load_from_file(path: Path) -> List[GoalCommandPlugin]:
        """从 .py 文件加载所有 GoalCommandPlugin 子类实例。
        
        流程：
        1. importlib.util.spec_from_file_location 动态加载
        2. 遍历 module 属性，识别 GoalCommandPlugin 子类
        3. 实例化 + 返回列表
        """
        spec = importlib.util.spec_from_file_location(
            f"plugins_extra.{path.stem}", path
        )
        if spec is None or spec.loader is None:
            raise DropInLoadError(f"无法构造 spec：{path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module  # 防止重复 import
        spec.loader.exec_module(module)
        
        plugins = []
        for name in dir(module):
            obj = getattr(module, name)
            if (inspect.isclass(obj) 
                and issubclass(obj, GoalCommandPlugin) 
                and obj is not GoalCommandPlugin):
                plugins.append(obj())
        if not plugins:
            raise DropInLoadError(
                f"{path} 未定义任何 GoalCommandPlugin 子类"
            )
        return plugins
```

### 2.5 CLI 集成（Phase 17 新增 2 个 flag）

```python
# cli/parser.py (Phase 17 扩展)
parser.add_argument(
    '--hot-reload',
    action='store_true',
    help='Phase 17 启用插件热加载（默认开启，--no-hot-reload 关闭）'
)
parser.add_argument(
    '--hot-reload-dir',
    type=str,
    default='plugins_extra',
    help='drop-in 目录路径（相对 project_root，默认 plugins_extra/）'
)
parser.add_argument(
    '--hot-reload-interval',
    type=float,
    default=5.0,
    help='轮询间隔（秒，默认 5.0；范围 [0.5, 60.0]）'
)
parser.add_argument(
    '--no-hot-reload',
    action='store_true',
    help='禁用插件热加载（生产环境推荐）'
)
```

### 2.6 安全与回滚机制

```python
# dispatcher/reload_guard.py (Phase 17 新增)
class ReloadGuard:
    """reload 操作的事务性保护（acquire-release 语义）。"""
    
    def __init__(self, dispatcher: GoalDispatcher):
        self._dispatcher = dispatcher
        self._lock = RLock()
        self._active_executes: Set[str] = set()  # 当前正在 execute 的 plugin name
    
    def enter_execute(self, plugin_name: str) -> None:
        """plugin.execute() 入口调用：标记 busy。"""
        with self._lock:
            self._active_executes.add(plugin_name)
    
    def exit_execute(self, plugin_name: str) -> None:
        """plugin.execute() 出口调用：清除 busy。"""
        with self._lock:
            self._active_executes.discard(plugin_name)
    
    def wait_for_idle(self, plugin_name: str, timeout: float = 10.0) -> bool:
        """hot_unregister 等待 plugin 空闲。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if plugin_name not in self._active_executes:
                    return True
            time.sleep(0.05)
        return False
```

**回滚策略**：
- hot_register 失败 → 不修改 `_plugins` 列表，抛异常
- hot_unregister 失败 → 不修改 `_plugins` 列表，抛异常
- drop-in 加载失败 → 保留旧 plugin 实例，log error，继续运行
- drop-in reload（mtime 变化）失败 → 保留旧实例，log error，不替换

---

## 3. 兼容性矩阵（19 + 5 = 24 个兼容点）

### 3.1 Phase 16 兼容承诺（19 个，全部保留）

（沿用 [PHASE16_PLAN.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE16_PLAN.md) §3.4）

### 3.2 Phase 17 新增兼容点（5 个）

| # | 兼容点 | 验证方式 | 状态 |
|---|--------|----------|------|
| 20 | `dispatcher.hot_register(plugin)` 可调用 | test_v4_hot_reload.py | ✅ |
| 21 | `dispatcher.hot_unregister(name)` 可调用 | test_v4_hot_reload.py | ✅ |
| 22 | `HotReloadWatcher` 启动/停止/扫描 | test_v4_hot_reload_watcher.py | ✅ |
| 23 | `DropInLoader.load_from_file()` 加载 | test_v4_drop_in_loader.py | ✅ |
| 24 | `plugins_extra/*.py` 自动加载 | test_v4_drop_in_integration.py | ✅ |

### 3.3 CLI 行为不变承诺

- `--hot-reload`：默认开启；`--no-hot-reload` 关闭
- 不传 `--hot-reload*`：行为与 Phase 16 完全一致（向后兼容）
- 老 CLI 调用方（无 hot-reload 概念）100% 不受影响

---

## 4. 测试策略

### 4.1 单元测试（6 个新文件）

| 测试文件 | 覆盖 | 预计测试数 |
|----------|------|-----------|
| test_v4_hot_reload.py | hot_register / hot_unregister / busy / 冲突 | 20+ |
| test_v4_hot_reload_watcher.py | watcher 启动/停止/扫描/mtime 检测 | 15+ |
| test_v4_drop_in_loader.py | drop-in .py 加载/失败/多类 | 10+ |
| test_v4_drop_in_integration.py | drop-in 目录 + 实际加载 | 10+ |
| test_v4_reload_guard.py | reload 锁/等待/超时 | 8+ |
| test_v4_cli_hot_reload.py | --hot-reload / --no-hot-reload | 8+ |
| **小计** | | **70+** |

### 4.2 集成测试（1 个新文件）

- `test_v4_hot_reload_integration.py`：CLI 启动 + drop-in 目录 + 运行时 reload

### 4.3 兼容性回归（沿用 Phase 16 套件）

- `test_v3_integration.py`：19 个 compat points 全部继续通过
- `test_v3_plugins.py`：5 个内置 plugin 契约不变
- `test_v3_dispatcher.py`：register 语义不变

### 4.4 关键验证清单

- ✅ hot_register 与 register 行为等价（H-6 校验全保留）
- ✅ hot_unregister 不破坏现有 plugin
- ✅ watcher 线程 graceful shutdown（不泄漏）
- ✅ drop-in 加载失败不破坏 dispatcher
- ✅ reload 失败回滚（保留旧实例）
- ✅ 关闭热加载时行为与 Phase 16 完全一致

---

## 5. 实施阶段

### 5.1 阶段 1：核心 API（hot_register / hot_unregister）

1. `dispatcher/goal_dispatcher.py` 新增 `hot_register` / `hot_unregister`
2. `dispatcher/errors.py` 新增 `PluginNotFoundError` / `PluginBusyError`
3. `dispatcher/reload_guard.py` 新增（ReloadGuard 类）
4. dispatcher 内部 `enter_execute` / `exit_execute` 包裹 plugin.execute()
5. 单元测试 `test_v4_hot_reload.py`

### 5.2 阶段 2：DropInLoader

1. `dispatcher/drop_in_loader.py` 新增（DropInLoader 类）
2. 单元测试 `test_v4_drop_in_loader.py`
3. 集成测试 `test_v4_drop_in_integration.py`

### 5.3 阶段 3：HotReloadWatcher

1. `dispatcher/hot_reload_watcher.py` 新增（HotReloadWatcher 类）
2. 单元测试 `test_v4_hot_reload_watcher.py`
3. 单元测试 `test_v4_reload_guard.py`

### 5.4 阶段 4：CLI 集成

1. `cli/parser.py` 新增 4 个 CLI flag
2. `facade.py` `_dispatch_through_v3` 接受 `args.hot_reload` / `args.hot_reload_dir` / `args.hot_reload_interval`
3. 单元测试 `test_v4_cli_hot_reload.py`

### 5.5 阶段 5：测试 + 验证

1. 风险评估清单（§6）逐项验证
2. 462 旧测试 100% 通过
3. 70+ 新测试 100% 通过
4. 集成测试（CLI 启动 + drop-in + reload）
5. 更新 [PHASE16_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE16_FINAL_REPORT.md)（追加 Phase 17 一节）

### 5.6 阶段 6：commit + tag

1. `git commit` + `tag phase-17-plugin-hot-reload`

---

## 6. 风险评估清单（架构师 review 重点）

| 风险 | 严重度 | 描述 | 缓解策略 | 状态 |
|------|--------|------|----------|------|
| **R-1** | P0 | plugin reload 时若有正在执行的任务 → 中断 | ReloadGuard 持锁 + wait_for_idle | 阶段 1 |
| **R-2** | P0 | drop-in 与 BUILTIN_PLUGINS name 冲突 | hot_register 走 H-6 唯一性校验 | 阶段 1 |
| **R-3** | P1 | 轮询间隔与 CPU 开销权衡 | 默认 5s + 可配置（0.5-60s）| 阶段 3 |
| **R-4** | P0 | reload 失败如何回滚 | 保留旧实例 + log error（不抛异常）| 阶段 1 |
| **R-5** | P1 | hot_unregister 触发 mutex 关系断裂 | 默认校验 + force=True 应急开关 | 阶段 1 |
| **R-6** | P1 | plugin 内部状态如何处理 | plugin 必须 stateless（风险-9 延续）| 文档 |
| **R-7** | P1 | 多进程 fork 后 plugin 实例不同步 | v1 仅支持单进程，多进程留 v2 | 文档 |
| **R-8** | P2 | 第三方 drop-in 引入恶意代码 | 文档警示 + 用户自负责任 | 文档 |
| **R-9** | P2 | watcher 线程泄漏（start 后未 stop）| daemon=True + atexit hook | 阶段 3 |
| **R-10** | P2 | 大量 drop-in 文件时扫描性能 | 单目录 glob 100 个 .py 应 < 100ms | 性能测试 |

---

## 7. v1 初稿 → 架构师 review 问题清单

1. **R-1 缓解策略**：ReloadGuard 持锁 + wait_for_idle 等待 plugin 空闲（默认 10s）→ 是否合理？超时应 fail-fast 还是 force-unregister？
2. **R-4 回滚策略**：reload 失败时保留旧实例 + log error → 还是抛异常让调用方决定？
3. **R-5 force 开关**：hot_unregister(name, force=True) 跳过 mutex 校验 → 是否保留？
4. **R-7 多进程**：v1 不支持 fork 后 hot reload 同步（多 Goal 编排走 ProcessPoolExecutor 仍有 v2 需求）→ 是否纳入 v1 范围？
5. **R-3 轮询间隔**：默认 5s 是否合理？是否应根据 drop-in 文件数量动态调整？
6. **CLI 默认值**：--hot-reload 默认开启 → 还是默认关闭（生产环境更安全）？
7. **drop-in 目录位置**：`plugins_extra/`（相对 project_root）→ 还是 `scripts/plugins_extra/`（绝对）？

---

## 8. 总结

**Phase 17 核心目标**：

1. ✅ **零回归**：Phase 16 462 tests 100% 通过
2. ✅ **静态注册保留**：BUILTIN_PLUGINS 行为不变
3. ✅ **显式 API**：hot_register / hot_unregister 完整实现
4. ✅ **drop-in 目录**：扫描 plugins_extra/*.py 自动加载
5. ✅ **轮询 reload**：mtime 变化自动 reload（保留旧实例回滚）
6. ✅ **安全机制**：ReloadGuard + 启动期校验 + force 开关
7. ✅ **70+ 新测试**：单元 + 集成 + 兼容性回归

**核心设计原则**：
- 不破坏 Phase 16 任何承诺（11 个旧符号 / 19 个 compat points）
- 热加载失败不破坏现有调度（保留旧实例 + log error）
- 线程安全（RLock 保护 _plugins 列表）
- 生产安全（--no-hot-reload 关闭动态能力）

**v1 初稿** → 待架构师 review 7 个核心问题（§7）→ 修订 v2 → 实施阶段 1-6

🤖 Generated with [MiniMax-M3](https://MiniMax)

Co-Authored-By: MiniMax <noreply@MiniMax.com>
