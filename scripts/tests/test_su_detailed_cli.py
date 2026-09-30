# -*- coding: utf-8 -*-
"""SFD CLI 层单测（argparse 互斥 + 组合拒绝 + 必填校验，REQ-SFD-005）。

覆盖 ARCH §10.1 切分表 "cli" 行的关键断言：
  - mode_group 三 flag（--render-only/--detailed-doc/--assemble）两两互斥
    → argparse 标准 SystemExit(2)；
  - --fresh / --resume / --skip-llm-phase 与两 SFD 模式组合 → exit 2
    （--resume 经 sys.argv 扫描判定，测试需 patch sys.argv 模拟显式给出）；
  - 缺 --out / --system-id → exit 2（两模式同口径，P1-5b/P2-12）；
  - 既有终稿 status: final：--detailed-doc 无 --force → exit 2；
    --force 放行覆盖（P1-5d）；
  - 违例路径零副作用（不创建 detailed/ 产物）。

口径说明：SFD 分支在 SystemUnderstanding.run() 内以 SuConfigError
（DetailedDocError 语义别名）收口为返回码 2，不抛异常——测试统一经
``SystemUnderstanding(args).run()`` 返回值断言，与 main() 行为一致。
"""

import argparse
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

from system_understanding import (  # noqa: E402
    SystemUnderstanding,
    build_arg_parser,
)


def _base_args(**overrides):
    """构造 SFD 分支所需最小 argparse.Namespace（属性全集显式给出）。

    默认值与 build_arg_parser 的 SFD 相关默认逐项一致：resume=True
    （--resume 默认）、其余布尔 flag False、路径/标识默认给全——用例
    只覆写被测维度，杜绝缺属性路径干扰。

    Args:
        **overrides: 覆写属性。

    Returns:
        argparse.Namespace: 编程式 args。
    """
    defaults = dict(
        out=None, system_id=None, verbose=False,
        resume=True, skip_llm_phase=False,
        render_only=False, detailed_doc=False, assemble=False, force=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestArgparseMutex(unittest.TestCase):
    """mode_group 三 flag 两两互斥 → argparse SystemExit(2)。"""

    def _parse_fails(self, argv):
        """断言 argv 解析以 SystemExit(2) 收口。

        Args:
            argv: 参数列表（不含 prog）。
        """
        parser = build_arg_parser()
        with self.assertRaises(SystemExit) as cm:
            parser.parse_args(argv)
        self.assertEqual(cm.exception.code, 2)

    def test_render_only_vs_detailed_doc(self):
        """--render-only + --detailed-doc → exit 2。"""
        self._parse_fails(["--render-only", "--detailed-doc"])

    def test_render_only_vs_assemble(self):
        """--render-only + --assemble → exit 2。"""
        self._parse_fails(["--render-only", "--assemble"])

    def test_detailed_doc_vs_assemble(self):
        """--detailed-doc + --assemble → exit 2。"""
        self._parse_fails(["--detailed-doc", "--assemble"])

    def test_resume_vs_fresh(self):
        """--resume + --fresh（既有互斥组）→ exit 2。"""
        self._parse_fails(["--resume", "--fresh"])

    def test_single_mode_parses(self):
        """单 flag 正常解析（互斥组不误伤合法输入）。"""
        args = build_arg_parser().parse_args(
            ["--detailed-doc", "--out", "/tmp/x", "--system-id", "s"])
        self.assertTrue(args.detailed_doc)
        self.assertFalse(args.assemble)


class TestLifecycleFlagRejection(unittest.TestCase):
    """采集生命周期参数 × 两 SFD 模式组合 → run() 返回 2（ARCH §3.2）。"""

    def setUp(self):
        """建工作区（渲染 + detailed-doc 前置产物齐备——违例判定须先于编排）。"""
        self.ws = sfd_harness.SfdWorkspace(system_id="sfd-cli-lc")
        self.ws.setUp()

    def tearDown(self):
        """清理工作区。"""
        self.ws.tearDown()

    def _run_with_argv(self, args, extra_argv):
        """patch sys.argv 后跑 run()（--resume 显式性经 argv 扫描）。

        Args:
            args: 编程式 Namespace。
            extra_argv: 模拟的显式命令行参数（进 sys.argv[1:]）。

        Returns:
            int: run() 返回码。
        """
        saved = sys.argv
        sys.argv = ["system_understanding.py"] + list(extra_argv)
        try:
            return SystemUnderstanding(args).run()
        finally:
            sys.argv = saved

    def _assert_rejected(self, mode_flag, lifecycle_flag):
        """两模式 × 单生命周期参数组合统一断言 exit 2。

        Args:
            mode_flag: "--detailed-doc" / "--assemble"。
            lifecycle_flag: "--fresh" / "--resume" / "--skip-llm-phase"。
        """
        overrides = {"out": str(self.ws.out_root),
                     "system_id": self.ws.system_id}
        overrides[mode_flag.lstrip("-").replace("-", "_")] = True
        if lifecycle_flag == "--fresh":
            overrides["resume"] = False  # --fresh 语义：store_false 翻转
        if lifecycle_flag == "--skip-llm-phase":
            # store_true 显式性判定消费 args 值（argparse 解析后恒 True）
            overrides["skip_llm_phase"] = True
        argv_extra = [mode_flag, lifecycle_flag]
        code = self._run_with_argv(_base_args(**overrides), argv_extra)
        self.assertEqual(code, 2,
                         "{0} + {1} 应拒绝".format(mode_flag, lifecycle_flag))

    def test_detailed_doc_rejects_fresh(self):
        """--detailed-doc --fresh → 2。"""
        self._assert_rejected("--detailed-doc", "--fresh")

    def test_detailed_doc_rejects_resume(self):
        """--detailed-doc --resume（显式，argv 扫描）→ 2。"""
        self._assert_rejected("--detailed-doc", "--resume")

    def test_detailed_doc_rejects_skip_llm_phase(self):
        """--detailed-doc --skip-llm-phase → 2。"""
        self._assert_rejected("--detailed-doc", "--skip-llm-phase")

    def test_assemble_rejects_fresh(self):
        """--assemble --fresh → 2。"""
        self._assert_rejected("--assemble", "--fresh")

    def test_assemble_rejects_resume(self):
        """--assemble --resume（显式）→ 2。"""
        self._assert_rejected("--assemble", "--resume")

    def test_assemble_rejects_skip_llm_phase(self):
        """--assemble --skip-llm-phase → 2。"""
        self._assert_rejected("--assemble", "--skip-llm-phase")

    def test_default_resume_true_not_rejected(self):
        """args.resume=True 默认值（未显式给 --resume/--fresh）不误杀。

        --detailed-doc 正常链路在此工作区可直接跑通（返回 0）——同时
        验证判定顺序"组合拒绝先于必填校验"不产生误报。
        """
        argv = ["--detailed-doc", "--out", str(self.ws.out_root),
                "--system-id", self.ws.system_id]
        saved = sys.argv
        sys.argv = ["system_understanding.py"] + argv
        try:
            args = build_arg_parser().parse_args(argv)
            code = SystemUnderstanding(args).run()
        finally:
            sys.argv = saved
        self.assertEqual(code, 0)


class TestRequiredArgs(unittest.TestCase):
    """缺 --out / --system-id → 2（两模式同口径）。"""

    def test_detailed_doc_missing_out(self):
        """--detailed-doc 缺 --out → 2。"""
        code = SystemUnderstanding(
            _base_args(detailed_doc=True, system_id="s")).run()
        self.assertEqual(code, 2)

    def test_detailed_doc_missing_system_id(self):
        """--detailed-doc 缺 --system-id → 2。"""
        code = SystemUnderstanding(
            _base_args(detailed_doc=True, out="/tmp/none")).run()
        self.assertEqual(code, 2)

    def test_assemble_missing_out(self):
        """--assemble 缺 --out → 2。"""
        code = SystemUnderstanding(
            _base_args(assemble=True, system_id="s")).run()
        self.assertEqual(code, 2)

    def test_assemble_missing_system_id(self):
        """--assemble 缺 --system-id → 2。"""
        code = SystemUnderstanding(
            _base_args(assemble=True, out="/tmp/none")).run()
        self.assertEqual(code, 2)

    def test_missing_required_writes_nothing(self):
        """必填违例判定先于一切产物写面（out 指向空目录零创建）。"""
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="sfd-cli-empty."))
        try:
            code = SystemUnderstanding(
                _base_args(detailed_doc=True, out=str(tmp))).run()
            self.assertEqual(code, 2)
            self.assertEqual(list(tmp.iterdir()), [])
        finally:
            import shutil
            shutil.rmtree(str(tmp), ignore_errors=True)


