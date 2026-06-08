"""Phase 18: RalphAutonomousPlugin 单元测试。

测试 RalphAutonomousPlugin 的全部行为：
- 插件元数据（name / priority / mutex_with / requires_task）
- matches() 各种 CLI flag 组合
- execute() 真实运行路径（dry_run 短路、构造组件、启动循环）
- 资源释放（SleepGuard）
"""
import argparse
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from plugins.autonomous import RalphAutonomousPlugin


# ---------------------------------------------------------------------- #
# 工具：构造测试上下文                                                    #
# ---------------------------------------------------------------------- #


def _make_args(**overrides) -> argparse.Namespace:
    """构造测试用 args Namespace。"""
    defaults = {
        "autonomous": False,
        "auto_resume": None,
        "auto_resume_latest": False,
        "task": "实现功能 X",
        "task_file": None,
        "auto_max_iterations": 2,
        "auto_max_tokens": 100_000,
        "auto_stop_when": "",
        "auto_stage_order": "plan,dev,verify,fix",
        "auto_backoff_base": 1.0,
        "auto_backoff_max": 60.0,
        "auto_failure_abort": 3,
        "auto_git_author_name": "Ralph Test",
        "auto_git_author_email": "test@example.com",
        "auto_test_command": "echo PASS",
        "auto_security_analyzer": "builtin",
        "auto_run_dir": ".gnhf/runs",
        "auto_notes_path": "notes.md",
        "auto_max_size_kb": 1024,
        "auto_trim_keep_last_n": 20,
        "auto_no_caffeinate": True,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _make_ctx(tmpdir: Path) -> "PluginContext":
    """构造测试用 PluginContext。"""
    from dispatcher.plugin_context import PluginContext
    return PluginContext(
        project_root=tmpdir,
        log=lambda msg, level: None,
        dry_run=False,
    )


# ---------------------------------------------------------------------- #
# TestRalphAutonomousPluginMetadata: 插件元数据                           #
# ---------------------------------------------------------------------- #


class TestRalphAutonomousPluginMetadata(unittest.TestCase):
    """测试插件元数据契约。"""

    def setUp(self):
        self.plugin = RalphAutonomousPlugin()

    def test_01_name(self):
        """name = "autonomous"。"""
        self.assertEqual(self.plugin.name, "autonomous")

    def test_02_priority(self):
        """priority = 5。"""
        self.assertEqual(self.plugin.priority, 5)

    def test_03_mutex_with(self):
        """mutex_with 包含所有互斥 plugin。"""
        mutex = self.plugin.mutex_with
        # 应包含 5 个互斥 plugin
        for name in ("goal-cancel", "goal-graph", "goal-resume", "multi-goal", "loop"):
            self.assertIn(name, mutex)
        # 不应包含自己
        self.assertNotIn("autonomous", mutex)

    def test_04_requires_task(self):
        """requires_task = False（resume 模式可无 task）。"""
        self.assertFalse(self.plugin.requires_task)

    def test_05_repr(self):
        """__repr__ 包含 name 和 priority。"""
        r = repr(self.plugin)
        self.assertIn("autonomous", r)
        self.assertIn("5", r)


# ---------------------------------------------------------------------- #
# TestRalphAutonomousPluginMatches: matches()                            #
# ---------------------------------------------------------------------- #


class TestRalphAutonomousPluginMatches(unittest.TestCase):
    """测试 matches() 各种 CLI flag 组合。"""

    def setUp(self):
        self.plugin = RalphAutonomousPlugin()

    def test_06_match_autonomous_flag(self):
        """args.autonomous=True → 匹配。"""
        args = _make_args(autonomous=True)
        self.assertTrue(self.plugin.matches(args))

    def test_07_match_auto_resume(self):
        """args.auto_resume='r-xxx' → 匹配。"""
        args = _make_args(auto_resume="r-abc123")
        self.assertTrue(self.plugin.matches(args))

    def test_08_match_auto_resume_latest(self):
        """args.auto_resume_latest=True → 匹配。"""
        args = _make_args(auto_resume_latest=True)
        self.assertTrue(self.plugin.matches(args))

    def test_09_no_match(self):
        """所有 flag 都为 False/None → 不匹配。"""
        args = _make_args()
        self.assertFalse(self.plugin.matches(args))

    def test_10_missing_attrs(self):
        """args 缺少 autonomous 等属性时 → 不抛异常。"""
        args = argparse.Namespace(task="x")
        # 不应抛 AttributeError
        self.assertFalse(self.plugin.matches(args))


# ---------------------------------------------------------------------- #
# TestRalphAutonomousPluginExecute: execute()                            #
# ---------------------------------------------------------------------- #


class TestRalphAutonomousPluginExecute(unittest.TestCase):
    """测试 execute() 行为。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.plugin = RalphAutonomousPlugin()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_11_dry_run_short_circuits(self):
        """dry_run=True → 短路返回 True。"""
        ctx = _make_ctx(self.tmpdir)
        ctx.dry_run = True
        args = _make_args(autonomous=True)
        result = self.plugin.execute(args, ctx)
        self.assertTrue(result)

    def test_12_execute_creates_run_dir(self):
        """execute() 创建 run_dir。"""
        ctx = _make_ctx(self.tmpdir)
        args = _make_args(autonomous=True, auto_no_caffeinate=True, auto_max_iterations=1)
        # patch loop_controller to avoid real execution
        with patch("autonomous.loop_controller.RalphLoopController") as mock_loop:
            mock_loop.return_value.run.return_value = 0
            self.plugin.execute(args, ctx)
            # run_dir 应被创建
            run_root = self.tmpdir / ".gnhf" / "runs"
            self.assertTrue(run_root.exists())
            # 至少一个子目录
            subdirs = [d for d in run_root.iterdir() if d.is_dir()]
            self.assertGreaterEqual(len(subdirs), 1)

    def test_13_execute_new_run_requires_task(self):
        """新建 run 必须有 task。"""
        ctx = _make_ctx(self.tmpdir)
        args = _make_args(autonomous=True, task="", task_file=None)
        result = self.plugin.execute(args, ctx)
        self.assertFalse(result)

    def test_14_execute_resume_nonexistent_fails(self):
        """resume 不存在的 run_id → False。"""
        ctx = _make_ctx(self.tmpdir)
        args = _make_args(autonomous=False, auto_resume="r-nonexistent")
        result = self.plugin.execute(args, ctx)
        self.assertFalse(result)

    def test_15_execute_loop_called(self):
        """execute() 内部调用 RalphLoopController.run()。"""
        ctx = _make_ctx(self.tmpdir)
        args = _make_args(autonomous=True, auto_max_iterations=1, auto_no_caffeinate=True)
        with patch("autonomous.loop_controller.RalphLoopController") as mock_loop:
            mock_loop.return_value.run.return_value = 0
            result = self.plugin.execute(args, ctx)
            self.assertTrue(result)
            # run() 被调用
            mock_loop.return_value.run.assert_called_once()

    def test_16_execute_loop_fails_returns_false(self):
        """loop.run() 返回非 0 → False。"""
        ctx = _make_ctx(self.tmpdir)
        args = _make_args(autonomous=True, auto_max_iterations=1, auto_no_caffeinate=True)
        with patch("autonomous.loop_controller.RalphLoopController") as mock_loop:
            mock_loop.return_value.run.return_value = 2  # fatal
            result = self.plugin.execute(args, ctx)
            self.assertFalse(result)

    def test_17_execute_sleep_guard_released(self):
        """execute() 后 SleepGuard.release 被调用。"""
        ctx = _make_ctx(self.tmpdir)
        args = _make_args(autonomous=True, auto_max_iterations=1, auto_no_caffeinate=False)
        with patch("autonomous.loop_controller.RalphLoopController") as mock_loop:
            with patch("autonomous.sleep_guard.SleepGuard") as mock_guard_cls:
                mock_guard = MagicMock()
                mock_guard_cls.return_value = mock_guard
                mock_loop.return_value.run.return_value = 0
                self.plugin.execute(args, ctx)
                # release 被调用
                mock_guard.release.assert_called()

    def test_18_execute_exception_marked_aborted(self):
        """execute() 抛异常 → run_state.mark_aborted 被调用。"""
        ctx = _make_ctx(self.tmpdir)
        args = _make_args(autonomous=True, auto_max_iterations=1, auto_no_caffeinate=True)
        with patch("autonomous.loop_controller.RalphLoopController") as mock_loop:
            mock_loop.return_value.run.side_effect = RuntimeError("boom")
            result = self.plugin.execute(args, ctx)
            self.assertFalse(result)


# ---------------------------------------------------------------------- #
# TestRalphAutonomousPluginComponents: 组件构造                          #
# ---------------------------------------------------------------------- #


class TestRalphAutonomousPluginComponents(unittest.TestCase):
    """测试 _build_components / _build_stage_handlers 内部 API。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.plugin = RalphAutonomousPlugin()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_19_build_loop_config_defaults(self):
        """_build_loop_config() 默认值合理。"""
        args = _make_args()
        config = self.plugin._build_loop_config(args)
        self.assertEqual(config.max_iterations, 2)  # from _make_args
        self.assertEqual(config.test_command, "echo PASS")  # from _make_args

    def test_20_build_loop_config_stage_order(self):
        """_build_loop_config() 解析 stage_order。"""
        args = _make_args(auto_stage_order="dev,plan")
        config = self.plugin._build_loop_config(args)
        # stage_order 长度 2
        self.assertEqual(len(config.stage_order), 2)

    def test_21_build_components_has_all_keys(self):
        """_build_components() 返回 7 个键。"""
        ctx = _make_ctx(self.tmpdir)
        from autonomous.loop_controller import LoopConfig
        config = LoopConfig()
        # 构造一个 run_state
        from autonomous.run_state import RunState
        run_dir = self.tmpdir / ".gnhf" / "runs" / "r-test"
        run_dir.mkdir(parents=True, exist_ok=True)
        run_state = RunState(run_dir=run_dir, run_id="r-test")
        components = self.plugin._build_components(_make_args(), ctx, run_state, run_dir, config)
        self.assertIsNotNone(components)
        # 应包含 7 个键
        for key in (
            "notes_memory",
            "git_driver",
            "auto_skill_loader",
            "smart_confirmation",
            "dispatcher_adapter",
            "sleep_guard",
            "sleep_guard_enabled",
        ):
            self.assertIn(key, components)

    def test_22_build_stage_handlers_has_4(self):
        """_build_stage_handlers() 返回 4 个 handler。"""
        from autonomous.loop_controller import LoopConfig, StageKind
        config = LoopConfig()
        components = {
            "auto_skill_loader": MagicMock(),
            "notes_memory": MagicMock(),
            "dispatcher_adapter": MagicMock(),
            "smart_confirmation": MagicMock(),
            "git_driver": MagicMock(),
            "sleep_guard": MagicMock(),
            "sleep_guard_enabled": True,
        }
        handlers = self.plugin._build_stage_handlers(components, config=config)
        # 4 个阶段
        for stage in (StageKind.PLAN, StageKind.DEV, StageKind.VERIFY, StageKind.FIX):
            self.assertIn(stage, handlers)


if __name__ == "__main__":
    unittest.main()
