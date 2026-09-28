# -*- coding: utf-8 -*-
"""SU 能力单元测试：SQLite 状态机（REQ-SU-019 / REQ-SU-002 AC3）。

覆盖 su.state_store 模块（真临时 SQLite，不 mock 业务逻辑）：
- 锁与生命周期：acquire_lock 新建/新鲜拒绝/陈旧接管/interrupted resume；
  mark/heartbeat/set_config_snapshot 的 _require_run 前置校验
- RedactedDict 运行时断言：普通 dict 落库必抛 TypeError（红线①四层组合之②）
- 幂等写入：upsert_page 同 url_key 返同 id 且不覆盖进度、upsert_api_endpoint
  样本 FIFO ≤5、observed_on_pages 保序去重
- 红线⑤落库约束：T3+executed → ValueError；kind/tier 枚举校验
- findings：validate_findings_schema 中文违约收集、replace_findings 调用方
  栈断言（唯一合法调用方 = system_understanding 编排层）
- export_understanding：全 ORDER BY 主键、两次导出逐字段一致（幂等）
- stats/frontier 计数口径

运行方式（项目根目录）：
    python3 -B -m unittest scripts.tests.test_su_state
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path

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
    SuConfigError,
    SuLockHeldError,
)
from su.state_store import (  # noqa: E402
    HEARTBEAT_STALE_SECONDS,
    MARK_EXECUTED_BACKFILL_RULE,
    StateStore,
)


def _make_store(tmpdir: str, name: str = "state.sqlite") -> StateStore:
    """在临时目录构造 StateStore 并获取运行锁（多数用例的前置）。"""
    store = StateStore(Path(tmpdir) / name, "sys-state-test")
    store.acquire_lock(resume=False)
    return store


class TestLockLifecycle(unittest.TestCase):
    """REQ-SU-019：run 锁——新鲜拒绝 / 陈旧接管 / resume 复用。"""

    def test_new_run_created(self):
        """空库 acquire_lock → 新建 running 行，locked_by=pid 标记。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.sqlite", "sys-a")
            meta = store.acquire_lock(resume=False)
            self.assertEqual(meta["status"], "running")
            self.assertEqual(meta["system_id"], "sys-a")
            self.assertTrue(str(meta["locked_by"]).startswith("pid:"))
            self.assertEqual(meta["config_snapshot"], {})
            store.close()

    def test_fresh_lock_held_by_other_pid_rejected(self):
        """running + 心跳新鲜 + locked_by 非本进程 → SuLockHeldError。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.sqlite", "sys-a")
            store.acquire_lock(resume=False)
            # 伪造他进程持锁（心跳=现在）
            store._conn.execute(
                "UPDATE run_meta SET locked_by='pid:999999', heartbeat_ts=?",
                (time.time(),),
            )
            with self.assertRaises(SuLockHeldError) as ctx:
                store.acquire_lock(resume=False)
            self.assertIn("其他进程", ctx.exception.message)
            self.assertEqual(ctx.exception.exit_code, 2)
            store.close()

    def test_stale_running_takeover(self):
        """心跳超阈值（陈旧锁）→ 条件 UPDATE 原子接管，run_id 不变。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.sqlite", "sys-a")
            first = store.acquire_lock(resume=False)
            # 伪造陈旧心跳 + 他进程持有
            store._conn.execute(
                "UPDATE run_meta SET locked_by='pid:999999', heartbeat_ts=?",
                (time.time() - HEARTBEAT_STALE_SECONDS - 5,),
            )
            # 制造 exploring 残留页：接管时应重置 pending
            page_id = store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/1", url="https://a.test/1", depth=0))
            store.set_page_status(page_id, "exploring")
            second = store.acquire_lock(resume=False)
            self.assertEqual(second["run_id"], first["run_id"])  # 接管复用同一 run
            self.assertTrue(str(second["locked_by"]).startswith("pid:"))
            # exploring 页已重置 pending
            data = store.export_understanding()
            self.assertEqual(data["pages"][0]["status"], "pending")
            store.close()

    def test_interrupted_resume_reuses_run(self):
        """interrupted + resume=True → 复用 run 且 exploring 重置 pending。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.sqlite", "sys-a")
            first = store.acquire_lock(resume=False)
            page_id = store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/2", url="https://a.test/2", depth=1))
            store.set_page_status(page_id, "exploring")
            store.mark("interrupted", exit_reason="sigint")
            meta = store.acquire_lock(resume=True)
            self.assertEqual(meta["run_id"], first["run_id"])
            self.assertEqual(meta["status"], "running")
            self.assertIsNone(meta["exit_reason"])  # 复用时清空退出原因
            data = store.export_understanding()
            self.assertEqual(data["pages"][0]["status"], "pending")
            store.close()

    def test_interrupted_fresh_creates_new_run(self):
        """interrupted + resume=False → 新建 run（旧 run 行保留）。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.sqlite", "sys-a")
            first = store.acquire_lock(resume=False)
            store.mark("interrupted", exit_reason="sigint")
            second = store.acquire_lock(resume=False)
            self.assertNotEqual(second["run_id"], first["run_id"])
            rows = store._conn.execute("SELECT COUNT(*) n FROM run_meta").fetchone()
            self.assertEqual(int(rows["n"]), 2)
            store.close()

    def test_interrupted_fresh_archives_state_dir(self):
        """interrupted + resume=False 且 db 位于 <out>/state/ → 真实归档。

        2026-09-29 e2e 场景[4]根因修复回归锚：--fresh 归档语义收敛至
        acquire_lock 新建分支（CLI 层预读判据在 resume 已消费 interrupted
        的时序下永远落空）。断言链：
          - 归档目录 state.archive.<ts>/ 生成且旧库随目录整体迁入；
          - <out>/state/u.sqlite 重建且 run_meta 新库从零（仅 1 行 running）；
          - acquire_lock 事务契约完好（归档路径 COMMIT 后重开 BEGIN，
            否则收尾 COMMIT 报 "no transaction is active"）——mark 后续
            流转可正常提交即证明。
        """
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            db = out_dir / "state" / "u.sqlite"
            store = StateStore(db, "sys-a")
            first = store.acquire_lock(resume=False)
            store.mark("interrupted", exit_reason="sigint")
            store.close()
            # 新一轮 --fresh：acquire_lock 内部完成 归档→重连→重建→新建 run
            store2 = StateStore(db, "sys-a")
            meta = store2.acquire_lock(resume=False)
            self.assertEqual(meta["status"], "running")
            self.assertNotEqual(meta["run_id"], first["run_id"])
            archives = sorted(out_dir.glob("state.archive.*"))
            self.assertEqual(len(archives), 1)
            self.assertTrue((archives[0] / "u.sqlite").exists())
            # 新库从零：run_meta 仅本 run 一行
            rows = store2._conn.execute(
                "SELECT COUNT(*) n FROM run_meta").fetchone()
            self.assertEqual(int(rows["n"]), 1)
            # 事务契约验证：归档重开事务后普通写流转正常提交
            store2.mark("completed", exit_reason=None)
            self.assertEqual(
                store2._run_meta_row(store2._run_id)["status"], "completed")
            store2.close()

    def test_reentrant_same_pid_new_run(self):
        """本进程重入（locked_by==pid 标签且 running）→ 直接新建 run。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.sqlite", "sys-a")
            first = store.acquire_lock(resume=False)
            # 同进程二次 acquire_lock：locked_by 等于自身 pid 标签，不算他持
            second = store.acquire_lock(resume=False)
            self.assertNotEqual(second["run_id"], first["run_id"])
            store.close()

    def test_require_run_before_write_helpers(self):
        """未持锁调用 heartbeat/set_config_snapshot/mark → RuntimeError。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.sqlite", "sys-a")
            with self.assertRaises(RuntimeError):
                store.heartbeat()
            with self.assertRaises(RuntimeError):
                store.set_config_snapshot(RedactedDict({"a": 1}))
            with self.assertRaises(RuntimeError):
                store.mark("completed")
            store.close()

    def test_mark_enum_and_config_snapshot(self):
        """mark 非法枚举 ValueError；set_config_snapshot 必须 RedactedDict。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            with self.assertRaises(ValueError):
                store.mark("paused")
            with self.assertRaises(TypeError):
                store.set_config_snapshot({"plain": "dict"})  # 普通 dict 拒写
            snap = redact({"system": {"base_url": "https://a.test"}})
            store.set_config_snapshot(snap)
            meta = store._run_meta_row(store._run_id)
            self.assertEqual(meta["config_snapshot"]["system"]["base_url"], "https://a.test")
            store.mark("completed", exit_reason=None)
            meta = store._run_meta_row(store._run_id)
            self.assertEqual(meta["status"], "completed")
            self.assertIsNotNone(meta["finished_at"])
            store.close()

    def test_archive_and_reset_moves_state_dir(self):
        """archive_and_reset：state/ 原子改名 state.archive.<ts>/。"""
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            store = StateStore(out_dir / "state" / "u.sqlite", "sys-a")
            store.acquire_lock(resume=False)
            store.close()
            # 新实例执行归档（连接指向旧 inode 安全废弃）
            store2 = StateStore(out_dir / "state" / "u.sqlite", "sys-a")
            store2.archive_and_reset(out_dir)
            self.assertFalse((out_dir / "state").exists())
            archives = list(out_dir.glob("state.archive.*"))
            self.assertEqual(len(archives), 1)
            self.assertTrue((archives[0] / "u.sqlite").exists())

    def test_archive_missing_state_dir_noop(self):
        """state/ 不存在时 archive_and_reset 静默返回（不抛）。"""
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            store = StateStore(out_dir / "elsewhere" / "u.sqlite", "sys-a")
            store.archive_and_reset(out_dir)  # 无 state/ 子目录 → no-op
            store.close()

    def test_schema_version_mismatch_rejected(self):
        """库内 schema 版本与代码不一致 → 初始化 RuntimeError。"""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "s.sqlite"
            store = StateStore(db, "sys-a")
            store._conn.execute("UPDATE schema_meta SET version=999")
            store.close()
            with self.assertRaises(RuntimeError):
                StateStore(db, "sys-a")


class TestRedactedEnforcement(unittest.TestCase):
    """REQ-SU-002 AC3：写盘入口 RedactedDict 运行时断言（红线①）。"""

    def test_endpoint_shape_plain_dict_rejected(self):
        """response_shape 普通 dict → TypeError（未脱敏禁止落库）。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/p", url="https://a.test/p", depth=0))
            with self.assertRaises(TypeError) as ctx:
                store.upsert_api_endpoint(ApiObservationRedacted(
                    url_path="/api/x", method="GET", observed_on_page=1, ts=1.0,
                    response_shape={"plain": "dict"}))
            self.assertIn("RedactedDict", str(ctx.exception))
            store.close()

    def test_db_table_column_plain_dict_rejected(self):
        """columns 混入普通 dict → TypeError 且标明下标。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            with self.assertRaises(TypeError) as ctx:
                store.upsert_db_table(DbTableRedacted(
                    schema_name="d", table_name="t", kind="BASE TABLE",
                    columns=[{"name": "id"}]))
            self.assertIn("t.columns[0]", str(ctx.exception))
            store.close()

    def test_samples_plain_dict_rejected(self):
        """insert_samples 行级断言（rows[1] 普通 dict 拒写）。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            store.upsert_db_table(DbTableRedacted(
                schema_name="d", table_name="users", kind="BASE TABLE",
                columns=[redact({"name": "id", "data_type": "int", "type_family": "int"})]))
            with self.assertRaises(TypeError):
                store.insert_samples("d.users", [redact({"id": 1}), {"id": 2}])
            store.close()

    def test_relation_evidence_plain_dict_rejected(self):
        """insert_relation evidence 普通 dict → TypeError。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            with self.assertRaises(TypeError):
                store.insert_relation(RelationRedacted(
                    rtype="page_api", left_ref="pages:1", right_ref="api:1",
                    score=1.0, evidence={"plain": "dict"}))
            store.close()

    def test_redis_pattern_row_plain_dict_rejected(self):
        """replace_redis_patterns 行级 RedactedDict 断言。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            with self.assertRaises(TypeError):
                store.replace_redis_patterns([{"pattern": "a:{n}"}])
            store.close()


