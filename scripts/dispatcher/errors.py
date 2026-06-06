"""V3 调度器异常（5 类）。

设计原则：
- 每类异常语义单一（不混用通用 Exception）
- 错误消息含具体 plugin name / priority / mutex name 便于调试
- 所有异常继承 BaseException 子类（推荐 Exception）
- dispatcher 内部 catch 时区分 MutexViolationError（用户可恢复）
  和 MutexDeclarationError（程序员错误，应启动期失败）
"""


class DispatcherError(Exception):
    """所有 dispatcher 异常的基类（统一捕获时用）。"""


class MutexViolationError(DispatcherError):
    """运行时 mutex 冲突：用户传入 args 触发多个互斥 plugin。

    抛出场景：用户同时传 --goal-cancel + --goal-graph。
    错误消息应含具体 plugin name 便于 CLI 错误提示。
    """


class NoMatchingPluginError(DispatcherError):
    """无 plugin 匹配 args。

    抛出场景：v1 阶段所有 CLI flag 都被处理，理论上不会触发。
    保留作为防御性异常。
    """


class DuplicatePluginNameError(DispatcherError):
    """plugin name 重复（H-6 启动期校验）。

    抛出场景：两个 plugin 自报相同 name。
    错误消息应含重复 name 便于定位。
    """


class DuplicatePriorityError(DispatcherError):
    """plugin priority 重复（H-6 启动期校验）。

    抛出场景：两个 plugin 自报相同 priority。
    错误消息应含重复 priority 便于定位。
    """


class MutexDeclarationError(DispatcherError):
    """plugin mutex 声明错误（H-1 启动期校验）。

    抛出场景：
    - mutex_with 包含自己（自指）
    - mutex_with 引用不存在的 plugin name
    - A.mutex_with 包含 B 但 B.mutex_with 不包含 A（不对称）
    """


__all__ = [
    "DispatcherError",
    "MutexViolationError",
    "NoMatchingPluginError",
    "DuplicatePluginNameError",
    "DuplicatePriorityError",
    "MutexDeclarationError",
]
