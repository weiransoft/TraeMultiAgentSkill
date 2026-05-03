#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
真正的多角色协作代码走读分析器 v2.0

本工具通过真正调用 Trae Agent 的多角色调度机制，实现：
1. 各角色使用专属 prompt 模板进行真实分析
2. 通过 Trae Agent 调度系统分配任务到各角色
3. 各角色独立分析并输出结果
4. 文档对齐引擎合并结果
5. 生成统一代码地图和审查报告

使用方法:
    python multi_role_collaborative_analyzer.py <project_root> [--workspace <workspace_root>]
"""

import os
import json
import argparse
import subprocess
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Set, Tuple
from dataclasses import dataclass, field, asdict
from collections import defaultdict
import concurrent.futures
import time


@dataclass
class RoleAnalysisResult:
    """角色分析结果"""
    role: str
    role_name: str
    timestamp: str
    project_path: str
    analysis_content: str
    key_findings: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    quality_issues: List[Dict[str, Any]] = field(default_factory=list)
    modules_identified: List[str] = field(default_factory=list)
    functions_analyzed: List[str] = field(default_factory=list)
    classes_analyzed: List[str] = field(default_factory=list)


@dataclass
class CodeElement:
    """代码元素"""
    name: str
    type: str
    file_path: str
    line_range: Tuple[int, int] = (0, 0)
    description: str = ""
    complexity: str = "low"
    role_perspectives: Dict[str, str] = field(default_factory=dict)


class RolePromptLoader:
    """角色专属 Prompt 加载器"""

    PROMPT_TEMPLATES = {
        "architect": {
            "name": "架构师",
            "template_path": "docs/spec/role-prompts/architect-code-analysis.md",
            "focus_areas": ["系统架构", "模块划分", "技术选型", "扩展性设计", "性能考量"],
            "task_template": """你是一位资深架构师，负责分析项目代码的架构设计。

请分析以下项目的代码结构，从架构师视角输出你的分析。

【Karpathy 原则提醒】
- 🧠 Think Before Coding: 分析前先明确系统边界和技术约束
- 🎯 Simplicity First: 评估架构是否过度设计，是否有不必要的抽象
- 🔬 Surgical Changes: 只关注架构问题，不碰业务逻辑
- ✅ Goal-Driven: 确保架构建议可验证、可落地

项目路径: {project_path}
工作空间: {workspace}

需要分析的内容：
1. 系统整体架构（架构风格、核心组件、技术选型）
2. 模块划分（模块边界、模块职责、模块依赖）
3. 设计模式识别
4. 扩展性评估
5. 性能考量
6. 逻辑漏洞检查（状态流转是否闭环、模块依赖是否有循环依赖、数据流是否清晰）

请阅读代码文件并输出你的分析结果，格式要求：
- 识别的模块列表
- 核心类和函数
- 调用关系
- 架构建议
- 逻辑漏洞清单（如有）

输出完成后，用 JSON 格式总结你的发现：
{{"modules": [...], "classes": [...], "functions": [...], "recommendations": [...], "logic_vulnerabilities": [...]}}
"""
        },
        "product_manager": {
            "name": "产品经理",
            "template_path": "docs/spec/role-prompts/pm-code-analysis.md",
            "focus_areas": ["业务功能", "用户流程", "数据需求", "需求完整性"],
            "task_template": """你是一位资深产品经理，负责分析项目代码的业务价值。

请分析以下项目的代码结构，从产品经理视角输出你的分析。

【Karpathy 原则提醒】
- 🧠 Think Before Coding: 分析前先明确业务目标和用户价值
- 🎯 Simplicity First: 识别过度功能，建议最小可行产品
- 🔬 Surgical Changes: 只关注业务逻辑问题，不碰技术实现
- ✅ Goal-Driven: 确保业务建议可衡量、可验证

项目路径: {project_path}
工作空间: {workspace}

需要分析的内容：
1. 核心业务功能（主要功能模块、用户价值）
2. 用户流程（关键操作路径、交互体验）
3. 数据需求（核心数据实体、数据流转）
4. 需求完整性（功能闭环、异常处理）
5. 产品亮点与改进点
6. 业务漏洞检查（业务流程是否可绕过、业务规则是否有漏洞、权限控制是否完善）

请阅读代码文件并输出你的分析结果，格式要求：
- 识别的业务模块
- 核心用户流程
- 数据模型
- 需求缺口
- 业务漏洞清单（如有）

输出完成后，用 JSON 格式总结你的发现：
{{"modules": [...], "user_flows": [...], "data_models": [...], "recommendations": [...], "business_vulnerabilities": [...]}}
"""
        },
        "solo_coder": {
            "name": "独立开发者",
            "template_path": "docs/spec/role-prompts/coder-code-analysis.md",
            "focus_areas": ["代码实现", "函数逻辑", "接口设计", "错误处理", "代码质量"],
            "task_template": """你是一位资深独立开发者，负责详细分析代码实现细节。

请分析以下项目的代码结构，从开发者视角输出你的分析。

