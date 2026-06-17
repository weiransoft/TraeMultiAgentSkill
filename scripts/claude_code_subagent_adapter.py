#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Code SubAgent 调用适配器

用于在 Claude Code 环境中调用 subagent，提供与 Trae IDE 相同的接口

使用方法:
    from claude_code_subagent_adapter import ClaudeCodeSubAgentAdapter
    
    adapter = ClaudeCodeSubAgentAdapter()
    result = adapter.invoke_agent('architect', '设计系统架构')
"""

import os
import sys
import json
import subprocess
from pathlib import Path
from typing import Dict, Optional, Any
from datetime import datetime


class ClaudeCodeSubAgentAdapter:
    """
    Claude Code SubAgent 适配器
    
    提供统一的接口来调用不同平台的 subagent：
    - 在 Claude Code 中使用 claude subagent 命令
    - 在 Trae IDE 中使用原有机制
    """
    
    def __init__(self, skill_root: str = None):
        """
        初始化适配器
        
        Args:
            skill_root: skill 根目录路径
        """
        self.skill_root = Path(skill_root) if skill_root else Path(__file__).parent
        self.platform = self._detect_platform()
        
    def _detect_platform(self) -> str:
        """
        检测运行平台
        
        Returns:
            str: 平台名称 ('claude_code', 'trae', 'unknown')
        """
        # 检测是否在 Claude Code 环境中
        if os.environ.get('CLAUDE_CODE_ENV') or os.environ.get('ANTHROPIC_ENV'):
            return 'claude_code'
        
        # 检测是否在 Trae IDE 环境中
        if os.environ.get('TRAE_ENV') or os.environ.get('TRAE_AGENT_PATH'):
            return 'trae'
        
        # 默认未知
        return 'unknown'
    
    def invoke_agent(self, agent_type: str, task: str, 
                    context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        调用 subagent
        
        Args:
            agent_type: agent 类型 (architect, product-manager, tester, solo-coder, ui-designer)
            task: 任务描述
            context: 上下文信息
            
        Returns:
            Dict: 执行结果
        """
        if self.platform == 'claude_code':
            return self._invoke_via_claude_code(agent_type, task, context)
        elif self.platform == 'trae':
            return self._invoke_via_trae(agent_type, task, context)
        else:
            # 未知平台，尝试使用通用方法
            return self._invoke_generic(agent_type, task, context)
    
    def _invoke_via_claude_code(self, agent_type: str, task: str, 
                               context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        通过 Claude Code 调用 subagent
        
        使用 claude 命令的 subagent 功能
        """
        try:
            # 构建 subagent 提示词
            agent_prompt = self._build_agent_prompt(agent_type, task, context)
            
            # 使用 claude 命令调用 subagent
            # 注意：这里使用 subprocess 调用 claude 命令
            # 在实际 Claude Code 环境中，应该使用内置的 subagent API
            
            # 方案 1: 使用 subprocess 调用 claude 命令（如果有）
            claude_cmd = self._build_claude_command(agent_prompt)
            if claude_cmd:
                result = subprocess.run(
                    claude_cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=300  # 5 分钟超时
                )
                
                return {
                    'success': result.returncode == 0,
                    'output': result.stdout,
                    'error': result.stderr,
                    'platform': 'claude_code_subprocess'
                }
            
            # 方案 2: 使用 Python 模拟 subagent 调用
            # 这是降级方案，当 claude 命令不可用时使用
            return self._simulate_subagent_call(agent_type, task, context)
            
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'error': 'Subagent 调用超时（超过 5 分钟）',
                'platform': 'claude_code'
            }
        except Exception as e:
            return {
                'success': False,
                'error': f'Subagent 调用失败：{str(e)}',
                'platform': 'claude_code'
            }
    
    def _invoke_via_trae(self, agent_type: str, task: str,
                        context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        通过 Trae IDE 调用 subagent
        
        使用原有的 DualLayerContextManager 机制
        """
        try:
            # 导入 Trae 的组件
            sys.path.insert(0, str(self.skill_root))
            from dual_layer_context_manager import DualLayerContextManager
            from role_matcher import RoleMatcher
            
            # 初始化上下文管理器
            context_manager = DualLayerContextManager(str(self.skill_root))
            
            # 创建任务定义
            from dual_layer_context_manager import TaskDefinition
            task_def = TaskDefinition(
                task_id=f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                description=task,
                role=agent_type
            )
            
            # 启动任务
            context_manager.start_task(task_def)
            
            # 这里需要实际调用 Trae 的 agent
            # 但由于这是在 Python 中，我们需要使用其他方式
            # 通常是调用 trae_agent_dispatch_v2.py
            
            dispatch_script = self.skill_root / 'scripts' / 'trae_agent_dispatch_v2.py'
            if dispatch_script.exists():
                result = subprocess.run(
                    [sys.executable, str(dispatch_script), 
                     '--task', task, 
                     '--agent', agent_type],
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                
                return {
                    'success': result.returncode == 0,
                    'output': result.stdout,
                    'error': result.stderr,
                    'platform': 'trae'
                }
            else:
                return {
                    'success': False,
                    'error': f'找不到调度脚本：{dispatch_script}',
                    'platform': 'trae'
                }
                
        except Exception as e:
            return {
                'success': False,
                'error': f'Trae Subagent 调用失败：{str(e)}',
                'platform': 'trae'
            }
    
    def _invoke_generic(self, agent_type: str, task: str,
                       context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        通用 subagent 调用方法
        
        当无法检测平台时使用
        """
        # 尝试使用 Python 脚本调用
        return self._simulate_subagent_call(agent_type, task, context)
    
    def _build_agent_prompt(self, agent_type: str, task: str,
                           context: Optional[Dict] = None) -> str:
        """构建 agent 提示词（v2 修订：按角色注入 Ponytail 决策梯）。

        v2 核心变更：
        - 参数化注入决策梯（非改私有字段，线程安全）
        - 优先使用 context 中的 ponytail_decision_ladder（由 DevHandler/Legacy 注入）
        - 兜底：从 context 中的 _ponytail_engine 按角色生成
        - 决策梯作为 Karpathy Simplicity First 的可执行步骤，不替换 Karpathy 原则

        Args:
            agent_type: agent 类型
            task: 任务
            context: 上下文（可能包含 ponytail_decision_ladder 或 _ponytail_engine）

        Returns:
            str: 完整的提示词
        """
        # 加载角色定义
        role_prompt = self._get_role_prompt(agent_type)

        # 【新增】按角色注入决策梯（参数化，非改私有字段，线程安全）
        # 优先级：context['ponytail_decision_ladder'] > context['_ponytail_engine'] > 空
        ponytail_injection = ""
        if context and isinstance(context, dict):
            if context.get('ponytail_decision_ladder'):
                # context 中已有决策梯（由 DevHandler/Legacy 注入）
                ponytail_injection = context['ponytail_decision_ladder']
            elif context.get('_ponytail_engine') is not None:
                # 兜底：从 engine 按角色生成（线程安全：get_injection_prompt 是纯函数）
                try:
                    engine = context['_ponytail_engine']
                    ponytail_injection = engine.get_injection_prompt(role=agent_type)
                except Exception:
                    # engine 调用失败不阻塞 prompt 构建
                    ponytail_injection = ""

        # 构建完整提示词
        prompt = f"""{role_prompt}

## 任务
{task}

## 要求
1. 遵循 Karpathy 四大核心原则：
   - Think Before Coding: 明确假设，问清楚，不隐藏困惑
   - Simplicity First: 最小代码，无 speculative features
   - Surgical Changes: 只改必要的，不改无关的
   - Goal-Driven: 定义成功标准，验证检查点

2. 输出结构化的结果，包括：
   - 任务理解
   - 方案设计
   - 实现步骤
   - 验证标准

3. 如果有不确定的地方，请先澄清再继续。

{ponytail_injection}
"""
        if context:
            # 【修复】context 中可能包含非 JSON 序列化对象（如 _ponytail_engine），
            # 使用 default=str 兜底，避免 TypeError 中断 prompt 构建
            prompt += (
                f"\n## 上下文\n"
                f"{json.dumps(context, indent=2, ensure_ascii=False, default=str)}\n"
            )

        return prompt
    
    def _get_role_prompt(self, agent_type: str) -> str:
        """
        获取角色提示词
        
        Args:
            agent_type: agent 类型
            
        Returns:
            str: 角色提示词
        """
        role_prompts = {
            'architect': '''你是一位资深架构师，职责是设计系统性、前瞻性、可落地、可验证的架构。

核心原则：
- 系统性思维：设计前回答 4 个关键问题
- 5-Why 分析法：连续追问找到根因
- 零容忍清单：禁止 mock、硬编码、简化
- 验证驱动设计：完整验收标准''',
            
            'product-manager': '''你是一位资深产品经理，职责是定义用户价值清晰、需求明确、可落地、可验收的产品。

核心原则：
- 需求三层挖掘：表面→真实→本质
- SMART 验收标准：具体、可衡量、可实现
- 竞品分析规则：至少 5 个竞品对比''',
            
            'tester': '''你是一位资深测试专家，职责是确保全面、深入、自动化、可量化的质量保障。

核心原则：
- 测试金字塔：70% 单元 +20% 集成 +10%E2E
- 正交分析法：5 类场景全覆盖
- 真机测试规则：真实环境验证''',
            
            'solo-coder': '''你是一位资深开发者，职责是编写完整、高质量、可维护、可测试的代码。

核心原则：
- 零容忍清单：10 项绝对禁止
- 完整性检查：4 维度检查清单
- 自测规则：3 层测试验证''',
            
            'ui-designer': '''你是一位资深 UI 设计师，职责是创建独特、生产级的 UI 界面，具有高设计质量，避免通用的 AI "slop" 美学。

核心原则：
- 设计思维规则：设计前回答 4 个关键问题
- UI 设计美学指南：字体、色彩、动画、布局
- 零容忍清单：禁止通用字体、陈旧配色、AI slop'''
        }
        
        return role_prompts.get(agent_type, f'你是一位{agent_type}专家，请完成以下任务。')
    
    def _build_claude_command(self, prompt: str) -> Optional[str]:
        """
        构建 claude 命令
        
        Args:
            prompt: 提示词
            
        Returns:
            Optional[str]: 命令字符串，如果不可用返回 None
        """
        # 检查 claude 命令是否可用
        try:
            result = subprocess.run(
                ['which', 'claude'],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                # claude 命令可用，构建命令
                # 使用 echo 传递提示词给 claude
                escaped_prompt = prompt.replace('"', '\\"').replace('\n', '\\n')
                return f'echo "{escaped_prompt}" | claude'
        except Exception:
            pass
        
        return None
    
    def _simulate_subagent_call(self, agent_type: str, task: str,
                               context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        模拟 subagent 调用
        
        这是降级方案，当无法调用真实 subagent 时使用
        
        Args:
            agent_type: agent 类型
            task: 任务
            context: 上下文
            
        Returns:
            Dict: 模拟结果
        """
        # 构建提示词
        prompt = self._build_agent_prompt(agent_type, task, context)
        
        # 输出到文件，供用户手动处理
        output_file = self.skill_root / 'logs' / f'subagent_call_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt'
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(f"# Subagent Call Simulation\n")
            f.write(f"# Platform: {self.platform}\n")
            f.write(f"# Agent Type: {agent_type}\n")
            f.write(f"# Timestamp: {datetime.now().isoformat()}\n\n")
            f.write(prompt)
        
        return {
            'success': True,
            'output': f'Subagent 调用已保存到：{output_file}\n\n由于当前环境不支持直接调用 subagent，请手动处理该文件。',
            'prompt_file': str(output_file),
            'platform': 'simulation'
        }


def invoke_subagent(agent_type: str, task: str, context: Optional[Dict] = None) -> Dict[str, Any]:
    """
    便捷函数：调用 subagent
    
    Args:
        agent_type: agent 类型
        task: 任务
        context: 上下文
        
    Returns:
        Dict: 执行结果
    """
    adapter = ClaudeCodeSubAgentAdapter()
    return adapter.invoke_agent(agent_type, task, context)


if __name__ == '__main__':
    # 测试
    if len(sys.argv) > 2:
        agent_type = sys.argv[1]
        task = ' '.join(sys.argv[2:])
        
        print(f"调用 subagent: {agent_type}")
        print(f"任务：{task}")
        
        result = invoke_subagent(agent_type, task)
        
        print(f"\n结果：{json.dumps(result, indent=2, ensure_ascii=False)}")
    else:
        print("使用方法：python claude_code_subagent_adapter.py <agent_type> <task>")
        print("示例：python claude_code_subagent_adapter.py architect '设计系统架构'")
