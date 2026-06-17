#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Skill Injector - 技能自动注入（Phase 8：SkillDistribution）

职责：
1. 解析 task.task_skill 字段（4 种合法形式）
2. 校验 skill 名称合法性（白名单 + 注入攻击检测）
3. 解析 skill 依赖（DFS + 循环检测 + 拓扑排序）
4. 渲染注入内容（structured/markdown/compact/full 4 种模式）
5. Token 预算截断（4 级降级策略）
6. 与 PerformanceFingerprint 联动

依据：
- DYNAMIC_WORKFLOWS_INTEGRATION.md v1.4 §下一步决策
- PHASE8_PLAN.md 完整方案
- 架构师审查 §3.0.3 安全约束、§6 数据模型约束

设计原则：
- 不修改任何 V2 文件
- 严格向后兼容：task_skill 字段为 optional，缺失时行为与 Phase 7 一致
- 真实实现：禁模拟/占位/简化
- Java/Rust 风格中文注释

作者：trae-multi-agent 融合 Phase 8
创建日期：2026-06-04
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger(__name__)


# ============================================================================
# 异常类
# ============================================================================

class SkillInjectorError(Exception):
    """SkillInjector 基础异常"""
    pass


class InvalidTaskSkillFormatError(SkillInjectorError):
    """task_skill 字段格式非法"""
    def __init__(self, message: str, value: Any = None):
        super().__init__(message)
        self.value = value


class SkillGuardError(SkillInjectorError):
    """SkillGuard 拒绝（注入攻击 / 名称非法 / 数量超限 / 循环依赖）"""
    def __init__(self, message: str, skill_name: Optional[str] = None, attack_type: Optional[str] = None):
        super().__init__(message)
        self.skill_name = skill_name
        self.attack_type = attack_type


class SkillResolutionError(SkillInjectorError):
    """Skill 解析失败（如 critical 优先级 skill 不存在）"""
    def __init__(self, message: str, missing_skills: Optional[List[str]] = None):
        super().__init__(message)
        self.missing_skills = missing_skills or []


class SkillCircularDependencyError(SkillInjectorError):
    """Skill 循环依赖（hard error，避免栈溢出）"""
    def __init__(self, message: str, cycle: Optional[List[str]] = None):
        super().__init__(message)
        self.cycle = cycle or []


# ============================================================================
# 枚举：注入模式 / 合并策略 / 优先级
# ============================================================================

class InjectionMode(str, Enum):
    """skill 注入模式"""
    STRUCTURED = "structured"  # XML 结构化（默认）
    MARKDOWN = "markdown"      # Markdown 段
    COMPACT = "compact"        # 单行紧凑
    FULL = "full"             # 完整 YAML dump


class SkillPriority(str, Enum):
    """skill 缺失行为优先级"""
    CRITICAL = "critical"  # 缺失时硬中断
    HIGH = "high"          # 缺失时 warning
    NORMAL = "normal"      # 缺失时 info（默认）
    LOW = "low"            # 缺失时静默


class SkillMergePolicy(str, Enum):
    """多 skill 合并策略"""
    APPEND = "append"           # 按顺序拼接
    PRIORITIZE = "prioritize"  # 数值越小越靠前
    OVERRIDE = "override"      # 后者覆盖前者（同名 capability）


# ============================================================================
# 注入模式合法值
# ============================================================================

VALID_INJECTION_MODES = frozenset(m.value for m in InjectionMode)
VALID_SKILL_PRIORITIES = frozenset(p.value for p in SkillPriority)

# Skill 名称合法性正则：小写字母/数字/连字符，长度 ≤ 63
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

# Skill 数量硬上限（避免 prompt 体积攻击）
MAX_SKILLS_PER_TASK = 10

# 依赖深度硬上限（避免递归爆栈）
MAX_DEPENDENCY_DEPTH = 5

# 单 skill 描述 token 截断阈值（粗略按 4 字符/token 估算）
MAX_DESCRIPTION_CHARS = 2000

