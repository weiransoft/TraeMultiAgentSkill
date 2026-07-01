"""Loop Engineering 配置加载器。

将 CLI 参数、项目级 `.trae/autonomous.yml` 中的 loop_* 字段、以及默认值
合并为统一的 `LoopEngineeringConfig`。

复用 `autonomous.config_loader` 中的 `AutonomousConfig` 和 `SimpleYAMLParser`，
避免重复实现 YAML 解析逻辑。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, Optional, Type

from autonomous.config_loader import AutonomousConfig, load_config
from loop_engineering.models import (
    DiscoveryMode,
    EvaluatorMode,
    LoopEngineeringConfig,
    LoopType,
)


def _coerce_enum(value: Any, enum_cls: Type[Any], default: Any) -> Any:
    """将输入值强制转换为指定 Enum 类型。

    Args:
        value: 原始值（字符串或 Enum 实例）。
        enum_cls: 目标 Enum 类。
        default: 转换失败时的默认值。

    Returns:
        enum_cls 实例。
    """
    if isinstance(value, enum_cls):
        return value
    if value is None:
        return default
    try:
        return enum_cls(str(value).lower())
    except ValueError:
        return default


def build_loop_config(
    args: Optional[argparse.Namespace] = None,
    project_root: Optional[Path] = None,
    autonomous_config: Optional[AutonomousConfig] = None,
) -> LoopEngineeringConfig:
    """构建 LoopEngineeringConfig。

    优先级（从高到低）：
    1. CLI args（如果提供且非 None）
    2. 项目级 autonomous.yml 中的 loop_* 字段
    3. LoopEngineeringConfig 默认值

    Args:
        args: argparse 解析后的 CLI 参数（可选）。
        project_root: 项目根目录（可选，默认当前目录）。
        autonomous_config: 已加载的 AutonomousConfig（可选，避免重复加载）。

    Returns:
        LoopEngineeringConfig: 合并后的配置。
    """
    root = Path(project_root) if project_root else Path(".").resolve()

    # 加载 autonomous 配置（如果未传入）
    auto_cfg = autonomous_config
    if auto_cfg is None:
        try:
            auto_cfg = load_config(root)
        except Exception:
            auto_cfg = AutonomousConfig()

    # 基础默认值
    cfg = LoopEngineeringConfig(project_root=root)

    # 从 autonomous 配置覆盖（仅 loop_* 字段）
    if auto_cfg.loop_engineering_enabled:
        cfg.loop_type = _coerce_enum(auto_cfg.loop_type, LoopType, cfg.loop_type)
        cfg.discovery_mode = _coerce_enum(
            auto_cfg.loop_discovery_mode, DiscoveryMode, cfg.discovery_mode
        )
        cfg.evaluator_mode = _coerce_enum(
            auto_cfg.loop_evaluator_mode, EvaluatorMode, cfg.evaluator_mode
        )
        cfg.human_checkpoint_every = auto_cfg.loop_human_checkpoint_every
        cfg.max_iterations = auto_cfg.loop_max_iterations
        cfg.max_tokens = auto_cfg.loop_max_tokens
        cfg.sampling_read_ratio = auto_cfg.loop_sampling_read_ratio

    # 从 CLI args 覆盖（最高优先级）
    if args is not None:
        cfg.loop_type = _coerce_enum(
            getattr(args, "loop_type", None), LoopType, cfg.loop_type
        )
        cfg.discovery_mode = _coerce_enum(
            getattr(args, "loop_discovery", None), DiscoveryMode, cfg.discovery_mode
        )
        cfg.evaluator_mode = _coerce_enum(
            getattr(args, "loop_evaluator", None), EvaluatorMode, cfg.evaluator_mode
        )
        cfg.human_checkpoint_every = _coerce_int(
            getattr(args, "loop_human_checkpoint_every", None),
            cfg.human_checkpoint_every,
        )
        cfg.max_iterations = _coerce_int(
            getattr(args, "loop_max_iterations", None), cfg.max_iterations
        )
        cfg.max_tokens = _coerce_int(
            getattr(args, "loop_max_tokens", None), cfg.max_tokens
        )
        cfg.sampling_read_ratio = _coerce_float(
            getattr(args, "loop_sampling_read_ratio", None), cfg.sampling_read_ratio
        )
        if getattr(args, "project_root", None):
            cfg.project_root = Path(args.project_root)
        if getattr(args, "task", None):
            # task 作为 stop_when 的默认补充，但不覆盖显式 stop_when
            if not cfg.stop_when:
                cfg.stop_when = str(args.task)

    return cfg


def _coerce_int(value: Any, default: int) -> int:
    """将值强制转换为 int，失败返回默认值。"""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float) -> float:
    """将值强制转换为 float，失败返回默认值。"""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def loop_config_to_dict(cfg: LoopEngineeringConfig) -> Dict[str, Any]:
    """将 LoopEngineeringConfig 序列化为 dict（便于日志和持久化）。"""
    return {
        "loop_type": cfg.loop_type.value,
        "discovery_mode": cfg.discovery_mode.value,
        "evaluator_mode": cfg.evaluator_mode.value,
        "max_iterations": cfg.max_iterations,
        "max_tokens": cfg.max_tokens,
        "human_checkpoint_every": cfg.human_checkpoint_every,
        "sampling_read_ratio": cfg.sampling_read_ratio,
        "stop_when": cfg.stop_when,
        "stage_order": list(cfg.stage_order),
        "project_root": str(cfg.project_root),
        "run_dir": cfg.run_dir,
        "notes_path": cfg.notes_path,
        "test_command": cfg.test_command,
        "test_timeout_sec": cfg.test_timeout_sec,
        "security_analyzer": cfg.security_analyzer,
        "auto_commit": cfg.auto_commit,
        "extra": dict(cfg.extra),
    }


__all__ = [
    "build_loop_config",
    "loop_config_to_dict",
]
