#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DocCodeConsistencyChecker 单元测试。

测试覆盖：
- T1: 解析 PRD 功能列表表格
- T2: 解析架构文档模块依赖
- T3: 扫描 Python 代码函数/类
- T4: 功能完成度：全部实现
- T5: 功能完成度：部分缺失
- T6: 集成完整性：import 匹配
- T7: 测试正确性：全部通过
- T8: 测试正确性：有失败
- T9: TODO/FIXME 扫描
- T10: 验收标准解析
- T11: 报告生成：通过场景
- T12: 报告生成：不通过场景
"""
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

# 确保能导入被测模块
_script_dir = Path(__file__).resolve().parent.parent
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))

from doc_code_consistency_checker import (
    AcceptanceCheckItem,
    CodeScanner,
    ConsistencyReport,
    DeviationItem,
    DocCodeConsistencyChecker,
    DocParser,
    FeatureCheckItem,
    GapItem,
    IntegrationCheckItem,
    TestCheckResult,
    TodoItem,
)


class TestDocParser(unittest.TestCase):
    """文档解析器测试。"""

    def test_t1_parse_features_from_table(self):
        """T1: 解析 PRD 功能列表表格。"""
        prd_content = textwrap.dedent("""\
        # 产品需求文档

        ## 2. 功能列表

        | 功能ID | 功能名称 | 功能描述 | 优先级 | 所属模块 | 状态 |
        |--------|----------|----------|--------|----------|------|
        | F-001 | 用户登录 | 用户通过账号密码登录 | P0 | auth | 已实现 |
        | F-002 | 用户注册 | 新用户注册账号 | P0 | auth | 已实现 |
        | F-003 | 密码重置 | 忘记密码时重置 | P1 | auth | 待实现 |

        ## 3. 其他章节
        """)
        features = DocParser.parse_features(prd_content, "prd.md")
        self.assertEqual(len(features), 3)
        self.assertEqual(features[0]["feature_id"], "F-001")
        self.assertEqual(features[0]["feature_name"], "用户登录")
        self.assertEqual(features[1]["feature_id"], "F-002")
        self.assertEqual(features[2]["feature_id"], "F-003")

    def test_t2_parse_integration_relations(self):
        """T2: 解析架构文档模块依赖。"""
        arch_content = textwrap.dedent("""\
        # 架构设计文档

        ## 3. 模块依赖

        - auth 模块 依赖 database 模块
        - api 模块 调用 auth 模块
        - frontend 模块→backend 模块
        """)
        relations = DocParser.parse_integration_relations(arch_content, "arch.md")
        self.assertGreaterEqual(len(relations), 3)
        # 验证提取了正确的依赖关系
        descs = [r["integration_desc"] for r in relations]
        self.assertIn("auth→database", descs)
        self.assertIn("api→auth", descs)
        self.assertIn("frontend→backend", descs)

    def test_t10_parse_acceptance_criteria(self):
        """T10: 验收标准解析。"""
        prd_content = textwrap.dedent("""\
        # PRD

        ## 验收标准

        | 编号 | 描述 | 验证方式 |
        |------|------|----------|
        | AC-001 | 登录响应时间 < 200ms | 测试 |
        | AC-002 | 注册成功后发送邮件 | 测试 |

        ## 其他

        - AC-003: 密码长度不少于 8 位
        """)
        criteria = DocParser.parse_acceptance_criteria(prd_content, "prd.md")
        self.assertGreaterEqual(len(criteria), 3)
        ids = [c["criteria_id"] for c in criteria]
        self.assertIn("AC-001", ids)
        self.assertIn("AC-002", ids)
        self.assertIn("AC-003", ids)


class TestCodeScanner(unittest.TestCase):
    """代码扫描器测试。"""

    def setUp(self):
        """创建临时项目目录。"""
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)

    def tearDown(self):
        """清理临时目录。"""
        self._tmpdir.cleanup()

    def test_t3_scan_python_functions_and_classes(self):
        """T3: 扫描 Python 代码函数/类。"""
        # 创建 Python 源码文件
        src_dir = self.project_root / "src"
        src_dir.mkdir()
        (src_dir / "auth.py").write_text(textwrap.dedent("""\
            # 认证模块
            class AuthService:
                def login(self, username, password):
                    pass
                def logout(self):
                    pass

            def check_token(token):
                pass
            """), encoding="utf-8")

        symbols, imports, todos = CodeScanner.scan_project(self.project_root)
        # 验证扫描到了函数和类
        names = [s.name for s in symbols]
        self.assertIn("AuthService", names)
        self.assertIn("login", names)
        self.assertIn("logout", names)
        self.assertIn("check_token", names)

    def test_t9_scan_todo_fixme(self):
        """T9: TODO/FIXME 扫描。"""
        src_dir = self.project_root / "src"
        src_dir.mkdir()
        (src_dir / "module.py").write_text(textwrap.dedent("""\
            # TODO: 实现错误处理
            def process(data):
                pass

            # FIXME: 修复空指针问题
            def validate(input):
                pass
            """), encoding="utf-8")

        _, _, todos = CodeScanner.scan_project(self.project_root)
        # 验证扫描到了 TODO 和 FIXME
        self.assertGreaterEqual(len(todos), 2)
        todo_types = [t.todo_type for t in todos]
        self.assertIn("TODO", todo_types)
        self.assertIn("FIXME", todo_types)


class TestDocCodeConsistencyChecker(unittest.TestCase):
    """DocCodeConsistencyChecker 核心测试。"""

    def setUp(self):
        """创建临时项目目录和文档。"""
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)

        # 创建 PRD 文档
        self.prd_path = self.project_root / "prd.md"
        self.prd_path.write_text(textwrap.dedent("""\
            # 产品需求文档

            ## 2. 功能列表

            | 功能ID | 功能名称 | 功能描述 | 优先级 | 所属模块 | 状态 |
            |--------|----------|----------|--------|----------|------|
            | F-001 | login | 用户登录功能 | P0 | auth | 已实现 |
            | F-002 | register | 用户注册功能 | P0 | auth | 已实现 |
            | F-003 | export | 数据导出功能 | P1 | data | 待实现 |

            ## 验收标准

            | 编号 | 描述 | 验证方式 |
            |------|------|----------|
            | AC-001 | 登录功能可用 | 测试 |
            | AC-002 | 注册功能可用 | 测试 |
            """), encoding="utf-8")

        # 创建架构文档
        self.arch_path = self.project_root / "architecture.md"
        self.arch_path.write_text(textwrap.dedent("""\
            # 架构设计文档

            ## 3. 模块依赖

            - auth 依赖 database
            - api 调用 auth
            """), encoding="utf-8")

        # 创建源码
        src_dir = self.project_root / "src"
        src_dir.mkdir()
        (src_dir / "auth.py").write_text(textwrap.dedent("""\
            # 认证模块
            from database import Database

            class AuthService:
                def login(self, username, password):
                    pass
                def register(self, username, password):
                    pass
            """), encoding="utf-8")
        (src_dir / "api.py").write_text(textwrap.dedent("""\
            from auth import AuthService

            class ApiServer:
                def start(self):
                    pass
            """), encoding="utf-8")

    def tearDown(self):
        """清理临时目录。"""
        self._tmpdir.cleanup()

    def _create_checker(self, test_command=""):
        """创建检查器实例。"""
        return DocCodeConsistencyChecker(
            project_root=self.project_root,
            doc_paths={
                "prd": self.prd_path,
                "architecture": self.arch_path,
            },
            test_command=test_command,
        )

    def test_t4_feature_completeness_all_implemented(self):
        """T4: 功能完成度：已实现的功能被正确识别。"""
        checker = self._create_checker()
        results = checker.check_feature_completeness()
        # F-001 login 和 F-002 register 应该被识别为已实现
        login_item = next(r for r in results if r.feature_id == "F-001")
        self.assertEqual(login_item.status, "implemented")
        self.assertIn("login", login_item.code_location)

        register_item = next(r for r in results if r.feature_id == "F-002")
        self.assertEqual(register_item.status, "implemented")

    def test_t5_feature_completeness_partial_missing(self):
        """T5: 功能完成度：未实现的功能被正确识别。"""
        checker = self._create_checker()
        results = checker.check_feature_completeness()
        # F-003 export 应该被识别为未实现
        export_item = next(r for r in results if r.feature_id == "F-003")
        self.assertEqual(export_item.status, "missing")

    def test_t6_integration_completeness_import_match(self):
        """T6: 集成完整性：import 匹配。"""
        checker = self._create_checker()
        results = checker.check_integration_completeness()
        # auth→database 和 api→auth 应该被识别为已连通
        connected = [r for r in results if r.status == "connected"]
        self.assertGreaterEqual(len(connected), 1)

    def test_t7_test_correctness_all_pass(self):
        """T7: 测试正确性：全部通过。"""
        # 创建一个简单的测试脚本
        test_script = self.project_root / "run_tests.py"
        test_script.write_text(textwrap.dedent("""\
            print("1 passed")
            print("0 failed")
            print("All tests passed")
            """), encoding="utf-8")
        checker = DocCodeConsistencyChecker(
            project_root=self.project_root,
            doc_paths={"prd": self.prd_path, "architecture": self.arch_path},
            test_command=f"python3 {test_script}",
        )
        result = checker.check_test_correctness()
        # 应该有通过的测试
        self.assertGreater(result.passed, 0)

    def test_t8_test_correctness_with_failure(self):
        """T8: 测试正确性：有失败。"""
        # 创建一个会输出失败信息的测试脚本
        test_script = self.project_root / "run_tests.py"
        test_script.write_text(textwrap.dedent("""\
            print("1 passed")
            print("1 failed")
            print("FAILED test_something")
            """), encoding="utf-8")
        checker = DocCodeConsistencyChecker(
            project_root=self.project_root,
            doc_paths={"prd": self.prd_path, "architecture": self.arch_path},
            test_command=f"python3 {test_script}",
        )
        result = checker.check_test_correctness()
        self.assertGreater(result.failed, 0)

    def test_t11_report_generation_passed(self):
        """T11: 报告生成：通过场景。"""
        # 创建一个全部通过的检查器
        # 添加测试目录使测试通过
        test_dir = self.project_root / "tests"
        test_dir.mkdir()
        (test_dir / "test_auth.py").write_text(textwrap.dedent("""\
            # F-001 login 测试
            # F-002 register 测试
            def test_login():
                pass
            def test_register():
                pass
            """), encoding="utf-8")

        test_script = self.project_root / "run_tests.py"
        test_script.write_text(textwrap.dedent("""\
            print("2 passed")
            print("0 failed")
            """), encoding="utf-8")

        checker = DocCodeConsistencyChecker(
            project_root=self.project_root,
            doc_paths={"prd": self.prd_path, "architecture": self.arch_path},
            test_command=f"python3 {test_script}",
        )
        report = checker.check_all()
        report_md = checker.generate_report(report)
        # 验证报告包含关键章节
        self.assertIn("文档对照代码审查报告", report_md)
        self.assertIn("D1 功能完成度", report_md)
        self.assertIn("D2 集成完整性", report_md)
        self.assertIn("D3 测试正确性", report_md)
        self.assertIn("D4 验收标准", report_md)
        self.assertIn("D5 TODO/FIXME", report_md)
        self.assertIn("D6 文档意图", report_md)

    def test_t12_report_generation_failed(self):
        """T12: 报告生成：不通过场景。"""
        checker = self._create_checker(test_command="")
        report = checker.check_all()
        # 由于 F-003 未实现且无测试，应该不通过
        self.assertFalse(report.overall_passed)
        self.assertGreater(len(report.gap_list), 0)
        report_md = checker.generate_report(report)
        self.assertIn("❌ 审查不通过", report_md)
        self.assertIn("缺口清单", report_md)


if __name__ == "__main__":
    unittest.main(verbosity=2)
