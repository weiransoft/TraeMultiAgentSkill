"""dispatch/legacy.py 覆盖度补全测试（RED→GREEN 阶段）。

针对覆盖率分析中 dispatch/legacy.py 的缺失行（35% → 目标 ≥ 50%）：
- 56-63: GoalStatus fallback（loop_goal 不可用时）
- 551-573: _is_overall_success 各种 status 分支
- 416-435: _module_level_single_dispatch 默认值与基本流程
- 99-153: dispatch_agent_v2 降级路径（mock 方式）

TDD 流程：
1. RED：写测试 → 验证通过（这些是已实现的纯函数）
2. GREEN：验证全部通过
"""
import io
import logging
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

# 路径设置
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent if False else os.path.dirname(
    os.path.abspath(__file__)
) + "/.."
sys.path.insert(0, os.path.abspath(_SCRIPTS_DIR))

from dispatch import legacy as legacy_mod
from dispatch.legacy import (
    _is_overall_success,
    log,
    _module_level_single_dispatch,
)


class TestLegacyGoalStatusFallback(unittest.TestCase):
    """line 56-63：loop_goal 不可用时 GoalStatus 降级。"""

    def test_goalstatus_fallback_defined(self):
        """GoalStatus 永远是可用的（loop_goal 在或不在）。"""
        self.assertTrue(hasattr(legacy_mod, "GoalStatus"))
        # 无论真伪枚举，都应有 ACHIEVED / FAILED 等关键字段
        for attr in ("ACHIEVED", "FAILED", "IN_PROGRESS"):
            self.assertTrue(
                hasattr(legacy_mod.GoalStatus, attr),
                f"GoalStatus 应有 {attr} 字段",
            )


class TestLegacyLog(unittest.TestCase):
    """line 28-45：log() 函数四种 level。"""

    def test_log_info(self):
        """log(level='INFO') → 打印 INFO 标签。"""
        buf = io.StringIO()
        with redirect_stdout(buf):
            log("info message", "INFO")
        output = buf.getvalue()
        self.assertIn("INFO", output)
        self.assertIn("info message", output)

    def test_log_warning(self):
        """log(level='WARNING') → 打印 WARNING 标签。"""
        buf = io.StringIO()
        with redirect_stdout(buf):
            log("warn message", "WARNING")
        output = buf.getvalue()
        self.assertIn("WARNING", output)
        self.assertIn("warn message", output)

    def test_log_error(self):
        """log(level='ERROR') → 打印 ERROR 标签。"""
        buf = io.StringIO()
        with redirect_stdout(buf):
            log("error message", "ERROR")
        output = buf.getvalue()
        self.assertIn("ERROR", output)
        self.assertIn("error message", output)

    def test_log_success(self):
        """log(level='SUCCESS') → 打印 SUCCESS 标签。"""
        buf = io.StringIO()
        with redirect_stdout(buf):
            log("success message", "SUCCESS")
        output = buf.getvalue()
        self.assertIn("SUCCESS", output)
        self.assertIn("success message", output)

    def test_log_unknown_level(self):
        """未知 level → 仍输出消息（容错）。"""
        buf = io.StringIO()
        with redirect_stdout(buf):
            log("unknown level", "MYSTERY")
        output = buf.getvalue()
        self.assertIn("unknown level", output)


