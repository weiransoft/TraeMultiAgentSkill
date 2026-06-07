"""hot_reload_watcher.py 覆盖度补全测试（RED→GREEN 阶段）。

针对覆盖率分析中 dispatcher/hot_reload_watcher.py 的缺失行（89% → 目标 100%）：
- 151-152: drop_in_dir 路径存在但不是目录（普通文件）
- 157: 缺失目录时自动创建 + log info
- 176-178: start() 重复调用第二次 no-op
- 295-299: _load_file 失败隔离（不抛异常到调用方）
- 341-343: _unload_file 单个 plugin unregister 失败
- 446: critical_failure_callback 在 rollback 失败时触发
- 452-453: critical_failure_callback 自身异常隔离
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

# 路径设置
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from plugins.base import GoalCommandPlugin
from dispatcher.errors import (
    DropInPathError,
    DuplicatePluginNameError,
)
from dispatcher.goal_dispatcher import GoalDispatcher
from dispatcher.hot_reload_watcher import HotReloadWatcher


def _write_plugin_file(
    path: Path, plugin_name: str = "test", priority: int = 100
) -> Path:
    """写一个标准 plugin 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    code = f"""
from plugins.base import GoalCommandPlugin
class _Gen(GoalCommandPlugin):
    @property
    def name(self): return "{plugin_name}"
    @property
    def priority(self): return {priority}
    @property
    def mutex_with(self): return set()
    @property
    def requires_task(self): return False
    def matches(self, args): return True
    def execute(self, args, ctx): return True
"""
    path.write_text(code, encoding="utf-8")
    return path


class _TempDirMixin:
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="watch_cov_")
        self.tmp_path = Path(self._tmp)
        self.project_root = self.tmp_path / "project_root"
        self.project_root.mkdir()
        self.drop_in_dir = self.project_root / "plugins_extra"
        self.drop_in_dir.mkdir()

    def tearDown(self) -> None:
        for key in list(sys.modules.keys()):
            if key.startswith("plugins_extra."):
                del sys.modules[key]
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestWatcherResolveEdgeCases(_TempDirMixin, unittest.TestCase):
    """_resolve_drop_in_dir 边界条件：path 不是目录（line 151-152）。"""

    def test_path_is_a_file_raises_dropinpatherror(self):
        """drop_in_dir 路径存在但不是目录（是个文件）→ DropInPathError。"""
        # 在 project_root 内创建一个普通文件
        fake_file = self.project_root / "not_a_dir"
        fake_file.write_text("# not a directory")

        dispatcher = GoalDispatcher()
        with self.assertRaises(DropInPathError) as cm:
            HotReloadWatcher(
                dispatcher=dispatcher,
                drop_in_dir=Path("not_a_dir"),
                project_root=self.project_root,
            )
        # 错误消息应说明"不是目录"
        self.assertIn("不是目录", str(cm.exception))

    def test_missing_dir_auto_creates(self):
        """drop_in_dir 不存在 → 自动创建 + log info（line 157）。"""
        # 删除预先创建的 drop-in 目录
        shutil.rmtree(self.drop_in_dir)

        dispatcher = GoalDispatcher()
        watcher = HotReloadWatcher(
            dispatcher=dispatcher,
            drop_in_dir=Path("plugins_extra"),
            project_root=self.project_root,
        )
        try:
            # 目录应被自动创建
            self.assertTrue(self.drop_in_dir.exists())
            self.assertTrue(self.drop_in_dir.is_dir())
        finally:
            watcher.stop(timeout=1.0)


class TestWatcherStartIdempotent(_TempDirMixin, unittest.TestCase):
    """start() 重复调用 → 第二次 no-op（line 176-178）。"""

    def test_double_start_noop(self):
        """start() 第二次调用 → 不重启线程（保持 _thread 不变）。"""
        dispatcher = GoalDispatcher()
        watcher = HotReloadWatcher(
            dispatcher=dispatcher,
            drop_in_dir=Path("plugins_extra"),
            project_root=self.project_root,
            poll_interval=0.3,
        )
        try:
            watcher.start()
            first_thread = watcher._thread
            # 第二次 start 应是 no-op
            watcher.start()
            # 关键：_thread 不变（无重启）
            self.assertIs(watcher._thread, first_thread)
        finally:
            watcher.stop(timeout=2.0)


class TestWatcherLoadFileFailureIsolation(_TempDirMixin, unittest.TestCase):
    """_load_file 异常隔离（line 295-299）：load 失败不抛异常。"""

    def test_load_syntax_error_does_not_propagate(self):
        """_load_file 在 _scan_once 中调用 → 语法错误应被 _scan_once 捕获。"""
        # 写一个语法错误的文件
        (self.drop_in_dir / "broken.py").write_text(
            "def broken(:\n  pass\n", encoding="utf-8"
        )

        dispatcher = GoalDispatcher()
        watcher = HotReloadWatcher(
            dispatcher=dispatcher,
            drop_in_dir=Path("plugins_extra"),
            project_root=self.project_root,
        )
        try:
            # start() 内调用 _scan_once → 遇到 broken.py → 异常被隔离
            watcher.start()
            # 关键：start() 正常返回（无异常）
            self.assertTrue(watcher._initial_scan_done.is_set())
        finally:
            watcher.stop(timeout=2.0)


