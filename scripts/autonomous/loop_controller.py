"""Ralph 风格主循环控制器。

行为：
- 编排每轮迭代：plan → dev → verify → fix
- 与 GitDriver / NotesMemory / RunState 协作
- 强制 runtime caps（max-iterations / max-tokens / stop-when）
- 失败重试与退避（指数退避 + 连续 3 次失败 abort）
- try/finally 严格 release SleepGuard
"""
from __future__ import annotations

import os
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional


class StageKind(str, Enum):
    """Ralph 阶段枚举（v2.8 新增 REVIEW）。

    PLAN: 制定计划
    DEV: 实际开发
    VERIFY: 验证
    FIX: 修复
    REVIEW: 文档对照代码审查（v2.8 新增）
    """

    PLAN = "plan"
    DEV = "dev"
    VERIFY = "verify"
    FIX = "fix"
    REVIEW = "review"


@dataclass
class LoopConfig:
    """Ralph 循环配置。

    字段说明：
    - max_iterations: 硬上限
    - max_tokens: token 预算
    - stop_when: 自然语言停止条件
    - stage_order: 阶段顺序
    - backoff_base_sec: 失败退避基数
    - backoff_max_sec: 退避上限
    - consecutive_failure_abort: 连续失败次数阈值
    - git_author_name/email: commit 作者
    - test_command: 测试命令
    - security_analyzer: 安全分析器名称
    """

    max_iterations: int = 50
    max_tokens: int = 500_000
    stop_when: str = ""
    stage_order: List[StageKind] = field(
        default_factory=lambda: [StageKind.PLAN, StageKind.DEV, StageKind.VERIFY, StageKind.FIX]
    )
    backoff_base_sec: float = 1.0
    backoff_max_sec: float = 60.0
    consecutive_failure_abort: int = 3
    git_author_name: str = "Ralph Autonomous Agent"
    git_author_email: str = "ralph@trae-multi-agent.local"
    test_command: str = "python3 -m unittest discover -s tests -p 'test_*.py'"
    security_analyzer: str = "builtin"


@dataclass
class IterationContext:
    """单次迭代上下文。

    字段说明：
    - run_id: 本次 Ralph run 的唯一 ID
    - iter_index: 当前迭代索引（从 1 开始）
    - stage: 当前阶段
    - current_plan: 当前 plan
    - notes_snapshot: notes.md 内容
    - prev_results: 历史迭代结果
    - project_root: 项目根目录
    - worktree_path: 工作路径
    - objective: 原始目标
    - agent_output: 智能体输出（dev 阶段填充）
    - token_used: 本轮 token 消耗
    - verify_artifacts: verify 阶段产出
    """

    run_id: str
    iter_index: int
    stage: StageKind
    current_plan: str
    notes_snapshot: str
    prev_results: List["IterationResult"]
    project_root: Path
    worktree_path: Path
    objective: str = ""
    agent_output: str = ""
    token_used: int = 0
    verify_artifacts: Optional[dict] = None


@dataclass
class IterationResult:
    """单次迭代结果（4 类判定）。

    字段说明：
    - kind: success / failed / retriable / fatal
    - summary: 摘要
    - agent_output: 智能体原始输出
    - diff_stats: (lines_added, lines_removed)
    - test_results: (passed, failed, skipped)
    - security_issues: 安全问题列表
    - duration_sec: 本轮耗时
    - token_used: 本轮 token 消耗
    - error: 异常对象
    - committed: 是否成功 commit
    """

    kind: str  # success / failed / retriable / fatal
    summary: str = ""
    agent_output: str = ""
    diff_stats: tuple = (0, 0)
    test_results: tuple = (0, 0, 0)
    security_issues: List[dict] = field(default_factory=list)
    duration_sec: float = 0.0
    token_used: int = 0
    error: Optional[BaseException] = None
    committed: bool = False


