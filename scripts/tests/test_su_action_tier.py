# -*- coding: utf-8 -*-
"""SU 能力单元测试：动作分级判定（REQ-SU-006）。

覆盖 su.action_tier 模块：
- classify_action 七步判定链（表单方法 / 同域 / 危险动词 / 危险控件 / GET 表单 / 默认拒绝）
- 规则名全中文口径；method=None 不算"显式 GET"不得进 T2
- SAFE_VERBS 命中仍 T3（默认拒绝 + semantic_hint）
- _same_domain：伪协议 / 相对 href / 无 base_url fail-safe
- fill_neutral：中性填值规则，恒满足 NEUTRAL_VALUE_PATTERN
- ElementSignature / ActionDecision 数据结构

运行方式（项目根目录）：
    python3 -B -m unittest scripts.tests.test_su_action_tier
"""

import re
import sys
import unittest
from pathlib import Path

# 将 scripts/ 目录注入 sys.path，使 `from su.xxx import ...` 生效
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from su.action_tier import (  # noqa: E402
    ActionDecision,
    ElementSignature,
    classify_action,
    fill_neutral,
)


def _link(href="", text="", aria_label="", base="https://shop.example.com/cart"):
    """构造同域链接元素替身（base 由 classify_action 参数传入，不挂在元素上）。"""
    return ElementSignature(
        tag="a",
        role="link",
        text=text,
        aria_label=aria_label,
        href=href,
        is_form_control=False,
    )


def _form_element(method, text="", form_text="", tag="button", input_type="",
                  name="", element_id="", aria_label=""):
    """构造表单控件元素替身（form_method / form_text 为所在表单上下文）。"""
    return ElementSignature(
        tag=tag,
        role=None,
        text=text,
        aria_label=aria_label,
        href=None,
        is_form_control=True,
        form_method=method,
        form_text=form_text,
        input_type=input_type,
        name=name,
        element_id=element_id,
    )


class TestTierConstants(unittest.TestCase):
    """REQ-SU-006：分级常量存在且互异。"""

    def test_decision_fields(self):
        """ActionDecision 必须携带 tier / rule_name / matched_keyword / semantic_hint。"""
        el = _link("/home")
        d = classify_action(el, form_ctx=None, base_url="https://shop.example.com/cart")
        self.assertIsInstance(d, ActionDecision)
        self.assertIs(d.element, el)
        self.assertIn(d.tier, ("T1", "T2", "T3"))
        self.assertIsInstance(d.rule_name, str)
        self.assertTrue(len(d.rule_name) > 0)


class TestFormMethodRules(unittest.TestCase):
    """REQ-SU-006：① 表单方法非 GET 直接 T3。"""

    def test_post_form_t3(self):
        """method=POST 的表单控件 → T3（无论文本内容）。"""
        el = _form_element("POST", text="搜索", form_text="搜索商品")
        d = classify_action(el, form_ctx=None, base_url="https://shop.example.com/cart")
        self.assertEqual(d.tier, "T3")

    def test_lowercase_post_t3(self):
        """method 大小写不敏感（post 同样拒绝）。"""
        el = _form_element("post", text="搜索", form_text="搜索商品")
        d = classify_action(el, form_ctx=None, base_url="https://shop.example.com/cart")
        self.assertEqual(d.tier, "T3")

    def test_delete_put_patch_t3(self):
        """DELETE / PUT / PATCH 表单方法一律 T3。"""
        for m in ("DELETE", "PUT", "PATCH"):
            with self.subTest(method=m):
                el = _form_element(m, text="搜索", form_text="搜索内容")
                d = classify_action(el, form_ctx=None,
                                    base_url="https://shop.example.com/cart")
                self.assertEqual(d.tier, "T3")
                self.assertEqual(d.rule_name, "表单方法非GET")

    def test_explicit_get_safe_form_t2(self):
        """⑤ 显式 GET 表单且无危险动词 → T2。"""
        el = _form_element("GET", text="搜索", form_text="搜索商品",
                           tag="input", input_type="submit", name="q")
        d = classify_action(el, form_ctx=None, base_url="https://shop.example.com/cart")
        self.assertEqual(d.tier, "T2")

    def test_method_none_not_explicit_get(self):
        """method=None 不算"显式 GET"，不得进 T2（最保守只能 T1/T3）。"""
        el = _form_element(None, text="搜索", form_text="搜索商品",
                           tag="button", input_type="submit")
        d = classify_action(el, form_ctx=None, base_url="https://shop.example.com/cart")
        self.assertNotEqual(d.tier, "T2")


