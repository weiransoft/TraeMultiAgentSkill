"""测试 ClaudeCodeSubAgentAdapter 的 Ponytail 决策梯注入与线程安全。

测试用例编号：TC-AP-01 ~ TC-AP-12

覆盖场景：
1. _build_agent_prompt 参数化注入决策梯（ponytail_decision_ladder 优先）
2. 兜底路径：context['_ponytail_engine'] 按角色生成
3. 无 context / 空 context 向后兼容
4. engine 异常时不阻塞 prompt 构建
5. 100 并发调用线程安全（无实例状态修改）
6. 不同角色注入不同强度的决策梯
7. context 中 ponytail_decision_ladder 为空字符串时不注入
8. 完整 prompt 结构包含 Karpathy 原则 + 决策梯 + 上下文
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from unittest.mock import MagicMock, patch

# 确保可以导入 scripts 目录下的模块
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from claude_code_subagent_adapter import ClaudeCodeSubAgentAdapter
from ponytail.ruleset import PonytailMode, PonytailRulesetEngine, ROLE_INTENSITY


class TestClaudeCodeSubAgentAdapterPrompt(unittest.TestCase):
    """测试 ClaudeCodeSubAgentAdapter 的 Ponytail 注入与线程安全。"""

    def setUp(self):
        """每个测试前创建新的 adapter 实例。"""
        self.adapter = ClaudeCodeSubAgentAdapter()
        self.engine = PonytailRulesetEngine()

    # ------------------------------------------------------------------
    # TC-AP-01: context['ponytail_decision_ladder'] 优先注入
    # ------------------------------------------------------------------
    def test_01_ponytail_decision_ladder_in_context_takes_priority(self):
        """TC-AP-01: context 中已有 ponytail_decision_ladder 时优先使用。"""
        ladder = "## Ponytail 决策梯\n测试决策梯内容（优先级最高）"
        context = {"ponytail_decision_ladder": ladder}

        prompt = self.adapter._build_agent_prompt("solo_coder", "test task", context)

        self.assertIn(ladder, prompt, "应包含 context 中的决策梯内容")
        self.assertIn("## 任务", prompt)
        self.assertIn("test task", prompt)

    # ------------------------------------------------------------------
    # TC-AP-02: 兜底从 context['_ponytail_engine'] 按角色生成
    # ------------------------------------------------------------------
    def test_02_fallback_to_ponytail_engine_in_context(self):
        """TC-AP-02: 无 ponytail_decision_ladder 但有 _ponytail_engine 时兜底生成。"""
        context = {"_ponytail_engine": self.engine}

        prompt = self.adapter._build_agent_prompt("solo_coder", "test task", context)

        # solo_coder 默认 FULL 模式，应包含决策梯标题
        self.assertIn("Ponytail 决策梯", prompt, "应包含从 engine 生成的决策梯")
        # FULL 模式应包含 6 步决策梯（使用中文标题）
        self.assertIn("YAGNI", prompt)
        self.assertIn("标准库", prompt)

    # ------------------------------------------------------------------
    # TC-AP-03: 无 context 向后兼容
    # ------------------------------------------------------------------
    def test_03_no_context_backward_compatible(self):
        """TC-AP-03: 无 context 时正常构建 prompt，不注入决策梯。"""
        prompt = self.adapter._build_agent_prompt("solo_coder", "test task", None)

        self.assertIn("Karpathy 四大核心原则", prompt)
        self.assertIn("test task", prompt)
        self.assertNotIn("Ponytail 决策梯", prompt, "无 context 时不应注入决策梯")

    # ------------------------------------------------------------------
    # TC-AP-04: 空 context 向后兼容
    # ------------------------------------------------------------------
    def test_04_empty_context_backward_compatible(self):
        """TC-AP-04: 空 context dict 时正常构建 prompt，不注入决策梯。"""
        prompt = self.adapter._build_agent_prompt("solo_coder", "test task", {})

        self.assertIn("Karpathy 四大核心原则", prompt)
        self.assertNotIn("Ponytail 决策梯", prompt, "空 context 时不应注入决策梯")

    # ------------------------------------------------------------------
    # TC-AP-05: engine 异常时不阻塞 prompt 构建
    # ------------------------------------------------------------------
    def test_05_engine_exception_does_not_block_prompt(self):
        """TC-AP-05: _ponytail_engine.get_injection_prompt 抛异常时返回空注入。"""
        # 创建会抛异常的 mock engine
        bad_engine = MagicMock()
        bad_engine.get_injection_prompt.side_effect = RuntimeError("engine 故障")
        context = {"_ponytail_engine": bad_engine}

        # 不应抛异常
        prompt = self.adapter._build_agent_prompt("solo_coder", "test task", context)

        self.assertIn("Karpathy 四大核心原则", prompt, "应仍包含 Karpathy 原则")
        self.assertNotIn("Ponytail 决策梯", prompt, "engine 故障时不应注入决策梯")

    # ------------------------------------------------------------------
    # TC-AP-06: 100 并发调用线程安全
    # ------------------------------------------------------------------
    def test_06_100_concurrent_calls_thread_safe(self):
        """TC-AP-06: 100 个线程并发调用 _build_agent_prompt，结果应正确且无竞争。"""
        # 每个线程使用不同的角色和 context
        roles = ["solo_coder", "architect", "test_expert", "product_manager", "ui_designer"]
        ladder_text = "## Ponytail 决策梯\n线程安全测试"
        context = {"ponytail_decision_ladder": ladder_text}

        results = {}
        errors = []

        def call_prompt(thread_id: int):
            """单线程任务：构建 prompt 并验证。"""
            try:
                role = roles[thread_id % len(roles)]
                adapter = ClaudeCodeSubAgentAdapter()
                prompt = adapter._build_agent_prompt(role, f"task-{thread_id}", context)
                return thread_id, prompt
            except Exception as e:
                errors.append((thread_id, str(e)))
                return thread_id, None

        # 100 并发
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(call_prompt, i) for i in range(100)]
            for future in as_completed(futures):
                tid, prompt = future.result()
                if prompt is not None:
                    results[tid] = prompt

        # 验证无异常
        self.assertEqual(len(errors), 0, f"并发调用产生异常: {errors}")
        # 验证 100 个结果都正确
        self.assertEqual(len(results), 100, "应有 100 个成功结果")
        # 验证每个结果都包含决策梯
        for tid, prompt in results.items():
            self.assertIn(ladder_text, prompt, f"线程 {tid} 的 prompt 应包含决策梯")
            self.assertIn(f"task-{tid}", prompt, f"线程 {tid} 的 prompt 应包含对应任务")

    # ------------------------------------------------------------------
    # TC-AP-07: 不同角色注入不同强度的决策梯
    # ------------------------------------------------------------------
    def test_07_different_roles_get_different_intensity(self):
        """TC-AP-07: solo_coder=FULL, test_expert=LITE, product_manager=OFF。"""
        # solo_coder → FULL（包含 6 步决策梯）
        prompt_coder = self.adapter._build_agent_prompt(
            "solo_coder", "task", {"_ponytail_engine": self.engine}
        )
        self.assertIn("Ponytail 决策梯", prompt_coder)
        self.assertIn("YAGNI", prompt_coder)

        # test_expert → LITE（精简版）
        prompt_tester = self.adapter._build_agent_prompt(
            "test_expert", "task", {"_ponytail_engine": self.engine}
        )
        # LITE 模式也应包含决策梯标题（但内容更精简）
        self.assertIn("Ponytail 决策梯", prompt_tester)

        # product_manager → OFF（不注入）
        prompt_pm = self.adapter._build_agent_prompt(
            "product_manager", "task", {"_ponytail_engine": self.engine}
        )
        self.assertNotIn("Ponytail 决策梯", prompt_pm, "product_manager=OFF 不应注入决策梯")

    # ------------------------------------------------------------------
    # TC-AP-08: ponytail_decision_ladder 为空字符串时不注入
    # ------------------------------------------------------------------
    def test_08_empty_string_ladder_not_injected(self):
        """TC-AP-08: context['ponytail_decision_ladder'] 为空字符串时不注入。"""
        context = {"ponytail_decision_ladder": ""}

        prompt = self.adapter._build_agent_prompt("solo_coder", "test task", context)

        self.assertNotIn("Ponytail 决策梯", prompt, "空字符串决策梯不应注入")
        self.assertIn("Karpathy 四大核心原则", prompt)

    # ------------------------------------------------------------------
    # TC-AP-09: 完整 prompt 结构验证
    # ------------------------------------------------------------------
    def test_09_full_prompt_structure(self):
        """TC-AP-09: 完整 prompt 包含角色 + 任务 + 要求 + 决策梯 + 上下文。"""
        ladder = "## Ponytail 决策梯\n结构验证"
        context = {
            "ponytail_decision_ladder": ladder,
            "task_id": "test-123",
            "project_root": "/tmp/project",
        }

        prompt = self.adapter._build_agent_prompt("solo_coder", "实现功能 X", context)

        # 验证各部分存在
        self.assertIn("## 任务", prompt)
        self.assertIn("实现功能 X", prompt)
        self.assertIn("## 要求", prompt)
        self.assertIn("Karpathy 四大核心原则", prompt)
        self.assertIn(ladder, prompt)
        self.assertIn("## 上下文", prompt)
        # 验证上下文 JSON 序列化
        self.assertIn("test-123", prompt)
        self.assertIn("/tmp/project", prompt)

    # ------------------------------------------------------------------
    # TC-AP-10: ponytail_decision_ladder 优先于 _ponytail_engine
    # ------------------------------------------------------------------
    def test_10_ladder_takes_priority_over_engine(self):
        """TC-AP-10: 同时存在 ladder 和 engine 时，ladder 优先。"""
        ladder = "## Ponytail 决策梯\n优先级测试"
        context = {
            "ponytail_decision_ladder": ladder,
            "_ponytail_engine": self.engine,
        }

        prompt = self.adapter._build_agent_prompt("solo_coder", "task", context)

        # 应包含 ladder 的内容
        self.assertIn("优先级测试", prompt)
        # 不应包含 engine 生成的 FULL 模式内容（避免重复注入）
        # engine 生成的内容会包含 "决策梯（FULL 模式）" 标题
        # ladder 优先时不应出现 engine 的标题
        full_mode_header = "FULL 模式"
        # 统计 FULL 模式出现次数（ladder 中不应有）
        self.assertEqual(
            prompt.count(full_mode_header),
            0,
            "ladder 优先时不应出现 engine 的 FULL 模式标题",
        )

    # ------------------------------------------------------------------
    # TC-AP-11: adapter 实例无状态修改（线程安全核心保证）
    # ------------------------------------------------------------------
    def test_11_adapter_instance_state_not_modified(self):
        """TC-AP-11: 调用 _build_agent_prompt 不修改 adapter 实例状态。"""
        # 记录调用前的实例属性
        attrs_before = dict(self.adapter.__dict__)

        # 多次调用
        for i in range(10):
            context = {"ponytail_decision_ladder": f"## Ponytail 决策梯\n调用 {i}"}
            self.adapter._build_agent_prompt("solo_coder", f"task-{i}", context)

        # 验证实例属性未被修改
        attrs_after = dict(self.adapter.__dict__)
        self.assertEqual(
            attrs_before,
            attrs_after,
            "adapter 实例属性不应被 _build_agent_prompt 修改",
        )

    # ------------------------------------------------------------------
    # TC-AP-12: ULTRA 模式在 autonomous 场景下被降级为 FULL
    # ------------------------------------------------------------------
    def test_12_ultra_mode_engine_still_works_in_adapter(self):
        """TC-AP-12: engine 设置为 ULTRA 模式时，adapter 仍能正常注入。"""
        # 创建 engine，通过 get_injection_prompt 指定 ULTRA 模式
        ultra_engine = PonytailRulesetEngine()
        context = {"_ponytail_engine": ultra_engine}

        # 使用 mock 让 get_injection_prompt 返回 ULTRA 模式内容
        with patch.object(
            ultra_engine,
            "get_injection_prompt",
            return_value="## Ponytail 决策梯\nULTRA 模式 YAGNI 极端主义",
        ):
            prompt = self.adapter._build_agent_prompt("solo_coder", "task", context)

        # ULTRA 模式应包含 YAGNI 极端主义提示
        self.assertIn("Ponytail 决策梯", prompt)
        self.assertIn("ULTRA", prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
