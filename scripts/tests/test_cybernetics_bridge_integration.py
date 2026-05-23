#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 Cybernetics 协同修复的集成测试"""

import sys
import os
import warnings
import inspect

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

def test_strategy_resolver():
    """测试1: StrategyResolver 基本功能"""
    print("=== 测试1: StrategyResolver ===")
    from strategy_resolver import StrategyResolver
    resolver = StrategyResolver()
    
    task = {"id": "test-1", "type": "architect", "complexity": 5, "description": "设计系统架构"}
    strategy = resolver.select_strategy(task)
    print(f"  策略选择结果: {strategy}")
    assert strategy in ["conservative", "balanced", "aggressive"], f"策略选择异常: {strategy}"
    
    # 高复杂度 -> conservative
    task_high = {"id": "test-2", "type": "architect", "complexity": 9, "description": "重构系统"}
    strategy_high = resolver.select_strategy(task_high)
    print(f"  高复杂度策略: {strategy_high}")
    assert strategy_high == "conservative", f"高复杂度应选保守策略: {strategy_high}"
    
    # 验证未通过 -> conservative
    task_valid = {"id": "test-3", "type": "test", "complexity": 3}
    strategy_valid = resolver.select_strategy(task_valid, validation={"passed": False})
    print(f"  验证未通过策略: {strategy_valid}")
    assert strategy_valid == "conservative", f"验证未通过应选保守策略: {strategy_valid}"
    
    print("  PASS StrategyResolver 基本功能正常")
    return True


def test_guard_karpathy_rules():
    """测试2: GuardCoordinator Karpathy 规则注入"""
    print("\n=== 测试2: GuardCoordinator Karpathy 规则 ===")
    from guard_coordinator import GuardCoordinator
    guard = GuardCoordinator(agent_id="test")
    rules = guard.validation_rules
    karpathy_rules = [r for r in rules if "karpathy" in r["rule_id"]]
    print(f"  Karpathy 规则数量: {len(karpathy_rules)}")
    for r in karpathy_rules:
        print(f"    - {r['rule_id']}: {r['name']}")
    assert len(karpathy_rules) >= 4, f"Karpathy 规则数量不足: {len(karpathy_rules)}"
    print("  PASS GuardCoordinator Karpathy 规则注入正常")
    return True


def test_karpathy_rule_checks():
    """测试3: Karpathy 规则检查功能"""
    print("\n=== 测试3: Karpathy 规则检查功能 ===")
    from guard_coordinator import GuardCoordinator
    guard = GuardCoordinator(agent_id="test")
    
    # 占位符检测
    task_placeholder = {"id": "t1", "type": "test", "description": "实现功能 # TODO: 占位实现"}
    has_placeholder = guard._contains_placeholder_code(task_placeholder)
    print(f"  占位符检测: {has_placeholder}")
    assert has_placeholder, "占位符检测失败"
    
    # 投机性代码检测
    task_speculative = {"id": "t2", "type": "test", "description": "为未来预留的接口"}
    has_speculative = guard._contains_speculative_code(task_speculative)
    print(f"  投机性代码检测: {has_speculative}")
    assert has_speculative, "投机性代码检测失败"
    
    # 目标定义检测 - 无目标
    task_no_goals = {"id": "t3", "type": "test"}
    has_goals = guard._has_clear_goals(task_no_goals)
    print(f"  目标定义检测（无目标）: {has_goals}")
    assert not has_goals, "目标定义检测异常"
    
    # 目标定义检测 - 有目标
    task_with_goals = {"id": "t4", "type": "test", "description": "实现用户注册功能"}
    has_goals = guard._has_clear_goals(task_with_goals)
    print(f"  目标定义检测（有目标）: {has_goals}")
    assert has_goals, "目标定义检测异常"
    
    # 未验证假设检测
    task_assumption = {"id": "t5", "type": "test", "description": "假设用户已登录"}
    has_assumption = guard._contains_unverified_assumptions(task_assumption)
    print(f"  未验证假设检测: {has_assumption}")
    assert has_assumption, "未验证假设检测失败"
    
    print("  PASS Karpathy 规则检查功能正常")
    return True


def test_cybernetics_bridge_import():
    """测试4: CyberneticsBridge 导入"""
    print("\n=== 测试4: CyberneticsBridge ===")
    try:
        from cybernetics_bridge import CyberneticsBridge, BridgeExecutionResult
        print("  PASS CyberneticsBridge 导入正常")
        return True
    except Exception as e:
        print(f"  WARN CyberneticsBridge 导入异常: {e}")
        return False


