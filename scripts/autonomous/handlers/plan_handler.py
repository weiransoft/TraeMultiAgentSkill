"""PlanHandler - 计划阶段：生成 / 加载 plan。

行为（v2 修订：注入 YAGNI 规划约束）：
1. 加载 notes（NotesMemory.load）
2. 检测相关 skills（AutoSkillLoader.detect_for_task）
3. 生成或复用 plan 文本（注入 YAGNI 规划约束）
4. 写入 iter_ctx.current_plan

YAGNI 规划约束（Ponytail 决策梯第 1 阶在 plan 阶段的前置应用）：
- 推测性需求 = 跳过，用一行注释说明为何跳过
- 红线：用户明确要求的功能不可跳过；需求文档明确列出的功能不可跳过
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from autonomous.handlers.base import StageHandler, StageResult

if TYPE_CHECKING:
    from autonomous.loop_controller import IterationContext
    from ponytail.ruleset import PonytailRulesetEngine


class PlanHandler(StageHandler):
    """计划阶段 handler（v2 修订：注入 YAGNI 规划约束）。

    行为：
    1. 加载 notes.md（NotesMemory.load）
    2. 检测相关 skills（AutoSkillLoader.detect_for_task）
    3. 生成或复用 plan 文本（注入 YAGNI 规划约束）
    4. 写入 iter_ctx.current_plan
    """

    name = "plan"
    kind = "plan"

    def __init__(
        self,
        auto_skill_loader=None,
        notes_memory=None,
        ponytail_engine: "Optional[PonytailRulesetEngine]" = None,
    ):
        """构造 PlanHandler。

        Args:
            auto_skill_loader: AutoSkillLoader 实例
            notes_memory: NotesMemory 实例
            ponytail_engine: Ponytail 决策梯引擎实例（None 则不注入 YAGNI 约束）
        """
        self._auto_skill_loader = auto_skill_loader
        self._notes_memory = notes_memory
        # Ponytail 决策梯引擎（线程安全，无状态修改）
        self._ponytail_engine = ponytail_engine

    def do_handle(self, iter_ctx) -> StageResult:
        """实际处理：生成 / 加载 plan（注入 YAGNI 规划约束）。"""
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

        # 【新增】注入 YAGNI 规划约束（Ponytail 决策梯第 1 阶在 plan 阶段的前置应用）
        # 架构师评审 P0：architect 角色也需要 FULL 强度决策梯，否则下游救不回来
        if self._ponytail_engine is not None:
            yagni_constraint = self._ponytail_engine.get_injection_prompt(
                role="architect",
            )
            if yagni_constraint:
                plan_text += "\n\n## YAGNI 规划约束\n"
                plan_text += "在制定计划时，应用以下决策梯约束：\n\n"
                plan_text += yagni_constraint
                plan_text += "\n\n### 规划阶段重点\n"
                plan_text += "- 推测性需求 = 跳过，用一行注释说明为何跳过\n"
                plan_text += "- 红线：用户明确要求的功能不可跳过；需求文档明确列出的功能不可跳过\n"
                plan_text += "- 优先复用现有依赖，绝不为几行能搞定的事新增依赖\n"
                plan_text += "- 优先使用标准库和平台原生特性\n"

        # 4. 写入 iter_ctx.current_plan
        iter_ctx.current_plan = plan_text
        return StageResult(
            kind="success",
            summary=f"plan 已生成（iter={iter_ctx.iter_index}，检测到 {len(relevant_skills)} 个相关 skills）",
            artifacts={
                "plan_length": len(plan_text),
                "relevant_skills": [s.name for s in relevant_skills],
                "notes_size": len(notes_snapshot),
                "ponytail_injected": self._ponytail_engine is not None,
            },
        )


__all__ = ["PlanHandler"]
