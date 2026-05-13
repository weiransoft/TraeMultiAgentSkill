#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cybernetics 集成测试

测试各增强组件之间的集成以及与现有系统的集成：
1. CyberneticsIntegration 核心功能
2. 与 DualLayerContextManager 集成
3. 端到端反馈闭环
4. 守护协调与反馈控制协同
"""

import unittest
import tempfile
import shutil
from pathlib import Path

# 导入待测试模块
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from cybernetics_integration import (
    CyberneticsIntegration,
    CyberneticsConfig,
    IntegrationMetrics,
    create_enhanced_agent_loop
)


class TestCyberneticsIntegration(unittest.TestCase):
    """Cybernetics 集成测试类"""
    
    def setUp(self):
        """测试前准备"""
        self.temp_dir = tempfile.mkdtemp()
        self.agent_id = "test_integration_agent"
        
        # 创建测试配置
        self.config = CyberneticsConfig(
            feedback_loop_enabled=True,
            fingerprint_enabled=True,
            guard_enabled=True,
            feedback_storage_path=f"{self.temp_dir}/feedback",
            fingerprint_storage_path=f"{self.temp_dir}/fingerprints"
        )
        
        # 创建集成实例
        self.integration = CyberneticsIntegration(
            agent_id=self.agent_id,
            config=self.config
        )
    
    def tearDown(self):
        """测试后清理"""
        if Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)
    
    def test_integration_initialization(self):
        """测试集成初始化"""
        self.assertIsNotNone(self.integration.agent_id)
        self.assertIsNotNone(self.integration.config)
        self.assertIsNotNone(self.integration.metrics)
    
    def test_pre_execute_validation(self):
        """测试执行前验证"""
        task = {
            'id': 'task_valid_001',
            'type': 'code_analysis',
            'complexity': 5,
            'timeout': 120
        }
        
        result = self.integration.pre_execute_validation(task)
        
        self.assertIn('passed', result)
        self.assertIn('warnings', result)
    
    def test_execute_with_feedback_basic(self):
        """测试带反馈的基础执行"""
        def executor(task):
            return {
                'success': True,
                'task_id': task.get('id'),
                'result': '执行完成'
            }
        
        task = {
            'id': 'task_exec_001',
            'type': 'test_task',
            'complexity': 5
        }
        
        result = self.integration.execute_with_feedback(task, executor)
        
        self.assertTrue(result['success'])
        self.assertEqual(result['task_id'], 'task_exec_001')
        self.assertIn('strategy', result)
        self.assertIn('execution_time', result)
    
    def test_execute_with_feedback_failure(self):
        """测试执行失败场景"""
        def failing_executor(task):
            return {
                'success': False,
                'error_type': 'timeout',
                'error_message': '执行超时'
            }
        
        task = {
            'id': 'task_fail_001',
            'type': 'complex_task',
            'complexity': 9
        }
        
        result = self.integration.execute_with_feedback(task, failing_executor)
        
        self.assertFalse(result['success'])
        # 检查反馈循环是否记录了失败
        self.assertGreater(self.integration.metrics.feedback_loop_failure, 0)
    
    def test_retrieve_similar_cases(self):
        """测试相似案例检索"""
        # 先记录一些执行
        for i in range(15):
            self.integration.fingerprint.record(
                task_type='test_type',
                task_complexity=5 + (i % 3),
                success=(i % 3 != 0)
            )
        
        task = {
            'id': 'task_retrieve_001',
            'type': 'test_type',
            'complexity': 6
        }
        
        similar = self.integration.retrieve_similar_cases(task, limit=5)
        
        self.assertLessEqual(len(similar), 5)
    
    def test_retrieve_similar_cases_insufficient(self):
        """测试样本不足时的检索"""
        task = {
            'id': 'task_small_001',
            'type': 'small_test',
            'complexity': 5
        }
        
        # 只记录少量样本
        for i in range(3):
            self.integration.fingerprint.record(
                task_type='small_test',
                task_complexity=5,
                success=True
            )
        
        similar = self.integration.retrieve_similar_cases(task, limit=5)
        
        # 样本不足应返回空
        self.assertEqual(len(similar), 0)
    
    def test_get_recommendations(self):
        """测试获取执行建议"""
        # 先记录一些执行
        for i in range(10):
            self.integration.fingerprint.record(
                task_type='recommendation_test',
                task_complexity=5,
                success=(i % 2 == 0)
            )
        
        task = {
            'id': 'task_recommend_001',
            'type': 'recommendation_test',
            'complexity': 6
        }
        
        recommendations = self.integration.get_recommendations(task)
        
        self.assertIn('strategy', recommendations)
        self.assertIn('similar_cases', recommendations)
    
    def test_statistics(self):
        """测试统计信息"""
        # 执行一些任务
        for i in range(5):
            self.integration.execute_with_feedback({
                'id': f'task_stat_{i}',
                'type': 'test',
                'complexity': 5
            })
        
        stats = self.integration.get_statistics()
        
        self.assertEqual(stats['agent_id'], self.agent_id)
        self.assertIn('components', stats)
        self.assertIn('metrics', stats)
    
    def test_export_report(self):
        """测试导出报告"""
        # 执行一些任务
        for i in range(3):
            self.integration.execute_with_feedback({
                'id': f'task_report_{i}',
                'type': 'test',
                'complexity': 5
            })
        
        report = self.integration.export_report()
        
        self.assertIn('agent_id', report)
        self.assertIn('statistics', report)
        self.assertIn('performance_summary', report)
    
    def test_integration_metrics_update(self):
        """测试集成指标更新"""
        initial_calls = self.integration.metrics.feedback_loop_calls
        
        self.integration.execute_with_feedback({
            'id': 'task_metric_001',
            'type': 'test',
            'complexity': 5
        })
        
        self.assertEqual(self.integration.metrics.feedback_loop_calls, initial_calls + 1)
    
    def test_guard_validation_metrics(self):
        """测试守护验证指标"""
        initial_validations = self.integration.metrics.guard_validations
        
        self.integration.pre_execute_validation({
            'id': 'task_guard_metric_001',
            'type': 'test',
            'complexity': 5
        })
        
        self.assertEqual(self.integration.metrics.guard_validations, initial_validations + 1)


class TestCyberneticsConfig(unittest.TestCase):
    """Cybernetics 配置测试类"""
    
    def test_default_config(self):
        """测试默认配置"""
        config = CyberneticsConfig()
        
        self.assertTrue(config.feedback_loop_enabled)
        self.assertTrue(config.fingerprint_enabled)
        self.assertTrue(config.guard_enabled)
        self.assertFalse(config.hierarchical_enabled)
        self.assertFalse(config.ai_provider_enabled)
    
    def test_custom_config(self):
        """测试自定义配置"""
        config = CyberneticsConfig(
            feedback_loop_enabled=False,
            hierarchical_enabled=True,
            ai_provider_enabled=True,
            adaptation_threshold=0.5
        )
        
        self.assertFalse(config.feedback_loop_enabled)
        self.assertTrue(config.hierarchical_enabled)
        self.assertTrue(config.ai_provider_enabled)
        self.assertEqual(config.adaptation_threshold, 0.5)


class TestIntegrationMetrics(unittest.TestCase):
    """集成指标测试类"""
    
    def test_metrics_creation(self):
        """测试指标创建"""
        metrics = IntegrationMetrics(agent_id='test_metrics')
        
        self.assertEqual(metrics.agent_id, 'test_metrics')
        self.assertEqual(metrics.feedback_loop_calls, 0)
    
    def test_feedback_success_rate(self):
        """测试反馈成功率计算"""
        metrics = IntegrationMetrics(agent_id='test')
        metrics.feedback_loop_calls = 10
        metrics.feedback_loop_success = 8
        
        self.assertAlmostEqual(metrics.feedback_success_rate, 0.8)
    
    def test_guard_pass_rate(self):
        """测试守护通过率计算"""
        metrics = IntegrationMetrics(agent_id='test')
        metrics.guard_validations = 10
        metrics.guard_blocks = 2
        
        self.assertAlmostEqual(metrics.guard_pass_rate, 0.8)


class TestContextFingerprintIntegration(unittest.TestCase):
    """上下文与画像集成测试类"""
    
    def setUp(self):
        """测试前准备"""
        self.temp_dir = tempfile.mkdtemp()
    
    def tearDown(self):
        """测试后清理"""
        if Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)
    
    def test_create_enhanced_context_manager(self):
        """测试创建增强上下文管理器"""
        try:
            from context_fingerprint_integration import create_enhanced_context_manager
            
            context_mgr, fingerprint, integration = create_enhanced_context_manager(
                project_root=self.temp_dir,
                fingerprint_storage=f"{self.temp_dir}/fingerprints"
            )
            
            self.assertIsNotNone(context_mgr)
            self.assertIsNotNone(fingerprint)
            self.assertIsNotNone(integration)
            
        except ImportError as e:
            self.skipTest(f"模块导入失败: {e}")


class TestEndToEndFeedback(unittest.TestCase):
    """端到端反馈闭环测试类"""
    
    def setUp(self):
        """测试前准备"""
        self.temp_dir = tempfile.mkdtemp()
        self.agent_id = "test_e2e_agent"
        
        self.config = CyberneticsConfig(
            feedback_loop_enabled=True,
            fingerprint_enabled=True,
            guard_enabled=True,
            feedback_storage_path=f"{self.temp_dir}/feedback",
            fingerprint_storage_path=f"{self.temp_dir}/fingerprints"
        )
        
        self.integration = CyberneticsIntegration(
            agent_id=self.agent_id,
            config=self.config
        )
    
    def tearDown(self):
        """测试后清理"""
        if Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)
    
    def test_full_feedback_loop(self):
        """测试完整反馈闭环"""
        # 1. 执行前验证
        task = {
            'id': 'task_e2e_001',
            'type': 'code_analysis',
            'complexity': 6,
            'description': '分析代码架构'
        }
        
        validation = self.integration.pre_execute_validation(task)
        
        # 2. 执行任务
        def executor(task):
            return {
                'success': True,
                'task_id': task.get('id'),
                'result': '分析完成'
            }
        
        result = self.integration.execute_with_feedback(task, executor)
        
        # 3. 验证闭环
        self.assertTrue(result['success'])
        
        # 4. 检查反馈被记录
        stats = self.integration.get_statistics()
        self.assertGreater(stats['metrics']['feedback_loop_calls'], 0)
        
        # 5. 检查画像被更新
        self.assertGreater(stats['components']['fingerprint']['total_executions'], 0)
    
    def test_feedback_improves_strategy(self):
        """测试反馈改进策略"""
        # 执行多次任务
        for i in range(10):
            task = {
                'id': f'task_improve_{i}',
                'type': 'strategy_test',
                'complexity': 5
            }
            
            # 使用集成层的 execute_with_feedback
            result = self.integration.execute_with_feedback(task)
        
        # 检查反馈循环有记录
        stats = self.integration.get_statistics()
        self.assertGreater(stats['metrics']['feedback_loop_calls'], 0)
        self.assertGreater(stats['components']['feedback_loop']['execution_count'], 0)


class TestGuardFeedbackCoordination(unittest.TestCase):
    """守护与反馈协调测试类"""
    
    def setUp(self):
        """测试前准备"""
        self.temp_dir = tempfile.mkdtemp()
        
        self.config = CyberneticsConfig(
            feedback_loop_enabled=True,
            guard_enabled=True,
            feedback_storage_path=f"{self.temp_dir}/feedback"
        )
        
        self.integration = CyberneticsIntegration(
            agent_id="test_guard_feedback",
            config=self.config
        )
    
    def tearDown(self):
        """测试后清理"""
        if Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)
    
    def test_guard_warns_feedback_learns(self):
        """测试守护警告，反馈学习"""
        # 执行一个高风险任务
        task = {
            'id': 'task_high_risk_001',
            'type': 'complex_architecture',
            'complexity': 9,
            'description': '复杂架构设计'
        }
        
        # 1. 守护应发出警告
        validation = self.integration.pre_execute_validation(task)
        
        # 2. 执行应使用保守策略
        result = self.integration.execute_with_feedback(task)
        
        # 3. 验证使用了保守策略
        self.assertEqual(result['strategy'], 'conservative')
    
    def test_guard_blocks_low_quality(self):
        """测试守护阻止低质量任务"""
        # 任务缺少必填字段
        task = {
            'id': 'task_incomplete',
            'complexity': 5
            # 缺少 'type'
        }
        
        validation = self.integration.pre_execute_validation(task)
        
        # 验证应未通过
        self.assertFalse(validation.get('passed'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
