"""route 拦截决策纯函数 + 有界拦截事件队列（红线④）
——架构 ARCH-SU-001 §2.3.6 ``install_route_guard`` / §3 DDL ``blocked_events``，
PRD REQ-SU-007。

**拆分说明（相对架构文件清单的合理微调）**：架构把 route 逻辑写在
``site_crawler.install_route_guard`` 的 handler 闭包内。本文件把 handler 中**全部
可判定逻辑纯函数化**，site_crawler 后续只保留薄壳（``route.abort()`` /
``route.continue_()`` 两行 + 内存计数），理由：

§2.3.6 审查给出的 handler 硬性执行模型——"体内禁止调用任何 sync Playwright API，
仅允许 ① 内存计数器 ② 有界 ``queue.put(block=False)`` ③ abort/continue_"。
把决策与净化逻辑外提成纯函数后：
① handler 天然只剩三类允许操作，"零 Playwright API 调用"（PRD §6.1
   ``test_su_route_guard`` 要求以 mock page 断言零调用）由**结构**保证而非靠自觉；
② 决策分支（method / origin / 放行）与有界队列满丢弃计数可脱离浏览器单测；
③ url/post_data 净化（防凭据经查询串落库）与 :func:`su.config.redact` 的接线
   在此集中，避免净化口径散落到 handler 体内难以审查。

**本模块不 import playwright**（红线：软依赖绝不顶层硬 import）。
"""

import json
import queue
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from su.config import redact, scrub_text
from su.dto import BlockedEventRedacted

__all__ = [
    "BLOCKED_METHODS",
    "QUERY_VALUE_PLACEHOLDER",
    "DEFAULT_BLOCKED_QUEUE_MAXSIZE",
    "RouteDecision",
    "decide",
    "normalize_origin",
    "sanitize_blocked_url",
    "sanitize_post_data",
    "BlockedEventQueue",
]

# 一律拦截的请求方法（REQ-SU-007 原文集合；T2 显式 GET 表单天然不属于此集合）
BLOCKED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# query 值键名化占位符（§3 DDL 注释：url 只存 path，query 值全部置 KEY，
# 防凭据经查询串落库；键名保留以维持可解释性）
QUERY_VALUE_PLACEHOLDER = "KEY"

# 有界队列默认容量（handler 运行在 Playwright 回调线程，落库在页边界 drain；
# 容量给足以覆盖单页突发，满则丢弃并计数，绝不阻塞回调线程）
DEFAULT_BLOCKED_QUEUE_MAXSIZE = 1000

# post_data 解析后单值截断长度（复用 config.redact 默认口径，避免超长 body 撑爆库）
_POST_DATA_MAX_STR_LEN = 200


@dataclass
class RouteDecision:
    """route handler 的决策产物（薄壳据此直接调 abort/continue_）。

    Attributes:
        action: ``'abort'`` 或 ``'continue'``。
        kind: 拦截类型（``'aborted_method'`` / ``'blocked_origin'``）；
            放行时为 None。与 ``blocked_events.kind`` CHECK 约束取值对齐。
        reason: 中文原因说明（供报告与人工复核解释"为什么拦掉这个请求"）。
    """

    action: str
    kind: Optional[str]
    reason: str


