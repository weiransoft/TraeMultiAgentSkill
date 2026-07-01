"""Loop Engineering 核心编排器：LoopKernel。

实现五步闭环：
    Discovery -> Handoff -> Verification -> Persistence -> Scheduling

LoopKernel 不直接执行具体业务逻辑，而是通过 Protocol 组合以下组件：
- DiscoveryProbeProtocol：发现本轮该做什么
- HandoffAdapterProtocol：生成工作项并调用 Generator 执行
- IndependentEvaluatorProtocol：独立评估 Generator 产出
- UnifiedMemoryLayerProtocol：统一记忆读写
- LoopScheduler：决定下一步动作

设计约束：
- 所有依赖均为真实对象实例，禁止 mock。
- 上限保护：max_iterations / max_tokens / 连续失败上限。
- 事件驱动：每步产生 LoopEvent 并写入 Memory。
- 可安全停止：stop() 设置停止标志，当前轮次完成后退出。
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from loop_engineering.loop_scheduler import LoopScheduler
from loop_engineering.models import (
    HumanCheckpointResponse,
    LoopCycleResult,
    LoopEngineeringConfig,
    LoopEvent,
    LoopEventType,
    LoopRunReport,
    SchedulingAction,
)
from loop_engineering.protocols import (
    DiscoveryProbeProtocol,
    HandoffAdapterProtocol,
    IndependentEvaluatorProtocol,
    UnifiedMemoryLayerProtocol,
)


class LoopKernel:
    """Loop Engineering 五步闭环编排核心。"""

    def __init__(
        self,
        config: LoopEngineeringConfig,
        discovery_probe: DiscoveryProbeProtocol,
        handoff_adapter: HandoffAdapterProtocol,
        evaluator: IndependentEvaluatorProtocol,
        memory: UnifiedMemoryLayerProtocol,
        scheduler: LoopScheduler,
        log: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """构造 LoopKernel。

        Args:
            config: Loop Engineering 配置。
            discovery_probe: Discovery 阶段组件。
            handoff_adapter: Handoff 阶段组件。
            evaluator: 独立 Evaluator 组件。
            memory: 统一 Memory 层组件。
            scheduler: Loop 调度器。
            log: 日志回调函数（可选）。
        """
        self._config = config
        self._discovery_probe = discovery_probe
        self._handoff_adapter = handoff_adapter
        self._evaluator = evaluator
        self._memory = memory
        self._scheduler = scheduler
        self._log = log

        self._run_id: str = uuid.uuid4().hex[:12]
        self._events: List[LoopEvent] = []
        self._human_checkpoints: List[Dict[str, Any]] = []
        self._stop_requested: bool = False
        self._consecutive_failures: int = 0
        self._committed_count: int = 0

    def _info(self, message: str) -> None:
        """输出 INFO 级别日志。"""
        if self._log:
            self._log(message, "INFO")

    def _warn(self, message: str) -> None:
        """输出 WARN 级别日志。"""
        if self._log:
            self._log(message, "WARN")

    def _new_event_id(self) -> str:
        """生成事件唯一标识。"""
        return f"evt-{uuid.uuid4().hex[:8]}"

    def _emit(
        self,
        event_type: LoopEventType,
        phase: str,
        iter_index: int,
        payload: Optional[Dict[str, Any]] = None,
    ) -> LoopEvent:
        """创建事件、追加到内存并写入 Memory。"""
        event = LoopEvent(
            event_id=self._new_event_id(),
            event_type=event_type,
            phase=phase,
            run_id=self._run_id,
            iter_index=iter_index,
            payload=payload or {},
        )
        self._events.append(event)
        try:
            self._memory.persist_event(event)
        except Exception as exc:
            self._warn(f"持久化事件失败：{exc}")
        return event

    def run(self, objective: str) -> LoopRunReport:
        """启动完整 Loop Engineering 流程。

        循环直到满足停止条件或触发上限。

        Args:
            objective: 运行目标描述。

        Returns:
            LoopRunReport: 完整运行报告。
        """
        start_time = time.time()
        self._info(
            f"启动 Loop Engineering：run_id={self._run_id} "
            f"loop_type={self._config.loop_type.value} objective={objective!r}"
        )

        iter_index = 0
        final_status = "failed"

        while not self._stop_requested:
            # 1. 单轮执行
            cycle_result = self.run_one_cycle(objective, iter_index)

            # 2. 根据调度决策更新状态
            decision = cycle_result.scheduling_decision
            if decision.action == SchedulingAction.STOP_SUCCESS:
                final_status = "completed"
                self._info(f"Loop 正常完成：{decision.reason}")
                break
            if decision.action == SchedulingAction.STOP_FAILURE:
                final_status = "failed"
                self._warn(f"Loop 失败停止：{decision.reason}")
                break
            if decision.action == SchedulingAction.HUMAN_CHECKPOINT:
                response = self.request_human_checkpoint(decision.reason)
                self._human_checkpoints.append(
                    {
                        "iter_index": iter_index,
                        "reason": decision.reason,
                        "approved": response.approved,
                        "feedback": response.feedback,
                        "abort": response.abort,
                    }
                )
                if response.abort:
                    final_status = "aborted"
                    self._info("人类中止 Loop")
                    break
                if not response.approved:
                    # 人类不批准，本轮视为失败，继续修复
                    self._consecutive_failures += 1

            # 3. 下一轮
            iter_index += 1
            if decision.action == SchedulingAction.FIX:
                self._consecutive_failures += 1
            elif decision.action == SchedulingAction.CONTINUE:
                self._consecutive_failures = 0

            # 安全上限：如果 iter_index 已经超过 max_iterations，强制停止
            if iter_index >= self._config.max_iterations:
                final_status = "failed"
                self._warn(f"达到最大迭代次数上限 {self._config.max_iterations}")
                break

        # 4. 生成最终报告
        duration = time.time() - start_time
        token_used = self._memory.estimate_token_usage()

        self._emit(
            LoopEventType.LOOP_COMPLETED
            if final_status == "completed"
            else LoopEventType.LOOP_FAILED,
            phase="scheduling",
            iter_index=iter_index,
            payload={
                "final_status": final_status,
                "duration_sec": duration,
                "token_used": token_used,
            },
        )

        summary = self._build_final_summary(
            objective, iter_index, final_status, duration, token_used
        )

        return LoopRunReport(
            run_id=self._run_id,
            loop_type=self._config.loop_type,
            objective=objective,
            total_iterations=iter_index + 1,
            final_status=final_status,
            events=list(self._events),
            token_used=token_used,
            duration_sec=duration,
            committed_count=self._committed_count,
            human_checkpoints=list(self._human_checkpoints),
            final_summary=summary,
        )

    def run_one_cycle(
        self,
        objective: str,
        iter_index: int,
    ) -> LoopCycleResult:
        """执行单轮五步闭环。

        Args:
            objective: 运行目标。
            iter_index: 当前迭代索引。

        Returns:
            LoopCycleResult: 本轮执行结果。
        """
        cycle_start = time.time()
        self._info(f"开始第 {iter_index + 1} 轮循环")

        # Step 1: Discovery
        self._emit(
            LoopEventType.DISCOVERY_STARTED,
            phase="discovery",
            iter_index=iter_index,
            payload={"objective": objective},
        )
        discovery = self._discovery_probe.discover(
            objective=objective,
            prev_events=list(self._events),
            memory=self._memory,
        )
        self._emit(
            LoopEventType.DISCOVERY_COMPLETED,
            phase="discovery",
            iter_index=iter_index,
            payload={
                "objective": discovery.objective,
                "risks": discovery.detected_risks,
                "agents": discovery.suggested_agents,
                "patterns": discovery.suggested_patterns,
            },
        )

        # Step 2: Handoff - 生成工作项
        handoff_items = self._handoff_adapter.create_work_items(
            discovery=discovery,
            loop_type=self._config.loop_type.value,
        )
        self._emit(
            LoopEventType.HANDOFF_CREATED,
            phase="handoff",
            iter_index=iter_index,
            payload={"item_count": len(handoff_items)},
        )

        # Step 3: Handoff - 执行 Generator
        generator_result = self._handoff_adapter.execute(
            items=handoff_items,
            config=self._config,
        )
        self._emit(
            LoopEventType.HANDOFF_DISPATCHED,
            phase="handoff",
            iter_index=iter_index,
            payload={
                "generator_keys": list(generator_result.keys()),
                "success": generator_result.get("success", False),
            },
        )

        # Step 4: Verification - 独立 Evaluator
        self._emit(
            LoopEventType.VERIFICATION_STARTED,
            phase="verification",
            iter_index=iter_index,
            payload={"evaluator_mode": self._config.evaluator_mode.value},
        )
        verdict = self._evaluator.evaluate(
            handoff_items=handoff_items,
            generator_result=generator_result,
            context={"objective": objective, "loop_type": self._config.loop_type.value},
        )
        self._emit(
            LoopEventType.VERIFICATION_PASSED
            if verdict.passed
            else LoopEventType.VERIFICATION_REJECTED,
            phase="verification",
            iter_index=iter_index,
            payload={
                "passed": verdict.passed,
                "reason": verdict.reason,
                "severity": verdict.severity,
                "findings": verdict.findings,
            },
        )

        # Step 5: Persistence
        if verdict.passed:
            self._committed_count += generator_result.get("committed_count", 0)
        self._emit(
            LoopEventType.PERSISTENCE_WRITTEN,
            phase="persistence",
            iter_index=iter_index,
            payload={
                "passed": verdict.passed,
                "committed_count": self._committed_count,
            },
        )

        # Step 6: Scheduling
        cumulative_tokens = self._memory.estimate_token_usage()
        decision = self._scheduler.decide_next(
            current_iter=iter_index,
            verdict=verdict,
            memory_events=list(self._events),
            cumulative_tokens=cumulative_tokens,
            consecutive_failures=self._consecutive_failures,
        )
        if decision.action in (SchedulingAction.FIX, SchedulingAction.CONTINUE):
            decision.backoff_seconds = self._scheduler.compute_backoff(
                self._consecutive_failures
            )
        self._emit(
            LoopEventType.SCHEDULING_DECISION,
            phase="scheduling",
            iter_index=iter_index,
            payload={
                "action": decision.action.value,
                "reason": decision.reason,
                "backoff": decision.backoff_seconds,
            },
        )

        cycle_duration = time.time() - cycle_start
        token_used = self._memory.estimate_token_usage()

        return LoopCycleResult(
            iter_index=iter_index,
            discovery=discovery,
            handoff_items=handoff_items,
            generator_result=generator_result,
            verdict=verdict,
            events=[e for e in self._events if e.iter_index == iter_index],
            token_used=token_used,
            duration_sec=cycle_duration,
            scheduling_decision=decision,
        )

    def request_human_checkpoint(self, reason: str) -> HumanCheckpointResponse:
        """触发人类检查点。

        默认实现自动批准（非交互式环境）。
        未来可扩展为通过 CLI / UI 等待人类输入。

        Args:
            reason: 触发原因。

        Returns:
            HumanCheckpointResponse: 人类响应（默认 approved=True）。
        """
        self._info(f"人类检查点：{reason}")
        self._emit(
            LoopEventType.HUMAN_CHECKPOINT,
            phase="scheduling",
            iter_index=0,
            payload={"reason": reason},
        )
        # 默认实现：自动批准，不中止
        return HumanCheckpointResponse(approved=True, feedback="自动批准", abort=False)

    def stop(self, reason: str) -> None:
        """安全停止循环。

        设置停止标志，当前轮次完成后退出。

        Args:
            reason: 停止原因。
        """
        self._stop_requested = True
        self._info(f"收到停止请求：{reason}")

    def _build_final_summary(
        self,
        objective: str,
        total_iterations: int,
        final_status: str,
        duration_sec: float,
        token_used: int,
    ) -> str:
        """构建最终摘要文本。"""
        return (
            f"Loop Engineering 运行报告\n"
            f"- run_id: {self._run_id}\n"
            f"- loop_type: {self._config.loop_type.value}\n"
            f"- objective: {objective}\n"
            f"- total_iterations: {total_iterations + 1}\n"
            f"- final_status: {final_status}\n"
            f"- duration_sec: {duration_sec:.2f}\n"
            f"- token_used: {token_used}\n"
            f"- committed_count: {self._committed_count}\n"
            f"- human_checkpoints: {len(self._human_checkpoints)}\n"
        )


__all__ = ["LoopKernel"]
