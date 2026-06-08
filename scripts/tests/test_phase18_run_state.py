"""Phase 18: RunState 单元测试。

测试 RunState 的全部行为：
- 初始化 / persist / record_iteration
- mark_running / mark_complete / mark_aborted / mark_failed
- get_resume_context / verify_integrity / restore_from_backup
- 断点续跑（加载已有 state）
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from autonomous.run_state import RunState, RunStateSchema, ResumeContext


class TestRunStateInit(unittest.TestCase):
    """测试 RunState 初始化。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.run_dir = self.tmpdir / "runs" / "test-run-001"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_init_creates_run_dir(self):
        """初始化时创建 run_dir 目录。"""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        rs = RunState(self.run_dir, "test-run-001", objective="test")
        self.assertTrue(self.run_dir.exists())
        self.assertEqual(rs.state.run_id, "test-run-001")
        self.assertEqual(rs.state.objective, "test")
        self.assertEqual(rs.state.status, "pending")
        self.assertEqual(rs.state.iter_index, 0)

    def test_02_init_loads_existing(self):
        """如果 state.json 存在，初始化时加载。"""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        # 先创建一个 state.json
        rs1 = RunState(self.run_dir, "test", objective="orig")
        rs1.mark_complete()
        # 重新初始化（应加载已有 state）
        rs2 = RunState(self.run_dir, "test", objective="new")
        self.assertEqual(rs2.state.objective, "orig")  # 加载原值
        self.assertEqual(rs2.state.status, "completed")

    def test_03_state_path_property(self):
        """state_path 属性返回 state.json 完整路径。"""
        rs = RunState(self.run_dir, "r1")
        self.assertEqual(rs.state_path, self.run_dir / "state.json")


class TestRunStatePersist(unittest.TestCase):
    """测试 persist() 行为。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.run_dir = self.tmpdir / "runs" / "r1"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.rs = RunState(self.run_dir, "r1", objective="test")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_04_persist_creates_file(self):
        """persist() 创建 state.json。"""
        self.rs.persist()
        self.assertTrue((self.run_dir / "state.json").exists())

    def test_05_persist_creates_backup(self):
        """persist() 创建 state.json.bak 备份。"""
        self.rs.persist()
        # 第一次 persist 不会创建 backup
        self.rs.persist()
        # 第二次 persist 应有 backup
        self.assertTrue((self.run_dir / "state.json.bak").exists())

    def test_06_persist_atomic(self):
        """persist() 是原子的（不留下 .tmp 文件）。"""
        self.rs.persist()
        tmp = self.run_dir / "state.json.tmp"
        self.assertFalse(tmp.exists())

    def test_07_persist_content_valid_json(self):
        """persist() 写入的 state.json 是有效 JSON。"""
        self.rs.persist()
        content = (self.run_dir / "state.json").read_text(encoding="utf-8")
        data = json.loads(content)
        self.assertIn("schema_version", data)
        self.assertIn("state", data)
        self.assertEqual(data["schema_version"], 1)


class TestRunStateRecordIteration(unittest.TestCase):
    """测试 record_iteration() 行为。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.run_dir = self.tmpdir / "runs" / "r1"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.rs = RunState(self.run_dir, "r1", objective="test")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_08_record_success_increments_commits(self):
        """成功 + committed 应增加 commits_made。"""
        self.rs.record_iteration(1, "success", "ok", committed=True)
        self.assertEqual(self.rs.state.commits_made, 1)
        self.assertEqual(self.rs.state.consecutive_failures, 0)

    def test_09_record_failure_increments_failures(self):
        """failed 应增加 consecutive_failures。"""
        self.rs.record_iteration(1, "failed", "err")
        self.assertEqual(self.rs.state.consecutive_failures, 1)
        self.assertEqual(self.rs.state.iter_index, 1)

    def test_10_record_success_resets_failures(self):
        """成功（无论是否 commit）应重置连续失败计数。"""
        self.rs.record_iteration(1, "failed", "err")
        self.rs.record_iteration(2, "failed", "err")
        self.assertEqual(self.rs.state.consecutive_failures, 2)
        self.rs.record_iteration(3, "success", "ok", committed=False)
        self.assertEqual(self.rs.state.consecutive_failures, 0)

    def test_11_record_cumulative_tokens(self):
        """cumulative_tokens 应累计。"""
        self.rs.record_iteration(1, "success", "ok", tokens=100)
        self.rs.record_iteration(2, "success", "ok", tokens=200)
        self.assertEqual(self.rs.state.cumulative_tokens, 300)

    def test_12_record_history_capped(self):
        """history 列表超过 100 条时裁剪。"""
        for i in range(105):
            self.rs.record_iteration(i + 1, "success", "ok")
        # history 应裁剪到 100 条
        self.assertEqual(len(self.rs.state.history), 100)

    def test_13_record_persists(self):
        """record_iteration() 应自动 persist。"""
        self.rs.record_iteration(1, "success", "ok", committed=True)
        # 重新加载
        rs2 = RunState(self.run_dir, "r1", objective="test")
        self.assertEqual(rs2.state.iter_index, 1)
        self.assertEqual(rs2.state.commits_made, 1)