【Karpathy 原则提醒】
- 🧠 Think Before Coding: 分析前先明确功能边界和输入输出
- 🎯 Simplicity First: 识别过度抽象，建议简化方案
- 🔬 Surgical Changes: 只改必要的代码，不碰无关功能
- ✅ Goal-Driven: 确保每个函数都有明确的测试覆盖

项目路径: {project_path}
工作空间: {workspace}

需要分析的内容：
1. 核心函数实现（函数逻辑、算法、参数设计）
2. 接口设计（API 设计、模块接口、一致性）
3. 错误处理（异常处理、边界条件、容错）
4. 代码质量（可读性、可维护性、可测试性）
5. 改进建议
6. 逻辑漏洞检查（条件判断缺陷、循环边界、状态机一致性、时序问题）

请阅读代码文件并输出你的分析结果，格式要求：
- 核心函数列表及说明
- 接口设计评估
- 错误处理覆盖
- 代码质量问题
- 逻辑漏洞清单（如有）

输出完成后，用 JSON 格式总结你的发现：
{{"functions": [...], "interfaces": [...], "quality_issues": [...], "recommendations": [...], "logic_vulnerabilities": [...]}}
"""
        },
        "ui_designer": {
            "name": "UI设计师",
            "template_path": "docs/spec/role-prompts/ui-code-analysis.md",
            "focus_areas": ["界面组件", "交互流程", "状态管理", "响应式设计", "用户体验"],
            "task_template": """你是一位资深 UI 设计师，负责分析代码中的界面和交互实现。

请分析以下项目的代码结构，从 UI 设计师视角输出你的分析。

【Karpathy 原则提醒】
- 🧠 Think Before Coding: 分析前先明确用户群体和使用场景
- 🎯 Simplicity First: 识别过度设计，建议最小必要组件
- 🔬 Surgical Changes: 只改需要的设计元素，不碰无关部分
- ✅ Goal-Driven: 确保设计有明确的验收标准

项目路径: {project_path}
工作空间: {workspace}

需要分析的内容：
1. 界面组件（组件设计、组件复用、组件结构）
2. 交互流程（用户操作、反馈机制、状态变化）
3. 状态管理（组件状态、全局状态、数据流）
4. 响应式设计（多端适配、不同屏幕尺寸）
5. 用户体验（可用性、可访问性、一致性）
6. 页面内联功能设计（行内编辑、就地修改、内联校验、撤销机制）
7. 操作流设计（最短路径、操作引导、操作连贯性、反馈闭环）

请阅读代码文件并输出你的分析结果，格式要求：
- 识别的 UI 组件
- 交互流程
- 状态管理方式
- 用户体验评估
- 页面内联功能评估
- 操作流评估

输出完成后，用 JSON 格式总结你的发现：
{{"components": [...], "interactions": [...], "state_management": [...], "recommendations": [...], "inline_features": [...], "operation_flows": [...]}}
"""
        },
        "test_expert": {
            "name": "测试专家",
            "template_path": "docs/spec/role-prompts/test-code-analysis.md",
            "focus_areas": ["测试覆盖", "边界条件", "异常处理", "测试策略", "质量风险"],
            "task_template": """你是一位资深测试专家，负责分析代码的测试覆盖和质量风险。

请分析以下项目的代码结构，从测试专家视角输出你的分析。

【Karpathy 原则提醒】
- 🧠 Think Before Coding: 分析前先明确测试范围和验证标准
- 🎯 Simplicity First: 测试用例最小必要，避免冗余测试
- 🔬 Surgical Changes: 只改需要的测试，不碰其他测试用例
- ✅ Goal-Driven: 定义明确的通过/失败标准

项目路径: {project_path}
工作空间: {workspace}

需要分析的内容：
1. 测试覆盖（单元测试、集成测试、E2E 测试）
2. 边界条件（空值、极限输入、特殊字符）
3. 异常处理（try-catch、错误码、超时处理）
4. 测试策略（测试金字塔、Mock 使用、测试数据）
5. 质量风险（安全风险、性能风险、可靠性）
6. 逻辑漏洞测试覆盖（条件分支、状态流转、并发场景）
7. 业务漏洞测试覆盖（业务流程、权限控制、数据一致性）

请阅读代码文件并输出你的分析结果，格式要求：
- 测试覆盖评估
- 边界条件处理
- 异常处理机制
- 质量风险点
- 逻辑漏洞测试覆盖评估
- 业务漏洞测试覆盖评估

输出完成后，用 JSON 格式总结你的发现：
{{"test_coverage": {...}, "boundary_conditions": [...], "quality_risks": [...], "recommendations": [...], "logic_vulnerability_coverage": [...], "business_vulnerability_coverage": [...]}}
"""
        },
        "logic_vulnerability_expert": {
            "name": "逻辑漏洞与业务漏洞审查专家",
            "template_path": "docs/spec/role-prompts/logic-vulnerability-analysis.md",
            "focus_areas": ["逻辑漏洞", "业务漏洞", "数据一致性", "权限与授权", "竞态条件"],
            "task_template": """你是一位资深逻辑漏洞与业务漏洞审查专家，负责深入分析代码中的逻辑漏洞、业务漏洞、数据一致性风险、权限绕过风险和竞态条件。

请分析以下项目的代码结构，从逻辑漏洞与业务漏洞审查专家视角输出你的分析。

