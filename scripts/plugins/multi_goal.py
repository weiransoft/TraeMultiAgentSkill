"""MultiGoalPlugin — Phase 13 引入的多 Goal 编排功能插件化（priority=30，状态变更）。"""

import argparse
from typing import Set
from plugins.base import GoalCommandPlugin
from dispatcher.plugin_context import PluginContext


class MultiGoalPlugin(GoalCommandPlugin):
    """Phase 13 引入的多 Goal 编排功能插件化（priority=30，状态变更）。"""

    @property
    def name(self) -> str:
        return "multi-goal"

    @property
    def priority(self) -> int:
        return 30  # STATE_MUTATION_HIGH

    @property
    def mutex_with(self) -> Set[str]:
        # Phase 18：与 autonomous 互斥
        # Loop Engineering：与 loop-engineering 互斥
        return {
            "goal-cancel",
            "goal-graph",
            "goal-resume",
            "loop",
            "autonomous",
            "loop-engineering",
        }

    @property
    def requires_task(self) -> bool:
        return False

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "multi_goal", None) is not None

    def execute(self, args: argparse.Namespace, ctx: PluginContext) -> bool:
        from dispatch.legacy import dispatch_agent_v2_with_multi_goal

        ctx.log(
            f"🎯 Phase 16 检测到多 Goal 编排模式：goals={args.multi_goal}",
            "INFO",
        )
        return dispatch_agent_v2_with_multi_goal(
            root_goal_id=args.multi_goal,
            project_root=str(ctx.project_root),
        )