def decide(
    request_method: str,
    request_url: str,
    allowed_origins: List[str],
    login_path: Optional[str] = None,
) -> RouteDecision:
    """route 拦截决策纯函数（红线④判定内核，REQ-SU-007）。

    判定顺序（危险优先）：
      0. method == POST 且请求同源且 path == login_path → continue
         （``login_relogin_allowed``，REQ-SU-004.4）：会话失效自动重登要
         在同一浏览器上下文里重新提交登录表单——route 守卫先于任何导航
         安装且不可动态摘除（Playwright 无可靠动态解除通道），若一律拦
         截 POST 则 crawler 运行期的重登导航必然失败。登录 POST 是 SU
         自身行为（fixture 写计数对 POST /login 同样豁免、红线口径"非
         GET 请求 POST /login 除外"），豁免面钉死为**同源 + 精确等于配
         置登录路径**，扩一个字符都不放行；
      1. method ∈ {POST, PUT, PATCH, DELETE} → abort（``aborted_method``）；
      2. 请求 origin ∉ allowed_origins → abort（``blocked_origin``）；
      3. 其余（GET/HEAD/OPTIONS 且同白名单域）→ continue。

    Args:
        request_method: 请求方法（大小写不敏感）。
        request_url: 请求完整 URL。
        allowed_origins: 域名白名单（形如 ``['https://admin.example.com']``，
            由 :class:`su.config.SystemConfig.allowed_origins` 提供，
            默认 = base_url 同源）。
        login_path: 登录页 path（绝对路径形态，如 ``/login``；None/空 =
            不启用登录 POST 豁免，保持红线④原始"一律拦截"语义）。

    Returns:
        RouteDecision: abort/continue + kind + 中文原因。

    Note:
        纯函数——不发请求、不碰 Playwright、不写库，可完全 fixture 驱动单测。
        非白名单域判定用 scheme+host+port 三元组精确比对，避免
        ``https://evil.example.com`` 被 ``example.com`` 前缀误放行。
    """
    method = (request_method or "").strip().upper()

    # ---- 0. 登录 POST 豁免（REQ-SU-004.4 运行期自动重登所需）----
    # 精确匹配口径：origin 必须命中 allowed_origins 白名单、path 与配置
    # 登录路径**逐字符相等**（归一仅限尾斜杠折叠；不做前缀/子路由放宽——
    # 登录端点自身是唯一豁免面，/login/../x 等形态经 urlparse 归一后天然
    # 落不回 login_path）。GET 类导航请求不受本分支影响。
    if method == "POST" and login_path:
        request_origin = _origin_of(request_url)
        allowed_set = {_normalize_origin(o) for o in (allowed_origins or []) if o}
        if (request_origin is not None
                and _normalize_origin(request_origin) in allowed_set):
            normalized_login = (login_path or "").rstrip("/") or "/"
            request_path = urlsplit(request_url).path.rstrip("/") or "/"
            if request_path == normalized_login:
                return RouteDecision(
                    action="continue",
                    kind=None,
                    reason="同源登录端点 POST 放行（REQ-SU-004.4 会话失效自动重登）",
                )

    # ---- 1. 非 GET 类方法一律拦截（REQ-SU-007 主判据）----
    if method in BLOCKED_METHODS:
        return RouteDecision(
            action="abort",
            kind="aborted_method",
            reason="请求方法 {0} 属于一律拦截集合（仅 T2 显式 GET 表单不受此限）".format(method),
        )

    # ---- 2. 白名单外域拦截（防遍历把 agent 引到外部站点）----
    origin = _origin_of(request_url)
    # 无法解析出 origin（相对 URL / 伪协议）：交由薄壳按"同域基准"处理，
    # 此处保守放行会扩大攻击面，故 origin 解析失败也判拦截
    if origin is None:
        return RouteDecision(
            action="abort",
            kind="blocked_origin",
            reason="请求 URL 无法解析出来源域，按最严口径拦截：{0}".format(
                scrub_text((request_url or "")[:160])
            ),
        )
    allowed = {_normalize_origin(o) for o in (allowed_origins or []) if o}
    if _normalize_origin(origin) not in allowed:
        return RouteDecision(
            action="abort",
            kind="blocked_origin",
            reason="请求域 {0} 不在 allowed_origins 白名单内".format(origin),
        )

    # ---- 3. 其余放行（GET/HEAD/OPTIONS + 同白名单域）----
    return RouteDecision(
        action="continue",
        kind=None,
        reason="方法 {0} 且域 {1} 在白名单内，放行".format(method, origin),
    )


def _origin_of(url: str) -> Optional[str]:
    """取 URL 的 origin（scheme://host[:port]，主机小写）。

    Args:
        url: 请求 URL。

    Returns:
        str | None: origin 文本；无法解析（无 scheme 或无 host）返回 None。
    """
    parts = urlsplit(url or "")
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        return None
    try:
        port = parts.port
    except ValueError:
        # 端口非数字（畸形 URL）：视为不可解析
        return None
    if port is not None:
        return "{0}://{1}:{2}".format(scheme, host, port)
    return "{0}://{1}".format(scheme, host)


def _normalize_origin(origin: str) -> str:
    """origin 归一：小写 + 剥离默认端口（``https://x:443`` == ``https://x``）。

    配置里 ``allowed_origins`` 可能写成 ``https://host:443``，而实际请求 URL 常省略
    默认端口；不归一会误拦同域请求。

    公开别名 :func:`normalize_origin`：site_crawler 的白名单外目标发现通道
    （``_register_discovered_origin``）必须与 route 判定用**同一归一口径**比对
    白名单，否则 ``https://host:443`` 形态配置下会把放行域误登记为被拦域。
    """
    parts = urlsplit((origin or "").strip())
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        return (origin or "").strip().lower()
    default_port = {"http": 80, "https": 443}.get(scheme)
    try:
        port = parts.port
    except ValueError:
        port = None
    if port is not None and port != default_port:
        return "{0}://{1}:{2}".format(scheme, host, port)
    return "{0}://{1}".format(scheme, host)


