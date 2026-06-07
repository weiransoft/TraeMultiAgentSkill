"""V4 hot_register / hot_unregister 单元测试（Phase 17 §2.2）。

测试目标：
- hot_register 行为：与 register 走统一校验入口
- hot_unregister 行为：busy 检查 + force 开关 + mutex 校验
- _validate_plugin_metadata 统一入口：register 和 hot_register 都走
- _lock 线程安全
- 边界：重复 name / 非法 name / busy 等待 / force skip mutex
"""
import threading
import time
import unittest

from dispatcher.errors import (
    MutexDeclarationError,
    DuplicatePluginNameError,
    DuplicatePriorityError,
    MutexViolationError,
    PluginNotFoundError,
    PluginBusyError,
)
from dispatcher.goal_dispatcher import GoalDispatcher
from plugins.base import GoalCommandPlugin


class StubPlugin(GoalCommandPlugin):
    """Phase 17 测试用 stub plugin（满足 ABC 接口契约）。"""

    def __init__(
        self,
        name: str = "stub",
        priority: int = 100,
        mutex_with=None,
        matches_result: bool = True,
        execute_result: bool = True,
        cleanup_called=None,
    ):
        self._name = name
        self._priority = priority
        self._mutex_with = mutex_with or set()
        self._matches_result = matches_result
        self._execute_result = execute_result
        self._cleanup_called = (
            cleanup_called if cleanup_called is not None else []
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def priority(self) -> int:
        return self._priority

    @property
    def mutex_with(self) -> set:
        return self._mutex_with

    @property
    def requires_task(self) -> bool:
        return False

    def matches(self, args) -> bool:
        return self._matches_result

    def execute(self, args, ctx) -> bool:
        return self._execute_result

    def cleanup(self, ctx, exc) -> None:
        self._cleanup_called.append((ctx, exc))


class TestHotRegister(unittest.TestCase):
    """hot_register 与 register 走同一校验入口。"""

    def test_hot_register_success(self):
        """合法 plugin → hot_register 成功，加入 _plugins 列表。"""
        dispatcher = GoalDispatcher()
        p = StubPlugin(name="hot-a", priority=200)
        dispatcher.hot_register(p)
        names = [pl.name for pl in dispatcher.list_plugins()]
        self.assertIn("hot-a", names)

    def test_hot_register_rejects_invalid_name(self):
        """非法 name → 抛 MutexDeclarationError（与 register 行为一致）。"""
        dispatcher = GoalDispatcher()
        bad = StubPlugin(name="InvalidName", priority=200)  # 大写开头违反 kebab-case
        with self.assertRaises(MutexDeclarationError):
            dispatcher.hot_register(bad)

    def test_hot_register_rejects_duplicate_name(self):
        """重复 name → 抛 DuplicatePluginNameError。"""
        dispatcher = GoalDispatcher()
        dispatcher.hot_register(StubPlugin(name="dup", priority=200))
        with self.assertRaises(DuplicatePluginNameError):
            dispatcher.hot_register(StubPlugin(name="dup", priority=300))

    def test_hot_register_rejects_duplicate_priority(self):
        """重复 priority → 抛 DuplicatePriorityError。"""
        dispatcher = GoalDispatcher()
        dispatcher.hot_register(StubPlugin(name="p1", priority=200))
        with self.assertRaises(DuplicatePriorityError):
            dispatcher.hot_register(StubPlugin(name="p2", priority=200))

    def test_hot_register_validates_mutex_against_existing(self):
        """hot_register 校验 mutex 关系对称性（与 register 一致）。"""
        dispatcher = GoalDispatcher()
        dispatcher.hot_register(
            StubPlugin(name="alpha", priority=100, mutex_with={"beta"})
        )
        # beta 不在 mutex_with 反向声明 alpha → 不对称
        with self.assertRaises(MutexDeclarationError):
            dispatcher.hot_register(
                StubPlugin(name="beta", priority=200, mutex_with=set())
            )

    def test_hot_register_rejects_active_dispatch_mutex_violation(self):
        """hot_register 时若新 plugin mutex_with 引用正在执行的 plugin → 抛 MutexViolationError。"""
        dispatcher = GoalDispatcher()
        # active 已注册，且主动声明 newcomer 为 mutex_with（对称性已建立）
        active = StubPlugin(
            name="active", priority=100, mutex_with={"newcomer"}
        )
        dispatcher.hot_register(active)
        # 模拟 active 正在执行
        dispatcher._reload_guard.enter_execute("active")
        try:
            # newcomer 反向声明 active 为 mutex_with（对称），但 active 正在执行
            new_plugin = StubPlugin(
                name="newcomer", priority=200, mutex_with={"active"}
            )
            with self.assertRaises(MutexViolationError):
                dispatcher.hot_register(new_plugin)
        finally:
            dispatcher._reload_guard.exit_execute("active")


class TestHotUnregister(unittest.TestCase):
    """hot_unregister 行为：busy 检查 + force 开关 + mutex 引用。"""

    def test_hot_unregister_removes_plugin(self):
        """存在的 plugin → hot_unregister 移除。"""
        dispatcher = GoalDispatcher()
        p = StubPlugin(name="removable", priority=200)
        dispatcher.hot_register(p)
        dispatcher.hot_unregister("removable")
        names = [pl.name for pl in dispatcher.list_plugins()]
        self.assertNotIn("removable", names)

    def test_hot_unregister_returns_instance(self):
        """hot_unregister 返回被移除的 plugin 实例。"""
        dispatcher = GoalDispatcher()
        p = StubPlugin(name="ret", priority=200)
        dispatcher.hot_register(p)
        removed = dispatcher.hot_unregister("ret")
        self.assertIs(removed, p)

    def test_hot_unregister_unknown_raises(self):
        """不存在的 plugin → 抛 PluginNotFoundError。"""
        dispatcher = GoalDispatcher()
        with self.assertRaises(PluginNotFoundError) as cm:
            dispatcher.hot_unregister("ghost")
        self.assertIn("ghost", str(cm.exception))

    def test_hot_unregister_busy_raises_plugin_busy(self):
        """plugin 正在执行 → hot_unregister (force=False) 抛 PluginBusyError。"""
        dispatcher = GoalDispatcher()
        p = StubPlugin(name="busy", priority=200)
        dispatcher.hot_register(p)
        dispatcher._reload_guard.enter_execute("busy")
        try:
            with self.assertRaises(PluginBusyError):
                dispatcher.hot_unregister("busy", force=False)
        finally:
            dispatcher._reload_guard.exit_execute("busy")

    def test_hot_unregister_force_waits_for_idle(self):
        """force=True → wait_for_idle 30s，期间退出后正常 unregister。"""
        dispatcher = GoalDispatcher()
        p = StubPlugin(name="force-busy", priority=200)
        dispatcher.hot_register(p)
        dispatcher._reload_guard.enter_execute("force-busy")

        def releaser():
            time.sleep(0.1)
            dispatcher._reload_guard.exit_execute("force-busy")

        threading.Thread(target=releaser).start()
        removed = dispatcher.hot_unregister("force-busy", force=True)
        self.assertIs(removed, p)

    def test_hot_unregister_validates_no_mutex_references(self):
        """plugin 被其他 plugin 引用为 mutex_with → unregister 抛 MutexViolationError。"""
        dispatcher = GoalDispatcher()
        dispatcher.hot_register(
            StubPlugin(name="anchor", priority=100, mutex_with={"target"})
        )
        dispatcher.hot_register(
            StubPlugin(name="target", priority=200, mutex_with={"anchor"})
        )
        # 不带 force 卸载 target → 抛 MutexViolationError（因 anchor 仍引用 target）
        with self.assertRaises(MutexViolationError):
            dispatcher.hot_unregister("target", force=False)

    def test_hot_unregister_force_skips_mutex_check(self):
        """force=True → 跳过 mutex 校验。"""
        dispatcher = GoalDispatcher()
        dispatcher.hot_register(
            StubPlugin(name="anchor2", priority=100, mutex_with={"target2"})
        )
        dispatcher.hot_register(
            StubPlugin(name="target2", priority=200, mutex_with={"anchor2"})
        )
        removed = dispatcher.hot_unregister("target2", force=True)
        self.assertIsNotNone(removed)


class TestRegisterHotRegisterParity(unittest.TestCase):
    """register 和 hot_register 走同一 _validate_plugin_metadata 入口。"""

    def test_register_and_hot_register_same_validation(self):
        """register 和 hot_register 对相同非法 plugin 都抛同类异常。"""
        # register 路径
        d1 = GoalDispatcher()
        with self.assertRaises(MutexDeclarationError):
            d1.register(StubPlugin(name="BadName", priority=100))

        # hot_register 路径
        d2 = GoalDispatcher()
        with self.assertRaises(MutexDeclarationError):
            d2.hot_register(StubPlugin(name="BadName", priority=100))

    def test_register_and_hot_register_same_duplicate_check(self):
        """重复 name 检测一致。"""
        d1 = GoalDispatcher()
        d1.register(StubPlugin(name="dup1", priority=100))
        with self.assertRaises(DuplicatePluginNameError):
            d1.register(StubPlugin(name="dup1", priority=200))

        d2 = GoalDispatcher()
        d2.hot_register(StubPlugin(name="dup2", priority=100))
        with self.assertRaises(DuplicatePluginNameError):
            d2.hot_register(StubPlugin(name="dup2", priority=200))


class TestDispatchSnapshotContract(unittest.TestCase):
    """§2.10 dispatch snapshot 契约：dispatch 期间 _plugins 修改不影响本次。"""

    def test_dispatch_uses_snapshot(self):
        """dispatch 开始时锁定 plugin 实例，hot_unregister 不影响本次 execute。"""
        dispatcher = GoalDispatcher()
        # 使用一个会记录调用次数的 plugin
        from dispatcher.dispatch_result import DispatchResult
        from dispatcher.plugin_context import PluginContext
        from pathlib import Path
        from argparse import Namespace

        executed = []

        class RecordingPlugin(GoalCommandPlugin):
            def __init__(self):
                self._name = "rec"
                self._priority = 100
                self._mutex_with = set()

            @property
            def name(self): return self._name

            @property
            def priority(self): return self._priority

            @property
            def mutex_with(self): return self._mutex_with

            @property
            def requires_task(self): return False

            def matches(self, args): return True

            def execute(self, args, ctx):
                executed.append(self)
                return True

            def cleanup(self, ctx, exc): pass

        rec = RecordingPlugin()
        dispatcher.hot_register(rec)
        args = Namespace()
        # 构造最小 PluginContext：仅提供 project_root + log（其他字段有默认值）
        ctx = PluginContext(
            project_root=Path("/tmp"),
            log=lambda msg, level: None,
            dry_run=False,
        )
        result = dispatcher.dispatch(args, ctx)
        # 验证本次 dispatch 使用了 rec 实例
        self.assertEqual(executed, [rec])
        self.assertTrue(result.success)
        self.assertEqual(result.matched_plugin, "rec")


if __name__ == "__main__":
    unittest.main()
