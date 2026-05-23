#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一策略选择器

消除 FeedbackControlLoop、CyberneticsIntegration、StrategicController、
TacticalController 中4处重复的策略选择逻辑，提供统一的策略选择接口。

核心功能：
1. 合并 FeedbackControlLoop 和 PerformanceFingerprint 的案例数据
2. 优先使用案例投票（FeedbackControlLoop 的案例投票机制更优）
3. 次优使用性能画像的相似案例检索
4. 最后回退到基于复杂度的默认阈值策略

修复的断裂点：
- 断裂点4: 策略选择在4个地方重复且不共享状态
- 断裂点7: PerformanceFingerprint 与 FeedbackControlLoop 功能重叠
"""

from typing import Dict, List, Any, Optional
from collections import Counter
import logging

logger = logging.getLogger(__name__)


class StrategyResolver:
    """
    统一策略选择器

    合并 FeedbackControlLoop 和 PerformanceFingerprint 的案例数据，
    提供统一的策略选择接口，替代4处重复的策略选择逻辑。

    策略选择优先级：
    1. 验证未通过 -> 保守策略
    2. FeedbackControlLoop 案例投票（有历史案例支撑）
    3. PerformanceFingerprint 相似案例检索
    4. 默认复杂度阈值策略

    使用方式：
    ```python
    resolver = StrategyResolver(
        feedback_loop=feedback_loop,
        fingerprint=fingerprint
    )
    strategy = resolver.select_strategy(task, validation)
    ```
    """

    def __init__(self,
                 feedback_loop=None,
                 fingerprint=None):
        """
        初始化统一策略选择器

        Args:
            feedback_loop: FeedbackControlLoop 实例（可选）
            fingerprint: PerformanceFingerprint 实例（可选）
        """
        self.feedback_loop = feedback_loop
        self.fingerprint = fingerprint

    def select_strategy(self, task: Dict[str, Any],
                        validation: Optional[Dict] = None) -> str:
        """
        统一策略选择

        按优先级依次尝试：
        1. 验证未通过 -> 保守策略
        2. FeedbackControlLoop 案例投票
        3. PerformanceFingerprint 相似案例检索
        4. 默认复杂度阈值策略

        Args:
            task: 任务字典，包含 type、complexity 等字段
            validation: 可选的验证结果字典

        Returns:
            str: 选择的策略名称（conservative/balanced/aggressive）
        """
        # 优先级1: 验证未通过 -> 保守策略
        if validation and not validation.get('passed', True):
            logger.info(f"策略选择: 验证未通过，使用保守策略")
            return 'conservative'

        # 优先级2: FeedbackControlLoop 案例投票
        strategy = self._select_from_feedback_loop(task)
        if strategy and strategy != 'default':
            logger.info(f"策略选择: 基于反馈控制环案例投票，选择 {strategy}")
            return strategy

        # 优先级3: PerformanceFingerprint 相似案例检索
        strategy = self._select_from_fingerprint(task)
        if strategy:
            logger.info(f"策略选择: 基于性能画像相似案例，选择 {strategy}")
            return strategy

        # 优先级4: 默认复杂度阈值策略
        strategy = self._select_by_complexity(task)
        logger.info(f"策略选择: 基于复杂度阈值，选择 {strategy}")
        return strategy

    def _select_from_feedback_loop(self, task: Dict[str, Any]) -> Optional[str]:
        """
        从 FeedbackControlLoop 的案例库中投票选择策略

        使用 FeedbackControlLoop 的案例投票机制，
        查找相似的成功案例并投票选择最常用的策略

        Args:
            task: 任务字典

        Returns:
            Optional[str]: 选择的策略，如果没有足够案例返回 None
        """
        if not self.feedback_loop:
            return None

        try:
            task_type = task.get('type', 'unknown')
            complexity = task.get('complexity', 5)

            # 获取相似案例
            similar_cases = self.feedback_loop.get_similar_cases(task, limit=10)

            if not similar_cases:
                return None

            # 从成功案例中投票
            successful_strategies = [
                case['strategy'] for case in similar_cases
                if case.get('success', False) and case.get('strategy')
            ]

            if successful_strategies:
                strategy_counts = Counter(successful_strategies)
                return strategy_counts.most_common(1)[0][0]

        except Exception as e:
            logger.warning(f"FeedbackControlLoop 策略选择异常: {e}")

        return None

    def _select_from_fingerprint(self, task: Dict[str, Any]) -> Optional[str]:
        """
        从 PerformanceFingerprint 的相似案例中检索策略

        使用性能画像的相似案例检索功能，
        找到历史上成功的相似任务使用的策略

        Args:
            task: 任务字典

        Returns:
            Optional[str]: 选择的策略，如果没有足够样本返回 None
        """
        if not self.fingerprint:
            return None

        try:
            if not self.fingerprint.has_sufficient_samples():
                return None

            similar = self.fingerprint.retrieve_similar_cases(
                task_type=task.get('type', 'unknown'),
                task_complexity=task.get('complexity', 5),
                limit=5
            )

            if not similar:
                return None

            # 从成功案例中选择
            successful = [s for s in similar if s.success]
            if successful:
                strategies = Counter(s.strategy for s in successful)
                return strategies.most_common(1)[0][0]

        except Exception as e:
            logger.warning(f"PerformanceFingerprint 策略选择异常: {e}")

        return None

    def _select_by_complexity(self, task: Dict[str, Any]) -> str:
        """
        基于复杂度的默认阈值策略

        Args:
            task: 任务字典

        Returns:
            str: 策略名称
        """
        complexity = task.get('complexity', 5)
        if complexity > 7:
            return 'conservative'
        elif complexity > 4:
            return 'balanced'
        return 'aggressive'

    def get_strategy_recommendation(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        获取策略推荐详情（包含各数据源的推荐结果）

        用于调试和可解释性，展示各数据源的推荐结果

        Args:
            task: 任务字典

        Returns:
            Dict[str, Any]: 策略推荐详情
        """
        recommendation = {
            'task_id': task.get('id'),
            'task_type': task.get('type'),
            'complexity': task.get('complexity', 5),
            'sources': {},
            'selected_strategy': None
        }

        # FeedbackControlLoop 推荐
        fb_strategy = self._select_from_feedback_loop(task)
        recommendation['sources']['feedback_loop'] = {
            'strategy': fb_strategy,
            'available': self.feedback_loop is not None,
            'case_count': len(self.feedback_loop.case_library) if self.feedback_loop else 0
        }

        # PerformanceFingerprint 推荐
        fp_strategy = self._select_from_fingerprint(task)
        recommendation['sources']['fingerprint'] = {
            'strategy': fp_strategy,
            'available': self.fingerprint is not None,
            'sufficient_samples': self.fingerprint.has_sufficient_samples() if self.fingerprint else False
        }

        # 默认阈值推荐
        default_strategy = self._select_by_complexity(task)
        recommendation['sources']['default'] = {
            'strategy': default_strategy
        }

        # 最终选择
        recommendation['selected_strategy'] = self.select_strategy(task)

        return recommendation


# 导出主要类
__all__ = ['StrategyResolver']
