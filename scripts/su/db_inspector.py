"""DB 透镜纯函数与采集类——架构 ARCH-SU-001 §2.3.8，PRD REQ-SU-010/011/012/013。

**与 db_guard.py 的分层关系（相对架构文件清单的合理微调）**：架构把
ReadOnlyGuard / DbSessionHardener / build_containment_sql /
ImplicitFkCountExecutor / DataMasker / normalize_type_family 全部列在
``db_inspector.py``。安全守卫两类（ReadOnlyGuard、DbSessionHardener）已按
"纯状态机可脱离 DB 容器全量单测 + preflight 反向依赖"的理由先行拆入
``su/db_guard.py``；本模块补齐 §2.3.8 其余组件并以
``from su.db_guard import ReadOnlyGuard, DbSessionHardener`` 复用守卫。
全包 ``cursor.execute`` 唯一所在地保持 ``su/db_guard.py::ReadOnlyGuard.execute``
不变（§5.1 静态审查项）——本模块的 :class:`DbInspector` 同样只经守卫通道下发。

本模块组件：
  - :func:`build_containment_sql`：包含度查询生成器（纯函数）——输出
    NOT EXISTS 形态 SQL，**禁用 NOT IN**（子查询含 NULL 时三值逻辑导致
    整体恒 0 的陷阱，2026-09-28 审查修订）；产物必须能通过
    ``ReadOnlyGuard.validate``（单测断言）。
  - :func:`containment_from_counts`：非空值数/未命中数 → 包含度纯函数。
  - :class:`ImplicitFkCountExecutor`：薄层计数执行器，全部语句经
    ReadOnlyGuard 单通道下发。
  - :class:`DataMasker`：采样脱敏器（列名/注释中英词典 + 值形态正则双通道，
    值形态优先，PRD §8 口径）。
  - :func:`normalize_type_family`：数据类型 → 类型族（int/string/uuid/other），
    与 ``db_columns.type_family`` CHECK 枚举对齐（REQ-SU-013 规则 2）。
  - :class:`DbInspector`：**采集类**（§2.3.8）——建连（含会话只读加固）、
    information_schema 内省、样采（DataMasker 逐列）、隐式 FK 三重预筛。
    驱动模块由编排层经 :class:`su.deps.DependencyReport` 注入，本模块
    **绝不顶层 import pymysql/psycopg2**（REQ-SU-021 软依赖红线）。
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from su.config import DatabaseConfig, redact
from su.db_guard import DbSessionHardener, ReadOnlyGuard
from su.dto import DbTableRedacted, ImplicitFkCandidateRedacted, RedactedDict
# FIX(2026-09-28 自测发现)：单复数还原与 relation_analyzer 共用单一实现，
# 消除规则双处漂移（relation_analyzer 仅依赖 dto/state_store，无循环导入）
from su.relation_analyzer import _singularize as _relation_singularize
from su.state_store import StateStore

__all__ = [
    # 守卫再导出：下游（preflight / DbInspector）统一从 db_inspector 取，
    # 与架构 §2.3.8 "守卫住在 db_inspector" 的使用者视角保持一致
    "ReadOnlyGuard",
    "DbSessionHardener",
    "build_containment_sql",
    "containment_from_counts",
    "ImplicitFkCountExecutor",
    "DataMasker",
    "normalize_type_family",
    # 采集类（§2.3.8 后半段，驱动经依赖注入使用）
    "DbInspector",
    "ImplicitFkCandidate",
]

# ---------------------------------------------------------------------------
# 标识符白名单（build_containment_sql 的拼接面防线）
# ---------------------------------------------------------------------------
# 表/列名来自 information_schema 采集所得字面量（架构：不接受用户输入），
# 但生成 SQL 前仍以强白名单校验字符集——这是纵深防御：即使上游采集被污染，
# 标识符注入也无法穿过该正则（反引号无法逃逸反引号封闭形态）。
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

# ---------------------------------------------------------------------------
# 类型族归一表（§3 DDL：db_columns.type_family ∈ int/string/uuid/other）
# ---------------------------------------------------------------------------
# 数值族：MySQL 整型/定点/浮点 + PG 对应形态（information_schema.data_type
# 返回小写全称，如 'integer'、'bigint unsigned'）
_INT_TYPE_TOKENS = frozenset({
    "int", "integer", "tinyint", "smallint", "mediumint", "bigint",
    "decimal", "numeric", "float", "double", "real", "bit", "year",
    "serial", "bigserial", "smallserial", "int2", "int4", "int8",
    "float4", "float8", "money",
})
# 字符串族：char/varchar/text 系 + 枚举/集（语义上是字符串标签）
_STRING_TYPE_TOKENS = frozenset({
    "char", "varchar", "tinytext", "text", "mediumtext", "longtext",
    "character", "character varying", "name", "citext", "enum", "set",
    "string",
})
# UUID 族：PG 原生 uuid；MySQL 侧 uuid 以 char(36) 形态出现，由列名/值形态
# 另判（数据形态判定归 prescreen 规则，类型族仅按声明类型归一）
_UUID_TYPE_TOKENS = frozenset({"uuid"})

# data_type 中的修饰词剥离（'bigint unsigned'、'varchar(255)'、'numeric(10,2)'）
_TYPE_MODIFIER_RE = re.compile(r"\s*\((?:[^)]*)\)")
# 复合声明取首词：'bigint unsigned' → 'bigint'、'double precision' → 'double'、
# 'character varying' → 'character'。用 split()[0]（2026-09-28 自测修正：
# 旧实现的"取前缀"正则会把 'bigint unsigned' 整串带空格取出，查表必失配）。


def normalize_type_family(data_type: str) -> str:
    """数据库声明类型 → 类型族归一（纯函数，REQ-SU-013 规则 2 的匹配基准）。

    归一口径（与 ``db_columns.type_family`` CHECK 枚举严格一致）：
      - ``'int'``：整型/定点/浮点/位/序列（MySQL+PG 全部数值声明类型）；
      - ``'string'``：char/varchar/text 系、枚举/集、PG name/citext；
      - ``'uuid'``：PG 原生 uuid（MySQL 的 char(36) 存 UUID 属**值形态**问题，
        声明类型仍是 string——由 DataMasker/预筛的值形态通道另行处理）；
      - ``'other'``：日期时间、二进制、json、几何等其余全部（隐式 FK 类型族
        兼容判定中视为不兼容，保守排除）。

    输入容错：大小写不敏感；剥离长度修饰（``varchar(255)``）与复合修饰词
    （``bigint unsigned``、``double precision`` 取首词）；None/空串 → 'other'。

    Args:
        data_type: information_schema 返回的声明类型文本。

    Returns:
        str: ``'int'`` / ``'string'`` / ``'uuid'`` / ``'other'`` 之一。
    """
    if not data_type:
        return "other"
    # 归一小写并去首尾空白
    normalized = str(data_type).strip().lower()
    if not normalized:
        return "other"
    # 剥离长度/精度修饰：'numeric(10,2)' → 'numeric'
    normalized = _TYPE_MODIFIER_RE.sub("", normalized).strip()
    # 复合声明取首词：'double precision' → 'double'；'bigint unsigned' → 'bigint'
    words = normalized.split()
    head = words[0] if words else normalized
    if head in _UUID_TYPE_TOKENS:
        return "uuid"
    if head in _INT_TYPE_TOKENS:
        return "int"
    if head in _STRING_TYPE_TOKENS:
        return "string"
    # 'timestamp with time zone' 之类取首词后仍不在表内 → other（日期时间保守归 other）
    return "other"


# ---------------------------------------------------------------------------
# 包含度 SQL 生成器（纯函数，REQ-SU-013 规则 3 的 SQL 侧，2026-09-28 审查拆层）
# ---------------------------------------------------------------------------

def _quote_identifier(name: str, kind_label: str) -> str:
    """校验并反引号封闭标识符（MySQL 反引号形态；PG 亦接受反引号? 否——见下）。

    架构口径："表/列名只允许来自采集所得字面量（不接受用户输入），标识符
    转义后内插"。实现：
      1. 白名单正则校验（仅 [A-Za-z0-9_$]，首字符非数字）——含任何其它字符
         （空白、引号、分号、注释符）即抛错，注入面在此归零；
      2. 反引号封闭。MySQL 原生支持；PG 场景由 ImplicitFkCountExecutor 执行前
         经 ReadOnlyGuard.validate——validate 的反引号态与 PG 双引号语义不同，
         故对 PG 由 :func:`build_containment_sql` 的 ``dialect`` 参数决定引号
         字符（默认 mysql）。

    Args:
        name: 标识符（表名或列名，采集所得字面量）。
        kind_label: 报错定位标签（'child_table' 等）。

    Returns:
        str: 已通过白名单校验的裸标识符（引号由调用方按方言添加）。

    Raises:
        ValueError: 标识符含白名单外字符（防注入纵深防御，非用户输入面）。
    """
    if not name or not _IDENTIFIER_RE.match(name):
        raise ValueError(
            "包含度 SQL 的标识符 {0} 含非法字符：{1!r}"
            "（仅允许字母/数字/下划线/$，来源必须为 information_schema 采集字面量）".format(
                kind_label, name)
        )
    return name


def build_containment_sql(
    child_table: str,
    child_col: str,
    parent_table: str,
    parent_col: str,
    limit: int = 1000,
    dialect: str = "mysql",
) -> Tuple[str, str]:
    """包含度查询生成器（纯函数）——输出 ``(非空值数 SQL, 未命中数 SQL)``。

    SQL 语义（REQ-SU-013 规则 3：子侧 ≤limit 去重值的包含度）：
      - 语句①：子表该列在 ≤limit 行取样内的 **DISTINCT 非空值数**；
      - 语句②：同取样内 **在父表中不存在的 DISTINCT 非空值数**，形态固定为::

            SELECT COUNT(*) FROM (
              SELECT c.<col> FROM <child> c
              WHERE c.<col> IS NOT NULL LIMIT <limit>
            ) s WHERE NOT EXISTS (
              SELECT 1 FROM <parent> p WHERE p.<parent_col> = s.<col>
            )

    审查修订两点强制口径：
      1. **禁用 ``NOT IN (SELECT …)``**——子查询结果含 NULL 时三值逻辑使
         ``x NOT IN (…, NULL)`` 恒为 UNKNOWN，未命中数恒 0、包含度恒 1，
         是伪证据（架构 §2.3.8 明令）。NOT EXISTS 对 NULL 天然免疫。
      2. 两条语句首关键字均为 SELECT，且标识符经白名单校验后内插、LIMIT
         为服务端 int 参数内插（limit 由本函数强转正整数，无字符串注入面），
         产物必须能通过 ``ReadOnlyGuard.validate``（单测逐条断言）。

    引号方言：``dialect='mysql'`` 用反引号；``dialect='postgresql'`` 用双引号
    （PG 标准标识符引用）。采样 LIMIT 子查询在 MySQL 5.7 / PG 均为合法形态。

    Args:
        child_table: 子表名（外键所在表，采集字面量）。
        child_col: 子表列名。
        parent_table: 父表名（被引用表）。
        parent_col: 父表列名。
        limit: 子侧去重取样上限，默认 1000（PRD REQ-SU-013 口径）；
            非正整数一律归一为 1000（保守取默认）。
        dialect: ``'mysql'`` / ``'postgresql'``，决定标识符引号字符。

    Returns:
        tuple[str, str]: ``(非空值数 SQL, 未命中数 SQL)``，均为单语句、无末尾
        分号（validate 产物口径一致，可直接进 ReadOnlyGuard.execute）。

    Raises:
        ValueError: 任一标识符含白名单外字符（防注入纵深防御）。
    """
    # 标识符白名单校验（四个全查，任何注入字符即抛）
    _quote_identifier(child_table, "child_table")
    _quote_identifier(child_col, "child_col")
    _quote_identifier(parent_table, "parent_table")
    _quote_identifier(parent_col, "parent_col")

    # limit 归一：非 int / bool / ≤0 → 默认 1000（int() 后再查一次，杜绝 0/负数）
    try:
        limit_int = int(limit)
    except (TypeError, ValueError):
        limit_int = 1000
    if limit_int <= 0:
        limit_int = 1000

    quote = "`" if (dialect or "mysql").strip().lower() == "mysql" else '"'

    def q(identifier: str) -> str:
        """按方言引号封闭（已通过白名单校验，引号字符不可能出现在内部）。"""
        return "{0}{1}{2}".format(quote, identifier, quote)

    # 取样子查询：DISTINCT 非空值 + LIMIT（两条语句共用同一取样口径，
    # 保证"未命中数 ≤ 非空值数"在数学上恒成立，包含度 ∈ [0,1]）
    sample = (
        "SELECT DISTINCT {child}.{child_col} AS v FROM {child} {child} "
        "WHERE {child}.{child_col} IS NOT NULL LIMIT {limit}"
    ).format(
        child=q(child_table), child_col=q(child_col), limit=limit_int,
    )

    # 语句①：取样内 DISTINCT 非空值总数
    total_sql = "SELECT COUNT(*) FROM ({0}) s".format(sample)

    # 语句②：取样内父表不存在的值数——NOT EXISTS 形态（禁 NOT IN，见 docstring）
    missing_sql = (
        "SELECT COUNT(*) FROM ({sample}) s WHERE NOT EXISTS "
        "(SELECT 1 FROM {parent} p WHERE p.{parent_col} = s.v)"
    ).format(
        sample=sample,
        parent=q(parent_table),
        parent_col=q(parent_col),
    )
    return total_sql, missing_sql


def containment_from_counts(total: int, missing: int) -> float:
    """包含度纯函数：``(total - missing) / total``（REQ-SU-013 规则 3 得分）。

    边界口径：
      - ``total <= 0``（子侧无非空值）→ 返回 ``0.0``：无值即无证据，
        **绝不返回 1.0**（空真包含是伪证据，预筛应止步）；
      - ``missing > total``（理论上不可能——同一取样；脏计数防御）→ 0.0；
      - 正常域返回 ``[0, 1]`` 浮点。

    Args:
        total: 非空值数（语句①计数结果）。
        missing: 未命中数（语句②计数结果）。

    Returns:
        float: 包含度 ∈ [0, 1]。
    """
    try:
        total_int = int(total)
        missing_int = int(missing)
    except (TypeError, ValueError):
        return 0.0
    if total_int <= 0 or missing_int > total_int:
        return 0.0
    return (total_int - missing_int) / float(total_int)


# ---------------------------------------------------------------------------
# 计数执行器（薄层，2026-09-28 审查拆层）
# ---------------------------------------------------------------------------

class ImplicitFkCountExecutor:
    """包含度计数执行器：执行生成器的两条 SQL 并取回计数（§2.3.8）。

    分层理由（审查修订）：SQL **生成**是纯函数（本模块单测全覆盖），
    计数**执行**需要真实连接——本薄层把两者接起来，自身只有"取首行首列"
    一处逻辑。单测以行内存过滤替身注入（自实现 execute 的假连接），
    不依赖真实方言；真实 MySQL/PG 方言正确性由容器场景验证（§11.2/§11.3 [8]）。

    全部语句一律经 :class:`ReadOnlyGuard` 单通道下发（红线②）：本类自身
    不触碰 cursor，不存在绕过校验器的第二条执行路径。
    """

    def __init__(self, guard: Optional[ReadOnlyGuard] = None) -> None:
        """初始化计数执行器。

        Args:
            guard: 复用的只读守卫（默认内部新建；二者无状态，等效）。
        """
        self._guard = guard if guard is not None else ReadOnlyGuard()

    def execute_counts(
        self,
        conn: Any,
        total_sql: str,
        missing_sql: str,
    ) -> Tuple[int, int]:
        """执行两条计数 SQL，返回 ``(非空值数, 未命中数)``。

        Args:
            conn: DB-API 2.0 连接对象（经 guard 单通道使用）。
            total_sql: :func:`build_containment_sql` 产物①。
            missing_sql: 产物②。

        Returns:
            tuple[int, int]: 两个计数（任一查询无结果行按 0 计）。

        Raises:
            SuReadonlyViolation: 语句未通过只读校验（理论上不发生——
                生成器产物恒为 SELECT 语句，此处是纵深防御）。
        """
        total_rows = self._guard.execute(conn, total_sql)
        missing_rows = self._guard.execute(conn, missing_sql)
        return _first_cell_int(total_rows), _first_cell_int(missing_rows)

    def containment(
        self,
        conn: Any,
        child_table: str,
        child_col: str,
        parent_table: str,
        parent_col: str,
        limit: int = 1000,
        dialect: str = "mysql",
    ) -> float:
        """生成 SQL → 执行计数 → 归一包含度（预筛规则 3 的完整调用面）。

        Args:
            conn: DB-API 2.0 连接对象。
            child_table: 子表名（采集字面量）。
            child_col: 子表列名。
            parent_table: 父表名。
            parent_col: 父表列名。
            limit: 子侧去重取样上限（默认 1000，PRD 口径）。
            dialect: ``'mysql'`` / ``'postgresql'``。

        Returns:
            float: 包含度 ∈ [0, 1]。
        """
        total_sql, missing_sql = build_containment_sql(
            child_table, child_col, parent_table, parent_col,
            limit=limit, dialect=dialect,
        )
        total, missing = self.execute_counts(conn, total_sql, missing_sql)
        return containment_from_counts(total, missing)


def _first_cell_int(rows: List[RedactedDict]) -> int:
    """取 COUNT 结果首行首列并转 int（空结果/NULL/非数字 → 0）。

    Args:
        rows: ReadOnlyGuard.execute 的返回值（COUNT 查询恒一行一列）。

    Returns:
        int: 计数值；任何异常形态按 0 处理（计数缺失宁小勿大，保守预筛）。
    """
    if not rows:
        return 0
    for value in rows[0].values():
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    return 0


# ---------------------------------------------------------------------------
# 采样脱敏器（REQ-SU-012，§2.3.8 DataMasker）
# ---------------------------------------------------------------------------

class DataMasker:
    """采样值脱敏器：列名词典 + 值形态正则双通道（值形态优先，PRD §8）。

    与 :func:`config.redact` 的分工：redact 是**键名 + PII 值形态**的通用落盘
    管线（``ReadOnlyGuard.execute`` 已逐 cell 过一遍）；DataMasker 在其之上
    补 **DB 语境专属**的两层：
      1. 列名/注释命中敏感词典（``password``/``手机号`` 等）→ 该列**全部值**
         替换 ``<REDACTED:类型>``（redact 的键名正则只精确匹配 password 等
         少数键，phone/mobile/bank 这类"列名即敏感"的字段需要词典兜住）；
      2. 值长度区间保留 + 字符类别保留：脱敏输出 ``<REDACTED:类型|len=11|class=digits>``
         形态，供 LLM 判断字段语义而不接触明文（架构"保留长度区间/字符类别"）。
    BLOB/bytes 或超长文本 → ``<BLOB size=N>``（不落正文）。
    """

    # 敏感列名词典（§2.3.8 中英原文口径；比较时对列名/注释做"包含"匹配，
    # 列名常带前后缀：user_mobile / id_card_no / 手机号(备用)）
    SENSITIVE_COLUMN_DICT = {
        "password", "passwd", "pwd", "secret", "token", "api_key",
        "phone", "mobile", "email", "mail",
        "id_card", "idcard", "identity", "bank", "card", "address",
        "身份证", "手机号", "手机", "电话", "邮箱", "邮件", "银行卡", "信用卡",
        "地址", "密码", "密钥", "令牌",
    }

    # 词典命中 → 脱敏类型标签映射（列名通道专用；值形态通道复用 config 口径）
    _COLUMN_LABELS: Dict[str, str] = {
        "password": "password", "passwd": "password", "pwd": "password",
        "密码": "password",
        "secret": "secret", "密钥": "secret",
        "token": "token", "令牌": "token", "api_key": "api_key",
        "phone": "phone", "mobile": "phone",
        "手机": "phone", "电话": "phone", "手机号": "phone",
        "email": "email", "mail": "email", "邮箱": "email", "邮件": "email",
        "id_card": "id_card", "idcard": "id_card", "identity": "id_card",
        "身份证": "id_card",
        "bank": "bank_card", "card": "bank_card", "银行卡": "bank_card",
        "信用卡": "bank_card",
        "address": "address", "地址": "address",
    }

    # 值形态正则（复用 config 的 PII 口径，标签保持一致）
    _VALUE_PATTERNS: Tuple[Tuple[str, "re.Pattern"], ...] = (
        ("phone", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
        ("id_card", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
        ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
        ("bank_card", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    )

    # 值形态命中的键名标签（与 ReadOnlyGuard 逐 cell redact 输出的
    # <REDACTED:类型> 前缀对齐——识别"上游 redact 已处理"的 cell 不二次加工）
    _ALREADY_REDACTED_PREFIX = "<REDACTED:"

    # 超长文本阈值（NFR-SU-005：采样单值超过即按 BLOB 形态归档，不落正文）
    MAX_VALUE_LEN = 512
    # BLOB 预览字节数（bytes 值保留前 N 字节的 hex 供编码判断，其余丢弃）
    BLOB_PREVIEW_BYTES = 16

    def mask_value(
        self,
        column_name: str,
        comment: Optional[str],
        value: Any,
    ) -> str:
        """单值脱敏（列名词典通道 ∪ 值形态正则通道，值形态优先）。

        判定顺序（宁多脱不漏脱，但避免对已脱敏 cell 二次加工）：
          1. ``None`` → 原样返回 ``None``（采样语义：NULL 就是 NULL）；
          2. bytes/bytearray → ``<BLOB size=N>``（前 16 字节 hex 预览，二进制
             永不解码入文）；
          3. 值形态正则命中（手机号/身份证/邮箱/银行卡）→
             ``<REDACTED:类型|len=N|class=<类别>>``（**优先于列名通道**，
             PRD §8 值形态优先；两通道命中类型不同也不冲突——值形态是事实）；
          4. 列名/注释命中词典 → ``<REDACTED:词典类型|len=N|class=<类别>>``；
          5. 超长字符串（>512）→ ``<BLOB size=N>``；
          6. 上游 ReadOnlyGuard 已 redact 的 cell（``<REDACTED:`` 前缀）→ 原样；
          7. 其余（数字/日期/短文本）→ ``str(value)`` 原样。

        Args:
            column_name: 列名（采集所得字面量）。
            comment: 列注释（可空；中文注释是敏感列的高频信号）。
            value: 采样原始值（ReadOnlyGuard 已 redact 的字符串或原始标量）。

        Returns:
            str: 脱敏文本（NULL 例外返回 None——列值缺失本身是合法采样事实）。
        """
        if value is None:
            return None
        # ---- bytes/BLOB 通道：永不解码正文入文 ----
        if isinstance(value, (bytes, bytearray, memoryview)):
            raw = bytes(value)
            preview = raw[:self.BLOB_PREVIEW_BYTES].hex()
            return "<BLOB size={0} preview_hex={1}>".format(len(raw), preview)
        text = value if isinstance(value, str) else str(value)
        # ---- 上游已 redact 的值：识别 <REDACTED:类型> 前缀则原样保留 ----
        if text.startswith(self._ALREADY_REDACTED_PREFIX):
            # 仅当整串就是一个占位符（无残留明文）才放行；
            # 混合串（"手机：<REDACTED:phone>"）继续走词典通道重新包壳
            if text.endswith(">") and "|" not in text and ":" in text:
                return text
        # ---- 超长文本按 BLOB 归档（NFR-SU-005）----
        if len(text) > self.MAX_VALUE_LEN:
            return "<BLOB size={0}>".format(len(text))
        # ---- 值形态正则通道（值形态优先于列名，PRD §8）----
        for pii_type, pattern in self._VALUE_PATTERNS:
            if pattern.search(text):
                return self._redacted_with_shape(pii_type, text)
        # ---- 列名/注释词典通道（包含匹配，大小写不敏感）----
        dict_label = self._dict_label(column_name, comment)
        if dict_label is not None:
            return self._redacted_with_shape(dict_label, text)
        return text

    # ---- 内部辅助 ----

    def _dict_label(self, column_name: str, comment: Optional[str]) -> Optional[str]:
        """列名/注释过敏感词典，返回脱敏类型标签（未命中 None）。

        匹配口径：对列名与注释分别做"小写后包含匹配"——词典条目任一出现
        即命中（user_mobile / '用户手机号' / id_card_no 都能兜住）。
        先查长条目再查短条目，使 ``手机号`` 的标签优先于 ``手机``（结果同为
        phone，仅为确定性）；卡号类 ``card`` 放最后查，避免 ``card_no`` 类
        业务编号被 ``card`` 抢先误判前先让 ``bank``/``信用卡`` 等长词命中。

        Args:
            column_name: 列名。
            comment: 列注释（可空）。

        Returns:
            str | None: 脱敏类型标签；无命中 None。
        """
        haystacks = [
            (column_name or "").lower(),
            (comment or "").lower(),
        ]
        # 词典按条目长度降序，长词优先（'手机号' 先于 '手机'、'银行卡' 先于 'card'）
        for entry in sorted(self.SENSITIVE_COLUMN_DICT, key=len, reverse=True):
            entry_lower = entry.lower()
            for haystack in haystacks:
                if haystack and entry_lower in haystack:
                    return self._COLUMN_LABELS.get(entry, "sensitive")
        return None

    @staticmethod
    def _redacted_with_shape(label: str, text: str) -> str:
        """生成保留长度区间/字符类别的脱敏文本（不落任何原文片段）。

        长度取**整值长度**（保留长度区间信号：LLM 可据 len=11 推断手机号列）；
        字符类别归一为四类：digits（纯数字）/ alnum（字母数字）/
        mixed（含符号/空白）/ cjk（含中日韩字符）——仅类别，不含具体字符。

        Args:
            label: 脱敏类型标签（phone/email/password…）。
            text: 原始值文本（只用于测长与测类别，内容不外泄）。

        Returns:
            str: ``<REDACTED:label|len=N|class=类别>``。
        """
        if text.isdigit():
            char_class = "digits"
        elif any("\u4e00" <= ch <= "\u9fff" for ch in text):
            char_class = "cjk"
        elif text.isalnum():
            char_class = "alnum"
        else:
            char_class = "mixed"
        return "<REDACTED:{0}|len={1}|class={2}>".format(label, len(text), char_class)


# ---------------------------------------------------------------------------
# 隐式 FK 候选（内存工作对象；落库形态为 ImplicitFkCandidateRedacted）
# ---------------------------------------------------------------------------

@dataclass
class ImplicitFkCandidate:
    """隐式外键候选（§2.3.8 prescreen 产物，REQ-SU-013）。

    Attributes:
        child_table: 外键所在表（含 schema 限定，'schema.table' 形态）。
        child_column: 子表列名。
        parent_table: 被引用表（含 schema 限定）。
        parent_column: 父表列名。
        prescreen_score: 三重预筛加权分 ∈ [0,1]（权重见
            :data:`PRESCREEN_WEIGHTS`；止步规则之前的规则命中均计分）。
        stopped_at_rule: 止步规则编号——1=命名约定未过；2=类型族不兼容；
            3=包含度未达阈值；0=三重全过（候选成立）。
        containment: 包含度 ∈ [0,1]（止步 1/2 时为 None，未执行计数查询）。
        naming_hit: 命名约定通道的命中说明（如 'user_id→users.id'）。
    """

    child_table: str
    child_column: str
    parent_table: str
    parent_column: str
    prescreen_score: float
    stopped_at_rule: int
    containment: Optional[float] = None
    naming_hit: str = ""

    def to_redacted(self) -> ImplicitFkCandidateRedacted:
        """转落库 DTO（evidence_json 已脱敏并带"推断，需确认"标注，AP-1）。

        Returns:
            ImplicitFkCandidateRedacted: state_store.insert_implicit_fk 入参。
        """
        evidence = redact({
            "naming": self.naming_hit,
            "containment": self.containment,
            "stopped_at_rule": self.stopped_at_rule,
            "note": "隐式外键为预筛推断，需人工确认（REQ-SU-013）",
        })
        import json as _json  # 局部导入：仅本方法使用，避免顶层噪音

        return ImplicitFkCandidateRedacted(
            child_table=self.child_table,
            child_column=self.child_column,
            parent_table=self.parent_table,
            parent_column=self.parent_column,
            prescreen_score=self.prescreen_score,
            stopped_at_rule=self.stopped_at_rule,
            containment=self.containment,
            evidence_json=_json.dumps(dict(evidence), ensure_ascii=False,
                                      sort_keys=True),
        )


# 三重预筛加权（REQ-SU-013：命名 0.4 + 类型族 0.2 + 包含度 0.4，命中即得分）
PRESCREEN_WEIGHTS = {"naming": 0.4, "type_family": 0.2, "containment": 0.4}

# 包含度达标阈值（§2.3.8 默认 0.85，可由调用方覆盖）
DEFAULT_CONTAINMENT_THRESHOLD = 0.85

# 包含度计数的子侧去重取样上限（PRD REQ-SU-013 口径）
CONTAINMENT_SAMPLE_LIMIT = 1000

# 英文常用单复数还原规则（命名约定通道的归一键）
# FIX(2026-09-28 自测发现)：旧表第 2 条 `(ses|xes|zes|ches|shes)$ → ""` 把
# 整个匹配段吞掉，addresses→addres、boxes→bo、batches→bat（过度剥离）。
# 修复：删除本地 _SINGULAR_RULES 表，:func:`singularize` 直接委托
# relation_analyzer._singularize（其"es 结尾且词根尾缀 ∈ {ch,sh,s,x,z,o}
# 才去 es"的规则经 test_su_relation 验证正确：addresses→address、
# boxes→box），规则单点维护、杜绝第三处漂移。

# 外键列名形态：<parent>_id / <parent>_pk / <parent>Id（驼峰）——命名约定入口
_FK_COLUMN_RE = re.compile(r"^(?P<base>[A-Za-z][A-Za-z0-9$]*)_(?:id|pk)$",
                           re.IGNORECASE)


def singularize(word: str) -> str:
    """英文单词单数化（命名约定通道的轻量归一，纯函数）。

    仅覆盖采集场景高频形态（``categories→category``、``addresses→address``、
    ``users→user``）；``person/child`` 等不规则形态**不做**（漏归一只会让
    命名通道 miss → 止步规则 1，宁缺勿错，不产生假阳性候选）。

    FIX(2026-09-28 自测发现)：改为委托 relation_analyzer._singularize——
    旧本地规则表会把 addresses 剥成 addres、boxes 剥成 bo（整个
    'ses/xes' 匹配段被吞）。委托后 addresses→address、boxes→box、
    batches→batch，且 -ss/-us/-is 结尾保护口径与 relation 侧一致。

    Args:
        word: 小写单词（调用方负责 lower）。

    Returns:
        str: 单数形态（无法判定时原样返回）。
    """
    lowered = (word or "").lower()
    if not lowered:
        return lowered
    return _relation_singularize(lowered)


# ---------------------------------------------------------------------------
# 采集类（§2.3.8 DbInspector——驱动经依赖注入，红线②全包守卫单通道）
# ---------------------------------------------------------------------------

class DbInspector:
    """DB 透镜采集类：内省 / 采样 / 隐式 FK 预筛（REQ-SU-010~013）。

    构造只存配置与注入物，**不建连**（无 DB 环境可自由构造做单测）；
    建连发生在 :meth:`connect`——驱动模块对象由编排层从
    :class:`su.deps.DependencyReport` 取出后注入（软依赖红线：本模块
    绝不 ``import pymysql`` / ``import psycopg2``）。

    红线②在本类的体现：所有语句（含 information_schema 内省、采样、
    包含度计数）一律经 :meth:`ReadOnlyGuard.execute` 下发；建连后立即
    :class:`DbSessionHardener` 会话只读加固并验证生效。::

        inspector = DbInspector(db_cfg, store, driver_module)
        inspector.connect()
        n_tables = inspector.collect_schema()
        inspector.sample_tables(cfg.budget.sample_rows)
        candidates = inspector.prescreen_implicit_fks()
        inspector.close()
    """

    def __init__(
        self,
        cfg: DatabaseConfig,
        store: StateStore,
        driver_module: Any,
        guard: Optional[ReadOnlyGuard] = None,
    ) -> None:
        """初始化采集器（不建连）。

        Args:
            cfg: 数据库配置段（engine/host/…/schemas）。
            store: SQLite 状态机（db_tables/db_columns/db_samples 写入面）。
            driver_module: pymysql / psycopg2 模块对象（DependencyReport 注入）。
            guard: 复用的只读守卫（默认内部新建；执行通道唯一性不受影响）。

        Raises:
            ValueError: driver_module 为 None（编排层降级判定遗漏时快速失败，
                绝不静默 mock——禁 mock 红线）。
        """
        if driver_module is None:
            raise ValueError(
                "DbInspector 需要驱动模块对象（pymysql/psycopg2，经 "
                "DependencyReport 注入）；None 意味着编排层降级判定遗漏——"
                "驱动缺失时应跳过 DB 透镜并登记显式缺失声明（REQ-SU-021）"
            )
        self._cfg = cfg
        self._store = store
        self._driver = driver_module
        self._guard = guard if guard is not None else ReadOnlyGuard()
        self._masker = DataMasker()
        self._executor = ImplicitFkCountExecutor(self._guard)
        self._conn: Any = None
        # collect_schema 产物缓存：[(schema, table, kind)] 供采样/预筛复用
        self._tables: List[Tuple[str, str, str]] = []
        # 列元数据缓存：'schema.table' → [列 dict（未脱敏工作副本）]
        self._columns: Dict[str, List[Dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # 连接管理（reveal 边界②：建连是凭据唯一合法落地位置之一）
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """建立**已加固**连接（幂等：已连接直接返回）。

        流程（与 preflight 的 _connect_db 同口径）：
          1. 按 engine 调注入驱动的 ``connect``——密码仅在此经
             :meth:`SensitiveStr.reveal` 落地（§5.1 静态审查白名单位置）；
          2. :meth:`DbSessionHardener.harden` 会话只读加固并验证生效
             （加固语句仍经 ReadOnlyGuard 单一通道下发）。

        Raises:
            Exception: 驱动层连接/加固错误原样上抛（编排层登记降级）。
        """
        if self._conn is not None:
            return
        cfg = self._cfg
        engine = cfg.engine.strip().lower()
        if engine == "mysql":
            conn = self._driver.connect(
                host=cfg.host,
                port=cfg.port,
                user=cfg.user,
                # reveal 边界②：DB 建连（凭据明文仅进入驱动层，不落任何存储）
                password=cfg.password.reveal(),
                database=cfg.database,
                charset="utf8mb4",
                autocommit=True,
            )
        else:  # postgresql（config.validate 已保证枚举二选一）
            conn = self._driver.connect(
                host=cfg.host,
                port=cfg.port,
                user=cfg.user,
                password=cfg.password.reveal(),  # reveal 边界②：DB 建连
                dbname=cfg.database,
            )
        try:
            # 加固失败必须断开上抛：未加固连接绝不允许进入采集面
            DbSessionHardener().harden(conn, engine, guard=self._guard)
        except Exception:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - 关闭失败不掩盖原始异常
                pass
            raise
        self._conn = conn

    def close(self) -> None:
        """关闭连接（幂等；编排层 finally 调用，异常吸收不掩盖主流程结论）。"""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 - 关闭失败无恢复动作
                pass
            self._conn = None

    # ------------------------------------------------------------------
    # schema 内省（REQ-SU-011）
    # ------------------------------------------------------------------

    def collect_schema(self) -> int:
        """内省 information_schema 写 db_tables/db_columns（幂等 upsert）。

        MySQL：``information_schema.TABLES``（含 VIEW 识别）+
        ``COLUMNS`` + ``KEY_COLUMN_USAGE``（主键与显式 FK 目标）。
        PostgreSQL：``information_schema.tables/columns`` +
        ``pg_catalog.pg_constraint``（主键/FK）+ ``pg_class.reltuples``
        （行数估算，information_schema 无行数面）。

        schema 限定口径（§2.3.8）：``cfg.schemas`` 非空 → 仅采这些 schema；
        空 → MySQL 限定到 ``cfg.database`` 并排除 MySQL 系统库
        （information_schema/mysql/performance_schema/sys），PG 排除
        ``information_schema``/``pg_catalog``/``pg_toast``，默认 schema 用 public。

        Returns:
            int: 采集到的表数量（VIEW 计入 kind='VIEW'）。

        Raises:
            RuntimeError: 未调用 :meth:`connect`。
        """
        self._require_conn()
        engine = self._cfg.engine.strip().lower()
        if engine == "mysql":
            return self._collect_schema_mysql()
        return self._collect_schema_postgres()

    def _collect_schema_mysql(self) -> int:
        """MySQL 内省实现（TABLES/COLUMNS/KEY_COLUMN_USAGE 三查询）。

        Returns:
            int: 表数量。
        """
        schemas = [s for s in self._cfg.schemas if s] or [self._cfg.database]
        system_schemas = {"information_schema", "mysql", "performance_schema", "sys"}
        target_schemas = [s for s in schemas
                          if s.lower() not in system_schemas]
        if not target_schemas:
            return 0
        placeholders = ", ".join(["%s"] * len(target_schemas))

        # ① 表清单（BASE TABLE + VIEW 分开标识）
        tables_sql = (
            "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE, TABLE_ROWS, TABLE_COMMENT "
            "FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA IN ({0}) "
            "ORDER BY TABLE_SCHEMA, TABLE_NAME"
        ).format(placeholders)
        table_rows = self._guard.execute(self._conn, tables_sql, tuple(target_schemas))

        # ② 列清单
        columns_sql = (
            "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE, COLUMN_KEY, "
            "COLUMN_COMMENT, ORDINAL_POSITION "
            "FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA IN ({0}) "
            "ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION"
        ).format(placeholders)
        column_rows = self._guard.execute(self._conn, columns_sql, tuple(target_schemas))

        # ③ 显式外键目标（REFERENTIAL_CONSTRAINTS 与 KEY_COLUMN_USAGE join；
        #    仅取列级 fk_target 展示用——隐式 FK 预筛不依赖显式约束）
        fk_sql = (
            "SELECT k.TABLE_SCHEMA, k.TABLE_NAME, k.COLUMN_NAME, "
            "CONCAT(r.REFERENCED_TABLE_SCHEMA, '.', r.REFERENCED_TABLE_NAME) "
            "AS ref_table "
            "FROM information_schema.KEY_COLUMN_USAGE k "
            "JOIN information_schema.REFERENTIAL_CONSTRAINTS r "
            "ON k.CONSTRAINT_NAME = r.CONSTRAINT_NAME "
            "AND k.CONSTRAINT_SCHEMA = r.CONSTRAINT_SCHEMA "
            "WHERE k.TABLE_SCHEMA IN ({0}) AND k.REFERENCED_TABLE_NAME IS NOT NULL"
        ).format(placeholders)
        fk_rows = self._guard.execute(self._conn, fk_sql, tuple(target_schemas))
        fk_map: Dict[Tuple[str, str, str], str] = {}
        for row in fk_rows:
            key = (self._cell(row, "TABLE_SCHEMA"),
                   self._cell(row, "TABLE_NAME"),
                   self._cell(row, "COLUMN_NAME"))
            fk_map[key] = self._cell(row, "ref_table")

        # 列按表分组
        columns_by_table: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for row in column_rows:
            key = (self._cell(row, "TABLE_SCHEMA"), self._cell(row, "TABLE_NAME"))
            columns_by_table.setdefault(key, []).append(row)

        # FIX(2026-09-28 自测发现)：count 缺初始化——循环体内 `count += 1` 在
        # count 从未赋值时会抛 UnboundLocalError（任何非空 schema 采集必崩）。
        # 口径与 _collect_schema_postgres 的 `count = 0` 保持一致。
        count = 0
        for row in table_rows:
            schema = self._cell(row, "TABLE_SCHEMA")
            table = self._cell(row, "TABLE_NAME")
            table_type = (self._cell(row, "TABLE_TYPE") or "").upper()
            kind = "VIEW" if "VIEW" in table_type else "BASE TABLE"
            raw_columns = columns_by_table.get((schema, table), [])
            columns = self._build_column_dicts_mysql(raw_columns, schema, table, fk_map)
            dto = DbTableRedacted(
                schema_name=schema,
                table_name=table,
                kind=kind,
                columns=columns,
                row_estimate=self._cell_int(row, "TABLE_ROWS"),
                comment=redact({"c": self._cell(row, "TABLE_COMMENT")}).get("c"),
            )
            self._store.upsert_db_table(dto)
            qualified = "{0}.{1}".format(schema, table)
            # 工作副本保存**未脱敏**结构（列名/类型是 schema 元数据、非业务值；
            # ReadOnlyGuard 的逐 cell redact 已保证其中无 PII 值形态）
            self._columns[qualified] = raw_columns
            self._tables.append((schema, table, kind))
            count += 1
        return count

    @staticmethod
    def _build_column_dicts_mysql(
        raw_columns: List[RedactedDict],
        schema: str,
        table: str,
        fk_map: Dict[Tuple[str, str, str], str],
    ) -> List[RedactedDict]:
        """MySQL 列行 → db_columns DTO（type_family 归一 + pk/fk 标注）。

        Args:
            raw_columns: information_schema.COLUMNS 行（该表）。
            schema: schema 名。
            table: 表名。
            fk_map: 显式 FK 映射 (schema,table,column)→'schema.table'。

        Returns:
            list[RedactedDict]: upsert_db_table 的 columns 入参。
        """
        out: List[RedactedDict] = []
        for row in raw_columns:
            name = DbInspector._cell(row, "COLUMN_NAME")
            data_type = DbInspector._cell(row, "DATA_TYPE") or "unknown"
            column_key = (DbInspector._cell(row, "COLUMN_KEY") or "").upper()
            fk_target = fk_map.get((schema, table, name))
            out.append(RedactedDict({
                "name": name,
                "data_type": data_type,
                "type_family": normalize_type_family(data_type),
                "is_pk": 1 if column_key == "PRI" else 0,
                "fk_target": fk_target,
                "comment": DbInspector._cell(row, "COLUMN_COMMENT") or None,
            }))
        return out

    def _collect_schema_postgres(self) -> int:
        """PostgreSQL 内省实现（information_schema + pg_catalog 行数估算）。

        Returns:
            int: 表数量。
        """
        schemas = [s for s in self._cfg.schemas if s] or ["public"]
        system_schemas = {"information_schema", "pg_catalog", "pg_toast"}
        target_schemas = [s for s in schemas if s.lower() not in system_schemas]
        if not target_schemas:
            return 0
        placeholders = ", ".join(["%s"] * len(target_schemas))

        # ① 表清单 + reltuples 行数估算（pg_class 在 pg_catalog，按命名空间联查）
        tables_sql = (
            "SELECT t.table_schema, t.table_name, t.table_type, "
            "c.reltuples::bigint AS row_estimate "
            "FROM information_schema.tables t "
            "LEFT JOIN pg_catalog.pg_class c ON c.relname = t.table_name "
            "LEFT JOIN pg_catalog.pg_namespace n "
            "ON n.oid = c.relnamespace AND n.nspname = t.table_schema "
            "WHERE t.table_schema IN ({0}) "
            "AND t.table_type IN ('BASE TABLE','VIEW') "
            "ORDER BY t.table_schema, t.table_name"
        ).format(placeholders)
        table_rows = self._guard.execute(self._conn, tables_sql, tuple(target_schemas))

        # ② 列清单 + 列注释（col_description 按 regclass+列号定位列注释）
        columns_sql = (
            "SELECT c.table_schema, c.table_name, c.column_name, c.data_type, "
            "c.ordinal_position, "
            "col_description(format('%I.%I', c.table_schema, c.table_name)::regclass, "
            "c.ordinal_position) AS column_comment "
            "FROM information_schema.columns c "
            "WHERE c.table_schema IN ({0}) "
            "ORDER BY c.table_schema, c.table_name, c.ordinal_position"
        ).format(placeholders)
        column_rows = self._guard.execute(self._conn, columns_sql, tuple(target_schemas))

        # ③ 主键 / 显式 FK（pg_constraint：contype 'p'=主键 'f'=外键；
        #    conkey[1] 是首主键/首外键列号，经 pg_attribute 还原列名——
        #    组合键取首列即满足采集面展示与预筛需求）
        pk_sql = (
            "SELECT n.nspname AS table_schema, cl.relname AS table_name, "
            "a.attname AS column_name "
            "FROM pg_catalog.pg_constraint con "
            "JOIN pg_catalog.pg_class cl ON cl.oid = con.conrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid = cl.relnamespace "
            "JOIN pg_catalog.pg_attribute a "
            "ON a.attrelid = con.conrelid AND a.attnum = con.conkey[1] "
            "WHERE con.contype = 'p' AND n.nspname IN ({0})"
        ).format(placeholders)
        pk_rows = self._guard.execute(self._conn, pk_sql, tuple(target_schemas))
        pk_set = {(self._cell(r, "table_schema"), self._cell(r, "table_name"),
                   self._cell(r, "column_name")) for r in pk_rows}

        fk_sql = (
            "SELECT n.nspname AS table_schema, cl.relname AS table_name, "
            "a.attname AS column_name, "
            "concat(n2.nspname, '.', cl2.relname) AS ref_table "
            "FROM pg_catalog.pg_constraint con "
            "JOIN pg_catalog.pg_class cl ON cl.oid = con.conrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid = cl.relnamespace "
            "JOIN pg_catalog.pg_attribute a "
            "ON a.attrelid = con.conrelid AND a.attnum = con.conkey[1] "
            "JOIN pg_catalog.pg_class cl2 ON cl2.oid = con.confrelid "
            "JOIN pg_catalog.pg_namespace n2 ON n2.oid = cl2.relnamespace "
            "WHERE con.contype = 'f' AND n.nspname IN ({0})"
        ).format(placeholders)
        fk_rows = self._guard.execute(self._conn, fk_sql, tuple(target_schemas))
        fk_map: Dict[Tuple[str, str, str], str] = {}
        for row in fk_rows:
            key = (self._cell(row, "table_schema"), self._cell(row, "table_name"),
                   self._cell(row, "column_name"))
            fk_map[key] = self._cell(row, "ref_table")

        columns_by_table: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for row in column_rows:
            key = (self._cell(row, "table_schema"), self._cell(row, "table_name"))
            columns_by_table.setdefault(key, []).append(row)

        count = 0
        for row in table_rows:
            schema = self._cell(row, "table_schema")
            table = self._cell(row, "table_name")
            table_type = (self._cell(row, "table_type") or "").upper()
            kind = "VIEW" if "VIEW" in table_type else "BASE TABLE"
            raw_columns = columns_by_table.get((schema, table), [])
            columns: List[RedactedDict] = []
            for col in raw_columns:
                name = self._cell(col, "column_name")
                data_type = self._cell(col, "data_type") or "unknown"
                out = RedactedDict({
                    "name": name,
                    "data_type": data_type,
                    "type_family": normalize_type_family(data_type),
                    "is_pk": 1 if (schema, table, name) in pk_set else 0,
                    "fk_target": fk_map.get((schema, table, name)),
                    "comment": self._cell(col, "column_comment") or None,
                })
                columns.append(out)
            dto = DbTableRedacted(
                schema_name=schema,
                table_name=table,
                kind=kind,
                columns=columns,
                row_estimate=_reltuples_to_int(self._cell(row, "row_estimate")),
                comment=None,  # PG 表注释在 pg_description，采集面从列注释即可满足
            )
            self._store.upsert_db_table(dto)
            qualified = "{0}.{1}".format(schema, table)
            self._columns[qualified] = raw_columns
            self._tables.append((schema, table, kind))
            count += 1
        return count

    # ------------------------------------------------------------------
    # 表采样（REQ-SU-012）
    # ------------------------------------------------------------------

    def sample_tables(self, rows: int) -> None:
        """每表 ORDER BY 主键 LIMIT 采样，DataMasker 逐列处理后写 db_samples。

        采样纪律（§2.3.8 / NFR-SU-005）：
          - LIMIT 上限 50（架构口径"≤50"），入参超限自动钳制；
          - 有主键表 ``ORDER BY <pk>`` 稳定取样（渲染幂等 §7.3 前提）；
            无主键表（含 VIEW）不加 ORDER BY——大表全扫代价高，LIMIT 裸取即可，
            采样本是一次性观测不是审计基准；
          - 标识符（表/列名）来自 collect_schema 的采集字面量，先过
            :func:`_quote_identifier` 同款白名单（复用本模块 :data:`_IDENTIFIER_RE`
            逻辑）再按方言引号内插——information_schema 被污染也注入不进来；
          - 每个 cell 先经 ReadOnlyGuard 的逐值 redact（execute 内置），再过
            :class:`DataMasker` 的列名/注释词典 + 值形态双通道（外层脱敏）。

        Args:
            rows: 每表采样行数（cfg.budget.sample_rows 传入；>50 钳到 50，
                ≤0 视为跳过采样）。
        """
        limit = min(int(rows), 50)
        if limit <= 0 or not self._tables:
            return
        self._require_conn()
        engine = self._cfg.engine.strip().lower()
        for schema, table, _kind in self._tables:
            qualified = "{0}.{1}".format(schema, table)
            raw_columns = self._columns.get(qualified, [])
            pk_column = self._pk_column_of(raw_columns, engine)
            try:
                sample_rows = self._guard.execute(
                    self._conn,
                    _build_sample_sql(engine, schema, table, pk_column, limit),
                )
            except Exception:  # noqa: BLE001 - 单表采样失败不拖垮整体（VIEW 无主键等）
                continue
            masked_rows: List[RedactedDict] = []
            for row in sample_rows:
                masked_rows.append(self._mask_row(row, raw_columns, engine))
            if masked_rows:
                self._store.insert_samples(qualified, masked_rows)

    def _mask_row(
        self,
        row: RedactedDict,
        raw_columns: List[Dict[str, Any]],
        engine: str,
    ) -> RedactedDict:
        """单行采样值过 DataMasker（列名/注释来自采集元数据）。

        Args:
            row: ReadOnlyGuard 返回的一行（逐 cell 已 redact）。
            raw_columns: 该表 COLUMNS 原始行（取列名/注释映射）。
            engine: 方言（列键名大小写口径：MySQL 大写列名 / PG 小写）。

        Returns:
            RedactedDict: 脱敏后的采样行（写 db_samples.row_json）。
        """
        # 列元数据索引：结果集列键 → (列名, 注释)
        meta: Dict[str, Tuple[str, Optional[str]]] = {}
        for col in raw_columns:
            name = self._cell(col, "COLUMN_NAME") if engine == "mysql" \
                else self._cell(col, "column_name")
            comment = col.get("COLUMN_COMMENT") if engine == "mysql" \
                else col.get("column_comment")
            meta[name] = (name, str(comment) if comment else None)
        out = RedactedDict()
        for key, value in row.items():
            name, comment = meta.get(str(key), (str(key), None))
            out[str(key)] = self._masker.mask_value(name, comment, value)
        return out

    @staticmethod
    def _pk_column_of(raw_columns: List[Dict[str, Any]], engine: str) -> Optional[str]:
        """取该表首个主键列名（稳定采样 ORDER BY 用；无主键返回 None）。

        Args:
            raw_columns: 该表 COLUMNS 原始行。
            engine: 方言（键名大小写口径）。

        Returns:
            str | None: 主键列名或 None。
        """
        for col in raw_columns:
            if engine == "mysql":
                key_flag = (str(col.get("COLUMN_KEY") or "")).upper()
                if key_flag == "PRI":
                    return str(col.get("COLUMN_NAME") or "") or None
            else:
                # PG 路径主键标注在 collect 阶段已并入 is_pk 语义：
                # _columns 保存的是 information_schema 原始行（无 is_pk），
                # 此处按 information_schema 无主键面 → 保守返回 None
                # （PG 采样不加 ORDER BY；主键事实源在 db_columns.is_pk）
                continue
        return None

    # ------------------------------------------------------------------
    # 隐式外键三重预筛（REQ-SU-013）
    # ------------------------------------------------------------------

    def prescreen_implicit_fks(
        self,
        include_threshold: float = DEFAULT_CONTAINMENT_THRESHOLD,
    ) -> List[ImplicitFkCandidate]:
        """三重预筛隐式 FK 候选（命名 → 类型族 → 包含度，逐规则止步）。

        规则链（REQ-SU-013；先淘汰先止步，止步规则随候选记录落库可解释）：
          1. **命名约定**：子列形如 ``<base>_id``/``<base>_pk``，
             ``singularize(base)`` 与候选父表名单数化后相等，且父表存在
             ``id``/``pk``/``<base>_id`` 同名主键列；父侧列排除自身同名自引用；
          2. **类型族兼容**：两侧 :func:`normalize_type_family` 相等
             （int↔int、string↔string、uuid↔uuid；other 一律不兼容）；
          3. **包含度 ≥ 阈值**（默认 0.85）：委托
             :func:`build_containment_sql` + :class:`ImplicitFkCountExecutor`
             （NOT EXISTS 形态、子侧 ≤1000 去重值）。

        评分：命中规则加权累计（naming 0.4 / type_family 0.2 / containment 0.4，
        :data:`PRESCREEN_WEIGHTS`）。止步 1 → score 0；止步 2 → 0.4；
        止步 3 → 0.6（含 containment 实测值但低于阈值，一并落库供人工复核）；
        三重全过 → 1.0（stopped_at_rule=0）。

        规模控制（遗留大库防御）：仅对**通过规则 1** 的 (子列,父表) 对执行
        计数查询——命名约定是廉价前置，避免 O(列×表) 的全量包含度扫描。

        Args:
            include_threshold: 包含度达标阈值 ∈ (0,1]（默认 0.85）。

        Returns:
            list[ImplicitFkCandidate]: 达标候选（stopped_at_rule=0；
                未达标者也**不落库**——止步记录属于噪声，仅达标者写
                implicit_fk_candidates；返回列表含全部止步详情供报告引用）。
        """
        self._require_conn()
        if not self._tables:
            return []
        engine = self._cfg.engine.strip().lower()
        dialect = "mysql" if engine == "mysql" else "postgresql"

        # 候选父表索引：单数表名 → [(schema.table, 该表主键列名)]
        parent_index: Dict[str, List[Tuple[str, str]]] = {}
        for schema, table, kind in self._tables:
            if kind != "BASE TABLE":
                continue  # VIEW 不作父表（行数估算不可靠、包含度语义弱）
            for key in self._parent_name_keys(table):
                pk_col = self._pk_column_for_prescreen(
                    self._columns.get("{0}.{1}".format(schema, table), []), engine)
                if pk_col:
                    parent_index.setdefault(key, []).append(
                        ("{0}.{1}".format(schema, table), pk_col))

        candidates: List[ImplicitFkCandidate] = []
        for schema, table, kind in self._tables:
            if kind != "BASE TABLE":
                continue
            qualified_child = "{0}.{1}".format(schema, table)
            for col in self._columns.get(qualified_child, []):
                child_col_name, data_type, comment = self._column_facts(col, engine)
                match = _FK_COLUMN_RE.match(child_col_name or "")
                if not match:
                    continue  # 规则 1 入口：非 <base>_id/pk 形态直接跳过（噪声不入库）
                base = match.group("base").lower()
                # 命名候选父表：base 与单数化两形态都查（users 表名未复数化的库）
                parent_candidates: List[Tuple[str, str]] = []
                for key in (base, singularize(base)):
                    parent_candidates.extend(parent_index.get(key, []))
                if not parent_candidates:
                    continue  # 规则 1 止步：命名无可配父表（噪声，不落库）
                child_family = normalize_type_family(data_type or "")
                for parent_ref, parent_pk in parent_candidates:
                    if parent_ref == qualified_child and parent_pk == child_col_name:
                        continue  # 自引用排除（同表同列）
                    parent_cols = self._columns.get(parent_ref, [])
                    parent_type, _ptype, _ppc = self._column_facts_by_name(
                        parent_cols, parent_pk, engine)
                    parent_family = normalize_type_family(parent_type or "")
                    score = PRESCREEN_WEIGHTS["naming"]
                    naming_hit = "{0}→{1}.{2}".format(
                        child_col_name, parent_ref, parent_pk)
                    if (child_family != parent_family
                            or child_family == "other"):
                        # 规则 2 止步：类型族不兼容（含双侧 other）
                        candidates.append(ImplicitFkCandidate(
                            child_table=qualified_child,
                            child_column=child_col_name,
                            parent_table=parent_ref,
                            parent_column=parent_pk,
                            prescreen_score=score,
                            stopped_at_rule=2,
                            containment=None,
                            naming_hit="{0}（类型族 {1}≠{2}）".format(
                                naming_hit, child_family, parent_family),
                        ))
                        continue
                    score += PRESCREEN_WEIGHTS["type_family"]
                    # 规则 3：包含度实测（表名/列名传**裸名**——containment SQL
                    # 生成器按单表名拼方言引号，跨 schema 限定属编排层职责；
                    # 遗留库惯例同 schema，跨 schema 候选在 contain 查询失败时保守跳过）
                    containment = self._measure_containment(
                        dialect, table, child_col_name,
                        parent_ref.partition(".")[2], parent_pk)
                    if containment is None:
                        continue  # 计数查询失败（权限/跨 schema）：静默跳过该对
                    if containment >= include_threshold:
                        candidates.append(ImplicitFkCandidate(
                            child_table=qualified_child,
                            child_column=child_col_name,
                            parent_table=parent_ref,
                            parent_column=parent_pk,
                            prescreen_score=1.0,
                            stopped_at_rule=0,
                            containment=containment,
                            naming_hit=naming_hit,
                        ))
                    else:
                        candidates.append(ImplicitFkCandidate(
                            child_table=qualified_child,
                            child_column=child_col_name,
                            parent_table=parent_ref,
                            parent_column=parent_pk,
                            prescreen_score=score,
                            stopped_at_rule=3,
                            containment=containment,
                            naming_hit="{0}（包含度 {1:.2f} 未达 {2:.2f}）".format(
                                naming_hit, containment, include_threshold),
                        ))

        # 达标候选落库（幂等四元组 UNIQUE）；止步记录只随返回列表供报告引用
        for cand in candidates:
            if cand.stopped_at_rule == 0:
                self._store.insert_implicit_fk(cand.to_redacted())
        return candidates

    def _measure_containment(
        self,
        dialect: str,
        child_table: str,
        child_col: str,
        parent_table: str,
        parent_col: str,
    ) -> Optional[float]:
        """执行包含度计数（规则 3）；标识符非法/查询失败返回 None（保守跳过）。

        Args:
            dialect: 'mysql' / 'postgresql'（containment SQL 引号方言）。
            child_table: 子表裸名。
            child_col: 子列名。
            parent_table: 父表裸名。
            parent_col: 父列名。

        Returns:
            float | None: 包含度 ∈ [0,1]；None=无法测量（跳过该候选对）。
        """
        try:
            return self._executor.containment(
                self._conn, child_table, child_col, parent_table, parent_col,
                limit=CONTAINMENT_SAMPLE_LIMIT, dialect=dialect)
        except Exception:  # noqa: BLE001 - 非法标识符（白名单拒绝）/查询错误统一跳过
            return None

    @staticmethod
    def _parent_name_keys(table_name: str) -> List[str]:
        """表名 → 命名通道匹配键集（原名 + 单数化，小写去重）。

        Args:
            table_name: 表名原文。

        Returns:
            list[str]: 匹配键（如 'users' → ['users','user']）。
        """
        lowered = (table_name or "").lower()
        keys = [lowered]
        singular = singularize(lowered)
        if singular and singular != lowered:
            keys.append(singular)
        return keys

    def _pk_column_for_prescreen(
        self,
        raw_columns: List[Dict[str, Any]],
        engine: str,
    ) -> Optional[str]:
        """父表预筛主键列：PRI/主键优先，退化首个名为 id/pk 的列。

        Args:
            raw_columns: 父表 COLUMNS 原始行。
            engine: 方言。

        Returns:
            str | None: 主键列名或 None。
        """
        fallback: Optional[str] = None
        for col in raw_columns:
            name, _dt, _cm = self._column_facts(col, engine)
            if engine == "mysql":
                if (str(col.get("COLUMN_KEY") or "")).upper() == "PRI":
                    return name
            if (name or "").lower() in ("id", "pk"):
                fallback = fallback or name
        return fallback

    @staticmethod
    def _column_facts(
        col: Dict[str, Any],
        engine: str,
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """列行 → (列名, data_type, 注释)，屏蔽 MySQL/PG 键名大小写差异。

        Args:
            col: COLUMNS 单行。
            engine: 方言。

        Returns:
            tuple: (name, data_type, comment)。
        """
        if engine == "mysql":
            return (col.get("COLUMN_NAME"), col.get("DATA_TYPE"),
                    col.get("COLUMN_COMMENT"))
        return (col.get("column_name"), col.get("data_type"),
                col.get("column_comment"))

    @classmethod
    def _column_facts_by_name(
        cls,
        raw_columns: List[Dict[str, Any]],
        name: str,
        engine: str,
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """按列名在列行列表中定位事实三元组（未找到返回全 None）。

        Args:
            raw_columns: 表的 COLUMNS 行。
            name: 目标列名。
            engine: 方言。

        Returns:
            tuple: (name, data_type, comment)。
        """
        for col in raw_columns:
            col_name, data_type, comment = cls._column_facts(col, engine)
            if col_name == name:
                return col_name, data_type, comment
        return None, None, None

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _require_conn(self) -> None:
        """断言已建连（编排层漏调 connect 时快速失败，绝不静默空采）。

        Raises:
            RuntimeError: 未调用 :meth:`connect`。
        """
        if self._conn is None:
            raise RuntimeError("DbInspector 尚未 connect()——请先建连再采集")

    @staticmethod
    def _cell(row: Dict[str, Any], key: str) -> Optional[str]:
        """取结果 cell 文本（None 透传；非 str 转 str）。

        Args:
            row: ReadOnlyGuard 返回行。
            key: 列键。

        Returns:
            str | None: 文本或 None。
        """
        value = row.get(key)
        if value is None:
            return None
        return str(value)

    @staticmethod
    def _cell_int(row: Dict[str, Any], key: str) -> Optional[int]:
        """取结果 cell 并转 int（NULL/非数字 → None）。

        Args:
            row: 结果行。
            key: 列键。

        Returns:
            int | None: 整数值或 None。
        """
        value = row.get(key)
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


# ---------------------------------------------------------------------------
# DbInspector 模块级辅助（SQL 文本生成，全部经标识符白名单防线）
# ---------------------------------------------------------------------------

def _quote_ident(name: str, engine: str) -> str:
    """标识符白名单校验 + 方言引号封闭（采样 SQL 拼接面防线）。

    与 :func:`_quote_identifier`（containment 通道）同口径：information_schema
    采集所得字面量仍以强白名单二次校验——纵深防御，任何注入字符即抛。

    Args:
        name: 标识符（表/列/名）。
        engine: 'mysql' / 'postgresql'。

    Returns:
        str: 引号封闭的可内插标识符。

    Raises:
        ValueError: 含白名单外字符。
    """
    if not name or not _IDENTIFIER_RE.match(name):
        raise ValueError(
            "采样 SQL 标识符含非法字符：{0!r}（来源必须为 "
            "information_schema 采集字面量）".format(name))
    quote = "`" if engine == "mysql" else '"'
    return "{0}{1}{2}".format(quote, name, quote)


def _build_sample_sql(
    engine: str,
    schema: str,
    table: str,
    pk_column: Optional[str],
    limit: int,
) -> str:
    """采样 SELECT 生成（ORDER BY 主键 + LIMIT；标识符全白名单校验）。

    Args:
        engine: 方言。
        schema: schema/库名。
        table: 表名。
        pk_column: 主键列名（None=不加 ORDER BY）。
        limit: 行数上限（调用方已钳制 ≤50）。

    Returns:
        str: 单条 SELECT（可通过 ReadOnlyGuard.validate）。

    Raises:
        ValueError: 任一标识符非法。
    """
    quoted_table = "{0}.{1}".format(_quote_ident(schema, engine),
                                    _quote_ident(table, engine))
    sql = "SELECT * FROM {0}".format(quoted_table)
    if pk_column:
        sql += " ORDER BY {0}".format(_quote_ident(pk_column, engine))
    sql += " LIMIT {0}".format(int(limit))
    return sql


def _reltuples_to_int(value: Any) -> Optional[int]:
    """pg_class.reltuples → 正整数行数估算（-1 = 未分析 → None）。

    Args:
        value: reltuples 原始值（float/None）。

    Returns:
        int | None: 行数估算；未分析（<0）/NULL → None。
    """
    if value is None:
        return None
    try:
        numeric = int(float(value))
    except (TypeError, ValueError):
        return None
    return numeric if numeric >= 0 else None
