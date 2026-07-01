"""LoopGoalPlugin — Phase 11 引入的 /loop 功能插件化（priority=40，循环/长期运行）。"""

import argparse
from typing import Set
from plugins.base import GoalCommandPlugin
from dispatcher.plugin_context import PluginContext


class LoopGoalPlugin(GoalCommandPlugin):
    """Phase 11 引入的 /loop 功能插件化（priority=40，循环/长期运行）。"""

    @property
    def name(self) -> str:
        return "loop"

    @property
    def priority(self) -> int:
        return 40  # LOOP_LOW

    @property
    def mutex_with(self) -> Set[str]:
        # Phase 18：与 autonomous 互斥
        return {
            "goal-cancel",
            "goal-graph",
            "goal-resume",
            "multi-goal",
            "autonomous",
            "loop-engineering",
        }

    @property
    def requires_task(self) -> bool:
        return False

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "loop", 1) > 1 or getattr(args, "goal", None) is not None

    def execute(self, args: argparse.Namespace, ctx: PluginContext) -> bool:
        from dispatch.legacy import dispatch_agent_v2_with_loop_goal

        ctx.log(
            f"🔁 Phase 16 检测到 /loop 模式：loop_count={getattr(args, 'loop', 1)}",
            "INFO",
        )
        return dispatch_agent_v2_with_loop_goal(
            agent_type=getattr(args, "agent", "auto"),
            task=args.task or "",
            project_root=str(ctx.project_root),
            loop_count=getattr(args, "loop", 1),
            goal_id=getattr(args, "goal", None),
            goal_desc=getattr(args, "goal_desc", None),
            criteria=getattr(args, "criteria", None),
            convergence_window=getattr(args, "convergence_window", 3),
        )
