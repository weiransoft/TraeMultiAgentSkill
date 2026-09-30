#!/usr/bin/env bash
# =============================================================================
# SU（System Understanding）e2e 集成测试聚合脚本（PRD §6.2 六场景 / 架构 §11.3）
#
# 真实 Playwright + 真实本地 fixture 站点（tests/fixtures/su_site/server.py）
# 全链路：零 mock、零假通过——每场景断言全部真实执行；playwright/chromium
# 缺失时浏览器场景打印显式 SKIP 行（绝不假通过），fixture 站点自身的纯 HTTP
# 断言（场景[1]）不受依赖影响照常执行。
#
# 分工互引：本脚本只跑【fixture 站点 + 真实 SU 进程】的端到端场景；
# 纯单测（test_su_*.py，含 fixture 自测 test_su_e2e_site.py）见同目录
# run_system_understanding.sh。两者均由 run_all.sh 统一调度。
#
# 场景矩阵（PRD §6.2 / 架构 §11.3）：
#   [0] playwright + chromium 前置探测（真实 launch；缺失 → 浏览器场景 SKIP）
#   [1] fixture 纯 HTTP 冒烟（无需 playwright：登录 401/302、write-counter、
#       路由可达、外域/下载/手机号样本页素材——依赖缺失时唯一照常执行的场景）
#   [2] 全链路：su_site 独立进程 + SU 完整采集（--skip-llm-phase）→ 10 节文档
#       + write-counter==0 + blocked 含 aborted_method + 搜索表单 T2 执行
#       + 外域进 blocked_origins + hash 页入图 + 下载被拒
#   [3] 安全红线：运行目录全文件 grep 凭据明文（SuTest#2026）与手机号样本
#       明文 0 命中 + 快照中手机号呈 <REDACTED:phone> 脱敏 + 外域零入库
#   [4] 断点续跑：SIGINT→130 → --resume 进度继承 → --fresh 归档目录断言
#   [5] 预算：--max-pages 3 → 恰好 3 done 页 + frontier>0 + 第 10 节预算声明
#   [6] 凭据错误：错密码 → exit 4 且无 UNDERSTANDING.md
#   [7] render-only：种子库注入合法 findings → exit 0 且第 5 节含 claim；
#       非法 findings（缺 confidence）→ exit 2 整批拒绝；另含 redis 缺配
#       第 6 节"未采集"显式声明断言
#   [8] DB/Redis 容器场景（需 SU_TEST_MYSQL_DSN / SU_TEST_REDIS_URL 外部
#       注入，缺省显式 SKIP——env 探测缺失不假通过也不 FAIL）
#   [9] SFD S-1 全链路（复用场景[2]三件套 + findings 注入 + render-only
#       收口）：detailed-doc → 五合规草稿 → assemble 终稿 8 节（浏览器场景，
#       playwright 缺失时显式 SKIP）
#   [10] SFD S-2 降级装配：只放 2 份草稿 → exit 0 出稿、degraded 节与
#        声明齐备（builder 链路，零浏览器恒执行）
#   [11] SFD S-3 凭据收口：草稿注入假凭据（C1/C3/C4 + C2 脱敏自引用反例）
#        → exit 2 且终稿未被更新（builder 链路，零浏览器恒执行）
#   [12] SFD S-4 CLI 违例矩阵：三 flag 互斥 / 生命周期组合 / 缺必填 /
#        非空目录零副作用（builder 链路，零浏览器恒执行）
#   [13] SFD S-5 幂等：重跑 detailed-doc + assemble 终稿逐字节一致
#        （builder 链路，零浏览器恒执行）
#
# SFD 场景（[10]-[13]）走 su_state_builder 链路零浏览器依赖，playwright
# 缺失时照常执行（ARCH-SFD-001 §10.3：详说阶段零网络零凭据面）。
#
# write-counter 基线口径（架构 §11.2）：每场景独立起 fixture server 进程，
# 进程内存计数天然归零；SU 全链路跑完 POST==0 即 route guard 零写红线证据。
#
# 规范：零第三方测试依赖（curl + python3 + sqlite3 CLI）；
# 任一 FAIL → 总退出码非 0。
#
# 用法：run_system_understanding_e2e.sh [--only N[,N...]]
#   --only 2,4,5  只执行指定编号的场景（调试迭代用，属场景选择参数而非
#                 断言削弱——未选中的场景打印 SKIP 行，矩阵如实呈现）。
#                 场景 [0]（依赖探测）与 [1]（fixture 冒烟）恒执行：
#                 [0] 是浏览器场景的 DEPS_OK 前置，[1] 是站点素材契约。
# =============================================================================
set -u

# ---------------------------------------------------------------------------
# 场景选择（--only）：空白名单 = 全量执行
# ---------------------------------------------------------------------------
ONLY_SCENARIOS=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --only)
      # --only 2,4,5 与 --only=2,4,5 两种形态都接受
      if [ "$#" -ge 2 ]; then
        ONLY_SCENARIOS="$2"; shift 2
      else
        printf '[su-e2e] 错误：--only 需要场景编号参数（如 --only 2,4,5）\n' >&2
        exit 64
      fi
      ;;
    --only=*)
      ONLY_SCENARIOS="${1#--only=}"; shift
      ;;
    -h|--help)
      printf '用法: %s [--only N[,N...]]\n  --only 只跑指定场景编号（0-13）；[0][1] 恒执行\n' "$(basename "$0")"
      exit 0
      ;;
    *)
      printf '[su-e2e] 错误：未知参数 %s（见 --help）\n' "$1" >&2
      exit 64
      ;;
  esac
done

# scenario_selected <编号>：该场景是否应执行。空白名单（未传 --only）
# 恒真；[0][1] 不受 --only 限制恒执行（前置探测/素材契约，成本极低）；
# [8]（容器场景，缺 DSN 时场景内部首分支即 SKIP 成本≈0）与 [10]-[13]
# （SFD builder 链路，零浏览器、不绑定 su_site fixture 站点）同样不受
# --only 限制——[1] 站点冒烟恒执行的成本上界即其全部成本。
# （2026-09-30 教训：旧版把浏览器型场景排除在 --only 白名单外却仍起
# su_site 站点空等——场景[1]的站点在批次收尾统一 kill，--only 批次
# 无场景消费站点时挂到看门狗 300s 被强杀，整批 rc=1。）
scenario_selected() {
  local id="$1" item
  case "${id}" in
    0|1|8) return 0 ;;
    10|11|12|13) return 0 ;;
  esac
  [ -z "${ONLY_SCENARIOS}" ] && return 0
  # 逗号/空格分隔白名单逐项匹配
  local list="${ONLY_SCENARIOS//,/ }"
  for item in ${list}; do
    [ "${item}" = "${id}" ] && return 0
  done
  return 1
}

# ---------------------------------------------------------------------------
# 路径与环境
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # tests/scripts
TESTS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"                  # tests
SCRIPTS_DIR="$(cd "${TESTS_DIR}/.." && pwd)"                 # scripts
REPO_ROOT="$(cd "${SCRIPTS_DIR}/.." && pwd)"
SU_CLI="${SCRIPTS_DIR}/system_understanding.py"
SU_SITE_PY="${TESTS_DIR}/fixtures/su_site/server.py"
STATE_BUILDER_PY="${TESTS_DIR}/fixtures/su_state_builder.py"
WORK_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/su-e2e.XXXXXX")"
PYTHON="${PYTHON:-python3}"
PLAYWRIGHT_GUIDE="pip install 'playwright>=1.40.0' && python3 -m playwright install chromium"

# fixture 凭据契约（与 tests/fixtures/su_site/server.py 常量一致；
# test_su_e2e_site.py 锁死该契约，password 含 '#' 字符——全流程走
# config JSON 文件传递，规避 shell 对 # 的注释/历史语义风险）
SU_USER="su_test"
SU_PASSWORD="SuTest#2026"
SU_PASSWORD_WRONG="WRONG-password-0000"
# PII 手机号样本（fixture /users 页内嵌；红线扫描：明文 0 命中 + 脱敏形态命中）
PHONE_SAMPLE="13812345678"
PHONE_REDACTED="<REDACTED:phone>"

# 场景结果矩阵（"标签:结果" 列表；结果 ∈ PASS/FAIL/SKIP）
RESULTS=()
FAIL_COUNT=0

log()  { printf '[su-e2e] %s\n' "$*"; }
pass() { RESULTS+=("$1:PASS"); log "PASS  $1"; }
fail() { RESULTS+=("$1:FAIL"); FAIL_COUNT=$((FAIL_COUNT + 1)); log "FAIL  $1${2:+ —— $2}"; }
skip() { RESULTS+=("$1:SKIP"); log "SKIP  $1${2:+ —— $2}"; }

# ---------------------------------------------------------------------------
# 清理 trap：杀光本脚本拉起的全部子进程 + 删除临时工作区
# ---------------------------------------------------------------------------
CHILD_PIDS=()
# 看门狗 pid 登记簿：run_su 每次拉起的 sleep 300 看门狗都登记于此。
# 命令替换（$( )）子 shell 里的 kill/wait 各自作用于子 shell 的作业表，
# 主 shell 作业表里的看门狗必须回到主流程统一收割——否则收尾 EXIT trap
# 的 `wait` 会挂等到 sleep 300 到期，整批挂死 5 分钟（2026-09-30 实测
# 教训：--only 10-13 批次在场景[10]命令替换后挂满看门狗时长）。
WATCHDOG_PIDS=()

# reap_watchdogs：收割已结束的登记看门狗并清空（主流程调用；对仍在
# sleep 的看门狗 wait 会阻塞——主流程调用点均在 run_su 同步收尾后，
# 看门狗已被 watchdog_stop kill，wait 立即返回）
reap_watchdogs() {
  local pid
  for pid in "${WATCHDOG_PIDS[@]:-}"; do
    if [ -n "${pid}" ]; then
      kill -TERM "${pid}" 2>/dev/null || true
      wait "${pid}" 2>/dev/null || true
    fi
  done
  WATCHDOG_PIDS=()
  WATCHDOG_PID=""
}

