#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
反馈控制环单元测试

测试 FeedbackControlLoop 的核心功能：
- 反馈环基本执行流程
- 冷启动场景
- 超时处理
- 策略选择
- 多实例隔离
"""

import unittest
import tempfile
import shutil
import time
from pathlib import Path
from typing import Dict, Any

# 导入待测试模块
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from feedback_control_loop import (
    FeedbackControlLoop,
    FeedbackCollector,
    StateEstimator,
    ControlState,
    ExecutionCase,
    Feedback,
    StrategyPool,
    ControlPhase
)


class TestFeedbackControlLoop(unittest.TestCase):
    """反馈控制环测试类"""
    
    def setUp(self):
        """测试前准备"""
        self.temp_dir = tempfile.mkdtemp()
        self.agent_id = "test_agent"
        
        # 创建反馈控制环实例
        self.loop = FeedbackControlLoop(
            agent_id=self.agent_id,
            storage_path=self.temp_dir
        )
    
    def tearDown(self):
        """测试后清理"""
        if Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)
    
    def test_feedback_loop_basic(self):
        """测试反馈环基本执行流程"""
        # 定义简单执行器
        def simple_executor(task):
            return {
                'success': True,
                'task_id': task.get('id'),
                'message': '任务完成'
            }
        
        self.loop.set_executor(simple_executor)
        
        # 执行任务
        task = {
            'id': 'task_001',
            'type': 'test',
            'complexity': 5
        }
        
        result = self.loop.execute_with_feedback(task)
        
        # 验证结果
        self.assertTrue(result['success'])
        self.assertEqual(result['task_id'], 'task_001')
        self.assertIn('execution_time', result)
        
        # 验证控制状态更新
        state = self.loop.get_control_state()
        self.assertEqual(state.current_phase, ControlPhase.COMPLETED)
        self.assertEqual(state.execution_count, 1)
        self.assertEqual(state.success_count, 1)
    
    def test_feedback_loop_cold_start(self):
        """测试冷启动场景 - 无历史数据"""
        # 清除案例库
        self.loop.case_library = []
        
        # 执行任务（使用默认执行器）
        task = {
            'id': 'task_cold_001',
            'type': 'cold_test',
            'complexity': 5
        }
        
        # 不应抛出异常
        try:
            result = self.loop.execute_with_feedback(task)
            # 默认执行器返回成功，所以检查是否有执行记录
            state = self.loop.get_control_state()
            self.assertIsNotNone(state)
            self.assertEqual(state.execution_count, 1)
            self.assertEqual(state.current_phase, ControlPhase.COMPLETED)
        except Exception as e:
            self.fail(f"冷启动不应抛出异常: {e}")
        
        # 验证状态
        state = self.loop.get_control_state()
        self.assertIsNotNone(state)
    
    def test_feedback_loop_timeout(self):
        """测试执行器超时处理"""
        # 定义超时执行器
        def timeout_executor(task):
            time.sleep(0.1)
            return {
                'success': False,
                'error_type': 'timeout',
                'error_message': '执行超时'
            }
        
        self.loop.set_executor(timeout_executor)
        
        # 执行长时间任务
        task = {
            'id': 'task_timeout_001',
            'type': 'timeout_test',
            'complexity': 8,
            'timeout': 0.05
        }
        
        result = self.loop.execute_with_feedback(task)
        
        # 验证错误处理
        self.assertFalse(result['success'])
        self.assertIn('error_type', result)
        
        # 验证反馈收集
        recent_feedback = self.loop.feedback_collector.get_recent_feedback(limit=1)
        self.assertEqual(len(recent_feedback), 1)
        self.assertEqual(recent_feedback[0].error_type, 'timeout')
    
    def test_strategy_selection(self):
        """测试基于案例的策略选择"""
        # 添加一些历史案例
        self.loop.case_library = [
            ExecutionCase(
                case_id='case_001',
                task_type='test',
                task_complexity=5,
                task_features={},
                strategy='conservative',
                execution_time=10.0,
                success=True
            ),
            ExecutionCase(
                case_id='case_002',
                task_type='test',
                task_complexity=5,
                task_features={},
                strategy='conservative',
                execution_time=12.0,
                success=True
            ),
            ExecutionCase(
                case_id='case_003',
                task_type='test',
                task_complexity=5,
                task_features={},
                strategy='aggressive',
                execution_time=8.0,
                success=False
            )
        ]
        
        # 选择策略
        task = {
            'id': 'task_select_001',
            'type': 'test',
            'complexity': 5
        }
        current_state = self.loop.state_estimator.estimate(task)
        strategy = self.loop._select_strategy(task, current_state)
        
        # 验证选择了成功的策略
        self.assertEqual(strategy, 'conservative')
    
    def test_fallback_to_default_strategy(self):
        """测试无相似案例时回退到默认策略"""
        # 清空案例库
        self.loop.case_library = []
        
        task = {
            'id': 'task_fallback_001',
            'type': 'unknown_type',
            'complexity': 5
        }
        
        current_state = self.loop.state_estimator.estimate(task)
        strategy = self.loop._select_strategy(task, current_state)
        
        # 应回退到默认策略
        self.assertEqual(strategy, 'default')
    
    def test_feedback_collection(self):
        """测试反馈收集"""
        collector = FeedbackCollector()
        
        # 收集反馈
        result = {
            'success': True,
            'execution_time': 5.0
        }
        
        feedback = collector.collect('task_001', result)
        
        # 验证反馈创建
        self.assertEqual(feedback.task_id, 'task_001')
        self.assertTrue(feedback.success)
        self.assertEqual(feedback.execution_time, 5.0)
        
        # 验证反馈历史
        history = collector.get_recent_feedback(limit=10)
        self.assertEqual(len(history), 1)
    
    def test_error_statistics(self):
        """测试错误统计"""
        collector = FeedbackCollector()
        
        # 收集包含错误的反馈
        for i in range(3):
            collector.collect(f'task_error_{i}', {
                'success': False,
                'error_type': 'timeout'
            })
        
        collector.collect('task_success', {
            'success': True
        })
        
        # 验证统计
        stats = collector.get_error_statistics()
        self.assertEqual(stats['timeout'], 3)
    
    def test_state_estimation(self):
        """测试状态估计"""
        estimator = StateEstimator()
        
        # 估计状态
        task = {
            'type': 'test',
            'complexity': 5
        }
        
        state = estimator.estimate(task)
        
        # 验证状态包含必要字段
        self.assertEqual(state['task_type'], 'test')
        self.assertEqual(state['complexity'], 5)
        self.assertIn('estimated_success_rate', state)
        self.assertIn('estimated_execution_time', state)
    
    def test_control_state_metrics(self):
        """测试控制状态指标计算"""
        state = ControlState(agent_id='test')
        
        # 添加一些执行记录
        state.execution_count = 10
        state.success_count = 8
        state.failure_count = 2
        state.total_execution_time = 100.0
        
        # 验证指标计算
        self.assertAlmostEqual(state.success_rate, 0.8)
        self.assertAlmostEqual(state.average_execution_time, 10.0)
    
    def test_feedback_loop_isolation(self):
        """测试多实例隔离"""
        # 创建两个独立的反馈控制环
        temp_dir1 = tempfile.mkdtemp()
        temp_dir2 = tempfile.mkdtemp()
        
        try:
            loop1 = FeedbackControlLoop('agent_1', temp_dir1)
            loop2 = FeedbackControlLoop('agent_2', temp_dir2)
            
            # 定义执行器
            def executor1(task):
                return {'success': True, 'agent': 'agent_1'}
            
            def executor2(task):
                return {'success': True, 'agent': 'agent_2'}
            
            loop1.set_executor(executor1)
            loop2.set_executor(executor2)
            
            # 执行任务
            loop1.execute_with_feedback({'id': 'task_1', 'type': 'test', 'complexity': 5})
            loop2.execute_with_feedback({'id': 'task_2', 'type': 'test', 'complexity': 5})
            
            # 验证状态隔离
            self.assertEqual(loop1.get_control_state().execution_count, 1)
            self.assertEqual(loop2.get_control_state().execution_count, 1)
            self.assertEqual(loop1.agent_id, 'agent_1')
            self.assertEqual(loop2.agent_id, 'agent_2')
            
            # 验证案例库隔离
            self.assertEqual(len(loop1.case_library), 1)
            self.assertEqual(len(loop2.case_library), 1)
            self.assertNotEqual(loop1.case_library[0].case_id, loop2.case_library[0].case_id)
            
        finally:
            shutil.rmtree(temp_dir1, ignore_errors=True)
            shutil.rmtree(temp_dir2, ignore_errors=True)
    
    def test_strategy_pool(self):
        """测试策略池"""
        pool = StrategyPool()
        
        # 获取策略
        conservative = pool.get_strategy('conservative')
        self.assertEqual(conservative['name'], '保守策略')
        self.assertEqual(conservative['timeout'], 300)
        
        # 获取默认策略
        default = pool.get_default_strategy()
        self.assertEqual(default, 'default')
        
        # 获取保守策略
        strat = pool.get_conservative_strategy()
        self.assertEqual(strat, 'conservative')
        
        # 获取所有策略
        all_strategies = pool.get_all_strategies()
        self.assertIn('conservative', all_strategies)
        self.assertIn('balanced', all_strategies)
        self.assertIn('aggressive', all_strategies)
    
    def test_reset_functionality(self):
        """测试重置功能"""
        # 执行一些任务
        self.loop.execute_with_feedback({
            'id': 'task_reset',
            'type': 'test',
            'complexity': 5
        })
        
        # 验证有执行记录
        self.assertGreater(self.loop.control_state.execution_count, 0)
        
        # 重置
        self.loop.reset()
        
        # 验证状态已重置
        state = self.loop.get_control_state()
        self.assertEqual(state.execution_count, 0)
        self.assertEqual(state.success_count, 0)
        self.assertEqual(state.current_phase, ControlPhase.PERCEPTION)
    
    def test_get_statistics(self):
        """测试统计信息获取"""
        # 执行一些任务
        for i in range(3):
            self.loop.execute_with_feedback({
                'id': f'task_stat_{i}',
                'type': 'test',
                'complexity': 5
            })
        
        # 获取统计
        stats = self.loop.get_statistics()
        
        # 验证统计包含必要字段
        self.assertEqual(stats['agent_id'], self.agent_id)
        self.assertEqual(stats['execution_count'], 3)
        self.assertIn('success_rate', stats)
        self.assertIn('strategy_usage', stats)


class TestFeedbackCollector(unittest.TestCase):
    """反馈收集器测试类"""
    
    def setUp(self):
        """测试前准备"""
        self.collector = FeedbackCollector()
    
    def test_collect_success(self):
        """测试收集成功反馈"""
        feedback = self.collector.collect('task_success', {
            'success': True,
            'execution_time': 10.0,
            'metrics': {'cpu': 50}
        })
        
        self.assertTrue(feedback.success)
        self.assertEqual(feedback.execution_time, 10.0)
        self.assertEqual(feedback.task_id, 'task_success')
    
    def test_collect_failure(self):
        """测试收集失败反馈"""
        feedback = self.collector.collect('task_failure', {
            'success': False,
            'error_type': 'timeout',
            'error_message': '执行超时',
            'execution_time': 30.0
        })
        
        self.assertFalse(feedback.success)
        self.assertEqual(feedback.error_type, 'timeout')
        self.assertEqual(feedback.task_id, 'task_failure')
    
    def test_recent_feedback_limit(self):
        """测试获取最近反馈的数量限制"""
        # 添加20条反馈
        for i in range(20):
            self.collector.collect(f'task_{i}', {
                'success': True,
                'execution_time': 5.0
            })
        
        # 获取最近10条
        recent = self.collector.get_recent_feedback(limit=10)
        self.assertEqual(len(recent), 10)
    
    def test_error_pattern_tracking(self):
        """测试错误模式跟踪"""
        # 添加不同类型的错误
        for _ in range(5):
            self.collector.collect('task_timeout', {
                'success': False,
                'error_type': 'timeout'
            })
        
        for _ in range(3):
            self.collector.collect('task_memory', {
                'success': False,
                'error_type': 'memory_error'
            })
        
        # 验证统计
        stats = self.collector.get_error_statistics()
        self.assertEqual(stats['timeout'], 5)
        self.assertEqual(stats['memory_error'], 3)


class TestStateEstimator(unittest.TestCase):
    """状态估计器测试类"""
    
    def setUp(self):
        """测试前准备"""
        self.estimator = StateEstimator()
    
    def test_estimate_basic(self):
        """测试基本状态估计"""
        task = {
            'type': 'code_analysis',
            'complexity': 7,
            'features': {'language': 'python'}
        }
        
        state = self.estimator.estimate(task)
        
        self.assertEqual(state['task_type'], 'code_analysis')
        self.assertEqual(state['complexity'], 7)
        self.assertIn('features', state)
        self.assertIn('timestamp', state)
    
    def test_estimate_with_context(self):
        """测试带上下文的估计"""
        task = {
            'type': 'test',
            'complexity': 5
        }
        
        context = {
            'user_level': 'expert',
            'has_documentation': True
        }
        
        state = self.estimator.estimate(task, context)
        
        self.assertIn('context', state)
        self.assertEqual(state['context']['user_level'], 'expert')
    
    def test_similar_state_finding(self):
        """测试相似状态查找"""
        # 添加历史状态
        for i in range(5):
            self.estimator.state_history.append({
                'task_type': 'test',
                'complexity': 5,
                'success': True,
                'execution_time': 10.0
            })
        
        task = {'type': 'test', 'complexity': 6}
        state = self.estimator.estimate(task)
        
        self.assertGreater(len(state['similar_states']), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
