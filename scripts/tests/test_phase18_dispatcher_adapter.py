"""Phase 18: DispatcherAdapter 单元测试。

测试 DispatcherAdapter 的全部行为：
- 构造 + 延迟加载 facade
- is_available() 检测
- invoke() / invoke_with_args() 调用
- 失败包装（retriable / fatal）
- 不抛异常给调用方
"""
import argparse
import unittest
from unittest.mock import MagicMock, patch

from autonomous.dispatcher_adapter import AdapterInvokeResult, DispatcherAdapter


# ---------------------------------------------------------------------- #
# TestDispatcherAdapterBasics: 基础功能                                  #
# ---------------------------------------------------------------------- #


class TestDispatcherAdapterBasics(unittest.TestCase):
    """测试 DispatcherAdapter 基础行为。"""

    def test_01_init(self):
        """默认构造成功。"""
        adapter = DispatcherAdapter()
        self.assertIsNone(adapter._facade)
        self.assertIsNone(adapter._dispatcher_available)

    def test_02_is_available_no_facade(self):
        """无 facade → 不可用。"""
        adapter = DispatcherAdapter(facade_module=None)
        with patch.object(adapter, "_get_facade", return_value=None):
            result = adapter.is_available()
        self.assertFalse(result)

    def test_03_is_available_with_v3_method(self):
        """有 _dispatch_through_v3 方法 → 可用。"""
        facade = MagicMock()
        # MagicMock 默认所有属性都存在，所以可用
        adapter = DispatcherAdapter(facade_module=facade)
        result = adapter.is_available()
        self.assertTrue(result)

    def test_04_is_available_cached(self):
        """is_available 缓存结果。"""
        facade = MagicMock()
        adapter = DispatcherAdapter(facade_module=facade)
        r1 = adapter.is_available()
        r2 = adapter.is_available()
        self.assertEqual(r1, r2)
        # _get_facade 只应被调用一次（因为缓存）
        # 这里 _get_facade 返回 facade，但 cached 后不再调用
        self.assertTrue(adapter._dispatcher_available is not None)


# ---------------------------------------------------------------------- #
# TestDispatcherAdapterInvoke: invoke() 行为                             #
# ---------------------------------------------------------------------- #


class TestDispatcherAdapterInvoke(unittest.TestCase):
    """测试 invoke() 各种行为。"""

    def _make_adapter(self, returncode: int = 0) -> DispatcherAdapter:
        """构造一个 facade 返回特定 returncode 的 adapter。"""
        facade = MagicMock()
        facade._dispatch_through_v3.return_value = returncode
        return DispatcherAdapter(facade_module=facade)

    def test_05_invoke_empty_task_fatal(self):
        """空 task → fatal。"""
        adapter = self._make_adapter()
        result = adapter.invoke(task="")
        self.assertEqual(result.kind, "fatal")
        self.assertFalse(result.success)
        self.assertIn("空", result.summary)

    def test_06_invoke_success(self):
        """rc=0 → success。"""
        adapter = self._make_adapter(returncode=0)
        result = adapter.invoke(task="实现功能 X")
        self.assertTrue(result.success)
        self.assertEqual(result.kind, "success")
        self.assertIn("rc=0", result.summary)

    def test_07_invoke_retriable(self):
        """rc=1 或 2 → retriable。"""
        for rc in (1, 2):
            adapter = self._make_adapter(returncode=rc)
            result = adapter.invoke(task="任务")
            self.assertFalse(result.success)
            self.assertEqual(result.kind, "retriable")

    def test_08_invoke_fatal_unknown_rc(self):
        """rc>=3 → fatal。"""
        adapter = self._make_adapter(returncode=99)
        result = adapter.invoke(task="任务")
        self.assertFalse(result.success)
        self.assertEqual(result.kind, "fatal")

    def test_09_invoke_injects_auto_skills(self):
        """auto_skills 注入到 args。"""
        facade = MagicMock()
        facade._dispatch_through_v3.return_value = 0
        adapter = DispatcherAdapter(facade_module=facade)

        skills = [{"name": "translation", "description": "翻译"}]
        result = adapter.invoke(task="翻译", auto_skills=skills)

        # 检查 facade 收到了 args
        self.assertTrue(facade._dispatch_through_v3.called)
        args = facade._dispatch_through_v3.call_args[0][0]
        self.assertEqual(args.auto_skills, skills)
        self.assertTrue(args.autonomous)
        self.assertTrue(result.success)

    def test_10_invoke_catches_exception(self):
        """facade 抛异常 → fatal 包装（不抛）。"""
        facade = MagicMock()
        facade._dispatch_through_v3.side_effect = RuntimeError("boom")
        adapter = DispatcherAdapter(facade_module=facade)
        result = adapter.invoke(task="任务")

        # 不应抛异常
        self.assertFalse(result.success)
        self.assertEqual(result.kind, "fatal")
        self.assertIn("boom", result.summary)
        self.assertNotEqual(result.error_trace, "")

    def test_11_invoke_catches_attribute_error(self):
        """AttributeError → fatal 包装。"""
        facade = MagicMock()
        # 模拟 facade 缺少方法：直接调用不存在的属性会失败
        del facade._dispatch_through_v3
        adapter = DispatcherAdapter(facade_module=facade)
        result = adapter.invoke(task="任务")

        self.assertFalse(result.success)
        self.assertEqual(result.kind, "fatal")

    def test_12_invoke_unavailable_facade(self):
        """facade 不可用 → fatal。"""
        adapter = DispatcherAdapter()
        with patch.object(adapter, "_get_facade", return_value=None):
            result = adapter.invoke(task="任务")
        self.assertFalse(result.success)
        self.assertEqual(result.kind, "fatal")
        self.assertIn("不可用", result.summary)