【Karpathy 原则提醒】
- 🧠 Think Before Coding: 分析前先明确业务规则和预期行为
- 🎯 Simplicity First: 识别过度复杂的业务逻辑，建议简化
- 🔬 Surgical Changes: 只关注漏洞问题，不提出无关重构
- ✅ Goal-Driven: 确保每个漏洞都有明确的修复标准和验证方法

项目路径: {project_path}
工作空间: {workspace}

需要分析的内容：
1. 逻辑漏洞（条件判断缺陷、循环边界错误、状态机不一致、时序问题）
2. 业务漏洞（业务流程绕过、业务规则违反、数据完整性破坏、权限提升）
3. 数据一致性（事务边界、并发修改、脏读幻读、缓存不一致）
4. 权限与授权（水平越权、垂直越权、未授权访问、权限检查遗漏）
5. 竞态条件（并发安全、原子性缺失、死锁风险、资源竞争）

请阅读代码文件并输出你的分析结果，格式要求：
- 逻辑漏洞清单（含位置、风险等级、修复建议）
- 业务漏洞清单（含位置、风险等级、修复建议）
- 数据一致性风险清单
- 权限漏洞清单
- 竞态条件清单

输出完成后，用 JSON 格式总结你的发现：
{{"logic_vulnerabilities": [...], "business_vulnerabilities": [...], "data_consistency_risks": [...], "permission_vulnerabilities": [...], "race_conditions": [...], "recommendations": [...]}}
"""
        }
    }

    @classmethod
    def get_all_roles(cls) -> List[str]:
        """获取所有角色列表"""
        return list(cls.PROMPT_TEMPLATES.keys())

    @classmethod
    def get_role_info(cls, role: str) -> Dict[str, Any]:
        """获取角色信息"""
        return cls.PROMPT_TEMPLATES.get(role, {})

    @classmethod
    def get_task_template(cls, role: str, project_path: str, workspace: str) -> str:
        """获取角色的任务模板

        模板中的 {{ 和 }} 表示 JSON 对象，{...} 表示省略号，都需要特殊处理：
        1. 先将 {{ 和 }} 转换为占位符，避免 format() 误解析
        2. 将 {...} 转换为占位符，避免 format() 误解析
        3. 执行字符串替换变量
        4. 恢复 JSON 格式的双大括号和省略号
        """
        template = cls.PROMPT_TEMPLATES.get(role, {})
        task_template = template.get("task_template", "")

        # 第一步：将 {{ 和 }} 转换为占位符（避免 format() 误解析）
        task_template = task_template.replace("{{", "__DOUBLE_BRACE_OPEN__")
        task_template = task_template.replace("}}", "__DOUBLE_BRACE_CLOSE__")

        # 第二步：将 {...} 转换为占位符（避免 format() 误解析省略号）
        task_template = task_template.replace("{...}", "__ELLIPSIS__")

        # 第三步：执行字符串替换变量（使用 replace 而不是 format 避免位置参数问题）
        task_template = task_template.replace("{project_path}", project_path)
        task_template = task_template.replace("{workspace}", workspace)

        # 第四步：恢复被误转义的引号
        task_template = task_template.replace('\\"', '"')

        # 第五步：恢复双大括号（用于 JSON 格式）
        task_template = task_template.replace("__DOUBLE_BRACE_OPEN__", "{{")
        task_template = task_template.replace("__DOUBLE_BRACE_CLOSE__", "}}")

        # 第六步：恢复省略号
        task_template = task_template.replace("__ELLIPSIS__", "...")

        return task_template


class TraeAgentExecutor:
    """Trae Agent 执行器"""

    def __init__(self, skill_root: str):
        self.skill_root = Path(skill_root)
        self.dispatch_script = self.skill_root / "scripts" / "trae_agent_dispatch_v2.py"

    def execute_role_analysis(self, role: str, task: str, project_root: str, workspace: str) -> Dict[str, Any]:
        """
        执行角色的分析任务

        Args:
            role: 角色 ID
            task: 任务描述
            project_root: 项目根目录
            workspace: 工作空间

        Returns:
            分析结果字典
        """
        print(f"  [{role}] 开始执行角色分析...")

        role_info = RolePromptLoader.get_role_info(role)
        role_name = role_info.get("name", role)

        try:
            cmd = [
                "python3",
                str(self.dispatch_script),
                "--task", task,
                "--project-root", project_root,
                "--workspace", workspace,
                "--agent-type", role
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(self.skill_root)
            )

            output = result.stdout
            if result.stderr:
                output += "\n" + result.stderr

            return {
                "success": result.returncode == 0,
                "role": role,
                "role_name": role_name,
                "output": output,
                "returncode": result.returncode
            }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "role": role,
                "role_name": role_name,
                "output": "执行超时（5分钟）",
                "returncode": -1
            }
        except Exception as e:
            return {
                "success": False,
                "role": role,
                "role_name": role_name,
                "output": f"执行错误: {str(e)}",
                "returncode": -1
            }

    def execute_role_analysis_with_context(self, role: str, project_root: str, workspace: str,
                                          analysis_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        使用上下文执行角色分析

        Args:
            role: 角色 ID
            project_root: 项目根目录
            workspace: 工作空间
            analysis_context: 分析上下文（项目扫描结果等）

        Returns:
            分析结果字典
        """
        task_template = RolePromptLoader.get_task_template(role, project_root, workspace)

        task_with_context = f"""{task_template}

项目基本信息（由系统扫描获得）：
- 编程语言: {', '.join(analysis_context.get('languages', []))}
- 框架: {', '.join(analysis_context.get('frameworks', []))}
- 源文件数: {analysis_context.get('source_file_count', 0)}
- 模块数: {analysis_context.get('module_count', 0)}

已识别的模块: {', '.join(analysis_context.get('modules', [])[:10])}

请基于以上信息开始分析代码。
"""

        return self.execute_role_analysis(role, task_with_context, project_root, workspace)