class RalphLoopController:
    """Ralph 风格自主迭代主循环。

    设计原则：
    1. try/finally 严格 release SleepGuard
    2. RunState.persist() 在每轮结束后调用
    3. 失败按 4 类判定处理
    4. 真实执行每个阶段（不模拟）
    """

    def __init__(
        self,
        config: LoopConfig,
        project_root: Path,
        git_driver,
        notes_memory,
        auto_skill_loader,
        smart_confirmation,
        run_state,
        dispatcher_adapter,
        stage_handlers: Dict[StageKind, any],
        objective: str = "",
        log: Optional[Callable[[str, str], None]] = None,
        sleep_guard=None,
    ):
        """构造 RalphLoopController。

        Args:
            config: 循环配置
            project_root: 项目根目录
            git_driver: GitDriver 实例
            notes_memory: NotesMemory 实例
            auto_skill_loader: AutoSkillLoader 实例
            smart_confirmation: SmartConfirmation 实例
            run_state: RunState 实例
            dispatcher_adapter: DispatcherAdapter 实例
            stage_handlers: 4 阶段 handler 字典
            objective: 用户目标
            log: 日志函数 (level, message)
            sleep_guard: SleepGuard 实例（可选）
        """
        self._config = config
        self._project_root = Path(project_root).resolve()
        self._worktree_path = self._project_root
        self._git_driver = git_driver
        self._notes_memory = notes_memory
        self._auto_skill_loader = auto_skill_loader
        self._smart_confirmation = smart_confirmation
        self._run_state = run_state
        self._dispatcher_adapter = dispatcher_adapter
        self._stage_handlers = stage_handlers
        self._objective = objective or run_state.state.objective
        self._log = log or (lambda level, msg: None)
        self._sleep_guard = sleep_guard
        self._prev_results: List[IterationResult] = []

    # ------------------------------------------------------------------ #
    # 公共 API                                                            #
    # ------------------------------------------------------------------ #

    def run(self) -> int:
        """主循环入口。

        Returns:
            int: 退出码（0=全部成功；1=部分失败；2=fatal abort；3=命中 stop_when）

        行为：
        1. 标记 running
        2. 循环 while not should_stop()
        3. 每轮：run_one_iteration + commit/rollback + persist
        4. 退出前 final summary
        """
        self._log("info", f"[RalphLoop] 启动 run_id={self._run_state.state.run_id}")
        self._run_state.mark_running()
        # 启动 sleep guard
        if self._sleep_guard is not None:
            try:
                self._sleep_guard.acquire()
            except Exception as e:
                self._log("warn", f"[RalphLoop] SleepGuard 启动失败: {e}")
        try:
            consecutive_failures = 0
            exit_code = 0
            while not self._should_stop():
                iter_index = self._run_state.state.iter_index + 1
                # 记录起始
                start_time = time.time()
                try:
                    iter_result = self._run_one_iteration(iter_index)
                except Exception as e:
                    iter_result = IterationResult(
                        kind="fatal",
                        summary=f"迭代未捕获异常: {type(e).__name__}: {e}",
                        error=e,
                    )
                iter_result.duration_sec = time.time() - start_time
                # 处理 4 类判定
                committed = False
                if iter_result.kind == "success":
                    consecutive_failures = 0
                    commit_result = self._git_driver.commit(
                        f"ralph iter-{iter_index}: {iter_result.summary[:80]}"
                    )
                    if commit_result.success:
                        iter_result.committed = True
                        committed = True
                    else:
                        # commit 失败 → 保留 uncommitted work
                        self._log(
                            "warn",
                            f"[RalphLoop] commit 失败：{commit_result.error_message}",
                        )
                elif iter_result.kind in ("failed", "retriable"):
                    consecutive_failures += 1
                    # 回滚工作区（保留 uncommitted work）
                    rb = self._git_driver.rollback()
                    if not rb.success:
                        self._log("warn", f"[RalphLoop] rollback 失败: {rb.error_message}")
                    # 退避
                    if iter_result.kind == "retriable":
                        self._backoff_sleep(consecutive_failures - 1)
                elif iter_result.kind == "fatal":
                    consecutive_failures += 1
                    self._log("error", f"[RalphLoop] FATAL: {iter_result.summary}")
                # 持久化
                self._run_state.record_iteration(
                    iter_index=iter_index,
                    result_kind=iter_result.kind,
                    summary=iter_result.summary,
                    tokens=iter_result.token_used,
                    committed=committed,
                    error=str(iter_result.error) if iter_result.error else "",
                )
                # append notes
                self._append_notes_for_iter(iter_index, iter_result)
                self._prev_results.append(iter_result)
                # 连续失败 abort
                if consecutive_failures >= self._config.consecutive_failure_abort:
                    self._log(
                        "error",
                        f"[RalphLoop] 连续失败 {consecutive_failures} 次，abort",
                    )
                    self._run_state.mark_aborted("连续失败次数超限")
                    exit_code = 2
                    break
                # 命中 stop_when
                if self._is_stop_when_matched():
                    self._log("info", f"[RalphLoop] 命中 stop_when: {self._config.stop_when}")
                    self._run_state.mark_complete()
                    exit_code = 3
                    break
                # 全部成功计数
                if iter_result.kind == "success" and committed:
                    pass
            else:
                # 自然退出（max_iterations 触发）
                exit_code = 0 if consecutive_failures == 0 else 1
                if exit_code == 0:
                    self._run_state.mark_complete()
                else:
                    self._run_state.mark_failed("达到 max_iterations 仍有失败")
            # final summary
            summary = self._build_final_summary()
            if self._notes_memory is not None:
                try:
                    self._notes_memory.write_final_summary(summary)
                except Exception as e:
                    self._log("warn", f"[RalphLoop] 写 final summary 失败: {e}")
            return exit_code
        finally:
            # 严格 release sleep guard
            if self._sleep_guard is not None:
                try:
                    self._sleep_guard.release()
                except Exception as e:
                    self._log("warn", f"[RalphLoop] SleepGuard release 失败: {e}")

    def run_one_iteration(self, iter_index: int) -> IterationResult:
        """公开 API：执行一次完整迭代。

        Args:
            iter_index: 迭代索引
        """
        return self._run_one_iteration(iter_index)

    def should_stop(self) -> bool:
        """公开 API：判断是否应停止。"""
        return self._should_stop()

    # ------------------------------------------------------------------ #
    # 内部辅助                                                            #
    # ------------------------------------------------------------------ #

    def _run_one_iteration(self, iter_index: int) -> IterationResult:
        """执行一次完整迭代。"""
        # 构造 IterationContext
        notes_snapshot = self._notes_memory.load() if self._notes_memory else ""
        iter_ctx = IterationContext(
            run_id=self._run_state.state.run_id,
            iter_index=iter_index,
            stage=self._config.stage_order[0] if self._config.stage_order else StageKind.DEV,
            current_plan=self._objective,  # 初始 plan = objective
            notes_snapshot=notes_snapshot,
            prev_results=list(self._prev_results),
            project_root=self._project_root,
            worktree_path=self._worktree_path,
            objective=self._objective,
        )
        # 累计 token
        total_token = 0
        # 阶段聚合结果
        stage_kinds: List[StageKind] = list(self._config.stage_order)
        verify_artifacts: dict = {}
        for stage_kind in stage_kinds:
            handler = self._stage_handlers.get(stage_kind)
            if handler is None:
                continue
            iter_ctx.stage = stage_kind
            stage_result = handler.handle(iter_ctx)
            # 累计 token
            token = 0
            if isinstance(stage_result.artifacts, dict):
                token = int(stage_result.artifacts.get("tokens", 0) or 0)
            total_token += token
            if stage_kind == StageKind.VERIFY:
                iter_ctx.verify_artifacts = stage_result.artifacts
                verify_artifacts = stage_result.artifacts
            # 任意阶段 FATAL → 立即返回
            if stage_result.kind == "fatal":
                return IterationResult(
                    kind="fatal",
                    summary=f"阶段 {stage_kind.value} FATAL: {stage_result.summary}",
                    agent_output=iter_ctx.agent_output,
                    token_used=total_token,
                    error=Exception(stage_result.error) if stage_result.error else None,
                )
            # 阶段失败但非 fatal
            if stage_result.kind in ("failed", "retriable"):
                # 收集诊断信息后返回
                diff_stats = (0, 0)
                if verify_artifacts:
                    ds = verify_artifacts.get("diff_stats", [0, 0, 0, 0])
                    diff_stats = (ds[1], ds[2])
                return IterationResult(
                    kind=stage_result.kind,
                    summary=f"阶段 {stage_kind.value}: {stage_result.summary}",
                    agent_output=iter_ctx.agent_output,
                    diff_stats=diff_stats,
                    token_used=total_token,
                    error=Exception(stage_result.error) if stage_result.error else None,
                )
        # 全部阶段 success
        diff_stats = (0, 0)
        test_results = (0, 0, 0)
        if verify_artifacts:
            ds = verify_artifacts.get("diff_stats", [0, 0, 0, 0])
            diff_stats = (ds[1], ds[2])
            tr = verify_artifacts.get("test_results", [0, 0, 0])
            test_results = (tr[0], tr[1], tr[2])
        return IterationResult(
            kind="success",
            summary=f"iter-{iter_index} 全阶段完成（{len(stage_kinds)} stages）",
            agent_output=iter_ctx.agent_output,
            diff_stats=diff_stats,
            test_results=test_results,
            token_used=total_token,
        )

    def _should_stop(self) -> bool:
        """判断是否应停止（短路求值）。"""
        # 1. max_iterations
        if self._run_state.state.iter_index >= self._config.max_iterations:
            return True
        # 2. max_tokens
        if self._run_state.state.cumulative_tokens >= self._config.max_tokens:
            return True
        # 3. stop_when（已通过 _is_stop_when_matched 判断）
        # 4. RunState.status
        if self._run_state.state.status in ("completed", "aborted", "failed"):
            return True
        return False

    def _is_stop_when_matched(self) -> bool:
        """检查 stop_when 条件是否匹配。

        简单实现：基于最近 N 次结果的 summary 拼接后做关键词匹配。
        复杂 LLM 评估不在本 Phase 18 范围。
        """
        if not self._config.stop_when:
            return False
        stop_keywords = self._config.stop_when.lower().split()
        if not stop_keywords:
            return False
        # 检查最近 5 次结果的 summary
        recent = self._prev_results[-5:]
        for r in recent:
            summary_lower = r.summary.lower()
            # 所有关键词都需出现
            if all(kw in summary_lower for kw in stop_keywords):
                return True
        return False

    def _backoff_sleep(self, attempt: int) -> None:
        """指数退避 + jitter。"""
        base = self._config.backoff_base_sec
        max_sec = self._config.backoff_max_sec
        sleep_sec = min(max_sec, base * (2 ** max(0, attempt)))
        # ± 10% jitter
        sleep_sec *= random.uniform(0.9, 1.1)
        if sleep_sec > 0.1:
            self._log("info", f"[RalphLoop] 退避 {sleep_sec:.2f}s（attempt={attempt}）")
            time.sleep(sleep_sec)

    def _append_notes_for_iter(self, iter_index: int, result: IterationResult) -> None:
        """把本轮结果追加到 notes.md。"""
        if self._notes_memory is None:
            return
        try:
            from autonomous.notes_memory import NotesSection
            from datetime import datetime, timezone
            section = NotesSection(
                title=f"Iteration {iter_index}: {result.kind}",
                body=(
                    f"## {result.summary}\n\n"
                    f"```\ndiff: +{result.diff_stats[0]} -{result.diff_stats[1]}\n"
                    f"tests: passed={result.test_results[0]} "
                    f"failed={result.test_results[1]} "
                    f"skipped={result.test_results[2]}\n"
                    f"tokens: {result.token_used}\n"
                    f"duration: {result.duration_sec:.2f}s\n"
                    f"committed: {result.committed}\n```"
                ),
                timestamp=datetime.now(timezone.utc).isoformat(),
                iter_index=iter_index,
                tags=[result.kind],
            )
            self._notes_memory.append(section)
        except Exception as e:
            self._log("warn", f"[RalphLoop] append notes 失败: {e}")

    def _build_final_summary(self) -> str:
        """构建最终总结 markdown。"""
        state = self._run_state.state
        success_count = sum(1 for r in self._prev_results if r.kind == "success")
        failed_count = sum(1 for r in self._prev_results if r.kind in ("failed", "retriable"))
        fatal_count = sum(1 for r in self._prev_results if r.kind == "fatal")
        total_tokens = sum(r.token_used for r in self._prev_results)
        total_duration = sum(r.duration_sec for r in self._prev_results)
        return (
            f"## Ralph Run Summary\n\n"
            f"- run_id: {state.run_id}\n"
            f"- status: {state.status}\n"
            f"- iterations: {state.iter_index} (success={success_count}, failed={failed_count}, fatal={fatal_count})\n"
            f"- commits: {state.commits_made}\n"
            f"- tokens: {total_tokens}\n"
            f"- duration: {total_duration:.2f}s\n"
            f"- objective: {state.objective[:200]}\n"
        )


__all__ = [
    "StageKind",
    "LoopConfig",
    "IterationContext",
    "IterationResult",
    "RalphLoopController",
]


def generate_run_id() -> str:
    """生成唯一 run_id（短格式）。"""
    return f"r-{uuid.uuid4().hex[:12]}"
