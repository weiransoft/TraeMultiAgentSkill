"""Loop Engineering 配置加载器单元测试。"""

import argparse
import shutil
import tempfile
import unittest
from pathlib import Path

from autonomous.config_loader import AutonomousConfig
from loop_engineering.config_loader import build_loop_config, loop_config_to_dict
from loop_engineering.models import (
    DiscoveryMode,
    EvaluatorMode,
    LoopEngineeringConfig,
    LoopType,
)


class TestBuildLoopConfigDefaults(unittest.TestCase):
    """测试 build_loop_config 默认值。"""

    def test_no_args_no_autonomous(self):
        """无 args 且无配置时返回默认值。"""
        tmpdir = Path(tempfile.mkdtemp())
        try:
            cfg = build_loop_config(project_root=tmpdir)
            self.assertEqual(cfg.loop_type, LoopType.CODING)
            self.assertEqual(cfg.discovery_mode, DiscoveryMode.AUTO)
            self.assertEqual(cfg.evaluator_mode, EvaluatorMode.STRICT)
            self.assertEqual(cfg.max_iterations, 50)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_autonomous_config_not_enabled(self):
        """autonomous 配置未启用 loop_engineering 时不影响默认值。"""
        auto_cfg = AutonomousConfig(loop_engineering_enabled=False)
        cfg = build_loop_config(autonomous_config=auto_cfg)
        self.assertEqual(cfg.loop_type, LoopType.CODING)
        self.assertEqual(cfg.max_iterations, 50)

    def test_autonomous_config_enabled_overrides(self):
        """autonomous 启用 loop_engineering 时覆盖默认值。"""
        auto_cfg = AutonomousConfig(
            loop_engineering_enabled=True,
            loop_type="design",
            loop_discovery_mode="manual",
            loop_evaluator_mode="standard",
            loop_human_checkpoint_every=3,
            loop_max_iterations=20,
            loop_max_tokens=100_000,
            loop_sampling_read_ratio=0.2,
        )
        cfg = build_loop_config(autonomous_config=auto_cfg)
        self.assertEqual(cfg.loop_type, LoopType.DESIGN)
        self.assertEqual(cfg.discovery_mode, DiscoveryMode.MANUAL)
        self.assertEqual(cfg.evaluator_mode, EvaluatorMode.STANDARD)
        self.assertEqual(cfg.human_checkpoint_every, 3)
        self.assertEqual(cfg.max_iterations, 20)
        self.assertEqual(cfg.max_tokens, 100_000)
        self.assertAlmostEqual(cfg.sampling_read_ratio, 0.2)


class TestBuildLoopConfigCliArgs(unittest.TestCase):
    """测试 CLI args 优先级最高。"""

    def test_cli_overrides_autonomous(self):
        """CLI args 覆盖 autonomous 配置。"""
        auto_cfg = AutonomousConfig(
            loop_engineering_enabled=True,
            loop_type="design",
            loop_max_iterations=20,
        )
        args = argparse.Namespace(
            loop_type="testing",
            loop_discovery=None,
            loop_evaluator=None,
            loop_human_checkpoint_every=None,
            loop_max_iterations=None,
            loop_max_tokens=None,
            loop_sampling_read_ratio=None,
            project_root=None,
            task=None,
        )
        cfg = build_loop_config(args=args, autonomous_config=auto_cfg)
        self.assertEqual(cfg.loop_type, LoopType.TESTING)
        self.assertEqual(cfg.max_iterations, 20)

    def test_cli_all_fields(self):
        """CLI 提供全部字段。"""
        args = argparse.Namespace(
            loop_type="coding",
            loop_discovery="off",
            loop_evaluator="strict",
            loop_human_checkpoint_every=10,
            loop_max_iterations=100,
            loop_max_tokens=1_000_000,
            loop_sampling_read_ratio=0.5,
            project_root="/tmp/project",
            task="实现登录功能",
        )
        cfg = build_loop_config(args=args)
        self.assertEqual(cfg.loop_type, LoopType.CODING)
        self.assertEqual(cfg.discovery_mode, DiscoveryMode.OFF)
        self.assertEqual(cfg.evaluator_mode, EvaluatorMode.STRICT)
        self.assertEqual(cfg.human_checkpoint_every, 10)
        self.assertEqual(cfg.max_iterations, 100)
        self.assertEqual(cfg.max_tokens, 1_000_000)
        self.assertAlmostEqual(cfg.sampling_read_ratio, 0.5)
        self.assertEqual(str(cfg.project_root), "/tmp/project")
        self.assertEqual(cfg.stop_when, "实现登录功能")

    def test_invalid_enum_falls_back(self):
        """非法 enum 值 fallback 到当前值。"""
        args = argparse.Namespace(
            loop_type="invalid-type",
            loop_discovery="also-invalid",
            loop_evaluator="unknown",
            loop_human_checkpoint_every=None,
            loop_max_iterations=None,
            loop_max_tokens=None,
            loop_sampling_read_ratio=None,
            project_root=None,
            task=None,
        )
        cfg = build_loop_config(args=args)
        self.assertEqual(cfg.loop_type, LoopType.CODING)
        self.assertEqual(cfg.discovery_mode, DiscoveryMode.AUTO)
        self.assertEqual(cfg.evaluator_mode, EvaluatorMode.STRICT)


class TestLoopConfigToDict(unittest.TestCase):
    """测试 loop_config_to_dict 序列化。"""

    def test_serialization(self):
        """配置可序列化为 dict。"""
        cfg = LoopEngineeringConfig(loop_type=LoopType.TESTING)
        d = loop_config_to_dict(cfg)
        self.assertEqual(d["loop_type"], "testing")
        self.assertEqual(d["discovery_mode"], "auto")
        self.assertEqual(d["max_iterations"], 50)
        self.assertIn("stage_order", d)
        self.assertIsInstance(d["stage_order"], list)


if __name__ == "__main__":
    unittest.main()
