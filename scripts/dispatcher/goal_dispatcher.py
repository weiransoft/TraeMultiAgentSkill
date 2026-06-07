"""GoalDispatcher 调度中心（V3 + Phase 17 hot reload 扩展）。

行为契约：
1. 收集所有 plugin（H-6 校验 name/priority 唯一性 + 格式）
2. 启动期 _validate_mutex_declarations（H-1：自指 / 名字存在 / 对称性）
3. validate_mutex(args) 运行时预校验
4. dispatch() 完整流程（风险-3/4/5 全部修复）：
   a. 风险-5：dry_run 入口检查并短路
   b. 中间件 before（H-2）
   c. matches 找出匹配 plugin（snapshot 语义：matched = [...] 后 _plugins 变更不影响本次）
   d. enter_execute 标记 active（_lock 外，避免与 hot_unregister wait_for_idle 死锁）
   e. execute + cleanup（try/finally，exc_to_pass 持有真实异常）
   f. exit_execute 释放 active
   g. 中间件 after（result 变量持有 DispatchResult）
5. 返回 DispatchResult（H-7：结构化结果）
6. Phase 17：hot_register / hot_unregister（与 register 走同一 _validate_plugin_metadata 入口）
7. Phase 17：_lock (RLock) 保护 _plugins 列表
8. Phase 17：_reload_guard 保护正在执行的 plugin
"""
import argparse
import logging
import re
from threading import RLock
from typing import List, Optional, Set

from plugins.base import GoalCommandPlugin
from dispatcher.plugin_context import PluginContext
from dispatcher.dispatch_result import DispatchResult
from dispatcher.middleware import DispatchMiddleware
from dispatcher.reload_guard import ReloadGuard
from dispatcher.errors import (
    MutexViolationError,
    DuplicatePluginNameError,
    DuplicatePriorityError,
    MutexDeclarationError,
    PluginNotFoundError,
    PluginBusyError,
)


# 插件名验证正则（M-2）：kebab-case 强制
_PLUGIN_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


