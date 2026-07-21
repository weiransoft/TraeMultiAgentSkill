#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""八阶段工作流循环控制器。

将八阶段标准工作流构建为一个完整的循环：
1. 需求分析（产品经理）     → PRD 文档
2. 架构设计（架构师）       → 架构文档
3. UI 设计（UI 设计师）     → UI 设计稿
4. 测试设计（测试专家）     → 测试计划
5. 任务分解（独立开发者）   → 任务清单
6. 开发实现（独立开发者）   → 代码
7. 测试验证（测试专家）     → 测试报告
8. 文档对照代码审查（多角色）→ 审查报告

循环行为：
- 阶段 1-5 为"规划阶段"，产出文档
- 阶段 6 为"开发阶段"，产出代码
- 阶段 7 为"验证阶段"，执行测试
- 阶段 8 为"审查阶段"，对照文档检查代码

审查失败时的回退策略：
- D1 功能缺失 → 回退到阶段 6（开发）
- D2 集成缺失 → 回退到阶段 6（开发）
- D3 测试失败 → 回退到阶段 7（验证）
- D4 验收标准未满足 → 回退到阶段 6（开发）
- D5 TODO/FIXME 未实现 → 回退到阶段 6（开发）
- D6 文档意图偏离 → 回退到阶段 6（开发）

最大迭代次数限制防止无限循环。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# 确保能导入上层模块
_script_dir = Path(__file__).resolve().parent
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))

from doc_code_consistency_checker import ConsistencyReport, GapItem


# ------------------------------------------------------------------ #
# 八阶段枚举                                                          #
# ------------------------------------------------------------------ #


class WorkflowStage(str, Enum):
    """八阶段工作流阶段枚举。

    每个阶段对应一个角色和产出。
    """

    REQUIREMENTS = "requirements"      # 阶段 1: 需求分析（产品经理）
    ARCHITECTURE = "architecture"      # 阶段 2: 架构设计（架构师）
    UI_DESIGN = "ui_design"            # 阶段 3: UI 设计（UI 设计师）
    TEST_DESIGN = "test_design"        # 阶段 4: 测试设计（测试专家）
    TASK_BREAKDOWN = "task_breakdown"  # 阶段 5: 任务分解（独立开发者）
    DEVELOPMENT = "development"        # 阶段 6: 开发实现（独立开发者）
    TEST_VERIFICATION = "test_verification"  # 阶段 7: 测试验证（测试专家）
    DOC_CODE_REVIEW = "doc_code_review"      # 阶段 8: 文档对照代码审查（多角色）

    @property
    def stage_number(self) -> int:
        """获取阶段编号（1-8）。"""
        return {
            WorkflowStage.REQUIREMENTS: 1,
            WorkflowStage.ARCHITECTURE: 2,
            WorkflowStage.UI_DESIGN: 3,
            WorkflowStage.TEST_DESIGN: 4,
            WorkflowStage.TASK_BREAKDOWN: 5,
            WorkflowStage.DEVELOPMENT: 6,
            WorkflowStage.TEST_VERIFICATION: 7,
            WorkflowStage.DOC_CODE_REVIEW: 8,
        }[self]

    @property
    def role_name(self) -> str:
        """获取阶段对应的角色名称。"""
        return {
            WorkflowStage.REQUIREMENTS: "产品经理",
            WorkflowStage.ARCHITECTURE: "架构师",
            WorkflowStage.UI_DESIGN: "UI 设计师",
            WorkflowStage.TEST_DESIGN: "测试专家",
            WorkflowStage.TASK_BREAKDOWN: "独立开发者",
            WorkflowStage.DEVELOPMENT: "独立开发者",
            WorkflowStage.TEST_VERIFICATION: "测试专家",
            WorkflowStage.DOC_CODE_REVIEW: "多角色",
        }[self]

    @property
    def output_name(self) -> str:
        """获取阶段产出名称。"""
        return {
            WorkflowStage.REQUIREMENTS: "PRD 文档",
            WorkflowStage.ARCHITECTURE: "架构设计文档",
            WorkflowStage.UI_DESIGN: "UI 设计稿",
            WorkflowStage.TEST_DESIGN: "测试计划",
            WorkflowStage.TASK_BREAKDOWN: "任务清单",
            WorkflowStage.DEVELOPMENT: "代码实现",
            WorkflowStage.TEST_VERIFICATION: "测试报告",
            WorkflowStage.DOC_CODE_REVIEW: "审查报告",
        }[self]

    def to_stage_kind(self) -> Optional["StageKind"]:
        """将 WorkflowStage 映射为 Ralph 循环的 StageKind。

        映射关系（详见设计文档 §10.3.2）：
        - REQUIREMENTS / ARCHITECTURE / UI_DESIGN / TEST_DESIGN → None（Ralph 小循环无对应）
        - TASK_BREAKDOWN → PLAN
        - DEVELOPMENT → DEV
        - TEST_VERIFICATION → VERIFY
        - DOC_CODE_REVIEW → REVIEW

        Returns:
            Optional[StageKind]: 对应的 StageKind，无对应则 None

        Note:
            采用延迟导入避免循环依赖（autonomous.loop_controller 不依赖本模块）。
        """
        # 延迟导入避免循环依赖
        from autonomous.loop_controller import StageKind

        mapping = {
            WorkflowStage.TASK_BREAKDOWN: StageKind.PLAN,
            WorkflowStage.DEVELOPMENT: StageKind.DEV,
            WorkflowStage.TEST_VERIFICATION: StageKind.VERIFY,
            WorkflowStage.DOC_CODE_REVIEW: StageKind.REVIEW,
        }
        return mapping.get(self)


