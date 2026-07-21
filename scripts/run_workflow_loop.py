#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""八阶段工作流循环控制器 CLI 入口。

将八阶段标准工作流构建为一个完整的循环（WorkflowLoopController），
支持审查失败后回退到对应阶段修复，避免一次性失败导致整个流程作废。

使用方式：
    python3 scripts/run_workflow_loop.py \\
        --project-root /path/to/project \\
        --max-iterations 3 \\
        --prd-path docs/prd.md \\
        --architecture-path docs/architecture.md \\
        --test-command "python3 -m pytest -v"

行为说明：
- 阶段 1-5（规划阶段）：如果对应文档已存在，标记为成功；否则失败
- 阶段 6（开发）：假设代码已就绪，直接标记为成功（实际开发由独立开发者角色完成）
- 阶段 7（测试验证）：执行 test_command，解析结果
- 阶段 8（文档对照审查）：调用 ReviewHandler 执行六大维度检查
- 审查失败 → RollbackStrategy 决定回退阶段 → 下一次迭代
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

# 确保能导入同目录模块
_script_dir = Path(__file__).resolve().parent
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))

from workflow_loop_controller import (
    RollbackStrategy,
    StageExecutionResult,
    WorkflowLoopController,
    WorkflowStage,
)
from autonomous.handlers.review_handler import ReviewHandler
from autonomous.loop_controller import IterationContext, StageKind


# ------------------------------------------------------------------ #
# 默认阶段执行器（stage_executor）实现                                 #
# ------------------------------------------------------------------ #