class ProjectScanner:
    """项目扫描器 - 提取基本信息供各角色参考"""

    SOURCE_EXTENSIONS = {
        ".java", ".py", ".js", ".jsx", ".ts", ".tsx",
        ".go", ".rs", ".c", ".cpp", ".h", ".hpp", ".cs",
        ".rb", ".php", ".swift", ".kt", ".vue", ".svelte"
    }

    def __init__(self, project_root: str, workspace_root: Optional[str] = None):
        self.project_root = Path(project_root)
        self.workspace_root = Path(workspace_root) if workspace_root else self.project_root.parent
        self.source_files: List[Path] = []
        self.config_files: List[Path] = []
        self.project_info: Dict[str, Any] = {}

    def scan(self) -> Dict[str, Any]:
        """扫描项目"""
        print(f"开始扫描项目: {self.project_root}")

        for root, dirs, files in os.walk(self.project_root):
            dirs[:] = [d for d in dirs if self._should_keep_dir(d)]

            for file_name in files:
                file_path = Path(root) / file_name
                relative_path = file_path.relative_to(self.project_root)

                if file_path.suffix in self.SOURCE_EXTENSIONS:
                    self.source_files.append(file_path)
                elif self._is_config_file(file_name):
                    self.config_files.append(file_path)

        self._gather_project_info()

        return {
            "project_info": self.project_info,
            "source_files": [str(f) for f in self.source_files],
            "config_files": [str(f) for f in self.config_files]
        }

    def _should_keep_dir(self, dir_name: str) -> bool:
        """判断是否保留目录"""
        skip_dirs = {
            'node_modules', '.git', '__pycache__', 'target', 'build', 'dist',
            '.gradle', '.idea', '.vscode', 'vendor', 'venv', '.venv',
            '.cache', '.next', '.nuxt', '.turbo', 'coverage', '.pytest_cache',
            'bin', 'obj', '.vs', '.svn'
        }
        if dir_name.startswith('.') or dir_name in skip_dirs:
            return False
        return True

    def _is_config_file(self, file_name: str) -> bool:
        """判断是否为配置文件"""
        config_names = {
            'package.json', 'pom.xml', 'build.gradle', 'build.gradle.kts',
            'Dockerfile', 'Makefile', '.gitignore', 'docker-compose.yml',
            'requirements.txt', 'Gemfile', 'go.mod', 'go.sum', 'Cargo.toml'
        }
        return file_name in config_names

    def _gather_project_info(self):
        """收集项目信息"""
        try:
            relative_path = self.project_root.relative_to(self.workspace_root)
        except ValueError:
            relative_path = self.project_root

        self.project_info = {
            "name": self.project_root.name,
            "workspace_name": self.workspace_root.name,
            "workspace_path": str(self.workspace_root),
            "project_path": str(self.project_root),
            "relative_path": str(relative_path),
            "source_file_count": len(self.source_files),
            "config_file_count": len(self.config_files),
            "languages": self._detect_languages(),
            "frameworks": self._detect_frameworks(),
            "modules": self._detect_modules()
        }

    def _detect_languages(self) -> List[str]:
        """检测编程语言"""
        languages: Set[str] = set()
        ext_to_lang = {
            ".java": "Java", ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript",
            ".ts": "TypeScript", ".tsx": "TypeScript", ".go": "Go", ".rs": "Rust",
            ".c": "C", ".cpp": "C++", ".cs": "C#", ".rb": "Ruby", ".php": "PHP",
            ".swift": "Swift", ".kt": "Kotlin", ".vue": "Vue", ".svelte": "Svelte"
        }
        for f in self.source_files:
            lang = ext_to_lang.get(f.suffix)
            if lang:
                languages.add(lang)
        return sorted(list(languages))

    def _detect_frameworks(self) -> List[str]:
        """检测框架"""
        frameworks: Set[str] = set()

        for f in self.source_files[:50]:
            try:
                content = f.read_text(encoding='utf-8', errors='ignore')
                content_lower = content.lower()

                if 'spring' in content_lower or 'org.springframework' in content:
                    frameworks.add("Spring")
                if 'django' in content_lower:
                    frameworks.add("Django")
                if 'flask' in content_lower:
                    frameworks.add("Flask")
                if 'fastapi' in content_lower:
                    frameworks.add("FastAPI")
                if 'react' in content_lower:
                    frameworks.add("React")
                if 'vue' in content_lower and 'vue' not in frameworks:
                    frameworks.add("Vue")
                if 'angular' in content_lower:
                    frameworks.add("Angular")
                if 'express' in content_lower:
                    frameworks.add("Express")

            except Exception:
                pass

        return sorted(list(frameworks))

    def _detect_modules(self) -> List[str]:
        """检测模块"""
        modules: Set[str] = set()
        for f in self.source_files:
            parts = f.relative_to(self.project_root).parts
            if len(parts) > 1:
                modules.add(parts[0])
        return sorted(list(modules))


