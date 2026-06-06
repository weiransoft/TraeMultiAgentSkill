"""V3 插件包：定义 BUILTIN_PLUGINS 单一注册真相源。

任何模块（facade / dispatcher / tests）都从这里 import plugin 列表，
避免分散注册导致的不一致。

风险-9 修正：plugin 必须满足 stateless 契约
- BUILTIN_PLUGINS 在模块加载时构造 5 个 plugin 实例
- Python 进程内所有 dispatcher 共享这同一组实例
- 若 plugin 持有可变状态（self.field），测试间会状态泄漏
- 当前 5 个内置 plugin 都是 stateless（仅返回常量 / 调用函数），安全
- 未来新增 plugin 时必须：
  1. 不持有实例变量状态
  2. 或在 execute() 入口 self-reinit
  3. 契约测试 test_v3_plugin_contract.py 增加
     test_plugin_instances_independent 验证
"""

from plugins.base import GoalCommandPlugin
from plugins.cancel import GoalCancelPlugin
from plugins.graph import GoalGraphPlugin
from plugins.resume import GoalResumePlugin
from plugins.multi_goal import MultiGoalPlugin
from plugins.loop import LoopGoalPlugin


# 5 个内置插件（H-8 契约测试覆盖）
# 风险-9 修正：所有 plugin 实例必须 stateless（不持有实例状态）
BUILTIN_PLUGINS: list = [
    GoalCancelPlugin(),
    GoalGraphPlugin(),
    GoalResumePlugin(),
    MultiGoalPlugin(),
    LoopGoalPlugin(),
]


__all__ = [
    "GoalCommandPlugin",
    "GoalCancelPlugin",
    "GoalGraphPlugin",
    "GoalResumePlugin",
    "MultiGoalPlugin",
    "LoopGoalPlugin",
    "BUILTIN_PLUGINS",
]