# ------------------------------------------------------------------ #
# 阶段执行结果                                                        #
# ------------------------------------------------------------------ #


@dataclass
class StageExecutionResult:
    """单阶段执行结果。

    字段说明：
    - stage: 执行的阶段
    - success: 是否成功
    - summary: 摘要
    - artifacts: 阶段产出（dict）
    - error: 错误信息
    - duration_sec: 执行耗时
    """

    stage: WorkflowStage
    success: bool = True
    summary: str = ""
    artifacts: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    duration_sec: float = 0.0


@dataclass
class WorkflowIterationRecord:
    """工作流迭代记录。

    记录一次完整八阶段迭代的执行情况。

    字段说明：
    - iteration_index: 迭代索引（从 1 开始）
    - stages: 各阶段执行结果
    - review_passed: 审查是否通过
    - gaps: 审查发现的缺口
    - rollback_to: 回退到的阶段（None 表示不回退）
    - timestamp: 记录时间
    """

    iteration_index: int
    stages: List[StageExecutionResult] = field(default_factory=list)
    review_passed: bool = False
    gaps: List[GapItem] = field(default_factory=list)
    rollback_to: Optional[WorkflowStage] = None
    timestamp: str = ""


# ------------------------------------------------------------------ #
# 回退策略                                                            #
# ------------------------------------------------------------------ #


class RollbackStrategy:
    """审查失败时的回退策略。

    根据缺口维度决定回退到哪个阶段：
    - D1 功能缺失 → 回退到 DEVELOPMENT（阶段 6）
    - D2 集成缺失 → 回退到 DEVELOPMENT（阶段 6）
    - D3 测试失败 → 回退到 TEST_VERIFICATION（阶段 7）
    - D4 验收标准未满足 → 回退到 DEVELOPMENT（阶段 6）
    - D5 TODO/FIXME 未实现 → 回退到 DEVELOPMENT（阶段 6）
    - D6 文档意图偏离 → 回退到 DEVELOPMENT（阶段 6）
    """

    # 缺口维度到回退阶段的映射
    _ROLLBACK_MAP: Dict[str, WorkflowStage] = {
        "D1 功能完成度": WorkflowStage.DEVELOPMENT,
        "D2 集成完整性": WorkflowStage.DEVELOPMENT,
        "D3 测试正确性": WorkflowStage.TEST_VERIFICATION,
        "D4 验收标准": WorkflowStage.DEVELOPMENT,
        "D5 TODO/FIXME": WorkflowStage.DEVELOPMENT,
        "D6 文档意图": WorkflowStage.DEVELOPMENT,
    }

    @classmethod
    def determine_rollback(
        cls, gaps: List[GapItem]
    ) -> Optional[WorkflowStage]:
        """根据缺口列表决定回退到哪个阶段。

        策略：
        1. 如果有 D3 测试失败缺口，回退到 TEST_VERIFICATION
        2. 否则如果有其他开发类缺口，回退到 DEVELOPMENT
        3. 如果没有缺口，返回 None

        Args:
            gaps: 缺口列表

        Returns:
            Optional[WorkflowStage]: 回退到的阶段，None 表示不需要回退
        """
        if not gaps:
            return None

        # 按优先级确定回退阶段
        # P0 缺口优先处理
        rollback_stages = set()
        for gap in gaps:
            # 匹配缺口维度到回退阶段
            for dim_prefix, stage in cls._ROLLBACK_MAP.items():
                if gap.dimension.startswith(dim_prefix):
                    rollback_stages.add(stage)
                    break

        # 优先回退到更早的阶段（DEVELOPMENT < TEST_VERIFICATION）
        if WorkflowStage.DEVELOPMENT in rollback_stages:
            return WorkflowStage.DEVELOPMENT
        if WorkflowStage.TEST_VERIFICATION in rollback_stages:
            return WorkflowStage.TEST_VERIFICATION

        # 默认回退到开发阶段
        return WorkflowStage.DEVELOPMENT


