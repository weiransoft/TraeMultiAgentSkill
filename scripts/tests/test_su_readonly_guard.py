# -*- coding: utf-8 -*-
"""SU 能力单元测试：DB 只读边界守卫（REQ-SU-010，红线②）。

覆盖 su.db_guard 模块：
- validate 状态机 S0→S1→S1b→S2→S3：空 SQL / 首关键字 / 多语句 / 未闭合引号
- 注释绕过：词中缝合（SEL/**/ECT 放行、DROP/**/TABLE 必拒）vs 词间补空格
- INTO OUTFILE/DUMPFILE 写盘拦截（含缝合/换行变体，字面量不误杀）
- INTERNAL_STATEMENTS 封闭旁路（逐字面量，其余 SET/BEGIN 拒绝）
- execute 单通道：FakeConn 参数化下发、逐 cell 值脱敏、列名不过 PII
- DbSessionHardener.harden：mysql/pg 验证生效、非法引擎违例、语句清单

运行方式（项目根目录）：
    python3 -B -m unittest scripts.tests.test_su_readonly_guard
"""

import sys
import unittest
from pathlib import Path

# 将 scripts/ 目录注入 sys.path，使 `from su.xxx import ...` 生效
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from su.db_guard import (  # noqa: E402
    ALLOWED_LEADING_KEYWORDS,
    DbSessionHardener,
    INTERNAL_STATEMENTS,
    ReadOnlyGuard,
)
from su.dto import RedactedDict, SuReadonlyViolation  # noqa: E402


class _FakeCursor:
    """DB-API 游标替身：记录下发语句与参数，回放预置结果集。"""

    def __init__(self, results=None, description=None):
        # results: fetchall 回放的行列表；description: 结果集列描述
        self._results = results if results is not None else []
        self.description = description
        self.executed = []          # [(sql, params_or_None), ...]
        self.closed = False

    def execute(self, sql, params=None):
        """记录校验后下发的语句（不是原始 SQL，可断言"校验==执行"）。"""
        self.executed.append((sql, params))

    def fetchall(self):
        return list(self._results)

    def close(self):
        self.closed = True


class _FakeConn:
    """DB-API 连接替身：按语句前缀路由预置结果（模拟服务端行为）。"""

    def __init__(self, responses=None, default_description=(("col",),)):
        # responses: {语句匹配子串: (description, rows)}；未命中用默认
        self._responses = responses or {}
        self._default_description = default_description
        self.cursor_objects = []

    def cursor(self):
        cur = _FakeCursor()
        # 语句执行时再按 SQL 匹配回填结果集（模拟服务端应答）
        original_execute = cur.execute

        def _execute(sql, params=None):
            original_execute(sql, params)
            for needle, (desc, rows) in self._responses.items():
                if needle in sql:
                    cur.description = desc
                    cur._results = rows
                    return
            if self._default_description is not None:
                cur.description = list(self._default_description)
                cur._results = []

        cur.execute = _execute
        self.cursor_objects.append(cur)
        return cur


