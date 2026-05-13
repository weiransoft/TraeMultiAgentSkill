#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
层次化控制器单元测试

测试 HierarchicalControlManager 的核心功能：
- 三层控制协调
- AI 增强的动态规划
- 完整任务执行流程
- 各层统计和状态管理
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

from hierarchical_control import (
    HierarchicalControlManager,
    StrategicController,
    TacticalController,
    ExecutionController,
    StrategicPlan,
    TacticalDecision,
    ExecutionMetrics,
    ControlLevel
)


class MockAIProvider:
    """模拟 AI 提供者"""
    
    def generate(self, prompt: str) -> str:
        """模拟 AI 生成响应"""
        if "strategic" in prompt.lower() or "规划" in prompt:
            return '{"strategy": "balanced", "roles": ["solo_coder"], "recommendations": ["建议使用平衡策略"], "risk_mitigation": "启用额外监控"}'
        elif "tactical" in prompt.lower() or "决策" in prompt:
            return '{"strategy": "conservative", "compensations": ["启用超时保护"], "confidence": 0.85, "reasoning": "基于历史数据分析"}'
        elif "execution" in prompt.lower() or "执行" in prompt:
            return '{"suggestions": ["优化执行顺序"], "optimizations": ["减少等待时间"]}'
        elif "assess" in prompt.lower() or "评估" in prompt:
            return '{"assessment": "执行正常", "lessons": ["成功经验"], "improvements": ["可进一步优化"]}'
        return '{}'


