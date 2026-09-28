"""API 观测器（架构 ARCH-SU-001 §2.3.7，PRD REQ-SU-009）。

**职责**：被动观测浏览器实际发生的 XHR/fetch/document 响应，把响应体归一成
"shape"（键路径 + 类型 + 脱敏示例），按 ``(url_path, method)`` 聚合落
``api_observations`` 表（样本 ≤5，FIFO 丢最旧）。

**双通道数据源划分（2026-09-28 审查口径，本节为唯一权威说明）**：
route 拦截层（红线④）与响应观测层是**互斥的两条通道**——

  - 被 ``route.abort()`` 的请求（非 GET 方法、白名单外域）**永远不会到达
    本 observer**：请求根本没有发生，也就没有 response 事件。这些请求的
    观测面由 ``blocked_events`` 表承担（url 只存 path、query 值置 KEY、
    post_data 过 redact，见 :mod:`su.route_policy`）；
  - 经 ``route.continue_()`` 放行的请求（GET/HEAD/OPTIONS + 白名单域）才会
    触发 ``page.on('response')``，进入本模块的 shape 观测。

  因此 ``api_observations``（被放行的只读请求）与 ``blocked_events``
  （被拦截的写/外域请求）在数据源上天然不重叠，渲染器分别取用。

安全口径（红线①，§2.3.7 审查修订）：
  - shape 三要素 = body + status + content-type，**永不落 headers 整段**
    （headers 常带 Authorization/Cookie，属 auth 泄露面；content-type 是
    从 response.headers 仅取的**单键**，不做全文快照）；
  - JSON 示例值、非 JSON 文本片段全部过 :func:`su.config.redact` /
    :func:`su.config.scrub_text`；二进制只记 content-type + size，不落正文；
  - 单条 shape ≤8KB（NFR-SU-005），超限逐步降级（示例置空 → 仅类型骨架）。

**不 import playwright**：page/response 对象按 duck-typing 使用，
类型注解仅在 ``typing.TYPE_CHECKING`` 下引用（REQ-SU-021）。
"""

import json
import time
import weakref
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from urllib.parse import urlsplit

from su.config import redact, scrub_text
from su.dto import ApiObservationRedacted, RedactedDict
from su.state_store import StateStore

if TYPE_CHECKING:  # 仅类型注解引用，运行时不导入 playwright（软依赖红线）
    from playwright.sync_api import Page, Request, Response

__all__ = [
    "MAX_RECORD_BYTES",
    "MAX_SAMPLES_PER_ENDPOINT",
    "MAX_TEXT_SAMPLE",
    "OBSERVED_RESOURCE_TYPES",
    "ApiObservation",
    "ApiObserver",
]

# 单条 shape 落盘上限（NFR-SU-005，§2.3.7 常量口径）
MAX_RECORD_BYTES = 8 * 1024

# 同端点聚合样本上限（REQ-SU-009.3；state_store.upsert_api_endpoint 同口径）
MAX_SAMPLES_PER_ENDPOINT = 5

# 非 JSON 文本响应的前置采样长度（字符，§2.3.7：前 200 字符脱敏文本）
MAX_TEXT_SAMPLE = 200

# 记录范围（§2.3.7：resource_type ∈ {xhr, fetch, document} 才记录；
# image/font/media/stylesheet 等静态资源与 API 语义无关，直接丢弃）
OBSERVED_RESOURCE_TYPES = frozenset({"xhr", "fetch", "document"})

# JSON shape 的路径深度上限（遗留系统响应嵌套极深时防 shape 自身爆炸）
_MAX_SHAPE_DEPTH = 6

# 列表采样的代表元素数（取前 N 个元素归一后合并类型）
_LIST_SAMPLE_ELEMENTS = 3

# shape 内示例值截断长度（过 redact 的 max_str_len 参数）
_SHAPE_EXAMPLE_MAX_LEN = 64