class MultiRoleCollaborativeAnalyzer:
    """真正的多角色协作分析器"""

    def __init__(self, project_root: str, workspace_root: Optional[str] = None, skill_root: Optional[str] = None):
        self.project_root = Path(project_root)
        self.workspace_root = Path(workspace_root) if workspace_root else self.project_root.parent
        self.skill_root = Path(skill_root) if skill_root else self._find_skill_root()

        self.scanner = ProjectScanner(str(self.project_root), str(self.workspace_root))
        self.executor = TraeAgentExecutor(str(self.skill_root))
        self.role_results: Dict[str, RoleAnalysisResult] = {}

    def _find_skill_root(self) -> Path:
        """查找 skill 根目录"""
        current = Path(__file__).parent
        while current != current.parent:
            if (current / "trae-agent").exists() or (current / "SKILL.md").exists():
                return current
            current = current.parent
        return Path(__file__).parent

    def run(self, output_dir: Optional[str] = None) -> Dict[str, Any]:
        """执行多角色协作分析"""
        print("=" * 60)
        print("多角色协作代码走读分析器 v2.0")
        print("=" * 60)

        if output_dir is None:
            output_dir = self.project_root

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n项目: {self.project_root.name}")
        print(f"工作空间: {self.workspace_root.name}")
        print(f"Skill 目录: {self.skill_root}")

        print("\n" + "-" * 40)
        print("步骤 1: 扫描项目...")
        print("-" * 40)

        project_data = self.scanner.scan()
        project_info = project_data["project_info"]

        print(f"  - 源文件: {project_info['source_file_count']}")
        print(f"  - 配置文件: {project_info['config_file_count']}")
        print(f"  - 语言: {', '.join(project_info['languages'])}")
        print(f"  - 模块: {', '.join(project_info['modules'][:5])}...")

        print("\n" + "-" * 40)
        print("步骤 2: 分发多角色分析任务...")
        print("-" * 40)

        analysis_context = {
            "languages": project_info["languages"],
            "frameworks": project_info["frameworks"],
            "source_file_count": project_info["source_file_count"],
            "module_count": len(project_info["modules"]),
            "modules": project_info["modules"]
        }

        roles = RolePromptLoader.get_all_roles()
        print(f"  - 参与角色: {', '.join(roles)}")

        for role in roles:
            role_info = RolePromptLoader.get_role_info(role)
            role_name = role_info.get("name", role)
            print(f"\n  [{role_name}] 分析中...")

            result = self.executor.execute_role_analysis_with_context(
                role, str(self.project_root), str(self.workspace_root), analysis_context
            )

            if result["success"]:
                analysis = RoleAnalysisResult(
                    role=role,
                    role_name=role_name,
                    timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    project_path=str(self.project_root),
                    analysis_content=result["output"],
                    key_findings=self._extract_key_findings(result["output"]),
                    recommendations=self._extract_recommendations(result["output"]),
                    quality_issues=self._extract_quality_issues(result["output"]),
                    modules_identified=self._extract_modules(result["output"]),
                    functions_analyzed=self._extract_functions(result["output"]),
                    classes_analyzed=self._extract_classes(result["output"])
                )
                self.role_results[role] = analysis
                print(f"  [{role_name}] ✓ 完成")
            else:
                print(f"  [{role_name}] ✗ 失败: {result.get('output', 'Unknown error')[:100]}")

        print("\n" + "-" * 40)
        print("步骤 3: 文档对齐...")
        print("-" * 40)

        aligned_result = self._align_documents(project_info)

        print("\n" + "-" * 40)
        print("步骤 4: 生成报告...")
        print("-" * 40)

        project_name = project_info['name']
        code_map_path = output_dir / f"{project_name}-ALIGNED-CODE-MAP.md"
        review_report_path = output_dir / f"{project_name}-CODE-REVIEW-REPORT.md"

        self._generate_unified_code_map(str(code_map_path), project_info, aligned_result)
        print(f"  - 统一代码地图: {code_map_path}")

        self._generate_review_report(str(review_report_path), project_info, aligned_result)
        print(f"  - 代码走读审查报告: {review_report_path}")

        results = {
            "project_info": project_info,
            "role_count": len(self.role_results),
            "analyzed_files": project_info["source_file_count"],
            "output_files": {
                "unified_code_map": str(code_map_path),
                "code_review_report": str(review_report_path)
            }
        }

        print("\n" + "=" * 60)
        print("多角色协作代码走读完成!")
        print("=" * 60)
        print(f"\n参与角色: {len(self.role_results)}/{len(roles)}")
        print(f"输出文件:")
        print(f"  - 统一代码地图: {code_map_path}")
        print(f"  - 代码走读审查报告: {review_report_path}")

        return results

    def _extract_key_findings(self, content: str) -> List[str]:
        """提取关键发现"""
        findings = []
        lines = content.split('\n')
        for line in lines:
            if any(kw in line.lower() for kw in ['发现', 'finding', '识别', 'identified', '核心', 'key']):
                findings.append(line.strip())
        return findings[:10]

    def _extract_recommendations(self, content: str) -> List[str]:
        """提取建议"""
        recommendations = []
        try:
            json_match = re.search(r'\{[^{}]*"recommendations"\s*:\s*\[[^\]]+\][^{}]*\}', content, re.DOTALL)
            if json_match:
                rec_data = json.loads(json_match.group())
                if isinstance(rec_data.get("recommendations"), list):
                    recommendations = rec_data["recommendations"][:10]
        except:
            lines = content.split('\n')
            for line in lines:
                if any(kw in line.lower() for kw in ['建议', 'recommend', 'suggest', '应该', 'should']):
                    recommendations.append(line.strip())
        return recommendations[:10]

    def _extract_quality_issues(self, content: str) -> List[Dict[str, Any]]:
        """提取质量问题"""
        issues = []
        try:
            json_match = re.search(r'\{[^{}]*"quality_issues"\s*:\s*\[[^\]]+\][^{}]*\}', content, re.DOTALL)
            if json_match:
                issue_data = json.loads(json_match.group())
                if isinstance(issue_data.get("quality_issues"), list):
                    issues = issue_data["quality_issues"]
        except:
            pass
        return issues[:10]

    def _extract_modules(self, content: str) -> List[str]:
        """提取模块"""
        modules = []
        try:
            json_match = re.search(r'\{[^{}]*"modules"\s*:\s*\[[^\]]+\][^{}]*\}', content, re.DOTALL)
            if json_match:
                mod_data = json.loads(json_match.group())
                if isinstance(mod_data.get("modules"), list):
                    modules = mod_data["modules"]
        except:
            pass
        return modules[:20]

    def _extract_functions(self, content: str) -> List[str]:
        """提取函数"""
        functions = []
        try:
            json_match = re.search(r'\{[^{}]*"functions"\s*:\s*\[[^\]]+\][^{}]*\}', content, re.DOTALL)
            if json_match:
                func_data = json.loads(json_match.group())
                if isinstance(func_data.get("functions"), list):
                    functions = func_data["functions"]
        except:
            pass
        return functions[:30]

    def _extract_classes(self, content: str) -> List[str]:
        """提取类"""
        classes = []
        try:
            json_match = re.search(r'\{[^{}]*"classes"\s*:\s*\[[^\]]+\][^{}]*\}', content, re.DOTALL)
            if json_match:
                cls_data = json.loads(json_match.group())
                if isinstance(cls_data.get("classes"), list):
                    classes = cls_data["classes"]
        except:
            pass
        return classes[:20]

    def _align_documents(self, project_info: Dict[str, Any]) -> Dict[str, Any]:
        """对齐各角色的分析文档"""
        aligned = {
            "project_summary": {
                "project_name": project_info["name"],
                "workspace_name": project_info["workspace_name"],
                "languages": project_info["languages"],
                "frameworks": project_info["frameworks"],
                "total_modules": len(project_info["modules"]),
                "total_files": project_info["source_file_count"]
            },
            "modules": project_info["modules"],
            "role_perspectives": {},
            "consensus": [],
            "discrepancies": []
        }

        all_modules = set()
        all_recommendations = []

        for role, analysis in self.role_results.items():
            aligned["role_perspectives"][role] = {
                "role_name": analysis.role_name,
                "key_findings": analysis.key_findings,
                "recommendations": analysis.recommendations,
                "modules": analysis.modules_identified,
                "functions": analysis.functions_analyzed,
                "classes": analysis.classes_analyzed
            }
            all_modules.update(analysis.modules_identified)
            all_recommendations.extend(analysis.recommendations)

        if len(self.role_results) > 0:
            aligned["consensus"].append(f"所有角色共同识别了 {len(all_modules)} 个模块")
            aligned["consensus"].append(f"共提出 {len(all_recommendations)} 条建议")

        return aligned

    def _generate_unified_code_map(self, output_path: str, project_info: Dict[str, Any], aligned: Dict[str, Any]):
        """生成统一代码地图"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        roles = [a.role_name for a in self.role_results.values()]

        md = f"""# {project_info['name']} 代码地图

