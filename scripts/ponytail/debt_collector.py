"""Ponytail 债务台账收割器。

设计目标：
- 扫描项目中的 `ponytail:` 注释（支持 # 和 //）
- 识别缺少升级触发条件的标记（no_trigger，腐烂风险）
- 生成技术债台账报告

债务标记规范：
    # ponytail: <简化说明>
    # ponytail: <已知上限>, <升级路径>

缺少升级路径的标记（no_trigger=True）有腐烂风险，
VerifyHandler 检测到 no_trigger 项 ≥ 3 即中止（ultra 模式安全加固）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set


@dataclass
class DebtEntry:
    """单条债务记录。

    字段说明：
    - file: 文件相对路径
    - line: 行号
    - content: 债务说明内容
    - has_ceiling: 是否标注了已知上限（如 lock/o(n)/scan/heuristic 等）
    - has_upgrade_path: 是否标注了升级路径（如 upgrade/if/when/switch/replace 等）
    - no_trigger: 是否缺少升级触发条件（= not has_upgrade_path，腐烂风险）
    """

    file: str
    line: int
    content: str
    has_ceiling: bool
    has_upgrade_path: bool
    no_trigger: bool


class DebtCollector:
    """债务台账收割器。

    线程安全保证：
    - collect 方法是纯函数（不修改实例状态）
    - 所有正则模式是类级不可变常量
    - 无共享可变状态
    """

    # 匹配 ponytail: 注释（支持 # 和 //）
    _DEBT_RE = re.compile(r'(#|//)\s*ponytail:\s*(.+)')

    # 默认排除目录（避免扫描 node_modules/.git/build 等）
    _DEFAULT_EXCLUDE: Set[str] = {
        "node_modules", ".git", "build", "__pycache__",
        ".venv", "venv", ".pytest_cache", ".mypy_cache",
        "dist", "target",
    }

    # 已知上限关键词（标注了上限的债务有明确的边界）
    _CEILING_KEYWORDS = {
        "lock", "o(n", "o(n²", "o(n^2", "scan",
        "heuristic", "naive", "global",
    }

    # 升级路径关键词（标注了升级路径的债务有明确的触发条件）
    _UPGRADE_KEYWORDS = {
        "upgrade", "if", "when", "switch", "replace",
        "migrate", "per-account", "trigger",
    }

    # 只扫描代码文件（避免扫描二进制/文档/配置）
    _CODE_EXTENSIONS = {
        ".py", ".js", ".ts", ".jsx", ".tsx", ".java",
        ".go", ".rs", ".c", ".cpp", ".h", ".sh",
    }

    def collect(
        self,
        project_root: Path,
        exclude_dirs: Optional[Set[str]] = None,
    ) -> List[DebtEntry]:
        """扫描项目中的 ponytail: 注释，生成债务台账。

        线程安全：纯函数，不修改实例状态。

        Args:
            project_root: 项目根目录
            exclude_dirs: 排除目录（默认 node_modules/.git/build 等）

        Returns:
            List[DebtEntry]: 债务记录列表（按文件路径 + 行号排序）
        """
        excludes = exclude_dirs if exclude_dirs is not None else self._DEFAULT_EXCLUDE
        entries: List[DebtEntry] = []

        if not project_root.exists() or not project_root.is_dir():
            return entries

        for path in project_root.rglob("*"):
            # 排除目录
            if any(part in excludes for part in path.parts):
                continue
            if not path.is_file():
                continue
            # 只扫描代码文件
            if path.suffix.lower() not in self._CODE_EXTENSIONS:
                continue

            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            lines = content.splitlines()
            for i, line in enumerate(lines, 1):
                m = self._DEBT_RE.search(line)
                if not m:
                    continue

                debt_content = m.group(2).strip()
                content_lower = debt_content.lower()
                has_ceiling = any(kw in content_lower for kw in self._CEILING_KEYWORDS)
                has_upgrade = any(kw in content_lower for kw in self._UPGRADE_KEYWORDS)
                no_trigger = not has_upgrade

                # 计算相对路径（容错：绝对路径回退到原路径）
                try:
                    rel_path = str(path.relative_to(project_root))
                except ValueError:
                    rel_path = str(path)

                entries.append(DebtEntry(
                    file=rel_path,
                    line=i,
                    content=debt_content,
                    has_ceiling=has_ceiling,
                    has_upgrade_path=has_upgrade,
                    no_trigger=no_trigger,
                ))

        # 按文件路径 + 行号排序（稳定排序，便于报告阅读）
        entries.sort(key=lambda e: (e.file, e.line))
        return entries

    def format_report(self, entries: List[DebtEntry]) -> str:
        """格式化债务报告。

        Args:
            entries: 债务记录列表

        Returns:
            str: 可读的债务报告文本
        """
        if not entries:
            return "No ponytail: debt. Clean ledger."

        lines = []
        no_trigger_count = 0
        for e in entries:
            lines.append(f"{e.file}:{e.line} — {e.content}")
            if e.no_trigger:
                no_trigger_count += 1

        lines.append(f"\n{len(entries)} markers, {no_trigger_count} with no trigger.")
        return "\n".join(lines)


__all__ = ["DebtCollector", "DebtEntry"]
