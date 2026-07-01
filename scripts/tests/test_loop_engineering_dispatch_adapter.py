"""LoopDispatchAdapter 单元测试。

测试目标：
- 空任务返回 fatal。
- facade 不可用时返回 fatal。
- facade 返回码正确映射为 AdapterInvokeResult kind。
- facade 抛异常时被包装为 fatal，不向上抛异常。
- 构造的 argparse.Namespace 中所有 plugin 匹配 flag 均被关闭，
  确保 dispatcher fallthrough 到 dispatch_agent_v2，避免 autonomous 递归。

本测试使用 MagicMock 模拟 facade 模块，因为 _dispatch_through_v3 是 V3 调度器入口，
单元测试不宜真实触发完整调度流程。
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from autonomous.dispatcher_adapter import AdapterInvokeResult
from loop_engineering.dispatch_adapter import LoopDispatchAdapter


# ---------------------------------------------------------------------- #
# TestLoopDispatchAdapterBasics: 基础构造与可用性                         #
# ---------------------------------------------------------------------- #


class TestLoopDispatchAdapterBasics(unittest.TestCase):
    """测试 LoopDispatchAdapter 基础行为。"""

    def test_01_init_default_project_root(self):
        """默认构造使用当前工作目录作为 project_root。"""
        adapter = LoopDispatchAdapter()
        self.assertIsInstance(adapter.project_root, Path)
        self.assertTrue(adapter.project_root.is_absolute())

    def test_02_init_with_project_root(self):
        """可显式指定 project_root。"""
        root = Path("/tmp/loop_test")
        adapter = LoopDispatchAdapter(project_root=root)
        self.assertEqual(adapter.project_root, root)

    def test_03_facade_unavailable_returns_fatal(self):
        """facade 不可用时返回 fatal，且不抛异常。"""
        adapter = LoopDispatchAdapter()
        with patch.object(adapter, "_get_facade", return_value=None):
            result = adapter.invoke(task="实现一个函数")

        self.assertFalse(result.success)
        self.assertEqual(result.kind, "fatal")
        self.assertIn("facade", result.summary)


# ---------------------------------------------------------------------- #
# TestLoopDispatchAdapterInvoke: invoke() 行为                            #
# ---------------------------------------------------------------------- #


class TestLoopDispatchAdapterInvoke(unittest.TestCase):
    """测试 invoke() 各种返回码与异常路径。"""

    def _make_adapter(self, returncode: int = 0) -> LoopDispatchAdapter:
        """构造一个 facade 返回特定 returncode 的 adapter。"""
        facade = MagicMock()
        facade._dispatch_through_v3.return_value = returncode
        return LoopDispatchAdapter(facade_module=facade)

    def test_04_invoke_empty_task_fatal(self):
        """空 task → fatal。"""
        adapter = self._make_adapter()
        result = adapter.invoke(task="")

        self.assertFalse(result.success)
        self.assertEqual(result.kind, "fatal")
        self.assertIn("空", result.summary)

    def test_05_invoke_whitespace_only_task_fatal(self):
        """仅空白 task → fatal。"""
        adapter = self._make_adapter()
        result = adapter.invoke(task="   \n  ")

        self.assertFalse(result.success)
        self.assertEqual(result.kind, "fatal")

    def test_06_invoke_rc_zero_success(self):
        """rc=0 → success。"""
        adapter = self._make_adapter(returncode=0)
        result = adapter.invoke(task="实现功能 X", agent="solo-coder")

        self.assertTrue(result.success)
        self.assertEqual(result.kind, "success")
        self.assertIn("rc=0", result.summary)
        self.assertIn("solo-coder", result.summary)

    def test_07_invoke_rc_one_retriable(self):
        """rc=1 → retriable。"""
        adapter = self._make_adapter(returncode=1)
        result = adapter.invoke(task="实现功能 X")

        self.assertFalse(result.success)
        self.assertEqual(result.kind, "retriable")
        self.assertIn("rc=1", result.summary)

    def test_08_invoke_rc_two_retriable(self):
        """rc=2 → retriable。"""
        adapter = self._make_adapter(returncode=2)
        result = adapter.invoke(task="实现功能 X")

        self.assertFalse(result.success)
        self.assertEqual(result.kind, "retriable")

    def test_09_invoke_rc_three_fatal(self):
        """rc=3 → fatal。"""
        adapter = self._make_adapter(returncode=3)
        result = adapter.invoke(task="实现功能 X")

        self.assertFalse(result.success)
        self.assertEqual(result.kind, "fatal")

    def test_10_invoke_large_rc_fatal(self):
        """rc=99 → fatal。"""
        adapter = self._make_adapter(returncode=99)
        result = adapter.invoke(task="实现功能 X")

        self.assertFalse(result.success)
        self.assertEqual(result.kind, "fatal")

    def test_11_invoke_catches_exception(self):
        """facade 抛异常 → fatal 包装（不抛异常给调用方）。"""
        facade = MagicMock()
        facade._dispatch_through_v3.side_effect = RuntimeError("boom")
        adapter = LoopDispatchAdapter(facade_module=facade)

        result = adapter.invoke(task="实现功能 X")

        self.assertFalse(result.success)
        self.assertEqual(result.kind, "fatal")
        self.assertIn("boom", result.summary)
        self.assertIsNotNone(result.error)
        self.assertNotEqual(result.error_trace, "")


# ---------------------------------------------------------------------- #
# TestLoopDispatchAdapterArgs: 构造的 args 必须关闭所有 plugin flag       #
# ---------------------------------------------------------------------- #


class TestLoopDispatchAdapterArgs(unittest.TestCase):
    """测试 _build_args 构造的 argparse.Namespace。"""

    def test_12_args_disable_autonomous(self):
        """args.autonomous 必须为 False，避免命中 RalphAutonomousPlugin。"""
        facade = MagicMock()
        facade._dispatch_through_v3.return_value = 0
        adapter = LoopDispatchAdapter(facade_module=facade)

        adapter.invoke(task="实现功能 X", agent="solo-coder")

        self.assertTrue(facade._dispatch_through_v3.called)
        args = facade._dispatch_through_v3.call_args[0][0]
        self.assertFalse(args.autonomous)

    def test_13_args_disable_loop_engineering(self):
        """args.loop_engineering 必须为 False，避免递归匹配。"""
        facade = MagicMock()
        facade._dispatch_through_v3.return_value = 0
        adapter = LoopDispatchAdapter(facade_module=facade)

        adapter.invoke(task="实现功能 X")

        args = facade._dispatch_through_v3.call_args[0][0]
        self.assertFalse(args.loop_engineering)

    def test_14_args_loop_is_one(self):
        """args.loop 必须为 1，避免进入 loop plugin。"""
        facade = MagicMock()
        facade._dispatch_through_v3.return_value = 0
        adapter = LoopDispatchAdapter(facade_module=facade)

        adapter.invoke(task="实现功能 X")

        args = facade._dispatch_through_v3.call_args[0][0]
        self.assertEqual(args.loop, 1)

    def test_15_args_goal_flags_disabled(self):
        """所有 goal / multi_goal 匹配 flag 必须关闭。"""
        facade = MagicMock()
        facade._dispatch_through_v3.return_value = 0
        adapter = LoopDispatchAdapter(facade_module=facade)

        adapter.invoke(task="实现功能 X")

        args = facade._dispatch_through_v3.call_args[0][0]
        self.assertIsNone(args.goal)
        self.assertIsNone(args.multi_goal)
        self.assertIsNone(args.goal_graph)
        self.assertIsNone(args.goal_cancel)
        self.assertIsNone(args.goal_resume)

    def test_16_args_task_and_agent_preserved(self):
        """task 与 agent 必须原样透传。"""
        facade = MagicMock()
        facade._dispatch_through_v3.return_value = 0
        adapter = LoopDispatchAdapter(facade_module=facade)

        adapter.invoke(task="实现功能 X", agent="architect")

        args = facade._dispatch_through_v3.call_args[0][0]
        self.assertEqual(args.task, "实现功能 X")
        self.assertEqual(args.agent, "architect")

    def test_17_args_project_root_is_string(self):
        """project_root 必须为字符串路径。"""
        facade = MagicMock()
        facade._dispatch_through_v3.return_value = 0
        root = Path("/tmp/loop_project")
        adapter = LoopDispatchAdapter(project_root=root, facade_module=facade)

        adapter.invoke(task="实现功能 X")

        args = facade._dispatch_through_v3.call_args[0][0]
        self.assertEqual(args.project_root, str(root))

    def test_18_args_hot_reload_disabled(self):
        """hot_reload 必须关闭，避免 loop 内部启动 watcher。"""
        facade = MagicMock()
        facade._dispatch_through_v3.return_value = 0
        adapter = LoopDispatchAdapter(facade_module=facade)

        adapter.invoke(task="实现功能 X")

        args = facade._dispatch_through_v3.call_args[0][0]
        self.assertFalse(args.hot_reload)


# ---------------------------------------------------------------------- #
# TestLoopDispatchAdapterResult: 返回结果结构                              #
# ---------------------------------------------------------------------- #


class TestLoopDispatchAdapterResult(unittest.TestCase):
    """测试 AdapterInvokeResult 字段完整性。"""

    def test_19_success_result_has_output_and_summary(self):
        """成功时 output / summary 非空。"""
        facade = MagicMock()
        facade._dispatch_through_v3.return_value = 0
        adapter = LoopDispatchAdapter(facade_module=facade)

        result = adapter.invoke(task="实现功能 X", agent="test-expert")

        self.assertTrue(result.success)
        self.assertEqual(result.kind, "success")
        self.assertIn("dispatcher 返回码 0", result.output)
        self.assertIn("test-expert", result.summary)

    def test_20_fatal_result_has_summary(self):
        """fatal 时 summary 说明原因。"""
        adapter = LoopDispatchAdapter()
        with patch.object(adapter, "_get_facade", return_value=None):
            result = adapter.invoke(task="实现功能 X")

        self.assertFalse(result.success)
        self.assertEqual(result.kind, "fatal")
        self.assertNotEqual(result.summary, "")


if __name__ == "__main__":
    unittest.main()