> **生成时间**: {timestamp}
> **工作空间**: `{project_info['workspace_name']}`
> **多角色协作分析**: {len(self.role_results)} 个角色参与

---

## 文档说明

本代码地图由以下 {len(self.role_results)} 个角色协作分析生成：
{', '.join(roles)}

## 1. 项目概览

| 属性 | 值 |
|------|-----|
| **项目名称** | {project_info['name']} |
| **工作空间** | {project_info['workspace_name']} |
| **编程语言** | {', '.join(project_info['languages'])} |
| **框架** | {', '.join(project_info['frameworks']) if project_info['frameworks'] else '未检测到'} |
| **源文件数** | {project_info['source_file_count']} |
| **模块数** | {len(project_info['modules'])} |

## 2. 架构分层

| 层级 | 说明 | 典型目录 |
|------|------|----------|
| **API Layer** | HTTP 端点、路由处理 | controller, handler, route, api |
| **Service Layer** | 业务逻辑 | service, business, usecase |
| **Data Layer** | 数据持久化 | repository, model, dao |
| **Utility Layer** | 通用工具 | util, helper, common |
| **Middleware Layer** | 中间件 | middleware, interceptor |
| **Config Layer** | 配置 | config, setting |

## 3. 识别的模块

{', '.join(project_info['modules']) if project_info['modules'] else '未识别到模块'}

