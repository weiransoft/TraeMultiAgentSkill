#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dynamic Workflows Token 预算守护（Token Budget Guard）

Phase 3 实现：执行期 Token 监控 + 自动降级

核心职责：
1. 三阶段 Token 校验：pre_execute / during_execute / post_execute
2. 超限触发降级（切换 haiku 继续）而非中断
3. 与 GuardCoordinator 兼容（validate() 接口对齐）
4. 降级历史写入 PerformanceFingerprint（反哺 + 审计）
5. 预算异常时硬中断（HARD 模式）

设计约束（来自 DYNAMIC_WORKFLOWS_INTEGRATION.md §3.0）：
- 🔴 持久化复用：决策历史写入 PerformanceFingerprint.execution_record
- 🔴 V2 不修改：本模块独立运行，不触碰 V2 引擎
- 🔴 安全：Token 硬上限（HARD 模式），不允许超额消耗
- 🔴 一阶段一模块：仅 Token 预算，不引入模型路由（独立模块 ModelRouter）

参考来源：
- [DYNAMIC_WORKFLOWS_INTEGRATION.md v1.1 §模块 4]
- [Anthropic Dynamic Workflows - Token 预算]
- [PHASE3_PLAN.md]
- [PHASE2_FINAL_REPORT.md §3.2.4 Token 预算硬上限]

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
from typing import Any, Dict, List, Optional, Tuple

# 复用现有 PerformanceFingerprint（架构师审查 §3.0.1 强约束）
try:
    from performance_fingerprint import PerformanceFingerprint
    from guard_coordinator import (
        RiskLevel,
        ValidationWarning,
        CompensationStrategy,
        ValidationResult,
    )
except ImportError:
    # 当作为独立模块加载时（tests/ 目录场景），添加 scripts 目录到 sys.path
    import sys
    SCRIPTS_DIR = Path(__file__).resolve().parent.parent
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    from performance_fingerprint import PerformanceFingerprint
    from guard_coordinator import (
        RiskLevel,
        ValidationWarning,
        CompensationStrategy,
        ValidationResult,
    )


# ============================================================================
# 日志配置
# ============================================================================

logger = logging.getLogger("dynamic_workflow.token_budget_guard")
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

class TokenBudgetGuardError(Exception):
    """TokenBudgetGuard 异常基类"""


class TokenBudgetExceeded(TokenBudgetGuardError):
    """
    Token 预算超限异常

    当 Token 消耗达到硬阈值（HARD 模式）时抛出。
    """

    def __init__(self, consumed: int, budget: int, message: Optional[str] = None):
        """
        Args:
            consumed: 实际消耗的 token 数
            budget: 总预算 token 数
            message: 可选的自定义错误消息
        """
        self.consumed = consumed
        self.budget = budget
        if message is None:
            message = f"Token 预算超限：consumed={consumed}, budget={budget}, ratio={consumed/budget:.1%}"
        super().__init__(message)

    def __repr__(self) -> str:
        return (
            f"TokenBudgetExceeded(consumed={self.consumed}, budget={self.budget}, "
            f"ratio={self.consumed/self.budget:.1%})"
        )


class InvalidBudgetError(TokenBudgetGuardError):
    """预算参数非法"""


# ============================================================================
# 枚举定义
# ============================================================================

class BudgetEnforcementMode(str, Enum):
    """
    预算执行模式

    - HARD   ：硬上限，超限立即抛 TokenBudgetExceeded
    - SOFT   ：软上限，超限警告 + 切换 haiku 继续
    - HYBRID ：混合：>= 100% → hard；>= 80% → soft
    """
    HARD   = "hard"
    SOFT   = "soft"
    HYBRID = "hybrid"

    @classmethod
    def from_str(cls, value: str) -> "BudgetEnforcementMode":
        """从字符串解析（大小写不敏感）"""
        normalized = value.lower().strip()
        for mode in cls:
            if mode.value == normalized:
                return mode
        raise TokenBudgetGuardError(
            f"未知的执行模式：{value}（有效值：hard / soft / hybrid）"
        )


class BudgetRecommendation(str, Enum):
    """预算决策建议"""
    CONTINUE         = "continue"           # 继续
    SWITCH_TO_HAIKU  = "switch_to_haiku"    # 切换 haiku
    SPLIT_TASK       = "split_task"         # 拆分任务
    ABORT            = "abort"              # 中止
    RETRY_WITH_LOWER = "retry_with_lower"   # 重试（更小输入）


