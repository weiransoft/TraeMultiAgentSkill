"""Loop Engineering 组件协议（Protocol）。

本模块定义 LoopKernel 所依赖的各阶段组件的抽象接口。
Phase 3 将提供这些协议的具体实现（DiscoveryProbe / UnifiedMemoryLayer / IndependentEvaluator）。
通过 Protocol 而非抽象基类，可以在不修改 LoopKernel 的情况下替换实现，
同时支持测试时注入轻量级真实对象（非 mock）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Protocol

from loop_engineering.models import (
    DiscoveryResult,
    EvaluationVerdict,
    HandoffItem,
    LoopEvent,
    MemoryQuery,
)


class DiscoveryProbeProtocol(Protocol):
    """Discovery 阶段协议：感知需求、上下文、风险、可用 skill。"""

    def discover(
        self,
        objective: str,
        prev_events: List[LoopEvent],
        memory: "UnifiedMemoryLayerProtocol",
    ) -> DiscoveryResult:
        """执行 Discovery，返回结构化结果。"""
        ...


class HandoffAdapterProtocol(Protocol):
    """Handoff 阶段协议：将 Discovery 结果转换为工作项并分发执行。"""

    def create_work_items(
        self,
        discovery: DiscoveryResult,
        loop_type: str,
    ) -> List[HandoffItem]:
        """根据 Discovery 结果生成工作项列表。"""
        ...

    def execute(
        self,
        items: List[HandoffItem],
        config: Any,
    ) -> Dict[str, Any]:
        """执行工作项，返回 Generator 执行结果。"""
        ...


class IndependentEvaluatorProtocol(Protocol):
    """Verification 阶段协议：独立评估 Generator 产出。"""

    def evaluate(
        self,
        handoff_items: List[HandoffItem],
        generator_result: Dict[str, Any],
        context: Dict[str, Any],
    ) -> EvaluationVerdict:
        """对 Generator 产出进行独立评估并返回判定。"""
        ...


class UnifiedMemoryLayerProtocol(Protocol):
    """Persistence 阶段协议：统一读写记忆。"""

    def persist_event(self, event: LoopEvent) -> None:
        """持久化单个 Loop 事件。"""
        ...

    def query(self, query: MemoryQuery) -> List[Dict[str, Any]]:
        """统一查询记忆。"""
        ...

    def estimate_token_usage(self) -> int:
        """估算当前累计 token 消耗。"""
        ...


__all__ = [
    "DiscoveryProbeProtocol",
    "HandoffAdapterProtocol",
    "IndependentEvaluatorProtocol",
    "UnifiedMemoryLayerProtocol",
]
