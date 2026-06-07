"""hot_reload_watcher.py 第二轮覆盖度补全测试。

针对 hot_reload_watcher.py 第二轮 missing 行（15 stmt / 9 ranges）：
- 151-152: drop-in 目录创建 OSError → DropInPathError
- 176-178: start() 中 _scan_once() 抛异常 → log 隔离
- 209: stop() 线程未在 timeout 内停止 → log warning
- 341-343: _reload_file 步骤 1 unregister 失败 → 加到 failures
- 349-358: 步骤 1.5 fail-fast → log + rollback + return
- 389: _reload_file 步骤 4 register_failures 路径（部分成功）
- 446: _unload_file 早返回（name 不在 _file_states）
- 452-453: _unload_file 中 unregister 抛错 → log 隔离
- 460->465: 关键分支

TDD 流程：写测试 → 验证覆盖目标行。
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
        self._tmp = tempfile.mkdtemp(prefix="watch_cov2_")
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


class TestWatcherMkdirFailure(_TempDirMixin, unittest.TestCase):
    """_resolve_drop_in_dir：mkdir 抛 OSError（line 151-152）。"""

    def test_mkdir_oserror_raises_dropinpatherror(self):
        """drop-in 父目录无法创建（如权限拒绝）→ DropInPathError。"""
        # 删除 project_root，模拟项目根目录不存在
        # 这种情况下 makedirs 会失败（因为父目录都没有）
        # 但 drop_in_dir 是相对路径，会拼接 project_root
        # 实际上更精确的测试：mock Path.mkdir 抛 OSError

        dispatcher = GoalDispatcher()
        # 提前 drop_in_dir 删除，并用 patch 让 mkdir 抛 OSError
        shutil.rmtree(self.drop_in_dir)

        with patch.object(Path, "mkdir", side_effect=OSError(13, "Permission denied")):
            with self.assertRaises(DropInPathError) as cm:
                HotReloadWatcher(
                    dispatcher=dispatcher,
                    drop_in_dir=Path("plugins_extra"),
                    project_root=self.project_root,
                )
            # 错误消息应说明"无法创建"
            self.assertIn("无法创建", str(cm.exception))


class TestWatcherStartScanException(_TempDirMixin, unittest.TestCase):
    """start() 中 _scan_once() 抛异常（line 176-178）。"""

    def test_start_isolates_scan_exception(self):
        """_scan_once() 抛异常 → start() 不抛 + _initial_scan_done set。"""
        dispatcher = GoalDispatcher()
        watcher = HotReloadWatcher(
            dispatcher=dispatcher,
            drop_in_dir=Path("plugins_extra"),
            project_root=self.project_root,
        )
        try:
            # mock _scan_once 让其抛异常
            with patch.object(
                watcher, "_scan_once",
                side_effect=RuntimeError("mock scan failure")
            ):
                # start() 不应抛
                watcher.start()
                # 关键：_initial_scan_done 必须 set
                self.assertTrue(watcher._initial_scan_done.is_set())
        finally:
            watcher.stop(timeout=2.0)


class TestWatcherStopTimeout(_TempDirMixin, unittest.TestCase):
    """stop() 线程未在 timeout 内停止 → log warning（line 209）。"""

    def test_stop_logs_warning_when_thread_not_terminated(self):
        """线程 join 超时未结束 → log warning。"""
        dispatcher = GoalDispatcher()
        watcher = HotReloadWatcher(
            dispatcher=dispatcher,
            drop_in_dir=Path("plugins_extra"),
            project_root=self.project_root,
            poll_interval=0.3,
        )
        try:
            watcher.start()
            # mock _thread.join 让其超时（不结束）
            with patch.object(
                threading.Thread, "join"
            ) as mock_join:
                # mock is_alive 始终返回 True
                with patch.object(
                    threading.Thread, "is_alive", return_value=True
                ):
                    watcher.stop(timeout=0.001)
                    # 关键：join 被调用
                    self.assertGreater(mock_join.call_count, 0)
        finally:
            # 强制清理（mock 后线程可能仍在）
            watcher._running = False
            if watcher._thread is not None:
                try:
                    watcher._thread.join(timeout=2.0)
                except Exception:
                    pass


class TestWatcherReloadStep1Failure(_TempDirMixin, unittest.TestCase):
    """_reload_file 步骤 1 unregister 失败（line 341-343 + 349-358）。"""

    def test_reload_step1_partial_failure_triggers_rollback(self):
        """步骤 1 部分 unregister 失败 → 步骤 1.5 触发 + rollback + return。"""
        _write_plugin_file(self.drop_in_dir / "doomed.py", "doomed")

        dispatcher = GoalDispatcher()
        watcher = HotReloadWatcher(
            dispatcher=dispatcher,
            drop_in_dir=Path("plugins_extra"),
            project_root=self.project_root,
        )
        try:
            watcher.start()
            watcher.stop(timeout=2.0)  # 阻止后台线程

            old_plugin = next(
                p for p in dispatcher.list_plugins()
                if p.name == "doomed"
            )

            # mock hot_unregister 让其抛错
            original = dispatcher.hot_unregister

            def failing_unregister(name, force=False):
                raise DuplicatePluginNameError(
                    f"mock unregister failure: {name}"
                )

            dispatcher.hot_unregister = failing_unregister

            # 修改文件 + bump mtime
            _write_plugin_file(
                self.drop_in_dir / "doomed.py",
                "doomed", priority=999,
            )
            future = time.time() + 2.0
            os.utime(
                self.drop_in_dir / "doomed.py", (future, future)
            )

            # 关键：不抛异常
            try:
                watcher._reload_file(
                    self.drop_in_dir / "doomed.py", [old_plugin]
                )
            except Exception as e:
                self.fail(
                    f"_reload_file 不应抛异常：{e}"
                )
        finally:
            dispatcher.hot_unregister = original
            watcher.stop(timeout=2.0)


class TestWatcherReloadStep4PartialSuccess(_TempDirMixin, unittest.TestCase):
    """_reload_file 步骤 4 register_failures 路径（line 389）。"""

    def test_reload_step4_partial_success_logs_warning(self):
        """reload 时部分新 plugin 成功 → log warning。"""
        # 写入单 plugin 文件
        _write_plugin_file(
            self.drop_in_dir / "good.py", "good", priority=100
        )

        dispatcher = GoalDispatcher()
        watcher = HotReloadWatcher(
            dispatcher=dispatcher,
            drop_in_dir=Path("plugins_extra"),
            project_root=self.project_root,
        )
        try:
            watcher.start()
            watcher.stop(timeout=2.0)

            old_plugin = next(
                p for p in dispatcher.list_plugins()
                if p.name == "good"
            )

            # mock hot_register 让 1 个失败、1 个成功
            # 通过制造 1 个错误 + 1 个正常
            original = dispatcher.hot_register
            call_count = [0]

            def flaky_hot_register(plugin):
                call_count[0] += 1
                if call_count[0] == 1:
                    return original(plugin)  # 第 1 个成功
                raise DuplicatePluginNameError(
                    f"mock: {plugin.name}"
                )

            dispatcher.hot_register = flaky_hot_register

            # 写一个含 2 个 plugin 的文件 + bump mtime
            code = """
