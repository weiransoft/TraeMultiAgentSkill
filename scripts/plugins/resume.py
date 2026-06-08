"""GoalResumePlugin — Phase 13/14 引入的 Goal 续跑功能插件化（priority=20，状态变更）。"""
import argparse
from typing import Set
from plugins.base import GoalCommandPlugin
from dispatcher.plugin_context import PluginContext


class GoalResumePlugin(GoalCommandPlugin):
    """Phase 13/14 引入的 Goal 续跑功能插件化（priority=20，状态变更）。"""

    @property
    def name(self) -> str:
        return "goal-resume"

    @property
    def priority(self) -> int:
        return 20  # STATE_MUTATION_LOW

    @property
    def mutex_with(self) -> Set[str]:
        # Phase 18：与 autonomous 互斥
        return {"goal-cancel", "goal-graph", "multi-goal", "loop", "autonomous"}

    @property
    def requires_task(self) -> bool:
        return False

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "goal_resume", None) is not None

    def execute(self, args: argparse.Namespace, ctx: PluginContext) -> bool:
        from dispatch.legacy import dispatch_agent_v2_with_goal_resume
        ctx.log(
            f"🔄 Phase 16 检测到续跑模式：goal={args.goal_resume}",
            "INFO",
        )
        return dispatch_agent_v2_with_goal_resume(
            goal_id=args.goal_resume,
            project_root=str(ctx.project_root),
        )
