#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
goal_orchestrator.py — Phase 13 多 Goal 编排核心模块

实现多 Goal 编排（Multi-Goal Orchestration）能力：
1. GoalGraph（DAG 数据结构 + 拓扑算法 + 环检测）
2. GoalScheduler（并发执行 + barrier 同步，ProcessPoolExecutor）
3. GoalResumeManager（续跑状态机 + deepcopy 隔离）
4. GoalIterationReuser（跨 Goal 语义复用 + 审计）
5. GoalOrchestrator（顶层门面）
6. GoalOrchestratorReport（报告生成 + D5 截断）
7. register_goal_executor（V2 零修改集成入口）

V2 集成：通过 register_goal_executor() 桥接（V2 0 行修改）。

设计约束（来自 PHASE13_PLAN.md）：
- 🔴 V2 不修改：本模块通过 register_executor 公共 API 桥接
- 🔴 进程隔离：使用 ProcessPoolExecutor 避免 fcntl 跨进程锁 + GIL 抢占
- 🔴 入参隔离：所有修改 Goal 状态的方法首行 deepcopy(goal)
- 🔴 后向兼容：旧 Goal JSON 无 schema_version 字段时自动迁移到 "13.0"
- 🔴 强类型：跨 Goal 复用基于 paraphrase-multilingual-MiniLM-L12-v2

参考来源：
- [PHASE13_PLAN.md v1.0]
- [PHASE12_FINAL_REPORT.md v1.0]

作者：trae-multi-agent Phase 13
创建日期：2026-06-06
"""

from __future__ import annotations

import json
import logging
import math
import multiprocessing as mp
import os
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# Phase 13.1: 从 loop_goal 导入基础类型（SCHEMA_VERSION、Goal、GoalStatus 等）
# 注意：GoalAggregationStrategy 已在 loop_goal.py 中定义
from loop_goal import (
    SCHEMA_VERSION,
    Goal,
    GoalAggregationStrategy,
    GoalNotFoundError,
    GoalRegistry,
    GoalRegistryError,
    GoalStatus,
    GoalStatusTransitionError,
    IterationResult,
    LoopConfig,
    LoopGoalError,
    LoopGoalExecutor,
    MAX_ITERATIONS_LIMIT,
)


# ============================================================================
# 日志配置
# ============================================================================

logger = logging.getLogger("goal_orchestrator")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ============================================================================
# 异常类（Phase 13.1）
# ============================================================================

class GoalGraphCycleError(LoopGoalError):
    """DAG 存在环（拓扑排序失败时抛）。"""


class GoalGraphSizeError(LoopGoalError):
    """DAG 节点数超过上限（> 50）。"""


class GoalGraphDepthError(LoopGoalError):
    """DAG 深度超过上限（> 5）。"""


class GoalGraphIntegrityError(LoopGoalError):
    """DAG 边端点缺失（goal 未在存储中找到）。"""


# 注意：GoalNotFoundError 已在 loop_goal.py 定义并导出；此处不重复定义

class GoalResumeError(LoopGoalError):
    """续跑错误（不可续跑 / 上限超限 / force 缺失）。"""


class GoalSchedulerTimeoutError(LoopGoalError):
    """调度器超时（DAG 或单 Goal 超时）。"""


# ============================================================================
# 基础数据类（Phase 13.1）
# ============================================================================

@dataclass
class _GraphNode:
    """Goal 包装器，存储图遍历结果（depth）而非修改原始 Goal（C1 修复）。"""
    goal: Goal
    depth: int


@dataclass
class GoalExecutionResult:
    """单 Goal 执行结果（含子 Goal 合并）。"""
    goal_id: str
    status: GoalStatus
    total_iterations: int = 0
    elapsed_seconds: float = 0.0
    children_results: List["GoalExecutionResult"] = field(default_factory=list)
    aggregation_passed: Optional[bool] = None
    error_message: Optional[str] = None

    def count_nodes(self) -> int:
        """递归计算子树节点数（用于 D5 截断判断）。"""
        count = 1
        for child in self.children_results:
            count += child.count_nodes()
        return count


@dataclass
class CrossGoalReuseEntry:
    """跨 Goal 复用审计条目（C4 修复：结构化审计）。

    字段：
    - source_goal_id: 复用的源 Goal ID（"reuse from"）
    - target_goal_id: 被注入的目标 Goal ID（"reuse into"）
    - similarity: 余弦相似度（0.0-1.0）
    - threshold: 复用阈值（决策时使用的 reuse_threshold）
    - decision: 决策类型（reuse / skip_low_similarity / skip_no_parent /
                skip_disabled / skip_embedder_error / skip_iterations_exist）
    - reused_iteration_no: 复用的 iteration_no（-1 表示无）
    - timestamp: ISO 格式时间戳
    - notes: 备注（embedder 名称、错误信息等）
    """
    source_goal_id: str
    target_goal_id: str
    similarity: float
    threshold: float
    decision: str
    reused_iteration_no: int
    timestamp: str
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典。"""
        return {
            "source_goal_id": self.source_goal_id,
            "target_goal_id": self.target_goal_id,
            "similarity": self.similarity,
            "threshold": self.threshold,
            "decision": self.decision,
            "reused_iteration_no": self.reused_iteration_no,
            "timestamp": self.timestamp,
            "notes": self.notes,
        }


