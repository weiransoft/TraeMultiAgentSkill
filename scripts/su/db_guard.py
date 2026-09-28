"""DB 只读边界守卫（红线②）——架构 ARCH-SU-001 §2.3.8 / §5.3，PRD REQ-SU-010。

**本模块的 :meth:`ReadOnlyGuard.execute` 是整个 ``su/`` 包唯一 ``cursor.execute``
所在地**（§5.1 静态审查项：CI 检查 ``su/`` 包内 ``cursor.execute`` 仅出现于此处）。
一切内省、采样、包含度计数、加固语句都必须经本通道下发，不存在绕过校验器的
第二条执行路径（REQ-SU-010 AC3）。

**拆分说明（相对架构文件清单的合理微调）**：架构把 ReadOnlyGuard /
DbSessionHardener 列在 ``db_inspector.py``。本文件先落这两个安全类，
db_inspector（后续交付）以 ``from su.db_guard import ReadOnlyGuard, DbSessionHardener``
复用。理由：① 守卫是纯状态机 + 单通道执行器，可在无 DB 容器环境完成 60+ 语句
正负例单测（§11 ``test_su_readonly_guard``）；② preflight（采集层更早交付）要用
守卫做 ``SELECT 1`` 握手预检（§2.3.5），放在 db_inspector 会形成反向依赖。

两层防御（§5.3 附加层）：
  1. 客户端语句状态机 :meth:`ReadOnlyGuard.validate`——DDL/DML/DCL/TCL、多语句、
     注释绕过、未闭合引号全部在**发出前**拒绝；
  2. 服务端会话只读 :meth:`DbSessionHardener.harden`——即便校验器被绕过，
     服务端也拒绝写。
"""

import re
from typing import Any, List, Optional, Sequence, Tuple

from su.config import redact, scrub_text
from su.dto import RedactedDict, SuReadonlyViolation

__all__ = [
    "ALLOWED_LEADING_KEYWORDS",
    "INTERNAL_STATEMENTS",
    "ReadOnlyGuard",
    "DbSessionHardener",
]

# 首关键字白名单（§5.3 S1）：仅这三类开头视为只读语句
ALLOWED_LEADING_KEYWORDS = ("SELECT", "SHOW", "EXPLAIN")

# S1 首关键字正则：允许前置空白/换行；\b 保证 'SELECTED' 不被误判为 SELECT
_FIRST_KEYWORD_RE = re.compile(r"^\s*(SELECT|SHOW|EXPLAIN)\b", re.IGNORECASE)

# SELECT ... INTO 子句黑名单（2026-09-28 自测发现的**真实写盘漏洞**）：
# MySQL 的 ``SELECT ... INTO OUTFILE / DUMPFILE`` 会把查询结果**写到服务端磁盘**，
# 首关键字虽是 SELECT，实际却产生文件写入——首关键字白名单必须叠加子句级检查。
# 扫描面 = **原文 + S0 后的 token 形态**（2026-09-28 自测定稿），两个正则分支：
#   - ``INTO\s+OUTFILE/DUMPFILE``：原文分支覆盖标准形态与行注释/换行分割变体
#     （``INTO --c\nOUTFILE`` 在原文里仍隔空白）；
#   - ``INTOOUTFILE`` 缝合词：token 形态分支覆盖块注释分割变体
#     （``INTO/**/OUTFILE`` 词间注释补空格 → INTO OUTFILE；``INTO/**/O…`` 词中
#     缝合 → INTOOUTFILE）与无空白形态（``INTOOUTFILE'p'``）。
# 误杀防护：字面量 ``WHERE note='INTO OUTFILE 手册'`` 只命中"原文空白分支"？
# ——会误杀！因此原文分支要求 OUTFILE/DUMPFILE 后紧跟**空白+引号**或**引号**
# （INTO 子句的文件名必须是字符串字面量，紧跟 ``'``）；字面量形态里 OUTFILE
# 后面跟的是普通文字（" 手册'"），不满足"引号紧随"，天然不误杀。
_INTO_CLAUSE_RE = re.compile(
    r"(?i)(?:\bINTO\s+OUTFILE\s*['\"`]"
    r"|\bINTO\s+DUMPFILE\s*['\"`]"
    r"|\bINTOOUTFILE\s*['\"`]"
    r"|\bINTODUMPFILE\s*['\"`])"
)

# 语句摘要截断长度（进违规 message 前先 scrub，防凭据/PII 随报错泄露）
_SUMMARY_MAX_LEN = 160

