#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dag_visualizer.py — Phase 15 DAG 依赖图可视化器

实现多 Goal 编排 DAG 的可视化能力，输出 3 种格式：
- Mermaid（默认，GitHub Markdown 原生支持）
- JSON（机器友好）
- DOT（Graphviz 兼容，可生成 PNG）

设计约束（来自 PHASE15_PLAN.md）：
- 🔴 只读：可视化器不修改 GoalGraph / Goal / Registry
- 🔴 零 V2 / Phase 13 修改：仅通过公共 API 访问数据
- 🔴 公共 API：仅用 GoalGraph.nodes / edges / reverse_edges /
  topological_order / max_depth / detect_cycle
- 🔴 零 BFS depth 字段访问：使用 _compute_depth_bfs 辅助函数
  （不访问 GoalGraph._graph_nodes 私有属性）
- 🔴 > 50 节点截断：捕获 GoalGraphSizeError 输出截断摘要
- 🔴 环 DAG 跳过：检测环后跳过环边，仍渲染环上节点
- 🔴 路径安全：output_file 必须在 project_root 内

参考来源：
- [PHASE15_PLAN.md v2 架构师 review 通过]
- [PHASE13_FINAL_REPORT.md]
- [PHASE14_FINAL_REPORT.md]

作者：trae-multi-agent Phase 15
创建日期：2026-06-06
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# 路径处理：dag_visualizer 在 scripts/，loop_goal / goal_orchestrator 同目录
# 需要先把 scripts/ 加入 sys.path 才能 import loop_goal / goal_orchestrator
# 因此该 import 必须在 sys.path 操作之后；加 noqa 抑制 E402
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from goal_orchestrator import (  # noqa: E402
    GoalGraph,
    GoalGraphSizeError,
)
from loop_goal import GoalNotFoundError, GoalRegistry, GoalStatus  # noqa: E402


# ============================================================================
# 日志配置
# ============================================================================

logger = logging.getLogger("dag_visualizer")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


# ============================================================================
# 异常类（Phase 15 新增）
# ============================================================================

class InvalidFormatError(ValueError):
    """可视化格式非法（不在 SUPPORTED_FORMATS 列表内）。

    继承自 ValueError 以便 argparse 等上层捕获一致行为。
    """


class GoalGraphVisualizationError(Exception):
    """DAG 可视化错误（路径越界 / 渲染失败）。"""


# ============================================================================
# 内部数据结构
# ============================================================================

@dataclass
class _VisualNode:
    """可视化节点数据结构（与 GoalGraph 节点对应）。

    字段：
    - goal_id: 节点 ID
    - status: GoalStatus 枚举
    - depth: BFS 计算的层级（0 = root）
    - description: 节点描述（已截断）
    - total_iterations: 累计迭代次数
    - error_message: 错误信息（终态 goal 才有）
    - depends_on: 依赖列表（不含父子关系）
    """
    goal_id: str
    status: GoalStatus
    depth: int
    description: str
    total_iterations: int = 0
    error_message: Optional[str] = None
    depends_on: List[str] = field(default_factory=list)

    def __repr__(self) -> str:
        """调试用 repr（M-5 修复：必须包含 goal_id + status）。"""
        return (
            f"_VisualNode(goal_id={self.goal_id!r}, "
            f"status={self.status.value if hasattr(self.status, 'value') else str(self.status)}, "
            f"depth={self.depth})"
        )


@dataclass
class _VisualEdge:
    """可视化边数据结构。

    字段：
    - source: 源节点 ID
    - target: 目标节点 ID
    - edge_type: "parent" | "depends_on"
    """
    source: str
    target: str
    edge_type: str  # "parent" | "depends_on"

    def __repr__(self) -> str:
        """调试用 repr（M-5 修复：必须包含 source + target + type）。"""
        return (
            f"_VisualEdge(source={self.source!r}, target={self.target!r}, "
            f"edge_type={self.edge_type!r})"
        )


# ============================================================================
# Mermaid 转义辅助函数（修复 H-1：完整特殊字符转义）
# ============================================================================

