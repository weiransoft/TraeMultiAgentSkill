"""Ponytail 决策梯模块。

借鉴 ponytail (https://github.com/DietrichGebert/ponytail) 的 6 步决策梯思想，
在 TraeMultiAgentSkill 中实现"让 AI 像最懒的资深工程师一样写代码"。

子模块：
- ruleset: 决策梯规则集引擎（Python 常量，按模式/角色返回规则片段）
- mode_tracker: 模式跟踪（lite/full/ultra/off）
- debt_collector: ponytail: 注释债务台账收割
- requirement_tracer: 需求文档功能点追溯（红线检测）
"""

from ponytail.ruleset import PonytailRulesetEngine, PonytailMode, ROLE_INTENSITY
from ponytail.mode_tracker import ModeTracker
from ponytail.debt_collector import DebtCollector, DebtEntry
from ponytail.requirement_tracer import RequirementTracer, Requirement, TraceReport

__all__ = [
    "PonytailRulesetEngine",
    "PonytailMode",
    "ROLE_INTENSITY",
    "ModeTracker",
    "DebtCollector",
    "DebtEntry",
    "RequirementTracer",
    "Requirement",
    "TraceReport",
]

