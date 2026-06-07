"""ReloadGuard 组件（Phase 17 §2.5）。

职责：在 reload 期间保护正在执行的 plugin 不会被错误 unload。
- 引用计数：同 plugin 并发 execute 正确计数
- Condition 通知：wait_for_idle 立即唤醒（不切片）
- 不平衡 counter：exit 多于 enter 的防御性 metrics

线程安全：所有方法使用内部 RLock 串行化。
"""
import logging
import threading
import time
from threading import Condition, RLock
from typing import Dict, Set


class ReloadGuard:
    """reload 操作的事务性保护（v3 重写：Condition 替代 Event）。

    设计要点：
    - 单一 Condition 内部 RLock 串行化所有状态变更
    - Dict[str, int] 记录每个 plugin 的活跃 execute 计数
    - exit_execute 触发 notify_all → 等待者立即唤醒（0 延迟）
    - 不平衡 counter 暴露给外部 metrics 监控
    """

    # 默认 idle 等待超时（10s；hot_unregister(force=True) 路径用 30s）
    DEFAULT_IDLE_TIMEOUT = 10.0

    def __init__(self) -> None:
        """初始化 ReloadGuard。

        说明：
        - _cond 内部 RLock 同时作为 _active_counts / _unbalanced_exit_count 的写锁
        - 启动时无任何 active execute
        """
        # Condition 内部使用 RLock；wait()/notify_all() 自动加锁
        self._cond: Condition = Condition(RLock())
        # plugin name → 活跃 execute 计数（支持同 plugin 并发）
        self._active_counts: Dict[str, int] = {}
        # 不平衡退出计数（enter/exit 不配对时 +1，外部可读）
        self._unbalanced_exit_count: int = 0
        self._logger: logging.Logger = logging.getLogger("reload_guard")

    def enter_execute(self, plugin_name: str) -> None:
        """标记 plugin 进入 execute。

        Args:
            plugin_name: 正在执行的 plugin 名称

        线程安全：持锁内增加计数。
        """
        # Condition 上下文管理器等价于 acquire/release RLock
        with self._cond:
            # 若 plugin_name 已有计数则 +1，否则初始化为 1
            self._active_counts[plugin_name] = (
                self._active_counts.get(plugin_name, 0) + 1
            )

    def exit_execute(self, plugin_name: str) -> None:
        """标记 plugin 退出 execute。

        Args:
            plugin_name: 退出执行的 plugin 名称

        行为：
        - 正常路径：count > 0 → 减 1，归零时从 dict 删除 + notify_all 唤醒等待者
        - 异常路径：count == 0（exit 多于 enter）→ 不平衡 counter +1 + error log，不抛异常

        线程安全：持锁内修改计数 + 通知。
        """
        with self._cond:
            current: int = self._active_counts.get(plugin_name, 0)
            if current <= 0:
                # 防御性：exit 比 enter 多 → 严重 bug 信号
                self._unbalanced_exit_count += 1
                self._logger.error(
                    f"[ReloadGuard] exit_execute({plugin_name!r}) 计数为 0，"
                    f"enter/exit 不配对！total={self._unbalanced_exit_count}"
                )
                # 不抛异常：避免污染主流程（dispatcher.execute 路径）
                return
            # 减 1
            self._active_counts[plugin_name] = current - 1
            # 归零 → 从 dict 移除（节省内存 + active_plugin_names() 准确）
            if self._active_counts[plugin_name] == 0:
                del self._active_counts[plugin_name]
                # 通知所有等待者（可能有多个不同 plugin 在等）
                # Condition 0 延迟通知（vs Event 1s 切片）
                self._cond.notify_all()

    def is_busy(self, plugin_name: str) -> bool:
        """检查 plugin 当前是否有活跃 execute。

        Args:
            plugin_name: 待检查的 plugin 名称

        Returns:
            True = 至少 1 个 execute 未退出；False = 完全 idle

        线程安全：持锁内读 dict。
        """
        with self._cond:
            return self._active_counts.get(plugin_name, 0) > 0

    def active_plugin_names(self) -> Set[str]:
        """返回当前所有活跃 plugin 名称的副本。

        Returns:
            活跃 plugin 名称集合（每个 plugin 至少 1 个 execute 未退出）

        线程安全：持锁内构造新 set 返回（避免外部修改内部状态）。
        """
        with self._cond:
            return {
                name
                for name, count in self._active_counts.items()
                if count > 0
            }

    def wait_for_idle(
        self, plugin_name: str, timeout: float = DEFAULT_IDLE_TIMEOUT
    ) -> bool:
        """阻塞等待 plugin 进入 idle 状态。

        Args:
            plugin_name: 等待的 plugin 名称
            timeout: 最大等待秒数（默认 10s）

        Returns:
            True = 已 idle（count == 0）；False = 超时仍有 execute 在跑

        实现：Condition.wait 被 notify 时立即唤醒（vs Event.wait 1s 切片）
        """
        # 计算 deadline（避免累积 sleep 误差）
        deadline: float = time.time() + timeout
        with self._cond:
            # 循环等待：被 notify 后再次检查（防止 spurious wakeup）
            while self._active_counts.get(plugin_name, 0) > 0:
                remaining: float = deadline - time.time()
                if remaining <= 0:
                    return False
                # Condition.wait 释放 RLock + 阻塞，被 notify 时重新获取 RLock
                self._cond.wait(timeout=remaining)
            return True

    @property
    def unbalanced_exit_count(self) -> int:
        """暴露给外部 metrics 的不平衡退出计数（P2-7）。

        Returns:
            累计 exit 多于 enter 的次数

        用途：监控 / Sentry 告警 / 单元测试
        """
        with self._cond:
            return self._unbalanced_exit_count


__all__ = ["ReloadGuard"]