# 二进制大小探测失败时的占位值
_SIZE_UNKNOWN = -1


@dataclass
class ApiObservation:
    """单条 API 观测（内存形态，flush 时转 :class:`ApiObservationRedacted`）。

    Attributes:
        url_path: 端点路径（query 值全部键名化置 KEY，与 blocked_events 同口径）。
        method: 请求方法（大写）。
        status: 响应状态码（观测通道必有响应，理论上非 None；类型容错）。
        request_shape: 请求体 shape（可空；POST 类已被 route abort，
            实践中多为 GET 的 None 或 query 键名摘要）。
        response_shape: 响应体 shape（body + status + content-type 三要素）。
        observed_on_page: 观测发生页（crawler 注入的 current page_id）。
        ts: 观测时间戳（unix 秒）。
    """

    url_path: str
    method: str
    status: Optional[int]
    request_shape: Optional[RedactedDict]
    response_shape: Optional[RedactedDict]
    observed_on_page: int
    ts: float

    def to_dto(self) -> ApiObservationRedacted:
        """转写盘 DTO（shape 已是 RedactedDict——summarize_shape 产物或 None）。

        Returns:
            ApiObservationRedacted: 供 :meth:`StateStore.upsert_api_endpoint`
                的 DTO（入口运行时断言在 state_store 侧执行）。
        """
        return ApiObservationRedacted(
            url_path=self.url_path,
            method=self.method,
            observed_on_page=self.observed_on_page,
            ts=self.ts,
            status=self.status,
            request_shape=self.request_shape,
            response_shape=self.response_shape,
        )


def _query_keynames(query: str) -> str:
    """query 串 → 键名化文本（值一律置 KEY，同键去重保序）。

    与 :func:`su.route_policy.sanitize_blocked_url` 的 query 口径一致，
    使 api_observations.url_path 与 blocked_events.url 形态可互相印证。

    Args:
        query: urlsplit 得到的 query 部分（不含 '?'）。

    Returns:
        str: 形如 ``ids=KEY&page=KEY``；无参数时空串。
    """
    if not query:
        return ""
    from urllib.parse import parse_qsl  # 局部导入：仅此处需要，避免顶层噪音

    kept: List[str] = []
    seen = set()
    for key, _value in parse_qsl(query, keep_blank_values=True):
        if key not in seen:
            seen.add(key)
            kept.append(key)
    return "&".join("{0}=KEY".format(key) for key in kept)


