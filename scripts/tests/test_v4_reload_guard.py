"""ReloadGuard 单元测试（Phase 17 §2.5）。

测试 ReloadGuard 的核心契约：
- 引用计数：同一 plugin 并发 execute 时正确计数
- Condition 通知：wait_for_idle 立即唤醒（不切片）
- is_busy / active_plugin_names 状态查询
- enter/exit 不配对防御：unbalanced counter
- 异常路径：wait_for_idle 超时仍能正确返回值
"""
import threading
import time
import unittest

from dispatcher.reload_guard import ReloadGuard


class TestReloadGuardBasic(unittest.TestCase):
    """ReloadGuard 基础引用计数行为。"""

    def test_initial_state_is_idle(self):
        """初始状态：无 active execute。"""
        guard = ReloadGuard()
        self.assertFalse(guard.is_busy("any-plugin"))
        self.assertEqual(guard.active_plugin_names(), set())

    def test_enter_marks_busy(self):
        """enter_execute 后 is_busy 应为 True。"""
        guard = ReloadGuard()
        guard.enter_execute("foo")
        self.assertTrue(guard.is_busy("foo"))
        self.assertEqual(guard.active_plugin_names(), {"foo"})

    def test_exit_clears_busy(self):
        """exit_execute 后 is_busy 应为 False。"""
        guard = ReloadGuard()
        guard.enter_execute("foo")
        guard.exit_execute("foo")
        self.assertFalse(guard.is_busy("foo"))
        self.assertEqual(guard.active_plugin_names(), set())


class TestReloadGuardReferenceCount(unittest.TestCase):
    """并发 execute 引用计数。"""

    def test_concurrent_execute_increments_count(self):
        """同一 plugin 多次 enter_execute → count 累加。"""
        guard = ReloadGuard()
        guard.enter_execute("foo")
        guard.enter_execute("foo")
        guard.enter_execute("foo")
        # active_plugin_names 仍为 {"foo"}（去重）
        self.assertEqual(guard.active_plugin_names(), {"foo"})
        # 退出 1 次后仍 busy（count=2）
        guard.exit_execute("foo")
        self.assertTrue(guard.is_busy("foo"))
        # 退出第 2 次后仍 busy（count=1）
        guard.exit_execute("foo")
        self.assertTrue(guard.is_busy("foo"))
        # 退出第 3 次后 idle（count=0）
        guard.exit_execute("foo")
        self.assertFalse(guard.is_busy("foo"))

    def test_multiple_distinct_plugins(self):
        """多个不同 plugin 并发 execute。"""
        guard = ReloadGuard()
        guard.enter_execute("foo")
        guard.enter_execute("bar")
        self.assertEqual(guard.active_plugin_names(), {"foo", "bar"})
        guard.exit_execute("foo")
        self.assertEqual(guard.active_plugin_names(), {"bar"})
        guard.exit_execute("bar")
        self.assertEqual(guard.active_plugin_names(), set())


class TestReloadGuardUnbalancedExit(unittest.TestCase):
    """exit 多于 enter 的防御性 counter（P2-7 暴露 metrics）。"""

    def test_exit_without_enter_increments_counter(self):
        """exit 比 enter 多 → unbalanced counter +1。"""
        guard = ReloadGuard()
        initial = guard.unbalanced_exit_count
        guard.exit_execute("never-entered")
        self.assertEqual(guard.unbalanced_exit_count, initial + 1)

    def test_unbalanced_exit_does_not_raise(self):
        """exit 多于 enter 不抛异常（防御性，避免污染主流程）。"""
        guard = ReloadGuard()
        try:
            guard.exit_execute("never-entered")
        except Exception as e:
            self.fail(f"exit_execute 不应抛异常，但抛了：{e}")

    def test_balanced_exit_resets_counter(self):
        """balanced enter/exit 不增加 counter。"""
        guard = ReloadGuard()
        initial = guard.unbalanced_exit_count
        guard.enter_execute("foo")
        guard.exit_execute("foo")
        self.assertEqual(guard.unbalanced_exit_count, initial)


class TestReloadGuardWaitForIdle(unittest.TestCase):
    """Condition 通知：wait_for_idle 立即唤醒。"""

    def test_wait_for_idle_returns_true_when_already_idle(self):
        """plugin 未 busy → wait_for_idle 立即返回 True。"""
        guard = ReloadGuard()
        start = time.time()
        result = guard.wait_for_idle("foo", timeout=5.0)
        elapsed = time.time() - start
        self.assertTrue(result)
        self.assertLess(elapsed, 0.1)  # 立即返回

    def test_wait_for_idle_returns_true_after_exit(self):
        """plugin busy 时 wait → 另一线程 exit 后立即唤醒。"""
        guard = ReloadGuard()
        guard.enter_execute("foo")
        result_holder = []

        def waiter():
            result_holder.append(guard.wait_for_idle("foo", timeout=5.0))

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.05)  # 确保 waiter 进入 wait
        guard.exit_execute("foo")
        t.join(timeout=2.0)
        self.assertEqual(result_holder, [True], "wait_for_idle 应在 exit 后立即返回 True")

    def test_wait_for_idle_returns_false_on_timeout(self):
        """plugin 持续 busy → wait_for_idle 超时返回 False。"""
        guard = ReloadGuard()
        guard.enter_execute("foo")
        result_holder = []

        def waiter():
            result_holder.append(guard.wait_for_idle("foo", timeout=0.2))

        t = threading.Thread(target=waiter)
        t.start()
        t.join(timeout=2.0)
        self.assertEqual(result_holder, [False], "wait_for_idle 应在超时后返回 False")
        # 清理
        guard.exit_execute("foo")

    def test_wait_for_idle_is_different_per_plugin(self):
        """wait_for_idle 只等待指定 plugin，不影响其他。"""
        guard = ReloadGuard()
        guard.enter_execute("foo")
        # bar 未 busy → wait_for_idle("bar") 立即返回 True
        self.assertTrue(guard.wait_for_idle("bar", timeout=0.1))
        # foo 仍 busy → wait_for_idle("foo") 会等到超时
        # 用极短 timeout 验证
        self.assertFalse(guard.wait_for_idle("foo", timeout=0.1))
        # 清理
        guard.exit_execute("foo")


if __name__ == "__main__":
    unittest.main()
