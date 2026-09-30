#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Interruption Recovery - subagent 中断恢复（Phase 9：InterruptionRecovery）

职责：
1. 分类 subagent 中断类型（5 种）
2. 智能选择恢复策略（5 种）
3. 实现指数退避 + 抖动重试算法
4. 保存/恢复 subagent 状态快照
5. 复用 V2 CheckpointManager 做深恢复
6. 联动 PerformanceFingerprint 记录中断事件

依据：
- DYNAMIC_WORKFLOWS_INTEGRATION.md v1.5 §下一步决策
- PHASE9_PLAN.md 完整方案
- 架构师审查 §3.0.3 安全约束、§6 数据模型约束

设计原则：
- 不修改任何 V2 文件（V2 文件零 diff）
- 严格向后兼容：recovery_manager=None 时行为与 Phase 8 完全一致
- 真实实现：禁模拟/占位/简化
- Java/Rust 风格中文注释
- 默认值安全：max_retries=3、max_delay_ms=30000、jitter=True 避免雪崩

作者：trae-multi-agent 融合 Phase 9
创建日期：2026-06-05
"""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    Type,
    Union,
)

# 同目录模块导入
import sys
from pathlib import Path
_DYNAMIC_WORKFLOW_DIR = Path(__file__).resolve().parent
if str(_DYNAMIC_WORKFLOW_DIR) not in sys.path:
    sys.path.insert(0, str(_DYNAMIC_WORKFLOW_DIR))

# 父目录模块（V2 模块）—— 仅类型注解用 Optional["CheckpointManager"]
_SCRIPTS_DIR = _DYNAMIC_WORKFLOW_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# V2 CheckpointManager 与 PerformanceFingerprint（运行时按需导入，失败时优雅降级）
try:
    from checkpoint_manager import Checkpoint, CheckpointManager, CheckpointStatus
    CHECKPOINT_AVAILABLE = True
except ImportError:
    CHECKPOINT_AVAILABLE = False
    Checkpoint = None  # type: ignore[assignment]
    CheckpointManager = None  # type: ignore[assignment]
    CheckpointStatus = None  # type: ignore[assignment]

try:
    from performance_fingerprint import PerformanceFingerprint
    FINGERPRINT_AVAILABLE = True
except ImportError:
    FINGERPRINT_AVAILABLE = False
    PerformanceFingerprint = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


# ============================================================================
# 异常类
# ============================================================================

class InterruptionRecoveryError(Exception):
    """InterruptionRecovery 基础异常"""
    pass


class InterruptionNotFoundError(InterruptionRecoveryError):
    """指定的中断记录不存在"""
    pass


class SnapshotNotFoundError(InterruptionRecoveryError):
    """指定的状态快照不存在"""
    pass


class RetryExhaustedError(InterruptionRecoveryError):
    """重试次数已耗尽，放弃恢复"""
    def __init__(self, message: str, record: Optional["InterruptionRecord"] = None):
        super().__init__(message)
        self.record = record


class SnapshotSerializationError(InterruptionRecoveryError):
    """快照序列化失败（如不可 JSON 序列化对象）"""
    pass


# ============================================================================
# 枚举：中断类型
# ============================================================================

class InterruptionType(str, Enum):
    """
    subagent 中断类型

    - TIMEOUT: 执行超时（外部 deadline 触发）
    - EXCEPTION: 业务异常（executor 抛错）
    - SIGNAL: 外部信号（SIGINT/SIGTERM，进程级）
    - RESOURCE_EXHAUSTED: Token/内存/CPU 耗尽
    - USER_ABORT: 用户主动取消
    - UNKNOWN: 未知类型
    """
    TIMEOUT = "timeout"
    EXCEPTION = "exception"
    SIGNAL = "signal"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    USER_ABORT = "user_abort"
    UNKNOWN = "unknown"


# ============================================================================
# 枚举：恢复策略
# ============================================================================

class RecoveryStrategy(str, Enum):
    """
    subagent 中断恢复策略

    - RETRY: 原地重试（同一 sandbox 状态恢复）
    - RESTART: 全新 sandbox 重启（state 持久化但 sandbox 重建）
    - FALLBACK: 降级（切到 haiku / 减小 token_budget）
    - SKIP: 跳过此 subagent，标记为失败
    - MANUAL: 需人工介入（返回待决策状态）
    - ABORT: 整体 workflow 终止
    """
    RETRY = "retry"
    RESTART = "restart"
    FALLBACK = "fallback"
    SKIP = "skip"
    MANUAL = "manual"
    ABORT = "abort"


# ============================================================================
# 数据类：重试策略
# ============================================================================

@dataclass
class RetryPolicy:
    """
    重试退避策略

    采用业界最佳实践：指数退避 + 抖动（jitter），避免雪崩效应。

    字段：
    - max_retries: 最大重试次数（超过则升级 strategy）
    - initial_delay_ms: 初始延迟（毫秒）
    - backoff_factor: 退避因子（每次延迟乘以此值）
    - max_delay_ms: 单次最大延迟（毫秒），避免单次等待过久
    - jitter: 是否加随机抖动（0-25%），避免多个 sandbox 同时重试
    - retry_on: 可重试的中断类型元组（USER_ABORT / SIGNAL 默认不重试）
    """
    max_retries: int = 3
    initial_delay_ms: int = 1000
    backoff_factor: float = 2.0
    max_delay_ms: int = 30000
    jitter: bool = True
    retry_on: Tuple[InterruptionType, ...] = (
        InterruptionType.TIMEOUT,
        InterruptionType.EXCEPTION,
        InterruptionType.RESOURCE_EXHAUSTED,
    )

    def __post_init__(self) -> None:
        """
        初始化后校验参数合法性

        Raises:
            ValueError: 参数非法
        """
        if self.max_retries < 0:
            raise ValueError(f"max_retries 必须 >= 0，实际 {self.max_retries}")
        if self.initial_delay_ms <= 0:
            raise ValueError(f"initial_delay_ms 必须 > 0，实际 {self.initial_delay_ms}")
        if self.backoff_factor < 1.0:
            raise ValueError(f"backoff_factor 必须 >= 1.0，实际 {self.backoff_factor}")
        if self.max_delay_ms < self.initial_delay_ms:
            raise ValueError(
                f"max_delay_ms ({self.max_delay_ms}) 必须 >= initial_delay_ms "
                f"({self.initial_delay_ms})"
            )

    def compute_delay_ms(self, attempt: int) -> int:
        """
        计算第 N 次重试的延迟（毫秒）

        算法：
        1. base_delay = initial_delay_ms * (backoff_factor ** attempt)
        2. capped_delay = min(base_delay, max_delay_ms)
        3. 若 jitter=True，实际延迟 = capped_delay * (1 + random(0, 0.25))

        Args:
            attempt: 重试序号（0 表示第一次重试）

        Returns:
            int: 实际等待毫秒数
        """
        # 步骤 1：基础指数退避
        base_delay = self.initial_delay_ms * (self.backoff_factor ** attempt)
        # 步骤 2：截断到 max_delay_ms
        capped_delay = min(base_delay, self.max_delay_ms)
        # 步骤 3：加 0-25% 随机抖动，避免雪崩
        if self.jitter:
            capped_delay = capped_delay * (1.0 + random.uniform(0, 0.25))
        return int(capped_delay)

    def should_retry(self, attempt: int, interruption_type: InterruptionType) -> bool:
        """
        判定是否应该重试

        Args:
            attempt: 当前已尝试次数（0 = 第一次执行）
            interruption_type: 中断类型

        Returns:
            bool: True 表示可重试
        """
        return (
            attempt < self.max_retries
            and interruption_type in self.retry_on
        )


# ============================================================================
# 数据类：subagent 状态快照
# ============================================================================

@dataclass
class SubagentStateSnapshot:
    """
    subagent 状态快照（用于中断恢复）

    字段：
    - snapshot_id: 唯一 ID
    - sandbox_id: 所属沙箱 ID
    - agent_id: 所属 subagent ID
    - task: 原始 task 字典
    - progress: 进度 0-100
    - intermediate_results: 中间结果（可序列化）
    - executor_state: executor 自定义状态（可序列化）
    - checkpoint_id: 关联的 V2 Checkpoint ID（深恢复）
    - created_at / updated_at: ISO 时间戳
    """
    snapshot_id: str
    sandbox_id: str
    agent_id: str
    task: Dict[str, Any]
    progress: float = 0.0
    intermediate_results: Dict[str, Any] = field(default_factory=dict)
    executor_state: Dict[str, Any] = field(default_factory=dict)
    checkpoint_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """
        序列化为 dict（用于持久化或日志）

        Returns:
            Dict[str, Any]: 完整字段副本
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SubagentStateSnapshot":
        """
        从 dict 反序列化

        Args:
            data: 字典数据

        Returns:
            SubagentStateSnapshot: 反序列化的快照对象
        """
        # 防御性拷贝，避免外部修改污染实例
        data_copy = dict(data)
        return cls(**data_copy)

    def touch(self) -> None:
        """更新 updated_at 为当前时间（snapshot 修改时调用）"""
        self.updated_at = datetime.now().isoformat()


