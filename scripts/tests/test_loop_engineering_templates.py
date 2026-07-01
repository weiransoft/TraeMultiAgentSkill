"""LoopTemplate 单元测试。"""

import unittest
from pathlib import Path

from loop_engineering.models import DiscoveryResult, HandoffItem, LoopType
from loop_engineering.templates import (
    CodingLoopTemplate,
    DesignLoopTemplate,
    LoopTemplateRegistry,
    TestingLoopTemplate,
)


class TestDesignLoopTemplate(unittest.TestCase):
    """测试 DesignLoopTemplate。"""

    def setUp(self):
        """初始化模板和 Discovery 结果。"""
        self.template = DesignLoopTemplate()
        self.discovery = DiscoveryResult(
            objective="设计用户认证 API 接口",
            inferred_goal="完成设计文档：设计用户认证 API 接口",
            detected_risks=["安全敏感", "缺少设计文档"],
            relevant_skills=["architecture", "security"],
            suggested_patterns=["adversarial-verify"],
            artifacts_to_read=[Path("README.md"), Path("docs/spec/SPEC.md")],
            context_features={"project_root": "/tmp/project"},
        )

    def test_name_and_loop_type(self):
        """模板名称和 Loop 类型正确。"""
        self.assertEqual(self.template.name, "design-loop-template")
        self.assertEqual(self.template.loop_type, LoopType.DESIGN)

    def test_default_acceptance_criteria_not_empty(self):
        """默认验收标准非空且包含关键项。"""
        criteria = self.template.default_acceptance_criteria("设计 API")
        self.assertGreater(len(criteria), 0)
        self.assertTrue(any("设计文档" in c for c in criteria))
        self.assertTrue(any("adversarial-verify" in c for c in criteria))

    def test_create_work_items_includes_architect(self):
        """生成 architect 工作项。"""
        items = self.template.create_work_items(self.discovery)
        self.assertGreaterEqual(len(items), 1)
        self.assertEqual(items[0].agent_type, "architect")
        self.assertIn("设计用户认证 API 接口", items[0].task)
        self.assertEqual(items[0].acceptance_criteria, self.template.default_acceptance_criteria("设计用户认证 API 接口"))

    def test_create_work_items_adds_review_when_adversarial(self):
        """推荐 adversarial-verify 时增加评审工作项。"""
        items = self.template.create_work_items(self.discovery)
        self.assertEqual(len(items), 2)
        review_item = items[1]
        self.assertEqual(review_item.agent_type, "product-manager")
        self.assertEqual(review_item.dependencies, [items[0].item_id])

    def test_create_work_items_no_review_without_adversarial(self):
        """未推荐 adversarial-verify 时不增加评审工作项。"""
        discovery = DiscoveryResult(
            objective="设计简单工具函数",
            inferred_goal="完成设计文档：设计简单工具函数",
            suggested_patterns=[],
            context_features={"project_root": "/tmp/project"},
        )
        items = self.template.create_work_items(discovery)
        self.assertEqual(len(items), 1)


class TestCodingLoopTemplate(unittest.TestCase):
    """测试 CodingLoopTemplate。"""

    def setUp(self):
        """初始化模板和 Discovery 结果。"""
        self.template = CodingLoopTemplate()
        self.discovery = DiscoveryResult(
            objective="实现用户登录接口",
            inferred_goal="完成代码实现并通过验证：实现用户登录接口",
            detected_risks=["安全敏感"],
            relevant_skills=["security"],
            suggested_agents=["solo-coder"],
            artifacts_to_read=[Path("docs/spec/AUTH.md")],
            context_features={"project_root": "/tmp/project"},
        )

    def test_name_and_loop_type(self):
        """模板名称和 Loop 类型正确。"""
        self.assertEqual(self.template.name, "coding-loop-template")
        self.assertEqual(self.template.loop_type, LoopType.CODING)

    def test_default_acceptance_criteria_includes_tests(self):
        """验收标准包含测试和静态检查。"""
        criteria = self.template.default_acceptance_criteria("实现登录接口")
        self.assertTrue(any("单元测试" in c for c in criteria))
        self.assertTrue(any("ruff" in c or "mypy" in c for c in criteria))

    def test_create_work_items_single_solo_coder(self):
        """生成 solo-coder 工作项。"""
        items = self.template.create_work_items(self.discovery)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.agent_type, "solo-coder")
        self.assertIn("实现用户登录接口", item.task)
        self.assertIn("plan", item.metadata.get("stage_order", []))
        self.assertEqual(item.metadata.get("loop_type"), "coding")


class TestTestingLoopTemplate(unittest.TestCase):
    """测试 TestingLoopTemplate。"""

    def setUp(self):
        """初始化模板和 Discovery 结果。"""
        self.template = TestingLoopTemplate()
        self.discovery = DiscoveryResult(
            objective="补充认证模块单元测试",
            inferred_goal="完成测试补充并提升覆盖率：补充认证模块单元测试",
            detected_risks=["缺少测试"],
            relevant_skills=["testing"],
            suggested_agents=["test-expert"],
            artifacts_to_read=[Path("src/auth.py")],
            context_features={"project_root": "/tmp/project"},
        )

    def test_name_and_loop_type(self):
        """模板名称和 Loop 类型正确。"""
        self.assertEqual(self.template.name, "testing-loop-template")
        self.assertEqual(self.template.loop_type, LoopType.TESTING)

    def test_default_acceptance_criteria_includes_coverage(self):
        """验收标准包含覆盖率。"""
        criteria = self.template.default_acceptance_criteria("补充测试")
        self.assertTrue(any("覆盖率" in c for c in criteria))
        self.assertTrue(any("flaky" in c for c in criteria))

    def test_create_work_items_single_test_expert(self):
        """生成 test-expert 工作项。"""
        items = self.template.create_work_items(self.discovery)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.agent_type, "test-expert")
        self.assertIn("补充认证模块单元测试", item.task)
        self.assertEqual(item.metadata.get("pattern"), "generate-filter")


class TestLoopTemplateRegistry(unittest.TestCase):
    """测试 LoopTemplateRegistry。"""

    def setUp(self):
        """初始化注册表。"""
        self.registry = LoopTemplateRegistry()

    def test_all_builtin_templates_registered(self):
        """所有内置模板已注册。"""
        templates = self.registry.list_templates()
        self.assertEqual(len(templates), 3)
        names = {t.name for t in templates}
        self.assertEqual(
            names,
            {
                "design-loop-template",
                "coding-loop-template",
                "testing-loop-template",
            },
        )

    def test_get_template_by_loop_type(self):
        """根据 LoopType 获取模板。"""
        self.assertIsInstance(
            self.registry.get_template(LoopType.DESIGN), DesignLoopTemplate
        )
        self.assertIsInstance(
            self.registry.get_template(LoopType.CODING), CodingLoopTemplate
        )
        self.assertIsInstance(
            self.registry.get_template(LoopType.TESTING), TestingLoopTemplate
        )

    def test_get_unknown_template_raises(self):
        """未知 LoopType 抛出 ValueError。"""
        # 构造一个不在注册表中的 LoopType 需要技巧：直接传入不存在的值
        with self.assertRaises(ValueError):
            self.registry.get_template(LoopType("unknown"))


if __name__ == "__main__":
    unittest.main()
