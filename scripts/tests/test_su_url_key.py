# -*- coding: utf-8 -*-
"""SU 能力单元测试：URL 归一化与去重键（REQ-SU-005）。

覆盖 su.url_key 模块：
- normalize_url：协议归一 https、默认端口剥离、userinfo 丢弃、主机小写
- 追踪参数剥离：utm_ 前缀、timestamp/_t/spm/scm/trace_ 等全键匹配（大小写不敏感）
- 路径段 {id} 归一：纯数字 / UUID / >=16 位长 hex；混合段不归一
- query 键排序 (k.lower(), k)，同键多值保序
- hash 路由保留并内部归一；相对 URL 形态；空输入返回空串
- url_key：去重键与 normalize_url 同口径，语义等价 URL 键相同

运行方式（项目根目录）：
    python3 -B -m unittest scripts.tests.test_su_url_key
"""

import sys
import unittest
from pathlib import Path

# 将 scripts/ 目录注入 sys.path，使 `from su.xxx import ...` 生效
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from su.url_key import (  # noqa: E402
    ID_PLACEHOLDER,
    TRACKING_PARAMS,
    normalize_url,
    url_key,
)


class TestNormalizeBasics(unittest.TestCase):
    """REQ-SU-005：基础归一——协议、端口、userinfo、主机大小写。"""

    def test_empty_input_returns_empty_string(self):
        """空串 / None 输入必须返回空串，绝不抛异常。"""
        self.assertEqual(normalize_url(""), "")
        self.assertEqual(normalize_url(None), "")

    def test_https_scheme_preserved(self):
        """https 协议原样保留。"""
        self.assertEqual(
            normalize_url("https://example.com/path"),
            "https://example.com/path",
        )

    def test_http_upgraded_to_https(self):
        """所有协议一律归一为 https。"""
        self.assertEqual(
            normalize_url("http://example.com/path"),
            "https://example.com/path",
        )

    def test_default_ports_stripped(self):
        """默认端口（http:80 / https:443）剥离，非默认端口保留。"""
        self.assertEqual(
            normalize_url("https://example.com:443/a"),
            "https://example.com/a",
        )
        self.assertEqual(
            normalize_url("http://example.com:80/a"),
            "https://example.com/a",
        )
        # 非默认端口必须保留
        self.assertEqual(
            normalize_url("https://example.com:8443/a"),
            "https://example.com:8443/a",
        )

    def test_userinfo_dropped(self):
        """URL 中的用户名口令（userinfo）必须整体丢弃（红线①）。"""
        self.assertEqual(
            normalize_url("https://admin:secret@example.com/a"),
            "https://example.com/a",
        )

    def test_host_lowercased(self):
        """主机名统一小写。"""
        self.assertEqual(
            normalize_url("https://EXAMPLE.COM/Path"),
            "https://example.com/Path",
        )

    def test_empty_path_becomes_root(self):
        """根路径与空路径归一一致（都落到 /）。"""
        self.assertEqual(
            normalize_url("https://example.com"),
            normalize_url("https://example.com/"),
        )
        self.assertEqual(normalize_url("https://example.com"), "https://example.com/")


