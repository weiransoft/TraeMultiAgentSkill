"""DevHandler - 开发阶段：调用 dispatcher 实际执行。

行为：
1. 调用 SmartConfirmation.check 决定是否需要 confirm（实际跳过，仅记录）
2. 调用 DispatcherAdapter.invoke 真实执行
3. 把输出写入 iter_ctx
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from autonomous.handlers.base import StageHandler, StageResult

if TYPE_CHECKING:
    from autonomous.loop_controller import IterationContext


class DevHandler(StageHandler):
    """开发阶段 handler。

    行为：
    1. 调用 SmartConfirmation.check 决定是否需要 confirm
    2. 调用 DispatcherAdapter.invoke 真实执行
    3. 把输出写入 iter_ctx
    """

    name = "dev"
    kind = "dev"

    def __init__(
        self,
        dispatcher_adapter=None,
        smart_confirmation=None,
        auto_skill_loader=None,
    ):
        """构造 DevHandler。

        Args:
            dispatcher_adapter: DispatcherAdapter 实例
            smart_confirmation: SmartConfirmation 实例
            auto_skill_loader: AutoSkillLoader 实例
        """
        self._dispatcher_adapter = dispatcher_adapter
        self._smart_confirmation = smart_confirmation
        self._auto_skill_loader = auto_skill_loader

    def do_handle(self, iter_ctx) -> StageResult:
        """实际处理：调用 dispatcher 执行任务。"""
        if self._dispatcher_adapter is None:
            return StageResult(
                kind="fatal",
                summary="DispatcherAdapter 未配置",
            )
        # 1. 检测相关 skills
        skills_payload = []
        if self._auto_skill_loader is not None:
            try:
                relevant = self._auto_skill_loader.detect_for_task(
                    iter_ctx.current_plan or iter_ctx.run_id
                )
                skills_payload = [
                    {
                        "name": s.name,
                        "description": s.description,
                        "triggers": s.triggers,
                        "path": str(s.path),
                    }
                    for s in relevant
                ]
            except Exception:
                skills_payload = []
        # 2. 构造任务描述
        task = iter_ctx.current_plan or f"完成 Objective: {iter_ctx.run_id}"
        # 3. 调用 dispatcher
        result = self._dispatcher_adapter.invoke(
            task=task,
            agent="auto",
            auto_skills=skills_payload,
            extra_context={
                "iter_index": iter_ctx.iter_index,
                "run_id": iter_ctx.run_id,
            },
        )
        # 4. 把结果写入 iter_ctx（供后续阶段使用）
        iter_ctx.agent_output = result.output
        iter_ctx.token_used = result.tokens
        if result.success:
            return StageResult(
                kind="success",
                summary=f"dispatcher 执行成功（{result.summary}）",
                artifacts={
                    "output": result.output[:2000],
                    "tokens": result.tokens,
                    "skills_used": result.skills_used,
                },
            )
        # 失败时根据 kind 决定
        if result.kind == "retriable":
            return StageResult(
                kind="retriable",
                summary=result.summary,
                error=result.error_trace[:1000] if result.error_trace else "",
            )
        return StageResult(
            kind="fatal",
            summary=result.summary,
            error=result.error_trace[:1000] if result.error_trace else "",
        )


__all__ = ["DevHandler"]
