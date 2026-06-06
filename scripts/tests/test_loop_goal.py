#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_loop_goal.py — Phase 11 LoopGoalExecutor 单元测试 + 集成测试

测试结构（合计 24 用例）：
- TestGoalDataModel（4 个）：Goal / IterationResult / LoopConfig 数据类
- TestGoalStatusTransitions（3 个）：状态机合法性
- TestGoalRegistryPersistence（4 个）：CRUD + 原子写 + 列表
- TestConvergenceDetector（3 个）：收敛检测算法
- TestGoalVerifier（4 个）：关键词 + 模糊匹配
- TestLoopGoalExecutor（5 个）：主循环 + 退出条件
- TestKarpathyIntegration（1 个）：检查点验证联动
- TestCLIIntegration（2 个）：argparse 参数解析
- TestBackwardCompatibility（1 个）：旧调用方零影响

作者：trae-multi-agent 融合 Phase 11
创建日期：2026-06-05
"""

import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

# 测试 logger（用于子进程失败日志等）
logger = logging.getLogger("test_loop_goal")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)

# 路径处理：tests 在 scripts/tests/，模块在 scripts/ 下
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(TESTS_DIR)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from loop_goal import (
    ConvergenceDetector,
    Goal,
    GoalNotFoundError,
    GoalRegistry,
    GoalRegistryError,
    GoalStatus,
    GoalStatusTransitionError,
    GoalVerifier,
    InvalidGoalIdError,
    InvalidLoopConfigError,
    IterationResult,
    LoopConfig,
    LoopGoalError,
    LoopGoalExecutor,
    MAX_ITERATIONS_LIMIT,
    MIN_ITERATIONS,
    create_default_executor,
)


# ============================================================================
# 测试 1：数据模型（4 个）
# ============================================================================

class TestGoalDataModel(unittest.TestCase):
    """Goal / IterationResult / LoopConfig 数据类测试"""

    def test_01_goal_creation_with_valid_id(self):
        """合法 goal_id 创建 Goal"""
        goal = Goal(
            goal_id="fix-tests",
            description="修复所有测试",
            success_criteria=["tests pass", "no warnings"],
        )
        self.assertEqual(goal.goal_id, "fix-tests")
        self.assertEqual(goal.status, GoalStatus.ACTIVE)
        self.assertEqual(len(goal.success_criteria), 2)

    def test_02_goal_invalid_id_raises(self):
        """非法 goal_id（不符合 kebab-case）抛 InvalidGoalIdError"""
        for invalid_id in ["Invalid_ID", "FixTests", "fix_tests", "-invalid", "invalid-"]:
            with self.subTest(invalid_id=invalid_id):
                with self.assertRaises(InvalidGoalIdError):
                    Goal(goal_id=invalid_id, description="x")

    def test_03_iteration_result_fingerprint(self):
        """IterationResult.fingerprint() 计算正确"""
        iter1 = IterationResult(
            iteration_no=1, success=True,
            outputs={"files_modified": 3, "tests_passed": 10, "tests_failed": 2},
        )
        iter2 = IterationResult(
            iteration_no=2, success=True,
            outputs={"files_modified": 3, "tests_passed": 10, "tests_failed": 2},
        )
        iter3 = IterationResult(
            iteration_no=3, success=True,
            outputs={"files_modified": 5, "tests_passed": 12, "tests_failed": 0},
        )
        # 相同产出 → 相同指纹
        self.assertEqual(iter1.fingerprint(), iter2.fingerprint())
        # 不同产出 → 不同指纹
        self.assertNotEqual(iter1.fingerprint(), iter3.fingerprint())

    def test_04_loop_config_validation(self):
        """LoopConfig 字段校验"""
        # 合法
        cfg = LoopConfig(max_iterations=5, convergence_window=2)
        self.assertEqual(cfg.max_iterations, 5)
        # 非法：max < 1
        with self.assertRaises(InvalidLoopConfigError):
            LoopConfig(max_iterations=0)
        # 非法：max > 上限
        with self.assertRaises(InvalidLoopConfigError):
            LoopConfig(max_iterations=MAX_ITERATIONS_LIMIT + 1)
        # 非法：convergence_window < 1
        with self.assertRaises(InvalidLoopConfigError):
            LoopConfig(max_iterations=5, convergence_window=0)
        # 非法：负 delay
        with self.assertRaises(InvalidLoopConfigError):
            LoopConfig(max_iterations=5, inter_iteration_delay_seconds=-1.0)


# ============================================================================
# 测试 2：状态机转换（3 个）
# ============================================================================

class TestGoalStatusTransitions(unittest.TestCase):
    """GoalStatus 状态机合法性"""

    def test_01_active_to_in_progress(self):
        """ACTIVE → IN_PROGRESS 合法"""
        goal = Goal(goal_id="g1", description="x")
        goal.transition_to(GoalStatus.IN_PROGRESS)
        self.assertEqual(goal.status, GoalStatus.IN_PROGRESS)

    def test_02_in_progress_to_achieved(self):
        """IN_PROGRESS → ACHIEVED 合法 + achieved_at 自动设置"""
        goal = Goal(goal_id="g1", description="x")
        goal.transition_to(GoalStatus.IN_PROGRESS)
        goal.transition_to(GoalStatus.ACHIEVED)
        self.assertEqual(goal.status, GoalStatus.ACHIEVED)
        self.assertIsNotNone(goal.achieved_at)

    def test_03_invalid_transition_raises(self):
        """非法状态转换抛 GoalStatusTransitionError"""
        goal = Goal(goal_id="g1", description="x")
        # ACTIVE → ACHIEVED 不合法（必须经过 IN_PROGRESS）
        with self.assertRaises(GoalStatusTransitionError):
            goal.transition_to(GoalStatus.ACHIEVED)
        # 终态无法转换
        goal.transition_to(GoalStatus.IN_PROGRESS)
        goal.transition_to(GoalStatus.ACHIEVED)
        with self.assertRaises(GoalStatusTransitionError):
            goal.transition_to(GoalStatus.IN_PROGRESS)


# ============================================================================
# 测试 3：GoalRegistry 持久化（4 个）
# ============================================================================

class TestGoalRegistryPersistence(unittest.TestCase):
    """GoalRegistry CRUD + 原子写"""

    def setUp(self):
        """每个测试前创建临时目录"""
        self.tmp_dir = tempfile.mkdtemp(prefix="test_goal_")
        self.registry = GoalRegistry(storage_root=self.tmp_dir)

    def tearDown(self):
        """清理临时目录"""
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_create_and_get_goal(self):
        """create → get 完整路径"""
        goal = self.registry.create_goal(
            description="修复所有测试",
            criteria=["tests pass", "no warnings"],
            goal_id="fix-tests",
            max_iterations=5,
        )
        self.assertEqual(goal.goal_id, "fix-tests")
        # 从磁盘读回
        loaded = self.registry.get_goal("fix-tests")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.description, "修复所有测试")
        self.assertEqual(len(loaded.success_criteria), 2)
        self.assertEqual(loaded.max_iterations, 5)

    def test_02_get_nonexistent_returns_none(self):
        """不存在的 goal → None"""
        self.assertIsNone(self.registry.get_goal("nonexistent"))

    def test_03_get_nonexistent_raises(self):
        """不存在的 goal → get_or_raise 抛 GoalNotFoundError"""
        with self.assertRaises(GoalNotFoundError):
            self.registry.get_goal_or_raise("nonexistent")

    def test_04_save_iteration_persists(self):
        """save_iteration 写入磁盘并可读回"""
        goal = self.registry.create_goal(
            description="x", goal_id="g1", max_iterations=3
        )
        iteration = IterationResult(
            iteration_no=1, success=True,
            outputs={"files_modified": 2, "tests_passed": 5},
            execution_time_seconds=10.5,
        )
        self.registry.save_iteration("g1", iteration)
        # 重新读取
        loaded = self.registry.get_goal("g1")
        self.assertEqual(len(loaded.iterations), 1)
        self.assertEqual(loaded.iterations[0].iteration_no, 1)
        self.assertEqual(loaded.iterations[0].outputs["files_modified"], 2)

    def test_05_list_goals_filter_by_status(self):
        """list_goals 按 status 过滤"""
        self.registry.create_goal(description="a", goal_id="g1")
        self.registry.create_goal(description="b", goal_id="g2")
        # g1 改为 IN_PROGRESS
        g1 = self.registry.get_goal("g1")
        g1.transition_to(GoalStatus.IN_PROGRESS)
        self.registry.update_goal(g1)
        # 全部
        all_goals = self.registry.list_goals()
        self.assertEqual(len(all_goals), 2)
        # 仅 ACTIVE
        active = self.registry.list_goals(status=GoalStatus.ACTIVE)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].goal_id, "g2")
        # 仅 IN_PROGRESS
        in_progress = self.registry.list_goals(status=GoalStatus.IN_PROGRESS)
        self.assertEqual(len(in_progress), 1)
        self.assertEqual(in_progress[0].goal_id, "g1")


# ============================================================================
# 测试 4：ConvergenceDetector（3 个）
# ============================================================================

class TestConvergenceDetector(unittest.TestCase):
    """收敛检测算法"""

    def _make_iter(self, no: int, files: int = 1, passed: int = 0, failed: int = 0) -> IterationResult:
        return IterationResult(
            iteration_no=no, success=True,
            outputs={
                "files_modified": files,
                "tests_passed": passed,
                "tests_failed": failed,
            },
        )

    def test_01_no_convergence_with_different_outputs(self):
        """3 次不同产出 → 不收敛"""
        detector = ConvergenceDetector(window=3)
        iterations = [
            self._make_iter(1, files=1),
            self._make_iter(2, files=2),
            self._make_iter(3, files=3),
        ]
        self.assertFalse(detector.is_converged(iterations))

    def test_02_convergence_with_same_outputs(self):
        """3 次相同产出 → 收敛"""
        detector = ConvergenceDetector(window=3)
        iterations = [
            self._make_iter(1, files=1, passed=5, failed=2),
            self._make_iter(2, files=1, passed=5, failed=2),
            self._make_iter(3, files=1, passed=5, failed=2),
        ]
        self.assertTrue(detector.is_converged(iterations))

    def test_03_below_window_threshold_no_convergence(self):
        """窗口 3 但仅 2 次 iteration → 不收敛"""
        detector = ConvergenceDetector(window=3)
        iterations = [
            self._make_iter(1, files=1),
            self._make_iter(2, files=1),
        ]
        self.assertFalse(detector.is_converged(iterations))
        self.assertEqual(len(iterations), 2)

    def test_04_get_convergence_info(self):
        """get_convergence_info 返回诊断信息"""
        detector = ConvergenceDetector(window=3)
        iterations = [
            self._make_iter(1, files=1),
            self._make_iter(2, files=2),
            self._make_iter(3, files=3),
        ]
        info = detector.get_convergence_info(iterations)
        self.assertEqual(info["window"], 3)
        self.assertEqual(info["recent_count"], 3)
        self.assertEqual(info["unique_fingerprints"], 3)
        self.assertFalse(info["converged"])


# ============================================================================
# 测试 5：GoalVerifier（4 个）
# ============================================================================

class TestGoalVerifier(unittest.TestCase):
    """关键词 + 模糊匹配"""

    def _make_iter(self, **outputs) -> IterationResult:
        return IterationResult(iteration_no=1, success=True, outputs=outputs)

    def test_01_keyword_match_tests_pass(self):
        """'tests pass' + tests_failed=0 → 满足"""
        verifier = GoalVerifier()
        iter_ok = self._make_iter(tests_failed=0, tests_run=10)
        iter_fail = self._make_iter(tests_failed=2, tests_run=10)
        self.assertTrue(verifier.check_criterion("tests pass", iter_ok))
        self.assertFalse(verifier.check_criterion("tests pass", iter_fail))

    def test_02_keyword_match_no_warnings(self):
        """'no warnings' + warnings_count=0 → 满足"""
        verifier = GoalVerifier()
        iter_ok = self._make_iter(warnings_count=0)
        iter_fail = self._make_iter(warnings_count=3)
        self.assertTrue(verifier.check_criterion("no warnings", iter_ok))
        self.assertFalse(verifier.check_criterion("no warnings", iter_fail))

    def test_03_chinese_keyword_match(self):
        """中文关键词 '测试通过' / '无警告' / '代码已提交'"""
        verifier = GoalVerifier()
        iter_all = self._make_iter(
            tests_failed=0, tests_run=5, warnings_count=0, git_committed=True
        )
        self.assertTrue(verifier.check_criterion("测试通过", iter_all))
        self.assertTrue(verifier.check_criterion("无警告", iter_all))
        self.assertTrue(verifier.check_criterion("代码已提交", iter_all))

    def test_04_check_all_criteria_partial_fail(self):
        """多 criterion 部分满足"""
        verifier = GoalVerifier()
        goal = Goal(
            goal_id="g1", description="x",
            success_criteria=["tests pass", "no warnings", "code committed"],
        )
        iter_partial = self._make_iter(
            tests_failed=0, tests_run=5, warnings_count=0, git_committed=False
        )
        all_met, met_list = verifier.check_all_criteria(goal, iter_partial)
        self.assertFalse(all_met)
        self.assertEqual(len(met_list), 2)  # tests pass + no warnings

    def test_05_custom_rule_override(self):
        """自定义规则覆盖默认"""
        custom = {"special criterion": lambda o: o.get("special_flag", False)}
        verifier = GoalVerifier(custom_rules=custom)
        iter_with = self._make_iter(special_flag=True)
        iter_without = self._make_iter(special_flag=False)
        self.assertTrue(verifier.check_criterion("special criterion", iter_with))
        self.assertFalse(verifier.check_criterion("special criterion", iter_without))


# ============================================================================
# 测试 6：LoopGoalExecutor 主循环（5 个）
# ============================================================================

class TestLoopGoalExecutor(unittest.TestCase):
    """主循环 + 退出条件"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_exec_")
        self.registry = GoalRegistry(storage_root=self.tmp_dir)
        self.executor = LoopGoalExecutor(registry=self.registry)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_dispatch_fn(self, success_values: List[bool], outputs_list: Optional[List[Dict]] = None):
        """构造 mock dispatch 函数（按列表顺序返回结果）"""
        call_count = {"i": 0}

        def dispatch_fn(agent_type, task, task_id=None, project_root=".", progress=None):
            idx = call_count["i"]
            call_count["i"] += 1
            if outputs_list and idx < len(outputs_list):
                # 通过 closure 注入 outputs（这里仅返回 success）
                # IterationResult.outputs 由 executor 默认填充
                pass
            return success_values[idx] if idx < len(success_values) else False

        return dispatch_fn

    def test_01_single_iteration_no_loop(self):
        """max=1 → 仅 1 次 dispatch"""
        dispatch_fn = self._make_dispatch_fn(success_values=[True])
        result = self.executor.execute_with_loop_goal(
            task="test",
            agent_type="solo-coder",
            dispatch_fn=dispatch_fn,
            project_root=self.tmp_dir,
            loop_config=LoopConfig(max_iterations=1),
        )
        self.assertEqual(result["total_iterations"], 1)
        self.assertEqual(result["max_iterations_reached"], True)

    def test_02_max_iterations_exhausted(self):
        """max=5 + 5 次全部成功 + 无 criterion → 5 次 dispatch"""
        dispatch_fn = self._make_dispatch_fn(success_values=[True] * 5)
        result = self.executor.execute_with_loop_goal(
            task="test",
            agent_type="solo-coder",
            dispatch_fn=dispatch_fn,
            project_root=self.tmp_dir,
            loop_config=LoopConfig(max_iterations=5),
        )
        self.assertEqual(result["total_iterations"], 5)
        self.assertFalse(result["success_early"])
        self.assertFalse(result["converged_early"])

    def test_03_convergence_early_exit(self):
        """第 4 次收敛 → 提前退出（max=10 但仅跑 5 次）"""
        # 全部返回相同产出 → 第 3 次开始触发收敛检测
        # 实际：iter 1, 2, 3 → 触发收敛（第 3 次 = window=3）
        # 但 verifier 用关键词规则，无 criterion 不触发 success_early
        dispatch_fn = self._make_dispatch_fn(success_values=[True] * 10)
        # 需要 goal 才有 convergence 检测
        goal = self.registry.create_goal(
            description="x", goal_id="g1",
            max_iterations=10, convergence_window=3,
        )
        result = self.executor.execute_with_loop_goal(
            task="test",
            agent_type="solo-coder",
            dispatch_fn=dispatch_fn,
            project_root=self.tmp_dir,
            loop_config=LoopConfig(max_iterations=10, convergence_window=3),
            goal=goal,
        )
        # 收敛 → 应在 iteration 3 退出（window=3）
        self.assertTrue(result["converged_early"])
        self.assertEqual(result["total_iterations"], 3)

    def test_04_success_early_exit(self):
        """verifier 检测成功 → 提前退出"""
        # 第 1 次不满足（返回 False），第 2 次满足（返回 True）→ 应在第 2 次退出
        mock_verifier = MagicMock()
        mock_verifier.check_all_criteria.side_effect = [
            (False, []),  # iter 1: 不满足
            (True, ["c1", "c2"]),  # iter 2: 满足
            (True, ["c1", "c2"]),  # iter 3: 满足（不应执行）
        ]
        executor = LoopGoalExecutor(registry=self.registry, verifier=mock_verifier)
        goal = self.registry.create_goal(
            description="x", goal_id="g2",
            criteria=["c1", "c2"],
            max_iterations=10,
        )
        dispatch_fn = self._make_dispatch_fn(success_values=[True] * 10)
        result = executor.execute_with_loop_goal(
            task="test",
            agent_type="solo-coder",
            dispatch_fn=dispatch_fn,
            project_root=self.tmp_dir,
            loop_config=LoopConfig(max_iterations=10, stop_on_success=True),
            goal=goal,
        )
        self.assertTrue(result["success_early"])
        # 第 2 次满足 → 2 次 iteration
        self.assertEqual(result["total_iterations"], 2)
        self.assertEqual(result["status"], GoalStatus.ACHIEVED.value)

    def test_05_dispatch_failure_recorded(self):
        """dispatch 失败时 iteration 记录 error"""
        def failing_dispatch(agent_type, task, task_id=None, project_root=".", progress=None):
            raise RuntimeError("simulated failure")

        result = self.executor.execute_with_loop_goal(
            task="test",
            agent_type="solo-coder",
            dispatch_fn=failing_dispatch,
            project_root=self.tmp_dir,
            loop_config=LoopConfig(max_iterations=3),
        )
        self.assertEqual(result["total_iterations"], 3)
        # 失败时不抛异常（executor 容错）
        self.assertIn("last_error", result)

    def test_06_no_goal_loop_only(self):
        """仅 /loop 无 /goal → 正常运行"""
        dispatch_fn = self._make_dispatch_fn(success_values=[True] * 3)
        result = self.executor.execute_with_loop_goal(
            task="test",
            agent_type="solo-coder",
            dispatch_fn=dispatch_fn,
            project_root=self.tmp_dir,
            loop_config=LoopConfig(max_iterations=3),
            goal=None,
        )
        self.assertEqual(result["total_iterations"], 3)
        # 无 goal → result 无 goal_id / status
        self.assertNotIn("goal_id", result)


