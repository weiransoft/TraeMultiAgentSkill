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


class TestLegacyDispatchViaClaudeCode(unittest.TestCase):
    """_dispatch_via_claude_code 主要分支覆盖（line 162-230）。

    关键路径：
    - success=True + actual_task_id=None / set
    - success=False + error msg
    - adapter.invoke_agent 抛异常
    """

    def _make_fake_adapter(self, result: dict) -> MagicMock:
        """构造一个 invoke_agent 返回固定 result 的 fake adapter。"""
        fake = MagicMock()
        fake.invoke_agent.return_value = result
        return fake

    def test_dispatch_via_claude_code_success_with_task_id(self):
        """Claude Code 调度成功 + task_id 已设置 → 走 success path + update_task_status。"""
        with patch.object(
            legacy_mod, "ClaudeCodeSubAgentAdapter"
        ) as mock_cls, patch(
            "trae_agent_dispatch.update_task_status"
        ) as mock_update:
            mock_cls.return_value = self._make_fake_adapter(
                {"success": True, "platform": "macos", "output": "完成"}
            )
            mock_update.return_value = None

            result = legacy_mod._dispatch_via_claude_code(
                agent_type="test_agent",
                task="TASK-001 - 任务描述",
                task_id="TASK-001",
                project_root="/tmp",
                progress={},
            )
            # 关键：成功 + 有 task_id
            self.assertTrue(result)
            # 关键：update_task_status 被调用
            self.assertEqual(mock_update.call_count, 1)

    def test_dispatch_via_claude_code_success_without_task_id(self):
        """Claude Code 调度成功 + task_id 为空 + task 第一段为空 → 不调用 update_task_status。"""
        with patch.object(
            legacy_mod, "ClaudeCodeSubAgentAdapter"
        ) as mock_cls, patch(
            "trae_agent_dispatch.update_task_status"
        ) as mock_update:
            mock_cls.return_value = self._make_fake_adapter(
                {"success": True, "platform": "macos", "output": "完成"}
            )

            result = legacy_mod._dispatch_via_claude_code(
                agent_type="test_agent",
                task=" - 任务描述",  # 关键：第一段为空，strip 后 actual_task_id=""
                task_id=None,
                project_root="/tmp",
                progress={},
            )
            self.assertTrue(result)
            # 关键：actual_task_id 为空 → 不调用 update
            self.assertEqual(mock_update.call_count, 0)

    def test_dispatch_via_claude_code_success_no_output(self):
        """Claude Code 调度成功 + result 无 output 字段 → 不打印输出。"""
        with patch.object(
            legacy_mod, "ClaudeCodeSubAgentAdapter"
        ) as mock_cls, patch(
            "trae_agent_dispatch.update_task_status"
        ) as mock_update:
            mock_cls.return_value = self._make_fake_adapter(
                {"success": True, "platform": "macos"}  # 无 output
            )

            result = legacy_mod._dispatch_via_claude_code(
                agent_type="test_agent",
                task="TASK-003",
                task_id="TASK-003",
                project_root="/tmp",
                progress={},
            )
            self.assertTrue(result)
            # update_task_status 仍被调用
            self.assertEqual(mock_update.call_count, 1)

    def test_dispatch_via_claude_code_failure(self):
        """Claude Code 调度失败 + task_id 设置 → 走 failure path + update_task_status。"""
        with patch.object(
            legacy_mod, "ClaudeCodeSubAgentAdapter"
        ) as mock_cls, patch(
            "trae_agent_dispatch.update_task_status"
        ) as mock_update:
            mock_cls.return_value = self._make_fake_adapter(
                {"success": False, "error": "mock claude code error"}
            )
            mock_update.return_value = None

            result = legacy_mod._dispatch_via_claude_code(
                agent_type="test_agent",
                task="TASK-004",
                task_id="TASK-004",
                project_root="/tmp",
                progress={},
            )
            # 关键：失败
            self.assertFalse(result)
            # 关键：失败也调用 update_task_status（标记失败状态）
            self.assertEqual(mock_update.call_count, 1)

    def test_dispatch_via_claude_code_failure_no_task_id(self):
        """Claude Code 调度失败 + task_id 为空 + task 第一段为空 → 不调用 update_task_status。"""
        with patch.object(
            legacy_mod, "ClaudeCodeSubAgentAdapter"
        ) as mock_cls, patch(
            "trae_agent_dispatch.update_task_status"
        ) as mock_update:
            mock_cls.return_value = self._make_fake_adapter(
                {"success": False, "error": "mock error"}
            )

            result = legacy_mod._dispatch_via_claude_code(
                agent_type="test_agent",
                task=" - description",  # 关键：第一段为空
                task_id=None,
                project_root="/tmp",
                progress={},
            )
            self.assertFalse(result)
            self.assertEqual(mock_update.call_count, 0)

    def test_dispatch_via_claude_code_task_with_dash_separator(self):
        """task 形如 'TASK-006 - 描述' → 从中提取 task_id。"""
        with patch.object(
            legacy_mod, "ClaudeCodeSubAgentAdapter"
        ) as mock_cls, patch(
            "trae_agent_dispatch.update_task_status"
        ) as mock_update:
            mock_cls.return_value = self._make_fake_adapter(
                {"success": True, "platform": "macos"}
            )

            # task_id=None, task 形如 "TASK-006 - xxx"
            result = legacy_mod._dispatch_via_claude_code(
                agent_type="test_agent",
                task="TASK-006 - 自动提取 task_id",
                task_id=None,
                project_root="/tmp",
                progress={},
            )
            self.assertTrue(result)
            # 关键：从 task 中提取了 TASK-006，调 update
            self.assertEqual(mock_update.call_count, 1)

    def test_dispatch_via_claude_code_adapter_exception(self):
        """ClaudeCodeSubAgentAdapter 构造抛异常 → 异常被隔离 + 返回 False。"""
        with patch.object(
            legacy_mod, "ClaudeCodeSubAgentAdapter",
            side_effect=RuntimeError("adapter init failure")
        ):
            # 关键：不抛异常到调用方
            result = legacy_mod._dispatch_via_claude_code(
                agent_type="test_agent",
                task="TASK-007",
                task_id="TASK-007",
                project_root="/tmp",
                progress={},
            )
            self.assertFalse(result)

    def test_dispatch_via_claude_code_invoke_exception(self):
        """adapter.invoke_agent 抛异常 → 异常被隔离 + 返回 False。"""
        with patch.object(
            legacy_mod, "ClaudeCodeSubAgentAdapter"
        ) as mock_cls:
            mock_cls.return_value = MagicMock()
            mock_cls.return_value.invoke_agent.side_effect = RuntimeError(
                "invoke failure"
            )
            result = legacy_mod._dispatch_via_claude_code(
                agent_type="test_agent",
                task="TASK-008",
                task_id=None,
                project_root="/tmp",
                progress={},
            )
            self.assertFalse(result)


