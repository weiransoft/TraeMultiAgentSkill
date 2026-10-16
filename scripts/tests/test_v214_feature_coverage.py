#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v2.14 功能覆盖驱动测试 —— 端到端集成测试

被测对象: scripts/browser_test_recorder.py（FUNC-* 功能清单 / 覆盖矩阵 / 缺陷台账）

测试场景:
  1. 功能清单 schema 校验    —— evidence 格式 / kind 枚举 / ID 格式与唯一性 /
                              interactive-form 禁豁免 / 豁免必须带 reason
  2. 覆盖矩阵 finalize 现算  —— PASS 断言才算覆盖（fail 断言不算）、
                              display/nav 无豁免判 missing、waived 计入 covered
  3. 缺陷台账状态机          —— add 强制 bug_ref / close fixed 路径回归核对 /
                              reopen 必须 reason / audit 漏修视图 / 非法转移拒绝
  4. finalize 缺陷登记闸     —— suspected_bug 漏登 → gate=FAIL 且违规入 summary
  5. v2.13 回归（数据门禁）  —— 纯 UI run（无清单无台账）行为与 v2.12/13 完全一致
  6. SHA256 锚定             —— 执行期修改清单原件 → finalize 直接拒绝
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
RECORDER = SCRIPTS_DIR / "browser_test_recorder.py"
PY = sys.executable


def run_rec(out_root: Path, *argv: str, expect_rc: int = 0) -> str:
    """调用记录器 CLI 并断言退出码（stdout 打印便于失败排查）。"""
    cmd = [PY, str(RECORDER), "--out", str(out_root)] + list(argv)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != expect_rc:
        raise AssertionError(
            f"rc={proc.returncode}（期望 {expect_rc}）cmd={argv}\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}")
    return proc.stdout