def _escape_mermaid_text(text: str) -> str:
    """转义 Mermaid 节点文本中的特殊字符（修复 H-1）。

    Mermaid 节点内文本中以下字符需要转义：
    - " → &quot;
    - < → &lt;
    - > → &gt;
    - & → &amp;
    - [ → &#91;
    - ] → &#93;
    - { → &#123;
    - } → &#125;
    - | → &#124;
    - ` → &#96;（反引号）
    - \\n → <br/>（Mermaid 节点内换行）

    Args:
        text: 原始文本

    Returns:
        转义后的安全文本
    """
    if not text:
        return ""
    # 先做 HTML 实体转义（处理 <, >, &, "）
    text = html.escape(text, quote=True)
    # 再做 Mermaid 特殊字符转义
    text = text.replace("[", "&#91;").replace("]", "&#93;")
    text = text.replace("{", "&#123;").replace("}", "&#125;")
    text = text.replace("|", "&#124;").replace("`", "&#96;")
    # 换行 → <br/>（Mermaid 节点内换行语法）
    text = text.replace("\n", "<br/>").replace("\r", "")
    return text


def _escape_dot_text(text: str) -> str:
    """转义 DOT (Graphviz) 节点文本中的特殊字符。

    DOT 节点 label 用双引号包裹，需要转义：
    - " → \"
    - \\ → \\\\

    Args:
        text: 原始文本

    Returns:
        转义后的安全文本
    """
    if not text:
        return ""
    # DOT label 用双引号包裹，\n 用字面换行（不是 \\n）
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _sanitize_mermaid_id(goal_id: str) -> str:
    """Mermaid 节点 ID 清理（替换特殊字符为下划线）。

    Mermaid 节点 ID 必须是 [A-Za-z0-9_]+。

    Args:
        goal_id: 原始 goal_id（kebab-case）

    Returns:
        清理后的合法 Mermaid ID
    """
    # kebab-case → snake_case，并把所有非 [A-Za-z0-9_] 替换为 _
    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", goal_id)
    # 数字开头的 ID 前缀加 n（避免 Mermaid 解析失败）
    if sanitized and sanitized[0].isdigit():
        sanitized = "n_" + sanitized
    return sanitized


def _sanitize_dot_id(goal_id: str) -> str:
    """DOT (Graphviz) 节点 ID 清理。

    DOT 节点 ID 用 [A-Za-z0-9_]+，连字符需替换。

    Args:
        goal_id: 原始 goal_id（kebab-case）

    Returns:
        清理后的合法 DOT ID
    """
    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", goal_id)
    if sanitized and sanitized[0].isdigit():
        sanitized = "n_" + sanitized
    return sanitized


# ============================================================================
# 状态 / 形状 / 颜色映射（Phase 15 节点视觉化）
# ============================================================================

# Mermaid classDef 颜色配置（5 种状态）
_MERMAID_STATUS_STYLE: Dict[str, Dict[str, str]] = {
    "active": {
        "fill": "#E3F2FD",
        "stroke": "#1976D2",
        "color": "#000",
    },
    "in_progress": {
        "fill": "#FFF9C4",
        "stroke": "#F57C00",
        "color": "#000",
    },
    "achieved": {
        "fill": "#C8E6C9",
        "stroke": "#388E3C",
        "color": "#000",
    },
    "failed": {
        "fill": "#FFCDD2",
        "stroke": "#C62828",
        "color": "#000",
    },
    "abandoned": {
        "fill": "#CFD8DC",
        "stroke": "#455A64",
        "color": "#000",
    },
}

# DOT 节点 shape 映射（5 种状态）
_DOT_STATUS_SHAPE: Dict[str, str] = {
    "active": "box",
    "in_progress": "box,rounded",
    "achieved": "diamond",
    "failed": "parallelogram",
    "abandoned": "oval",
}

# DOT 节点 fillcolor 映射
_DOT_STATUS_FILL: Dict[str, str] = {
    "active": "#E3F2FD",
    "in_progress": "#FFF9C4",
    "achieved": "#C8E6C9",
    "failed": "#FFCDD2",
    "abandoned": "#CFD8DC",
}


def _status_to_str(status: Any) -> str:
    """将 GoalStatus 枚举或字符串统一转为小写字符串。

    Args:
        status: GoalStatus 枚举或字符串

    Returns:
        小写状态字符串（如 "active" / "in_progress"）
    """
    if hasattr(status, "value"):
        return str(status.value).lower()
    return str(status).lower()


# ============================================================================
# 核心辅助函数
# ============================================================================