class ApiObserver:
    """API 响应观测器（§2.3.7 全量方法，REQ-SU-009）。

    典型接线（SiteCrawler 在 BFS 开始前）::

        observer = ApiObserver(store)
        observer.set_current_page(page_id)       # 主线程维护（同 current_url_key）
        observer.attach(page)                    # page.on('response') 注册

    线程模型：Playwright 同步 API 的事件回调在 ``sync_playwright()`` 块内
    于主线程派发（sync 实现为单线程 greenlet 调度），回调体内只允许
    duck-typing 访问事件对象（``response.request.resource_type`` /
    ``response.status`` / ``response.headers`` 等同步属性读取均为合法事件面）；
    网络取体（``response.body()``）改在**页边界 flush()** 执行，回调线程零 IO。

    **完结防护（2026-09-28 e2e 场景[2]卡死根因）**：sync Playwright 的
    ``response.body()`` 会等待响应体完结——被导航中断/永不完结的请求
    （如 T2 提交引发的卡死导航）上调用会**无界挂起**（实测：页边界 flush
    跟随卡死导航拖满服务端 keep-alive 窗口，greenlet 派发循环被占死、
    进程 0% CPU 假死）。防护协议：:meth:`attach` 同时注册
    ``requestfinished`` / ``requestfailed`` 监听，以 request 引用为键记录
    "已完结"标记；:meth:`flush` 只对已完结响应取体，未完结者**绝不取体**
    （标记 pending 丢弃、只记元数据计数——文档端 shape 缺失属诚实降级），
    确保 flush 全程有界。
    """

    def __init__(self, store: StateStore) -> None:
        """初始化观测器。

        Args:
            store: 状态库（flush 时经 upsert_api_endpoint 聚合落库）。
        """
        self._store = store
        # 待落库观测缓冲（页边界 flush 清空；观测对象为轻封装 _ResponseCapture）
        self._pending: List["_ResponseCapture"] = []
        # 已完结请求集合（WeakSet，键 = request 对象引用）：requestfinished/
        # requestfailed 事件把 request 放入集合，flush 据此判定"取体安全"。
        # 用 WeakSet 的理由：request 对象被 Playwright 释放时条目自动消失，
        # 长跑整站不会积累引用（完成标记的生命周期与 request 天然一致）
        self._finished_requests = weakref.WeakSet()
        # 静默开关（set_silent）：blocked_origin 探测期间 True，回调直接返回
        self._silent: bool = False
        # 当前页 id：由 crawler 主线程在每次 goto/click 后 set_current_page 注入
        # （与 route 层的 current_url_key 同一维护点；handler/回调不现查 URL）
        self._current_page_id: int = 0
        # 观测统计（报告用：总数/记录数/忽略数/取体失败数）
        self.stats: Dict[str, int] = {
            "responses_seen": 0,      # response 事件总数
            "observations": 0,        # 进入缓冲的观测数（过滤后）
            "ignored_resource": 0,    # resource_type 不在记录集的忽略数
            "body_fetch_errors": 0,   # flush 阶段取体失败数
            "body_fetch_skipped_pending": 0,  # 未完结响应跳过取体数（完结防护）
            "dropped_overflow": 0,    # 缓冲溢出丢弃数（防御性上界）
        }

    # ------------------------------------------------------------------
    # 事件接线
    # ------------------------------------------------------------------

    def attach(self, page: "Page") -> None:
        """注册响应观测与完结跟踪监听（§2.3.7 + 完结防护）。

        三个监听面：
          - ``response``：仅 ``resource_type ∈ {xhr, fetch, document}`` 的
            响应进入缓冲；其余（image/font/stylesheet/media…）忽略并计数；
          - ``requestfinished`` / ``requestfailed``：请求完结信号（响应体
            完结或请求终止），把 request 引用登记进
            :attr:`_finished_requests`——:meth:`flush` 仅对已完结请求取体，
            杜绝 sync ``response.body()`` 在导航中断响应上的无界挂起
            （docstring"完结防护"段，2026-09-28 e2e 场景[2]根因）。

        Args:
            page: Playwright Page 对象（crawler 注入，本模块不 import playwright）。
        """
        page.on("response", self._on_response)
        page.on("requestfinished", self._on_request_finished)
        page.on("requestfailed", self._on_request_finished)

    def _on_request_finished(self, request: "Request") -> None:
        """requestfinished/requestfailed 回调（只登记引用，零 IO 零阻塞）。

        两个事件都代表"该请求不会再有未完结的响应体"：finished = 响应体
        完整到达；failed = 请求终止（abort/导航抛弃/网络错误），其响应
        即便可达也必然已定型，取体不会挂起。

        Args:
            request: Playwright Request 事件对象（duck-typing）。
        """
        try:
            # WeakSet 要求对象可弱引用；Playwright 的 Request 是普通 Python
            # 对象，天然满足。异常（如不可弱引用的代理类型）保守忽略——
            # 未登记 = flush 时按"未完结"跳过取体，方向安全
            self._finished_requests.add(request)
        except TypeError:
            return

    def set_current_page(self, page_id: int) -> None:
        """crawler 主线程注入当前页 id（triggered_from 语义的观测面对应物）。

        Args:
            page_id: 当前正在探索的 ``pages.page_id``。
        """
        self._current_page_id = int(page_id)

    def set_silent(self, silent: bool) -> None:
        """开关观测静默模式（blocked_origin 探测等"必须不经 API 面"的导航用）。

        静默期内 ``_on_response`` 直接返回（不进缓冲、不计数）：探测导航外域
        被 abort 的失败响应/半途子资源不得进入 api_observations——blocked_events
        才是拦截证据的唯一落盘面（api_observations 外域零入库红线口径）。

        Args:
            silent: True=静默（回调直接返回）；False=恢复正常观测。
        """
        self._silent = bool(silent)

    def _on_response(self, response: "Response") -> None:
        """response 事件回调（只读事件对象属性 + 追加缓冲，零 IO 零阻塞）。

        Args:
            response: Playwright Response 事件对象（duck-typing）。
        """
        # 静默期（探测导航）：零观测、零计数（口径见 set_silent）
        if self._silent:
            return
        self.stats["responses_seen"] += 1
        request = response.request
        resource_type = ""
        try:
            resource_type = (request.resource_type or "").lower()
        except Exception:  # noqa: BLE001 - 事件对象异常不得拖垮浏览器回调
            self.stats["ignored_resource"] += 1
            return
        if resource_type not in OBSERVED_RESOURCE_TYPES:
            self.stats["ignored_resource"] += 1
            return
        # 缓冲上界（防御：单页 XHR 风暴时丢弃最旧，与样本 FIFO 同哲学）
        if len(self._pending) >= 500:
            self._pending.pop(0)
            self.stats["dropped_overflow"] += 1
        self._pending.append(
            _ResponseCapture(
                response=response,
                # request 引用：flush 时据此在 _finished_requests 查完结标记
                request=request,
                method=(request.method or "GET").upper(),
                url=request.url or "",
                status=_safe_int(getattr(response, "status", None)),
                # content-type 是从 headers 仅取的单键（§2.3.7：不做全文快照）
                content_type=_content_type_of(response),
                post_data=_safe_post_data(request),
                page_id=self._current_page_id,
                ts=time.time(),
            )
        )
        self.stats["observations"] += 1

    # ------------------------------------------------------------------
    # shape 归一（纯函数面，可离线单测）
    # ------------------------------------------------------------------

    def summarize_shape(
        self,
        body: Optional[bytes],
        content_type: str,
    ) -> RedactedDict:
        """响应体 → shape（三要素：body 摘要 + content-type + size；§2.3.7）。

        三分支（REQ-SU-009 AC3）：
          1. **JSON**（content-type 含 json 且可解析）：递归键路径 shape——
             每键记录 ``type`` 与示例值（示例值过 redact 脱敏 + 截断），
             列表归一元素类型（前 ≤3 个代表元素合并）；
          2. **文本**（text/、xml、html、javascript、form 等可判读形态）：
             ``text_sample`` 前 200 字符 scrub 文本 + size；
          3. **二进制/其它**（图片、字体、protobuf…）：只记
             ``content_type`` + ``size``，**不落任何正文**。

        体积收口：任一分支产物 JSON 序列化超 :data:`MAX_RECORD_BYTES` 时
        逐步降级——先清空全部示例值（``examples_dropped: true`` 标注），
        仍超限则整体退化为最小骨架（content_type + size + ``shape_truncated``）。

        **永不记录 headers 整段**：content_type 由调用方单键传入。

        Args:
            body: 响应体字节（None/空 → size=0 形态）。
            content_type: 响应 content-type 单键文本（可为空串）。

        Returns:
            RedactedDict: 已脱敏 shape dict（唯一合法落盘形态）。
        """
        raw = body or b""
        size = len(raw)
        shape: RedactedDict = RedactedDict({"content_type": content_type or "", "size": size})

        if size == 0:
            # 空响应体：仅三要素骨架（204/HEAD 语义等）
            return shape

        lowered = (content_type or "").lower()
        decoded: Optional[str] = None
        if "json" in lowered:
            decoded = _decode_utf8(raw)
            parsed: Any = None
            if decoded is not None:
                try:
                    parsed = json.loads(decoded)
                except (json.JSONDecodeError, ValueError):
                    parsed = None
            if parsed is not None:
                # ---- 分支 1：JSON → 递归键路径 shape ----
                shape["kind"] = "json"
                shape["shape"] = redact(
                    _json_shape(parsed, 0), max_str_len=_SHAPE_EXAMPLE_MAX_LEN)
                return _fit_shape_budget(shape)
            # content-type 声称 json 但解析失败：落入文本分支兜底
        if decoded is None:
            decoded = _decode_utf8(raw)

        if decoded is not None and _looks_textual(lowered, decoded):
            # ---- 分支 2：文本 → 前 200 字符 scrub 采样 ----
            shape["kind"] = "text"
            shape["text_sample"] = scrub_text(decoded[:MAX_TEXT_SAMPLE])
            return _fit_shape_budget(shape)

        # ---- 分支 3：二进制 → 只记 content-type + size（AC3 不落正文）----
        shape["kind"] = "binary"
        return shape

    def summarize_request_shape(self, post_data: Optional[str]) -> Optional[RedactedDict]:
        """请求体 → request_shape（GET 类请求通常无 body → None）。

        放行的请求理论上只可能是 GET/HEAD/OPTIONS（非 GET 已被 route abort），
        出现 body 属罕见形态（GET with body 的遗留系统确实存在）——按与响应
        相同的三分类归一；无 body 返回 None。

        Args:
            post_data: 请求体文本（duck-typing 自 request.post_data）。

        Returns:
            RedactedDict | None: 已脱敏请求 shape；无 body 时 None。
        """
        if not post_data:
            return None
        return self.summarize_shape(post_data.encode("utf-8"), "application/json; text")

    # ------------------------------------------------------------------
    # 落库
    # ------------------------------------------------------------------

    def flush(self) -> int:
        """页边界落库：缓冲观测逐条 summarize 后经 store 聚合进 api_observations。

        聚合语义由 :meth:`StateStore.upsert_api_endpoint` 承担
        （UNIQUE(url_path, method)，同端点样本 FIFO 截断 ≤5）。

        完结防护（有界性保证，2026-09-28 e2e 场景[2]卡死根因）：逐条先查
        完结标记——**未完结响应绝不取体**（sync ``response.body()`` 等待
        响应体完结，导航中断/永不完结的响应上会无界挂起并占死 greenlet
        派发循环），计数 ``body_fetch_skipped_pending`` 后丢弃（文档端
        shape 缺失属诚实降级，元数据 path/method/status 亦不再落——保持
        "api_observations 必有真实 shape"的表语义纯净）。取体失败
        （响应已被 GC 等）计数 ``body_fetch_errors`` 后跳过，
        **绝不写入编造的 shape**（禁 mock 红线）。

        Returns:
            int: 本次成功落库的观测条数。
        """
        written = 0
        pending, self._pending = self._pending, []
        for capture in pending:
            # 完结判定：requestfinished/requestfailed 未登记的响应一律不取体
            # （见 ApiObserver docstring"完结防护"）——挂起的 response.body()
            # 是页边界 flush 唯一可能的无界阻塞点，此处闸门封死后 flush 全程有界
            if not self._is_request_finished(capture):
                self.stats["body_fetch_skipped_pending"] += 1
                continue
            try:
                body = capture.fetch_body()
            except Exception:  # noqa: BLE001 - 响应体不可得：计数跳过，不造假
                self.stats["body_fetch_errors"] += 1
                continue
            response_shape = self.summarize_shape(body, capture.content_type)
            request_shape = self.summarize_request_shape(capture.post_data)
            observation = ApiObservation(
                # url_path：只存 path，query 值键名化置 KEY（§2.3.7 常量注释口径）
                url_path=_path_with_keynames(capture.url),
                method=capture.method,
                status=capture.status,
                request_shape=request_shape,
                response_shape=response_shape,
                observed_on_page=capture.page_id,
                ts=capture.ts,
            )
            self._store.upsert_api_endpoint(observation.to_dto())
            written += 1
        return written

    def _is_request_finished(self, capture: "_ResponseCapture") -> bool:
        """捕获条目的请求是否已收到完结信号（requestfinished/requestfailed）。

        request 引用不可弱引用等异常形态保守判"未完结"（跳过取体，方向安全）。

        Args:
            capture: 待判定观测条目。

        Returns:
            bool: True=已完结，取体安全；False=未完结，禁止取体。
        """
        try:
            return capture.request in self._finished_requests
        except TypeError:  # 不可哈希/不可弱引用的异常对象形态：保守未完结
            return False

    def pending_count(self) -> int:
        """当前未落库观测数（页边界 drain 前观测用）。"""
        return len(self._pending)


