"""Redis 透镜——SCAN 采集与键模式聚类（架构 ARCH-SU-001 §2.3.9，PRD REQ-SU-014/015）。

**与 redis_guard.py 的分层关系（相对架构文件清单的合理微调）**：架构把
READONLY_COMMANDS / RedisGuard / RedisInspector / cluster_patterns 列在
``redis_inspector.py``。命令白名单守卫（READONLY_COMMANDS、RedisGuard）已按
"纯判定可脱离 Redis 容器全量拒绝集单测"的理由先行拆入 ``su/redis_guard.py``；
本模块补齐**键模式聚类**（纯函数部分）与 :class:`RedisInspector`（SCAN 采集类）
并再导出守卫。

**不 import redis**：全部代码零软依赖（纯标准库），客户端对象由调用方
按 duck-typing 注入（REQ-SU-021：软依赖探测统一在 deps.py）；采集类
**所有命令一律经 :meth:`su.redis_guard.RedisGuard.call` 单通道下发**
（红线③：su/ 包唯一命令出口，§5.4 静态审查项）。

聚类口径（REQ-SU-015 AC2）：键名按 ``:`` / ``_`` 分段，UUID/数字/日期/长 hex
段替换为 ``{uuid}``/``{n}``/``{date}``/``{hex}`` 占位符后聚合；每模式输出
count、无 TTL 占比（潜在泄漏信号）、类型分布、脱敏样例键 ≤3——**模式与样例
键均不含完整敏感值**（键名本身非敏感值，样例保留原始键供人工定位）。
"""

import json
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence

from su.config import RedisConfig, redact
from su.dto import RedactedDict, RedisKeyRecordRedacted
from su.redis_guard import READONLY_COMMANDS, RedisGuard
from su.state_store import StateStore

__all__ = [
    # 守卫再导出：下游（preflight / RedisInspector）统一从本模块取，
    # 与架构 §2.3.9 "守卫住在 redis_inspector" 的使用者视角保持一致
    "READONLY_COMMANDS",
    "RedisGuard",
    "RedisPattern",
    "cluster_patterns",
    # 采集类（§2.3.9 后半段，客户端经依赖注入使用）
    "RedisInspector",
]

# ---------------------------------------------------------------------------
# 分段与占位符识别（纯函数内部原语）
# ---------------------------------------------------------------------------

# 键名分段分隔符：冒号（Redis 惯例层级）与下划线（蛇形命名）——
# re.split 带捕获组可保留分隔符，重组模式串时维持层级可读性
_SEGMENT_SPLIT_RE = re.compile(r"([:_])")

# UUID 段：8-4-4-4-12 十六进制（大小写均可）
_UUID_SEGMENT_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
# 纯数字段：123、0007
_NUMERIC_SEGMENT_RE = re.compile(r"^\d+$")
# 日期段：2026-09-28（带横杠）与 20260928（紧凑，恰 8 位且月/日范围合法才判日期；
# 下划线分隔形态在分段后已拆成三段，天然不会命中紧凑判定）
_DATE_DASH_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 长 hex 段：≥16 位十六进制（sha1/md5/session hash 等），
# 注意数字段已被 _NUMERIC 先行捕获，纯数字的 16 位串归 {n} 而非 {hex}
_LONG_HEX_SEGMENT_RE = re.compile(r"^[0-9a-fA-F]{16,}$")

# 占位符常量（模式串中的替换形态，§2.3.9 口径）
PLACEHOLDER_UUID = "{uuid}"
PLACEHOLDER_NUMBER = "{n}"
PLACEHOLDER_DATE = "{date}"
PLACEHOLDER_HEX = "{hex}"

# 样例键数量上限（REQ-SU-015 AC2：模式不含完整敏感值，样例 ≤3）
SAMPLE_KEYS_LIMIT = 3


