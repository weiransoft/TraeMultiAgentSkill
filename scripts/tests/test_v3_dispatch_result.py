"""DispatchResult 单元测试。"""
import unittest
from dispatcher.dispatch_result import DispatchResult


class TestDispatchResult(unittest.TestCase):
    """DispatchResult 4 字段 + __bool__ 兼容旧 bool() 调用。"""

    def test_default_construction(self):
        r = DispatchResult()
        self.assertIsNone(r.matched_plugin)
        self.assertFalse(r.success)
        self.assertIsNone(r.error)
        self.assertIsNone(r.skipped_reason)

    def test_bool_conversion_matched_and_success(self):
        r = DispatchResult(matched_plugin="goal-cancel", success=True)
        self.assertTrue(bool(r))

    def test_bool_conversion_matched_but_failed(self):
        r = DispatchResult(matched_plugin="goal-cancel", success=False)
        self.assertFalse(bool(r))

    def test_bool_conversion_no_match(self):
        r = DispatchResult(matched_plugin=None, success=False, skipped_reason="no_match")
        self.assertFalse(bool(r))

    def test_dry_run_short_circuit(self):
        r = DispatchResult(success=True, skipped_reason="dry_run")
        self.assertTrue(bool(r))
