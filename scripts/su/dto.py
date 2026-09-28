"""SU（System Understanding）跨模块数据传输对象与类型约束层。

对应架构文档 ARCH-SU-001 §2.3.2：
- 结构化错误基类 SuError 及子类（携带中文 message / code / exit_code / hints）；
- SensitiveStr：明文凭据专用独立 frozen dataclass（不继承 str，杜绝明文经字符串操作扩散）；
- RedactedDict：脱敏完成标记类型（普通 dict 子类，构造唯一合法途径 = config.redact()）；
- 各 *Redacted DTO：state_store 写盘入口的类型约束载体（红线①"红线即类型"四层组合之①）。

约束语义（REQ-SU-002 AC3 四层组合）：
① 写盘函数入参类型声明为 *Redacted；② 入口运行时 isinstance 断言；
③ CI 静态审查；④ 集成 grep 收口。本模块只落实①的"类型定义"。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = [
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

# 统一脱敏占位符：所有键名命中/PII 命中的替换值（§5.2 redact 管线口径）
REDACTED_PLACEHOLDER = "***REDACTED***"


# ---------------------------------------------------------------------------
# 结构化错误族（§2.3.2 / §8.2 退出码状态机）
# ---------------------------------------------------------------------------

class SuError(Exception):
    """SU 结构化错误基类：中文 message + 机器可读 code + 进程退出码 + 修复建议。

    设计意图：CLI 编排层捕获本族异常后可直接按 exit_code 收口退出，
    并把 hints（安装命令/修复建议）原样打印给用户，保证"缺失即声明"（AP-3）。
    """

    def __init__(
        self,
        message: str,
        code: str,
        exit_code: int,
        hints: Optional[List[str]] = None,
    ) -> None:
        """初始化结构化错误。

        Args:
            message: 中文错误描述（面向用户，可直接展示）。
            code: 机器可读错误码（如 'config_missing'/'lock_held'），供日志检索。
            exit_code: 进程退出码（对齐 §8.2：0/2/3/4/5/130）。
            hints: 修复建议列表（安装命令、参数示例等），None 视为空列表。
        """
        super().__init__(message)
        self.message = message
        self.code = code
        self.exit_code = exit_code
        # hints 默认值必须是新列表，避免可变默认参数共享
        self.hints: List[str] = list(hints) if hints is not None else []

    def format(self) -> str:
        """渲染为多行中文报错文本：首行错误描述，后续逐行输出修复建议。

        Returns:
            str: 形如 "错误：<message>\\n建议：<hint1>\\n建议：<hint2>" 的展示文本。
        """
        lines = ["错误：{0}".format(self.message)]
        for hint in self.hints:
            lines.append("建议：{0}".format(hint))
        return "\n".join(lines)


class SuConfigError(SuError):
    """配置错误（REQ-SU-001 AC3 / §8.2 退出码 2）。

    message 必须结构化列出缺失/非法字段名，保证用户无需读源码即可修复。
    """

    def __init__(self, message: str, hints: Optional[List[str]] = None) -> None:
        super().__init__(message=message, code="config_error", exit_code=2, hints=hints)


class SuLoginError(SuError):
    """登录失败 / 会话反复失效（REQ-SU-004 / §8.2 退出码 4）。"""

    def __init__(self, message: str, hints: Optional[List[str]] = None) -> None:
        super().__init__(message=message, code="login_failed", exit_code=4, hints=hints)


class SuDepsError(SuError):
    """致命软依赖缺失（REQ-SU-021 / §8.2 退出码 5，目前仅 playwright）。

    hints 必须包含完整安装命令（pip + playwright install chromium）。
    """

    def __init__(self, message: str, hints: Optional[List[str]] = None) -> None:
        super().__init__(message=message, code="deps_missing", exit_code=5, hints=hints)


class SuReadonlyViolation(SuError):
    """DB 只读边界违例（红线②，REQ-SU-010）。

    由 ReadOnlyGuard.validate/execute 抛出；语句摘要必须先脱敏再进 message。
    退出码复用 2（配置/使用方式错误类），由编排层决定最终收口。
    """

    def __init__(self, message: str, hints: Optional[List[str]] = None) -> None:
        super().__init__(message=message, code="readonly_violation", exit_code=2, hints=hints)


class SuRedisViolation(SuError):
    """Redis 命令白名单违例（红线③，REQ-SU-014）。

    由 RedisGuard.call 抛出：命令名不在 READONLY_COMMANDS frozenset 即拒绝。
    """

    def __init__(self, message: str, hints: Optional[List[str]] = None) -> None:
        super().__init__(message=message, code="redis_violation", exit_code=2, hints=hints)


class SuLockHeldError(SuError):
    """状态库锁被其他进程持有（REQ-SU-019 AC3 / §2.3.11）。

    acquire_lock 在 BEGIN IMMEDIATE 事务内条件更新 run_meta 后 rowcount==0
    （他进程持锁且心跳 ≤60s）时抛出；调用方以非 0 码明确报错退出。
    """

    def __init__(self, message: str, hints: Optional[List[str]] = None) -> None:
        super().__init__(message=message, code="lock_held", exit_code=2, hints=hints)


# ---------------------------------------------------------------------------
# 凭据与脱敏标记类型（§2.3.2 / §5.1 红线①）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SensitiveStr:
    """明文凭据专用**独立**类型（2026-09-28 审查修订：不继承 str）。

    不继承 str 的理由：继承 str 会经 join/format/切片等字符串操作静默扩散明文，
    不可控。真实值只存私有字段 _value；__repr__/__str__ 恒返回 ***REDACTED***；
    唯一取回明文的途径是显式 .reveal()，且只允许出现在两类边界：
      ① 登录填表（browser_login）；② DB/Redis 建连（preflight/inspector）。
    其余位置出现 .reveal() 调用属于静态审查违例（§5.1）。
    """

    _value: str

    def reveal(self) -> str:
        """取回明文。**仅限登录填表 / DB·Redis 建连两类边界调用**（§5.1 静态审查项）。

        Returns:
            str: 构造时持有的明文值。
        """
        return self._value

    def __repr__(self) -> str:
        """repr 恒为脱敏占位，杜绝 repr 意外泄露（如日志 f-string、异常上下文）。"""
        return "SensitiveStr({0})".format(REDACTED_PLACEHOLDER)

    def __str__(self) -> str:
        """str 恒为脱敏占位，杜绝 str()/f-string/format 扩散明文。"""
        return REDACTED_PLACEHOLDER

    def __eq__(self, other: Any) -> bool:
        """按明文值比较（配置合并/测试需要），但比较本身不暴露值到字符串通道。"""
        if isinstance(other, SensitiveStr):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        """按明文值哈希（frozen dataclass 需要哈希能力时保持语义一致）。"""
        return hash(self._value)


class RedactedDict(Dict[str, Any]):
    """脱敏完成**标记类型**（2026-09-28 审查修订：普通 dict 子类，不用 dataclass）。

    dict 与 dataclass 组合语义不清，故采用普通 dict 子类：天然可 JSON 序列化、
    可递归嵌套。约束力来自四层组合（REQ-SU-002 AC3）：
      ① 写盘函数入参类型声明 + ② 入口运行时 isinstance 断言
      + ③ CI 静态审查 + ④ 集成 grep 收口。
    **构造唯一合法途径是 config.redact() 的返回值**——业务代码直接 new
    RedactedDict 未经脱敏管线属于静态审查违例（§5.2）。
    """

    def __repr__(self) -> str:
        """repr 前缀标记，便于日志中肉眼区分已脱敏 dict 与普通 dict。"""
        return "RedactedDict({0})".format(super().__repr__())


# ---------------------------------------------------------------------------
# 写盘 DTO（*Redacted 命名约定 = 落库前必须已经过 redact()/DataMasker 处理）
# 字段按架构 §3 DDL 列设计；标 *Redacted 后缀的字段值为 RedactedDict/脱敏 JSON。
# ---------------------------------------------------------------------------

@dataclass
class PageNodeRedacted:
    """页面节点 DTO（pages 表，REQ-SU-005）。

    url 必须已经过 strip_url_credentials；tech_fingerprint 为已脱敏 JSON 文本。
    """

    url_key: str                      # 规范化去重键（crawler 的 url_key() 产物）
    url: str                          # 已剥离内嵌凭据的原始 URL
    depth: int                        # BFS 深度（单调不减）
    status: str = "pending"           # pending/exploring/done/timeout/error
    title: Optional[str] = None
    discover_from: Optional[int] = None       # BFS 父节点 page_id
    snapshot_path: Optional[str] = None       # snapshots/<page_id>.json
    tech_fingerprint: Optional[str] = None    # 技术栈指纹 JSON（已脱敏）
    error: Optional[str] = None               # 超时/错误说明（含 wait_ready_timeout）


@dataclass
class EdgeRedacted:
    """导航边 DTO（edges 表，REQ-SU-005）。"""

    from_key: str                     # 源页面 url_key
    to_key: str                       # 目标页面 url_key
    via_action: Optional[int] = None  # 经由的 page_actions.action_id


@dataclass
class ActionDecisionRedacted:
    """候选动作分级 DTO（page_actions 表，REQ-SU-006/008）。

    element_sig 必须是 ElementSignature 的**已脱敏 JSON**（永不含 input value）。
    T3 恒 executed=0（红线⑤）。注意 DDL 无 result 列（2026-09-28 审查删除）。
    """

    page_id: int                      # 所在页面
    element_sig: str                  # ElementSignature JSON（已脱敏）
    tier: str                         # 'T1'/'T2'/'T3'
    rule_name: str                    # 命中规则名（NFR-SU-008 可解释）
    executed: int = 0                 # 0=未执行（T3 恒 0），1=已执行（仅 T1/T2）


@dataclass
class BlockedEventRedacted:
    """route 拦截事件 DTO（blocked_events 表，REQ-SU-007）。

    url 只存 path 且 query 值全部键名化（值置 KEY）；post_data 已过 redact()。
    """

    kind: str                         # aborted_method/blocked_origin/download/new_window
    url: str                          # 只存 path；query 值置 KEY
    ts: float                         # 事件时间戳（unix 秒）
    method: Optional[str] = None
    post_data: Optional[str] = None   # 已脱敏 JSON（RedactedDict 序列化）
    page_id: Optional[int] = None     # crawler 以 current_url_key 注入


@dataclass
class ApiObservationRedacted:
    """API 观测 DTO（api_observations 表，REQ-SU-009）。

    shape 只含 body+status+content-type 三要素，**永不落 headers**（§2.3.7）。
    """

    url_path: str                     # 去 query 值、保留键名（/x?ids=KEY）
    method: str
    observed_on_page: int             # 首次观测 page_id
    ts: float
    status: Optional[int] = None
    request_shape: Optional[RedactedDict] = None    # ≤8KB，已过 redact
    response_shape: Optional[RedactedDict] = None   # ≤8KB，已过 redact


@dataclass
class DbTableRedacted:
    """数据库表清单 DTO（db_tables + db_columns 平表，REQ-SU-011）。

    columns 每项为已脱敏 RedactedDict：
    {name, data_type, type_family, is_pk, fk_target, comment, ...}
    """

    schema_name: str
    table_name: str
    kind: str                         # 'BASE TABLE' / 'VIEW'
    columns: List[RedactedDict] = field(default_factory=list)
    row_estimate: Optional[int] = None
    comment: Optional[str] = None


@dataclass
class ImplicitFkCandidateRedacted:
    """隐式外键候选 DTO（implicit_fk_candidates 表，REQ-SU-013）。

    evidence_json 为命中规则记录的**已脱敏 JSON**，一律含"推断，需确认"标注。
    """

    child_table: str
    child_column: str
    parent_table: str
    parent_column: str
    prescreen_score: float            # 三重命中加权分
    stopped_at_rule: int              # 止步规则（1/2/3=达标）
    containment: Optional[float] = None   # 包含度（止步 1/2 时为 None）
    evidence_json: str = "{}"         # 已脱敏 JSON


@dataclass
class RedisKeyRecordRedacted:
    """Redis 键记录 DTO（redis_keys 表，REQ-SU-014）。

    value_sample ≤512B 且必须已过 config.redact()（缓存值常含会话用户信息）。
    """

    key_name: str                     # 键名本身非敏感值
    key_type: str                     # string/hash/list/set/zstream 等
    ttl_ms: Optional[int] = None      # -1=无 TTL；-2 不出现（键存在性已验证）
    encoding: Optional[str] = None    # OBJECT ENCODING 结果
    mem_bytes: Optional[int] = None   # MEMORY USAGE 结果
    value_sample: Optional[str] = None  # ≤512B 已脱敏样例


@dataclass
class RelationRedacted:
    """确定性关联证据 DTO（relations 表，REQ-SU-016/017）。

    left_ref/right_ref 形如 'pages:12' / 'api:3' / 'redis_pattern:5'。
    evidence 为重合度/包含度等数值证据（RedactedDict）。
    """

    rtype: str                        # page_api/api_table/redis_entity
    left_ref: str
    right_ref: str
    score: float                      # 数值证据（重合度/包含度）
    evidence: Optional[RedactedDict] = None


@dataclass
class BudgetSummaryRedacted:
    """预算消耗摘要 DTO（limiter.summary() 产物 → 第 1 节 + summary.json）。"""

    max_pages: int
    max_depth: int
    max_actions_per_page: int
    time_budget_minutes: int
    pages_consumed: int
    actions_consumed: int
    depth_reached: int
    elapsed_seconds: float
    exhausted_dimension: Optional[str] = None   # 耗尽维度名（None=未耗尽）