## 4. 多角色分析结果

### 4.1 架构师视角

"""
        if "architect" in self.role_results:
            arch = self.role_results["architect"]
            md += f"**关键发现**:\n"
            for finding in arch.key_findings[:5]:
                md += f"- {finding}\n"

        md += "\n### 4.2 产品经理视角\n\n"
        if "product_manager" in self.role_results:
            pm = self.role_results["product_manager"]
            md += f"**关键发现**:\n"
            for finding in pm.key_findings[:5]:
                md += f"- {finding}\n"

        md += "\n### 4.3 独立开发者视角\n\n"
        if "solo_coder" in self.role_results:
            coder = self.role_results["solo_coder"]
            md += f"**关键发现**:\n"
            for finding in coder.key_findings[:5]:
                md += f"- {finding}\n"

        md += "\n### 4.4 UI 设计师视角\n\n"
        if "ui_designer" in self.role_results:
            ui = self.role_results["ui_designer"]
            md += f"**关键发现**:\n"
            for finding in ui.key_findings[:5]:
                md += f"- {finding}\n"

        md += "\n### 4.5 测试专家视角\n\n"
        if "test_expert" in self.role_results:
            test = self.role_results["test_expert"]
            md += f"**关键发现**:\n"
            for finding in test.key_findings[:5]:
                md += f"- {finding}\n"

        md += f"""

## 5. 分析共识

"""
        for consensus in aligned.get("consensus", []):
            md += f"- {consensus}\n"

        md += f"""

---