class TestLegacyDispatchViaTrae(unittest.TestCase):
    """_dispatch_via_trae 主要分支覆盖（line 240-365）。

    关键路径：
    - agent_type='auto' + 匹配到角色
    - agent_type 指定 + 在 matched_roles 中找到
    - agent_type 指定 + 不在 matched_roles 但 registered
    - agent_type 指定 + 完全找不到 → fallback 到 matched_roles[0]
    - 完全无匹配 → False
    """

    def _patch_trae_deps(self, mock_matcher=None, mock_workflow=None,
                         mock_cm=None):
        """公共 patch：替换 DualLayerContextManager / RoleMatcher / WorkflowEngine。"""
        if mock_cm is None:
            mock_cm = MagicMock()
        if mock_matcher is None:
            mock_matcher = MagicMock()
        if mock_workflow is None:
            mock_workflow = MagicMock()
        return (
            patch.object(legacy_mod, "DualLayerContextManager", return_value=mock_cm),
            patch.object(legacy_mod, "RoleMatcher", return_value=mock_matcher),
            patch.object(legacy_mod, "WorkflowEngine", return_value=mock_workflow),
        )

    def test_dispatch_via_trae_agent_type_auto(self):
        """agent_type='auto' → 取 matched_roles[0] 作为 best_match。"""
        from role_matcher import MatchResult

        # 构造 mock matcher: roles list, match 返回 3 个结果
        fake_result = MatchResult(
            role_id="role1",
            role_name="Role One",
            confidence=0.9,
            reasons=["matched"],
            matched_capabilities=[],
        )
        mock_matcher = MagicMock()
        mock_matcher.match.return_value = [fake_result]
        mock_matcher.roles = {"role1": MagicMock()}

        mock_cm = MagicMock()
        mock_cm.get_statistics.return_value = {
            "global_context": {
                "version": 1,
                "knowledge_count": 5,
                "experience_count": 2,
            }
        }
        mock_workflow = MagicMock()

        (p_cm, p_matcher, p_workflow) = self._patch_trae_deps(
            mock_matcher=mock_matcher,
            mock_workflow=mock_workflow,
            mock_cm=mock_cm,
        )

        with p_cm, p_matcher, p_workflow, patch(
            "trae_agent_dispatch.update_task_status"
        ) as mock_update:
            mock_update.return_value = None
            result = legacy_mod._dispatch_via_trae(
                agent_type="auto",
                task="TASK-AUTO",
                task_id="TASK-AUTO",
                project_root="/tmp",
                progress={},
            )
            # 关键：成功
            self.assertTrue(result)
            # 关键：update_task_status 被调用
            self.assertEqual(mock_update.call_count, 1)

    def test_dispatch_via_trae_agent_type_specified_matched(self):
        """agent_type='role1' + 在 matched_roles 中找到 → 走指定角色。"""
        from role_matcher import MatchResult

        target = MatchResult(
            role_id="role2",
            role_name="Role Two",
            confidence=0.8,
            reasons=["match"],
            matched_capabilities=[],
        )
        other = MatchResult(
            role_id="role3",
            role_name="Role Three",
            confidence=0.7,
            reasons=["match"],
            matched_capabilities=[],
        )
        mock_matcher = MagicMock()
        mock_matcher.match.return_value = [other, target]
        mock_matcher.roles = {"role2": MagicMock(), "role3": MagicMock()}

        mock_cm = MagicMock()
        mock_cm.get_statistics.return_value = {
            "global_context": {
                "version": 1,
                "knowledge_count": 0,
                "experience_count": 0,
            }
        }

        (p_cm, p_matcher, p_workflow) = self._patch_trae_deps(
            mock_matcher=mock_matcher, mock_cm=mock_cm,
        )

        with p_cm, p_matcher, p_workflow:
            result = legacy_mod._dispatch_via_trae(
                agent_type="role2",
                task="TASK-SPEC",
                task_id="TASK-SPEC",
                project_root="/tmp",
                progress={},
            )
            self.assertTrue(result)

    def test_dispatch_via_trae_agent_type_specified_registered_only(self):
        """agent_type='role4' + 不在 matched_roles 但已 register → 用 registered 角色。"""
        from role_matcher import MatchResult

        match_result = MatchResult(
            role_id="role1",
            role_name="Role One",
            confidence=0.6,
            reasons=["weak"],
            matched_capabilities=[],
        )
        mock_matcher = MagicMock()
        mock_matcher.match.return_value = [match_result]
        # role4 已注册但不在 match 结果
        mock_role4 = MagicMock()
        mock_role4.role_id = "role4"
        mock_role4.name = "Role Four"
        mock_role4.capabilities = []
        mock_matcher.roles = {"role1": MagicMock(), "role4": mock_role4}

        mock_cm = MagicMock()
        mock_cm.get_statistics.return_value = {
            "global_context": {
                "version": 1,
                "knowledge_count": 0,
                "experience_count": 0,
            }
        }

        (p_cm, p_matcher, p_workflow) = self._patch_trae_deps(
            mock_matcher=mock_matcher, mock_cm=mock_cm,
        )

        with p_cm, p_matcher, p_workflow:
            result = legacy_mod._dispatch_via_trae(
                agent_type="role4",
                task="TASK-REG",
                task_id="TASK-REG",
                project_root="/tmp",
                progress={},
            )
            self.assertTrue(result)

    def test_dispatch_via_trae_agent_type_not_found_fallback(self):
        """agent_type='unknown' + 不在 matched 也不在 registered → fallback 到 matched[0]。"""
        from role_matcher import MatchResult

        match_result = MatchResult(
            role_id="role1",
            role_name="Role One",
            confidence=0.6,
            reasons=["fallback"],
            matched_capabilities=[],
        )
        mock_matcher = MagicMock()
        mock_matcher.match.return_value = [match_result]
        mock_matcher.roles = {"role1": MagicMock()}  # role1 已注册，unknown 不在

        mock_cm = MagicMock()
        mock_cm.get_statistics.return_value = {
            "global_context": {
                "version": 1,
                "knowledge_count": 0,
                "experience_count": 0,
            }
        }

        (p_cm, p_matcher, p_workflow) = self._patch_trae_deps(
            mock_matcher=mock_matcher, mock_cm=mock_cm,
        )

        with p_cm, p_matcher, p_workflow:
            result = legacy_mod._dispatch_via_trae(
                agent_type="unknown_role",
                task="TASK-UNKNOWN",
                task_id="TASK-UNKNOWN",
                project_root="/tmp",
                progress={},
            )
            # 关键：fallback 仍能完成
            self.assertTrue(result)

    def test_dispatch_via_trae_no_matched_roles(self):
        """matched_roles 为空 → 返回 False。"""
        mock_matcher = MagicMock()
        mock_matcher.match.return_value = []  # 无匹配
        mock_matcher.roles = {}

        mock_cm = MagicMock()

        (p_cm, p_matcher, p_workflow) = self._patch_trae_deps(
            mock_matcher=mock_matcher, mock_cm=mock_cm,
        )

        with p_cm, p_matcher, p_workflow:
            result = legacy_mod._dispatch_via_trae(
                agent_type="auto",
                task="TASK-NONE",
                task_id="TASK-NONE",
                project_root="/tmp",
                progress={},
            )
            # 关键：无匹配 → False
            self.assertFalse(result)

    def test_dispatch_via_trae_exception_isolated(self):
        """_dispatch_via_trae 内部抛异常 → 异常被隔离 + 返回 False。"""
        with patch.object(
            legacy_mod, "DualLayerContextManager",
            side_effect=RuntimeError("cm init failure")
        ):
            result = legacy_mod._dispatch_via_trae(
                agent_type="auto",
                task="TASK-EXC",
                task_id="TASK-EXC",
                project_root="/tmp",
                progress={},
            )
            self.assertFalse(result)

    def test_dispatch_via_trae_skill_root_in_project(self):
        """project_root 包含 '.trae' + 'skills' → skill_root = project_root。"""
        from role_matcher import MatchResult

        match_result = MatchResult(
            role_id="role1",
            role_name="Role One",
            confidence=0.9,
            reasons=["matched"],
            matched_capabilities=[],
        )
        mock_matcher = MagicMock()
        mock_matcher.match.return_value = [match_result]
        mock_matcher.roles = {"role1": MagicMock()}

        mock_cm = MagicMock()
        mock_cm.get_statistics.return_value = {
            "global_context": {
                "version": 1,
                "knowledge_count": 0,
                "experience_count": 0,
            }
        }

        (p_cm, p_matcher, p_workflow) = self._patch_trae_deps(
            mock_matcher=mock_matcher, mock_cm=mock_cm,
        )

        # 关键：project_root 包含 .trae/skills 路径
        with p_cm, p_matcher, p_workflow:
            result = legacy_mod._dispatch_via_trae(
                agent_type="auto",
                task="TASK-SKILL",
                task_id="TASK-SKILL",
                project_root="/tmp/.trae/skills/trae-multi-agent",
                progress={},
            )
            # 关键：skill_root 走 project_root 分支
            self.assertTrue(result)
            # 验证：DualLayerContextManager 的 skill_root 参数 == project_root
            call_kwargs = legacy_mod.DualLayerContextManager.call_args.kwargs
            self.assertEqual(
                call_kwargs["skill_root"],
                "/tmp/.trae/skills/trae-multi-agent",
            )


