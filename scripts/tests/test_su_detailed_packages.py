# -*- coding: utf-8 -*-
"""SFD 素材包单测（build_material_packages / scrub / 落盘，REQ-SFD-002）。

覆盖 ARCH §10.1 切分表 "packages" 行的关键断言：
  - 五包白名单与模块常量表对表（include_keys 逐字一致）；
  - redis_keys / findings_prompt / lenses 物理不进包（白名单默认拒绝，ADR-2）；
  - manifest 锚点字段（run_id / evidence_index_sha256 / started_at /
    sources 计数 / dev 包 db_samples_masked_by）；
  - scrub_json_tree 四判据在素材包面的注入检出（C4 双因子向量）；
  - collect_package_violations 命中即 report 只含定位不含原文；
  - write_material_packages 同输入二次写出逐字节一致（NFR-SFD-002）；
  - 非 RedactedDict 包体拒写（红线①四层组合之②）。
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

from su.dto import RedactedDict  # noqa: E402
from su.detailed_doc import (  # noqa: E402
    ARCHITECT_PACKAGE_KEYS,
    DEV_PACKAGE_KEYS,
    FORBIDDEN_PACKAGE_KEYS,
    PRODUCT_PACKAGE_KEYS,
    QA_PACKAGE_KEYS,
    UI_PACKAGE_KEYS,
    DetailedDocError,
    _PACKAGE_SPECS,
    build_material_packages,
    collect_package_violations,
    scrub_json_tree,
    write_material_packages,
)


class TestPackageWhitelist(unittest.TestCase):
    """五包白名单表与常量对表 + 禁入 key 物理排除。"""

    @classmethod
    def setUpClass(cls):
        """类级渲染一次（纯函数断言共享同一 understanding）。"""
        cls.ws = sfd_harness.SfdWorkspace(system_id="sfd-pkg-wl")
        cls.ws.setUp()
        cls.packages = build_material_packages(
            cls.ws.understanding,
            cls.ws.understanding["meta"]["started_at"],
            {"ui": "collected", "api": "collected", "db": "collected",
             "redis": "skipped"},
            evidence_index_sha256="ab" * 32)

    @classmethod
    def tearDownClass(cls):
        """清理工作区。"""
        cls.ws.tearDown()

    def test_five_roles_and_keys_match_constants(self):
        """五包 role 齐全且 include_keys 与模块常量表逐字一致。"""
        expected = {
            "architect": ARCHITECT_PACKAGE_KEYS,
            "product": PRODUCT_PACKAGE_KEYS,
            "dev": DEV_PACKAGE_KEYS,
            "ui": UI_PACKAGE_KEYS,
            "qa": QA_PACKAGE_KEYS,
        }
        self.assertEqual(sorted(self.packages), sorted(expected))
        for spec in _PACKAGE_SPECS:
            self.assertEqual(spec.include_keys, expected[spec.role],
                             "spec {0} 白名单漂移".format(spec.role))
            # 白名单本体不得含禁入 key（防御性断言之上的表级自检）
            for key in spec.include_keys:
                self.assertNotIn(key, FORBIDDEN_PACKAGE_KEYS)

    def test_data_keys_exactly_whitelist(self):
        """包体 data 段 key 集合 = 白名单（多一个少一个都算违例）。"""
        for spec in _PACKAGE_SPECS:
            data = self.packages[spec.role]["data"]
            self.assertEqual(sorted(data), sorted(spec.include_keys),
                             "包 {0} data 段越界".format(spec.role))

    def test_forbidden_keys_physically_absent(self):
        """findings_prompt / lenses / redis_keys 序列化全文零出现。

        redis_keys 不在任何包白名单里（understanding.json 顶层有该段
        ——builder 种子含 redis 采集数据），"物理不进包"以序列化文本
        零出现为最严格证据。
        """
        blob = json.dumps(self.packages, ensure_ascii=False)
        self.assertNotIn("findings_prompt", blob)
        self.assertNotIn("\"lenses\"", blob)
        self.assertNotIn("redis_keys", blob)
        # 自证：源 understanding.json 磁盘上确实有 redis_keys 段
        disk = json.dumps(self.ws.understanding_disk(), ensure_ascii=False)
        self.assertIn("redis_keys", disk)


class TestPackageManifest(unittest.TestCase):
    """manifest 锚点字段与来源计数（P0-1b / AC3 / P0-3b）。"""

    @classmethod
    def setUpClass(cls):
        """类级渲染 + 构建素材包（固定 sha 便于锚点断言）。"""
        cls.ws = sfd_harness.SfdWorkspace(system_id="sfd-pkg-mf")
        cls.ws.setUp()
        cls.sha = "cafe" + "0" * 60
        cls.packages = build_material_packages(
            cls.ws.understanding, 1234.5,
            {"ui": "collected", "db": "skipped"},
            evidence_index_sha256=cls.sha)

    @classmethod
    def tearDownClass(cls):
        """清理工作区。"""
        cls.ws.tearDown()

    def test_anchor_fields(self):
        """run_id / evidence_index_sha256 / started_at 三锚点齐备。"""
        meta = self.ws.understanding["meta"]
        for role, pkg in self.packages.items():
            mf = pkg["manifest"]
            self.assertEqual(mf["run_id"], meta["run_id"], role)
            self.assertEqual(mf["evidence_index_sha256"], self.sha, role)
            self.assertEqual(mf["started_at"], 1234.5, role)
            self.assertEqual(mf["system_id"], "sfd-pkg-mf", role)
            # scrub_violations 字段结构在位（正常路径恒空列表）
            self.assertEqual(mf["scrub_violations"], [], role)

    def test_dev_package_db_samples_masked_by(self):
        """仅 dev 包携带 db_samples_masked_by=DataMasker（P0-3b）。"""
        self.assertEqual(
            self.packages["dev"]["manifest"]["db_samples_masked_by"],
            "DataMasker")
        for role in ("architect", "product", "ui", "qa"):
            self.assertNotIn("db_samples_masked_by",
                             self.packages[role]["manifest"], role)

    def test_sources_counts(self):
        """sources 计数：list 取长度、meta 记键数（AC3）。"""
        u = self.ws.understanding
        src = self.packages["architect"]["manifest"]["sources"]
        self.assertEqual(src["pages"], len(u["pages"]))
        self.assertEqual(src["db_tables"], len(u["db_tables"]))
        # meta 是 dict → len(dict)（键数），恒 >0
        self.assertGreater(src["meta"], 0)
        # lens_status 原样透传（缺失即声明的事实质）
        mf = self.packages["qa"]["manifest"]
        self.assertEqual(mf["lens_status"], {"ui": "collected",
                                             "db": "skipped"})


class TestScrubViolations(unittest.TestCase):
    """素材包面的四判据注入检出（含 C4 双因子，P0-3b）。"""

    def setUp(self):
        """建工作区（不渲染——纯函数注入用最小 understanding）。"""
        self.ws = sfd_harness.SfdWorkspace(render=False)
        self.ws.setUp()

    def tearDown(self):
        """清理工作区。"""
        self.ws.tearDown()

    def _minimal_understanding(self):
        """构造最小 understanding（db_tables 注入 C4 向量用）。

        Returns:
            dict: 含 meta 与 db_tables 的最小可切包结构。
        """
        return {
            "meta": {"run_id": "r-1", "started_at": 1.0,
                     "system_id": "x", "run_status": "completed"},
            "db_tables": [{"table": "orders",
                           "samples": {"auth_code":
                                       sfd_harness.fake_credential_token()}}],
        }

    def test_c4_entropy_key_detected_with_path(self):
        """C4：非敏感采样键名 auth_code + 高熵值 → entropy_key 命中含路径。"""
        hits = scrub_json_tree(self._minimal_understanding()["db_tables"])
        self.assertEqual(len(hits), 1)
        path, rule = hits[0]
        self.assertEqual(rule, "entropy_key")
        self.assertIn("auth_code", path)

    def test_c4_requires_dual_factor(self):
        """C4 反例：键名命中但值低熵（纯数字）→ 不误伤（双因子）。"""
        clean = scrub_json_tree(
            {"auth_code": "000000000000", "secret": "ok", "token": "短值"})
        self.assertEqual([r for _, r in clean if r == "entropy_key"], [])

    def test_collect_violations_reports_role_path_rule(self):
        """collect_package_violations 三元组含 role 且不含原文。"""
        packages = build_material_packages(
            self._minimal_understanding(), 1.0, {}, "")
        hits = collect_package_violations(packages)
        self.assertTrue(hits)
        roles = {r for r, _, _ in hits}
        self.assertIn("dev", roles)  # db_tables 只在 dev/architect 白名单
        blob = json.dumps(hits, ensure_ascii=False)
        # 扫描结果自身不成泄露面：不得携带注入的凭据值
        self.assertNotIn(sfd_harness.fake_credential_token(), blob)

    def test_run_detailed_doc_refuses_all_writes_on_violation(self):
        """素材包复核命中 → run_detailed_doc 整批拒写（无 inputs/ 落盘）。"""
        ws = sfd_harness.SfdWorkspace(system_id="sfd-pkg-rw")
        ws.setUp()
        try:
            # 真实渲染后向磁盘 understanding.json 的 db_tables 注入 C4 向量
            data = ws.understanding_disk()
            data["db_tables"][0]["samples"] = {
                "auth_code": sfd_harness.fake_credential_token()}
            (ws.sys_root / "understanding.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8")
            store = sfd_harness.StateStore(
                ws.sys_root / "state" / "understanding.sqlite", ws.system_id)
            try:
                with self.assertRaises(DetailedDocError) as cm:
                    from su.detailed_doc import run_detailed_doc
                    run_detailed_doc(ws.out_root, ws.system_id, store=store)
            finally:
                store.close()
            self.assertEqual(int(cm.exception.exit_code), 2)
            self.assertIn("scrub 复核命中", str(cm.exception))
            # 不落任何半成品：inputs/ 目录与大纲骨架都不许出现
            self.assertFalse((ws.sys_root / "detailed" / "inputs").exists())
            self.assertFalse((ws.sys_root / "SYSTEM_FUNCTION_DOC.md").exists())
        finally:
            ws.tearDown()


class TestPackageWrite(unittest.TestCase):
    """落盘：原子写五包、幂等字节一致、非 RedactedDict 拒写。"""

    def setUp(self):
        """建工作区 + 构建素材包。"""
        self.ws = sfd_harness.SfdWorkspace(system_id="sfd-pkg-w")
        self.ws.setUp()
        self.paths = None
        self.packages = build_material_packages(
            self.ws.understanding,
            self.ws.understanding["meta"]["started_at"], {}, "f" * 64)

    def tearDown(self):
        """清理工作区。"""
        self.ws.tearDown()

    def _paths(self):
        """懒构建路径集（指向当前工作区）。

        Returns:
            DetailedPaths: build_paths 产物。
        """
        from su.detailed_doc import build_paths
        return build_paths(self.ws.out_root, self.ws.system_id)

    def test_write_five_packages_and_idempotent(self):
        """五包全部落盘；同输入二次写出逐字节一致（NFR-SFD-002）。"""
        paths = self._paths()
        written = write_material_packages(paths, self.packages)
        self.assertEqual(len(written), 5)
        first = {p: (paths.inputs_dir / Path(p).name).read_bytes()
                 for p in written}
        write_material_packages(paths, self.packages)
        for p, blob in first.items():
            self.assertEqual((paths.inputs_dir / Path(p).name).read_bytes(),
                             blob, "{0} 二次写出不一致".format(p))
        # 原子写无残差
        self.assertEqual([p.name for p in paths.inputs_dir.iterdir()
                          if p.name.startswith(".tmp.")], [])

    def test_non_redacted_dict_rejected(self):
        """包体替换为普通 dict（绕过脱敏管线）→ 拒写 exit 2。"""
        paths = self._paths()
        bad = dict(self.packages)
        bad["qa"] = json.loads(json.dumps(bad["qa"]))  # 普通 dict
        with self.assertRaises(DetailedDocError) as cm:
            write_material_packages(paths, bad)
        self.assertEqual(int(cm.exception.exit_code), 2)
        self.assertIn("非 RedactedDict", str(cm.exception))
        # 断言先于落盘发生——qa 包不许写出
        self.assertFalse((paths.inputs_dir / "qa.json").exists())

    def test_packages_are_redacted_dict(self):
        """build_material_packages 全部产出 RedactedDict（红线①组合之①）。"""
        for role, pkg in self.packages.items():
            self.assertIsInstance(pkg, RedactedDict, role)


if __name__ == "__main__":
    unittest.main()