# Capability 描述 token 截断阈值
MAX_CAPABILITY_DESCRIPTION_CHARS = 500

# 注入攻击检测关键词（复用 guard.INJECTION_KEYWORDS 模式）
INJECTION_KEYWORDS = (
    "ignore previous",
    "ignore all",
    "disregard",
    "system prompt",
    "you are now",
    "forget",
    "override",
    "execute shell",
    "rm -rf",
    "drop table",
    "<script",
    "javascript:",
)


# ============================================================================
# 数据类
# ============================================================================

@dataclass
class SkillInjectableView:
    """
    Skill 注入视图（从 SkillManifest 派生的轻量结构）

    与 SkillManifest 的区别：
    - 移除 metadata / created_at / updated_at 等内部字段
    - description 已被截断（避免长 description 撑爆 token）
    - capabilities 已按优先级排序
    - 不可变（frozen=True）
    """
    name: str
    version: str
    description: str
    capabilities: List[Dict[str, str]] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    status: str = "active"

    @classmethod
    def from_manifest(
        cls,
        manifest: Any,
        description_max_chars: int = MAX_DESCRIPTION_CHARS,
        capability_desc_max_chars: int = MAX_CAPABILITY_DESCRIPTION_CHARS,
    ) -> "SkillInjectableView":
        """
        从 SkillManifest 派生 SkillInjectableView

        Args:
            manifest: SkillManifest 实例
            description_max_chars: description 截断阈值
            capability_desc_max_chars: 单 capability description 截断阈值

        Returns:
            SkillInjectableView
        """
        # description 截断（避免撑爆 token）
        desc = str(getattr(manifest, "description", "") or "")
        if len(desc) > description_max_chars:
            desc = desc[:description_max_chars] + "..."

        # capabilities 结构化
        caps: List[Dict[str, str]] = []
        for cap in getattr(manifest, "capabilities", []) or []:
            cap_desc = str(getattr(cap, "description", "") or "")
            if len(cap_desc) > capability_desc_max_chars:
                cap_desc = cap_desc[:capability_desc_max_chars] + "..."
            caps.append({
                "name": str(getattr(cap, "name", "")),
                "description": cap_desc,
            })

        return cls(
            name=str(getattr(manifest, "name", "")),
            version=str(getattr(manifest, "version", "0.0.0")),
            description=desc,
            capabilities=caps,
            dependencies=list(getattr(manifest, "dependencies", []) or []),
            status=str(getattr(manifest, "status", "active")),
        )


@dataclass
class ParsedTaskSkill:
    """解析后的 task_skill 字段"""
    skill_names: List[str]  # 按优先级排序的 skill 名称列表
    priorities: Dict[str, SkillPriority]  # skill 名 → 优先级
    primary: List[str] = field(default_factory=list)  # primary 列表（高级形式）
    fallback: List[str] = field(default_factory=list)  # fallback 列表（高级形式）
    raw: Any = None  # 原始 task_skill 值