# 公开别名：白名单外目标发现通道（site_crawler._register_discovered_origin）
# 与 route 判定共用同一 origin 归一口径，防止两处口径漂移
normalize_origin = _normalize_origin


def sanitize_blocked_url(url: str) -> str:
    """拦截事件的 URL 净化（§3 DDL 约束：只存 path，query 值全部键名化）。

    处理：
      1. 丢弃 scheme/host（只留 path）——域名信息由 kind+报告体现，path 足够定位端点；
      2. query 的**值全部替换为 ``KEY``**，键名保留（可解释"带哪些参数"而不泄露值）；
      3. fragment 丢弃（SPA 参数常塞 hash）；
      4. 结果再过 :func:`su.config.scrub_text` 兜一层 PII 值形态
         （path 段本身可能内嵌手机号/邮箱，如 ``/user/13800138000``）。

    Args:
        url: 原始请求 URL。

    Returns:
        str: 形如 ``/api/user?id=KEY&page=KEY`` 的净化文本。
    """
    parts = urlsplit(url or "")
    path = parts.path or "/"

    # query 键名化：保留键与出现顺序，值一律置 KEY（同键多值合并为一个键）
    kept_keys: List[str] = []
    seen = set()
    for key, _value in parse_qsl(parts.query, keep_blank_values=True):
        if key not in seen:
            seen.add(key)
            kept_keys.append(key)
    if kept_keys:
        query = "&".join("{0}={1}".format(key, QUERY_VALUE_PLACEHOLDER) for key in kept_keys)
    else:
        query = ""

    cleaned = urlunsplit(("", "", path, query, ""))
    return scrub_text(cleaned)


def sanitize_post_data(body: Any) -> Optional[str]:
    """post_data 净化（REQ-SU-007 原文：post_data 过 redact()）。

    解析策略（按可信度递降）：
      1. JSON 对象/数组 → :func:`su.config.redact`（键名命中 + PII 值形态 + 截断）；
      2. ``application/x-www-form-urlencoded`` 形态 → 解析成 dict 后 redact；
      3. 其它（multipart 二进制、纯文本）→ 只保留长度与 content 形态标注，
         **不落正文**（二进制正文无法有意义地脱敏，存了反成泄露面）。

    Args:
        body: 请求体（bytes / str / dict / None）。

    Returns:
        str | None: 已脱敏的 JSON 文本；body 为空返回 None。
    """
    if body is None:
        return None
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8")
        except UnicodeDecodeError:
            # 二进制 body：只记长度，绝不落正文
            return json.dumps(
                {"_binary": True, "size": len(body)}, ensure_ascii=False
            )
    if isinstance(body, dict):
        return json.dumps(dict(redact(body, max_str_len=_POST_DATA_MAX_STR_LEN)), ensure_ascii=False)
    if not isinstance(body, str):
        body = str(body)
    if not body.strip():
        return None

    # 1. JSON
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        parsed = None
    if parsed is not None:
        redacted = redact(parsed, max_str_len=_POST_DATA_MAX_STR_LEN)
        # redact 对 dict 返回 RedactedDict；对 list 返回 list；标量包装成 dict 落库
        if isinstance(redacted, dict):
            return json.dumps(dict(redacted), ensure_ascii=False)
        return json.dumps({"_value": redacted}, ensure_ascii=False)

    # 2. form-urlencoded
    # FIX(2026-09-28 自测发现①)：parse_qsl 对"无 '=' 的单段文本"会返回
    # (整段, '') 键值对，导致旧判定把任何自由文本都误认成 form 形态、
    # _text 通道不可达。修正：body 必须含 '=' 才走 form 通道，否则落
    # _text 通道整体 scrub（无键值语义的文本没有"键名"可言）。
    if "=" in body:
        pairs = parse_qsl(body, keep_blank_values=True)
        if pairs:
            form_dict: Dict[str, Any] = {}
            for key, value in pairs:
                # FIX(2026-09-28 自测发现②)：键名同样过 scrub_text——
                # PII 可能藏在键名里（如 `联系方式 13800138000=…`），而
                # redact 只处理值、键名仅做敏感键匹配不 scrub。值仍走
                # redact 管线（键名命中 + PII 值形态 + 截断），口径不变。
                key = scrub_text(key)
                # 同键多值归一为列表，保持信息量
                if key in form_dict:
                    existing = form_dict[key]
                    if isinstance(existing, list):
                        existing.append(value)
                    else:
                        form_dict[key] = [existing, value]
                else:
                    form_dict[key] = value
            return json.dumps(
                dict(redact(form_dict, max_str_len=_POST_DATA_MAX_STR_LEN)), ensure_ascii=False
            )

    # 3. 无法结构化的自由文本：只存脱敏截断文本（scrub 兜底 PII 值形态）
    return json.dumps(
        {"_text": scrub_text(body[:_POST_DATA_MAX_STR_LEN])}, ensure_ascii=False
    )


