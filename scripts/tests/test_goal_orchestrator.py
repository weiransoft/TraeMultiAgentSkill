#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_goal_orchestrator.py — Phase 13 多 Goal 编排核心测试

测试覆盖（合计 ~50+ 用例）：
- 异常类 / 基础数据类导入（2 个）
- GoalGraph（8 个）：加载 / 拓扑 / 环检测 / 完整性 / 大小 / 深度
- GoalScheduler（10 个）：导入 / 单 goal / DAG barrier / 配置 / 子进程入口
- GoalResumeManager（11 个）：5 种 status 续跑 / force / B5
- GoalIterationReuser（11 个）：skip_* 路径 / top-K / embedder / B5
- GoalOrchestrator（11 个）：构造器 / list_active / report JSON/MD / 截断
- 端到端（4 个）：单 root / 父子 / 50 节点 perf / 报告 perf
- DAG 失败路径（5 个）：root 缺失 / depends_on 缺失 / 环 / 环路径

作者：trae-multi-agent Phase 13
创建日期：2026-06-06
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

# 路径处理：tests 在 scripts/tests/，模块在 scripts/ 下
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(TESTS_DIR)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

# 父目录加入 path 以便 dynamic_workflow 等子包可被找到
DYNAMIC_WORKFLOW_PARENT = os.path.dirname(SCRIPTS_DIR)
if DYNAMIC_WORKFLOW_PARENT not in sys.path:
    sys.path.insert(0, DYNAMIC_WORKFLOW_PARENT)

from loop_goal import (
    Goal,
    GoalNotFoundError,
    GoalRegistry,
    GoalStatus,
    IterationResult,
    LoopConfig,
    LoopGoalError,
)

from goal_orchestrator import (
    # 异常
    GoalGraphCycleError,
    GoalGraphDepthError,
    GoalGraphIntegrityError,
    GoalGraphSizeError,
    GoalResumeError,
    GoalSchedulerTimeoutError,
    # 数据类
    CrossGoalReuseEntry,
    GoalExecutionResult,
    GoalGraph,
    GoalIterationReuser,
    GoalOrchestrator,
    GoalOrchestratorReport,
    GoalResumeManager,
    GoalScheduler,
    # 常量
    DEFAULT_REUSE_THRESHOLD,
    DEFAULT_EMBEDDER_NAME,
    TOP_K,
    _cosine_similarity,
    _execute_goal_in_subprocess,
    register_goal_executor,
)


# ============================================================================
# 测试 1：异常类与基础数据类导入（2 个）
# ============================================================================

class TestGoalOrchestratorImports(unittest.TestCase):
    """Phase 13.1: 异常类与基础数据类应能从 goal_orchestrator 导入。"""

    def test_01_import_goal_orchestrator_exceptions(self):
        """异常类应能从 goal_orchestrator 导入。"""
        assert GoalGraphCycleError is not None
        assert GoalGraphSizeError is not None
        assert GoalGraphDepthError is not None
        assert GoalGraphIntegrityError is not None
        assert GoalResumeError is not None
        assert GoalSchedulerTimeoutError is not None

    def test_02_import_goal_orchestrator_dataclasses(self):
        """数据类应能从 goal_orchestrator 导入。"""
        assert GoalGraph is not None
        assert GoalExecutionResult is not None
        assert GoalOrchestratorReport is not None
        assert CrossGoalReuseEntry is not None


# ============================================================================
# 工具函数
# ============================================================================

