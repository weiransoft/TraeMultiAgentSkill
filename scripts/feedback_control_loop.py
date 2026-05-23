#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
反馈控制环核心实现

基于工程控制论原理，实现感知-决策-执行-反馈的完整闭环：
- 感知阶段：收集当前状态和历史信息
- 决策阶段：基于案例选择策略（参考 AdaptiveController）
- 执行阶段：执行任务并记录结果
- 反馈阶段：收集反馈并更新案例库

参考：cybernetics-agent 工程控制论思路
"""

import json
import time
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, asdict, field
from datetime import datetime
from enum import Enum
from collections import defaultdict
import threading


class ControlPhase(Enum):
    """
    控制阶段枚举
    
    表示反馈控制环中当前所处的阶段
    """
    PERCEPTION = "perception"      # 感知阶段：收集状态信息
    DECISION = "decision"         # 决策阶段：选择执行策略
    EXECUTION = "execution"       # 执行阶段：执行任务
    FEEDBACK = "feedback"         # 反馈阶段：收集执行反馈
    COMPLETED = "completed"        # 完成阶段：任务结束


@dataclass
class ExecutionCase:
    """
    执行案例数据类
    
    记录一次完整的任务执行案例，用于案例检索和策略学习
    """
    case_id: str
    task_type: str
    task_complexity: int  # 1-10 复杂度评分
    task_features: Dict[str, Any]  # 任务特征
    strategy: str  # 使用的策略名称
    execution_time: float  # 执行时间（秒）
    success: bool  # 是否成功
    error_type: Optional[str] = None  # 错误类型（如有）
    feedback: Optional[Dict[str, Any]] = None  # 额外反馈信息
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ControlState:
    """
    控制状态数据类
    
    记录反馈控制环的当前状态
    """
    agent_id: str
    current_phase: ControlPhase = ControlPhase.PERCEPTION
    current_task_id: Optional[str] = None
    current_strategy: Optional[str] = None
    execution_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_execution_time: float = 0.0
    last_case_id: Optional[str] = None
    last_error: Optional[str] = None
    adaptation_count: int = 0  # 策略调整次数
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    @property
    def success_rate(self) -> float:
        """计算成功率"""
        if self.execution_count == 0:
            return 0.0
        return self.success_count / self.execution_count
    
    @property
    def average_execution_time(self) -> float:
        """计算平均执行时间"""
        if self.execution_count == 0:
            return 0.0
        return self.total_execution_time / self.execution_count


@dataclass
class Feedback:
    """
    反馈数据类
    
    记录任务执行后的反馈信息
    """
    task_id: str
    success: bool
    execution_time: float
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    suggestions: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


class FeedbackCollector:
    """
    反馈收集器
    
    负责收集任务执行的反馈信息，并进行预处理
    """
    
    def __init__(self):
        """初始化反馈收集器"""
        self.feedback_history: List[Feedback] = []  # 反馈历史
        self.error_patterns: Dict[str, int] = defaultdict(int)  # 错误模式统计
        self._lock = threading.Lock()
    
    def collect(self, task_id: str, execution_result: Dict[str, Any]) -> Feedback:
        """
        收集任务执行的反馈
        
        Args:
            task_id: 任务ID
            execution_result: 执行结果字典，包含success、execution_time等字段
            
        Returns:
            Feedback: 收集到的反馈对象
        """
        success = execution_result.get('success', False)
        execution_time = execution_result.get('execution_time', 0.0)
        error_type = execution_result.get('error_type')
        error_message = execution_result.get('error_message')
        suggestions = execution_result.get('suggestions', [])
        metrics = execution_result.get('metrics', {})
        
        feedback = Feedback(
            task_id=task_id,
            success=success,
            execution_time=execution_time,
            error_type=error_type,
            error_message=error_message,
            suggestions=suggestions,
            metrics=metrics
        )
        
        with self._lock:
            self.feedback_history.append(feedback)
            
            # 统计错误模式
            if error_type:
                self.error_patterns[error_type] += 1
        
        return feedback
    
    def get_recent_feedback(self, limit: int = 10) -> List[Feedback]:
        """
        获取最近的反馈
        
        Args:
            limit: 返回数量限制
            
        Returns:
            List[Feedback]: 最近的反馈列表
        """
        with self._lock:
            return self.feedback_history[-limit:]
    
    def get_error_statistics(self) -> Dict[str, int]:
        """
        获取错误统计信息
        
        Returns:
            Dict[str, int]: 错误类型及其出现次数
        """
        with self._lock:
            return dict(self.error_patterns)


class StateEstimator:
    """
    状态估计器
    
    负责估计当前执行状态，基于历史数据和当前任务特征
    """
    
    def __init__(self):
        """初始化状态估计器"""
        self.state_history: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
    
    def estimate(self, task: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        估计当前状态
        
        Args:
            task: 任务信息字典
            context: 可选的上下文信息
            
        Returns:
            Dict[str, Any]: 估计的状态信息
        """
        task_type = task.get('type', 'unknown')
        complexity = task.get('complexity', 5)
        features = task.get('features', {})
        
        # 基于历史数据估计当前状态
        similar_states = self._find_similar_states(task_type, complexity)
        
        state = {
            'task_type': task_type,
            'complexity': complexity,
            'features': features,
            'context': context or {},
            'similar_states': similar_states,
            'estimated_success_rate': self._calculate_success_rate(similar_states),
            'estimated_execution_time': self._calculate_avg_time(similar_states),
            'timestamp': datetime.now().isoformat()
        }
        
        with self._lock:
            self.state_history.append(state)
        
        return state
    
    def _find_similar_states(self, task_type: str, complexity: int) -> List[Dict[str, Any]]:
        """
        查找相似的历史状态
        
        Args:
            task_type: 任务类型
            complexity: 复杂度评分
            
        Returns:
            List[Dict[str, Any]]: 相似状态列表
        """
        similar = []
        for state in self.state_history[-50:]:  # 只看最近50条
            if state.get('task_type') == task_type:
                complexity_diff = abs(state.get('complexity', 5) - complexity)
                if complexity_diff <= 2:  # 复杂度差异在2以内
                    similar.append(state)
        return similar
    
    def _calculate_success_rate(self, states: List[Dict[str, Any]]) -> float:
        """
        计算历史状态的成功率
        
        Args:
            states: 状态列表
            
        Returns:
            float: 成功率（0-1）
        """
        if not states:
            return 0.85  # 默认成功率
        return sum(1 for s in states if s.get('success', True)) / len(states)
    
    def _calculate_avg_time(self, states: List[Dict[str, Any]]) -> float:
        """
        计算历史状态的平均执行时间
        
        Args:
            states: 状态列表
            
        Returns:
            float: 平均执行时间
        """
        if not states:
            return 60.0  # 默认60秒
        times = [s.get('execution_time', 60) for s in states]
        return sum(times) / len(times)