class TestIdempotentWrites(unittest.TestCase):
    """REQ-SU-019：幂等写入（采集先查后采 / resume 不重复）。"""

    def test_upsert_page_same_id_and_status_preserved(self):
        """同 url_key 二次 upsert → 同 page_id，进度状态不被覆盖。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            node = PageNodeRedacted(url_key="https://a.test/q", url="https://a.test/q", depth=0)
            pid1 = store.upsert_page(node)
            store.set_page_status(pid1, "done", snapshot_path="snap/1.json")
            # 二次 upsert（status 传 pending 也不得回退 done）
            pid2 = store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/q", url="https://a.test/q", depth=0,
                status="pending", title="补标题"))
            self.assertEqual(pid1, pid2)
            self.assertTrue(store.page_done("https://a.test/q"))
            data = store.export_understanding()
            self.assertEqual(data["pages"][0]["title"], "补标题")  # 非进度字段刷新
            store.close()

    def test_endpoint_samples_fifo_max_five(self):
        """同端点 7 次观测 → 样本 FIFO 截断保留最新 5 条。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/r", url="https://a.test/r", depth=0))
            for i in range(7):
                store.upsert_api_endpoint(ApiObservationRedacted(
                    url_path="/api/feed", method="GET", observed_on_page=1,
                    ts=float(100 + i), status=200))
            data = store.export_understanding()
            ep = data["endpoints"][0]
            self.assertEqual(ep["sample_count"], 5)
            self.assertEqual(len(ep["samples"]), 5)
            # FIFO 丢最旧：保留的样本 ts 应为 102..106
            ts_list = [s["ts"] for s in ep["samples"]]
            self.assertEqual(ts_list, [102.0, 103.0, 104.0, 105.0, 106.0])
            # latest_status 以最新观测覆盖
            self.assertEqual(ep["latest_status"], 200)
            store.close()

    def test_observed_on_pages_order_preserved_dedup(self):
        """多页观测 → observed_on_pages 保序去重，首元素=首次观测页。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            p1 = store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/1", url="https://a.test/1", depth=0))
            p2 = store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/2", url="https://a.test/2", depth=0))
            store.upsert_api_endpoint(ApiObservationRedacted(
                url_path="/api/x", method="GET", observed_on_page=p2, ts=1.0))
            store.upsert_api_endpoint(ApiObservationRedacted(
                url_path="/api/x", method="GET", observed_on_page=p1, ts=2.0))
            # 重复观测 p2 → 不重复追加
            store.upsert_api_endpoint(ApiObservationRedacted(
                url_path="/api/x", method="GET", observed_on_page=p2, ts=3.0))
            data = store.export_understanding()
            self.assertEqual(data["endpoints"][0]["observed_on_pages"], [p2, p1])
            store.close()

    def test_edges_unique_ignored(self):
        """edges UNIQUE(from_key,to_key,via_action) 显式列全冲突 → 忽略。

        实现事实（SQLite 语义锁定）：UNIQUE 约束含可空列 via_action 时，
        NULL != NULL，(a,b,NULL) 重复插入**不会**冲突去重（INSERT OR IGNORE
        照插）；只有非 NULL 的完整元组重复才被忽略。
        """
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            pid = store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/s", url="https://a.test/s", depth=0))
            # via_action 引用真实 action_id（edges.via_action 外键 → page_actions）
            store.insert_action(ActionDecisionRedacted(
                page_id=pid, element_sig='{"text":"go"}', tier="T1", rule_name="同域链接"))
            action_id = store.export_understanding()["pages"][0]["actions"][0]["action_id"]
            for _ in range(3):
                store.insert_edge(EdgeRedacted(from_key="a", to_key="b", via_action=action_id))
            self.assertEqual(store.stats()["edges_total"], 1)
            # NULL 边不去重（锁定 SQLite NULL 语义实现事实）
            for _ in range(2):
                store.insert_edge(EdgeRedacted(from_key="c", to_key="d", via_action=None))
            self.assertEqual(store.stats()["edges_total"], 3)
            store.close()

    def test_action_unique_ignored(self):
        """page_actions UNIQUE(page_id,element_sig) 同页重复发现不重复入库。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            pid = store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/s", url="https://a.test/s", depth=0))
            for _ in range(3):
                store.insert_action(ActionDecisionRedacted(
                    page_id=pid, element_sig='{"text":"btn"}', tier="T1",
                    rule_name="同域链接"))
            self.assertEqual(store.stats()["actions_total"], 1)
            store.close()

    def test_insert_samples_replaces_old_rows(self):
        """insert_samples 快照语义：重采以最新为准（先清后写）。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            store.upsert_db_table(DbTableRedacted(
                schema_name="d", table_name="cfg", kind="BASE TABLE",
                columns=[redact({"name": "k", "data_type": "varchar", "type_family": "string"})]))
            store.insert_samples("d.cfg", [redact({"k": "old1"}), redact({"k": "old2"})])
            store.insert_samples("d.cfg", [redact({"k": "new1"})])
            data = store.export_understanding()
            samples = data["db_tables"][0]["samples"]
            self.assertEqual(len(samples), 1)
            self.assertEqual(samples[0]["k"], "new1")
            store.close()

    def test_insert_samples_unknown_table(self):
        """目标表未 upsert → ValueError 提示先建表。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            with self.assertRaises(ValueError):
                store.insert_samples("d.missing", [redact({"k": 1})])
            store.close()