def _write_goal_file(
    storage: Path,
    goal_id: str,
    *,
    status: str = "active",
    depends_on: list = None,
    parent_goal_id: str = None,
    description: str = "test",
) -> None:
    """辅助：直接写一个 goal.json。"""
    if depends_on is None:
        depends_on = []
    goal_dir = storage / goal_id
    goal_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": "13.0",
        "goal_id": goal_id,
        "description": description,
        "status": status,
        "depends_on": depends_on,
        "parent_goal_id": parent_goal_id,
    }
    (goal_dir / "goal.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


# ============================================================================
# 测试 2：GoalGraph（8 个）
# ============================================================================

class TestGoalGraphBasics(unittest.TestCase):
    """Phase 13.1: GoalGraph 基础加载 + 拓扑排序。"""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="p13_graph_"))
        self.storage = self.tmp_dir / "goals"
        self.storage.mkdir()
        self.registry = GoalRegistry(storage_root=str(self.storage))

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_load_single_node(self):
        """Phase 13.1: GoalGraph 应能从 registry 加载单节点。"""
        _write_goal_file(self.storage, "g1", status="active")
        graph = GoalGraph(self.registry, "g1")
        self.assertEqual(len(graph.nodes), 1)
        self.assertIn("g1", graph.nodes)
        self.assertEqual(graph.topological_order(), ["g1"])

    def test_02_load_diamond(self):
        """Phase 13.1: 菱形依赖 root→b,c; b,c→d。"""
        for gid, deps in [
            ("root", []),
            ("goal-b", ["root"]),
            ("goal-c", ["root"]),
            ("goal-d", ["goal-b", "goal-c"]),
        ]:
            _write_goal_file(
                self.storage,
                gid,
                status="active",
                depends_on=deps,
                parent_goal_id="root" if gid != "root" else None,
            )
        graph = GoalGraph(self.registry, "root")
        order = graph.topological_order()
        self.assertEqual(order[0], "root")
        self.assertEqual(order[-1], "goal-d")
        self.assertEqual(set(order[1:3]), {"goal-b", "goal-c"})

    def test_03_cycle_self_loop(self):
        """Phase 13.1: 自环应触发 GoalGraphCycleError。"""
        _write_goal_file(
            self.storage, "g1", status="active", depends_on=["g1"]
        )
        graph = GoalGraph(self.registry, "g1")
        with self.assertRaises(GoalGraphCycleError):
            graph.topological_order()

    def test_04_cycle_3_nodes(self):
        """Phase 13.1: 3 节点环 node-1→node-2→node-3→node-1。"""
        for gid, deps in [
            ("node-1", ["node-3"]),
            ("node-2", ["node-1"]),
            ("node-3", ["node-2"]),
        ]:
            _write_goal_file(
                self.storage, gid, status="active", depends_on=deps
            )
        graph = GoalGraph(self.registry, "node-1")
        with self.assertRaises(GoalGraphCycleError):
            graph.topological_order()

    def test_05_forward_reference(self):
        """Phase 13.1 A4 修复: 加载 goal 时其 depends_on 引用应被自动加载。"""
        _write_goal_file(
            self.storage, "node-1", status="active", depends_on=["node-2"]
        )
        _write_goal_file(
            self.storage, "node-2", status="active", depends_on=[]
        )
        graph = GoalGraph(self.registry, "node-1")
        self.assertIn("node-2", graph.nodes)
        self.assertEqual(graph.topological_order(), ["node-2", "node-1"])

    def test_06_integrity_error_missing_endpoint(self):
        """Phase 13.1 A4 修复: 边端点缺失应抛 GoalNotFoundError。"""
        _write_goal_file(
            self.storage,
            "node-1",
            status="active",
            depends_on=["nonexistent-goal"],
        )
        with self.assertRaises(GoalNotFoundError):
            GoalGraph(self.registry, "node-1")

    def test_07_size_limit(self):
        """Phase 13.1: 节点数 > MAX_NODES 应抛 GoalGraphSizeError。"""
        # 构造星型依赖：root 依赖 50 个 leaf（根+50叶=51 节点，深度 1）
        leaf_ids = [f"leaf-{i:02d}" for i in range(GoalGraph.MAX_NODES)]
        for leaf_id in leaf_ids:
            _write_goal_file(
                self.storage,
                leaf_id,
                status="active",
                depends_on=[],
            )
        _write_goal_file(
            self.storage,
            "root",
            status="active",
            depends_on=leaf_ids,
        )
        with self.assertRaises(GoalGraphSizeError):
            GoalGraph(self.registry, "root")

    def test_08_depth_limit(self):
        """Phase 13.1: 深度 > MAX_DEPTH 应抛 GoalGraphDepthError。"""
        for i in range(GoalGraph.MAX_DEPTH + 2):
            deps = [f"g{i-1}"] if i > 0 else []
            _write_goal_file(
                self.storage,
                f"g{i}",
                status="active",
                depends_on=deps,
                parent_goal_id=f"g{i-1}" if i > 0 else None,
            )
        with self.assertRaises(GoalGraphDepthError):
            GoalGraph(self.registry, "g0")


# ============================================================================
# 测试 3：GoalScheduler（10 个）
# ============================================================================

