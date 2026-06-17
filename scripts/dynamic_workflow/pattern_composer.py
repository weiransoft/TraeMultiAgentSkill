#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dynamic Workflows 模式选择器（Pattern Composer）

Phase 0 + Phase 5 累计实现：
- 6 大模式全部沉淀（Phase 0: 3 个核心；Phase 5: 补齐 generate-filter / tournament / loop-until-done）
- 基于任务特征（TaskFeature）选择最合适的模式
- 模式库 schema 校验（防止损坏的模式定义被使用）
- 通过 PerformanceFingerprint 实现"模式选择 → 执行 → 画像反哺"闭环
- 模式不适用时优雅回退到 sequential（不强行套模式）

设计约束（来自 DYNAMIC_WORKFLOWS_INTEGRATION.md §3.0）：
- 🔴 持久化复用：禁止新建并行存储，复用 PerformanceFingerprint
- 🔴 V2 不修改：本模块独立运行，不触碰 V2 引擎
- 🔴 安全：模式库加载时严格 schema 校验
- 🔴 模式上限 6：Phase 5 补齐 6 大模式，不再扩展
- 🔴 一阶段一模块：仅模式选择器，不引入沙箱/路由/预算

参考来源：
- [DYNAMIC_WORKFLOWS_INTEGRATION.md v1.1]
- [PATTERNS_REFERENCE.md]
- [pattern_examples/*.json]
- [Anthropic Dynamic Workflows](https://mp.weixin.qq.com/s/ZGOlA1IPSQaK3MXv_5fStQ)

作者：trae-multi-agent 融合 Phase 0 + Phase 5
创建日期：2026-06-03（Phase 5 扩展：2026-06-04）
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 复用现有 PerformanceFingerprint（架构师审查 §3.0.1 强约束）
try:
    from performance_fingerprint import PerformanceFingerprint
except ImportError:
    # 当作为独立模块加载时（tests/ 目录场景），添加 scripts 目录到 sys.path
    import sys
    SCRIPTS_DIR = Path(__file__).resolve().parent.parent
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    from performance_fingerprint import PerformanceFingerprint


# ============================================================================
# 日志配置
# ============================================================================

logger = logging.getLogger("dynamic_workflow.pattern_composer")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ============================================================================
# 枚举定义
# ============================================================================

class IsolationLevel(str, Enum):
    """
    隔离级别枚举

    对齐 PATTERNS_REFERENCE.md §0.2 中各模式的 isolation_requirement 字段。
    """
    NONE = "none"            # 无隔离（默认顺序执行）
    CONTEXT = "context"      # 独立 context window
    WORKTREE = "worktree"    # 独立 worktree（git 仓库）
    FULL = "full"            # context + worktree 双隔离


class RiskLevel(str, Enum):
    """
    风险等级枚举

    用于 adversarial-verify 等模式的 pass_threshold 决策。
    """
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ============================================================================
# 数据类定义
# ============================================================================

@dataclass
class TaskFeature:
    """
    任务特征：模式选择的输入

    字段必须可序列化（用于画像反哺和持久化）。
    字段设计对齐 PATTERNS_REFERENCE.md §3 决策树所需的所有判断维度。
    """
    # 任务类型变体数（多少种不同类型的子任务）
    type_variants: int = 1

    # 子任务数
    subtask_count: int = 1

    # 子任务是否同质
    subtask_homogeneous: bool = True

    # 子任务是否独立（无强依赖）
    subtask_independent: bool = True

    # 风险等级
    risk_level: RiskLevel = RiskLevel.LOW

    # 是否有可机器/人工验证的评估准则
    has_evaluation_criteria: bool = False

    # 准则可测量性
    criteria_measurable: bool = False

    # 未知工作量（用 stop_condition 解决）
    workload_unknown: bool = False

    # 是否有清晰停止条件
    has_stop_condition: bool = False

    # 候选数（多方案选型场景）
    candidate_count: int = 0

    # 是否基于对比（两两 PK 优于绝对打分）
    comparison_based: bool = False

    # 是否创意探索（容忍重复候选）
    is_creative: bool = False

    # 目标环境是否为 Git 仓库（worktree 隔离前置条件）
    target_is_git: bool = True

    # 任务原始描述（用于画像反哺）
    task_description: str = ""

    # 任务类型字符串（用于画像分类）
    task_type: str = "general"

    # 任务复杂度 1-10（用于画像）
    task_complexity: int = 5

    # 任务自定义特征（扩展点）
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（用于画像存储）"""
        return {
            "type_variants": self.type_variants,
            "subtask_count": self.subtask_count,
            "subtask_homogeneous": self.subtask_homogeneous,
            "subtask_independent": self.subtask_independent,
            "risk_level": self.risk_level.value,
            "has_evaluation_criteria": self.has_evaluation_criteria,
            "criteria_measurable": self.criteria_measurable,
            "workload_unknown": self.workload_unknown,
            "has_stop_condition": self.has_stop_condition,
            "candidate_count": self.candidate_count,
            "comparison_based": self.comparison_based,
            "is_creative": self.is_creative,
            "target_is_git": self.target_is_git,
            "task_description": self.task_description,
            "task_type": self.task_type,
            "task_complexity": self.task_complexity,
            "extra": self.extra,
        }


@dataclass
class FailureMode:
    """
    失败模式：模式使用过程中的已知风险点

    字段对齐 pattern_examples/*.json 中 failure_modes 数组元素。
    """
    name: str
    trigger: str
    mitigation: str


@dataclass
class WorkflowPattern:
    """
    工作流模式：声明式可复用模板

    字段定义严格对齐 [PATTERNS_REFERENCE.md §5.1] 与
    [pattern_examples/*.json] 的元数据结构。

    加载时必须通过 PatternLibrary 的 schema 校验。
    """
    pattern_id: str
    name: str
    version: str
    description: str
    isolation_requirement: IsolationLevel
    default_token_budget: int
    applicable_roles: List[str]
    priority: int
    parameters_schema: Dict[str, Any]
    example_parameters: Dict[str, Any]
    failure_modes: List[FailureMode]
    success_criteria: List[str]
    not_applicable_scenarios: List[str]

    # 可选：选择规则的 lambda 函数（决策树节点）
    # 接受 TaskFeature，返回 (applicable: bool, confidence: float, rationale: str)
    selector: Optional[Any] = None

    def validate(self) -> List[str]:
        """
        实例级 schema 校验（粗粒度）

        Returns:
            List[str]: 错误信息列表（空列表表示通过）
        """
        errors: List[str] = []

        # pattern_id 必须符合命名规范：kebab-case
        if not re.match(r"^[a-z][a-z0-9-]*[a-z0-9]$", self.pattern_id):
            errors.append(
                f"pattern_id '{self.pattern_id}' 不符合 kebab-case 命名规范"
            )

        # 必填字段非空
        if not self.name:
            errors.append("name 不能为空")
        if not self.description:
            errors.append("description 不能为空")

        # applicable_roles 至少 1 个
        if not self.applicable_roles:
            errors.append("applicable_roles 至少需要 1 个角色")

        # isolation_requirement 必须是合法枚举
        if not isinstance(self.isolation_requirement, IsolationLevel):
            errors.append(
                f"isolation_requirement 必须是 IsolationLevel 枚举，"
                f"实际为 {type(self.isolation_requirement).__name__}"
            )

        # default_token_budget 必须在合理范围
        if not (100 <= self.default_token_budget <= 1000000):
            errors.append(
                f"default_token_budget={self.default_token_budget} 超出合理范围 [100, 1000000]"
            )

        # priority 必须在合理范围
        if not (0 <= self.priority <= 100):
            errors.append(
                f"priority={self.priority} 超出合理范围 [0, 100]"
            )

        # parameters_schema 必须是 dict
        if not isinstance(self.parameters_schema, dict):
            errors.append("parameters_schema 必须是 dict 类型")

        # failure_modes 至少 1 个
        if not self.failure_modes:
            errors.append("failure_modes 至少需要 1 个失败模式")

        # success_criteria 至少 1 个
        if not self.success_criteria:
            errors.append("success_criteria 至少需要 1 个成功标准")

        # not_applicable_scenarios 至少 1 个
        if not self.not_applicable_scenarios:
            errors.append("not_applicable_scenarios 至少需要 1 个不适用场景")

        return errors


@dataclass
class PatternSelection:
    """
    模式选择结果

    字段对齐 [PATTERNS_REFERENCE.md §5.2] 与
    [pattern_examples/*.json] 的 example_selection_output 结构。
    """
    pattern_id: Optional[str]            # 选中的模式 ID；None 表示不需要模式
    applicable: bool                      # 是否适用
    confidence: float                     # 0.0-1.0 置信度
    rationale: str                        # 选择理由（必须可解释）
    parameters: Dict[str, Any]            # 实例化后的参数
    estimated_token_budget: int           # 预估 token 预算
    fallback_pattern: Optional[str]       # 不适用时的回退模式
    rejection_reason: Optional[str] = None  # 不适用时的拒绝原因

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（用于画像存储与 JSON 输出）"""
        return {
            "pattern_id": self.pattern_id,
            "applicable": self.applicable,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "parameters": self.parameters,
            "estimated_token_budget": self.estimated_token_budget,
            "fallback_pattern": self.fallback_pattern,
            "rejection_reason": self.rejection_reason,
        }


# ============================================================================
# 模式选择规则（决策树节点）
# ============================================================================

def _select_classifier_dispatch(task: TaskFeature) -> Tuple[bool, float, str]:
    """
    模式 1：classifier-dispatch 选择规则

    适用条件：任务存在 ≥ 3 种异构类型
    强约束：单一类型任务不适用
    """
    if task.type_variants >= 3:
        confidence = min(0.95, 0.7 + 0.05 * task.type_variants)
        rationale = (
            f"任务存在 {task.type_variants} 种异构类型，"
            f"单一流程无法高效处理；启用分类器路由到不同子流程。"
        )
        return True, confidence, rationale

    return False, 0.0, (
        f"任务类型数 {task.type_variants} < 3，无需分类器，顺序执行即可。"
    )


def _select_fan_out_aggregate(task: TaskFeature) -> Tuple[bool, float, str]:
    """
    模式 2：fan-out-aggregate 选择规则

    适用条件：
    - 子任务数 ≥ 10 且同质
    - 子任务独立
    - 目标环境为 Git 仓库（worktree 隔离前置）

    排除：
    - 任务类型已 ≥ 3（优先用 classifier-dispatch）
    - 资源受限（通过 target_is_git 间接判断）
    """
    # 已被 classifier-dispatch 接管，不重复推荐
    if task.type_variants >= 3:
        return False, 0.0, "异构任务已由 classifier-dispatch 模式处理"

    # 核心条件
    if task.subtask_count >= 10 and task.subtask_homogeneous and task.subtask_independent:
        confidence = min(0.95, 0.7 + 0.005 * task.subtask_count)

        # 关键反模式：单 context 下的 Agentic laziness
        laziness_risk = ""
        if task.subtask_count >= 50:
            laziness_risk = (
                f"（{task.subtask_count} 个子任务，单 context 下 LLM 通常只完成前 20% "
                f"就宣布完成，Agentic laziness 痛点突出）"
            )

        # 隔离前置检查
        isolation_note = ""
        if not task.target_is_git:
            isolation_note = "（注意：目标环境非 Git 仓库，worktree 隔离不可用）"

        rationale = (
            f"{task.subtask_count} 个同质子任务且可独立处理，"
            f"扇出并行可显著加速{laziness_risk}{isolation_note}。"
        )
        return True, confidence, rationale

    return False, 0.0, (
        f"子任务数 {task.subtask_count} < 10 或非同质/非独立，"
        f"扇出开销大于收益，顺序执行即可。"
    )


def _select_adversarial_verify(task: TaskFeature) -> Tuple[bool, float, str]:
    """
    模式 3：adversarial-verify 选择规则

    适用条件：
    - 风险等级 ≥ medium
    - 有评估准则
    - 准则可测量

    反模式：
    - 主观性强的任务（设计审美）
    - 没有评估准则
    - 简单任务（成本不划算）
    """
    # 风险等级过滤
    if task.risk_level == RiskLevel.LOW:
        return False, 0.0, "风险等级为 low，对抗验证成本不划算"

    # 评估准则过滤
    if not task.has_evaluation_criteria:
        return False, 0.0, "没有评估准则，对抗验证无法判定通过/不通过"

    # 可测量性过滤
    if not task.criteria_measurable:
        return False, 0.0, "评估准则不可测量，对抗验证无意义"

    # 高风险时置信度更高
    if task.risk_level == RiskLevel.HIGH:
        confidence = 0.88
    elif task.risk_level == RiskLevel.CRITICAL:
        confidence = 0.95
    else:
        confidence = 0.80

    # self-preferential bias 痛点
    bias_note = ""
    if task.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        bias_note = (
            "（高风险任务存在 self-preferential bias：让模型验证自己产出，"
            "通过率虚高 30%+）"
        )

    # 隔离级别决策
    isolation = (
        IsolationLevel.FULL
        if task.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        else IsolationLevel.CONTEXT
    )

    rationale = (
        f"任务风险等级为 {task.risk_level.value}，"
        f"且已定义可测量评估准则；启用对抗验证{bias_note}。"
        f"验证者隔离级别：{isolation.value}。"
    )
    return True, confidence, rationale


# ============================================================================
# 模式 4：generate-filter（生成与筛选）选择规则
# ============================================================================

def _select_generate_filter(task: TaskFeature) -> Tuple[bool, float, str]:
    """
    模式 4：generate-filter 选择规则

    适用条件：
    - 创意探索任务（命名 / 标语 / 方案）
    - 容忍重复候选（去重器可处理）
    - 评估标准可量化（filter_criteria 可写为可测量指标）
    - 候选数 >= 3

    反模式：
    - 候选不能重复（每生成都贵）
    - 评估标准主观（筛选结果不稳定）
    - 候选数 < 3（单次生成即可）
    """
    # 已被更高优先级模式接管时不重复推荐
    if task.type_variants >= 3:
        return False, 0.0, "异构任务已由 classifier-dispatch 模式处理"

    if task.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        return False, 0.0, "高风险任务应优先用 adversarial-verify，不适合生成筛选"

    if not task.is_creative:
        return False, 0.0, "非创意探索任务，无需大量生成后筛选"

    if not task.has_evaluation_criteria:
        return False, 0.0, "无评估准则，筛选无依据"

    if task.candidate_count < 3:
        return False, 0.0, (
            f"候选数 {task.candidate_count} < 3，"
            f"单次生成即可，无需 generate-filter"
        )

    confidence = min(0.92, 0.7 + 0.04 * task.candidate_count)
    rationale = (
        f"创意任务且候选数={task.candidate_count} ≥ 3，"
        f"通过'概率质量'（多生成后筛选）提高产出质量。"
    )
    return True, confidence, rationale


# ============================================================================
# 模式 5：tournament（锦标赛模式）选择规则
# ============================================================================

def _select_tournament(task: TaskFeature) -> Tuple[bool, float, str]:
    """
    模式 5：tournament 选择规则

    适用条件：
    - 多方案选型（架构 / 库选择 / UI 配色）
    - 候选数 3-8（太少无需锦标赛，太多成本爆炸）
    - 基于对比（两两 PK 优于绝对打分）
    - 有明确裁判标准

    反模式：
    - 候选无对比性（完全不同的产物）
    - 评估需要全局视角（PK 信息不足）
    - 候选数 < 3（顺序打分即可）
    - 候选数 > 8（成本爆炸）
    """
    if task.type_variants >= 3:
        return False, 0.0, "异构任务已由 classifier-dispatch 模式处理"

    if task.candidate_count < 3:
        return False, 0.0, (
            f"候选数 {task.candidate_count} < 3，"
            f"无需锦标赛，顺序评估即可"
        )

    if task.candidate_count > 8:
        return False, 0.0, (
            f"候选数 {task.candidate_count} > 8，"
            f"锦标赛成本爆炸，应先用 generate-filter 收敛候选"
        )

    if not task.comparison_based:
        return False, 0.0, "非基于对比的评估，锦标赛无意义"

    if not task.has_evaluation_criteria:
        return False, 0.0, "无评估准则，裁判无标准"

    confidence = min(0.93, 0.75 + 0.025 * task.candidate_count)
    rationale = (
        f"{task.candidate_count} 个候选方案需择优，"
        f"两两 PK 比绝对打分更可靠（信息熵更高）。"
    )
    return True, confidence, rationale


# ============================================================================
# 模式 6：loop-until-done（循环直到完成）选择规则
# ============================================================================

def _select_loop_until_done(task: TaskFeature) -> Tuple[bool, float, str]:
    """
    模式 6：loop-until-done 选择规则

    适用条件：
    - 未知工作量（用 stop_condition 解决）
    - 清晰停止条件（无新发现 / 无错误日志）
    - 每次迭代可积累上下文

    反模式：
    - 工作量已知（用顺序即可）
    - 停止条件模糊（容易死循环）
    - 单次执行可完成
    """
    if task.type_variants >= 3:
        return False, 0.0, "异构任务已由 classifier-dispatch 模式处理"

    if task.subtask_count >= 10 and task.subtask_homogeneous and task.subtask_independent:
        return False, 0.0, "大量同质子任务已由 fan-out-aggregate 处理"

    if not task.workload_unknown:
        return False, 0.0, "工作量已知，无需循环，顺序执行即可"

    if not task.has_stop_condition:
        return False, 0.0, (
            "无清晰停止条件，loop-until-done 容易陷入死循环，"
            "应选择确定性模式（顺序 / 扇出）"
        )

    # 置信度：工作量越不确定、停止条件越清晰，置信度越高
    confidence = 0.80
    rationale = (
        "未知工作量任务 + 清晰停止条件，"
        "用循环迭代替代固定次数（goal drift 痛点缓解）。"
    )
    return True, confidence, rationale


# ============================================================================
# 3 大核心模式定义
# ============================================================================

# 模式 1：classifier-dispatch（分类并行动）
PATTERN_CLASSIFIER_DISPATCH = WorkflowPattern(
    pattern_id="classifier-dispatch",
    name="分类并行动",
    version="1.0",
    description=(
        "用分类器判断任务类型 → 路由到不同子流程或子智能体。"
        "适用于多种异构任务混处理的场景。"
    ),
    isolation_requirement=IsolationLevel.NONE,
    default_token_budget=4000,
    applicable_roles=["product-manager", "test-expert", "architect", "solo-coder"],
    priority=10,
    parameters_schema={
        "type": "object",
        "required": ["classifier_role", "route_table", "fallback_route"],
        "properties": {
            "classifier_role": {
                "type": "string",
                "enum": ["product-manager", "test-expert", "architect", "solo-coder"],
            },
            "route_table": {"type": "object"},
            "fallback_route": {"type": "string"},
            "classification_confidence_threshold": {
                "type": "number",
                "default": 0.7,
                "minimum": 0.0,
                "maximum": 1.0,
            },
        },
    },
    example_parameters={
        "classifier_role": "test-expert",
        "route_table": {
            "bug": {"target_pattern": "sequential", "target_role": "solo-coder"},
            "feature_request": {"target_pattern": "sequential", "target_role": "product-manager"},
            "incident": {"target_pattern": "adversarial-verify", "target_role": "solo-coder"},
        },
        "fallback_route": "solo-coder",
        "classification_confidence_threshold": 0.7,
    },
    failure_modes=[
        FailureMode(
            name="分类不准确",
            trigger="训练样本不足 / 任务表达歧义",
            mitigation="保留 fallback 路由 + 反馈回流到 PerformanceFingerprint",
        ),
        FailureMode(
            name="路由死循环",
            trigger="route_table 中目标互指形成环",
            mitigation="PatternLibrary 加载时静态检测路由环",
        ),
        FailureMode(
            name="分类开销过大",
            trigger="任务量极大（> 10000）",
            mitigation="引入分类缓存（LRU 1000 条）",
        ),
    ],
    success_criteria=[
        "分类器准确率 ≥ 90%",
        "路由到目标流程的成功率 ≥ 95%",
        "整体处理时间 < 顺序执行的 80%",
    ],
    not_applicable_scenarios=[
        "任务类型单一（直接顺序执行即可）",
        "分类器本身准确率 < 70%（错误分类比不分类更糟）",
        "子任务数 < 5（分类开销大于收益）",
    ],
    selector=_select_classifier_dispatch,
)


# 模式 2：fan-out-aggregate（扇出与聚合）
PATTERN_FAN_OUT_AGGREGATE = WorkflowPattern(
    pattern_id="fan-out-aggregate",
    name="扇出与聚合",
    version="1.0",
    description=(
        "任务拆 N 份并行处理 → 屏障等待 → 聚合为单一结果。"
        "每个子任务拥有独立 context，规避单 context 下的 Agentic laziness 痛点。"
    ),
    isolation_requirement=IsolationLevel.WORKTREE,
    default_token_budget=12000,
    applicable_roles=["test-expert", "solo-coder", "architect", "product-manager"],
    priority=20,
    parameters_schema={
        "type": "object",
        "required": ["fanout_count", "subagent_role", "aggregator_role", "aggregation_strategy"],
        "properties": {
            "fanout_count": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "Phase 0 硬上限 10",
            },
            "fanout_strategy": {"type": "string", "enum": ["static", "dynamic"]},
            "subagent_role": {"type": "string"},
            "subagent_isolation": {
                "type": "string",
                "enum": ["worktree", "context", "full"],
            },
            "barrier_timeout_seconds": {"type": "integer", "default": 3600},
            "aggregator_role": {"type": "string"},
            "aggregation_strategy": {
                "type": "string",
                "enum": ["concat", "vote", "rank", "merge"],
            },
            "partial_failure_policy": {
                "type": "string",
                "enum": ["fail", "skip", "retry"],
            },
        },
    },
    example_parameters={
        "fanout_count": 10,
        "fanout_strategy": "static",
        "subagent_role": "test-expert",
        "subagent_isolation": "worktree",
        "barrier_timeout_seconds": 3600,
        "aggregator_role": "architect",
        "aggregation_strategy": "merge",
        "partial_failure_policy": "skip",
    },
    failure_modes=[
        FailureMode(
            name="屏障超时",
            trigger="部分子任务死锁或处理过慢",
            mitigation="barrier_timeout_seconds 硬超时 + partial_failure_policy 兜底",
        ),
        FailureMode(
            name="资源耗尽",
            trigger="fanout_count 过大（本机资源不足）",
            mitigation="Phase 0 硬上限 10 + 资源监控（Phase 2 引入 WorktreeManager）",
        ),
        FailureMode(
            name="聚合冲突",
            trigger="子结果 schema 不一致",
            mitigation="聚合前 schema 校验，不通过则丢弃",
        ),
        FailureMode(
            name="subagent 崩溃污染",
            trigger="异常隔离不完整",
            mitigation="worktree 隔离 + finally 块清理（Phase 2 实施）",
        ),
    ],
    success_criteria=[
        "覆盖率 100%（无 Agentic laziness）",
        "扇出 + 聚合总耗时 < 顺序执行的 50%",
        "聚合结果 schema 100% 一致",
    ],
    not_applicable_scenarios=[
        "子任务数 < 3（扇出开销大于收益）",
        "子任务间强依赖（必须等前一个完成）",
        "目标环境非 Git 仓库（worktree 隔离不可用）",
    ],
    selector=_select_fan_out_aggregate,
)


# 模式 3：adversarial-verify（对抗性验证）
PATTERN_ADVERSARIAL_VERIFY = WorkflowPattern(
    pattern_id="adversarial-verify",
    name="对抗性验证",
    version="1.0",
    description=(
        "生成者产出 → 独立 context 验证者对照评估准则验证。"
        "解决 self-preferential bias 痛点：模型验证自己产出时倾向于放行。"
    ),
    isolation_requirement=IsolationLevel.CONTEXT,
    default_token_budget=8000,
    applicable_roles=["architect", "test-expert", "solo-coder"],
    priority=30,
    parameters_schema={
        "type": "object",
        "required": [
            "generator_role",
            "verifier_role",
            "verifier_isolation",
            "evaluation_criteria",
        ],
        "properties": {
            "generator_role": {"type": "string"},
            "verifier_role": {"type": "string"},
            "verifier_isolation": {
                "type": "string",
                "enum": ["context", "full"],
                "description": "至少 context 隔离；高风险必须 full",
            },
            "evaluation_criteria": {
                "type": "array",
                "minItems": 3,
                "items": {"type": "string"},
            },
            "verification_depth": {
                "type": "string",
                "enum": ["shallow", "deep"],
            },
            "max_rounds": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
            },
            "pass_threshold": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
            },
            "fallback_on_reject": {
                "type": "string",
                "enum": ["regenerate", "human_review", "abort"],
            },
        },
    },
    example_parameters={
        "generator_role": "architect",
        "verifier_role": "test-expert",
        "verifier_isolation": "full",
        "evaluation_criteria": [
            "满足性能需求（P99 < 200ms）",
            "无单点故障",
            "符合现有代码规范",
            "通过 OWASP Top 10 安全检查",
        ],
        "verification_depth": "deep",
        "max_rounds": 3,
        "pass_threshold": 0.8,
        "fallback_on_reject": "regenerate",
    },
    failure_modes=[
        FailureMode(
            name="验证者与生成者共享偏见",
            trigger="verifier_isolation 失效（共享 context）",
            mitigation=(
                "🔴 强约束：PatternLibrary 启动前校验 isolation 字段，"
                "强制 context 隔离"
            ),
        ),
        FailureMode(
            name="评估准则不明确",
            trigger="evaluation_criteria 是模糊描述",
            mitigation="schema 校验：每条 criteria 必须含可测量指标",
        ),
        FailureMode(
            name="对抗无限循环",
            trigger="生成者和验证者不断找理由",
            mitigation="max_rounds 硬上限 3-5",
        ),
        FailureMode(
            name="验证者过度严苛",
            trigger="通过率 < 10%（验证标准脱离实际）",
            mitigation="pass_threshold 动态调整（基于历史 50 次执行 P50）",
        ),
    ],
    success_criteria=[
        "通过率（人工确认）≥ 80%",
        "平均对抗轮次 ≤ 2",
        "验证发现的关键缺陷数 ≥ 顺序评审的 1.5x",
    ],
    not_applicable_scenarios=[
        "主观性强的任务（设计审美）",
        "没有评估准则的任务",
        "简单任务（如修复 typo，引入对抗成本不划算）",
        "验证者与生成者能力差距过大",
    ],
    selector=_select_adversarial_verify,
)


# ============================================================================
# 模式 4：generate-filter（生成与筛选）
# ============================================================================

PATTERN_GENERATE_FILTER = WorkflowPattern(
    pattern_id="generate-filter",
    name="生成与筛选",
    version="1.0",
    description=(
        "大量生成候选 → 评估筛选 → 去重 → 仅返回通过项。"
        "通过'概率质量'（多生成后筛选）抵消单次生成的不稳定性。"
    ),
    isolation_requirement=IsolationLevel.NONE,
    default_token_budget=10000,
    applicable_roles=["product-manager", "solo-coder", "architect"],
    priority=40,
    parameters_schema={
        "type": "object",
        "required": ["generator_role", "generator_count", "filter_criteria", "dedup_strategy"],
        "properties": {
            "generator_role": {"type": "string"},
            "generator_count": {
                "type": "integer",
                "minimum": 3,
                "maximum": 20,
                "description": "生成候选数（3-20，硬上限 20）",
            },
            "filter_criteria": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string"},
                "description": "筛选标准（必须含可量化指标）",
            },
            "dedup_strategy": {
                "type": "string",
                "enum": ["exact", "fuzzy", "semantic"],
            },
            "dedup_threshold": {
                "type": "number",
                "default": 0.85,
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "模糊/语义去重阈值（仅 fuzzy/semantic 生效）",
            },
            "output_top_n": {
                "type": "integer",
                "default": 3,
                "minimum": 1,
                "description": "返回通过项的前 N 个",
            },
            "quality_floor": {
                "type": "number",
                "default": 0.6,
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "低于此分数丢弃",
            },
            "embedder": {
                "type": "object",
                "description": (
                    "Phase 6 新增：embedder 配置。"
                    "dedup_strategy=semantic 时生效。"
                    "type=auto 时优先 SentenceTransformer，未安装则 fallback 到 TFIDF。"
                ),
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["auto", "tfidf", "hashing", "sentence_transformer"],
                        "default": "auto",
                    },
                    "model_name": {
                        "type": "string",
                        "default": "all-MiniLM-L6-v2",
                        "description": "sentence-transformer 模型名（仅 sentence_transformer 生效）",
                    },
                    "n_features": {
                        "type": "integer",
                        "default": 1024,
                        "description": "hashing embedder 桶数量",
                    },
                    "max_features": {
                        "type": "integer",
                        "default": 5000,
                        "description": "TFIDF embedder 最大特征数",
                    },
                },
            },
        },
    },
    example_parameters={
        "generator_role": "product-manager",
        "generator_count": 8,
        "filter_criteria": [
            "简洁（<= 4 字）",
            "易记（无生僻字）",
            "与品牌调性一致",
        ],
        "dedup_strategy": "fuzzy",
        "dedup_threshold": 0.85,
        "output_top_n": 3,
        "quality_floor": 0.6,
    },
    failure_modes=[
        FailureMode(
            name="生成候选全失败",
            trigger="generator_role 配置错误或 dispatch 不可用",
            mitigation="fallback_to_sequential + 异常隔离单候选",
        ),
        FailureMode(
            name="筛选标准不可量化",
            trigger="filter_criteria 含模糊描述",
            mitigation="schema 校验要求每条 criteria 含可测量指标",
        ),
        FailureMode(
            name="去重过激",
            trigger="dedup_threshold 过高导致全部命中同一类",
            mitigation="动态阈值（基于生成结果分布）",
        ),
        FailureMode(
            name="输出不足",
            trigger="quality_floor 过高或 filter 过严",
            mitigation="返回所有通过 quality_floor 的候选 + 警告",
        ),
        FailureMode(
            name="embedder 不可用",
            trigger="dedup_strategy=semantic 时未安装 sentence-transformers",
            mitigation="graceful fallback 到 TFIDFEmbedder（无外部依赖）",
        ),
    ],
    success_criteria=[
        "至少返回 1 个通过筛选的候选",
        "通过率（人工确认）≥ 70%",
        "去重后候选数 ≥ 1",
    ],
    not_applicable_scenarios=[
        "候选不能重复（每生成都贵）",
        "评估标准主观（筛选结果不稳定）",
        "候选数 < 3（单次生成即可）",
        "高风险任务（应优先 adversarial-verify）",
    ],
    selector=_select_generate_filter,
)


# ============================================================================
# 模式 5：tournament（锦标赛模式）
# ============================================================================

PATTERN_TOURNAMENT = WorkflowPattern(
    pattern_id="tournament",
    name="锦标赛模式",
    version="1.0",
    description=(
        "N 个候选方案 → 两两 PK → 逐步淘汰 → 决出冠军。"
        "两两对比比绝对打分更可靠，参考信息熵更高。"
    ),
    isolation_requirement=IsolationLevel.CONTEXT,
    default_token_budget=15000,
    applicable_roles=["architect", "test-expert", "product-manager"],
    priority=50,
    parameters_schema={
        "type": "object",
        "required": [
            "candidate_count",
            "candidate_generator",
            "judge_role",
            "ranking_method",
        ],
        "properties": {
            "candidate_count": {
                "type": "integer",
                "minimum": 3,
                "maximum": 8,
                "description": "候选数（3-8 硬上限，> 8 成本爆炸）",
            },
            "candidate_generator": {
                "type": "string",
                "description": "候选生成器角色",
            },
            "judge_role": {
                "type": "string",
                "description": "裁判角色",
            },
            "ranking_method": {
                "type": "string",
                "enum": ["knockout", "round-robin", "elo"],
                "description": "排名方法：淘汰赛 / 循环赛 / ELO 评分",
            },
            "judge_criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": "裁判标准（缺省沿用 evaluation_criteria）",
            },
            "judge_context_isolation": {
                "type": "boolean",
                "default": True,
                "description": "裁判是否必须独立 context（防 self-bias）",
            },
        },
    },
    example_parameters={
        "candidate_count": 4,
        "candidate_generator": "architect",
        "judge_role": "test-expert",
        "ranking_method": "knockout",
        "judge_criteria": [
            "性能（P99 < 200ms）",
            "可维护性（耦合度低）",
            "可扩展性（支持未来 10x 流量）",
        ],
        "judge_context_isolation": True,
    },
    failure_modes=[
        FailureMode(
            name="候选数非 2 的幂",
            trigger="knockout 模式下候选数不为 2^n",
            mitigation="自动补齐 bye（轮空）位",
        ),
        FailureMode(
            name="裁判不公",
            trigger="judge 与候选生成器共享偏见",
            mitigation="judge_context_isolation=True 强约束",
        ),
        FailureMode(
            name="PK 死循环",
            trigger="knockout 平局无法分出胜负",
            mitigation="平局策略：随机晋级 / 重新 PK（最多 3 次）",
        ),
        FailureMode(
            name="cost 爆炸",
            trigger="candidate_count 接近 8 + round-robin",
            mitigation="硬上限 8，超过则降级 knockout",
        ),
    ],
    success_criteria=[
        "成功决出唯一冠军",
        "每场 PK 都有明确裁判结果",
        "总 PK 次数 < candidate_count * 2",
    ],
    not_applicable_scenarios=[
        "候选无对比性（完全不同的产物）",
        "评估需要全局视角（PK 信息不足）",
        "候选数 < 3（顺序打分即可）",
        "候选数 > 8（成本爆炸）",
    ],
    selector=_select_tournament,
)


# ============================================================================
# 模式 6：loop-until-done（循环直到完成）
# ============================================================================

PATTERN_LOOP_UNTIL_DONE = WorkflowPattern(
    pattern_id="loop-until-done",
    name="循环直到完成",
    version="1.0",
    description=(
        "动态生成 subagent → 直至满足停止条件。"
        "用停止条件替代固定次数上限，缓解 goal drift 痛点。"
    ),
    isolation_requirement=IsolationLevel.NONE,
    default_token_budget=20000,
    applicable_roles=["architect", "test-expert", "solo-coder"],
    priority=60,
    parameters_schema={
        "type": "object",
        "required": ["max_iterations", "stop_conditions", "iteration_executor"],
        "properties": {
            "max_iterations": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "硬上限（避免死循环）",
            },
            "stop_conditions": {
                "type": "object",
                "properties": {
                    "no_new_findings": {"type": "boolean"},
                    "no_error_logs": {"type": "boolean"},
                    "quality_threshold_met": {"type": "boolean"},
                    "convergence_detected": {"type": "boolean"},
                },
                "description": (
                    "停止条件：满足任一即停止（OR 关系）；"
                    "quality_threshold_met 需要 quality_threshold 字段"
                ),
            },
            "iteration_executor": {
                "type": "string",
                "description": "每轮执行器角色",
            },
            "state_persistence": {
                "type": "string",
                "enum": ["memory", "checkpoint"],
                "default": "memory",
                "description": "跨迭代状态：内存 / 持久化（Phase 5+ 引入 CheckpointManager）",
            },
            "quality_threshold": {
                "type": "number",
                "default": 0.85,
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "质量阈值（仅 quality_threshold_met 生效）",
            },
        },
    },
    example_parameters={
        "max_iterations": 10,
        "stop_conditions": {
            "no_new_findings": True,
            "no_error_logs": True,
            "quality_threshold_met": True,
            "convergence_detected": True,
        },
        "iteration_executor": "architect",
        "state_persistence": "memory",
        "quality_threshold": 0.85,
    },
    failure_modes=[
        FailureMode(
            name="死循环",
            trigger="停止条件永远不满足",
            mitigation="max_iterations 硬上限 + 超限报警",
        ),
        FailureMode(
            name="状态丢失",
            trigger="state_persistence=memory 跨调用丢失",
            mitigation="Phase 5+ 引入 CheckpointManager 持久化",
        ),
        FailureMode(
            name="每轮上下文膨胀",
            trigger="迭代结果累积到下一次 prompt",
            mitigation="上下文截断（仅保留最近 N 轮结果）",
        ),
        FailureMode(
            name="成本失控",
            trigger="max_iterations 过大 + 单轮成本高",
            mitigation="max_iterations 硬上限 50",
        ),
    ],
    success_criteria=[
        "在 max_iterations 内停止（不超限）",
        "至少满足 1 个停止条件后停止",
        "返回最后一轮的执行结果",
    ],
    not_applicable_scenarios=[
        "工作量已知（顺序即可）",
        "无清晰停止条件（容易死循环）",
        "单次执行可完成（无需循环）",
        "大量同质子任务（应改用 fan-out-aggregate）",
    ],
    selector=_select_loop_until_done,
)


# ============================================================================
# 模式库（PatternLibrary）
# ============================================================================

# Phase 5 扩展：6 大模式全部沉淀（Phase 0 仅 3 个核心模式）
ALL_PATTERNS: List[WorkflowPattern] = [
    PATTERN_CLASSIFIER_DISPATCH,
    PATTERN_FAN_OUT_AGGREGATE,
    PATTERN_ADVERSARIAL_VERIFY,
    PATTERN_GENERATE_FILTER,
    PATTERN_TOURNAMENT,
    PATTERN_LOOP_UNTIL_DONE,
]

# 兼容旧名（Phase 0 代码可能仍引用 PHASE0_PATTERNS）
PHASE0_PATTERNS: List[WorkflowPattern] = [
    PATTERN_CLASSIFIER_DISPATCH,
    PATTERN_FAN_OUT_AGGREGATE,
    PATTERN_ADVERSARIAL_VERIFY,
]


class PatternLibrary:
    """
    模式库：管理 6 大模式（Phase 5 后）的加载、校验、查询

    关键约束（来自 DYNAMIC_WORKFLOWS_INTEGRATION.md §3.0.1）：
    - 模式定义不在本类内持久化（持久化复用 PerformanceFingerprint）
    - 模式定义仅在内存中持有（Phase 0/5 简化）
    - 加载时严格 schema 校验（防止损坏的模式定义被使用）

    关键约束（§3.0.3 安全）：
    - 模式库加载时校验每个 WorkflowPattern.validate() 必须通过
    - 校验失败时抛出 ValueError，不允许降级使用损坏模式
    """

    def __init__(
        self,
        patterns: Optional[List[WorkflowPattern]] = None,
        use_all_patterns: bool = True,
    ):
        """
        初始化模式库

        Args:
            patterns: 自定义模式列表（默认 None → 根据 use_all_patterns 选择）
            use_all_patterns: 当 patterns 为 None 时，是否使用全部 6 大模式（Phase 5+ 默认 True）

        Raises:
            ValueError: 当任一模式未通过 schema 校验时
        """
        self._patterns: Dict[str, WorkflowPattern] = {}

        # 加载模式（带 schema 校验）
        if patterns is not None:
            patterns_to_load = patterns
        else:
            # Phase 5: 默认加载全部 6 大模式
            patterns_to_load = ALL_PATTERNS if use_all_patterns else PHASE0_PATTERNS

        for pattern in patterns_to_load:
            errors = pattern.validate()
            if errors:
                # 🔴 强约束：校验失败直接抛错，不允许降级
                error_msg = "; ".join(errors)
                raise ValueError(
                    f"模式 '{pattern.pattern_id}' schema 校验失败：{error_msg}"
                )
            self._patterns[pattern.pattern_id] = pattern

        logger.info(
            f"PatternLibrary 加载完成：{len(self._patterns)} 个模式 "
            f"({', '.join(self._patterns.keys())})"
        )

    def get(self, pattern_id: str) -> Optional[WorkflowPattern]:
        """
        根据 ID 获取模式

        Args:
            pattern_id: 模式 ID

        Returns:
            WorkflowPattern 或 None（不存在时）
        """
        return self._patterns.get(pattern_id)

    def list_ids(self) -> List[str]:
        """列出所有已加载模式 ID"""
        return list(self._patterns.keys())

    def list_all(self) -> List[WorkflowPattern]:
        """列出所有已加载模式（按 priority 升序）"""
        return sorted(self._patterns.values(), key=lambda p: p.priority)

    def size(self) -> int:
        """返回已加载模式数"""
        return len(self._patterns)

    def __len__(self) -> int:
        """支持 len(library) 调用（Phase 5 扩展）"""
        return len(self._patterns)


# ============================================================================
# 模式选择器（PatternComposer）
# ============================================================================

class PatternComposer:
    """
    模式选择器：基于任务特征选择最合适的模式

    选择流程（对齐 PATTERNS_REFERENCE.md §3 决策树）：
    1. 遍历所有模式，按 priority 升序调用其 selector
    2. 第一个 applicable=True 的模式胜出
    3. 都不适用时返回 PatternSelection(pattern_id=None, applicable=False)
    4. 通过 PerformanceFingerprint 反哺历史选择效果
    5. 性能目标：< 100ms / 次

    关键约束（来自 DYNAMIC_WORKFLOWS_INTEGRATION.md §3.0）：
    - 默认顺序：pattern_id=None 表示不需要模式（顺序执行即可）
    - 持久化复用：通过 PerformanceFingerprint 记录历史选择
    - 安全：所有 mode 选择都返回可解释的 rationale
    """

    # 性能基线目标
    PERFORMANCE_BUDGET_MS = 100.0

    def __init__(
        self,
        library: Optional[PatternLibrary] = None,
        fingerprint: Optional[PerformanceFingerprint] = None,
    ):
        """
        初始化模式选择器

        Args:
            library: 模式库（默认使用 PHASE0_PATTERNS）
            fingerprint: 性能画像（默认创建独立 agent_id='pattern_composer'）
        """
        self.library = library or PatternLibrary()
        self.fingerprint = fingerprint or PerformanceFingerprint(
            agent_id="pattern_composer"
        )

    def select(
        self,
        task: TaskFeature,
        enable_history_lookup: bool = True,
    ) -> PatternSelection:
        """
        基于任务特征选择最合适的模式

        Args:
            task: 任务特征
            enable_history_lookup: 是否查询历史相似案例（Phase 0 简化：仅记录，不参与决策）

        Returns:
            PatternSelection: 模式选择结果
        """
        start_time = time.perf_counter()

        # 阶段 1：按 priority 顺序遍历所有模式
        candidate_results: List[Tuple[WorkflowPattern, bool, float, str]] = []

        for pattern in self.library.list_all():
            if pattern.selector is None:
                # 无 selector 的模式不参与自动选择（保留扩展能力）
                continue

            applicable, confidence, rationale = pattern.selector(task)
            candidate_results.append((pattern, applicable, confidence, rationale))

        # 阶段 2：选择第一个 applicable 的模式（按 priority 顺序）
        selected: Optional[WorkflowPattern] = None
        selected_confidence: float = 0.0
        selected_rationale: str = ""

        for pattern, applicable, confidence, rationale in candidate_results:
            if applicable:
                selected = pattern
                selected_confidence = confidence
                selected_rationale = rationale
                break

        # 阶段 3：构建选择结果
        if selected is None:
            # 没有任何模式适用 → 回退到顺序执行
            all_reasons = "; ".join(
                f"[{p.pattern_id}]{r}" for p, _, _, r in candidate_results
            )
            selection = PatternSelection(
                pattern_id=None,
                applicable=False,
                confidence=0.0,
                rationale=(
                    f"所有 {len(self.library)} 个模式均不适用，"
                    f"回退到顺序执行（fallback_pattern=sequential）。"
                ),
                parameters={},
                estimated_token_budget=2000,  # 顺序执行默认预算
                fallback_pattern="sequential",
                rejection_reason=all_reasons,
            )
        else:
            # 模式适用 → 构建完整选择结果
            selection = PatternSelection(
                pattern_id=selected.pattern_id,
                applicable=True,
                confidence=selected_confidence,
                rationale=selected_rationale,
                parameters=dict(selected.example_parameters),  # 复制避免引用
                estimated_token_budget=selected.default_token_budget,
                fallback_pattern="sequential",
            )

        # 阶段 4：性能基线检查（架构师审查要求 < 100ms）
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        if elapsed_ms > self.PERFORMANCE_BUDGET_MS:
            logger.warning(
                f"模式选择耗时 {elapsed_ms:.2f}ms 超出预算 {self.PERFORMANCE_BUDGET_MS}ms "
                f"（task_type={task.task_type}）"
            )
        else:
            logger.debug(
                f"模式选择耗时 {elapsed_ms:.2f}ms "
                f"（task_type={task.task_type}, pattern_id={selection.pattern_id}）"
            )

        # 阶段 5：画像反哺（复用 PerformanceFingerprint）
        # 记录本次"选择"事件，供后续 Phase 0+ 做画像反哺决策
        # 注意：此处仅记录选择行为，不记录"执行结果"
        # 真正的执行结果反哺需在 PatternExecutor（Phase 1+）中调用 record_outcome
        if enable_history_lookup and self.fingerprint is not None:
            try:
                # 选择本身不是任务执行，但可作为"决策记录"存储
                # 留作 Phase 0 简化版的画像反哺入口
                pass  # Phase 0 暂不存储选择事件，避免污染 ExecutionRecord
            except Exception as e:  # noqa: BLE001
                # 画像反哺失败不影响主流程
                logger.warning(f"画像反哺失败（非致命）: {e}")

        return selection

    def record_outcome(
        self,
        task: TaskFeature,
        selection: PatternSelection,
        success: bool,
        execution_time_seconds: float = 0.0,
        error_type: Optional[str] = None,
    ) -> None:
        """
        记录模式执行结果到画像

        这是"模式选择 → 执行 → 画像反哺"闭环的关键接口。
        Phase 0 仅暴露接口，具体记录策略由调用方控制。

        Args:
            task: 任务特征
            selection: 模式选择结果
            success: 执行是否成功
            execution_time_seconds: 执行耗时（秒）
            error_type: 错误类型（失败时）
        """
        if self.fingerprint is None:
            logger.warning("未配置 PerformanceFingerprint，跳过结果记录")
            return

        # 将模式选择作为 strategy 字段存储到画像
        # PerformanceFingerprint 已有 strategy 字段，可直接复用
        try:
            self.fingerprint.record(
                task_type=task.task_type,
                task_complexity=task.task_complexity,
                success=success,
                error_type=error_type,
                execution_time=execution_time_seconds,
                strategy=selection.pattern_id or "sequential",
                context_features=task.to_dict(),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"记录执行结果到画像失败（非致命）: {e}")


# ============================================================================
# 便捷函数
# ============================================================================

def create_default_composer() -> PatternComposer:
    """
    创建默认配置的模式选择器

    这是给上层调用方（产品经理/独立开发者）的一键入口。
    """
    return PatternComposer()


def select_pattern_for_task(task: TaskFeature) -> PatternSelection:
    """
    一键模式选择：给定任务特征，返回选择结果

    这是最简化的对外接口。
    """
    composer = create_default_composer()
    return composer.select(task)


# ============================================================================
# CLI 入口（供命令行测试与人工 review）
# ============================================================================

def _cli_main() -> int:
    """
    CLI 入口：接受 task_type / subtask_count / risk_level 等参数，
    输出 PatternSelection JSON。
    """
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Dynamic Workflows 模式选择器（Phase 0 演示）"
    )
    parser.add_argument("--task-type", default="general", help="任务类型")
    parser.add_argument("--task-complexity", type=int, default=5, help="任务复杂度 1-10")
    parser.add_argument("--type-variants", type=int, default=1, help="任务类型变体数")
    parser.add_argument("--subtask-count", type=int, default=1, help="子任务数")
    parser.add_argument("--risk-level", default="low", choices=["low", "medium", "high", "critical"])
    parser.add_argument(
        "--has-criteria", action="store_true", help="是否有评估准则"
    )
    parser.add_argument(
        "--criteria-measurable", action="store_true", help="准则可测量"
    )
    parser.add_argument(
        "--target-is-git", action="store_true", default=True, help="目标环境是 Git 仓库"
    )
    parser.add_argument(
        "--description", default="", help="任务描述"
    )

    args = parser.parse_args()

    task = TaskFeature(
        type_variants=args.type_variants,
        subtask_count=args.subtask_count,
        subtask_homogeneous=True,
        subtask_independent=True,
        risk_level=RiskLevel(args.risk_level),
        has_evaluation_criteria=args.has_criteria,
        criteria_measurable=args.criteria_measurable,
        target_is_git=args.target_is_git,
        task_description=args.description,
        task_type=args.task_type,
        task_complexity=args.task_complexity,
    )

    composer = create_default_composer()
    selection = composer.select(task)
    print(json.dumps(selection.to_dict(), ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(_cli_main())
