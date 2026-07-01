"""Loop Engineering 统一 Memory 层。

桥接 multi-agent-team 现有多种记忆/持久化组件：
- NotesMemory：跨轮 notes.md，LLM 可直接阅读。
- RunState：state.json，支持断点续跑。
- PerformanceFingerprint：执行案例与失败模式库。
- FeedbackControlLoop：反馈控制与策略选择。

通过 UnifiedMemoryLayer 对外提供统一接口，避免 LoopKernel 直接依赖多个异构存储，
同时避免 Amnesiac Loop（失忆循环）。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from autonomous.notes_memory import NotesMemory, NotesSection
from autonomous.run_state import RunState
from feedback_control_loop import FeedbackControlLoop
from loop_engineering.models import LoopEvent, MemoryQuery
from performance_fingerprint import PerformanceFingerprint


class UnifiedMemoryLayer:
    """统一 Memory 层：桥接 NotesMemory / RunState / PerformanceFingerprint / FeedbackControlLoop。"""

    def __init__(
        self,
        notes_memory: NotesMemory,
        run_state: RunState,
        fingerprint: PerformanceFingerprint,
        feedback_loop: FeedbackControlLoop,
        run_id: str,
    ) -> None:
        """构造统一 Memory 层。

        Args:
            notes_memory: notes.md 记忆组件。
            run_state: state.json 运行状态组件。
            fingerprint: 性能画像组件。
            feedback_loop: 反馈控制环组件。
            run_id: 当前运行 ID。
        """
        self._notes = notes_memory
        self._run_state = run_state
        self._fingerprint = fingerprint
        self._feedback_loop = feedback_loop
        self._run_id = run_id
        self._event_count = 0

    def persist_event(self, event: LoopEvent) -> None:
        """持久化单个 Loop 事件。

        同时写入：
        1. NotesMemory（追加 markdown section，供 LLM 阅读）
        2. RunState（更新 history 和 cumulative_tokens）
        3. PerformanceFingerprint（根据事件类型记录案例）

        Args:
            event: Loop 事件。
        """
        self._event_count += 1

        # 1. 写入 NotesMemory
        section = NotesSection(
            title=f"## {event.event_type.value} (iter={event.iter_index})",
            body=self._format_event_body(event),
            timestamp=event.timestamp,
            iter_index=event.iter_index,
            tags=[event.phase, event.event_type.value],
        )
        try:
            self._notes.append(section)
        except Exception as exc:
            # NotesMemory 写入失败不应阻塞 RunState 和 fingerprint
            print(f"[UnifiedMemoryLayer] NotesMemory 写入失败：{exc}")

        # 2. 写入 RunState history
        try:
            # 首次写入事件时，若状态仍为 pending，则标记为 running
            if self._run_state.state.status == "pending":
                self._run_state.mark_running()
            self._run_state.record_iteration(
                iter_index=event.iter_index,
                result_kind=self._event_kind_to_result_kind(event),
                summary=f"{event.event_type.value}: {event.phase}",
                tokens=self._estimate_event_tokens(event),
                committed=event.event_type.value == "persistence_written"
                and event.payload.get("passed", False),
                error=event.payload.get("reason", ""),
            )
        except Exception as exc:
            print(f"[UnifiedMemoryLayer] RunState 记录失败：{exc}")

        # 3. 写入 PerformanceFingerprint
        try:
            task_type = f"loop_{event.phase}"
            success = event.event_type.value in (
                "verification_passed",
                "persistence_written",
                "loop_completed",
            )
            error_type = None
            if event.event_type.value == "verification_rejected":
                error_type = event.payload.get("severity", "verification_rejected")
            elif event.event_type.value == "loop_failed":
                error_type = event.payload.get("final_status", "loop_failed")

            self._fingerprint.record(
                task_type=task_type,
                task_complexity=5,
                success=success,
                error_type=error_type,
                execution_time=event.payload.get("duration_sec", 0.0),
                strategy=event.phase,
                context_features={
                    "run_id": self._run_id,
                    "event_type": event.event_type.value,
                    "iter_index": event.iter_index,
                },
            )
        except Exception as exc:
            print(f"[UnifiedMemoryLayer] Fingerprint 记录失败：{exc}")

    def query(self, query: MemoryQuery) -> List[Dict[str, Any]]:
        """统一查询接口。

        支持：
        - recent：最近 N 条事件（来自 NotesMemory）
        - similar：相似历史案例（来自 PerformanceFingerprint）
        - risk：高风险事件
        - event：按事件类型过滤

        Args:
            query: 查询参数。

        Returns:
            List[Dict[str, Any]]: 查询结果列表。
        """
        query_type = query.query_type
        if query_type == "recent":
            return self._query_recent(query)
        if query_type == "similar":
            return self._query_similar(query)
        if query_type == "risk":
            return self._query_risk(query)
        if query_type == "event":
            return self._query_event(query)
        # 默认返回最近事件
        return self._query_recent(query)

    def _query_recent(self, query: MemoryQuery) -> List[Dict[str, Any]]:
        """查询最近 notes sections。"""
        sections = self._notes.get_recent_sections(query.limit)
        results = []
        for section in sections:
            results.append(
                {
                    "source": "notes_memory",
                    "title": section.title,
                    "body": section.body,
                    "iter_index": section.iter_index,
                    "tags": section.tags,
                }
            )
        return results

    def _query_similar(self, query: MemoryQuery) -> List[Dict[str, Any]]:
        """查询相似历史案例。

        通过 PerformanceFingerprint.retrieve_similar_cases 检索与当前目标
        相似的历史执行记录，供 Discovery 阶段参考。
        """
        # 使用目标描述中的关键词估算复杂度（简单启发式）
        objective_len = len(query.objective or "")
        complexity = 5
        if objective_len < 30:
            complexity = 3
        elif objective_len > 200:
            complexity = 8

        similar_cases = self._fingerprint.retrieve_similar_cases(
            task_type="loop_execution",
            task_complexity=complexity,
            limit=query.limit,
        )
        results = []
        for case in similar_cases:
            results.append(
                {
                    "source": "performance_fingerprint",
                    "record_id": case.record_id,
                    "task_type": case.task_type,
                    "success": case.success,
                    "error_type": case.error_type,
                    "similarity_score": case.similarity_score,
                    "strategy": case.strategy,
                    "lessons_learned": case.lessons_learned,
                }
            )
        return results

    def _query_risk(self, query: MemoryQuery) -> List[Dict[str, Any]]:
        """查询高风险事件。"""
        sections = self._notes.get_recent_sections(100)
        results = []
        for section in sections:
            body = section.body or ""
            if "severity: blocker" in body or "验证未通过" in body:
                results.append(
                    {
                        "source": "notes_memory",
                        "title": section.title,
                        "body": body,
                        "iter_index": section.iter_index,
                    }
                )
            if len(results) >= query.limit:
                break
        return results

    def _query_event(self, query: MemoryQuery) -> List[Dict[str, Any]]:
        """按事件类型过滤查询。"""
        event_type_filter = query.filters.get("event_type")
        sections = self._notes.get_recent_sections(100)
        results = []
        for section in sections:
            if event_type_filter and event_type_filter in section.title:
                results.append(
                    {
                        "source": "notes_memory",
                        "title": section.title,
                        "body": section.body,
                        "iter_index": section.iter_index,
                    }
                )
            if len(results) >= query.limit:
                break
        return results

    def record_feedback(
        self,
        task: Dict[str, Any],
        success: bool,
        execution_time: float,
        error_type: Optional[str] = None,
    ) -> None:
        """写入 FeedbackControlLoop 与 PerformanceFingerprint。

        FeedbackControlLoop.execute_with_feedback 仅接收 task 参数，执行器需
        提前通过 set_executor() 注入。本方法注入一个确定性执行器，将 success、
        execution_time、error_type 注入结果，随后调用 execute_with_feedback 完成
        反馈闭环。

        Args:
            task: 任务信息。
            success: 是否成功。
            execution_time: 执行时间。
            error_type: 错误类型（可选）。
        """
        # 1. 写入 FeedbackControlLoop
        try:
            # 构造确定性执行器：直接返回已知的执行结果，不调用外部逻辑
            def _deterministic_executor(t: Dict[str, Any]) -> Dict[str, Any]:
                return {
                    "success": success,
                    "execution_time": execution_time,
                    "error_type": error_type,
                }

            self._feedback_loop.set_executor(_deterministic_executor)
            self._feedback_loop.execute_with_feedback(task=task)
        except Exception as exc:
            print(f"[UnifiedMemoryLayer] FeedbackControlLoop 写入失败：{exc}")

        # 2. 写入 PerformanceFingerprint
        try:
            self._fingerprint.record(
                task_type=task.get("type", "unknown"),
                task_complexity=task.get("complexity", 5),
                success=success,
                error_type=error_type,
                execution_time=execution_time,
                strategy=task.get("strategy", "default"),
                context_features=task.get("features", {}),
            )
        except Exception as exc:
            print(f"[UnifiedMemoryLayer] Fingerprint 记录失败：{exc}")

    def get_recent_notes(self, n: int = 5) -> str:
        """获取最近 notes 摘要文本。

        Args:
            n: 最近段落数。

        Returns:
            str: notes 文本摘要。
        """
        sections = self._notes.get_recent_sections(n)
        if not sections:
            return ""
        return "\n\n".join(f"{s.title}\n{s.body}" for s in sections)

    def estimate_token_usage(self) -> int:
        """估算当前累计 token 消耗。

        汇总 NotesMemory、RunState、PerformanceFingerprint 的估算。

        Returns:
            int: 累计 token 估算。
        """
        total = 0
        try:
            total += self._notes.estimate_tokens()
        except Exception:
            pass
        try:
            total += self._run_state.state.cumulative_tokens // 4
        except Exception:
            pass
        try:
            total += len(self._fingerprint.records)
        except Exception:
            pass
        return max(total, self._event_count)

    def _format_event_body(self, event: LoopEvent) -> str:
        """将 LoopEvent 格式化为 markdown body。"""
        lines = [
            f"- **phase**: {event.phase}",
            f"- **run_id**: {event.run_id}",
            f"- **event_id**: {event.event_id}",
        ]
        if event.payload:
            lines.append("- **payload**:")
            try:
                payload_text = json.dumps(event.payload, ensure_ascii=False, indent=2)
                for line in payload_text.splitlines():
                    lines.append(f"  {line}")
            except (TypeError, ValueError):
                lines.append(f"  {event.payload}")
        return "\n".join(lines)

    def _estimate_event_tokens(self, event: LoopEvent) -> int:
        """粗略估算单个事件的 token 数。"""
        try:
            text = json.dumps(event.payload, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(event.payload)
        # 粗略按字符数 / 4
        return max(10, len(text) // 4)

    def _event_kind_to_result_kind(self, event: LoopEvent) -> str:
        """将 LoopEventType 映射为 RunState 的 result_kind。"""
        if event.event_type.value in ("verification_passed", "loop_completed"):
            return "success"
        if event.event_type.value == "verification_rejected":
            return "failed"
        if event.event_type.value == "loop_failed":
            return "fatal"
        return "retriable"


__all__ = ["UnifiedMemoryLayer"]
