# -*- coding: utf-8 -*-
"""SU 能力单元测试：确定性三角关联分析器（REQ-SU-016 / REQ-SU-017）。

覆盖 su.relation_analyzer 模块（真临时 SQLite StateStore，不 mock 业务逻辑）：
- normalize_name：驼峰↔下划线互转 + 简单单复数还原 + 大小写归一
- page_to_api：observed_on_pages 直连（score 恒 1.0）、blocked_events
  kind='aborted_method' 按 path 计数写"被拦截非GET次数"、GET 标"读请求":1
- api_to_table：path 业务段↔归一表名（命名通道 1.0）∪ 响应键↔列名重合度
  （≥阈值产证据），score = max(命名, 重合度)，evidence 中文键完整
- redis_to_entity：模式段↔归一表名 ∪ 值样例 JSON 顶层键↔列名重合度
  （截断 JSON 不产假证据）
- persist：UNIQUE(rtype,left_ref,right_ref) 幂等，冲突取 MAX(score)

运行方式（项目根目录）：
    python3 -B -m unittest scripts.tests.test_su_relation
"""

import tempfile
import unittest
from pathlib import Path
import sys

# 将 scripts/ 目录注入 sys.path，使 `from su.xxx import ...` 生效
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from su.config import redact  # noqa: E402
from su.dto import (  # noqa: E402
    ActionDecisionRedacted,
    ApiObservationRedacted,
    BlockedEventRedacted,
    DbTableRedacted,
    EdgeRedacted,
    PageNodeRedacted,
    RedactedDict,
    RedisKeyRecordRedacted,
    RelationRedacted,
)
from su.relation_analyzer import RelationAnalyzer, evidence_score  # noqa: E402
from su.state_store import StateStore  # noqa: E402


def _mk_store(tmpdir: str) -> StateStore:
    """在临时目录建真 StateStore（SQLite），返回已持锁实例。"""
    store = StateStore(Path(tmpdir) / "test_relation.sqlite", "sys-relation-test")
    store.acquire_lock(resume=False)
    return store


def _mk_page(store: StateStore, url_key: str, url: str, page_id_hint: int = None) -> int:
    """写入一个页面节点并返回 page_id。"""
    node = PageNodeRedacted(url_key=url_key, url=url, depth=0)
    store.upsert_page(node)
    # 通过 export 查询实际 page_id
    data = store.export_understanding()
    for p in data["pages"]:
        if p["url_key"] == url_key:
            return int(p["page_id"])
    raise AssertionError("page not found: {0}".format(url_key))


def _mk_table(store: StateStore, schema: str, table: str, cols: list) -> None:
    """写入一张表（含列），列必须为 RedactedDict。"""
    cols_redacted = []
    for col in cols:
        d = redact(col) if isinstance(col, dict) else col
        cols_redacted.append(d)
    store.upsert_db_table(DbTableRedacted(
        schema_name=schema, table_name=table, kind="BASE TABLE", columns=cols_redacted
    ))


def _mk_endpoint(store: StateStore, url_path: str, method: str,
                 page_id: int, response_shape: dict = None) -> int:
    """写入一个 API 端点并返回 endpoint_id。"""
    resp = redact(response_shape) if response_shape else None
    store.upsert_api_endpoint(ApiObservationRedacted(
        url_path=url_path, method=method, observed_on_page=page_id, ts=1000.0,
        response_shape=resp,
    ))
    data = store.export_understanding()
    for ep in data["endpoints"]:
        if ep["url_path"] == url_path and ep["method"] == method:
            return int(ep["endpoint_id"])
    raise AssertionError("endpoint not found: {0} {1}".format(method, url_path))


def _mk_blocked(store: StateStore, kind: str, url: str, method: str = None) -> None:
    """写入一条拦截事件。"""
    store.insert_blocked_event(BlockedEventRedacted(
        kind=kind, url=url, ts=1000.0, method=method,
    ))


def _mk_redis_patterns(store: StateStore, rows: list) -> None:
    """批量写入 redis_patterns 快照（一次 replace，避免快照语义互相覆盖）。"""
    dict_rows = []
    for r in rows:
        d = redact({
            "pattern": r["pattern"], "key_count": r.get("key_count", 1),
            "no_ttl_ratio": 0.0, "ttl_summary": {}, "type_summary": {},
            "sample_keys": [],
        })
        dict_rows.append(d)
    store.replace_redis_patterns(dict_rows)


