"""V3 Legacy 入口：完整搬迁 god module 5 个 dispatch 函数 + 助手。

B-1 修复：所有 dispatch 函数从 trae_agent_dispatch_v2.py 迁出，
避免 facade ↔ 薄壳循环依赖。

约束：
- 函数体保持原样（保持业务行为 100% 一致）
- 内部 lazy import 业务层（goal_orchestrator / loop_goal / dag_visualizer）
- 顶层 try/except 保留原降级逻辑（graceful degradation）

import 边界（风险-10）：
- 本文件不允许 import facade 或 trae_agent_dispatch_v2
- 本文件内部 lazy import 业务层（goal_orchestrator / loop_goal / dag_visualizer）
"""
import logging
import os
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, Dict, Optional

_log = logging.getLogger("dispatch.legacy")


# ============================================================
# 日志助手（搬迁自 trae_agent_dispatch_v2.py line 318-336）
# ============================================================
def log(message: str, level: str = 'INFO') -> None:
    """统一日志输出（保持与 god module 一致语义）。

    Args:
        message: 日志消息
        level: 日志级别 (INFO, WARNING, ERROR, SUCCESS)
    """
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    level_colors = {
        'INFO': '\033[94m',
        'WARNING': '\033[93m',
        'ERROR': '\033[91m',
        'SUCCESS': '\033[92m'
    }
    reset_color = '\033[0m'
    color = level_colors.get(level, '')

    print(f"{color}[{timestamp}] [{level}] {message}{reset_color}")


# ============================================================
# GoalStatus 顶层导入（搬迁自 line 31-46，保证 _is_overall_success 可用）
# ============================================================
try:
    from loop_goal import GoalStatus as _GoalStatus
    GoalStatus = _GoalStatus
    _LOOP_GOAL_AVAILABLE = True
except ImportError:
    # loop_goal 模块不可用时（向后兼容）：提供占位枚举
    class _FallbackGoalStatus:
        ACHIEVED = "achieved"
        FAILED = "failed"
        IN_PROGRESS = "in_progress"
        ABANDONED = "abandoned"
    GoalStatus = _FallbackGoalStatus  # type: ignore[assignment]
    _LOOP_GOAL_AVAILABLE = False


# ============================================================
# v2.0 双层上下文组件导入（搬迁自 line 49-69）
# ============================================================
try:
    from dual_layer_context_manager import (
        DualLayerContextManager,
        TaskDefinition,
        UserProfile
    )
    from skill_registry import SkillRegistry, SkillManifest
    from role_matcher import RoleMatcher, TaskRequirement, MatchResult, create_default_roles
    from workflow_engine_v2 import WorkflowEngineV2 as WorkflowEngine
    NEW_COMPONENTS_AVAILABLE = True
except ImportError as e:
    NEW_COMPONENTS_AVAILABLE = False
    log(f'警告：无法导入新组件，将使用旧版本逻辑：{e}', 'WARNING')

# 导入 Claude Code SubAgent 适配器
try:
    from claude_code_subagent_adapter import ClaudeCodeSubAgentAdapter, invoke_subagent
    CLAUDE_CODE_ADAPTER_AVAILABLE = True
except ImportError as e:
    CLAUDE_CODE_ADAPTER_AVAILABLE = False
    log(f'警告：无法导入 Claude Code 适配器：{e}', 'WARNING')


