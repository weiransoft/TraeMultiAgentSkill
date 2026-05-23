#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
守护协调器单元测试

测试 GuardCoordinator 的核心功能：
- 执行前预验证
- 多角色验证冲突处理
- 异常检测
- AI 增强能力（模拟）
"""

import unittest
from pathlib import Path

# 导入待测试模块
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from guard_coordinator import (
    GuardCoordinator,
    ValidationResult,
    ValidationWarning,
    CompensationStrategy,
    MonitorResult,
    ReviewResult,
    AnomalyPattern,
    RiskLevel
)


class MockAIProvider:
    """模拟 AI 提供者"""
    
    def generate(self, prompt: str) -> str:
        """模拟 AI 生成响应"""
        if "risk" in prompt.lower():
            return '{"risk_detected": false, "risks": [], "recommended_strategies": []}'
        elif "anomal" in prompt.lower():
            return '["检查系统状态"]'
        elif "lesson" in prompt.lower():
            return '{"lessons": ["从执行结果中学到的经验"], "suggestions": ["建议改进"]}'
        return '{}'


class TestGuardCoordinator(unittest.TestCase):
    """守护协调器测试类"""
    
    def setUp(self):
        """测试前准备"""
        self.coordinator = GuardCoordinator(agent_id='test_guard')
    
    def test_pre_validation_pass(self):
        """测试通过验证"""
        task = {
            'id': 'task_valid_001',
            'type': 'code_analysis',
            'complexity': 5,
            'timeout': 120,
            'description': '分析代码质量和安全性'
        }
        
        result = self.coordinator.pre_execute_validation(task)
        
        # 验证应通过
        self.assertTrue(result.passed)
        self.assertEqual(result.risk_level, RiskLevel.LOW)
        self.assertGreater(result.validation_time, 0)
    
    def test_pre_validation_complexity_error(self):
        """测试复杂度超出范围"""
        task = {
            'id': 'task_invalid_001',
            'type': 'test',
            'complexity': 15,  # 超出范围
            'timeout': 60,
            'description': '测试复杂度验证'
        }
        
        result = self.coordinator.pre_execute_validation(task)
        
        # 验证应失败
        self.assertFalse(result.passed)
        self.assertGreater(len(result.warnings), 0)
        
        # 检查是否有复杂度相关警告
        complexity_warning = any(
            '复杂度' in w.message or 'complexity' in w.message.lower() or 'range' in w.message.lower()
            for w in result.warnings
        )
        self.assertTrue(complexity_warning)
    
    def test_pre_validation_missing_fields(self):
        """测试缺少必填字段"""
        task = {
            'complexity': 5
            # 缺少 'type' 和 'id'
        }
        
        result = self.coordinator.pre_execute_validation(task)
        
        # 验证应失败
        self.assertFalse(result.passed)
        
        # 检查是否有必填字段警告
        field_warning = any(
            '必填' in w.message or 'required' in w.message.lower() or 'field' in w.message.lower()
            for w in result.warnings
        )
        self.assertTrue(field_warning)
    
    def test_pre_validation_high_complexity(self):
        """测试高复杂度任务"""
        task = {
            'id': 'task_high_001',
            'type': 'architecture_design',
            'complexity': 9,
            'timeout': 300
        }
        
        result = self.coordinator.pre_execute_validation(task)
        
        # 高复杂度应产生警告或建议
        self.assertGreaterEqual(len(result.warnings) + len(result.recommended_compensations), 0)
    
    def test_guard_validation_conflict(self):
        """测试多角色验证冲突处理"""
        # 设置多个补偿策略
        self.coordinator.add_compensation_strategy(CompensationStrategy(
            strategy_id='strat_test_1',
            error_type='test_error',
            strategy_type='feedforward',
            actions=['action1', 'action2'],
            priority=2,
            confidence=0.8
        ))
        
        task = {
            'id': 'task_conflict_001',
            'type': 'test',
            'complexity': 7
        }
        
        result = self.coordinator.pre_execute_validation(task)
        
        # 验证结果聚合正确
        self.assertIsNotNone(result.validation_details)
    
    def test_validation_with_ai_enhancement(self):
        """测试带 AI 增强的验证"""
        ai_provider = MockAIProvider()
        self.coordinator.set_ai_provider(ai_provider)
        
        task = {
            'id': 'task_ai_001',
            'type': 'code_analysis',
            'complexity': 6
        }
        
        result = self.coordinator.pre_execute_validation(task)
        
        # 验证 AI 评估被调用
        self.assertIn('ai_assessment', result.validation_details)
    
    def test_add_compensation_strategy(self):
        """测试添加补偿策略"""
        strategy = CompensationStrategy(
            strategy_id='strat_new',
            error_type='new_error',
            strategy_type='feedback',
            actions=['action1'],
            priority=1,
            confidence=0.9
        )
        
        self.coordinator.add_compensation_strategy(strategy)
        
        # 验证策略已添加
        self.assertIn('new_error', self.coordinator.compensation_strategies)
    
    def test_add_anomaly_pattern(self):
        """测试添加异常模式"""
        pattern = AnomalyPattern(
            pattern_id='pattern_custom',
            pattern_type='custom_anomaly',
            trigger_conditions=[{'type': 'error_rate', 'operator': '>', 'value': 0.2}],
            anomaly_indicators=['indicator1'],
            recommended_response='custom response',
            severity=RiskLevel.MEDIUM
        )
        
        self.coordinator.add_anomaly_pattern(pattern)
        
        # 验证模式已添加
        self.assertIn('pattern_custom', self.coordinator.anomaly_patterns)
    
    def test_monitor_execution_normal(self):
        """测试正常执行监控"""
        result = {
            'success': True,
            'execution_time': 10.0,
            'timeout': 300
        }
        
        monitor_result = self.coordinator.monitor_execution('exec_001', result)
        
        # 正常执行应返回 normal 状态
        self.assertEqual(monitor_result.status, 'normal')
        self.assertTrue(hasattr(monitor_result, 'metrics'))
    
    def test_monitor_execution_anomaly(self):
        """测试异常执行监控"""
        # 添加异常模式
        pattern = AnomalyPattern(
            pattern_id='pattern_high_error',
            pattern_type='high_error_rate',
            trigger_conditions=[{'type': 'error_rate', 'operator': '>', 'value': 0.1}],
            anomaly_indicators=['错误率过高'],
            recommended_response='暂停任务',
            severity=RiskLevel.HIGH
        )
        self.coordinator.add_anomaly_pattern(pattern)
        
        result = {
            'success': False,
            'execution_time': 5.0,
            'error_type': 'test_error',
            'error_count': 5,
            'total_count': 30
        }
        
        # 计算错误率
        if 'total_count' in result and result['total_count'] > 0:
            result['error_rate'] = result['error_count'] / result['total_count']
        
        monitor_result = self.coordinator.monitor_execution('exec_002', result)
        
        # 应检测到异常
        self.assertIn(monitor_result.status, ['normal', 'warning', 'anomaly', 'critical'])
    
    def test_monitor_with_ai_enhancement(self):
        """测试带 AI 增强的监控"""
        ai_provider = MockAIProvider()
        self.coordinator.set_ai_provider(ai_provider)
        
        result = {
            'success': False,
            'execution_time': 5.0,
            'error_type': 'timeout'
        }
        
        monitor_result = self.coordinator.monitor_execution('exec_003', result)
        
        # AI 增强的监控结果
        self.assertIsNotNone(monitor_result)
    
    def test_post_execute_review_success(self):
        """测试成功执行审查"""
        result = {
            'success': True,
            'execution_time': 15.0,
            'task_id': 'task_review_001'
        }
        
        review = self.coordinator.post_execute_review('exec_review_001', result)
        
        # 成功应标记为 SUCCESS
        self.assertEqual(review.outcome, 'SUCCESS')
        self.assertGreater(len(review.lessons_learned), 0)
    
    def test_post_execute_review_failure(self):
        """测试失败执行审查"""
        result = {
            'success': False,
            'execution_time': 30.0,
            'error_type': 'memory_error',
            'task_id': 'task_review_002'
        }
        
        review = self.coordinator.post_execute_review('exec_review_002', result)
        
        # 失败应标记为 FAILURE
        self.assertEqual(review.outcome, 'FAILURE')
        self.assertGreater(len(review.lessons_learned), 0)
    
    def test_post_execute_review_with_ai(self):
        """测试带 AI 的执行审查"""
        ai_provider = MockAIProvider()
        self.coordinator.set_ai_provider(ai_provider)
        
        result = {
            'success': False,
            'error_type': 'timeout'
        }
        
        review = self.coordinator.post_execute_review('exec_ai_review', result)
        
        # AI 增强的审查
        self.assertIsNotNone(review)
    
    def test_get_statistics(self):
        """测试获取统计信息"""
        # 执行一些验证
        for i in range(10):
            task = {
                'id': f'task_stat_{i}',
                'type': 'test',
                'complexity': 5
            }
            self.coordinator.pre_execute_validation(task)
        
        stats = self.coordinator.get_statistics()
        
        # 验证统计包含必要字段
        self.assertEqual(stats['agent_id'], 'test_guard')
        self.assertEqual(stats['total_validations'], 10)
        self.assertIn('risk_distribution', stats)
    
    def test_validation_history(self):
        """测试验证历史"""
        # 执行多次验证
        for i in range(5):
            task = {
                'id': f'task_history_{i}',
                'type': 'test',
                'complexity': 5
            }
            self.coordinator.pre_execute_validation(task)
        
        # 验证历史记录
        self.assertEqual(len(self.coordinator.validation_history), 5)
    
    def test_alternative_strategies_generation(self):
        """测试备选策略生成"""
        task = {
            'id': 'task_alt_001',
            'type': 'test',
            'complexity': 8
        }
        
        result = self.coordinator.pre_execute_validation(task)
        
        # 高复杂度应生成备选策略
        if result.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
            self.assertGreater(len(result.alternative_strategies), 0)


class TestValidationWarning(unittest.TestCase):
    """验证警告测试类"""
    
    def test_warning_creation(self):
        """测试警告创建"""
        warning = ValidationWarning(
            warning_code='warn_001',
            warning_type='test',
            message='测试警告',
            severity='warning',
            recommended_action='建议措施'
        )
        
        self.assertEqual(warning.warning_code, 'warn_001')
        self.assertEqual(warning.severity, 'warning')


class TestCompensationStrategy(unittest.TestCase):
    """补偿策略测试类"""
    
    def test_strategy_creation(self):
        """测试策略创建"""
        strategy = CompensationStrategy(
            strategy_id='strat_001',
            error_type='timeout',
            strategy_type='feedforward',
            actions=['action1', 'action2'],
            priority=3,
            confidence=0.85
        )
        
        self.assertEqual(strategy.strategy_id, 'strat_001')
        self.assertEqual(strategy.priority, 3)
        self.assertEqual(strategy.confidence, 0.85)


class TestRiskLevel(unittest.TestCase):
    """风险等级测试类"""
    
    def test_risk_levels(self):
        """测试风险等级枚举"""
        self.assertEqual(RiskLevel.LOW.value, 'low')
        self.assertEqual(RiskLevel.MEDIUM.value, 'medium')
        self.assertEqual(RiskLevel.HIGH.value, 'high')
        self.assertEqual(RiskLevel.CRITICAL.value, 'critical')


if __name__ == '__main__':
    unittest.main(verbosity=2)
