# -*- coding: utf-8 -*-
"""SU 能力单元测试：文档渲染器（REQ-SU-018）。

覆盖 su.document_renderer 模块（真临时 StateStore + 真文件产物，不 mock）：
- render() 产物清单：UNDERSTANDING.md / understanding.json / summary.json /
  diagrams/navigation.mmd / diagrams/er.mmd / evidence/evidence-index.json
- 渲染幂等（AC4/§7.3）：二次渲染除 ``<!-- generated_at:`` 行外逐字节一致
- 10 节固定标题齐全（AC1）
- set_lens_status：非 collected 缺 skip_reason → ValueError；未登记透镜保守
  返回 failed（AP-3 绝不冒充已采集）
- 缺失即声明：skipped 透镜在文档输出"未采集：<原因>"
- findings 未回填 → 第 5/7 节 LLM_PENDING_NOTE；low confidence → ⚠ 前缀
- ER 图：隐式 FK 关系标签"推断FK(包含度x.xx)"正则可识别（AC2）；
  列数 >12 追加"仅展示关键列"属性行
- 导航图：>60 节点折叠（subgraph + 计数节点）；≤60 平铺
- Mermaid 自检坏图必须抛 RuntimeError
- 第 8 节：aborted_method 单独清单 + 其他 kind 统计

运行方式（项目根目录）：
    python3 -B -m unittest scripts.tests.test_su_doc_render
"""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

# 将 scripts/ 目录注入 sys.path，使 `from su.xxx import ...` 生效
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from su.config import (  # noqa: E402
    DatabaseConfig,
    RedisConfig,
    RunBudget,
    SuConfig,
    SystemConfig,
    redact,
)
from su.dto import (  # noqa: E402
    BlockedEventRedacted,
    DbTableRedacted,
    ImplicitFkCandidateRedacted,
    PageNodeRedacted,
    RedactedDict,
    RelationRedacted,
    SensitiveStr,
)
from su.document_renderer import (  # noqa: E402
    LLM_PENDING_NOTE,
    SECTION_TITLES,
    DocumentRenderer,
)
from su.state_store import StateStore  # noqa: E402


def _make_cfg(tmpdir: str) -> SuConfig:
    """构造渲染所需最小 SuConfig（凭据全 SensitiveStr，不落盘）。"""
    return SuConfig(
        system=SystemConfig(
            base_url="https://legacy.example.com",
            login_url="/login",
            username=SensitiveStr("u"),
            password=SensitiveStr("p"),
        ),
        database=None,
        redis=None,
        budget=RunBudget(),
        out_dir=Path(tmpdir),
        system_id="legacy-test",
    )


def _make_store(tmpdir: str) -> StateStore:
    """建状态库（放在 <tmp>/legacy-test/state/ 与渲染根目录约定一致）。"""
    store = StateStore(Path(tmpdir) / "legacy-test" / "state" / "understanding.sqlite",
                       "legacy-test")
    store.acquire_lock(resume=False)
    store.set_config_snapshot(redact({"base_url": "https://legacy.example.com"}))
    return store


def _seed(store: StateStore) -> None:
    """写入跨透镜的最小事实集。"""
    store.upsert_page(PageNodeRedacted(
        url_key="https://legacy.example.com/home",
        url="https://legacy.example.com/home", depth=0, title="首页"))
    store.upsert_db_table(DbTableRedacted(
        schema_name="appdb", table_name="orders", kind="BASE TABLE", row_estimate=42,
        columns=[redact({"name": "id", "data_type": "bigint",
                         "type_family": "int", "is_pk": 1})]))
    store.insert_relation(RelationRedacted(
        rtype="api_table", left_ref="api:1", right_ref="db_tables:1",
        score=0.9, evidence=redact({"依据": "响应键重合度0.90达阈值（推断，需确认）"})))
    store.insert_blocked_event(BlockedEventRedacted(
        kind="aborted_method", url="/api/orders", method="DELETE", ts=1.0))
    store.insert_blocked_event(BlockedEventRedacted(
        kind="blocked_origin", url="https://evil.test/x", ts=2.0))


