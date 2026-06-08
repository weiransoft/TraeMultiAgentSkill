"""VerifyHandler - 验证阶段：跑测试 + 安全分析。

行为：
1. 执行 test_command（LoopConfig.test_command）
2. 收集 diff stats（GitDriver.diff_stats）
3. 简单安全检查（敏感关键词扫描）
4. 返回 StageResult
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

from autonomous.handlers.base import StageHandler, StageResult

if TYPE_CHECKING:
    from autonomous.loop_controller import IterationContext


class VerifyHandler(StageHandler):
    """验证阶段 handler。

    行为：
    1. 执行 test_command（LoopConfig.test_command）
    2. 收集 diff stats（GitDriver.diff_stats）
    3. 简单安全检查（敏感关键词扫描）
    4. 返回 StageResult
    """

    name = "verify"
    kind = "verify"

    # 敏感模式（简单的内置安全检查）
    _SENSITIVE_PATTERNS = [
        (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS Access Key"),
        (re.compile(r"sk-[A-Za-z0-9]{32,}"), "OpenAI/Anthropic API Key"),
        (re.compile(r"ghp_[A-Za-z0-9]{36,}"), "GitHub Personal Access Token"),
        (re.compile(r"password\s*=\s*['\"][^'\"]{8,}['\"]"), "hardcoded password"),
    ]

    def __init__(
        self,
        git_driver=None,
        test_command: str = "python3 -m unittest discover -s tests -p 'test_*.py'",
        security_analyzer: str = "builtin",
        test_timeout_sec: float = 600.0,
    ):
        """构造 VerifyHandler。

        Args:
            git_driver: GitDriver 实例
            test_command: 测试命令
            security_analyzer: 安全分析器名称
            test_timeout_sec: 测试超时
        """
        self._git_driver = git_driver
        self._test_command = test_command
        self._security_analyzer = security_analyzer
        self._test_timeout = max(10.0, float(test_timeout_sec))

    def do_handle(self, iter_ctx) -> StageResult:
        """实际处理：跑测试 + 安全分析。"""
        test_results = (0, 0, 0)  # passed, failed, skipped
        test_output = ""
        # 1. 执行测试
        if self._test_command:
            try:
                proc = subprocess.run(
                    self._test_command,
                    shell=True,
                    cwd=iter_ctx.worktree_path,
                    capture_output=True,
                    text=True,
                    timeout=self._test_timeout,
                    check=False,
                    env=os.environ.copy(),
                )
                test_output = proc.stdout + "\n" + proc.stderr
                # 简单解析：统计 PASSED / FAILED / SKIPPED
                passed = len(re.findall(r"\bPASS(?:ED)?\b|ok\b", proc.stdout, re.IGNORECASE))
                failed = len(re.findall(r"\bFAIL(?:ED)?\b|ERROR\b", proc.stdout + proc.stderr, re.IGNORECASE))
                skipped = len(re.findall(r"\bSKIP(?:PED)?\b", proc.stdout, re.IGNORECASE))
                test_results = (passed, failed, skipped)
            except subprocess.TimeoutExpired:
                return StageResult(
                    kind="retriable",
                    summary=f"测试超时（>{self._test_timeout}s）",
                    error="subprocess.TimeoutExpired",
                    artifacts={"test_results": list(test_results)},
                )
            except OSError as e:
                return StageResult(
                    kind="retriable",
                    summary=f"测试执行失败: {e}",
                    error=str(e),
                )
        # 2. 收集 diff stats
        diff_stats_data = (0, 0, 0, 0)  # files, added, removed, binary
        if self._git_driver is not None and self._git_driver.is_git_repo():
            try:
                stats = self._git_driver.diff_stats()
                diff_stats_data = (
                    stats.files_changed,
                    stats.lines_added,
                    stats.lines_removed,
                    stats.binary_files,
                )
            except Exception:
                pass
        # 3. 安全检查（简单内置）
        security_issues = []
        if self._security_analyzer == "builtin":
            security_issues = self._builtin_security_check(iter_ctx.worktree_path)
        # 4. 判定
        passed, failed, skipped = test_results
        if failed > 0:
            return StageResult(
                kind="retriable",
                summary=f"测试失败：{failed} failed / {passed} passed",
                artifacts={
                    "test_results": list(test_results),
                    "diff_stats": list(diff_stats_data),
                    "security_issues": security_issues,
                    "test_output_tail": test_output[-2000:],
                },
            )
        if security_issues:
            return StageResult(
                kind="fatal",
                summary=f"发现 {len(security_issues)} 个安全问题",
                artifacts={
                    "test_results": list(test_results),
                    "diff_stats": list(diff_stats_data),
                    "security_issues": security_issues,
                },
            )
        return StageResult(
            kind="success",
            summary=f"验证通过：{passed} passed / {failed} failed / {skipped} skipped",
            artifacts={
                "test_results": list(test_results),
                "diff_stats": list(diff_stats_data),
                "security_issues": [],
                "test_output_tail": test_output[-500:],
            },
        )

    def _builtin_security_check(self, worktree: Path) -> list:
        """内置安全检查：扫描敏感关键词。

        Args:
            worktree: 工作目录

        Returns:
            list: 安全问题列表
        """
        if not worktree.exists():
            return []
        issues = []
        # 限制扫描范围：仅 .py / .yaml / .yml / .json / .env / .sh / .ts / .js
        extensions = {".py", ".yaml", ".yml", ".json", ".env", ".sh", ".ts", ".js", ".md"}
        max_file_size = 1024 * 1024  # 1MB
        for p in worktree.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in extensions and p.name != ".env":
                continue
            # 跳过 .git / node_modules / venv
            if any(part in p.parts for part in (".git", "node_modules", ".venv", "venv", "__pycache__")):
                continue
            try:
                if p.stat().st_size > max_file_size:
                    continue
                content = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for pattern, label in self._SENSITIVE_PATTERNS:
                if pattern.search(content):
                    issues.append(
                        {
                            "file": str(p.relative_to(worktree)),
                            "issue": label,
                            "severity": "high",
                        }
                    )
        return issues


__all__ = ["VerifyHandler"]
