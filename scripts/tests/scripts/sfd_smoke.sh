#!/usr/bin/env bash
# =============================================================================
# SFD 手工冒烟脚本（S-1/S-5 场景最小版）
#
# 流程：
#   1. su_state_builder 造状态库 + finalize 收口（completed）；
#   2. 构造带 findings 段的 understanding.json，跑 --render-only 产出
#      UNDERSTANDING.md / understanding.json / evidence-index.json 三件套；
#   3. --detailed-doc：验证素材包五件 + 大纲 8 节 + 派发指引；
#   4. 按契约放置五份合规专家草稿（含 SFD-SECTION: 5 标记、E-n 引用）；
#   5. --assemble：验证终稿 8 节 + assembly-report 字段齐；
#   6. 连跑两次 --assemble（第二次带 --force），验证终稿字节一致（S-5 幂等）。
#
# 运行：bash scripts/tests/scripts/sfd_smoke.sh（项目根目录）
# 退出码：0 全部通过；非 0 任一步失败。
# =============================================================================
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SCRIPTS_DIR="${REPO_ROOT}/scripts"
STATE_BUILDER="${SCRIPTS_DIR}/tests/fixtures/su_state_builder.py"
SU_CLI="${SCRIPTS_DIR}/system_understanding.py"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/sfd-smoke.XXXXXX")"
OUT="${WORK}/out"
SID="smoke-sys"
SYS_ROOT="${OUT}/${SID}"
FAIL=0

cleanup() { rm -rf "${WORK}"; }
trap cleanup EXIT

say() { printf '[sfd-smoke] %s\n' "$*"; }
die() { printf '[sfd-smoke] FAIL %s\n' "$*"; FAIL=1; }

# ---------------------------------------------------------------------------
# 1. 造状态库（builder CLI 模式：注入即收口 completed）
# ---------------------------------------------------------------------------
mkdir -p "${SYS_ROOT}/state"
python3 -B "${STATE_BUILDER}" "${SYS_ROOT}/state/understanding.sqlite" "${SID}" completed >/dev/null \
  || { die "builder 造库失败"; exit 1; }
say "状态库已建 ${SYS_ROOT}/state/understanding.sqlite"

# ---------------------------------------------------------------------------
# 2. 构造 findings 段并跑 --render-only（findings evidence_refs 引用真实行 id）
# ---------------------------------------------------------------------------
python3 -B - "${SYS_ROOT}" <<'PYEOF'
"""render-only 前置：构造 understanding.json（findings 段引用 builder 真实种子）。"""
import json
import sqlite3
import sys
from pathlib import Path

sys_root = Path(sys.argv[1])
db = sys_root / "state" / "understanding.sqlite"
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
page_orders = conn.execute(
    "SELECT page_id FROM pages WHERE url_key='/orders'").fetchone()["page_id"]
table_orders = conn.execute(
    "SELECT table_id FROM db_tables WHERE table_name='orders'").fetchone()["table_id"]
ep = conn.execute(
    "SELECT endpoint_id FROM api_observations WHERE url_path='/api/orders'"
).fetchone()["endpoint_id"]
conn.close()

# render-only 只要求"understanding.json 存在且含 findings 数组"——最小骨架即可，
# meta 段由渲染层从状态库重建
understanding = {
    "findings": [
        {
            "claim": "订单列表页（pages:{0}）经 GET /api/orders（api_observations:{1}）读取 orders 表（db_tables:{2}）".format(
                page_orders, ep, table_orders),
            "kind": "mapping",
            "confidence": "high",
            "evidence_refs": ["pages:{0}".format(page_orders),
                              "api_observations:{0}".format(ep),
                              "db_tables:{0}".format(table_orders)],
            "status": "proposed",
        }
    ]
}
(sys_root / "understanding.json").write_text(
    json.dumps(understanding, ensure_ascii=False, indent=2), encoding="utf-8")
print("findings 段已写入 understanding.json")
PYEOF
[ $? -eq 0 ] || { die "findings 注入失败"; exit 1; }

python3 -B "${SU_CLI}" --out "${OUT}" --system-id "${SID}" --render-only \
  >"${WORK}/render.log" 2>&1 \
  || { die "--render-only 失败（见日志要点）"; tail -5 "${WORK}/render.log"; exit 1; }
for f in UNDERSTANDING.md understanding.json evidence/evidence-index.json; do
  [ -f "${SYS_ROOT}/${f}" ] || { die "render 三件套缺 ${f}"; exit 1; }
done
say "render 三件套齐备"

