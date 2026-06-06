#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_dag_visualizer_integration.py — Phase 15 DAG 可视化器集成测试

测试覆盖（合计 ~14 个用例）：
- TestEndToEndVisualization（6 个）：端到端渲染各类型 DAG
- TestCliFlags（4 个）：CLI 参数解析
- TestCliMutexRules（2 个）：互斥规则（修复 B-5）
- TestCliExecution（3 个）：CLI 入口函数执行（含路径安全）

作者：trae-multi-agent Phase 15
创建日期：2026-06-06
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

# 路径处理：tests 在 scripts/tests/，模块在 scripts/ 下
# 因此 dag_visualizer / goal_orchestrator 等 import 必须在 sys.path 操作之后
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(TESTS_DIR)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from loop_goal import (  # noqa: E402
    GoalRegistry,
)

from dag_visualizer import (  # noqa: E402
    DagVisualizer,
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
    error_message: Optional[str] = None,
) -> None:
    """辅助：直接写一个 goal.json。"""
    if depends_on is None:
        depends_on = []
    goal_dir = storage / goal_id
    goal_dir.mkdir(parents=True, exist_ok=True)
    data: Dict[str, Any] = {
        "schema_version": "13.0",
        "goal_id": goal_id,
        "description": description,
        "status": status,
        "depends_on": depends_on,
        "parent_goal_id": parent_goal_id,
    }
    if error_message:
        data["error_message"] = error_message
    (goal_dir / "goal.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


def _build_3_level_tree(storage: Path) -> None:
    """3 层树：root + 2 children + 4 grandchildren。"""
    _write_goal_file(storage, "e2e-root", description="e2e root goal")
    _write_goal_file(
        storage, "e2e-child-1",
        parent_goal_id="e2e-root",
        description="child 1",
        status="in_progress",
    )
    _write_goal_file(
        storage, "e2e-child-2",
        parent_goal_id="e2e-root",
        description="child 2",
        status="achieved",
    )
    _write_goal_file(
        storage, "e2e-gc-1",
        parent_goal_id="e2e-child-1",
        description="gc 1",
        status="achieved",
    )
    _write_goal_file(
        storage, "e2e-gc-2",
        parent_goal_id="e2e-child-1",
        description="gc 2",
        status="active",
    )
    _write_goal_file(
        storage, "e2e-gc-3",
        parent_goal_id="e2e-child-2",
        description="gc 3",
        status="active",
    )
    _write_goal_file(
        storage, "e2e-gc-4",
        parent_goal_id="e2e-child-2",
        description="gc 4",
        status="failed",
        error_message="max iterations exceeded",
    )


# ============================================================================
# Test 1: 端到端可视化（6 个）
# ============================================================================

class TestEndToEndVisualization(unittest.TestCase):
    """Phase 15: 端到端可视化测试。"""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="p15_e2e_"))
        self.storage = self.tmp_dir / ".trae" / "goals"
        self.storage.mkdir(parents=True)
        self.registry = GoalRegistry(storage_root=str(self.storage))
        self.visualizer = DagVisualizer(self.registry)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_e2e_root_only_visualize(self):
        """单 root goal → 3 种格式都正确。"""
        _write_goal_file(
            self.storage, "solo-root", description="only node"
        )
        for fmt in ("mermaid", "json", "dot"):
            output = self.visualizer.render("solo-root", format=fmt)
            assert "solo-root" in output
            if fmt == "mermaid":
                assert "flowchart TD" in output
            elif fmt == "json":
                data = json.loads(output)
                assert data["root_goal_id"] == "solo-root"
            else:  # dot
                assert "digraph DAG" in output

    def test_02_e2e_3_level_tree_visualize(self):
        """root + 2 children + 4 grandchildren → 3 格式正确。"""
        _build_3_level_tree(self.storage)
        for fmt in ("mermaid", "json", "dot"):
            output = self.visualizer.render("e2e-root", format=fmt)
            assert "e2e-root" in output
            assert "e2e-child-1" in output
            assert "e2e-gc-4" in output  # 包含 failed 状态
            if fmt == "json":
                data = json.loads(output)
                assert data["summary"]["total_nodes"] == 7
                assert data["summary"]["max_depth"] == 2

    def test_03_e2e_diamond_visualize(self):
        """A->B,C->D 钻石依赖 → 渲染含 parent + depends_on 边。"""
        _write_goal_file(self.storage, "dia-a", description="a")
        _write_goal_file(
            self.storage, "dia-b",
            parent_goal_id="dia-a",
            description="b",
        )
        _write_goal_file(
            self.storage, "dia-c",
            parent_goal_id="dia-a",
            description="c",
        )
        _write_goal_file(self.storage, "dia-d", description="d (shared)")
        for gid in ("dia-b", "dia-c"):
            data = json.loads(
                (self.storage / gid / "goal.json").read_text(encoding="utf-8")
            )
            data["depends_on"] = ["dia-d"]
            (self.storage / gid / "goal.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
        # JSON 输出应包含 depends_on 边
        output = self.visualizer.render("dia-a", format="json")
        data = json.loads(output)
        depends_edges = [
            e for e in data["edges"] if e["edge_type"] == "depends_on"
        ]
        assert len(depends_edges) >= 2  # b→d, c→d

    def test_04_e2e_complex_dag_visualize(self):
        """20 节点混合（parent + depends_on）→ 渲染稳定。"""
        _write_goal_file(self.storage, "cx-root", description="complex root")
        for i in range(19):
            status = (
                "achieved" if i % 3 == 0 else
                "in_progress" if i % 3 == 1 else "active"
            )
            _write_goal_file(
                self.storage, f"cx-c-{i:02d}",
                parent_goal_id="cx-root",
                description=f"child {i}",
                status=status,
            )
        # 渲染 3 种格式
        for fmt in ("mermaid", "json", "dot"):
            output = self.visualizer.render("cx-root", format=fmt)
            assert "cx-root" in output
        # JSON 统计验证
        output = self.visualizer.render("cx-root", format="json")
        data = json.loads(output)
        assert data["summary"]["total_nodes"] == 20

    def test_05_e2e_51_node_truncation(self):
        """51 节点触发截断摘要（不抛异常，修复 B-2）。"""
        _write_goal_file(self.storage, "trunc-root", description="trunc root")
        for i in range(50):  # root + 50 children = 51
            _write_goal_file(
                self.storage, f"trunc-c-{i:02d}",
                parent_goal_id="trunc-root",
                description=f"child {i}",
                status="active",
            )
        # 不应抛 GoalGraphSizeError
        for fmt in ("mermaid", "json", "dot"):
            output = self.visualizer.render("trunc-root", format=fmt)
            assert "truncated" in output.lower() or "TRUNCATED" in output

    def test_06_e2e_cycle_warning(self):
        """构造环 DAG → JSON 包含 has_cycle=true（不抛异常）。"""
        # 构造 a -> b -> c -> a 的环（用 depends_on）
        _write_goal_file(self.storage, "cy-a", description="a")
        _write_goal_file(self.storage, "cy-b", description="b")
        _write_goal_file(self.storage, "cy-c", description="c")
        # a depends on b
        data_a = json.loads(
            (self.storage / "cy-a" / "goal.json").read_text(encoding="utf-8")
        )
        data_a["depends_on"] = ["cy-b"]
        (self.storage / "cy-a" / "goal.json").write_text(
            json.dumps(data_a, ensure_ascii=False), encoding="utf-8"
        )
        # b depends on c
        data_b = json.loads(
            (self.storage / "cy-b" / "goal.json").read_text(encoding="utf-8")
        )
        data_b["depends_on"] = ["cy-c"]
        (self.storage / "cy-b" / "goal.json").write_text(
            json.dumps(data_b, ensure_ascii=False), encoding="utf-8"
        )
        # c depends on a（形成环）
        data_c = json.loads(
            (self.storage / "cy-c" / "goal.json").read_text(encoding="utf-8")
        )
        data_c["depends_on"] = ["cy-a"]
        (self.storage / "cy-c" / "goal.json").write_text(
            json.dumps(data_c, ensure_ascii=False), encoding="utf-8"
        )
        # 渲染 JSON：has_cycle=True，环边被跳过，但节点保留
        output = self.visualizer.render("cy-a", format="json")
        data = json.loads(output)
        assert data["summary"]["has_cycle"] is True
        goal_ids = [n["goal_id"] for n in data["nodes"]]
        assert "cy-a" in goal_ids
        assert "cy-b" in goal_ids
        assert "cy-c" in goal_ids


# ============================================================================
# Test 2: CLI 参数解析（4 个）
# ============================================================================

class TestCliFlags(unittest.TestCase):
    """Phase 15: CLI 参数解析测试。"""

    def setUp(self):
        # CLI 模块导入（避免重复 import）
        from trae_agent_dispatch_v2 import parse_arguments
        self.parse_args = parse_arguments

    def test_14_goal_graph_flag(self):
        """--goal-graph <id> 解析到 args.goal_graph。"""
        # 模拟 sys.argv
        old_argv = sys.argv
        try:
            sys.argv = [
                "trae_agent_dispatch_v2.py",
                "--goal-graph", "my-goal-id",
                "--project-root", "/tmp",
            ]
            args = self.parse_args()
            assert args.goal_graph == "my-goal-id"
        finally:
            sys.argv = old_argv

    def test_15_goal_graph_format_flag(self):
        """--goal-graph-format json 解析正确。"""
        old_argv = sys.argv
        try:
            sys.argv = [
                "trae_agent_dispatch_v2.py",
                "--goal-graph", "g1",
                "--goal-graph-format", "json",
                "--project-root", "/tmp",
            ]
            args = self.parse_args()
            assert args.goal_graph_format == "json"
        finally:
            sys.argv = old_argv

    def test_16_goal_graph_output_flag(self):
        """--goal-graph-output /tmp/x.md 解析正确。"""
        old_argv = sys.argv
        try:
            sys.argv = [
                "trae_agent_dispatch_v2.py",
                "--goal-graph", "g1",
                "--goal-graph-output", "/tmp/x.md",
                "--project-root", "/tmp",
            ]
            args = self.parse_args()
            assert args.goal_graph_output == "/tmp/x.md"
        finally:
            sys.argv = old_argv

    def test_17_goal_graph_desc_max_flag(self):
        """--goal-graph-desc-max 200 解析正确（修复 M-1）。"""
        old_argv = sys.argv
        try:
            sys.argv = [
                "trae_agent_dispatch_v2.py",
                "--goal-graph", "g1",
                "--goal-graph-desc-max", "200",
                "--project-root", "/tmp",
            ]
            args = self.parse_args()
            assert args.goal_graph_desc_max == 200
        finally:
            sys.argv = old_argv


# ============================================================================
# Test 3: CLI 互斥规则（修复 B-5，2 个）
# ============================================================================

class TestCliMutexRules(unittest.TestCase):
    """Phase 15: CLI 互斥规则测试（修复 B-5）。

    通过 subprocess 调用 CLI，验证：
    - --goal-graph + --goal-cancel 互斥
    - --goal-graph + --multi-goal 互斥
    """

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="p15_mutex_"))
        # 创建必要目录结构
        self.project_root = self.tmp_dir
        (self.project_root / ".trae" / "goals").mkdir(parents=True)
        # 写一个简单 goal
        goals_dir = self.project_root / ".trae" / "goals"
        (goals_dir / "test-goal").mkdir()
        (goals_dir / "test-goal" / "goal.json").write_text(
            json.dumps(
                {
                    "schema_version": "13.0",
                    "goal_id": "test-goal",
                    "description": "test",
                    "status": "active",
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_18_cli_mutex_goal_graph_with_cancel(self):
        """--goal-graph + --goal-cancel → sys.exit(1)。"""
        cmd = [
            sys.executable,
            str(Path(SCRIPTS_DIR) / "trae_agent_dispatch_v2.py"),
            "--goal-graph", "test-goal",
            "--goal-cancel", "test-goal",
            "--project-root", str(self.project_root),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10
        )
        # 应以非 0 退出（互斥校验失败）
        assert result.returncode != 0, (
            f"应退出失败，实际 returncode={result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        # 错误信息应包含"互斥"
        combined = result.stdout + result.stderr
        assert "互斥" in combined or "mutex" in combined.lower()

    def test_19_cli_mutex_goal_graph_with_multi_goal(self):
        """--goal-graph + --multi-goal → sys.exit(1)。"""
        cmd = [
            sys.executable,
            str(Path(SCRIPTS_DIR) / "trae_agent_dispatch_v2.py"),
            "--goal-graph", "test-goal",
            "--multi-goal", "test-goal",
            "--project-root", str(self.project_root),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10
        )
        assert result.returncode != 0, (
            f"应退出失败，实际 returncode={result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "互斥" in combined or "mutex" in combined.lower()


# ============================================================================
# Test 4: CLI 入口函数执行（3 个）
# ============================================================================

class TestCliExecution(unittest.TestCase):
    """Phase 15: CLI 入口函数执行测试（含路径安全）。"""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="p15_exec_"))
        self.project_root = self.tmp_dir
        (self.project_root / ".trae" / "goals").mkdir(parents=True)
        goals_dir = self.project_root / ".trae" / "goals"
        for gid in ("cli-root", "cli-child-1", "cli-child-2"):
            (goals_dir / gid).mkdir()
            (goals_dir / gid / "goal.json").write_text(
                json.dumps(
                    {
                        "schema_version": "13.0",
                        "goal_id": gid,
                        "description": f"{gid} desc",
                        "status": (
                            "active" if gid == "cli-root" else
                            "achieved" if gid == "cli-child-1" else
                            "in_progress"
                        ),
                        "parent_goal_id": (
                            "cli-root" if "child" in gid else None
                        ),
                    }
                ),
                encoding="utf-8",
            )

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_20_cli_dispatch_with_graph(self):
        """通过 dispatch_agent_v2_with_goal_graph() 调用成功。"""
        from trae_agent_dispatch_v2 import (
            dispatch_agent_v2_with_goal_graph,
        )
        success = dispatch_agent_v2_with_goal_graph(
            root_goal_id="cli-root",
            project_root=str(self.project_root),
            format="mermaid",
        )
        assert success is True

    def test_21_cli_dispatch_to_file(self):
        """output_file 参数写入文件。"""
        from trae_agent_dispatch_v2 import (
            dispatch_agent_v2_with_goal_graph,
        )
        output_file = str(self.project_root / "viz.mmd")
        success = dispatch_agent_v2_with_goal_graph(
            root_goal_id="cli-root",
            project_root=str(self.project_root),
            format="mermaid",
            output_file=output_file,
        )
        assert success is True
        # 文件应被创建
        assert Path(output_file).exists()
        content = Path(output_file).read_text(encoding="utf-8")
        assert "flowchart TD" in content

    def test_22_cli_dispatch_nonexistent_goal(self):
        """root_id 不存在 → 返回 False。"""
        from trae_agent_dispatch_v2 import (
            dispatch_agent_v2_with_goal_graph,
        )
        success = dispatch_agent_v2_with_goal_graph(
            root_goal_id="nonexistent-goal",
            project_root=str(self.project_root),
            format="mermaid",
        )
        assert success is False

    def test_23_cli_dispatch_to_file_outside_project_root(self):
        """--goal-graph-output ../../tmp/x → 报错（修复 H-3）。

        注意：dispatch_agent_v2_with_goal_graph 内部调用 _validate_output_path
        会抛 GoalGraphVisualizationError，但被捕获后返回 False。
        """
        from trae_agent_dispatch_v2 import (
            dispatch_agent_v2_with_goal_graph,
        )
        # 尝试写入 project_root 之外的文件
        bad_output = str(
            self.project_root / ".." / ".." / "tmp" / "evil.mmd"
        )
        success = dispatch_agent_v2_with_goal_graph(
            root_goal_id="cli-root",
            project_root=str(self.project_root),
            format="mermaid",
            output_file=bad_output,
        )
        # 应返回 False（路径越界被 catch）
        assert success is False
        # 文件不应被创建
        assert not Path(bad_output).exists()


if __name__ == "__main__":
    unittest.main(verbosity=2)
