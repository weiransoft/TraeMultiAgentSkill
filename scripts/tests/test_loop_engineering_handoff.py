"""HandoffAdapter 单元测试。

测试目标：
- create_work_items 根据 loop_type 生成正确 agent_type 的 HandoffItem。
- execute 使用自定义 executor 时返回成功，且结果包含完整字段。
- 无 executor 且无 dispatcher_adapter 时返回失败，不伪造成功。
- 空 items 时返回失败。
- config.test_command 会被真实运行并记录结果。

所有使用 project_root 的测试均使用真实临时目录，避免 /tmp/project 不存在
导致 subprocess / git / 安全扫描等行为不一致。
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from loop_engineering.handoff_adapter import HandoffAdapter
from loop_engineering.models import (
    DiscoveryResult,
    EvaluatorMode,
    LoopEngineeringConfig,
    LoopType,
)


class TestHandoffAdapter(unittest.TestCase):
    """测试 HandoffAdapter 的工作项生成与执行。"""

    def setUp(self):
        """每个测试前创建真实临时项目目录。"""
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        """每个测试后清理临时目录。"""
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_config(self, **overrides) -> LoopEngineeringConfig:
        """构造一个用于测试的 LoopEngineeringConfig。"""
        defaults = {
            "loop_type": LoopType.CODING,
            "evaluator_mode": EvaluatorMode.STRICT,
            "project_root": self.tmpdir,
            "test_command": "python3 -c \"print('ok')\"",
            "test_timeout_sec": 10.0,
            "auto_commit": False,
            "security_analyzer": "builtin",
        }
        defaults.update(overrides)
        return LoopEngineeringConfig(**defaults)

    def _make_discovery(self, objective: str = "实现登录接口") -> DiscoveryResult:
        """构造一个简化的 DiscoveryResult。"""
        return DiscoveryResult(
            objective=objective,
            inferred_goal=f"完成代码实现：{objective}",
            detected_risks=["安全敏感"],
            relevant_skills=["security"],
            suggested_agents=["solo-coder"],
            artifacts_to_read=[Path("docs/spec/AUTH.md")],
            context_features={"project_root": str(self.tmpdir)},
        )

    def test_create_work_items_coding_generates_solo_coder(self):
        """coding loop 生成 solo-coder 工作项。"""
        config = self._make_config()
        adapter = HandoffAdapter(config=config)
        discovery = self._make_discovery()

        items = adapter.create_work_items(discovery, "coding")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].agent_type, "solo-coder")
        self.assertIn("实现登录接口", items[0].task)
        self.assertTrue(items[0].item_id)

    def test_create_work_items_design_generates_architect(self):
        """design loop 生成 architect 工作项。"""
        config = self._make_config(loop_type=LoopType.DESIGN)
        adapter = HandoffAdapter(config=config)
        discovery = DiscoveryResult(
            objective="设计用户认证 API",
            inferred_goal="完成设计文档：设计用户认证 API",
            suggested_patterns=[],
            context_features={"project_root": str(self.tmpdir)},
        )

        items = adapter.create_work_items(discovery, "design")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].agent_type, "architect")

    def test_create_work_items_testing_generates_test_expert(self):
        """testing loop 生成 test-expert 工作项。"""
        config = self._make_config(loop_type=LoopType.TESTING)
        adapter = HandoffAdapter(config=config)
        discovery = DiscoveryResult(
            objective="补充认证模块测试",
            inferred_goal="完成测试补充：补充认证模块测试",
            context_features={"project_root": str(self.tmpdir)},
        )

        items = adapter.create_work_items(discovery, "testing")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].agent_type, "test-expert")

    def test_create_work_items_invalid_loop_type_raises(self):
        """非法 loop_type 抛出 ValueError。"""
        config = self._make_config()
        adapter = HandoffAdapter(config=config)
        discovery = self._make_discovery()

        with self.assertRaises(ValueError):
            adapter.create_work_items(discovery, "unknown")

    def test_execute_with_custom_executor_success(self):
        """自定义 executor 成功时整体成功，且结果字段完整。"""
        config = self._make_config()

        def _executor(item):
            return {
                "success": True,
                "output": f"executed {item.agent_type}",
                "summary": "ok",
                "skills_used": ["test-skill"],
                "error": "",
            }

        adapter = HandoffAdapter(config=config, executor=_executor)
        items = adapter.create_work_items(self._make_discovery(), "coding")
        result = adapter.execute(items, config)

        self.assertTrue(result["success"])
        self.assertIn("executed solo-coder", result["output"])
        self.assertIn("test_result", result)
        self.assertIn("lint_result", result)
        self.assertIn("security_result", result)
        self.assertIn("modified_files", result)
        self.assertIn("committed_count", result)
        self.assertIn("test-skill", result["skills_used"])

    def test_execute_custom_executor_failure_marks_unsuccessful(self):
        """自定义 executor 失败时整体不成功。"""
        config = self._make_config()

        def _executor(item):
            return {
                "success": False,
                "output": "",
                "summary": "failed",
                "error": "something wrong",
            }

        adapter = HandoffAdapter(config=config, executor=_executor)
        items = adapter.create_work_items(self._make_discovery(), "coding")
        result = adapter.execute(items, config)

        self.assertFalse(result["success"])
        # HandoffAdapter 将 error 与 summary 拼接为错误说明
        self.assertIn("something wrong", result["error"])

    def test_execute_no_executor_returns_failure(self):
        """无 executor 且无 dispatcher_adapter 时返回失败，不伪造成功。"""
        config = self._make_config()
        adapter = HandoffAdapter(config=config)
        items = adapter.create_work_items(self._make_discovery(), "coding")
        result = adapter.execute(items, config)

        self.assertFalse(result["success"])
        self.assertIn("无可用执行器", result["error"])

    def test_execute_empty_items_returns_failure(self):
        """空工作项列表直接返回失败。"""
        config = self._make_config()
        adapter = HandoffAdapter(config=config)
        result = adapter.execute([], config)

        self.assertFalse(result["success"])
        self.assertIn("没有可执行的工作项", result["error"])

    def test_execute_runs_configured_test_command(self):
        """execute 会真实运行 config.test_command 并记录结果。"""
        config = self._make_config(
            test_command="python3 -c \"print('handoff-test')\"",
        )

        def _executor(item):
            return {
                "success": True,
                "output": "done",
                "summary": "ok",
                "skills_used": [],
                "error": "",
            }

        adapter = HandoffAdapter(config=config, executor=_executor)
        items = adapter.create_work_items(self._make_discovery(), "coding")
        result = adapter.execute(items, config)

        self.assertTrue(result["success"])
        self.assertTrue(result["test_result"]["passed"])
        self.assertIn("handoff-test", result["test_result"]["summary"])


if __name__ == "__main__":
    unittest.main()
