"""V3 集成测试：19 个 compat points + 端到端 dispatch。"""
import sys
import unittest
import argparse
import subprocess
from pathlib import Path

SCRIPTS_DIR = str(Path(__file__).parent.parent)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


class TestBackwardCompat(unittest.TestCase):
    """19 个 compat points 验证。"""

    def test_01_goal_cancel_cli(self):
        # 模拟 god module 行为：plugin 通过 facade 走到 dispatch.legacy
        from facade import main_compat
        self.assertTrue(callable(main_compat))

    def test_02_dry_run_via_ctx_field(self):
        # 风险-5 验证：dry_run 字段在 dispatcher 入口短路
        from dispatcher.plugin_context import PluginContext
        from dispatcher.dispatch_result import DispatchResult
        from dispatcher.goal_dispatcher import GoalDispatcher
        from plugins import BUILTIN_PLUGINS

        ctx = PluginContext(project_root=Path("/tmp"), log=lambda m, l="INFO": None, dry_run=True)
        d = GoalDispatcher(plugins=list(BUILTIN_PLUGINS))
        result = d.dispatch(argparse.Namespace(), ctx)
        self.assertEqual(result.skipped_reason, "dry_run")
        self.assertTrue(result.success)

    def test_03_thin_shell_imports(self):
        # 11 个符号全部可从薄壳 import
        import trae_agent_dispatch_v2
        for name in [
            "log", "dispatch_agent_v2", "dispatch_agent",
            "dispatch_agent_v2_with_loop_goal", "dispatch_agent_v2_with_goal_resume",
            "dispatch_agent_v2_with_multi_goal", "dispatch_agent_v2_with_goal_cancel",
            "dispatch_agent_v2_with_goal_graph", "_is_overall_success",
            "_module_level_single_dispatch", "parse_arguments",
        ]:
            self.assertTrue(hasattr(trae_agent_dispatch_v2, name), f"Missing {name}")

    def test_04_thin_shell_as_alias(self):
        import trae_agent_dispatch_v2 as v2
        self.assertTrue(callable(v2.main))

    def test_05_facade_re_exports_all_11_symbols(self):
        # B-2 修复：facade 完整 re-export 11 个符号
        import facade
        for name in [
            "main_compat", "log", "dispatch_agent_v2", "dispatch_agent",
            "dispatch_agent_v2_with_loop_goal", "dispatch_agent_v2_with_goal_resume",
            "dispatch_agent_v2_with_multi_goal", "dispatch_agent_v2_with_goal_cancel",
            "dispatch_agent_v2_with_goal_graph", "_is_overall_success",
            "_module_level_single_dispatch", "parse_arguments",
        ]:
            self.assertTrue(hasattr(facade, name), f"facade missing {name}")

    def test_06_no_circular_import(self):
        # 风险-6 验证：薄壳 import 不循环
        import trae_agent_dispatch_v2
        import facade
        import dispatch.legacy
        from plugins import BUILTIN_PLUGINS
        self.assertEqual(len(BUILTIN_PLUGINS), 5)

    def test_07_dispatch_legacy_no_reverse_import(self):
        # 风险-6 验证：dispatch.legacy 不反向 import facade / 薄壳
        result = subprocess.run(
            ["grep", "-E", "import facade$|from facade|import trae_agent_dispatch_v2",
             f"{SCRIPTS_DIR}/dispatch/legacy.py"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1, f"dispatch.legacy 包含反向 import：{result.stdout}")

    def test_08_plugin_no_thin_shell_import(self):
        # 风险-6 验证：plugin 不 import 薄壳（递归 grep plugins/ 目录）
        result = subprocess.run(
            ["grep", "-rE", "import trae_agent_dispatch_v2",
             f"{SCRIPTS_DIR}/plugins/"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1, f"plugins 包含反向 import 薄壳：{result.stdout}")

    def test_09_facade_dispatches_through_v3(self):
        # 验证 _dispatch_through_v3 可调用（main_compat 入口）
        from facade import _dispatch_through_v3
        self.assertTrue(callable(_dispatch_through_v3))

    def test_10_thin_shell_under_50_lines(self):
        # T18 验证：薄壳 < 50 行
        thin_shell = Path(f"{SCRIPTS_DIR}/trae_agent_dispatch_v2.py")
        line_count = sum(1 for _ in thin_shell.open())
        self.assertLess(line_count, 50, f"薄壳 {line_count} 行超过 50 行限制")

    def test_11_dispatcher_5_builtins(self):
        from dispatcher.goal_dispatcher import GoalDispatcher
        from plugins import BUILTIN_PLUGINS
        d = GoalDispatcher(plugins=list(BUILTIN_PLUGINS))
        names = [p.name for p in d.list_plugins()]
        self.assertEqual(len(names), 5)
        self.assertIn("goal-cancel", names)
        self.assertIn("goal-graph", names)
        self.assertIn("goal-resume", names)
        self.assertIn("multi-goal", names)
        self.assertIn("loop", names)

    def test_12_dispatcher_priority_order(self):
        # priority 升序：cancel(0) < graph(10) < resume(20) < multi-goal(30) < loop(40)
        from dispatcher.goal_dispatcher import GoalDispatcher
        from plugins import BUILTIN_PLUGINS
        d = GoalDispatcher(plugins=list(BUILTIN_PLUGINS))
        names = [p.name for p in d.list_plugins()]
        self.assertEqual(names, ["goal-cancel", "goal-graph", "goal-resume", "multi-goal", "loop"])

    def test_13_dispatch_result_bool_semantics(self):
        from dispatcher.dispatch_result import DispatchResult
        # success=True → True
        self.assertTrue(bool(DispatchResult(success=True)))
        # success=False → False
        self.assertFalse(bool(DispatchResult(success=False)))
        # success=True, dry_run → True
        self.assertTrue(bool(DispatchResult(success=True, skipped_reason="dry_run")))

    def test_14_plugin_context_dry_run(self):
        from dispatcher.plugin_context import PluginContext
        ctx = PluginContext(project_root="/tmp", log=lambda m, l="INFO": None, dry_run=True)
        self.assertTrue(ctx.dry_run)

    def test_15_cli_parser_imports(self):
        from cli.parser import parse_arguments
        self.assertTrue(callable(parse_arguments))


class TestEndToEndDispatch(unittest.TestCase):
    """5 模式端到端 dispatch（mock dispatch.legacy）。"""

    def _make_args(self, **overrides):
        """构造 args Namespace，缺省字段填充 V3 plugin 期望的所有字段。

        Phase 17 v3 P1-5 修复：必须显式设置 hot_reload / hot_reload_dir /
        hot_reload_interval，否则 facade.assert 兜底会失败。
        """
        defaults = {
            "project_root": "/tmp",
            "agent": "auto",
            "task": "test-task",
            "task_file": None,
            "verbose": False,
            "dry_run": False,
            "goal_graph": None,
            "goal_cancel": None,
            "goal_resume": None,
            "multi_goal": None,
            "loop": 1,
            "goal": None,
            "goal_desc": None,
            "criteria": None,
            "convergence_window": 3,
            "goal_graph_format": "mermaid",
            "goal_graph_output": None,
            "goal_graph_desc_max": 100,
            # Phase 17 v3 新增：热加载相关字段（V3 测试场景使用 --no-hot-reload
            # 避免启动 watcher 增加测试耗时；路径校验与 P0-7 一致）
            "hot_reload": False,
            "hot_reload_dir": "plugins_extra",
            "hot_reload_interval": 5.0,
        }
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_cancel_end_to_end(self):
        from facade import _dispatch_through_v3
        from unittest.mock import patch
        args = self._make_args(goal_cancel="g1", task=None)
        with patch("dispatch.legacy.dispatch_agent_v2_with_goal_cancel", return_value=True) as mock_cancel:
            rc = _dispatch_through_v3(args)
            self.assertEqual(rc, 0)
            mock_cancel.assert_called_once()

    def test_graph_end_to_end(self):
        from facade import _dispatch_through_v3
        from unittest.mock import patch
        args = self._make_args(goal_graph="g1", task=None)
        with patch("dispatch.legacy.dispatch_agent_v2_with_goal_graph", return_value=True) as mock_graph:
            rc = _dispatch_through_v3(args)
            self.assertEqual(rc, 0)
            mock_graph.assert_called_once()

    def test_resume_end_to_end(self):
        from facade import _dispatch_through_v3
        from unittest.mock import patch
        args = self._make_args(goal_resume="g1", task=None)
        with patch("dispatch.legacy.dispatch_agent_v2_with_goal_resume", return_value=True) as mock_resume:
            rc = _dispatch_through_v3(args)
            self.assertEqual(rc, 0)
            mock_resume.assert_called_once()

    def test_multi_goal_end_to_end(self):
        from facade import _dispatch_through_v3
        from unittest.mock import patch
        args = self._make_args(multi_goal="g1", task=None)
        with patch("dispatch.legacy.dispatch_agent_v2_with_multi_goal", return_value=True) as mock_mg:
            rc = _dispatch_through_v3(args)
            self.assertEqual(rc, 0)
            mock_mg.assert_called_once()

    def test_loop_end_to_end(self):
        from facade import _dispatch_through_v3
        from unittest.mock import patch
        args = self._make_args(loop=3, task="t", goal="g1", goal_desc="d")
        with patch("dispatch.legacy.dispatch_agent_v2_with_loop_goal", return_value=True) as mock_loop:
            rc = _dispatch_through_v3(args)
            self.assertEqual(rc, 0)
            mock_loop.assert_called_once()

    def test_dry_run_end_to_end(self):
        from facade import _dispatch_through_v3
        args = self._make_args(dry_run=True)
        rc = _dispatch_through_v3(args)
        self.assertEqual(rc, 0)  # dry_run 路径总是成功

    def test_task_required_no_plugin_returns_error(self):
        from facade import _dispatch_through_v3
        args = self._make_args(task="")  # task 空 + 无 plugin 模式 → 必填错误
        rc = _dispatch_through_v3(args)
        self.assertEqual(rc, 1)  # 必填校验失败

    def test_project_root_not_exists(self):
        from facade import _dispatch_through_v3
        args = self._make_args(project_root="/nonexistent/path/12345")
        rc = _dispatch_through_v3(args)
        self.assertEqual(rc, 1)  # 项目根目录不存在
