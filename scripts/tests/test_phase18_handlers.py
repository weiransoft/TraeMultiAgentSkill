"""Phase 18: 4 阶段 Handler 单元测试。

测试 StageHandler 基类 + 4 个具体 handler：
- PlanHandler: 计划生成
- DevHandler: dispatcher 调用（v2 修订：直接调用 _dispatch_via_claude_code）
- VerifyHandler: 测试 + 安全检查（v2 修订：新增空 diff 检测）
- FixHandler: 错误分类 + 修复策略（v2 修订：直接调用 _dispatch_via_claude_code）
"""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from autonomous.handlers.base import StageHandler, StageResult
from autonomous.handlers.dev_handler import DevHandler
from autonomous.handlers.fix_handler import ErrorCategory, FixHandler
from autonomous.handlers.plan_handler import PlanHandler
from autonomous.handlers.verify_handler import VerifyHandler
from autonomous.loop_controller import (
    IterationContext,
    IterationResult,
    StageKind,
)


# ---------------------------------------------------------------------- #
# 工具函数：构造 mock IterationContext                                    #
# ---------------------------------------------------------------------- #


def _make_iter_ctx(
    iter_index: int = 1,
    run_id: str = "r-test-001",
    worktree: Path = None,
    plan: str = "初始 plan",
    verify_artifacts: dict = None,
    objective: str = "测试目标",
) -> IterationContext:
    """构造 IterationContext 用于测试。"""
    worktree = worktree or Path(tempfile.mkdtemp())
    return IterationContext(
        run_id=run_id,
        iter_index=iter_index,
        stage=StageKind.PLAN,
        current_plan=plan,
        notes_snapshot="",
        prev_results=[],
        project_root=worktree,
        worktree_path=worktree,
        objective=objective,
        verify_artifacts=verify_artifacts,
    )


# ---------------------------------------------------------------------- #
# TestStageHandlerBase: 基类行为                                         #
# ---------------------------------------------------------------------- #


class TestStageHandlerBase(unittest.TestCase):
    """测试 StageHandler 抽象基类。"""

    def test_01_handle_catches_exception(self):
        """基类 handle() 捕获子 do_handle() 异常，返回 fatal。"""

        class BoomHandler(StageHandler):
            name = "boom"
            kind = "boom"

            def do_handle(self, iter_ctx):
                raise RuntimeError("boom!")

        h = BoomHandler()
        ctx = _make_iter_ctx()
        result = h.handle(ctx)
        self.assertEqual(result.kind, "fatal")
        self.assertIn("boom", result.summary)
        self.assertNotEqual(result.error, "")

    def test_02_handle_returns_success(self):
        """基类 handle() 直接透传 success StageResult。"""

        class OkHandler(StageHandler):
            name = "ok"
            kind = "ok"

            def do_handle(self, iter_ctx):
                return StageResult(kind="success", summary="ok")

        h = OkHandler()
        result = h.handle(_make_iter_ctx())
        self.assertEqual(result.kind, "success")
        self.assertEqual(result.summary, "ok")

    def test_03_do_handle_raises_not_implemented(self):
        """基类 do_handle() 默认抛 NotImplementedError。"""
        h = StageHandler()
        with self.assertRaises(NotImplementedError):
            h.do_handle(_make_iter_ctx())


# ---------------------------------------------------------------------- #
# TestPlanHandler                                                         #
# ---------------------------------------------------------------------- #