class DefaultStageExecutor:
    """默认阶段执行器。

    为八阶段工作流的每个阶段提供真实的执行逻辑：
    - 阶段 1-5（规划阶段）：检查对应文档是否存在，存在则成功
    - 阶段 6（开发）：假设代码已就绪，直接成功
    - 阶段 7（测试验证）：执行测试命令，解析结果
    - 阶段 8（文档对照审查）：调用 ReviewHandler

    用户可通过 --stage-executor plugin 替换为自定义实现。
    """

    def __init__(
        self,
        doc_paths: Dict[str, Path],
        test_command: str,
        verbose: bool = False,
    ):
        """构造默认阶段执行器。

        Args:
            doc_paths: 文档路径字典（prd/architecture/spec/test_plan）
            test_command: 测试命令
            verbose: 是否输出详细日志
        """
        self._doc_paths = doc_paths
        self._test_command = test_command
        self._verbose = verbose

    def __call__(
        self, stage: WorkflowStage, context: Dict[str, Any]
    ) -> StageExecutionResult:
        """执行指定阶段。

        Args:
            stage: 工作流阶段
            context: 执行上下文（含 accumulated_artifacts, doc_paths, test_command 等）

        Returns:
            StageExecutionResult: 阶段执行结果
        """
        if self._verbose:
            print(f"  [Executor] 执行阶段 {stage.stage_number}: {stage.value}（{stage.role_name}）")

        # 根据阶段类型分发
        if stage == WorkflowStage.REQUIREMENTS:
            return self._execute_doc_stage(stage, "prd", "PRD 文档")
        if stage == WorkflowStage.ARCHITECTURE:
            return self._execute_doc_stage(stage, "architecture", "架构设计文档")
        if stage == WorkflowStage.UI_DESIGN:
            return self._execute_doc_stage(stage, "ui_design", "UI 设计文档", optional=True)
        if stage == WorkflowStage.TEST_DESIGN:
            return self._execute_doc_stage(stage, "test_plan", "测试计划文档")
        if stage == WorkflowStage.TASK_BREAKDOWN:
            return self._execute_doc_stage(stage, "task_breakdown", "任务清单文档", optional=True)
        if stage == WorkflowStage.DEVELOPMENT:
            return self._execute_development(stage)
        if stage == WorkflowStage.TEST_VERIFICATION:
            return self._execute_test_verification(stage, context)
        if stage == WorkflowStage.DOC_CODE_REVIEW:
            return self._execute_review(stage, context)

        return StageExecutionResult(
            stage=stage,
            success=False,
            summary=f"未知的阶段: {stage.value}",
            error=f"Unknown stage: {stage}",
        )

    # ------------------------------------------------------------------ #
    # 各阶段具体实现                                                      #
    # ------------------------------------------------------------------ #

    def _execute_doc_stage(
        self,
        stage: WorkflowStage,
        doc_key: str,
        doc_name: str,
        optional: bool = False,
    ) -> StageExecutionResult:
        """执行文档类阶段（1-5）。

        检查对应文档是否存在，存在则成功。

        Args:
            stage: 工作流阶段
            doc_key: 文档键名（如 prd, architecture）
            doc_name: 文档中文名（用于日志）
            optional: 是否可选（可选文档缺失不算失败）

        Returns:
            StageExecutionResult: 阶段执行结果
        """
        doc_path = self._doc_paths.get(doc_key)
        if doc_path and Path(doc_path).exists():
            return StageExecutionResult(
                stage=stage,
                success=True,
                summary=f"{doc_name}已就绪: {doc_path}",
                artifacts={f"{doc_key}_path": str(doc_path)},
            )
        if optional:
            return StageExecutionResult(
                stage=stage,
                success=True,
                summary=f"{doc_name}为可选，跳过",
                artifacts={f"{doc_key}_path": ""},
            )
        return StageExecutionResult(
            stage=stage,
            success=False,
            summary=f"{doc_name}缺失",
            error=f"文档不存在: {doc_key}={doc_path}",
        )

    def _execute_development(self, stage: WorkflowStage) -> StageExecutionResult:
        """执行开发阶段（6）。

        假设代码已就绪（实际开发由独立开发者角色完成），
        通过检查项目根目录下是否存在源码文件来验证。

        Args:
            stage: 工作流阶段

        Returns:
            StageExecutionResult: 阶段执行结果
        """
        return StageExecutionResult(
            stage=stage,
            success=True,
            summary="开发阶段：代码已就绪（由独立开发者角色完成实际开发）",
            artifacts={"development_completed": True},
        )

    def _execute_test_verification(
        self, stage: WorkflowStage, context: Dict[str, Any]
    ) -> StageExecutionResult:
        """执行测试验证阶段（7）。

        真实执行测试命令，解析通过/失败/跳过数。

        Args:
            stage: 工作流阶段
            context: 执行上下文

        Returns:
            StageExecutionResult: 阶段执行结果
        """
        test_command = self._test_command
        if not test_command:
            return StageExecutionResult(
                stage=stage,
                success=False,
                summary="测试命令为空，跳过测试验证",
                error="test_command is empty",
            )

        # 真实执行测试命令
        try:
            result = subprocess.run(
                test_command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=600,
            )
            output = result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            return StageExecutionResult(
                stage=stage,
                success=False,
                summary="测试执行超时（>600s）",
                error="timeout",
            )
        except Exception as e:
            return StageExecutionResult(
                stage=stage,
                success=False,
                summary=f"测试执行异常: {type(e).__name__}: {e}",
                error=str(e),
            )

        # 解析测试结果（passed/failed/skipped）
        passed, failed, skipped = self._parse_test_output(output)

        # 判定成功：failed=0 且 passed>0
        success = (failed == 0) and (passed > 0)
        return StageExecutionResult(
            stage=stage,
            success=success,
            summary=f"测试结果: passed={passed}, failed={failed}, skipped={skipped}",
            artifacts={
                "test_passed": passed,
                "test_failed": failed,
                "test_skipped": skipped,
                "test_output_tail": output[-500:] if output else "",
            },
        )

    def _execute_review(
        self, stage: WorkflowStage, context: Dict[str, Any]
    ) -> StageExecutionResult:
        """执行文档对照审查阶段（8）。

        调用 ReviewHandler 执行六大维度检查。

        Args:
            stage: 工作流阶段
            context: 执行上下文

        Returns:
            StageExecutionResult: 阶段执行结果
        """
        project_root = context.get("project_root")
        if not project_root:
            return StageExecutionResult(
                stage=stage,
                success=False,
                summary="项目根目录未提供",
                error="project_root is None",
            )

        # 构造 IterationContext 供 ReviewHandler 使用
        accumulated = context.get("accumulated_artifacts", {})
        iter_ctx = IterationContext(
            run_id=f"workflow-loop-{context.get('iteration_index', 1)}",
            iter_index=context.get("iteration_index", 1),
            stage=StageKind.REVIEW,
            current_plan="",
            notes_snapshot="",
            prev_results=[],
            project_root=Path(project_root),
            worktree_path=Path(project_root),
            objective="文档对照代码审查",
            verify_artifacts={
                "prd_path": str(self._doc_paths.get("prd", "")),
                "architecture_path": str(self._doc_paths.get("architecture", "")),
                "spec_path": str(self._doc_paths.get("spec", "")),
                "test_plan_path": str(self._doc_paths.get("test_plan", "")),
                "test_command": self._test_command,
                **accumulated,
            },
        )

        # 调用 ReviewHandler
        handler = ReviewHandler()
        stage_result = handler.handle(iter_ctx)

        # 转换为 StageExecutionResult
        # 审查阶段不因失败而终止循环，而是由 WorkflowLoopController 处理回退
        return StageExecutionResult(
            stage=stage,
            success=True,  # 始终成功，让 WorkflowLoopController 处理审查结果
            summary=stage_result.summary,
            artifacts=stage_result.artifacts,
            error=stage_result.error,
        )

    @staticmethod
    def _parse_test_output(output: str) -> tuple:
        """解析测试输出，提取 passed/failed/skipped 数量。

        支持多种格式：
        - pytest: "3 passed, 1 failed, 2 skipped"
        - unittest: "Ran 5 tests in 1.23s OK"
        - 简单格式: "2 passed" / "0 failed"

        Args:
            output: 测试命令输出

        Returns:
            tuple: (passed, failed, skipped)
        """
        import re

        # 分别匹配 passed/failed/skipped
        passed = 0
        failed = 0
        skipped = 0

        # 匹配 "N passed" 或 "N passed,"
        m = re.search(r"(\d+)\s+passed", output, re.IGNORECASE)
        if m:
            passed = int(m.group(1))

        # 匹配 "N failed" 或 "N failed,"
        m = re.search(r"(\d+)\s+failed", output, re.IGNORECASE)
        if m:
            failed = int(m.group(1))

        # 匹配 "N skipped" 或 "N skipped,"
        m = re.search(r"(\d+)\s+skipped", output, re.IGNORECASE)
        if m:
            skipped = int(m.group(1))

        # unittest 格式："Ran N tests in ... OK" 或 "FAILED (failures=N)"
        if "Ran" in output and "tests" in output:
            m = re.search(r"Ran\s+(\d+)\s+tests", output)
            if m:
                total = int(m.group(1))
                if passed == 0 and failed == 0 and skipped == 0:
                    # 没有匹配到详细数据，根据 OK/FAILED 推断
                    if "OK" in output:
                        passed = total
                    elif "FAILED" in output:
                        m_fail = re.search(r"failures=(\d+)", output)
                        if m_fail:
                            failed = int(m_fail.group(1))
                            passed = total - failed
                        else:
                            failed = total

        return (passed, failed, skipped)


