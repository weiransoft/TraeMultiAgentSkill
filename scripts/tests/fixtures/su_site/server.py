"""SU fixture 测试站——纯标准库登录型多页 Web 站（架构 §11.2 fixture 设计）。

用途：为 system_understanding.py 的 e2e 集成测试提供"真实可爬"的被测系统，
覆盖 PRD §6.2 场景矩阵所需的全部站点能力，零第三方依赖（http.server +
ThreadingHTTPServer）。

站点能力清单（≥12 页）：
  1. /login            GET 登录表单；POST 正确账密 → Set-Cookie 会话 + 302 /dashboard；
                       POST 错误账密 → 401（供场景[6]凭据错误验证 exit 4）
  2. /dashboard        首页/控制台；未登录一律 302 回 /login（登录成功判据来源）
  3. /orders           订单列表页：fetch /api/orders、删除按钮（真实 POST，供
                       route abort 验证）、hash 路由链接 #/orders/1..3
  4. /orders/<id>      订单详情页（服务端数字路径段路由）
  5. /orders#<route>   hash 路由 SPA 片段（url_key 保留 hash 验证）
  6. /products         商品页：重复链接（同 URL 多入口去重验证）、乱序查询参数
                       + utm_source 追踪噪声（url_key 剥离验证）
  7. /reports          报表页：GET 搜索表单（T2 中性动作验证）
  8. /search           GET 表单提交端点（按 q 参数回显）
  9. /admin            管理页：含"删除"文本的 GET 表单（分词器应判 T3 不执行）
 10. /users            用户管理页（手机号样本页：crawler 快照必须落
                       <REDACTED:phone> 脱敏形态，明文 0 出现——PII 红线素材）
 11. /external         外链陷阱页：https://example.invalid/（白名单外 blocked_origin）
 12. /downloads        下载陷阱页：Content-Disposition 附件 + .zip/.csv 后缀链接
 13. /slow             慢响应页：sleep 40s（供 --page-timeout-ms 制造 timeout）
 14. /settings /help /about  补齐广度的普通受保护页
 15. /favicon.ico      204 静默（避免 404 噪声污染 api 观测）
 16. /                首页（站点根，BFS depth 0 入口）

安全红线验证端点：
  - 任何非 GET 请求（POST /login 除外——登录 POST 是 SU 自身行为）都会使
    进程内写计数 +1；
  - GET /api/write-counter 返回 {"count": N}——SU 全链路跑完后 N 必须为 0
    （route guard 拦截全部非 GET 红线的直接证据）。
  - 计数保存在进程内存 + threading.Lock，每次独立起 server 实例计数天然归零
    （架构 §11.2 write-counter 基线口径）。

编程入口：start_su_site(port=0) -> (server, thread, state)
  port=0 由 OS 分配空闲端口；调用方用 server.server_address[1] 取实际端口，
  结束调用 server.shutdown() + server.server_close()。

测试设施：SU_SITE_ACCESS_LOG 环境变量指定路径时，每个请求处理完成落一行
``<epoch_ms> <method> <path> -> <status>`` 访问日志（2026-09-28 e2e 场景[2]
302 来源排查——真实 302 归属必须由服务端侧请求日志取证，不能靠浏览器侧
行为反推）。日志写失败静默容错，绝不影响请求处理本身。
"""
import html
import json
import os
import secrets
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

__all__ = ["SiteState", "SuSiteHandler", "start_su_site"]

# 测试账密（fixture 明文常量——仅存在于测试站内存，e2e 敏感串扫描以
# 运行目录为范围，本文件不在扫描范围内；密码为高熵串便于 grep 精确匹配，
# 并刻意包含 `#` 字符——验证密码经 config JSON/shell 全链路传递不被当注释截断）
TEST_USERNAME = "su_test"
TEST_PASSWORD = "SuTest#2026"

# 慢响应页 sleep 秒数：大于 e2e 场景注入的 --page-timeout-ms（建议 8000），
# 远大于正常超时，保证 page_timeout 场景必然触发。/slow 入口仅在 /dashboard
# 一处——route 层 goto 超时后页面仍在服务端后台 sleep，若多处入口会让
# crawler 重复 goto /slow（每次阻塞 40s），单入口把总耗时钉死在一次
SLOW_PAGE_SLEEP_SECONDS = 40
SLOW_LINK_PAGES = {"/dashboard"}

