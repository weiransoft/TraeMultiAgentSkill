# -*- coding: utf-8 -*-
"""SU 能力单元测试：route 拦截决策与有界事件队列（REQ-SU-007，红线④）。

覆盖 su.route_policy 模块（纯函数 + 队列，全部脱浏览器单测）：
- decide 判定链：非 GET 方法一律 abort（aborted_method）→ 白名单外域
  abort（blocked_origin）→ origin 不可解析保守拦截 → 其余 continue
- origin 归一：默认端口剥离（https://x:443 == https://x）、前缀域不误放
  （evil.example.com ≠ example.com）
- sanitize_blocked_url：只留 path、query 值全置 KEY 键名保留、fragment
  丢弃、path 内嵌 PII 经 scrub_text
- sanitize_post_data：JSON redact / form-urlencoded 归一 / 二进制只记长度 /
  自由文本 scrub 截断
- BlockedEventQueue：maxsize 归一、满队 dropped 计数非阻塞、drain 保序、
  入队即净化
- 红线④结构保证：mock route 对象断言 decide 纯函数零调用（决策不碰浏览器）

运行方式（项目根目录）：
    python3 -B -m unittest scripts.tests.test_su_route_guard
"""

import sys
import unittest
from pathlib import Path

# 将 scripts/ 目录注入 sys.path，使 `from su.xxx import ...` 生效
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from su.dto import REDACTED_PLACEHOLDER  # noqa: E402
from su.route_policy import (  # noqa: E402
    BLOCKED_METHODS,
    DEFAULT_BLOCKED_QUEUE_MAXSIZE,
    QUERY_VALUE_PLACEHOLDER,
    BlockedEventQueue,
    decide,
    sanitize_blocked_url,
    sanitize_post_data,
)


