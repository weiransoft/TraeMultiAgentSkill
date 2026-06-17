#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ModelRouter 单元测试

测试覆盖：
1. 数据类（ModelProfile / TaskFeature / RoutingDecision）字段 + 校验
2. 静态规则决策（3 档复杂度 + 关键路径）
3. 画像反哺（冷启动 / 样本充足 / 一致 / 不一致）
4. 错误路径（越界 / 负值 / 未知 tier）
5. 并发安全 + 性能基线
"""

import sys
import os
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

from model_router import (
    ModelTier,
    ModelProfile,
    TaskFeature,
    RoutingDecision,
    ModelRouter,
    ModelRouterError,
    InvalidTaskFeatureError,
    ModelTierNotFoundError,
    DEFAULT_PROFILES,
    HIGH_COMPLEXITY_THRESHOLD,
    BUDGET_EXHAUSTED_THRESHOLD,
    TIGHT_DEADLINE_MS,
    MIN_FINGERPRINT_SAMPLES,
)


# ============================================================================
# 测试 1: ModelProfile 数据类
# ============================================================================

class TestModelProfile(unittest.TestCase):
    """ModelProfile 数据类测试"""

    def test_default_haiku_profile(self):
        """测试 haiku 默认画像"""
        p = DEFAULT_PROFILES[ModelTier.HAIKU]
        self.assertEqual(p.tier, ModelTier.HAIKU)
        self.assertEqual(p.cost_per_1k_tokens, 0.25)
        self.assertEqual(p.quality_score, 0.70)
        self.assertEqual(p.speed_score, 1.0)
        self.assertGreater(p.max_context_tokens, 0)
        self.assertTrue(len(p.description) > 0)

    def test_default_sonnet_profile(self):
        """测试 sonnet 默认画像"""
        p = DEFAULT_PROFILES[ModelTier.SONNET]
        self.assertEqual(p.tier, ModelTier.SONNET)
        self.assertEqual(p.cost_per_1k_tokens, 1.0)
        self.assertEqual(p.quality_score, 0.85)
        self.assertEqual(p.speed_score, 0.6)

    def test_default_opus_profile(self):
        """测试 opus 默认画像"""
        p = DEFAULT_PROFILES[ModelTier.OPUS]
        self.assertEqual(p.tier, ModelTier.OPUS)
        self.assertEqual(p.cost_per_1k_tokens, 5.0)
        self.assertEqual(p.quality_score, 0.95)
        self.assertEqual(p.speed_score, 0.3)

    def test_quality_score_out_of_range(self):
        """测试 quality_score 越界抛异常"""
        with self.assertRaises(ModelRouterError):
            ModelProfile(
                tier=ModelTier.HAIKU,
                cost_per_1k_tokens=0.25,
                quality_score=1.5,  # 越界
                speed_score=1.0,
                max_context_tokens=200_000,
                description="test",
            )

    def test_negative_cost_rejected(self):
        """测试负成本被拒绝"""
        with self.assertRaises(ModelRouterError):
            ModelProfile(
                tier=ModelTier.HAIKU,
                cost_per_1k_tokens=-0.1,  # 负值
                quality_score=0.7,
                speed_score=1.0,
                max_context_tokens=200_000,
                description="test",
            )

    def test_max_context_must_be_positive(self):
        """测试 max_context_tokens 必须为正"""
        with self.assertRaises(ModelRouterError):
            ModelProfile(
                tier=ModelTier.HAIKU,
                cost_per_1k_tokens=0.25,
                quality_score=0.7,
                speed_score=1.0,
                max_context_tokens=0,  # 非正
                description="test",
            )


# ============================================================================
# 测试 2: TaskFeature 数据类
# ============================================================================

class TestTaskFeature(unittest.TestCase):
    """TaskFeature 数据类测试"""

    def test_minimal_valid_feature(self):
        """测试最小有效特征（只填必填字段）"""
        f = TaskFeature(task_complexity=5, estimated_tokens=1000)
        self.assertEqual(f.task_complexity, 5)
        self.assertEqual(f.estimated_tokens, 1000)
        self.assertIsNone(f.role)
        self.assertIsNone(f.deadline_ms)
        self.assertEqual(f.quality_threshold, 0.85)
        self.assertEqual(f.budget_remaining, 1.0)
        self.assertFalse(f.is_critical)
        self.assertEqual(f.task_type, "general")

    def test_full_feature(self):
        """测试完整特征"""
        f = TaskFeature(
            task_complexity=8,
            estimated_tokens=50_000,
            role="architect",
            deadline_ms=10_000,
            quality_threshold=0.9,
            budget_remaining=0.5,
            is_critical=True,
            task_type="design",
        )
        self.assertEqual(f.task_complexity, 8)
        self.assertEqual(f.role, "architect")
        self.assertTrue(f.is_critical)

    def test_complexity_out_of_range(self):
        """测试复杂度越界"""
        with self.assertRaises(InvalidTaskFeatureError):
            TaskFeature(task_complexity=0, estimated_tokens=1000)
        with self.assertRaises(InvalidTaskFeatureError):
            TaskFeature(task_complexity=11, estimated_tokens=1000)

    def test_estimated_tokens_must_be_positive(self):
        """测试 estimated_tokens 必须为正"""
        with self.assertRaises(InvalidTaskFeatureError):
            TaskFeature(task_complexity=5, estimated_tokens=0)
        with self.assertRaises(InvalidTaskFeatureError):
            TaskFeature(task_complexity=5, estimated_tokens=-1)

    def test_quality_threshold_out_of_range(self):
        """测试质量阈值越界"""
        with self.assertRaises(InvalidTaskFeatureError):
            TaskFeature(task_complexity=5, estimated_tokens=1000, quality_threshold=1.5)
        with self.assertRaises(InvalidTaskFeatureError):
            TaskFeature(task_complexity=5, estimated_tokens=1000, quality_threshold=-0.1)

    def test_budget_remaining_out_of_range(self):
        """测试预算剩余越界"""
        with self.assertRaises(InvalidTaskFeatureError):
            TaskFeature(task_complexity=5, estimated_tokens=1000, budget_remaining=1.5)
        with self.assertRaises(InvalidTaskFeatureError):
            TaskFeature(task_complexity=5, estimated_tokens=1000, budget_remaining=-0.1)

    def test_deadline_negative(self):
        """测试负截止时间被拒绝"""
        with self.assertRaises(InvalidTaskFeatureError):
            TaskFeature(task_complexity=5, estimated_tokens=1000, deadline_ms=-1)

    def test_to_dict(self):
        """测试转字典"""
        f = TaskFeature(
            task_complexity=5,
            estimated_tokens=1000,
            role="test",
        )
        d = f.to_dict()
        self.assertEqual(d["task_complexity"], 5)
        self.assertEqual(d["estimated_tokens"], 1000)
        self.assertEqual(d["role"], "test")


# ============================================================================
# 测试 3: RoutingDecision 数据类
# ============================================================================

class TestRoutingDecision(unittest.TestCase):
    """RoutingDecision 数据类测试"""

    def test_basic_decision(self):
        """测试基础决策"""
        d = RoutingDecision(
            selected_tier=ModelTier.SONNET,
            confidence=0.85,
            reasoning="测试",
            alternatives=[ModelTier.HAIKU, ModelTier.OPUS],
        )
        self.assertEqual(d.selected_tier, ModelTier.SONNET)
        self.assertEqual(d.confidence, 0.85)
        self.assertEqual(d.reasoning, "测试")
        self.assertEqual(d.decision_source, "static_rule")

    def test_confidence_out_of_range(self):
        """测试置信度越界"""
        with self.assertRaises(ModelRouterError):
            RoutingDecision(
                selected_tier=ModelTier.SONNET,
                confidence=1.5,  # 越界
                reasoning="test",
            )
        with self.assertRaises(ModelRouterError):
            RoutingDecision(
                selected_tier=ModelTier.SONNET,
                confidence=-0.1,  # 越界
                reasoning="test",
            )

    def test_empty_reasoning_rejected(self):
        """测试空 reason 被拒绝"""
        with self.assertRaises(ModelRouterError):
            RoutingDecision(
                selected_tier=ModelTier.SONNET,
                confidence=0.85,
                reasoning="",  # 空
            )

    def test_to_dict(self):
        """测试转字典"""
        d = RoutingDecision(
            selected_tier=ModelTier.OPUS,
            confidence=0.95,
            reasoning="测试",
            alternatives=[ModelTier.SONNET],
        )
        result = d.to_dict()
        self.assertEqual(result["selected_tier"], "opus")
        self.assertEqual(result["confidence"], 0.95)
        self.assertEqual(result["reasoning"], "测试")
        self.assertEqual(result["alternatives"], ["sonnet"])

    def test_model_tier_from_str(self):
        """测试 ModelTier.from_str"""
        self.assertEqual(ModelTier.from_str("haiku"), ModelTier.HAIKU)
        self.assertEqual(ModelTier.from_str("SONNET"), ModelTier.SONNET)
        self.assertEqual(ModelTier.from_str("Opus"), ModelTier.OPUS)
        with self.assertRaises(ModelTierNotFoundError):
            ModelTier.from_str("gpt-4")


# ============================================================================
# 测试 4: ModelRouter 基础路由
# ============================================================================

class TestModelRouterBasic(unittest.TestCase):
    """ModelRouter 基础路由测试"""

    def setUp(self):
        """每个测试前创建新 router"""
        self.router = ModelRouter()

    def test_low_complexity_routes_to_haiku(self):
        """测试低复杂度（1-3）路由到 haiku"""
        for complexity in [1, 2, 3]:
            with self.subTest(complexity=complexity):
                decision = self.router.route(TaskFeature(
                    task_complexity=complexity,
                    estimated_tokens=1000,
                ))
                self.assertEqual(decision.selected_tier, ModelTier.HAIKU)
                self.assertIn("低复杂度", decision.reasoning)

    def test_medium_complexity_routes_to_sonnet(self):
        """测试中等复杂度（4-6）路由到 sonnet"""
        for complexity in [4, 5, 6]:
            with self.subTest(complexity=complexity):
                decision = self.router.route(TaskFeature(
                    task_complexity=complexity,
                    estimated_tokens=1000,
                ))
                self.assertEqual(decision.selected_tier, ModelTier.SONNET)
                self.assertIn("中等复杂度", decision.reasoning)

    def test_high_complexity_routes_to_opus(self):
        """测试高复杂度（7-10）路由到 opus"""
        for complexity in [7, 8, 9, 10]:
            with self.subTest(complexity=complexity):
                decision = self.router.route(TaskFeature(
                    task_complexity=complexity,
                    estimated_tokens=1000,
                ))
                self.assertEqual(decision.selected_tier, ModelTier.OPUS)
                self.assertIn("高复杂度", decision.reasoning)

    def test_decision_has_confidence(self):
        """测试决策包含置信度"""
        decision = self.router.route(TaskFeature(
            task_complexity=5,
            estimated_tokens=1000,
        ))
        self.assertGreater(decision.confidence, 0.0)
        self.assertLessEqual(decision.confidence, 1.0)

    def test_decision_has_alternatives(self):
        """测试决策包含备选方案"""
        decision = self.router.route(TaskFeature(
            task_complexity=5,
            estimated_tokens=1000,
        ))
        self.assertGreater(len(decision.alternatives), 0)
        # 备选方案不应包含已选 tier
        self.assertNotIn(decision.selected_tier, decision.alternatives)

    def test_get_profiles_returns_all_tiers(self):
        """测试 get_profiles 返回所有 tier"""
        profiles = self.router.get_profiles()
        self.assertEqual(len(profiles), 3)
        self.assertIn(ModelTier.HAIKU, profiles)
        self.assertIn(ModelTier.SONNET, profiles)
        self.assertIn(ModelTier.OPUS, profiles)

    def test_get_profile_specific_tier(self):
        """测试 get_profile 指定 tier"""
        p = self.router.get_profile(ModelTier.SONNET)
        self.assertEqual(p.tier, ModelTier.SONNET)

    def test_get_profile_unknown_tier_raises(self):
        """测试 get_profile 未知 tier 抛异常"""
        with self.assertRaises(ModelTierNotFoundError):
            self.router.get_profile(ModelTier.from_str("invalid"))


# ============================================================================
# 测试 5: ModelRouter 关键路径
# ============================================================================

class TestModelRouterCriticalPath(unittest.TestCase):
    """ModelRouter 关键路径测试"""

    def setUp(self):
        self.router = ModelRouter()

    def test_critical_task_forces_opus(self):
        """测试关键任务强制 opus"""
        decision = self.router.route(TaskFeature(
            task_complexity=1,  # 即使低复杂度
            estimated_tokens=1000,
            is_critical=True,  # 关键任务
        ))
        self.assertEqual(decision.selected_tier, ModelTier.OPUS)
        self.assertIn("关键任务", decision.reasoning)
        self.assertEqual(decision.confidence, 0.95)

    def test_budget_exhausted_forces_haiku(self):
        """测试预算耗尽强制 haiku"""
        decision = self.router.route(TaskFeature(
            task_complexity=10,  # 即使高复杂度
            estimated_tokens=1000,
            budget_remaining=0.05,  # < 10%
        ))
        self.assertEqual(decision.selected_tier, ModelTier.HAIKU)
        self.assertIn("预算", decision.reasoning)
        self.assertEqual(decision.confidence, 0.90)

    def test_tight_deadline_with_low_quality_routes_sonnet(self):
        """测试截止时间紧 + 质量宽松 → sonnet"""
        decision = self.router.route(TaskFeature(
            task_complexity=8,  # 复杂度高
            estimated_tokens=1000,
            deadline_ms=3000,  # < 5s
            quality_threshold=0.7,  # < 0.8
        ))
        self.assertEqual(decision.selected_tier, ModelTier.SONNET)
        self.assertIn("截止时间紧", decision.reasoning)

    def test_critical_takes_precedence_over_budget(self):
        """测试关键任务优先级高于预算"""
        decision = self.router.route(TaskFeature(
            task_complexity=1,
            estimated_tokens=1000,
            is_critical=True,
            budget_remaining=0.05,  # 预算也耗尽
        ))
        # 关键任务优先 → opus
        self.assertEqual(decision.selected_tier, ModelTier.OPUS)

    def test_tight_deadline_with_high_quality_uses_complexity(self):
        """测试截止时间紧 + 高质量要求 → 走复杂度规则"""
        decision = self.router.route(TaskFeature(
            task_complexity=8,
            estimated_tokens=1000,
            deadline_ms=3000,  # 紧
            quality_threshold=0.95,  # 高质量（不宽松）
        ))
        # 不满足"质量宽松"条件 → 走高复杂度规则 → opus
        self.assertEqual(decision.selected_tier, ModelTier.OPUS)


# ============================================================================
# 测试 6: ModelRouter 画像反哺
# ============================================================================

class TestModelRouterFingerprintIntegration(unittest.TestCase):
    """ModelRouter 画像反哺测试"""

    def setUp(self):
        """每个测试前创建 mock fingerprint"""
        # 使用 mock 避免真实文件 IO
        self.mock_fp = MagicMock()
        self.mock_fp.total_executions = 0
        self.mock_fp.records = []
        self.router = ModelRouter(fingerprint=self.mock_fp)

    def test_cold_start_uses_static_rule(self):
        """测试冷启动（无样本）使用静态规则"""
        self.mock_fp.total_executions = 0
        decision = self.router.route(TaskFeature(
            task_complexity=5,
            estimated_tokens=1000,
        ))
        self.assertEqual(decision.selected_tier, ModelTier.SONNET)
        self.assertEqual(decision.decision_source, "static_rule:medium_complexity")

    def test_insufficient_samples_uses_static_rule(self):
        """测试样本不足使用静态规则"""
        self.mock_fp.total_executions = 5  # < MIN_FINGERPRINT_SAMPLES
        decision = self.router.route(TaskFeature(
            task_complexity=5,
            estimated_tokens=1000,
        ))
        # 样本不足 → 静态规则
        self.assertIn("static_rule", decision.decision_source)

    def test_sufficient_samples_with_no_history_uses_static(self):
        """测试样本充足但无同类历史 → 静态规则"""
        self.mock_fp.total_executions = 20
        # 无记录
        decision = self.router.route(TaskFeature(
            task_complexity=5,
            estimated_tokens=1000,
        ))
        self.assertIn("static_rule", decision.decision_source)

    def test_history_with_consistent_decision_increases_confidence(self):
        """测试历史决策一致时增加置信度"""
        # 构造 20 条历史，全部使用 opus
        from performance_fingerprint import ExecutionRecord
        records = []
        for i in range(20):
            rec = ExecutionRecord(
                record_id=f"rec_{i}",
                agent_id="test",
                task_type="model_routing:opus",
                task_complexity=5,
                success=True,
                error_type=None,
                execution_time=1.0,
                strategy="model_tier=opus;source=static_rule:high_complexity",
                context_features={},
            )
            records.append(rec)
        self.mock_fp.records = records
        self.mock_fp.total_executions = 20

        decision = self.router.route(TaskFeature(
            task_complexity=5,  # 中等复杂度，sonnet
            estimated_tokens=1000,
        ))
        # 历史都是 opus → 画像反哺选 opus（覆盖静态规则）
        self.assertEqual(decision.selected_tier, ModelTier.OPUS)
        self.assertIn("fingerprint_history", decision.decision_source)
        # 置信度应该比静态规则高
        self.assertGreater(decision.confidence, 0.8)

    def test_history_with_inconsistent_decision_overrides(self):
        """测试历史决策不一致时采用历史"""
        from performance_fingerprint import ExecutionRecord
        # 15 条 opus + 5 条 haiku
        records = []
        for i in range(15):
            rec = ExecutionRecord(
                record_id=f"rec_{i}",
                agent_id="test",
                task_type="model_routing:opus",
                task_complexity=8,
                success=True,
                error_type=None,
                execution_time=1.0,
                strategy="model_tier=opus;source=static_rule:high_complexity",
                context_features={},
            )
            records.append(rec)
        for i in range(5):
            rec = ExecutionRecord(
                record_id=f"rec_{15+i}",
                agent_id="test",
                task_type="model_routing:haiku",
                task_complexity=8,
                success=True,
                error_type=None,
                execution_time=0.5,
                strategy="model_tier=haiku;source=static_rule:low_complexity",
                context_features={},
            )
            records.append(rec)
        self.mock_fp.records = records
        self.mock_fp.total_executions = 20

        decision = self.router.route(TaskFeature(
            task_complexity=8,  # 应该选 opus
            estimated_tokens=1000,
        ))
        # opus 众数 → 选 opus（一致）
        self.assertEqual(decision.selected_tier, ModelTier.OPUS)

    def test_record_decision_writes_to_fingerprint(self):
        """测试 record_decision 写入画像"""
        decision = RoutingDecision(
            selected_tier=ModelTier.SONNET,
            confidence=0.85,
            reasoning="test",
            alternatives=[ModelTier.HAIKU, ModelTier.OPUS],
            feature_snapshot={"task_complexity": 5, "estimated_tokens": 1000},
        )
        self.router.record_decision(decision, actual_outcome={
            "success": True,
            "quality": 0.9,
            "execution_time": 2.0,
        })
        # 验证 mock 收到 record 调用
        self.mock_fp.record.assert_called_once()
        call_kwargs = self.mock_fp.record.call_args.kwargs
        self.assertEqual(call_kwargs["task_type"], "model_routing:sonnet")
        self.assertTrue(call_kwargs["success"])
        self.assertIn("model_tier=sonnet", call_kwargs["strategy"])

    def test_record_decision_without_fingerprint_does_not_raise(self):
        """测试无 fingerprint 时 record_decision 不抛异常"""
        router = ModelRouter()  # 无 fingerprint
        decision = RoutingDecision(
            selected_tier=ModelTier.SONNET,
            confidence=0.85,
            reasoning="test",
        )
        # 不应抛异常
        router.record_decision(decision)


# ============================================================================
# 测试 7: ModelRouter 错误路径
# ============================================================================

class TestModelRouterErrorPaths(unittest.TestCase):
    """ModelRouter 错误路径测试"""

    def test_custom_profiles_with_invalid_key_raises(self):
        """测试 custom_profiles 键非法抛异常"""
        with self.assertRaises(ModelRouterError):
            ModelRouter(custom_profiles={"invalid": MagicMock()})

    def test_route_records_decision_time(self):
        """测试 route 记录决策耗时"""
        router = ModelRouter()
        decision = router.route(TaskFeature(
            task_complexity=5,
            estimated_tokens=1000,
        ))
        self.assertGreater(decision.decision_time_ms, 0)
        # 决策应快（< 100ms）
        self.assertLess(decision.decision_time_ms, 100)

    def test_get_decision_history_returns_list(self):
        """测试 get_decision_history 返回列表"""
        router = ModelRouter()
        router.route(TaskFeature(task_complexity=5, estimated_tokens=1000))
        history = router.get_decision_history()
        self.assertEqual(len(history), 1)

    def test_decision_history_capped(self):
        """测试决策历史有上限"""
        router = ModelRouter()
        # 路由 1100 次
        for i in range(1100):
            router.route(TaskFeature(task_complexity=5, estimated_tokens=1000))
        # 历史应被截断到 500
        history = router.get_decision_history()
        self.assertEqual(len(history), 500)


# ============================================================================
# 测试 8: ModelRouter 并发
# ============================================================================

class TestModelRouterConcurrency(unittest.TestCase):
    """ModelRouter 并发测试"""

    def test_concurrent_routing(self):
        """测试并发路由安全"""
        router = ModelRouter()
        results = []
        errors = []
        lock = threading.Lock()

        def route_task(complexity):
            try:
                decision = router.route(TaskFeature(
                    task_complexity=complexity,
                    estimated_tokens=1000,
                ))
                with lock:
                    results.append(decision)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = []
        for i in range(50):
            t = threading.Thread(target=route_task, args=(i % 10 + 1,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        # 无错误
        self.assertEqual(len(errors), 0)
        # 所有 50 个结果
        self.assertEqual(len(results), 50)


# ============================================================================
# 测试 9: ModelRouter 性能
# ============================================================================

class TestModelRouterPerformance(unittest.TestCase):
    """ModelRouter 性能基线"""

    def test_route_under_10ms_cold(self):
        """测试冷启动路由 < 10ms"""
        router = ModelRouter()
        # 预热
        router.route(TaskFeature(task_complexity=5, estimated_tokens=1000))

        start = time.time()
        for _ in range(100):
            router.route(TaskFeature(task_complexity=5, estimated_tokens=1000))
        elapsed_ms = (time.time() - start) * 1000 / 100
        self.assertLess(elapsed_ms, 10, f"平均路由耗时 {elapsed_ms:.2f}ms 超过 10ms")

    def test_route_under_50ms_with_fingerprint(self):
        """测试带画像路由 < 50ms"""
        mock_fp = MagicMock()
        mock_fp.total_executions = 20
        # 构造一些历史记录
        from performance_fingerprint import ExecutionRecord
        records = []
        for i in range(20):
            rec = ExecutionRecord(
                record_id=f"rec_{i}",
                agent_id="test",
                task_type="model_routing:sonnet",
                task_complexity=5,
                success=True,
                error_type=None,
                execution_time=1.0,
                strategy="model_tier=sonnet;source=static_rule",
                context_features={},
            )
            records.append(rec)
        mock_fp.records = records

        router = ModelRouter(fingerprint=mock_fp)
        # 预热
        router.route(TaskFeature(task_complexity=5, estimated_tokens=1000))

        start = time.time()
        for _ in range(100):
            router.route(TaskFeature(task_complexity=5, estimated_tokens=1000))
        elapsed_ms = (time.time() - start) * 1000 / 100
        self.assertLess(elapsed_ms, 50, f"平均路由耗时 {elapsed_ms:.2f}ms 超过 50ms")


# ============================================================================
# 测试入口
# ============================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