def _is_valid_compact_date(segment: str) -> bool:
    """判定 8 位紧凑日期（20260928 形态）的月/日范围合法性。

    仅长度匹配不够——``00000000``/``20991340`` 之类数字 id 不应误判为日期。
    校验：年在 1900~2999、月在 01~12、日在 01~31（不校验大小月，
    聚类容错优先；真日期段必然通过，真 id 段大概率被月/日范围拒绝）。

    Args:
        segment: 恰 8 位的数字段。

    Returns:
        bool: True=日期形态。
    """
    year = int(segment[0:4])
    month = int(segment[4:6])
    day = int(segment[6:8])
    return 1900 <= year <= 2999 and 1 <= month <= 12 and 1 <= day <= 31


def _placeholder_for_segment(segment: str) -> Optional[str]:
    """单段 → 占位符（非可变段返回 None 表示保留原文）。

    判定顺序敏感：UUID（含 '-'，先于日期）→ 日期（带横杠先判，8 位紧凑
    日期须过范围校验）→ 纯数字 → 长 hex。长 hex 放最后：纯数字 16 位串
    已由 {n} 捕获，剩余 ≥16 位且含 a-f 的才归 {hex}。

    Args:
        segment: 键名切分出的内容段（分隔符不进入本函数）。

    Returns:
        str | None: ``'{uuid}'/'{n}'/'{date}'/'{hex}'``；保留段 None。
    """
    if not segment:
        return None
    if _UUID_SEGMENT_RE.match(segment):
        return PLACEHOLDER_UUID
    if _DATE_DASH_RE.match(segment):
        return PLACEHOLDER_DATE
    if _NUMERIC_SEGMENT_RE.match(segment):
        # 8 位纯数字先做一次日期范围校验（20260928 形态的日期键很常见）
        if len(segment) == 8 and _is_valid_compact_date(segment):
            return PLACEHOLDER_DATE
        return PLACEHOLDER_NUMBER
    if _LONG_HEX_SEGMENT_RE.match(segment):
        return PLACEHOLDER_HEX
    return None


def key_to_pattern(key_name: str) -> str:
    """单个键名 → 模式串（纯函数，聚类键的生成口径）。

    步骤：按 ``:``/``_`` 切分（分隔符保留）→ 内容段逐个过占位符判定 →
    原样拼回（层级结构不变，``sess:abc:123`` 与 ``sess:abc:456`` 同归
    ``sess:abc:{n}``）。空键名返回空串（理论上 SCAN 不产出空键，防御）。

    Args:
        key_name: Redis 键名原文。

    Returns:
        str: 模式串（含 ``{uuid}/{n}/{date}/{hex}`` 占位符）。
    """
    if not key_name:
        return ""
    tokens = _SEGMENT_SPLIT_RE.split(key_name)
    out: List[str] = []
    for token in tokens:
        # 捕获组切分：分隔符（':'/'_'）与内容段交替出现；空串原样保留
        if token in (":", "_", ""):
            out.append(token)
            continue
        placeholder = _placeholder_for_segment(token)
        out.append(placeholder if placeholder is not None else token)
    return "".join(out)


class RedisPattern:
    """键模式聚类结果（对齐 ``redis_patterns`` 表行，§3 DDL）。

    Attributes:
        pattern: 模式串（如 ``'sess:{uuid}'``），表内 UNIQUE。
        key_count: 命中键数。
        no_ttl_ratio: 无 TTL（ttl_ms == -1）键占比 ∈ [0,1]——潜在泄漏信号。
        ttl_summary: TTL 分桶分布 dict（RedactedDict，落库为 JSON 文本）。
        type_summary: 类型分布 dict（key_type → 数量）。
        sample_keys: 脱敏样例键 ≤3（键名非敏感值，保留原文供人工定位）。
    """

    __slots__ = (
        "pattern", "key_count", "no_ttl_ratio",
        "ttl_summary", "type_summary", "sample_keys",
    )

    def __init__(
        self,
        pattern: str,
        key_count: int,
        no_ttl_ratio: float,
        ttl_summary: RedactedDict,
        type_summary: RedactedDict,
        sample_keys: List[str],
    ) -> None:
        """初始化聚类结果（字段语义见类 docstring）。"""
        self.pattern = pattern
        self.key_count = key_count
        self.no_ttl_ratio = no_ttl_ratio
        self.ttl_summary = ttl_summary
        self.type_summary = type_summary
        self.sample_keys = sample_keys

    def to_dict(self) -> RedactedDict:
        """转 RedactedDict（供 state_store 写 redis_patterns 表 / export）。

        ttl_summary/type_summary 已是 RedactedDict；sample_keys 转 JSON 文本
        与 DDL 列（TEXT）对齐。统计值全为计数与键名，无敏感值。
        """
        return RedactedDict({
            "pattern": self.pattern,
            "key_count": self.key_count,
            "no_ttl_ratio": self.no_ttl_ratio,
            "ttl_summary": json.dumps(
                dict(self.ttl_summary), ensure_ascii=False, sort_keys=True),
            "type_summary": json.dumps(
                dict(self.type_summary), ensure_ascii=False, sort_keys=True),
            "sample_keys": json.dumps(
                self.sample_keys, ensure_ascii=False),
        })