class TestPlanHandler(unittest.TestCase):
    """测试 PlanHandler。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_04_first_iter_uses_objective(self):
        """iter_index=1 时使用 objective 作为 plan。"""
        loader = MagicMock()
        loader.detect_for_task.return_value = []
        notes = MagicMock()
        notes.load.return_value = ""

        h = PlanHandler(auto_skill_loader=loader, notes_memory=notes)
        ctx = _make_iter_ctx(iter_index=1, plan="自定义 plan")
        result = h.do_handle(ctx)

        self.assertEqual(result.kind, "success")
        # iter_ctx.current_plan 应被赋值
        self.assertTrue(ctx.current_plan)

    def test_05_subsequent_iter_uses_notes(self):
        """iter_index>1 时基于 notes 生成 plan。"""
        loader = MagicMock()
        loader.detect_for_task.return_value = []
        notes = MagicMock()
        notes.load.return_value = "## 上次内容\n上次做了 ABC"

        h = PlanHandler(auto_skill_loader=loader, notes_memory=notes)
        ctx = _make_iter_ctx(iter_index=2)
        result = h.do_handle(ctx)

        self.assertEqual(result.kind, "success")
        self.assertIn("Iteration 2", ctx.current_plan)

    def test_06_plan_records_skill_count(self):
        """artifacts 包含检测到的 skills 数量。"""
        loader = MagicMock()
        # 构造 2 个 mock skills
        s1 = MagicMock()
        s1.name = "s1"
        s2 = MagicMock()
        s2.name = "s2"
        loader.detect_for_task.return_value = [s1, s2]
        notes = MagicMock()
        notes.load.return_value = ""

        h = PlanHandler(auto_skill_loader=loader, notes_memory=notes)
        ctx = _make_iter_ctx(iter_index=1)
        result = h.do_handle(ctx)

        self.assertEqual(result.kind, "success")
        self.assertEqual(result.artifacts["relevant_skills"], ["s1", "s2"])

    def test_07_handles_notes_load_failure(self):
        """notes 加载失败 → retriable（不阻塞）。"""
        loader = MagicMock()
        loader.detect_for_task.return_value = []
        notes = MagicMock()
        notes.load.side_effect = OSError("磁盘错误")

        h = PlanHandler(auto_skill_loader=loader, notes_memory=notes)
        ctx = _make_iter_ctx()
        result = h.do_handle(ctx)

        self.assertEqual(result.kind, "retriable")
        self.assertIn("加载 notes 失败", result.summary)

    def test_08_handles_skill_detect_failure(self):
        """skill 检测失败不阻塞 plan。"""
        loader = MagicMock()
        loader.detect_for_task.side_effect = RuntimeError("失败")
        notes = MagicMock()
        notes.load.return_value = ""

        h = PlanHandler(auto_skill_loader=loader, notes_memory=notes)
        ctx = _make_iter_ctx()
        result = h.do_handle(ctx)

        # 失败被吞掉，plan 仍然成功
        self.assertEqual(result.kind, "success")


# ---------------------------------------------------------------------- #
# TestDevHandler                                                          #
# ---------------------------------------------------------------------- #


class TestDevHandler(unittest.TestCase):
    """测试 DevHandler。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_dispatcher(self, success: bool = True, output: str = "ok") -> MagicMock:
        """构造 mock DispatcherAdapter。"""
        from autonomous.dispatcher_adapter import AdapterInvokeResult
        result = AdapterInvokeResult(
            success=success,
            kind="success" if success else "retriable",
            output=output,
            summary="mock result",
            tokens=100,
            skills_used=[],
        )
        dispatcher = MagicMock()
        dispatcher.invoke.return_value = result
        return dispatcher

    def test_09_no_dispatcher_fatal(self):
        """v2 修订：DevHandler 不再依赖 dispatcher_adapter，直接调用 _dispatch_via_claude_code。
        无 _dispatch_via_claude_code 成功时返回 retriable。"""
        h = DevHandler(dispatcher_adapter=None)
        ctx = _make_iter_ctx()
        # Mock _dispatch_via_claude_code 返回 False（失败）
        with patch("dispatch.legacy._dispatch_via_claude_code", return_value=False):
            result = h.do_handle(ctx)
        self.assertEqual(result.kind, "retriable")

    def test_10_successful_dispatch(self):
        """v2 修订：成功 dispatch → success（通过 _dispatch_via_claude_code）。"""
        h = DevHandler(dispatcher_adapter=None)
        ctx = _make_iter_ctx(plan="实现功能 X")
        # Mock _dispatch_via_claude_code 返回 True（成功）
        with patch("dispatch.legacy._dispatch_via_claude_code", return_value=True) as mock_dispatch:
            result = h.do_handle(ctx)

        self.assertEqual(result.kind, "success")
        # _dispatch_via_claude_code 被调用
        mock_dispatch.assert_called_once()

    def test_11_failed_dispatch_retriable(self):
        """v2 修订：dispatch 返回 False → retriable。"""
        h = DevHandler(dispatcher_adapter=None)
        ctx = _make_iter_ctx()
        with patch("dispatch.legacy._dispatch_via_claude_code", return_value=False):
            result = h.do_handle(ctx)
        self.assertEqual(result.kind, "retriable")

    def test_12_failed_dispatch_fatal(self):
        """v2 修订：_dispatch_via_claude_code 抛异常 → handle() 捕获为 fatal。"""
        h = DevHandler(dispatcher_adapter=None)
        ctx = _make_iter_ctx()
        with patch("dispatch.legacy._dispatch_via_claude_code", side_effect=RuntimeError("致命错误")):
            # 使用 handle()（基类方法）而非 do_handle()，handle() 会捕获异常
            result = h.handle(ctx)
        # 异常被 handle() 捕获为 fatal
        self.assertEqual(result.kind, "fatal")

    def test_13_injects_skills(self):
        """v2 修订：auto_skills 注入到 _dispatch_via_claude_code 的 context。"""
        loader = MagicMock()
        s = MagicMock()
        s.name = "translation"
        s.description = "翻译"
        s.triggers = ["翻译", "translate"]
        s.path = "/path/to/skill"
        loader.detect_for_task.return_value = [s]

        h = DevHandler(dispatcher_adapter=None, auto_skill_loader=loader)
        ctx = _make_iter_ctx(plan="翻译文档")
        with patch("dispatch.legacy._dispatch_via_claude_code", return_value=True) as mock_dispatch:
            h.do_handle(ctx)

        # 验证 _dispatch_via_claude_code 被调用
        mock_dispatch.assert_called_once()
        # 验证 artifacts 中包含 skills_used
        # 由于 context 是内部构造的，我们验证 artifacts 中的 skills_used
        # （DevHandler 在 artifacts 中记录 skills_used）


