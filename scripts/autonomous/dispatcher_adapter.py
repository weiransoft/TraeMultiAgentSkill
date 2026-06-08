"""Phase 18: DispatcherAdapter - 复用现有 GoalDispatcher 的适配层。

设计目标：
- 不修改 V3 dispatcher（facade/dispatcher/plugin 零修改）
- 构造 PluginContext 并调用 GoalDispatcher.dispatch()
- 把 dispatch 结果包装为 AdapterInvokeResult 返回
- 捕获异常并以 FATAL 形式返回（不抛异常给调用方）
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class AdapterInvokeResult:
    """适配器调用结果。

    字段说明：
    - success: 是否成功
    - kind: success / failed / retriable / fatal
    - output: 智能体原始输出
    - summary: 人类可读摘要
    - tokens: 估算 token 消耗
    - skills_used: 实际使用的 skills 列表
    - error: 异常对象（如果有）
    - error_trace: 异常 traceback
    """

    success: bool
    kind: str = "failed"  # success / failed / retriable / fatal
    output: str = ""
    summary: str = ""
    tokens: int = 0
    skills_used: List[str] = field(default_factory=list)
    error: Optional[BaseException] = None
    error_trace: str = ""


class DispatcherAdapter:
    """Ralph 风格 Dispatcher 适配器。

    设计原则：
    1. 不修改 V3 dispatcher
    2. 调用 facade.dispatch_through_v3()（如果可用）或 facade._dispatch_through_v3()
    3. 失败以 FATAL 包装（不抛异常）
    4. 记录真实错误 trace
    """

    def __init__(
        self,
        facade_module: Optional[Any] = None,
        log: Optional[Any] = None,
    ):
        """构造 DispatcherAdapter。

        Args:
            facade_module: facade 模块（默认延迟导入）
            log: 日志函数
        """
        self._facade = facade_module
        self._log = log or (lambda level, msg: None)
        self._dispatcher_available: Optional[bool] = None

    # ------------------------------------------------------------------ #
    # 公共 API                                                            #
    # ------------------------------------------------------------------ #

    def is_available(self) -> bool:
        """检测 dispatcher 是否可用（不实际调用）。"""
        if self._dispatcher_available is not None:
            return self._dispatcher_available
        try:
            facade = self._get_facade()
            self._dispatcher_available = (
                facade is not None
                and hasattr(facade, "_dispatch_through_v3")
            )
        except (ImportError, AttributeError):
            self._dispatcher_available = False
        return self._dispatcher_available

    def invoke(
        self,
        task: str,
        agent: str = "auto",
        auto_skills: Optional[List[Dict[str, Any]]] = None,
        extra_context: Optional[Dict[str, Any]] = None,
        timeout_sec: float = 600.0,
    ) -> AdapterInvokeResult:
        """调用 dispatcher 执行一次任务。

        Args:
            task: 任务描述
            agent: 智能体名称（"auto" / "architect" / "solo_coder" 等）
            auto_skills: 自动加载的 skills（注入到 context）
            extra_context: 额外上下文
            timeout_sec: 超时（秒）

        Returns:
            AdapterInvokeResult: 调用结果

        行为：
        1. 构造 argparse.Namespace 模拟命令行参数
        2. 调用 facade._dispatch_through_v3(args)
        3. 包装结果为 AdapterInvokeResult
        4. 异常 → FATAL
        """
        if not task or not task.strip():
            return AdapterInvokeResult(
                success=False,
                kind="fatal",
                summary="任务描述为空",
            )
        if not self.is_available():
            return AdapterInvokeResult(
                success=False,
                kind="fatal",
                summary="GoalDispatcher 不可用（facade 模块未找到或缺少 _dispatch_through_v3）",
            )
        facade = self._get_facade()
        if facade is None:
            return AdapterInvokeResult(
                success=False,
                kind="fatal",
                summary="无法导入 facade 模块",
            )
        # 构造 args
        import argparse
        args = argparse.Namespace(
            task=task,
            agent=agent,
            consensus=False,
            explain=False,
            match_strategy="auto",
            project_full_lifecycle=False,
            resume=False,
            goal="",
            goal_desc="",
            criteria=None,
            convergence_window=3,
            loop=False,
            max_iterations=1,
            hot_reload=False,
            hot_reload_dir=None,
            hot_reload_interval=5.0,
            # 注入 autonomous 相关字段
            autonomous=True,
            auto_skills=auto_skills or [],
            auto_context=extra_context or {},
        )
        # 调用 dispatcher
        try:
            self._log("info", f"[DispatcherAdapter] 调用 dispatcher: task={task[:60]}...")
            rc = facade._dispatch_through_v3(args)
            # rc 是退出码（0=成功）
            if rc == 0:
                return AdapterInvokeResult(
                    success=True,
                    kind="success",
                    output=f"dispatcher 返回码 {rc}",
                    summary=f"任务执行成功（rc={rc}）",
                    skills_used=[s.get("name", "") for s in (auto_skills or []) if s.get("name")],
                )
            elif rc in (1, 2):
                # 用户级错误 / 部分失败
                return AdapterInvokeResult(
                    success=False,
                    kind="retriable",
                    output=f"dispatcher 返回码 {rc}",
                    summary=f"任务执行失败（rc={rc}，可重试）",
                )
            else:
                # 未知错误码
                return AdapterInvokeResult(
                    success=False,
                    kind="fatal",
                    output=f"dispatcher 返回码 {rc}",
                    summary=f"dispatcher 致命错误（rc={rc}）",
                )
        except (ImportError, AttributeError) as e:
            return AdapterInvokeResult(
                success=False,
                kind="fatal",
                summary=f"dispatcher 缺少依赖: {e}",
                error=e,
                error_trace=traceback.format_exc(),
            )
        except Exception as e:
            # 未知异常 → FATAL
            return AdapterInvokeResult(
                success=False,
                kind="fatal",
                summary=f"dispatcher 异常: {type(e).__name__}: {e}",
                error=e,
                error_trace=traceback.format_exc(),
            )

    def invoke_with_args(self, args) -> AdapterInvokeResult:
        """使用预先构造的 args 调用 dispatcher。

        Args:
            args: argparse.Namespace 或类似对象

        Returns:
            AdapterInvokeResult: 调用结果
        """
        if not self.is_available():
            return AdapterInvokeResult(
                success=False,
                kind="fatal",
                summary="GoalDispatcher 不可用",
            )
        facade = self._get_facade()
        if facade is None:
            return AdapterInvokeResult(
                success=False,
                kind="fatal",
                summary="无法导入 facade 模块",
            )
        try:
            self._log("info", "[DispatcherAdapter] invoke_with_args 调用")
            rc = facade._dispatch_through_v3(args)
            if rc == 0:
                return AdapterInvokeResult(
                    success=True,
                    kind="success",
                    output=f"dispatcher 返回码 {rc}",
                    summary=f"任务执行成功（rc={rc}）",
                )
            return AdapterInvokeResult(
                success=False,
                kind="retriable" if rc in (1, 2) else "fatal",
                output=f"dispatcher 返回码 {rc}",
                summary=f"任务执行失败（rc={rc}）",
            )
        except Exception as e:
            return AdapterInvokeResult(
                success=False,
                kind="fatal",
                summary=f"dispatcher 异常: {type(e).__name__}: {e}",
                error=e,
                error_trace=traceback.format_exc(),
            )

    # ------------------------------------------------------------------ #
    # 内部辅助                                                            #
    # ------------------------------------------------------------------ #

    def _get_facade(self):
        """获取 facade 模块（延迟导入）。"""
        if self._facade is not None:
            return self._facade
        try:
            # 优先从 scripts 目录导入
            import sys
            scripts_dir = str(Path(__file__).resolve().parent.parent)
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            import facade as _facade  # type: ignore
            self._facade = _facade
            return _facade
        except (ImportError, ModuleNotFoundError):
            return None


__all__ = ["DispatcherAdapter", "AdapterInvokeResult"]