# ============================================================
# dispatch_agent_v2 (搬迁自 line 339-416)
# ============================================================
def dispatch_agent_v2(agent_type: str, task: str, task_id: Optional[str] = None,
                     project_root: str = ".", progress: Optional[Dict] = None,
                     cybernetics_enabled: bool = True) -> bool:
    """v2.0 调度逻辑 - 使用双层上下文和智能匹配 + Cybernetics 增强。"""
    if progress is None:
        progress = {}

    # Cybernetics 增强桥接
    bridge = None
    validation = {"passed": True}
    if cybernetics_enabled:
        try:
            from cybernetics_bridge import CyberneticsBridge
            bridge = CyberneticsBridge(project_root=project_root)
        except Exception as e:
            log(f'⚠️  Cybernetics 桥接层初始化失败，将使用无增强模式：{e}', 'WARNING')
            bridge = None

    try:
        # 执行前验证（Guard + Karpathy）
        if bridge:
            task_dict = bridge._build_task_dict(agent_type, task, task_id)
            validation = bridge._pre_execute_check(task_dict)
            if not validation.get('passed', True):
                log(f'⚠️  Guard 验证警告：{validation.get("warnings", [])}', 'WARNING')
                if validation.get('karpathy_violations'):
                    for v in validation['karpathy_violations']:
                        log(f'  📛 Karpathy 违规：{v.get("description", "")}', 'WARNING')

        # 检测运行环境，优先使用 Claude Code SubAgent
        if CLAUDE_CODE_ADAPTER_AVAILABLE:
            log('🚀 使用 Claude Code SubAgent 适配器', 'SUCCESS')
            result = _dispatch_via_claude_code(agent_type, task, task_id, project_root, progress)
        else:
            # 降级到原有逻辑
            log('⚠️  使用双层上下文管理器（Trae IDE）', 'WARNING')
            result = _dispatch_via_trae(agent_type, task, task_id, project_root, progress)

        # 执行后处理（反馈收集 + 性能画像更新）
        if bridge:
            try:
                task_dict = bridge._build_task_dict(agent_type, task, task_id)
                bridge._post_execute_process(
                    task_dict=task_dict,
                    success=result,
                    strategy=bridge.strategy_resolver.select_strategy(task_dict),
                    validation=validation,
                    execution_time=0.0
                )
            except Exception as e:
                log(f'⚠️  Cybernetics 执行后处理异常：{e}', 'WARNING')

        return result

    except Exception as e:
        log(f'❌ 调度失败：{e}', 'ERROR')
        import traceback
        traceback.print_exc()
        return False


# ============================================================
# _dispatch_via_claude_code (搬迁自 line 419-502)
# ============================================================
def _dispatch_via_claude_code(agent_type: str, task: str, task_id: Optional[str],
                             project_root: str, progress: Dict,
                             ponytail_prompt: str = "") -> bool:
    """通过 Claude Code SubAgent 调度。

    v2 修订：新增 ponytail_prompt 参数，支持 DevHandler/FixHandler 注入决策梯。
    ponytail_prompt 非空时，追加到 context dict 的 ponytail_decision_ladder 字段，
    被 _build_agent_prompt 用 json.dumps 拼到 LLM prompt 末尾。

    Args:
        agent_type: agent 类型
        task: 任务描述
        task_id: 任务 ID
        project_root: 项目根目录
        progress: 进度字典
        ponytail_prompt: Ponytail 决策梯 prompt 片段（可选，由 DevHandler/FixHandler 注入）
    """
    try:
        # 1. 初始化 Claude Code SubAgent 适配器
        adapter = ClaudeCodeSubAgentAdapter(skill_root=project_root)

        # 2. 提取任务 ID
        actual_task_id = task_id
        if not actual_task_id:
            task_parts = task.split(' - ')
            if len(task_parts) > 0:
                actual_task_id = task_parts[0].strip()

        # 3. 构建上下文（v2 修订：追加 ponytail_decision_ladder 字段）
        context = {
            'task_id': actual_task_id,
            'project_root': project_root,
            'timestamp': datetime.now().isoformat(),
            'karpathy_principles': {
                'think_before_coding': '明确假设、问清楚、不隐藏困惑',
                'simplicity_first': '最小代码、无 speculative features',
                'surgical_changes': '只改必要的、不改无关的',
                'goal_driven': '定义成功标准、验证检查点'
            },
            # 【新增】Ponytail 决策梯注入（作为 Karpathy Simplicity First 的可执行步骤）
            # 由 DevHandler/FixHandler 通过 ponytail_prompt 参数传入
            # _build_agent_prompt 会把 context 用 json.dumps 拼到 prompt 末尾
            'ponytail_decision_ladder': ponytail_prompt,
        }

        # 4. 调用 subagent
        log(f'🤖 调用 Claude Code SubAgent: {agent_type}', 'INFO')
        result = adapter.invoke_agent(agent_type, task, context)

        # 5. 处理结果
        if result.get('success'):
            log(f'✅ SubAgent 调用成功', 'SUCCESS')
            log(f'   平台：{result.get("platform", "unknown")}', 'INFO')

            if result.get('output'):
                log(f'\n{result["output"]}', 'INFO')

            # 6. 更新进度
            if actual_task_id:
                from trae_agent_dispatch import update_task_status
                update_task_status(
                    progress,
                    actual_task_id,
                    '✅ 已完成',
                    f'任务已完成，角色：{agent_type}',
                    project_root
                )

            return True
        else:
            error_msg = result.get('error', '未知错误')
            log(f'❌ SubAgent 调用失败：{error_msg}', 'ERROR')

            if actual_task_id:
                from trae_agent_dispatch import update_task_status
                update_task_status(
                    progress,
                    actual_task_id,
                    '❌ 失败',
                    f'SubAgent 调用失败：{error_msg}',
                    project_root
                )

            return False

    except Exception as e:
        log(f'❌ Claude Code SubAgent 调度失败：{e}', 'ERROR')
        import traceback
        traceback.print_exc()
        return False