def _compute_depth_bfs(graph: GoalGraph) -> Dict[str, int]:
    """BFS 计算每节点 depth（修复 B-1：仅用公共 API）。

    重要：仅访问 GoalGraph 的公共属性（nodes / edges / reverse_edges）
    和 Goal 的公共属性（parent_goal_id / depends_on），不访问
    _graph_nodes 私有属性。这保证零 Phase 13 修改约束。

    算法：
    1. 构建"入边表"incoming[gid] = 指向 gid 的所有节点列表
       - parent 边：gid.parent_goal_id 是入边源
       - depends_on 边：gid.depends_on 列表中的每个 dep_id 是入边源
    2. in_degree[gid] = len(incoming[gid])
    3. BFS 从 in_degree=0 的节点开始，depth = max(parent_depth) + 1
    4. 使用 visited 集合防止环导致无限循环

    Args:
        graph: 已加载的 DAG

    Returns:
        goal_id → depth 映射（root = 0）

    Note:
        该函数完全独立，不修改 graph 的任何状态；纯只读。
    """
    depth_map: Dict[str, int] = {}
    # 1. 构建入边表：incoming[gid] = 所有指向 gid 的节点列表
    #    包括 parent 边（gid.parent_goal_id → gid）
    #    和 depends_on 反向边（gid.depends_on 列表中的每个 dep_id → gid）
    incoming: Dict[str, List[str]] = {gid: [] for gid in graph.nodes}
    for gid in graph.nodes:
        goal = graph.nodes[gid]
        # parent 边：gid 的 parent_goal_id 是入边源
        parent_id = getattr(goal, "parent_goal_id", None)
        if parent_id and parent_id in graph.nodes:
            incoming[gid].append(parent_id)
        # depends_on 边：gid 依赖的所有 dep_id 是入边源
        for dep_id in graph.edges.get(gid, []):
            if dep_id in graph.nodes and dep_id != parent_id:
                # 防御：parent_id 已在 depends_on 中时去重
                incoming[gid].append(dep_id)
    # 2. in_degree = len(incoming[gid])
    in_degree: Dict[str, int] = {
        gid: len(inc) for gid, inc in incoming.items()
    }
    # 3. 初始化：in_degree=0 的节点是 root（无前置），depth=0
    queue: deque = deque(
        [gid for gid, d in in_degree.items() if d == 0]
    )
    for gid in queue:
        depth_map[gid] = 0
    # 4. BFS 传播 depth：每完成一个节点，子节点 depth = max(parent_depth) + 1
    #    visited 集合防止环导致无限循环
    visited: Set[str] = set(queue)
    while queue:
        cur = queue.popleft()
        # 遍历当前节点的所有"reverse"邻居（即依赖当前节点完成的 goal）
        # reverse_edges 包含 BOTH：依赖当前节点完成的 goal + 把当前节点当 parent 的 child
        for child in graph.reverse_edges.get(cur, []):
            if child in visited:
                continue
            new_depth = depth_map[cur] + 1
            if child not in depth_map or depth_map[child] < new_depth:
                depth_map[child] = new_depth
            visited.add(child)
            queue.append(child)
    return depth_map


def _detect_cycle_edges(graph: GoalGraph) -> Set[Tuple[str, str]]:
    """检测环，返回需要跳过的边集合（修复 M-8）。

    算法：
    1. 调用 graph.detect_cycle() 获取环路径（如 ["A", "B", "C", "A"]）
    2. 构造环边集合 {(A,B), (B,C), (C,A)}
    3. 返回用于在渲染时过滤的边集合

    Args:
        graph: 已加载的 DAG

    Returns:
        {(src, dst), ...} 环上的边集合；无环返回空 set
    """
    cycle = graph.detect_cycle()
    if not cycle:
        return set()
    cycle_edges: Set[Tuple[str, str]] = set()
    # cycle 是 List[str]，例如 ["A", "B", "C", "A"]
    # 构造环边集合 {(A,B), (B,C), (C,A)}
    for i in range(len(cycle) - 1):
        cycle_edges.add((cycle[i], cycle[i + 1]))
    return cycle_edges


def _validate_output_path(output_file: str, project_root: str) -> Path:
    """校验输出路径在 project_root 之内（修复 H-3：防路径遍历）。

    Args:
        output_file: 用户指定的输出路径
        project_root: 项目根目录

    Returns:
        解析后的绝对路径（Path 对象）

    Raises:
        GoalGraphVisualizationError: 路径在 project_root 之外 / 不是绝对路径
    """
    output_path = Path(output_file).resolve()
    project_root_abs = Path(project_root).resolve()
    try:
        # Python 3.9+ 推荐写法：检查路径是否在 project_root 之下
        output_path.relative_to(project_root_abs)
    except ValueError:
        raise GoalGraphVisualizationError(
            f"输出路径 {output_path} 必须在项目根目录 {project_root_abs} 之内"
        )
    # 父目录不存在时自动创建（mkdir -p）
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return output_path


# ============================================================================
# MermaidRenderer：Mermaid 输出
# ============================================================================

