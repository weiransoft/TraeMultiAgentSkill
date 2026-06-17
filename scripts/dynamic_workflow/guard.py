#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pattern Executor 安全防护模块（Guard）

Phase 1 必做（架构师审查 §3.0.3 强约束）：
- 🔴 subagent 输入 schema 校验
- 🔴 提示词注入防护（关键词 + 编码特征检测）
- 🔴 Token 硬上限（执行器主动中断，非软警告）

与 v2.5 GuardCoordinator 的关系：
- 本模块是 pattern executor 专用的**输入防护层**
- v2.5 GuardCoordinator 在 cybernetics_bridge 中也执行类似检查
- 本模块独立可运行，**不依赖** v2.5 cybernetics_bridge
- 真实调用时两层独立校验（防御纵深）

作者：trae-multi-agent 融合 Phase 1
创建日期：2026-06-03
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dynamic_workflow.guard")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ============================================================================
# 枚举定义
# ============================================================================

class GuardDecision(str, Enum):
    """守卫决策"""
    ALLOW = "allow"               # 允许执行
    REJECT = "reject"             # 拒绝执行（硬拒绝）
    SANITIZE = "sanitize"         # 净化后执行（去除危险内容）


class ThreatType(str, Enum):
    """威胁类型"""
    PROMPT_INJECTION = "prompt_injection"           # 提示词注入
    SUSPECTED_INJECTION = "suspected_injection"     # 疑似注入
    SCHEMA_VIOLATION = "schema_violation"           # Schema 违规
    TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded" # Token 超限
    SUSPICIOUS_ENCODING = "suspicious_encoding"     # 可疑编码


# ============================================================================
# 数据类
# ============================================================================

@dataclass
class ThreatFinding:
    """威胁发现记录"""
    threat_type: ThreatType
    severity: int  # 1-10，10 最严重
    evidence: str  # 触发证据
    location: str  # 检出位置（field name）


@dataclass
class GuardResult:
    """守卫检查结果"""
    decision: GuardDecision
    findings: List[ThreatFinding] = field(default_factory=list)
    sanitized_input: Optional[Dict[str, Any]] = None  # 净化后的输入（仅 SANITIZE 决策时有值）
    reason: str = ""

    @property
    def is_allowed(self) -> bool:
        """是否允许执行"""
        return self.decision in (GuardDecision.ALLOW, GuardDecision.SANITIZE)

    @property
    def has_critical_threats(self) -> bool:
        """是否存在严重威胁（severity >= 7）"""
        return any(f.severity >= 7 for f in self.findings)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict（供画像反哺）"""
        return {
            "decision": self.decision.value,
            "findings": [
                {
                    "threat_type": f.threat_type.value,
                    "severity": f.severity,
                    "evidence": f.evidence[:200],  # 截断避免日志爆炸
                    "location": f.location,
                }
                for f in self.findings
            ],
            "reason": self.reason,
        }


# ============================================================================
# 提示词注入检测（关键词 + 编码特征）
# ============================================================================

# 已知注入攻击的关键词（不区分大小写）
INJECTION_KEYWORDS: List[str] = [
    # 经典提示词注入
    r"ignore\s+(previous|above|all)\s+instructions?",
    r"disregard\s+(previous|above|all)\s+instructions?",
    r"forget\s+(previous|above|all)\s+instructions?",
    r"override\s+(previous|above|all)\s+instructions?",
    # 系统提示词冒充
    r"system\s*[::]\s*",
    r"<\|im_start\|>",
    r"<\|im_end\|>",
    r"###\s*system\s*:",
    r"###\s*assistant\s*:",
    # 越权指令
    r"reveal\s+(your|the)\s+(system|initial|original)\s+prompt",
    r"show\s+(your|the)\s+(system|initial|original)\s+prompt",
    r"print\s+(your|the)\s+(system|initial|original)\s+prompt",
    # 角色劫持
    r"you\s+are\s+now\s+",
    r"act\s+as\s+(a\s+)?(developer|admin|root|jailbreak)",
    r"pretend\s+(to\s+be|you\s+are)",
    # 数据外泄
    r"output\s+(the|all)\s+(previous|conversation|context)",
    r"dump\s+(the|all)\s+(data|memory|context)",
    # 中文注入特征
    r"忽略\s*之前\s*的\s*指令",
    r"忽略\s*以上\s*指令",
    r"无视\s*之前\s*的\s*指令",
    r"无视\s*系统\s*提示",
    r"输出\s*(你的|系统的)\s*提示",
    r"忘记\s*之前\s*的\s*对话",
    r"重新\s*扮演",
    r"你现在\s*是",
    r"从现在开始你是",
]

# 编译正则（性能优化）
_COMPILED_PATTERNS: List[re.Pattern] = [
    re.compile(p, re.IGNORECASE | re.MULTILINE)
    for p in INJECTION_KEYWORDS
]

# 可疑编码特征
SUSPICIOUS_ENCODING_PATTERNS: List[re.Pattern] = [
    re.compile(r"\\u[0-9a-f]{4}", re.IGNORECASE),  # Unicode 转义
    re.compile(r"&#\d+;"),  # HTML 实体
    re.compile(r"\\x[0-9a-f]{2}", re.IGNORECASE),  # 十六进制转义
    re.compile(r"""\\[nrtbfv\\"047]"""),  # 字符串转义
    re.compile(r"%[0-9a-f]{2}", re.IGNORECASE),  # URL 编码
]