cleanup() {
  local pid
  for pid in "${CHILD_PIDS[@]:-}"; do
    if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
      kill -TERM "${pid}" 2>/dev/null || true
    fi
  done
  # 看门狗一并停掉（只 kill 不 wait——wait 未注册 pid 会挂等到 sleep 300
  # 到期；已退出的由下方轮询回收，未退出的残留由 shell 退出时系统回收）
  for pid in "${WATCHDOG_PIDS[@]:-}"; do
    [ -n "${pid}" ] && kill -TERM "${pid}" 2>/dev/null || true
  done
  # 给子进程 2 秒优雅退出后强杀残留（轮询式，不用裸 sleep+wait）
  local waited=0
  while [ "${waited}" -lt 20 ]; do
    local alive=0
    for pid in "${CHILD_PIDS[@]:-}"; do
      [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null && alive=1
    done
    [ "${alive}" = "0" ] && break
    sleep 0.1
    waited=$((waited + 1))
  done
  for pid in "${CHILD_PIDS[@]:-}"; do
    if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
      kill -KILL "${pid}" 2>/dev/null || true
    fi
  done
  # SU_E2E_KEEP=1：保留临时工作区供事后排查（崩溃现场/产物），默认删除
  if [ "${SU_E2E_KEEP:-0}" = "1" ]; then
    log "SU_E2E_KEEP=1 —— 保留工作区 ${WORK_ROOT}"
    return 0
  fi
  rm -rf "${WORK_ROOT}"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

# run_su <场景目录> <参数...>：跑 SU CLI，stdout+stderr 汇入 场景目录/run.log，
# 全局 RC 置为退出码（set -u 安全：命令替换内赋值经全局变量回传）。
# macOS 无 coreutils timeout：以 per-PID 看门狗子 shell 兜底（SU_PID 全局）。
RC=0
SU_PID=""
watchdog_stop() {
  # 停止看门狗（只 kill 不 wait——命令替换子 shell 里 wait 未注册 pid 会
  # 挂等到 sleep 300 到期；回收统一交给主流程 reap_watchdogs / cleanup）
  if [ -n "${WATCHDOG_PID:-}" ]; then
    kill "${WATCHDOG_PID}" 2>/dev/null || true
    WATCHDOG_PID=""
  fi
}
WATCHDOG_PID=""
run_su() {
  local scen_dir="$1"; shift
  mkdir -p "${scen_dir}"
  # exec 形态启动：子 shell 被 exec 替换为 python 进程本体，$! 即 SU 进程
  # 真实 pid（2026-09-28 实测教训：旧写法 `( cd && python ) &` 的 $! 是子
  # shell pid——bash 对单命令子 shell 虽也 exec 优化，但复合命令（cd && …）
  # 不优化，看门狗/清理 kill 的是子 shell 而非 SU 进程，SU 永不超时被杀）
  ( cd "${REPO_ROOT}" && exec "${PYTHON}" -B "${SU_CLI}" "$@" ) \
    >"${scen_dir}/run.log" 2>&1 &
  SU_PID=$!
  CHILD_PIDS+=("${SU_PID}")
  # 看门狗：300s 未退出则 KILL（防挂死拖垮整套件）；登记 WATCHDOG_PIDS
  # 供 cleanup 兜底回收（命令替换子 shell 里 watchdog_stop 的 wait 会
  # 挂等到 sleep 300 到期——主流程 reap_watchdogs 非阻塞收割防挂死）
  (
    sleep 300
    kill -KILL "${SU_PID}" 2>/dev/null || true
  ) &
  WATCHDOG_PID=$!
  WATCHDOG_PIDS+=("${WATCHDOG_PID}")
  wait "${SU_PID}"; RC=$?
  watchdog_stop
  reap_watchdogs
}

# wait_site_ready <port> [最大尝试次数]：轮询 write-counter 直到站点可服务
wait_site_ready() {
  local port="$1" tries="${2:-50}" i
  for ((i = 0; i < tries; i++)); do
    if curl -s -m 2 "http://127.0.0.1:${port}/api/write-counter" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.2
  done
  return 1
}

# site_write_counter <port>：读取站点写计数（站点已死/无响应返回 -1）
site_write_counter() {
  local raw
  raw="$(curl -s -m 3 "http://127.0.0.1:$1/api/write-counter" 2>/dev/null)"
  printf '%s' "${raw}" | "${PYTHON}" -c 'import json,sys
try:
    print(int(json.load(sys.stdin)["count"]))
except Exception:
    print(-1)'
}

# json_get <文件> <python表达式 d 取值>：JSON 字段提取（无 jq 依赖）
json_get() {
  "${PYTHON}" - "$1" "$2" <<'PYEOF'
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    d = json.load(fh)
try:
    # 内建注入 len/str/int/float：eval 的 __builtins__ 置空后表达式仍需
    # 基础类型转换（2026-09-30 教训：str(...) 表达式报 name 'str' not defined）
    print(eval(sys.argv[2], {"__builtins__": {}},
               {"d": d, "len": len, "str": str, "int": int, "float": float}))
except Exception as exc:  # 提取失败打印哨兵供断言捕获
    print("ERR:{0}".format(exc))
PYEOF
}

# 场景[4]首跑参数：delay=3000ms 使非慢页处理节奏稳定（每页 ≥3s）——8 页
# 预算 + /slow goto 超时使首跑时长有界（500ms 节奏下 8s 即跑完、信号根本
# 来不及参与，2026-09-28 实测教训）；--page-timeout-ms 8000——即便中断窗口
# 撞上 /slow（goto 挂起 CDP 管道期间 Python 信号处理被拖住），8s 也会超时
# 返回让信号处理跟上；crawler 侧心跳停滞看门狗为挂起窗口兜底（见
# site_crawler._watch_interrupt 通道二）
S4_SIGINT_AT=6

# run_scoped_count <db> <SQL模板(%s=run_id 占位)>：按 run_id 作用域计数——
# --resume 复用同一 run 行（页数跨首跑/续跑累积），跨阶段比较必须锁定 run
run_scoped_count() {
  local db="$1" sql="$2" run_id
  run_id="$(sqlite3 "${db}" "SELECT run_id FROM run_meta ORDER BY started_at DESC LIMIT 1" 2>/dev/null)"
  [ -n "${run_id}" ] || { echo ""; return; }
  sqlite3 "${db}" "$(printf "${sql}" "${run_id}")" 2>/dev/null
}

# ---------------------------------------------------------------------------
# SFD（专家详说）共享工装（场景 [9]-[13]，ARCH-SFD-001 §10.3）
# ---------------------------------------------------------------------------

# sfd_seed_workspace <sys_root> <sid>：builder 种子库 + 直插合法 findings
# （render-only 前置校验要求磁盘 understanding.json 含 findings 段）。
# 直插 findings 表属测试设施建设（与 builder 直连 INSERT、单测工装
# fixtures/sfd_harness.inject_findings 同性质），入库形态过
# validate_findings_schema 口径。
sfd_seed_workspace() {
  local sys_root="$1" sid="$2"
  mkdir -p "${sys_root}/state"
  # builder CLI 成功时 stdout 打印 seed_ids JSON（id 映射）——消费其退出码
  # 即可，输出必须重定向丢弃：sfd_seed_workspace 被 sfd_builder_chain 经
  # $() 调用，其 stdout 全部拼进 sys_root 返回值（2026-09-30 实测教训：
  # seed_ids JSON 混入 sys_root 导致草稿放置 FileNotFoundError）
  "${PYTHON}" -B "${STATE_BUILDER_PY}" \
    "${sys_root}/state/understanding.sqlite" "${sid}" completed >/dev/null || return 1
  "${PYTHON}" -B - "${sys_root}" <<'PYEOF' || return 1
"""builder 库直插 1 条合法 findings（evidence_refs 引用真实种子行 id）。"""
import json
import sqlite3
import sys
import time
from pathlib import Path

sys_root = Path(sys.argv[1])
db = sys_root / "state" / "understanding.sqlite"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row
try:
    pid = conn.execute(
        "SELECT page_id FROM pages WHERE url_key='/orders'").fetchone()["page_id"]
    tid = conn.execute(
        "SELECT table_id FROM db_tables WHERE table_name='orders'"
    ).fetchone()["table_id"]
    finding = {
        "claim": "订单列表页经 GET /api/orders 读取 orders 表（e2e SFD 注入结论）",
        "kind": "mapping",
        "confidence": "high",
        "evidence_refs": ["pages:{0}".format(pid), "db_tables:{0}".format(tid)],
    }
    conn.execute(
        "INSERT INTO findings(claim, confidence, evidence_refs, status,"
        " kind, created_at) VALUES(?,?,?,?,?,?)",
        (finding["claim"], finding["confidence"],
         json.dumps(finding["evidence_refs"], ensure_ascii=False),
         "proposed", finding["kind"], time.time()))
    conn.commit()
finally:
    conn.close()
# render-only 磁盘前置：最小 understanding.json 含 findings 段
# （findings 由渲染层从状态库回灌，此处与 render-only 校验口径对齐）
(sys_root / "understanding.json").write_text(json.dumps(
    {"findings": [finding]}, ensure_ascii=False, indent=2), encoding="utf-8")
PYEOF
}

# sfd_place_drafts <sys_root> <keep>：按 PROMPT-SFD 契约放置合规草稿。
# keep=all 五份齐；keep=part 只放 01+04（S-2 降级素材）。引用编号动态取
# evidence-index 首条 seq（跨 render 稳定）。
sfd_place_drafts() {
  local sys_root="$1" keep="$2"
  # stderr 汇入场景级排障日志（本函数经 $() 调用——stdout 必须绝对纯净，
  # 任何杂散输出都会拼进 sys_root 返回值；stdout 成功时无输出、失败时由
  # python traceback 走 stderr）
  local sys_parent
  sys_parent="$(dirname "${sys_root}")"
  "${PYTHON}" -B - "${sys_root}" "${keep}" 2>>"${sys_parent}/place_drafts.err" <<'PYEOF' || return 1
"""放置合规专家草稿（首行节头逐字、04 含 SFD-SECTION: 5 标记、真实 E-n）。"""
import json
import sys
from pathlib import Path

sys_root = Path(sys.argv[1])
keep = sys.argv[2]
entries = json.loads((sys_root / "evidence" / "evidence-index.json")
                     .read_text("utf-8"))["entries"]
e1 = "E{0:04d}".format(entries[0]["seq"])
sections = sys_root / "detailed" / "sections"
sections.mkdir(parents=True, exist_ok=True)
drafts = {
    "01-architecture.doc.md":
        "# 1. 系统定位与技术架构\n\n订单管理系统，三层架构（{0}）。\n".format(e1),
    "02-product.doc.md":
        "# 2. 功能全景与业务流程\n\n核心流：下单落 orders 表（{0}）。\n".format(e1),
    "03-pages.doc.md":
        "# 3. 页面功能详说\n\n订单列表页展示订单明细（{0}）。\n".format(e1),
    "04-data-semantics.doc.md":
        "# 4. 数据模型业务语义\n\norders 表存订单主数据（{0}）。\n"
        "<!-- SFD-SECTION: 5 -->\n\nGET /api/orders 供订单列表页消费。\n".format(e1),
    "05-quality.doc.md":
        "# 6. 质量盲区与风险建议\n\nT3 删除动作未执行，风险高（{0}）。\n".format(e1),
}
wanted = sorted(drafts) if keep == "all" else [
    "01-architecture.doc.md", "04-data-semantics.doc.md"]
for name in wanted:
    (sections / name).write_text(drafts[name], encoding="utf-8")
PYEOF
}

# start_scene_site <场景名>：为场景独立起 su_site 进程（写计数天然归零），
# 全局 SITE_PORT / SITE_PID。
# SU_SITE_ACCESS_LOG（测试设施，2026-09-28 e2e 场景[2]302 来源取证）：
# fixture server 把每个请求的 <ts_ms> <method> <path> -> <status> 落
# 场景目录 site_access.log，服务端侧直接证明 302 的真实归属。
start_scene_site() {
  local scen_dir="${WORK_ROOT}/$1"
  mkdir -p "${scen_dir}"
  SU_SITE_ACCESS_LOG="${scen_dir}/site_access.log" \
    "${PYTHON}" -B "${SU_SITE_PY}" 0 >"${scen_dir}/site.out" 2>&1 &
  local pid=$!
  CHILD_PIDS+=("${pid}")
  local i port=""
  for ((i = 0; i < 50; i++)); do
    port="$(sed -n 's/^SU_SITE_PORT=\([0-9]*\)$/\1/p' "${scen_dir}/site.out" 2>/dev/null | head -1)"
    [ -n "${port}" ] && break
    sleep 0.1
  done
  if [ -z "${port}" ]; then
    fail "$1:站点启动" "未输出 SU_SITE_PORT（见 ${scen_dir}/site.out）"
    return 1
  fi
  if ! wait_site_ready "${port}"; then
    fail "$1:站点就绪" "write-counter 端点 10s 内不可达"
    return 1
  fi
  SITE_PORT="${port}"
  SITE_PID="${pid}"
  return 0
}

# make_config <路径> <port> [password]：生成 SU config JSON（无 database/redis
# 段——两透镜走"未配置"降级；system_id 固定 su-fixture 保证输出目录确定）。
make_config() {
  local path="$1" port="$2" password="${3:-${SU_PASSWORD}}"
  "${PYTHON}" - "$path" "$port" "$password" "$SU_USER" <<'PYEOF'
import json, sys
path, port, password, username = sys.argv[1:5]
cfg = {
    "system": {
        "base_url": "http://127.0.0.1:{0}".format(port),
        "login_url": "/login",
        "username": username,
        "password": password,
        "success_hint": 'nav a[href="/settings"]',
    },
}
with open(path, "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, ensure_ascii=False, indent=2)
PYEOF
  chmod 600 "${path}"
}

# ---------------------------------------------------------------------------
# 场景 [0]：playwright + chromium 前置探测（架构 §11.3：探测"可用"而非"已装"）
# ---------------------------------------------------------------------------
log "===== 场景 [0] playwright/chromium 前置探测 ====="
DEPS_OK=0
if "${PYTHON}" - <<'PYEOF' 2>/dev/null
# 真实启动一次 chromium（非仅 import）：探测"可用"而非"已安装"
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    browser.close()
print("chromium-launch-ok")
PYEOF
then
  DEPS_OK=1
  pass "[0]playwright探测"
else
  skip "[0]playwright探测" "playwright 或 chromium 不可用"
  log "安装指引：${PLAYWRIGHT_GUIDE}"
fi

# ---------------------------------------------------------------------------
# 场景 [1]：fixture 站点纯 HTTP 冒烟（零 playwright 依赖，任何时候都执行）
# 依赖缺失时唯一照常运行的场景——登录 401、write-counter、路由可达等
# 纯 HTTP 断言与浏览器无关（用户红线：playwright 缺失浏览器场景 SKIP，
# 但 fixture 自身 HTTP 断言仍要跑）。
# ---------------------------------------------------------------------------
log "===== 场景 [1] fixture 纯 HTTP 冒烟 ====="
S1="${WORK_ROOT}/s1"; mkdir -p "${S1}"
S1_ERR=""
if start_scene_site s1; then
  BASE="http://127.0.0.1:${SITE_PORT}"
  # a) 未登录访问受保护页 → 302 /login（cookie 校验中间件）
  LOC="$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' -m 5 "${BASE}/orders")"
  [ "${LOC}" = "302 ${BASE}/login" ] || S1_ERR="未登录 /orders 响应=${LOC}（期望 302 ${BASE}/login）"
  # b) 登录表单可达（GET /login 200 + 表单控件）
  if ! curl -s -m 5 "${BASE}/login" | grep -q 'name="password"'; then
    S1_ERR="${S1_ERR} /login 缺密码表单控件"
  fi
  # c) 错误凭据 POST → 401（凭据错误场景 exit 4 的站点侧判据）
  CODE="$(curl -s -o /dev/null -w '%{http_code}' -m 5 -X POST \
    --data-urlencode "username=${SU_USER}" --data-urlencode "password=${SU_PASSWORD_WRONG}" \
    "${BASE}/login")"
  [ "${CODE}" = "401" ] || S1_ERR="${S1_ERR} 错误凭据登录码=${CODE}（期望 401）"
  # d) 正确凭据 POST → 302 + Set-Cookie su_session（凭据含 '#'，走 urlencode）
  rm -f "${S1}/cookies.txt"
  HDR_FILE="${S1}/login_headers.txt"
  CODE="$(curl -s -o /dev/null -D "${HDR_FILE}" -w '%{http_code}' -m 5 -X POST \
    --data-urlencode "username=${SU_USER}" --data-urlencode "password=${SU_PASSWORD}" \
    -c "${S1}/cookies.txt" "${BASE}/login")"
  [ "${CODE}" = "302" ] || S1_ERR="${S1_ERR} 正确凭据登录码=${CODE}（期望 302）"
  grep -qi "su_session=" "${HDR_FILE}" || S1_ERR="${S1_ERR} 登录响应缺 Set-Cookie su_session"
  # e) 带 session cookie 路由可达（≥12 页清单抽样：首页/订单/用户管理/外链/下载）
  for path in / /dashboard /orders /users /external /downloads /reports; do
    CODE="$(curl -s -o /dev/null -w '%{http_code}' -m 5 -b "${S1}/cookies.txt" "${BASE}${path}")"
    [ "${CODE}" = "200" ] || S1_ERR="${S1_ERR} 已登录 ${path} 码=${CODE}（期望 200）"
  done
  # f) /users 手机号样本在位（PII 红线素材——站点侧保证样本存在）
  curl -s -m 5 -b "${S1}/cookies.txt" "${BASE}/users" | grep -qF "${PHONE_SAMPLE}" \
    || S1_ERR="${S1_ERR} /users 缺手机号样本"
  # g) /api/orders JSON 端点可达且键名与 orders 表列一致（映射证据素材）
  API_KEYS="$(curl -s -m 5 "${BASE}/api/orders" \
    | "${PYTHON}" -c 'import json,sys;print(",".join(sorted(json.load(sys.stdin)["orders"][0])))')"
  [ "${API_KEYS}" = "customer_name,order_id,status,total_amount" ] \
    || S1_ERR="${S1_ERR} /api/orders 键集=${API_KEYS}（期望 customer_name,order_id,status,total_amount）"
  # h) write-counter 初始为 0；纯 GET 探测不计数；POST 计数 +1
  WC="$(site_write_counter "${SITE_PORT}")"
  [ "${WC}" = "0" ] || S1_ERR="${S1_ERR} 新实例 write-counter=${WC}（期望 0）"
  curl -s -o /dev/null -m 5 -b "${S1}/cookies.txt" "${BASE}/orders"
  WC="$(site_write_counter "${SITE_PORT}")"
  [ "${WC}" = "0" ] || S1_ERR="${S1_ERR} GET 探测后 write-counter=${WC}（期望 0）"
  # i) 会话失效控制端点：置位 → 下一个鉴权请求 302 回 /login（自动重登素材）
  curl -s -o /dev/null -m 5 -X POST "${BASE}/api/test/invalidate-session"
  LOC="$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' -m 5 -b "${S1}/cookies.txt" "${BASE}/orders")"
  [ "${LOC}" = "302 ${BASE}/login" ] || S1_ERR="${S1_ERR} 失效开关后 /orders=${LOC}（期望 302 回登录）"
  # j) 重登恢复（开关只消费一次）；写计数 = 1（invalidate-session 属写请求）
  curl -s -o /dev/null -m 5 -X POST -c "${S1}/cookies.txt" \
    --data-urlencode "username=${SU_USER}" --data-urlencode "password=${SU_PASSWORD}" \
    "${BASE}/login"
  CODE="$(curl -s -o /dev/null -w '%{http_code}' -m 5 -b "${S1}/cookies.txt" "${BASE}/orders")"
  [ "${CODE}" = "200" ] || S1_ERR="${S1_ERR} 重登后 /orders=${CODE}（期望 200）"
  WC="$(site_write_counter "${SITE_PORT}")"
  [ "${WC}" = "1" ] || S1_ERR="${S1_ERR} invalidate-session 后 write-counter=${WC}（期望 1）"
  # k) 下载端点 Content-Disposition 附件头（响应侧下载通道素材）
  curl -s -m 5 -D - -o /dev/null -b "${S1}/cookies.txt" "${BASE}/downloads/attachment" \
    | grep -qi "Content-Disposition: attachment" \
    || S1_ERR="${S1_ERR} /downloads/attachment 缺附件头"
  # l) 外域链接页素材（https://example.invalid —— route guard blocked_origin 素材）
  curl -s -m 5 -b "${S1}/cookies.txt" "${BASE}/external" | grep -qF "https://example.invalid/" \
    || S1_ERR="${S1_ERR} /external 缺 example.invalid 外域素材"
  if [ -z "${S1_ERR}" ]; then pass "[1]fixture冒烟"; else fail "[1]fixture冒烟" "${S1_ERR}"; fi
  kill -TERM "${SITE_PID}" 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# 场景 [2]~[7]（playwright 缺失时整体显式 SKIP，绝不假通过）
# ---------------------------------------------------------------------------
if [ "${DEPS_OK}" -ne 1 ]; then
  for s in "[2]全链路" "[3]安全红线" "[4]断点续跑" "[5]预算" "[6]凭据错误" "[7]render-only" "[9]SFD全链路"; do
    skip "${s}" "playwright/chromium 缺失（见场景[0]）"
  done
else
  # ---- 场景 [2]：全链路 ----------------------------------------------------
  log "===== 场景 [2] 全链路采集 ====="
  S2="${WORK_ROOT}/s2"; mkdir -p "${S2}"
  S2_DIR=""   # 全链路产物目录（场景[3]/[7]/[9] 素材，独立重跑兜底时覆写）
  # 未选中 [2] 但选中了产物消费方 [3]/[7]/[9]：照常跑 [2] 供素材（行为与全量
  # 一致），只在收尾如实打 SKIP——绝不因 --only 削弱消费方场景的真实性
  S2_SELECTED=1
  scenario_selected 2 || S2_SELECTED=0
  S2_SUPPLY=0
  if [ "${S2_SELECTED}" = "0" ]; then
    if scenario_selected 3 || scenario_selected 7; then
      S2_SUPPLY=1
      log "场景[2]未选中但 [3]/[7] 需要其产物——照常执行供素材（收尾如实 SKIP）"
    fi
  fi
  if [ "${S2_SELECTED}" = "0" ] && [ "${S2_SUPPLY}" = "0" ]; then
    skip "[2]全链路" "--only 未选择（S2_DIR 置占位，[3]/[7]/[9] 走独立兜底）"
  elif start_scene_site s2; then
    make_config "${S2}/config.json" "${SITE_PORT}"
    # REQ-SU-004.4 自动重登链路注入（2026-09-28 P1-1 修复回归锁）：
    # SU 运行前置位 fixture 的"会话服务端失效"一次性开关——SU 阶段 1
    # 登录签发的新会话在 BFS 首个鉴权页（base_url '/'）被撤销并 302 回
    # /login，crawler 检测到回跳后必须自动重登并补采当前页。
    # 开关时序自洽依据（fixture server.py 场景[1] 实测语义）：登录 POST
    # 在失效请求之前发生（新会话不被追溯撤销）；/search 端点不消费开关
    # （T2 素材导航不受干扰）。invalidate-session 属写请求：write-counter
    # 基线相应从 0 调整为 1（见下方 WC2 断言——红线口径不变：增量仍须为
    # 0，即 SU 自身全程零写）。
    curl -s -o /dev/null -m 5 -X POST "http://127.0.0.1:${SITE_PORT}/api/test/invalidate-session"
    # run_su 内置 300s 看门狗；--page-timeout-ms 15000——/slow 页 sleep 40s
    # 必然超时，按设计降级为 timeout 页，不阻塞其余页面
    # SU_E2E_VERBOSE=1：追加 --verbose（DEBUG 级、仍过 RedactingFormatter
    # 脱敏）——场景卡死归因取证用，默认不加、正式跑日志口径不变
    S2_VERBOSE_ARGS=()
    [ "${SU_E2E_VERBOSE:-0}" = "1" ] && S2_VERBOSE_ARGS=(--verbose)
    run_su "${S2}" --config "${S2}/config.json" \
      --out "${S2}/out" --system-id su-fixture \
      --skip-llm-phase --delay-ms 100 --max-pages 15 \
      --page-timeout-ms 15000 ${S2_VERBOSE_ARGS[@]+"${S2_VERBOSE_ARGS[@]}"}
    RC=$?
    S2_DIR="${S2}/out/su-fixture"
    S2_ERR=""
    [ "${RC}" -eq 0 ] || S2_ERR="退出码 ${RC}（期望 0）"
    S2DB="${S2_DIR}/state/understanding.sqlite"
    # 10 节标题逐节断言（## N. 前缀，标题文本与 SECTION_TITLES 对齐）
    if [ -z "${S2_ERR}" ]; then
      MD="${S2_DIR}/UNDERSTANDING.md"
      if [ ! -f "${MD}" ]; then
        S2_ERR="UNDERSTANDING.md 缺失"
      else
        for sec in "## 1. 系统概览" "## 2. 功能地图" "## 3. 导航图" \
                   "## 4. 数据模型" "## 5. UI ↔ 数据映射" "## 6. 缓存与中间件" \
                   "## 7. 业务规则汇编" "## 8. API 面" "## 9. 证据附录" \
                   "## 10. 未验证推断与未覆盖清单"; do
          grep -qF "${sec}" "${MD}" || S2_ERR="${S2_ERR} 缺节[${sec}]"
        done
      fi
    fi
    # 机读产物在位
    if [ -z "${S2_ERR}" ]; then
      [ -f "${S2_DIR}/understanding.json" ] || S2_ERR="understanding.json 缺失"
      [ -f "${S2_DIR}/summary.json" ] || S2_ERR="summary.json 缺失"
    fi
    # write-counter 红线：SU 全程零写（对 fixture server 直连断言）。
    # 基线 = 1：上方注入的 POST /api/test/invalidate-session 属写请求
    # （意动注入的会话失效模拟，非 SU 行为）；SU 自身仍必须零写——
    # 计数超过 1 即 route guard 红线失守（回归锁：基线口径与本文件
    # 场景[1] i/j 步 invalidate-session 计数 +1 的实测语义一致）
    if [ -z "${S2_ERR}" ]; then
      WC2="$(site_write_counter "${SITE_PORT}")"
      [ "${WC2}" = "1" ] || S2_ERR="write-counter=${WC2}（期望 1——注入基线）"
    fi
    # REQ-SU-004.4 自动重登证据（P1-1 修复回归锁）：run.log 必须出现
    # crawler 的回跳检测与重登补采日志行——缺失说明会话失效自动重登
    # 链路又断了（接线回退），场景直接判 FAIL
    if [ -z "${S2_ERR}" ]; then
      grep -q "检测到回跳登录页" "${S2}/run.log" \
        || S2_ERR="${S2_ERR} run.log 缺回跳登录页检测日志（REQ-SU-004.4 回归）"
      grep -q "重登成功，重新导航当前页补采本轮" "${S2}/run.log" \
        || S2_ERR="${S2_ERR} run.log 缺重登成功补采日志（REQ-SU-004.4 回归）"
    fi
    # blocked 含 aborted_method：/orders 页 load() 在页面加载时即发起真实
    # POST /api/orders/delete（页面加载触发比 delSel 按钮可靠——crawler 主
    # 线程 T2/快照操作会挤占 JS 任务队列，按钮点击路径不保证执行）。
    # route guard 必须 abort 该 POST 并记 aborted_method
    AB="$(sqlite3 "${S2DB}" \
      "SELECT COUNT(*) FROM blocked_events WHERE kind='aborted_method' AND method='POST'" 2>/dev/null)"
    if [ -z "${AB}" ] || [ "${AB}" -lt 1 ] 2>/dev/null; then
      S2_ERR="${S2_ERR} blocked_events 缺 aborted_method POST（实际 ${AB:-查询失败}）"
    fi
    # 搜索表单 T2 执行：/reports 页 GET 表单（action=/search）被 fill_neutral
    # + requestSubmit 真实提交 → 入边存在且动作记录 executed=1 / tier='T2'
    T2E="$(sqlite3 "${S2DB}" \
      "SELECT COUNT(*) FROM page_actions WHERE tier='T2' AND executed=1" 2>/dev/null)"
    if [ -z "${T2E}" ] || [ "${T2E}" -lt 1 ] 2>/dev/null; then
      S2_ERR="${S2_ERR} 缺 T2 已执行动作（实际 ${T2E:-查询失败}）"
    fi
    # 外域进 blocked_origins：/external 页 gateway-form 是显式 GET 表单，T2
    # 真实 requestSubmit 导航到 example.invalid → route handler abort 并记
    # blocked_origin，页边界 drain 落库 + 写 state/blocked_origins.json
    BO="$(sqlite3 "${S2DB}" \
      "SELECT COUNT(*) FROM blocked_events WHERE kind='blocked_origin'" 2>/dev/null)"
    if [ -z "${BO}" ] || [ "${BO}" -lt 1 ] 2>/dev/null; then
      S2_ERR="${S2_ERR} blocked_events 缺 blocked_origin（实际 ${BO:-查询失败}）"
    elif ! grep -q "example.invalid" "${S2_DIR}/state/blocked_origins.json" 2>/dev/null; then
      S2_ERR="${S2_ERR} blocked_origins.json 缺 example.invalid 汇总"
    fi
    # hash 页入图：/orders#/orders/N 三条 hash 路由页必须入 pages 表且入边
    HP="$(sqlite3 "${S2DB}" \
      "SELECT COUNT(*) FROM pages WHERE url_key LIKE '%#/orders/%'" 2>/dev/null)"
    if [ -z "${HP}" ] || [ "${HP}" -lt 1 ] 2>/dev/null; then
      S2_ERR="${S2_ERR} hash 路由页未入 pages 表（实际 ${HP:-查询失败}）"
    fi
    HE="$(sqlite3 "${S2DB}" \
      "SELECT COUNT(*) FROM edges WHERE to_key LIKE '%#/orders/%'" 2>/dev/null)"
    if [ -z "${HE}" ] || [ "${HE}" -lt 1 ] 2>/dev/null; then
      S2_ERR="${S2_ERR} hash 路由页无入边（实际 ${HE:-查询失败}）"
    fi
    # 下载被拒：.zip/.csv 后缀启发式（请求侧 abort）或 page.on('download')
    # 响应侧通道，任一命中即 download 事件 ≥1（双通道均只记录不落文件）
    DL="$(sqlite3 "${S2DB}" \
      "SELECT COUNT(*) FROM blocked_events WHERE kind='download'" 2>/dev/null)"
    if [ -z "${DL}" ] || [ "${DL}" -lt 1 ] 2>/dev/null; then
      S2_ERR="${S2_ERR} 缺 download 拦截事件（实际 ${DL:-查询失败}）"
    fi
    # API 观测：/orders 页 fetch /api/orders 被记录（GET 放行 + 观测通道）
    AO="$(sqlite3 "${S2DB}" \
      "SELECT COUNT(*) FROM api_observations WHERE url_path LIKE '%/api/orders%' AND method='GET'" 2>/dev/null)"
    if [ -z "${AO}" ] || [ "${AO}" -lt 1 ] 2>/dev/null; then
      S2_ERR="${S2_ERR} api_observations 缺 /api/orders GET（实际 ${AO:-查询失败}）"
    fi
    if [ "${S2_SELECTED}" = "1" ]; then
      if [ -z "${S2_ERR}" ]; then pass "[2]全链路"; else fail "[2]全链路" "${S2_ERR}"; fi
    elif [ "${S2_SUPPLY}" = "1" ]; then
      # 供素材模式：断言照常计算并打印（不达标以 log 警示），矩阵如实 SKIP
      [ -z "${S2_ERR}" ] || log "警示：供素材的 [2] 断言不达标：${S2_ERR}"
      skip "[2]全链路" "--only 未选择（已为 [3]/[7] 供素材）"
    fi
    kill -TERM "${SITE_PID}" 2>/dev/null || true
  fi

  # ---- 场景 [3]：安全红线（复用场景[2]产物 + 独立重跑兜底） ----------------
  # 红线口径：运行目录全文件（含 state/understanding.sqlite、snapshots、
  # logs、storage_state.json）grep 凭据明文与手机号样本明文必须 0 命中；
  # 快照中手机号必须呈 <REDACTED:phone> 脱敏形态（真实脱敏而非删除）。
  log "===== 场景 [3] 安全红线 ====="
  S3="${WORK_ROOT}/s3"; mkdir -p "${S3}"
  S3_ERR=""
  S3_SELECTED=1
  if ! scenario_selected 3; then
    S3_SELECTED=0
    skip "[3]安全红线" "--only 未选择"
  fi
  if [ "${S3_SELECTED}" = "1" ] && [ ! -f "${S2_DIR:-/nonexistent}/UNDERSTANDING.md" ]; then
    log "场景[2]产物缺失——场景[3]独立重跑兜底"
    if start_scene_site s3; then
      make_config "${S3}/config.json" "${SITE_PORT}"
      run_su "${S3}" --config "${S3}/config.json" --out "${S3}/out" \
        --system-id su-fixture --skip-llm-phase --delay-ms 100 \
        --max-pages 15 --page-timeout-ms 15000 || true
      S2_DIR="${S3}/out/su-fixture"
    else
      S3_ERR="兜底站点启动失败"
    fi
  fi
  if [ "${S3_SELECTED}" = "1" ] && [ -z "${S3_ERR}" ]; then
    if [ ! -f "${S2_DIR}/UNDERSTANDING.md" ]; then
      S3_ERR="红线素材缺失（UNDERSTANDING.md 不存在）"
    else
      # 红线①a：fixture 密码明文在运行目录全文件（含 sqlite 二进制、日志、
      # storage_state.json）0 出现——grep -r 对二进制匹配同样报命中
      if grep -rqF "${SU_PASSWORD}" "${S2_DIR}" 2>/dev/null; then
        S3_ERR="运行目录发现密码明文（红线①违例）"
      fi
      # 红线①b：手机号样本明文 0 命中（PII 值形态正则必须先于任何落盘）
      if grep -rqF "${PHONE_SAMPLE}" "${S2_DIR}" 2>/dev/null; then
        S3_ERR="${S3_ERR} 运行目录发现手机号明文（PII 红线违例）"
      fi
      # 红线①c：正向证据——/users 页快照确实采集到手机号且呈脱敏形态
      # （证明扫描范围真实覆盖了含手机号的页面，而非"页面根本没采到"）
      SNAP_DIR="${S2_DIR}/snapshots"
      if ! grep -rqF "${PHONE_REDACTED}" "${SNAP_DIR}" 2>/dev/null; then
        S3_ERR="${S3_ERR} 快照缺 ${PHONE_REDACTED} 脱敏形态（手机号页未采集或未脱敏）"
      fi
      # 外域零入库：pages/api_observations 两表 0 条 example.invalid
      EX_API="$(sqlite3 "${S2_DIR}/state/understanding.sqlite" \
        "SELECT COUNT(*) FROM api_observations WHERE url_path LIKE '%example.invalid%'" 2>/dev/null)"
      [ "${EX_API:-0}" = "0" ] || S3_ERR="${S3_ERR} api_observations 出现 example.invalid（${EX_API} 条）"
      EX_PAGES="$(sqlite3 "${S2_DIR}/state/understanding.sqlite" \
        "SELECT COUNT(*) FROM pages WHERE url LIKE '%example.invalid%'" 2>/dev/null)"
      [ "${EX_PAGES:-0}" = "0" ] || S3_ERR="${S3_ERR} pages 表出现 example.invalid（${EX_PAGES} 条）"
      # 红线⑤：T3 危险动作零执行（page_actions executed=0 的 T3 计数 ≥1）
      T3U="$(sqlite3 "${S2_DIR}/state/understanding.sqlite" \
        "SELECT COUNT(*) FROM page_actions WHERE tier='T3' AND executed=0" 2>/dev/null)"
      if [ -z "${T3U}" ] || [ "${T3U}" -lt 1 ] 2>/dev/null; then
        S3_ERR="${S3_ERR} 缺 T3 未执行动作记录（实际 ${T3U:-查询失败}）"
      fi
    fi
  fi
  if [ "${S3_SELECTED}" = "1" ] && [ -z "${S3_ERR}" ]; then pass "[3]安全红线"; else [ "${S3_SELECTED}" = "1" ] && fail "[3]安全红线" "${S3_ERR}"; fi

  # ---- 场景 [4]：断点续跑 ---------------------------------------------------
  log "===== 场景 [4] 断点续跑 ====="
  S4="${WORK_ROOT}/s4"; mkdir -p "${S4}"
  S4_ERR=""
  if ! scenario_selected 4; then
    skip "[4]断点续跑" "--only 未选择"
  elif start_scene_site s4; then
    make_config "${S4}/config.json" "${SITE_PORT}"
    # 首跑：delay=3000（整站 20 页 ×3s，采集期约 60s，中断窗口覆盖全采集
    # 期——delay 过小会整轮采完、信号无处参与，2026-09-28 实测教训）；
    # --max-pages 8——页预算进程级计数，8 页 + /slow goto 超时使首跑时长
    # 有界（即便全部 SIGINT 落入挂起窗口，首跑也只会"预算耗尽"收口，
    # 绝不可能完整跑完）；--page-timeout-ms 8000 限定 /slow 挂起
    S4_KILL_COUNT=0
    # 首跑退出码经专属全局 S4_RC 回传、函数退出后赋回 RC（不再在函数体
    # 内直接写共享全局 RC——隔离既有干扰路径，退出码归因唯一化）
    s4_launch() {
      ( cd "${REPO_ROOT}" && exec "${PYTHON}" -B "${SU_CLI}" \
          --config "${S4}/config.json" --out "${S4}/out" --system-id su-fixture \
          --skip-llm-phase --delay-ms 3000 --max-pages 8 --page-timeout-ms 8000 ) \
        >>"${S4}/run1.log" 2>&1 &
      S4P=$!
      CHILD_PIDS+=("${S4P}")
      sleep "${S4_SIGINT_AT}"
      kill -INT "${S4P}" 2>/dev/null || true
      S4_KILL_COUNT=1
      # 兜底重试：SIGINT 若恰落在 /slow goto 挂起窗口（信号挂起到 goto
      # 最多 8s 超时返回），10s 内未退出则再补一枚；最多 3 枚（覆盖
      # 连续两页慢页串挂的最坏情形）
      for _sigint_try in 1 2 3; do
        for _ in 1 2 3 4 5 6 7 8 9 10; do
          kill -0 "${S4P}" 2>/dev/null || break
          sleep 1
        done
        kill -0 "${S4P}" 2>/dev/null || break
        kill -INT "${S4P}" 2>/dev/null || true
        S4_KILL_COUNT=$((S4_KILL_COUNT + 1))
      done
      wait "${S4P}"; S4_RC=$?
    }
    s4_launch
    RC="${S4_RC:-1}"
    # 自动补刀（理论不可达分支的诚实兜底）：首跑若未被中断（SIGINT 全被
    # 挂起窗口吞掉——crawler 45s 看门狗 + 发送侧 3 枚补刀双保险下理论不可
    # 达），run 呈 completed/budget_exhausted 而非 interrupted。补刀必须
    # --fresh 重置：页预算是进程级计数、不跨 run 归还，上一轮 consume 掉的
    # 页不会复原，resume 轮极易"秒耗尽"再次错过信号（2026-09-28 实测教训）。
    # fresh 归档旧 state/ 后全新采集（delay 3s 慢节奏），信号窗口 = 全量
    # 采集期；代价是 run_meta 变 3 行 + 页数断言以 fresh 新库为准——全部
    # 由下方断言按实际值如实核对，绝不为凑数放宽
    S4_FIRST_STATUS="$(sqlite3 "${S4}/out/su-fixture/state/understanding.sqlite" \
      "SELECT status FROM run_meta ORDER BY started_at DESC LIMIT 1" 2>/dev/null)"
    S4_FRESH_REPLAY=0
    if [ "${RC}" -ne 130 ] && [ "${S4_FIRST_STATUS}" != "interrupted" ]; then
      S4_FRESH_REPLAY=1
      log "场景[4] 首跑未被中断（SIGINT 落入 CDP 挂起窗口被吞）——--fresh 补刀重跑"
      # --max-pages 8：fresh 新库全量 BFS 共 19 页，delay 3s 下不设预算整轮
      # 约 60s+（含 /slow 超时），叠加每 10s 补刀节奏极易撞 run_su 之外的
      # 300s 总预算；8 页预算使补刀轮必然在 ~35s 内"预算耗尽"收口，
      # SIGINT 窗口覆盖全部慢采期（2026-09-28 实测教训）
      ( cd "${REPO_ROOT}" && exec "${PYTHON}" -B "${SU_CLI}" \
          --config "${S4}/config.json" --out "${S4}/out" --system-id su-fixture \
          --skip-llm-phase --delay-ms 3000 --max-pages 8 --page-timeout-ms 8000 --fresh ) \
        >>"${S4}/run1.log" 2>&1 &
      S4P=$!
      CHILD_PIDS+=("${S4P}")
      # 补刀轮：fresh 新库全量慢采（delay 3s），每 10s 一枚 SIGINT 的密度
      # 下，中断必然命中非挂起间隙；--page-timeout-ms 8000 限定 /slow 挂起
      sleep "${S4_SIGINT_AT}"
      kill -INT "${S4P}" 2>/dev/null || true
      for _sigint_try in 1 2 3 4 5 6; do
        for _ in 1 2 3 4 5 6 7 8 9 10; do
          kill -0 "${S4P}" 2>/dev/null || break
          sleep 1
        done
        kill -0 "${S4P}" 2>/dev/null || break
        kill -INT "${S4P}" 2>/dev/null || true
      done
      wait "${S4P}"; S4_RC=$?
    fi
    # fresh 补刀路径同样经 S4_RC 回传（与 s4_launch 同口径）；若未走补刀
    # 分支，S4_RC 仍是 s4_launch 的回传值，赋回 RC 为幂等操作
    RC="${S4_RC:-1}"
    [ "${RC}" -eq 130 ] || S4_ERR="SIGINT 首跑退出码 ${RC}（期望 130）"
    S4DB="${S4}/out/su-fixture/state/understanding.sqlite"
    interrupted="$(sqlite3 "${S4DB}" \
      "SELECT status FROM run_meta ORDER BY started_at DESC LIMIT 1" 2>/dev/null)"
    [ "${interrupted}" = "interrupted" ] || S4_ERR="${S4_ERR} run 状态=${interrupted}（期望 interrupted）"
    P1="$(sqlite3 "${S4DB}" "SELECT COUNT(*) FROM pages" 2>/dev/null)"
    if [ -z "${P1}" ] || [ "${P1}" -lt 1 ] 2>/dev/null; then
      S4_ERR="${S4_ERR} 首跑 pages=${P1:-?}（SIGINT 前应有进度落库）"
    fi
    # --resume 续跑（同 out：interrupted 复用既有 run，页数跨阶段累积）。
    # 预算必须是 CLI 默认 30（= RunBudget.max_pages）：crawler 的页预算是
    # 进程级计数、不跨 run 持久——上一轮（首跑/补刀轮）consume_page 掉的 8
    # 页不会"归还"，若这里传 --max-pages 8，本轮 consume_page 立即失败、
    # frontier 永远采不完（RC=0 但 done 页不增长）。默认 30 ≥ 8+20（全站页）
    # 保证 resume 完整收口且不会二次截断
    run_su "${S4}" --config "${S4}/config.json" --out "${S4}/out" \
      --system-id su-fixture --skip-llm-phase --delay-ms 100 --resume
    [ "${RC}" -eq 0 ] || S4_ERR="${S4_ERR} resume 退出码 ${RC}（期望 0）"
    D2="$(sqlite3 "${S4DB}" "SELECT COUNT(*) FROM pages WHERE status='done'" 2>/dev/null)"
    if [ -z "${D2}" ] || [ "${D2}" -lt 5 ] 2>/dev/null; then
      S4_ERR="${S4_ERR} resume 后 done 页 ${D2:-?}<5（全站 20 页预算充足应基本采完）"
    fi
    # 进度单调：续跑后 pages 总数 ≥ 首跑（首跑数据未被清库 = resume 复用证据）
    P2="$(sqlite3 "${S4DB}" "SELECT COUNT(*) FROM pages" 2>/dev/null)"
    if [ -n "${P1}" ] && [ -n "${P2}" ] && [ "${P2}" -lt "${P1}" ] 2>/dev/null; then
      S4_ERR="${S4_ERR} resume 后 pages ${P2}<首跑${P1}"
    fi
    # run_meta 行数（fresh 前口径）：resume 复用同一 run 行
    # （interrupted→running→completed 改写）是 acquire_lock 的设计语义
    # （"interrupted + resume=True → 改写锁+心跳"，非新建行）——
    # 常规路径 == 1 正是"复用而非新建"的库内证据；fresh 补刀路径 == 2
    # （补刀 completed + 终跑 interrupted→resume 复用行）
    RUNS="$(sqlite3 "${S4DB}" "SELECT COUNT(*) FROM run_meta" 2>/dev/null)"
    S4_RUNS_WANT=1
    [ "${S4_FRESH_REPLAY}" = "1" ] && S4_RUNS_WANT=2
    [ "${RUNS}" = "${S4_RUNS_WANT}" ] || S4_ERR="${S4_ERR} resume 后 run_meta 行数=${RUNS}（期望 ${S4_RUNS_WANT}：resume 复用 interrupted 行$([ "${S4_FRESH_REPLAY}" = "1" ] && echo '+补刀 completed')）"
    # fresh 前把最新 run 置回 interrupted（原因：resume 成功时 interrupted
    # 已被复用成 completed——fresh 无 interrupted 可归档、不新建行，
    # "缺归档目录"断言将级联误报。置回模拟"首跑被中断后直接 --fresh"的
    # PRD 主路径——CLI 无"resume 后回滚 interrupted"的公开入口，脚本层
    # 状态置回属测试设施行为，不改变被测代码语义；exit_reason 同步置
    # sigint 与真实中断收口形态一致）。
    sqlite3 "${S4DB}" \
      "UPDATE run_meta SET status='interrupted', exit_reason='sigint' WHERE rowid=(SELECT MAX(rowid) FROM run_meta)" \
      2>/dev/null
    # --fresh：interrupted 状态归档 state.archive.<ts>/ 后全新重跑
    # （PRD REQ-SU-019 AC2；归档判定 2026-09-29 收敛至
    # StateStore.acquire_lock(resume=False) 的 interrupted 分支——本断言
    # 为该修复的回归锁）。预算同 resume 用 CLI 默认 30（fresh 库已清空，
    # consume_page 从零起算，30 ≥ 全站页 → 必然完整收口不二次截断）
    run_su "${S4}" --config "${S4}/config.json" --out "${S4}/out" \
      --system-id su-fixture --skip-llm-phase --delay-ms 100 --fresh
    [ "${RC}" -eq 0 ] || S4_ERR="${S4_ERR} fresh 退出码 ${RC}（期望 0）"
    # 归档目录名口径（以 state_store.archive_and_reset 实现为准）：
    # state/ 整体改名 state.archive.<ts>/ 后重建空 state/。fresh 补刀路径
    # 已先行归档过一次（≥1 个归档目录即证明 rename 语义成立）
    if [ "$(ls -d "${S4}/out/su-fixture/"state.archive.* 2>/dev/null | wc -l | tr -d ' ')" -lt 1 ]; then
      S4_ERR="${S4_ERR} 缺 <system_id>/state.archive.<ts> 归档目录"
    fi
    # fresh 后新库从零起算：state/ 整目录 rename 走 + 重建空 state/ 只含
    # fresh 新建行 → run_meta == 1（置回 interrupted 的复用行随归档移走）。
    # 补刀路径同理（fresh 前的行全部随 rename 归档）。"resume 复用同一
    # run 行（interrupted→running→completed 改写）"的证据由上方
    # pages 单调 + done≥5 + run_meta=1（resume 后）组合锁定
    RUNS="$(sqlite3 "${S4DB}" "SELECT COUNT(*) FROM run_meta" 2>/dev/null)"
    [ "${RUNS}" = "1" ] || S4_ERR="${S4_ERR} fresh 后新库 run_meta=${RUNS}（期望 1，归档生效从零起算）"
    # write-counter：SIGINT/续跑全程站点零写
    WC4="$(site_write_counter "${SITE_PORT}")"
    [ "${WC4}" = "0" ] || S4_ERR="${S4_ERR} write-counter=${WC4}（期望 0）"
    log "场景[4] 计数：首跑 pages=${P1:-?}；resume 后 done=${D2:-?} total=${P2:-?}；run_meta=${RUNS:-?}"
    if [ -z "${S4_ERR}" ]; then pass "[4]断点续跑"; else fail "[4]断点续跑" "${S4_ERR}"; fi
    kill -TERM "${SITE_PID}" 2>/dev/null || true
  fi

  # ---- 场景 [5]：页面预算 ---------------------------------------------------
  log "===== 场景 [5] 预算 --max-pages 3 ====="
  S5="${WORK_ROOT}/s5"; mkdir -p "${S5}"
  S5_ERR=""
  if ! scenario_selected 5; then
    skip "[5]预算" "--only 未选择"
  elif start_scene_site s5; then
    make_config "${S5}/config.json" "${SITE_PORT}"
    # 首跑预算 3 + --max-depth 1：深度 1 截断把可发现广度压到入口+11 个
    # 导航页（12 > 3），预算必然在第 4 次消费时截断——frontier（pages 表
    # pending 行）>0。不加 --max-depth 时 BFS 会先把 /orders 的 hash 子页、
    # /products 的 query 子页等深度 2 节点逐个入队，3 页预算在队列消费到
    # pending 行之前就已耗尽（深度 2 节点只入内存队列、budget break 前
    # 永不落库），pending 恒为 0——2026-09-28 e2e 实测教训
    run_su "${S5}" --config "${S5}/config.json" --out "${S5}/out" \
      --max-depth 1 \
      --system-id su-fixture --skip-llm-phase --delay-ms 100 \
      --max-pages 3 --page-timeout-ms 8000
    [ "${RC}" -eq 0 ] || S5_ERR="退出码 ${RC}（期望 0）"
    S5DB="${S5}/out/su-fixture/state/understanding.sqlite"
    # 站点广度远超 3 页（dashboard 扇出 ≥3 链接）：预算必然截断 → 恰好
    # 消费 3 页（done/timeout 或探索后落库），frontier = pending 行 >0
    DONE5="$(sqlite3 "${S5DB}" "SELECT COUNT(*) FROM pages WHERE status IN ('done','timeout')" 2>/dev/null)"
    [ "${DONE5:-0}" = "3" ] || S5_ERR="采集页数=${DONE5:-?}（期望恰好 3）"
    FR5="$(sqlite3 "${S5DB}" "SELECT COUNT(*) FROM pages WHERE status='pending'" 2>/dev/null)"
    if [ -z "${FR5}" ] || [ "${FR5}" -lt 1 ] 2>/dev/null; then
      S5_ERR="${S5_ERR} 预算截断后 frontier pending=${FR5:-?}（期望 >0）"
    fi
    # 第 10 节预算声明：frontier 节点清单必须出现在文档 c) 小节
    MD5="${S5}/out/su-fixture/UNDERSTANDING.md"
    if [ -z "${S5_ERR}" ]; then
      if [ ! -f "${MD5}" ]; then
        S5_ERR="UNDERSTANDING.md 缺失"
      else
        grep -qF "### c) 预算耗尽时未探索的 frontier 节点" "${MD5}" \
          || S5_ERR="${S5_ERR} 第 10 节缺 frontier 小节标题"
        # frontier 小节必须列出具体节点（URL 行）而非"无 frontier"占位
        if grep -A20 "### c) 预算耗尽时未探索的 frontier 节点" "${MD5}" | grep -q "无 frontier"; then
          S5_ERR="${S5_ERR} 第 10 节 frontier 小节为\"无 frontier\"（预算截断未如实声明）"
        fi
      fi
    fi
    # "确有未采页"（frontier 语义的库外证据）：--max-depth 1 下深度 1
    # 全集（入口 + 11 全局导航 + dashboard 扇出 /slow，共 13 页）全部
    # 在首页采集时入队落库（pending 行），3 页预算截断后其中 10 页
    # status='pending' 从未探索——"未采"必须按 status 判定（url_key
    # 只能证明"已发现"；2026-09-29 取证：按 url_key 对全量 13 页求差
    # 恒为 0，因 pending 行同样在 pages 表内）。pending ≥ 10 即
    # "预算截断漏采 ≥10 页"的库外证据
    UNSEEN="$(sqlite3 "${S5DB}" \
      "SELECT COUNT(*) FROM pages WHERE status='pending'" 2>/dev/null)"
    if [ -z "${UNSEEN}" ] || [ "${UNSEEN}" -lt 10 ] 2>/dev/null; then
      S5_ERR="${S5_ERR} 预算截断后未采（pending）页数=${UNSEEN:-?}（期望 ≥10）"
    fi
    # summary.json 预算段与 stats 同源可核
    MP="$(json_get "${S5}/out/su-fixture/summary.json" "d['budget']['max_pages']" 2>/dev/null)"
    [ "${MP}" = "3" ] || S5_ERR="summary.json budget.max_pages=${MP:-?}（期望 3）"
    WC5="$(site_write_counter "${SITE_PORT}")"
    [ "${WC5}" = "0" ] || S5_ERR="${S5_ERR} write-counter=${WC5}（期望 0）"
    if [ -z "${S5_ERR}" ]; then pass "[5]预算"; else fail "[5]预算" "${S5_ERR}"; fi
    kill -TERM "${SITE_PID}" 2>/dev/null || true
  fi

  # ---- 场景 [6]：凭据错误 ---------------------------------------------------
  log "===== 场景 [6] 凭据错误 ====="
  S6="${WORK_ROOT}/s6"; mkdir -p "${S6}"
  S6_ERR=""
  if ! scenario_selected 6; then
    skip "[6]凭据错误" "--only 未选择"
  elif start_scene_site s6; then
    make_config "${S6}/config.json" "${SITE_PORT}" "${SU_PASSWORD_WRONG}"
    run_su "${S6}" --config "${S6}/config.json" --out "${S6}/out" \
      --system-id su-fixture --skip-llm-phase --delay-ms 100 --max-pages 15
    [ "${RC}" -eq 4 ] || S6_ERR="退出码 ${RC}（期望 4）"
    if [ -f "${S6}/out/su-fixture/UNDERSTANDING.md" ]; then
      S6_ERR="${S6_ERR} 登录失败却产出 UNDERSTANDING.md（禁半成品文档）"
    fi
    if [ -z "${S6_ERR}" ]; then pass "[6]凭据错误"; else fail "[6]凭据错误" "${S6_ERR}"; fi
    kill -TERM "${SITE_PID}" 2>/dev/null || true
  fi

  # ---- 场景 [7]：render-only + 降级声明 --------------------------------------
  # 7a：redis/db 透镜"未配置"显式声明（config 无对应段——场景[2]产物即素材）
  # 7b：--render-only 合法 findings 注入 → 第 5 节含该 claim（exit 0）
  # 7c：非法 findings（缺 confidence 键）→ exit 2 整批拒绝（负向断言）
  log "===== 场景 [7] render-only 与降级声明 ====="
  S7="${WORK_ROOT}/s7"; mkdir -p "${S7}"
  S7_ERR=""
  S7_SELECTED=1
  if ! scenario_selected 7; then
    S7_SELECTED=0
    skip "[7]render-only" "--only 未选择"
  fi
  MD7A="${S2_DIR:-/nonexistent}/UNDERSTANDING.md"
  if [ "${S7_SELECTED}" = "1" ] && [ -f "${MD7A}" ]; then
    grep -qF "## 6. 缓存与中间件" "${MD7A}" || S7_ERR="缺第 6 节标题"
    if ! grep -A8 "## 6. 缓存与中间件" "${MD7A}" | grep -q "未采集"; then
      S7_ERR="${S7_ERR} 第 6 节缺未采集显式声明"
    fi
  elif [ "${S7_SELECTED}" = "1" ]; then
    S7_ERR="场景[2]产物缺失（第 6 节素材不可用）"
  fi
  # 7b：--render-only 合法 findings 注入 → 文档含该 claim
  # 目录布局以 CLI 实现为准：_run_render_only 前置校验读
  # <out>/<system_id>/state/understanding.sqlite 与 <out>/<system_id>/
  # understanding.json；渲染产物经 DocumentRenderer 同样落 <out>/<system_id>/
  S7RO="${S7}/ro"; mkdir -p "${S7RO}/su-fixture-ro/state"
  if [ "${S7_SELECTED}" = "1" ] && [ -z "${S7_ERR}" ]; then
    "${PYTHON}" -B "${STATE_BUILDER_PY}" \
      "${S7RO}/su-fixture-ro/state/understanding.sqlite" su-fixture-ro completed \
      >"${S7}/builder.out" 2>&1 \
      || S7_ERR="种子库构建失败（见 ${S7}/builder.out）"
  fi
  if [ "${S7_SELECTED}" = "1" ] && [ -z "${S7_ERR}" ]; then
    # 最小 understanding.json：findings 引用种子库真实行 id（pages:1 / db_tables:1）
    "${PYTHON}" - "${S7RO}/su-fixture-ro/understanding.json" <<'PYEOF'
import json, sys
payload = {
    "findings": [
        {
            "claim": "订单接口字段 order_id 与数据表 orders 主键构成读写映射（fixture 注入结论）",
            "kind": "mapping",
            "confidence": "high",
            "evidence_refs": ["pages:1", "db_tables:1"],
        }
    ]
}
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(payload, fh, ensure_ascii=False, indent=2)
PYEOF
    run_su "${S7}" --out "${S7RO}" --system-id su-fixture-ro --render-only
    [ "${RC}" -eq 0 ] || S7_ERR="render-only 退出码 ${RC}（期望 0）"
    # 产物路径：DocumentRenderer._root = <out_dir>/<system_id>（render-only
    # 壳配置 out_dir=--out 值）→ UNDERSTANDING.md 落 <out>/<system_id>/；
    # findings 渲染进第 5 节（UI↔数据映射）
    if [ -z "${S7_ERR}" ]; then
      MD7B="${S7RO}/su-fixture-ro/UNDERSTANDING.md"
      if [ ! -f "${MD7B}" ]; then
        S7_ERR="render-only 未产出 UNDERSTANDING.md"
      else
        grep -qF "订单接口字段 order_id" "${MD7B}" \
          || S7_ERR="注入的 findings claim 未出现在重渲染文档"
        grep -qF "## 5. UI ↔ 数据映射" "${MD7B}" \
          || S7_ERR="${S7_ERR} 第 5 节标题缺失（claim 应渲染于该节）"
      fi
    fi
  fi
  # 7c：非法 findings（缺 confidence 键）→ exit 2 整批拒绝（负向断言）
  #
  # 布局注意：非法版必须建在独立根目录（out=SUITE 根 ${S7BAD} + system_id 同名），
  # 绝不复用 7b 的种子库——acquire_lock 对 completed 历史 run 会新建 run 行
  # （run_meta 行数变化），复用会污染 7b"文档含 claim"断言的素材完整性。
  if [ "${S7_SELECTED}" = "1" ] && [ -z "${S7_ERR}" ]; then
    S7BAD="${S7}/ro-bad"; mkdir -p "${S7BAD}/su-fixture-ro/state"
    "${PYTHON}" -B "${STATE_BUILDER_PY}" \
      "${S7BAD}/su-fixture-ro/state/understanding.sqlite" su-fixture-ro completed \
      >>"${S7}/builder.out" 2>&1 \
      || S7_ERR="非法场景种子库构建失败（见 ${S7}/builder.out）"
  fi
  if [ "${S7_SELECTED}" = "1" ] && [ -z "${S7_ERR}" ]; then
    # 非法版 understanding.json：缺失 confidence 必填键
    #（validate_findings_schema 要求 confidence ∈ {high,medium,low}）
    "${PYTHON}" - "${S7BAD}/su-fixture-ro/understanding.json" <<'PYEOF'
import json, sys
payload = {
    "findings": [
        {
            "claim": "缺 confidence 必填键应整批拒绝（负向断言素材）",
            "kind": "mapping",
            # confidence 键缺失——validate_findings_schema 整批拒绝
            "evidence_refs": ["pages:1"],
        }
    ]
}
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(payload, fh, ensure_ascii=False, indent=2)
PYEOF
    run_su "${S7}" --out "${S7BAD}" --system-id su-fixture-ro --render-only
    [ "${RC}" -eq 2 ] || S7_ERR="非法 findings 退出码 ${RC}（期望 2）"
    # 7b 素材完整性：非法场景用独立目录，7b 文档 claim 必须原样在位
    if [ -z "${S7_ERR}" ] \
       && ! grep -qF "订单接口字段 order_id" "${S7RO}/su-fixture-ro/UNDERSTANDING.md" 2>/dev/null; then
      S7_ERR="非法场景污染了 7b 产物（claim 丢失）"
    fi
  fi
  if [ "${S7_SELECTED}" = "1" ] && [ -z "${S7_ERR}" ]; then pass "[7]render-only"
  elif [ "${S7_SELECTED}" = "1" ]; then fail "[7]render-only" "${S7_ERR}"; fi
fi

# ---------------------------------------------------------------------------
# 场景 [8]：DB/Redis 容器场景（env 探测缺失显式 SKIP，不假通过也不 FAIL）
# ---------------------------------------------------------------------------
log "===== 场景 [8] DB/Redis 容器场景 ====="
if ! scenario_selected 8; then
  skip "[8]DB-Redis容器" "--only 未选择"
elif [ -z "${SU_TEST_MYSQL_DSN:-}" ] || [ -z "${SU_TEST_REDIS_URL:-}" ]; then
  skip "[8]DB-Redis容器" "未注入 SU_TEST_MYSQL_DSN / SU_TEST_REDIS_URL（缺省显式 SKIP）"
  log "如需本场景：起 MySQL/Redis 容器后 export SU_TEST_MYSQL_DSN='mysql://user:pass@host:3306/db' SU_TEST_REDIS_URL='redis://:pass@host:6379/0' 重跑"
elif [ "${DEPS_OK}" -ne 1 ]; then
  skip "[8]DB-Redis容器" "playwright/chromium 缺失（见场景[0]）"
else
  S8="${WORK_ROOT}/s8"; mkdir -p "${S8}"
  S8_ERR=""
  if start_scene_site s8; then
    # 用 DSN 注入 database/redis 段（config 增加两段后走完整四透镜）
    "${PYTHON}" - "${S8}/config.json" "${SITE_PORT}" "${SU_TEST_MYSQL_DSN}" "${SU_TEST_REDIS_URL}" <<PYEOF
import json, sys
from urllib.parse import urlsplit


def parse_db(dsn: str) -> dict:
    """DSN → config.database 段（mysql/postgres 双形态）。"""
    u = urlsplit(dsn)
    engine = "mysql" if u.scheme.startswith("mysql") else "postgresql"
    return {
        "engine": engine,
        "host": u.hostname,
        "port": u.port or (3306 if engine == "mysql" else 5432),
        "database": (u.path or "/").lstrip("/"),
        "username": u.username or "",
        "password": u.password or "",
    }


u = urlsplit("${SU_TEST_REDIS_URL}")
cfg = {
    "system": {
        "base_url": "http://127.0.0.1:${SITE_PORT}",
        "login_url": "/login",
        "username": "${SU_USER}",
        "password": "${SU_PASSWORD}",
        "success_hint": 'nav a[href="/settings"]',
    },
    "database": parse_db("${SU_TEST_MYSQL_DSN}"),
    "redis": {"host": u.hostname, "port": u.port or 6379,
              "password": u.password or "", "db": int((u.path or "/0").lstrip("/") or 0)},
}
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, ensure_ascii=False, indent=2)
PYEOF
    chmod 600 "${S8}/config.json"
    run_su "${S8}" --config "${S8}/config.json" --out "${S8}/out" \
      --system-id su-fixture --skip-llm-phase --delay-ms 100 --max-pages 15 \
      --page-timeout-ms 15000
    [ "${RC}" -eq 0 ] || S8_ERR="退出码 ${RC}（期望 0）"
    if [ -z "${S8_ERR}" ]; then
      LENSES="$(json_get "${S8}/out/su-fixture/summary.json" \
        "'/'.join(str(d['lenses'][k]) for k in ('db','redis'))" 2>/dev/null)"
      case "${LENSES}" in
        *failed*) S8_ERR="DB/Redis 透镜 failed：${LENSES}" ;;
        collected*) : ;;  # db/redis 两位非 failed 即通过
      esac
    fi
    WC8="$(site_write_counter "${SITE_PORT}")"
    [ "${WC8}" = "0" ] || S8_ERR="${S8_ERR} write-counter=${WC8}（期望 0）"
    if [ -z "${S8_ERR}" ]; then pass "[8]DB-Redis容器"; else fail "[8]DB-Redis容器" "${S8_ERR}"; fi
    kill -TERM "${SITE_PID}" 2>/dev/null || true
  fi
