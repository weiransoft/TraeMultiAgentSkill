#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
性能画像单元测试

测试 PerformanceFingerprint 的核心功能：
- 画像记录更新
- 相似案例检索
- 冷启动场景
- 样本不足场景
"""

import unittest
import tempfile
import shutil
from pathlib import Path

# 导入待测试模块
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from performance_fingerprint import (
    PerformanceFingerprint,
    FailurePattern,
    SuccessPattern,
    ExecutionRecord,
    SimilarCase
)


class TestPerformanceFingerprint(unittest.TestCase):
    """性能画像测试类"""
    
    def setUp(self):
        """测试前准备"""
        self.temp_dir = tempfile.mkdtemp()
        self.agent_id = "test_agent"
        
        # 创建性能画像实例
        self.fingerprint = PerformanceFingerprint(
            agent_id=self.agent_id,
            storage_path=self.temp_dir
        )
    
    def tearDown(self):
        """测试后清理"""
        if Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)
    
    def test_record_success(self):
        """测试记录成功执行"""
        record = self.fingerprint.record(
            task_type='code_analysis',
            task_complexity=5,
            success=True,
            execution_time=10.0,
            strategy='balanced'
        )
        
        # 验证记录创建
        self.assertIsNotNone(record.record_id)
        self.assertTrue(record.success)
        self.assertEqual(record.task_type, 'code_analysis')
        self.assertEqual(record.task_complexity, 5)
        
        # 验证统计更新
        self.assertEqual(self.fingerprint.total_executions, 1)
        self.assertEqual(self.fingerprint.success_count, 1)
        self.assertEqual(self.fingerprint.failure_count, 0)
    
    def test_record_failure(self):
        """测试记录失败执行"""
        record = self.fingerprint.record(
            task_type='code_analysis',
            task_complexity=7,
            success=False,
            error_type='timeout',
            execution_time=30.0,
            strategy='aggressive'
        )
        
        # 验证记录创建
        self.assertFalse(record.success)
        self.assertEqual(record.error_type, 'timeout')
        
        # 验证统计更新
        self.assertEqual(self.fingerprint.failure_count, 1)
        
        # 验证失败模式更新
        self.assertIn('timeout', self.fingerprint.failure_patterns)
        pattern = self.fingerprint.failure_patterns['timeout']
        self.assertEqual(pattern.frequency, 1)
        self.assertEqual(pattern.failure_count, 1)
    
    def test_failure_pattern_creation(self):
        """测试失败模式创建"""
        # 记录多次相同错误
        for _ in range(3):
            self.fingerprint.record(
                task_type='test',
                task_complexity=5,
                success=False,
                error_type='memory_error'
            )
        
        # 验证模式创建
        self.assertIn('memory_error', self.fingerprint.failure_patterns)
        pattern = self.fingerprint.failure_patterns['memory_error']
        self.assertEqual(pattern.frequency, 3)
        self.assertEqual(pattern.failure_count, 3)
    
    def test_success_pattern_creation(self):
        """测试成功模式创建"""
        # 记录多次成功
        for _ in range(3):
            self.fingerprint.record(
                task_type='code_review',
                task_complexity=4,
                success=True,
                strategy='conservative'
            )
        
        # 验证模式创建
        pattern_key = 'success_code_review_conservative'
        self.assertIn(pattern_key, self.fingerprint.success_patterns)
        pattern = self.fingerprint.success_patterns[pattern_key]
        self.assertEqual(pattern.frequency, 3)
    
    def test_retrieve_similar_cases(self):
        """测试相似案例检索"""
        # 添加一些历史记录
        for i in range(15):
            self.fingerprint.record(
                task_type='code_analysis',
                task_complexity=5 + (i % 3),  # 复杂度在 5-7 之间
                success=(i % 3 != 0),  # 部分成功
                execution_time=10.0 + i
            )
        
        # 检索相似案例
        similar = self.fingerprint.retrieve_similar_cases(
            task_type='code_analysis',
            task_complexity=6,
            limit=5
        )
        
        # 验证检索结果
        self.assertLessEqual(len(similar), 5)
        for case in similar:
            self.assertEqual(case.task_type, 'code_analysis')
            self.assertLessEqual(abs(case.task_complexity - 6), 2)
    
    def test_retrieval_cold_start(self):
        """测试冷启动检索 - 无历史数据"""
        # 清空记录
        self.fingerprint.records = []
        self.fingerprint.total_executions = 0
        
        # 检索相似案例
        similar = self.fingerprint.retrieve_similar_cases(
            task_type='new_task',
            task_complexity=5,
            limit=5
        )
        
        # 应返回空列表（优雅降级）
        self.assertEqual(len(similar), 0)
    
    def test_insufficient_samples(self):
        """测试样本不足场景"""
        # 只添加少量样本
        for i in range(5):
            self.fingerprint.record(
                task_type='test',
                task_complexity=5,
                success=True
            )
        
        # 验证样本检查
        self.assertFalse(self.fingerprint.has_sufficient_samples())
        
        # 检索应返回空
        similar = self.fingerprint.retrieve_similar_cases(
            task_type='test',
            task_complexity=5,
            limit=5
        )
        self.assertEqual(len(similar), 0)
    
    def test_sufficient_samples(self):
        """测试样本充足场景"""
        # 添加足够样本
        for i in range(15):
            self.fingerprint.record(
                task_type='test',
                task_complexity=5,
                success=(i % 2 == 0)
            )
        
        # 验证样本检查
        self.assertTrue(self.fingerprint.has_sufficient_samples())
        
        # 检索应有结果
        similar = self.fingerprint.retrieve_similar_cases(
            task_type='test',
            task_complexity=5,
            limit=5
        )
        self.assertGreater(len(similar), 0)
    
    def test_get_failure_patterns(self):
        """测试获取失败模式"""
        # 记录多种错误
        for _ in range(5):
            self.fingerprint.record(
                task_type='test',
                task_complexity=5,
                success=False,
                error_type='timeout'
            )
        
        for _ in range(3):
            self.fingerprint.record(
                task_type='test',
                task_complexity=5,
                success=False,
                error_type='memory_error'
            )
        
        # 获取失败模式
        patterns = self.fingerprint.get_failure_patterns(min_frequency=2)
        
        # 验证
        self.assertEqual(len(patterns), 2)
        pattern_types = [p.error_type for p in patterns]
        self.assertIn('timeout', pattern_types)
        self.assertIn('memory_error', pattern_types)
    
    def test_get_success_patterns(self):
        """测试获取成功模式"""
        # 记录成功执行
        for _ in range(5):
            self.fingerprint.record(
                task_type='code_review',
                task_complexity=4,
                success=True,
                strategy='conservative'
            )
        
        patterns = self.fingerprint.get_success_patterns(min_frequency=3)
        
        # 验证
        self.assertEqual(len(patterns), 1)
        self.assertEqual(patterns[0].success_type, 'code_review')
    
    def test_context_outcome_mapping(self):
        """测试上下文结果映射"""
        # 记录不同上下文的任务
        for _ in range(3):
            self.fingerprint.record(
                task_type='test',
                task_complexity=4,
                success=True,
                execution_time=8.0,
                strategy='aggressive'
            )
        
        for _ in range(2):
            self.fingerprint.record(
                task_type='test',
                task_complexity=4,
                success=False,
                error_type='timeout',
                execution_time=15.0,
                strategy='aggressive'
            )
        
        # 获取上下文结果
        outcome = self.fingerprint.get_context_outcome(
            task_type='test',
            complexity=4,
            strategy='aggressive'
        )
        
        # 验证
        self.assertEqual(outcome['task_count'], 5)
        self.assertEqual(outcome['success_count'], 3)
        self.assertEqual(outcome['failure_count'], 2)
        self.assertGreater(outcome['total_time'], 0)
    
    def test_statistics(self):
        """测试统计信息"""
        # 记录一些任务
        for i in range(10):
            self.fingerprint.record(
                task_type='test',
                task_complexity=5,
                success=(i % 2 == 0),
                execution_time=10.0 + i
            )
        
        stats = self.fingerprint.get_statistics()
        
        # 验证统计
        self.assertEqual(stats['total_executions'], 10)
        self.assertEqual(stats['success_count'], 5)
        self.assertEqual(stats['failure_count'], 5)
        self.assertAlmostEqual(stats['success_rate'], 0.5)
        self.assertGreater(stats['average_execution_time'], 0)
    
    def test_export(self):
        """测试导出功能"""
        # 记录一些任务
        for i in range(5):
            self.fingerprint.record(
                task_type='test',
                task_complexity=5,
                success=(i % 2 == 0)
            )
        
        # 导出数据
        exported = self.fingerprint.export()
        
        # 验证导出包含必要字段
        self.assertEqual(exported['agent_id'], self.agent_id)
        self.assertIn('statistics', exported)
        self.assertIn('failure_patterns', exported)
        self.assertIn('success_patterns', exported)
        self.assertIn('records', exported)
    
    def test_record_with_context_features(self):
        """测试带上下文特征的记录"""
        context = {
            'has_documentation': True,
            'code_quality': 'high',
            'test_coverage': 0.8
        }
        
        record = self.fingerprint.record(
            task_type='code_analysis',
            task_complexity=5,
            success=True,
            execution_time=12.0,
            context_features=context
        )
        
        # 验证上下文特征保存
        self.assertEqual(record.context_features, context)
    
    def test_pattern_mitigation_generation(self):
        """测试缓解建议生成"""
        # 记录各种错误
        error_types = ['timeout', 'memory_error', 'syntax_error', 
                      'import_error', 'permission_error', 'network_error']
        
        for error_type in error_types:
            self.fingerprint.record(
                task_type='test',
                task_complexity=5,
                success=False,
                error_type=error_type
            )
        
        # 验证每个错误类型都有缓解建议
        for error_type in error_types:
            if error_type in self.fingerprint.failure_patterns:
                pattern = self.fingerprint.failure_patterns[error_type]
                self.assertIsNotNone(pattern.mitigation)
                self.assertGreater(len(pattern.mitigation), 0)
    
    def test_record_limit_enforcement(self):
        """测试记录数量限制"""
        # 添加大量记录
        for i in range(150):
            self.fingerprint.record(
                task_type='test',
                task_complexity=5,
                success=(i % 2 == 0)
            )
        
        # 验证限制（应该只保留最后500条）
        self.assertLessEqual(len(self.fingerprint.records), 500)
    
    def test_concurrent_access(self):
        """测试并发访问"""
        import threading
        
        def record_tasks(start, count):
            for i in range(count):
                self.fingerprint.record(
                    task_type='test',
                    task_complexity=5,
                    success=(i % 2 == 0)
                )
        
        # 创建多个线程同时记录
        threads = []
        for i in range(3):
            t = threading.Thread(target=record_tasks, args=(i * 10, 10))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        # 验证记录数量
        self.assertEqual(self.fingerprint.total_executions, 30)


class TestSimilarCase(unittest.TestCase):
    """相似案例测试类"""
    
    def test_similarity_score_calculation(self):
        """测试相似度评分计算"""
        case = SimilarCase(
            record_id='rec_001',
            agent_id='test_agent',
            task_type='code_analysis',
            task_complexity=5,
            success=True,
            error_type=None,
            execution_time=10.0,
            strategy='balanced',
            similarity_score=0.85,
            lessons_learned=['lesson1']
        )
        
        # 验证相似度
        self.assertEqual(case.similarity_score, 0.85)
        self.assertTrue(case.success)
    
    def test_lessons_learned(self):
        """测试经验教训"""
        case = SimilarCase(
            record_id='rec_001',
            agent_id='test_agent',
            task_type='test',
            task_complexity=5,
            success=False,
            error_type='timeout',
            execution_time=30.0,
            strategy='aggressive',
            similarity_score=0.9,
            lessons_learned=['使用aggressive策略导致超时', '建议下次使用conservative策略']
        )
        
        # 验证经验教训
        self.assertEqual(len(case.lessons_learned), 2)
        self.assertIn('超时', case.lessons_learned[0])


class TestFailurePattern(unittest.TestCase):
    """失败模式测试类"""
    
    def test_success_rate_calculation(self):
        """测试成功率计算"""
        pattern = FailurePattern(
            pattern_id='fp_001',
            error_type='timeout',
            trigger_conditions=[],
            description='超时错误',
            mitigation='增加超时时间',
            success_count=7,
            failure_count=3
        )
        
        # 验证成功率
        self.assertAlmostEqual(pattern.success_rate, 0.7)
        self.assertAlmostEqual(pattern.failure_rate, 0.3)
    
    def test_zero_executions(self):
        """测试零执行情况"""
        pattern = FailurePattern(
            pattern_id='fp_001',
            error_type='new_error',
            trigger_conditions=[],
            description='新错误',
            mitigation='待分析',
            success_count=0,
            failure_count=0
        )
        
        # 零执行时成功率应为1.0
        self.assertEqual(pattern.success_rate, 1.0)
        self.assertEqual(pattern.failure_rate, 0.0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
