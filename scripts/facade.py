"""V3 兼容层：保持旧 API 100% 工作。

设计原则：
- 完整 re-export 11 个旧符号（B-2 修复：不是 5 个）
- 旧 CLI 入口（main_compat）继续可用
- 旧 import 路径（from trae_agent_dispatch_v2 import ...）通过薄壳 re-export 工作
- 风险-2 修正：恢复与 god module 同等 6 模式豁免的 --task 必填校验
- 风险-5 修正：dry_run 短路在 dispatcher 内部实现（PluginContext.dry_run 字段驱动）
"""
import sys
from pathlib import Path

# 1. re-export 11 个符号（B-2 完整列表）
from dispatch.legacy import (  # noqa: F401
    log,                            # 多处 test 引用
    dispatch_agent_v2,              # 2 处 test
    dispatch_agent,                 # plan 列入
    dispatch_agent_v2_with_loop_goal,         # 4 处 test
    dispatch_agent_v2_with_goal_resume,       # 1 处 test
    dispatch_agent_v2_with_multi_goal,        # 1 处 test
    dispatch_agent_v2_with_goal_cancel,       # 1 处 test
    dispatch_agent_v2_with_goal_graph,        # 4 处 dag_visualizer_integration test
    _is_overall_success,            # 3 处 test_loop_goal
    _module_level_single_dispatch,  # 2 处 test_goal_orchestrator
)
from cli.parser import parse_arguments  # 4 处 test 引用  # noqa: F401


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
        dry_run=getattr(args, 'dry_run', False),
        verbose=getattr(args, 'verbose', False),
        agent_type=getattr(args, 'agent', 'auto'),
    )

    # 互斥预校验（替代 god module line 1326-1334 的硬编码 if-elif 链）
    dispatcher = GoalDispatcher(plugins=list(BUILTIN_PLUGINS))
    try:
        dispatcher.validate_mutex(args)
    except MutexViolationError as e:
        log(f"❌ {e}", "ERROR")
        return 1

    # 风险-2 修正：--task 必填校验（与 god module line 1339-1348 行为一致）
    # 排除模式：goal_graph / goal_cancel / goal_resume / multi_goal /
    #          loop > 1 / goal 不为 None
    if not args.task and not (
        args.goal_graph or args.goal_cancel or args.goal_resume
        or args.multi_goal or args.loop > 1 or args.goal is not None
    ):
        log(
            "❌ --task 必填（除非使用 --goal-graph / --goal-cancel / "
            "--goal-resume / --multi-goal / --loop / --goal 模式）",
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
        log('🔄 模拟模式：不实际调用智能体', 'WARNING')
        log(f'   将调度智能体：{args.agent}', 'WARNING')
        log(f'   任务：{args.task}', 'WARNING')
        log('✅ 模拟完成', 'SUCCESS')
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
]