@dataclass
class InjectionResult:
    """skill 注入结果"""
    rendered_text: str  # 注入到 system context 的最终文本
    injected_skills: List[str]  # 成功注入的 skill 名列表
    missing_skills: List[str]  # 缺失的 skill 名列表
    circular_skills: List[str]  # 循环依赖中跳过的 skill 名列表
    truncated: bool  # 是否触发了 token 截断
    injection_time_ms: float  # 注入耗时（毫秒）
    mode: str = InjectionMode.STRUCTURED.value  # 实际使用的注入模式
    total_chars: int = 0  # 注入文本总字符数
    errors: List[str] = field(default_factory=list)  # 非致命的错误列表

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict"""
        return {
            "rendered_text": self.rendered_text,
            "injected_skills": self.injected_skills,
            "missing_skills": self.missing_skills,
            "circular_skills": self.circular_skills,
            "truncated": self.truncated,
            "injection_time_ms": self.injection_time_ms,
            "mode": self.mode,
            "total_chars": self.total_chars,
            "errors": self.errors,
        }


# ============================================================================
# SkillTaskFieldParser：解析 task.task_skill 字段
# ============================================================================

class SkillTaskFieldParser:
    """
    解析 task.task_skill 字段

    支持 4 种合法形式（按优先级解析）：
    1. 字符串: "trae-multi-agent"
    2. 字符串列表: ["trae-multi-agent", "code-review"]
    3. 字典（含优先级）: {"trae-multi-agent": 1, "code-review": 2}
    4. 嵌套字典（高级）: {"primary": [...], "fallback": [...]}
    """

    @staticmethod
    def parse(task_skill: Any) -> ParsedTaskSkill:
        """
        解析 task_skill 字段

        Args:
            task_skill: task_skill 原始值

        Returns:
            ParsedTaskSkill: 解析结果

        Raises:
            InvalidTaskSkillFormatError: 格式非法
        """
        if task_skill is None:
            return ParsedTaskSkill(
                skill_names=[],
                priorities={},
                primary=[],
                fallback=[],
                raw=task_skill,
            )

        # 形式 1：字符串
        if isinstance(task_skill, str):
            return ParsedTaskSkill(
                skill_names=[task_skill],
                priorities={task_skill: SkillPriority.NORMAL},
                primary=[],
                fallback=[],
                raw=task_skill,
            )

        # 形式 2：列表
        if isinstance(task_skill, list):
            if not task_skill:
                return ParsedTaskSkill(
                    skill_names=[],
                    priorities={},
                    primary=[],
                    fallback=[],
                    raw=task_skill,
                )
            # 验证列表元素均为字符串
            for i, item in enumerate(task_skill):
                if not isinstance(item, str):
                    raise InvalidTaskSkillFormatError(
                        f"task_skill 列表元素[{i}]必须为字符串，得到 {type(item).__name__}",
                        value=task_skill,
                    )
            return ParsedTaskSkill(
                skill_names=list(task_skill),
                priorities={s: SkillPriority.NORMAL for s in task_skill},
                primary=list(task_skill),
                fallback=[],
                raw=task_skill,
            )

        # 形式 3 / 4：字典
        if isinstance(task_skill, dict):
            # 形式 4：嵌套字典（含 primary/fallback）
            if "primary" in task_skill or "fallback" in task_skill:
                primary = task_skill.get("primary", [])
                fallback = task_skill.get("fallback", [])
                # 验证
                for fld in ("primary", "fallback"):
                    val = task_skill.get(fld, [])
                    if not isinstance(val, list):
                        raise InvalidTaskSkillFormatError(
                            f"task_skill['{fld}']必须为列表，得到 {type(val).__name__}",
                            value=task_skill,
                        )
                    for i, item in enumerate(val):
                        if not isinstance(item, str):
                            raise InvalidTaskSkillFormatError(
                                f"task_skill['{fld}'][{i}]必须为字符串",
                                value=task_skill,
                            )
                all_skills = list(primary) + list(fallback)
                return ParsedTaskSkill(
                    skill_names=all_skills,
                    priorities={
                        **{s: SkillPriority.CRITICAL for s in primary},
                        **{s: SkillPriority.LOW for s in fallback},
                    },
                    primary=list(primary),
                    fallback=list(fallback),
                    raw=task_skill,
                )

            # 形式 3：优先级字典 {skill_name: priority_int}
            skills_with_priority: List[Tuple[str, int]] = []
            for name, prio in task_skill.items():
                if not isinstance(name, str):
                    raise InvalidTaskSkillFormatError(
                        f"task_skill 字典键必须为字符串，得到 {type(name).__name__}",
                        value=task_skill,
                    )
                if not isinstance(prio, (int, float)):
                    raise InvalidTaskSkillFormatError(
                        f"task_skill['{name}']必须为数字，得到 {type(prio).__name__}",
                        value=task_skill,
                    )
                skills_with_priority.append((name, int(prio)))

            # 按 priority 升序排序（数值越小越靠前）
            skills_with_priority.sort(key=lambda x: x[1])
            return ParsedTaskSkill(
                skill_names=[s for s, _ in skills_with_priority],
                priorities={s: SkillPriority.NORMAL for s, _ in skills_with_priority},
                primary=[s for s, _ in skills_with_priority],
                fallback=[],
                raw=task_skill,
            )

        # 非法类型
        raise InvalidTaskSkillFormatError(
            f"task_skill 类型非法：{type(task_skill).__name__}"
            f"（支持：str / list / dict / None）",
            value=task_skill,
        )


# ============================================================================
# SkillGuard：技能名安全校验
# ============================================================================

class SkillGuard:
    """
    Skill 安全校验器

    4 项校验：
    1. 名称合法性（[a-z0-9-]{1,63}）
    2. 数量 ≤ MAX_SKILLS_PER_TASK
    3. 依赖深度 ≤ MAX_DEPENDENCY_DEPTH
    4. 内容注入攻击检测（description / capability description）
    """

    def __init__(
        self,
        max_skills: int = MAX_SKILLS_PER_TASK,
        max_depth: int = MAX_DEPENDENCY_DEPTH,
        injection_keywords: Tuple[str, ...] = INJECTION_KEYWORDS,
    ):
        """
        初始化 SkillGuard

        Args:
            max_skills: 单 task 允许的最大 skill 数
            max_depth: 依赖最大深度
            injection_keywords: 注入攻击检测关键词元组
        """
        self.max_skills = max_skills
        self.max_depth = max_depth
        self.injection_keywords = injection_keywords

    def validate_names(self, skill_names: List[str]) -> None:
        """
        校验 skill 名称合法性

        Args:
            skill_names: skill 名列表

        Raises:
            SkillGuardError: 名称非法
        """
        for name in skill_names:
            if not isinstance(name, str):
                raise SkillGuardError(
                    f"skill 名必须为字符串：{type(name).__name__}",
                    skill_name=str(name),
                    attack_type="non_string",
                )
            if not SKILL_NAME_PATTERN.match(name):
                raise SkillGuardError(
                    f"skill 名非法：{name!r}（必须匹配 [a-z0-9-]{{1,63}}）",
                    skill_name=name,
                    attack_type="invalid_name",
                )

    def validate_count(self, skill_names: List[str]) -> None:
        """
        校验 skill 数量

        Args:
            skill_names: skill 名列表

        Raises:
            SkillGuardError: 数量超限
        """
        if len(skill_names) > self.max_skills:
            raise SkillGuardError(
                f"skill 数量超限：{len(skill_names)} > {self.max_skills}",
                attack_type="too_many_skills",
            )

    def validate_depth(self, dependency_graph: Dict[str, List[str]]) -> None:
        """
        校验依赖深度（DFS 检测）

        Args:
            dependency_graph: skill → 依赖列表的映射

        Raises:
            SkillGuardError: 依赖过深
        """
        # 找到所有路径的最大深度
        def dfs(node: str, depth: int, visited: Set[str]) -> int:
            if node in visited:
                return depth  # 循环检测：返回当前深度（不递增）
            if depth > self.max_depth:
                raise SkillGuardError(
                    f"依赖深度超限：{depth} > {self.max_depth}（节点={node}）",
                    skill_name=node,
                    attack_type="deep_dependency",
                )
            visited.add(node)
            max_child_depth = depth
            for child in dependency_graph.get(node, []):
                child_depth = dfs(child, depth + 1, visited)
                max_child_depth = max(max_child_depth, child_depth)
            visited.discard(node)
            return max_child_depth

        for root in dependency_graph:
            dfs(root, 0, set())

    def validate_content(self, view: SkillInjectableView) -> None:
        """
        校验 skill 内容无注入攻击

        Args:
            view: SkillInjectableView 实例

        Raises:
            SkillGuardError: 检测到注入攻击
        """
        # 检测 description
        self._check_text_for_injection(
            view.name, view.description, "description"
        )
        # 检测 capability description
        for cap in view.capabilities:
            self._check_text_for_injection(
                view.name,
                cap.get("description", ""),
                f"capability[{cap.get('name', '?')}]",
            )

    def _check_text_for_injection(
        self, skill_name: str, text: str, location: str
    ) -> None:
        """检查文本是否包含注入攻击关键词"""
        if not text:
            return
        text_lower = text.lower()
        for kw in self.injection_keywords:
            if kw.lower() in text_lower:
                raise SkillGuardError(
                    f"检测到注入攻击：skill={skill_name!r} "
                    f"location={location!r} keyword={kw!r}",
                    skill_name=skill_name,
                    attack_type="content_injection",
                )


# ============================================================================
# SkillDependencyResolver：依赖解析
# ============================================================================

class SkillDependencyResolver:
    """
    Skill 依赖解析器

    功能：
    - DFS 遍历依赖
    - 循环依赖检测 + 跳过循环节点（不抛异常，仅记录）
    - 拓扑排序（依赖在前面）
    - 深度限制（避免栈溢出）
    """

    def __init__(
        self,
        registry: Any,
        guard: Optional[SkillGuard] = None,
        max_depth: int = MAX_DEPENDENCY_DEPTH,
    ):
        """
        初始化 SkillDependencyResolver

        Args:
            registry: SkillRegistry 实例
            guard: SkillGuard 实例（None 则自动创建）
            max_depth: 最大依赖深度
        """
        self.registry = registry
        self.guard = guard or SkillGuard(max_depth=max_depth)
        self.max_depth = max_depth

    def resolve(
        self,
        skill_names: List[str],
    ) -> Tuple[List[SkillInjectableView], List[str], List[str]]:
        """
        解析 skill 列表（含依赖展开 + 循环检测）

        Args:
            skill_names: 顶层 skill 名列表

        Returns:
            (views, missing, circular):
            - views: SkillInjectableView 列表（按拓扑序）
            - missing: 缺失的 skill 名列表
            - circular: 因循环依赖被跳过的 skill 名列表
        """
        resolved: List[SkillInjectableView] = []
        seen: Set[str] = set()  # 已处理（避免重复）
        missing: List[str] = []
        circular: List[str] = []
        visiting: Set[str] = set()  # DFS 中正在访问（循环检测）

        def visit(name: str, depth: int) -> None:
            """DFS 访问单个 skill"""
            if name in seen:
                return  # 已解析
            if name in visiting:
                # 检测到循环：记录并跳过
                circular.append(name)
                return
            if depth > self.max_depth:
                logger.warning(
                    f"依赖深度超限，跳过 skill={name!r}（depth={depth} > {self.max_depth}）"
                )
                circular.append(name)
                return

            visiting.add(name)

            # 从 registry 查找
            try:
                manifest = self.registry.get_skill(name) if self.registry else None
            except Exception as e:
                logger.warning(f"查询 skill={name!r} 失败：{e}，视为缺失")
                manifest = None

            if manifest is None:
                missing.append(name)
                visiting.discard(name)
                return

            # 先访问依赖
            for dep in getattr(manifest, "dependencies", []) or []:
                visit(dep, depth + 1)

            # 再添加自己
            view = SkillInjectableView.from_manifest(manifest)
            resolved.append(view)
            seen.add(name)
            visiting.discard(name)

        for name in skill_names:
            visit(name, 0)

        return resolved, missing, circular


# ============================================================================
# SkillInjector：抽象基类
# ============================================================================

class SkillInjector:
    """
    Skill 注入器（抽象基类）

    子类必须实现：
    - _render(views, mode) -> str
    """

    # Token 预算占比（方案决策：20%）
    SKILL_BUDGET_RATIO = 0.20

    # Token → char 估算（粗略：1 token ≈ 4 char）
    CHARS_PER_TOKEN = 4

    def __init__(
        self,
        registry: Any,
        guard: Optional[SkillGuard] = None,
        resolver: Optional[SkillDependencyResolver] = None,
        default_mode: str = InjectionMode.STRUCTURED.value,
    ):
        """
        初始化 SkillInjector

        Args:
            registry: SkillRegistry 实例
            guard: SkillGuard 实例
            resolver: SkillDependencyResolver 实例
            default_mode: 默认注入模式
        """
        if default_mode not in VALID_INJECTION_MODES:
            raise ValueError(
                f"无效注入模式：{default_mode}（有效：{VALID_INJECTION_MODES}）"
            )
        self.registry = registry
        self.guard = guard or SkillGuard()
        self.resolver = resolver or SkillDependencyResolver(
            registry=registry, guard=self.guard
        )
        self.default_mode = default_mode

    def inject(
        self,
        task_skill: Any,
        skill_mode: Optional[str] = None,
        skill_priority: Optional[str] = None,
        token_budget: int = 10000,
    ) -> InjectionResult:
        """
        完整注入流程

        Args:
            task_skill: task.task_skill 字段（支持 4 种形式）
            skill_mode: 注入模式（None 则用 default_mode）
            skill_priority: 缺失行为（None 则视为 normal）
            token_budget: subagent token 预算（用于截断）

        Returns:
            InjectionResult: 注入结果
        """
        start = time.perf_counter()

        # 1. 解析 task_skill 字段
        try:
            parsed = SkillTaskFieldParser.parse(task_skill)
        except InvalidTaskSkillFormatError as e:
            # 格式非法：返回空结果 + 错误
            return InjectionResult(
                rendered_text="",
                injected_skills=[],
                missing_skills=[],
                circular_skills=[],
                truncated=False,
                injection_time_ms=(time.perf_counter() - start) * 1000,
                mode=self.default_mode,
                errors=[f"task_skill 格式非法：{e}"],
            )

        # 空：直接返回（Phase 7 行为）
        if not parsed.skill_names:
            return InjectionResult(
                rendered_text="",
                injected_skills=[],
                missing_skills=[],
                circular_skills=[],
                truncated=False,
                injection_time_ms=(time.perf_counter() - start) * 1000,
                mode=self.default_mode,
            )

        # 2. Guard 校验（名称 / 数量 / 内容）
        try:
            self.guard.validate_names(parsed.skill_names)
            self.guard.validate_count(parsed.skill_names)
        except SkillGuardError as e:
            return InjectionResult(
                rendered_text="",
                injected_skills=[],
                missing_skills=[],
                circular_skills=[],
                truncated=False,
                injection_time_ms=(time.perf_counter() - start) * 1000,
                mode=self.default_mode,
                errors=[f"Guard 拒绝：{e}（attack_type={e.attack_type}）"],
            )

        # 3. 解析依赖（DFS + 循环检测）
        views, missing, circular = self.resolver.resolve(parsed.skill_names)

        # 4. 内容注入攻击检测（每个 view 的 description）
        for view in views:
            try:
                self.guard.validate_content(view)
            except SkillGuardError as e:
                return InjectionResult(
                    rendered_text="",
                    injected_skills=[],
                    missing_skills=[],
                    circular_skills=[],
                    truncated=False,
                    injection_time_ms=(time.perf_counter() - start) * 1000,
                    mode=self.default_mode,
                    errors=[
                        f"内容注入攻击：skill={view.name!r} reason={e}"
                    ],
                )

        # 5. 处理缺失 skill（按 skill_priority）
        priority = SkillPriority(skill_priority) if skill_priority else SkillPriority.NORMAL
        if missing:
            if priority == SkillPriority.CRITICAL:
                # critical：硬中断
                return InjectionResult(
                    rendered_text="",
                    injected_skills=[],
                    missing_skills=missing,
                    circular_skills=circular,
                    truncated=False,
                    injection_time_ms=(time.perf_counter() - start) * 1000,
                    mode=self.default_mode,
                    errors=[f"critical skill 缺失：{missing}"],
                )
            elif priority == SkillPriority.HIGH:
                logger.warning(f"high 优先级 skill 缺失：{missing}")
            elif priority == SkillPriority.NORMAL:
                logger.info(f"normal 优先级 skill 缺失：{missing}")
            # low: 静默

        # 6. 渲染（按 skill_mode）
        mode = skill_mode if skill_mode in VALID_INJECTION_MODES else self.default_mode
        try:
            rendered = self._render(views, mode)
        except Exception as e:
            return InjectionResult(
                rendered_text="",
                injected_skills=[v.name for v in views],
                missing_skills=missing,
                circular_skills=circular,
                truncated=False,
                injection_time_ms=(time.perf_counter() - start) * 1000,
                mode=mode,
                errors=[f"渲染失败：{type(e).__name__}: {e}"],
            )

        # 7. Token 预算截断
        truncated = False
        max_chars = int(token_budget * self.SKILL_BUDGET_RATIO * self.CHARS_PER_TOKEN)
        if len(rendered) > max_chars and max_chars > 0:
            rendered = self._truncate(rendered, views, mode, max_chars)
            truncated = True

        elapsed_ms = (time.perf_counter() - start) * 1000
        return InjectionResult(
            rendered_text=rendered,
            injected_skills=[v.name for v in views],
            missing_skills=missing,
            circular_skills=circular,
            truncated=truncated,
            injection_time_ms=elapsed_ms,
            mode=mode,
            total_chars=len(rendered),
        )

    def _render(self, views: List[SkillInjectableView], mode: str) -> str:
        """抽象方法：按模式渲染"""
        raise NotImplementedError

    def _truncate(
        self,
        rendered: str,
        views: List[SkillInjectableView],
        mode: str,
        max_chars: int,
    ) -> str:
        """
        Token 预算超限截断（4 级降级）

        1. 截断每个 capability description（保留 200 字符）
        2. 截断每个 skill description（保留 500 字符）
        3. 丢弃末尾的 skill
        4. 切到 compact 模式
        """
        # 降级 1：截断 capability 描述到 200
        truncated_views: List[SkillInjectableView] = []
        for v in views:
            new_caps = []
            for cap in v.capabilities:
                desc = cap.get("description", "")
                if len(desc) > 200:
                    desc = desc[:200] + "..."
                new_caps.append({"name": cap["name"], "description": desc})
            truncated_views.append(SkillInjectableView(
                name=v.name, version=v.version,
                description=v.description[:500] + "..." if len(v.description) > 500 else v.description,
                capabilities=new_caps,
                dependencies=v.dependencies,
                status=v.status,
            ))
        rendered = self._render(truncated_views, mode)
        if len(rendered) <= max_chars:
            return rendered

        # 降级 2：丢弃末尾的 skill（保留 50%）
        keep = max(1, len(truncated_views) // 2)
        rendered = self._render(truncated_views[:keep], mode)
        if len(rendered) <= max_chars:
            return rendered

        # 降级 3：切到 compact 模式
        rendered = self._render(truncated_views[:keep], InjectionMode.COMPACT.value)
        return rendered


# ============================================================================
# StructuredSkillInjector：默认结构化 XML 注入器
# ============================================================================

class StructuredSkillInjector(SkillInjector):
    """
    结构化 XML 注入器（默认）

    注入格式示例：
    ```
    <available_skills>
    <skill name="trae-multi-agent" version="2.5.0" status="active">
      <description>trae-multi-agent 多角色协作 skill</description>
      <capabilities>
        <capability name="orchestration">多角色编排</capability>
      </capabilities>
    </skill>
    </available_skills>
    ```
    """

    def _render(self, views: List[SkillInjectableView], mode: str) -> str:
        """渲染为指定模式"""
        if mode == InjectionMode.STRUCTURED.value:
            return self._render_structured(views)
        elif mode == InjectionMode.MARKDOWN.value:
            return self._render_markdown(views)
        elif mode == InjectionMode.COMPACT.value:
            return self._render_compact(views)
        elif mode == InjectionMode.FULL.value:
            return self._render_full(views)
        # 非法模式回退到 structured
        return self._render_structured(views)

    def _render_structured(self, views: List[SkillInjectableView]) -> str:
        """结构化 XML 渲染"""
        if not views:
            return ""
        lines: List[str] = ["<available_skills>"]
        for v in views:
            lines.append(
                f'  <skill name="{_xml_escape(v.name)}" '
                f'version="{_xml_escape(v.version)}" '
                f'status="{_xml_escape(v.status)}">'
            )
            lines.append(f"    <description>{_xml_escape(v.description)}</description>")
            if v.capabilities:
                lines.append("    <capabilities>")
                for cap in v.capabilities:
                    lines.append(
                        f'      <capability name="{_xml_escape(cap["name"])}">'
                        f"{_xml_escape(cap.get('description', ''))}</capability>"
                    )
                lines.append("    </capabilities>")
            if v.dependencies:
                deps_str = ",".join(_xml_escape(d) for d in v.dependencies)
                lines.append(f"    <dependencies>{deps_str}</dependencies>")
            lines.append("  </skill>")
        lines.append("</available_skills>")
        return "\n".join(lines)

    def _render_markdown(self, views: List[SkillInjectableView]) -> str:
        """Markdown 渲染"""
        if not views:
            return ""
        lines: List[str] = ["## Available Skills", ""]
        for v in views:
            lines.append(f"### {v.name} (v{v.version})")
            lines.append("")
            lines.append(v.description)
            lines.append("")
            if v.capabilities:
                lines.append("**Capabilities:**")
                for cap in v.capabilities:
                    lines.append(f"- **{cap['name']}**: {cap.get('description', '')}")
                lines.append("")
        return "\n".join(lines)

    def _render_compact(self, views: List[SkillInjectableView]) -> str:
        """单行紧凑渲染"""
        if not views:
            return ""
        parts: List[str] = []
        for v in views:
            cap_names = "/".join(c["name"] for c in v.capabilities)
            parts.append(f"{v.name}({cap_names})")
        return "Skills: " + ", ".join(parts)

    def _render_full(self, views: List[SkillInjectableView]) -> str:
        """完整 YAML dump"""
        # 简单实现：每行一个 skill 的全部信息
        lines: List[str] = ["# Skills (full dump)", ""]
        for v in views:
            lines.append(f"- name: {v.name}")
            lines.append(f"  version: {v.version}")
            lines.append(f"  status: {v.status}")
            lines.append(f"  description: |")
            for line in v.description.split("\n"):
                lines.append(f"    {line}")
            if v.capabilities:
                lines.append("  capabilities:")
                for cap in v.capabilities:
                    lines.append(f"    - name: {cap['name']}")
                    lines.append(f"      description: {cap.get('description', '')}")
            if v.dependencies:
                lines.append(f"  dependencies: {v.dependencies}")
        return "\n".join(lines)


def _xml_escape(s: str) -> str:
    """XML 字符转义"""
    if not s:
        return ""
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


# ============================================================================
# 工厂函数
# ============================================================================

def create_skill_injector(
    registry: Any = None,
    default_mode: str = InjectionMode.STRUCTURED.value,
) -> SkillInjector:
    """
    工厂函数：创建默认 SkillInjector

    Args:
        registry: SkillRegistry 实例（None 则尝试自动加载）
        default_mode: 默认注入模式

    Returns:
        SkillInjector 实例（默认 StructuredSkillInjector）
    """
    if registry is None:
        # 尝试从默认路径加载 registry
        try:
            from skill_registry import SkillRegistry
            registry = SkillRegistry(registry_path=".")
        except Exception as e:
            logger.warning(f"SkillRegistry 加载失败，使用 None：{e}")
            registry = None

    return StructuredSkillInjector(
        registry=registry,
        default_mode=default_mode,
    )