class TestFinalProtectionViaCli(unittest.TestCase):
    """既有终稿 status: final 的 CLI 面保护与 --force 放行（P1-5d）。"""

    def setUp(self):
        """完整链路：渲染 → detailed-doc → 合规草稿 → 装配出终稿。"""
        self.ws = sfd_harness.SfdWorkspace(system_id="sfd-cli-final")
        self.ws.setUp()
        from su.detailed_doc import run_assemble, run_detailed_doc
        store = sfd_harness.StateStore(
            self.ws.sys_root / "state" / "understanding.sqlite",
            self.ws.system_id)
        try:
            run_detailed_doc(self.ws.out_root, self.ws.system_id, store=store)
        finally:
            store.close()
        self.ws.write_compliant_drafts()
        run_assemble(self.ws.out_root, self.ws.system_id)
        self.final_before = self.ws.final_doc_text()

    def tearDown(self):
        """清理工作区。"""
        self.ws.tearDown()

    def _run_detailed_doc_cli(self, force=False):
        """以 CLI 门面跑 --detailed-doc。

        Args:
            force: 是否 --force。

        Returns:
            int: run() 返回码。
        """
        return SystemUnderstanding(_base_args(
            detailed_doc=True, out=str(self.ws.out_root),
            system_id=self.ws.system_id, force=force)).run()

    def test_detailed_doc_on_final_without_force_exit2(self):
        """终稿在位：--detailed-doc 无 --force → 2 且终稿未动。"""
        self.assertEqual(self._run_detailed_doc_cli(force=False), 2)
        self.assertEqual(self.ws.final_doc_text(), self.final_before)

    def test_detailed_doc_on_final_with_force_passes(self):
        """--force → 放行（骨架重置回 status: outline）。"""
        self.assertEqual(self._run_detailed_doc_cli(force=True), 0)
        head = (self.ws.sys_root / "SYSTEM_FUNCTION_DOC.md").read_text(
            "utf-8")[:400]
        self.assertIn("status: outline", head)

    def test_assemble_on_final_without_force_exit2(self):
        """终稿在位：--assemble 无 --force → 2（轻校验层收口）。"""
        code = SystemUnderstanding(_base_args(
            assemble=True, out=str(self.ws.out_root),
            system_id=self.ws.system_id)).run()
        self.assertEqual(code, 2)
        self.assertEqual(self.ws.final_doc_text(), self.final_before)


if __name__ == "__main__":
    unittest.main()
