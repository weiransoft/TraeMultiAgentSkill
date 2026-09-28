"""确定性三角关联分析器——架构 ARCH-SU-001 §2.3.10，PRD REQ-SU-016/017。

职责边界（AP-1）：只出**确定性证据**（重合度/包含度数值 + 命中明细），
不出语义结论——结论由宿主 LLM 经 findings 回填。本模块从 SQLite 状态库
读采集事实（pages / api_observations / db_tables+db_columns /
redis_patterns），产出 :class:`su.dto.RelationRedacted` 供
``persist()`` 落 ``relations`` 表（UNIQUE(rtype,left_ref,right_ref) 幂等）。

零软依赖：只用标准库 sqlite3/json/re，读取经 StateStore 既有导出
（``export_understanding()``，全部 RedactedDict）——分析器自身不开
第二只读连接，避免绕过状态层直接查表。

三类关联（§2.3.10）：
  - :meth:`RelationAnalyzer.page_to_api`：api_observations.observed_on_pages
    直连分组；GET 与被拦截观测非 GET 分开标注（blocked_events 佐证）。
  - :meth:`RelationAnalyzer.api_to_table`：端点 path 段 ↔ 表名归一匹配
    （单复数、驼峰↔下划线、大小写）+ 响应 JSON 键 ↔ 列名重合度 ≥ 阈值。
  - :meth:`RelationAnalyzer.redis_to_entity`：键模式段 ↔ 表名/实体名 +
    值样例 JSON 键 ↔ 列名（REQ-SU-017）。
"""

import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from su.dto import RedactedDict, RelationRedacted
from su.state_store import StateStore

__all__ = ["RelationAnalyzer"]

# ---------------------------------------------------------------------------
# 名称归一（normalize_name）原语
# ---------------------------------------------------------------------------

# 驼峰拆词：小写段与大写段边界（'OrderItem' → ['Order','Item']；
# 'HTTPServer' → ['HTTP','Server']，连续大写按缩写词整体保留）
_CAMEL_SPLIT_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")
# 非字母数字分隔符（下划线/连字符/空格）
_WORD_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")

# 英文简单单复数还原表（§2.3.10 "常用单复数"口径——只做确定性最高的三条
# 规则，不做名词词典：orders→order / categories→category / boxes→box）。
# 保护清单：以 -ss/-us/-is 结尾的词不做 -s 剥离（status/address/canvas 剥离
# 后反而破坏匹配），双写尾缀（如 'logs' 源词 'log'）自然在 -s 规则内正确。
_SINGULAR_ES_SUFFIXES = ("ch", "sh", "s", "x", "z", "o")  # +es 复数族的词根尾缀


def _singularize(word: str) -> str:
    """单个小写英文词的简单单复数还原（确定性规则，无词典）。

    规则（依次尝试，先长后短防误剥）：
      1. ``ies`` → ``y``（categories→category，限长度 >4 防 'ies' 本身）；
      2. ``es`` 且词根尾缀 ∈ {ch,sh,s,x,z,o} → 去 ``es``（boxes→box、
         matches→match、heroes→hero）；'ses'（statuses）剥离 es 后残留
         尾缀 's' 再经规则 3 的保护检查止步——statuses 保持原形是
         可接受口径（表名列名同词归一后仍相等）；
      3. ``s`` 结尾且非 ``ss/us/is`` 双字母尾缀 → 去 ``s``（orders→order）；
      4. 其余原样（含不可数词、复数异形——确定性优先，宁不剥不误剥）。

    Args:
        word: 已小写的英文单词。

    Returns:
        str: 还原单数后的词。
    """
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("es"):
        stem = word[:-2]
        if stem.endswith(_SINGULAR_ES_SUFFIXES):
            return stem
    if len(word) > 2 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


