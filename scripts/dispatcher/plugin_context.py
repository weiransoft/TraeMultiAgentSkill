"""PluginContext 共享上下文（V3 引入）。

封装所有插件共享的资源，避免插件之间通过全局变量通信。
H-3 修复：字段完整化（dry_run / verbose / agent_type / config）。
风险-5 修复：dry_run 字段由 dispatcher.dispatch() 入口检查并短路。
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING, Any, Mapping, Dict

if TYPE_CHECKING:
    from loop_goal import GoalRegistry


@dataclass
class PluginContext:
    """插件执行上下文（V3 引入）。

    字段（v1 必含，B-5/H-3/风险-5 修复）：
    - project_root: 项目根目录（Path）
    - log: 日志函数（签名同现有 log(message, level)）
    - registry: GoalRegistry 实例（可选，部分 plugin 懒初始化）
    - dry_run: 模拟模式（bool，默认 False；风险-5 修正：dispatcher.dispatch()
      入口检查并短路返回 DispatchResult(skipped_reason='dry_run')）
    - verbose: 详细日志（bool，默认 False，H-3 修复）
    - agent_type: 智能体类型（str，默认 "auto"，H-3 修复）
    - config: 配置文件（Optional[Mapping]，v1 占位不强制使用，H-3 修复）
    - extra: 扩展字段（Dict[str, Any]，用于 plugin 间传递临时数据）
    """
    project_root: Path
    log: Callable[[str, str], None]
    registry: Optional["GoalRegistry"] = None
    dry_run: bool = False  # 风险-5 修正：dispatcher 入口检查并短路
    verbose: bool = False
    agent_type: str = "auto"
    config: Optional[Mapping[str, Any]] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """__post_init__ 自动转 Path（H-3 修复）。"""
        if not isinstance(self.project_root, Path):
            self.project_root = Path(self.project_root)


__all__ = ["PluginContext"]