# ============================================================
# _dispatch_via_trae (搬迁自 line 505-659)
# ============================================================
def _dispatch_via_trae(agent_type: str, task: str, task_id: Optional[str],
                      project_root: str, progress: Dict) -> bool:
    """通过 Trae IDE 双层上下文管理器调度（原有逻辑）。"""
    try:
        # 1. 初始化双层上下文管理器
        project_root_path = Path(project_root)

        if '.trae' in project_root_path.parts and 'skills' in project_root_path.parts:
            skill_root = project_root
        else:
            skill_root = str(project_root_path / '.trae' / 'skills' / 'trae-multi-agent')

        context_manager = DualLayerContextManager(
            project_root=project_root,
            skill_root=skill_root
        )

        # 2. 初始化角色匹配器
        matcher = RoleMatcher()
        roles = create_default_roles()
        for role in roles:
            matcher.register_role(role)

        # 3. 创建工作流引擎
        workflow_engine = WorkflowEngine(
            storage_path=str(Path(project_root) / '.trae' / 'skills' / 'trae-multi-agent')
        )

        # 4. 创建任务定义
        task_def = TaskDefinition(
            task_id=task_id or f"TASK-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            title=task,
            description=task,
            goals=[task],
            constraints=[]
        )

        # 5. 开始任务（自动注入相关知识）
        log(f'🚀 启动任务：{task_def.task_id}', 'SUCCESS')
        task_ctx = context_manager.start_task(task_def)

        # 6. 智能匹配角色
        requirement = TaskRequirement(
            task_id=task_def.task_id,
            title=task_def.title,
            description=task_def.description
        )

        matched_roles = matcher.match(requirement, top_k=3)
        log(f'🎯 匹配到 {len(matched_roles)} 个角色:', 'INFO')
        for i, result in enumerate(matched_roles, 1):
            log(f'   {i}. {result.role_name} (置信度：{result.confidence:.2%})', 'INFO')

        # 7. 选择最佳角色（或用户指定的角色）
        if matched_roles:
            if agent_type == 'auto':
                best_match = matched_roles[0]
            else:
                best_match = None
                for result in matched_roles:
                    if result.role_id == agent_type:
                        best_match = result
                        break

                if not best_match:
                    registered_role = matcher.roles.get(agent_type)
                    if registered_role:
                        best_match = MatchResult(
                            role_id=registered_role.role_id,
                            role_name=registered_role.name,
                            confidence=1.0,
                            reasons=["用户直接指定角色"],
                            matched_capabilities=registered_role.capabilities
                        )
                        log(f'✅ 直接使用指定角色：{best_match.role_name}', 'SUCCESS')
                    else:
                        best_match = matched_roles[0]
                        log(f'⚠️  未找到指定角色 {agent_type}，使用最佳匹配：{best_match.role_name}', 'WARNING')

            log(f'✅ 选择角色：{best_match.role_name}', 'SUCCESS')

            # 8. 记录思考
            task_ctx.add_thought(
                role=best_match.role_id,
                thought_type="decision",
                content=f"选择角色 {best_match.role_name} 执行任务",
                context={
                    "confidence": best_match.confidence,
                    "reasons": best_match.reasons
                }
            )

            # 9. 模拟执行
            log(f'▶️  执行任务...', 'INFO')
            task_ctx.add_artifact(
                "EXECUTION",
                {
                    "role": best_match.role_id,
                    "task": task,
                    "status": "completed"
                },
                role=best_match.role_id
            )

            # 10. 完成任务（自动沉淀经验）
            context_manager.complete_task(task_def.task_id)

            # 11. 更新进度
            if task_id:
                from trae_agent_dispatch import update_task_status
                update_task_status(progress, task_id, '✅ 已完成',
                                 f'任务已完成，角色：{best_match.role_name}', project_root)

            # 12. 显示统计
            stats = context_manager.get_statistics()
            log(f'📊 上下文统计:', 'INFO')
            log(f'   全局上下文版本：{stats["global_context"]["version"]}', 'INFO')
            log(f'   知识库条目：{stats["global_context"]["knowledge_count"]}', 'INFO')
            log(f'   经验库条目：{stats["global_context"]["experience_count"]}', 'INFO')

            return True
        else:
            log(f'❌ 未匹配到合适的角色', 'ERROR')
            return False

    except Exception as e:
        log(f'❌ 调度失败：{e}', 'ERROR')
        import traceback
        traceback.print_exc()
        return False


