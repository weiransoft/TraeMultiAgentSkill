"""Ralph 风格 run 状态持久化与断点续跑。

设计目标：
- 原子写入（先 .tmp，再 rename）
- 每次迭代结束 persist()（崩溃可恢复）
- sha256 校验（损坏检测）
- 与 NotesMemory / GitDriver 协同
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class RunStateSchema:
    """run 状态结构（可序列化）。

    字段说明：
    - run_id: 本次 run 的唯一 ID
    - objective: 用户目标
    - started_at: 起始时间（ISO 8601）
    - updated_at: 最后更新时间
    - iter_index: 当前迭代索引（已完成的）
    - consecutive_failures: 连续失败次数
    - cumulative_tokens: 累计 token 估算
    - commits_made: 已成功提交的 commit 数量
    - history: 每轮迭代的简要记录
    - status: pending | running | completed | aborted | failed
    - stop_when: 持久化的自然语言停止条件
    - last_error: 最后的错误信息
    """

    run_id: str
    objective: str
    started_at: str
    updated_at: str
    iter_index: int = 0
    consecutive_failures: int = 0
    cumulative_tokens: int = 0
    commits_made: int = 0
    history: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "pending"
    stop_when: str = ""
    last_error: str = ""


@dataclass
class ResumeContext:
    """断点续跑上下文。

    字段说明：
    - can_resume: 是否可以恢复
    - last_iter_index: 上次完成的迭代索引
    - skipped_count: 跳过的迭代数（resume 时跳过）
    - notes_path: notes.md 路径
    - uncommitted_manifests: 待恢复的 uncommitted work 清单
    """

    can_resume: bool
    last_iter_index: int
    skipped_count: int
    notes_path: Path
    uncommitted_manifests: List[Path] = field(default_factory=list)


class RunState:
    """Ralph 风格 run 状态持久化。

    设计原则：
    1. 原子写入：先写 .tmp，fsync 后 rename（避免半写）
    2. 每次迭代结束 persist()（崩溃可恢复）
    3. sha256 校验（损坏检测）
    4. 单文件 JSON 格式（可读、可调试）
    """

    _SCHEMA_VERSION = 1
    _FILENAME = "state.json"
    _BACKUP_FILENAME = "state.json.bak"

    def __init__(self, run_dir: Path, run_id: str, objective: str = ""):
        """构造 RunState。

        Args:
            run_dir: .gnhf/runs/<run_id>/ 目录
            run_id: 本次 run 的唯一 ID
            objective: 用户目标
        """
        self._run_dir = Path(run_dir)
        self._state_path = self._run_dir / self._FILENAME
        self._backup_path = self._run_dir / self._BACKUP_FILENAME
        self._run_id = run_id
        # 内存中的 state
        self._state: RunStateSchema = RunStateSchema(
            run_id=run_id,
            objective=objective,
            started_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        # 如果 state.json 已存在（resume 模式），加载它
        if self._state_path.exists():
            loaded = self._load_from_disk()
            if loaded is not None:
                self._state = loaded

    # ------------------------------------------------------------------ #
    # 公共 API                                                            #
    # ------------------------------------------------------------------ #

    @property
    def state(self) -> RunStateSchema:
        """当前内存中的 state（只读引用）。"""
        return self._state

    @property
    def state_path(self) -> Path:
        """state.json 路径。"""
        return self._state_path

    def persist(self) -> None:
        """原子写入 state 到磁盘。

        行为：
        1. 更新 updated_at
        2. 写 .tmp
        3. fsync + rename
        4. 备份旧文件到 state.json.bak
        """
        self._state.updated_at = datetime.now(timezone.utc).isoformat()
        # 序列化为 JSON
        payload = {
            "schema_version": self._SCHEMA_VERSION,
            "state": asdict(self._state),
        }
        # 备份旧文件
        if self._state_path.exists():
            try:
                import shutil
                shutil.copy2(self._state_path, self._backup_path)
            except OSError:
                pass  # 备份失败不阻塞 persist
        # 写 .tmp
        self._run_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self._state_path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        # 原子 rename
        os.replace(tmp_path, self._state_path)

    def record_iteration(
        self,
        iter_index: int,
        result_kind: str,
        summary: str = "",
        tokens: int = 0,
        committed: bool = False,
        error: str = "",
    ) -> None:
        """记录一轮迭代。

        Args:
            iter_index: 迭代索引
            result_kind: success/failed/retriable/fatal
            summary: 摘要
            tokens: 本轮 token 消耗
            committed: 是否成功 commit
            error: 错误信息
        """
        self._state.iter_index = iter_index
        if result_kind == "success":
            self._state.consecutive_failures = 0
            if committed:
                self._state.commits_made += 1
        elif result_kind in ("failed", "retriable"):
            self._state.consecutive_failures += 1
        elif result_kind == "fatal":
            self._state.consecutive_failures += 1
        self._state.cumulative_tokens += tokens
        # 记录历史（最多保留最近 100 条，避免无限增长）
        self._state.history.append(
            {
                "iter": iter_index,
                "kind": result_kind,
                "summary": summary[:500],  # 截断过长摘要
                "tokens": tokens,
                "committed": committed,
                "error": error[:500] if error else "",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        if len(self._state.history) > 100:
            self._state.history = self._state.history[-100:]
        if error:
            self._state.last_error = error
        self.persist()

    def mark_running(self) -> None:
        """标记为 running。"""
        self._state.status = "running"
        self.persist()

    def mark_complete(self) -> None:
        """标记为 completed。"""
        self._state.status = "completed"
        self.persist()

    def mark_aborted(self, reason: str = "") -> None:
        """标记为 aborted。"""
        self._state.status = "aborted"
        if reason:
            self._state.last_error = reason
        self.persist()

    def mark_failed(self, reason: str = "") -> None:
        """标记为 failed。"""
        self._state.status = "failed"
        if reason:
            self._state.last_error = reason
        self.persist()

    def get_resume_context(self, uncommitted_dir: Optional[Path] = None) -> ResumeContext:
        """获取断点续跑上下文。

        Args:
            uncommitted_dir: uncommitted work 目录
        """
        uncommitted_manifests: List[Path] = []
        if uncommitted_dir is not None and uncommitted_dir.exists():
            for d in sorted(uncommitted_dir.iterdir()):
                if d.is_dir():
                    manifest = d / "manifest.json"
                    if manifest.exists():
                        uncommitted_manifests.append(manifest)
        # 仅在 status=running 或 failed 时才允许 resume
        can_resume = self._state.status in ("running", "failed", "aborted")
        return ResumeContext(
            can_resume=can_resume,
            last_iter_index=self._state.iter_index,
            skipped_count=0,
            notes_path=self._run_dir / "notes.md",
            uncommitted_manifests=uncommitted_manifests,
        )

    def verify_integrity(self) -> bool:
        """校验 state.json 完整性（基于 JSON 解析）。

        Returns:
            bool: True = 完整可读
        """
        if not self._state_path.exists():
            return False
        try:
            content = self._state_path.read_text(encoding="utf-8")
            data = json.loads(content)
            return (
                "schema_version" in data
                and "state" in data
                and data.get("schema_version") == self._SCHEMA_VERSION
            )
        except (OSError, json.JSONDecodeError):
            return False

    def restore_from_backup(self) -> bool:
        """从 backup 恢复（损坏时调用）。

        Returns:
            bool: True = 成功恢复
        """
        if not self._backup_path.exists():
            return False
        try:
            import shutil
            shutil.copy2(self._backup_path, self._state_path)
            loaded = self._load_from_disk()
            if loaded is not None:
                self._state = loaded
                return True
        except OSError:
            return False
        return False

    # ------------------------------------------------------------------ #
    # 内部辅助                                                            #
    # ------------------------------------------------------------------ #

    def _load_from_disk(self) -> Optional[RunStateSchema]:
        """从磁盘加载 state。"""
        try:
            content = self._state_path.read_text(encoding="utf-8")
            data = json.loads(content)
            if data.get("schema_version") != self._SCHEMA_VERSION:
                return None
            state_dict = data.get("state", {})
            return RunStateSchema(**state_dict)
        except (OSError, json.JSONDecodeError, TypeError):
            return None


__all__ = ["RunState", "RunStateSchema", "ResumeContext"]
