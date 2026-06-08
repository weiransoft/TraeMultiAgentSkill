"""GoalCancelPlugin — Phase 14 引入的 Goal 取消功能插件化（priority=0，破坏性最高）。"""
import argparse
from typing import Set
from plugins.base import GoalCommandPlugin
from dispatcher.plugin_context import PluginContext


class GoalCancelPlugin(GoalCommandPlugin):
    """Phase 14 引入的 Goal 取消功能插件化（priority=0，破坏性最高）。"""

    @property
    def name(self) -> str:
        return "goal-cancel"

    @property
    def priority(self) -> int:
        return 0  # 最高优先级

    @property
    def mutex_with(self) -> Set[str]:
        # cancel 与所有其他 plugin 互斥
        # Phase 18：与 autonomous 互斥
        return {"goal-graph", "goal-resume", "multi-goal", "loop", "autonomous"}

    @property
    def requires_task(self) -> bool:
        return False

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "goal_cancel", None) is not None

    def execute(self, args: argparse.Namespace, ctx: PluginContext) -> bool:
        # B-3 修复：从 dispatch.legacy 导入（不再 from goal_orchestrator）
        from dispatch.legacy import dispatch_agent_v2_with_goal_cancel
        ctx.log(
            f"🛑 Phase 16 检测到取消模式：goal={args.goal_cancel}",
            "INFO",
        )
        return dispatch_agent_v2_with_goal_cancel(
            goal_id=args.goal_cancel,
            project_root=str(ctx.project_root),
        )