class TestStrategicController(unittest.TestCase):
    """战略控制器测试类"""
    
    def setUp(self):
        """测试前准备"""
        self.controller = StrategicController()
    
    def test_plan_basic(self):
        """测试基本规划"""
        task = {
            'id': 'task_plan_001',
            'type': 'code_analysis',
            'complexity': 6,
            'description': '分析代码架构'
        }
        
        plan = self.controller.plan(task)
        
        # 验证规划创建
        self.assertIsNotNone(plan.plan_id)
        self.assertEqual(plan.task_type, 'code_analysis')
        self.assertGreater(len(plan.recommended_roles), 0)
        self.assertIsNotNone(plan.execution_strategy)
        self.assertGreater(plan.estimated_time, 0)
        self.assertIn('risk_assessment', plan)
    
    def test_plan_high_complexity(self):
        """测试高复杂度任务规划"""
        task = {
            'id': 'task_complex_001',
            'type': 'architecture_design',
            'complexity': 9,
            'description': '设计微服务架构'
        }
        
        plan = self.controller.plan(task)
        
        # 高复杂度应选择保守策略
        self.assertEqual(plan.execution_strategy, 'conservative')
        self.assertIn('risks', plan.risk_assessment)
        self.assertGreater(len(plan.recommended_roles), 0)
    
    def test_plan_low_complexity(self):
        """测试低复杂度任务规划"""
        task = {
            'id': 'task_simple_001',
            'type': 'testing',
            'complexity': 2,
            'description': '简单测试任务'
        }
        
        plan = self.controller.plan(task)
        
        # 低复杂度可选择激进策略
        self.assertIn(plan.execution_strategy, ['aggressive', 'balanced'])
    
    def test_role_matching(self):
        """测试角色匹配"""
        roles = self.controller._match_roles('code_analysis', 5)
        
        # 应返回非空角色列表
        self.assertIsInstance(roles, list)
        self.assertGreater(len(roles), 0)
    
    def test_role_matching_architecture(self):
        """测试架构任务角色匹配"""
        roles = self.controller._match_roles('architecture_design', 8)
        
        # 架构任务应匹配架构师
        self.assertIn('architect', roles)
    
    def test_role_matching_testing(self):
        """测试测试任务角色匹配"""
        roles = self.controller._match_roles('unit_testing', 4)
        
        # 测试任务应匹配测试专家
        self.assertIn('test_expert', roles)
    
    def test_role_config_conservative(self):
        """测试保守角色配置"""
        config = self.controller._configure_roles(['architect'], {
            'complexity': 9
        })
        
        # 高复杂度配置应有更长超时和更多重试
        self.assertIn('architect', config)
        self.assertEqual(config['architect']['timeout'], 600)
        self.assertEqual(config['architect']['retry'], True)
        self.assertEqual(config['architect']['max_retry'], 3)
    
    def test_role_config_aggressive(self):
        """测试激进角色配置"""
        config = self.controller._configure_roles(['solo_coder'], {
            'complexity': 2
        })
        
        # 低复杂度配置应有更短超时和更少重试
        self.assertEqual(config['solo_coder']['timeout'], 180)
        self.assertEqual(config['solo_coder']['retry'], False)
    
    def test_time_estimation(self):
        """测试时间预估"""
        # 基础复杂度
        time1 = self.controller._estimate_time(5, 'implementation')
        self.assertGreater(time1, 0)
        
        # 架构任务时间更长
        time2 = self.controller._estimate_time(5, 'architecture')
        self.assertGreater(time2, time1)
    
    def test_risk_assessment(self):
        """测试风险评估"""
        # 高复杂度高风险
        risks1 = self.controller._assess_risks('implementation', 9)
        self.assertEqual(risks1['level'], 'high')
        
        # 低复杂度低风险
        risks2 = self.controller._assess_risks('testing', 2)
        self.assertIn(risks2['level'], ['low', 'medium'])
    
    def test_ai_enhanced_planning(self):
        """测试 AI 增强规划"""
        ai_provider = MockAIProvider()
        self.controller.set_ai_provider(ai_provider)
        
        task = {
            'id': 'task_ai_001',
            'type': 'code_review',
            'complexity': 6
        }
        
        base_plan = StrategicPlan(
            plan_id='plan_ai_001',
            task_type='code_review',
            recommended_roles=['architect'],
            role_config={},
            execution_strategy='balanced',
            estimated_time=120.0,
            risk_assessment={}
        )
        
        ai_plan = self.controller._ai_enhanced_planning(task, base_plan)
        
        # AI 应返回增强建议
        self.assertIsInstance(ai_plan, dict)
    
    def test_statistics(self):
        """测试统计信息"""
        # 执行一些规划
        for i in range(5):
            self.controller.plan({
                'id': f'task_stat_{i}',
                'type': 'test',
                'complexity': 5
            })
        
        stats = self.controller.get_statistics()
        
        # 验证统计
        self.assertEqual(stats['total_plans'], 5)
        self.assertIn('role_usage', stats)
        self.assertIn('strategy_usage', stats)