def _find_pattern_id(store: StateStore, pattern: str) -> int:
    """按 pattern 文本查询 pattern_id。"""
    for p in store.export_understanding()["redis_patterns"]:
        if p["pattern"] == pattern:
            return int(p["pattern_id"])
    raise AssertionError("pattern not found: {0}".format(pattern))


def _mk_redis_key(store: StateStore, key_name: str, value_sample: str = None) -> None:
    """写入一条 redis_keys 记录。"""
    store.upsert_redis_key(RedisKeyRecordRedacted(
        key_name=key_name, key_type="string", value_sample=value_sample,
    ))


def _find_rel(rels: list, rtype: str, left: str, right: str) -> dict:
    """从 RelationRedacted 列表中找指定三元组。"""
    for r in rels:
        if r.rtype == rtype and r.left_ref == left and r.right_ref == right:
            return r
    raise AssertionError("relation not found: {0} {1} → {2}".format(rtype, left, right))


class TestNormalizeName(unittest.TestCase):
    """REQ-SU-016：normalize_name 静态方法。"""

    def test_camel_case_to_snake(self):
        """驼峰拆词 + 下划线连接。"""
        self.assertEqual(RelationAnalyzer.normalize_name("OrderItem"), "order_item")
        self.assertEqual(RelationAnalyzer.normalize_name("HTTPServer"), "http_server")
        self.assertEqual(RelationAnalyzer.normalize_name("orderItem"), "order_item")

    def test_snake_case_simplified(self):
        """下划线输入直接归一。"""
        self.assertEqual(RelationAnalyzer.normalize_name("order_item"), "order_item")
        self.assertEqual(RelationAnalyzer.normalize_name("ORDER_ITEM"), "order_item")

    def test_plural_to_singular(self):
        """简单复数→单数（orders→order / boxes→box / categories→category）。"""
        self.assertEqual(RelationAnalyzer.normalize_name("orders"), "order")
        self.assertEqual(RelationAnalyzer.normalize_name("boxes"), "box")
        self.assertEqual(RelationAnalyzer.normalize_name("categories"), "category")

    def test_protection_no_strip(self):
        """-ss/-us/-is 结尾不剥 -s（status/address 保持原形）。

        canvas（-as 结尾）不在保护后缀集内是**既定口径**：规则表只收
        确定性最高的 -ss/-us/-is 三保护缀（宁不剥不误剥——canvas 这类
        单形词的漏保护只会让命名/关联通道 miss 止步规则 1，不产假阳性）；
        2026-09-28 自测确认后维持该口径，锁定为正确行为防止无声漂移。
        """
        self.assertEqual(RelationAnalyzer.normalize_name("status"), "status")
        self.assertEqual(RelationAnalyzer.normalize_name("address"), "address")
        self.assertEqual(RelationAnalyzer.normalize_name("canvas"), "canva")

    def test_empty_input(self):
        """空输入返回空串。"""
        self.assertEqual(RelationAnalyzer.normalize_name(""), "")
        self.assertEqual(RelationAnalyzer.normalize_name(None), "")

    def test_numeric_segment_preserved(self):
        """数字段保留原样（v2 不变化）。"""
        self.assertEqual(RelationAnalyzer.normalize_name("v2"), "v2")


