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


class PluginNotFoundError(DispatcherError):
    """v3 新增：hot_unregister 时 plugin 不存在。

    抛出场景：dispatcher.hot_unregister('unknown-plugin')。
    错误消息应含被引用的 plugin name 便于调用方定位。

    线程安全：异常对象本身不可变，由 hot_unregister 在持有 _lock 时抛出。
    """

    def __init__(self, name: str) -> None:
        """构造 plugin-not-found 异常。

        Args:
            name: 不存在的 plugin 名称（用于错误消息）
        """
        super().__init__(f"Plugin 不存在：{name!r}")
        self.name = name


class PluginBusyError(DispatcherError):
    """v3 新增：plugin 正在执行 execute()，无法立即 unregister。

    抛出场景：dispatcher.hot_unregister('foo') 而 foo 当前正被 dispatch。
    不适用 force=True 场景（force 会 wait_for_idle 30s，仍失败才抛）。
    错误消息应含正在执行的 plugin name 便于调用方决定重试或 force。
    """

    def __init__(self, name: str) -> None:
        """构造 plugin-busy 异常。

        Args:
            name: 正在执行的 plugin 名称
        """
        super().__init__(f"Plugin {name!r} 正在执行，请稍后重试")
        self.name = name


class DropInLoadError(DispatcherError):
    """v3 新增：drop-in 文件加载失败。

    抛出场景：
    - 文件不存在
    - importlib.util.spec_from_file_location 失败
    - exec_module 抛 ImportError / SyntaxError
    - 文件无 GoalCommandPlugin 子类
    错误消息应含失败的文件路径便于 drop-in 目录定位。
    """

    def __init__(self, path_or_msg) -> None:
        """构造 drop-in load 异常。

        Args:
            path_or_msg: 文件路径（str / Path）或完整错误消息
        """
        msg = str(path_or_msg)
        super().__init__(f"drop-in 加载失败：{msg}")
        self.path = str(path_or_msg)


class DropInPathError(DispatcherError):
    """v3 新增：drop-in 路径不安全（P0-7 路径穿越防护）。

    抛出场景：
    - 绝对路径（拒绝 project_root 外引用）
    - resolve() 后跳出 project_root（含软链跳出）
    - 路径不是目录且 parent 也不存在
    错误消息应含具体路径便于 watcher / facade 定位问题来源。
    """

    def __init__(self, path_or_msg) -> None:
        """构造 drop-in path 异常。

        Args:
            path_or_msg: 不安全路径（str / Path）或完整错误消息
        """
        msg = str(path_or_msg)
        super().__init__(f"drop-in 路径不安全：{msg}")
        self.path = str(path_or_msg)


__all__ = [
    "DispatcherError",
    "MutexViolationError",
    "NoMatchingPluginError",
    "DuplicatePluginNameError",
    "DuplicatePriorityError",
    "MutexDeclarationError",
    "PluginNotFoundError",
    "PluginBusyError",
    "DropInLoadError",
    "DropInPathError",
]
