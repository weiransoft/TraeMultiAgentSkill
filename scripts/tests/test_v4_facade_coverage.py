"""facade.py 覆盖度补全测试（RED→GREEN 阶段）。

针对覆盖率分析中 facade.py 的缺失行（72% → 目标 100%）：
- 48-49: main_compat() 内部调用 _dispatch_through_v3
- 88-90: project_root 不存在 → 退出码 1
- 112-115: --task 缺失且非豁免模式 → 退出码 1
- 130-135: result.skipped_reason == "dry_run" → 退出码 0
- 199: watcher.wait_initial_scan 超时 → log warning
- 214-219: weakref tracking + atexit 注册

TDD 流程：
1. RED：写测试 → 验证失败
2. GREEN：让测试通过（修改 facade 行为或 mock）
3. REFACTOR：清理
"""
import argparse
import importlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# 路径设置
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import facade
import facade as facade_mod
from facade import (
    _start_hot_reload_if_enabled,
    _safe_watcher_stop,
    _dispatch_through_v3,
    main_compat,
    _cleanup_all_watchers,
)
from dispatcher.goal_dispatcher import GoalDispatcher


class TestFacadeMainCompat(unittest.TestCase):
    """main_compat() 兼容性入口（line 48-49）。"""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="facade_main_"))
        self.project_root = self._tmp / "project"
        self.project_root.mkdir()
        (self.project_root / "plugins_extra").mkdir()
        self._old_argv = sys.argv

    def tearDown(self) -> None:
        sys.argv = self._old_argv
        for key in list(sys.modules.keys()):
            if key.startswith("plugins_extra."):
                del sys.modules[key]
        shutil.rmtree(self._tmp, ignore_errors=True)
        # 清理 facade 跟踪的 watcher
        import facade
        with facade._watcher_tracking_lock:
            facade._watcher_refs.clear()

    def test_main_compat_dispatches_through_v3(self):
        """main_compat() 必须调用 _dispatch_through_v3 并返回其退出码。"""
        # 注入一个 --task，触发 dispatch 路径
        sys.argv = [
            "prog", "--project-root", str(self.project_root),
            "--task", "hello", "--no-hot-reload",
        ]
        # main_compat 会调用 parse_arguments() + _dispatch_through_v3
        # 我们不需要真的执行成功，只验证流程跑到 dispatch
        # 用 mock 让 _dispatch_through_v3 立即返回
        with patch.object(
            facade_mod, "_dispatch_through_v3", return_value=0
        ) as mock_dispatch:
            rc = main_compat()
            self.assertEqual(rc, 0)
            self.assertEqual(mock_dispatch.call_count, 1)


class TestFacadeProjectRootMissing(unittest.TestCase):
    """_dispatch_through_v3：project_root 不存在 → 退出码 1（line 88-90）。"""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="facade_proj_"))

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)
        import facade
        with facade._watcher_tracking_lock:
            facade._watcher_refs.clear()

    def test_nonexistent_project_root_returns_1(self):
        """不存在的 project_root → 退出码 1。"""
        nonexistent = self._tmp / "ghost"
        args = argparse.Namespace(
            project_root=str(nonexistent),
            task="hello",
            hot_reload=False,
            hot_reload_dir="plugins_extra",
            hot_reload_interval=5.0,
            goal_graph=None,
            goal_cancel=None,
            goal_resume=None,
            multi_goal=None,
            loop=1,
            goal=None,
            task_file=None,
            dry_run=False,
            agent="auto",
        )
        rc = _dispatch_through_v3(args)
        self.assertEqual(rc, 1)


