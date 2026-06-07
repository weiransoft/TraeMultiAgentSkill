"""Phase 17 v3 CLI 集成测试（§5.5 阶段 4）。

测试目标：
- P0-1：--hot-reload / --no-hot-reload 互斥 group
- P0-7 第一层：CLI type 校验绝对路径 / '..'
- 路径安全负测试（N1/N2/N3 三层防护）
- --hot-reload-interval 默认值与钳制
- facade 集成：_start_hot_reload_if_enabled 行为

注解：本测试用 subprocess 真实运行 CLI 入口（trae_agent_dispatch_v2.py），
或直接调用 parse_arguments()（更稳定、更快）。
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

# 添加 scripts 目录到 sys.path
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from cli.parser import _validate_drop_in_dir, parse_arguments  # noqa: E402
from facade import (  # noqa: E402
    _start_hot_reload_if_enabled,
    _safe_watcher_stop,
)


class TestValidateDropInDir(unittest.TestCase):
    """P0-7 第一层：CLI type 校验器（_validate_drop_in_dir）。"""

    def test_relative_path_accepted(self):
        """合法相对路径 → 返回原字符串。"""
        self.assertEqual(_validate_drop_in_dir("plugins_extra"), "plugins_extra")
        self.assertEqual(
            _validate_drop_in_dir("./sub/plugins"), "./sub/plugins"
        )

    def test_absolute_path_rejected(self):
        """绝对路径 → 抛 argparse.ArgumentTypeError（argparse 会自动转 SystemExit）。"""
        import argparse
        with self.assertRaises(argparse.ArgumentTypeError):
            _validate_drop_in_dir("/etc/passwd")

    def test_dotdot_path_rejected(self):
        """包含 '..' 的相对路径 → 抛 argparse.ArgumentTypeError。"""
        import argparse
        with self.assertRaises(argparse.ArgumentTypeError):
            _validate_drop_in_dir("../../etc")

    def test_nested_path_accepted(self):
        """嵌套相对路径（不含 ..）→ 接受。"""
        self.assertEqual(
            _validate_drop_in_dir("a/b/c"), "a/b/c"
        )


class TestParseArgumentsHotReload(unittest.TestCase):
    """parse_arguments() 中 hot-reload 相关 flag。"""

    def test_default_hot_reload_is_true(self):
        """不传任何 hot-reload flag → args.hot_reload == True（默认开启）。"""
        # parse_arguments 依赖 sys.argv，临时替换
        old_argv = sys.argv
        try:
            sys.argv = ["prog", "--project-root", "/tmp"]
            args = parse_arguments()
            self.assertTrue(args.hot_reload)
        finally:
            sys.argv = old_argv

    def test_no_hot_reload_flag_disables(self):
        """--no-hot-reload → args.hot_reload == False。"""
        old_argv = sys.argv
        try:
            sys.argv = [
                "prog", "--project-root", "/tmp", "--no-hot-reload"
            ]
            args = parse_arguments()
            self.assertFalse(args.hot_reload)
        finally:
            sys.argv = old_argv

    def test_hot_reload_flag_enables(self):
        """--hot-reload → args.hot_reload == True。"""
        old_argv = sys.argv
        try:
            sys.argv = [
                "prog", "--project-root", "/tmp", "--hot-reload"
            ]
            args = parse_arguments()
            self.assertTrue(args.hot_reload)
        finally:
            sys.argv = old_argv

    def test_both_flags_mutually_exclusive(self):
        """--hot-reload --no-hot-reload → argparse SystemExit（互斥）。"""
        old_argv = sys.argv
        try:
            sys.argv = [
                "prog", "--project-root", "/tmp",
                "--hot-reload", "--no-hot-reload",
            ]
            with self.assertRaises(SystemExit):
                parse_arguments()
        finally:
            sys.argv = old_argv

    def test_hot_reload_dir_default(self):
        """--hot-reload-dir 默认值 = plugins_extra。"""
        old_argv = sys.argv
        try:
            sys.argv = ["prog", "--project-root", "/tmp"]
            args = parse_arguments()
            self.assertEqual(args.hot_reload_dir, "plugins_extra")
        finally:
            sys.argv = old_argv

    def test_hot_reload_dir_custom(self):
        """--hot-reload-dir <path> → 接受合法路径。"""
        old_argv = sys.argv
        try:
            sys.argv = [
                "prog", "--project-root", "/tmp",
                "--hot-reload-dir", "custom/extra",
            ]
            args = parse_arguments()
            self.assertEqual(args.hot_reload_dir, "custom/extra")
        finally:
            sys.argv = old_argv

    def test_hot_reload_interval_default(self):
        """--hot-reload-interval 默认值 = 5.0。"""
        old_argv = sys.argv
        try:
            sys.argv = ["prog", "--project-root", "/tmp"]
            args = parse_arguments()
            self.assertEqual(args.hot_reload_interval, 5.0)
        finally:
            sys.argv = old_argv


class TestFacadeStartHotReload(unittest.TestCase):
    """facade._start_hot_reload_if_enabled 集成测试。"""

    def setUp(self) -> None:
        """构造临时 project_root + dispatcher。"""
        import tempfile
        import shutil
        from dispatcher.goal_dispatcher import GoalDispatcher

        self._tmp = Path(tempfile.mkdtemp(prefix="facade_test_"))
        self.project_root = self._tmp / "project"
        self.project_root.mkdir()
        # 预创建 drop-in 目录
        (self.project_root / "plugins_extra").mkdir()
        self.dispatcher = GoalDispatcher()

    def tearDown(self) -> None:
        """清理。"""
        import shutil
        # 清理 sys.modules 注入
        for key in list(sys.modules.keys()):
            if key.startswith("plugins_extra."):
                del sys.modules[key]
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_args(self, hot_reload=True, drop_in_dir="plugins_extra"):
        """构造模拟 args（仅含本测试关心的字段）。"""

        class _Args:
            pass

        args = _Args()
        args.hot_reload = hot_reload
        args.hot_reload_dir = drop_in_dir
        args.hot_reload_interval = 5.0
        return args

    def test_disabled_returns_none(self):
        """--no-hot-reload → 返回 None（不启动 watcher）。"""
        args = self._make_args(hot_reload=False)
        result = _start_hot_reload_if_enabled(
            self.dispatcher, args, self.project_root
        )
        self.assertIsNone(result)

    def test_enabled_starts_watcher(self):
        """--hot-reload → 返回 watcher 实例。"""
        args = self._make_args(hot_reload=True)
        watcher = _start_hot_reload_if_enabled(
            self.dispatcher, args, self.project_root
        )
        try:
            self.assertIsNotNone(watcher)
            self.assertTrue(watcher._initial_scan_done.is_set())
        finally:
            if watcher is not None:
                _safe_watcher_stop(watcher)

    def test_watcher_failure_does_not_break_facade(self):
        """watcher 构造异常 → facade 仍返回 None（不阻断 main）。"""
        # 用一个绝对路径触发 DropInPathError
        # 注意：CLI 第一层会拦截绝对路径，但 facade 是直接传 args（不经过 CLI），
        # 所以 facade 应处理异常（生产友好）
        args = self._make_args(drop_in_dir="/etc/passwd")
        watcher = _start_hot_reload_if_enabled(
            self.dispatcher, args, self.project_root
        )
        # 期望：返回 None（异常被隔离，不向上传播）
        self.assertIsNone(watcher)


if __name__ == "__main__":
    unittest.main()
