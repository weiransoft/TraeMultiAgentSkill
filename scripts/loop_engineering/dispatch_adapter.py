"""Loop Engineering 专用子代理调度适配器。

本适配器负责把 HandoffItem 分发给单个 V3 子代理（architect / solo-coder / test-expert 等），
而不是进入 autonomous 模式。它通过显式关闭所有 plugin 匹配 flag，让 GoalDispatcher
fallthrough 到 dispatch_agent_v2，从而避免 Loop Engineering 内部再启动一个完整的
RalphAutonomousPlugin 循环。

设计约束：
- stateless：不持有可变实例状态。
- 真实调用：通过 facade._dispatch_through_v3 复用现有 V3 调度能力。
- 失败安全：异常时返回 AdapterInvokeResult(kind="fatal")，不向上抛异常。
"""

from __future__ import annotations

import argparse
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from autonomous.dispatcher_adapter import AdapterInvokeResult


@dataclass
class LoopDispatchAdapter:
    """Loop 专用子代理调度适配器。"""

    project_root: Path = field(default_factory=lambda: Path(".").resolve())
    facade_module: Optional[Any] = None
    log: Optional[Any] = None

    def _info(self, message: str) -> None:
        """输出 INFO 级别日志。

        日志回调签名与 PluginContext.log 保持一致：(message, level)。
        """
        if self.log is not None:
            try:
                self.log(message, "INFO")
            except Exception:
                pass

    def invoke(
        self,
        task: str,
        agent: str = "auto",
    ) -> AdapterInvokeResult:
        """调用 V3 调度器执行单个子代理任务。

        Args:
            task: 任务描述。
            agent: 子代理角色（如 architect / solo-coder / test-expert）。

        Returns:
            AdapterInvokeResult: 调用结果。
        """
        if not task or not task.strip():
            return AdapterInvokeResult(
                success=False,
                kind="fatal",
                summary="任务描述为空",
            )

        facade = self._get_facade()
        if facade is None:
            return AdapterInvokeResult(
                success=False,
                kind="fatal",
                summary="facade 模块不可用（无法导入 _dispatch_through_v3）",
            )

        args = self._build_args(task, agent)
        try:
            self._info(
                f"[LoopDispatchAdapter] 调用 dispatcher: agent={agent} "
                f"task={task[:60]}..."
            )
            rc = facade._dispatch_through_v3(args)
            if rc == 0:
                return AdapterInvokeResult(
                    success=True,
                    kind="success",
                    output=f"dispatcher 返回码 {rc}",
                    summary=f"子代理 {agent} 执行成功（rc={rc}）",
                )
            if rc in (1, 2):
                return AdapterInvokeResult(
                    success=False,
                    kind="retriable",
                    output=f"dispatcher 返回码 {rc}",
                    summary=f"子代理 {agent} 执行失败（rc={rc}，可重试）",
                )
            return AdapterInvokeResult(
                success=False,
                kind="fatal",
                output=f"dispatcher 返回码 {rc}",
                summary=f"子代理 {agent} 遇到致命错误（rc={rc}）",
            )
        except Exception as exc:
            return AdapterInvokeResult(
                success=False,
                kind="fatal",
                summary=(f"子代理 {agent} 调度异常: {type(exc).__name__}: {exc}"),
                error=exc,
                error_trace=traceback.format_exc(),
            )

    def _get_facade(self) -> Optional[Any]:
        """延迟导入 facade 模块，避免循环导入。"""
        if self.facade_module is not None:
            return self.facade_module
        try:
            import facade as _facade

            self.facade_module = _facade
            return _facade
        except Exception:
            return None

    def _build_args(self, task: str, agent: str) -> argparse.Namespace:
        """构造用于 _dispatch_through_v3 的 argparse.Namespace。

        关键：关闭所有 plugin 匹配 flag，确保调度器 fallthrough 到 dispatch_agent_v2。
        """
        project_root = str(self.project_root)
        return argparse.Namespace(
            task=task,
            agent=agent,
            project_root=project_root,
            task_file=None,
            output=None,
            verbose=False,
            dry_run=False,
            use_v1=False,
            project_full_lifecycle=False,
            loop=1,
            goal=None,
            goal_desc=None,
            criteria=[],
            convergence_window=3,
            multi_goal=None,
            goal_parent=None,
            goal_depends=[],
            goal_aggregation="AND",
            goal_resume=None,
            goal_resume_force=False,
            goal_max_resume_count=3,
            reuse_threshold=0.85,
            disable_iteration_reuse=False,
            max_concurrent=10,
            goal_report=None,
            goal_cancel=None,
            goal_graph=None,
            goal_graph_format="mermaid",
            goal_graph_output=None,
            goal_graph_desc_max=100,
            hot_reload=False,
            hot_reload_dir="plugins_extra",
            hot_reload_interval=5.0,
            # 关键：关闭 autonomous 模式，避免命中 RalphAutonomousPlugin
            autonomous=False,
            auto_max_iterations=50,
            # auto_max_tokens=0 表示不限制（与 LoopConfig/AutonomousConfig 对齐）
            auto_max_tokens=0,
            auto_stop_when="",
            auto_test_command="python3 -m unittest discover -s tests -p 'test_*.py'",
            auto_stage_order="plan,dev,verify,fix",
            auto_backoff_base=1.0,
            auto_backoff_max=60.0,
            auto_failure_abort=3,
            auto_resume=None,
            auto_resume_latest=False,
            auto_no_caffeinate=False,
            auto_no_commit=False,
            auto_confirm_mode="smart",
            auto_run_dir=".gnhf/runs",
            auto_git_author_name="Ralph Autonomous Agent",
            auto_git_author_email="ralph@trae-multi-agent.local",
            auto_security_analyzer="builtin",
            auto_notes_path="notes.md",
            auto_max_size_kb=1024,
            auto_trim_keep_last_n=20,
            # Loop Engineering 自身 flag 必须关闭，避免递归匹配
            loop_engineering=False,
            loop_type="coding",
            loop_discovery="auto",
            loop_evaluator="strict",
            loop_human_checkpoint_every=5,
            loop_max_iterations=50,
            # loop_max_tokens=0 表示不限制（与 LoopConfig/AutonomousConfig 对齐）
            loop_max_tokens=0,
            loop_sampling_read_ratio=0.1,
            loop_stop_when="",
        )


__all__ = ["LoopDispatchAdapter"]