def _ttl_bucket(ttl_ms: Optional[int]) -> str:
    """单键 TTL → 分桶标签（ttl_summary 的分桶口径）。

    分桶：``no_ttl``（-1/None，永不过期——泄漏信号主体）、
    ``expired_or_missing``（≤-2，SCAN 与 TTL 间隙被删，理论罕见）、
    ``lt_1min`` / ``lt_1h`` / ``lt_1d`` / ``gte_1d``。

    Args:
        ttl_ms: PTTL 毫秒值（-1=无 TTL；None 视同 -1）。

    Returns:
        str: 分桶标签。
    """
    if ttl_ms is None or ttl_ms == -1:
        return "no_ttl"
    if ttl_ms <= -2:
        return "expired_or_missing"
    if ttl_ms < 60_000:
        return "lt_1min"
    if ttl_ms < 3_600_000:
        return "lt_1h"
    if ttl_ms < 86_400_000:
        return "lt_1d"
    return "gte_1d"


def cluster_patterns(keys: Sequence[RedisKeyRecordRedacted]) -> List[RedisPattern]:
    """键模式聚类（纯函数，REQ-SU-015 AC1/AC2）。

    输入是 SCAN 采集所得的键记录（``RedisKeyRecordRedacted``），输出按
    模式聚合的 :class:`RedisPattern` 列表：

      - **聚类键**：``key_to_pattern(key_name)``——UUID/数字/日期/长 hex
        段占位后同形态键自动归并；
      - **count**：模式命中键数；
      - **no_ttl_ratio**：ttl_ms == -1（或 None 视同）的键占比——架构点名
        的"潜在泄漏信号"（会话键忘设 TTL 是缓存层头号事故源）；
      - **type_summary**：key_type → 数量（string/hash/list…分布）；
      - **ttl_summary**：TTL 分桶计数（见 :func:`_ttl_bucket`）；
      - **sample_keys**：该模式下的样例键 ≤3（按输入序取前 3，输入由
        SCAN 游标序提供；键名非敏感值，样例本身供人工复核定位，
        **值样例不在此处**——REQ-SU-015 AC2 的模式面天然不含任何键值）。

    输出序：按 (key_count 降序, pattern 升序)——高频模式在前，同频按
    模式名稳定排序（渲染幂等 §7.3 前提：无随机序）。

    Args:
        keys: SCAN 采集的键记录序列（可空）。

    Returns:
        list[RedisPattern]: 聚类结果（空输入 → 空列表）。
    """
    # 分组：pattern → 记录列表（dict 保持插入序，但输出显式排序，幂等有保障）
    groups: Dict[str, List[RedisKeyRecordRedacted]] = {}
    for record in keys:
        pattern = key_to_pattern(record.key_name)
        groups.setdefault(pattern, []).append(record)

    results: List[RedisPattern] = []
    for pattern, records in groups.items():
        count = len(records)
        # 无 TTL 占比：-1 与 None 都算"无 TTL"（上游 PTTL 缺采时 None 保守归泄漏面）
        no_ttl_count = sum(1 for r in records if r.ttl_ms is None or r.ttl_ms == -1)
        no_ttl_ratio = round(no_ttl_count / float(count), 6) if count else 0.0
        # 类型分布（Counter → 排序 dict，落库 JSON 稳定）
        type_counter: Counter = Counter(r.key_type or "unknown" for r in records)
        type_summary = RedactedDict(
            (k, int(v)) for k, v in sorted(type_counter.items()))
        # TTL 分桶分布
        bucket_counter: Counter = Counter(_ttl_bucket(r.ttl_ms) for r in records)
        ttl_summary = RedactedDict(
            (k, int(v)) for k, v in sorted(bucket_counter.items()))
        # 样例键 ≤3（输入序前 3；键名非敏感，schema 注明"脱敏后存样例"，
        # 键名通道无脱敏对象，原文保留）
        samples = [r.key_name for r in records[:SAMPLE_KEYS_LIMIT]]
        results.append(RedisPattern(
            pattern=pattern,
            key_count=count,
            no_ttl_ratio=no_ttl_ratio,
            ttl_summary=ttl_summary,
            type_summary=type_summary,
            sample_keys=samples,
        ))
    # 稳定输出序：数量降序 → 模式名升序
    results.sort(key=lambda p: (-p.key_count, p.pattern))
    return results


