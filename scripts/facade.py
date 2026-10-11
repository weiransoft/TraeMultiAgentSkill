"""V3 兼容层：保持旧 API 100% 工作。

设计原则：
- 完整 re-export 11 个旧符号（B-2 修复：不是 5 个）
- 旧 CLI 入口（main_compat）继续可用
- 旧 import 路径（from trae_agent_dispatch_v2 import ...）通过薄壳 re-export 工作
- 风险-2 修正：恢复与 god module 同等 6 模式豁免的 --task 必填校验
- 风险-5 修正：dry_run 短路在 dispatcher 内部实现（PluginContext.dry_run 字段驱动）
- Phase 17：_start_hot_reload_if_enabled（v3 §2.9）— 启动 HotReloadWatcher
  + atexit 清理 + weakref 防重复（v3 P1-8）
"""

import atexit
import logging
import weakref
from pathlib import Path
from threading import RLock
from typing import Optional

# 1. re-export 11 个符号（B-2 完整列表）
from dispatch.legacy import (  # noqa: F401
    log,  # 多处 test 引用
    dispatch_agent_v2,  # 2 处 test
    dispatch_agent,  # plan 列入
    dispatch_agent_v2_with_loop_goal,  # 4 处 test
    dispatch_agent_v2_with_goal_resume,  # 1 处 test
    dispatch_agent_v2_with_multi_goal,  # 1 处 test
    dispatch_agent_v2_with_goal_cancel,  # 1 处 test
    dispatch_agent_v2_with_goal_graph,  # 4 处 dag_visualizer_integration test
    _is_overall_success,  # 3 处 test_loop_goal
    _module_level_single_dispatch,  # 2 处 test_goal_orchestrator
)
from cli.parser import parse_arguments  # 4 处 test 引用  # noqa: F401


# Phase 17 v3 P1-8：模块级跟踪所有已启动的 watcher（weakref 防泄漏）
_watcher_refs: "set[weakref.ref]" = set()
_watcher_tracking_lock: RLock = RLock()
_facade_logger: logging.Logger = logging.getLogger("facade")


# 2. 旧 main 入口（保证 19 处外部 import 站点继续工作）
def main_compat() -> int:
    """兼容旧 main() — 走新 dispatcher 路径。

    Returns:
        int: 进程退出码（0 = 成功，1 = 失败）
    """
    args = parse_arguments()
    return _dispatch_through_v3(args)