# ---------------------------------------------------------------------------
# 内部：轻封装的响应捕获（延迟取体）
# ---------------------------------------------------------------------------

@dataclass
class _ResponseCapture:
    """response 事件的轻封装（回调线程只存引用与廉价属性，体延迟到 flush 取）。

    延迟取体的理由（§2.3.7 线程模型）：回调线程保持零 IO/零阻塞；页边界
    flush 时响应体通常仍在 Playwright 内存缓存中，取回成功率高且失败可计数。

    完结防护配套：额外保存 ``request`` 引用——flush 以它在
    :attr:`ApiObserver._finished_requests` 查完结标记，未完结者不取体
    （sync ``response.body()`` 对未完结响应会无界挂起，2026-09-28 实测）。
    """

    response: "Response"      # 事件对象引用（duck-typing）
    request: "Request"        # 所属请求引用（完结标记的查表键）
    method: str               # 请求方法（已大写）
    url: str                  # 请求完整 URL
    status: Optional[int]     # 响应状态码
    content_type: str         # content-type 单键（headers 唯一取用面）
    post_data: Optional[str]  # 请求体文本（放行 GET 常为 None）
    page_id: int              # 观测发生页（crawler 注入）
    ts: float                 # 事件时间戳

    def fetch_body(self) -> Optional[bytes]:
        """取响应体（页边界主线程调用；调用前置条件 = 请求已完结）。

        **调用方必须先过完结判定**（:meth:`ApiObserver._is_request_finished`）：
        未完结响应上调用会等待响应体完结而无限阻塞（sync Playwright 语义）。
        已过完结判定的调用只可能立即返回或快速抛错（体缓存已释放等）。

        Returns:
            bytes | None: 响应体字节；事件对象不提供 body 时 None。

        Raises:
            Exception: Playwright 取体错误（响应已失效等）——由调用方吸收计数。
        """
        body_fn = getattr(self.response, "body", None)
        if not callable(body_fn):
            return None
        return body_fn()


