"""Dispatch 中间件接口（H-2 修复）。

用途：audit logging / metrics 收集 / tracing / 动态特性开关。
v1 阶段 middlewares 留空（不引入任何内置 middleware），
但接口先定义，避免 Phase 17+ 再改 dispatcher。
"""
from abc import ABC, abstractmethod
import argparse
from typing import Optional
from dispatcher.plugin_context import PluginContext
from dispatcher.dispatch_result import DispatchResult


class DispatchMiddleware(ABC):
    """Dispatch 中间件接口（H-2 修复）。

    继承此 ABC 实现自定义 middleware，在 dispatcher 构造时注入：

    dispatcher = GoalDispatcher(
        plugins=BUILTIN_PLUGINS,
        middlewares=[MyAuditMiddleware()],
    )
    """

    @abstractmethod
    def before(self, args: argparse.Namespace, ctx: PluginContext) -> None:
        """dispatch 之前调用。

        Args:
            args: argparse 解析结果
            ctx: 共享上下文
        """

    @abstractmethod
    def after(
        self,
        args: argparse.Namespace,
        ctx: PluginContext,
        result: Optional[DispatchResult],
    ) -> None:
        """dispatch 之后调用（result 可能为 None 如果 dispatch 异常）。

        Args:
            args: argparse 解析结果
            ctx: 共享上下文
            result: 调度结果（None 表示 dispatcher 异常退出）
        """


__all__ = ["DispatchMiddleware"]