# ============================================================
# dispatch_agent (搬迁自 line 662-706)
# ============================================================
def dispatch_agent(agent_type: str, task: str, project_root: str,
                  task_file: str, use_v1: bool = False) -> bool:
    """调度智能体角色。

    Args:
        agent_type: 智能体角色类型
        task: 任务描述
        project_root: 项目根目录
        task_file: 任务文件路径
        use_v1: 是否使用 v1.0 版本

    Returns:
        bool: 调度是否成功
    """
    log(f'🎯 开始调度智能体角色：{agent_type}', 'INFO')
    log(f'📝 任务：{task}', 'INFO')
    log(f'📁 项目根目录：{project_root}', 'INFO')

    from trae_agent_dispatch import load_task_progress, update_task_status
    progress = load_task_progress(project_root)

    task_id = None
    task_parts = task.split(' - ')
    if len(task_parts) > 0:
        task_id = task_parts[0].strip()

    if task_id:
        update_task_status(progress, task_id, '进行中', '任务已提交给智能体执行', project_root)

    if not use_v1 and NEW_COMPONENTS_AVAILABLE:
        log('🚀 使用 v2.0 双层上下文增强版', 'SUCCESS')
        success = dispatch_agent_v2(agent_type, task, task_id, project_root, progress)
    else:
        log('⚠️  使用 v1.0 简单版本', 'WARNING')
        log(f'✅ 任务已完成（v1.0 模拟）', 'SUCCESS')
        if task_id:
            update_task_status(progress, task_id, '✅ 已完成', '任务已完成', project_root)
        success = True

    return success


# ============================================================
# _module_level_single_dispatch (搬迁自 line 709-746)
# ============================================================
def _module_level_single_dispatch(
    agent_type: str = 'goal_orchestrator',
    task: str = '',
    task_id: Optional[str] = None,
    project_root: str = '.',
    progress: Optional[Dict] = None,
) -> bool:
    """模块级单 Goal dispatch 函数（Pickle 兼容：B-1 修复）。"""
    return dispatch_agent_v2(
        agent_type=agent_type,
        task=task,
        task_id=task_id,
        project_root=project_root,
        progress=progress if progress is not None else {},
        cybernetics_enabled=True,
    )


