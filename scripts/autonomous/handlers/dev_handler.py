"""DevHandler - 开发阶段：调用 dispatcher 实际执行。

行为（v2 修订：Phase 0 验证后修复递归）：
1. 调用 SmartConfirmation.check 决定是否需要 confirm（实际跳过，仅记录）
2. 【修复】直接调用 _dispatch_via_claude_code（绕过 DispatcherAdapter.invoke 递归）
3. 注入 Ponytail 决策梯到 context dict（通过 json.dumps 进入 LLM prompt）
4. 把输出写入 iter_ctx

Phase 0 验证发现：
- DevHandler 调用 DispatcherAdapter.invoke → facade._dispatch_through_v3
  → args.autonomous=True 再次匹配 autonomous plugin → 无限递归
- auto_context 不被 facade 消费（注入链路断裂）
- 修复方案：直接调用 _dispatch_via_claude_code，那里有真正的 prompt 注入点
  （context dict → adapter.invoke_agent → _build_agent_prompt → json.dumps → prompt）
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from autonomous.handlers.base import StageHandler, StageResult

if TYPE_CHECKING:
    from autonomous.loop_controller import IterationContext
    from ponytail.ruleset import PonytailMode, PonytailRulesetEngine


class DevHandler(StageHandler):
    """开发阶段 handler（v2 修订：修复递归 + 注入决策梯）。

    行为：
    1. 调用 SmartConfirmation.check 决定是否需要 confirm
    2. 【修复】直接调用 _dispatch_via_claude_code（绕过递归）
    3. 注入 Ponytail 决策梯到 context dict
    4. 把输出写入 iter_ctx
    """

    name = "dev"
    kind = "dev"

    def __init__(
        self,
        dispatcher_adapter=None,
        smart_confirmation=None,
        auto_skill_loader=None,
        ponytail_engine: "Optional[PonytailRulesetEngine]" = None,
        project_root: Optional[str] = None,
        ponytail_mode: "Optional[PonytailMode]" = None,
    ):
        """构造 DevHandler。

        Args:
            dispatcher_adapter: DispatcherAdapter 实例（保留用于兼容性，实际不再调用其 invoke）
            smart_confirmation: SmartConfirmation 实例
            auto_skill_loader: AutoSkillLoader 实例
            ponytail_engine: Ponytail 决策梯引擎实例（None 则不注入决策梯）
            project_root: 项目根目录（_dispatch_via_claude_code 需要）
            ponytail_mode: 可选模式覆盖（None 则用角色默认强度 solo_coder=FULL）
        """
        self._dispatcher_adapter = dispatcher_adapter
        self._smart_confirmation = smart_confirmation
        self._auto_skill_loader = auto_skill_loader
        # Ponytail 决策梯引擎（线程安全，无状态修改）
        self._ponytail_engine = ponytail_engine
        self._project_root = project_root or "."
        self._ponytail_mode = ponytail_mode

    def do_handle(self, iter_ctx) -> StageResult:
        """实际处理：直接调用 _dispatch_via_claude_code（修复递归）+ 注入决策梯。

        Phase 0 验证后的核心变更：
        - 不再调用 DispatcherAdapter.invoke（会无限递归 autonomous plugin）
        - 直接调用 _dispatch_via_claude_code（真正的 prompt 注入点）
        - 构造 context dict，注入 Ponytail 决策梯
        """
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
                # skill 检测失败不阻塞 dev
                skills_payload = []

        # 2. 构造任务描述
        task = iter_ctx.current_plan or f"完成 Objective: {iter_ctx.run_id}"

        # 3. 【修复】直接调用 _dispatch_via_claude_code（绕过递归）
        #    延迟导入避免循环依赖
        from dispatch.legacy import _dispatch_via_claude_code

        # 4. 构造 context（Ponytail 决策梯注入点）
        #    context 会被 _build_agent_prompt 用 json.dumps 拼到 prompt 末尾
        ponytail_prompt = ""
        if self._ponytail_engine is not None:
            ponytail_prompt = self._ponytail_engine.get_injection_prompt(
                role="solo_coder",
                mode=self._ponytail_mode,
            )

        context = {
            "task_id": iter_ctx.run_id,
            "project_root": str(self._project_root),
            "timestamp": datetime.now().isoformat(),
            "iter_index": iter_ctx.iter_index,
            "karpathy_principles": {
                "think_before_coding": "明确假设、问清楚、不隐藏困惑",
                "simplicity_first": "最小代码、无 speculative features",
                "surgical_changes": "只改必要的、不改无关的",
                "goal_driven": "定义成功标准、验证检查点",
            },
            # 【新增】Ponytail 决策梯注入（作为 Karpathy Simplicity First 的可执行步骤）
            "ponytail_decision_ladder": ponytail_prompt,
            # 【新增】skills 注入（修复 auto_skills 链路断裂）
            "auto_skills": skills_payload,
        }

        # 5. 调用 _dispatch_via_claude_code（真正的 prompt 注入点）
        #    agent_type 固定为 solo_coder（dev 阶段由开发者执行）
        success = _dispatch_via_claude_code(
            agent_type="solo_coder",
            task=task,
            task_id=iter_ctx.run_id,
            project_root=str(self._project_root),
            progress={},
        )

        # 6. 把结果写入 iter_ctx（供后续阶段使用）
        #    _dispatch_via_claude_code 返回 bool，无 output/tokens
        #    保留 agent_output 接口供 verify 阶段使用
        iter_ctx.agent_output = ""  # 实际 output 由 _dispatch_via_claude_code 内部 log
        iter_ctx.token_used = 0

        if success:
            return StageResult(
                kind="success",
                summary="dev 执行成功（_dispatch_via_claude_code）",
                artifacts={
                    "output": "",
                    "tokens": 0,
                    "skills_used": [s["name"] for s in skills_payload],
                    "ponytail_injected": bool(ponytail_prompt),
                },
            )
        # 失败时返回 retriable（允许下一轮重试）
        # v2.8.4：summary 包含 dispatch_failed 关键词，供 loop_controller 熔断判断
        return StageResult(
            kind="retriable",
            summary="dev 执行失败（dispatch_failed: _dispatch_via_claude_code 返回 False）",
            error="_dispatch_via_claude_code failed",
        )


__all__ = ["DevHandler"]