class TestTacticalController(unittest.TestCase):
    """战术控制器测试类"""
    
    def setUp(self):
        """测试前准备"""
        self.controller = TacticalController()
    
    def test_decide_basic(self):
        """测试基本决策"""
        context = {
            'task': {
                'id': 'task_tactic_001',
                'type': 'code_analysis',
                'complexity': 5
            },
            'guard_results': []
        }
        
        decision = self.controller.decide(context)
        
        # 验证决策创建
        self.assertIsNotNone(decision.decision_id)
        self.assertIsNotNone(decision.selected_strategy)
        self.assertIsInstance(decision.compensations, list)
    
    def test_decide_with_guard_validation(self):
        """测试带 Guard 验证的决策"""
        context = {
            'task': {
                'id': 'task_guard_001',
                'type': 'implementation',
                'complexity': 7
            },
            'guard_results': [{
                'passed': True,
                'warnings': []
            }]
        }
        
        decision = self.controller.decide(context)
        
        self.assertIsNotNone(decision.guard_validations)
    
    def test_decide_failed_validation(self):
        """测试验证失败的决策"""
        context = {
            'task': {
                'id': 'task_fail_001',
                'type': 'complex_task',
                'complexity': 9
            },
            'guard_results': [{
                'passed': False,
                'warnings': [{'message': '风险过高'}]
            }]
        }
        
        decision = self.controller.decide(context)
        
        # 验证失败应选择保守策略
        self.assertEqual(decision.selected_strategy, 'conservative')
    
    def test_select_strategy_conservative(self):
        """测试选择保守策略"""
        strategy = self.controller._select_strategy(
            {'complexity': 9},
            [{'passed': True}]
        )
        self.assertEqual(strategy, 'conservative')
    
    def test_select_strategy_aggressive(self):
        """测试选择激进策略"""
        strategy = self.controller._select_strategy(
            {'complexity': 2},
            [{'passed': True}]
        )
        self.assertEqual(strategy, 'aggressive')
    
    def test_compute_compensations(self):
        """测试计算补偿措施"""
        compensations = self.controller._compute_compensations(
            {'complexity': 9},
            [{'passed': True, 'recommended_compensations': ['措施1', '措施2']}]
        )
        
        self.assertGreater(len(compensations), 0)
        self.assertIn('措施1', compensations)
    
    def test_get_fallback_strategies(self):
        """测试获取备用策略"""
        fallbacks = self.controller._get_fallback_strategies(
            {'complexity': 5},
            [{'passed': True}]
        )
        
        # 应返回策略列表
        self.assertIsInstance(fallbacks, list)
        self.assertIn('conservative', fallbacks)
        self.assertIn('balanced', fallbacks)
    
    def test_ai_enhanced_decision(self):
        """测试 AI 增强决策"""
        ai_provider = MockAIProvider()
        self.controller.set_ai_provider(ai_provider)
        
        context = {
            'task': {
                'id': 'task_ai_001',
                'type': 'test',
                'complexity': 5
            },
            'guard_results': []
        }
        
        decision = self.controller.decide(context)
        
        # AI 增强的决策
        self.assertTrue(decision.ai_enhanced)
        self.assertGreater(decision.confidence, 0)
    
    def test_statistics(self):
        """测试统计信息"""
        # 执行一些决策
        for i in range(5):
            self.controller.decide({
                'task': {'id': f'task_{i}', 'type': 'test', 'complexity': 5},
                'guard_results': []
            })
        
        stats = self.controller.get_statistics()
        
        self.assertEqual(stats['total_decisions'], 5)
        self.assertIn('strategy_distribution', stats)


class TestExecutionController(unittest.TestCase):
    """执行控制器测试类"""
    
    def setUp(self):
        """测试前准备"""
        self.controller = ExecutionController()
    
    def test_execute_success(self):
        """测试成功执行"""
        def executor(task):
            return {'success': True, 'result': '完成'}
        
        metrics = self.controller.execute(
            task={'id': 'task_exec_001', 'type': 'test'},
            strategy='balanced',
            compensations=['补偿1'],
            executor=executor
        )
        
        # 验证执行成功
        self.assertTrue(metrics.success)
        self.assertGreater(metrics.duration, 0)
        self.assertEqual(metrics.strategy_used, 'balanced')
    
    def test_execute_failure(self):
        """测试失败执行"""
        def executor(task):
            return {'success': False, 'error_type': 'timeout'}
        
        metrics = self.controller.execute(
            task={'id': 'task_fail_001', 'type': 'test'},
            strategy='conservative',
            compensations=[],
            executor=executor
        )
        
        # 验证执行失败
        self.assertFalse(metrics.success)
        self.assertEqual(metrics.error_type, 'timeout')
    
    def test_execute_with_exception(self):
        """测试执行异常处理"""
        def failing_executor(task):
            raise ValueError("执行错误")
        
        metrics = self.controller.execute(
            task={'id': 'task_except_001', 'type': 'test'},
            strategy='balanced',
            compensations=[],
            executor=failing_executor
        )
        
        # 应捕获异常
        self.assertFalse(metrics.success)
        self.assertEqual(metrics.error_type, 'ValueError')
    
    def test_default_execution(self):
        """测试默认执行"""
        metrics = self.controller.execute(
            task={'id': 'task_default_001', 'type': 'test'},
            strategy='default',
            compensations=[],
            executor=None  # 不提供执行器
        )
        
        # 使用默认执行
        self.assertTrue(metrics.success)
    
    def test_ai_prepare_execution(self):
        """测试 AI 准备执行"""
        ai_provider = MockAIProvider()
        self.controller.set_ai_provider(ai_provider)
        
        metrics = self.controller.execute(
            task={'id': 'task_ai_001', 'type': 'test'},
            strategy='balanced',
            compensations=[],
            executor=None
        )
        
        # AI 准备应被调用
        self.assertIn('ai_preparation', metrics.metrics)
    
    def test_statistics(self):
        """测试统计信息"""
        # 执行一些任务
        for i in range(5):
            self.controller.execute(
                task={'id': f'task_{i}', 'type': 'test'},
                strategy='balanced',
                compensations=[],
                executor=lambda t: {'success': i % 2 == 0}
            )
        
        stats = self.controller.get_statistics()
        
        self.assertEqual(stats['total_executions'], 5)
        self.assertIn('success_count', stats)
        self.assertIn('average_duration', stats)