def _detect_prompt_injection(text: str) -> List[ThreatFinding]:
    """
    提示词注入检测

    Args:
        text: 待检测文本

    Returns:
        List[ThreatFinding]: 威胁发现列表
    """
    findings: List[ThreatFinding] = []

    if not text or not isinstance(text, str):
        return findings

    # 关键词匹配
    for pattern in _COMPILED_PATTERNS:
        match = pattern.search(text)
        if match:
            findings.append(ThreatFinding(
                threat_type=ThreatType.PROMPT_INJECTION,
                severity=9,
                evidence=f"匹配注入关键词: '{match.group(0)[:80]}'",
                location="text",
            ))
            # 找到强注入即返回（避免一个文本被多次报告）
            return findings

    # 编码特征检测
    for pattern in SUSPICIOUS_ENCODING_PATTERNS:
        matches = pattern.findall(text)
        if len(matches) > 5:  # 短文本中转义过多为可疑
            findings.append(ThreatFinding(
                threat_type=ThreatType.SUSPICIOUS_ENCODING,
                severity=6,
                evidence=f"发现 {len(matches)} 个编码转义符（阈值 5）",
                location="text",
            ))

    return findings


# ============================================================================
# 输入 Schema 校验
# ============================================================================

class FieldSchema:
    """
    字段 schema 描述符

    用于 Guard.check_schema() 校验输入字典是否符合预定义 schema。
    支持类型校验、必需字段、枚举值、长度限制。
    """

    def __init__(
        self,
        name: str,
        type_: type,
        required: bool = True,
        enum: Optional[List[Any]] = None,
        max_length: Optional[int] = None,
        min_length: Optional[int] = None,
    ):
        self.name = name
        self.type_ = type_
        self.required = required
        self.enum = enum
        self.max_length = max_length
        self.min_length = min_length

    def validate(self, value: Any) -> Optional[str]:
        """
        校验字段值

        Returns:
            Optional[str]: 错误信息（None 表示通过）
        """
        # 必需字段检查
        if value is None:
            if self.required:
                return f"字段 '{self.name}' 必填但未提供"
            return None

        # 类型检查
        if not isinstance(value, self.type_):
            return (
                f"字段 '{self.name}' 类型错误："
                f"期望 {self.type_.__name__}，实际 {type(value).__name__}"
            )

        # 枚举检查
        if self.enum is not None and value not in self.enum:
            return (
                f"字段 '{self.name}' 值 {value!r} 不在允许的枚举中："
                f"{self.enum}"
            )

        # 字符串 / 列表长度检查（同时支持 str 和 list）
        if self.type_ is str or self.type_ is list:
            length = len(value)
            if self.max_length is not None and length > self.max_length:
                return (
                    f"字段 '{self.name}' 长度 {length} 超出 max_length={self.max_length}"
                )
            if self.min_length is not None and length < self.min_length:
                return (
                    f"字段 '{self.name}' 长度 {length} 低于 min_length={self.min_length}"
                )

        return None


def check_schema(
    data: Dict[str, Any],
    schema: List[FieldSchema],
) -> List[ThreatFinding]:
    """
    按 schema 列表校验数据

    严重度区分：
    - 必填字段缺失：severity=8（critical → REJECT）
    - 长度 / 类型 / 枚举违规：severity=5（warning → SANITIZE）

    Args:
        data: 待校验数据（dict）
        schema: 字段 schema 列表

    Returns:
        List[ThreatFinding]: 校验失败项（空列表表示通过）
    """
    findings: List[ThreatFinding] = []

    for field_schema in schema:
        value = data.get(field_schema.name)
        error = field_schema.validate(value)
        if error is not None:
            # 严重威胁判定：
            # - 必填字段缺失（value is None and required）
            # - 必填字段长度不足（required and min_length 违规）
            is_required_missing = value is None and field_schema.required
            is_required_length_violation = (
                field_schema.required
                and field_schema.min_length is not None
                and value is not None
                and len(value) < field_schema.min_length
            )
            severity = 8 if (is_required_missing or is_required_length_violation) else 5
            findings.append(ThreatFinding(
                threat_type=ThreatType.SCHEMA_VIOLATION,
                severity=severity,
                evidence=error,
                location=field_schema.name,
            ))

    return findings