# 访问日志写入互斥锁（模块级：ThreadingHTTPServer 每请求一线程，多线程
# append 同一文件必须互斥，防单行交错撕裂；见 SuSiteHandler._access_log）
_ACCESS_LOG_LOCK = threading.Lock()


class SiteState:
    """测试站进程内共享状态（会话表 + 写计数 + 请求日志 + 会话失效开关）。

    每个 server 实例独占一个 SiteState：进程内全局计数 + 线程锁，
    "每次独立起 server 计数天然归零"由实例隔离保证（架构 §11.2）。
    """

    def __init__(self):
        """初始化空会话表、零写计数、请求日志与关断的失效开关（线程安全）。"""
        self.lock = threading.Lock()      # 保护 write_count/sessions/invalidate 开关
        self.write_count = 0              # 非 GET 写请求计数（红线断言目标）
        self.sessions = set()             # 有效会话 token 集合
        self.requests = []                # (method, path) 请求日志（调试用）
        # 会话失效开关（POST /api/test/invalidate-session 置位）：置位后
        # 下一个鉴权请求消费开关并拒绝该会话（302 回 /login）——真实站点
        # "会话服务端失效"场景的测试控制端点（验证自动重登链路素材）。
        self.invalidate_pending = False

    def bump_write(self) -> int:
        """写计数 +1 并返回累加后的值（非 GET 请求统一入口）。

        Args:
            无。

        Returns:
            int: 累加后的写计数。
        """
        with self.lock:
            self.write_count += 1
            return self.write_count

    def get_write_count(self) -> int:
        """读取当前写计数（快照，加锁读）。"""
        with self.lock:
            return self.write_count

    def issue_session(self) -> str:
        """签发新会话 token 并登记（登录成功时调用）。"""
        token = secrets.token_hex(16)
        with self.lock:
            self.sessions.add(token)
        return token

    def has_session(self, token, consume_invalidate: bool = True) -> bool:
        """判断 token 是否为有效会话（含"失效开关命中"消费语义）。

        开关置位且本请求携带有效会话时：撤销该 token 并复位开关，返回
        False（鉴权失败 → 调用方 302 回登录页）。一次开关只失效一次会话
        ——与真实服务端会话过期行为一致，供"失效 → 重登 → 恢复"链路验证。

        Args:
            token: 请求 cookie 携带的会话 token（可为 None）。
            consume_invalidate: False=本请求**不消费**失效开关（开关保持
                置位、本请求照常按会话有效性放行）。/search 端点专用
                （2026-09-28 e2e 场景[2]根因修复：SU 全链路 T2 素材导航
                不得被残留开关随机 302，详见 do_GET /search 注释）。

        Returns:
            bool: True=会话有效放行；False=无效或失效开关刚命中。
        """
        with self.lock:
            if not token or token not in self.sessions:
                return False
            if self.invalidate_pending and consume_invalidate:
                # 消费开关：撤销当前会话并复位（仅命中一次，后续重登正常）
                self.invalidate_pending = False
                self.sessions.discard(token)
                return False
            return True

    def invalidate_session_once(self) -> None:
        """置位会话失效开关（下一个鉴权请求失效一次，幂等置位）。"""
        with self.lock:
            self.invalidate_pending = True

    def log_request(self, method: str, path: str) -> None:
        """追加请求日志（上限 1000 条防内存膨胀，仅供排障不回传）。"""
        with self.lock:
            if len(self.requests) < 1000:
                self.requests.append((method, path))


