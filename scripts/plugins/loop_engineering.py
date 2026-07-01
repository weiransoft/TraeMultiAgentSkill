"""LoopEngineeringPlugin — Loop Engineering 五步闭环的 V3 插件入口。

设计目标：
- 作为 Loop Engineering 模式的 V3 插件入口（priority=42，LOOP 范围）。
- 不修改 dispatcher / facade 核心逻辑，仅通过注册新 plugin 扩展。
- 复用 PluginContext 传递 project_root / log / dry_run。
- 内部真实实例化 LoopKernel 所需的全部组件（Discovery / Handoff / Verification /
  Persistence / Scheduling），禁止 mock/占位。

约束：
- 与 autonomous / loop / goal-* 等模式互斥。
- requires_task=True：新建 run 必须提供 --task 或 --task-file。
- dry_run 短路：不构造 kernel，直接返回 True。
"""

from __future__ import annotations

import argparse
import uuid
from typing import Set

from plugins.base import GoalCommandPlugin
from dispatcher.plugin_context import PluginContext

from autonomous.notes_memory import NotesMemory
from autonomous.run_state import RunState
from feedback_control_loop import FeedbackControlLoop
from performance_fingerprint import PerformanceFingerprint

from loop_engineering.config_loader import build_loop_config
from loop_engineering.discovery_probe import DiscoveryProbe
from loop_engineering.dispatch_adapter import LoopDispatchAdapter
from loop_engineering.handoff_adapter import HandoffAdapter
from loop_engineering.independent_evaluator import IndependentEvaluator
from loop_engineering.kernel import LoopKernel
from loop_engineering.loop_scheduler import LoopScheduler
from loop_engineering.unified_memory import UnifiedMemoryLayer


class LoopEngineeringPlugin(GoalCommandPlugin):
    """Loop Engineering 五步闭环入口插件（priority=42）。"""

    @property
    def name(self) -> str:
        """插件名：loop-engineering（CLI flag --loop-engineering）。"""
        return "loop-engineering"

    @property
    def priority(self) -> int:
        """priority=42，位于 loop(40) 之后，LOOP 范围内唯一。"""
        return 42

    @property
    def mutex_with(self) -> Set[str]:
        """Loop Engineering 与其他模式互斥。"""
        return {
            "goal-cancel",
            "autonomous",
            "goal-graph",
            "goal-resume",
            "multi-goal",
            "loop",
        }

    @property
    def requires_task(self) -> bool:
        """新建 run 必须提供 --task 或 --task-file。"""
        return True

    def matches(self, args: argparse.Namespace) -> bool:
        """匹配条件：--loop-engineering 被显式开启。"""
        return getattr(args, "loop_engineering", False)

    def execute(self, args: argparse.Namespace, ctx: PluginContext) -> bool:
        """执行 Loop Engineering 五步闭环。

        Args:
            args: argparse 解析结果。
            ctx: 共享上下文（project_root / log / dry_run 等）。

        Returns:
            bool: True = 成功（Loop 正常完成）；False = 失败。
        """
        # 1. dry_run 短路
        if getattr(ctx, "dry_run", False):
            ctx.log("🔄 loop-engineering 模式：dry_run 短路", "WARNING")
            return True

        # 2. 解析 objective（支持 --task 或 --task-file）
        objective = self._resolve_objective(args, ctx)
        if not objective:
            ctx.log(
                "❌ loop-engineering 模式必须提供 --task 或 --task-file",
                "ERROR",
            )
            return False

        # 3. 构建 LoopEngineeringConfig
        try:
            config = build_loop_config(args, project_root=ctx.project_root)
        except Exception as exc:
            ctx.log(f"❌ 构建 LoopEngineeringConfig 失败: {exc}", "ERROR")
            return False

        # 4. 创建 run_dir
        run_id = f"le-{uuid.uuid4().hex[:12]}"
        run_dir = config.project_root / config.run_dir / run_id
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            ctx.log(f"❌ 创建 run_dir 失败: {exc}", "ERROR")
            return False

        # 5. 实例化持久化与反馈组件
        notes_memory = NotesMemory(notes_path=run_dir / config.notes_path)
        run_state = RunState(
            run_dir=run_dir,
            run_id=run_id,
            objective=objective,
        )
        run_state.mark_running()
        fingerprint = PerformanceFingerprint(
            agent_id=run_id,
            storage_path=str(run_dir / "fingerprint"),
        )
        feedback_loop = FeedbackControlLoop(
            agent_id=run_id,
            storage_path=str(run_dir / "feedback"),
        )

        # 6. 构建统一 Memory 层
        memory = UnifiedMemoryLayer(
            notes_memory=notes_memory,
            run_state=run_state,
            fingerprint=fingerprint,
            feedback_loop=feedback_loop,
            run_id=run_id,
        )

        # 7. 构建 Loop 各阶段组件
        discovery_probe = DiscoveryProbe(config=config, log=ctx.log)
        dispatch_adapter = LoopDispatchAdapter(
            project_root=config.project_root,
            log=ctx.log,
        )
        handoff_adapter = HandoffAdapter(
            config=config,
            dispatcher_adapter=dispatch_adapter,
            log=ctx.log,
        )
        evaluator = IndependentEvaluator(config=config, log=ctx.log)
        scheduler = LoopScheduler(config=config, log=ctx.log)

        # 8. 构建并运行 LoopKernel
        kernel = LoopKernel(
            config=config,
            discovery_probe=discovery_probe,
            handoff_adapter=handoff_adapter,
            evaluator=evaluator,
            memory=memory,
            scheduler=scheduler,
            log=ctx.log,
        )

        try:
            report = kernel.run(objective)
        except Exception as exc:
            ctx.log(
                f"❌ LoopKernel 运行异常: {type(exc).__name__}: {exc}",
                "ERROR",
            )
            try:
                run_state.mark_failed(str(exc))
            except Exception:
                pass
            return False

        # 9. 根据最终状态返回
        if report.final_status == "completed":
            ctx.log(
                f"✅ loop-engineering 完成：run_id={report.run_id} "
                f"iterations={report.total_iterations} "
                f"duration={report.duration_sec:.2f}s",
                "SUCCESS",
            )
            return True

        ctx.log(
            f"❌ loop-engineering 未成功：status={report.final_status} "
            f"run_id={report.run_id}",
            "ERROR",
        )
        return False

    def _resolve_objective(
        self,
        args: argparse.Namespace,
        ctx: PluginContext,
    ) -> str:
        """从 --task 或 --task-file 解析目标描述。

        Args:
            args: argparse 解析结果。
            ctx: 插件上下文。

        Returns:
            str: 目标描述；如果都未提供则返回空字符串。
        """
        objective = getattr(args, "task", "") or ""
        task_file = getattr(args, "task_file", None)
        if not objective and task_file:
            task_path = ctx.project_root / task_file
            if task_path.exists():
                try:
                    objective = task_path.read_text(encoding="utf-8").strip()
                except OSError:
                    objective = ""
        return objective


__all__ = ["LoopEngineeringPlugin"]