def _dispatch_through_v3(args) -> int:
    """通过 V3 dispatcher 执行（与旧 main() 行为一致）。

    风险-2 修正：恢复与 god module 同等 6 模式豁免的 --task 必填校验
    风险-5 修正：dry_run 短路在 dispatcher 内部实现（ctx.dry_run 字段）

    Args:
        args: argparse.Namespace

    Returns:
        int: 进程退出码
    """
    from dispatcher.goal_dispatcher import GoalDispatcher
    from dispatcher.plugin_context import PluginContext
    from dispatcher.errors import MutexViolationError
    from plugins import BUILTIN_PLUGINS

    # 项目根目录校验（与 god module line 1317-1322 行为一致）
    project_root = Path(args.project_root).resolve()
    if not project_root.exists():
        log(f"❌ 项目根目录不存在：{project_root}", "ERROR")
        return 1

    # 构建 PluginContext（风险-5 修正：dry_run 由 dispatcher 内部检查）
    ctx = PluginContext(
        project_root=project_root,
        log=log,
        dry_run=getattr(args, "dry_run", False),
        verbose=getattr(args, "verbose", False),
        agent_type=getattr(args, "agent", "auto"),
    )

    # 互斥预校验（替代 god module line 1326-1334 的硬编码 if-elif 链）
    dispatcher = GoalDispatcher(plugins=list(BUILTIN_PLUGINS))
    try:
        dispatcher.validate_mutex(args)
    except MutexViolationError as e:
        log(f"❌ {e}", "ERROR")
        return 1

    # v2.10 新增：proposal-review 系列 flag 的最小路由和互斥校验
    proposal_mode = getattr(args, "proposal_review", False)
    research_only = getattr(args, "industry_research_only", False)
    review_only = getattr(args, "proposal_review_only", False)

    # proposal-review 与 --autonomous / --loop / --multi-goal 互斥校验
    proposal_flags_present = proposal_mode or research_only or review_only
    if proposal_flags_present:
        if getattr(args, "autonomous", False):
            log("❌ --proposal-review 系列标志与 --autonomous 互斥，不能同时使用", "ERROR")
            return 1
        if args.loop and args.loop > 1:
            log("❌ --proposal-review 系列标志与 --loop 互斥，不能同时使用", "ERROR")
            return 1
        if getattr(args, "multi_goal", None):
            log("❌ --proposal-review 系列标志与 --multi-goal 互斥，不能同时使用", "ERROR")
            return 1

        # v2.10：proposal-review 是提示词层工作流（宿主 LLM 执行五阶段）
        # 脚本层仅做最小拦截，输出工作流指令供宿主 LLM 接管
        if proposal_mode or research_only or review_only:
            _handle_proposal_review_mode(args, project_root, proposal_mode, research_only, review_only)
            return 0

    # Phase 17 v3 §2.9：启动 hot reload watcher（如启用）
    # 关键：放在 validate_mutex 之后（避免无效 args 启动 watcher）
    _start_hot_reload_if_enabled(dispatcher, args, project_root)

    # 风险-2 修正：--task 必填校验（与 god module line 1339-1348 行为一致）
    # 排除模式：goal_graph / goal_cancel / goal_resume / multi_goal /
    #          loop > 1 / goal 不为 None
    if not args.task and not (
        args.goal_graph
        or args.goal_cancel
        or args.goal_resume
        or args.multi_goal
        or args.loop > 1
        or args.goal is not None
        or getattr(args, "loop_engineering", False)
    ):
        log(
            "❌ --task 必填（除非使用 --goal-graph / --goal-cancel / "
            "--goal-resume / --multi-goal / --loop / --goal / "
            "--loop-engineering 模式）",
            "ERROR",
        )
        return 1

    # 任务文件校验（与 god module line 1351-1359 行为一致）
    if getattr(args, "task_file", None):
        task_file = project_root / args.task_file
        if not task_file.exists():
            log(f"❌ 任务文件不存在：{task_file}", "ERROR")
            return 1

    # 调度（V3 dispatcher 入口）
    result = dispatcher.dispatch(args, ctx)

    if result.skipped_reason == "dry_run":
        # 风险-5 修正：dispatcher 内部 dry_run 短路
        log("🔄 模拟模式：不实际调用智能体", "WARNING")
        log(f"   将调度智能体：{args.agent}", "WARNING")
        log(f"   任务：{args.task}", "WARNING")
        log("✅ 模拟完成", "SUCCESS")
        return 0

    if result.skipped_reason == "no_match":
        # 无插件匹配 → 默认 dispatch_agent_v2（与 god module line 1446-1453 行为一致）
        success = dispatch_agent_v2(
            agent_type=args.agent,
            task=args.task,
            project_root=str(project_root),
        )
        return 0 if success else 1

    # 有 plugin 匹配：result.success 决定退出码
    return 0 if result.success else 1


# === Phase 17 v3 §2.9：hot reload watcher 启动 + 清理 ===


def _start_hot_reload_if_enabled(
    dispatcher: "object",  # GoalDispatcher
    args,
    project_root: Path,
) -> "Optional[object]":  # Optional[HotReloadWatcher]
    """Phase 17 v3：根据 args 启动 hot reload watcher。

    行为：
    - args.hot_reload == False → 不启动，返回 None
    - args.hot_reload == True → 启动 watcher + atexit 注册清理
    - args.hot_reload is None → assert 兜底（v3 P1-5）

    Args:
        dispatcher: 已构造的 GoalDispatcher
        args: parse_arguments() 结果
        project_root: 项目根目录（Path；用于 drop-in 路径安全校验）

    Returns:
        HotReloadWatcher 实例 or None
    """
    # v3 P1-5 兜底
    enabled = getattr(args, "hot_reload", None)
    assert enabled is not None, (
        "args.hot_reload is None — parser 解析异常，"
        "请检查 --hot-reload/--no-hot-reload 配置"
    )
    if not enabled:
        _facade_logger.info("[facade] hot reload 显式禁用（--no-hot-reload）")
        return None

    # v3 P0-7 第三层：facade 串联（即使 parser 漏过 + watcher 兜底）
    # 注解：args.hot_reload_dir 已被 parser type 校验（CLI 第一层）
    drop_in_dir = Path(getattr(args, "hot_reload_dir", "plugins_extra"))
    poll_interval = float(getattr(args, "hot_reload_interval", 5.0))

    try:
        # 延迟 import：避免 HotReloadWatcher 自身 import 异常阻断 facade 加载
        from dispatcher.hot_reload_watcher import HotReloadWatcher

        watcher = HotReloadWatcher(
            dispatcher=dispatcher,
            drop_in_dir=drop_in_dir,
            project_root=project_root,
            poll_interval=poll_interval,
        )
    except Exception as e:
        # watcher 构造失败不阻断 main 流程（生产友好）
        _facade_logger.error(f"[facade] watcher 启动失败：{e}")
        return None

    # 启动 + 等待首次扫描完成（v3 P0-4：避免 dispatch 早于首次扫描）
    watcher.start()
    if not watcher.wait_initial_scan(timeout=30.0):
        _facade_logger.warning(
            "[facade] watcher 初始扫描 30s 超时（drop-in 目录异常大？）"
        )

    # v3 P1-8：weakref 跟踪 + atexit 注册（多 dispatcher 防重复）
    with _watcher_tracking_lock:
        _watcher_refs.add(weakref.ref(watcher, _watcher_refs.discard))
        # 只对第一个 watcher 注册 atexit（避免重复 cleanup）
        if len(_watcher_refs) == 1:
            atexit.register(_cleanup_all_watchers)
    return watcher