def _page(title: str, body: str, logged_in: bool, with_nav: bool = True) -> bytes:
    """渲染统一 HTML 页面骨架（含导航栏，模拟真实后台系统）。

    Args:
        title: 页面标题。
        body: 主体 HTML 片段。
        logged_in: 已登录时导航栏带退出文本（仅作视觉真实感，不做退出）。
        with_nav: False=不带导航栏——登录失败页必须与受保护页的登录判定
            选择器（success_hint 等）视觉隔离，否则凭据错误时 SU 的
            success_hint 判据会误命中导航链接（REQ-SU-004 三判据语义前提）。

    Returns:
        bytes: 完整 UTF-8 HTML。
    """
    if not with_nav:
        html = (
            "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            f"<title>{title} - SU 测试站</title></head><body>"
            f"<h1>{title}</h1>{body}</body></html>"
        )
        return html.encode("utf-8")
    nav_links = [
        ('<a href="/dashboard">首页</a>', "/dashboard"),
        ('<a href="/orders">订单</a>', "/orders"),
        ('<a href="/products">商品</a>', "/products"),
        ('<a href="/reports">报表</a>', "/reports"),
        ('<a href="/admin">管理</a>', "/admin"),
        ('<a href="/users">用户管理</a>', "/users"),
        ('<a href="/external">外部集成</a>', "/external"),
        ('<a href="/downloads">下载中心</a>', "/downloads"),
        ('<a href="/settings">设置</a>', "/settings"),
        ('<a href="/help">帮助</a>', "/help"),
        ('<a href="/about">关于</a>', "/about"),
    ]
    nav = " | ".join(link for link, _ in nav_links)
    tail = ' | <span>退出登录</span>' if logged_in else ""
    html = (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"<title>{title} - SU 测试站</title></head><body>"
        f"<nav>{nav}{tail}</nav><hr><h1>{title}</h1>{body}"
        "</body></html>"
    )
    return html.encode("utf-8")


# ---------------------------------------------------------------------------
# 各页面 HTML 模板（内嵌常量，模块级便于单测直接引用比对）
# ---------------------------------------------------------------------------

LOGIN_HTML = (
    "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
    "<title>登录 - SU 测试站</title></head><body>"
    "<h1>系统登录</h1>"
    "<form id=\"login-form\" method=\"POST\" action=\"/login\">"
    "<label>用户名 <input name=\"username\" type=\"text\"></label>"
    "<label>密码 <input name=\"password\" type=\"password\"></label>"
    "<button type=\"submit\">登 录</button>"
    "</form></body></html>"
).encode("utf-8")

DASHBOARD_HTML_BODY = (
    "<p>欢迎使用 SU 测试站控制台。</p>"
    "<ul><li><a href=\"/orders\">处理中的订单</a></li>"
    "<li><a href=\"/products\">商品总览</a></li>"
    "<li><a href=\"/slow\">慢速报表（测试用）</a></li></ul>"
)

ORDERS_HTML_BODY = (
    "<h2>订单列表</h2>"
    "<table id=\"orders\"><thead><tr>"
    "<th>order_id</th><th>customer_name</th><th>total_amount</th><th>status</th>"
    "</tr></thead><tbody id=\"orders-body\"><tr><td colspan=\"4\">加载中…</td></tr></tbody></table>"
    # hash 路由链接：SPA 片段导航（url_key 保留 #hash 的验证素材）
    "<p><a href=\"/orders#/orders/1\">订单明细 1</a>"
    " <a href=\"/orders#/orders/2\">订单明细 2</a>"
    " <a href=\"/orders#/orders/3\">订单明细 3</a></p>"
    # 删除按钮：真实 POST 触发（route guard 必须 abort，write-counter 保持 0）
    "<button id=\"delete-selected\" onclick=\"delSel()\">删除选中订单</button>"
    # 正常翻页链接（重复链接验证去重：同 URL 双入口）
    "<p><a href=\"/products\">查看商品（重复链接 A）</a>"
    " <a href=\"/products\">查看商品（重复链接 B）</a></p>"
    "<script>\n"
    "async function load(){\n"
    "  const r = await fetch('/api/orders');\n"
    "  const d = await r.json();\n"
    "  const tb = document.getElementById('orders-body');\n"
    "  tb.innerHTML = d.orders.map(o =>\n"
    "    `<tr><td>${o.order_id}</td><td>${o.customer_name}</td>` +\n"
    "    `<td>${o.total_amount}</td><td>${o.status}</td></tr>`).join('');\n"
    "}\n"
    "function delSel(){\n"
    "  fetch('/api/orders/delete', {method:'POST',headers:{'Content-Type':'application/json'},"
    "body:JSON.stringify({ids:[1]})});\n"
    "}\n"
    "load();\n"
    # 页面加载即发起真实 POST：route guard 的 aborted_method 断言不依赖
    # 按钮点击路径（crawler 主线程 T2/快照操作会挤占 JS 任务队列，onclick
    # 路径不保证执行；加载期 fetch 在 domcontentloaded 前已进网络层，必然
    # 经过 route handler → abort）。若 guard 失效则 write-counter +1 可观测
    "fetch('/api/orders/delete', {method:'POST',headers:{'Content-Type':'application/json'},"
    "body:JSON.stringify({ids:[2]})}).catch(function(){});\n"
    "</script>"
)

