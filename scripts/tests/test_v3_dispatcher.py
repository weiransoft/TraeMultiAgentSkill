"""V3 Dispatcher 单元测试（T5 完整版）。"""
import unittest
import argparse
from typing import Set, Optional
from pathlib import Path
from dispatcher.errors import (
    MutexViolationError,
    DuplicatePluginNameError,
    DuplicatePriorityError,
    MutexDeclarationError,
)
from dispatcher.goal_dispatcher import GoalDispatcher
from dispatcher.plugin_context import PluginContext
from dispatcher.dispatch_result import DispatchResult
from dispatcher.middleware import DispatchMiddleware


# === Mock 插件 ===
class MockPlugin:
    """测试用 mock plugin（满足 ABC 接口契约）。"""
    def __init__(self, name="mock", priority=100, mutex_with=None, matches_result=True,
                 execute_result=True, raise_exc=None, cleanup_called=None):
        self._name = name
        self._priority = priority
        self._mutex_with = mutex_with or set()
        self._matches_result = matches_result
        self._execute_result = execute_result
        self._raise_exc = raise_exc
        self._cleanup_called = cleanup_called if cleanup_called is not None else []

    @property
    def name(self): return self._name
    @property
    def priority(self): return self._priority
    @property
    def mutex_with(self): return self._mutex_with
    @property
    def requires_task(self): return False
    def matches(self, args): return self._matches_result
    def execute(self, args, ctx):
        if self._raise_exc:
            raise self._raise_exc
        return self._execute_result
    def cleanup(self, ctx, exc): self._cleanup_called.append(exc)


def noop_log(message, level="INFO"): pass


def make_args(**kwargs):
    args = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(args, k, v)
    return args


def make_ctx(**kwargs):
    defaults = {"project_root": Path("/tmp"), "log": noop_log}
    defaults.update(kwargs)
    return PluginContext(**defaults)


# === 测试类 ===
class TestRegister(unittest.TestCase):
    """register() 行为（H-6 修复）。"""

    def test_register_single_plugin(self):
        d = GoalDispatcher(plugins=[MockPlugin(priority=10)])
        self.assertEqual(len(d.list_plugins()), 1)

    def test_register_sorted_by_priority(self):
        p1 = MockPlugin(name="p1", priority=30)
        p2 = MockPlugin(name="p2", priority=10)
        p3 = MockPlugin(name="p3", priority=20)
        d = GoalDispatcher(plugins=[p1, p2, p3])
        names = [p.name for p in d.list_plugins()]
        self.assertEqual(names, ["p2", "p3", "p1"])

    def test_register_duplicate_name_raises(self):
        with self.assertRaises(DuplicatePluginNameError):
            GoalDispatcher(plugins=[
                MockPlugin(name="dup", priority=10),
                MockPlugin(name="dup", priority=20),
            ])

    def test_register_duplicate_priority_raises(self):
        with self.assertRaises(DuplicatePriorityError):
            GoalDispatcher(plugins=[
                MockPlugin(name="p1", priority=10),
                MockPlugin(name="p2", priority=10),
            ])

    def test_register_invalid_name_format_raises(self):
        with self.assertRaises(MutexDeclarationError):
            GoalDispatcher(plugins=[MockPlugin(name="InvalidName")])


class TestMutexValidation(unittest.TestCase):
    """启动期 mutex 一致性校验（H-1 修复）。"""

    def test_mutex_self_reference_raises(self):
        with self.assertRaises(MutexDeclarationError):
            GoalDispatcher(plugins=[MockPlugin(name="p1", priority=10, mutex_with={"p1"})])

    def test_mutex_nonexistent_name_raises(self):
        with self.assertRaises(MutexDeclarationError):
            GoalDispatcher(plugins=[MockPlugin(name="p1", priority=10, mutex_with={"nonexistent"})])

    def test_mutex_asymmetric_raises(self):
        # p1 mutex p2, but p2 not mutex p1
        with self.assertRaises(MutexDeclarationError):
            GoalDispatcher(plugins=[
                MockPlugin(name="p1", priority=10, mutex_with={"p2"}),
                MockPlugin(name="p2", priority=20, mutex_with=set()),
            ])

    def test_mutex_symmetric_ok(self):
        # Both sides declare each other
        d = GoalDispatcher(plugins=[
            MockPlugin(name="p1", priority=10, mutex_with={"p2"}),
            MockPlugin(name="p2", priority=20, mutex_with={"p1"}),
        ])
        self.assertEqual(len(d.list_plugins()), 2)


