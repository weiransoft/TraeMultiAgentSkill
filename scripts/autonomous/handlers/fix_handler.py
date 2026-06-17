"""FixHandler - 修复阶段：基于 verify 错误分类并应用修复策略。

行为（v2 修订：Phase 0 验证后修复递归）：
1. 接收 verify 阶段的错误信息
2. 分类错误（测试失败 / 安全问题 / 编译错误）
3. 应用修复策略（重试 / 调用 _dispatch_via_claude_code 重做 / 标记 FATAL）
4. 注入"只改必要的"约束（Ponytail Surgical Changes 的可执行步骤）
5. 返回 StageResult

Phase 0 验证后的核心变更：
- 不再调用 DispatcherAdapter.invoke（会无限递归 autonomous plugin）
- 直接调用 _dispatch_via_claude_code（真正的 prompt 注入点）
- 注入 Ponytail 决策梯 + "只改必要的"修复约束
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from autonomous.handlers.base import StageHandler, StageResult

if TYPE_CHECKING:
    from autonomous.loop_controller import IterationContext
    from ponytail.ruleset import PonytailMode, PonytailRulesetEngine


@dataclass
class ErrorCategory:
    """错误分类。

    字段说明：
    - kind: test_failure / security_issue / compile_error / unknown
    - message: 错误消息
    - file_hint: 错误相关文件（如果可推断）
    - line_hint: 错误相关行号
    """

    kind: str
    message: str
    file_hint: Optional[str] = None
    line_hint: Optional[int] = None


class FixHandler(StageHandler):
    """修复阶段 handler（v2 修订：修复递归 + 注入修复约束）。

    行为：
    1. 接收 verify 阶段的错误信息
    2. 分类错误（测试失败 / 安全问题 / 编译错误）
    3. 应用修复策略（重试 / 调用 _dispatch_via_claude_code 重做 / 标记 FATAL）
    4. 注入"只改必要的"约束（Ponytail Surgical Changes）
    5. 返回 StageResult
    """

    name = "fix"
    kind = "fix"

    def __init__(
        self,
        dispatcher_adapter=None,
        max_fix_attempts: int = 2,
        ponytail_engine: "Optional[PonytailRulesetEngine]" = None,
        project_root: Optional[str] = None,
        ponytail_mode: "Optional[PonytailMode]" = None,
    ):
        """构造 FixHandler。

        Args:
            dispatcher_adapter: DispatcherAdapter 实例（保留用于兼容性，实际不再调用其 invoke）
            max_fix_attempts: 最大连续 fix 尝试次数
            ponytail_engine: Ponytail 决策梯引擎实例（None 则不注入决策梯）
            project_root: 项目根目录（_dispatch_via_claude_code 需要）
            ponytail_mode: 可选模式覆盖（None 则用角色默认强度 solo_coder=FULL）
        """
        self._dispatcher_adapter = dispatcher_adapter
        self._max_fix_attempts = max(1, max_fix_attempts)
        # 记录每轮的 fix 尝试次数
        self._fix_attempts_per_iter: dict = {}
        # Ponytail 决策梯引擎（线程安全，无状态修改）
        self._ponytail_engine = ponytail_engine
        self._project_root = project_root or "."
        self._ponytail_mode = ponytail_mode

    def do_handle(self, iter_ctx) -> StageResult:
        """实际处理：分析 verify 错误 + 应用修复（修复递归 + 注入约束）。"""
        # 1. 从 iter_ctx 获取 verify 阶段产出的错误信息
        verify_artifacts = iter_ctx.verify_artifacts or {}
        test_results = verify_artifacts.get("test_results", [0, 0, 0])
        security_issues = verify_artifacts.get("security_issues", [])
        test_output = verify_artifacts.get("test_output_tail", "")
        # 2. 分类错误
        categories: List[ErrorCategory] = []
        if test_results[1] > 0:
            # 测试失败 → 解析失败位置
            cat = self._classify_test_failure(test_output)
            categories.append(cat)
        for issue in security_issues:
            categories.append(
                ErrorCategory(
                    kind="security_issue",
                    message=issue.get("issue", "unknown"),
                    file_hint=issue.get("file"),
                )
            )
        if not categories:
            return StageResult(
                kind="success",
                summary="无需修复",
            )
        # 3. 安全问题 → FATAL（永远需要人工确认）
        security_cats = [c for c in categories if c.kind == "security_issue"]
        if security_cats:
            return StageResult(
                kind="fatal",
                summary=f"发现 {len(security_cats)} 个安全问题，需要人工确认",
                artifacts={
                    "categories": [
                        {
                            "kind": c.kind,
                            "message": c.message,
                            "file": c.file_hint,
                            "line": c.line_hint,
                        }
                        for c in security_cats
                    ],
                },
            )
        # 4. 测试失败 → 检查 fix 尝试次数 + 应用修复
        iter_key = iter_ctx.iter_index
        attempts = self._fix_attempts_per_iter.get(iter_key, 0)
        if attempts >= self._max_fix_attempts:
            return StageResult(
                kind="fatal",
                summary=f"iter {iter_key} 连续 fix 失败 {attempts} 次，已达上限",
            )

        # 5. 构造 fix_task（含"只改必要的"修复约束）
        fix_task = f"修复以下错误：\n\n"
        for cat in categories:
            fix_task += f"- [{cat.kind}] {cat.message}\n"
            if cat.file_hint:
                fix_task += f"  文件: {cat.file_hint}\n"
            if cat.line_hint:
                fix_task += f"  行: {cat.line_hint}\n"

        # 【新增】注入"只改必要的"修复约束（Ponytail Surgical Changes 的可执行步骤）
        fix_task += "\n## 修复约束\n"
        fix_task += "- 只改必要的：只修改导致错误的代码，不要溢出修改\n"
        fix_task += "- 不要顺手重构无关代码\n"
        fix_task += "- 修复后标记：`# ponytail: fix-only, no refactor`\n"

        # 6. 【修复】直接调用 _dispatch_via_claude_code（绕过递归）
        #    延迟导入避免循环依赖
        from dispatch.legacy import _dispatch_via_claude_code

        # 7. 构造 context（Ponytail 决策梯注入点）
        ponytail_prompt = ""
        if self._ponytail_engine is not None:
            ponytail_prompt = self._ponytail_engine.get_injection_prompt(
                role="solo_coder",
                mode=self._ponytail_mode,
            )

        context = {
            "task_id": iter_ctx.run_id,
            "project_root": str(self._project_root),
            "timestamp": datetime.now().isoformat(),
            "iter_index": iter_ctx.iter_index,
            "karpathy_principles": {
                "think_before_coding": "明确假设、问清楚、不隐藏困惑",
                "simplicity_first": "最小代码、无 speculative features",
                "surgical_changes": "只改必要的、不改无关的",
                "goal_driven": "定义成功标准、验证检查点",
            },
            # 【新增】Ponytail 决策梯注入
            "ponytail_decision_ladder": ponytail_prompt,
            # 【新增】修复阶段标记（供 _build_agent_prompt 识别）
            "fix_phase": True,
            "error_categories": [
                {
                    "kind": c.kind,
                    "message": c.message,
                    "file": c.file_hint,
                    "line": c.line_hint,
                }
                for c in categories
            ],
        }

        # 8. 调用 _dispatch_via_claude_code（真正的 prompt 注入点）
        success = _dispatch_via_claude_code(
            agent_type="solo_coder",
            task=fix_task,
            task_id=iter_ctx.run_id,
            project_root=str(self._project_root),
            progress={},
        )

        self._fix_attempts_per_iter[iter_key] = attempts + 1
        if success:
            return StageResult(
                kind="success",
                summary=f"fix 执行成功（attempt={attempts + 1}，_dispatch_via_claude_code）",
                artifacts={
                    "fix_attempts": attempts + 1,
                    "categories_addressed": len(categories),
                    "ponytail_injected": bool(ponytail_prompt),
                },
            )
        return StageResult(
            kind="retriable",
            summary=f"fix 执行失败（attempt={attempts + 1}）：_dispatch_via_claude_code 返回 False",
            artifacts={"fix_attempts": attempts + 1},
        )

    @staticmethod
    def _classify_test_failure(test_output: str) -> ErrorCategory:
        """分类测试失败。

        Returns:
            ErrorCategory: 错误分类
        """
        if not test_output:
            return ErrorCategory(kind="test_failure", message="未知测试失败")
        # 尝试提取 file:line
        m = re.search(
            r'File\s+"([^"]+)",\s+line\s+(\d+)',
            test_output,
        )
        if m:
            return ErrorCategory(
                kind="test_failure",
                message=m.group(0)[:200],
                file_hint=m.group(1),
                line_hint=int(m.group(2)),
            )
        # 尝试提取 AssertionError
        m = re.search(r"AssertionError[:\s]+(.{0,200})", test_output)
        if m:
            return ErrorCategory(
                kind="test_failure",
                message=m.group(0)[:200],
            )
        return ErrorCategory(
            kind="test_failure",
            message=test_output.splitlines()[-1][:200] if test_output else "未知错误",
        )


__all__ = ["FixHandler"]
