"""Loop Engineering 数据模型单元测试。"""

import unittest
from pathlib import Path

from loop_engineering.models import (
    DiscoveryMode,
    DiscoveryResult,
    EvaluationVerdict,
    EvaluatorMode,
    HandoffItem,
    HumanCheckpointResponse,
    LoopEngineeringConfig,
    LoopEvent,
    LoopEventType,
    LoopRunReport,
    LoopType,
    MemoryQuery,
    SchedulingAction,
    SchedulingDecision,
)


class TestEnums(unittest.TestCase):
    """测试 Enum 值。"""

    def test_loop_type_values(self):
        """LoopType 枚举值正确。"""
        self.assertEqual(LoopType.DESIGN.value, "design")
        self.assertEqual(LoopType.CODING.value, "coding")
        self.assertEqual(LoopType.TESTING.value, "testing")

    def test_discovery_mode_values(self):
        """DiscoveryMode 枚举值正确。"""
        self.assertEqual(DiscoveryMode.AUTO.value, "auto")
        self.assertEqual(DiscoveryMode.MANUAL.value, "manual")
        self.assertEqual(DiscoveryMode.OFF.value, "off")

    def test_evaluator_mode_values(self):
        """EvaluatorMode 枚举值正确。"""
        self.assertEqual(EvaluatorMode.STRICT.value, "strict")
        self.assertEqual(EvaluatorMode.STANDARD.value, "standard")
        self.assertEqual(EvaluatorMode.OFF.value, "off")

    def test_scheduling_action_values(self):
        """SchedulingAction 枚举值正确。"""
        self.assertEqual(SchedulingAction.CONTINUE.value, "continue")
        self.assertEqual(SchedulingAction.FIX.value, "fix")
        self.assertEqual(SchedulingAction.HUMAN_CHECKPOINT.value, "human_checkpoint")
        self.assertEqual(SchedulingAction.STOP_SUCCESS.value, "stop_success")
        self.assertEqual(SchedulingAction.STOP_FAILURE.value, "stop_failure")


class TestLoopEngineeringConfig(unittest.TestCase):
    """测试 LoopEngineeringConfig。"""

    def test_default_values(self):
        """默认值符合预期。"""
        cfg = LoopEngineeringConfig()
        self.assertEqual(cfg.loop_type, LoopType.CODING)
        self.assertEqual(cfg.discovery_mode, DiscoveryMode.AUTO)
        self.assertEqual(cfg.evaluator_mode, EvaluatorMode.STRICT)
        self.assertEqual(cfg.max_iterations, 50)
        self.assertEqual(cfg.max_tokens, 500_000)
        self.assertEqual(cfg.human_checkpoint_every, 5)
        self.assertAlmostEqual(cfg.sampling_read_ratio, 0.1)
        self.assertTrue(cfg.project_root.is_absolute())

    def test_post_init_string_project_root(self):
        """project_root 传入字符串时自动转 Path。"""
        cfg = LoopEngineeringConfig(project_root="/tmp/test")
        self.assertIsInstance(cfg.project_root, Path)
        self.assertEqual(str(cfg.project_root), "/tmp/test")

    def test_invalid_max_iterations(self):
        """max_iterations 非法应抛 ValueError。"""
        with self.assertRaises(ValueError):
            LoopEngineeringConfig(max_iterations=0)

    def test_invalid_max_tokens(self):
        """max_tokens 非法应抛 ValueError。"""
        with self.assertRaises(ValueError):
            LoopEngineeringConfig(max_tokens=-1)

    def test_invalid_sampling_ratio(self):
        """sampling_read_ratio 越界应抛 ValueError。"""
        with self.assertRaises(ValueError):
            LoopEngineeringConfig(sampling_read_ratio=1.5)
        with self.assertRaises(ValueError):
            LoopEngineeringConfig(sampling_read_ratio=-0.1)

    def test_invalid_human_checkpoint_interval(self):
        """human_checkpoint_every 为负数应抛 ValueError。"""
        with self.assertRaises(ValueError):
            LoopEngineeringConfig(human_checkpoint_every=-1)

    def test_stage_order_isolated(self):
        """不同实例的 stage_order 应相互隔离。"""
        cfg1 = LoopEngineeringConfig()
        cfg2 = LoopEngineeringConfig()
        cfg1.stage_order.append("extra")
        self.assertNotIn("extra", cfg2.stage_order)


