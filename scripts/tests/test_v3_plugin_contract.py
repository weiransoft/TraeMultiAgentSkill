"""Plugin 契约测试（H-8 修复：保证内置 plugin 满足 ABC 接口）。"""
import unittest
import re
from plugins.base import GoalCommandPlugin
from plugins import BUILTIN_PLUGINS
from plugins.cancel import GoalCancelPlugin
from plugins.graph import GoalGraphPlugin
from plugins.resume import GoalResumePlugin
from plugins.multi_goal import MultiGoalPlugin
from plugins.loop import LoopGoalPlugin
from plugins.loop_engineering import LoopEngineeringPlugin
from plugins.autonomous import RalphAutonomousPlugin


class TestABCContract(unittest.TestCase):
    """ABC 不可实例化 + 子类必须实现所有抽象。"""

    def test_abc_cannot_instantiate(self):
        with self.assertRaises(TypeError):
            GoalCommandPlugin()


class TestBuiltinPluginsContract(unittest.TestCase):
    """内置 plugin 满足契约。"""

    def test_all_builtins_are_goal_command_plugin(self):
        for plugin in BUILTIN_PLUGINS:
            self.assertIsInstance(plugin, GoalCommandPlugin)

    def test_all_builtins_have_unique_names(self):
        names = [p.name for p in BUILTIN_PLUGINS]
        self.assertEqual(len(names), len(set(names)))

    def test_all_builtins_have_unique_priorities(self):
        priorities = [p.priority for p in BUILTIN_PLUGINS]
        self.assertEqual(len(priorities), len(set(priorities)))

    def test_all_builtins_have_valid_name_format(self):
        pattern = re.compile(r"^[a-z][a-z0-9-]*$")
        for plugin in BUILTIN_PLUGINS:
            self.assertRegex(plugin.name, pattern, f"Plugin {plugin.name!r} name 格式不合法")

    def test_builtin_mutex_is_symmetric(self):
        # H-1 验证：所有 plugin 的 mutex 关系对称
        for p1 in BUILTIN_PLUGINS:
            for p2 in BUILTIN_PLUGINS:
                if p1.name == p2.name:
                    continue
                a_mutex_b = p2.name in p1.mutex_with
                b_mutex_a = p1.name in p2.mutex_with
                self.assertEqual(a_mutex_b, b_mutex_a,
                    f"Plugin {p1.name!r} 与 {p2.name!r} mutex 关系不对称")

    def test_builtin_mutex_no_self_reference(self):
        for p in BUILTIN_PLUGINS:
            self.assertNotIn(p.name, p.mutex_with, f"Plugin {p.name!r} 自指")

    def test_builtin_mutex_references_exist(self):
        names = {p.name for p in BUILTIN_PLUGINS}
        for p in BUILTIN_PLUGINS:
            for mutex_name in p.mutex_with:
                self.assertIn(mutex_name, names, f"Plugin {p.name!r} mutex_with 引用不存在 {mutex_name!r}")

    def test_builtin_plugin_classes_importable(self):
        # 7 个 plugin class 全部可 import
        self.assertIsNotNone(GoalCancelPlugin)
        self.assertIsNotNone(GoalGraphPlugin)
        self.assertIsNotNone(GoalResumePlugin)
        self.assertIsNotNone(MultiGoalPlugin)
        self.assertIsNotNone(LoopGoalPlugin)
        self.assertIsNotNone(LoopEngineeringPlugin)
        self.assertIsNotNone(RalphAutonomousPlugin)


class TestBuiltinPluginsStateless(unittest.TestCase):
    """plugin 必须 stateless（风险-9 修正）。"""

    def test_plugin_instances_independent(self):
        # 验证内置 plugin 是多个不同 class
        from plugins import BUILTIN_PLUGINS as builtin1
        plugin_classes = set()
        for p in builtin1:
            plugin_classes.add(type(p))
        self.assertEqual(len(plugin_classes), 7, "内置 plugin 应该是 7 个不同 class")

    def test_builtins_count_is_7(self):
        # Phase 18 + Loop Engineering：共 7 个内置 plugin
        self.assertEqual(len(BUILTIN_PLUGINS), 7)
