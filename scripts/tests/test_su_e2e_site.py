"""SU fixture 测试站自测（无 playwright 依赖，防 fixture 腐烂）。

验证对象是 fixture 自身（tests/fixtures/su_site/server.py）与状态库种子
builder（tests/fixtures/su_state_builder.py）——e2e 场景 [1]~[6] 全部构建在
这两者之上，任何能力退化（登录语义变化、写计数失效、陷阱页丢失）都会在
纯单测阶段被本文件捕获，而不是等到 e2e 阶段才暴露为疑似 SU 缺陷。

运行方式（零第三方依赖，纯标准库 unittest + urllib）：
    python3 -B scripts/tests/test_su_e2e_site.py
"""

import json
import socket
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

# 路径装配：scripts/（su 生产包）与 tests/fixtures（builder）、su_site（server）
_TESTS_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _TESTS_DIR.parent
for _p in (str(_SCRIPTS_DIR), str(_TESTS_DIR / "fixtures"),
           str(_TESTS_DIR / "fixtures" / "su_site")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# unittest 模块名兼容：以 tests.test_su_e2e_site 点号路径被
# run_system_understanding.sh 调度时（tests/ 无 __init__.py = 命名空间
# 包），解释器会把绝对导入"server"错误解析为 tests.server（tests/ 目录
# 被当成包前缀）——须显式把 su_site/、fixtures/ 目录插到 sys.path 最前，
# 让顶层名 server / su_state_builder 在命名空间包遮蔽下仍直接命中文件；
# 直接 python3 运行时本段等价 no-op。两种调度形态统一可发现。
for _p in (str(_TESTS_DIR / "fixtures" / "su_site"), str(_TESTS_DIR / "fixtures")):
    if _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

from server import (  # noqa: E402 - sys.path 装配后固定导入
    ORDERS_API_PAYLOAD,
    TEST_PASSWORD,
    TEST_USERNAME,
    start_su_site,
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁自动重定向的 handler（302 语义断言必须看到原始响应）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """覆写：永不跟随重定向，让 HTTPError(302) 原样抛给调用方。"""
        return None


class SuSiteBehaviorTest(unittest.TestCase):
    """fixture 站点行为契约（每测试类共享一次 server 实例，类级起停）。"""

    @classmethod
    def setUpClass(cls):
        """类级启动测试站（port=0 空闲端口，杜绝并行端口冲突）。"""
        cls.server, cls.thread, cls.state = start_su_site(0)
        cls.base = "http://127.0.0.1:{0}".format(cls.server.server_address[1])
        cls.opener = urllib.request.build_opener(_NoRedirect)

    @classmethod
    def tearDownClass(cls):
        """类级关停测试站（shutdown 等待在途请求后关闭监听套接字）。"""
        cls.server.shutdown()
        cls.server.server_close()

    # -- 请求工具 -----------------------------------------------------------

    def _req(self, path, method="GET", data=None, headers=None):
        """发请求并返回 (status, headers_dict, body_bytes)（302/401 不抛错）。"""
        request = urllib.request.Request(
            self.base + path, data=data, method=method, headers=headers or {})
        try:
            resp = self.opener.open(request, timeout=5)
            return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def _login(self) -> str:
        """执行真实登录流程并返回会话 Cookie 头值（供受保护页断言复用）。

        表单值走 urlencode：密码含 `#`，不编码会被 parse_qs 之前的
        fragment/注释语义吞掉，编码链路与 SU 真实登录（浏览器表单提交）一致。
        """
        from urllib.parse import urlencode
        body = urlencode({"username": TEST_USERNAME,
                          "password": TEST_PASSWORD}).encode("utf-8")
        status, headers, _ = self._req(
            "/login", "POST", body,
            {"Content-Type": "application/x-www-form-urlencoded"})
        assert status == 302, "登录 POST 应 302，实际 {0}".format(status)
        return headers["Set-Cookie"].split(";")[0]

    # -- 登录流程契约 --------------------------------------------------------

    def test_login_form_served(self):
        """/login GET 返回 200 且含登录表单（username/password 控件齐全）。"""
        status, _, body = self._req("/login")
        self.assertEqual(status, 200)
        self.assertIn(b'name="username"', body)
        self.assertIn(b'name="password"', body)
        self.assertIn(b'method="POST"', body)

    def test_login_password_contains_hash_char(self):
        """凭据契约（e2e 场景[3]/红线扫描依赖）：su_test / SuTest#2026。

        密码含 `#` 字符——该字符经 config JSON、shell 变量、curl 表单编码
        全链路传递时极易被当注释/fragment 截断，fixture 必须原样比较。
        """
        self.assertEqual(TEST_USERNAME, "su_test")
        self.assertEqual(TEST_PASSWORD, "SuTest#2026")

    def test_login_wrong_password_401(self):
        """错误密码 → 401（SU 凭据错误场景 exit 4 的站点侧判据）。"""
        body = "username={0}&password=definitely-wrong".format(
            TEST_USERNAME).encode("utf-8")
        status, _, html = self._req(
            "/login", "POST", body,
            {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 401)
        self.assertIn("登录失败".encode("utf-8"), html)

    def test_login_success_sets_session_cookie(self):
        """正确账密 → 302 /dashboard + Set-Cookie su_session（会话判据）。

        表单值走 urlencode 编码：密码含 `#`（fragment 截断风险），必须
        percent-encode 后传输并验证服务端原样命中——与 SU 真实登录链路一致。
        """
        from urllib.parse import urlencode
        body = urlencode({"username": TEST_USERNAME,
                          "password": TEST_PASSWORD}).encode("utf-8")
        status, headers, _ = self._req(
            "/login", "POST", body,
            {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 302)
        self.assertEqual(headers.get("Location"), "/dashboard")
        self.assertIn("su_session=", headers.get("Set-Cookie", ""))

    def test_unauthenticated_redirected_to_login(self):
        """未登录访问受保护页 → 302 /login（会话鉴权红线）。"""
        for path in ("/dashboard", "/orders", "/admin", "/external"):
            status, headers, _ = self._req(path)
            self.assertEqual(status, 302, path)
            self.assertEqual(headers.get("Location"), "/login", path)

    # -- 页面内容契约（e2e 断言素材防腐烂） ----------------------------------

    def test_orders_page_traps(self):
        """订单页：删除按钮 + fetch /api/orders + hash 路由链接三素材齐全。"""
        cookie = self._login()
        status, _, body = self._req("/orders", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertIn(b'delete-selected', body)      # 删除按钮（POST 陷阱）
        self.assertIn(b"fetch('/api/orders'", body)  # JSON 端点观测素材
        self.assertIn(b"/orders#/orders/1", body)    # hash 路由素材
        # 重复链接（url_key 去重验证素材）出现 ≥2 次
        self.assertGreaterEqual(body.count(b'href="/products"'), 2)

    def test_get_search_form_and_dangerous_get_form(self):
        """T2 搜索表单 + 含"删除"文本 GET 表单（T3 判定素材）双页在位。"""
        cookie = self._login()
        status, _, reports = self._req("/reports", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        # method="GET" 的搜索表单（T2：crawler 应 fill+submit）
        self.assertIn('method="GET" action="/search"'.encode("utf-8"), reports)
        status, _, admin = self._req("/admin", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        # GET 表单但按钮文本含"删除"（分词器必须判 T3 绝不提交）
        self.assertIn("删除全部审计日志".encode("utf-8"), admin)
        self.assertIn('method="GET"'.encode("utf-8"), admin)

    def test_external_links_on_https_example_invalid(self):
        """外链页含 https://example.invalid/（route guard blocked_origin 断言素材）。"""
        cookie = self._login()
        status, _, body = self._req("/external", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertIn(b"https://example.invalid/", body)

    def test_users_page_phone_samples(self):
        """用户管理页含手机号明文样本（PII 脱敏红线素材）与近邻负样本。"""
        cookie = self._login()
        status, _, body = self._req("/users", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertIn(b"13812345678", body)   # 应被脱敏的正样本
        self.assertIn(b"12345678901", body)   # 不应被脱敏的负样本（首位非3-9）

    def test_root_page_serves_dashboard_when_logged_in(self):
        """站点根 /：未登录 302 /login；登录后 200 渲染首页（BFS 入口契约）。"""
        status, headers, _ = self._req("/")
        self.assertEqual(status, 302)
        self.assertEqual(headers.get("Location"), "/login")
        cookie = self._login()
        status, _, body = self._req("/", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertIn("订单".encode("utf-8"), body)

    def test_invalidate_session_endpoint(self):
        """会话失效控制端点：置位后下一个鉴权请求 302 回 /login（失效一次）。

        契约：POST /api/test/invalidate-session 是写请求（write-counter +1，
        证明"route guard 失效则计数可观测"的真实语义）；置位后旧 cookie 的
        首个鉴权请求被拒（302 /login = SU 自动重登触发素材）；重登新会话
        恢复正常（开关只消费一次）。
        """
        cookie = self._login()
        # 置位前：受保护页正常 200
        status, _, _ = self._req("/orders", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        # 置位失效开关（写请求 → 计数 +1）
        before = self.state.get_write_count()
        status, _, body = self._req("/api/test/invalidate-session", "POST", b"", {})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body.decode("utf-8"))["invalidate_pending"])
        self.assertEqual(self.state.get_write_count(), before + 1)
        # /search 不消费失效开关（2026-09-28 e2e 场景[2]根因回归锁）：
        # 开关置位期间 SU 全链路 T2 素材导航 /search 必须照常 200 回显，
        # 且开关保持置位（下一个普通鉴权页才消费——SU 浏览器导航不得被
        # 残留开关随机 302 导致 settled 永不落定）
        status, _, body = self._req("/search?q=keep", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertIn("keep".encode("utf-8"), body)
        self.assertTrue(self.state.invalidate_pending)
        # 旧会话首个**普通**鉴权请求被拒（模拟服务端会话过期）
        status, headers, _ = self._req("/orders", headers={"Cookie": cookie})
        self.assertEqual(status, 302)
        self.assertEqual(headers.get("Location"), "/login")
        # 重登 → 新会话恢复正常（开关已消费）
        new_cookie = self._login()
        status, _, _ = self._req("/orders", headers={"Cookie": new_cookie})
        self.assertEqual(status, 200)

    def test_download_links_and_attachment_header(self):
        """下载页 .zip/.csv 后缀链接 + /attachment 的 Content-Disposition 头。"""
        cookie = self._login()
        status, _, body = self._req("/downloads", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertIn(b"/downloads/orders-export.zip", body)
        self.assertIn(b"/downloads/report.csv", body)
        status, headers, _ = self._req(
            "/downloads/attachment", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers.get("Content-Disposition", ""))

    def test_api_orders_keys_match_table_columns(self):
        """/api/orders 键名与种子 orders 表列名高重合（映射证据素材防漂移）。"""
        status, _, body = self._req("/api/orders")
        self.assertEqual(status, 200)
        payload = json.loads(body.decode("utf-8"))
        api_keys = set(payload["orders"][0].keys())
        # 与 su_state_builder.SEED_DB_TABLES 的 orders 列集合逐字一致
        from su_state_builder import SEED_DB_TABLES
        orders_columns = {
            col[0] for tbl in SEED_DB_TABLES if tbl[1] == "orders"
            for col in tbl[3]}
        self.assertEqual(api_keys, orders_columns)
        self.assertEqual(ORDERS_API_PAYLOAD, payload)

    def test_slow_page_route_exists(self):
        """/slow 路由存在（HEAD 触发 40s sleep 不实际等待——仅验证 404 排除）。

        口径：用极短 timeout 发 GET，超时（URLError/timeout）即证明请求被
        服务端挂起处理（sleep 生效）；秒回 404 才是路由缺失。
        """
        try:
            status, _, _ = self._req("/slow")
            # 理论上 sleep 40s > timeout 5s 不会走到这里；若环境极快返回 200 也算路由在位
            self.assertIn(status, (200, 504))
        except (urllib.error.URLError, socket.timeout) as exc:
            # timeout 异常 = 服务端确实在挂起响应（sleep 生效）；
            # 注意 Python3.9 socket.timeout 不是 urllib.error.URLError 子类
            self.assertNotIn("404", str(exc))

    def test_site_has_minimum_page_count(self):
        """受保护页面数 ≥12 项能力清单（PRD §6.2 fixture 页量红线）。"""
        from server import PROTECTED_PAGES
        # 11 个常规受保护页（含 /users 用户管理）+ /login + /search
        # + /orders/<id> + /slow + api 端点
        self.assertGreaterEqual(len(PROTECTED_PAGES) + 4, 14)

    # -- 写计数红线契约 -------------------------------------------------------

    def test_write_counter_starts_zero_and_counts_writes(self):
        """write-counter 初始语义 + 非 GET 计数 +1（红线断言的站点侧基础）。

        注意：本测试会真实 +1 计数（这正是验证目标）。e2e 各场景独立起
        server 实例，类内顺序污染不影响独立进程断言（架构 §11.2 口径）。
        基线不用字面 0：同套件中 invalidate-session 等测试也合法产生写请求
        （每笔都必须计数 +1），以"当前值 +1"验证增量语义才是本测试目标。
        """
        status, _, body = self._req("/api/write-counter")
        self.assertEqual(status, 200)
        before = json.loads(body.decode("utf-8"))["count"]
        self.assertGreaterEqual(before, 0, "计数端点必须返回非负整数")
        status, _, body = self._req(
            "/api/orders/delete", "POST", b'{"ids":[1]}',
            {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        status, _, body = self._req("/api/write-counter")
        after = json.loads(body.decode("utf-8"))["count"]
        self.assertEqual(after, before + 1)
        # 登录 POST 豁免计数（SU 登录本身合法）
        self._login()
        status, _, body = self._req("/api/write-counter")
        self.assertEqual(json.loads(body.decode("utf-8"))["count"], after)


class SuStateBuilderTest(unittest.TestCase):
    """状态库种子 builder 契约（schema 与生产一致 + 种子数据完整）。"""

    def test_build_state_db_seed(self):
        """builder 建库：run 终态收口/blocked_events/种子表/外键候选逐项真实校验。"""
        import shutil
        import sqlite3
        import tempfile

        from su_state_builder import build_state_db, finalize_state_db

        tmp = Path(tempfile.mkdtemp(prefix="su-builder-"))
        try:
            db_path = tmp / "state" / "understanding.sqlite"
            ids = build_state_db(db_path, system_id="fixture-local")
            # 建库阶段保持 running（模拟采集进行中，供调用方继续回填）
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT status FROM run_meta").fetchone()
            conn.close()
            self.assertEqual(row["status"], "running")
            # 收口后进入 render-only 合法终态
            finalize_state_db(db_path, "completed")
            self.assertTrue(db_path.is_file())
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                # run 终态 completed（--render-only 前置判据）
                row = conn.execute(
                    "SELECT status, system_id FROM run_meta").fetchone()
                self.assertEqual(row["status"], "completed")
                self.assertEqual(row["system_id"], "fixture-local")
                # 种子页面 5 条（含 timeout 盲区页素材）且 id 映射真实
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0], 5)
                for url_key, page_id in ids["page_ids"].items():
                    row = conn.execute(
                        "SELECT url_key FROM pages WHERE page_id=?",
                        (page_id,)).fetchone()
                    self.assertEqual(row["url_key"], url_key)
                # aborted_method 拦截事件（第 8 节观测清单素材）
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM blocked_events WHERE "
                                 "kind='aborted_method'").fetchone()[0], 1)
                # T3 未执行动作（红线口径素材）
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM page_actions WHERE "
                                 "tier='T3' AND executed=0").fetchone()[0], 1)
                # orders/customers 表与列（含 customer_name 重合列）
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM db_tables").fetchone()[0], 2)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM db_columns WHERE "
                                 "name='customer_name'").fetchone()[0], 2)
                # 隐式外键候选 + redis 素材 + relations 证据
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM implicit_fk_candidates"
                                 ).fetchone()[0], 1)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM redis_patterns"
                                 ).fetchone()[0], 1)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0], 2)
            finally:
                conn.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
