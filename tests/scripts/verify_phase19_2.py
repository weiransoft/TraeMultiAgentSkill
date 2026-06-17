"""Phase 19.2 注入点改造验证脚本。

验证内容：
1. ponytail 模块导入正常
2. handler 改造后语法正确，新参数可用
3. _dispatch_via_claude_code 签名变更正确
4. _build_agent_prompt 决策梯注入正常
"""
import sys
import os
from pathlib import Path

# 确保 scripts 目录在 path 中
# 脚本位于 tests/scripts/，scripts 目录位于项目根的 scripts/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = str(_PROJECT_ROOT / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def main():
    """主验证函数。"""
    # 1. 测试 ponytail 模块导入
    from ponytail.ruleset import PonytailRulesetEngine, PonytailMode, ROLE_INTENSITY
    from ponytail.mode_tracker import ModeTracker
    from ponytail.debt_collector import DebtCollector, DebtEntry
    print("[OK] ponytail 模块导入成功")

    # 2. 测试 handler 导入（验证改造后的文件语法正确）
    from autonomous.handlers.dev_handler import DevHandler
    from autonomous.handlers.fix_handler import FixHandler
    from autonomous.handlers.plan_handler import PlanHandler
    from autonomous.handlers.verify_handler import VerifyHandler
    print("[OK] handler 导入成功（改造后语法正确）")

    # 3. 测试 legacy 导入（验证 _dispatch_via_claude_code 签名变更）
    from dispatch.legacy import _dispatch_via_claude_code
    import inspect
    sig = inspect.signature(_dispatch_via_claude_code)
    params = list(sig.parameters.keys())
    assert "ponytail_prompt" in params, f"_dispatch_via_claude_code 缺少 ponytail_prompt 参数: {params}"
    print(f"[OK] _dispatch_via_claude_code 签名包含 ponytail_prompt: {params}")

    # 4. 测试 adapter 导入
    from claude_code_subagent_adapter import ClaudeCodeSubAgentAdapter
    adapter = ClaudeCodeSubAgentAdapter()
    print("[OK] ClaudeCodeSubAgentAdapter 导入成功")

    # 5. 验证 DevHandler 新参数
    engine = PonytailRulesetEngine()
    dh = DevHandler(
        dispatcher_adapter=None,
        smart_confirmation=None,
        auto_skill_loader=None,
        ponytail_engine=engine,
        project_root="/tmp",
        ponytail_mode=PonytailMode.FULL,
    )
    assert dh._ponytail_engine is not None, "DevHandler ponytail_engine 未注入"
    assert dh._project_root == "/tmp", "DevHandler project_root 未注入"
    assert dh._ponytail_mode == PonytailMode.FULL, "DevHandler ponytail_mode 未注入"
    print("[OK] DevHandler 构造成功，ponytail_engine + project_root + ponytail_mode 已注入")

    # 6. 验证 FixHandler 新参数
    fh = FixHandler(
        dispatcher_adapter=None,
        max_fix_attempts=2,
        ponytail_engine=engine,
        project_root="/tmp",
        ponytail_mode=PonytailMode.FULL,
    )
    assert fh._ponytail_engine is not None, "FixHandler ponytail_engine 未注入"
    assert fh._project_root == "/tmp", "FixHandler project_root 未注入"
    print("[OK] FixHandler 构造成功，ponytail_engine + project_root + ponytail_mode 已注入")

    # 7. 验证 PlanHandler 新参数
    ph = PlanHandler(
        auto_skill_loader=None,
        notes_memory=None,
        ponytail_engine=engine,
    )
    assert ph._ponytail_engine is not None, "PlanHandler ponytail_engine 未注入"
    print("[OK] PlanHandler 构造成功，ponytail_engine 已注入")

    # 8. 验证 VerifyHandler 新参数
    vh = VerifyHandler(
        git_driver=None,
        test_command="echo test",
        security_analyzer="builtin",
        ponytail_engine=engine,
        debt_collector=DebtCollector(),
        project_root="/tmp",
    )
    assert vh._ponytail_engine is not None, "VerifyHandler ponytail_engine 未注入"
    assert vh._debt_collector is not None, "VerifyHandler debt_collector 未注入"
    assert vh._project_root == "/tmp", "VerifyHandler project_root 未注入"
    print("[OK] VerifyHandler 构造成功，ponytail_engine + debt_collector + project_root 已注入")

    # 9. 验证 _build_agent_prompt 注入决策梯
    ponytail_prompt = engine.get_injection_prompt(role="solo_coder")
    assert ponytail_prompt, "solo_coder 决策梯为空"
    assert "Ponytail 决策梯" in ponytail_prompt, "决策梯缺少标题"
    assert "不可简化红线" in ponytail_prompt, "决策梯缺少红线段落"

    prompt = adapter._build_agent_prompt("solo_coder", "test task", {
        "ponytail_decision_ladder": ponytail_prompt,
    })
    assert "Ponytail 决策梯" in prompt, "决策梯未注入到 prompt"
    assert "不可简化红线" in prompt, "红线未注入到 prompt"
    print(f"[OK] _build_agent_prompt 决策梯注入成功，prompt 长度: {len(prompt)}")

    # 10. 验证角色差异化注入
    for role, expected_mode in [
        ("solo_coder", PonytailMode.FULL),
        ("architect", PonytailMode.FULL),
        ("test_expert", PonytailMode.LITE),
        ("product_manager", PonytailMode.OFF),
        ("ui_designer", PonytailMode.LITE),
    ]:
        rp = engine.get_injection_prompt(role=role)
        if expected_mode == PonytailMode.OFF:
            assert rp == "", f"{role} 应该返回空（OFF），实际返回长度 {len(rp)}"
        else:
            assert rp, f"{role} 应该返回非空（{expected_mode.value}），实际返回空"
            assert f"模式：{expected_mode.value}" in rp, f"{role} 模式标记不正确"
    print("[OK] 角色差异化注入验证通过（solo_coder=FULL, architect=FULL, test_expert=LITE, product_manager=OFF, ui_designer=LITE）")

    # 11. 验证 DebtCollector 基本功能
    dc = DebtCollector()
    # 创建临时测试目录
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        # 创建带 ponytail 注释的测试文件
        test_file = tmp_path / "test.py"
        test_file.write_text(
            "# ponytail: stdlib covers this\n"
            "# ponytail: global lock, per-account locks if throughput matters\n"
            "# ponytail: naive scan\n",
            encoding="utf-8",
        )
        entries = dc.collect(tmp_path)
        assert len(entries) == 3, f"应检测到 3 条债务，实际 {len(entries)}"
        no_trigger_count = sum(1 for e in entries if e.no_trigger)
        # "stdlib covers this" 没有 upgrade 关键词 → no_trigger=True
        # "global lock, per-account locks if throughput matters" 有 if → no_trigger=False
        # "naive scan" 没有 upgrade 关键词 → no_trigger=True
        assert no_trigger_count == 2, f"应检测到 2 条 no_trigger，实际 {no_trigger_count}"
    print("[OK] DebtCollector 基本功能验证通过（3 条债务，2 条 no_trigger）")

    print()
    print("=== Phase 19.2 注入点改造验证全部通过 ===")


if __name__ == "__main__":
    main()