# ---------------------------------------------------------------------------
# 内部：shape 归一纯函数
# ---------------------------------------------------------------------------

def _json_shape(node: Any, depth: int) -> Dict[str, Any]:
    """JSON 节点 → 递归 shape（键路径 + 类型 + 示例值；未脱敏，外层统一 redact）。

    形态约定：
      - dict → ``{"type": "object", "keys": {k: shape(k), ...}}``；
      - list → ``{"type": "array", "count_sample": n, "items": <元素合并 shape>}``；
      - 标量 → ``{"type": <json类型名>, "example": <截断示例>}``；
      - 超过 :data:`_MAX_SHAPE_DEPTH` 层 → 只记类型不再下钻（防 shape 爆炸）。

    Args:
        node: json.loads 产物节点。
        depth: 当前深度（根为 0）。

    Returns:
        dict: 未脱敏 shape 节点（示例值由外层 redact 管线统一处理）。
    """
    if depth >= _MAX_SHAPE_DEPTH:
        return {"type": _json_type_name(node), "truncated_depth": True}
    if isinstance(node, dict):
        keys: Dict[str, Any] = {}
        for key, value in node.items():
            # 敏感键在此**保留键名**（示例值置 None），由外层 redact 把键名
            # 命中替换为 ***REDACTED*** 时键名形态仍完整可见（schema 语义）
            keys[str(key)] = _json_shape(value, depth + 1)
        return {"type": "object", "keys": keys}
    if isinstance(node, list):
        merged: Optional[Dict[str, Any]] = None
        for item in node[:_LIST_SAMPLE_ELEMENTS]:
            item_shape = _json_shape(item, depth + 1)
            merged = _merge_item_shapes(merged, item_shape)
        return {
            "type": "array",
            "count_sample": len(node),
            "items": merged if merged is not None else {"type": "empty"},
        }
    shape: Dict[str, Any] = {"type": _json_type_name(node)}
    if node is not None:
        shape["example"] = node
    return shape


