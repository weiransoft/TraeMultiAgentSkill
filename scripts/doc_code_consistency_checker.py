#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文档对照代码一致性检查器。

职责：
1. 解析文档（PRD/SPEC/架构/测试计划）中的功能点、验收标准、集成关系
2. 扫描代码中的函数、类、模块、API、import 依赖
3. 逐项对照检查六大维度（D1~D6）
4. 生成结构化审查报告

六大维度：
- D1 功能完成度：文档中每个功能点是否有对应代码实现
- D2 集成完整性：文档定义的模块间集成关系是否在代码中体现
- D3 测试正确性：全部测试通过且覆盖文档功能
- D4 验收标准满足：文档中每条验收标准是否被代码满足
- D5 TODO/FIXME 清零：代码中无残留的未实现 TODO/FIXME
- D6 文档意图遵从：代码实现未偏离文档设计意图

支持语言：Python / JavaScript / TypeScript / Java / Go / Rust
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ------------------------------------------------------------------ #
# 数据结构定义                                                        #
# ------------------------------------------------------------------ #


@dataclass
class FeatureCheckItem:
    """功能完成度检查项。

    字段说明：
    - feature_id: 功能 ID（如 F-001）
    - feature_name: 功能名称
    - feature_desc: 功能描述
    - doc_source: 文档来源（如 "PRD §2.1"）
    - code_location: 代码位置（如 "auth.py:login()"），空表示未找到
    - status: 实现状态（"implemented" / "missing"）
    - evidence: 证据描述
    """

    feature_id: str
    feature_name: str
    feature_desc: str = ""
    doc_source: str = ""
    code_location: str = ""
    status: str = "missing"
    evidence: str = ""


@dataclass
class IntegrationCheckItem:
    """集成完整性检查项。

    字段说明：
    - integration_desc: 集成关系描述（如 "模块A→模块B"）
    - doc_source: 文档来源
    - code_location: 代码位置（如 "a.py: import b"），空表示未找到
    - status: 集成状态（"connected" / "missing"）
    """

    integration_desc: str
    doc_source: str = ""
    code_location: str = ""
    status: str = "missing"


@dataclass
class TestCheckResult:
    """测试正确性检查结果。

    字段说明：
    - test_command: 执行的测试命令
    - passed: 通过数
    - failed: 失败数
    - skipped: 跳过数
    - covered_features: 测试覆盖的功能 ID 列表
    - uncovered_features: 未覆盖的功能 ID 列表
    - test_output_tail: 测试输出末尾（诊断用）
    - duration_sec: 执行耗时（秒）
    """

    test_command: str = ""
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    covered_features: List[str] = field(default_factory=list)
    uncovered_features: List[str] = field(default_factory=list)
    test_output_tail: str = ""
    duration_sec: float = 0.0


@dataclass
class AcceptanceCheckItem:
    """验收标准检查项。

    字段说明：
    - criteria_id: 验收标准 ID（如 AC-001）
    - criteria_desc: 验收标准描述
    - doc_source: 文档来源
    - verification: 验证方式（"test" / "code" / "manual"）
    - status: 满足状态（"satisfied" / "unsatisfied"）
    """

    criteria_id: str
    criteria_desc: str = ""
    doc_source: str = ""
    verification: str = "manual"
    status: str = "unsatisfied"


@dataclass
class TodoItem:
    """TODO/FIXME 检查项。

    字段说明：
    - file_path: 文件路径
    - line_number: 行号
    - todo_type: 类型（"TODO" / "FIXME"）
    - content: 内容
    - has_implementation: 是否有对应实现
    """

    file_path: str
    line_number: int = 0
    todo_type: str = "TODO"
    content: str = ""
    has_implementation: bool = False


@dataclass
class DeviationItem:
    """文档意图偏离项。

    字段说明：
    - dimension: 偏离维度（如 "架构" / "功能范围" / "技术选型"）
    - doc_intent: 文档意图
    - code_reality: 代码实际情况
    - severity: 严重程度（"high" / "medium" / "low"）
    """

    dimension: str
    doc_intent: str = ""
    code_reality: str = ""
    severity: str = "low"


@dataclass
class GapItem:
    """缺口清单项。

    字段说明：
    - dimension: 所属维度（D1~D6）
    - description: 缺口描述
    - feature_id: 关联功能 ID（可选）
    - priority: 优先级（P0 / P1 / P2）
    - suggestion: 建议修复方式
    """

    dimension: str
    description: str = ""
    feature_id: str = ""
    priority: str = "P1"
    suggestion: str = ""


@dataclass
class ConsistencyReport:
    """一致性检查完整报告。

    字段说明：
    - project_name: 项目名称
    - check_time: 检查时间（ISO 格式）
    - feature_checks: D1 功能完成度检查项列表
    - integration_checks: D2 集成完整性检查项列表
    - test_result: D3 测试正确性检查结果
    - acceptance_checks: D4 验收标准检查项列表
    - todo_items: D5 TODO/FIXME 检查项列表
    - deviation_items: D6 文档意图偏离项列表
    - overall_passed: 最终判定（True=通过）
    - gap_list: 缺口清单
    """

    project_name: str = ""
    check_time: str = ""
    feature_checks: List[FeatureCheckItem] = field(default_factory=list)
    integration_checks: List[IntegrationCheckItem] = field(default_factory=list)
    test_result: Optional[TestCheckResult] = None
    acceptance_checks: List[AcceptanceCheckItem] = field(default_factory=list)
    todo_items: List[TodoItem] = field(default_factory=list)
    deviation_items: List[DeviationItem] = field(default_factory=list)
    overall_passed: bool = False
    gap_list: List[GapItem] = field(default_factory=list)


# ------------------------------------------------------------------ #
# 文档解析器                                                          #
# ------------------------------------------------------------------ #