class TestSafetyConstraints(unittest.TestCase):
    """红线⑤落库约束与枚举校验。"""

    def test_t3_executed_rejected(self):
        """T3 + executed=1 → ValueError（红线⑤：危险动作零执行）。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            pid = store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/t", url="https://a.test/t", depth=0))
            with self.assertRaises(ValueError):
                store.insert_action(ActionDecisionRedacted(
                    page_id=pid, element_sig="{}", tier="T3",
                    rule_name="危险动词", executed=1))
            store.close()

    def test_tier_enum_rejected(self):
        """tier 非法值 → ValueError（T3 检查通过后再验 tier 枚举）。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            pid = store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/t", url="https://a.test/t", depth=0))
            with self.assertRaises(ValueError):
                store.insert_action(ActionDecisionRedacted(
                    page_id=pid, element_sig="{}", tier="T4", rule_name="x"))
            store.close()

    def test_blocked_event_kind_enum(self):
        """blocked_events.kind 非法值 → ValueError。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            with self.assertRaises(ValueError):
                store.insert_blocked_event(BlockedEventRedacted(
                    kind="weird", url="/x", ts=1.0))
            # 合法 kind 正常写入
            store.insert_blocked_event(BlockedEventRedacted(
                kind="blocked_origin", url="/x", ts=1.0))
            store.close()

    def test_page_status_enum(self):
        """set_page_status 非法状态 → ValueError。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            pid = store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/e", url="https://a.test/e", depth=0))
            with self.assertRaises(ValueError):
                store.set_page_status(pid, "finished")
            store.close()