fi

# ---------------------------------------------------------------------------
# 场景 [9]：SFD S-1 全链路（复用场景[2]三件套；DEPS_OK 分支内——浏览器场景）
# ---------------------------------------------------------------------------
if [ "${DEPS_OK}" -eq 1 ]; then
  log "===== 场景 [9] SFD 全链路（detailed-doc → 五草稿 → assemble） ====="
  S9="${WORK_ROOT}/s9"; mkdir -p "${S9}"
  S9_ERR=""
  S9_OUT=""   # 详说阶段 --out 根目录（场景[2]或兜底跑的输出根）
  if ! scenario_selected 9; then
    skip "[9]SFD全链路" "--only 未选择"
  else
    # 三件套素材兜底（与场景[3]同款，2026-09-30）：--only 9 单独执行或
    # [2] 未跑成功时，独立起重跑一次采集（delay 100ms 快采）供素材，
    # 绝不因 --only 削弱本场景真实性
    if [ ! -f "${S2_DIR:-/nonexistent}/UNDERSTANDING.md" ]; then
      log "场景[2]产物缺失——场景[9]独立重跑兜底"
      if start_scene_site s9; then
        make_config "${S9}/config.json" "${SITE_PORT}"
        run_su "${S9}" --config "${S9}/config.json" --out "${S9}/out" \
          --system-id su-fixture --skip-llm-phase --delay-ms 100 \
          --page-timeout-ms 15000 || true
        kill -TERM "${SITE_PID}" 2>/dev/null || true
        S9_OUT="${S9}/out"
        S9_DIR="${S9}/out/su-fixture"
      else
        S9_ERR="兜底站点启动失败"
      fi
    else
      S9_OUT="${S2}/out"
      S9_DIR="${S2_DIR}"
    fi
  fi
  if [ -n "${S9_OUT}" ] && [ ! -f "${S9_DIR}/UNDERSTANDING.md" ]; then
    S9_ERR="三件套素材缺失（UNDERSTANDING.md 不存在，采集兜底失败）"
    S9_OUT=""
  fi
  if [ -n "${S9_OUT}" ]; then
    # 9a：findings 注入（直插状态库 + 磁盘 findings 段）→ render-only 收口
    S9SID="su-fixture"   # 直接在采集产物目录上追加详说阶段（真实用户口径）
    "${PYTHON}" -B - "${S9_DIR}" <<'PYEOF' >"${S9}/inject.log" 2>&1
