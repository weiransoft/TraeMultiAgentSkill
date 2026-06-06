"""GoalGraphPlugin — Phase 15 引入的 DAG 可视化插件化（priority=10，只读）。"""
import argparse
from typing import Set
from plugins.base import GoalCommandPlugin
from dispatcher.plugin_context import PluginContext


class GoalGraphPlugin(GoalCommandPlugin):
    """Phase 15 引入的 DAG 可视化插件化（priority=10，只读）。"""

    @property
    def name(self) -> str:
        return "goal-graph"

    @property
    def priority(self) -> int:
        return 10  # READONLY

    @property
    def mutex_with(self) -> Set[str]:
        return {"goal-cancel", "goal-resume", "multi-goal", "loop"}

    @property
    def requires_task(self) -> bool:
        return False

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "goal_graph", None) is not None

    def execute(self, args: argparse.Namespace, ctx: PluginContext) -> bool:
        from dispatch.legacy import dispatch_agent_v2_with_goal_graph
        ctx.log(
            f"📊 Phase 16 检测到 DAG 可视化模式：root={args.goal_graph}",
            "INFO",
        )
        return dispatch_agent_v2_with_goal_graph(
            root_goal_id=args.goal_graph,
            project_root=str(ctx.project_root),
            format=getattr(args, "goal_graph_format", "mermaid"),
            output_file=getattr(args, "goal_graph_output", None),
            desc_max_length=getattr(args, "goal_graph_desc_max", 100),
        )
