"""GoalDispatcher 调度中心（V3 引入）。

行为契约：
1. 收集所有 plugin（H-6 校验 name/priority 唯一性 + 格式）
2. 启动期 _validate_mutex_declarations（H-1：自指 / 名字存在 / 对称性）
3. validate_mutex(args) 运行时预校验
4. dispatch() 完整流程（风险-3/4/5 全部修复）：
   a. 风险-5：dry_run 入口检查并短路
   b. 中间件 before（H-2）
   c. matches 找出匹配 plugin
   d. execute + cleanup（try/finally，exc_to_pass 持有真实异常）
   e. 中间件 after（result 变量持有 DispatchResult）
5. 返回 DispatchResult（H-7：结构化结果）
"""
import argparse
import logging
import re
from typing import List, Optional
from plugins.base import GoalCommandPlugin
from dispatcher.plugin_context import PluginContext
from dispatcher.dispatch_result import DispatchResult
from dispatcher.middleware import DispatchMiddleware
from dispatcher.errors import (
    MutexViolationError,
    DuplicatePluginNameError,
    DuplicatePriorityError,
    MutexDeclarationError,
)


# 插件名验证正则（M-2）：kebab-case 强制
_PLUGIN_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


class GoalDispatcher:
    """V3 Goal 命令调度器。"""

    def __init__(
        self,
        plugins: List[GoalCommandPlugin] = None,
        middlewares: List[DispatchMiddleware] = None,
    ):
        self._plugins: List[GoalCommandPlugin] = []
        self._middlewares: List[DispatchMiddleware] = list(middlewares or [])
        self._logger = logging.getLogger("goal_dispatcher")
        for p in (plugins or []):
            self.register(p)
        self._validate_mutex_declarations()  # H-1 启动期校验

    def register(self, plugin: GoalCommandPlugin) -> None:
        """注册插件（按 priority 升序插入）。

        Raises:
            MutexDeclarationError: 插件名不符合 kebab-case 规范
            DuplicatePluginNameError: 插件名重复
            DuplicatePriorityError: 插件优先级重复
        """
        # name 格式校验（M-2 强制）
        if not _PLUGIN_NAME_RE.match(plugin.name):
            raise MutexDeclarationError(
                f"Plugin name {plugin.name!r} 不符合 kebab-case 规范"
            )
        # name 唯一性（H-6 修复）
        if any(p.name == plugin.name for p in self._plugins):
            raise DuplicatePluginNameError(
                f"Plugin name {plugin.name!r} 重复"
            )
        # priority 唯一性（H-6 修复）
        if any(p.priority == plugin.priority for p in self._plugins):
            raise DuplicatePriorityError(
                f"Plugin priority {plugin.priority} 重复"
            )
        # 按 priority 升序插入（稳定排序：相同时按注册顺序）
        self._plugins.append(plugin)
        self._plugins.sort(key=lambda p: p.priority)

    def _validate_mutex_declarations(self) -> None:
        """H-1 启动期 mutex 一致性校验。

        Raises:
            MutexDeclarationError: 自指 / 名字不存在 / 关系不对称
        """
        names = {p.name for p in self._plugins}
        for plugin in self._plugins:
            # 自指
            if plugin.name in plugin.mutex_with:
                raise MutexDeclarationError(
                    f"Plugin {plugin.name!r} mutex_with 包含自己"
                )
            # 名字存在性
            for mutex_name in plugin.mutex_with:
                if mutex_name not in names:
                    raise MutexDeclarationError(
                        f"Plugin {plugin.name!r} mutex_with 引用不存在"
                        f"的 plugin {mutex_name!r}"
                    )
            # 对称性
            for other in self._plugins:
                if other.name == plugin.name:
                    continue
                a_mutex_b = other.name in plugin.mutex_with
                b_mutex_a = plugin.name in other.mutex_with
                if a_mutex_b != b_mutex_a:
                    raise MutexDeclarationError(
                        f"Plugin {plugin.name!r} 与 {other.name!r} "
                        f"mutex 关系不对称"
                    )

    def list_plugins(self) -> List[GoalCommandPlugin]:
        """返回所有已注册插件（只读副本，按 priority 升序）。"""
        return list(self._plugins)

    def validate_mutex(self, args: argparse.Namespace) -> None:
        """互斥预校验（在 dispatch 前调用，给出友好错误信息）。

        Raises:
            MutexViolationError: 多个互斥插件同时匹配 args
        """
        matched_names = {p.name for p in self._plugins if p.matches(args)}
        for name in matched_names:
            plugin = next(p for p in self._plugins if p.name == name)
            for mutex_name in plugin.mutex_with:
                if mutex_name in matched_names:
                    raise MutexViolationError(
                        f"插件 --{plugin.name} 与 {mutex_name} 互斥"
                    )

    def dispatch(
        self, args: argparse.Namespace, ctx: PluginContext
    ) -> DispatchResult:
        """调度入口（H-7 + 风险-3/4/5 全部修复）。

        流程：
        1. 风险-5：dry_run 入口检查
        2. 中间件 before
        3. matches 找出匹配 plugin
        4. execute + cleanup（exc_to_pass 持有真实异常）
        5. 中间件 after（result 变量持有 DispatchResult）
        6. 返回 DispatchResult
        """
        # 风险-5：dry_run 入口短路
        if getattr(ctx, "dry_run", False):
            return DispatchResult(
                matched_plugin=None,
                success=True,
                error=None,
                skipped_reason="dry_run",
            )

        # 风险-4：result 变量在外层 finally 可见
        result: Optional[DispatchResult] = None
        # 中间件 before（异常用 try/except 兜底，不阻断 dispatch）
        for mw in self._middlewares:
            try:
                mw.before(args, ctx)
            except Exception as e:
                self._logger.warning(f"[Dispatcher] middleware.before 异常：{e}")

        try:
            matched = [p for p in self._plugins if p.matches(args)]
            if not matched:
                result = DispatchResult(
                    matched_plugin=None,
                    success=False,
                    error=None,
                    skipped_reason="no_match",
                )
                return result
            if len(matched) > 1:
                # H-4 v1 严格：多个 plugin 匹配视为错误
                names = [p.name for p in matched]
                raise MutexViolationError(
                    f"多个插件同时匹配（args 解析层应已阻止）：{names}"
                )
            plugin = matched[0]
            self._logger.info(
                f"[Dispatcher] 匹配插件：{plugin.name} (priority={plugin.priority})"
            )

            # 风险-3：exc_to_pass 持有真实异常
            exc_to_pass: Optional[BaseException] = None
            try:
                success = plugin.execute(args, ctx)
                result = DispatchResult(
                    matched_plugin=plugin.name,
                    success=success,
                    error=None,
                )
                return result
            except BaseException as exc:
                exc_to_pass = exc
                result = DispatchResult(
                    matched_plugin=plugin.name,
                    success=False,
                    error=exc,
                )
                return result
            finally:
                # cleanup 一定执行（H-5 契约 + 风险-3 修正）
                try:
                    plugin.cleanup(ctx, exc_to_pass)
                except Exception as e:
                    self._logger.warning(
                        f"[Dispatcher] plugin.cleanup 异常：{e}"
                    )
        finally:
            # 中间件 after（风险-4 修正：传真实 DispatchResult）
            for mw in self._middlewares:
                try:
                    mw.after(args, ctx, result)
                except Exception as e:
                    self._logger.warning(
                        f"[Dispatcher] middleware.after 异常：{e}"
                    )


__all__ = ["GoalDispatcher"]
