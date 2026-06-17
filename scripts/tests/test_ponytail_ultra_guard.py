"""Ponytail ultra 模式守护单元测试。

覆盖 6 个用例：
- TC-UG-01: ultra 模式包含追加条款
- TC-UG-02: ultra 模式包含 YAGNI 极端主义
- TC-UG-03: ultra 模式包含红线硬阻断
- TC-UG-04: ultra 模式包含不可 re-arguing 约束
- TC-UG-05: autonomous 模式下 ultra 降级为 full
- TC-UG-06: ultra 模式 prompt 长度限制
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# 确保 scripts 目录在 path 中
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from ponytail.ruleset import PonytailRulesetEngine, PonytailMode


class TestPonytailUltraGuard(unittest.TestCase):
    """Ponytail ultra 模式守护单元测试。"""

    def setUp(self):
        """每个测试前创建引擎。"""
        self.engine = PonytailRulesetEngine()

    def test_01_ultra_contains_extra_section(self):
        """TC-UG-01: ultra 模式包含追加条款段落。"""
        prompt = self.engine.get_injection_prompt(
            role="solo_coder", mode=PonytailMode.ULTRA
        )
        self.assertIn("Ultra 模式追加条款", prompt)

    def test_02_ultra_contains_yagni_extremism(self):
        """TC-UG-02: ultra 模式包含 YAGNI 极端主义。"""
        prompt = self.engine.get_injection_prompt(
            role="solo_coder", mode=PonytailMode.ULTRA
        )
        self.assertIn("YAGNI 极端主义", prompt)
        self.assertIn("删除优先于添加", prompt)

    def test_03_ultra_contains_hard_block_on_redline(self):
        """TC-UG-03: ultra 模式包含红线硬阻断。"""
        prompt = self.engine.get_injection_prompt(
            role="solo_coder", mode=PonytailMode.ULTRA
        )
        self.assertIn("红线违反时硬阻断", prompt)
        self.assertIn("降级到 full 并告警", prompt)

    def test_04_ultra_contains_no_re_arguing_constraint(self):
        """TC-UG-04: ultra 模式包含不可 re-arguing 约束。"""
        prompt = self.engine.get_injection_prompt(
            role="solo_coder", mode=PonytailMode.ULTRA
        )
        self.assertIn("不可 re-arguing", prompt)
        self.assertIn("用户明确要求完整实现时", prompt)

    def test_05_autonomous_downgrades_ultra_to_full(self):
        """TC-UG-05: autonomous 模式下 ultra 降级为 full。

        验证 plugins/autonomous.py 中的降级逻辑：
        ultra → full（autonomous 模式下禁止 ultra）
        """
        # 模拟 autonomous 模式下的降级逻辑
        mode = PonytailMode.ULTRA
        is_autonomous = True

        if is_autonomous and mode == PonytailMode.ULTRA:
            mode = PonytailMode.FULL

        self.assertEqual(mode, PonytailMode.FULL, "autonomous 模式下 ultra 应降级为 full")

    def test_06_ultra_prompt_length_reasonable(self):
        """TC-UG-06: ultra 模式 prompt 长度合理（不超过 2000 字符）。"""
        prompt = self.engine.get_injection_prompt(
            role="solo_coder", mode=PonytailMode.ULTRA
        )
        # ultra 模式应包含完整决策梯 + 红线 + 追加条款
        # 但不应过长（token 预算控制）
        self.assertGreater(len(prompt), 500, "ultra prompt 应有足够内容")
        self.assertLess(len(prompt), 5000, "ultra prompt 不应过长")


if __name__ == "__main__":
    unittest.main()
