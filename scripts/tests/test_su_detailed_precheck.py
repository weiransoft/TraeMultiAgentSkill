# -*- coding: utf-8 -*-
"""SFD 前置校验单测（precheck_detailed_run，REQ-SFD-001 / ARCH §2.2.1）。

覆盖 ARCH §10.1 切分表 "precheck" 行的关键断言：
  - 三件套缺文件逐项 exit 2（PRD E-1）；
  - understanding.json 损坏 JSON / 顶层非 dict；
  - findings 缺段（"未回填"）/ 空数组（"撤回全部结论"）/ 非数组（PRD E-2）；
  - findings 双源不一致（json 条数 ≠ 状态库 findings_total，P1-6）；
  - 锚定 run 状态违例（锚定行=最新行且 status=running → exit 2）；
  - 锚定行非最新行（render run 在后）：meta.run_status 合法 → 放行 + notes；
    meta.run_status 违例 → exit 2（P0-2 锚定口径）；
  - store=None（状态库缺失）→ "run 状态不可考" 宽松放行 + notes（AP-3）；
  - 违例路径零写副作用（detailed/ 目录不产生——校验先于一切写操作）。
"""

import json
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
_FIXTURES_DIR = TESTS_DIR / "fixtures"
for _p in (str(_FIXTURES_DIR), str(TESTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sfd_harness  # noqa: E402  共享工装（真实渲染链路）

from su.detailed_doc import (  # noqa: E402
    DetailedDocError,
    precheck_detailed_run,
)


class _FakeStore:
    """duck-typed 只读状态库替身（只实现 precheck 用到的两个只读方法）。

    precheck 契约只调 ``read_latest_run()`` / ``stats()``——替身按显式
    注入值回放，用于构造"锚定行非最新行""双源不一致"等无法用真实库
    直接摆出的违例场景（真实库场景另有专测覆盖）。
    """

    def __init__(self, latest, findings_total):
        """记录回放值。

        Args:
            latest: read_latest_run() 回放值（dict 或 None）。
            findings_total: stats()["findings_total"] 回放值。
        """
        self._latest = latest
        self._findings_total = findings_total

    def read_latest_run(self):
        """回放构造时注入的最新 run 行。"""
        return self._latest

    def stats(self):
        """回放仅含 findings_total 的统计（precheck 只读该键）。"""
        return {"findings_total": self._findings_total}


class TestPrecheckHappyPath(unittest.TestCase):
    """正常链路：真实渲染三件套 + 真实状态库 → 校验通过。"""

    def setUp(self):
        """建工作区（builder 种子库 + findings 注入 + 真实渲染）。"""
        self.ws = sfd_harness.SfdWorkspace(system_id="sfd-pre-ok")
        self.ws.setUp()

    def tearDown(self):
        """清理工作区。"""
        self.ws.tearDown()

    def test_pass_with_anchored_latest_run(self):
        """锚定 run（meta.run_id）= 最新行且 completed → 放行且字段齐备。"""
        store = sfd_harness.StateStore(
            self.ws.sys_root / "state" / "understanding.sqlite",
            self.ws.system_id)
        try:
            ctx = precheck_detailed_run(self.ws.out_root, self.ws.system_id,
                                        store=store)
        finally:
            store.close()
        # 锚定口径：run_id 取 understanding.json meta.run_id（P0-2）
        self.assertEqual(ctx["run_id"], self.ws.understanding["meta"]["run_id"])
        self.assertEqual(ctx["run_status"], "completed")
        self.assertTrue(ctx["anchored_run_is_latest"])
        self.assertTrue(ctx["run_status_verifiable"])
        self.assertEqual(ctx["notes"], [])
        # started_at 为幂等唯一时间真相源，必须原样透传（ARCH §6）
        self.assertEqual(ctx["started_at"],
                         self.ws.understanding["meta"]["started_at"])
        # paths 指向真实产物
        self.assertTrue(ctx["paths"].understanding_json.is_file())


class TestPrecheckMissingArtifacts(unittest.TestCase):
    """三件套缺文件：逐项列出缺项，exit_code=2（PRD E-1）。"""

    def setUp(self):
        """建空 tmp（不渲染任何产物）。"""
        self.ws = sfd_harness.SfdWorkspace(render=False)
        self.ws.setUp()

    def tearDown(self):
        """清理工作区。"""
        self.ws.tearDown()

    def test_all_three_missing_lists_all(self):
        """三件全缺 → message 同时点名三个缺项。"""
        with self.assertRaises(DetailedDocError) as cm:
            precheck_detailed_run(self.ws.out_root, self.ws.system_id,
                                  store=None)
        err = cm.exception
        self.assertEqual(int(err.exit_code), 2)
        msg = str(err)
        for name in ("UNDERSTANDING.md", "understanding.json",
                     "evidence-index.json"):
            self.assertIn(name, msg)

    def test_single_missing_lists_only_that_one(self):
        """只缺 evidence-index.json → message 只点名该缺项。"""
        # 手工放置另两件（缺项定位断言用最小放置，非链路构造）
        self.ws.sys_root.mkdir(parents=True, exist_ok=True)
        (self.ws.sys_root / "UNDERSTANDING.md").write_text("# x\n",
                                                           encoding="utf-8")
        (self.ws.sys_root / "understanding.json").write_text(
            json.dumps({"findings": [{"claim": "c", "kind": "mapping",
                                      "confidence": "high",
                                      "evidence_refs": ["pages:1"]}]}),
            encoding="utf-8")
        with self.assertRaises(DetailedDocError) as cm:
            precheck_detailed_run(self.ws.out_root, self.ws.system_id,
                                  store=None)
        msg = str(cm.exception)
        self.assertIn("evidence-index.json", msg)
        self.assertNotIn("UNDERSTANDING.md", msg)


class TestPrecheckUnderstandingViolations(unittest.TestCase):
    """understanding.json 形态违例：损坏/非对象/findings 三态（PRD E-2）。"""

    def setUp(self):
        """建工作区并渲染一次（获得合法三件套，再按用例篡改磁盘文件）。"""
        self.ws = sfd_harness.SfdWorkspace(system_id="sfd-pre-u")
        self.ws.setUp()

    def tearDown(self):
        """清理工作区。"""
        self.ws.tearDown()

    def _patch_json(self, payload):
        """覆写磁盘 understanding.json（payload 为 dict 则序列化）。

        Args:
            payload: 写入内容（str 原样写入——构造损坏 JSON 用）。
        """
        text = payload if isinstance(payload, str) else json.dumps(
            payload, ensure_ascii=False)
        (self.ws.sys_root / "understanding.json").write_text(
            text, encoding="utf-8")

    def test_corrupt_json_exit2(self):
        """JSON 语法损坏 → exit 2 且文案含"无法解析"。"""
        self._patch_json("{ 这不是合法 JSON ")
        with self.assertRaises(DetailedDocError) as cm:
            precheck_detailed_run(self.ws.out_root, self.ws.system_id,
                                  store=None)
        self.assertEqual(int(cm.exception.exit_code), 2)
        self.assertIn("无法解析", str(cm.exception))

    def test_top_level_not_dict_exit2(self):
        """顶层为 JSON 数组 → exit 2 且文案含"必须是 JSON 对象"。"""
        self._patch_json([1, 2, 3])
        with self.assertRaises(DetailedDocError) as cm:
            precheck_detailed_run(self.ws.out_root, self.ws.system_id,
                                  store=None)
        self.assertEqual(int(cm.exception.exit_code), 2)
        self.assertIn("必须是 JSON 对象", str(cm.exception))

    def test_findings_section_missing_wording(self):
        """缺 findings 段 → 文案为"未回填"语义。"""
        data = dict(self.ws.understanding)
        data.pop("findings")
        self._patch_json(data)
        with self.assertRaises(DetailedDocError) as cm:
            precheck_detailed_run(self.ws.out_root, self.ws.system_id,
                                  store=None)
        self.assertIn("未回填", str(cm.exception))

    def test_findings_empty_wording(self):
        """findings=[] → 文案为"撤回全部结论"语义（与缺段区分，P0-2）。"""
        data = dict(self.ws.understanding)
        data["findings"] = []
        self._patch_json(data)
        with self.assertRaises(DetailedDocError) as cm:
            precheck_detailed_run(self.ws.out_root, self.ws.system_id,
                                  store=None)
        self.assertIn("撤回全部结论", str(cm.exception))

    def test_findings_not_list_exit2(self):
        """findings 非数组 → exit 2 且文案含"必须是 JSON 数组"。"""
        data = dict(self.ws.understanding)
        data["findings"] = {"claim": "不是数组"}
        self._patch_json(data)
        with self.assertRaises(DetailedDocError) as cm:
            precheck_detailed_run(self.ws.out_root, self.ws.system_id,
                                  store=None)
        self.assertIn("必须是 JSON 数组", str(cm.exception))


class TestPrecheckDualSource(unittest.TestCase):
    """双源一致性（P1-6）：json findings 条数 vs 状态库 findings_total。"""

    def setUp(self):
        """建工作区（findings 注入 1 条 + 渲染）。"""
        self.ws = sfd_harness.SfdWorkspace(system_id="sfd-pre-ds",
                                           n_findings=1)
        self.ws.setUp()

    def tearDown(self):
        """清理工作区。"""
        self.ws.tearDown()

    def test_mismatch_exit2(self):
        """磁盘 json 手动 +1 条（未回灌库）→ exit 2 且文案给出两计数。"""
        data = self.ws.understanding_disk()
        data["findings"].append(dict(data["findings"][0]))
        (self.ws.sys_root / "understanding.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
        store = _FakeStore(
            latest={"run_id": data["meta"]["run_id"], "status": "completed"},
            findings_total=1)  # 库里恒 1 条（注入不真写库，替身回放）
        with self.assertRaises(DetailedDocError) as cm:
            precheck_detailed_run(self.ws.out_root, self.ws.system_id,
                                  store=store)
        self.assertEqual(int(cm.exception.exit_code), 2)
        self.assertIn("findings 与状态库不同步", str(cm.exception))
        self.assertIn("2 条", str(cm.exception))
        self.assertIn("1 条", str(cm.exception))

    def test_match_passes(self):
        """双源一致（json 1 = 库 1）→ 放行。"""
        store = _FakeStore(
            latest={"run_id": self.ws.understanding["meta"]["run_id"],
                    "status": "completed"},
            findings_total=1)
        ctx = precheck_detailed_run(self.ws.out_root, self.ws.system_id,
                                    store=store)
        self.assertEqual(ctx["run_status"], "completed")


class TestPrecheckAnchoredRun(unittest.TestCase):
    """锚定 run 口径（P0-2）：锚定对象 = meta.run_id 行而非最新行。"""

    def setUp(self):
        """建工作区。"""
        self.ws = sfd_harness.SfdWorkspace(system_id="sfd-pre-anchor")
        self.ws.setUp()

    def tearDown(self):
        """清理工作区。"""
        self.ws.tearDown()

    def test_anchored_latest_running_exit2(self):
        """锚定行=最新行但 status=running → exit 2（等收口提示）。"""
        meta = self.ws.understanding["meta"]
        store = _FakeStore(
            latest={"run_id": meta["run_id"], "status": "running"},
            findings_total=len(self.ws.understanding["findings"]))
        with self.assertRaises(DetailedDocError) as cm:
            precheck_detailed_run(self.ws.out_root, self.ws.system_id,
                                  store=store)
        self.assertEqual(int(cm.exception.exit_code), 2)
        self.assertIn("状态为 running", str(cm.exception))

    def test_anchored_not_latest_meta_ok_passes(self):
        """锚定行非最新行 + meta.run_status=completed → 放行 + notes 附注。"""
        meta = self.ws.understanding["meta"]
        # 伪造一条"更新的" render run 在后（render-only 后典型形态）
        store = _FakeStore(
            latest={"run_id": "later-render-run", "status": "completed"},
            findings_total=len(self.ws.understanding["findings"]))
        ctx = precheck_detailed_run(self.ws.out_root, self.ws.system_id,
                                    store=store)
        self.assertEqual(ctx["run_id"], meta["run_id"])  # 锚定仍取 meta 行
        self.assertEqual(ctx["run_status"], "completed")  # 真相源=meta 段
        self.assertFalse(ctx["anchored_run_is_latest"])
        self.assertTrue(ctx["run_status_verifiable"])
        self.assertTrue(any("锚定 run 非最新行" in n for n in ctx["notes"]))

    def test_anchored_not_latest_meta_running_exit2(self):
        """锚定行非最新行 + meta.run_status=running → exit 2（P0-2 违例）。"""
        meta = dict(self.ws.understanding["meta"])
        data = self.ws.understanding_disk()
        data["meta"]["run_status"] = "running"
        (self.ws.sys_root / "understanding.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
        store = _FakeStore(
            latest={"run_id": "later-render-run", "status": "completed"},
            findings_total=len(self.ws.understanding["findings"]))
        with self.assertRaises(DetailedDocError) as cm:
            precheck_detailed_run(self.ws.out_root, self.ws.system_id,
                                  store=store)
        self.assertEqual(int(cm.exception.exit_code), 2)
        self.assertIn("非状态库最新行", str(cm.exception))
        self.assertTrue(meta["run_id"])  # 场景自证：锚定 run 确实在位

    def test_interrupted_status_accepted(self):
        """锚定行=最新行且 status=interrupted → 放行（合法集合内）。"""
        meta = self.ws.understanding["meta"]
        store = _FakeStore(
            latest={"run_id": meta["run_id"], "status": "interrupted"},
            findings_total=len(self.ws.understanding["findings"]))
        ctx = precheck_detailed_run(self.ws.out_root, self.ws.system_id,
                                    store=store)
        self.assertEqual(ctx["run_status"], "interrupted")


class TestPrecheckStoreUnavailable(unittest.TestCase):
    """状态库缺失（store=None / 库无 run 行）→ 宽松放行 + 不可考声明。"""

    def setUp(self):
        """建工作区。"""
        self.ws = sfd_harness.SfdWorkspace(system_id="sfd-pre-none")
        self.ws.setUp()

    def tearDown(self):
        """清理工作区。"""
        self.ws.tearDown()

    def test_store_none_passes_with_note(self):
        """store=None → run_status_verifiable=False + "不可考" notes。"""
        ctx = precheck_detailed_run(self.ws.out_root, self.ws.system_id,
                                    store=None)
        self.assertFalse(ctx["run_status_verifiable"])
        self.assertTrue(any("run 状态不可考" in n for n in ctx["notes"]))
        # meta.run_status=completed 时透传为参考值
        self.assertEqual(ctx["run_status"], "completed")

    def test_store_none_dual_source_skipped(self):
        """store=None → 双源断言天然跳过（json 篡改条数不阻断）。"""
        data = self.ws.understanding_disk()
        data["findings"].append(dict(data["findings"][0]))
        (self.ws.sys_root / "understanding.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
        ctx = precheck_detailed_run(self.ws.out_root, self.ws.system_id,
                                    store=None)
        self.assertFalse(ctx["run_status_verifiable"])


class TestPrecheckNoWriteSideEffects(unittest.TestCase):
    """违例零写副作用：校验失败不得创建 detailed/ 任何产物（先读后写红线）。"""

    def setUp(self):
        """建工作区并渲染。"""
        self.ws = sfd_harness.SfdWorkspace(system_id="sfd-pre-ws")
        self.ws.setUp()

    def tearDown(self):
        """清理工作区。"""
        self.ws.tearDown()

    def test_violation_writes_nothing(self):
        """findings=[] 违例 → sys_root 文件集在调用前后完全一致。"""
        before = sorted(p.relative_to(self.ws.sys_root).as_posix()
                        for p in self.ws.sys_root.rglob("*") if p.is_file())
        data = self.ws.understanding_disk()
        data["findings"] = []
        (self.ws.sys_root / "understanding.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(DetailedDocError):
            precheck_detailed_run(self.ws.out_root, self.ws.system_id,
                                  store=None)
        after = sorted(p.relative_to(self.ws.sys_root).as_posix()
                       for p in self.ws.sys_root.rglob("*") if p.is_file())
        # understanding.json 是本用例自己的篡改写入（两侧都在），其余零增量
        self.assertEqual(set(after) - set(before), set())
        self.assertFalse((self.ws.sys_root / "detailed").exists())


if __name__ == "__main__":
    unittest.main()
