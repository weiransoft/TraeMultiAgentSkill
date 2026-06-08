"""Phase 18: GitDriver 单元测试。

测试 GitDriver 的全部公共 API：
- is_git_repo() / status() / diff_stats()
- add_all() / commit() / rollback() / restore_uncommitted()
- log_last_n() / get_current_sha()
- 真实 git 操作（不模拟）
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from autonomous.git_driver import GitDriver, GitOpResult, DiffStats


def _init_git_repo(repo_dir: Path) -> None:
    """在临时目录初始化 git 仓库（真实操作）。"""
    repo_dir.mkdir(parents=True, exist_ok=True)
    # 初始化
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    # 配置 user
    subprocess.run(
        ["git", "config", "user.email", "test@test.local"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    # 初次 commit
    (repo_dir / "README.md").write_text("# Test", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )


class TestGitDriverInit(unittest.TestCase):
    """测试 GitDriver 初始化。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        _init_git_repo(self.tmpdir)
        self.driver = GitDriver(
            repo_root=self.tmpdir,
            run_id="test-run",
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_is_git_repo_true(self):
        """真实 git 仓库应返回 True。"""
        self.assertTrue(self.driver.is_git_repo())

    def test_02_is_git_repo_caches(self):
        """is_git_repo() 缓存结果（多次调用不重复执行）。"""
        # 第一次调用
        result1 = self.driver.is_git_repo()
        # 第二次调用应使用缓存
        result2 = self.driver.is_git_repo()
        self.assertEqual(result1, result2)
        self.assertTrue(self.driver._is_repo_cache is not None)

    def test_03_init_default_run_dir(self):
        """默认 run_dir = .gnhf/runs/<run_id>。

        注：实际 run_dir 会被 resolve()，可能与 repo_root 解析后路径不同（符号链接）。
        这里只验证 run_dir 包含 .gnhf/runs/<run_id>。
        """
        self.assertIn(".gnhf", str(self.driver._run_dir))
        self.assertIn("runs", str(self.driver._run_dir))
        self.assertTrue(str(self.driver._run_dir).endswith("test-run"))

    def test_04_init_custom_run_dir(self):
        """自定义 run_dir 生效。"""
        custom_run = self.tmpdir / "custom" / "run1"
        driver = GitDriver(
            repo_root=self.tmpdir,
            run_id="r1",
            run_dir=custom_run,
        )
        self.assertEqual(driver._run_dir, custom_run.resolve())


class TestGitDriverNotRepo(unittest.TestCase):
    """测试非 git 仓库的检测。"""

    def setUp(self):
        # 创建非 git 目录
        self.tmpdir = Path(tempfile.mkdtemp())
        self.driver = GitDriver(
            repo_root=self.tmpdir,
            run_id="test",
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_05_is_git_repo_false_for_non_repo(self):
        """非 git 目录应返回 False。"""
        self.assertFalse(self.driver.is_git_repo())


class TestGitDriverStatus(unittest.TestCase):
    """测试 status() 行为。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        _init_git_repo(self.tmpdir)
        self.driver = GitDriver(
            repo_root=self.tmpdir,
            run_id="test",
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_06_status_clean_returns_empty(self):
        """干净工作区 status 返回空 stdout。"""
        result = self.driver.status()
        self.assertTrue(result.success)
        self.assertEqual(result.stdout.strip(), "")

    def test_07_status_detects_modification(self):
        """修改文件后 status 应检测到。"""
        (self.tmpdir / "README.md").write_text("# Modified", encoding="utf-8")
        result = self.driver.status()
        self.assertTrue(result.success)
        self.assertIn("README.md", result.stdout)

    def test_08_status_detects_untracked(self):
        """新增 untracked 文件 status 应检测到。"""
        (self.tmpdir / "new.txt").write_text("hello", encoding="utf-8")
        result = self.driver.status()
        self.assertTrue(result.success)
        self.assertIn("new.txt", result.stdout)


class TestGitDriverCommit(unittest.TestCase):
    """测试 commit() 行为。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        _init_git_repo(self.tmpdir)
        self.driver = GitDriver(
            repo_root=self.tmpdir,
            run_id="test",
            author_name="Ralph Test",
            author_email="ralph@test.local",
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_09_commit_empty_fails(self):
        """空 commit message 应失败。"""
        result = self.driver.commit("")
        self.assertFalse(result.success)

    def test_10_commit_no_changes_fails(self):
        """工作区干净时 commit 应失败（避免空 commit）。"""
        result = self.driver.commit("test commit")
        self.assertFalse(result.success)
        # 错误信息中应包含"干净"或"无变更"
        combined = result.error_message.lower() + result.stderr.lower()
        self.assertTrue(
            "干净" in combined or "nothing" in combined or "无变更" in combined,
            f"错误信息不明确: {combined}"
        )

    def test_11_commit_with_changes_succeeds(self):
        """有变更时 commit 应成功。"""
        (self.tmpdir / "new.txt").write_text("hello", encoding="utf-8")
        result = self.driver.commit("add new.txt")
        self.assertTrue(result.success)

    def test_12_commit_uses_custom_author(self):
        """commit 使用自定义 author（环境变量注入，不修改全局配置）。"""
        (self.tmpdir / "author_test.txt").write_text("x", encoding="utf-8")
        result = self.driver.commit("test author")
        self.assertTrue(result.success)
        # 验证：log 中 author 是自定义的
        log_result = subprocess.run(
            ["git", "log", "-1", "--format=%an <%ae>"],
            cwd=self.tmpdir,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("Ralph Test", log_result.stdout)
        self.assertIn("ralph@test.local", log_result.stdout)

    def test_13_commit_increments_log(self):
        """多次 commit 后 git log 行数增加。"""
        for i in range(3):
            (self.tmpdir / f"file{i}.txt").write_text(f"content {i}", encoding="utf-8")
            r = self.driver.commit(f"add file{i}")
            self.assertTrue(r.success)
        log = self.driver.log_last_n(10)
        # 应有 1 init + 3 new = 4 条
        self.assertEqual(len(log), 4)


class TestGitDriverDiffStats(unittest.TestCase):
    """测试 diff_stats() 行为。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        _init_git_repo(self.tmpdir)
        self.driver = GitDriver(
            repo_root=self.tmpdir,
            run_id="test",
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_14_diff_stats_after_commit(self):
        """commit 后 diff_stats（与上次 commit 相比）应正确统计。

        注：diff_stats() 默认对比 HEAD，但 commit 后无未提交变更。
        使用 since_commit=<init_commit> 验证：1 文件 +3 行。
        """
        # 获取 init commit 的 SHA
        init_sha = self.driver.get_current_sha()
        # 添加新文件
        (self.tmpdir / "f.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")
        # 提交
        result = self.driver.commit("add f.txt")
        self.assertTrue(result.success)
        # 验证：从 init_sha 到 HEAD 的 diff
        stats = self.driver.diff_stats(since_commit=init_sha)
        self.assertEqual(stats.files_changed, 1)
        self.assertEqual(stats.lines_added, 3)
        self.assertEqual(stats.lines_removed, 0)

    def test_15_diff_stats_modification(self):
        """修改文件后 diff_stats 统计新增和删除。

        使用 since_commit=<init_commit> 验证统计。
        """
        # 获取 init commit 的 SHA
        init_sha = self.driver.get_current_sha()
        # 修改 README.md（原 1 行 → 新 3 行）
        (self.tmpdir / "README.md").write_text("a\nb\nc\n", encoding="utf-8")
        # 提交
        self.driver.commit("modify")
        # 验证：lines_added > 0
        stats = self.driver.diff_stats(since_commit=init_sha)
        self.assertGreater(stats.lines_added, 0)


class TestGitDriverRollback(unittest.TestCase):
    """测试 rollback() 行为（保留 uncommitted work）。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        _init_git_repo(self.tmpdir)
        self.driver = GitDriver(
            repo_root=self.tmpdir,
            run_id="test-run",
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_16_rollback_no_changes(self):
        """无变更时 rollback 应返回成功且无快照。"""
        result = self.driver.rollback()
        self.assertTrue(result.success)

    def test_17_rollback_preserves_modified(self):
        """rollback 应保留 modified 文件到 uncommitted 目录。"""
        # 1. 创建修改
        (self.tmpdir / "modified.txt").write_text("modified content", encoding="utf-8")
        # 2. rollback
        result = self.driver.rollback()
        self.assertTrue(result.success)
        # 3. 验证：uncommitted 目录中有文件
        uncommitted_root = self.tmpdir / ".gnhf" / "runs" / "test-run" / "uncommitted"
        self.assertTrue(uncommitted_root.exists())
        # 找到时间戳目录
        snapshot_dirs = [d for d in uncommitted_root.iterdir() if d.is_dir()]
        self.assertGreater(len(snapshot_dirs), 0)
        # 验证 manifest.json 存在
        manifest = snapshot_dirs[0] / "manifest.json"
        self.assertTrue(manifest.exists())

    def test_18_rollback_undoes_tracked(self):
        """rollback 后 tracked 文件的修改应被撤销。"""
        # 修改 README.md（原内容 "# Test"）
        (self.tmpdir / "README.md").write_text("# BAD CHANGE", encoding="utf-8")
        # rollback
        result = self.driver.rollback()
        self.assertTrue(result.success)
        # 验证：README.md 恢复为原内容
        content = (self.tmpdir / "README.md").read_text(encoding="utf-8")
        self.assertEqual(content, "# Test")


class TestGitDriverRestoreUncommitted(unittest.TestCase):
    """测试 restore_uncommitted() 行为。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        _init_git_repo(self.tmpdir)
        self.driver = GitDriver(
            repo_root=self.tmpdir,
            run_id="test-run",
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_19_restore_uncommitted_works(self):
        """restore_uncommitted() 应能恢复文件。"""
        # 1. 制造变更 + rollback
        (self.tmpdir / "to_restore.txt").write_text("restore me", encoding="utf-8")
        self.driver.rollback()
        # 2. 找到 manifest
        uncommitted_root = self.tmpdir / ".gnhf" / "runs" / "test-run" / "uncommitted"
        snapshot_dirs = [d for d in uncommitted_root.iterdir() if d.is_dir()]
        manifest = snapshot_dirs[0] / "manifest.json"
        # 3. 删除原文件模拟
        # 实际上 rollback 后原文件已被 checkout 撤销
        # 但 snapshot 目录中保留了 untracked 文件
        # 4. restore
        result = self.driver.restore_uncommitted(manifest)
        self.assertTrue(result.success)

    def test_20_restore_uncommitted_missing_manifest(self):
        """restore_uncommitted() 在 manifest 不存在时返回失败。"""
        fake_manifest = self.tmpdir / "nonexistent_manifest.json"
        result = self.driver.restore_uncommitted(fake_manifest)
        self.assertFalse(result.success)


class TestGitDriverLog(unittest.TestCase):
    """测试 log_last_n() 和 get_current_sha() 行为。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        _init_git_repo(self.tmpdir)
        self.driver = GitDriver(
            repo_root=self.tmpdir,
            run_id="test",
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_21_log_last_n_returns_list(self):
        """log_last_n() 返回字符串列表。"""
        log = self.driver.log_last_n(5)
        self.assertIsInstance(log, list)
        self.assertEqual(len(log), 1)  # 至少有 init commit

    def test_22_log_last_n_correct_count(self):
        """log_last_n(n) 最多返回 n 条。"""
        # 多 commit 几次
        for i in range(3):
            (self.tmpdir / f"f{i}.txt").write_text("x", encoding="utf-8")
            self.driver.commit(f"commit {i}")
        log = self.driver.log_last_n(2)
        self.assertEqual(len(log), 2)

    def test_23_get_current_sha(self):
        """get_current_sha() 返回当前 commit hash 短格式。"""
        sha = self.driver.get_current_sha()
        self.assertTrue(len(sha) >= 7)  # 短 hash 通常 7+ 字符
        self.assertNotEqual(sha, "")


class TestGitDriverMissingBinary(unittest.TestCase):
    """测试 git 命令不可用的情况。"""

    def test_24_handles_no_git(self):
        """当 git 不在 PATH 时，命令应返回明确错误。"""
        # 此测试用例仅在特殊环境运行：直接修改 PATH
        original_path = os.environ.get("PATH", "")
        os.environ["PATH"] = ""
        try:
            tmpdir = Path(tempfile.mkdtemp())
            driver = GitDriver(repo_root=tmpdir, run_id="test")
            result = driver.status()
            self.assertFalse(result.success)
            self.assertIn("git", result.error_message.lower())
        finally:
            os.environ["PATH"] = original_path


if __name__ == "__main__":
    unittest.main()
