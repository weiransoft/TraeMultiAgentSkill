#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Worktree Manager - git worktree 隔离管理器

职责：
1. 为每个 subagent 创建独立 git worktree（避免文件冲突）
2. 路径白名单校验（防止越权创建到系统目录）
3. 自动清理（finally 块 + 启动时扫描残留）
4. 降级策略（非 Git 环境返回 None，由调用方处理）
5. 并发安全（threading.Lock）

依据：
- DYNAMIC_WORKFLOWS_INTEGRATION.md §模块 3：Subagent Sandbox
- 架构师审查 §3.0.3：worktree 路径白名单

设计原则：
- 不修改任何 V2 文件
- 复用 PerformanceFingerprint 记录 worktree 元数据
- 路径必须经白名单 + 绝对路径转换 + 真实路径校验 三重保险

作者：trae-multi-agent 融合 Phase 2
创建日期：2026-06-03
"""

import logging
import os
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

# Module logger
logger = logging.getLogger(__name__)


# ============================================================================
# 异常类
# ============================================================================

class WorktreeError(Exception):
    """worktree 操作基础异常"""
    pass


class WorktreePathError(WorktreeError):
    """worktree 路径越权或非法"""
    pass


class WorktreeAlreadyExistsError(WorktreeError):
    """worktree 已存在"""
    pass


class WorktreeNotFoundError(WorktreeError):
    """worktree 不存在"""
    pass


class WorktreeTimeoutError(WorktreeError):
    """worktree 创建超时"""
    pass


class GitNotAvailableError(WorktreeError):
    """git 命令不可用"""
    pass


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class WorktreeInfo:
    """
    worktree 元数据

    字段：
    - worktree_id: 唯一 ID（wt_xxxxxxxx 格式）
    - agent_id: 所属 subagent ID
    - worktree_path: 绝对路径
    - base_branch: 来源分支
    - created_at: ISO 时间字符串
    - git_available: Git 是否可用（False 表示降级）
    - cleanup_on_exit: 进程退出时是否自动清理
    """
    worktree_id: str
    agent_id: str
    worktree_path: str
    base_branch: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    git_available: bool = True
    cleanup_on_exit: bool = True

    def to_dict(self) -> Dict:
        """序列化为 dict（用于持久化到 PerformanceFingerprint）"""
        return asdict(self)


# ============================================================================
# 路径白名单校验
# ============================================================================

def _is_path_safe(path: str, allow_paths: List[str]) -> bool:
    """
    检查路径是否在白名单内

    校验规则：
    1. 路径必须经 Path.resolve() 转换为绝对路径
    2. 真实路径必须以某个白名单路径为前缀
    3. 禁止特殊系统目录（macOS 上 /etc 解析为 /private/etc，需要兼容）

    Args:
        path: 待校验路径
        allow_paths: 允许的根路径白名单

    Returns:
        bool: True 表示安全

    Raises:
        WorktreePathError: 路径不安全时
    """
    if not path:
        raise WorktreePathError("路径不能为空")

    # 转换为绝对路径并解析符号链接
    try:
        real_path = Path(path).resolve()
    except (OSError, RuntimeError) as e:
        raise WorktreePathError(f"路径解析失败：{path}：{e}") from e

    real_path_str = str(real_path)

    # 系统目录黑名单（macOS 上 /var/folders 实际是用户 temp，不能拒绝）
    # 因此需要根据 tempfile.gettempdir() 区分
    import tempfile
    user_temp = str(Path(tempfile.gettempdir()).resolve())

    # 系统目录黑名单
    system_blacklist = {
        "/etc", "/bin", "/sbin", "/usr", "/var",  # /var 中除了用户 temp 都拒绝
        "/System", "/Library", "/Applications",
        "/boot", "/dev", "/proc", "/sys", "/root",
    }

    # 根目录特殊处理
    if real_path_str == "/":
        raise WorktreePathError(f"禁止在根目录创建 worktree：{real_path_str}")

    # 用户临时目录白名单（macOS 上是 /private/var/folders/.../T）
    is_user_temp = False
    if real_path_str == user_temp or real_path_str.startswith(user_temp + "/"):
        is_user_temp = True

    # 系统目录检测
    for blocked in system_blacklist:
        # /var 特殊情况：用户 temp 目录（macOS）允许
        if blocked == "/var" and is_user_temp:
            continue
        # 直接匹配
        if real_path_str == blocked:
            raise WorktreePathError(
                f"禁止在系统目录创建 worktree：{real_path_str}"
            )
        # /private 前缀匹配（macOS）
        if real_path_str.startswith(f"/private{blocked}"):
            # /private/var 中如果是用户 temp，允许
            if blocked == "/var" and is_user_temp:
                continue
            raise WorktreePathError(
                f"禁止在系统目录创建 worktree：{real_path_str}"
            )
        # 一般前缀匹配
        if real_path_str.startswith(blocked + "/"):
            raise WorktreePathError(
                f"禁止在系统目录创建 worktree：{real_path_str}"
            )

    # 用户主目录需要显式允许（防止 ~ 误用）
    home = str(Path.home())
    if real_path_str == home or real_path_str.startswith(home + "/"):
        # 检查是否在白名单中
        is_in_allowlist = False
        for allow in allow_paths:
            try:
                allow_real = str(Path(allow).resolve())
                if real_path_str.startswith(allow_real + "/") or real_path_str == allow_real:
                    is_in_allowlist = True
                    break
            except (OSError, RuntimeError):
                continue
        if not is_in_allowlist:
            raise WorktreePathError(
                f"用户主目录路径必须在白名单内：{real_path_str}"
            )

    # 白名单校验
    for allow in allow_paths:
        try:
            allow_real = str(Path(allow).resolve())
            if real_path_str.startswith(allow_real + "/") or real_path_str == allow_real:
                return True
        except (OSError, RuntimeError):
            continue

    # 默认允许用户 temp（兜底）
    if is_user_temp:
        return True

    raise WorktreePathError(
        f"路径不在白名单内：{real_path_str}（白名单：{allow_paths}）"
    )


# ============================================================================
# Git 命令封装
# ============================================================================

def _check_git_available() -> bool:
    """
    检查 git 命令是否可用

    Returns:
        bool: True 表示 git 可用
    """
    try:
        result = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _is_git_repo(path: str) -> bool:
    """
    检查路径是否为 Git 仓库

    Args:
        path: 路径

    Returns:
        bool: True 表示是 Git 仓库
    """
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _get_git_root(path: str) -> Optional[str]:
    """
    获取 Git 仓库根目录

    Args:
        path: 任意子目录

    Returns:
        Optional[str]: 仓库根目录绝对路径；非仓库返回 None
    """
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def _get_default_branch(git_root: str) -> str:
    """
    获取 Git 仓库的默认分支

    策略：
    1. 尝试 git symbolic-ref refs/remotes/origin/HEAD
    2. 尝试 git branch --list（取第一个）
    3. 回退到 main

    Args:
        git_root: Git 仓库根目录

    Returns:
        str: 默认分支名
    """
    # 策略 1: 远程 HEAD
    try:
        result = subprocess.run(
            ["git", "-C", git_root,
             "symbolic-ref", "refs/remotes/origin/HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # 输出格式：refs/remotes/origin/main
            ref = result.stdout.strip()
            if ref.startswith("refs/remotes/origin/"):
                return ref[len("refs/remotes/origin/"):]
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # 策略 2: 本地分支列表
    try:
        result = subprocess.run(
            ["git", "-C", git_root, "branch", "--list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                # 跳过当前分支标记（带 *）和 HEAD
                line = line.strip().lstrip("* ").strip()
                if line and line != "HEAD":
                    return line
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # 回退
    return "main"


def _run_git_worktree_add(
    git_root: str,
    worktree_path: str,
    base_branch: str,
    timeout: int = 30,
) -> None:
    """
    执行 git worktree add

    Args:
        git_root: Git 仓库根目录
        worktree_path: 新 worktree 路径
        base_branch: 来源分支
        timeout: 超时秒数

    Raises:
        WorktreeTimeoutError: 创建超时
        WorktreeError: 其他 Git 错误
    """
    cmd = [
        "git", "-C", git_root,
        "worktree", "add",
        "-b", f"dw-{Path(worktree_path).name}",  # 新分支名（避免冲突）
        worktree_path,
        base_branch,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise WorktreeError(
                f"git worktree add 失败：{result.stderr.strip()}"
            )
    except subprocess.TimeoutExpired as e:
        raise WorktreeTimeoutError(
            f"git worktree add 超时（{timeout}s）：{worktree_path}"
        ) from e


def _run_git_worktree_remove(
    git_root: str,
    worktree_path: str,
    timeout: int = 30,
) -> None:
    """
    执行 git worktree remove

    Args:
        git_root: Git 仓库根目录
        worktree_path: 待移除的 worktree 路径
        timeout: 超时秒数

    Raises:
        WorktreeError: Git 错误（不存在时静默）
    """
    cmd = ["git", "-C", git_root, "worktree", "remove", "--force", worktree_path]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        # worktree 不存在时，git 返回非 0；这里静默忽略
        if result.returncode != 0 and "not exist" not in result.stderr.lower():
            logger.warning(f"git worktree remove 失败：{result.stderr.strip()}")
    except subprocess.TimeoutExpired:
        logger.warning(f"git worktree remove 超时（{timeout}s）：{worktree_path}")


# ============================================================================
# WorktreeManager
# ============================================================================

class WorktreeManager:
    """
    worktree 隔离管理器

    核心职责：
    1. 为每个 subagent 创建独立 git worktree（避免文件冲突）
    2. 路径白名单校验（防止越权创建到系统目录）
    3. 自动清理（finally 块 + 启动时扫描残留）
    4. 降级策略（非 Git 环境返回 None，由调用方处理）
    5. 并发安全（threading.Lock）

    使用示例：
    ```python
    wm = WorktreeManager(
        base_path="./.dw_worktrees",
        allow_paths=[os.getcwd(), "/tmp"],
    )
    info = wm.create(agent_id="sa_001", base_branch="main")
    try:
        # 在 info.worktree_path 中执行任务
        ...
    finally:
        wm.remove(info.worktree_path)
    ```
    """

    DEFAULT_CREATE_TIMEOUT = 30   # 创建超时（秒）
    DEFAULT_REMOVE_TIMEOUT = 30   # 移除超时（秒）

    def __init__(
        self,
        base_path: str = "./.dw_worktrees",
        allow_paths: Optional[List[str]] = None,
        git_root: Optional[str] = None,
        create_timeout: int = DEFAULT_CREATE_TIMEOUT,
    ):
        """
        初始化 WorktreeManager

        Args:
            base_path: worktree 父目录（所有 worktree 都创建在此目录下）
            allow_paths: 允许的根路径白名单（默认：当前工作目录 + 临时目录）
            git_root: Git 仓库根目录（默认：自动检测 base_path 的祖先）
            create_timeout: 创建超时（秒）
        """
        self._base_path = base_path
        self._allow_paths = allow_paths or [os.getcwd(), "/tmp"]
        self._create_timeout = create_timeout
        self._lock = threading.Lock()  # 并发安全

        # 检测 Git 可用性
        self._git_available = _check_git_available()
        if not self._git_available:
            logger.warning("git 不可用，WorktreeManager 将以降级模式运行")

        # 检测 Git 仓库根
        if git_root:
            self._git_root = git_root
        elif self._git_available:
            self._git_root = _get_git_root(os.getcwd())
        else:
            self._git_root = None

        # worktree 元数据
        self._active_worktrees: Dict[str, WorktreeInfo] = {}

        # 启动时扫描残留 worktree（防止上次崩溃）
        if self._git_available and self._git_root:
            self._scan_residual_worktrees()

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    @property
    def git_available(self) -> bool:
        """Git 是否可用"""
        return self._git_available

    @property
    def active_count(self) -> int:
        """活跃 worktree 数量"""
        return len(self._active_worktrees)

    def create(
        self,
        agent_id: str,
        base_branch: Optional[str] = None,
    ) -> Optional[WorktreeInfo]:
        """
        创建 worktree

        Args:
            agent_id: subagent ID（用于命名）
            base_branch: 起始分支（None 时自动检测：origin/HEAD → 第一个本地分支 → main）

        Returns:
            Optional[WorktreeInfo]: 成功返回元数据；Git 不可用时返回 None

        Raises:
            WorktreePathError: 路径不在白名单
            WorktreeAlreadyExistsError: 同名 worktree 已存在
            WorktreeTimeoutError: 创建超时
        """
        if not self._git_available or not self._git_root:
            logger.warning(
                f"Git 不可用，跳过 worktree 创建（agent_id={agent_id}）"
            )
            return None

        # 自动检测默认分支
        if base_branch is None:
            base_branch = _get_default_branch(self._git_root)
            logger.debug(f"自动检测默认分支：{base_branch}")

        worktree_id = f"wt_{uuid.uuid4().hex[:8]}"
        worktree_path = str(Path(self._base_path) / worktree_id)

        # 路径白名单校验
        try:
            _is_path_safe(worktree_path, self._allow_paths)
        except WorktreePathError as e:
            logger.error(f"worktree 路径越权：{worktree_path}：{e}")
            raise

        with self._lock:
            # 重复检测
            for existing in self._active_worktrees.values():
                if existing.worktree_path == worktree_path:
                    raise WorktreeAlreadyExistsError(
                        f"worktree 已存在：{worktree_path}"
                    )

            # 创建父目录
            try:
                Path(self._base_path).mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise WorktreeError(
                    f"创建父目录失败：{self._base_path}：{e}"
                ) from e

            # 执行 git worktree add
            try:
                _run_git_worktree_add(
                    git_root=self._git_root,
                    worktree_path=worktree_path,
                    base_branch=base_branch,
                    timeout=self._create_timeout,
                )
            except (WorktreeTimeoutError, WorktreeError) as e:
                # 清理可能残留的目录
                if Path(worktree_path).exists():
                    shutil.rmtree(worktree_path, ignore_errors=True)
                raise

            # 记录元数据
            info = WorktreeInfo(
                worktree_id=worktree_id,
                agent_id=agent_id,
                worktree_path=worktree_path,
                base_branch=base_branch,
                git_available=True,
            )
            self._active_worktrees[worktree_id] = info
            logger.info(
                f"worktree 创建成功：{worktree_id} → {worktree_path}（agent={agent_id}）"
            )
            return info

    def remove(self, worktree_path: str) -> bool:
        """
        移除 worktree

        Args:
            worktree_path: worktree 路径

        Returns:
            bool: True 表示成功移除
        """
        with self._lock:
            # 查找对应的 WorktreeInfo
            target_info: Optional[WorktreeInfo] = None
            for info in self._active_worktrees.values():
                if info.worktree_path == worktree_path:
                    target_info = info
                    break

            if target_info is None:
                # 不在活跃列表，但可能物理存在
                if Path(worktree_path).exists() and self._git_root:
                    _run_git_worktree_remove(
                        git_root=self._git_root,
                        worktree_path=worktree_path,
                        timeout=self.DEFAULT_REMOVE_TIMEOUT,
                    )
                    # 物理清理
                    shutil.rmtree(worktree_path, ignore_errors=True)
                return True

            # 从活跃列表中移除
            if self._git_root:
                _run_git_worktree_remove(
                    git_root=self._git_root,
                    worktree_path=worktree_path,
                    timeout=self.DEFAULT_REMOVE_TIMEOUT,
                )
            # 物理清理（即使 git remove 失败也强制清理）
            shutil.rmtree(worktree_path, ignore_errors=True)
            del self._active_worktrees[target_info.worktree_id]
            logger.info(f"worktree 移除成功：{target_info.worktree_id}")
            return True

    def list_active(self) -> List[WorktreeInfo]:
        """
        列出所有活跃 worktree

        Returns:
            List[WorktreeInfo]: 活跃 worktree 列表
        """
        with self._lock:
            return list(self._active_worktrees.values())

    def get(self, worktree_id: str) -> Optional[WorktreeInfo]:
        """
        根据 ID 获取 worktree 元数据

        Args:
            worktree_id: worktree ID

        Returns:
            Optional[WorktreeInfo]: 不存在返回 None
        """
        with self._lock:
            return self._active_worktrees.get(worktree_id)

    def cleanup_all(self) -> int:
        """
        清理所有 worktree（异常路径）

        Returns:
            int: 清理的数量
        """
        with self._lock:
            count = 0
            for info in list(self._active_worktrees.values()):
                try:
                    if self._git_root:
                        _run_git_worktree_remove(
                            git_root=self._git_root,
                            worktree_path=info.worktree_path,
                            timeout=self.DEFAULT_REMOVE_TIMEOUT,
                        )
                    shutil.rmtree(info.worktree_path, ignore_errors=True)
                    del self._active_worktrees[info.worktree_id]
                    count += 1
                except Exception as e:  # noqa: BLE001
                    logger.error(f"清理 worktree 失败 {info.worktree_id}：{e}")
            logger.info(f"cleanup_all 清理 {count} 个 worktree")
            return count

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _scan_residual_worktrees(self) -> None:
        """
        扫描并清理残留 worktree（防止上次崩溃）

        启动时调用：如果 .dw_worktrees/ 目录存在残留 worktree 目录，
        且不在活跃列表中，强制清理。
        """
        base = Path(self._base_path)
        if not base.exists():
            return

        try:
            for child in base.iterdir():
                if child.is_dir() and child.name.startswith("wt_"):
                    # 检查是否在活跃列表
                    is_active = any(
                        Path(info.worktree_path) == child
                        for info in self._active_worktrees.values()
                    )
                    if not is_active:
                        logger.warning(f"清理残留 worktree：{child}")
                        if self._git_root:
                            _run_git_worktree_remove(
                                git_root=self._git_root,
                                worktree_path=str(child),
                                timeout=self.DEFAULT_REMOVE_TIMEOUT,
                            )
                        shutil.rmtree(str(child), ignore_errors=True)
        except OSError as e:
            logger.warning(f"扫描残留 worktree 失败：{e}")