class TestRenderProducts(unittest.TestCase):
    """REQ-SU-018 AC1：产物清单与 10 节结构。"""

    def test_all_products_written(self):
        """render 写出全部 6 类产物且 outcome.files 排序稳定。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            _seed(store)
            renderer = DocumentRenderer(store, _make_cfg(tmp))
            outcome = renderer.render()
            root = Path(tmp) / "legacy-test"
            expected = sorted([
                "UNDERSTANDING.md", "understanding.json", "summary.json",
                "diagrams/navigation.mmd", "diagrams/er.mmd",
                "evidence/evidence-index.json",
            ])
            self.assertEqual(outcome.files, expected)
            for rel in expected:
                self.assertTrue((root / rel).is_file(), rel)
            self.assertTrue(outcome.mermaid_ok)
            store.close()

    def test_ten_sections_in_order(self):
        """UNDERSTANDING.md 含全部 10 节标题且顺序固定。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            _seed(store)
            DocumentRenderer(store, _make_cfg(tmp)).render()
            text = (Path(tmp) / "legacy-test" / "UNDERSTANDING.md").read_text("utf-8")
            positions = []
            for idx, title in enumerate(SECTION_TITLES, start=1):
                marker = "## {0}. {1}".format(idx, title)
                pos = text.find(marker)
                self.assertGreater(pos, -1, "缺少章节：{0}".format(marker))
                positions.append(pos)
            self.assertEqual(positions, sorted(positions))  # 章节顺序
            store.close()

    def test_render_idempotent_except_generated_at(self):
        """二次渲染除 generated_at 行外逐字节一致（AC4/§7.3）。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            _seed(store)
            cfg = _make_cfg(tmp)
            DocumentRenderer(store, cfg).render()
            first = (Path(tmp) / "legacy-test" / "UNDERSTANDING.md").read_text("utf-8")
            DocumentRenderer(store, cfg).render()
            second = (Path(tmp) / "legacy-test" / "UNDERSTANDING.md").read_text("utf-8")

            def strip_ts(text: str) -> str:
                return re.sub(r"<!-- generated_at: [\d.]+ -->", "TS", text)

            self.assertEqual(strip_ts(first), strip_ts(second))
            # JSON 产物本身完全一致（时间戳取 run 级常量）
            j1 = (Path(tmp) / "legacy-test" / "understanding.json").read_text("utf-8")
            DocumentRenderer(store, cfg).render()
            j2 = (Path(tmp) / "legacy-test" / "understanding.json").read_text("utf-8")
            self.assertEqual(j1, j2)
            store.close()

    def test_understanding_json_structure(self):
        """understanding.json 含 lenses / findings_prompt / schema_version。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            _seed(store)
            renderer = DocumentRenderer(store, _make_cfg(tmp))
            renderer.set_lens_status("ui", "collected")
            renderer.render()
            data = json.loads((Path(tmp) / "legacy-test" / "understanding.json").read_text("utf-8"))
            self.assertEqual(data["meta"]["schema_version"], 1)
            self.assertEqual(data["lenses"]["ui"]["status"], "collected")
            self.assertIn("instructions_ref", data["findings_prompt"])
            self.assertEqual(len(data["relations"]), 1)
            store.close()

    def test_summary_json_fields(self):
        """summary.json：stats + confidence 分布 + 预算快照。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            _seed(store)
            DocumentRenderer(store, _make_cfg(tmp)).render()
            data = json.loads((Path(tmp) / "legacy-test" / "summary.json").read_text("utf-8"))
            self.assertEqual(data["stats"]["db_tables_total"], 1)
            self.assertEqual(data["stats"]["blocked_events_total"], 2)
            self.assertEqual(data["confidence_distribution"],
                             {"high": 0, "medium": 0, "low": 0})
            # budget 来自 config_snapshot（测试快照只放了 base_url → None 兜底）
            self.assertIsNone(data["budget"]["max_pages"])
            store.close()

    def test_evidence_index_dedup(self):
        """evidence-index.json：同一记录多次引用只登记一次，seq 连续。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            _seed(store)
            renderer = DocumentRenderer(store, _make_cfg(tmp))
            # db 透镜登记 collected 才会渲染表卡片（未登记默认 failed → 整节降级）
            renderer.set_lens_status("db", "collected")
            renderer.render()
            data = json.loads(
                (Path(tmp) / "legacy-test" / "evidence" / "evidence-index.json").read_text("utf-8"))
            refs = [e["ref"] for e in data["entries"]]
            self.assertEqual(len(refs), len(set(refs)))  # 去重
            seqs = [e["seq"] for e in data["entries"]]
            self.assertEqual(seqs, list(range(1, len(seqs) + 1)))  # 编号连续
            # db_tables:1 在表卡片与 relations 表均被引用 → 仍只一条
            self.assertIn("db_tables:1", refs)
            store.close()


