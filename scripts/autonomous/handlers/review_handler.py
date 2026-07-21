#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文档对照代码审查阶段 Handler。

职责：
1. 收集阶段 1-7 的文档产出路径
2. 调用 DocCodeConsistencyChecker 执行六大维度检查
3. 生成审查报告文件
4. 根据审查结果返回 StageResult
   - 全部通过 → success
   - 有缺口 → retriable（回退到 dev 修复）
   - 检查器异常 → fatal
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional

# 确保能导入上层模块
_script_dir = Path(__file__).resolve().parent
_scripts_dir = _script_dir.parent.parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from autonomous.handlers.base import StageHandler, StageResult
from doc_code_consistency_checker import DocCodeConsistencyChecker, ConsistencyReport

if TYPE_CHECKING:
    from autonomous.loop_controller import IterationContext


class ReviewHandler(StageHandler):
    """文档对照代码审查阶段 Handler。

    行为：
    1. 从迭代上下文中获取项目根目录和文档路径
    2. 调用 DocCodeConsistencyChecker 执行六大维度检查
    3. 将审查报告写入文件
    4. 返回 StageResult：
       - 全部通过 → success
       - 有缺口 → retriable（回退到 dev 修复）
       - 检查器异常 → fatal
    """

    name = "review"
    kind = "review"

    def do_handle(self, iter_ctx: "IterationContext") -> StageResult:
        """执行文档对照代码审查。

        Args:
            iter_ctx: 迭代上下文，包含项目根目录、文档路径等信息

        Returns:
            StageResult: 审查结果
        """
        project_root = iter_ctx.project_root

        # 收集文档路径
        doc_paths = self._collect_doc_paths(project_root, iter_ctx)

        # 检查是否有可用文档
        available_docs = {k: v for k, v in doc_paths.items() if v and v.exists()}
        if not available_docs:
            return StageResult(
                kind="retriable",
                summary="文档对照审查跳过：未找到任何设计文档（PRD/架构/SPEC/测试计划）",
                artifacts={"review_skipped": True, "reason": "no_docs"},
                error="未找到设计文档，无法执行文档对照审查",
            )

        # 获取测试命令
        test_command = self._get_test_command(iter_ctx)

        # 构造检查器并执行检查
        try:
            checker = DocCodeConsistencyChecker(
                project_root=project_root,
                doc_paths=available_docs,
                test_command=test_command,
                test_timeout_sec=600.0,
            )
            report = checker.check_all()
        except Exception as e:
            return StageResult(
                kind="fatal",
                summary=f"文档对照审查异常: {type(e).__name__}: {e}",
                error=traceback.format_exc(),
            )

        # 生成审查报告文件
        report_path = self._write_report(project_root, report)

        # 构建 artifacts
        artifacts = {
            "review_report_path": str(report_path),
            "overall_passed": report.overall_passed,
            "gap_count": len(report.gap_list),
            "gap_list": [
                {
                    "dimension": g.dimension,
                    "description": g.description,
                    "feature_id": g.feature_id,
                    "priority": g.priority,
                    "suggestion": g.suggestion,
                }
                for g in report.gap_list
            ],
            "feature_total": len(report.feature_checks),
            "feature_implemented": sum(1 for f in report.feature_checks if f.status == "implemented"),
            "integration_total": len(report.integration_checks),
            "integration_connected": sum(1 for i in report.integration_checks if i.status == "connected"),
            "test_passed": report.test_result.passed if report.test_result else 0,
            "test_failed": report.test_result.failed if report.test_result else 0,
            "todo_total": len(report.todo_items),
            "todo_unimplemented": sum(1 for t in report.todo_items if not t.has_implementation),
            "deviation_count": len(report.deviation_items),
        }

        # 根据审查结果返回 StageResult
        if report.overall_passed:
            return StageResult(
                kind="success",
                summary=f"文档对照审查通过：{artifacts['feature_implemented']}/{artifacts['feature_total']} 功能已实现，"
                       f"测试 {artifacts['test_passed']} passed / {artifacts['test_failed']} failed，"
                       f"无 TODO/FIXME 残留，无文档偏离",
                artifacts=artifacts,
            )
        else:
            # 构建缺口摘要
            gap_summary = self._build_gap_summary(report)
            return StageResult(
                kind="retriable",
                summary=f"文档对照审查不通过：{len(report.gap_list)} 个缺口\n{gap_summary}",
                artifacts=artifacts,
                error=gap_summary,
            )

    def _collect_doc_paths(
        self, project_root: Path, iter_ctx: "IterationContext"
    ) -> Dict[str, Path]:
        """收集设计文档路径。

        搜索项目根目录下的常见文档位置：
        - docs/ 目录
        - 项目根目录的 .md 文件
        - artifacts/ 目录

        Args:
            project_root: 项目根目录
            iter_ctx: 迭代上下文

        Returns:
            Dict[str, Path]: 文档类型到路径的映射
        """
        doc_paths: Dict[str, Path] = {}

        # 从 verify_artifacts 中获取文档路径（如果前序阶段已产出）
        if iter_ctx.verify_artifacts:
            for key in ("prd_path", "architecture_path", "spec_path", "test_plan_path"):
                path_str = iter_ctx.verify_artifacts.get(key, "")
                if path_str:
                    p = Path(path_str)
                    doc_type = key.replace("_path", "")
                    doc_paths[doc_type] = p

        # 自动搜索常见文档位置
        search_dirs = [
            project_root / "docs",
            project_root / "doc",
            project_root / "artifacts",
            project_root,
        ]

        # 文档类型关键词映射
        type_keywords = {
            "prd": ["prd", "需求", "requirement", "产品需求"],
            "architecture": ["architecture", "架构", "arch", "设计"],
            "spec": ["spec", "规格", "specification"],
            "test_plan": ["test_plan", "测试计划", "test-plan", "测试方案"],
        }

        for search_dir in search_dirs:
            if not search_dir.exists() or not search_dir.is_dir():
                continue
            for md_file in search_dir.glob("*.md"):
                file_name_lower = md_file.name.lower()
                for doc_type, keywords in type_keywords.items():
                    if doc_type in doc_paths and doc_paths[doc_type].exists():
                        continue  # 已找到，跳过
                    if any(kw in file_name_lower for kw in keywords):
                        doc_paths[doc_type] = md_file
                        break

        return doc_paths

    def _get_test_command(self, iter_ctx: "IterationContext") -> str:
        """获取测试命令。

        优先从 verify_artifacts 获取，其次从 notes_snapshot 中提取。

        Args:
            iter_ctx: 迭代上下文

        Returns:
            str: 测试命令
        """
        # 从 verify_artifacts 获取
        if iter_ctx.verify_artifacts:
            cmd = iter_ctx.verify_artifacts.get("test_command", "")
            if cmd:
                return cmd
        # 默认命令
        return "python3 -m pytest -v 2>&1 || python3 -m unittest discover -s tests -p 'test_*.py' 2>&1"

    def _write_report(self, project_root: Path, report: ConsistencyReport) -> Path:
        """将审查报告写入文件。

        Args:
            project_root: 项目根目录
            report: 一致性检查报告

        Returns:
            Path: 报告文件路径
        """
        # 构造检查器以使用其报告生成方法
        checker = DocCodeConsistencyChecker(project_root=project_root)
        report_md = checker.generate_report(report)

        # 写入文件
        report_dir = project_root / "docs" / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"{report.project_name}-DOC-CODE-REVIEW-REPORT.md"
        report_path.write_text(report_md, encoding="utf-8")
        return report_path

    @staticmethod
    def _build_gap_summary(report: ConsistencyReport) -> str:
        """构建缺口摘要文本。

        Args:
            report: 一致性检查报告

        Returns:
            str: 缺口摘要文本
        """
        if not report.gap_list:
            return ""
        lines = ["缺口清单："]
        for idx, gap in enumerate(report.gap_list[:20], 1):  # 最多显示前 20 条
            lines.append(f"  {idx}. [{gap.priority}] {gap.dimension}: {gap.description}")
        if len(report.gap_list) > 20:
            lines.append(f"  ...（共 {len(report.gap_list)} 个缺口）")
        return "\n".join(lines)


__all__ = ["ReviewHandler"]
