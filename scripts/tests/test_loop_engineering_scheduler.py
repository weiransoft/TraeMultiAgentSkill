"""LoopScheduler 单元测试。"""

import unittest

from loop_engineering.loop_scheduler import LoopScheduler
from loop_engineering.models import (
    EvaluationVerdict,
    LoopEngineeringConfig,
    LoopEvent,
    LoopEventType,
    SchedulingAction,
)


class TestLoopSchedulerDecisions(unittest.TestCase):
    """测试 LoopScheduler 决策逻辑。"""

    def setUp(self):
        """每个测试用例前创建 Scheduler。"""
        self.config = LoopEngineeringConfig(
            max_iterations=10,
            max_tokens=1000,
            human_checkpoint_every=3,
        )
        self.scheduler = LoopScheduler(self.config)
        self.passed_verdict = EvaluationVerdict(passed=True, reason="通过")
        self.failed_verdict = EvaluationVerdict(passed=False, reason="未通过")

    def test_passed_continue(self):
        """验证通过且无停止条件时继续。"""
        decision = self.scheduler.decide_next(
            current_iter=0,
            verdict=self.passed_verdict,
            memory_events=[],
            cumulative_tokens=100,
            consecutive_failures=0,
        )
        self.assertEqual(decision.action, SchedulingAction.CONTINUE)

    def test_failed_fix(self):
        """验证未通过时进入 fix。"""
        decision = self.scheduler.decide_next(
            current_iter=0,
            verdict=self.failed_verdict,
            memory_events=[],
            cumulative_tokens=100,
            consecutive_failures=0,
        )
        self.assertEqual(decision.action, SchedulingAction.FIX)

    def test_token_budget_stop(self):
        """Token 预算耗尽时停止失败。"""
        decision = self.scheduler.decide_next(
            current_iter=0,
            verdict=self.passed_verdict,
            memory_events=[],
            cumulative_tokens=1000,
            consecutive_failures=0,
        )
        self.assertEqual(decision.action, SchedulingAction.STOP_FAILURE)
        self.assertIn("Token", decision.reason)

    def test_max_iterations_stop_failure(self):
        """达到最大迭代次数且未通过时失败停止。"""
        decision = self.scheduler.decide_next(
            current_iter=9,
            verdict=self.failed_verdict,
            memory_events=[],
            cumulative_tokens=100,
            consecutive_failures=0,
        )
        self.assertEqual(decision.action, SchedulingAction.STOP_FAILURE)

    def test_max_iterations_stop_success(self):
        """达到最大迭代次数且通过时成功停止。"""
        decision = self.scheduler.decide_next(
            current_iter=9,
            verdict=self.passed_verdict,
            memory_events=[],
            cumulative_tokens=100,
            consecutive_failures=0,
        )
        self.assertEqual(decision.action, SchedulingAction.STOP_SUCCESS)

    def test_consecutive_failures_stop(self):
        """连续失败达到上限时停止失败。"""
        decision = self.scheduler.decide_next(
            current_iter=2,
            verdict=self.failed_verdict,
            memory_events=[],
            cumulative_tokens=100,
            consecutive_failures=5,
        )
        self.assertEqual(decision.action, SchedulingAction.STOP_FAILURE)

    def test_human_checkpoint_interval(self):
        """固定间隔触发人类检查点。"""
        decision = self.scheduler.decide_next(
            current_iter=2,  # 第 3 轮，3 的倍数
            verdict=self.passed_verdict,
            memory_events=[],
            cumulative_tokens=100,
            consecutive_failures=0,
        )
        self.assertEqual(decision.action, SchedulingAction.HUMAN_CHECKPOINT)
        self.assertTrue(decision.requires_human_input)

    def test_high_risk_event_checkpoint(self):
        """高风险事件触发人类检查点。"""
        events = [
            LoopEvent(
                event_id="e1",
                event_type=LoopEventType.VERIFICATION_REJECTED,
                phase="verification",
                run_id="r1",
                iter_index=0,
                payload={"severity": "blocker"},
            )
        ]
        decision = self.scheduler.decide_next(
            current_iter=0,
            verdict=self.passed_verdict,
            memory_events=events,
            cumulative_tokens=100,
            consecutive_failures=0,
        )
        self.assertEqual(decision.action, SchedulingAction.HUMAN_CHECKPOINT)

    def test_stop_when_keyword(self):
        """stop_when 包含完成关键词且验证通过时停止。"""
        config = LoopEngineeringConfig(
            max_iterations=10,
            stop_when="所有测试通过",
        )
        scheduler = LoopScheduler(config)
        events = [
            LoopEvent(
                event_id="e1",
                event_type=LoopEventType.VERIFICATION_PASSED,
                phase="verification",
                run_id="r1",
                iter_index=0,
                payload={},
            )
        ]
        decision = scheduler.decide_next(
            current_iter=0,
            verdict=self.passed_verdict,
            memory_events=events,
            cumulative_tokens=100,
            consecutive_failures=0,
        )
        self.assertEqual(decision.action, SchedulingAction.STOP_SUCCESS)

    def test_loop_completed_event_stop(self):
        """历史事件中有 LOOP_COMPLETED 时停止。"""
        events = [
            LoopEvent(
                event_id="e1",
                event_type=LoopEventType.LOOP_COMPLETED,
                phase="scheduling",
                run_id="r1",
                iter_index=0,
                payload={},
            )
        ]
        decision = self.scheduler.decide_next(
            current_iter=0,
            verdict=self.passed_verdict,
            memory_events=events,
            cumulative_tokens=100,
            consecutive_failures=0,
        )
        self.assertEqual(decision.action, SchedulingAction.STOP_SUCCESS)


class TestLoopSchedulerBackoff(unittest.TestCase):
    """测试退避计算。"""

    def test_backoff_exponential(self):
        """退避时间指数增长。"""
        config = LoopEngineeringConfig(
            extra={"backoff_base_sec": 1.0, "backoff_max_sec": 30.0}
        )
        scheduler = LoopScheduler(config)
        self.assertEqual(scheduler.compute_backoff(0), 1.0)
        self.assertEqual(scheduler.compute_backoff(1), 2.0)
        self.assertEqual(scheduler.compute_backoff(2), 4.0)
        self.assertEqual(scheduler.compute_backoff(5), 30.0)  # 达到上限


if __name__ == "__main__":
    unittest.main()
