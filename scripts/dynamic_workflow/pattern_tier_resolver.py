#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dynamic Workflows 模式 tier 解析器（Pattern Tier Resolver）

Phase 10 实现：根据 pattern_id 推导 model_tier

核心职责：
1. 6 大模式 → ModelTier 映射表（默认策略）
2. 支持升级/降级条件（基于 TaskFeature 字段）
3. 显式覆盖（explicit_tier 参数，优先级最高）
4. 未知 pattern_id → fallback（返回 tier=None，由 ModelRouter 走通用规则）
5. 自定义策略覆盖默认（custom_policies）
6. 线程安全（注册 / 解析均加锁）

设计约束（来自 DYNAMIC_WORKFLOWS_INTEGRATION.md §3.0）：
- 🔴 持久化复用：不新建存储，仅做策略计算
- 🔴 V2 不修改：本模块独立运行
- 🔴 安全：仅依据 pattern_id + TaskFeature 字段决策；不读取任务描述
- 🔴 一阶段一模块：仅做 tier 解析，不做路由 / 调度
- 🔴 6 大模式上限：不引入新模式

升级/降级条件优先级（高到低）：
0. explicit_tier 强制覆盖（来自调用方 / task._meta.model_tier）
1. upgrade_condition 触发 → upgrade_to
2. downgrade_condition 触发 → downgrade_to
3. 否则 → default_tier

参考来源：
- [PHASE10_PLAN.md v1.1]
- [DYNAMIC_WORKFLOWS_INTEGRATION.md v1.6]
- [ARCHITECT_REVIEW_DYNAMIC_WORKFLOWS.md]

作者：trae-multi-agent 融合 Phase 10
创建日期：2026-06-05
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

# 复用 ModelTier 枚举与 TaskFeature
try:
    from model_router import ModelTier, ModelRouterError, TaskFeature
except ImportError:
    # 当作为独立模块加载时（tests/ 目录场景），添加 scripts 目录到 sys.path
    import sys
    from pathlib import Path
    SCRIPTS_DIR = Path(__file__).resolve().parent.parent
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    from model_router import ModelTier, ModelRouterError, TaskFeature


# ============================================================================
# 日志配置
# ============================================================================

logger = logging.getLogger("dynamic_workflow.pattern_tier_resolver")
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

class PatternTierPolicyError(ModelRouterError):
    """PatternTierPolicy / PatternTierResolver 异常基类"""


class InvalidTierError(PatternTierPolicyError):
    """非法 tier 值"""


# ============================================================================
# 数据类定义
# ============================================================================

@dataclass
class PatternTierPolicy:
    """
    单个模式的 tier 策略

    字段：
    - pattern_id: 模式 ID（kebab-case，与 WorkflowPattern.pattern_id 对齐）
    - default_tier: 默认 tier
    - upgrade_to: 升级目标 tier（可选）
    - upgrade_condition: 升级条件 callable，接收 TaskFeature，返回 bool
    - downgrade_to: 降级目标 tier（可选）
    - downgrade_condition: 降级条件 callable
    - rationale: 策略理由（中文，用于可解释性）
    """
    pattern_id: str
    default_tier: ModelTier
    upgrade_to: Optional[ModelTier] = None
    upgrade_condition: Optional[Callable[[TaskFeature], bool]] = None
    downgrade_to: Optional[ModelTier] = None
    downgrade_condition: Optional[Callable[[TaskFeature], bool]] = None
    rationale: str = ""

    def __post_init__(self):
        """字段合法性校验（构造时立即检查）"""
        # pattern_id 校验（kebab-case）
        import re
        if not re.match(r"^[a-z][a-z0-9-]*[a-z0-9]$", self.pattern_id):
            raise PatternTierPolicyError(
                f"pattern_id '{self.pattern_id}' 不符合 kebab-case 命名规范"
            )

        # default_tier 必须是 ModelTier
        if not isinstance(self.default_tier, ModelTier):
            raise PatternTierPolicyError(
                f"default_tier 必须是 ModelTier 枚举，"
                f"实际为 {type(self.default_tier).__name__}"
            )

        # upgrade_to / downgrade_to 校验
        for field_name, value in [
            ("upgrade_to", self.upgrade_to),
            ("downgrade_to", self.downgrade_to),
        ]:
            if value is not None and not isinstance(value, ModelTier):
                raise PatternTierPolicyError(
                    f"{field_name} 必须是 ModelTier 或 None，"
                    f"实际为 {type(value).__name__}"
                )

        # upgrade_condition / downgrade_condition 必须是 callable
        for field_name, value in [
            ("upgrade_condition", self.upgrade_condition),
            ("downgrade_condition", self.downgrade_condition),
        ]:
            if value is not None and not callable(value):
                raise PatternTierPolicyError(
                    f"{field_name} 必须是 callable 或 None，"
                    f"实际为 {type(value).__name__}"
                )

        # 升级目标不能等于 default_tier（避免无意义升级）
        if (
            self.upgrade_to is not None
            and self.upgrade_to == self.default_tier
        ):
            raise PatternTierPolicyError(
                f"upgrade_to ({self.upgrade_to.value}) 不能等于 default_tier，"
                f"请明确升级目标差异"
            )


