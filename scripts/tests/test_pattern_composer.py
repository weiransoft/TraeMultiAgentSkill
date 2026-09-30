#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pattern_composer.py 单元测试

测试目标：
- 3 个核心模式的选择规则（适用边界 + 拒绝边界）
- 模式库 schema 校验
- 决策树优先级（classifier > fanout > adversarial > sequential）
- 性能基线（< 100ms / 次）
- 端到端场景（与 pattern_examples/*.json 对齐）
- 持久化反哺接口

测试约定（与 scripts/tests/ 现有风格保持一致）：
- 使用 unittest 框架
- 不依赖外部网络
- 不修改任何 V2 文件
- 测试数据使用临时目录
"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict

# 添加 scripts 目录到 sys.path（确保性能画像可导入）
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# 动态加载 pattern_composer（独立模块）
DYNAMIC_WORKFLOW_DIR = SCRIPTS_DIR / "dynamic_workflow"
if str(DYNAMIC_WORKFLOW_DIR) not in sys.path:
    sys.path.insert(0, str(DYNAMIC_WORKFLOW_DIR))

from pattern_composer import (  # noqa: E402
    PATTERN_ADVERSARIAL_VERIFY,
    PATTERN_CLASSIFIER_DISPATCH,
    PATTERN_FAN_OUT_AGGREGATE,
    PHASE0_PATTERNS,
    FailureMode,
    IsolationLevel,
    PatternComposer,
    PatternLibrary,
    PatternSelection,
    RiskLevel,
    TaskFeature,
    WorkflowPattern,
    _select_adversarial_verify,
    _select_classifier_dispatch,
    _select_fan_out_aggregate,
    create_default_composer,
    select_pattern_for_task,
)

# 导入 PerformanceFingerprint（持久化反哺依赖）
from performance_fingerprint import PerformanceFingerprint  # noqa: E402

# 本地计算 DYNAMIC_WORKFLOW_DIR（不依赖 pattern_composer 模块导出）
DYNAMIC_WORKFLOW_DIR = SCRIPTS_DIR / "dynamic_workflow"

# PerformanceFingerprint 是 str 枚举，索引需通过 list() 获取
_RISK_LEVELS = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]


# ============================================================================
# 1. 枚举与数据类测试
# ============================================================================

class TestEnumsAndDataclasses(unittest.TestCase):
    """测试枚举与基础数据类"""

    def test_01_isolation_level_values(self):
        """IsolationLevel 枚举值正确"""
        self.assertEqual(IsolationLevel.NONE.value, "none")
        self.assertEqual(IsolationLevel.CONTEXT.value, "context")
        self.assertEqual(IsolationLevel.WORKTREE.value, "worktree")
        self.assertEqual(IsolationLevel.FULL.value, "full")

    def test_02_risk_level_values(self):
        """RiskLevel 枚举值正确"""
        self.assertEqual(RiskLevel.LOW.value, "low")
        self.assertEqual(RiskLevel.MEDIUM.value, "medium")
        self.assertEqual(RiskLevel.HIGH.value, "high")
        self.assertEqual(RiskLevel.CRITICAL.value, "critical")

    def test_03_task_feature_defaults(self):
        """TaskFeature 默认值"""
        task = TaskFeature()
        self.assertEqual(task.type_variants, 1)
        self.assertEqual(task.subtask_count, 1)
        self.assertTrue(task.subtask_homogeneous)
        self.assertTrue(task.subtask_independent)
        self.assertEqual(task.risk_level, RiskLevel.LOW)
        self.assertFalse(task.has_evaluation_criteria)

    def test_04_task_feature_to_dict(self):
        """TaskFeature 可正确序列化为 dict"""
        task = TaskFeature(
            type_variants=3,
            subtask_count=20,
            risk_level=RiskLevel.HIGH,
            task_type="security_audit",
            task_complexity=7,
        )
        d = task.to_dict()
        self.assertEqual(d["type_variants"], 3)
        self.assertEqual(d["subtask_count"], 20)
        self.assertEqual(d["risk_level"], "high")
        self.assertEqual(d["task_type"], "security_audit")
        # extra 默认空 dict
        self.assertEqual(d["extra"], {})

    def test_05_pattern_selection_to_dict(self):
        """PatternSelection 可正确序列化为 dict"""
        sel = PatternSelection(
            pattern_id="classifier-dispatch",
            applicable=True,
            confidence=0.9,
            rationale="test",
            parameters={"k": "v"},
            estimated_token_budget=4000,
            fallback_pattern="sequential",
        )
        d = sel.to_dict()
        self.assertEqual(d["pattern_id"], "classifier-dispatch")
        self.assertEqual(d["confidence"], 0.9)
        self.assertEqual(d["parameters"]["k"], "v")
        self.assertEqual(d["fallback_pattern"], "sequential")
        self.assertIsNone(d["rejection_reason"])


# ============================================================================
# 2. 模式定义 schema 校验测试
# ============================================================================

class TestWorkflowPatternValidation(unittest.TestCase):
    """测试 WorkflowPattern.validate() schema 校验"""

    def test_01_default_3_patterns_pass_validation(self):
        """3 个内置核心模式必须通过校验"""
        for pattern in [PATTERN_CLASSIFIER_DISPATCH, PATTERN_FAN_OUT_AGGREGATE, PATTERN_ADVERSARIAL_VERIFY]:
            errors = pattern.validate()
            self.assertEqual(
                errors, [],
                f"模式 {pattern.pattern_id} 校验失败：{errors}"
            )

    def test_02_invalid_pattern_id_rejected(self):
        """非 kebab-case 的 pattern_id 必须被拒绝"""
        # 含大写字母
        bad_pattern = WorkflowPattern(
            pattern_id="ClassifierDispatch",  # 大写，违反规范
            name="test",
            version="1.0",
            description="test",
            isolation_requirement=IsolationLevel.NONE,
            default_token_budget=1000,
            applicable_roles=["test-expert"],
            priority=10,
            parameters_schema={},
            example_parameters={},
            failure_modes=[FailureMode("a", "b", "c")],
            success_criteria=["a"],
            not_applicable_scenarios=["a"],
        )
        errors = bad_pattern.validate()
        self.assertGreater(len(errors), 0, "应检出 pattern_id 命名违规")
        self.assertTrue(any("kebab-case" in e for e in errors))

    def test_03_empty_applicable_roles_rejected(self):
        """applicable_roles 为空必须被拒绝"""
        bad_pattern = WorkflowPattern(
            pattern_id="test-pattern",
            name="t",
            version="1.0",
            description="d",
            isolation_requirement=IsolationLevel.NONE,
            default_token_budget=1000,
            applicable_roles=[],  # 空
            priority=10,
            parameters_schema={},
            example_parameters={},
            failure_modes=[FailureMode("a", "b", "c")],
            success_criteria=["a"],
            not_applicable_scenarios=["a"],
        )
        errors = bad_pattern.validate()
        self.assertTrue(any("applicable_roles" in e for e in errors))

    def test_04_token_budget_out_of_range_rejected(self):
        """default_token_budget 超出 [100, 1000000] 必须被拒绝"""
        bad_pattern = WorkflowPattern(
            pattern_id="test-pattern",
            name="t",
            version="1.0",
            description="d",
            isolation_requirement=IsolationLevel.NONE,
            default_token_budget=10,  # 太小
            applicable_roles=["test-expert"],
            priority=10,
            parameters_schema={},
            example_parameters={},
            failure_modes=[FailureMode("a", "b", "c")],
            success_criteria=["a"],
            not_applicable_scenarios=["a"],
        )
        errors = bad_pattern.validate()
        self.assertTrue(any("default_token_budget" in e for e in errors))

    def test_05_empty_failure_modes_rejected(self):
        """failure_modes 为空必须被拒绝"""
        bad_pattern = WorkflowPattern(
            pattern_id="test-pattern",
            name="t",
            version="1.0",
            description="d",
            isolation_requirement=IsolationLevel.NONE,
            default_token_budget=1000,
            applicable_roles=["test-expert"],
            priority=10,
            parameters_schema={},
            example_parameters={},
            failure_modes=[],  # 空
            success_criteria=["a"],
            not_applicable_scenarios=["a"],
        )
        errors = bad_pattern.validate()
        self.assertTrue(any("failure_modes" in e for e in errors))


# ============================================================================
# 3. 模式选择规则单元测试
# ============================================================================

class TestClassifierDispatchSelector(unittest.TestCase):
    """测试 classifier-dispatch 模式选择规则"""

    def test_01_high_type_variants_applicable(self):
        """type_variants >= 3 适用"""
        task = TaskFeature(type_variants=3, subtask_count=100)
        applicable, conf, rationale = _select_classifier_dispatch(task)
        self.assertTrue(applicable)
        self.assertGreaterEqual(conf, 0.7)
        self.assertIn("3", rationale)

    def test_02_low_type_variants_rejected(self):
        """type_variants < 3 不适用"""
        task = TaskFeature(type_variants=2)
        applicable, conf, rationale = _select_classifier_dispatch(task)
        self.assertFalse(applicable)
        self.assertEqual(conf, 0.0)
        self.assertIn("无需分类器", rationale)

    def test_03_single_type_rejected(self):
        """单类型任务不适用"""
        task = TaskFeature(type_variants=1)
        applicable, _, _ = _select_classifier_dispatch(task)
        self.assertFalse(applicable)


class TestFanOutAggregateSelector(unittest.TestCase):
    """测试 fan-out-aggregate 模式选择规则"""

    def test_01_large_homogeneous_applicable(self):
        """subtask_count >= 10 + 同质 + 独立 适用"""
        task = TaskFeature(
            type_variants=1,
            subtask_count=50,
            subtask_homogeneous=True,
            subtask_independent=True,
        )
        applicable, conf, rationale = _select_fan_out_aggregate(task)
        self.assertTrue(applicable)
        self.assertGreaterEqual(conf, 0.7)
        self.assertIn("50", rationale)

    def test_02_small_count_rejected(self):
        """subtask_count < 10 不适用"""
        task = TaskFeature(subtask_count=5, subtask_homogeneous=True)
        applicable, _, rationale = _select_fan_out_aggregate(task)
        self.assertFalse(applicable)
        self.assertIn("扇出开销大于收益", rationale)

    def test_03_heterogeneous_rejected(self):
        """非同质子任务不适用"""
        task = TaskFeature(
            subtask_count=50,
            subtask_homogeneous=False,
            subtask_independent=True,
        )
        applicable, _, _ = _select_fan_out_aggregate(task)
        self.assertFalse(applicable)

    def test_04_dependent_rejected(self):
        """子任务强依赖不适用"""
        task = TaskFeature(
            subtask_count=50,
            subtask_homogeneous=True,
            subtask_independent=False,
        )
        applicable, _, _ = _select_fan_out_aggregate(task)
        self.assertFalse(applicable)

    def test_05_type_variants_3_yields_to_classifier(self):
        """type_variants >= 3 时让位给 classifier-dispatch"""
        task = TaskFeature(
            type_variants=4,
            subtask_count=100,
            subtask_homogeneous=True,
            subtask_independent=True,
        )
        applicable, _, rationale = _select_fan_out_aggregate(task)
        self.assertFalse(applicable)
        self.assertIn("classifier-dispatch", rationale)


class TestAdversarialVerifySelector(unittest.TestCase):
    """测试 adversarial-verify 模式选择规则"""

    def test_01_high_risk_with_criteria_applicable(self):
        """高风险 + 有可测量准则 适用"""
        task = TaskFeature(
            risk_level=RiskLevel.HIGH,
            has_evaluation_criteria=True,
            criteria_measurable=True,
        )
        applicable, conf, rationale = _select_adversarial_verify(task)
        self.assertTrue(applicable)
        self.assertGreaterEqual(conf, 0.85)
        self.assertIn("self-preferential bias", rationale)

    def test_02_low_risk_rejected(self):
        """低风险不适用"""
        task = TaskFeature(risk_level=RiskLevel.LOW)
        applicable, _, _ = _select_adversarial_verify(task)
        self.assertFalse(applicable)

    def test_03_no_criteria_rejected(self):
        """无评估准则不适用"""
        task = TaskFeature(
            risk_level=RiskLevel.HIGH,
            has_evaluation_criteria=False,
        )
        applicable, _, _ = _select_adversarial_verify(task)
        self.assertFalse(applicable)

    def test_04_unmeasurable_criteria_rejected(self):
        """准则不可测量不适用"""
        task = TaskFeature(
            risk_level=RiskLevel.HIGH,
            has_evaluation_criteria=True,
            criteria_measurable=False,
        )
        applicable, _, _ = _select_adversarial_verify(task)
        self.assertFalse(applicable)

    def test_05_critical_risk_highest_confidence(self):
        """critical 风险等级 confidence 最高"""
        task = TaskFeature(
            risk_level=RiskLevel.CRITICAL,
            has_evaluation_criteria=True,
            criteria_measurable=True,
        )
        applicable, conf, _ = _select_adversarial_verify(task)
        self.assertTrue(applicable)
        self.assertGreaterEqual(conf, 0.90)


# ============================================================================
# 4. 模式库（PatternLibrary）测试
# ============================================================================

class TestPatternLibrary(unittest.TestCase):
    """测试 PatternLibrary 加载、校验、查询"""

    def test_01_default_library_loads_6_patterns(self):
        """默认库加载 6 大模式（Phase 5 全部沉淀）"""
        lib = PatternLibrary()
        self.assertEqual(lib.size(), 6)
        self.assertIn("classifier-dispatch", lib.list_ids())
        self.assertIn("fan-out-aggregate", lib.list_ids())
        self.assertIn("adversarial-verify", lib.list_ids())
        # Phase 5 新增
        self.assertIn("generate-filter", lib.list_ids())
        self.assertIn("tournament", lib.list_ids())
        self.assertIn("loop-until-done", lib.list_ids())

    def test_01b_legacy_library_loads_3_patterns(self):
        """向后兼容：use_all_patterns=False 加载 3 个核心模式（Phase 0 行为）"""
        lib = PatternLibrary(use_all_patterns=False)
        self.assertEqual(lib.size(), 3)
        self.assertNotIn("generate-filter", lib.list_ids())
        self.assertNotIn("tournament", lib.list_ids())
        self.assertNotIn("loop-until-done", lib.list_ids())

    def test_02_get_existing_pattern(self):
        """获取存在的模式"""
        lib = PatternLibrary()
        p = lib.get("classifier-dispatch")
        self.assertIsNotNone(p)
        self.assertEqual(p.pattern_id, "classifier-dispatch")

    def test_03_get_nonexistent_returns_none(self):
        """获取不存在的模式返回 None"""
        lib = PatternLibrary()
        p = lib.get("nonexistent-pattern")
        self.assertIsNone(p)

    def test_04_list_all_sorted_by_priority(self):
        """list_all 按 priority 升序"""
        lib = PatternLibrary()
        patterns = lib.list_all()
        priorities = [p.priority for p in patterns]
        self.assertEqual(priorities, sorted(priorities))

    def test_05_invalid_pattern_raises_value_error(self):
        """加载损坏模式必须抛 ValueError（不允许降级）"""
        bad = WorkflowPattern(
            pattern_id="BadPattern",  # 命名违规
            name="t",
            version="1.0",
            description="d",
            isolation_requirement=IsolationLevel.NONE,
            default_token_budget=1000,
            applicable_roles=["test-expert"],
            priority=10,
            parameters_schema={},
            example_parameters={},
            failure_modes=[FailureMode("a", "b", "c")],
            success_criteria=["a"],
            not_applicable_scenarios=["a"],
        )
        with self.assertRaises(ValueError) as ctx:
            PatternLibrary(patterns=[bad])
        self.assertIn("schema 校验失败", str(ctx.exception))


# ============================================================================
# 5. PatternComposer 端到端选择测试
# ============================================================================

class TestPatternComposerEndToEnd(unittest.TestCase):
    """测试 PatternComposer 端到端选择（与 pattern_examples/*.json 对齐）"""

    def setUp(self):
        """每个用例前创建新选择器"""
        self.composer = create_default_composer()

    def test_01_classifier_dispatch_scenario(self):
        """场景 1：客服工单分流 → classifier-dispatch"""
        # 对应 pattern_examples/classifier-dispatch.json
        task = TaskFeature(
            type_variants=4,
            subtask_count=200,
            subtask_homogeneous=False,
            risk_level=RiskLevel.LOW,
            task_type="customer_ticket",
            task_complexity=4,
        )
        sel = self.composer.select(task)
        self.assertEqual(sel.pattern_id, "classifier-dispatch")
        self.assertTrue(sel.applicable)
        self.assertGreaterEqual(sel.confidence, 0.8)
        self.assertEqual(sel.fallback_pattern, "sequential")
        # 关键参数应被填充
        self.assertIn("classifier_role", sel.parameters)
        self.assertIn("route_table", sel.parameters)

    def test_02_fan_out_aggregate_scenario(self):
        """场景 2：50 文件安全审查 → fan-out-aggregate"""
        # 对应 pattern_examples/fan-out-aggregate.json
        task = TaskFeature(
            type_variants=1,
            subtask_count=50,
            subtask_homogeneous=True,
            subtask_independent=True,
            risk_level=RiskLevel.HIGH,
            target_is_git=True,
            task_type="security_audit",
            task_complexity=7,
        )
        sel = self.composer.select(task)
        self.assertEqual(sel.pattern_id, "fan-out-aggregate")
        self.assertTrue(sel.applicable)
        self.assertGreaterEqual(sel.confidence, 0.7)
        # 关键参数：fanout_count <= 10（Phase 0 硬上限）
        self.assertLessEqual(sel.parameters["fanout_count"], 10)
        # worktree 隔离
        self.assertEqual(sel.parameters["subagent_isolation"], "worktree")

    def test_03_adversarial_verify_scenario(self):
        """场景 3：架构方案对抗验证 → adversarial-verify"""
        # 对应 pattern_examples/adversarial-verify.json
        task = TaskFeature(
            type_variants=1,
            subtask_count=1,
            risk_level=RiskLevel.HIGH,
            has_evaluation_criteria=True,
            criteria_measurable=True,
            task_type="architecture_design",
            task_complexity=8,
        )
        sel = self.composer.select(task)
        self.assertEqual(sel.pattern_id, "adversarial-verify")
        self.assertTrue(sel.applicable)
        self.assertGreaterEqual(sel.confidence, 0.85)
        # 高风险 → 隔离级别应为 full
        self.assertEqual(sel.parameters["verifier_isolation"], "full")
        # 准则 ≥ 3 条
        self.assertGreaterEqual(len(sel.parameters["evaluation_criteria"]), 3)

    def test_04_no_pattern_applicable_fallback_to_sequential(self):
        """场景 4：简单任务 → 无模式适用，回退顺序"""
        task = TaskFeature(
            type_variants=1,
            subtask_count=1,
            subtask_homogeneous=True,
            subtask_independent=True,
            risk_level=RiskLevel.LOW,
            has_evaluation_criteria=False,
            task_type="typo_fix",
            task_complexity=1,
        )
        sel = self.composer.select(task)
        self.assertFalse(sel.applicable)
        self.assertIsNone(sel.pattern_id)
        self.assertEqual(sel.fallback_pattern, "sequential")
        self.assertIsNotNone(sel.rejection_reason)

    def test_05_decision_tree_priority_classifier_first(self):
        """场景 5：异构任务优先选 classifier-dispatch（priority=10）"""
        # 即使其他条件也满足，classifier 应胜出
        task = TaskFeature(
            type_variants=4,  # 异构
            subtask_count=50,  # 也满足 fan-out
            subtask_homogeneous=True,
            subtask_independent=True,
            risk_level=RiskLevel.HIGH,  # 也满足 adversarial
            has_evaluation_criteria=True,
            criteria_measurable=True,
        )
        sel = self.composer.select(task)
        self.assertEqual(sel.pattern_id, "classifier-dispatch")

    def test_06_decision_tree_priority_fanout_before_adversarial(self):
        """场景 6：同质 + 高风险 → fan-out 优先于 adversarial"""
        # 没有 type_variants>=3，没有准则 → fan-out 胜出
        task = TaskFeature(
            type_variants=1,
            subtask_count=50,
            subtask_homogeneous=True,
            subtask_independent=True,
            risk_level=RiskLevel.HIGH,
            has_evaluation_criteria=False,  # 关键：没准则
        )
        sel = self.composer.select(task)
        self.assertEqual(sel.pattern_id, "fan-out-aggregate")


# ============================================================================
# 6. 性能基线测试
# ============================================================================

class TestPerformanceBaseline(unittest.TestCase):
    """测试模式选择性能基线（架构师审查要求 < 100ms）"""

    def setUp(self):
        self.composer = create_default_composer()

    def test_01_single_selection_under_100ms(self):
        """单次选择 < 100ms"""
        task = TaskFeature(
            type_variants=3,
            subtask_count=100,
            subtask_homogeneous=True,
            subtask_independent=True,
            risk_level=RiskLevel.HIGH,
            has_evaluation_criteria=True,
            criteria_measurable=True,
            task_type="complex_task",
            task_complexity=8,
        )

        # 预热（避免冷启动影响）
        self.composer.select(task)

        # 测量 100 次
        start = time.perf_counter()
        for _ in range(100):
            self.composer.select(task)
        elapsed_ms = (time.perf_counter() - start) * 1000.0 / 100

        self.assertLess(
            elapsed_ms, PatternComposer.PERFORMANCE_BUDGET_MS,
            f"平均单次选择耗时 {elapsed_ms:.2f}ms 超出预算 "
            f"{PatternComposer.PERFORMANCE_BUDGET_MS}ms"
        )

    def test_02_1000_sequential_selections_under_100ms_each(self):
        """1000 次连续选择，每次 < 100ms"""
        # 模拟 1000 个不同任务
        start = time.perf_counter()
        for i in range(1000):
            task = TaskFeature(
                type_variants=(i % 5) + 1,  # 1-5 循环
                subtask_count=(i % 60) + 1,  # 1-60 循环
                subtask_homogeneous=True,
                subtask_independent=True,
                risk_level=_RISK_LEVELS[i % len(_RISK_LEVELS)],
                has_evaluation_criteria=(i % 2 == 0),
                criteria_measurable=(i % 2 == 0),
                task_type=f"task_{i}",
                task_complexity=(i % 10) + 1,
            )
            sel = self.composer.select(task)
            self.assertIsNotNone(sel)
        elapsed_ms = (time.perf_counter() - start) * 1000.0 / 1000
        self.assertLess(
            elapsed_ms, PatternComposer.PERFORMANCE_BUDGET_MS,
            f"1000 次平均耗时 {elapsed_ms:.2f}ms 超出预算"
        )


# ============================================================================
# 7. 持久化反哺测试
# ============================================================================

class TestFingerprintIntegration(unittest.TestCase):
    """测试与 PerformanceFingerprint 的集成"""

    def setUp(self):
        # 使用临时目录避免污染生产数据
        self.tmpdir = tempfile.mkdtemp(prefix="pattern_composer_test_")
        self.fingerprint = PerformanceFingerprint(
            agent_id="test_composer",
            storage_path=self.tmpdir,
        )
        self.composer = PatternComposer(fingerprint=self.fingerprint)

    def test_01_record_outcome_writes_to_fingerprint(self):
        """record_outcome 必须真实写入 PerformanceFingerprint"""
        task = TaskFeature(
            type_variants=4,
            task_type="customer_ticket",
            task_complexity=5,
        )
        sel = self.composer.select(task)

        # 记录成功
        self.composer.record_outcome(
            task=task,
            selection=sel,
            success=True,
            execution_time_seconds=1.5,
        )

        # 验证画像收到记录
        self.assertGreaterEqual(self.fingerprint.total_executions, 1)
        # strategy 字段应包含模式 ID
        last_record = self.fingerprint.records[-1]
        self.assertEqual(last_record.strategy, "classifier-dispatch")
        self.assertTrue(last_record.success)
        self.assertEqual(last_record.task_complexity, 5)

    def test_02_record_failure_with_error_type(self):
        """失败执行 + 错误类型应正确记录"""
        task = TaskFeature(subtask_count=50, task_type="security_audit", task_complexity=7)
        sel = self.composer.select(task)

        self.composer.record_outcome(
            task=task,
            selection=sel,
            success=False,
            execution_time_seconds=2.0,
            error_type="barrier_timeout",
        )

        # 验证失败模式被画像捕获
        self.assertIn("barrier_timeout", self.fingerprint.failure_patterns)
        fp = self.fingerprint.failure_patterns["barrier_timeout"]
        self.assertGreaterEqual(fp.frequency, 1)

    def test_03_no_fingerprint_does_not_crash(self):
        """未配置指纹时不应崩溃"""
        composer = PatternComposer(fingerprint=None)
        task = TaskFeature(type_variants=3, task_type="x", task_complexity=5)
        sel = composer.select(task)
        # 不应抛异常
        composer.record_outcome(
            task=task,
            selection=sel,
            success=True,
            execution_time_seconds=1.0,
        )


# ============================================================================
# 8. 便捷函数测试
# ============================================================================

class TestConvenienceFunctions(unittest.TestCase):
    """测试便捷函数"""

    def test_01_select_pattern_for_task(self):
        """select_pattern_for_task 一键调用"""
        task = TaskFeature(
            type_variants=4,
            subtask_count=100,
            task_type="x",
            task_complexity=5,
        )
        sel = select_pattern_for_task(task)
        self.assertEqual(sel.pattern_id, "classifier-dispatch")

    def test_02_create_default_composer_returns_usable_instance(self):
        """create_default_composer 返回可用实例（含 6 大模式）"""
        c = create_default_composer()
        self.assertIsInstance(c, PatternComposer)
        self.assertEqual(c.library.size(), 6)


# ============================================================================
# 9. 与 pattern_examples/*.json 数据一致性测试
# ============================================================================

class TestJsonExamplesConsistency(unittest.TestCase):
    """测试 pattern_composer 的输出与 JSON 示例文件结构一致"""

    def setUp(self):
        self.composer = create_default_composer()
        self.examples_dir = SCRIPTS_DIR.parent / "docs" / "dev" / "pattern_examples"

    def test_01_classifier_example_keys_match(self):
        """classifier-dispatch 示例的 keys 与选择结果一致"""
        example_path = self.examples_dir / "classifier-dispatch.json"
        if not example_path.exists():
            self.skipTest(f"示例文件不存在：{example_path}")

        with open(example_path, "r", encoding="utf-8") as f:
            example = json.load(f)

        # 关键字段必须存在
        self.assertIn("example_selection_output", example)
        output = example["example_selection_output"]
        self.assertEqual(output["pattern_id"], "classifier-dispatch")
        self.assertIn("confidence", output)
        self.assertIn("rationale", output)
        self.assertIn("parameters", output)
        self.assertIn("estimated_token_budget", output)
        self.assertIn("fallback_pattern", output)

        # 运行时输出应与示例结构一致
        task = TaskFeature(
            type_variants=4,
            subtask_count=200,
            risk_level=RiskLevel.LOW,
            task_type="customer_ticket",
            task_complexity=4,
        )
        sel = self.composer.select(task)
        self.assertEqual(sel.pattern_id, output["pattern_id"])

    def test_02_fanout_example_keys_match(self):
        """fan-out-aggregate 示例的 keys 与选择结果一致"""
        example_path = self.examples_dir / "fan-out-aggregate.json"
        if not example_path.exists():
            self.skipTest(f"示例文件不存在：{example_path}")

        with open(example_path, "r", encoding="utf-8") as f:
            example = json.load(f)

        output = example["example_selection_output"]
        # fanout_count 必为 1-10
        self.assertLessEqual(output["parameters"]["fanout_count"], 10)
        # subagent_isolation 必为合法值
        self.assertIn(output["parameters"]["subagent_isolation"], ["worktree", "context", "full"])

    def test_03_adversarial_example_keys_match(self):
        """adversarial-verify 示例的 keys 与选择结果一致"""
        example_path = self.examples_dir / "adversarial-verify.json"
        if not example_path.exists():
            self.skipTest(f"示例文件不存在：{example_path}")

        with open(example_path, "r", encoding="utf-8") as f:
            example = json.load(f)

        output = example["example_selection_output"]
        # criteria ≥ 3
        self.assertGreaterEqual(len(output["parameters"]["evaluation_criteria"]), 3)
        # 高风险 → full 隔离
        self.assertEqual(output["parameters"]["verifier_isolation"], "full")


# ============================================================================
# 10. 一键 CLI 入口测试
# ============================================================================

class TestCLIEntry(unittest.TestCase):
    """测试 CLI 入口"""

    def test_01_cli_runs_without_error(self):
        """CLI 入口可正常执行"""
        import subprocess

        result = subprocess.run(
            [
                sys.executable,
                str(DYNAMIC_WORKFLOW_DIR / "pattern_composer.py"),
                "--task-type", "security_audit",
                "--type-variants", "1",
                "--subtask-count", "50",
                "--risk-level", "high",
                "--target-is-git",
                "--description", "test audit",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        self.assertEqual(result.returncode, 0, f"CLI 失败：{result.stderr}")
        # 输出应为合法 JSON
        output = json.loads(result.stdout)
        self.assertIn("pattern_id", output)
        self.assertEqual(output["pattern_id"], "fan-out-aggregate")

    def test_02_cli_with_classifier_args(self):
        """CLI 异构任务参数应触发 classifier-dispatch"""
        import subprocess

        result = subprocess.run(
            [
                sys.executable,
                str(DYNAMIC_WORKFLOW_DIR / "pattern_composer.py"),
                "--task-type", "customer_ticket",
                "--type-variants", "4",
                "--subtask-count", "200",
                "--risk-level", "low",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        self.assertEqual(result.returncode, 0)
        output = json.loads(result.stdout)
        self.assertEqual(output["pattern_id"], "classifier-dispatch")


# ============================================================================
# 入口
# ============================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
