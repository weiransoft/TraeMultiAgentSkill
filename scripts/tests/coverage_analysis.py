"""Phase 17 测试覆盖度分析脚本。

功能：
1. 解析 coverage.json
2. 按模块 / 阶段分组计算覆盖度
3. 输出分级统计报告

用法：
    coverage3 run ... && python3 tests/coverage_analysis.py
"""
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple


def load_coverage(json_path: Path) -> dict:
    """加载 coverage.py 生成的 JSON 报告。"""
    with open(json_path) as f:
        return json.load(f)


def categorize_file(filename: str) -> str:
    """根据文件路径分类到阶段。"""
    name = Path(filename).name
    if name in ("__init__.py",) and "dispatcher" in filename:
        return "dispatcher-core"
    if "dispatcher/" in filename:
        return "dispatcher-core"
    if "plugins/" in filename:
        return "plugins"
    if "dispatch/legacy.py" in filename:
        return "dispatch-legacy"
    if "cli/parser.py" in filename:
        return "cli"
    if "facade.py" in filename:
        return "facade"
    if "trae_agent_dispatch_v2.py" in filename:
        return "compat-shell"
    if name == "test_ai_components.py" or name == "test_v2_components.py":
        return "test-scripts-root"  # 这些是根目录的 test 脚本，不计入核心
    return "other"


def aggregate_by_category(
    coverage: dict,
) -> Dict[str, Dict[str, int]]:
    """按分类聚合覆盖度统计。"""
    files = coverage["files"]
    categories: Dict[str, Dict[str, int]] = {}

    for fname, data in files.items():
        cat = categorize_file(fname)
        if cat not in categories:
            categories[cat] = {
                "stmts": 0, "miss": 0, "branches": 0, "miss_branches": 0,
                "files": 0, "covered_files": 0
            }
        stmts = data["summary"]["num_statements"]
        miss = data["summary"]["missing_lines"]
        # branches
        branch_info = data.get("summary", {}).get("num_branches", 0)
        miss_branch = data.get("summary", {}).get("missing_branches", 0)
        # 兼容 coverage.py v7 格式
        if "num_branches" not in data["summary"]:
            # 尝试从 missing 字段推断
            branch_info = 0
            miss_branch = 0
        categories[cat]["stmts"] += stmts
        categories[cat]["miss"] += miss
        categories[cat]["branches"] += branch_info
        categories[cat]["miss_branches"] += miss_branch
        categories[cat]["files"] += 1
        if miss == 0:
            categories[cat]["covered_files"] += 1

    return categories


def compute_percent(stmts: int, miss: int) -> float:
    if stmts == 0:
        return 0.0
    return round((stmts - miss) / stmts * 100, 1)


