# -*- coding: utf-8 -*-
"""SFD（专家详说）单测共享工装——builder 种子库 → 真实 render 三件套 → 详说产物链路。

设计口径（ARCH-SFD-001 §10.2 Fixture 策略）：
  - 三件套（UNDERSTANDING.md / understanding.json / evidence-index.json）
    一律由真实 ``StateStore.export_understanding()`` +
    ``DocumentRenderer.render()`` 产出，**绝不手搓 JSON**（避免与
    export_understanding 实际结构漂移——素材包白名单 key 表直接依赖该结构）；
  - findings 注入直连 sqlite（validate_findings_schema 的形态口径），
    随后走真实 render——evidence-index 的 seq 由渲染期累加器自然生成；
  - 凭据注入样例与扫描判据**同源运行时生成**（仓库内不落任何可 grep 的
    明文凭据字面量——ARCH §8.2 静态审查项）。

宿主模块用法（unittest）::

    from fixtures.sfd_harness import SfdWorkspace
    ws = SfdWorkspace(keep=False)
    ws.setUp()
    ...
    ws.tearDown()

跨包复用说明：test_su_detailed_*.py 以 ``tests.sfd_harness`` 或
``fixtures.sfd_harness`` 之外的形态不可见，故各测试文件用
``sys.path.insert(TESTS_DIR)`` 后 ``import sfd_harness``——与既有
``from su.xxx import`` 的 sys.path 注入惯例同源。
"""

import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# 路径注入：scripts/（su.* 与 system_understanding 可导入）
_SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
# fixtures/（su_state_builder 按模块名导入——importlib 重载友好）
_FIXTURES_DIR = Path(__file__).resolve().parent
if str(_FIXTURES_DIR) not in sys.path:
    sys.path.insert(0, str(_FIXTURES_DIR))

import su_state_builder  # noqa: E402

from su.config import (  # noqa: E402
    RunBudget,
    SensitiveStr,
    SuConfig,
    SystemConfig,
)
from su.document_renderer import DocumentRenderer  # noqa: E402
from su.state_store import StateStore  # noqa: E402

__all__ = [
    "SfdWorkspace",
    "render_three_piece",
    "inject_findings",
    "seed_state_db",
    "fake_credential_token",
    "fake_kv_credential_line",
    "fake_url_credential_line",
    "fake_pii_line",
    "DRAFT_FIRST_LINES",
]

# 五份合规草稿的首行节头（与 su.detailed_doc.SECTION_TITLES_SFD 契约同源；
# 04 草稿同时承载第 4/5 两节，首行只校验第 4 节节头）
DRAFT_FIRST_LINES: Dict[str, str] = {
    "01-architecture.doc.md": "# 1. 系统定位与技术架构",
    "02-product.doc.md": "# 2. 功能全景与业务流程",
    "03-pages.doc.md": "# 3. 页面功能详说",
    "04-data-semantics.doc.md": "# 4. 数据模型业务语义",
    "05-quality.doc.md": "# 6. 质量盲区与风险建议",
}

# finding 必填形态（state_store.validate_findings_schema 口径）——
# 供负例注入（json-SQLite 双源不一致等）在宿主模块复用
FINDING_TEMPLATE_KINDS = ("mapping", "business_rule", "redis_entity", "semantic_name")


# ---------------------------------------------------------------------------
# 凭据注入样例（与判据同源运行时生成，ARCH §10.2——禁止入库明文样例）
# ---------------------------------------------------------------------------

def fake_credential_token() -> str:
    """C4 双因子的高熵凭据值（12+ 位、字母数字混合，运行时拼接生成）。

    用于构造 ``{"auth_code": <本值>}`` 类"DB 采样非敏感键名 + 真实形态
    凭据值"注入向量（PRD S-3 / ARCH P0-3b）。

    Returns:
        str: 形如 ``k7f2q9d4x1m8vb3c`` 的 16 位字母数字混合 token。
    """
    seed = "k7f2q9d4x1m8vb3c"
    # 两两交叠重排仍保持"含字母+含数字、长度≥12"双因子形态
    return seed[8:] + seed[:8]