class TestDiscoveryResult(unittest.TestCase):
    """测试 DiscoveryResult。"""

    def test_default_values(self):
        """默认值符合预期。"""
        dr = DiscoveryResult()
        self.assertEqual(dr.objective, "")
        self.assertTrue(dr.worktree_required)
        self.assertIsInstance(dr.relevant_skills, list)
        self.assertIsInstance(dr.detected_risks, list)

    def test_timestamp_auto_set(self):
        """timestamp 自动设置。"""
        dr = DiscoveryResult()
        self.assertTrue(dr.timestamp)
        self.assertIn("T", dr.timestamp)


class TestHandoffItem(unittest.TestCase):
    """测试 HandoffItem。"""

    def test_creation(self):
        """正常构造。"""
        item = HandoffItem(
            item_id="wi-1",
            agent_type="architect",
            task="设计认证模块",
            acceptance_criteria=["覆盖需求", "无技术债"],
        )
        self.assertEqual(item.item_id, "wi-1")
        self.assertEqual(item.agent_type, "architect")
        self.assertEqual(len(item.acceptance_criteria), 2)


class TestEvaluationVerdict(unittest.TestCase):
    """测试 EvaluationVerdict。"""

    def test_default_not_passed(self):
        """默认未通过（保守默认）。"""
        v = EvaluationVerdict()
        self.assertFalse(v.passed)
        self.assertEqual(v.severity, "info")

    def test_passed_verdict(self):
        """构造通过判定。"""
        v = EvaluationVerdict(
            passed=True,
            evaluator_id="independent-reviewer",
            reason="所有验收标准满足",
        )
        self.assertTrue(v.passed)
        self.assertEqual(v.evaluator_id, "independent-reviewer")


class TestLoopEvent(unittest.TestCase):
    """测试 LoopEvent。"""

    def test_default_event_type(self):
        """默认事件类型。"""
        e = LoopEvent()
        self.assertEqual(e.event_type, LoopEventType.DISCOVERY_STARTED)
        self.assertTrue(e.timestamp)


class TestMemoryQuery(unittest.TestCase):
    """测试 MemoryQuery。"""

    def test_default_query_type(self):
        """默认查询类型为 recent。"""
        q = MemoryQuery()
        self.assertEqual(q.query_type, "recent")
        self.assertEqual(q.limit, 10)


class TestSchedulingDecision(unittest.TestCase):
    """测试 SchedulingDecision。"""

    def test_default_stop_success(self):
        """默认动作为 stop_success。"""
        d = SchedulingDecision()
        self.assertEqual(d.action, SchedulingAction.STOP_SUCCESS)

    def test_continue_with_backoff(self):
        """continue 动作可携带退避时间。"""
        d = SchedulingDecision(
            action=SchedulingAction.CONTINUE,
            reason="验证未通过，继续修复",
            backoff_seconds=2.0,
        )
        self.assertEqual(d.action, SchedulingAction.CONTINUE)
        self.assertEqual(d.backoff_seconds, 2.0)


class TestHumanCheckpointResponse(unittest.TestCase):
    """测试 HumanCheckpointResponse。"""

    def test_approve(self):
        """批准继续。"""
        r = HumanCheckpointResponse(approved=True, feedback="继续")
        self.assertTrue(r.approved)
        self.assertFalse(r.abort)

    def test_abort(self):
        """中止 Loop。"""
        r = HumanCheckpointResponse(approved=False, abort=True)
        self.assertFalse(r.approved)
        self.assertTrue(r.abort)


class TestLoopRunReport(unittest.TestCase):
    """测试 LoopRunReport。"""

    def test_default_status(self):
        """默认状态为空。"""
        report = LoopRunReport()
        self.assertEqual(report.final_status, "")
        self.assertIsInstance(report.events, list)


if __name__ == "__main__":
    unittest.main()
