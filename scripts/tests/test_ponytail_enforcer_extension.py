"""Ponytail enforcer 扩展检测单元测试。

覆盖：
- enforcer 扩展模式存在性验证
- 白名单功能验证（file_whitelist + context_whitelist）
- 新增 ponytail 相关检测模式验证
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

from karpathy_principle_enforcer import (
    KarpathyPrincipleEnforcer,
    PrincipleType,
    ViolationSeverity,
)


class TestPonytailEnforcerExtension(unittest.TestCase):
    """Ponytail enforcer 扩展检测单元测试。"""

    def setUp(self):
        """每个测试前创建 enforcer 和临时目录。"""
        self.enforcer = KarpathyPrincipleEnforcer(project_root=".")
        self.tmpdir = tempfile.mkdtemp()
        self.tmp_path = Path(self.tmpdir)

    def tearDown(self):
        """每个测试后清理临时目录。"""
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_simplicity_first_has_ponytail_patterns(self):
        """验证 SIMPLICITY_FIRST 包含 ponytail 扩展模式。"""
        patterns = self.enforcer.VIOLATION_PATTERNS[PrincipleType.SIMPLICITY_FIRST]
        # 应包含 YAGNI 违规检测（Manager/Handler/Controller/Service + ponytail）
        yagni_pattern = next(
            (p for p in patterns if "Manager" in p["pattern"] and "ponytail" in p["pattern"]),
            None,
        )
        self.assertIsNotNone(yagni_pattern, "应包含 YAGNI 违规检测模式")

        # 应包含新增依赖检测
        dep_pattern = next(
            (p for p in patterns if "new" in p["pattern"].lower() and "dep" in p["pattern"].lower()),
            None,
        )
        self.assertIsNotNone(dep_pattern, "应包含新增依赖检测模式")

    def test_02_surgical_changes_has_ponytail_patterns(self):
        """验证 SURGICAL_CHANGES 包含 ponytail 扩展模式。"""
        patterns = self.enforcer.VIOLATION_PATTERNS[PrincipleType.SURGICAL_CHANGES]
        # 应包含 pass 检测
        pass_pattern = next(
            (p for p in patterns if p["pattern"] == r"^\s*pass\s*$"),
            None,
        )
        self.assertIsNotNone(pass_pattern, "应包含 pass 检测模式")
        self.assertIn("context_whitelist", pass_pattern, "pass 模式应有 context_whitelist")

        # 应包含 mock 红线检测（pattern 使用 r"from\s+unittest\.mock\s+import\s+Mock"，
        # 其中 \. 是正则转义，所以检查 "unittest" 和 "mock" 两个子串）
        mock_pattern = next(
            (p for p in patterns
             if "unittest" in p["pattern"] and "mock" in p["pattern"]),
            None,
        )
        self.assertIsNotNone(mock_pattern, "应包含 unittest.mock 检测模式")
        self.assertIn("file_whitelist", mock_pattern, "mock 模式应有 file_whitelist")

    def test_03_file_whitelist_excludes_test_files(self):
        """验证 file_whitelist 排除测试文件。"""
        # 创建测试文件
        test_file = self.tmp_path / "tests" / "test_service.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text(
            "from unittest.mock import Mock\n"
            "def test_something():\n"
            "    m = Mock()\n",
            encoding="utf-8",
        )
        violations = self.enforcer.scan_file(str(test_file))
        mock_violations = [v for v in violations if "mock" in v.description.lower()]
        self.assertEqual(len(mock_violations), 0, "测试文件中的 mock 应被白名单排除")

    def test_04_file_whitelist_includes_production_files(self):
        """验证 file_whitelist 不排除生产文件。"""
        # 创建生产文件
        prod_file = self.tmp_path / "src" / "service.py"
        prod_file.parent.mkdir(parents=True)
        prod_file.write_text(
            "from unittest.mock import Mock\n"
            "def process():\n"
            "    m = Mock()\n",
            encoding="utf-8",
        )
        violations = self.enforcer.scan_file(str(prod_file))
        mock_violations = [v for v in violations if "mock" in v.description.lower()]
        self.assertGreater(len(mock_violations), 0, "生产文件中的 mock 应被报告")

    def test_05_context_whitelist_excludes_class_def(self):
        """验证 context_whitelist 排除 class/def/except 上下文中的 pass。"""
        # 创建带 class/def 的文件（pass 在合法上下文中）
        prod_file = self.tmp_path / "service.py"
        prod_file.write_text(
            "class BaseService:\n"
            "    pass\n"
            "def abstract_method():\n"
            "    pass\n",
            encoding="utf-8",
        )
        violations = self.enforcer.scan_file(str(prod_file))
        pass_violations = [v for v in violations if "pass" in v.description.lower()]
        # class/def 上下文中的 pass 应被白名单排除
        self.assertEqual(len(pass_violations), 0, "class/def 上下文中的 pass 应被白名单排除")

    def test_06_context_whitelist_includes_standalone_pass(self):
        """验证 context_whitelist 不排除独立 pass。"""
        # 创建带独立 pass 的文件（不在 class/def/except 上下文中）
        prod_file = self.tmp_path / "service.py"
        prod_file.write_text(
            "x = 1\n"
            "pass\n"
            "y = 2\n",
            encoding="utf-8",
        )
        violations = self.enforcer.scan_file(str(prod_file))
        pass_violations = [v for v in violations if "pass" in v.description.lower()]
        self.assertGreater(len(pass_violations), 0, "独立 pass 应被报告")

    def test_07_ponytail_yagni_pattern_detection(self):
        """验证 ponytail YAGNI 违规检测。"""
        # 创建带 ponytail 标记的抽象类
        prod_file = self.tmp_path / "service.py"
        prod_file.write_text(
            "class UserManager:  # ponytail: maybe needed\n"
            "    pass\n",
            encoding="utf-8",
        )
        violations = self.enforcer.scan_file(str(prod_file))
        yagni_violations = [
            v for v in violations
            if "YAGNI" in v.description or "抽象类" in v.description
        ]
        self.assertGreater(len(yagni_violations), 0, "应检测到 YAGNI 违规")

    def test_08_ponytail_new_dep_pattern_detection(self):
        """验证 ponytail 新增依赖检测。"""
        prod_file = self.tmp_path / "service.py"
        prod_file.write_text(
            "import requests  # ponytail: new dep\n",
            encoding="utf-8",
        )
        violations = self.enforcer.scan_file(str(prod_file))
        dep_violations = [
            v for v in violations
            if "依赖" in v.description or "dep" in v.description.lower()
        ]
        self.assertGreater(len(dep_violations), 0, "应检测到新增依赖违规")


if __name__ == "__main__":
    unittest.main()