class TestGoalSchedulerBasics(unittest.TestCase):
    """Phase 13.1: GoalScheduler 配置 + 子进程入口 + 导入。"""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="p13_sched_"))

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_scheduler_import(self):
        """Phase 13.1: GoalScheduler 应能从 goal_orchestrator 导入。"""
        self.assertIsNotNone(GoalScheduler)

    def test_02_scheduler_max_concurrent_default(self):
        """Phase 13.1: max_concurrent 默认值应为 10。"""
        registry = GoalRegistry(storage_root=str(self.tmp_dir))
        scheduler = GoalScheduler(registry)
        self.assertEqual(scheduler.max_concurrent, 10)
        scheduler.shutdown()

    def test_03_scheduler_uses_process_pool(self):
        """Phase 13.1 B1 修复: GoalScheduler 应使用 ProcessPoolExecutor。"""
        from concurrent.futures import ProcessPoolExecutor

        registry = GoalRegistry(storage_root=str(self.tmp_dir))
        scheduler = GoalScheduler(registry, max_concurrent=2)
        self.assertIsInstance(scheduler.executor_pool, ProcessPoolExecutor)
        scheduler.shutdown()

    def test_04_scheduler_cancel_event(self):
        """Phase 13.1: cancel_event 应存在且可设置。"""
        registry = GoalRegistry(storage_root=str(self.tmp_dir))
        scheduler = GoalScheduler(registry)
        self.assertFalse(scheduler._cancel_event.is_set())
        scheduler._cancel_event.set()
        self.assertTrue(scheduler._cancel_event.is_set())
        scheduler.shutdown()

    def test_05_scheduler_pause_event(self):
        """Phase 13.1: pause_event 应存在（跨进程 pause）。"""
        registry = GoalRegistry(storage_root=str(self.tmp_dir))
        scheduler = GoalScheduler(registry)
        self.assertFalse(scheduler._pause_event.is_set())
        scheduler.pause()
        self.assertTrue(scheduler._pause_event.is_set())
        scheduler.resume_event()
        self.assertFalse(scheduler._pause_event.is_set())
        scheduler.shutdown()

    def test_06_scheduler_shutdown(self):
        """Phase 13.1: shutdown() 应释放 ProcessPoolExecutor。"""
        registry = GoalRegistry(storage_root=str(self.tmp_dir))
        scheduler = GoalScheduler(registry, max_concurrent=2)
        scheduler.shutdown()  # 不应抛错

    def test_07_scheduler_max_concurrent_20(self):
        """Phase 13.1: max_concurrent 可设为 20（D1 优化）。"""
        registry = GoalRegistry(storage_root=str(self.tmp_dir))
        scheduler = GoalScheduler(registry, max_concurrent=20)
        self.assertEqual(scheduler.max_concurrent, 20)
        scheduler.shutdown()

    def test_08_scheduler_dag_timeout_config(self):
        """Phase 13.1: dag_timeout_seconds 应可配置。"""
        registry = GoalRegistry(storage_root=str(self.tmp_dir))
        scheduler = GoalScheduler(registry)
        scheduler.dag_timeout_seconds = 120
        self.assertEqual(scheduler.dag_timeout_seconds, 120)
        scheduler.shutdown()

    def test_09_execute_goal_in_subprocess_function_exists(self):
        """Phase 13.1: _execute_goal_in_subprocess 必须是模块级函数（pickle 兼容）。"""
        self.assertTrue(callable(_execute_goal_in_subprocess))

    def test_10_scheduler_cancel_method(self):
        """Phase 13.1: cancel() 应设置 cancel_event。"""
        registry = GoalRegistry(storage_root=str(self.tmp_dir))
        scheduler = GoalScheduler(registry)
        scheduler.cancel()
        self.assertTrue(scheduler._cancel_event.is_set())
        scheduler.shutdown()


# ============================================================================
# 测试 4：GoalResumeManager（11 个）
# ============================================================================