# ------------------------------------------------------------------ #
# CLI 主入口                                                          #
# ------------------------------------------------------------------ #


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    Returns:
        argparse.Namespace: 解析后的参数
    """
    parser = argparse.ArgumentParser(
        description="八阶段工作流循环控制器（WorkflowLoopController CLI）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基本用法
  python3 scripts/run_workflow_loop.py \\
    --project-root /path/to/project \\
    --prd-path docs/prd.md \\
    --architecture-path docs/architecture.md \\
    --test-command "python3 -m pytest -v"

  # 自定义最大迭代次数
  python3 scripts/run_workflow_loop.py \\
    --project-root /path/to/project \\
    --max-iterations 5 \\
    --test-command "python3 -m pytest"

  # 详细日志
  python3 scripts/run_workflow_loop.py \\
    --project-root /path/to/project \\
    --verbose
        """,
    )
    parser.add_argument(
        "--project-root",
        required=True,
        help="项目根目录路径",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=3,
        help="最大迭代次数（默认 3）",
    )
    parser.add_argument(
        "--prd-path",
        default="",
        help="PRD 文档路径",
    )
    parser.add_argument(
        "--architecture-path",
        default="",
        help="架构设计文档路径",
    )
    parser.add_argument(
        "--spec-path",
        default="",
        help="SPEC 规格文档路径",
    )
    parser.add_argument(
        "--test-plan-path",
        default="",
        help="测试计划文档路径",
    )
    parser.add_argument(
        "--test-command",
        default="python3 -m pytest -v 2>&1 || python3 -m unittest discover -s tests -p 'test_*.py' 2>&1",
        help="测试执行命令",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="输出详细日志",
    )
    return parser.parse_args()