"""向场景[2]状态库直插 1 条合法 findings（e2e 测试设施注入）。"""
import json
import sqlite3
import sys
import time
from pathlib import Path

sys_root = Path(sys.argv[1])
db = sys_root / "state" / "understanding.sqlite"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row
try:
    # 引用素材自适应（2026-09-30）：真实采集库 url_key 含协议主机端口
    # （动态端口不可预知）——按后缀 '/orders' 匹配；api_observations 主键
    # 列名为 endpoint_id（state_store schema）；db_tables 在未配置
    # database 的降级口径下为空，绝不引用
    pid_row = conn.execute(
        "SELECT page_id FROM pages"
        " WHERE url_key LIKE '%/orders' AND status='done'"
        " ORDER BY page_id LIMIT 1").fetchone()
    if pid_row is None:
        raise SystemExit("pages 缺 /orders done 行（采集素材异常）")
    refs = ["pages:{0}".format(pid_row["page_id"])]
    claim = "订单列表页展示订单明细（e2e S-1 注入结论）"
    ao = conn.execute(
        "SELECT endpoint_id FROM api_observations"
        " WHERE url_path LIKE '%/api/orders%' AND method='GET'"
        " ORDER BY endpoint_id LIMIT 1").fetchone()
    if ao is not None:
        refs.append("api_observations:{0}".format(ao["endpoint_id"]))
        claim = "订单列表页经 GET /api/orders 读取订单数据（e2e S-1 注入结论）"
    finding = {
        "claim": claim,
        "kind": "mapping",
        "confidence": "high",
        "evidence_refs": refs,
    }
    conn.execute(
        "INSERT INTO findings(claim, confidence, evidence_refs, status,"
        " kind, created_at) VALUES(?,?,?,?,?,?)",
        (finding["claim"], finding["confidence"],
         json.dumps(finding["evidence_refs"], ensure_ascii=False),
         "proposed", finding["kind"], time.time()))
    conn.commit()
