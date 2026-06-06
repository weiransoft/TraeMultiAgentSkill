#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trae Agent 调度脚本（v2.0 - 双层上下文增强版）

用于调度不同的智能体角色（架构师、产品经理、测试专家、独立开发者）来实现任务
支持命令行参数配置，方便集成到自动化流程中

新增功能（v2.0）:
- 双层动态上下文管理
- 智能角色匹配
- 工作流编排
- 技能注册管理

Phase 11 新增:
- /loop + /goal 集成：长程任务"目标定义 → 循环迭代 → 收敛退出"能力
- --loop N：循环执行 N 次
- --goal <id>：目标 ID
- --goal-desc <desc>：目标描述
- --criteria <criterion>：验收标准（可多次传入）
"""

import os
import sys
import argparse
import json
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional

# Phase 11 P1-6 修复：将 GoalStatus 提到模块级导入
# 原问题：_is_overall_success（模块级函数）使用 GoalStatus 但仅在
# dispatch_agent_v2_with_loop_goal 函数内部 import，导致 NameError。
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

# 导入新组件
try:
    from dual_layer_context_manager import (
        DualLayerContextManager,
        TaskDefinition,
        UserProfile
    )
    from skill_registry import SkillRegistry, SkillManifest
    from role_matcher import RoleMatcher, TaskRequirement, MatchResult, create_default_roles
    from workflow_engine import WorkflowEngine
    NEW_COMPONENTS_AVAILABLE = True
except ImportError as e:
    NEW_COMPONENTS_AVAILABLE = False
    print(f"警告：无法导入新组件，将使用旧版本逻辑：{e}")

# 导入 Claude Code SubAgent 适配器
try:
    from claude_code_subagent_adapter import ClaudeCodeSubAgentAdapter, invoke_subagent
    CLAUDE_CODE_ADAPTER_AVAILABLE = True
except ImportError as e:
    CLAUDE_CODE_ADAPTER_AVAILABLE = False
    print(f"警告：无法导入 Claude Code 适配器：{e}")


def parse_arguments():
    """
    解析命令行参数
    
    Returns:
        argparse.Namespace: 解析后的参数
    """
    parser = argparse.ArgumentParser(
        description='Trae Agent 调度脚本 v2.0 - 调度不同的智能体角色来实现任务'
    )
    
    parser.add_argument(
        '--task',
        type=str,
        required=True,
        help='任务描述，例如："实现 SOUL-007 专注模式切换测试用例"'
    )
    
    parser.add_argument(
        '--agent',
        type=str,
        choices=['architect', 'product-manager', 'tester', 'solo-coder', 'ui-designer', 'devops', 'auto'],
        default='auto',
        help='指定要调度的智能体角色（默认：auto - 自动匹配）'
    )
    
    parser.add_argument(
        '--project-root',
        type=str,
        default='.',
        help='项目根目录路径（默认：当前目录）'
    )
    
    parser.add_argument(
        '--task-file',
        type=str,
        help='任务文件路径'
    )
    
    parser.add_argument(
        '--output',
        type=str,
        default=None,
        help='输出文件路径（可选）'
    )
    
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='启用详细输出模式'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='仅模拟执行，不实际调用智能体'
    )
    
    parser.add_argument(
        '--use-v1',
        action='store_true',
        help='使用 v1.0 版本逻辑（不使用新组件）'
    )
    
    parser.add_argument(
        '--project-full-lifecycle',
        action='store_true',
        help='启用项目全生命周期模式（8 阶段标准工作流程：需求→架构→UI→测试→任务→开发→测试→发布）'
    )

    # Phase 11 新增：/loop + /goal 集成
    parser.add_argument(
        '--loop',
        type=int,
        default=1,
        help='循环执行次数（默认 1 = 不循环；范围 [1, 100]）'
    )

    parser.add_argument(
        '--goal',
        type=str,
        default=None,
        help='目标 ID（kebab-case，例如：fix-tests / refactor-auth）'
    )

    parser.add_argument(
        '--goal-desc',
        type=str,
        default=None,
        help='目标描述（创建新目标时必填；已存在目标可省略）'
    )

    parser.add_argument(
        '--criteria',
        action='append',
        default=[],
        help='验收标准（可多次传入，例如：--criteria "tests pass" --criteria "no warnings"）'
    )

    parser.add_argument(
        '--convergence-window',
        type=int,
        default=3,
        help='收敛窗口：连续 N 次无新产出则提前退出（默认 3）'
    )

    # Phase 13 新增：多 Goal 编排 CLI 标志
    # --multi-goal <root_id>：以 root_id 为入口执行多 Goal 编排（DAG 调度）
    parser.add_argument(
        '--multi-goal',
        type=str,
        default=None,
        help='以指定 root Goal ID 为入口执行多 Goal 编排（触发 DAG 调度器）',
    )
    # --goal-parent <parent_id>：创建子 Goal 时指定 parent_goal_id
    parser.add_argument(
        '--goal-parent',
        type=str,
        default=None,
        help='创建新 Goal 时指定 parent_goal_id（多 Goal 树）',
    )
    # --goal-depends <dep_id>：为新 Goal 增加 depends_on 依赖（可多次传入）
    parser.add_argument(
        '--goal-depends',
        action='append',
        default=[],
        help='为新 Goal 增加 depends_on 依赖（可多次传入，例如：--goal-depends g1 --goal-depends g2）',
    )
    # --goal-aggregation <strategy>：子 Goal 聚合策略（AND / OR / MAJORITY）
    parser.add_argument(
        '--goal-aggregation',
        type=str,
        default='AND',
        choices=['AND', 'OR', 'MAJORITY'],
        help='子 Goal 聚合策略（AND=全部成功 / OR=任一成功 / MAJORITY=多数成功；默认 AND）',
    )
    # --goal-resume <goal_id>：续跑指定 Goal（无 force 仅续可续跑的）
    parser.add_argument(
        '--goal-resume',
        type=str,
        default=None,
        help='续跑指定 Goal（不带 --force 时仅续可续跑 goal）',
    )
    # --goal-resume-force：强制续跑（覆盖 ABANDONED / FAILED 超限）
    parser.add_argument(
        '--goal-resume-force',
        action='store_true',
        help='强制续跑（包括 ABANDONED 状态的 Goal / FAILED 续跑超限）',
    )
    # --goal-max-resume-count <N>：覆盖单 Goal 续跑上限
    parser.add_argument(
        '--goal-max-resume-count',
        type=int,
        default=3,
        help='覆盖单 Goal 续跑上限（默认 3）',
    )
    # --reuse-threshold <float>：跨 Goal 复用相似度阈值
    parser.add_argument(
        '--reuse-threshold',
        type=float,
        default=0.85,
        help='跨 Goal 复用相似度阈值（0.0-1.0；默认 0.85）',
    )
    # --disable-iteration-reuse：禁用跨 Goal iteration 语义复用
    parser.add_argument(
        '--disable-iteration-reuse',
        action='store_true',
        help='禁用跨 Goal iteration 语义复用',
    )
    # --max-concurrent <N>：DAG 并发 worker 数（D1 优化默认 20）
    parser.add_argument(
        '--max-concurrent',
        type=int,
        default=10,
        help='多 Goal 编排时 DAG 并发 worker 数（默认 10）',
    )
    # --goal-report <format>：编排报告格式（json / md）
    parser.add_argument(
        '--goal-report',
        type=str,
        default=None,
        choices=['json', 'md'],
        help='多 Goal 编排完成后输出报告（json / md）',
    )

    return parser.parse_args()


def log(message: str, level: str = 'INFO'):
    """
    统一日志输出
    
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