# ---------------------------------------------------------------------------
# 采集类（§2.3.9 RedisInspector——客户端经依赖注入，红线③守卫单通道）
# ---------------------------------------------------------------------------

# SCAN 每游标提示批大小（§2.3.9 口径 count=200；服务端可返回多于该数，
# 属 SCAN 语义——预算判定在客户端按"已采集键数"截断）
SCAN_COUNT = 200

# 值样例字节上限（NFR-SU-005：样例 ≤512B，redact 前置截断防超大值整读）
VALUE_SAMPLE_MAX_BYTES = 512

# 大 hash 降级阈值（REQ-SU-014.1：HLEN 超过该值弃 HGETALL 走 HSCAN 限量，
# 避免一次 HGETALL 拉取超大哈希阻塞服务端）
HASH_SCAN_THRESHOLD = 100
# HSCAN 降级取样的字段数上限（够看 shape 即可，不追求全量）
HASH_SCAN_FIELD_LIMIT = 50

# 集合/列表/有序集单类型样例读取条数（与 hash 限量同一保守口径）
CONTAINER_SAMPLE_LIMIT = 10
# 集合类样例序列化上限（SMEMBERS 无限量参数——超大集合整取的返回值在
# 序列化侧只取前 N 个成员，其余丢弃，防样例通道内存放大）
SERIALIZE_MEMBER_LIMIT = 10


