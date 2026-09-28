# -*- coding: utf-8 -*-
"""SU 能力单元测试：隐式外键预筛与采样脱敏（REQ-SU-012/013）。

覆盖 su.db_inspector 模块的纯函数与脱敏器：
- build_containment_sql：NOT EXISTS 形态（禁 NOT IN）、标识符白名单、limit 归一、
  方言引号、产物必过 ReadOnlyGuard.validate
- containment_from_counts：total<=0 → 0.0（绝不 1.0）、missing>total → 0.0
- normalize_type_family：类型族归一（int/string/uuid/other）
- DataMasker.mask_value：值形态优先 / 列名词典 / BLOB / 已脱敏壳识别
- singularize：单复数还原（不规则不动）
- ImplicitFkCandidate.to_redacted：证据 JSON 带"推断需确认"标注
- DbInspector 构造防线：驱动 None → ValueError；未 connect 采集 → RuntimeError

运行方式（项目根目录）：
    python3 -B -m unittest scripts.tests.test_su_implicit_fk
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

# 将 scripts/ 目录注入 sys.path，使 `from su.xxx import ...` 生效
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from su.db_guard import ReadOnlyGuard  # noqa: E402
from su.db_inspector import (  # noqa: E402
    CONTAINMENT_SAMPLE_LIMIT,
    DEFAULT_CONTAINMENT_THRESHOLD,
    PRESCREEN_WEIGHTS,
    DataMasker,
    DbInspector,
    ImplicitFkCandidate,
    build_containment_sql,
    containment_from_counts,
    normalize_type_family,
    singularize,
)


class TestBuildContainmentSql(unittest.TestCase):
    """REQ-SU-013 规则 3：包含度 SQL 生成器（NOT EXISTS 硬口径）。"""

    def test_two_statements_not_exists_form(self):
        """产物两条语句；未命中数 SQL 必为 NOT EXISTS 形态、绝无 NOT IN。"""
        total_sql, missing_sql = build_containment_sql(
            "order_items", "order_id", "orders", "id")
        self.assertIn("SELECT COUNT(*)", total_sql)
        self.assertIn("NOT EXISTS", missing_sql)
        self.assertNotIn("NOT IN", missing_sql)
        self.assertNotIn("NOT IN", total_sql)

    def test_both_statements_pass_readonly_guard(self):
        """两条产物必须能通过 ReadOnlyGuard.validate（单语句、SELECT 开头）。"""
        guard = ReadOnlyGuard()
        total_sql, missing_sql = build_containment_sql(
            "order_items", "order_id", "orders", "id")
        self.assertEqual(guard.validate(total_sql), total_sql)
        self.assertEqual(guard.validate(missing_sql), missing_sql)

    def test_sample_uses_distinct_is_not_null_limit(self):
        """取样口径：DISTINCT + IS NOT NULL + LIMIT 去重非空值。"""
        _, missing_sql = build_containment_sql("a", "b", "c", "d", limit=500)
        self.assertIn("DISTINCT", missing_sql)
        self.assertIn("IS NOT NULL", missing_sql)
        self.assertIn("LIMIT 500", missing_sql)

    def test_mysql_backtick_pg_double_quote(self):
        """方言引号：mysql 反引号 / postgresql 双引号。"""
        total_sql, _ = build_containment_sql("t", "c", "p", "id", dialect="mysql")
        self.assertIn("`t`", total_sql)
        total_pg, _ = build_containment_sql(
            "t", "c", "p", "id", dialect="postgresql")
        self.assertIn('"t"', total_pg)
        self.assertNotIn("`", total_pg)

    def test_limit_normalization(self):
        """limit 非正 / 非 int / None → 1000；数字字符串按 int() 转换。"""
        for bad in (0, -5, "abc", None):
            total_sql, _ = build_containment_sql("t", "c", "p", "id", limit=bad)
            with self.subTest(limit=bad):
                self.assertIn("LIMIT 1000", total_sql)
        # bool True → int(True)=1（实现未特殊处理 bool，锁定事实口径）
        total_sql, _ = build_containment_sql("t", "c", "p", "id", limit=True)
        self.assertIn("LIMIT 1", total_sql)
        # 数字字符串正常转换
        total_sql, _ = build_containment_sql("t", "c", "p", "id", limit="250")
        self.assertIn("LIMIT 250", total_sql)

    def test_identifier_injection_rejected(self):
        """标识符白名单：注入字符（反引号/分号/空格/注释符）抛 ValueError。"""
        injections = [
            "t`; DROP TABLE u; --",
            "t` OR `1`=`1",
            "bad name",
            "1starts_digit",
            "",
            None,
        ]
        for bad in injections:
            with self.subTest(identifier=bad):
                with self.assertRaises(ValueError):
                    build_containment_sql(bad, "c", "p", "id")
                with self.assertRaises(ValueError):
                    build_containment_sql("t", "c", "p", bad)

    def test_dollar_sign_identifier_allowed(self):
        """白名单允许 $（MySQL 合法标识符字符）。"""
        total_sql, _ = build_containment_sql("t$1", "c$2", "p_3", "id")
        self.assertIn("`t$1`", total_sql)


class TestContainmentFromCounts(unittest.TestCase):
    """REQ-SU-013 规则 3：包含度纯函数边界口径。"""

    def test_normal_ratio(self):
        """正常域：(total-missing)/total。"""
        self.assertAlmostEqual(containment_from_counts(10, 2), 0.8)
        self.assertAlmostEqual(containment_from_counts(10, 0), 1.0)
        self.assertAlmostEqual(containment_from_counts(10, 10), 0.0)

    def test_zero_total_is_zero_not_one(self):
        """total<=0（子侧无非空值）→ 0.0，绝不返回 1.0（空真伪证据）。"""
        self.assertEqual(containment_from_counts(0, 0), 0.0)
        self.assertEqual(containment_from_counts(-1, 0), 0.0)

    def test_missing_gt_total_zero(self):
        """missing > total（脏计数防御）→ 0.0。"""
        self.assertEqual(containment_from_counts(5, 6), 0.0)

    def test_non_numeric_inputs_zero(self):
        """非数字输入 → 0.0（宁小勿大）。"""
        self.assertEqual(containment_from_counts("x", 1), 0.0)
        self.assertEqual(containment_from_counts(None, None), 0.0)

    def test_constants(self):
        """阈值/权重/取样上限常量口径锁定。"""
        self.assertEqual(DEFAULT_CONTAINMENT_THRESHOLD, 0.85)
        self.assertEqual(
            PRESCREEN_WEIGHTS,
            {"naming": 0.4, "type_family": 0.2, "containment": 0.4},
        )
        self.assertEqual(CONTAINMENT_SAMPLE_LIMIT, 1000)


class TestNormalizeTypeFamily(unittest.TestCase):
    """REQ-SU-013 规则 2：类型族归一。"""

    def test_int_family(self):
        """整型/定点/浮点/修饰词形态 → int。"""
        for t in ("int", "integer", "bigint unsigned", "decimal(10,2)",
                  "BIGINT", "double precision", "float8", "serial"):
            with self.subTest(t=t):
                self.assertEqual(normalize_type_family(t), "int")

    def test_string_family(self):
        """字符/文本/枚举系 → string。"""
        for t in ("varchar(255)", "char", "text", "longtext",
                  "character varying", "enum", "set"):
            with self.subTest(t=t):
                self.assertEqual(normalize_type_family(t), "string")

    def test_uuid_family(self):
        """PG 原生 uuid → uuid。"""
        self.assertEqual(normalize_type_family("uuid"), "uuid")

    def test_other_family(self):
        """日期时间/二进制/json/未知/None/空 → other（保守排除）。"""
        for t in ("timestamp with time zone", "datetime", "json", "jsonb",
                  "bytea", "blob", "", None, "totally_unknown"):
            with self.subTest(t=t):
                self.assertEqual(normalize_type_family(t), "other")


class TestDataMasker(unittest.TestCase):
    """REQ-SU-012：采样脱敏器（值形态优先 + 列名词典 + BLOB）。"""

    def setUp(self):
        self.masker = DataMasker()

    def test_none_passthrough(self):
        """NULL 就是 NULL：None 原样返回。"""
        self.assertIsNone(self.masker.mask_value("phone", None, None))

    def test_bytes_blob_form(self):
        """bytes → <BLOB size=N preview_hex=...>（前 16 字节 hex）。"""
        out = self.masker.mask_value("avatar", None, b"\x89PNG\r\n\x1a\n" + b"x" * 30)
        self.assertTrue(out.startswith("<BLOB size=38 preview_hex="), msg=out)
        self.assertIn("89504e47", out)

    def test_value_shape_beats_column_name(self):
        """值形态优先：列名中性但值是手机号 → phone 标签。"""
        out = self.masker.mask_value("note", None, "13800138000")
        self.assertEqual(out, "<REDACTED:phone|len=11|class=digits>")

    def test_column_dict_channel(self):
        """列名词典通道：user_mobile → phone；值不含 PII 形态也脱。"""
        out = self.masker.mask_value("user_mobile", None, "abc123")
        self.assertTrue(out.startswith("<REDACTED:phone|"), msg=out)

    def test_comment_dict_channel(self):
        """中文注释命中词典：comment='手机号' → phone。"""
        out = self.masker.mask_value("f1", "手机号", "abc123")
        self.assertTrue(out.startswith("<REDACTED:phone|"), msg=out)

    def test_card_no_bank_card_label(self):
        """card_no 列名 → bank_card 标签（长词优先于短词后仍由 card 兜住）。"""
        out = self.masker.mask_value("card_no", None, "abc")
        self.assertTrue(out.startswith("<REDACTED:bank_card|"), msg=out)

    def test_email_value_shape(self):
        """邮箱值形态 → email 标签（mixed 类）。"""
        out = self.masker.mask_value("contact", None, "a@b.com")
        self.assertTrue(out.startswith("<REDACTED:email|"), msg=out)
        self.assertIn("class=mixed", out)

    def test_id_card_value_shape(self):
        """18 位身份证值形态 → id_card。"""
        out = self.masker.mask_value("code", None, "110101199001011234")
        self.assertTrue(out.startswith("<REDACTED:id_card|len=18"), msg=out)

    def test_long_string_blob(self):
        """>512 字符 → <BLOB size=N>（不落正文）。"""
        out = self.masker.mask_value("body", None, "x" * 600)
        self.assertEqual(out, "<BLOB size=600>")

    def test_already_redacted_shell_passthrough(self):
        """上游 redact 完整壳（<REDACTED:type>）原样保留，不二次加工。"""
        out = self.masker.mask_value("phone", None, "<REDACTED:phone>")
        self.assertEqual(out, "<REDACTED:phone>")

    def test_plain_short_text_kept(self):
        """中性列 + 中性值 → 原样字符串。"""
        self.assertEqual(self.masker.mask_value("name", None, "widget"), "widget")
        self.assertEqual(self.masker.mask_value("qty", None, 42), "42")

    def test_cjk_class_detected(self):
        """敏感列 + 中文值 → class=cjk（类别保留、内容不外泄）。

        词典按条目长度降序匹配，"地址" 短于更长的干扰词时优先命中 address；
        此处列名 addr 不含词典词，改用注释"联系地址"命中 address。
        """
        out = self.masker.mask_value("addr", "联系地址", "北京市海淀区")
        self.assertTrue(out.startswith("<REDACTED:address|"), msg=out)
        self.assertIn("class=cjk", out)
        self.assertNotIn("海淀", out)


class TestSingularize(unittest.TestCase):
    """REQ-SU-013 规则 1：单复数还原轻量归一（修复后正确行为）。"""

    def test_regular_rules(self):
        """修复后正确行为：委托 relation_analyzer._singularize，
        es 结尾且词根尾缀 ∈ {ch,sh,s,x,z,o} 才去 es——
        addresses→address、boxes→box、batches→batch（旧表整段吞匹配
        导致 addres/bo/bat 的过度剥离已修复，与 relation 侧单点共用）。
        """
        self.assertEqual(singularize("categories"), "category")  # ies→y
        self.assertEqual(singularize("users"), "user")           # 去 s
        self.assertEqual(singularize("addresses"), "address")    # es→(sh 词根)
        self.assertEqual(singularize("boxes"), "box")            # es→(x 词根)

    def test_regression_matches_relation_analyzer(self):
        """回归：与 relation_analyzer._singularize 逐词结果一致（杜绝漂移）。"""
        from su.relation_analyzer import _singularize
        for w in ("addresses", "boxes", "batches", "ches", "statuses",
                  "heroes", "orders", "categories", "person", "data"):
            with self.subTest(word=w):
                self.assertEqual(singularize(w), _singularize(w))
        # 过度剥离回归哨兵
        self.assertNotEqual(singularize("addresses"), "addres")
        self.assertNotEqual(singularize("boxes"), "bo")

    def test_irregular_untouched(self):
        """不规则形态（辅音+d 结尾无 s）原样返回。"""
        self.assertEqual(singularize("person"), "person")
        self.assertEqual(singularize("child"), "child")

    def test_empty_and_case(self):
        """空串与大小写容错。"""
        self.assertEqual(singularize(""), "")
        self.assertEqual(singularize("USERS"), "user")


class TestImplicitFkCandidate(unittest.TestCase):
    """REQ-SU-013：候选 → 落库 DTO（证据必须带"推断需确认"标注）。"""

    def test_to_redacted_evidence_note(self):
        """evidence_json 必含"隐式外键为预筛推断，需人工确认"。"""
        cand = ImplicitFkCandidate(
            child_table="shop.order_items",
            child_column="order_id",
            parent_table="shop.orders",
            parent_column="id",
            prescreen_score=1.0,
            stopped_at_rule=0,
            containment=0.97,
            naming_hit="order_id→shop.orders.id",
        )
        dto = cand.to_redacted()
        evidence = json.loads(dto.evidence_json)
        self.assertIn("note", evidence)
        self.assertIn("隐式外键为预筛推断，需人工确认", evidence["note"])
        self.assertEqual(evidence["containment"], 0.97)
        self.assertEqual(dto.prescreen_score, 1.0)
        self.assertEqual(dto.stopped_at_rule, 0)

    def test_to_redacted_sorted_keys(self):
        """evidence_json sort_keys 序列化（渲染幂等前提）。"""
        cand = ImplicitFkCandidate(
            child_table="a.b", child_column="x_id", parent_table="a.x",
            parent_column="id", prescreen_score=0.4, stopped_at_rule=2,
        )
        raw = cand.to_redacted().evidence_json
        self.assertEqual(raw, json.dumps(json.loads(raw), ensure_ascii=False,
                                         sort_keys=True))
        self.assertIsNone(cand.containment)


class TestDbInspectorGuards(unittest.TestCase):
    """REQ-SU-021 红线：驱动注入缺失快速失败，绝不静默 mock。"""

    def _cfg(self):
        """最小 DatabaseConfig 替身（构造不建连，仅存字段）。"""
        from su.config import DatabaseConfig
        from su.dto import SensitiveStr
        return DatabaseConfig(
            engine="mysql", host="localhost", port=3306,
            user="ro", password=SensitiveStr("x"), database="shop",
            schemas=(),
        )

    def test_none_driver_rejected(self):
        """driver_module=None → ValueError（编排层降级判定遗漏防线）。"""
        with self.assertRaises(ValueError):
            DbInspector(self._cfg(), store=None, driver_module=None)

    def test_no_connect_collect_runtime_error(self):
        """未 connect 调用 collect_schema → RuntimeError。"""
        import types
        fake_driver = types.ModuleType("fakepymysql")
        insp = DbInspector(self._cfg(), store=None, driver_module=fake_driver)
        with self.assertRaises(RuntimeError):
            insp.collect_schema()

    def test_close_idempotent_without_conn(self):
        """未连接 close 幂等（不抛）。"""
        import types
        fake_driver = types.ModuleType("fakepymysql")
        insp = DbInspector(self._cfg(), store=None, driver_module=fake_driver)
        insp.close()
        insp.close()


class _CollectCursor:
    """MySQL 内省替身 cursor：按语句关键字路由预置结果行（模拟服务端应答）。"""

    def __init__(self, responses):
        # responses: {语句匹配子串: (列名元组, 行列表)}
        self._responses = responses
        self.description = None
        self._results = []

    def execute(self, sql, params=None):
        """按语句子串命中预置结果集；未命中给空结果。"""
        self.description = None
        self._results = []
        for needle, (desc, rows) in self._responses.items():
            if needle in sql:
                self.description = [(c,) for c in desc]
                self._results = rows
                return

    def fetchall(self):
        """取回上次 execute 的预置行。"""
        return list(self._results)

    def close(self):
        """DB-API 契约占位（guard 的 finally 会调用）。"""


class _CollectConn:
    """DB-API 连接替身：返回共享 _CollectCursor。"""

    def __init__(self, responses):
        self._responses = responses

    def cursor(self):
        """新建游标（guard 每条语句一个 cursor，共享预置表即可）。"""
        return _CollectCursor(self._responses)


class TestCollectSchemaMysqlCount(unittest.TestCase):
    """回归（2026-09-28 自测发现）：_collect_schema_mysql 的 count 未初始化
    必抛 UnboundLocalError——修复后表计数正确返回。"""

    def test_mysql_collect_returns_count(self):
        """两条表行 → collect_schema 返回 2（修复前 UnboundLocalError）。"""
        import types
        from su.config import DatabaseConfig
        from su.dto import SensitiveStr

        responses = {
            # 表清单：2 张 BASE TABLE
            "FROM information_schema.TABLES": (
                ("TABLE_SCHEMA", "TABLE_NAME", "TABLE_TYPE", "TABLE_ROWS", "TABLE_COMMENT"),
                [("shop", "orders", "BASE TABLE", 10, None),
                 ("shop", "users", "BASE TABLE", 5, None)],
            ),
            # 列清单 / FK：空结果即可（计数与列无关）
            "FROM information_schema.COLUMNS": (
                ("TABLE_SCHEMA", "TABLE_NAME", "COLUMN_NAME", "DATA_TYPE",
                 "COLUMN_KEY", "COLUMN_COMMENT", "ORDINAL_POSITION"),
                [],
            ),
            "KEY_COLUMN_USAGE": (
                ("TABLE_SCHEMA", "TABLE_NAME", "COLUMN_NAME", "ref_table"),
                [],
            ),
        }

        class _Store:
            """最小 StateStore 替身：只接 upsert_db_table，记录调用次数。"""

            def __init__(self):
                self.calls = 0

            def upsert_db_table(self, dto):
                """记录写库调用（不真落 SQLite）。"""
                self.calls += 1

        cfg = DatabaseConfig(
            engine="mysql", host="localhost", port=3306,
            user="ro", password=SensitiveStr("x"), database="shop",
            schemas=(),
        )
        store = _Store()
        insp = DbInspector(cfg, store=store, driver_module=types.ModuleType("fake"))
        # 绕过 connect（凭据红线：单测不建连），直接注入替身连接
        insp._conn = _CollectConn(responses)
        count = insp.collect_schema()
        self.assertEqual(count, 2)
        self.assertEqual(store.calls, 2)
        self.assertEqual(len(insp._tables), 2)


if __name__ == "__main__":
    unittest.main()
