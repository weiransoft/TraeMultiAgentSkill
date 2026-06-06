"""V3 Plugin 抽象基类。

任何 CLI 模式都必须实现本接口。dispatcher 通过该接口调度。

关键设计：插件自带元数据（priority / mutex_with / requires_task），
不依赖外部配置，避免 god module 的"中心化硬编码"问题。

契约：
1. 所有抽象方法必须实现（6 个：name/priority/mutex_with/requires_task/matches/execute）
2. cleanup 默认 no-op，可被 plugin 覆盖（H-5）
3. plugin 必须 stateless（无实例变量状态；风险-9 修正）
4. plugin name 满足 ^[a-z][a-z0-9-]*$（M-2 强制）
5. CLI flag 派生：dispatcher 内部用 f"--{name}"（B-4 修复：删 cli_flag property）
"""
from abc import ABC, abstractmethod
from typing import Set, Optional
import argparse
from dispatcher.plugin_context import PluginContext


class GoalCommandPlugin(ABC):
    """V3 Goal 命令插件接口。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """插件唯一名称（用于日志 / 错误信息 / mutex 引用 / CLI flag 派生）。

        约定：
        - 满足 ^[a-z][a-z0-9-]*$（kebab-case，M-2 强制）
        - 与 CLI flag 后半段保持一致（去掉 -- 前缀）
        - 例：'goal-cancel' / 'goal-graph' / 'goal-resume'
        - dispatcher 内部用 f"--{name}" 派生 CLI flag（B-4 修复）
        """

    @property
    @abstractmethod
    def priority(self) -> int:
        """调度优先级（数字越小越优先）。

        约定（间隔 10 预留 gap，M-1 优化）：
        - 0（DESTROY）：破坏性最高（cancel）
        - 10（READONLY）：只读（graph）
        - 20~30（STATE_MUTATION_*）：状态变更（resume / multi_goal）
        - 40~50（LOOP_*）：循环 / 长期运行（loop）

        唯一性约束：H-6 dispatch 时 register() 强制检查
        """

    @property
    @abstractmethod
    def mutex_with(self) -> Set[str]:
        """互斥的插件名称集合（基于 plugin.name，非 cli_flag）。

        例：{'goal-resume', 'multi-goal', 'goal-cancel', 'goal-graph'}
        表示这些插件不能与本插件同时启用。

        一致性约束：H-1 dispatcher 启动期校验
        - 不含自己（自指）
        - 引用的每个名字都有已注册 plugin 对应
        - 对称性：A.mutex_with ⊇ {B.name} iff B.mutex_with ⊇ {A.name}
        """

    @property
    @abstractmethod
    def requires_task(self) -> bool:
        """是否要求 --task 参数（默认 False，所有 plugin 都不需要）。"""

    @abstractmethod
    def matches(self, args: argparse.Namespace) -> bool:
        """检查是否匹配（args 中相应字段非 None）。

        例：cancel_plugin.matches(args) → return args.goal_cancel is not None
        """

    @abstractmethod
    def execute(self, args: argparse.Namespace, ctx: PluginContext) -> bool:
        """执行插件逻辑。

        Args:
            args: argparse 解析结果
            ctx: 共享上下文（project_root / log / registry / dry_run 等）

        Returns:
            bool: True 表示成功；False 表示失败
        """

    def cleanup(self, ctx: PluginContext, exc: Optional[BaseException]) -> None:
        """资源回收钩子（H-5 契约 + 风险-3 修正：exc 真实传递）。

        默认 no-op。Plugin 实现时必须保证幂等（可被多次调用）。
        dispatcher 在 try/finally 中调用，无论 execute 成功/失败/异常。
        """
        # 默认 no-op（无状态 plugin 不需要清理）

    # === 便捷方法（子类可继承也可覆盖） ===

    def get_arg(self, args: argparse.Namespace, key: str, default=None):
        """安全获取 args 属性（避免 AttributeError）。"""
        return getattr(args, key, default)

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} "
            f"name={self.name!r} priority={self.priority}>"
        )


__all__ = ["GoalCommandPlugin"]