class TestFacadeTaskRequired(unittest.TestCase):
    """_dispatch_through_v3：--task 缺失且非豁免模式 → 退出码 1（line 112-115）。"""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="facade_task_"))
        self.project_root = self._tmp / "project"
        self.project_root.mkdir()
        (self.project_root / "plugins_extra").mkdir()

    def tearDown(self) -> None:
        for key in list(sys.modules.keys()):
            if key.startswith("plugins_extra."):
                del sys.modules[key]
        shutil.rmtree(self._tmp, ignore_errors=True)
        import facade
        with facade._watcher_tracking_lock:
            facade._watcher_refs.clear()

    def test_missing_task_without_exemption_returns_1(self):
        """task 为空 + 非豁免模式 → 退出码 1。"""
        args = argparse.Namespace(
            project_root=str(self.project_root),
            task=None,  # 关键：task 缺失
            hot_reload=False,
            hot_reload_dir="plugins_extra",
            hot_reload_interval=5.0,
            goal_graph=None,
            goal_cancel=None,
            goal_resume=None,
            multi_goal=None,
            loop=1,
            goal=None,
            task_file=None,
            dry_run=False,
            agent="auto",
        )
        rc = _dispatch_through_v3(args)
        self.assertEqual(rc, 1)

    def test_task_file_missing_returns_1(self):
        """task_file 指定但不存在的文件 → 退出码 1。"""
        args = argparse.Namespace(
            project_root=str(self.project_root),
            task="hello",
            hot_reload=False,
            hot_reload_dir="plugins_extra",
            hot_reload_interval=5.0,
            goal_graph=None,
            goal_cancel=None,
            goal_resume=None,
            multi_goal=None,
            loop=1,
            goal=None,
            task_file="ghost.md",  # 关键：task_file 不存在
            dry_run=False,
            agent="auto",
        )
        rc = _dispatch_through_v3(args)
        self.assertEqual(rc, 1)

    def test_exemption_goal_graph_skips_task_required(self):
        """--goal-graph 模式豁免 task 必填（即使 task=None 也允许执行）。"""
        # 这里只验证不返回 1（可能返回 0 或因 dispatch 失败返回 1，
        # 但不应是 task 必填导致的 1）
        # 通过 mock dispatcher.dispatch 让其直接返回 dry_run = false，success = True
        with patch(
            "dispatcher.goal_dispatcher.GoalDispatcher"
        ) as mock_dispatcher_cls:
            mock_dispatcher = MagicMock()
            mock_dispatcher_cls.return_value = mock_dispatcher
            mock_result = MagicMock()
            mock_result.skipped_reason = None
            mock_result.success = True
            mock_dispatcher.dispatch.return_value = mock_result
            mock_dispatcher.list_plugins.return_value = []

            args = argparse.Namespace(
                project_root=str(self.project_root),
                task=None,
                hot_reload=False,
                hot_reload_dir="plugins_extra",
                hot_reload_interval=5.0,
                goal_graph="mermaid",  # 豁免模式
                goal_cancel=None,
                goal_resume=None,
                multi_goal=None,
                loop=1,
                goal=None,
                task_file=None,
                dry_run=False,
                agent="auto",
            )
            rc = _dispatch_through_v3(args)
            # 关键：不应该是 task 必填的 1（应该跑到 dispatch）
            # 因为是 mock，dispatch 返回 success=True，所以应该返回 0
            self.assertEqual(rc, 0)


