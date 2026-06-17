"""Ponytail 全链路注入集成测试。

验证 Plan→Dev→Verify→Fix 4 阶段均注入 ponytail 决策梯。

覆盖：
- TC-INT-01: PlanHandler 注入 YAGNI 约束
- TC-INT-02: DevHandler 注入决策梯到 context
- TC-INT-03: FixHandler 注入修复约束 + 决策梯
- TC-INT-04: VerifyHandler 注入债务检测
- TC-INT-05: _build_agent_prompt 参数化注入
- TC-INT-06: _dispatch_via_claude_code 签名包含 ponytail_prompt
- TC-INT-07: 角色差异化注入（5 个角色）
- TC-INT-08: 端到端 prompt 包含决策梯
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

from ponytail.ruleset import PonytailRulesetEngine, PonytailMode


class TestPonytailIntegration(unittest.TestCase):
    """Ponytail 全链路注入集成测试。"""

    def setUp(self):
        """每个测试前创建引擎。"""
        self.engine = PonytailRulesetEngine()

    def test_01_plan_handler_injects_yagni(self):
        """TC-INT-01: PlanHandler 注入 YAGNI 约束。"""
        from autonomous.handlers.plan_handler import PlanHandler

        # 创建 mock iter_ctx
        iter_ctx = MagicMock()
        iter_ctx.iter_index = 1
        iter_ctx.current_plan = "测试目标"
        iter_ctx.run_id = "test-run"

        # 不注入 ponytail_engine
        ph_no_ponytail = PlanHandler(
            auto_skill_loader=None,
            notes_memory=None,
            ponytail_engine=None,
        )
        result_no = ph_no_ponytail.do_handle(iter_ctx)
        self.assertEqual(result_no.kind, "success")
        self.assertNotIn("YAGNI 规划约束", iter_ctx.current_plan)

        # 注入 ponytail_engine
        iter_ctx.current_plan = "测试目标"
        ph_with_ponytail = PlanHandler(
            auto_skill_loader=None,
            notes_memory=None,
            ponytail_engine=self.engine,
        )
        result_with = ph_with_ponytail.do_handle(iter_ctx)
        self.assertEqual(result_with.kind, "success")
        self.assertIn("YAGNI 规划约束", iter_ctx.current_plan)
        self.assertIn("Ponytail 决策梯", iter_ctx.current_plan)

    def test_02_dev_handler_injects_decision_ladder(self):
        """TC-INT-02: DevHandler 注入决策梯到 context。"""
        from autonomous.handlers.dev_handler import DevHandler

        # 创建 mock iter_ctx
        iter_ctx = MagicMock()
        iter_ctx.iter_index = 1
        iter_ctx.current_plan = "测试任务"
        iter_ctx.run_id = "test-run"

        # Mock _dispatch_via_claude_code 避免真实调用
        with patch("dispatch.legacy._dispatch_via_claude_code") as mock_dispatch:
            mock_dispatch.return_value = True

            dh = DevHandler(
                dispatcher_adapter=None,
                smart_confirmation=None,
                auto_skill_loader=None,
                ponytail_engine=self.engine,
                project_root="/tmp",
                ponytail_mode=PonytailMode.FULL,
            )
            result = dh.do_handle(iter_ctx)

            self.assertEqual(result.kind, "success")
            self.assertTrue(result.artifacts.get("ponytail_injected"))

            # 验证 _dispatch_via_claude_code 被调用
            mock_dispatch.assert_called_once()
            call_args = mock_dispatch.call_args
            # 验证 agent_type 是 solo_coder
            self.assertEqual(call_args.kwargs.get("agent_type") or call_args.args[0], "solo_coder")

    def test_03_fix_handler_injects_fix_constraint(self):
        """TC-INT-03: FixHandler 注入修复约束 + 决策梯。"""
        from autonomous.handlers.fix_handler import FixHandler

        # 创建 mock iter_ctx
        iter_ctx = MagicMock()
        iter_ctx.iter_index = 1
        iter_ctx.run_id = "test-run"
        iter_ctx.verify_artifacts = {
            "test_results": [0, 1, 0],  # 1 failed
            "security_issues": [],
            "test_output_tail": 'File "test.py", line 10\nAssertionError: test failed',
        }

        # Mock _dispatch_via_claude_code 避免真实调用
        with patch("dispatch.legacy._dispatch_via_claude_code") as mock_dispatch:
            mock_dispatch.return_value = True

            fh = FixHandler(
                dispatcher_adapter=None,
                max_fix_attempts=2,
                ponytail_engine=self.engine,
                project_root="/tmp",
                ponytail_mode=PonytailMode.FULL,
            )
            result = fh.do_handle(iter_ctx)

            self.assertEqual(result.kind, "success")
            self.assertTrue(result.artifacts.get("ponytail_injected"))

            # 验证 fix_task 包含修复约束
            call_args = mock_dispatch.call_args
            task = call_args.kwargs.get("task") or call_args.args[1]
            self.assertIn("修复约束", task)
            self.assertIn("只改必要的", task)

    def test_04_verify_handler_debt_detection(self):
        """TC-INT-04: VerifyHandler 注入债务检测。"""
        from autonomous.handlers.verify_handler import VerifyHandler
        from ponytail.debt_collector import DebtCollector

        # 创建 mock iter_ctx
        iter_ctx = MagicMock()
        iter_ctx.worktree_path = Path("/tmp")
        iter_ctx.agent_output = "some output"  # 非空，避免空 diff 检测

        # 创建带债务的临时目录
        import tempfile
        import shutil
        tmpdir = tempfile.mkdtemp()
        tmp_path = Path(tmpdir)
        try:
            # 创建 4 条 no_trigger 债务（超过阈值 3）
            test_file = tmp_path / "test.py"
            test_file.write_text(
                "# ponytail: first\n"
                "# ponytail: second\n"
                "# ponytail: third\n"
                "# ponytail: fourth\n",
                encoding="utf-8",
            )

            vh = VerifyHandler(
                git_driver=None,
                test_command="",  # 不执行测试
                security_analyzer="builtin",
                ponytail_engine=self.engine,
                debt_collector=DebtCollector(),
                project_root=str(tmp_path),
            )
            result = vh.do_handle(iter_ctx)

            # 应检测到 4 条 no_trigger 债务，触发 retriable
            self.assertEqual(result.kind, "retriable")
            self.assertIn("no_trigger", result.summary)
            self.assertEqual(result.artifacts.get("debt_no_trigger_count"), 4)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_05_build_agent_prompt_parametrized_injection(self):
        """TC-INT-05: _build_agent_prompt 参数化注入。"""
        from claude_code_subagent_adapter import ClaudeCodeSubAgentAdapter

        adapter = ClaudeCodeSubAgentAdapter()

        # 无决策梯注入
        prompt_no = adapter._build_agent_prompt("solo_coder", "task", {})
        self.assertNotIn("Ponytail 决策梯", prompt_no)

        # 有决策梯注入（通过 context）
        ponytail_prompt = self.engine.get_injection_prompt(role="solo_coder")
        prompt_with = adapter._build_agent_prompt("solo_coder", "task", {
            "ponytail_decision_ladder": ponytail_prompt,
        })
        self.assertIn("Ponytail 决策梯", prompt_with)
        self.assertIn("不可简化红线", prompt_with)

    def test_06_dispatch_via_claude_code_signature(self):
        """TC-INT-06: _dispatch_via_claude_code 签名包含 ponytail_prompt。"""
        from dispatch.legacy import _dispatch_via_claude_code
        import inspect

        sig = inspect.signature(_dispatch_via_claude_code)
        params = list(sig.parameters.keys())
        self.assertIn("ponytail_prompt", params)

    def test_07_role_differentiated_injection(self):
        """TC-INT-07: 角色差异化注入（5 个角色）。"""
        roles_expected = {
            "solo_coder": (PonytailMode.FULL, True),      # 非空
            "architect": (PonytailMode.FULL, True),        # 非空
            "test_expert": (PonytailMode.LITE, True),      # 非空
            "product_manager": (PonytailMode.OFF, False),  # 空
            "ui_designer": (PonytailMode.LITE, True),      # 非空
        }

        for role, (expected_mode, should_be_non_empty) in roles_expected.items():
            prompt = self.engine.get_injection_prompt(role=role)
            if should_be_non_empty:
                self.assertTrue(prompt, f"{role} 应返回非空 prompt")
                self.assertIn(f"模式：{expected_mode.value}", prompt)
            else:
                self.assertEqual(prompt, "", f"{role} 应返回空 prompt（OFF 模式）")

    def test_08_end_to_end_prompt_contains_ladder(self):
        """TC-INT-08: 端到端 prompt 包含决策梯。"""
        from claude_code_subagent_adapter import ClaudeCodeSubAgentAdapter

        adapter = ClaudeCodeSubAgentAdapter()

        # 模拟 DevHandler 构造的 context
        ponytail_prompt = self.engine.get_injection_prompt(
            role="solo_coder", mode=PonytailMode.FULL
        )
        context = {
            "task_id": "test-run",
            "project_root": "/tmp",
            "ponytail_decision_ladder": ponytail_prompt,
            "karpathy_principles": {
                "simplicity_first": "最小代码",
            },
        }

        prompt = adapter._build_agent_prompt("solo_coder", "完成开发任务", context)

        # 验证 prompt 包含所有关键部分
        self.assertIn("Karpathy 四大核心原则", prompt)
        self.assertIn("Ponytail 决策梯", prompt)
        self.assertIn("代码决策梯", prompt)
        self.assertIn("不可简化红线", prompt)
        self.assertIn("输出规范", prompt)
        self.assertIn("完成开发任务", prompt)


if __name__ == "__main__":
    unittest.main()
