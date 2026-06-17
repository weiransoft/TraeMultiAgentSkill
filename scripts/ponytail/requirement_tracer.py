"""Ponytail 需求文档功能点追溯器。

设计目标：
- 解析需求文档（Markdown）中的功能点列表
- 扫描代码实现，检测需求功能点是否被实现
- 生成"需求 → 实现"追溯报告
- 检测红线违规：需求文档规定的功能未实现（红线第 8 条）

需求文档格式（Markdown）：
    ## 功能需求
    - [REQ-001] 用户登录
    - [REQ-002] 数据导出
    - [REQ-003] 权限管理

追溯逻辑：
    1. 从需求文档提取 [REQ-XXX] 标记的功能点
    2. 扫描代码中的注释/函数名/类名，检测是否包含功能点关键词
    3. 未检测到的功能点标记为"未实现"（红线违规）

线程安全保证：
- trace 方法是纯函数（不修改实例状态）
- 所有正则模式是类级不可变常量
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set


@dataclass
class Requirement:
    """单条需求记录。

    字段说明：
    - req_id: 需求 ID（如 REQ-001）
    - description: 需求描述
    - source_file: 需求来源文件
    - source_line: 需求来源行号
    - implemented: 是否检测到实现
    - implementation_files: 检测到的实现文件列表
    """

    req_id: str
    description: str
    source_file: str
    source_line: int
    implemented: bool = False
    implementation_files: List[str] = field(default_factory=list)


@dataclass
class TraceReport:
    """追溯报告。

    字段说明：
    - total: 需求总数
    - implemented: 已实现需求数
    - missing: 未实现需求数
    - missing_reqs: 未实现需求列表（红线违规）
    - requirements: 全部需求列表
    """

    total: int
    implemented: int
    missing: int
    missing_reqs: List[Requirement]
    requirements: List[Requirement]


class RequirementTracer:
    """需求文档功能点追溯器。

    线程安全保证：
    - trace 方法是纯函数（不修改实例状态）
    - 所有正则模式是类级不可变常量
    """

    # 匹配需求标记：[REQ-001] / [REQ-002] 等
    _REQ_RE = re.compile(r'\[REQ-(\d+)\]\s*(.+)')

    # 默认排除目录
    _DEFAULT_EXCLUDE: Set[str] = {
        "node_modules", ".git", "build", "__pycache__",
        ".venv", "venv", ".pytest_cache", ".mypy_cache",
        "dist", "target", "docs",
    }

    # 只扫描代码文件
    _CODE_EXTENSIONS = {
        ".py", ".js", ".ts", ".jsx", ".tsx", ".java",
        ".go", ".rs", ".c", ".cpp", ".h", ".sh",
    }

    # 需求文档扩展名
    _DOC_EXTENSIONS = {".md", ".txt", ".rst"}

    def parse_requirements(self, doc_path: Path) -> List[Requirement]:
        """从需求文档解析功能点列表。

        线程安全：纯函数，不修改实例状态。

        Args:
            doc_path: 需求文档路径（Markdown/TXT/RST）

        Returns:
            List[Requirement]: 需求列表（按行号排序）
        """
        requirements: List[Requirement] = []
        if not doc_path.exists() or not doc_path.is_file():
            return requirements

        try:
            content = doc_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return requirements

        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            m = self._REQ_RE.search(line)
            if not m:
                continue

            req_id = f"REQ-{m.group(1)}"
            description = m.group(2).strip()

            try:
                rel_path = str(doc_path)
            except Exception:
                rel_path = str(doc_path)

            requirements.append(Requirement(
                req_id=req_id,
                description=description,
                source_file=rel_path,
                source_line=i,
            ))

        return requirements

    def trace(
        self,
        doc_path: Path,
        project_root: Path,
        exclude_dirs: Optional[Set[str]] = None,
    ) -> TraceReport:
        """追溯需求功能点的实现情况。

        线程安全：纯函数，不修改实例状态。

        Args:
            doc_path: 需求文档路径
            project_root: 项目根目录（扫描代码实现）
            exclude_dirs: 排除目录（默认 node_modules/.git/build 等）

        Returns:
            TraceReport: 追溯报告（含未实现需求列表，红线违规）
        """
        excludes = exclude_dirs if exclude_dirs is not None else self._DEFAULT_EXCLUDE

        # 1. 解析需求
        requirements = self.parse_requirements(doc_path)
        if not requirements:
            return TraceReport(
                total=0, implemented=0, missing=0,
                missing_reqs=[], requirements=[],
            )

        # 2. 扫描代码，检测每个需求是否被实现
        #    检测逻辑：需求描述的关键词出现在代码注释/函数名/类名中
        code_files = self._collect_code_files(project_root, excludes)
        code_content_cache = self._build_code_content_cache(code_files)

        for req in requirements:
            keywords = self._extract_keywords(req.description)
            if not keywords:
                continue

            for file_path, content in code_content_cache.items():
                if self._is_implemented(content, keywords):
                    req.implemented = True
                    req.implementation_files.append(file_path)

        # 3. 生成报告
        implemented_count = sum(1 for r in requirements if r.implemented)
        missing_reqs = [r for r in requirements if not r.implemented]

        return TraceReport(
            total=len(requirements),
            implemented=implemented_count,
            missing=len(missing_reqs),
            missing_reqs=missing_reqs,
            requirements=requirements,
        )

    def format_report(self, report: TraceReport) -> str:
        """格式化追溯报告。

        Args:
            report: 追溯报告

        Returns:
            str: 可读的追溯报告文本
        """
        lines = [
            f"## 需求追溯报告",
            f"- 总数: {report.total}",
            f"- 已实现: {report.implemented}",
            f"- 未实现: {report.missing}",
            "",
        ]

        if report.missing_reqs:
            lines.append("### 未实现需求（红线违规）")
            for req in report.missing_reqs:
                lines.append(
                    f"- [{req.req_id}] {req.description} "
                    f"(来源: {req.source_file}:{req.source_line})"
                )
        else:
            lines.append("### 全部需求已实现")

        return "\n".join(lines)

    def _collect_code_files(
        self,
        project_root: Path,
        excludes: Set[str],
    ) -> List[Path]:
        """收集项目中的代码文件。"""
        if not project_root.exists() or not project_root.is_dir():
            return []

        files: List[Path] = []
        for path in project_root.rglob("*"):
            if any(part in excludes for part in path.parts):
                continue
            if not path.is_file():
                continue
            if path.suffix.lower() not in self._CODE_EXTENSIONS:
                continue
            files.append(path)
        return files

    def _build_code_content_cache(self, code_files: List[Path]) -> Dict[str, str]:
        """构建代码内容缓存（文件路径 → 内容）。

        一次性读取所有文件，避免重复 IO。
        """
        cache: Dict[str, str] = {}
        for path in code_files:
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
                cache[str(path)] = content
            except OSError:
                continue
        return cache

    def _extract_keywords(self, description: str) -> List[str]:
        """从需求描述中提取关键词。

        提取逻辑：
        - 英文：按空格分割，保留长度 ≥ 3 的词，转小写
        - 中文：按标点分割，保留长度 ≥ 2 的词
        - 中文额外提取 2-3 字的子串（提高匹配率）
        """
        if not description:
            return []

        keywords: List[str] = []
        # 英文关键词
        for word in re.split(r'[\s,;:|/\\]+', description):
            word = word.strip().lower()
            if len(word) >= 3 and word.isascii():
                keywords.append(word)

        # 中文关键词（按标点分割）
        chinese_parts = []
        for part in re.split(r'[\s,;:|/\\，；：、]+', description):
            part = part.strip()
            if len(part) >= 2 and not part.isascii():
                chinese_parts.append(part)
                # 额外提取 2-3 字的子串（提高匹配率）
                # 例如"用户登录功能" → ["用户登录", "登录功能", "用户登录功能"]
                if len(part) >= 3:
                    for i in range(len(part) - 1):
                        sub = part[i:i+2]
                        if len(sub) >= 2:
                            keywords.append(sub)
                    for i in range(len(part) - 2):
                        sub = part[i:i+3]
                        if len(sub) >= 3:
                            keywords.append(sub)

        keywords.extend(chinese_parts)

        # 去重（保留顺序）
        seen = set()
        unique_keywords = []
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                unique_keywords.append(kw)

        return unique_keywords

    def _is_implemented(self, content: str, keywords: List[str]) -> bool:
        """检测代码内容是否包含所有关键词（实现检测）。

        检测逻辑：
        - 至少匹配 50% 的关键词（容错，避免关键词过多时漏报）
        - 关键词匹配不区分大小写
        """
        if not keywords:
            return False

        content_lower = content.lower()
        matched = sum(1 for kw in keywords if kw.lower() in content_lower)
        threshold = max(1, len(keywords) // 2)
        return matched >= threshold


__all__ = ["RequirementTracer", "Requirement", "TraceReport"]
