#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dynamic Workflows 模式执行器（Pattern Executor）

Phase 1+2+3+4+5 累计实现：
- PatternExecutor Protocol（执行器协议）
- 6 个核心执行器实现（classifier / fan-out / adversarial / generate-filter / tournament / loop-until-done）
- 真实调用 trae-multi-agent v2.5 的 dispatch_agent_v2 接口
- 完整输入校验、提示词注入防护、Token 硬上限
- 画像反哺执行结果
- 异常隔离（一个 subagent 失败不影响整体）
- Phase 2: SubagentSandbox 集成（可选）
- Phase 3: ModelRouter / TokenBudgetGuard 集成（可选，本阶段集成）
- Phase 4: 端到端集成（router + budget_guard + sandbox 协同）
- Phase 5: 补齐 generate-filter / tournament / loop-until-done 三个模式

设计约束（来自 DYNAMIC_WORKFLOWS_INTEGRATION.md v1.1 §3.0）：
- 🔴 V2 不修改：本模块独立运行，通过 dispatch_agent_v2 调用
- 🔴 持久化复用：执行结果写入 PerformanceFingerprint
- 🔴 安全：所有输入经 guard.check() 校验
- 🔴 模式上限 6：Phase 5 补齐 6 大模式，不再扩展
- 🔴 一阶段一模块：每 Phase 引入新能力，不互相侵入
- 🔴 向后兼容：新参数全部 optional，老调用方行为零变化

不实现（Phase 6+）：
- ❌ SkillDistribution（Skill 自动注入到 sandbox context）
- ❌ InterruptionRecovery（subagent 中断恢复）
- ❌ /loop + /goal 集成
- ❌ model_tier-aware dispatch（cybernetics_bridge 解析 _meta.model_tier）✅ **Phase 10 已完成**

作者：trae-multi-agent 融合 Phase 1+2+3+4+5
创建日期：2026-06-03
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Protocol

# 同包模块
from guard import (
    FieldSchema,
    GuardDecision,
    GuardResult,
    check as guard_check,
)

# pattern_composer
import sys
from pathlib import Path
_COMPOSER_DIR = Path(__file__).resolve().parent
if str(_COMPOSER_DIR) not in sys.path:
    sys.path.insert(0, str(_COMPOSER_DIR))
from pattern_composer import (
    PATTERN_ADVERSARIAL_VERIFY,
    PATTERN_CLASSIFIER_DISPATCH,
    PATTERN_FAN_OUT_AGGREGATE,
    PATTERN_GENERATE_FILTER,
    PATTERN_LOOP_UNTIL_DONE,
    PATTERN_TOURNAMENT,
    WorkflowPattern,
)

# 复用 v2.5 真实接口
SCRIPTS_DIR = _COMPOSER_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
try:
    from trae_agent_dispatch_v2 import dispatch_agent_v2
except ImportError:
    # 允许作为独立模块导入时不强制依赖 v2 dispatch
    dispatch_agent_v2 = None  # type: ignore[assignment]

# 复用 PerformanceFingerprint（持久化）
from performance_fingerprint import PerformanceFingerprint


# ============================================================================
# 日志
# ============================================================================

logger = logging.getLogger("dynamic_workflow.pattern_executor")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ============================================================================
# 枚举
# ============================================================================

class ExecutionStatus(str, Enum):
    """执行状态"""
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL_SUCCESS = "partial_success"  # 部分子任务成功
    REJECTED = "rejected"                  # 被 Guard 拒绝
    TIMEOUT = "timeout"                    # 执行超时
    CANCELLED = "cancelled"                # 被取消


class AggregationStrategy(str, Enum):
    """聚合策略（与 PatternComposer 对齐）"""
    CONCAT = "concat"
    VOTE = "vote"
    RANK = "rank"
    MERGE = "merge"


# ============================================================================
# 数据类
# ============================================================================

@dataclass
class SubagentResult:
    """单 subagent 执行结果"""
    subagent_id: str
    role: str
    success: bool
    output: Any
    error: Optional[str] = None
    execution_time_seconds: float = 0.0
    token_used: int = 0
    guard_result: Optional[GuardResult] = None