# ============================================================================
# Token 预算硬上限
# ============================================================================

# 经验估值：1 token ≈ 4 字符（英文）或 1.5 字符（中文）
# 用于在不依赖真实 LLM 的情况下预估文本 token 数
CHARS_PER_TOKEN_EN = 4.0
CHARS_PER_TOKEN_ZH = 1.5


def estimate_tokens(text: str) -> int:
    """
    估算文本的 token 数

    简化算法：
    - 中文字符按 1.5 字符/token
    - 其他字符按 4 字符/token
    - 这是粗估，实际 LLM tokenizer 可能有偏差，但作为预算检查够用

    Args:
        text: 待估算文本

    Returns:
        int: 估算的 token 数
    """
    if not text:
        return 0

    # 统计中文字符数
    chinese_count = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other_count = len(text) - chinese_count

    zh_tokens = chinese_count / CHARS_PER_TOKEN_ZH
    other_tokens = other_count / CHARS_PER_TOKEN_EN

    return int(zh_tokens + other_tokens) + 1  # +1 避免 0


def check_token_budget(
    inputs: Dict[str, Any],
    budget: int,
) -> List[ThreatFinding]:
    """
    检查输入是否超出 token 预算

    Args:
        inputs: 输入字典（所有字符串字段会累加）
        budget: token 预算硬上限

    Returns:
        List[ThreatFinding]: 超限项（空列表表示通过）
    """
    findings: List[ThreatFinding] = []

    total_tokens = 0
    for key, value in inputs.items():
        if isinstance(value, str):
            tokens = estimate_tokens(value)
            total_tokens += tokens

    if total_tokens > budget:
        findings.append(ThreatFinding(
            threat_type=ThreatType.TOKEN_BUDGET_EXCEEDED,
            severity=8,
            evidence=(
                f"估算 token 数 {total_tokens} 超出预算 {budget} "
                f"（超出 {total_tokens - budget} tokens）"
            ),
            location="aggregate",
        ))

    return findings


# ============================================================================
# 主入口：Guard.check()
# ============================================================================

def check(
    inputs: Dict[str, Any],
    schema: Optional[List[FieldSchema]] = None,
    token_budget: Optional[int] = None,
) -> GuardResult:
    """
    Guard 主入口：执行所有安全检查

    Args:
        inputs: 待检查的输入字典
        schema: 可选的字段 schema 校验
        token_budget: 可选的 token 预算硬上限

    Returns:
        GuardResult: 检查结果
    """
    all_findings: List[ThreatFinding] = []

    # 阶段 1：schema 校验
    if schema is not None:
        all_findings.extend(check_schema(inputs, schema))

    # 阶段 2：提示词注入检测（对所有字符串字段）
    for key, value in inputs.items():
        if isinstance(value, str):
            injection_findings = _detect_prompt_injection(value)
            for finding in injection_findings:
                finding.location = key
                all_findings.append(finding)
        elif isinstance(value, list):
            # 列表中每个字符串也检查
            for i, item in enumerate(value):
                if isinstance(item, str):
                    injection_findings = _detect_prompt_injection(item)
                    for finding in injection_findings:
                        finding.location = f"{key}[{i}]"
                        all_findings.append(finding)

    # 阶段 3：token 预算硬上限
    if token_budget is not None:
        all_findings.extend(check_token_budget(inputs, token_budget))

    # 决策
    if not all_findings:
        return GuardResult(decision=GuardDecision.ALLOW, reason="所有检查通过")

    # 严重威胁直接拒绝
    critical_findings = [f for f in all_findings if f.severity >= 8]
    if critical_findings:
        threat_summary = "; ".join(
            f"[{f.threat_type.value}@{f.location}] {f.evidence}"
            for f in critical_findings
        )
        return GuardResult(
            decision=GuardDecision.REJECT,
            findings=all_findings,
            reason=f"检测到严重威胁：{threat_summary}",
        )

    # 非严重威胁 → 净化
    sanitized = dict(inputs)
    # 净化策略：截断可疑字段到 200 字符
    for finding in all_findings:
        if finding.location in sanitized:
            value = sanitized[finding.location]
            if isinstance(value, str):
                sanitized[finding.location] = value[:200] + "...[truncated]"

    return GuardResult(
        decision=GuardDecision.SANITIZE,
        findings=all_findings,
        sanitized_input=sanitized,
        reason=f"检测到 {len(all_findings)} 项威胁，已净化",
    )


# ============================================================================
# 便捷函数
# ============================================================================

def is_safe_text(text: str) -> bool:
    """便捷函数：检查文本是否安全（无注入）"""
    findings = _detect_prompt_injection(text)
    return len(findings) == 0
