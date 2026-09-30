"""SFD（System Function Detail，系统功能与业务流程详说）脚本层。

对应架构文档 ARCH-SFD-001（docs/dev/SYSTEM_FUNCTION_DOC_ARCHITECTURE.md）§2
与 PRD-SFD-001（docs/dev/SYSTEM_FUNCTION_DOC_PRD.md）REQ-SFD-001~005/012/013。

**定位**：SU 两阶段工作流（采集 → LLM 回填 → --render-only 收口）之后的
第三阶段 "post-render 专家详说" 的**确定性支撑层**——

  1. ``--detailed-doc``：前置校验（precheck_detailed_run）→ 五专家素材包
     切片（build_material_packages / write_material_packages）→ 8 节大纲
     骨架渲染（render_outline）→ stdout 打印派发指引；
  2. ``--assemble``：读取 ``detailed/sections/`` 专家草稿 → 节归属与 E-n
     引用校验（assemble_final_doc）→ 凭据扫描收口（scan_credential_leak）→
     原子写终稿 SYSTEM_FUNCTION_DOC.md + assembly-report.json
     （finalize_assembly / run_assemble）。

**零新增凭据面 / 零网络 / 零 LLM**（NFR-SFD-001）：本模块只 import 标准库 +
``su.config`` / ``su.dto``；对状态库的依赖经构造函数参数注入 duck-typed
store（只调 ``read_latest_run()`` / ``stats()`` 两个只读方法，ARCH §2.1）。

**幂等口径**（ARCH §6）：全部时间戳唯一真相源 = understanding.json 的
``meta.started_at``（禁止 time.time()）；JSON 统一 sort_keys + ensure_ascii
+ indent=2；全部落盘走 write_text_atomic（临时文件 + os.replace）。

安全红线（ARCH §8）：素材包写盘入口 isinstance(RedactedDict) 断言 +
scrub_json_tree 四判据复核（命中整批拒写 exit 2）；终稿 scan_credential_leak
收口（命中不落盘 exit 2，报告只记位置类别绝不携带原文）。
"""

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from su.config import (
    _INLINE_USERINFO_RE,
    _PII_PLACEHOLDER,
    redact,
    scrub_text,
)
from su.dto import (
    REDACTED_PLACEHOLDER,
    RedactedDict,
    SuConfigError,
)
from su.state_store import _CLAIM_CREDENTIAL_RE

__all__ = [
    "DetailedDocError",
    "DetailedPaths",
    "DraftOutcome",
    "ScanHit",
    "ScanReport",
    "MaterialPackageSpec",
    "SECTION_TITLES_SFD",
    "SECTION_SOURCE_SFD",
    "build_paths",
    "check_existing_final",
    "collect_package_violations",
    "precheck_detailed_run",
    "build_material_packages",
    "scrub_json_tree",
    "write_material_packages",
    "render_outline",
    "write_text_atomic",
    "run_detailed_doc",
    "load_evidence_index",
    "assemble_final_doc",
    "finalize_assembly",
    "run_assemble",
    "scan_credential_leak",
]

# 语义别名（不新建类；报错口径靠 message 统一 "[SFD]" 前缀区分来源——
# 2026-09-30 审查修订 P2-11：SuConfigError.code 固定为 "config_error"
# 不可定制，ARCH §2.2 口径修正）
DetailedDocError = SuConfigError

# ---------------------------------------------------------------------------
# 模块级常量（ARCH §2.2 常量清单）
# ---------------------------------------------------------------------------

DETAILED_DIRNAME: str = "detailed"                    # <out>/<sid>/ 下详说产物根目录
FINAL_DOC_FILENAME: str = "SYSTEM_FUNCTION_DOC.md"    # 终稿（<out>/<sid>/ 根下）
# 大纲骨架与终稿同名（骨架被终稿覆盖式产出，ARCH §2.2.6 注）
OUTLINE_DOC_FILENAME: str = "SYSTEM_FUNCTION_DOC.md"
ROLE_PROMPTS_DIR: str = "docs/spec/role-prompts"      # 五专家 prompt 目录（相对 skill 根）

# 8 节固定大纲标题（ARCH §2.2.4 定稿表；节序即文档序）
SECTION_TITLES_SFD: Dict[int, str] = {
    1: "系统定位与技术架构",
    2: "功能全景与业务流程",
    3: "页面功能详说",
    4: "数据模型业务语义",
    5: "接口契约说明",
    6: "质量盲区与风险建议",
    7: "未验证推断与附录",
    8: "附录：证据索引与运行说明",
}

# 节归属表：1/2/3/4/5/6 → 对应草稿文件名（相对 detailed/ 的路径）；
# 第 6 节（业务规则与状态机裁决）内嵌于 02 产品草稿的 "### 2.4" 小节，
# 归属仍记 05-quality 之外的产品草稿（ARCH §2.2.4：第 6 节主责=产品经理
# 草稿 02 承载——映射表中第 6 节来源为 05-quality.doc.md 的"质量盲区"
# 系终稿节 6"质量盲区与风险建议"，见 ARCH §4.3 映射表逐字）；
# 7/8 → 装配层自动生成（"__assembler__" 哨兵值）
SECTION_SOURCE_SFD: Dict[int, str] = {
    1: "sections/01-architecture.doc.md",
    2: "sections/02-product.doc.md",
    3: "sections/03-pages.doc.md",
    4: "sections/04-data-semantics.doc.md",
    5: "sections/04-data-semantics.doc.md",     # 04 草稿标记切分第二段
    6: "sections/05-quality.doc.md",
    7: "__assembler__",                          # 装配层自动
    8: "__assembler__",                          # 装配层自动
}

# 素材包禁入 understanding.json 顶层 key（REQ-SFD-002 AC2 白名单前置排除）
FORBIDDEN_PACKAGE_KEYS: Tuple[str, ...] = (
    "findings_prompt",   # 回填契约引用段，对五专家无素材价值
    "lenses",            # 透镜状态段——状态语义已由 manifest 的 lens_status 表达
)

# 五视角字段白名单（ARCH §2.2.3 表；值为 understanding.json 顶层 key 元组）
ARCHITECT_PACKAGE_KEYS: Tuple[str, ...] = (
    "meta", "pages", "endpoints", "db_tables", "redis_patterns")
PRODUCT_PACKAGE_KEYS: Tuple[str, ...] = ("pages", "edges", "endpoints", "findings")
DEV_PACKAGE_KEYS: Tuple[str, ...] = (
    "db_tables", "implicit_fk_candidates", "endpoints", "relations", "findings")
UI_PACKAGE_KEYS: Tuple[str, ...] = ("pages", "edges", "blocked_events", "findings")
QA_PACKAGE_KEYS: Tuple[str, ...] = (
    "pages", "endpoints", "blocked_events", "relations", "findings")

# 扫描器复用编译对象（NFR-SFD-003 复用优先，不复制正则）
_INLINE_CRED_SCAN_RE = _INLINE_USERINFO_RE     # C2：任意 scheme URL 内嵌凭据
_CLAIM_CRED_SCAN_RE = _CLAIM_CREDENTIAL_RE     # C3：键值对形态凭据

# 详说草稿中的 E-n 引用 token：恰好 4 位数字 + 前后边界
# （2026-09-30 审查修订 P2-13：边界断言保证 E0012 独立成 token，
# 5 位以上编号视为非法文本由 [未验证引用] 路径处理）
EVIDENCE_REF_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])E(\d{4})(?!\d)")

# E-n 引用 + 可选锚注形态（E0012(pages:3)——装配器 (seq, ref) 双键复核用，
# ARCH §2.2.5 漂移校验：括号内附证据 ref）
_E_REF_WITH_ANCHOR_RE = re.compile(
    r"(?<![A-Za-z0-9])E(\d{4})\(([^()\s]{1,64})\)(?!\d)")

# C4 扩展敏感键名表（2026-09-30 审查修订 P0-3b：db_samples 非敏感键名采样
# 值穿透 C1-C3 三判据的缺口收口）。与 su/config.SENSITIVE_KEY_PATTERN
# （redact 管线层、键名完整匹配）不同口径——C4 仅用于素材包/终稿的
# **防御性复核拦截**（判违规、不改写），词表用"包含"匹配而非全等
# （键名 auth_code、db_credential 等复合形态都要命中）
SENSITIVE_KEY_SFD_EXTRA = re.compile(
    r"(?i)(credential|auth_?code|pwd|secret|token|api_?key|access_key|private_key)"
)

# 高熵/短随机形态值判据（与 C4 键名命中构成双因子，**同时命中才拦截**：
# 值中存在长度≥12 且同时含字母与数字的连续 token——纯数字/纯字母/短语
# 值不误伤，ARCH §2.2 常量注释）
_HIGH_ENTROPY_TOKEN_RE = re.compile(r"(?=[a-z0-9]*[a-z])(?=[a-z0-9]*[0-9])[a-z0-9]{12,}")

# C2 脱敏形态豁免判定（2026-09-30 审查修订 P1-10）：userinfo 部分已是
# ***REDACTED***（dto.REDACTED_PLACEHOLDER）或 <REDACTED:*>
# （config._PII_PLACEHOLDER 形态）→ 不算命中。scrub 后的 URL 自引用
# （如审计文本转录 mysql://***REDACTED***@host）不得被 C2 再命中。
# 命中 _INLINE_USERINFO_RE 后，用本正则复核被掩码段是否已是脱敏占位。
_REDACTED_USERINFO_RE = re.compile(
    r"([a-zA-Z][a-zA-Z0-9+.-]*://)(?:"
    + re.escape(REDACTED_PLACEHOLDER)
    + r"|<REDACTED:[A-Za-z_]+>)@"
)

# 04 草稿第 5 节起点标记（逐字契约，prompt 文档给逐字样例）
SFD_SECTION5_MARKER: str = "<!-- SFD-SECTION: 5 -->"

# 终稿/骨架头部 status 行（骨架 status: outline，终稿 status: final）
_STATUS_LINE_OUTLINE = "status: outline"
_STATUS_LINE_FINAL = "status: final"

# 装配报告文件名（detailed/ 下）
ASSEMBLY_REPORT_FILENAME: str = "assembly-report.json"

# 证据引用合法率达标线（PRD §7，仅度量不拦截）
REF_LEGALITY_TARGET: float = 0.95