class MermaidRenderer:
    """Mermaid 格式渲染器。

    输出 flowchart TD（top-down）格式的 Mermaid 图：
    - 节点形状按状态区分（box / diamond / parallelogram / oval）
    - 颜色按状态 classDef 着色
    - 边按类型区分（实线 parent / 虚线 depends_on）
    - subgraph 按 depth 分层
    """

    def render(
        self,
        graph: GoalGraph,
        nodes: Dict[str, _VisualNode],
        edges: List[_VisualEdge],
        desc_max_length: int = 100,
    ) -> str:
        """渲染 Mermaid 图。

        Args:
            graph: 加载的 DAG
            nodes: 节点映射（goal_id → _VisualNode）
            edges: 边列表（_VisualEdge）
            desc_max_length: description 截断长度

        Returns:
            Mermaid 图字符串
        """
        lines: List[str] = []
        # 1. 头部注释
        max_depth = max((n.depth for n in nodes.values()), default=0)
        lines.append("flowchart TD")
        lines.append(
            f"    %% DAG: {graph.root_goal_id} "
            f"({len(nodes)} nodes, max_depth={max_depth})"
        )
        lines.append(
            "    %% Generated by trae-multi-agent v2.7 Phase 15"
        )
        lines.append("")

        # 2. 按 depth 分组（subgraph 分层）
        depth_groups: Dict[int, List[str]] = {}
        for goal_id, node in nodes.items():
            depth_groups.setdefault(node.depth, []).append(goal_id)
        for depth in sorted(depth_groups.keys()):
            goal_ids_in_layer = depth_groups[depth]
            layer_label = f"Layer {depth}"
            if depth == 0:
                layer_label += " (root)"
            lines.append(
                f'    subgraph {layer_label}["{layer_label} '
                f'({len(goal_ids_in_layer)} nodes)"]'
            )
            for goal_id in goal_ids_in_layer:
                node = nodes[goal_id]
                mermaid_id = _sanitize_mermaid_id(goal_id)
                desc = self._truncate_description(
                    node.description, desc_max_length
                )
                # 节点形状按状态选择
                lines.append(
                    f"        {mermaid_id}{self._mermaid_node_shape(goal_id, desc, node)}"
                )
            lines.append("    end")
            lines.append("")

        # 3. 边：parent（实线） + depends_on（虚线）
        parent_edges = [e for e in edges if e.edge_type == "parent"]
        depends_edges = [e for e in edges if e.edge_type == "depends_on"]
        if parent_edges:
            lines.append("    %% Parent -> child edges (solid)")
            for edge in parent_edges:
                src = _sanitize_mermaid_id(edge.source)
                dst = _sanitize_mermaid_id(edge.target)
                lines.append(f"    {src} --> {dst}")
            lines.append("")
        if depends_edges:
            lines.append("    %% DAG depends_on edges (dashed)")
            for edge in depends_edges:
                src = _sanitize_mermaid_id(edge.source)
                dst = _sanitize_mermaid_id(edge.target)
                lines.append(
                    f'    {src} -. "{_escape_mermaid_text("depends on")}" .-> {dst}'
                )
            lines.append("")

        # 4. 状态着色：classDef + class
        lines.append("    %% Status colors")
        for status, style in _MERMAID_STATUS_STYLE.items():
            # Mermaid classDef 名：active → active，in_progress → inProgress
            class_name = self._mermaid_class_name(status)
            lines.append(
                f"    classDef {class_name} "
                f"fill:{style['fill']},"
                f"stroke:{style['stroke']},"
                f"color:{style['color']}"
            )
        lines.append("")

        # 5. 把节点映射到 class
        status_groups: Dict[str, List[str]] = {}
        for goal_id, node in nodes.items():
            status_str = _status_to_str(node.status)
            status_groups.setdefault(status_str, []).append(goal_id)
        for status, goal_ids in status_groups.items():
            class_name = self._mermaid_class_name(status)
            mermaid_ids = [_sanitize_mermaid_id(g) for g in goal_ids]
            lines.append(
                f"    class {','.join(mermaid_ids)} {class_name}"
            )

        return "\n".join(lines)

    def _truncate_description(self, desc: str, max_length: int) -> str:
        """截断 description 到 max_length 字符（M-1 修复）。"""
        if not desc:
            return ""
        if len(desc) <= max_length:
            return desc
        return desc[: max_length - 3] + "..."

    def _mermaid_node_shape(
        self, goal_id: str, desc: str, node: _VisualNode
    ) -> str:
        """根据状态生成 Mermaid 节点形状语法。

        状态 → 形状：
        - active: ["description"] 矩形
        - in_progress: ("description") 圆角矩形
        - achieved: {"description"} 菱形
        - failed: [/"description"/] 平行四边形
        - abandoned: (("description")) 圆形
        """
        status_str = _status_to_str(node.status)
        # 转义描述（处理 CJK / emoji / 特殊字符）
        desc_escaped = _escape_mermaid_text(desc)
        # 加上 iterations 提示（H-5 修复：CJK/emoji 支持）
        iter_text = ""
        if node.total_iterations > 0:
            iter_text = f"<br/>🔄 {node.total_iterations} iters"
        # 终态 goal 附加错误信息
        error_text = ""
        if (
            status_str in ("failed", "abandoned")
            and node.error_message
        ):
            err = self._truncate_description(node.error_message, 80)
            error_text = f"<br/>⚠️ {_escape_mermaid_text(err)}"

        label = f"{_escape_mermaid_text(goal_id)}<br/>📝 {desc_escaped}{iter_text}{error_text}"

        if status_str == "active":
            return f'["{label}"]'
        elif status_str == "in_progress":
            return f'("{label}")'
        elif status_str == "achieved":
            return f'{{"{label}"}}'
        elif status_str == "failed":
            return f'[/"{label}"/]'
        elif status_str == "abandoned":
            return f'(("{label}"))'
        else:
            return f'["{label}"]'

    def _mermaid_class_name(self, status: str) -> str:
        """Mermaid classDef 名称映射（in_progress → inProgress）。"""
        mapping = {
            "active": "active",
            "in_progress": "inProgress",
            "achieved": "achieved",
            "failed": "failed",
            "abandoned": "abandoned",
        }
        return mapping.get(status, "active")

    def render_truncation(
        self, root_goal_id: str, reason: str
    ) -> str:
        """渲染截断摘要（修复 B-2：> 50 节点时输出）。"""
        lines: List[str] = []
        lines.append("flowchart TD")
        lines.append(
            f"    %% DAG: {root_goal_id} (truncated: {reason})"
        )
        lines.append(
            "    %% Please reduce the DAG to 50 nodes or fewer "
            "to see the full graph."
        )
        lines.append("    %% Generated by trae-multi-agent v2.7 Phase 15")
        lines.append("")
        lines.append(
            '    truncated["⚠️ Graph truncated<br/>'
            f'{_escape_mermaid_text(reason)}<br/>'
            "见 JSON 报告获取摘要\"]"
        )
        lines.append("")
        lines.append("    classDef truncatedBox fill:#FFCDD2,stroke:#C62828,color:#000")
        lines.append("    class truncated truncatedBox")
        return "\n".join(lines)


