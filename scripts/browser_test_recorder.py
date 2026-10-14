#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
浏览器测试执行记录器（v2.12）

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
  7. blocker —— 记录阻碍及解决过程（选择器修复/数据补造/宿主降级等）
  8. verify  —— 校验单个用例文件与目录结构的 schema 合规性
  9. finalize—— 全量一致性校验（summary 与逐条 case 必须吻合）
                 → 生成 summary.json + summary.md（通过率门禁）

设计原则:
  - 失败安全: 记录器自身的单次调用失败不影响测试执行主流程
  - schema 强制: 任何缺必填字段的写入直接报错退出（非零码），杜绝残缺记录
  - 幂等安全: 同一 run_id 重复 run 仅补写缺失的 meta，不清空已有记录
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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
        print(json.dumps({"status": "exists", "run_dir": str(run_dir),
                          "run_id": meta.get("run_id")}, ensure_ascii=False))
        return 0
    for sub in ("cases", "screenshots", "visual-diff"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    meta = {
        "run_id": args.run_id,
        "created_at": _now_iso(),
        "target_url": args.url,
        "hosts": hosts,
        "env": args.env,
        "note": args.note or "",
        "plan_ref": args.plan_ref or "",
        "cases": {},  # TC-ID -> {status, steps, assertions_total, assertions_failed, errors}
    }
    for h in meta["hosts"]:
        if h not in VALID_HOSTS:
            _fail(f"非法宿主: {h}（合法: {sorted(VALID_HOSTS)}）")
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
            "linked_flows": [s for s in (args.flows or "").split(",") if s],
            "linked_datasets": [s for s in (args.datasets or "").split(",") if s],
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
    a_total = sum(len(s["assertions"]) for s in case["steps"])
    a_failed = sum(1 for s in case["steps"] for a in s["assertions"] if not a["pass"])
    meta["cases"][args.case_id] = {
        "status": args.status,
        "priority": case["priority"],
        "steps": len(case["steps"]),
        "assertions_total": a_total,
        "assertions_failed": a_failed,
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
    if args.kind not in {"url", "dom", "network", "console", "text", "state"}:
        _fail(f"非法断言类型: {args.kind}")
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
        for err in case.get("errors", []):
            if err["source"] == "console":
                stats["console_errors"] += 1
            elif err["source"] == "network":
                stats["network_errors"] += 1
            if err.get("suspected_bug"):
                stats["suspected_bugs"] += 1
                bug_list.append({"case_id": cid, **err})
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
    gate = "PASS" if (stats["fail"] == 0 and stats["blocked"] == 0
                      and p0["fail"] == 0 and p0["blocked"] == 0
                      and p0["total"] > 0) else "FAIL"
    summary = {
        "run_id": meta["run_id"],
        "finalized_at": _now_iso(),
        "target_url": meta.get("target_url", ""),
        "hosts": meta.get("hosts", []),
        "gate": gate,
        "p0": p0,
        "stats": stats,
        "suspected_bugs": bug_list,
        "unresolved_blockers": blocker_list,
        "cases": meta["cases"],
    }
    _dump_json(run_dir / "summary.json", summary)
    (run_dir / "summary.md").write_text(_render_summary_md(summary), encoding="utf-8")
    print(json.dumps({"status": "finalized", "gate": gate,
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
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """构建子命令解析器（run/case/step/assert/error/shot/blocker/verify/finalize）"""
    p = argparse.ArgumentParser(
        prog="browser_test_recorder.py",
        description="v2.12 浏览器测试执行记录器：步骤/断言/错误/阻碍/schema 校验/汇总报告")
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

    sp = sub.add_parser("step", help="追加步骤记录")
    sp.add_argument("--run-id", required=True)
    sp.add_argument("--case-id", required=True)
    sp.add_argument("--action-name", required=True,
                    help=f"动作: {'/'.join(sorted(VALID_ACTIONS))}")
    sp.add_argument("--target", required=True, help="目标元素定位 / URL")
    sp.add_argument("--dataset", default="", help="使用的数据组 DT-ID")
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

    sp = sub.add_parser("finalize", help="一致性校验 + 生成汇总报告")
    sp.add_argument("--run-id", required=True)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    """CLI 主入口：命令 → 处理函数分派"""
    args = build_parser().parse_args(argv)
    handlers = {
        "run": cmd_run, "case": cmd_case, "step": cmd_step,
        "assert": cmd_assert, "error": cmd_error, "shot": cmd_shot,
        "blocker": cmd_blocker, "verify": cmd_verify, "finalize": cmd_finalize,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