# ---------------------------------------------------------------------------
# 内部固定语句旁路白名单（§2.3.8 审查口径）
# ---------------------------------------------------------------------------
# SET / BEGIN / START 属 TCL，**不在** SELECT/SHOW/EXPLAIN 只读白名单内；
# 但 DbSessionHardener 必须下发它们才能完成"会话只读加固"（架构明确要求这两条
# 仍走 execute() 以维持单一执行通道）。折中方案 = **封闭字面量白名单**：
# 只有下列逐字面量可经 execute() 通道下发，其余一律走只读校验器。
# 这样既满足"单一执行通道 + 加固不被自家校验器拒死"，又不放开任意 TCL 面。
INTERNAL_STATEMENTS = frozenset({
    "SET SESSION TRANSACTION READ ONLY",          # MySQL 会话级只读
    "SET default_transaction_read_only = on",     # PG 默认事务只读
    "BEGIN READ ONLY",                            # PG 显式只读事务
})


# ---------------------------------------------------------------------------
# 语句摘要（进报错文本前必过 scrub_text，红线①）
# ---------------------------------------------------------------------------

def _summary(sql: str) -> str:
    """生成**已脱敏并截断**的语句摘要。

    违规语句可能内嵌凭据（如 ``SELECT * FROM t WHERE pwd='p@ss'``），摘要是给人
    看的报错片段，必须先压平空白、过 PII 值形态替换，再截断到固定长度。

    Args:
        sql: 原始 SQL。

    Returns:
        str: 压平空白 + 脱敏 + 截断后的摘要文本。
    """
    flattened = " ".join((sql or "").split())
    cleaned = scrub_text(flattened)
    if len(cleaned) > _SUMMARY_MAX_LEN:
        cleaned = cleaned[:_SUMMARY_MAX_LEN]
    return cleaned


# ---------------------------------------------------------------------------
# S0 注释剥离（引号感知；防 SEL/**/ECT 之类"注释分割关键字"绕过）
# ---------------------------------------------------------------------------