# ---------------------------------------------------------------------------
# 3. --detailed-doc：素材包五件 + 大纲 8 节
# ---------------------------------------------------------------------------
python3 -B "${SU_CLI}" --out "${OUT}" --system-id "${SID}" --detailed-doc \
  >"${WORK}/detailed.log" 2>&1
RC=$?
[ ${RC} -eq 0 ] || { die "--detailed-doc 退出码 ${RC}"; tail -8 "${WORK}/detailed.log"; exit 1; }
grep -q "\[SFD\] 专家派发" "${WORK}/detailed.log" \
  || { die "派发指引缺失"; tail -8 "${WORK}/detailed.log"; exit 1; }
for pkg in architect product dev ui qa; do
  [ -f "${SYS_ROOT}/detailed/inputs/${pkg}.json" ] || die "素材包缺失 ${pkg}.json"
done
# manifest 锚点字段核验（P0-1b/P1-6）
python3 -B - "${SYS_ROOT}" <<'PYEOF' || exit 1
import json
import sys
from pathlib import Path
root = Path(sys.argv[1])
for role in ("architect", "product", "dev", "ui", "qa"):
    manifest = json.loads((root / "detailed/inputs" / f"{role}.json").read_text("utf-8"))["manifest"]
    for key in ("run_id", "evidence_index_sha256", "started_at", "sources"):
        assert key in manifest, f"{role}.json manifest 缺 {key}"
masked = json.loads((root / "detailed/inputs" / "dev.json").read_text("utf-8"))["manifest"]
assert masked.get("db_samples_masked_by") == "DataMasker", "dev 包缺 db_samples_masked_by"
print("manifest 锚点字段核验通过")
PYEOF
[ ${FAIL} -eq 0 ] || exit 1
# 头部区（前 5 行）内必须出现 status: outline 标记行（文档一级标题在首行）
head -5 "${SYS_ROOT}/SYSTEM_FUNCTION_DOC.md" | grep -q '^<!-- status: outline -->$' \
  || die "大纲头部 5 行内无 status: outline 标记"
SEC_COUNT="$(grep -c '^## [1-8]\. ' "${SYS_ROOT}/SYSTEM_FUNCTION_DOC.md")"
[ "${SEC_COUNT}" -eq 8 ] || die "大纲节数 ${SEC_COUNT} ≠ 8"
say "--detailed-doc 通过（素材包 5 件、大纲 8 节、派发指引在位）"

# ---------------------------------------------------------------------------
# 4. 放置五份合规草稿（首行节头逐字、走读稿含 SFD-SECTION: 5 标记、E-n 引用）
# ---------------------------------------------------------------------------
E1="$(python3 -B -c "import json;print(json.load(open('${SYS_ROOT}/evidence/evidence-index.json'))['entries'][0]['seq'])")"
python3 -B - "${SYS_ROOT}" "${E1}" <<'PYEOF' || exit 1
"""按 PROMPT-SFD 契约放置五份合规草稿（引用第 1 条真实证据编号）。"""
import sys
from pathlib import Path

sys_root = Path(sys.argv[1])
e1 = "E{0:04d}".format(int(sys.argv[2]))
sections = sys_root / "detailed" / "sections"
sections.mkdir(parents=True, exist_ok=True)

(sections / "01-architecture.doc.md").write_text(
    "# 1. 系统定位与技术架构\n\n订单管理系统，三层架构（E0001）"
    "，BFF 形态为[推断]。\n".replace("E0001", e1), encoding="utf-8")
(sections / "02-product.doc.md").write_text(
    "# 2. 功能全景与业务流程\n\n```mermaid\nflowchart LR\n  A[下单] --> B[订单列表]\n```\n"
    "核心流：用户下单落 orders 表（E1）。\n".replace("E1", e1), encoding="utf-8")
(sections / "03-pages.doc.md").write_text(
    "# 3. 页面功能详说\n\n订单列表页（/orders）展示订单明细（E1）。\n"
    .replace("E1", e1), encoding="utf-8")
(sections / "04-data-semantics.doc.md").write_text(
    "# 4. 数据模型业务语义\n\norders 表存订单主数据（E1）。\n"
    "<!-- SFD-SECTION: 5 -->\n\nGET /api/orders 供订单列表页消费。\n"
    .replace("E1", e1), encoding="utf-8")
(sections / "05-quality.doc.md").write_text(
    "# 6. 质量盲区与风险建议\n\nT3 删除动作未执行，风险高，建议灰度验证（E1）。\n"
    .replace("E1", e1), encoding="utf-8")
