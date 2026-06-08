"""FixHandler - 修复阶段：基于 verify 错误分类并应用修复策略。

行为：
1. 接收 verify 阶段的错误信息
2. 分类错误（测试失败 / 安全问题 / 编译错误）
3. 应用修复策略（重试 / 调用 dispatcher 重做 / 标记 FATAL）
4. 返回 StageResult
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from autonomous.handlers.base import StageHandler, StageResult

if TYPE_CHECKING:
    from autonomous.loop_controller import IterationContext


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
    """修复阶段 handler。

    行为：
    1. 接收 verify 阶段的错误信息
    2. 分类错误（测试失败 / 安全问题 / 编译错误）
    3. 应用修复策略（重试 / 调用 dispatcher 重做 / 标记 FATAL）
    4. 返回 StageResult
    """

    name = "fix"
    kind = "fix"

    def __init__(
        self,
        dispatcher_adapter=None,
        max_fix_attempts: int = 2,
    ):
        """构造 FixHandler。

        Args:
            dispatcher_adapter: DispatcherAdapter 实例（用于调用 dispatcher 重做）
            max_fix_attempts: 最大连续 fix 尝试次数
        """
        self._dispatcher_adapter = dispatcher_adapter
        self._max_fix_attempts = max(1, max_fix_attempts)
        # 记录每轮的 fix 尝试次数
        self._fix_attempts_per_iter: dict = {}

    def do_handle(self, iter_ctx) -> StageResult:
        """实际处理：分析 verify 错误 + 应用修复。"""
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
        # 调用 dispatcher 重做
        if self._dispatcher_adapter is not None:
            fix_task = f"修复以下错误：\n\n"
            for cat in categories:
                fix_task += f"- [{cat.kind}] {cat.message}\n"
                if cat.file_hint:
                    fix_task += f"  文件: {cat.file_hint}\n"
                if cat.line_hint:
                    fix_task += f"  行: {cat.line_hint}\n"
            result = self._dispatcher_adapter.invoke(
                task=fix_task,
                agent="solo_coder",
            )
            self._fix_attempts_per_iter[iter_key] = attempts + 1
            if result.success:
                return StageResult(
                    kind="success",
                    summary=f"fix dispatcher 调用成功（attempt={attempts + 1}）",
                    artifacts={
                        "fix_attempts": attempts + 1,
                        "categories_addressed": len(categories),
                    },
                )
            return StageResult(
                kind="retriable",
                summary=f"fix dispatcher 失败（attempt={attempts + 1}）：{result.summary}",
                artifacts={"fix_attempts": attempts + 1},
            )
        return StageResult(
            kind="retriable",
            summary=f"未配置 DispatcherAdapter，无法自动修复（{len(categories)} 个错误）",
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
