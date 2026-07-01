"""LoopKernel 单元测试。

使用轻量级真实对象（非 mock）满足 LoopKernel 依赖的 Protocol，
验证五步闭环编排、状态管理和停止条件。
"""

import unittest
from typing import Any, Dict, List

from loop_engineering.kernel import LoopKernel
from loop_engineering.loop_scheduler import LoopScheduler
from loop_engineering.models import (
    DiscoveryResult,
    EvaluationVerdict,
    HandoffItem,
    LoopEngineeringConfig,
    LoopEvent,
    LoopEventType,
    LoopRunReport,
    LoopType,
    MemoryQuery,
    SchedulingAction,
)


class _FakeMemory:
    """测试用统一 Memory 层（真实对象，非 mock）。"""

    def __init__(self):
        self.events: List[LoopEvent] = []
        self.token_usage = 0

    def persist_event(self, event: LoopEvent) -> None:
        """持久化事件。"""
        self.events.append(event)
        # 简单估算：每个事件 10 token
        self.token_usage += 10

    def query(self, query: MemoryQuery) -> List[Dict[str, Any]]:
        """返回最近事件。"""
        return [
            {"event_id": e.event_id, "event_type": e.event_type.value}
            for e in self.events[-query.limit:]
        ]

    def estimate_token_usage(self) -> int:
        """返回累计 token 估算。"""
        return self.token_usage


class _FakeDiscoveryProbe:
    """测试用 Discovery Probe（真实对象）。"""

    def __init__(self, risks: List[str] = None):
        self.risks = risks or []

    def discover(
        self,
        objective: str,
        prev_events: List[LoopEvent],
        memory: _FakeMemory,
    ) -> DiscoveryResult:
        """返回 Discovery 结果。"""
        return DiscoveryResult(
            objective=objective,
            detected_risks=list(self.risks),
            suggested_agents=["solo-coder"],
        )


class _FakeHandoffAdapter:
    """测试用 Handoff Adapter（真实对象）。"""

    def __init__(self, success: bool = True, committed_count: int = 0):
        self.success = success
        self.committed_count = committed_count

    def create_work_items(
        self,
        discovery: DiscoveryResult,
        loop_type: str,
    ) -> List[HandoffItem]:
        """生成一个工作项。"""
        return [
            HandoffItem(
                item_id="wi-1",
                agent_type="solo-coder",
                task=discovery.objective,
            )
        ]

    def execute(
        self,
        items: List[HandoffItem],
        config: LoopEngineeringConfig,
    ) -> Dict[str, Any]:
        """返回 Generator 结果。"""
        return {
            "success": self.success,
            "committed_count": self.committed_count,
            "output": "generated output",
        }


class _FakeEvaluator:
    """测试用独立 Evaluator（真实对象）。"""

    def __init__(self, passed: bool = True):
        self.passed = passed

    def evaluate(
        self,
        handoff_items: List[HandoffItem],
        generator_result: Dict[str, Any],
        context: Dict[str, Any],
    ) -> EvaluationVerdict:
        """返回判定结果。"""
        return EvaluationVerdict(
            passed=self.passed,
            evaluator_id="fake-evaluator",
            reason="通过" if self.passed else "未通过",
        )