def _strip_comments(sql: str) -> Tuple[str, str]:
    """剥离 SQL 注释（S0），产出 **token 形态** 与 **缝合形态** 两个版本。

    处理三类注释（§5.3 S0 / REQ-SU-010 AC1）：
      - ``/* ... */`` 块注释；
      - ``--`` 行注释（标准 SQL，须连续两个连字符）；
      - ``#`` 行注释（MySQL 特有）。
    **引号内绝不剥离**：``WHERE note = '-- 不是注释'`` 必须原样保留，
    否则合法只读语句的语义会被破坏（误杀 = 不可用）。

    **§5.3 S0"剥离后重新检查"的实现（2026-09-28 自测定稿）**——块注释分两种语境：
      - **词中注释**（左右都是标识符字符）：缝合两个半截词。
        ``SEL/**/ECT`` → ``SELECT``（token 与缝合形态都命中白名单 → 放行，防误杀）；
        ``DROP/**/TABLE`` → ``DROPTABLE`` 不是合法关键字 → 必拒（防绕过）。
      - **词间注释**（任一侧非标识符字符）：替换为单个空格，维持 token 边界。
        ``DROP/**/TABLE``（两侧空格）→ 仍是 ``DROP TABLE``，**绝不会被误缝成
        合法词**——这正是上一版无条件缝合引入的绕过面（自测发现并已封堵）。

    返回两个版本（同一趟扫描产出，口径严格同源）：
      - ``token``：词中缝合 + 词间补空格 —— S2/S3 的分号扫描与最终下发文本都用它，
        保证"校验的文本 == 执行的文本"；
      - ``glued``：在 token 基础上再去掉**引号外**全部空白 —— 供 S1b 的
        ``INTO/**/OUTFILE`` 这类子句级分割变体归一匹配。

    Args:
        sql: 原始 SQL 文本。

    Returns:
        tuple[str, str]: ``(token 形态, 缝合形态)``。

    Raises:
        SuReadonlyViolation: 块注释未闭合 / 引号未闭合。
    """
    token: List[str] = []      # 词中缝合 / 词间补空格
    glued: List[str] = []      # 引号外空白全丢
    i = 0
    length = len(sql)
    # 引号态：None / "'"（单引号）/ '"'（双引号）/ '`'（反引号标识符）
    quote_char: Optional[str] = None

    def emit(ch: str) -> None:
        """输出一字符：token 形态原样；缝合形态丢弃引号外空白。"""
        token.append(ch)
        if quote_char is not None or not ch.isspace():
            glued.append(ch)

    while i < length:
        ch = sql[i]

        if quote_char is not None:
            # 引号内：原样保留，只找配对结束引号（''/""/`` 双写视为转义内容）
            emit(ch)
            if ch == quote_char:
                if i + 1 < length and sql[i + 1] == quote_char:
                    emit(sql[i + 1])         # 双写转义：两个引号一并保留
                    i += 2
                    continue
                quote_char = None
            i += 1
            continue

        if ch in ("'", '"', "`"):
            quote_char = ch
            emit(ch)
            i += 1
            continue

        # 块注释 /* ... */
        if ch == "/" and i + 1 < length and sql[i + 1] == "*":
            end = sql.find("*/", i + 2)
            if end == -1:
                raise SuReadonlyViolation(
                    "SQL 块注释未闭合（缺少 */），拒绝执行：{0}".format(_summary(sql)),
                    hints=["请提交完整、单条、无残缺注释的只读语句"],
                )
            # 词中注释缝合、词间注释补空格（判定见 docstring 语境说明）
            left = sql[i - 1] if i > 0 else ""
            right = sql[end + 2] if end + 2 < length else ""
            in_word = (left.isalnum() or left == "_") and (right.isalnum() or right == "_")
            if not in_word:
                # 补空格：token 形态记录空格；缝合形态丢弃（空白不进取词文本，
                # 但引号内的空白由 emit 的 quote 分支保留）
                emit(" ")
            i = end + 2
            continue

        # 行注释 -- （标准 SQL）：删到行尾，保留换行符维持行结构
        if ch == "-" and i + 1 < length and sql[i + 1] == "-":
            newline = sql.find("\n", i)
            if newline == -1:
                break          # 注释直到语句末尾
            i = newline        # 换行符由下一轮循环追加
            continue

        # 行注释 # （MySQL）
        if ch == "#":
            newline = sql.find("\n", i)
            if newline == -1:
                break
            i = newline
            continue

        emit(ch)
        i += 1

    if quote_char is not None:
        raise SuReadonlyViolation(
            "SQL 引号未闭合（{0}），拒绝执行：{1}".format(quote_char, _summary(sql)),
            hints=["请检查单引号 / 双引号 / 反引号的配对"],
        )
    return "".join(token), "".join(glued)


# ---------------------------------------------------------------------------
# S2 逐字符引号态扫描（引号感知统计分号）
# ---------------------------------------------------------------------------

def _count_toplevel_semicolons(sql: str) -> int:
    """引号感知统计**引号外**分号个数（§5.3 S2/S3）。

    状态机：
      - NORMAL：遇 ``'`` / ``"`` / ``` ` ``` → 进对应引号态；遇 ``;`` → 计数 +1；
      - QUOTED：遇配对引号回 NORMAL（``''`` 双写转义正确跳过，不视为闭合）；
      - 扫描结束仍在 QUOTED → 未闭合引号 → 拒绝。

    Args:
        sql: **已剥离注释**的 SQL。

    Returns:
        int: 引号外分号数量（调用方据此判定多语句）。

    Raises:
        SuReadonlyViolation: 引号未闭合。
    """
    quote_char: Optional[str] = None
    semicolons = 0
    i = 0
    length = len(sql)
    while i < length:
        ch = sql[i]
        if quote_char is not None:
            if ch == quote_char:
                # 双写转义（'' / "" / ``）：第二个引号是内容而非闭合符
                if i + 1 < length and sql[i + 1] == quote_char:
                    i += 2
                    continue
                quote_char = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote_char = ch
            i += 1
            continue
        if ch == ";":
            semicolons += 1
        i += 1

    if quote_char is not None:
        raise SuReadonlyViolation(
            "SQL 引号未闭合（{0}），拒绝执行".format(quote_char),
            hints=["请检查单引号 / 双引号 / 反引号的配对"],
        )
    return semicolons