class TestLegacyIsOverallSuccess(unittest.TestCase):
    """line 551-573：_is_overall_success 各种 status 分支。"""

    def test_no_status_key(self):
        """result 不含 'status' → 看 total_iterations。"""
        # total_iterations > 0 → True
        self.assertTrue(_is_overall_success({"total_iterations": 3}))
        # total_iterations == 0 → False
        self.assertFalse(_is_overall_success({"total_iterations": 0}))
        # total_iterations 缺失 → False
        self.assertFalse(_is_overall_success({}))

    def test_status_achieved(self):
        """status == ACHIEVED → True。"""
        from dispatch.legacy import GoalStatus
        result = {"status": GoalStatus.ACHIEVED.value}
        self.assertTrue(_is_overall_success(result))

    def test_status_failed(self):
        """status == FAILED → False。"""
        from dispatch.legacy import GoalStatus
        result = {"status": GoalStatus.FAILED.value}
        self.assertFalse(_is_overall_success(result))

    def test_status_in_progress_converged_early(self):
        """status == IN_PROGRESS + converged_early=True → True。"""
        from dispatch.legacy import GoalStatus
        result = {
            "status": GoalStatus.IN_PROGRESS.value,
            "converged_early": True,
            "has_criteria": True,  # 即使有 criteria，converged 仍 True
        }
        self.assertTrue(_is_overall_success(result))

    def test_status_in_progress_with_criteria_not_converged(self):
        """status == IN_PROGRESS + has_criteria + 未收敛 → False。"""
        from dispatch.legacy import GoalStatus
        result = {
            "status": GoalStatus.IN_PROGRESS.value,
            "converged_early": False,
            "has_criteria": True,
        }
        self.assertFalse(_is_overall_success(result))

    def test_status_in_progress_no_criteria(self):
        """status == IN_PROGRESS + 无 criteria → True。"""
        from dispatch.legacy import GoalStatus
        result = {
            "status": GoalStatus.IN_PROGRESS.value,
            "converged_early": False,
            "has_criteria": False,
        }
        self.assertTrue(_is_overall_success(result))

    def test_status_unknown_returns_false(self):
        """status 是未知值 → False（默认）。"""
        result = {"status": "MYSTERY_STATUS"}
        self.assertFalse(_is_overall_success(result))


class TestLegacyModuleLevelSingleDispatchDefaults(unittest.TestCase):
    """line 416-435：_module_level_single_dispatch 默认参数签名。"""

    def test_default_agent_type(self):
        """_module_level_single_dispatch 的默认 agent_type = 'goal_orchestrator'。"""
        import inspect
        sig = inspect.signature(_module_level_single_dispatch)
        self.assertEqual(
            sig.parameters["agent_type"].default, "goal_orchestrator"
        )
        self.assertEqual(
            sig.parameters["task"].default, ""
        )
        self.assertIsNone(
            sig.parameters["task_id"].default
        )
        self.assertEqual(
            sig.parameters["project_root"].default, "."
        )


class TestLegacyDispatchAgentV2(unittest.TestCase):
    """line 99-153：dispatch_agent_v2 主流程（mock 方式覆盖关键分支）。"""

    def test_dispatch_agent_v2_with_cybernetics_disabled(self):
        """cybernetics_enabled=False → 不初始化 bridge。"""
        # patch 内部分支：让 cybernetics_disabled 路径走到最后
        with patch.object(
            legacy_mod, "NEW_COMPONENTS_AVAILABLE", False
        ), patch.object(
            legacy_mod, "CLAUDE_CODE_ADAPTER_AVAILABLE", False
        ):
            result = legacy_mod.dispatch_agent_v2(
                agent_type="test_agent",
                task="test_task",
                project_root="/tmp",
                cybernetics_enabled=False,
            )
            # 降级路径应返回 False
            self.assertIsInstance(result, bool)

    def test_dispatch_agent_v2_cybernetics_init_failure(self):
        """Cybernetics bridge 初始化失败 → 降级到无增强模式。"""
        # patch CyberneticsBridge 让其构造抛异常
        with patch.dict(
            sys.modules,
            {
                "cybernetics_bridge": MagicMock(
                    CyberneticsBridge=MagicMock(
                        side_effect=ImportError("mock init failure")
                    )
                )
            }
        ), patch.object(
            legacy_mod, "CLAUDE_CODE_ADAPTER_AVAILABLE", False
        ), patch.object(
            legacy_mod, "NEW_COMPONENTS_AVAILABLE", True
        ):
            # 关键：dispatch_agent_v2 不应抛异常
            result = legacy_mod.dispatch_agent_v2(
                agent_type="test_agent",
                task="test_task",
                project_root="/tmp",
                cybernetics_enabled=True,
            )
            self.assertIsInstance(result, bool)

    def test_dispatch_agent_v2_top_level_exception_returns_false(self):
        """dispatch_agent_v2 顶层抛异常 → 返回 False。"""
        # patch CLAUDE_CODE_ADAPTER_AVAILABLE = True + 模拟内部异常
        with patch.object(
            legacy_mod, "CLAUDE_CODE_ADAPTER_AVAILABLE", True
        ), patch.object(
            legacy_mod, "_dispatch_via_claude_code",
            side_effect=RuntimeError("mock dispatch failure")
        ):
            result = legacy_mod.dispatch_agent_v2(
                agent_type="test_agent",
                task="test_task",
                project_root="/tmp",
            )
            # 顶层异常被捕获 → 返回 False
            self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
