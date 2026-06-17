#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PatternExecutor Phase 4 集成测试

测试目标：
- _dispatch_subagent 接受 router / budget_guard（向后兼容）
- 3 个执行器透传 router / budget_guard
- PatternExecutorRegistry.create_default 接受新参数
- execute_workflow_step 在 registry 含 router/budget_guard 时正确传递
- 完整端到端：sandbox + router + budget_guard 协同
- 错误路径：router 异常 / budget 异常 / 沙箱异常

测试约定：
- 使用 unittest 框架
- 不修改任何 V2 文件
- 通过 monkey patch 模拟 dispatch_agent_v2
- mock router / budget_guard / sandbox

作者：trae-multi-agent 融合 Phase 4
创建日期：2026-06-03
"""

import sys
import time
import unittest
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

# 添加 scripts 目录到 sys.path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# 动态加载 pattern_executor（独立模块）
DYNAMIC_WORKFLOW_DIR = SCRIPTS_DIR / "dynamic_workflow"
if str(DYNAMIC_WORKFLOW_DIR) not in sys.path:
    sys.path.insert(0, str(DYNAMIC_WORKFLOW_DIR))

import pattern_executor  # noqa: E402
from pattern_executor import (  # noqa: E402
    AdversarialVerifyExecutor,
    ClassifierDispatchExecutor,
    DispatchError,
    ExecutionResult,
    ExecutionStatus,
    FanOutAggregateExecutor,
    PatternExecutorRegistry,
    _dispatch_subagent,
    _extract_task_feature,
    execute_pattern,
)
from workflow_step_adapter import (  # noqa: E402
    execute_workflow_step,
    make_pattern_step,
)

# 真实导入 model_router / token_budget_guard
from model_router import (  # noqa: E402
    ModelRouter,
    ModelTier,
    RoutingDecision,
    TaskFeature,
)
from token_budget_guard import (  # noqa: E402
    TokenBudgetGuard,
    TokenBudgetExceeded,
    BudgetDecision,
    BudgetEnforcementMode,
    BudgetRecommendation,
)


# ============================================================================
# Mock 工具
# ============================================================================

def _mock_dispatch_ok(*args, **kwargs):
    """Mock 永远成功的 dispatch_agent_v2"""
    return True


def _mock_dispatch_fail(*args, **kwargs):
    """Mock 永远失败的 dispatch_agent_v2"""
    return False


# ============================================================================
# 测试 1: 向后兼容
# ============================================================================

class TestPhase4BackwardCompat(unittest.TestCase):
    """Phase 4 向后兼容：所有新参数为 None 时行为等同 Phase 1"""

    def setUp(self):
        # patch dispatch_agent_v2
        self._dispatch_patcher = patch.object(
            pattern_executor, "dispatch_agent_v2", _mock_dispatch_ok
        )
        self._dispatch_patcher.start()

    def tearDown(self):
        self._dispatch_patcher.stop()

    def test_dispatch_subagent_no_optional_args(self):
        """_dispatch_subagent 不传任何可选参数 → 等同 Phase 1 行为"""
        result = _dispatch_subagent(
            agent_type="solo_coder",
            task={"description": "test"},
        )
        self.assertTrue(result)

    def test_classifier_dispatch_executor_with_no_phase4_args(self):
        """ClassifierDispatchExecutor 不传 router/budget_guard → Phase 1 行为"""
        executor = ClassifierDispatchExecutor()
        result = executor.execute(
            task={"description": "test", "task_type": "code_review"},
            parameters={"token_budget": 5000, "route_table": {"code_review": {"target_role": "test_expert"}}},
        )
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)

    def test_fanout_executor_with_no_phase4_args(self):
        """FanOutAggregateExecutor 不传 router/budget_guard → Phase 1 行为"""
        executor = FanOutAggregateExecutor()
        result = executor.execute(
            task={"description": "test", "chunks": ["a", "b", "c"]},
            parameters={"fanout_count": 3, "subagent_role": "solo_coder", "aggregator_role": "architect"},
        )
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)

    def test_adversarial_executor_with_no_phase4_args(self):
        """AdversarialVerifyExecutor 不传 router/budget_guard → Phase 1 行为"""
        executor = AdversarialVerifyExecutor()
        result = executor.execute(
            task={"description": "test", "evaluation_criteria": ["c1", "c2", "c3"]},
            parameters={"token_budget": 5000, "max_rounds": 1},
        )
        # 可能成功或失败，取决于 _estimate_pass_rate；只验证不抛异常
        self.assertIn(result.status, (ExecutionStatus.SUCCESS, ExecutionStatus.FAILURE))


# ============================================================================
# 测试 2: 路由集成
# ============================================================================

class TestPhase4RouterIntegration(unittest.TestCase):
    """Phase 4 ModelRouter 集成"""

    def setUp(self):
        self._dispatch_patcher = patch.object(
            pattern_executor, "dispatch_agent_v2", _mock_dispatch_ok
        )
        self._dispatch_patcher.start()
        # mock fingerprint
        self.mock_fp = MagicMock()
        self.mock_fp.total_executions = 0
        self.mock_fp.records = []
        # real router
        self.router = ModelRouter(fingerprint=self.mock_fp)

    def tearDown(self):
        self._dispatch_patcher.stop()

    def test_dispatch_subagent_with_router(self):
        """_dispatch_subagent 传 router → 触发路由决策"""
        result = _dispatch_subagent(
            agent_type="architect",
            task={
                "description": "test",
                "task_complexity": 8,  # 高复杂度
                "estimated_tokens": 1000,
            },
            router=self.router,
        )
        self.assertTrue(result)
        # 路由历史应有 1 条
        history = self.router.get_decision_history()
        self.assertEqual(len(history), 1)
        # 高复杂度 → opus
        self.assertEqual(history[0]["decision"]["selected_tier"], "opus")

    def test_routing_decision_recorded(self):
        """路由决策被记录到画像（mock fingerprint.record 被调用）"""
        _dispatch_subagent(
            agent_type="solo_coder",
            task={"description": "test", "task_complexity": 5, "estimated_tokens": 1000},
            router=self.router,
        )
        # record_decision 写画像
        self.mock_fp.record.assert_called()
        call_kwargs = self.mock_fp.record.call_args.kwargs
        self.assertIn("model_tier=", call_kwargs["strategy"])

    def test_routing_meta_written_to_task(self):
        """路由决策写入 task（最终被 _to_dispatch_str 序列化为字符串）"""
        captured_task = {}

        def _capture(*args, **kwargs):
            captured_task.update(kwargs)
            return True

        # patch dispatch_agent_v2 捕获参数
        with patch.object(pattern_executor, "dispatch_agent_v2", _capture):
            _dispatch_subagent(
                agent_type="architect",
                task={"description": "test", "task_complexity": 8, "estimated_tokens": 1000},
                router=self.router,
            )
        # task 字符串应包含路由元数据
        task_str = captured_task.get("task", "")
        self.assertIn("model_tier", task_str)
        self.assertIn("opus", task_str)
        self.assertIn("routing_reasoning", task_str)


# ============================================================================
# 测试 3: Token 预算集成
# ============================================================================

class TestPhase4BudgetGuardIntegration(unittest.TestCase):
    """Phase 4 TokenBudgetGuard 集成"""

    def setUp(self):
        self._dispatch_patcher = patch.object(
            pattern_executor, "dispatch_agent_v2", _mock_dispatch_ok
        )
        self._dispatch_patcher.start()
        self.mock_fp = MagicMock()
        self.mock_fp.total_executions = 0
        self.guard = TokenBudgetGuard(fingerprint=self.mock_fp)

    def tearDown(self):
        self._dispatch_patcher.stop()

    def test_dispatch_subagent_with_budget_guard(self):
        """_dispatch_subagent 传 budget_guard → 触发 Token 预检 + 后审"""
        result = _dispatch_subagent(
            agent_type="solo_coder",
            task={"description": "test", "token_budget": 100_000},
            budget_guard=self.guard,
        )
        self.assertTrue(result)
        # 后审写入画像
        self.mock_fp.record.assert_called()

    def test_budget_guard_rejects_oversized_task(self):
        """Token 预算拒绝超额任务"""
        with self.assertRaises(DispatchError):
            _dispatch_subagent(
                agent_type="solo_coder",
                task={
                    "description": "test",
                    "token_budget": 1000,
                    "estimated_tokens": 5000,  # 5x 总预算
                },
                budget_guard=self.guard,
            )

    def test_budget_guard_soft_warning(self):
        """Token 预算软警告（不拒绝）"""
        result = _dispatch_subagent(
            agent_type="solo_coder",
            task={
                "description": "test",
                "token_budget": 1000,
                "estimated_tokens": 850,  # 85% > 80% 软阈值
            },
            budget_guard=self.guard,
        )
        # 软警告不拒绝，正常执行
        self.assertTrue(result)


# ============================================================================
# 测试 4: 完整集成
# ============================================================================

class TestPhase4FullIntegration(unittest.TestCase):
    """Phase 4 sandbox + router + budget_guard 完整集成"""

    def setUp(self):
        self._dispatch_patcher = patch.object(
            pattern_executor, "dispatch_agent_v2", _mock_dispatch_ok
        )
        self._dispatch_patcher.start()

        # mock fingerprint
        self.mock_fp = MagicMock()
        self.mock_fp.total_executions = 0
        self.mock_fp.records = []

        # real router + budget guard
        self.router = ModelRouter(fingerprint=self.mock_fp)
        self.guard = TokenBudgetGuard(fingerprint=self.mock_fp)

    def tearDown(self):
        self._dispatch_patcher.stop()

    def test_dispatch_with_all_three(self):
        """_dispatch_subagent 同时传 router + budget_guard + sandbox（mock）"""
        mock_sandbox = MagicMock()
        mock_sandbox.spawn.return_value = "mock_sandbox_id"

        # mock SandboxResult
        from subagent_sandbox import SandboxResult
        mock_sandbox.execute.return_value = SandboxResult(
            sandbox_id="mock_sandbox_id",
            agent_id="test_agent",
            status="success",
            output={},
            execution_time_seconds=0.1,
        )

        result = _dispatch_subagent(
            agent_type="architect",
            task={
                "description": "test",
                "task_complexity": 8,
                "estimated_tokens": 1000,
                "token_budget": 100_000,
            },
            router=self.router,
            budget_guard=self.guard,
            sandbox=mock_sandbox,
        )
        self.assertTrue(result)
        # 路由历史应有 1 条
        self.assertEqual(len(self.router.get_decision_history()), 1)
        # 沙箱 spawn 被调用
        mock_sandbox.spawn.assert_called_once()
        # 沙箱 cleanup 被调用（finally 块）
        mock_sandbox.cleanup.assert_called_once_with("mock_sandbox_id")

    def test_classifier_dispatch_with_full_integration(self):
        """ClassifierDispatchExecutor 端到端集成"""
        mock_sandbox = MagicMock()
        from subagent_sandbox import SandboxResult
        mock_sandbox.spawn.return_value = "sb_id"
        mock_sandbox.execute.return_value = SandboxResult(
            sandbox_id="sb_id",
            agent_id="test_agent",
            status="success",
            output={},
            execution_time_seconds=0.1,
        )

        executor = ClassifierDispatchExecutor(
            fingerprint=self.mock_fp,
            sandbox=mock_sandbox,
            router=self.router,
            budget_guard=self.guard,
        )
        result = executor.execute(
            task={
                "description": "test",
                "task_type": "design",
                "task_complexity": 7,
                "token_budget": 50_000,
            },
            parameters={
                "token_budget": 50_000,
                "route_table": {"design": {"target_role": "architect"}},
            },
        )
        # 应成功（mock sandbox + mock dispatch 都成功）
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        # 路由被触发
        self.assertEqual(len(self.router.get_decision_history()), 1)
        # 沙箱被使用
        mock_sandbox.spawn.assert_called()

# ============================================================================
# 测试 5: 错误路径
# ============================================================================

class TestPhase4ErrorPaths(unittest.TestCase):
    """Phase 4 错误路径"""

    def setUp(self):
        self._dispatch_patcher = patch.object(
            pattern_executor, "dispatch_agent_v2", _mock_dispatch_ok
        )
        self._dispatch_patcher.start()

    def tearDown(self):
        self._dispatch_patcher.stop()

    def test_router_exception_does_not_break_dispatch(self):
        """router 抛异常 → 降级到默认（不中断主流程）"""
        # mock router 抛异常
        broken_router = MagicMock()
        broken_router.route.side_effect = RuntimeError("router broken")
        broken_router.get_decision_history.return_value = []

        # 应不抛异常（路由降级）
        result = _dispatch_subagent(
            agent_type="solo_coder",
            task={"description": "test", "task_complexity": 5, "estimated_tokens": 1000},
            router=broken_router,
        )
        # 降级到默认（sonnet），但 _safe_dispatch 成功
        self.assertTrue(result)

    def test_budget_guard_exception_does_not_break_dispatch(self):
        """budget_guard 抛异常 → 降级（不中断主流程）"""
        broken_guard = MagicMock()
        broken_guard.create_budget.side_effect = RuntimeError("guard broken")

        # 应不抛异常（预算降级）
        result = _dispatch_subagent(
            agent_type="solo_coder",
            task={"description": "test", "token_budget": 1000},
            budget_guard=broken_guard,
        )
        # 预算降级，正常执行
        self.assertTrue(result)

    def test_invalid_sandbox_type_raises(self):
        """sandbox 缺少必要方法 → 抛 DispatchError（duck typing 检查）"""
        with self.assertRaises(DispatchError):
            _dispatch_subagent(
                agent_type="solo_coder",
                task={"description": "test"},
                sandbox="not_a_sandbox",  # 字符串没有 spawn/execute/cleanup
            )


# ============================================================================
# 测试 6: Registry 创建
# ============================================================================

class TestPhase4RegistryCreation(unittest.TestCase):
    """Phase 4 PatternExecutorRegistry.create_default 接受新参数"""

    def setUp(self):
        self._dispatch_patcher = patch.object(
            pattern_executor, "dispatch_agent_v2", _mock_dispatch_ok
        )
        self._dispatch_patcher.start()
        self.mock_fp = MagicMock()
        self.mock_fp.total_executions = 0
        self.router = ModelRouter(fingerprint=self.mock_fp)
        self.guard = TokenBudgetGuard(fingerprint=self.mock_fp)
        self.sandbox = MagicMock()

    def tearDown(self):
        self._dispatch_patcher.stop()

    def test_create_default_with_phase4_args(self):
        """create_default 接受 router / budget_guard / sandbox"""
        registry = PatternExecutorRegistry.create_default(
            fingerprint=self.mock_fp,
            sandbox=self.sandbox,
            router=self.router,
            budget_guard=self.guard,
        )
        # 6 大执行器已注册（Phase 5 补齐）
        self.assertEqual(len(registry.list_ids()), 6)
        # dispatch 上下文
        ctx = registry.get_dispatch_context()
        self.assertEqual(ctx["router"], self.router)
        self.assertEqual(ctx["budget_guard"], self.guard)
        self.assertEqual(ctx["sandbox"], self.sandbox)

    def test_executor_receives_phase4_args(self):
        """执行器实例接受了 phase4 参数"""
        registry = PatternExecutorRegistry.create_default(
            fingerprint=self.mock_fp,
            sandbox=self.sandbox,
            router=self.router,
            budget_guard=self.guard,
        )
        executor = registry.get("classifier-dispatch")
        # 内部字段已被设置
        self.assertEqual(executor._sandbox, self.sandbox)
        self.assertEqual(executor._router, self.router)
        self.assertEqual(executor._budget_guard, self.guard)


# ============================================================================
# 测试 7: extract_task_feature 辅助函数
# ============================================================================

class TestExtractTaskFeature(unittest.TestCase):
    """_extract_task_feature 辅助函数测试"""

    def test_extract_minimal_dict(self):
        """最小 dict 提取"""
        feature = _extract_task_feature({"description": "test"})
        self.assertEqual(feature.task_complexity, 5)
        self.assertGreater(feature.estimated_tokens, 0)
        self.assertEqual(feature.role, None)
        self.assertEqual(feature.budget_remaining, 1.0)
        self.assertFalse(feature.is_critical)

    def test_extract_full_dict(self):
        """完整 dict 提取"""
        feature = _extract_task_feature({
            "description": "test",
            "task_complexity": 8,
            "estimated_tokens": 5000,
            "role": "architect",
            "deadline_ms": 10000,
            "quality_threshold": 0.9,
            "budget_remaining": 0.5,
            "is_critical": True,
            "task_type": "design",
        })
        self.assertEqual(feature.task_complexity, 8)
        self.assertEqual(feature.estimated_tokens, 5000)
        self.assertEqual(feature.role, "architect")
        self.assertEqual(feature.deadline_ms, 10000)
        self.assertTrue(feature.is_critical)
        self.assertEqual(feature.task_type, "design")

    def test_extract_string(self):
        """str 输入 → 默认特征"""
        feature = _extract_task_feature("test")
        self.assertEqual(feature.task_complexity, 5)
        self.assertEqual(feature.task_type, "general")

    def test_extract_estimated_default(self):
        """estimated_tokens 缺省时按 budget//4"""
        feature = _extract_task_feature({"token_budget": 10000})
        self.assertEqual(feature.estimated_tokens, 2500)


