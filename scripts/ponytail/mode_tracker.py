"""Ponytail 模式跟踪器。

设计目标：
- 解析用户输入中的 /ponytail 命令
- 持久化当前模式（文件标志，原子写入）
- 支持环境变量覆盖
- 线程安全（原子文件操作）

配置解析优先级：
    环境变量 PONYTAIL_DEFAULT_MODE > 配置文件 ~/.trae/ponytail.json > 默认 full

ultra 模式安全策略（架构师评审 P0）：
- ultra 模式不持久化（单任务生效）
- autonomous 模式下禁止 ultra（强制降级为 full）
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path


class ModeTracker:
    """模式跟踪器（线程安全文件操作）。

    所有方法都是 classmethod，无实例状态，天然线程安全。
    文件写入使用原子操作（先写 tmp 再 rename）。
    """

    # 标志文件路径（记录当前激活的模式）
    _FLAG_FILE = Path.home() / ".trae" / ".ponytail-active"

    # 配置文件路径（持久化默认模式）
    _CONFIG_FILE = Path.home() / ".trae" / "ponytail.json"

    # 合法模式集合
    VALID_MODES = {"off", "lite", "full", "ultra"}

    # 并发写入锁（保护 tmp 文件创建与 rename 的原子性）
    _write_lock = threading.Lock()

    @classmethod
    def get_default_mode(cls) -> str:
        """获取默认模式（env > config file > full）。

        Returns:
            str: 默认模式（off/lite/full/ultra）
        """
        # 1. 环境变量（最高优先级）
        env_mode = os.environ.get("PONYTAIL_DEFAULT_MODE", "").lower()
        if env_mode in cls.VALID_MODES:
            return env_mode
        # 2. 配置文件
        try:
            if cls._CONFIG_FILE.exists():
                config = json.loads(cls._CONFIG_FILE.read_text(encoding="utf-8"))
                mode = str(config.get("defaultMode", "")).lower()
                if mode in cls.VALID_MODES:
                    return mode
        except (json.JSONDecodeError, OSError):
            pass
        # 3. 默认
        return "full"

    @classmethod
    def set_mode(cls, mode: str) -> None:
        """设置当前模式（原子写入，线程安全）。

        使用线程唯一的 tmp 文件名 + 全局锁双重保护：
        - 线程唯一 tmp 名避免多线程竞争同一 tmp 路径
        - 全局锁保证 tmp 创建到 rename 的原子性窗口不被打断

        Args:
            mode: 模式（off/lite/full/ultra）
        """
        if mode not in cls.VALID_MODES:
            return
        cls._FLAG_FILE.parent.mkdir(parents=True, exist_ok=True)
        # 使用线程 ID 生成唯一 tmp 文件名，避免多线程竞争同一 tmp 路径
        # 配合全局锁，保证 write+rename 的原子性
        with cls._write_lock:
            tmp = cls._FLAG_FILE.with_suffix(f".{threading.get_ident()}.tmp")
            try:
                tmp.write_text(mode, encoding="utf-8")
                # 原子 rename：POSIX 保证 rename 是原子的
                tmp.replace(cls._FLAG_FILE)
            finally:
                # 清理可能残留的 tmp 文件（如 rename 失败）
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass

    @classmethod
    def get_current_mode(cls) -> str:
        """获取当前模式。

        优先读标志文件，不存在则用默认模式。

        Returns:
            str: 当前模式（off/lite/full/ultra）
        """
        try:
            if cls._FLAG_FILE.exists():
                mode = cls._FLAG_FILE.read_text(encoding="utf-8").strip().lower()
                if mode in cls.VALID_MODES:
                    return mode
        except OSError:
            pass
        return cls.get_default_mode()

    @classmethod
    def clear_mode(cls) -> None:
        """清除模式（回到默认）。"""
        try:
            if cls._FLAG_FILE.exists():
                cls._FLAG_FILE.unlink()
        except OSError:
            pass

    @classmethod
    def parse_user_command(cls, user_input: str) -> str:
        """解析用户输入中的 /ponytail 命令。

        支持的命令格式：
        - /ponytail lite → lite
        - /ponytail full → full
        - /ponytail ultra → ultra
        - /ponytail off → off
        - /ponytail（无参数）→ 当前模式
        - stop ponytail → off
        - normal mode → off

        Args:
            user_input: 用户输入文本

        Returns:
            str: 解析出的模式（off/lite/full/ultra），无命令返回当前模式
        """
        if not user_input:
            return cls.get_current_mode()

        # /ponytail ultra → ultra（支持 / @ $ 前缀）
        m = re.match(r'^[/@$]ponytail\s+(lite|full|ultra|off)\b', user_input, re.IGNORECASE)
        if m:
            return m.group(1).lower()

        # /ponytail（无参数）→ 当前模式
        if re.match(r'^[/@$]ponytail\b', user_input, re.IGNORECASE):
            return cls.get_current_mode()

        # stop ponytail / normal mode → off
        if re.search(r'\b(stop\s+ponytail|normal\s+mode)\b', user_input, re.IGNORECASE):
            return "off"

        return cls.get_current_mode()


__all__ = ["ModeTracker"]