class RedisInspector:
    """Redis 透镜采集类：SCAN 枚举 + 每键元数据/值样例 + 模式聚类落库。

    构造只存注入物，**不连接**（无 Redis 环境可自由构造做单测）；
    ``client`` 由编排层从 :class:`su.deps.DependencyReport` 取出 redis 模块
    建连后注入（软依赖红线：本模块绝不 ``import redis``）。

    红线③在本类的体现：**全部命令**（SCAN/TYPE/PTTL/OBJECT ENCODING/
    MEMORY USAGE/GET/HGETALL/HSCAN/LRANGE/SMEMBERS/ZRANGE）一律经
    :meth:`RedisGuard.call` 下发——本类不存在第二条命令路径；
    白名单外的任何命令（含 KEYS/CONFIG/MEMORY DOCTOR）会被守卫拒绝，
    单测以全量拒绝集断言（§11 test_su_redis_patterns）。::

        inspector = RedisInspector(redis_cfg, store, client,
                                   max_keys=cfg.budget.redis_max_keys)
        keys = inspector.scan_keys()
        report = inspector.collect()   # scan → cluster → 写 redis_keys/patterns
    """

    def __init__(
        self,
        cfg: RedisConfig,
        store: StateStore,
        client: Any,
        max_keys: int = 5000,
        guard: Optional[RedisGuard] = None,
    ) -> None:
        """初始化采集器（不连接、不发命令）。

        Args:
            cfg: Redis 配置段（key_allowlist 决定 SCAN MATCH 前缀集）。
            store: SQLite 状态机（redis_keys / redis_patterns 写入面）。
            client: redis 客户端对象（duck-typing；decode_responses 建议开启）。
            max_keys: 键采集预算（cfg.budget.redis_max_keys 传入；超限截断
                并在返回计数中可见，不静默丢弃）。
            guard: 复用的命令守卫（默认内部新建；命令出口唯一性不受影响）。

        Raises:
            ValueError: client 为 None（编排层降级判定遗漏时快速失败，
                绝不静默 mock——禁 mock 红线）。
        """
        if client is None:
            raise ValueError(
                "RedisInspector 需要 redis 客户端对象（经 DependencyReport "
                "建连注入）；None 意味着编排层降级判定遗漏——驱动缺失时应跳过 "
                "Redis 透镜并登记显式缺失声明（REQ-SU-021）"
            )
        self._cfg = cfg
        self._store = store
        self._client = client
        self._guard = guard if guard is not None else RedisGuard()
        try:
            self._max_keys = max(1, int(max_keys))
        except (TypeError, ValueError):
            self._max_keys = 5000

    # ------------------------------------------------------------------
    # SCAN 枚举与每键采集（REQ-SU-014）
    # ------------------------------------------------------------------

    def scan_keys(self) -> List[RedisKeyRecordRedacted]:
        """SCAN 游标枚举 + 每键元数据/值样例采集（白名单 MATCH 逐个）。

        流程（§2.3.9）：
          1. MATCH 模式集 = ``key_allowlist`` 各前缀补 ``*``；allowlist 为空
             时以单模式 ``*`` 全库扫描（配置面已声明"空=不限前缀"）；
          2. 每模式 ``SCAN cursor COUNT 200 MATCH <pattern>`` 循环至游标归 0
             （游标收敛判定用 int() 归一——redis-py 返回 int 或 bytes 形态）；
          3. 全局预算 ``max_keys``：达到即停止采集（**已采结果照常返回**，
             截断量经 scan_stats 可见）；同键多模式命中只采一次（内存去重）；
          4. 每键采集：TYPE / PTTL / OBJECT ENCODING / MEMORY USAGE / 值样例
             （按类型分通道，截 512B + :func:`config.redact`）。

        单键采集的容错口径：任一命令失败（键在扫描间隙过期、集群跨 slot 等）
        该字段记 None 继续——**键的元数据缺失不使整轮采集失败**；但守卫违例
        （SuRedisViolation）不在容错范围内：那是代码违例而非运行时噪声，
        必须原样上抛（红线③的"违例即停"语义）。

        Returns:
            list[RedisKeyRecordRedacted]: 键记录（SCAN 序，含已截断信号）。
        """
        patterns = ["{0}*".format(prefix) for prefix in self._cfg.key_allowlist] \
            or ["*"]
        seen: set = set()
        records: List[RedisKeyRecordRedacted] = []
        for pattern in patterns:
            cursor = 0
            while True:
                # SCAN 经守卫下发（游标/COUNT/MATCH 为参数，非命令名拼接）
                result = self._guard.call(
                    self._client, "SCAN", cursor,
                    "COUNT", SCAN_COUNT, "MATCH", pattern)
                cursor, batch = _unpack_scan(result)
                for key in batch:
                    key_name = _to_text(key)
                    if key_name in seen:
                        continue
                    seen.add(key_name)
                    records.append(self._collect_key(key_name))
                    if len(records) >= self._max_keys:
                        # 预算截断：内层立即收敛；外层模式循环经 break 标志收口
                        return records
                if cursor == 0:
                    break
        return records

    def _collect_key(self, key_name: str) -> RedisKeyRecordRedacted:
        """单键元数据 + 值样例采集（全部命令走守卫；单命令失败字段记 None）。

        Args:
            key_name: 键名（SCAN 所得原文）。

        Returns:
            RedisKeyRecordRedacted: 键记录（value_sample 已过 redact）。
        """
        key_type = self._try("TYPE", key_name)
        # PTTL：-1=无 TTL，-2=键已消失（间隙过期）；两者都原样保留供聚类判读
        ttl_ms = _to_int(self._try("PTTL", key_name))
        encoding = _to_text(self._try("OBJECT ENCODING", key_name))
        mem_bytes = _to_int(self._try("MEMORY USAGE", key_name))
        value_sample = self._sample_value(
            key_name, _to_text(key_type) or "unknown")
        return RedisKeyRecordRedacted(
            key_name=key_name,
            key_type=_to_text(key_type) or "unknown",
            ttl_ms=ttl_ms,
            encoding=encoding,
            mem_bytes=mem_bytes,
            value_sample=value_sample,
        )

    def _try(self, command: str, *args: Any) -> Any:
        """守卫命令 + 运行时容错包装（键间隙过期等噪声 → None）。

        SuRedisViolation（白名单违例）**不在容错范围**——违例原样上抛，
        保证红线③的违例绝不会被静默吞掉。

        Args:
            command: 命令名（∈ READONLY_COMMANDS，守卫兜底校验）。
            *args: 命令参数。

        Returns:
            Any: 命令返回值；运行时错误返回 None。
        """
        from su.dto import SuRedisViolation  # 局部导入：仅异常判定使用

        try:
            return self._guard.call(self._client, command, *args)
        except SuRedisViolation:
            raise  # 红线③：违例即停，绝不吞
        except Exception:  # noqa: BLE001 - 键过期/类型竞态等运行时噪声记 None
            return None

    def _sample_value(self, key_name: str, key_type: str) -> Optional[str]:
        """按键类型取样例文本（截 512B → :func:`config.redact` 出口）。

        分通道（全部命令 ∈ 白名单）：
          - string → GET；
          - hash → 先 HGETALL，返回长度超 :data:`HASH_SCAN_THRESHOLD` 时
            追加 HSCAN 限量 ≤50 字段作样例（REQ-SU-014.1 大 hash 样例面
            收敛；HLEN 不在白名单，分流判定在客户端侧做，见 _sample_hash）；
          - list → LRANGE 0 N-1；set → SMEMBERS；zset → ZRANGE 0 N-1
            （统一限量 :data:`CONTAINER_SAMPLE_LIMIT`；SMEMBERS 无限量参数，
            超大集合的返回值截断在序列化侧兜住）；
          - 其余类型（stream/json…）→ 无样例（None，不猜不 mock）。

        序列化文本先 UTF-8 字节截断（512B，多字节字符按字节安全丢弃），
        再过 redact——缓存值常含会话用户信息，脱敏是落库前最后闸门。

        Args:
            key_name: 键名。
            key_type: TYPE 结果小写文本。

        Returns:
            str | None: 已脱敏样例；无法取样返回 None。
        """
        raw: Any = None
        if key_type == "string":
            raw = self._try("GET", key_name)
        elif key_type == "hash":
            raw = self._sample_hash(key_name)
        elif key_type == "list":
            raw = self._try("LRANGE", key_name, 0, CONTAINER_SAMPLE_LIMIT - 1)
        elif key_type == "set":
            raw = self._try("SMEMBERS", key_name)
        elif key_type == "zset":
            raw = self._try("ZRANGE", key_name, 0, CONTAINER_SAMPLE_LIMIT - 1)
        else:
            return None
        if raw is None:
            return None
        text = _serialize_sample(raw)
        text = _truncate_utf8(text, VALUE_SAMPLE_MAX_BYTES)
        return redact({"v": text}).get("v")

    def _sample_hash(self, key_name: str) -> Any:
        """hash 取样：先 HGETALL 整取，按返回长度做**客户端侧**分流。

        红线③口径说明：架构的"大 hash 用 HLEN 分流"需要 HLEN 命令，但
        HLEN 不在只读采集白名单（READONLY_COMMANDS 为冻结 frozenset，
        扩展须走架构评审）。实现采用**严格保守等价路径**：无条件先
        HGETALL——返回长度 ≤ :data:`HASH_SCAN_THRESHOLD` 即直接作为样例；
        超过阈值才**追加 HSCAN 限量**取前 :data:`HASH_SCAN_FIELD_LIMIT`
        字段作为样例（样例面更小）。采集窗口内该键被并发写入导致两次
        读数不一致时，以 HSCAN 限量版为准（更晚观测、且限量面更小）。

        Args:
            key_name: 键名。

        Returns:
            Any: dict 形态样例（HGETALL 原样或 HSCAN 限量组装）或 None。
        """
        whole = self._try("HGETALL", key_name)
        if whole is None:
            return None
        if not isinstance(whole, dict) or len(whole) <= HASH_SCAN_THRESHOLD:
            return whole  # 小 hash（或非 dict 脏返回）：整取即样例
        # 大 hash：追加 HSCAN 限量（count 提示 + 客户端计数双限）
        fields: Dict[str, str] = {}
        cursor = 0
        while True:
            result = self._guard.call(
                self._client, "HSCAN", key_name, cursor,
                "COUNT", HASH_SCAN_FIELD_LIMIT)
            cursor, batch = _unpack_scan(result)
            # HSCAN 批次是 [f1,v1,f2,v2,...] 平铺数组
            for idx in range(0, len(batch) - 1, 2):
                fields[_to_text(batch[idx])] = _to_text(batch[idx + 1])
                if len(fields) >= HASH_SCAN_FIELD_LIMIT:
                    return fields
            if cursor == 0:
                break
        return fields

    # ------------------------------------------------------------------
    # 全量采集（REQ-SU-014 + 015 汇总入口）
    # ------------------------------------------------------------------

    def collect(self) -> RedactedDict:
        """SCAN → 逐键写 redis_keys → cluster_patterns → 写 redis_patterns。

        模式行落库走 :meth:`StateStore.replace_redis_patterns`（快照语义：
        先清全表再写本轮——聚类是当轮观测的汇总，跨轮合并无意义）。
        ``redis_patterns`` DDL 对齐口径：ttl_summary/type_summary/sample_keys
        以 JSON 文本入 TEXT 列（RedisPattern.to_dict 已序列化，
        replace_redis_patterns 对已是文本的值原样透传）。

        Returns:
            RedactedDict: 采集汇总 {keys_total, patterns_total, truncated}
            ——truncated=True 表示达到 max_keys 预算截断（报告 d 项声明源）。
        """
        records = self.scan_keys()
        for record in records:
            self._store.upsert_redis_key(record)
        patterns = cluster_patterns(records)
        self._store.replace_redis_patterns([p.to_dict() for p in patterns])
        return redact({
            "keys_total": len(records),
            "patterns_total": len(patterns),
            "truncated": len(records) >= self._max_keys,
        })