def _glue_outside_quotes(sql: str) -> str:
    """生成"**引号外**去掉全部空白"的缝合形态（引号内文本原样保留）。

    用途：为 S1 的注释分割复检与 S1b 的 INTO 子句判定提供统一归一形态——
    ``INTO/**/OUTFILE``、``INTO\\nOUTFILE``、``INTO OUTFILE`` 都会归一为
    ``INTOOUTFILE``，而 ``WHERE note='INTO OUTFILE'`` 这类字面量因在引号内
    而完全不受影响（避免误杀合法只读语句）。

    未闭合引号在此不重复报错——S2 状态机已负责，这里只需保证不越界。

    Args:
        sql: 已剥离注释的 SQL。

    Returns:
        str: 引号外无空白的缝合文本。
    """
    out: List[str] = []
    quote_char: Optional[str] = None
    for ch in sql:
        if quote_char is not None:
            out.append(ch)                     # 引号内：原样保留（含空白）
            if ch == quote_char:
                quote_char = None
            continue
        if ch in ("'", '"', "`"):
            quote_char = ch
            out.append(ch)
            continue
        if ch.isspace():
            continue                           # 引号外空白一律丢弃
        out.append(ch)
    return "".join(out)


class ReadOnlyGuard:
    """SQL 只读白名单校验器 + 全包唯一语句执行通道（红线②）。

    典型用法（db_inspector / preflight 的唯一入口）::

        guard = ReadOnlyGuard()
        rows = guard.execute(conn, "SELECT id FROM t WHERE id = %s", (7,))

    安全语义：
      - :meth:`validate` 拒绝一切非 SELECT/SHOW/EXPLAIN、多语句、注释绕过、
        未闭合引号——违规抛 :class:`SuReadonlyViolation`（中文，摘要已脱敏）；
      - :meth:`execute` 是全包唯一 ``cursor.execute`` 调用点，**参数化传参**，
        严禁字符串拼接构造 SQL（§5.3 S4）；
      - 返回值逐 **cell 值** 过 :func:`config.redact`。
    """

    # ---- 校验 ----

    def validate(self, sql: str) -> str:
        """校验单条只读语句（S0→S1→S2→S3），通过返回可下发文本。

        返回值是"剥离注释 + 去掉末尾单个分号"后的 SQL——execute 下发的就是这个
        返回值，保证 **校验的文本 == 执行的文本**，不存在两版本漂移绕过。

        Args:
            sql: 待执行 SQL（可含注释、可有末尾分号）。

        Returns:
            str: 可下发的规范化 SQL。

        Raises:
            SuReadonlyViolation: 空语句 / 首关键字非法 / 引号外分号（多语句）/
                未闭合引号 / 块注释未闭合。
        """
        if sql is None or not str(sql).strip():
            raise SuReadonlyViolation(
                "空 SQL 语句，拒绝执行",
                hints=["请提交 SELECT / SHOW / EXPLAIN 开头的只读语句"],
            )
        original = str(sql)

        # ---- 内部固定语句旁路（§2.3.8）：仅**逐字面量**命中 INTERNAL_STATEMENTS
        # 才放行。判定用"压平空白 + 大小写不敏感"的归一形态，防止加固语句因
        # 多空格/换行而绕过旁路被误拒；除此以外的任何 SET/BEGIN/COMMIT 等 TCL
        # 仍落入下方只读白名单而被拒绝——旁路面是封闭的，不放开任意 TCL。
        normalized_literal = " ".join(original.split()).strip().lower()
        for literal in INTERNAL_STATEMENTS:
            if normalized_literal == literal.lower():
                return " ".join(original.split()).strip()

        # ---- S0 剥注释（引号感知；词中注释缝合、词间注释补空格，一次产出两形态）----
        stripped, glued = _strip_comments(original)

        # ---- S1 首关键字白名单（剥离注释后再判，杜绝 SEL/**/ECT 注释分割绕过）----
        if not _FIRST_KEYWORD_RE.match(stripped):
            raise SuReadonlyViolation(
                "语句首关键字不在只读白名单 {0} 内，拒绝执行：{1}".format(
                    "/".join(ALLOWED_LEADING_KEYWORDS), _summary(original)
                ),
                hints=[
                    "本能力对目标库严格只读：仅允许 SELECT / SHOW / EXPLAIN",
                    "DDL/DML/DCL/TCL（DROP/TRUNCATE/INSERT/UPDATE/DELETE/GRANT 等）一律拒绝",
                ],
            )

        # ---- S1b SELECT ... INTO OUTFILE/DUMPFILE 子句拦截（写盘漏洞）----
        # 扫描面 = 原文（覆盖 INTO OUTFILE 'path' 标准形态与行注释分割）
        #        + token 形态（覆盖 INTO/**/OUTFILE 词间/词中缝合与无空白变体）
        into_hit = _INTO_CLAUSE_RE.search(original) or _INTO_CLAUSE_RE.search(stripped)
        if into_hit is not None:
            raise SuReadonlyViolation(
                "SELECT ... INTO OUTFILE/DUMPFILE 会把查询结果写入服务端磁盘，"
                "属写操作，拒绝执行：{0}".format(_summary(original)),
                hints=["只读内省不得导出文件；如需数据请走采样通道（LIMIT + 逐值脱敏）"],
            )

        # ---- S2 逐字符引号态扫描（顺带检测未闭合引号）----
        semicolons = _count_toplevel_semicolons(stripped)
        if semicolons == 0:
            return stripped

        # ---- S3 引号外存在分号：只放行"末尾单个分号"，其余判多语句 ----
        rstripped = stripped.rstrip()
        if rstripped.endswith(";"):
            # 去掉末尾分号后必须不再有任何引号外分号，才是真单语句
            if _count_toplevel_semicolons(rstripped[:-1]) == 0:
                return rstripped[:-1]
        raise SuReadonlyViolation(
            "检测到多语句（引号外存在分号），拒绝执行：{0}".format(_summary(original)),
            hints=["每次执行只允许一条语句；请拆分后分别执行"],
        )

    # ---- 执行（全包唯一 cursor.execute 所在地）----

    def execute(
        self,
        conn: Any,
        sql: str,
        params: Sequence[Any] = (),
    ) -> List[RedactedDict]:
        """校验并执行 SQL，返回**逐值脱敏**的行列表。

        流程（§5.3 S4 + 审查 D-2/B-6 修订）：
          1. :meth:`validate` 前置校验（内部固定加固语句走封闭字面量旁路）；
          2. ``cursor.execute(sql, params)`` **参数化**下发，禁止字符串拼接；
          3. 全行取回，逐 **cell 值** 过 :func:`config.redact`；
          4. **列名不过 PII 正则**（审查 D-2/B-6：列名如 ``email`` /
             ``id_card_no`` 是 schema 元数据、本身非敏感值，过正则会把整个
             数据模型抹成 ***REDACTED*** 而使内省完全失效；敏感列的值侧
             由 db_inspector.DataMasker 按列名词典 + 值形态再兜一层）。

        Args:
            conn: DB-API 2.0 连接对象（pymysql / psycopg2）。
            sql: 待执行 SQL。
            params: 参数化绑定值序列（默认空）。

        Returns:
            list[RedactedDict]: 每行一个 RedactedDict（键=列名，值=已脱敏）。

        Raises:
            SuReadonlyViolation: 校验未通过。
            Exception: 驱动层执行错误原样上抛（不吞异常，避免"假成功"）。
        """
        statement = self.validate(sql)

        cursor = conn.cursor()
        try:
            # 全 su/ 包唯一的 cursor.execute：参数化通道，SQL 文本不含外部输入拼接
            if params:
                cursor.execute(statement, tuple(params))
            else:
                cursor.execute(statement)
            rows = self._fetch_all(cursor)
            columns = self._column_names(cursor)
        finally:
            cursor.close()

        return [self._mask_row(columns, row) for row in rows]

    # ---- 内部辅助 ----

    @staticmethod
    def _fetch_all(cursor: Any) -> List[Any]:
        """取回全部结果行（无结果集的语句返回空列表）。

        ``SET`` / ``BEGIN`` 类语句的 cursor 可能不实现 ``description``/``fetchall``
        （依驱动而异），此处容错为空列表，不因此报错。
        """
        if getattr(cursor, "description", None) is None:
            return []
        fetchall = getattr(cursor, "fetchall", None)
        if fetchall is None:
            return []
        rows = fetchall()
        return list(rows) if rows else []

    @staticmethod
    def _column_names(cursor: Any) -> List[str]:
        """从 cursor.description 取列名列表（无结果集时为空列表）。"""
        description = getattr(cursor, "description", None)
        if not description:
            return []
        names: List[str] = []
        for index, column in enumerate(description):
            # DB-API 约定 description 项的第 0 位是列名；缺失时以序号兜底
            name = column[0] if column and len(column) > 0 else None
            names.append(str(name) if name is not None else "column_{0}".format(index))
        return names

    @staticmethod
    def _mask_row(columns: List[str], row: Any) -> RedactedDict:
        """单行脱敏：列名原样保留，值逐个过 redact（仅值脱敏口径）。"""
        # 列名数量与实际值数量不一致时（驱动差异）以值的序号补名，绝不丢值
        values = list(row) if isinstance(row, (tuple, list)) else [row]
        masked: RedactedDict = RedactedDict()
        for index, value in enumerate(values):
            if index < len(columns):
                key = columns[index]
            else:
                key = "column_{0}".format(index)
            # redact 对 str 做 PII 值形态替换 + URL 凭据剥离 + 截断，标量原样返回
            masked[key] = redact(value)
        return masked