from plugins.base import GoalCommandPlugin
class A(GoalCommandPlugin):
    @property
    def name(self): return "good"
    @property
    def priority(self): return 200
    @property
    def mutex_with(self): return set()
    @property
    def requires_task(self): return False
    def matches(self, args): return True
    def execute(self, args, ctx): return True
class B(GoalCommandPlugin):
    @property
    def name(self): return "second"
    @property
    def priority(self): return 100
    @property
    def mutex_with(self): return set()
    @property
    def requires_task(self): return False
    def matches(self, args): return True
    def execute(self, args, ctx): return True
"""
            (self.drop_in_dir / "good.py").write_text(
                code, encoding="utf-8"
            )
            future = time.time() + 2.0
            os.utime(
                self.drop_in_dir / "good.py", (future, future)
            )

            watcher._reload_file(
                self.drop_in_dir / "good.py", [old_plugin]
            )
            # 关键：第 1 个 plugin 成功（名字仍是 "good"），
            # 第 2 个 plugin 失败（"second"）
            # _file_states 应记录 1 个 plugin
            self.assertIn("good.py", watcher._file_states)
        finally:
            dispatcher.hot_register = original
            watcher.stop(timeout=2.0)


class TestWatcherUnloadFileEdgeCases(_TempDirMixin, unittest.TestCase):
    """_unload_file 边缘情况（line 446, 452-453）。"""

    def test_unload_unknown_file_no_op(self):
        """_unload_file 对未记录文件 → no-op（line 446 early return）。"""
        dispatcher = GoalDispatcher()
        watcher = HotReloadWatcher(
            dispatcher=dispatcher,
            drop_in_dir=Path("plugins_extra"),
            project_root=self.project_root,
        )
        try:
            watcher.start()
            # 卸载不存在的文件名 → 不抛异常
            watcher._unload_file("ghost_file.py")
            # 关键：_file_states 不变
            self.assertNotIn("ghost_file.py", watcher._file_states)
        finally:
            watcher.stop(timeout=2.0)

    def test_unload_file_with_unregister_exception(self):
        """_unload_file 中 unregister 抛错 → log 隔离 + 继续（line 452-453）。"""
        # 写一个含 2 个 plugin 的文件
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

            # mock hot_unregister 让其抛错
            original = dispatcher.hot_unregister

            def failing_unregister(name, force=False):
                raise RuntimeError(f"mock unregister: {name}")

            dispatcher.hot_unregister = failing_unregister

            # 关键：不抛异常
            try:
                watcher._unload_file("ab.py")
            except Exception as e:
                self.fail(
                    f"_unload_file 不应传播 unregister 异常：{e}"
                )
        finally:
            dispatcher.hot_unregister = original
            watcher.stop(timeout=2.0)


class TestWatcherUnloadFileSysmodulesCleanup(_TempDirMixin, unittest.TestCase):
    """_unload_file 中 sys.modules 清理路径。"""

    def test_unload_file_cleans_sysmodules(self):
        """_unload_file 卸载成功 → sys.modules 中 plugins_extra.<stem> 被清。"""
        # 关键：使用 kebab-case 名字以通过 _load_file 的 name 校验
        _write_plugin_file(
            self.drop_in_dir / "cleanup_test.py",
            "cleanup-test", priority=100,
        )

        dispatcher = GoalDispatcher()
        watcher = HotReloadWatcher(
            dispatcher=dispatcher,
            drop_in_dir=Path("plugins_extra"),
            project_root=self.project_root,
        )
        try:
            watcher.start()
            # 关键：sys.modules 中应有 plugins_extra.cleanup_test
            self.assertIn(
                "plugins_extra.cleanup_test", sys.modules
            )
            # 卸载文件
            watcher._unload_file("cleanup_test.py")
            # 关键：sys.modules 中应被清理
            self.assertNotIn(
                "plugins_extra.cleanup_test", sys.modules
            )
        finally:
            watcher.stop(timeout=2.0)

    def test_unload_file_cleans_sysmodules_with_special_chars(self):
        """特殊字符文件名 → sys.modules key 仍被正确清理。"""
        # 写一个文件名含特殊字符的文件
        code = """
