# -*- coding: utf-8 -*-
"""SFD 装配器单测（assemble_final_doc / run_assemble，REQ-SFD-004/006）。

覆盖 ARCH §10.1 切分表 "assemble" 行的关键断言：
  - 合规五草稿 → 8/8 节 ok、零降级、引用合法率 1.0；
  - 缺草稿降级：N 份缺失 → degraded_sections 恰 N 个且终稿降级声明在位；
  - 首行容错：首部空行 / --- front matter 通过；错误节头 → degraded；
  - 04 草稿 SFD-SECTION:5 标记 0 次 → 第 5 节 degraded；≥2 次 → 4/5 同降
    且 reason="SFD-SECTION:5 标记多写"（P0-4b）；
  - 非法 E-n → "E9999 [未验证引用]" 改写 + 第 7 节汇入 + report 计数；
  - 漂移：manifest 锚点失配 → drift_suspected + 第 7 节强制漂移声明；
    带锚注同 seq 异 ref → "E0012 [漂移引用]" + ref_drifted 计数（P0-1a）；
  - token 统计口径 = 专家草稿原文（改写前），自动节 7/8 恒 0（P0-4a）；
  - outline_sha256 双记录；手改骨架 → outline_modified=true 不阻塞（P2-14）；
  - report 全字段齐备（§5.3 结构）；
  - 二次 --assemble（--force）终稿字节一致（幂等，PRD S-5）；
  - 既有终稿 status: final：无 --force → exit 2；--force → 放行（P1-5d）。
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

import sfd_harness  # noqa: E402  共享工装

from su.detailed_doc import (  # noqa: E402
    DetailedDocError,
    build_paths,
    check_existing_final,
    run_assemble,
    run_detailed_doc,
)


class _AssembleFixture(unittest.TestCase):
    """装配用例共享基座：完整链路前置（渲染→detailed-doc→草稿→装配）。"""

    def setUp(self):
        """建工作区并跑 --detailed-doc（素材包/骨架/sections 就绪）。"""
        self.ws = sfd_harness.SfdWorkspace(system_id=self.SID)
        self.ws.setUp()
        store = sfd_harness.StateStore(
            self.ws.sys_root / "state" / "understanding.sqlite",
            self.ws.system_id)
        try:
            run_detailed_doc(self.ws.out_root, self.ws.system_id, store=store)
        finally:
            store.close()
        self.paths = build_paths(self.ws.out_root, self.ws.system_id)
        self.sections = self.ws.sys_root / "detailed" / "sections"

    def tearDown(self):
        """清理工作区。"""
        self.ws.tearDown()

    def assemble(self, force=False):
        """跑 --assemble 编排。

        Args:
            force: 覆盖既有终稿开关。

        Returns:
            int: run_assemble 返回码。
        """
        return run_assemble(self.ws.out_root, self.ws.system_id, force=force)

    def e1(self):
        """当前 evidence-index 首条 seq 的展示编号（合规引用锚）。

        Returns:
            str: 形如 "E0001"。
        """
        index = json.loads((self.ws.sys_root / "evidence"
                            / "evidence-index.json").read_text("utf-8"))
        return "E{0:04d}".format(index["entries"][0]["seq"])

    def first_evidence_ref(self):
        """首条证据的原始 ref（锚注断言用）。

        Returns:
            str: 形如 "db_tables:1"。
        """
        index = json.loads((self.ws.sys_root / "evidence"
                            / "evidence-index.json").read_text("utf-8"))
        return index["entries"][0]["ref"]


class TestAssembleHappyPath(_AssembleFixture):
    """合规五草稿全链路：8/8 ok、报告字段、字节幂等。"""

    SID = "sfd-as-ok"

    def test_all_sections_ok_and_final_markers(self):
        """装配成功：8 节全 ok、status: final、无降级声明。"""
        self.ws.write_compliant_drafts()
        self.assertEqual(self.assemble(), 0)
        rep = self.ws.report()
        self.assertEqual(rep["degraded_sections"], [])
        statuses = {s["section_no"]: s["status"] for s in rep["sections"]}
        self.assertEqual(statuses, {n: ("auto" if n in (7, 8) else "ok")
                                    for n in range(1, 9)})
        doc = self.ws.final_doc_text()
        self.assertIn("status: final", doc)
        self.assertNotIn("**[降级]**", doc)
        # 终稿 8 个节标题恒 1 次（降级声明模板文本不在正文出现）
        self.assertEqual(doc.count("## 7. 未验证推断与附录"), 1)
        # 引用统计（5 份草稿各 1 处合法引用；自动节恒 0）
        self.assertEqual(rep["ref_total"], 5)
        self.assertEqual(rep["ref_invalid"], 0)
        self.assertEqual(rep["ref_drifted"], 0)
        self.assertEqual(rep["ref_legality_rate"], 1.0)
        self.assertFalse(rep["drift_suspected"])

    def test_report_full_fields(self):
        """assembly-report §5.3 全字段齐备。"""
        self.ws.write_compliant_drafts()
        self.assemble()
        rep = self.ws.report()
        required = {
            "system_id", "started_at", "assembled_at_section_basis",
            "sections", "degraded_sections", "degraded_reasons",
            "ref_total", "ref_valid", "ref_invalid", "ref_drifted",
            "ref_legality_rate", "ref_legality_target", "drift_suspected",
            "outline_modified", "credential_scan", "outline_sha256",
            "outline_sha256_on_disk", "inputs_manifest_digest",
        }
        self.assertTrue(required.issubset(set(rep)),
                        "缺字段：{0}".format(required - set(rep)))
        self.assertEqual(rep["assembled_at_section_basis"], "started_at")
        # 时间戳唯一真相源 = meta.started_at（幂等口径，ARCH §6）
        self.assertEqual(rep["started_at"],
                         self.ws.understanding["meta"]["started_at"])
        self.assertEqual(rep["credential_scan"]["status"], "clean")
        self.assertEqual(len(rep["inputs_manifest_digest"]), 5)
        # sections 条目字段
        for entry in rep["sections"]:
            self.assertTrue({"section_no", "title", "source", "status",
                             "ref_total", "ref_invalid", "ref_drifted",
                             "invalid_refs"}.issubset(set(entry)))

    def test_second_assemble_byte_identical(self):
        """重装配起点须重置骨架（--force --detailed-doc）后终稿字节一致。

        PRD S-5 的幂等口径：首次装配把骨架覆盖为终稿后，第二次装配时
        磁盘文档已非骨架（outline_modified=true 如实登记，P2-14），
        该差异是报告设计内必变字段；要复现"同一起点重装配"的字节一致，
        须先以 --force 重跑 --detailed-doc 重置骨架再装配。
        """
        self.ws.write_compliant_drafts()
        self.assemble()
        doc_path = self.ws.sys_root / "SYSTEM_FUNCTION_DOC.md"
        report_path = (self.ws.sys_root / "detailed"
                       / "assembly-report.json")
        doc_blob, rep_blob = doc_path.read_bytes(), report_path.read_bytes()
        # 同起点重装配：--force 重置骨架（status: outline）后重装配
        store = sfd_harness.StateStore(
            self.ws.sys_root / "state" / "understanding.sqlite",
            self.ws.system_id)
        try:
            check_existing_final(self.paths, force=True)
            run_detailed_doc(self.ws.out_root, self.ws.system_id,
                             store=store)
        finally:
            store.close()
        self.assertEqual(self.assemble(), 0)
        self.assertEqual(doc_path.read_bytes(), doc_blob)
        rep2 = json.loads(report_path.read_text("utf-8"))
        self.assertFalse(rep2["outline_modified"])
        self.assertEqual(doc_path.read_bytes(), doc_blob)

    def test_auto_section8_evidence_table(self):
        """第 8 节自动生成证据编号对照表（含首条编号与 ref）。"""
        self.ws.write_compliant_drafts()
        self.assemble()
        doc = self.ws.final_doc_text()
        self.assertIn(self.e1(), doc)
        self.assertIn(self.first_evidence_ref(), doc)

    def test_no_tmp_residue(self):
        """装配收尾无 .tmp.* 残差（原子写清理）。"""
        self.ws.write_compliant_drafts()
        self.assemble()
        residues = [p for p in self.ws.sys_root.rglob(".tmp.*") if p.is_file()]
        self.assertEqual(residues, [])


class TestAssembleDegradation(_AssembleFixture):
    """缺草稿 / 首行容错 / 04 标记三类降级路径（AC1 / P0-4b / P0-4c）。"""

    SID = "sfd-as-deg"

    def test_missing_drafts_degrade_exactly(self):
        """只交 2 份草稿（01、04）→ degraded_sections 恰 {2,3,5,6}。

        04 缺 SFD 标记场景不在本例（write_compliant_drafts 手工放置）；
        缺 02/03/05 三文件 → 第 2/3/6 节 degraded；04 未放置 → 4/5 同降。
        """
        drafts = self.ws.write_compliant_drafts()
        # 只保留 01；删 02/03/04/05
        for name in list(drafts):
            if name != "01-architecture.doc.md":
                (self.sections / name).unlink()
        self.assertEqual(self.assemble(), 0)  # 降级出稿不阻断（exit 0）
        rep = self.ws.report()
        self.assertEqual(rep["degraded_sections"], [2, 3, 4, 5, 6])
        doc = self.ws.final_doc_text()
        # 降级节替换为声明文本，ok 节保留正文
        self.assertIn("[降级]", doc)
        self.assertIn("素材不足/角色未完成", doc)
        # 降级节不参与引用统计（body 恒空）——仅 01 草稿 1 处引用
        self.assertEqual(rep["ref_total"], 1)

    def test_first_line_tolerance_blank_and_front_matter(self):
        """首部空行与 --- front matter 容错 → 仍 ok（P0-4c）。"""
        drafts = self.ws.write_compliant_drafts()
        p1 = self.sections / "01-architecture.doc.md"
        p1.write_text("\n\n" + drafts["01-architecture.doc.md"],
                      encoding="utf-8")
        p2 = self.sections / "02-product.doc.md"
        p2.write_text("---\ntitle: x\n---\n\n"
                      + drafts["02-product.doc.md"], encoding="utf-8")
        self.assertEqual(self.assemble(), 0)
        rep = self.ws.report()
        self.assertEqual(rep["degraded_sections"], [])

    def test_wrong_heading_degrades_section(self):
        """节头写错（## 双井号）→ 该节 degraded 且 reason 含"节头不符"。"""
        self.ws.write_compliant_drafts()
        p3 = self.sections / "03-pages.doc.md"
        text = p3.read_text(encoding="utf-8")
        p3.write_text(text.replace("# 3. ", "## 3. ", 1), encoding="utf-8")
        self.assertEqual(self.assemble(), 0)
        rep = self.ws.report()
        self.assertEqual(rep["degraded_sections"], [3])
        self.assertIn("节头不符", rep["degraded_reasons"]["3"])

    def test_section5_marker_missing_degrades_only_five(self):
        """04 草稿无标记 → 第 5 节 degraded、第 4 节仍 ok（P0-4b）。"""
        self.ws.write_compliant_drafts(with_section5=False)
        self.assertEqual(self.assemble(), 0)
        rep = self.ws.report()
        self.assertEqual(rep["degraded_sections"], [5])
        self.assertIn("SFD-SECTION: 5", rep["degraded_reasons"]["5"])

    def test_section5_marker_duplicate_degrades_four_and_five(self):
        """标记 2 次 → 第 4/5 节同时 degraded（P0-4b 整体降级）。"""
        self.ws.write_compliant_drafts()
        p4 = self.sections / "04-data-semantics.doc.md"
        text = p4.read_text(encoding="utf-8")
        p4.write_text(text + "\n重复段\n<!-- SFD-SECTION: 5 -->\n再来一段\n",
                      encoding="utf-8")
        self.assertEqual(self.assemble(), 0)
        rep = self.ws.report()
        self.assertEqual(rep["degraded_sections"], [4, 5])
        self.assertEqual(rep["degraded_reasons"]["4"],
                         "SFD-SECTION:5 标记多写")
        self.assertEqual(rep["degraded_reasons"]["5"],
                         "SFD-SECTION:5 标记多写")

    def test_degraded_doc_still_final_and_scannable(self):
        """降级出稿仍是 status: final 合法文档（降级不崩，NFR-SFD-004）。"""
        self.ws.write_compliant_drafts(with_section5=False)
        self.assemble()
        self.assertIn("status: final", self.ws.final_doc_text())


