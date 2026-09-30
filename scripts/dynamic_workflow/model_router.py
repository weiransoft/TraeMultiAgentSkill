#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dynamic Workflows 模型路由器（Model Router）

Phase 3 实现：基于 subagent 能力 / 成本的任务路由

核心职责：
1. 定义 3 个模型层级（haiku / sonnet / opus）的画像（成本 / 质量 / 速度）
2. 根据任务特征（复杂度 / 角色 / Token 预算 / 截止时间）选择最合适的模型
3. 路由决策可解释（返回中文 reasoning 字段）
4. 路由历史写入 PerformanceFingerprint（反哺 + 审计）
5. 冷启动降级：无历史数据时使用静态决策表

设计约束（来自 DYNAMIC_WORKFLOWS_INTEGRATION.md §3.0）：
- 🔴 持久化复用：路由决策历史写入 PerformanceFingerprint.execution_record
- 🔴 V2 不修改：本模块独立运行，不触碰 V2 引擎
- 🔴 安全：任务特征 schema 校验；不允许任务描述直接决定模型
- 🔴 一阶段一模块：仅模型路由，不引入 Token 预算（独立模块 TokenBudgetGuard）

参考来源：
- [DYNAMIC_WORKFLOWS_INTEGRATION.md v1.1 §模块 4]
- [Anthropic Dynamic Workflows - 模型路由]
- [PHASE3_PLAN.md]

作者：trae-multi-agent 融合 Phase 3
创建日期：2026-06-03
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

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

# Phase 10：仅类型注解导入 PatternTierResolver，避免循环导入
if TYPE_CHECKING:
    from pattern_tier_resolver import PatternTierResolver, TierResolution


# ============================================================================
# 日志配置
# ============================================================================

logger = logging.getLogger("dynamic_workflow.model_router")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ============================================================================
# 异常定义
# ============================================================================

class ModelRouterError(Exception):
    """ModelRouter 异常基类"""


class InvalidTaskFeatureError(ModelRouterError):
    """任务特征非法（schema 校验失败）"""


class ModelTierNotFoundError(ModelRouterError):
    """模型层级未在画像中定义"""


# ============================================================================
# 枚举定义
# ============================================================================

class ModelTier(str, Enum):
    """
    模型层级枚举

    对齐 Anthropic 模型家族：
    - HAIKU  ：轻量级，低成本低延迟，适合简单任务
    - SONNET ：标准级，平衡成本与质量，适合中等任务
    - OPUS   ：重量级，高成本高质量，适合复杂关键任务
    """
    HAIKU  = "haiku"
    SONNET = "sonnet"
    OPUS   = "opus"

    @classmethod
    def from_str(cls, value: str) -> "ModelTier":
        """从字符串解析（大小写不敏感）；找不到则抛 ModelTierNotFoundError"""
        normalized = value.lower().strip()
        for tier in cls:
            if tier.value == normalized:
                return tier
        raise ModelTierNotFoundError(
            f"未知的模型层级：{value}（有效值：haiku / sonnet / opus）"
        )


# ============================================================================
# 数据类定义
# ============================================================================

@dataclass
class ModelProfile:
    """
    模型画像

    描述一个模型层级的关键属性，供路由决策使用。
    """
    tier: ModelTier
    cost_per_1k_tokens: float  # 每 1k token 相对成本（haiku=0.25, sonnet=1.0, opus=5.0）
    quality_score: float       # 质量分 (0-1)
    speed_score: float         # 速度分 (0-1, 越大越快)
    max_context_tokens: int    # 最大上下文 token 数
    description: str           # 适用场景描述（中文）

    def __post_init__(self):
        """字段合法性校验（构造时立即检查）"""
        if self.cost_per_1k_tokens < 0:
            raise ModelRouterError(
                f"cost_per_1k_tokens 不能为负：{self.cost_per_1k_tokens}"
            )
        if not (0.0 <= self.quality_score <= 1.0):
            raise ModelRouterError(
                f"quality_score 必须在 [0, 1] 范围内：{self.quality_score}"
            )
        if not (0.0 <= self.speed_score <= 1.0):
            raise ModelRouterError(
                f"speed_score 必须在 [0, 1] 范围内：{self.speed_score}"
            )
        if self.max_context_tokens <= 0:
            raise ModelRouterError(
                f"max_context_tokens 必须为正整数：{self.max_context_tokens}"
            )