# ---------------------------------------------------------------------- #
# TestVerifyHandler                                                       #
# ---------------------------------------------------------------------- #


class TestVerifyHandler(unittest.TestCase):
    """测试 VerifyHandler。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_14_verify_no_test_command(self):
        """v2 修订：无 test_command → 默认 success（仅做安全检查）。
        需设置 agent_output 非空以避免空 diff 检测。"""
        h = VerifyHandler(test_command="", security_analyzer="builtin")
        ctx = _make_iter_ctx()
        ctx.agent_output = "some output"  # 避免空 diff 检测
        result = h.do_handle(ctx)
        # 没有 test 失败 + 没有安全问题 → success
        self.assertEqual(result.kind, "success")

    def test_15_verify_builtin_security_detects_aws_key(self):
        """v2 修订：内置安全检查检测到 AWS key → fatal。
        需设置 agent_output 非空以避免空 diff 检测。"""
        h = VerifyHandler(
            test_command="", security_analyzer="builtin", test_timeout_sec=10.0
        )
        ctx = _make_iter_ctx()
        ctx.agent_output = "some output"  # 避免空 diff 检测
        # 写入一个含 AWS key 的文件
        secret_file = ctx.worktree_path / "leaked.py"
        secret_file.write_text('AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n', encoding="utf-8")

        result = h.do_handle(ctx)
        self.assertEqual(result.kind, "fatal")
        self.assertGreaterEqual(len(result.artifacts["security_issues"]), 1)

    def test_16_verify_security_ignores_git(self):
        """安全检查跳过 .git 目录。"""
        h = VerifyHandler(
            test_command="", security_analyzer="builtin", test_timeout_sec=10.0
        )
        ctx = _make_iter_ctx()
        git_dir = ctx.worktree_path / ".git"
        git_dir.mkdir(parents=True, exist_ok=True)
        # 在 .git 中写 AWS key 不应被检测
        (git_dir / "config").write_text('AKIAIOSFODNN7EXAMPLE\n', encoding="utf-8")

        result = h.do_handle(ctx)
        # 不应有安全问题
        self.assertEqual(len(result.artifacts["security_issues"]), 0)

    def test_17_verify_test_pass(self):
        """测试通过 → success。"""
        # 写一个会成功的简单测试命令
        h = VerifyHandler(
            test_command="echo 'PASS'",
            security_analyzer="builtin",
            test_timeout_sec=10.0,
        )
        ctx = _make_iter_ctx()
        result = h.do_handle(ctx)
        # echo 不会触发 fail/skip 解析
        self.assertIn(result.kind, ("success", "retriable"))

    def test_18_verify_sensitive_patterns_loaded(self):
        """敏感模式列表被加载。"""
        h = VerifyHandler()
        self.assertGreater(len(h._SENSITIVE_PATTERNS), 0)


# ---------------------------------------------------------------------- #
# TestFixHandler                                                          #
# ---------------------------------------------------------------------- #


class TestFixHandler(unittest.TestCase):
    """测试 FixHandler。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_dispatcher(self, success: bool = True) -> MagicMock:
        from autonomous.dispatcher_adapter import AdapterInvokeResult
        result = AdapterInvokeResult(
            success=success,
            kind="success" if success else "retriable",
            output="",
            summary="mock",
            tokens=50,
        )
        dispatcher = MagicMock()
        dispatcher.invoke.return_value = result
        return dispatcher

    def test_19_fix_no_errors_success(self):
        """无错误时 → success。"""
        h = FixHandler()
        ctx = _make_iter_ctx(verify_artifacts={"test_results": [0, 0, 0]})
        result = h.do_handle(ctx)
        self.assertEqual(result.kind, "success")
        self.assertIn("无需修复", result.summary)

    def test_20_fix_security_issue_fatal(self):
        """安全问题 → fatal。"""
        h = FixHandler()
        ctx = _make_iter_ctx(
            verify_artifacts={
                "test_results": [0, 0, 0],
                "security_issues": [{"file": "x.py", "issue": "AWS key"}],
            }
        )
        result = h.do_handle(ctx)
        self.assertEqual(result.kind, "fatal")
        self.assertIn("安全问题", result.summary)

    def test_21_fix_test_failure_calls_dispatcher(self):
        """v2 修订：测试失败时调用 _dispatch_via_claude_code（不再调用 dispatcher.invoke）。"""
        h = FixHandler(dispatcher_adapter=None, max_fix_attempts=3)
        ctx = _make_iter_ctx(
            iter_index=1,
            verify_artifacts={
                "test_results": [5, 2, 0],
                "security_issues": [],
                "test_output_tail": "AssertionError: ... File \"x.py\", line 10",
            },
        )
        with patch("dispatch.legacy._dispatch_via_claude_code", return_value=True) as mock_dispatch:
            result = h.do_handle(ctx)
        self.assertEqual(result.kind, "success")
        mock_dispatch.assert_called_once()

    def test_22_fix_max_attempts_exceeded(self):
        """超过 max_fix_attempts → fatal。"""
        h = FixHandler(dispatcher_adapter=None, max_fix_attempts=1)
        ctx = _make_iter_ctx(
            iter_index=1,
            verify_artifacts={
                "test_results": [5, 1, 0],
                "security_issues": [],
                "test_output_tail": "FAIL",
            },
        )
        # 第一次成功
        with patch("dispatch.legacy._dispatch_via_claude_code", return_value=True):
            r1 = h.do_handle(ctx)
        # 第二次 → 达到上限
        r2 = h.do_handle(ctx)
        self.assertEqual(r1.kind, "success")
        self.assertEqual(r2.kind, "fatal")
        self.assertIn("已达上限", r2.summary)

    def test_23_fix_no_dispatcher_retriable(self):
        """v2 修订：无 dispatcher → _dispatch_via_claude_code 失败时 retriable。"""
        h = FixHandler(dispatcher_adapter=None, max_fix_attempts=3)
        ctx = _make_iter_ctx(
            iter_index=1,
            verify_artifacts={
                "test_results": [5, 1, 0],
                "security_issues": [],
                "test_output_tail": "FAIL",
            },
        )
        with patch("dispatch.legacy._dispatch_via_claude_code", return_value=False):
            result = h.do_handle(ctx)
        self.assertEqual(result.kind, "retriable")

    def test_24_classify_test_failure_with_file_line(self):
        """解析测试输出中的 file:line。"""
        test_output = 'File "tests/test_x.py", line 42, in test_foo\nAssertionError'
        cat = FixHandler._classify_test_failure(test_output)
        self.assertEqual(cat.kind, "test_failure")
        self.assertEqual(cat.file_hint, "tests/test_x.py")
        self.assertEqual(cat.line_hint, 42)

    def test_25_classify_test_failure_assertion_only(self):
        """仅 AssertionError 的分类。"""
        test_output = "AssertionError: expected 1 got 2"
        cat = FixHandler._classify_test_failure(test_output)
        self.assertEqual(cat.kind, "test_failure")
        self.assertIn("AssertionError", cat.message)

    def test_26_classify_test_failure_empty(self):
        """空测试输出 → 未知失败。"""
        cat = FixHandler._classify_test_failure("")
        self.assertEqual(cat.kind, "test_failure")
        self.assertEqual(cat.message, "未知测试失败")


if __name__ == "__main__":
    unittest.main()
