#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
worktree_manager.py 单元测试 + 集成测试

测试目标：
- 路径白名单（系统目录、用户主目录、白名单校验）
- WorktreeManager 完整生命周期（create/remove/list/cleanup）
- 降级策略（非 Git 环境）
- 错误路径（路径越权、已存在、超时）
- 并发安全

作者：trae-multi-agent 融合 Phase 2
创建日期：2026-06-03
"""

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# 添加 scripts 目录到 sys.path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

DYNAMIC_WORKFLOW_DIR = SCRIPTS_DIR / "dynamic_workflow"
if str(DYNAMIC_WORKFLOW_DIR) not in sys.path:
    sys.path.insert(0, str(DYNAMIC_WORKFLOW_DIR))

from worktree_manager import (  # noqa: E402
    GitNotAvailableError,
    WorktreeAlreadyExistsError,
    WorktreeError,
    WorktreeInfo,
    WorktreeManager,
    WorktreeNotFoundError,
    WorktreePathError,
    WorktreeTimeoutError,
    _check_git_available,
    _get_default_branch,
    _get_git_root,
    _is_git_repo,
    _is_path_safe,
)


def _run(cmd: str, cwd: str = None) -> tuple:
    """执行 shell 命令"""
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, cwd=cwd
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _create_git_repo(path: str, default_branch: str = "master") -> None:
    """在指定路径创建 Git 仓库"""
    _run(f"git init -b {default_branch}", cwd=path)
    _run("git config user.email test@test.com", cwd=path)
    _run("git config user.name test", cwd=path)
    with open(f"{path}/README.md", "w") as f:
        f.write("# Test")
    _run("git add .", cwd=path)
    _run("git commit -m init", cwd=path)


# ============================================================================
# 1. 路径白名单测试
# ============================================================================

class TestPathSafety(unittest.TestCase):
    """测试路径白名单 _is_path_safe"""

    def test_01_etc_rejected(self):
        """/etc 应被拒绝"""
        with self.assertRaises(WorktreePathError):
            _is_path_safe("/etc", ["/tmp"])

    def test_02_etc_subdir_rejected(self):
        """/etc/foo 应被拒绝"""
        with self.assertRaises(WorktreePathError):
            _is_path_safe("/etc/foo", ["/tmp"])

    def test_03_usr_local_bin_rejected(self):
        """/usr/local/bin 应被拒绝"""
        with self.assertRaises(WorktreePathError):
            _is_path_safe("/usr/local/bin", ["/tmp"])

    def test_04_macos_system_rejected(self):
        """/System/Library 应被拒绝"""
        with self.assertRaises(WorktreePathError):
            _is_path_safe("/System/Library", ["/tmp"])

    def test_05_tmp_allowed(self):
        """/tmp/test 应被允许"""
        result = _is_path_safe("/tmp/test", ["/tmp"])
        self.assertTrue(result)

    def test_06_tmp_subdir_allowed(self):
        """/tmp/foo/bar 应被允许"""
        result = _is_path_safe("/tmp/foo/bar", ["/tmp"])
        self.assertTrue(result)

    def test_07_private_etc_rejected(self):
        """/private/etc/passwd 应被拒绝（macOS 系统目录）"""
        with self.assertRaises(WorktreePathError):
            _is_path_safe("/private/etc/passwd", ["/tmp"])

    def test_08_private_tmp_allowed(self):
        """/private/tmp/test 应被允许（macOS 用户 temp）"""
        result = _is_path_safe("/private/tmp/test", ["/tmp"])
        self.assertTrue(result)

    def test_09_var_log_rejected(self):
        """/var/log/test 应被拒绝"""
        with self.assertRaises(WorktreePathError):
            _is_path_safe("/var/log/test", ["/tmp"])

    def test_10_root_rejected(self):
        """/ 应被拒绝"""
        with self.assertRaises(WorktreePathError):
            _is_path_safe("/", ["/tmp"])

    def test_11_dev_rejected(self):
        """/dev/null 应被拒绝"""
        with self.assertRaises(WorktreePathError):
            _is_path_safe("/dev/null", ["/tmp"])

    def test_12_bin_rejected(self):
        """/bin/ls 应被拒绝"""
        with self.assertRaises(WorktreePathError):
            _is_path_safe("/bin/ls", ["/tmp"])

    def test_13_empty_path_rejected(self):
        """空路径应被拒绝"""
        with self.assertRaises(WorktreePathError):
            _is_path_safe("", ["/tmp"])

    def test_14_user_temp_fallback(self):
        """用户临时目录默认兜底允许"""
        import tempfile
        user_temp = tempfile.gettempdir()
        result = _is_path_safe(f"{user_temp}/mywork", ["/nonexistent"])
        self.assertTrue(result)

    def test_15_home_dir_requires_allowlist(self):
        """用户主目录需要显式在白名单"""
        home = str(Path.home())
        with self.assertRaises(WorktreePathError):
            _is_path_safe(f"{home}/test", ["/tmp"])

    def test_16_home_dir_with_allowlist(self):
        """用户主目录在白名单中允许"""
        home = str(Path.home())
        result = _is_path_safe(f"{home}/test", [home])
        self.assertTrue(result)


# ============================================================================
# 2. Git 命令封装测试
# ============================================================================

class TestGitUtilities(unittest.TestCase):
    """测试 Git 命令封装"""

    def test_01_check_git_available(self):
        """git --version 应返回 True（在测试机器上 git 可用）"""
        # 大多数 CI 和开发机都有 git
        result = _check_git_available()
        self.assertTrue(result)

    def test_02_get_git_root_in_repo(self):
        """在 Git 仓库中获取根目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            _create_git_repo(tmpdir)
            root = _get_git_root(tmpdir)
            self.assertIsNotNone(root)
            # 标准化路径后比较
            self.assertEqual(Path(root).resolve(), Path(tmpdir).resolve())

    def test_03_get_git_root_not_in_repo(self):
        """非 Git 仓库返回 None"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _get_git_root(tmpdir)
            self.assertIsNone(root)

    def test_04_is_git_repo_true(self):
        """Git 仓库应返回 True"""
        with tempfile.TemporaryDirectory() as tmpdir:
            _create_git_repo(tmpdir)
            self.assertTrue(_is_git_repo(tmpdir))

    def test_05_is_git_repo_false(self):
        """非 Git 仓库返回 False"""
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertFalse(_is_git_repo(tmpdir))

    def test_06_get_default_branch_master(self):
        """获取默认分支（master）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            _create_git_repo(tmpdir, default_branch="master")
            branch = _get_default_branch(tmpdir)
            self.assertEqual(branch, "master")

    def test_07_get_default_branch_main(self):
        """获取默认分支（main）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            _create_git_repo(tmpdir, default_branch="main")
            branch = _get_default_branch(tmpdir)
            self.assertEqual(branch, "main")


# ============================================================================
# 3. WorktreeManager 基本功能测试
# ============================================================================

class TestWorktreeManagerBasic(unittest.TestCase):
    """测试 WorktreeManager 基本功能"""

    def setUp(self):
        """创建临时 Git 仓库"""
        self.tmpdir = tempfile.mkdtemp()
        _create_git_repo(self.tmpdir, default_branch="master")
        self.wt_base = f"{self.tmpdir}/.dw_worktrees"

    def tearDown(self):
        """清理"""
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_init(self):
        """WorktreeManager 初始化"""
        wm = WorktreeManager(
            base_path=self.wt_base,
            allow_paths=[self.tmpdir],
            git_root=self.tmpdir,
        )
        self.assertTrue(wm.git_available)
        self.assertEqual(wm.active_count, 0)

    def test_02_create_worktree(self):
        """创建 worktree"""
        wm = WorktreeManager(
            base_path=self.wt_base,
            allow_paths=[self.tmpdir],
            git_root=self.tmpdir,
        )
        info = wm.create(agent_id="sa_001", base_branch="master")
        self.assertIsNotNone(info)
        self.assertTrue(info.git_available)
        self.assertTrue(Path(info.worktree_path).exists())
        self.assertEqual(wm.active_count, 1)

    def test_03_remove_worktree(self):
        """移除 worktree"""
        wm = WorktreeManager(
            base_path=self.wt_base,
            allow_paths=[self.tmpdir],
            git_root=self.tmpdir,
        )
        info = wm.create(agent_id="sa_001", base_branch="master")
        wm.remove(info.worktree_path)
        self.assertEqual(wm.active_count, 0)
        self.assertFalse(Path(info.worktree_path).exists())

    def test_04_list_active(self):
        """列出活跃 worktree"""
        wm = WorktreeManager(
            base_path=self.wt_base,
            allow_paths=[self.tmpdir],
            git_root=self.tmpdir,
        )
        info1 = wm.create(agent_id="sa_001", base_branch="master")
        info2 = wm.create(agent_id="sa_002", base_branch="master")
        active = wm.list_active()
        self.assertEqual(len(active), 2)
        worktree_ids = {a.worktree_id for a in active}
        self.assertIn(info1.worktree_id, worktree_ids)
        self.assertIn(info2.worktree_id, worktree_ids)

    def test_05_get(self):
        """根据 ID 获取 worktree"""
        wm = WorktreeManager(
            base_path=self.wt_base,
            allow_paths=[self.tmpdir],
            git_root=self.tmpdir,
        )
        info = wm.create(agent_id="sa_001", base_branch="master")
        fetched = wm.get(info.worktree_id)
        self.assertEqual(fetched.worktree_id, info.worktree_id)
        # 不存在
        self.assertIsNone(wm.get("nonexistent_id"))

    def test_06_cleanup_all(self):
        """清理所有 worktree"""
        wm = WorktreeManager(
            base_path=self.wt_base,
            allow_paths=[self.tmpdir],
            git_root=self.tmpdir,
        )
        for i in range(3):
            wm.create(agent_id=f"sa_{i}", base_branch="master")
        self.assertEqual(wm.active_count, 3)
        cleaned = wm.cleanup_all()
        self.assertEqual(cleaned, 3)
        self.assertEqual(wm.active_count, 0)

    def test_07_auto_detect_branch(self):
        """自动检测默认分支"""
        wm = WorktreeManager(
            base_path=self.wt_base,
            allow_paths=[self.tmpdir],
            git_root=self.tmpdir,
        )
        # base_branch=None 时自动检测（master）
        info = wm.create(agent_id="sa_001", base_branch=None)
        self.assertEqual(info.base_branch, "master")

    def test_08_residual_cleanup_on_init(self):
        """启动时清理残留 worktree"""
        # 第一次创建
        wm1 = WorktreeManager(
            base_path=self.wt_base,
            allow_paths=[self.tmpdir],
            git_root=self.tmpdir,
        )
        info = wm1.create(agent_id="sa_001", base_branch="master")
        # 不清理，模拟崩溃
        del wm1

        # 第二次创建 WorktreeManager，应自动清理残留
        wm2 = WorktreeManager(
            base_path=self.wt_base,
            allow_paths=[self.tmpdir],
            git_root=self.tmpdir,
        )
        # 残留已清理
        self.assertFalse(Path(info.worktree_path).exists())


# ============================================================================
# 4. WorktreeManager 错误路径测试
# ============================================================================

class TestWorktreeManagerErrors(unittest.TestCase):
    """测试 WorktreeManager 错误路径"""

    def setUp(self):
        """创建临时 Git 仓库"""
        self.tmpdir = tempfile.mkdtemp()
        _create_git_repo(self.tmpdir, default_branch="master")
        self.wt_base = f"{self.tmpdir}/.dw_worktrees"

    def tearDown(self):
        """清理"""
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_path_outside_allowlist(self):
        """路径不在白名单 → WorktreePathError"""
        wm = WorktreeManager(
            base_path="/var/folders/some_other_path",  # 不在白名单
            allow_paths=[self.tmpdir],
            git_root=self.tmpdir,
        )
        with self.assertRaises(WorktreePathError):
            wm.create(agent_id="sa_001", base_branch="master")

    def test_02_remove_nonexistent_worktree(self):
        """移除不存在的 worktree 不抛异常（幂等）"""
        wm = WorktreeManager(
            base_path=self.wt_base,
            allow_paths=[self.tmpdir],
            git_root=self.tmpdir,
        )
        result = wm.remove("/nonexistent/path")
        self.assertTrue(result)  # 幂等返回 True

    def test_03_remove_with_active_record(self):
        """移除活跃 worktree"""
        wm = WorktreeManager(
            base_path=self.wt_base,
            allow_paths=[self.tmpdir],
            git_root=self.tmpdir,
        )
        info = wm.create(agent_id="sa_001", base_branch="master")
        result = wm.remove(info.worktree_path)
        self.assertTrue(result)
        self.assertEqual(wm.active_count, 0)

    def test_04_duplicate_create_same_id(self):
        """重复 create 同名（极小概率但需测试）"""
        wm = WorktreeManager(
            base_path=self.wt_base,
            allow_paths=[self.tmpdir],
            git_root=self.tmpdir,
        )
        # 手动模拟重复：在 _active_worktrees 中插入
        fake_info = WorktreeInfo(
            worktree_id="wt_test123",
            agent_id="dup",
            worktree_path=f"{self.wt_base}/wt_test123",
            base_branch="master",
        )
        wm._active_worktrees["wt_test123"] = fake_info

        # 由于路径生成用 uuid，实际上不会冲突
        # 但我们可以验证 _active_worktrees 不会无限增长
        initial_count = wm.active_count
        wm.create(agent_id="sa_001", base_branch="master")
        self.assertEqual(wm.active_count, initial_count + 1)


# ============================================================================
# 5. WorktreeManager 降级策略测试
# ============================================================================

class TestWorktreeManagerDegradation(unittest.TestCase):
    """测试非 Git 环境的降级策略"""

    def test_01_no_git_returns_none(self):
        """非 Git 环境 create 返回 None"""
        with patch("worktree_manager._check_git_available", return_value=False):
            wm = WorktreeManager(
                base_path="/tmp/somewhere",
                allow_paths=["/tmp"],
                git_root=None,
            )
            result = wm.create(agent_id="sa_001", base_branch="main")
            self.assertIsNone(result)
            self.assertFalse(wm.git_available)

    def test_02_no_git_active_count_zero(self):
        """非 Git 环境 active_count 为 0"""
        with patch("worktree_manager._check_git_available", return_value=False):
            wm = WorktreeManager(
                base_path="/tmp/somewhere",
                allow_paths=["/tmp"],
            )
            self.assertEqual(wm.active_count, 0)

    def test_03_list_empty_when_degraded(self):
        """降级时 list_active 返回空"""
        with patch("worktree_manager._check_git_available", return_value=False):
            wm = WorktreeManager(
                base_path="/tmp/somewhere",
                allow_paths=["/tmp"],
            )
            self.assertEqual(len(wm.list_active()), 0)


# ============================================================================
# 6. WorktreeInfo 数据类测试
# ============================================================================

class TestWorktreeInfo(unittest.TestCase):
    """测试 WorktreeInfo 数据类"""

    def test_01_default_values(self):
        """默认值正确"""
        info = WorktreeInfo(
            worktree_id="wt_test",
            agent_id="sa_001",
            worktree_path="/tmp/test",
            base_branch="main",
        )
        self.assertEqual(info.worktree_id, "wt_test")
        self.assertEqual(info.agent_id, "sa_001")
        self.assertEqual(info.worktree_path, "/tmp/test")
        self.assertEqual(info.base_branch, "main")
        self.assertTrue(info.git_available)
        self.assertTrue(info.cleanup_on_exit)
        self.assertIsNotNone(info.created_at)

    def test_02_to_dict(self):
        """to_dict 序列化完整"""
        info = WorktreeInfo(
            worktree_id="wt_test",
            agent_id="sa_001",
            worktree_path="/tmp/test",
            base_branch="main",
        )
        d = info.to_dict()
        self.assertIn("worktree_id", d)
        self.assertIn("agent_id", d)
        self.assertIn("worktree_path", d)
        self.assertIn("base_branch", d)
        self.assertIn("created_at", d)
        self.assertIn("git_available", d)


# ============================================================================
# 7. 性能基线测试
# ============================================================================

class TestWorktreeManagerPerformance(unittest.TestCase):
    """性能基线：单次 create < 1s（git worktree 通常 100-500ms）"""

    def setUp(self):
        """创建临时 Git 仓库"""
        self.tmpdir = tempfile.mkdtemp()
        _create_git_repo(self.tmpdir, default_branch="master")

    def tearDown(self):
        """清理"""
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_create_latency(self):
        """create 延迟 < 1s"""
        wm = WorktreeManager(
            base_path=f"{self.tmpdir}/.wt",
            allow_paths=[self.tmpdir],
            git_root=self.tmpdir,
        )

        # 预热
        info = wm.create(agent_id="sa_001", base_branch="master")
        wm.remove(info.worktree_path)

        # 测量
        start = time.perf_counter()
        for i in range(5):
            info = wm.create(agent_id=f"sa_{i}", base_branch="master")
            wm.remove(info.worktree_path)
        elapsed = (time.perf_counter() - start) / 5
        self.assertLess(
            elapsed, 1.0,
            f"create+remove 平均时间 {elapsed*1000:.1f}ms 超过基线 1000ms",
        )


# ============================================================================
# 8. 并发安全测试
# ============================================================================

class TestWorktreeManagerConcurrency(unittest.TestCase):
    """并发安全测试"""

    def setUp(self):
        """创建临时 Git 仓库"""
        self.tmpdir = tempfile.mkdtemp()
        _create_git_repo(self.tmpdir, default_branch="master")

    def tearDown(self):
        """清理"""
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_concurrent_create(self):
        """并发创建 5 个 worktree（不同 agent_id）"""
        wm = WorktreeManager(
            base_path=f"{self.tmpdir}/.wt",
            allow_paths=[self.tmpdir],
            git_root=self.tmpdir,
        )

        results = []
        errors = []

        def create_worktree(idx: int):
            try:
                info = wm.create(agent_id=f"sa_{idx}", base_branch="master")
                results.append(info)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [
            threading.Thread(target=create_worktree, args=(i,))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 所有都成功
        self.assertEqual(len(errors), 0, f"并发错误：{errors}")
        self.assertEqual(len(results), 5)
        self.assertEqual(wm.active_count, 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
