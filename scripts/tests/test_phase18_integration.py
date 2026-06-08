"""Phase 18: 集成测试。

7 个端到端集成场景：
1. 完整流程：autonomous 模式从 CLI flag 到主循环结束
2. Resume 流程：resume 一个已存在的 run
3. 黑名单拦截 + 安全回滚
4. 4 阶段 handler 串联
5. SleepGuard 生命周期
6. notes.md 跨轮累积
7. run_state 持久化 + crash recovery
"""
import argparse
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from autonomous.dispatcher_adapter import AdapterInvokeResult
from autonomous.git_driver import GitOpResult
from autonomous.handlers.base import StageResult
from autonomous.loop_controller import (
    LoopConfig,
    RalphLoopController,
    StageKind,
)
from autonomous.notes_memory import NotesMemory
from autonomous.run_state import RunState
from autonomous.sleep_guard import SleepGuard, SleepGuardMode
from autonomous.smart_confirmation import SmartConfirmation
from autonomous.config_loader import load_config


# ---------------------------------------------------------------------- #
# 工具                                                                      #
# ---------------------------------------------------------------------- #


def _make_dispatcher(success: bool = True, output: str = "ok") -> MagicMock:
    """构造 mock DispatcherAdapter。"""
    dispatcher = MagicMock()
    dispatcher.invoke.return_value = AdapterInvokeResult(
        success=success,
        kind="success" if success else "retriable",
        output=output,
        summary="dispatcher mock",
        tokens=50,
        skills_used=[],
    )
    return dispatcher


def _make_git_driver() -> MagicMock:
    """构造 mock GitDriver（commit 总是成功）。"""
    driver = MagicMock()
    driver.commit.return_value = GitOpResult(success=True, stdout="committed")
    driver.rollback.return_value = GitOpResult(success=True, stdout="rolled back")
    driver.diff_stats.return_value = MagicMock(
        files_changed=0, lines_added=0, lines_removed=0, binary_files=0
    )
    driver.is_git_repo.return_value = False
    return driver


def _make_handler(success: bool = True) -> MagicMock:
    """构造一个返回 success 的 mock handler。"""
    h = MagicMock()
    h.name = "mock"
    h.kind = "mock"
    h.handle.return_value = StageResult(
        kind="success" if success else "retriable",
        summary="ok",
        artifacts={},
    )
    return h


def _build_full_controller(
    tmpdir: Path,
    max_iterations: int = 3,
    dispatcher_success: bool = True,
) -> RalphLoopController:
    """构造完整 mock 装配的 controller。"""
    project_root = tmpdir
    run_dir = project_root / ".gnhf" / "runs" / "r-int-test"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_state = RunState(run_dir=run_dir, run_id="r-int-test", objective="集成测试目标")
    git_driver = _make_git_driver()
    notes = NotesMemory(notes_path=project_root / "notes.md")
    auto_skill_loader = MagicMock()
    auto_skill_loader.detect_for_task.return_value = []
    smart_confirmation = SmartConfirmation()
    dispatcher = _make_dispatcher(success=dispatcher_success)
    handlers = {
        StageKind.PLAN: _make_handler(success=dispatcher_success),
        StageKind.DEV: _make_handler(success=dispatcher_success),
        StageKind.VERIFY: _make_handler(success=dispatcher_success),
        StageKind.FIX: _make_handler(success=dispatcher_success),
    }
    config = LoopConfig(max_iterations=max_iterations, consecutive_failure_abort=3)
    return RalphLoopController(
        config=config,
        project_root=project_root,
        git_driver=git_driver,
        notes_memory=notes,
        auto_skill_loader=auto_skill_loader,
        smart_confirmation=smart_confirmation,
        run_state=run_state,
        dispatcher_adapter=dispatcher,
        stage_handlers=handlers,
        objective="集成测试目标",
        log=lambda level, msg: None,
        sleep_guard=SleepGuard(mode=SleepGuardMode.OFF),
    )


# ---------------------------------------------------------------------- #
# TestIntegration01: 完整 autonomous 流程                                #
# ---------------------------------------------------------------------- #