class DocParser:
    """Markdown 文档解析器。

    职责：
    1. 解析功能列表表格（F-xxx 格式）
    2. 解析验收标准（AC-xxx 格式）
    3. 解析模块集成关系（A→B / A 依赖 B 格式）
    4. 提取文档章节标题用于来源定位
    """

    # 功能 ID 正则：F-001 / F001 / F_001
    _FEATURE_ID_PATTERN = re.compile(r"\bF[-_]?\d{3,}\b", re.IGNORECASE)
    # 验收标准 ID 正则：AC-001 / AC001
    _ACCEPTANCE_ID_PATTERN = re.compile(r"\bAC[-_]?\d{3,}\b", re.IGNORECASE)
    # 模块依赖正则：A→B / A->B / A 依赖 B / A 调用 B / A 模块 依赖 B 模块
    # 支持模块名后跟可选的 "模块" 后缀，避免将 "模块" 误匹配为源模块名
    _DEPENDENCY_PATTERN = re.compile(
        r"([\w\u4e00-\u9fff]+)(?:\s*模块)?\s*(?:→|->|依赖|调用|引用|import[s]?)\s*([\w\u4e00-\u9fff]+)(?:\s*模块)?"
    )
    # Markdown 表格行正则
    _TABLE_ROW_PATTERN = re.compile(r"^\|(.+)\|$", re.MULTILINE)
    # 章节标题正则
    _SECTION_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

    @classmethod
    def parse_features(cls, content: str, doc_name: str = "") -> List[Dict]:
        """解析文档中的功能列表表格。

        支持的表格格式：
        | 功能ID | 功能名称 | 功能描述 | 优先级 | 所属模块 | 状态 |
        | F-001 | 登录 | 用户登录功能 | P0 | auth | 待实现 |

        Args:
            content: 文档内容
            doc_name: 文档名称（用于来源标记）

        Returns:
            List[Dict]: 功能列表，每个元素含 feature_id, feature_name, feature_desc, section
        """
        features = []
        lines = content.split("\n")
        # 当前章节标题（用于来源定位）
        current_section = ""
        # 是否在功能表格内
        in_feature_table = False
        # 表头列索引映射
        col_map: Dict[str, int] = {}

        for line_idx, line in enumerate(lines):
            # 检测章节标题
            section_match = cls._SECTION_PATTERN.match(line)
            if section_match:
                current_section = section_match.group(2).strip()
                in_feature_table = False
                continue

            # 检测表格行
            if not line.strip().startswith("|"):
                in_feature_table = False
                continue

            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not cells:
                continue

            # 检测表头行（包含 "功能" 和 "ID" 或 "名称" 关键词）
            header_text = " ".join(cells).lower()
            if ("功能" in header_text or "feature" in header_text) and (
                "id" in header_text or "编号" in header_text or "标识" in header_text
            ):
                in_feature_table = True
                col_map = {}
                for idx, cell in enumerate(cells):
                    cell_lower = cell.lower()
                    if "id" in cell_lower or "编号" in cell_lower or "标识" in cell_lower:
                        col_map["id"] = idx
                    elif "名称" in cell_lower or "name" in cell_lower:
                        col_map["name"] = idx
                    elif "描述" in cell_lower or "desc" in cell_lower:
                        col_map["desc"] = idx
                    elif "优先" in cell_lower or "priority" in cell_lower:
                        col_map["priority"] = idx
                    elif "模块" in cell_lower or "module" in cell_lower:
                        col_map["module"] = idx
                    elif "状态" in cell_lower or "status" in cell_lower:
                        col_map["status"] = idx
                continue

            # 跳过分隔行（| --- | --- |）
            if all(re.match(r"^[-:\s]+$", c) for c in cells):
                continue

            # 解析功能行
            if in_feature_table and col_map:
                feature_id = cells[col_map["id"]] if "id" in col_map and col_map["id"] < len(cells) else ""
                # 检查是否是有效的功能 ID
                if not cls._FEATURE_ID_PATTERN.search(feature_id):
                    continue
                feature_name = cells[col_map["name"]] if "name" in col_map and col_map["name"] < len(cells) else ""
                feature_desc = cells[col_map["desc"]] if "desc" in col_map and col_map["desc"] < len(cells) else ""
                # 构建来源标记
                section_ref = f"{doc_name} §{current_section}" if current_section else doc_name
                features.append({
                    "feature_id": feature_id.strip(),
                    "feature_name": feature_name.strip(),
                    "feature_desc": feature_desc.strip(),
                    "section": section_ref,
                })

        # 如果表格解析未找到功能，尝试从全文提取功能 ID
        if not features:
            for match in cls._FEATURE_ID_PATTERN.finditer(content):
                fid = match.group(0).upper()
                # 提取上下文作为名称
                line_start = content.rfind("\n", 0, match.start()) + 1
                line_end = content.find("\n", match.end())
                if line_end < 0:
                    line_end = len(content)
                context_line = content[line_start:line_end].strip()
                # 去掉功能 ID 本身，剩余作为名称
                name = context_line.replace(match.group(0), "").strip(" -|：:")
                features.append({
                    "feature_id": fid,
                    "feature_name": name[:100] if name else fid,
                    "feature_desc": "",
                    "section": doc_name,
                })

        return features

    @classmethod
    def parse_acceptance_criteria(cls, content: str, doc_name: str = "") -> List[Dict]:
        """解析文档中的验收标准。

        支持格式：
        1. 表格：| AC-001 | 描述 | ... |
        2. 列表：- AC-001: 描述
        3. 章节内容："验收标准" 章节下的条目

        Args:
            content: 文档内容
            doc_name: 文档名称

        Returns:
            List[Dict]: 验收标准列表
        """
        criteria = []
        lines = content.split("\n")
        current_section = ""
        in_acceptance_section = False
        in_ac_table = False
        col_map: Dict[str, int] = {}

        for line in lines:
            section_match = cls._SECTION_PATTERN.match(line)
            if section_match:
                current_section = section_match.group(2).strip()
                # 检测"验收标准"章节
                in_acceptance_section = "验收" in current_section or "acceptance" in current_section.lower()
                in_ac_table = False
                continue

            # 表格行解析
            if line.strip().startswith("|"):
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                header_text = " ".join(cells).lower()
                # 检测验收标准表头
                # 在"验收标准"章节内，或表头包含"验收"/"验证"/"acceptance"关键词
                if (in_acceptance_section or "验收" in header_text
                        or "acceptance" in header_text or "验证" in header_text) and (
                    "id" in header_text or "编号" in header_text
                ):
                    in_ac_table = True
                    col_map = {}
                    for idx, cell in enumerate(cells):
                        cell_lower = cell.lower()
                        if "id" in cell_lower or "编号" in cell_lower:
                            col_map["id"] = idx
                        elif "描述" in cell_lower or "desc" in cell_lower or "标准" in cell_lower:
                            col_map["desc"] = idx
                        elif "验证" in cell_lower or "verify" in cell_lower:
                            col_map["verify"] = idx
                    continue

                # 跳过分隔行
                if all(re.match(r"^[-:\s]+$", c) for c in cells):
                    continue

                # 解析验收标准表格行
                if in_ac_table and col_map:
                    ac_id = cells[col_map["id"]] if "id" in col_map and col_map["id"] < len(cells) else ""
                    if cls._ACCEPTANCE_ID_PATTERN.search(ac_id):
                        ac_desc = cells[col_map["desc"]] if "desc" in col_map and col_map["desc"] < len(cells) else ""
                        section_ref = f"{doc_name} §{current_section}" if current_section else doc_name
                        criteria.append({
                            "criteria_id": ac_id.strip(),
                            "criteria_desc": ac_desc.strip(),
                            "section": section_ref,
                        })
                continue

            # 列表项解析：- AC-001: 描述
            if in_acceptance_section or True:  # 全局搜索列表项中的 AC ID
                list_match = re.match(r"^\s*[-*]\s*(AC[-_]?\d{3,}\s*[:：]?\s*.+)", line, re.IGNORECASE)
                if list_match:
                    text = list_match.group(1).strip()
                    id_match = cls._ACCEPTANCE_ID_PATTERN.search(text)
                    if id_match:
                        ac_id = id_match.group(0).upper()
                        ac_desc = text[id_match.end():].strip(":： \t")
                        section_ref = f"{doc_name} §{current_section}" if current_section else doc_name
                        # 去重
                        if not any(c["criteria_id"] == ac_id for c in criteria):
                            criteria.append({
                                "criteria_id": ac_id,
                                "criteria_desc": ac_desc,
                                "section": section_ref,
                            })

        return criteria

    @classmethod
    def parse_integration_relations(cls, content: str, doc_name: str = "") -> List[Dict]:
        """解析文档中的模块集成关系。

        支持格式：
        1. A→B / A->B
        2. A 依赖 B / A 调用 B / A 引用 B
        3. A imports B

        Args:
            content: 文档内容
            doc_name: 文档名称

        Returns:
            List[Dict]: 集成关系列表
        """
        relations = []
        lines = content.split("\n")
        current_section = ""

        for line in lines:
            section_match = cls._SECTION_PATTERN.match(line)
            if section_match:
                current_section = section_match.group(2).strip()
                continue

            # 跳过代码块内的内容
            if line.strip().startswith("```"):
                continue

            # 查找依赖关系
            for match in cls._DEPENDENCY_PATTERN.finditer(line):
                source_mod = match.group(1).strip()
                target_mod = match.group(2).strip()
                # 过滤掉太短的或非模块名的匹配
                if len(source_mod) < 2 or len(target_mod) < 2:
                    continue
                # 过滤掉常见非模块词
                skip_words = {"如果", "则", "否则", "当", "在", "通过", "使用", "基于"}
                if source_mod in skip_words or target_mod in skip_words:
                    continue
                desc = f"{source_mod}→{target_mod}"
                section_ref = f"{doc_name} §{current_section}" if current_section else doc_name
                # 去重
                if not any(r["integration_desc"] == desc for r in relations):
                    relations.append({
                        "integration_desc": desc,
                        "source": source_mod,
                        "target": target_mod,
                        "section": section_ref,
                    })

        return relations


# ------------------------------------------------------------------ #
# 代码扫描器                                                          #
# ------------------------------------------------------------------ #


@dataclass
class CodeSymbol:
    """代码符号（函数/类/模块）。

    字段说明：
    - name: 符号名称
    - symbol_type: 类型（"function" / "class" / "module"）
    - file_path: 文件路径
    - line_number: 行号
    - language: 编程语言
    """

    name: str
    symbol_type: str  # function / class / module
    file_path: str
    line_number: int = 0
    language: str = ""