class TestAssembleEvidenceRefs(_AssembleFixture):
    """E-n 引用改写与统计口径（AC2 / P0-1a / P0-4a）。"""

    SID = "sfd-as-ref"

    def test_invalid_ref_rewrite_and_report(self):
        """非法编号 → [未验证引用] 改写 + 第 7 节汇入 + report 计数。"""
        self.ws.write_compliant_drafts()
        p1 = self.sections / "01-architecture.doc.md"
        p1.write_text(p1.read_text("utf-8") + "\n旁证 E9999 显示冗余部署。\n",
                      encoding="utf-8")
        self.assertEqual(self.assemble(), 0)
        doc = self.ws.final_doc_text()
        self.assertIn("E9999 [未验证引用]", doc)
        rep = self.ws.report()
        self.assertEqual(rep["ref_invalid"], 1)
        self.assertEqual(rep["ref_drifted"], 0)
        # ref_total 含非法 token（改写前统计口径），合法率手算复核
        self.assertEqual(rep["ref_total"], 6)
        self.assertEqual(rep["ref_valid"], 5)
        self.assertAlmostEqual(rep["ref_legality_rate"], 5 / 6, places=6)
        sec1 = [s for s in rep["sections"] if s["section_no"] == 1][0]
        self.assertEqual(sec1["invalid_refs"], ["E9999"])
        # 第 7 节未验证引用清单汇入
        self.assertIn("未验证引用清单", doc)
        self.assertIn("`E9999`", doc)

    def test_valid_anchor_ref_not_touched(self):
        """合法编号带正确锚注（锚点一致）→ 不改写、计数零。"""
        self.ws.write_compliant_drafts()
        p1 = self.sections / "01-architecture.doc.md"
        token = "{0}({1})".format(self.e1(), self.first_evidence_ref())
        p1.write_text(p1.read_text("utf-8") + "\n主证据 {0}。\n".format(token),
                      encoding="utf-8")
        self.assertEqual(self.assemble(), 0)
        doc = self.ws.final_doc_text()
        self.assertIn(token, doc)
        # 第 7 节两类清单均为"无"（"漂移引用"字样只允许出现在规约标题行）
        rep = self.ws.report()
        self.assertEqual(rep["ref_drifted"], 0)
        self.assertEqual(rep["ref_invalid"], 0)
        self.assertIn("### 漂移引用清单（同 seq 不同 ref：编号指向已跨 render 漂移）"
                      "\n\n- 无漂移引用", doc)
        self.assertIn("### 未验证引用清单（草稿引用了 evidence-index 不存在的编号）"
                      "\n\n- 无未验证引用", doc)

    def test_drift_rewrite_and_declaration(self):
        """manifest sha 篡改 → drift_suspected + 锚注异 ref → [漂移引用]。"""
        self.ws.write_compliant_drafts()
        # 带锚注但锚注与当前映射不符（模拟跨 render 编号漂移后的旧引用）
        p1 = self.sections / "01-architecture.doc.md"
        p1.write_text(p1.read_text("utf-8")
                      + "\n旧编号 E0001(pages:3) 曾指订单页。\n",
                      encoding="utf-8")
        # 篡改 architect 包 manifest 的派发锚点 → 装配期 sha 失配
        pkg = self.paths.inputs_dir / "architect.json"
        payload = json.loads(pkg.read_text("utf-8"))
        payload["manifest"]["evidence_index_sha256"] = "0" * 64
        pkg.write_text(json.dumps(payload, ensure_ascii=False,
                                  sort_keys=True, indent=2), encoding="utf-8")
        self.assertEqual(self.assemble(), 0)
        rep = self.ws.report()
        self.assertTrue(rep["drift_suspected"])
        self.assertEqual(rep["ref_drifted"], 1)
        doc = self.ws.final_doc_text()
        self.assertIn("E0001 [漂移引用]", doc)
        # 第 7 节强制漂移声明 + 漂移引用清单汇入
        self.assertIn("漂移声明", doc)
        self.assertIn("`E0001(pages:3)`", doc)
        # 合法率：漂移 token 计入不合法侧（ref_valid 手算）
        self.assertEqual(rep["ref_valid"], rep["ref_total"]
                         - rep["ref_invalid"] - rep["ref_drifted"])