@dataclass
class TaskFeature:
    """
    任务特征（路由决策输入）

    描述一次任务执行的关键特征，ModelRouter 据此选择模型。

    Phase 10 新增字段：
    - pattern_id: 当前任务所属模式（用于 PatternTierResolver 决策）
    - extra: 扩展字段字典（透传 subtask_count / is_final_iteration / risk_level / type_variants 等模式特定信息）
    """
    # 必填字段
    task_complexity: int                   # 任务复杂度 1-10
    estimated_tokens: int                  # 预计 token 消耗（含 input + output）

    # 可选字段
    role: Optional[str] = None             # 角色标识（架构师/产品/solo-coder/test-expert/...）
    deadline_ms: Optional[int] = None      # 截止时间（毫秒），None 表示无截止
    quality_threshold: float = 0.85        # 质量阈值（任务最低可接受质量）
    budget_remaining: float = 1.0          # 预算剩余比例 (0-1)，1.0 表示充足
    is_critical: bool = False              # 是否关键任务（关键任务强制 opus）
    task_type: str = "general"             # 任务类型（用于画像检索）

    # Phase 10 新增
    pattern_id: Optional[str] = None       # 当前任务所属模式（None 时不触发 PatternTierResolver）
    extra: Dict[str, Any] = field(default_factory=dict)  # 模式特定扩展字段

    def __post_init__(self):
        """字段合法性校验"""
        if not (1 <= self.task_complexity <= 10):
            raise InvalidTaskFeatureError(
                f"task_complexity 必须在 [1, 10] 范围内：{self.task_complexity}"
            )
        if self.estimated_tokens <= 0:
            raise InvalidTaskFeatureError(
                f"estimated_tokens 必须为正整数：{self.estimated_tokens}"
            )
        if not (0.0 <= self.quality_threshold <= 1.0):
            raise InvalidTaskFeatureError(
                f"quality_threshold 必须在 [0, 1] 范围内：{self.quality_threshold}"
            )
        if not (0.0 <= self.budget_remaining <= 1.0):
            raise InvalidTaskFeatureError(
                f"budget_remaining 必须在 [0, 1] 范围内：{self.budget_remaining}"
            )
        if self.deadline_ms is not None and self.deadline_ms <= 0:
            raise InvalidTaskFeatureError(
                f"deadline_ms 为正整数或 None：{self.deadline_ms}"
            )
        # Phase 10：pattern_id 校验（kebab-case 或 None）
        if self.pattern_id is not None and not isinstance(self.pattern_id, str):
            raise InvalidTaskFeatureError(
                f"pattern_id 必须是 str 或 None：{type(self.pattern_id).__name__}"
            )
        # Phase 10：extra 校验（必须是 dict）
        if not isinstance(self.extra, dict):
            raise InvalidTaskFeatureError(
                f"extra 必须是 dict，实际为 {type(self.extra).__name__}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """
        转字典（用于画像反哺）

        Phase 10 修复（架构师审查 2.10）：
        显式排除 `pattern_id`，保持 fingerprint schema 向后兼容。
        `extra` 字段保留（用于模式特定特征反哺）。
        """
        d = asdict(self)
        d.pop("pattern_id", None)  # 保持 fingerprint schema 兼容
        return d


@dataclass
class RoutingDecision:
    """
    路由决策（带可解释性）

    包含最终选择的模型 + 决策理由 + 备选方案 + 决策时的特征快照。
    """
    selected_tier: ModelTier
    confidence: float                                  # 决策置信度 (0-1)
    reasoning: str                                     # 决策理由（中文，人类可读）
    alternatives: List[ModelTier] = field(default_factory=list)  # 备选方案
    feature_snapshot: Dict[str, Any] = field(default_factory=dict)  # 决策时的特征快照
    decision_source: str = "static_rule"                # 决策来源：static_rule / fingerprint_history
    decision_time_ms: float = 0.0                       # 决策耗时（毫秒）

    def __post_init__(self):
        """字段合法性校验"""
        if not (0.0 <= self.confidence <= 1.0):
            raise ModelRouterError(
                f"confidence 必须在 [0, 1] 范围内：{self.confidence}"
            )
        if not self.reasoning:
            raise ModelRouterError("reasoning 不能为空")
        if self.selected_tier not in self.alternatives and self.alternatives:
            # alternatives 不为空时，selected_tier 应在 alternatives 中
            # （但允许 alternatives 为空以兼容极简场景）
            pass  # 软约束，不抛异常

    def to_dict(self) -> Dict[str, Any]:
        """转字典（用于画像反哺）"""
        return {
            "selected_tier": self.selected_tier.value,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "alternatives": [t.value for t in self.alternatives],
            "feature_snapshot": self.feature_snapshot,
            "decision_source": self.decision_source,
            "decision_time_ms": self.decision_time_ms,
        }


# ============================================================================
# 默认模型画像（出厂设置）
# ============================================================================

# 三个模型层级的默认画像（与 Anthropic Claude 家族对齐）
# cost_per_1k_tokens: 相对值（sonnet=1.0 为基准）
# quality_score / speed_score: 0-1 经验值
DEFAULT_PROFILES: Dict[ModelTier, ModelProfile] = {
    ModelTier.HAIKU: ModelProfile(
        tier=ModelTier.HAIKU,
        cost_per_1k_tokens=0.25,
        quality_score=0.70,
        speed_score=1.0,
        max_context_tokens=200_000,
        description="轻量级模型，适合简单任务：分类、提取、格式化、单元测试",
    ),
    ModelTier.SONNET: ModelProfile(
        tier=ModelTier.SONNET,
        cost_per_1k_tokens=1.0,
        quality_score=0.85,
        speed_score=0.6,
        max_context_tokens=200_000,
        description="标准级模型，适合中等任务：代码实现、文档撰写、API 设计",
    ),
    ModelTier.OPUS: ModelProfile(
        tier=ModelTier.OPUS,
        cost_per_1k_tokens=5.0,
        quality_score=0.95,
        speed_score=0.3,
        max_context_tokens=200_000,
        description="重量级模型，适合复杂关键任务：架构设计、深度分析、关键审查",
    ),
}


# ============================================================================
# 决策阈值常量
# ============================================================================

# 关键任务复杂度阈值（>= 7 视为高复杂度）
HIGH_COMPLEXITY_THRESHOLD = 7

# 预算耗尽阈值（< 10% 强制 haiku）
BUDGET_EXHAUSTED_THRESHOLD = 0.1

# 截止时间紧阈值（< 5s）
TIGHT_DEADLINE_MS = 5_000

# 画像检索最小样本数（低于此数走静态规则）
MIN_FINGERPRINT_SAMPLES = 10

# 画像历史权重（用于加权决策）
FINGERPRINT_HISTORY_WEIGHT = 0.6
STATIC_RULE_WEIGHT = 0.4


# ============================================================================
# ModelRouter 主类
# ============================================================================

class ModelRouter:
    """
    模型路由器

    根据任务特征选择最合适的模型层级，并提供决策可解释性 + 画像反哺。

    Phase 10 增强：
    - 接受可选的 tier_resolver（PatternTierResolver 实例）
    - 当 resolver 存在且 feature.pattern_id 命中策略时，优先使用 resolver 决策
    - 决策链：explicit_tier > critical_path > pattern_policy > static_rule > history

    使用方式：
    ```python
    router = ModelRouter(fingerprint=PerformanceFingerprint(agent_id="..."))
    decision = router.route(TaskFeature(
        task_complexity=8,
        estimated_tokens=50000,
        role="architect",
        budget_remaining=0.5,
    ))
    print(decision.selected_tier, decision.reasoning)
    # -> opus "高复杂度任务，opus 必需"

    # 执行后反哺
    router.record_decision(decision, actual_outcome={"success": True, "quality": 0.92})
    ```

    Phase 10 进阶：
    ```python
    from pattern_tier_resolver import create_default_resolver

    router = ModelRouter(
        fingerprint=PerformanceFingerprint(agent_id="..."),
        tier_resolver=create_default_resolver(),
    )
    decision = router.route(TaskFeature(
        task_complexity=5,
        estimated_tokens=1000,
        pattern_id="adversarial-verify",  # 触发 pattern_policy
    ))
    # -> opus "模式 adversarial-verify 默认策略，使用 opus"

    # 显式覆盖
    decision = router.route(
        TaskFeature(task_complexity=5, estimated_tokens=1000, pattern_id="adversarial-verify"),
        explicit_tier=ModelTier.HAIKU,  # 强制 haiku
    )
    # -> haiku "显式声明 model_tier=haiku，强制覆盖"
    ```
    """

    def __init__(
        self,
        fingerprint: Optional[PerformanceFingerprint] = None,
        custom_profiles: Optional[Dict[ModelTier, ModelProfile]] = None,
        tier_resolver: Optional["PatternTierResolver"] = None,
    ):
        """
        初始化 ModelRouter

        Args:
            fingerprint: 可选的 PerformanceFingerprint 实例（用于反哺）
            custom_profiles: 可选的自定义模型画像（覆盖默认值）
            tier_resolver: 可选的 PatternTierResolver 实例（Phase 10 新增；用于基于模式选择 tier）
        """
        # 模型画像：合并默认 + 自定义（自定义优先）
        self._profiles: Dict[ModelTier, ModelProfile] = dict(DEFAULT_PROFILES)
        if custom_profiles:
            for tier, profile in custom_profiles.items():
                if not isinstance(tier, ModelTier):
                    raise ModelRouterError(
                        f"custom_profiles 键必须是 ModelTier：{type(tier).__name__}"
                    )
                if not isinstance(profile, ModelProfile):
                    raise ModelRouterError(
                        f"custom_profiles 值必须是 ModelProfile：{type(profile).__name__}"
                    )
                self._profiles[tier] = profile

        # 性能画像（可空；为空时无反哺）
        self._fingerprint = fingerprint

        # Phase 10：PatternTierResolver（延迟导入 + 类型校验，避免循环导入）
        if tier_resolver is not None:
            # 延迟导入（避免循环；架构师审查 2.11）
            from pattern_tier_resolver import PatternTierResolver
            if not isinstance(tier_resolver, PatternTierResolver):
                raise ModelRouterError(
                    f"tier_resolver 必须是 PatternTierResolver 实例，"
                    f"实际为 {type(tier_resolver).__name__}"
                )
        self._tier_resolver = tier_resolver

        # 路由历史（内存缓存，用于审计 + 测试）
        self._decision_history: List[Dict[str, Any]] = []

        # 锁（线程安全）
        self._lock = threading.Lock()

        logger.info(
            "ModelRouter 初始化完成: tiers=%s, fingerprint=%s, tier_resolver=%s",
            [t.value for t in self._profiles.keys()],
            "enabled" if fingerprint else "disabled",
            "enabled" if tier_resolver else "disabled",
        )

    # ========================================================================
    # 公共方法
    # ========================================================================

    def route(
        self,
        feature: TaskFeature,
        explicit_tier: Optional[ModelTier] = None,
    ) -> RoutingDecision:
        """
        路由决策：根据任务特征选择最合适的模型

        决策流程（5 层优先级，Phase 10 强化）：
        0. 强制覆盖（explicit_tier，Phase 10 新增；来自 task._meta.model_tier）
        1. 关键路径检查（is_critical=True → opus / budget_remaining<0.1 → haiku / tight_deadline → sonnet）
           ↑ 关键路径强制高于 pattern_policy（架构师审查 2.6 安全约束）
        2. Pattern policy 解析（Phase 10 新增；feature.pattern_id 命中时）
        3. 静态规则决策（基于 task_complexity）
        4. 画像反哺（>= 10 samples 时加权历史决策）

        Args:
            feature: 任务特征
            explicit_tier: 可选的显式覆盖（Phase 10 新增；None 时不触发）

        Returns:
            RoutingDecision: 路由决策（含可解释性）
        """
        start_time = time.time()
        feature_snapshot = feature.to_dict()

        try:
            # 0. 强制覆盖（最高优先级）
            if explicit_tier is not None:
                if not isinstance(explicit_tier, ModelTier):
                    raise ModelRouterError(
                        f"explicit_tier 必须是 ModelTier，实际为 {type(explicit_tier).__name__}"
                    )
                decision = RoutingDecision(
                    selected_tier=explicit_tier,
                    confidence=1.0,
                    reasoning=(
                        f"显式声明 model_tier={explicit_tier.value}，强制覆盖"
                    ),
                    alternatives=[],
                    decision_source="explicit_override",
                )
            else:
                # 1. 关键路径检查（is_critical / budget / deadline）
                #    关键路径强制高于 pattern_policy，确保 critical 任务永远用 opus
                critical_decision = self._check_critical_path(feature)
                if critical_decision:
                    decision = critical_decision
                else:
                    # 2. Pattern policy 解析（Phase 10 新增）
                    pattern_decision = self._decide_by_pattern_policy(feature)
                    if pattern_decision:
                        decision = pattern_decision
                    else:
                        # 3. 静态规则决策
                        static_decision = self._decide_by_static_rule(feature)

                        # 4. 画像反哺（如果可用且样本充足）
                        if self._fingerprint and self._has_enough_samples():
                            history_decision = self._decide_by_history(feature, static_decision)
                            if history_decision:
                                decision = history_decision
                            else:
                                decision = static_decision
                        else:
                            decision = static_decision
        except Exception as e:
            # 决策失败时降级到 sonnet（最安全的中间档）
            logger.error("路由决策异常，降级到 sonnet: %s", e)
            decision = RoutingDecision(
                selected_tier=ModelTier.SONNET,
                confidence=0.5,
                reasoning=f"决策异常，降级到 sonnet：{e}",
                alternatives=[ModelTier.HAIKU, ModelTier.OPUS],
                feature_snapshot=feature_snapshot,
                decision_source="fallback_on_error",
            )

        # 记录决策耗时
        decision.decision_time_ms = (time.time() - start_time) * 1000
        decision.feature_snapshot = feature_snapshot

        # 写入内存历史
        with self._lock:
            self._decision_history.append({
                "decision": decision.to_dict(),
                "timestamp": time.time(),
            })
            # 限制历史大小（避免内存膨胀）
            if len(self._decision_history) > 500:
                self._decision_history = self._decision_history[-500:]

        logger.debug(
            "路由决策: tier=%s, confidence=%.2f, reasoning=%s",
            decision.selected_tier.value,
            decision.confidence,
            decision.reasoning,
        )
        return decision

    def record_decision(
        self,
        decision: RoutingDecision,
        actual_outcome: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        记录路由决策 + 实际结果到性能画像

        用于反哺：未来同类任务可参考历史成功决策。

        Args:
            decision: 路由决策
            actual_outcome: 实际执行结果，字段：
                - success: bool
                - quality: float (0-1, 实际产出质量)
                - error_type: Optional[str]
                - execution_time: float
        """
        if self._fingerprint is None:
            logger.debug("PerformanceFingerprint 未启用，跳过决策反哺")
            return

        outcome = actual_outcome or {}
        success = outcome.get("success", True)
        quality = outcome.get("quality", 0.0)
        error_type = outcome.get("error_type")

        # 估算复杂度（用于画像反哺，1-10 区间）
        task_complexity = decision.feature_snapshot.get("task_complexity", 5)

        try:
            self._fingerprint.record(
                task_type=f"model_routing:{decision.selected_tier.value}",
                task_complexity=task_complexity,
                success=success,
                error_type=error_type,
                execution_time=outcome.get("execution_time", 0.0),
                strategy=f"model_tier={decision.selected_tier.value};source={decision.decision_source}",
                context_features={
                    "model_tier": decision.selected_tier.value,
                    "decision_confidence": decision.confidence,
                    "decision_source": decision.decision_source,
                    "actual_quality": quality,
                    "feature_snapshot": decision.feature_snapshot,
                },
            )
        except Exception as e:
            logger.error("写入路由决策到画像失败: %s", e)

    def get_profiles(self) -> Dict[ModelTier, ModelProfile]:
        """
        获取所有模型画像（只读副本）

        Returns:
            模型画像字典
        """
        return dict(self._profiles)

    def get_profile(self, tier: ModelTier) -> ModelProfile:
        """
        获取指定层级的模型画像

        Args:
            tier: 模型层级

        Returns:
            模型画像

        Raises:
            ModelTierNotFoundError: tier 未在画像中
        """
        if tier not in self._profiles:
            raise ModelTierNotFoundError(f"模型层级 {tier.value} 未在画像中")
        return self._profiles[tier]

    def get_decision_history(self) -> List[Dict[str, Any]]:
        """
        获取内存中的路由决策历史（最多 500 条）

        Returns:
            决策历史列表
        """
        with self._lock:
            return list(self._decision_history)

    # ========================================================================
    # 内部方法 - 决策逻辑
    # ========================================================================

    def _decide_by_pattern_policy(
        self,
        feature: TaskFeature,
    ) -> Optional[RoutingDecision]:
        """
        Phase 10 新增：基于 PatternTierResolver 的策略决策

        触发条件：
        1. self._tier_resolver 存在
        2. feature.pattern_id 存在且非空

        决策流程：
        - 委托给 tier_resolver.resolve() 解析 tier
        - 如果解析结果 tier 为 None → 返回 None（fallback 到通用规则）
        - 否则构建 RoutingDecision 返回

        Returns:
            RoutingDecision 如果 resolver 返回有效 tier；否则 None
        """
        if self._tier_resolver is None:
            return None
        if not feature.pattern_id:
            return None

        # 委托给 resolver
        resolution = self._tier_resolver.resolve(
            pattern_id=feature.pattern_id,
            feature=feature,
        )

        # fallback（resolver 返回 tier=None）→ 走通用规则
        if resolution.tier is None:
            return None

        # 构建备选 tier 列表（除 selected_tier 外的所有 tier）
        alternatives = [t for t in ModelTier if t != resolution.tier]

        return RoutingDecision(
            selected_tier=resolution.tier,
            confidence=resolution.confidence,
            reasoning=resolution.reasoning,
            alternatives=alternatives,
            decision_source=f"pattern_policy:{feature.pattern_id}",
        )

    def _check_critical_path(self, feature: TaskFeature) -> Optional[RoutingDecision]:
        """
        关键路径检查：is_critical / 预算耗尽 / 截止时间紧

        Returns:
            RoutingDecision 如果命中关键路径；否则 None（继续走静态规则）
        """
        # 1. 关键任务 → 强制 opus
        if feature.is_critical:
            return RoutingDecision(
                selected_tier=ModelTier.OPUS,
                confidence=0.95,
                reasoning="关键任务（is_critical=True），强制使用 opus 确保质量",
                alternatives=[ModelTier.SONNET],
                decision_source="static_rule:critical_task",
            )

        # 2. 预算耗尽（< 10%）→ 强制 haiku
        if feature.budget_remaining < BUDGET_EXHAUSTED_THRESHOLD:
            return RoutingDecision(
                selected_tier=ModelTier.HAIKU,
                confidence=0.90,
                reasoning=f"预算即将耗尽（剩余 {feature.budget_remaining:.1%} < {BUDGET_EXHAUSTED_THRESHOLD:.0%}），强制使用 haiku 节省成本",
                alternatives=[ModelTier.SONNET],
                decision_source="static_rule:budget_exhausted",
            )

        # 3. 截止时间紧（< 5s）+ 质量阈值宽松（< 0.8）→ sonnet
        if (
            feature.deadline_ms is not None
            and feature.deadline_ms < TIGHT_DEADLINE_MS
            and feature.quality_threshold < 0.8
        ):
            return RoutingDecision(
                selected_tier=ModelTier.SONNET,
                confidence=0.85,
                reasoning=f"截止时间紧（{feature.deadline_ms}ms < {TIGHT_DEADLINE_MS}ms）且质量阈值宽松（{feature.quality_threshold:.2f}），使用 sonnet 平衡速度与质量",
                alternatives=[ModelTier.HAIKU, ModelTier.OPUS],
                decision_source="static_rule:tight_deadline",
            )

        # 未命中关键路径
        return None

    def _decide_by_static_rule(self, feature: TaskFeature) -> RoutingDecision:
        """
        静态规则决策：基于任务复杂度分级

        规则：
        - 1-3  → haiku（低复杂度）
        - 4-6  → sonnet（中等复杂度）
        - 7-10 → opus（高复杂度）
        """
        complexity = feature.task_complexity

        if complexity <= 3:
            return RoutingDecision(
                selected_tier=ModelTier.HAIKU,
                confidence=0.80,
                reasoning=f"低复杂度任务（complexity={complexity} <= 3），haiku 即可满足",
                alternatives=[ModelTier.SONNET, ModelTier.OPUS],
                decision_source="static_rule:low_complexity",
            )
        elif complexity <= 6:
            return RoutingDecision(
                selected_tier=ModelTier.SONNET,
                confidence=0.80,
                reasoning=f"中等复杂度任务（complexity={complexity} in [4,6]），sonnet 平衡成本与质量",
                alternatives=[ModelTier.HAIKU, ModelTier.OPUS],
                decision_source="static_rule:medium_complexity",
            )
        else:
            return RoutingDecision(
                selected_tier=ModelTier.OPUS,
                confidence=0.80,
                reasoning=f"高复杂度任务（complexity={complexity} >= 7），opus 必需",
                alternatives=[ModelTier.SONNET],
                decision_source="static_rule:high_complexity",
            )

    def _decide_by_history(
        self,
        feature: TaskFeature,
        static_decision: RoutingDecision,
    ) -> Optional[RoutingDecision]:
        """
        画像反哺决策：检索历史相似任务的成功决策，加权到静态规则

        策略：
        - 检索同 task_type + 相近 task_complexity 的历史记录
        - 取最近 N 次成功记录中 model_tier 的众数
        - 加权：历史权重 0.6 + 静态规则权重 0.4
        - 如果历史决策与静态决策一致 → 提高置信度
        - 如果历史决策与静态决策不一致 → 选择历史决策（基于真实数据）

        Args:
            feature: 任务特征
            static_decision: 静态规则决策

        Returns:
            RoutingDecision 如果有可用历史；否则 None
        """
        if self._fingerprint is None:
            return None

        try:
            # 检索相似历史（同 task_type + complexity ±2）
            target_complexity = feature.task_complexity
            target_task_type = f"model_routing:"  # 检索所有 model_routing 记录

            similar_records = []
            for record in self._fingerprint.records:
                if not record.strategy.startswith("model_tier="):
                    continue
                if abs(record.task_complexity - target_complexity) > 2:
                    continue
                if not record.success:
                    continue
                similar_records.append(record)

            if not similar_records:
                return None

            # 取最近 20 条
            similar_records = similar_records[-20:]

            # 统计 model_tier 众数
            from collections import Counter
            tier_counts = Counter()
            for record in similar_records:
                # strategy 格式: "model_tier=xxx;source=yyy"
                for part in record.strategy.split(";"):
                    if part.startswith("model_tier="):
                        tier_value = part.split("=", 1)[1]
                        try:
                            tier_counts[ModelTier.from_str(tier_value)] += 1
                        except ModelTierNotFoundError:
                            continue
                        break

            if not tier_counts:
                return None

            # 众数
            history_tier, history_count = tier_counts.most_common(1)[0]
            history_ratio = history_count / sum(tier_counts.values())

            # 加权决策
            if history_tier == static_decision.selected_tier:
                # 一致 → 提高置信度
                new_confidence = min(
                    1.0,
                    static_decision.confidence * STATIC_RULE_WEIGHT
                    + history_ratio * FINGERPRINT_HISTORY_WEIGHT
                    + 0.1,  # 一致性奖励
                )
                return RoutingDecision(
                    selected_tier=history_tier,
                    confidence=new_confidence,
                    reasoning=(
                        f"画像反哺：历史 {history_count}/{sum(tier_counts.values())} 次同类任务"
                        f"均使用 {history_tier.value}，与静态规则一致，提高置信度"
                    ),
                    alternatives=static_decision.alternatives,
                    decision_source="fingerprint_history:consistent",
                )
            else:
                # 不一致 → 采用历史决策（基于真实数据优先）
                new_confidence = min(
                    1.0,
                    static_decision.confidence * STATIC_RULE_WEIGHT
                    + history_ratio * FINGERPRINT_HISTORY_WEIGHT,
                )
                return RoutingDecision(
                    selected_tier=history_tier,
                    confidence=new_confidence,
                    reasoning=(
                        f"画像反哺：历史 {history_count}/{sum(tier_counts.values())} 次同类任务"
                        f"使用 {history_tier.value}（与静态规则的 {static_decision.selected_tier.value} 不一致），"
                        f"基于真实数据采用 {history_tier.value}"
                    ),
                    alternatives=[static_decision.selected_tier] + static_decision.alternatives,
                    decision_source="fingerprint_history:override",
                )
        except Exception as e:
            logger.error("画像反哺决策失败，降级到静态规则: %s", e)
            return None

    def _has_enough_samples(self) -> bool:
        """
        检查画像样本数是否足够（>= MIN_FINGERPRINT_SAMPLES）

        Returns:
            True 表示样本充足，可启用反哺；False 表示冷启动
        """
        if self._fingerprint is None:
            return False
        return self._fingerprint.total_executions >= MIN_FINGERPRINT_SAMPLES


# ============================================================================
# 模块导出
# ============================================================================

__all__ = [
    # 异常
    "ModelRouterError",
    "InvalidTaskFeatureError",
    "ModelTierNotFoundError",
    # 枚举
    "ModelTier",
    # 数据类
    "ModelProfile",
    "TaskFeature",
    "RoutingDecision",
    # 常量
    "DEFAULT_PROFILES",
    "HIGH_COMPLEXITY_THRESHOLD",
    "BUDGET_EXHAUSTED_THRESHOLD",
    "TIGHT_DEADLINE_MS",
    "MIN_FINGERPRINT_SAMPLES",
    # 主类
    "ModelRouter",
]