class TestRunStateStatus(unittest.TestCase):
    """测试 mark_* 状态标记。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.run_dir = self.tmpdir / "runs" / "r1"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.rs = RunState(self.run_dir, "r1", objective="test")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_14_mark_running(self):
        """mark_running() 设置 status=running。"""
        self.rs.mark_running()
        self.assertEqual(self.rs.state.status, "running")

    def test_15_mark_complete(self):
        """mark_complete() 设置 status=completed。"""
        self.rs.mark_complete()
        self.assertEqual(self.rs.state.status, "completed")

    def test_16_mark_aborted(self):
        """mark_aborted(reason) 设置 status=aborted + last_error。"""
        self.rs.mark_aborted("测试 abort")
        self.assertEqual(self.rs.state.status, "aborted")
        self.assertEqual(self.rs.state.last_error, "测试 abort")

    def test_17_mark_failed(self):
        """mark_failed(reason) 设置 status=failed + last_error。"""
        self.rs.mark_failed("测试 failed")
        self.assertEqual(self.rs.state.status, "failed")
        self.assertEqual(self.rs.state.last_error, "测试 failed")


class TestRunStateResume(unittest.TestCase):
    """测试 get_resume_context() 行为。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.run_dir = self.tmpdir / "runs" / "r1"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.rs = RunState(self.run_dir, "r1", objective="test")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_18_resume_running_status(self):
        """running 状态可 resume。"""
        self.rs.mark_running()
        ctx = self.rs.get_resume_context()
        self.assertTrue(ctx.can_resume)

    def test_19_resume_failed_status(self):
        """failed 状态可 resume。"""
        self.rs.mark_failed("err")
        ctx = self.rs.get_resume_context()
        self.assertTrue(ctx.can_resume)

    def test_20_resume_completed_status(self):
        """completed 状态不可 resume。"""
        self.rs.mark_complete()
        ctx = self.rs.get_resume_context()
        self.assertFalse(ctx.can_resume)

    def test_21_resume_context_collects_uncommitted(self):
        """get_resume_context() 收集 uncommitted manifests。

        路径结构：<uncommitted_dir>/<timestamp>/manifest.json
        """
        uncommitted_root = self.tmpdir / "uncommitted"
        uncommitted_root.mkdir(parents=True, exist_ok=True)
        # 创建一个时间戳子目录 + manifest
        ts_dir = uncommitted_root / "12345"
        ts_dir.mkdir(parents=True, exist_ok=True)
        (ts_dir / "manifest.json").write_text("{}", encoding="utf-8")
        ctx = self.rs.get_resume_context(uncommitted_dir=uncommitted_root)
        self.assertEqual(len(ctx.uncommitted_manifests), 1)
        self.assertTrue(ctx.uncommitted_manifests[0].name == "manifest.json")


class TestRunStateIntegrity(unittest.TestCase):
    """测试 verify_integrity() 和 restore_from_backup() 行为。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.run_dir = self.tmpdir / "runs" / "r1"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.rs = RunState(self.run_dir, "r1", objective="test")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_22_verify_integrity_valid(self):
        """完整 state.json 应通过校验。"""
        self.rs.persist()
        self.assertTrue(self.rs.verify_integrity())

    def test_23_verify_integrity_no_file(self):
        """state.json 不存在时返回 False。"""
        self.assertFalse(self.rs.verify_integrity())

    def test_24_verify_integrity_corrupted(self):
        """损坏的 state.json 返回 False。"""
        self.rs.persist()
        # 损坏文件
        (self.run_dir / "state.json").write_text("not valid json", encoding="utf-8")
        self.assertFalse(self.rs.verify_integrity())

    def test_25_restore_from_backup(self):
        """restore_from_backup() 从 backup 恢复成功。"""
        # 创建 2 次 persist 以生成 backup
        self.rs.persist()
        self.rs.record_iteration(1, "success", "ok", committed=True)
        # 损坏当前 state
        (self.run_dir / "state.json").write_text("corrupted", encoding="utf-8")
        # 恢复
        result = self.rs.restore_from_backup()
        self.assertTrue(result)
        # 验证：state 已恢复
        self.assertTrue(self.rs.verify_integrity())

    def test_26_restore_no_backup_fails(self):
        """无 backup 时 restore_from_backup() 返回 False。"""
        result = self.rs.restore_from_backup()
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