@dataclass
class GoalOrchestratorReport:
    """编排报告（JSON + Markdown 双格式；D5 截断 > 50 节点）。

    字段：
    - root_goal_id: 根 Goal ID
    - total_elapsed_seconds: 整 DAG 耗时
    - goal_tree: 根 Goal 执行结果（含子 Goal 树）
    - iteration_reuse_count: 复用 iteration 次数
    - cross_goal_reuse_log: 跨 Goal 复用审计链
    - resource_stats: 资源统计（max_concurrent / total_goals / process_pool_size）
    - REPORT_MAX_NODES: 截断阈值（默认 50）
    """
    root_goal_id: str
    total_elapsed_seconds: float
    goal_tree: GoalExecutionResult
    iteration_reuse_count: int = 0
    cross_goal_reuse_log: List[Dict[str, Any]] = field(default_factory=list)
    resource_stats: Dict[str, Any] = field(default_factory=dict)
    REPORT_MAX_NODES: int = 50  # D5 修复：节点 > 50 截断

    def _count_nodes(self, result: GoalExecutionResult) -> int:
        """递归计算节点数（D5 截断判断）。"""
        return result.count_nodes()

    def _serialize_result(
        self, result: GoalExecutionResult, truncate: bool = False
    ) -> Dict[str, Any]:
        """递归序列化 GoalExecutionResult 为字典。"""
        data = {
            "goal_id": result.goal_id,
            "status": (
                result.status.value
                if hasattr(result.status, "value")
                else str(result.status)
            ),
            "total_iterations": result.total_iterations,
            "elapsed_seconds": result.elapsed_seconds,
            "aggregation_passed": result.aggregation_passed,
            "error_message": result.error_message,
        }
        if not truncate and result.children_results:
            data["children_results"] = [
                self._serialize_result(child) for child in result.children_results
            ]
        return data

    def _render_tree_md(
        self, result: GoalExecutionResult, lines: List[str], depth: int
    ) -> None:
        """递归渲染 Goal 树为 Markdown 列表。"""
        indent = "  " * depth
        if result.status.value == "ACHIEVED":
            marker = "✅"
        elif result.status.value == "FAILED":
            marker = "❌"
        elif result.status.value == "ABANDONED":
            marker = "🚫"
        else:
            marker = "⏳"
        lines.append(
            f"{indent}- {marker} **`{result.goal_id}`** "
            f"({result.status.value}) - "
            f"{result.total_iterations} iters, "
            f"{result.elapsed_seconds:.2f}s"
        )
        if result.error_message:
            lines.append(f"{indent}  - ⚠️ {result.error_message}")
        for child in result.children_results:
            self._render_tree_md(child, lines, depth + 1)

    def to_json(self) -> str:
        """序列化为 JSON 字符串（Phase 13.4 N11 修复：完整实现 + D5 截断）。"""
        total_nodes = self._count_nodes(self.goal_tree)
        if total_nodes > self.REPORT_MAX_NODES:
            # D5 修复：截断为摘要
            goal_tree_data = {
                "_truncated": True,
                "_reason": (
                    f"node_count={total_nodes} > max={self.REPORT_MAX_NODES}"
                ),
                "summary": {
                    "root_goal_id": self.goal_tree.goal_id,
                    "root_status": (
                        self.goal_tree.status.value
                        if hasattr(self.goal_tree.status, "value")
                        else str(self.goal_tree.status)
                    ),
                    "total_iterations": self.goal_tree.total_iterations,
                    "child_count": len(self.goal_tree.children_results),
                },
            }
        else:
            goal_tree_data = self._serialize_result(self.goal_tree)

        report_dict = {
            "root_goal_id": self.root_goal_id,
            "total_elapsed_seconds": self.total_elapsed_seconds,
            "goal_tree": goal_tree_data,
            "iteration_reuse_count": self.iteration_reuse_count,
            "cross_goal_reuse_log": list(self.cross_goal_reuse_log),
            "resource_stats": dict(self.resource_stats),
            "_schema_version": SCHEMA_VERSION,
        }
        return json.dumps(report_dict, ensure_ascii=False, indent=2)

    def to_markdown(self) -> str:
        """序列化为 Markdown（Phase 13.4 N11 修复：完整实现 + D5 截断）。"""
        total_nodes = self._count_nodes(self.goal_tree)
        truncated = total_nodes > self.REPORT_MAX_NODES

        lines: List[str] = []
        lines.append(f"# Goal 编排报告 - `{self.root_goal_id}`")
        lines.append("")
        lines.append("## 元数据")
        lines.append("")
        lines.append("| 字段 | 值 |")
        lines.append("|------|----|")
        lines.append(f"| 根 Goal ID | `{self.root_goal_id}` |")
        lines.append(f"| 总耗时 | {self.total_elapsed_seconds:.2f}s |")
        root_status = (
            self.goal_tree.status.value
            if hasattr(self.goal_tree.status, "value")
            else str(self.goal_tree.status)
        )
        lines.append(f"| 根 Goal 状态 | **{root_status}** |")
        lines.append(f"| 复用 iteration 数 | {self.iteration_reuse_count} |")
        lines.append(f"| 总节点数 | {total_nodes} |")
        if truncated:
            lines.append(
                f"| 截断警告 | 节点数 > {self.REPORT_MAX_NODES}（已截断为摘要） |"
            )
        lines.append("")
        lines.append("## Goal 树")
        lines.append("")
        if truncated:
            lines.append(
                f"- **`{self.goal_tree.goal_id}`** ({root_status})"
            )
            lines.append(
                f"  - _（{total_nodes} 个节点已截断；详见 JSON 报告）_"
            )
        else:
            self._render_tree_md(self.goal_tree, lines, depth=0)
        lines.append("")
        return "\n".join(lines)


# ============================================================================
# GoalGraph：Phase 13.1 DAG 数据结构 + 拓扑排序 + 环检测
# ============================================================================

