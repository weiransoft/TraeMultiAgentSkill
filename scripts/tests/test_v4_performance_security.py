"""Phase 17 v3.1 性能与安全专项测试。

测试目标：
- R-10：1000 个 drop-in 文件 start() 启动 < 5s（性能基线）
- 性能：单个 drop-in 文件 load 耗时
- 性能：reload 单文件耗时
- 安全：pathlib 路径穿越 + 软链攻击 + 注入攻击
- 安全：plugin 名称注入（kebab-case 强制）
- 安全：单文件最大 plugin 数（DoS 防护）
"""
import os
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
from dispatcher.errors import DropInPathError, MutexDeclarationError
from dispatcher.goal_dispatcher import GoalDispatcher
from dispatcher.hot_reload_watcher import HotReloadWatcher
from dispatcher.drop_in_loader import DropInLoader


def _write_plugin_file(
    path: Path, plugin_name: str, priority: int = 100
) -> Path:
    """写入标准 plugin 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    code = f"""
from plugins.base import GoalCommandPlugin

class _Generated(GoalCommandPlugin):
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


class TestPerformanceBulkLoad(unittest.TestCase):
    """R-10：1000 个 drop-in 文件 start() 启动 < 5s。"""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="perf_test_")
        self.tmp_path = Path(self._tmp)
        self.project_root = self.tmp_path / "project"
        self.project_root.mkdir()
        self.drop_in_dir = self.project_root / "plugins_extra"
        self.drop_in_dir.mkdir()
        self._dispatcher = GoalDispatcher()

    def tearDown(self) -> None:
        for key in list(sys.modules.keys()):
            if key.startswith("plugins_extra."):
                del sys.modules[key]
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_1000_dropin_files_start_under_5s(self):
        """1000 个 drop-in 文件：start() 启动 < 5s（性能基线 R-10）。"""
        # 写入 1000 个文件
        for i in range(1000):
            _write_plugin_file(
                self.drop_in_dir / f"plugin_{i:04d}.py",
                plugin_name=f"perf-{i:04d}",
                priority=1000 + i,
            )

        watcher = HotReloadWatcher(
            dispatcher=self._dispatcher,
            drop_in_dir=Path("plugins_extra"),
            project_root=self.project_root,
            poll_interval=60.0,  # 启动后立即停止，不进入轮询
        )

        try:
            start = time.time()
            watcher.start()
            elapsed = time.time() - start

            # 关键断言：启动 < 5s
            self.assertLess(
                elapsed, 5.0,
                f"R-10 性能违约：1000 文件 start 耗时 {elapsed:.2f}s > 5s"
            )

            # 验证：所有 plugin 都被加载
            loaded_count = sum(
                1 for p in self._dispatcher.list_plugins()
                if p.name.startswith("perf-")
            )
            self.assertEqual(loaded_count, 1000)
        finally:
            watcher.stop(timeout=10.0)

    def test_single_file_load_under_50ms(self):
        """单文件 load 耗时 < 50ms（正常 plugin 复杂度）。"""
        _write_plugin_file(
            self.drop_in_dir / "fast.py", plugin_name="fast"
        )
        start = time.time()
        plugins = DropInLoader.load_from_file(self.drop_in_dir / "fast.py")
        elapsed = time.time() - start
        self.assertEqual(len(plugins), 1)
        self.assertLess(
            elapsed, 0.05,
            f"单文件 load 耗时 {elapsed*1000:.1f}ms > 50ms"
        )