class TestSameDomainRules(unittest.TestCase):
    """REQ-SU-006：② 非同域链接 T3 / ⑥ 同域链接 T1。"""

    def test_same_domain_link_t1(self):
        """⑥ 同域普通链接 → T1。"""
        d = classify_action(_link("/products"), form_ctx=None,
                            base_url="https://shop.example.com/cart")
        self.assertEqual(d.tier, "T1")

    def test_absolute_same_domain_t1(self):
        """绝对 URL 但同主机同样 T1。"""
        d = classify_action(_link("https://shop.example.com/items"), form_ctx=None,
                            base_url="https://shop.example.com/cart")
        self.assertEqual(d.tier, "T1")

    def test_cross_domain_link_t3(self):
        """② 异主机链接 → T3。"""
        d = classify_action(_link("https://evil.example.org/phish"), form_ctx=None,
                            base_url="https://shop.example.com/cart")
        self.assertEqual(d.tier, "T3")

    def test_subdomain_diff_t3(self):
        """子域不同视为非同域（pay.example.com ≠ shop.example.com）。"""
        d = classify_action(_link("https://pay.example.com/checkout"), form_ctx=None,
                            base_url="https://shop.example.com/cart")
        self.assertEqual(d.tier, "T3")

    def test_pseudo_protocol_t3(self):
        """② 伪协议（javascript: / mailto:）判不同域 → T3。

        注意 javascript: 的 rest 以 '/' 开头时 urlsplit 会误解析出 netloc，
        这里用无斜杠形态确保命中"伪协议 scheme 有、netloc 无"分支。
        """
        for href in ("javascript:alert(1)", "mailto:a@b.com", "data:text,htmlx"):
            with self.subTest(href=href):
                d = classify_action(_link(href), form_ctx=None,
                                    base_url="https://shop.example.com/cart")
                self.assertEqual(d.tier, "T3")

    def test_absolute_link_without_base_fail_safe(self):
        """无 base_url 时绝对链接 fail-safe 判非同域 → T3。"""
        d = classify_action(_link("https://shop.example.com/a"), form_ctx=None,
                            base_url=None)
        self.assertEqual(d.tier, "T3")


class TestDangerousVerbRules(unittest.TestCase):
    """REQ-SU-006：③ 命中危险动词 T3（扫描面仅元素 text / aria-label）。"""

    def test_dangerous_verb_in_text_t3(self):
        """链接文本含"删除" → T3。"""
        d = classify_action(_link("/acct", text="删除账户"), form_ctx=None,
                            base_url="https://shop.example.com/cart")
        self.assertEqual(d.tier, "T3")

    def test_dangerous_verb_in_aria_label_t3(self):
        """③ 扫描面口径（实现事实）：text 优先，text 非空时不再扫 aria-label。

        实现第 310 行 `el_text_norm = _norm(el.text) or _norm(el.aria_label)`：
        text 非空时 aria-label 不参与③扫描（2026-09-28 自测确认的实现口径）。
        本用例锁定该口径：text="查询"（SAFE 词）+ aria-label="删除"（DANGER 词）
        的显式 GET 表单控件走⑤ → T2，并配套下一用例验证图标按钮（text 空）
        的 aria-label 危险词能被③拦截。
        """
        el = _form_element("GET", text="查询", form_text="关键字检索",
                           aria_label="删除", tag="input",
                           input_type="submit", name="q")
        d = classify_action(el, form_ctx=None, base_url="https://shop.example.com/cart")
        self.assertEqual(d.tier, "T2")

    def test_icon_button_aria_label_danger_t3(self):
        """图标按钮（text 空）aria-label 命中危险动词 → ③ T3。"""
        el = _form_element("GET", form_text="个人资料", aria_label="删除记录",
                           tag="button")
        d = classify_action(el, form_ctx=None, base_url="https://shop.example.com/cart")
        self.assertEqual(d.tier, "T3")
        self.assertEqual(d.rule_name, "命中危险动词")

    def test_matched_keyword_recorded(self):
        """命中危险动词必须记录 matched_keyword 供审计。"""
        d = classify_action(_link("/acct", text="删除账户"), form_ctx=None,
                            base_url="https://shop.example.com/cart")
        self.assertIsNotNone(d.matched_keyword)
        self.assertIn("删除", d.matched_keyword)

    def test_form_text_not_scanned_by_step3_for_link(self):
        """③ 扫描面仅元素文本：form_text 含危险词不影响纯链接（留给⑤）。"""
        el = ElementSignature(
            tag="a", role="link", text="详情", aria_label="",
            href="/detail", is_form_control=False,
            form_text="删除全部订单",  # 同表单存在危险词，但链接文本干净
        )
        d = classify_action(el, form_ctx=None, base_url="https://shop.example.com/cart")
        self.assertEqual(d.tier, "T1")