class TestHierarchicalControlManager(unittest.TestCase):
    """层次化控制管理器测试类"""
    
    def setUp(self):
        """测试前准备"""
        self.temp_dir = tempfile.mkdtemp()
        self.manager = HierarchicalControlManager()
    
    def tearDown(self):
        """测试后清理"""
        if Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)
    
    def test_execute_task_full_flow(self):
        """测试完整任务执行流程"""
        def executor(task):
            return {
                'success': True,
                'task_id': task.get('id'),
                'result': '执行完成'
            }
        
        task = {
            'id': 'task_full_001',
            'type': 'code_analysis',
            'complexity': 6,
            'description': '完整流程测试'
        }
        
        result = self.manager.execute_task(task, executor)
        
        # 验证完整流程
        self.assertTrue(result['success'])
        self.assertIn('strategic_plan', result)
        self.assertIn('tactical_decision', result)
        self.assertIn('execution_metrics', result)
        self.assertIn('control_record', result)
        
        # 验证战略层
        plan = result['strategic_plan']
        self.assertEqual(plan['task_type'], 'code_analysis')
        
        # 验证战术层
        decision = result['tactical_decision']
        self.assertIsNotNone(decision['selected_strategy'])
        
        # 验证执行层
        metrics = result['execution_metrics']
        self.assertTrue(metrics['success'])
    
    def test_execute_with_guard_coordinator(self):
        """测试带守护协调器的执行"""
        from guard_coordinator import GuardCoordinator
        
        # 设置守护协调器
        guard = GuardCoordinator('test_guard')
        self.manager.set_guard_coordinator(guard)
        
        def executor(task):
            return {'success': True}
        
        task = {
            'id': 'task_guard_001',
            'type': 'test',
            'complexity': 5
        }
        
        result = self.manager.execute_task(task, executor)
        
        # 验证守护协调被调用
        self.assertIn('control_record', result)
    
    def test_execute_with_feedback_loop(self):
        """测试带反馈控制环的执行"""
        from feedback_control_loop import FeedbackControlLoop
        
        # 设置反馈控制环
        feedback = FeedbackControlLoop('test_agent', self.temp_dir)
        self.manager.set_feedback_loop(feedback)
        
        def executor(task):
            return {'success': True}
        
        task = {
            'id': 'task_feedback_001',
            'type': 'test',
            'complexity': 5
        }
        
        result = self.manager.execute_task(task, executor)
        
        # 验证反馈被记录
        state = feedback.get_control_state()
        self.assertGreater(state.execution_count, 0)
    
    def test_execute_failure_flow(self):
        """测试失败执行流程"""
        def failing_executor(task):
            return {
                'success': False,
                'error_type': 'timeout',
                'message': '执行超时'
            }
        
        task = {
            'id': 'task_fail_001',
            'type': 'complex_task',
            'complexity': 9
        }
        
        result = self.manager.execute_task(task, failing_executor)
        
        # 验证失败流程
        self.assertFalse(result['success'])
        self.assertIn('control_record', result)
    
    def test_execute_with_ai_enhancement(self):
        """测试 AI 增强执行"""
        ai_provider = MockAIProvider()
        self.manager.ai_provider = ai_provider
        
        # 各层设置 AI 提供者
        self.manager.strategic_controller.set_ai_provider(ai_provider)
        self.manager.tactical_controller.set_ai_provider(ai_provider)
        self.manager.execution_controller.set_ai_provider(ai_provider)
        
        def executor(task):
            return {'success': True}
        
        task = {
            'id': 'task_ai_001',
            'type': 'code_review',
            'complexity': 6
        }
        
        result = self.manager.execute_task(task, executor)
        
        # 验证 AI 增强
        plan = result['strategic_plan']
        self.assertTrue(plan['ai_enhanced'])
    
    def test_control_history(self):
        """测试控制历史"""
        def executor(task):
            return {'success': True}
        
        # 执行多个任务
        for i in range(3):
            self.manager.execute_task({
                'id': f'task_history_{i}',
                'type': 'test',
                'complexity': 5
            }, executor)
        
        # 验证历史记录
        self.assertEqual(len(self.manager.control_history), 3)
    
    def test_all_statistics(self):
        """测试各层统计信息"""
        def executor(task):
            return {'success': True}
        
        # 执行任务
        for i in range(3):
            self.manager.execute_task({
                'id': f'task_stat_{i}',
                'type': 'test',
                'complexity': 5
            }, executor)
        
        stats = self.manager.get_all_statistics()
        
        # 验证各层统计
        self.assertIn('strategic', stats)
        self.assertIn('tactical', stats)
        self.assertIn('execution', stats)
        self.assertIn('hierarchical', stats)
        
        # 验证层次化统计
        self.assertEqual(stats['hierarchical']['total_control_records'], 3)
    
    def test_exception_handling(self):
        """测试异常处理"""
        def crashing_executor(task):
            raise RuntimeError("执行器崩溃")
        
        task = {
            'id': 'task_crash_001',
            'type': 'test',
            'complexity': 5
        }
        
        result = self.manager.execute_task(task, crashing_executor)
        
        # 应优雅处理异常
        self.assertFalse(result['success'])
        self.assertIn('error', result)
        self.assertIn('control_record', result)


