#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cybernetics 增强桥接层

将 CyberneticsIntegration 接入主调度流程（trae_agent_dispatch_v2），
作为 dispatch 函数和 AgentLoopControllerV2 的增强装饰器。

核心功能：
1. 包装调度函数，注入 Guard 预验证和执行后审查
2. 将 Karpathy 原则违规信息传递给 GuardCoordinator
3. 统一策略选择（委托给 StrategyResolver）
4. 收集执行反馈并更新性能画像

修复的断裂点：
- 断裂点1: KarpathyPrincipleEnforcer 脱离执行管线 -> 通过桥接层接入
- 断裂点2: CyberneticsIntegration 未接入主调度 -> 桥接层作为接入点
- 断裂点9: WorkflowEngineV2 不使用 Cybernetics -> 通过桥接层注入
"""

import time
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable
from datetime import datetime
from dataclasses import dataclass, field

from cybernetics_integration import (
    CyberneticsIntegration,
    CyberneticsConfig,
    IntegrationMetrics
)
from karpathy_principle_enforcer import (
    KarpathyPrincipleEnforcer,
    PrincipleType,
    ViolationSeverity
)
from strategy_resolver import StrategyResolver

logger = logging.getLogger(__name__)


@dataclass
class BridgeExecutionResult:
    """
    桥接层执行结果

    包含原始调度结果和 Cybernetics 增强信息
    """
    success: bool
    task_id: str
    original_result: Any = None
    validation: Optional[Dict[str, Any]] = None
    strategy: str = ""
    karpathy_violations: List[Dict[str, Any]] = field(default_factory=list)
    execution_time: float = 0.0
    feedback_metrics: Optional[Dict[str, Any]] = None


class CyberneticsBridge:
    """
    Cybernetics 增强桥接层

    将 CyberneticsIntegration 接入主调度流程，
    作为 dispatch 函数和 AgentLoopControllerV2 的增强装饰器。

    使用方式：
    ```python
    bridge = CyberneticsBridge(project_root="/path/to/project")

    # 包装调度函数
    enhanced_dispatch = bridge.wrap_dispatch(original_dispatch)

    # 或直接使用增强执行
    result = bridge.enhanced_execute(task_dict, executor=my_executor)
    ```
    """

    def __init__(self, project_root: str = ".",
                 config: Optional[CyberneticsConfig] = None,
                 karpathy_enabled: bool = True):
        """
        初始化 Cybernetics 桥接层

        Args:
            project_root: 项目根目录
            config: Cybernetics 配置（默认启用所有增强）
            karpathy_enabled: 是否启用 Karpathy 原则检查
        """
        self.project_root = project_root
        self.karpathy_enabled = karpathy_enabled

        # 创建 Cybernetics 集成（启用层次化控制）
        if config is None:
            config = CyberneticsConfig(
                feedback_loop_enabled=True,
                fingerprint_enabled=True,
                guard_enabled=True,
                hierarchical_enabled=True,
                feedback_storage_path=str(Path(project_root) / ".cybernetics" / "feedback"),
                fingerprint_storage_path=str(Path(project_root) / ".cybernetics" / "fingerprints")
            )

        self.integration = CyberneticsIntegration(
            agent_id=f"bridge_{project_root}",
            config=config
        )

        # 初始化 Karpathy 原则执行器
        self.karpathy_enforcer: Optional[KarpathyPrincipleEnforcer] = None
        if karpathy_enabled:
            self.karpathy_enforcer = KarpathyPrincipleEnforcer(project_root)

        # 初始化统一策略选择器
        self.strategy_resolver = StrategyResolver(
            feedback_loop=self.integration.feedback_loop,
            fingerprint=self.integration.fingerprint
        )

        # 执行历史
        self._execution_history: List[BridgeExecutionResult] = []

    def wrap_dispatch(self, dispatch_fn: Callable) -> Callable:
        """
        包装调度函数，注入 Cybernetics 增强

        在原始调度函数前后添加：
        - 执行前：Guard 预验证 + Karpathy 原则检查
        - 执行后：反馈收集 + 性能画像更新 + 执行后审查

        Args:
            dispatch_fn: 原始调度函数，签名为 (agent_type, task, task_id, project_root, progress) -> bool

        Returns:
            Callable: 增强后的调度函数
        """
        def enhanced_dispatch(agent_type: str, task: str,
                              task_id: Optional[str], project_root: str,
                              progress: Dict) -> bool:
            start_time = time.time()

            # 构建任务字典
            task_dict = self._build_task_dict(agent_type, task, task_id)

            # 执行前验证（Guard + Karpathy）
            validation = self._pre_execute_check(task_dict)

            # 选择策略
            strategy = self.strategy_resolver.select_strategy(task_dict, validation)

            # 执行原始调度
            try:
                result = dispatch_fn(agent_type, task, task_id, project_root, progress)
            except Exception as e:
                result = False
                logger.error(f"调度执行异常: {e}")

            execution_time = time.time() - start_time

            # 执行后处理
            self._post_execute_process(
                task_dict=task_dict,
                success=result,
                strategy=strategy,
                validation=validation,
                execution_time=execution_time
            )

            return result

        return enhanced_dispatch

    def enhanced_execute(self, task: Dict[str, Any],
                         executor: Optional[Callable] = None) -> BridgeExecutionResult:
        """
        增强执行（完整 Cybernetics 闭环）

        完整流程：
        1. 执行前验证（Guard + Karpathy）
        2. 策略选择（StrategyResolver）
        3. 带反馈的任务执行（FeedbackControlLoop）
        4. 执行后审查（GuardCoordinator.post_execute_review）
        5. Karpathy 检查点验证

        Args:
            task: 任务字典
            executor: 可选的执行器函数

        Returns:
            BridgeExecutionResult: 增强执行结果
        """
        start_time = time.time()
        task_id = task.get('id', f"task_{int(start_time * 1000)}")

        # 阶段1: 执行前验证
        validation = self._pre_execute_check(task)

        # 阶段2: 策略选择
        strategy = self.strategy_resolver.select_strategy(task, validation)

        # 阶段3: 带反馈的任务执行
        if self.integration.feedback_loop:
            execution_result = self.integration.execute_with_feedback(task, executor)
        elif executor:
            execution_result = executor(task)
        else:
            execution_result = {'success': True, 'task_id': task_id}

        # 阶段4: 执行后审查
        if self.integration.guard:
            try:
                self.integration.guard.post_execute_review(
                    task_id,
                    execution_result if isinstance(execution_result, dict) else {'success': True}
                )
            except Exception as e:
                logger.warning(f"执行后审查异常: {e}")

        # 阶段5: Karpathy 检查点验证
        karpathy_violations = []
        if self.karpathy_enforcer:
            karpathy_violations = self._check_karpathy_principles(task)

        execution_time = time.time() - start_time

        # 构建结果
        result = BridgeExecutionResult(
            success=execution_result.get('success', True) if isinstance(execution_result, dict) else True,
            task_id=task_id,
            original_result=execution_result,
            validation=validation,
            strategy=strategy,
            karpathy_violations=karpathy_violations,
            execution_time=execution_time,
            feedback_metrics=self.integration.feedback_loop.get_statistics() if self.integration.feedback_loop else None
        )

        self._execution_history.append(result)

        return result

    def _build_task_dict(self, agent_type: str, task: str,
                         task_id: Optional[str]) -> Dict[str, Any]:
        """
        构建标准化的任务字典

        Args:
            agent_type: 智能体类型
            task: 任务描述
            task_id: 任务 ID

        Returns:
            Dict[str, Any]: 标准化任务字典
        """
        return {
            'id': task_id or f"task_{int(time.time() * 1000)}",
            'type': agent_type,
            'complexity': self._estimate_complexity(task),
            'description': task,
            'features': {}
        }

    def _estimate_complexity(self, task_description: str) -> int:
        """
        从任务描述估算复杂度

        Args:
            task_description: 任务描述

        Returns:
            int: 复杂度评分 (1-10)
        """
        desc = task_description.lower()
        high_keywords = ['架构', 'architecture', '系统', 'system', '分布式', 'distributed',
                         '微服务', 'microservice', '重构', 'refactor', '安全', 'security']
        medium_keywords = ['功能', 'feature', '模块', 'module', '接口', 'api',
                          '测试', 'test', '优化', 'optimize']
        low_keywords = ['修复', 'fix', 'bug', '更新', 'update', '配置', 'config']

        if any(kw in desc for kw in high_keywords):
            return 8
        elif any(kw in desc for kw in medium_keywords):
            return 5
        elif any(kw in desc for kw in low_keywords):
            return 3
        return 5

    def _pre_execute_check(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行前检查（Guard 预验证 + Karpathy 原则检查）

        Args:
            task: 任务字典

        Returns:
            Dict[str, Any]: 验证结果
        """
        validation = {"passed": True, "warnings": [], "karpathy_violations": []}

        # Guard 预验证
        if self.integration.guard:
            try:
                guard_result = self.integration.pre_execute_validation(task)
                validation["passed"] = guard_result.get("passed", True)
                validation["warnings"].extend(guard_result.get("warnings", []))
                validation["risk_level"] = guard_result.get("risk_level", "low")
                validation["compensations"] = guard_result.get("compensations", [])
            except Exception as e:
                logger.warning(f"Guard 预验证异常: {e}")

        # Karpathy 原则检查
        if self.karpathy_enforcer:
            violations = self._check_karpathy_principles(task)
            validation["karpathy_violations"] = violations

            # 严重违规影响验证结果
            critical_violations = [v for v in violations if v.get("severity") == "critical"]
            if critical_violations:
                validation["passed"] = False
                for v in critical_violations:
                    validation["warnings"].append({
                        "code": v.get("principle", "karpathy"),
                        "message": v.get("description", ""),
                        "severity": "critical",
                        "action": v.get("suggestion", "")
                    })

        return validation

    def _check_karpathy_principles(self, task: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        检查 Karpathy 原则违规

        对任务描述和项目代码进行原则检查

        Args:
            task: 任务字典

        Returns:
            List[Dict[str, Any]]: 违规列表
        """
        if not self.karpathy_enforcer:
            return []

        violations = []

        # 检查任务描述中的原则违规
        description = task.get('description', '')
        for principle, patterns in self.karpathy_enforcer.VIOLATION_PATTERNS.items():
            for pattern_def in patterns:
                import re
                if re.search(pattern_def["pattern"], description, re.IGNORECASE):
                    violations.append({
                        "principle": principle.value,
                        "severity": pattern_def["severity"].value,
                        "description": pattern_def["description"],
                        "suggestion": pattern_def["suggestion"],
                        "source": "task_description"
                    })

        return violations

    def _post_execute_process(self, task_dict: Dict[str, Any],
                              success: bool, strategy: str,
                              validation: Dict[str, Any],
                              execution_time: float):
        """
        执行后处理

        Args:
            task_dict: 任务字典
            success: 是否成功
            strategy: 使用的策略
            validation: 验证结果
            execution_time: 执行时间
        """
        # 更新性能画像
        if self.integration.fingerprint:
            try:
                self.integration.fingerprint.record(
                    task_type=task_dict.get('type', 'unknown'),
                    task_complexity=task_dict.get('complexity', 5),
                    success=success,
                    execution_time=execution_time,
                    strategy=strategy,
                    context_features={
                        'validation_passed': validation.get('passed', True),
                        'karpathy_violations': len(validation.get('karpathy_violations', []))
                    }
                )
            except Exception as e:
                logger.warning(f"性能画像更新异常: {e}")

        # 执行后审查
        if self.integration.guard:
            try:
                self.integration.guard.post_execute_review(
                    task_dict.get('id', 'unknown'),
                    {
                        'success': success,
                        'execution_time': execution_time,
                        'strategy': strategy
                    }
                )
            except Exception as e:
                logger.warning(f"执行后审查异常: {e}")

        # Karpathy 检查点自动验证
        if self.karpathy_enforcer and success:
            self._auto_verify_karpathy_checkpoints(task_dict)

    def _auto_verify_karpathy_checkpoints(self, task: Dict[str, Any]):
        """
        根据任务完成情况自动验证 Karpathy 检查点

        将工作流步骤与 Karpathy 验证检查点关联：
        - 需求分析 -> cp_think_1 (需求理解检查点)
        - 架构设计 -> cp_think_2 (方案评估检查点)
        - 简单性审查 -> cp_simple_1 (简单性检查点)
        - 代码修改 -> cp_surgical_1 (精准修改检查点)
        - 目标定义 -> cp_goal_1 (目标定义检查点)
        - 测试验证 -> cp_goal_2 (验证完成检查点)

        Args:
            task: 任务字典
        """
        if not self.karpathy_enforcer:
            return

        task_type = task.get('type', '').lower()
        description = task.get('description', '').lower()
        combined = f"{task_type} {description}"

        # 需求分析步骤 -> 验证"需求理解检查点"
        if any(kw in combined for kw in ['需求', 'requirement', '分析', 'analysis']):
            self.karpathy_enforcer.verify_checkpoint(
                "cp_think_1", verified=True,
                verified_by="cybernetics_bridge",
                notes=f"需求分析任务 {task.get('id')} 已完成"
            )

        # 架构设计步骤 -> 验证"方案评估检查点"
        if any(kw in combined for kw in ['架构', 'architecture', '设计', 'design']):
            self.karpathy_enforcer.verify_checkpoint(
                "cp_think_2", verified=True,
                verified_by="cybernetics_bridge",
                notes=f"架构设计任务 {task.get('id')} 已完成"
            )

        # 代码实现步骤 -> 验证"精准修改检查点"
        if any(kw in combined for kw in ['实现', 'implement', '编码', 'coding', '开发', 'develop']):
            self.karpathy_enforcer.verify_checkpoint(
                "cp_surgical_1", verified=True,
                verified_by="cybernetics_bridge",
                notes=f"代码实现任务 {task.get('id')} 已完成"
            )

        # 目标定义步骤 -> 验证"目标定义检查点"
        if any(kw in combined for kw in ['目标', 'goal', '计划', 'plan']):
            self.karpathy_enforcer.verify_checkpoint(
                "cp_goal_1", verified=True,
                verified_by="cybernetics_bridge",
                notes=f"目标定义任务 {task.get('id')} 已完成"
            )

        # 测试验证步骤 -> 验证"验证完成检查点"
        if any(kw in combined for kw in ['测试', 'test', '验证', 'verify']):
            self.karpathy_enforcer.verify_checkpoint(
                "cp_goal_2", verified=True,
                verified_by="cybernetics_bridge",
                notes=f"测试验证任务 {task.get('id')} 已完成"
            )

    def get_statistics(self) -> Dict[str, Any]:
        """
        获取桥接层统计信息

        Returns:
            Dict[str, Any]: 统计信息
        """
        stats = {
            'project_root': self.project_root,
            'karpathy_enabled': self.karpathy_enabled,
            'total_executions': len(self._execution_history),
            'successful_executions': sum(1 for r in self._execution_history if r.success),
            'failed_executions': sum(1 for r in self._execution_history if not r.success),
            'cybernetics': self.integration.get_statistics() if self.integration else {}
        }

        # Karpathy 检查点状态
        if self.karpathy_enforcer:
            stats['karpathy_checkpoints'] = self.karpathy_enforcer.get_checkpoint_status()

        return stats

    def generate_report(self) -> str:
        """
        生成桥接层执行报告

        Returns:
            str: Markdown 格式报告
        """
        stats = self.get_statistics()
        total = stats['total_executions']
        success_rate = (stats['successful_executions'] / total * 100) if total > 0 else 0

        report = f"""# Cybernetics 增强桥接层执行报告

> **项目路径**: {self.project_root}
> **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 执行统计

| 指标 | 数值 |
|------|------|
| 总执行次数 | {total} |
| 成功次数 | {stats['successful_executions']} |
| 失败次数 | {stats['failed_executions']} |
| 成功率 | {success_rate:.1f}% |
| Karpathy 检查 | {'启用' if self.karpathy_enabled else '禁用'} |

## Karpathy 检查点状态

"""
        if self.karpathy_enforcer:
            cp_status = self.karpathy_enforcer.get_checkpoint_status()
            for cp in cp_status.get('checkpoints', []):
                status = "✅ 已验证" if cp['verified'] else "⏳ 待验证"
                report += f"- {cp['description']}: {status}\n"
        else:
            report += "Karpathy 检查未启用\n"

        return report


# 导出主要类
__all__ = [
    'CyberneticsBridge',
    'BridgeExecutionResult'
]