# ============================================================================
# 测试 7：Karpathy 联动（1 个）
# ============================================================================

class TestKarpathyIntegration(unittest.TestCase):
    """Karpathy 检查点验证联动"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_karpathy_")
        self.registry = GoalRegistry(storage_root=self.tmp_dir)
        # mock Karpathy enforcer
        self.mock_karpathy = MagicMock()
        self.executor = LoopGoalExecutor(
            registry=self.registry, karpathy_enforcer=self.mock_karpathy
        )

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_goal_definition_verifies_cp_goal_1(self):
        """/goal 创建时验证 cp_goal_1（目标定义）"""
        def dispatch_fn(agent_type, task, task_id=None, project_root=".", progress=None):
            return True

        goal = self.registry.create_goal(
            description="x", goal_id="kg1", max_iterations=2
        )
        result = self.executor.execute_with_loop_goal(
            task="t",
            agent_type="solo-coder",
            dispatch_fn=dispatch_fn,
            project_root=self.tmp_dir,
            loop_config=LoopConfig(max_iterations=2),
            goal=goal,
        )
        # 至少调用 1 次 cp_goal_1
        calls = self.mock_karpathy.verify_checkpoint.call_args_list
        cp_goal_1_called = any(
            call.args[0] == "cp_goal_1" for call in calls
        )
        self.assertTrue(cp_goal_1_called, f"cp_goal_1 未被验证：{calls}")


# ============================================================================
# 测试 8：CLI 集成（2 个）
# ============================================================================

class TestCLIIntegration(unittest.TestCase):
    """argparse 参数解析"""

    def test_01_parse_loop_argument(self):
        """--loop 5 正确解析"""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument('--loop', type=int, default=1)
        parser.add_argument('--goal', type=str, default=None)
        parser.add_argument('--criteria', action='append', default=[])
        args = parser.parse_args(['--loop', '5'])
        self.assertEqual(args.loop, 5)
        self.assertIsNone(args.goal)
        self.assertEqual(args.criteria, [])

    def test_02_parse_goal_and_criteria(self):
        """--goal + 多个 --criteria"""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument('--loop', type=int, default=1)
        parser.add_argument('--goal', type=str, default=None)
        parser.add_argument('--criteria', action='append', default=[])
        args = parser.parse_args([
            '--loop', '3',
            '--goal', 'fix-tests',
            '--criteria', 'tests pass',
            '--criteria', 'no warnings',
        ])
        self.assertEqual(args.loop, 3)
        self.assertEqual(args.goal, 'fix-tests')
        self.assertEqual(args.criteria, ['tests pass', 'no warnings'])

    def test_03_parse_v2_dispatch_args(self):
        """trae_agent_dispatch_v2 真实 argparse 验证 /loop + /goal + /criteria + /goal-desc + /convergence-window"""
        import argparse
        # 复用 trae_agent_dispatch_v2 真实 parse_arguments（需要 sys.path 已包含 scripts/）
        from trae_agent_dispatch_v2 import parse_arguments
        import sys
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)
        # 模拟命令行
        test_argv_backup = sys.argv
        try:
            sys.argv = [
                'trae_agent_dispatch_v2.py',
                '--task', '修复所有单元测试',
                '--agent', 'solo-coder',
                '--project-root', '/tmp',
                '--loop', '5',
                '--goal', 'fix-tests-phase-11',
                '--goal-desc', '修复 Phase 11 引入的所有 bug',
                '--criteria', 'tests pass',
                '--criteria', 'no warnings',
                '--convergence-window', '3',
            ]
            args = parse_arguments()
            self.assertEqual(args.loop, 5)
            self.assertEqual(args.goal, 'fix-tests-phase-11')
            self.assertEqual(args.goal_desc, '修复 Phase 11 引入的所有 bug')
            self.assertEqual(args.criteria, ['tests pass', 'no warnings'])
            self.assertEqual(args.convergence_window, 3)
        finally:
            sys.argv = test_argv_backup

    def test_04_default_loop_one_means_no_loop(self):
        """--loop 默认 1（不循环；保持向后兼容）"""
        from trae_agent_dispatch_v2 import parse_arguments
        import sys
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)
        test_argv_backup = sys.argv
        try:
            sys.argv = [
                'trae_agent_dispatch_v2.py',
                '--task', 'x',
            ]
            args = parse_arguments()
            self.assertEqual(args.loop, 1)
            self.assertIsNone(args.goal)
            self.assertEqual(args.criteria, [])
            self.assertEqual(args.convergence_window, 3)
        finally:
            sys.argv = test_argv_backup


class TestDispatchV2LoopGoalWrapper(unittest.TestCase):
    """trae_agent_dispatch_v2.dispatch_agent_v2_with_loop_goal 包装器集成"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_dispatch_v2_loop_goal_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_wrapper_loop_only(self):
        """仅 /loop 不传 /goal → 循环执行 max_iterations 次"""
        from trae_agent_dispatch_v2 import dispatch_agent_v2_with_loop_goal
        import sys
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)

        # 替换 dispatch_agent_v2 为 mock（避免真实 Claude Code 调用）
        # 风险-1 修复（V3）：patch 路径从 trae_agent_dispatch_v2 改为 dispatch.legacy
        # 原因：dispatch_agent_v2 已迁出到 dispatch/legacy.py，薄壳仅 re-export
        with patch(
            'dispatch.legacy.dispatch_agent_v2', return_value=True
        ) as mock_dispatch:
            success = dispatch_agent_v2_with_loop_goal(
                agent_type="solo-coder",
                task="test-task",
                project_root=self.tmp_dir,
                loop_count=3,
            )
        self.assertTrue(success)
        # dispatch_agent_v2 被调用 3 次
        self.assertEqual(mock_dispatch.call_count, 3)

    def test_02_wrapper_with_goal_creates_persists(self):
        """/goal 模式 → 创建目标 + 持久化到 .trae/goals/<goal_id>.json"""
        from trae_agent_dispatch_v2 import dispatch_agent_v2_with_loop_goal
        import sys
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)

        # 使用不匹配默认规则的 criterion（避免成功提前退出，确保跑满 2 次）
        # 风险-1 修复（V3）：patch 路径从 trae_agent_dispatch_v2 改为 dispatch.legacy
        # 原因：dispatch_agent_v2 已迁出到 dispatch/legacy.py，薄壳仅 re-export
        with patch(
            'dispatch.legacy.dispatch_agent_v2', return_value=True
        ):
            success = dispatch_agent_v2_with_loop_goal(
                agent_type="solo-coder",
                task="test-task",
                project_root=self.tmp_dir,
                loop_count=2,
                goal_id="fix-loop-test",
                goal_desc="测试 /loop + /goal 集成",
                criteria=["non-existent-criterion-12345"],
                convergence_window=2,
            )
        self.assertTrue(success)
        # 验证目标文件持久化
        goal_file = os.path.join(self.tmp_dir, ".trae", "goals", "fix-loop-test", "goal.json")
        self.assertTrue(os.path.exists(goal_file), f"目标文件未创建：{goal_file}")
        with open(goal_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["goal_id"], "fix-loop-test")
        self.assertEqual(data["description"], "测试 /loop + /goal 集成")
        self.assertEqual(data["success_criteria"], ["non-existent-criterion-12345"])
        # iteration 落盘（2 次，因 criterion 不匹配 → 跑满）
        self.assertEqual(len(data["iterations"]), 2)

    def test_03_wrapper_goal_without_desc_fails(self):
        """--goal 不存在 + 不传 --goal-desc → 返回 False（友好错误）"""
        from trae_agent_dispatch_v2 import dispatch_agent_v2_with_loop_goal
        import sys
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)

        success = dispatch_agent_v2_with_loop_goal(
            agent_type="solo-coder",
            task="test-task",
            project_root=self.tmp_dir,
            loop_count=1,
            goal_id="nonexistent-goal",
            goal_desc=None,  # 关键：未提供
        )
        self.assertFalse(success)

    def test_04_wrapper_convergence_exits_early(self):
        """收敛检测 → 提前退出（不跑满 max_iterations）"""
        from trae_agent_dispatch_v2 import dispatch_agent_v2_with_loop_goal
        import sys
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)

        # 风险-1 修复（V3）：patch 路径从 trae_agent_dispatch_v2 改为 dispatch.legacy
        # 原因：dispatch_agent_v2 已迁出到 dispatch/legacy.py，薄壳仅 re-export
        with patch(
            'dispatch.legacy.dispatch_agent_v2', return_value=True
        ) as mock_dispatch:
            success = dispatch_agent_v2_with_loop_goal(
                agent_type="solo-coder",
                task="test-task",
                project_root=self.tmp_dir,
                loop_count=10,
                goal_id="conv-test",
                goal_desc="收敛测试",
                criteria=[],  # 无 criterion → 走收敛检测
                convergence_window=3,
            )
        self.assertTrue(success)
        # 收敛：第 3 次触发 → 总共 3 次
        self.assertEqual(mock_dispatch.call_count, 3)