def make_inventory(tmp: Path) -> Path:
    """构造两页功能清单：page1（列表页）+ page2（详情页）。"""
    inv = {
        "generated_at": "2026-10-16T00:00:00+08:00",
        "source": {"analysis": "unit-test", "flow_report": "FLOW-01"},
        "pages": [
            {"page_id": "PAGE-01", "path": "/orders", "name": "订单列表", "funcs": [
                {"id": "FUNC-01-01", "kind": "form",
                 "desc": "新建订单弹窗", "branches": ["提交成功", "校验失败"],
                 "evidence": ["src/pages/orders.tsx:20", "src/pages/orders.tsx:55"]},
                {"id": "FUNC-01-02", "kind": "display",
                 "desc": "列表分页", "branches": ["翻页"],
                 "evidence": ["src/pages/orders.tsx:30"]},
                {"id": "FUNC-01-03", "kind": "nav",
                 "desc": "顶部导航入口", "branches": ["跳转"],
                 "evidence": ["src/components/nav.tsx:10"],
                 "waived": True, "waive_reason": "跳转行为已由 FLOW-02 主路径用例覆盖"},
            ]},
            {"page_id": "PAGE-02", "path": "/orders/:id", "name": "订单详情", "funcs": [
                {"id": "FUNC-02-01", "kind": "interactive",
                 "desc": "取消订单按钮", "branches": ["确认取消", "取消确认框"],
                 "evidence": ["src/pages/detail.tsx:12"]},
            ]},
        ],
    }
    p = tmp / "page-feature-inventory.json"
    p.write_text(json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def add_bug_case(out_root: Path, run_id: str, tc: str, funcs: list,
                 bug_message: str = "列表金额与详情不一致",
                 priority: str = "P0") -> int:
    """建一个带 PASS 覆盖 + suspected_bug 的用例，返回该 bug 的 error_seq。"""
    run_rec(out_root, "case", "--run-id", run_id, "--case-id", tc,
            "--action", "start", "--title", f"用例 {tc}", "--priority", priority,
            "--funcs", ",".join(funcs))
    step_argv = ["step", "--run-id", run_id, "--case-id", tc,
                 "--action-name", "click", "--target", "#btn", "--status", "pass"]
    if funcs:
        step_argv += ["--funcs", ",".join(funcs)]
    run_rec(out_root, *step_argv)
    run_rec(out_root, "assert", "--run-id", run_id, "--case-id", tc,
            "--kind", "dom", "--expected", "ok", "--actual", "ok", "--pass", "true")
    run_rec(out_root, "error", "--run-id", run_id, "--case-id", tc,
            "--source", "bug", "--message", bug_message,
            "--detail", "复现：打开列表→点第一条→金额显示 100，详情显示 90",
            "--suspected-bug", "true")
    run_rec(out_root, "case", "--run-id", run_id, "--case-id", tc,
            "--action", "end", "--status", "pass")
    case = json.loads((out_root / run_id / "cases" / f"{tc}.json")
                      .read_text(encoding="utf-8"))
    bugs = [e for e in case["errors"] if e.get("suspected_bug")]
    assert len(bugs) == 1, "期望恰好一条 suspected_bug"
    return bugs[0]["error_seq"]


class TestV214FeatureInventory(unittest.TestCase):
    """功能清单 schema 校验（run --feature-inventory 当场拒绝非法清单）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="v214-inv-"))
        self.out = self.tmp / "runs"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_inv(self, funcs, page_id="PAGE-01"):
        inv = {"pages": [{"page_id": page_id, "path": "/x", "name": "X 页",
                          "funcs": funcs}]}
        p = self.tmp / "inv.json"
        p.write_text(json.dumps(inv, ensure_ascii=False), encoding="utf-8")
        return p

    def test_bad_evidence_format_rejected(self):
        """evidence 必须是 文件:行号 格式（缺行号直接拒绝）。"""
        inv = self._write_inv([{"id": "FUNC-01-01", "kind": "form",
                                "desc": "x", "evidence": ["src/a.tsx"]}])
        run_rec(self.out, "run", "--run-id", "r1")
        run_rec(self.out, "run", "--run-id", "r2", "--feature-inventory", str(inv),
                expect_rc=1)

    def test_bad_kind_rejected(self):
        """kind 必须在五枚举内。"""
        inv = self._write_inv([{"id": "FUNC-01-01", "kind": "backend",
                                "desc": "x", "evidence": ["src/a.tsx:1"]}])
        run_rec(self.out, "run", "--run-id", "r2", "--feature-inventory", str(inv),
                expect_rc=1)

    def test_interactive_waive_rejected(self):
        """interactive/form 禁止豁免（门禁覆盖对象不得口头放行）。"""
        inv = self._write_inv([{"id": "FUNC-01-01", "kind": "interactive",
                                "desc": "x", "evidence": ["src/a.tsx:1"],
                                "waived": True, "waive_reason": "不想测"}])
        run_rec(self.out, "run", "--run-id", "r2", "--feature-inventory", str(inv),
                expect_rc=1)

    def test_waive_without_reason_rejected(self):
        """display/nav 豁免也必须书面 reason。"""
        inv = self._write_inv([{"id": "FUNC-01-01", "kind": "nav",
                                "desc": "x", "evidence": ["src/a.tsx:1"],
                                "waived": True}])
        run_rec(self.out, "run", "--run-id", "r2", "--feature-inventory", str(inv),
                expect_rc=1)

    def test_duplicate_and_bad_id_rejected(self):
        """FUNC ID 唯一性 + 格式校验。"""
        inv = self._write_inv([
            {"id": "FUNC-01-01", "kind": "form", "desc": "a",
             "evidence": ["src/a.tsx:1"]},
            {"id": "FUNC-01-01", "kind": "form", "desc": "b",
             "evidence": ["src/b.tsx:1"]},
            {"id": "FUNC-1-1", "kind": "form", "desc": "c",
             "evidence": ["src/c.tsx:1"]},
        ])
        run_rec(self.out, "run", "--run-id", "r2", "--feature-inventory", str(inv),
                expect_rc=1)

    def test_unknown_func_in_case_rejected(self):
        """case --funcs 引用未登记 ID 当场拒绝（防编造功能项）。"""
        inv = make_inventory(self.tmp)
        run_rec(self.out, "run", "--run-id", "r1", "--feature-inventory", str(inv))
        run_rec(self.out, "case", "--run-id", "r1", "--case-id", "TC-X",
                "--action", "start", "--funcs", "FUNC-99-99", expect_rc=1)

    def test_inventory_sha_anchor(self):
        """执行期修改清单原件 → SHA 失配 → finalize 拒绝收尾。"""
        inv = make_inventory(self.tmp)
        run_rec(self.out, "run", "--run-id", "r1", "--url", "http://x",
                "--feature-inventory", str(inv))
        # 覆盖全部功能，本可 PASS
        seq = add_bug_case(self.out, "r1", "TC-01",
                           ["FUNC-01-01", "FUNC-01-02", "FUNC-02-01"])
        # 执行期偷偷删掉 FUNC-02-01（移动球门）
        data = json.loads(inv.read_text(encoding="utf-8"))
        data["pages"][1]["funcs"] = []
        inv.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        out = run_rec(self.out, "defect", "--action", "add", "--run-id", "r1",
                      "--case-id", "TC-01", "--bug-ref", str(seq),
                      "--severity", "minor", "--symptom", "金额不一致")
        did = json.loads(out)["defect_id"]
        run_rec(self.out, "defect", "--action", "close", "--defect-id", did,
                "--reason", "误报：预期口径不同")
        run_rec(self.out, "finalize", "--run-id", "r1", expect_rc=1)


class TestV214CoverageGate(unittest.TestCase):
    """覆盖矩阵 finalize 现算与 feature_gate 门禁。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="v214-cov-"))
        self.out = self.tmp / "runs"
        self.inv = make_inventory(self.tmp)
        run_rec(self.out, "run", "--run-id", "r1", "--url", "http://x",
                "--feature-inventory", str(self.inv))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _close_ledger(self):
        """台账建立（本组用例 focus 覆盖门禁，缺陷统一按误报关闭）。"""
        out = run_rec(self.out, "defect", "--action", "add", "--run-id", "r1",
                      "--case-id", "TC-01", "--bug-ref", "1",
                      "--severity", "major", "--symptom", "金额不一致")
        did = json.loads(out)["defect_id"]
        run_rec(self.out, "defect", "--action", "close", "--defect-id", did,
                "--reason", "误报：格式化口径不同")
        return did

    def test_fail_assert_not_counted_as_covered(self):
        """FUNC 关联步骤只有 fail 断言 → 不算覆盖，feature_gate=FAIL。"""
        run_rec(self.out, "case", "--run-id", "r1", "--case-id", "TC-01",
                "--action", "start", "--priority", "P0",
                "--funcs", "FUNC-01-01,FUNC-01-02,FUNC-02-01")
        run_rec(self.out, "step", "--run-id", "r1", "--case-id", "TC-01",
                "--action-name", "click", "--target", "#btn", "--status", "fail",
                "--funcs", "FUNC-01-01,FUNC-01-02,FUNC-02-01")
        run_rec(self.out, "assert", "--run-id", "r1", "--case-id", "TC-01",
                "--kind", "dom", "--expected", "A", "--actual", "B", "--pass", "false")
        run_rec(self.out, "error", "--run-id", "r1", "--case-id", "TC-01",
                "--source", "bug", "--message", "金额不一致",
                "--detail", "复现：见 TC-01", "--suspected-bug", "true")
        run_rec(self.out, "case", "--run-id", "r1", "--case-id", "TC-01",
                "--action", "end", "--status", "fail")
        self._close_ledger()
        run_rec(self.out, "finalize", "--run-id", "r1", expect_rc=1)
        summary = json.loads((self.out / "r1" / "summary.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(summary["feature_gate"], "FAIL")
        # 三个必覆盖/无豁免功能全在 missing（fail 断言不构成覆盖）
        missing_ids = {m["id"] for m in summary["feature_coverage"]["missing"]}
        self.assertEqual(missing_ids, {"FUNC-01-01", "FUNC-01-02", "FUNC-02-01"})
        # FUNC-01-03 nav 已豁免 → 不在 missing
        self.assertNotIn("FUNC-01-03", missing_ids)

    def test_partial_coverage_lists_missing(self):
        """只覆盖 FUNC-01-01 → 其余必覆盖项进 missing，gate=FAIL。"""
        run_rec(self.out, "case", "--run-id", "r1", "--case-id", "TC-01",
                "--action", "start", "--priority", "P0", "--funcs", "FUNC-01-01")
        run_rec(self.out, "step", "--run-id", "r1", "--case-id", "TC-01",
                "--action-name", "submit", "--target", "#form", "--status", "pass",
                "--funcs", "FUNC-01-01")
        run_rec(self.out, "assert", "--run-id", "r1", "--case-id", "TC-01",
                "--kind", "url", "--expected", "/orders", "--actual", "/orders",
                "--pass", "true")
        run_rec(self.out, "case", "--run-id", "r1", "--case-id", "TC-01",
                "--action", "end", "--status", "pass")
        run_rec(self.out, "finalize", "--run-id", "r1", expect_rc=1)
        summary = json.loads((self.out / "r1" / "summary.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(summary["feature_gate"], "FAIL")
        missing_ids = {m["id"] for m in summary["feature_coverage"]["missing"]}
        self.assertEqual(missing_ids, {"FUNC-01-02", "FUNC-02-01"})

    def test_full_coverage_pass_with_waived_nav(self):
        """全功能覆盖 + nav 豁免 → feature_gate=PASS，summary.md 呈现缺口清单。"""
        seq = add_bug_case(self.out, "r1", "TC-01", ["FUNC-01-01"], priority="P2")
        run_rec(self.out, "case", "--run-id", "r1", "--case-id", "TC-02",
                "--action", "start", "--priority", "P0",
                "--funcs", "FUNC-01-02,FUNC-02-01")
        run_rec(self.out, "step", "--run-id", "r1", "--case-id", "TC-02",
                "--action-name", "click", "--target", "#page2", "--status", "pass",
                "--funcs", "FUNC-01-02")
        run_rec(self.out, "assert", "--run-id", "r1", "--case-id", "TC-02",
                "--kind", "dom", "--expected", "ok", "--actual", "ok", "--pass", "true")
        run_rec(self.out, "step", "--run-id", "r1", "--case-id", "TC-02",
                "--action-name", "click", "--target", "#cancel", "--status", "pass",
                "--funcs", "FUNC-02-01")
        run_rec(self.out, "assert", "--run-id", "r1", "--case-id", "TC-02",
                "--kind", "text", "--expected", "已取消", "--actual", "已取消",
                "--pass", "true")
        run_rec(self.out, "case", "--run-id", "r1", "--case-id", "TC-02",
                "--action", "end", "--status", "pass")
        # 缺陷登记（漏登会被登记闸拦下），按误报关闭保持台账零 open
        out = run_rec(self.out, "defect", "--action", "add", "--run-id", "r1",
                      "--case-id", "TC-01", "--bug-ref", str(seq),
                      "--severity", "major", "--symptom", "金额不一致",
                      "--funcs", "FUNC-01-02")
        did = json.loads(out)["defect_id"]
        run_rec(self.out, "defect", "--action", "close", "--defect-id", did,
                "--reason", "误报：列表分单位、详情元单位")
        run_rec(self.out, "finalize", "--run-id", "r1")
        summary = json.loads((self.out / "r1" / "summary.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(summary["feature_gate"], "PASS")
        self.assertEqual(summary["gate"], "PASS")
        fc = summary["feature_coverage"]
        self.assertEqual(fc["must_total"], 2)      # form + interactive
        self.assertEqual(fc["must_covered"], 2)
        self.assertEqual(fc["total"], 4)
        self.assertEqual(len(fc["waived"]), 1)      # nav 豁免
        self.assertEqual(fc["missing"], [])
        # covered_by 溯源：FUNC-01-02 的覆盖用例是 TC-02
        self.assertEqual(fc["covered_by"]["FUNC-01-02"], ["TC-02"])
        md = (self.out / "r1" / "summary.md").read_text(encoding="utf-8")
        self.assertIn("功能覆盖门禁", md)
        self.assertIn("已豁免 FUNC-01-03", md)


class TestV214DefectLedger(unittest.TestCase):
    """缺陷台账状态机 / 登记闸 / audit 审计。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="v214-def-"))
        self.out = self.tmp / "runs"
        self.inv = make_inventory(self.tmp)
        run_rec(self.out, "run", "--run-id", "r1", "--url", "http://x",
                "--feature-inventory", str(self.inv))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ledger(self):
        return json.loads((self.out / "defects.json").read_text(encoding="utf-8"))

    def test_add_requires_real_bug_signal(self):
        """bug_ref 指向不存在的 bug 信号 → 拒绝凭空造缺陷。"""
        add_bug_case(self.out, "r1", "TC-01", ["FUNC-01-01"])
        run_rec(self.out, "defect", "--action", "add", "--run-id", "r1",
                "--case-id", "TC-01", "--bug-ref", "99",
                "--severity", "major", "--symptom", "x", expect_rc=1)

    def test_defect_id_increment_and_bug_ref_binding(self):
        """工单号递增 + bug_ref 与信号 error_seq 绑定可追溯。"""
        seq1 = add_bug_case(self.out, "r1", "TC-01", ["FUNC-01-01"])
        seq2 = add_bug_case(self.out, "r1", "TC-02", ["FUNC-01-02"], "分页越界")
        out = run_rec(self.out, "defect", "--action", "add", "--run-id", "r1",
                      "--case-id", "TC-01", "--bug-ref", str(seq1),
                      "--severity", "major", "--symptom", "金额不一致",
                      "--funcs", "FUNC-01-02")
        d1 = json.loads(out)["defect_id"]
        self.assertEqual(d1, "DEF-001")
        out = run_rec(self.out, "defect", "--action", "add", "--run-id", "r1",
                      "--case-id", "TC-02", "--bug-ref", str(seq2),
                      "--severity", "minor", "--symptom", "分页越界")
        self.assertEqual(json.loads(out)["defect_id"], "DEF-002")
        led = self._ledger()
        self.assertEqual(led["DEF-001"]["bug_ref"],
                         {"run_id": "r1", "case_id": "TC-01", "error_seq": 1})

    def test_unregistered_suspected_bug_fails_finalize(self):
        """台账已启用但漏登 → 登记闸 violation，gate=FAIL。"""
        # 先建立台账（用一个已登缺陷误报关闭）
        seq = add_bug_case(self.out, "r1", "TC-01", ["FUNC-01-01"])
        out = run_rec(self.out, "defect", "--action", "add", "--run-id", "r1",
                      "--case-id", "TC-01", "--bug-ref", str(seq),
                      "--severity", "minor", "--symptom", "金额不一致")
        did = json.loads(out)["defect_id"]
        run_rec(self.out, "defect", "--action", "close", "--defect-id", did,
                "--reason", "误报")
        # 再产生一条不登记的 suspected_bug
        run_rec(self.out, "case", "--run-id", "r1", "--case-id", "TC-02",
                "--action", "start", "--priority", "P0", "--funcs", "FUNC-01-02")
        run_rec(self.out, "step", "--run-id", "r1", "--case-id", "TC-02",
                "--action-name", "click", "--target", "#x", "--status", "pass",
                "--funcs", "FUNC-01-02")
        run_rec(self.out, "assert", "--run-id", "r1", "--case-id", "TC-02",
                "--kind", "dom", "--expected", "a", "--actual", "a", "--pass", "true")
        run_rec(self.out, "error", "--run-id", "r1", "--case-id", "TC-02",
                "--source", "bug", "--message", "漏登缺陷", "--detail", "d",
                "--suspected-bug", "true")
        run_rec(self.out, "case", "--run-id", "r1", "--case-id", "TC-02",
                "--action", "end", "--status", "pass")
        # 补齐 FUNC-02-01 覆盖，让 gate 唯一失败原因就是漏登
        run_rec(self.out, "case", "--run-id", "r1", "--case-id", "TC-03",
                "--action", "start", "--priority", "P0", "--funcs", "FUNC-02-01")
        run_rec(self.out, "step", "--run-id", "r1", "--case-id", "TC-03",
                "--action-name", "click", "--target", "#c", "--status", "pass",
                "--funcs", "FUNC-02-01")
        run_rec(self.out, "assert", "--run-id", "r1", "--case-id", "TC-03",
                "--kind", "dom", "--expected", "a", "--actual", "a", "--pass", "true")
        run_rec(self.out, "case", "--run-id", "r1", "--case-id", "TC-03",
                "--action", "end", "--status", "pass")
        run_rec(self.out, "finalize", "--run-id", "r1", expect_rc=1)
        summary = json.loads((self.out / "r1" / "summary.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(len(summary["defect_registration_violations"]), 1)
        self.assertEqual(summary["defect_registration_violations"][0]["case_id"],
                         "TC-02")
        self.assertEqual(summary["gate"], "FAIL")
        self.assertEqual(summary["feature_gate"], "PASS")

    def test_fixed_close_requires_regression_run(self):
        """fixed 关闭必须带 --regression-run（缺参拒绝）。"""
        seq = add_bug_case(self.out, "r1", "TC-01", ["FUNC-01-01"])
        out = run_rec(self.out, "defect", "--action", "add", "--run-id", "r1",
                      "--case-id", "TC-01", "--bug-ref", str(seq),
                      "--severity", "major", "--symptom", "金额不一致")
        did = json.loads(out)["defect_id"]
        run_rec(self.out, "defect", "--action", "close", "--defect-id", did,
                "--commit", "abc123", expect_rc=1)

    def test_regression_must_actually_pass(self):
        """回归 run 未全 PASS → close 拒绝（结果不配合不得关闭）。"""
        seq = add_bug_case(self.out, "r1", "TC-01", ["FUNC-01-01"])
        out = run_rec(self.out, "defect", "--action", "add", "--run-id", "r1",
                      "--case-id", "TC-01", "--bug-ref", str(seq),
                      "--severity", "major", "--symptom", "金额不一致")
        did = json.loads(out)["defect_id"]
        # 回归 run：TC-R1 fail（finalize gate FAIL，但 summary 仍落盘）
        run_rec(self.out, "run", "--run-id", "r2")
        run_rec(self.out, "case", "--run-id", "r2", "--case-id", "TC-R1",
                "--action", "start")
        run_rec(self.out, "step", "--run-id", "r2", "--case-id", "TC-R1",
                "--action-name", "click", "--target", "#btn", "--status", "fail")
        run_rec(self.out, "assert", "--run-id", "r2", "--case-id", "TC-R1",
                "--kind", "dom", "--expected", "A", "--actual", "B", "--pass", "false")
        run_rec(self.out, "error", "--run-id", "r2", "--case-id", "TC-R1",
                "--source", "console", "--message", "js err")
        run_rec(self.out, "case", "--run-id", "r2", "--case-id", "TC-R1",
                "--action", "end", "--status", "fail")
        run_rec(self.out, "finalize", "--run-id", "r2", expect_rc=1)
        run_rec(self.out, "defect", "--action", "close", "--defect-id", did,
                "--commit", "abc123", "--regression-run", "r2",
                "--regression-cases", "TC-R1", expect_rc=1)

    def test_reopen_paths_and_history(self):
        """closed 不可直接 reopen；regressed reopen 需 reason；history 轨迹完整。"""
        seq = add_bug_case(self.out, "r1", "TC-01", ["FUNC-01-01"])
        out = run_rec(self.out, "defect", "--action", "add", "--run-id", "r1",
                      "--case-id", "TC-01", "--bug-ref", str(seq),
                      "--severity", "minor", "--symptom", "金额不一致")
        did = json.loads(out)["defect_id"]
        # 误报关闭 → closed
        run_rec(self.out, "defect", "--action", "close", "--defect-id", did,
                "--reason", "误报")
        # closed 允许 reopen（再犯回炉），但必须带 reason
        run_rec(self.out, "defect", "--action", "reopen", "--defect-id", did,
                expect_rc=1)  # 缺 reason 拒绝
        # 模拟回归再犯（closed -> regressed 由回归执行器标记），验证 reopen 后续路径
        led = self._ledger()
        led[did]["status"] = "regressed"
        (self.out / "defects.json").write_text(json.dumps(led), encoding="utf-8")
        run_rec(self.out, "defect", "--action", "reopen", "--defect-id", did,
                expect_rc=1)  # regressed 不允许 reopen（必须先走 fixed 修复）
        run_rec(self.out, "defect", "--action", "reopen", "--defect-id", did,
                "--reason", "修复无效复现")  # regressed -> open 需 reason
        led = self._ledger()
        self.assertEqual(led[did]["status"], "open")
        self.assertEqual([h["action"] for h in led[did]["history"]],
                         ["add", "close", "reopen"])

    def test_audit_detects_open_defects(self):
        """audit 输出漏修视图：open 缺陷存在时 gate=FAIL。"""
        seq = add_bug_case(self.out, "r1", "TC-01", ["FUNC-01-01"])
        run_rec(self.out, "defect", "--action", "add", "--run-id", "r1",
                "--case-id", "TC-01", "--bug-ref", str(seq),
                "--severity", "major", "--symptom", "金额不一致")
        proc = subprocess.run(
            [PY, str(RECORDER), "--out", str(self.out), "defect", "--action", "audit"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        audit = json.loads(proc.stdout)
        self.assertEqual(audit["gate"], "FAIL")
        self.assertEqual(len(audit["open_or_regressed"]), 1)

    def test_close_verified_with_passing_regression(self):
        """回归 run 全 PASS → fixed 关闭成功，regression 记录落台账。"""
        seq = add_bug_case(self.out, "r1", "TC-01", ["FUNC-01-01"])
        out = run_rec(self.out, "defect", "--action", "add", "--run-id", "r1",
                      "--case-id", "TC-01", "--bug-ref", str(seq),
                      "--severity", "major", "--symptom", "金额不一致",
                      "--cases", "TC-01")
        did = json.loads(out)["defect_id"]
        # 修复记录：open -> fixed（commit 强制）
        run_rec(self.out, "defect", "--action", "update", "--defect-id", did,
                "--commit", "abc123", "--fix-summary", "金额换算统一走 centToYuan")
        # 回归 run 全 PASS（回归用例按 P0 门禁执行，确保 gate 语义严格）
        run_rec(self.out, "run", "--run-id", "r2")
        run_rec(self.out, "case", "--run-id", "r2", "--case-id", "TC-R1",
                "--action", "start", "--priority", "P0")
        run_rec(self.out, "step", "--run-id", "r2", "--case-id", "TC-R1",
                "--action-name", "click", "--target", "#btn", "--status", "pass")
        run_rec(self.out, "assert", "--run-id", "r2", "--case-id", "TC-R1",
                "--kind", "dom", "--expected", "A", "--actual", "A", "--pass", "true")
        run_rec(self.out, "case", "--run-id", "r2", "--case-id", "TC-R1",
                "--action", "end", "--status", "pass")
        run_rec(self.out, "finalize", "--run-id", "r2")
        run_rec(self.out, "defect", "--action", "close", "--defect-id", did,
                "--commit", "abc123", "--regression-run", "r2",
                "--regression-cases", "TC-R1")
        led = self._ledger()
        self.assertEqual(led[did]["status"], "closed")
        self.assertEqual(led[did]["fix"]["commit"], "abc123")
        self.assertEqual(led[did]["regression"],
                         {"run_id": "r2", "cases": ["TC-R1"]})


class TestV214LegacyRegression(unittest.TestCase):
    """v2.12/v2.13 回归：未启用功能清单/台账时行为与旧版本完全一致。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="v214-reg-"))
        self.out = self.tmp / "runs"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pure_ui_run_backward_compatible(self):
        """纯 UI run（无清单无台账）：gate 语义、summary 结构向后兼容。"""
        run_rec(self.out, "run", "--run-id", "r1", "--url", "http://x")
        run_rec(self.out, "case", "--run-id", "r1", "--case-id", "TC-01",
                "--action", "start", "--priority", "P0")
        run_rec(self.out, "step", "--run-id", "r1", "--case-id", "TC-01",
                "--action-name", "navigate", "--target", "http://x",
                "--status", "pass")
        run_rec(self.out, "assert", "--run-id", "r1", "--case-id", "TC-01",
                "--kind", "url", "--expected", "http://x", "--actual", "http://x",
                "--pass", "true")
        run_rec(self.out, "case", "--run-id", "r1", "--case-id", "TC-01",
                "--action", "end", "--status", "pass")
        run_rec(self.out, "verify", "--run-id", "r1", "--all")
        run_rec(self.out, "finalize", "--run-id", "r1")
        summary = json.loads((self.out / "r1" / "summary.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(summary["gate"], "PASS")
        self.assertEqual(summary["data_gate"], "PASS")     # 未启用数据层
        self.assertEqual(summary["feature_gate"], "PASS")  # 未启用功能清单
        self.assertEqual(summary["defect_registration_violations"], [])
        self.assertFalse(summary["feature_coverage"]["enabled"])
        self.assertEqual(summary["defects_ledger"], {})
        self.assertEqual(summary["stats"]["total"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
