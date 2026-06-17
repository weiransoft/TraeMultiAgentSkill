"""Ponytail 决策梯规则集引擎单元测试。

覆盖 15 个用例：
- TC-RS-01~06: 模式过滤（OFF/FULL/LITE/ULTRA/兜底/缓存）
- TC-RS-07~11: 角色差异化（solo_coder/test_expert/product_manager/architect/ui_designer）
- TC-RS-12~13: 红线与输出规范
- TC-RS-14: 未知角色回退
- TC-RS-15: 线程安全（并发调用）
"""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

# 确保 scripts 目录在 path 中
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from ponytail.ruleset import PonytailRulesetEngine, PonytailMode, ROLE_INTENSITY


class TestPonytailRulesetEngine(unittest.TestCase):
    """PonytailRulesetEngine 单元测试。"""

    def setUp(self):
        """每个测试前创建引擎实例。"""
        self.engine = PonytailRulesetEngine()

    # ==================== 模式过滤测试 ====================

    def test_01_off_mode_returns_empty(self):
        """TC-RS-01: OFF 模式返回空字符串。"""
        prompt = self.engine.get_injection_prompt(role="solo_coder", mode=PonytailMode.OFF)
        self.assertEqual(prompt, "")
        self.assertEqual(len(prompt), 0)

    def test_02_full_mode_returns_non_empty(self):
        """TC-RS-02: FULL 模式返回非空决策梯。"""
        prompt = self.engine.get_injection_prompt(role="solo_coder", mode=PonytailMode.FULL)
        self.assertGreater(len(prompt), 200)
        self.assertIn("决策梯", prompt)
        self.assertIn("YAGNI", prompt)
        self.assertIn("标准库优先", prompt)
        self.assertIn("平台原生", prompt)
        self.assertIn("复用现有", prompt)
        self.assertIn("一行优先", prompt)
        self.assertIn("最小可行", prompt)

    def test_03_lite_mode_contains_lite_extra(self):
        """TC-RS-03: LITE 模式包含 lite 追加条款。"""
        prompt = self.engine.get_injection_prompt(role="solo_coder", mode=PonytailMode.LITE)
        self.assertIn("Lite 模式追加条款", prompt)
        # lite 不应包含 ultra 条款
        self.assertNotIn("Ultra 模式追加条款", prompt)

    def test_04_ultra_mode_contains_ultra_extra(self):
        """TC-RS-04: ULTRA 模式包含 ultra 追加条款。"""
        prompt = self.engine.get_injection_prompt(role="solo_coder", mode=PonytailMode.ULTRA)
        self.assertIn("Ultra 模式追加条款", prompt)
        self.assertIn("删除优先于添加", prompt)
        # ultra 不应包含 lite 条款
        self.assertNotIn("Lite 模式追加条款", prompt)

    def test_05_ultra_contains_no_re_arguing_constraint(self):
        """TC-RS-05: ULTRA 模式包含"不可 re-arguing"约束。"""
        prompt = self.engine.get_injection_prompt(role="solo_coder", mode=PonytailMode.ULTRA)
        self.assertIn("不可 re-arguing", prompt)

    def test_06_mode_header_contains_mode_and_role(self):
        """TC-RS-06: prompt 头部包含模式和角色标记。"""
        prompt = self.engine.get_injection_prompt(role="solo_coder", mode=PonytailMode.FULL)
        self.assertIn("模式：full", prompt)
        self.assertIn("角色：solo_coder", prompt)

    # ==================== 角色差异化测试 ====================

    def test_07_solo_coder_default_is_full(self):
        """TC-RS-07: solo_coder 角色默认注入 FULL。"""
        prompt = self.engine.get_injection_prompt(role="solo_coder")
        self.assertGreater(len(prompt), 200)
        self.assertIn("YAGNI", prompt)

    def test_08_test_expert_default_is_lite(self):
        """TC-RS-08: test_expert 角色默认注入 LITE。"""
        prompt = self.engine.get_injection_prompt(role="test_expert")
        self.assertGreater(len(prompt), 0)
        self.assertIn("Lite 模式追加条款", prompt)

    def test_09_product_manager_default_is_off(self):
        """TC-RS-09: product_manager 角色默认注入空。"""
        prompt = self.engine.get_injection_prompt(role="product_manager")
        self.assertEqual(prompt, "")

    def test_10_architect_default_is_full(self):
        """TC-RS-10: architect 角色默认注入 FULL。"""
        prompt = self.engine.get_injection_prompt(role="architect")
        self.assertGreater(len(prompt), 200)
        self.assertIn("模式：full", prompt)

    def test_11_ui_designer_default_is_lite(self):
        """TC-RS-11: ui_designer 角色默认注入 LITE。"""
        prompt = self.engine.get_injection_prompt(role="ui_designer")
        self.assertGreater(len(prompt), 0)
        self.assertIn("Lite 模式追加条款", prompt)

    # ==================== 红线与输出规范测试 ====================

    def test_12_red_lines_contains_16_items(self):
        """TC-RS-12: 红线段落包含 16 条不可简化项。"""
        prompt = self.engine.get_injection_prompt(role="solo_coder", mode=PonytailMode.FULL)
        self.assertIn("不可简化红线", prompt)
        # 项目规则追加的 3 条
        self.assertIn("真实业务逻辑", prompt)
        self.assertIn("需求文档规定的功能", prompt)
        self.assertIn("非平凡逻辑必须留一个可运行检查", prompt)
        # 架构师评审追加的 7 条
        self.assertIn("并发安全代码", prompt)
        self.assertIn("真实错误处理", prompt)
        self.assertIn("日志与审计", prompt)
        self.assertIn("配置与密钥管理", prompt)
        self.assertIn("数据库事务边界", prompt)
        self.assertIn("API 契约", prompt)
        self.assertIn("隐私数据处理", prompt)

    def test_13_output_spec_contains_ponytail_comment_format(self):
        """TC-RS-13: 输出规范段包含 ponytail 注释标记说明。"""
        prompt = self.engine.get_injection_prompt(role="solo_coder", mode=PonytailMode.FULL)
        self.assertIn("输出规范", prompt)
        self.assertIn("ponytail:", prompt)

    # ==================== 边界场景测试 ====================

    def test_14_unknown_role_falls_back_to_off(self):
        """TC-RS-14: 未知角色回退到 OFF（返回空字符串）。"""
        prompt = self.engine.get_injection_prompt(role="unknown_role")
        self.assertEqual(prompt, "")

    def test_15_thread_safety_concurrent_calls(self):
        """TC-RS-15: 线程安全 - 100 次并发调用结果与串行一致。"""
        results = {}
        errors = []

        def call_engine(idx):
            """线程函数：调用引擎并记录结果。"""
            try:
                role = ["solo_coder", "architect", "test_expert", "product_manager"][idx % 4]
                prompt = self.engine.get_injection_prompt(role=role)
                results[idx] = (role, prompt)
            except Exception as e:
                errors.append(e)

        # 串行调用获取基线
        baseline = {}
        for i in range(100):
            role = ["solo_coder", "architect", "test_expert", "product_manager"][i % 4]
            baseline[i] = (role, self.engine.get_injection_prompt(role=role))

        # 并发调用
        threads = [threading.Thread(target=call_engine, args=(i,)) for i in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 验证无错误
        self.assertEqual(len(errors), 0, f"并发调用产生错误: {errors}")

        # 验证结果一致
        for i in range(100):
            self.assertEqual(results[i], baseline[i], f"第 {i} 次调用结果不一致")

    # ==================== ROLE_INTENSITY 映射测试 ====================

    def test_16_role_intensity_mapping(self):
        """TC-RS-16: 角色强度映射表正确。"""
        expected = {
            "solo_coder": PonytailMode.FULL,
            "architect": PonytailMode.FULL,
            "test_expert": PonytailMode.LITE,
            "product_manager": PonytailMode.OFF,
            "ui_designer": PonytailMode.LITE,
        }
        for role, expected_mode in expected.items():
            actual = ROLE_INTENSITY.get(role)
            self.assertEqual(actual, expected_mode, f"角色 {role} 强度映射错误")

    def test_17_get_red_lines_method(self):
        """TC-RS-17: get_red_lines 返回红线文本。"""
        red_lines = self.engine.get_red_lines()
        self.assertIn("不可简化红线", red_lines)
        self.assertIn("真实业务逻辑", red_lines)

    def test_18_get_ladder_body_method(self):
        """TC-RS-18: get_ladder_body 返回决策梯主体。"""
        body = self.engine.get_ladder_body()
        self.assertIn("代码决策梯", body)
        self.assertIn("YAGNI", body)


if __name__ == "__main__":
    unittest.main()