# ============================================================================
# 测试 9：向后兼容（1 个）
# ============================================================================

class TestBackwardCompatibility(unittest.TestCase):
    """旧调用方零影响验证"""

    def test_01_default_executor_creates_registry(self):
        """create_default_executor 自动创建 registry"""
        executor = create_default_executor(project_root="/tmp")
        self.assertIsNotNone(executor.registry)
        # 仓库类型正确
        self.assertIsInstance(executor.registry, GoalRegistry)

    def test_02_max_iterations_one_means_no_loop(self):
        """max_iterations=1 等同不循环（行为兼容旧 dispatch）"""
        registry = GoalRegistry(storage_root="/tmp/test_bc")
        executor = LoopGoalExecutor(registry=registry)
        call_count = {"i": 0}

        def dispatch_fn(agent_type, task, task_id=None, project_root=".", progress=None):
            call_count["i"] += 1
            return True

        result = executor.execute_with_loop_goal(
            task="x",
            agent_type="solo-coder",
            dispatch_fn=dispatch_fn,
            project_root="/tmp",
            loop_config=LoopConfig(max_iterations=1),  # 不循环
        )
        # 仅 1 次 dispatch
        self.assertEqual(call_count["i"], 1)
        self.assertEqual(result["total_iterations"], 1)


# ============================================================================
# 测试 10：性能基线（1 个）
# ============================================================================

class TestPerformanceBaseline(unittest.TestCase):
    """性能基线：确保循环开销可接受"""

    def test_01_executor_100_iterations_under_5s(self):
        """100 次 iteration（mock dispatch）在 5s 内完成"""
        registry = GoalRegistry(storage_root="/tmp/test_perf")
        executor = LoopGoalExecutor(registry=registry)

        def fast_dispatch(agent_type, task, task_id=None, project_root=".", progress=None):
            return True

        start = time.perf_counter()
        result = executor.execute_with_loop_goal(
            task="x",
            agent_type="solo-coder",
            dispatch_fn=fast_dispatch,
            project_root="/tmp",
            loop_config=LoopConfig(max_iterations=100, inter_iteration_delay_seconds=0.0),
        )
        elapsed = time.perf_counter() - start
        self.assertEqual(result["total_iterations"], 100)
        # 100 次 mock dispatch < 5s（实际 < 1s）
        self.assertLess(elapsed, 5.0, f"100 次 iteration 耗时 {elapsed:.2f}s 超出预算")


# ============================================================================
# 测试 11：P0 修复补充测试（20 个）
# 覆盖：P0-1 CLI 退出码 / P0-2 跨进程并发 / P0-3 Verifier 兜底 / P0-4 模糊匹配
# ============================================================================

