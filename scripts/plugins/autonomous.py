"""RalphAutonomousPlugin — Phase 18 入口插件（priority=5）。

设计目标：
- 作为 autonomous 模式的 V3 插件入口
- 不修改 dispatcher（仅注册新 plugin）
- 复用 V3 PluginContext 传项目根 + log
- 内部调用 autonomous 模块的 8 个组件 + RalphLoopController

约束（来自 PHASE18_PLAN.md §2.1）：
- 不破坏 V3 三层结构
- 仅调用 dispatcher.dispatch() 间接走 V3 流程
- 真实实现所有逻辑（无 mock/简化）
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Optional, Set

from plugins.base import GoalCommandPlugin
from dispatcher.plugin_context import PluginContext


class RalphAutonomousPlugin(GoalCommandPlugin):
    """Ralph 风格 autonomous 模式入口插件（priority=5，DESTROY-like）。

    行为：
    1. 检查 args.autonomous / args.auto_resume / args.auto_resume_latest
    2. 初始化 / 加载 RunState
    3. 构造 8 个 autonomous 组件
    4. 启动 SleepGuard（caffeinate）
    5. 启动 RalphLoopController.run()
    6. 退出时 release SleepGuard
    """

    @property
    def name(self) -> str:
        """插件名：autonomous（CLI flag --autonomous）。"""
        return "autonomous"

    @property
    def priority(self) -> int:
        """priority=5（DESTROY-like，比 cancel 略低，autonomous 是长时间运行模式）。"""
        return 5

    @property
    def mutex_with(self) -> Set[str]:
        """autonomous 模式与其他模式互斥。

        互斥集合：
        - goal-cancel: 取消模式
        - goal-graph: 依赖图可视化
        - goal-resume: 续跑模式（autonomous 自带 resume）
        - multi-goal: 多 Goal 编排
        - loop: /loop 循环（autonomous 自带循环）
        """
        return {"goal-cancel", "goal-graph", "goal-resume", "multi-goal", "loop"}

    @property
    def requires_task(self) -> bool:
        """resume 模式可无 --task（False）。"""
        return False

    def matches(self, args: argparse.Namespace) -> bool:
        """检查是否匹配。

        匹配条件（短路求值）：
        1. args.autonomous == True
        2. 或 args.auto_resume 不为 None
        3. 或 args.auto_resume_latest == True
        """
        if getattr(args, "autonomous", False):
            return True
        if getattr(args, "auto_resume", None) is not None:
            return True
        if getattr(args, "auto_resume_latest", False):
            return True
        return False

    def execute(self, args: argparse.Namespace, ctx: PluginContext) -> bool:
        """执行 autonomous 模式。

        Args:
            args: argparse 解析结果
            ctx: 共享上下文（project_root / log / dry_run 等）

        Returns:
            bool: True = 成功（exit_code 0）；False = 失败

        行为：
        1. dry_run 短路（与 dispatcher.dispatch() 行为一致）
        2. 解析配置（CLI flag → LoopConfig）
        3. 初始化 / 加载 RunState
        4. 构造 8 个组件（真实实例化）
        5. 构造 4 阶段 handler
        6. 构造 RalphLoopController
        7. SleepGuard.acquire() + loop.run() + finally: release()
        """
        # 1. dry_run 短路
        if getattr(ctx, "dry_run", False):
            ctx.log("🔄 autonomous 模式：dry_run 短路", "WARNING")
            return True
        # 2. 解析配置
        config = self._build_loop_config(args)
        # 3. 初始化 / 加载 RunState
        run_state, run_dir = self._init_or_load_run_state(args, ctx, config)
        if run_state is None:
            return False
        # 4. 构造 8 个组件
        components = self._build_components(args, ctx, run_state, run_dir, config)
        if components is None:
            return False
        # 5. 构造 4 阶段 handler
        stage_handlers = self._build_stage_handlers(components, config=config)
        # 6. 构造 RalphLoopController
        from autonomous.loop_controller import RalphLoopController
        loop = RalphLoopController(
            config=config,
            project_root=ctx.project_root,
            git_driver=components["git_driver"],
            notes_memory=components["notes_memory"],
            auto_skill_loader=components["auto_skill_loader"],
            smart_confirmation=components["smart_confirmation"],
            run_state=run_state,
            dispatcher_adapter=components["dispatcher_adapter"],
            stage_handlers=stage_handlers,
            objective=run_state.state.objective,
            log=ctx.log,
            sleep_guard=components["sleep_guard"],
        )
        # 7. 启动 SleepGuard + 跑主循环
        sleep_guard = components["sleep_guard"]
        try:
            if components["sleep_guard_enabled"]:
                sleep_guard.acquire()
            ctx.log(
                f"[RalphPlugin] 启动 run_id={run_state.state.run_id} "
                f"objective={run_state.state.objective[:80]}",
                "INFO",
            )
            exit_code = loop.run()
            return exit_code == 0
        except Exception as e:
            ctx.log(f"[RalphPlugin] 未捕获异常: {type(e).__name__}: {e}", "ERROR")
            try:
                run_state.mark_aborted(f"plugin 未捕获异常: {e}")
            except Exception:
                pass
            return False
        finally:
            # 严格 release sleep guard
            if components["sleep_guard_enabled"]:
                try:
                    sleep_guard.release()
                except Exception as e:
                    ctx.log(f"[RalphPlugin] SleepGuard.release 异常: {e}", "WARN")

    # ------------------------------------------------------------------ #
    # 内部辅助                                                            #
    # ------------------------------------------------------------------ #

    def _build_loop_config(self, args: argparse.Namespace):
        """从 CLI args 构造 LoopConfig。"""
        from autonomous.loop_controller import LoopConfig, StageKind
        # 解析 stage_order
        stage_order_str = getattr(args, "auto_stage_order", "plan,dev,verify,fix")
        stage_order_list = [
            s.strip() for s in stage_order_str.split(",") if s.strip()
        ]
        valid_stages = {StageKind.PLAN, StageKind.DEV, StageKind.VERIFY, StageKind.FIX}
        stage_order = []
        for s in stage_order_list:
            try:
                stage_order.append(StageKind(s))
            except ValueError:
                # 跳过未知 stage
                pass
        if not stage_order:
            # fallback 到默认
            stage_order = list(valid_stages)
        return LoopConfig(
            max_iterations=int(getattr(args, "auto_max_iterations", 50)),
            max_tokens=int(getattr(args, "auto_max_tokens", 500_000)),
            stop_when=str(getattr(args, "auto_stop_when", "")),
            stage_order=stage_order,
            backoff_base_sec=float(getattr(args, "auto_backoff_base", 1.0)),
            backoff_max_sec=float(getattr(args, "auto_backoff_max", 60.0)),
            consecutive_failure_abort=int(getattr(args, "auto_failure_abort", 3)),
            git_author_name=str(getattr(args, "auto_git_author_name", "Ralph Autonomous Agent")),
            git_author_email=str(
                getattr(args, "auto_git_author_email", "ralph@trae-multi-agent.local")
            ),
            test_command=str(
                getattr(
                    args,
                    "auto_test_command",
                    "python3 -m unittest discover -s tests -p 'test_*.py'",
                )
            ),
            security_analyzer=str(getattr(args, "auto_security_analyzer", "builtin")),
        )

    def _init_or_load_run_state(
        self,
        args: argparse.Namespace,
        ctx: PluginContext,
        config,
    ):
        """初始化 / 加载 RunState。

        Returns:
            (RunState, run_dir) 或 (None, None)（失败时）
        """
        from autonomous.run_state import RunState
        run_dir_rel = str(getattr(args, "auto_run_dir", ".gnhf/runs"))
        run_root = ctx.project_root / run_dir_rel
        try:
            run_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            ctx.log(f"❌ 创建 run_root 失败: {e}", "ERROR")
            return None, None
        # 1. resume 指定 run
        if getattr(args, "auto_resume", None):
            run_id = args.auto_resume
            run_dir = run_root / run_id
            state_path = run_dir / "state.json"
            if not state_path.exists():
                ctx.log(
                    f"❌ resume 失败：state.json 不存在：{state_path}",
                    "ERROR",
                )
                return None, None
            run_state = RunState(run_dir=run_dir, run_id=run_id)
            ctx.log(f"🔄 resume run_id={run_id}", "INFO")
            return run_state, run_dir
        # 2. resume 最新可续跑 run
        if getattr(args, "auto_resume_latest", False):
            resumable = self._list_resumable_runs(run_root)
            if not resumable:
                ctx.log("❌ 无可 resume 的 run", "ERROR")
                return None, None
            run_dir = resumable[0]
            run_id = run_dir.name
            run_state = RunState(run_dir=run_dir, run_id=run_id)
            ctx.log(f"🔄 resume latest run_id={run_id}", "INFO")
            return run_state, run_dir
        # 3. 新建 run
        run_id = f"r-{uuid.uuid4().hex[:12]}"
        run_dir = run_root / run_id
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            ctx.log(f"❌ 创建 run_dir 失败: {e}", "ERROR")
            return None, None
        # 校验 task（新建模式必须有 task）
        if not args.task and not args.task_file:
            ctx.log("❌ 新建 run 必须提供 --task 或 --task-file", "ERROR")
            return None, None
        objective = args.task
        if not objective and args.task_file:
            # 读取 task_file
            task_file = ctx.project_root / args.task_file
            if task_file.exists():
                objective = task_file.read_text(encoding="utf-8").strip()
        run_state = RunState(
            run_dir=run_dir, run_id=run_id, objective=objective
        )
        run_state.state.objective = objective
        run_state.persist()
        ctx.log(f"🆕 新建 run_id={run_id}", "INFO")
        return run_state, run_dir

    def _list_resumable_runs(self, run_root: Path) -> list:
        """列出可 resume 的 run 目录（按 updated_at 降序）。"""
        if not run_root.exists():
            return []
        candidates = []
        for d in run_root.iterdir():
            if not d.is_dir():
                continue
            state_path = d / "state.json"
            if not state_path.exists():
                continue
            try:
                data = json.loads(state_path.read_text(encoding="utf-8"))
                state = data.get("state", {})
                status = state.get("status", "pending")
                if status in ("running", "failed", "aborted"):
                    candidates.append((d, state.get("updated_at", "")))
            except (OSError, json.JSONDecodeError):
                continue
        # 按 updated_at 降序
        candidates.sort(key=lambda x: x[1], reverse=True)
        return [d for d, _ in candidates]

    def _build_components(
        self,
        args: argparse.Namespace,
        ctx: PluginContext,
        run_state,
        run_dir: Path,
        config,
    ) -> Optional[dict]:
        """构造 8 个 autonomous 组件（v2 修订：新增 ponytail_engine）。

        Returns:
            dict 包含 keys: notes_memory / git_driver / auto_skill_loader /
            smart_confirmation / dispatcher_adapter / sleep_guard /
            sleep_guard_enabled / ponytail_engine / debt_collector

            失败返回 None
        """
        # 1. NotesMemory
        from autonomous.notes_memory import NotesMemory
        notes_path = run_dir / str(
            getattr(args, "auto_notes_path", "notes.md")
        )
        notes_memory = NotesMemory(
            notes_path=notes_path,
            max_size_kb=int(getattr(args, "auto_max_size_kb", 1024)),
            trim_keep_last_n=int(getattr(args, "auto_trim_keep_last_n", 20)),
        )
        # 2. GitDriver
        from autonomous.git_driver import GitDriver
        git_driver = GitDriver(
            repo_root=ctx.project_root,
            run_id=run_state.state.run_id,
            author_name=config.git_author_name,
            author_email=config.git_author_email,
            run_dir=run_dir,
        )
        # 3. AutoSkillLoader
        from autonomous.auto_skill_loader import AutoSkillLoader
        auto_skill_loader = AutoSkillLoader(project_root=ctx.project_root)
        # 4. SmartConfirmation
        from autonomous.smart_confirmation import SmartConfirmation
        smart_confirmation = SmartConfirmation()
        # 5. DispatcherAdapter
        from autonomous.dispatcher_adapter import DispatcherAdapter
        dispatcher_adapter = DispatcherAdapter(log=ctx.log)
        # 6. SleepGuard
        from autonomous.sleep_guard import SleepGuard, SleepGuardMode
        sleep_guard_enabled = not bool(getattr(args, "auto_no_caffeinate", False))
        sleep_guard_mode = (
            SleepGuardMode.ON if sleep_guard_enabled else SleepGuardMode.OFF
        )
        sleep_guard = SleepGuard(mode=sleep_guard_mode, log=lambda m: None)

        # 【新增】7. PonytailRulesetEngine（决策梯引擎，线程安全，无状态修改）
        # 从用户输入解析模式（支持 /ponytail lite/full/ultra/off 命令）
        ponytail_engine = None
        ponytail_mode = None
        try:
            from ponytail.ruleset import PonytailRulesetEngine, PonytailMode
            from ponytail.mode_tracker import ModeTracker

            # 解析用户输入中的 /ponytail 命令（如果有）
            user_task = getattr(args, "task", "") or ""
            parsed_mode = ModeTracker.parse_user_command(user_task)
            if parsed_mode in ModeTracker.VALID_MODES:
                ponytail_mode = PonytailMode(parsed_mode)
                # ultra 模式安全加固：autonomous 模式下禁止 ultra（强制降级为 full）
                # 架构师评审 P0：无人值守 + 激进删除 = 高风险
                if ponytail_mode == PonytailMode.ULTRA:
                    ctx.log(
                        "[Ponytail] ultra 模式在 autonomous 下被禁用，降级为 full",
                        "WARNING",
                    )
                    ponytail_mode = PonytailMode.FULL

            # 构造决策梯引擎（始终构造，由 handler 按角色选择是否注入）
            ponytail_engine = PonytailRulesetEngine(
                skill_root=str(ctx.project_root / ".trae" / "skills" / "trae-multi-agent")
            )
        except ImportError as e:
            # ponytail 模块不可用时，不注入决策梯（向后兼容）
            ctx.log(f"[Ponytail] 模块不可用，跳过决策梯注入：{e}", "WARNING")
            ponytail_engine = None
            ponytail_mode = None

        # 【新增】8. DebtCollector（债务台账收割，供 VerifyHandler 使用）
        debt_collector = None
        try:
            from ponytail.debt_collector import DebtCollector
            debt_collector = DebtCollector()
        except ImportError:
            # debt_collector 模块不可用时，不检测债务（向后兼容）
            pass

        return {
            "notes_memory": notes_memory,
            "git_driver": git_driver,
            "auto_skill_loader": auto_skill_loader,
            "smart_confirmation": smart_confirmation,
            "dispatcher_adapter": dispatcher_adapter,
            "sleep_guard": sleep_guard,
            "sleep_guard_enabled": sleep_guard_enabled,
            # 【新增】Ponytail 决策梯组件
            "ponytail_engine": ponytail_engine,
            "ponytail_mode": ponytail_mode,
            "debt_collector": debt_collector,
        }

    def _build_stage_handlers(
        self,
        components: dict,
        config=None,
    ) -> dict:
        """构造 4 阶段 handler 字典（v2 修订：注入 ponytail_engine）。

        v2 核心变更：
        - 所有 handler 注入 ponytail_engine（按角色差异化）
        - DevHandler/FixHandler 注入 project_root（_dispatch_via_claude_code 需要）
        - VerifyHandler 注入 debt_collector（债务台账检测）
        """
        from autonomous.loop_controller import StageKind
        from autonomous.handlers.plan_handler import PlanHandler
        from autonomous.handlers.dev_handler import DevHandler
        from autonomous.handlers.verify_handler import VerifyHandler
        from autonomous.handlers.fix_handler import FixHandler
        # 从 config 读取 test_command 和 security_analyzer（stateless 契约）
        test_command = config.test_command if config is not None else "python3 -m unittest discover -s tests -p 'test_*.py'"
        security_analyzer = config.security_analyzer if config is not None else "builtin"

        # 【新增】Ponytail 组件（从 components 读取，可能为 None）
        ponytail_engine = components.get("ponytail_engine")
        ponytail_mode = components.get("ponytail_mode")
        debt_collector = components.get("debt_collector")
        # project_root 从 git_driver 获取（GitDriver 已有 repo_root）
        project_root = "."
        git_driver = components.get("git_driver")
        if git_driver is not None and hasattr(git_driver, "repo_root"):
            project_root = str(git_driver.repo_root)

        return {
            StageKind.PLAN: PlanHandler(
                auto_skill_loader=components["auto_skill_loader"],
                notes_memory=components["notes_memory"],
                # 【新增】注入 ponytail_engine（architect 角色 = FULL 强度）
                ponytail_engine=ponytail_engine,
            ),
            StageKind.DEV: DevHandler(
                dispatcher_adapter=components["dispatcher_adapter"],
                smart_confirmation=components["smart_confirmation"],
                auto_skill_loader=components["auto_skill_loader"],
                # 【新增】注入 ponytail_engine + project_root + ponytail_mode
                ponytail_engine=ponytail_engine,
                project_root=project_root,
                ponytail_mode=ponytail_mode,
            ),
            StageKind.VERIFY: VerifyHandler(
                git_driver=components["git_driver"],
                test_command=test_command,
                security_analyzer=security_analyzer,
                # 【新增】注入 ponytail_engine + debt_collector + project_root
                ponytail_engine=ponytail_engine,
                debt_collector=debt_collector,
                project_root=project_root,
            ),
            StageKind.FIX: FixHandler(
                dispatcher_adapter=components["dispatcher_adapter"],
                max_fix_attempts=2,
                # 【新增】注入 ponytail_engine + project_root + ponytail_mode
                ponytail_engine=ponytail_engine,
                project_root=project_root,
                ponytail_mode=ponytail_mode,
            ),
        }


__all__ = ["RalphAutonomousPlugin"]
