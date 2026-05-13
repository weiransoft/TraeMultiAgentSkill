#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
层次化控制器模块

基于工程控制论的三层控制架构，结合 AI 大模型进行动态规划：
- 战略层：任务规划、角色配置、全局策略
- 战术层：Guard 验证、异常检测、补偿计算
- 执行层：任务执行、反馈收集、结果评估

参考：cybernetics-agent 工程控制论 + Profile-Aware Maneuvering 架构
"""

import json
import time
import threading
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, asdict, field
from datetime import datetime
from enum import Enum


class ControlLevel(Enum):
    """
    控制层级枚举
    """
    STRATEGIC = "strategic"  # 战略层
    TACTICAL = "tactical"    # 战术层
    EXECUTION = "execution"   # 执行层


@dataclass
class StrategicPlan:
    """
    战略规划数据类
    
    战略层输出的执行计划
    """
    plan_id: str
    task_type: str  # 任务类型
    recommended_roles: List[str]  # 推荐的执行角色
    role_config: Dict[str, Any]  # 角色配置
    execution_strategy: str  # 执行策略
    estimated_time: float  # 预估时间
    risk_assessment: Dict[str, Any]  # 风险评估
    ai_enhanced: bool = False  # 是否经过 AI 增强
    ai_recommendations: List[str] = field(default_factory=list)  # AI 推荐
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class TacticalDecision:
    """
    战术决策数据类
    
    战术层输出的决策结果
    """
    decision_id: str
    context: Dict[str, Any]  # 决策上下文
    selected_strategy: str  # 选择的策略
    compensations: List[str]  # 补偿措施
    guard_validations: List[Dict[str, Any]]  # Guard 验证结果
    fallback_strategies: List[str]  # 备用策略
    ai_enhanced: bool = False  # 是否经过 AI 增强
    ai_reasoning: str = ""  # AI 推理过程
    confidence: float = 0.0  # 置信度
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ExecutionMetrics:
    """
    执行指标数据类
    
    执行层的执行结果指标
    """
    execution_id: str
    start_time: float
    end_time: Optional[float] = None
    duration: float = 0.0
    success: bool = False
    error_type: Optional[str] = None
    strategy_used: str = ""
    compensations_applied: List[str] = field(default_factory=list)
    retry_count: int = 0
    fallback_triggered: bool = False
    metrics: Dict[str, Any] = field(default_factory=dict)


class StrategicController:
    """
    战略控制器
    
    负责：
    1. 任务分析和规划
    2. 角色池配置
    3. 全局策略制定
    4. AI 增强的动态规划
    """
    
    def __init__(self, ai_provider: Optional[Any] = None):
        """
        初始化战略控制器
        
        Args:
            ai_provider: AI 提供者
        """
        self.ai_provider = ai_provider
        
        # 角色能力库
        self.role_capabilities: Dict[str, Dict[str, Any]] = {}
        self._init_role_capabilities()
        
        # 历史规划
        self.planning_history: List[StrategicPlan] = []
        self._lock = threading.Lock()
    
    def _init_role_capabilities(self):
        """初始化角色能力库"""
        self.role_capabilities = {
            "architect": {
                "name": "架构师",
                "capabilities": ["架构设计", "代码审查", "性能优化"],
                "suitable_tasks": ["architecture", "design", "review"],
                "complexity_range": [5, 10]
            },
            "product_manager": {
                "name": "产品经理",
                "capabilities": ["需求分析", "PRD编写", "优先级排序"],
                "suitable_tasks": ["requirement", "planning", "prioritization"],
                "complexity_range": [3, 8]
            },
            "solo_coder": {
                "name": "独立开发者",
                "capabilities": ["代码实现", "单元测试", "bug修复"],
                "suitable_tasks": ["implementation", "coding", "testing"],
                "complexity_range": [1, 7]
            },
            "ui_designer": {
                "name": "UI设计师",
                "capabilities": ["界面设计", "交互设计", "视觉优化"],
                "suitable_tasks": ["ui", "design", "interface"],
                "complexity_range": [2, 6]
            },
            "test_expert": {
                "name": "测试专家",
                "capabilities": ["测试用例设计", "自动化测试", "质量评估"],
                "suitable_tasks": ["testing", "qa", "validation"],
                "complexity_range": [2, 7]
            }
        }
    
    def set_ai_provider(self, ai_provider: Any):
        """设置 AI 提供者"""
        self.ai_provider = ai_provider
    
    def plan(self, task: Dict[str, Any]) -> StrategicPlan:
        """
        战略规划
        
        制定任务执行的战略计划
        
        Args:
            task: 任务字典
            
        Returns:
            StrategicPlan: 战略规划结果
        """
        task_type = task.get('type', 'unknown')
        complexity = task.get('complexity', 5)
        description = task.get('description', '')
        
        # 1. 基础规划
        recommended_roles = self._match_roles(task_type, complexity)
        role_config = self._configure_roles(recommended_roles, task)
        execution_strategy = self._select_strategy(complexity, recommended_roles)
        estimated_time = self._estimate_time(complexity, task_type)
        risk_assessment = self._assess_risks(task_type, complexity)
        
        # 创建规划
        plan = StrategicPlan(
            plan_id=f"plan_{int(time.time() * 1000)}",
            task_type=task_type,
            recommended_roles=recommended_roles,
            role_config=role_config,
            execution_strategy=execution_strategy,
            estimated_time=estimated_time,
            risk_assessment=risk_assessment
        )
        
        # 2. AI 增强规划
        if self.ai_provider:
            try:
                ai_plan = self._ai_enhanced_planning(task, plan)
                plan.ai_enhanced = True
                plan.ai_recommendations = ai_plan.get('recommendations', [])
                
                # 融合 AI 建议
                if ai_plan.get('strategy'):
                    plan.execution_strategy = ai_plan['strategy']
                if ai_plan.get('roles'):
                    plan.recommended_roles = ai_plan['roles']
                if ai_plan.get('risk_mitigation'):
                    plan.risk_assessment['mitigation'] = ai_plan['risk_mitigation']
            except Exception:
                pass
        
        # 保存历史
        with self._lock:
            self.planning_history.append(plan)
        
        return plan
    
    def _match_roles(self, task_type: str, complexity: int) -> List[str]:
        """
        匹配适合执行任务的角色
        
        Args:
            task_type: 任务类型
            complexity: 复杂度
            
        Returns:
            List[str]: 角色列表
        """
        matched_roles = []
        
        for role_id, capabilities in self.role_capabilities.items():
            suitable_tasks = capabilities['suitable_tasks']
            complexity_range = capabilities['complexity_range']
            
            # 检查任务类型匹配
            type_match = any(task_type.lower() in t.lower() or t.lower() in task_type.lower() 
                          for t in suitable_tasks)
            
            # 检查复杂度范围
            complexity_match = complexity_range[0] <= complexity <= complexity_range[1]
            
            if type_match or complexity_match:
                matched_roles.append(role_id)
        
        # 默认至少有一个角色
        if not matched_roles:
            matched_roles = ["solo_coder"]
        
        return matched_roles
    
    def _configure_roles(self, roles: List[str], task: Dict[str, Any]) -> Dict[str, Any]:
        """
        配置角色
        
        Args:
            roles: 角色列表
            task: 任务字典
            
        Returns:
            Dict[str, Any]: 角色配置
        """
        complexity = task.get('complexity', 5)
        
        config = {}
        for role in roles:
            role_info = self.role_capabilities.get(role, {})
            
            # 根据复杂度调整配置
            if complexity > 7:
                # 高复杂度：增强资源配置
                config[role] = {
                    "enabled": True,
                    "priority": 1,
                    "timeout": 600,  # 10分钟
                    "retry": True,
                    "max_retry": 3
                }
            elif complexity > 4:
                # 中复杂度：标准配置
                config[role] = {
                    "enabled": True,
                    "priority": 2,
                    "timeout": 300,  # 5分钟
                    "retry": True,
                    "max_retry": 2
                }
            else:
                # 低复杂度：简化配置
                config[role] = {
                    "enabled": True,
                    "priority": 3,
                    "timeout": 180,  # 3分钟
                    "retry": False,
                    "max_retry": 1
                }
        
        return config
    
    def _select_strategy(self, complexity: int, roles: List[str]) -> str:
        """
        选择执行策略
        
        Args:
            complexity: 复杂度
            roles: 角色列表
            
        Returns:
            str: 策略名称
        """
        if complexity > 7:
            return "conservative"
        elif complexity > 4:
            return "balanced"
        else:
            return "aggressive"
    
    def _estimate_time(self, complexity: int, task_type: str) -> float:
        """
        预估执行时间
        
        Args:
            complexity: 复杂度
            task_type: 任务类型
            
        Returns:
            float: 预估时间（秒）
        """
        base_time = complexity * 30  # 每复杂度 30 秒基准
        
        # 任务类型调整
        type_multipliers = {
            "architecture": 2.0,
            "design": 1.8,
            "implementation": 1.0,
            "testing": 1.2,
            "review": 0.8,
            "planning": 1.5
        }
        
        multiplier = 1.0
        for t, m in type_multipliers.items():
            if t in task_type.lower():
                multiplier = m
                break
        
        return base_time * multiplier
    
    def _assess_risks(self, task_type: str, complexity: int) -> Dict[str, Any]:
        """
        评估风险
        
        Args:
            task_type: 任务类型
            complexity: 复杂度
            
        Returns:
            Dict[str, Any]: 风险评估结果
        """
        risks = []
        risk_level = "low"
        
        # 复杂度风险
        if complexity > 8:
            risks.append("高复杂度可能导致执行失败")
            risk_level = "high"
        elif complexity > 6:
            risks.append("中等复杂度存在一定风险")
            risk_level = "medium"
        
        # 类型风险
        high_risk_types = ["architecture", "performance", "security"]
        if any(t in task_type.lower() for t in high_risk_types):
            risks.append("高风险任务类型需要额外验证")
            if risk_level != "high":
                risk_level = "medium"
        
        return {
            "level": risk_level,
            "risks": risks,
            "mitigation": "启用保守策略和额外监控"
        }
    
    def _ai_enhanced_planning(self, task: Dict[str, Any], 
                             base_plan: StrategicPlan) -> Dict[str, Any]:
        """
        AI 增强的规划
        
        Args:
            task: 任务字典
            base_plan: 基础规划
            
        Returns:
            Dict[str, Any]: AI 增强建议
        """
        if not self.ai_provider:
            return {}
        
        try:
            prompt = f"""作为战略规划专家，分析以下任务并提供优化建议：