class DbSessionHardener:
    """会话级只读加固（§5.3 附加层 / REQ-SU-010.1 的纵深防御）。

    连接建立后立即调用：把会话/事务钉在服务端只读模式——即便客户端语句校验器
    被任何形式的 cleverness 绕过，服务端也会拒绝写操作（双保险）。
    """

    def harden(
        self,
        conn: Any,
        engine: str,
        guard: Optional[ReadOnlyGuard] = None,
    ) -> List[str]:
        """按引擎执行会话只读加固并**验证生效**。

        MySQL（REQ-SU-010.1）：
          ``SET SESSION TRANSACTION READ ONLY`` → 验证 ``SELECT @@tx_read_only`` == 1
        PostgreSQL：
          ``SET default_transaction_read_only = on`` + ``BEGIN READ ONLY``
          → 验证 ``SHOW default_transaction_read_only`` == ``on``

        加固语句是**内部固定字面量**（零外部输入），依架构 §2.3.8 要求仍经
        :meth:`ReadOnlyGuard.execute` 下发以维持"单一执行通道"；它们命中
        :data:`INTERNAL_STATEMENTS` 封闭旁路，故不被只读白名单误拒（SET/BEGIN
        属 TCL，本就不在 SELECT/SHOW/EXPLAIN 集合内）。

        Args:
            conn: DB-API 2.0 连接对象。
            engine: ``'mysql'`` / ``'postgresql'``（DatabaseConfig.engine 口径）。
            guard: 复用外部 ReadOnlyGuard（默认内部新建；二者无状态，等效）。

        Returns:
            list[str]: 已下发的语句清单（供 preflight 记录"加固已生效"证据链）。

        Raises:
            SuReadonlyViolation: 引擎非法 / 加固未生效（验证值不符）。
        """
        active_guard = guard if guard is not None else ReadOnlyGuard()
        engine_key = (engine or "").strip().lower()

        if engine_key == "mysql":
            applied = ["SET SESSION TRANSACTION READ ONLY"]
            active_guard.execute(conn, applied[0])
            # 验证生效：读回服务端状态，绝不允许"假装加固成功"
            probe = active_guard.execute(conn, "SELECT @@tx_read_only")
            value = _first_cell(probe)
            if str(value).strip().lower() not in ("1", "true", "on"):
                raise SuReadonlyViolation(
                    "MySQL 会话只读加固未生效（@@tx_read_only={0!r}）".format(value),
                    hints=["请确认连接账号未被服务端/代理层策略覆盖只读设置"],
                )
            return applied + ["SELECT @@tx_read_only"]

        if engine_key == "postgresql":
            # PG 的 SET 作用于后续事务，因此还需显式开启一个 READ ONLY 事务
            applied = [
                "SET default_transaction_read_only = on",
                "BEGIN READ ONLY",
            ]
            for statement in applied:
                active_guard.execute(conn, statement)
            probe = active_guard.execute(conn, "SHOW default_transaction_read_only")
            value = _first_cell(probe)
            if str(value).strip().lower() not in ("on", "true", "1"):
                raise SuReadonlyViolation(
                    "PostgreSQL 会话只读加固未生效"
                    "（default_transaction_read_only={0!r}）".format(value),
                    hints=["请确认连接账号未被角色级 GUC 覆盖该参数"],
                )
            return applied + ["SHOW default_transaction_read_only"]

        raise SuReadonlyViolation(
            "不支持的数据库引擎，无法执行只读加固：{0!r}".format(engine),
            hints=["engine 仅支持 'mysql' / 'postgresql'"],
        )


def _first_cell(rows: List[RedactedDict]) -> Any:
    """取结果集首行首列值（验证类查询 ``SELECT @@tx_read_only`` 的通用取值）。

    Args:
        rows: :meth:`ReadOnlyGuard.execute` 的返回值。

    Returns:
        Any: 首行第一个值；结果集为空时返回 None。
    """
    if not rows:
        return None
    for value in rows[0].values():
        return value
    return None
