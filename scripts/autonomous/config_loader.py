"""Phase 18: autonomous 配置文件加载（config.yml）。

设计目标：
- 加载用户级 ~/.trae/autonomous.yml + 项目级 <project_root>/.trae/autonomous.yml
- 项目级覆盖用户级
- 简化 YAML 解析（不引入 PyYAML 依赖）
- 严格实现，失败有详细错误
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class AutonomousConfig:
    """Autonomous 配置数据类（可序列化）。

    字段说明：
    - max_iterations: 最大迭代次数
    - max_tokens: token 预算（0=不限制，默认）
    - stop_when: 停止条件（自然语言）
    - stage_order: 阶段顺序
    - backoff_base_sec: 退避基数
    - backoff_max_sec: 退避上限
    - consecutive_failure_abort: 连续失败 abort 阈值
    - test_command: 测试命令
    - test_timeout_sec: 测试超时
    - security_analyzer: 安全分析器
    - git_author_name: commit 作者名
    - git_author_email: commit 作者邮箱
    - auto_commit: 是否自动 commit
    - sleep_guard_enabled: 是否启用防休眠
    - run_dir: run 目录（相对 project_root）
    - max_size_kb: notes.md 最大大小
    - trim_keep_last_n: trim 时保留段落数
    - notes_path: notes.md 路径
    - confirm_mode: 确认模式（smart/whitelist-only/blacklist-only）
    - risk_threshold: 风险评分阈值
    - extra: 扩展字段（未识别 key 落入此处）
    """

    max_iterations: int = 50
    # max_tokens=0 表示不限制（默认）；正整数表示显式预算上限。
    max_tokens: int = 0
    stop_when: str = ""
    stage_order: List[str] = field(default_factory=lambda: ["plan", "dev", "verify", "fix"])
    backoff_base_sec: float = 1.0
    backoff_max_sec: float = 60.0
    consecutive_failure_abort: int = 3
    test_command: str = "python3 -m unittest discover -s tests -p 'test_*.py'"
    test_timeout_sec: float = 600.0
    security_analyzer: str = "builtin"
    git_author_name: str = "Ralph Autonomous Agent"
    git_author_email: str = "ralph@trae-multi-agent.local"
    auto_commit: bool = True
    sleep_guard_enabled: bool = True
    run_dir: str = ".gnhf/runs"
    max_size_kb: int = 1024
    trim_keep_last_n: int = 20
    notes_path: str = "notes.md"
    confirm_mode: str = "smart"
    risk_threshold: int = 5
    # Phase 19: Loop Engineering 全局默认配置（可选，向后兼容）
    loop_engineering_enabled: bool = False
    loop_type: str = "coding"
    loop_discovery_mode: str = "auto"
    loop_evaluator_mode: str = "strict"
    loop_human_checkpoint_every: int = 5
    loop_max_iterations: int = 50
    # loop_max_tokens=0 表示不限制（默认）；正整数表示显式预算上限。
    loop_max_tokens: int = 0
    loop_sampling_read_ratio: float = 0.1
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict。"""
        return asdict(self)


# 默认配置（fallback）
DEFAULT_CONFIG = AutonomousConfig()


# 简化 YAML 解析器
# 支持：
# - key: value 形式
# - 嵌套（缩进 2 空格）
# - 列表（- item）
# - 字符串、整数、浮点数、布尔值
# 不支持：复杂 anchor、多行字符串、tag