class GoalDispatcher:
    """V3 Goal 命令调度器 + Phase 17 hot reload 扩展。"""

    def __init__(
        self,
        plugins: List[GoalCommandPlugin] = None,
        middlewares: List[DispatchMiddleware] = None,
    ):
        # Phase 17：RLock 保护 _plugins 列表的并发修改
        self._lock: RLock = RLock()
        # Phase 17：ReloadGuard 跟踪活跃 execute（Condition 通知，0 延迟唤醒）
        self._reload_guard: ReloadGuard = ReloadGuard()
        self._plugins: List[GoalCommandPlugin] = []
        self._middlewares: List[DispatchMiddleware] = list(middlewares or [])
        self._logger = logging.getLogger("goal_dispatcher")
        for p in (plugins or []):
            self.register(p)
        self._validate_mutex_declarations()  # H-1 启动期校验

    def _validate_plugin_metadata(
        self, plugin: GoalCommandPlugin, *, require_mutex_check: bool
    ) -> None:
        """Phase 17 v3：register() 和 hot_register() 走同一校验入口（统一约束）。

        校验项（顺序敏感：name 格式 → name 唯一 → priority 唯一 → mutex 可选）：
        1. name 格式（kebab-case，M-2 强制）→ MutexDeclarationError
        2. name 唯一性（H-6 启动期校验）→ DuplicatePluginNameError
        3. priority 唯一性（H-6 启动期校验）→ DuplicatePriorityError
        4. mutex 关系（仅 hot_register 校验，register 启动期已 _validate_mutex_declarations）→ MutexDeclarationError

        Args:
            plugin: 待校验的 plugin 实例
            require_mutex_check: True = 校验 mutex 关系（hot_register 用）
                               False = 跳过 mutex（register 用，依赖启动期校验）

        Raises:
            MutexDeclarationError: name 非法 / mutex 关系不对称
            DuplicatePluginNameError: name 重复
            DuplicatePriorityError: priority 重复

        线程安全：调用方应已持 _lock；本方法不再加锁（避免重入死锁）
        """
        # 1. name 格式校验
        if not _PLUGIN_NAME_RE.match(plugin.name):
            raise MutexDeclarationError(
                f"Plugin name {plugin.name!r} 不符合 kebab-case 规范"
            )
        # 2. name 唯一性校验
        if any(p.name == plugin.name for p in self._plugins):
            raise DuplicatePluginNameError(
                f"Plugin name {plugin.name!r} 重复"
            )
        # 3. priority 唯一性校验
        if any(p.priority == plugin.priority for p in self._plugins):
            raise DuplicatePriorityError(
                f"Plugin priority {plugin.priority} 重复"
            )
        # 4. mutex 关系校验（仅 hot_register 路径）
        if require_mutex_check:
            self._validate_mutex_against_existing(plugin)

    def _validate_mutex_against_existing(
        self, plugin: GoalCommandPlugin
    ) -> None:
        """Phase 17 v3：单 plugin 与现有 plugins 的 mutex 对称性校验。

        校验项（**只校验已存在的引用**）：
        1. plugin.name 不在 plugin.mutex_with 中（自指禁止）
        2. 对称性：对 _plugins 中每个 existing，若 existing.name ∈ plugin.mutex_with
                  则 plugin.name 必须 ∈ existing.mutex_with（**仅校验**已存在的引用）
        3. 不校验"plugin.mutex_with 引用是否都已存在"——支持分步注册
           （启动期 _validate_mutex_declarations 才检查全部引用）

        Args:
            plugin: 待校验的 plugin 实例

        Raises:
            MutexDeclarationError: 自指 / 关系不对称

        线程安全：调用方应已持 _lock；本方法不再加锁
        """
        # 自指校验
        if plugin.name in plugin.mutex_with:
            raise MutexDeclarationError(
                f"Plugin {plugin.name!r} mutex_with 包含自己"
            )
        # 对称性校验：仅校验 _plugins 中已存在的引用
        # 关键：plugin.mutex_with 中引用的不存在的 plugin 不在此检查范围
        # （启动期 _validate_mutex_declarations 会做全量检查）
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

    def _validate_against_active_dispatch(
        self, plugin: GoalCommandPlugin
    ) -> None:
        """Phase 17 v3：检查 plugin 与当前 dispatch 状态是否冲突。

        场景：若新 plugin mutex_with 引用正在执行的 plugin → 抛 MutexViolationError
        理由：避免 reload 期间 mutex 关系被破坏导致执行顺序错乱

        Args:
            plugin: 待 hot_register 的 plugin 实例

        Raises:
            MutexViolationError: mutex_with 引用正在执行的 plugin

        线程安全：仅读 ReloadGuard 状态（无锁）
        """
        active_plugins: Set[str] = self._reload_guard.active_plugin_names()
        for mutex_name in plugin.mutex_with:
            if mutex_name in active_plugins:
                raise MutexViolationError(
                    f"Plugin {plugin.name!r} mutex_with 引用正在执行的 "
                    f"plugin {mutex_name!r}，请稍后重试"
                )

    def _find_plugin(
        self, name: str
    ) -> Optional[GoalCommandPlugin]:
        """Phase 17 v3：通过 name 查找 plugin 实例（内部辅助，调用方持 _lock）。

        Args:
            name: plugin 名称

        Returns:
            找到返回 plugin 实例；未找到返回 None

        线程安全：调用方应已持 _lock
        """
        for p in self._plugins:
            if p.name == name:
                return p
        return None

    def _validate_no_mutex_references(self, name: str) -> None:
        """Phase 17 v3：检查 plugin 被其他 plugin 引用为 mutex_with。

        Args:
            name: 待 unregister 的 plugin 名称

        Raises:
            MutexViolationError: 其他 plugin 的 mutex_with 包含 name

        线程安全：调用方应已持 _lock
        """
        for other in self._plugins:
            if other.name == name:
                continue
            if name in other.mutex_with:
                raise MutexViolationError(
                    f"Plugin {other.name!r} mutex_with 引用 {name!r}，"
                    f"不能 unregister"
                )

    def register(self, plugin: GoalCommandPlugin) -> None:
        """注册插件（按 priority 升序插入，Phase 16 行为不变）。

        Phase 17 v3 修订：调用 _validate_plugin_metadata 统一入口（require_mutex_check=False，
        启动期 _validate_mutex_declarations 已覆盖）。

        Raises:
            MutexDeclarationError: 插件名不符合 kebab-case 规范
            DuplicatePluginNameError: 插件名重复
            DuplicatePriorityError: 插件优先级重复
        """
        with self._lock:
            # 走统一校验入口
            self._validate_plugin_metadata(plugin, require_mutex_check=False)
            # 按 priority 升序插入（稳定排序：相同时按注册顺序）
            self._plugins.append(plugin)
            self._plugins.sort(key=lambda p: p.priority)

    def hot_register(self, plugin: GoalCommandPlugin) -> None:
        """Phase 17 v3：运行时注册 plugin（与 register 走同一校验入口）。

        校验项（比 register 多 2 项）：
        - mutex 关系对称性（与现有 plugin）
        - 不与正在 dispatch 的 plugin mutex 冲突

        线程安全：内部用 RLock 保护 _plugins 列表

        Args:
            plugin: 待注册的 plugin 实例

        Raises:
            MutexDeclarationError: name 非法 / mutex 关系不对称
            DuplicatePluginNameError: name 重复
            DuplicatePriorityError: priority 重复
            MutexViolationError: mutex_with 引用正在执行的 plugin
        """
        with self._lock:
            # 走统一校验入口（require_mutex_check=True 比 register 严格）
            self._validate_plugin_metadata(plugin, require_mutex_check=True)
            # 校验与当前 dispatch 状态的冲突
            self._validate_against_active_dispatch(plugin)
            # 按 priority 升序插入
            self._plugins.append(plugin)
            self._plugins.sort(key=lambda p: p.priority)
            self._logger.info(f"[Dispatcher] hot_register: {plugin.name}")

    def hot_unregister(
        self, name: str, force: bool = False
    ) -> GoalCommandPlugin:
        """Phase 17 v3：运行时卸载 plugin（busy 检查 + force 开关）。

        Args:
            name: 待卸载 plugin 名称
            force: True 跳过 mutex 引用校验（应急场景；仍不跳过 busy 等待）

        Returns:
            被卸载的 plugin 实例

        Raises:
            PluginNotFoundError: plugin 不存在
            PluginBusyError: plugin 正在执行 execute()（force=True 仍 wait 30s 后才抛）
            MutexViolationError: 被其他 plugin 引用为 mutex_with（除非 force=True）

        行为：
        1. busy 检查：plugin 正在执行 → force=True 时 wait_for_idle 30s；force=False 时抛 PluginBusyError
        2. mutex 引用检查：被其他 plugin 引用 → force=True 跳过；force=False 抛 MutexViolationError
        3. 从 _plugins 移除

        线程安全：内部用 RLock 保护 _plugins 列表
        """
        with self._lock:
            # 1. 查找 plugin
            plugin: Optional[GoalCommandPlugin] = self._find_plugin(name)
            if plugin is None:
                raise PluginNotFoundError(name)
            # 2. busy 检查（v2 行为：force 仍 wait_for_idle 30s）
            if self._reload_guard.is_busy(name):
                if not force:
                    raise PluginBusyError(name)
                self._logger.warning(
                    f"[Dispatcher] force unload {name!r}（等待执行完成 30s）"
                )
                # 临时释放 _lock 让 execute 完成（避免与 dispatch 死锁）
                # 注意：wait_for_idle 内部使用 ReloadGuard 自己的 Condition，
                # 不依赖 _lock；这里只是不持 _lock 等待
                # 实现：通过临时释放再获取的 trick（RLock 允许同线程重入）
                pass  # 见下方的 _lock 释放逻辑
        # 注意：wait_for_idle 必须在 _lock 外执行（避免 dispatcher 死锁）
        # 但 RLock 是可重入的，这里 force=True 时主动等待
        if force and self._reload_guard.is_busy(name):
            self._reload_guard.wait_for_idle(name, timeout=30.0)
        # 重新获取 _lock 完成 unregister
        with self._lock:
            # 重新查找（plugin 可能已被其他线程移除）
            plugin = self._find_plugin(name)
            if plugin is None:
                raise PluginNotFoundError(name)
            # 3. mutex 引用检查（仅 force=False 时）
            if not force:
                self._validate_no_mutex_references(name)
            # 4. 移除
            self._plugins.remove(plugin)
            self._logger.info(f"[Dispatcher] hot_unregister: {name}")
            return plugin

    def _validate_mutex_declarations(self) -> None:
        """H-1 启动期 mutex 一致性校验（Phase 16 行为不变）。

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
        with self._lock:
            return list(self._plugins)

    def validate_mutex(self, args: argparse.Namespace) -> None:
        """互斥预校验（在 dispatch 前调用，给出友好错误信息）。

        Raises:
            MutexViolationError: 多个互斥插件同时匹配 args
        """
        with self._lock:
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
        """调度入口（H-7 + 风险-3/4/5 + Phase 17 §2.10 snapshot 全部修复）。

        流程：
        1. 风险-5：dry_run 入口检查
        2. 中间件 before
        3. matches 找出匹配 plugin（snapshot 语义：matched = [...] 后 _plugins 修改不影响本次）
        4. enter_execute 标记 active（_lock 外，避免与 hot_unregister wait_for_idle 死锁）
        5. execute + cleanup（exc_to_pass 持有真实异常）
        6. exit_execute 释放 active
        7. 中间件 after（result 变量持有 DispatchResult）
        8. 返回 DispatchResult
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
            # Phase 17 §2.10：snapshot 语义
            # - matched = [...] 拿 _plugins 当前快照
            # - 即使后续 _plugins 被 hot_register / hot_unregister 修改，
            #   本次 dispatch 仍跑捕获的 plugin 实例
            # - 主流程不持 self._lock（避免与 hot_unregister 的 wait_for_idle 死锁）
            with self._lock:
                matched: List[GoalCommandPlugin] = [
                    p for p in self._plugins if p.matches(args)
                ]
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
            plugin = matched[0]  # 捕获实例引用（snapshot 语义）
            self._logger.info(
                f"[Dispatcher] 匹配插件：{plugin.name} (priority={plugin.priority})"
            )

            # Phase 17：enter_execute 必须在 _lock 外
            # 理由：wait_for_idle 持 self._lock 时，enter 不能重入
            self._reload_guard.enter_execute(plugin.name)
            exc_to_pass: Optional[BaseException] = None
            try:
                # 风险-3：exc_to_pass 持有真实异常
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
                # Phase 17：exit_execute 必须在 _lock 外
                self._reload_guard.exit_execute(plugin.name)
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