# ---------------------------------------------------------------------- #
# TestDispatcherAdapterInvokeWithArgs: invoke_with_args()                #
# ---------------------------------------------------------------------- #


class TestDispatcherAdapterInvokeWithArgs(unittest.TestCase):
    """测试 invoke_with_args() 行为。"""

    def test_13_invoke_with_args_success(self):
        """使用预构造 args 调用 → 透传 returncode。"""
        facade = MagicMock()
        facade._dispatch_through_v3.return_value = 0
        adapter = DispatcherAdapter(facade_module=facade)
        args = argparse.Namespace(task="X")
        result = adapter.invoke_with_args(args)
        self.assertTrue(result.success)
        self.assertEqual(result.kind, "success")
        facade._dispatch_through_v3.assert_called_once_with(args)

    def test_14_invoke_with_args_exception(self):
        """facade 抛异常 → fatal。"""
        facade = MagicMock()
        facade._dispatch_through_v3.side_effect = ValueError("err")
        adapter = DispatcherAdapter(facade_module=facade)
        args = argparse.Namespace(task="X")
        result = adapter.invoke_with_args(args)
        self.assertEqual(result.kind, "fatal")
        self.assertIn("ValueError", result.summary)


# ---------------------------------------------------------------------- #
# TestAdapterInvokeResult: 数据类行为                                    #
# ---------------------------------------------------------------------- #


class TestAdapterInvokeResult(unittest.TestCase):
    """测试 AdapterInvokeResult 数据类。"""

    def test_15_default_values(self):
        """默认字段值正确。"""
        r = AdapterInvokeResult(success=True)
        self.assertTrue(r.success)
        self.assertEqual(r.kind, "failed")
        self.assertEqual(r.output, "")
        self.assertEqual(r.tokens, 0)
        self.assertEqual(r.skills_used, [])

    def test_16_to_dict_like(self):
        """可以被访问所有字段。"""
        r = AdapterInvokeResult(
            success=True,
            kind="success",
            output="out",
            summary="sum",
            tokens=100,
            skills_used=["a", "b"],
        )
        self.assertEqual(r.skills_used, ["a", "b"])
        self.assertEqual(r.tokens, 100)


if __name__ == "__main__":
    unittest.main()