class TestLensStatus(unittest.TestCase):
    """REQ-SU-018 / AP-3：透镜状态登记与缺失即声明。"""

    def test_skipped_without_reason_rejected(self):
        """非 collected 且缺 skip_reason → ValueError。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            renderer = DocumentRenderer(store, _make_cfg(tmp))
            with self.assertRaises(ValueError):
                renderer.set_lens_status("db", "skipped")
            with self.assertRaises(ValueError):
                renderer.set_lens_status("db", "failed", skip_reason="   ")
            with self.assertRaises(ValueError):
                renderer.set_lens_status("db", "unknown_status", skip_reason="x")
            store.close()

    def test_unregistered_lens_conservative_failed(self):
        """未登记透镜 → failed + 未登记声明（绝不冒充 collected）。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            renderer = DocumentRenderer(store, _make_cfg(tmp))
            st = renderer.get_lens_status("redis")
            self.assertEqual(st["status"], "failed")
            self.assertIn("未登记", st["skip_reason"])
            store.close()

    def test_skipped_lens_declared_in_doc(self):
        """db 透镜 skipped → 第 4 节输出"未采集：<原因>"（空假数据红线）。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            _seed(store)
            renderer = DocumentRenderer(store, _make_cfg(tmp))
            renderer.set_lens_status("db", "skipped",
                                     skip_reason="database 段未配置（pip install pymysql）")
            renderer.render()
            text = (Path(tmp) / "legacy-test" / "UNDERSTANDING.md").read_text("utf-8")
            self.assertIn("未采集：database 段未配置（pip install pymysql）", text)
            # 事实数据虽在库中，但 DB 节不渲染表卡片（缺失即声明优先）
            section4 = text[text.find("## 4."):text.find("## 5.")]
            self.assertNotIn("#### `appdb.orders`", section4)
            store.close()


class TestFindingsRendering(unittest.TestCase):
    """REQ-SU-018/020：findings 未回填声明与 low 前缀。"""

    def test_llm_pending_note_when_no_findings(self):
        """findings 空 → 第 5/7 节输出 LLM_PENDING_NOTE。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            _seed(store)
            DocumentRenderer(store, _make_cfg(tmp)).render()
            text = (Path(tmp) / "legacy-test" / "UNDERSTANDING.md").read_text("utf-8")
            self.assertIn(LLM_PENDING_NOTE, text)
            # 第 5 节同时输出确定性关联证据表（脚本层事实不隐瞒）
            self.assertIn("api_table", text)
            store.close()

    def _inject_low_finding(self, store: StateStore) -> None:
        """绕过调用方断言注入一条 low confidence finding。"""
        pid = store.export_understanding()["pages"][0]["page_id"]
        findings = [{
            "claim": "orders 表可能承载订单聚合根",
            "confidence": "low",
            "kind": "mapping",
            "evidence_refs": ["pages:{0}".format(pid)],
        }]
        # exec + 伪装编排层模块名（replace_findings 单一入口契约的合法测试注入）
        g = {"__name__": "system_understanding", "store": store, "findings": findings}
        exec("store.replace_findings(findings)", g)

    def test_low_confidence_warning_prefix(self):
        """confidence=low → 渲染加"⚠ 待人工确认"前缀（§6.2 规则 4）。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            _seed(store)
            self._inject_low_finding(store)
            DocumentRenderer(store, _make_cfg(tmp)).render()
            text = (Path(tmp) / "legacy-test" / "UNDERSTANDING.md").read_text("utf-8")
            self.assertIn("⚠ 待人工确认：orders 表可能承载订单聚合根（confidence=low）", text)
            # 第 10 节 a) 低置信汇总同样收录
            section10 = text[text.find("## 10."):]
            self.assertIn("orders 表可能承载订单聚合根", section10)
            # summary.json confidence 分布计数
            summary = json.loads((Path(tmp) / "legacy-test" / "summary.json").read_text("utf-8"))
            self.assertEqual(summary["confidence_distribution"]["low"], 1)
            store.close()


class TestErMermaid(unittest.TestCase):
    """REQ-SU-018 AC2：ER 图推断 FK 标注与列折叠。"""

    def test_implicit_fk_inferred_label(self):
        """隐式 FK 候选 → er.mmd 关系标签匹配 `推断FK(包含度[\\d.]+)`。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            _seed(store)
            store.upsert_db_table(DbTableRedacted(
                schema_name="appdb", table_name="order_items", kind="BASE TABLE",
                columns=[redact({"name": "order_id", "data_type": "bigint",
                                 "type_family": "int"})]))
            store.insert_implicit_fk(ImplicitFkCandidateRedacted(
                child_table="appdb.order_items", child_column="order_id",
                parent_table="appdb.orders", parent_column="id",
                prescreen_score=0.9, stopped_at_rule=3, containment=0.92,
                evidence_json='{"说明": "包含度0.92达标（推断，需人工确认）"}'))
            DocumentRenderer(store, _make_cfg(tmp)).render()
            er = (Path(tmp) / "legacy-test" / "diagrams" / "er.mmd").read_text("utf-8")
            self.assertTrue(re.search(r"推断FK\(包含度[\d.]+\)", er), er)
            self.assertIn("appdb_orders", er)
            store.close()

    def test_explicit_fk_relation_line(self):
        """列级 fk_target → `||--o{` 显式 FK 关系行。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            store.upsert_db_table(DbTableRedacted(
                schema_name="appdb", table_name="orders", kind="BASE TABLE",
                columns=[redact({"name": "id", "data_type": "bigint",
                                 "type_family": "int", "is_pk": 1})]))
            store.upsert_db_table(DbTableRedacted(
                schema_name="appdb", table_name="order_items", kind="BASE TABLE",
                columns=[redact({"name": "order_id", "data_type": "bigint",
                                 "type_family": "int",
                                 "fk_target": "appdb.orders.id"})]))
            DocumentRenderer(store, _make_cfg(tmp)).render()
            er = (Path(tmp) / "legacy-test" / "diagrams" / "er.mmd").read_text("utf-8")
            self.assertIn('appdb_orders ||--o{ appdb_order_items : "FK order_id"', er)
            store.close()

    def test_column_truncation_marker(self):
        """列数 13 > 上限 12 → 属性行区追加 `_truncated "仅展示关键列`。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            cols = [redact({"name": "c{0}".format(i), "data_type": "int",
                            "type_family": "int"}) for i in range(13)]
            store.upsert_db_table(DbTableRedacted(
                schema_name="appdb", table_name="wide", kind="BASE TABLE", columns=cols))
            renderer = DocumentRenderer(store, _make_cfg(tmp))
            renderer.set_lens_status("db", "collected")  # 表卡片渲染前提
            renderer.render()
            er = (Path(tmp) / "legacy-test" / "diagrams" / "er.mmd").read_text("utf-8")
            self.assertIn('_truncated "仅展示关键列（共13列）"', er)
            md = (Path(tmp) / "legacy-test" / "UNDERSTANDING.md").read_text("utf-8")
            self.assertIn("仅展示关键列——前 12 列", md)
            store.close()