def fake_kv_credential_line() -> str:
    """C3 键值对形态凭据行（与 _CLAIM_CREDENTIAL_RE 同源形态，运行时拼接）。

    Returns:
        str: ``password=<拼接值>`` 形态的一行文本（无入库明文样例）。
    """
    return "password=" + "Tr9" + "uXa2" + "Lk5"


def fake_url_credential_line() -> str:
    """C2 任意 scheme URL 内嵌凭据行（与 _INLINE_USERINFO_RE 同源形态）。

    Returns:
        str: ``mysql://user:pwd@host/db`` 形态文本（分段拼接、不入库明文）。
    """
    return "mysql://" + "deploy" + ":" + "Qz7" + "Wm2" + "@db.internal:3306/appdb"


def fake_pii_line() -> str:
    """C1 PII 值形态行（与 PII_VALUE_PATTERNS.phone 同源形态，运行时拼接）。

    Returns:
        str: 含中国大陆手机号值形态的一行文本。
    """
    return "联系人手机号 " + "138" + "0013" + "8000" + " 请复核"


# ---------------------------------------------------------------------------
# 种子库与三件套渲染（全部真实生产代码路径）
# ---------------------------------------------------------------------------

def seed_state_db(db_path: Path, system_id: str) -> Dict[str, Any]:
    """builder 建库 + 收口 completed（生产 fixture 复用，零手搓 schema）。

    Args:
        db_path: 状态库目标路径（``<out>/<sid>/state/understanding.sqlite``）。
        system_id: 系统标识（与输出目录名一致）。

    Returns:
        dict: builder 的种子 id 映射（findings evidence_refs 定位真实行 id）。
    """
    ids = su_state_builder.build_state_db(Path(db_path), system_id=system_id)
    su_state_builder.finalize_state_db(Path(db_path), run_status="completed")
    return ids


def inject_findings(db_path: Path, findings: List[Dict[str, Any]]) -> None:
    """把 findings 直插状态库 findings 表（跳过 replace_findings 调用栈断言）。

    口径说明：``replace_findings`` 的单一入口断言只约束生产 CLI 编排层；
    fixture 注入属测试设施建设（与 builder 直连 INSERT 同一性质）。入库
    前仍过生产 ``validate_findings_schema`` 形态校验，保证注入数据与
    render-only 入库产物形态逐字段一致。

    Args:
        db_path: 状态库路径。
        findings: findings dict 列表（claim/kind/confidence/evidence_refs）。

    Raises:
        AssertionError: 注入数据未通过生产 schema 校验（fixture 自身缺陷）。
    """
    probe = StateStore(Path(db_path), "fixture-inject")
    try:
        errors = probe.validate_findings_schema(findings)
        assert not errors, "fixture findings 未过生产校验：{0}".format(errors)
    finally:
        probe.close()
    conn = sqlite3.connect(str(db_path))
    try:
        now = time.time()
        for item in findings:
            conn.execute(
                "INSERT INTO findings(claim, confidence, evidence_refs, status,"
                " kind, created_at) VALUES(?,?,?,?,?,?)",
                (item["claim"], item["confidence"],
                 json.dumps(item["evidence_refs"], ensure_ascii=False),
                 "proposed", item["kind"], now),
            )
        conn.commit()
    finally:
        conn.close()


def _render_shell_config(out_root: Path, system_id: str) -> SuConfig:
    """渲染参数壳 SuConfig（仿 CLI _render_only_config：凭据占位、不落盘）。

    Args:
        out_root: 输出根目录。
        system_id: 系统标识。

    Returns:
        SuConfig: DocumentRenderer 所需最小配置。
    """
    return SuConfig(
        system=SystemConfig(
            base_url="http://127.0.0.1:9",
            login_url="/login",
            username=SensitiveStr("fixture"),
            password=SensitiveStr("fixture"),
        ),
        database=None,
        redis=None,
        budget=RunBudget(),
        out_dir=Path(out_root),
        system_id=system_id,
    )


