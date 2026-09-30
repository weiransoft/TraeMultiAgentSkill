# -*- coding: utf-8 -*-
"""SFD 大纲骨架单测（render_outline / --detailed-doc 幂等，REQ-SFD-003）。

覆盖 ARCH §10.1 切分表 "outline" 行的关键断言：
  - 8 节标题与 SECTION_TITLES_SFD 逐字一致且节序固定；
  - 头部 status: outline 标记行（装配器"骨架 vs 终稿"判定依据，P1-5d）；
  - 专家负责节占位行含角色中文名 + 草稿绝对路径；自动节（7/8）自动声明；
  - 第 2 节 Mermaid 预留块三种语言标注（flowchart/sequenceDiagram/
    stateDiagram-v2，AC2）；
  - 同输入二次渲染逐字节一致（幂等，NFR-SFD-002）；
  - generated 行取 meta.started_at（run 级常量，禁 time.time()）；
  - run_detailed_doc 连跑两次产物字节一致（大纲 + 五包）。
"""

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
    SECTION_SOURCE_SFD,
    SECTION_TITLES_SFD,
    _PACKAGE_SPECS,
    build_paths,
    render_outline,
)


def _draft_placeholder_segment(source_rel, paths, role_by_source):
    """从 SECTION_SOURCE_SFD 契约推导指定草稿的占位行全文。

    与 render_outline 内部拼接式同源（"> 待专家回填：{角色}（草稿文件：
    {绝对路径}）"）——用例独立复算期望值，不复用被测函数输出。

    Args:
        source_rel: 草稿相对路径（SECTION_SOURCE_SFD 值形态）。
        paths: 详说路径集。
        role_by_source: 草稿相对路径 → 角色中文名映射（测试自表）。

    Returns:
        str: 期望的占位行全文。
    """
    draft_abs = str((paths.detailed_dir / source_rel).resolve())
    return "> 待专家回填：{0}（草稿文件：{1}）".format(
        role_by_source[source_rel], draft_abs)


class TestOutlineStructure(unittest.TestCase):
    """8 节结构 / status 行 / 占位行 / Mermaid 预留。"""

    @classmethod
    def setUpClass(cls):
        """类级渲染一次并出大纲（结构断言共享同一文本）。"""
        cls.ws = sfd_harness.SfdWorkspace(system_id="sfd-ol")
        cls.ws.setUp()
        cls.paths = build_paths(cls.ws.out_root, cls.ws.system_id)
        cls.text = render_outline(cls.paths, cls.ws.understanding)

    @classmethod
    def tearDownClass(cls):
        """清理工作区。"""
        cls.ws.tearDown()

    def test_eight_section_headings_in_order(self):
        """8 节标题逐字在位且节序 = 1..8。"""
        positions = []
        for no in sorted(SECTION_TITLES_SFD):
            heading = "## {0}. {1}".format(no, SECTION_TITLES_SFD[no])
            pos = self.text.find(heading)
            self.assertGreater(pos, -1, "缺节标题：{0}".format(heading))
            positions.append(pos)
        # 节序单调递增（文档序即节号序）
        self.assertEqual(positions, sorted(positions))

    def test_status_outline_line_present(self):
        """头部含 status: outline 标记行（终稿装配前骨架身份标识）。"""
        head = self.text[:600]
        self.assertIn("status: outline", head)
        self.assertNotIn("status: final", head)

    def test_expert_sections_placeholder(self):
        """专家节占位行：角色中文名（_PACKAGE_SPECS 契约同源）+ 绝对路径。"""
        # 角色名表从 _PACKAGE_SPECS 推导（渲染器同源键：output_section），
        # 再映射到 SECTION_SOURCE_SFD 的 "sections/<文件名>" 键形态
        role_by_source = {"sections/" + spec.output_section: spec.role_label
                          for spec in _PACKAGE_SPECS}
        for no in (1, 2, 3, 5, 6):
            source = SECTION_SOURCE_SFD[no]
            self.assertIn(
                _draft_placeholder_segment(source, self.paths,
                                           role_by_source),
                self.text, "第 {0} 节占位行缺失或角色/路径错误".format(no))

    def test_auto_sections_declaration(self):
        """第 7/8 节为自动节声明（无"待专家回填"占位）。"""
        auto_decl = "本节由 --assemble 装配时自动生成"
        # 恰好两处（7/8 两节各一）
        self.assertEqual(self.text.count(auto_decl), 2)

    def test_mermaid_three_language_tags(self):
        """第 2 节 Mermaid 预留三种语言标注（AC2）。"""
        for tag in ("```flowchart", "```sequenceDiagram",
                    "```stateDiagram-v2"):
            self.assertIn(tag, self.text)
        # stateDiagram-v2 另在第 2 节 2.4 小节预留（单一出现即可满足
        # AC2 三形态；出现次数只增不减由后续演进保证，此处不设上界）
        self.assertGreaterEqual(self.text.count("```stateDiagram-v2"), 1)

    def test_generated_line_uses_started_at(self):
        """generated 行 = repr(meta.started_at)（run 级常量真相源）。"""
        started = self.ws.understanding["meta"]["started_at"]
        self.assertIn("<!-- generated: {0} -->".format(repr(float(started))),
                      self.text)

    def test_section5_marker_instruction(self):
        """第 5 节说明行给出 SFD-SECTION:5 逐字标记样例。"""
        self.assertIn("<!-- SFD-SECTION: 5 -->", self.text)

    def test_reference_convention_section(self):
        """引用规约段在位：E-nnnn、[推断]、两类改写标记说明。"""
        for token in ("E-nnnn", "[推断]", "[未验证引用]", "[漂移引用]"):
            self.assertIn(token, self.text)


class TestOutlineIdempotency(unittest.TestCase):
    """幂等：同输入二次渲染 / run_detailed_doc 连跑字节一致。"""

    def setUp(self):
        """建工作区。"""
        self.ws = sfd_harness.SfdWorkspace(system_id="sfd-ol-idem")
        self.ws.setUp()

    def tearDown(self):
        """清理工作区。"""
        self.ws.tearDown()

    def test_render_outline_byte_stable(self):
        """render_outline 纯函数：同输入两次调用逐字节一致。"""
        paths = build_paths(self.ws.out_root, self.ws.system_id)
        first = render_outline(paths, self.ws.understanding)
        second = render_outline(paths, self.ws.understanding)
        self.assertEqual(first, second)

    def test_run_detailed_doc_twice_identical(self):
        """run_detailed_doc 连跑两次：大纲与五包全部字节一致（PRD S-5）。"""
        first = {}
        for round_no in (1, 2):
            rc = self.ws.run_detailed_doc()
            self.assertEqual(rc, 0)
            snapshot = {}
            for p in sorted(self.ws.sys_root.rglob("*")):
                if p.is_file():
                    snapshot[p.relative_to(self.ws.sys_root).as_posix()] = \
                        p.read_bytes()
            if round_no == 1:
                first = snapshot
            else:
                self.assertEqual(sorted(snapshot), sorted(first))
                for name, blob in snapshot.items():
                    self.assertEqual(blob, first[name],
                                     "{0} 二次跑批不一致".format(name))


if __name__ == "__main__":
    unittest.main()
