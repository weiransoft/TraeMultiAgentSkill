"""LoopEngineeringPlugin 单元测试。

测试目标：
- 插件满足 V3 契约：name / priority / mutex_with / requires_task / matches / execute。
- matches() 仅在 --loop-engineering 时返回 True。
- execute() dry_run 短路返回 True。
- execute() 缺少 task 时返回 False。
- execute() 根据 LoopKernel 运行报告的最终状态返回 bool。
- mutex_with 与 autonomous / loop 对称。

本测试对 LoopKernel.run() 使用 patch，因为 plugin 单元测试关注插件编排逻辑，
真实 Kernel 运行路径由 integration / E2E 测试覆盖。
"""

import argparse
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dispatcher.plugin_context import PluginContext
from plugins.autonomous import RalphAutonomousPlugin
from plugins.loop import LoopGoalPlugin
from plugins.loop_engineering import LoopEngineeringPlugin


# ---------------------------------------------------------------------- #
# 工具函数                                                               #
# ---------------------------------------------------------------------- #


def _make_args(**overrides) -> argparse.Namespace:
    """构造测试用 args Namespace。

    字段与 cli/parser.py 中 --loop-engineering 相关 flag 保持一致。
    """
    defaults = {
        "loop_engineering": True,
        "loop_type": "coding",
        "loop_discovery": "auto",
        "loop_evaluator": "strict",
        "loop_human_checkpoint_every": 5,
        "loop_max_iterations": 2,
        "loop_max_tokens": 100_000,
        "loop_sampling_read_ratio": 0.1,
        "loop_stop_when": "",
        "task": "实现一个无依赖的 hello 函数",
        "task_file": None,
        "project_root": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _make_ctx(tmpdir: Path) -> PluginContext:
    """构造测试用 PluginContext。"""
    return PluginContext(
        project_root=tmpdir,
        log=lambda msg, level: None,
        dry_run=False,
    )


# ---------------------------------------------------------------------- #
# TestLoopEngineeringPluginMetadata: 插件元数据                          #
# ---------------------------------------------------------------------- #


class TestLoopEngineeringPluginMetadata(unittest.TestCase):
    """测试插件元数据契约。"""

    def setUp(self):
        self.plugin = LoopEngineeringPlugin()

    def test_01_name(self):
        """name = 'loop-engineering'。"""
        self.assertEqual(self.plugin.name, "loop-engineering")

    def test_02_priority(self):
        """priority = 42。"""
        self.assertEqual(self.plugin.priority, 42)

    def test_03_requires_task(self):
        """新建 run 必须提供 task。"""
        self.assertTrue(self.plugin.requires_task)

    def test_04_mutex_with_contains_autonomous_and_loop(self):
        """mutex_with 包含 autonomous 与 loop。"""
        mutex = self.plugin.mutex_with
        self.assertIn("autonomous", mutex)
        self.assertIn("loop", mutex)

    def test_05_mutex_with_no_self_reference(self):
        """mutex_with 不包含自身。"""
        self.assertNotIn("loop-engineering", self.plugin.mutex_with)

    def test_06_repr(self):
        """__repr__ 包含 name 与 priority。"""
        r = repr(self.plugin)
        self.assertIn("loop-engineering", r)
        self.assertIn("42", r)


# ---------------------------------------------------------------------- #
# TestLoopEngineeringPluginMatches: matches()                            #
# ---------------------------------------------------------------------- #


class TestLoopEngineeringPluginMatches(unittest.TestCase):
    """测试 matches() 行为。"""

    def setUp(self):
        self.plugin = LoopEngineeringPlugin()

    def test_07_match_loop_engineering_flag(self):
        """args.loop_engineering=True → 匹配。"""
        args = _make_args()
        self.assertTrue(self.plugin.matches(args))

    def test_08_no_match_when_flag_false(self):
        """args.loop_engineering=False → 不匹配。"""
        args = _make_args(loop_engineering=False)
        self.assertFalse(self.plugin.matches(args))

    def test_09_no_match_when_flag_missing(self):
        """args 缺少 loop_engineering 属性 → 不匹配且不抛异常。"""
        args = argparse.Namespace(task="x")
        self.assertFalse(self.plugin.matches(args))


# ---------------------------------------------------------------------- #
# TestLoopEngineeringPluginMutex: 互斥对称性                              #
# ---------------------------------------------------------------------- #


class TestLoopEngineeringPluginMutex(unittest.TestCase):
    """验证 Loop Engineering 与 autonomous / loop 的互斥关系对称。"""

    def test_10_autonomous_mutex_loop_engineering(self):
        """autonomous plugin 也声明与 loop-engineering 互斥。"""
        autonomous = RalphAutonomousPlugin()
        self.assertIn("loop-engineering", autonomous.mutex_with)

    def test_11_loop_mutex_loop_engineering(self):
        """loop plugin 也声明与 loop-engineering 互斥。"""
        loop = LoopGoalPlugin()
        self.assertIn("loop-engineering", loop.mutex_with)


# ---------------------------------------------------------------------- #
# TestLoopEngineeringPluginExecute: execute()                            #
# ---------------------------------------------------------------------- #


class TestLoopEngineeringPluginExecute(unittest.TestCase):
    """测试 execute() 行为。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.plugin = LoopEngineeringPlugin()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_12_dry_run_short_circuits(self):
        """dry_run=True → 短路返回 True。"""
        ctx = _make_ctx(self.tmpdir)
        ctx.dry_run = True
        args = _make_args()
        result = self.plugin.execute(args, ctx)
        self.assertTrue(result)

    def test_13_execute_missing_task_returns_false(self):
        """task 与 task_file 都未提供 → 返回 False。"""
        ctx = _make_ctx(self.tmpdir)
        args = _make_args(task="", task_file=None)
        result = self.plugin.execute(args, ctx)
        self.assertFalse(result)

    def test_14_execute_task_file_provides_objective(self):
        """提供 task_file 时从文件读取 objective。"""
        task_file = self.tmpdir / "task.txt"
        task_file.write_text("从文件读取的目标", encoding="utf-8")

        ctx = _make_ctx(self.tmpdir)
        args = _make_args(task="", task_file="task.txt")

        report = MagicMock()
        report.final_status = "completed"
        report.run_id = "le-test123"
        report.total_iterations = 1
        report.duration_sec = 1.0

        with patch("plugins.loop_engineering.LoopKernel") as mock_kernel_cls:
            mock_kernel_cls.return_value.run.return_value = report
            result = self.plugin.execute(args, ctx)

        self.assertTrue(result)

    def test_15_execute_completed_returns_true(self):
        """LoopKernel 报告 completed → execute 返回 True。"""
        ctx = _make_ctx(self.tmpdir)
        args = _make_args()

        report = MagicMock()
        report.final_status = "completed"
        report.run_id = "le-test123"
        report.total_iterations = 1
        report.duration_sec = 1.0

        with patch("plugins.loop_engineering.LoopKernel") as mock_kernel_cls:
            mock_kernel_cls.return_value.run.return_value = report
            result = self.plugin.execute(args, ctx)

        self.assertTrue(result)
        mock_kernel_cls.return_value.run.assert_called_once_with(args.task)

    def test_16_execute_failed_returns_false(self):
        """LoopKernel 报告 failed → execute 返回 False。"""
        ctx = _make_ctx(self.tmpdir)
        args = _make_args()

        report = MagicMock()
        report.final_status = "failed"
        report.run_id = "le-test123"
        report.total_iterations = 1
        report.duration_sec = 1.0

        with patch("plugins.loop_engineering.LoopKernel") as mock_kernel_cls:
            mock_kernel_cls.return_value.run.return_value = report
            result = self.plugin.execute(args, ctx)

        self.assertFalse(result)

    def test_17_execute_aborted_returns_false(self):
        """LoopKernel 报告 aborted → execute 返回 False。"""
        ctx = _make_ctx(self.tmpdir)
        args = _make_args()

        report = MagicMock()
        report.final_status = "aborted"
        report.run_id = "le-test123"
        report.total_iterations = 1
        report.duration_sec = 1.0

        with patch("plugins.loop_engineering.LoopKernel") as mock_kernel_cls:
            mock_kernel_cls.return_value.run.return_value = report
            result = self.plugin.execute(args, ctx)

        self.assertFalse(result)

    def test_18_execute_exception_returns_false(self):
        """LoopKernel.run() 抛异常 → execute 返回 False。"""
        ctx = _make_ctx(self.tmpdir)
        args = _make_args()

        with patch("plugins.loop_engineering.LoopKernel") as mock_kernel_cls:
            mock_kernel_cls.return_value.run.side_effect = RuntimeError("boom")
            result = self.plugin.execute(args, ctx)

        self.assertFalse(result)

    def test_19_execute_creates_run_dir(self):
        """execute() 在 project_root/.gnhf/runs/<run_id>/ 创建 run_dir。"""
        ctx = _make_ctx(self.tmpdir)
        args = _make_args()

        report = MagicMock()
        report.final_status = "completed"
        report.run_id = "le-test123"
        report.total_iterations = 1
        report.duration_sec = 1.0

        with patch("plugins.loop_engineering.LoopKernel") as mock_kernel_cls:
            mock_kernel_cls.return_value.run.return_value = report
            self.plugin.execute(args, ctx)

        run_root = self.tmpdir / ".gnhf" / "runs"
        self.assertTrue(run_root.exists())
        subdirs = [d for d in run_root.iterdir() if d.is_dir()]
        self.assertGreaterEqual(len(subdirs), 1)


if __name__ == "__main__":
    unittest.main()