finally:
    conn.close()
u_path = sys_root / "understanding.json"
u = json.loads(u_path.read_text("utf-8"))
u["findings"] = [finding]
u_path.write_text(json.dumps(u, ensure_ascii=False, indent=2), "utf-8")
print("injected refs: {0}".format(refs))
PYEOF
    [ $? -eq 0 ] || S9_ERR="findings 注入失败（见 ${S9}/inject.log）"
    if [ -z "${S9_ERR}" ]; then
      run_su "${S9}" --out "${S9_OUT}" --system-id "${S9SID}" --render-only
      [ "${RC}" -eq 0 ] || S9_ERR="render-only 收口退出码 ${RC}（期望 0）"
    fi
    # 9b：--detailed-doc → 素材包五件 + manifest 锚点 + 大纲 8 节 + 派发指引
    # REQ-SFD-012 AC2 sha 守卫基线：详说阶段绝不改动 SU 既有产物，
    # 进入 --detailed-doc 前对 SU 产物快照 sha256，装配收口后逐字节比对
    if [ -z "${S9_ERR}" ]; then
      SU_SHA_BEFORE="$(cd "${S9_DIR}" && shasum -a 256 UNDERSTANDING.md understanding.json \
        summary.json evidence/evidence-index.json 2>/dev/null | shasum -a 256 | cut -d' ' -f1)"
    fi
    if [ -z "${S9_ERR}" ]; then
      run_su "${S9}" --out "${S9_OUT}" --system-id "${S9SID}" --detailed-doc
      [ "${RC}" -eq 0 ] || S9_ERR="detailed-doc 退出码 ${RC}（期望 0）"
    fi
    if [ -z "${S9_ERR}" ]; then
      grep -q "\[SFD\] 专家派发" "${S9}/run.log" \
        || S9_ERR="派发指引缺失（见 ${S9}/run.log）"
      for pkg in architect product dev ui qa; do
        [ -f "${S9_DIR}/detailed/inputs/${pkg}.json" ] \
          || S9_ERR="${S9_ERR} 素材包缺失 ${pkg}.json"
      done
      head -5 "${S9_DIR}/SYSTEM_FUNCTION_DOC.md" | grep -q '^<!-- status: outline -->$' \
        || S9_ERR="${S9_ERR} 大纲缺 status: outline 标记"
      [ "$(grep -c '^## [1-8]\. ' "${S9_DIR}/SYSTEM_FUNCTION_DOC.md")" -eq 8 ] \
        || S9_ERR="${S9_ERR} 大纲节数 ≠ 8"
    fi
    # manifest 锚点字段（P0-1b）+ 禁入 key 物理不进包（ADR-2）
    if [ -z "${S9_ERR}" ]; then
      "${PYTHON}" -B - "${S9_DIR}" >"${S9}/manifest.log" 2>&1 <<'PYEOF' || S9_ERR="manifest/白名单核验失败（见 ${S9}/manifest.log）"
