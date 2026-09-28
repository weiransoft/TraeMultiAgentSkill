"""URL 规范化与去重键纯函数（架构 ARCH-SU-001 §2.3.6，REQ-SU-005.2）。

**拆分说明（相对架构文件清单的合理微调）**：架构 §2.3.6 把 ``url_key()`` 声明在
``site_crawler.py`` 内。本模块把它单独拆出（``url_key.py``），理由有三：
① 该函数是**纯函数**（无 Playwright / 无 IO / 无状态），单测无需浏览器即可逐例断言
   （§11 测试矩阵 ``test_su_url_key`` 要求数字段/UUID/hash 路由/追踪参数全量用例）；
② 它同时被 site_crawler（BFS 去重）、api_observer（endpoint 归一）、
   relation_analyzer（path↔表名匹配）三处复用，放在采集层叶子模块避免循环依赖；
③ site_crawler 后续交付时以 ``from su.url_key import url_key`` 复用，
   不产生第二份实现（DRY，杜绝两处归一口径漂移）。

归一口径（REQ-SU-005.2 逐条落实）：
  - 主机名小写；协议统一归一为 https（http 也归一，使同一站点不因协议重复入队）；
  - 默认端口剥离（https:443 / http:80）；
  - query 键名排序，追踪参数（TRACKING_PARAMS：utm_ 前缀、_t、timestamp 等）整体剥离；
  - 路径中的纯数字段 / UUID 段归一为 ``{id}``；
  - hash 路由保留 ``#/path`` 部分（SPA 的真实路由在 hash 里），且 hash 内部
    同样执行"数字/UUID 段归一 + 追踪参数剥离 + query 键排序"。
"""

import re
from typing import Dict, List, Tuple
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

__all__ = [
    "TRACKING_PARAMS",
    "ID_PLACEHOLDER",
    "normalize_url",
    "url_key",
]

# 追踪参数剥离名单（架构 §2.3.6 常量口径）。
# 以 ``_`` 结尾者按**前缀**匹配（如 ``utm_`` 覆盖 utm_source/utm_medium/…）；
# 其余按**完整键名**匹配（大小写不敏感）。
TRACKING_PARAMS: Tuple[str, ...] = (
    "utm_",          # UTM 系列（前缀）
    "timestamp",     # 时间戳
    "_t",            # 时间戳类短参
    "t",             # 通用时间戳/缓存击穿参数
    "_from",         # 来源标记
    "from_",         # 来源标记（前缀）
    "spm",           # 阿里系埋点
    "scm",           # 阿里系埋点
    "trace_",        # 链路追踪（前缀）
    "request_id",    # 单次请求 id
    "reqid",         # 同上简写
    "fbclid",        # Facebook 点击 id
    "gclid",         # Google 点击 id
    "ref",           # 来源页标记
    "ref_src",       # 来源标记
)

# 路径可变段归一占位符（数字段 / UUID 段统一替换）
ID_PLACEHOLDER = "{id}"

# 纯数字路径段：123、007 等
_NUMERIC_SEGMENT_RE = re.compile(r"^\d+$")
# UUID 路径段：8-4-4-4-12 十六进制（大小写均可）
_UUID_SEGMENT_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
# 长十六进制串（≥16 位，常见 hash 型 id，如 sha1 文件名、session 段）：一并归一
_LONG_HEX_SEGMENT_RE = re.compile(r"^[0-9a-fA-F]{16,}$")

# 默认端口表：与归一后的 https 协议一致的端口一律剥离
_DEFAULT_PORTS = {"http": "80", "https": "443"}


def _is_tracking_param(key: str) -> bool:
    """判定 query 键名是否命中追踪参数剥离名单（大小写不敏感）。

    Args:
        key: query 参数键名（原始大小写）。

    Returns:
        bool: True=命中剥离名单，规范化时整体丢弃该键值对。
    """
    lowered = key.strip().lower()
    if not lowered:
        return False
    for entry in TRACKING_PARAMS:
        entry_lower = entry.lower()
        # 以 '_' 结尾的条目按前缀匹配（utm_ / from_ / trace_）
        if entry_lower.endswith("_"):
            if lowered.startswith(entry_lower):
                return True
        elif lowered == entry_lower:
            return True
    return False


def _normalize_segment(segment: str) -> str:
    """归一单个路径段：数字段 / UUID 段 / 长 hex 段 → ``{id}``，其余原样返回。

    仅对"整段即 id"的形态归一（``/order/123``→``/order/{id}``）；
    ``/order-123`` 这类混合段**不**归一，避免误杀语义化路径。

    Args:
        segment: 单个路径段（已 percent 解码前的原文，归一判断用原文即可）。

    Returns:
        str: 归一后的路径段。
    """
    if not segment:
        return segment
    # 解码后再判定：URL 编码过的 UUID（%2D 形态）也要能被识别
    decoded = unquote(segment)
    if _NUMERIC_SEGMENT_RE.match(decoded):
        return ID_PLACEHOLDER
    if _UUID_SEGMENT_RE.match(decoded):
        return ID_PLACEHOLDER
    if _LONG_HEX_SEGMENT_RE.match(decoded):
        return ID_PLACEHOLDER
    return segment


def _normalize_path(path: str) -> str:
    """逐段归一路径（保留首斜杠与段序，空路径归一为 ``/``）。

    Args:
        path: urlsplit 得到的 path 部分（可能为空串）。

    Returns:
        str: 归一后的路径。
    """
    if not path:
        return "/"
    parts = path.split("/")
    # parts[0] 恒为空串（path 以 '/' 起始时），逐段替换非空段
    normalized = [_normalize_segment(p) if p else p for p in parts]
    return "/".join(normalized) or "/"


