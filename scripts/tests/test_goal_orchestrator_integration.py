#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_goal_orchestrator_integration.py — Phase 13 端到端集成测试 + DAG 失败路径

测试覆盖：
- 端到端集成（4 个）：单 root / 父子 / 50 节点 perf / 报告 perf
- DAG 失败路径（5 个）：root 缺失 / depends_on 缺失 / 环 / 环路径 / 深度超限
- CLI 集成（3 个）：resume / multi-goal 解析 + 调用

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
from typing import Any, Dict, List, Optional, Tuple

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
    GoalAggregationStrategy,
    GoalNotFoundError,
    GoalRegistry,
    GoalStatus,
    LoopConfig,
)

from goal_orchestrator import (
    GoalGraph,
    GoalGraphCycleError,
    GoalGraphDepthError,
    GoalGraphIntegrityError,
    GoalGraphSizeError,
    GoalIterationReuser,
    GoalOrchestrator,
    GoalResumeManager,
    GoalScheduler,
    CrossGoalReuseEntry,
    GoalExecutionResult,
    GoalOrchestratorReport,
    DEFAULT_REUSE_THRESHOLD,
)


# ============================================================================
# 工具函数
# ============================================================================

def _write_goal_file(
    storage: Path,
    goal_id: str,
    *,
    status: str = "active",
    depends_on: Optional[List[str]] = None,
    parent_goal_id: Optional[str] = None,
    description: str = "test",
    max_iterations: int = 1,
    convergence_window: int = 1,
) -> None:
    """辅助：直接写一个 goal.json（最小化字段）。"""
    if depends_on is None:
        depends_on = []
    goal_dir = storage / goal_id
    goal_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": "13.0",
        "goal_id": goal_id,
        "description": description,
        "status": status,
        "depends_on": list(depends_on),
        "parent_goal_id": parent_goal_id,
        "max_iterations": max_iterations,
        "convergence_window": convergence_window,
        "success_criteria": [],
        "iterations": [],
        "resume_count": 0,
        "max_resume_count": 3,
        "created_at": "2026-06-06T00:00:00",
        "updated_at": "2026-06-06T00:00:00",
    }
    (goal_dir / "goal.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


def _create_goal_via_registry(
    registry: GoalRegistry,
    goal_id: str,
    description: str = "test",
    *,
    parent_goal_id: Optional[str] = None,
    depends_on: Optional[List[str]] = None,
    max_iterations: int = 1,
) -> Goal:
    """辅助：通过 registry.create_goal 创建后回填 parent / depends_on（保持原子写）。"""
    goal = registry.create_goal(
        description=description,
        criteria=[],
        goal_id=goal_id,
        max_iterations=max_iterations,
        convergence_window=1,
        created_by="test",
        task_template=description,
    )
    goal.parent_goal_id = parent_goal_id
    goal.depends_on = list(depends_on or [])
    registry._save_goal_atomic(goal)
    return goal


def _fast_dispatch(agent_type: str = "test", task: str = "", task_id: Optional[str] = None,
                    project_root: str = ".", progress: Optional[Dict] = None) -> bool:
    """极简 dispatch mock（用于集成测试，不调真实 LLM）。"""
    return True


# ============================================================================
# 测试 1：端到端集成
# ============================================================================

class TestEndToEndIntegration(unittest.TestCase):
    """Phase 13.7: 端到端集成 + 性能基线。"""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="p13_e2e_"))
        self.storage = self.tmp_dir / "goals"
        self.storage.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_e2e_single_root_active(self):
        """Phase 13.7: 单 root（ACTIVE）端到端：应返回 ACTIVE 报告。"""
        registry = GoalRegistry(storage_root=str(self.storage))
        _write_goal_file(self.storage, "root", status="active", max_iterations=1)
        orch = GoalOrchestrator(registry=registry, max_concurrent=2)
        try:
            report = orch.generate_report("root", format="json")
            parsed = json.loads(report)
            self.assertEqual(parsed["root_goal_id"], "root")
            self.assertEqual(parsed["goal_tree"]["status"], "active")
        finally:
            orch.scheduler.shutdown()

    def test_02_e2e_parent_child_tree(self):
        """Phase 13.7: 父子树（root + 2 children）端到端报告。"""
        registry = GoalRegistry(storage_root=str(self.storage))
        _write_goal_file(self.storage, "parent-1", status="achieved", max_iterations=1)
        _write_goal_file(
            self.storage, "child-1", status="active",
            parent_goal_id="parent-1", max_iterations=1,
        )
        _write_goal_file(
            self.storage, "child-2", status="in_progress",
            parent_goal_id="parent-1", max_iterations=1,
        )
        orch = GoalOrchestrator(registry=registry, max_concurrent=2)
        try:
            report = orch.generate_report("parent-1", format="md")
            # 包含 2 个子 goal 的状态标记
            self.assertIn("child-1", report)
            self.assertIn("child-2", report)
        finally:
            orch.scheduler.shutdown()

    def test_03_e2e_50_node_perf_baseline(self):
        """Phase 13.7 D2 性能基线（B-4 修复：真实 DAG 调度）。

        原 B-4 问题：原测试只调用 generate_report() 序列化现有数据，
        测的是"序列化速度"而非"DAG 调度端到端时间"，不能反映真实性能。

        修复（B-4）：使用真实 orchestrator.run() 调度 50 节点 DAG 端到端执行，
        测量从入口到报告生成的完整耗时。

        拓扑结构：1 root + 49 children（无 depends_on，全部可并行）
        模拟负载：_fast_dispatch 返回 True，不做实际工作（避免 LLM/IO 抖动）
        并发度：max_concurrent=10（默认）
        性能基线：50 节点端到端 < 10s（足够 10x 余量覆盖子进程启动 + 锁竞争）
        """
        from functools import partial
        registry = GoalRegistry(storage_root=str(self.storage))
        # 50 节点：root + 49 children（无 depends_on，全部可并行）
        _write_goal_file(self.storage, "perf-root", status="active", max_iterations=1)
        for i in range(49):
            _write_goal_file(
                self.storage, f"perf-child-{i:02d}", status="active",
                parent_goal_id="perf-root", max_iterations=1,
            )
        # 复用 B-1 修复后的 _module_level_single_dispatch（Pickle 兼容），
        # 用 partial 绑定 project_root，让 orchestrator.run() 通过
        # ProcessPoolExecutor.submit() 真正跨进程调度 50 个节点。
        # 关键：B-4 修复不能简单地传一个 lambda 或闭包（pickle 失败），
        # 必须传可序列化的可调用对象。
        from trae_agent_dispatch_v2 import _module_level_single_dispatch
        bound_dispatch = partial(
            _module_level_single_dispatch,
            project_root=str(self.tmp_dir),
        )
        orch = GoalOrchestrator(registry=registry, max_concurrent=10)
        try:
            # 真实端到端测量：goal.json 加载 + 拓扑排序 + 跨 Goal 复用审计 +
            # 进程池调度 + barrier 等待 + 报告生成
            loop_config = LoopConfig(max_iterations=1, convergence_window=1)
            start = time.time()
            report = orch.run(
                root_goal_id="perf-root",
                dispatch_fn=bound_dispatch,
                loop_config=loop_config,
                project_root=str(self.tmp_dir),
            )
            elapsed = time.time() - start
            # 验证：报告有 50 个 goal
            self.assertEqual(report.resource_stats["total_goals"], 50)
            # 性能基线 < 10s（10x 余量）
            self.assertLess(
                elapsed, 10.0,
                f"50 节点端到端调度 {elapsed:.2f}s 超过 10s 阈值",
            )
        finally:
            orch.scheduler.shutdown()

    def test_04_e2e_51_node_truncation_perf(self):
        """Phase 13.7 D5: 报告序列化时 51 节点应截断为摘要；性能 < 0.5s。

        注意：GoalGraph.MAX_NODES=50 限制 DAG 大小；此处通过直接构造
        GoalExecutionResult 树（不通过 GoalGraph）验证报告层的 D5 截断。
        """
        # 构造 51 节点的树（root + 50 child）
        root = GoalExecutionResult(
            goal_id="big-root", status=GoalStatus.ACHIEVED
        )
        current = root
        for i in range(50):
            child = GoalExecutionResult(
                goal_id=f"big-child-{i:02d}", status=GoalStatus.ACHIEVED
            )
            current.children_results.append(child)
            current = child
        report = GoalOrchestratorReport(
            root_goal_id="big-root",
            total_elapsed_seconds=0.5,
            goal_tree=root,
        )
        start = time.time()
        json_str = report.to_json()
        elapsed = time.time() - start
        parsed = json.loads(json_str)
        # 51 节点 > 50 → 截断
        self.assertTrue(parsed["goal_tree"]["_truncated"])
        self.assertIn("summary", parsed["goal_tree"])
        self.assertLess(
            elapsed, 0.5,
            f"51 节点截断 {elapsed:.2f}s 超过 0.5s 阈值",
        )