def _cleanup_all_watchers() -> None:
    """Phase 17 v3 P1-8：atexit hook，清理所有活跃 watcher。"""
    with _watcher_tracking_lock:
        refs = list(_watcher_refs)
    for ref in refs:
        watcher = ref()
        if watcher is not None:
            _safe_watcher_stop(watcher)


def _safe_watcher_stop(watcher) -> None:
    """Phase 17 v3 P1-7：异常隔离的 stop，atexit 不能抛异常。"""
    try:
        watcher.stop(timeout=5.0)
    except Exception as e:
        _facade_logger.warning(f"[facade] watcher.stop 异常：{e}")


# === v2.10：方案撰写全流程 Proposal Review 工作流处理 ===


def _handle_proposal_review_mode(
    args,
    project_root: Path,
    proposal_mode: bool,
    research_only: bool,
    review_only: bool,
) -> None:
    """v2.10：处理 proposal-review 系列 flag。

    此工作流是提示词层执行的——宿主 LLM 负责按 SKILL.md 的五阶段定义
    执行业界调研→方案撰写→多角色交叉评审→共识收敛→闭环修改。
    脚本层仅做最小拦截，输出结构化指令供宿主 LLM 接管。

    Args:
        args: argparse.Namespace — CLI 解析结果
        project_root: Path — 项目根目录
        proposal_mode: bool — 完整五阶段工作流
        research_only: bool — 仅 Phase 0 业界调研
        review_only: bool — 仅 Phase 2-3 多角色评审
    """
    task_desc = args.task or "(未指定任务)"
    max_iterations = getattr(args, "proposal_max_iterations", 3)
    consensus_threshold = getattr(args, "proposal_consensus_threshold", 2)
    proposal_doc = getattr(args, "proposal_doc", None)

    log("=" * 60, "INFO")
    log("📋 v2.10 方案撰写全流程（Proposal Review Workflow）已启动", "INFO")
    log("=" * 60, "INFO")
    log(f"📌 项目根目录: {project_root}", "INFO")
    log(f"📌 任务描述: {task_desc}", "INFO")

    # 根据模式输出对应的工作流指令
    if research_only:
        log("\n【模式】仅执行 Phase 0：业界最佳实践调研", "INFO")
        log("=" * 40, "INFO")
        log("📖 宿主 LLM 执行指令：", "INFO")
        log("  请读取 docs/spec/role-prompts/industry-research-prompt.md", "INFO")
        log("  按其中定义的五步骤执行业界调研：", "INFO")
        log("    Step 1 - 关键词提取（从任务描述中提取核心技术关键词）", "INFO")
        log("    Step 2 - 多路径并行搜索（WebSearch + 浏览器深度阅读）", "INFO")
        log("    Step 3 - 信息交叉验证（至少 2-3 个独立来源确认）", "INFO")
        log("    Step 4 - 结构化整理（主流方案对比矩阵 + 项目适配分析）", "INFO")
        log("    Step 5 - 产出调研报告（docs/proposals/{task-id}/industry-research.md）", "INFO")
        log("", "INFO")
        log("  ⚠️ 强制约束：所有结论必须有真实来源支撑（禁止凭空生成）", "WARNING")

    elif review_only:
        log("\n【模式】仅执行 Phase 2-3：多角色交叉评审 + 共识收敛", "INFO")
        log("=" * 40, "INFO")

        if not proposal_doc:
            log("❌ --proposal-review-only 需要配合 --proposal-doc 指定待评审方案路径", "ERROR")
            return

        proposal_path = Path(proposal_doc)
        if not proposal_path.exists():
            log(f"❌ 待评审方案文档不存在: {proposal_path}", "ERROR")
            return

        log(f"📖 待评审方案: {proposal_path}", "INFO")
        log("📖 宿主 LLM 执行指令：", "INFO")
        log("  Phase 2 - 4 角色独立评审（必须扫描项目最新代码）", "INFO")
        log("    角色 1: 架构师 — docs/spec/role-prompts/proposal-architect-review.md", "INFO")
        log("    角色 2: 产品经理 — docs/spec/role-prompts/proposal-pm-review.md", "INFO")
        log("    角色 3: 测试专家 — docs/spec/role-prompts/proposal-test-review.md", "INFO")
        log("    角色 4: 独立开发者 — docs/spec/role-prompts/proposal-coder-review.md", "INFO")
        log("", "INFO")
        log("  Phase 3 - 共识收敛", "INFO")
        log(f"    共识阈值: ≥ {consensus_threshold} 个角色同时提出 = 共识问题", "INFO")
        log("", "INFO")
        log("  ⚠️ 强制约束：每个评审必须扫描项目最新代码并引用具体文件路径/行号", "WARNING")

    else:  # proposal_mode（完整五阶段）
        log("\n【模式】完整五阶段方案撰写全流程", "INFO")
        log("=" * 40, "INFO")
        log(f"📖 宿主 LLM 执行指令（按 SKILL.md 「方案撰写全流程」章节）：", "INFO")
        log("", "INFO")
        log("  Phase 0 — 业界调研（真实执行，禁止凭空生成）", "INFO")
        log("    Prompt: docs/spec/role-prompts/industry-research-prompt.md", "INFO")
        log("    工具: WebSearch 多路并行搜索 + 浏览器插件深度阅读", "INFO")
        log("", "INFO")
        log("  Phase 1 — 方案撰写（结合调研 + 项目代码）", "INFO")
        log("    必须扫描项目最新代码，引用业界调研结论，定义清晰验收标准", "INFO")
        log("", "INFO")
        log("  Phase 2 — 多角色交叉评审（4 角色并行）", "INFO")
        log("    架构师 / 产品经理 / 测试专家 / 独立开发者", "INFO")
        log("    每个角色必须扫描项目最新代码并引用具体文件路径/行号", "INFO")
        log("", "INFO")
        log("  Phase 3 — 共识收敛", "INFO")
        log(f"    共识阈值: ≥ {consensus_threshold} 个角色同时提出 = 共识问题", "INFO")
        log("", "INFO")
        log("  Phase 4 — 根据共识修改 + 闭环判定", "INFO")
        log(f"    最大迭代次数: {max_iterations}（超过则输出当前方案交由用户裁决）", "INFO")
        log("", "INFO")
        log("  ⚠️ 强制约束清单（v2.10 绝对禁止）：", "WARNING")
        log("    ❌ 跳过业界调研直接开始写方案", "WARNING")
        log("    ❌ 凭空捏造业界最佳实践", "WARNING")
        log("    ❌ 评审时不结合项目代码", "WARNING")
        log("    ❌ 跳过共识收敛直接修改", "WARNING")
        log("    ❌ 无限循环不收敛", "WARNING")

    log("", "INFO")
    log("✅ 工作流指令已输出，宿主 LLM 请按以上指令执行。", "SUCCESS")
    log("   详细说明见 SKILL.md 「方案撰写全流程」章节", "INFO")


__all__ = [
    "main_compat",
    "_dispatch_through_v3",
    "log",
    "dispatch_agent_v2",
    "dispatch_agent",
    "dispatch_agent_v2_with_loop_goal",
    "dispatch_agent_v2_with_goal_resume",
    "dispatch_agent_v2_with_multi_goal",
    "dispatch_agent_v2_with_goal_cancel",
    "dispatch_agent_v2_with_goal_graph",
    "_is_overall_success",
    "_module_level_single_dispatch",
    "parse_arguments",
    # Phase 17 v3 新增：hot reload 集成
    "_start_hot_reload_if_enabled",
    "_cleanup_all_watchers",
    "_safe_watcher_stop",
    # v2.10 新增：方案撰写全流程
    "_handle_proposal_review_mode",
]