*本代码地图由多角色协作分析生成*
*分析角色: {', '.join(roles)}*
*生成时间: {timestamp}*
"""

        Path(output_path).write_text(md, encoding='utf-8')

    def _generate_review_report(self, output_path: str, project_info: Dict[str, Any], aligned: Dict[str, Any]):
        """生成代码走读审查报告"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        roles = [a.role_name for a in self.role_results.values()]

        all_recommendations = []
        for analysis in self.role_results.values():
            all_recommendations.extend(analysis.recommendations)

        high_priority = [r for r in all_recommendations if any(kw in r.lower() for kw in ['安全', '性能', 'bug', 'critical', '紧急', '必须', '漏洞', '越权', '死锁', '竞态'])]
        medium_priority = [r for r in all_recommendations if r not in high_priority][:15]

        # 提取逻辑漏洞专家的分析结果
        logic_vuln_findings = []
        business_vuln_findings = []
        permission_vuln_findings = []
        race_condition_findings = []

        if "logic_vulnerability_expert" in self.role_results:
            logic_expert = self.role_results["logic_vulnerability_expert"]
            for finding in logic_expert.key_findings:
                if any(kw in finding.lower() for kw in ['逻辑', '条件', '循环', '状态机', '时序']):
                    logic_vuln_findings.append(finding)
                elif any(kw in finding.lower() for kw in ['业务', '流程', '规则', '完整性']):
                    business_vuln_findings.append(finding)
                elif any(kw in finding.lower() for kw in ['权限', '越权', '授权']):
                    permission_vuln_findings.append(finding)
                elif any(kw in finding.lower() for kw in ['竞态', '并发', '死锁', '原子']):
                    race_condition_findings.append(finding)

        md = f"""# {project_info['name']} 代码走读审查报告

> **生成时间**: {timestamp}
> **工作空间**: `{project_info['workspace_name']}`
> **审查角色数**: {len(self.role_results)} 个

---

## 1. 审查概述

| 属性 | 值 |
|------|-----|
| **项目名称** | {project_info['name']} |
| **工作空间** | {project_info['workspace_name']} |
| **编程语言** | {', '.join(project_info['languages'])} |
| **框架** | {', '.join(project_info['frameworks']) if project_info['frameworks'] else '未检测到'} |
| **源文件数** | {project_info['source_file_count']} |
| **模块数** | {len(project_info['modules'])} |

### 参与审查的角色

{', '.join(roles)}

## 2. 架构评审

### 2.1 架构风格评估

| 评估维度 | 评估结果 |
|----------|----------|
| **架构风格** | {'分层架构' if len(project_info['modules']) > 3 else '模块化架构'} |
| **模块数量** | {len(project_info['modules'])} 个 |
| **代码规模** | {'大' if project_info['source_file_count'] > 50 else '中' if project_info['source_file_count'] > 20 else '小'} |

### 2.2 架构建议

"""
        for role, perspective in aligned.get("role_perspectives", {}).items():
            if perspective.get("recommendations"):
                md += f"\n#### {perspective['role_name']} 建议\n\n"
                for rec in perspective["recommendations"][:3]:
                    md += f"- {rec}\n"

        md += f"""

## 3. 代码质量评估

### 3.1 代码复杂度

| 指标 | 数值 | 评估 |
|------|------|------|
| 总文件数 | {project_info['source_file_count']} | {'多' if project_info['source_file_count'] > 50 else '中' if project_info['source_file_count'] > 20 else '少'} |
| 总模块数 | {len(project_info['modules'])} | {'多' if len(project_info['modules']) > 10 else '中' if len(project_info['modules']) > 5 else '少'} |

### 3.2 质量风险

| 风险类型 | 风险等级 | 说明 |
|----------|----------|------|
| 代码规模 | {'高' if project_info['source_file_count'] > 100 else '中' if project_info['source_file_count'] > 50 else '低'} | {'建议关注重点模块' if project_info['source_file_count'] > 50 else '规模可控'} |
| 模块划分 | {'建议优化' if len(project_info['modules']) > 15 else '划分合理'} | - |

## 4. 逻辑漏洞与业务漏洞审查

### 4.1 逻辑漏洞

"""
        if logic_vuln_findings:
            md += "| 序号 | 发现内容 |\n|------|----------|\n"
            for i, finding in enumerate(logic_vuln_findings[:10], 1):
                md += f"| {i} | {finding} |\n"
        else:
            md += "未发现明显的逻辑漏洞\n"

        md += "\n### 4.2 业务漏洞\n\n"
        if business_vuln_findings:
            md += "| 序号 | 发现内容 |\n|------|----------|\n"
            for i, finding in enumerate(business_vuln_findings[:10], 1):
                md += f"| {i} | {finding} |\n"
        else:
            md += "未发现明显的业务漏洞\n"

        md += "\n### 4.3 权限与授权漏洞\n\n"
        if permission_vuln_findings:
            md += "| 序号 | 发现内容 |\n|------|----------|\n"
            for i, finding in enumerate(permission_vuln_findings[:10], 1):
                md += f"| {i} | {finding} |\n"
        else:
            md += "未发现明显的权限漏洞\n"

        md += "\n### 4.4 竞态条件与并发安全\n\n"
        if race_condition_findings:
            md += "| 序号 | 发现内容 |\n|------|----------|\n"
            for i, finding in enumerate(race_condition_findings[:10], 1):
                md += f"| {i} | {finding} |\n"
        else:
            md += "未发现明显的竞态条件问题\n"

        md += f"""

## 5. 多角色共识

"""
        for consensus in aligned.get("consensus", []):
            md += f"- {consensus}\n"

        md += f"""

## 6. 改进建议

### 6.1 高优先级（安全/漏洞相关）

"""
        if high_priority:
            for rec in high_priority[:10]:
                md += f"- {rec}\n"
        else:
            md += "无高优先级改进项\n"

        md += f"""

### 6.2 中优先级

"""
        for rec in medium_priority[:10]:
            md += f"- {rec}\n"

        md += f"""

---

## 审查结论

本代码走读审查报告综合了 {len(self.role_results)} 个角色的分析视角，提供以下核心结论：

1. **架构评估**: 项目{'采用分层架构，模块划分基本合理' if len(project_info['modules']) > 3 else '采用模块化架构'}
2. **代码质量**: 代码规模{'较大，建议关注重点模块' if project_info['source_file_count'] > 50 else '规模适中'}
3. **逻辑漏洞**: {'发现 ' + str(len(logic_vuln_findings)) + ' 个逻辑漏洞，建议优先修复' if logic_vuln_findings else '未发现明显逻辑漏洞'}
4. **业务漏洞**: {'发现 ' + str(len(business_vuln_findings)) + ' 个业务漏洞，建议优先修复' if business_vuln_findings else '未发现明显业务漏洞'}
5. **权限漏洞**: {'发现 ' + str(len(permission_vuln_findings)) + ' 个权限漏洞，建议优先修复' if permission_vuln_findings else '未发现明显权限漏洞'}
6. **改进建议**: 详见第六章，建议按优先级逐步实施

---

*本报告由多角色协作分析生成*
*审查角色: {', '.join(roles)}*
*生成时间: {timestamp}*
"""

        Path(output_path).write_text(md, encoding='utf-8')


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="真正的多角色协作代码走读分析器 v2.0",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument("project_root", help="项目根目录路径")
    parser.add_argument("--workspace", "-w", default=None, help="工作空间根目录路径")
    parser.add_argument("--skill-root", "-s", default=None, help="Skill 根目录路径")
    parser.add_argument("--output", "-o", default=None, help="输出目录路径")

    args = parser.parse_args()

    if not os.path.exists(args.project_root):
        print(f"错误: 路径不存在: {args.project_root}")
        return 1

    workspace_root = args.workspace if args.workspace else os.path.dirname(os.path.abspath(args.project_root))
    skill_root = args.skill_root if args.skill_root else str(Path(__file__).parent.parent)

    analyzer = MultiRoleCollaborativeAnalyzer(
        args.project_root,
        workspace_root,
        skill_root
    )

    analyzer.run(args.output)

    return 0


if __name__ == "__main__":
    exit(main())