# ============================================================================
# 测试 2：DAG 失败路径
# ============================================================================

class TestDAGFailurePaths(unittest.TestCase):
    """Phase 13.7: DAG 各种失败路径（错误传播 + 异常类型）。"""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="p13_fail_"))
        self.storage = self.tmp_dir / "goals"
        self.storage.mkdir()
        self.registry = GoalRegistry(storage_root=str(self.storage))

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_root_goal_missing(self):
        """Phase 13.7: 加载不存在的 root 应抛 GoalNotFoundError。"""
        with self.assertRaises(GoalNotFoundError):
            GoalGraph(self.registry, "nonexistent-root")

    def test_02_depends_on_missing(self):
        """Phase 13.7: depends_on 引用不存在的 goal 应抛 GoalNotFoundError。"""
        _write_goal_file(
            self.storage, "main", status="active",
            depends_on=["phantom-goal"],
        )
        with self.assertRaises(GoalNotFoundError):
            GoalGraph(self.registry, "main")

    def test_03_cycle_simple(self):
        """Phase 13.7: 自环应抛 GoalGraphCycleError。"""
        _write_goal_file(
            self.storage, "loop-1", status="active", depends_on=["loop-1"]
        )
        graph = GoalGraph(self.registry, "loop-1")
        with self.assertRaises(GoalGraphCycleError):
            graph.topological_order()

    def test_04_cycle_path_3_nodes(self):
        """Phase 13.7: 3 节点环 a→b→c→a；环路径应被识别。"""
        for gid, deps in [
            ("loop-1", ["loop-3"]),
            ("loop-2", ["loop-1"]),
            ("loop-3", ["loop-2"]),
        ]:
            _write_goal_file(
                self.storage, gid, status="active", depends_on=deps
            )
        graph = GoalGraph(self.registry, "loop-1")
        with self.assertRaises(GoalGraphCycleError) as ctx:
            graph.topological_order()
        # 错误信息应包含环路径
        msg = str(ctx.exception)
        self.assertIn("loop", msg)

    def test_05_depth_exceeds_limit(self):
        """Phase 13.7: 深度 > MAX_DEPTH 应抛 GoalGraphDepthError。"""
        for i in range(GoalGraph.MAX_DEPTH + 2):
            deps = [f"chain-{i-1}"] if i > 0 else []
            _write_goal_file(
                self.storage,
                f"chain-{i}",
                status="active",
                depends_on=deps,
                parent_goal_id=f"chain-{i-1}" if i > 0 else None,
            )
        with self.assertRaises(GoalGraphDepthError):
            GoalGraph(self.registry, "chain-0")

    def test_06_size_exceeds_limit(self):
        """Phase 13.7: 节点数 > MAX_NODES 应抛 GoalGraphSizeError。"""
        # 星型：root + 50 leaf = 51 节点
        leaf_ids = [f"size-leaf-{i:02d}" for i in range(GoalGraph.MAX_NODES)]
        for leaf_id in leaf_ids:
            _write_goal_file(
                self.storage, leaf_id, status="active", depends_on=[]
            )
        _write_goal_file(
            self.storage, "size-root", status="active", depends_on=leaf_ids
        )
        with self.assertRaises(GoalGraphSizeError):
            GoalGraph(self.registry, "size-root")