# ============================================================
# dispatch_agent_v2_with_loop_goal (搬迁自 line 749-891)
# ============================================================
def dispatch_agent_v2_with_loop_goal(
    agent_type: str,
    task: str,
    project_root: str,
    loop_count: int = 1,
    goal_id: Optional[str] = None,
    goal_desc: Optional[str] = None,
    criteria: Optional[list] = None,
    convergence_window: int = 3,
    task_file: Optional[str] = None,
) -> bool:
    """Phase 11 dispatch_agent_v2 的 /loop + /goal 包装器。"""
    from loop_goal import (
        Goal,
        GoalRegistry,
        GoalStatus,
        LoopConfig,
        LoopGoalError,
        LoopGoalExecutor,
    )

    from trae_agent_dispatch import load_task_progress, update_task_status
    progress = load_task_progress(project_root)

    task_id = None
    task_parts = task.split(' - ')
    if len(task_parts) > 0:
        task_id = task_parts[0].strip()

    storage_root = os.path.join(str(project_root), '.trae', 'goals')
    registry = GoalRegistry(storage_root=storage_root)

    goal: Optional[Goal] = None
    if goal_id is not None:
        existing = registry.get_goal(goal_id)
        if existing is not None:
            log(f'♻️  复用已存在目标：{goal_id} (status={existing.status.value})', 'INFO')
            goal = existing
        else:
            if not goal_desc:
                log(
                    f'❌ 目标 {goal_id} 不存在且未提供 --goal-desc，无法创建',
                    'ERROR',
                )
                return False
            try:
                goal = registry.create_goal(
                    description=goal_desc,
                    criteria=criteria or [],
                    goal_id=goal_id,
                    max_iterations=loop_count,
                    convergence_window=convergence_window,
                    created_by=agent_type,
                    task_template=task,
                )
                log(
                    f'✅ 目标已创建：{goal_id} (criteria={len(goal.success_criteria)}, '
                    f'max_iterations={goal.max_iterations})',
                    'SUCCESS',
                )
            except LoopGoalError as e:
                log(f'❌ 创建目标失败：{e}', 'ERROR')
                return False

    loop_config = LoopConfig(
        max_iterations=max(1, loop_count),
        convergence_window=convergence_window,
        stop_on_success=True,
        inter_iteration_delay_seconds=0.0,
    )

    executor = LoopGoalExecutor(registry=registry)

    bound_dispatch_fn = partial(
        _module_level_single_dispatch,
        project_root=str(project_root),
    )

    log(
        f'🔁 启动 /loop 循环：max_iterations={loop_config.max_iterations}, '
        f'goal_id={goal_id or "无"}',
        'INFO',
    )

    result = executor.execute_with_loop_goal(
        task=task,
        agent_type=agent_type,
        dispatch_fn=bound_dispatch_fn,
        project_root=str(project_root),
        loop_config=loop_config,
        goal=goal,
    )

    log(
        f'📊 循环结束：total_iterations={result["total_iterations"]}, '
        f'converged_early={result["converged_early"]}, '
        f'success_early={result["success_early"]}, '
        f'status={result.get("status", "no-goal")}',
        'INFO',
    )

    if task_id:
        final_status = '✅ 已完成' if result.get("success_early") else '⏸ 已停止'
        update_task_status(
            progress, task_id, final_status,
            f'循环执行结束：{result["total_iterations"]} 次', project_root
        )

    return _is_overall_success(result)


# ============================================================
# _is_overall_success (搬迁自 line 894-940)
# ============================================================
def _is_overall_success(result: Dict[str, Any]) -> bool:
    """判定整体成功语义（Phase 11 P0-1 + P1-1 修复）。

    明确区分"达成 / 收敛 / 跑满 / 失败"四种状态。
    """
    if "status" not in result:
        return result.get("total_iterations", 0) > 0

    status = result["status"]
    if status == GoalStatus.ACHIEVED.value:
        return True
    if status == GoalStatus.FAILED.value:
        return False
    if status == GoalStatus.IN_PROGRESS.value:
        if result.get("converged_early"):
            return True
        if result.get("has_criteria"):
            return False
        return True
    return False


# ============================================================
# dispatch_agent_v2_with_goal_resume (搬迁自 line 947-1002)
# ============================================================
def dispatch_agent_v2_with_goal_resume(
    goal_id: str,
    force: bool = False,
    max_resume_count: int = 3,
    project_root: str = ".",
) -> bool:
    """Phase 13.2: 续跑模式 dispatch 入口。"""
    from goal_orchestrator import (
        GoalResumeError,
        GoalResumeManager,
    )
    from loop_goal import GoalRegistry, GoalStatus

    storage_root = os.path.join(str(project_root), '.trae', 'goals')
    registry = GoalRegistry(storage_root=storage_root)
    mgr = GoalResumeManager(registry)

    try:
        resumed = mgr.resume(goal_id, force=force)
    except GoalResumeError as e:
        log(f'❌ 续跑失败：{e}', 'ERROR')
        return False
    except Exception as e:
        log(f'❌ 续跑异常：{e}', 'ERROR')
        return False

    log(
        f'✅ 续跑成功：goal={goal_id}, '
        f'status={resumed.status.value if hasattr(resumed.status, "value") else resumed.status}, '
        f'resume_count={resumed.resume_count}',
        'SUCCESS',
    )
    return resumed.status in (
        GoalStatus.ACTIVE,
        GoalStatus.IN_PROGRESS,
        GoalStatus.ACHIEVED,
    )