@dataclass
class TierResolution:
    """
    tier 解析结果

    字段：
    - tier: 解析得到的 ModelTier（None 表示 fallback，由 ModelRouter 走通用规则）
    - source: 决策来源
        - "explicit_override"：调用方显式指定
        - "pattern_policy_default"：模式默认策略
        - "pattern_policy_upgrade"：模式升级条件触发
        - "pattern_policy_downgrade"：模式降级条件触发
        - "fallback"：未匹配任何策略
    - reasoning: 决策理由（中文）
    - confidence: 置信度 (0-1)
    """
    tier: Optional[ModelTier]
    source: str
    reasoning: str
    confidence: float

    def to_dict(self) -> Dict[str, Any]:
        """转字典（用于画像反哺和 JSON 输出）"""
        return {
            "tier": self.tier.value if self.tier else None,
            "source": self.source,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
        }


# ============================================================================
# 6 大模式默认策略
# ============================================================================

def _adversarial_verify_upgrade(f: TaskFeature) -> bool:
    """
    adversarial-verify 升级条件：高风险任务
    （当前实现：永不升级，opus 已为最高 tier）
    """
    return False


def _generate_filter_upgrade(f: TaskFeature) -> bool:
    """
    generate-filter 升级条件：任务复杂度 ≥ 8 → sonnet
    """
    return f.task_complexity >= 8


def _generate_filter_downgrade(f: TaskFeature) -> bool:
    """
    generate-filter 降级条件：预算即将耗尽 → haiku（已是最低，无变化）
    （保留接口，downgrade_to=None 表示不降级）
    """
    return False


def _loop_until_done_upgrade(f: TaskFeature) -> bool:
    """
    loop-until-done 升级条件：最终轮（is_final_iteration=True）→ sonnet
    """
    return f.extra.get("is_final_iteration", False) is True


def _fan_out_aggregate_upgrade(f: TaskFeature) -> bool:
    """
    fan-out-aggregate 升级条件：子任务数 ≥ 50 → sonnet
    """
    return f.extra.get("subtask_count", 0) >= 50


def _tournament_upgrade(f: TaskFeature) -> bool:
    """
    tournament 升级条件：高风险任务（risk_level in high/critical）→ opus
    """
    risk = f.extra.get("risk_level", "low")
    return risk in ("high", "critical")


def _classifier_dispatch_upgrade(f: TaskFeature) -> bool:
    """
    classifier-dispatch 升级条件：异构类型 ≥ 5 → opus
    """
    return f.extra.get("type_variants", 0) >= 5


# ============================================================================
# PatternTierResolver 主类
# ============================================================================

