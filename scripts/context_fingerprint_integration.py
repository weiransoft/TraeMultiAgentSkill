#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
性能画像与双层上下文深度集成

将 PerformanceFingerprint 与 DualLayerContextManager 深度集成：
1. 经验自动沉淀到性能画像
2. 相似案例检索增强知识注入
3. 失败模式自动学习
4. 成功模式库共享

参考：CYBERNETICS_INTEGRATION_PLAN.md v2.0
"""

import json
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict, field
from datetime import datetime

# 导入组件
try:
    from dual_layer_context_manager import (
        DualLayerContextManager,
        ExperienceItem,
        KnowledgeItem,
        TaskContext
    )
    from performance_fingerprint import (
        PerformanceFingerprint,
        ExecutionRecord,
        SimilarCase
    )
except ImportError as e:
    print(f"⚠️  导入组件失败：{e}")


@dataclass
class ExperienceToFingerprintMapper:
    """
    经验到画像映射器
    
    负责将 DualLayerContextManager 中的经验项转换为性能画像记录
    """
    
    @staticmethod
    def map_experience_to_record(experience: ExperienceItem) -> Dict[str, Any]:
        """
        将经验项映射为画像记录
        
        Args:
            experience: 经验项
            
        Returns:
            Dict[str, Any]: 画像记录格式
        """
        return {
            'task_type': experience.task_type,
            'task_complexity': ExperienceToFingerprintMapper._estimate_complexity(experience),
            'success': experience.success,
            'error_type': ExperienceToFingerprintMapper._extract_error_type(experience),
            'execution_time': 0.0,  # 经验项不包含执行时间
            'strategy': 'default',
            'context_features': {
                'description': experience.description,
                'lessons_learned': experience.lessons_learned,
                'patterns': experience.patterns
            }
        }
    
    @staticmethod
    def _estimate_complexity(experience: ExperienceItem) -> int:
        """
        从经验描述估算复杂度
        
        Args:
            experience: 经验项
            
        Returns:
            int: 估算的复杂度 (1-10)
        """
        description = experience.description.lower()
        complexity_indicators = {
            'high': ['complex', 'difficult', 'architecture', 'microservice', 'distributed'],
            'medium': ['feature', 'module', 'component', 'refactor'],
            'low': ['simple', 'fix', 'bug', 'update', 'typo']
        }
        
        for level, keywords in complexity_indicators.items():
            if any(kw in description for kw in keywords):
                if level == 'high':
                    return 8
                elif level == 'medium':
                    return 5
                else:
                    return 3
        
        return 5  # 默认中等复杂度
    
    @staticmethod
    def _extract_error_type(experience: ExperienceItem) -> Optional[str]:
        """
        从经验中提取错误类型
        
        Args:
            experience: 经验项
            
        Returns:
            Optional[str]: 错误类型
        """
        if not experience.success:
            # 从描述中尝试提取错误类型
            description = experience.description.lower()
            
            error_keywords = {
                'timeout': ['timeout', '超时', 'timed out'],
                'syntax_error': ['syntax', '语法', 'parse error'],
                'import_error': ['import', '导入', 'module not found'],
                'memory_error': ['memory', '内存', 'out of memory'],
                'network_error': ['network', '网络', 'connection'],
                'permission_error': ['permission', '权限', 'access denied']
            }
            
            for error_type, keywords in error_keywords.items():
                if any(kw in description for kw in keywords):
                    return error_type
        
        return None


@dataclass
class KnowledgeToFingerprintEnhancer:
    """
    知识到画像增强器
    
    负责将知识库中的知识项增强到性能画像
    """
    
    @staticmethod
    def enhance_fingerprint_with_knowledge(
        fingerprint: PerformanceFingerprint,
        knowledge_items: List[KnowledgeItem]
    ):
        """
        使用知识库增强性能画像

        将知识库中的最佳实践注入为成功模式，
        将经验教训注入为失败模式的缓解措施

        Args:
            fingerprint: 性能画像
            knowledge_items: 知识项列表
        """
        from performance_fingerprint import SuccessPattern

        for knowledge in knowledge_items:
            if knowledge.category == 'best_practices':
                # 最佳实践作为成功模式注入
                success_key = f"success_best_practice_{knowledge.id}"
                if success_key not in fingerprint.success_patterns:
                    key_factors = []
                    if isinstance(knowledge.content, str):
                        key_factors = [knowledge.content]
                    elif isinstance(knowledge.content, dict):
                        key_factors = list(knowledge.content.values())

                    fingerprint.success_patterns[success_key] = SuccessPattern(
                        pattern_id=f"sp_{success_key}",
                        success_type='best_practice',
                        trigger_conditions=[{'type': 'knowledge_id', 'value': knowledge.id}],
                        description=knowledge.title,
                        key_factors=key_factors,
                        frequency=1
                    )
                else:
                    fingerprint.success_patterns[success_key].frequency += 1

            elif knowledge.category == 'lessons_learned':
                # 经验教训作为失败模式的缓解措施注入
                if isinstance(knowledge.content, dict):
                    error_type = knowledge.content.get('error_type', 'unknown')
                    if error_type in fingerprint.failure_patterns:
                        pattern = fingerprint.failure_patterns[error_type]
                        mitigation = knowledge.content.get('mitigation', '')
                        if mitigation:
                            pattern.mitigation = mitigation


class EnhancedTaskContext(TaskContext):
    """
    增强的任务上下文
    
    在原有 TaskContext 基础上集成性能画像能力
    """
    
    def __init__(self, *args, fingerprint: Optional[PerformanceFingerprint] = None, **kwargs):
        """
        初始化增强任务上下文
        
        Args:
            fingerprint: 性能画像实例
        """
        super().__init__(*args, **kwargs)
        self.fingerprint = fingerprint
        self.similar_cases: List[SimilarCase] = []
    
    def retrieve_similar_cases(self, limit: int = 5) -> List[SimilarCase]:
        """
        检索相似案例
        
        Args:
            limit: 返回数量限制
            
        Returns:
            List[SimilarCase]: 相似案例列表
        """
        if not self.fingerprint:
            return []
        
        # 从任务定义中提取特征
        task_features = {
            'task_type': self.task_definition.title if hasattr(self, 'task_definition') else 'unknown',
            'complexity': self._estimate_complexity()
        }
        
        similar = self.fingerprint.retrieve_similar_cases(
            task_type=task_features['task_type'],
            task_complexity=task_features['complexity'],
            limit=limit
        )
        
        self.similar_cases = similar
        return similar
    
    def _estimate_complexity(self) -> int:
        """
        估算当前任务复杂度
        
        Returns:
            int: 复杂度评分
        """
        # 基于任务上下文估计复杂度
        base_complexity = 5
        
        # 根据思考记录数量调整
        if hasattr(self, 'thoughts'):
            thought_count = len(self.thoughts) if isinstance(self.thoughts, list) else 0
            if thought_count > 10:
                base_complexity += 2
            elif thought_count > 5:
                base_complexity += 1
        
        return min(base_complexity, 10)
    
    def apply_compensation_from_similar(self) -> List[str]:
        """
        从相似案例中应用补偿措施
        
        Returns:
            List[str]: 补偿措施列表
        """
        compensations = []
        
        for case in self.similar_cases:
            if not case.success and case.error_type:
                # 从失败案例中提取补偿措施
                compensation = f"避免 {case.error_type}：{case.strategy}"
                compensations.append(compensation)
        
        return compensations


class DualLayerContextFingerprintIntegration:
    """
    双层上下文与性能画像集成类
    
    核心功能：
    1. 经验自动同步到画像
    2. 相似案例检索增强上下文构建
    3. 失败模式学习
    4. 知识与画像双向同步
    """
    
    def __init__(self, 
                 context_manager: DualLayerContextManager,
                 fingerprint: PerformanceFingerprint):
        """
        初始化集成
        
        Args:
            context_manager: 双层上下文管理器
            fingerprint: 性能画像
        """
        self.context_manager = context_manager
        self.fingerprint = fingerprint
        
        # 同步状态
        self.sync_enabled = True
        self.last_sync_time: Optional[str] = None
    
    def sync_experiences_to_fingerprint(self, limit: int = 100) -> int:
        """
        同步经验到性能画像
        
        Args:
            limit: 同步数量限制
            
        Returns:
            int: 同步的经验数量
        """
        if not self.context_manager or not self.context_manager.global_ctx:
            return 0
        
        # 获取全局上下文中的经验
        experiences = self.context_manager.global_ctx.experiences[-limit:]
        sync_count = 0
        
        for exp in experiences:
            # 检查是否已同步
            if self._is_experience_synced(exp):
                continue
            
            # 映射并记录
            record_data = ExperienceToFingerprintMapper.map_experience_to_record(exp)
            
            self.fingerprint.record(
                task_type=record_data['task_type'],
                task_complexity=record_data['task_complexity'],
                success=record_data['success'],
                error_type=record_data['error_type'],
                execution_time=record_data['execution_time'],
                strategy=record_data['strategy'],
                context_features=record_data['context_features']
            )
            
            sync_count += 1
        
        if sync_count > 0:
            self.last_sync_time = datetime.now().isoformat()
        
        return sync_count
    
    def _is_experience_synced(self, experience: ExperienceItem) -> bool:
        """
        检查经验是否已同步
        
        Args:
            experience: 经验项
            
        Returns:
            bool: 是否已同步
        """
        # 简单实现：检查记录库中是否有相同描述的经验
        for record in self.fingerprint.records[-50:]:  # 只检查最近50条
            if hasattr(record, 'context_features'):
                if record.context_features.get('description') == experience.description:
                    return True
        return False
    
    def enhance_context_with_similar_cases(self, 
                                         task_def: Any) -> List[SimilarCase]:
        """
        使用相似案例增强任务上下文
        
        Args:
            task_def: 任务定义
            
        Returns:
            List[SimilarCase]: 相似案例列表
        """
        # 检索相似案例
        similar = self.fingerprint.retrieve_similar_cases(
            task_type=task_def.title if hasattr(task_def, 'title') else 'unknown',
            task_complexity=self._estimate_task_complexity(task_def),
            limit=5
        )
        
        # 为任务上下文注入相关知识
        if self.context_manager and similar:
            self._inject_knowledge_from_cases(similar)
        
        return similar
    
    def _estimate_task_complexity(self, task_def: Any) -> int:
        """
        估算任务复杂度
        
        Args:
            task_def: 任务定义
            
        Returns:
            int: 复杂度评分
        """
        complexity = 5  # 默认中等
        
        if hasattr(task_def, 'description'):
            desc = task_def.description.lower()
            
            high_keywords = ['complex', 'difficult', 'architecture', 'microservice']
            low_keywords = ['simple', 'fix', 'bug', 'update']
            
            if any(kw in desc for kw in high_keywords):
                complexity = 8
            elif any(kw in desc for kw in low_keywords):
                complexity = 3
        
        return complexity
    
    def _inject_knowledge_from_cases(self, cases: List[SimilarCase]):
        """
        从案例注入知识到上下文
        
        Args:
            cases: 相似案例列表
        """
        if not self.context_manager or not self.context_manager.global_ctx:
            return
        
        for case in cases:
            # 创建知识项
            knowledge = KnowledgeItem(
                id=f"from_case_{case.record_id}",
                category='similar_case',
                title=f"相似案例：{case.task_type}",
                content={
                    'case_id': case.record_id,
                    'success': case.success,
                    'strategy': case.strategy,
                    'lessons_learned': case.lessons_learned
                },
                tags=[case.task_type, 'similar_case'],
                source=f"fingerprint:{case.record_id}",
                confidence=case.similarity_score
            )
            
            # 添加到全局上下文
            self.context_manager.global_ctx.add_knowledge(knowledge)
    
    def learn_from_completion(self, task_id: str, success: bool, 
                            artifacts: Optional[Dict] = None):
        """
        从任务完成中学习
        
        在任务完成时自动学习并更新画像
        
        Args:
            task_id: 任务ID
            success: 是否成功
            artifacts: 任务产出
        """
        # 获取当前任务的上下文
        if not self.context_manager or not self.context_manager.current_task_ctx:
            return
        
        task_ctx = self.context_manager.current_task_ctx
        
        # 提取任务特征
        task_type = task_ctx.task_definition.title if hasattr(task_ctx.task_definition, 'title') else 'unknown'
        complexity = self._estimate_task_complexity(task_ctx.task_definition)
        
        # 提取错误信息
        error_type = None
        if not success and artifacts:
            error_type = artifacts.get('error_type')
        
        # 记录到画像
        self.fingerprint.record(
            task_type=task_type,
            task_complexity=complexity,
            success=success,
            error_type=error_type,
            execution_time=artifacts.get('execution_time', 0) if artifacts else 0,
            strategy='default',
            context_features=artifacts or {}
        )
        
        # 如果失败，提取经验教训
        if not success:
            self._learn_from_failure(task_ctx, artifacts)
    
    def _learn_from_failure(self, task_ctx: TaskContext, artifacts: Optional[Dict]):
        """
        从失败中学习
        
        Args:
            task_ctx: 任务上下文
            artifacts: 任务产出
        """
        if not self.context_manager or not self.context_manager.global_ctx:
            return
        
        # 创建失败经验
        lessons = []
        
        if artifacts and 'error_message' in artifacts:
            lessons.append(f"错误：{artifacts['error_message']}")
        
        if artifacts and 'suggestions' in artifacts:
            lessons.extend(artifacts['suggestions'])
        
        # 从失败模式中学习缓解措施
        if artifacts and 'error_type' in artifacts:
            error_type = artifacts['error_type']
            patterns = self.fingerprint.get_failure_patterns(min_frequency=1)
            
            for pattern in patterns:
                if pattern.error_type == error_type:
                    lessons.append(f"缓解措施：{pattern.mitigation}")
                    break
        
        # 添加到全局上下文
        experience = ExperienceItem(
            id=f"exp_from_failure_{task_ctx.task_definition.task_id}",
            task_id=task_ctx.task_definition.task_id,
            task_type=task_ctx.task_definition.title,
            success=False,
            description=artifacts.get('error_message', '任务失败') if artifacts else '任务失败',
            lessons_learned=lessons
        )
        
        self.context_manager.global_ctx.add_experience(experience)
    
    def get_integration_report(self) -> Dict[str, Any]:
        """
        获取集成报告
        
        Returns:
            Dict[str, Any]: 集成报告
        """
        return {
            'timestamp': datetime.now().isoformat(),
            'last_sync_time': self.last_sync_time,
            'sync_enabled': self.sync_enabled,
            'context_experiences': len(self.context_manager.global_ctx.experiences) if self.context_manager and self.context_manager.global_ctx else 0,
            'fingerprint_records': self.fingerprint.total_executions,
            'fingerprint_success_rate': self.fingerprint.get_statistics().get('success_rate', 0),
            'failure_patterns_count': len(self.fingerprint.failure_patterns),
            'similar_cases_available': self.fingerprint.has_sufficient_samples()
        }


def create_enhanced_context_manager(project_root: str = ".",
                                     skill_root: Optional[str] = None,
                                     fingerprint_storage: Optional[str] = None) -> tuple:
    """
    创建增强版双层上下文管理器
    
    返回：(DualLayerContextManager, PerformanceFingerprint, DualLayerContextFingerprintIntegration)
    
    Args:
        project_root: 项目根目录
        skill_root: 技能目录
        fingerprint_storage: 画像存储路径
        
    Returns:
        tuple: (上下文管理器, 性能画像, 集成实例)
    """
    # 1. 创建原有上下文管理器
    context_manager = DualLayerContextManager(
        project_root=project_root,
        skill_root=skill_root or str(Path(__file__).parent.parent)
    )
    
    # 2. 创建性能画像
    agent_id = f"context_{project_root}"
    fingerprint = PerformanceFingerprint(
        agent_id=agent_id,
        storage_path=fingerprint_storage or f"./fingerprints/{agent_id}"
    )
    
    # 3. 创建集成实例
    integration = DualLayerContextFingerprintIntegration(
        context_manager=context_manager,
        fingerprint=fingerprint
    )
    
    return context_manager, fingerprint, integration


# 导出主要类
__all__ = [
    'ExperienceToFingerprintMapper',
    'KnowledgeToFingerprintEnhancer',
    'EnhancedTaskContext',
    'DualLayerContextFingerprintIntegration',
    'create_enhanced_context_manager'
]
