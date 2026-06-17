"""Ponytail 债务台账收割器单元测试。

覆盖 10 个用例：
- TC-DC-01~03: 基本债务检测（# 和 // 注释）
- TC-DC-04~05: 上限/升级路径识别
- TC-DC-06: no_trigger 检测
- TC-DC-07: 排除目录
- TC-DC-08: 代码文件过滤
- TC-DC-09: format_report
- TC-DC-10: 空项目
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

from ponytail.debt_collector import DebtCollector, DebtEntry


class TestDebtCollector(unittest.TestCase):
    """DebtCollector 单元测试。"""

    def setUp(self):
        """每个测试前创建临时目录。"""
        self.tmpdir = tempfile.mkdtemp()
        self.tmp_path = Path(self.tmpdir)

    def tearDown(self):
        """每个测试后清理临时目录。"""
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_detect_python_comment(self):
        """TC-DC-01: 检测 Python # 注释。"""
        test_file = self.tmp_path / "test.py"
        test_file.write_text("# ponytail: stdlib covers this\n", encoding="utf-8")
        dc = DebtCollector()
        entries = dc.collect(self.tmp_path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].content, "stdlib covers this")
        self.assertEqual(entries[0].line, 1)

    def test_02_detect_js_comment(self):
        """TC-DC-02: 检测 JS // 注释。"""
        test_file = self.tmp_path / "test.js"
        test_file.write_text("// ponytail: one-liner\n", encoding="utf-8")
        dc = DebtCollector()
        entries = dc.collect(self.tmp_path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].content, "one-liner")

    def test_03_detect_multiple_comments(self):
        """TC-DC-03: 检测多个注释。"""
        test_file = self.tmp_path / "test.py"
        test_file.write_text(
            "# ponytail: first\n"
            "code = 1\n"
            "# ponytail: second\n",
            encoding="utf-8",
        )
        dc = DebtCollector()
        entries = dc.collect(self.tmp_path)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].content, "first")
        self.assertEqual(entries[1].content, "second")

    def test_04_ceiling_keyword_detection(self):
        """TC-DC-04: 上限关键词识别。"""
        test_file = self.tmp_path / "test.py"
        test_file.write_text(
            "# ponytail: global lock\n"
            "# ponytail: naive scan\n"
            "# ponytail: simple code\n",
            encoding="utf-8",
        )
        dc = DebtCollector()
        entries = dc.collect(self.tmp_path)
        self.assertTrue(entries[0].has_ceiling)  # "global lock" has "lock"
        self.assertTrue(entries[1].has_ceiling)  # "naive scan" has "naive" + "scan"
        self.assertFalse(entries[2].has_ceiling)  # "simple code" no ceiling keyword

    def test_05_upgrade_path_detection(self):
        """TC-DC-05: 升级路径识别。"""
        test_file = self.tmp_path / "test.py"
        test_file.write_text(
            "# ponytail: upgrade to Redis when needed\n"
            "# ponytail: replace with stdlib if available\n"
            "# ponytail: just a note\n",
            encoding="utf-8",
        )
        dc = DebtCollector()
        entries = dc.collect(self.tmp_path)
        self.assertTrue(entries[0].has_upgrade_path)  # "upgrade" + "when"
        self.assertTrue(entries[1].has_upgrade_path)  # "replace" + "if"
        self.assertFalse(entries[2].has_upgrade_path)  # no upgrade keyword

    def test_06_no_trigger_detection(self):
        """TC-DC-06: no_trigger 检测（缺少升级路径）。"""
        test_file = self.tmp_path / "test.py"
        test_file.write_text(
            "# ponytail: stdlib covers this\n"
            "# ponytail: upgrade when needed\n",
            encoding="utf-8",
        )
        dc = DebtCollector()
        entries = dc.collect(self.tmp_path)
        self.assertTrue(entries[0].no_trigger)  # no upgrade keyword
        self.assertFalse(entries[1].no_trigger)  # has "upgrade" + "when"

    def test_07_exclude_directories(self):
        """TC-DC-07: 排除目录。"""
        # 创建 node_modules 目录（应被排除）
        nm_dir = self.tmp_path / "node_modules"
        nm_dir.mkdir()
        (nm_dir / "test.py").write_text("# ponytail: should be excluded\n", encoding="utf-8")
        # 创建正常文件
        (self.tmp_path / "main.py").write_text("# ponytail: included\n", encoding="utf-8")
        dc = DebtCollector()
        entries = dc.collect(self.tmp_path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].content, "included")

    def test_08_code_file_filter(self):
        """TC-DC-08: 只扫描代码文件。"""
        # 创建 .md 文件（不应被扫描）
        (self.tmp_path / "readme.md").write_text("# ponytail: in markdown\n", encoding="utf-8")
        # 创建 .py 文件（应被扫描）
        (self.tmp_path / "code.py").write_text("# ponytail: in python\n", encoding="utf-8")
        dc = DebtCollector()
        entries = dc.collect(self.tmp_path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].content, "in python")

    def test_09_format_report(self):
        """TC-DC-09: format_report 格式化报告。"""
        test_file = self.tmp_path / "test.py"
        test_file.write_text(
            "# ponytail: first\n"
            "# ponytail: upgrade when needed\n",
            encoding="utf-8",
        )
        dc = DebtCollector()
        entries = dc.collect(self.tmp_path)
        report = dc.format_report(entries)
        self.assertIn("2 markers", report)
        self.assertIn("1 with no trigger", report)
        self.assertIn("first", report)

    def test_10_empty_project(self):
        """TC-DC-10: 空项目返回空列表。"""
        dc = DebtCollector()
        entries = dc.collect(self.tmp_path)
        self.assertEqual(len(entries), 0)
        report = dc.format_report(entries)
        self.assertIn("Clean ledger", report)


if __name__ == "__main__":
    unittest.main()