@dataclass
class ImportRelation:
    """代码 import 关系。

    字段说明：
    - source_file: 源文件路径
    - imported_module: 被导入的模块名
    - import_type: 导入方式（"import" / "from" / "require" / "use"）
    - line_number: 行号
    - language: 编程语言
    """

    source_file: str
    imported_module: str
    import_type: str = "import"
    line_number: int = 0
    language: str = ""


class CodeScanner:
    """多语言代码扫描器。

    职责：
    1. 扫描源码中的函数、类定义
    2. 扫描 import / require / use 语句
    3. 扫描 TODO / FIXME 注释
    4. 支持多种编程语言

    支持语言：Python / JavaScript / TypeScript / Java / Go / Rust
    """

    # 支持的源码文件扩展名
    _SOURCE_EXTENSIONS = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".jsx": "javascript",
        ".tsx": "typescript",
        ".java": "java",
        ".go": "go",
        ".rs": "rust",
    }

    # 跳过的目录
    _SKIP_DIRS = {
        ".git", "node_modules", ".venv", "venv", "__pycache__",
        ".pytest_cache", "dist", "build", ".next", ".nuxt",
        "target", ".gradle", ".idea", ".vscode",
    }

    # 各语言函数定义正则
    _FUNCTION_PATTERNS = {
        "python": re.compile(
            r"^(?P<indent>[ \t]*)def\s+(?P<name>\w+)\s*\(", re.MULTILINE
        ),
        "javascript": re.compile(
            r"^(?P<indent>[ \t]*)"
            r"(?:export\s+)?(?:async\s+)?function\s+(?P<name>\w+)\s*\(",
            re.MULTILINE,
        ),
        "typescript": re.compile(
            r"^(?P<indent>[ \t]*)"
            r"(?:export\s+)?(?:async\s+)?function\s+(?P<name>\w+)\s*\(",
            re.MULTILINE,
        ),
        "java": re.compile(
            r"^(?P<indent>[ \t]*)"
            r"(?:public|private|protected|static|final|abstract|synchronized|\s)*"
            r"(?:[\w<>\[\]]+\s+)*"
            r"(?P<name>\w+)\s*\([^)]*\)\s*(?:\{|=>)",
            re.MULTILINE,
        ),
        "go": re.compile(
            r"^func\s+(?:\([^)]*\)\s+)?(?P<name>\w+)\s*\(", re.MULTILINE
        ),
        "rust": re.compile(
            r"^(?P<indent>[ \t]*)(?:pub\s+)?(?:async\s+)?fn\s+(?P<name>\w+)\s*\(",
            re.MULTILINE,
        ),
    }

    # 各语言类定义正则
    _CLASS_PATTERNS = {
        "python": re.compile(
            r"^class\s+(?P<name>\w+)", re.MULTILINE
        ),
        "javascript": re.compile(
            r"(?:export\s+)?class\s+(?P<name>\w+)", re.MULTILINE
        ),
        "typescript": re.compile(
            r"(?:export\s+)?(?:abstract\s+)?class\s+(?P<name>\w+)", re.MULTILINE
        ),
        "java": re.compile(
            r"(?:public|private|protected|static|final|abstract|\s)*class\s+(?P<name>\w+)",
            re.MULTILINE,
        ),
        "go": re.compile(
            r"type\s+(?P<name>\w+)\s+struct", re.MULTILINE
        ),
        "rust": re.compile(
            r"(?:pub\s+)?struct\s+(?P<name>\w+)", re.MULTILINE
        ),
    }

    # 各语言 import 正则
    _IMPORT_PATTERNS = {
        "python": [
            re.compile(r"^import\s+(?P<module>[\w.]+)", re.MULTILINE),
            re.compile(r"^from\s+(?P<module>[\w.]+)\s+import", re.MULTILINE),
        ],
        "javascript": [
            re.compile(r"import\s+.*\s+from\s+['\"](?P<module>[\w./@-]+)['\"]", re.MULTILINE),
            re.compile(r"require\s*\(\s*['\"](?P<module>[\w./@-]+)['\"]\s*\)", re.MULTILINE),
        ],
        "typescript": [
            re.compile(r"import\s+.*\s+from\s+['\"](?P<module>[\w./@-]+)['\"]", re.MULTILINE),
        ],
        "java": [
            re.compile(r"^import\s+(?P<module>[\w.]+);", re.MULTILINE),
        ],
        "go": [
            re.compile(r'"(?P<module>[\w./]+)"', re.MULTILINE),
        ],
        "rust": [
            re.compile(r"use\s+(?P<module>[\w:]+)", re.MULTILINE),
        ],
    }

    # TODO/FIXME 正则
    _TODO_PATTERN = re.compile(
        r"#\s*(TODO|FIXME)\s*[:：]?\s*(.+)",
        re.IGNORECASE,
    )
    _TODO_PATTERN_MULTI = re.compile(
        r"//\s*(TODO|FIXME)\s*[:：]?\s*(.+)",
        re.IGNORECASE,
    )
    _TODO_PATTERN_BLOCK = re.compile(
        r"\*\s*(TODO|FIXME)\s*[:：]?\s*(.+)",
        re.IGNORECASE,
    )

    # 最大文件大小（1MB）
    _MAX_FILE_SIZE = 1024 * 1024

    @classmethod
    def scan_project(cls, project_root: Path) -> Tuple[List[CodeSymbol], List[ImportRelation], List[TodoItem]]:
        """扫描项目全部源码。

        Args:
            project_root: 项目根目录

        Returns:
            Tuple: (代码符号列表, import 关系列表, TODO/FIXME 列表)
        """
        symbols: List[CodeSymbol] = []
        imports: List[ImportRelation] = []
        todos: List[TodoItem] = []

        if not project_root.exists():
            return symbols, imports, todos

        for file_path in cls._iter_source_files(project_root):
            language = cls._SOURCE_EXTENSIONS.get(file_path.suffix, "")
            if not language:
                continue
            try:
                if file_path.stat().st_size > cls._MAX_FILE_SIZE:
                    continue
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            rel_path = str(file_path.relative_to(project_root))

            # 扫描函数
            for symbol in cls._scan_functions(content, rel_path, language):
                symbols.append(symbol)
            # 扫描类
            for symbol in cls._scan_classes(content, rel_path, language):
                symbols.append(symbol)
            # 扫描 import
            for imp in cls._scan_imports(content, rel_path, language):
                imports.append(imp)
            # 扫描 TODO/FIXME
            for todo in cls._scan_todos(content, rel_path):
                todos.append(todo)

        return symbols, imports, todos

    @classmethod
    def _iter_source_files(cls, root: Path):
        """遍历项目中的源码文件，跳过排除目录。"""
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            # 跳过排除目录
            if any(part in cls._SKIP_DIRS for part in p.parts):
                continue
            # 仅处理支持的扩展名
            if p.suffix.lower() in cls._SOURCE_EXTENSIONS:
                yield p

    @classmethod
    def _scan_functions(cls, content: str, file_path: str, language: str) -> List[CodeSymbol]:
        """扫描函数定义。"""
        symbols = []
        pattern = cls._FUNCTION_PATTERNS.get(language)
        if not pattern:
            return symbols
        for match in pattern.finditer(content):
            name = match.group("name")
            # 计算行号
            line_num = content.count("\n", 0, match.start()) + 1
            symbols.append(CodeSymbol(
                name=name,
                symbol_type="function",
                file_path=file_path,
                line_number=line_num,
                language=language,
            ))
        return symbols

    @classmethod
    def _scan_classes(cls, content: str, file_path: str, language: str) -> List[CodeSymbol]:
        """扫描类定义。"""
        symbols = []
        pattern = cls._CLASS_PATTERNS.get(language)
        if not pattern:
            return symbols
        for match in pattern.finditer(content):
            name = match.group("name")
            line_num = content.count("\n", 0, match.start()) + 1
            symbols.append(CodeSymbol(
                name=name,
                symbol_type="class",
                file_path=file_path,
                line_number=line_num,
                language=language,
            ))
        return symbols

    @classmethod
    def _scan_imports(cls, content: str, file_path: str, language: str) -> List[ImportRelation]:
        """扫描 import 语句。"""
        imports = []
        patterns = cls._IMPORT_PATTERNS.get(language, [])
        for pattern in patterns:
            for match in pattern.finditer(content):
                module = match.group("module")
                line_num = content.count("\n", 0, match.start()) + 1
                imports.append(ImportRelation(
                    source_file=file_path,
                    imported_module=module,
                    import_type="import",
                    line_number=line_num,
                    language=language,
                ))
        return imports

    @classmethod
    def _scan_todos(cls, content: str, file_path: str) -> List[TodoItem]:
        """扫描 TODO/FIXME 注释。"""
        todos = []
        for pattern in [cls._TODO_PATTERN, cls._TODO_PATTERN_MULTI, cls._TODO_PATTERN_BLOCK]:
            for match in pattern.finditer(content):
                todo_type = match.group(1).upper()
                content_text = match.group(2).strip()
                line_num = content.count("\n", 0, match.start()) + 1
                # 检查是否有对应实现：搜索同文件中是否有与 TODO 内容相关的函数/类
                # 简单策略：如果 TODO 内容中提到函数名，检查该函数是否已定义
                has_impl = cls._check_todo_implementation(content, content_text)
                todos.append(TodoItem(
                    file_path=file_path,
                    line_number=line_num,
                    todo_type=todo_type,
                    content=content_text,
                    has_implementation=has_impl,
                ))
        # 去重（同一行可能被多个正则匹配）
        seen = set()
        unique_todos = []
        for todo in todos:
            key = (todo.file_path, todo.line_number, todo.content[:50])
            if key not in seen:
                seen.add(key)
                unique_todos.append(todo)
        return unique_todos

    @classmethod
    def _check_todo_implementation(cls, content: str, todo_content: str) -> bool:
        """检查 TODO/FIXME 是否有对应实现。

        策略：
        1. 从 TODO 内容中提取可能的函数名/类名关键词
        2. 在同文件中搜索是否有对应的 def/class/function 定义
        3. 如果找到，认为有对应实现

        Args:
            content: 文件内容
            todo_content: TODO/FIXME 内容

        Returns:
            bool: 是否有对应实现
        """
        # 提取关键词：中文 + 英文单词
        keywords = re.findall(r"[\w\u4e00-\u9fff]+", todo_content)
        # 过滤掉太短的关键词
        keywords = [kw for kw in keywords if len(kw) >= 3]
        if not keywords:
            return False

        # 搜索函数/类定义
        impl_patterns = [
            re.compile(r"^\s*def\s+(\w+)", re.MULTILINE),
            re.compile(r"^\s*function\s+(\w+)", re.MULTILINE),
            re.compile(r"^\s*class\s+(\w+)", re.MULTILINE),
            re.compile(r"^\s*fn\s+(\w+)", re.MULTILINE),
            re.compile(r"^\s*func\s+(\w+)", re.MULTILINE),
        ]

        defined_names = set()
        for pattern in impl_patterns:
            for match in pattern.finditer(content):
                defined_names.add(match.group(1).lower())

        # 检查关键词是否在已定义名称中出现
        for kw in keywords:
            kw_lower = kw.lower()
            for name in defined_names:
                if kw_lower in name or name in kw_lower:
                    return True
        return False