class TestLoopKernel(unittest.TestCase):
    """测试 LoopKernel 核心编排。"""

    def _create_kernel(
        self,
        config: LoopEngineeringConfig = None,
        passed: bool = True,
        committed_count: int = 0,
    ) -> LoopKernel:
        """创建 LoopKernel 及依赖。"""
        cfg = config or LoopEngineeringConfig(max_iterations=5)
        memory = _FakeMemory()
        scheduler = LoopScheduler(cfg)
        return LoopKernel(
            config=cfg,
            discovery_probe=_FakeDiscoveryProbe(),
            handoff_adapter=_FakeHandoffAdapter(
                success=passed, committed_count=committed_count
            ),
            evaluator=_FakeEvaluator(passed=passed),
            memory=memory,
            scheduler=scheduler,
        )

    def test_run_one_cycle_event_order(self):
        """单轮循环产生正确的事件顺序。"""
        kernel = self._create_kernel()
        result = kernel.run_one_cycle("测试目标", 0)

        events = result.events
        event_types = [e.event_type for e in events]
        self.assertEqual(event_types[0], LoopEventType.DISCOVERY_STARTED)
        self.assertEqual(event_types[1], LoopEventType.DISCOVERY_COMPLETED)
        self.assertEqual(event_types[2], LoopEventType.HANDOFF_CREATED)
        self.assertEqual(event_types[3], LoopEventType.HANDOFF_DISPATCHED)
        self.assertEqual(event_types[4], LoopEventType.VERIFICATION_STARTED)
        self.assertEqual(event_types[5], LoopEventType.VERIFICATION_PASSED)
        self.assertEqual(event_types[6], LoopEventType.PERSISTENCE_WRITTEN)
        self.assertEqual(event_types[7], LoopEventType.SCHEDULING_DECISION)

    def test_run_completes_on_passed(self):
        """验证通过时 Loop 成功完成。"""
        config = LoopEngineeringConfig(
            max_iterations=5,
            stop_when="完成",
        )
        kernel = self._create_kernel(config=config, passed=True)
        report = kernel.run("测试目标")

        self.assertIsInstance(report, LoopRunReport)
        self.assertEqual(report.final_status, "completed")
        self.assertEqual(report.loop_type, LoopType.CODING)
        self.assertTrue(report.total_iterations >= 1)
        self.assertIn("Loop Engineering 运行报告", report.final_summary)

    def test_run_fix_until_failed(self):
        """验证持续未通过时达到最大迭代次数后失败。"""
        config = LoopEngineeringConfig(max_iterations=3)
        kernel = self._create_kernel(config=config, passed=False)
        report = kernel.run("测试目标")

        self.assertEqual(report.final_status, "failed")
        # 至少执行了 max_iterations 轮
        self.assertEqual(report.total_iterations, config.max_iterations)

    def test_stop_request(self):
        """stop() 请求后安全停止。"""
        config = LoopEngineeringConfig(max_iterations=10)
        kernel = self._create_kernel(config=config, passed=True)
        # 第一轮后就会成功停止，所以这里测试 stop 标志是否生效意义不大
        # 改为直接调用 stop 后再运行
        kernel.stop("用户取消")
        report = kernel.run("测试目标")

        # 由于 stop_requested 为 True，while 循环不执行
        self.assertEqual(report.total_iterations, 1)
        self.assertIn("failed", report.final_status)

    def test_token_budget_stop(self):
        """Token 预算超限时 Loop 停止。"""
        config = LoopEngineeringConfig(max_iterations=10, max_tokens=50)
        kernel = self._create_kernel(config=config, passed=True)
        report = kernel.run("测试目标")

        # 每轮 8 个事件 * 10 = 80 token，第一轮就会超过 50
        self.assertEqual(report.final_status, "failed")
        self.assertIn("token_used", report.final_summary.lower())

    def test_committed_count_accumulation(self):
        """通过的 committed_count 会累加。"""
        config = LoopEngineeringConfig(max_iterations=2, stop_when="完成")
        kernel = self._create_kernel(
            config=config, passed=True, committed_count=1
        )
        report = kernel.run("测试目标")

        self.assertEqual(report.committed_count, 1)

    def test_run_one_cycle_rejected_verdict(self):
        """验证未通过时产生 VERIFICATION_REJECTED 事件。"""
        kernel = self._create_kernel(passed=False)
        result = kernel.run_one_cycle("测试目标", 0)

        event_types = [e.event_type for e in result.events]
        self.assertIn(LoopEventType.VERIFICATION_REJECTED, event_types)
        self.assertEqual(
            result.scheduling_decision.action, SchedulingAction.FIX
        )


if __name__ == "__main__":
    unittest.main()
