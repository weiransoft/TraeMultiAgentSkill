#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2.13 数据校验闭环端到端测试（真实 SQLite + 真实记录器 CLI 子进程）。

覆盖范围（与 docs/dev/DATA_CONSISTENCY_TESTING.md 约定一一对应）：
  1. 内核纯函数：行级快照 diff / DC 预期匹配 / 规则引擎（UNIQ/INV/CONS/SM/REF）；
  2. CLI 全链路：run → case → step → db-snapshot(before/after) → rule →
     dc-check → case end → finalize（SQLite 临时库真实读写，零 mock）；
  3. 数据门禁：契约 FAIL → data_gate=FAIL → 总 gate=FAIL；
     声明 --db-check-* 却零快照 → 记录器拒绝收尾（stderr 报错 + 非零退出）；
  4. v2.12 回归：纯 UI 用例（未启用数据校验）行为完全不变。

运行：python3 -m pytest tests/test_v213_data_consistency.py -q
"""
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
RECORDER = SCRIPTS / "browser_test_recorder.py"
sys.path.insert(0, str(SCRIPTS))

from data_test.data_rules import (  # noqa: E402
    diff_matches_expectation,
    diff_snapshots,
    evaluate_rules,
)


def _rec(*argv, out=None):
    """以子进程方式调用记录器 CLI，返回 (returncode, stdout_json, stderr_text)。"""
    cmd = [sys.executable, str(RECORDER)]
    if out:
        cmd += ["--out", str(out)]
    cmd += [str(a) for a in argv]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        payload = json.loads(r.stdout)
    except json.JSONDecodeError:
        payload = {"raw": r.stdout}
    return r.returncode, payload, r.stderr


class TestKernelDiff(unittest.TestCase):
    """行级快照 diff 与 DC 预期匹配（data_test 确定性内核）。"""

    def test_diff_added_removed_changed(self):
        before = [{"id": 1, "status": "new"}, {"id": 2, "status": "new"}]
        after = [{"id": 2, "status": "paid"}, {"id": 3, "status": "new"}]
        d = diff_snapshots(before, after, ["id"])
        self.assertEqual([r["id"] for r in d["added"]], [3])
        self.assertEqual([r["id"] for r in d["removed"]], [1])
        self.assertEqual(d["before_count"], 2)
        self.assertEqual(d["after_count"], 2)
        ch = d["changed"]
        self.assertEqual(len(ch), 1)
        self.assertEqual(ch[0]["key"], ["2"])
        cd = {c["column"]: (c["before"], c["after"]) for c in ch[0]["changed_columns"]}
        self.assertEqual(cd["status"], ("new", "paid"))

    def test_expectation_counts_and_columns(self):
        before = [{"id": 1, "status": "new"}]
        after = [{"id": 1, "status": "paid"}, {"id": 2, "status": "new"}]
        d = diff_snapshots(before, after, ["id"])
        ok, _ = diff_matches_expectation(
            d, {"row_delta": 1, "added_count": 1, "removed_count": 0,
                "changed_count": 1, "expected_columns": {"id": 1, "status": "paid"}})
        self.assertTrue(ok)
        ok2, reason = diff_matches_expectation(d, {"row_delta": 5})
        self.assertFalse(ok2)
        self.assertIn("行数增量", reason)
        ok3, _ = diff_matches_expectation(d, {"expected_columns": {"status": "cancelled"}})
        self.assertFalse(ok3)


class TestKernelRules(unittest.TestCase):
    """规则引擎求值器（结构非法拒绝、数据违例判假）。"""

    SNAP = {
        "orders": {"rows": [{"id": 1, "status": "new", "total": 100},
                            {"id": 2, "status": "paid", "total": 200}]},
        "orders_after": {"rows": [{"id": 1, "status": "paid", "total": 100},
                                  {"id": 2, "status": "paid", "total": 200}]},
        "stats_new": {"rows": [{"cnt": 1}]},
        "users": {"rows": [{"id": 1}, {"id": 2}]},
    }

    def _one(self, rule):
        return evaluate_rules([rule], self.SNAP)[0]

    def test_uniq_pass_and_fail(self):
        ok = self._one({"rule_id": "U1", "type": "UNIQ",
                        "snapshot": "orders", "column": "id"})
        self.assertTrue(ok["pass"])
        bad = self._one({"rule_id": "U2", "type": "UNIQ",
                         "snapshot": "orders_after", "column": "status"})
        self.assertFalse(bad["pass"])

    def test_inv_rowwise(self):
        ok = self._one({"rule_id": "I1", "type": "INV",
                        "snapshot": "orders", "expr": "total > 0"})
        self.assertTrue(ok["pass"])
        bad = self._one({"rule_id": "I2", "type": "INV",
                         "snapshot": "orders", "expr": "total > 150"})
        self.assertFalse(bad["pass"])
        self.assertEqual(bad["violations"][0]["row_data"]["id"], 1)

    def test_cons_binding(self):
        ok = self._one({"rule_id": "C1", "type": "CONS",
                        "vars": {"cnt": "stats_new.cnt"},
                        "expr": "cnt == 1"})
        self.assertTrue(ok["pass"])

    def test_sm_transition_table(self):
        ok = self._one({"rule_id": "S1", "type": "SM",
                        "before_snapshot": "orders", "after_snapshot": "orders_after",
                        "column": "status", "key_columns": ["id"],
                        "transitions": [["new", "paid"]]})
        self.assertTrue(ok["pass"])
        bad = self._one({"rule_id": "S2", "type": "SM",
                         "before_snapshot": "orders", "after_snapshot": "orders_after",
                         "column": "status", "key_columns": ["id"],
                         "transitions": [], "allow_same": False})
        self.assertFalse(bad["pass"])

    def test_ref_integrity(self):
        ok = self._one({"rule_id": "R1", "type": "REF",
                        "child_snapshot": "orders", "child_column": "id",
                        "parent_snapshot": "users", "parent_column": "id"})
        self.assertTrue(ok["pass"])


class TestEndToEndSQLite(unittest.TestCase):
    """真实 SQLite 全链路：before 快照 → 业务写库 → after 快照 → 规则/契约 → 门禁。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.out = self.root / "runs"
        self.db = self.root / "app.db"
        conn = sqlite3.connect(str(self.db))
        conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, "
                     "status TEXT, total REAL)")
        conn.execute("INSERT INTO orders VALUES (1,'new',100)")
        conn.execute("INSERT INTO orders VALUES (2,'new',200)")
        conn.commit()
        conn.close()
        self.cfg = self.root / "db-config.json"
        self.cfg.write_text(json.dumps({"engine": "sqlite", "path": str(self.db)}))
        sql = "SELECT id,status,total FROM orders ORDER BY id"
        self.plan_before = self.root / "check-before.json"
        self.plan_before.write_text(json.dumps({
            "check_id": "orders-list", "queries": [{"label": "orders", "sql": sql}]}))
        self.plan_after = self.root / "check-after.json"
        self.plan_after.write_text(json.dumps({
            "check_id": "orders-list", "queries": [{"label": "orders", "sql": sql}]}))
        self.rules = self.root / "rules.json"
        self.rules.write_text(json.dumps({"rules": [
            {"rule_id": "BR-1", "type": "INV", "snapshot": "orders", "expr": "total > 0"},
        ]}))

    def tearDown(self):
        self.tmp.cleanup()

    def _base_case(self, run_id, case_id, with_checks=True):
        """跑标准用例骨架到 step 完成（可选挂 DB 前后清单）。"""
        rc, _, _ = _rec("run", "--run-id", run_id, "--db-config", str(self.cfg),
                        out=self.out)
        self.assertEqual(rc, 0)
        argv = ["case", "--run-id", run_id, "--case-id", case_id,
                "--action", "start", "--priority", "P0"]
        if with_checks:
            argv += ["--db-check-before", str(self.plan_before),
                     "--db-check-after", str(self.plan_after)]
        rc, _, _ = _rec(*argv, out=self.out)
        self.assertEqual(rc, 0)
        rc, _, _ = _rec("step", "--run-id", run_id, "--case-id", case_id,
                        "--action-name", "navigate", "--target", "/orders",
                        out=self.out)
        self.assertEqual(rc, 0)

    def test_full_data_gate_pass(self):
        run_id, case_id = "e2e-pass", "TC-001"
        self._base_case(run_id, case_id)
        rc, p, _ = _rec("db-snapshot", "--run-id", run_id, "--case-id", case_id,
                        "--check-file", str(self.plan_before), "--phase", "before",
                        out=self.out)
        self.assertEqual(rc, 0, p)
        conn = sqlite3.connect(str(self.db))
        conn.execute("INSERT INTO orders VALUES (3,'new',300)")
        conn.commit()
        conn.close()
        rc, p, _ = _rec("db-snapshot", "--run-id", run_id, "--case-id", case_id,
                        "--check-file", str(self.plan_after), "--phase", "after",
                        out=self.out)
        self.assertEqual(rc, 0, p)
        rc, p, _ = _rec("rule", "--run-id", run_id, "--case-id", case_id,
                        "--rules", str(self.rules), out=self.out)
        self.assertEqual(rc, 0, p)
        self.assertEqual(p.get("passed"), 1, p)
        rc, p, _ = _rec("dc-check", "--run-id", run_id, "--case-id", case_id,
                        "--contract-id", "DC-001", "--key-columns", "id",
                        "--expectation", json.dumps(
                            {"row_delta": 1, "added_count": 1, "removed_count": 0,
                             "changed_count": 0}), out=self.out)
        self.assertEqual(rc, 0, p)
        self.assertTrue(p.get("pass"), p)
        rc, _, _ = _rec("case", "--run-id", run_id, "--case-id", case_id,
                        "--action", "end", "--status", "pass", out=self.out)
        self.assertEqual(rc, 0)
        rc, p, _ = _rec("finalize", "--run-id", run_id, out=self.out)
        self.assertEqual(rc, 0, p)
        self.assertEqual(p.get("gate"), "PASS", p)
        self.assertEqual(p.get("data_gate"), "PASS", p)
        s = json.loads((self.out / run_id / "summary.json").read_text())
        self.assertEqual(s["stats"]["data_snapshots"], 2)
        self.assertEqual(s["stats"]["data_snapshot_files"], 2)
        self.assertEqual(s["stats"]["dc_total"], 1)
        self.assertEqual(s["stats"]["dc_pass"], 1)
        self.assertEqual(s["stats"]["rules_total"], 1)
        self.assertEqual(s["stats"]["rules_pass"], 1)

    def test_data_fail_makes_gate_fail(self):
        run_id, case_id = "e2e-fail", "TC-002"
        self._base_case(run_id, case_id)
        _rec("db-snapshot", "--run-id", run_id, "--case-id", case_id,
             "--check-file", str(self.plan_before), "--phase", "before", out=self.out)
        _rec("db-snapshot", "--run-id", run_id, "--case-id", case_id,
             "--check-file", str(self.plan_after), "--phase", "after", out=self.out)
        rc, p, _ = _rec("dc-check", "--run-id", run_id, "--case-id", case_id,
                        "--contract-id", "DC-X", "--key-columns", "id",
                        "--expectation", json.dumps({"added_count": 5}), out=self.out)
        self.assertEqual(rc, 0)
        self.assertFalse(p.get("pass"))
        _rec("case", "--run-id", run_id, "--case-id", case_id,
             "--action", "end", "--status", "pass", out=self.out)
        rc, p, _ = _rec("finalize", "--run-id", run_id, out=self.out)
        self.assertEqual(p.get("data_gate"), "FAIL", p)
        self.assertEqual(p.get("gate"), "FAIL", p)

    def test_declared_checks_without_snapshot_rejected(self):
        run_id, case_id = "e2e-missing", "TC-003"
        self._base_case(run_id, case_id)
        rc, _, err = _rec("case", "--run-id", run_id, "--case-id", case_id,
                          "--action", "end", "--status", "pass", out=self.out)
        self.assertNotEqual(rc, 0)
        self.assertIn("数据校验不允许静默跳过", err)

    def test_ui_only_regression_unaffected(self):
        """v2.12 纯 UI 用例（无数据校验）：gate 行为与 v2.12 完全一致。"""
        run_id = "e2e-ui"
        _rec("run", "--run-id", run_id, out=self.out)
        _rec("case", "--run-id", run_id, "--case-id", "TC-UI", "--action", "start",
             "--priority", "P0", out=self.out)
        _rec("step", "--run-id", run_id, "--case-id", "TC-UI",
             "--action-name", "navigate", "--target", "/", out=self.out)
        _rec("assert", "--run-id", run_id, "--case-id", "TC-UI",
             "--kind", "url", "--expected", "/", "--actual", "/", "--pass", "true",
             out=self.out)
        _rec("case", "--run-id", run_id, "--case-id", "TC-UI",
             "--action", "end", "--status", "pass", out=self.out)
        rc, p, _ = _rec("finalize", "--run-id", run_id, out=self.out)
        self.assertEqual(rc, 0)
        self.assertEqual(p.get("gate"), "PASS")
        self.assertEqual(p.get("data_gate"), "PASS")
        s = json.loads((self.out / run_id / "summary.json").read_text())
        self.assertEqual(s["stats"]["data_snapshot_files"], 0)


if __name__ == "__main__":
    unittest.main()