def _merge_item_shapes(
    existing: Optional[Dict[str, Any]],
    incoming: Dict[str, Any],
) -> Dict[str, Any]:
    """列表代表元素的 shape 合并（异构列表的类型并集 + 键并集）。

    Args:
        existing: 已合并 shape（首个元素时为 None）。
        incoming: 新元素 shape。

    Returns:
        dict: 合并后 shape。
    """
    if existing is None:
        return incoming
    if existing.get("type") == incoming.get("type") == "object":
        merged_keys = dict(existing.get("keys") or {})
        for key, sub in (incoming.get("keys") or {}).items():
            if key in merged_keys:
                merged_keys[key] = _merge_item_shapes(merged_keys[key], sub)
            else:
                merged_keys[key] = sub
        return {"type": "object", "keys": merged_keys}
    if existing.get("type") == incoming.get("type"):
        return existing
    # 类型不同 → 并集标注（示例取先到者，保留可解释性）
    return {
        "type": "union",
        "members": sorted({existing.get("type", "?"), incoming.get("type", "?")}),
        "example": existing.get("example", incoming.get("example")),
    }


def _json_type_name(node: Any) -> str:
    """JSON 节点 → 类型名（string/number/boolean/null/object/array）。

    Args:
        node: JSON 节点值。

    Returns:
        str: 类型名文本。
    """
    if node is None:
        return "null"
    if isinstance(node, bool):
        return "boolean"
    if isinstance(node, (int, float)):
        return "number"
    if isinstance(node, str):
        return "string"
    if isinstance(node, list):
        return "array"
    if isinstance(node, dict):
        return "object"
    return "unknown"