class TestValidateBasics(unittest.TestCase):
    """REQ-SU-010：validate 基础通过/拒绝口径。"""

    def setUp(self):
        self.guard = ReadOnlyGuard()

    def test_select_passes(self):
        """普通 SELECT 通过并原样返回。"""
        self.assertEqual(
            self.guard.validate("SELECT id FROM t"),
            "SELECT id FROM t",
        )

    def test_show_explain_pass(self):
        """SHOW / EXPLAIN 首关键字同样放行。"""
        self.assertEqual(self.guard.validate("SHOW TABLES"), "SHOW TABLES")
        self.assertEqual(
            self.guard.validate("EXPLAIN SELECT 1"), "EXPLAIN SELECT 1"
        )

    def test_empty_sql_rejected(self):
        """空串 / 空白 / None → 违例。"""
        for bad in ("", "   ", None):
            with self.subTest(sql=bad):
                with self.assertRaises(SuReadonlyViolation):
                    self.guard.validate(bad)

    def test_leading_keyword_blacklist(self):
        """DDL/DML/DCL/TCL 首关键字一律拒绝。

        负例清单与 PRD REQ-SU-010 AC1 点名语句全量对齐（INSERT/UPDATE/
        DELETE/DROP/ALTER/TRUNCATE/GRANT/CALL/多语句注入）——CALL 存储
        过程属白名单（SELECT/SHOW/EXPLAIN）之外的可执行语句，必须拒。
        """
        for bad in (
            "DROP TABLE t",
            "TRUNCATE t",
            "INSERT INTO t VALUES (1)",
            "UPDATE t SET a=1",
            "DELETE FROM t",
            "GRANT ALL ON *.* TO u",
            "CREATE TABLE t (a INT)",
            "ALTER TABLE t ADD b INT",
            "COMMIT",
            # PRD REQ-SU-010 AC1 点名：CALL 存储过程调用（大小写两形态）
            "CALL p()",
            "call drop_all_tables()",
            # PRD REQ-SU-010 AC1 点名：多语句注入（SELECT 开头夹带 DROP）
            "SELECT 1; DROP TABLE x",
            # ROLLBACK 同属 TCL 类首关键字（与 COMMIT 同口径）
            "ROLLBACK",
        ):
            with self.subTest(sql=bad):
                with self.assertRaises(SuReadonlyViolation):
                    self.guard.validate(bad)

    def test_selected_not_mistaken(self):
        """\\b 边界：'SELECTED ...' 不得误判为 SELECT 放行。"""
        with self.assertRaises(SuReadonlyViolation):
            self.guard.validate("SELECTED FROM t")

    def test_trailing_single_semicolon_ok(self):
        """末尾单个分号放行且返回值剥掉分号。"""
        self.assertEqual(
            self.guard.validate("SELECT 1;"), "SELECT 1"
        )

    def test_multi_statement_rejected(self):
        """引号外多分号（多语句）拒绝。"""
        with self.assertRaises(SuReadonlyViolation):
            self.guard.validate("SELECT 1; SELECT 2")

    def test_semicolon_inside_literal_ok(self):
        """引号内分号不算多语句。"""
        self.assertEqual(
            self.guard.validate("SELECT 'a;b' AS x"),
            "SELECT 'a;b' AS x",
        )

    def test_unclosed_quote_rejected(self):
        """未闭合引号拒绝（S2 状态机）。"""
        with self.assertRaises(SuReadonlyViolation):
            self.guard.validate("SELECT 'abc")

    def test_unclosed_block_comment_rejected(self):
        """块注释未闭合拒绝（S0）。"""
        with self.assertRaises(SuReadonlyViolation):
            self.guard.validate("SELECT 1 /* 未闭合")


class TestCommentBypass(unittest.TestCase):
    """REQ-SU-010 AC1：注释绕过封堵（词中缝合 vs 词间补空格）。"""

    def setUp(self):
        self.guard = ReadOnlyGuard()

    def test_mid_word_comment_glued(self):
        """SEL/**/ECT：词中缝合回 SELECT → 放行（防误杀）。"""
        self.assertEqual(
            self.guard.validate("SEL/**/ECT id FROM t"),
            "SELECT id FROM t",
        )

    def test_drop_mid_word_comment_denied(self):
        """DROP/**/TABLE：词中缝合 DROPTABLE 非法 → 必拒。"""
        with self.assertRaises(SuReadonlyViolation):
            self.guard.validate("DROP/**/TABLE t")

    def test_drop_between_word_comment_denied(self):
        """DROP/**/TABLE（两侧空格）：词间补空格仍是 DROP TABLE → 必拒。"""
        with self.assertRaises(SuReadonlyViolation):
            self.guard.validate("DROP /**/ TABLE t")

    def test_line_comment_stripped(self):
        """行注释 -- / # 删到行尾（validate 产物按实现口径逐字符锁定）。"""
        # 有换行：注释文本删除、换行符保留（实现语义：换行由下一轮循环追加）
        self.assertEqual(
            self.guard.validate("SELECT 1 -- 尾注释\n"),
            "SELECT 1 \n",
        )
        self.assertEqual(
            self.guard.validate("SELECT 1 # mysql 注释\n"),
            "SELECT 1 \n",
        )
        # 无换行：-- 之后整段视为注释被吞掉（token 以 break 结束）
        self.assertEqual(
            self.guard.validate("SELECT 1 -- 行尾无换行"),
            "SELECT 1 ",
        )

    def test_comment_inside_literal_kept(self):
        """引号内 '--' 不是注释，原样保留放行。"""
        sql = "SELECT * FROM t WHERE note='-- 不是注释'"
        self.assertEqual(self.guard.validate(sql), sql)

    def test_leading_comment_before_select(self):
        """前置块注释 + SELECT：词间补空格后首关键字仍命中白名单。"""
        self.assertEqual(
            self.guard.validate("/* c */ SELECT 1"),
            "  SELECT 1",
        )