def render_three_piece(out_root: Path, system_id: str,
                       meta_patch: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """真实渲染三件套（与 CLI --render-only 的渲染收口同一代码路径）。

    流程：只读校验最新 run ∈ {completed, interrupted}（校验先于取锁，
    继承 render-only 口径）→ acquire_lock(resume=True) → 渲染 →
    run 收口 completed 后重新导出 meta 并回写 understanding.json 的
    meta.run_status/run_id——复现 CLI"meta 与收口时点一致"的设计形态
    （渲染收口 meta.run_status=running 属 SU 既有语义，SFD 契约前提为
    meta 段与锚定 run 收口时点一致，ARCH §2.2.1 校验 4 的 hints 口径）。

    Args:
        out_root: 输出根目录（``--out`` 语义）。
        system_id: 输出子目录名。
        meta_patch: 渲染完成后对 understanding.json meta 段的追加覆写
            （构造锚定违例场景用，如 run_status="running"）。

    Returns:
        dict: 渲染后的 understanding.json 全量数据。
    """
    out_root = Path(out_root)
    db_path = out_root / system_id / "state" / "understanding.sqlite"
    store = StateStore(db_path, system_id)
    try:
        history = store.read_latest_run()
        assert history is not None and str(history.get("status")) in (
            "completed", "interrupted"), "fixture 前置：最新 run 必须先收口"
        store.acquire_lock(resume=True)
        renderer = DocumentRenderer(store, _render_shell_config(out_root, system_id))
        # 四透镜按库内数据有无登记（与 CLI _infer_lens_status 同口径的最小化）
        stats = store.stats()
        if int(stats.get("pages_total") or 0) > 0:
            renderer.set_lens_status("ui", "collected")
            renderer.set_lens_status("api", "collected")
        else:
            renderer.set_lens_status("ui", "skipped", "fixture 无页面数据")
            renderer.set_lens_status("api", "skipped", "fixture 无端点数据")
        if int(stats.get("db_tables_total") or 0) > 0:
            renderer.set_lens_status("db", "collected")
        else:
            renderer.set_lens_status("db", "skipped", "fixture 无 DB 数据")
        if int(stats.get("redis_patterns_total") or 0) > 0:
            renderer.set_lens_status("redis", "collected")
        else:
            renderer.set_lens_status("redis", "skipped", "fixture 无 Redis 数据")
        renderer.render()
        # render run 自身收口（同 CLI mark("completed","render_only")）
        store.mark("completed", "render_only")
        store.release_lock()
        # meta 与收口时点对齐（见 docstring 口径说明：CLI 渲染时 run 仍
        # running 会留下 meta.run_status=running——候选缺陷 D1，fixture
        # 在此复现 CLI"meta 与收口时点一致"的设计契约形态）+ 违例覆写
        u_path = out_root / system_id / "understanding.json"
        data = json.loads(u_path.read_text(encoding="utf-8"))
        data["meta"]["run_status"] = "completed"
        if meta_patch:
            data["meta"].update(meta_patch)
        u_path.write_text(json.dumps(data, ensure_ascii=False,
                                     sort_keys=True, indent=2), encoding="utf-8")
        return data
    finally:
        store.close()


class SfdWorkspace:
    """一个完整的 SFD 测试工作区（tmp 目录 + 三件套 + 可选详说前置产物）。

    生命周期由宿主 unittest 显式驱动：``setUp()`` 建工作区并（默认）渲染
    三件套；``tearDown()`` 删除 tmp 目录。``keep=True`` 时保留目录供排障
    （测试自身不依赖该分支）。
    """

    def __init__(self, system_id: str = "sfd-sys", n_findings: int = 1,
                 render: bool = True, keep: bool = False,
                 meta_patch: Optional[Dict[str, Any]] = None) -> None:
        """记录工作区构造参数（不做任何 IO——IO 收敛到 setUp）。

        Args:
            system_id: 输出子目录名。
            n_findings: 注入的合法 findings 条数（引用 builder 真实种子行）。
            render: 是否渲染三件套（纯素材包/大纲纯函数测试可关闭）。
            keep: True 时 tearDown 不删除 tmp 目录（排障用）。
            meta_patch: 渲染后对 understanding.json meta 段的覆写。
        """
        self.system_id = system_id
        self.n_findings = n_findings
        self.render_enabled = render
        self.keep = keep
        self.meta_patch = meta_patch
        self.tmp: Optional[Path] = None
        self.out_root: Optional[Path] = None
        self.sys_root: Optional[Path] = None
        self.seed_ids: Optional[Dict[str, Any]] = None
        self.understanding: Optional[Dict[str, Any]] = None

    # -- 生命周期 ---------------------------------------------------------
    def setUp(self) -> None:
        """建 tmp 工作区 →（可选）种子库 + findings 注入 + 真实渲染三件套。"""
        self.tmp = Path(tempfile.mkdtemp(prefix="sfd-ws."))
        self.out_root = self.tmp / "out"
        self.out_root.mkdir(parents=True, exist_ok=True)
        self.sys_root = self.out_root / self.system_id
        if self.render_enabled:
            self.seed_and_render()

    def tearDown(self) -> None:
        """删除 tmp 工作区（keep=True 时保留）。"""
        if self.tmp is not None and not self.keep:
            import shutil
            shutil.rmtree(str(self.tmp), ignore_errors=True)

    # -- 前置产物构造 -----------------------------------------------------
    def seed_and_render(self, n_findings: Optional[int] = None) -> Dict[str, Any]:
        """种子库 + findings 注入 + 真实渲染（幂等：可重入重渲染）。

        Args:
            n_findings: 覆盖构造参数的 findings 条数（None=用构造参数）。

        Returns:
            dict: 渲染后的 understanding.json 数据。
        """
        count = self.n_findings if n_findings is None else n_findings
        db_path = self.sys_root / "state" / "understanding.sqlite"
        self.seed_ids = seed_state_db(db_path, self.system_id)
        if count > 0:
            inject_findings(db_path, self.make_findings(count))
        self.understanding = render_three_piece(
            self.out_root, self.system_id, meta_patch=self.meta_patch)
        return self.understanding

    def make_findings(self, count: int) -> List[Dict[str, Any]]:
        """生成合法 findings（evidence_refs 引用 builder 种子真实行 id）。

        条数上限自动收敛为"页面证据 + DB 表证据"可用行数之和，超限抛
        AssertionError（防测试静默使用重复 ref 造成 schema 歧义）。

        Args:
            count: 期望条数。

        Returns:
            list[dict]: findings 列表（kind/confidence 在枚举内轮换）。
        """
        assert self.seed_ids is not None, "必须先 seed_state_db"
        refs: List[str] = []
        for url_key, pid in sorted(self.seed_ids["page_ids"].items()):
            refs.append("pages:{0}".format(pid))
        for table, tid in sorted(self.seed_ids["table_ids"].items()):
            refs.append("db_tables:{0}".format(tid))
        assert count <= len(refs), (
            "fixture findings 条数 {0} 超可用证据 {1}".format(count, len(refs)))
        kinds = ["mapping", "business_rule", "semantic_name"]
        confs = ["high", "medium", "low"]
        findings: List[Dict[str, Any]] = []
        for i in range(count):
            findings.append({
                "claim": "fixture 结论 {0}：证据 {1} 支撑的功能语义走读".format(i + 1, refs[i]),
                "kind": kinds[i % len(kinds)],
                "confidence": confs[i % len(confs)],
                "evidence_refs": [refs[i]],
            })
        return findings

    # -- 详说链路步骤（薄封装，走 detailed_doc 模块公开接口） ---------------
    def run_detailed_doc(self, store: Any = "auto") -> int:
        """跑 --detailed-doc 编排（素材包 + 大纲 + 派发指引）。

        Args:
            store: duck-typed 只读状态库；``"auto"`` = 自开自关真实库。

        Returns:
            int: 编排返回值（0 成功；违例抛 DetailedDocError）。
        """
        from su.detailed_doc import run_detailed_doc
        if store == "auto":
            opened = StateStore(self.sys_root / "state" / "understanding.sqlite",
                                self.system_id)
            try:
                return run_detailed_doc(self.out_root, self.system_id, store=opened)
            finally:
                opened.close()
        return run_detailed_doc(self.out_root, self.system_id, store=store)

    def write_draft(self, filename: str, text: str) -> Path:
        """按契约文件名放置专家草稿（sections/ 自动创建）。

        Args:
            filename: 草稿文件名（如 ``01-architecture.doc.md``）。
            text: 草稿全文。

        Returns:
            Path: 草稿落盘路径。
        """
        sections = self.sys_root / "detailed" / "sections"
        sections.mkdir(parents=True, exist_ok=True)
        path = sections / filename
        path.write_text(text, encoding="utf-8")
        return path

    def write_compliant_drafts(self, with_section5: bool = True) -> Dict[str, str]:
        """放置五份合规草稿（首行节头逐字 + 真实 E-n 引用 + 04 标记段）。

        引用编号取当前 evidence-index 首条 seq（E{seq:04d}），保证
        ref_invalid=0；04 草稿含逐字 ``<!-- SFD-SECTION: 5 -->`` 标记。

        Args:
            with_section5: False 时 04 草稿省略第 5 节标记（降级场景）。

        Returns:
            dict: {文件名: 草稿文本}（宿主做追加篡改时用）。
        """
        from su.detailed_doc import SFD_SECTION5_MARKER
        index = json.loads((self.sys_root / "evidence" / "evidence-index.json")
                           .read_text(encoding="utf-8"))
        seq = index["entries"][0]["seq"]
        e1 = "E{0:04d}".format(seq)
        drafts = {
            "01-architecture.doc.md": (
                "{0}\n\n订单管理系统，三层架构（{1}）。\n".format(
                    DRAFT_FIRST_LINES["01-architecture.doc.md"], e1)),
            "02-product.doc.md": (
                "{0}\n\n核心流：下单落 orders 表（{1}）。\n".format(
                    DRAFT_FIRST_LINES["02-product.doc.md"], e1)),
            "03-pages.doc.md": (
                "{0}\n\n订单列表页展示订单明细（{1}）。\n".format(
                    DRAFT_FIRST_LINES["03-pages.doc.md"], e1)),
            "04-data-semantics.doc.md": (
                "{0}\n\norders 表存订单主数据（{1}）。\n{2}\n\n"
                "GET /api/orders 供订单列表页消费。\n".format(
                    DRAFT_FIRST_LINES["04-data-semantics.doc.md"], e1,
                    SFD_SECTION5_MARKER)),
            "05-quality.doc.md": (
                "{0}\n\nT3 删除动作未执行，风险高（{1}）。\n".format(
                    DRAFT_FIRST_LINES["05-quality.doc.md"], e1)),
        }
        if not with_section5:
            drafts["04-data-semantics.doc.md"] = drafts[
                "04-data-semantics.doc.md"].replace(SFD_SECTION5_MARKER + "\n\n", "")
        for name, text in drafts.items():
            self.write_draft(name, text)
        return drafts

    # -- 常用产物读取 ------------------------------------------------------
    def final_doc_text(self) -> str:
        """读终稿/骨架 SYSTEM_FUNCTION_DOC.md 全文。

        Returns:
            str: 文件全文（不存在时 AssertionError 由宿主断言转化）。
        """
        path = self.sys_root / "SYSTEM_FUNCTION_DOC.md"
        assert path.is_file(), "SYSTEM_FUNCTION_DOC.md 不存在"
        return path.read_text(encoding="utf-8")

    def report(self) -> Dict[str, Any]:
        """读 assembly-report.json。

        Returns:
            dict: 装配报告全量。
        """
        path = self.sys_root / "detailed" / "assembly-report.json"
        assert path.is_file(), "assembly-report.json 不存在"
        return json.loads(path.read_text(encoding="utf-8"))

    def understanding_disk(self) -> Dict[str, Any]:
        """读磁盘 understanding.json（磁盘真相，非内存副本）。

        Returns:
            dict: 磁盘全量数据。
        """
        return json.loads((self.sys_root / "understanding.json")
                          .read_text(encoding="utf-8"))
