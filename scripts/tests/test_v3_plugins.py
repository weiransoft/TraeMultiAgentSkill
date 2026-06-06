"""5 个内置 plugin 单元测试。"""
import unittest
import argparse
from pathlib import Path
from unittest.mock import patch
from plugins.cancel import GoalCancelPlugin
from plugins.graph import GoalGraphPlugin
from plugins.resume import GoalResumePlugin
from plugins.multi_goal import MultiGoalPlugin
from plugins.loop import LoopGoalPlugin


def noop_log(message, level="INFO"): pass


def make_ctx():
    from dispatcher.plugin_context import PluginContext
    return PluginContext(project_root=Path("/tmp"), log=noop_log)


def make_args(**kwargs):
    args = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(args, k, v)
    return args


class TestGoalCancelPlugin(unittest.TestCase):
    def setUp(self):
        self.plugin = GoalCancelPlugin()

    def test_name(self):
        self.assertEqual(self.plugin.name, "goal-cancel")

    def test_priority(self):
        self.assertEqual(self.plugin.priority, 0)

    def test_mutex_with(self):
        self.assertEqual(
            self.plugin.mutex_with,
            {"goal-graph", "goal-resume", "multi-goal", "loop"},
        )

    def test_requires_task(self):
        self.assertFalse(self.plugin.requires_task)

    def test_matches_with_cancel(self):
        self.assertTrue(self.plugin.matches(make_args(goal_cancel="g1")))

    def test_matches_without_cancel(self):
        self.assertFalse(self.plugin.matches(make_args(goal_cancel=None)))

    @patch("dispatch.legacy.dispatch_agent_v2_with_goal_cancel", return_value=True)
    def test_execute_calls_legacy_function(self, mock_legacy):
        result = self.plugin.execute(make_args(goal_cancel="g1"), make_ctx())
        self.assertTrue(result)
        mock_legacy.assert_called_once()


class TestGoalGraphPlugin(unittest.TestCase):
    def setUp(self):
        self.plugin = GoalGraphPlugin()

    def test_name(self):
        self.assertEqual(self.plugin.name, "goal-graph")

    def test_priority(self):
        self.assertEqual(self.plugin.priority, 10)

    def test_mutex_with(self):
        self.assertEqual(
            self.plugin.mutex_with,
            {"goal-cancel", "goal-resume", "multi-goal", "loop"},
        )

    def test_requires_task(self):
        self.assertFalse(self.plugin.requires_task)

    def test_matches_with_graph(self):
        self.assertTrue(self.plugin.matches(make_args(goal_graph="g1")))

    def test_matches_without_graph(self):
        self.assertFalse(self.plugin.matches(make_args(goal_graph=None)))

    @patch("dispatch.legacy.dispatch_agent_v2_with_goal_graph", return_value=True)
    def test_execute_calls_legacy_function(self, mock_legacy):
        result = self.plugin.execute(
            make_args(goal_graph="g1", goal_graph_format="json", goal_graph_output=None, goal_graph_desc_max=50),
            make_ctx(),
        )
        self.assertTrue(result)
        mock_legacy.assert_called_once()


class TestGoalResumePlugin(unittest.TestCase):
    def setUp(self):
        self.plugin = GoalResumePlugin()

    def test_name(self):
        self.assertEqual(self.plugin.name, "goal-resume")

    def test_priority(self):
        self.assertEqual(self.plugin.priority, 20)

    def test_mutex_with(self):
        self.assertEqual(
            self.plugin.mutex_with,
            {"goal-cancel", "goal-graph", "multi-goal", "loop"},
        )

    def test_requires_task(self):
        self.assertFalse(self.plugin.requires_task)

    def test_matches_with_resume(self):
        self.assertTrue(self.plugin.matches(make_args(goal_resume="g1")))

    def test_matches_without_resume(self):
        self.assertFalse(self.plugin.matches(make_args(goal_resume=None)))

    @patch("dispatch.legacy.dispatch_agent_v2_with_goal_resume", return_value=True)
    def test_execute_calls_legacy_function(self, mock_legacy):
        result = self.plugin.execute(make_args(goal_resume="g1"), make_ctx())
        self.assertTrue(result)
        mock_legacy.assert_called_once()


class TestMultiGoalPlugin(unittest.TestCase):
    def setUp(self):
        self.plugin = MultiGoalPlugin()

    def test_name(self):
        self.assertEqual(self.plugin.name, "multi-goal")

    def test_priority(self):
        self.assertEqual(self.plugin.priority, 30)

    def test_mutex_with(self):
        self.assertEqual(
            self.plugin.mutex_with,
            {"goal-cancel", "goal-graph", "goal-resume", "loop"},
        )

    def test_requires_task(self):
        self.assertFalse(self.plugin.requires_task)

    def test_matches_with_multi_goal(self):
        self.assertTrue(self.plugin.matches(make_args(multi_goal="g1,g2")))

    def test_matches_without_multi_goal(self):
        self.assertFalse(self.plugin.matches(make_args(multi_goal=None)))

    @patch("dispatch.legacy.dispatch_agent_v2_with_multi_goal", return_value=True)
    def test_execute_calls_legacy_function(self, mock_legacy):
        result = self.plugin.execute(make_args(multi_goal="g1,g2"), make_ctx())
        self.assertTrue(result)
        mock_legacy.assert_called_once()


class TestLoopGoalPlugin(unittest.TestCase):
    def setUp(self):
        self.plugin = LoopGoalPlugin()

    def test_name(self):
        self.assertEqual(self.plugin.name, "loop")

    def test_priority(self):
        self.assertEqual(self.plugin.priority, 40)

    def test_mutex_with(self):
        self.assertEqual(
            self.plugin.mutex_with,
            {"goal-cancel", "goal-graph", "goal-resume", "multi-goal"},
        )

    def test_requires_task(self):
        self.assertFalse(self.plugin.requires_task)

    def test_matches_with_loop(self):
        self.assertTrue(self.plugin.matches(make_args(loop=5)))

    def test_matches_with_goal(self):
        self.assertTrue(self.plugin.matches(make_args(loop=1, goal="g1")))

    def test_matches_without_loop_or_goal(self):
        self.assertFalse(self.plugin.matches(make_args(loop=1, goal=None)))

    @patch("dispatch.legacy.dispatch_agent_v2_with_loop_goal", return_value=True)
    def test_execute_calls_legacy_function(self, mock_legacy):
        result = self.plugin.execute(
            make_args(loop=3, task="t", agent="solo-coder", goal=None, goal_desc=None,
                      criteria=None, convergence_window=3),
            make_ctx(),
        )
        self.assertTrue(result)
        mock_legacy.assert_called_once()
