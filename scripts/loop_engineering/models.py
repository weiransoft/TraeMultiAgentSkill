"""Loop Engineering 核心数据模型。

本模块定义 Loop Engineering 运行过程中所需的全部结构化数据类型，
保持与现有模块解耦，便于独立测试和复用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class LoopType(str, Enum):
    """业务 Loop 类型。

    - DESIGN: 设计 Loop，产出或更新架构/需求/接口设计文档。
    - CODING: 编码 Loop，完成代码实现、测试、提交。
    - TESTING: 测试 Loop，补充/运行/修复测试并提升覆盖率。
    """

    DESIGN = "design"
    CODING = "coding"
    TESTING = "testing"


class DiscoveryMode(str, Enum):
    """Discovery 阶段工作模式。

    - AUTO: 自动感知项目上下文、历史记录、相关 skills。
    - MANUAL: 仅使用用户输入，不主动扫描项目。
    - OFF: 关闭 Discovery（仅调试用，生产不推荐）。
    """

    AUTO = "auto"
    MANUAL = "manual"
    OFF = "off"


class EvaluatorMode(str, Enum):
    """独立 Evaluator 的严格程度。

    - STRICT: 必须独立 Evaluator 通过才算成功（推荐生产环境）。
    - STANDARD: 允许 Generator 自评 + 独立 Evaluator 抽检。
    - OFF: 关闭独立 Evaluator（仅调试用，生产不推荐）。
    """

    STRICT = "strict"
    STANDARD = "standard"
    OFF = "off"


class LoopEventType(str, Enum):
    """Loop 运行过程中产生的各类事件类型。"""

    DISCOVERY_STARTED = "discovery_started"
    DISCOVERY_COMPLETED = "discovery_completed"
    HANDOFF_CREATED = "handoff_created"
    HANDOFF_DISPATCHED = "handoff_dispatched"
    VERIFICATION_STARTED = "verification_started"
    VERIFICATION_REJECTED = "verification_rejected"
    VERIFICATION_PASSED = "verification_passed"
    PERSISTENCE_WRITTEN = "persistence_written"
    SCHEDULING_DECISION = "scheduling_decision"
    HUMAN_CHECKPOINT = "human_checkpoint"
    LOOP_COMPLETED = "loop_completed"
    LOOP_FAILED = "loop_failed"


class SchedulingAction(str, Enum):
    """LoopScheduler 的决策动作。"""

    CONTINUE = "continue"  # 继续下一轮
    FIX = "fix"  # 基于验证结果修复后重试
    HUMAN_CHECKPOINT = "human_checkpoint"  # 触发人类检查点
    STOP_SUCCESS = "stop_success"  # 目标达成，正常停止
    STOP_FAILURE = "stop_failure"  # 达到上限或连续失败，失败停止


@dataclass
class LoopEngineeringConfig:
    """Loop Engineering 专属配置。

    字段优先从 CLI args 获取，未提供时 fallback 到项目级
    `.trae/autonomous.yml` 中的 loop_* 字段。

    Attributes:
        loop_type: 业务 Loop 类型。
        discovery_mode: Discovery 工作模式。
        evaluator_mode: Evaluator 严格程度。
        max_iterations: 最大迭代次数（硬上限，避免无限循环）。
        max_tokens: 最大 token 消耗预算（硬上限），0 表示不限制（默认）。
        human_checkpoint_every: 每 N 轮触发一次人类检查点，0 表示关闭。
        sampling_read_ratio: 抽样阅读比例（0.0-1.0）。
        stop_when: 自然语言停止条件，供 Scheduler 参考。
        stage_order: 编码 Loop 内部阶段顺序。
        project_root: 项目根目录。
        run_dir: run 状态目录（相对 project_root）。
        notes_path: notes.md 路径（相对 project_root）。
        test_command: 测试命令。
        test_timeout_sec: 测试超时秒数。
        security_analyzer: 安全分析器标识。
        auto_commit: 验证通过后是否自动 git commit。
        extra: 扩展字段，供未来插件使用。
    """

    loop_type: LoopType = LoopType.CODING
    discovery_mode: DiscoveryMode = DiscoveryMode.AUTO
    evaluator_mode: EvaluatorMode = EvaluatorMode.STRICT
    max_iterations: int = 50
    # max_tokens=0 表示不限制（默认）；正整数表示显式预算上限。
    max_tokens: int = 0
    human_checkpoint_every: int = 5
    sampling_read_ratio: float = 0.1
    stop_when: str = ""
    stage_order: List[str] = field(
        default_factory=lambda: ["plan", "dev", "verify", "fix"]
    )
    project_root: Path = field(default_factory=lambda: Path(".").resolve())
    run_dir: str = ".gnhf/runs"
    notes_path: str = "notes.md"
    test_command: str = "python3 -m unittest discover -s tests -p 'test_*.py'"
    test_timeout_sec: float = 600.0
    security_analyzer: str = "builtin"
    auto_commit: bool = True
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验配置字段的合法性。"""
        if self.max_iterations < 1:
            raise ValueError(f"max_iterations 必须 >= 1，当前：{self.max_iterations}")
        # max_tokens=0 表示不限制；负数为非法值
        if self.max_tokens < 0:
            raise ValueError(f"max_tokens 必须 >= 0（0=不限制），当前：{self.max_tokens}")
        if not 0.0 <= self.sampling_read_ratio <= 1.0:
            raise ValueError(
                f"sampling_read_ratio 必须在 [0, 1] 之间，当前：{self.sampling_read_ratio}"
            )
        if self.human_checkpoint_every < 0:
            raise ValueError(
                f"human_checkpoint_every 必须 >= 0，当前：{self.human_checkpoint_every}"
            )
        # 确保 project_root 是 Path 对象
        if isinstance(self.project_root, str):
            object.__setattr__(self, "project_root", Path(self.project_root))