"""五包 manifest 锚点 + redis_keys/findings_prompt/lenses 物理不进包断言。"""
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
blob_all = []
for role in ("architect", "product", "dev", "ui", "qa"):
    pkg = json.loads((root / "detailed/inputs" / f"{role}.json").read_text("utf-8"))
    mf = pkg["manifest"]
    for key in ("run_id", "evidence_index_sha256", "started_at", "sources"):
        assert key in mf, f"{role}.json manifest 缺 {key}"
    assert set(pkg["data"]), "data 段为空"
    blob_all.append(json.dumps(pkg, ensure_ascii=False))
dev = json.loads((root / "detailed/inputs" / "dev.json").read_text("utf-8"))
assert dev["manifest"].get("db_samples_masked_by") == "DataMasker"
blob = "\n".join(blob_all)
for banned in ("findings_prompt", '"lenses"', "redis_keys"):
    assert banned not in blob, f"禁入 key {banned} 出现在素材包"
print("manifest 锚点与白名单核验通过")
PYEOF
    fi
    # 9c：五份合规草稿 → --assemble 终稿 8 节 + report
    if [ -z "${S9_ERR}" ] && ! sfd_place_drafts "${S9_DIR}" all; then
      S9_ERR="合规草稿放置失败"
    fi
    if [ -z "${S9_ERR}" ]; then
      run_su "${S9}" --out "${S9_OUT}" --system-id "${S9SID}" --assemble
      [ "${RC}" -eq 0 ] || S9_ERR="assemble 退出码 ${RC}（期望 0，见 run.log）"
    fi
    if [ -z "${S9_ERR}" ]; then
      head -5 "${S9_DIR}/SYSTEM_FUNCTION_DOC.md" | grep -q '^<!-- status: final -->$' \
        || S9_ERR="终稿缺 status: final 标记"
      [ "$(grep -c '^## [1-8]\. ' "${S9_DIR}/SYSTEM_FUNCTION_DOC.md")" -eq 8 ] \
        || S9_ERR="${S9_ERR} 终稿节数 ≠ 8"
      REP9="$(json_get "${S9_DIR}/detailed/assembly-report.json" \
        "'{0}'.format([d['ref_invalid'], d['credential_scan']['status'], len(d['sections'])])" 2>/dev/null)"
      [ "${REP9}" = "[0, 'clean', 8]" ] \
        || S9_ERR="${S9_ERR} report 断言失败：${REP9}"
      # 合法引用必须进清单——草稿五份各含 E-n，ref_total 至少覆盖草稿引用数
      # （引用体系全链路参与：E-n 识别→清单→统计）
      REFT9="$(json_get "${S9_DIR}/detailed/assembly-report.json" \
        "str(d['ref_total'])" 2>/dev/null)"
      [ -n "${REFT9}" ] && [ "${REFT9}" -ge 5 ] 2>/dev/null \
        || S9_ERR="${S9_ERR} report ref_total=${REFT9}（期望 ≥5）"
      # S-1 语义：e2e 场景[3] 同款红线——详说产物目录零凭据明文
      if grep -rqF "${SU_PASSWORD}" "${S9_DIR}/detailed" "${S9_DIR}/SYSTEM_FUNCTION_DOC.md" 2>/dev/null; then
        S9_ERR="${S9_ERR} 详说产物泄露 fixture 凭据明文"
      fi
      # REQ-SFD-012 AC2 sha 守卫：详说全链路（detailed-doc+assemble）对 SU
      # 既有产物（UNDERSTANDING.md/understanding.json/summary/evidence-index）
      # 必须零改动——聚合 sha 与装配前基线逐字节一致
      SU_SHA_AFTER="$(cd "${S9_DIR}" && shasum -a 256 UNDERSTANDING.md understanding.json \
        summary.json evidence/evidence-index.json 2>/dev/null | shasum -a 256 | cut -d' ' -f1)"
      [ -n "${SU_SHA_BEFORE}" ] && [ "${SU_SHA_BEFORE}" = "${SU_SHA_AFTER}" ] \
        || S9_ERR="${S9_ERR} SU 既有产物被详说阶段改动（sha 基线失配）"
    fi
    if [ -z "${S9_ERR}" ]; then pass "[9]SFD全链路"; else fail "[9]SFD全链路" "${S9_ERR}"; fi
  fi