class TestNavigationMermaid(unittest.TestCase):
    """REQ-SU-018 §7.2：导航图平铺与折叠。"""

    def test_flat_under_threshold(self):
        """节点 ≤60 → 平铺无 subgraph；孤点清单输出。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            _seed(store)  # 1 页
            DocumentRenderer(store, _make_cfg(tmp)).render()
            nav = (Path(tmp) / "legacy-test" / "diagrams" / "navigation.mmd").read_text("utf-8")
            self.assertTrue(nav.startswith("graph TD"))
            self.assertNotIn("subgraph", nav)
            md = (Path(tmp) / "legacy-test" / "UNDERSTANDING.md").read_text("utf-8")
            # 首页无任何边 → 孤点
            self.assertIn("https://legacy.example.com/home", md)
            store.close()

    def test_fold_over_threshold(self):
        """节点 >60 → subgraph 分组 + 每组 >15 折叠计数节点。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            # 80 页（深度 1、同首段 'mod'）→ 单组 80 > NAV_GROUP_LIMIT 15
            for i in range(80):
                store.upsert_page(PageNodeRedacted(
                    url_key="https://legacy.example.com/mod/p{0}".format(i),
                    url="https://legacy.example.com/mod/p{0}".format(i), depth=1))
            DocumentRenderer(store, _make_cfg(tmp)).render()
            nav = (Path(tmp) / "legacy-test" / "diagrams" / "navigation.mmd").read_text("utf-8")
            self.assertIn("subgraph", nav)
            self.assertIn("+65 节点未展示", nav)  # 80-15
            store.close()


