"""HotReloadWatcher 子组件（Phase 17 §2.3 v3.1）。

职责：轮询 drop-in 目录，检测文件变更 → hot_register / hot_unregister。

v3.1 关键变更（相对 v3 新增）：
- _reload_file 步骤 1 后 fail-fast 防 ghost plugin 泄漏（P0-8）
- _rollback_old_plugins 触发 critical_failure_callback 外部告警（P1-9）
- Callable 类型加入 import（P1-9 依赖）

v3 关键变更：
- _file_states: Dict[str, Tuple[float, List[GoalCommandPlugin]]] 支持单文件多 plugin（P0-5）
- 强制 project_root 参数 + _resolve_drop_in_dir 路径校验（P0-7）
- _scan_once 目录缺失 → log warning + 跳过（P1-1）
- _reload_file 严格多 plugin 回滚路径（P0-6）
- 启动期同步首次扫描 + _initial_scan_done Event（沿用 v2 P0-4）

线程安全：watcher 自身单线程轮询；与 dispatcher 交互通过 RLock 串行化。
"""
import logging
import re
import sys
import threading
import time
from pathlib import Path
from threading import Event, Thread
from typing import Callable, Dict, List, Optional, Tuple

from plugins.base import GoalCommandPlugin
from dispatcher.drop_in_loader import DropInLoader
from dispatcher.errors import DropInPathError