# ---------------------------------------------------------------------------
# RedisInspector 模块级辅助（duck-typing 容错与序列化）
# ---------------------------------------------------------------------------

def _unpack_scan(result: Any) -> Any:
    """SCAN/HSCAN 返回值 → ``(游标 int, 批次 list)``。

    redis-py 同步客户端返回 ``(int, list)``；个别兼容库（encode 形态）
    游标是 bytes——int() 归一失败视为 0（保守收敛，绝不死循环）。

    Args:
        result: 守卫下发的 SCAN/HSCAN 原始返回。

    Returns:
        tuple[int, list]: 游标与键/字段批次。
    """
    if not isinstance(result, (tuple, list)) or len(result) != 2:
        return (0, [])
    cursor_raw, batch = result
    try:
        cursor = int(cursor_raw)
    except (TypeError, ValueError):
        cursor = 0
    if not isinstance(batch, (list, tuple)):
        batch = []
    return (cursor, list(batch))


def _to_text(value: Any) -> Optional[str]:
    """任意返回 → 文本（bytes 按 UTF-8 解码，失败 hex 化；None 透传）。

    Args:
        value: 命令原始返回。

    Returns:
        str | None: 文本或 None。
    """
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    return str(value)


def _to_int(value: Any) -> Optional[int]:
    """任意返回 → int（bytes/str 容错；失败 None）。

    Args:
        value: 命令原始返回（PTTL/MEMORY USAGE 等数值命令）。

    Returns:
        int | None: 整数或 None。
    """
    if value is None:
        return None
    try:
        return int(_to_text(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _has_hlen(client: Any) -> bool:
    """客户端是否暴露 hlen 方法（**保留供未来白名单评审后使用，当前采集器不调用**）。

    **设计说明（红线③口径）**：HLEN 不在只读采集白名单
    :data:`su.redis_guard.READONLY_COMMANDS`（冻结 frozenset，扩展须走架构
    评审），采集器**绝不下发 HLEN**——大小 hash 分流改用 HGETALL 返回长度
    在客户端侧判定（见 :meth:`RedisInspector._sample_hash`）。本函数仅做
    纯属性探测（不发包、不经守卫），作为白名单未来扩充时的接线点保留。

    Args:
        client: redis 客户端对象。

    Returns:
        bool: 是否存在可调用 hlen。
    """
    return callable(getattr(client, "hlen", None))


def _serialize_sample(raw: Any) -> str:
    """样例值（标量/列表/dict）→ JSON 文本（非序列化对象 str() 兜底）。

    容器类返回值只序列化前 :data:`SERIALIZE_MEMBER_LIMIT` 个成员/字段
    （SMEMBERS 等命令无服务端限量参数——内存放大在序列化侧兜住），
    截断时追加 ``[+N more]`` 数量信号（计数本身非敏感）。

    Args:
        raw: 取样原始值。

    Returns:
        str: 序列化文本（键序稳定：sort_keys）。
    """
    limited: Any = raw
    if isinstance(raw, (list, tuple)) and len(raw) > SERIALIZE_MEMBER_LIMIT:
        limited = list(raw[:SERIALIZE_MEMBER_LIMIT]) + [
            "[+{0} more]".format(len(raw) - SERIALIZE_MEMBER_LIMIT)]
    elif isinstance(raw, dict) and len(raw) > SERIALIZE_MEMBER_LIMIT:
        items = sorted(raw.items(), key=lambda kv: str(kv[0]))
        limited = {str(k): v for k, v in items[:SERIALIZE_MEMBER_LIMIT]}
        limited["+more"] = "{0} more fields".format(len(raw) - SERIALIZE_MEMBER_LIMIT)
    elif isinstance(raw, (set, frozenset)) and len(raw) > SERIALIZE_MEMBER_LIMIT:
        limited = sorted(str(m) for m in raw)[:SERIALIZE_MEMBER_LIMIT] + [
            "[+{0} more]".format(len(raw) - SERIALIZE_MEMBER_LIMIT)]
    try:
        return json.dumps(limited, ensure_ascii=False, sort_keys=True,
                          default=str)
    except (TypeError, ValueError):
        return str(limited)


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """UTF-8 字节安全截断（多字节字符不劈半）。

    Args:
        text: 原文。
        max_bytes: 字节上限。

    Returns:
        str: 截断后文本（附加截断标记不占用预算外——在预算内预留 8B）。
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    cut = encoded[:max(0, max_bytes - 8)]
    # 逆向退到字符边界（UTF-8 续字节 0b10xxxxxx）
    while cut and (cut[-1] & 0xC0) == 0x80:
        cut = cut[:-1]
    if cut and (cut[-1] & 0x80):
        # 末字节是首字节（多字节字符被切开）——丢弃该首字节
        first_len = 0
        mask = cut[-1]
        for bit in (0x80, 0xE0, 0xF0, 0xF8):
            if not (mask & bit):
                break
            first_len += 1
        if first_len > 1:
            cut = cut[:-1]
    return cut.decode("utf-8", errors="ignore") + "…[truncated]"