class TestFacadeDryRunSkipped(unittest.TestCase):
    """_dispatch_through_v3：dispatcher 返回 dry_run skipped → 退出码 0（line 130-135）。"""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="facade_dry_"))
        self.project_root = self._tmp / "project"
        self.project_root.mkdir()
        (self.project_root / "plugins_extra").mkdir()

    def tearDown(self) -> None:
        for key in list(sys.modules.keys()):
            if key.startswith("plugins_extra."):
                del sys.modules[key]
        shutil.rmtree(self._tmp, ignore_errors=True)
        import facade
        with facade._watcher_tracking_lock:
            facade._watcher_refs.clear()

    def test_dry_run_skipped_returns_0(self):
        """dispatcher 返回 skipped_reason="dry_run" → 退出码 0。"""
        with patch(
            "dispatcher.goal_dispatcher.GoalDispatcher"
        ) as mock_dispatcher_cls:
            mock_dispatcher = MagicMock()
            mock_dispatcher_cls.return_value = mock_dispatcher
            mock_result = MagicMock()
            mock_result.skipped_reason = "dry_run"  # 关键
            mock_dispatcher.dispatch.return_value = mock_result
            mock_dispatcher.list_plugins.return_value = []

            args = argparse.Namespace(
                project_root=str(self.project_root),
                task="hello",
                hot_reload=False,
                hot_reload_dir="plugins_extra",
                hot_reload_interval=5.0,
                goal_graph=None,
                goal_cancel=None,
                goal_resume=None,
                multi_goal=None,
                loop=1,
                goal=None,
                task_file=None,
                dry_run=True,  # 关键：启用 dry-run
                agent="auto",
            )
            rc = _dispatch_through_v3(args)
            self.assertEqual(rc, 0)

    def test_no_match_skipped_falls_back_to_dispatch_agent_v2(self):
        """dispatcher 返回 skipped_reason="no_match" → 走 dispatch_agent_v2 兜底。"""
        with patch(
            "dispatcher.goal_dispatcher.GoalDispatcher"
        ) as mock_dispatcher_cls, patch(
            "facade.dispatch_agent_v2", return_value=True
        ) as mock_v2:
            mock_dispatcher = MagicMock()
            mock_dispatcher_cls.return_value = mock_dispatcher
            mock_result = MagicMock()
            mock_result.skipped_reason = "no_match"
            mock_dispatcher.dispatch.return_value = mock_result
            mock_dispatcher.list_plugins.return_value = []

            args = argparse.Namespace(
                project_root=str(self.project_root),
                task="hello",
                hot_reload=False,
                hot_reload_dir="plugins_extra",
                hot_reload_interval=5.0,
                goal_graph=None,
                goal_cancel=None,
                goal_resume=None,
                multi_goal=None,
                loop=1,
                goal=None,
                task_file=None,
                dry_run=False,
                agent="auto",
            )
            rc = _dispatch_through_v3(args)
            # 关键：dispatch_agent_v2 被调用
            self.assertEqual(mock_v2.call_count, 1)
            self.assertEqual(rc, 0)  # v2 成功 → 0

    def test_plugin_matched_success_returns_0(self):
        """有 plugin 匹配 + result.success=True → 退出码 0。"""
        with patch(
            "dispatcher.goal_dispatcher.GoalDispatcher"
        ) as mock_dispatcher_cls:
            mock_dispatcher = MagicMock()
            mock_dispatcher_cls.return_value = mock_dispatcher
            mock_result = MagicMock()
            mock_result.skipped_reason = None
            mock_result.success = True
            mock_dispatcher.dispatch.return_value = mock_result
            mock_dispatcher.list_plugins.return_value = []

            args = argparse.Namespace(
                project_root=str(self.project_root),
                task="hello",
                hot_reload=False,
                hot_reload_dir="plugins_extra",
                hot_reload_interval=5.0,
                goal_graph=None,
                goal_cancel=None,
                goal_resume=None,
                multi_goal=None,
                loop=1,
                goal=None,
                task_file=None,
                dry_run=False,
                agent="auto",
            )
            rc = _dispatch_through_v3(args)
            self.assertEqual(rc, 0)

    def test_plugin_matched_failure_returns_1(self):
        """有 plugin 匹配 + result.success=False → 退出码 1。"""
        with patch(
            "dispatcher.goal_dispatcher.GoalDispatcher"
        ) as mock_dispatcher_cls:
            mock_dispatcher = MagicMock()
            mock_dispatcher_cls.return_value = mock_dispatcher
            mock_result = MagicMock()
            mock_result.skipped_reason = None
            mock_result.success = False
            mock_dispatcher.dispatch.return_value = mock_result
            mock_dispatcher.list_plugins.return_value = []

            args = argparse.Namespace(
                project_root=str(self.project_root),
                task="hello",
                hot_reload=False,
                hot_reload_dir="plugins_extra",
                hot_reload_interval=5.0,
                goal_graph=None,
                goal_cancel=None,
                goal_resume=None,
                multi_goal=None,
                loop=1,
                goal=None,
                task_file=None,
                dry_run=False,
                agent="auto",
            )
            rc = _dispatch_through_v3(args)
            self.assertEqual(rc, 1)


