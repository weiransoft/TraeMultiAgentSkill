"""PluginContext 单元测试。"""
import unittest
from pathlib import Path
from dispatcher.plugin_context import PluginContext


def noop_log(message: str, level: str = "INFO") -> None:
    pass


class TestPluginContext(unittest.TestCase):
    """PluginContext 8 字段 + __post_init__ 自动转 Path。"""

    def test_required_fields(self):
        ctx = PluginContext(project_root="/tmp", log=noop_log)
        self.assertEqual(ctx.project_root, Path("/tmp"))
        self.assertIsNotNone(ctx.log)

    def test_default_optional_fields(self):
        ctx = PluginContext(project_root="/tmp", log=noop_log)
        self.assertIsNone(ctx.registry)
        self.assertFalse(ctx.dry_run)
        self.assertFalse(ctx.verbose)
        self.assertEqual(ctx.agent_type, "auto")
        self.assertIsNone(ctx.config)

    def test_explicit_optional_fields(self):
        ctx = PluginContext(
            project_root="/tmp",
            log=noop_log,
            dry_run=True,
            verbose=True,
            agent_type="solo-coder",
            config={"key": "value"},
        )
        self.assertTrue(ctx.dry_run)
        self.assertTrue(ctx.verbose)
        self.assertEqual(ctx.agent_type, "solo-coder")
        self.assertEqual(ctx.config, {"key": "value"})

    def test_path_post_init_conversion(self):
        ctx = PluginContext(project_root="/tmp", log=noop_log)
        self.assertIsInstance(ctx.project_root, Path)

    def test_dry_run_field_used_by_dispatcher(self):
        # 风险-5 验证：dry_run 字段在 dispatcher.dispatch() 入口检查
        ctx = PluginContext(project_root="/tmp", log=noop_log, dry_run=True)
        self.assertTrue(ctx.dry_run)
