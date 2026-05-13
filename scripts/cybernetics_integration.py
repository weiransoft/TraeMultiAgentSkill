#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cybernetics 增强集成层

将工程控制论增强组件（反馈控制环、性能画像、守护协调器、层次化控制器）
与现有 trae-multi-agent 系统深度集成

集成策略：
1. FeedbackControlLoop 作为 AgentLoopControllerV2 的增强装饰器
2. PerformanceFingerprint 与 DualLayerContextManager 深度集成
3. GuardCoordinator 与 WorkflowEngineV2 集成
4. HierarchicalControlManager 提供统一的增强接口

参考：CYBERNETICS_INTEGRATION_PLAN.md v2.0
"""

import json
import time
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable
from datetime import datetime
from dataclasses import dataclass, asdict, field

# 导入现有组件
try:
    from agent_loop_controller_v2 import AgentLoopControllerV2
    from dual_layer_context_manager import DualLayerContextManager
    from workflow_engine_v2 import WorkflowEngineV2
except ImportError as e:
    print(f"⚠️  导入现有组件失败：{e}")

# 导入新组件
try:
    from feedback_control_loop import FeedbackControlLoop
    from performance_fingerprint import PerformanceFingerprint
    from guard_coordinator import GuardCoordinator
    from hierarchical_control import HierarchicalControlManager
except ImportError as e:
    print(f"⚠️  导入增强组件失败：{e}")


@dataclass
class CyberneticsConfig:
    """
    Cybernetics 增强配置
    
    控制各增强功能的启用状态和参数
    """
    # 反馈控制环配置
    feedback_loop_enabled: bool = True
    feedback_storage_path: str = "./feedback_data"
    
    # 性能画像配置
    fingerprint_enabled: bool = True
    fingerprint_storage_path: str = "./fingerprints"
    min_samples_for_prediction: int = 10
    
    # 守护协调器配置
    guard_enabled: bool = True
    guard_pre_validation: bool = True
    guard_real_time_monitoring: bool = True
    guard_post_review: bool = True
    
    # 层次化控制器配置
    hierarchical_enabled: bool = False  # 默认关闭，需要显式启用
    ai_provider_enabled: bool = False
    
    # 适配控制器配置
    adaptive_enabled: bool = True
    adaptation_threshold: float = 0.3  # 触发适应的错误率阈值


@dataclass
class IntegrationMetrics:
    """
    集成指标
    
    记录增强组件的集成状态和性能指标
    """
    agent_id: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # 反馈控制环指标
    feedback_loop_calls: int = 0
    feedback_loop_success: int = 0
    feedback_loop_failure: int = 0
    
    # 性能画像指标
    fingerprint_records: int = 0
    fingerprint_retrievals: int = 0
    fingerprint_hit_rate: float = 0.0
    
    # 守护协调器指标
    guard_validations: int = 0
    guard_warnings: int = 0
    guard_blocks: int = 0
    
    # 层次化控制器指标
    strategic_plans: int = 0
    tactical_decisions: int = 0
    ai_enhancements: int = 0
    
    @property
    def feedback_success_rate(self) -> float:
        """计算反馈成功率"""
        if self.feedback_loop_calls == 0:
            return 0.0
        return self.feedback_loop_success / self.feedback_loop_calls
    
    @property
    def guard_pass_rate(self) -> float:
        """计算守护通过率"""
        if self.guard_validations == 0:
            return 1.0
        return (self.guard_validations - self.guard_blocks) / self.guard_validations


class CyberneticsIntegration:
    """
    Cybernetics 增强集成类
    
    核心功能：
    1. 统一管理所有增强组件
    2. 提供与现有系统的深度集成
    3. 收集和聚合各组件的指标
    4. 支持配置化的功能开关
    
    使用方式：
    ```python
    # 基础集成
    integration = CyberneticsIntegration()
    
    # 任务执行前验证
    validation = integration.pre_execute_validation(task)
    
    # 带反馈的任务执行
    result = integration.execute_with_feedback(task)
    
    # 任务执行后审查
    review = integration.post_execute_review(task_id, result)
    ```
    """
    
    def __init__(self, agent_id: str = "default", 
                 config: Optional[CyberneticsConfig] = None,
                 ai_provider: Optional[Any] = None):
        """
        初始化 Cybernetics 集成
        
        Args:
            agent_id: 智能体ID
            config: 增强配置
            ai_provider: AI 提供者（用于层次化控制的 AI 增强）
        """
        self.agent_id = agent_id
        self.config = config or CyberneticsConfig()
        self.ai_provider = ai_provider
        
        # 集成指标
        self.metrics = IntegrationMetrics(agent_id=agent_id)
        
        # 初始化增强组件
        self._init_components()
    
    def _init_components(self):
        """初始化增强组件"""
        # 1. 反馈控制环
        if self.config.feedback_loop_enabled:
            try:
                self.feedback_loop = FeedbackControlLoop(
                    agent_id=self.agent_id,
                    storage_path=self.config.feedback_storage_path
                )
                print(f"✅ 反馈控制环已初始化")
            except Exception as e:
                print(f"⚠️  反馈控制环初始化失败：{e}")
                self.feedback_loop = None
        else:
            self.feedback_loop = None
        
        # 2. 性能画像
        if self.config.fingerprint_enabled:
            try:
                self.fingerprint = PerformanceFingerprint(
                    agent_id=self.agent_id,
                    storage_path=self.config.fingerprint_storage_path
                )
                print(f"✅ 性能画像已初始化")
            except Exception as e:
                print(f"⚠️  性能画像初始化失败：{e}")
                self.fingerprint = None
        else:
            self.fingerprint = None
        
        # 3. 守护协调器
        if self.config.guard_enabled:
            try:
                self.guard = GuardCoordinator(
                    agent_id=self.agent_id,
                    ai_provider=self.ai_provider if self.config.ai_provider_enabled else None
                )
                print(f"✅ 守护协调器已初始化")
            except Exception as e:
                print(f"⚠️  守护协调器初始化失败：{e}")
                self.guard = None
        else:
            self.guard = None
        
        # 4. 层次化控制器
        if self.config.hierarchical_enabled:
            try:
                self.hierarchical = HierarchicalControlManager(
                    ai_provider=self.ai_provider if self.config.ai_provider_enabled else None
                )
                print(f"✅ 层次化控制器已初始化")
            except Exception as e:
                print(f"⚠️  层次化控制器初始化失败：{e}")
                self.hierarchical = None
        else:
            self.hierarchical = None
    
    def pre_execute_validation(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行前验证
        
        使用守护协调器进行任务预验证
        
        Args:
            task: 任务字典
            
        Returns:
            Dict[str, Any]: 验证结果
        """
        if not self.guard or not self.config.guard_pre_validation:
            return {"passed": True, "reason": "guard_disabled"}
        
        try:
            # 调用守护协调器验证
            validation_result = self.guard.pre_execute_validation(task)
            
            # 更新指标
            self.metrics.guard_validations += 1
            self.metrics.guard_warnings += len(validation_result.warnings)
            
            if not validation_result.passed:
                self.metrics.guard_blocks += 1
            
            # 返回验证结果
            return {
                "passed": validation_result.passed,
                "risk_level": validation_result.risk_level.value,
                "warnings": [
                    {
                        "code": w.warning_code,
                        "message": w.message,
                        "severity": w.severity,
                        "action": w.recommended_action
                    }
                    for w in validation_result.warnings
                ],
                "compensations": [
                    {
                        "strategy_id": c.strategy_id,
                        "error_type": c.error_type,
                        "actions": c.actions,
                        "priority": c.priority,
                        "confidence": c.confidence
                    }
                    for c in validation_result.recommended_compensations
                ],
                "alternatives": validation_result.alternative_strategies,
                "validation_time": validation_result.validation_time
            }
            
        except Exception as e:
            print(f"⚠️  守护验证异常：{e}")
            return {
                "passed": True,  # 验证失败不影响执行
                "error": str(e)
            }
    
    def execute_with_feedback(self, task: Dict[str, Any],
                             executor: Optional[Callable] = None) -> Dict[str, Any]:
        """
        带反馈的任务执行
        
        整合反馈控制环和性能画像执行任务
        
        Args:
            task: 任务字典
            executor: 可选的执行器函数
            
        Returns:
            Dict[str, Any]: 执行结果
        """
        start_time = time.time()
        task_id = task.get('id', f"task_{int(start_time * 1000)}")
        
        # 1. 执行前验证（如果启用）
        validation = self.pre_execute_validation(task)
        
        # 2. 选择执行策略
        strategy = self._select_strategy(task, validation)
        
        # 3. 应用补偿措施
        compensations = self._get_compensations(validation)
        
        # 4. 执行任务
        try:
            if self.feedback_loop and self.config.feedback_loop_enabled:
                # 使用反馈控制环执行
                loop_task = {
                    'id': task_id,
                    'type': task.get('type', 'default'),
                    'complexity': task.get('complexity', 5),
                    'features': task.get('features', {})
                }
                
                if executor:
                    self.feedback_loop.set_executor(executor)
                
                result = self.feedback_loop.execute_with_feedback(loop_task)
                
                # 更新指标
                self.metrics.feedback_loop_calls += 1
                if result.get('success'):
                    self.metrics.feedback_loop_success += 1
                else:
                    self.metrics.feedback_loop_failure += 1
            else:
                # 直接执行
                if executor:
                    result = executor(task)
                else:
                    result = {'success': True, 'task_id': task_id}
            
            # 5. 记录到性能画像
            if self.fingerprint and self.config.fingerprint_enabled:
                execution_time = time.time() - start_time
                
                self.fingerprint.record(
                    task_type=task.get('type', 'default'),
                    task_complexity=task.get('complexity', 5),
                    success=result.get('success', False),
                    error_type=result.get('error_type'),
                    execution_time=execution_time,
                    strategy=strategy,
                    context_features=task.get('features', {})
                )
                
                # 更新指标
                self.metrics.fingerprint_records += 1
            
            # 6. 执行后审查
            if self.guard and self.config.guard_post_review:
                self.guard.post_execute_review(task_id, result)
            
            # 返回结果
            return {
                'success': result.get('success', False),
                'task_id': task_id,
                'strategy': strategy,
                'validation': validation,
                'compensations_applied': compensations,
                'execution_time': time.time() - start_time,
                'feedback_metrics': self.feedback_loop.get_statistics() if self.feedback_loop else None
            }
            
        except Exception as e:
            # 异常处理
            return {
                'success': False,
                'task_id': task_id,
                'error_type': 'execution_exception',
                'error_message': str(e),
                'validation': validation
            }
    
    def _select_strategy(self, task: Dict[str, Any], 
                        validation: Dict[str, Any]) -> str:
        """
        选择执行策略
        
        基于任务特征和验证结果选择策略
        
        Args:
            task: 任务字典
            validation: 验证结果
            
        Returns:
            str: 策略名称
        """
        # 如果验证未通过，使用保守策略
        if not validation.get('passed', True):
            return 'conservative'
        
        # 基于复杂度选择
        complexity = task.get('complexity', 5)
        if complexity > 7:
            return 'conservative'
        elif complexity > 4:
            return 'balanced'
        else:
            return 'aggressive'
    
    def _get_compensations(self, validation: Dict[str, Any]) -> List[str]:
        """
        获取补偿措施
        
        Args:
            validation: 验证结果
            
        Returns:
            List[str]: 补偿措施列表
        """
        compensations = []
        
        for comp in validation.get('compensations', []):
            compensations.extend(comp.get('actions', []))
        
        return compensations
    
    def execute_with_hierarchical_control(self, task: Dict[str, Any],
                                          executor: Optional[Callable] = None) -> Dict[str, Any]:
        """
        使用层次化控制执行任务
        
        需要启用 hierarchical_enabled
        
        Args:
            task: 任务字典
            executor: 可选的执行器函数
            
        Returns:
            Dict[str, Any]: 执行结果
        """
        if not self.hierarchical:
            # 如果层次化控制器未启用，回退到普通执行
            return self.execute_with_feedback(task, executor)
        
        try:
            # 使用层次化控制器执行
            result = self.hierarchical.execute_task(task, executor)
            
            # 更新指标
            self.metrics.strategic_plans += 1
            self.metrics.tactical_decisions += 1
            
            if result.get('strategic_plan', {}).get('ai_enhanced'):
                self.metrics.ai_enhancements += 1
            
            return result
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'fallback': 'hierarchical_to_standard'
            }
    
    def retrieve_similar_cases(self, task: Dict[str, Any], 
                             limit: int = 5) -> List[Dict[str, Any]]:
        """
        检索相似案例
        
        使用性能画像检索相似的历史执行案例
        
        Args:
            task: 任务字典
            limit: 返回数量限制
            
        Returns:
            List[Dict[str, Any]]: 相似案例列表
        """
        if not self.fingerprint:
            return []
        
        try:
            similar = self.fingerprint.retrieve_similar_cases(
                task_type=task.get('type', 'default'),
                task_complexity=task.get('complexity', 5),
                limit=limit
            )
            
            # 更新指标
            self.metrics.fingerprint_retrievals += 1
            self.metrics.fingerprint_hit_rate = len(similar) / limit if limit > 0 else 0
            
            return [
                {
                    'record_id': s.record_id,
                    'task_type': s.task_type,
                    'task_complexity': s.task_complexity,
                    'success': s.success,
                    'error_type': s.error_type,
                    'execution_time': s.execution_time,
                    'strategy': s.strategy,
                    'similarity_score': s.similarity_score,
                    'lessons_learned': s.lessons_learned
                }
                for s in similar
            ]
            
        except Exception as e:
            print(f"⚠️  相似案例检索异常：{e}")
            return []
    
    def get_recommendations(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        获取任务执行建议
        
        综合使用性能画像和反馈控制环提供建议
        
        Args:
            task: 任务字典
            
        Returns:
            Dict[str, Any]: 执行建议
        """
        recommendations = {
            'strategy': self._select_strategy(task, {}),
            'similar_cases': [],
            'failure_patterns': [],
            'guard_warnings': []
        }
        
        # 1. 获取相似案例
        similar = self.retrieve_similar_cases(task, limit=3)
        if similar:
            recommendations['similar_cases'] = similar
            # 基于相似案例建议策略
            successful_strategies = [s['strategy'] for s in similar if s['success']]
            if successful_strategies:
                recommendations['strategy'] = successful_strategies[0]
        
        # 2. 获取失败模式
        if self.fingerprint:
            patterns = self.fingerprint.get_failure_patterns(min_frequency=2)
            recommendations['failure_patterns'] = [
                {
                    'error_type': p.error_type,
                    'mitigation': p.mitigation,
                    'frequency': p.frequency,
                    'failure_rate': p.failure_rate
                }
                for p in patterns[:5]
            ]
        
        # 3. 获取守护警告
        if self.guard:
            validation = self.pre_execute_validation(task)
            recommendations['guard_warnings'] = validation.get('warnings', [])
        
        return recommendations
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        获取集成统计信息
        
        Returns:
            Dict[str, Any]: 各组件统计信息
        """
        stats = {
            'agent_id': self.agent_id,
            'config': asdict(self.config),
            'metrics': asdict(self.metrics),
            'components': {}
        }
        
        # 反馈控制环统计
        if self.feedback_loop:
            stats['components']['feedback_loop'] = self.feedback_loop.get_statistics()
        
        # 性能画像统计
        if self.fingerprint:
            stats['components']['fingerprint'] = self.fingerprint.get_statistics()
        
        # 守护协调器统计
        if self.guard:
            stats['components']['guard'] = self.guard.get_statistics()
        
        # 层次化控制器统计
        if self.hierarchical:
            stats['components']['hierarchical'] = self.hierarchical.get_all_statistics()
        
        return stats
    
    def export_report(self) -> Dict[str, Any]:
        """
        导出完整报告
        
        Returns:
            Dict[str, Any]: 完整报告
        """
        return {
            'agent_id': self.agent_id,
            'timestamp': datetime.now().isoformat(),
            'statistics': self.get_statistics(),
            'recommendations': {
                'total_tasks': self.metrics.feedback_loop_calls,
                'success_rate': self.metrics.feedback_success_rate,
                'guard_pass_rate': self.metrics.guard_pass_rate,
                'fingerprint_records': self.metrics.fingerprint_records,
                'ai_enhancements': self.metrics.ai_enhancements
            },
            'performance_summary': {
                'feedback_efficiency': self._calculate_feedback_efficiency(),
                'guard_effectiveness': self._calculate_guard_effectiveness(),
                'fingerprint_quality': self._calculate_fingerprint_quality()
            }
        }
    
    def _calculate_feedback_efficiency(self) -> float:
        """计算反馈效率"""
        if self.metrics.feedback_loop_calls == 0:
            return 0.0
        return self.metrics.feedback_success_rate
    
    def _calculate_guard_effectiveness(self) -> float:
        """计算守护有效性"""
        if self.metrics.guard_validations == 0:
            return 1.0
        # 有效性 = 通过率 * (1 - 阻止率)
        pass_rate = self.metrics.guard_pass_rate
        block_rate = self.metrics.guard_blocks / self.metrics.guard_validations
        return pass_rate * (1 - block_rate * 0.5)
    
    def _calculate_fingerprint_quality(self) -> float:
        """计算画像质量"""
        if not self.fingerprint:
            return 0.0
        return self.fingerprint.get_statistics().get('success_rate', 0.0)


def create_enhanced_agent_loop(project_root: str = ".",
                              max_iterations: int = 100,
                              task_file: Optional[str] = None,
                              config: Optional[CyberneticsConfig] = None,
                              ai_provider: Optional[Any] = None) -> tuple:
    """
    创建增强版 AgentLoopControllerV2
    
    返回元组：(AgentLoopControllerV2, CyberneticsIntegration)
    
    Args:
        project_root: 项目根目录
        max_iterations: 最大迭代次数
        task_file: 任务文件路径
        config: Cybernetics 配置
        ai_provider: AI 提供者
        
    Returns:
        tuple: (控制器, 集成层)
    """
    # 1. 创建原有控制器
    controller = AgentLoopControllerV2(
        project_root=project_root,
        max_iterations=max_iterations,
        task_file=task_file
    )
    
    # 2. 创建 Cybernetics 集成
    integration = CyberneticsIntegration(
        agent_id=f"agent_loop_{project_root}",
        config=config,
        ai_provider=ai_provider
    )
    
    return controller, integration


# 导出主要类
__all__ = [
    'CyberneticsIntegration',
    'CyberneticsConfig',
    'IntegrationMetrics',
    'create_enhanced_agent_loop'
]
