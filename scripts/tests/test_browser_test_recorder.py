#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
browser_test_recorder.py 单元测试（v2.12）

覆盖场景:
  1. run 初始化目录结构与幂等性
  2. case start/end 完整生命周期 + 重复 start 拦截
  3. step 记录 + 非法动作/空目标/缺失截图拦截
  4. assert 记录 + 失败断言级联步骤状态
  5. error 三来源记录 + 疑似缺陷必须带 detail
  6. blocker 记录 + 已解决必须带 resolution
  7. verify schema 校验（合规通过 / 手工篡改后拦截）
  8. finalize 一致性校验（meta 快照不符即拒绝）
  9. finalize 门禁（P0 全过=PASS / P0 失败=FAIL / 存在 fail=FAIL）
  10. FAIL 用例缺错误依据时 case end 拦截
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import browser_test_recorder as rec  # noqa: E402


class RecorderTestCase(unittest.TestCase):
    """公共夹具：每个用例一个独立临时 out 目录"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = self._tmp.name
        self.run_id = "test-run-001"
        rc = rec.main(["--out", self.out, "run", "--run-id", self.run_id,
                       "--url", "http://localhost:8000", "--hosts", "auto"])
        self.assertEqual(rc, 0)

    def tearDown(self):
        self._tmp.cleanup()

    def run_recorder(self, argv, expect_error=False):
        """调用 recorder CLI；expect_error=True 时断言其非零退出（SystemExit）"""
        if expect_error:
            with self.assertRaises(SystemExit) as cm:
                rec.main(["--out", self.out] + argv)
            self.assertNotEqual(cm.exception.code, 0)
            return None
        return rec.main(["--out", self.out] + argv)

    def case_path(self, cid):
        return Path(self.out) / self.run_id / "cases" / f"{cid}.json"

    def read_case(self, cid):
        return json.loads(self.case_path(cid).read_text(encoding="utf-8"))


class TestRunInit(RecorderTestCase):
    """场景 1：run 初始化与幂等"""

    def test_run_creates_structure(self):
        rd = Path(self.out) / self.run_id
        self.assertTrue((rd / "run-meta.json").is_file())
        self.assertTrue((rd / "cases").is_dir())
        self.assertTrue((rd / "screenshots").is_dir())
        self.assertTrue((rd / "visual-diff").is_dir())

    def test_run_idempotent(self):
        rc = rec.main(["--out", self.out, "run", "--run-id", self.run_id])
        self.assertEqual(rc, 0)  # 重复 run 不报错、不清空

    def test_run_rejects_bad_host(self):
        """非法宿主必须在建目录之前拦截（不留半成品目录）"""
        with self.assertRaises(SystemExit):
            rec.main(["--out", self.out, "run", "--run-id", "r2",
                      "--hosts", "firefox-extended"])
        self.assertFalse((Path(self.out) / "r2").exists(),
                         "非法宿主不应留下任何目录")


class TestCaseLifecycle(RecorderTestCase):
    """场景 2 + 10：用例生命周期与失败依据强制"""

    def _start(self, cid="TC-001", priority="P0"):
        self.run_recorder(["case", "--run-id", self.run_id, "--case-id", cid,
                           "--action", "start", "--title", "登录主流程",
                           "--priority", priority, "--host", "chrome-devtools",
                           "--flows", "FLOW-01", "--datasets", "DT-P-001"])

    def test_start_end_pass(self):
        self._start()
        self.run_recorder(["step", "--run-id", self.run_id, "--case-id", "TC-001",
                           "--action-name", "navigate", "--target",
                           "http://localhost:8000/login", "--status", "pass"])
        self.run_recorder(["assert", "--run-id", self.run_id, "--case-id", "TC-001",
                           "--kind", "url", "--expected", "/login",
                           "--actual", "/login", "--pass", "true"])
        rc = self.run_recorder(["case", "--run-id", self.run_id, "--case-id", "TC-001",
                                "--action", "end", "--status", "pass"])
        self.assertEqual(rc, 0)
        case = self.read_case("TC-001")
        self.assertEqual(case["status"], "pass")
        self.assertEqual(len(case["steps"][0]["assertions"]), 1)

    def test_duplicate_start_rejected(self):
        self._start()
        self.run_recorder(["case", "--run-id", self.run_id, "--case-id", "TC-001",
                           "--action", "start"], expect_error=True)

    def test_fail_without_reason_rejected(self):
        """场景 10：判 fail 但无 error/blocker → case end 必须拒绝"""
        self._start()
        self.run_recorder(["step", "--run-id", self.run_id, "--case-id", "TC-001",
                           "--action-name", "click", "--target", "#submit"])
        self.run_recorder(["case", "--run-id", self.run_id, "--case-id", "TC-001",
                           "--action", "end", "--status", "fail"], expect_error=True)

    def test_fail_with_error_ok(self):
        self._start()
        self.run_recorder(["step", "--run-id", self.run_id, "--case-id", "TC-001",
                           "--action-name", "submit", "--target", "#form"])
        self.run_recorder(["error", "--run-id", self.run_id, "--case-id", "TC-001",
                           "--source", "network", "--message", "POST /api/login 500"])
        rc = self.run_recorder(["case", "--run-id", self.run_id, "--case-id", "TC-001",
                                "--action", "end", "--status", "fail"])
        self.assertEqual(rc, 0)


class TestStepAssertError(RecorderTestCase):
    """场景 3/4/5：步骤、断言、错误的 schema 强制"""

    def setUp(self):
        super().setUp()
        rec.main(["--out", self.out, "case", "--run-id", self.run_id,
                  "--case-id", "TC-010", "--action", "start", "--priority", "P1"])

    def test_bad_action_rejected(self):
        self.run_recorder(["step", "--run-id", self.run_id, "--case-id", "TC-010",
                           "--action-name", "teleport", "--target", "x"],
                          expect_error=True)

    def test_empty_target_rejected(self):
        self.run_recorder(["step", "--run-id", self.run_id, "--case-id", "TC-010",
                           "--action-name", "click", "--target", ""],
                          expect_error=True)

    def test_screenshot_step_requires_file(self):
        self.run_recorder(["step", "--run-id", self.run_id, "--case-id", "TC-010",
                           "--action-name", "screenshot", "--target", "page",
                           "--path", "/nonexistent/shot.png"], expect_error=True)

    def test_assert_fail_cascades_step_status(self):
        rec.main(["--out", self.out, "step", "--run-id", self.run_id,
                  "--case-id", "TC-010", "--action-name", "submit",
                  "--target", "#form", "--status", "pass"])
        rec.main(["--out", self.out, "assert", "--run-id", self.run_id,
                  "--case-id", "TC-010", "--kind", "network",
                  "--expected", "200", "--actual", "500", "--pass", "false"])
        case = self.read_case("TC-010")
        self.assertEqual(case["steps"][0]["status"], "fail",
                         "失败断言必须级联步骤状态为 fail")

    def test_bug_requires_detail(self):
        self.run_recorder(["error", "--run-id", self.run_id, "--case-id", "TC-010",
                           "--source", "bug", "--message", "购物车金额错误"],
                          expect_error=True)

    def test_console_error_mirrors_to_step(self):
        rec.main(["--out", self.out, "step", "--run-id", self.run_id,
                  "--case-id", "TC-010", "--action-name", "navigate",
                  "--target", "http://x"])
        rec.main(["--out", self.out, "error", "--run-id", self.run_id,
                  "--case-id", "TC-010", "--source", "console",
                  "--message", "TypeError: undefined"])
        case = self.read_case("TC-010")
        self.assertIn("TypeError: undefined", case["steps"][-1]["console_errors"])


class TestBlockerVerifyFinalize(RecorderTestCase):
    """场景 6/7/8/9：阻碍、校验、一致性、门禁"""

    def _build_pass_case(self, cid="TC-100", priority="P0"):
        rec.main(["--out", self.out, "case", "--run-id", self.run_id,
                  "--case-id", cid, "--action", "start", "--priority", priority])
        rec.main(["--out", self.out, "step", "--run-id", self.run_id,
                  "--case-id", cid, "--action-name", "navigate",
                  "--target", "http://localhost:8000/"])
        rec.main(["--out", self.out, "assert", "--run-id", self.run_id,
                  "--case-id", cid, "--kind", "dom", "--expected", "h1 存在",
                  "--actual", "h1 存在", "--pass", "true"])
        rec.main(["--out", self.out, "case", "--run-id", self.run_id,
                  "--case-id", cid, "--action", "end", "--status", "pass"])

    def test_blocker_resolved_requires_resolution(self):
        rec.main(["--out", self.out, "case", "--run-id", self.run_id,
                  "--case-id", "TC-200", "--action", "start"])
        self.run_recorder(["blocker", "--run-id", self.run_id, "--case-id", "TC-200",
                           "--blocker-type", "selector_fix",
                           "--description", "按钮选择器失效",
                           "--resolved", "true"], expect_error=True)

    def test_verify_ok_after_clean_run(self):
        self._build_pass_case()
        rc = self.run_recorder(["verify", "--run-id", self.run_id, "--all"])
        self.assertEqual(rc, 0)

    def test_verify_detects_tampering(self):
        """手工篡改步骤动作 → verify 返回退出码 1 且列出违规项"""
        self._build_pass_case("TC-201")
        p = self.case_path("TC-201")
        case = json.loads(p.read_text(encoding="utf-8"))
        case["steps"][0]["action"] = "teleport"
        p.write_text(json.dumps(case), encoding="utf-8")
        rc = rec.main(["--out", self.out, "verify", "--run-id", self.run_id, "--all"])
        self.assertEqual(rc, 1, "篡改后 verify 必须返回非零退出码")

    def test_finalize_gate_pass(self):
        self._build_pass_case("TC-300", "P0")
        self._build_pass_case("TC-301", "P1")
        rc = rec.main(["--out", self.out, "finalize", "--run-id", self.run_id])
        self.assertEqual(rc, 0)
        summary = json.loads((Path(self.out) / self.run_id / "summary.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(summary["gate"], "PASS")
        self.assertEqual(summary["stats"]["pass"], 2)
        md = (Path(self.out) / self.run_id / "summary.md").read_text(encoding="utf-8")
        self.assertIn("浏览器测试运行报告", md)

    def test_finalize_gate_fail_on_p0_fail(self):
        """场景 9：P0 用例 fail → gate=FAIL 且 finalize 退出码非零"""
        rec.main(["--out", self.out, "case", "--run-id", self.run_id,
                  "--case-id", "TC-400", "--action", "start", "--priority", "P0"])
        rec.main(["--out", self.out, "step", "--run-id", self.run_id,
                  "--case-id", "TC-400", "--action-name", "submit",
                  "--target", "#login"])
        rec.main(["--out", self.out, "error", "--run-id", self.run_id,
                  "--case-id", "TC-400", "--source", "network",
                  "--message", "POST /api/login 500"])
        rec.main(["--out", self.out, "case", "--run-id", self.run_id,
                  "--case-id", "TC-400", "--action", "end", "--status", "fail"])
        # gate=FAIL 时 finalize 仍必须落盘 summary（报告反映真实结果），只以退出码 1 表示门禁失败
        rc = rec.main(["--out", self.out, "finalize", "--run-id", self.run_id])
        self.assertEqual(rc, 1, "P0 fail → finalize 退出码必须为 1")
        summary = json.loads((Path(self.out) / self.run_id / "summary.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(summary["gate"], "FAIL")

    def test_finalize_detects_meta_inconsistency(self):
        """场景 8：篡改 run-meta 快照数字 → finalize 拒绝"""
        self._build_pass_case("TC-500")
        mp = Path(self.out) / self.run_id / "run-meta.json"
        meta = json.loads(mp.read_text(encoding="utf-8"))
        meta["cases"]["TC-500"]["steps"] = 99  # 与文件实算值(1)不符
        mp.write_text(json.dumps(meta), encoding="utf-8")
        with self.assertRaises(SystemExit):
            rec.main(["--out", self.out, "finalize", "--run-id", self.run_id])

    def test_finalize_rejects_running_case(self):
        """finalize 前所有用例必须已 end（running 残留即拒绝）"""
        rec.main(["--out", self.out, "case", "--run-id", self.run_id,
                  "--case-id", "TC-600", "--action", "start"])
        with self.assertRaises(SystemExit):
            rec.main(["--out", self.out, "finalize", "--run-id", self.run_id])


if __name__ == "__main__":
    unittest.main(verbosity=2)