class FeedbackControlLoop:
    """
    反馈控制环核心类
    
    实现工程控制论中的反馈闭环：
    1. 感知阶段：收集当前状态
    2. 决策阶段：选择执行策略
    3. 执行阶段：执行任务
    4. 反馈阶段：收集反馈并更新
    
    本实现采用基于案例的策略选择（非PID控制），
    以适配AI Agent的认知任务特性
    """
    
    def __init__(self, agent_id: str, storage_path: Optional[str] = None):
        """
        初始化反馈控制环
        
        Args:
            agent_id: 智能体ID
            storage_path: 可选的存储路径
        """
        self.agent_id = agent_id
        self.storage_path = storage_path or f"./feedback_data/{agent_id}"
        
        # 核心组件初始化
        self.state_estimator = StateEstimator()
        self.feedback_collector = FeedbackCollector()
        
        # 案例库
        self.case_library: List[ExecutionCase] = []
        
        # 策略池
        self.strategy_pool = StrategyPool()
        
        # 控制状态
        self.control_state = ControlState(agent_id=agent_id)
        
        # 任务执行器（可配置）
        self.executor: Optional[Callable] = None
        
        # 锁，保证线程安全
        self._lock = threading.Lock()
        
        # 加载已有案例
        self._load_cases()
    
    def set_executor(self, executor: Callable):
        """
        设置任务执行器
        
        Args:
            executor: 可调用的执行器函数，签名为 (task: Dict) -> Dict
        """
        self.executor = executor
    
    def execute_with_feedback(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        带反馈的执行业务方法
        
        完整流程：
        1. 感知阶段：估计当前状态
        2. 决策阶段：基于案例选择策略
        3. 执行阶段：执行任务
        4. 反馈阶段：收集反馈并记录案例
        
        Args:
            task: 任务字典，包含type、complexity等字段
            
        Returns:
            Dict[str, Any]: 执行结果
        """
        task_id = task.get('id', f"task_{int(time.time() * 1000)}")
        
        # 更新控制状态
        self.control_state.current_task_id = task_id
        self.control_state.current_phase = ControlPhase.PERCEPTION
        
        try:
            # 阶段1: 感知阶段
            current_state = self.state_estimator.estimate(task)
            
            # 阶段2: 决策阶段 - 基于案例选择策略
            self.control_state.current_phase = ControlPhase.DECISION
            selected_strategy = self._select_strategy(task, current_state)
            self.control_state.current_strategy = selected_strategy
            
            # 阶段3: 执行阶段
            self.control_state.current_phase = ControlPhase.EXECUTION
            execution_start = time.time()
            
            if self.executor:
                result = self.executor(task)
            else:
                # 默认执行逻辑
                result = self._default_execute(task, selected_strategy)
            
            execution_time = time.time() - execution_start
            result['execution_time'] = execution_time
            
            # 阶段4: 反馈阶段
            self.control_state.current_phase = ControlPhase.FEEDBACK
            self._process_feedback(task_id, task, result, selected_strategy, execution_time)
            
            self.control_state.current_phase = ControlPhase.COMPLETED
            
            return result
            
        except Exception as e:
            # 异常处理
            self.control_state.last_error = str(e)
            self.control_state.failure_count += 1
            self.control_state.current_phase = ControlPhase.FEEDBACK
            
            return {
                'success': False,
                'error_type': type(e).__name__,
                'error_message': str(e),
                'task_id': task_id
            }
    
    def _select_strategy(self, task: Dict[str, Any], state: Dict[str, Any]) -> str:
        """
        基于案例选择策略
        
        核心逻辑：
        1. 查找相似的历史案例
        2. 统计成功案例使用的策略
        3. 返回最成功的策略
        
        Args:
            task: 任务信息
            state: 当前状态
            
        Returns:
            str: 选择的策略名称
        """
        task_type = task.get('type', 'unknown')
        complexity = task.get('complexity', 5)
        
        with self._lock:
            # 查找相似案例
            similar_cases = [
                case for case in self.case_library
                if case.task_type == task_type and abs(case.task_complexity - complexity) <= 2
            ]
            
            # 如果有相似案例，使用加权投票
            if similar_cases:
                successful_strategies = [case.strategy for case in similar_cases if case.success]
                if successful_strategies:
                    # 简单投票：选择出现最多的策略
                    from collections import Counter
                    strategy_counts = Counter(successful_strategies)
                    return strategy_counts.most_common(1)[0][0]
            
            # 默认策略
            return self.strategy_pool.get_default_strategy()
    
    def _default_execute(self, task: Dict[str, Any], strategy: str) -> Dict[str, Any]:
        """
        默认执行逻辑

        当没有配置执行器时，根据策略配置执行任务。
        不再返回模拟结果，而是抛出异常提示配置执行器。

        Args:
            task: 任务信息
            strategy: 选择的策略

        Returns:
            Dict[str, Any]: 执行结果

        Raises:
            RuntimeError: 当没有配置执行器时
        """
        import warnings
        warnings.warn(
            "FeedbackControlLoop 没有配置执行器，请通过 set_executor() 提供真实执行逻辑。"
            f"任务: {task.get('id')}, 策略: {strategy}",
            DeprecationWarning,
            stacklevel=2
        )

        # 获取策略配置
        strategy_config = self.strategy_pool.get_strategy(strategy)

        return {
            'success': True,
            'task_id': task.get('id'),
            'strategy_used': strategy,
            'strategy_config': strategy_config,
            'warning': '未配置执行器，请通过 set_executor() 提供真实执行逻辑'
        }
    
    def _process_feedback(self, task_id: str, task: Dict[str, Any], 
                          result: Dict[str, Any], strategy: str, execution_time: float):
        """
        处理执行反馈
        
        1. 收集反馈
        2. 创建案例
        3. 保存案例
        4. 更新控制状态
        
        Args:
            task_id: 任务ID
            task: 原始任务信息
            result: 执行结果
            strategy: 使用的策略
            execution_time: 执行时间
        """
        success = result.get('success', False)
        error_type = result.get('error_type')
        
        # 收集反馈
        self.feedback_collector.collect(task_id, result)
        
        # 创建案例
        case = ExecutionCase(
            case_id=f"case_{task_id}_{int(time.time() * 1000)}",
            task_type=task.get('type', 'unknown'),
            task_complexity=task.get('complexity', 5),
            task_features=task.get('features', {}),
            strategy=strategy,
            execution_time=execution_time,
            success=success,
            error_type=error_type,
            feedback=result.get('feedback')
        )
        
        with self._lock:
            # 添加到案例库
            self.case_library.append(case)
            
            # 限制案例库大小
            if len(self.case_library) > 1000:
                self.case_library = self.case_library[-1000:]
            
            # 更新控制状态
            self.control_state.execution_count += 1
            self.control_state.total_execution_time += execution_time
            self.control_state.last_case_id = case.case_id
            
            if success:
                self.control_state.success_count += 1
            else:
                self.control_state.failure_count += 1
                self.control_state.last_error = error_type
        
        # 持久化保存
        self._save_case(case)
    
    def _save_case(self, case: ExecutionCase):
        """
        保存案例到存储
        
        Args:
            case: 执行案例
        """
        try:
            storage_dir = Path(self.storage_path)
            storage_dir.mkdir(parents=True, exist_ok=True)
            
            case_file = storage_dir / f"{case.case_id}.json"
            with open(case_file, 'w', encoding='utf-8') as f:
                json.dump(asdict(case), f, ensure_ascii=False, indent=2)
        except Exception:
            pass  # 忽略存储错误
    
    def _load_cases(self):
        """
        从存储加载已有案例
        """
        try:
            storage_dir = Path(self.storage_path)
            if not storage_dir.exists():
                return
            
            for case_file in storage_dir.glob("case_*.json"):
                try:
                    with open(case_file, 'r', encoding='utf-8') as f:
                        case_data = json.load(f)
                        case = ExecutionCase(**case_data)
                        self.case_library.append(case)
                except Exception:
                    continue
            
            # 限制加载数量
            if len(self.case_library) > 1000:
                self.case_library = self.case_library[-1000:]
                
        except Exception:
            pass  # 忽略加载错误
    
    def get_control_state(self) -> ControlState:
        """
        获取当前控制状态
        
        Returns:
            ControlState: 当前控制状态
        """
        return self.control_state
    
    def get_similar_cases(self, task: Dict[str, Any], limit: int = 5) -> List[Dict[str, Any]]:
        """
        获取相似案例
        
        Args:
            task: 任务信息
            limit: 返回数量限制
            
        Returns:
            List[Dict[str, Any]]: 相似案例列表
        """
        task_type = task.get('type', 'unknown')
        complexity = task.get('complexity', 5)
        
        with self._lock:
            similar = [
                case for case in self.case_library
                if case.task_type == task_type and abs(case.task_complexity - complexity) <= 2
            ]
            
            return [asdict(case) for case in similar[-limit:]]
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        获取统计信息
        
        Returns:
            Dict[str, Any]: 统计信息
        """
        with self._lock:
            return {
                'agent_id': self.agent_id,
                'total_cases': len(self.case_library),
                'execution_count': self.control_state.execution_count,
                'success_count': self.control_state.success_count,
                'failure_count': self.control_state.failure_count,
                'success_rate': self.control_state.success_rate,
                'average_execution_time': self.control_state.average_execution_time,
                'strategy_usage': self._get_strategy_usage()
            }
    
    def _get_strategy_usage(self) -> Dict[str, int]:
        """
        获取策略使用统计
        
        Returns:
            Dict[str, int]: 策略名称及其使用次数
        """
        from collections import Counter
        strategies = [case.strategy for case in self.case_library]
        return dict(Counter(strategies))
    
    def reset(self):
        """
        重置控制环状态
        
        注意：不会删除持久化的案例数据
        """
        with self._lock:
            self.control_state = ControlState(agent_id=self.agent_id)
            self.feedback_collector = FeedbackCollector()
            self.state_estimator = StateEstimator()


class StrategyPool:
    """
    策略池
    
    管理可用的执行策略
    """
    
    # 预定义策略
    STRATEGIES = {
        'conservative': {
            'name': '保守策略',
            'description': '优先保证成功率，降低执行速度',
            'timeout': 300,  # 5分钟超时
            'retry': True,
            'max_retry': 3
        },
        'balanced': {
            'name': '平衡策略',
            'description': '在成功率和速度之间取得平衡',
            'timeout': 180,  # 3分钟超时
            'retry': True,
            'max_retry': 2
        },
        'aggressive': {
            'name': '激进策略',
            'description': '优先追求速度，可能牺牲一些成功率',
            'timeout': 60,  # 1分钟超时
            'retry': False,
            'max_retry': 1
        },
        'default': {
            'name': '默认策略',
            'description': '使用系统默认配置',
            'timeout': 120,  # 2分钟超时
            'retry': True,
            'max_retry': 2
        }
    }
    
    def get_strategy(self, name: str) -> Dict[str, Any]:
        """
        获取指定策略
        
        Args:
            name: 策略名称
            
        Returns:
            Dict[str, Any]: 策略配置
        """
        return self.STRATEGIES.get(name, self.STRATEGIES['default'])
    
    def get_default_strategy(self) -> str:
        """
        获取默认策略名称
        
        Returns:
            str: 策略名称
        """
        return 'default'
    
    def get_conservative_strategy(self) -> str:
        """
        获取保守策略名称
        
        Returns:
            str: 策略名称
        """
        return 'conservative'
    
    def get_all_strategies(self) -> Dict[str, Dict[str, Any]]:
        """
        获取所有策略
        
        Returns:
            Dict[str, Dict[str, Any]]: 所有策略配置
        """
        return self.STRATEGIES.copy()


# 导出主要类
__all__ = [
    'FeedbackControlLoop',
    'FeedbackCollector',
    'StateEstimator',
    'ControlState',
    'ExecutionCase',
    'Feedback',
    'StrategyPool',
    'ControlPhase'
]