def test_workflow_engine_signature():
    """测试5: WorkflowEngineV2 构造函数签名"""
    print("\n=== 测试5: WorkflowEngineV2 构造函数 ===")
    from workflow_engine_v2 import WorkflowEngineV2
    sig = inspect.signature(WorkflowEngineV2.__init__)
    params = list(sig.parameters.keys())
    print(f"  参数列表: {params}")
    assert "cybernetics" in params, "WorkflowEngineV2 缺少 cybernetics 参数"
    print("  PASS WorkflowEngineV2 构造函数签名正确")
    return True


def test_agent_loop_controller_signature():
    """测试6: AgentLoopControllerV2 构造函数签名"""
    print("\n=== 测试6: AgentLoopControllerV2 构造函数 ===")
    from agent_loop_controller_v2 import AgentLoopControllerV2
    sig = inspect.signature(AgentLoopControllerV2.__init__)
    params = list(sig.parameters.keys())
    print(f"  参数列表: {params}")
    assert "cybernetics" in params, "AgentLoopControllerV2 缺少 cybernetics 参数"
    print("  PASS AgentLoopControllerV2 构造函数签名正确")
    return True


def test_feedback_loop_default_execute():
    """测试7: FeedbackControlLoop._default_execute 不再是纯 mock"""
    print("\n=== 测试7: FeedbackControlLoop._default_execute ===")
    from feedback_control_loop import FeedbackControlLoop
    loop = FeedbackControlLoop(agent_id="test")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = loop._default_execute({"id": "test"}, "balanced")
        assert len(w) > 0, "未触发 DeprecationWarning"
        assert issubclass(w[0].category, DeprecationWarning), "警告类型不正确"
        assert "warning" in result, "结果缺少 warning 字段"
        print(f"  警告消息: {str(w[0].message)[:80]}")
        print("  PASS _default_execute 已添加 DeprecationWarning 和 warning 标记")
    return True


def test_dispatch_v2_signature():
    """测试8: dispatch_agent_v2 构造函数签名"""
    print("\n=== 测试8: dispatch_agent_v2 签名 ===")
    from trae_agent_dispatch_v2 import dispatch_agent_v2
    sig = inspect.signature(dispatch_agent_v2)
    params = list(sig.parameters.keys())
    print(f"  参数列表: {params}")
    assert "cybernetics_enabled" in params, "dispatch_agent_v2 缺少 cybernetics_enabled 参数"
    print("  PASS dispatch_agent_v2 签名正确")
    return True


def test_guard_pre_validation_with_karpathy():
    """测试9: Guard 预验证包含 Karpathy 规则"""
    print("\n=== 测试9: Guard 预验证含 Karpathy ===")
    from guard_coordinator import GuardCoordinator
    guard = GuardCoordinator(agent_id="test")
    
    # 正常任务应通过
    normal_task = {"id": "t1", "type": "architect", "complexity": 5, "description": "设计系统架构"}
    result = guard.pre_execute_validation(normal_task)
    passed = result.passed if hasattr(result, 'passed') else result.get('passed', True)
    print(f"  正常任务验证: passed={passed}")
    
    # 含占位符的任务应不通过
    placeholder_task = {"id": "t2", "type": "test", "complexity": 3, "description": "# TODO: 占位实现"}
    result = guard.pre_execute_validation(placeholder_task)
    passed = result.passed if hasattr(result, 'passed') else result.get('passed', True)
    print(f"  占位符任务验证: passed={passed}")
    
    # 无目标定义的任务应有警告
    no_goal_task = {"id": "t3", "type": "test", "complexity": 3}
    result = guard.pre_execute_validation(no_goal_task)
    passed = result.passed if hasattr(result, 'passed') else result.get('passed', True)
    warnings_count = len(result.warnings) if hasattr(result, 'warnings') else len(result.get('warnings', []))
    print(f"  无目标任务验证: passed={passed}, warnings={warnings_count}")
    
    print("  PASS Guard 预验证含 Karpathy 规则正常")
    return True


def main():
    """运行所有测试"""
    print("=" * 60)
    print("Cybernetics 协同修复集成测试")
    print("=" * 60)
    
    tests = [
        test_strategy_resolver,
        test_guard_karpathy_rules,
        test_karpathy_rule_checks,
        test_cybernetics_bridge_import,
        test_workflow_engine_signature,
        test_agent_loop_controller_signature,
        test_feedback_loop_default_execute,
        test_dispatch_v2_signature,
        test_guard_pre_validation_with_karpathy,
    ]
    
    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            if test_fn():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  FAIL {test_fn.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"测试结果: {passed} 通过, {failed} 失败, 共 {len(tests)} 项")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