class GoalGraph:
    """Goal DAG 数据结构 + 拓扑算法。

    Phase 13.1 修复（A4）：
    - _load_recursive 处理前向引用（depends_on 引用未在初始 children 中的 goal）
    - __init__ 末尾做完整性校验（边端点缺失 → GoalGraphIntegrityError）
    - 使用 _GraphNode.depth 包装器而非修改原始 Goal（C1 修复）

    节点上限：50（MAX_NODES）；深度上限：5（MAX_DEPTH）
    """

    MAX_NODES = 50
    MAX_DEPTH = 5

    def __init__(self, registry: GoalRegistry, root_goal_id: str):
        """构造器：加载 root + 所有 descendants + 解析 depends_on 边。

        Args:
            registry: Goal 注册表
            root_goal_id: 根 Goal ID

        Raises:
            GoalNotFoundError: root 不存在
            GoalGraphIntegrityError: 边端点缺失
            GoalGraphSizeError: 节点数 > MAX_NODES
            GoalGraphDepthError: 深度 > MAX_DEPTH
        """
        self.registry = registry
        self.root_goal_id = root_goal_id
        self._graph_nodes: Dict[str, _GraphNode] = {}
        self.nodes: Dict[str, Goal] = {}
        self.edges: Dict[str, List[str]] = {}
        self.reverse_edges: Dict[str, List[str]] = {}
        self.has_cycle = False
        self.cycle_path: Optional[List[str]] = None

        # A4 修复：递归加载 + 完整性校验
        self._load_recursive(root_goal_id, depth=0)
        self._validate_edge_integrity()
        self._validate_size()

    def _load_recursive(self, goal_id: str, depth: int) -> None:
        """DFS 加载 root + 所有 descendants + 解析 depends_on 边（A4 修复）。

        流程：
        1. 读取 goal（不存在 → GoalNotFoundError）
        2. 包装为 _GraphNode 记录 depth
        3. 注册 edges[goal_id] = goal.depends_on
        4. 加载子 Goal（通过 parent_goal_id 反向查找，list_children API）
        5. 递归加载 depends_on 引用的前向 goal（forward reference）

        Args:
            goal_id: 当前加载的 Goal ID
            depth: 距 root 的层级（0 = root）
        """
        if goal_id in self.nodes:
            # 已加载过（避免循环触发）
            return
        try:
            goal = self.registry.get_goal_or_raise(goal_id)
        except (GoalRegistryError, GoalNotFoundError) as e:
            # 包装为 GoalNotFoundError 以便上层捕获
            raise GoalNotFoundError(
                f"Goal {goal_id} 不存在（depends_on 引用了不存在的 Goal）：{e}"
            ) from e

        self._graph_nodes[goal_id] = _GraphNode(goal=goal, depth=depth)
        self.nodes[goal_id] = goal
        self.edges[goal_id] = list(goal.depends_on)
        for dep_id in goal.depends_on:
            self.reverse_edges.setdefault(dep_id, [])
            self.reverse_edges[dep_id].append(goal_id)

        # 加载子 Goal（通过 parent_goal_id 反向查找，Phase 13.1 list_children API）
        children = self.registry.list_children(goal_id)
        self.reverse_edges.setdefault(goal_id, [])
        for child_id in children:
            self.reverse_edges[goal_id].append(child_id)
            self._load_recursive(child_id, depth + 1)

        # A4 修复：递归加载 depends_on 引用的前向 goal（forward reference）
        for dep_id in list(goal.depends_on):
            if dep_id not in self.nodes:
                self._load_recursive(dep_id, depth=depth)

    def _validate_edge_integrity(self) -> None:
        """A4 修复：完整性校验（边端点缺失 → GoalGraphIntegrityError）。"""
        missing_edges: List[Tuple[str, str]] = []
        for src, deps in self.edges.items():
            for dst in deps:
                if dst not in self.nodes:
                    missing_edges.append((src, dst))
        if missing_edges:
            missing_list = ", ".join(f"{s}->{d}" for s, d in missing_edges)
            raise GoalGraphIntegrityError(
                f"DAG 边端点缺失（goal 未在存储中找到）：{missing_list}"
            )

    def _validate_size(self) -> None:
        """节点数 / 深度硬上限校验。"""
        if len(self.nodes) > self.MAX_NODES:
            raise GoalGraphSizeError(
                f"DAG 节点数 {len(self.nodes)} 超过上限 {self.MAX_NODES}"
            )
        max_depth = max(
            (n.depth for n in self._graph_nodes.values()), default=0
        )
        if max_depth > self.MAX_DEPTH:
            raise GoalGraphDepthError(
                f"DAG 深度 {max_depth} 超过上限 {self.MAX_DEPTH}"
            )

    def detect_cycle(self) -> Optional[List[str]]:
        """DFS 三色标记检测环。

        Returns:
            环路径（如 ["A", "B", "C", "A"]）；无环返回 None
        """
        # color: 0 = WHITE（未访问），1 = GRAY（DFS 中），2 = BLACK（完成）
        color: Dict[str, int] = {gid: 0 for gid in self.nodes}
        parent: Dict[str, Optional[str]] = {gid: None for gid in self.nodes}

        for start in self.nodes:
            if color[start] == 0:
                cycle = self._dfs_cycle(start, color, parent)
                if cycle:
                    self.has_cycle = True
                    self.cycle_path = cycle
                    return cycle
        return None

    def _dfs_cycle(
        self,
        start: str,
        color: Dict[str, int],
        parent: Dict[str, Optional[str]],
    ) -> Optional[List[str]]:
        """DFS 找环（递归）。"""
        color[start] = 1  # GRAY
        for neighbor in self.edges.get(start, []):
            if neighbor not in self.nodes:
                # 完整性校验已确保所有边端点存在；这里防御性跳过
                continue
            if color[neighbor] == 1:
                # 回到 GRAY 节点 → 环；重建环路径
                cycle = [neighbor, start]
                cur = parent[start]
                while cur is not None and cur != neighbor:
                    cycle.append(cur)
                    cur = parent[cur]
                if cur == neighbor:
                    cycle.append(neighbor)
                    cycle.reverse()
                    return cycle
            if color[neighbor] == 0:
                parent[neighbor] = start
                result = self._dfs_cycle(neighbor, color, parent)
                if result:
                    return result
        color[start] = 2  # BLACK
        return None

    def topological_order(self) -> List[str]:
        """Kahn 算法拓扑排序。

        Returns:
            拓扑顺序的 goal_id 列表

        Raises:
            GoalGraphCycleError: 存在环
        """
        in_degree: Dict[str, int] = {
            gid: len(self.edges.get(gid, [])) for gid in self.nodes
        }
        queue: deque = deque(
            [gid for gid, d in in_degree.items() if d == 0]
        )
        order: List[str] = []
        while queue:
            gid = queue.popleft()
            order.append(gid)
            for neighbor in self.reverse_edges.get(gid, []):
                if neighbor not in in_degree:
                    continue
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
        if len(order) != len(self.nodes):
            cycle = self.detect_cycle()
            raise GoalGraphCycleError(f"DAG 存在环：{cycle}")
        return order

    def max_depth(self) -> int:
        """返回 DAG 最大深度。"""
        return max(
            (n.depth for n in self._graph_nodes.values()), default=0
        )


# ============================================================================
# GoalScheduler：Phase 13.1 并发执行 + barrier 同步
# ============================================================================

def _execute_goal_in_subprocess(
    goal_id: str,
    goal_dict: Dict[str, Any],
    dispatch_fn: Any,
    loop_config: LoopConfig,
    project_root: str,
    storage_root: str,
) -> Dict[str, Any]:
    """子进程入口函数（必须模块级以支持 pickle）。

    B1 修复：每个子进程独立 GoalRegistry 实例，避免 fcntl 跨进程锁。

    Args:
        goal_id: Goal ID
        goal_dict: Goal.to_dict() 输出（用于跨进程传递）
        dispatch_fn: dispatch 函数（必须可 pickle）
        loop_config: 循环配置
        project_root: 项目根目录
        storage_root: 共享存储根目录

    Returns:
        包含 goal_id / status / total_iterations / elapsed_seconds / error_message 的字典
    """
    goal = Goal.from_dict(goal_dict)
    sub_registry = GoalRegistry(storage_root=storage_root)
    executor = LoopGoalExecutor(sub_registry, loop_config=loop_config)
    start = time.time()
    try:
        result = executor.execute_with_loop_goal(
            task=goal.task_template or goal.description,
            agent_type="goal_orchestrator",
            dispatch_fn=dispatch_fn,
            project_root=project_root,
            loop_config=loop_config,
            goal=goal,
        )
        elapsed = time.time() - start
        return {
            "goal_id": goal_id,
            "status": result.get("status", "failed"),
            "total_iterations": result.get("total_iterations", 0),
            "elapsed_seconds": elapsed,
            "error_message": result.get("error_message"),
        }
    except Exception as e:
        elapsed = time.time() - start
        logger.exception(f"[GoalScheduler] 子进程执行 {goal_id} 失败：{e}")
        return {
            "goal_id": goal_id,
            "status": "failed",
            "total_iterations": 0,
            "elapsed_seconds": elapsed,
            "error_message": str(e),
        }