# ============================================================================
# 数据类定义
# ============================================================================

@dataclass
class TokenBudget:
    """
    Token 预算

    描述一次任务执行的 Token 预算上限与消耗状态。
    """
    total_budget: int                       # 总预算
    consumed: int = 0                       # 已消耗
    reserved: int = 0                       # 预留（并行任务）
    soft_threshold: float = 0.8             # 软阈值（达到此比例触发降级）
    hard_threshold: float = 1.0             # 硬阈值（达到此比例触发中断）

    def __post_init__(self):
        """字段合法性校验"""
        if self.total_budget <= 0:
            raise InvalidBudgetError(
                f"total_budget 必须为正整数：{self.total_budget}"
            )
        if self.consumed < 0:
            raise InvalidBudgetError(
                f"consumed 不能为负：{self.consumed}"
            )
        if self.reserved < 0:
            raise InvalidBudgetError(
                f"reserved 不能为负：{self.reserved}"
            )
        if not (0.0 < self.soft_threshold < 1.0):
            raise InvalidBudgetError(
                f"soft_threshold 必须在 (0, 1) 范围内：{self.soft_threshold}"
            )
        if not (0.0 < self.hard_threshold <= 1.0):
            raise InvalidBudgetError(
                f"hard_threshold 必须在 (0, 1] 范围内：{self.hard_threshold}"
            )
        if self.soft_threshold >= self.hard_threshold:
            raise InvalidBudgetError(
                f"soft_threshold ({self.soft_threshold}) 必须 < hard_threshold ({self.hard_threshold})"
            )

    @property
    def consumption_ratio(self) -> float:
        """消费比例（consumed / total_budget）"""
        return self.consumed / self.total_budget

    @property
    def remaining(self) -> int:
        """剩余 token 数（不含 reserved）"""
        return max(0, self.total_budget - self.consumed - self.reserved)

    @property
    def is_soft_exceeded(self) -> bool:
        """是否达到软阈值"""
        return self.consumption_ratio >= self.soft_threshold

    @property
    def is_hard_exceeded(self) -> bool:
        """是否达到硬阈值"""
        return self.consumption_ratio >= self.hard_threshold

    def to_dict(self) -> Dict[str, Any]:
        """转字典（用于画像反哺）"""
        return {
            "total_budget": self.total_budget,
            "consumed": self.consumed,
            "reserved": self.reserved,
            "soft_threshold": self.soft_threshold,
            "hard_threshold": self.hard_threshold,
            "consumption_ratio": self.consumption_ratio,
            "remaining": self.remaining,
        }


@dataclass
class BudgetDecision:
    """
    预算决策

    Token 校验的结果：是否允许继续 + 建议 + 警告。
    """
    allow_continue: bool                                # 是否允许继续
    enforcement: BudgetEnforcementMode                  # 适用的执行模式
    recommendation: BudgetRecommendation                # 建议动作
    remaining: int                                      # 剩余 token
    consumption_ratio: float                            # 当前消费比例
    warnings: List[str] = field(default_factory=list)   # 警告列表
    stage: str = "pre_execute"                          # 校验阶段：pre/during/post

    def to_dict(self) -> Dict[str, Any]:
        """转字典（用于画像反哺）"""
        return {
            "allow_continue": self.allow_continue,
            "enforcement": self.enforcement.value,
            "recommendation": self.recommendation.value,
            "remaining": self.remaining,
            "consumption_ratio": self.consumption_ratio,
            "warnings": self.warnings,
            "stage": self.stage,
        }


# ============================================================================
# 决策阈值常量
# ============================================================================

# 默认软阈值（达到此比例触发降级）
DEFAULT_SOFT_THRESHOLD = 0.8

# 默认硬阈值（达到此比例触发中断）
DEFAULT_HARD_THRESHOLD = 1.0

# pre_execute 校验：estimated_tokens 超过 total 的此比例 → 拒绝启动
PRE_EXECUTE_REJECT_RATIO = 1.0

# pre_execute 校验：estimated_tokens 超过 total 的此比例 → 软警告 + 建议降级
PRE_EXECUTE_SOFT_RATIO = 0.8