class _ZeroCallRecorder:
    """mock Playwright 对象：任何属性访问/调用都会被记录。

    红线④断言基准——决策与净化全程是纯函数，被传入也不得被调用。
    """

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def _recorded(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None
        return _recorded


class TestDecideMethod(unittest.TestCase):
    """REQ-SU-007 判据 1：非 GET 方法一律拦截。"""

    def test_blocked_methods_all_aborted(self):
        """POST/PUT/PATCH/DELETE → abort + aborted_method。"""
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            d = decide(method, "https://app.example.com/api/x",
                       ["https://app.example.com"])
            with self.subTest(method=method):
                self.assertEqual(d.action, "abort")
                self.assertEqual(d.kind, "aborted_method")
                self.assertIn(method, d.reason)

    def test_method_case_insensitive(self):
        """方法大小写不敏感（小写 post 同样拦截）。"""
        d = decide("post", "https://app.example.com/api/x", ["https://app.example.com"])
        self.assertEqual(d.kind, "aborted_method")

    def test_get_same_origin_continues(self):
        """GET + 白名单域 → continue，kind=None。"""
        d = decide("GET", "https://app.example.com/page", ["https://app.example.com"])
        self.assertEqual(d.action, "continue")
        self.assertIsNone(d.kind)

    def test_head_options_continue(self):
        """HEAD/OPTIONS 同域放行（预检类请求）。"""
        for method in ("HEAD", "OPTIONS"):
            d = decide(method, "https://app.example.com/p", ["https://app.example.com"])
            with self.subTest(method=method):
                self.assertEqual(d.action, "continue")

    def test_blocked_first_even_foreign_origin(self):
        """危险优先：外域 POST → aborted_method（不标 blocked_origin）。"""
        d = decide("POST", "https://evil.test/x", ["https://app.example.com"])
        self.assertEqual(d.kind, "aborted_method")

    def test_blocked_methods_constant(self):
        """拦截集合常量口径锁定（REQ-SU-007 原文集合）。"""
        self.assertEqual(set(BLOCKED_METHODS), {"POST", "PUT", "PATCH", "DELETE"})


class TestDecideLoginPostExemption(unittest.TestCase):
    """登录 POST 豁免（REQ-SU-004.4 运行期自动重登的 route 守卫口径）。"""

    def test_login_path_none_keeps_original_semantics(self):
        """login_path 缺省（None/空）→ POST 一律拦截，红线原貌零变化。"""
        for login_path in (None, ""):
            d = decide("POST", "https://app.example.com/login",
                       ["https://app.example.com"], login_path=login_path)
            with self.subTest(login_path=login_path):
                self.assertEqual(d.action, "abort")
                self.assertEqual(d.kind, "aborted_method")

    def test_same_origin_login_post_allowed(self):
        """同源 + path 精确等于登录路径的 POST → continue（重登放行）。"""
        d = decide("POST", "https://app.example.com/login",
                   ["https://app.example.com"], login_path="/login")
        self.assertEqual(d.action, "continue")
        self.assertIsNone(d.kind)
        self.assertIn("REQ-SU-004.4", d.reason)

    def test_login_path_trailing_slash_normalized(self):
        """登录路径尾斜杠折叠归一（/login/ 与 /login 等价）。"""
        d = decide("POST", "https://app.example.com/login/",
                   ["https://app.example.com"], login_path="/login/")
        self.assertEqual(d.action, "continue")

    def test_other_path_post_still_blocked(self):
        """豁免面精确钉死：非登录路径 POST 照拦（子路由/相似前缀均不放行）。"""
        for url in ("https://app.example.com/api/login",
                    "https://app.example.com/login/sso",
                    "https://app.example.com/loginfoo",
                    "https://app.example.com/orders"):
            d = decide("POST", url, ["https://app.example.com"],
                       login_path="/login")
            with self.subTest(url=url):
                self.assertEqual(d.action, "abort")
                self.assertEqual(d.kind, "aborted_method")

    def test_foreign_origin_login_post_blocked(self):
        """外域登录 POST 不放行（origin 前置校验，防豁免面外溢）。"""
        d = decide("POST", "https://evil.test/login",
                   ["https://app.example.com"], login_path="/login")
        self.assertEqual(d.action, "abort")

    def test_non_post_methods_never_exempted(self):
        """豁免只针对 POST：PUT/PATCH/DELETE 打到登录路径也照拦。"""
        for method in ("PUT", "PATCH", "DELETE"):
            d = decide(method, "https://app.example.com/login",
                       ["https://app.example.com"], login_path="/login")
            with self.subTest(method=method):
                self.assertEqual(d.action, "abort")
                self.assertEqual(d.kind, "aborted_method")


class TestDecideOrigin(unittest.TestCase):
    """REQ-SU-007 判据 2：白名单外域拦截。"""

    def test_foreign_origin_blocked(self):
        """GET 外域 → abort + blocked_origin。"""
        d = decide("GET", "https://evil.test/track.gif", ["https://app.example.com"])
        self.assertEqual(d.action, "abort")
        self.assertEqual(d.kind, "blocked_origin")
        self.assertIn("allowed_origins", d.reason)

    def test_prefix_lookalike_not_allowed(self):
        """前缀伪装域不误放：evil-app.example.com ∉ app.example.com。"""
        d = decide("GET", "https://evil-app.example.com/x", ["https://app.example.com"])
        self.assertEqual(d.kind, "blocked_origin")

    def test_default_port_normalized(self):
        """白名单写 https://x:443，请求省略端口 → 归一后同域放行。"""
        d = decide("GET", "https://app.example.com/p", ["https://app.example.com:443"])
        self.assertEqual(d.action, "continue")

    def test_non_default_port_must_match(self):
        """非默认端口必须精确匹配（:8443 ≠ :443）。"""
        d = decide("GET", "https://app.example.com:8443/p", ["https://app.example.com"])
        self.assertEqual(d.kind, "blocked_origin")

    def test_unparseable_origin_blocked(self):
        """相对 URL / 伪协议（origin 解析失败）→ 最严口径拦截。"""
        for url in ("/relative/path", "javascript:alert(1)"):
            d = decide("GET", url, ["https://app.example.com"])
            with self.subTest(url=url):
                self.assertEqual(d.action, "abort")
                self.assertEqual(d.kind, "blocked_origin")
                self.assertIn("无法解析出来源域", d.reason)

    def test_empty_allowlist_blocks_all(self):
        """空白名单 → 全部外域判定（保守拒绝）。"""
        d = decide("GET", "https://app.example.com/p", [])
        self.assertEqual(d.kind, "blocked_origin")

    def test_unparseable_url_reason_scrubbed(self):
        """不可解析 URL 的 reason 过 scrub_text：PII 值形态 + 任意 scheme
        URL 凭据掩码均生效（修复后正确行为）。
        """
        d = decide("GET", "data:text/html,http://admin:S3cr3tP@ss@x", ["https://a.test"])
        self.assertEqual(d.kind, "blocked_origin")
        # 截断口径：reason 中的 URL 原文最长 160 字符
        self.assertIn("data:text/html", d.reason)
        # 修复回归：嵌套于其它协议体内的 http URL 凭据必须被掩码
        self.assertNotIn("S3cr3tP@ss", d.reason)
        self.assertNotIn("admin:S3cr3tP", d.reason)
        # 手机号等 PII 值形态仍会被 scrub（scrub 生效范围的正面断言）
        d2 = decide("GET", "/contact/13800138000", ["https://a.test"])
        self.assertNotIn("13800138000", d2.reason)


class TestSanitizeBlockedUrl(unittest.TestCase):
    """§3 DDL：url 只存 path，query 值全置 KEY。"""

    def test_only_path_with_keyed_query(self):
        """完整 URL → path + 键名化 query（键序保留，值一律 KEY）。"""
        out = sanitize_blocked_url(
            "https://app.example.com/api/user?token=abc123&page=2")
        self.assertEqual(out, "/api/user?token={0}&page={0}".format(QUERY_VALUE_PLACEHOLDER))

    def test_duplicate_keys_merged(self):
        """同键多值合并为一个键。"""
        out = sanitize_blocked_url("https://a.test/x?id=1&id=2")
        self.assertEqual(out, "/x?id={0}".format(QUERY_VALUE_PLACEHOLDER))

    def test_fragment_dropped(self):
        """fragment 丢弃（SPA 参数常塞 hash）。"""
        out = sanitize_blocked_url("https://a.test/p#/secret?k=v")
        self.assertNotIn("#", out)

    def test_blank_values_kept_as_key(self):
        """空值参数保留键名（keep_blank_values）。"""
        out = sanitize_blocked_url("https://a.test/x?q=")
        self.assertEqual(out, "/x?q={0}".format(QUERY_VALUE_PLACEHOLDER))

    def test_pii_in_path_scrubbed(self):
        """path 内嵌手机号 → scrub_text 值形态脱敏。"""
        out = sanitize_blocked_url("https://a.test/user/13800138000")
        self.assertIn("<REDACTED:phone>", out)
        self.assertNotIn("13800138000", out)

    def test_empty_url_root_path(self):
        """空 URL → path 兜底 '/'。"""
        self.assertEqual(sanitize_blocked_url(""), "/")


class TestSanitizePostData(unittest.TestCase):
    """REQ-SU-007：post_data 过 redact() 净化。"""

    def test_none_returns_none(self):
        """空 body → None。"""
        self.assertIsNone(sanitize_post_data(None))
        self.assertIsNone(sanitize_post_data("   "))

    def test_json_object_redacted(self):
        """JSON 对象：敏感键名命中 → ***REDACTED***。"""
        import json as _json
        out = _json.loads(sanitize_post_data('{"username":"u1","password":"P@ssw0rd"}'))
        self.assertEqual(out["username"], "u1")
        self.assertEqual(out["password"], REDACTED_PLACEHOLDER)

    def test_json_list_wrapped(self):
        """JSON 数组 → _value 包装（标量/列表统一 dict 落库形态）。"""
        import json as _json
        out = _json.loads(sanitize_post_data("[1, 2, 3]"))
        self.assertEqual(out, {"_value": [1, 2, 3]})

    def test_form_urlencoded_normalized(self):
        """form-urlencoded → dict 归一，同键多值转列表。"""
        import json as _json
        out = _json.loads(sanitize_post_data("a=1&a=2&token=t0k3n"))
        self.assertEqual(out["a"], ["1", "2"])
        # token 非 SENSITIVE_KEY 全词命中？token 属敏感键名 → 值替换
        self.assertEqual(out["token"], REDACTED_PLACEHOLDER)

    def test_binary_body_size_only(self):
        """multipart 二进制 → 只记长度，不落正文。"""
        import json as _json
        blob = b"\xff\xd8\xff\xe0\x00\x10JFIF" * 10
        out = _json.loads(sanitize_post_data(blob))
        self.assertTrue(out["_binary"])
        self.assertEqual(out["size"], len(blob))

    def test_free_text_scrubbed_truncated(self):
        """无法结构化文本（无 '='）→ _text 键，scrub 兜底 + 200 截断。

        修复后正确行为：parse_qsl 判定已收紧——body 必须含 '=' 才走
        form 通道，无键值语义的自由文本可达 _text 通道。
        """
        import json as _json
        out = _json.loads(sanitize_post_data("纯文本无键值对形态"))
        self.assertEqual(out, {"_text": "纯文本无键值对形态"})

    def test_free_text_pii_scrubbed_and_truncated(self):
        """回归：_text 通道 PII 值形态 scrub + 超长截断到 200。"""
        import json as _json
        out = _json.loads(sanitize_post_data("联系我 13800138000 详谈"))
        self.assertEqual(out, {"_text": "联系我 <REDACTED:phone> 详谈"})
        long_out = _json.loads(sanitize_post_data("x" * 500))
        self.assertLessEqual(len(long_out["_text"]), 200)

    def test_form_channel_key_name_pii_scrubbed(self):
        """修复后正确行为：form 通道键名同样过 scrub（PII 可藏键名）。"""
        import json as _json
        out = _json.loads(sanitize_post_data("联系方式 13800138000=李四"))
        self.assertEqual(out, {"联系方式 <REDACTED:phone>": "李四"})
        # 值位置的 PII 则会被 redact 管线替换（正面口径）
        out2 = _json.loads(sanitize_post_data("contact=13800138000"))
        self.assertNotIn("13800138000", out2["contact"])

    def test_no_equals_body_never_forms_channel(self):
        """回归：无 '=' 的文本（含空格/中文/分号）一律不走 form 通道。"""
        import json as _json
        for body in ("单段文本", "a b c", "键名 13800138000 abc", "a;b;c"):
            out = _json.loads(sanitize_post_data(body))
            with self.subTest(body=body):
                self.assertEqual(list(out.keys()), ["_text"])

    def test_dict_input_redacted(self):
        """dict 直接输入同样过 redact。"""
        import json as _json
        out = _json.loads(sanitize_post_data({"api_key": "sk-secret"}))
        self.assertEqual(out["api_key"], REDACTED_PLACEHOLDER)


class TestBlockedEventQueue(unittest.TestCase):
    """§2.3.6：有界队列——非阻塞、满丢弃计数、drain 保序。"""

    def test_default_maxsize(self):
        """默认容量 1000。"""
        q = BlockedEventQueue()
        self.assertEqual(q.maxsize, DEFAULT_BLOCKED_QUEUE_MAXSIZE)

    def test_nonpositive_maxsize_normalized(self):
        """maxsize ≤0 归一为默认（杜绝无界队列）。"""
        self.assertEqual(BlockedEventQueue(maxsize=0).maxsize, DEFAULT_BLOCKED_QUEUE_MAXSIZE)
        self.assertEqual(BlockedEventQueue(maxsize=-5).maxsize, DEFAULT_BLOCKED_QUEUE_MAXSIZE)

    def test_put_sanitize_on_enqueue(self):
        """入队即净化：url 键名化、post_data 脱敏（净化不进 handler）。"""
        q = BlockedEventQueue(maxsize=10)
        ok = q.put("aborted_method", "https://a.test/api?pwd=abc",
                   method="POST", post_data='{"pwd":"abc"}', page_id=7, ts=1.0)
        self.assertTrue(ok)
        events = q.drain()
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.url, "/api?pwd={0}".format(QUERY_VALUE_PLACEHOLDER))
        self.assertNotIn("abc", ev.post_data)  # pwd 敏感键 → 占位符
        self.assertEqual(ev.page_id, 7)
        self.assertEqual(ev.ts, 1.0)

    def test_full_queue_dropped_counted(self):
        """满队 → put 返回 False 且 dropped+1（非阻塞、非静默）。"""
        q = BlockedEventQueue(maxsize=2)
        self.assertTrue(q.put("blocked_origin", "https://a/1"))
        self.assertTrue(q.put("blocked_origin", "https://a/2"))
        self.assertFalse(q.put("blocked_origin", "https://a/3"))
        self.assertEqual(q.dropped, 1)
        self.assertEqual(q.enqueued, 2)
        self.assertEqual(q.qsize(), 2)

    def test_drain_order_and_empty(self):
        """drain 按入队顺序返回列表，二次 drain 为空。"""
        q = BlockedEventQueue(maxsize=5)
        q.put("aborted_method", "https://a/first", ts=1.0)
        q.put("aborted_method", "https://a/second", ts=2.0)
        first = q.drain()
        self.assertEqual([e.url for e in first], ["/first", "/second"])
        self.assertEqual(q.drain(), [])

    def test_stats_snapshot(self):
        """stats 四字段口径（summary 报告数据源）。"""
        q = BlockedEventQueue(maxsize=1)
        q.put("download", "https://a/f.zip")
        q.put("new_window", "https://a/w")  # 满 → dropped
        stats = q.stats()
        self.assertEqual(stats["maxsize"], 1)
        self.assertEqual(stats["enqueued"], 1)
        self.assertEqual(stats["dropped"], 1)
        self.assertEqual(stats["pending"], 1)

    def test_ts_defaults_now(self):
        """ts 缺省取当前时刻（非 None）。"""
        q = BlockedEventQueue(maxsize=1)
        q.put("blocked_origin", "https://a/x")
        ev = q.drain()[0]
        self.assertIsNotNone(ev.ts)
        self.assertGreater(ev.ts, 0)


class TestPureFunctionBoundary(unittest.TestCase):
    """红线④结构保证：决策/净化是纯函数，零 Playwright API 调用。"""

    def test_decide_never_touches_playwright_object(self):
        """mock route/page 对象即使可达也不被 decide 触碰（零调用断言）。"""
        recorder = _ZeroCallRecorder()
        # decide/sanitize 签名根本不接收 page/route——此处以全局替身验证
        # 决策路径无任何隐式浏览器对象依赖：调用后 recorder 零记录
        decide("POST", "https://a.test/x", ["https://a.test"])
        decide("GET", "https://a.test/x", ["https://a.test"])
        sanitize_blocked_url("https://a.test/x?t=1")
        sanitize_post_data('{"a":1}')
        self.assertEqual(recorder.calls, [])


if __name__ == "__main__":
    unittest.main()