# ============================================================
# dispatch_agent_v2_with_multi_goal (搬迁自 line 1005-1111)
# ============================================================
def dispatch_agent_v2_with_multi_goal(
    root_goal_id: str,
    max_concurrent: int = 10,
    reuse_threshold: float = 0.85,
    reuse_enabled: bool = True,
    report_format: Optional[str] = None,
    project_root: str = ".",
) -> bool:
    """Phase 13.4: 多 Goal 编排 dispatch 入口。"""
    from goal_orchestrator import (
        GoalGraphCycleError,
        GoalGraphDepthError,
        GoalGraphIntegrityError,
        GoalGraphSizeError,
        GoalOrchestrator,
        GoalNotFoundError as OrchestratorGoalNotFoundError,
    )
    from loop_goal import GoalRegistry, GoalStatus, LoopConfig

    storage_root = os.path.join(str(project_root), '.trae', 'goals')
    registry = GoalRegistry(storage_root=storage_root)

    orchestrator = GoalOrchestrator(
        registry=registry,
        max_concurrent=max_concurrent,
        reuse_threshold=reuse_threshold,
        reuse_enabled=reuse_enabled,
    )

    bound_dispatch_fn = partial(
        _module_level_single_dispatch,
        agent_type='goal_orchestrator',
        project_root=str(project_root),
    )

    loop_config = LoopConfig(
        max_iterations=10,
        convergence_window=3,
    )

    try:
        report = orchestrator.run(
            root_goal_id=root_goal_id,
            dispatch_fn=bound_dispatch_fn,
            loop_config=loop_config,
            project_root=str(project_root),
        )
    except (GoalGraphCycleError, GoalGraphDepthError,
            GoalGraphIntegrityError, GoalGraphSizeError) as e:
        log(f'❌ DAG 校验失败：{e}', 'ERROR')
        orchestrator.scheduler.shutdown()
        return False
    except OrchestratorGoalNotFoundError as e:
        log(f'❌ Goal 不存在：{e}', 'ERROR')
        orchestrator.scheduler.shutdown()
        return False
    except Exception as e:
        log(f'❌ 编排执行异常：{e}', 'ERROR')
        orchestrator.scheduler.shutdown()
        return False

    log(
        f'📊 编排完成：root={root_goal_id}, '
        f'total_elapsed={report.total_elapsed_seconds:.2f}s, '
        f'reuse_count={report.iteration_reuse_count}, '
        f'total_goals={report.resource_stats.get("total_goals", 0)}',
        'INFO',
    )

    if report_format:
        report_str = (
            report.to_json() if report_format == 'json'
            else report.to_markdown()
        )
        log(f'📄 编排报告（{report_format}）：\n{report_str}', 'INFO')

    orchestrator.scheduler.shutdown()

    root_status = report.goal_tree.status
    if hasattr(root_status, 'value'):
        root_status = root_status.value
    return root_status == GoalStatus.ACHIEVED.value