class TestTrackingParamsStripped(unittest.TestCase):
    """REQ-SU-005：追踪参数剥离（utm_ 前缀 + 全键匹配，大小写不敏感）。"""

    def test_utm_prefix_matching(self):
        """utm_ 前缀族参数全部剥离（utm_source / utm_medium / utm_campaign...）。"""
        self.assertEqual(
            normalize_url("https://example.com/a?utm_source=ad&utm_medium=cpc"),
            "https://example.com/a",
        )
        # 大写键同样剥离
        self.assertEqual(
            normalize_url("https://example.com/a?UTM_SOURCE=ad"),
            "https://example.com/a",
        )

    def test_prefix_family_from_and_trace(self):
        """from_ / trace_ 前缀族同样按前缀剥离。"""
        self.assertEqual(
            normalize_url("https://example.com/a?from_page=home"),
            "https://example.com/a",
        )
        self.assertEqual(
            normalize_url("https://example.com/a?trace_id=abc"),
            "https://example.com/a",
        )

    def test_full_key_tracking_params_case_insensitive(self):
        """timestamp / _t / t / spm / scm / request_id / reqid / gclid / fbclid /
        ref / ref_src 全键匹配剥离（大小写不敏感）。"""
        cases = [
            "https://example.com/a?timestamp=123",
            "https://example.com/a?_t=123",
            "https://example.com/a?t=123",
            "https://example.com/a?_from=search",
            "https://example.com/a?spm=a2cg",
            "https://example.com/a?scm=1007",
            "https://example.com/a?request_id=r1",
            "https://example.com/a?reqid=r1",
            "https://example.com/a?gclid=g1",
            "https://example.com/a?fbclid=f1",
            "https://example.com/a?ref=home",
            "https://example.com/a?ref_src=google",
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_url(raw), "https://example.com/a")
        # 大小写混合键同样剥离
        self.assertEqual(
            normalize_url("https://example.com/a?TimeStamp=1&SPM=x"),
            "https://example.com/a",
        )

    def test_business_params_kept(self):
        """非追踪业务参数必须保留（ref 的近亲 refer/referer 不在名单）。"""
        self.assertEqual(
            normalize_url("https://example.com/a?page=2"),
            "https://example.com/a?page=2",
        )
        self.assertEqual(
            normalize_url("https://example.com/a?referer=x"),
            "https://example.com/a?referer=x",
        )

    def test_mixed_tracking_and_business(self):
        """追踪参数剥离、业务参数保留同现场景。"""
        self.assertEqual(
            normalize_url("https://example.com/a?utm_medium=x&page=2&id=5"),
            "https://example.com/a?id=5&page=2",
        )

    def test_all_tracking_params_output_no_question_mark(self):
        """全部参数被剥离后不残留 '?'。"""
        out = normalize_url("https://example.com/a?utm_source=x&t=1")
        self.assertNotIn("?", out)

    def test_tracking_params_constant_shape(self):
        """TRACKING_PARAMS 常量非空且为字符串元组（防止误删配置）。"""
        self.assertTrue(len(TRACKING_PARAMS) > 0)
        for item in TRACKING_PARAMS:
            self.assertIsInstance(item, str)


class TestQuerySorting(unittest.TestCase):
    """REQ-SU-005：query 键排序 (k.lower(), k)，同键多值保序。"""

    def test_query_keys_sorted_case_insensitive(self):
        """多个业务键按 (k.lower(), k) 排序输出。"""
        out = normalize_url("https://example.com/a?Zebra=1&alpha=2&Mike=3")
        # lower 序：alpha < mike < zebra
        self.assertEqual(out, "https://example.com/a?alpha=2&Mike=3&Zebra=1")

    def test_same_key_multi_values_order_preserved(self):
        """同键多值保持原始顺序，不排序值。"""
        out = normalize_url("https://example.com/a?tag=c&tag=a&tag=b")
        self.assertEqual(out, "https://example.com/a?tag=c&tag=a&tag=b")

    def test_blank_values_kept(self):
        """空值参数保留（keep_blank_values=True 语义，urlencode 编码为 q=）。"""
        out = normalize_url("https://example.com/a?q=")
        self.assertEqual(out, "https://example.com/a?q=")