def _fit_shape_budget(shape: RedactedDict) -> RedactedDict:
    """shape 体积收口（≤ :data:`MAX_RECORD_BYTES`，超限两级降级）。

    降级阶梯：
      1. 清空所有 ``example``/``text_sample``（保留类型骨架，标注
         ``examples_dropped: true``）；
      2. 仍超限 → 整体退化为最小骨架（content_type/size/kind + shape_truncated）。

    Args:
        shape: 已脱敏 shape（summarize_shape 分支产物）。

    Returns:
        RedactedDict: 满足体积约束的 shape。
    """
    if _json_size(shape) <= MAX_RECORD_BYTES:
        return shape
    stripped = _strip_examples(shape)
    if _json_size(stripped) <= MAX_RECORD_BYTES:
        stripped["examples_dropped"] = True
        return stripped
    return RedactedDict({
        "content_type": shape.get("content_type", ""),
        "size": shape.get("size"),
        "kind": shape.get("kind", "unknown"),
        "shape_truncated": True,
    })


def _strip_examples(node: Any) -> Any:
    """递归剔除 example/text_sample 键（保留类型骨架）。

    Args:
        node: shape 节点（dict/list/标量）。

    Returns:
        Any: 剔除示例后的同形结构。
    """
    if isinstance(node, dict):
        out = RedactedDict() if isinstance(node, RedactedDict) else {}
        for key, value in node.items():
            if key in ("example", "text_sample"):
                continue
            out[key] = _strip_examples(value)
        return out
    if isinstance(node, list):
        return [_strip_examples(item) for item in node]
    return node