class TestFindings(unittest.TestCase):
    """REQ-SU-016 AC2 / §6.2：findings 校验与单一入口入库。"""

    def _valid_findings(self, store: StateStore):
        """构造引用真实存在的 evidence_ref 的合法 findings。"""
        pid = store.upsert_page(PageNodeRedacted(
            url_key="https://a.test/f", url="https://a.test/f", depth=0))
        return [{
            "claim": "orders 表承载订单实体",
            "confidence": "high",
            "kind": "mapping",
            "evidence_refs": ["pages:{0}".format(pid)],
        }]

    def test_validate_ok_empty_errors(self):
        """合法 findings → 校验返回空列表。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            self.assertEqual(store.validate_findings_schema(self._valid_findings(store)), [])
            store.close()

    def test_validate_collects_all_violations(self):
        """多违约一次性收集：claim 空白/confidence 非法/kind 非法/refs 空。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            errors = store.validate_findings_schema([{
                "claim": "   ",
                "confidence": "certain",
                "kind": "vibe",
                "evidence_refs": [],
            }])
            self.assertEqual(len(errors), 4)
            joined = "；".join(errors)
            self.assertIn("claim 缺失或为空白", joined)
            self.assertIn("confidence 缺失或非法", joined)
            self.assertIn("kind 缺失或非法", joined)
            self.assertIn("evidence_refs 必须为 ≥1 条的数组", joined)
            store.close()

    def test_validate_credential_in_claim(self):
        """claim 含键值对形态凭据（token=xxx）→ 拒绝。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            pid = store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/g", url="https://a.test/g", depth=0))
            errors = store.validate_findings_schema([{
                "claim": "登录接口返回 token=abc123def",
                "confidence": "high",
                "kind": "business_rule",
                "evidence_refs": ["pages:{0}".format(pid)],
            }])
            self.assertEqual(len(errors), 1)
            self.assertIn("键值对形态凭据", errors[0])
            # 收窄口径：业务文本提及"token 列"不构成违约
            errors2 = store.validate_findings_schema([{
                "claim": "sessions 表含 token 列",
                "confidence": "medium",
                "kind": "semantic_name",
                "evidence_refs": ["pages:{0}".format(pid)],
            }])
            self.assertEqual(errors2, [])
            store.close()

    def test_validate_evidence_ref_existence(self):
        """evidence_ref 指向不存在的 id / 未知表 → 拒绝。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            errors = store.validate_findings_schema([{
                "claim": "x", "confidence": "low", "kind": "mapping",
                "evidence_refs": ["pages:9999", "nonexistent:1", "pages:notanumber"],
            }])
            self.assertEqual(len(errors), 3)
            joined = "；".join(errors)
            self.assertIn("在库中不存在", joined)
            self.assertIn("未知引用表", joined)
            self.assertIn("id 必须是数字", joined)
            store.close()

    def test_replace_findings_wrong_caller_permission_error(self):
        """非编排层调用 → PermissionError（单一入口契约）。

        实现事实：调用方模块名取自调用帧 ``f_globals['__name__']``（exec 的
        代码对象默认 globals 为调用方环境）。以 __name__='scripts.tests' 的
        globals 执行 exec 代码 → 必然被拒；成功路径见下一用例（伪装
        'system_understanding'）。
        """
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            findings = self._valid_findings(store)
            bad_globals = {"__name__": "scripts.tests", "store": store,
                           "findings": findings}
            with self.assertRaises(PermissionError) as ctx:
                exec("store.replace_findings(findings)", bad_globals)
            self.assertIn("system_understanding", ctx.exception.args[0])
            # 违约拒绝后库内保持原状（本用例未成功入库过任何 findings）
            self.assertEqual(store.export_understanding()["findings"], [])
            store.close()

    def test_replace_findings_via_orchestrator_globals(self):
        """注入 system_understanding 调用方语境后成功入库 status=proposed。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            findings = self._valid_findings(store)
            # 用独立函数承载调用帧，把其模块 __name__ 伪装为编排层
            def orchestrator_call():
                store.replace_findings(findings)
            orchestrator_call.__globals__["__name__"] = "system_understanding"
            # 注入后函数体经 __globals__ 查名——必须经模块属性访问才生效：
            # 直接闭包引用 store 不受影响，但 inspect 读的是 f.__globals__
            orchestrator_call()
            data = store.export_understanding()
            self.assertEqual(len(data["findings"]), 1)
            self.assertEqual(data["findings"][0]["status"], "proposed")
            self.assertEqual(data["findings"][0]["evidence_refs"][0][:5], "pages")
            store.close()

    def test_replace_findings_batch_reject_keeps_old(self):
        """校验失败 → SuConfigError 整批拒绝，旧 findings 不被清空。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            good = self._valid_findings(store)

            def orch_good():
                store.replace_findings(good)
            orch_good.__globals__["__name__"] = "system_understanding"
            orch_good()

            def orch_bad():
                store.replace_findings([{"claim": "ok", "confidence": "high",
                                         "kind": "mapping", "evidence_refs": []},
                                        {"claim": "", "confidence": "x",
                                         "kind": "y", "evidence_refs": []}])
            orch_bad.__globals__["__name__"] = "system_understanding"
            with self.assertRaises(SuConfigError) as ctx:
                orch_bad()
            self.assertIn("整批拒绝", ctx.exception.message)
            self.assertEqual(ctx.exception.exit_code, 2)
            # 旧数据仍在
            data = store.export_understanding()
            self.assertEqual(len(data["findings"]), 1)
            store.close()

    def test_replace_findings_full_replacement(self):
        """成功路径为全量替换：新批次覆盖旧批次（DELETE 全表再 INSERT）。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            first = self._valid_findings(store)
            second = [{
                "claim": "redis user:{n} 缓存用户会话",
                "confidence": "medium", "kind": "redis_entity",
                "evidence_refs": ["pages:1"],
            }]

            def orch(batch):
                store.replace_findings(batch)
            orch.__globals__["__name__"] = "system_understanding"
            orch(first)
            orch(second)
            data = store.export_understanding()
            self.assertEqual(len(data["findings"]), 1)
            self.assertEqual(data["findings"][0]["kind"], "redis_entity")
            store.close()


class TestExportAndStats(unittest.TestCase):
    """§6.1 / §7.3：导出幂等（纯函数视图）与统计口径。"""

    def _seed(self, store: StateStore) -> None:
        """写入覆盖全部透镜的最小事实集。"""
        pid = store.upsert_page(PageNodeRedacted(
            url_key="https://a.test/home", url="https://a.test/home", depth=0,
            title="首页"))
        store.insert_action(ActionDecisionRedacted(
            page_id=pid, element_sig='{"text":"登录"}', tier="T2",
            rule_name="显式GET表单", executed=1))
        store.insert_edge(EdgeRedacted(from_key="https://a.test/home", to_key="https://a.test/list"))
        store.upsert_api_endpoint(ApiObservationRedacted(
            url_path="/api/orders", method="GET", observed_on_page=pid, ts=1.0,
            status=200, response_shape=redact({"id": 1, "status": "new"})))
        store.upsert_db_table(DbTableRedacted(
            schema_name="appdb", table_name="orders", kind="BASE TABLE",
            columns=[redact({"name": "id", "data_type": "bigint",
                             "type_family": "int", "is_pk": 1})]))
        store.insert_samples("appdb.orders", [redact({"id": 1})])
        store.upsert_redis_key(RedisKeyRecordRedacted(
            key_name="user:1", key_type="string", ttl_ms=-1))
        store.replace_redis_patterns([redact({
            "pattern": "user:{n}", "key_count": 1, "no_ttl_ratio": 1.0,
            "ttl_summary": {}, "type_summary": {}, "sample_keys": []})])
        store.insert_relation(RelationRedacted(
            rtype="page_api", left_ref="pages:1", right_ref="api:1",
            score=1.0, evidence=redact({"依据": "观测直连"})))
        store.insert_blocked_event(BlockedEventRedacted(
            kind="aborted_method", url="/api/orders", method="DELETE", ts=1.0))

    def test_export_idempotent_byte_identical(self):
        """同一库状态不变时两次 export 完全一致（渲染幂等前提）。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            self._seed(store)
            first = store.export_understanding()
            second = store.export_understanding()
            self.assertEqual(first, second)
            store.close()

    def test_export_shape_key_sets(self):
        """export 顶层键完整（全透镜 + meta + findings + blocked_events）。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            self._seed(store)
            data = store.export_understanding()
            for key in ("meta", "pages", "edges", "endpoints", "db_tables",
                        "implicit_fk_candidates", "redis_patterns", "redis_keys",
                        "relations", "findings", "blocked_events"):
                self.assertIn(key, data)
            self.assertIsInstance(data, RedactedDict)
            # observed_on_pages 导出为 int 列表（存储是逗号文本）
            self.assertEqual(data["endpoints"][0]["observed_on_pages"], [1])
            # 页内嵌 actions
            self.assertEqual(data["pages"][0]["actions"][0]["tier"], "T2")
            store.close()

    def test_export_empty_store(self):
        """空库导出：各透镜为空列表，meta 的 run 字段可用。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            data = store.export_understanding()
            self.assertEqual(data["pages"], [])
            self.assertEqual(data["endpoints"], [])
            self.assertEqual(data["relations"], [])
            self.assertEqual(data["meta"]["system_id"], "sys-state-test")
            self.assertIsNotNone(data["meta"]["run_id"])  # 持锁后 meta 带 run
            store.close()

    def test_stats_counts(self):
        """stats 各计数与写入事实一致。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            self._seed(store)
            stats = store.stats()
            self.assertEqual(stats["pages_total"], 1)
            self.assertEqual(stats["actions_total"], 1)
            self.assertEqual(stats["edges_total"], 1)
            self.assertEqual(stats["endpoints_total"], 1)
            self.assertEqual(stats["db_tables_total"], 1)
            self.assertEqual(stats["redis_keys_total"], 1)
            self.assertEqual(stats["redis_patterns_total"], 1)
            self.assertEqual(stats["relations_total"], 1)
            self.assertEqual(stats["blocked_events_total"], 1)
            self.assertEqual(stats["findings_total"], 0)
            store.close()

    def test_frontier_pending_and_exploring_only(self):
        """frontier 只含 pending/exploring，按 depth 排序。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            p1 = store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/1", url="https://a.test/1", depth=0))
            p2 = store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/2", url="https://a.test/2", depth=1))
            p3 = store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/3", url="https://a.test/3", depth=2))
            store.set_page_status(p1, "done")
            store.set_page_status(p3, "exploring")
            fr = store.frontier()
            keys = [f["url_key"] for f in fr]
            # p1 done 排除；p2 pending、p3 exploring 保留，深度升序
            self.assertEqual(keys, ["https://a.test/2", "https://a.test/3"])
            self.assertEqual(fr[1]["status"], "exploring")
            store.close()

    def test_unfinished_pages_filter_and_order(self):
        """unfinished_pages：status ≠ done 全收，按 page_id ASC（BFS 发现序）。

        2026-09-29 e2e 场景[4]根因修复回归锚：--resume 队列重建的数据源
        口径。与 frontier（仅 pending/exploring、按 depth 排序）区分——
        error/timeout 页也要重放（interrupted 库可续采语义），排序必须是
        发现序而非深度序，否则重放后深度/父子溯源乱序。
        """
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            p1 = store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/a", url="https://a.test/a", depth=0))
            p2 = store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/b", url="https://a.test/b", depth=1))
            p3 = store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/c", url="https://a.test/c", depth=1))
            p4 = store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/d", url="https://a.test/d", depth=2))
            store.set_page_status(p1, "done")      # 已采完 → 排除
            store.set_page_status(p3, "error", error="navigate_failed")
            store.set_page_status(p4, "timeout", error="wait_ready_timeout")
            rows = store.unfinished_pages()
            # p2 pending、p3 error、p4 timeout 全收；排序 page_id ASC
            self.assertEqual([r["page_id"] for r in rows], [p2, p3, p4])
            self.assertEqual(
                [r["status"] for r in rows], ["pending", "error", "timeout"])
            # 行字段完整性（crawler._requeue_unfinished 依赖 url/depth/父溯源）
            self.assertEqual(rows[0]["url"], "https://a.test/b")
            self.assertEqual(rows[0]["depth"], 1)
            self.assertIsNone(rows[0]["discover_from"])
            store.close()


