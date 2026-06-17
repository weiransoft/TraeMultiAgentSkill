"""Phase 18 回归测试（Ponytail 改造后）。

验证 Ponytail 改造没有破坏 Phase 18 的 autonomous 模式功能。

覆盖：
- TC-REG-01: PlanHandler 无 ponytail_engine 时正常工作
- TC-REG-02: DevHandler 无 ponytail_engine 时正常工作
- TC-REG-03: FixHandler 无 ponytail_engine 时正常工作
- TC-REG-04: VerifyHandler 无 ponytail_engine 时正常工作
- TC-REG-05: RalphAutonomousPlugin 构造不崩溃
- TC-REG-06: handler 向后兼容（旧参数仍可用）
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# 确保 scripts 目录在 path 中
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


class TestPonytailRegressionPhase18(unittest.TestCase):
    """Phase 18 回归测试（Ponytail 改造后）。"""

    def test_01_plan_handler_without_ponytail(self):
        """TC-REG-01: PlanHandler 无 ponytail_engine 时正常工作。"""
        from autonomous.handlers.plan_handler import PlanHandler

        iter_ctx = MagicMock()
        iter_ctx.iter_index = 1
        iter_ctx.current_plan = "测试目标"
        iter_ctx.run_id = "test-run"

        # 无 ponytail_engine（向后兼容）
        ph = PlanHandler(
            auto_skill_loader=None,
            notes_memory=None,
            ponytail_engine=None,  # 不注入
        )
        result = ph.do_handle(iter_ctx)
        self.assertEqual(result.kind, "success")
        # 无 ponytail_engine 时不应注入 YAGNI 约束
        self.assertNotIn("YAGNI 规划约束", iter_ctx.current_plan)

    def test_02_dev_handler_without_ponytail(self):
        """TC-REG-02: DevHandler 无 ponytail_engine 时正常工作。"""
        from autonomous.handlers.dev_handler import DevHandler

        iter_ctx = MagicMock()
        iter_ctx.iter_index = 1
        iter_ctx.current_plan = "测试任务"
        iter_ctx.run_id = "test-run"

        with patch("dispatch.legacy._dispatch_via_claude_code") as mock_dispatch:
            mock_dispatch.return_value = True

            # 无 ponytail_engine（向后兼容）
            dh = DevHandler(
                dispatcher_adapter=None,
                smart_confirmation=None,
                auto_skill_loader=None,
                ponytail_engine=None,  # 不注入
                project_root="/tmp",
            )
            result = dh.do_handle(iter_ctx)
            self.assertEqual(result.kind, "success")
            # 无 ponytail_engine 时不应标记注入
            self.assertFalse(result.artifacts.get("ponytail_injected"))

    def test_03_fix_handler_without_ponytail(self):
        """TC-REG-03: FixHandler 无 ponytail_engine 时正常工作。"""
        from autonomous.handlers.fix_handler import FixHandler

        iter_ctx = MagicMock()
        iter_ctx.iter_index = 1
        iter_ctx.run_id = "test-run"
        iter_ctx.verify_artifacts = {
            "test_results": [0, 1, 0],
            "security_issues": [],
            "test_output_tail": "AssertionError: failed",
        }

        with patch("dispatch.legacy._dispatch_via_claude_code") as mock_dispatch:
            mock_dispatch.return_value = True

            # 无 ponytail_engine（向后兼容）
            fh = FixHandler(
                dispatcher_adapter=None,
                max_fix_attempts=2,
                ponytail_engine=None,  # 不注入
                project_root="/tmp",
            )
            result = fh.do_handle(iter_ctx)
            self.assertEqual(result.kind, "success")
            # 修复约束仍应注入（不依赖 ponytail_engine）
            call_args = mock_dispatch.call_args
            task = call_args.kwargs.get("task") or call_args.args[1]
            self.assertIn("修复约束", task)

    def test_04_verify_handler_without_ponytail(self):
        """TC-REG-04: VerifyHandler 无 ponytail_engine 时正常工作。"""
        from autonomous.handlers.verify_handler import VerifyHandler

        iter_ctx = MagicMock()
        iter_ctx.worktree_path = Path("/tmp")
        iter_ctx.agent_output = "some output"

        # 无 ponytail_engine 和 debt_collector（向后兼容）
        vh = VerifyHandler(
            git_driver=None,
            test_command="",  # 不执行测试
            security_analyzer="builtin",
            ponytail_engine=None,
            debt_collector=None,
            project_root="/tmp",
        )
        result = vh.do_handle(iter_ctx)
        # 无测试命令时应返回 success（0 passed/0 failed/0 skipped）
        self.assertEqual(result.kind, "success")

    def test_05_ralph_autonomous_plugin_construction(self):
        """TC-REG-05: RalphAutonomousPlugin 构造不崩溃。"""
        from plugins.autonomous import RalphAutonomousPlugin

        plugin = RalphAutonomousPlugin()
        self.assertEqual(plugin.name, "autonomous")
        self.assertEqual(plugin.priority, 5)
        self.assertFalse(plugin.requires_task)

    def test_06_handler_backward_compatible(self):
        """TC-REG-06: handler 向后兼容（旧参数仍可用）。"""
        from autonomous.handlers.dev_handler import DevHandler
        from autonomous.handlers.fix_handler import FixHandler
        from autonomous.handlers.plan_handler import PlanHandler
        from autonomous.handlers.verify_handler import VerifyHandler

        # 使用旧参数构造（不传 ponytail 相关参数）
        # 应该不崩溃，使用默认值 None
        dh = DevHandler(
            dispatcher_adapter=None,
            smart_confirmation=None,
            auto_skill_loader=None,
        )
        self.assertIsNone(dh._ponytail_engine)

        fh = FixHandler(
            dispatcher_adapter=None,
            max_fix_attempts=2,
        )
        self.assertIsNone(fh._ponytail_engine)

        ph = PlanHandler(
            auto_skill_loader=None,
            notes_memory=None,
        )
        self.assertIsNone(ph._ponytail_engine)

        vh = VerifyHandler(
            git_driver=None,
            test_command="echo test",
            security_analyzer="builtin",
        )
        self.assertIsNone(vh._ponytail_engine)
        self.assertIsNone(vh._debt_collector)


if __name__ == "__main__":
    unittest.main()