@dataclass
class DiscoveryResult:
    """Discovery 阶段产物。

    Attributes:
        objective: 本轮明确后的目标描述。
        inputs: 原始输入上下文（用户需求、任务描述等）。
        context_features: 提取的项目上下文特征。
        relevant_skills: 识别到的相关 skill 名称列表。
        detected_risks: 识别到的风险列表（非空时应优先处理）。
        inferred_goal: 推断出的可验证目标。
        worktree_required: 是否需要 worktree 隔离。
        suggested_agents: 建议调用的智能体角色列表。
        suggested_patterns: 建议使用的动态工作流模式列表。
        artifacts_to_read: 建议读取的工件路径列表。
        timestamp: Discovery 完成时间 ISO 格式。
    """

    objective: str = ""
    inputs: Dict[str, Any] = field(default_factory=dict)
    context_features: Dict[str, Any] = field(default_factory=dict)
    relevant_skills: List[str] = field(default_factory=list)
    detected_risks: List[str] = field(default_factory=list)
    inferred_goal: str = ""
    worktree_required: bool = True
    suggested_agents: List[str] = field(default_factory=list)
    suggested_patterns: List[str] = field(default_factory=list)
    artifacts_to_read: List[Path] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class HandoffItem:
    """Handoff 阶段生成的工作项。

    Attributes:
        item_id: 工作项唯一标识。
        agent_type: 执行该工作项的智能体角色（如 architect / solo-coder）。
        task: 任务描述。
        acceptance_criteria: 验收标准列表。
        worktree_path: 工作树路径（如使用隔离）。
        dependencies: 依赖的其他工作项 ID 列表。
        metadata: 扩展元数据。
    """

    item_id: str = ""
    agent_type: str = ""
    task: str = ""
    acceptance_criteria: List[str] = field(default_factory=list)
    worktree_path: Optional[Path] = None
    dependencies: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationVerdict:
    """独立 Evaluator 判定结果。

    Attributes:
        passed: 是否通过。
        evaluator_id: 执行评估的 Evaluator 标识。
        reason: 判定理由。
        findings: 发现的具体问题列表。
        severity: 严重级别（info / warning / blocker）。
        suggested_fix: 建议修复方案。
        sampled_artifacts: 抽样阅读的工件路径列表（调试用）。
    """

    passed: bool = False
    evaluator_id: str = ""
    reason: str = ""
    findings: List[str] = field(default_factory=list)
    severity: str = "info"
    suggested_fix: str = ""
    sampled_artifacts: List[Path] = field(default_factory=list)