class GoalScheduler:
    """并发执行 + barrier 同步。

    Phase 13.1 B1 修复：使用 ProcessPoolExecutor 替代 ThreadPoolExecutor
    - 避免 fcntl 跨进程锁 + GIL 抢占的并发死锁风险
    - 跨进程通信：pickle Goal/IterationResult（数据量小，可接受）

    Attributes:
        DEFAULT_MAX_CONCURRENT: 默认 max_concurrent = 10
        DEFAULT_DAG_TIMEOUT_SECONDS: 默认 DAG 整超时 = 60 min
        DEFAULT_PER_GOAL_TIMEOUT_SECONDS: 默认单 Goal 超时 = 30 min
    """

    DEFAULT_MAX_CONCURRENT = 10
    DEFAULT_DAG_TIMEOUT_SECONDS = 60 * 60  # 60 min
    DEFAULT_PER_GOAL_TIMEOUT_SECONDS = 30 * 60  # 30 min

    def __init__(
        self,
        registry: GoalRegistry,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    ):
        """构造器。

        Args:
            registry: Goal 注册表
            max_concurrent: 并发 worker 数（默认 10；D1 优化可设 20）
        """
        self.registry = registry
        self.max_concurrent = max_concurrent
        # B1 修复：ProcessPoolExecutor
        self.executor_pool = ProcessPoolExecutor(max_workers=max_concurrent)
        self._cancel_event = mp.Event()
        self._pause_event = mp.Event()
        self._running_goals: Dict[str, Any] = {}
        self.dag_timeout_seconds = self.DEFAULT_DAG_TIMEOUT_SECONDS
        self.per_goal_timeout_seconds = self.DEFAULT_PER_GOAL_TIMEOUT_SECONDS

    def execute(
        self,
        graph: GoalGraph,
        dispatch_fn_picklable: Any,
        loop_config: LoopConfig,
        project_root: str,
    ) -> Dict[str, GoalExecutionResult]:
        """拓扑顺序执行 DAG（带 barrier 同步 + 跨进程）。

        Args:
            graph: 目标 DAG
            dispatch_fn_picklable: dispatch 函数（必须可 pickle）
            loop_config: 循环配置
            project_root: 项目根目录

        Returns:
            goal_id → GoalExecutionResult 映射

        Raises:
            GoalSchedulerTimeoutError: 整 DAG 超时
        """
        results: Dict[str, GoalExecutionResult] = {}
        order = graph.topological_order()
        completed: Set[str] = set()
        dag_start = time.time()

        # 主调度循环：按拓扑顺序逐层提交
        for goal_id in order:
            # 整 DAG 超时检查
            if time.time() - dag_start > self.dag_timeout_seconds:
                raise GoalSchedulerTimeoutError(
                    f"整 DAG 执行超过 {self.dag_timeout_seconds}s 超时"
                )

            if self._cancel_event.is_set():
                logger.info(
                    f"[GoalScheduler] 收到 cancel_event，停止提交 {goal_id}"
                )
                break

            # 等待所有依赖完成（barrier）
            deps = graph.edges[goal_id]
            while not all(d in completed for d in deps):
                if self._cancel_event.is_set():
                    break
                if time.time() - dag_start > self.dag_timeout_seconds:
                    raise GoalSchedulerTimeoutError(
                        f"整 DAG 执行超过 {self.dag_timeout_seconds}s 超时"
                    )
                # B-2 修复：暂停支持。pause_event 被设置时，调度循环进入
                # 阻塞等待，直到 pause_event 被清除（resume_event）。
                # 关键点：
                # 1. _pause_event.is_set() 不消耗事件，循环中需持续检查
                # 2. 用 0.5s 轮询避免长时间挂起不能响应 cancel/timeout
                # 3. 仅在 barrier 等待阶段生效，不影响已提交子进程的执行
                if self._pause_event.is_set():
                    logger.info(
                        f"[GoalScheduler] Goal {goal_id} 在 barrier 等待时检测到 "
                        f"pause_event，进入暂停状态"
                    )
                    while self._pause_event.is_set():
                        if self._cancel_event.is_set():
                            break
                        if time.time() - dag_start > self.dag_timeout_seconds:
                            raise GoalSchedulerTimeoutError(
                                f"整 DAG 执行超过 {self.dag_timeout_seconds}s 超时"
                            )
                        # 0.5s 轮询：兼顾响应速度与 CPU 占用
                        self._pause_event.wait(timeout=0.5)
                    logger.info(
                        f"[GoalScheduler] Goal {goal_id} 收到 resume_event，"
                        f"恢复调度"
                    )
                time.sleep(0.1)

            # 提交到 ProcessPoolExecutor
            goal = graph.nodes[goal_id]
            future = self.executor_pool.submit(
                _execute_goal_in_subprocess,
                goal_id=goal_id,
                goal_dict=goal.to_dict(),
                dispatch_fn=dispatch_fn_picklable,
                loop_config=loop_config,
                project_root=project_root,
                storage_root=str(self.registry.storage_root),
            )
            self._running_goals[goal_id] = future

        # barrier 等待所有提交的任务完成
        if not self._running_goals:
            return results

        try:
            for future in as_completed(
                list(self._running_goals.values()),
                timeout=self.dag_timeout_seconds,
            ):
                try:
                    result_dict = future.result(
                        timeout=self.per_goal_timeout_seconds
                    )
                    results[result_dict["goal_id"]] = GoalExecutionResult(
                        goal_id=result_dict["goal_id"],
                        status=GoalStatus(result_dict["status"]),
                        total_iterations=result_dict["total_iterations"],
                        elapsed_seconds=result_dict["elapsed_seconds"],
                        error_message=result_dict.get("error_message"),
                    )
                    completed.add(result_dict["goal_id"])
                except Exception as e:
                    # 错误处理：goal_id 通过 future 映射反向查找
                    goal_id = self._find_goal_id_by_future(future)
                    if goal_id:
                        results[goal_id] = GoalExecutionResult(
                            goal_id=goal_id,
                            status=GoalStatus.FAILED,
                            total_iterations=0,
                            elapsed_seconds=0.0,
                            error_message=str(e),
                        )
                        completed.add(goal_id)
        except Exception as e:
            logger.exception(f"[GoalScheduler] barrier 等待异常：{e}")
            raise

        return results

    def _find_goal_id_by_future(self, future: Any) -> Optional[str]:
        """根据 future 反查 goal_id。"""
        for gid, f in self._running_goals.items():
            if f is future:
                return gid
        return None

    def cancel(self) -> Dict[str, str]:
        """取消 DAG 执行（B-3 修复：终止运行中 future + 防止资源泄漏）。

        原 B-3 问题：cancel() 只设置 cancel_event，运行中的子进程会继续执行
        直到自然结束或超时，导致资源（文件锁、内存、CPU）泄漏。

        修复策略（三层防御）：
        1. 设置 cancel_event → 调度循环下次轮询时退出（防止新提交）
        2. 取消所有 PENDING 状态的 future（已排队但未启动的任务）
        3. Shutdown ProcessPoolExecutor（cancel_futures=True + wait=False）
           → 终止所有 RUNNING 子进程，防止资源泄漏
        4. 返回被取消的 goal_id 列表，供上层 GoalOrchestrator 标记 ABANDONED

        Returns:
            被取消的 goal_id 列表（Dict[goal_id, "pending"|"running"）
        """
        # 1. 设置 cancel_event（防止新提交 + 调度循环退出）
        self._cancel_event.set()
        logger.info("[GoalScheduler] cancel_event 已设置")

        # 记录取消的 goal_id 及其状态
        cancelled: Dict[str, str] = {}

        # 2. 取消所有 PENDING 状态的 future
        for goal_id, future in list(self._running_goals.items()):
            try:
                # future.cancel() 仅在 PENDING 状态返回 True；RUNNING 时返回 False
                if future.cancel():
                    cancelled[goal_id] = "pending"
                    logger.info(
                        f"[GoalScheduler] 取消 PENDING future: {goal_id}"
                    )
                else:
                    # RUNNING 状态：标记，待 shutdown 终止
                    cancelled[goal_id] = "running"
                    logger.info(
                        f"[GoalScheduler] 标记 RUNNING future 待终止: {goal_id}"
                    )
            except Exception as e:
                logger.warning(
                    f"[GoalScheduler] 取消 future {goal_id} 异常：{e}"
                )

        # 3. Shutdown ProcessPoolExecutor 以终止 RUNNING 子进程
        #    - wait=False：不等子进程结束（避免 cancel 自身被阻塞）
        #    - cancel_futures=True：取消 PENDING futures（双重保险）
        #    注：shutdown 是幂等的，可多次调用
        try:
            self.executor_pool.shutdown(wait=False, cancel_futures=True)
            logger.info(
                "[GoalScheduler] ProcessPoolExecutor shutdown 完成，"
                "所有 RUNNING 子进程将被终止"
            )
        except Exception as e:
            logger.warning(f"[GoalScheduler] shutdown 异常：{e}")

        # 清空运行中目标跟踪（避免后续操作访问已 shutdown 的 future）
        self._running_goals.clear()

        return cancelled

    def pause(self) -> None:
        """设置 pause_event（B-2 修复：调度循环在 barrier 等待时实际进入暂停）。"""
        self._pause_event.set()
        logger.info("[GoalScheduler] pause_event 已设置")

    def resume_event(self) -> None:
        """清除 pause_event（恢复调度）。"""
        self._pause_event.clear()
        logger.info("[GoalScheduler] pause_event 已清除（resume）")

    def shutdown(self) -> None:
        """关闭 ProcessPoolExecutor（幂等）。"""
        try:
            self.executor_pool.shutdown(wait=True, cancel_futures=True)
        except Exception:
            # shutdown 二次调用可能抛 RuntimeError(Interpreter not shutdown)
            # 或类似异常；幂等忽略即可
            pass