# ------------------------------------------------------------------ #
# 核心检查器                                                          #
# ------------------------------------------------------------------ #


class DocCodeConsistencyChecker:
    """文档对照代码一致性检查器。

    职责：
    1. 解析文档（PRD/SPEC/架构/测试计划）中的功能点、验收标准、集成关系
    2. 扫描代码中的函数、类、模块、API、import 依赖
    3. 逐项对照检查六大维度（D1~D6）
    4. 生成结构化审查报告

    使用方式：
        checker = DocCodeConsistencyChecker(
            project_root=Path("/path/to/project"),
            doc_paths={"prd": Path("prd.md"), "architecture": Path("arch.md")},
            test_command="python3 -m pytest",
        )
        report = checker.check_all()
        markdown_report = checker.generate_report(report)
    """

    def __init__(
        self,
        project_root: Path,
        doc_paths: Optional[Dict[str, Path]] = None,
        test_command: str = "",
        test_timeout_sec: float = 600.0,
    ):
        """构造检查器。

        Args:
            project_root: 项目根目录
            doc_paths: 文档路径字典，键为文档类型（prd/architecture/spec/test_plan），
                       值为文档文件路径。如果为 None，则自动在项目根目录下搜索
            test_command: 测试执行命令（空字符串则跳过测试检查）
            test_timeout_sec: 测试执行超时时间（秒）
        """
        self._project_root = Path(project_root).resolve()
        self._doc_paths = doc_paths or {}
        self._test_command = test_command
        self._test_timeout = max(10.0, float(test_timeout_sec))

        # 缓存：代码扫描结果
        self._symbols: List[CodeSymbol] = []
        self._imports: List[ImportRelation] = []
        self._todos: List[TodoItem] = []
        self._code_scanned = False

        # 缓存：文档解析结果
        self._features: List[Dict] = []
        self._acceptance_criteria: List[Dict] = []
        self._integration_relations: List[Dict] = []
        self._docs_parsed = False

    def check_all(self) -> ConsistencyReport:
        """执行全部六大维度检查，返回完整报告。

        Returns:
            ConsistencyReport: 一致性检查完整报告
        """
        # 1. 解析文档
        self._parse_documents()
        # 2. 扫描代码
        self._scan_code()
        # 3. 执行六大维度检查
        feature_checks = self.check_feature_completeness()
        integration_checks = self.check_integration_completeness()
        test_result = self.check_test_correctness()
        acceptance_checks = self.check_acceptance_criteria()
        todo_items = self.check_todo_fixme()
        deviation_items = self.check_doc_intent_alignment()
        # 4. 构建缺口清单
        gap_list = self._build_gap_list(
            feature_checks, integration_checks, test_result,
            acceptance_checks, todo_items, deviation_items,
        )
        # 5. 判定最终结果
        overall_passed = len(gap_list) == 0
        # 6. 构建报告
        report = ConsistencyReport(
            project_name=self._project_root.name,
            check_time=datetime.now(timezone.utc).isoformat(),
            feature_checks=feature_checks,
            integration_checks=integration_checks,
            test_result=test_result,
            acceptance_checks=acceptance_checks,
            todo_items=todo_items,
            deviation_items=deviation_items,
            overall_passed=overall_passed,
            gap_list=gap_list,
        )
        return report

    # ------------------------------------------------------------------ #
    # D1: 功能完成度检查                                                  #
    # ------------------------------------------------------------------ #

    def check_feature_completeness(self) -> List[FeatureCheckItem]:
        """D1: 功能完成度检查。

        将文档中的每个功能点与代码符号进行匹配，
        判断功能是否已实现。

        匹配策略：
        1. 功能名称关键词在函数名/类名中出现 → 已实现
        2. 功能 ID 在代码注释中出现 → 已实现
        3. 功能描述关键词在代码中密集出现 → 已实现
        4. 以上均不满足 → 未实现

        Returns:
            List[FeatureCheckItem]: 功能完成度检查项列表
        """
        if not self._docs_parsed:
            self._parse_documents()
        if not self._code_scanned:
            self._scan_code()

        results: List[FeatureCheckItem] = []
        for feature in self._features:
            fid = feature["feature_id"]
            fname = feature["feature_name"]
            fdesc = feature.get("feature_desc", "")
            section = feature.get("section", "")

            # 在代码符号中搜索匹配
            code_location = ""
            evidence = ""
            matched_symbol = self._match_feature_to_code(fname, fdesc, fid)
            if matched_symbol:
                code_location = f"{matched_symbol.file_path}:{matched_symbol.name}()"
                symbol_type_cn = "函数" if matched_symbol.symbol_type == "function" else "类"
                evidence = f"在 {matched_symbol.file_path} 中找到{symbol_type_cn} {matched_symbol.name}（行 {matched_symbol.line_number}）"
                status = "implemented"
            else:
                status = "missing"

            results.append(FeatureCheckItem(
                feature_id=fid,
                feature_name=fname,
                feature_desc=fdesc,
                doc_source=section,
                code_location=code_location,
                status=status,
                evidence=evidence,
            ))
        return results

    def _match_feature_to_code(
        self, feature_name: str, feature_desc: str, feature_id: str
    ) -> Optional[CodeSymbol]:
        """将功能点匹配到代码符号。

        匹配策略（按优先级）：
        1. 功能 ID 在代码注释中出现
        2. 功能名称的英文关键词在函数名/类名中出现
        3. 功能名称的中文关键词在代码注释中出现

        Args:
            feature_name: 功能名称
            feature_desc: 功能描述
            feature_id: 功能 ID

        Returns:
            Optional[CodeSymbol]: 匹配到的代码符号，None 表示未匹配
        """
        # 提取功能名称中的关键词
        keywords = self._extract_keywords(feature_name, feature_desc)
        if not keywords:
            return None

        # 策略1: 功能 ID 在符号名中出现
        fid_lower = feature_id.lower().replace("-", "").replace("_", "")
        for symbol in self._symbols:
            if fid_lower in symbol.name.lower():
                return symbol

        # 策略2: 关键词在函数名/类名中出现
        for symbol in self._symbols:
            symbol_name_lower = symbol.name.lower()
            for kw in keywords:
                kw_lower = kw.lower()
                if len(kw_lower) >= 3 and kw_lower in symbol_name_lower:
                    return symbol

        # 策略3: 关键词在文件路径中出现
        for symbol in self._symbols:
            file_path_lower = symbol.file_path.lower()
            for kw in keywords:
                kw_lower = kw.lower()
                if len(kw_lower) >= 4 and kw_lower in file_path_lower:
                    return symbol

        return None

    def _extract_keywords(self, *texts: str) -> List[str]:
        """从文本中提取关键词（英文单词 + 中文词语）。

        Args:
            texts: 一个或多个文本

        Returns:
            List[str]: 关键词列表
        """
        keywords = set()
        for text in texts:
            if not text:
                continue
            # 提取英文单词
            for word in re.findall(r"[a-zA-Z][a-zA-Z0-9_]+", text):
                if len(word) >= 3:
                    keywords.add(word)
            # 提取中文词语（2~6个连续中文字符）
            for match in re.finditer(r"[\u4e00-\u9fff]{2,6}", text):
                keywords.add(match.group(0))
        return list(keywords)

    # ------------------------------------------------------------------ #
    # D2: 集成完整性检查                                                  #
    # ------------------------------------------------------------------ #

    def check_integration_completeness(self) -> List[IntegrationCheckItem]:
        """D2: 集成完整性检查。

        将文档中定义的模块集成关系与代码中的 import 语句进行匹配，
        判断集成是否已实现。

        Returns:
            List[IntegrationCheckItem]: 集成完整性检查项列表
        """
        if not self._docs_parsed:
            self._parse_documents()
        if not self._code_scanned:
            self._scan_code()

        results: List[IntegrationCheckItem] = []
        for relation in self._integration_relations:
            desc = relation["integration_desc"]
            source_mod = relation.get("source", "")
            target_mod = relation.get("target", "")
            section = relation.get("section", "")

            # 在 import 关系中搜索匹配
            code_location = ""
            matched_import = self._match_integration_to_imports(source_mod, target_mod)
            if matched_import:
                code_location = f"{matched_import.source_file}: import {matched_import.imported_module}"
                status = "connected"
            else:
                status = "missing"

            results.append(IntegrationCheckItem(
                integration_desc=desc,
                doc_source=section,
                code_location=code_location,
                status=status,
            ))
        return results

    def _match_integration_to_imports(
        self, source_mod: str, target_mod: str
    ) -> Optional[ImportRelation]:
        """将集成关系匹配到代码 import。

        匹配策略：
        1. target_mod 在 import 的模块名中出现
        2. source_mod 在 import 的源文件路径中出现
        3. 放宽：仅匹配 target_mod

        Args:
            source_mod: 源模块名
            target_mod: 目标模块名

        Returns:
            Optional[ImportRelation]: 匹配到的 import 关系
        """
        target_lower = target_mod.lower()
        source_lower = source_mod.lower()

        # 策略1: target 在 import 模块名中出现，且 source 在文件路径中出现
        for imp in self._imports:
            imported_lower = imp.imported_module.lower()
            if target_lower in imported_lower and source_lower in imp.source_file.lower():
                return imp

        # 策略2: 仅 target 在 import 模块名中出现
        for imp in self._imports:
            imported_lower = imp.imported_module.lower()
            if target_lower in imported_lower:
                return imp

        # 策略3: target 在文件路径中出现（模块作为文件存在）
        for imp in self._imports:
            if target_lower in imp.source_file.lower():
                return imp

        return None

    # ------------------------------------------------------------------ #
    # D3: 测试正确性检查                                                  #
    # ------------------------------------------------------------------ #

    def check_test_correctness(self) -> TestCheckResult:
        """D3: 测试正确性检查。

        执行测试命令，解析通过/失败/跳过数量，
        并检查测试是否覆盖文档中定义的功能。

        Returns:
            TestCheckResult: 测试正确性检查结果
        """
        if not self._test_command:
            return TestCheckResult(
                test_command="(未配置测试命令)",
                test_output_tail="跳过测试执行：未配置测试命令",
            )
        if not self._docs_parsed:
            self._parse_documents()

        # 执行测试命令
        passed, failed, skipped = 0, 0, 0
        test_output = ""
        duration = 0.0

        try:
            start_time = time.time()
            proc = subprocess.run(
                self._test_command,
                shell=True,
                cwd=str(self._project_root),
                capture_output=True,
                text=True,
                timeout=self._test_timeout,
                check=False,
                env=os.environ.copy(),
            )
            duration = time.time() - start_time
            test_output = proc.stdout + "\n" + proc.stderr
            # 解析测试结果：分别使用独立正则匹配 passed / failed / skipped
            # 支持 pytest / unittest / mocha / jest 等格式的 summary 行
            combined_output = proc.stdout + "\n" + proc.stderr
            passed_match = re.search(r"(\d+)\s+passed", combined_output, re.IGNORECASE)
            failed_match = re.search(r"(\d+)\s+failed", combined_output, re.IGNORECASE)
            skipped_match = re.search(r"(\d+)\s+skipped", combined_output, re.IGNORECASE)
            if passed_match:
                passed = int(passed_match.group(1))
            if failed_match:
                failed = int(failed_match.group(1))
            if skipped_match:
                skipped = int(skipped_match.group(1))
        except subprocess.TimeoutExpired:
            return TestCheckResult(
                test_command=self._test_command,
                test_output_tail=f"测试超时（>{self._test_timeout}s）",
                duration_sec=self._test_timeout,
            )
        except OSError as e:
            return TestCheckResult(
                test_command=self._test_command,
                test_output_tail=f"测试执行失败: {e}",
            )

        # 检查功能覆盖：扫描测试文件中是否提及功能 ID
        covered_features: List[str] = []
        uncovered_features: List[str] = []
        for feature in self._features:
            fid = feature["feature_id"]
            fname = feature["feature_name"]
            # 在测试输出中搜索功能 ID 或功能名称
            if fid.lower() in test_output.lower() or fname.lower() in test_output.lower():
                covered_features.append(fid)
            else:
                # 扫描测试文件内容
                found_in_test = False
                test_dirs = ["tests", "test", "__tests__", "spec"]
                for test_dir in test_dirs:
                    test_path = self._project_root / test_dir
                    if test_path.exists():
                        for test_file in test_path.rglob("*"):
                            if not test_file.is_file():
                                continue
                            try:
                                test_content = test_file.read_text(encoding="utf-8", errors="ignore")
                                if fid.lower() in test_content.lower() or fname.lower() in test_content.lower():
                                    found_in_test = True
                                    break
                            except OSError:
                                continue
                    if found_in_test:
                        break
                if found_in_test:
                    covered_features.append(fid)
                else:
                    uncovered_features.append(fid)

        return TestCheckResult(
            test_command=self._test_command,
            passed=passed,
            failed=failed,
            skipped=skipped,
            covered_features=covered_features,
            uncovered_features=uncovered_features,
            test_output_tail=test_output[-2000:],
            duration_sec=duration,
        )

    # ------------------------------------------------------------------ #
    # D4: 验收标准满足检查                                                #
    # ------------------------------------------------------------------ #

    def check_acceptance_criteria(self) -> List[AcceptanceCheckItem]:
        """D4: 验收标准满足检查。

        将文档中的验收标准与代码和测试结果进行匹配，
        判断验收标准是否满足。

        判定策略：
        1. 如果验收标准描述中的关键词在测试输出中出现 → satisfied（测试验证）
        2. 如果验收标准描述中的关键词在代码符号中出现 → satisfied（代码验证）
        3. 否则 → unsatisfied

        Returns:
            List[AcceptanceCheckItem]: 验收标准检查项列表
        """
        if not self._docs_parsed:
            self._parse_documents()
        if not self._code_scanned:
            self._scan_code()

        results: List[AcceptanceCheckItem] = []
        for criteria in self._acceptance_criteria:
            ac_id = criteria["criteria_id"]
            ac_desc = criteria.get("criteria_desc", "")
            section = criteria.get("section", "")

            # 提取关键词
            keywords = self._extract_keywords(ac_desc)
            verification = "manual"
            status = "unsatisfied"

            # 策略1: 在代码符号中搜索
            for kw in keywords:
                kw_lower = kw.lower()
                for symbol in self._symbols:
                    if kw_lower in symbol.name.lower():
                        verification = "code"
                        status = "satisfied"
                        break
                if status == "satisfied":
                    break

            # 策略2: 如果代码未匹配，在测试输出中搜索
            if status != "satisfied" and self._test_command:
                # 获取测试输出（复用 check_test_correctness 的结果）
                test_result = self.check_test_correctness()
                test_output = test_result.test_output_tail
                for kw in keywords:
                    if kw.lower() in test_output.lower():
                        verification = "test"
                        status = "satisfied"
                        break

            # 策略3: 如果仍未匹配，在测试文件和源码文件内容中搜索 AC ID
            if status != "satisfied":
                ac_id_lower = ac_id.lower()
                # 搜索测试文件
                test_dirs = ["tests", "test", "__tests__", "spec"]
                for test_dir in test_dirs:
                    test_path = self._project_root / test_dir
                    if test_path.exists():
                        for test_file in test_path.rglob("*"):
                            if not test_file.is_file():
                                continue
                            try:
                                test_content = test_file.read_text(encoding="utf-8", errors="ignore")
                                if ac_id_lower in test_content.lower():
                                    verification = "test"
                                    status = "satisfied"
                                    break
                            except OSError:
                                continue
                    if status == "satisfied":
                        break
                # 搜索源码文件中的 AC ID 注释
                if status != "satisfied":
                    for symbol in self._symbols:
                        try:
                            src_path = self._project_root / symbol.file_path
                            if src_path.exists():
                                src_content = src_path.read_text(encoding="utf-8", errors="ignore")
                                if ac_id_lower in src_content.lower():
                                    verification = "code"
                                    status = "satisfied"
                                    break
                        except OSError:
                            continue

            results.append(AcceptanceCheckItem(
                criteria_id=ac_id,
                criteria_desc=ac_desc,
                doc_source=section,
                verification=verification,
                status=status,
            ))
        return results

    # ------------------------------------------------------------------ #
    # D5: TODO/FIXME 清零检查                                             #
    # ------------------------------------------------------------------ #

    def check_todo_fixme(self) -> List[TodoItem]:
        """D5: TODO/FIXME 清零检查。

        扫描代码中所有 TODO/FIXME 注释，
        检查是否有对应实现。

        Returns:
            List[TodoItem]: TODO/FIXME 检查项列表
        """
        if not self._code_scanned:
            self._scan_code()
        return list(self._todos)

    # ------------------------------------------------------------------ #
    # D6: 文档意图遵从检查                                                #
    # ------------------------------------------------------------------ #

    def check_doc_intent_alignment(self) -> List[DeviationItem]:
        """D6: 文档意图遵从检查。

        基于代码-文档关键词匹配，检测代码实现是否偏离文档设计意图。

        检查策略：
        1. 架构一致性：文档中提到的技术栈/框架是否在代码中使用
        2. 功能范围：代码中是否有文档未定义的额外模块（过度实现）
        3. 模块命名：文档中定义的模块名是否在代码中体现

        Returns:
            List[DeviationItem]: 偏离项列表
        """
        if not self._docs_parsed:
            self._parse_documents()
        if not self._code_scanned:
            self._scan_code()

        deviations: List[DeviationItem] = []

        # 检查1: 文档中提到的技术栈是否在代码中使用
        # 从架构文档中提取技术栈关键词
        tech_keywords = self._extract_tech_stack_from_docs()
        code_text = self._get_code_summary()
        for tech in tech_keywords:
            if tech.lower() not in code_text.lower():
                deviations.append(DeviationItem(
                    dimension="技术选型",
                    doc_intent=f"文档要求使用 {tech}",
                    code_reality=f"代码中未发现 {tech} 的使用",
                    severity="medium",
                ))

        # 检查2: 文档中定义的模块名是否在代码中体现
        for relation in self._integration_relations:
            source_mod = relation.get("source", "")
            target_mod = relation.get("target", "")
            # 检查模块名是否在文件路径、符号名或 import 关系中出现
            found_source = any(
                source_mod.lower() in s.file_path.lower() or source_mod.lower() in s.name.lower()
                for s in self._symbols
            ) or any(
                source_mod.lower() in imp.source_file.lower() or source_mod.lower() in imp.imported_module.lower()
                for imp in self._imports
            )
            found_target = any(
                target_mod.lower() in s.file_path.lower() or target_mod.lower() in s.name.lower()
                for s in self._symbols
            ) or any(
                target_mod.lower() in imp.source_file.lower() or target_mod.lower() in imp.imported_module.lower()
                for imp in self._imports
            )
            if not found_source:
                deviations.append(DeviationItem(
                    dimension="模块划分",
                    doc_intent=f"文档定义了模块 {source_mod}",
                    code_reality=f"代码中未发现 {source_mod} 相关文件或符号",
                    severity="low",
                ))
            if not found_target:
                deviations.append(DeviationItem(
                    dimension="模块划分",
                    doc_intent=f"文档定义了模块 {target_mod}",
                    code_reality=f"代码中未发现 {target_mod} 相关文件或符号",
                    severity="low",
                ))

        return deviations

    def _extract_tech_stack_from_docs(self) -> List[str]:
        """从文档中提取技术栈关键词。

        搜索文档中常见的"技术栈"/"框架"/"依赖"章节，
        提取技术名称。

        Returns:
            List[str]: 技术栈关键词列表
        """
        tech_keywords = set()
        # 常见技术栈关键词
        known_techs = [
            "Flask", "Django", "FastAPI", "Express", "Koa", "NestJS",
            "React", "Vue", "Angular", "Svelte",
            "PostgreSQL", "MySQL", "SQLite", "MongoDB", "Redis",
            "Docker", "Kubernetes", "Celery", "RabbitMQ",
            "pytest", "unittest", "jest", "mocha",
        ]
        for doc_path in self._doc_paths.values():
            if not doc_path or not doc_path.exists():
                continue
            try:
                content = doc_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for tech in known_techs:
                if tech.lower() in content.lower():
                    tech_keywords.add(tech)
        return list(tech_keywords)

    def _get_code_summary(self) -> str:
        """获取代码摘要文本（用于关键词匹配）。

        将所有代码符号的名称和文件路径拼接为文本。

        Returns:
            str: 代码摘要文本
        """
        parts = []
        for symbol in self._symbols:
            parts.append(f"{symbol.file_path}:{symbol.name}")
        for imp in self._imports:
            parts.append(f"import:{imp.imported_module}")
        return " ".join(parts)

    # ------------------------------------------------------------------ #
    # 缺口清单构建                                                        #
    # ------------------------------------------------------------------ #

    def _build_gap_list(
        self,
        feature_checks: List[FeatureCheckItem],
        integration_checks: List[IntegrationCheckItem],
        test_result: TestCheckResult,
        acceptance_checks: List[AcceptanceCheckItem],
        todo_items: List[TodoItem],
        deviation_items: List[DeviationItem],
    ) -> List[GapItem]:
        """构建缺口清单。

        汇总所有维度的不通过项，生成结构化缺口清单。

        Args:
            feature_checks: D1 检查结果
            integration_checks: D2 检查结果
            test_result: D3 检查结果
            acceptance_checks: D4 检查结果
            todo_items: D5 检查结果
            deviation_items: D6 检查结果

        Returns:
            List[GapItem]: 缺口清单
        """
        gaps: List[GapItem] = []

        # D1 缺口：未实现的功能
        for item in feature_checks:
            if item.status == "missing":
                gaps.append(GapItem(
                    dimension="D1 功能完成度",
                    description=f"功能 {item.feature_id}({item.feature_name}) 未实现",
                    feature_id=item.feature_id,
                    priority="P0",
                    suggestion=f"实现功能 {item.feature_name}，参考 {item.doc_source}",
                ))

        # D2 缺口：缺失的集成
        for item in integration_checks:
            if item.status == "missing":
                gaps.append(GapItem(
                    dimension="D2 集成完整性",
                    description=f"集成关系 {item.integration_desc} 缺失",
                    priority="P0",
                    suggestion=f"添加 {item.integration_desc} 的 import/调用关系，参考 {item.doc_source}",
                ))

        # D3 缺口：测试失败或未覆盖
        if test_result.failed > 0:
            gaps.append(GapItem(
                dimension="D3 测试正确性",
                description=f"测试失败：{test_result.failed} failed / {test_result.passed} passed",
                priority="P0",
                suggestion="修复失败的测试用例",
            ))
        if test_result.passed == 0 and test_result.failed == 0:
            gaps.append(GapItem(
                dimension="D3 测试正确性",
                description="无测试执行结果（可能未配置测试命令或测试为空）",
                priority="P1",
                suggestion="配置并执行测试命令",
            ))
        for fid in test_result.uncovered_features:
            gaps.append(GapItem(
                dimension="D3 测试正确性",
                description=f"功能 {fid} 未被测试覆盖",
                feature_id=fid,
                priority="P1",
                suggestion=f"为功能 {fid} 添加测试用例",
            ))

        # D4 缺口：未满足的验收标准
        for item in acceptance_checks:
            if item.status == "unsatisfied":
                gaps.append(GapItem(
                    dimension="D4 验收标准",
                    description=f"验收标准 {item.criteria_id}({item.criteria_desc[:50]}) 未满足",
                    priority="P1",
                    suggestion=f"实现验收标准 {item.criteria_id}，参考 {item.doc_source}",
                ))

        # D5 缺口：未实现的 TODO/FIXME
        for item in todo_items:
            if not item.has_implementation:
                gaps.append(GapItem(
                    dimension="D5 TODO/FIXME",
                    description=f"{item.todo_type} 未实现：{item.file_path}:{item.line_number} {item.content[:50]}",
                    priority="P1",
                    suggestion=f"实现或删除 {item.todo_type}：{item.file_path}:{item.line_number}",
                ))

        # D6 缺口：文档意图偏离
        for item in deviation_items:
            gaps.append(GapItem(
                dimension="D6 文档意图",
                description=f"{item.dimension} 偏离：{item.doc_intent}（实际：{item.code_reality}）",
                priority="P2" if item.severity == "low" else "P1",
                suggestion=f"对齐 {item.dimension}：{item.doc_intent}",
            ))

        return gaps

    # ------------------------------------------------------------------ #
    # 报告生成                                                            #
    # ------------------------------------------------------------------ #

    def generate_report(self, report: ConsistencyReport) -> str:
        """生成 Markdown 审查报告。

        Args:
            report: 一致性检查完整报告

        Returns:
            str: Markdown 格式的审查报告
        """
        lines: List[str] = []
        # 文档头部
        lines.append("# 文档对照代码审查报告")
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## 文档信息")
        lines.append("")
        lines.append("| 项目 | 内容 |")
        lines.append("|------|------|")
        lines.append(f"| 项目名称 | {report.project_name} |")
        lines.append(f"| 审查时间 | {report.check_time} |")
        lines.append("| 审查角色 | 架构师、独立开发者、测试专家 |")
        judgment = "✅ 审查通过" if report.overall_passed else "❌ 审查不通过"
        lines.append(f"| 最终判定 | {judgment} |")
        lines.append("| 报告版本 | v1.0 |")
        lines.append("")

        # 1. 审查概览
        lines.append("## 1. 审查概览")
        lines.append("")
        total_features = len(report.feature_checks)
        impl_features = sum(1 for f in report.feature_checks if f.status == "implemented")
        missing_features = total_features - impl_features
        total_integrations = len(report.integration_checks)
        connected_integrations = sum(1 for i in report.integration_checks if i.status == "connected")
        missing_integrations = total_integrations - connected_integrations
        total_criteria = len(report.acceptance_checks)
        satisfied_criteria = sum(1 for a in report.acceptance_checks if a.status == "satisfied")
        unsatisfied_criteria = total_criteria - satisfied_criteria
        total_todos = len(report.todo_items)
        impl_todos = sum(1 for t in report.todo_items if t.has_implementation)
        unimpl_todos = total_todos - impl_todos
        total_deviations = len(report.deviation_items)

        lines.append("### 1.1 审查范围")
        lines.append("")
        lines.append(f"- **功能点总数**: {total_features}")
        lines.append(f"- **集成关系总数**: {total_integrations}")
        lines.append(f"- **验收标准总数**: {total_criteria}")
        lines.append(f"- **TODO/FIXME 总数**: {total_todos}")
        lines.append("")

        lines.append("### 1.2 审查结果摘要")
        lines.append("")
        lines.append("| 维度 | 检查项 | 通过 | 不通过 | 通过率 | 判定 |")
        lines.append("|------|--------|------|--------|--------|------|")
        feature_rate = f"{impl_features * 100 // total_features}%" if total_features > 0 else "-"
        d1_pass = "✅" if missing_features == 0 else "❌"
        lines.append(f"| D1 功能完成度 | {total_features} | {impl_features} | {missing_features} | {feature_rate} | {d1_pass} |")
        int_rate = f"{connected_integrations * 100 // total_integrations}%" if total_integrations > 0 else "-"
        d2_pass = "✅" if missing_integrations == 0 else "❌"
        lines.append(f"| D2 集成完整性 | {total_integrations} | {connected_integrations} | {missing_integrations} | {int_rate} | {d2_pass} |")
        if report.test_result:
            tr = report.test_result
            d3_pass = "✅" if tr.failed == 0 and tr.passed > 0 else "❌"
            test_rate = f"{tr.passed * 100 // (tr.passed + tr.failed) if (tr.passed + tr.failed) > 0 else 0}%"
            lines.append(f"| D3 测试正确性 | {tr.passed + tr.failed} | {tr.passed} | {tr.failed} | {test_rate} | {d3_pass} |")
        ac_rate = f"{satisfied_criteria * 100 // total_criteria}%" if total_criteria > 0 else "-"
        d4_pass = "✅" if unsatisfied_criteria == 0 else "❌"
        lines.append(f"| D4 验收标准 | {total_criteria} | {satisfied_criteria} | {unsatisfied_criteria} | {ac_rate} | {d4_pass} |")
        d5_pass = "✅" if unimpl_todos == 0 else "❌"
        lines.append(f"| D5 TODO/FIXME | {total_todos} | {impl_todos} | {unimpl_todos} | - | {d5_pass} |")
        d6_pass = "✅" if total_deviations == 0 else "❌"
        lines.append(f"| D6 文档意图 | {total_deviations} | 0 | {total_deviations} | - | {d6_pass} |")
        lines.append("")

        # 2. D1 功能完成度
        lines.append("## 2. D1 功能完成度")
        lines.append("")
        if report.feature_checks:
            lines.append("### 2.1 功能对照清单")
            lines.append("")
            lines.append("| 功能 ID | 功能名称 | 文档来源 | 代码位置 | 状态 | 证据 |")
            lines.append("|---------|----------|----------|----------|------|------|")
            for item in report.feature_checks:
                status_cn = "✅ 已实现" if item.status == "implemented" else "❌ 未实现"
                lines.append(
                    f"| {item.feature_id} | {item.feature_name} | {item.doc_source} | "
                    f"{item.code_location or '-'} | {status_cn} | {item.evidence or '-'} |"
                )
            lines.append("")
        else:
            lines.append("（未解析到功能列表）")
            lines.append("")

        # 3. D2 集成完整性
        lines.append("## 3. D2 集成完整性")
        lines.append("")
        if report.integration_checks:
            lines.append("### 3.1 集成对照清单")
            lines.append("")
            lines.append("| 集成关系 | 文档来源 | 代码位置 | 状态 |")
            lines.append("|----------|----------|----------|------|")
            for item in report.integration_checks:
                status_cn = "✅ 已连通" if item.status == "connected" else "❌ 缺失"
                lines.append(
                    f"| {item.integration_desc} | {item.doc_source} | "
                    f"{item.code_location or '-'} | {status_cn} |"
                )
            lines.append("")
        else:
            lines.append("（未解析到集成关系）")
            lines.append("")

        # 4. D3 测试正确性
        lines.append("## 4. D3 测试正确性")
        lines.append("")
        if report.test_result:
            tr = report.test_result
            lines.append("### 4.1 测试执行结果")
            lines.append("")
            lines.append(f"- 测试命令: `{tr.test_command}`")
            lines.append(f"- 通过: {tr.passed}")
            lines.append(f"- 失败: {tr.failed}")
            lines.append(f"- 跳过: {tr.skipped}")
            lines.append(f"- 执行时间: {tr.duration_sec:.2f}s")
            lines.append("")
            if tr.covered_features or tr.uncovered_features:
                lines.append("### 4.2 功能覆盖检查")
                lines.append("")
                lines.append("| 功能 ID | 是否有测试 |")
                lines.append("|---------|-----------|")
                for fid in tr.covered_features:
                    lines.append(f"| {fid} | ✅ |")
                for fid in tr.uncovered_features:
                    lines.append(f"| {fid} | ❌ |")
                lines.append("")
            if tr.test_output_tail:
                lines.append("### 4.3 测试输出（末尾 2000 字符）")
                lines.append("")
                lines.append("```")
                lines.append(tr.test_output_tail[-2000:])
                lines.append("```")
                lines.append("")
        else:
            lines.append("（未执行测试检查）")
            lines.append("")

        # 5. D4 验收标准
        lines.append("## 5. D4 验收标准满足")
        lines.append("")
        if report.acceptance_checks:
            lines.append("### 5.1 验收标准对照清单")
            lines.append("")
            lines.append("| 验收标准 ID | 描述 | 文档来源 | 验证方式 | 状态 |")
            lines.append("|-------------|------|----------|----------|------|")
            for item in report.acceptance_checks:
                status_cn = "✅ 满足" if item.status == "satisfied" else "❌ 不满足"
                lines.append(
                    f"| {item.criteria_id} | {item.criteria_desc[:60]} | {item.doc_source} | "
                    f"{item.verification} | {status_cn} |"
                )
            lines.append("")
        else:
            lines.append("（未解析到验收标准）")
            lines.append("")

        # 6. D5 TODO/FIXME
        lines.append("## 6. D5 TODO/FIXME 清零")
        lines.append("")
        if report.todo_items:
            lines.append("### 6.1 TODO/FIXME 清单")
            lines.append("")
            lines.append("| 文件 | 行号 | 类型 | 内容 | 是否有对应实现 |")
            lines.append("|------|------|------|------|---------------|")
            for item in report.todo_items:
                impl_cn = "✅ 已实现" if item.has_implementation else "❌ 未实现"
                lines.append(
                    f"| {item.file_path} | {item.line_number} | {item.todo_type} | "
                    f"{item.content[:60]} | {impl_cn} |"
                )
            lines.append("")
        else:
            lines.append("✅ 无 TODO/FIXME 残留")
            lines.append("")

        # 7. D6 文档意图
        lines.append("## 7. D6 文档意图遵从")
        lines.append("")
        if report.deviation_items:
            lines.append("### 7.1 偏离清单")
            lines.append("")
            lines.append("| 偏离维度 | 文档意图 | 代码实际情况 | 严重程度 |")
            lines.append("|----------|----------|-------------|----------|")
            for item in report.deviation_items:
                lines.append(
                    f"| {item.dimension} | {item.doc_intent} | {item.code_reality} | {item.severity} |"
                )
            lines.append("")
        else:
            lines.append("✅ 无偏离项")
            lines.append("")

        # 8. 缺口清单
        lines.append("## 8. 缺口清单")
        lines.append("")
        if report.gap_list:
            lines.append("| # | 维度 | 缺口描述 | 功能 ID | 优先级 | 建议修复方式 |")
            lines.append("|---|------|----------|---------|--------|-------------|")
            for idx, gap in enumerate(report.gap_list, 1):
                lines.append(
                    f"| {idx} | {gap.dimension} | {gap.description} | "
                    f"{gap.feature_id or '-'} | {gap.priority} | {gap.suggestion} |"
                )
            lines.append("")
        else:
            lines.append("✅ 无缺口")
            lines.append("")

        # 9. 审查结论
        lines.append("## 9. 审查结论")
        lines.append("")
        if report.overall_passed:
            lines.append(f"- **最终判定**: ✅ 审查通过，可发布")
        else:
            lines.append(f"- **最终判定**: ❌ 审查不通过，需回退修复")
        lines.append(f"- **缺口总数**: {len(report.gap_list)}")
        if report.overall_passed:
            lines.append("- **建议操作**: 发布")
        else:
            lines.append("- **建议操作**: 回退到开发阶段修复缺口")
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("> 审查人签名: ________________  日期: ________________")

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # 内部辅助方法                                                        #
    # ------------------------------------------------------------------ #

    def _parse_documents(self) -> None:
        """解析所有文档，提取功能点、验收标准、集成关系。"""
        self._features = []
        self._acceptance_criteria = []
        self._integration_relations = []

        for doc_type, doc_path in self._doc_paths.items():
            if not doc_path or not doc_path.exists():
                continue
            try:
                content = doc_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            doc_name = doc_path.name
            # 解析功能列表
            features = DocParser.parse_features(content, doc_name)
            self._features.extend(features)
            # 解析验收标准
            criteria = DocParser.parse_acceptance_criteria(content, doc_name)
            self._acceptance_criteria.extend(criteria)
            # 解析集成关系（仅从架构文档解析）
            if doc_type == "architecture":
                relations = DocParser.parse_integration_relations(content, doc_name)
                self._integration_relations.extend(relations)

        # 去重
        self._features = self._dedup_features(self._features)
        self._acceptance_criteria = self._dedup_criteria(self._acceptance_criteria)
        self._integration_relations = self._dedup_relations(self._integration_relations)

        self._docs_parsed = True

    def _scan_code(self) -> None:
        """扫描项目代码，提取符号、import、TODO。"""
        self._symbols, self._imports, self._todos = CodeScanner.scan_project(self._project_root)
        self._code_scanned = True

    @staticmethod
    def _dedup_features(features: List[Dict]) -> List[Dict]:
        """功能列表去重（按 feature_id）。"""
        seen = set()
        result = []
        for f in features:
            fid = f["feature_id"]
            if fid not in seen:
                seen.add(fid)
                result.append(f)
        return result

    @staticmethod
    def _dedup_criteria(criteria: List[Dict]) -> List[Dict]:
        """验收标准去重（按 criteria_id）。"""
        seen = set()
        result = []
        for c in criteria:
            cid = c["criteria_id"]
            if cid not in seen:
                seen.add(cid)
                result.append(c)
        return result

    @staticmethod
    def _dedup_relations(relations: List[Dict]) -> List[Dict]:
        """集成关系去重（按 integration_desc）。"""
        seen = set()
        result = []
        for r in relations:
            desc = r["integration_desc"]
            if desc not in seen:
                seen.add(desc)
                result.append(r)
        return result


__all__ = [
    "FeatureCheckItem",
    "IntegrationCheckItem",
    "TestCheckResult",
    "AcceptanceCheckItem",
    "TodoItem",
    "DeviationItem",
    "GapItem",
    "ConsistencyReport",
    "CodeSymbol",
    "ImportRelation",
    "DocParser",
    "CodeScanner",
    "DocCodeConsistencyChecker",
]