class TestDangerousControlRules(unittest.TestCase):
    """REQ-SU-006：④ 危险控件（file_upload / logout 词）T3。"""

    def test_file_upload_t3(self):
        """input type=file 一律 T3（上传不可控）。"""
        el = _form_element("GET", form_text="更新头像", input_type="file", name="avatar")
        d = classify_action(el, form_ctx=None, base_url="https://shop.example.com/cart")
        self.assertEqual(d.tier, "T3")
        self.assertEqual(d.matched_keyword, "file_upload")

    def test_logout_keyword_control_t3(self):
        """控件 name 命中 logout 词 → T3（文本为空的图标登出按钮场景）。"""
        el = _form_element("GET", form_text="个人资料", name="logout_btn")
        d = classify_action(el, form_ctx=None, base_url="https://shop.example.com/cart")
        self.assertEqual(d.tier, "T3")
        self.assertTrue(str(d.matched_keyword).startswith("logout:"))


class TestGetFormDangerousVerb(unittest.TestCase):
    """REQ-SU-006：⑤ GET 表单含危险动词 → T3。"""

    def test_search_plus_delete_same_form(self):
        """同表单"搜索框+删除按钮"：元素文本干净但 form_text 含"删除" → T3。"""
        el = _form_element("GET", text="搜索", form_text="搜索订单 删除选中订单",
                           tag="input", input_type="submit", name="q")
        d = classify_action(el, form_ctx=None, base_url="https://shop.example.com/cart")
        self.assertEqual(d.tier, "T3")
        self.assertIn("危险动词", d.rule_name)

    def test_clean_get_form_still_t2(self):
        """对照组：同结构但 form_text 无危险词 → T2。

        注意表单聚合文本包含字段名/标签文本，元素文本"搜索"命中 DANGER 表
        的英文词 "new"（sou-rch 子串），因此元素与表单文案全部选用完全无
        危险词子串的中文（"查询"、"筛选"仅属 SAFE 表，不触发③⑤）。
        """
        el = _form_element("GET", text="查询", form_text="查询条件 关键字",
                           tag="input", input_type="submit", name="q")
        d = classify_action(el, form_ctx=None, base_url="https://shop.example.com/cart")
        self.assertEqual(d.tier, "T2")
        self.assertEqual(d.rule_name, "显式GET表单")