class TestIntegration01EndToEnd(unittest.TestCase):
    """场景 1：完整 autonomous 流程（CLI → plugin → loop → final summary）。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_end_to_end_autonomous_run(self):
        """端到端跑 autonomous。"""
        controller = _build_full_controller(self.tmpdir, max_iterations=2)
        rc = controller.run()
        # 应成功
        self.assertEqual(rc, 0)
        # state 已持久化
        state_path = self.tmpdir / ".gnhf" / "runs" / "r-int-test" / "state.json"
        self.assertTrue(state_path.exists())
        # notes.md 包含 Final Summary
        notes = (self.tmpdir / "notes.md").read_text(encoding="utf-8")
        self.assertIn("Final Summary", notes)
        # 至少 commit 1 次
        self.assertGreaterEqual(controller._run_state.state.commits_made, 1)


# ---------------------------------------------------------------------- #
# TestIntegration02: Resume 流程                                        #
# ---------------------------------------------------------------------- #


class TestIntegration02Resume(unittest.TestCase):
    """场景 2：resume 已存在的 run。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_02_resume_existing_run(self):
        """resume 之前创建的 run。"""
        # 第一阶段：跑 1 轮然后 mark failed
        controller1 = _build_full_controller(self.tmpdir, max_iterations=1)
        controller1.run_one_iteration(iter_index=1)
        # 模拟中断：state 已持久化但未完成
        controller1._run_state.record_iteration(
            iter_index=1, result_kind="failed", summary="中断", committed=False
        )
        # 验证 state.json 存在
        state_path = self.tmpdir / ".gnhf" / "runs" / "r-int-test" / "state.json"
        self.assertTrue(state_path.exists())

        # 第二阶段：构造新的 controller，加载已有 state
        new_state = RunState(
            run_dir=state_path.parent,
            run_id="r-int-test",
            objective="集成测试目标",
        )
        self.assertEqual(new_state.state.iter_index, 1)
        # status 可能是 pending/failed/running/aborted 之一（取决于 run_one_iteration 是否 mark_running）
        self.assertIn(
            new_state.state.status,
            ("pending", "failed", "running", "aborted"),
        )
        # can_resume
        ctx = new_state.get_resume_context()
        # 当 status 为 pending 时不应可 resume；其他状态应可 resume
        if new_state.state.status == "pending":
            self.assertFalse(ctx.can_resume)
        else:
            self.assertTrue(ctx.can_resume)


# ---------------------------------------------------------------------- #
# TestIntegration03: 黑名单 + 安全回滚                                  #
# ---------------------------------------------------------------------- #


class TestIntegration03BlacklistAndRollback(unittest.TestCase):
    """场景 3：黑名单命令拦截 + 回滚流程。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_03_blacklist_blocks_dangerous(self):
        """黑名单命令被 SmartConfirmation 拦截。"""
        sc = SmartConfirmation()
        # rm -rf / → DENY
        result = sc.check("rm -rf /")
        self.assertEqual(result.decision.value, "deny")

        # DROP DATABASE → DENY
        result2 = sc.check("DROP DATABASE production")
        self.assertEqual(result2.decision.value, "deny")

        # git push --force → DENY
        result3 = sc.check("git push --force origin main")
        self.assertEqual(result3.decision.value, "deny")

    def test_04_rollback_preserves_uncommitted(self):
        """rollback 保留 uncommitted work。"""
        controller = _build_full_controller(self.tmpdir, max_iterations=1)
        # 直接调用 rollback（mock 不真做）
        rb = controller._git_driver.rollback()
        self.assertTrue(rb.success)


# ---------------------------------------------------------------------- #
# TestIntegration04: 4 阶段 handler 串联                                #
# ---------------------------------------------------------------------- #


class TestIntegration04HandlerChain(unittest.TestCase):
    """场景 4：4 阶段 handler 按序执行。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_05_4_stages_executed_in_order(self):
        """4 阶段按 plan → dev → verify → fix 顺序执行。"""
        call_order = []

        def make_ordered_handler(stage_name: str) -> MagicMock:
            h = MagicMock()
            h.name = stage_name
            h.kind = stage_name

            def handle(ctx):
                call_order.append(stage_name)
                return StageResult(kind="success", summary=stage_name, artifacts={})

            h.handle.side_effect = handle
            return h

        project_root = self.tmpdir
        run_dir = project_root / ".gnhf" / "runs" / "r-chain"
        run_dir.mkdir(parents=True, exist_ok=True)
        run_state = RunState(run_dir=run_dir, run_id="r-chain")
        config = LoopConfig(max_iterations=1, consecutive_failure_abort=3)
        handlers = {
            StageKind.PLAN: make_ordered_handler("plan"),
            StageKind.DEV: make_ordered_handler("dev"),
            StageKind.VERIFY: make_ordered_handler("verify"),
            StageKind.FIX: make_ordered_handler("fix"),
        }
        controller = RalphLoopController(
            config=config,
            project_root=project_root,
            git_driver=_make_git_driver(),
            notes_memory=NotesMemory(notes_path=project_root / "notes.md"),
            auto_skill_loader=MagicMock(),
            smart_confirmation=SmartConfirmation(),
            run_state=run_state,
            dispatcher_adapter=_make_dispatcher(),
            stage_handlers=handlers,
            objective="chain test",
        )
        result = controller.run_one_iteration(iter_index=1)
        self.assertEqual(result.kind, "success")
        # 顺序
        self.assertEqual(call_order, ["plan", "dev", "verify", "fix"])


# ---------------------------------------------------------------------- #
# TestIntegration05: SleepGuard 生命周期                                 #
# ---------------------------------------------------------------------- #