# ============================================================================
# GoalResumeManager：Phase 13.2 续跑状态机
# ============================================================================

class GoalResumeManager:
    """续跑状态机（5 种 status + force 标志）。

    Phase 13.2 修复：
    - A5：ABANDONED + force=True 重置 resume_count → 0
    - N10：FAILED 超限 + force=True 重置（不标记 ABANDONED）
    - B5：所有修改都先 deepcopy 入参
    """

    def __init__(self, registry: GoalRegistry):
        """构造器。

        Args:
            registry: Goal 注册表
        """
        self.registry = registry

    def should_resume(self, goal_id: str, force: bool = False) -> bool:
        """判断是否可续跑。

        决策表：
        - ACTIVE / IN_PROGRESS → True（不递增计数）
        - ACHIEVED → False（终态）
        - FAILED：
            - resume_count < max_resume_count → True
            - resume_count >= max_resume_count → 仅 force=True 时 True
        - ABANDONED → 仅 force=True 时 True
        """
        try:
            goal = self.registry.get_goal_or_raise(goal_id)
        except (GoalRegistryError, GoalNotFoundError, LoopGoalError):
            return False

        if goal.status in (GoalStatus.ACTIVE, GoalStatus.IN_PROGRESS):
            return True
        if goal.status == GoalStatus.ACHIEVED:
            return False
        if goal.status == GoalStatus.FAILED:
            if goal.resume_count < goal.max_resume_count:
                return True
            # 超限：仅 force=True 时返回 True
            return force
        if goal.status == GoalStatus.ABANDONED:
            return force
        return False

    def resume(self, goal_id: str, force: bool = False) -> Goal:
        """执行续跑（B5 修复：deepcopy 入参；A5/N10 修复：force 处理）。

        Args:
            goal_id: Goal ID
            force: 强制续跑（允许重置超限 / ABANDONED 状态）

        Returns:
            续跑后的 Goal（已持久化；新对象，不修改入参）

        Raises:
            GoalResumeError: 续跑失败（不可续跑 / 上限超限 / force 缺失）
        """
        # 1. 读取 goal（不修改）
        original_goal = self.registry.get_goal_or_raise(goal_id)

        # 2. B5 修复：先 deepcopy
        goal = deepcopy(original_goal)

        # 3. 状态机决策
        if goal.status in (GoalStatus.ACTIVE, GoalStatus.IN_PROGRESS):
            # 不递增计数；直接返回（已活跃）
            return goal

        if goal.status == GoalStatus.ACHIEVED:
            raise GoalResumeError(
                f"Goal {goal_id} 已 ACHIEVED，不可续跑"
            )

        if goal.status == GoalStatus.FAILED:
            if goal.resume_count >= goal.max_resume_count:
                if force:
                    # N10 修复：FAILED 超限 + force=True → 重置
                    goal.resume_count = 0
                    goal.status = GoalStatus.IN_PROGRESS
                    self.registry._save_goal_atomic(goal)
                    logger.info(
                        f"[GoalResumeManager] Goal {goal_id} FAILED 超限 + "
                        f"force=True → 重置并续跑"
                    )
                    return goal
                # 未提供 force → 标记 ABANDONED + 抛错
                goal.status = GoalStatus.ABANDONED
                self.registry._save_goal_atomic(goal)
                raise GoalResumeError(
                    f"Goal {goal_id} 续跑次数已达上限 "
                    f"{goal.max_resume_count}，已标记 ABANDONED"
                    f"（用 --force 强制续跑）"
                )
            # 未超限 → 递增 + 置 IN_PROGRESS
            goal.resume_count += 1
            goal.status = GoalStatus.IN_PROGRESS
            self.registry._save_goal_atomic(goal)
            logger.info(
                f"[GoalResumeManager] Goal {goal_id} 续跑成功："
                f"resume_count={goal.resume_count}"
            )
            return goal

        if goal.status == GoalStatus.ABANDONED:
            if not force:
                raise GoalResumeError(
                    f"Goal {goal_id} 已 ABANDONED，续跑需指定 --force 标志"
                )
            # A5 修复：force=True → 重置
            goal.resume_count = 0
            goal.status = GoalStatus.IN_PROGRESS
            self.registry._save_goal_atomic(goal)
            logger.info(
                f"[GoalResumeManager] Goal {goal_id} ABANDONED + "
                f"force=True → 重置并续跑"
            )
            return goal

        raise GoalResumeError(
            f"Goal {goal_id} 处于未知状态 {goal.status}"
        )

    def get_resumable_goals(self, force: bool = False) -> List[Goal]:
        """列出所有可续跑的 goal（B5 修复：返回 deepcopy 避免外部修改影响磁盘）。

        Args:
            force: 传递 force 给 should_resume

        Returns:
            可续跑的 Goal 列表（deepcopy 副本）
        """
        all_goals = self.registry.list_goals()
        resumable: List[Goal] = []
        for goal in all_goals:
            if self.should_resume(goal.goal_id, force=force):
                resumable.append(deepcopy(goal))
        return resumable


# ============================================================================
# GoalIterationReuser：Phase 13.3 跨 Goal 语义复用
# ============================================================================

