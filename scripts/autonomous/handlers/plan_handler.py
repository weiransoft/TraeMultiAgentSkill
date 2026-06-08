"""PlanHandler - 计划阶段：生成 / 加载 plan。

行为：
1. 加载 notes（NotesMemory.load）
2. 检测相关 skills（AutoSkillLoader.detect_for_task）
3. 生成或复用 plan 文本
4. 写入 iter_ctx.current_plan
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from autonomous.handlers.base import StageHandler, StageResult

if TYPE_CHECKING:
    from autonomous.loop_controller import IterationContext


class PlanHandler(StageHandler):
    """计划阶段 handler。

    行为：
    1. 加载 notes.md（NotesMemory.load）
    2. 检测相关 skills（AutoSkillLoader.detect_for_task）
    3. 生成或复用 plan 文本
    4. 写入 iter_ctx.current_plan
    """

    name = "plan"
    kind = "plan"

    def __init__(self, auto_skill_loader=None, notes_memory=None):
        """构造 PlanHandler。

        Args:
            auto_skill_loader: AutoSkillLoader 实例
            notes_memory: NotesMemory 实例
        """
        self._auto_skill_loader = auto_skill_loader
        self._notes_memory = notes_memory

    def do_handle(self, iter_ctx) -> StageResult:
        """实际处理：生成 / 加载 plan。"""
        # 1. 加载 notes
        notes_snapshot = ""
        if self._notes_memory is not None:
            try:
                notes_snapshot = self._notes_memory.load()
            except Exception as e:
                return StageResult(
                    kind="retriable",
                    summary=f"加载 notes 失败: {e}",
                    error=str(e),
                )
        # 2. 检测相关 skills
        relevant_skills = []
        if self._auto_skill_loader is not None:
            try:
                relevant_skills = self._auto_skill_loader.detect_for_task(
                    iter_ctx.current_plan or iter_ctx.run_id
                )
            except Exception as e:
                # 失败不阻塞 plan
                relevant_skills = []
        # 3. 生成 plan
        #   - 第 1 轮：使用 initial prompt 作为 plan
        #   - 后续轮：基于 notes 增量调整
        if iter_ctx.iter_index == 1:
            # 首轮：使用 run objective 作为 plan（由 LoopController 预填）
            plan_text = iter_ctx.current_plan or f"完成目标: {iter_ctx.run_id}"
        else:
            # 后续轮：基于 notes 提炼
            recent_notes = notes_snapshot[-2000:] if notes_snapshot else ""
            plan_text = f"## Iteration {iter_ctx.iter_index} Plan\n\n"
            plan_text += f"基于历史 notes（最近 2KB）继续迭代...\n\n"
            if recent_notes:
                plan_text += f"### 上次要点\n{recent_notes[:500]}\n\n"
            plan_text += "### 本轮目标\n继续推进 Objective，完成剩余任务。\n"
        # 4. 写入 iter_ctx.current_plan
        iter_ctx.current_plan = plan_text
        return StageResult(
            kind="success",
            summary=f"plan 已生成（iter={iter_ctx.iter_index}，检测到 {len(relevant_skills)} 个相关 skills）",
            artifacts={
                "plan_length": len(plan_text),
                "relevant_skills": [s.name for s in relevant_skills],
                "notes_size": len(notes_snapshot),
            },
        )


__all__ = ["PlanHandler"]