# ============================================================================
# 测试 8: execute_workflow_step + V2 适配器
# ============================================================================

class TestPhase4WorkflowStepAdapter(unittest.TestCase):
    """Phase 4 execute_workflow_step 集成"""

    def setUp(self):
        self._dispatch_patcher = patch.object(
            pattern_executor, "dispatch_agent_v2", _mock_dispatch_ok
        )
        self._dispatch_patcher.start()
        self.mock_fp = MagicMock()
        self.mock_fp.total_executions = 0
        self.router = ModelRouter(fingerprint=self.mock_fp)
        self.guard = TokenBudgetGuard(fingerprint=self.mock_fp)
        self.registry = PatternExecutorRegistry.create_default(
            fingerprint=self.mock_fp,
            router=self.router,
            budget_guard=self.guard,
        )

    def tearDown(self):
        self._dispatch_patcher.stop()

    def test_execute_workflow_step_with_router_guard(self):
        """execute_workflow_step 在 registry 含 router/guard 时正常执行"""
        step = make_pattern_step(
            step_id="s1",
            pattern_id="classifier-dispatch",
            description="test",
            pattern_parameters={
                "token_budget": 5000,
                "route_table": {"general": {"target_role": "solo_coder"}},
            },
        )
        # inputs 加 task_type
        step["inputs"]["task_type"] = "general"
        step["inputs"]["task_complexity"] = 5
        step["inputs"]["estimated_tokens"] = 1000

        result = execute_workflow_step(step, registry=self.registry)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        # 路由被触发
        self.assertEqual(len(self.router.get_decision_history()), 1)

    def test_execute_workflow_step_non_pattern_action(self):
        """非 pattern action → 返回 None（让 V2 走原生）"""
        step = {
            "step_id": "s2",
            "action": "some_native_action",  # 非 pattern: 开头
            "inputs": {},
        }
        result = execute_workflow_step(step, registry=self.registry)
        self.assertIsNone(result)


# ============================================================================
# 测试入口
# ============================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