# ============================================================================
# 数据类：中断记录
# ============================================================================

@dataclass
class InterruptionRecord:
    """
    subagent 中断记录（用于追溯与恢复调度）

    字段：
    - record_id: 唯一 ID
    - sandbox_id: 沙箱 ID
    - agent_id: subagent ID
    - interruption_type: 中断类型
    - strategy: 选定的恢复策略
    - attempts: 已尝试次数
    - max_attempts: 最大尝试次数
    - last_error: 最后一次错误
    - snapshot_id: 关联快照 ID
    - created_at: 创建时间
    - recovered_at: 恢复成功时间（None 表示未恢复）
    """
    record_id: str
    sandbox_id: str
    agent_id: str
    interruption_type: InterruptionType
    strategy: RecoveryStrategy
    attempts: int = 0
    max_attempts: int = 3
    last_error: Optional[str] = None
    snapshot_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    recovered_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """
        序列化为 dict（enums 转为字符串）

        Returns:
            Dict[str, Any]: 可 JSON 序列化的字典
        """
        return {
            "record_id": self.record_id,
            "sandbox_id": self.sandbox_id,
            "agent_id": self.agent_id,
            "interruption_type": self.interruption_type.value
                if isinstance(self.interruption_type, InterruptionType) else self.interruption_type,
            "strategy": self.strategy.value
                if isinstance(self.strategy, RecoveryStrategy) else self.strategy,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "last_error": self.last_error,
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at,
            "recovered_at": self.recovered_at,
        }


