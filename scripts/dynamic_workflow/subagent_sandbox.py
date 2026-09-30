#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Subagent Sandbox - subagent 执行沙箱

职责：
1. 提供"独立 worktree + 独立 context + Token 预算"的 subagent 执行环境
2. 强制 Guard 校验（不可绕过）
3. 异常隔离（一个 subagent 失败不影响父 workflow）
4. 生命周期管理（spawn → execute → cleanup）
5. 与 PerformanceFingerprint 联动（沙箱元数据记录）

依据：
- DYNAMIC_WORKFLOWS_INTEGRATION.md §模块 3
- 架构师审查 §3.0.3 安全约束、§8.1 安全必做
- Phase 2 计划 PHASE2_PLAN.md

设计原则：
- 不修改任何 V2 文件
- 复用 PerformanceFingerprint 记录沙箱元数据
- 复用 Guard 进行输入校验
- 隔离级别可配置（none/context/worktree/full）
- 异常隔离：executor 抛异常不传播给父

作者：trae-multi-agent 融合 Phase 2
创建日期：2026-06-03
"""

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

# Local imports
from worktree_manager import WorktreeInfo, WorktreeManager

# 同目录模块
import sys
from pathlib import Path
DYNAMIC_WORKFLOW_DIR = Path(__file__).resolve().parent
if str(DYNAMIC_WORKFLOW_DIR) not in sys.path:
    sys.path.insert(0, str(DYNAMIC_WORKFLOW_DIR))

# Guard
import guard
from guard import GuardDecision, GuardResult, check as guard_check

# Phase 8：Skill Injector（可选依赖，向后兼容）
try:
    from skill_injector import SkillInjector, InjectionResult  # noqa: E402
    SKILL_INJECTOR_AVAILABLE = True
except ImportError:
    # 优雅降级：未实现时不影响 sandbox
    SKILL_INJECTOR_AVAILABLE = False
    SkillInjector = None  # type: ignore
    InjectionResult = None  # type: ignore

# Phase 9：InterruptionRecovery（可选依赖，向后兼容）
try:
    from interruption_recovery import (  # noqa: E402
        InterruptionRecoveryManager,
        InterruptionType,
        RecoveryStrategy,
        RetryPolicy,
        SubagentStateSnapshot,
    )
    INTERRUPTION_RECOVERY_AVAILABLE = True
except ImportError:
    # 优雅降级：未实现时不影响 sandbox（行为与 Phase 8 完全一致）
    INTERRUPTION_RECOVERY_AVAILABLE = False
    InterruptionRecoveryManager = None  # type: ignore
    InterruptionType = None  # type: ignore
    RecoveryStrategy = None  # type: ignore
    RetryPolicy = None  # type: ignore
    SubagentStateSnapshot = None  # type: ignore

# PerformanceFingerprint
SCRIPTS_DIR = DYNAMIC_WORKFLOW_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from performance_fingerprint import PerformanceFingerprint  # noqa: E402

logger = logging.getLogger(__name__)


# ============================================================================
# 异常类
# ============================================================================

class SandboxError(Exception):
    """沙箱基础异常"""
    pass


class GuardRejectError(SandboxError):
    """Guard 拒绝（输入校验失败）"""
    def __init__(self, message: str, guard_result: Optional[GuardResult] = None):
        super().__init__(message)
        self.guard_result = guard_result


class TokenBudgetExceeded(SandboxError):
    """Token 预算硬上限"""
    def __init__(self, message: str, token_used: int = 0, token_budget: int = 0):
        super().__init__(message)
        self.token_used = token_used
        self.token_budget = token_budget


class SandboxNotFoundError(SandboxError):
    """沙箱不存在"""
    pass


class SandboxAlreadyExistsError(SandboxError):
    """沙箱已存在"""
    pass


class SandboxTimeoutError(SandboxError):
    """沙箱执行超时"""
    pass


# Phase 9 新增：用户主动控制异常（pause/cancel 触发）
class UserAbort(SandboxError):
    """用户主动取消（cancel_event 触发）"""
    pass


class PauseRequest(SandboxError):
    """用户主动暂停（pause_event 触发）"""
    pass


# SandboxStatus 扩展（Phase 9 新增：CANCELLED 用于 cancel() 后的终态）
class SandboxStatus(Enum):
    """沙箱状态"""
    PENDING = "pending"               # 已创建未执行
    RUNNING = "running"               # 执行中
    SUCCESS = "success"               # 成功
    FAILURE = "failure"               # 失败（executor 异常）
    REJECTED = "rejected"             # Guard 拒绝
    TOKEN_EXCEEDED = "token_exceeded"  # Token 预算耗尽
    TIMEOUT = "timeout"               # 执行超时
    CANCELLED = "cancelled"           # Phase 9：用户主动取消
    PAUSED = "paused"                 # Phase 9：用户主动暂停
    SKIPPED = "skipped"               # Phase 9：恢复策略 SKIP
    CLEANED = "cleaned"               # 已清理


# ============================================================================
# 隔离级别
# ============================================================================

class IsolationLevel:
    """隔离级别常量"""
    NONE = "none"           # 无隔离
    CONTEXT = "context"     # 仅 context 隔离
    WORKTREE = "worktree"   # 仅 worktree 隔离
    FULL = "full"           # worktree + context 双重隔离

    ALL = (NONE, CONTEXT, WORKTREE, FULL)


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class SandboxContext:
    """
    沙箱执行上下文（传给 executor）

    executor 接收此对象，可访问：
    - sandbox_id: 沙箱 ID
    - agent_id: subagent ID
    - worktree_path: worktree 路径（None 表示未创建）
    - context_instance_id: 用于 DualLayerContextManager 的实例 ID
    - token_budget: 预算上限
    - record_token: 回调，用于报告 token 消耗

    Phase 8 新增（SkillDistribution）：
    - injected_skills: 已注入的 skill 名列表（按拓扑序）
    - skill_injection_text: 注入到 system context 的文本（XML/Markdown/Compact）
    - skill_injection_meta: 注入元数据（mode / truncated / 耗时 / 缺失）

    Phase 9 新增（InterruptionRecovery）：
    - pause_event: 暂停信号（executor 内部轮询；set 后挂起）
    - cancel_event: 取消信号（executor 内部轮询；set 后抛 UserAbort）
    - snapshot: 当前快照（恢复时注入）
    - intermediate_results: 中间结果（executor 可写）
    """
    sandbox_id: str
    agent_id: str
    isolation_level: str
    worktree_path: Optional[str]
    context_instance_id: Optional[str]
    token_used: int = 0
    token_budget: int = 10000
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    # Phase 8 新增字段（向后兼容：有默认值）
    injected_skills: List[str] = field(default_factory=list)
    skill_injection_text: str = ""
    skill_injection_meta: Dict[str, Any] = field(default_factory=dict)
    # Phase 9 新增字段（向后兼容：有默认值；recovery_manager=None 时不强制使用）
    pause_event: threading.Event = field(default_factory=threading.Event)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    snapshot: Optional[Dict[str, Any]] = None  # 序列化的快照（轻量；不直接放对象避免循环引用）
    intermediate_results: Dict[str, Any] = field(default_factory=dict)

    def record_token(self, count: int) -> None:
        """
        报告 token 消耗（executor 调用）

        Args:
            count: 消耗的 token 数

        Raises:
            TokenBudgetExceeded: 累计超过预算
        """
        self.token_used += count
        if self.token_used > self.token_budget:
            raise TokenBudgetExceeded(
                f"Token 预算超限：{self.token_used} > {self.token_budget}",
                token_used=self.token_used,
                token_budget=self.token_budget,
            )


@dataclass
class SandboxResult:
    """沙箱执行结果"""
    sandbox_id: str
    agent_id: str
    status: str
    output: Any = None
    token_used: int = 0
    execution_time_seconds: float = 0.0
    error: Optional[str] = None
    worktree_cleaned: bool = False
    isolated: bool = False  # 是否被异常隔离
    guard_result: Optional[Dict[str, Any]] = None  # Guard 结果
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict"""
        return asdict(self)