# ============================================================================
# JsonRenderer：JSON 输出
# ============================================================================

class JsonRenderer:
    """JSON 格式渲染器（机器友好）。"""

    def render(
        self,
        graph: GoalGraph,
        nodes: Dict[str, _VisualNode],
        edges: List[_VisualEdge],
        desc_max_length: int = 100,
    ) -> str:
        """渲染 JSON 字符串。

        Args:
            graph: 加载的 DAG
            nodes: 节点映射
            edges: 边列表
            desc_max_length: description 截断长度（M-1 修复：JSON 同样截断）

        Returns:
            JSON 字符串（缩进 2，ensure_ascii=False 支持中文）
        """
        # 1. 统计状态分布
        status_counts: Dict[str, int] = {
            "active": 0,
            "in_progress": 0,
            "achieved": 0,
            "failed": 0,
            "abandoned": 0,
        }
        for node in nodes.values():
            status_str = _status_to_str(node.status)
            status_counts[status_str] = status_counts.get(status_str, 0) + 1

        # 2. max_depth
        max_depth = max((n.depth for n in nodes.values()), default=0)

        # 3. 节点数据
        #    修复：使用 _compute_depth_bfs 后获得的 nodes 列表（已拓扑序）
        #    避免 graph.topological_order() 在环 DAG 时抛 GoalGraphCycleError
        #    排序策略：先按 depth 升序，depth 相同按 goal_id 字典序（稳定排序）
        sorted_nodes = sorted(
            nodes.values(),
            key=lambda n: (n.depth, n.goal_id)
        )
        node_data: List[Dict[str, Any]] = []
        for node in sorted_nodes:
            desc = self._truncate_description(
                node.description, desc_max_length
            )
            node_data.append(
                {
                    "goal_id": node.goal_id,
                    "depth": node.depth,
                    "status": _status_to_str(node.status),
                    "description": desc,
                    "total_iterations": node.total_iterations,
                    "resume_count": 0,  # Phase 13 GoalGraph 未暴露
                    "error_message": node.error_message,
                    "depends_on": list(node.depends_on),
                }
            )

        # 4. 边数据
        edge_data: List[Dict[str, Any]] = [
            {
                "source": e.source,
                "target": e.target,
                "edge_type": e.edge_type,
            }
            for e in edges
        ]

        # 5. 完整报告
        # 修复 H-6：使用 UTC 时间戳（带时区后缀）
        report: Dict[str, Any] = {
            "schema_version": "15.0",
            "format": "json",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "root_goal_id": graph.root_goal_id,
            "summary": {
                "total_nodes": len(nodes),
                "max_depth": max_depth,
                "has_cycle": graph.has_cycle,
                "cycle_path": graph.cycle_path,
                "truncated": False,
                "status_counts": status_counts,
            },
            "nodes": node_data,
            "edges": edge_data,
        }
        return json.dumps(report, ensure_ascii=False, indent=2)

    def _truncate_description(self, desc: str, max_length: int) -> str:
        """截断 description。"""
        if not desc:
            return ""
        if len(desc) <= max_length:
            return desc
        return desc[: max_length - 3] + "..."

    def render_truncation(
        self, root_goal_id: str, reason: str, registry: GoalRegistry
    ) -> str:
        """渲染截断摘要 JSON（修复 B-2：> 50 节点时输出）。

        Args:
            root_goal_id: 根 Goal ID
            reason: 截断原因
            registry: GoalRegistry（用于在截断模式下仍能获取 root goal 的元数据）

        Returns:
            截断摘要 JSON 字符串
        """
        # 尝试从 registry 读取 root goal 的 status（仅 root 节点数据可用）
        root_status = "unknown"
        try:
            goal = registry.get_goal_or_raise(root_goal_id)
            root_status = _status_to_str(goal.status)
        except (GoalNotFoundError, Exception):
            pass

        report: Dict[str, Any] = {
            "schema_version": "15.0",
            "format": "json",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "root_goal_id": root_goal_id,
            "_truncated": True,
            "_reason": reason,
            "summary": {
                "root_goal_id": root_goal_id,
                "root_status": root_status,
                "truncated": True,
            },
            "nodes": [],
            "edges": [],
        }
        return json.dumps(report, ensure_ascii=False, indent=2)