# ---------------------------------------------------------------------------
# 路径常量与草稿规格（ARCH §2.2.1 / §2.2.2）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DetailedPaths:
    """详说流程的全部路径常量（一次构造、全流程只读传递，REQ-SFD-012）。"""

    sys_root: Path              # <out>/<system_id>/
    state_db: Path              # sys_root/state/understanding.sqlite
    understanding_json: Path    # sys_root/understanding.json
    understanding_md: Path      # sys_root/UNDERSTANDING.md
    evidence_index: Path        # sys_root/evidence/evidence-index.json
    detailed_dir: Path          # sys_root/detailed/
    inputs_dir: Path            # sys_root/detailed/inputs/
    sections_dir: Path          # sys_root/detailed/sections/
    final_doc: Path             # sys_root/SYSTEM_FUNCTION_DOC.md


@dataclass(frozen=True)
class MaterialPackageSpec:
    """一个专家素材包的规格（五包各一条，模块级常量表 _PACKAGE_SPECS）。"""

    role: str                       # architect | product | dev | ui | qa
    role_label: str                 # 派发指引中的中文角色名
    prompt_doc: str                 # docs/spec/role-prompts/su-detailed-*.md
    output_section: str             # 该角色草稿的确切输出文件名（§4.4）
    include_keys: Tuple[str, ...]   # understanding.json 顶层 key 白名单


# 五包规格表（ARCH §4.1 命名定稿 + §2.2.3 白名单表；顺序即派发指引顺序）
_PACKAGE_SPECS: Tuple[MaterialPackageSpec, ...] = (
    MaterialPackageSpec(
        role="architect", role_label="架构师",
        prompt_doc="{0}/su-detailed-architect.md".format(ROLE_PROMPTS_DIR),
        output_section="01-architecture.doc.md",
        include_keys=ARCHITECT_PACKAGE_KEYS),
    MaterialPackageSpec(
        role="product", role_label="产品经理",
        prompt_doc="{0}/su-detailed-product.md".format(ROLE_PROMPTS_DIR),
        output_section="02-product.doc.md",
        include_keys=PRODUCT_PACKAGE_KEYS),
    MaterialPackageSpec(
        role="dev", role_label="代码走读",
        prompt_doc="{0}/su-detailed-walkthrough.md".format(ROLE_PROMPTS_DIR),
        output_section="04-data-semantics.doc.md",
        include_keys=DEV_PACKAGE_KEYS),
    MaterialPackageSpec(
        role="ui", role_label="UI设计师",
        prompt_doc="{0}/su-detailed-ui.md".format(ROLE_PROMPTS_DIR),
        output_section="03-pages.doc.md",
        include_keys=UI_PACKAGE_KEYS),
    MaterialPackageSpec(
        role="qa", role_label="测试专家",
        prompt_doc="{0}/su-detailed-qa.md".format(ROLE_PROMPTS_DIR),
        output_section="05-quality.doc.md",
        include_keys=QA_PACKAGE_KEYS),
)


def build_paths(out_root: Path, system_id: str) -> DetailedPaths:
    """由输出根目录与系统标识构造详说路径集（唯一路径真相源）。

    Args:
        out_root: CLI --out 指定的输出根目录。
        system_id: CLI --system-id 指定的输出子目录名。

    Returns:
        DetailedPaths: 全部路径常量（不校验存在性——校验属 precheck 职责）。
    """
    sys_root = Path(out_root) / system_id
    detailed_dir = sys_root / DETAILED_DIRNAME
    return DetailedPaths(
        sys_root=sys_root,
        state_db=sys_root / "state" / "understanding.sqlite",
        understanding_json=sys_root / "understanding.json",
        understanding_md=sys_root / "UNDERSTANDING.md",
        evidence_index=sys_root / "evidence" / "evidence-index.json",
        detailed_dir=detailed_dir,
        inputs_dir=detailed_dir / "inputs",
        sections_dir=detailed_dir / "sections",
        final_doc=sys_root / FINAL_DOC_FILENAME,
    )


# ---------------------------------------------------------------------------
# 前置校验（REQ-SFD-001，ARCH §2.2.1）
# ---------------------------------------------------------------------------