def _json_size(value: Any) -> int:
    """值的 JSON 序列化字节长度（体积判定用；default=str 容错非常规类型）。

    Args:
        value: 任意可序列化值。

    Returns:
        int: UTF-8 字节数。
    """
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return MAX_RECORD_BYTES + 1  # 无法序列化 → 视为超限走降级


def _looks_textual(content_type: str, decoded: str) -> bool:
    """判定解码文本是否可按"文本响应"采样（防把乱码二进制当文本落盘）。

    判据：content-type 命中已知文本形态，或解码文本的可打印比例 ≥ 0.7。

    Args:
        content_type: 归一小写的 content-type。
        decoded: UTF-8 解码成功的文本。

    Returns:
        bool: True=按文本分支处理。
    """
    textual_hints = ("text/", "xml", "html", "javascript", "form-urlencoded", "csv", "yaml")
    if any(hint in content_type for hint in textual_hints):
        return True
    if not decoded:
        return False
    sample = decoded[:MAX_TEXT_SAMPLE]
    printable = sum(1 for ch in sample if ch.isprintable() or ch in "\r\n\t")
    return printable / float(len(sample)) >= 0.7


def _decode_utf8(raw: bytes) -> Optional[str]:
    """UTF-8 解码（失败返回 None，不强行替换解码防乱码入文）。

    Args:
        raw: 字节数据。

    Returns:
        str | None: 解码文本；非 UTF-8 返回 None。
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _path_with_keynames(url: str) -> str:
    """完整 URL → 观测路径（scheme/host 丢弃、path 保留、query 值置 KEY）。

    与 :func:`su.route_policy.sanitize_blocked_url` 同口径（api 观测与拦截
    事件两张表的 URL 形态保持一致，便于渲染器交叉印证），但**不叠加
    scrub_text**：path 段是端点语义主体，PII 值形态（内嵌手机号段等）由
    落库前的 redact 管线在 shape 侧兜底，路径本身保持可定位性。

    Args:
        url: 请求完整 URL。

    Returns:
        str: 形如 ``/api/order?id=KEY`` 的端点路径文本。
    """
    parts = urlsplit(url or "")
    path = parts.path or "/"
    keynames = _query_keynames(parts.query)
    if keynames:
        return "{0}?{1}".format(path, keynames)
    return path


def _content_type_of(response: "Response") -> str:
    """从 response.headers 仅取 content-type 单键（headers 唯一取用面）。

    Args:
        response: Playwright Response 事件对象。

    Returns:
        str: content-type 文本；缺失/异常返回空串。
    """
    try:
        headers = response.headers or {}
        value = headers.get("content-type")
        return str(value) if value else ""
    except Exception:  # noqa: BLE001 - 事件对象异常不得冒泡进回调
        return ""


def _safe_post_data(request: Any) -> Optional[str]:
    """安全读取 request.post_data（个别重定向请求访问会抛，容错为 None）。

    Args:
        request: Playwright Request 事件对象。

    Returns:
        str | None: 请求体文本；不可得时 None。
    """
    try:
        return getattr(request, "post_data", None)
    except Exception:  # noqa: BLE001 - 同上，容错读取
        return None


def _safe_int(value: Any) -> Optional[int]:
    """宽松 int 转换（状态码容错；不可转换返回 None）。

    Args:
        value: 原始值。

    Returns:
        int | None: 整数值或 None。
    """
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