# ============================================================================
# 测试 3：resume 端到端
# ============================================================================

class TestResumeEndToEnd(unittest.TestCase):
    """Phase 13.7: GoalResumeManager + registry 真实交互。"""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="p13_resume_e2e_"))
        self.storage = self.tmp_dir / "goals"
        self.storage.mkdir()
        self.registry = GoalRegistry(storage_root=str(self.storage))
        self.mgr = GoalResumeManager(self.registry)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_resume_failed_persists_in_progress(self):
        """Phase 13.7: 续跑 FAILED 后磁盘应更新为 IN_PROGRESS。"""
        _write_goal_file(
            self.storage, "fail-1", status="failed", max_iterations=1
        )
        resumed = self.mgr.resume("fail-1")
        # 重新从磁盘加载验证
        fresh = self.registry.get_goal_or_raise("fail-1")
        self.assertEqual(fresh.status, GoalStatus.IN_PROGRESS)
        self.assertEqual(fresh.resume_count, 1)

    def test_02_resume_force_resets_persists(self):
        """Phase 13.7: 强制续跑 ABANDONED 后磁盘应更新为 IN_PROGRESS。"""
        data = {
            "schema_version": "13.0",
            "goal_id": "aban-1",
            "description": "test",
            "status": "abandoned",
            "resume_count": 3,
            "depends_on": [],
            "iterations": [],
        }
        goal_dir = self.storage / "aban-1"
        goal_dir.mkdir(parents=True, exist_ok=True)
        (goal_dir / "goal.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        resumed = self.mgr.resume("aban-1", force=True)
        fresh = self.registry.get_goal_or_raise("aban-1")
        self.assertEqual(fresh.status, GoalStatus.IN_PROGRESS)
        self.assertEqual(fresh.resume_count, 0)

    def test_03_resume_exceeds_limit_marks_abandoned(self):
        """Phase 13.7: FAILED 超限无 force 应标记 ABANDONED 并持久化。"""
        data = {
            "schema_version": "13.0",
            "goal_id": "exceed-1",
            "description": "test",
            "status": "failed",
            "resume_count": 3,
            "max_resume_count": 3,
            "depends_on": [],
            "iterations": [],
        }
        goal_dir = self.storage / "exceed-1"
        goal_dir.mkdir(parents=True, exist_ok=True)
        (goal_dir / "goal.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        with self.assertRaises(Exception):
            self.mgr.resume("exceed-1", force=False)
        fresh = self.registry.get_goal_or_raise("exceed-1")
        self.assertEqual(fresh.status, GoalStatus.ABANDONED)


# ============================================================================
# 测试 4：迭代复用端到端
# ============================================================================

class TestIterationReuseEndToEnd(unittest.TestCase):
    """Phase 13.7: GoalIterationReuser + 多 sibling 真实嵌入复用。"""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="p13_reuse_e2e_"))
        self.storage = self.tmp_dir / "goals"
        self.storage.mkdir()
        self.registry = GoalRegistry(storage_root=str(self.storage))

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_disabled_reuse_produces_no_iterations(self):
        """Phase 13.7: enabled=False 时不应向 goal 注入 iteration。"""
        # 写 2 个 sibling（已 ACHIEVED）
        for i in range(2):
            _write_goal_file(
                self.storage, f"sib-{i}", status="achieved",
                parent_goal_id="common-parent", max_iterations=1,
            )
        # 目标 goal（无 iteration）
        _write_goal_file(
            self.storage, "target-1", status="active",
            parent_goal_id="common-parent", max_iterations=1,
        )
        reuser = GoalIterationReuser(
            self.registry, enabled=False, reuse_threshold=0.0
        )
        target = self.registry.get_goal_or_raise("target-1")
        similar = reuser.find_similar_iterations(target)
        new_target = reuser.reuse_into(target, similar)
        # 禁用时不应有 seed iteration
        self.assertEqual(len(new_target.iterations), 0)
        self.assertTrue(
            any(e.decision == "skip_disabled" for e in reuser.audit_log)
        )

    def test_02_audit_log_contains_decision_records(self):
        """Phase 13.7: 每次 find_similar_iterations 应记录决策到 audit_log。"""
        _write_goal_file(
            self.storage, "audit-1", status="active",
            parent_goal_id="p1", max_iterations=1,
        )
        reuser = GoalIterationReuser(self.registry, enabled=True)
        target = self.registry.get_goal_or_raise("audit-1")
        reuser.find_similar_iterations(target)
        # 应有 ≥ 1 条审计
        self.assertGreaterEqual(len(reuser.audit_log), 1)
        for entry in reuser.audit_log:
            self.assertIsInstance(entry, CrossGoalReuseEntry)
            # 字段完整
            self.assertIn("decision", entry.to_dict())


# ============================================================================
# 入口
# ============================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