def main():
    json_path = Path(__file__).parent / "coverage.json"
    if not json_path.exists():
        print(f"❌ 找不到 {json_path}，请先运行 coverage3 json")
        sys.exit(1)

    coverage = load_coverage(json_path)
    categories = aggregate_by_category(coverage)

    # 总览
    total = coverage["totals"]
    total_stmts = total["num_statements"]
    total_miss = total["missing_lines"]
    total_branches = total.get("num_branches", 0)
    total_miss_branches = total.get("missing_branches", 0)
    total_pct = compute_percent(total_stmts, total_miss)
    total_branch_pct = compute_percent(
        total_branches, total_miss_branches
    ) if total_branches else 0.0

    print("=" * 78)
    print("📊 项目代码测试覆盖度统计报告 (Phase 17)")
    print("=" * 78)

    print(f"\n【总体统计】")
    print(f"  测试文件数：13")
    print(f"  源代码文件数：{len(coverage['files'])}")
    print(f"  总语句数：{total_stmts}")
    print(f"  已覆盖：{total_stmts - total_miss} ({total_pct}%)")
    print(f"  未覆盖：{total_miss}")
    if total_branches:
        print(f"  分支覆盖：{total_branches - total_miss_branches}/{total_branches} ({total_branch_pct}%)")

    # 按分类输出
    print(f"\n【按阶段 / 模块分类】")
    print(f"  {'分类':<25} {'文件':>5} {'100%':>5} {'语句':>8} {'覆盖':>8} {'行覆盖':>8}")
    print(f"  {'-'*25} {'-'*5} {'-'*5} {'-'*8} {'-'*8} {'-'*8}")

    # 按覆盖率排序
    sorted_cats = sorted(
        categories.items(),
        key=lambda x: compute_percent(x[1]['stmts'], x[1]['miss']),
        reverse=True,
    )
    for cat, data in sorted_cats:
        pct = compute_percent(data["stmts"], data["miss"])
        covered_pct = round(
            data["covered_files"] / data["files"] * 100, 1
        ) if data["files"] else 0
        print(
            f"  {cat:<25} {data['files']:>5} {data['covered_files']:>5} "
            f"{data['stmts']:>8} {data['stmts']-data['miss']:>8} {pct:>7}%"
        )

    # 核心模块详情（Phase 17 + V3 架构）
    print(f"\n【核心架构模块（Phase 17 / V3 plugin architecture）】")
    print(f"  {'文件':<35} {'语句':>5} {'覆盖':>5} {'覆盖率':>8}")
    print(f"  {'-'*35} {'-'*5} {'-'*5} {'-'*8}")
    core_files_priority = [
        "dispatcher/goal_dispatcher.py",
        "dispatcher/hot_reload_watcher.py",
        "dispatcher/drop_in_loader.py",
        "dispatcher/reload_guard.py",
        "dispatcher/plugin_context.py",
        "dispatcher/errors.py",
        "dispatcher/dispatch_result.py",
        "dispatcher/middleware.py",
        "facade.py",
        "cli/parser.py",
        "dispatch/legacy.py",
        "plugins/__init__.py",
        "plugins/base.py",
        "plugins/cancel.py",
        "plugins/graph.py",
        "plugins/loop.py",
        "plugins/multi_goal.py",
        "plugins/resume.py",
        "trae_agent_dispatch_v2.py",
    ]
    for fname in core_files_priority:
        # 找到匹配的文件
        for actual_fname, data in coverage["files"].items():
            if actual_fname.endswith(fname):
                stmts = data["summary"]["num_statements"]
                miss = data["summary"]["missing_lines"]
                pct = compute_percent(stmts, miss)
                status = "✅" if pct >= 90 else ("⚠️" if pct >= 70 else "❌")
                print(
                    f"  {status} {actual_fname:<33} {stmts:>5} "
                    f"{stmts-miss:>5} {pct:>7}%"
                )
                break

    # 低覆盖模块（< 50%）
    print(f"\n【低覆盖模块（< 50% 且语句 ≥ 100）需要补充测试】")
    print(f"  {'文件':<50} {'语句':>5} {'覆盖率':>8}")
    print(f"  {'-'*50} {'-'*5} {'-'*8}")
    low_cov = []
    for fname, data in coverage["files"].items():
        stmts = data["summary"]["num_statements"]
        miss = data["summary"]["missing_lines"]
        if stmts < 100:
            continue
        pct = compute_percent(stmts, miss)
        if pct < 50:
            low_cov.append((fname, stmts, miss, pct))
    low_cov.sort(key=lambda x: x[3])
    for fname, stmts, miss, pct in low_cov:
        print(
            f"  ❌ {fname:<48} {stmts:>5} {pct:>7}%"
        )
    if not low_cov:
        print("  ✅ 无低覆盖模块")

    # 总结
    print(f"\n【总结】")
    core_modules = [
        "dispatcher/goal_dispatcher.py",
        "dispatcher/hot_reload_watcher.py",
        "dispatcher/drop_in_loader.py",
        "dispatcher/reload_guard.py",
        "dispatcher/plugin_context.py",
        "dispatcher/errors.py",
        "dispatcher/dispatch_result.py",
        "dispatcher/middleware.py",
        "facade.py",
        "cli/parser.py",
        "plugins/cancel.py",
        "plugins/graph.py",
        "plugins/loop.py",
        "plugins/multi_goal.py",
        "plugins/resume.py",
    ]
    core_stmts = 0
    core_miss = 0
    for fname in core_modules:
        for actual_fname, data in coverage["files"].items():
            if actual_fname.endswith(fname):
                core_stmts += data["summary"]["num_statements"]
                core_miss += data["summary"]["missing_lines"]
                break
    core_pct = compute_percent(core_stmts, core_miss)
    print(f"  核心 V3 plugin 架构模块：{core_pct}% "
          f"({core_stmts - core_miss}/{core_stmts} 语句)")
    print(f"  总体项目覆盖度：{total_pct}% "
          f"({total_stmts - total_miss}/{total_stmts} 语句)")

    print("\n📄 HTML 报告：tests/coverage_html/index.html")
    print("📄 JSON 报告：tests/coverage.json")
    print("=" * 78)


if __name__ == "__main__":
    main()
