#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WorkflowLoopController 单元测试。

测试覆盖：
- W1: 八阶段顺序执行（全部通过）
- W2: 审查失败回退到开发阶段
- W3: 最大迭代次数限制
- W4: 回退策略判定
- W5: 执行结果摘要生成
"""
import sys
import unittest
from pathlib import Path

# 确保能导入被测模块
_script_dir = Path(__file__).resolve().parent.parent
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))

from doc_code_consistency_checker import GapItem
from workflow_loop_controller import (
    RollbackStrategy,
    StageExecutionResult,
    WorkflowIterationRecord,
    WorkflowLoopController,
    WorkflowRunResult,
    WorkflowStage,
)


class TestRollbackStrategy(unittest.TestCase):
    """回退策略测试。"""

    def test_w4a_rollback_for_d1_feature_missing(self):
        """W4a: D1 功能缺失 → 回退到 DEVELOPMENT。"""
        gaps = [GapItem(dimension="D1 功能完成度", description="功能 F-003 未实现", priority="P0")]
        rollback = RollbackStrategy.determine_rollback(gaps)
        self.assertEqual(rollback, WorkflowStage.DEVELOPMENT)

    def test_w4b_rollback_for_d3_test_failure(self):
        """W4b: D3 测试失败 → 回退到 TEST_VERIFICATION。"""
        gaps = [GapItem(dimension="D3 测试正确性", description="测试失败: 2 failed", priority="P0")]
        rollback = RollbackStrategy.determine_rollback(gaps)
        self.assertEqual(rollback, WorkflowStage.TEST_VERIFICATION)

    def test_w4c_rollback_for_d5_todo(self):
        """W4c: D5 TODO 未实现 → 回退到 DEVELOPMENT。"""
        gaps = [GapItem(dimension="D5 TODO/FIXME", description="TODO 未实现", priority="P1")]
        rollback = RollbackStrategy.determine_rollback(gaps)
        self.assertEqual(rollback, WorkflowStage.DEVELOPMENT)

    def test_w4d_no_rollback_when_no_gaps(self):
        """W4d: 无缺口 → 不回退。"""
        gaps = []
        rollback = RollbackStrategy.determine_rollback(gaps)
        self.assertIsNone(rollback)

    def test_w4e_mixed_gaps_prioritize_development(self):
        """W4e: 混合缺口优先回退到 DEVELOPMENT。"""
        gaps = [
            GapItem(dimension="D1 功能完成度", description="功能缺失", priority="P0"),
            GapItem(dimension="D3 测试正确性", description="测试失败", priority="P0"),
        ]
        rollback = RollbackStrategy.determine_rollback(gaps)
        # D1 和 D3 混合时，应优先回退到 DEVELOPMENT（更早的阶段）
        self.assertEqual(rollback, WorkflowStage.DEVELOPMENT)


class TestWorkflowLoopController(unittest.TestCase):
    """WorkflowLoopController 测试。"""

    def test_w1_all_stages_pass(self):
        """W1: 八阶段顺序执行，全部通过。"""
        # 创建一个总是成功的执行器
        def success_executor(stage, context):
            if stage == WorkflowStage.DOC_CODE_REVIEW:
                return StageExecutionResult(
                    stage=stage,
                    success=True,
                    summary="审查通过",
                    artifacts={"overall_passed": True, "gap_count": 0, "gap_list": []},
                )
            return StageExecutionResult(
                stage=stage,
                success=True,
                summary=f"{stage.value} 完成",
                artifacts={},
            )

        controller = WorkflowLoopController(
            project_root=Path("/tmp/test-project"),
            stage_executor=success_executor,
            max_iterations=3,
        )
        result = controller.run()

        self.assertTrue(result.overall_success)
        self.assertEqual(result.total_iterations, 1)
        self.assertEqual(len(result.iterations), 1)
        # 验证执行了全部 8 个阶段
        self.assertEqual(len(result.iterations[0].stages), 8)
        self.assertTrue(result.iterations[0].review_passed)

    def test_w2_review_fail_rollback_to_development(self):
        """W2: 审查失败回退到开发阶段。"""
        call_log = []

        def fail_then_pass_executor(stage, context):
            call_log.append(stage)
            iter_idx = context.get("iteration_index", 1)

            if stage == WorkflowStage.DOC_CODE_REVIEW and iter_idx == 1:
                # 第一次审查失败
                return StageExecutionResult(
                    stage=stage,
                    success=True,
                    summary="审查不通过",
                    artifacts={
                        "overall_passed": False,
                        "gap_count": 1,
                        "gap_list": [
                            {
                                "dimension": "D1 功能完成度",
                                "description": "功能 F-003 未实现",
                                "feature_id": "F-003",
                                "priority": "P0",
                                "suggestion": "实现 F-003",
                            }
                        ],
                    },
                )
            elif stage == WorkflowStage.DOC_CODE_REVIEW and iter_idx == 2:
                # 第二次审查通过
                return StageExecutionResult(
                    stage=stage,
                    success=True,
                    summary="审查通过",
                    artifacts={"overall_passed": True, "gap_count": 0, "gap_list": []},
                )
            return StageExecutionResult(
                stage=stage,
                success=True,
                summary=f"{stage.value} 完成",
                artifacts={},
            )

        controller = WorkflowLoopController(
            project_root=Path("/tmp/test-project"),
            stage_executor=fail_then_pass_executor,
            max_iterations=3,
        )
        result = controller.run()

        self.assertTrue(result.overall_success)
        self.assertEqual(result.total_iterations, 2)
        # 验证第一次迭代审查失败后回退到 DEVELOPMENT
        self.assertFalse(result.iterations[0].review_passed)
        self.assertEqual(result.iterations[0].rollback_to, WorkflowStage.DEVELOPMENT)
        # 验证第二次迭代从 DEVELOPMENT 开始
        self.assertTrue(result.iterations[1].review_passed)
        # 第二次迭代的第一个阶段应该是 DEVELOPMENT
        self.assertEqual(result.iterations[1].stages[0].stage, WorkflowStage.DEVELOPMENT)

    def test_w3_max_iterations_limit(self):
        """W3: 最大迭代次数限制。"""
        def always_fail_review_executor(stage, context):
            if stage == WorkflowStage.DOC_CODE_REVIEW:
                return StageExecutionResult(
                    stage=stage,
                    success=True,
                    summary="审查不通过",
                    artifacts={
                        "overall_passed": False,
                        "gap_count": 1,
                        "gap_list": [
                            {
                                "dimension": "D1 功能完成度",
                                "description": "功能缺失",
                                "feature_id": "F-001",
                                "priority": "P0",
                                "suggestion": "实现功能",
                            }
                        ],
                    },
                )
            return StageExecutionResult(
                stage=stage,
                success=True,
                summary=f"{stage.value} 完成",
                artifacts={},
            )

        controller = WorkflowLoopController(
            project_root=Path("/tmp/test-project"),
            stage_executor=always_fail_review_executor,
            max_iterations=2,
        )
        result = controller.run()

        self.assertFalse(result.overall_success)
        self.assertEqual(result.total_iterations, 2)
        self.assertEqual(result.max_iterations, 2)
        # 验证最终仍有缺口
        self.assertGreater(len(result.final_gaps), 0)

    def test_w5_summary_generation(self):
        """W5: 执行结果摘要生成。"""
        def success_executor(stage, context):
            if stage == WorkflowStage.DOC_CODE_REVIEW:
                return StageExecutionResult(
                    stage=stage,
                    success=True,
                    summary="审查通过",
                    artifacts={"overall_passed": True, "gap_count": 0, "gap_list": []},
                )
            return StageExecutionResult(
                stage=stage,
                success=True,
                summary=f"{stage.value} 完成",
                artifacts={},
            )

        controller = WorkflowLoopController(
            project_root=Path("/tmp/test-project"),
            stage_executor=success_executor,
            max_iterations=3,
        )
        result = controller.run()
        summary = result.summary()

        self.assertIn("八阶段工作流执行结果", summary)
        self.assertIn("✅ 成功", summary)
        self.assertIn("迭代 1", summary)
        self.assertIn("requirements", summary)
        self.assertIn("doc_code_review", summary)

    def test_w6_stage_failure_stops_iteration(self):
        """W6: 非审查阶段失败时终止当前迭代。"""
        def fail_at_dev_executor(stage, context):
            if stage == WorkflowStage.DEVELOPMENT:
                return StageExecutionResult(
                    stage=stage,
                    success=False,
                    summary="开发失败",
                    error="编译错误",
                )
            if stage == WorkflowStage.DOC_CODE_REVIEW:
                return StageExecutionResult(
                    stage=stage,
                    success=True,
                    summary="审查通过",
                    artifacts={"overall_passed": True, "gap_count": 0, "gap_list": []},
                )
            return StageExecutionResult(
                stage=stage,
                success=True,
                summary=f"{stage.value} 完成",
                artifacts={},
            )

        controller = WorkflowLoopController(
            project_root=Path("/tmp/test-project"),
            stage_executor=fail_at_dev_executor,
            max_iterations=1,
        )
        result = controller.run()

        # 由于开发阶段失败，审查阶段不会执行
        self.assertFalse(result.overall_success)
        # 验证迭代中执行了前 6 个阶段（1-6），阶段 6 失败后停止
        stages_executed = result.iterations[0].stages
        self.assertEqual(stages_executed[-1].stage, WorkflowStage.DEVELOPMENT)
        self.assertFalse(stages_executed[-1].success)

    def test_w7_workflow_stage_enum(self):
        """W7: WorkflowStage 枚举属性。"""
        self.assertEqual(WorkflowStage.REQUIREMENTS.stage_number, 1)
        self.assertEqual(WorkflowStage.DOC_CODE_REVIEW.stage_number, 8)
        self.assertEqual(WorkflowStage.REQUIREMENTS.role_name, "产品经理")
        self.assertEqual(WorkflowStage.DEVELOPMENT.role_name, "独立开发者")
        self.assertEqual(WorkflowStage.DOC_CODE_REVIEW.role_name, "多角色")
        self.assertEqual(WorkflowStage.REQUIREMENTS.output_name, "PRD 文档")
        self.assertEqual(WorkflowStage.DEVELOPMENT.output_name, "代码实现")

    def test_w8_accumulated_artifacts_across_iterations(self):
        """W8: 累计上下文跨迭代传递。

        验证第一次迭代产出的 artifacts 在第二次迭代的 exec_context["accumulated_artifacts"] 中可见。
        第二次迭代从 DEVELOPMENT 开始（因 D1 回退），所以检查 DEVELOPMENT 阶段接收到的累计上下文。
        """
        # 记录每次迭代接收到的 accumulated_artifacts
        received_accumulated = []

        def track_and_pass_executor(stage, context):
            iter_idx = context.get("iteration_index", 1)
            accumulated = context.get("accumulated_artifacts", {})

            if stage == WorkflowStage.REQUIREMENTS:
                # 阶段 1 产出 prd_path，并记录本次迭代接收到的累计上下文
                received_accumulated.append({
                    "iter": iter_idx,
                    "stage": stage.value,
                    "accumulated_before": dict(accumulated),
                })
                return StageExecutionResult(
                    stage=stage,
                    success=True,
                    summary="需求完成",
                    artifacts={"prd_path": "/tmp/prd.md", "prd_version": "1.0"},
                )

            if stage == WorkflowStage.DEVELOPMENT:
                # 开发阶段也记录累计上下文（第二次迭代从此开始）
                received_accumulated.append({
                    "iter": iter_idx,
                    "stage": stage.value,
                    "accumulated_before": dict(accumulated),
                })
                return StageExecutionResult(
                    stage=stage,
                    success=True,
                    summary="开发完成",
                    artifacts={"dev_completed": True},
                )

            if stage == WorkflowStage.DOC_CODE_REVIEW:
                # 审查阶段也记录累计上下文
                received_accumulated.append({
                    "iter": iter_idx,
                    "stage": stage.value,
                    "accumulated_at_review": dict(accumulated),
                })
                if iter_idx == 1:
                    return StageExecutionResult(
                        stage=stage,
                        success=True,
                        summary="审查不通过",
                        artifacts={
                            "overall_passed": False,
                            "gap_count": 1,
                            "gap_list": [
                                {
                                    "dimension": "D1 功能完成度",
                                    "description": "功能缺失",
                                    "feature_id": "F-001",
                                    "priority": "P0",
                                    "suggestion": "实现功能",
                                }
                            ],
                        },
                    )
                # 第二次审查通过
                return StageExecutionResult(
                    stage=stage,
                    success=True,
                    summary="审查通过",
                    artifacts={"overall_passed": True, "gap_count": 0, "gap_list": []},
                )

            return StageExecutionResult(
                stage=stage,
                success=True,
                summary=f"{stage.value} 完成",
                artifacts={},
            )

        controller = WorkflowLoopController(
            project_root=Path("/tmp/test-project"),
            stage_executor=track_and_pass_executor,
            max_iterations=3,
        )
        result = controller.run()

        # 验证工作流最终成功
        self.assertTrue(result.overall_success)
        self.assertEqual(result.total_iterations, 2)

        # 验证第一次迭代 REQUIREMENTS 开始时 accumulated_artifacts 为空
        iter1_req = next(
            r for r in received_accumulated
            if r["iter"] == 1 and r["stage"] == "requirements"
        )
        self.assertEqual(iter1_req["accumulated_before"], {})

        # 验证第二次迭代 DEVELOPMENT 开始时 accumulated_artifacts 包含第一次迭代的产出
        iter2_dev = next(
            r for r in received_accumulated
            if r["iter"] == 2 and r["stage"] == "development"
        )
        # 第二次迭代从 DEVELOPMENT 开始（回退），accumulated_artifacts 应保留 prd_path
        self.assertIn("prd_path", iter2_dev["accumulated_before"])
        self.assertEqual(iter2_dev["accumulated_before"]["prd_path"], "/tmp/prd.md")
        # 验证累计上下文包含 prd_version
        self.assertIn("prd_version", iter2_dev["accumulated_before"])

        # 验证审查阶段也能访问累计上下文
        iter2_review = next(
            r for r in received_accumulated
            if r["iter"] == 2 and r["stage"] == "doc_code_review"
        )
        self.assertIn("prd_path", iter2_review["accumulated_at_review"])

    def test_w9_end_to_end_with_real_review_handler(self):
        """W9: 端到端集成测试（真实 ReviewHandler）。

        使用真实 ReviewHandler 而非 mock executor，验证完整八阶段流程。
        """
        import tempfile
        import textwrap

        # 创建临时项目目录
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)

            # 创建 PRD 文档
            prd_path = project_root / "prd.md"
            prd_path.write_text(textwrap.dedent("""\
                # PRD

                ## 功能列表

                | 功能ID | 功能名称 | 功能描述 | 优先级 |
                |--------|----------|----------|--------|
                | F-001 | login | 登录 | P0 |

                ## 验收标准

                | 编号 | 描述 |
                |------|------|
                | AC-001 | 登录可用 |
                """), encoding="utf-8")

            # 创建架构文档
            arch_path = project_root / "architecture.md"
            arch_path.write_text(textwrap.dedent("""\
                # 架构

                ## 模块依赖

                - auth 依赖 database
                """), encoding="utf-8")

            # 创建源码
            src_dir = project_root / "src"
            src_dir.mkdir()
            (src_dir / "auth.py").write_text(textwrap.dedent("""\
                from database import Database

                class AuthService:
                    def login(self, username, password):
                        pass
                """), encoding="utf-8")

            # 创建测试目录
            test_dir = project_root / "tests"
            test_dir.mkdir()
            (test_dir / "test_auth.py").write_text(textwrap.dedent("""\
                # F-001 login 测试
                # AC-001 登录可用
                def test_login():
                    pass
                """), encoding="utf-8")

            # 创建测试脚本（模拟测试通过）
            test_script = project_root / "run_tests.py"
            test_script.write_text('print("2 passed")\nprint("0 failed")\n', encoding="utf-8")

            # 构造真实的 stage_executor
            # 阶段 1-7 使用简单实现，阶段 8 使用真实 ReviewHandler
            from autonomous.handlers.review_handler import ReviewHandler
            from autonomous.loop_controller import IterationContext, StageKind

            doc_paths = {
                "prd": prd_path,
                "architecture": arch_path,
            }
            test_command = f"python3 {test_script}"

            def real_executor(stage, context):
                if stage == WorkflowStage.REQUIREMENTS:
                    return StageExecutionResult(
                        stage=stage, success=True,
                        summary="PRD 已就绪",
                        artifacts={"prd_path": str(prd_path)},
                    )
                if stage == WorkflowStage.ARCHITECTURE:
                    return StageExecutionResult(
                        stage=stage, success=True,
                        summary="架构文档已就绪",
                        artifacts={"architecture_path": str(arch_path)},
                    )
                if stage in (WorkflowStage.UI_DESIGN, WorkflowStage.TASK_BREAKDOWN):
                    return StageExecutionResult(
                        stage=stage, success=True,
                        summary=f"{stage.value} 跳过（可选）",
                        artifacts={},
                    )
                if stage == WorkflowStage.TEST_DESIGN:
                    return StageExecutionResult(
                        stage=stage, success=True,
                        summary="测试计划已就绪",
                        artifacts={"test_plan_path": ""},
                    )
                if stage == WorkflowStage.DEVELOPMENT:
                    return StageExecutionResult(
                        stage=stage, success=True,
                        summary="代码已就绪",
                        artifacts={"development_completed": True},
                    )
                if stage == WorkflowStage.TEST_VERIFICATION:
                    return StageExecutionResult(
                        stage=stage, success=True,
                        summary="测试通过",
                        artifacts={"test_passed": 2, "test_failed": 0},
                    )
                if stage == WorkflowStage.DOC_CODE_REVIEW:
                    # 调用真实 ReviewHandler
                    iter_ctx = IterationContext(
                        run_id=f"w9-test-{context.get('iteration_index', 1)}",
                        iter_index=context.get("iteration_index", 1),
                        stage=StageKind.REVIEW,
                        current_plan="",
                        notes_snapshot="",
                        prev_results=[],
                        project_root=project_root,
                        worktree_path=project_root,
                        objective="端到端测试",
                        verify_artifacts={
                            "prd_path": str(prd_path),
                            "architecture_path": str(arch_path),
                            "test_command": test_command,
                        },
                    )
                    handler = ReviewHandler()
                    review_result = handler.handle(iter_ctx)
                    # 始终返回 success=True，让 WorkflowLoopController 处理审查结果
                    return StageExecutionResult(
                        stage=stage,
                        success=True,
                        summary=review_result.summary,
                        artifacts=review_result.artifacts,
                        error=review_result.error,
                    )
                return StageExecutionResult(
                    stage=stage, success=False,
                    summary=f"未知阶段: {stage.value}",
                    error="unknown stage",
                )

            controller = WorkflowLoopController(
                project_root=project_root,
                stage_executor=real_executor,
                max_iterations=2,
                doc_paths=doc_paths,
                test_command=test_command,
            )
            result = controller.run()

            # 验证工作流成功
            self.assertTrue(result.overall_success, f"工作流未成功: {result.summary()}")
            self.assertEqual(result.total_iterations, 1)
            self.assertTrue(result.iterations[0].review_passed)
            # 验证执行了全部 8 个阶段
            self.assertEqual(len(result.iterations[0].stages), 8)
            # 验证审查报告已生成
            self.assertTrue(
                result.iterations[0].stages[-1].artifacts.get("overall_passed", False)
            )

    def test_w10_d3_rollback_to_test_verification(self):
        """W10: D3 测试失败回退到 TEST_VERIFICATION 完整流程。

        验证 D3 缺口下回退后下一次迭代从 TEST_VERIFICATION 开始。
        """
        call_log = []

        def d3_fail_then_pass_executor(stage, context):
            call_log.append((context.get("iteration_index", 1), stage.value))
            iter_idx = context.get("iteration_index", 1)

            if stage == WorkflowStage.DOC_CODE_REVIEW and iter_idx == 1:
                # 第一次审查失败：D3 测试失败
                return StageExecutionResult(
                    stage=stage,
                    success=True,
                    summary="审查不通过：D3 测试失败",
                    artifacts={
                        "overall_passed": False,
                        "gap_count": 1,
                        "gap_list": [
                            {
                                "dimension": "D3 测试正确性",
                                "description": "2 个测试失败",
                                "feature_id": "",
                                "priority": "P0",
                                "suggestion": "修复测试",
                            }
                        ],
                    },
                )
            if stage == WorkflowStage.DOC_CODE_REVIEW and iter_idx == 2:
                # 第二次审查通过
                return StageExecutionResult(
                    stage=stage,
                    success=True,
                    summary="审查通过",
                    artifacts={"overall_passed": True, "gap_count": 0, "gap_list": []},
                )
            return StageExecutionResult(
                stage=stage,
                success=True,
                summary=f"{stage.value} 完成",
                artifacts={},
            )

        controller = WorkflowLoopController(
            project_root=Path("/tmp/test-project"),
            stage_executor=d3_fail_then_pass_executor,
            max_iterations=3,
        )
        result = controller.run()

        # 验证工作流最终成功
        self.assertTrue(result.overall_success)
        self.assertEqual(result.total_iterations, 2)

        # 验证第一次迭代审查失败后回退到 TEST_VERIFICATION
        self.assertFalse(result.iterations[0].review_passed)
        self.assertEqual(
            result.iterations[0].rollback_to, WorkflowStage.TEST_VERIFICATION
        )

        # 验证第二次迭代从 TEST_VERIFICATION 开始
        iter2_stages = result.iterations[1].stages
        self.assertEqual(iter2_stages[0].stage, WorkflowStage.TEST_VERIFICATION)

        # 验证第二次迭代只执行了 TEST_VERIFICATION 和 DOC_CODE_REVIEW 两个阶段
        self.assertEqual(len(iter2_stages), 2)
        self.assertEqual(iter2_stages[0].stage, WorkflowStage.TEST_VERIFICATION)
        self.assertEqual(iter2_stages[1].stage, WorkflowStage.DOC_CODE_REVIEW)

    def test_w11_workflow_stage_to_stage_kind_mapping(self):
        """W11: WorkflowStage.to_stage_kind() 映射方法。

        验证 WorkflowStage 与 StageKind 的映射关系（详见设计文档 §10.3.2）。
        """
        from autonomous.loop_controller import StageKind

        # 前 4 个阶段无对应 StageKind
        self.assertIsNone(WorkflowStage.REQUIREMENTS.to_stage_kind())
        self.assertIsNone(WorkflowStage.ARCHITECTURE.to_stage_kind())
        self.assertIsNone(WorkflowStage.UI_DESIGN.to_stage_kind())
        self.assertIsNone(WorkflowStage.TEST_DESIGN.to_stage_kind())

        # 后 4 个阶段有对应 StageKind
        self.assertEqual(WorkflowStage.TASK_BREAKDOWN.to_stage_kind(), StageKind.PLAN)
        self.assertEqual(WorkflowStage.DEVELOPMENT.to_stage_kind(), StageKind.DEV)
        self.assertEqual(WorkflowStage.TEST_VERIFICATION.to_stage_kind(), StageKind.VERIFY)
        self.assertEqual(WorkflowStage.DOC_CODE_REVIEW.to_stage_kind(), StageKind.REVIEW)


if __name__ == "__main__":
    unittest.main(verbosity=2)