class TestIdSegmentNormalization(unittest.TestCase):
    """REQ-SU-005：路径段 {id} 归一（数字 / UUID / 长 hex）。"""

    def test_placeholder_constant(self):
        """占位符常量必须是 {id}。"""
        self.assertEqual(ID_PLACEHOLDER, "{id}")

    def test_numeric_segment(self):
        """纯数字段替换为 {id}。"""
        self.assertEqual(
            normalize_url("https://example.com/order/12345"),
            "https://example.com/order/{id}",
        )

    def test_uuid_segment(self):
        """标准 8-4-4-4-12 UUID 段替换为 {id}（大小写均可）。"""
        self.assertEqual(
            normalize_url(
                "https://example.com/u/550e8400-e29b-41d4-a716-446655440000"
            ),
            "https://example.com/u/{id}",
        )

    def test_long_hex_segment(self):
        """>=16 位长 hex 段替换为 {id}。"""
        self.assertEqual(
            normalize_url("https://example.com/t/deadbeefcafebabe1234"),
            "https://example.com/t/{id}",
        )

    def test_mixed_segment_not_normalized(self):
        """混合段（如 order-123）不归一，保留原样。"""
        self.assertEqual(
            normalize_url("https://example.com/order-123"),
            "https://example.com/order-123",
        )

    def test_short_hex_not_normalized(self):
        """不足 16 位的 hex 段不归一。"""
        self.assertEqual(
            normalize_url("https://example.com/t/abc123"),
            "https://example.com/t/abc123",
        )

    def test_percent_encoded_segment_normalized_after_unquote(self):
        """路径段先 unquote 再判定（编码数字 %31%32%33 → 123 → {id}）。"""
        self.assertEqual(
            normalize_url("https://example.com/order/%31%32%33"),
            "https://example.com/order/{id}",
        )


class TestHashAndRelative(unittest.TestCase):
    """REQ-SU-005：hash 路由保留并内部归一；相对 URL 形态。"""

    def test_hash_route_preserved_and_normalized(self):
        """hash 片段保留，且片段内路径段同样执行 {id} 归一。"""
        out = normalize_url("https://example.com/#/user/42")
        self.assertEqual(out, "https://example.com/#/user/{id}")

    def test_hash_internal_query_normalized(self):
        """hash 内部自带 query：追踪剥离 + 键排序同样生效。"""
        out = normalize_url("https://example.com/#/list?page=2&utm_source=x")
        self.assertEqual(out, "https://example.com/#/list?page=2")

    def test_plain_anchor_untouched(self):
        """纯语义锚点（不含 / 与 ?）原样保留。"""
        out = normalize_url("https://example.com/doc#section1")
        self.assertEqual(out, "https://example.com/doc#section1")

    def test_relative_url_form(self):
        """相对 URL 输出 path[?query][#fragment] 形态（无协议主机）。"""
        out = normalize_url("/api/v1/items/7?page=2")
        self.assertEqual(out, "/api/v1/items/{id}?page=2")

    def test_relative_fragment_only(self):
        """纯 hash 相对片段不抛异常；path 为空时归一为 '/' 再拼 fragment。"""
        out = normalize_url("#/home")
        self.assertEqual(out, "/#/home")


class TestUrlKey(unittest.TestCase):
    """REQ-SU-005：url_key 去重键——与 normalize_url 同口径，语义等价键相同。"""

    def test_equivalent_urls_same_key(self):
        """协议/默认端口/追踪参数/键序不同但语义等价的 URL，键必须相同。"""
        a = url_key("http://EXAMPLE.com:80/a?utm_source=x&page=2&sort=asc")
        b = url_key("https://example.com/a?sort=asc&page=2")
        self.assertEqual(a, b)

    def test_different_urls_different_key(self):
        """语义不同的 URL 键必须不同。"""
        a = url_key("https://example.com/a?page=1")
        b = url_key("https://example.com/a?page=2")
        self.assertNotEqual(a, b)

    def test_key_equals_normalize_url(self):
        """当前实现口径：url_key 产物 == normalize_url 产物。"""
        raw = "http://Shop.Example.com:80/product/99?utm_campaign=x#tab"
        self.assertEqual(url_key(raw), normalize_url(raw))

    def test_key_is_stable_string(self):
        """url_key 输出稳定非空字符串；同输入重复调用键一致。"""
        k1 = url_key("https://example.com/path")
        k2 = url_key("https://example.com/path")
        self.assertIsInstance(k1, str)
        self.assertTrue(len(k1) > 0)
        self.assertEqual(k1, k2)

    def test_empty_url_key(self):
        """空 URL 键为空串（归一口径一致，不抛异常）。"""
        self.assertEqual(url_key(""), "")


if __name__ == "__main__":
    unittest.main()