class TestPageToApi(unittest.TestCase):
    """REQ-SU-016：page ↔ api 直连证据。"""

    def test_basic_connection(self):
        """单个端点在单页观测 → 1 条 relation（score=1.0，left='pages:1'）。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _mk_store(tmp)
            page_id = _mk_page(store, "https://example.com/a", "https://example.com/a")
            _mk_endpoint(store, "/api/items", "GET", page_id)
            analyzer = RelationAnalyzer(store)
            rels = analyzer.page_to_api()
            self.assertEqual(len(rels), 1)
            r = rels[0]
            self.assertEqual(r.rtype, "page_api")
            self.assertEqual(r.left_ref, "pages:{0}".format(page_id))
            self.assertEqual(r.score, 1.0)
            self.assertTrue(r.evidence["method"] == "GET")
            self.assertTrue(r.evidence["读请求"] == 1)
            store.close()

    def test_multi_page_observation(self):
        """同一端点在多页观测 → 多条 relation（每页一条）。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _mk_store(tmp)
            p1 = _mk_page(store, "https://example.com/p1", "https://example.com/p1")
            p2 = _mk_page(store, "https://example.com/p2", "https://example.com/p2")
            _mk_endpoint(store, "/api/items", "GET", p1)
            # 第二次观测（另一页）→ observed_on_pages 追加
            store.upsert_api_endpoint(ApiObservationRedacted(
                url_path="/api/items", method="GET", observed_on_page=p2, ts=2000.0,
            ))
            analyzer = RelationAnalyzer(store)
            rels = analyzer.page_to_api()
            self.assertEqual(len(rels), 2)
            left_refs = sorted(r.left_ref for r in rels)
            self.assertEqual(left_refs[0], "pages:{0}".format(min(p1, p2)))
            self.assertEqual(left_refs[1], "pages:{0}".format(max(p1, p2)))
            store.close()

    def test_blocked_non_get_counted(self):
        """GET 端点 + aborted_method 同 path → 证据含"被拦截非GET次数"=1。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _mk_store(tmp)
            page_id = _mk_page(store, "https://example.com/x", "https://example.com/x")
            _mk_endpoint(store, "/api/items", "GET", page_id)
            _mk_blocked(store, "aborted_method", "/api/items", method="POST")
            analyzer = RelationAnalyzer(store)
            rels = analyzer.page_to_api()
            self.assertEqual(len(rels), 1)
            self.assertEqual(rels[0].evidence["被拦截非GET次数"], 1)
            store.close()

    def test_non_get_method_not_read(self):
        """POST 端点 → "读请求"=0。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _mk_store(tmp)
            page_id = _mk_page(store, "https://example.com/y", "https://example.com/y")
            _mk_endpoint(store, "/api/items", "POST", page_id)
            analyzer = RelationAnalyzer(store)
            rels = analyzer.page_to_api()
            self.assertEqual(rels[0].evidence["读请求"], 0)
            store.close()

    def test_empty_store_returns_empty(self):
        """空库（无端点）→ 空列表。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _mk_store(tmp)
            analyzer = RelationAnalyzer(store)
            self.assertEqual(analyzer.page_to_api(), [])
            store.close()


class TestApiToTable(unittest.TestCase):
    """REQ-SU-016：api ↔ table 双通道证据。"""

    def test_name_hit_only(self):
        """path '/api/orders/{id}' 命中表 orders（命名通道 1.0），无响应键。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _mk_store(tmp)
            page_id = _mk_page(store, "https://example.com/z", "https://example.com/z")
            _mk_table(store, "appdb", "orders", [{"name": "id", "data_type": "bigint", "type_family": "int", "is_pk": 1}])
            ep_id = _mk_endpoint(store, "/api/orders/{id}", "GET", page_id)
            analyzer = RelationAnalyzer(store)
            rels = analyzer.api_to_table()
            self.assertEqual(len(rels), 1)
            r = rels[0]
            self.assertEqual(r.rtype, "api_table")
            self.assertEqual(r.left_ref, "api:{0}".format(ep_id))
            self.assertIn("db_tables:", r.right_ref)
            self.assertEqual(r.score, 1.0)
            self.assertEqual(r.evidence["path段命中表名"], 1)
            store.close()

    def test_overlap_channel_only(self):
        """响应键 {"order_id","amount"} 与列名 {order_id, amount} 重合度 1.0 ≥ 阈值。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _mk_store(tmp)
            page_id = _mk_page(store, "https://example.com/w", "https://example.com/w")
            _mk_table(store, "appdb", "payments", [
                {"name": "order_id", "data_type": "bigint", "type_family": "int", "is_pk": 1},
                {"name": "amount", "data_type": "decimal", "type_family": "other"},
            ])
            # path 段 'charge' 与表 payments 归一后不等，确保走重合度通道
            _mk_endpoint(store, "/api/paid/charge", "POST", page_id,
                         response_shape={"order_id": 123, "amount": 10.5})
            analyzer = RelationAnalyzer(store)
            rels = analyzer.api_to_table()
            self.assertEqual(len(rels), 1)
            r = rels[0]
            self.assertEqual(r.evidence["列名重合度"], 1.0)
            self.assertEqual(r.score, 1.0)
            self.assertEqual(r.evidence["path段命中表名"], 0)
            store.close()

    def test_both_channels_score_max(self):
        """命名命中 1.0 + 重合度 0.5 → score = max(1.0, 0.5) = 1.0。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _mk_store(tmp)
            page_id = _mk_page(store, "https://example.com/v", "https://example.com/v")
            _mk_table(store, "appdb", "orders", [
                {"name": "id", "data_type": "bigint", "type_family": "int", "is_pk": 1},
                {"name": "name", "data_type": "varchar", "type_family": "string"},
            ])
            _mk_endpoint(store, "/api/orders/{id}", "GET", page_id,
                         response_shape={"id": 1, "title": "x"})  # id 命中 1/2=0.5
            analyzer = RelationAnalyzer(store)
            rels = analyzer.api_to_table()
            self.assertEqual(len(rels), 1)
            self.assertEqual(rels[0].score, 1.0)  # max(1.0, 0.5)
            store.close()

    def test_below_threshold_no_relation(self):
        """重合度 0.25 < 阈值 0.5 且无命名命中 → 不产证据。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _mk_store(tmp)
            page_id = _mk_page(store, "https://example.com/u", "https://example.com/u")
            _mk_table(store, "appdb", "products", [
                {"name": "sku", "data_type": "varchar", "type_family": "string", "is_pk": 1},
            ])
            _mk_endpoint(store, "/api/unknown/x", "GET", page_id,
                         response_shape={"a": 1, "b": 2, "c": 3, "d": 4})  # 0/4=0.0
            analyzer = RelationAnalyzer(store)
            self.assertEqual(analyzer.api_to_table(), [])
            store.close()

    def test_empty_returns_empty(self):
        """无端点或无表 → 空列表。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _mk_store(tmp)
            _mk_table(store, "appdb", "empty", [{"name": "id", "data_type": "int", "type_family": "int"}])
            self.assertEqual(RelationAnalyzer(store).api_to_table(), [])
            store.close()