# /api/orders 键名与 fixture 订单表列名高重合（隐式外键/映射推断素材）
ORDERS_API_PAYLOAD = {
    "orders": [
        {"order_id": 1, "customer_name": "张三", "total_amount": 199.0, "status": "paid"},
        {"order_id": 2, "customer_name": "李四", "total_amount": 88.5, "status": "pending"},
        {"order_id": 3, "customer_name": "王五", "total_amount": 42.0, "status": "shipped"},
    ]
}

PRODUCTS_HTML_BODY = (
    "<h2>商品总览</h2>"
    # 乱序查询参数 + utm_source 追踪噪声：同语义不同参数顺序/含追踪参数
    # （url_key 归一化验证素材——utm_ 前缀剥离 + query 键排序后必须同键）
    "<p><a href=\"/search?q=widget&amp;page=2&amp;utm_source=campaign\">搜索 widget 第 2 页</a>"
    " <a href=\"/search?page=3&amp;q=widget\">搜索 widget 第 3 页</a>"
    " <a href=\"/reports\">导出报表</a></p>"
    # 深度 2 独有子页（仅本页可达）：广度优先下这些页只在 /products 访问后
    # 才入队——页数预算截断必然留下 pending frontier（e2e 场景[5]素材）
    # SKU 用纯字母数字（w1/w2/w3）：连字符混排串（widget-001）在浏览器
    # T2 表单回显场景曾被实测触发 renderer 崩溃（配合 /search 转义双保险）
    "<ul><li>w1 标准件</li><li>w2 加固件</li></ul>"
    "<p><a href=\"/products/w1\">w1 详情</a>"
    " <a href=\"/products/w2\">w2 详情</a>"
    " <a href=\"/products/w3\">w3 详情</a></p>"
)

REPORTS_HTML_BODY = (
    "<h2>报表中心</h2>"
    # GET 搜索表单：中性动作（T2），crawler 应 fill+submit
    "<form id=\"search-form\" method=\"GET\" action=\"/search\">"
    "<label>关键词 <input name=\"q\" type=\"text\"></label>"
    "<input name=\"page\" type=\"hidden\" value=\"1\">"
    "<button type=\"submit\">搜索</button>"
    "</form>"
    "<p><a href=\"/downloads\">数据文件下载</a></p>"
)

SEARCH_HTML_BODY_TMPL = (
    # 回显值先做最小 HTML 转义（& < > "）——fixture 保持纯标准库，
    # 但绝不把 query 参数原样拼进 HTML：/search?q=widget-001 这类混排串
    # 会触发浏览器 tel: 误判扫描（renderer 崩溃源，2026-09-28 实测教训）
    "<h2>搜索结果</h2><p>关键词：<code>{q}</code>（第 {page} 页）</p>"
    "<p>共找到 3 条结果。</p>"
)

ADMIN_HTML_BODY = (
    "<h2>系统管理</h2>"
    # 含"删除"文本的 GET 表单：动作分词器必须判 T3 危险而绝不提交
    "<form id=\"purge-form\" method=\"GET\" action=\"/search\">"
    "<input name=\"q\" type=\"hidden\" value=\"audit\">"
    "<button type=\"submit\">删除全部审计日志</button>"
    "</form>"
    "<p><a href=\"/settings\">返回设置</a></p>"
)