class PatternTierResolver:
    """
    模式 tier 解析器

    6 大模式默认策略：
    - adversarial-verify → opus（验证者质量优先）
    - generate-filter → haiku（大量生成成本优先）
    - loop-until-done → haiku（最终轮升级 sonnet）
    - fan-out-aggregate → haiku（子任务同质降级）
    - tournament → sonnet（候选质量平衡）
    - classifier-dispatch → sonnet（路由决策准确性）

    使用方式：
    ```python
    resolver = PatternTierResolver()
    feature = TaskFeature(
        task_complexity=5,
        estimated_tokens=1000,
        pattern_id="adversarial-verify",
    )
    resolution = resolver.resolve(pattern_id=feature.pattern_id, feature=feature)
    print(resolution.tier)  # ModelTier.OPUS
    print(resolution.reasoning)  # "模式 adversarial-verify 默认策略，..."

    # 显式覆盖
    resolution = resolver.resolve(
        pattern_id="adversarial-verify",
        feature=feature,
        explicit_tier=ModelTier.HAIKU,
    )
    print(resolution.tier)  # ModelTier.HAIKU（覆盖）
    ```

    线程安全：所有读/写均通过 _lock 保护。
    """

    def __init__(
        self,
        custom_policies: Optional[Dict[str, PatternTierPolicy]] = None,
    ):
        """
        初始化 PatternTierResolver

        Args:
            custom_policies: 自定义策略字典（key=pattern_id），覆盖默认
        """
        # 策略注册表
        self._policies: Dict[str, PatternTierPolicy] = {}
        # 锁（线程安全；用 RLock 支持 register_policy 时遍历 _policies）
        self._lock = threading.RLock()

        # 注册 6 大默认策略
        self._register_default_policies()

        # 应用用户自定义
        if custom_policies:
            for pattern_id, policy in custom_policies.items():
                if not isinstance(policy, PatternTierPolicy):
                    raise PatternTierPolicyError(
                        f"custom_policies[{pattern_id}] 必须是 PatternTierPolicy，"
                        f"实际为 {type(policy).__name__}"
                    )
                if policy.pattern_id != pattern_id:
                    raise PatternTierPolicyError(
                        f"custom_policies key '{pattern_id}' 与 policy.pattern_id "
                        f"'{policy.pattern_id}' 不一致"
                    )
            with self._lock:
                self._policies.update(custom_policies)

        logger.info(
            f"PatternTierResolver 初始化完成：{len(self._policies)} 个策略 "
            f"({', '.join(sorted(self._policies.keys()))})"
        )

    def _register_default_policies(self) -> None:
        """注册 6 大默认策略（Phase 10 设计）"""
        with self._lock:
            # 1. adversarial-verify → opus（永不降级；升级条件永不触发）
            self._policies["adversarial-verify"] = PatternTierPolicy(
                pattern_id="adversarial-verify",
                default_tier=ModelTier.OPUS,
                upgrade_condition=_adversarial_verify_upgrade,
                rationale=(
                    "验证者放行 bias → 质量优先 opus；"
                    "永不降级（critical 任务必须高质量）"
                ),
            )

            # 2. generate-filter → haiku（complexity >= 8 升级 sonnet）
            self._policies["generate-filter"] = PatternTierPolicy(
                pattern_id="generate-filter",
                default_tier=ModelTier.HAIKU,
                upgrade_to=ModelTier.SONNET,
                upgrade_condition=_generate_filter_upgrade,
                downgrade_condition=_generate_filter_downgrade,
                rationale=(
                    "大量生成、单次成本优先 haiku；"
                    "高复杂度（>=8）升级 sonnet"
                ),
            )

            # 3. loop-until-done → haiku（最终轮升级 sonnet）
            self._policies["loop-until-done"] = PatternTierPolicy(
                pattern_id="loop-until-done",
                default_tier=ModelTier.HAIKU,
                upgrade_to=ModelTier.SONNET,
                upgrade_condition=_loop_until_done_upgrade,
                rationale=(
                    "多数迭代轻量 haiku；"
                    "最终轮（is_final_iteration=True）升级 sonnet"
                ),
            )

            # 4. fan-out-aggregate → haiku（subtask_count >= 50 升级 sonnet）
            self._policies["fan-out-aggregate"] = PatternTierPolicy(
                pattern_id="fan-out-aggregate",
                default_tier=ModelTier.HAIKU,
                upgrade_to=ModelTier.SONNET,
                upgrade_condition=_fan_out_aggregate_upgrade,
                rationale=(
                    "子任务同质、批量降级 haiku；"
                    ">= 50 子任务升级 sonnet"
                ),
            )

            # 5. tournament → sonnet（risk >= high 升级 opus）
            self._policies["tournament"] = PatternTierPolicy(
                pattern_id="tournament",
                default_tier=ModelTier.SONNET,
                upgrade_to=ModelTier.OPUS,
                upgrade_condition=_tournament_upgrade,
                rationale=(
                    "候选质量平衡 sonnet；"
                    "高风险（risk_level in high/critical）升级 opus"
                ),
            )

            # 6. classifier-dispatch → sonnet（type_variants >= 5 升级 opus）
            self._policies["classifier-dispatch"] = PatternTierPolicy(
                pattern_id="classifier-dispatch",
                default_tier=ModelTier.SONNET,
                upgrade_to=ModelTier.OPUS,
                upgrade_condition=_classifier_dispatch_upgrade,
                rationale=(
                    "路由决策准确性 sonnet；"
                    "异构类型 >= 5 升级 opus"
                ),
            )

    # ========================================================================
    # 公共方法
    # ========================================================================

    def resolve(
        self,
        pattern_id: Optional[str],
        feature: TaskFeature,
        explicit_tier: Optional[ModelTier] = None,
    ) -> TierResolution:
        """
        解析 model_tier

        优先级：
        0. explicit_tier 强制覆盖
        1. pattern_id 命中 + upgrade 条件触发 → upgrade_to
        2. pattern_id 命中 + downgrade 条件触发 → downgrade_to
        3. pattern_id 命中 → default_tier
        4. 未匹配 → fallback（tier=None）

        Args:
            pattern_id: 当前任务所属模式（None/空字符串/unknown 时走 fallback）
            feature: 任务特征（用于 upgrade/downgrade 条件判断）
            explicit_tier: 调用方显式指定的 tier（优先级最高）

        Returns:
            TierResolution: 解析结果
        """
        # 0. 强制覆盖（最高优先级）
        if explicit_tier is not None:
            if not isinstance(explicit_tier, ModelTier):
                raise InvalidTierError(
                    f"explicit_tier 必须是 ModelTier 枚举，"
                    f"实际为 {type(explicit_tier).__name__}"
                )
            return TierResolution(
                tier=explicit_tier,
                source="explicit_override",
                reasoning=(
                    f"显式声明 model_tier={explicit_tier.value}，"
                    f"强制覆盖（pattern_id={pattern_id}）"
                ),
                confidence=1.0,
            )

        # 1-3. Pattern policy 匹配
        if pattern_id:
            with self._lock:
                policy = self._policies.get(pattern_id)

            if policy is not None:
                # 1. 检查 upgrade
                if (
                    policy.upgrade_to is not None
                    and policy.upgrade_condition is not None
                ):
                    try:
                        if policy.upgrade_condition(feature):
                            return TierResolution(
                                tier=policy.upgrade_to,
                                source="pattern_policy_upgrade",
                                reasoning=(
                                    f"模式 {pattern_id} 升级条件触发，"
                                    f"使用 {policy.upgrade_to.value} "
                                    f"（默认 {policy.default_tier.value}）"
                                ),
                                confidence=0.90,
                            )
                    except Exception as e:
                        # 升级条件异常 → 降级到 default（架构师审查 2.8 建议）
                        logger.warning(
                            f"模式 {pattern_id} upgrade_condition 异常，"
                            f"降级到 default_tier：{e}"
                        )

                # 2. 检查 downgrade
                if (
                    policy.downgrade_to is not None
                    and policy.downgrade_condition is not None
                ):
                    try:
                        if policy.downgrade_condition(feature):
                            return TierResolution(
                                tier=policy.downgrade_to,
                                source="pattern_policy_downgrade",
                                reasoning=(
                                    f"模式 {pattern_id} 降级条件触发，"
                                    f"使用 {policy.downgrade_to.value} "
                                    f"（默认 {policy.default_tier.value}）"
                                ),
                                confidence=0.85,
                            )
                    except Exception as e:
                        logger.warning(
                            f"模式 {pattern_id} downgrade_condition 异常，"
                            f"降级到 default_tier：{e}"
                        )

                # 3. 默认策略
                return TierResolution(
                    tier=policy.default_tier,
                    source="pattern_policy_default",
                    reasoning=(
                        f"模式 {pattern_id} 默认策略，"
                        f"使用 {policy.default_tier.value}（{policy.rationale}）"
                    ),
                    confidence=0.85,
                )

        # 4. Fallback（tier=None，由 ModelRouter 走通用规则）
        return TierResolution(
            tier=None,
            source="fallback",
            reasoning=(
                f"未匹配 pattern policy（pattern_id={pattern_id}），"
                f"由 ModelRouter 通用规则决策"
            ),
            confidence=0.0,
        )

    def register_policy(self, policy: PatternTierPolicy) -> None:
        """
        运行时注册/覆盖自定义 policy

        Args:
            policy: PatternTierPolicy 实例
        """
        if not isinstance(policy, PatternTierPolicy):
            raise PatternTierPolicyError(
                f"policy 必须是 PatternTierPolicy，实际为 {type(policy).__name__}"
            )
        with self._lock:
            self._policies[policy.pattern_id] = policy
            logger.info(f"注册自定义 policy: {policy.pattern_id}")

    def get_policy(self, pattern_id: str) -> Optional[PatternTierPolicy]:
        """
        获取 policy（线程安全）

        Args:
            pattern_id: 模式 ID

        Returns:
            PatternTierPolicy 或 None
        """
        with self._lock:
            return self._policies.get(pattern_id)

    def list_pattern_ids(self) -> list:
        """列出所有已注册 pattern_id（排序）"""
        with self._lock:
            return sorted(self._policies.keys())

    def size(self) -> int:
        """返回已注册 policy 数"""
        with self._lock:
            return len(self._policies)

    def __len__(self) -> int:
        """支持 len(resolver) 调用"""
        return self.size()


# ============================================================================
# 便捷函数
# ============================================================================

def create_default_resolver() -> PatternTierResolver:
    """
    创建默认配置的 PatternTierResolver（6 大默认策略）
    """
    return PatternTierResolver()


# ============================================================================
# 模块导出
# ============================================================================

__all__ = [
    # 异常
    "PatternTierPolicyError",
    "InvalidTierError",
    # 数据类
    "PatternTierPolicy",
    "TierResolution",
    # 主类
    "PatternTierResolver",
    # 便捷函数
    "create_default_resolver",
]