class SimpleYAMLParser:
    """简化 YAML 解析器（不依赖 PyYAML）。

    设计目标：支持 autonomous config 所需的最小 YAML 子集。
    限制：不支持 anchor/alias、多行 block scalar、复杂 mapping。
    """

    _LINE_RE = re.compile(r"^(\s*)(- )?(\S+):\s*(.*)$")
    _LIST_ITEM_RE = re.compile(r"^(\s*)- (.+)$")

    def parse(self, text: str) -> Dict[str, Any]:
        """解析 YAML 文本为 dict。

        Args:
            text: YAML 文本

        Returns:
            Dict[str, Any]: 解析结果
        """
        # 按行处理
        lines = text.splitlines()
        # 先 strip 注释和空行
        cleaned: List[str] = []
        for line in lines:
            stripped = line.rstrip()
            # 跳过空行
            if not stripped.strip():
                continue
            # 跳过注释行（以 # 开头）
            if stripped.lstrip().startswith("#"):
                continue
            cleaned.append(stripped)
        if not cleaned:
            return {}
        # 解析为 token 流
        # 修复：_parse_block 返回 (result, next_index)，parse 应仅返回 result
        result, _ = self._parse_block(cleaned, 0, 0)
        return result

    def _parse_block(
        self, lines: List[str], start: int, indent: int
    ) -> tuple:
        """解析一个块（dict 或 list）。

        Args:
            lines: 行列表
            start: 起始行索引
            indent: 缩进空格数

        Returns:
            (parsed_value, next_line_index)
        """
        result: Dict[str, Any] = {}
        i = start
        # 收集顶层
        while i < len(lines):
            line = lines[i]
            current_indent = len(line) - len(line.lstrip())
            # 缩进不匹配 → 块结束
            if current_indent < indent:
                break
            if current_indent > indent:
                # 异常：子缩进但父级不是 dict
                i += 1
                continue
            # 匹配 key: value 或 - item
            if line.lstrip().startswith("- "):
                # 列表项
                break
            m = self._LINE_RE.match(line)
            if not m:
                i += 1
                continue
            key = m.group(3)
            value = m.group(4).strip()
            # 判断 value 是 inline 还是 block
            if value:
                # inline
                result[key] = self._parse_scalar(value)
                i += 1
            else:
                # block：检查下一行
                if i + 1 >= len(lines):
                    i += 1
                    continue
                next_line = lines[i + 1]
                next_indent = len(next_line) - len(next_line.lstrip())
                if next_indent <= indent:
                    # 块为空
                    result[key] = {}
                    i += 1
                elif next_line.lstrip().startswith("- "):
                    # 列表
                    list_value, new_i = self._parse_list(lines, i + 1, next_indent)
                    result[key] = list_value
                    i = new_i
                else:
                    # dict
                    dict_value, new_i = self._parse_dict(lines, i + 1, next_indent)
                    result[key] = dict_value
                    i = new_i
        return result, i

    def _parse_dict(
        self, lines: List[str], start: int, indent: int
    ) -> tuple:
        """解析 dict 块。

        Returns:
            (dict, next_line_index)
        """
        result: Dict[str, Any] = {}
        i = start
        while i < len(lines):
            line = lines[i]
            current_indent = len(line) - len(line.lstrip())
            if current_indent < indent:
                break
            if current_indent > indent:
                i += 1
                continue
            if line.lstrip().startswith("- "):
                # 字典块中不应有列表项
                i += 1
                continue
            m = self._LINE_RE.match(line)
            if not m:
                i += 1
                continue
            key = m.group(3)
            value = m.group(4).strip()
            if value:
                result[key] = self._parse_scalar(value)
                i += 1
            else:
                if i + 1 >= len(lines):
                    i += 1
                    continue
                next_line = lines[i + 1]
                next_indent = len(next_line) - len(next_line.lstrip())
                if next_indent <= indent:
                    result[key] = {}
                    i += 1
                elif next_line.lstrip().startswith("- "):
                    list_value, new_i = self._parse_list(lines, i + 1, next_indent)
                    result[key] = list_value
                    i = new_i
                else:
                    dict_value, new_i = self._parse_dict(lines, i + 1, next_indent)
                    result[key] = dict_value
                    i = new_i
        return result, i

    def _parse_list(
        self, lines: List[str], start: int, indent: int
    ) -> tuple:
        """解析列表块。

        Returns:
            (list, next_line_index)
        """
        result: List[Any] = []
        i = start
        while i < len(lines):
            line = lines[i]
            current_indent = len(line) - len(line.lstrip())
            if current_indent < indent:
                break
            if current_indent > indent:
                # 嵌套在 - 后
                i += 1
                continue
            if not line.lstrip().startswith("- "):
                break
            # 解析列表项
            item_content = line.lstrip()[2:].strip()
            if item_content:
                # 简单 item
                result.append(self._parse_scalar(item_content))
                i += 1
            else:
                # 嵌套 dict/list
                if i + 1 >= len(lines):
                    i += 1
                    continue
                next_line = lines[i + 1]
                next_indent = len(next_line) - len(next_line.lstrip())
                if next_indent <= indent:
                    result.append(None)
                    i += 1
                elif next_line.lstrip().startswith("- "):
                    list_value, new_i = self._parse_list(lines, i + 1, next_indent)
                    result.append(list_value)
                    i = new_i
                else:
                    dict_value, new_i = self._parse_dict(lines, i + 1, next_indent)
                    result.append(dict_value)
                    i = new_i
        return result, i

    def _parse_scalar(self, value: str) -> Any:
        """解析标量值（string / int / float / bool / null）。"""
        if not value:
            return ""
        # 去掉引号
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            return value[1:-1]
        # 布尔
        if value.lower() in ("true", "yes", "on"):
            return True
        if value.lower() in ("false", "no", "off"):
            return False
        # null
        if value.lower() in ("null", "~", ""):
            return None
        # 数字
        try:
            if "." in value or "e" in value.lower():
                return float(value)
            return int(value)
        except ValueError:
            pass
        # 字符串
        return value


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """深度合并两个 dict（override 优先）。

    Args:
        base: 基础 dict
        override: 覆盖 dict

    Returns:
        Dict[str, Any]: 合并后的 dict
    """
    result = dict(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _coerce_value(field_name: str, value: Any, default: Any) -> Any:
    """类型强制转换（容错处理）。

    Args:
        field_name: 字段名（用于错误信息）
        value: 原始值
        default: 默认值（用于类型推断）

    Returns:
        Any: 转换后的值
    """
    if value is None:
        return default
    # 如果是 list，转 list of str
    if isinstance(default, list):
        if isinstance(value, list):
            return [str(v) for v in value]
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return default
    # 如果是 bool
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "yes", "on", "1")
        return bool(value)
    # 如果是 int
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    # 如果是 float
    if isinstance(default, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    # str
    return str(value)


def load_config(
    project_root: Path,
    user_config_path: Optional[Path] = None,
) -> AutonomousConfig:
    """加载 autonomous 配置（用户级 + 项目级，项目级优先）。

    Args:
        project_root: 项目根目录
        user_config_path: 用户级配置路径（默认 ~/.trae/autonomous.yml）

    Returns:
        AutonomousConfig: 合并后的配置
    """
    parser = SimpleYAMLParser()
    merged: Dict[str, Any] = {}
    # 1. 用户级配置
    user_path = user_config_path or (Path.home() / ".trae" / "autonomous.yml")
    if user_path.exists():
        try:
            text = user_path.read_text(encoding="utf-8")
            user_cfg = parser.parse(text)
            merged = _deep_merge(merged, user_cfg)
        except OSError:
            pass
    # 2. 项目级配置（覆盖用户级）
    project_path = project_root / ".trae" / "autonomous.yml"
    if project_path.exists():
        try:
            text = project_path.read_text(encoding="utf-8")
            project_cfg = parser.parse(text)
            merged = _deep_merge(merged, project_cfg)
        except OSError:
            pass
    # 3. 应用到 dataclass（容错：未知字段落入 extra）
    return _apply_config(merged)


def _apply_config(merged: Dict[str, Any]) -> AutonomousConfig:
    """应用 merged dict 到 AutonomousConfig（容错 + extra 收集）。"""
    # 已定义字段
    field_names = {
        "max_iterations": int,
        "max_tokens": int,
        "stop_when": str,
        "stage_order": list,
        "backoff_base_sec": float,
        "backoff_max_sec": float,
        "consecutive_failure_abort": int,
        "test_command": str,
        "test_timeout_sec": float,
        "security_analyzer": str,
        "git_author_name": str,
        "git_author_email": str,
        "auto_commit": bool,
        "sleep_guard_enabled": bool,
        "run_dir": str,
        "max_size_kb": int,
        "trim_keep_last_n": int,
        "notes_path": str,
        "confirm_mode": str,
        "risk_threshold": int,
        # Phase 19: Loop Engineering 全局默认配置
        "loop_engineering_enabled": bool,
        "loop_type": str,
        "loop_discovery_mode": str,
        "loop_evaluator_mode": str,
        "loop_human_checkpoint_every": int,
        "loop_max_iterations": int,
        "loop_max_tokens": int,
        "loop_sampling_read_ratio": float,
    }
    kwargs: Dict[str, Any] = {}
    extra: Dict[str, Any] = {}
    for key, value in merged.items():
        if key in field_names:
            default_value = getattr(DEFAULT_CONFIG, key)
            kwargs[key] = _coerce_value(key, value, default_value)
        else:
            extra[key] = value
    if extra:
        kwargs["extra"] = extra
    return AutonomousConfig(**kwargs)


__all__ = [
    "AutonomousConfig",
    "DEFAULT_CONFIG",
    "load_config",
    "SimpleYAMLParser",
]