fi

# ---------------------------------------------------------------------------
# 场景 [10]-[13]：SFD 降级/凭据收口/CLI 违例/幂等（builder 链路，零浏览器，
# playwright 缺失照常执行——详说阶段零网络零凭据面，ARCH-SFD-001 §10.3）
# ---------------------------------------------------------------------------

# sfd_builder_chain <scen_dir> <sid>：builder 种子 → render-only → detailed-doc
# 三步前置（[10]-[13] 共同基座）。成功回显 sys_root；任一步失败回显空串。
# 全局 RC 反映最后一步 CLI 退出码。
# 实现约束（2026-09-30 实测教训）：禁用 run_su——本函数经 $() 命令替换
# 调用，子 shell 里 `wait SU_PID` 可正常收割，但收尾 `wait WATCHDOG_PID`
# 在子 shell 作业表中的行为不可靠（看门狗 sleep 300 等待挂满），整批卡死
# 至看门狗超时。SU 详说/渲染链路毫秒级无浏览器挂起面，前台同步跑 +
# 手工超时守护（180s）即可，语义与 run_su 等价。
sfd_builder_chain() {
  local scen_dir="$1" sid="$2"
  local out="${scen_dir}/out" sys_root="${scen_dir}/out/${sid}"
  sfd_seed_workspace "${sys_root}" "${sid}" || { echo ""; return 1; }
  sfd_run_su_sync "${scen_dir}" --out "${out}" --system-id "${sid}" --render-only
  [ "${RC}" -eq 0 ] || { echo ""; return 1; }
  sfd_run_su_sync "${scen_dir}" --out "${out}" --system-id "${sid}" --detailed-doc
  [ "${RC}" -eq 0 ] || { echo ""; return 1; }
  echo "${sys_root}"
}

# sfd_run_su_sync <场景目录> <参数...>：run_su 的命令替换安全形态——
# 前台同步执行 SU CLI，180s 手工超时守护（sleep+kill -0 轮询，不产生
# 需 wait 的后台作业），stdout/stderr 汇入 场景目录/run.log，RC 回传
# 退出码（124=超时）。
sfd_run_su_sync() {
  local scen_dir="$1"; shift
  mkdir -p "${scen_dir}"
  ( cd "${REPO_ROOT}" && exec "${PYTHON}" -B "${SU_CLI}" "$@" ) \
    >>"${scen_dir}/run.log" 2>&1 &
  local pid=$!
  CHILD_PIDS+=("${pid}")
  local waited=0
  while kill -0 "${pid}" 2>/dev/null; do
    if [ "${waited}" -ge 1800 ]; then   # 180s = 1800 × 0.1s
      kill -KILL "${pid}" 2>/dev/null || true
      RC=124
      return "${RC}"
    fi
    sleep 0.1
    waited=$((waited + 1))
  done
  wait "${pid}"; RC=$?
  return "${RC}"
}

# ---- 场景 [10]：SFD S-2 降级装配（2/5 草稿 → 恰 4 节 degraded、exit 0）----
log "===== 场景 [10] SFD 降级装配 ====="
if ! scenario_selected 10; then
  skip "[10]SFD降级装配" "--only 未选择"
