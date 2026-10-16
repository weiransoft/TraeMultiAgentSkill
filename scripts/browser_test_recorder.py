#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
浏览器测试执行记录器（v2.12/v2.13/v2.14）

核心定位:
  浏览器测试的**执行由宿主 LLM 驱动**（agent-browser / Trae 内置浏览器 /
  Chrome DevTools MCP），本脚本是配套的确定性记录与校验工具——
  不负责驱动浏览器，只负责让每一步操作、每一个断言、每一条错误
  以严格 schema 落盘，并在 finalize 时做一致性校验与人读报告渲染。

职责（纯标准库，零第三方依赖）:
  1. run     —— 初始化一次测试运行目录（run-meta.json）
  2. case    —— 开始/结束一个测试用例（cases/{TC-ID}.json）
  3. step    —— 追加一条步骤记录（动作/输入/耗时/状态 + 内联断言）
  4. assert  —— 给最近一条步骤追加断言明细（expected vs actual）
  5. error   —— 记录页面错误（console）/ 后台错误（网络）/ 疑似缺陷
  6. shot    —— 登记截图（校验文件存在后写索引）
  7. blocker     —— 记录阻碍及解决过程（选择器修复/数据补造/宿主降级等）
  8. db-snapshot —— 执行只读 DB 快照清单并登记进用例（v2.13）
  9. rule        —— 声明式业务规则确定性求值并转断言落盘（v2.13）
  10. dc-check   —— before/after 快照 diff 与 DC 契约预期对照（v2.13）
  11. verify         —— 校验单个用例文件与目录结构的 schema 合规性
  12. defect         —— 跨 run 缺陷台账 add/update/close/audit/reopen/list
                      （v2.14：信号 error(suspected_bug) → 工单，修复+回归记录）
  13. finalize       —— 全量一致性校验（summary 与逐条 case 必须吻合）
                      → 生成 summary.json + summary.md
                      （通过率门禁 + 数据门禁 + 功能覆盖门禁 + 缺陷登记闸）

设计原则:
  - 失败安全: 记录器自身的单次调用失败不影响测试执行主流程
  - schema 强制: 任何缺必填字段的写入直接报错退出（非零码），杜绝残缺记录
  - 幂等安全: 同一 run_id 重复 run 仅补写缺失的 meta，不清空已有记录
  - 数据结论确定性（v2.13）: DB 快照/规则判定/契约对照全部由
    scripts/data_test 确定性内核计算，宿主 LLM 不口算任何数据结论
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# ---------------------------------------------------------------------------
# 常量与合法值（schema 校验的唯一事实来源）
# ---------------------------------------------------------------------------

# 步骤动作合法值（与 browser-test-execution.md 中执行循环对齐）
VALID_ACTIONS = {"navigate", "click", "fill", "submit", "upload",
                 "select", "keypress", "wait", "screenshot", "evaluate"}
# 步骤/用例状态合法值
VALID_STATUSES = {"pass", "fail", "blocked", "skipped"}
# 错误来源合法值：console=页面控制台，network=后台接口，bug=疑似产品缺陷
VALID_ERROR_SOURCES = {"console", "network", "bug"}
# 宿主合法值
VALID_HOSTS = {"agent-browser", "trae-internal", "chrome-devtools", "auto"}
# 阻碍类型合法值（与执行协议表格对齐）
VALID_BLOCKER_TYPES = {"selector_fix", "data_prep", "env_failure",
                       "host_downgrade", "case_fix", "other"}
# 数据断言类型合法值（v2.13：与 UI 六类断言分域，数据结论永不混入口算断言）
VALID_DATA_KINDS = {"db_count", "db_value", "db_diff", "dc_contract", "data_rule"}

# v2.14 功能清单（FUNC-*）：功能项类型五枚举。
# interactive/form 是门禁覆盖对象（必须有用例 + PASS 断言真实覆盖，禁止豁免）；
# display/nav/guard 属展示/跳转/守卫类，允许书面豁免（必须写 reason）。
VALID_FUNC_KINDS = {"interactive", "form", "display", "nav", "guard"}
# FUNC ID 格式：FUNC-{两位页序号}-{两位功能序号}，如 FUNC-03-02
_FUNC_ID_RE = re.compile(r"^FUNC-\d{2}-\d{2}$")

# v2.14 缺陷台账状态机（defects.json 为跨 run 全局台账）：
#   open → fixed（必须 fix.commit）→ closed（回归全 PASS）；
#   closed → regressed（回归再犯）→ fixed → closed；
#   open 可直接 closed（误报关闭，需 --reason 书面说明）；fixed 可回退 open（修复无效）。
DEFECT_TRANSITIONS = {
    "open": {"fixed", "closed"},
    "fixed": {"closed", "open", "regressed"},
    # regressed -> fixed = 再犯后重新修复；regressed -> open = 回炉重开（带 reason）
    "regressed": {"fixed", "open"},
    # closed -> regressed = 回归执行器确认再犯；closed -> open = 再犯回炉重开
    # （带 reason 的 reopen，等同回到 open 重走 fixed->closed 闭环）
    "closed": {"regressed", "open"},
}
# 缺陷严重度五枚举（台账统计与 summary 渲染用）
VALID_DEFECT_SEVERITIES = {"critical", "major", "minor", "trivial", "enhancement"}

# v2.13 确定性数据内核（受限表达式求值 / 快照 diff / 规则求值 / 只读 DB 快照）；
# 导入失败视为安装缺陷直接崩溃——数据门禁不允许静默降级为"无数据校验"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_test.data_rules import (  # noqa: E402
    RuleEvalError,
    diff_matches_expectation,
    diff_snapshots,
    evaluate_rules,
)
from data_test.db_probe import (  # noqa: E402
    DbProbeConfig,
    DbProbeDegraded,
    DbProbeError,
    DbProber,
    load_plan_file,
    run_snapshot_plan,
)


def _now_iso() -> str:
    """当前 UTC 时间的 ISO8601 字符串（记录器统一时间源，保证可对比）"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fail(msg: str) -> None:
    """统一错误出口：打印后非零退出，保证调用方（宿主 LLM）能感知记录失败"""
    print(f"[recorder] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _load_json(path: Path) -> Dict[str, Any]:
    """读取 JSON 文件，不存在或损坏时报错退出（记录文件不允许静默损坏）"""
    if not path.exists():
        _fail(f"文件不存在: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"JSON 损坏: {path}: {exc}")
        return {}  # unreachable


def _dump_json(path: Path, data: Any) -> None:
    """写 JSON 文件（UTF-8、缩进 2、保留中文），自动建父目录"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# run 子命令：初始化运行目录