# ============================================================
# dispatch_agent_v2_with_goal_cancel (搬迁自 line 1114-1205)
# ============================================================
def dispatch_agent_v2_with_goal_cancel(
    goal_id: str,
    project_root: str,
) -> bool:
    """Phase 14 新增：取消运行中 Goal（B-3 修复 + 架构师 Top-1 方向）。"""
    from goal_orchestrator import (
        GoalGraph,
        GoalNotFoundError as OrchestratorGoalNotFoundError,
        GoalOrchestrator,
    )
    from loop_goal import (
        GoalNotFoundError,
        GoalRegistry,
        LoopGoalError,
    )

    log(f'🛑 Phase 14 取消 Goal 启动：goal={goal_id}', 'INFO')

    storage_root = os.path.join(str(project_root), '.trae', 'goals')
    registry = GoalRegistry(storage_root=storage_root)

    try:
        goal = registry.get_goal_or_raise(goal_id)
    except (GoalNotFoundError, LoopGoalError) as e:
        log(f'❌ Goal {goal_id} 不存在或无法加载：{e}', 'ERROR')
        return False

    if goal.status in (GoalStatus.ACHIEVED,):
        log(f'⚠️  Goal {goal_id} 已 ACHIEVED，无需取消', 'WARNING')
        return False

    if goal.status == GoalStatus.ABANDONED:
        log(f'⚠️  Goal {goal_id} 已 ABANDONED，无需重复取消', 'WARNING')
        return False

    try:
        graph = GoalGraph(registry, goal_id)
        descendants = graph.topological_order()
        log(
            f'📊 DAG 包含 {len(descendants)} 个 goal，将级联取消',
            'INFO',
        )
    except OrchestratorGoalNotFoundError as e:
        log(f'❌ DAG 加载失败：{e}', 'ERROR')
        return False
    except Exception as e:
        log(f'❌ DAG 校验失败：{e}', 'ERROR')
        return False

    orchestrator = GoalOrchestrator(registry=registry, max_concurrent=2)
    try:
        cancelled = orchestrator.cancel(goal_id)
        log(
            f'✅ 取消完成：受影响 goal 数 {len(cancelled)}，'
            f'已被标记为 ABANDONED',
            'SUCCESS',
        )
        for gid, state in cancelled.items():
            log(f'   - {gid} (state={state})', 'INFO')
        return True
    except Exception as e:
        log(f'❌ 取消过程异常：{e}', 'ERROR')
        return False
    finally:
        try:
            orchestrator.scheduler.shutdown()
        except Exception:
            pass


# ============================================================
# dispatch_agent_v2_with_goal_graph (搬迁自 line 1208-1305)
# ============================================================
def dispatch_agent_v2_with_goal_graph(
    root_goal_id: str,
    project_root: str,
    format: str = "mermaid",
    output_file: Optional[str] = None,
    desc_max_length: int = 100,
) -> bool:
    """Phase 15 新增：DAG 可视化 CLI 入口。"""
    from dag_visualizer import (
        DagVisualizer,
        GoalGraphVisualizationError,
        InvalidFormatError,
    )
    from loop_goal import (
        GoalNotFoundError,
        GoalRegistry,
        GoalRegistryError,
        LoopGoalError,
    )

    log(
        f'🎨 Phase 15 DAG 可视化启动：root={root_goal_id}, '
        f'format={format}, output={output_file or "stdout"}',
        'INFO',
    )

    storage_root = os.path.join(str(project_root), '.trae', 'goals')
    try:
        registry = GoalRegistry(storage_root=storage_root)
    except Exception as e:
        log(f'❌ 初始化 GoalRegistry 失败：{e}', 'ERROR')
        return False

    visualizer = DagVisualizer(registry)

    try:
        if output_file:
            written_path = visualizer.write_to_file(
                root_goal_id=root_goal_id,
                output_file=output_file,
                project_root=str(project_root),
                format=format,
                desc_max_length=desc_max_length,
            )
            log(
                f'✅ DAG 可视化已写入文件：{written_path}',
                'SUCCESS',
            )
        else:
            content = visualizer.render(
                root_goal_id=root_goal_id,
                format=format,
                desc_max_length=desc_max_length,
            )
            print(content)
            log(
                f'✅ DAG 可视化已输出到 stdout（{len(content)} 字符）',
                'SUCCESS',
            )
        return True
    except InvalidFormatError as e:
        log(f'❌ format 非法：{e}', 'ERROR')
        return False
    except GoalGraphVisualizationError as e:
        log(f'❌ DAG 可视化错误：{e}', 'ERROR')
        return False
    except GoalNotFoundError as e:
        log(f'❌ 根 Goal 不存在：{e}', 'ERROR')
        return False
    except (GoalRegistryError, LoopGoalError) as e:
        log(f'❌ Goal Graph 加载失败：{e}', 'ERROR')
        return False
    except Exception as e:
        log(f'❌ DAG 可视化异常：{e}', 'ERROR')
        return False


__all__ = [
    "log",
    "_is_overall_success",
    "dispatch_agent_v2",
    "dispatch_agent",
    "_module_level_single_dispatch",
    "dispatch_agent_v2_with_loop_goal",
    "dispatch_agent_v2_with_goal_resume",
    "dispatch_agent_v2_with_multi_goal",
    "dispatch_agent_v2_with_goal_cancel",
    "dispatch_agent_v2_with_goal_graph",
]
