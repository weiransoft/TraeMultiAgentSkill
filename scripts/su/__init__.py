"""SU（System Understanding，既有系统反向理解）能力包。

包定位（ARCH-SU-001 §2.1）：`scripts/system_understanding.py` CLI 编排层之下的
确定性脚本层——纯标准库 + 软依赖（playwright/pymysql/psycopg2/redis 一律
运行时 try-import，绝不在包模块顶层硬 import，REQ-SU-021）。

六阶段流水线分层：
  基础层（本包当前交付）：dto / config / limiter / deps / state_store
  采集层（后续交付）：preflight / browser_login / site_crawler / api_observer
                     / db_inspector / redis_inspector
  分析渲染层（后续交付）：relation_analyzer / document_renderer

本 __init__ 仅做包声明与公共 DTO 再导出——**不 import config/state_store 等
子模块**：子模块间存在 config→dto、limiter→config、state_store→dto 的单向
依赖链，包级再导出保持最浅（仅 dto）以避免任何导入期副作用与循环风险。
"""

__version__ = "1.0.0"

# 公共 DTO 再导出（下游模块 `from su import SuError, SensitiveStr` 即得，
# 全部来自无依赖的 dto 叶子模块，导入零副作用）
from su.dto import (  # noqa: E402  （包声明之后即重导出，保证版本常量先定义）
    REDACTED_PLACEHOLDER,
    ActionDecisionRedacted,
    ApiObservationRedacted,
    BlockedEventRedacted,
    BudgetSummaryRedacted,
    DbTableRedacted,
    EdgeRedacted,
    ImplicitFkCandidateRedacted,
    PageNodeRedacted,
    RedactedDict,
    RedisKeyRecordRedacted,
    RelationRedacted,
    SensitiveStr,
    SuConfigError,
    SuDepsError,
    SuError,
    SuLockHeldError,
    SuLoginError,
    SuReadonlyViolation,
    SuRedisViolation,
)

__all__ = [
    "__version__",
    "REDACTED_PLACEHOLDER",
    "SensitiveStr",
    "RedactedDict",
    "SuError",
    "SuConfigError",
    "SuLoginError",
    "SuDepsError",
    "SuReadonlyViolation",
    "SuRedisViolation",
    "SuLockHeldError",
    "PageNodeRedacted",
    "EdgeRedacted",
    "ActionDecisionRedacted",
    "BlockedEventRedacted",
    "ApiObservationRedacted",
    "DbTableRedacted",
    "ImplicitFkCandidateRedacted",
    "RedisKeyRecordRedacted",
    "RelationRedacted",
    "BudgetSummaryRedacted",
]
