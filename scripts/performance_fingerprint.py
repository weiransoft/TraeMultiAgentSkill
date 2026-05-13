#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
性能画像模块

基于工程控制论的性能画像机制：
- 记录执行案例和失败模式
- 提供相似案例检索（非预测）
- 支持冷启动优雅降级

参考：cybernetics-agent 工程控制论思路 + trae-multi-agent 多角色协作机制
"""

import json
import threading
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict, field
from datetime import datetime
from collections import defaultdict, Counter


@dataclass
class FailurePattern:
    """
    失败模式数据类
    
    记录一种失败模式的特征和触发条件
    """
    pattern_id: str
    error_type: str  # 错误类型
    trigger_conditions: List[Dict[str, Any]]  # 触发条件
    description: str  # 模式描述
    mitigation: str  # 缓解建议
    frequency: int = 0  # 出现频率
    success_count: int = 0  # 成功次数
    failure_count: int = 0  # 失败次数
    last_observed: str = field(default_factory=lambda: datetime.now().isoformat())
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    @property
    def success_rate(self) -> float:
        """计算成功率"""
        total = self.success_count + self.failure_count
        if total == 0:
            return 1.0
        return self.success_count / total
    
    @property
    def failure_rate(self) -> float:
        """计算失败率"""
        return 1.0 - self.success_rate


@dataclass
class SuccessPattern:
    """
    成功模式数据类
    
    记录一种成功模式的特征和关键因素
    """
    pattern_id: str
    success_type: str  # 成功类型
    trigger_conditions: List[Dict[str, Any]]  # 触发条件
    description: str  # 模式描述
    key_factors: List[str]  # 关键成功因素
    frequency: int = 0  # 出现频率
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ExecutionRecord:
    """
    执行记录数据类
    
    记录一次任务执行的基本信息
    """
    record_id: str
    agent_id: str  # 智能体ID
    task_type: str  # 任务类型
    task_complexity: int  # 任务复杂度 1-10
    success: bool  # 是否成功
    error_type: Optional[str] = None  # 错误类型
    execution_time: float = 0.0  # 执行时间（秒）
    strategy: str = "default"  # 使用的策略
    context_features: Dict[str, Any] = field(default_factory=dict)  # 上下文特征
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class SimilarCase:
    """
    相似案例数据类
    
    表示一个与当前任务相似的历史案例
    """
    record_id: str
    agent_id: str
    task_type: str
    task_complexity: int
    success: bool
    error_type: Optional[str]
    execution_time: float
    strategy: str
    similarity_score: float  # 相似度评分 0-1
    lessons_learned: List[str] = field(default_factory=list)


class PerformanceFingerprint:
    """
    性能画像类
    
    核心功能：
    1. 记录执行案例（成功/失败）
    2. 提取失败模式和成功模式
    3. 检索相似案例（非预测）
    4. 冷启动优雅降级
    
    注意：本实现采用案例检索而非预测，
    以避免 AI 任务预测不可靠的问题
    """
    
    def __init__(self, agent_id: str, storage_path: Optional[str] = None):
        """
        初始化性能画像
        
        Args:
            agent_id: 智能体ID
            storage_path: 可选的存储路径
        """
        self.agent_id = agent_id
        self.storage_path = storage_path or f"./fingerprints/{agent_id}"
        
        # 执行记录库
        self.records: List[ExecutionRecord] = []
        
        # 失败模式库
        self.failure_patterns: Dict[str, FailurePattern] = {}
        
        # 成功模式库
        self.success_patterns: Dict[str, SuccessPattern] = {}
        
        # 上下文-结果映射
        self.context_outcome_map: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {
                'task_count': 0,
                'success_count': 0,
                'failure_count': 0,
                'total_time': 0.0,
                'error_types': Counter()
            }
        )
        
        # 统计信息
        self.total_executions = 0
        self.success_count = 0
        self.failure_count = 0
        
        # 最小样本数（用于检索）
        self.min_samples = 10
        
        # 锁
        self._lock = threading.Lock()
        
        # 加载已有数据
        self._load_data()
    
    def record(self, task_type: str, task_complexity: int, success: bool,
               error_type: Optional[str] = None, execution_time: float = 0.0,
               strategy: str = "default", context_features: Optional[Dict[str, Any]] = None) -> ExecutionRecord:
        """
        记录一次执行
        
        Args:
            task_type: 任务类型
            task_complexity: 任务复杂度 (1-10)
            success: 是否成功
            error_type: 错误类型（如有）
            execution_time: 执行时间（秒）
            strategy: 使用的策略
            context_features: 上下文特征
            
        Returns:
            ExecutionRecord: 创建的记录
        """
        record = ExecutionRecord(
            record_id=f"rec_{self.agent_id}_{self.total_executions + 1}_{int(datetime.now().timestamp())}",
            agent_id=self.agent_id,
            task_type=task_type,
            task_complexity=task_complexity,
            success=success,
            error_type=error_type,
            execution_time=execution_time,
            strategy=strategy,
            context_features=context_features or {}
        )
        
        with self._lock:
            # 添加到记录库
            self.records.append(record)
            
            # 限制记录库大小
            if len(self.records) > 10000:
                self.records = self.records[-5000:]
            
            # 更新统计
            self._update_statistics(record)
            
            # 更新模式库
            self._update_patterns(record)
            
            # 更新上下文映射
            self._update_context_mapping(record)
            
            # 更新总数
            self.total_executions += 1
            if success:
                self.success_count += 1
            else:
                self.failure_count += 1
        
        # 持久化保存
        self._save_record(record)
        
        return record
    
    def _update_statistics(self, record: ExecutionRecord):
        """
        更新统计信息
        
        Args:
            record: 执行记录
        """
        # 统计已在主方法中更新
    
    def _update_patterns(self, record: ExecutionRecord):
        """
        更新模式库
        
        Args:
            record: 执行记录
        """
        if record.error_type:
            # 更新失败模式
            if record.error_type not in self.failure_patterns:
                self.failure_patterns[record.error_type] = FailurePattern(
                    pattern_id=f"fp_{record.error_type}",
                    error_type=record.error_type,
                    trigger_conditions=self._extract_conditions(record),
                    description=f"错误类型: {record.error_type}",
                    mitigation=self._generate_mitigation(record.error_type)
                )
            
            pattern = self.failure_patterns[record.error_type]
            pattern.frequency += 1
            pattern.last_observed = datetime.now().isoformat()
            if record.success:
                pattern.success_count += 1
            else:
                pattern.failure_count += 1
        else:
            # 更新成功模式
            success_key = f"success_{record.task_type}_{record.strategy}"
            if success_key not in self.success_patterns:
                self.success_patterns[success_key] = SuccessPattern(
                    pattern_id=f"sp_{success_key}",
                    success_type=record.task_type,
                    trigger_conditions=self._extract_conditions(record),
                    description=f"任务类型 {record.task_type} 成功执行",
                    key_factors=[record.strategy]
                )
            
            sp = self.success_patterns[success_key]
            sp.frequency += 1
    
    def _extract_conditions(self, record: ExecutionRecord) -> List[Dict[str, Any]]:
        """
        从记录中提取触发条件
        
        Args:
            record: 执行记录
            
        Returns:
            List[Dict[str, Any]]: 触发条件列表
        """
        return [
            {'type': 'task_type', 'value': record.task_type},
            {'type': 'complexity', 'operator': '==', 'value': record.task_complexity},
            {'type': 'strategy', 'value': record.strategy}
        ]
    
    def _generate_mitigation(self, error_type: str) -> str:
        """
        生成缓解建议
        
        Args:
            error_type: 错误类型
            
        Returns:
            str: 缓解建议
        """
        mitigation_map = {
            'timeout': '建议增加执行超时时间或简化任务',
            'memory_error': '建议减少并发任务数量或优化内存使用',
            'syntax_error': '建议检查代码语法和格式',
            'import_error': '建议检查依赖是否正确安装',
            'permission_error': '建议检查文件权限设置',
            'network_error': '建议检查网络连接状态'
        }
        return mitigation_map.get(error_type, '建议查看详细错误日志进行排查')
    
    def _update_context_mapping(self, record: ExecutionRecord):
        """
        更新上下文映射
        
        Args:
            record: 执行记录
        """
        context_key = self._get_context_key(record)
        mapping = self.context_outcome_map[context_key]
        
        mapping['task_count'] += 1
        mapping['total_time'] += record.execution_time
        
        if record.success:
            mapping['success_count'] += 1
        else:
            mapping['failure_count'] += 1
            if record.error_type:
                mapping['error_types'][record.error_type] += 1
    
    def _get_context_key(self, record: ExecutionRecord) -> str:
        """
        获取上下文键
        
        Args:
            record: 执行记录
            
        Returns:
            str: 上下文键
        """
        complexity_level = 'low' if record.task_complexity <= 3 else ('medium' if record.task_complexity <= 7 else 'high')
        return f"{record.task_type}_{complexity_level}_{record.strategy}"
    
    def has_sufficient_samples(self) -> bool:
        """
        检查是否有足够样本进行检索
        
        Returns:
            bool: 是否有足够样本
        """
        return self.total_executions >= self.min_samples
    
    def retrieve_similar_cases(self, task_type: str, task_complexity: int,
                               limit: int = 5) -> List[SimilarCase]:
        """
        检索相似案例（非预测）
        
        基于任务特征检索相似的历史案例，供决策参考
        
        Args:
            task_type: 任务类型
            task_complexity: 任务复杂度
            limit: 返回数量限制
            
        Returns:
            List[SimilarCase]: 相似案例列表
        """
        with self._lock:
            # 如果样本不足，返回空列表（优雅降级）
            if not self.has_sufficient_samples():
                return []
            
            # 计算相似度并排序
            scored_records = []
            for record in self.records:
                similarity = self._calculate_similarity(record, task_type, task_complexity)
                if similarity > 0.3:  # 相似度阈值
                    scored_records.append((record, similarity))
            
            # 按相似度排序
            scored_records.sort(key=lambda x: x[1], reverse=True)
            
            # 转换为SimilarCase并返回
            return [
                SimilarCase(
                    record_id=record.record_id,
                    agent_id=record.agent_id,
                    task_type=record.task_type,
                    task_complexity=record.task_complexity,
                    success=record.success,
                    error_type=record.error_type,
                    execution_time=record.execution_time,
                    strategy=record.strategy,
                    similarity_score=similarity,
                    lessons_learned=self._get_lessons_learned(record)
                )
                for record, similarity in scored_records[:limit]
            ]
    
    def _calculate_similarity(self, record: ExecutionRecord, 
                             task_type: str, task_complexity: int) -> float:
        """
        计算相似度
        
        Args:
            record: 历史记录
            task_type: 目标任务类型
            task_complexity: 目标任务复杂度
            
        Returns:
            float: 相似度评分 (0-1)
        """
        score = 0.0
        
        # 任务类型权重 0.5
        if record.task_type == task_type:
            score += 0.5
        
        # 复杂度权重 0.3
        complexity_diff = abs(record.task_complexity - task_complexity)
        if complexity_diff == 0:
            score += 0.3
        elif complexity_diff == 1:
            score += 0.15
        elif complexity_diff == 2:
            score += 0.05
        
        # 最近性权重 0.2（最近的任务更有参考价值）
        recency = 0.2  # 简化处理，所有记录都有基本分数
        
        return min(score + recency, 1.0)
    
    def _get_lessons_learned(self, record: ExecutionRecord) -> List[str]:
        """
        获取经验教训
        
        Args:
            record: 执行记录
            
        Returns:
            List[str]: 经验教训列表
        """
        lessons = []
        
        if record.success:
            lessons.append(f"使用{record.strategy}策略成功完成任务")
        else:
            lessons.append(f"错误类型: {record.error_type}")
            if record.error_type in self.failure_patterns:
                pattern = self.failure_patterns[record.error_type]
                lessons.append(f"缓解建议: {pattern.mitigation}")
        
        return lessons
    
    def get_failure_patterns(self, min_frequency: int = 2) -> List[FailurePattern]:
        """
        获取失败模式
        
        Args:
            min_frequency: 最小频率过滤
            
        Returns:
            List[FailurePattern]: 失败模式列表
        """
        with self._lock:
            return [
                p for p in self.failure_patterns.values()
                if p.frequency >= min_frequency
            ]
    
    def get_success_patterns(self, min_frequency: int = 2) -> List[SuccessPattern]:
        """
        获取成功模式
        
        Args:
            min_frequency: 最小频率过滤
            
        Returns:
            List[SuccessPattern]: 成功模式列表
        """
        with self._lock:
            return [
                p for p in self.success_patterns.values()
                if p.frequency >= min_frequency
            ]
    
    def get_context_outcome(self, task_type: str, complexity: int, 
                           strategy: str) -> Dict[str, Any]:
        """
        获取上下文结果统计
        
        Args:
            task_type: 任务类型
            complexity: 复杂度
            strategy: 策略
            
        Returns:
            Dict[str, Any]: 上下文结果统计
        """
        context_key = f"{task_type}_{complexity}_{strategy}"
        return dict(self.context_outcome_map.get(context_key, {
            'task_count': 0,
            'success_count': 0,
            'failure_count': 0,
            'total_time': 0.0,
            'error_types': {}
        }))
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        获取统计信息
        
        Returns:
            Dict[str, Any]: 统计信息
        """
        with self._lock:
            return {
                'agent_id': self.agent_id,
                'total_executions': self.total_executions,
                'success_count': self.success_count,
                'failure_count': self.failure_count,
                'success_rate': self.success_count / self.total_executions if self.total_executions > 0 else 0.0,
                'average_execution_time': sum(r.execution_time for r in self.records) / len(self.records) if self.records else 0.0,
                'failure_pattern_count': len(self.failure_patterns),
                'success_pattern_count': len(self.success_patterns),
                'has_sufficient_samples': self.has_sufficient_samples()
            }
    
    def export(self) -> Dict[str, Any]:
        """
        导出画像数据
        
        Returns:
            Dict[str, Any]: 画像数据
        """
        with self._lock:
            return {
                'agent_id': self.agent_id,
                'exported_at': datetime.now().isoformat(),
                'statistics': self.get_statistics(),
                'failure_patterns': [asdict(p) for p in self.failure_patterns.values()],
                'success_patterns': [asdict(p) for p in self.success_patterns.values()],
                'records': [asdict(r) for r in self.records[-100:]]  # 只导出最近100条
            }
    
    def _save_record(self, record: ExecutionRecord):
        """
        保存记录到存储
        
        Args:
            record: 执行记录
        """
        try:
            storage_dir = Path(self.storage_path)
            storage_dir.mkdir(parents=True, exist_ok=True)
            
            records_file = storage_dir / "records.json"
            
            # 追加保存
            existing = []
            if records_file.exists():
                try:
                    with open(records_file, 'r', encoding='utf-8') as f:
                        existing = json.load(f)
                except Exception:
                    pass
            
            existing.append(asdict(record))
            
            # 限制文件大小
            if len(existing) > 5000:
                existing = existing[-2500:]
            
            with open(records_file, 'w', encoding='utf-8') as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
                
        except Exception:
            pass  # 忽略存储错误
    
    def _load_data(self):
        """
        从存储加载数据
        """
        try:
            storage_dir = Path(self.storage_path)
            if not storage_dir.exists():
                return
            
            # 加载记录
            records_file = storage_dir / "records.json"
            if records_file.exists():
                with open(records_file, 'r', encoding='utf-8') as f:
                    records_data = json.load(f)
                    for r in records_data:
                        try:
                            record = ExecutionRecord(**r)
                            self.records.append(record)
                            self.total_executions += 1
                            if record.success:
                                self.success_count += 1
                            else:
                                self.failure_count += 1
                        except Exception:
                            continue
                            
        except Exception:
            pass  # 忽略加载错误


# 导出主要类
__all__ = [
    'PerformanceFingerprint',
    'FailurePattern',
    'SuccessPattern',
    'ExecutionRecord',
    'SimilarCase'
]