class RelationAnalyzer:
    """三角关联证据生成器（§2.3.10 全量方法，AP-1 只出证据不出结论）。

    构造只依赖 StateStore（读取全部经 ``export_understanding()`` 唯一导出口，
    红线①：导出内容已全脱敏）。分析器无内部游标缓存——每个公开方法独立
    读取、独立产证据，可单方法单测。
    """

    def __init__(self, store: StateStore, key_overlap_threshold: float = 0.5) -> None:
        """初始化关联分析器。

        Args:
            store: 状态库（阶段 2 采集完成后才有意义；空库各方法返回空列表）。
            key_overlap_threshold: 响应 JSON 键 ↔ 列名重合度阈值
                （REQ-SU-016 AC1：≥ 阈值才产证据），默认 0.5。
        """
        self._store = store
        self._threshold = float(key_overlap_threshold)

    # ------------------------------------------------------------------
    # 名称归一（§2.3.10 静态方法）
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_name(name: str) -> str:
        """标识符归一：小写 + 驼峰↔下划线互转 + 简单英文单复数还原。

        归一目标：``orders`` / ``order`` / ``OrderItem`` ↔ ``order_item``
        等形态在 path 段、表名、列名三个命名域之间可比（§2.3.10）。

        步骤：
          1. 驼峰与分隔符统一拆词（``OrderItem``/``order_item``/``order-item``
             → ``['Order','Item']``，全小写）；
          2. 逐词单复数还原（:func:`_singularize`，确定性规则）；
          3. 下划线连接（``order_item``）。

        数字段保留原样（``v2`` → ``v2``）；空输入返回空串。

        Args:
            name: 原始标识符（表名/列名/path 段/键模式段）。

        Returns:
            str: 归一形态（小写、下划线分隔、单数）。
        """
        if not name:
            return ""
        # 先按非字母数字切大块，再对大块做驼峰拆词
        words: List[str] = []
        for chunk in _WORD_SPLIT_RE.split(str(name)):
            if not chunk:
                continue
            words.extend(m.lower() for m in _CAMEL_SPLIT_RE.findall(chunk))
        if not words:
            return ""
        return "_".join(_singularize(w) for w in words)

    # ------------------------------------------------------------------
    # ① page ↔ api（REQ-SU-016：观测直连）
    # ------------------------------------------------------------------

    def page_to_api(self) -> List[RelationRedacted]:
        """页面 ↔ API 直连证据（observed_on_pages 分组，score 恒 1.0 事实连）。

        口径：
          - 端点的 ``observed_on_pages`` 首元素 = 首次观测页（state_store
            聚合契约），页→端点直连的 score=1.0（观测事实，非推断）；
          - **GET 与被拦截观测非 GET 分开标注**：blocked_events 里
            kind='aborted_method' 的 url path 与端点 path 对齐后，证据里
            ``blocked_non_get`` 计数 +1（第 8 节"写请求观测清单"的关联侧）。

        Returns:
            list[RelationRedacted]: rtype='page_api'，
            left_ref='pages:<id>'，right_ref='api:<endpoint_id>'。
        """
        data = self._store.export_understanding()
        endpoints: List[RedactedDict] = list(data.get("endpoints") or [])
        blocked: List[RedactedDict] = list(data.get("blocked_events") or [])

        # 被拦截的非 GET 观测：url 存 path（query 已键名化），按 path 计数
        blocked_path_counts: Dict[str, int] = {}
        for event in blocked:
            if event.get("kind") != "aborted_method":
                continue
            path = self._path_only(str(event.get("url") or ""))
            if path:
                blocked_path_counts[path] = blocked_path_counts.get(path, 0) + 1

        relations: List[RelationRedacted] = []
        for endpoint in endpoints:
            endpoint_id = endpoint.get("endpoint_id")
            path = str(endpoint.get("url_path") or "")
            method = str(endpoint.get("method") or "").upper()
            blocked_count = blocked_path_counts.get(path, 0)
            pages_seen = endpoint.get("observed_on_pages") or []
            for page_id in pages_seen:
                evidence = RedactedDict({
                    "依据": "api_observations.observed_on_pages 直连观测",
                    "method": method,
                    "首次观测页": pages_seen[0] if pages_seen else page_id,
                    # GET 与"被拦截的非 GET"分开标注（REQ-SU-016：非 GET 只可能
                    # 来自 route 拦截观测，绝不会被执行）
                    "读请求": 1 if method == "GET" else 0,
                    "被拦截非GET次数": blocked_count,
                })
                relations.append(RelationRedacted(
                    rtype="page_api",
                    left_ref="pages:{0}".format(page_id),
                    right_ref="api:{0}".format(endpoint_id),
                    score=1.0,
                    evidence=evidence,
                ))
        return relations

    # ------------------------------------------------------------------
    # ② api ↔ table（REQ-SU-016：path↔表名 + 键重合度双通道）
    # ------------------------------------------------------------------

    def api_to_table(self) -> List[RelationRedacted]:
        """API ↔ 表确定性证据：path 段↔表名 ∪ 响应键↔列名重合度。

        双通道评分（证据并列记录，任一命中即产 Relation）：
          1. **命名通道**：端点 path 的业务段（剥离 api 前缀、版本段、
             数字/{id} 段）归一后与表名归一比对——相等 → 命名得分 1.0，
             仅末段单复数互含（'order' in 'order_item' 类前缀关系不计，
             只做精确相等，宁缺勿滥）；
          2. **重合度通道**：端点 response_shape 的顶层 JSON 键集合 ↔
             表列名集合，按 :meth:`normalize_name` 归一后计算
             ``|交| / |响应键|``，≥ key_overlap_threshold 产证据
             （分母取响应键数：响应键是"候选字段"侧，REQ-SU-016 口径）。

        最终 score = max(命名得分, 重合度得分)，两通道证据均完整落
        evidence_json（可解释，NFR-SU-008）。

        Returns:
            list[RelationRedacted]: rtype='api_table'，
            left_ref='api:<endpoint_id>'，right_ref='db_tables:<table_id>'。
        """
        data = self._store.export_understanding()
        endpoints: List[RedactedDict] = list(data.get("endpoints") or [])
        tables: List[RedactedDict] = list(data.get("db_tables") or [])
        if not endpoints or not tables:
            return []

        # 表索引：归一表名 → [(table_id, 列名归一集, 原始表名)]
        table_index: Dict[str, List[Tuple[Any, set, str]]] = {}
        for table in tables:
            normalized = self.normalize_name(str(table.get("name") or ""))
            columns = {
                self.normalize_name(str(col.get("name") or ""))
                for col in (table.get("columns") or [])
                if col.get("name")
            }
            table_index.setdefault(normalized, []).append(
                (table.get("table_id"), columns, str(table.get("name") or "")))

        relations: List[RelationRedacted] = []
        for endpoint in endpoints:
            endpoint_id = endpoint.get("endpoint_id")
            path_segments = self._business_segments(str(endpoint.get("url_path") or ""))
            response_keys = self._response_top_keys(endpoint.get("response_shape"))
            if not path_segments and not response_keys:
                continue
            normalized_response_keys = {self.normalize_name(k) for k in response_keys}

            # 候选表：命名通道命中 ∪ 重合度通道达标
            candidates: Dict[Any, RedactedDict] = {}
            for normalized, entries in table_index.items():
                for table_id, columns, raw_name in entries:
                    name_hit = normalized in path_segments
                    overlap, matched = self._key_overlap(
                        normalized_response_keys, columns)
                    if not name_hit and overlap < self._threshold:
                        continue
                    score = max(1.0 if name_hit else 0.0, overlap)
                    candidates[table_id] = RedactedDict({
                        "依据": self._api_table_basis(name_hit, overlap),
                        "表名": raw_name,
                        "path业务段": list(path_segments),
                        "path段命中表名": 1 if name_hit else 0,
                        "响应键数": len(normalized_response_keys),
                        "列名重合度": round(overlap, 4),
                        "重合键": sorted(matched),
                        "阈值": self._threshold,
                    })
            for table_id, evidence in sorted(
                    candidates.items(), key=lambda kv: str(kv[0])):
                relations.append(RelationRedacted(
                    rtype="api_table",
                    left_ref="api:{0}".format(endpoint_id),
                    right_ref="db_tables:{0}".format(table_id),
                    score=round(evidence_score(evidence,
                                               1.0 if evidence.get("path段命中表名") else 0.0), 4),
                    evidence=evidence,
                ))
        return relations

    # ------------------------------------------------------------------
    # ③ redis ↔ entity（REQ-SU-017：模式段↔表名 + 值键↔列名）
    # ------------------------------------------------------------------

    def redis_to_entity(self) -> List[RelationRedacted]:
        """Redis 模式 ↔ 实体（表）证据：模式段匹配 ∪ 值样例 JSON 键匹配。

        通道：
          1. **模式段通道**：redis_patterns.pattern 按 ``:``/``_`` 拆段，
             非占位符段归一后与归一表名精确相等 → 命名得分 1.0；
          2. **值样例通道**：该模式下各 redis_keys.value_sample 解析 JSON
             顶层键（非 JSON 跳过），与表列名归一重合度 ≥ 阈值 → 得分。

        pattern 与 keys 的关联用 key_to_pattern 复算（redis_keys 表无模式
        外键列，聚类是纯函数可重算——避免为此新增 schema）。

        Returns:
            list[RelationRedacted]: rtype='redis_entity'，
            left_ref='redis_pattern:<pattern_id>'，right_ref='db_tables:<id>'。
        """
        data = self._store.export_understanding()
        patterns: List[RedactedDict] = list(data.get("redis_patterns") or [])
        keys: List[RedactedDict] = list(data.get("redis_keys") or [])
        tables: List[RedactedDict] = list(data.get("db_tables") or [])
        if not patterns or not tables:
            return []

        # 延迟导入：redis_inspector 依赖 dto/state 无环，此处函数内导入
        # 保持模块级依赖最浅（采集层与透镜层解耦）
        from su.redis_inspector import key_to_pattern

        # 模式 → 该模式下的键记录（用纯函数复算归属，与 cluster 同口径）
        keys_by_pattern: Dict[str, List[RedactedDict]] = {}
        for record in keys:
            keys_by_pattern.setdefault(
                key_to_pattern(str(record.get("key_name") or "")), []).append(record)

        table_index: Dict[str, List[Tuple[Any, set, str]]] = {}
        for table in tables:
            normalized = self.normalize_name(str(table.get("name") or ""))
            columns = {
                self.normalize_name(str(col.get("name") or ""))
                for col in (table.get("columns") or [])
                if col.get("name")
            }
            table_index.setdefault(normalized, []).append(
                (table.get("table_id"), columns, str(table.get("name") or "")))

        relations: List[RelationRedacted] = []
        for pattern_row in patterns:
            pattern = str(pattern_row.get("pattern") or "")
            pattern_id = pattern_row.get("pattern_id")
            # 模式段：拆 : 与 _，丢弃占位符与短词（<2 字符噪声）
            segments = [
                self.normalize_name(seg)
                for seg in re.split(r"[:_]", pattern)
                if seg and seg not in ("{uuid}", "{n}", "{date}", "{hex}")
                and len(seg) >= 2
            ]
            # 该模式的值样例 JSON 顶层键全集
            value_keys: set = set()
            for record in keys_by_pattern.get(pattern, []):
                value_keys |= self._json_top_keys(record.get("value_sample"))
            normalized_value_keys = {self.normalize_name(k) for k in value_keys}

            candidates: Dict[Any, RedactedDict] = {}
            for normalized_table, entries in table_index.items():
                for table_id, columns, raw_name in entries:
                    name_hit = normalized_table in segments
                    overlap, matched = self._key_overlap(normalized_value_keys, columns)
                    if not name_hit and overlap < self._threshold:
                        continue
                    score = max(1.0 if name_hit else 0.0, overlap)
                    best_key = max(
                        candidates.get(table_id, RedactedDict()).get("重合度", 0.0)
                        if table_id in candidates else 0.0, score)
                    candidates[table_id] = RedactedDict({
                        "依据": self._redis_entity_basis(name_hit, overlap),
                        "表名": raw_name,
                        "模式": pattern,
                        "模式段命中表名": 1 if name_hit else 0,
                        "值样例键数": len(normalized_value_keys),
                        "重合度": round(overlap, 4),
                        "重合键": sorted(matched),
                        "阈值": self._threshold,
                        "综合得分": round(best_key, 4),
                    })
            for table_id, evidence in sorted(
                    candidates.items(), key=lambda kv: str(kv[0])):
                relations.append(RelationRedacted(
                    rtype="redis_entity",
                    left_ref="redis_pattern:{0}".format(pattern_id),
                    right_ref="db_tables:{0}".format(table_id),
                    score=float(evidence.get("综合得分", 0.0)),
                    evidence=evidence,
                ))
        return relations

    # ------------------------------------------------------------------
    # 落库（§2.3.10 persist：写 relations 表）
    # ------------------------------------------------------------------

    def persist(self, relations: Sequence[RelationRedacted]) -> int:
        """批量幂等落库（store.insert_relation：UNIQUE 冲突取 max(score)）。

        Args:
            relations: 三类分析器产出的 Relation DTO 序列。

        Returns:
            int: 提交条数（幂等冲突不报错，计数仍含该条）。
        """
        count = 0
        for relation in relations:
            self._store.insert_relation(relation)
            count += 1
        return count

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _path_only(url: str) -> str:
        """URL/路径 → 纯 path（blocked_events.url 已只存 path，此处兜底去 query）。"""
        return url.split("?", 1)[0].split("#", 1)[0]

    # path 中的非业务段（api 网关前缀、版本段）——业务段匹配的噪声源
    _PATH_NOISE_SEGMENTS = frozenset({"api", "v1", "v2", "v3"})
    # url_key 归一产物中的 id 占位段
    _PATH_ID_SEGMENTS = frozenset({"id", "0"})

    @classmethod
    def _business_segments(cls, url_path: str) -> List[str]:
        """端点 path → 业务段归一集合（匹配用）。

        剥离：前导/尾随斜杠、api/版本段（v1…）、数字段与 ``{id}`` 占位段、
        键名化残留（KEY）；其余段 :meth:`normalize_name` 归一。

        Args:
            url_path: api_observations.url_path（如 '/api/orders/{id}'）。

        Returns:
            list[str]: 归一业务段（如 ['order']）。
        """
        segments: List[str] = []
        for raw in cls._path_only(url_path).split("/"):
            if not raw:
                continue
            lowered = raw.lower().strip("{}")
            if lowered in cls._PATH_NOISE_SEGMENTS:
                continue
            if lowered in ("key",):       # query 键名化的 KEY 值段
                continue
            if lowered.isdigit() or lowered == "id":
                continue
            segments.append(cls.normalize_name(raw))
        return segments

    @staticmethod
    def _json_top_keys(text: Any) -> set:
        """值样例文本 → JSON 对象顶层键集合（非 JSON/非对象 → 空集）。

        Args:
            text: value_sample 文本（≤512B 已脱敏；被截断的 JSON 解析失败
                自然归空集，不产假证据）。

        Returns:
            set[str]: 顶层键原文集合。
        """
        if not isinstance(text, str) or not text:
            return set()
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return set()
        if isinstance(parsed, dict):
            return {str(k) for k in parsed.keys()}
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            # 列表形态取首元素键（数组是行的常见序列化形态）
            return {str(k) for k in parsed[0].keys()}
        return set()

    @classmethod
    def _response_top_keys(cls, response_shape: Any) -> set:
        """response_shape（RedactedDict shape）→ 顶层 JSON 键集合。

        shape 结构（§2.3.7）：JSON 响应为"键路径+类型+示例"递归形态——
        顶层键直接取 shape dict 中排除三要素保留键（status/content_type/
        kind 等元数据键）后的业务键；无法识别结构时返回空集。

        Args:
            response_shape: 端点的 response_shape（可 None）。

        Returns:
            set[str]: 顶层业务键原文集合。
        """
        if not isinstance(response_shape, dict):
            return set()
        # shape 元数据保留键（api_observer 产出侧的三要素字段名口径）
        meta_keys = {"status", "content_type", "content-type", "kind", "shape",
                     "type", "size", "sample", "example"}
        inner = response_shape.get("shape")
        source: Any = inner if isinstance(inner, dict) else response_shape
        if not isinstance(source, dict):
            return set()
        keys = set()
        for key in source.keys():
            key_str = str(key)
            if key_str.lower() in meta_keys:
                continue
            keys.add(key_str)
        return keys

    @staticmethod
    def _key_overlap(left: set, right: set) -> Tuple[float, set]:
        """重合度 = |left ∩ right| / |left|（left 为空 → 0.0）。

        Args:
            left: 候选键集合（响应键/值样例键，归一后）。
            right: 目标列名集合（归一后）。

        Returns:
            tuple[float, set]: (重合度, 命中键集合)。
        """
        if not left:
            return 0.0, set()
        matched = {k for k in left if k in right}
        return len(matched) / float(len(left)), matched

    @staticmethod
    def _api_table_basis(name_hit: bool, overlap: float) -> str:
        """api↔table 依据文本（中文可解释，NFR-SU-008）。"""
        if name_hit and overlap > 0:
            return "path段命中表名 且 响应键与列名重合度{0:.2f}（推断，需确认）".format(overlap)
        if name_hit:
            return "path段命中表名（推断，需确认）"
        return "响应键与列名重合度{0:.2f}达阈值（推断，需确认）".format(overlap)

    @staticmethod
    def _redis_entity_basis(name_hit: bool, overlap: float) -> str:
        """redis↔entity 依据文本（中文可解释）。"""
        if name_hit and overlap > 0:
            return "键模式段命中表名 且 值样例键与列名重合度{0:.2f}（推断，需确认）".format(overlap)
        if name_hit:
            return "键模式段命中表名（推断，需确认）"
        return "值样例JSON键与列名重合度{0:.2f}达阈值（推断，需确认）".format(overlap)


def evidence_score(evidence: RedactedDict, name_score: float) -> float:
    """从 evidence 里取综合得分（api_table 的 score = max(命名, 重合度)）。

    Args:
        evidence: 已组装的证据 dict。
        name_score: 命名通道得分（0 或 1）。

    Returns:
        float: 两通道最大值。
    """
    overlap = 0.0
    try:
        overlap = float(evidence.get("列名重合度") or 0.0)
    except (TypeError, ValueError):
        overlap = 0.0
    return max(name_score, overlap)