def dispatch_agent_v2(agent_type: str, task: str, task_id: Optional[str] = None,
                     project_root: str = ".", progress: Optional[Dict] = None,
                     cybernetics_enabled: bool = True) -> bool:
    """
    v2.0 调度逻辑 - 使用双层上下文和智能匹配 + Cybernetics 增强
    
    v2.5 新增：Cybernetics 增强桥接层
    - Guard 预验证（含 Karpathy 原则检查）
    - 反馈控制环
    - 性能画像更新
    - 检查点自动验证
    
    Args:
        agent_type: 智能体类型
        task: 任务
        task_id: 任务 ID
        project_root: 项目根目录
        progress: 进度数据
        cybernetics_enabled: 是否启用 Cybernetics 增强（默认启用）
        
    Returns:
        bool: 调度是否成功
    """
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


def _dispatch_via_claude_code(agent_type: str, task: str, task_id: Optional[str],
                             project_root: str, progress: Dict) -> bool:
    """
    通过 Claude Code SubAgent 调度
    
    Args:
        agent_type: 智能体类型
        task: 任务
        task_id: 任务 ID
        project_root: 项目根目录
        progress: 进度数据
        
    Returns:
        bool: 调度是否成功
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
        
        # 3. 构建上下文
        context = {
            'task_id': actual_task_id,
            'project_root': project_root,
            'timestamp': datetime.now().isoformat(),
            'karpathy_principles': {
                'think_before_coding': '明确假设、问清楚、不隐藏困惑',
                'simplicity_first': '最小代码、无 speculative features',
                'surgical_changes': '只改必要的、不改无关的',
                'goal_driven': '定义成功标准、验证检查点'
            }
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


def _dispatch_via_trae(agent_type: str, task: str, task_id: Optional[str],
                      project_root: str, progress: Dict) -> bool:
    """
    通过 Trae IDE 双层上下文管理器调度（原有逻辑）
    
    Args:
        agent_type: 智能体类型
        task: 任务
        task_id: 任务 ID
        project_root: 项目根目录
        progress: 进度数据
        
    Returns:
        bool: 调度是否成功
    """
    try:
        # 1. 初始化双层上下文管理器
        # 注意：project_root 可能是实际的业务项目目录（如 /path/to/business/project）
        # 也可能是 skill 根目录（如 /path/to/.trae/skills/trae-multi-agent）
        # 需要根据路径结构正确设置 skill_root
        
        # 将 project_root 转换为 Path 对象
        project_root_path = Path(project_root)
        
        # 检查 project_root 是否已经包含 .trae/skills/trae-multi-agent
        # 如果是，说明传入的 project_root 本身就是 skill 根目录
        if '.trae' in project_root_path.parts and 'skills' in project_root_path.parts:
            # project_root 本身就是 skill 根目录（如 /path/to/.trae/skills/trae-multi-agent）
            skill_root = project_root
        else:
            # 需要拼接 .trae/skills/trae-multi-agent
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
            # 如果是 auto，选择最佳匹配
            if agent_type == 'auto':
                best_match = matched_roles[0]
            else:
                # 首先尝试从匹配结果中查找用户指定的角色
                best_match = None
                for result in matched_roles:
                    if result.role_id == agent_type:
                        best_match = result
                        break
                
                # 如果匹配结果中没有找到，尝试从注册的角色中直接获取
                if not best_match:
                    registered_role = matcher.roles.get(agent_type)
                    if registered_role:
                        # 创建一个 MatchResult 对象
                        best_match = MatchResult(
                            role_id=registered_role.role_id,
                            role_name=registered_role.name,
                            confidence=1.0,  # 直接指定，置信度为 100%
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
            
            # 9. 模拟执行（实际应该调用对应的智能体）
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


def dispatch_agent(agent_type: str, task: str, project_root: str,
                  task_file: str, use_v1: bool = False) -> bool:
    """
    调度智能体角色

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

    # 加载任务进度
    from trae_agent_dispatch import load_task_progress, update_task_status
    progress = load_task_progress(project_root)

    # 提取任务 ID
    task_id = None
    task_parts = task.split(' - ')
    if len(task_parts) > 0:
        task_id = task_parts[0].strip()

    if task_id:
        update_task_status(progress, task_id, '进行中', '任务已提交给智能体执行', project_root)

    # 使用 v2.0 新组件
    if not use_v1 and NEW_COMPONENTS_AVAILABLE:
        log('🚀 使用 v2.0 双层上下文增强版', 'SUCCESS')
        success = dispatch_agent_v2(agent_type, task, task_id, project_root, progress)
    else:
        log('⚠️  使用 v1.0 简单版本', 'WARNING')
        # 简化的 v1 逻辑
        log(f'✅ 任务已完成（v1.0 模拟）', 'SUCCESS')
        if task_id:
            update_task_status(progress, task_id, '✅ 已完成', '任务已完成', project_root)
        success = True

    return success


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
    """
    Phase 11 新增：dispatch_agent_v2 的 /loop + /goal 包装器

    串联 GoalRegistry / LoopGoalExecutor 与现有 dispatch_agent_v2，
    实现"目标定义 → 循环迭代 → 收敛退出"的长程任务执行能力。

    Args:
        agent_type: 智能体角色类型
        task: 任务描述
        project_root: 项目根目录
        loop_count: 循环执行次数（默认 1 = 不循环；范围 [1, 100]）
        goal_id: 目标 ID（kebab-case）
        goal_desc: 目标描述（创建新目标时必填）
        criteria: 验收标准列表
        convergence_window: 收敛窗口
        task_file: 任务文件路径

    Returns:
        bool: 任务是否成功（达成 / 收敛 / 异常退出均返回布尔结果）
    """
    from loop_goal import (
        Goal,
        GoalRegistry,
        GoalStatus,
        LoopConfig,
        LoopGoalError,
        LoopGoalExecutor,
    )

    # 加载任务进度（与 dispatch_agent 一致）
    from trae_agent_dispatch import load_task_progress, update_task_status
    progress = load_task_progress(project_root)

    # 提取任务 ID（与 dispatch_agent 一致）
    task_id = None
    task_parts = task.split(' - ')
    if len(task_parts) > 0:
        task_id = task_parts[0].strip()

    # 初始化 Registry（持久化根：{project_root}/.trae/goals）
    storage_root = os.path.join(str(project_root), '.trae', 'goals')
    registry = GoalRegistry(storage_root=storage_root)

    # 处理 Goal
    goal: Optional[Goal] = None
    if goal_id is not None:
        existing = registry.get_goal(goal_id)
        if existing is not None:
            log(f'♻️  复用已存在目标：{goal_id} (status={existing.status.value})', 'INFO')
            goal = existing
        else:
            # 创建新目标（必须提供 goal_desc）
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

    # 构造 LoopConfig
    loop_config = LoopConfig(
        max_iterations=max(1, loop_count),
        convergence_window=convergence_window,
        stop_on_success=True,
        inter_iteration_delay_seconds=0.0,
    )

    # 构造执行器
    executor = LoopGoalExecutor(registry=registry)

    # 单次 dispatch 函数（被 LoopGoalExecutor 多次调用）
    def _single_dispatch(agent_type=agent_type, task=task, task_id=task_id,
                         project_root=project_root, progress=progress):
        return dispatch_agent_v2(
            agent_type=agent_type,
            task=task,
            task_id=task_id,
            project_root=project_root,
            progress=progress,
            cybernetics_enabled=True,
        )

    log(
        f'🔁 启动 /loop 循环：max_iterations={loop_config.max_iterations}, '
        f'goal_id={goal_id or "无"}',
        'INFO',
    )

    # 执行循环
    result = executor.execute_with_loop_goal(
        task=task,
        agent_type=agent_type,
        dispatch_fn=_single_dispatch,
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
        # 更新进度（与 dispatch_agent 一致）
        final_status = '✅ 已完成' if result.get("success_early") else '⏸ 已停止'
        update_task_status(
            progress, task_id, final_status,
            f'循环执行结束：{result["total_iterations"]} 次', project_root
        )

    return _is_overall_success(result)


def _is_overall_success(result: Dict[str, Any]) -> bool:
    """
    判定整体成功语义（Phase 11 P0-1 + P1-1 修复）

    明确区分"达成 / 收敛 / 跑满 / 失败"四种状态，避免"失败也返回 True"导致 CI 流水线误判。

    判定规则：
    - 无 goal（/loop 仅循环）：跑过 >= 1 次 → True（向后兼容）
    - 有 goal + ACHIEVED：True
    - 有 goal + FAILED：False
    - 有 goal + IN_PROGRESS + converged_early：True（已达稳态）
    - 有 goal + IN_PROGRESS + has_criteria=False 跑满：True（容错，无明确失败标准）
    - 有 goal + IN_PROGRESS + has_criteria=True 跑满：False（未达成，跑满视为失败）

    P1-1 修复：通过 result["has_criteria"] 准确判断"是否设了 criterion"
    原 P0-1 修复兜底逻辑错误：无条件 return True 导致有 criterion 但未满足也返回 True

    Args:
        result: LoopGoalExecutor.execute_with_loop_goal 返回的字典

    Returns:
        bool: True 表示任务成功；False 表示任务失败
    """
    # /loop 无 /goal 模式：跑过 1 次就算成功（兼容旧 dispatch 行为）
    if "status" not in result:
        return result.get("total_iterations", 0) > 0

    status = result["status"]
    # 达成 → 成功
    if status == GoalStatus.ACHIEVED.value:
        return True
    # 明确失败 → 失败
    if status == GoalStatus.FAILED.value:
        return False
    # IN_PROGRESS：区分收敛提前退出 vs 跑满
    if status == GoalStatus.IN_PROGRESS.value:
        if result.get("converged_early"):
            # 收敛 → 已达稳态，视为成功
            return True
        # P1-1 修复：基于 has_criteria 字段判定（避免兜底逻辑错误）
        # has_criteria=True：用户设了 criterion 但跑满未满足 → 视为失败
        # has_criteria=False：未设 criterion → 视为容错成功
        if result.get("has_criteria"):
            return False
        return True
    # ABANDONED / 其它终态 → 视为失败
    return False


# ============================================================================
# Phase 13 新增：续跑 + 多 Goal 编排 dispatch 入口
# ============================================================================

def dispatch_agent_v2_with_goal_resume(
    goal_id: str,
    force: bool = False,
    max_resume_count: int = 3,
    project_root: str = ".",
) -> bool:
    """
    Phase 13.2: 续跑模式 dispatch 入口。

    通过 GoalResumeManager 续跑指定 goal：
    - ACTIVE / IN_PROGRESS：直接返回（不递增计数）
    - FAILED 且未超限：递增 resume_count + 置 IN_PROGRESS
    - FAILED 超限 + force=True：重置 resume_count + 置 IN_PROGRESS
    - FAILED 超限无 force：标记 ABANDONED + 抛错
    - ABANDONED + force=True：重置 + 置 IN_PROGRESS
    - ABANDONED 无 force：抛错

    Args:
        goal_id: 待续跑 Goal ID（kebab-case）
        force: 强制续跑（覆盖 ABANDONED / FAILED 超限）
        max_resume_count: 单 Goal 续跑上限（默认 3）
        project_root: 项目根目录

    Returns:
        bool: True 表示续跑成功（或目标已活跃）；False 表示续跑失败
    """
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


def dispatch_agent_v2_with_multi_goal(
    root_goal_id: str,
    max_concurrent: int = 10,
    reuse_threshold: float = 0.85,
    reuse_enabled: bool = True,
    report_format: Optional[str] = None,
    project_root: str = ".",
) -> bool:
    """
    Phase 13.4: 多 Goal 编排 dispatch 入口。

    通过 GoalOrchestrator 编排以 root_goal_id 为根的 DAG：
    1. 加载 DAG（GoalGraph：拓扑 + 环检测）
    2. 续跑检查（GoalResumeManager）
    3. 跨 Goal 语义复用（GoalIterationReuser）
    4. 并发执行（GoalScheduler + ProcessPoolExecutor）
    5. 生成报告（GoalOrchestratorReport：JSON / MD）

    Args:
        root_goal_id: 根 Goal ID（kebab-case）
        max_concurrent: DAG 并发 worker 数（默认 10）
        reuse_threshold: 跨 Goal 复用相似度阈值（默认 0.85）
        reuse_enabled: 是否启用跨 Goal 复用（默认 True）
        report_format: 报告格式（json / md；None 表示不输出报告）
        project_root: 项目根目录

    Returns:
        bool: True 表示根 Goal 达成；False 表示失败
    """
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

    # 单 Goal dispatch 函数（Pickle 兼容：仅引用本文件中的 dispatch_agent_v2）
    def _single_dispatch(agent_type: str = 'goal_orchestrator',
                         task: str = '',
                         task_id: Optional[str] = None,
                         project_root: str = str(project_root),
                         progress: Optional[Dict] = None) -> bool:
        return dispatch_agent_v2(
            agent_type=agent_type,
            task=task,
            task_id=task_id,
            project_root=project_root,
            progress=progress or {},
            cybernetics_enabled=True,
        )

    loop_config = LoopConfig(
        max_iterations=10,
        convergence_window=3,
    )

    try:
        report = orchestrator.run(
            root_goal_id=root_goal_id,
            dispatch_fn=_single_dispatch,
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


def main():
    """
    主函数
    """
    args = parse_arguments()
    
    log('🚀 Trae Agent 调度脚本 v2.0 启动', 'INFO')
    
    # 检查项目根目录
    project_root = Path(args.project_root).resolve()
    if not project_root.exists():
        log(f'❌ 项目根目录不存在：{project_root}', 'ERROR')
        sys.exit(1)
    
    log(f'📁 项目根目录：{project_root}', 'INFO')
    
    # 检查任务文件
    if args.task_file:
        task_file = project_root / args.task_file
        if not task_file.exists():
            log(f'❌ 任务文件不存在：{task_file}', 'ERROR')
            sys.exit(1)
        log(f'📄 任务文件：{task_file}', 'INFO')
    else:
        task_file = None
        log('📝 使用命令行任务描述', 'INFO')
    
    # 模拟模式
    if args.dry_run:
        log('🔄 模拟模式：不实际调用智能体', 'WARNING')
        log(f'   将调度智能体：{args.agent}', 'WARNING')
        log(f'   任务：{args.task}', 'WARNING')
        log('✅ 模拟完成', 'SUCCESS')
        sys.exit(0)
    
    # 调度智能体（Phase 11：支持 /loop + /goal；Phase 13：多 Goal 编排）
    # Phase 13 优先级 1：续跑模式（--goal-resume）
    if args.goal_resume:
        log(
            f'♻️  Phase 13 检测到续跑模式：goal={args.goal_resume}, '
            f'force={args.goal_resume_force}',
            'INFO',
        )
        success = dispatch_agent_v2_with_goal_resume(
            goal_id=args.goal_resume,
            force=args.goal_resume_force,
            max_resume_count=args.goal_max_resume_count,
            project_root=str(project_root),
        )
    # Phase 13 优先级 2：多 Goal 编排模式（--multi-goal）
    elif args.multi_goal:
        log(
            f'🌐 Phase 13 检测到多 Goal 编排：root={args.multi_goal}, '
            f'max_concurrent={args.max_concurrent}, '
            f'reuse_threshold={args.reuse_threshold}, '
            f'disable_reuse={args.disable_iteration_reuse}',
            'INFO',
        )
        success = dispatch_agent_v2_with_multi_goal(
            root_goal_id=args.multi_goal,
            max_concurrent=args.max_concurrent,
            reuse_threshold=args.reuse_threshold,
            reuse_enabled=not args.disable_iteration_reuse,
            report_format=args.goal_report,
            project_root=str(project_root),
        )
    # Phase 11：支持 /loop + /goal
    elif args.loop > 1 or args.goal is not None:
        log(
            f'🔁 检测到 /loop + /goal 模式：loop={args.loop}, '
            f'goal={args.goal or "无"}, criteria={args.criteria}',
            'INFO',
        )
        success = dispatch_agent_v2_with_loop_goal(
            agent_type=args.agent,
            task=args.task,
            project_root=str(project_root),
            loop_count=args.loop,
            goal_id=args.goal,
            goal_desc=args.goal_desc,
            criteria=args.criteria,
            convergence_window=args.convergence_window,
            task_file=str(task_file) if task_file else None,
        )
    else:
        success = dispatch_agent(
            args.agent,
            args.task,
            str(project_root),
            str(task_file) if task_file else "",
            use_v1=args.use_v1
        )

    if success:
        log('✅ 任务调度成功', 'SUCCESS')
        sys.exit(0)
    else:
        log('❌ 任务调度失败', 'ERROR')
        sys.exit(1)


if __name__ == '__main__':
    main()