任务信息：
- 类型: {task.get('type', 'unknown')}
- 复杂度: {task.get('complexity', 5)}/10
- 描述: {task.get('description', '无')}
- 当前推荐角色: {base_plan.recommended_roles}
- 当前策略: {base_plan.execution_strategy}
- 当前风险评估: {json.dumps(base_plan.risk_assessment, ensure_ascii=False)}

请提供优化建议，返回JSON格式：
{{
    "strategy": "优化后的策略",
    "roles": ["可能的角色调整"],
    "recommendations": ["建议1", "建议2", "建议3"],
    "risk_mitigation": "风险缓解建议"
}}"""
            
            response = self.ai_provider.generate(prompt)
            
            import re
            json_match = re.search(r'\{{.*\}}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
                
        except Exception:
            pass
        
        return {}
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._lock:
            return {
                'total_plans': len(self.planning_history),
                'ai_enhanced_count': sum(1 for p in self.planning_history if p.ai_enhanced),
                'role_usage': self._get_role_usage(),
                'strategy_usage': self._get_strategy_usage()
            }
    
    def _get_role_usage(self) -> Dict[str, int]:
        """获取角色使用统计"""
        usage = {}
        for plan in self.planning_history:
            for role in plan.recommended_roles:
                usage[role] = usage.get(role, 0) + 1
        return usage
    
    def _get_strategy_usage(self) -> Dict[str, int]:
        """获取策略使用统计"""
        usage = {}
        for plan in self.planning_history:
            usage[plan.execution_strategy] = usage.get(plan.execution_strategy, 0) + 1
        return usage


class TacticalController:
    """
    战术控制器
    
    负责：
    1. Guard 验证协调
    2. 异常模式检测
    3. 补偿策略计算
    4. AI 增强的动态决策
    """
    
    def __init__(self, ai_provider: Optional[Any] = None):
        """
        初始化战术控制器
        
        Args:
            ai_provider: AI 提供者
        """
        self.ai_provider = ai_provider
        
        # Guard 协调器（可选，由外部注入）
        self.guard_coordinator = None
        
        # 战术决策历史
        self.decision_history: List[TacticalDecision] = []
        self._lock = threading.Lock()
    
    def set_ai_provider(self, ai_provider: Any):
        """设置 AI 提供者"""
        self.ai_provider = ai_provider
    
    def set_guard_coordinator(self, guard_coordinator: Any):
        """设置守护协调器"""
        self.guard_coordinator = guard_coordinator
    
    def decide(self, context: Dict[str, Any]) -> TacticalDecision:
        """
        战术决策
        
        根据上下文做出战术决策
        
        Args:
            context: 决策上下文，包含任务信息、Guard验证结果等
            
        Returns:
            TacticalDecision: 战术决策结果
        """
        task = context.get('task', {})
        guard_results = context.get('guard_results', [])
        
        # 1. 聚合 Guard 验证结果
        guard_validations = self._aggregate_guard_results(guard_results)
        
        # 2. 选择策略
        selected_strategy = self._select_strategy(task, guard_validations)
        
        # 3. 计算补偿措施
        compensations = self._compute_compensations(task, guard_validations)
        
        # 4. 确定备用策略
        fallback_strategies = self._get_fallback_strategies(task, guard_validations)
        
        # 创建决策
        decision = TacticalDecision(
            decision_id=f"tac_{int(time.time() * 1000)}",
            context=context,
            selected_strategy=selected_strategy,
            compensations=compensations,
            guard_validations=guard_validations,
            fallback_strategies=fallback_strategies
        )
        
        # 5. AI 增强决策
        if self.ai_provider:
            try:
                ai_decision = self._ai_enhanced_decision(task, decision)
                decision.ai_enhanced = True
                decision.ai_reasoning = ai_decision.get('reasoning', '')
                decision.confidence = ai_decision.get('confidence', 0.0)
                
                # 融合 AI 建议
                if ai_decision.get('strategy'):
                    decision.selected_strategy = ai_decision['strategy']
                if ai_decision.get('compensations'):
                    decision.compensations = ai_decision['compensations']
            except Exception:
                pass
        
        # 保存历史
        with self._lock:
            self.decision_history.append(decision)
        
        return decision
    
    def _aggregate_guard_results(self, guard_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        聚合 Guard 验证结果
        
        Args:
            guard_results: Guard 验证结果列表
            
        Returns:
            List[Dict[str, Any]]: 聚合后的结果
        """
        if not guard_results:
            return [{"source": "default", "passed": True, "warnings": []}]
        
        aggregated = []
        for result in guard_results:
            if isinstance(result, dict):
                aggregated.append(result)
            elif hasattr(result, '__dict__'):
                aggregated.append(asdict(result))
        
        return aggregated
    
    def _select_strategy(self, task: Dict[str, Any], 
                        guard_validations: List[Dict[str, Any]]) -> str:
        """
        选择策略
        
        Args:
            task: 任务字典
            guard_validations: Guard 验证结果
            
        Returns:
            str: 策略名称
        """
        complexity = task.get('complexity', 5)
        
        # 检查 Guard 验证是否通过
        validation_passed = all(
            v.get('passed', True) for v in guard_validations
        )
        
        if not validation_passed:
            return "conservative"
        
        if complexity > 7:
            return "conservative"
        elif complexity > 4:
            return "balanced"
        else:
            return "aggressive"
    
    def _compute_compensations(self, task: Dict[str, Any],
                              guard_validations: List[Dict[str, Any]]) -> List[str]:
        """
        计算补偿措施
        
        Args:
            task: 任务字典
            guard_validations: Guard 验证结果
            
        Returns:
            List[str]: 补偿措施列表
        """
        compensations = []
        
        # 从 Guard 结果提取补偿建议
        for validation in guard_validations:
            if isinstance(validation, dict):
                recs = validation.get('recommended_compensations', [])
                if isinstance(recs, list):
                    compensations.extend(recs)
        
        # 基于任务特征添加补偿
        complexity = task.get('complexity', 5)
        if complexity > 7:
            compensations.append("启用超时保护")
            compensations.append("启用断点保存")
        
        # 去重
        seen = set()
        unique_compensations = []
        for c in compensations:
            if c not in seen:
                seen.add(c)
                unique_compensations.append(c)
        
        return unique_compensations
    
    def _get_fallback_strategies(self, task: Dict[str, Any],
                                guard_validations: List[Dict[str, Any]]) -> List[str]:
        """
        获取备用策略
        
        Args:
            task: 任务字典
            guard_validations: Guard 验证结果
            
        Returns:
            List[str]: 备用策略列表
        """
        strategies = ["conservative", "balanced", "aggressive"]
        selected = self._select_strategy(task, guard_validations)
        
        # 当前策略放到第一位
        if selected in strategies:
            strategies.remove(selected)
            strategies.insert(0, selected)
        
        return strategies
    
    def _ai_enhanced_decision(self, task: Dict[str, Any],
                              base_decision: TacticalDecision) -> Dict[str, Any]:
        """
        AI 增强的决策
        
        Args:
            task: 任务字典
            base_decision: 基础决策
            
        Returns:
            Dict[str, Any]: AI 决策建议
        """
        if not self.ai_provider:
            return {}
        
        try:
            prompt = f"""作为战术决策专家，分析以下任务上下文并做出最优决策：

任务信息：
- 类型: {task.get('type', 'unknown')}
- 复杂度: {task.get('complexity', 5)}/10
- 描述: {task.get('description', '无')}

当前决策：
- 选择的策略: {base_decision.selected_strategy}
- 补偿措施: {base_decision.compensations}
- 备用策略: {base_decision.fallback_strategies}

请分析并返回JSON格式：
{{
    "strategy": "最优策略",
    "compensations": ["补偿措施1", "补偿措施2"],
    "confidence": 0.0-1.0,
    "reasoning": "决策推理过程"
}}"""
            
            response = self.ai_provider.generate(prompt)
            
            import re
            json_match = re.search(r'\{{.*\}}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
                
        except Exception:
            pass
        
        return {}
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._lock:
            recent_decisions = self.decision_history[-100:]
            
            return {
                'total_decisions': len(self.decision_history),
                'ai_enhanced_count': sum(1 for d in recent_decisions if d.ai_enhanced),
                'average_confidence': sum(d.confidence for d in recent_decisions) / len(recent_decisions) if recent_decisions else 0.0,
                'strategy_distribution': self._get_strategy_distribution(recent_decisions)
            }
    
    def _get_strategy_distribution(self, decisions: List[TacticalDecision]) -> Dict[str, int]:
        """获取策略分布"""
        dist = {}
        for d in decisions:
            dist[d.selected_strategy] = dist.get(d.selected_strategy, 0) + 1
        return dist


class ExecutionController:
    """
    执行控制器
    
    负责：
    1. 任务执行
    2. 反馈收集
    3. 结果评估
    4. 与反馈控制环集成
    """
    
    def __init__(self, ai_provider: Optional[Any] = None):
        """
        初始化执行控制器
        
        Args:
            ai_provider: AI 提供者
        """
        self.ai_provider = ai_provider
        
        # 反馈控制环（可选，由外部注入）
        self.feedback_loop = None
        
        # 执行指标历史
        self.metrics_history: List[ExecutionMetrics] = []
        self._lock = threading.Lock()
    
    def set_ai_provider(self, ai_provider: Any):
        """设置 AI 提供者"""
        self.ai_provider = ai_provider
    
    def set_feedback_loop(self, feedback_loop: Any):
        """设置反馈控制环"""
        self.feedback_loop = feedback_loop
    
    def execute(self, task: Dict[str, Any], 
                strategy: str, 
                compensations: List[str],
                executor: Optional[Callable] = None) -> ExecutionMetrics:
        """
        执行任务
        
        Args:
            task: 任务字典
            strategy: 执行策略
            compensations: 补偿措施
            executor: 可选的执行器
            
        Returns:
            ExecutionMetrics: 执行指标
        """
        execution_id = f"exec_{int(time.time() * 1000)}"
        start_time = time.time()
        
        # 创建执行指标
        metrics = ExecutionMetrics(
            execution_id=execution_id,
            start_time=start_time,
            strategy_used=strategy,
            compensations_applied=compensations
        )
        
        try:
            # 1. 准备执行
            if self.ai_provider:
                try:
                    execution_context = self._ai_prepare_execution(task, strategy, compensations)
                    metrics.metrics['ai_preparation'] = execution_context
                except Exception:
                    pass
            
            # 2. 执行任务
            if executor:
                result = executor(task)
            else:
                result = self._default_execution(task, strategy)
            
            # 3. 更新指标
            metrics.success = result.get('success', False)
            metrics.end_time = time.time()
            metrics.duration = metrics.end_time - metrics.start_time
            
            if not metrics.success:
                metrics.error_type = result.get('error_type', 'unknown')
            
            # 4. AI 增强的结果评估
            if self.ai_provider:
                try:
                    evaluation = self._ai_evaluate_result(result, metrics)
                    metrics.metrics['ai_evaluation'] = evaluation
                except Exception:
                    pass
            
        except Exception as e:
            metrics.success = False
            metrics.error_type = type(e).__name__
            metrics.end_time = time.time()
            metrics.duration = metrics.end_time - metrics.start_time
        
        # 5. 反馈收集
        if self.feedback_loop:
            try:
                self.feedback_loop.record(
                    task_type=task.get('type', 'unknown'),
                    task_complexity=task.get('complexity', 5),
                    success=metrics.success,
                    error_type=metrics.error_type,
                    execution_time=metrics.duration,
                    strategy=strategy
                )
            except Exception:
                pass
        
        # 保存历史
        with self._lock:
            self.metrics_history.append(metrics)
        
        return metrics
    
    def _ai_prepare_execution(self, task: Dict[str, Any],
                             strategy: str,
                             compensations: List[str]) -> Dict[str, Any]:
        """
        AI 准备执行
        
        Args:
            task: 任务字典
            strategy: 执行策略
            compensations: 补偿措施
            
        Returns:
            Dict[str, Any]: 准备结果
        """
        if not self.ai_provider:
            return {}
        
        try:
            prompt = f"""分析以下任务执行上下文，提供执行建议：

任务：
{json.dumps(task, ensure_ascii=False, indent=2)}

策略: {strategy}
补偿措施: {compensations}

返回JSON格式：
{{
    "suggestions": ["建议1", "建议2"],
    "optimizations": ["优化1", "优化2"],
    "warnings": ["警告1"]
}}"""
            
            response = self.ai_provider.generate(prompt)
            
            import re
            json_match = re.search(r'\{{.*\}}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
                
        except Exception:
            pass
        
        return {}
    
    def _default_execution(self, task: Dict[str, Any], strategy: str) -> Dict[str, Any]:
        """
        默认执行逻辑
        
        Args:
            task: 任务字典
            strategy: 执行策略
            
        Returns:
            Dict[str, Any]: 执行结果
        """
        return {
            'success': True,
            'task_id': task.get('id'),
            'strategy': strategy,
            'message': f"使用{strategy}策略执行任务"
        }
    
    def _ai_evaluate_result(self, result: Dict[str, Any],
                          metrics: ExecutionMetrics) -> Dict[str, Any]:
        """
        AI 评估执行结果
        
        Args:
            result: 执行结果
            metrics: 执行指标
            
        Returns:
            Dict[str, Any]: 评估结果
        """
        if not self.ai_provider:
            return {}
        
        try:
            prompt = f"""评估以下任务执行结果：

结果：
{json.dumps(result, ensure_ascii=False, indent=2)}

指标：
- 执行时间: {metrics.duration}秒
- 成功: {metrics.success}
- 错误类型: {metrics.error_type}

返回JSON格式：
{{
    "assessment": "评估结果",
    "lessons": ["经验1", "经验2"],
    "improvements": ["改进1", "改进2"]
}}"""
            
            response = self.ai_provider.generate(prompt)
            
            import re
            json_match = re.search(r'\{{.*\}}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
                
        except Exception:
            pass
        
        return {}
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._lock:
            return {
                'total_executions': len(self.metrics_history),
                'success_count': sum(1 for m in self.metrics_history if m.success),
                'failure_count': sum(1 for m in self.metrics_history if not m.success),
                'average_duration': sum(m.duration for m in self.metrics_history) / len(self.metrics_history) if self.metrics_history else 0.0,
                'retry_count': sum(m.retry_count for m in self.metrics_history),
                'fallback_triggered': sum(1 for m in self.metrics_history if m.fallback_triggered)
            }


class HierarchicalControlManager:
    """
    层次化控制管理器
    
    整合战略层、战术层、执行层三层控制，
    实现完整的层次化控制流程
    """
    
    def __init__(self, ai_provider: Optional[Any] = None):
        """
        初始化层次化控制管理器
        
        Args:
            ai_provider: AI 提供者
        """
        self.ai_provider = ai_provider
        
        # 初始化三层控制器
        self.strategic_controller = StrategicController(ai_provider)
        self.tactical_controller = TacticalController(ai_provider)
        self.execution_controller = ExecutionController(ai_provider)
        
        # 反馈控制环（可选）
        self.feedback_loop = None
        
        # 守护协调器（可选）
        self.guard_coordinator = None
        
        # 控制历史
        self.control_history: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
    
    def set_feedback_loop(self, feedback_loop: Any):
        """设置反馈控制环"""
        self.feedback_loop = feedback_loop
        self.execution_controller.set_feedback_loop(feedback_loop)
    
    def set_guard_coordinator(self, guard_coordinator: Any):
        """设置守护协调器"""
        self.guard_coordinator = guard_coordinator
        self.tactical_controller.set_guard_coordinator(guard_coordinator)
    
    def execute_task(self, task: Dict[str, Any],
                    executor: Optional[Callable] = None) -> Dict[str, Any]:
        """
        执行任务（层次化控制）
        
        完整流程：
        1. 战略层：任务规划、角色配置
        2. 战术层：Guard 验证、策略决策
        3. 执行层：任务执行、反馈收集
        
        Args:
            task: 任务字典
            executor: 可选的执行器
            
        Returns:
            Dict[str, Any]: 执行结果
        """
        control_record = {
            'task_id': task.get('id'),
            'start_time': datetime.now().isoformat(),
            'levels': {}
        }
        
        try:
            # 阶段1: 战略控制
            strategic_plan = self.strategic_controller.plan(task)
            control_record['levels']['strategic'] = asdict(strategic_plan)
            
            # 阶段2: 战术控制
            guard_results = []
            if self.guard_coordinator:
                validation = self.guard_coordinator.pre_execute_validation(task)
                guard_results.append(asdict(validation))
            
            tactical_context = {
                'task': task,
                'strategic_plan': asdict(strategic_plan),
                'guard_results': guard_results
            }
            tactical_decision = self.tactical_controller.decide(tactical_context)
            control_record['levels']['tactical'] = asdict(tactical_decision)
            
            # 阶段3: 执行控制
            execution_metrics = self.execution_controller.execute(
                task=task,
                strategy=tactical_decision.selected_strategy,
                compensations=tactical_decision.compensations,
                executor=executor
            )
            control_record['levels']['execution'] = asdict(execution_metrics)
            control_record['success'] = execution_metrics.success
            control_record['end_time'] = datetime.now().isoformat()
            
            # 执行后审查
            if self.guard_coordinator:
                review = self.guard_coordinator.post_execute_review(
                    execution_metrics.execution_id,
                    asdict(execution_metrics)
                )
                control_record['review'] = asdict(review)
            
            # 记录控制历史
            with self._lock:
                self.control_history.append(control_record)
            
            return {
                'success': execution_metrics.success,
                'strategic_plan': asdict(strategic_plan),
                'tactical_decision': asdict(tactical_decision),
                'execution_metrics': asdict(execution_metrics),
                'control_record': control_record
            }
            
        except Exception as e:
            control_record['error'] = str(e)
            control_record['success'] = False
            control_record['end_time'] = datetime.now().isoformat()
            
            with self._lock:
                self.control_history.append(control_record)
            
            return {
                'success': False,
                'error': str(e),
                'control_record': control_record
            }
    
    def get_all_statistics(self) -> Dict[str, Any]:
        """
        获取所有层的统计信息
        
        Returns:
            Dict[str, Any]: 各层统计信息
        """
        return {
            'strategic': self.strategic_controller.get_statistics(),
            'tactical': self.tactical_controller.get_statistics(),
            'execution': self.execution_controller.get_statistics(),
            'hierarchical': {
                'total_control_records': len(self.control_history),
                'success_rate': sum(1 for r in self.control_history if r.get('success')) / len(self.control_history) if self.control_history else 0.0
            }
        }


# 导出主要类
__all__ = [
    'HierarchicalControlManager',
    'StrategicController',
    'TacticalController',
    'ExecutionController',
    'StrategicPlan',
    'TacticalDecision',
    'ExecutionMetrics',
    'ControlLevel'
]
