# -*- coding: utf-8 -*-
"""SFD 凭据扫描单测（scan_credential_leak / finalize_assembly，REQ-SFD-013）。

覆盖 ARCH §10.1 切分表 "scan" 行 + PRD S-3 判据口径：
  - C1 pii：手机号 / email / 长数字卡号，正反例 ≥3；
  - C2 url_userinfo：明文 URL 凭据命中；***REDACTED***@ 与 <REDACTED:*>@
    脱敏形态豁免（P1-10——回归缺陷 D2：旧实现把占位换成含 @ 残句导致
    误判命中）；
  - C3 kv_credential：password=/token=/api_key= 命中，中文叙述反例；
  - C4 entropy_key：JSON 键名扩展词表 + 高熵值双因子；md 文本无键名语境
    不启用 C4；
  - 正/反例样例一律运行时拼接（fake_* 工装），仓库不落可 grep 明文；
  - finalize_assembly：命中 → 返回 2 且终稿/报告都不落盘、上一版完好、
    打印只含位置类别不含原文。
"""

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
_FIXTURES_DIR = TESTS_DIR / "fixtures"
for _p in (str(_FIXTURES_DIR), str(TESTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sfd_harness  # noqa: E402  共享工装

from su.config import scrub_text  # noqa: E402
from su.dto import REDACTED_PLACEHOLDER  # noqa: E402
from su.detailed_doc import (  # noqa: E402
    _url_userinfo_hit,
    finalize_assembly,
    build_paths,
    run_detailed_doc,
    scan_credential_leak,
)


def _rules(text):
    """对单行文本跑逐行三判据面（C1-C3），返回命中类别集合。

    Args:
        text: 单行文本（含换行会破坏行号定位语义，调用方保证单行）。

    Returns:
        set[str]: 命中类别（空集 = 无命中）。
    """
    report = scan_credential_leak({"probe.md": text})
    return {h.rule for h in report.hits}


class TestC1Pii(unittest.TestCase):
    """C1：PII 值形态（scrub_text 差集判据）。"""

    def test_phone_detected(self):
        """手机号值形态 → pii 命中。"""
        self.assertIn("pii", _rules(sfd_harness.fake_pii_line()))

    def test_email_detected(self):
        """邮箱值形态 → pii 命中（拼接生成）。"""
        line = "运维邮箱 " + "ops" + "@" + "corp" + "-" + "example" + ".com 备案"
        self.assertIn("pii", _rules(line))

    def test_long_digit_card_detected(self):
        """16-19 位连续数字（卡号形态）→ pii 命中。"""
        line = "结算账号 " + "6222" * 4 + " 请核对"
        self.assertIn("pii", _rules(line))

    def test_normal_chinese_narrative_clean(self):
        """正常中文叙述（业务语义、无值形态）→ 零命中。"""
        self.assertEqual(_rules(
            "订单列表页聚合展示订单明细，支持按状态筛选后导出对账单。"), set())

    def test_redacted_placeholders_clean(self):
        """已脱敏占位形态 <REDACTED:phone> 自引用 → 零命中（审计文本面）。"""
        self.assertEqual(_rules(
            "采样值已脱敏为 <REDACTED:phone> 形态入库。"), set())

    def test_short_digits_clean(self):
        """普通短数字（金额/计数）→ 不误伤。"""
        self.assertEqual(_rules("共 12345 笔订单，合计 6789 元。"), set())


class TestC2UrlUserinfo(unittest.TestCase):
    """C2：URL 内嵌凭据 + 脱敏形态豁免（P1-10 / 缺陷 D2 回归）。"""

    def test_plain_url_credentials_detected(self):
        """明文 mysql://user:pwd@host → url_userinfo 命中。"""
        self.assertIn("url_userinfo",
                      _rules("连接串 " + sfd_harness.fake_url_credential_line()))

    def test_http_basic_auth_detected(self):
        """http://u:p@host 形态 → url_userinfo 命中（拼接生成）。"""
        line = "回调配置 http://" + "admin" + ":" + "Adm1" + "nPwd" + "@gw.internal/cb"
        self.assertTrue(_url_userinfo_hit(line))

    def test_redis_url_detected(self):
        """redis:// 任意 scheme 通用 → url_userinfo 命中。"""
        line = "缓存连接 redis://" + "cache" + ":" + "R3d1" + "sCred" + "@redis.internal:6379/0"
        self.assertTrue(_url_userinfo_hit(line))

    def test_scrubbed_star_redacted_exempt(self):
        """***REDACTED***@ 形态（scrub 后自引用）→ 豁免不命中。

        缺陷 D2 回归锚：旧实现 sub 残句仍含 '@'，本断言在修复前必红。
        """
        line = "审计转录 mysql://" + REDACTED_PLACEHOLDER + "@db.internal:3306/appdb"
        self.assertFalse(_url_userinfo_hit(line))

    def test_scrubbed_pii_placeholder_exempt(self):
        """<REDACTED:email>@ 形态 → 豁免不命中。"""
        line = "示例 redis://<REDACTED:email>@redis.internal:6379/0 已脱敏"
        self.assertFalse(_url_userinfo_hit(line))

    def test_scrub_output_stable_under_c2(self):
        """scrub_text 输出行二次扫描 C2 恒零命中（自引用稳定）。"""
        original = sfd_harness.fake_url_credential_line()
        scrubbed = scrub_text(original)
        self.assertNotEqual(scrubbed, original)  # 自证确实被脱敏
        self.assertNotIn("url_userinfo", _rules(scrubbed))

    def test_plain_url_without_userinfo_clean(self):
        """无 userinfo 的普通 URL → 不命中（path 中 @ 不算）。"""
        self.assertFalse(_url_userinfo_hit(
            "https://example.com/next?to=user@corp.example.com"))


class TestC3KvCredential(unittest.TestCase):
    """C3：键值对形态凭据（_CLAIM_CREDENTIAL_RE 同源口径）。"""

    def test_password_kv_detected(self):
        """password=<值> → kv_credential 命中。"""
        self.assertIn("kv_credential",
                      _rules("配置行 " + sfd_harness.fake_kv_credential_line()))

    def test_token_kv_detected(self):
        """token: <值> 冒号形态 → kv_credential 命中。"""
        self.assertIn("kv_credential",
                      _rules("token: " + "Tk10" + "ab29" + "cd38"))

    def test_api_key_kv_detected(self):
        """api-key=<值> 连字符形态 → kv_credential 命中。"""
        self.assertIn("kv_credential",
                      _rules("api-key=" + "Ak9x" + "7qW2" + "Zz41"))

    def test_chinese_prose_about_password_clean(self):
        """中文叙述"密码策略"（无键值对形态）→ 不误伤。"""
        self.assertEqual(_rules(
            "系统密码策略要求 12 位以上混合字符，登录失败五次锁定。"), set())

    def test_token_word_in_prose_clean(self):
        """"令牌"业务语义叙述（无 token= 形态）→ 不误伤。"""
        self.assertEqual(_rules(
            "分布式令牌桶限流器保护下单接口，突发容量 200。"), set())


class TestC4EntropyKey(unittest.TestCase):
    """C4：JSON 结构化双因子（键名扩展词表 + 高熵值，P0-3b）。"""

    def test_auth_code_json_detected(self):
        """{"auth_code": <12+位字母数字>} → entropy_key 命中（PRD S-3 向量）。"""
        payload = {"auth_code": sfd_harness.fake_credential_token()}
        report = scan_credential_leak(
            {"pkg.json": json.dumps(payload, ensure_ascii=False)})
        self.assertIn("entropy_key", report.categories)

    def test_nested_json_detected(self):
        """嵌套结构深层键命中且定位含点分路径。"""
        payload = {"data": {"rows": [{"secret_value":
                                      sfd_harness.fake_credential_token()}]}}
        report = scan_credential_leak(
            {"pkg.json": json.dumps(payload, ensure_ascii=False)})
        hits = [h for h in report.hits if h.rule == "entropy_key"]
        self.assertEqual(len(hits), 1)
        self.assertIn("secret_value", hits[0].location)

    def test_pure_digits_value_not_entropy(self):
        """C4 反例：键名命中但纯数字值（无字母）→ 不构成 entropy_key。

        注：18 位纯数字会命中 C1 bank_card 值形态——断言只锁定
        entropy_key 类别不出现，双因子判据不被单因子击穿。
        """
        payload = {"auth_code": "9" * 18}
        report = scan_credential_leak(
            {"pkg.json": json.dumps(payload, ensure_ascii=False)})
        self.assertNotIn("entropy_key", report.categories)

    def test_short_mixed_value_not_entropy(self):
        """C4 反例：含字母数字但长度 <12 → 不构成 entropy_key。"""
        payload = {"auth_code": "a1b2c3d4"}
        report = scan_credential_leak(
            {"pkg.json": json.dumps(payload, ensure_ascii=False)})
        self.assertNotIn("entropy_key", report.categories)

    def test_pure_letters_value_not_entropy(self):
        """C4 反例：长纯字母值（无数字）→ 不构成 entropy_key。"""
        payload = {"auth_code": "abcdefghij" * 2}
        report = scan_credential_leak(
            {"pkg.json": json.dumps(payload, ensure_ascii=False)})
        self.assertNotIn("entropy_key", report.categories)

    def test_nonsensitive_key_high_entropy_clean(self):
        """C4 反例：值高熵但键名不含敏感词 → 不构成 entropy_key。"""
        payload = {"order_serial": sfd_harness.fake_credential_token()}
        report = scan_credential_leak(
            {"pkg.json": json.dumps(payload, ensure_ascii=False)})
        self.assertNotIn("entropy_key", report.categories)

    def test_markdown_text_c4_disabled(self):
        """md 文本无键名语境：同载荷按 .md 扫描 → 恒无 entropy_key。"""
        payload_text = '"auth_code": "' + sfd_harness.fake_credential_token() + '"'
        report = scan_credential_leak({"note.md": payload_text})
        self.assertNotIn("entropy_key", report.categories)

    def test_broken_json_falls_back_lines(self):
        """JSON 解析失败 → 逐行 C1-C3 兜底（PII 仍命中、C4 降级不可用）。"""
        broken = "{ 损坏: [auth_code: " + sfd_harness.fake_credential_token()
        report = scan_credential_leak({"broken.json": broken})
        # 键值行本身不含 password|token|api_key 关键词、无 PII 值形态——
        # 兜底面允许零命中，但绝不因解析失败而抛异常
        self.assertIsInstance(report.hits, list)
        # 兜底"零命中也合法"的正向对拍：同一损坏文本里嵌入 PII 行——
        # 兜底逐行 C1-C3 必须确有执行（PII 值形态在兜底面照常命中），
        # 否则"零命中"可能只是兜底扫描根本没跑
        broken_pii = broken + "\n联系电话 13812345678"
        report_pii = scan_credential_leak({"broken.json": broken_pii})
        self.assertIn("pii", report_pii.categories)
        # C3 键值对判据在兜底面同样执行：password= 明文行必须命中
        broken_kv = "corrupt { password=" + sfd_harness.fake_credential_token()
        report_kv = scan_credential_leak({"broken.json": broken_kv})
        self.assertIn("kv_credential", report_kv.categories)


class TestScanReportShape(unittest.TestCase):
    """扫描报告形状：只记位置类别，绝不携带原文（不成泄露面）。"""

    def test_hits_never_contain_plaintext(self):
        """四判据全命中场景下，报告序列化零出现任何注入值。"""
        secret = sfd_harness.fake_credential_token()
        named = {
            "a.md": sfd_harness.fake_pii_line() + "\n"
                    + sfd_harness.fake_url_credential_line() + "\n"
                    + sfd_harness.fake_kv_credential_line(),
            "b.json": json.dumps({"auth_code": secret}),
        }
        report = scan_credential_leak(named)
        blob = json.dumps([(h.location, h.rule) for h in report.hits])
        self.assertNotIn(secret, blob)
        # 自证：四类判据在该组输入下确实全部触发（.json 后缀启用 C4 walk；
        # C2 的 ***REDACTED*** 掩码豁免例外不存在——fake_url 为明文形态）
        self.assertEqual(report.categories,
                         ["entropy_key", "kv_credential", "pii",
                          "url_userinfo"])

    def test_categories_sorted_dedup(self):
        """categories 去重排序。"""
        report = scan_credential_leak({
            "a.md": sfd_harness.fake_kv_credential_line(),
            "b.md": "第二行 " + sfd_harness.fake_kv_credential_line(),
        })
        self.assertEqual(report.categories, ["kv_credential"])


class TestFinalizeAssemblyGate(unittest.TestCase):
    """finalize_assembly 收口：命中不落盘、上一版完好、输出不含原文。"""

    def setUp(self):
        """完整链路前置：渲染 → detailed-doc → 合规草稿 → 首次装配。"""
        self.ws = sfd_harness.SfdWorkspace(system_id="sfd-scan-fin")
        self.ws.setUp()
        store = sfd_harness.StateStore(
            self.ws.sys_root / "state" / "understanding.sqlite",
            self.ws.system_id)
        try:
            run_detailed_doc(self.ws.out_root, self.ws.system_id, store=store)
        finally:
            store.close()
        self.ws.write_compliant_drafts()
        code = finalize_assembly(
            build_paths(self.ws.out_root, self.ws.system_id),
            *self._assemble())
        self.assertEqual(code, 0)  # 首次装配必须成功（基线版本）
        self.first_final = self.ws.final_doc_text()

    def tearDown(self):
        """清理工作区。"""
        self.ws.tearDown()

    def _assemble(self):
        """跑装配纯函数取 (body, report)。

        Returns:
            tuple: assemble_final_doc 产物。
        """
        from su.detailed_doc import assemble_final_doc, load_evidence_index
        paths = build_paths(self.ws.out_root, self.ws.system_id)
        return assemble_final_doc(
            paths, self.ws.understanding_disk(),
            load_evidence_index(paths.evidence_index), False)

    def test_hit_blocks_write_and_preserves_previous(self):
        """草稿注入假凭据 → finalize 返回 2、终稿与上一版逐字节一致。"""
        # 向 03 草稿注入 C1（手机号）与 C4 向量（终稿 md 面）+ C3
        tainted = (self.ws.sys_root / "detailed" / "sections"
                   / "03-pages.doc.md")
        text = tainted.read_text(encoding="utf-8")
        tainted.write_text(
            text + "\n" + sfd_harness.fake_pii_line() + "\n"
            + sfd_harness.fake_kv_credential_line() + "\n",
            encoding="utf-8")
        body, report = self._assemble()
        paths = build_paths(self.ws.out_root, self.ws.system_id)
        report_path = paths.detailed_dir / "assembly-report.json"
        report_before = report_path.read_bytes()
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = finalize_assembly(paths, body, report)
        self.assertEqual(code, 2)
        # 终稿保持上一版（逐字节一致），报告未更新
        self.assertEqual(self.ws.final_doc_text(), self.first_final)
        self.assertEqual(report_path.read_bytes(), report_before)
        # 输出只含位置类别；不携带注入凭据原文
        # （fake KV 行同时命中 C1——拼接值含 11 位手机号形态子串——
        #  断言锁定 C3 类别在位即可，C1 命中属判据口径内正常现象）
        printed = buf.getvalue()
        self.assertIn("凭据扫描命中", printed)
        self.assertIn("[kv_credential]", printed)
        self.assertNotIn(sfd_harness.fake_pii_line(), printed)
        self.assertNotIn(sfd_harness.fake_kv_credential_line(), printed)

    def test_clean_path_updates_report_scan_section(self):
        """扫描通过 → credential_scan 收口为 clean 且终稿含 status: final。"""
        body, report = self._assemble()
        code = finalize_assembly(
            build_paths(self.ws.out_root, self.ws.system_id), body, report)
        self.assertEqual(code, 0)
        rep = self.ws.report()
        self.assertEqual(rep["credential_scan"]["status"], "clean")
        self.assertIn("status: final", self.ws.final_doc_text())


if __name__ == "__main__":
    unittest.main()
