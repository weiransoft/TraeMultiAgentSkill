"""Phase 18: Ralph 风格 Autonomous 自主迭代执行器。

包结构：
- notes_memory: 跨轮 notes.md 记忆
- git_driver: git 操作封装
- run_state: run 状态持久化 + 断点续跑
- sleep_guard: caffeinate 跨平台防休眠
- auto_skill_loader: 自动扫描 .trae/skills/ 和 plugins_extra/
- smart_confirmation: 智能确认跳过（白名单 + 风险评估）
- dispatcher_adapter: 复用现有 GoalDispatcher 的适配层
- handlers: 4 阶段 handler（plan/dev/verify/fix）
- loop_controller: 主循环
- config_loader: config.yml 加载

设计原则（不破坏 V3 三层结构）：
- autonomous 是**上层**调用 dispatcher，不是替代
- 不修改 V3 模块（facade / dispatch / dispatcher / plugin）
- 真实实现所有逻辑（无 mock/占位/简化）
"""
from autonomous.notes_memory import NotesMemory, NotesSection
from autonomous.git_driver import GitDriver, GitOpResult, DiffStats
from autonomous.run_state import RunState, RunStateSchema, ResumeContext
from autonomous.sleep_guard import SleepGuard, SleepGuardMode
from autonomous.auto_skill_loader import AutoSkillLoader, SkillManifest
from autonomous.smart_confirmation import SmartConfirmation, ConfirmationDecision, RiskLevel
from autonomous.dispatcher_adapter import DispatcherAdapter, AdapterInvokeResult
from autonomous.loop_controller import (
    RalphLoopController,
    LoopConfig,
    StageKind,
    IterationContext,
    IterationResult,
)
from autonomous.config_loader import AutonomousConfig, load_config

__all__ = [
    # 基础
    "NotesMemory",
    "NotesSection",
    "GitDriver",
    "GitOpResult",
    "DiffStats",
    "RunState",
    "RunStateSchema",
    "ResumeContext",
    "SleepGuard",
    "SleepGuardMode",
    # 智能
    "AutoSkillLoader",
    "SkillManifest",
    "SmartConfirmation",
    "ConfirmationDecision",
    "RiskLevel",
    # 适配器
    "DispatcherAdapter",
    "AdapterInvokeResult",
    # 主循环
    "RalphLoopController",
    "LoopConfig",
    "StageKind",
    "IterationContext",
    "IterationResult",
    # 配置
    "AutonomousConfig",
    "load_config",
]