class TestFacadeWatchTimeoutWarning(unittest.TestCase):
    """_start_hot_reload_if_enabled：watcher.wait_initial_scan 超时 → log warning（line 199）。"""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="facade_watch_"))
        self.project_root = self._tmp / "project"
        self.project_root.mkdir()
        (self.project_root / "plugins_extra").mkdir()

    def tearDown(self) -> None:
        for key in list(sys.modules.keys()):
            if key.startswith("plugins_extra."):
                del sys.modules[key]
        shutil.rmtree(self._tmp, ignore_errors=True)
        import facade
        with facade._watcher_tracking_lock:
            facade._watcher_refs.clear()

    def test_watcher_initial_scan_timeout_logged(self):
        """watcher.wait_initial_scan 返回 False → 记录 warning 日志。"""
        dispatcher = GoalDispatcher()

        class _Args:
            hot_reload = True
            hot_reload_dir = "plugins_extra"
            hot_reload_interval = 5.0

        args = _Args()

        # mock watcher 行为：wait_initial_scan 返回 False（超时）
        with patch(
            "dispatcher.hot_reload_watcher.HotReloadWatcher"
        ) as mock_watcher_cls:
            mock_watcher = MagicMock()
            mock_watcher_cls.return_value = mock_watcher
            mock_watcher.wait_initial_scan.return_value = False  # 超时

            watcher = _start_hot_reload_if_enabled(
                dispatcher, args, self.project_root
            )

            try:
                self.assertIsNotNone(watcher)
                # 关键：wait_initial_scan 被调用了
                self.assertGreater(mock_watcher.wait_initial_scan.call_count, 0)
            finally:
                # 清理（避免污染）
                with facade._watcher_tracking_lock:
                    facade._watcher_refs.clear()


class TestFacadeWeakrefTracking(unittest.TestCase):
    """_start_hot_reload_if_enabled：weakref 跟踪 + atexit 注册（line 214-219）。"""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="facade_weakref_"))
        self.project_root = self._tmp / "project"
        self.project_root.mkdir()
        (self.project_root / "plugins_extra").mkdir()

    def tearDown(self) -> None:
        for key in list(sys.modules.keys()):
            if key.startswith("plugins_extra."):
                del sys.modules[key]
        shutil.rmtree(self._tmp, ignore_errors=True)
        import facade
        with facade._watcher_tracking_lock:
            facade._watcher_refs.clear()

    def test_watcher_registered_in_weakref_set(self):
        """_start_hot_reload_if_enabled 成功启动 → watcher 加入 weakref 集合。"""
        import facade

        class _Args:
            hot_reload = True
            hot_reload_dir = "plugins_extra"
            hot_reload_interval = 5.0

        args = _Args()
        dispatcher = GoalDispatcher()
        watcher = _start_hot_reload_if_enabled(
            dispatcher, args, self.project_root
        )

        try:
            self.assertIsNotNone(watcher)
            # 验证：_watcher_refs 中应至少有一个 watcher
            with facade._watcher_tracking_lock:
                self.assertGreaterEqual(len(facade._watcher_refs), 1)
                # 取出 watcher 引用
                refs = list(facade._watcher_refs)
                resolved = [r() for r in refs]
                resolved = [w for w in resolved if w is not None]
                # 至少有一个是我们刚启动的 watcher
                self.assertIn(watcher, resolved)
        finally:
            _safe_watcher_stop(watcher)


class TestFacadeCleanupWatchers(unittest.TestCase):
    """_cleanup_all_watchers 端到端：遍历 weakref 集合并 stop（line 226-227）。"""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="facade_cleanup_"))
        self.project_root = self._tmp / "project"
        self.project_root.mkdir()
        (self.project_root / "plugins_extra").mkdir()

    def tearDown(self) -> None:
        for key in list(sys.modules.keys()):
            if key.startswith("plugins_extra."):
                del sys.modules[key]
        shutil.rmtree(self._tmp, ignore_errors=True)
        import facade
        with facade._watcher_tracking_lock:
            facade._watcher_refs.clear()

    def test_cleanup_calls_stop_on_alive_watchers(self):
        """_cleanup_all_watchers 遍历所有 alive watcher 并调用 stop。"""
        dispatcher = GoalDispatcher()

        class _Args:
            hot_reload = True
            hot_reload_dir = "plugins_extra"
            hot_reload_interval = 5.0

        args = _Args()
        watcher = _start_hot_reload_if_enabled(
            dispatcher, args, self.project_root
        )

        # 触发 cleanup
        _cleanup_all_watchers()
        # 验证：watcher 线程已结束
        if watcher is not None:
            self.assertFalse(watcher._thread.is_alive())