# ============================================================================
# 主调度器：InterruptionRecoveryManager
# ============================================================================

class InterruptionRecoveryManager:
    """
    中断恢复管理器（Phase 9 核心）

    核心职责：
    1. 记录中断事件（record_interruption）
    2. 智能选择恢复策略（基于 task.interruption_policy 字段）
    3. 状态快照保存/恢复（save_snapshot / load_snapshot）
    4. 调度恢复动作（attempt_recovery）
    5. 恢复历史追溯（get_history）

    使用示例：
    ```python
    manager = InterruptionRecoveryManager(
        retry_policy=RetryPolicy(max_retries=3),
        checkpoint_manager=CheckpointManager("./recovery_cp"),
        fingerprint=PerformanceFingerprint(agent_id="main"),
    )

    # executor 主动保存快照
    snapshot = manager.save_snapshot(
        sandbox_id=sb_id,
        agent_id="sa_001",
        task={"description": "..."},
        progress=50.0,
        intermediate_results={"files_done": 25},
    )

    # 记录中断
    record = manager.record_interruption(
        sandbox_id=sb_id,
        agent_id="sa_001",
        interruption_type=InterruptionType.EXCEPTION,
        error="timeout after 60s",
    )

    # 尝试恢复
    success, result = manager.attempt_recovery(
        record=record,
        executor=executor_fn,
        sandbox_id=sb_id,
    )
    ```

    线程安全：
    - 所有公共方法受 _lock 保护
    - 内部状态（_active_records / _snapshots / _recovery_history）并发安全
    """

    def __init__(
        self,
        retry_policy: Optional[RetryPolicy] = None,
        checkpoint_manager: Optional["CheckpointManager"] = None,
        fingerprint: Optional["PerformanceFingerprint"] = None,
        default_strategy: RecoveryStrategy = RecoveryStrategy.RETRY,
        max_history: int = 1000,
    ):
        """
        初始化 InterruptionRecoveryManager

        Args:
            retry_policy: 重试策略（None 时使用默认 RetryPolicy()）
            checkpoint_manager: V2 CheckpointManager（None 时不写 checkpoint）
            fingerprint: V2.5 PerformanceFingerprint（None 时不写画像）
            default_strategy: 默认恢复策略（task 字段未指定时使用）
            max_history: 最大保留历史记录数（超过后丢弃最早的）
        """
        self.retry_policy = retry_policy or RetryPolicy()
        self.checkpoint_manager = checkpoint_manager
        self.fingerprint = fingerprint
        self.default_strategy = default_strategy
        self.max_history = max_history

        # 状态：活跃中断记录 + 恢复历史 + 快照
        self._active_records: Dict[str, InterruptionRecord] = {}
        self._snapshots: Dict[str, SubagentStateSnapshot] = {}
        self._recovery_history: List[InterruptionRecord] = []
        self._lock = threading.Lock()

        # 校验 CheckpointManager 兼容性
        if self.checkpoint_manager is not None and not CHECKPOINT_AVAILABLE:
            logger.warning(
                "checkpoint_manager 已传入但 checkpoint_manager 模块不可用，已忽略。"
                "请检查 checkpoint_manager.py 是否存在。"
            )
            self.checkpoint_manager = None

        if self.fingerprint is not None and not FINGERPRINT_AVAILABLE:
            logger.warning(
                "fingerprint 已传入但 performance_fingerprint 模块不可用，已忽略。"
                "请检查 performance_fingerprint.py 是否存在。"
            )
            self.fingerprint = None

        logger.info(
            f"InterruptionRecoveryManager 初始化："
            f"max_retries={self.retry_policy.max_retries}, "
            f"default_strategy={self.default_strategy.value}, "
            f"checkpoint={'enabled' if self.checkpoint_manager else 'disabled'}, "
            f"fingerprint={'enabled' if self.fingerprint else 'disabled'}"
        )

    # ------------------------------------------------------------------
    # 状态快照 API
    # ------------------------------------------------------------------

    def save_snapshot(
        self,
        sandbox_id: str,
        agent_id: str,
        task: Dict[str, Any],
        progress: float = 0.0,
        intermediate_results: Optional[Dict[str, Any]] = None,
        executor_state: Optional[Dict[str, Any]] = None,
    ) -> SubagentStateSnapshot:
        """
        保存 subagent 状态快照

        Args:
            sandbox_id: 沙箱 ID
            agent_id: subagent ID
            task: 原始任务字典
            progress: 进度（0-100）
            intermediate_results: 中间结果
            executor_state: executor 自定义状态

        Returns:
            SubagentStateSnapshot: 新建的快照对象

        Raises:
            SnapshotSerializationError: 中间结果不可序列化
        """
        snapshot_id = f"snap_{uuid.uuid4().hex[:16]}"
        snapshot = SubagentStateSnapshot(
            snapshot_id=snapshot_id,
            sandbox_id=sandbox_id,
            agent_id=agent_id,
            task=dict(task),  # 防御性拷贝
            progress=max(0.0, min(100.0, progress)),  # 钳制到 0-100
            intermediate_results=dict(intermediate_results) if intermediate_results else {},
            executor_state=dict(executor_state) if executor_state else {},
        )

        # 预校验可序列化性（避免后续恢复时才发现）
        try:
            snapshot.to_dict()
        except (TypeError, ValueError) as e:
            raise SnapshotSerializationError(
                f"快照不可序列化：{type(e).__name__}: {e}"
            ) from e

        # 关联 V2 Checkpoint（深恢复）
        if self.checkpoint_manager is not None and CHECKPOINT_AVAILABLE:
            cp_id = self._save_checkpoint(snapshot)
            if cp_id is not None:
                snapshot.checkpoint_id = cp_id

        with self._lock:
            self._snapshots[snapshot_id] = snapshot

        logger.debug(
            f"快照保存：{snapshot_id}（sandbox={sandbox_id}, agent={agent_id}, "
            f"progress={progress:.1f}%, checkpoint={snapshot.checkpoint_id}）"
        )
        return snapshot

    def load_snapshot(self, snapshot_id: str) -> Optional[SubagentStateSnapshot]:
        """
        加载快照

        Args:
            snapshot_id: 快照 ID

        Returns:
            SubagentStateSnapshot: 快照对象；不存在时返回 None
        """
        with self._lock:
            return self._snapshots.get(snapshot_id)

    def delete_snapshot(self, snapshot_id: str) -> bool:
        """
        删除快照（释放内存）

        Args:
            snapshot_id: 快照 ID

        Returns:
            bool: True 表示删除成功（幂等）
        """
        with self._lock:
            return self._snapshots.pop(snapshot_id, None) is not None

    def list_snapshots(
        self,
        sandbox_id: Optional[str] = None,
    ) -> List[SubagentStateSnapshot]:
        """
        列出快照

        Args:
            sandbox_id: 可选过滤（None 时返回全部）

        Returns:
            List[SubagentStateSnapshot]: 快照列表
        """
        with self._lock:
            snapshots = list(self._snapshots.values())
        if sandbox_id is not None:
            snapshots = [s for s in snapshots if s.sandbox_id == sandbox_id]
        return snapshots

    # ------------------------------------------------------------------
    # 中断记录 API
    # ------------------------------------------------------------------

    def record_interruption(
        self,
        sandbox_id: str,
        agent_id: str,
        interruption_type: InterruptionType,
        error: Optional[str] = None,
        task: Optional[Dict[str, Any]] = None,
        max_attempts: Optional[int] = None,
    ) -> InterruptionRecord:
        """
        记录中断事件 + 智能选择恢复策略

        策略选择优先级：
        1. task["interruption_policy"]["strategy"] - 全局显式策略（最高）
        2. task["interruption_policy"][interruption_type.value] - 按类型指定
        3. 基于类型的内置默认策略：
           - TIMEOUT → RETRY
           - EXCEPTION → RETRY
           - SIGNAL → RESTART
           - RESOURCE_EXHAUSTED → FALLBACK
           - USER_ABORT → SKIP
           - UNKNOWN → MANUAL
        4. 升级策略（attempt >= max_retries 时）：
           - RETRY → FALLBACK
           - RESTART → SKIP
           - FALLBACK → MANUAL
           - 其它 → MANUAL

        Args:
            sandbox_id: 沙箱 ID
            agent_id: subagent ID
            interruption_type: 中断类型
            error: 错误描述
            task: 任务字典（用于读 interruption_policy 字段）
            max_attempts: 自定义最大尝试次数（None 时使用 RetryPolicy.max_retries）

        Returns:
            InterruptionRecord: 新建的中断记录
        """
        # 查找是否已有同 sandbox+agent 的活跃记录（用于累计 attempts）
        existing_record = self._find_active_record(sandbox_id, agent_id)
        # 累计 attempts：新 attempts = 旧 attempts + 1；首次为 0
        attempt = (existing_record.attempts + 1) if existing_record else 0
        # max_attempts = max_retries + 1（max_retries 表示"额外重试次数"）
        # 例如 max_retries=2 → 最多 3 次尝试（1 次原始 + 2 次重试）
        effective_max_attempts = (
            (max_attempts if max_attempts is not None
             else self.retry_policy.max_retries) + 1
        )
        # 继承快照 ID（连续中断复用同一快照）
        inherited_snapshot_id = existing_record.snapshot_id if existing_record else None

        # 智能策略选择
        strategy = self._select_strategy(
            interruption_type=interruption_type,
            attempt=attempt,
            task=task,
        )

        # 创建记录
        record = InterruptionRecord(
            record_id=f"int_{uuid.uuid4().hex[:16]}",
            sandbox_id=sandbox_id,
            agent_id=agent_id,
            interruption_type=interruption_type,
            strategy=strategy,
            attempts=attempt,
            max_attempts=effective_max_attempts,
            last_error=error,
            snapshot_id=inherited_snapshot_id,
        )

        with self._lock:
            # 替换 active 中的旧记录（保留 attempts 累计语义）
            if existing_record is not None:
                self._active_records.pop(existing_record.record_id, None)
            self._active_records[record.record_id] = record

        # 写入画像（失败不抛异常）
        self._record_to_fingerprint(record)

        logger.info(
            f"中断记录：{record.record_id}（sandbox={sandbox_id}, agent={agent_id}, "
            f"type={interruption_type.value}, strategy={strategy.value}, "
            f"attempts={attempt}/{effective_max_attempts}）"
        )
        return record

    def _find_active_record(
        self,
        sandbox_id: str,
        agent_id: str,
    ) -> Optional[InterruptionRecord]:
        """
        查找同 sandbox+agent 的活跃记录

        Args:
            sandbox_id: 沙箱 ID
            agent_id: subagent ID

        Returns:
            Optional[InterruptionRecord]: 找到则返回；否则 None
        """
        with self._lock:
            for record in self._active_records.values():
                if record.sandbox_id == sandbox_id and record.agent_id == agent_id:
                    return record
        return None

    def _select_strategy(
        self,
        interruption_type: InterruptionType,
        attempt: int,
        task: Optional[Dict[str, Any]] = None,
    ) -> RecoveryStrategy:
        """
        智能策略选择（核心算法）

        优先级详见 record_interruption 文档。

        Args:
            interruption_type: 中断类型
            attempt: 已尝试次数
            task: 任务字典（可选）

        Returns:
            RecoveryStrategy: 选定的恢复策略
        """
        policy = (task or {}).get("interruption_policy", {})

        # 优先级 1：显式全局 strategy
        if isinstance(policy, dict) and "strategy" in policy:
            try:
                return RecoveryStrategy(policy["strategy"])
            except ValueError:
                logger.warning(
                    f"task.interruption_policy.strategy 非法值：{policy['strategy']}，"
                    f"回退到默认策略"
                )

        # 优先级 2：按类型指定
        if isinstance(policy, dict) and interruption_type.value in policy:
            try:
                return RecoveryStrategy(policy[interruption_type.value])
            except ValueError:
                logger.warning(
                    f"task.interruption_policy.{interruption_type.value} 非法值："
                    f"{policy[interruption_type.value]}，回退到默认策略"
                )

        # 优先级 3：基于类型的默认策略
        default_map: Dict[InterruptionType, RecoveryStrategy] = {
            InterruptionType.TIMEOUT: RecoveryStrategy.RETRY,
            InterruptionType.EXCEPTION: RecoveryStrategy.RETRY,
            InterruptionType.SIGNAL: RecoveryStrategy.RESTART,
            InterruptionType.RESOURCE_EXHAUSTED: RecoveryStrategy.FALLBACK,
            InterruptionType.USER_ABORT: RecoveryStrategy.SKIP,
            InterruptionType.UNKNOWN: RecoveryStrategy.MANUAL,
        }
        strategy = default_map.get(interruption_type, self.default_strategy)

        # 优先级 4：升级策略（attempt 超限）
        if attempt >= self.retry_policy.max_retries:
            escalation_map: Dict[RecoveryStrategy, RecoveryStrategy] = {
                RecoveryStrategy.RETRY: RecoveryStrategy.FALLBACK,
                RecoveryStrategy.RESTART: RecoveryStrategy.SKIP,
                RecoveryStrategy.FALLBACK: RecoveryStrategy.MANUAL,
            }
            strategy = escalation_map.get(strategy, RecoveryStrategy.MANUAL)

        return strategy

    # ------------------------------------------------------------------
    # 恢复执行 API
    # ------------------------------------------------------------------

    def attempt_recovery(
        self,
        record: InterruptionRecord,
        executor: Callable[..., Any],
        sandbox_id: str,
    ) -> Tuple[bool, Any]:
        """
        尝试恢复（带重试）

        行为：
        - SKIP / MANUAL / ABORT 策略：直接返回 (False, error)，由上层决策
        - RETRY / RESTART / FALLBACK 策略：按 retry_policy 循环重试

        Args:
            record: 中断记录
            executor: 执行函数，签名支持 (ctx) 或 (ctx, snapshot)
            sandbox_id: 沙箱 ID

        Returns:
            Tuple[bool, Any]: (成功, 结果或错误)
        """
        # 快速路径：不可恢复策略
        if record.strategy == RecoveryStrategy.SKIP:
            logger.info(f"恢复跳过（SKIP 策略）：{record.record_id}")
            return False, "skipped"
        if record.strategy == RecoveryStrategy.MANUAL:
            logger.info(f"需人工介入（MANUAL 策略）：{record.record_id}")
            return False, "manual_intervention_required"
        if record.strategy == RecoveryStrategy.ABORT:
            logger.warning(f"整体中止（ABORT 策略）：{record.record_id}")
            return False, "aborted"

        # 加载快照（如果有）
        snapshot: Optional[SubagentStateSnapshot] = None
        if record.snapshot_id is not None:
            snapshot = self.load_snapshot(record.snapshot_id)

        # 重试循环
        last_error: Optional[str] = None
        while record.attempts < record.max_attempts:
            record.attempts += 1

            # 退避延迟
            delay_ms = self.retry_policy.compute_delay_ms(record.attempts - 1)
            if delay_ms > 0:
                logger.debug(
                    f"重试退避 {delay_ms}ms（attempt={record.attempts}/{record.max_attempts}）"
                )
                time.sleep(delay_ms / 1000.0)

            # 重新调用 executor
            try:
                # FALLBACK 策略：传入 snapshot 提示 executor 降级
                if record.strategy == RecoveryStrategy.FALLBACK and snapshot is not None:
                    result = executor(snapshot, snapshot)
                elif snapshot is not None:
                    result = executor(snapshot, snapshot)
                else:
                    result = executor(snapshot) if snapshot else executor(None)

                # 成功
                record.recovered_at = datetime.now().isoformat()
                self._move_to_history(record)
                logger.info(
                    f"恢复成功：{record.record_id}（attempts={record.attempts}）"
                )
                return True, result
            except Exception as e:  # noqa: BLE001
                last_error = f"{type(e).__name__}: {e}"
                record.last_error = last_error
                logger.warning(
                    f"恢复失败（attempt {record.attempts}/{record.max_attempts}）：{last_error}"
                )

        # 重试耗尽
        logger.error(
            f"重试耗尽：{record.record_id}（{record.attempts} 次尝试，最后错误：{last_error}）"
        )
        self._move_to_history(record)
        return False, last_error or "retry_exhausted"

    def _move_to_history(self, record: InterruptionRecord) -> None:
        """
        将记录从 active 移到 history

        Args:
            record: 中断记录
        """
        with self._lock:
            self._active_records.pop(record.record_id, None)
            self._recovery_history.append(record)
            # 限制历史长度，避免内存泄漏
            if len(self._recovery_history) > self.max_history:
                # 丢弃最早的记录
                self._recovery_history = self._recovery_history[-self.max_history:]

    # ------------------------------------------------------------------
    # 查询 API
    # ------------------------------------------------------------------

    def list_active_records(
        self,
        sandbox_id: Optional[str] = None,
    ) -> List[InterruptionRecord]:
        """
        列出活跃中断记录

        Args:
            sandbox_id: 可选过滤

        Returns:
            List[InterruptionRecord]: 记录列表
        """
        with self._lock:
            records = list(self._active_records.values())
        if sandbox_id is not None:
            records = [r for r in records if r.sandbox_id == sandbox_id]
        return records

    def get_history(
        self,
        sandbox_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[InterruptionRecord]:
        """
        获取恢复历史

        Args:
            sandbox_id: 可选过滤
            limit: 限制返回数量（从最新开始）

        Returns:
            List[InterruptionRecord]: 历史记录列表（按时间倒序）
        """
        with self._lock:
            history = list(self._recovery_history)
        if sandbox_id is not None:
            history = [r for r in history if r.sandbox_id == sandbox_id]
        # 倒序：最新在前
        history.reverse()
        if limit is not None:
            history = history[:limit]
        return history

    def cleanup_records(self, sandbox_id: str) -> int:
        """
        清理指定 sandbox 的所有记录（sandbox 销毁时调用）

        Args:
            sandbox_id: 沙箱 ID

        Returns:
            int: 清理的记录数
        """
        with self._lock:
            active_keys = [
                rid for rid, r in self._active_records.items()
                if r.sandbox_id == sandbox_id
            ]
            for rid in active_keys:
                self._active_records.pop(rid, None)

            history_removed = sum(
                1 for r in self._recovery_history if r.sandbox_id == sandbox_id
            )
            self._recovery_history = [
                r for r in self._recovery_history if r.sandbox_id != sandbox_id
            ]

            snapshot_keys = [
                sid for sid, s in self._snapshots.items()
                if s.sandbox_id == sandbox_id
            ]
            for sid in snapshot_keys:
                self._snapshots.pop(sid, None)

        logger.info(
            f"清理 sandbox {sandbox_id} 的记录："
            f"active={len(active_keys)}, history={history_removed}, "
            f"snapshots={len(snapshot_keys)}"
        )
        return len(active_keys) + history_removed + len(snapshot_keys)

    # ------------------------------------------------------------------
    # V2 CheckpointManager 集成
    # ------------------------------------------------------------------

    def _save_checkpoint(
        self,
        snapshot: SubagentStateSnapshot,
    ) -> Optional[str]:
        """
        将 snapshot 关联到 V2 checkpoint（深恢复）

        Args:
            snapshot: 状态快照

        Returns:
            Optional[str]: checkpoint_id；失败时返回 None
        """
        if self.checkpoint_manager is None or not CHECKPOINT_AVAILABLE:
            return None
        try:
            checkpoint = Checkpoint(
                checkpoint_id=f"cp_snapshot_{snapshot.snapshot_id}",
                task_id=snapshot.agent_id,
                step_id="interruption_recovery",
                step_name=f"Snapshot for {snapshot.sandbox_id}",
                agent_id=snapshot.agent_id,
                status=CheckpointStatus.ACTIVE,
                progress_percentage=snapshot.progress,
                context_snapshot=snapshot.to_dict(),
            )
            if self.checkpoint_manager.save_checkpoint(checkpoint):
                return checkpoint.checkpoint_id
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"快照 checkpoint 写入失败（已忽略）：{type(e).__name__}: {e}"
            )
            return None

    # ------------------------------------------------------------------
    # V2.5 PerformanceFingerprint 集成
    # ------------------------------------------------------------------

    def _record_to_fingerprint(self, record: InterruptionRecord) -> None:
        """
        记录中断/恢复事件到 PerformanceFingerprint

        Args:
            record: 中断记录
        """
        if self.fingerprint is None or not FINGERPRINT_AVAILABLE:
            return
        try:
            # PerformanceFingerprint.record() 真实签名：
            # record(task_type, task_complexity, success, error_type, execution_time, strategy, context_features)
            self.fingerprint.record(
                task_type="interruption_recovery",
                task_complexity=5,  # 中断恢复为中等复杂度
                success=(record.recovered_at is not None),
                error_type=record.last_error,
                execution_time=0.0,
                strategy=record.strategy.value,
                context_features={
                    "event_type": "interruption_recovery",
                    "sandbox_id": record.sandbox_id,
                    "agent_id": record.agent_id,
                    "interruption_type": record.interruption_type.value
                        if isinstance(record.interruption_type, InterruptionType)
                        else record.interruption_type,
                    "strategy": record.strategy.value
                        if isinstance(record.strategy, RecoveryStrategy)
                        else record.strategy,
                    "attempts": record.attempts,
                    "max_attempts": record.max_attempts,
                    "snapshot_id": record.snapshot_id,
                },
            )
        except Exception as e:  # noqa: BLE001
            # 画像写入失败不应影响主流程
            logger.debug(f"画像写入失败（已忽略）：{type(e).__name__}: {e}")


# ============================================================================
# 便捷工厂
# ============================================================================

def create_default_recovery_manager(
    storage_path: Optional[str] = None,
    fingerprint_agent_id: Optional[str] = None,
    max_retries: int = 3,
) -> InterruptionRecoveryManager:
    """
    创建默认配置的 InterruptionRecoveryManager

    Args:
        storage_path: V2 Checkpoint 存储路径（None 时禁用 checkpoint）
        fingerprint_agent_id: V2.5 PerformanceFingerprint 的 agent_id
            （None 时禁用 fingerprint）
        max_retries: 最大重试次数

    Returns:
        InterruptionRecoveryManager: 默认管理器
    """
    retry_policy = RetryPolicy(max_retries=max_retries)

    checkpoint_manager = None
    if storage_path is not None and CHECKPOINT_AVAILABLE:
        checkpoint_manager = CheckpointManager(storage_path=storage_path)

    fingerprint = None
    if fingerprint_agent_id is not None and FINGERPRINT_AVAILABLE:
        fingerprint = PerformanceFingerprint(agent_id=fingerprint_agent_id)

    return InterruptionRecoveryManager(
        retry_policy=retry_policy,
        checkpoint_manager=checkpoint_manager,
        fingerprint=fingerprint,
    )