class TestMarkActionExecuted(unittest.TestCase):
    """mark_action_executed "执行事实优先"补录语义（2026-09-28 e2e 场景[2]）。"""

    def test_backfill_when_tier_row_missing(self):
        """分级行缺失 → 补录 T2/executed=1 行，rule_name=补录占位，返回 1。

        T2 执行时序先于分级落库（crawler 主循环 _execute_t2_forms 在
        _classify_and_dispatch 之前），executed 回写时目标行可能尚未插入；
        "已真实执行"是事实必须可观测，静默 0 行即观测断链。
        """
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            pid = store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/f", url="https://a.test/f", depth=0))
            affected = store.mark_action_executed(pid, '{"sig":"missing"}')
            self.assertEqual(affected, 1)
            row = store._conn.execute(
                "SELECT tier, rule_name, executed FROM page_actions"
                " WHERE page_id=? AND element_sig=?",
                (pid, '{"sig":"missing"}')).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["tier"], "T2")
            self.assertEqual(row["executed"], 1)
            self.assertEqual(row["rule_name"], MARK_EXECUTED_BACKFILL_RULE)
            store.close()

    def test_update_existing_t2_row(self):
        """既有 T2 行 → 直接 UPDATE executed=1（不产生补录行）。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            pid = store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/g", url="https://a.test/g", depth=0))
            store.insert_action(ActionDecisionRedacted(
                page_id=pid, element_sig='{"sig":"t2"}', tier="T2",
                rule_name="显式 GET 表单"))
            affected = store.mark_action_executed(pid, '{"sig":"t2"}')
            self.assertEqual(affected, 1)
            rows = store._conn.execute(
                "SELECT rule_name, executed FROM page_actions"
                " WHERE page_id=? AND element_sig=?",
                (pid, '{"sig":"t2"}')).fetchall()
            # 只有一行（补录 INSERT OR IGNORE 不重复插入），rule_name 保持分级器原值
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["rule_name"], "显式 GET 表单")
            self.assertEqual(rows[0]["executed"], 1)
            store.close()

    def test_t3_row_rejected(self):
        """目标行 tier='T3' → ValueError（红线⑤：T3 永不标记 executed）。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            pid = store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/h", url="https://a.test/h", depth=0))
            store.insert_action(ActionDecisionRedacted(
                page_id=pid, element_sig='{"sig":"t3"}', tier="T3",
                rule_name="危险动词"))
            with self.assertRaises(ValueError):
                store.mark_action_executed(pid, '{"sig":"t3"}')
            # 拒绝后 T3 行 executed 恒 0（红线⑤落库约束不被破坏）
            row = store._conn.execute(
                "SELECT executed FROM page_actions"
                " WHERE page_id=? AND element_sig=?",
                (pid, '{"sig":"t3"}')).fetchone()
            self.assertEqual(row["executed"], 0)
            store.close()


if __name__ == "__main__":
    unittest.main()