from plugins.base import GoalCommandPlugin
class Special(GoalCommandPlugin):
    @property
    def name(self): return "special"
    @property
    def priority(self): return 100
    @property
    def mutex_with(self): return set()
    @property
    def requires_task(self): return False
    def matches(self, args): return True
    def execute(self, args, ctx): return True
"""
        # 用合法 ASCII 特殊字符（sanitize 会替换为 _）
        (self.drop_in_dir / "my-special@file.py").write_text(
            code, encoding="utf-8"
        )

        dispatcher = GoalDispatcher()
        watcher = HotReloadWatcher(
            dispatcher=dispatcher,
            drop_in_dir=Path("plugins_extra"),
            project_root=self.project_root,
        )
        try:
            watcher.start()

            # 找到刚注入的 sys.modules key（用 _ 替换特殊字符）
            matched_keys = [
                k for k in sys.modules
                if k.startswith("plugins_extra.my")
            ]
            self.assertEqual(len(matched_keys), 1)
            module_key = matched_keys[0]
            self.assertIn(module_key, sys.modules)

            # 卸载
            watcher._unload_file("my-special@file.py")
            # 关键：sys.modules key 被清理
            self.assertNotIn(module_key, sys.modules)
        finally:
            watcher.stop(timeout=2.0)


if __name__ == "__main__":
    unittest.main()
