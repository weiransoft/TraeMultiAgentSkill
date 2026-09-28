"""SU 文档渲染器（架构 ARCH-SU-001 §2.3.12 / §7，PRD REQ-SU-018）。

**职责**：把 SQLite 状态库渲染为全部对外产物——
  - ``UNDERSTANDING.md``：固定 10 节主文档（§7.1 数据来源映射表逐节落实）；
  - ``understanding.json``：结构化机读全量（:meth:`StateStore.export_understanding`
    数据源 + ``lenses`` 状态段 + ``findings_prompt`` 契约引用，§6.1）；
  - ``summary.json``：运行摘要（统计/预算/透镜完成度/confidence 分布）；
  - ``diagrams/navigation.mmd`` / ``diagrams/er.mmd``：Mermaid 源（内置正则自检）；
  - ``evidence/evidence-index.json``：证据编号 → 状态库记录映射（第 9 节数据源）。

**核心不变量**：
  1. 纯函数视图（AP-5）：渲染只读状态库，绝不写业务表；findings 对渲染器只读
     （入库唯一入口 = CLI 编排层 ``_phase_llm_bridge`` → replace_findings，§1.2）；
  2. 渲染幂等（REQ-SU-018 AC4 / §7.3）：查询全部经 export_understanding 的
     ORDER BY 主键序；时间戳统一取 ``run_meta.started_at``（run 级常量）；
     唯一每次渲染变化的内容 = 文档头部 ``generated_at`` 行；
  3. 缺失即声明（AP-3）：透镜 skipped/failed 时输出
     "未采集：<原因>（安装命令）"显式声明，**绝不以空数据/假数据冒充**；
  4. findings 未回填时第 5/7 节输出"待 LLM 语义回填"声明，不虚构结论；
  5. confidence=low 的结论渲染时加"⚠ 待人工确认"前缀（§6.2 规则 4）。

本模块**零软依赖顶层 import**（无 playwright/pymysql/psycopg2/redis），
可在任何纯标准库环境导入与运行（REQ-SU-021 环境无关性）。
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from su.config import SuConfig, redact
from su.dto import RedactedDict
from su.state_store import StateStore

__all__ = ["RenderOutcome", "DocumentRenderer"]

logger = logging.getLogger("su.renderer")

# ---------------------------------------------------------------------------
# 常量（§7.2 / §12 口径）
# ---------------------------------------------------------------------------

# 导航图节点折叠阈值：>60 节点时按 (深度桶, URL 首段) 分 subgraph（§7.2）
NAV_FOLD_THRESHOLD = 60
# 导航图每 subgraph 展开节点上限（其余折叠为 (+K) 计数节点，§7.2）
NAV_GROUP_LIMIT = 15
# ER 图表折叠阈值：>40 表时按表名前缀分 subgraph（§7.2）
ER_FOLD_THRESHOLD = 40
# ER 图单表属性（列）展示上限——超限注明"仅展示关键列"（PRD 卡片口径）
ER_COLUMN_LIMIT = 12
# 导航图节点标签 title 截断长度（§7.2：40 字符，`"` 转义）
NAV_LABEL_MAX = 40
# understanding.json / summary.json 的 schema 版本（§6.1）
UNDERSTANDING_SCHEMA_VERSION = 1

# 10 节固定标题（PRD REQ-SU-018 结构；顺序即文档顺序，AC1 章节断言基准）
SECTION_TITLES: Tuple[str, ...] = (
    "系统概览",
    "功能地图",
    "导航图",
    "数据模型",
    "UI ↔ 数据映射",
    "缓存与中间件",
    "业务规则汇编",
    "API 面",
    "证据附录",
    "未验证推断与未覆盖清单",
)

# 节序号 → 渲染方法名映射（下标 0 弃用，与 SECTION_TITLES 一一对应；
# render() 经 getattr 动态调度，方法名与 §7.1 映射表章节顺序一致）
SECTION_RENDERERS: Tuple[str, ...] = (
    "",                                # 占位：序号从 1 开始
    "_section_1_overview",             # 第 1 节 系统概览
    "_section_2_feature_map",          # 第 2 节 功能地图
    "_section_3_navigation",           # 第 3 节 导航图
    "_section_4_data_model",           # 第 4 节 数据模型
    "_section_5_ui_data_mapping",      # 第 5 节 UI ↔ 数据映射
    "_section_6_cache",                # 第 6 节 缓存与中间件
    "_section_7_business_rules",       # 第 7 节 业务规则汇编
    "_section_8_api_surface",          # 第 8 节 API 面
    "_section_9_evidence",             # 第 9 节 证据附录（延后渲染）
    "_section_10_uncovered",           # 第 10 节 未验证推断与未覆盖清单
)

# 第 5/7 节无 findings 时的显式声明（REQ-SU-020 AC3：骨架文档不虚构结论）
LLM_PENDING_NOTE = "待 LLM 语义回填（本运行未包含语义结论，请回填 findings 后重跑 --render-only）"

# ---------------------------------------------------------------------------
# Mermaid 内置正则自检（§7.2："生成后跑内置正则校验——括号配对、
# 关系行格式、标签引号转义"；AC2 mermaid-cli 为可选外部校验，不在依赖面）
# ---------------------------------------------------------------------------

# 成对符号表（导航图方括号 / ER 图实体块大括号；逐字符配对扫描）。
# 注意：`|`、`o`、`x`、`|` 形态的 ER **关系基数标记**（如 `||--o{` 行尾 `{`、
# `}o--||` 行首 `}`）不是块括号——由 `_ER_RELATION_RE` 关系行格式检查保证
# 其合法性，配对扫描前必须剔除，否则每个关系行都会产生伪"未闭合"报告。
_PAIR_OPEN = "[{"
_PAIR_CLOSE = "]}"
_PAIR_MAP = {"[": "]", "{": "}"}
# ER 关系基数标记 token（与 _ER_RELATION_RE / _ANY_CARDINALITY 保持同一全集）
_ER_CARDINALITY_TOKENS = (
    "||--o{", "}o--||", "||--||", "}o--|{", "|o--||", "||--|o",
    "|o--|{", "}o--|o", "||--}o", "}o--{x",
)

# 引号字符串 token（结构符配对检查专用，先于方括号剥离）：
#   - `".."` 双引号串：整段豁免（ER 属性注释、关系标签——标签内允许括号、
#     单引号等结构符，如 `"推断FK(包含度0.92) user_id->id"`）；
#   - `'..'` 单引号串：整段豁免（如标签文案内 pip install 'redis>=5.0.0'）。
_TOKEN_QUOTED_RE = re.compile(
    r'"(?:\\.|[^"\\])*"'             # 双引号串（转义安全）
    r"|'(?:\\.|[^'\\])*'"            # 单引号串（转义安全）
)
# 方括号节点标签 token（graph TD `P1["x"]` 剥离引号串后余 `P1[Q]`、裸 `[文本]`；
# 生成器保证标签内无裸 ]，非嵌套整段豁免）
_TOKEN_BRACKET_RE = re.compile(r"\[[^\]]*\]")
# erDiagram 关系行：`ENTITY ||--o{ ENTITY : "标签"`（§7.2 关系行格式）
_ER_RELATION_RE = re.compile(
    r'^[A-Za-z_][A-Za-z0-9_]*'
    r"\s+(?:\|\|--o\{|\}o--\|\||\|\|--\|\||\}o--\{x|\|o--\|\||\|--\|o|\|\|--\|o)"
    r"\s+[A-Za-z_][A-Za-z0-9_]*\s*:\s*\"(?:[^\"\\]|\\.)*\"\s*$"
)
# ER 实体块头：`ENTITY {`
_ER_BLOCK_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*\{$")
# ER 实体块尾：单独 `}`
_ER_BLOCK_END_RE = re.compile(r"^\}$")
# ER 属性行：`type name "注释"?`
_ER_ATTR_RE = re.compile(
    r'^[A-Za-z_][A-Za-z0-9_]*\s+[A-Za-z_][A-Za-z0-9_]*(?:\s+"(?:[^\"\\]|\\.)*")?\s*$'
)
# erDiagram 允许的关系基数 token 全集（注释行豁免，其余非块行必须命中关系行）
_ANY_CARDINALITY = ("||--o{", "}o--||", "||--||", "}o--|{", "|o--||", "||--|o", "|o--|{", "}o--|o", "||--}o")


def _strip_labels(line: str) -> str:
    """把 ``[\"标签\"]`` 形态 token 整体替换为占位符（标签内容豁免结构符检查）。

    Args:
        line: mermaid 源码单行。

    Returns:
        str: 标签置 ``[]`` 后的行——结构括号配对检查专用形态。
    """
    # 剥离顺序：引号串 → ER 关系基数标记 → 方括号节点标签。
    # 关系基数标记（如 `||--o{` 尾部 `{`）不是块括号，必须在配对扫描前剔除，
    # 否则每个 `||--o{` 关系行都会误报"'{' 未闭合"；其语法合法性由
    # _ER_RELATION_RE 关系行格式检查单独负责。
    line = _TOKEN_QUOTED_RE.sub("Q", line)
    for token in _ER_CARDINALITY_TOKENS:
        line = line.replace(token, "REL")
    return _TOKEN_BRACKET_RE.sub("[]", line)


def _iter_code_lines(text: str):
    """迭代 mermaid 源码的**代码行**（``(行号, 行)``）。

    注释（``%%`` 前缀）与空行豁免——注释不参与语法判定。

    Args:
        text: mermaid 源码全文。

    Yields:
        tuple[int, str]: (1-based 行号, 去除行尾空白后的行)。
    """
    for idx, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("%%"):
            continue
        yield idx, line


def _check_bracket_balance(text: str) -> List[str]:
    """括号配对自检（剔除标签后逐字符扫描，支持跨行配对）。

    Args:
        text: mermaid 源码全文。

    Returns:
        list[str]: 中文错误列表（空=通过）。
    """
    problems: List[str] = []
    stack: List[Tuple[str, int]] = []
    for line_no, line in _iter_code_lines(text):
        stripped = _strip_labels(line)
        for ch in stripped:
            if ch in _PAIR_OPEN:
                stack.append((ch, line_no))
            elif ch in _PAIR_CLOSE:
                if not stack:
                    problems.append("第 {0} 行：多余的闭合括号 '{1}'".format(line_no, ch))
                else:
                    opener, opener_line = stack.pop()
                    if _PAIR_MAP[opener] != ch:
                        problems.append(
                            "第 {0} 行：'{1}' 与第 {2} 行的 '{3}' 不配对".format(
                                line_no, ch, opener_line, opener))
    for opener, opener_line in stack:
        problems.append("第 {0} 行：'{1}' 未闭合".format(opener_line, opener))
    return problems


def _check_quoted_line_balance(text: str) -> List[str]:
    """引号转义自检：剔除标签内转义引号后，每行 ``\"`` 计数必须为偶数。

    实现口径：先把标签内 ``\\"`` 转义对剔除，再要求每代码行的裸引号成对
    ——未闭合引号会使后续所有标签解析错位（§7.2 标签引号转义检查）。

    Args:
        text: mermaid 源码全文。

    Returns:
        list[str]: 中文错误列表（空=通过）。
    """
    problems: List[str] = []
    for line_no, line in _iter_code_lines(text):
        # 剔除成对转义引号（\\"）后统计裸引号
        without_escaped = line.replace('\\"', "")
        if without_escaped.count('"') % 2 != 0:
            problems.append("第 {0} 行：引号未成对闭合".format(line_no))
    return problems


def _check_graph_td(text: str) -> List[str]:
    """导航图（graph TD）语法自检：首行声明 + 括号配对 + 引号成对。

    Args:
        text: navigation.mmd 全文。

    Returns:
        list[str]: 中文错误列表（空=通过）。
    """
    problems: List[str] = []
    first = next((ln for _, ln in _iter_code_lines(text)), "")
    if not first.startswith("graph TD"):
        problems.append("导航图首行必须是 'graph TD'，实际：{0!r}".format(first))
    problems.extend(_check_bracket_balance(text))
    problems.extend(_check_quoted_line_balance(text))
    return problems


def _check_er_diagram(text: str) -> List[str]:
    """ER 图（erDiagram）语法自检：首行声明 + 大括号配对 + 关系/属性行格式。

    关系行必须命中 :data:`_ER_RELATION_RE`（保证"推断FK(包含度x.xx)"这类
    标签以带引号关系行形态出现，REQ-SU-018 AC2 可正则识别"推断"字样）。

    Args:
        text: er.mmd 全文。

    Returns:
        list[str]: 中文错误列表（空=通过）。
    """
    problems: List[str] = []
    lines = list(_iter_code_lines(text))
    if not lines:
        return ["erDiagram 源为空"]
    if not lines[0][1].startswith("erDiagram"):
        problems.append("ER 图首行必须是 'erDiagram'，实际：{0!r}".format(lines[0][1]))
    problems.extend(_check_bracket_balance(text))
    problems.extend(_check_quoted_line_balance(text))
    # 逐行分类：实体块内（属性行）/ 块外（关系行）。subgraph 行与 end 行豁免。
    in_block = False
    for line_no, line in lines[1:]:
        if line.startswith("subgraph") or line == "end":
            continue
        if in_block:
            if _ER_BLOCK_END_RE.match(line):
                in_block = False
            elif not _ER_ATTR_RE.match(line):
                problems.append("第 {0} 行：实体块内属性行格式非法：{1!r}".format(line_no, line))
        elif _ER_BLOCK_RE.match(line):
            in_block = True
        elif _ER_RELATION_RE.match(line):
            continue
        else:
            problems.append("第 {0} 行：erDiagram 关系行格式非法：{1!r}".format(line_no, line))
    return problems


def _validate_mermaid(kind: str, text: str) -> None:
    """按图类型跑内置正则自检，失败即抛 RuntimeError（渲染失败必须显式暴露）。

    Args:
        kind: 'navigation' / 'er'。
        text: mermaid 源码全文。

    Raises:
        RuntimeError: 自检未通过（中文列出全部语法问题）。
    """
    if kind == "navigation":
        problems = _check_graph_td(text)
    else:
        problems = _check_er_diagram(text)
    if problems:
        raise RuntimeError("Mermaid {0} 图自检未通过：{1}".format(kind, "；".join(problems)))


# ---------------------------------------------------------------------------
# 通用文本/标识符工具
# ---------------------------------------------------------------------------

def _nav_escape(text: str) -> str:
    """导航图标签转义：反斜杠与双引号转义、换行压平（§7.2 标签转义口径）。

    Args:
        text: 原始标签文本。

    Returns:
        str: 可安全置于 ``[\"...\"]`` 内的文本。
    """
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _nav_label(text: str, limit: int = NAV_LABEL_MAX) -> str:
    """标签截断 + 转义（title 缺失时回空串由调用方兜底 url_key）。

    Args:
        text: 原始文本。
        limit: 截断长度（§7.2：40 字符）。

    Returns:
        str: 截断并转义后的标签文本。
    """
    text = (text or "").strip()
    if len(text) > limit:
        text = text[:limit]
    return _nav_escape(text)


def _md_cell(value: Any) -> str:
    """表格单元格安全化：管道符转义、换行压平、None → '-'。

    Args:
        value: 单元格原值。

    Returns:
        str: Markdown 表格安全文本。
    """
    if value is None:
        return "-"
    text = str(value).replace("|", "\\|").replace("\n", " ")
    return text or "-"


def _er_identifier(name: str) -> str:
    """ER 实体标识符净化：非 [A-Za-z0-9_] 一律替换为下划线（Mermaid 语法要求）。

    Args:
        name: 原始表名（可能含 schema 限定与点号）。

    Returns:
        str: 合法 erDiagram 标识符（与原表名一一对应，纯函数幂等）。
    """
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name)
    # 必须以字母/下划线开头（数字开头表名兜底加前缀）
    if cleaned and cleaned[0].isdigit():
        cleaned = "t_" + cleaned
    return cleaned or "unnamed"


def _first_path_segment(url_key_value: str) -> str:
    """url_key → URL 首段路径（subgraph 聚类键，§7.2）。

    形态说明：url_key 归一产物为 ``scheme://host/path…``（含协议）。取 host 之后
    的第一个非空路径段；无路径段归 ``(root)``。

    Args:
        url_key_value: pages.url_key。

    Returns:
        str: 首段路径或 ``(root)``。
    """
    without_scheme = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", url_key_value or "")
    _host, slash, rest = without_scheme.partition("/")
    if not slash:
        return "(root)"
    # hash 路由形态：path 为空时取 #!/#/ 之后首段
    if not rest:
        hash_part = re.sub(r"^#?!?", "", _host)
        rest = hash_part
    for seg in rest.replace("#", "/").split("/"):
        if seg:
            return seg
    return "(root)"


def _table_prefix(table_name: str) -> str:
    """表名 → 聚类前缀（首个下划线段；无下划线取全名，§7.2 ER 聚类口径）。

    Args:
        table_name: 表名（不含 schema）。

    Returns:
        str: 前缀（小写）。
    """
    base = table_name.split(".")[-1]
    prefix = base.split("_", 1)[0].lower()
    return prefix or "(other)"


def _format_ts(value: Optional[float]) -> str:
    """unix 秒 → 本地可读时间文本（None → '-'；渲染用，非幂等风险源——
    所有输入均为 run 级常量 started_at）。

    Args:
        value: unix 时间戳秒。

    Returns:
        str: ``YYYY-MM-DD HH:MM:SS`` 形态文本。
    """
    if value is None:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))


# ---------------------------------------------------------------------------
# 渲染出参
# ---------------------------------------------------------------------------

@dataclass
class RenderOutcome:
    """渲染结果汇总（CLI 编排层据此打印摘要 / 断言产物存在）。

    Attributes:
        files: 实际写出的产物路径清单（相对输出根目录，排序稳定）。
        stats: 状态库统计快照（store.stats() 直传，summary.json 同源）。
        mermaid_ok: 两份 .mmd 是否全部通过内置正则自检（写盘前强制自检，
            失败会直接抛错——本字段恒 True 属留档断言）。
    """

    files: List[str] = field(default_factory=list)
    stats: RedactedDict = field(default_factory=RedactedDict)
    mermaid_ok: bool = True

    def to_redacted(self) -> RedactedDict:
        """转已脱敏 dict（summary.json 的 render 段）。

        Returns:
            RedactedDict: 渲染摘要（全为路径与计数，无外部敏感数据）。
        """
        return redact({
            "files": list(self.files),
            "mermaid_ok": self.mermaid_ok,
            "stats": dict(self.stats),
        })


# ---------------------------------------------------------------------------
# 渲染器
# ---------------------------------------------------------------------------

class DocumentRenderer:
    """状态库 → 全部产物的纯函数渲染器（§2.3.12 / §7）。

    典型用法（CLI 编排层阶段 5）::

        renderer = DocumentRenderer(store, cfg)
        outcome = renderer.render()

    透镜状态（ui/api/db/redis 的 collected/skipped/failed + 中文 skip_reason）
    由编排层经 :meth:`set_lens_status` 注入（AP-3：渲染器不猜测缺失原因，
    只渲染被登记的事实）。
    """

    def __init__(self, store: StateStore, cfg: SuConfig) -> None:
        """初始化渲染器。

        Args:
            store: 状态库（渲染期间只读——findings 只读不写，§1.2 边界规则 2）。
            cfg: 完整运行配置（输出目录 / budget 参数，run_meta.config_snapshot
                为文档内展示口径，cfg 仅作渲染参数兜底）。
        """
        self._store = store
        self._cfg = cfg
        # 透镜状态登记表：lens 名 → {status, skip_reason}（编排层注入）
        self._lenses: Dict[str, Dict[str, Optional[str]]] = {}
        # 单次 render() 的 understanding 导出缓存（同次渲染零重复查询）
        self._understanding: Optional[RedactedDict] = None
        # 证据索引条目（第 9 节与 evidence-index.json 同源，render 内重建）
        self._evidence_index: List[RedactedDict] = []
        # build_navigation_mermaid 折叠端点映射（url_key → 计数节点 id；
        # 方法入口重置、出口清空，实例状态仅单方法生命周期内存活）
        self._endpoint_decls: Dict[str, str] = {}
        # 输出根目录 = <out_dir>/<system_id>（§5.5 文件系统边界）
        self._root = Path(cfg.out_dir) / cfg.system_id

    # ------------------------------------------------------------------
    # 透镜状态登记（编排层 → 渲染器唯一的状态注入口）
    # ------------------------------------------------------------------

    def set_lens_status(self, lens: str, status: str, skip_reason: Optional[str] = None) -> None:
        """登记单个透镜的采集状态（collected/skipped/failed）。

        Args:
            lens: 透镜名（'ui' / 'api' / 'db' / 'redis'）。
            status: 'collected' / 'skipped' / 'failed'（§6.1 枚举）。
            skip_reason: 非 collected 时**必须**提供中文原因（含安装命令，
                §6.1 硬约束）；collected 时忽略。

        Raises:
            ValueError: status 非法，或非 collected 且缺 skip_reason
                （缺失即声明红线——不允许无原因的降级状态进入文档）。
        """
        if status not in ("collected", "skipped", "failed"):
            raise ValueError("透镜状态非法：{0}（合法 collected/skipped/failed）".format(status))
        if status != "collected" and not (skip_reason or "").strip():
            raise ValueError(
                "透镜 {0} 状态为 {1} 时必须提供中文 skip_reason（AP-3 缺失即声明）".format(
                    lens, status))
        self._lenses[lens] = {
            "status": status,
            "skip_reason": skip_reason if status != "collected" else None,
        }

    def get_lens_status(self, lens: str) -> Dict[str, Optional[str]]:
        """读取透镜状态（未登记时保守返回 failed + 未登记声明）。

        默认口径选择 failed 而非 collected：渲染器绝不把"未登记"冒充
        "已采集"（AP-3）；正常流水线中编排层总会为四透镜逐一登记。

        Args:
            lens: 透镜名。

        Returns:
            dict: {status, skip_reason}。
        """
        if lens in self._lenses:
            return dict(self._lenses[lens])
        return {"status": "failed", "skip_reason": "透镜状态未登记（编排层未报告采集结果）"}

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def render(self) -> RenderOutcome:
        """渲染全部产物（幂等：同一状态库二次渲染除头部时间行外逐字节稳定）。

        流程：一次性导出 understanding 数据 → 渲染 10 节 Markdown →
        写 understanding.json（export + lenses + findings_prompt）→
        写 summary.json → 生成并自检两份 Mermaid → 写 evidence-index.json。
        全部 JSON 落盘统一 ``sort_keys=True``（字典序稳定）+ 时间戳取
        run 级常量（§7.3 幂等策略）。

        Returns:
            RenderOutcome: 产物清单 + 统计快照。

        Raises:
            RuntimeError: Mermaid 自检未通过（语法错误必须显式失败，不产出坏图）。
        """
        self._root.mkdir(parents=True, exist_ok=True)
        self._understanding = self._store.export_understanding()
        started_at = self._run_started_at()
        outcome = RenderOutcome(stats=self._store.stats())

        # 1) UNDERSTANDING.md（头部时间行 = 唯一每次渲染可变内容）
        #    注意顺序：先渲染除第 9 节外的全部章节（_evidence_index 在渲染
        #    过程中累加），最后渲染第 9 节——证据附录必须引用完整索引。
        md_lines: List[str] = ["# 系统功能理解文档（{0}）".format(self._cfg.system_id)]
        md_lines.append("")
        md_lines.append("<!-- generated_at: {0:.6f} -->".format(time.time()))
        md_lines.append("")
        body_sections: List[str] = []
        for idx, title in enumerate(SECTION_TITLES, start=1):
            if idx == 9:
                continue  # 第 9 节延后渲染（证据索引需先由其余章节填充完整）
            renderer = getattr(self, SECTION_RENDERERS[idx])
            body_sections.append("## {0}. {1}\n\n{2}".format(
                idx, title, renderer().rstrip()))
        section_9 = "## 9. {0}\n\n{1}".format(
            SECTION_TITLES[8], self._section_9_evidence().rstrip())
        for chunk in body_sections[:8]:
            md_lines.append(chunk)
            md_lines.append("")
        md_lines.append(section_9)
        md_lines.append("")
        md_lines.append(body_sections[8])  # 第 10 节
        md_lines.append("")
        md_path = self._root / "UNDERSTANDING.md"
        md_path.write_text("\n".join(md_lines), encoding="utf-8")
        outcome.files.append("UNDERSTANDING.md")

        # 2) understanding.json（export_understanding + lenses + findings_prompt，§6.1）
        understanding = RedactedDict(dict(self._understanding))
        understanding["lenses"] = self._lenses_section()
        understanding["findings_prompt"] = redact({
            "instructions_ref": "docs/spec/role-prompts/su-llm-backfill.md",
            "output_contract": (
                "findings 数组写入本文件 findings 段后重跑 --render-only；"
                "每条含 claim/kind/confidence/evidence_refs(≥1)/status=proposed"),
        })
        understanding_out = redact({
            "meta": {
                "system_id": self._cfg.system_id,
                "run_id": (self._understanding.get("meta") or {}).get("run_id"),
                "schema_version": UNDERSTANDING_SCHEMA_VERSION,
                "run_status": (self._understanding.get("meta") or {}).get("run_status"),
                "started_at": started_at,
                "config_snapshot": (self._understanding.get("meta") or {}).get("config_snapshot") or {},
            },
            "pages": self._understanding.get("pages") or [],
            "edges": self._understanding.get("edges") or [],
            "endpoints": self._understanding.get("endpoints") or [],
            "db_tables": self._understanding.get("db_tables") or [],
            "implicit_fk_candidates": self._understanding.get("implicit_fk_candidates") or [],
            "redis_patterns": self._understanding.get("redis_patterns") or [],
            "redis_keys": self._understanding.get("redis_keys") or [],
            "relations": self._understanding.get("relations") or [],
            "findings": self._understanding.get("findings") or [],
            "blocked_events": self._understanding.get("blocked_events") or [],
            "lenses": understanding["lenses"],
            "findings_prompt": understanding["findings_prompt"],
        })
        with (self._root / "understanding.json").open("w", encoding="utf-8") as fh:
            json.dump(understanding_out, fh, ensure_ascii=False, sort_keys=True, indent=2)
        outcome.files.append("understanding.json")

        # 3) summary.json（预算消耗 + 透镜完成度 + confidence 分布，NFR-SU-006）
        budget_snapshot = (self._understanding.get("meta") or {}).get("config_snapshot") or {}
        summary = redact({
            "system_id": self._cfg.system_id,
            "run_id": (self._understanding.get("meta") or {}).get("run_id"),
            "started_at": started_at,
            "stats": dict(self._store.stats()),
            "lenses": {name: self.get_lens_status(name) for name in ("ui", "api", "db", "redis")},
            "confidence_distribution": self._confidence_distribution(),
            "budget": {
                "max_pages": budget_snapshot.get("max_pages"),
                "max_depth": budget_snapshot.get("max_depth"),
                "max_actions_per_page": budget_snapshot.get("max_actions_per_page"),
                "time_budget_minutes": budget_snapshot.get("time_budget_minutes"),
            },
            "run_exit_reason": (self._understanding.get("meta") or {}).get("exit_reason"),
        })
        with (self._root / "summary.json").open("w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, sort_keys=True, indent=2)
        outcome.files.append("summary.json")

        # 4) diagrams/（先自检后写盘——坏图绝不落盘，§7.2）
        diagrams_dir = self._root / "diagrams"
        diagrams_dir.mkdir(parents=True, exist_ok=True)
        nav_mmd = self.build_navigation_mermaid()
        _validate_mermaid("navigation", nav_mmd)
        er_mmd = self.build_er_mermaid()
        _validate_mermaid("er", er_mmd)
        (diagrams_dir / "navigation.mmd").write_text(nav_mmd + "\n", encoding="utf-8")
        (diagrams_dir / "er.mmd").write_text(er_mmd + "\n", encoding="utf-8")
        outcome.files.extend(["diagrams/navigation.mmd", "diagrams/er.mmd"])

        # 5) evidence/evidence-index.json（第 9 节同源，编号稳定可 diff）
        evidence_dir = self._root / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_payload = redact({
            "system_id": self._cfg.system_id,
            "state_db": "state/understanding.sqlite",
            "started_at": started_at,
            "entries": [dict(e) for e in self._evidence_index],
        })
        with (evidence_dir / "evidence-index.json").open("w", encoding="utf-8") as fh:
            json.dump(evidence_payload, fh, ensure_ascii=False, sort_keys=True, indent=2)
        outcome.files.append("evidence/evidence-index.json")

        outcome.files.sort()
        logger.info("渲染完成：%s 个产物 → %s", len(outcome.files), str(self._root))
        return outcome

    # ------------------------------------------------------------------
    # 第 1 节 系统概览
    # ------------------------------------------------------------------

    def _section_1_overview(self) -> str:
        """系统概览（§7.1：config_snapshot + 技术栈指纹 + 预检 + 透镜完成度）。"""
        meta = self._understanding.get("meta") or {}
        snapshot = meta.get("config_snapshot") or {}
        lines: List[str] = []
        lines.append("- 系统标识：`{0}`".format(self._cfg.system_id))
        lines.append("- 目标入口：`{0}`".format(_md_cell(snapshot.get("base_url"))))
        lines.append("- run 状态：{0}（started_at：{1}）".format(
            _md_cell(meta.get("run_status")), _format_ts(meta.get("started_at"))))
        lines.append("- 配置快照（已脱敏，run_meta.config_snapshot）：")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2))
        lines.append("```")
        lines.append("")
        # 技术栈指纹（pages.tech_fingerprint 去重聚合，主键序）
        fingerprints = sorted({
            str(p.get("tech_fingerprint"))
            for p in self._pages()
            if p.get("tech_fingerprint")
        })
        lines.append("### 技术栈指纹观察")
        lines.append("")
        if fingerprints:
            for fp in fingerprints:
                lines.append("- `{0}`".format(_md_cell(fp)))
        elif self.get_lens_status("ui")["status"] == "collected":
            lines.append("- 未观测到 server/HTML 技术栈特征（采集完成但指纹为空）")
        else:
            lines.append("- 未采集：{0}".format(self._lens_declare("ui")))
        lines.append("")
        lines.append("### 预检摘要（state/preflight.json）")
        lines.append("")
        lines.extend(self._preflight_lines())
        lines.append("")
        lines.append("### 透镜完成度")
        lines.append("")
        lines.append("| 透镜 | 状态 | 说明 |")
        lines.append("|---|---|---|")
        for lens in ("ui", "api", "db", "redis"):
            st = self.get_lens_status(lens)
            lines.append("| {0} | {1} | {2} |".format(
                lens, st["status"], _md_cell(st.get("skip_reason") or "已采集")))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 第 2 节 功能地图
    # ------------------------------------------------------------------

    def _section_2_feature_map(self) -> str:
        """功能地图（§7.1：pages 按 depth/discover_from 树 + page_actions 分级标注）。"""
        ui = self.get_lens_status("ui")
        if ui["status"] != "collected":
            return "- UI 未采集：{0}".format(self._lens_declare("ui"))
        pages = self._pages()
        if not pages:
            return "- UI 采集完成但未发现任何页面（入口页即失败时属预期，详见第 10 节）"
        # BFS 树（幂等：children 按主键序排序，DFS 序稳定）
        children: Dict[int, List[RedactedDict]] = {}
        roots: List[RedactedDict] = []
        by_id: Dict[int, RedactedDict] = {int(p["page_id"]): p for p in pages}
        for page in pages:
            parent = page.get("discover_from")
            if parent is not None and int(parent) in by_id:
                children.setdefault(int(parent), []).append(page)
            else:
                roots.append(page)
        for group in children.values():
            group.sort(key=lambda p: int(p["page_id"]))
        lines: List[str] = ["```text"]
        for root_page in sorted(roots, key=lambda p: int(p["page_id"])):
            self._walk_feature_tree(root_page, children, 0, lines)
        lines.append("```")
        return "\n".join(lines)

    def _walk_feature_tree(
        self,
        page: RedactedDict,
        children: Dict[int, List[RedactedDict]],
        depth: int,
        lines: List[str],
    ) -> None:
        """递归渲染功能树单节点（DFS 序 = 主键序 children，幂等）。

        Args:
            page: 当前页节点。
            children: 父 page_id → 子页列表。
            depth: 缩进层级。
            lines: 输出行累加器（原地修改）。
        """
        page_id = int(page["page_id"])
        title = _md_cell(page.get("title") or page.get("url_key"))
        lines.append("{0}- [P{1}] {2}（深度 {3}，状态 {4}）".format(
            "  " * depth, page_id, title, page.get("depth"), page.get("status")))
        actions = sorted(
            page.get("actions") or [], key=lambda a: int(a.get("action_id", 0)))
        for action in actions[:10]:
            lines.append("{0}  - 动作#{1} [{2}] 规则 `{3}`{4}".format(
                "  " * depth, action.get("action_id"), action.get("tier"),
                action.get("rule_name"),
                "（已执行）" if action.get("executed") else "（仅记录）"))
        if len(actions) > 10:
            lines.append("{0}  - …（其余 {1} 个候选动作见状态库 page_actions 表）".format(
                "  " * depth, len(actions) - 10))
        for child in children.get(page_id, []):
            self._walk_feature_tree(child, children, depth + 1, lines)

    # ------------------------------------------------------------------
    # 第 3 节 导航图
    # ------------------------------------------------------------------

    def _section_3_navigation(self) -> str:
        """导航图（§7.1：edges+pages → Mermaid graph TD + 孤点清单）。"""
        ui = self.get_lens_status("ui")
        if ui["status"] != "collected":
            return "- UI 未采集：{0}".format(self._lens_declare("ui"))
        nav_mmd = self.build_navigation_mermaid()
        lines = [
            "Mermaid 源同步落盘 `diagrams/navigation.mmd`（生成时已通过内置正则自检）。",
            "",
            "```mermaid",
            nav_mmd,
            "```",
            "",
            "### 孤点清单（无出入边页面，REQ-SU-018 第 3 节）",
            "",
        ]
        orphans = self._orphan_pages()
        if orphans:
            for page in orphans:
                lines.append("- [P{0}] `{1}`（{2}）".format(
                    page.get("page_id"), _md_cell(page.get("url_key")),
                    _md_cell(page.get("title") or "无标题")))
        else:
            lines.append("- 无孤点页面")
        return "\n".join(lines)

    def _orphan_pages(self) -> List[RedactedDict]:
        """孤点页面列表（既非任何边端点、且自身无进出边；主键序，幂等）。

        Returns:
            list[RedactedDict]: 孤点页节点。
        """
        edge_keys: set = set()
        for edge in self._edges():
            edge_keys.add(str(edge.get("from_key")))
            edge_keys.add(str(edge.get("to_key")))
        return [p for p in self._pages() if str(p.get("url_key")) not in edge_keys]

    def build_navigation_mermaid(self) -> str:
        """生成导航图 Mermaid（§2.3.12 / §7.2）。

        规则：
          - ``graph TD``；节点 id=``P<page_id>``，标签=title 截断 40 字符转义；
          - 边=edges 按 (from,to) 去重（同一对页面多条边只画一条，边序=主键序）；
          - 节点数 >60 时按 (深度桶, URL 首段) 分 subgraph，每组按主键序取前
            15 节点、其余折叠为 ``(…+K 节点未展示)`` 计数节点；
            跨组边全部保留（§7.2 统一折叠规则：不做跨组边过滤）；
          - 端点不在已渲染节点集内的 url_key（frontier 未采页面）折叠进
            目标组的计数节点（其 id 未声明、不可作画线端点）。

        Returns:
            str: mermaid 源码（不含尾部换行；调用方负责自检与写盘）。
        """
        pages = self._pages()
        edges = self._edges()
        url_to_page = {str(p.get("url_key")): p for p in pages}
        # 折叠端点映射每次重建（幂等 + 无跨调用残留）
        self._endpoint_decls = {}

        # 去重边集：(from_key, to_key) 保序去重（边序=主键序，幂等）
        seen_pairs: set = set()
        pairs: List[Tuple[str, str]] = []
        for edge in edges:
            key = (str(edge.get("from_key")), str(edge.get("to_key")))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            pairs.append(key)

        def render_group(title: str, members: List[RedactedDict],
                         hidden_keys: List[str], lines_out: List[str]) -> None:
            """渲染一个 subgraph（节点行 + 折叠计数节点 + 端点声明）。

            Args:
                title: subgraph 标题（转义后）。
                members: 展开渲染的页节点（主键序）。
                hidden_keys: 折叠进计数节点的 url_key 列表。
                lines_out: 输出行累加器。
            """
            lines_out.append('    subgraph "SG_{0}"'.format(_nav_escape(title)))
            for page in members:
                label = _nav_label(str(page.get("title") or page.get("url_key") or ""))
                lines_out.append('        P{0}["{1}"]'.format(int(page["page_id"]), label))
            if hidden_keys:
                counter_id = "C" + _nav_escape(title).replace(" ", "_")
                lines_out.append('        {0}["({1}+{2} 节点未展示)"]'.format(
                    counter_id, _nav_escape(title), len(hidden_keys)))
                for key in hidden_keys:
                    self._endpoint_decls[key] = counter_id
            lines_out.append("    end")

        lines: List[str] = ["graph TD"]
        rendered_keys: set = set()
        if len(pages) <= NAV_FOLD_THRESHOLD:
            # 不折叠：全部节点平铺（声明序=主键序）
            for page in pages:
                label = _nav_label(str(page.get("title") or page.get("url_key") or ""))
                lines.append('    P{0}["{1}"]'.format(int(page["page_id"]), label))
                rendered_keys.add(str(page.get("url_key")))
        else:
            # 折叠：按 (深度桶, URL 首段) 聚类；组按 (深度, 首段) 排序保证幂等
            groups: Dict[Tuple[int, str], List[RedactedDict]] = {}
            for page in pages:
                gkey = (int(page.get("depth") or 0), _first_path_segment(str(page.get("url_key") or "")))
                groups.setdefault(gkey, []).append(page)
            for gkey in sorted(groups):
                members = sorted(groups[gkey], key=lambda p: int(p["page_id"]))
                shown = members[:NAV_GROUP_LIMIT]
                hidden = members[NAV_GROUP_LIMIT:]
                render_group("深度{0}/{1}".format(gkey[0], gkey[1]), shown,
                             [str(p.get("url_key")) for p in hidden], lines)
                rendered_keys.update(str(p.get("url_key")) for p in shown)
        # 未渲染 url_key（frontier 页 / 边端点异常）→ 归入"未采集端点"折叠组
        unrendered: List[str] = []
        for from_key, to_key in pairs:
            for key in (from_key, to_key):
                if key not in rendered_keys and key not in unrendered:
                    unrendered.append(key)
        if unrendered:
            render_group("未采集端点", [], unrendered, lines)
        # 关系行：端点 id 经映射解析（已渲染 → P<id>；折叠 → 组计数节点 id）
        for key_from, key_to in pairs:
            src = self._resolve_endpoint(url_to_page, key_from)
            dst = self._resolve_endpoint(url_to_page, key_to)
            if src and dst:
                lines.append("    {0} --> {1}".format(src, dst))
        self._endpoint_decls = {}
        return "\n".join(lines)

    def _resolve_endpoint(self, url_to_page: Dict[str, RedactedDict], key: str) -> str:
        """url_key → mermaid 节点 id（已渲染节点 P<id>；折叠节点 C<组名>）。

        Args:
            url_to_page: url_key → 页节点映射。
            key: 边端点 url_key。

        Returns:
            str: 节点 id；完全无法解析（页不存在且未折叠登记）时返回空串。
        """
        if key in self._endpoint_decls:
            return self._endpoint_decls[key]
        page = url_to_page.get(key)
        if page is not None:
            return "P{0}".format(int(page["page_id"]))
        return ""

    # ------------------------------------------------------------------
    # 第 4 节 数据模型
    # ------------------------------------------------------------------

    def _section_4_data_model(self) -> str:
        """数据模型（§7.1：表卡片 + 隐式 FK 候选 + erDiagram）。"""
        db = self.get_lens_status("db")
        if db["status"] != "collected":
            return "- 未采集：{0}".format(self._lens_declare("db"))
        tables = self._understanding.get("db_tables") or []
        candidates = self._understanding.get("implicit_fk_candidates") or []
        if not tables:
            return "- DB 透镜采集完成但无业务表（schemas 限定过窄或空库，请人工确认）"
        lines: List[str] = ["### 表卡片（逐表，主键序）", ""]
        for table in tables:
            table_id = int(table["table_id"])
            full_name = "{0}.{1}".format(table.get("schema"), table.get("name"))
            self._index_evidence("db_tables", table_id, "数据模型表卡片 {0}".format(full_name))
            lines.append("#### `{0}`（{1}，行数估算 {2}）".format(
                full_name, _md_cell(table.get("kind")), _md_cell(table.get("row_estimate"))))
            if table.get("comment"):
                lines.append("")
                lines.append("> {0}".format(_md_cell(table.get("comment"))))
            columns = table.get("columns") or []
            lines.append("")
            lines.append("| 列 | 类型 | PK | 显式 FK | 注释 |")
            lines.append("|---|---|---|---|---|")
            for col in columns[:ER_COLUMN_LIMIT]:
                fk = _md_cell(col.get("fk_target")) if col.get("fk_target") else "-"
                lines.append("| {0} | {1} | {2} | {3} | {4} |".format(
                    _md_cell(col.get("name")), _md_cell(col.get("data_type")),
                    "✓" if col.get("is_pk") else "", fk, _md_cell(col.get("comment"))))
            if len(columns) > ER_COLUMN_LIMIT:
                lines.append("")
                lines.append("（共 {0} 列，仅展示关键列——前 {1} 列，完整列清单见状态库 db_columns 表）".format(
                    len(columns), ER_COLUMN_LIMIT))
            lines.append("")
        lines.append("### 隐式 FK 候选（推断，需人工确认，REQ-SU-013）")
        lines.append("")
        if candidates:
            lines.append("| 子表.列 | 父表.列 | 预筛分 | 包含度 | 止步规则 |")
            lines.append("|---|---|---|---|---|")
            for cand in candidates:
                self._index_evidence("implicit_fk_candidates", int(cand["cand_id"]),
                                     "隐式 FK 候选 {0}.{1}".format(
                                         cand.get("child_table"), cand.get("child_column")))
                lines.append("| {0}.{1} | {2}.{3} | {4} | {5} | {6} |".format(
                    _md_cell(cand.get("child_table")), _md_cell(cand.get("child_column")),
                    _md_cell(cand.get("parent_table")), _md_cell(cand.get("parent_column")),
                    _md_cell(cand.get("prescreen_score")), _md_cell(cand.get("containment")),
                    _md_cell(cand.get("stopped_at_rule"))))
        else:
            lines.append("- 无达标隐式 FK 候选（止步候选属噪声不落库，见 db_inspector 报告）")
        lines.append("")
        lines.append("### ER 图")
        lines.append("")
        lines.append("Mermaid 源同步落盘 `diagrams/er.mmd`（显式 FK `||--o{` 实线；"
                     "隐式 FK 关系标签 `\"推断FK(包含度x.xx)\"` 显式标注，需人工确认）。")
        lines.append("")
        lines.append("```mermaid")
        lines.append(self.build_er_mermaid())
        lines.append("```")
        return "\n".join(lines)

    def build_er_mermaid(self) -> str:
        """生成 ER 图 Mermaid（§2.3.12 / §7.2）。

        规则：
          - ``erDiagram``；实体名 = schema_table 净化标识符，块注释携带原表名；
          - 显式 FK（列 fk_target='schema.table.column'）画 ``||--o{`` 实线；
          - 隐式 FK 候选同样画 ``||--o{`` 但关系标签固定
            ``"推断FK(包含度0.92)"`` 形态（Mermaid 无原生虚线，用标签显式
            标注——保证正则可识别"推断"字样，REQ-SU-018 AC2）；
          - 表数 >40 时按表名前缀分 subgraph、每组截断计数（§7.2 统一折叠规则）；
          - 实体块属性行 ≤12 列，超限追加 ``_truncated "仅展示关键列"`` 属性行注明。

        Returns:
            str: mermaid 源码（不含尾部换行）。
        """
        tables = self._understanding.get("db_tables") or []
        candidates = self._understanding.get("implicit_fk_candidates") or []
        # 表名（含/不含 schema 两种形态）→ 实体 id 解析表（FK/隐式 FK 引用兼容）
        ident_by_name: Dict[str, str] = {}
        for table in tables:
            full = "{0}.{1}".format(table.get("schema"), table.get("name"))
            ident = _er_identifier(full)
            ident_by_name[full] = ident
            ident_by_name.setdefault(str(table.get("name")), ident)

        def resolve_entity(raw: str) -> str:
            """表名引用（可带 schema 前缀）→ 实体 id；未入库表自动建净化 id。"""
            raw = str(raw or "")
            if raw in ident_by_name:
                return ident_by_name[raw]
            # 隐式 FK 候选表名带 schema 前缀而库内表未匹配时，按尾段名兜底
            tail = raw.split(".")[-1]
            if tail in ident_by_name:
                return ident_by_name[tail]
            return _er_identifier(raw)

        folded = len(tables) > ER_FOLD_THRESHOLD
        # 折叠分组（表名前缀）；未折叠时单组
        if folded:
            groups: Dict[str, List[RedactedDict]] = {}
            for table in tables:
                groups.setdefault(_table_prefix(str(table.get("name") or "")), []).append(table)
            group_names = sorted(groups)
        else:
            groups = {"": tables}
            group_names = [""]

        rendered_entities: set = set()
        lines: List[str] = ["erDiagram"]
        for gname in group_names:
            members = sorted(groups[gname], key=lambda t: int(t["table_id"]))
            if folded:
                lines.append('    subgraph "SG_{0}"'.format(gname.upper()))
            indent = "    " if folded else ""
            shown = members[:NAV_GROUP_LIMIT] if folded else members
            hidden_count = max(0, len(members) - len(shown))
            for table in shown:
                full = "{0}.{1}".format(table.get("schema"), table.get("name"))
                ident = ident_by_name[full]
                rendered_entities.add(ident)
                lines.append("{0}{1} {{".format(indent, ident))
                lines.append('{0}    string __table "{1}"'.format(indent, _nav_escape(full)))
                columns = table.get("columns") or []
                for col in columns[:ER_COLUMN_LIMIT]:
                    attr_type = _er_identifier(str(col.get("data_type") or "other"))
                    attr_name = _er_identifier(str(col.get("name") or "col"))
                    comment = str(col.get("comment") or "").strip()
                    if comment:
                        lines.append('{0}        {1} {2} "{3}"'.format(
                            indent, attr_type, attr_name, _nav_escape(comment)))
                    else:
                        lines.append("{0}        {1} {2}".format(indent, attr_type, attr_name))
                if len(columns) > ER_COLUMN_LIMIT:
                    lines.append('{0}        string _truncated "仅展示关键列（共{1}列）"'.format(
                        indent, len(columns)))
                lines.append("{0}}}".format(indent))
            if hidden_count:
                lines.append('{0}    %% 组 {1} 折叠：另有 {2} 张表未展示（完整清单见状态库 db_tables）'.format(
                    indent, gname, hidden_count))
            if folded:
                lines.append("    end")
        lines.append("    %% 显式外键：||--o{ 实线关系（父 ||--o{ 子）")
        # 显式 FK（列级 fk_target 非空，按 table_id/列名主键序）
        for table in tables:
            child_ident = ident_by_name["{0}.{1}".format(table.get("schema"), table.get("name"))]
            for col in sorted(table.get("columns") or [], key=lambda c: str(c.get("name"))):
                fk_raw = col.get("fk_target")
                if not fk_raw:
                    continue
                parent_table = str(fk_raw).rsplit(".", 1)[0]
                parent_ident = resolve_entity(parent_table)
                if parent_ident == child_ident:
                    continue  # 自引用边省略（ER 图自环可读性差，卡片已含 FK 列信息）
                lines.append('    {0} ||--o{{ {1} : "FK {2}"'.format(
                    parent_ident, child_ident, _nav_escape(str(col.get("name") or ""))))
        lines.append('    %% 隐式外键候选：关系标签"推断FK(包含度x.xx)"显式标注，需人工确认')
        # 隐式 FK 候选（cand_id 主键序，幂等）
        for cand in sorted(candidates, key=lambda c: int(c.get("cand_id", 0))):
            parent_ident = resolve_entity(str(cand.get("parent_table")))
            child_ident = resolve_entity(str(cand.get("child_table")))
            if parent_ident == child_ident:
                continue
            containment = cand.get("containment")
            ratio_text = "{0:.2f}".format(float(containment)) if containment is not None else "未测"
            lines.append('    {0} ||--o{{ {1} : "推断FK(包含度{2}) {3}->{4}"'.format(
                parent_ident, child_ident, ratio_text,
                _nav_escape(str(cand.get("child_column"))),
                _nav_escape(str(cand.get("parent_column")))))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 第 5 节 UI ↔ 数据映射
    # ------------------------------------------------------------------

    def _section_5_ui_data_mapping(self) -> str:
        """UI↔数据映射（§7.1：relations ⋈ findings(kind=mapping)；未回填→声明）。"""
        findings = self._findings_of_kind("mapping")
        relations = self._relations_of_type("api_table") + self._relations_of_type("page_api")
        if not findings:
            lines = ["**{0}**".format(LLM_PENDING_NOTE), ""]
            lines.append("已产出的确定性关联证据（脚本层事实，非语义结论）：")
            lines.append("")
            lines.extend(self._relations_table(relations))
            return "\n".join(lines)
        lines = ["| 结论（confidence） | 证据引用 | 确定性关联证据 |", "|---|---|---|"]
        for finding in findings:
            refs = []
            for ref in finding.get("evidence_refs") or []:
                self._index_evidence_ref(ref, "第 5 节映射结论 #{0}".format(finding.get("finding_id")))
                refs.append("`{0}`".format(ref))
            lines.append("| {0} | {1} | relations 共 {2} 条（api_table/page_api） |".format(
                self._render_finding(finding), "、".join(refs) or "-", len(relations)))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 第 6 节 缓存与中间件
    # ------------------------------------------------------------------

    def _section_6_cache(self) -> str:
        """缓存与中间件（§7.1：redis_patterns 表 + 未采集声明）。"""
        redis_status = self.get_lens_status("redis")
        if redis_status["status"] != "collected":
            return "- 未采集：{0}".format(self._lens_declare("redis"))
        patterns = self._understanding.get("redis_patterns") or []
        if not patterns:
            return "- Redis 采集完成但 SCAN 未命中任何键（空库或 allowlist 过窄，请人工确认）"
        lines = [
            "| 模式 | 键数 | 无 TTL 占比 | TTL 分布 | 类型分布 | 样例键（脱敏） |",
            "|---|---|---|---|---|---|",
        ]
        for pattern in patterns:
            self._index_evidence("redis_patterns", int(pattern["pattern_id"]),
                                 "Redis 键模式 {0}".format(pattern.get("pattern")))
            lines.append("| `{0}` | {1} | {2} | `{3}` | `{4}` | `{5}` |".format(
                _md_cell(pattern.get("pattern")), _md_cell(pattern.get("key_count")),
                "{0:.0%}".format(float(pattern.get("no_ttl_ratio") or 0.0)),
                _md_cell(pattern.get("ttl_summary")), _md_cell(pattern.get("type_summary")),
                _md_cell(pattern.get("sample_keys"))))
        no_ttl_hot = [
            p for p in patterns
            if float(p.get("no_ttl_ratio") or 0.0) >= 0.8 and int(p.get("key_count") or 0) > 0
        ]
        if no_ttl_hot:
            lines.append("")
            lines.append("观察项（仅观察不断言，REQ-SU-015）：以下模式无 TTL 占比 ≥80%，"
                         "存在常驻泄漏可能，建议人工复核：" + "、".join(
                             "`{0}`".format(p.get("pattern")) for p in no_ttl_hot))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 第 7 节 业务规则汇编
    # ------------------------------------------------------------------

    def _section_7_business_rules(self) -> str:
        """业务规则汇编（§7.1：findings(kind=business_rule/semantic_name)）。"""
        findings = self._findings_of_kind("business_rule") + self._findings_of_kind("semantic_name")
        if not findings:
            return "**{0}**".format(LLM_PENDING_NOTE)
        lines: List[str] = []
        for finding in findings:
            refs = []
            for ref in finding.get("evidence_refs") or []:
                self._index_evidence_ref(ref, "第 7 节业务规则 #{0}".format(finding.get("finding_id")))
                refs.append("`{0}`".format(ref))
            lines.append("- {0}（kind={1}，证据：{2}）".format(
                self._render_finding(finding), finding.get("kind"), "、".join(refs)))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 第 8 节 API 面
    # ------------------------------------------------------------------

    def _section_8_api_surface(self) -> str:
        """API 面（§7.1：端点清单 + api_table 关联 + 被拦截写请求观测清单）。"""
        api_status = self.get_lens_status("api")
        lines: List[str] = []
        if api_status["status"] != "collected":
            lines.append("- API 观测未采集：{0}".format(self._lens_declare("api")))
        else:
            endpoints = self._understanding.get("endpoints") or []
            if not endpoints:
                lines.append("- API 观测完成但未捕获任何端点（纯 SSR 站点属预期）")
            else:
                # 端点 → 匹配表名集合（relations api_table 证据 join）
                tables_by_endpoint: Dict[str, List[str]] = {}
                for rel in self._relations_of_type("api_table"):
                    left = str(rel.get("left_ref") or "")
                    if left.startswith("api:"):
                        tables_by_endpoint.setdefault(left.split(":", 1)[1], []).append(
                            str(rel.get("right_ref")))
                lines.append("| 端点 | 方法 | 状态 | 触达页面 | 匹配表证据 | 样本数 |")
                lines.append("|---|---|---|---|---|---|")
                for ep in endpoints:
                    endpoint_id = str(ep["endpoint_id"])
                    self._index_evidence("api_observations", int(ep["endpoint_id"]),
                                         "API 端点 {0} {1}".format(ep.get("method"), ep.get("url_path")))
                    pages_seen = "、".join(
                        "P{0}".format(pid) for pid in (ep.get("observed_on_pages") or []))
                    lines.append("| `{0}` | {1} | {2} | {3} | {4} | {5} |".format(
                        _md_cell(ep.get("url_path")), _md_cell(ep.get("method")),
                        _md_cell(ep.get("latest_status")), _md_cell(pages_seen),
                        _md_cell("、".join(tables_by_endpoint.get(endpoint_id, [])) or "-"),
                        _md_cell(ep.get("sample_count"))))
        lines.append("")
        lines.append("### 被拦截的写请求观测清单（系统有写能力但**未经执行验证**的显式声明）")
        lines.append("")
        blocked = [b for b in (self._understanding.get("blocked_events") or [])
                   if b.get("kind") == "aborted_method"]
        if blocked:
            lines.append("| URL（query 已键名化） | 方法 | 观测页 |")
            lines.append("|---|---|---|")
            for event in blocked:
                self._index_evidence("blocked_events", int(event["block_id"]),
                                     "被拦截写请求 {0}".format(event.get("url")))
                page_ref = event.get("page_id")
                lines.append("| `{0}` | {1} | {2} |".format(
                    _md_cell(event.get("url")), _md_cell(event.get("method")),
                    "P{0}".format(page_ref) if page_ref is not None else "-"))
        else:
            lines.append("- 未观测到被拦截的非 GET 请求（本次遍历未触发任何写方法请求）")
        other_kinds = [b for b in (self._understanding.get("blocked_events") or [])
                       if b.get("kind") != "aborted_method"]
        if other_kinds:
            lines.append("")
            kind_counts: Dict[str, int] = {}
            for event in other_kinds:
                kind = str(event.get("kind"))
                kind_counts[kind] = kind_counts.get(kind, 0) + 1
            lines.append("其他拦截事件统计：" + "、".join(
                "{0} ×{1}".format(k, v) for k, v in sorted(kind_counts.items())))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 第 9 节 证据附录
    # ------------------------------------------------------------------

    def _section_9_evidence(self) -> str:
        """证据附录（§7.1：证据索引 + 脱敏声明 + 方法论声明，常量文案）。"""
        lines = [
            "证据索引（本次渲染共 {0} 条被文档引用；完整映射见 "
            "`evidence/evidence-index.json`）：".format(len(self._evidence_index)),
            "",
            "| 证据编号 | 状态库引用 | 说明 |",
            "|---|---|---|",
        ]
        for entry in self._evidence_index:
            lines.append("| E{0:04d} | `{1}` | {2} |".format(
                int(entry["seq"]), _md_cell(entry["ref"]), _md_cell(entry["description"])))
        if not self._evidence_index:
            lines.append("| - | - | 本次文档未引用任何状态库记录 |")
        lines.extend([
            "",
            "**脱敏声明**：本文档及全部产物（JSON/快照/日志）在落盘前经统一 "
            "redact 管线处理（键名敏感模式 + PII 值形态正则 + URL 内嵌凭据剥离），",
            "数据库采样另经 DataMasker 列级脱敏；明文凭据仅以内存 SensitiveStr 形态存在，"
            "进程退出即消失。`state/storage_state.json` 为浏览器会话态（权限 0600），",
            "属敏感文件，**建议运行结束后人工清理**。",
            "",
            "**运行环境与方法论声明**：本系统理解由确定性脚本流水线产出（预检 → 登录 → "
            "四透镜只读采集 → 三角关联 → LLM 语义回填 → 渲染），全程满足：",
            "",
            "1. 浏览器网络层拦截全部非 GET 请求与白名单外域（写能力仅观测、零执行）；",
            "2. 危险/不可逆动作零点击（T3 只记录）；",
            "3. 数据库会话只读加固 + 语句白名单校验器；",
            "4. Redis 硬编码只读命令白名单；",
            "5. 全局限速（等效 QPS ≤ 1/delay），遗留系统友好串行遍历。",
            "",
            "对遗留系统建议先在预发环境试跑；风控敏感系统应调大 `--delay-ms`。",
        ])
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 第 10 节 未验证推断与未覆盖清单（恒输出）
    # ------------------------------------------------------------------

    def _section_10_uncovered(self) -> str:
        """未覆盖清单（§7.1：a 低置信汇总 / b T3 清单 / c frontier / d 失败页 / e 缺失透镜）。"""
        lines: List[str] = []
        # a) 全部低置信/推断结论
        lines.append("### a) 低置信结论汇总（⚠ 待人工确认）")
        lines.append("")
        low_findings = [f for f in (self._understanding.get("findings") or [])
                        if f.get("confidence") == "low"]
        if low_findings:
            for finding in low_findings:
                lines.append("- {0}（kind={1}，证据：{2}）".format(
                    self._render_finding(finding), finding.get("kind"),
                    "、".join("`{0}`".format(r) for r in (finding.get("evidence_refs") or []))))
        else:
            lines.append("- 无低置信结论")
        # b) 未点击 T3 动作清单（只记录红线⑤）
        lines.append("")
        lines.append("### b) 未点击的 T3 危险/不可逆动作清单（红线⑤：仅记录）")
        lines.append("")
        t3_rows: List[str] = []
        for page in self._pages():
            actions = sorted(page.get("actions") or [],
                             key=lambda a: int(a.get("action_id", 0)))
            for action in actions:
                if action.get("tier") == "T3":
                    t3_rows.append("- 页面 P{0}：动作#{1} 规则 `{2}`".format(
                        page.get("page_id"), action.get("action_id"), action.get("rule_name")))
        lines.extend(t3_rows if t3_rows else ["- 无 T3 动作记录"])
        # c) 预算耗尽 frontier（store.frontier 直查）
        lines.append("")
        lines.append("### c) 预算耗尽时未探索的 frontier 节点")
        lines.append("")
        frontier = self._store.frontier()
        if frontier:
            for node in frontier:
                lines.append("- `{0}`（深度 {1}，状态 {2}）".format(
                    _md_cell(node.get("url_key")), _md_cell(node.get("depth")),
                    _md_cell(node.get("status"))))
        else:
            lines.append("- 无 frontier（队列自然耗尽或采集未启用 UI 透镜）")
        # d) timeout/error 页面
        lines.append("")
        lines.append("### d) 因超时/错误未成功采集的页面")
        lines.append("")
        failed_pages = [p for p in self._pages()
                        if p.get("status") in ("timeout", "error")]
        if failed_pages:
            for page in failed_pages:
                lines.append("- [P{0}] `{1}`：{2}".format(
                    page.get("page_id"), _md_cell(page.get("url_key")),
                    _md_cell(page.get("error") or page.get("status"))))
        else:
            lines.append("- 全部已发现页面均采集成功（或未启用 UI 透镜）")
        # e) 缺失透镜清单
        lines.append("")
        lines.append("### e) 缺失透镜清单")
        lines.append("")
        missing = [name for name in ("ui", "api", "db", "redis")
                   if self.get_lens_status(name)["status"] != "collected"]
        if missing:
            for name in missing:
                lines.append("- {0}：{1}".format(name, self._lens_declare(name)))
        else:
            lines.append("- 四透镜（UI/API/DB/Redis）全部完成采集")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # findings 渲染（§2.3.12）
    # ------------------------------------------------------------------

    def _render_finding(self, finding: RedactedDict) -> str:
        """渲染单条 finding（low → '⚠ 待人工确认' 前缀，§6.2 规则 4）。

        缺 confidence/evidence_refs 的条目根本到不了这里——入库时已被
        validate_findings_schema 整批拒绝（§6.2），渲染层无需再兜底。

        Args:
            finding: findings 表行（export_understanding 产物）。

        Returns:
            str: 形如 '⚠ 待人工确认：<claim>（confidence=low）' 的文本。
        """
        claim = str(finding.get("claim") or "")
        confidence = str(finding.get("confidence") or "")
        prefix = "⚠ 待人工确认：" if confidence == "low" else ""
        return "{0}{1}（confidence={2}）".format(prefix, claim, confidence)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _lens_declare(self, lens: str) -> str:
        """透镜缺失显式声明文本（AP-3：'未采集：<原因>'，绝不空假数据）。

        Args:
            lens: 透镜名。

        Returns:
            str: 中文声明（含编排层登记的 skip_reason / 安装命令）。
        """
        st = self.get_lens_status(lens)
        reason = st.get("skip_reason") or "原因未登记（编排层未报告降级原因）"
        return "{0}（状态：{1}）".format(reason, st["status"])

    def _preflight_lines(self) -> List[str]:
        """读取 ``state/preflight.json`` 渲染预检摘要行（不存在则声明缺失）。

        预检文件由编排层在阶段 0 落盘（已脱敏）；渲染只读该文件，
        绝不重新发起任何网络/连接探测（渲染是状态库+产物的纯函数视图）。

        Returns:
            list[str]: Markdown 行列表。
        """
        path = self._root / "state" / "preflight.json"
        if not path.is_file():
            return ["- 预检报告缺失（本次运行未执行预检阶段——--render-only 属预期）"]
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ["- 预检报告存在但无法解析（请以 state/ 目录原始文件为准）"]
        lines: List[str] = []
        for item in report.get("items") or []:
            lines.append("- `{0}`：**{1}**{2}".format(
                _md_cell(item.get("name")), _md_cell(item.get("status")),
                "——{0}".format(_md_cell(item.get("reason"))) if item.get("reason") else ""))
        return lines or ["- 预检报告为空（异常，请人工检查）"]

    def _pages(self) -> List[RedactedDict]:
        """pages（export 已主键序，直接透传；主键序=幂等序）。"""
        return list(self._understanding.get("pages") or [])

    def _edges(self) -> List[RedactedDict]:
        """edges（主键序透传）。"""
        return list(self._understanding.get("edges") or [])

    def _relations_of_type(self, rtype: str) -> List[RedactedDict]:
        """按 rtype 过滤 relations（主键序保持）。"""
        return [r for r in (self._understanding.get("relations") or [])
                if r.get("rtype") == rtype]

    def _findings_of_kind(self, kind: str) -> List[RedactedDict]:
        """按 kind 过滤 findings（主键序保持）。"""
        return [f for f in (self._understanding.get("findings") or [])
                if f.get("kind") == kind]

    def _confidence_distribution(self) -> RedactedDict:
        """findings confidence 分布统计（summary.json 数据源）。"""
        dist: Dict[str, int] = {"high": 0, "medium": 0, "low": 0}
        for finding in self._understanding.get("findings") or []:
            level = str(finding.get("confidence"))
            if level in dist:
                dist[level] += 1
        return redact(dist)

    def _relations_table(self, relations: List[RedactedDict]) -> List[str]:
        """关联证据通用表（第 5 节骨架与各节复用；主键序）。"""
        if not relations:
            return ["- 无确定性关联证据（relations 表为空——采集面未观测到可配对信号）"]
        lines = ["| 类型 | 左引用 | 右引用 | 分数 | 证据 |", "|---|---|---|---|---|"]
        for rel in relations:
            self._index_evidence("relations", int(rel["relation_id"]),
                                 "关联证据 {0}".format(rel.get("rtype")))
            lines.append("| {0} | `{1}` | `{2}` | {3} | `{4}` |".format(
                _md_cell(rel.get("rtype")), _md_cell(rel.get("left_ref")),
                _md_cell(rel.get("right_ref")), _md_cell(rel.get("score")),
                _md_cell(json.dumps(dict(rel.get("evidence") or {}),
                                    ensure_ascii=False, sort_keys=True))))
        return lines

    def _index_evidence(self, table: str, record_id: int, description: str) -> None:
        """登记一条被文档引用的证据（evidence-index 同源累加器，幂等去重）。

        Args:
            table: 状态库表名。
            record_id: 记录主键。
            description: 人类可读说明（进索引，已 _md_cell 安全的调用方文本）。
        """
        ref = "{0}:{1}".format(table, record_id)
        for entry in self._evidence_index:
            if entry.get("ref") == ref:
                return  # 同记录多次引用只登记一次（编号稳定，幂等）
        self._evidence_index.append(RedactedDict({
            "seq": len(self._evidence_index) + 1,
            "ref": ref,
            "description": description,
        }))

    def _index_evidence_ref(self, ref: Any, description: str) -> None:
        """按 findings 的 evidence_ref（'<table>:<id>'）登记证据索引。

        Args:
            ref: evidence_ref 原文（已通过入库存在性校验）。
            description: 引用位置说明。
        """
        ref_text = str(ref)
        table, _sep, id_text = ref_text.rpartition(":")
        if not table or not id_text.isdigit():
            return  # 形态异常不登记（入库校验保证正常路径不会到这里）
        self._index_evidence(table, int(id_text), description)

    def _run_started_at(self) -> Optional[float]:
        """run 级时间常量 started_at（§7.3：全部落盘时间戳唯一来源）。"""
        return (self._understanding.get("meta") or {}).get("started_at")

    def _lenses_section(self) -> RedactedDict:
        """understanding.json 的 lenses 段（状态 + skip_reason，§6.1 硬约束）。

        透镜节点数据不在此重复存放（export 顶层 pages/endpoints/db_tables/
        redis_patterns/… 已是唯一数据源，避免双源不一致），本段只携带
        状态与中文缺失原因（渲染器据 set_lens_status 的事实输出）。
        """
        payload: Dict[str, Any] = {}
        for lens in ("ui", "api", "db", "redis"):
            st = self.get_lens_status(lens)
            node: Dict[str, Any] = {"status": st["status"]}
            if st.get("skip_reason"):
                node["skip_reason"] = st["skip_reason"]
            payload[lens] = node
        return redact(payload)
