#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TokenBudgetGuard 单元测试

测试覆盖：
1. 数据类（TokenBudget / BudgetDecision）字段 + 校验
2. 三阶段校验：pre_execute / during_execute / post_execute
3. 三种执行模式：HARD / SOFT / HYBRID
4. 画像反哺（mock fingerprint）
5. GuardCoordinator 兼容接口
6. 错误路径 + 异常
7. 并发安全 + 性能基线
"""

import sys
import unittest
import time
import threading
from pathlib import Path
from unittest.mock import MagicMock

# 添加 dynamic_workflow 目录到路径
SCRIPT_DIR = Path(__file__).resolve().parent.parent
DYNAMIC_WORKFLOW_DIR = SCRIPT_DIR / "dynamic_workflow"
SCRIPTS_DIR = SCRIPT_DIR
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(DYNAMIC_WORKFLOW_DIR))

from token_budget_guard import (
    TokenBudget,
    BudgetDecision,
    BudgetEnforcementMode,
    BudgetRecommendation,
    TokenBudgetExceeded,
    TokenBudgetGuard,
    TokenBudgetGuardError,
    InvalidBudgetError,
    DEFAULT_SOFT_THRESHOLD,
    DEFAULT_HARD_THRESHOLD,
    PRE_EXECUTE_REJECT_RATIO,
    PRE_EXECUTE_SOFT_RATIO,
)


# ============================================================================
# 测试 1: TokenBudget 数据类
# ============================================================================

class TestTokenBudget(unittest.TestCase):
    """TokenBudget 数据类测试"""

    def test_basic_budget(self):
        """测试基础预算"""
        b = TokenBudget(total_budget=100_000)
        self.assertEqual(b.total_budget, 100_000)
        self.assertEqual(b.consumed, 0)
        self.assertEqual(b.soft_threshold, 0.8)
        self.assertEqual(b.hard_threshold, 1.0)
        self.assertEqual(b.remaining, 100_000)
        self.assertEqual(b.consumption_ratio, 0.0)
        self.assertFalse(b.is_soft_exceeded)
        self.assertFalse(b.is_hard_exceeded)

    def test_budget_total_must_be_positive(self):
        """测试 total_budget 必须为正"""
        with self.assertRaises(InvalidBudgetError):
            TokenBudget(total_budget=0)
        with self.assertRaises(InvalidBudgetError):
            TokenBudget(total_budget=-1)

    def test_budget_consumed_must_be_non_negative(self):
        """测试 consumed 不能为负"""
        with self.assertRaises(InvalidBudgetError):
            TokenBudget(total_budget=100, consumed=-1)

    def test_soft_threshold_must_be_less_than_hard(self):
        """测试 soft_threshold < hard_threshold"""
        with self.assertRaises(InvalidBudgetError):
            TokenBudget(total_budget=100, soft_threshold=0.9, hard_threshold=0.8)
        with self.assertRaises(InvalidBudgetError):
            TokenBudget(total_budget=100, soft_threshold=0.9, hard_threshold=0.9)

    def test_soft_threshold_out_of_range(self):
        """测试 soft_threshold 越界"""
        with self.assertRaises(InvalidBudgetError):
            TokenBudget(total_budget=100, soft_threshold=0)
        with self.assertRaises(InvalidBudgetError):
            TokenBudget(total_budget=100, soft_threshold=1.0)
        with self.assertRaises(InvalidBudgetError):
            TokenBudget(total_budget=100, soft_threshold=1.5)

    def test_consumption_ratio(self):
        """测试消费比计算"""
        b = TokenBudget(total_budget=1000, consumed=250)
        self.assertEqual(b.consumption_ratio, 0.25)

    def test_remaining_subtracts_consumed_and_reserved(self):
        """测试 remaining = total - consumed - reserved"""
        b = TokenBudget(total_budget=1000, consumed=200, reserved=100)
        self.assertEqual(b.remaining, 700)

    def test_remaining_never_negative(self):
        """测试 remaining 不会为负"""
        b = TokenBudget(total_budget=1000, consumed=1500)
        self.assertEqual(b.remaining, 0)

    def test_is_soft_exceeded(self):
        """测试 is_soft_exceeded"""
        b = TokenBudget(total_budget=1000, consumed=850)  # 85% > 80%
        self.assertTrue(b.is_soft_exceeded)
        self.assertFalse(b.is_hard_exceeded)

    def test_is_hard_exceeded(self):
        """测试 is_hard_exceeded"""
        b = TokenBudget(total_budget=1000, consumed=1000)  # 100%
        self.assertTrue(b.is_soft_exceeded)
        self.assertTrue(b.is_hard_exceeded)

    def test_to_dict(self):
        """测试转字典"""
        b = TokenBudget(total_budget=1000, consumed=500)
        d = b.to_dict()
        self.assertEqual(d["total_budget"], 1000)
        self.assertEqual(d["consumed"], 500)
        self.assertEqual(d["consumption_ratio"], 0.5)


# ============================================================================
# 测试 2: BudgetDecision 数据类
# ============================================================================

class TestBudgetDecision(unittest.TestCase):
    """BudgetDecision 数据类测试"""

    def test_basic_decision(self):
        """测试基础决策"""
        d = BudgetDecision(
            allow_continue=True,
            enforcement=BudgetEnforcementMode.HARD,
            recommendation=BudgetRecommendation.CONTINUE,
            remaining=500,
            consumption_ratio=0.5,
        )
        self.assertTrue(d.allow_continue)
        self.assertEqual(d.recommendation, BudgetRecommendation.CONTINUE)
        self.assertEqual(d.stage, "pre_execute")

    def test_to_dict(self):
        """测试转字典"""
        d = BudgetDecision(
            allow_continue=True,
            enforcement=BudgetEnforcementMode.SOFT,
            recommendation=BudgetRecommendation.SWITCH_TO_HAIKU,
            remaining=0,
            consumption_ratio=1.0,
        )
        result = d.to_dict()
        self.assertEqual(result["allow_continue"], True)
        self.assertEqual(result["enforcement"], "soft")
        self.assertEqual(result["recommendation"], "switch_to_haiku")

    def test_mode_from_str(self):
        """测试 BudgetEnforcementMode.from_str"""
        self.assertEqual(BudgetEnforcementMode.from_str("hard"), BudgetEnforcementMode.HARD)
        self.assertEqual(BudgetEnforcementMode.from_str("SOFT"), BudgetEnforcementMode.SOFT)
        self.assertEqual(BudgetEnforcementMode.from_str("Hybrid"), BudgetEnforcementMode.HYBRID)
        with self.assertRaises(TokenBudgetGuardError):
            BudgetEnforcementMode.from_str("invalid")


# ============================================================================
# 测试 3: TokenBudgetExceeded 异常
# ============================================================================

class TestTokenBudgetExceeded(unittest.TestCase):
    """TokenBudgetExceeded 异常测试"""

    def test_basic_exception(self):
        """测试基础异常"""
        e = TokenBudgetExceeded(consumed=1500, budget=1000)
        self.assertEqual(e.consumed, 1500)
        self.assertEqual(e.budget, 1000)
        self.assertIn("Token 预算超限", str(e))
        self.assertIn("1500", str(e))

    def test_custom_message(self):
        """测试自定义消息"""
        e = TokenBudgetExceeded(
            consumed=1500,
            budget=1000,
            message="自定义错误",
        )
        self.assertEqual(str(e), "自定义错误")

    def test_repr(self):
        """测试 __repr__"""
        e = TokenBudgetExceeded(consumed=1500, budget=1000)
        r = repr(e)
        self.assertIn("TokenBudgetExceeded", r)
        self.assertIn("1500", r)
        self.assertIn("1000", r)


# ============================================================================
# 测试 4: Guard 基础功能
# ============================================================================

class TestGuardBasic(unittest.TestCase):
    """Guard 基础功能测试"""

    def setUp(self):
        self.guard = TokenBudgetGuard()

    def test_create_budget(self):
        """测试创建预算"""
        b = self.guard.create_budget(total=100_000)
        self.assertEqual(b.total_budget, 100_000)
        self.assertEqual(b.remaining, 100_000)

    def test_create_budget_with_task_id(self):
        """测试带 task_id 创建"""
        b = self.guard.create_budget(total=100_000, task_id="task_001")
        retrieved = self.guard.get_budget("task_001")
        self.assertEqual(retrieved, b)

    def test_get_budget_nonexistent(self):
        """测试获取不存在的预算返回 None"""
        self.assertIsNone(self.guard.get_budget("nonexistent"))

    def test_release_budget(self):
        """测试释放预算"""
        self.guard.create_budget(total=100, task_id="task_001")
        self.guard.release_budget("task_001")
        self.assertIsNone(self.guard.get_budget("task_001"))

    def test_pre_execute_check_normal(self):
        """测试预检正常情况"""
        b = self.guard.create_budget(total=100_000)
        decision = self.guard.pre_execute_check(b, estimated_tokens=50_000)
        self.assertTrue(decision.allow_continue)
        self.assertEqual(decision.recommendation, BudgetRecommendation.CONTINUE)
        self.assertEqual(decision.stage, "pre_execute")

    def test_pre_execute_check_reject_over_budget(self):
        """测试预检超额拒绝"""
        b = self.guard.create_budget(total=100_000)
        decision = self.guard.pre_execute_check(b, estimated_tokens=150_000)
        self.assertFalse(decision.allow_continue)
        self.assertEqual(decision.recommendation, BudgetRecommendation.SPLIT_TASK)

    def test_pre_execute_check_soft_warning(self):
        """测试预检软警告"""
        b = self.guard.create_budget(total=100_000)
        decision = self.guard.pre_execute_check(b, estimated_tokens=85_000)
        # 85% > 80% 软阈值
        self.assertTrue(decision.allow_continue)
        self.assertEqual(decision.recommendation, BudgetRecommendation.SWITCH_TO_HAIKU)
        self.assertGreater(len(decision.warnings), 0)

    def test_record_consumption_normal(self):
        """测试消费记录正常情况"""
        b = self.guard.create_budget(total=100_000)
        decision = self.guard.record_consumption(b, consumed=10_000)
        self.assertTrue(decision.allow_continue)
        self.assertEqual(b.consumed, 10_000)
        self.assertEqual(decision.consumption_ratio, 0.1)

    def test_record_consumption_hard_exceeded_hard_mode(self):
        """测试 HARD 模式硬超限抛异常"""
        b = self.guard.create_budget(total=100_000)
        b.consumed = 80_000  # 已达软阈值
        with self.assertRaises(TokenBudgetExceeded):
            self.guard.record_consumption(b, consumed=30_000, mode=BudgetEnforcementMode.HARD)

    def test_record_consumption_soft_exceeded(self):
        """测试软超限建议切换 haiku"""
        b = self.guard.create_budget(total=100_000)
        decision = self.guard.record_consumption(b, consumed=85_000)
        self.assertTrue(decision.allow_continue)
        self.assertEqual(decision.recommendation, BudgetRecommendation.SWITCH_TO_HAIKU)
        self.assertEqual(decision.enforcement, BudgetEnforcementMode.SOFT)

    def test_post_execute_review(self):
        """测试后审"""
        b = self.guard.create_budget(total=100_000)
        b.consumed = 50_000
        # 不应抛异常
        self.guard.post_execute_review(b, success=True)

    def test_invalid_estimated_tokens_raises(self):
        """测试非法 estimated_tokens 抛异常"""
        b = self.guard.create_budget(total=100_000)
        with self.assertRaises(InvalidBudgetError):
            self.guard.pre_execute_check(b, estimated_tokens=0)
        with self.assertRaises(InvalidBudgetError):
            self.guard.pre_execute_check(b, estimated_tokens=-1)

    def test_invalid_consumed_raises(self):
        """测试非法 consumed 抛异常"""
        b = self.guard.create_budget(total=100_000)
        with self.assertRaises(InvalidBudgetError):
            self.guard.record_consumption(b, consumed=0)
        with self.assertRaises(InvalidBudgetError):
            self.guard.record_consumption(b, consumed=-1)


# ============================================================================
# 测试 5: 执行模式
# ============================================================================

class TestGuardEnforcement(unittest.TestCase):
    """执行模式测试"""

    def test_hard_mode_raises_on_exceed(self):
        """测试 HARD 模式硬超限抛异常"""
        guard = TokenBudgetGuard(default_mode=BudgetEnforcementMode.HARD)
        b = guard.create_budget(total=100_000)
        b.consumed = 80_000
        with self.assertRaises(TokenBudgetExceeded):
            guard.record_consumption(b, consumed=30_000)

    def test_soft_mode_warns_on_exceed(self):
        """测试 SOFT 模式软超限警告 + 继续"""
        guard = TokenBudgetGuard(default_mode=BudgetEnforcementMode.SOFT)
        b = guard.create_budget(total=100_000)
        b.consumed = 80_000
        decision = guard.record_consumption(b, consumed=30_000)
        # 软模式不抛异常
        self.assertTrue(decision.allow_continue)
        self.assertEqual(decision.recommendation, BudgetRecommendation.SWITCH_TO_HAIKU)

    def test_hybrid_mode_raises_at_hard(self):
        """测试 HYBRID 模式硬阈值抛异常"""
        guard = TokenBudgetGuard(default_mode=BudgetEnforcementMode.HYBRID)
        b = guard.create_budget(total=100_000)
        b.consumed = 80_000
        with self.assertRaises(TokenBudgetExceeded):
            guard.record_consumption(b, consumed=30_000)

    def test_hybrid_mode_warns_at_soft(self):
        """测试 HYBRID 模式软阈值警告"""
        guard = TokenBudgetGuard(default_mode=BudgetEnforcementMode.HYBRID)
        b = guard.create_budget(total=100_000)
        decision = guard.record_consumption(b, consumed=85_000)  # 85% > 80%
        self.assertTrue(decision.allow_continue)
        self.assertEqual(decision.recommendation, BudgetRecommendation.SWITCH_TO_HAIKU)

    def test_hard_mode_under_soft_threshold_no_warning(self):
        """测试 HARD 模式软阈值下无警告"""
        guard = TokenBudgetGuard(default_mode=BudgetEnforcementMode.HARD)
        b = guard.create_budget(total=100_000)
        decision = guard.record_consumption(b, consumed=50_000)  # 50% < 80%
        self.assertEqual(decision.recommendation, BudgetRecommendation.CONTINUE)
        self.assertEqual(len(decision.warnings), 0)


# ============================================================================
# 测试 6: 画像反哺
# ============================================================================

class TestGuardFingerprintIntegration(unittest.TestCase):
    """画像反哺测试"""

    def setUp(self):
        self.mock_fp = MagicMock()
        self.guard = TokenBudgetGuard(fingerprint=self.mock_fp)

    def test_post_execute_writes_to_fingerprint(self):
        """测试后审写入画像"""
        b = self.guard.create_budget(total=100_000)
        b.consumed = 50_000
        self.guard.post_execute_review(b, success=True, task_type="code_review")
        # 验证 mock 收到 record 调用
        self.mock_fp.record.assert_called_once()
        call_kwargs = self.mock_fp.record.call_args.kwargs
        self.assertEqual(call_kwargs["task_type"], "code_review")
        self.assertTrue(call_kwargs["success"])
        self.assertEqual(call_kwargs["context_features"]["total_budget"], 100_000)

    def test_post_execute_with_failure_records_error(self):
        """测试失败任务写入画像"""
        b = self.guard.create_budget(total=100_000)
        b.consumed = 100_000  # 硬超限
        self.guard.post_execute_review(b, success=False)
        call_kwargs = self.mock_fp.record.call_args.kwargs
        self.assertFalse(call_kwargs["success"])
        self.assertEqual(call_kwargs["error_type"], "budget_exhausted")

    def test_post_execute_without_fingerprint_does_not_raise(self):
        """测试无画像时后审不抛异常"""
        guard = TokenBudgetGuard()  # 无 fingerprint
        b = guard.create_budget(total=100_000)
        b.consumed = 50_000
        guard.post_execute_review(b, success=True)  # 不应抛异常


# ============================================================================
# 测试 7: GuardCoordinator 兼容接口
# ============================================================================

class TestGuardCoordinatorCompatibility(unittest.TestCase):
    """GuardCoordinator 兼容接口测试"""

    def setUp(self):
        self.guard = TokenBudgetGuard()

    def test_validate_with_valid_budget(self):
        """测试有效预算的 validate"""
        result = self.guard.validate({
            "token_budget": 100_000,
            "estimated_tokens": 50_000,
        })
        self.assertTrue(result.passed)
        self.assertEqual(len(result.warnings), 0)

    def test_validate_without_budget_raises(self):
        """测试无 token_budget 时不通过"""
        result = self.guard.validate({
            "description": "test",
        })
        self.assertFalse(result.passed)
        self.assertEqual(result.risk_level.value, "high")
        self.assertGreater(len(result.warnings), 0)

    def test_validate_with_invalid_budget_type(self):
        """测试非法 budget 类型"""
        result = self.guard.validate({
            "token_budget": "not_int",
        })
        self.assertFalse(result.passed)

    def test_validate_with_over_budget(self):
        """测试超额预算的 validate"""
        result = self.guard.validate({
            "token_budget": 100_000,
            "estimated_tokens": 150_000,
        })
        self.assertFalse(result.passed)
        self.assertIn("split_task", str([c.strategy_id for c in result.recommended_compensations]))

    def test_validate_with_soft_warning(self):
        """测试软警告的 validate"""
        result = self.guard.validate({
            "token_budget": 100_000,
            "estimated_tokens": 85_000,
        })
        self.assertTrue(result.passed)
        # 应该有软警告
        self.assertGreater(len(result.warnings), 0)


# ============================================================================
# 测试 8: 错误路径
# ============================================================================

class TestGuardErrorPaths(unittest.TestCase):
    """错误路径测试"""

    def setUp(self):
        self.guard = TokenBudgetGuard()

    def test_decision_history_returns_list(self):
        """测试决策历史返回列表"""
        b = self.guard.create_budget(total=100_000)
        self.guard.pre_execute_check(b, estimated_tokens=50_000)
        history = self.guard.get_decision_history()
        self.assertGreater(len(history), 0)

    def test_decision_history_capped(self):
        """测试决策历史有上限"""
        for i in range(600):
            b = self.guard.create_budget(total=100_000)
            self.guard.pre_execute_check(b, estimated_tokens=50_000)
        history = self.guard.get_decision_history()
        # 上限 500
        self.assertLessEqual(len(history), 500)

    def test_complexity_estimation(self):
        """测试复杂度估算"""
        b = self.guard.create_budget(total=100_000)

        # 消费 10% → 简单
        b.consumed = 10_000
        c1 = self.guard._estimate_complexity(b)
        self.assertLessEqual(c1, 3)

        # 消费 80% → 高
        b.consumed = 80_000
        c2 = self.guard._estimate_complexity(b)
        self.assertGreaterEqual(c2, 7)


# ============================================================================
# 测试 9: 并发
# ============================================================================

class TestGuardConcurrency(unittest.TestCase):
    """并发测试"""

    def test_concurrent_create_budget(self):
        """测试并发创建预算"""
        guard = TokenBudgetGuard()
        errors = []

        def create_budget(i):
            try:
                b = guard.create_budget(total=100_000, task_id=f"task_{i}")
                guard.pre_execute_check(b, estimated_tokens=50_000)
                guard.record_consumption(b, consumed=10_000)
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(50):
            t = threading.Thread(target=create_budget, args=(i,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)

    def test_concurrent_consumption_same_budget(self):
        """测试并发消费同一预算（线程安全）"""
        guard = TokenBudgetGuard()
        b = guard.create_budget(total=1_000_000)

        def consume():
            for _ in range(10):
                try:
                    guard.record_consumption(b, consumed=100)
                except TokenBudgetExceeded:
                    pass

        threads = [threading.Thread(target=consume) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 100 次消费 × 100 = 10_000
        self.assertEqual(b.consumed, 10_000)


# ============================================================================
# 测试 10: 性能
# ============================================================================

class TestGuardPerformance(unittest.TestCase):
    """性能基线"""

    def test_pre_execute_under_5ms(self):
        """测试预检 < 5ms"""
        guard = TokenBudgetGuard()
        b = guard.create_budget(total=100_000)
        # 预热
        guard.pre_execute_check(b, estimated_tokens=50_000)

        start = time.time()
        for _ in range(100):
            guard.pre_execute_check(b, estimated_tokens=50_000)
        elapsed_ms = (time.time() - start) * 1000 / 100
        self.assertLess(elapsed_ms, 5, f"平均预检耗时 {elapsed_ms:.2f}ms 超过 5ms")

    def test_record_consumption_under_5ms(self):
        """测试消费记录 < 5ms"""
        guard = TokenBudgetGuard()
        b = guard.create_budget(total=1_000_000)
        # 预热
        guard.record_consumption(b, consumed=100)

        start = time.time()
        for _ in range(100):
            try:
                guard.record_consumption(b, consumed=100)
            except TokenBudgetExceeded:
                b.consumed = 0  # 重置
        elapsed_ms = (time.time() - start) * 1000 / 100
        self.assertLess(elapsed_ms, 5, f"平均消费记录耗时 {elapsed_ms:.2f}ms 超过 5ms")


# ============================================================================
# 测试入口
# ============================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