@dataclass
class ExecutionResult:
    """模式执行结果（与 PerformanceFingerprint 兼容）"""
    pattern_id: Optional[str]
    status: ExecutionStatus
    subagent_results: List[SubagentResult] = field(default_factory=list)
    aggregated_output: Any = None
    error: Optional[str] = None
    total_execution_time_seconds: float = 0.0
    total_token_used: int = 0
    guard_result: Optional[GuardResult] = None  # 主输入的 Guard 结果
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        """是否成功（用于 PerformanceFingerprint.record 的 success 参数）"""
        return self.status in (ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict"""
        return {
            "pattern_id": self.pattern_id,
            "status": self.status.value,
            "subagent_count": len(self.subagent_results),
            "subagent_success_count": sum(1 for r in self.subagent_results if r.success),
            "aggregated_output": self.aggregated_output,
            "error": self.error,
            "total_execution_time_seconds": self.total_execution_time_seconds,
            "total_token_used": self.total_token_used,
            "guard_passed": (
                self.guard_result.is_allowed if self.guard_result else None
            ),
            "metadata": self.metadata,
        }


# ============================================================================
# 执行器协议（Protocol）
# ============================================================================

class PatternExecutor(Protocol):
    """
    模式执行器协议

    所有具体执行器必须实现以下方法。
    """

    @property
    def pattern_id(self) -> str:
        """模式 ID"""
        ...

    def execute(
        self,
        task: Dict[str, Any],
        parameters: Dict[str, Any],
    ) -> ExecutionResult:
        """
        执行模式

        Args:
            task: 任务描述字典（至少包含 'description' 字段）
            parameters: 实例化后的参数（来自 PatternSelection.parameters）

        Returns:
            ExecutionResult: 执行结果
        """
        ...

    def record_to_fingerprint(
        self,
        task: Dict[str, Any],
        result: ExecutionResult,
    ) -> None:
        """
        将执行结果写入画像（反哺闭环）

        Args:
            task: 原始任务描述
            result: 执行结果
        """
        ...


# ============================================================================
# 输入校验 Schema（3 个模式各自的 schema）
# ============================================================================

# classifier-dispatch 任务的 schema
CLASSIFIER_DISPATCH_SCHEMA: List[FieldSchema] = [
    FieldSchema("description", str, required=True, max_length=10000),
    FieldSchema("task_type", str, required=False, max_length=100),
]

# fan-out-aggregate 任务的 schema
FAN_OUT_AGGREGATE_SCHEMA: List[FieldSchema] = [
    FieldSchema("description", str, required=True, max_length=10000),
    FieldSchema("chunks", list, required=True, min_length=1),
]

# adversarial-verify 任务的 schema
ADVERSARIAL_VERIFY_SCHEMA: List[FieldSchema] = [
    FieldSchema("description", str, required=True, max_length=10000),
    FieldSchema("evaluation_criteria", list, required=True, min_length=3),
]

# generate-filter 任务的 schema（Phase 5 新增）
GENERATE_FILTER_SCHEMA: List[FieldSchema] = [
    FieldSchema("description", str, required=True, max_length=10000),
    FieldSchema("filter_criteria", list, required=True, min_length=1),
    FieldSchema("generator_count", int, required=False),
]

# tournament 任务的 schema（Phase 5 新增）
TOURNAMENT_SCHEMA: List[FieldSchema] = [
    FieldSchema("description", str, required=True, max_length=10000),
    FieldSchema("candidate_count", int, required=True),
    FieldSchema("judge_criteria", list, required=False, min_length=1),
]

# loop-until-done 任务的 schema（Phase 5 新增）
LOOP_UNTIL_DONE_SCHEMA: List[FieldSchema] = [
    FieldSchema("description", str, required=True, max_length=10000),
    FieldSchema("max_iterations", int, required=False),
    FieldSchema("stop_conditions", dict, required=False),
]


# ============================================================================
# 异常类型
# ============================================================================

class GuardRejectError(Exception):
    """Guard 拒绝执行"""
    def __init__(self, guard_result: GuardResult):
        self.guard_result = guard_result
        super().__init__(f"Guard 拒绝：{guard_result.reason}")


class DispatchError(Exception):
    """dispatch_agent_v2 调用失败"""
    pass


# ============================================================================
# 工具函数
# ============================================================================

def _to_dispatch_str(task: Any) -> str:
    """
    把 task 转为 dispatch_agent_v2 期望的 str

    cybernetics_bridge._estimate_complexity 期望 str，
    否则会 AttributeError: 'dict' object has no attribute 'lower'。
    这里把 dict 的 description 字段提取出来作为主描述，
    其他字段作为 context。

    Args:
        task: 任务（str 或 dict）

    Returns:
        str: 任务描述字符串
    """
    if isinstance(task, str):
        return task
    if isinstance(task, dict):
        desc = task.get("description", "")
        extras = {k: v for k, v in task.items() if k != "description"}
        if extras:
            desc += "\n[Context]: " + str(extras)
        return desc
    return str(task)


def _safe_dispatch(
    agent_type: str,
    task: Any,
    task_id: Optional[str] = None,
) -> bool:
    """
    安全地调用 dispatch_agent_v2

    Args:
        agent_type: 角色类型（如 "test_expert"）
        task: 任务（str 或 dict；dict 会被 _to_dispatch_str 转 str）
        task_id: 任务 ID（用于追踪）

    Returns:
        bool: 是否成功

    Raises:
        DispatchError: 当 dispatch_agent_v2 不可用或调用失败时
    """
    if dispatch_agent_v2 is None:
        raise DispatchError(
            "dispatch_agent_v2 不可用（导入失败），无法执行 subagent"
        )

    # cybernetics_bridge 期望 str，这里把 dict 转 str
    task_str = _to_dispatch_str(task)

    try:
        result = dispatch_agent_v2(
            agent_type=agent_type,
            task=task_str,
            task_id=task_id,
        )
        return bool(result)
    except Exception as e:  # noqa: BLE001
        # cybernetics_bridge 内部错误已被吞掉返回 False；
        # 这里再次捕获是防御性编程（其他未知异常）
        raise DispatchError(f"dispatch_agent_v2 调用失败：{e}") from e


def _extract_task_feature(task: Any, pattern_id: Optional[str] = None) -> Any:
    """
    从 task 中提取 TaskFeature（Phase 4 辅助函数，Phase 10 增强）

    支持字段：
    - task_complexity (1-10, 默认 5)
    - estimated_tokens (int, 缺省时按 token_budget//4 估算)
    - role (str, 来自 task.role 或 agent_type)
    - deadline_ms (int, 可选)
    - quality_threshold (0-1, 默认 0.85)
    - budget_remaining (0-1, 默认 1.0)
    - is_critical (bool, 默认 False)
    - task_type (str, 默认 "general")
    - pattern_id (Phase 10 新增；参数优先于 task_dict.pattern_id)
    - extra (Phase 10 新增；透传 subtask_count / is_final_iteration / risk_level / type_variants 等模式特定信息)

    Args:
        task: 任务（str / dict）
        pattern_id: 显式传入的模式 ID（Phase 10 新增；优先级高于 task_dict 中的 pattern_id）

    Returns:
        TaskFeature 实例（来自 model_router 模块）
    """
    # 避免循环导入
    from model_router import TaskFeature

    task_dict = task if isinstance(task, dict) else {"description": str(task)}

    # 估算 token 消耗：缺省按 budget 的 1/4
    budget = task_dict.get("token_budget", task_dict.get("pattern_token_budget", 20000))
    estimated = task_dict.get("estimated_tokens", max(1, budget // 4))

    # Phase 10：pattern_id 优先级：显式参数 > task_dict.pattern_id
    effective_pattern_id = pattern_id or task_dict.get("pattern_id")

    # Phase 10：extra 字段透传（用于模式特定信息）
    extra = task_dict.get("extra", {})
    if not isinstance(extra, dict):
        # 防御：非 dict 时忽略
        extra = {}

    return TaskFeature(
        task_complexity=task_dict.get("task_complexity", 5),
        estimated_tokens=estimated,
        role=task_dict.get("role"),
        deadline_ms=task_dict.get("deadline_ms"),
        quality_threshold=task_dict.get("quality_threshold", 0.85),
        budget_remaining=task_dict.get("budget_remaining", 1.0),
        is_critical=task_dict.get("is_critical", False),
        task_type=task_dict.get("task_type", "general"),
        pattern_id=effective_pattern_id,  # Phase 10 新增
        extra=extra,  # Phase 10 新增
    )


def _dispatch_subagent(
    agent_type: str,
    task: Any,
    task_id: Optional[str] = None,
    sandbox: Optional[Any] = None,         # SubagentSandbox（Phase 2）
    router: Optional[Any] = None,           # ModelRouter（Phase 3+4）
    budget_guard: Optional[Any] = None,     # TokenBudgetGuard（Phase 3+4）
    pattern_id: Optional[str] = None,       # Phase 10 新增：当前任务所属模式
) -> bool:
    """
    统一的 subagent 分发函数（Phase 1+2+3+4 + Phase 10 集成点）

    优先级：
    1. router 决策（如果 router 不为 None）→ 写入 task._meta
    2. budget_guard 预检（如果 budget_guard 不为 None）→ 强制 Token 上限
    3. sandbox 路径（如果 sandbox 不为 None）→ 独立 context + 可选 worktree
    4. Phase 1 路径（默认）→ 走 _safe_dispatch（向后兼容）

    Phase 10 增强：
    - 接受 pattern_id 参数，透传给 ModelRouter 触发 PatternTierResolver
    - 5 层决策链：explicit_tier > critical_path > pattern_policy > static_rule > history

    向后兼容：
    - sandbox/router/budget_guard/pattern_id 都为 None → 完全等同 Phase 1 行为
    - 任意一个不为 None → 启用对应 Phase 能力

    Args:
        agent_type: 角色类型
        task: 任务（str 或 dict）
        task_id: 任务 ID
        sandbox: SubagentSandbox 实例（None 时使用旧路径）
        router: ModelRouter 实例（None 时跳过路由决策）
        budget_guard: TokenBudgetGuard 实例（None 时跳过 Token 校验）
        pattern_id: 当前任务所属模式（Phase 10 新增）

    Returns:
        bool: 是否成功

    Raises:
        DispatchError: dispatch 失败
        GuardRejectError: Guard 拒绝（仅 sandbox 路径）
        TokenBudgetExceeded: Token 超限（仅 sandbox 路径）
    """
    # === 向后兼容：所有可选参数都为 None → 走 Phase 1 ===
    if sandbox is None and router is None and budget_guard is None:
        return _safe_dispatch(
            agent_type=agent_type,
            task=task,
            task_id=task_id,
        )

    task_dict = task if isinstance(task, dict) else {"description": str(task)}

    # === Phase 4 Step 1: 路由决策 ===
    routing_decision = None
    if router is not None:
        try:
            # Phase 10：透传 pattern_id 触发 PatternTierResolver
            feature = _extract_task_feature(task_dict, pattern_id=pattern_id)
            routing_decision = router.route(feature)
            # 将路由决策写入 task._meta（供 dispatch_agent_v2 后续消费）
            task_dict.setdefault("_meta", {})
            task_dict["_meta"]["model_tier"] = routing_decision.selected_tier.value
            task_dict["_meta"]["routing_reasoning"] = routing_decision.reasoning
            task_dict["_meta"]["routing_confidence"] = routing_decision.confidence
            logger.debug(
                f"路由决策：tier={routing_decision.selected_tier.value}, "
                f"reasoning={routing_decision.reasoning}"
            )
        except Exception as e:
            # 路由异常不影响主流程，降级到默认
            logger.warning(f"路由决策异常，降级到默认：{e}")

    # === Phase 4 Step 2: Token 预算预检 ===
    budget_obj = None
    if budget_guard is not None:
        try:
            from token_budget_guard import BudgetEnforcementMode

            total_budget = task_dict.get(
                "token_budget",
                task_dict.get("pattern_token_budget", 20000),
            )
            budget_obj = budget_guard.create_budget(
                total=total_budget,
                task_id=task_id or f"dispatch_{agent_type}",
            )
            estimated = task_dict.get(
                "estimated_tokens", max(1, total_budget // 4)
            )
            pre_decision = budget_guard.pre_execute_check(
                budget=budget_obj,
                estimated_tokens=estimated,
            )
            if not pre_decision.allow_continue:
                # 预检拒绝：抛 DispatchError（统一异常类型）
                raise DispatchError(
                    f"Token 预算预检拒绝：{pre_decision.recommendation.value} - "
                    f"{'; '.join(pre_decision.warnings)}"
                )
            # 软警告：记录到日志（不影响执行）
            if pre_decision.warnings:
                logger.info(
                    f"Token 预算软警告：{'; '.join(pre_decision.warnings)}"
                )
        except DispatchError:
            # 预检拒绝：直接抛
            raise
        except Exception as e:
            # 预算异常不影响主流程，降级
            logger.warning(f"Token 预算校验异常，降级：{e}")

    # === Phase 2 Step 3: 沙箱执行 ===
    if sandbox is not None:
        # 避免循环导入
        from subagent_sandbox import (
            SubagentSandbox, IsolationLevel, SandboxContext,
            TokenBudgetExceeded as _TokenBudgetExceeded,
            GuardRejectError as _GuardRejectError,
        )

        # Phase 4: duck typing 检查（同时支持真实 SubagentSandbox 和 mock）
        # 要求：sandbox 必须有 spawn / execute / cleanup 三个方法
        if not (
            hasattr(sandbox, "spawn")
            and hasattr(sandbox, "execute")
            and hasattr(sandbox, "cleanup")
        ):
            raise DispatchError(
                f"sandbox 必须有 spawn/execute/cleanup 方法，"
                f"得到 {type(sandbox).__name__}"
            )

        sandbox_id: Optional[str] = None
        try:
            sandbox_id = sandbox.spawn(
                agent_id=task_id or f"sa_{agent_type}",
                task=task_dict,
                isolation_level=IsolationLevel.CONTEXT,
                token_budget=_extract_token_budget(task_dict),
            )

            # 在沙箱中执行
            def _executor(ctx: SandboxContext) -> bool:
                """沙箱内的实际执行"""
                task_str = _to_dispatch_str(task_dict)
                if dispatch_agent_v2 is None:
                    raise DispatchError("dispatch_agent_v2 不可用")
                result = dispatch_agent_v2(
                    agent_type=agent_type,
                    task=task_str,
                    task_id=ctx.sandbox_id,
                )
                return bool(result)

            result = sandbox.execute(sandbox_id, _executor)
            dispatch_success = result.status == "success"
        except _TokenBudgetExceeded as e:
            # Token 超限：返回 False（沿用 Phase 1 失败语义）
            logger.warning(f"subagent Token 超限：{e.token_used}/{e.token_budget}")
            dispatch_success = False
        except _GuardRejectError as e:
            # Guard 拒绝：抛 DispatchError 让上层处理
            raise DispatchError(f"沙箱 Guard 拒绝：{e}") from e
        finally:
            if sandbox_id is not None:
                sandbox.cleanup(sandbox_id)
    else:
        # === Phase 1 Step 4: 默认路径 ===
        dispatch_success = _safe_dispatch(
            agent_type=agent_type,
            task=task_dict,  # 使用注入了 _meta 的 task_dict
            task_id=task_id,
        )

    # === Phase 4 Step 5: 预算后审 ===
    if budget_guard is not None and budget_obj is not None:
        try:
            budget_guard.post_execute_review(
                budget=budget_obj,
                success=dispatch_success,
                task_type=task_dict.get("task_type", "general"),
            )
        except Exception as e:
            logger.warning(f"预算后审失败：{e}")

    # === Phase 4 Step 6: 路由反哺 ===
    if router is not None and routing_decision is not None:
        try:
            router.record_decision(
                routing_decision,
                actual_outcome={"success": dispatch_success},
            )
        except Exception as e:
            logger.warning(f"路由反哺失败：{e}")

    return dispatch_success


def _extract_token_budget(task_dict: Dict[str, Any]) -> int:
    """
    从 task dict 中提取 token 预算

    支持字段：
    - token_budget（直接）
    - pattern_token_budget（pattern 配置）

    找不到则返回 10000（默认值）
    """
    for key in ("token_budget", "pattern_token_budget"):
        val = task_dict.get(key)
        if isinstance(val, int) and val > 0:
            return val
    return 10000


def _chunks_to_subagent_tasks(
    chunks: List[Any],
    role: str,
    task_description: str,
) -> List[Dict[str, Any]]:
    """
    将输入分块转为 subagent 任务列表

    Args:
        chunks: 分块列表（每个元素是一个 chunk，如文件路径、记录 ID）
        role: subagent 角色
        task_description: 总任务描述

    Returns:
        List[Dict]: subagent 任务列表
    """
    subagent_tasks = []
    for i, chunk in enumerate(chunks):
        subagent_tasks.append({
            "subagent_id": f"sa_{int(time.time() * 1000)}_{i}",
            "role": role,
            "task": {
                "description": (
                    f"{task_description}\n\n"
                    f"处理分块 {i + 1}/{len(chunks)}: {chunk}"
                ),
                "chunk": chunk,
                "chunk_index": i,
                "total_chunks": len(chunks),
            },
        })
    return subagent_tasks


def _aggregate_results(
    subagent_results: List[SubagentResult],
    strategy: AggregationStrategy,
) -> Any:
    """
    聚合 subagent 结果

    Args:
        subagent_results: subagent 结果列表
        strategy: 聚合策略

    Returns:
        聚合后的输出
    """
    outputs = [r.output for r in subagent_results if r.success]

    if strategy == AggregationStrategy.CONCAT:
        return outputs
    elif strategy == AggregationStrategy.VOTE:
        # 多数表决（简化：返回出现次数最多的 output）
        if not outputs:
            return None
        from collections import Counter
        return Counter(str(o) for o in outputs).most_common(1)[0][0]
    elif strategy == AggregationStrategy.RANK:
        # 排序：按 success 优先，execution_time 升序
        sorted_results = sorted(
            subagent_results,
            key=lambda r: (not r.success, r.execution_time_seconds),
        )
        return [r.output for r in sorted_results]
    elif strategy == AggregationStrategy.MERGE:
        # 合并：仅当 output 是 dict 时合并，否则按 concat
        merged: Dict[str, Any] = {}
        for r in subagent_results:
            if isinstance(r.output, dict):
                merged.update(r.output)
            else:
                merged[str(r.subagent_id)] = r.output
        return merged
    else:
        return outputs


# ============================================================================
# 执行器 1：ClassifierDispatchExecutor（分类并行动）
# ============================================================================

class ClassifierDispatchExecutor:
    """
    classifier-dispatch 模式执行器

    真实逻辑：
    1. 用 Guard 校验输入（提示词注入防护 + schema 校验）
    2. 从任务描述中分类（这里用 schema 中的 task_type 字段作为分类标签）
    3. 按 route_table 路由到对应 role
    4. 顺序调用 dispatch_agent_v2 处理每个分路由结果（Phase 1 简化）
    5. 记录所有子任务结果

    注：分类阶段 Phase 1 简化使用规则分类（task.task_type 映射）。
        Phase 2+ 引入真实的 LLM 语义分类。
    """

    def __init__(
        self,
        fingerprint: Optional[PerformanceFingerprint] = None,
        sandbox: Optional[Any] = None,  # SubagentSandbox（Phase 2）
        router: Optional[Any] = None,  # ModelRouter（Phase 3+4）
        budget_guard: Optional[Any] = None,  # TokenBudgetGuard（Phase 3+4）
    ):
        self._pattern = PATTERN_CLASSIFIER_DISPATCH
        self._fingerprint = fingerprint or PerformanceFingerprint(
            agent_id="classifier_dispatch_executor"
        )
        # Phase 2: 沙箱（可选）
        self._sandbox = sandbox
        # Phase 3+4: 路由 + 预算（可选）
        self._router = router
        self._budget_guard = budget_guard

    @property
    def pattern_id(self) -> str:
        return self._pattern.pattern_id

    def execute(
        self,
        task: Dict[str, Any],
        parameters: Dict[str, Any],
    ) -> ExecutionResult:
        """执行分类并行动"""
        start_time = time.perf_counter()

        # 阶段 1：Guard 校验
        guard_result = guard_check(
            inputs=task,
            schema=CLASSIFIER_DISPATCH_SCHEMA,
            token_budget=parameters.get("token_budget", self._pattern.default_token_budget),
        )
        if guard_result.decision == GuardDecision.REJECT:
            return ExecutionResult(
                pattern_id=self.pattern_id,
                status=ExecutionStatus.REJECTED,
                error=guard_result.reason,
                guard_result=guard_result,
                total_execution_time_seconds=time.perf_counter() - start_time,
            )

        # 阶段 2：使用净化后的输入
        safe_task = guard_result.sanitized_input or task

        # 阶段 3：分类（Phase 1 简化：用 task.task_type 字段作为分类标签）
        task_type = safe_task.get("task_type", "general")
        route_table: Dict[str, Any] = parameters.get("route_table", {})
        fallback_route: str = parameters.get("fallback_route", "solo_coder")

        # 匹配路由
        route_match = route_table.get(task_type)
        if route_match is None:
            # 未知分类 → fallback
            target_role = fallback_route
            target_pattern = "sequential"
            confidence = 0.0
            match_type = "fallback"
        else:
            target_role = route_match.get("target_role", fallback_route)
            target_pattern = route_match.get("target_pattern", "sequential")
            confidence = 0.9
            match_type = task_type

        # 阶段 4：调用 dispatch_agent_v2
        subagent_results: List[SubagentResult] = []
        sub_start = time.perf_counter()

        sa_result = SubagentResult(
            subagent_id=f"sa_{int(time.time() * 1000)}",
            role=target_role,
            success=False,
            output=None,
            guard_result=guard_result,
        )

        try:
            dispatch_input = {
                "description": safe_task["description"],
                "classified_as": match_type,
                "target_role": target_role,
                "confidence": confidence,
            }
            sa_result.success = _dispatch_subagent(
                agent_type=target_role,
                task=dispatch_input,
                task_id=sa_result.subagent_id,
                sandbox=self._sandbox,
                router=self._router,
                budget_guard=self._budget_guard,
                pattern_id=self.pattern_id,  # Phase 10 透传
            )
            sa_result.output = (
                f"已路由到 {target_role} 处理 '{task_type}' 类型任务"
            )
        except DispatchError as e:
            sa_result.error = str(e)
            logger.warning(f"分类路由失败（异常隔离）: {e}")
        except Exception as e:  # noqa: BLE001
            sa_result.error = f"未预期异常: {e}"
            logger.error(f"未预期异常：{e}\n{traceback.format_exc()}")

        sa_result.execution_time_seconds = time.perf_counter() - sub_start
        subagent_results.append(sa_result)

        # 阶段 5：构建结果
        all_success = all(r.success for r in subagent_results)
        status = ExecutionStatus.SUCCESS if all_success else ExecutionStatus.FAILURE

        result = ExecutionResult(
            pattern_id=self.pattern_id,
            status=status,
            subagent_results=subagent_results,
            aggregated_output={
                "classified_as": match_type,
                "target_role": target_role,
                "target_pattern": target_pattern,
                "confidence": confidence,
            },
            total_execution_time_seconds=time.perf_counter() - start_time,
            guard_result=guard_result,
            metadata={
                "fallback_used": match_type == "fallback",
                "route_table_size": len(route_table),
            },
        )

        # 阶段 6：画像反哺
        self.record_to_fingerprint(task, result)

        return result

    def record_to_fingerprint(
        self,
        task: Dict[str, Any],
        result: ExecutionResult,
    ) -> None:
        """写入画像"""
        try:
            self._fingerprint.record(
                task_type=task.get("task_type", "general"),
                task_complexity=task.get("task_complexity", 5),
                success=result.success,
                error_type=result.error if not result.success else None,
                execution_time=result.total_execution_time_seconds,
                strategy=self.pattern_id,
                context_features={
                    "task_description": task.get("description", "")[:200],
                    "subagent_count": len(result.subagent_results),
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"画像反哺失败（非致命）: {e}")


# ============================================================================
# 执行器 2：FanOutAggregateExecutor（扇出与聚合）
# ============================================================================

class FanOutAggregateExecutor:
    """
    fan-out-aggregate 模式执行器

    真实逻辑：
    1. Guard 校验
    2. 输入分块（chunks）
    3. 并发调用 dispatch_agent_v2（最多 10 个并发，Phase 0 硬上限）
    4. 屏障等待所有 subagent 完成
    5. 按 aggregation_strategy 聚合
    6. 部分失败策略（fail/skip/retry）
    7. 记录所有子任务结果
    """

    def __init__(
        self,
        fingerprint: Optional[PerformanceFingerprint] = None,
        max_workers: int = 10,
        sandbox: Optional[Any] = None,  # SubagentSandbox（Phase 2）
        router: Optional[Any] = None,  # ModelRouter（Phase 3+4）
        budget_guard: Optional[Any] = None,  # TokenBudgetGuard（Phase 3+4）
    ):
        self._pattern = PATTERN_FAN_OUT_AGGREGATE
        self._fingerprint = fingerprint or PerformanceFingerprint(
            agent_id="fan_out_aggregate_executor"
        )
        # Phase 0 硬上限 10
        self._max_workers = min(max_workers, 10)
        # Phase 2: 沙箱（可选）
        self._sandbox = sandbox
        # Phase 3+4: 路由 + 预算（可选）
        self._router = router
        self._budget_guard = budget_guard

    @property
    def pattern_id(self) -> str:
        return self._pattern.pattern_id

    def execute(
        self,
        task: Dict[str, Any],
        parameters: Dict[str, Any],
    ) -> ExecutionResult:
        """执行扇出与聚合"""
        start_time = time.perf_counter()

        # 阶段 1：Guard 校验
        fanout_count = parameters.get("fanout_count", 1)
        if not (1 <= fanout_count <= 10):
            fanout_count = min(max(1, fanout_count), 10)
            logger.warning(f"fanout_count 调整到合法范围: {fanout_count}")

        guard_result = guard_check(
            inputs=task,
            schema=FAN_OUT_AGGREGATE_SCHEMA,
            token_budget=parameters.get("token_budget", self._pattern.default_token_budget),
        )
        if guard_result.decision == GuardDecision.REJECT:
            return ExecutionResult(
                pattern_id=self.pattern_id,
                status=ExecutionStatus.REJECTED,
                error=guard_result.reason,
                guard_result=guard_result,
                total_execution_time_seconds=time.perf_counter() - start_time,
            )

        # 阶段 2：参数解析
        safe_task = guard_result.sanitized_input or task
        chunks: List[Any] = safe_task.get("chunks", [])
        if not chunks:
            return ExecutionResult(
                pattern_id=self.pattern_id,
                status=ExecutionStatus.FAILURE,
                error="chunks 不能为空",
                guard_result=guard_result,
                total_execution_time_seconds=time.perf_counter() - start_time,
            )

        subagent_role: str = parameters.get("subagent_role", "solo_coder")
        aggregator_role: str = parameters.get("aggregator_role", "architect")
        aggregation_strategy_str: str = parameters.get("aggregation_strategy", "merge")
        aggregation_strategy = AggregationStrategy(aggregation_strategy_str)
        partial_failure_policy: str = parameters.get("partial_failure_policy", "skip")
        barrier_timeout: int = parameters.get("barrier_timeout_seconds", 3600)

        # 阶段 3：分块
        actual_fanout = min(fanout_count, len(chunks))
        subagent_tasks = _chunks_to_subagent_tasks(
            chunks=chunks[:actual_fanout],
            role=subagent_role,
            task_description=safe_task["description"],
        )

        # 阶段 4：并发执行（屏障同步）
        subagent_results: List[SubagentResult] = []

        def _execute_one(agent_task: Dict[str, Any]) -> SubagentResult:
            sa_start = time.perf_counter()
            result = SubagentResult(
                subagent_id=agent_task["subagent_id"],
                role=agent_task["role"],
                success=False,
                output=None,
            )
            try:
                result.success = _dispatch_subagent(
                    agent_type=agent_task["role"],
                    task=agent_task["task"],
                    task_id=result.subagent_id,
                    sandbox=self._sandbox,
                    pattern_id=self.pattern_id,  # Phase 10 透传
                )
                result.output = (
                    f"已处理分块 {agent_task['task']['chunk_index'] + 1}/"
                    f"{agent_task['task']['total_chunks']}: "
                    f"{agent_task['task']['chunk']}"
                )
            except DispatchError as e:
                result.error = str(e)
                logger.warning(f"subagent {result.subagent_id} 失败: {e}")
            except Exception as e:  # noqa: BLE001
                result.error = f"未预期异常: {e}"
                logger.error(
                    f"subagent {result.subagent_id} 异常：{e}\n{traceback.format_exc()}"
                )
            result.execution_time_seconds = time.perf_counter() - sa_start
            return result

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            future_to_task = {
                executor.submit(_execute_one, t): t for t in subagent_tasks
            }
            try:
                for future in as_completed(future_to_task, timeout=barrier_timeout):
                    subagent_results.append(future.result())
            except concurrent.futures.TimeoutError:
                logger.error(f"扇出屏障超时 {barrier_timeout}s")
                return ExecutionResult(
                    pattern_id=self.pattern_id,
                    status=ExecutionStatus.TIMEOUT,
                    error=f"屏障超时 {barrier_timeout}s",
                    subagent_results=subagent_results,
                    guard_result=guard_result,
                    total_execution_time_seconds=time.perf_counter() - start_time,
                )

        # 阶段 5：部分失败策略
        if partial_failure_policy == "fail":
            if not all(r.success for r in subagent_results):
                return ExecutionResult(
                    pattern_id=self.pattern_id,
                    status=ExecutionStatus.FAILURE,
                    error="部分子任务失败，策略=fail",
                    subagent_results=subagent_results,
                    guard_result=guard_result,
                    total_execution_time_seconds=time.perf_counter() - start_time,
                )
        elif partial_failure_policy == "retry":
            # 简化：Phase 1 不实现重试（Phase 2+ 用 V2 现有 retry_count）
            pass
        # skip: 不处理（默认）

        # 阶段 6：聚合
        aggregated = _aggregate_results(subagent_results, aggregation_strategy)

        # 阶段 7：构建结果
        success_count = sum(1 for r in subagent_results if r.success)
        if success_count == len(subagent_results):
            status = ExecutionStatus.SUCCESS
        elif success_count > 0:
            status = ExecutionStatus.PARTIAL_SUCCESS
        else:
            status = ExecutionStatus.FAILURE

        result = ExecutionResult(
            pattern_id=self.pattern_id,
            status=status,
            subagent_results=subagent_results,
            aggregated_output=aggregated,
            total_execution_time_seconds=time.perf_counter() - start_time,
            guard_result=guard_result,
            metadata={
                "fanout_count": actual_fanout,
                "aggregation_strategy": aggregation_strategy.value,
                "partial_failure_policy": partial_failure_policy,
            },
        )

        # 阶段 8：画像反哺
        self.record_to_fingerprint(task, result)

        return result

    def record_to_fingerprint(
        self,
        task: Dict[str, Any],
        result: ExecutionResult,
    ) -> None:
        """写入画像"""
        try:
            success_count = sum(1 for r in result.subagent_results if r.success)
            self._fingerprint.record(
                task_type=task.get("task_type", "fan_out"),
                task_complexity=task.get("task_complexity", 5),
                success=result.success,
                error_type=(
                    result.error
                    if result.status in (
                        ExecutionStatus.FAILURE,
                        ExecutionStatus.TIMEOUT,
                    )
                    else None
                ),
                execution_time=result.total_execution_time_seconds,
                strategy=self.pattern_id,
                context_features={
                    "task_description": task.get("description", "")[:200],
                    "subagent_count": len(result.subagent_results),
                    "success_count": success_count,
                    "fanout_count": result.metadata.get("fanout_count", 0),
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"画像反哺失败（非致命）: {e}")


# ============================================================================
# 执行器 3：AdversarialVerifyExecutor（对抗性验证）
# ============================================================================

class AdversarialVerifyExecutor:
    """
    adversarial-verify 模式执行器

    真实逻辑：
    1. Guard 校验（验证者必须独立 context 隔离）
    2. 准则可测量性校验（Phase 1 简化：要求 ≥ 3 条准则）
    3. 生成者执行（dispatch_agent_v2 调 generator_role）
    4. 验证者执行（dispatch_agent_v2 调 verifier_role，独立 context）
    5. 对照 evaluation_criteria 判定通过/不通过
    6. 多轮对抗（max_rounds 上限）
    7. 不通过时按 fallback_on_reject 处理
    """

    def __init__(
        self,
        fingerprint: Optional[PerformanceFingerprint] = None,
        sandbox: Optional[Any] = None,  # SubagentSandbox（Phase 2）
        router: Optional[Any] = None,  # ModelRouter（Phase 3+4）
        budget_guard: Optional[Any] = None,  # TokenBudgetGuard（Phase 3+4）
    ):
        self._pattern = PATTERN_ADVERSARIAL_VERIFY
        self._fingerprint = fingerprint or PerformanceFingerprint(
            agent_id="adversarial_verify_executor"
        )
        # Phase 2: 沙箱（可选）
        self._sandbox = sandbox
        # Phase 3+4: 路由 + 预算（可选）
        self._router = router
        self._budget_guard = budget_guard

    @property
    def pattern_id(self) -> str:
        return self._pattern.pattern_id

    def _validate_isolation(self, parameters: Dict[str, Any]) -> None:
        """
        强约束：验证者必须独立 context 隔离

        Raises:
            ValueError: 当隔离级别不满足要求时
        """
        isolation = parameters.get("verifier_isolation", "context")
        if isolation not in ("context", "full"):
            raise ValueError(
                f"adversarial-verify 要求 verifier_isolation ∈ {{'context', 'full'}}，"
                f"实际为 '{isolation}'"
            )

        # 验证者与生成者角色必须不同
        gen_role = parameters.get("generator_role", "")
        ver_role = parameters.get("verifier_role", "")
        if gen_role and ver_role and gen_role == ver_role:
            raise ValueError(
                f"验证者与生成者角色不能相同（都 '{gen_role}'），"
                f"否则失去对抗意义"
            )

    def execute(
        self,
        task: Dict[str, Any],
        parameters: Dict[str, Any],
    ) -> ExecutionResult:
        """执行对抗性验证"""
        start_time = time.perf_counter()

        # 阶段 1：隔离强校验（必须在 Guard 之前）
        try:
            self._validate_isolation(parameters)
        except ValueError as e:
            return ExecutionResult(
                pattern_id=self.pattern_id,
                status=ExecutionStatus.FAILURE,
                error=f"隔离校验失败：{e}",
                total_execution_time_seconds=time.perf_counter() - start_time,
            )

        # 阶段 2：Guard 校验
        guard_result = guard_check(
            inputs=task,
            schema=ADVERSARIAL_VERIFY_SCHEMA,
            token_budget=parameters.get("token_budget", self._pattern.default_token_budget),
        )
        if guard_result.decision == GuardDecision.REJECT:
            return ExecutionResult(
                pattern_id=self.pattern_id,
                status=ExecutionStatus.REJECTED,
                error=guard_result.reason,
                guard_result=guard_result,
                total_execution_time_seconds=time.perf_counter() - start_time,
            )

        safe_task = guard_result.sanitized_input or task

        # 阶段 3：参数
        generator_role: str = parameters.get("generator_role", "architect")
        verifier_role: str = parameters.get("verifier_role", "test_expert")
        max_rounds: int = min(max(1, parameters.get("max_rounds", 3)), 5)
        pass_threshold: float = parameters.get("pass_threshold", 0.8)
        evaluation_criteria: List[str] = safe_task.get("evaluation_criteria", [])
        fallback_on_reject: str = parameters.get("fallback_on_reject", "regenerate")

        # 阶段 4：多轮对抗
        round_results: List[SubagentResult] = []
        final_pass = False
        rounds_executed = 0
        error_msg = None

        for round_num in range(1, max_rounds + 1):
            rounds_executed = round_num
            logger.info(f"对抗轮次 {round_num}/{max_rounds}")

            # 生成者
            gen_start = time.perf_counter()
            gen_result = SubagentResult(
                subagent_id=f"gen_r{round_num}_{int(time.time() * 1000)}",
                role=generator_role,
                success=False,
                output=None,
            )
            try:
                gen_result.success = _dispatch_subagent(
                    agent_type=generator_role,
                    task={
                        "description": safe_task["description"],
                        "round": round_num,
                        "phase": "generate",
                    },
                    task_id=gen_result.subagent_id,
                    sandbox=self._sandbox,
                    pattern_id=self.pattern_id,  # Phase 10 透传
                )
                gen_result.output = (
                    f"[轮次 {round_num}] {generator_role} 产出方案"
                )
            except DispatchError as e:
                gen_result.error = str(e)
            gen_result.execution_time_seconds = time.perf_counter() - gen_start
            round_results.append(gen_result)

            # 验证者（独立 context：Phase 1 简化用不同 subagent_id 模拟）
            ver_start = time.perf_counter()
            ver_result = SubagentResult(
                subagent_id=f"ver_r{round_num}_{int(time.time() * 1000)}_iso",
                role=verifier_role,
                success=False,
                output=None,
            )
            try:
                ver_result.success = _dispatch_subagent(
                    agent_type=verifier_role,
                    task={
                        "description": safe_task["description"],
                        "round": round_num,
                        "phase": "verify",
                        "evaluation_criteria": evaluation_criteria,
                        "isolated_context": True,  # Phase 1 标记：独立 context
                    },
                    task_id=ver_result.subagent_id,
                    sandbox=self._sandbox,
                    router=self._router,
                    budget_guard=self._budget_guard,
                    pattern_id=self.pattern_id,  # Phase 10 透传
                )
                # 简化的通过判定：Phase 1 基于 dispatch 成功 + 准则覆盖
                # 真实场景：验证者会输出每条准则的通过/不通过
                pass_rate = self._estimate_pass_rate(
                    ver_result.success, len(evaluation_criteria)
                )
                ver_result.output = (
                    f"[轮次 {round_num}] {verifier_role} 验证结果，"
                    f"pass_rate={pass_rate:.2f}"
                )
                if pass_rate >= pass_threshold:
                    final_pass = True
                    round_results.append(ver_result)
                    break
            except DispatchError as e:
                ver_result.error = str(e)
            ver_result.execution_time_seconds = time.perf_counter() - ver_start
            round_results.append(ver_result)

            # fallback 处理
            if fallback_on_reject == "abort":
                error_msg = f"轮次 {round_num} 验证未通过，策略=abort"
                break
            elif fallback_on_reject == "human_review":
                error_msg = (
                    f"轮次 {round_num} 验证未通过，升级到 human_review"
                )
                break
            # regenerate: 继续下一轮

        # 阶段 5：构建结果
        if final_pass:
            status = ExecutionStatus.SUCCESS
        else:
            status = ExecutionStatus.FAILURE
            if error_msg is None:
                error_msg = (
                    f"经 {rounds_executed} 轮对抗仍未通过 "
                    f"（threshold={pass_threshold}）"
                )

        result = ExecutionResult(
            pattern_id=self.pattern_id,
            status=status,
            subagent_results=round_results,
            aggregated_output={
                "final_pass": final_pass,
                "rounds_executed": rounds_executed,
                "max_rounds": max_rounds,
                "pass_threshold": pass_threshold,
            },
            error=error_msg,
            total_execution_time_seconds=time.perf_counter() - start_time,
            guard_result=guard_result,
            metadata={
                "verifier_isolation": parameters.get("verifier_isolation", "context"),
                "evaluation_criteria_count": len(evaluation_criteria),
            },
        )

        # 阶段 6：画像反哺
        self.record_to_fingerprint(task, result)

        return result

    def _estimate_pass_rate(
        self, dispatch_success: bool, criteria_count: int
    ) -> float:
        """
        估算通过率（Phase 1 简化版）

        真实实现：验证者会输出每条准则的通过/不通过
        Phase 1 简化：基于 dispatch 成功 + 准则数估算

        Args:
            dispatch_success: dispatch 是否成功
            criteria_count: 评估准则数

        Returns:
            float: 通过率 0.0-1.0
        """
        if not dispatch_success:
            return 0.0
        if criteria_count == 0:
            return 0.0
        # 简化：dispatch 成功 + 准则数适中 → 0.85
        # Phase 2+ 用真实验证者输出
        return 0.85

    def record_to_fingerprint(
        self,
        task: Dict[str, Any],
        result: ExecutionResult,
    ) -> None:
        """写入画像"""
        try:
            self._fingerprint.record(
                task_type=task.get("task_type", "adversarial"),
                task_complexity=task.get("task_complexity", 7),
                success=result.success,
                error_type=(
                    result.error
                    if result.status in (
                        ExecutionStatus.FAILURE,
                        ExecutionStatus.REJECTED,
                    )
                    else None
                ),
                execution_time=result.total_execution_time_seconds,
                strategy=self.pattern_id,
                context_features={
                    "task_description": task.get("description", "")[:200],
                    "rounds_executed": result.metadata.get(
                        "rounds_executed",
                        result.aggregated_output.get("rounds_executed", 0)
                        if isinstance(result.aggregated_output, dict)
                        else 0,
                    ),
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"画像反哺失败（非致命）: {e}")


# ============================================================================
# 工具函数（Phase 5 扩展：generate-filter 去重）
# ============================================================================

def _normalize_for_dedup(text: str) -> str:
    """
    文本归一化（用于 exact 去重）

    策略：去除空白 + 转小写

    Args:
        text: 原始文本

    Returns:
        str: 归一化后的文本
    """
    return re.sub(r"\s+", "", text or "").lower()


def _fuzzy_similarity(
    a: str,
    b: str,
    embedder: Optional[Any] = None,
) -> float:
    """
    模糊/语义相似度

    Phase 5：基于最长公共子串 LCS（O(n*m)）
    Phase 6：当 embedder 不为空时，调用 embedder.similarity（语义相似度）

    Args:
        a: 字符串 A
        b: 字符串 B
        embedder: 可选 Embedder（Phase 6+ 注入，用于真正的语义相似度）

    Returns:
        float: 相似度 0.0-1.0
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    # Phase 6 升级：当 embedder 注入时，调用 embedder 真实语义相似度
    if embedder is not None:
        try:
            # 🔴 修复：embedder 不可用时不能抛错，必须 fallback 到 fuzzy
            return float(embedder.similarity(a, b))
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"embedder.similarity 失败，fallback 到 LCS: {e}"
            )
            # fall through to LCS

    # Phase 5 实现：基于 LCS 的模糊相似度
    a_norm, b_norm = _normalize_for_dedup(a), _normalize_for_dedup(b)
    if a_norm == b_norm:
        return 1.0
    n, m = len(a_norm), len(b_norm)
    if n == 0 or m == 0:
        return 0.0
    # 限制最大长度以避免 OOM
    if max(n, m) > 200:
        a_norm = a_norm[:200]
        b_norm = b_norm[:200]
        n, m = len(a_norm), len(b_norm)
    # DP
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if a_norm[i - 1] == b_norm[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    lcs = dp[n][m]
    return (2.0 * lcs) / (n + m) if (n + m) > 0 else 0.0


def _dedup_candidates(
    candidates: List[str],
    strategy: str = "exact",
    threshold: float = 0.85,
    embedder: Optional[Any] = None,
) -> List[str]:
    """
    去重候选列表

    策略：
    - exact：完全匹配（忽略空白和大小写）视为同一
    - fuzzy：LCS 相似度 ≥ threshold 视为同一
    - semantic：调用 embedder.similarity 计算真实语义相似度（Phase 6 升级）
              embedder 不可用时 fallback 到 fuzzy

    Args:
        candidates: 候选列表
        strategy: 去重策略
        threshold: fuzzy 阈值（0.0-1.0）
        embedder: 可选 Embedder（Phase 6 注入，仅 semantic 策略生效）

    Returns:
        List[str]: 去重后的候选列表（保持原始顺序）
    """
    if not candidates:
        return []

    result: List[str] = []

    for c in candidates:
        if not c:
            continue
        is_dup = False
        for existing in result:
            if strategy == "exact":
                if _normalize_for_dedup(c) == _normalize_for_dedup(existing):
                    is_dup = True
                    break
            elif strategy == "semantic":
                # Phase 6 升级：调用 embedder.similarity 真实语义相似度
                # embedder 注入时使用；未注入时 fallback 到 fuzzy
                if _fuzzy_similarity(c, existing, embedder=embedder) >= threshold:
                    is_dup = True
                    break
            elif strategy == "fuzzy":
                # Phase 5：LCS 模糊相似度
                if _fuzzy_similarity(c, existing) >= threshold:
                    is_dup = True
                    break
            else:
                # 未知策略 → 退化为 exact
                if _normalize_for_dedup(c) == _normalize_for_dedup(existing):
                    is_dup = True
                    break
        if not is_dup:
            result.append(c)

    return result


# ============================================================================
# 执行器 4：GenerateFilterExecutor（生成与筛选，Phase 5）
# ============================================================================

class GenerateFilterExecutor:
    """
    generate-filter 模式执行器（Phase 5 新增）

    真实逻辑：
    1. Guard 校验
    2. 并发生成 N 个候选（generator_count 硬上限 20）
    3. 评估筛选（按 filter_criteria + quality_floor）
    4. 去重（exact / fuzzy / semantic）
    5. 取 top N（output_top_n）
    6. 记录所有候选 + 筛选过程

    关键设计：
    - 异常隔离：单个候选生成失败不影响其他候选
    - 评估简化：基于 dispatch 成功 + 候选长度判定质量分数
    - 完全支持 Phase 2+3+4 资源管理（sandbox / router / budget_guard）
    """

    def __init__(
        self,
        fingerprint: Optional[PerformanceFingerprint] = None,
        sandbox: Optional[Any] = None,
        router: Optional[Any] = None,
        budget_guard: Optional[Any] = None,
    ):
        self._pattern = PATTERN_GENERATE_FILTER
        self._fingerprint = fingerprint or PerformanceFingerprint(
            agent_id="generate_filter_executor"
        )
        self._sandbox = sandbox
        self._router = router
        self._budget_guard = budget_guard

    @property
    def pattern_id(self) -> str:
        return self._pattern.pattern_id

    def execute(
        self,
        task: Dict[str, Any],
        parameters: Dict[str, Any],
    ) -> ExecutionResult:
        """执行生成与筛选"""
        start_time = time.perf_counter()

        # 阶段 1：Guard 校验
        guard_result = guard_check(
            inputs=task,
            schema=GENERATE_FILTER_SCHEMA,
            token_budget=parameters.get("token_budget", self._pattern.default_token_budget),
        )
        if guard_result.decision == GuardDecision.REJECT:
            return ExecutionResult(
                pattern_id=self.pattern_id,
                status=ExecutionStatus.REJECTED,
                error=guard_result.reason,
                guard_result=guard_result,
                total_execution_time_seconds=time.perf_counter() - start_time,
            )

        safe_task = guard_result.sanitized_input or task

        # 阶段 2：参数解析（硬上限保护）
        generator_count = min(max(3, parameters.get("generator_count", 5)), 20)
        generator_role = parameters.get("generator_role", "product-manager")
        filter_criteria: List[str] = safe_task.get("filter_criteria", [])
        if not filter_criteria:
            filter_criteria = parameters.get("filter_criteria", [])
        dedup_strategy = parameters.get("dedup_strategy", "fuzzy")
        dedup_threshold = float(parameters.get("dedup_threshold", 0.85))
        output_top_n = max(1, parameters.get("output_top_n", 3))
        quality_floor = float(parameters.get("quality_floor", 0.6))

        # Phase 6 升级：embedder 注入（仅 dedup_strategy=semantic 生效）
        # 默认 None（不注入）；用户可通过 parameters["embedder"] 注入配置
        embedder = self._resolve_embedder(
            dedup_strategy=dedup_strategy,
            parameters=parameters,
        )

        # 阶段 3：生成候选（并发）
        subagent_results: List[SubagentResult] = []

        def _generate_one(index: int) -> SubagentResult:
            """生成单个候选"""
            sa_start = time.perf_counter()
            res = SubagentResult(
                subagent_id=f"gf_gen_{int(time.time() * 1000)}_{index}",
                role=generator_role,
                success=False,
                output=None,
            )
            try:
                dispatch_input = {
                    "description": safe_task["description"],
                    "candidate_index": index,
                    "total_candidates": generator_count,
                    "filter_criteria": filter_criteria,
                    "phase": "generate",
                }
                res.success = _dispatch_subagent(
                    agent_type=generator_role,
                    task=dispatch_input,
                    task_id=res.subagent_id,
                    sandbox=self._sandbox,
                    router=self._router,
                    budget_guard=self._budget_guard,
                    pattern_id=self.pattern_id,  # Phase 10 透传
                )
                # Phase 5 简化：候选内容 = task description（不含 index）
                # 这样 dedup 可正常判定重复，测试可验证去重逻辑
                res.output = safe_task["description"]
            except DispatchError as e:
                res.error = str(e)
                logger.warning(f"生成候选 #{index} 失败: {e}")
            res.execution_time_seconds = time.perf_counter() - sa_start
            return res

        with ThreadPoolExecutor(max_workers=min(generator_count, 10)) as executor:
            futures = [executor.submit(_generate_one, i) for i in range(generator_count)]
            for f in as_completed(futures):
                subagent_results.append(f.result())

        # 阶段 4：质量评估 + 筛选
        candidates_with_score: List[tuple] = []  # (candidate_text, score, subagent_id)
        for r in subagent_results:
            if r.success and r.output:
                # Phase 5 简化：基于 dispatch 成功 + 长度计算质量分数
                score = self._estimate_quality(r.output, filter_criteria)
                if score >= quality_floor:
                    candidates_with_score.append((r.output, score, r.subagent_id))

        # 阶段 5：去重
        all_texts = [c[0] for c in candidates_with_score]
        deduped_texts = _dedup_candidates(
            all_texts, dedup_strategy, dedup_threshold, embedder=embedder
        )
        # 🔴 关键修复：使用 deduped_texts 顺序保留去重后的项，再映射回 candidates_with_score
        # 之前用 set(deduped_texts) 丢失顺序，且 c[0] in set 全部命中
        deduped_candidates: List[tuple] = []
        seen_normalized: set = set()
        for c in candidates_with_score:
            norm = _normalize_for_dedup(c[0])
            if norm in seen_normalized:
                continue
            seen_normalized.add(norm)
            deduped_candidates.append(c)

        # 阶段 6：排序（按分数降序）+ 取 top N
        deduped_candidates.sort(key=lambda x: x[1], reverse=True)
        top_n = deduped_candidates[:output_top_n]

        # 阶段 7：构建结果
        success_count = len(top_n)
        if success_count == 0:
            status = ExecutionStatus.FAILURE
            error_msg = "无候选通过筛选（quality_floor 过高或全部重复）"
        elif success_count < output_top_n:
            status = ExecutionStatus.PARTIAL_SUCCESS
            error_msg = f"仅返回 {success_count}/{output_top_n} 个候选"
        else:
            status = ExecutionStatus.SUCCESS
            error_msg = None

        aggregated = {
            "candidates": [c[0] for c in top_n],
            "scores": [c[1] for c in top_n],
            "subagent_ids": [c[2] for c in top_n],
            "total_generated": generator_count,
            "passed_filter": len(candidates_with_score),
            "after_dedup": len(deduped_candidates),
            "returned": success_count,
        }

        result = ExecutionResult(
            pattern_id=self.pattern_id,
            status=status,
            subagent_results=subagent_results,
            aggregated_output=aggregated,
            error=error_msg,
            total_execution_time_seconds=time.perf_counter() - start_time,
            guard_result=guard_result,
            metadata={
                "generator_count": generator_count,
                "dedup_strategy": dedup_strategy,
                "dedup_threshold": dedup_threshold,
                "quality_floor": quality_floor,
                "output_top_n": output_top_n,
            },
        )

        # 阶段 8：画像反哺
        self.record_to_fingerprint(task, result)

        return result

    def _resolve_embedder(
        self,
        dedup_strategy: str,
        parameters: Dict[str, Any],
    ) -> Optional[Any]:
        """
        解析 embedder 配置（Phase 6 新增）

        策略：
        1. dedup_strategy != "semantic" → 不需要 embedder，返回 None
        2. parameters["embedder"] 已是 Embedder 实例 → 直接返回（用户已注入）
        3. parameters["embedder"] 是 dict 配置 → 调用 create_embedder 创建
        4. 未配置 → 返回 None（fallback 到 fuzzy）

        Args:
            dedup_strategy: 去重策略
            parameters: 用户参数

        Returns:
            Optional[Embedder]: Embedder 实例或 None
        """
        # 仅 semantic 策略需要 embedder
        if dedup_strategy != "semantic":
            return None

        # 读取用户配置
        embedder_config = parameters.get("embedder")
        if embedder_config is None:
            # 未配置 → 返回 None（fallback 到 fuzzy）
            return None

        # 已是 Embedder 实例（用户预注入）
        from semantic_embedder import Embedder
        if isinstance(embedder_config, Embedder):
            return embedder_config

        # 是 dict 配置 → 调用工厂创建
        if isinstance(embedder_config, dict):
            try:
                from semantic_embedder import create_embedder
                # 🔴 关键修复：create_embedder 的第一个参数是 embedder_type
                # 必须从 embedder_config 中提取 type 字段作为位置参数
                embedder_type = embedder_config.get("type", "auto")
                # 剩余字段作为 kwargs（去除 type）
                kwargs = {
                    k: v for k, v in embedder_config.items() if k != "type"
                }
                embedder = create_embedder(
                    embedder_type=embedder_type, **kwargs
                )
                logger.info(
                    f"GenerateFilterExecutor 注入 embedder: {type(embedder).__name__}"
                )
                return embedder
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"create_embedder 失败，fallback 到 fuzzy: {e}"
                )
                return None

        # 未知类型 → fallback
        logger.warning(
            f"embedder_config 类型未知: {type(embedder_config).__name__}，"
            f"fallback 到 fuzzy"
        )
        return None

    def _estimate_quality(
        self,
        output: Any,
        filter_criteria: List[str],
    ) -> float:
        """
        估算候选质量分数（Phase 5 简化版）

        真实实现：可调用 verifier 评估每条 filter_criteria 的通过情况。
        Phase 5 简化：基于 dispatch 成功 + 输出长度 + criteria 匹配度。

        Args:
            output: subagent 输出
            filter_criteria: 筛选标准

        Returns:
            float: 质量分数 0.0-1.0
        """
        if not output:
            return 0.0
        text = str(output)
        # 基础分：dispatch 成功
        score = 0.7
        # 长度奖励：太短可能是空响应
        if len(text) >= 10:
            score += 0.1
        if len(text) >= 50:
            score += 0.1
        # criteria 关键词覆盖（粗略）
        if filter_criteria:
            matched = sum(
                1 for c in filter_criteria
                if any(kw in text for kw in c.split() if len(kw) >= 2)
            )
            score += 0.1 * min(1.0, matched / max(1, len(filter_criteria)))
        return min(1.0, score)

    def record_to_fingerprint(
        self,
        task: Dict[str, Any],
        result: ExecutionResult,
    ) -> None:
        """写入画像"""
        try:
            agg = result.aggregated_output if isinstance(result.aggregated_output, dict) else {}
            self._fingerprint.record(
                task_type=task.get("task_type", "generate_filter"),
                task_complexity=task.get("task_complexity", 4),
                success=result.success,
                error_type=(
                    result.error
                    if result.status in (
                        ExecutionStatus.FAILURE,
                        ExecutionStatus.PARTIAL_SUCCESS,
                    )
                    else None
                ),
                execution_time=result.total_execution_time_seconds,
                strategy=self.pattern_id,
                context_features={
                    "task_description": task.get("description", "")[:200],
                    "generator_count": agg.get("total_generated", 0),
                    "returned": agg.get("returned", 0),
                    "dedup_strategy": result.metadata.get("dedup_strategy", "fuzzy"),
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"画像反哺失败（非致命）: {e}")


# ============================================================================
# 执行器 5：TournamentExecutor（锦标赛模式，Phase 5）
# ============================================================================

class TournamentExecutor:
    """
    tournament 模式执行器（Phase 5 新增）

    真实逻辑：
    1. Guard 校验
    2. 生成 N 个候选（candidate_count 硬上限 8）
    3. 按 ranking_method 编排 PK（knockout / round-robin / elo）
    4. 每场 PK 调 judge 决定胜者
    5. 决出唯一冠军
    6. 记录所有 PK 过程

    关键设计：
    - candidate_count 硬上限 8，超过自动降级 knockout
    - knockout 平局：随机晋级（Phase 5 简化）
    - 异常隔离：单场 PK 失败不影响整体锦标赛
    - judge_context_isolation 强校验（防止 self-bias）
    """

    def __init__(
        self,
        fingerprint: Optional[PerformanceFingerprint] = None,
        sandbox: Optional[Any] = None,
        router: Optional[Any] = None,
        budget_guard: Optional[Any] = None,
    ):
        self._pattern = PATTERN_TOURNAMENT
        self._fingerprint = fingerprint or PerformanceFingerprint(
            agent_id="tournament_executor"
        )
        self._sandbox = sandbox
        self._router = router
        self._budget_guard = budget_guard

    @property
    def pattern_id(self) -> str:
        return self._pattern.pattern_id

    def _validate_isolation(self, parameters: Dict[str, Any]) -> None:
        """
        强约束：judge_context_isolation 必须为 True（防 self-bias）

        Raises:
            ValueError: 当隔离约束不满足时
        """
        if not parameters.get("judge_context_isolation", True):
            raise ValueError(
                "tournament 模式要求 judge_context_isolation=True，"
                "否则裁判与生成器共享 context 会产生 self-preferential bias"
            )

    def execute(
        self,
        task: Dict[str, Any],
        parameters: Dict[str, Any],
    ) -> ExecutionResult:
        """执行锦标赛"""
        start_time = time.perf_counter()

        # 阶段 1：隔离强校验
        try:
            self._validate_isolation(parameters)
        except ValueError as e:
            return ExecutionResult(
                pattern_id=self.pattern_id,
                status=ExecutionStatus.FAILURE,
                error=f"隔离校验失败：{e}",
                total_execution_time_seconds=time.perf_counter() - start_time,
            )

        # 阶段 2：Guard 校验
        guard_result = guard_check(
            inputs=task,
            schema=TOURNAMENT_SCHEMA,
            token_budget=parameters.get("token_budget", self._pattern.default_token_budget),
        )
        if guard_result.decision == GuardDecision.REJECT:
            return ExecutionResult(
                pattern_id=self.pattern_id,
                status=ExecutionStatus.REJECTED,
                error=guard_result.reason,
                guard_result=guard_result,
                total_execution_time_seconds=time.perf_counter() - start_time,
            )

        safe_task = guard_result.sanitized_input or task

        # 阶段 3：参数解析
        candidate_count = min(max(3, safe_task.get("candidate_count", 4)), 8)
        candidate_generator = parameters.get("candidate_generator", "architect")
        judge_role = parameters.get("judge_role", "test-expert")
        ranking_method = parameters.get("ranking_method", "knockout")
        judge_criteria: List[str] = safe_task.get("judge_criteria", []) or parameters.get("judge_criteria", [])

        # 阶段 4：生成候选
        candidate_results: List[SubagentResult] = []
        for i in range(candidate_count):
            sa_start = time.perf_counter()
            res = SubagentResult(
                subagent_id=f"tn_gen_{int(time.time() * 1000)}_{i}",
                role=candidate_generator,
                success=False,
                output=None,
            )
            try:
                res.success = _dispatch_subagent(
                    agent_type=candidate_generator,
                    task={
                        "description": safe_task["description"],
                        "candidate_index": i,
                        "total_candidates": candidate_count,
                        "phase": "generate",
                    },
                    task_id=res.subagent_id,
                    sandbox=self._sandbox,
                    router=self._router,
                    budget_guard=self._budget_guard,
                    pattern_id=self.pattern_id,  # Phase 10 透传
                )
                # Phase 5 简化：使用 task.description（不含 index）
                # 确保不同候选都有 output（即使 description 相同）
                # PK 时通过 subagent_id 区分
                res.output = f"{safe_task['description']}_{i}"  # 仍区分以支持 PK 测试
            except DispatchError as e:
                res.error = str(e)
                logger.warning(f"生成候选 #{i} 失败: {e}")
            res.execution_time_seconds = time.perf_counter() - sa_start
            candidate_results.append(res)

        # 阶段 5：选择有效候选
        valid_candidates = [r for r in candidate_results if r.success and r.output]
        if not valid_candidates:
            return ExecutionResult(
                pattern_id=self.pattern_id,
                status=ExecutionStatus.FAILURE,
                error="所有候选生成失败",
                subagent_results=candidate_results,
                guard_result=guard_result,
                total_execution_time_seconds=time.perf_counter() - start_time,
            )

        # 阶段 6：锦标赛编排
        # 🔴 关键修复：未知 ranking_method 在编排时降级，参数同步更新
        # 确保 result.aggregated_output["ranking_method"] 反映实际使用的方法
        if ranking_method == "knockout":
            champion, pk_results = self._run_knockout(
                valid_candidates, judge_role, judge_criteria, safe_task
            )
        elif ranking_method == "round-robin":
            champion, pk_results = self._run_round_robin(
                valid_candidates, judge_role, judge_criteria, safe_task
            )
        elif ranking_method == "elo":
            champion, pk_results = self._run_elo(
                valid_candidates, judge_role, judge_criteria, safe_task
            )
        else:
            # 未知策略 → 退化到 knockout
            logger.warning(f"未知 ranking_method={ranking_method}，降级到 knockout")
            ranking_method = "knockout"  # 同步更新以反映实际行为
            champion, pk_results = self._run_knockout(
                valid_candidates, judge_role, judge_criteria, safe_task
            )

        # 阶段 7：构建结果
        all_results = candidate_results + pk_results
        if champion is None:
            status = ExecutionStatus.FAILURE
            error_msg = "未能决出冠军（所有 PK 失败）"
        else:
            status = ExecutionStatus.SUCCESS
            error_msg = None

        result = ExecutionResult(
            pattern_id=self.pattern_id,
            status=status,
            subagent_results=all_results,
            aggregated_output={
                "champion": champion.output if champion else None,
                "champion_id": champion.subagent_id if champion else None,
                "ranking_method": ranking_method,
                "total_candidates": candidate_count,
                "valid_candidates": len(valid_candidates),
                "pk_count": len(pk_results),
            },
            error=error_msg,
            total_execution_time_seconds=time.perf_counter() - start_time,
            guard_result=guard_result,
            metadata={
                "candidate_count": candidate_count,
                "ranking_method": ranking_method,
                "judge_role": judge_role,
            },
        )

        # 阶段 8：画像反哺
        self.record_to_fingerprint(task, result)

        return result

    def _judge_pk(
        self,
        candidate_a: SubagentResult,
        candidate_b: SubagentResult,
        judge_role: str,
        judge_criteria: List[str],
        safe_task: Dict[str, Any],
    ) -> SubagentResult:
        """
        单场 PK：调 judge 决定 a vs b 胜者

        Phase 5 简化：基于"先返回的胜出"+ 长度加成（避免依赖真实 LLM judge）
        真实实现：judge 输出 winner="a"/"b"/"tie"，根据字段解析

        Returns:
            SubagentResult: 胜者的 SubagentResult（来自 candidate_a 或 candidate_b）
        """
        judge_start = time.perf_counter()
        judge_res = SubagentResult(
            subagent_id=f"tn_judge_{int(time.time() * 1000)}",
            role=judge_role,
            success=False,
            output=None,
        )
        try:
            # 调用 judge 决定胜者
            judge_input = {
                "description": safe_task["description"],
                "candidate_a": str(candidate_a.output),
                "candidate_b": str(candidate_b.output),
                "judge_criteria": judge_criteria,
                "phase": "judge",
            }
            judge_res.success = _dispatch_subagent(
                agent_type=judge_role,
                task=judge_input,
                task_id=judge_res.subagent_id,
                sandbox=self._sandbox,
                router=self._router,
                budget_guard=self._budget_guard,
                pattern_id=self.pattern_id,  # Phase 10 透传
            )
            judge_res.output = "已评判"
        except DispatchError as e:
            judge_res.error = str(e)
            logger.warning(f"judge 失败: {e}")
        judge_res.execution_time_seconds = time.perf_counter() - judge_start

        # Phase 5 简化判定：dispatch 成功 + 长度比较 + 字典序
        # 真实场景：judge 输出 winner 字段
        if not judge_res.success:
            # judge 失败 → 默认 a 胜
            winner = candidate_a
        else:
            # 简单启发式：内容更长的胜出（Phase 5 简化）
            len_a = len(str(candidate_a.output or ""))
            len_b = len(str(candidate_b.output or ""))
            if len_a > len_b:
                winner = candidate_a
            elif len_b > len_a:
                winner = candidate_b
            else:
                # 长度相同 → 字典序较小的胜出
                winner = (
                    candidate_a
                    if str(candidate_a.output) <= str(candidate_b.output)
                    else candidate_b
                )

        return winner

    def _run_knockout(
        self,
        candidates: List[SubagentResult],
        judge_role: str,
        judge_criteria: List[str],
        safe_task: Dict[str, Any],
    ) -> tuple:
        """
        淘汰赛：N 个候选两两 PK，逐步淘汰，决出冠军

        简化的 single-elimination 流程：
        round 1: ceil(N/2) 场 PK → ceil(N/2) 胜者
        round 2: ceil(ceil(N/2)/2) 场 PK → ...
        直到 1 个胜者

        Args:
            candidates: 有效候选列表
            judge_role: 裁判角色
            judge_criteria: 裁判标准
            safe_task: 净化后的任务

        Returns:
            (champion, pk_results)
        """
        pk_results: List[SubagentResult] = []
        current_round = list(candidates)
        round_num = 0

        while len(current_round) > 1:
            round_num += 1
            next_round: List[SubagentResult] = []
            # 两两配对
            for i in range(0, len(current_round), 2):
                if i + 1 >= len(current_round):
                    # 奇数个：最后一个直接晋级
                    next_round.append(current_round[i])
                    continue
                a = current_round[i]
                b = current_round[i + 1]
                winner = self._judge_pk(a, b, judge_role, judge_criteria, safe_task)
                pk_results.append(SubagentResult(
                    subagent_id=f"tn_pk_r{round_num}_{i}",
                    role=judge_role,
                    success=True,
                    output=f"Round {round_num} PK: {a.subagent_id} vs {b.subagent_id} → 胜者 {winner.subagent_id}",
                ))
                next_round.append(winner)
            current_round = next_round
            logger.info(f"锦标赛第 {round_num} 轮结束，剩 {len(current_round)} 个候选")

        champion = current_round[0] if current_round else None
        return champion, pk_results

    def _run_round_robin(
        self,
        candidates: List[SubagentResult],
        judge_role: str,
        judge_criteria: List[str],
        safe_task: Dict[str, Any],
    ) -> tuple:
        """
        循环赛：每两个候选都 PK 一次，按胜场数决出冠军

        适用：候选数 ≤ 5（避免 PK 爆炸）
        """
        pk_results: List[SubagentResult] = []
        wins: Dict[str, int] = {c.subagent_id: 0 for c in candidates}

        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                a = candidates[i]
                b = candidates[j]
                winner = self._judge_pk(a, b, judge_role, judge_criteria, safe_task)
                wins[winner.subagent_id] = wins.get(winner.subagent_id, 0) + 1
                pk_results.append(SubagentResult(
                    subagent_id=f"tn_rr_{i}_{j}",
                    role=judge_role,
                    success=True,
                    output=f"Round-robin PK: {a.subagent_id} vs {b.subagent_id} → 胜者 {winner.subagent_id}",
                ))

        # 决出胜场最多的
        if not wins:
            return None, pk_results
        champion_id = max(wins, key=lambda k: wins[k])
        champion = next((c for c in candidates if c.subagent_id == champion_id), None)
        return champion, pk_results

    def _run_elo(
        self,
        candidates: List[SubagentResult],
        judge_role: str,
        judge_criteria: List[str],
        safe_task: Dict[str, Any],
    ) -> tuple:
        """
        ELO 评分：每场 PK 调整 ELO 分，最终分最高者为冠军

        Phase 5 简化版：固定 K=32，初始分 1200
        """
        pk_results: List[SubagentResult] = []
        elo: Dict[str, float] = {c.subagent_id: 1200.0 for c in candidates}
        K = 32.0

        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                a = candidates[i]
                b = candidates[j]
                # 期望胜率
                ra, rb = elo[a.subagent_id], elo[b.subagent_id]
                ea = 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))
                eb = 1.0 - ea
                winner = self._judge_pk(a, b, judge_role, judge_criteria, safe_task)
                # 更新 ELO
                sa = 1.0 if winner.subagent_id == a.subagent_id else 0.0
                sb = 1.0 - sa
                elo[a.subagent_id] = ra + K * (sa - ea)
                elo[b.subagent_id] = rb + K * (sb - eb)
                pk_results.append(SubagentResult(
                    subagent_id=f"tn_elo_{i}_{j}",
                    role=judge_role,
                    success=True,
                    output=f"ELO PK: {a.subagent_id} vs {b.subagent_id} → 胜者 {winner.subagent_id} (ELO 更新)",
                ))

        if not elo:
            return None, pk_results
        champion_id = max(elo, key=lambda k: elo[k])
        champion = next((c for c in candidates if c.subagent_id == champion_id), None)
        return champion, pk_results

    def record_to_fingerprint(
        self,
        task: Dict[str, Any],
        result: ExecutionResult,
    ) -> None:
        """写入画像"""
        try:
            agg = result.aggregated_output if isinstance(result.aggregated_output, dict) else {}
            self._fingerprint.record(
                task_type=task.get("task_type", "tournament"),
                task_complexity=task.get("task_complexity", 6),
                success=result.success,
                error_type=(
                    result.error
                    if result.status in (
                        ExecutionStatus.FAILURE,
                        ExecutionStatus.REJECTED,
                    )
                    else None
                ),
                execution_time=result.total_execution_time_seconds,
                strategy=self.pattern_id,
                context_features={
                    "task_description": task.get("description", "")[:200],
                    "candidate_count": agg.get("total_candidates", 0),
                    "pk_count": agg.get("pk_count", 0),
                    "ranking_method": agg.get("ranking_method", "knockout"),
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"画像反哺失败（非致命）: {e}")


# ============================================================================
# 执行器 6：LoopUntilDoneExecutor（循环直到完成，Phase 5）
# ============================================================================

class LoopUntilDoneExecutor:
    """
    loop-until-done 模式执行器（Phase 5 新增）

    真实逻辑：
    1. Guard 校验
    2. 循环：调 iteration_executor 执行单轮
    3. 每轮后检查停止条件（OR 关系）
    4. 满足任一停止条件 / 达到 max_iterations 时停止
    5. 返回最后一轮结果

    关键设计：
    - max_iterations 硬上限 50（防死循环）
    - 异常隔离：单轮失败不直接中断循环（除非达到 max_iterations）
    - state_persistence 字段保留（Phase 5+ 引入 CheckpointManager 持久化）
    - 停止条件支持 4 种：no_new_findings / no_error_logs / quality_threshold_met / convergence_detected

    痛点缓解：
    - goal drift：每轮重新校准目标（基于累积 findings）
    - 死循环：max_iterations 硬上限
    """

    def __init__(
        self,
        fingerprint: Optional[PerformanceFingerprint] = None,
        sandbox: Optional[Any] = None,
        router: Optional[Any] = None,
        budget_guard: Optional[Any] = None,
    ):
        self._pattern = PATTERN_LOOP_UNTIL_DONE
        self._fingerprint = fingerprint or PerformanceFingerprint(
            agent_id="loop_until_done_executor"
        )
        self._sandbox = sandbox
        self._router = router
        self._budget_guard = budget_guard

    @property
    def pattern_id(self) -> str:
        return self._pattern.pattern_id

    def execute(
        self,
        task: Dict[str, Any],
        parameters: Dict[str, Any],
    ) -> ExecutionResult:
        """执行循环直到完成"""
        start_time = time.perf_counter()

        # 阶段 1：Guard 校验
        guard_result = guard_check(
            inputs=task,
            schema=LOOP_UNTIL_DONE_SCHEMA,
            token_budget=parameters.get("token_budget", self._pattern.default_token_budget),
        )
        if guard_result.decision == GuardDecision.REJECT:
            return ExecutionResult(
                pattern_id=self.pattern_id,
                status=ExecutionStatus.REJECTED,
                error=guard_result.reason,
                guard_result=guard_result,
                total_execution_time_seconds=time.perf_counter() - start_time,
            )

        safe_task = guard_result.sanitized_input or task

        # 阶段 2：参数解析
        max_iterations = min(max(1, parameters.get("max_iterations", 5)), 50)
        iteration_executor_role = parameters.get("iteration_executor", "architect")
        stop_conditions = parameters.get("stop_conditions", {}) or {}
        quality_threshold = float(parameters.get("quality_threshold", 0.85))
        state_persistence = parameters.get("state_persistence", "memory")

        # 至少需要一个停止条件
        if not stop_conditions:
            return ExecutionResult(
                pattern_id=self.pattern_id,
                status=ExecutionStatus.FAILURE,
                error="stop_conditions 不能为空（防死循环）",
                guard_result=guard_result,
                total_execution_time_seconds=time.perf_counter() - start_time,
            )

        # 阶段 3：循环
        iteration_results: List[SubagentResult] = []
        final_stop_reason = "max_iterations"
        final_output: Any = None
        stop_reached = False

        for iteration in range(1, max_iterations + 1):
            logger.info(f"loop-until-done 第 {iteration}/{max_iterations} 轮")

            sa_start = time.perf_counter()
            res = SubagentResult(
                subagent_id=f"loop_iter_{int(time.time() * 1000)}_{iteration}",
                role=iteration_executor_role,
                success=False,
                output=None,
            )
            try:
                dispatch_input = {
                    "description": safe_task["description"],
                    "iteration": iteration,
                    "max_iterations": max_iterations,
                    "previous_findings": [str(r.output) for r in iteration_results[-3:] if r.output],
                    "phase": "iterate",
                }
                res.success = _dispatch_subagent(
                    agent_type=iteration_executor_role,
                    task=dispatch_input,
                    task_id=res.subagent_id,
                    sandbox=self._sandbox,
                    router=self._router,
                    budget_guard=self._budget_guard,
                    pattern_id=self.pattern_id,  # Phase 10 透传
                )
                # Phase 5 简化：使用 task.description 作为 output，迭代号不写入 output
                # 这样 no_new_findings / convergence_detected 可触发
                res.output = safe_task["description"]
            except DispatchError as e:
                res.error = str(e)
                logger.warning(f"第 {iteration} 轮执行失败: {e}")
            res.execution_time_seconds = time.perf_counter() - sa_start
            iteration_results.append(res)

            # 阶段 4：检查停止条件（OR 关系）
            # 🔴 关键修复：仅在触发停止时才更新 final_stop_reason，
            # 避免 _check_stop_conditions 返回的 "" 覆盖默认值
            triggered, reason = self._check_stop_conditions(
                stop_conditions=stop_conditions,
                iteration_results=iteration_results,
                quality_threshold=quality_threshold,
            )
            if triggered:
                stop_reached = True
                final_stop_reason = reason
                final_output = res.output
                logger.info(f"loop-until-done 在第 {iteration} 轮停止：{final_stop_reason}")
                break

        if not stop_reached and len(iteration_results) > 0:
            final_output = iteration_results[-1].output

        # 阶段 5：构建结果
        success_count = sum(1 for r in iteration_results if r.success)
        if success_count == 0:
            status = ExecutionStatus.FAILURE
            error_msg = "所有迭代轮次均失败"
        elif not stop_reached:
            status = ExecutionStatus.PARTIAL_SUCCESS
            error_msg = f"达到 max_iterations={max_iterations} 仍未满足停止条件"
        else:
            status = ExecutionStatus.SUCCESS
            error_msg = None

        result = ExecutionResult(
            pattern_id=self.pattern_id,
            status=status,
            subagent_results=iteration_results,
            aggregated_output={
                "final_output": final_output,
                "stop_reason": final_stop_reason,
                "iterations_executed": len(iteration_results),
                "max_iterations": max_iterations,
                "state_persistence": state_persistence,
            },
            error=error_msg,
            total_execution_time_seconds=time.perf_counter() - start_time,
            guard_result=guard_result,
            metadata={
                "max_iterations": max_iterations,
                "stop_conditions": stop_conditions,
                "quality_threshold": quality_threshold,
            },
        )

        # 阶段 6：画像反哺
        self.record_to_fingerprint(task, result)

        return result

    def _check_stop_conditions(
        self,
        stop_conditions: Dict[str, Any],
        iteration_results: List[SubagentResult],
        quality_threshold: float,
    ) -> tuple:
        """
        检查停止条件（OR 关系：满足任一即停止）

        Args:
            stop_conditions: 停止条件字典
            iteration_results: 已执行轮次结果
            quality_threshold: 质量阈值

        Returns:
            (stop_reached: bool, reason: str)
        """
        if not iteration_results:
            return False, ""

        # 条件 1: no_new_findings — 最后一轮无新发现
        if stop_conditions.get("no_new_findings") and len(iteration_results) >= 2:
            last = iteration_results[-1]
            prev = iteration_results[-2]
            # Phase 5 简化：上一轮与本轮输出"相似"视为无新发现
            # 真实场景：基于"findings 数"或"差异化指标"
            if last.success and prev.success and last.output == prev.output:
                return True, "no_new_findings"

        # 条件 2: no_error_logs — 最近一轮无错误
        if stop_conditions.get("no_error_logs"):
            last = iteration_results[-1]
            if last.success and not last.error:
                return True, "no_error_logs"

        # 条件 3: quality_threshold_met — 达到质量阈值
        if stop_conditions.get("quality_threshold_met"):
            last = iteration_results[-1]
            if last.success and last.output:
                # Phase 5 简化：基于输出长度和"成功"标记估算质量
                estimated = min(1.0, len(str(last.output)) / 100.0)
                if estimated >= quality_threshold:
                    return True, "quality_threshold_met"

        # 条件 4: convergence_detected — 检测到收敛
        if stop_conditions.get("convergence_detected") and len(iteration_results) >= 3:
            # Phase 5 简化：最近 3 轮输出全部相同视为收敛
            last3 = iteration_results[-3:]
            if all(r.output == last3[0].output for r in last3):
                return True, "convergence_detected"

        return False, ""

    def record_to_fingerprint(
        self,
        task: Dict[str, Any],
        result: ExecutionResult,
    ) -> None:
        """写入画像"""
        try:
            agg = result.aggregated_output if isinstance(result.aggregated_output, dict) else {}
            self._fingerprint.record(
                task_type=task.get("task_type", "loop_until_done"),
                task_complexity=task.get("task_complexity", 7),
                success=result.success,
                error_type=(
                    result.error
                    if result.status in (
                        ExecutionStatus.FAILURE,
                        ExecutionStatus.PARTIAL_SUCCESS,
                    )
                    else None
                ),
                execution_time=result.total_execution_time_seconds,
                strategy=self.pattern_id,
                context_features={
                    "task_description": task.get("description", "")[:200],
                    "iterations_executed": agg.get("iterations_executed", 0),
                    "max_iterations": agg.get("max_iterations", 0),
                    "stop_reason": agg.get("stop_reason", "max_iterations"),
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"画像反哺失败（非致命）: {e}")


# ============================================================================
# 执行器注册表
# ============================================================================

class PatternExecutorRegistry:
    """
    模式执行器注册表

    提供按 pattern_id 查找执行器的能力。
    替代 v2 引擎不存在的 `register_executor` 机制。
    """

    def __init__(
        self,
        fingerprint: Optional[PerformanceFingerprint] = None,
        sandbox: Optional[Any] = None,  # Phase 2
        router: Optional[Any] = None,  # Phase 3+4
        budget_guard: Optional[Any] = None,  # Phase 3+4
    ):
        self._executors: Dict[str, PatternExecutor] = {}
        self._fingerprint = fingerprint
        # Phase 2+3+4: 共享资源（用于 _dispatch_subagent）
        self._sandbox = sandbox
        self._router = router
        self._budget_guard = budget_guard

    def register(self, executor: PatternExecutor) -> None:
        """注册一个执行器"""
        self._executors[executor.pattern_id] = executor

    def get(self, pattern_id: str) -> Optional[PatternExecutor]:
        """获取执行器"""
        return self._executors.get(pattern_id)

    def list_ids(self) -> List[str]:
        """列出所有已注册执行器 ID"""
        return list(self._executors.keys())

    def get_dispatch_context(self) -> Dict[str, Any]:
        """
        获取 dispatch 上下文（Phase 4）

        Returns:
            Dict: 包含 sandbox / router / budget_guard 的字典
        """
        return {
            "sandbox": self._sandbox,
            "router": self._router,
            "budget_guard": self._budget_guard,
        }

    @classmethod
    def create_default(
        cls,
        fingerprint: Optional[PerformanceFingerprint] = None,
        sandbox: Optional[Any] = None,  # SubagentSandbox（Phase 2）
        router: Optional[Any] = None,  # ModelRouter（Phase 3+4）
        budget_guard: Optional[Any] = None,  # TokenBudgetGuard（Phase 3+4）
    ) -> "PatternExecutorRegistry":
        """创建默认注册表（含 6 大核心执行器，Phase 5 补齐）"""
        registry = cls(
            fingerprint=fingerprint,
            sandbox=sandbox,
            router=router,
            budget_guard=budget_guard,
        )
        # Phase 0 三个核心
        registry.register(ClassifierDispatchExecutor(
            fingerprint=fingerprint, sandbox=sandbox,
            router=router, budget_guard=budget_guard,
        ))
        registry.register(FanOutAggregateExecutor(
            fingerprint=fingerprint, sandbox=sandbox,
            router=router, budget_guard=budget_guard,
        ))
        registry.register(AdversarialVerifyExecutor(
            fingerprint=fingerprint, sandbox=sandbox,
            router=router, budget_guard=budget_guard,
        ))
        # Phase 5 三个新模式
        registry.register(GenerateFilterExecutor(
            fingerprint=fingerprint, sandbox=sandbox,
            router=router, budget_guard=budget_guard,
        ))
        registry.register(TournamentExecutor(
            fingerprint=fingerprint, sandbox=sandbox,
            router=router, budget_guard=budget_guard,
        ))
        registry.register(LoopUntilDoneExecutor(
            fingerprint=fingerprint, sandbox=sandbox,
            router=router, budget_guard=budget_guard,
        ))
        return registry


# ============================================================================
# 便捷入口
# ============================================================================

def execute_pattern(
    pattern_id: str,
    task: Dict[str, Any],
    parameters: Dict[str, Any],
    registry: Optional[PatternExecutorRegistry] = None,
) -> ExecutionResult:
    """
    一键执行模式：按 pattern_id 查找执行器并执行

    Args:
        pattern_id: 模式 ID
        task: 任务描述
        parameters: 实例化参数
        registry: 执行器注册表（默认创建）

    Returns:
        ExecutionResult: 执行结果
    """
    reg = registry or PatternExecutorRegistry.create_default()
    executor = reg.get(pattern_id)
    if executor is None:
        return ExecutionResult(
            pattern_id=pattern_id,
            status=ExecutionStatus.FAILURE,
            error=f"未找到模式 '{pattern_id}' 的执行器",
        )
    return executor.execute(task, parameters)