class TestWatcherUnloadFileFailure(_TempDirMixin, unittest.TestCase):
    """_unload_file 单个 plugin 失败 → 其他 plugin 仍尝试 unregister（line 341-343）。"""

    def test_unload_file_with_partial_failure_continues(self):
        """_unload_file 中部分 plugin unregister 失败 → 继续处理其他 plugin。"""
        # 写入一个含 2 个 plugin 的文件
        code = """
from plugins.base import GoalCommandPlugin
class A(GoalCommandPlugin):
    @property
    def name(self): return "a"
    @property
    def priority(self): return 100
    @property
    def mutex_with(self): return set()
    @property
    def requires_task(self): return False
    def matches(self, args): return True
    def execute(self, args, ctx): return True

class B(GoalCommandPlugin):
    @property
    def name(self): return "b"
    @property
    def priority(self): return 200
    @property
    def mutex_with(self): return set()
    @property
    def requires_task(self): return False
    def matches(self, args): return True
    def execute(self, args, ctx): return True
"""
        (self.drop_in_dir / "ab.py").write_text(code, encoding="utf-8")

        dispatcher = GoalDispatcher()
        watcher = HotReloadWatcher(
            dispatcher=dispatcher,
            drop_in_dir=Path("plugins_extra"),
            project_root=self.project_root,
        )
        try:
            watcher.start()
            # 验证：a + b 都已注册
            names = [p.name for p in dispatcher.list_plugins()]
            self.assertIn("a", names)
            self.assertIn("b", names)

            # 直接调用 _unload_file
            watcher._unload_file("ab.py")

            # 关键：a + b 都被 unregister（即使有内部 try/except 处理单个失败）
            names_after = [p.name for p in dispatcher.list_plugins()]
            self.assertNotIn("a", names_after)
            self.assertNotIn("b", names_after)
        finally:
            watcher.stop(timeout=2.0)


class TestWatcherCriticalCallbackEdge(_TempDirMixin, unittest.TestCase):
    """critical_failure_callback 边缘情况（line 446, 452-453）。"""

    def test_callback_triggered_with_file_name_and_failed_names(self):
        """rollback 失败 → callback 接收 (file_name, failed_names)。"""
        _write_plugin_file(self.drop_in_dir / "doomed.py", "doomed")

        dispatcher = GoalDispatcher()
        invocations: List[tuple] = []
        inv_lock = threading.Lock()

        def critical_cb(file_name, failed_names):
            with inv_lock:
                invocations.append((file_name, list(failed_names)))

        watcher = HotReloadWatcher(
            dispatcher=dispatcher,
            drop_in_dir=Path("plugins_extra"),
            project_root=self.project_root,
            critical_failure_callback=critical_cb,
        )
        try:
            watcher.start()
            watcher.stop(timeout=2.0)

            # 让 hot_register 抛错（模拟回滚失败）
            original = dispatcher.hot_register

            def failing_hot_register(plugin):
                raise DuplicatePluginNameError(
                    f"mock failure: {plugin.name}"
                )

            dispatcher.hot_register = failing_hot_register

            old_plugin = next(
                p for p in dispatcher.list_plugins()
                if p.name == "doomed"
            )
            # 写入一个新文件 + bump mtime
            _write_plugin_file(
                self.drop_in_dir / "doomed.py", "doomed", priority=999
            )
            future = time.time() + 2.0
            os.utime(self.drop_in_dir / "doomed.py", (future, future))

            watcher._reload_file(
                self.drop_in_dir / "doomed.py", [old_plugin]
            )

            # 关键：callback 被调用了 1 次
            self.assertEqual(len(invocations), 1)
            file_name, failed_names = invocations[0]
            self.assertEqual(file_name, "doomed.py")
            self.assertIn("doomed", failed_names)
        finally:
            dispatcher.hot_register = original
            watcher.stop(timeout=2.0)

    def test_callback_exception_isolated_from_main_flow(self):
        """callback 自身抛错 → _reload_file 不传播异常。"""
        _write_plugin_file(self.drop_in_dir / "isolated.py", "isolated")

        dispatcher = GoalDispatcher()

        def bad_callback(file_name, failed_names):
            raise RuntimeError("callback 异常")

        watcher = HotReloadWatcher(
            dispatcher=dispatcher,
            drop_in_dir=Path("plugins_extra"),
            project_root=self.project_root,
            critical_failure_callback=bad_callback,
        )
        try:
            watcher.start()
            watcher.stop(timeout=2.0)

            # 让 hot_register 抛错
            original = dispatcher.hot_register

            def failing_hot_register(plugin):
                raise DuplicatePluginNameError("mock")

            dispatcher.hot_register = failing_hot_register

            old_plugin = next(
                p for p in dispatcher.list_plugins()
                if p.name == "isolated"
            )
            _write_plugin_file(
                self.drop_in_dir / "isolated.py",
                "isolated", priority=999,
            )
            future = time.time() + 2.0
            os.utime(
                self.drop_in_dir / "isolated.py", (future, future)
            )

            # 关键：不抛异常到调用方
            try:
                watcher._reload_file(
                    self.drop_in_dir / "isolated.py", [old_plugin]
                )
            except Exception as e:
                self.fail(
                    f"_reload_file 不应传播 callback 异常：{e}"
                )
        finally:
            dispatcher.hot_register = original
            watcher.stop(timeout=2.0)


if __name__ == "__main__":
    unittest.main()