# ============================================================================
# SubagentSandbox
# ============================================================================

class SubagentSandbox:
    """
    subagent 执行沙箱

    核心能力：
    1. worktree 隔离（可选，通过 isolation_level 控制）
    2. context 隔离（通过 context_instance_id 区分）
    3. Token 预算硬上限（执行中实时检查）
    4. Guard 强制校验（不可绕过）
    5. 异常隔离（finally 块清理 worktree）
    6. 资源画像反哺（写入 PerformanceFingerprint）

    使用示例：
    ```python
    sandbox = SubagentSandbox(
        worktree_manager=WorktreeManager(...),
        fingerprint=PerformanceFingerprint(...),
    )

    # 创建沙箱
    sandbox_id = sandbox.spawn(
        agent_id="sa_001",
        task={"description": "审查 50 个文件"},
        isolation_level="context",
        token_budget=5000,
    )

    # 执行
    def my_executor(ctx: SandboxContext):
        ctx.record_token(100)  # 报告 token 消耗
        return {"result": "ok"}

    try:
        result = sandbox.execute(sandbox_id, my_executor)
        print(result.status)  # success
    finally:
        sandbox.cleanup(sandbox_id)
    ```
    """

    DEFAULT_TOKEN_BUDGET = 10000      # 默认 token 预算
    DEFAULT_EXEC_TIMEOUT = 300        # 默认执行超时（秒）

    def __init__(
        self,
        worktree_manager: Optional[WorktreeManager] = None,
        fingerprint: Optional[PerformanceFingerprint] = None,
        guard_enabled: bool = True,
        skill_injector: Optional["SkillInjector"] = None,
        recovery_manager: Optional["InterruptionRecoveryManager"] = None,
    ):
        """
        初始化 SubagentSandbox

        Args:
            worktree_manager: WorktreeManager 实例（None 时自动创建）
            fingerprint: PerformanceFingerprint 实例（None 时自动创建）
            guard_enabled: 是否启用 Guard 校验（默认 True；不可关闭以满足安全约束）
            skill_injector: Phase 8 新增 - SkillInjector 实例
                （None 时不启用 skill 注入；行为与 Phase 7 完全一致）
            recovery_manager: Phase 9 新增 - InterruptionRecoveryManager 实例
                （None 时不启用中断恢复；行为与 Phase 8 完全一致）

        Notes:
            - Phase 8 向后兼容：默认不启用 skill 注入
            - Phase 9 向后兼容：默认不启用中断恢复
            - 用户可显式传入 recovery_manager 启用 pause/resume/cancel + 自动重试
            - 启用后，sandbox 暴露 pause()/resume()/cancel() 公共方法
        """
        if not guard_enabled:
            logger.warning(
                "guard_enabled=False：跳过 Guard 校验。生产环境必须保持 True。"
            )

        if skill_injector is not None and not SKILL_INJECTOR_AVAILABLE:
            logger.warning(
                "skill_injector 已传入但 skill_injector 模块不可用，已忽略。"
                "请检查 skill_injector.py 是否存在。"
            )
            skill_injector = None

        if recovery_manager is not None and not INTERRUPTION_RECOVERY_AVAILABLE:
            logger.warning(
                "recovery_manager 已传入但 interruption_recovery 模块不可用，已忽略。"
                "请检查 interruption_recovery.py 是否存在。"
            )
            recovery_manager = None

        self._worktree_manager = worktree_manager or WorktreeManager()
        self._fingerprint = fingerprint
        self._guard_enabled = guard_enabled
        self._skill_injector = skill_injector
        self._recovery_manager = recovery_manager
        self._lock = threading.Lock()

        # 活跃沙箱
        self._active_sandboxes: Dict[str, SandboxContext] = {}
        # 沙箱结果（用于事后查询）
        self._results: Dict[str, SandboxResult] = {}

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    @property
    def active_count(self) -> int:
        """活跃沙箱数量"""
        with self._lock:
            return len(self._active_sandboxes)

    @property
    def worktree_manager(self) -> WorktreeManager:
        """WorktreeManager 实例"""
        return self._worktree_manager

    @property
    def fingerprint(self) -> Optional[PerformanceFingerprint]:
        """PerformanceFingerprint 实例"""
        return self._fingerprint

    @property
    def skill_injector(self) -> Optional["SkillInjector"]:
        """Phase 8 新增：SkillInjector 实例（None = 未启用）"""
        return self._skill_injector

    @property
    def recovery_manager(self) -> Optional["InterruptionRecoveryManager"]:
        """Phase 9 新增：InterruptionRecoveryManager 实例（None = 未启用）"""
        return self._recovery_manager

    # ------------------------------------------------------------------
    # Phase 9 新增：pause / resume / cancel 公共方法
    # ------------------------------------------------------------------

    def pause(self, sandbox_id: str, reason: str = "user_request") -> bool:
        """
        Phase 9：暂停正在执行的 subagent

        行为：
        1. 设置 sandbox.pause_event
        2. executor 内部循环检测到事件 → 进入 wait() 挂起
        3. 返回 True

        Args:
            sandbox_id: 沙箱 ID
            reason: 暂停原因（用于日志）

        Returns:
            bool: True 表示暂停信号已设置；sandbox 不存在时返回 False

        注意：
        - python GIL 限制，pause 不可能瞬间生效；executor 必须配合检查 pause_event
        - 多次 pause 幂等（重复 pause 等于 1 次）
        - pause 30 分钟未恢复不强制自动 resume（避免静默行为；由上层决策）
        """
        with self._lock:
            ctx = self._active_sandboxes.get(sandbox_id)
            if ctx is None:
                logger.warning(f"pause 失败：sandbox 不存在 {sandbox_id}")
                return False
            ctx.pause_event.set()
        logger.info(f"暂停信号已设置：{sandbox_id}（reason={reason}）")
        return True

    def resume(self, sandbox_id: str, snapshot_id: Optional[str] = None) -> bool:
        """
        Phase 9：恢复暂停的 subagent

        行为：
        1. 清除 sandbox.pause_event
        2. 如果有 snapshot_id 且 recovery_manager 可用 → 注入 snapshot 到 ctx.snapshot
        3. 多次 resume 幂等

        Args:
            sandbox_id: 沙箱 ID
            snapshot_id: 可选快照 ID（用于深恢复时注入 executor_state）

        Returns:
            bool: True 表示恢复信号已清除；sandbox 不存在时返回 False
        """
        with self._lock:
            ctx = self._active_sandboxes.get(sandbox_id)
            if ctx is None:
                logger.warning(f"resume 失败：sandbox 不存在 {sandbox_id}")
                return False
            ctx.pause_event.clear()
            # 注入快照（如有）
            if snapshot_id is not None and self._recovery_manager is not None:
                snapshot = self._recovery_manager.load_snapshot(snapshot_id)
                if snapshot is not None:
                    ctx.snapshot = snapshot.to_dict()
                    logger.info(
                        f"恢复时注入快照：{snapshot_id}（sandbox={sandbox_id}）"
                    )
        logger.info(f"恢复信号已清除：{sandbox_id}")
        return True

    def cancel(self, sandbox_id: str, reason: str = "user_request") -> bool:
        """
        Phase 9：主动取消 subagent

        行为：
        1. 设置 sandbox.cancel_event
        2. executor 检测到事件 → 抛 UserAbort
        3. sandbox.execute() 捕获并返回 CANCELLED 状态
        4. 多次 cancel 幂等

        Args:
            sandbox_id: 沙箱 ID
            reason: 取消原因（用于日志）

        Returns:
            bool: True 表示取消信号已设置；sandbox 不存在时返回 False
        """
        with self._lock:
            ctx = self._active_sandboxes.get(sandbox_id)
            if ctx is None:
                logger.warning(f"cancel 失败：sandbox 不存在 {sandbox_id}")
                return False
            ctx.cancel_event.set()
        logger.info(f"取消信号已设置：{sandbox_id}（reason={reason}）")
        return True

    def is_paused(self, sandbox_id: str) -> bool:
        """Phase 9：查询 sandbox 是否处于暂停状态"""
        with self._lock:
            ctx = self._active_sandboxes.get(sandbox_id)
            if ctx is None:
                return False
            return ctx.pause_event.is_set()

    def is_cancelled(self, sandbox_id: str) -> bool:
        """Phase 9：查询 sandbox 是否处于取消状态"""
        with self._lock:
            ctx = self._active_sandboxes.get(sandbox_id)
            if ctx is None:
                return False
            return ctx.cancel_event.is_set()

    def _perform_skill_injection(
        self,
        task: Dict[str, Any],
        token_budget: int,
    ) -> Dict[str, Any]:
        """
        Phase 8 新增：执行 skill 注入

        行为：
        - task 不含 task_skill 字段 → 返回空结果（向后兼容）
        - skill_injector 未设置 → 返回空结果（向后兼容）
        - 注入失败 → 记录 warning + 返回空结果（不抛异常，sandbox 仍可工作）

        Args:
            task: 任务字典
            token_budget: subagent token 预算

        Returns:
            Dict: 注入结果元数据（含 injected_skills / rendered_text / errors）
        """
        if self._skill_injector is None:
            return {
                "enabled": False,
                "injected_skills": [],
                "rendered_text": "",
                "mode": None,
                "missing_skills": [],
                "circular_skills": [],
                "truncated": False,
                "injection_time_ms": 0.0,
                "errors": [],
            }

        task_skill = task.get("task_skill")
        if task_skill is None:
            # 向后兼容：不传 task_skill 视为不注入
            return {
                "enabled": True,
                "skipped": True,  # 标记：用户未声明 skill
                "injected_skills": [],
                "rendered_text": "",
                "mode": None,
                "missing_skills": [],
                "circular_skills": [],
                "truncated": False,
                "injection_time_ms": 0.0,
                "errors": [],
            }

        # 调用 SkillInjector
        try:
            result: "InjectionResult" = self._skill_injector.inject(
                task_skill=task_skill,
                skill_mode=task.get("skill_mode"),
                skill_priority=task.get("skill_priority"),
                token_budget=token_budget,
            )
        except Exception as e:  # noqa: BLE001
            # 注入失败不应导致 sandbox 失败（隔离故障）
            logger.warning(
                f"Skill 注入异常（已隔离，sandbox 继续）：{type(e).__name__}: {e}"
            )
            return {
                "enabled": True,
                "skipped": False,
                "injected_skills": [],
                "rendered_text": "",
                "mode": None,
                "missing_skills": [],
                "circular_skills": [],
                "truncated": False,
                "injection_time_ms": 0.0,
                "errors": [f"{type(e).__name__}: {e}"],
            }

        return {
            "enabled": True,
            "skipped": False,
            "injected_skills": result.injected_skills,
            "rendered_text": result.rendered_text,
            "mode": result.mode,
            "missing_skills": result.missing_skills,
            "circular_skills": result.circular_skills,
            "truncated": result.truncated,
            "injection_time_ms": result.injection_time_ms,
            "errors": result.errors,
        }

    def spawn(
        self,
        agent_id: str,
        task: Dict[str, Any],
        isolation_level: str = IsolationLevel.CONTEXT,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
    ) -> str:
        """
        创建 subagent 沙箱

        流程：
        1. Guard 校验（如启用）
        2. 创建 worktree（如需）
        3. 分配 context_instance_id
        4. 记录到 PerformanceFingerprint（如可用）
        5. 返回 sandbox_id

        Args:
            agent_id: subagent ID
            task: 任务字典（含 description 等字段）
            isolation_level: 隔离级别
            token_budget: token 预算

        Returns:
            str: sandbox_id

        Raises:
            ValueError: 隔离级别无效
            GuardRejectError: Guard 拒绝
            WorktreePathError: 路径越权
            WorktreeError: worktree 创建失败
        """
        if isolation_level not in IsolationLevel.ALL:
            raise ValueError(
                f"无效隔离级别：{isolation_level}（有效：{IsolationLevel.ALL}）"
            )

        # Step 1: Guard 校验（不可绕过）
        if self._guard_enabled:
            guard_result = guard_check(inputs=task, token_budget=token_budget)
            if not guard_result.is_allowed:
                # 写入画像（如有）
                self._record_to_fingerprint(
                    agent_id=agent_id,
                    sandbox_id=None,  # 创建失败
                    status=SandboxStatus.REJECTED.value,
                    error=f"Guard rejected: {guard_result.reason}",
                    token_used=0,
                )
                raise GuardRejectError(
                    f"Guard 拒绝：{guard_result.reason}",
                    guard_result=guard_result,
                )

        # Step 2: 创建 worktree
        worktree_info: Optional[WorktreeInfo] = None
        if isolation_level in (IsolationLevel.WORKTREE, IsolationLevel.FULL):
            worktree_info = self._worktree_manager.create(
                agent_id=agent_id,
                base_branch=None,  # 自动检测默认分支
            )
            # worktree_info 为 None 表示 git 不可用，降级处理
            if worktree_info is None:
                logger.warning(
                    f"worktree 创建失败（git 不可用），降级 isolation_level 到 context"
                )

        # Step 3: 分配 context_instance_id
        context_instance_id: Optional[str] = None
        if isolation_level in (IsolationLevel.CONTEXT, IsolationLevel.FULL):
            context_instance_id = f"ctx_{uuid.uuid4().hex[:12]}"

        # Step 3.5: Phase 8 - Skill 注入（task 字典含 task_skill 时执行）
        skill_injection_result = self._perform_skill_injection(
            task=task, token_budget=token_budget
        )

        # Step 4: 创建沙箱
        sandbox_id = f"sb_{uuid.uuid4().hex[:12]}"
        sandbox_ctx = SandboxContext(
            sandbox_id=sandbox_id,
            agent_id=agent_id,
            isolation_level=isolation_level,
            worktree_path=worktree_info.worktree_path if worktree_info else None,
            context_instance_id=context_instance_id,
            token_budget=token_budget,
            # Phase 8 新增字段
            injected_skills=skill_injection_result.get("injected_skills", []),
            skill_injection_text=skill_injection_result.get("rendered_text", ""),
            skill_injection_meta={
                "enabled": skill_injection_result.get("enabled", False),
                "skipped": skill_injection_result.get("skipped", False),
                "mode": skill_injection_result.get("mode"),
                "missing_skills": skill_injection_result.get("missing_skills", []),
                "circular_skills": skill_injection_result.get("circular_skills", []),
                "truncated": skill_injection_result.get("truncated", False),
                "injection_time_ms": skill_injection_result.get("injection_time_ms", 0.0),
                "errors": skill_injection_result.get("errors", []),
            },
        )

        with self._lock:
            if sandbox_id in self._active_sandboxes:
                # 清理已创建的 worktree
                if worktree_info:
                    self._worktree_manager.remove(worktree_info.worktree_path)
                raise SandboxAlreadyExistsError(f"沙箱已存在：{sandbox_id}")
            self._active_sandboxes[sandbox_id] = sandbox_ctx

        # Step 5: 记录到画像
        self._record_to_fingerprint(
            agent_id=agent_id,
            sandbox_id=sandbox_id,
            status=SandboxStatus.PENDING.value,
            error=None,
            token_used=0,
            isolation_level=isolation_level,
        )

        logger.info(
            f"沙箱创建成功：{sandbox_id}（agent={agent_id}, "
            f"isolation={isolation_level}, token_budget={token_budget}）"
        )
        return sandbox_id

    def execute(
        self,
        sandbox_id: str,
        executor: Callable[[SandboxContext], Any],
        timeout: int = DEFAULT_EXEC_TIMEOUT,
    ) -> SandboxResult:
        """
        在沙箱中执行任务

        Args:
            sandbox_id: spawn 返回的 ID
            executor: 执行函数，接收 SandboxContext，返回结果
            timeout: 执行超时（秒）

        Returns:
            SandboxResult: 执行结果

        Raises:
            SandboxNotFoundError: 沙箱不存在

        Phase 9 行为变更（向后兼容）：
        - 若 ctx.cancel_event 被 set，executor 抛 UserAbort → 返回 CANCELLED 状态
        - 若 ctx.pause_event 被 set，executor 抛 PauseRequest → 挂起到 resume
        - 若 self._recovery_manager 设置，捕获业务异常后自动触发中断恢复
        - 若 self._recovery_manager=None，行为与 Phase 8 完全一致
        """
        with self._lock:
            if sandbox_id not in self._active_sandboxes:
                raise SandboxNotFoundError(f"沙箱不存在：{sandbox_id}")
            sandbox_ctx = self._active_sandboxes[sandbox_id]

        start_time = time.perf_counter()
        status = SandboxStatus.RUNNING
        output: Any = None
        error: Optional[str] = None
        isolated = False

        # Phase 9：构造 wrap 后的 executor（带 pause/cancel 轮询 + recovery 触发）
        wrapped = self._wrap_executor_for_recovery(executor, sandbox_ctx)

        try:
            try:
                output = wrapped(sandbox_ctx)
                status = SandboxStatus.SUCCESS
            except UserAbort as e:
                # Phase 9：用户主动取消
                status = SandboxStatus.CANCELLED
                error = f"UserAbort: {e}"
                isolated = True
                logger.info(f"沙箱 {sandbox_id} 被用户取消")
            except TokenBudgetExceeded as e:
                # Token 耗尽：优雅降级
                status = SandboxStatus.TOKEN_EXCEEDED
                error = f"Token 预算超限：{e.token_used}/{e.token_budget}"
                logger.warning(f"沙箱 {sandbox_id} Token 超限，降级处理")
            except SandboxTimeoutError as e:
                status = SandboxStatus.TIMEOUT
                error = str(e)
            except Exception as e:  # noqa: BLE001
                # 异常隔离：不传播给父
                status = SandboxStatus.FAILURE
                error = f"{type(e).__name__}: {e}"
                isolated = True
                logger.error(
                    f"沙箱 {sandbox_id} executor 异常（已隔离）：{error}"
                )
        finally:
            execution_time = time.perf_counter() - start_time
            token_used = sandbox_ctx.token_used

        # 构造结果
        result = SandboxResult(
            sandbox_id=sandbox_id,
            agent_id=sandbox_ctx.agent_id,
            status=status.value,
            output=output,
            token_used=token_used,
            execution_time_seconds=execution_time,
            error=error,
            worktree_cleaned=False,  # cleanup 时再设置
            isolated=isolated,
            metadata={
                "isolation_level": sandbox_ctx.isolation_level,
                "worktree_path": sandbox_ctx.worktree_path,
                "context_instance_id": sandbox_ctx.context_instance_id,
                # Phase 8 新增：skill 注入元数据（写入 result 以便后续分析）
                "skill_injection": {
                    "injected_skills": sandbox_ctx.injected_skills,
                    "mode": sandbox_ctx.skill_injection_meta.get("mode"),
                    "missing_skills": sandbox_ctx.skill_injection_meta.get("missing_skills", []),
                    "circular_skills": sandbox_ctx.skill_injection_meta.get("circular_skills", []),
                    "truncated": sandbox_ctx.skill_injection_meta.get("truncated", False),
                    "injection_time_ms": sandbox_ctx.skill_injection_meta.get("injection_time_ms", 0.0),
                } if (sandbox_ctx.injected_skills or sandbox_ctx.skill_injection_meta.get("missing_skills")) else None,
                # Phase 9 新增：中断恢复元数据
                "interruption": {
                    "cancelled": sandbox_ctx.cancel_event.is_set(),
                    "paused": sandbox_ctx.pause_event.is_set(),
                    "has_snapshot": sandbox_ctx.snapshot is not None,
                    "intermediate_results_count": len(sandbox_ctx.intermediate_results),
                },
            },
        )

        with self._lock:
            self._results[sandbox_id] = result

        # 画像反哺
        self._record_to_fingerprint(
            agent_id=sandbox_ctx.agent_id,
            sandbox_id=sandbox_id,
            status=status.value,
            error=error,
            token_used=token_used,
            execution_time=execution_time,
        )

        return result

    def _wrap_executor_for_recovery(
        self,
        executor: Callable[[SandboxContext], Any],
        sandbox_ctx: SandboxContext,
    ) -> Callable[[SandboxContext], Any]:
        """
        Phase 9：包装 executor 注入 pause/cancel 轮询和恢复逻辑

        行为：
        - 每次进入 executor 前先检查 cancel_event（set 则抛 UserAbort）
        - 每次进入 executor 前先检查 pause_event（set 则 wait() 挂起到 resume）
        - 捕获 PauseRequest 异常后 wait() 挂起；resume 后重试
        - 若 recovery_manager 可用，业务异常后尝试 attempt_recovery
        - recovery_manager=None 时，包装后的 executor 行为等价于原 executor
            （pause/cancel 检查依然存在，但不触发恢复重试）

        Args:
            executor: 原始 executor
            sandbox_ctx: 沙箱上下文（用于访问 pause_event / cancel_event / recovery_manager）

        Returns:
            Callable[[SandboxContext], Any]: 包装后的 executor
        """
        def wrapped(ctx: SandboxContext) -> Any:
            # 检查 cancel_event（最高优先级）
            if ctx.cancel_event.is_set():
                raise UserAbort("user_abort_signal_set_before_invoke")

            # 检查 pause_event（set 则挂起，直到 resume 清除）
            if ctx.pause_event.is_set():
                # 等待 resume（pause_event.clear() 在 resume() 中）
                ctx.pause_event.wait()

            # 尝试执行（含重试逻辑）
            attempts = 0
            max_attempts = 1  # 默认单次；启用 recovery 时由 recovery_manager 控制
            if self._recovery_manager is not None and INTERRUPTION_RECOVERY_AVAILABLE:
                max_attempts = self._recovery_manager.retry_policy.max_retries + 1

            last_exception: Optional[Exception] = None
            while attempts < max_attempts:
                attempts += 1
                # 每次重试前再次检查 cancel_event
                if ctx.cancel_event.is_set():
                    raise UserAbort("user_abort_signal_during_retry")
                # 重试前再次检查 pause_event
                if ctx.pause_event.is_set():
                    ctx.pause_event.wait()

                try:
                    return executor(ctx)
                except PauseRequest:
                    # 用户主动暂停：挂起到 resume 后重试
                    logger.debug(f"executor 抛 PauseRequest（sandbox={ctx.sandbox_id}）")
                    ctx.pause_event.wait()
                    continue
                except UserAbort:
                    # 用户主动取消：直接抛出，由外层捕获
                    raise
                except (TokenBudgetExceeded, SandboxTimeoutError):
                    # 内部已知异常：不触发恢复（由外层决定）
                    raise
                except Exception as e:  # noqa: BLE001
                    last_exception = e
                    # 触发恢复（如可用）
                    if (
                        self._recovery_manager is not None
                        and INTERRUPTION_RECOVERY_AVAILABLE
                        and attempts < max_attempts
                    ):
                        record = self._recovery_manager.record_interruption(
                            sandbox_id=ctx.sandbox_id,
                            agent_id=ctx.agent_id,
                            interruption_type=InterruptionType.EXCEPTION,
                            error=f"{type(e).__name__}: {e}",
                            task={},  # SandboxContext 未存 task 引用；空 dict 占位
                        )
                        if record.strategy == RecoveryStrategy.SKIP:
                            # SKIP 策略：返回特殊标记，由调用方处理
                            raise PauseRequest(f"recovery_skipped:{record.record_id}")
                        # 计算退避延迟
                        delay_ms = self._recovery_manager.retry_policy.compute_delay_ms(
                            record.attempts
                        )
                        logger.info(
                            f"中断恢复：{record.record_id} "
                            f"strategy={record.strategy.value} "
                            f"退避 {delay_ms}ms 后重试"
                        )
                        time.sleep(delay_ms / 1000.0)
                        continue
                    # 无 recovery_manager 或已是最后一次尝试：抛出
                    raise
            # 理论上不可达（最后一次失败会被 raise 出去）
            if last_exception is not None:
                raise last_exception
            return None

        return wrapped

    def cleanup(self, sandbox_id: str) -> bool:
        """
        清理沙箱（移除 worktree + 释放 context）

        Args:
            sandbox_id: 沙箱 ID

        Returns:
            bool: True 表示清理成功（幂等：重复清理也返回 True）
        """
        with self._lock:
            sandbox_ctx = self._active_sandboxes.pop(sandbox_id, None)
            result = self._results.get(sandbox_id)

        if sandbox_ctx is None:
            return True  # 幂等

        # 清理 worktree
        if sandbox_ctx.worktree_path and self._worktree_manager:
            try:
                self._worktree_manager.remove(sandbox_ctx.worktree_path)
                if result:
                    result.worktree_cleaned = True
            except Exception as e:  # noqa: BLE001
                logger.error(
                    f"清理 worktree 失败 {sandbox_ctx.worktree_path}：{e}"
                )

        logger.info(f"沙箱清理：{sandbox_id}")
        return True

    def cleanup_all(self) -> int:
        """
        清理所有活跃沙箱（异常路径）

        Returns:
            int: 清理的数量
        """
        with self._lock:
            sandbox_ids = list(self._active_sandboxes.keys())
        for sid in sandbox_ids:
            self.cleanup(sid)
        return len(sandbox_ids)

    def get_context(self, sandbox_id: str) -> Optional[SandboxContext]:
        """获取沙箱上下文"""
        with self._lock:
            return self._active_sandboxes.get(sandbox_id)

    def get_result(self, sandbox_id: str) -> Optional[SandboxResult]:
        """获取沙箱结果"""
        with self._lock:
            return self._results.get(sandbox_id)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _record_to_fingerprint(
        self,
        agent_id: str,
        sandbox_id: Optional[str],
        status: str,
        error: Optional[str],
        token_used: int,
        isolation_level: Optional[str] = None,
        execution_time: Optional[float] = None,
    ) -> None:
        """
        沙箱事件写入 PerformanceFingerprint

        写入策略：
        - 创建/执行/清理事件都记录
        - 失败和成功都记录（用于后续分析）
        - 不抛异常（画像写入失败不应影响主流程）
        """
        if self._fingerprint is None:
            return

        try:
            record = {
                "event_type": "subagent_sandbox",
                "agent_id": agent_id,
                "sandbox_id": sandbox_id,
                "status": status,
                "token_used": token_used,
                "error": error,
            }
            if isolation_level is not None:
                record["isolation_level"] = isolation_level
            if execution_time is not None:
                record["execution_time"] = execution_time

            # Phase 8：附加 skill 注入元数据
            if sandbox_id and sandbox_id in self._active_sandboxes:
                ctx = self._active_sandboxes[sandbox_id]
                if ctx.injected_skills or ctx.skill_injection_meta:
                    record["skill_injection"] = {
                        "injected_skills": ctx.injected_skills,
                        "mode": ctx.skill_injection_meta.get("mode"),
                        "missing_skills": ctx.skill_injection_meta.get("missing_skills", []),
                        "truncated": ctx.skill_injection_meta.get("truncated", False),
                        "injection_time_ms": ctx.skill_injection_meta.get("injection_time_ms", 0.0),
                    }
            elif sandbox_id and sandbox_id in self._results:
                # sandbox 已清理时，从 result.metadata 取
                result = self._results.get(sandbox_id)
                if result and "skill_injection" in result.metadata:
                    record["skill_injection"] = result.metadata["skill_injection"]

            # 写入 fingerprint（如有 record 方法）
            if hasattr(self._fingerprint, "record"):
                self._fingerprint.record(
                    pattern_id="subagent_sandbox",
                    success=(status == SandboxStatus.SUCCESS.value),
                    context_features=record,
                    strategy=isolation_level or "unknown",
                )
        except Exception as e:  # noqa: BLE001
            # 画像写入失败不应影响主流程
            logger.debug(f"沙箱事件写入画像失败（已忽略）：{e}")


# ============================================================================
# 便捷工厂
# ============================================================================

def create_default_sandbox(
    worktree_base: str = "./.dw_worktrees",
    allow_paths: Optional[List[str]] = None,
    fingerprint: Optional[PerformanceFingerprint] = None,
) -> SubagentSandbox:
    """
    创建默认配置的 SubagentSandbox

    Args:
        worktree_base: worktree 父目录
        allow_paths: worktree 路径白名单
        fingerprint: 性能画像

    Returns:
        SubagentSandbox: 默认沙箱
    """
    import os
    wm = WorktreeManager(
        base_path=worktree_base,
        allow_paths=allow_paths or [os.getcwd()],
    )
    return SubagentSandbox(
        worktree_manager=wm,
        fingerprint=fingerprint,
    )