class TestIntoOutfileBlocked(unittest.TestCase):
    """REQ-SU-010：SELECT ... INTO OUTFILE/DUMPFILE 写盘拦截。"""

    def setUp(self):
        self.guard = ReadOnlyGuard()

    def test_into_outfile_denied(self):
        """标准 INTO OUTFILE 'path' 形态拒绝。"""
        with self.assertRaises(SuReadonlyViolation):
            self.guard.validate("SELECT * FROM t INTO OUTFILE '/tmp/x'")

    def test_into_dumpfile_denied(self):
        """DUMPFILE 同样拒绝。"""
        with self.assertRaises(SuReadonlyViolation):
            self.guard.validate("SELECT a FROM t INTO DUMPFILE '/tmp/y'")

    def test_into_outfile_comment_split_denied(self):
        """INTO/**/OUTFILE 块注释分割变体拒绝。"""
        with self.assertRaises(SuReadonlyViolation):
            self.guard.validate("SELECT a FROM t INTO/**/OUTFILE '/tmp/z'")

    def test_into_outfile_newline_split_denied(self):
        """INTO 换行 OUTFILE 拒绝。"""
        with self.assertRaises(SuReadonlyViolation):
            self.guard.validate("SELECT a FROM t INTO\nOUTFILE '/tmp/w'")

    def test_intooutfile_no_space_denied(self):
        """INTOOUTFILE'p' 无空白缝合形态拒绝。"""
        with self.assertRaises(SuReadonlyViolation):
            self.guard.validate("SELECT a FROM t INTOOUTFILE'p'")

    def test_literal_text_not_miskilled(self):
        """字面量 'INTO OUTFILE 手册'（OUTFILE 后是普通文字）不误杀。"""
        sql = "SELECT * FROM docs WHERE note='INTO OUTFILE 手册'"
        self.assertEqual(self.guard.validate(sql), sql)


class TestInternalStatementsBypass(unittest.TestCase):
    """REQ-SU-010：INTERNAL_STATEMENTS 封闭旁路（逐字面量）。"""

    def setUp(self):
        self.guard = ReadOnlyGuard()

    def test_whitelisted_literals_pass(self):
        """三条内部加固字面量放行（压平空白、大小写不敏感、输出压平形态）。"""
        self.assertEqual(
            self.guard.validate("SET SESSION TRANSACTION READ ONLY"),
            "SET SESSION TRANSACTION READ ONLY",
        )
        # 多空格输入命中旁路后输出压平空白的小写原文（旁路返回压平文本本身）
        self.assertEqual(
            self.guard.validate("set  default_transaction_read_only =  on"),
            "set default_transaction_read_only = on",
        )
        self.assertEqual(
            self.guard.validate("BEGIN READ ONLY"), "BEGIN READ ONLY"
        )

    def test_other_set_begin_denied(self):
        """白名单外的任意 SET/BEGIN 仍被拒（旁路面封闭）。"""
        for bad in (
            "SET GLOBAL read_only = off",
            "SET autocommit = 1",
            "BEGIN",
            "COMMIT",
            "ROLLBACK",
        ):
            with self.subTest(sql=bad):
                with self.assertRaises(SuReadonlyViolation):
                    self.guard.validate(bad)

    def test_internal_set_constant(self):
        """旁路常量恰为 3 条封闭字面量（防扩面）。"""
        self.assertEqual(len(INTERNAL_STATEMENTS), 3)
        self.assertIn("BEGIN READ ONLY", INTERNAL_STATEMENTS)


class TestExecuteChannel(unittest.TestCase):
    """REQ-SU-010 AC3：execute 单通道 + 逐值脱敏 + 列名不过 PII。"""

    def setUp(self):
        self.guard = ReadOnlyGuard()

    def test_execute_validates_before_dispatch(self):
        """违规 SQL 绝不触达 cursor.execute。"""
        conn = _FakeConn()
        with self.assertRaises(SuReadonlyViolation):
            self.guard.execute(conn, "DROP TABLE t")
        # 违例在 cursor() 之前抛出：没有任何游标被创建/下发
        self.assertEqual(conn.cursor_objects, [])

    def test_execute_dispatches_validated_text(self):
        """下发文本 == validate 产物（剥注释、去末尾分号逐字符一致）。"""
        conn = _FakeConn()
        self.guard.execute(conn, "SELECT/**/ id FROM t;")
        sql, params = conn.cursor_objects[0].executed[0]
        # SELECT/**/id → 词中缝合 SELECTid？否——'T' 与 'i' 都是标识符字符，
        # 缝合为 SELECTid 非法首关键字会被拒；正确形态是词间注释补空格：
        # "SELECT /**/ id" → "SELECT  id"。此处输入 SELECT/**/ id：左 'T' 右 ' '
        # → 词间补空格 → "SELECT  id FROM t"，末尾分号剥离。
        self.assertEqual(sql, self.guard.validate("SELECT/**/ id FROM t;"))
        self.assertEqual(sql, "SELECT  id FROM t")
        self.assertIsNone(params)

    def test_execute_params_bound(self):
        """参数化传参：params 原样绑给驱动，不拼进 SQL。"""
        conn = _FakeConn()
        self.guard.execute(conn, "SELECT id FROM t WHERE id = %s", (7,))
        sql, params = conn.cursor_objects[0].executed[0]
        self.assertEqual(sql, "SELECT id FROM t WHERE id = %s")
        self.assertEqual(params, (7,))

    def test_execute_rows_redacted_and_columns_kept(self):
        """敏感值逐 cell 脱敏；列名（email 等）原样保留。"""
        conn = _FakeConn(
            responses={
                "SELECT": (
                    (("email",), ("phone",)),
                    [("a@b.com", "13800138000")],
                ),
            },
            default_description=None,
        )
        rows = self.guard.execute(conn, "SELECT email, phone FROM users")
        self.assertIsInstance(rows[0], RedactedDict)
        # 列名保留 schema 元数据语义
        self.assertEqual(set(rows[0].keys()), {"email", "phone"})
        # 值侧：手机号必被脱敏（值形态 PII 替换）
        self.assertNotIn("13800138000", str(rows[0]["phone"]))

    def test_execute_no_resultset_empty(self):
        """无结果集语句（INTERNAL 旁路 SET）返回空列表不报错。"""
        conn = _FakeConn(default_description=None)
        rows = self.guard.execute(conn, "SET SESSION TRANSACTION READ ONLY")
        self.assertEqual(rows, [])

    def test_execute_closes_cursor(self):
        """执行完毕游标必须关闭（资源泄漏防护）。"""
        conn = _FakeConn()
        self.guard.execute(conn, "SELECT 1")
        self.assertTrue(conn.cursor_objects[0].closed)