class TestDispatch(unittest.TestCase):
    """dispatch() 行为（H-7 + 风险-3/4/5 修复）。"""

    def test_dispatch_no_match_returns_skipped(self):
        d = GoalDispatcher(plugins=[MockPlugin(matches_result=False)])
        result = d.dispatch(make_args(), make_ctx())
        self.assertIsNone(result.matched_plugin)
        self.assertEqual(result.skipped_reason, "no_match")

    def test_dispatch_matched_and_success(self):
        p = MockPlugin(matches_result=True, execute_result=True)
        d = GoalDispatcher(plugins=[p])
        result = d.dispatch(make_args(), make_ctx())
        self.assertEqual(result.matched_plugin, "mock")
        self.assertTrue(result.success)

    def test_dispatch_matched_but_failed(self):
        p = MockPlugin(matches_result=True, execute_result=False)
        d = GoalDispatcher(plugins=[p])
        result = d.dispatch(make_args(), make_ctx())
        self.assertEqual(result.matched_plugin, "mock")
        self.assertFalse(result.success)

    def test_dispatch_exception_caught(self):
        p = MockPlugin(matches_result=True, raise_exc=ValueError("boom"))
        d = GoalDispatcher(plugins=[p])
        result = d.dispatch(make_args(), make_ctx())
        self.assertIsInstance(result.error, ValueError)
        self.assertFalse(result.success)

    def test_dispatch_dry_run_short_circuit(self):
        # 风险-5 验证
        d = GoalDispatcher(plugins=[MockPlugin(matches_result=True)])
        result = d.dispatch(make_args(), make_ctx(dry_run=True))
        self.assertEqual(result.skipped_reason, "dry_run")
        self.assertTrue(result.success)

    def test_dispatch_cleanup_always_called(self):
        # 风险-3 验证
        cleanup_called = []
        p = MockPlugin(cleanup_called=cleanup_called, execute_result=True)
        d = GoalDispatcher(plugins=[p])
        d.dispatch(make_args(), make_ctx())
        self.assertEqual(len(cleanup_called), 1)
        self.assertIsNone(cleanup_called[0])  # success path: exc=None

    def test_dispatch_cleanup_receives_exception(self):
        # 风险-3 验证：exc 真实传递
        cleanup_called = []
        exc = ValueError("boom")
        p = MockPlugin(cleanup_called=cleanup_called, raise_exc=exc)
        d = GoalDispatcher(plugins=[p])
        d.dispatch(make_args(), make_ctx())
        self.assertEqual(len(cleanup_called), 1)
        self.assertIs(cleanup_called[0], exc)


class TestMiddleware(unittest.TestCase):
    """middleware 钩子（H-2 + 风险-4 修复）。"""

    def test_middleware_before_after_called(self):
        # 风险-4 验证：after 收到真实 DispatchResult
        before_calls = []
        after_calls = []
        class MyMW(DispatchMiddleware):
            def before(self, args, ctx): before_calls.append((args, ctx))
            def after(self, args, ctx, result): after_calls.append((args, ctx, result))

        d = GoalDispatcher(
            plugins=[MockPlugin()],
            middlewares=[MyMW()],
        )
        result = d.dispatch(make_args(), make_ctx())
        self.assertEqual(len(before_calls), 1)
        self.assertEqual(len(after_calls), 1)
        self.assertIs(after_calls[0][2], result)  # after 收到真实 result

    def test_middleware_exception_does_not_break_dispatch(self):
        class FailingMW(DispatchMiddleware):
            def before(self, args, ctx): raise RuntimeError("mw boom")
            def after(self, args, ctx, result): pass

        d = GoalDispatcher(
            plugins=[MockPlugin()],
            middlewares=[FailingMW()],
        )
        result = d.dispatch(make_args(), make_ctx())  # 不应 raise
        self.assertTrue(result.success)


class TestMutexRuntimeCheck(unittest.TestCase):
    """运行时 validate_mutex（dispatch 前调用）。"""

    def test_validate_mutex_no_conflict(self):
        d = GoalDispatcher(plugins=[
            MockPlugin(name="p1", priority=10, matches_result=True, mutex_with={"p2"}),
            MockPlugin(name="p2", priority=20, matches_result=False, mutex_with={"p1"}),
        ])
        d.validate_mutex(make_args())  # 不应 raise

    def test_validate_mutex_conflict_raises(self):
        d = GoalDispatcher(plugins=[
            MockPlugin(name="p1", priority=10, matches_result=True, mutex_with={"p2"}),
            MockPlugin(name="p2", priority=20, matches_result=True, mutex_with={"p1"}),
        ])
        with self.assertRaises(MutexViolationError):
            d.validate_mutex(make_args())