# ============================================================================
# DotRenderer：DOT (Graphviz) 输出
# ============================================================================

class DotRenderer:
    """DOT (Graphviz) 格式渲染器。"""

    def render(
        self,
        graph: GoalGraph,
        nodes: Dict[str, _VisualNode],
        edges: List[_VisualEdge],
        desc_max_length: int = 100,
    ) -> str:
        """渲染 DOT 字符串。

        Args:
            graph: 加载的 DAG
            nodes: 节点映射
            edges: 边列表
            desc_max_length: description 截断长度

        Returns:
            DOT 字符串
        """
        lines: List[str] = []
        # 1. digraph 头部
        max_depth = max((n.depth for n in nodes.values()), default=0)
        lines.append("digraph DAG {")
        lines.append("    rankdir=TB;")
        lines.append(
            f'    label="DAG: {graph.root_goal_id} '
            f'({len(nodes)} nodes, {max_depth + 1} layers)";'
        )
        lines.append("    labelloc=t;")
        lines.append("    fontsize=14;")
        lines.append("    node [style=filled, fontname=\"Helvetica\"];")
        lines.append("")

        # 2. 节点定义
        for goal_id, node in nodes.items():
            dot_id = _sanitize_dot_id(goal_id)
            status_str = _status_to_str(node.status)
            shape = _DOT_STATUS_SHAPE.get(status_str, "box")
            fillcolor = _DOT_STATUS_FILL.get(status_str, "#FFFFFF")
            desc = self._truncate_description(
                node.description, desc_max_length
            )
            label_lines = [
                _escape_dot_text(goal_id),
                f"📝 {_escape_dot_text(desc)}",
            ]
            if node.total_iterations > 0:
                label_lines.append(f"🔄 {node.total_iterations} iters")
            if (
                status_str in ("failed", "abandoned")
                and node.error_message
            ):
                err = self._truncate_description(node.error_message, 60)
                label_lines.append(f"⚠️ {_escape_dot_text(err)}")
            label = "\\n".join(label_lines)
            lines.append(
                f'    {dot_id} [label="{label}", '
                f'shape={shape}, fillcolor="{fillcolor}"];'
            )
        lines.append("")

        # 3. 边定义
        parent_edges = [e for e in edges if e.edge_type == "parent"]
        depends_edges = [e for e in edges if e.edge_type == "depends_on"]
        for edge in parent_edges:
            src = _sanitize_dot_id(edge.source)
            dst = _sanitize_dot_id(edge.target)
            lines.append(f'    {src} -> {dst} [label="parent"];')
        for edge in depends_edges:
            src = _sanitize_dot_id(edge.source)
            dst = _sanitize_dot_id(edge.target)
            lines.append(
                f'    {src} -> {dst} [label="depends_on", style=dashed];'
            )
        lines.append("")

        # 4. 按 depth 分层
        depth_groups: Dict[int, List[str]] = {}
        for goal_id, node in nodes.items():
            depth_groups.setdefault(node.depth, []).append(goal_id)
        for depth in sorted(depth_groups.keys()):
            dot_ids = [_sanitize_dot_id(g) for g in depth_groups[depth]]
            lines.append(
                f"    {{ rank=same; {'; '.join(dot_ids)}; }}"
            )

        lines.append("}")
        return "\n".join(lines)

    def _truncate_description(self, desc: str, max_length: int) -> str:
        """截断 description。"""
        if not desc:
            return ""
        if len(desc) <= max_length:
            return desc
        return desc[: max_length - 3] + "..."

    def render_truncation(
        self, root_goal_id: str, reason: str
    ) -> str:
        """渲染截断摘要 DOT（修复 B-2：> 50 节点时输出）。"""
        lines: List[str] = []
        lines.append("digraph DAG {")
        lines.append(f"    /* TRUNCATED: {reason} */")
        lines.append("    rankdir=TB;")
        lines.append(
            f'    label="DAG: {root_goal_id} (truncated: {reason})";'
        )
        lines.append("    labelloc=t;")
        lines.append("    fontsize=14;")
        lines.append(
            f'    truncated [label="Graph truncated\\n'
            f'{_escape_dot_text(reason)}", '
            'shape=octagon, fillcolor="#FFCDD2"];'
        )
        lines.append("}")
        return "\n".join(lines)