def _normalize_query(query: str) -> str:
    """归一 query 串：剥离追踪参数 → 键名排序 → 同键多值保持原相对顺序。

    输出用 ``urlencode`` 重新编码，保证同一逻辑 URL 得到字节级一致的键。

    Args:
        query: urlsplit 得到的 query 部分（不含 '?'）。

    Returns:
        str: 归一后的 query 串（不含 '?'）；无有效参数时为空串。
    """
    if not query:
        return ""
    # keep_blank_values=True：?a=&b=1 这类空值参数需保留（语义上仍是该键）
    pairs: List[Tuple[str, str]] = parse_qsl(query, keep_blank_values=True)
    kept: Dict[str, List[str]] = {}
    order: List[str] = []
    for key, value in pairs:
        if _is_tracking_param(key):
            continue
        if key not in kept:
            kept[key] = []
            order.append(key)
        kept[key].append(value)
    # 键名排序（大小写不敏感，稳定排序保持同键多值原序）
    sorted_keys = sorted(kept.keys(), key=lambda k: (k.lower(), k))
    flat: List[Tuple[str, str]] = []
    for key in sorted_keys:
        for value in kept[key]:
            flat.append((key, value))
    if not flat:
        return ""
    return urlencode(flat)


def _normalize_fragment(fragment: str) -> str:
    """归一 hash 路由片段（SPA 场景：``#/user/123?tab=2&utm_source=x``）。

    对 hash 内部执行与主 URL 一致的归一：路径段归一 + query 键排序 + 追踪参数剥离。
    非路由形态（如纯锚点 ``#section1``）仅做原样保留——其内容不含 '/' 与 '?'，
    归一函数天然不会改写它。

    Args:
        fragment: urlsplit 得到的 fragment 部分（不含 '#'）。

    Returns:
        str: 归一后的 fragment（不含 '#'）。
    """
    if not fragment:
        return ""
    # hash 内部可能自带 query：``/list?page=2`` —— 拆出 query 部分单独归一
    hash_path, hash_query = fragment, ""
    if "?" in hash_path:
        hash_path, hash_query = hash_path.split("?", 1)
    # 仅当 hash 呈路由形态（以 '/' 起始）才归一路径段，避免改写语义化锚点
    if hash_path.startswith("/"):
        hash_path = _normalize_path(hash_path)
    else:
        # 非 '/' 起始的路由（如 ``#user/123``）也逐段归一，但保留原始首段文本形态
        hash_path = "/".join(_normalize_segment(p) for p in hash_path.split("/"))
    hash_query = _normalize_query(hash_query)
    if hash_query:
        return "{0}?{1}".format(hash_path, hash_query)
    return hash_path


def normalize_url(raw_url: str) -> str:
    """URL 规范化（纯函数）：协议归一 https + 主机小写 + 默认端口剥离 +
    路径 id 段归一 + query 去追踪参数并排序 + hash 路由同规则归一。

    Args:
        raw_url: 原始 URL（绝对或仅路径；相对路径会保留原形态，仅做归一处理）。

    Returns:
        str: 规范化 URL。绝对 URL 输出 ``https://host[:port]/path[?query][#fragment]``；
        相对 URL（无 scheme/host）输出归一后的 ``path[?query][#fragment]``。
        空输入返回空串。
    """
    if not raw_url:
        return ""
    # 首尾空白不参与去重键计算（页面 href 常带换行/空格）
    url = raw_url.strip()
    parts = urlsplit(url)

    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()

    if scheme and host:
        # 绝对 URL：协议一律归一 https（REQ-SU-005.2：同一站点不因 http/https 重复入队）
        port = parts.port
        netloc = host
        # 显式端口且非该协议的默认端口才保留（默认端口剥离）
        if port is not None and _DEFAULT_PORTS.get(scheme) != str(port):
            netloc = "{0}:{1}".format(host, port)
        # userinfo 一律丢弃：URL 内嵌凭据不得进入任何键/落盘数据（红线①）
        normalized_scheme = "https"
        rebuilt_netloc = netloc
    else:
        # 相对 URL：无 scheme/host，保持相对形态（BFS 拼接前也可能直接喂相对路径）
        normalized_scheme = ""
        rebuilt_netloc = ""

    path = _normalize_path(parts.path)
    query = _normalize_query(parts.query)
    fragment = _normalize_fragment(parts.fragment)

    if not normalized_scheme:
        # 相对 URL 重组：path(?query)(#fragment)；path 为空时以 '/' 呈现
        out = path
        if query:
            out = "{0}?{1}".format(out, query)
        if fragment:
            out = "{0}#{1}".format(out, fragment)
        return out

    # 绝对 URL 用 urlunsplit 重组，避免手工拼接出错
    return urlunsplit((normalized_scheme, rebuilt_netloc, path, query, fragment))


def url_key(raw_url: str) -> str:
    """页面去重键（纯函数，BFS 幂等判定的唯一口径，REQ-SU-005.2）。

    当前实现等于 :func:`normalize_url` 的产物——单列一层是为了给后续扩展留缝
    （例如追加 locale 归一），并让调用方语义自解释：
    ``pages.url_key`` 唯一约束、``edges.from_key/to_key``、blocked_events 的
    ``triggered_from_page`` 注入全部走这一个函数，杜绝多处口径漂移。

    Args:
        raw_url: 原始 URL。

    Returns:
        str: 规范化去重键（同 normalize_url 输出）。
    """
    return normalize_url(raw_url)