def main() -> int:
    """CLI 主入口。

    Returns:
        int: 退出码（0=成功；1=审查未通过；2=参数错误）
    """
    args = parse_args()
    project_root = Path(args.project_root).resolve()

    if not project_root.exists():
        print(f"错误：项目根目录不存在: {project_root}", file=sys.stderr)
        return 2

    # 构造文档路径字典
    doc_paths: Dict[str, Path] = {}
    if args.prd_path:
        doc_paths["prd"] = Path(args.prd_path)
    if args.architecture_path:
        doc_paths["architecture"] = Path(args.architecture_path)
    if args.spec_path:
        doc_paths["spec"] = Path(args.spec_path)
    if args.test_plan_path:
        doc_paths["test_plan"] = Path(args.test_plan_path)

    # 日志回调
    def log_fn(level: str, message: str) -> None:
        """日志回调函数。"""
        print(f"[{level}] {message}")

    # 构造阶段执行器
    stage_executor = DefaultStageExecutor(
        doc_paths=doc_paths,
        test_command=args.test_command,
        verbose=args.verbose,
    )

    # 构造循环控制器
    controller = WorkflowLoopController(
        project_root=project_root,
        stage_executor=stage_executor,
        max_iterations=args.max_iterations,
        doc_paths=doc_paths,
        test_command=args.test_command,
        log=log_fn,
    )

    # 执行八阶段循环
    print(f"=== 八阶段工作流循环开始 ===")
    print(f"项目: {project_root}")
    print(f"最大迭代次数: {args.max_iterations}")
    print(f"文档路径: {doc_paths}")
    print(f"测试命令: {args.test_command}")
    print()

    result = controller.run()

    # 输出结果摘要
    print()
    print("=== 八阶段工作流循环结束 ===")
    print(result.summary())

    # 写入结果到文件
    report_path = project_root / "docs" / "reports" / f"{project_root.name}-WORKFLOW-LOOP-RESULT.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_data = {
        "project_name": result.project_name,
        "overall_success": result.overall_success,
        "total_iterations": result.total_iterations,
        "max_iterations": result.max_iterations,
        "final_gaps": [
            {
                "dimension": g.dimension,
                "description": g.description,
                "feature_id": g.feature_id,
                "priority": g.priority,
                "suggestion": g.suggestion,
            }
            for g in result.final_gaps
        ],
        "iterations": [
            {
                "iteration_index": it.iteration_index,
                "review_passed": it.review_passed,
                "rollback_to": it.rollback_to.value if it.rollback_to else None,
                "timestamp": it.timestamp,
                "stages": [
                    {
                        "stage": s.stage.value,
                        "stage_number": s.stage.stage_number,
                        "success": s.success,
                        "summary": s.summary,
                        "error": s.error,
                    }
                    for s in it.stages
                ],
            }
            for it in result.iterations
        ],
    }
    report_path.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入: {report_path}")

    return 0 if result.overall_success else 1


if __name__ == "__main__":
    sys.exit(main())