class TestSecurityPathTraversal(unittest.TestCase):
    """安全：路径穿越攻击防护（多层验证）。"""

    def test_cli_rejects_absolute_path(self):
        """CLI 第一层：绝对路径拒绝。"""
        from cli.parser import _validate_drop_in_dir
        import argparse
        with self.assertRaises(argparse.ArgumentTypeError):
            _validate_drop_in_dir("/etc/passwd")

    def test_cli_rejects_dotdot_path(self):
        """CLI 第一层：含 '..' 路径拒绝。"""
        from cli.parser import _validate_drop_in_dir
        import argparse
        with self.assertRaises(argparse.ArgumentTypeError):
            _validate_drop_in_dir("../../etc/passwd")

    def test_watcher_rejects_symlink_outside_project_root(self):
        """watcher 第二层：软链跳出 project_root 拒绝（N5）。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project_root = tmp_path / "project"
            project_root.mkdir()
            outside = tmp_path / "outside"
            outside.mkdir()
            try:
                evil_link = project_root / "evil"
                evil_link.symlink_to(outside)
            except OSError:
                self.skipTest("当前平台不支持创建软链")

            dispatcher = GoalDispatcher()
            with self.assertRaises(DropInPathError) as cm:
                HotReloadWatcher(
                    dispatcher=dispatcher,
                    drop_in_dir=Path("evil"),
                    project_root=project_root,
                )
            self.assertIn(str(outside), str(cm.exception))

    def test_watcher_rejects_absolute_path(self):
        """watcher 第二层：绝对路径拒绝。"""
        with tempfile.TemporaryDirectory() as tmp:
            dispatcher = GoalDispatcher()
            with self.assertRaises(DropInPathError):
                HotReloadWatcher(
                    dispatcher=dispatcher,
                    drop_in_dir=Path("/tmp"),
                    project_root=Path(tmp),
                )


class TestSecurityPluginNameValidation(unittest.TestCase):
    """安全：plugin name 强制 kebab-case（防注入）。"""

    def test_uppercase_name_rejected(self):
        """plugin name 含大写字母 → 拒绝（H-6 校验）。"""
        from plugins.base import GoalCommandPlugin

        class BadNamePlugin(GoalCommandPlugin):
            @property
            def name(self): return "BadName"
            @property
            def priority(self): return 100
            @property
            def mutex_with(self): return set()
            @property
            def requires_task(self): return False
            def matches(self, args): return True
            def execute(self, args, ctx): return True

        dispatcher = GoalDispatcher()
        with self.assertRaises(MutexDeclarationError):
            dispatcher.hot_register(BadNamePlugin())

    def test_special_chars_in_name_rejected(self):
        """plugin name 含特殊字符 → 拒绝。"""
        from plugins.base import GoalCommandPlugin

        class SpecialCharPlugin(GoalCommandPlugin):
            @property
            def name(self): return "evil; rm -rf /"
            @property
            def priority(self): return 100
            @property
            def mutex_with(self): return set()
            @property
            def requires_task(self): return False
            def matches(self, args): return True
            def execute(self, args, ctx): return True

        dispatcher = GoalDispatcher()
        with self.assertRaises(MutexDeclarationError):
            dispatcher.hot_register(SpecialCharPlugin())

    def test_empty_name_rejected(self):
        """plugin name 为空字符串 → 拒绝。"""
        from plugins.base import GoalCommandPlugin

        class EmptyNamePlugin(GoalCommandPlugin):
            @property
            def name(self): return ""
            @property
            def priority(self): return 100
            @property
            def mutex_with(self): return set()
            @property
            def requires_task(self): return False
            def matches(self, args): return True
            def execute(self, args, ctx): return True

        dispatcher = GoalDispatcher()
        with self.assertRaises(MutexDeclarationError):
            dispatcher.hot_register(EmptyNamePlugin())


class TestSecuritySysModulesCleanup(unittest.TestCase):
    """安全：sys.modules 内存泄漏防护（reload 100 次不累积）。"""

    def test_100_reload_no_sysmodules_leak(self):
        """100 次 reload → sys.modules 中 plugins_extra.* 项数稳定 ≤ 1。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project_root = tmp_path / "project"
            project_root.mkdir()
            drop_in_dir = project_root / "plugins_extra"
            drop_in_dir.mkdir()

            _write_plugin_file(
                drop_in_dir / "cycle.py", plugin_name="cycle"
            )

            dispatcher = GoalDispatcher()
            watcher = HotReloadWatcher(
                dispatcher=dispatcher,
                drop_in_dir=Path("plugins_extra"),
                project_root=project_root,
            )

            try:
                watcher.start()
                watcher.stop(timeout=2.0)

                # 记录 reload 前的 sys.modules 计数
                for _ in range(100):
                    # 写文件 + bump mtime + 手动 reload
                    _write_plugin_file(
                        drop_in_dir / "cycle.py",
                        plugin_name="cycle",
                        priority=100,
                    )
                    # 强制 mtime 严格递增
                    future = time.time() + 2.0
                    os.utime(
                        drop_in_dir / "cycle.py", (future, future)
                    )
                    # 直接调用 _reload_file 模拟一次 reload
                    old_plugin = next(
                        p for p in dispatcher.list_plugins()
                        if p.name == "cycle"
                    )
                    watcher._reload_file(
                        drop_in_dir / "cycle.py", [old_plugin]
                    )

                # 验证：sys.modules 中 plugins_extra.* 项数 ≤ 1
                leaked_modules = [
                    k for k in sys.modules
                    if k.startswith("plugins_extra.")
                ]
                self.assertLessEqual(
                    len(leaked_modules), 1,
                    f"sys.modules 泄漏：{leaked_modules}"
                )
            finally:
                # 清理
                for key in list(sys.modules.keys()):
                    if key.startswith("plugins_extra."):
                        del sys.modules[key]
                watcher.stop(timeout=2.0)


