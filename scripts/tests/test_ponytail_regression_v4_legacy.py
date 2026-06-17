"""V4 legacy 回归测试（Ponytail 改造后）。

验证 Ponytail 改造没有破坏 V4 legacy dispatch 路径。

覆盖：
- TC-V4-01: _dispatch_via_claude_code 签名向后兼容
- TC-V4-02: _dispatch_via_claude_code 无 ponytail_prompt 时正常工作
- TC-V4-03: _dispatch_via_claude_code 有 ponytail_prompt 时注入到 context
- TC-V4-04: dispatch_agent_v2 不受影响
- TC-V4-05: _build_agent_prompt 向后兼容（无 context 时正常）
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


class TestPonytailRegressionV4Legacy(unittest.TestCase):
    """V4 legacy 回归测试（Ponytail 改造后）。"""

    def test_01_dispatch_via_claude_code_signature_backward_compatible(self):
        """TC-V4-01: _dispatch_via_claude_code 签名向后兼容。"""
        from dispatch.legacy import _dispatch_via_claude_code
        import inspect

        sig = inspect.signature(_dispatch_via_claude_code)
        params = sig.parameters

        # 原有参数应存在
        self.assertIn("agent_type", params)
        self.assertIn("task", params)
        self.assertIn("task_id", params)
        self.assertIn("project_root", params)
        self.assertIn("progress", params)

        # 新增参数应有默认值（向后兼容）
        self.assertIn("ponytail_prompt", params)
        self.assertEqual(
            params["ponytail_prompt"].default, "",
            "ponytail_prompt 应有默认值空字符串"
        )

    def test_02_dispatch_via_claude_code_without_ponytail(self):
        """TC-V4-02: _dispatch_via_claude_code 无 ponytail_prompt 时正常工作。"""
        from dispatch.legacy import _dispatch_via_claude_code

        # Mock ClaudeCodeSubAgentAdapter 避免真实调用
        with patch("dispatch.legacy.ClaudeCodeSubAgentAdapter") as mock_adapter_class:
            mock_adapter = MagicMock()
            mock_adapter.invoke_agent.return_value = {"success": True, "output": "ok"}
            mock_adapter_class.return_value = mock_adapter

            # 不传 ponytail_prompt（使用默认值）
            result = _dispatch_via_claude_code(
                agent_type="solo_coder",
                task="test task",
                task_id="test-id",
                project_root="/tmp",
                progress={},
            )
            self.assertTrue(result)

            # 验证 invoke_agent 被调用，context 中 ponytail_decision_ladder 为空
            mock_adapter.invoke_agent.assert_called_once()
            call_args = mock_adapter.invoke_agent.call_args
            context = call_args.args[2] if len(call_args.args) > 2 else call_args.kwargs.get("context")
            self.assertIn("ponytail_decision_ladder", context)
            self.assertEqual(context["ponytail_decision_ladder"], "")

    def test_03_dispatch_via_claude_code_with_ponytail(self):
        """TC-V4-03: _dispatch_via_claude_code 有 ponytail_prompt 时注入到 context。"""
        from dispatch.legacy import _dispatch_via_claude_code

        with patch("dispatch.legacy.ClaudeCodeSubAgentAdapter") as mock_adapter_class:
            mock_adapter = MagicMock()
            mock_adapter.invoke_agent.return_value = {"success": True, "output": "ok"}
            mock_adapter_class.return_value = mock_adapter

            ponytail_prompt = "## Ponytail 决策梯\n测试决策梯内容"
            result = _dispatch_via_claude_code(
                agent_type="solo_coder",
                task="test task",
                task_id="test-id",
                project_root="/tmp",
                progress={},
                ponytail_prompt=ponytail_prompt,
            )
            self.assertTrue(result)

            # 验证 context 中 ponytail_decision_ladder 被注入
            call_args = mock_adapter.invoke_agent.call_args
            context = call_args.args[2] if len(call_args.args) > 2 else call_args.kwargs.get("context")
            self.assertEqual(context["ponytail_decision_ladder"], ponytail_prompt)

    def test_04_dispatch_agent_v2_unaffected(self):
        """TC-V4-04: dispatch_agent_v2 不受影响。"""
        from dispatch.legacy import dispatch_agent_v2

        # 验证函数存在且可调用
        self.assertTrue(callable(dispatch_agent_v2))

        # Mock 内部调用避免真实执行
        # 注意：CyberneticsBridge 在函数内部 import（非模块级），
        # 且 cybernetics_enabled=False 时不会触发该路径，无需 patch
        with patch("dispatch.legacy._dispatch_via_claude_code") as mock_dispatch, \
             patch("dispatch.legacy.CLAUDE_CODE_ADAPTER_AVAILABLE", True):
            mock_dispatch.return_value = True

            result = dispatch_agent_v2(
                agent_type="solo_coder",
                task="test task",
                task_id="test-id",
                project_root="/tmp",
                progress={},
                cybernetics_enabled=False,  # 禁用 cybernetics 简化测试
            )
            # dispatch_agent_v2 应正常返回
            self.assertTrue(result)

    def test_05_build_agent_prompt_backward_compatible(self):
        """TC-V4-05: _build_agent_prompt 向后兼容（无 context 时正常）。"""
        from claude_code_subagent_adapter import ClaudeCodeSubAgentAdapter

        adapter = ClaudeCodeSubAgentAdapter()

        # 无 context（向后兼容）
        prompt_no_ctx = adapter._build_agent_prompt("solo_coder", "task", None)
        self.assertIn("Karpathy 四大核心原则", prompt_no_ctx)
        self.assertIn("task", prompt_no_ctx)
        self.assertNotIn("Ponytail 决策梯", prompt_no_ctx)

        # 空 context（向后兼容）
        prompt_empty_ctx = adapter._build_agent_prompt("solo_coder", "task", {})
        self.assertIn("Karpathy 四大核心原则", prompt_empty_ctx)
        self.assertNotIn("Ponytail 决策梯", prompt_empty_ctx)

        # 有 context 但无 ponytail_decision_ladder（向后兼容）
        prompt_no_ponytail = adapter._build_agent_prompt("solo_coder", "task", {
            "task_id": "test",
        })
        self.assertIn("Karpathy 四大核心原则", prompt_no_ponytail)
        self.assertNotIn("Ponytail 决策梯", prompt_no_ponytail)


if __name__ == "__main__":
    unittest.main()