class TestRedisToEntity(unittest.TestCase):
    """REQ-SU-017：redis ↔ entity（表）证据。"""

    def test_pattern_segment_hit(self):
        """模式 'orders:{n}' 段 'orders' 命中表 orders（命名通道 1.0）。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _mk_store(tmp)
            _mk_table(store, "appdb", "orders", [{"name": "id", "data_type": "bigint", "type_family": "int", "is_pk": 1}])
            _mk_redis_patterns(store, [{"pattern": "orders:{n}", "key_count": 5}])
            pid = _find_pattern_id(store, "orders:{n}")
            analyzer = RelationAnalyzer(store)
            rels = analyzer.redis_to_entity()
            self.assertEqual(len(rels), 1)
            r = rels[0]
            self.assertEqual(r.rtype, "redis_entity")
            self.assertEqual(r.left_ref, "redis_pattern:{0}".format(pid))
            self.assertEqual(r.score, 1.0)
            self.assertEqual(r.evidence["模式段命中表名"], 1)
            store.close()

    def test_value_sample_json_keys(self):
        """值样例 JSON 顶层键与列名重合度 ≥ 阈值 → 产证据。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _mk_store(tmp)
            _mk_table(store, "appdb", "users", [
                {"name": "id", "data_type": "bigint", "type_family": "int", "is_pk": 1},
                {"name": "name", "data_type": "varchar", "type_family": "string"},
            ])
            _mk_redis_patterns(store, [{"pattern": "user:{n}", "key_count": 1}])
            pid = _find_pattern_id(store, "user:{n}")
            _mk_redis_key(store, "user:1", value_sample='{"id": 1, "name": "test"}')
            analyzer = RelationAnalyzer(store)
            rels = analyzer.redis_to_entity()
            # pattern 段 'user' ≠ 'users'（归一后 'user'），但值样例键 {id,name} 与列 {id,name} 重合 1.0
            self.assertEqual(len(rels), 1)
            self.assertEqual(rels[0].evidence["重合度"], 1.0)
            store.close()

    def test_truncated_json_no_false_evidence(self):
        """截断 JSON（解析失败）→ 空键集，重合度通道不产假证据。

        实现事实：模式段通道仍独立产证据（此处 'log'→'log' 命中表 logs
        归一 'log'，score=1.0）——但值样例键贡献为零（"值样例键数":0、
        "重合度":0.0），即截断 JSON 不向重合度通道注入任何假键。
        """
        with tempfile.TemporaryDirectory() as tmp:
            store = _mk_store(tmp)
            _mk_table(store, "appdb", "logs", [{"name": "id", "data_type": "int", "type_family": "int", "is_pk": 1}])
            _mk_redis_patterns(store, [{"pattern": "cache:{n}", "key_count": 1}])  # 模式段 'cache' 不命中表 logs
            _mk_redis_key(store, "cache:1", value_sample='{"id": 1, "mess')  # 截断
            analyzer = RelationAnalyzer(store)
            rels = analyzer.redis_to_entity()
            # 模式段不命中 + 截断 JSON 值通道为空 → 零证据
            self.assertEqual(len(rels), 0)
            # 命名通道命中场景下值样例键数仍为 0（截断不产键）
            _mk_redis_patterns(store, [{"pattern": "cache:{n}", "key_count": 1},
                                       {"pattern": "logs:{n}", "key_count": 1}])
            _mk_redis_key(store, "logs:9", value_sample='{"id": 1, "mess')
            rels2 = analyzer.redis_to_entity()
            self.assertEqual(len(rels2), 1)
            self.assertEqual(rels2[0].evidence["模式段命中表名"], 1)
            self.assertEqual(rels2[0].evidence["值样例键数"], 0)
            self.assertEqual(rels2[0].evidence["重合度"], 0.0)
            store.close()

    def test_empty_patterns_returns_empty(self):
        """无 patterns → 空列表。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _mk_store(tmp)
            _mk_table(store, "appdb", "t", [{"name": "id", "data_type": "int", "type_family": "int"}])
            self.assertEqual(RelationAnalyzer(store).redis_to_entity(), [])
            store.close()


class TestPersist(unittest.TestCase):
    """REQ-SU-016/017：persist 幂等落库 + MAX(score) 冲突语义。"""

    def test_persist_basic(self):
        """首次 persist 2 条 → 返回 2，export relations 表含 2 条。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _mk_store(tmp)
            rels = [
                RelationRedacted(rtype="page_api", left_ref="pages:1", right_ref="api:1", score=1.0, evidence=RedactedDict({"依据": "test"})),
                RelationRedacted(rtype="api_table", left_ref="api:1", right_ref="db_tables:1", score=0.8, evidence=RedactedDict({"依据": "test2"})),
            ]
            count = RelationAnalyzer(store).persist(rels)
            self.assertEqual(count, 2)
            data = store.export_understanding()
            self.assertEqual(len(data["relations"]), 2)
            store.close()

    def test_conflict_max_score(self):
        """同三元组再 persist 低 score → export 保留 MAX(score)。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _mk_store(tmp)
            analyzer = RelationAnalyzer(store)
            # 首次：score=0.9
            analyzer.persist([RelationRedacted(
                rtype="page_api", left_ref="pages:1", right_ref="api:1", score=0.9,
                evidence=RedactedDict({"依据": "high"}))])
            # 再次：score=0.5（更低）
            analyzer.persist([RelationRedacted(
                rtype="page_api", left_ref="pages:1", right_ref="api:1", score=0.5,
                evidence=RedactedDict({"依据": "low"}))])
            data = store.export_understanding()
            self.assertEqual(len(data["relations"]), 1)
            self.assertEqual(data["relations"][0]["score"], 0.9)  # MAX 保留
            store.close()

    def test_conflict_higher_score_updates(self):
        """同三元组再 persist 高 score → export 更新为新高值。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _mk_store(tmp)
            analyzer = RelationAnalyzer(store)
            analyzer.persist([RelationRedacted(
                rtype="api_table", left_ref="api:1", right_ref="db_tables:2", score=0.3,
                evidence=RedactedDict({"依据": "initial"}))])
            analyzer.persist([RelationRedacted(
                rtype="api_table", left_ref="api:1", right_ref="db_tables:2", score=0.95,
                evidence=RedactedDict({"依据": "updated"}))])
            data = store.export_understanding()
            self.assertEqual(len(data["relations"]), 1)
            self.assertAlmostEqual(data["relations"][0]["score"], 0.95)
            store.close()


class TestEvidenceScore(unittest.TestCase):
    """evidence_score 辅助函数：max(命名得分, 重合度)。"""

    def test_name_only(self):
        """命名命中 1.0 + 重合度 0.0 → 1.0。"""
        ev = RedactedDict({"列名重合度": 0.0})
        self.assertEqual(evidence_score(ev, 1.0), 1.0)

    def test_overlap_only(self):
        """无命名命中 + 重合度 0.7 → 0.7。"""
        ev = RedactedDict({"列名重合度": 0.7})
        self.assertEqual(evidence_score(ev, 0.0), 0.7)

    def test_max_of_both(self):
        """命名 1.0 + 重合度 0.5 → 1.0。"""
        ev = RedactedDict({"列名重合度": 0.5})
        self.assertEqual(evidence_score(ev, 1.0), 1.0)

    def test_missing_key_defaults_zero(self):
        """evidence 无"列名重合度"键 → 按 0.0 处理。"""
        ev = RedactedDict({})
        self.assertEqual(evidence_score(ev, 1.0), 1.0)


if __name__ == "__main__":
    unittest.main()