print("五份合规草稿已放置")
PYEOF
[ $? -eq 0 ] || { die "草稿放置失败"; exit 1; }

# ---------------------------------------------------------------------------
# 5. --assemble：终稿 8 节 + report 字段齐
# ---------------------------------------------------------------------------
python3 -B "${SU_CLI}" --out "${OUT}" --system-id "${SID}" --assemble \
  >"${WORK}/assemble1.log" 2>&1
RC=$?
[ ${RC} -eq 0 ] || { die "--assemble 退出码 ${RC}"; tail -8 "${WORK}/assemble1.log"; exit 1; }
head -5 "${SYS_ROOT}/SYSTEM_FUNCTION_DOC.md" | grep -q '^<!-- status: final -->$' \
  || die "终稿头部 5 行内无 status: final 标记"
FINAL_SECS="$(grep -c '^## [1-8]\. ' "${SYS_ROOT}/SYSTEM_FUNCTION_DOC.md")"
[ "${FINAL_SECS}" -eq 8 ] || die "终稿节数 ${FINAL_SECS} ≠ 8"
python3 -B - "${SYS_ROOT}" <<'PYEOF' || exit 1
import json
import sys
from pathlib import Path
report = json.loads((Path(sys.argv[1]) / "detailed/assembly-report.json").read_text("utf-8"))
required = ("system_id", "started_at", "sections", "degraded_sections", "ref_total",
            "ref_valid", "ref_invalid", "ref_legality_rate", "drift_suspected",
            "outline_modified", "credential_scan", "outline_sha256",
            "outline_sha256_on_disk")
missing = [k for k in required if k not in report]
assert not missing, "assembly-report 缺字段：{0}".format(missing)
assert len(report["sections"]) == 8, "sections 非 8 条"
assert report["ref_invalid"] == 0, "ref_invalid 应为 0（合规草稿）"
print("assembly-report 字段核验通过（ref_total={0} 合法率={1}）".format(
    report["ref_total"], report["ref_legality_rate"]))
PYEOF
[ $? -eq 0 ] || exit 1
say "--assemble 通过（终稿 8 节、report 字段齐、0 非法引用）"

# 既有终稿保护：无 --force 重跑必须 exit 2
python3 -B "${SU_CLI}" --out "${OUT}" --system-id "${SID}" --assemble \
  >"${WORK}/assemble-guard.log" 2>&1
RC=$?
[ ${RC} -eq 2 ] || die "既有终稿保护失效（无 --force 重跑退出码 ${RC} ≠ 2）"
say "既有终稿 status: final 保护通过（exit 2）"

# ---------------------------------------------------------------------------
# 6. S-5 幂等：--force 重跑终稿字节一致
# ---------------------------------------------------------------------------
cp "${SYS_ROOT}/SYSTEM_FUNCTION_DOC.md" "${WORK}/final.v1"
python3 -B "${SU_CLI}" --out "${OUT}" --system-id "${SID}" --assemble --force \
  >"${WORK}/assemble2.log" 2>&1
RC=$?
[ ${RC} -eq 0 ] || { die "--assemble --force 退出码 ${RC}"; exit 1; }
if cmp -s "${WORK}/final.v1" "${SYS_ROOT}/SYSTEM_FUNCTION_DOC.md"; then
  say "幂等通过：两次 --assemble 终稿逐字节一致"
else
  die "幂等失败：两次 --assemble 终稿不一致"
  diff "${WORK}/final.v1" "${SYS_ROOT}/SYSTEM_FUNCTION_DOC.md" | head -10
fi

# ---------------------------------------------------------------------------
# 7. CLI 组合违例速查（S-4 局部）：互斥/生命周期参数/缺 --out
# ---------------------------------------------------------------------------
python3 -B "${SU_CLI}" --out x --system-id y --render-only --detailed-doc \
  >"${WORK}/mutex.log" 2>&1
[ $? -eq 2 ] || die "互斥组违例未 exit 2"
python3 -B "${SU_CLI}" --out "${OUT}" --system-id "${SID}" --detailed-doc --fresh \
  >"${WORK}/fresh.log" 2>&1
[ $? -eq 2 ] || die "--detailed-doc --fresh 组合未 exit 2"
python3 -B "${SU_CLI}" --system-id y --assemble >"${WORK}/noout.log" 2>&1
[ $? -eq 2 ] || die "--assemble 缺 --out 未 exit 2"
say "CLI 组合违例速查通过"

if [ ${FAIL} -eq 0 ]; then
  say "全部冒烟检查通过"
  exit 0
else
  say "存在失败项"
  exit 1
fi