@dataclass
class LoopEvent:
    """统一事件模型，用于 Memory 写入、审计和可视化。

    Attributes:
        event_id: 事件唯一标识。
        event_type: 事件类型。
        phase: 所属阶段（discovery / handoff / verification / persistence / scheduling）。
        run_id: 所属运行 ID。
        iter_index: 迭代轮次索引（从 0 开始）。
        payload: 事件负载数据。
        timestamp: 事件发生时间 ISO 格式。
    """

    event_id: str = ""
    event_type: LoopEventType = LoopEventType.DISCOVERY_STARTED
    phase: str = ""
    run_id: str = ""
    iter_index: int = 0
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class MemoryQuery:
    """统一 Memory 查询参数。

    Attributes:
        query_type: 查询类型。
            - recent: 最近 N 条事件。
            - similar: 与给定任务相似的历史案例。
            - risk: 高风险事件。
            - event: 按事件类型过滤。
        filters: 额外过滤条件（如 event_type / agent_type / passed）。
        limit: 返回条数上限。
        section: 查询 section（如 notes 中的某个章节）。
        min_similarity: 相似度阈值（similar 查询用）。
        objective: 用于相似度计算的目标描述（similar 查询用）。
    """

    query_type: str = "recent"
    filters: Dict[str, Any] = field(default_factory=dict)
    limit: int = 10
    section: Optional[str] = None
    min_similarity: float = 0.0
    objective: str = ""


@dataclass
class SchedulingDecision:
    """LoopScheduler 的决策结果。

    Attributes:
        action: 决策动作。
        reason: 决策理由（人类可读）。
        next_loop_type: 下一轮 Loop 类型（如需切换）。
        next_stage_order: 下一轮阶段顺序（编码 Loop 用）。
        backoff_seconds: 下一轮执行前的退避秒数。
        requires_human_input: 是否需要人类输入才能继续。
    """

    action: SchedulingAction = SchedulingAction.STOP_SUCCESS
    reason: str = ""
    next_loop_type: Optional[LoopType] = None
    next_stage_order: Optional[List[str]] = None
    backoff_seconds: float = 0.0
    requires_human_input: bool = False


@dataclass
class HumanCheckpointResponse:
    """人类检查点响应。

    Attributes:
        approved: 是否批准继续。
        feedback: 人类反馈文本。
        abort: 是否中止整个 Loop。
    """

    approved: bool = False
    feedback: str = ""
    abort: bool = False


@dataclass
class LoopCycleResult:
    """单轮五步闭环的执行结果。

    Attributes:
        iter_index: 迭代轮次索引。
        discovery: Discovery 结果。
        handoff_items: 生成的工作项列表。
        generator_result: Generator 执行结果（任意结构化数据）。
        verdict: 独立 Evaluator 判定。
        events: 本轮产生的事件列表。
        token_used: 本轮估算 token 消耗。
        duration_sec: 本轮耗时秒数。
        scheduling_decision: 调度决策。
    """

    iter_index: int = 0
    discovery: DiscoveryResult = field(default_factory=DiscoveryResult)
    handoff_items: List[HandoffItem] = field(default_factory=list)
    generator_result: Dict[str, Any] = field(default_factory=dict)
    verdict: EvaluationVerdict = field(default_factory=EvaluationVerdict)
    events: List[LoopEvent] = field(default_factory=list)
    token_used: int = 0
    duration_sec: float = 0.0
    scheduling_decision: SchedulingDecision = field(default_factory=SchedulingDecision)


@dataclass
class LoopRunReport:
    """完整 Loop Engineering 运行的最终报告。

    Attributes:
        run_id: 运行唯一标识。
        loop_type: 业务 Loop 类型。
        objective: 运行目标。
        total_iterations: 总迭代轮数。
        final_status: 最终状态（completed / failed / aborted）。
        events: 全部事件列表。
        token_used: 总 token 消耗估算。
        duration_sec: 总耗时秒数。
        committed_count: 成功持久化（如 commit）次数。
        human_checkpoints: 人类检查点记录。
        final_summary: 最终摘要。
    """

    run_id: str = ""
    loop_type: LoopType = LoopType.CODING
    objective: str = ""
    total_iterations: int = 0
    final_status: str = ""
    events: List[LoopEvent] = field(default_factory=list)
    token_used: int = 0
    duration_sec: float = 0.0
    committed_count: int = 0
    human_checkpoints: List[Dict[str, Any]] = field(default_factory=list)
    final_summary: str = ""


# 便捷类型别名
LogCallback = Optional[Callable[[str, str], None]]