# ---------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    """初始化一次浏览器测试运行的目录结构与 run-meta.json。

    产出结构::

        {out}/
        ├── run-meta.json
        ├── cases/
        ├── screenshots/
        └── visual-diff/

    幂等: 已存在同 run_id 时仅校验必填字段，不覆盖已有记录。
    """
    run_dir = Path(args.out) / args.run_id
    meta_path = run_dir / "run-meta.json"
    hosts = args.hosts.split(",")
    # 宿主合法性先于任何目录创建校验，避免非法输入留下半成品目录
    for h in hosts:
        if h not in VALID_HOSTS:
            _fail(f"非法宿主: {h}（合法: {sorted(VALID_HOSTS)}）")
    if meta_path.exists():
        meta = _load_json(meta_path)
        # v2.13 幂等补齐：v2.12 老 run 目录续跑数据用例时补新字段与新子目录
        changed = False
        if "db_snapshots" not in meta:
            meta["db_snapshots"] = {}
            meta["db_config"] = args.db_config or ""
            meta["db_checks"] = args.db_checks or ""
            changed = True
        # v2.14 幂等补齐：老 run 目录续跑功能覆盖用例时补清单登记字段
        if "feature_inventory" not in meta:
            meta["feature_inventory"] = None
            changed = True
        if changed:
            _dump_json(meta_path, meta)
        (run_dir / "db-snapshots").mkdir(parents=True, exist_ok=True)
        print(json.dumps({"status": "exists", "run_dir": str(run_dir),
                          "run_id": meta.get("run_id")}, ensure_ascii=False))
        return 0
    for sub in ("cases", "screenshots", "visual-diff", "db-snapshots"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    meta = {
        "run_id": args.run_id,
        "created_at": _now_iso(),
        "target_url": args.url,
        "hosts": hosts,
        "env": args.env,
        "note": args.note or "",
        "plan_ref": args.plan_ref or "",
        # v2.13 数据校验溯源：快照文件 -> 产生它的清单/配置（run 级全局）
        "db_snapshots": {},
        "db_config": args.db_config or "",
        "db_checks": args.db_checks or "",
        # v2.14 功能覆盖清单登记（{path, sha256, registered_at}；None = 未启用）
        "feature_inventory": None,
        "cases": {},  # TC-ID -> {status, steps, assertions_total, assertions_failed, errors}
    }
    for h in meta["hosts"]:
        if h not in VALID_HOSTS:
            _fail(f"非法宿主: {h}（合法: {sorted(VALID_HOSTS)}）")
    # v2.14：登记功能清单并做 SHA256 锚定（执行期改原文件即锚定失配，防"移动球门"）
    if getattr(args, "feature_inventory", ""):
        meta["feature_inventory"] = _register_inventory(args, run_dir)
    _dump_json(meta_path, meta)
    print(json.dumps({"status": "created", "run_dir": str(run_dir)},
                     ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# case 子命令：用例开始 / 结束
# ---------------------------------------------------------------------------

def cmd_case(args: argparse.Namespace) -> int:
    """开始或结束一个测试用例。

    start: 创建 cases/{TC-ID}.json 空壳并在 run-meta 登记 pending。
    end:   写入最终状态（pass/fail/blocked/skipped），同步 run-meta。
    """
    run_dir = Path(args.out) / args.run_id
    meta_path = run_dir / "run-meta.json"
    case_path = run_dir / "cases" / f"{args.case_id}.json"
    meta = _load_json(meta_path)

    if args.action == "start":
        if case_path.exists():
            _fail(f"用例已存在，禁止重复 start: {case_path}")
        case = {
            "case_id": args.case_id,
            "title": args.title or "",
            "priority": args.priority,
            "host": args.host,
            "priority": args.priority or "P2",
            "linked_flows": [s for s in (args.flows or "").split(",") if s],
            "linked_datasets": [s for s in (args.datasets or "").split(",") if s],
            # v2.13：关联的 DC 数据契约 ID 与 before/after 快照清单文件
            "linked_dc_ids": [s for s in (args.dcs or "").split(",") if s],
            # v2.14：本用例声明覆盖的功能项（必须在已登记清单内，start 当场校验）
            "linked_funcs": _validated_funcs(args.funcs, run_dir),
            "db_check_before": args.db_check_before or "",
            "db_check_after": args.db_check_after or "",
            "status": "running",
            "started_at": _now_iso(),
            "ended_at": None,
            "steps": [],
            "errors": [],
            "screenshots": [],
            "blockers": [],
        }
        if args.host not in VALID_HOSTS:
            _fail(f"非法宿主: {args.host}")
        if args.priority not in {"P0", "P1", "P2"}:
            _fail(f"非法优先级: {args.priority}（合法: P0/P1/P2）")
        _dump_json(case_path, case)
        meta["cases"][args.case_id] = {"status": "running", "priority": args.priority}
        _dump_json(meta_path, meta)
        print(json.dumps({"status": "started", "case_id": args.case_id},
                         ensure_ascii=False))
        return 0

    # end
    case = _load_json(case_path)
    if args.status not in VALID_STATUSES:
        _fail(f"非法状态: {args.status}（合法: {sorted(VALID_STATUSES)}）")
    # FAIL/BLOCKED 的用例强制要求有错误记录或阻碍记录，杜绝"只写失败不写原因"
    if args.status in {"fail", "blocked"} and not case["errors"] and not case["blockers"]:
        _fail(f"用例 {args.case_id} 判 {args.status} 但无任何 error/blocker 记录——"
              f"必须先 recorder error/blocker 记录原因")
    case["status"] = args.status
    case["ended_at"] = _now_iso()
    _dump_json(case_path, case)
    # v2.13 数据校验未执行闸：声明了快照清单却从未落任何快照 -> 拒绝结束
    if (case.get("db_check_before") or case.get("db_check_after")) \
            and not _snapshot_files(run_dir, args.case_id):
        _fail("用例 {0} 声明了 --db-check-before/--db-check-after "
              "但无任何 db-snapshot 记录——数据校验不允许静默跳过".format(args.case_id))
    a_total = sum(len(s["assertions"]) for s in case["steps"])
    a_failed = sum(1 for s in case["steps"] for a in s["assertions"] if not a["pass"])
    # v2.13：数据断言单独计数（kind in VALID_DATA_KINDS），finalize 数据门禁输入
    d_total = sum(1 for s in case["steps"] for a in s["assertions"]
                  if a["kind"] in VALID_DATA_KINDS)
    d_failed = sum(1 for s in case["steps"] for a in s["assertions"]
                   if a["kind"] in VALID_DATA_KINDS and not a["pass"])
    snap_files = _snapshot_files(run_dir, args.case_id)
    meta["cases"][args.case_id] = {
        "status": args.status,
        "priority": case["priority"],
        "steps": len(case["steps"]),
        "assertions_total": a_total,
        "assertions_failed": a_failed,
        "data_assertions_total": d_total,
        "data_assertions_failed": d_failed,
        "db_snapshots": len(snap_files),
        "errors": len(case["errors"]),
        "blockers": len(case["blockers"]),
    }
    _dump_json(meta_path, meta)
    print(json.dumps({"status": "ended", "case_id": args.case_id,
                      "result": args.status}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# step / assert / error / shot / blocker 子命令：步骤级明细记录
# ---------------------------------------------------------------------------

def _case_context(args: argparse.Namespace) -> tuple:
    """加载用例文件并校验其处于 running 状态（所有明细写入的前置条件）"""
    run_dir = Path(args.out) / args.run_id
    case_path = run_dir / "cases" / f"{args.case_id}.json"
    case = _load_json(case_path)
    if case["status"] != "running":
        _fail(f"用例 {args.case_id} 状态为 {case['status']}，明细只能写入 running 用例")
    return run_dir, case_path, case


def cmd_step(args: argparse.Namespace) -> int:
    """追加一条步骤记录。

    必填校验: 动作合法 / 目标非空 / 状态合法。
    截图动作（screenshot）必须带 --path 且文件真实存在。
    """
    run_dir, case_path, case = _case_context(args)
    if args.action_name not in VALID_ACTIONS:
        _fail(f"非法动作: {args.action_name}（合法: {sorted(VALID_ACTIONS)}）")
    if not args.target:
        _fail("步骤必须提供 --target（元素定位或 URL，禁止空目标）")
    if args.status not in VALID_STATUSES - {"skipped"}:
        _fail(f"步骤状态只能是 pass/fail/blocked，收到: {args.status}")
    step = {
        "seq": len(case["steps"]) + 1,
        "action": args.action_name,
        "target": args.target,
        "input_dataset": args.dataset or "",
        # v2.14：步骤挂接功能项；覆盖判定 = 该 FUNC 关联 >=1 条 PASS 断言
        "funcs": _validated_funcs(args.funcs, run_dir),
        "duration_ms": args.duration_ms,
        "status": args.status,
        "assertions": [],
        "console_errors": [],
        "network_errors": [],
        "screenshot": "",
        "note": args.note or "",
        "ts": _now_iso(),
    }
    if args.action_name == "screenshot":
        shot = Path(args.path or "")
        if not shot.is_file():
            _fail(f"截图文件不存在: {args.path}（必须先在浏览器端截图落盘再登记）")
        step["screenshot"] = args.path
    case["steps"].append(step)
    _dump_json(case_path, case)
    print(json.dumps({"status": "step_recorded", "case_id": args.case_id,
                      "seq": step["seq"]}, ensure_ascii=False))
    return 0


def cmd_assert(args: argparse.Namespace) -> int:
    """给指定步骤（默认最近一条）追加断言明细。

    断言失败（--pass false）时自动把所属步骤标为 fail（步骤级联规则）。
    """
    run_dir, case_path, case = _case_context(args)
    idx = (args.seq - 1) if args.seq else len(case["steps"]) - 1
    if not (0 <= idx < len(case["steps"])):
        _fail(f"断言目标步骤不存在: seq={args.seq}")
    assertion = {
        "expected": args.expected,
        "actual": args.actual,
        "pass": (args.passed == "true"),
        "kind": args.kind,
    }
    if args.kind not in {"url", "dom", "network", "console", "text", "state"} | VALID_DATA_KINDS:
        _fail(f"非法断言类型: {args.kind}（UI 六类 + v2.13 数据五类 {sorted(VALID_DATA_KINDS)}）")
    case["steps"][idx]["assertions"].append(assertion)
    if not assertion["pass"]:
        case["steps"][idx]["status"] = "fail"
    _dump_json(case_path, case)
    print(json.dumps({"status": "assert_recorded", "case_id": args.case_id,
                      "seq": idx + 1, "pass": assertion["pass"]}, ensure_ascii=False))
    return 0


def cmd_error(args: argparse.Namespace) -> int:
    """记录一条错误：console（页面）/ network（后台）/ bug（疑似产品缺陷）。

    疑似缺陷（source=bug）必须携带复现摘要 --detail。
    同时把错误镜像到最近步骤的对应错误数组，保证步骤级可回溯。
    """
    run_dir, case_path, case = _case_context(args)
    if args.source not in VALID_ERROR_SOURCES:
        _fail(f"非法错误来源: {args.source}（合法: {sorted(VALID_ERROR_SOURCES)}）")
    if args.source == "bug" and not args.detail:
        _fail("疑似缺陷必须提供 --detail（复现步骤 + 现象摘要）")
    entry = {
        "source": args.source,
        "step_seq": len(case["steps"]),
        # v2.14：errors 数组内 1 起序号，作为缺陷台账 bug_ref 的稳定关联键
        "error_seq": len(case["errors"]) + 1,
        "message": args.message,
        "detail": args.detail or "",
        "suspected_bug": (args.suspected_bug == "true"),
        "url": args.url or "",
        "ts": _now_iso(),
    }
    case["errors"].append(entry)
    if case["steps"]:
        if args.source == "console":
            case["steps"][-1]["console_errors"].append(args.message)
        elif args.source == "network":
            case["steps"][-1]["network_errors"].append(args.message)
    _dump_json(case_path, case)
    print(json.dumps({"status": "error_recorded", "case_id": args.case_id,
                      "source": args.source}, ensure_ascii=False))
    return 0


def cmd_shot(args: argparse.Namespace) -> int:
    """登记截图文件（校验存在性），关联到指定步骤（默认最近一条）。"""
    run_dir, case_path, case = _case_context(args)
    shot = Path(args.path)
    if not shot.is_file():
        _fail(f"截图文件不存在: {args.path}")
    idx = (args.seq - 1) if args.seq else len(case["steps"]) - 1
    case["screenshots"].append({
        "path": args.path,
        "step_seq": (idx + 1) if 0 <= idx < len(case["steps"]) else 0,
        "label": args.label or "",
        "ts": _now_iso(),
    })
    if 0 <= idx < len(case["steps"]) and not case["steps"][idx]["screenshot"]:
        case["steps"][idx]["screenshot"] = args.path
    _dump_json(case_path, case)
    print(json.dumps({"status": "shot_recorded", "case_id": args.case_id,
                      "path": args.path}, ensure_ascii=False))
    return 0


def cmd_blocker(args: argparse.Namespace) -> int:
    """记录一次阻碍及其解决过程（对应执行协议的六类阻碍）。

    解决（--resolved true）时必须写明 --resolution（怎么解决的）。
    """
    run_dir, case_path, case = _case_context(args)
    if args.blocker_type not in VALID_BLOCKER_TYPES:
        _fail(f"非法阻碍类型: {args.blocker_type}（合法: {sorted(VALID_BLOCKER_TYPES)}）")
    if args.resolved == "true" and not args.resolution:
        _fail("阻碍标记为已解决时必须提供 --resolution 说明解决方式")
    case["blockers"].append({
        "type": args.blocker_type,
        "step_seq": len(case["steps"]),
        "description": args.description,
        "resolved": (args.resolved == "true"),
        "resolution": args.resolution or "",
        "ts": _now_iso(),
    })
    _dump_json(case_path, case)
    print(json.dumps({"status": "blocker_recorded", "case_id": args.case_id,
                      "type": args.blocker_type}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# v2.13 数据校验子命令：db-snapshot / rule / dc-check
# ---------------------------------------------------------------------------

def _snapshot_files(run_dir: Path, case_id: str) -> List[Path]:
    """列出某用例已落盘的全部快照文件（case 数据闸与 finalize 计数的输入）。"""
    return sorted((run_dir / "db-snapshots").glob(f"{case_id}__*.json"))


def _resolve_db_config(args: argparse.Namespace, run_dir: Path):
    """解析本次快照用的 DB 配置：--db-config 优先，缺省回退 run-meta.db_config。

    返回 (config, source_label) 元组——source_label 写进 run 级索引做全链路溯源。
    """
    cfg_path = getattr(args, "db_config", "") or ""
    source = "cli"
    if not cfg_path:
        meta = _load_json(run_dir / "run-meta.json")
        cfg_path = meta.get("db_config") or ""
        source = "run-meta"
    if not cfg_path:
        _fail("db-snapshot 需要 --db-config（或 run 时已登记 --db-config）")
    try:
        cfg = DbProbeConfig.load(cfg_path)
    except DbProbeError as exc:
        _fail(str(exc))
        raise  # _fail 已退出；raise 仅为静态分析收口
    return cfg, source


def cmd_db_snapshot(args: argparse.Namespace) -> int:
    """执行只读 DB 快照清单并把快照文件登记进用例与 run-meta。

    诚实降级：驱动缺失（DbProbeDegraded）→ 自动写 env_failure 阻碍、退出码 0，
    让宿主 LLM 把数据步骤标 BLOCKED 而不是误报数据 FAIL；
    配置/清单/执行错误（DbProbeError）→ 非零退出，宿主必须先修复再跑。

    快照文件名：{case_id}__{check_id}__{phase}.json（phase = before/after）。
    """
    run_dir, case_path, case = _case_context(args)
    try:
        plans = load_plan_file(args.check_file)
    except DbProbeError as exc:
        _fail(str(exc))
        return 1
    cfg, cfg_source = _resolve_db_config(args, run_dir)
    # 参数注入映射（{{order_id}} 占位替换）——先校验 JSON 合法再建连接
    try:
        param_map = json.loads(args.params) if args.params else {}
    except json.JSONDecodeError as exc:
        _fail(f"--params 不是合法 JSON: {exc}")
        return 1
    if not isinstance(param_map, dict):
        _fail("--params 必须是 JSON 对象")
    snap_dir = run_dir / "db-snapshots"
    written: List[Dict[str, Any]] = []
    try:
        with DbProber(cfg) as prober:
            for plan in plans:
                one = dict(plan)
                one["phase"] = args.phase
                # 参数注入：清单里声明为 {{name}} 的占位由 --params 真实值替换，
                # 未提供的占位立即报错（防止把 {{name}} 字面量查进快照）
                if param_map:
                    one["queries"] = [
                        {**q, "sql": _inject_params(q.get("sql", ""), param_map,
                                                    args.case_id)}
                        for q in plan.get("queries", [])]
                snapshot = run_snapshot_plan(prober, one)
                snap_path = snap_dir / "{0}__{1}__{2}.json".format(
                    args.case_id, snapshot["check_id"], args.phase)
                _dump_json(snap_path, snapshot)
                written.append({"check_id": snapshot["check_id"],
                                "phase": args.phase,
                                "file": snap_path.name,
                                "query_count": len(snapshot["snapshots"])})
    except DbProbeDegraded as exc:
        # 诚实降级：记录 env_failure 阻碍，数据结论以 BLOCKED 呈现（退出码 0）
        case["blockers"].append({
            "type": "env_failure", "step_seq": len(case["steps"]),
            "description": "DB 快照引擎降级：{0}".format(exc),
            "resolved": False, "resolution": "", "ts": _now_iso()})
        _dump_json(case_path, case)
        print(json.dumps({"status": "degraded", "reason": str(exc),
                          "hint": "宿主应把数据步骤标 BLOCKED(env_failure)"},
                         ensure_ascii=False))
        return 0
    except DbProbeError as exc:
        _fail(str(exc))
        return 1
    # 登记关联：用例级（同 check_id+phase 重写时替换旧项，快照可修正重跑）
    links = case.setdefault("db_snapshot_files", [])
    for w in written:
        dup = [x for x in links
               if x["check_id"] == w["check_id"] and x["phase"] == w["phase"]]
        for d in dup:
            links.remove(d)
        links.append(w)
    _dump_json(case_path, case)
    # run 级索引：快照文件 → 产生它的配置/清单（数据结论的全链路溯源）
    meta_path = run_dir / "run-meta.json"
    meta = _load_json(meta_path)
    idx = meta.setdefault("db_snapshots", {})
    for w in written:
        idx[w["file"]] = {"case_id": args.case_id, "check_id": w["check_id"],
                          "phase": w["phase"], "config": cfg_source,
                          "plan": args.check_file, "ts": _now_iso()}
    _dump_json(meta_path, meta)
    print(json.dumps({"status": "snapshots_recorded", "case_id": args.case_id,
                      "phase": args.phase, "written": written}, ensure_ascii=False))
    return 0


def _inject_params(sql: str, param_map: Dict[str, Any], case_id: str) -> str:
    """把 SQL 中的 {{name}} 占位替换为 --params 提供的值文本。

    值必须是 JSON 标量；字符串加单引号（内部单引号翻倍转义），数字/布尔/None
    直写。防注入：替换结果复查，含分号/注释符/花括号残留直接拒绝。
    这是声明式清单的参数化旁路（主通道仍是 run_query 的 params 绑定）。
    """
    import re as _re

    def repl(m):
        name = m.group(1)
        if name not in param_map:
            _fail("用例 {0} 快照 SQL 占位 {1} 未在 --params 提供".format(case_id, name))
        val = param_map[name]
        if isinstance(val, bool):
            text = "1" if val else "0"
        elif val is None:
            text = "NULL"
        elif isinstance(val, (int, float)):
            text = str(val)
        elif isinstance(val, str):
            text = "'" + val.replace("'", "''") + "'"
        else:
            _fail("--params.{0} 只允许 JSON 标量（字符串/数字/布尔/null）".format(name))
        if any(tok in text for tok in ("{{", "}}", "--", "/*")) or ";" in text:
            _fail("--params.{0} 的值含 SQL 注入面字符，已拒绝".format(name))
        return text

    return _re.sub(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}", repl, sql)


def _load_snapshots_for_rule(run_dir: Path, case_id: str) -> Dict[str, Dict[str, Any]]:
    """加载用例全部快照文件 → {label: rows 结构} 合并视图（rule 子命令上下文）。

    合并语义：快照内 snapshots[label] 直接取 run_query 行结构；同时为每个
    label 生成 {phase}__{label} 键——before/after 同名 label 并存时（如 SM
    规则需要两份同名状态快照）用带前缀键显式区分。
    """
    merged: Dict[str, Dict[str, Any]] = {}
    for snap_file in _snapshot_files(run_dir, case_id):
        snap = _load_json(snap_file)
        phase = snap.get("phase", "")
        for label, payload in snap.get("snapshots", {}).items():
            merged[label] = payload
            merged["{0}__{1}".format(phase, label)] = payload
    return merged


def cmd_rule(args: argparse.Namespace) -> int:
    """业务规则求值：逐条确定性判定，每条转 data_rule 断言挂到目标步骤。

    求值上下文 = 该用例全部快照合并视图（见 _load_snapshots_for_rule）；
    结论同时写 case["rule_verdicts"]（finalize 数据门禁的输入）。
    """
    run_dir, case_path, case = _case_context(args)
    rules_raw = _load_json(Path(args.rules))
    rules = rules_raw.get("rules") if isinstance(rules_raw, dict) else rules_raw
    if not isinstance(rules, list) or not rules:
        _fail("规则文件 {0} 必须含非空 rules 数组".format(args.rules))
    idx = (args.seq - 1) if args.seq else len(case["steps"]) - 1
    if not (0 <= idx < len(case["steps"])):
        _fail("规则断言目标步骤不存在: seq={0}".format(args.seq))
    snapshots = _load_snapshots_for_rule(run_dir, args.case_id)
    if not snapshots:
        _fail("用例 {0} 无任何快照文件——rule 必须在 db-snapshot 之后执行".format(
            args.case_id))
    try:
        verdicts = evaluate_rules(rules, snapshots)
    except RuleEvalError as exc:
        _fail("规则求值失败：{0}".format(exc))
        return 1
    step = case["steps"][idx]
    verdict_list = case.setdefault("rule_verdicts", [])
    passed = failed = 0
    for v in verdicts:
        step["assertions"].append({"expected": v["expected"],
                                   "actual": v["actual"],
                                   "pass": v["pass"], "kind": "data_rule",
                                   "evidence": v["rule_id"]})
        verdict_list.append(v)
        if v["pass"]:
            passed += 1
        else:
            failed += 1
    if failed:
        step["status"] = "fail"
    _dump_json(case_path, case)
    print(json.dumps({"status": "rules_evaluated", "case_id": args.case_id,
                      "total": len(verdicts), "passed": passed, "failed": failed},
                     ensure_ascii=False))
    return 0


def cmd_dc_check(args: argparse.Namespace) -> int:
    """DC 契约对照：before/after 同名查询快照做行级 diff，与预期逐项核对。

    快照配对：before/after 各取对应 phase 快照中 label=--before-label
    （缺省取 before 的第一个 label）的查询结果；缺一侧即 FAIL 并带明细。
    判定由 data_test.diff_snapshots + diff_matches_expectation 确定性完成，
    结论以 dc_contract 断言落盘（evidence=契约 ID）。
    """
    run_dir, case_path, case = _case_context(args)
    # 解析预期：--expectation 内联优先，否则回退用例 dc_contracts 同 ID 条目
    expectation: Dict[str, Any] = {}
    if args.expectation:
        try:
            expectation = json.loads(args.expectation)
        except json.JSONDecodeError as exc:
            _fail("--expectation 不是合法 JSON: {0}".format(exc))
        if not isinstance(expectation, dict):
            _fail("--expectation 必须是 JSON 对象")
    else:
        for dcc in case.get("dc_contracts", []):
            if dcc.get("contract_id") == args.contract_id:
                expectation = dcc.get("expectation") or {}
                break
        if not expectation:
            _fail("用例无 DC 契约 {0}（且 --expectation 未提供）".format(args.contract_id))
    idx = (args.seq - 1) if args.seq else len(case["steps"]) - 1
    if not (0 <= idx < len(case["steps"])):
        _fail("DC 断言目标步骤不存在: seq={0}".format(args.seq))
    # 快照收集：phase -> label -> rows
    phase_rows: Dict[str, Dict[str, List[Dict[str, Any]]]] = {"before": {}, "after": {}}
    for snap_file in _snapshot_files(run_dir, args.case_id):
        snap = _load_json(snap_file)
        phase = snap.get("phase")
        if phase not in phase_rows:
            continue
        for label, payload in snap.get("snapshots", {}).items():
            phase_rows[phase][label] = payload.get("rows", [])
    key_cols = [s.strip() for s in (args.key_columns or "").split(",") if s.strip()]
    label = args.before_label or next(iter(phase_rows["before"]), "")
    b_rows = phase_rows["before"].get(label)
    a_rows = phase_rows["after"].get(label)
    diff_out: Dict[str, Any] = {}
    if b_rows is None or a_rows is None:
        ok = False
        detail = "缺少 phase={0} 快照（需要 label={1}，先跑 db-snapshot）".format(
            "before" if b_rows is None else "after", label or "<空>")
    else:
        diff_out = diff_snapshots(b_rows, a_rows, key_cols)
        ok, detail = diff_matches_expectation(diff_out, expectation)
    step = case["steps"][idx]
    step["assertions"].append({"expected": json.dumps(expectation, ensure_ascii=False),
                               "actual": detail, "pass": ok,
                               "kind": "dc_contract", "evidence": args.contract_id})
    if not ok:
        step["status"] = "fail"
    # 契约结论登记（finalize 数据门禁输入；同 ID 重写时替换旧结论）
    dcs = case.setdefault("dc_contracts", [])
    dcs[:] = [d for d in dcs if d.get("contract_id") != args.contract_id]
    dcs.append({"contract_id": args.contract_id, "expectation": expectation,
                "result": "pass" if ok else "fail",
                "diff_summary": {k: (len(v) if isinstance(v, list) else v)
                                 for k, v in diff_out.items()
                                 if k in ("added", "removed", "changed",
                                          "before_count", "after_count")},
                "ts": _now_iso()})
    _dump_json(case_path, case)
    print(json.dumps({"status": "dc_checked", "case_id": args.case_id,
                      "contract_id": args.contract_id, "pass": ok,
                      "detail": detail}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# verify 子命令：单用例 schema 校验
# ---------------------------------------------------------------------------

def _validate_case(case: Dict[str, Any]) -> List[str]:
    """校验单个用例记录，返回问题清单（空 = 合规）。

    规则与写入端保持一致，能拦住手工编辑引入的残缺。
    """
    problems: List[str] = []
    for key in ("case_id", "priority", "host", "status", "steps"):
        if key not in case:
            problems.append(f"缺必填字段: {key}")
    if case.get("host") not in VALID_HOSTS:
        problems.append(f"非法 host: {case.get('host')}")
    if case.get("status") not in VALID_STATUSES | {"running"}:
        problems.append(f"非法 status: {case.get('status')}")
    for st in case.get("steps", []):
        seq = st.get("seq")
        if st.get("action") not in VALID_ACTIONS:
            problems.append(f"step#{seq} 非法 action: {st.get('action')}")
        if not st.get("target"):
            problems.append(f"step#{seq} target 为空")
        if st.get("status") not in VALID_STATUSES:
            problems.append(f"step#{seq} 非法 status: {st.get('status')}")
        # 失败步骤必须有断言失败或错误作为依据
        if st.get("status") == "fail":
            has_failed_assert = any(not a["pass"] for a in st.get("assertions", []))
            has_error = bool(st.get("console_errors") or st.get("network_errors"))
            if not (has_failed_assert or has_error):
                problems.append(f"step#{seq} 判 fail 但无失败断言/错误依据")
        if st.get("action") == "screenshot" and not st.get("screenshot"):
            problems.append(f"step#{seq} screenshot 动作为空路径")
    # v2.14：步骤 funcs 字段必须为字符串数组（缺省 [] 兼容 v2.12/13 老用例文件）
    for st in case.get("steps", []):
        funcs = st.get("funcs", [])
        if not isinstance(funcs, list) or any(not isinstance(x, str) for x in funcs):
            problems.append("step#{0} funcs 必须是字符串数组".format(st.get("seq")))
    # v2.13：dc_contracts 结构校验（每项必须 contract_id + expectation 对象）
    for dcc in case.get("dc_contracts", []):
        if not dcc.get("contract_id"):
            problems.append("dc_contracts 存在缺 contract_id 的条目")
        if not isinstance(dcc.get("expectation"), dict):
            problems.append("dc_contracts[{0}] expectation 必须是对象".format(
                dcc.get("contract_id")))
    # v2.13：数据类断言必须携带 evidence 溯源（快照文件名或规则 ID），
    # 否则无法复核——数据结论的可审计性是闭环成立的前提
    for st in case.get("steps", []):
        for a in st.get("assertions", []):
            if a.get("kind") in VALID_DATA_KINDS and not a.get("evidence"):
                problems.append("step#{0} 数据断言 kind={1} 缺 evidence 溯源".format(
                    st.get("seq"), a.get("kind")))
    # FAIL 用例必须有原因
    if case.get("status") == "fail" and not case.get("errors") and not case.get("blockers"):
        problems.append("用例判 fail 但无 error/blocker 记录")
    return problems


def cmd_verify(args: argparse.Namespace) -> int:
    """校验一个（或 --all 全部）用例文件的 schema 合规性。"""
    run_dir = Path(args.out) / args.run_id
    cases_dir = run_dir / "cases"
    files: List[Path] = []
    if getattr(args, "all", False):
        files = sorted(cases_dir.glob("*.json"))
        if not files:
            _fail(f"无用例文件: {cases_dir}")
    else:
        files = [cases_dir / f"{args.case_id}.json"]
    total_problems: Dict[str, List[str]] = {}
    for fp in files:
        case = _load_json(fp)
        probs = _validate_case(case)
        if probs:
            total_problems[case.get("case_id", fp.name)] = probs
    ok = not total_problems
    print(json.dumps({"status": "ok" if ok else "violations",
                      "violations": total_problems}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# v2.14 功能覆盖（FUNC-*）与缺陷台账（defects.json）支撑函数
# ---------------------------------------------------------------------------

def _register_inventory(args: argparse.Namespace, run_dir: Path) -> Dict[str, Any]:
    """登记每页功能清单：schema 校验 + SHA256 锚定（run-meta 只存 path+sha 存根）。

    清单原件保留在 planner 产出位置（不拷贝进 run 目录），finalize 用 run-meta
    登记的 path 现读现算；SHA256 失配即拒绝收尾，防执行期改 kind/删条目绕门禁。
    """
    inv_path = Path(args.feature_inventory)
    if not inv_path.exists():
        _fail(f"功能清单文件不存在: {inv_path}")
    try:
        inv = json.loads(inv_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"功能清单不是合法 JSON: {inv_path}: {exc}")
    problems = _validate_inventory(inv)
    if problems:
        _fail("功能清单 schema 校验失败: " + "; ".join(problems[:6]))
    sha = hashlib.sha256(inv_path.read_bytes()).hexdigest()
    return {"path": str(inv_path.resolve()), "sha256": sha,
            "registered_at": _now_iso()}


def _validate_inventory(inv: Dict[str, Any]) -> List[str]:
    """功能清单 schema 校验：evidence 格式 / kind 五枚举 / ID 格式与唯一性 / 豁免约束。"""
    problems: List[str] = []
    ids: set = set()
    for page in inv.get("pages", []):
        if not page.get("path") or not page.get("name"):
            problems.append("页面条目缺 path/name: " + str(page.get("path")))
        for f in page.get("funcs", []):
            fid = f.get("id", "")
            if not _FUNC_ID_RE.match(fid):
                problems.append(f"FUNC ID 格式非法: {fid}（须 FUNC-NN-NN）")
                continue
            if fid in ids:
                problems.append(f"FUNC ID 重复: {fid}")
            ids.add(fid)
            if f.get("kind") not in VALID_FUNC_KINDS:
                problems.append(
                    f"{fid} kind 非法: {f.get('kind')}（合法: {sorted(VALID_FUNC_KINDS)}）")
            ev = f.get("evidence", [])
            if not ev:
                problems.append(f"{fid} 缺 evidence（每个功能项必须有代码证据）")
            for e in ev:
                if not re.search(r":\d+$", str(e)):
                    problems.append(f"{fid} evidence 格式须为 文件:行号: {e}")
            if f.get("waived"):
                if f.get("kind") in ("interactive", "form"):
                    problems.append(f"{fid} interactive/form 禁止豁免")
                if not f.get("waive_reason"):
                    problems.append(f"{fid} 已豁免但缺 waive_reason（豁免必须书面说明）")
    return problems


def _inventory_funcs(run_dir: Path) -> Optional[Dict[str, Dict[str, Any]]]:
    """加载本 run 锚定的功能清单，返回 {FUNC-ID: 功能项定义}；未启用时返回 None。

    SHA256 校验在此统一执行：run-meta.feature_inventory 只作存根（可审计），
    清单原件现读；执行期改动原件 → SHA 失配直接拒绝（防"移动球门"）。
    """
    meta = _load_json(run_dir / "run-meta.json")
    info = meta.get("feature_inventory")
    if not info:
        return None
    inv_path = Path(info["path"])
    if not inv_path.exists():
        _fail(f"已登记的功能清单文件不存在: {inv_path}")
    data = inv_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != info.get("sha256"):
        _fail(f"功能清单文件与登记 SHA256 不一致（执行期禁止修改清单）: {inv_path}")
    inv = json.loads(data.decode("utf-8"))
    problems = _validate_inventory(inv)
    if problems:
        _fail("功能清单 schema 校验失败: " + "; ".join(problems[:6]))
    return {f["id"]: dict(f) for page in inv.get("pages", []) for f in page.get("funcs", [])}


def _validated_funcs(raw: Optional[str], run_dir: Path) -> List[str]:
    """--funcs 合法性：FUNC ID 格式 + 必须存在于本 run 已登记清单（防编造 ID）。"""
    ids = [x.strip() for x in (raw or "").split(",") if x.strip()]
    if not ids:
        return []
    known = _inventory_funcs(run_dir)
    for fid in ids:
        if not _FUNC_ID_RE.match(fid):
            _fail(f"非法 FUNC ID: {fid}（格式 FUNC-NN-NN）")
        if known is not None and fid not in known:
            _fail(f"FUNC ID 不在本 run 已登记功能清单内: {fid}（禁止编造功能项）")
    return ids


def _compute_feature_gate(run_dir: Path, meta: Dict[str, Any],
                          case_files: List[Path]):
    """finalize 现算功能覆盖矩阵：覆盖判定 = FUNC 关联 >=1 条 PASS 断言（步骤级）。

    覆盖事实源 = cases/*.json 步骤内 funcs 字段（recorder 写入、经 schema 校验，
    是体系内唯一防篡改事实源）；run-meta 不参与覆盖判定。

    返回 (gate, coverage_dict)：
      - 未登记清单 -> ("PASS", {enabled: False, ...})（v2.12/13 老 run 不受影响）
      - interactive/form 全部覆盖、display/nav/guard 覆盖或已豁免 -> PASS
      - 存在未覆盖且未豁免（display/nav/guard 无豁免） -> FAIL
    """
    funcs = _inventory_funcs(run_dir)
    if funcs is None:
        return "PASS", {"enabled": False, "note": "本 run 未登记功能清单（门禁未启用）",
                        "missing": [], "waived": []}
    # 覆盖收集：FUNC -> 有 PASS 断言的步骤所属用例列表（真覆盖，非用例挂名）
    covered_map: Dict[str, List[str]] = {}
    for fp in case_files:
        case = _load_json(fp)
        cid = case["case_id"]
        for st in case.get("steps", []):
            n_pass = sum(1 for a in st.get("assertions", []) if a.get("pass"))
            if not n_pass:
                continue
            for fid in st.get("funcs", []):
                covered_map.setdefault(fid, []).append(cid)
    missing: List[Dict[str, Any]] = []
    waived_out: List[Dict[str, Any]] = []
    covered_ids: List[str] = []
    must_total = must_covered = 0
    for fid, f in sorted(funcs.items()):
        if fid in covered_map:
            covered_ids.append(fid)
            if f["kind"] in ("interactive", "form"):
                must_covered += 1
        elif f.get("waived"):
            waived_out.append({"id": fid, "kind": f["kind"],
                               "reason": f.get("waive_reason", "")})
            if f["kind"] in ("interactive", "form"):
                must_covered += 1
        else:
            missing.append({"id": fid, "kind": f["kind"],
                            "desc": f.get("desc", "")})
        if f["kind"] in ("interactive", "form"):
            must_total += 1
    note = ("interactive/form 功能项 {0}/{1} 已覆盖".format(must_covered, must_total)
            if must_total else "清单无 interactive/form 功能项")
    gate = "PASS" if not missing else "FAIL"
    return gate, {
        "enabled": True,
        "total": len(funcs),
        "covered": len(covered_ids),
        "must_total": must_total,
        "must_covered": must_covered,
        "covered_ids": covered_ids,
        "covered_by": {k: sorted(set(v)) for k, v in sorted(covered_map.items())},
        "waived": waived_out,
        "missing": missing,
        "note": note,
    }


def _defects_path(out_root: Path) -> Path:
    """缺陷台账文件路径（与 run 目录平级的跨 run 全局文件）。"""
    return out_root / "defects.json"


def _load_defects(out_root: Path) -> Dict[str, Any]:
    """加载全局缺陷台账；不存在返回空 dict（台账未启用≠有缺陷，不得据此 FAIL）。"""
    p = _defects_path(out_root)
    if not p.exists():
        return {}
    return _load_json(p)


def _append_history(entry: Dict[str, Any], action: str) -> None:
    """台账条目追加审计历史（只追加不改写历史，防篡改）。

    add 动作额外落 defect_id：条目即使被外部清理，历史中的用号仍可追溯，
    供 _next_defect_id 防 ID 复用。
    """
    rec = {"action": action, "ts": _now_iso()}
    if action == "add":
        rec["defect_id"] = entry.get("defect_id", "")
    entry.setdefault("history", []).append(rec)


def _defect_registration_check(out_root: Path,
                               bug_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """疑似缺陷必须登记台账（防漏记）。bug_list 来自 finalize 遍历用例文件。

    兼容策略：台账从未启用（文件不存在）时跳过核对（v2.12/13 行为不变）；
    台账一旦存在，本 run 每条 suspected_bug 必须有 bug_ref 匹配的记录。
    """
    p = _defects_path(out_root)
    if not p.exists():
        return []
    ledger = _load_defects(out_root)
    refs = set()
    for d in ledger.values():
        br = d.get("bug_ref") or {}
        refs.add((br.get("run_id", ""), br.get("case_id", ""), br.get("error_seq", -1)))
    violations: List[Dict[str, Any]] = []
    for b in bug_list:
        key = (b.get("run_id", ""), b.get("case_id", ""), b.get("error_seq", -1))
        if key not in refs:
            violations.append({"run_id": b.get("run_id", ""),
                               "case_id": b.get("case_id", ""),
                               "error_seq": b.get("error_seq"),
                               "message": b.get("message", "")})
    return violations


def _next_defect_id(ledger: Dict[str, Any]) -> str:
    """按台账历史最大序号生成下一个 DEF ID（DEF-001 起，三位补零）。

    同时扫描条目本身与 history 中的 add 记录（defect_id 字段）：
    即使工单条目被外部删除，历史用号也不复用，保证 ID 永不重复。
    """
    mx = 0
    candidates = list(ledger.keys())
    for d in ledger.values():
        for h in d.get("history", []):
            if h.get("action") == "add" and h.get("defect_id"):
                candidates.append(h["defect_id"])
    for did in candidates:
        m = re.match(r"DEF-(\d+)$", did or "")
        if m:
            mx = max(mx, int(m.group(1)))
    return "DEF-{0:03d}".format(mx + 1)


def _split_ids(raw: str) -> List[str]:
    """逗号分隔 ID 串的通用拆分（去空白、去空项）。"""
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def cmd_defect(args: argparse.Namespace) -> int:
    """跨 run 缺陷台账：add / update / close / audit / reopen / list。

    信号-工单模型（与 error(suspected_bug) 的边界）：
      error(suspected_bug) = 执行期信号；defect = 正式工单（bug_ref 强制关联信号、
      fix 修复记录、regression 回归记录、history 状态机审计轨迹）。
    门禁语义（架构师 B1 修正）：
      - 单 run finalize 只管"漏登"（每条 suspected_bug 必须有台账登记）；
      - audit 输出"漏修"视图（open/regressed 清单 + closed 但回归未全 PASS 违规）；
      - close 核对回归 run 的 summary.json 真实结果（cases[TC].status==pass），
        不采信口头声明。
    """
    out_root = Path(args.out)
    p = _defects_path(out_root)
    ledger: Dict[str, Any] = _load_defects(out_root) if p.exists() else {}

    if args.action == "list":
        print(json.dumps(ledger, ensure_ascii=False, indent=2))
        return 0

    if args.action == "audit":
        # 台账审计视图：未修清单 + 关闭但回归未全 PASS 的违规（独立于单 run 门禁）
        open_items = [d for d in ledger.values()
                      if d.get("status") in ("open", "regressed")]
        invalid_closed: List[Dict[str, Any]] = []
        for d in ledger.values():
            if d.get("status") == "closed" and d.get("regression", {}).get("run_id"):
                if not _verify_regression_run(out_root, d):
                    invalid_closed.append(d)
        verdict = "PASS" if not open_items and not invalid_closed else "FAIL"
        print(json.dumps({"status": "ok", "gate": verdict,
                          "open_or_regressed": open_items,
                          "closed_but_regression_failed": invalid_closed},
                         ensure_ascii=False, indent=2))
        return 0 if verdict == "PASS" else 1

    if args.action == "add":
        # 新工单必须关联真实存在的 suspected_bug 信号（杜绝凭空造缺陷）
        for key in ("run_id", "case_id", "symptom", "severity"):
            if not getattr(args, key, ""):
                _fail(f"defect add 缺少必填参数 --{key.replace('_', '-')}")
        if args.severity not in VALID_DEFECT_SEVERITIES:
            _fail(f"非法严重度: {args.severity}"
                  f"（合法: {sorted(VALID_DEFECT_SEVERITIES)}）")
        try:
            bug_seq = int(args.bug_ref or 0)
        except ValueError:
            _fail("--bug-ref 必须是数字（errors 数组内序号，1 起）")
            return 1
        case_file = out_root / args.run_id / "cases" / f"{args.case_id}.json"
        case = _load_json(case_file)
        errors = case.get("errors", [])
        if not (1 <= bug_seq <= len(errors)) or not errors[bug_seq - 1].get("suspected_bug"):
            _fail(f"bug_ref={bug_seq} 在用例 {args.case_id} 中不存在或非 suspected_bug"
                  f"——缺陷必须先有执行期信号（error --suspected-bug true）")
        did = _next_defect_id(ledger)
        evs = _split_ids(args.evidence)
        for ev in evs:
            if not Path(ev).exists():
                _fail(f"证据文件不存在: {ev}（台账证据必须真实可查）")
        entry = {
            "defect_id": did,
            "status": "open",
            "severity": args.severity,
            "symptom": args.symptom,
            "detail": args.detail or errors[bug_seq - 1].get("detail", ""),
            "bug_ref": {"run_id": args.run_id, "case_id": args.case_id,
                        "error_seq": bug_seq},
            "funcs": _split_ids(args.funcs),
            "cases": _split_ids(args.cases) or [args.case_id],
            "evidence": evs,
            "fix": {},
            "regression": {},
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        _append_history(entry, "add")
        ledger[did] = entry
        _dump_json(p, ledger)
        print(json.dumps({"status": "added", "defect_id": did}, ensure_ascii=False))
        return 0

    # update / close / reopen 共用前置：--defect-id 必填 + 存在性
    if not args.defect_id:
        _fail(f"defect {args.action} 必须提供 --defect-id")
    d = ledger.get(args.defect_id)
    if d is None:
        _fail(f"缺陷不存在: {args.defect_id}（现有: {sorted(ledger)}）")

    if args.action == "update":
        # 修复记录登记（open|regressed -> fixed）：--commit 即宣告已修复，
        # 状态转移 fixed；回归核对通过后才允许 close 进 closed。
        if args.commit:
            if d["status"] not in ("open", "regressed"):
                _fail(f"缺陷 {args.defect_id} 状态为 {d['status']}，"
                      f"仅 open/regressed 可登记修复记录")
            d["status"] = "fixed"
            d["fix"] = {"commit": args.commit,
                        "summary": args.fix_summary or "",
                        "at": _now_iso()}
            _append_history(d, "fixed")
        else:
            # 无修复记录的 update 仅允许 open 状态补充工单内容
            if d["status"] != "open":
                _fail(f"缺陷 {args.defect_id} 状态为 {d['status']}，"
                      f"仅 open 可 update（登记修复记录请带 --commit）")
        if args.severity:
            if args.severity not in VALID_DEFECT_SEVERITIES:
                _fail(f"非法严重度: {args.severity}")
            d["severity"] = args.severity
        if args.symptom:
            d["symptom"] = args.symptom
        if args.detail:
            d["detail"] = args.detail
        if args.funcs:
            d["funcs"] = _split_ids(args.funcs)
        if args.cases:
            d["cases"] = _split_ids(args.cases)
        if args.evidence:
            evs = _split_ids(args.evidence)
            for ev in evs:
                if not Path(ev).exists():
                    _fail(f"证据文件不存在: {ev}")
            d["evidence"] = d.get("evidence", []) + evs
        d["updated_at"] = _now_iso()
        _append_history(d, "update")
        _dump_json(p, ledger)
        print(json.dumps({"status": "updated", "defect_id": args.defect_id},
                         ensure_ascii=False))
        return 0

    if args.action == "reopen":
        # 修复无效/再犯：-> open（fixed/open 合法来源），必须书面理由
        if "open" not in DEFECT_TRANSITIONS.get(d["status"], set()):
            _fail(f"非法状态转移: {d['status']} -> open"
                  f"（合法: {sorted(DEFECT_TRANSITIONS.get(d['status'], set()))}）")
        if not args.reason:
            _fail("reopen 必须提供 --reason（修复无效/重新开启的书面说明）")
        d["status"] = "open"
        d["reopen_reason"] = args.reason
        d["fix"] = {}
        d["regression"] = {}
        d["updated_at"] = _now_iso()
        _append_history(d, "reopen")
        _dump_json(p, ledger)
        print(json.dumps({"status": "reopened", "defect_id": args.defect_id},
                         ensure_ascii=False))
        return 0

    # action == close：open|fixed -> closed
    if d["status"] == "open":
        # open 直接 close = 误报关闭，必须书面理由（正常修复关闭必须先 update 补 fix）
        if not args.reason:
            _fail("open 直接 close（误报关闭）必须提供 --reason 书面说明；"
                  "真实缺陷请先 update 补 fix 记录再走回归关闭")
        d["close_reason"] = args.reason
        d["status"] = "closed"
        d["updated_at"] = _now_iso()
        _append_history(d, "close")
        _dump_json(p, ledger)
        print(json.dumps({"status": "closed", "defect_id": args.defect_id,
                          "mode": "false-positive"}, ensure_ascii=False))
        return 0
    # fixed 关闭 = 回归验证关闭：必须带回归 run 且 summary.json 逐条核对全 PASS
    if args.commit:
        d["fix"] = {"commit": args.commit, "summary": args.fix_summary or ""}
    if not args.regression_run:
        _fail("fixed 关闭为 closed 必须提供 --regression-run"
              "（核对回归 run summary.json 结果；误报请 reopen --reason 后再关闭）")
    cases = _split_ids(args.regression_cases) or d.get("cases", [])
    d["regression"] = {"run_id": args.regression_run, "cases": cases}
    if not _verify_regression_run(out_root, d):
        _fail(f"回归 run {args.regression_run} 的 summary.json 中回归用例未全部 PASS"
              f"（清单: {cases}）——结果不配合不得关闭缺陷")
    d["status"] = "closed"
    d["updated_at"] = _now_iso()
    _append_history(d, "close")
    _dump_json(p, ledger)
    print(json.dumps({"status": "closed", "defect_id": args.defect_id,
                      "mode": "verified"}, ensure_ascii=False))
    return 0


def _verify_regression_run(out_root: Path, defect: Dict[str, Any]) -> bool:
    """核对回归 run 的 summary.json：回归用例必须全部存在且状态 pass。

    确定性规则（不依赖任何人工声明）：summary.cases[TC].status == "pass" 逐条吻合；
    回归 run 未 finalize / 用例缺失 / 存在非 pass 一律判不通过。
    """
    reg = defect.get("regression", {})
    run_id = reg.get("run_id", "")
    cases = reg.get("cases", [])
    if not run_id or not cases:
        return False
    summary_path = out_root / run_id / "summary.json"
    if not summary_path.exists():
        return False
    summary = _load_json(summary_path)
    case_results = summary.get("cases", {})
    for tc in cases:
        info = case_results.get(tc)
        if not info or info.get("status") != "pass":
            return False
    return True


# ---------------------------------------------------------------------------
# finalize 子命令：一致性校验 + 汇总报告
# ---------------------------------------------------------------------------

def cmd_finalize(args: argparse.Namespace) -> int:
    """收尾：全量校验 → 一致性核对 → summary.json + summary.md。

    一致性规则（summary 数字必须与逐条 case 文件吻合，防止统计造假）:
      - run-meta 的用例数 == cases/ 目录文件数
      - 每个 meta.cases[id].status == case 文件内 status（不允许 running 残留）
      - 每个 meta.cases[id].steps/assertions 数字 == case 文件实算值

    门禁规则:
      - P0 用例通过率必须 100%，否则整体 gate=FAIL
      - 存在 fail/blocked 用例 → gate=FAIL
    """
    run_dir = Path(args.out) / args.run_id
    meta = _load_json(run_dir / "run-meta.json")
    cases_dir = run_dir / "cases"
    files = sorted(cases_dir.glob("*.json"))
    if len(files) != len(meta["cases"]):
        _fail(f"一致性失败: run-meta 登记 {len(meta['cases'])} 例，"
              f"cases/ 实际 {len(files)} 个文件")
    stats = {
        "total": 0, "pass": 0, "fail": 0, "blocked": 0, "skipped": 0,
        "steps_total": 0, "assertions_total": 0, "assertions_failed": 0,
        "console_errors": 0, "network_errors": 0, "suspected_bugs": 0,
        "blockers": 0, "blockers_unresolved": 0, "screenshots": 0,
        # v2.13 数据校验统计（data_gate 的输入）
        "data_snapshots": 0, "data_assertions_total": 0, "data_assertions_failed": 0,
        "dc_total": 0, "dc_pass": 0,
        "rules_total": 0, "rules_pass": 0,
        # 唯一快照文件名收集用 set（finalize 末尾转计数，保证 summary.json 可序列化）
        "data_snapshot_files": set(),
    }
    p0 = {"total": 0, "fail": 0, "blocked": 0}
    bug_list: List[Dict[str, Any]] = []
    blocker_list: List[Dict[str, Any]] = []
    inconsistent: List[str] = []
    for fp in files:
        case = _load_json(fp)
        cid = case["case_id"]
        probs = _validate_case(case)
        if probs:
            _fail(f"schema 校验失败 {cid}: {probs}")
        if case["status"] == "running":
            _fail(f"用例 {cid} 仍在 running——finalize 前必须 recorder case --action end")
        # 数字一致性核对（meta 快照 vs 文件实算）
        a_total = sum(len(s["assertions"]) for s in case["steps"])
        a_fail = sum(1 for s in case["steps"] for a in s["assertions"] if not a["pass"])
        snap = meta["cases"].get(cid, {})
        if (snap.get("status") != case["status"]
                or snap.get("steps", -1) != len(case["steps"])
                or snap.get("assertions_total", -1) != a_total):
            inconsistent.append(cid)
        stats["total"] += 1
        stats[case["status"]] += 1
        stats["steps_total"] += len(case["steps"])
        stats["assertions_total"] += a_total
        stats["assertions_failed"] += a_fail
        stats["screenshots"] += len(case.get("screenshots", []))
        # v2.13 数据计数：用例关联快照文件 + 全局唯一快照集 + 数据断言/DC/规则统计
        case_snaps = _snapshot_files(run_dir, cid)
        stats["data_snapshots"] += len(case_snaps)
        stats["data_snapshot_files"].update(d.name for d in case_snaps)
        for d_assert in (a for s in case["steps"] for a in s["assertions"]
                         if a["kind"] in VALID_DATA_KINDS):
            stats["data_assertions_total"] += 1
            if not d_assert["pass"]:
                stats["data_assertions_failed"] += 1
        for dcc in case.get("dc_contracts", []):
            stats["dc_total"] += 1
            if dcc.get("result") == "pass":
                stats["dc_pass"] += 1
        for rv in case.get("rule_verdicts", []):
            stats["rules_total"] += 1
            if rv.get("pass"):
                stats["rules_pass"] += 1
        for err in case.get("errors", []):
            if err["source"] == "console":
                stats["console_errors"] += 1
            elif err["source"] == "network":
                stats["network_errors"] += 1
            if err.get("suspected_bug"):
                stats["suspected_bugs"] += 1
                bug_list.append({"run_id": args.run_id, "case_id": cid, **err})
        for blk in case.get("blockers", []):
            stats["blockers"] += 1
            if not blk["resolved"]:
                stats["blockers_unresolved"] += 1
                blocker_list.append({"case_id": cid, **blk})
        if case["priority"] == "P0":
            p0["total"] += 1
            if case["status"] == "fail":
                p0["fail"] += 1
            if case["status"] == "blocked":
                p0["blocked"] += 1
    if inconsistent:
        _fail(f"一致性失败（run-meta 快照与用例文件不符）: {inconsistent}")
    # v2.13 数据门禁：启用数据校验（有快照）时——
    #   任何数据断言失败 / DC 契约未全部通过 / 业务规则未全部通过 -> gate=FAIL；
    # 未启用（老 run 或纯 UI 测试）时数据门禁视为 PASS（不惩罚无数据场景）
    stats["data_snapshot_files"] = len(stats["data_snapshot_files"])
    data_enabled = stats["data_snapshot_files"] > 0
    data_gate = "PASS"
    data_gate_reasons: List[str] = []
    if data_enabled:
        if stats["data_assertions_failed"] > 0:
            data_gate = "FAIL"
            data_gate_reasons.append("数据断言失败 {0} 条".format(
                stats["data_assertions_failed"]))
        if stats["dc_total"] and stats["dc_pass"] != stats["dc_total"]:
            data_gate = "FAIL"
            data_gate_reasons.append("DC 契约 {0}/{1} 通过".format(
                stats["dc_pass"], stats["dc_total"]))
        if stats["rules_total"] and stats["rules_pass"] != stats["rules_total"]:
            data_gate = "FAIL"
            data_gate_reasons.append("业务规则 {0}/{1} 通过".format(
                stats["rules_pass"], stats["rules_total"]))
    else:
        data_gate_reasons.append("本 run 未启用 DB 数据校验")
    # v2.14 功能覆盖门禁（清单未登记视为未启用；登记则覆盖矩阵 finalize 现算——
    # cases/*.json 是体系内唯一防篡改事实源，run-meta 只存清单登记与 SHA 锚定）
    feature_gate, feature_cov = _compute_feature_gate(run_dir, meta, files)
    # v2.14 缺陷登记闸：本 run 每条 suspected_bug 必须已在缺陷台账登记（防漏记）；
    # "漏修"是跨 run 生命周期，由 defect --action audit 的台账门禁单独评估
    reg_viol = _defect_registration_check(out_root=Path(args.out), bug_list=bug_list)
    gate = "PASS" if (stats["fail"] == 0 and stats["blocked"] == 0
                      and p0["fail"] == 0 and p0["blocked"] == 0
                      and p0["total"] > 0 and stats["total"] > 0
                      and data_gate == "PASS"
                      and feature_gate == "PASS" and not reg_viol) else "FAIL"
    summary = {
        "run_id": meta["run_id"],
        "finalized_at": _now_iso(),
        "target_url": meta.get("target_url", ""),
        "hosts": meta.get("hosts", []),
        "gate": gate,
        "data_gate": data_gate,
        "data_gate_reasons": data_gate_reasons,
        "db_snapshots": meta.get("db_snapshots", {}),
        # v2.14 功能覆盖矩阵 + 缺陷登记核对 + 缺陷台账快照（跨 run 全局文件只读拷贝）
        "feature_gate": feature_gate,
        "feature_coverage": feature_cov,
        "defect_registration_violations": reg_viol,
        "defects_ledger": _load_defects(Path(args.out)),
        "p0": p0,
        "stats": stats,
        "suspected_bugs": bug_list,
        "unresolved_blockers": blocker_list,
        "cases": meta["cases"],
    }
    _dump_json(run_dir / "summary.json", summary)
    (run_dir / "summary.md").write_text(_render_summary_md(summary), encoding="utf-8")
    print(json.dumps({"status": "finalized", "gate": gate,
                      "data_gate": data_gate,
                      "summary": str(run_dir / "summary.json")}, ensure_ascii=False))
    return 0 if gate == "PASS" else 1


def _render_summary_md(s: Dict[str, Any]) -> str:
    """从 summary.json 渲染人读 Markdown 报告（数字与 JSON 同源，天然一致）"""
    st, p0 = s["stats"], s["p0"]
    rate = (f"{(st['pass'] / st['total'] * 100):.1f}%" if st["total"] else "N/A")
    lines = [
        f"# 浏览器测试运行报告 — {s['run_id']}",
        "",
        f"- **门禁**: {'✅ PASS' if s['gate'] == 'PASS' else '❌ FAIL'}"
        f"（P0 {p0['total']} 条：失败 {p0['fail']}，阻断 {p0['blocked']}）",
        f"- **被测地址**: {s['target_url'] or '未记录'}",
        f"- **宿主**: {', '.join(s['hosts'])}",
        f"- **通过率**: {rate}（{st['pass']}/{st['total']}）",
        f"- **步骤/断言**: {st['steps_total']} 步 / {st['assertions_total']} 断言"
        f"（失败 {st['assertions_failed']}）",
        f"- **页面错误(console)**: {st['console_errors']} 条；"
        f"**后台错误(network)**: {st['network_errors']} 条",
        f"- **疑似产品缺陷**: {st['suspected_bugs']} 条",
        f"- **阻碍**: {st['blockers']} 条（未解决 {st['blockers_unresolved']}）",
        f"- **截图**: {st['screenshots']} 张",
        # v2.13 数据校验统计段（始终渲染，未启用时明确说明）
        "- **数据校验门禁**: {0}（{1}）".format(
            "✅ PASS" if s.get("data_gate") == "PASS" else "❌ FAIL",
            "；".join(s.get("data_gate_reasons", []))),
        "- **DB 快照**: {0} 次 / {1} 个唯一快照文件".format(
            st.get("data_snapshots", 0), st.get("data_snapshot_files", 0)),
        "- **数据断言**: {0} 条（失败 {1}）".format(
            st.get("data_assertions_total", 0), st.get("data_assertions_failed", 0)),
        "- **DC 契约**: {0}/{1} 通过；**业务规则**: {2}/{3} 通过".format(
            st.get("dc_pass", 0), st.get("dc_total", 0),
            st.get("rules_pass", 0), st.get("rules_total", 0)),
        # v2.14 功能覆盖门禁段（始终渲染；未登记清单时明确说明未启用）
        "- **功能覆盖门禁**: {0}（{1}）".format(
            "✅ PASS" if s.get("feature_gate") == "PASS" else "❌ FAIL",
            s.get("feature_coverage", {}).get("note", "")),
        "- **功能覆盖**: {0}/{1}（interactive/form {2}/{3}；已豁免 {4}；未覆盖 {5}）".format(
            s.get("feature_coverage", {}).get("covered", 0),
            s.get("feature_coverage", {}).get("total", 0),
            s.get("feature_coverage", {}).get("must_covered", 0),
            s.get("feature_coverage", {}).get("must_total", 0),
            len(s.get("feature_coverage", {}).get("waived", [])),
            len(s.get("feature_coverage", {}).get("missing", []))),
        "",
        "## 用例结果",
        "",
        "| 用例 | 优先级 | 状态 |",
        "|------|--------|------|",
    ]
    for cid, info in sorted(s["cases"].items()):
        lines.append(f"| {cid} | {info.get('priority', '-')} | {info['status']} |")
    if s["suspected_bugs"]:
        lines += ["", "## 疑似产品缺陷", ""]
        for b in s["suspected_bugs"]:
            lines.append(f"- **[{b['case_id']}]** {b['message']}")
            if b.get("detail"):
                lines.append(f"  - 复现: {b['detail']}")
    if s["unresolved_blockers"]:
        lines += ["", "## 未解决阻碍", ""]
        for b in s["unresolved_blockers"]:
            lines.append(f"- **[{b['case_id']}]** ({b['type']}) {b['description']}")
    # v2.14 缺陷登记违规（suspected_bug 漏登缺陷台账）
    if s.get("defect_registration_violations"):
        lines += ["", "## 缺陷登记违规（疑似缺陷未入台账）", ""]
        for v in s["defect_registration_violations"]:
            lines.append(f"- [{v['run_id']}/{v['case_id']}] {v['message']}")
    # v2.14 缺陷台账章节（仅当台账存在时渲染；数字与 defects.json 同源）
    led = s.get("defects_ledger") or {}
    if led:
        led_items = list(led.values()) if isinstance(led, dict) else led
        cnt: Dict[str, int] = {}
        for d in led_items:
            cnt[d.get("status", "?")] = cnt.get(d.get("status", "?"), 0) + 1
        lines += ["", "## 缺陷台账（跨 run 全局）", "",
                  "状态分布: " + "、".join(f"{k} {v}" for k, v in sorted(cnt.items())), ""]
        for d in led_items:
            lines.append(f"- **{d['defect_id']}** [{d.get('severity', '-')}] "
                         f"({d.get('status', '-')}) {d.get('symptom', '')}")
            if d.get("funcs"):
                lines.append(f"  - 关联功能: {', '.join(d['funcs'])}")
            if d.get("fix") and d["fix"].get("commit"):
                lines.append(f"  - 修复: {d['fix']['commit']} {d['fix'].get('summary', '')}")
            if d.get("regression", {}).get("cases"):
                lines.append(f"  - 回归用例: {', '.join(d['regression']['cases'])}")
    # v2.14 功能覆盖缺口清单（missing + waived 全量列出，供评审复核）
    fc = s.get("feature_coverage") or {}
    if fc.get("missing") or fc.get("waived"):
        lines += ["", "## 功能覆盖缺口", ""]
        for m in fc.get("missing", []):
            lines.append(f"- **未覆盖 {m['id']}** [{m['kind']}] {m['desc']}")
        for w in fc.get("waived", []):
            lines.append(f"- 已豁免 {w['id']} [{w['kind']}]：{w.get('reason', '（缺豁免理由）')}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """构建子命令解析器（run/case/step/assert/error/shot/blocker/verify/finalize）"""
    p = argparse.ArgumentParser(
        prog="browser_test_recorder.py",
        description="v2.12/v2.13 浏览器测试执行记录器：步骤/断言/错误/阻碍/"
                    "DB 快照/业务规则/DC 契约对照/schema 校验/汇总报告")
    p.add_argument("--out", default="browser-test-runs", help="运行产物根目录")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("run", help="初始化一次运行")
    sp.add_argument("--run-id", required=True)
    sp.add_argument("--url", default="", help="被测系统 URL")
    sp.add_argument("--hosts", default="auto",
                    help="可用宿主清单（逗号分隔）")
    sp.add_argument("--env", default="", help="环境描述（如 staging/本地 docker）")
    sp.add_argument("--note", default="")
    sp.add_argument("--plan-ref", default="", help="测试计划文件路径（溯源）")
    sp.add_argument("--db-config", dest="db_config", default="",
                    help="v2.13：DB 连接配置 JSON（只读快照数据源，run 级溯源）")
    sp.add_argument("--db-checks", dest="db_checks", default="",
                    help="v2.13：DC 契约文件路径（planner 产出的 data-contracts.json）")
    sp.add_argument("--feature-inventory", dest="feature_inventory", default="",
                    help="v2.14：每页功能清单 JSON（planner 产出 page-feature-inventory.json）")

    sp = sub.add_parser("case", help="开始/结束用例")
    sp.add_argument("--run-id", required=True)
    sp.add_argument("--case-id", required=True)
    sp.add_argument("--action", choices=["start", "end"], required=True)
    sp.add_argument("--title", default="")
    sp.add_argument("--priority", default="P1", choices=["P0", "P1", "P2"])
    sp.add_argument("--host", default="auto")
    sp.add_argument("--flows", default="", help="关联 FLOW ID（逗号分隔）")
    sp.add_argument("--datasets", default="", help="关联 DT ID（逗号分隔）")
    sp.add_argument("--status", default="pass",
                    help="end 时的最终状态 pass/fail/blocked/skipped")
    sp.add_argument("--funcs", default="",
                    help="v2.14：关联 FUNC 功能项 ID（逗号分隔；须存在于已登记清单）")
    sp.add_argument("--dcs", default="", help="v2.13：关联 DC 契约 ID（逗号分隔）")
    sp.add_argument("--db-check-before", dest="db_check_before", default="",
                    help="v2.13：操作前快照清单文件（启用则该用例必须走数据校验）")
    sp.add_argument("--db-check-after", dest="db_check_after", default="",
                    help="v2.13：操作后快照清单文件")

    sp = sub.add_parser("step", help="追加步骤记录")
    sp.add_argument("--run-id", required=True)
    sp.add_argument("--case-id", required=True)
    sp.add_argument("--action-name", required=True,
                    help=f"动作: {'/'.join(sorted(VALID_ACTIONS))}")
    sp.add_argument("--target", required=True, help="目标元素定位 / URL")
    sp.add_argument("--dataset", default="", help="使用的数据组 DT-ID")
    sp.add_argument("--funcs", default="",
                    help="v2.14：步骤挂接 FUNC ID（逗号分隔；覆盖判定=FUNC 关联 PASS 断言）")
    sp.add_argument("--duration-ms", type=int, default=0)
    sp.add_argument("--status", default="pass")
    sp.add_argument("--path", default="", help="screenshot 动作的截图路径")
    sp.add_argument("--note", default="")

    sp = sub.add_parser("assert", help="给步骤追加断言")
    sp.add_argument("--run-id", required=True)
    sp.add_argument("--case-id", required=True)
    sp.add_argument("--seq", type=int, default=0, help="目标步骤序号（0=最近一条）")
    sp.add_argument("--kind", required=True,
                    help="断言类型 url/dom/network/console/text/state")
    sp.add_argument("--expected", required=True)
    sp.add_argument("--actual", required=True)
    sp.add_argument("--pass", dest="passed", default="true",
                    help="true/false")

    sp = sub.add_parser("error", help="记录页面/后台错误、疑似缺陷")
    sp.add_argument("--run-id", required=True)
    sp.add_argument("--case-id", required=True)
    sp.add_argument("--source", required=True,
                    help="console=页面 / network=后台 / bug=疑似缺陷")
    sp.add_argument("--message", required=True)
    sp.add_argument("--detail", default="")
    sp.add_argument("--url", default="", help="错误相关 URL/endpoint")
    sp.add_argument("--suspected-bug", dest="suspected_bug", default="false")

    sp = sub.add_parser("shot", help="登记截图")
    sp.add_argument("--run-id", required=True)
    sp.add_argument("--case-id", required=True)
    sp.add_argument("--path", required=True)
    sp.add_argument("--seq", type=int, default=0)
    sp.add_argument("--label", default="")

    sp = sub.add_parser("blocker", help="记录阻碍与解决")
    sp.add_argument("--run-id", required=True)
    sp.add_argument("--case-id", required=True)
    sp.add_argument("--blocker-type", required=True,
                    help=f"{'/'.join(sorted(VALID_BLOCKER_TYPES))}")
    sp.add_argument("--description", required=True)
    sp.add_argument("--resolved", default="false")
    sp.add_argument("--resolution", default="")

    sp = sub.add_parser("verify", help="用例 schema 校验")
    sp.add_argument("--run-id", required=True)
    sp.add_argument("--case-id", default="", help="单用例（与 --all 二选一）")
    sp.add_argument("--all", action="store_true", help="校验全部用例")

    sp = sub.add_parser("db-snapshot", help="v2.12/v2.13：执行只读 DB 快照清单并登记")
    sp.add_argument("--run-id", required=True)
    sp.add_argument("--case-id", required=True)
    sp.add_argument("--check-file", required=True,
                    help="快照清单 JSON（单对象或数组，见 db_probe.run_snapshot_plan）")
    sp.add_argument("--phase", required=True, choices=["before", "after"],
                    help="操作前/操作后（finalize 按 phase 配对做 diff）")
    sp.add_argument("--db-config", dest="db_config", default="",
                    help="DB 连接配置（缺省回退 run-meta.db_config）")
    sp.add_argument("--params", default="",
                    help="参数注入 JSON 对象（替换清单 SQL 中的双花括号占位）")

    sp = sub.add_parser("rule", help="v2.13：声明式业务规则确定性求值并转断言")
    sp.add_argument("--run-id", required=True)
    sp.add_argument("--case-id", required=True)
    sp.add_argument("--rules", required=True, help="规则文件 JSON（rules 数组）")
    sp.add_argument("--seq", type=int, default=0, help="断言挂到步骤序号（0=最近一条）")

    sp = sub.add_parser("dc-check", help="v2.13：before/after 快照 diff 对照 DC 契约")
    sp.add_argument("--run-id", required=True)
    sp.add_argument("--case-id", required=True)
    sp.add_argument("--contract-id", required=True, help="DC 契约 ID")
    sp.add_argument("--expectation", default="",
                    help="内联预期 JSON（缺省读用例 dc_contracts 中同 ID 条目）")
    sp.add_argument("--key-columns", default="", help="行主键列（逗号分隔）")
    sp.add_argument("--before-label", default="",
                    help="before 快照查询 label（缺省用 before 的第一个 label）")
    sp.add_argument("--seq", type=int, default=0, help="断言挂到步骤序号（0=最近一条）")

    sp = sub.add_parser("finalize", help="一致性校验 + 生成汇总报告")
    sp.add_argument("--run-id", required=True)

    # v2.14 defect：跨 run 缺陷台账（{out}/defects.json 追加式历史）
    sp = sub.add_parser("defect",
                        help="v2.14：缺陷台账 add/update/close/audit/reopen/list")
    sp.add_argument("--action", required=True,
                    choices=["add", "update", "close", "audit", "reopen", "list"],
                    help="v2.14：缺陷台账操作")
    sp.add_argument("--defect-id", dest="defect_id", help="DEF-{序号}（update/close/reopen 必填）")
    sp.add_argument("--run-id", dest="run_id", help="add：来源 run")
    sp.add_argument("--case-id", dest="case_id", help="add：来源用例")
    sp.add_argument("--bug-ref", dest="bug_ref", help="来源 bug 在该用例 errors 数组中的序号（1 起）")
    sp.add_argument("--symptom", dest="symptom", help="缺陷现象（一句话）")
    sp.add_argument("--detail", dest="detail", help="复现步骤与现象摘要")
    sp.add_argument("--funcs", dest="funcs", help="影响 FUNC ID（逗号分隔）")
    sp.add_argument("--cases", dest="cases", help="关联用例 TC ID（逗号分隔）")
    sp.add_argument("--severity", dest="severity", help="严重度（见 VALID_DEFECT_SEVERITIES）")
    sp.add_argument("--evidence", dest="evidence", help="证据路径（逗号分隔，须真实存在）")
    sp.add_argument("--commit", dest="commit", help="修复 commit（fixed 关闭强制）")
    sp.add_argument("--fix-summary", dest="fix_summary", help="修复说明")
    sp.add_argument("--regression-run", dest="regression_run", help="回归验证 run（fixed 关闭强制，核对 summary.json 结果）")
    sp.add_argument("--regression-cases", dest="regression_cases", help="回归用例清单（逗号分隔）")
    sp.add_argument("--reason", dest="reason", help="误报关闭 / 回退 open 的书面理由")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    """CLI 主入口：命令 → 处理函数分派"""
    args = build_parser().parse_args(argv)
    handlers = {
        "run": cmd_run, "case": cmd_case, "step": cmd_step,
        "assert": cmd_assert, "error": cmd_error, "shot": cmd_shot,
        "blocker": cmd_blocker, "verify": cmd_verify, "finalize": cmd_finalize,
        "db-snapshot": cmd_db_snapshot, "rule": cmd_rule, "dc-check": cmd_dc_check,
        "defect": cmd_defect,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