def precheck_detailed_run(
    out_root: Path,
    system_id: str,
    store: Optional[Any] = None,
) -> Dict[str, Any]:
    """详说入口前置校验（REQ-SFD-001，--detailed-doc 专用）。

    校验顺序（全部只读，校验通过前绝不触碰状态库写路径——继承
    _run_render_only 的"先读历史 run、后取锁"口径）：

      1. UNDERSTANDING.md / understanding.json / evidence-index.json 存在
         → 否则 DetailedDocError(exit 2) 逐项列出缺项（PRD E-1）；
      2. understanding.json 可解析为 dict 且 findings 段为非空 list
         → 空数组/缺段/非数组 → exit 2；提示语区分"未回填"（缺段）与
         "专家撤回全部结论"（空数组）两种语义（PRD E-2，2026-09-30 P0-2）；
      3. findings 双源一致性断言（P1-6）：len(findings) ≠
         store.stats()["findings_total"] → exit 2 提示"findings 与状态库
         不同步，请先 --render-only"；
      4. 锚定 run 状态校验（P0-2——锚定对象 = understanding.json
         meta.run_id 对应行而非最新行）：read_latest_run() 最新行 run_id
         == meta.run_id → 直接校验该行 status ∈ {completed, interrupted}；
         run_id 不一致（render-only 后典型）→ 以 understanding.json meta
         段为渲染时点真相源（meta.run_status ∈ {completed, interrupted}
         放行），并如实登记"锚定 run 非最新行（render run 在后）"；
      5. 状态库不存在（产物目录来自拷贝/归档场景）→ 降级为宽松通过，
         登记"run 状态不可考"声明（AP-3 缺失即声明）。

    Args:
        out_root: 输出根目录（--out）。
        system_id: 输出子目录名（--system-id）。
        store: duck-typed 状态库（只调 read_latest_run()/stats() 两个只读
            方法；None = 状态库文件不存在时的降级注入，见第 5 步）。

    Returns:
        dict: 校验上下文，键包括：
            paths (DetailedPaths)、understanding (dict)、started_at、
            run_id (meta.run_id)、run_status（校验通过的锚定状态）、
            anchored_run_is_latest (bool)、run_status_verifiable (bool)、
            notes (List[str]，如实登记的降级声明)。

    Raises:
        DetailedDocError: 任一前置违例（exit_code=2，中文 message+hints）。
    """
    paths = build_paths(out_root, system_id)
    notes: List[str] = []

    # ---- 第 1 步：三个必备产物存在性（逐项列出缺项，PRD E-1）----
    missing = [str(p) for p in (paths.understanding_md, paths.understanding_json,
                                paths.evidence_index) if not p.is_file()]
    if missing:
        raise DetailedDocError(
            "[SFD] 前置校验失败：缺少 SU 既有产物（{0}）——"
            "详说阶段只消费 SU 已脱敏落盘产物".format("、".join(missing)),
            hints=["先完成 SU 采集（可 --skip-llm-phase）与 findings 回填，"
                   "并运行 --render-only 收口后再进入详说阶段"])

    # ---- 第 2 步：understanding.json 可解析 + findings 非空 list ----
    try:
        understanding = json.loads(paths.understanding_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DetailedDocError(
            "[SFD] 前置校验失败：understanding.json 无法解析（{0}）".format(exc),
            hints=["请确保文件为 UTF-8 JSON（此前运行产物 + 回填的 findings 段）"])
    if not isinstance(understanding, dict):
        raise DetailedDocError(
            "[SFD] 前置校验失败：understanding.json 顶层必须是 JSON 对象",
            hints=["该文件应由 SU --render-only 渲染产出，请勿手工改坏结构"])
    if "findings" not in understanding:
        raise DetailedDocError(
            "[SFD] 前置校验失败：understanding.json 缺少 findings 段"
            "（findings **未回填**）",
            hints=["先按 docs/spec/role-prompts/su-llm-backfill.md 回填 findings"
                   " 并运行 --render-only 收口，再进入详说阶段"])
    findings = understanding["findings"]
    if not isinstance(findings, list):
        raise DetailedDocError(
            "[SFD] 前置校验失败：findings 段必须是 JSON 数组，实际 {0}".format(
                type(findings).__name__),
            hints=["形态示例见 docs/spec/role-prompts/su-llm-backfill.md"])
    if len(findings) == 0:
        # 空数组 = "专家撤回全部结论"语义（SU 允许，锚定 run 仍可为
        # completed）；SFD 侧口径仍需非空才能派发（PRD E-2 / P0-2）
        raise DetailedDocError(
            "[SFD] 前置校验失败：findings 数组为空——专家已**撤回全部结论**"
            "（或尚未回填），详说阶段无结论素材可派发",
            hints=["按 docs/spec/role-prompts/su-llm-backfill.md 重新回填 findings"
                   " 并运行 --render-only 收口后再进入详说阶段"])

    # ---- 第 3 步：findings 双源一致性断言（P1-6，复用 stats() 只读）----
    run_status_verifiable = store is not None
    if run_status_verifiable:
        db_findings_total = int(store.stats().get("findings_total") or 0)
        if len(findings) != db_findings_total:
            raise DetailedDocError(
                "[SFD] 前置校验失败：findings 与状态库不同步"
                "（understanding.json {0} 条 ≠ 状态库 {1} 条）".format(
                    len(findings), db_findings_total),
                hints=["understanding.json 的 findings 段可能被手工改过而未回灌"
                       "状态库——请先运行 --render-only 重新收口"])

    # ---- 第 4/5 步：锚定 run 状态（meta.run_id 行口径，P0-2）----
    meta = understanding.get("meta") or {}
    anchored_run_id = meta.get("run_id")
    started_at = meta.get("started_at")
    anchored_run_is_latest = False
    run_status: Optional[str] = None
    if store is None:
        # 第 5 步：状态库缺失（拷贝/归档场景）→ 宽松通过 + 如实声明
        run_status_verifiable = False
        meta_status = str(meta.get("run_status") or "")
        run_status = meta_status if meta_status in ("completed", "interrupted") else None
        notes.append("run 状态不可考（状态库文件缺失，产物目录可能来自拷贝/归档；"
                     "以 understanding.json meta.run_status={0} 为参考）".format(
                         meta_status or "（缺失）"))
    else:
        latest = store.read_latest_run()
        if latest is None:
            # 库存在但无任何 run 行——异常产物，宽松登记不可考（不阻断）
            run_status_verifiable = False
            notes.append("run 状态不可考（状态库无任何 run 记录）")
        else:
            latest_run_id = str(latest.get("run_id") or "")
            if anchored_run_id is not None and latest_run_id == str(anchored_run_id):
                # 最新行即锚定行 → 直接校验该行状态
                anchored_run_is_latest = True
                status = str(latest.get("status"))
                if status not in ("completed", "interrupted"):
                    raise DetailedDocError(
                        "[SFD] 前置校验失败：锚定 run（{0}）状态为 {1}"
                        "（要求 completed/interrupted）".format(anchored_run_id, status),
                        hints=["running 状态请等待对端进程收口；failed 状态请排查"
                               "该次运行日志后重新 --render-only"])
                run_status = status
            else:
                # render-only 后典型场景：锚定 run 非最新行（render run 在后）
                # → 以 understanding.json meta 段为渲染时点真相源（ARCH §2.1）
                meta_status = str(meta.get("run_status") or "")
                if meta_status not in ("completed", "interrupted"):
                    raise DetailedDocError(
                        "[SFD] 前置校验失败：锚定 run（{0}）非状态库最新行"
                        "（最新行={1}），且 understanding.json meta.run_status="
                        "{2} 不合法（要求 completed/interrupted）".format(
                            anchored_run_id, latest_run_id, meta_status or "（缺失）"),
                        hints=["锚定口径 = understanding.json meta.run_id 行；"
                               "meta 段应与该 run 收口时点一致，必要时重新 --render-only"])
                anchored_run_is_latest = False
                run_status = meta_status
                notes.append("锚定 run 非最新行（render run 在后）：状态以 "
                             "understanding.json meta 段为渲染时点真相源"
                             "（meta.run_status={0}）".format(meta_status))

    return {
        "paths": paths,
        "understanding": understanding,
        "started_at": started_at,
        "run_id": anchored_run_id,
        "run_status": run_status,
        "anchored_run_is_latest": anchored_run_is_latest,
        "run_status_verifiable": run_status_verifiable,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# 素材包切片器（REQ-SFD-002，ARCH §2.2.2 / §2.2.3）
# ---------------------------------------------------------------------------

def scrub_json_tree(obj: Any) -> List[Tuple[str, str]]:
    """递归遍历 dict/list/str，返回 [(json_path, 违规类别), ...]（不含原文）。

    四判据（只检测不改写——素材包正文本身已由 SU 脱敏，此处是 REQ-SFD-002
    AC2 的防御性复核；REQ-SFD-013 的素材包侧分支在同一函数上复用）：

      - ``pii``：字符串叶子 scrub_text(值) != 值（PII 值形态/URL 凭据残留）；
      - ``url_userinfo``：_INLINE_USERINFO_RE 命中且非脱敏形态豁免
        （***REDACTED*** / <REDACTED:*> userinfo 不算命中，P1-10）；
      - ``kv_credential``：_CLAIM_CREDENTIAL_RE 命中（键值对形态凭据）；
      - ``entropy_key``（C4，P0-3b）：dict 键名命中 SENSITIVE_KEY_SFD_EXTRA
        且其字符串值命中 _HIGH_ENTROPY_TOKEN_RE 双因子同时命中。

    Args:
        obj: 任意 JSON 兼容结构（dict/list/str/标量）。

    Returns:
        list[tuple[str, str]]: (点分 json_path, 违规类别) 列表，
        违规类别 ∈ pii/url_userinfo/kv_credential/entropy_key；不含原文。
    """
    violations: List[Tuple[str, str]] = []
    _walk_scrub(obj, "$", violations)
    return violations


def _walk_scrub(obj: Any, path: str, out: List[Tuple[str, str]]) -> None:
    """scrub_json_tree 的递归内层（path 为点分定位串）。

    Args:
        obj: 当前节点。
        path: 当前点分路径（根为 "$"）。
        out: 违规累积列表（原地追加 (path, 类别)）。
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_str = str(key)
            child_path = "{0}.{1}".format(path, key_str)
            # C4 双因子：键名命中扩展敏感词表 且 字符串值命中高熵 token
            if (isinstance(value, str)
                    and SENSITIVE_KEY_SFD_EXTRA.search(key_str)
                    and _HIGH_ENTROPY_TOKEN_RE.search(value.lower())):
                out.append((child_path, "entropy_key"))
            _walk_scrub(value, child_path, out)
        return
    if isinstance(obj, (list, tuple)):
        for idx, item in enumerate(obj):
            _walk_scrub(item, "{0}[{1}]".format(path, idx), out)
        return
    if isinstance(obj, str):
        # C1：scrub 差集（值形态 PII / URL 凭据被 scrub_text 改写 → 残留违规）
        if scrub_text(obj) != obj:
            out.append((path, "pii"))
        # C2：任意 scheme URL 内嵌凭据（脱敏形态豁免）
        if _url_userinfo_hit(obj):
            out.append((path, "url_userinfo"))
        # C3：键值对形态凭据（收窄口径，与 SU findings 校验同源正则）
        if _CLAIM_CRED_SCAN_RE.search(obj):
            out.append((path, "kv_credential"))


def _url_userinfo_hit(text: str) -> bool:
    """C2 判据：URL 内嵌 userinfo 命中且非脱敏形态豁免（P1-10）。

    口径：_INLINE_USERINFO_RE 命中的 userinfo 段若已是 ***REDACTED*** 或
    <REDACTED:类型> 形态（scrub 后的 URL 自引用），不算命中——否则终稿
    引用"已脱敏样例"会被误拦。

    Args:
        text: 待检字符串。

    Returns:
        bool: True = 存在未脱敏的 URL 内嵌凭据。
    """
    match = _INLINE_CRED_SCAN_RE.search(text)
    if match is None:
        return False
    # 存在至少一处未脱敏 userinfo 才算命中。实现口径（2026-09-30 测试专家
    # 审查修复 D2）：原"替换脱敏占位后再查残余"写法把占位换成
    # "\u0000NOP@"，scheme:// 前缀原样保留，残余仍形如
    # mysql://\u0000NOP@host——_INLINE_USERINFO_RE 的 userinfo 字符类
    # [^/?#\s]* 可匹配 \u0000NOP，导致 ***REDACTED***@ 与 <REDACTED:*>@
    # 脱敏形态误判命中（ARCH P1-10"scrub 后自引用不得再命中"契约失守，
    # e2e S-3 反例实测复现）。改为用 _REDACTED_USERINFO_RE 把所有脱敏
    # userinfo 段（scheme:// + 占位 + @）从文本中**整体移除**：脱敏引用
    # 移除后 scheme 前缀不复存在，天然不可能再构造出 userinfo 形态；
    # 未脱敏的 userinfo 不受影响，残余照常命中。
    residual = _REDACTED_USERINFO_RE.sub("", text)
    return _INLINE_CRED_SCAN_RE.search(residual) is not None


def build_material_packages(
    understanding: Dict[str, Any],
    started_at: Optional[float],
    lens_status: Dict[str, str],
    evidence_index_sha256: str = "",
) -> Dict[str, RedactedDict]:
    """按五视角白名单从 understanding.json 切出五个素材包（纯函数）。

    每个素材包 = ``{"manifest": {...}, "data": {白名单 key: 节点}}``：

      - 未列入 include_keys 的顶层 key 一律**物理不进包**（白名单默认
        拒绝，ADR-2；禁入项 findings_prompt/lenses 天然被排除）；
      - 缺失透镜 → 空列表兜底（lens_status 段如实声明）；
      - manifest 记录 run 级时间戳、锚定 run_id、evidence-index sha256
        派发锚点（P0-1b）、来源计数（AC3）、scrub 复核违规清单；
      - 走读包（dev）另记 db_samples_masked_by=DataMasker（P0-3b）。

    Args:
        understanding: understanding.json 全量 dict（SU 已脱敏产物）。
        started_at: run 级时间常量（understanding.json meta.started_at）。
        lens_status: 四透镜状态映射（collected/skipped 事实，manifest 用）。
        evidence_index_sha256: 派发时点 evidence-index.json 文件 sha256
            （装配期编号漂移复核锚点）。

    Returns:
        dict[str, RedactedDict]: {role: 素材包}；包体经 config.redact()
        产出（RedactedDict，红线①四层组合之①②）。violations 非空由
        上层（run_detailed_doc）汇总后抛 DetailedDocError。
    """
    packages: Dict[str, RedactedDict] = {}
    meta = understanding.get("meta") or {}
    for spec in _PACKAGE_SPECS:
        data: Dict[str, Any] = {}
        sources: Dict[str, int] = {}
        for key in spec.include_keys:
            # 禁入 key 双保险（正常白名单不会含它们，防御性断言）
            if key in FORBIDDEN_PACKAGE_KEYS:
                continue
            node = understanding.get(key, [])
            data[key] = node
            # AC3 来源计数：容器取长度，标量（如 meta）记 1/0
            if isinstance(node, (list, dict, str)):
                sources[key] = len(node)
            else:
                sources[key] = 1 if node is not None else 0
        manifest: Dict[str, Any] = {
            "role": spec.role,
            "system_id": meta.get("system_id"),
            "started_at": started_at,                     # run 级常量（§6）
            "run_id": meta.get("run_id"),                 # 派发锚定 run（P0-1b）
            "evidence_index_sha256": evidence_index_sha256,  # 派发时点锚点
            "generated_by": "su/detailed_doc.build_material_packages",
            "sources": sources,                           # AC3 来源计数
            "lens_status": dict(lens_status),             # 透镜事实（缺失即声明）
            "scrub_violations": [],                       # 上层检出后回填
        }
        if spec.role == "dev":
            # P0-3b：db_tables[].samples 采样值经 SU redact 管线/DataMasker 口径
            manifest["db_samples_masked_by"] = "DataMasker"
        packages[spec.role] = redact({"manifest": manifest, "data": data})
    return packages


def collect_package_violations(
    packages: Dict[str, RedactedDict],
) -> List[Tuple[str, str, str]]:
    """对五包执行 scrub_json_tree 复核并返回命中清单（纯函数）。

    Args:
        packages: build_material_packages 产物 {role: RedactedDict}。

    Returns:
        list[tuple[str, str, str]]: (role, json_path, 违规类别) 三元组列表
        （不含原文——扫描结果自身不得成为泄露面）。
    """
    hits: List[Tuple[str, str, str]] = []
    for role in sorted(packages):
        for json_path, rule in scrub_json_tree(packages[role]):
            hits.append((role, json_path, rule))
    return hits


def write_material_packages(
    paths: DetailedPaths, packages: Dict[str, RedactedDict]
) -> List[str]:
    """五包原子落盘 detailed/inputs/，返回写出的相对路径清单。

    每包：入口 isinstance(RedactedDict) 断言（红线①四层组合之②）→
    json.dumps(sort_keys=True, ensure_ascii=False, indent=2)（同输入
    逐字节稳定，NFR-SFD-002）→ 临时文件 + os.replace 原子写（E-6）。

    Args:
        paths: 详说路径集。
        packages: {role: RedactedDict} 素材包。

    Returns:
        list[str]: 相对 sys_root 的写出路径清单（排序稳定）。

    Raises:
        DetailedDocError: 任一包非 RedactedDict（未经脱敏管线，红线违例）。
    """
    paths.inputs_dir.mkdir(parents=True, exist_ok=True)
    written: List[str] = []
    for spec in _PACKAGE_SPECS:
        pkg = packages[spec.role]
        if not isinstance(pkg, RedactedDict):
            # 红线①：素材包构造唯一合法途径 = config.redact() 返回值
            raise DetailedDocError(
                "[SFD] 素材包 {0} 非 RedactedDict（未经统一脱敏管线），拒绝落盘".format(
                    spec.role),
                hints=["素材包必须经 config.redact() 产出后传入"])
        text = json.dumps(pkg, ensure_ascii=False, sort_keys=True, indent=2)
        target = paths.inputs_dir / "{0}.json".format(spec.role)
        write_text_atomic(target, text)
        written.append("{0}/inputs/{1}.json".format(DETAILED_DIRNAME, spec.role))
    return sorted(written)


# ---------------------------------------------------------------------------
# 大纲渲染器（REQ-SFD-003，ARCH §2.2.4）
# ---------------------------------------------------------------------------

def render_outline(paths: DetailedPaths, understanding: Dict[str, Any]) -> str:
    """渲染《系统功能与业务流程详说》8 节骨架 Markdown（纯函数）。

    内容（REQ-SFD-003）：头部 status: outline 标记行 + generated 行
    （run 级常量 started_at，禁止 time.time()）+ 引用规约说明段
    （E-n 语义、[推断] 标注约定、非法引用后果）+ 8 节标题。专家负责节写
    "> 待专家回填：<角色>（草稿文件：<路径>）"占位；自动节（7/8）写
    "> 本节由 --assemble 装配时自动生成"声明。Mermaid 预留块带语言标注
    （flowchart / sequenceDiagram / stateDiagram-v2，AC2——产品草稿据此
    附流程图与时序图；第 2 节另预留 stateDiagram-v2 供 2.4 状态机小节）。

    Args:
        paths: 详说路径集（占位行中的草稿路径用其绝对路径呈现）。
        understanding: understanding.json 全量 dict（meta 段供头部行）。

    Returns:
        str: 骨架 Markdown 全文（同输入逐字节稳定——幂等前置条件见
        ARCH §6 条款 6：understanding.json 字节不变）。
    """
    meta = understanding.get("meta") or {}
    system_id = meta.get("system_id") or ""
    started_at = meta.get("started_at")
    lines: List[str] = []
    lines.append("# 系统功能与业务流程详说（{0}）".format(system_id))
    lines.append("")
    # 头部 status 行：装配器据此做"骨架 vs 终稿"与 --force 保护判定（P1-5d）
    lines.append("<!-- {0} -->".format(_STATUS_LINE_OUTLINE))
    lines.append("<!-- generated: {0} -->".format(
        "（started_at 缺失）" if started_at is None else repr(float(started_at))))
    lines.append("")
    lines.append("> 本文档为 SU 专家详说阶段的 8 节**大纲骨架**：专家负责节为"
                 "\"待回填\"占位，装配（--assemble）后由五份专家草稿与装配层"
                 "自动节整体覆盖为终稿。与证据汇编 `UNDERSTANDING.md` 并存、"
                 "互链不合并（ADR-3）。")
    lines.append("")
    lines.append("## 证据引用规约")
    lines.append("")
    lines.append("- 每条业务结论句尾附证据编号 `（E-nnnn）`，编号 = "
                 "`UNDERSTANDING.md` 第 9 节 / `evidence/evidence-index.json` "
                 "的 `E{seq:04d}`（两处恒同源）；")
    lines.append("- 推荐锚注形态 `E0012(pages:3)`：括号内附证据 ref，装配器在"
                 "证据编号漂移场景下按 (seq, ref) 双键复核；")
    lines.append("- 无证据可引的推断显式标注 `[推断]`；")
    lines.append("- 装配器对不存在的编号改写为 `E-nnnn [未验证引用]`、对锚点"
                 "失效场景的同 seq 异 ref 引用改写为 `E-nnnn [漂移引用]`，"
                 "并全部计入 `detailed/assembly-report.json` 与第 7 节汇总。")
    lines.append("")
    # 节序固定遍历 SECTION_TITLES_SFD（幂等：遍历序即常量表序）
    # 键形态对齐（2026-09-30 测试专家审查修复 D3）：SECTION_SOURCE_SFD 的
    # 值为 "sections/<文件名>" 相对 detailed/ 的路径，而 spec.output_section
    # 是裸文件名——旧版直接用裸文件名做键，role_by_source.get(source) 恒
    # miss，大纲占位行退化为"待专家回填：（草稿文件：…）"，REQ-SFD-003
    # AC1"占位含负责角色"契约失守。补 "sections/" 前缀与节归属表对齐。
    role_by_source: Dict[str, str] = {
        "sections/" + spec.output_section: spec.role_label
        for spec in _PACKAGE_SPECS}
    for no in sorted(SECTION_TITLES_SFD):
        title = SECTION_TITLES_SFD[no]
        lines.append("## {0}. {1}".format(no, title))
        lines.append("")
        source = SECTION_SOURCE_SFD[no]
        if source == "__assembler__":
            lines.append("> 本节由 --assemble 装配时自动生成（低置信 findings "
                         "汇总 / 非法引用清单 / 证据索引附录），无需专家回填。")
        else:
            draft_abs = str((paths.detailed_dir / source).resolve())
            lines.append("> 待专家回填：{0}（草稿文件：{1}）".format(
                role_by_source.get(source, ""), draft_abs))
        if no == 2:
            # Mermaid 预留块（AC2 三种语言标注）：流程图 + 时序图 + 状态机
            lines.append("")
            lines.append("### 2.4 业务规则与状态机（由产品草稿承载）")
            lines.append("")
            lines.append("```flowchart")
            lines.append("%% 预留：核心业务流程图（产品专家据 edges/endpoints 绘制）")
            lines.append("```")
            lines.append("")
            lines.append("```sequenceDiagram")
            lines.append("%% 预留：端到端业务流程时序图（页面→API→表 链路）")
            lines.append("```")
            lines.append("")
            lines.append("```stateDiagram-v2")
            lines.append("%% 预留：状态字段枚举语义状态机（状态枚举来自采样值）")
            lines.append("```")
        if no == 5:
            lines.append("")
            lines.append("> 第 5 节取自 `04-data-semantics.doc.md` 草稿的 "
                         "`{0}` 标记后段（标记独占一行）。".format(SFD_SECTION5_MARKER))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_text_atomic(path: Path, text: str) -> None:
    """UTF-8 文本原子写：同目录临时文件（.tmp.<name>）写入 + os.replace。

    SIGINT 半程只留 .tmp.* 残差，上一版产物永不被覆写（PRD E-6）。

    Args:
        path: 目标文件路径（父目录自动创建）。
        text: 写入的完整文本。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / ".tmp.{0}".format(path.name)
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(str(tmp), str(path))


def _cleanup_tmp_residue(directory: Path) -> None:
    """清理指定目录下遗留的 .tmp.* 原子写残差（每次装配收尾调用）。

    Args:
        directory: 待清理目录（不存在时静默返回）。
    """
    if not directory.is_dir():
        return
    for item in directory.iterdir():
        if item.is_file() and item.name.startswith(".tmp."):
            try:
                item.unlink()
            except OSError:
                # 残差清理尽力而为（他进程持句柄等极端场景不阻断收口）
                pass


# ---------------------------------------------------------------------------
# 凭据扫描器（REQ-SFD-013，ARCH §2.2.6）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScanHit:
    """一处凭据命中（只记位置与类别，绝不携带原文——扫描报告自身不成泄露面）。"""

    location: str      # 相对路径 + 定位（md 文件给行号；json 给点分 key 路径）
    rule: str          # pii | url_userinfo | kv_credential | entropy_key


@dataclass(frozen=True)
class ScanReport:
    """扫描结果（hits 为全部命中；categories 为去重排序类别）。"""

    hits: List[ScanHit] = field(default_factory=list)

    @property
    def categories(self) -> List[str]:
        """去重排序的命中类别（exit 2 报告的"位置类别"输出源）。"""
        return sorted({hit.rule for hit in self.hits})


def scan_credential_leak(named_texts: Dict[str, str]) -> ScanReport:
    """对 {文件名: 文本} 集合执行四判据凭据扫描（终稿 + 素材包统一入口）。

    四判据（C1-C3 全部复用既有编译对象，判据口径与 SU 三层命中动作表对齐）：

      C1 pii：scrub_text(line) != line；
      C2 url_userinfo：_INLINE_USERINFO_RE（脱敏形态豁免，P1-10）；
      C3 kv_credential：_CLAIM_CREDENTIAL_RE（键值对形态，收窄口径）；
      C4 entropy_key（P0-3b）：JSON 结构中 dict 键名命中
         SENSITIVE_KEY_SFD_EXTRA 且字符串值命中 _HIGH_ENTROPY_TOKEN_RE
         双因子；md 文本无键名语境，C4 仅作用于 JSON walk。

    文件名以 ``.json`` 结尾时先 json.loads 再 walk（报点分 key 路径，
    C1-C3 同时逐行兜底）；解析失败按逐行文本兜底扫描（兜底模式 C4 不可用，
    如实降级为 C1-C3）。

    Args:
        named_texts: {逻辑文件名（用于定位报告）: 全文文本}。

    Returns:
        ScanReport: 全部命中（调用方 finalize_assembly 决定不落盘 + exit 2）。
    """
    hits: List[ScanHit] = []
    for name in sorted(named_texts):
        text = named_texts[name]
        lines = text.splitlines()
        # C1-C3 逐行扫描（md 与 json 统一执行，行号定位）
        for idx, line in enumerate(lines, start=1):
            if scrub_text(line) != line:
                hits.append(ScanHit(location="{0}:{1}".format(name, idx), rule="pii"))
            if _url_userinfo_hit(line):
                hits.append(ScanHit(location="{0}:{1}".format(name, idx),
                                    rule="url_userinfo"))
            if _CLAIM_CRED_SCAN_RE.search(line):
                hits.append(ScanHit(location="{0}:{1}".format(name, idx),
                                    rule="kv_credential"))
        # C4 仅对 JSON 文件做结构化键名 walk（md 无键名语境）
        if name.endswith(".json"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                # 解析失败按逐行文本兜底——兜底模式 C4 不可用（已执行 C1-C3）
                continue
            for json_path, rule in scrub_json_tree(parsed):
                if rule == "entropy_key":
                    hits.append(ScanHit(location="{0}:{1}".format(name, json_path),
                                        rule=rule))
    return ScanReport(hits=hits)


# ---------------------------------------------------------------------------
# --detailed-doc 主编排（ARCH §2.2.4 run_detailed_doc）
# ---------------------------------------------------------------------------

def _read_understanding_for_doc(paths: DetailedPaths) -> Dict[str, Any]:
    """读取 understanding.json（precheck 已验证可解析；装配路径独立复读）。

    Args:
        paths: 详说路径集。

    Returns:
        dict: understanding 全量数据。

    Raises:
        DetailedDocError: 读取/解析失败（装配路径不经过 precheck 时需自带兜底）。
    """
    try:
        data = json.loads(paths.understanding_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DetailedDocError(
            "[SFD] understanding.json 读取失败（{0}）".format(exc),
            hints=["该文件应为 SU --render-only 产物且未被手工改坏"])
    if not isinstance(data, dict):
        raise DetailedDocError("[SFD] understanding.json 顶层必须是 JSON 对象")
    return data


def _lens_status_from_understanding(understanding: Dict[str, Any]) -> Dict[str, str]:
    """从 understanding.json lenses 段提取四透镜状态（collected/skipped 事实）。

    lenses 段是 SU 渲染产物（详说只读，仅**读取状态字段**、不进任何素材包
    ——FORBIDDEN_PACKAGE_KEYS 约束的是包体内容，不禁止读取）。

    Args:
        understanding: understanding 全量 dict。

    Returns:
        dict: {lens 名: status}；段缺失时返回空 dict（诚实缺省）。
    """
    lenses = understanding.get("lenses")
    if not isinstance(lenses, dict):
        return {}
    out: Dict[str, str] = {}
    for lens in ("ui", "api", "db", "redis"):
        node = lenses.get(lens)
        if isinstance(node, dict) and node.get("status"):
            out[lens] = str(node["status"])
    return out


def run_detailed_doc(
    out_root: Path,
    system_id: str,
    store: Optional[Any] = None,
    skill_root: Optional[Path] = None,
) -> int:
    """--detailed-doc 主编排：前置校验 → 素材包 → 大纲 → 派发指引（REQ-SFD-001~005）。

    步骤（ARCH §2.2.4 伪代码逐行落实）：
      1. precheck_detailed_run（违例 DetailedDocError exit 2）；
      2. build_material_packages（含 manifest 锚点与来源计数）；
      3. 汇总 scrub_json_tree 违规 → 非空则**不落任何半成品** exit 2
         （凭据残留属上游产物异常，必须停机报告，绝不转录）；
      4. write_material_packages（原子写五包 + 创建空 sections/ 目录）；
      5. write_text_atomic 大纲骨架（status: outline，幂等）；
      6. stdout 打印派发指引（§3.4 格式，绝对路径）；
      既有终稿 status: final 保护在 CLI 层判定（--force 语义，P1-5d）。

    Args:
        out_root: 输出根目录（--out）。
        system_id: 输出子目录名（--system-id）。
        store: duck-typed 只读状态库（read_latest_run/stats；None=不可考）。
        skill_root: skill 根目录（派发指引中 prompt 文档绝对路径的锚点；
            None 时按本文件位置推导 scripts/.. 上一级）。

    Returns:
        int: 0 成功（DetailedDocError 由调用方 main() 收口为 exit 2）。
    """
    ctx = precheck_detailed_run(out_root, system_id, store=store)
    paths: DetailedPaths = ctx["paths"]
    understanding: Dict[str, Any] = ctx["understanding"]
    started_at = ctx["started_at"]

    # 派发锚点：当前 evidence-index.json 文件 sha256（P0-1b，装配期复核）
    index_sha256 = hashlib.sha256(paths.evidence_index.read_bytes()).hexdigest()

    packages = build_material_packages(
        understanding, started_at,
        _lens_status_from_understanding(understanding),
        evidence_index_sha256=index_sha256)

    # scrub 复核（§8.2 双保险）：命中即整批拒写——不落任何半成品
    violations = collect_package_violations(packages)
    if violations:
        # 违规清单只含 (role, key 路径, 类别)，绝不携带原文片段
        detail = "、".join("{0}:{1}[{2}]".format(r, p, c)
                           for r, p, c in violations[:20])
        raise DetailedDocError(
            "[SFD] 素材包 scrub 复核命中疑似凭据残留（共 {0} 处：{1}）——"
            "上游 SU 产物异常，必须停止并人工排查，绝不转录".format(
                len(violations), detail),
            hints=["核对 understanding.json / evidence-index.json 是否被手工"
                   "注入过明文凭据；确认后重跑 SU --render-only 收口"])

    # manifest 回填违规清单后落盘（正常路径恒空列表——命中路径已在上方退出；
    # 保留字段结构使 manifest 契约稳定）
    for pkg in packages.values():
        pkg["manifest"]["scrub_violations"] = []
    write_material_packages(paths, packages)
    # sections/ 空目录由 --detailed-doc 创建（草稿文件名即契约，§5.1 注）
    paths.sections_dir.mkdir(parents=True, exist_ok=True)

    # 大纲骨架（status: outline；装配后被终稿覆盖）
    write_text_atomic(paths.final_doc, render_outline(paths, understanding))

    # ---- 派发指引（§3.4 逐字格式；路径一律 Path.resolve() 绝对路径）----
    if skill_root is None:
        # 本文件位于 <skill根>/scripts/su/detailed_doc.py → parents[2] = skill 根
        skill_root = Path(__file__).resolve().parents[2]
    out_abs = str(Path(out_root).resolve())
    status_note = "" if ctx["anchored_run_is_latest"] else "（锚定 run 非最新行）"
    run_status_display = ctx["run_status"] if ctx["run_status"] else "不可考"
    print("[SFD] 前置校验通过 system={0} anchored_run={1} run_status={2}{3}"
          " started_at={4}".format(system_id, ctx["run_id"], run_status_display,
                                   status_note, started_at))
    for note in ctx["notes"]:
        print("[SFD] 声明：{0}".format(note))
    print("[SFD] 素材包（5）：")
    for spec in _PACKAGE_SPECS:
        print("  - {0}".format(str((paths.inputs_dir / "{0}.json".format(spec.role)).resolve())))
    print("[SFD] 大纲骨架：{0}（8 节；专家节为占位，装配后被终稿覆盖）".format(
        str(paths.final_doc.resolve())))
    print("[SFD] 专家派发（宿主 LLM 并行执行，prompt 路径相对 skill 根目录）：")
    for spec in _PACKAGE_SPECS:
        print("  - 角色={0}   prompt={1}".format(
            spec.role_label, str((Path(skill_root) / spec.prompt_doc).resolve())))
        print("    素材包={0}".format(
            str((paths.inputs_dir / "{0}.json".format(spec.role)).resolve())))
        print("    输出={0}".format(str((paths.sections_dir / spec.output_section).resolve())))
    print("[SFD] 装配命令：python scripts/system_understanding.py "
          "--out {0} --system-id {1} --assemble".format(out_abs, system_id))
    print("[SFD] 下一步：将上述五条派发分别交给对应专家子代理（可并行），"
          "草稿齐备（或接受降级）后执行装配命令。")
    return 0


# ---------------------------------------------------------------------------
# 装配器（REQ-SFD-004，ARCH §2.2.5）
# ---------------------------------------------------------------------------

@dataclass
class DraftOutcome:
    """单个专家草稿的装配结果（assembly-report 的 sections 条目数据源）。"""

    section_no: int
    source_file: str        # 相对 detailed/ 的路径；自动节为 "__assembler__"
    status: str = "degraded"  # ok | degraded | auto
    body: str = ""          # 改写后的节正文（degraded 时空串，装配层替换声明）
    ref_total: int = 0        # 草稿原文 E-n token 总数（改写前统计，P0-4a）
    ref_invalid: int = 0      # seq ∉ evidence-index 集合的 token 数
    ref_drifted: int = 0      # 同 seq 不同 ref 的"漂移引用"数（P0-1a）
    degraded_reason: str = ""  # 降级原因（report degraded_reasons 数据源）
    invalid_refs: List[str] = field(default_factory=list)   # 非法 token 原文列表
    drifted_refs: List[str] = field(default_factory=list)   # 漂移 token 原文列表


def load_evidence_index(evidence_index_path: Path) -> Dict[int, str]:
    """读 evidence/evidence-index.json，返回 seq→ref 映射 {seq: ref}。

    条目结构 {"seq","ref","description"}（document_renderer._index_evidence
    同源）。展示层编号口径 E{n:04d}（如 E0012）→ token 解析后 int 归一比对，
    幂等稳定。（P0-1a：升级为 seq→ref 双键映射，可检出"同 seq 不同 ref"的
    跨 render 漂移。）

    Args:
        evidence_index_path: evidence-index.json 路径。

    Returns:
        dict[int, str]: {seq: ref}；entries 缺失/形态异常条目跳过。

    Raises:
        DetailedDocError: 文件不可读/非法 JSON/顶层非对象。
    """
    try:
        data = json.loads(Path(evidence_index_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DetailedDocError(
            "[SFD] evidence-index.json 读取失败（{0}）".format(exc),
            hints=["该文件应为 SU --render-only 产物且未被手工改坏"])
    entries = data.get("entries") if isinstance(data, dict) else None
    mapping: Dict[int, str] = {}
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            seq_raw = entry.get("seq")
            ref = entry.get("ref")
            try:
                seq = int(seq_raw)
            except (TypeError, ValueError):
                continue  # 形态异常条目跳过（装配是软校验面）
            if ref is not None:
                mapping[seq] = str(ref)
    return mapping


def _split_section5_from_dev_draft(text: str) -> Tuple[str, Optional[str], int]:
    """把 04 走读草稿按 SFD-SECTION: 5 标记切分第 4/5 节。

    口径（P0-4b）：标记出现 0 次 → 第 5 节缺失（返回 None 由调用方降级）；
    出现 ≥2 次 → 草稿意图把同一内容灌进两节，逐段切分必然产生重复正文，
    **整体降级比静默截取安全**（返回 marker_count 供调用方判定）。

    Args:
        text: 04 草稿全文。

    Returns:
        tuple[str, Optional[str], int]: (第 4 节正文, 第 5 节正文或 None,
        标记出现次数)。
    """
    marker_count = text.count(SFD_SECTION5_MARKER)
    if marker_count == 0:
        return text, None, 0
    if marker_count >= 2:
        return "", None, marker_count
    head, _, tail = text.partition(SFD_SECTION5_MARKER)
    return head.rstrip(), tail.strip(), marker_count


def _extract_first_nonempty_line(text: str) -> Optional[str]:
    """草稿首个非空行提取（首部空行容错；'---' front matter 整体跳过）。

    P0-4c 容错口径：允许首部空行与 '---' 开头的 YAML front matter 块
    （两个 '---' 之间的内容整体跳过）；返回 front matter 之后的首个非空行。

    Args:
        text: 草稿全文。

    Returns:
        Optional[str]: 首个非空行（无内容时 None）。
    """
    lines = text.splitlines()
    idx = 0
    # 首部空行容错
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    # front matter 整体跳过（首个非空行为 '---' 时找闭合 '---'）
    if idx < len(lines) and lines[idx].strip() == "---":
        idx += 1
        while idx < len(lines) and lines[idx].strip() != "---":
            idx += 1
        if idx < len(lines):
            idx += 1  # 跳过闭合 '---'
        while idx < len(lines) and not lines[idx].strip():
            idx += 1
    if idx >= len(lines):
        return None
    return lines[idx].strip()


def _validate_draft_head(text: str, section_no: int) -> Optional[str]:
    """首行节头校验（首个非空行必须与 SECTION_TITLES_SFD 完全一致）。

    第 5 节特殊口径（P0-4c 配套）：04 草稿的标记后段（第 5 节正文）**不
    重复校验节头**——节头校验只作用于各草稿文件本体；本函数仅供草稿文件
    级校验调用，第 5 节由调用方按"标记段非空"判降级。

    Args:
        text: 草稿全文。
        section_no: 该草稿主归属节号（比对 SECTION_TITLES_SFD）。

    Returns:
        Optional[str]: 违例中文描述；None = 校验通过。
    """
    first = _extract_first_nonempty_line(text)
    expected = "# {0}. {1}".format(section_no, SECTION_TITLES_SFD[section_no])
    if first is None:
        return "草稿为空文件（无标题行）"
    if first != expected:
        return "节头不符（期望 {0!r} 实际 {1!r}）".format(expected, first)
    return None


def _rewrite_evidence_tokens(
    text: str,
    evidence_map: Dict[int, str],
    check_drift: bool,
) -> Tuple[str, int, int, int, List[str], List[str]]:
    """对草稿正文执行 E-n 引用校验与改写（改写前统计，P0-4a）。

    统计与改写规则（ARCH §2.2.5 ③/漂移校验段逐字落实）：

      - ref_total = EVIDENCE_REF_TOKEN_RE 在**草稿原文**上的 token 总数
        （改写前统计，第 7/8 节自动内容恒 0 且不进本函数）；
      - 非法 token（int ∉ evidence_map 键集）→ 原文保留、逐处改写为
        "E0012 [未验证引用]"（已带标记不重复加）并计数（AC2）；
      - 漂移（check_drift=True 时）：token 带锚注 E0012(pages:3) 且
        evidence_map[12] != "pages:3" → 改写为 "E0012 [漂移引用]" 并计数；
        无锚注的合法 token 无法判定时仅由锚点声明面（第 7 节）兜底。

    Args:
        text: 草稿正文（专家原文）。
        evidence_map: load_evidence_index 产物 {seq: ref}。
        check_drift: 派发锚点失配（drift_suspected）时 True。

    Returns:
        tuple: (改写后正文, ref_total, ref_invalid, ref_drifted,
        非法 token 原文列表, 漂移 token 原文列表)。
    """
    tokens = EVIDENCE_REF_TOKEN_RE.findall(text)
    ref_total = len(tokens)
    invalid: List[str] = []
    drifted: List[str] = []

    def _sub_invalid(match: "re.Match") -> str:
        """C-非法 token 改写回调：不在合法集合 → 加 [未验证引用] 标记。"""
        seq = int(match.group(1))
        if seq not in evidence_map:
            invalid.append(match.group(0))
            return "{0} [未验证引用]".format(match.group(0))
        return match.group(0)

    def _sub_drift(match: "re.Match") -> str:
        """漂移改写回调：带锚注且锚注 ≠ 当前映射 → 加 [漂移引用] 标记。"""
        seq = int(match.group(1))
        anchor = match.group(2)
        if seq in evidence_map and anchor != evidence_map[seq]:
            drifted.append(match.group(0))
            return "E{0:04d} [漂移引用]".format(seq)
        return match.group(0)

    # 先做漂移改写（锚注形态优先），再做非法改写；改写均在统计之后
    rewritten = text
    if check_drift:
        rewritten = _E_REF_WITH_ANCHOR_RE.sub(_sub_drift, rewritten)
    rewritten = EVIDENCE_REF_TOKEN_RE.sub(_sub_invalid, rewritten)
    return rewritten, ref_total, len(invalid), len(drifted), invalid, drifted


def _build_auto_section7(
    understanding: Dict[str, Any],
    outcomes: Dict[int, DraftOutcome],
    evidence_map: Dict[int, str],
    drift_suspected: bool,
) -> str:
    """装配层生成第 7 节"未验证推断与附录"（自动节，统计恒 0）。

    内容（ARCH §2.2.4/§2.2.5 定稿）：全部 confidence=low findings
    （claim+E-n+finding_id）∪ 全部非法引用清单 ∪ 全部漂移引用清单；
    drift_suspected=True → 节首**强制声明**"证据编号可能漂移，引用需复核"
    （P0-1b）。

    Args:
        understanding: understanding 全量 dict（findings 段数据源）。
        outcomes: 装配中已产出的 {节号: DraftOutcome}（引用清单数据源）。
        evidence_map: {seq: ref} 映射（低置信 finding 的 '<table>:<id>'
            引用反查 E-n 展示编号用）。
        drift_suspected: 派发锚点失配标记。

    Returns:
        str: 第 7 节正文（不含节标题行）。
    """
    # ref→seq 反查表（同一 ref 多次登记只取首个 seq——_index_evidence
    # 同 ref 去重保证正常路径一一对应，防御性取首不报错）
    ref_by_ref: Dict[str, int] = {}
    for seq, ref in evidence_map.items():
        ref_by_ref.setdefault(ref, seq)
    lines: List[str] = []
    if drift_suspected:
        lines.append("> **漂移声明**：派发锚点（素材包 manifest 记录的 run_id /"
                     " evidence-index sha256）与当前 SU 产物不一致，本文引用所"
                     "依据的证据编号可能已跨 render 漂移，**引用需复核**（以 "
                     "UNDERSTANDING.md 第 9 节现行编号为准）。")
        lines.append("")
    lines.append("### 低置信结论汇总（confidence=low，⚠ 待人工确认）")
    lines.append("")
    low_items = [f for f in (understanding.get("findings") or [])
                 if isinstance(f, dict) and f.get("confidence") == "low"]
    if low_items:
        for f in low_items:
            # 证据引用展示为 E{seq:04d}（编号）+（原始 ref）双形态：低置信
            # finding 的 evidence_refs 是 '<table>:<id>' 状态库引用，经
            # evidence_map（seq→ref 反查）换回详说终稿的 E-n 展示编号
            refs: List[str] = []
            for raw_ref in (f.get("evidence_refs") or []):
                ref_str = str(raw_ref)
                seq = ref_by_ref.get(ref_str)
                refs.append("E{0:04d}（{1}）".format(seq, ref_str)
                            if seq is not None else ref_str)
            lines.append("- [{0}] {1}（证据引用：{2}）".format(
                f.get("finding_id"), f.get("claim"), "、".join(refs) or "（无）"))
    else:
        lines.append("- 无低置信结论")
    lines.append("")
    lines.append("### 未验证引用清单（草稿引用了 evidence-index 不存在的编号）")
    lines.append("")
    invalid_lines: List[str] = []
    for no in sorted(outcomes):
        for tok in outcomes[no].invalid_refs:
            invalid_lines.append("- 第 {0} 节：`{1}`".format(no, tok))
    lines.extend(invalid_lines or ["- 无未验证引用"])
    lines.append("")
    lines.append("### 漂移引用清单（同 seq 不同 ref：编号指向已跨 render 漂移）")
    lines.append("")
    drift_lines: List[str] = []
    for no in sorted(outcomes):
        for tok in outcomes[no].drifted_refs:
            drift_lines.append("- 第 {0} 节：`{1}`".format(no, tok))
    lines.extend(drift_lines or ["- 无漂移引用"])
    return "\n".join(lines)


def _build_auto_section8(
    paths: DetailedPaths,
    understanding: Dict[str, Any],
    evidence_map: Dict[int, str],
    started_at: Any,
) -> str:
    """装配层生成第 8 节"附录：证据索引与运行说明"（自动节，统计恒 0）。

    内容：evidence-index 表（编号/引用/说明，编号与 UNDERSTANDING.md 第 9 节
    同源同形态 E{seq:04d}）+ 五包 manifest 来源计数汇总 + 脱敏/方法论常量
    声明（措辞与 UNDERSTANDING.md 第 9 节区分阅读目的，ADR-3）。

    Args:
        paths: 详说路径集（evidence-index / manifest 数据源）。
        understanding: understanding 全量 dict（system_id 展示）。
        evidence_map: {seq: ref}（编号表数据源）。
        started_at: run 级时间常量（运行说明行）。

    Returns:
        str: 第 8 节正文（不含节标题行）。
    """
    lines: List[str] = []
    # 证据索引表：description 需回读 evidence-index.json 原文（seq→ref 已够
    # 编号核对；说明列提升可读性，读失败时降级为仅编号+引用两列）
    descriptions: Dict[int, str] = {}
    try:
        raw = json.loads(paths.evidence_index.read_text(encoding="utf-8"))
        for entry in (raw.get("entries") or []):
            if isinstance(entry, dict):
                try:
                    descriptions[int(entry.get("seq"))] = str(entry.get("description") or "")
                except (TypeError, ValueError):
                    continue
    except (OSError, json.JSONDecodeError):
        pass
    lines.append("证据编号对照表（与 `UNDERSTANDING.md` 第 9 节 / "
                 "`evidence/evidence-index.json` 恒同源；本节供详说终稿读者"
                 "就地查阅）：")
    lines.append("")
    lines.append("| 证据编号 | 状态库引用 | 说明 |")
    lines.append("|---|---|---|")
    if evidence_map:
        for seq in sorted(evidence_map):
            lines.append("| E{0:04d} | `{1}` | {2} |".format(
                seq, evidence_map[seq], descriptions.get(seq, "")))
    else:
        lines.append("| - | - | evidence-index 为空（SU 产物异常或无引用记录） |")
    lines.append("")
    lines.append("### 素材包 manifest 汇总（派发时点来源计数）")
    lines.append("")
    for spec in _PACKAGE_SPECS:
        pkg_path = paths.inputs_dir / "{0}.json".format(spec.role)
        if not pkg_path.is_file():
            lines.append("- `{0}.json`：缺失（装配时素材包不在位）".format(spec.role))
            continue
        try:
            manifest = json.loads(pkg_path.read_text(encoding="utf-8")).get("manifest") or {}
        except (OSError, json.JSONDecodeError):
            lines.append("- `{0}.json`：manifest 不可解析（如实登记）".format(spec.role))
            continue
        sources = manifest.get("sources") or {}
        lines.append("- `{0}.json`（run_id={1}）：{2}".format(
            spec.role, manifest.get("run_id"),
            "、".join("{0}={1}".format(k, v) for k, v in sorted(sources.items()))
            or "（无来源计数）"))
    lines.append("")
    lines.append("### 脱敏与运行说明")
    lines.append("")
    lines.append("- 本终稿全部输入为 SU 已脱敏产物（统一 redact 管线 + "
                 "DataMasker 列级采样脱敏），详说阶段**零网络、零 LLM 调用、"
                 "零凭据面**（脚本层）；")
    lines.append("- 语义内容由五位专家子代理基于素材包与 UNDERSTANDING.md 撰写，"
                 "脚本层只做引用编号校验（软校验，ARCH ADR-4）与凭据扫描收口；")
    lines.append("- 运行常量：system_id=`{0}`，started_at={1}（本文全部时间戳"
                 "唯一真相源，幂等口径见 ARCH-SFD-001 §6）。".format(
                     (understanding.get("meta") or {}).get("system_id"), started_at))
    lines.append("- 证据级逐条核验请阅读《系统功能理解文档》`UNDERSTANDING.md`"
                 "（证据汇编）；本文是叙述性通读文档，两者并存互链（ADR-3）。")
    return "\n".join(lines)


def assemble_final_doc(
    paths: DetailedPaths,
    understanding: Dict[str, Any],
    evidence_map: Dict[int, str],
    drift_suspected: bool,
) -> Tuple[str, Dict[str, Any]]:
    """装配终稿与报告（纯函数：输入 → (终稿文本, report dict)，不落盘）。

    伪代码落实（ARCH §2.2.5）：
      - 逐节读 sections/ 草稿（04 草稿按标记切第 5 节；标记 ≥2 次 →
        第 4/5 节**同时** degraded，degraded_reason="SFD-SECTION:5 标记多写"）；
      - 首行节头校验（空行/front matter 容错，首个非空行须与
        SECTION_TITLES_SFD 完全一致，否则 degraded）；
      - 缺失/空文件 → degraded，正文替换"素材不足/角色未完成"声明（AC1）；
      - E-n 引用统计范围 = **专家草稿原文（改写前）**；第 7/8 节自动内容
        恒 0 并在统计之后生成（P0-4a 防自污染）；
      - report 双记 outline_sha256（以当前 understanding 重渲染骨架）与
        outline_sha256_on_disk（磁盘骨架），不等 ⇒ outline_modified=true
        不阻塞（P1-7/P2-14）；时间戳全部取 run 级常量 started_at。

    Args:
        paths: 详说路径集。
        understanding: understanding 全量 dict。
        evidence_map: load_evidence_index 产物 {seq: ref}。
        drift_suspected: 派发锚点失配标记（第 7 节强制漂移声明开关）。

    Returns:
        tuple[str, dict]: (终稿 Markdown 全文, assembly-report dict)。
    """
    started_at = (understanding.get("meta") or {}).get("started_at")

    # ---- 骨架 sha256 双记录（P1-7a/P2-14）----
    outline_disk_path = paths.final_doc
    outline_sha256_on_disk = ""
    disk_header = ""
    if outline_disk_path.is_file():
        disk_bytes = outline_disk_path.read_bytes()
        outline_sha256_on_disk = hashlib.sha256(disk_bytes).hexdigest()
        disk_header = disk_bytes.decode("utf-8", errors="replace")
    outline_rerendered = render_outline(paths, understanding)
    outline_sha256 = hashlib.sha256(outline_rerendered.encode("utf-8")).hexdigest()
    outline_modified = bool(outline_sha256_on_disk) and (
        outline_sha256_on_disk != outline_sha256)

    outcomes: Dict[int, DraftOutcome] = {}

    # ---- 第 1 步：逐节装载草稿并统计/改写（自动节 7/8 移至统计之后）----
    for no in sorted(SECTION_TITLES_SFD):
        source = SECTION_SOURCE_SFD[no]
        if source == "__assembler__":
            continue  # 自动节不参与草稿校验
        outcomes[no] = DraftOutcome(section_no=no, source_file=source)

    # 04 草稿（第 4/5 节共同来源）的特殊切分处理（P0-4b）
    dev_rel = SECTION_SOURCE_SFD[4]
    dev_path = paths.detailed_dir / dev_rel
    dev_text: Optional[str] = None
    if dev_path.is_file():
        dev_text = dev_path.read_text(encoding="utf-8")

    if dev_text is None or not dev_text.strip():
        # 04 草稿缺失/为空 → 第 4/5 节双双降级（同一来源共同失败）
        for no in (4, 5):
            outcomes[no].status = "degraded"
            outcomes[no].degraded_reason = "草稿缺失或为空（素材不足/角色未完成）"
    else:
        dev_marker_count = dev_text.count(SFD_SECTION5_MARKER)
        if dev_marker_count >= 2:
            # 标记多写 → 第 4/5 节同时整体降级（重复正文风险拒收）
            for no in (4, 5):
                outcomes[no].status = "degraded"
                outcomes[no].degraded_reason = "SFD-SECTION:5 标记多写"
        else:
            head, tail, _count = _split_section5_from_dev_draft(dev_text)
            # 第 4 节 = 标记前段（草稿文件本体，做节头校验）
            o4 = outcomes[4]
            head_problem = _validate_draft_head(dev_text, 4)
            if head_problem is not None:
                o4.status = "degraded"
                o4.degraded_reason = head_problem
            else:
                o4.status = "ok"
            rewritten, total, n_invalid, n_drift, inv, drf = \
                _rewrite_evidence_tokens(head, evidence_map, drift_suspected)
            o4.body = rewritten
            o4.ref_total, o4.ref_invalid, o4.ref_drifted = total, n_invalid, n_drift
            o4.invalid_refs, o4.drifted_refs = inv, drf
            # 第 5 节 = 标记后段（无标记 → 第 5 节 degraded；有标记 → ok）
            o5 = outcomes[5]
            if tail is None:
                o5.status = "degraded"
                o5.degraded_reason = "缺少 <!-- SFD-SECTION: 5 --> 标记（第 5 节起点未标注）"
            elif not tail.strip():
                o5.status = "degraded"
                o5.degraded_reason = "标记后段为空（第 5 节内容未完成）"
            else:
                o5.status = "ok"
                # 标记后段非空才做引用统计（第 5 节正文无草稿文件级节头，
                # 节头校验只作用于 04 草稿本体=第 4 节首行，P0-4c 配套口径）
                rewritten5, total5, n_inv5, n_drf5, inv5, drf5 = \
                    _rewrite_evidence_tokens(tail, evidence_map, drift_suspected)
                o5.body = rewritten5
                o5.ref_total, o5.ref_invalid, o5.ref_drifted = total5, n_inv5, n_drf5
                o5.invalid_refs, o5.drifted_refs = inv5, drf5

    # 其余单一来源草稿（1/2/3/6 节）
    for no in (1, 2, 3, 6):
        outcome = outcomes[no]
        draft_path = paths.detailed_dir / outcome.source_file
        if not draft_path.is_file():
            outcome.status = "degraded"
            outcome.degraded_reason = "草稿文件缺失（素材不足/角色未完成）"
            continue
        text = draft_path.read_text(encoding="utf-8")
        if not text.strip():
            outcome.status = "degraded"
            outcome.degraded_reason = "草稿为空文件（素材不足/角色未完成）"
            continue
        problem = _validate_draft_head(text, no)
        if problem is not None:
            outcome.status = "degraded"
            outcome.degraded_reason = problem
        else:
            # 节头不符仍做引用统计（降级不崩，NFR-SFD-004；正文用降级声明替换）
            outcome.status = "ok"
        rewritten, total, n_invalid, n_drift, inv, drf = \
            _rewrite_evidence_tokens(text, evidence_map, drift_suspected)
        outcome.body = rewritten
        outcome.ref_total, outcome.ref_invalid, outcome.ref_drifted = \
            total, n_invalid, n_drift
        outcome.invalid_refs, outcome.drifted_refs = inv, drf

    # ---- 第 2 步：自动节 7/8 在草稿统计**之后**生成（防自污染，P0-4a）----
    section7_body = _build_auto_section7(understanding, outcomes, evidence_map,
                                         drift_suspected)
    section8_body = _build_auto_section8(paths, understanding, evidence_map, started_at)
    outcomes[7] = DraftOutcome(section_no=7, source_file="__assembler__",
                               status="auto")
    outcomes[8] = DraftOutcome(section_no=8, source_file="__assembler__",
                               status="auto")

    # ---- 第 3 步：按 SECTION_TITLES_SFD 顺序拼接终稿（头部行=run 级常量）----
    body_lines: List[str] = []
    system_id = (understanding.get("meta") or {}).get("system_id") or ""
    body_lines.append("# 系统功能与业务流程详说（{0}）".format(system_id))
    body_lines.append("")
    body_lines.append("<!-- {0} -->".format(_STATUS_LINE_FINAL))
    body_lines.append("<!-- generated: {0} -->".format(
        "（started_at 缺失）" if started_at is None else repr(float(started_at))))
    body_lines.append("")
    body_lines.append("> 本终稿由 SU 专家详说阶段（post-render）装配产出；"
                     "证据级逐条核验请阅读同目录 `UNDERSTANDING.md`（证据汇编）。"
                     "装配明细与引用合法率见 `detailed/assembly-report.json`。")
    body_lines.append("")
    degraded_sections: List[int] = []
    degraded_reasons: Dict[str, str] = {}
    for no in sorted(SECTION_TITLES_SFD):
        body_lines.append("## {0}. {1}".format(no, SECTION_TITLES_SFD[no]))
        body_lines.append("")
        outcome = outcomes[no]
        if no == 7:
            body_lines.append(section7_body)
        elif no == 8:
            body_lines.append(section8_body)
        elif outcome.status == "ok":
            body_lines.append(getattr(outcome, "body", "").strip())
        else:
            degraded_sections.append(no)
            degraded_reasons[str(no)] = outcome.degraded_reason
            body_lines.append("> **[降级]** 素材不足/角色未完成：{0}——"
                              "补齐草稿 `{1}` 后重跑 --assemble 即可修复。".format(
                                  outcome.degraded_reason, outcome.source_file))
        body_lines.append("")
    body = "\n".join(body_lines).rstrip() + "\n"

    # ---- 第 4 步：assembly-report（§5.3 结构，全部新增字段齐备）----
    ref_total = sum(o.ref_total for o in outcomes.values())
    ref_invalid = sum(o.ref_invalid for o in outcomes.values())
    ref_drifted = sum(o.ref_drifted for o in outcomes.values())
    ref_valid = ref_total - ref_invalid - ref_drifted
    legality = (ref_valid / ref_total) if ref_total > 0 else 1.0
    # inputs_manifest_digest：五包内容 sha256（装配时点磁盘内容，审计锚点）
    inputs_digest: Dict[str, str] = {}
    for spec in _PACKAGE_SPECS:
        pkg_path = paths.inputs_dir / "{0}.json".format(spec.role)
        if pkg_path.is_file():
            inputs_digest["{0}.json".format(spec.role)] = \
                hashlib.sha256(pkg_path.read_bytes()).hexdigest()
    report: Dict[str, Any] = {
        "system_id": system_id,
        "started_at": started_at,
        "assembled_at_section_basis": "started_at",  # 时间戳策略声明（run 级常量）
        "sections": [
            {
                "section_no": o.section_no,
                "title": SECTION_TITLES_SFD[o.section_no],
                "source": o.source_file,
                "status": o.status,
                "ref_total": o.ref_total,
                "ref_invalid": o.ref_invalid,
                "ref_drifted": o.ref_drifted,
                "invalid_refs": list(o.invalid_refs),
            }
            for o in (outcomes[n] for n in sorted(SECTION_TITLES_SFD))
        ],
        "degraded_sections": degraded_sections,
        "degraded_reasons": degraded_reasons,
        # 口径=专家草稿原文 token（P0-4a；自动节恒 0）
        "ref_total": ref_total,
        "ref_valid": ref_valid,
        "ref_invalid": ref_invalid,
        "ref_drifted": ref_drifted,
        "ref_legality_rate": round(legality, 6),
        "ref_legality_target": REF_LEGALITY_TARGET,
        "drift_suspected": bool(drift_suspected),          # 派发锚点失配（P0-1b）
        "outline_modified": outline_modified,              # 骨架手改检测（P2-14）
        "credential_scan": {"status": "pending", "categories": []},
        # P1-7a：outline_sha256 = 装配时以当前 understanding 重渲染骨架的 sha256
        "outline_sha256": outline_sha256,
        "outline_sha256_on_disk": outline_sha256_on_disk,
        "inputs_manifest_digest": inputs_digest,
    }
    return body, report


def finalize_assembly(paths: DetailedPaths, body: str, report: Dict[str, Any]) -> int:
    """落盘收口：凭据扫描 →（通过才）原子写终稿 + 报告（REQ-SFD-013 AC1）。

    命中动作：终稿与报告**都不落盘**（上一版保持不动，PRD E-5/E-6），
    stdout/stderr 打印命中位置类别（文件 + 定位 + 类别，绝不含原文片段）。
    报告在扫描通过后才落盘——保证"报告存在 ⇔ 终稿为该报告对应版本"的一致性。

    Args:
        paths: 详说路径集。
        body: 终稿全文（assemble_final_doc 产物）。
        report: assembly-report dict（credential_scan 字段在本函数内收口）。

    Returns:
        int: 0 落盘成功；2 凭据扫描命中（不落盘）。
    """
    # 扫描对象 = 终稿全文 + 五包文本（统一四判据入口；md 文本 C4 不适用，
    # JSON 文本 C4 走结构化 walk，ARCH §8.2 终稿扫描层口径）
    named: Dict[str, str] = {"SYSTEM_FUNCTION_DOC.md": body}
    for spec in _PACKAGE_SPECS:
        pkg_path = paths.inputs_dir / "{0}.json".format(spec.role)
        if pkg_path.is_file():
            named["{0}/inputs/{1}.json".format(DETAILED_DIRNAME, spec.role)] = \
                pkg_path.read_text(encoding="utf-8")
    scan = scan_credential_leak(named)
    if scan.hits:
        # 位置类别报告（绝不携带原文片段——扫描输出自身不成泄露面）
        for hit in scan.hits:
            print("[SFD] 凭据扫描命中：{0} [{1}]".format(hit.location, hit.rule))
        print("[SFD] 终稿与报告均不落盘（上一版保持不动），退出码 2", flush=True)
        return 2
    report["credential_scan"] = {"status": "clean", "categories": scan.categories}
    # 原子写终稿 + 报告（临时文件+rename，E-6；SIGINT 半成品不覆写上一版）
    write_text_atomic(paths.final_doc, body)
    write_text_atomic(
        paths.detailed_dir / ASSEMBLY_REPORT_FILENAME,
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    # 清理原子写 .tmp.* 残差（§5.1 注：每次 --assemble 收尾）
    _cleanup_tmp_residue(paths.sys_root)
    _cleanup_tmp_residue(paths.detailed_dir)
    _cleanup_tmp_residue(paths.inputs_dir)
    return 0


def check_existing_final(paths: DetailedPaths, force: bool) -> None:
    """既有终稿保护：头部 status: final 且未 --force → DetailedDocError exit 2。

    与 --detailed-doc / --assemble 两模式共用（P1-5d：防误覆盖已交付终稿）。

    Args:
        paths: 详说路径集。
        force: CLI --force（True 时跳过保护直接放行）。

    Raises:
        DetailedDocError: 终稿在位且未 --force（exit_code=2）。
    """
    if force or not paths.final_doc.is_file():
        return
    try:
        head = paths.final_doc.read_text(encoding="utf-8")[:2000]
    except OSError:
        return  # 读不到头部（权限等）保守放行由后续原子写自然报错收口
    if _STATUS_LINE_FINAL in head:
        raise DetailedDocError(
            "[SFD] 既有终稿（头部 status: final）在位：{0}——覆盖已交付终稿"
            "需显式 --force".format(paths.final_doc),
            hints=["确认要重装配覆盖时追加 --force 重跑本命令"])


def run_assemble(
    out_root: Path,
    system_id: str,
    force: bool = False,
    store: Optional[Any] = None,
) -> int:
    """--assemble 主编排：轻校验 → 漂移锚点判定 → 装配 → finalize（REQ-SFD-004）。

    轻校验口径（ARCH §2.2.5 run_assemble，独立于 --detailed-doc）：
      - detailed/inputs/、sections/、understanding.json、evidence-index.json
        必须存在 → 否则 exit 2 缺项提示；
      - 既有终稿 status: final → 未 --force 时 exit 2（check_existing_final）；
      - 漂移锚点判定（P0-1b）：读五包 manifest 记录的 run_id 与
        evidence_index_sha256，与当前 understanding.json meta.run_id /
        evidence-index.json 文件 sha256 比对 → 任一不一致（或 manifest 缺
        锚点字段）→ drift_suspected=True 传入 assemble_final_doc；
      - UNDERSTANDING.md 缺失 → 只降级声明不阻断（装配仅消费 json+index）；
      - 不重做 findings 非空 / run 状态校验（严肃性由 --detailed-doc 把关）。

    Args:
        out_root: 输出根目录（--out）。
        system_id: 输出子目录名（--system-id）。
        force: 覆盖既有终稿开关（--force）。
        store: 未使用（签名保留与 --detailed-doc 对称；装配零状态库访问）。

    Returns:
        int: 0 成功（含降级出稿）；2 前置违例或凭据扫描命中。
    """
    paths = build_paths(out_root, system_id)
    missing = []
    for p in (paths.understanding_json, paths.evidence_index,
              paths.inputs_dir, paths.sections_dir):
        if not p.exists():
            missing.append(str(p))
    if missing:
        raise DetailedDocError(
            "[SFD] 装配前置违例：缺少 {0}——请先运行 --detailed-doc 产出"
            "素材包与大纲（sections/ 目录由该步骤创建）".format("、".join(missing)),
            hints=["装配只消费 detailed/ 草稿与 SU 产物；缺项补齐后重跑 --assemble"])
    check_existing_final(paths, force)

    understanding = _read_understanding_for_doc(paths)
    evidence_map = load_evidence_index(paths.evidence_index)

    # ---- 漂移锚点判定（P0-1b）：manifest 记录 vs 当前文件 ----
    current_run_id = str((understanding.get("meta") or {}).get("run_id") or "")
    current_index_sha256 = hashlib.sha256(paths.evidence_index.read_bytes()).hexdigest()
    drift_suspected = False
    drift_notes: List[str] = []
    for spec in _PACKAGE_SPECS:
        pkg_path = paths.inputs_dir / "{0}.json".format(spec.role)
        if not pkg_path.is_file():
            continue  # 缺包在草稿层自然降级，不参与锚点判定（无派发即无锚）
        try:
            manifest = json.loads(pkg_path.read_text(encoding="utf-8")).get("manifest") or {}
        except (OSError, json.JSONDecodeError):
            drift_suspected = True
            drift_notes.append("{0}.json manifest 不可解析".format(spec.role))
            continue
        m_run = manifest.get("run_id")
        m_sha = manifest.get("evidence_index_sha256")
        if m_run is None or m_sha is None:
            drift_suspected = True
            drift_notes.append("{0}.json manifest 缺派发锚点字段".format(spec.role))
            continue
        if str(m_run) != current_run_id or str(m_sha) != current_index_sha256:
            drift_suspected = True
            drift_notes.append("{0}.json 锚点失配（run_id 或 evidence-index sha256）".format(spec.role))
    for note in drift_notes:
        print("[SFD] 漂移锚点：{0}".format(note))
    if not paths.understanding_md.is_file():
        print("[SFD] 声明：UNDERSTANDING.md 缺失（装配仅消费 understanding.json "
              "与 evidence-index.json，如实降级不阻断；交叉印证面不可用）")

    body, report = assemble_final_doc(paths, understanding, evidence_map,
                                      drift_suspected)
    code = finalize_assembly(paths, body, report)
    if code == 0:
        print("[SFD] 装配完成：{0}".format(str(paths.final_doc.resolve())))
        print("[SFD] 报告：{0}".format(
            str((paths.detailed_dir / ASSEMBLY_REPORT_FILENAME).resolve())))
        print("[SFD] 引用统计 ref_total={0} ref_valid={1} ref_invalid={2} "
              "ref_drifted={3} 合法率={4:.3f}（达标线 {5}，仅度量不拦截）".format(
                  report["ref_total"], report["ref_valid"], report["ref_invalid"],
                  report["ref_drifted"], report["ref_legality_rate"],
                  report["ref_legality_target"]))
        if report["degraded_sections"]:
            print("[SFD] 降级节：{0}（终稿照常产出；补齐草稿后重跑可修复）".format(
                report["degraded_sections"]))
    return code