class TestControlLevels(unittest.TestCase):
    """控制层级测试类"""
    
    def test_control_levels(self):
        """测试控制层级枚举"""
        self.assertEqual(ControlLevel.STRATEGIC.value, 'strategic')
        self.assertEqual(ControlLevel.TACTICAL.value, 'tactical')
        self.assertEqual(ControlLevel.EXECUTION.value, 'execution')


class TestDataClasses(unittest.TestCase):
    """数据类测试类"""
    
    def test_strategic_plan_creation(self):
        """测试战略规划创建"""
        plan = StrategicPlan(
            plan_id='plan_test',
            task_type='test',
            recommended_roles=['solo_coder'],
            role_config={},
            execution_strategy='balanced',
            estimated_time=120.0,
            risk_assessment={}
        )
        
        self.assertEqual(plan.plan_id, 'plan_test')
        self.assertFalse(plan.ai_enhanced)
    
    def test_tactical_decision_creation(self):
        """测试战术决策创建"""
        decision = TacticalDecision(
            decision_id='tac_test',
            context={},
            selected_strategy='balanced',
            compensations=[],
            guard_validations=[],
            fallback_strategies=['conservative']
        )
        
        self.assertEqual(decision.decision_id, 'tac_test')
        self.assertFalse(decision.ai_enhanced)
    
    def test_execution_metrics_creation(self):
        """测试执行指标创建"""
        import time
        start = time.time()
        
        metrics = ExecutionMetrics(
            execution_id='exec_test',
            start_time=start,
            strategy_used='balanced'
        )
        
        self.assertEqual(metrics.execution_id, 'exec_test')
        self.assertFalse(metrics.success)


if __name__ == '__main__':
    unittest.main(verbosity=2)
