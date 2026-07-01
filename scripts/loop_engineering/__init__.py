"""Loop Engineering 包。

提供面向设计、编码、测试三类业务场景的闭环工程能力：
Discovery -> Handoff -> Verification -> Persistence -> Scheduling。

本包通过组合方式复用 multi-agent-team 现有组件：
- RalphLoopController：原子执行引擎
- PatternExecutor / PatternComposer：动态工作流模式
- SubagentSandbox / WorktreeManager：隔离与分发
- PerformanceFingerprint / FeedbackControlLoop：反馈与画像
- NotesMemory / RunState / CheckpointManager：持久化与续跑
"""

from loop_engineering.models import (
    DiscoveryMode,
    DiscoveryResult,
    EvaluationVerdict,
    EvaluatorMode,
    HandoffItem,
    HumanCheckpointResponse,
    LoopEngineeringConfig,
    LoopEvent,
    LoopEventType,
    LoopRunReport,
    LoopType,
    MemoryQuery,
    SchedulingDecision,
)

__all__ = [
    "LoopEngineeringConfig",
    "LoopType",
    "DiscoveryMode",
    "EvaluatorMode",
    "LoopEventType",
    "DiscoveryResult",
    "HandoffItem",
    "EvaluationVerdict",
    "LoopEvent",
    "MemoryQuery",
    "SchedulingDecision",
    "HumanCheckpointResponse",
    "LoopRunReport",
]