class TestAssembleOutlineGuard(_AssembleFixture):
    """骨架 sha 双记录与手改检测（P1-7a / P2-14）。"""

    SID = "sfd-as-ol"

    def test_outline_double_sha_recorded(self):
        """未手改：双 sha 相等、outline_modified=false。"""
        self.ws.write_compliant_drafts()
        self.assemble()
        rep = self.ws.report()
        self.assertTrue(rep["outline_sha256"])
        # 磁盘骨架已被终稿覆盖——报告记录的是装配时点（覆盖前）骨架双值
        self.assertEqual(rep["outline_sha256_on_disk"],
                         rep["outline_sha256"])
        self.assertFalse(rep["outline_modified"])

    def test_manual_outline_edit_flagged_not_blocking(self):
        """手改骨架 → outline_modified=true 且装配照常出稿（不阻塞）。"""
        self.ws.write_compliant_drafts()
        outline = self.ws.sys_root / "SYSTEM_FUNCTION_DOC.md"
        outline.write_text(
            outline.read_text("utf-8") + "\n<!-- 手工批注 -->\n",
            encoding="utf-8")
        self.assertEqual(self.assemble(), 0)
        rep = self.ws.report()
        self.assertTrue(rep["outline_modified"])
        self.assertNotEqual(rep["outline_sha256"],
                            rep["outline_sha256_on_disk"])