class TestSessionHardener(unittest.TestCase):
    """REQ-SU-010.1：会话级只读加固（两层防御第 2 层）。"""

    def test_mysql_harden_success(self):
        """MySQL 加固：SET 下发 + @@tx_read_only=1 验证通过。"""
        conn = _FakeConn(
            responses={
                "@@tx_read_only": ((("@@tx_read_only",),), [("1",)]),
            },
            default_description=None,
        )
        applied = DbSessionHardener().harden(conn, "mysql")
        self.assertIn("SET SESSION TRANSACTION READ ONLY", applied)
        self.assertIn("SELECT @@tx_read_only", applied)
        dispatched = [s for s, _ in conn.cursor_objects[0].executed]
        self.assertEqual(dispatched[0], "SET SESSION TRANSACTION READ ONLY")

    def test_mysql_harden_not_effective_denied(self):
        """@@tx_read_only 回读非只读值 → 违例（不许假装成功）。"""
        conn = _FakeConn(
            responses={
                "@@tx_read_only": ((("@@tx_read_only",),), [("0",)]),
            },
            default_description=None,
        )
        with self.assertRaises(SuReadonlyViolation):
            DbSessionHardener().harden(conn, "mysql")

    def test_pg_harden_success(self):
        """PG 加固：SET + BEGIN READ ONLY + SHOW 验证 on。"""
        conn = _FakeConn(
            responses={
                "SHOW default_transaction_read_only": (
                    (("default_transaction_read_only",),),
                    [("on",)],
                ),
            },
            default_description=None,
        )
        applied = DbSessionHardener().harden(conn, "postgresql")
        self.assertEqual(
            applied,
            [
                "SET default_transaction_read_only = on",
                "BEGIN READ ONLY",
                "SHOW default_transaction_read_only",
            ],
        )

    def test_pg_harden_not_effective_denied(self):
        """PG 回读 off → 违例。"""
        conn = _FakeConn(
            responses={
                "SHOW default_transaction_read_only": (
                    (("default_transaction_read_only",),),
                    [("off",)],
                ),
            },
            default_description=None,
        )
        with self.assertRaises(SuReadonlyViolation):
            DbSessionHardener().harden(conn, "postgresql")

    def test_unknown_engine_denied(self):
        """非法引擎（如 sqlite/oracle）直接违例。"""
        for engine in ("sqlite", "oracle", "", None):
            with self.subTest(engine=engine):
                with self.assertRaises(SuReadonlyViolation):
                    DbSessionHardener().harden(_FakeConn(), engine)


class TestViolationMessageSafety(unittest.TestCase):
    """红线①：违规报错摘要必须脱敏截断，不随报错泄露凭据。"""

    def test_error_message_redacts_pii_values(self):
        """含 PII 值形态（手机号）的违规 SQL，报错摘要被替换。"""
        guard = ReadOnlyGuard()
        with self.assertRaises(SuReadonlyViolation) as ctx:
            guard.validate("DROP TABLE t WHERE owner='13800138000'")
        msg = str(ctx.exception)
        self.assertNotIn("13800138000", msg)
        self.assertIn("<REDACTED:phone>", msg)

    def test_allowed_keyword_constant(self):
        """首关键字白名单恰为 SELECT/SHOW/EXPLAIN。"""
        self.assertEqual(ALLOWED_LEADING_KEYWORDS, ("SELECT", "SHOW", "EXPLAIN"))


if __name__ == "__main__":
    unittest.main()