class TestMermaidSelfCheck(unittest.TestCase):
    """§7.2：坏图必须显式失败（内置正则自检前置）。"""

    def test_unbalanced_bracket_rejected(self):
        """未闭合方括号 → _validate_mermaid 抛 RuntimeError。"""
        from su.document_renderer import _validate_mermaid
        with self.assertRaises(RuntimeError):
            _validate_mermaid("navigation", 'graph TD\n    P1["未闭合\n')

    def test_bad_er_relation_rejected(self):
        """erDiagram 非法关系行 → RuntimeError。"""
        from su.document_renderer import _validate_mermaid
        with self.assertRaises(RuntimeError):
            _validate_mermaid("er", 'erDiagram\n    A -- B : "非法基数"\n')

    def test_valid_er_passes(self):
        """合法关系行（含中文标签）通过自检。"""
        from su.document_renderer import _validate_mermaid
        _validate_mermaid("er",
                          'erDiagram\n    A ||--o{ B : "推断FK(包含度0.92) x->y"\n')


class TestSection8ApiSurface(unittest.TestCase):
    """REQ-SU-018 第 8 节：拦截事件分类呈现。"""

    def test_blocked_events_split(self):
        """aborted_method 单独清单；其他 kind 输出统计行。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            _seed(store)  # aborted_method×1 + blocked_origin×1
            DocumentRenderer(store, _make_cfg(tmp)).render()
            text = (Path(tmp) / "legacy-test" / "UNDERSTANDING.md").read_text("utf-8")
            self.assertIn("`/api/orders`", text)
            self.assertIn("blocked_origin ×1", text)
            store.close()

    def test_no_blocked_events_declaration(self):
        """无拦截事件 → 显式"未观测到"声明。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            DocumentRenderer(store, _make_cfg(tmp)).render()
            text = (Path(tmp) / "legacy-test" / "UNDERSTANDING.md").read_text("utf-8")
            self.assertIn("未观测到被拦截的非 GET 请求", text)
            store.close()


if __name__ == "__main__":
    unittest.main()