class TestSecurityThreadSafety(unittest.TestCase):
    """安全：线程安全（dispatcher 在并发 hot_register / dispatch 下不崩溃）。"""

    def test_concurrent_hot_register_serialized(self):
        """10 线程并发 hot_register 不同 name/priority → 全部成功（RLock 串行化）。"""
        from plugins.base import GoalCommandPlugin

        class _TempPlugin(GoalCommandPlugin):
            def __init__(self, name, priority):
                self._name = name
                self._priority = priority
            @property
            def name(self): return self._name
            @property
            def priority(self): return self._priority
            @property
            def mutex_with(self): return set()
            @property
            def requires_task(self): return False
            def matches(self, args): return True
            def execute(self, args, ctx): return True

        dispatcher = GoalDispatcher()
        errors: List[Exception] = []
        lock = threading.Lock()

        def register_one(idx: int) -> None:
            try:
                # 不同 name + 不同 priority（priority 必须唯一否则 DuplicatePriorityError）
                dispatcher.hot_register(
                    _TempPlugin(f"concurrent-{idx}", 1000 + idx)
                )
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [
            threading.Thread(target=register_one, args=(i,))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 关键断言：无错误 + 全部 10 个 plugin 注册成功
        self.assertEqual(errors, [])
        names = [
            p.name for p in dispatcher.list_plugins()
            if p.name.startswith("concurrent-")
        ]
        self.assertEqual(len(names), 10)


class TestWatcherResourceLeakage(unittest.TestCase):
    """安全：watcher 资源泄漏防护（stop 后线程结束）。"""

    def test_stop_releases_thread(self):
        """watcher.stop() 后线程正常退出（不泄漏）。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project_root = tmp_path / "project"
            project_root.mkdir()
            (project_root / "plugins_extra").mkdir()

            dispatcher = GoalDispatcher()
            watcher = HotReloadWatcher(
                dispatcher=dispatcher,
                drop_in_dir=Path("plugins_extra"),
                project_root=project_root,
                poll_interval=0.2,
            )
            watcher.start()
            # 启动后立即 stop
            watcher.stop(timeout=2.0)
            # 关键断言：线程已结束
            self.assertFalse(
                watcher._thread.is_alive(),
                "watcher 线程未在 stop() 后退出（资源泄漏）"
            )

    def test_multiple_start_stop_no_thread_leak(self):
        """多次 start+stop → 不累积线程。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project_root = tmp_path / "project"
            project_root.mkdir()
            (project_root / "plugins_extra").mkdir()

            dispatcher = GoalDispatcher()
            for _ in range(5):
                watcher = HotReloadWatcher(
                    dispatcher=dispatcher,
                    drop_in_dir=Path("plugins_extra"),
                    project_root=project_root,
                    poll_interval=0.2,
                )
                watcher.start()
                watcher.stop(timeout=2.0)
                self.assertFalse(watcher._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