else
  S10="${WORK_ROOT}/s10"; mkdir -p "${S10}"
  S10_ERR=""
  S10_ROOT="$(sfd_builder_chain "${S10}" sfd-deg)"
  [ -n "${S10_ROOT}" ] || S10_ERR="builder 前置链路失败（见 ${S10}/run.log）"
  if [ -z "${S10_ERR}" ] && ! sfd_place_drafts "${S10_ROOT}" part; then
    S10_ERR="部分草稿放置失败"
  fi
  if [ -z "${S10_ERR}" ]; then
    run_su "${S10}" --out "${S10}/out" --system-id sfd-deg --assemble
    [ "${RC}" -eq 0 ] || S10_ERR="降级装配退出码 ${RC}（期望 0——降级不崩）"
  fi
  if [ -z "${S10_ERR}" ]; then
    # 缺 02/03/05 → 第 2/3/6 节 degraded；04 缺 SFD 标记与否不受影响
    # （part 保留 04 全文含标记 → 4/5 ok）。期望 degraded_sections=[2,3,6]
    DEG10="$(json_get "${S10_ROOT}/detailed/assembly-report.json" \
      "str(d['degraded_sections'])" 2>/dev/null)"
    [ "${DEG10}" = "[2, 3, 6]" ] || S10_ERR="degraded_sections=${DEG10}（期望 [2, 3, 6]）"
    grep -q '\*\*\[降级\]\*\*' "${S10_ROOT}/SYSTEM_FUNCTION_DOC.md" \
      || S10_ERR="${S10_ERR} 终稿缺降级声明"
    head -5 "${S10_ROOT}/SYSTEM_FUNCTION_DOC.md" | grep -q '^<!-- status: final -->$' \
      || S10_ERR="${S10_ERR} 降级出稿仍须 status: final"
  fi
  if [ -z "${S10_ERR}" ]; then pass "[10]SFD降级装配"; else fail "[10]SFD降级装配" "${S10_ERR}"; fi
fi

# ---- 场景 [11]：SFD S-3 凭据扫描收口（注入命中 → exit 2 终稿未更新）----
log "===== 场景 [11] SFD 凭据扫描收口 ====="
if ! scenario_selected 11; then
  skip "[11]SFD凭据收口" "--only 未选择"
else
  S11="${WORK_ROOT}/s11"; mkdir -p "${S11}"
  S11_ERR=""
  S11_ROOT="$(sfd_builder_chain "${S11}" sfd-scan)"
  [ -n "${S11_ROOT}" ] || S11_ERR="builder 前置链路失败（见 ${S11}/run.log）"
  if [ -z "${S11_ERR}" ] && ! sfd_place_drafts "${S11_ROOT}" all; then
    S11_ERR="合规草稿放置失败"
  fi
  # 基线：先合规装配出一版终稿（保护对象——命中时必须原样保持）
  if [ -z "${S11_ERR}" ]; then
    run_su "${S11}" --out "${S11}/out" --system-id sfd-scan --assemble
    [ "${RC}" -eq 0 ] || S11_ERR="基线装配退出码 ${RC}（期望 0）"
  fi
  if [ -z "${S11_ERR}" ]; then
    cp "${S11_ROOT}/SYSTEM_FUNCTION_DOC.md" "${S11}/final.baseline"
    # 篡改 03 草稿注入四判据向量（假凭据全部运行时拼接，仓库不落明文）：
    # C1 手机号形态 + C3 键值对 + C2 明文 URL + C2 脱敏豁免反例（P1-10：
    # 该行本身不得单独触发命中，仅随行统计无害）+ C4 结构化向量入 04 草稿
    "${PYTHON}" -B - "${S11_ROOT}" <<'PYEOF' || S11_ERR="假凭据注入失败"
"""向合规草稿注入 S-3 判据向量（含 C2 脱敏自引用反例，运行时拼接）。"""
import sys
from pathlib import Path

root = Path(sys.argv[1])
sections = root / "detailed" / "sections"
p3 = sections / "03-pages.doc.md"
p3.write_text(
    p3.read_text("utf-8")
    + "\n联系人手机号 " + "138" + "0013" + "8000" + " 请复核\n"
    + "password=" + "Tr9" + "uXa2" + "Lk5" + "\n"
    + "mysql://" + "deploy" + ":" + "Qz7" + "Wm2" + "@db.internal:3306/appdb\n"
    + "审计转录 mysql://***" + "REDACTED" + "***@db.internal:3306/appdb 已脱敏\n",
    encoding="utf-8")
p4 = sections / "04-data-semantics.doc.md"
p4.write_text(
    p4.read_text("utf-8")
    + '\n{"auth_code": "' + "k7f2" + "q9d4" + "x1m8" + "vb3c" + '"}\n',
    encoding="utf-8")
PYEOF
  fi
  # 篡改素材包注入素材包面 C4 命中（dev 包 samples 注入拼接 token）
  if [ -z "${S11_ERR}" ]; then
    "${PYTHON}" -B - "${S11_ROOT}" <<'PYEOF' || S11_ERR="素材包篡改失败"
"""dev 素材包 samples 注入 C4 双因子向量（PRD S-3 采样穿透口径）。"""
import json
import sys
from pathlib import Path

pkg = Path(sys.argv[1]) / "detailed/inputs/dev.json"
payload = json.loads(pkg.read_text("utf-8"))
tables = payload["data"].get("db_tables") or [{}]
tables[0]["samples"] = {"auth_code": "k7f2" + "q9d4" + "x1m8" + "vb3c"}
pkg.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          indent=2), encoding="utf-8")
PYEOF
  fi
  if [ -z "${S11_ERR}" ]; then
    # 重跑装配需 --force（基线终稿 status: final 保护）——命中判定发生在
    # 落盘前，--force 只放开保护门，不影响"命中不落盘"红线
    run_su "${S11}" --out "${S11}/out" --system-id sfd-scan --assemble --force
    [ "${RC}" -eq 2 ] || S11_ERR="凭据命中退出码 ${RC}（期望 2）"
    grep -q "凭据扫描命中" "${S11}/run.log" \
      || S11_ERR="${S11_ERR} run.log 缺凭据扫描命中报告"
    # 命中类别断言（C3 至少必现；C4 素材包面与 C1/C2 草稿面按判据口径核对）
    grep -qE "\[kv_credential\]|\[pii\]" "${S11}/run.log" \
      || S11_ERR="${S11_ERR} 缺 kv/pii 类别命中报告"
    grep -q "\[entropy_key\]" "${S11}/run.log" \
      || S11_ERR="${S11_ERR} 缺 entropy_key 类别命中报告（素材包 C4）"
    # 报告只记位置类别不含原文（不成泄露面）
    if grep -q "k7f2q9d4x1m8vb3c\|13800138000" "${S11}/run.log" 2>/dev/null; then
      S11_ERR="${S11_ERR} 扫描报告泄露注入原文"
    fi
    # 终稿未被更新（与基线逐字节一致）
    cmp -s "${S11}/final.baseline" "${S11_ROOT}/SYSTEM_FUNCTION_DOC.md" \
      || S11_ERR="${S11_ERR} 命中后终稿被更新（红线违例）"
  fi
  if [ -z "${S11_ERR}" ]; then pass "[11]SFD凭据收口"; else fail "[11]SFD凭据收口" "${S11_ERR}"; fi
fi

# ---- 场景 [12]：SFD S-4 CLI 违例矩阵（全部 exit 2 + 零副作用）----
log "===== 场景 [12] SFD CLI 违例矩阵 ====="
if ! scenario_selected 12; then
  skip "[12]SFD-CLI违例" "--only 未选择"
else
  S12="${WORK_ROOT}/s12"; mkdir -p "${S12}"
  S12_ERR=""
  # 12a：三 flag 两两互斥（argparse 标准 exit 2）
  for pair in "--render-only --detailed-doc" "--render-only --assemble" \
              "--detailed-doc --assemble"; do
    run_su "${S12}" --out "${S12}/none" --system-id none ${pair}
    [ "${RC}" -eq 2 ] || S12_ERR="${S12_ERR} 互斥组 ${pair} 退出码 ${RC}≠2"
  done
  # 12b：生命周期参数组合拒绝（builder 产物上判定——先于编排即 exit 2）
  S12_ROOT="$(sfd_builder_chain "${S12}" sfd-cli)"
  if [ -z "${S12_ROOT}" ]; then
    S12_ERR="${S12_ERR} builder 前置链路失败（见 ${S12}/run.log）"
  else
    for combo in "--detailed-doc --fresh" "--detailed-doc --resume" \
                 "--detailed-doc --skip-llm-phase" "--assemble --fresh" \
                 "--assemble --resume" "--assemble --skip-llm-phase"; do
      run_su "${S12}" --out "${S12}/out" --system-id sfd-cli ${combo}
      [ "${RC}" -eq 2 ] || S12_ERR="${S12_ERR} 组合 ${combo} 退出码 ${RC}≠2"
    done
    # 12c：缺 --out / --system-id 必填违例（两模式 × 两参数）
    run_su "${S12}" --system-id sfd-cli --detailed-doc
    [ "${RC}" -eq 2 ] || S12_ERR="${S12_ERR} 缺 --out(detailed) 退出码 ${RC}≠2"
    run_su "${S12}" --out "${S12}/out" --assemble
    [ "${RC}" -eq 2 ] || S12_ERR="${S12_ERR} 缺 --system-id(assemble) 退出码 ${RC}≠2"
    # 12d：非空目录零副作用——违例判定先于任何产物写面
    S12EMPTY="${S12}/empty-dir"; mkdir -p "${S12EMPTY}"
    run_su "${S12}" --out "${S12EMPTY}" --system-id sfd-x --assemble
    [ "${RC}" -eq 2 ] || S12_ERR="${S12_ERR} 缺前置装配退出码 ${RC}≠2"
    if [ -n "$(ls -A "${S12EMPTY}" 2>/dev/null)" ]; then
      S12_ERR="${S12_ERR} 违例路径在空目录留下产物（先读后写违例）"
    fi
    # 12e：既有终稿 status: final 保护（先装配一版，再无 --force 重跑）
    sfd_place_drafts "${S12_ROOT}" all >/dev/null 2>&1 \
      || S12_ERR="${S12_ERR} 草稿放置失败"
    run_su "${S12}" --out "${S12}/out" --system-id sfd-cli --assemble
    [ "${RC}" -eq 0 ] || S12_ERR="${S12_ERR} 基线装配退出码 ${RC}≠0"
    run_su "${S12}" --out "${S12}/out" --system-id sfd-cli --assemble
    [ "${RC}" -eq 2 ] || S12_ERR="${S12_ERR} 终稿保护退出码 ${RC}≠2"
    run_su "${S12}" --out "${S12}/out" --system-id sfd-cli --assemble --force
    [ "${RC}" -eq 0 ] || S12_ERR="${S12_ERR} --force 放行退出码 ${RC}≠0"
  fi
  if [ -z "${S12_ERR}" ]; then pass "[12]SFD-CLI违例"; else fail "[12]SFD-CLI违例" "${S12_ERR}"; fi
fi

# ---- 场景 [13]：SFD S-5 幂等（重跑 detailed-doc + assemble 字节一致）----
log "===== 场景 [13] SFD 幂等 ====="
if ! scenario_selected 13; then
  skip "[13]SFD幂等" "--only 未选择"
else
  S13="${WORK_ROOT}/s13"; mkdir -p "${S13}"
  S13_ERR=""
  S13_ROOT="$(sfd_builder_chain "${S13}" sfd-idem)"
  [ -n "${S13_ROOT}" ] || S13_ERR="builder 前置链路失败（见 ${S13}/run.log）"
  if [ -z "${S13_ERR}" ] && ! sfd_place_drafts "${S13_ROOT}" all; then
    S13_ERR="合规草稿放置失败"
  fi
  if [ -z "${S13_ERR}" ]; then
    run_su "${S13}" --out "${S13}/out" --system-id sfd-idem --assemble
    [ "${RC}" -eq 0 ] || S13_ERR="首轮装配退出码 ${RC}（期望 0）"
  fi
  if [ -z "${S13_ERR}" ]; then
    cp "${S13_ROOT}/SYSTEM_FUNCTION_DOC.md" "${S13}/final.v1"
    # 同一起点重装配：--force 重置骨架（终稿保护放行）后重装配
    run_su "${S13}" --out "${S13}/out" --system-id sfd-idem --detailed-doc --force
    [ "${RC}" -eq 0 ] || S13_ERR="重跑 detailed-doc --force 退出码 ${RC}（期望 0）"
  fi
  if [ -z "${S13_ERR}" ]; then
    run_su "${S13}" --out "${S13}/out" --system-id sfd-idem --assemble
    [ "${RC}" -eq 0 ] || S13_ERR="重装配退出码 ${RC}（期望 0）"
  fi
  if [ -z "${S13_ERR}" ]; then
    cmp -s "${S13}/final.v1" "${S13_ROOT}/SYSTEM_FUNCTION_DOC.md" \
      || { S13_ERR="两次装配终稿不一致"; diff "${S13}/final.v1" \
             "${S13_ROOT}/SYSTEM_FUNCTION_DOC.md" | head -6 >>"${S13}/run.log"; }
  fi
  if [ -z "${S13_ERR}" ]; then pass "[13]SFD幂等"; else fail "[13]SFD幂等" "${S13_ERR}"; fi
fi

# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
log "===== 场景结果矩阵 ====="
for item in "${RESULTS[@]}"; do
  log "  ${item}"
done
if [ "${FAIL_COUNT}" -gt 0 ]; then
  log "结论：${FAIL_COUNT} 个场景 FAIL"
  exit 1
fi
log "结论：无 FAIL（PASS/SKIP 见矩阵）"
exit 0
