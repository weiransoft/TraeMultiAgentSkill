"""Phase 18: 4 阶段 Handler 抽象基类。

设计目标：
- 抽象 StageHandler ABC，4 个具体 handler 继承
- handle(iter_ctx) -> StageResult
- 不抛异常（返回 StageResult）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from autonomous.loop_controller import IterationContext


@dataclass
class StageResult:
    """单阶段处理结果。

    字段说明：
    - kind: success / failed / retriable / fatal
    - summary: 摘要
    - artifacts: 阶段产出（dict）
    - error: 错误信息
    """

    kind: str = "success"
    summary: str = ""
    artifacts: Dict[str, Any] = field(default_factory=dict)
    error: str = ""


class StageHandler:
    """阶段 Handler 抽象基类。

    设计原则：
    1. handle() 不抛异常（返回 StageResult）
    2. 子类必须实现 name / kind 属性
    3. 子类实现 do_handle() 业务逻辑
    """

    name: str = ""
    kind: str = ""  # plan / dev / verify / fix

    def handle(self, iter_ctx: "IterationContext") -> StageResult:
        """处理单阶段。

        Args:
            iter_ctx: 迭代上下文

        Returns:
            StageResult: 阶段结果
        """
        try:
            return self.do_handle(iter_ctx)
        except Exception as e:
            import traceback
            return StageResult(
                kind="fatal",
                summary=f"handler 异常: {type(e).__name__}: {e}",
                error=traceback.format_exc(),
            )

    def do_handle(self, iter_ctx: "IterationContext") -> StageResult:
        """实际处理逻辑（子类实现）。"""
        raise NotImplementedError


__all__ = ["StageHandler", "StageResult"]