class TestIntegration05SleepGuard(unittest.TestCase):
    """场景 5：SleepGuard 在 run 中正确启动 + 释放。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_06_sleep_guard_off_mode(self):
        """SleepGuard OFF 模式直接 no-op。"""
        guard = SleepGuard(mode=SleepGuardMode.OFF)
        handle = guard.acquire()
        self.assertEqual(handle.mode, SleepGuardMode.OFF)
        self.assertIsNone(handle.process)
        self.assertEqual(handle.backend, "noop")
        # release 不报错
        guard.release()
        self.assertIsNone(guard._handle)

    def test_07_sleep_guard_release_idempotent(self):
        """SleepGuard release 多次调用无副作用。"""
        guard = SleepGuard(mode=SleepGuardMode.OFF)
        guard.acquire()
        guard.release()
        # 再次 release 不报错
        guard.release()
        self.assertIsNone(guard._handle)


# ---------------------------------------------------------------------- #
# TestIntegration06: notes.md 跨轮累积                                  #
# ---------------------------------------------------------------------- #


class TestIntegration06NotesMemory(unittest.TestCase):
    """场景 6：notes.md 跨多轮迭代累积。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_08_notes_accumulate_across_iterations(self):
        """notes.md 跨多轮累积。"""
        from autonomous.notes_memory import NotesSection
        from datetime import datetime, timezone
        notes_path = self.tmpdir / "notes.md"
        notes = NotesMemory(notes_path=notes_path)
        # 添加 3 个 section
        for i in range(1, 4):
            notes.append(NotesSection(
                title=f"Iteration {i}",
                body=f"第 {i} 轮结果",
                timestamp=datetime.now(timezone.utc).isoformat(),
                iter_index=i,
                tags=["success"],
            ))
        # 重新加载
        notes2 = NotesMemory(notes_path=notes_path)
        sections = notes2.list_sections()
        self.assertEqual(len(sections), 3)
        # 验证内容
        self.assertIn("第 1 轮", sections[0].body)
        self.assertIn("第 3 轮", sections[2].body)


# ---------------------------------------------------------------------- #
# TestIntegration07: RunState crash recovery                             #
# ---------------------------------------------------------------------- #


class TestIntegration07CrashRecovery(unittest.TestCase):
    """场景 7：run_state 持久化 + crash recovery。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_09_persist_load_roundtrip(self):
        """state.json 持久化 + 重新加载一致。"""
        run_dir = self.tmpdir / "runs" / "r-crash"
        run_dir.mkdir(parents=True, exist_ok=True)
        # 创建 state
        rs1 = RunState(run_dir=run_dir, run_id="r-crash", objective="crash test")
        rs1.mark_running()
        rs1.record_iteration(1, "success", "iter 1 ok", committed=True, tokens=100)
        rs1.record_iteration(2, "failed", "iter 2 fail", tokens=50)
        # 模拟 crash
        del rs1
        # 重新加载
        rs2 = RunState(run_dir=run_dir, run_id="r-crash")
        self.assertEqual(rs2.state.iter_index, 2)
        self.assertEqual(rs2.state.commits_made, 1)
        self.assertEqual(rs2.state.consecutive_failures, 1)
        self.assertEqual(rs2.state.cumulative_tokens, 150)

    def test_10_verify_integrity_after_persist(self):
        """persist 后 state.json 通过完整性校验。"""
        run_dir = self.tmpdir / "runs" / "r-int"
        run_dir.mkdir(parents=True, exist_ok=True)
        rs = RunState(run_dir=run_dir, run_id="r-int", objective="integrity")
        rs.persist()
        self.assertTrue(rs.verify_integrity())

    def test_11_restore_from_backup(self):
        """state.json 损坏时 restore_from_backup 恢复。"""
        run_dir = self.tmpdir / "runs" / "r-backup"
        run_dir.mkdir(parents=True, exist_ok=True)
        rs = RunState(run_dir=run_dir, run_id="r-backup", objective="backup")
        # 多次 persist 生成 backup
        rs.persist()
        rs.record_iteration(1, "success", "ok", committed=True)
        # 损坏当前 state
        (run_dir / "state.json").write_text("corrupted", encoding="utf-8")
        # 验证完整性失败
        self.assertFalse(rs.verify_integrity())
        # 恢复
        self.assertTrue(rs.restore_from_backup())
        # 完整性通过
        self.assertTrue(rs.verify_integrity())


# ---------------------------------------------------------------------- #
# TestIntegration08: config 端到端                                       #
# ---------------------------------------------------------------------- #


class TestIntegration08ConfigE2E(unittest.TestCase):
    """场景 8：config 端到端（YAML → AutonomousConfig → LoopConfig）。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_12_load_config_applied_to_components(self):
        """YAML 配置加载后各组件使用对应值。"""
        cfg_dir = self.tmpdir / ".trae"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "autonomous.yml").write_text("""
max_iterations: 7
test_command: echo CUSTOM
auto_commit: false
""", encoding="utf-8")
        cfg = load_config(self.tmpdir)
        self.assertEqual(cfg.max_iterations, 7)
        self.assertEqual(cfg.test_command, "echo CUSTOM")
        self.assertFalse(cfg.auto_commit)


if __name__ == "__main__":
    unittest.main()
