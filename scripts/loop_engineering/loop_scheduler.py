"""Loop Engineering Scheduling 阶段。

基于当前迭代状态、验证结果、Memory 历史、Token 预算，
决定 Loop 下一步动作：continue / fix / human_checkpoint / stop。

设计原则：
- 保守停止：遇到硬上限（max_iterations / max_tokens / 连续失败）必须停止。
- 人类检查点：按固定间隔或高风险事件触发，避免 Cognitive Surrender。
- 动态修复：验证未通过时优先 fix，但超过尝试次数后停止。
"""

from __future__ import annotations

from typing import Callable, List, Optional

from loop_engineering.models import (
    EvaluationVerdict,
    LoopEngineeringConfig,
    LoopEvent,
    SchedulingAction,
    SchedulingDecision,
)


class LoopScheduler:
    """Loop 调度器：决定下一循环动作。"""

    def __init__(
        self,
        config: LoopEngineeringConfig,
        log: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """构造调度器。

        Args:
            config: Loop Engineering 配置。
            log: 日志回调函数（可选）。
        """
        self._config = config
        self._log = log

    def _info(self, message: str) -> None:
        """输出 INFO 级别日志。"""
        if self._log:
            self._log(message, "INFO")

    def decide_next(
        self,
        current_iter: int,
        verdict: EvaluationVerdict,
        memory_events: List[LoopEvent],
        cumulative_tokens: int,
        consecutive_failures: int = 0,
    ) -> SchedulingDecision:
        """决定下一步动作。

        决策优先级（从高到低）：
        1. 硬上限检查（max_tokens / max_iterations / 连续失败）。
        2. 人类检查点触发（按固定间隔）。
        3. 验证结果：通过 → 检查是否达成停止条件；未通过 → fix。

        Args:
            current_iter: 当前迭代索引（从 0 开始）。
            verdict: 本轮独立 Evaluator 判定结果。
            memory_events: 历史事件列表。
            cumulative_tokens: 累计 token 消耗估算。
            consecutive_failures: 连续验证失败次数。

        Returns:
            SchedulingDecision: 调度决策。
        """
        # 1. Token 预算硬上限
        if cumulative_tokens >= self._config.max_tokens:
            self._info(
                f"Token 预算耗尽：{cumulative_tokens} >= {self._config.max_tokens}"
            )
            return SchedulingDecision(
                action=SchedulingAction.STOP_FAILURE,
                reason=f"Token 预算耗尽：{cumulative_tokens} >= {self._config.max_tokens}",
            )

        # 2. 最大迭代次数硬上限（已通过 current_iter 判断）
        if current_iter + 1 >= self._config.max_iterations:
            if not verdict.passed:
                self._info(
                    f"达到最大迭代次数 {self._config.max_iterations} 且验证未通过"
                )
                return SchedulingDecision(
                    action=SchedulingAction.STOP_FAILURE,
                    reason=f"达到最大迭代次数 {self._config.max_iterations} 且验证未通过",
                )
            self._info(f"达到最大迭代次数 {self._config.max_iterations}，最后一轮通过")
            return SchedulingDecision(
                action=SchedulingAction.STOP_SUCCESS,
                reason=f"达到最大迭代次数 {self._config.max_iterations}，最后一轮通过",
            )

        # 3. 连续失败上限（默认 5 次，可通过 extra 配置）
        max_consecutive_failures = int(
            self._config.extra.get("max_consecutive_failures", 5)
        )
        if consecutive_failures >= max_consecutive_failures:
            self._info(f"连续失败 {consecutive_failures} 次，终止 Loop")
            return SchedulingDecision(
                action=SchedulingAction.STOP_FAILURE,
                reason=f"连续失败 {consecutive_failures} 次，终止 Loop",
            )

        # 4. 高风险事件触发人类检查点
        if self._has_high_risk_event(memory_events):
            self._info("检测到高风险事件，触发人类检查点")
            return SchedulingDecision(
                action=SchedulingAction.HUMAN_CHECKPOINT,
                reason="检测到高风险事件，需要人类确认",
                requires_human_input=True,
            )

        # 5. 固定间隔人类检查点
        checkpoint_every = self._config.human_checkpoint_every
        if checkpoint_every > 0 and (current_iter + 1) % checkpoint_every == 0:
            self._info(f"第 {current_iter + 1} 轮，触发固定间隔人类检查点")
            return SchedulingDecision(
                action=SchedulingAction.HUMAN_CHECKPOINT,
                reason=f"第 {current_iter + 1} 轮，固定间隔人类检查点",
                requires_human_input=True,
            )

        # 6. 验证结果驱动
        if verdict.passed:
            # 检查是否满足自然语言停止条件
            if self._should_stop_when(memory_events):
                self._info("满足停止条件，正常结束 Loop")
                return SchedulingDecision(
                    action=SchedulingAction.STOP_SUCCESS,
                    reason="满足停止条件",
                )
            # 否则继续下一轮（可能是设计 Loop 的下一个需求，或编码 Loop 的下一个任务）
            self._info("验证通过，继续下一轮")
            return SchedulingDecision(
                action=SchedulingAction.CONTINUE,
                reason="验证通过，继续下一轮",
            )

        # 验证未通过 → fix
        self._info(f"验证未通过：{verdict.reason}，进入修复阶段")
        return SchedulingDecision(
            action=SchedulingAction.FIX,
            reason=f"验证未通过：{verdict.reason}",
        )

    def _has_high_risk_event(self, memory_events: List[LoopEvent]) -> bool:
        """检查最近事件中是否有高风险事件。

        当前实现：检查 payload 中 severity == 'blocker' 的事件。
        """
        for event in reversed(memory_events[-10:]):
            payload = event.payload or {}
            if payload.get("severity") == "blocker":
                return True
            if payload.get("requires_human_input") is True:
                return True
        return False

    def _should_stop_when(self, memory_events: List[LoopEvent]) -> bool:
        """判断是否满足自然语言停止条件。

        简单实现：遍历最近事件，检查是否有 LOOP_COMPLETED 或显式 stop 标记。
        未来可扩展为 LLM 判断。
        """
        if not self._config.stop_when:
            # 没有显式 stop_when 时，检查是否有 LOOP_COMPLETED 事件
            for event in reversed(memory_events[-5:]):
                if event.event_type.value == "loop_completed":
                    return True
            return False

        # 基于关键词的朴素匹配（未来可替换为 LLM 语义判断）
        stop_keywords = ["完成", "通过", "成功", "done", "passed", "completed"]
        stop_when_lower = self._config.stop_when.lower()
        # 如果 stop_when 本身包含完成类词汇，且最近一轮验证通过，则停止
        if any(kw in stop_when_lower for kw in stop_keywords):
            for event in reversed(memory_events[-3:]):
                if event.event_type.value == "verification_passed":
                    return True
        return False

    def compute_backoff(self, consecutive_failures: int) -> float:
        """计算修复前的退避时间。

        Args:
            consecutive_failures: 连续失败次数。

        Returns:
            float: 退避秒数。
        """
        base = float(self._config.extra.get("backoff_base_sec", 1.0))
        max_backoff = float(self._config.extra.get("backoff_max_sec", 60.0))
        backoff = min(base * (2**consecutive_failures), max_backoff)
        # 显式转换为 float，消除 mypy 对 Any 返回值的推断
        return float(backoff)


__all__ = ["LoopScheduler"]
