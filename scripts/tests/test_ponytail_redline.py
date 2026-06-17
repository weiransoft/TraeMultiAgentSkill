"""Ponytail 红线违规检测单元测试。

覆盖 10 个用例：
- TC-RL-01~03: 红线清单完整性（16 条）
- TC-RL-04~05: 红线内容验证（关键条目）
- TC-RL-06~07: enforcer 检测 mock 在生产代码
- TC-RL-08: enforcer 白名单（测试文件中的 mock 不报告）
- TC-RL-09: requirement_tracer 检测未实现需求
- TC-RL-10: requirement_tracer 全部实现
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# 确保 scripts 目录在 path 中
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from ponytail.ruleset import PonytailRulesetEngine
from ponytail.requirement_tracer import RequirementTracer


class TestPonytailRedline(unittest.TestCase):
    """Ponytail 红线违规检测单元测试。"""

    def setUp(self):
        """每个测试前创建引擎和临时目录。"""
        self.engine = PonytailRulesetEngine()
        self.tmpdir = tempfile.mkdtemp()
        self.tmp_path = Path(self.tmpdir)

    def tearDown(self):
        """每个测试后清理临时目录。"""
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_red_lines_contains_16_items(self):
        """TC-RL-01: 红线包含 16 条。"""
        red_lines = self.engine.get_red_lines()
        # 统计编号 1-16
        import re
        numbers = re.findall(r'^(\d+)\.', red_lines, re.MULTILINE)
        self.assertEqual(len(numbers), 16, f"应有 16 条红线，实际 {len(numbers)}")

    def test_02_red_lines_contains_ponytail_original(self):
        """TC-RL-02: 红线包含 ponytail 原版 6 条。"""
        red_lines = self.engine.get_red_lines()
        self.assertIn("信任边界的输入校验", red_lines)
        self.assertIn("防止数据丢失的错误处理", red_lines)
        self.assertIn("安全措施", red_lines)
        self.assertIn("无障碍基础", red_lines)
        self.assertIn("用户明确要求保留的功能", red_lines)
        self.assertIn("真实硬件的校准旋钮", red_lines)

    def test_03_red_lines_contains_project_rules(self):
        """TC-RL-03: 红线包含项目规则追加条目。"""
        red_lines = self.engine.get_red_lines()
        self.assertIn("真实业务逻辑", red_lines)
        self.assertIn("禁止用 mock/占位/stub 替代", red_lines)
        self.assertIn("需求文档规定的功能", red_lines)
        self.assertIn("并发安全代码", red_lines)
        self.assertIn("真实错误处理", red_lines)
        self.assertIn("日志与审计", red_lines)
        self.assertIn("数据库事务边界", red_lines)
        self.assertIn("API 契约", red_lines)
        self.assertIn("隐私数据处理", red_lines)

    def test_04_red_lines_in_full_mode_prompt(self):
        """TC-RL-04: FULL 模式 prompt 包含红线段落。"""
        prompt = self.engine.get_injection_prompt(role="solo_coder", mode=None)
        self.assertIn("不可简化红线", prompt)
        self.assertIn("16", prompt)  # 16 条

    def test_05_red_lines_in_ultra_mode_prompt(self):
        """TC-RL-05: ULTRA 模式 prompt 包含红线段落。"""
        from ponytail.ruleset import PonytailMode
        prompt = self.engine.get_injection_prompt(
            role="solo_coder", mode=PonytailMode.ULTRA
        )
        self.assertIn("不可简化红线", prompt)
        self.assertIn("Ultra 模式追加条款", prompt)

    def test_06_enforcer_detects_mock_in_production(self):
        """TC-RL-06: enforcer 检测生产代码中的 mock。"""
        from karpathy_principle_enforcer import KarpathyPrincipleEnforcer
        enforcer = KarpathyPrincipleEnforcer(project_root=".")

        # 创建生产文件（应被报告）
        prod_file = self.tmp_path / "service.py"
        prod_file.write_text(
            "from unittest.mock import Mock\n"
            "def process():\n"
            "    m = Mock()\n"
            "    return m()\n",
            encoding="utf-8",
        )
        violations = enforcer.scan_file(str(prod_file))
        mock_violations = [v for v in violations if "mock" in v.description.lower()]
        self.assertGreater(len(mock_violations), 0, "生产文件中的 mock 应被报告")

    def test_07_enforcer_whitelist_test_files(self):
        """TC-RL-07: enforcer 白名单测试文件中的 mock。"""
        from karpathy_principle_enforcer import KarpathyPrincipleEnforcer
        enforcer = KarpathyPrincipleEnforcer(project_root=".")

        # 创建测试文件（应被白名单排除）
        test_file = self.tmp_path / "tests" / "test_mock.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text(
            "from unittest.mock import Mock\n"
            "def test_something():\n"
            "    m = Mock()\n"
            "    assert m is not None\n",
            encoding="utf-8",
        )
        violations = enforcer.scan_file(str(test_file))
        mock_violations = [v for v in violations if "mock" in v.description.lower()]
        self.assertEqual(len(mock_violations), 0, "测试文件中的 mock 不应被报告")

    def test_08_enforcer_detects_pass_without_ponytail(self):
        """TC-RL-08: enforcer 检测无 ponytail 标记的 pass（带上下文白名单）。"""
        from karpathy_principle_enforcer import KarpathyPrincipleEnforcer
        enforcer = KarpathyPrincipleEnforcer(project_root=".")

        # 创建带 pass 的文件（不在 class/def/except 上下文中）
        prod_file = self.tmp_path / "service.py"
        prod_file.write_text(
            "x = 1\n"
            "pass\n"
            "y = 2\n",
            encoding="utf-8",
        )
        violations = enforcer.scan_file(str(prod_file))
        pass_violations = [v for v in violations if "pass" in v.description.lower()]
        # 应检测到 pass（不在 class/def/except 上下文中）
        self.assertGreater(len(pass_violations), 0, "无 ponytail 标记的 pass 应被报告")

    def test_09_requirement_tracer_detects_missing(self):
        """TC-RL-09: requirement_tracer 检测未实现需求。"""
        tracer = RequirementTracer()

        # 创建需求文档
        doc_path = self.tmp_path / "requirements.md"
        doc_path.write_text(
            "- [REQ-001] 用户登录\n"
            "- [REQ-002] 数据导出\n"
            "- [REQ-003] 权限管理\n",
            encoding="utf-8",
        )

        # 只实现 REQ-001 和 REQ-002
        (self.tmp_path / "auth.py").write_text(
            "# 用户登录\n def login(): pass\n", encoding="utf-8"
        )
        (self.tmp_path / "export.py").write_text(
            "# 数据导出\n def export(): pass\n", encoding="utf-8"
        )

        report = tracer.trace(doc_path, self.tmp_path)
        self.assertEqual(report.total, 3)
        self.assertEqual(report.missing, 1)
        self.assertEqual(report.missing_reqs[0].req_id, "REQ-003")

    def test_10_requirement_tracer_all_implemented(self):
        """TC-RL-10: requirement_tracer 全部实现时无未实现需求。"""
        tracer = RequirementTracer()

        doc_path = self.tmp_path / "requirements.md"
        doc_path.write_text(
            "- [REQ-001] 用户登录\n"
            "- [REQ-002] 数据导出\n",
            encoding="utf-8",
        )

        (self.tmp_path / "auth.py").write_text(
            "# 用户登录\n def login(): pass\n", encoding="utf-8"
        )
        (self.tmp_path / "export.py").write_text(
            "# 数据导出\n def export(): pass\n", encoding="utf-8"
        )

        report = tracer.trace(doc_path, self.tmp_path)
        self.assertEqual(report.total, 2)
        self.assertEqual(report.implemented, 2)
        self.assertEqual(report.missing, 0)


if __name__ == "__main__":
    unittest.main()