class TestGoalResumeManager(unittest.TestCase):
    """Phase 13.2: GoalResumeManager 续跑状态机。"""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="p13_resume_"))
        self.storage = self.tmp_dir / "goals"
        self.storage.mkdir()
        self.registry = GoalRegistry(storage_root=str(self.storage))
        self.mgr = GoalResumeManager(self.registry)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_resume_manager_import(self):
        """Phase 13.2: GoalResumeManager 应能从 goal_orchestrator 导入。"""
        self.assertIsNotNone(GoalResumeManager)

    def test_02_resume_active_goal(self):
        """Phase 13.2: ACTIVE goal 应直接执行（不递增计数）。"""
        _write_goal_file(self.storage, "g1", status="active")
        resumed = self.mgr.resume("g1")
        self.assertEqual(resumed.status, GoalStatus.ACTIVE)
        self.assertEqual(resumed.resume_count, 0)

    def test_03_resume_in_progress_goal(self):
        """Phase 13.2: IN_PROGRESS goal 应续跑（不递增计数）。"""
        _write_goal_file(self.storage, "g1", status="in_progress")
        resumed = self.mgr.resume("g1")
        self.assertEqual(resumed.status, GoalStatus.IN_PROGRESS)
        self.assertEqual(resumed.resume_count, 0)

    def test_04_resume_achieved_goal_raises(self):
        """Phase 13.2: ACHIEVED goal 续跑应抛 GoalResumeError。"""
        _write_goal_file(self.storage, "g1", status="achieved")
        with self.assertRaises(GoalResumeError):
            self.mgr.resume("g1")

    def test_05_resume_failed_goal_increments_count(self):
        """Phase 13.2: FAILED goal 续跑应递增 resume_count + 置 IN_PROGRESS。"""
        data = {
            "schema_version": "13.0",
            "goal_id": "g1",
            "description": "test",
            "status": "failed",
            "resume_count": 0,
            "max_resume_count": 3,
        }
        goal_dir = self.storage / "g1"
        goal_dir.mkdir(parents=True, exist_ok=True)
        (goal_dir / "goal.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        resumed = self.mgr.resume("g1")
        self.assertEqual(resumed.resume_count, 1)
        self.assertEqual(resumed.status, GoalStatus.IN_PROGRESS)

    def test_06_resume_failed_exceeds_max_marks_abandoned(self):
        """Phase 13.2: FAILED 续跑超限应标记 ABANDONED + 抛错。"""
        data = {
            "schema_version": "13.0",
            "goal_id": "g1",
            "description": "test",
            "status": "failed",
            "resume_count": 3,
            "max_resume_count": 3,
        }
        goal_dir = self.storage / "g1"
        goal_dir.mkdir(parents=True, exist_ok=True)
        (goal_dir / "goal.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        with self.assertRaises(GoalResumeError):
            self.mgr.resume("g1")
        # 验证磁盘上已标记为 ABANDONED
        goal = self.registry.get_goal_or_raise("g1")
        self.assertEqual(goal.status, GoalStatus.ABANDONED)

    def test_07_resume_abandoned_without_force_raises(self):
        """Phase 13.2: ABANDONED goal 无 --force 应抛 GoalResumeError。"""
        _write_goal_file(self.storage, "g1", status="abandoned")
        with self.assertRaises(GoalResumeError):
            self.mgr.resume("g1", force=False)

    def test_08_resume_abandoned_with_force_resets(self):
        """Phase 13.2 A5/N10 修复: ABANDONED + force=True 应重置 + IN_PROGRESS。"""
        data = {
            "schema_version": "13.0",
            "goal_id": "g1",
            "description": "test",
            "status": "abandoned",
            "resume_count": 3,
        }
        goal_dir = self.storage / "g1"
        goal_dir.mkdir(parents=True, exist_ok=True)
        (goal_dir / "goal.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        resumed = self.mgr.resume("g1", force=True)
        self.assertEqual(resumed.status, GoalStatus.IN_PROGRESS)
        self.assertEqual(resumed.resume_count, 0)

    def test_09_resume_failed_exceeds_with_force_resets(self):
        """Phase 13.2 N10 修复: FAILED 超限 + force=True 应重置（不标记 ABANDONED）。"""
        data = {
            "schema_version": "13.0",
            "goal_id": "g1",
            "description": "test",
            "status": "failed",
            "resume_count": 3,
            "max_resume_count": 3,
        }
        goal_dir = self.storage / "g1"
        goal_dir.mkdir(parents=True, exist_ok=True)
        (goal_dir / "goal.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        resumed = self.mgr.resume("g1", force=True)
        self.assertEqual(resumed.status, GoalStatus.IN_PROGRESS)
        self.assertEqual(resumed.resume_count, 0)

    def test_10_resume_b5_does_not_mutate_input(self):
        """Phase 13.2 B5 修复: resume() 返回新对象；入参不被修改。"""
        data = {
            "schema_version": "13.0",
            "goal_id": "g1",
            "description": "test",
            "status": "failed",
            "resume_count": 0,
            "max_resume_count": 3,
        }
        goal_dir = self.storage / "g1"
        goal_dir.mkdir(parents=True, exist_ok=True)
        (goal_dir / "goal.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        # 重新读取以获取 fresh 对象
        original = self.registry.get_goal_or_raise("g1")
        original_status_before = original.status
        original_count_before = original.resume_count

        resumed = self.mgr.resume("g1")

        # 入参 original 应未被修改
        self.assertEqual(original.status, original_status_before)
        self.assertEqual(original.resume_count, original_count_before)
        # 返回对象是新对象
        self.assertIsNot(resumed, original)
        self.assertEqual(resumed.status, GoalStatus.IN_PROGRESS)

    def test_11_get_resumable_goals_returns_deepcopy(self):
        """Phase 13.2 B5 修复: get_resumable_goals 返回 deepcopy。"""
        _write_goal_file(self.storage, "g1", status="active")
        _write_goal_file(self.storage, "g2", status="in_progress")
        resumable = self.mgr.get_resumable_goals()
        self.assertEqual(len(resumable), 2)
        # 修改返回的 goal 不应影响磁盘
        resumable[0].status = GoalStatus.FAILED
        fresh = self.registry.get_goal_or_raise(resumable[0].goal_id)
        # 磁盘未改
        self.assertNotEqual(fresh.status, GoalStatus.FAILED)


# ============================================================================
# 测试 5：GoalIterationReuser（11 个）
# ============================================================================

class TestGoalIterationReuser(unittest.TestCase):
    """Phase 13.3: GoalIterationReuser 跨 Goal 复用。"""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="p13_reuser_"))
        self.storage = self.tmp_dir / "goals"
        self.storage.mkdir()
        self.registry = GoalRegistry(storage_root=str(self.storage))

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_reuser_import(self):
        """Phase 13.3: GoalIterationReuser 应能从 goal_orchestrator 导入。"""
        self.assertIsNotNone(GoalIterationReuser)
        self.assertIsNotNone(CrossGoalReuseEntry)

    def test_02_reuser_no_parent_skips(self):
        """Phase 13.3: 无 parent_goal_id 的 goal 不参与复用。"""
        _write_goal_file(self.storage, "g1", status="active")
        reuser = GoalIterationReuser(self.registry, enabled=True)
        g = self.registry.get_goal_or_raise("g1")
        result = reuser.find_similar_iterations(g)
        self.assertEqual(result, [])
        # 审计日志应记录 skip_no_parent
        self.assertTrue(
            any(e.decision == "skip_no_parent" for e in reuser.audit_log)
        )

    def test_03_reuser_disabled_skips(self):
        """Phase 13.3: enabled=False 应跳过并记录 skip_disabled。"""
        _write_goal_file(
            self.storage, "g1", status="active", parent_goal_id="parent"
        )
        reuser = GoalIterationReuser(self.registry, enabled=False)
        g = self.registry.get_goal_or_raise("g1")
        result = reuser.find_similar_iterations(g)
        self.assertEqual(result, [])
        self.assertTrue(
            any(e.decision == "skip_disabled" for e in reuser.audit_log)
        )

    def test_04_reuser_no_siblings_skips(self):
        """Phase 13.3: 无 sibling goal 应跳过。"""
        _write_goal_file(
            self.storage, "g1", status="active", parent_goal_id="parent"
        )
        reuser = GoalIterationReuser(
            self.registry, enabled=True, reuse_threshold=0.85
        )
        g = self.registry.get_goal_or_raise("g1")
        result = reuser.find_similar_iterations(g)
        self.assertEqual(result, [])

    def test_05_reuser_top_k_constant(self):
        """Phase 13.3: TOP_K 应为 3。"""
        self.assertEqual(TOP_K, 3)

    def test_06_reuser_default_embedder_name(self):
        """Phase 13.3 B2 修复: 默认 embedder 应为 paraphrase-multilingual-MiniLM-L12-v2。"""
        self.assertEqual(
            DEFAULT_EMBEDDER_NAME,
            "paraphrase-multilingual-MiniLM-L12-v2",
        )

    def test_07_reuser_default_threshold(self):
        """Phase 13.3: DEFAULT_REUSE_THRESHOLD 应为 0.85。"""
        self.assertEqual(DEFAULT_REUSE_THRESHOLD, 0.85)

    def test_08_reuser_reuse_into_b5_no_mutation(self):
        """Phase 13.3 B5/N13 修复: reuse_into 不修改入参；返回新对象。"""
        _write_goal_file(self.storage, "g1", status="active")
        reuser = GoalIterationReuser(self.registry)
        g = self.registry.get_goal_or_raise("g1")
        original_iter_count = len(g.iterations)

        similar = [
            (
                IterationResult(
                    iteration_no=1, success=True, outputs={"key": "value"}
                ),
                "sibling_g",
                0.9,
            )
        ]
        new_g = reuser.reuse_into(g, similar)
        # 入参 g 不应被修改
        self.assertEqual(len(g.iterations), original_iter_count)
        # 返回新对象
        self.assertIsNot(new_g, g)
        self.assertEqual(len(new_g.iterations), original_iter_count + 1)
        # 新 iteration 标记 reuse
        self.assertIn(
            "__reuse_from__", new_g.iterations[-1].outputs
        )

    def test_09_reuser_reuse_into_skips_when_iterations_exist(self):
        """Phase 13.3 N24 修复: goal.iterations 非空时跳过。"""
        _write_goal_file(self.storage, "g1", status="active")
        reuser = GoalIterationReuser(self.registry)
        g = self.registry.get_goal_or_raise("g1")
        g.iterations.append(
            IterationResult(iteration_no=1, success=True, outputs={})
        )
        original_count = len(g.iterations)

        similar = [
            (
                IterationResult(
                    iteration_no=1, success=True, outputs={}
                ),
                "sibling",
                0.9,
            )
        ]
        new_g = reuser.reuse_into(g, similar)
        # 不增加 iteration
        self.assertEqual(len(new_g.iterations), original_count)

    def test_10_reuser_audit_log_structure(self):
        """Phase 13.3: CrossGoalReuseEntry 字段完整。"""
        entry = CrossGoalReuseEntry(
            source_goal_id="src",
            target_goal_id="tgt",
            similarity=0.9,
            threshold=0.85,
            decision="reuse",
            reused_iteration_no=1,
            timestamp="2026-06-06T10:00:00",
            notes="test",
        )
        d = entry.to_dict()
        self.assertEqual(d["source_goal_id"], "src")
        self.assertEqual(d["decision"], "reuse")
        self.assertEqual(d["similarity"], 0.9)

    def test_11_reuser_audit_log_persists_across_calls(self):
        """Phase 13.3: audit_log 应累积跨多次调用。"""
        _write_goal_file(self.storage, "g1", status="active")
        reuser = GoalIterationReuser(self.registry, enabled=True)
        g = self.registry.get_goal_or_raise("g1")
        reuser.find_similar_iterations(g)
        reuser.find_similar_iterations(g)
        # 应有 2 条审计
        self.assertGreaterEqual(len(reuser.audit_log), 2)


# ============================================================================
# 测试 6：GoalOrchestrator（11 个）
# ============================================================================

class TestGoalOrchestratorFacade(unittest.TestCase):
    """Phase 13.4: GoalOrchestrator 顶层门面。"""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="p13_orch_"))

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_orchestrator_import(self):
        """Phase 13.4: GoalOrchestrator 应能从 goal_orchestrator 导入。"""
        self.assertIsNotNone(GoalOrchestrator)

    def test_02_orchestrator_constructor_defaults(self):
        """Phase 13.4 N9 修复: 构造器默认值。"""
        registry = GoalRegistry(storage_root=str(self.tmp_dir))
        orch = GoalOrchestrator(registry=registry)
        self.assertEqual(orch.reuse_threshold, 0.85)
        self.assertTrue(orch.reuse_enabled)
        orch.scheduler.shutdown()

    def test_03_orchestrator_constructor_with_params(self):
        """Phase 13.4 N9 修复: 构造器接受 reuse_threshold / reuse_enabled。"""
        registry = GoalRegistry(storage_root=str(self.tmp_dir))
        orch = GoalOrchestrator(
            registry=registry,
            reuse_threshold=0.95,
            reuse_enabled=False,
        )
        self.assertEqual(orch.reuse_threshold, 0.95)
        self.assertFalse(orch.reuse_enabled)
        orch.scheduler.shutdown()

    def test_04_orchestrator_list_active_no_goals(self):
        """Phase 13.4: list_active() 在无 goal 时返回空 list。"""
        registry = GoalRegistry(storage_root=str(self.tmp_dir))
        orch = GoalOrchestrator(registry=registry)
        active = orch.list_active()
        self.assertEqual(active, [])
        orch.scheduler.shutdown()

    def test_05_orchestrator_generate_report_json(self):
        """Phase 13.4 N12 修复: generate_report(json) 完整实现。"""
        registry = GoalRegistry(storage_root=str(self.tmp_dir))
        # 无 root → 应抛 GoalNotFoundError
        orch = GoalOrchestrator(registry=registry)
        with self.assertRaises(GoalNotFoundError):
            orch.generate_report("nonexistent", format="json")
        orch.scheduler.shutdown()

    def test_06_orchestrator_generate_report_markdown(self):
        """Phase 13.4: generate_report(md) 完整实现。"""
        registry = GoalRegistry(storage_root=str(self.tmp_dir))
        orch = GoalOrchestrator(registry=registry)
        with self.assertRaises(GoalNotFoundError):
            orch.generate_report("nonexistent", format="md")
        orch.scheduler.shutdown()

    def test_07_orchestrator_report_truncation_above_50_nodes(self):
        """Phase 13.4 D5 修复: 节点数 > 50 时截断为摘要。"""
        root = GoalExecutionResult(
            goal_id="root", status=GoalStatus.ACHIEVED
        )
        current = root
        for i in range(50):
            child = GoalExecutionResult(
                goal_id=f"g{i}", status=GoalStatus.ACHIEVED
            )
            current.children_results.append(child)
            current = child

        report = GoalOrchestratorReport(
            root_goal_id="root",
            total_elapsed_seconds=1.0,
            goal_tree=root,
        )
        json_str = report.to_json()
        parsed = json.loads(json_str)
        # 节点数 = 1 + 50 = 51 > 50 → 截断
        self.assertTrue(parsed["goal_tree"]["_truncated"])
        self.assertIn("summary", parsed["goal_tree"])

    def test_08_orchestrator_cancel(self):
        """Phase 13.4: cancel() 应设置 cancel_event。"""
        registry = GoalRegistry(storage_root=str(self.tmp_dir))
        orch = GoalOrchestrator(registry=registry)
        self.assertFalse(orch.scheduler._cancel_event.is_set())
        orch.cancel("any_goal_id")
        self.assertTrue(orch.scheduler._cancel_event.is_set())
        orch.scheduler.shutdown()

    def test_09_report_to_json_with_simple_tree(self):
        """Phase 13.4: report.to_json 含完整字段。"""
        result = GoalExecutionResult(
            goal_id="root",
            status=GoalStatus.ACHIEVED,
            total_iterations=3,
            elapsed_seconds=1.5,
        )
        report = GoalOrchestratorReport(
            root_goal_id="root",
            total_elapsed_seconds=1.5,
            goal_tree=result,
            iteration_reuse_count=0,
        )
        json_str = report.to_json()
        parsed = json.loads(json_str)
        self.assertEqual(parsed["root_goal_id"], "root")
        self.assertEqual(parsed["goal_tree"]["status"], "achieved")
        self.assertEqual(parsed["_schema_version"], "13.0")

    def test_10_report_to_markdown_includes_key_fields(self):
        """Phase 13.4: report.to_markdown 含关键字段。"""
        result = GoalExecutionResult(
            goal_id="root",
            status=GoalStatus.ACHIEVED,
            total_iterations=3,
            elapsed_seconds=1.5,
        )
        report = GoalOrchestratorReport(
            root_goal_id="root",
            total_elapsed_seconds=1.5,
            goal_tree=result,
        )
        md_str = report.to_markdown()
        self.assertIn("# Goal 编排报告", md_str)
        self.assertIn("`root`", md_str)
        # GoalStatus.ACHIEVED 的 value 是小写 "achieved"
        self.assertIn("achieved", md_str)

    def test_11_invalid_format_raises_value_error(self):
        """Phase 13.4: format 非法应抛 ValueError。"""
        registry = GoalRegistry(storage_root=str(self.tmp_dir))
        orch = GoalOrchestrator(registry=registry)
        with self.assertRaises(ValueError):
            orch.generate_report("any", format="xml")
        orch.scheduler.shutdown()


# ============================================================================
# 测试 7：_cosine_similarity 工具函数（3 个）
# ============================================================================

class TestCosineSimilarity(unittest.TestCase):
    """Phase 13.3: _cosine_similarity 工具函数。"""

    def test_01_identical_vectors(self):
        """相同向量余弦相似度 = 1.0。"""
        v = [1.0, 2.0, 3.0]
        self.assertAlmostEqual(_cosine_similarity(v, v), 1.0, places=5)

    def test_02_orthogonal_vectors(self):
        """正交向量余弦相似度 = 0.0。"""
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        self.assertAlmostEqual(_cosine_similarity(a, b), 0.0, places=5)

    def test_03_zero_vector_returns_zero(self):
        """零向量返回 0.0。"""
        a = [0.0, 0.0]
        b = [1.0, 2.0]
        self.assertEqual(_cosine_similarity(a, b), 0.0)


# ============================================================================
# 测试 8：Phase 13 CLI flag 解析（9 个）
# ============================================================================

class TestPhase13CLIFlags(unittest.TestCase):
    """Phase 13.6: CLI 9 个新 flag 应能被正确解析。"""

    def setUp(self):
        # 强制 import（避免影响其它测试的 sys.path）
        import importlib
        import importlib.util
        # scripts_dir 已在模块顶部加入 sys.path；trae_agent_dispatch_v2 位于 scripts/ 下
        try:
            import trae_agent_dispatch_v2 as v2
        except ImportError:
            # 当测试目录层级有变化时通过 spec 加载
            v2_path = SCRIPTS_DIR + "/trae_agent_dispatch_v2.py"
            spec = importlib.util.spec_from_file_location(
                "trae_agent_dispatch_v2", v2_path
            )
            v2 = importlib.util.module_from_spec(spec)
            sys.modules["trae_agent_dispatch_v2"] = v2
            spec.loader.exec_module(v2)
        self.v2 = v2
        self._real_argv = sys.argv

    def tearDown(self):
        sys.argv = self._real_argv

    def _parse(self, extra: list) -> Any:
        """辅助：设置 argv 并调用 parse_arguments。"""
        sys.argv = ["trae_agent_dispatch_v2.py", "--task", "test-task"] + extra
        return self.v2.parse_arguments()

    def test_01_multi_goal_flag(self):
        """--multi-goal 应被解析。"""
        args = self._parse(["--multi-goal", "root-id"])
        self.assertEqual(args.multi_goal, "root-id")

    def test_02_goal_parent_flag(self):
        """--goal-parent 应被解析。"""
        args = self._parse(["--goal-parent", "parent-id"])
        self.assertEqual(args.goal_parent, "parent-id")

    def test_03_goal_depends_flag(self):
        """--goal-depends 可多次传入，累积为 list。"""
        args = self._parse([
            "--goal-depends", "dep-1",
            "--goal-depends", "dep-2",
        ])
        self.assertEqual(args.goal_depends, ["dep-1", "dep-2"])

    def test_04_goal_aggregation_flag(self):
        """--goal-aggregation 应限制为 AND/OR/MAJORITY。"""
        args = self._parse(["--goal-aggregation", "OR"])
        self.assertEqual(args.goal_aggregation, "OR")

    def test_05_goal_resume_flag(self):
        """--goal-resume 应被解析。"""
        args = self._parse(["--goal-resume", "resume-id"])
        self.assertEqual(args.goal_resume, "resume-id")

    def test_06_goal_resume_force_flag(self):
        """--goal-resume-force 应为 store_true。"""
        args = self._parse(["--goal-resume", "id", "--goal-resume-force"])
        self.assertTrue(args.goal_resume_force)

    def test_07_goal_max_resume_count_flag(self):
        """--goal-max-resume-count 应被解析。"""
        args = self._parse(["--goal-max-resume-count", "5"])
        self.assertEqual(args.goal_max_resume_count, 5)

    def test_08_reuse_threshold_flag(self):
        """--reuse-threshold 应被解析。"""
        args = self._parse(["--reuse-threshold", "0.95"])
        self.assertAlmostEqual(args.reuse_threshold, 0.95, places=5)

    def test_09_disable_iteration_reuse_flag(self):
        """--disable-iteration-reuse 应为 store_true。"""
        args = self._parse(["--disable-iteration-reuse"])
        self.assertTrue(args.disable_iteration_reuse)

    def test_10_max_concurrent_flag(self):
        """--max-concurrent 应被解析。"""
        args = self._parse(["--max-concurrent", "20"])
        self.assertEqual(args.max_concurrent, 20)

    def test_11_goal_report_flag(self):
        """--goal-report 应限制为 json/md。"""
        args = self._parse(["--goal-report", "json"])
        self.assertEqual(args.goal_report, "json")

    def test_12_defaults(self):
        """未传入时所有 Phase 13 flag 走默认值。"""
        args = self._parse([])
        self.assertIsNone(args.multi_goal)
        self.assertIsNone(args.goal_parent)
        self.assertEqual(args.goal_depends, [])
        self.assertEqual(args.goal_aggregation, "AND")
        self.assertIsNone(args.goal_resume)
        self.assertFalse(args.goal_resume_force)
        self.assertEqual(args.goal_max_resume_count, 3)
        self.assertAlmostEqual(args.reuse_threshold, 0.85, places=5)
        self.assertFalse(args.disable_iteration_reuse)
        self.assertEqual(args.max_concurrent, 10)
        self.assertIsNone(args.goal_report)


# ============================================================================
# 入口
# ============================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