# 复用 top-K 限制
TOP_K = 3
# 默认相似度阈值（CLI --reuse-threshold）
DEFAULT_REUSE_THRESHOLD = 0.85
# 默认 embedder 模型名（跨语言）
DEFAULT_EMBEDDER_NAME = "paraphrase-multilingual-MiniLM-L12-v2"


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """计算两个 embedding 向量的余弦相似度（0.0-1.0）。

    Args:
        a: embedding 向量 1
        b: embedding 向量 2

    Returns:
        余弦相似度；任一为零向量返回 0.0
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class GoalIterationReuser:
    """跨 Goal 语义复用（基于 Phase 6/7 embedder）。

    Phase 13.3 修复：
    - 可配置 reuse_threshold（CLI --reuse-threshold，默认 0.85）
    - 默认 embedder 为 paraphrase-multilingual-MiniLM-L12-v2（跨语言）
    - 完整审计链 CrossGoalReuseEntry
    - 可全局禁用（CLI --disable-iteration-reuse）
    - top-K 限制（默认 3）
    - B5/N13 修复：reuse_into 不修改入参
    - N24 修复：goal.iterations 非空时跳过（避免污染历史）
    """

    def __init__(
        self,
        registry: GoalRegistry,
        embedder: Optional[Any] = None,
        reuse_threshold: float = DEFAULT_REUSE_THRESHOLD,
        enabled: bool = True,
    ):
        """构造器。

        Args:
            registry: Goal 注册表
            embedder: 自定义 embedder（None → 加载默认跨语言模型）
            reuse_threshold: 相似度阈值（>= 阈值才复用）
            enabled: 全局开关
        """
        self.registry = registry
        self.reuse_threshold = reuse_threshold
        self.enabled = enabled
        self.audit_log: List[CrossGoalReuseEntry] = []

        # B2 修复：默认 embedder 跨语言
        # N25 修复：扩展异常捕获范围，SentenceTransformer 构造器会触发网络下载
        # 在无网络或 huggingface 不可达时抛出网络异常（非 ImportError），需降级到本地 embedder
        if embedder is None:
            embedder_instance: Any = None
            # 第一层：尝试 SentenceTransformer（高精度语义，需网络/本地缓存）
            try:
                from semantic_embedder import (
                    SentenceTransformerEmbedder,
                )
                embedder_instance = SentenceTransformerEmbedder(
                    model_name=DEFAULT_EMBEDDER_NAME,
                )
            except Exception as e_st:
                logger.info(
                    f"[GoalIterationReuser] SentenceTransformerEmbedder 加载失败"
                    f"（{type(e_st).__name__}: {e_st}），将降级到本地 embedder"
                )
                # 第二层：降级到 TFIDF embedder（纯本地，不联网）
                try:
                    from semantic_embedder import (
                        create_default_embedder,
                    )
                    embedder_instance = create_default_embedder()
                except Exception as e_tf:
                    logger.info(
                        f"[GoalIterationReuser] TFIDF embedder 加载失败"
                        f"（{type(e_tf).__name__}: {e_tf}），将降级到 HashingEmbedder"
                    )
                    # 第三层：降级到 HashingEmbedder（纯本地，零外部依赖）
                    try:
                        from semantic_embedder import HashingEmbedder
                        embedder_instance = HashingEmbedder(n_features=512)
                    except Exception as e_hash:
                        logger.warning(
                            f"[GoalIterationReuser] HashingEmbedder 也加载失败"
                            f"（{type(e_hash).__name__}），跨 Goal 复用功能将不可用"
                        )
            self.embedder = embedder_instance
        else:
            self.embedder = embedder

    def find_similar_iterations(
        self, goal: Goal
    ) -> List[Tuple[IterationResult, str, float]]:
        """在同 parent 下找相似的 sibling iteration。

        Args:
            goal: 目标 Goal

        Returns:
            (iteration, source_goal_id, similarity) 元组列表；top-K 限制
        """
        timestamp = datetime.now().isoformat()

        # 全局禁用
        if not self.enabled:
            self.audit_log.append(
                CrossGoalReuseEntry(
                    source_goal_id="",
                    target_goal_id=goal.goal_id,
                    similarity=0.0,
                    threshold=self.reuse_threshold,
                    decision="skip_disabled",
                    reused_iteration_no=-1,
                    timestamp=timestamp,
                    notes="Reuser disabled",
                )
            )
            return []

        # 无 parent
        if not goal.parent_goal_id:
            self.audit_log.append(
                CrossGoalReuseEntry(
                    source_goal_id="",
                    target_goal_id=goal.goal_id,
                    similarity=0.0,
                    threshold=self.reuse_threshold,
                    decision="skip_no_parent",
                    reused_iteration_no=-1,
                    timestamp=timestamp,
                    notes="Goal has no parent_goal_id",
                )
            )
            return []

        # embedder 不可用
        if self.embedder is None:
            self.audit_log.append(
                CrossGoalReuseEntry(
                    source_goal_id="",
                    target_goal_id=goal.goal_id,
                    similarity=0.0,
                    threshold=self.reuse_threshold,
                    decision="skip_embedder_error",
                    reused_iteration_no=-1,
                    timestamp=timestamp,
                    notes="embedder is None",
                )
            )
            return []

        # 找 siblings（同 parent_goal_id 的其他 goal）
        siblings = self.registry.list_children(goal.parent_goal_id)
        candidates: List[Tuple[IterationResult, str, float]] = []

        try:
            goal_embedding = self.embedder.embed(goal.description)
        except Exception as e:
            logger.warning(
                f"[GoalIterationReuser] embed goal.description 失败：{e}"
            )
            self.audit_log.append(
                CrossGoalReuseEntry(
                    source_goal_id="",
                    target_goal_id=goal.goal_id,
                    similarity=0.0,
                    threshold=self.reuse_threshold,
                    decision="skip_embedder_error",
                    reused_iteration_no=-1,
                    timestamp=timestamp,
                    notes=f"embedder error: {e}",
                )
            )
            return []

        for sibling_id in siblings:
            if sibling_id == goal.goal_id:
                continue
            try:
                sibling = self.registry.get_goal_or_raise(sibling_id)
            except (GoalRegistryError, GoalNotFoundError):
                continue
            if sibling.status != GoalStatus.ACHIEVED:
                # 仅复用已 ACHIEVED 的 sibling
                continue

            try:
                sibling_embedding = self.embedder.embed(sibling.description)
            except Exception:
                continue

            similarity = _cosine_similarity(
                goal_embedding, sibling_embedding
            )
            if similarity >= self.reuse_threshold:
                last_iter = (
                    sibling.iterations[-1] if sibling.iterations else None
                )
                if last_iter:
                    candidates.append((last_iter, sibling_id, similarity))
                    self.audit_log.append(
                        CrossGoalReuseEntry(
                            source_goal_id=sibling_id,
                            target_goal_id=goal.goal_id,
                            similarity=similarity,
                            threshold=self.reuse_threshold,
                            decision="reuse",
                            reused_iteration_no=last_iter.iteration_no,
                            timestamp=timestamp,
                            notes=(
                                f"embedding_model={DEFAULT_EMBEDDER_NAME}"
                            ),
                        )
                    )

        candidates.sort(key=lambda x: x[2], reverse=True)
        return candidates[:TOP_K]

    def reuse_into(
        self,
        goal: Goal,
        similar_iters: List[Tuple[IterationResult, str, float]],
    ) -> Goal:
        """注入 seed iteration（B5/N13 修复：不修改入参，返回新对象）。

        N24 修复：goal.iterations 非空时跳过（避免污染历史）。

        Args:
            goal: 目标 Goal
            similar_iters: 候选 iteration 列表

        Returns:
            注入了 seed iteration 的新 Goal（deepcopy 副本）
        """
        # B5 + N13 修复：先 deepcopy
        new_goal = deepcopy(goal)

        if not similar_iters:
            return new_goal

        # N24 修复：已有 iterations → 跳过（避免污染历史）
        if new_goal.iterations:
            return new_goal

        best_iter, source_id, similarity = similar_iters[0]
        seed_outputs: Dict[str, Any] = (
            deepcopy(best_iter.outputs) if best_iter.outputs else {}
        )
        # seed iteration：iteration_no=1（LoopGoalExecutor 的合法范围是 1..N）；
        # 复用关系通过 outputs["__reuse_from__"] / ["__reuse_similarity__"] 标记
        seed = IterationResult(
            iteration_no=1,
            success=True,
            outputs=seed_outputs,
            criteria_met=[],
        )
        seed.outputs["__reuse_from__"] = source_id
        seed.outputs["__reuse_similarity__"] = similarity

        new_goal.iterations.append(seed)
        return new_goal


# ============================================================================
# GoalOrchestrator：Phase 13.4 顶层门面
# ============================================================================

class GoalOrchestrator:
    """多 Goal 编排顶层门面（Phase 13.4）。

    组合组件：
    - registry: Goal 注册表
    - scheduler: GoalScheduler（并发 + barrier）
    - resume_manager: GoalResumeManager（续跑状态机）
    - reuser: GoalIterationReuser（跨 Goal 复用）

    Phase 13.4 N9 修复：构造器支持 reuse_threshold / reuse_enabled 参数。
    """

    def __init__(
        self,
        registry: Optional[GoalRegistry] = None,
        embedder: Optional[Any] = None,
        max_concurrent: int = 10,
        reuse_threshold: float = DEFAULT_REUSE_THRESHOLD,
        reuse_enabled: bool = True,
    ):
        """构造器。

        Args:
            registry: Goal 注册表（None → 默认 .trae/goals）
            embedder: 自定义 embedder
            max_concurrent: 并发数（默认 10；D1 优化可设 20）
            reuse_threshold: 跨 Goal 复用相似度阈值（默认 0.85）
            reuse_enabled: 是否启用跨 Goal 复用（默认 True）
        """
        self.registry = registry if registry is not None else GoalRegistry()
        # 注意：不能用 `registry or GoalRegistry()`，因为 GoalRegistry 定义了
        #   __len__，当目标数 = 0 时 bool(registry) == False，会导致即使传入
        #   registry 也会被替换为默认实例（指向 .trae/goals）。
        self.scheduler = GoalScheduler(
            self.registry, max_concurrent=max_concurrent
        )
        self.resume_manager = GoalResumeManager(self.registry)
        self.reuse_threshold = reuse_threshold
        self.reuse_enabled = reuse_enabled
        self.reuser = GoalIterationReuser(
            self.registry,
            embedder=embedder,
            reuse_threshold=reuse_threshold,
            enabled=reuse_enabled,
        )

    def list_active(self) -> List[Goal]:
        """列出 ACTIVE/IN_PROGRESS root goals。

        Returns:
            root goal 列表
        """
        return self.registry.list_goals(
            statuses=[GoalStatus.ACTIVE, GoalStatus.IN_PROGRESS],
            include_root_only=True,
        )

    def cancel(self, goal_id: str, mark_all_in_dag: bool = True) -> Dict[str, str]:
        """取消 Goal（级联取消子 Goal + 标记 ABANDONED）。

        B-3 修复：调用 scheduler.cancel() 后，把所有被取消的 goal（包括级联子 goal）
        在 registry 中标记为 ABANDONED 并记录原因，避免后续 resume 误判。

        Args:
            goal_id: 根 Goal ID
            mark_all_in_dag: 是否级联标记 DAG 中所有非终态 goal（默认 True）。
                - True（默认）：从 registry 加载 DAG，所有 ACTIVE/IN_PROGRESS/FAILED
                  的 goal 都被标记 ABANDONED。适用于 CLI 调用场景（用户从外部取消）。
                - False：仅标记本进程 _running_goals 中的 goal。
                  适用于测试场景（避免影响其他测试）。

        Returns:
            被取消的 goal_id 列表 {goal_id: "pending"|"running"|"idle"}
        """
        # 1. 调度器取消（终止本进程中所有运行中子进程）
        cancelled = self.scheduler.cancel()

        # 2. 如果 mark_all_in_dag=True，扫描整个 DAG 标记非终态 goal
        #    场景：用户通过 CLI 取消一个不在本进程 _running_goals 中的 goal
        #    （例如：在另一终端启动的 goal，现在想取消它）
        if mark_all_in_dag:
            try:
                from goal_orchestrator import GoalGraph
                graph = GoalGraph(self.registry, goal_id)
                all_goal_ids = graph.topological_order()
            except Exception as e:
                # DAG 加载失败时降级为只标记传入的 goal_id
                logger.warning(
                    f"[GoalOrchestrator] DAG 加载失败，使用单 goal 标记：{e}"
                )
                all_goal_ids = [goal_id]

            for cancelled_goal_id in all_goal_ids:
                # 跳过已经在 cancelled 字典中的（被 scheduler.cancel 处理过）
                if cancelled_goal_id in cancelled:
                    continue
                try:
                    goal = self.registry.get_goal_or_raise(cancelled_goal_id)
                except (GoalRegistryError, GoalNotFoundError, LoopGoalError):
                    continue
                if goal.status in (
                    GoalStatus.ACHIEVED,
                    GoalStatus.ABANDONED,
                ):
                    # 已终态，不重复标记
                    continue
                cancelled[cancelled_goal_id] = "idle"
                updated = deepcopy(goal)
                updated.status = GoalStatus.ABANDONED
                updated.error_message = (
                    f"Goal 在 idle 状态被用户取消（DAG 级联）"
                )
                self.registry._save_goal_atomic(updated)
                logger.info(
                    f"[GoalOrchestrator] Goal {cancelled_goal_id} 标记为 ABANDONED "
                    f"（被用户取消，state=idle）"
                )

        # 3. 标记被取消的 goal 为 ABANDONED（本进程 _running_goals 路径）
        #    使用 deepcopy 避免修改入参影响 registry
        for cancelled_goal_id, state in cancelled.items():
            if state == "idle":
                continue  # 已在步骤 2 处理
            try:
                goal = self.registry.get_goal_or_raise(cancelled_goal_id)
            except (GoalRegistryError, GoalNotFoundError, LoopGoalError):
                continue
            if goal.status in (GoalStatus.ACHIEVED, GoalStatus.ABANDONED):
                # 已终态，不重复标记
                continue
            updated = deepcopy(goal)
            updated.status = GoalStatus.ABANDONED
            updated.error_message = (
                f"Goal 在 {state} 状态被用户取消"
            )
            self.registry._save_goal_atomic(updated)
            logger.info(
                f"[GoalOrchestrator] Goal {cancelled_goal_id} 标记为 ABANDONED "
                f"（被用户取消，state={state}）"
            )

        logger.info(
            f"[GoalOrchestrator] Goal {goal_id} 取消完成，"
            f"受影响 goal 数：{len(cancelled)}"
        )
        return cancelled

    def _build_goal_tree(
        self,
        graph: GoalGraph,
        results: Dict[str, GoalExecutionResult],
        root_goal_id: str,
    ) -> GoalExecutionResult:
        """自底向上构建 Goal 树（递归）。

        Args:
            graph: 目标 DAG
            results: 调度器返回的 goal_id → GoalExecutionResult 映射
            root_goal_id: 当前子树根

        Returns:
            完整 Goal 树
        """
        if root_goal_id not in results:
            raise GoalNotFoundError(
                f"Goal {root_goal_id} 不在 results 中"
            )
        root_result = results[root_goal_id]
        for child_id in graph.reverse_edges.get(root_goal_id, []):
            if child_id not in results:
                # 子 Goal 调度失败：插入占位 FAILED 节点
                root_result.children_results.append(
                    GoalExecutionResult(
                        goal_id=child_id,
                        status=GoalStatus.FAILED,
                        total_iterations=0,
                        elapsed_seconds=0.0,
                        error_message=(
                            "Goal not in results (execution failed)"
                        ),
                    )
                )
                continue
            child_result = self._build_goal_tree(
                graph, results, child_id
            )
            root_result.children_results.append(child_result)
        return root_result

    def generate_report(
        self, root_goal_id: str, format: str = "json"
    ) -> str:
        """生成编排报告（N12 修复：完整实现）。

        Args:
            root_goal_id: 根 Goal ID
            format: "json" 或 "md"

        Returns:
            序列化后的报告字符串

        Raises:
            ValueError: format 非法
        """
        if format not in ("json", "md"):
            raise ValueError(
                f"format 必须是 'json' 或 'md'，收到 {format!r}"
            )

        # 加载 DAG（不存在 → 抛 GoalNotFoundError）
        graph = GoalGraph(self.registry, root_goal_id)
        results: Dict[str, GoalExecutionResult] = {}
        for goal_id in graph.nodes:
            goal = graph.nodes[goal_id]
            results[goal_id] = GoalExecutionResult(
                goal_id=goal_id,
                status=goal.status,
                total_iterations=len(goal.iterations),
                elapsed_seconds=0.0,
            )

        goal_tree = self._build_goal_tree(graph, results, root_goal_id)
        report = GoalOrchestratorReport(
            root_goal_id=root_goal_id,
            total_elapsed_seconds=0.0,
            goal_tree=goal_tree,
            iteration_reuse_count=0,
            cross_goal_reuse_log=[
                e.to_dict() for e in self.reuser.audit_log
            ],
            resource_stats={
                "max_concurrent": self.scheduler.max_concurrent,
                "total_goals": len(graph.nodes),
            },
        )

        return (
            report.to_json() if format == "json" else report.to_markdown()
        )

    def run(
        self,
        root_goal_id: str,
        dispatch_fn: Any,
        loop_config: LoopConfig,
        project_root: str,
    ) -> GoalOrchestratorReport:
        """执行完整编排（N11 修复：完整实现）。

        流程：
        1. 加载 GoalGraph（拓扑排序）
        2. 续跑检查（记录哪些 goal 应跳过）
        3. 跨 Goal 语义复用（注入 seed iteration）
        4. 并发执行（scheduler.execute）
        5. 构建报告

        Args:
            root_goal_id: 根 Goal ID
            dispatch_fn: dispatch 函数（必须可 pickle；用于 ProcessPoolExecutor）
            loop_config: 循环配置
            project_root: 项目根目录

        Returns:
            完整 GoalOrchestratorReport
        """
        dag_start = time.time()

        # 1. 加载 GoalGraph
        graph = GoalGraph(self.registry, root_goal_id)
        order = graph.topological_order()
        logger.info(
            f"[GoalOrchestrator] DAG 拓扑排序完成：{len(order)} 个 goal"
        )

        # 2. 续跑检查（仅记录日志，不修改 goal）
        for goal_id in order:
            if not self.resume_manager.should_resume(goal_id):
                logger.warning(
                    f"[GoalOrchestrator] Goal {goal_id} 跳过续跑"
                )

        # 3. 跨 Goal 语义复用（注入 seed iteration）
        for goal_id in order:
            goal = graph.nodes[goal_id]
            similar_iters = self.reuser.find_similar_iterations(goal)
            if similar_iters:
                new_goal = self.reuser.reuse_into(goal, similar_iters)
                # 用新 goal 替换（仅在内存中；不持久化）
                graph.nodes[goal_id] = new_goal

        # 4. 并发执行
        results = self.scheduler.execute(
            graph=graph,
            dispatch_fn_picklable=dispatch_fn,
            loop_config=loop_config,
            project_root=project_root,
        )

        # 5. 构建报告
        dag_elapsed = time.time() - dag_start
        goal_tree = self._build_goal_tree(graph, results, root_goal_id)
        report = GoalOrchestratorReport(
            root_goal_id=root_goal_id,
            total_elapsed_seconds=dag_elapsed,
            goal_tree=goal_tree,
            iteration_reuse_count=sum(
                1 for e in self.reuser.audit_log if e.decision == "reuse"
            ),
            cross_goal_reuse_log=[
                e.to_dict() for e in self.reuser.audit_log
            ],
            resource_stats={
                "max_concurrent": self.scheduler.max_concurrent,
                "total_goals": len(order),
                "process_pool_size": self.scheduler.max_concurrent,
            },
        )

        logger.info(
            f"[GoalOrchestrator] 完成 root={root_goal_id}, "
            f"elapsed={dag_elapsed:.2f}s, "
            f"reuse_count={report.iteration_reuse_count}"
        )
        return report


# ============================================================================
# register_goal_executor：Phase 13.5 V2 零修改集成入口
# ============================================================================


def register_goal_executor(
    v2_engine: Any,
    orchestrator: "GoalOrchestrator",
) -> None:
    """把 GoalOrchestrator 注册为 V2 的一个 executor（V2 0 行修改）。

    Phase 13.5 N2 修复：完全通过 V2 公开 API register_executor() 桥接。
    V2 是 action-based 工作流；本函数注册一个名为 "execute_goal_subgraph" 的
    executor，使 V2 工作流可以包含 Goal 子图节点。

    Args:
        v2_engine: WorkflowEngineV2 实例
        orchestrator: GoalOrchestrator 实例

    Raises:
        TypeError: v2_engine 不是 WorkflowEngineV2 实例
    """
    # 延迟 import 避免循环依赖
    try:
        from workflow_engine_v2 import WorkflowEngineV2
    except ImportError as e:
        raise TypeError(
            f"无法导入 workflow_engine_v2：{e}"
            f"；请确认在正确的 Python 路径下"
        )

    if not isinstance(v2_engine, WorkflowEngineV2):
        raise TypeError(
            f"v2_engine 必须是 WorkflowEngineV2 实例，"
            f"收到 {type(v2_engine).__name__}"
        )

    def _executor(
        step: Any,
        inputs: Dict[str, Any],
        instance: Any,
    ) -> Dict[str, Any]:
        """V2 executor：执行一个 Goal 子图。

        流程：
        1. 从 inputs / step.inputs 提取 root_goal_id
        2. 从 inputs / instance.variables 提取 loop_config
        3. 委托给 GoalOrchestrator.run
        4. 返回 V2 可消费的 result dict
        """
        root_goal_id = (
            inputs.get("root_goal_id")
            or step.inputs.get("root_goal_id")
        )
        if not root_goal_id:
            raise ValueError(
                f"step.inputs 必须含 'root_goal_id'，收到 {step.inputs}"
            )

        # 从 V2 instance.variables 提取 loop_config
        loop_config_dict = (
            inputs.get("loop_config")
            or instance.variables.get("loop_config", {})
        )
        loop_config = LoopConfig(
            max_iterations=loop_config_dict.get("max_iterations", 10),
            convergence_window=loop_config_dict.get(
                "convergence_window", 3
            ),
        )

        # 委托给 GoalOrchestrator
        report = orchestrator.run(
            root_goal_id=root_goal_id,
            dispatch_fn=instance.variables.get("__dispatch_fn__"),
            loop_config=loop_config,
            project_root=instance.variables.get("project_root", "."),
        )

        return {
            "root_goal_id": report.root_goal_id,
            "status": (
                report.goal_tree.status.value
                if hasattr(report.goal_tree.status, "value")
                else str(report.goal_tree.status)
            ),
            "total_elapsed_seconds": report.total_elapsed_seconds,
            "iterations": report.goal_tree.total_iterations,
        }

    v2_engine.register_executor("execute_goal_subgraph", _executor)
    logger.info(
        "[register_goal_executor] 已注册 executor: execute_goal_subgraph"
    )