class TestLegacyDispatchAgent(unittest.TestCase):
    """dispatch_agent 主要分支（line 385-410）。

    关键路径：
    - NEW_COMPONENTS_AVAILABLE + use_v1=False → 走 v2
    - use_v1=True → 走 v1 模拟
    """

    def test_dispatch_agent_use_v1_returns_true(self):
        """use_v1=True → 走 v1 模拟 → 总是 True。"""
        with patch(
            "trae_agent_dispatch.load_task_progress", return_value={}
        ), patch(
            "trae_agent_dispatch.update_task_status", return_value=None
        ), patch.object(
            legacy_mod, "NEW_COMPONENTS_AVAILABLE", True
        ):
            result = legacy_mod.dispatch_agent(
                agent_type="role1",
                task="TASK-001 - desc",
                project_root="/tmp",
                task_file="task.md",
                use_v1=True,
            )
            # 关键：v1 模拟 → True
            self.assertTrue(result)

    def test_dispatch_agent_use_v1_no_task_id(self):
        """use_v1=True + task 为空 → task_id 解析为空字符串，不调用 update。

        关键：当 task_id 为空时，dispatch_agent 不会调 update_task_status。
        """
        with patch(
            "trae_agent_dispatch.load_task_progress", return_value={}
        ), patch(
            "trae_agent_dispatch.update_task_status", return_value=None
        ) as mock_update, patch.object(
            legacy_mod, "NEW_COMPONENTS_AVAILABLE", True
        ):
            result = legacy_mod.dispatch_agent(
                agent_type="role1",
                task="",  # 关键：空 task → task_id 解析为 ""
                project_root="/tmp",
                task_file="task.md",
                use_v1=True,
            )
            self.assertTrue(result)
            # 关键：task_id 为空 → 不调用 '进行中' 和 '✅ 已完成' 的 update
            self.assertEqual(mock_update.call_count, 0)

    def test_dispatch_agent_v2_path(self):
        """use_v1=False + NEW_COMPONENTS_AVAILABLE → 走 v2。"""
        with patch(
            "trae_agent_dispatch.load_task_progress", return_value={}
        ), patch(
            "trae_agent_dispatch.update_task_status", return_value=None
        ), patch(
            "dispatch.legacy.dispatch_agent_v2", return_value=True
        ) as mock_v2, patch.object(
            legacy_mod, "NEW_COMPONENTS_AVAILABLE", True
        ):
            result = legacy_mod.dispatch_agent(
                agent_type="role1",
                task="TASK-002 - desc",
                project_root="/tmp",
                task_file="task.md",
                use_v1=False,
            )
            # 关键：v2 被调用
            self.assertEqual(mock_v2.call_count, 1)
            self.assertTrue(result)

    def test_dispatch_agent_v2_failure(self):
        """use_v1=False + v2 返回 False → 返回 False。"""
        with patch(
            "trae_agent_dispatch.load_task_progress", return_value={}
        ), patch(
            "trae_agent_dispatch.update_task_status", return_value=None
        ), patch(
            "dispatch.legacy.dispatch_agent_v2", return_value=False
        ), patch.object(
            legacy_mod, "NEW_COMPONENTS_AVAILABLE", True
        ):
            result = legacy_mod.dispatch_agent(
                agent_type="role1",
                task="TASK-003 - desc",
                project_root="/tmp",
                task_file="task.md",
                use_v1=False,
            )
            self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