# ------------------------------------------------------------------ #
# 工作流循环控制器                                                    #
# ------------------------------------------------------------------ #


class WorkflowLoopController:
    """八阶段工作流循环控制器。

    职责：
    1. 按顺序执行八阶段工作流
    2. 在审查阶段（阶段 8）检查文档-代码一致性
    3. 审查失败时根据缺口维度回退到相应阶段
    4. 支持最大迭代次数限制
    5. 记录每次迭代的执行情况

    使用方式：
        controller = WorkflowLoopController(
            project_root=Path("/path/to/project"),
            stage_executor=my_executor,  # 阶段执行回调
            max_iterations=3,
        )
        result = controller.run()
    """

    # 默认八阶段顺序
    DEFAULT_STAGE_ORDER: List[WorkflowStage] = [
        WorkflowStage.REQUIREMENTS,
        WorkflowStage.ARCHITECTURE,
        WorkflowStage.UI_DESIGN,
        WorkflowStage.TEST_DESIGN,
        WorkflowStage.TASK_BREAKDOWN,
        WorkflowStage.DEVELOPMENT,
        WorkflowStage.TEST_VERIFICATION,
        WorkflowStage.DOC_CODE_REVIEW,
    ]

    def __init__(
        self,
        project_root: Path,
        stage_executor: Callable[[WorkflowStage, Dict[str, Any]], StageExecutionResult],
        max_iterations: int = 3,
        stage_order: Optional[List[WorkflowStage]] = None,
        doc_paths: Optional[Dict[str, Path]] = None,
        test_command: str = "",
        log: Optional[Callable[[str, str], None]] = None,
    ):
        """构造工作流循环控制器。

        Args:
            project_root: 项目根目录
            stage_executor: 阶段执行回调函数，签名为
                (stage: WorkflowStage, context: dict) -> StageExecutionResult
                context 包含 iteration_index, prev_results, doc_paths 等
            max_iterations: 最大迭代次数（默认 3 次）
            stage_order: 阶段顺序（默认为八阶段完整顺序）
            doc_paths: 文档路径字典
            test_command: 测试命令
            log: 日志回调 (level, message)
        """
        self._project_root = Path(project_root).resolve()
        self._stage_executor = stage_executor
        self._max_iterations = max(1, int(max_iterations))
        self._stage_order = stage_order or list(self.DEFAULT_STAGE_ORDER)
        self._doc_paths = doc_paths or {}
        self._test_command = test_command
        self._log = log or (lambda level, msg: None)

        # 迭代历史
        self._iterations: List[WorkflowIterationRecord] = []
        # 累计上下文（跨迭代传递）
        self._accumulated_artifacts: Dict[str, Any] = {}

    def run(self) -> "WorkflowRunResult":
        """执行八阶段工作流循环。

        Returns:
            WorkflowRunResult: 工作流执行结果
        """
        self._log("INFO", "八阶段工作流循环开始")
        overall_success = False

        for iter_idx in range(1, self._max_iterations + 1):
            self._log("INFO", f"===== 迭代 {iter_idx}/{self._max_iterations} =====")

            iteration_record = WorkflowIterationRecord(
                iteration_index=iter_idx,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

            # 确定本次迭代的起始阶段索引
            start_stage_idx = self._calculate_start_stage_idx(iter_idx)

            # 执行从起始阶段到最后的所有阶段
            overall_success = self._execute_stages(
                start_stage_idx, iter_idx, iteration_record
            )

            # 记录本次迭代
            self._iterations.append(iteration_record)

            # 如果审查通过，退出循环
            if overall_success:
                break

            # 如果是最后一次迭代仍未通过，退出
            if iter_idx == self._max_iterations:
                self._log("WARN", f"达到最大迭代次数 {self._max_iterations}，工作流终止")
                break

        # 构建最终结果
        return self._build_run_result(overall_success)

    def _calculate_start_stage_idx(self, iter_idx: int) -> int:
        """计算本次迭代的起始阶段索引。

        策略：
        - 第 1 次迭代：从第一个阶段开始（索引 0）
        - 后续迭代：从上次回退目标开始；若无回退目标，从审查阶段重新开始

        Args:
            iter_idx: 迭代索引（从 1 开始）

        Returns:
            int: 起始阶段在 _stage_order 中的索引
        """
        if iter_idx == 1:
            return 0

        prev_rollback = self._iterations[-1].rollback_to
        if prev_rollback is None:
            # 没有回退目标，从审查阶段重新开始
            return len(self._stage_order) - 1

        try:
            return self._stage_order.index(prev_rollback)
        except ValueError:
            return 0

    def _execute_stages(
        self,
        start_stage_idx: int,
        iter_idx: int,
        iteration_record: WorkflowIterationRecord,
    ) -> bool:
        """执行从起始阶段到最后的所有阶段。

        Args:
            start_stage_idx: 起始阶段索引
            iter_idx: 迭代索引
            iteration_record: 本次迭代记录（会追加 stage 结果）

        Returns:
            bool: 审查是否通过（True 表示通过，可退出循环）
        """
        review_passed = False

        for stage_idx in range(start_stage_idx, len(self._stage_order)):
            stage = self._stage_order[stage_idx]
            self._log("INFO", f"  阶段 {stage.stage_number}: {stage.value}（{stage.role_name}）")

            # 执行单个阶段
            result = self._execute_single_stage(stage, iter_idx, iteration_record)
            iteration_record.stages.append(result)

            # 更新累计上下文
            self._accumulated_artifacts.update(result.artifacts)

            # 如果阶段失败且不是审查阶段，终止本次迭代
            if not result.success and stage != WorkflowStage.DOC_CODE_REVIEW:
                self._log("WARN", f"  阶段 {stage.stage_number} 失败: {result.error}")
                break

            # 如果是审查阶段，处理审查结果
            if stage == WorkflowStage.DOC_CODE_REVIEW:
                review_passed = self._handle_review_result(result, iteration_record)
                if review_passed:
                    break  # 审查通过，退出阶段循环

        return review_passed

    def _execute_single_stage(
        self,
        stage: WorkflowStage,
        iter_idx: int,
        iteration_record: WorkflowIterationRecord,
    ) -> StageExecutionResult:
        """执行单个阶段。

        构建执行上下文并调用 stage_executor，捕获异常。

        Args:
            stage: 工作流阶段
            iter_idx: 迭代索引
            iteration_record: 本次迭代记录

        Returns:
            StageExecutionResult: 阶段执行结果
        """
        # 构建执行上下文
        exec_context = {
            "iteration_index": iter_idx,
            "stage_number": stage.stage_number,
            "prev_results": [r.__dict__ for r in iteration_record.stages],
            "accumulated_artifacts": self._accumulated_artifacts,
            "doc_paths": self._doc_paths,
            "test_command": self._test_command,
            "project_root": self._project_root,
        }

        # 执行阶段（捕获异常，避免单阶段异常导致整个循环崩溃）
        try:
            return self._stage_executor(stage, exec_context)
        except Exception as e:
            return StageExecutionResult(
                stage=stage,
                success=False,
                summary=f"阶段执行异常: {type(e).__name__}: {e}",
                error=str(e),
            )

    def _handle_review_result(
        self,
        result: StageExecutionResult,
        iteration_record: WorkflowIterationRecord,
    ) -> bool:
        """处理审查阶段的执行结果。

        提取审查通过状态和缺口清单，根据缺口决定回退阶段。

        Args:
            result: 审查阶段执行结果
            iteration_record: 本次迭代记录（会更新 review_passed / gaps / rollback_to）

        Returns:
            bool: 审查是否通过
        """
        review_passed = result.artifacts.get("overall_passed", False)
        iteration_record.review_passed = review_passed

        # 提取缺口信息
        gap_list = result.artifacts.get("gap_list", [])
        iteration_record.gaps = [
            GapItem(
                dimension=g.get("dimension", ""),
                description=g.get("description", ""),
                feature_id=g.get("feature_id", ""),
                priority=g.get("priority", "P1"),
                suggestion=g.get("suggestion", ""),
            )
            for g in gap_list
        ]

        if review_passed:
            self._log("INFO", "  审查通过！工作流完成")
            return True

        # 审查不通过，确定回退阶段
        self._log("WARN", f"  审查不通过：{len(gap_list)} 个缺口")
        rollback_stage = RollbackStrategy.determine_rollback(iteration_record.gaps)
        iteration_record.rollback_to = rollback_stage
        if rollback_stage:
            self._log("INFO", f"  回退到阶段 {rollback_stage.stage_number}: {rollback_stage.value}")
        else:
            self._log("WARN", "  无法确定回退目标")
        return False

    def _build_run_result(self, overall_success: bool) -> "WorkflowRunResult":
        """构建工作流执行结果。

        Args:
            overall_success: 是否最终成功

        Returns:
            WorkflowRunResult: 工作流执行结果
        """
        return WorkflowRunResult(
            project_name=self._project_root.name,
            iterations=list(self._iterations),
            overall_success=overall_success,
            total_iterations=len(self._iterations),
            max_iterations=self._max_iterations,
            final_gaps=self._iterations[-1].gaps if self._iterations else [],
            accumulated_artifacts=dict(self._accumulated_artifacts),
        )

    @property
    def iterations(self) -> List[WorkflowIterationRecord]:
        """获取迭代历史记录。"""
        return list(self._iterations)


# ------------------------------------------------------------------ #
# 工作流执行结果                                                      #
# ------------------------------------------------------------------ #


@dataclass
class WorkflowRunResult:
    """工作流执行结果。

    字段说明：
    - project_name: 项目名称
    - iterations: 迭代历史记录列表
    - overall_success: 是否最终成功
    - total_iterations: 总迭代次数
    - max_iterations: 最大迭代次数
    - final_gaps: 最终剩余缺口
    - accumulated_artifacts: 累计产出
    """

    project_name: str = ""
    iterations: List[WorkflowIterationRecord] = field(default_factory=list)
    overall_success: bool = False
    total_iterations: int = 0
    max_iterations: int = 3
    final_gaps: List[GapItem] = field(default_factory=list)
    accumulated_artifacts: Dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        """生成执行结果摘要文本。

        Returns:
            str: 摘要文本
        """
        lines = []
        lines.append(f"八阶段工作流执行结果：")
        lines.append(f"  项目: {self.project_name}")
        lines.append(f"  最终状态: {'✅ 成功' if self.overall_success else '❌ 未通过'}")
        lines.append(f"  迭代次数: {self.total_iterations}/{self.max_iterations}")
        if self.final_gaps:
            lines.append(f"  剩余缺口: {len(self.final_gaps)} 个")
            for idx, gap in enumerate(self.final_gaps[:10], 1):
                lines.append(f"    {idx}. [{gap.priority}] {gap.dimension}: {gap.description}")
        else:
            lines.append(f"  剩余缺口: 0 个")
        lines.append(f"  迭代历史:")
        for iteration in self.iterations:
            status = "✅ 通过" if iteration.review_passed else "❌ 不通过"
            rollback = f" → 回退到阶段 {iteration.rollback_to.stage_number}" if iteration.rollback_to else ""
            lines.append(f"    迭代 {iteration.iteration_index}: {status}{rollback}")
            for stage_result in iteration.stages:
                stage_status = "✅" if stage_result.success else "❌"
                lines.append(f"      阶段 {stage_result.stage.stage_number} ({stage_result.stage.value}): {stage_status}")
        return "\n".join(lines)


__all__ = [
    "WorkflowStage",
    "StageExecutionResult",
    "WorkflowIterationRecord",
    "RollbackStrategy",
    "WorkflowLoopController",
    "WorkflowRunResult",
]