class BlockedEventQueue:
    """有界拦截事件队列（§2.3.6 handler 执行模型之②）。

    设计约束（审查硬性口径）：
      - handler 在 Playwright 回调线程内只能 ``put(block=False)``，**绝不阻塞回调线程**；
      - 队列满 → 丢弃该事件并 ``dropped`` 计数 +1（丢弃可见，不静默吞）；
      - 落库发生在 BFS **页边界 drain**（主线程），批量转 BlockedEventRedacted；
      - ``triggered_from_page``（page_id）由 crawler 主线程维护的 ``current_url_key``
        注入，**不由 handler 现查 URL**。
    """

    def __init__(self, maxsize: int = DEFAULT_BLOCKED_QUEUE_MAXSIZE) -> None:
        """初始化有界队列。

        Args:
            maxsize: 队列容量（≤0 归一为默认值，避免无界队列撑爆内存）。
        """
        self._maxsize = maxsize if maxsize and maxsize > 0 else DEFAULT_BLOCKED_QUEUE_MAXSIZE
        self._queue: "queue.Queue[BlockedEventRedacted]" = queue.Queue(maxsize=self._maxsize)
        # 满队丢弃计数（报告字段 dropped_blocked_events，非静默丢弃）
        self.dropped = 0
        self.enqueued = 0

    @property
    def maxsize(self) -> int:
        """队列容量。"""
        return self._maxsize

    def put(
        self,
        kind: str,
        url: str,
        method: Optional[str] = None,
        post_data: Any = None,
        page_id: Optional[int] = None,
        ts: Optional[float] = None,
    ) -> bool:
        """非阻塞入队（handler 唯一可用的写入动作）。

        Args:
            kind: ``aborted_method`` / ``blocked_origin`` / ``download`` / ``new_window``。
            url: 原始请求 URL（**入队前在本方法内净化**，避免净化逻辑落进 handler）。
            method: 请求方法。
            post_data: 请求体（入队前过 :func:`sanitize_post_data`）。
            page_id: 触发页（crawler 的 current_url_key 对应 page_id）。
            ts: 事件时间戳（默认取当前时刻）。

        Returns:
            bool: True=入队成功；False=队列满已丢弃（:attr:`dropped` 同时 +1）。
        """
        event = BlockedEventRedacted(
            kind=kind,
            # url 只存 path + query 键名化（§3 DDL），post_data 已过 redact
            url=sanitize_blocked_url(url),
            ts=ts if ts is not None else time.time(),
            method=(method or None),
            post_data=sanitize_post_data(post_data),
            page_id=page_id,
        )
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            # 满则丢弃并计数：绝不阻塞回调线程（阻塞 = 死锁风险）
            self.dropped += 1
            return False
        self.enqueued += 1
        return True

    def drain(self) -> List[BlockedEventRedacted]:
        """取空队列（BFS 页边界调用，批量落库 + heartbeat 同点）。

        Returns:
            list[BlockedEventRedacted]: 按入队顺序的事件列表（元素已净化）。
        """
        drained: List[BlockedEventRedacted] = []
        while True:
            try:
                drained.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return drained

    def qsize(self) -> int:
        """当前积压事件数（近似值，用于页边界 drain 前的观测）。"""
        return self._queue.qsize()

    def stats(self) -> Dict[str, int]:
        """队列统计（供 summary.json / 第 10 节报告：dropped 非 0 需在文档声明）。

        Returns:
            dict: ``{'maxsize', 'enqueued', 'dropped', 'pending'}``。
        """
        return {
            "maxsize": self._maxsize,
            "enqueued": self.enqueued,
            "dropped": self.dropped,
            "pending": self.qsize(),
        }
