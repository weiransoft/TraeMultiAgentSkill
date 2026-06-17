"""Phase 19.3 债务台账与红线检测验证脚本。

验证内容：
1. debt_collector.py 导入 + 基本功能
2. requirement_tracer.py 导入 + 基本功能
3. karpathy_principle_enforcer.py 扩展检测模式（含白名单）
"""
import sys
import tempfile
from pathlib import Path

# 确保 scripts 目录在 path 中
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = str(_PROJECT_ROOT / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def main():
    """主验证函数。"""
    # 1. 测试 debt_collector 导入 + 基本功能
    from ponytail.debt_collector import DebtCollector, DebtEntry
    dc = DebtCollector()
    print("[OK] DebtCollector 导入成功")

    # 2. 测试 requirement_tracer 导入 + 基本功能
    from ponytail.requirement_tracer import RequirementTracer, Requirement, TraceReport
    rt = RequirementTracer()
    print("[OK] RequirementTracer 导入成功")

    # 3. 测试 ponytail 包导入
    from ponytail import (
        PonytailRulesetEngine, PonytailMode, ROLE_INTENSITY,
        ModeTracker, DebtCollector, DebtEntry,
        RequirementTracer, Requirement, TraceReport,
    )
    print("[OK] ponytail 包导入成功（全部子模块）")

    # 4. 测试 DebtCollector 完整功能
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        # 创建带 ponytail 注释的测试文件
        test_file = tmp_path / "test.py"
        test_file.write_text(
            "# ponytail: stdlib covers this\n"
            "# ponytail: global lock, per-account locks if throughput matters\n"
            "# ponytail: naive scan\n"
            "# ponytail: upgrade to Redis when throughput > 1k/s\n",
            encoding="utf-8",
        )
        entries = dc.collect(tmp_path)
        assert len(entries) == 4, f"应检测到 4 条债务，实际 {len(entries)}"
        no_trigger_count = sum(1 for e in entries if e.no_trigger)
        # "stdlib covers this" → no_trigger=True
        # "global lock, per-account locks if throughput matters" → has "if" → no_trigger=False
        # "naive scan" → no_trigger=True
        # "upgrade to Redis when throughput > 1k/s" → has "upgrade" + "when" → no_trigger=False
        assert no_trigger_count == 2, f"应检测到 2 条 no_trigger，实际 {no_trigger_count}"
        print(f"[OK] DebtCollector 完整功能验证通过（4 条债务，2 条 no_trigger）")

        # 测试 format_report
        report = dc.format_report(entries)
        assert "4 markers" in report, "报告应包含债务总数"
        assert "2 with no trigger" in report, "报告应包含 no_trigger 数"
        print("[OK] DebtCollector.format_report 验证通过")

    # 5. 测试 RequirementTracer 完整功能
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        # 创建需求文档
        doc_path = tmp_path / "requirements.md"
        doc_path.write_text(
            "# 需求文档\n\n"
            "## 功能需求\n"
            "- [REQ-001] 用户登录功能\n"
            "- [REQ-002] 数据导出功能\n"
            "- [REQ-003] 权限管理功能\n",
            encoding="utf-8",
        )

        # 创建代码实现（只实现 REQ-001 和 REQ-002）
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "auth.py").write_text(
            "# 用户登录实现\n"
            "def login(username, password):\n"
            "    return True\n",
            encoding="utf-8",
        )
        (src_dir / "export.py").write_text(
            "# 数据导出实现\n"
            "def export_data(data):\n"
            "    return data\n",
            encoding="utf-8",
        )
        # 不创建权限管理实现（REQ-003 未实现）

        report = rt.trace(doc_path, tmp_path)
        assert report.total == 3, f"应检测到 3 个需求，实际 {report.total}"
        assert report.implemented == 2, f"应检测到 2 个已实现，实际 {report.implemented}"
        assert report.missing == 1, f"应检测到 1 个未实现，实际 {report.missing}"
        assert len(report.missing_reqs) == 1, "未实现需求列表应有 1 项"
        assert report.missing_reqs[0].req_id == "REQ-003", "未实现需求应为 REQ-003"
        print(f"[OK] RequirementTracer 完整功能验证通过（3 需求，2 已实现，1 未实现）")

        # 测试 format_report
        report_text = rt.format_report(report)
        assert "未实现需求" in report_text, "报告应包含未实现需求段落"
        assert "REQ-003" in report_text, "报告应包含 REQ-003"
        print("[OK] RequirementTracer.format_report 验证通过")

    # 6. 测试 karpathy_principle_enforcer 扩展检测模式
    from karpathy_principle_enforcer import (
        KarpathyPrincipleEnforcer,
        PrincipleType,
        ViolationSeverity,
    )
    enforcer = KarpathyPrincipleEnforcer(project_root=".")

    # 验证新增模式存在
    simplicity_patterns = enforcer.VIOLATION_PATTERNS[PrincipleType.SIMPLICITY_FIRST]
    assert len(simplicity_patterns) >= 5, f"SIMPLICITY_FIRST 应有 ≥5 个模式，实际 {len(simplicity_patterns)}"

    surgical_patterns = enforcer.VIOLATION_PATTERNS[PrincipleType.SURGICAL_CHANGES]
    assert len(surgical_patterns) >= 5, f"SURGICAL_CHANGES 应有 ≥5 个模式，实际 {len(surgical_patterns)}"

    # 验证白名单字段存在
    mock_pattern = next(
        (p for p in surgical_patterns if "mock" in p["pattern"].lower()),
        None,
    )
    assert mock_pattern is not None, "应包含 mock 检测模式"
    assert "file_whitelist" in mock_pattern, "mock 模式应有 file_whitelist"
    assert "tests/" in mock_pattern["file_whitelist"], "mock 模式白名单应包含 tests/"
    print("[OK] karpathy_principle_enforcer 扩展模式验证通过（含白名单）")

    # 7. 测试白名单功能：测试文件中的 mock 不应被报告
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        # 创建测试文件（应被白名单排除）
        test_file = tmp_path / "tests" / "test_mock.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text(
            "from unittest.mock import Mock\n"
            "def test_something():\n"
            "    m = Mock()\n"
            "    assert m is not None\n",
            encoding="utf-8",
        )
        violations = enforcer.scan_file(str(test_file))
        # 测试文件中的 mock 应被白名单排除
        mock_violations = [v for v in violations if "mock" in v.description.lower()]
        assert len(mock_violations) == 0, f"测试文件中的 mock 不应被报告，实际 {len(mock_violations)} 个违规"
        print("[OK] 白名单功能验证通过（测试文件中的 mock 不被报告）")

        # 创建生产文件（应被报告）
        prod_file = tmp_path / "src" / "service.py"
        prod_file.parent.mkdir(parents=True)
        prod_file.write_text(
            "from unittest.mock import Mock\n"
            "def process():\n"
            "    m = Mock()\n"
            "    return m()\n",
            encoding="utf-8",
        )
        violations = enforcer.scan_file(str(prod_file))
        mock_violations = [v for v in violations if "mock" in v.description.lower()]
        assert len(mock_violations) > 0, "生产文件中的 mock 应被报告"
        print(f"[OK] 生产文件 mock 检测验证通过（{len(mock_violations)} 个违规）")

    print()
    print("=== Phase 19.3 债务台账与红线检测验证全部通过 ===")


if __name__ == "__main__":
    main()
