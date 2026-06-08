"""Git 操作封装（提交/回滚/恢复 uncommitted work）。

设计目标：
- 真实执行 git 命令（不模拟、不静默）
- rollback 保留 uncommitted work 到 .gnhf/runs/<id>/uncommitted/
- 失败暴露详细错误（不假装成功）
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class GitOpResult:
    """git 操作结果。

    字段说明：
    - success: 是否成功
    - stdout: 命令 stdout
    - stderr: 命令 stderr
    - returncode: git 命令返回码
    - error_message: 失败时的可读错误（中文）
    """

    success: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    error_message: str = ""


@dataclass
class DiffStats:
    """diff 统计。

    字段说明：
    - files_changed: 变更文件数
    - lines_added: 新增行数
    - lines_removed: 删除行数
    - binary_files: 二进制文件数
    """

    files_changed: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    binary_files: int = 0


class GitDriver:
    """Ralph 风格 Git 操作封装。

    设计原则：
    1. 真实调用 git 命令（不模拟）
    2. 失败有详细错误（不假装成功）
    3. rollback 保留 uncommitted work 到 .gnhf/runs/<id>/uncommitted/
    """

    def __init__(
        self,
        repo_root: Path,
        run_id: str,
        author_name: str = "Ralph Autonomous Agent",
        author_email: str = "ralph@trae-multi-agent.local",
        run_dir: Optional[Path] = None,
        git_timeout_sec: float = 30.0,
    ):
        """构造 GitDriver。

        Args:
            repo_root: git 仓库根目录
            run_id: 本次 run 的 ID（用于 uncommitted work 隔离目录）
            author_name: commit 作者名
            author_email: commit 作者邮箱
            run_dir: .gnhf/runs/<run_id>/ 路径
            git_timeout_sec: 单个 git 命令超时
        """
        self._repo_root = Path(repo_root).resolve()
        self._run_id = run_id
        self._author_name = author_name
        self._author_email = author_email
        self._run_dir = (
            Path(run_dir).resolve()
            if run_dir is not None
            else self._repo_root / ".gnhf" / "runs" / run_id
        )
        self._uncommitted_dir = self._run_dir / "uncommitted"
        self._git_timeout = max(1.0, float(git_timeout_sec))
        # 缓存 is_git_repo 结果
        self._is_repo_cache: Optional[bool] = None

    # ------------------------------------------------------------------ #
    # 公共 API                                                            #
    # ------------------------------------------------------------------ #

    def is_git_repo(self) -> bool:
        """检测 repo_root 是否为 git 仓库。

        Returns:
            bool: True = 是 git 仓库
        """
        if self._is_repo_cache is not None:
            return self._is_repo_cache
        result = self._run_git("rev-parse", "--is-inside-work-tree", check=False)
        self._is_repo_cache = result.success and result.stdout.strip() == "true"
        return self._is_repo_cache

    def status(self) -> GitOpResult:
        """git status --porcelain。"""
        return self._run_git("status", "--porcelain")

    def diff_stats(self, since_commit: Optional[str] = None) -> DiffStats:
        """获取 diff 统计。

        Args:
            since_commit: 起始 commit（None = 与 HEAD 相比；或 commit hash）
        """
        if since_commit:
            range_spec = f"{since_commit}..HEAD"
        else:
            range_spec = "HEAD"
        # 文件列表
        name_result = self._run_git("diff", "--name-only", range_spec, check=False)
        if not name_result.success:
            return DiffStats()
        files = [f for f in name_result.stdout.splitlines() if f.strip()]
        # 行数统计
        numstat_result = self._run_git(
            "diff", "--numstat", range_spec, check=False
        )
        added = 0
        removed = 0
        binary = 0
        if numstat_result.success:
            for line in numstat_result.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) >= 3:
                    a, r, _ = parts[0], parts[1], parts[2]
                    if a == "-" and r == "-":
                        binary += 1
                    else:
                        try:
                            added += int(a)
                            removed += int(r)
                        except ValueError:
                            continue
        return DiffStats(
            files_changed=len(files),
            lines_added=added,
            lines_removed=removed,
            binary_files=binary,
        )

    def add_all(self) -> GitOpResult:
        """git add -A。"""
        return self._run_git("add", "-A")

    def commit(self, message: str) -> GitOpResult:
        """git commit -m "<message>"。

        Args:
            message: commit message

        Returns:
            GitOpResult: 操作结果（含 commit hash 在 stdout）

        行为：
        1. 先 git status --porcelain 检查是否有变更
        2. git add -A
        3. GIT_AUTHOR_NAME / GIT_AUTHOR_EMAIL 环境变量注入作者
        4. git commit -m "<message>"
        5. 返回 commit hash
        """
        if not message or not message.strip():
            return GitOpResult(
                success=False,
                error_message="commit message 不能为空",
            )
        # 先检查是否有变更
        status_result = self.status()
        if not status_result.success:
            return status_result
        if not status_result.stdout.strip():
            return GitOpResult(
                success=False,
                stderr=status_result.stderr,
                error_message="工作区干净，无变更可提交",
            )
        # git add -A
        add_result = self.add_all()
        if not add_result.success:
            return add_result
        # 用环境变量注入作者（不修改全局 git config）
        env = os.environ.copy()
        env["GIT_AUTHOR_NAME"] = self._author_name
        env["GIT_AUTHOR_EMAIL"] = self._author_email
        env["GIT_COMMITTER_NAME"] = self._author_name
        env["GIT_COMMITTER_EMAIL"] = self._author_email
        return self._run_git_with_env(env, "commit", "-m", message)

    def rollback(self) -> GitOpResult:
        """回滚工作区（保留 uncommitted work）。

        Returns:
            GitOpResult: 操作结果

        行为：
        1. 如果有 uncommitted 变更：
           a. 创建 .gnhf/runs/<run_id>/uncommitted/<timestamp>/ 目录
           b. 用 git diff 和 git status 收集所有变更
           c. cp 所有 untracked/modified 文件到 uncommitted 目录
           d. git checkout -- . 撤销 tracked 变更
           e. 保留 untracked 文件
        2. 记录 uncommitted 清单到 manifest.json
        """
        if not self.is_git_repo():
            return GitOpResult(
                success=False,
                error_message=f"不是 git 仓库: {self._repo_root}",
            )
        # 检查工作区状态
        status_result = self.status()
        if not status_result.success:
            return status_result
        porcelain = status_result.stdout
        if not porcelain.strip():
            return GitOpResult(
                success=True,
                stdout="工作区已经干净，无需回滚",
            )
        # 创建 uncommitted 目录
        timestamp = int(time.time() * 1000)
        snapshot_dir = self._uncommitted_dir / str(timestamp)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        # 解析 porcelain 输出，收集所有变更文件
        manifest: List[dict] = []
        for line in porcelain.splitlines():
            if not line.strip():
                continue
            # porcelain 格式: "XY filename"（XY = 2 字符状态）
            # 也可能 "XY old -> new"（rename/copy）
            if len(line) < 4:
                continue
            status_code = line[:2]
            filename = line[3:].strip()
            # 处理 rename: "old -> new"
            if " -> " in filename:
                filename = filename.split(" -> ", 1)[1].strip().strip('"')
            src_path = self._repo_root / filename
            if not src_path.exists():
                continue
            # 计算 sha256（用于恢复时校验）
            try:
                sha = self._sha256_file(src_path)
            except OSError as e:
                manifest.append(
                    {
                        "path": filename,
                        "status": status_code,
                        "error": f"无法读取: {e}",
                    }
                )
                continue
            # 拷贝到 snapshot 目录
            dest = snapshot_dir / filename
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dest)
            except OSError as e:
                manifest.append(
                    {
                        "path": filename,
                        "status": status_code,
                        "error": f"无法拷贝: {e}",
                    }
                )
                continue
            manifest.append(
                {
                    "path": filename,
                    "status": status_code,
                    "sha256": sha,
                    "size": src_path.stat().st_size,
                }
            )
        # 写 manifest
        manifest_path = snapshot_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "run_id": self._run_id,
                    "timestamp_ms": timestamp,
                    "files": manifest,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        # git checkout -- . 撤销 tracked 变更
        checkout_result = self._run_git("checkout", "--", ".")
        if not checkout_result.success:
            return GitOpResult(
                success=False,
                stdout=checkout_result.stdout,
                stderr=checkout_result.stderr,
                returncode=checkout_result.returncode,
                error_message=f"git checkout -- . 失败: {checkout_result.error_message}",
            )
        # git clean -fd 删除新 untracked 文件
        # 注意：此处保留 untracked 文件（不调用 clean）以避免误删用户数据
        # 如果是测试场景，调用方可以主动 clean
        return GitOpResult(
            success=True,
            stdout=f"已回滚，uncommitted work 保留至 {snapshot_dir}",
        )

    def restore_uncommitted(self, manifest_path: Path) -> GitOpResult:
        """从 manifest 恢复 uncommitted work（供 fix_handler 使用）。

        Args:
            manifest_path: manifest.json 路径

        Returns:
            GitOpResult: 操作结果
        """
        if not manifest_path.exists():
            return GitOpResult(
                success=False,
                error_message=f"manifest 不存在: {manifest_path}",
            )
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            return GitOpResult(
                success=False,
                error_message=f"无法读取 manifest: {e}",
            )
        files = data.get("files", [])
        restored = 0
        errors: List[str] = []
        for entry in files:
            path = entry.get("path")
            sha = entry.get("sha256")
            if not path or not sha:
                continue
            src = manifest_path.parent / path
            if not src.exists():
                errors.append(f"{path}: 源文件不存在")
                continue
            # 校验 sha256
            try:
                actual_sha = self._sha256_file(src)
            except OSError as e:
                errors.append(f"{path}: 读取失败: {e}")
                continue
            if actual_sha != sha:
                errors.append(f"{path}: sha256 不匹配 (期望 {sha[:8]}, 实际 {actual_sha[:8]})")
                continue
            # 恢复
            dest = self._repo_root / path
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                restored += 1
            except OSError as e:
                errors.append(f"{path}: 恢复失败: {e}")
        # git add -A（让 git 重新跟踪）
        self.add_all()
        if errors:
            return GitOpResult(
                success=False,
                stdout=f"已恢复 {restored} 个文件",
                error_message=f"恢复过程中发生 {len(errors)} 个错误: " + "; ".join(errors[:3]),
            )
        return GitOpResult(
            success=True,
            stdout=f"已恢复 {restored} 个文件",
        )

    def log_last_n(self, n: int = 10) -> List[str]:
        """git log -n --oneline。

        Args:
            n: 取最近 N 条
        """
        result = self._run_git("log", f"-{max(1, n)}", "--oneline", check=False)
        if not result.success:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def get_current_sha(self) -> str:
        """获取当前 HEAD commit hash（短格式）。"""
        result = self._run_git("rev-parse", "--short", "HEAD", check=False)
        if not result.success:
            return ""
        return result.stdout.strip()

    # ------------------------------------------------------------------ #
    # 内部辅助                                                            #
    # ------------------------------------------------------------------ #

    def _run_git(
        self, *args: str, check: bool = True
    ) -> GitOpResult:
        """执行 git 命令。

        Args:
            *args: 传递给 git 的参数
            check: True=非零退出码视为失败
        """
        return self._run_git_with_env(os.environ.copy(), *args, check=check)

    def _run_git_with_env(
        self, env: dict, *args: str, check: bool = True
    ) -> GitOpResult:
        """使用指定环境变量执行 git 命令。"""
        if shutil.which("git") is None:
            return GitOpResult(
                success=False,
                error_message="git 命令未找到，请先安装 git",
            )
        cmd = ["git", "-C", str(self._repo_root), *args]
        try:
            proc = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=self._git_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return GitOpResult(
                success=False,
                error_message=f"git 命令超时（>{self._git_timeout}s）: {' '.join(args)}",
            )
        except OSError as e:
            return GitOpResult(
                success=False,
                error_message=f"无法执行 git: {e}",
            )
        success = proc.returncode == 0
        if check and not success:
            return GitOpResult(
                success=False,
                stdout=proc.stdout,
                stderr=proc.stderr,
                returncode=proc.returncode,
                error_message=proc.stderr.strip() or f"git 退出码 {proc.returncode}",
            )
        return GitOpResult(
            success=success,
            stdout=proc.stdout,
            stderr=proc.stderr,
            returncode=proc.returncode,
        )

    @staticmethod
    def _sha256_file(path: Path) -> str:
        """计算文件的 SHA-256 校验和。"""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()


__all__ = ["GitOpResult", "DiffStats", "GitDriver"]