# v3 修订：file_stem sanitize（处理中文/特殊字符）
# 注解：只允许 [a-zA-Z0-9_.]，其余字符 → "_"
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

    设计要点：
    - 单线程 daemon 线程：daemon=True + atexit 清理
    - mtime 精度：使用 st_mtime_ns（纳秒）避免 mtime 精度冲突
    - 文件名过滤：仅扫描 *.py 且不以下划线开头（_prefix.py 视为私有）
    """

    # 轮询间隔常量（用于钳制）
    DEFAULT_POLL_INTERVAL = 5.0
    MIN_POLL_INTERVAL = 0.5
    MAX_POLL_INTERVAL = 60.0

    def __init__(
        self,
        dispatcher: "object",  # 避免循环引用：GoalDispatcher
        drop_in_dir: Path,
        project_root: Path,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        # v3.1 P1-9 新增：critical failure 外部回调（用于对接 Sentry / 钉钉 / PagerDuty）
        # 签名：callback(file_name: str, failed_plugin_names: List[str]) -> None
        critical_failure_callback: Optional[Callable[[str, List[str]], None]] = None,
    ):
        """初始化 HotReloadWatcher。

        Args:
            dispatcher: 已构造的 GoalDispatcher 实例
            drop_in_dir: drop-in 目录路径（相对或绝对；解析时强制在 project_root 内）
            project_root: 项目根目录（用于 drop-in 路径安全校验）
            poll_interval: 轮询间隔（秒，钳制到 [0.5, 60.0]）
            critical_failure_callback: 严重失败时触发的外部回调
                                       （rollback 失败、unregister 严重失败时调用）

        Raises:
            DropInPathError: drop-in 路径不安全（绝对路径 / 跳出 project_root /
                            不是目录且 parent 不存在）
        """
        # 钳制轮询间隔（防御性：用户传 0.1 或 1000 也不出问题）
        self._poll_interval: float = max(
            self.MIN_POLL_INTERVAL,
            min(poll_interval, self.MAX_POLL_INTERVAL),
        )
        self._dispatcher = dispatcher
        # v3：路径安全解析
        self._project_root: Path = Path(project_root).resolve()
        # 内部 logger 需在 _resolve_drop_in_dir 之前初始化
        self._logger: logging.Logger = logging.getLogger("hot_reload_watcher")
        self._drop_in_dir: Path = self._resolve_drop_in_dir(Path(drop_in_dir))
        # v3 修订：单文件多 plugin 完全支持（P0-5）
        # key = 文件名（含 .py），value = (mtime_ns, [plugin instances...])
        self._file_states: Dict[str, Tuple[int, List[GoalCommandPlugin]]] = {}
        self._running: bool = False
        self._thread: Optional[Thread] = None
        # v3 P0-4：启动期同步首次扫描完成事件
        self._initial_scan_done: Event = Event()
        # v3.1 P1-9：critical failure 回调
        self._critical_failure_callback: Optional[
            Callable[[str, List[str]], None]
        ] = critical_failure_callback

    def _resolve_drop_in_dir(self, raw: Path) -> Path:
        """v3 P0-7 第二层：watcher 内部路径安全校验。

        规则：
        1. raw 必须为相对路径（绝对路径 → reject）
        2. resolve() 后必须 is_relative_to(project_root)（软链跳出 → reject）
        3. 不存在但 parent 存在 → 创建
        4. 不存在且 parent 也不存在 → DropInPathError
        5. 存在但不是目录 → DropInPathError

        Args:
            raw: 原始 drop-in 路径（可能是绝对或相对）

        Returns:
            解析后的绝对 drop-in 目录路径

        Raises:
            DropInPathError: 路径不安全

        线程安全：实例方法，仅 __init__ 阶段调用一次
        """
        # === 1. 绝对路径拒绝 ===
        if raw.is_absolute():
            raise DropInPathError(
                f"drop-in 目录必须为相对路径，绝对路径被拒绝：{raw}"
            )
        # === 2. resolve() + project_root 内校验 ===
        # (project_root / raw).resolve() 自动处理 '..' 和软链
        abs_path: Path = (self._project_root / raw).resolve()
        if not abs_path.is_relative_to(self._project_root):
            raise DropInPathError(
                f"drop-in 目录必须在 project_root 内："
                f"{abs_path} ∉ {self._project_root}"
            )
        # === 3. 目录不存在时按需创建 ===
        if not abs_path.exists():
            try:
                abs_path.mkdir(parents=True, exist_ok=True)
                self._logger.info(
                    f"[Watcher] 创建 drop-in 目录：{abs_path}"
                )
            except OSError as e:
                raise DropInPathError(
                    f"无法创建 drop-in 目录：{abs_path} ({e})"
                ) from e
        # === 4. 必须是目录（不能是文件）===
        if not abs_path.is_dir():
            raise DropInPathError(
                f"drop-in 路径不是目录：{abs_path}"
            )
        return abs_path

    def start(self) -> None:
        """v3 P0-4：先同步执行首次扫描，再启动后台线程。

        行为：
        1. 检查 _running 状态：已运行 → no-op（避免重复启动）
        2. 同步执行 _scan_once()（首次扫描结果在 start() 返回前可见）
        3. set _initial_scan_done（通知 wait_initial_scan() 的等待者）
        4. 启动 daemon 线程执行 _watch_loop
        5. 异常隔离：首次扫描抛异常 → 记录后继续（不阻断线程启动）
        """
        if self._running:
            return
        try:
            self._scan_once()
        except Exception as e:
            # 异常隔离：不阻断线程启动（生产友好）
            self._logger.error(f"[Watcher] 启动扫描异常：{e}")
        # 无论首次扫描是否成功，都标记 _initial_scan_done 已完成
        # 理由：start() 调用方不应被首次扫描异常永久阻塞
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
        """停止后台轮询线程。

        Args:
            timeout: 等待线程结束的最长时间（秒）

        行为：
        1. 设置 _running = False（_watch_loop 下次循环检测到后退出）
        2. join 线程（带超时）
        3. 超时未结束 → log warning（daemon 线程不会强制 kill，进程退出时回收）
        """
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                self._logger.warning(
                    f"[Watcher] 线程未在 {timeout}s 内停止"
                )

    def wait_initial_scan(
        self, timeout: Optional[float] = None
    ) -> bool:
        """等待首次扫描完成（start() 后调用，确保 dispatcher 已加载所有 drop-in）。

        Args:
            timeout: 最大等待秒数；None = 无限等待

        Returns:
            True = 首次扫描已完成；False = 超时仍未完成
        """
        return self._initial_scan_done.wait(timeout=timeout)

    def _watch_loop(self) -> None:
        """后台轮询循环（仅 watcher 自身线程调用）。

        行为：
        1. 循环执行 _scan_once() + sleep(_poll_interval)
        2. _scan_once 异常被隔离（log 后继续）
        3. _running = False 时优雅退出
        """
        while self._running:
            try:
                self._scan_once()
            except Exception as e:
                self._logger.error(f"[Watcher] 扫描异常：{e}")
            if not self._running:
                break
            time.sleep(self._poll_interval)

    def _scan_once(self) -> None:
        """执行一次完整扫描（新增/变更/删除检测）。

        v3 P1-1 修订：目录缺失 → log warning + 跳过（不再误删已加载 plugin）。

        流程：
        1. 目录存在性检查
        2. 列出所有 *.py 文件（排除 _prefix 私有文件）
        3. 新增检测：当前有但 _file_states 没有 → _load_file
        4. 变更检测：mtime_ns 变化 → _reload_file
        5. 删除检测：_file_states 有但当前没有 → _unload_file
        """
        # === 1. 目录缺失 graceful 跳过（P1-1）===
        if not self._drop_in_dir.exists():
            self._logger.warning(
                f"[Watcher] drop-in 目录不存在：{self._drop_in_dir}（跳过本次扫描）"
            )
            return
        # === 2. 列出所有 .py 文件（排除 _ 开头私有文件）===
        current_files: Dict[str, int] = {
            p.name: p.stat().st_mtime_ns
            for p in self._drop_in_dir.glob("*.py")
            if not p.name.startswith("_")
        }
        # === 3. 新增文件检测 ===
        for name, _mtime in current_files.items():
            if name not in self._file_states:
                self._load_file(self._drop_in_dir / name)
        # === 4. mtime 变化（reload）===
        # 使用 list() 防止 _reload_file 修改 _file_states 时的 RuntimeError
        for name, (old_mtime, old_plugins) in list(self._file_states.items()):
            if name in current_files:
                new_mtime: int = current_files[name]
                if new_mtime > old_mtime:
                    self._reload_file(
                        self._drop_in_dir / name, old_plugins
                    )
        # === 5. 文件删除检测 ===
        for name in list(self._file_states):
            if name not in current_files:
                self._unload_file(name)

    def _load_file(self, path: Path) -> None:
        """加载新文件到 dispatcher（v3 修订：单文件多 plugin 完全支持）。

        行为：
        1. DropInLoader.load_from_file 加载所有 plugin
        2. 逐个 hot_register；部分失败时仅记录成功的
        3. 至少 1 个成功才更新 _file_states（避免记录"加载了 0 个"）
        """
        try:
            plugins: List[GoalCommandPlugin] = DropInLoader.load_from_file(path)
        except Exception as e:
            self._logger.error(
                f"[Watcher] 加载 {path.name} 失败：{e}"
            )
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
        # 至少 1 个 plugin 成功注册才记录（避免空文件状态污染）
        if loaded:
            mtime: int = path.stat().st_mtime_ns
            self._file_states[path.name] = (mtime, loaded)
            self._logger.info(
                f"[Watcher] 加载 {path.name} 成功：{len(loaded)} 个 plugin"
            )

    def _reload_file(
        self, path: Path, old_plugins: List[GoalCommandPlugin]
    ) -> None:
        """v3.1 修订：多 plugin reload 完整回滚（P0-6） + 步骤 1 fail-fast（P0-8）。

        策略：
        1. unregister 全部旧 plugin（force=True 跳过 busy 等待）
        1.5 v3.1 P0-8：步骤 1 部分失败 → fail-fast 立即回滚 + return
            （防 ghost plugin 泄漏）
        2. 加载新实例
        3. register 新 plugin
        4. 任何步骤失败 → 逐个 hot_register 旧 plugin（严格回滚）
        5. 回滚也失败 → critical log + 外部 critical_failure_callback
        """
        # === 步骤 1：unregister 全部旧 plugin（force=True）===
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
        # === 步骤 1.5：v3.1 P0-8 fail-fast 防 ghost plugin ===
        if unregister_failures:
            self._logger.error(
                f"[Watcher] reload {path.name} 步骤 1 部分失败："
                f"{len(unregister_failures)} 个 plugin 拒绝 unregister，"
                f"拒绝继续 register（防止 ghost plugin 泄漏），开始回滚"
            )
            # 立即回滚已 unregister 的旧 plugin（恢复 dispatcher 状态）
            self._rollback_old_plugins(unregistered, path.name)
            # 保留 _file_states 中的旧 plugin 引用（不更新 mtime）
            # ——本次 reload 失败，下次 mtime 变化时再尝试 reload
            return
        # === 步骤 2：加载新实例 ===
        try:
            new_plugins: List[GoalCommandPlugin] = DropInLoader.load_from_file(
                path
            )
        except Exception as e:
            self._logger.error(
                f"[Watcher] reload {path.name} 加载新实例失败：{e}，"
                f"开始回滚"
            )
            self._rollback_old_plugins(unregistered, path.name)
            return
        # === 步骤 3：register 新 plugin ===
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
        # === 步骤 4：至少 1 个新 plugin 成功 → 更新 file_states ===
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
        self, old_plugins: List[GoalCommandPlugin], file_name: str
    ) -> None:
        """v3.1 新增：严格回滚 + 外部告警回调（P1-9）。

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
        # === v3.1 P1-9：critical log + 外部告警回调 ===
        if rollback_failures:
            failed_names: List[str] = [n for n, _ in rollback_failures]
            self._logger.critical(
                f"[Watcher] {file_name} 回滚失败，{len(rollback_failures)} "
                f"个 plugin 永久丢失：{failed_names}"
            )
            # 触发外部 critical failure 回调（用于对接 Sentry / 钉钉 / PagerDuty）
            # 回调在 try/except 内执行，避免回调自身异常影响主流程
            if self._critical_failure_callback is not None:
                try:
                    self._critical_failure_callback(file_name, failed_names)
                except Exception as cb_err:
                    self._logger.error(
                        f"[Watcher] critical_failure_callback 自身异常：{cb_err}"
                    )

    def _unload_file(self, name: str) -> None:
        """v3 修订：多 plugin 全部 unload + sys.modules 清理。

        行为：
        1. 从 _file_states 取出所有 plugin 实例
        2. 逐个 hot_unregister（force=True）
        3. sys.modules 清理 plugins_extra.<sanitized_stem> 引用
        """
        if name not in self._file_states:
            return
        _, plugins = self._file_states.pop(name)
        # 1. unregister 全部 plugin（force=True 跳过 mutex 校验）
        for plugin in plugins:
            try:
                self._dispatcher.hot_unregister(plugin.name, force=True)
            except Exception as e:
                self._logger.error(
                    f"[Watcher] 卸载 {plugin.name!r} 失败：{e}"
                )
        # 2. sys.modules 清理（P0-2 保留 + v3 P2-1 sanitize）
        stem: str = Path(name).stem
        safe_stem: str = _FILENAME_SAFE_RE.sub("_", stem)
        module_key: str = f"plugins_extra.{safe_stem}"
        if module_key in sys.modules:
            del sys.modules[module_key]
            self._logger.debug(
                f"[Watcher] 清理 sys.modules[{module_key}]"
            )
        self._logger.info(
            f"[Watcher] 卸载 {name} 成功：{len(plugins)} 个 plugin"
        )


__all__ = ["HotReloadWatcher"]