# 用户管理页——PII 红线素材：正文内嵌中国大陆手机号明文样本，SU 采集
# （快照骨架/日志）任何落盘位置都必须呈 <REDACTED:phone> 脱敏形态、
# 明文 0 出现（su.config.PII_VALUE_PATTERNS["phone"] 正则可命中这些样本）。
# 刻意混入不可命中的近邻串（12345678901 首位非 3-9、15 位长数字），
# 证明脱敏走的是真实值形态正则而非"所有数字全替换"式简化实现。
USERS_HTML_BODY = (
    "<h2>用户管理</h2>"
    "<table><thead><tr><th>用户</th><th>联系电话</th><th>备注</th></tr></thead>"
    "<tbody>"
    "<tr><td>张三</td><td>13812345678</td><td>优先客户</td></tr>"
    "<tr><td>李四</td><td>15987654321</td><td>待回访</td></tr>"
    "<tr><td>王五</td><td>17700001111</td><td>企业客户</td></tr>"
    "<tr><td>赵六</td><td>12345678901</td><td>非手机号（首位非3-9，脱敏不应命中）</td></tr>"
    "<tr><td>客服</td><td>1380000111122222</td><td>15 位长数字（非手机号，不应命中）</td></tr>"
    "</tbody></table>"
    "<p>批量导出请联系管理员。</p>"
)

EXTERNAL_HTML_BODY = (
    "<h2>外部集成</h2>"
    # 白名单外链接：T1 口径下只记 T3 不点击；route guard 拦截断言素材放在
    # 下方 GET 表单——T2 会真实 requestSubmit 导航到 example.invalid（无预解析
    # 白名单预判路径），必然经过 route handler → abort + blocked_origins.json
    "<p><a href=\"https://example.invalid/partner\">第三方支付网关（外链）</a></p>"
    "<p><a href=\"https://example.invalid/docs\">外部文档</a></p>"
    "<form id=\"gateway-form\" method=\"GET\" action=\"https://example.invalid/gateway\">"
    "<label>商户号 <input name=\"merchant\" type=\"text\"></label>"
    "<button type=\"submit\">跳转支付网关</button>"
    "</form>"
)

DOWNLOADS_HTML_BODY = (
    "<h2>下载中心</h2>"
    # 下载陷阱双通道：后缀启发式（.zip/.csv）+ Content-Disposition 附件头
    "<p><a href=\"/downloads/orders-export.zip\">订单打包（.zip 后缀）</a></p>"
    "<p><a href=\"/downloads/report.csv\">报表导出（.csv 后缀）</a></p>"
    "<p><a href=\"/downloads/attachment\">附件下载（Content-Disposition 触发）</a></p>"
    # GET 表单版下载入口：T2 requestSubmit 真实导航 → 后缀启发式必然命中
    # route handler（T1 口径只记录不点击，无法产生 download 拦截事件）
    "<form id=\"export-form\" method=\"GET\" action=\"/downloads/manual-export.zip\">"
    "<label>导出范围 <input name=\"scope\" type=\"text\"></label>"
    "<button type=\"submit\">手工导出（.zip）</button>"
    "</form>"
)

SLOW_HTML_BODY = (
    "<h2>慢速报表</h2><p>本页面响应极慢（服务器 sleep 40 秒），用于超时降级验证。</p>"
)

SETTINGS_HTML_BODY = "<h2>系统设置</h2><p>站点参数配置页。</p>"
HELP_HTML_BODY = (
    "<h2>帮助中心</h2><p><a href=\"/about\">关于本站</a></p>"
)
ABOUT_HTML_BODY = "<h2>关于</h2><p>SU fixture 测试站——仅供集成测试。</p>"

# 受保护页面路由表：path → (标题, body 模板字符串)
PROTECTED_PAGES = {
    "/dashboard": ("首页", DASHBOARD_HTML_BODY),
    "/orders": ("订单列表", ORDERS_HTML_BODY),
    "/products": ("商品总览", PRODUCTS_HTML_BODY),
    "/reports": ("报表中心", REPORTS_HTML_BODY),
    "/admin": ("系统管理", ADMIN_HTML_BODY),
    "/users": ("用户管理", USERS_HTML_BODY),
    "/external": ("外部集成", EXTERNAL_HTML_BODY),
    "/downloads": ("下载中心", DOWNLOADS_HTML_BODY),
    "/settings": ("系统设置", SETTINGS_HTML_BODY),
    "/help": ("帮助中心", HELP_HTML_BODY),
    "/about": ("关于", ABOUT_HTML_BODY),
}