# ============================================================================
# TokenBudgetGuard 主类
# ============================================================================

class TokenBudgetGuard:
    """
    Token 预算守护

    三阶段 Token 校验：pre_execute / during_execute / post_execute
    超限触发降级（切换 haiku）而非中断（HARD 模式除外）。
    与 GuardCoordinator 接口兼容。

    使用方式：
    ```python
    guard = TokenBudgetGuard(fingerprint=PerformanceFingerprint(agent_id="..."))

    # 1. 创建预算
    budget = guard.create_budget(total=100_000)

    # 2. 预检
    decision = guard.pre_execute_check(budget, estimated_tokens=90_000)
    if not decision.allow_continue:
        raise Exception("任务过大")

    # 3. 记录消费
    decision = guard.record_consumption(budget, consumed=85_000)
    # -> 软阈值触发，建议切换 haiku

    # 4. 后审
    guard.post_execute_review(budget, success=True)
    ```

    与 GuardCoordinator 集成：
    ```python
    guard_coordinator = GuardCoordinator(agent_id="...")
    budget_guard = TokenBudgetGuard(fingerprint=...)

    # 在 pre_execute_validation 阶段调用
    validation_result = budget_guard.validate(task_dict)
    # -> ValidationResult（与 GuardCoordinator.validate 兼容）
    ```
    """

    def __init__(
        self,
        fingerprint: Optional[PerformanceFingerprint] = None,
        default_mode: BudgetEnforcementMode = BudgetEnforcementMode.HARD,
    ):
        """
        初始化 TokenBudgetGuard

        Args:
            fingerprint: 可选的 PerformanceFingerprint 实例（用于反哺）
            default_mode: 默认执行模式（HARD / SOFT / HYBRID）
        """
        self._fingerprint = fingerprint
        self._default_mode = default_mode

        # 预算字典（task_id -> TokenBudget），支持并发
        self._budgets: Dict[str, TokenBudget] = {}

        # 决策历史（内存缓存，用于审计 + 测试）
        self._decision_history: List[Dict[str, Any]] = []

        # 锁（线程安全）
        self._lock = threading.Lock()

        logger.info(
            "TokenBudgetGuard 初始化完成: mode=%s, fingerprint=%s",
            default_mode.value,
            "enabled" if fingerprint else "disabled",
        )

    # ========================================================================
    # 公共方法 - 预算管理
    # ========================================================================

    def create_budget(
        self,
        total: int,
        soft_threshold: float = DEFAULT_SOFT_THRESHOLD,
        hard_threshold: float = DEFAULT_HARD_THRESHOLD,
        task_id: Optional[str] = None,
    ) -> TokenBudget:
        """
        创建 Token 预算

        Args:
            total: 总预算 token 数
            soft_threshold: 软阈值（达到此比例触发降级）
            hard_threshold: 硬阈值（达到此比例触发中断）
            task_id: 任务 ID（用于内部管理；None 则自动生成）

        Returns:
            TokenBudget 实例
        """
        budget = TokenBudget(
            total_budget=total,
            soft_threshold=soft_threshold,
            hard_threshold=hard_threshold,
        )

        if task_id is None:
            import uuid
            task_id = f"budget_{uuid.uuid4().hex[:12]}"

        with self._lock:
            self._budgets[task_id] = budget

        logger.debug(
            "创建预算: task_id=%s, total=%d, soft=%.2f, hard=%.2f",
            task_id, total, soft_threshold, hard_threshold,
        )
        return budget

    def get_budget(self, task_id: str) -> Optional[TokenBudget]:
        """获取指定 task_id 的预算（不存在则返回 None）"""
        with self._lock:
            return self._budgets.get(task_id)

    def release_budget(self, task_id: str) -> None:
        """释放指定 task_id 的预算"""
        with self._lock:
            self._budgets.pop(task_id, None)

    # ========================================================================
    # 公共方法 - 三阶段校验
    # ========================================================================

    def pre_execute_check(
        self,
        budget: TokenBudget,
        estimated_tokens: int,
        mode: Optional[BudgetEnforcementMode] = None,
    ) -> BudgetDecision:
        """
        预检：任务启动前检查预估 token 消耗

        决策规则：
        - estimated_tokens > total * 1.0 → REJECT（任务过大，不启动）
        - estimated_tokens > total * 0.8 → SOFT warning + 建议降级
        - 否则 → CONTINUE

        Args:
            budget: Token 预算
            estimated_tokens: 预估 token 消耗
            mode: 执行模式（None 则用 default_mode）

        Returns:
            BudgetDecision 决策结果

        Raises:
            InvalidBudgetError: 预算或预估参数非法
        """
        if estimated_tokens <= 0:
            raise InvalidBudgetError(
                f"estimated_tokens 必须为正整数：{estimated_tokens}"
            )

        effective_mode = mode or self._default_mode
        warnings: List[str] = []

        # 规则 1：estimated_tokens 超过总预算 → REJECT
        if estimated_tokens > budget.total_budget * PRE_EXECUTE_REJECT_RATIO:
            decision = BudgetDecision(
                allow_continue=False,
                enforcement=effective_mode,
                recommendation=BudgetRecommendation.SPLIT_TASK,
                remaining=budget.remaining,
                consumption_ratio=budget.consumption_ratio,
                warnings=[
                    f"预估消耗 {estimated_tokens} tokens 超过总预算 {budget.total_budget}",
                    "建议：拆分任务或扩大预算",
                ],
                stage="pre_execute",
            )
            self._record_decision(decision, "rejected_over_budget")
            logger.warning(
                "预检拒绝：estimated=%d, budget=%d",
                estimated_tokens, budget.total_budget,
            )
            return decision

        # 规则 2：estimated_tokens 达到软阈值 → SOFT warning
        if estimated_tokens > budget.total_budget * PRE_EXECUTE_SOFT_RATIO:
            warnings.append(
                f"预估消耗 {estimated_tokens} tokens 达到软阈值 "
                f"({PRE_EXECUTE_SOFT_RATIO:.0%} of {budget.total_budget})"
            )
            decision = BudgetDecision(
                allow_continue=True,
                enforcement=BudgetEnforcementMode.SOFT,
                recommendation=BudgetRecommendation.SWITCH_TO_HAIKU,
                remaining=budget.remaining,
                consumption_ratio=estimated_tokens / budget.total_budget,
                warnings=warnings,
                stage="pre_execute",
            )
            self._record_decision(decision, "soft_warning_estimated")
            logger.info(
                "预检软警告：estimated=%d, budget=%d, 建议切换 haiku",
                estimated_tokens, budget.total_budget,
            )
            return decision

        # 规则 3：正常
        decision = BudgetDecision(
            allow_continue=True,
            enforcement=effective_mode,
            recommendation=BudgetRecommendation.CONTINUE,
            remaining=budget.remaining,
            consumption_ratio=budget.consumption_ratio,
            warnings=warnings,
            stage="pre_execute",
        )
        self._record_decision(decision, "approved")
        return decision

    def record_consumption(
        self,
        budget: TokenBudget,
        consumed: int,
        mode: Optional[BudgetEnforcementMode] = None,
    ) -> BudgetDecision:
        """
        记录消费：执行过程中报告已消耗的 token 数

        决策规则（HARD/SOFT/HYBRID 三模式）：
        - HARD：达到 hard_threshold → 抛 TokenBudgetExceeded
        - SOFT：达到 soft_threshold → 警告 + 建议切换 haiku 继续
        - HYBRID：达到 hard_threshold → 抛异常；达到 soft_threshold → 警告

        Args:
            budget: Token 预算
            consumed: 本次消费 token 数
            mode: 执行模式（None 则用 default_mode）

        Returns:
            BudgetDecision 决策结果

        Raises:
            TokenBudgetExceeded: HARD 模式且达到硬阈值
            InvalidBudgetError: 消费参数非法
        """
        if consumed <= 0:
            raise InvalidBudgetError(
                f"consumed 必须为正整数：{consumed}"
            )

        effective_mode = mode or self._default_mode
        warnings: List[str] = []

        # 累加消费
        new_consumed = budget.consumed + consumed
        budget.consumed = new_consumed

        new_ratio = budget.consumption_ratio

        # 规则 1：达到硬阈值
        if budget.is_hard_exceeded:
            if effective_mode in (BudgetEnforcementMode.HARD, BudgetEnforcementMode.HYBRID):
                decision = BudgetDecision(
                    allow_continue=False,
                    enforcement=BudgetEnforcementMode.HARD,
                    recommendation=BudgetRecommendation.ABORT,
                    remaining=0,
                    consumption_ratio=new_ratio,
                    warnings=[
                        f"Token 消耗达到硬阈值 {budget.hard_threshold:.0%} "
                        f"({new_consumed}/{budget.total_budget})"
                    ],
                    stage="during_execute",
                )
                self._record_decision(decision, "hard_exceeded")
                logger.error(
                    "Token 硬超限：consumed=%d, budget=%d, ratio=%.1f%%",
                    new_consumed, budget.total_budget, new_ratio * 100,
                )
                raise TokenBudgetExceeded(
                    consumed=new_consumed,
                    budget=budget.total_budget,
                )

        # 规则 2：达到软阈值（且未达硬阈值）
        if budget.is_soft_exceeded:
            warnings.append(
                f"Token 消耗达到软阈值 {budget.soft_threshold:.0%} "
                f"({new_consumed}/{budget.total_budget})，建议切换 haiku 节省成本"
            )
            decision = BudgetDecision(
                allow_continue=True,
                enforcement=BudgetEnforcementMode.SOFT,
                recommendation=BudgetRecommendation.SWITCH_TO_HAIKU,
                remaining=budget.remaining,
                consumption_ratio=new_ratio,
                warnings=warnings,
                stage="during_execute",
            )
            self._record_decision(decision, "soft_exceeded")
            logger.warning(
                "Token 软超限：consumed=%d, budget=%d, ratio=%.1f%%, 建议切换 haiku",
                new_consumed, budget.total_budget, new_ratio * 100,
            )
            return decision

        # 规则 3：正常
        decision = BudgetDecision(
            allow_continue=True,
            enforcement=effective_mode,
            recommendation=BudgetRecommendation.CONTINUE,
            remaining=budget.remaining,
            consumption_ratio=new_ratio,
            warnings=warnings,
            stage="during_execute",
        )
        self._record_decision(decision, "consumed_normal")
        return decision

    def post_execute_review(
        self,
        budget: TokenBudget,
        success: bool,
        task_type: str = "general",
    ) -> None:
        """
        后审：任务完成后写入画像（用于反哺）

        Args:
            budget: Token 预算
            success: 任务是否成功
            task_type: 任务类型（用于画像分类）
        """
        if self._fingerprint is None:
            logger.debug("PerformanceFingerprint 未启用，跳过后审反哺")
            return

        try:
            self._fingerprint.record(
                task_type=task_type,
                task_complexity=self._estimate_complexity(budget),
                success=success,
                error_type=None if success else "budget_exhausted" if budget.is_hard_exceeded else None,
                execution_time=0.0,  # Token 视角不记录执行时间
                strategy="token_budget_guard",
                context_features={
                    "total_budget": budget.total_budget,
                    "consumed": budget.consumed,
                    "consumption_ratio": budget.consumption_ratio,
                    "soft_threshold": budget.soft_threshold,
                    "hard_threshold": budget.hard_threshold,
                    "is_soft_exceeded": budget.is_soft_exceeded,
                    "is_hard_exceeded": budget.is_hard_exceeded,
                },
            )
        except Exception as e:
            logger.error("写入后审到画像失败: %s", e)

        decision = BudgetDecision(
            allow_continue=True,
            enforcement=self._default_mode,
            recommendation=BudgetRecommendation.CONTINUE,
            remaining=budget.remaining,
            consumption_ratio=budget.consumption_ratio,
            warnings=[],
            stage="post_execute",
        )
        self._record_decision(decision, f"post_review_success={success}")

    # ========================================================================
    # 公共方法 - GuardCoordinator 兼容接口
    # ========================================================================

    def validate(self, task: Dict[str, Any]) -> ValidationResult:
        """
        验证任务（与 GuardCoordinator.validate 接口对齐）

        任务字段约定：
        - token_budget: int 任务总预算（必填）
        - estimated_tokens: int 预估消耗（可选）
        - task_id: str 任务 ID（可选，用于追踪）

        Args:
            task: 任务字典

        Returns:
            ValidationResult（与 GuardCoordinator 兼容）
        """
        token_budget = task.get("token_budget")
        if token_budget is None or not isinstance(token_budget, int) or token_budget <= 0:
            return ValidationResult(
                passed=False,
                risk_level=RiskLevel.HIGH,
                warnings=[
                    ValidationWarning(
                        warning_code="budget_missing",
                        warning_type="missing_field",
                        message="任务缺少有效 token_budget 字段",
                        severity="error",
                        recommended_action="添加 token_budget 字段",
                    )
                ],
                recommended_compensations=[],
                alternative_strategies=[],
                validation_details={"reason": "budget_missing"},
            )

        budget = self.create_budget(total=token_budget, task_id=task.get("task_id"))
        estimated = task.get("estimated_tokens", token_budget // 2)

        decision = self.pre_execute_check(budget, estimated)

        # 转换为 ValidationResult
        warnings = []
        for msg in decision.warnings:
            warnings.append(
                ValidationWarning(
                    warning_code=f"budget_{decision.recommendation.value}",
                    warning_type="budget_warning",
                    message=msg,
                    severity="warning" if decision.allow_continue else "error",
                    recommended_action=decision.recommendation.value,
                )
            )

        compensations = []
        if decision.recommendation == BudgetRecommendation.SWITCH_TO_HAIKU:
            compensations.append(
                CompensationStrategy(
                    strategy_id="budget_switch_haiku",
                    error_type="budget_warning",
                    strategy_type="feedforward",
                    actions=["切换到 haiku 模型", "减少 token 消耗"],
                    priority=3,
                    confidence=0.8,
                )
            )
        elif decision.recommendation == BudgetRecommendation.SPLIT_TASK:
            compensations.append(
                CompensationStrategy(
                    strategy_id="budget_split_task",
                    error_type="budget_exceeded",
                    strategy_type="feedforward",
                    actions=["拆分任务为多个子任务", "分批执行"],
                    priority=4,
                    confidence=0.9,
                )
            )

        # 释放预算（validate 是一次性检查）
        self.release_budget(task.get("task_id", budget.total_budget))

        return ValidationResult(
            passed=decision.allow_continue,
            risk_level=RiskLevel.LOW if decision.allow_continue else RiskLevel.HIGH,
            warnings=warnings,
            recommended_compensations=compensations,
            alternative_strategies=[decision.recommendation.value],
            validation_details={
                "budget": budget.to_dict(),
                "decision": decision.to_dict(),
            },
        )

    # ========================================================================
    # 公共方法 - 历史
    # ========================================================================

    def get_decision_history(self) -> List[Dict[str, Any]]:
        """
        获取内存中的决策历史（最近 500 条）

        Returns:
            决策历史列表
        """
        with self._lock:
            return list(self._decision_history)

    # ========================================================================
    # 内部方法
    # ========================================================================

    def _record_decision(
        self,
        decision: BudgetDecision,
        context: str = "",
    ) -> None:
        """记录决策到内存历史"""
        with self._lock:
            self._decision_history.append({
                "decision": decision.to_dict(),
                "context": context,
                "timestamp": time.time(),
            })
            # 限制历史大小
            if len(self._decision_history) > 500:
                self._decision_history = self._decision_history[-500:]

    def _estimate_complexity(self, budget: TokenBudget) -> int:
        """
        根据预算消费情况估算任务复杂度（1-10）

        - 消费 < 20% → 复杂度 1-2（简单）
        - 消费 20-50% → 复杂度 3-5（中等）
        - 消费 50-80% → 复杂度 6-8（高）
        - 消费 > 80% → 复杂度 9-10（极高）
        """
        ratio = budget.consumption_ratio
        if ratio < 0.2:
            return 2
        elif ratio < 0.5:
            return 4
        elif ratio < 0.8:
            return 7
        else:
            return 9


# ============================================================================
# 模块导出
# ============================================================================

__all__ = [
    # 异常
    "TokenBudgetGuardError",
    "TokenBudgetExceeded",
    "InvalidBudgetError",
    # 枚举
    "BudgetEnforcementMode",
    "BudgetRecommendation",
    # 数据类
    "TokenBudget",
    "BudgetDecision",
    # 常量
    "DEFAULT_SOFT_THRESHOLD",
    "DEFAULT_HARD_THRESHOLD",
    "PRE_EXECUTE_REJECT_RATIO",
    "PRE_EXECUTE_SOFT_RATIO",
    # 主类
    "TokenBudgetGuard",
]