class TestAssemblePrecheckAndFinalGuard(_AssembleFixture):
    """装配轻校验缺项 + 既有终稿 status: final 保护（P1-5d）。"""

    SID = "sfd-as-guard"

    def test_missing_inputs_dir_exit2(self):
        """detailed/inputs/ 缺失（未跑 --detailed-doc）→ exit 2 缺项提示。"""
        import shutil
        shutil.rmtree(self.paths.inputs_dir)
        self.ws.write_compliant_drafts()
        with self.assertRaises(DetailedDocError) as cm:
            self.assemble()
        self.assertEqual(int(cm.exception.exit_code), 2)
        self.assertIn("装配前置违例", str(cm.exception))

    def test_existing_final_without_force_exit2(self):
        """终稿 status: final 在位：无 --force → exit 2 且终稿未动。"""
        self.ws.write_compliant_drafts()
        self.assemble()
        keep = self.ws.final_doc_text()
        with self.assertRaises(DetailedDocError) as cm:
            self.assemble()  # force=False
        self.assertEqual(int(cm.exception.exit_code), 2)
        self.assertIn("--force", str(cm.exception))
        self.assertEqual(self.ws.final_doc_text(), keep)

    def test_existing_final_with_force_passes(self):
        """--force → 放行覆盖。"""
        self.ws.write_compliant_drafts()
        self.assemble()
        self.assertEqual(self.assemble(force=True), 0)

    def test_check_existing_final_skeleton_passes(self):
        """骨架（status: outline）在位不算终稿保护对象（两模式共用判定）。"""
        # setUp 刚跑完 --detailed-doc：磁盘是骨架
        check_existing_final(self.paths, force=False)  # 不抛即通过

    def test_evidence_map_token_boundaries(self):
        """E-n token 边界回归：SEQUENCE1234/E00123/e0012 不误配 4 位编号。

        草稿注入干扰串后 ref_total 只统计独立 E+4 位 token（P2-13）。
        """
        self.ws.write_compliant_drafts()
        p5 = self.sections / "05-quality.doc.md"
        p5.write_text(
            p5.read_text("utf-8") + "\n干扰串 SEQUENCE1234 E00123 e0012。\n",
            encoding="utf-8")
        self.assertEqual(self.assemble(), 0)
        rep = self.ws.report()
        # 5 份合规草稿各 1 引用 = 5；干扰串零贡献（E00123 属 5 位编号）
        self.assertEqual(rep["ref_total"], 5)
        self.assertEqual(rep["ref_invalid"], 0)


if __name__ == "__main__":
    unittest.main()