class SuSiteHandler(BaseHTTPRequestHandler):
    """SU 测试站请求处理器（每请求一线程，ThreadingHTTPServer 调度）。

    路由约定：
      - GET  /login、POST /login   —— 登录流程
      - GET  /api/write-counter    —— 写计数读取（红线断言）
      - GET  /api/orders           —— JSON 数据端点（登录态可选，便于观测）
      - POST 其余路径              —— 写请求：计数 +1 后返回 JSON（真实执行，
                                      证明"若 route guard 失效则计数可观测"）
      - GET  /slow                 —— sleep 40s 慢响应
      - GET  其余受保护页          —— 未登录 302 /login；已登录渲染页面
    """

    # 由 start_su_site 通过 server.site_state 注入（Handler 每请求新建，
    # 共享状态必须挂在 server 实例上）
    server: "ThreadingHTTPServer"

    def log_message(self, format, *args):  # noqa: A002 - 覆写基类签名
        """静默默认 stderr 访问日志（e2e 输出整洁；请求日志走 SiteState）。"""
        return

    def _access_log(self, status: int) -> None:
        """请求级访问日志（测试设施，2026-09-28 e2e 场景[2]302 来源取证）。

        SU_SITE_ACCESS_LOG 环境变量指定落盘路径（e2e 脚本注入场景目录下的
        site_access.log）；未设置时零开销直接返回。行格式::

            <epoch_ms> <method> <path> -> <status>

        文件锁用模块级 threading 锁（ThreadingHTTPServer 每请求一线程，
        单行 append 必须互斥防交错撕裂）。任何写失败静默容错——访问日志
        属排障设施，绝不允许影响请求处理本身。

        Args:
            status: 本请求最终响应状态码。
        """
        path = os.environ.get("SU_SITE_ACCESS_LOG")
        if not path:
            return
        line = "{0:.0f} {1} {2} -> {3}\n".format(
            time.time() * 1000.0, self.command, self.path, status)
        try:
            with _ACCESS_LOG_LOCK:
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(line)
        except OSError:
            return  # 日志设施故障不得反噬请求处理

    # -- 工具方法 -----------------------------------------------------------

    def _session_token(self):
        """从 Cookie 头解析会话 token（无则 None）。"""
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        cookie = SimpleCookie()
        cookie.load(raw)
        item = cookie.get("su_session")
        return item.value if item else None

    def _is_logged_in(self, consume_invalidate: bool = True) -> bool:
        """当前请求是否携带有效会话。

        Args:
            consume_invalidate: False=本请求不消费会话失效开关（透传给
                :meth:`SiteState.has_session`；/search 端点专用，见
                do_GET /search 注释）。
        """
        return self.server.site_state.has_session(
            self._session_token(), consume_invalidate=consume_invalidate)

    def _redirect(self, location: str, extra_headers=None) -> None:
        """发送 302 重定向（完成后落访问日志——302 归属取证的关键通道）。"""
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        for key, value in (extra_headers or []):
            self.send_header(key, value)
        self.send_header("Content-Length", "0")
        self.end_headers()
        self._access_log(int(HTTPStatus.FOUND))

    def _respond(self, status: int, body: bytes, content_type: str = "text/html; charset=utf-8", extra_headers=None) -> None:
        """发送带 body 的完整响应（完成后落访问日志）。"""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for key, value in (extra_headers or []):
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self._access_log(status)

    def _json(self, status: int, payload: dict) -> None:
        """发送 JSON 响应（api 端点统一出口）。"""
        self._respond(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      content_type="application/json; charset=utf-8")

    # -- GET ----------------------------------------------------------------

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 命名约定
        """GET 路由分发（含受保护页鉴权与特殊端点）。"""
        state = self.server.site_state
        parsed = urlparse(self.path)
        path = parsed.path
        state.log_request("GET", self.path)

        if path == "/favicon.ico":
            # 204 静默：浏览器自动请求，不应污染 api 观测统计
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            self._access_log(int(HTTPStatus.NO_CONTENT))
            return

        if path == "/login":
            if self._is_logged_in():
                self._redirect("/dashboard")
            else:
                self._respond(HTTPStatus.OK, LOGIN_HTML)
            return

        if path == "/api/write-counter":
            # 写计数读取端点（e2e 断言红线：SU 全链路跑完必须为 0）
            self._json(HTTPStatus.OK, {"count": state.get_write_count()})
            return

        if path == "/api/orders":
            # 登录态可选：SU route guard 只拦非 GET，GET 数据端点正常放行
            self._json(HTTPStatus.OK, ORDERS_API_PAYLOAD)
            return

        if path == "/slow":
            # 慢响应陷阱页：sleep 后置超时（不鉴权，保证 page_timeout 必触发）
            time.sleep(SLOW_PAGE_SLEEP_SECONDS)
            self._respond(HTTPStatus.OK, _page("慢速报表", SLOW_HTML_BODY, False))
            return

        if path == "/search":
            # /search 鉴权：本端点自身**不消费**会话失效开关（见下方注释）
            if not self._is_logged_in(consume_invalidate=False):
                self._redirect("/login")
                return
            # 会话失效开关只作用于普通鉴权页、不拦截本端点（2026-09-28
            # e2e 场景[2]根因修复）：/api/test/invalidate-session 是进程级
            # 一次性开关，场景[1]的 curl 用例置位后若没有后续鉴权请求消费，
            # 开关会跨场景残留到下一个 SU 进程——浏览器 T2 提交 /search 恰
            # 好命中开关时，本应回显的导航被 302 回 /login，URL 不变 →
            # settled 永不落定 → T2 协议收束并拖满页边界 flush（e2e 同脚本
            # 顺序复现、独立进程不复现的成因）。真实服务端的会话过期不会
            # "恰好吃掉某一条导航"；开关语义保留给重登链路素材页（orders 等）
            qs = parse_qs(parsed.query)
            # 回显值 HTML 转义后再拼模板（防注入 + 防浏览器 tel: 误判扫描，
            # 见 SEARCH_HTML_BODY_TMPL 注释）
            esc = html.escape
            body = SEARCH_HTML_BODY_TMPL.format(
                q=esc(qs.get("q", ["(空)"])[0]), page=esc(qs.get("page", ["1"])[0]))
            self._respond(HTTPStatus.OK, _page("搜索结果", body, True))
            return

        if path.startswith("/orders/"):
            # 服务端订单详情页 /orders/<id>（数字校验，非法 id 404）
            if not self._is_logged_in():
                self._redirect("/login")
                return
            order_id = path[len("/orders/"):]
            if order_id.isdigit():
                body = (f"<h2>订单 {order_id}</h2><p>该订单的状态与金额详见列表。</p>"
                        "<p><a href=\"/orders\">返回列表</a></p>")
                self._respond(HTTPStatus.OK, _page(f"订单 {order_id}", body, True))
            else:
                self._respond(HTTPStatus.NOT_FOUND, _page("未找到", "<p>页面不存在</p>", False))
            return

        if path.startswith("/products/"):
            # 商品详情页 /products/<sku>：仅从 /products 页可达（深度 2 独有）
            # ——广度优先下这些页在 /products 采集后才入队，页数预算截断时
            # 稳定留下 pending frontier（e2e 场景[4]预算断言素材）
            if not self._is_logged_in():
                self._redirect("/login")
                return
            sku = path[len("/products/"):]
            body = (f"<h2>商品 {sku}</h2><p>规格与库存详情。</p>"
                    "<p><a href=\"/products\">返回商品总览</a></p>")
            self._respond(HTTPStatus.OK, _page(f"商品 {sku}", body, True))
            return

        if path.startswith("/downloads/"):
            # 下载文件端点：/attachment（无扩展名）用 Content-Disposition 触发
            # 浏览器 download 事件（响应侧通道素材）；带后缀路径交给 route 层
            # 请求侧后缀启发式（.zip/.csv 直接 abort 不放行），本处理器不会被
            # 真实浏览器命中，保留仅为 curl 级自测可验证附件头
            if not self._is_logged_in():
                self._redirect("/login")
                return
            filename = path.rsplit("/", 1)[-1]
            payload = b"col_a,col_b\n1,2\n3,4\n"
            headers = [("Content-Disposition", f'attachment; filename="{filename}"')]
            self._respond(HTTPStatus.OK, payload,
                          content_type="application/octet-stream", extra_headers=headers)
            return

        if path in PROTECTED_PAGES:
            if not self._is_logged_in():
                # 未登录一律 302 回登录页（登录成功判据/会话失效行为验证）
                self._redirect("/login")
                return
            title, body = PROTECTED_PAGES[path]
            # /slow 入口按页注入：仅 SLOW_LINK_PAGES（/dashboard）携带——
            # 单入口防止 crawler 重复 goto 超时页造成 40s×N 耗时放大
            extra = ""
            if path in SLOW_LINK_PAGES:
                extra = '<p><a href="/slow">慢速报表（超时验证入口）</a></p>'
            self._respond(HTTPStatus.OK, _page(title, body + extra, True))
            return

        # 站点根 = 首页（BFS depth 0 入口）：与 /dashboard 同页渲染（导航
        # 高亮差异省略），保证 SU 以 base_url 起步时立即进入登录态首页
        if path == "/":
            if not self._is_logged_in():
                self._redirect("/login")
                return
            title, body = PROTECTED_PAGES["/dashboard"]
            self._respond(HTTPStatus.OK, _page(title, body, True))
            return

        self._respond(HTTPStatus.NOT_FOUND, _page("未找到", "<p>页面不存在</p>", False))

    # -- POST ---------------------------------------------------------------

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler 命名约定
        """POST 路由分发：登录 vs 写操作（计数）。"""
        state = self.server.site_state
        parsed = urlparse(self.path)
        path = parsed.path
        state.log_request("POST", self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""

        if path == "/login":
            # 登录 POST：解析表单，校验账密（此处计数豁免——SU 登录本身合法，
            # 红线断言只统计 /login 以外的写请求）
            form = parse_qs(raw.decode("utf-8", errors="replace"))
            username = form.get("username", [""])[0]
            password = form.get("password", [""])[0]
            if username == TEST_USERNAME and password == TEST_PASSWORD:
                token = state.issue_session()
                self._redirect("/dashboard", extra_headers=[
                    ("Set-Cookie", f"su_session={token}; Path=/; SameSite=Lax")])
            else:
                # 登录失败页：不带导航栏（与 success_hint 判定素材隔离）
                self._respond(HTTPStatus.UNAUTHORIZED,
                              _page("登录失败", "<p>用户名或密码错误</p>",
                                    False, with_nav=False))
            return

        # 测试控制端点：置位"会话失效一次"开关（e2e 场景模拟服务端会话过期）。
        # 该请求属写操作 → 计数 +1（真实语义：route guard 若失效放了行，
        # write-counter 断言立即捕获；SU 全链路零写下本端点永不被触碰）。
        if path == "/api/test/invalidate-session":
            state.invalidate_session_once()
            count = state.bump_write()
            self._json(HTTPStatus.OK, {"ok": True, "invalidate_pending": True,
                                       "write_count": count})
            return

        # 其余一切 POST 均为"写操作"：计数 +1 后真实执行（返回 200），
        # 若 SU route guard 失效放了行，write-counter 断言立即捕获
        count = state.bump_write()
        self._json(HTTPStatus.OK, {"ok": True, "write_count": count})


def start_su_site(port: int = 0):
    """启动 SU 测试站（后台守护线程），返回三元组供调用方管理与断言。

    Args:
        port: 监听端口；0 = OS 自动分配空闲端口（并发场景防冲突）。

    Returns:
        tuple: (server, thread, state)
            - server: ThreadingHTTPServer 实例；实际端口
              server.server_address[1]；收尾 server.shutdown() + server_close()
            - thread: 服务线程（daemon，进程退出自动回收）
            - state: SiteState（可直接读 write_count 做进程内断言）
    """
    state = SiteState()
    server = ThreadingHTTPServer(("127.0.0.1", port), SuSiteHandler)
    server.site_state = state  # Handler 经 self.server 访问共享状态
    thread = threading.Thread(target=server.serve_forever, daemon=True,
                              name="su-fixture-site")
    thread.start()
    return server, thread, state


if __name__ == "__main__":
    # 命令行模式：独立进程起站（e2e 脚本用），端口可用 argv[1] 指定。
    # 输出首行 "SU_SITE_PORT=<n>" 供 shell 解析，进程常驻直至 SIGTERM。
    import sys

    listen_port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    site_server, site_thread, site_state = start_su_site(listen_port)
    actual_port = site_server.server_address[1]
    print(f"SU_SITE_PORT={actual_port}", flush=True)
    try:
        # 主线程挂起等待（serve_forever 在守护线程；SIGTERM/SIGINT 自然终止）
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        site_server.shutdown()
        site_server.server_close()