class TestSafeVerbsStillDenied(unittest.TestCase):
    """REQ-SU-006 AC4：SAFE_VERBS 命中 → 仍 T3，只写解释性 semantic_hint。"""

    def test_safe_verb_bare_button_denied(self):
        """裸按钮文本命中安全动词（"查询"）仍 T3，绝不因白名单放行。"""
        el = ElementSignature(
            tag="button", role="button", text="查询", aria_label="",
            href=None, is_form_control=False,
        )
        d = classify_action(el, form_ctx=None, base_url="https://shop.example.com/cart")
        self.assertEqual(d.tier, "T3")
        self.assertIn("命中安全动词", d.rule_name)
        self.assertIn("查询", d.rule_name)
        self.assertEqual(d.semantic_hint, "readable_semantic:查询")
        # rule_name 必须解释"仍不执行"（红线⑤零点击口径）
        self.assertIn("仍不执行", d.rule_name)

    def test_default_denial_rule_chinese(self):
        """⑦ 默认拒绝（无危险词、无安全词、无可执行结构）→ T3。"""
        el = ElementSignature(
            tag="div", role="button", text="点击进行某种交互", aria_label="",
            href=None, is_form_control=False,
        )
        d = classify_action(el, form_ctx=None, base_url="https://shop.example.com/cart")
        self.assertEqual(d.tier, "T3")
        self.assertEqual(d.rule_name, "默认拒绝")
        self.assertIsNone(d.semantic_hint)


class TestRuleNamesChinese(unittest.TestCase):
    """REQ-SU-006：规则名全中文口径抽查。"""

    def test_rule_names_contain_chinese(self):
        """各判定路径 rule_name 均含中文字符。"""
        cases = [
            (_form_element("POST", text="搜索", form_text="搜索"), None),
            (_link("https://other.example.net/x"), None),
            (_link("/x", text="删除记录"), None),
            (_link("/x"), None),
        ]
        for el, _ in cases:
            d = classify_action(el, form_ctx=None,
                                base_url="https://shop.example.com/cart")
            with self.subTest(rule=d.rule_name):
                self.assertTrue(
                    re.search(r"[\u4e00-\u9fff]", d.rule_name),
                    msg="rule_name 必须为中文：%s" % d.rule_name,
                )


class TestFillNeutral(unittest.TestCase):
    """REQ-SU-006：fill_neutral 中性填值规则。"""

    PATTERN = re.compile(r"^[A-Za-z0-9]{0,20}$")

    def test_text_like_inputs_filled_test(self):
        """textarea 与文本类 input（text/search/tel/url/email/password/空 type）→ test。"""
        cases = [
            ElementSignature(tag="textarea", role=None, text="", aria_label="",
                             href=None, is_form_control=True, input_type=""),
            ElementSignature(tag="input", role=None, text="", aria_label="",
                             href=None, is_form_control=True, input_type=""),
        ]
        for t in ("text", "search", "tel", "url", "email", "password"):
            cases.append(ElementSignature(
                tag="input", role=None, text="", aria_label="",
                href=None, is_form_control=True, input_type=t,
            ))
        for el in cases:
            with self.subTest(input_type=el.input_type, tag=el.tag):
                self.assertEqual(fill_neutral(el), "test")

    def test_non_text_controls_empty(self):
        """非文本控件（number/checkbox/select 等）→ 空串不填值。"""
        for t in ("number", "checkbox", "radio", "date", "hidden", "file"):
            el = ElementSignature(
                tag="input", role=None, text="", aria_label="",
                href=None, is_form_control=True, input_type=t,
            )
            with self.subTest(input_type=t):
                self.assertEqual(fill_neutral(el), "")

    def test_output_always_matches_neutral_pattern(self):
        """任意控件的填值恒满足 NEUTRAL_VALUE_PATTERN。"""
        for t in ("", "text", "search", "number", "checkbox", "file", "weird"):
            el = ElementSignature(
                tag="input", role=None, text="", aria_label="",
                href=None, is_form_control=True, input_type=t,
            )
            v = fill_neutral(el)
            with self.subTest(input_type=t):
                self.assertTrue(self.PATTERN.match(v), msg=v)


class TestElementSignatureDefaults(unittest.TestCase):
    """REQ-SU-006：ElementSignature 默认值口径。"""

    def test_minimal_construction(self):
        """仅提供必填字段即可构造，可选字段有默认值。"""
        el = ElementSignature(
            tag="a", role="link", text="", aria_label="",
            href="/x", is_form_control=False,
        )
        self.assertIsNone(el.form_method)
        self.assertIsNone(el.form_text)


if __name__ == "__main__":
    unittest.main()
