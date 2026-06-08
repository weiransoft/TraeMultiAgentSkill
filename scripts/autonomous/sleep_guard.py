"""跨平台 Sleep 防护（caffeinate / systemd-inhibit / no-op）。

设计目标：
- macOS: caffeinate -i
- Linux: systemd-inhibit（如果可用）
- Windows: no-op
- 其他: no-op
- try/finally 严格 release（atexit 兜底）
"""
from __future__ import annotations

import atexit
import os
import platform
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional


class SleepGuardMode(str, Enum):
    """Sleep 防护模式。"""

    ON = "on"
    OFF = "off"


@dataclass
class SleepGuardHandle:
    """Sleep 防护句柄（内部使用）。

    字段说明：
    - mode: 实际生效的模式
    - process: 子进程（caffeinate / systemd-inhibit）
    - backend: 实际后端（"caffeinate" / "systemd-inhibit" / "noop"）
    """

    mode: SleepGuardMode
    process: Optional[subprocess.Popen]
    backend: str


class SleepGuard:
    """跨平台 Sleep 防护。

    设计原则：
    1. macOS 优先用 caffeinate -i（阻止空闲休眠）
    2. Linux 优先用 systemd-inhibit（如果可用）
    3. Windows 与未知平台 no-op
    4. atexit 兜底（即使异常退出也 release）
    5. SIGTERM/SIGINT 优雅 release
    """

    def __init__(
        self,
        mode: SleepGuardMode = SleepGuardMode.ON,
        log: Optional[Callable[[str], None]] = None,
    ):
        """构造 SleepGuard。

        Args:
            mode: ON = 启动时防止休眠，OFF = no-op
            log: 日志回调
        """
        self._mode = mode
        self._log = log or (lambda msg: None)
        self._handle: Optional[SleepGuardHandle] = None
        self._atexit_registered = False

    # ------------------------------------------------------------------ #
    # 公共 API                                                            #
    # ------------------------------------------------------------------ #

    def acquire(self) -> SleepGuardHandle:
        """启动防休眠子进程。

        Returns:
            SleepGuardHandle: 启动信息

        行为：
        1. 检测平台
        2. 启动对应后端子进程
        3. 注册 atexit 钩子
        4. 注册 SIGTERM/SIGINT 钩子
        """
        if self._mode == SleepGuardMode.OFF:
            self._log("[SleepGuard] 模式为 OFF，跳过防休眠")
            self._handle = SleepGuardHandle(
                mode=SleepGuardMode.OFF, process=None, backend="noop"
            )
            return self._handle
        backend = self._detect_backend()
        if backend == "noop":
            self._log(f"[SleepGuard] 平台 {sys.platform} 不支持防休眠，使用 no-op")
            self._handle = SleepGuardHandle(
                mode=SleepGuardMode.ON, process=None, backend="noop"
            )
            return self._handle
        # 启动子进程
        if backend == "caffeinate":
            cmd = ["caffeinate", "-i", "-w", str(os.getpid())]
        elif backend == "systemd-inhibit":
            cmd = [
                "systemd-inhibit",
                "--what=idle:sleep",
                "--who=trae-multi-agent-ralph",
                "--why=Ralph autonomous run",
                "--mode=block",
                "sleep", "infinity",
            ]
        else:
            cmd = []
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                preexec_fn=os.setsid if sys.platform != "win32" else None,
            )
        except (OSError, FileNotFoundError) as e:
            self._log(f"[SleepGuard] 启动 {backend} 失败: {e}，降级为 no-op")
            self._handle = SleepGuardHandle(
                mode=SleepGuardMode.ON, process=None, backend="noop"
            )
            return self._handle
        self._handle = SleepGuardHandle(
            mode=SleepGuardMode.ON, process=proc, backend=backend
        )
        self._log(f"[SleepGuard] 已启动 {backend}（pid={proc.pid}）")
        # 注册 atexit 兜底
        if not self._atexit_registered:
            atexit.register(self.release)
            self._atexit_registered = True
        # 注册信号钩子（仅在主线程有效）
        try:
            signal.signal(signal.SIGTERM, lambda *_: self.release())
            signal.signal(signal.SIGINT, lambda *_: self.release())
        except (ValueError, OSError):
            # 非主线程或不支持的信号
            pass
        return self._handle

    def release(self) -> None:
        """释放防休眠子进程（优雅关闭）。

        行为：
        1. 如果 handle 是 None → no-op
        2. 如果 process 存在且 alive → terminate + wait
        3. 设置 handle = None（避免重复 release）
        """
        if self._handle is None:
            return
        proc = self._handle.process
        backend = self._handle.backend
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2.0)
                self._log(f"[SleepGuard] 已停止 {backend}（pid={proc.pid}）")
            except OSError as e:
                self._log(f"[SleepGuard] 停止 {backend} 失败: {e}")
        self._handle = None

    def is_active(self) -> bool:
        """是否正在防护。"""
        if self._handle is None:
            return False
        if self._handle.process is None:
            return False
        return self._handle.process.poll() is None

    def backend_name(self) -> str:
        """实际后端名称（caffeinate / systemd-inhibit / noop）。"""
        if self._handle is None:
            return "uninitialized"
        return self._handle.backend

    @staticmethod
    def detect_platform_backend() -> str:
        """检测当前平台支持的后端（不实际启动）。"""
        system = platform.system().lower()
        if system == "darwin":
            if shutil.which("caffeinate"):
                return "caffeinate"
            return "noop"
        if system == "linux":
            if shutil.which("systemd-inhibit"):
                return "systemd-inhibit"
            return "noop"
        return "noop"

    # ------------------------------------------------------------------ #
    # 内部辅助                                                            #
    # ------------------------------------------------------------------ #

    def _detect_backend(self) -> str:
        """检测可用后端。"""
        return self.detect_platform_backend()


__all__ = ["SleepGuard", "SleepGuardMode", "SleepGuardHandle"]
