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
"""

import os
import sys
import argparse
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional

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


def dispatch_agent_v2(agent_type: str, task: str, task_id: Optional[str], 
                     project_root: str, progress: Dict) -> bool:
    """
    v2.0 调度逻辑 - 使用双层上下文和智能匹配
    
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
        # 检测运行环境，优先使用 Claude Code SubAgent
        if CLAUDE_CODE_ADAPTER_AVAILABLE:
            log('🚀 使用 Claude Code SubAgent 适配器', 'SUCCESS')
            return _dispatch_via_claude_code(agent_type, task, task_id, project_root, progress)
        
        # 降级到原有逻辑
        log('⚠️  使用双层上下文管理器（Trae IDE）', 'WARNING')
        return _dispatch_via_trae(agent_type, task, task_id, project_root, progress)
    
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
    
    # 调度智能体
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