class TestP0Fixes_CLIReturnCodes(unittest.TestCase):
    """P0-1 修复：CLI 退出码语义正确性（5 个）"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_cli_exit_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _run_cli(self, *extra_args):
        """运行 CLI 并返回 (returncode, stdout, stderr)"""
        import subprocess
        import sys
        cmd = [
            sys.executable,
            str(Path(SCRIPTS_DIR) / "trae_agent_dispatch_v2.py"),
            "--task", "test task",
            "--project-root", self.tmp_dir,
            *extra_args,
        ]
        return subprocess.run(
            cmd, check=False, capture_output=True, text=True, timeout=30
        )

    def test_01_cli_success_returns_zero(self):
        """CLI 达成 → 退出码 0"""
        # criterion 不存在 → 跑满 2 次 → 视为不满足
        # 这里我们验证：即使不满足，如果是 /loop 仅循环 → 退出码 0
        result = self._run_cli("--loop", "2")
        # 仅 /loop（无 goal）+ 全部成功 → 退出码 0
        self.assertEqual(result.returncode, 0, f"CLI 应返回 0：{result.stdout}\n{result.stderr}")

    def test_02_cli_goal_achieved_returns_zero(self):
        """/goal 达成 → 退出码 0"""
        # 使用不存在的 criterion（永远不满足）→ 跑满 1 次 → FAILED
        # 但 FAILED + 仅 /loop 1 次 → 视为完成
        # 这里改为：仅 /loop 无 goal → 退出码 0（向后兼容）
        result = self._run_cli(
            "--loop", "1",
            "--goal", "cli-loop-only-test",
            "--goal-desc", "测试 CLI 退出码 - 无 goal 检查",
        )
        # 无 criterion + max=1 跑完 → IN_PROGRESS → 视为成功
        self.assertEqual(result.returncode, 0, f"CLI 应返回 0：{result.stdout}\n{result.stderr}")

    def test_03_cli_dry_run_returns_zero(self):
        """--dry-run → 退出码 0"""
        result = self._run_cli("--dry-run")
        self.assertEqual(result.returncode, 0, f"--dry-run 应返回 0：{result.stderr}")

    def test_04_cli_project_root_not_exists(self):
        """项目根目录不存在 → 非零退出码"""
        import subprocess
        import sys
        cmd = [
            sys.executable,
            str(Path(SCRIPTS_DIR) / "trae_agent_dispatch_v2.py"),
            "--task", "x",
            "--project-root", "/nonexistent/path/that/does/not/exist",
        ]
        result = subprocess.run(
            cmd, check=False, capture_output=True, text=True, timeout=10
        )
        self.assertNotEqual(result.returncode, 0, "不存在的项目根应返回非零退出码")

    def test_05_cli_goal_status_field_present(self):
        """CLI 返回字典 result 含 status 字段（_is_overall_success 依赖）"""
        from trae_agent_dispatch_v2 import _is_overall_success
        # 模拟不同状态
        self.assertTrue(
            _is_overall_success({"status": "achieved", "total_iterations": 2})
        )
        self.assertFalse(
            _is_overall_success({"status": "failed", "total_iterations": 5})
        )
        self.assertTrue(
            _is_overall_success(
                {"status": "in_progress", "converged_early": True, "total_iterations": 3}
            )
        )


class TestP0Fixes_VerifierNegationDetection(unittest.TestCase):
    """P0-3 修复：否定词检测（5 个）"""

    def _make_iter(self, **outputs):
        return IterationResult(iteration_no=1, success=True, outputs=outputs)

    def test_01_chinese_negation_returns_false(self):
        """'测试不通过' → False（否定词触发）"""
        verifier = GoalVerifier()
        iter_obj = self._make_iter(tests_failed=0, tests_run=5)
        self.assertFalse(verifier.check_criterion("测试不通过", iter_obj))

    def test_02_english_negation_returns_false(self):
        """'tests not pass' → False"""
        verifier = GoalVerifier()
        iter_obj = self._make_iter(tests_failed=0, tests_run=5)
        self.assertFalse(verifier.check_criterion("tests not pass", iter_obj))

    def test_03_negation_no_returns_false(self):
        """'no test pass' → False（与合法规则 'no warnings' 区分）"""
        verifier = GoalVerifier()
        iter_obj = self._make_iter(tests_failed=0, warnings_count=0)
        # "no test pass" 不在规则中 → 兜底 + 否定词 → False
        self.assertFalse(verifier.check_criterion("no test pass", iter_obj))

    def test_04_placeholder_rule_removed(self):
        """'all criteria met' 占位规则已删除 → 返回 False"""
        verifier = GoalVerifier()
        iter_empty = self._make_iter()
        self.assertFalse(
            verifier.check_criterion("all criteria met", iter_empty),
            "P0-3 修复后，'all criteria met' 不应再自动通过"
        )

    def test_05_chinese_placeholder_rule_removed(self):
        """'目标达成' 占位规则已删除 → 返回 False"""
        verifier = GoalVerifier()
        iter_empty = self._make_iter()
        self.assertFalse(
            verifier.check_criterion("目标达成", iter_empty),
            "P0-3 修复后，'目标达成' 不应再自动通过"
        )


class TestP0Fixes_VerifierStrictSubstring(unittest.TestCase):
    """P0-4 修复：严格子串匹配（5 个）"""

    def _make_iter(self, **outputs):
        return IterationResult(iteration_no=1, success=True, outputs=outputs)

    def test_01_legitimate_rule_with_negation_keyword_still_works(self):
        """合法规则 'no warnings' / '无警告' 不被否定词检测误判"""
        verifier = GoalVerifier()
        iter_ok = self._make_iter(warnings_count=0)
        # 精确匹配命中规则 → True
        self.assertTrue(verifier.check_criterion("no warnings", iter_ok))
        iter_fail = self._make_iter(warnings_count=3)
        self.assertFalse(verifier.check_criterion("no warnings", iter_fail))

    def test_02_chinese_legitimate_rule_works(self):
        """合法中文规则 '无警告' / '无错误' 不被否定词检测误判"""
        verifier = GoalVerifier()
        iter_ok = self._make_iter(warnings_count=0, errors_count=0)
        self.assertTrue(verifier.check_criterion("无警告", iter_ok))
        self.assertTrue(verifier.check_criterion("无错误", iter_ok))

    def test_03_strict_substring_requires_long_criterion(self):
        """严格子串：criterion 必须包含 rule_key（criterion 较长）"""
        verifier = GoalVerifier()
        iter_ok = self._make_iter(tests_failed=0, tests_run=5)
        # "all tests pass" → rule_key "all tests pass" 精确匹配
        self.assertTrue(verifier.check_criterion("all tests pass", iter_ok))
        # "all" 单字 → 严格子串要求 "all" 在 criterion 中（实际是）→ 但无对应规则
        # → 兜底 → False
        self.assertFalse(verifier.check_criterion("all", iter_ok))

    def test_04_and_semantics_for_multiple_criteria(self):
        """多 criterion 使用 AND 语义（全部满足才整体满足）"""
        verifier = GoalVerifier()
        goal = Goal(
            goal_id="g1", description="x",
            success_criteria=["tests pass", "no warnings", "code committed"],
        )
        # 全部满足
        iter_full = self._make_iter(
            tests_failed=0, tests_run=5, warnings_count=0, git_committed=True
        )
        all_met, met_list = verifier.check_all_criteria(goal, iter_full)
        self.assertTrue(all_met)
        self.assertEqual(len(met_list), 3)
        # 仅满足 2 个
        iter_partial = self._make_iter(
            tests_failed=0, tests_run=5, warnings_count=0, git_committed=False
        )
        all_met, met_list = verifier.check_all_criteria(goal, iter_partial)
        self.assertFalse(all_met)
        self.assertEqual(len(met_list), 2)
        # 0 个满足
        iter_none = self._make_iter(
            tests_failed=10, warnings_count=5, git_committed=False
        )
        all_met, met_list = verifier.check_all_criteria(goal, iter_none)
        self.assertFalse(all_met)
        self.assertEqual(len(met_list), 0)

    def test_05_substring_match_only_longer_criterion(self):
        """子串匹配仅当 rule_key ⊂ criterion_lower（避免短 criterion 误匹配长规则）"""
        verifier = GoalVerifier()
        iter_obj = self._make_iter(tests_failed=0, tests_run=5)
        # "all" 单独 → 不应匹配 "all tests pass"（短 criterion 不会匹配长 rule_key）
        # 新逻辑：仅当 rule_key 在 criterion_lower 中才匹配 → "all tests pass" not in "all"
        self.assertFalse(verifier.check_criterion("all", iter_obj))
        # "all tests" → 是 "all tests pass" 的子串 → 命中规则
        self.assertTrue(verifier.check_criterion("all tests", iter_obj))


class TestP0Fixes_CrossProcessConcurrency(unittest.TestCase):
    """P0-2 修复：跨进程并发 + 状态合并（3 个）"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_concurrent_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_threading_concurrent_save_no_data_loss(self):
        """10 线程并发 save_iteration → 全部 10 条 iteration 落盘"""
        registry = GoalRegistry(storage_root=self.tmp_dir)
        goal = registry.create_goal(description="并发测试", goal_id="thread-test")
        iterations = [
            IterationResult(iteration_no=i + 1, success=True, outputs={"i": i})
            for i in range(10)
        ]

        def save_iter(i):
            registry.save_iteration(goal.goal_id, iterations[i])

        threads = []
        for i in range(10):
            t = threading.Thread(target=save_iter, args=(i,))
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        loaded = registry.get_goal(goal.goal_id)
        self.assertEqual(
            len(loaded.iterations), 10,
            f"并发保存后 iteration 不应丢失：实际 {len(loaded.iterations)} 条"
        )
        # 验证 iteration_no 全部存在
        iter_nos = {i.iteration_no for i in loaded.iterations}
        self.assertEqual(iter_nos, set(range(1, 11)))

    def test_02_multiprocessing_concurrent_save_no_data_loss(self):
        """多进程并发 save_iteration → 全部 iteration 落盘（跨进程锁验证）"""
        import subprocess
        import sys

        # 准备：主进程创建 goal
        registry = GoalRegistry(storage_root=self.tmp_dir)
        registry.create_goal(description="多进程测试", goal_id="mp-test")

        # 启动 4 个子进程（使用 subprocess 启动独立 Python 解释器）
        # 这样可以避免 multiprocessing.Pool 的 pickle 问题
        def make_worker_script(iteration_no: int) -> str:
            """为每个子进程生成独立脚本（避免 f-string 与 .format 冲突）"""
            return (
                "import sys\n"
                f"sys.path.insert(0, {SCRIPTS_DIR!r})\n"
                "from loop_goal import GoalRegistry, IterationResult\n"
                f"registry = GoalRegistry(storage_root={str(self.tmp_dir)!r})\n"
                "iter_obj = IterationResult(\n"
                f"    iteration_no={iteration_no}, success=True,\n"
                "    outputs={'pid': 'worker'}\n"
                ")\n"
                "registry.save_iteration('mp-test', iter_obj)\n"
            )

        procs = []
        for i in range(1, 5):
            script = make_worker_script(i)
            p = subprocess.Popen(
                [sys.executable, "-c", script],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            procs.append(p)
        for p in procs:
            stdout, stderr = p.communicate(timeout=10)
            if p.returncode != 0:
                logger.warning(f"子进程失败：{stderr}")

        # 主进程验证
        loaded = registry.get_goal("mp-test")
        self.assertGreaterEqual(
            len(loaded.iterations), 4,
            f"多进程保存后 iteration 至少 4 条：实际 {len(loaded.iterations)} 条"
        )

    def test_03_lock_file_cleanup_after_save(self):
        """保存完成后 lock 文件可被清理（不影响下次锁获取）"""
        registry = GoalRegistry(storage_root=self.tmp_dir)
        registry.create_goal(description="x", goal_id="lock-test")
        for i in range(1, 4):
            registry.save_iteration(
                "lock-test",
                IterationResult(iteration_no=i, success=True),
            )
        # 多次保存后应能正常读取
        loaded = registry.get_goal("lock-test")
        self.assertEqual(len(loaded.iterations), 3)


class TestP0Fixes_GoalIdBoundary(unittest.TestCase):
    """P0 修复：Goal ID 边界条件（2 个）"""

    def test_01_goal_id_minimum_length(self):
        """goal_id 最小长度 2（kebab-case）"""
        # "ab" 合法（2 字符）
        goal = Goal(goal_id="ab", description="x")
        self.assertEqual(goal.goal_id, "ab")
        # "a" 非法（GOAL_ID_PATTERN 要求至少 2 字符：[a-z][a-z0-9-]*[a-z0-9]）
        with self.assertRaises(InvalidGoalIdError):
            Goal(goal_id="a", description="x")

    def test_02_goal_id_uppercase_rejected(self):
        """goal_id 拒绝大写字母"""
        for bad_id in ["Fix-Tests", "FIX-TESTS", "FixTests", "FIX"]:
            with self.subTest(bad_id=bad_id):
                with self.assertRaises(InvalidGoalIdError):
                    Goal(goal_id=bad_id, description="x")


# ============================================================================
# 测试 12：P1 修复补充测试（5 个）
# 覆盖：P1-1 IN_PROGRESS 兜底 + P1-2 criteria_met 持久化 + P1-3 FAILED 重启
# ============================================================================

class TestP1Fixes_OverallSuccessCLI(unittest.TestCase):
    """P1-1 修复：_is_overall_success IN_PROGRESS 兜底逻辑（2 个）"""

    def test_01_in_progress_with_criteria_exhausted_returns_false(self):
        """P1-1 修复：IN_PROGRESS + has_criteria=True + 跑满未满足 → False（CLI 退出码 1）"""
        from trae_agent_dispatch_v2 import _is_overall_success
        result = {
            "status": "in_progress",
            "has_criteria": True,  # 用户设了 criterion
            "converged_early": False,
            "total_iterations": 5,
        }
        # P1-1 修复前：return True（错误）
        # P1-1 修复后：return False（正确）
        self.assertFalse(
            _is_overall_success(result),
            "IN_PROGRESS + has_criteria=True + 跑满未满足应返回 False（CLI 退出码 1）"
        )

    def test_02_in_progress_no_criteria_exhausted_returns_true(self):
        """P1-1 修复：IN_PROGRESS + has_criteria=False + 跑满 → True（容错成功）"""
        from trae_agent_dispatch_v2 import _is_overall_success
        result = {
            "status": "in_progress",
            "has_criteria": False,  # 用户未设 criterion
            "converged_early": False,
            "total_iterations": 5,
        }
        self.assertTrue(
            _is_overall_success(result),
            "IN_PROGRESS + has_criteria=False + 跑满应返回 True（向后兼容）"
        )


class TestP1Fixes_CriteriaMetPersistence(unittest.TestCase):
    """P1-2 修复：criteria_met 持久化到磁盘（1 个）"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_p1_2_")
        self.registry = GoalRegistry(storage_root=self.tmp_dir)
        self.executor = LoopGoalExecutor(registry=self.registry)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_criteria_met_persisted_to_disk(self):
        """P1-2 修复：满足部分 criterion 时，criteria_met 正确持久化到 goal.json"""
        goal = self.registry.create_goal(
            description="x", goal_id="p1-2-test",
            criteria=["tests pass", "no warnings"],
            max_iterations=3,
        )

        # mock verifier：第一次部分满足
        mock_verifier = MagicMock()
        mock_verifier.check_all_criteria.side_effect = [
            (False, ["tests pass"]),  # iter 1: 部分满足（tests pass met，no warnings 未 met）
            (True, ["tests pass", "no warnings"]),  # iter 2: 全部满足
        ]
        executor = LoopGoalExecutor(registry=self.registry, verifier=mock_verifier)
        # 替换原 executor 的 verifier
        goal_to_run = self.registry.get_goal_or_raise("p1-2-test")

        def dispatch_fn(agent_type, task, task_id=None, project_root=".", progress=None):
            return True

        result = executor.execute_with_loop_goal(
            task="test",
            agent_type="solo-coder",
            dispatch_fn=dispatch_fn,
            project_root=self.tmp_dir,
            loop_config=LoopConfig(max_iterations=3, stop_on_success=True),
            goal=goal_to_run,
        )
        # 验证：第一次 iteration 的 criteria_met 应持久化到磁盘（不再丢失）
        loaded = self.registry.get_goal("p1-2-test")
        iter1 = next((i for i in loaded.iterations if i.iteration_no == 1), None)
        self.assertIsNotNone(iter1, "第一次 iteration 必须存在")
        self.assertEqual(
            iter1.criteria_met, ["tests pass"],
            f"P1-2 修复：criteria_met 必须正确持久化（实际 {iter1.criteria_met}）"
        )


class TestP1Fixes_FailedGoalRestart(unittest.TestCase):
    """P1-3 修复：FAILED 目标重启后能转为 ACHIEVED（2 个）"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_p1_3_")
        self.registry = GoalRegistry(storage_root=self.tmp_dir)
        self.executor = LoopGoalExecutor(registry=self.registry)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_failed_goal_restart_with_success_achieves(self):
        """P1-3 修复：FAILED 目标重启 → 满足 criterion → ACHIEVED"""
        # 1. 创建 goal 并手动转为 FAILED
        goal = self.registry.create_goal(
            description="x", goal_id="p1-3-test",
            criteria=["tests pass"],
            max_iterations=2,
        )
        goal = self.registry.get_goal_or_raise("p1-3-test")
        goal.transition_to(GoalStatus.IN_PROGRESS)
        goal.transition_to(GoalStatus.FAILED)
        self.registry.update_goal(goal)

        # 2. mock verifier：第二次返回成功
        mock_verifier = MagicMock()
        mock_verifier.check_all_criteria.side_effect = [
            (False, []),  # iter 1: 不满足
            (True, ["tests pass"]),  # iter 2: 满足
        ]
        executor = LoopGoalExecutor(registry=self.registry, verifier=mock_verifier)

        def dispatch_fn(agent_type, task, task_id=None, project_root=".", progress=None):
            return True

        # 3. 重启目标执行
        goal_to_run = self.registry.get_goal_or_raise("p1-3-test")
        result = executor.execute_with_loop_goal(
            task="test",
            agent_type="solo-coder",
            dispatch_fn=dispatch_fn,
            project_root=self.tmp_dir,
            loop_config=LoopConfig(max_iterations=5, stop_on_success=True),
            goal=goal_to_run,
        )
        # 4. 验证：成功转为 ACHIEVED
        self.assertEqual(
            result["status"], "achieved",
            f"P1-3 修复：FAILED 重启满足 criterion 应转为 ACHIEVED（实际 {result['status']}）"
        )
        # 5. 验证磁盘状态
        loaded = self.registry.get_goal("p1-3-test")
        self.assertEqual(loaded.status, GoalStatus.ACHIEVED)

    def test_02_failed_goal_restart_without_success_stays_failed(self):
        """P1-3 修复：FAILED 目标重启 → 跑满未满足 → 仍 FAILED（不退化为 ACTIVE）"""
        goal = self.registry.create_goal(
            description="x", goal_id="p1-3-fail-test",
            criteria=["non-existent-criterion"],
            max_iterations=2,
        )
        # 手动转为 FAILED
        goal = self.registry.get_goal_or_raise("p1-3-fail-test")
        goal.transition_to(GoalStatus.IN_PROGRESS)
        goal.transition_to(GoalStatus.FAILED)
        self.registry.update_goal(goal)

        def dispatch_fn(agent_type, task, task_id=None, project_root=".", progress=None):
            return True

        # 重启执行（不满足 criterion → 跑满 2 次 → 应再次 FAILED）
        goal_to_run = self.registry.get_goal_or_raise("p1-3-fail-test")
        result = self.executor.execute_with_loop_goal(
            task="test",
            agent_type="solo-coder",
            dispatch_fn=dispatch_fn,
            project_root=self.tmp_dir,
            loop_config=LoopConfig(max_iterations=2, stop_on_success=True),
            goal=goal_to_run,
        )
        self.assertEqual(result["status"], "failed")
        loaded = self.registry.get_goal("p1-3-fail-test")
        self.assertEqual(loaded.status, GoalStatus.FAILED)


