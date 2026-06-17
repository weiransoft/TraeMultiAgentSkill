#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_pattern_tier_resolver.py — Phase 10 PatternTierResolver 单元测试 + 集成测试

测试结构（合计 45+ 用例）：
- TestPatternTierResolverDefaults（10 个）：6 模式默认策略验证
- TestPatternTierPolicyValidation（4 个）：policy 字段校验
- TestUpgradeDowngradeConditions（6 个）：升级/降级条件
- TestModelRouterIntegration（8 个）：ModelRouter 集成
- TestPatternExecutorIntegration（4 个）：pattern_executor 透传
- TestCyberneticsBridgeIntegration（6 个）：cybernetics_bridge 解析
- TestBoundaryScenarios（8 个）：边界场景
- TestConcurrency（1 个）：并发线程安全
- TestPerformanceBaseline（2 个）：性能基线

作者：trae-multi-agent 融合 Phase 10
创建日期：2026-06-05
"""

import os
import sys
import threading
import time
import unittest
from typing import Any, Dict, Optional

# 路径处理：tests 在 scripts/tests/，模块在 scripts/dynamic_workflow/
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(TESTS_DIR)
DYNAMIC_WORKFLOW_DIR = os.path.join(SCRIPTS_DIR, "dynamic_workflow")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
if DYNAMIC_WORKFLOW_DIR not in sys.path:
    sys.path.insert(0, DYNAMIC_WORKFLOW_DIR)

from model_router import ModelRouter, ModelTier, ModelRouterError, TaskFeature
from pattern_tier_resolver import (
    PatternTierResolver,
    PatternTierPolicy,
    PatternTierPolicyError,
    TierResolution,
    create_default_resolver,
)
from performance_fingerprint import PerformanceFingerprint


# ============================================================================
# 测试 1：6 模式默认策略验证（10 个）
# ============================================================================

class TestPatternTierResolverDefaults(unittest.TestCase):
    """6 模式默认策略验证"""

    def setUp(self):
        """每个测试前创建独立 resolver（避免测试间状态污染）"""
        self.resolver = create_default_resolver()
        # 基础 feature：复杂度 5，token 1000
        self.base_feature = TaskFeature(
            task_complexity=5,
            estimated_tokens=1000,
        )

    def test_01_all_six_patterns_registered(self):
        """6 大模式全部注册"""
        expected = {
            "adversarial-verify",
            "generate-filter",
            "loop-until-done",
            "fan-out-aggregate",
            "tournament",
            "classifier-dispatch",
        }
        self.assertEqual(set(self.resolver.list_pattern_ids()), expected)
        self.assertEqual(self.resolver.size(), 6)

    def test_02_adversarial_verify_default_opus(self):
        """adversarial-verify 默认 opus"""
        res = self.resolver.resolve(pattern_id="adversarial-verify", feature=self.base_feature)
        self.assertEqual(res.tier, ModelTier.OPUS)
        self.assertEqual(res.source, "pattern_policy_default")
        self.assertGreater(res.confidence, 0.0)

    def test_03_generate_filter_default_haiku(self):
        """generate-filter 默认 haiku"""
        res = self.resolver.resolve(pattern_id="generate-filter", feature=self.base_feature)
        self.assertEqual(res.tier, ModelTier.HAIKU)
        self.assertEqual(res.source, "pattern_policy_default")

    def test_04_loop_until_done_default_haiku(self):
        """loop-until-done 默认 haiku"""
        res = self.resolver.resolve(pattern_id="loop-until-done", feature=self.base_feature)
        self.assertEqual(res.tier, ModelTier.HAIKU)
        self.assertEqual(res.source, "pattern_policy_default")

    def test_05_fan_out_aggregate_default_haiku(self):
        """fan-out-aggregate 默认 haiku"""
        res = self.resolver.resolve(pattern_id="fan-out-aggregate", feature=self.base_feature)
        self.assertEqual(res.tier, ModelTier.HAIKU)
        self.assertEqual(res.source, "pattern_policy_default")

    def test_06_tournament_default_sonnet(self):
        """tournament 默认 sonnet"""
        res = self.resolver.resolve(pattern_id="tournament", feature=self.base_feature)
        self.assertEqual(res.tier, ModelTier.SONNET)
        self.assertEqual(res.source, "pattern_policy_default")

    def test_07_classifier_dispatch_default_sonnet(self):
        """classifier-dispatch 默认 sonnet"""
        res = self.resolver.resolve(pattern_id="classifier-dispatch", feature=self.base_feature)
        self.assertEqual(res.tier, ModelTier.SONNET)
        self.assertEqual(res.source, "pattern_policy_default")

    def test_08_unknown_pattern_fallback(self):
        """未知 pattern_id → fallback（tier=None）"""
        res = self.resolver.resolve(pattern_id="nonexistent-pattern", feature=self.base_feature)
        self.assertIsNone(res.tier)
        self.assertEqual(res.source, "fallback")

    def test_09_none_pattern_id_fallback(self):
        """None pattern_id → fallback"""
        res = self.resolver.resolve(pattern_id=None, feature=self.base_feature)
        self.assertIsNone(res.tier)
        self.assertEqual(res.source, "fallback")

    def test_10_explicit_tier_short_circuits(self):
        """显式 tier 强制覆盖"""
        res = self.resolver.resolve(
            pattern_id="adversarial-verify",
            feature=self.base_feature,
            explicit_tier=ModelTier.HAIKU,
        )
        self.assertEqual(res.tier, ModelTier.HAIKU)
        self.assertEqual(res.source, "explicit_override")
        self.assertEqual(res.confidence, 1.0)


# ============================================================================
# 测试 2：PatternTierPolicy 字段校验（4 个）
# ============================================================================

class TestPatternTierPolicyValidation(unittest.TestCase):
    """PatternTierPolicy 字段校验"""

    def test_01_invalid_pattern_id_raises(self):
        """非法 pattern_id（不符合 kebab-case）→ 抛 PatternTierPolicyError"""
        with self.assertRaises(PatternTierPolicyError):
            PatternTierPolicy(
                pattern_id="Invalid_Pattern_ID",  # 含大写和下划线
                default_tier=ModelTier.OPUS,
            )

    def test_02_non_callable_upgrade_condition_raises(self):
        """upgrade_condition 非 callable → 抛 PatternTierPolicyError"""
        with self.assertRaises(PatternTierPolicyError):
            PatternTierPolicy(
                pattern_id="test-pattern",
                default_tier=ModelTier.HAIKU,
                upgrade_to=ModelTier.SONNET,
                upgrade_condition="not_callable",  # 字符串而非 callable
            )

    def test_03_upgrade_to_equal_default_raises(self):
        """upgrade_to == default_tier → 抛 PatternTierPolicyError"""
        with self.assertRaises(PatternTierPolicyError):
            PatternTierPolicy(
                pattern_id="test-pattern",
                default_tier=ModelTier.SONNET,
                upgrade_to=ModelTier.SONNET,  # 等于 default
                upgrade_condition=lambda f: True,
            )

    def test_04_default_tier_not_model_tier_raises(self):
        """default_tier 非 ModelTier 枚举 → 抛 PatternTierPolicyError"""
        with self.assertRaises(PatternTierPolicyError):
            PatternTierPolicy(
                pattern_id="test-pattern",
                default_tier="opus",  # 字符串而非 ModelTier
            )


# ============================================================================
# 测试 3：升级/降级条件（6 个）
# ============================================================================

class TestUpgradeDowngradeConditions(unittest.TestCase):
    """升级/降级条件触发验证"""

    def setUp(self):
        self.resolver = create_default_resolver()

    def test_01_generate_filter_upgrade_on_high_complexity(self):
        """generate-filter: complexity >= 8 → 升级 sonnet"""
        feature = TaskFeature(task_complexity=8, estimated_tokens=1000, pattern_id="generate-filter")
        res = self.resolver.resolve(pattern_id="generate-filter", feature=feature)
        self.assertEqual(res.tier, ModelTier.SONNET)
        self.assertEqual(res.source, "pattern_policy_upgrade")

    def test_02_generate_filter_below_threshold_keeps_default(self):
        """generate-filter: complexity < 8 → 保持 haiku"""
        feature = TaskFeature(task_complexity=7, estimated_tokens=1000, pattern_id="generate-filter")
        res = self.resolver.resolve(pattern_id="generate-filter", feature=feature)
        self.assertEqual(res.tier, ModelTier.HAIKU)
        self.assertEqual(res.source, "pattern_policy_default")

    def test_03_loop_until_done_upgrade_on_final_iteration(self):
        """loop-until-done: is_final_iteration=True → 升级 sonnet"""
        feature = TaskFeature(
            task_complexity=5,
            estimated_tokens=1000,
            pattern_id="loop-until-done",
            extra={"is_final_iteration": True},
        )
        res = self.resolver.resolve(pattern_id="loop-until-done", feature=feature)
        self.assertEqual(res.tier, ModelTier.SONNET)
        self.assertEqual(res.source, "pattern_policy_upgrade")

    def test_04_loop_until_done_non_final_keeps_default(self):
        """loop-until-done: 非最终轮 → 保持 haiku"""
        feature = TaskFeature(
            task_complexity=5,
            estimated_tokens=1000,
            pattern_id="loop-until-done",
            extra={"is_final_iteration": False},
        )
        res = self.resolver.resolve(pattern_id="loop-until-done", feature=feature)
        self.assertEqual(res.tier, ModelTier.HAIKU)
        self.assertEqual(res.source, "pattern_policy_default")

    def test_05_fan_out_upgrade_on_large_subtask_count(self):
        """fan-out-aggregate: subtask_count >= 50 → 升级 sonnet"""
        feature = TaskFeature(
            task_complexity=5,
            estimated_tokens=1000,
            pattern_id="fan-out-aggregate",
            extra={"subtask_count": 50},
        )
        res = self.resolver.resolve(pattern_id="fan-out-aggregate", feature=feature)
        self.assertEqual(res.tier, ModelTier.SONNET)
        self.assertEqual(res.source, "pattern_policy_upgrade")

    def test_06_tournament_upgrade_on_high_risk(self):
        """tournament: risk_level=high → 升级 opus"""
        feature = TaskFeature(
            task_complexity=5,
            estimated_tokens=1000,
            pattern_id="tournament",
            extra={"risk_level": "high"},
        )
        res = self.resolver.resolve(pattern_id="tournament", feature=feature)
        self.assertEqual(res.tier, ModelTier.OPUS)
        self.assertEqual(res.source, "pattern_policy_upgrade")


# ============================================================================
# 测试 4：ModelRouter 集成（8 个）
# ============================================================================

class TestModelRouterIntegration(unittest.TestCase):
    """ModelRouter 与 PatternTierResolver 集成"""

    def setUp(self):
        self.resolver = create_default_resolver()
        self.router = ModelRouter(tier_resolver=self.resolver)

    def test_01_router_uses_resolver_when_configured(self):
        """resolver 存在时优先使用"""
        feature = TaskFeature(
            task_complexity=5,
            estimated_tokens=1000,
            pattern_id="adversarial-verify",
        )
        decision = self.router.route(feature)
        self.assertEqual(decision.selected_tier, ModelTier.OPUS)
        self.assertEqual(decision.decision_source, "pattern_policy:adversarial-verify")

    def test_02_router_falls_back_when_no_pattern_id(self):
        """无 pattern_id 时走通用规则（即使 resolver 存在）"""
        feature = TaskFeature(task_complexity=5, estimated_tokens=1000)  # pattern_id=None
        decision = self.router.route(feature)
        # 5 级复杂度 → sonnet
        self.assertEqual(decision.selected_tier, ModelTier.SONNET)
        self.assertEqual(decision.decision_source, "static_rule:medium_complexity")

    def test_03_router_falls_back_when_unknown_pattern_id(self):
        """未知 pattern_id 时走通用规则"""
        feature = TaskFeature(
            task_complexity=5,
            estimated_tokens=1000,
            pattern_id="unknown-pattern",
        )
        decision = self.router.route(feature)
        # resolver 返回 fallback → 走通用规则
        self.assertEqual(decision.selected_tier, ModelTier.SONNET)
        self.assertEqual(decision.decision_source, "static_rule:medium_complexity")

    def test_04_explicit_tier_short_circuits(self):
        """explicit_tier 短路 pattern_policy"""
        feature = TaskFeature(
            task_complexity=5,
            estimated_tokens=1000,
            pattern_id="adversarial-verify",  # 默认 opus
        )
        decision = self.router.route(feature, explicit_tier=ModelTier.HAIKU)
        self.assertEqual(decision.selected_tier, ModelTier.HAIKU)
        self.assertEqual(decision.decision_source, "explicit_override")

    def test_05_is_critical_overrides_pattern_policy(self):
        """is_critical=True 强制高于 pattern_policy（安全约束）"""
        feature = TaskFeature(
            task_complexity=2,  # 低复杂度
            estimated_tokens=1000,
            pattern_id="generate-filter",  # 默认 haiku
            is_critical=True,  # 强制 opus
        )
        decision = self.router.route(feature)
        self.assertEqual(decision.selected_tier, ModelTier.OPUS)
        self.assertEqual(decision.decision_source, "static_rule:critical_task")

    def test_06_budget_exhausted_overrides_pattern_policy(self):
        """budget < 0.1 强制高于 pattern_policy"""
        feature = TaskFeature(
            task_complexity=5,
            estimated_tokens=1000,
            pattern_id="adversarial-verify",  # 默认 opus
            budget_remaining=0.05,  # 预算耗尽 → haiku
        )
        decision = self.router.route(feature)
        self.assertEqual(decision.selected_tier, ModelTier.HAIKU)
        self.assertEqual(decision.decision_source, "static_rule:budget_exhausted")

    def test_07_router_without_resolver_compatibility(self):
        """无 resolver 时 ModelRouter 行为不变（向后兼容）"""
        router = ModelRouter()  # 不传 tier_resolver
        feature = TaskFeature(task_complexity=5, estimated_tokens=1000, pattern_id="adversarial-verify")
        decision = router.route(feature)
        # 无 resolver → 走通用规则（pattern_id 被忽略）
        self.assertEqual(decision.selected_tier, ModelTier.SONNET)
        self.assertEqual(decision.decision_source, "static_rule:medium_complexity")

    def test_08_invalid_tier_resolver_raises(self):
        """tier_resolver 非 PatternTierResolver → 抛 ModelRouterError"""
        with self.assertRaises(ModelRouterError):
            ModelRouter(tier_resolver="not_a_resolver")  # type: ignore


# ============================================================================
# 测试 5：pattern_executor 集成（4 个）
# ============================================================================

class TestPatternExecutorIntegration(unittest.TestCase):
    """pattern_executor 透传 pattern_id 验证"""

    def setUp(self):
        from pattern_executor import _extract_task_feature
        self._extract_task_feature = _extract_task_feature

    def test_01_extract_task_feature_with_pattern_id_param(self):
        """_extract_task_feature 接受 pattern_id 参数"""
        feature = self._extract_task_feature({"description": "test"}, pattern_id="adversarial-verify")
        self.assertEqual(feature.pattern_id, "adversarial-verify")

    def test_02_extract_task_feature_with_pattern_id_in_dict(self):
        """_extract_task_feature 从 task_dict 提取 pattern_id"""
        feature = self._extract_task_feature(
            {"description": "test", "pattern_id": "generate-filter"}
        )
        self.assertEqual(feature.pattern_id, "generate-filter")

    def test_03_extract_task_feature_param_overrides_dict(self):
        """_extract_task_feature 参数优先于 task_dict.pattern_id"""
        feature = self._extract_task_feature(
            {"description": "test", "pattern_id": "generate-filter"},
            pattern_id="adversarial-verify",  # 参数覆盖
        )
        self.assertEqual(feature.pattern_id, "adversarial-verify")

    def test_04_extract_task_feature_extra_field(self):
        """_extract_task_feature 透传 extra 字段"""
        feature = self._extract_task_feature(
            {
                "description": "test",
                "extra": {"is_final_iteration": True, "subtask_count": 30},
            }
        )
        self.assertEqual(feature.extra.get("is_final_iteration"), True)
        self.assertEqual(feature.extra.get("subtask_count"), 30)


# ============================================================================
# 测试 6：cybernetics_bridge 集成（6 个）
# ============================================================================

class TestCyberneticsBridgeIntegration(unittest.TestCase):
    """cybernetics_bridge 解析 _meta.model_tier 验证"""

    def setUp(self):
        from cybernetics_bridge import CyberneticsBridge
        self.bridge = CyberneticsBridge(project_root="/tmp")

    def test_01_extract_model_tier_normal(self):
        """正常 _meta.model_tier 提取"""
        task = {"_meta": {"model_tier": "opus"}}
        tier = self.bridge.extract_model_tier(task)
        self.assertEqual(tier, "opus")

    def test_02_extract_model_tier_case_insensitive(self):
        """大小写不敏感"""
        task = {"_meta": {"model_tier": "OPUS"}}
        tier = self.bridge.extract_model_tier(task)
        self.assertEqual(tier, "opus")

    def test_03_extract_model_tier_missing_returns_none(self):
        """无 _meta → None"""
        tier = self.bridge.extract_model_tier({"description": "test"})
        self.assertIsNone(tier)

    def test_04_extract_model_tier_invalid_type(self):
        """_meta 非 dict → None（防御性）"""
        tier = self.bridge.extract_model_tier({"_meta": "not_a_dict"})
        self.assertIsNone(tier)
        tier2 = self.bridge.extract_model_tier({"_meta": None})
        self.assertIsNone(tier2)

    def test_05_extract_model_tier_invalid_value(self):
        """_meta.model_tier 非法值 → None + warning"""
        task = {"_meta": {"model_tier": "invalid_tier"}}
        tier = self.bridge.extract_model_tier(task)
        self.assertIsNone(tier)

    def test_06_annotate_with_tier_writes_meta(self):
        """annotate_with_tier 正确写入 _meta.model_tier"""
        task = {"description": "test"}
        result = self.bridge.annotate_with_tier(task, "sonnet")
        self.assertIs(result, task)  # 链式友好
        self.assertEqual(task["_meta"]["model_tier"], "sonnet")


# ============================================================================
# 测试 7：边界场景（8 个）
# ============================================================================

class TestBoundaryScenarios(unittest.TestCase):
    """边界场景测试（架构师审查 2.8 建议）"""

    def setUp(self):
        self.resolver = create_default_resolver()

    def test_01_empty_string_pattern_id_fallback(self):
        """空字符串 pattern_id → fallback"""
        feature = TaskFeature(task_complexity=5, estimated_tokens=1000, pattern_id="")
        res = self.resolver.resolve(pattern_id="", feature=feature)
        self.assertIsNone(res.tier)
        self.assertEqual(res.source, "fallback")

    def test_02_explicit_none_not_treated_as_override(self):
        """explicit_tier=None 不应触发 override"""
        feature = TaskFeature(
            task_complexity=5,
            estimated_tokens=1000,
            pattern_id="adversarial-verify",
        )
        res = self.resolver.resolve(pattern_id="adversarial-verify", feature=feature, explicit_tier=None)
        self.assertEqual(res.tier, ModelTier.OPUS)  # 走默认
        self.assertEqual(res.source, "pattern_policy_default")

    def test_03_invalid_explicit_tier_raises(self):
        """explicit_tier 非 ModelTier → 抛 InvalidTierError"""
        feature = TaskFeature(task_complexity=5, estimated_tokens=1000)
        with self.assertRaises(Exception):  # InvalidTierError 是 ModelRouterError 子类
            self.resolver.resolve(
                pattern_id="x", feature=feature, explicit_tier="not_a_tier"  # type: ignore
            )

    def test_04_custom_policy_overrides_default(self):
        """自定义 policy 覆盖默认"""
        custom_policy = PatternTierPolicy(
            pattern_id="adversarial-verify",
            default_tier=ModelTier.SONNET,  # 自定义降级到 sonnet
        )
        resolver = PatternTierResolver(custom_policies={"adversarial-verify": custom_policy})
        feature = TaskFeature(task_complexity=5, estimated_tokens=1000, pattern_id="adversarial-verify")
        res = resolver.resolve(pattern_id="adversarial-verify", feature=feature)
        self.assertEqual(res.tier, ModelTier.SONNET)
        self.assertEqual(res.source, "pattern_policy_default")

    def test_05_broken_upgrade_condition_falls_back_to_default(self):
        """upgrade_condition 异常 → 降级到 default"""
        def broken_condition(f):
            raise RuntimeError("condition broken")

        custom_policy = PatternTierPolicy(
            pattern_id="generate-filter",
            default_tier=ModelTier.HAIKU,
            upgrade_to=ModelTier.SONNET,
            upgrade_condition=broken_condition,
        )
        resolver = PatternTierResolver(custom_policies={"generate-filter": custom_policy})
        feature = TaskFeature(task_complexity=10, estimated_tokens=1000, pattern_id="generate-filter")
        res = resolver.resolve(pattern_id="generate-filter", feature=feature)
        self.assertEqual(res.tier, ModelTier.HAIKU)  # 降级到 default
        self.assertEqual(res.source, "pattern_policy_default")

    def test_06_register_policy_at_runtime(self):
        """运行时注册新 policy"""
        resolver = PatternTierResolver()
        new_policy = PatternTierPolicy(
            pattern_id="custom-pattern",
            default_tier=ModelTier.OPUS,
        )
        resolver.register_policy(new_policy)
        feature = TaskFeature(task_complexity=5, estimated_tokens=1000, pattern_id="custom-pattern")
        res = resolver.resolve(pattern_id="custom-pattern", feature=feature)
        self.assertEqual(res.tier, ModelTier.OPUS)
        self.assertIn("custom-pattern", resolver.list_pattern_ids())

    def test_07_taskfeature_extra_must_be_dict(self):
        """TaskFeature.extra 非 dict → 抛 InvalidTaskFeatureError"""
        with self.assertRaises(Exception):
            TaskFeature(
                task_complexity=5,
                estimated_tokens=1000,
                extra="not_a_dict",  # type: ignore
            )

    def test_08_taskfeature_pattern_id_must_be_str_or_none(self):
        """TaskFeature.pattern_id 非 str/None → 抛 InvalidTaskFeatureError"""
        with self.assertRaises(Exception):
            TaskFeature(
                task_complexity=5,
                estimated_tokens=1000,
                pattern_id=123,  # type: ignore
            )


# ============================================================================
# 测试 8：并发线程安全（1 个）
# ============================================================================

class TestConcurrency(unittest.TestCase):
    """并发线程安全验证"""

    def test_concurrent_resolver_access(self):
        """10 个线程并发调用 resolver，验证无数据竞争"""
        resolver = create_default_resolver()
        errors = []

        def worker(thread_id: int) -> None:
            try:
                for i in range(50):
                    feature = TaskFeature(task_complexity=5, estimated_tokens=1000)
                    resolver.resolve(pattern_id="adversarial-verify", feature=feature)
                    resolver.resolve(pattern_id="generate-filter", feature=feature)
                    resolver.resolve(pattern_id="loop-until-done", feature=feature)
            except Exception as e:
                errors.append((thread_id, str(e)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"并发错误：{errors}")


# ============================================================================
# 测试 9：性能基线（2 个）
# ============================================================================

class TestPerformanceBaseline(unittest.TestCase):
    """性能基线（架构师审查 2.8 建议）"""

    def test_01_resolver_throughput_1000_calls(self):
        """1000 次 resolve 调用 < 100ms（单次 < 0.1ms）"""
        resolver = create_default_resolver()
        feature = TaskFeature(task_complexity=5, estimated_tokens=1000, pattern_id="generate-filter")
        # 预热
        for _ in range(10):
            resolver.resolve(pattern_id="generate-filter", feature=feature)

        start = time.perf_counter()
        for _ in range(1000):
            resolver.resolve(pattern_id="generate-filter", feature=feature)
        elapsed_ms = (time.perf_counter() - start) * 1000
        # 1000 次 < 100ms（平均 < 0.1ms）
        self.assertLess(elapsed_ms, 100, f"1000 次 resolve 耗时 {elapsed_ms:.2f}ms 超出 100ms 预算")

    def test_02_router_with_resolver_overhead_under_5ms(self):
        """带 resolver 的 router.route() 平均开销 < 5ms"""
        resolver = create_default_resolver()
        router_with = ModelRouter(tier_resolver=resolver)
        router_without = ModelRouter()

        feature_with = TaskFeature(
            task_complexity=5, estimated_tokens=1000, pattern_id="generate-filter"
        )
        feature_without = TaskFeature(task_complexity=5, estimated_tokens=1000)

        # 预热
        for _ in range(10):
            router_with.route(feature_with)
            router_without.route(feature_without)

        # 测量（各 100 次取平均）
        with_times = []
        for _ in range(100):
            start = time.perf_counter()
            router_with.route(feature_with)
            with_times.append((time.perf_counter() - start) * 1000)

        without_times = []
        for _ in range(100):
            start = time.perf_counter()
            router_without.route(feature_without)
            without_times.append((time.perf_counter() - start) * 1000)

        avg_with = sum(with_times) / len(with_times)
        avg_without = sum(without_times) / len(without_times)
        overhead = avg_with - avg_without
        # 平均开销 < 5ms（架构师审查 2.8 建议）
        self.assertLess(
            overhead, 5.0,
            f"resolver 开销 {overhead:.3f}ms 超出 5ms 预算（avg_with={avg_with:.3f}ms, avg_without={avg_without:.3f}ms）"
        )


# ============================================================================
# 入口
# ============================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