# ============================================================================
# DagVisualizer：顶层 facade
# ============================================================================

class DagVisualizer:
    """DAG 可视化器 facade（Phase 15 新增）。

    组合 Mermaid / JSON / DOT 三个 renderer，对外暴露统一 render() 入口。

    关键行为：
    1. 通过 GoalGraph 公共 API 加载 DAG
    2. BFS 计算 depth（不访问 _graph_nodes 私有属性）
    3. 检测环 → 跳过环边
    4. > 50 节点 → 输出截断摘要（捕获 GoalGraphSizeError）
    5. 委托给对应 Renderer 渲染
    """

    SUPPORTED_FORMATS = ("mermaid", "json", "dot")
    DEFAULT_FORMAT = "mermaid"
    DEFAULT_DESC_MAX_LENGTH = 100  # M-1 修复：默认 100 字符

    def __init__(self, registry: GoalRegistry):
        """构造器。

        Args:
            registry: Goal 注册表（公共 API）
        """
        self.registry = registry
        self._mermaid = MermaidRenderer()
        self._json = JsonRenderer()
        self._dot = DotRenderer()

    def render(
        self,
        root_goal_id: str,
        format: str = DEFAULT_FORMAT,
        include_error_tooltip: bool = True,
        desc_max_length: int = DEFAULT_DESC_MAX_LENGTH,
    ) -> str:
        """渲染 DAG 为指定格式字符串。

        Args:
            root_goal_id: 根 Goal ID
            format: 输出格式（"mermaid" | "json" | "dot"）
            include_error_tooltip: 是否在节点标注 error_message
                （终态 failed/abandoned 时）
            desc_max_length: description 截断长度

        Returns:
            序列化后的字符串

        Raises:
            InvalidFormatError: format 非法
            GoalNotFoundError: root_goal_id 不存在
            GoalGraphIntegrityError: 边端点缺失
            GoalGraphDepthError: 深度 > MAX_DEPTH(5)
        """
        # 1. 校验 format
        if format not in self.SUPPORTED_FORMATS:
            raise InvalidFormatError(
                f"format 必须是 {self.SUPPORTED_FORMATS} 之一，"
                f"收到 {format!r}"
            )

        # 2. 加载 DAG（公共 API），捕获 GoalGraphSizeError 输出截断摘要
        try:
            graph = GoalGraph(self.registry, root_goal_id)
        except GoalGraphSizeError as e:
            # 修复 B-2：>50 节点不抛异常，返回截断摘要
            logger.warning(
                f"[DagVisualizer] DAG 节点数超过 50，输出截断摘要：{e}"
            )
            reason = str(e)
            if format == "mermaid":
                return self._mermaid.render_truncation(root_goal_id, reason)
            elif format == "json":
                return self._json.render_truncation(
                    root_goal_id, reason, self.registry
                )
            else:  # dot
                return self._dot.render_truncation(root_goal_id, reason)
        # 3. 构造 _VisualNode 列表
        try:
            depth_map = _compute_depth_bfs(graph)
        except Exception as e:
            logger.exception(f"[DagVisualizer] BFS depth 计算失败：{e}")
            raise

        nodes: Dict[str, _VisualNode] = {}
        for goal_id, goal in graph.nodes.items():
            # 状态字符串仅在日志中需要展示一次（调试用），此处不重复使用
            desc = getattr(goal, "description", "") or ""
            error_msg = getattr(goal, "error_message", None) or None
            iterations = getattr(goal, "iterations", []) or []
            nodes[goal_id] = _VisualNode(
                goal_id=goal_id,
                status=goal.status,
                depth=depth_map.get(goal_id, 0),
                description=desc,
                total_iterations=len(iterations),
                error_message=error_msg if include_error_tooltip else None,
                depends_on=list(getattr(goal, "depends_on", []) or []),
            )

        # 4. 构造 _VisualEdge 列表
        #    parent 边：reverse_edges 包含所有 parent→child 关系
        #    depends_on 边：goal.depends_on 列表
        #    环边：detect_cycle 检测到的环边需要跳过（修复 M-8）
        try:
            cycle_edges = _detect_cycle_edges(graph)
        except Exception as e:
            logger.warning(f"[DagVisualizer] 环检测失败：{e}")
            cycle_edges = set()

        edges: List[_VisualEdge] = []
        # 4.1 parent 边：从 reverse_edges 中提取
        #     reverse_edges[parent_id] = [child_id, ...]
        for parent_id, child_ids in graph.reverse_edges.items():
            # reverse_edges 也包含 depends_on 反向边，需要区分
            # 只有当 child 的 parent_goal_id == parent_id 时才是 parent 边
            for child_id in child_ids:
                # 跳过环边
                if (parent_id, child_id) in cycle_edges:
                    continue
                # 检查 child 是否真的是 parent 的子 goal
                child_goal = graph.nodes.get(child_id)
                if child_goal and getattr(
                    child_goal, "parent_goal_id", None
                ) == parent_id:
                    edges.append(
                        _VisualEdge(
                            source=parent_id,
                            target=child_id,
                            edge_type="parent",
                        )
                    )
        # 4.2 depends_on 边：从 goal.depends_on 提取
        for goal_id, deps in graph.edges.items():
            for dep_id in deps:
                if (goal_id, dep_id) in cycle_edges:
                    continue
                edges.append(
                    _VisualEdge(
                        source=goal_id,
                        target=dep_id,
                        edge_type="depends_on",
                    )
                )

        # 5. 委托给对应 Renderer
        if format == "mermaid":
            return self._mermaid.render(
                graph, nodes, edges, desc_max_length
            )
        elif format == "json":
            return self._json.render(
                graph, nodes, edges, desc_max_length
            )
        else:  # dot
            return self._dot.render(
                graph, nodes, edges, desc_max_length
            )

    def write_to_file(
        self,
        root_goal_id: str,
        output_file: str,
        project_root: str,
        format: str = DEFAULT_FORMAT,
        desc_max_length: int = DEFAULT_DESC_MAX_LENGTH,
    ) -> str:
        """渲染并写入文件（路径安全校验在内部完成）。

        Args:
            root_goal_id: 根 Goal ID
            output_file: 输出文件路径（必须在 project_root 内）
            project_root: 项目根目录
            format: 输出格式
            desc_max_length: description 截断长度

        Returns:
            写入的文件绝对路径

        Raises:
            GoalGraphVisualizationError: 路径越界
        """
        # 1. 路径安全校验（修复 H-3）
        output_path = _validate_output_path(output_file, project_root)
        # 2. 渲染
        content = self.render(
            root_goal_id, format=format, desc_max_length=desc_max_length
        )
        # 3. 写入
        output_path.write_text(content, encoding="utf-8")
        logger.info(
            f"[DagVisualizer] 已写入文件：{output_path} "
            f"({len(content)} 字符，format={format})"
        )
        return str(output_path)