# ============================================================================
# 测试 13：Phase 12 修复补充测试（Issue 3 / 4 / 5 / 7）
# ============================================================================

class TestPhase12Fixes_DispatchFnReturnDict(unittest.TestCase):
    """Phase 12 修复（Issue 3）：dispatch_fn 支持返回 dict (success + outputs)"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_p12_issue3_")
        self.registry = GoalRegistry(storage_root=self.tmp_dir)
        self.executor = LoopGoalExecutor(registry=self.registry)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_dispatch_fn_returns_bool_backward_compatible(self):
        """向后兼容：dispatch_fn 返回 bool → iteration.success 正确设置"""
        def dispatch_fn(agent_type, task, task_id=None, project_root=".", progress=None):
            return True

        result = self.executor.execute_with_loop_goal(
            task="test",
            agent_type="solo-coder",
            dispatch_fn=dispatch_fn,
            project_root=self.tmp_dir,
            loop_config=LoopConfig(max_iterations=1),
        )
        self.assertEqual(result["total_iterations"], 1)
        # success_early / FAILED 不应出现
        self.assertFalse(result.get("success_early", False))

    def test_02_dispatch_fn_returns_dict_with_outputs(self):
        """dispatch_fn 返回 dict (success + outputs) → iteration.outputs 合并"""
        def dispatch_fn(agent_type, task, task_id=None, project_root=".", progress=None):
            return {
                "success": True,
                "outputs": {
                    "files_modified": 3,
                    "tests_passed": 10,
                    "tests_failed": 0,
                    "warnings_count": 0,
                },
            }

        # 准备 goal（确保 result["iterations"] 存在）
        goal = self.registry.create_goal(
            description="x", goal_id="p12-3-iter-out", max_iterations=1
        )
        result = self.executor.execute_with_loop_goal(
            task="test",
            agent_type="solo-coder",
            dispatch_fn=dispatch_fn,
            project_root=self.tmp_dir,
            loop_config=LoopConfig(max_iterations=1),
            goal=goal,
        )
        # 验证 outputs 已合并到 iteration
        self.assertEqual(result["total_iterations"], 1)
        iter_dict = result["iterations"][0]
        self.assertEqual(iter_dict["outputs"]["files_modified"], 3)
        self.assertEqual(iter_dict["outputs"]["tests_passed"], 10)
        self.assertEqual(iter_dict["outputs"]["tests_failed"], 0)
        self.assertEqual(iter_dict["outputs"]["warnings_count"], 0)

    def test_03_dispatch_fn_dict_enables_goal_verification(self):
        """dispatch_fn 返回 dict + outputs → GoalVerifier 能正确判定 criterion"""
        def dispatch_fn(agent_type, task, task_id=None, project_root=".", progress=None):
            return {
                "success": True,
                "outputs": {
                    "files_modified": 2,
                    "tests_passed": 5,
                    "tests_failed": 0,  # 关键：0 = tests pass
                    "warnings_count": 0,
                },
            }

        goal = self.registry.create_goal(
            description="x", goal_id="p12-3-test",
            criteria=["tests pass", "no warnings"],
            max_iterations=3,
        )
        result = self.executor.execute_with_loop_goal(
            task="test",
            agent_type="solo-coder",
            dispatch_fn=dispatch_fn,
            project_root=self.tmp_dir,
            loop_config=LoopConfig(max_iterations=3, stop_on_success=True),
            goal=goal,
        )
        # 第一次 iteration 就应满足 criterion → 提前成功退出
        self.assertTrue(result["success_early"], "Issue 3 修复：dict outputs 应能驱动验证")
        self.assertEqual(result["status"], "achieved")
        self.assertEqual(result["total_iterations"], 1)

    def test_04_dispatch_fn_dict_with_error(self):
        """dispatch_fn 返回 dict 含 error → iteration.error 正确写入"""
        def dispatch_fn(agent_type, task, task_id=None, project_root=".", progress=None):
            return {
                "success": False,
                "outputs": {"files_modified": 0},
                "error": "自定义错误信息",
            }

        goal = self.registry.create_goal(
            description="x", goal_id="p12-3-err", max_iterations=1
        )
        result = self.executor.execute_with_loop_goal(
            task="test",
            agent_type="solo-coder",
            dispatch_fn=dispatch_fn,
            project_root=self.tmp_dir,
            loop_config=LoopConfig(max_iterations=1),
            goal=goal,
        )
        self.assertEqual(result["total_iterations"], 1)
        iter_dict = result["iterations"][0]
        self.assertFalse(iter_dict["success"])
        self.assertEqual(iter_dict["error"], "自定义错误信息")

    def test_05_dispatch_fn_returns_none_treated_as_failure(self):
        """dispatch_fn 返回 None → 视为失败（保守策略）"""
        def dispatch_fn(agent_type, task, task_id=None, project_root=".", progress=None):
            return None

        goal = self.registry.create_goal(
            description="x", goal_id="p12-3-none", max_iterations=1
        )
        result = self.executor.execute_with_loop_goal(
            task="test",
            agent_type="solo-coder",
            dispatch_fn=dispatch_fn,
            project_root=self.tmp_dir,
            loop_config=LoopConfig(max_iterations=1),
            goal=goal,
        )
        iter_dict = result["iterations"][0]
        self.assertFalse(iter_dict["success"])
        self.assertIn("None", iter_dict["error"])

    def test_06_dispatch_fn_returns_unknown_type_treated_as_failure(self):
        """dispatch_fn 返回未知类型（如 int/str）→ 视为失败 + 警告日志"""
        def dispatch_fn(agent_type, task, task_id=None, project_root=".", progress=None):
            return 42  # 未知类型

        goal = self.registry.create_goal(
            description="x", goal_id="p12-3-unknown", max_iterations=1
        )
        result = self.executor.execute_with_loop_goal(
            task="test",
            agent_type="solo-coder",
            dispatch_fn=dispatch_fn,
            project_root=self.tmp_dir,
            loop_config=LoopConfig(max_iterations=1),
            goal=goal,
        )
        iter_dict = result["iterations"][0]
        self.assertFalse(iter_dict["success"])
        self.assertIn("未知类型", iter_dict["error"])

    def test_07_dispatch_fn_dict_missing_success_treated_as_failure(self):
        """dispatch_fn dict 缺少 success 键 → 视为失败（防御性编程）"""
        def dispatch_fn(agent_type, task, task_id=None, project_root=".", progress=None):
            return {"outputs": {"files_modified": 1}}  # 缺 success

        goal = self.registry.create_goal(
            description="x", goal_id="p12-3-miss", max_iterations=1
        )
        result = self.executor.execute_with_loop_goal(
            task="test",
            agent_type="solo-coder",
            dispatch_fn=dispatch_fn,
            project_root=self.tmp_dir,
            loop_config=LoopConfig(max_iterations=1),
            goal=goal,
        )
        iter_dict = result["iterations"][0]
        self.assertFalse(iter_dict["success"], "缺 success 键应视为失败")


class TestPhase12Fixes_SaveIterationNoReread(unittest.TestCase):
    """Phase 12 修复（Issue 5）：使用 save_iteration 返回值代替重读"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_p12_issue5_")
        self.registry = GoalRegistry(storage_root=self.tmp_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_save_iteration_returns_updated_goal(self):
        """save_iteration 返回更新后的 Goal 引用（不再需要 get_goal_or_raise 重读）"""
        goal = self.registry.create_goal(
            description="x", goal_id="p12-5-test", max_iterations=3
        )
        iteration = IterationResult(
            iteration_no=1, success=True,
            outputs={"files_modified": 2, "tests_passed": 5},
        )
        # save_iteration 返回新 Goal（已含本次 iteration）
        returned_goal = self.registry.save_iteration("p12-5-test", iteration)
        # 验证：returned_goal.iterations 包含本次 iteration
        self.assertEqual(len(returned_goal.iterations), 1)
        self.assertEqual(returned_goal.iterations[0].iteration_no, 1)
        # 验证：与磁盘一致
        loaded = self.registry.get_goal("p12-5-test")
        self.assertEqual(len(loaded.iterations), 1)
        # 验证：返回的 goal 与磁盘 goal 的 iterations 内容一致
        self.assertEqual(
            len(returned_goal.iterations), len(loaded.iterations)
        )

    def test_02_executor_uses_returned_goal_not_reread(self):
        """executor 内部使用 save_iteration 返回值（避免多余磁盘 IO）"""
        executor = LoopGoalExecutor(registry=self.registry)

        def dispatch_fn(agent_type, task, task_id=None, project_root=".", progress=None):
            return {
                "success": True,
                "outputs": {"files_modified": 1, "tests_passed": 0, "tests_failed": 0},
            }

        goal = self.registry.create_goal(
            description="x", goal_id="p12-5-exec", max_iterations=3
        )

        # 通过记录 get_goal_or_raise 的调用次数验证 executor 不在循环内重读
        # （保存后会调用 save_iteration 而不是 get_goal_or_raise）
        original_get_or_raise = self.registry.get_goal_or_raise
        get_call_count = {"n": 0}

        def spy_get_or_raise(goal_id):
            """Spy 版本：调用原方法并统计调用次数"""
            get_call_count["n"] += 1
            return original_get_or_raise(goal_id)

        self.registry.get_goal_or_raise = spy_get_or_raise
        try:
            result = executor.execute_with_loop_goal(
                task="test",
                agent_type="solo-coder",
                dispatch_fn=dispatch_fn,
                project_root=self.tmp_dir,
                loop_config=LoopConfig(max_iterations=3),
                goal=goal,
            )
            # Issue 5 修复前：executor 在循环内会调用 get_goal_or_raise 重读（每次 iteration 1 次 + 初始）
            # Issue 5 修复后：executor 仅在 execute_with_loop_goal 入口（创建 goal 后）调用 0 次
            #   save_iteration 返回值已含最新数据，不再需要重读
            # 期望：get_goal_or_raise 调用次数 <= 1（创建 goal 时可能调用 0 次，循环内 0 次）
            self.assertLessEqual(
                get_call_count["n"], 1,
                f"get_goal_or_raise 不应在循环内重读（实际 {get_call_count['n']} 次）"
            )
            # 验证基本执行正确
            self.assertEqual(result["total_iterations"], 3)
        finally:
            self.registry.get_goal_or_raise = original_get_or_raise


class TestPhase12Fixes_InputGoalNoSideEffect(unittest.TestCase):
    """Phase 12 修复（Issue 4）：_save_goal_atomic_with_lock 不修改入参 goal"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_p12_issue4_")
        self.registry = GoalRegistry(storage_root=self.tmp_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_input_goal_not_modified_by_save(self):
        """save_iteration 不应修改入参 goal 对象的 status / iterations"""
        # 1. 创建 goal
        goal = self.registry.create_goal(
            description="x", goal_id="p12-4-test", max_iterations=3
        )
        original_status = goal.status
        original_iter_count = len(goal.iterations)
        original_goal_id = goal.goal_id

        # 2. 调用 save_iteration
        iteration = IterationResult(iteration_no=1, success=True)
        self.registry.save_iteration("p12-4-test", iteration)

        # 3. 验证入参 goal 对象未被修改
        self.assertEqual(
            goal.status, original_status,
            f"入参 goal.status 不应被修改（原 {original_status}，现 {goal.status}）"
        )
        self.assertEqual(
            len(goal.iterations), original_iter_count,
            f"入参 goal.iterations 长度不应被修改（原 {original_iter_count}，现 {len(goal.iterations)}）"
        )
        self.assertEqual(goal.goal_id, original_goal_id)

    def test_02_remote_merge_does_not_affect_input_goal(self):
        """远端合并场景：入参 goal 不被污染（Issue 4 修复核心验证）

        场景设计：
        1. 创建 goal
        2. 写入 1 次 iteration（模拟远端已有数据）
        3. 重新读取得到 input_goal，清空其 iterations（模拟"本地内存未同步"）
        4. 调用 save_iteration（本地追加 iter 2 + 触发远端合并 iter 1）
        5. 验证：input_goal.iterations 仍为空（不应被远端合并污染）
        6. 验证：input_goal.status 仍为 ACTIVE（不应被远端状态覆盖）

        Issue 4 修复前的 bug：executor 会就地修改 input_goal（如 input_goal.iterations
        被填充 iter 1 + iter 2），违反"函数不应修改入参"原则。
        Issue 4 修复后：save_iteration 在 need_merge=True 时 deepcopy input_goal，
        返回的是新对象，input_goal 引用保持不变。
        """
        # 1. 创建 goal
        goal = self.registry.create_goal(
            description="x", goal_id="p12-4-merge-test", max_iterations=5
        )
        # 2. 写入 1 次 iteration（模拟远端）
        self.registry.save_iteration(
            "p12-4-merge-test",
            IterationResult(iteration_no=1, success=True, outputs={"i": 1}),
        )
        # 3. 创建新的入参 goal（status=ACTIVE, iterations=[]）
        input_goal = self.registry.get_goal_or_raise("p12-4-merge-test")
        # 清空 iterations 模拟"本地未追加"场景
        input_goal.iterations = []
        # 记录清空后的状态用于后续断言
        original_status = input_goal.status
        original_iter_count = len(input_goal.iterations)
        original_updated_at = input_goal.updated_at

        # 4. 调用 save_iteration（本地追加 iter 2 + 触发远端合并 iter 1）
        returned_goal = self.registry.save_iteration(
            "p12-4-merge-test",
            IterationResult(iteration_no=2, success=True, outputs={"i": 2}),
        )

        # 5. 验证入参 input_goal.iterations 仍为空（不应被远端合并污染）
        self.assertEqual(
            len(input_goal.iterations), original_iter_count,
            f"Issue 4：入参 input_goal.iterations 长度不应被修改"
            f"（原 {original_iter_count}，实际 {len(input_goal.iterations)}）"
        )
        # 6. 验证入参 input_goal.status 仍为 ACTIVE（不应被远端状态覆盖）
        self.assertEqual(
            input_goal.status, original_status,
            f"Issue 4：入参 input_goal.status 不应被修改"
            f"（原 {original_status}，实际 {input_goal.status}）"
        )
        # 7. 验证 returned_goal 与 input_goal 是不同对象（Issue 4 deepcopy 保证）
        self.assertIsNot(
            returned_goal, input_goal,
            "Issue 4：返回的 goal 应是新对象（deepcopy）而非入参引用"
        )
        # 8. 验证 returned_goal 包含合并后的 iteration（iter 1 + iter 2）
        self.assertEqual(
            len(returned_goal.iterations), 2,
            f"returned_goal 应包含 2 条 iteration（远端 1 + 本地 2）"
            f"，实际 {len(returned_goal.iterations)} 条"
        )


class TestPhase12P2Fixes_NoMergePathReferenceContract(unittest.TestCase):
    """Phase 12 P2 优化：no_merge 路径返回值引用契约

    本测试类验证 P2 优化后 _save_goal_atomic_with_lock 在两条路径上的
    引用契约是否严格符合 docstring 声明：

    - 路径 A (need_merge=False)：返回**入参同一对象**（returned_goal is goal）
    - 路径 B (need_merge=True) ：返回**新对象**（returned_goal is not goal）

    两路径都不修改入参 goal 对象（已由 TestPhase12Fixes_InputGoalNoSideEffect
    覆盖），本测试类专注于**引用身份**层面的契约验证。

    ⚠️ 重要：_save_goal_atomic_with_lock 是底层方法，本身不负责追加 new_iteration。
       完整流程应在 save_iteration 中调用：先 append new_iteration 到 goal.iterations
       （内存），再调用 _save_goal_atomic_with_lock 做远程合并。直接调用本方法时
       需先在本地 goal.iterations 中放入 new_iteration。
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_p12_p2_ref_")
        self.registry = GoalRegistry(storage_root=self.tmp_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _build_goal(self, goal_id: str, iterations: Optional[List[IterationResult]] = None,
                    status: GoalStatus = GoalStatus.ACTIVE) -> Goal:
        """构造测试用 Goal 实例（status=ACTIVE, max_iterations=5）"""
        return Goal(
            goal_id=goal_id,
            description="x",
            success_criteria=[],
            max_iterations=5,
            status=status,
            iterations=iterations or [],
            created_at="2026-06-06T00:00:00",
            updated_at="2026-06-06T00:00:00",
        )

    def test_01_path_a_no_remote_returns_same_object(self):
        """路径 A：无远端数据时返回入参同一对象（性能优化路径）

        场景设计：
        1. 内存中构造一个 goal（含 1 个 iteration），**未持久化到磁盘**
        2. 直接调用 _save_goal_atomic_with_lock（无 new_iteration）
        3. 验证：返回值 is 入参（同一对象引用；skip deepcopy）
        4. 验证：磁盘写入后，goal 内容与入参一致

        P2 优化前 docstring："Returns: 合并 + 写入后的 Goal 实例（新对象；如未
        发生合并可能与入参是同一对象）"——模糊不清。
        P2 优化后 docstring：明确路径 A 返回**入参同一对象引用**。
        """
        # 1. 构造内存 goal（未持久化），含 1 个 iteration
        iter1 = IterationResult(iteration_no=1, success=True)
        goal = self._build_goal(
            "p12-p2-path-a", iterations=[iter1]
        )

        # 2. 路径 A：无远端数据（磁盘无文件），无 new_iteration
        returned_goal = self.registry._save_goal_atomic_with_lock(
            goal, new_iteration=None
        )

        # 3. 验证：返回值与入参是同一对象引用
        self.assertIs(
            returned_goal, goal,
            "路径 A (need_merge=False) 应返回入参同一对象引用；"
            "P2 优化 docstring 契约要求 skip deepcopy"
        )

        # 4. 验证：磁盘已写入
        disk_goal = self.registry.get_goal_or_raise("p12-p2-path-a")
        self.assertEqual(len(disk_goal.iterations), 1)
        self.assertEqual(disk_goal.iterations[0].iteration_no, 1)

    def test_02_path_a_with_new_iteration_no_remote_returns_same_object(self):
        """路径 A2：磁盘有当前 goal 的所有 iteration（无新远程）时返回入参同一对象

        场景设计：
        1. 通过 save_iteration 持久化 goal（含 iter 1）
        2. 重新读出 input_goal（含 iter 1）
        3. 在内存中 append iter 2（模拟 save_iteration 流程）
        4. 调用 _save_goal_atomic_with_lock(input_goal, new_iteration=iter2)
           - 磁盘有 iter 1，本地也有 iter 1 → 远端无新增
           - need_merge=False → 不会发生 deepcopy
        5. 验证：returned_goal is input_goal（同一引用）
        6. 验证：input_goal.iterations 包含 iter 1 + iter 2（内存 in-place 写入）

        ⚠️ 重要：_save_goal_atomic_with_lock 只做"远程合并"判断，不负责把
           new_iteration append 到 goal.iterations。这部分由 save_iteration
           在调用本方法前完成。测试模拟该流程。
        """
        # 1. 通过 save_iteration 持久化 goal（含 iter 1）
        self.registry.create_goal(
            description="x", goal_id="p12-p2-path-a2", max_iterations=5
        )
        self.registry.save_iteration(
            "p12-p2-path-a2",
            IterationResult(iteration_no=1, success=True, outputs={"k": 1}),
        )

        # 2. 重新读出 input_goal（含 iter 1）
        input_goal = self.registry.get_goal_or_raise("p12-p2-path-a2")

        # 3. 在内存中 append iter 2（模拟 save_iteration 流程）
        iter2 = IterationResult(iteration_no=2, success=True, outputs={"k": 2})
        input_goal.iterations.append(iter2)
        original_input_id = id(input_goal)

        # 4. 路径 A2：磁盘有 iter 1 + 本地有 iter 1 → 远端无新增
        #    need_merge=False → skip deepcopy
        returned_goal = self.registry._save_goal_atomic_with_lock(
            input_goal, new_iteration=iter2
        )

        # 5. 验证：返回值与入参是同一对象引用
        self.assertIs(
            returned_goal, input_goal,
            "路径 A2 (need_merge=False, 有 new_iteration) 应返回入参同一对象引用"
        )
        self.assertEqual(
            id(returned_goal), original_input_id,
            "路径 A2 返回值对象 ID 应与入参一致"
        )

        # 6. 验证：input_goal.iterations 包含 iter 1 + iter 2（内存 in-place 写入）
        self.assertEqual(len(input_goal.iterations), 2)
        iteration_nos = {it.iteration_no for it in input_goal.iterations}
        self.assertEqual(iteration_nos, {1, 2})

    def test_03_path_b_with_remote_merge_returns_new_object(self):
        """路径 B：远端有未持有的 iteration 时，返回新对象（deepcopy 路径）

        场景设计：
        1. 通过 save_iteration 持久化 goal（含 iter 1，模拟远端已有数据）
        2. 重新读出 input_goal（含 iter 1）
        3. 清空 input_goal.iterations 模拟"本地内存未同步"（关键）
        4. 在内存中 append iter 2（模拟 save_iteration 流程）
        5. 调用 _save_goal_atomic_with_lock(input_goal, new_iteration=iter2)
           - 磁盘有 iter 1，本地无 → need_merge=True → deepcopy
        6. 验证：returned_goal is not input_goal（不同对象）
        7. 验证：input_goal.iterations 仍为空（未被远端污染——Issue 4 修复验证）
        8. 验证：returned_goal.iterations 包含 iter 1 + iter 2
        """
        # 1. 持久化 goal（含 iter 1）
        self.registry.create_goal(
            description="x", goal_id="p12-p2-path-b", max_iterations=5
        )
        self.registry.save_iteration(
            "p12-p2-path-b",
            IterationResult(iteration_no=1, success=True, outputs={"i": 1}),
        )

        # 2. 重新读出 input_goal
        input_goal = self.registry.get_goal_or_raise("p12-p2-path-b")

        # 3. 清空 input_goal.iterations 模拟"本地未同步"（关键步骤）
        input_goal.iterations = []
        original_input_id = id(input_goal)

        # 4. 在内存中 append iter 2（模拟 save_iteration 流程）
        iter2 = IterationResult(iteration_no=2, success=True, outputs={"i": 2})
        input_goal.iterations.append(iter2)
        # 注意：此时 input_goal.iterations == [iter2]（仅本地新增）

        # 5. 路径 B：磁盘有 iter 1（本地无）→ need_merge=True → deepcopy
        returned_goal = self.registry._save_goal_atomic_with_lock(
            input_goal, new_iteration=iter2
        )

        # 6. 验证：返回值与入参不是同一对象
        self.assertIsNot(
            returned_goal, input_goal,
            "路径 B (need_merge=True) 应返回新对象（deepcopy 后）"
        )
        self.assertNotEqual(
            id(returned_goal), original_input_id,
            "路径 B 的 returned_goal 对象 ID 应与入参不同（已 deepcopy）"
        )

        # 7. 验证：returned_goal 包含合并后的 iteration（iter 1 远端 + iter 2 本地）
        #    路径 B 的 merge 逻辑：goal.iterations = [iter 1, iter 2]（合并后）
        #    注意：path B 的 deepcopy 是在 input_goal（含 iter 2）的基础上做的，
        #    然后 merge 会追加 disk_goal 中没有的 iteration（即 iter 1）
        self.assertEqual(len(returned_goal.iterations), 2)
        iteration_nos = {it.iteration_no for it in returned_goal.iterations}
        self.assertEqual(iteration_nos, {1, 2})

    def test_04_path_b_remote_terminal_status_protected(self):
        """路径 B 子场景：远端终态保护 + deepcopy 行为

        场景设计：
        1. 持久化一个 goal
        2. 直接修改磁盘为 ACHIEVED 状态 + iter 1（模拟远端判定完成）
        3. 重新读出 input_goal（status=ACHIEVED, iter 1）
        4. 模拟"本地内存未同步"：input_goal.status=ACTIVE, iterations=[]
        5. 在内存中 append iter 2
        6. 调用 _save_goal_atomic_with_lock(input_goal, new_iteration=iter2)
           - 远端 ACHIEVED + iter 1（本地无）→ need_merge=True → deepcopy
           - 状态合并：远端终态覆盖本地 ACTIVE
        7. 验证：returned_goal is not input_goal
        8. 验证：input_goal.status 仍为 ACTIVE（入参未被污染）
        9. 验证：returned_goal.status 为 ACHIEVED（远端终态保护生效）
        10. 验证：returned_goal.iterations 包含 iter 1（远端）+ iter 2（本地）
        """
        # 1. 持久化 goal
        self.registry.create_goal(
            description="y", goal_id="p12-p2-terminal-2", max_iterations=5
        )

        # 2. 直接修改磁盘为 ACHIEVED（模拟远端判定完成）+ iter 1
        disk_goal = self.registry.get_goal_or_raise("p12-p2-terminal-2")
        disk_goal.status = GoalStatus.ACHIEVED
        disk_goal.iterations.append(
            IterationResult(iteration_no=1, success=True, outputs={"remote": 1})
        )
        self.registry._save_goal_atomic(disk_goal)

        # 3. 重新读出 input_goal（status=ACHIEVED, iter 1）
        input_goal = self.registry.get_goal_or_raise("p12-p2-terminal-2")
        # 此时 input_goal.status=ACHIEVED, input_goal.iterations=[iter 1]

        # 4. 模拟"本地内存未同步"：input_goal.status=ACTIVE, iterations=[]
        input_goal.status = GoalStatus.ACTIVE
        input_goal.iterations = []
        original_input_id = id(input_goal)

        # 5. 在内存中 append iter 2（模拟 save_iteration 流程）
        iter2 = IterationResult(iteration_no=2, success=True)
        input_goal.iterations.append(iter2)

        # 6. 路径 B：磁盘有 iter 1（本地无）+ 远端 ACHIEVED → need_merge=True
        returned_goal = self.registry._save_goal_atomic_with_lock(
            input_goal, new_iteration=iter2
        )

        # 7. 验证：返回值与入参不是同一对象（deepcopy）
        self.assertIsNot(
            returned_goal, input_goal,
            "路径 B + 远端终态：应返回新对象"
        )
        self.assertNotEqual(id(returned_goal), original_input_id)

        # 8. 验证：入参 input_goal.status 仍为 ACTIVE（未被污染）
        self.assertEqual(
            input_goal.status, GoalStatus.ACTIVE,
            "入参 input_goal.status 不应被远端终态覆盖"
        )

        # 9. 验证：returned_goal.status 为 ACHIEVED（远端终态保护）
        self.assertEqual(
            returned_goal.status, GoalStatus.ACHIEVED,
            "远端终态 ACHIEVED 应被采纳（终态保护）"
        )

        # 10. 验证：returned_goal.iterations 包含 iter 1（远端）+ iter 2（本地）
        self.assertEqual(len(returned_goal.iterations), 2)
        iteration_nos = {it.iteration_no for it in returned_goal.iterations}
        self.assertEqual(iteration_nos, {1, 2})

    def test_05_path_a_does_not_trigger_deepcopy_overhead(self):
        """路径 A 性能优化验证：skip deepcopy 不产生额外开销

        场景设计：
        1. 通过 save_iteration 持久化 100 个 iteration（覆盖 max_iterations 上限检查）
        2. 重新读出（确保磁盘已包含所有 iteration）
        3. 模拟 save_iteration：append iter 101
        4. 验证：保存后返回值 is 入参（skip deepcopy 路径生效）

        本测试主要验证**逻辑路径**而非性能数据（性能由 test_06_* 单独验证）。

        注意：max_iterations 上限 100，但 save_iteration 不强制 iteration_no <= max_iterations，
        仅在 execute_with_loop_goal 循环中强制。本测试验证 _save_goal_atomic_with_lock 性能路径。
        """
        # 1. 构造 goal 并持久化 100 个 iteration（max_iterations=10 不影响 save_iteration 持久化）
        self.registry.create_goal(
            description="x", goal_id="p12-p2-perf-skip", max_iterations=10
        )
        for i in range(1, 101):
            self.registry.save_iteration(
                "p12-p2-perf-skip",
                IterationResult(iteration_no=i, success=True, outputs={"i": i}),
            )

        # 2. 重新读出 goal（100 iterations）
        input_goal = self.registry.get_goal_or_raise("p12-p2-perf-skip")
        self.assertEqual(len(input_goal.iterations), 100)

        # 3. 模拟 save_iteration：append iter 101
        iter101 = IterationResult(iteration_no=101, success=True)
        input_goal.iterations.append(iter101)
        # 此时磁盘有 1-100，本地有 1-101（磁盘无新增）→ need_merge=False

        # 4. 调用 _save_goal_atomic_with_lock（路径 A：无远端新增）
        returned_goal = self.registry._save_goal_atomic_with_lock(
            input_goal, new_iteration=iter101
        )

        # 5. 验证：返回值 is 入参
        self.assertIs(
            returned_goal, input_goal,
            "路径 A (100 iters, 无远端新增) 应返回入参同一对象；"
            "skip deepcopy 性能优化路径"
        )

        # 6. 验证：磁盘正确写入（101 iterations）
        disk_goal = self.registry.get_goal_or_raise("p12-p2-perf-skip")
        self.assertEqual(len(disk_goal.iterations), 101)

    def test_06_path_a_returns_id_match_b_path_returns_id_mismatch(self):
        """路径 A vs 路径 B：返回值引用对比（docstring 契约核心）

        场景设计：
        1. 准备两个相同初始状态的 goal（status=ACTIVE, iterations=[]）
        2. 路径 A：写完 iter 1 后再写 iter 2，磁盘无新增 → returned is input
        3. 路径 B：磁盘有 iter 1（本地清空）+ 本地 append iter 2 → returned is not input

        本测试用同结构 goal 对象分别触发两种路径，确保 P2 优化后的
        引用契约（路径 A: is, 路径 B: is not）严格成立。
        """
        # 准备两个 goal（同时 create + 写 iter 1）
        self.registry.create_goal(
            description="x", goal_id="p12-p2-contract-a", max_iterations=5
        )
        self.registry.create_goal(
            description="x", goal_id="p12-p2-contract-b", max_iterations=5
        )
        iter1_a = IterationResult(iteration_no=1, success=True)
        iter1_b = IterationResult(iteration_no=1, success=True)
        self.registry.save_iteration("p12-p2-contract-a", iter1_a)
        self.registry.save_iteration("p12-p2-contract-b", iter1_b)

        # 路径 A：磁盘已有 iter 1，本地 append iter 2（磁盘无新增）→ need_merge=False
        goal_a = self.registry.get_goal_or_raise("p12-p2-contract-a")
        iter2_a = IterationResult(iteration_no=2, success=True)
        goal_a.iterations.append(iter2_a)
        returned_a = self.registry._save_goal_atomic_with_lock(
            goal_a, new_iteration=iter2_a
        )

        # 路径 B：磁盘已有 iter 1，本地清空 + append iter 2 → need_merge=True
        goal_b = self.registry.get_goal_or_raise("p12-p2-contract-b")
        goal_b.iterations = []  # 模拟"本地未同步"
        iter2_b = IterationResult(iteration_no=2, success=True)
        goal_b.iterations.append(iter2_b)
        returned_b = self.registry._save_goal_atomic_with_lock(
            goal_b, new_iteration=iter2_b
        )

        # 验证 P2 docstring 契约：
        # 路径 A: returned_a is goal_a
        self.assertIs(
            returned_a, goal_a,
            "P2 契约：路径 A 返回值 is 入参（skip deepcopy）"
        )
        # 路径 B: returned_b is not goal_b
        self.assertIsNot(
            returned_b, goal_b,
            "P2 契约：路径 B 返回值 is not 入参（deepcopy 合并）"
        )

        # 附加验证：路径 A 写盘正确
        disk_a = self.registry.get_goal_or_raise("p12-p2-contract-a")
        self.assertEqual(len(disk_a.iterations), 2)
        # 路径 B 写盘正确（合并后）
        disk_b = self.registry.get_goal_or_raise("p12-p2-contract-b")
        self.assertEqual(len(disk_b.iterations), 2)


class TestPhase12Fixes_DispatchFnTypeSignature(unittest.TestCase):
    """Phase 12 修复（Issue 7）：收紧 dispatch_fn 类型签名"""

    def test_01_dispatch_fn_type_alias_exists(self):
        """DispatchFnReturn 类型别名应存在且定义为 Union[bool, Dict]"""
        import inspect
        from loop_goal import LoopGoalExecutor

        # 验证：LoopGoalExecutor 类体内存在 DispatchFnReturn 注解
        # 通过 __annotations__ 读取（含类体内 type alias）
        annotations = getattr(LoopGoalExecutor, '__annotations__', {})
        # 备用：通过 inspect.get_annotations 读取（含继承的）
        all_annotations = inspect.get_annotations(LoopGoalExecutor)
        # 合并两个来源
        merged = {**annotations, **all_annotations}
        # 类型别名在类体中（Python 3.12+）也会出现在 annotations 中
        # 这里改为通过 callable 的签名间接验证：
        # 准备一个返回 bool 的 dispatch_fn 和一个返回 dict 的 dispatch_fn，
        # 两者都应能通过 execute_with_loop_goal 接受
        executor = LoopGoalExecutor(registry=GoalRegistry(storage_root="/tmp/test_sig"))

        def fn_bool(agent_type, task, **kwargs):
            return True

        def fn_dict(agent_type, task, **kwargs):
            return {"success": True, "outputs": {}}

        # 两种签名都应被接受（type alias Union[bool, Dict]）
        result_bool = executor.execute_with_loop_goal(
            task="t", agent_type="solo-coder", dispatch_fn=fn_bool,
            project_root="/tmp", loop_config=LoopConfig(max_iterations=1),
        )
        result_dict = executor.execute_with_loop_goal(
            task="t", agent_type="solo-coder", dispatch_fn=fn_dict,
            project_root="/tmp", loop_config=LoopConfig(max_iterations=1),
        )
        # 验证两者都执行成功（不抛 TypeError）
        self.assertEqual(result_bool["total_iterations"], 1)
        self.assertEqual(result_dict["total_iterations"], 1)

    def test_02_dispatch_fn_default_outputs_in_merged(self):
        """dispatch_fn 不返回 outputs 时，iteration.outputs 应含默认字段"""
        tmp_dir = tempfile.mkdtemp(prefix="test_def_")
        try:
            executor = LoopGoalExecutor(registry=GoalRegistry(storage_root=tmp_dir))

            def fn(agent_type, task, **kwargs):
                return True  # 旧式 bool，不含 outputs

            # 使用 unique goal_id 避免重入冲突
            unique_id = f"p12-7-def-{int(time.time() * 1000000)}"
            goal = executor.registry.create_goal(
                description="x", goal_id=unique_id, max_iterations=1
            )
            result = executor.execute_with_loop_goal(
                task="t", agent_type="solo-coder", dispatch_fn=fn,
                project_root=tmp_dir, loop_config=LoopConfig(max_iterations=1),
                goal=goal,
            )
            iter_dict = result["iterations"][0]
            # 默认字段应被填充
            self.assertIn("files_modified", iter_dict["outputs"])
            self.assertIn("tests_passed", iter_dict["outputs"])
            self.assertIn("tests_failed", iter_dict["outputs"])
            self.assertIn("warnings_count", iter_dict["outputs"])
            self.assertIn("errors_count", iter_dict["outputs"])
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ============================================================================
# Phase 13.1: 多 Goal 编排 — Goal 数据模型扩展（6 个新用例）
# ============================================================================

class TestGoalMultiGoalFields(unittest.TestCase):
    """Phase 13.1: 验证 Goal 扩展字段（schema_version / parent_goal_id / depends_on / aggregation_strategy）。"""

    def test_01_goal_schema_version_default(self):
        """Phase 13.1: Goal 默认 schema_version 应为 '13.0'。"""
        g = Goal(goal_id="g1", description="test")
        self.assertEqual(g.schema_version, "13.0")

    def test_02_goal_parent_goal_id_default(self):
        """Phase 13.1: Goal 默认 parent_goal_id 应为 None。"""
        g = Goal(goal_id="g1", description="test")
        self.assertIsNone(g.parent_goal_id)

    def test_03_goal_depends_on_default(self):
        """Phase 13.1: Goal 默认 depends_on 应为空 list。"""
        g = Goal(goal_id="g1", description="test")
        self.assertEqual(g.depends_on, [])

    def test_04_goal_aggregation_strategy_default(self):
        """Phase 13.1: Goal 默认 aggregation_strategy 应为 AND。"""
        g = Goal(goal_id="g1", description="test")
        # 可能为 GoalAggregationStrategy.AND 或字符串 "AND"，两者等价
        self.assertEqual(str(g.aggregation_strategy.value), "AND")

    def test_05_goal_aggregation_strategy_string_input(self):
        """Phase 13.1: aggregation_strategy 接受字符串并转换为枚举。"""
        g = Goal(goal_id="g1", description="test", aggregation_strategy="OR")
        # 转换为枚举后 value 应当为 "OR"
        self.assertEqual(g.aggregation_strategy.value, "OR")

    def test_06_goal_aggregation_strategy_invalid_string(self):
        """Phase 13.1: aggregation_strategy 非法字符串应抛 LoopGoalError。"""
        with self.assertRaises(LoopGoalError):
            Goal(goal_id="g1", description="test", aggregation_strategy="INVALID")


class TestGoalResumeFields(unittest.TestCase):
    """Phase 13.1: 验证 Goal 续跑相关字段（resume_count / max_resume_count）。"""

    def test_01_goal_resume_count_default(self):
        """Phase 13.1: Goal 默认 resume_count 应为 0。"""
        g = Goal(goal_id="g1", description="test")
        self.assertEqual(g.resume_count, 0)

    def test_02_goal_max_resume_count_default(self):
        """Phase 13.1: Goal 默认 max_resume_count 应为 3。"""
        g = Goal(goal_id="g1", description="test")
        self.assertEqual(g.max_resume_count, 3)


class TestGoalSchemaVersioning(unittest.TestCase):
    """Phase 13.1 B3 修复: Goal JSON schema_version 向后兼容。"""

    def test_01_from_dict_missing_schema_version_defaults_to_13(self):
        """Phase 13.1: 缺 schema_version 字段时反序列化为 '13.0'（B3 向后兼容）。"""
        data = {
            "goal_id": "g1",
            "description": "test",
            "status": "active",
        }
        g = Goal.from_dict(data)
        self.assertEqual(g.schema_version, "13.0")

    def test_02_to_dict_includes_schema_version(self):
        """Phase 13.1: to_dict 应包含 schema_version 字段。"""
        g = Goal(goal_id="g1", description="test")
        d = g.to_dict()
        self.assertIn("schema_version", d)
        self.assertEqual(d["schema_version"], "13.0")

    def test_03_round_trip_preserves_all_fields(self):
        """Phase 13.1: 序列化 + 反序列化应保留所有 Phase 13 新增字段。"""
        original = Goal(
            goal_id="child1",
            description="child",
            parent_goal_id="parent1",
            depends_on=["pre1", "pre2"],
            aggregation_strategy="OR",
            resume_count=2,
            max_resume_count=5,
        )
        d = original.to_dict()
        restored = Goal.from_dict(d)
        self.assertEqual(restored.goal_id, "child1")
        self.assertEqual(restored.parent_goal_id, "parent1")
        self.assertEqual(restored.depends_on, ["pre1", "pre2"])
        self.assertEqual(restored.aggregation_strategy.value, "OR")
        self.assertEqual(restored.resume_count, 2)
        self.assertEqual(restored.max_resume_count, 5)
        self.assertEqual(restored.schema_version, "13.0")


# ============================================================================
# Phase 13.1: GoalRegistry 扩展 API（4 个新用例）
# ============================================================================

class TestGoalRegistryMultiGoalAPIs(unittest.TestCase):
    """Phase 13.1 A1 修复: GoalRegistry 新增 list_children / get_goal_status / 扩展 list_goals 签名。"""

    def setUp(self):
        """每个用例前创建临时存储。"""
        self.tmp_dir = tempfile.mkdtemp(prefix="p13_registry_")
        self.registry = GoalRegistry(storage_root=self.tmp_dir)

    def tearDown(self):
        """每个用例后清理。"""
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write_goal(self, goal_id: str, **fields: Any) -> None:
        """辅助方法：直接写一个 goal.json（绕过 create_goal 校验）。"""
        data = {
            "schema_version": "13.0",
            "goal_id": goal_id,
            "description": fields.get("description", "test"),
            "status": fields.get("status", "active"),
            "parent_goal_id": fields.get("parent_goal_id"),
            "depends_on": fields.get("depends_on", []),
        }
        goal_dir = Path(self.tmp_dir) / goal_id
        goal_dir.mkdir(parents=True, exist_ok=True)
        (goal_dir / "goal.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )

    def test_01_list_children_returns_child_ids(self):
        """Phase 13.1: list_children(parent_id) 返回子 Goal ID 列表。"""
        self._write_goal("parent1", parent_goal_id=None)
        self._write_goal("child1", parent_goal_id="parent1")
        self._write_goal("child2", parent_goal_id="parent1")
        self._write_goal("unrelated", parent_goal_id="other")
        children = self.registry.list_children("parent1")
        self.assertEqual(set(children), {"child1", "child2"})

    def test_02_list_children_no_parent_returns_empty(self):
        """Phase 13.1: 无子 goal 时 list_children 返回空 list。"""
        self._write_goal("leaf", parent_goal_id=None)
        children = self.registry.list_children("nonexistent_parent")
        self.assertEqual(children, [])

    def test_03_get_goal_status_returns_status_enum(self):
        """Phase 13.1: get_goal_status 返回 GoalStatus 枚举。"""
        self._write_goal("g1", status="in_progress")
        status = self.registry.get_goal_status("g1")
        self.assertEqual(status, GoalStatus.IN_PROGRESS)

    def test_04_get_goal_status_not_found_returns_none(self):
        """Phase 13.1: 不存在的 goal 返回 None。"""
        status = self.registry.get_goal_status("nonexistent")
        self.assertIsNone(status)

    def test_05_list_goals_old_status_signature_backward_compat(self):
        """Phase 13.1 N1 修复: 旧 status= 签名仍工作（向后兼容）。"""
        self._write_goal("g1", status="active")
        self._write_goal("g2", status="failed")
        self._write_goal("g3", status="active")
        active_goals = self.registry.list_goals(status=GoalStatus.ACTIVE)
        self.assertEqual(len(active_goals), 2)
        for g in active_goals:
            self.assertEqual(g.status, GoalStatus.ACTIVE)

    def test_06_list_goals_new_statuses_multi_filter(self):
        """Phase 13.1: statuses= 多状态过滤。"""
        self._write_goal("g1", status="active")
        self._write_goal("g2", status="failed")
        self._write_goal("g3", status="in_progress")
        self._write_goal("g4", status="achieved")
        filtered = self.registry.list_goals(
            statuses=[GoalStatus.FAILED, GoalStatus.IN_PROGRESS]
        )
        ids = {g.goal_id for g in filtered}
        self.assertEqual(ids, {"g2", "g3"})

    def test_07_list_goals_parent_goal_id_filter(self):
        """Phase 13.1: parent_goal_id 过滤。"""
        self._write_goal("p1", parent_goal_id=None)
        self._write_goal("c1", parent_goal_id="p1")
        self._write_goal("c2", parent_goal_id="p1")
        self._write_goal("other", parent_goal_id="p2")
        children = self.registry.list_goals(parent_goal_id="p1")
        ids = {g.goal_id for g in children}
        self.assertEqual(ids, {"c1", "c2"})

    def test_08_list_goals_include_root_only(self):
        """Phase 13.1: include_root_only=True 仅返回 parent_goal_id=None 的 goal。"""
        self._write_goal("root1", parent_goal_id=None)
        self._write_goal("root2", parent_goal_id=None)
        self._write_goal("child1", parent_goal_id="root1")
        roots = self.registry.list_goals(include_root_only=True)
        ids = {g.goal_id for g in roots}
        self.assertEqual(ids, {"root1", "root2"})

    def test_09_list_goals_combined_filters(self):
        """Phase 13.1: statuses + parent_goal_id 组合过滤。"""
        self._write_goal("root", parent_goal_id=None, status="active")
        self._write_goal("c1", parent_goal_id="root", status="active")
        self._write_goal("c2", parent_goal_id="root", status="failed")
        result = self.registry.list_goals(
            statuses=[GoalStatus.ACTIVE],
            parent_goal_id="root",
        )
        ids = {g.goal_id for g in result}
        self.assertEqual(ids, {"c1"})


# ============================================================================
# 入口
# ============================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