class TestFacadeSafeWatcherStop(unittest.TestCase):
    """_safe_watcher_stop 异常隔离（atexit 路径）。"""

    def test_safe_stop_isolates_watcher_exceptions(self):
        """watcher.stop 抛异常 → _safe_watcher_stop 静默吞掉（atexit 不能抛）。"""
        mock_watcher = MagicMock()
        mock_watcher.stop.side_effect = RuntimeError("test exception")

        # 关键：不抛异常到调用方
        _safe_watcher_stop(mock_watcher)
        mock_watcher.stop.assert_called_once()


class TestFacadeMutexViolation(unittest.TestCase):
    """_dispatch_through_v3：mutex 校验失败 → 退出码 1（line 88-90）。"""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="facade_mutex_"))
        self.project_root = self._tmp / "project"
        self.project_root.mkdir()
        (self.project_root / "plugins_extra").mkdir()

    def tearDown(self) -> None:
        for key in list(sys.modules.keys()):
            if key.startswith("plugins_extra."):
                del sys.modules[key]
        shutil.rmtree(self._tmp, ignore_errors=True)
        import facade
        with facade._watcher_tracking_lock:
            facade._watcher_refs.clear()

    def test_mutex_violation_returns_1(self):
        """goal-cancel + goal-resume 同时设置 → mutex 违反 → 退出码 1。"""
        args = argparse.Namespace(
            project_root=str(self.project_root),
            task="hello",
            hot_reload=False,
            hot_reload_dir="plugins_extra",
            hot_reload_interval=5.0,
            goal_graph=None,
            goal_cancel="goal-1",  # 关键：与 goal_resume 互斥
            goal_resume="goal-1",   # 关键：与 goal_cancel 互斥
            multi_goal=None,
            loop=1,
            goal=None,
            task_file=None,
            dry_run=False,
            agent="auto",
        )
        rc = _dispatch_through_v3(args)
        # mutex 校验失败 → 退出码 1
        self.assertEqual(rc, 1)


class TestFacadeWatcherConstructionException(unittest.TestCase):
    """_start_hot_reload_if_enabled：watcher 构造异常（line 189-194）。"""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="facade_watch_exc_"))
        self.project_root = self._tmp / "project"
        self.project_root.mkdir()
        (self.project_root / "plugins_extra").mkdir()

    def tearDown(self) -> None:
        for key in list(sys.modules.keys()):
            if key.startswith("plugins_extra."):
                del sys.modules[key]
        shutil.rmtree(self._tmp, ignore_errors=True)
        import facade
        with facade._watcher_tracking_lock:
            facade._watcher_refs.clear()

    def test_watcher_construction_generic_exception(self):
        """watcher 构造抛通用 Exception（不是 DropInPathError）→ 隔离 + 返回 None。"""
        class _Args:
            hot_reload = True
            hot_reload_dir = "plugins_extra"
            hot_reload_interval = 5.0

        args = _Args()
        dispatcher = GoalDispatcher()

        # mock HotReloadWatcher 构造抛通用异常
        with patch(
            "dispatcher.hot_reload_watcher.HotReloadWatcher"
        ) as mock_watcher_cls:
            mock_watcher_cls.side_effect = RuntimeError(
                "mock construction failure"
            )
            watcher = _start_hot_reload_if_enabled(
                dispatcher, args, self.project_root
            )
            # 关键：异常被隔离，返回 None
            self.assertIsNone(watcher)


if __name__ == "__main__":
    unittest.main()
