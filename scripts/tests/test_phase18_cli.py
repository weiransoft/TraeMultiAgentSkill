"""Phase 18: CLI 参数解析 单元测试。

测试 Phase 18 新增的 17 个 autonomous CLI flag：
- --autonomous
- --auto-max-iterations / --auto-max-tokens
- --auto-stop-when
- --auto-test-command / --auto-stage-order
- --auto-backoff-base / --auto-backoff-max
- --auto-failure-abort
- --auto-resume / --auto-resume-latest
- --auto-no-caffeinate / --auto-no-commit
- --auto-confirm-mode
- --auto-run-dir
- --auto-git-author-name / --auto-git-author-email
- --auto-security-analyzer
- --auto-notes-path / --auto-max-size-kb / --auto-trim-keep-last-n
"""
import argparse
import io
import sys
import unittest
from unittest.mock import patch

from cli.parser import parse_arguments


# ---------------------------------------------------------------------- #
# 工具：用 argv 模拟 CLI 调用                                              #
# ---------------------------------------------------------------------- #


def _parse(argv: list) -> argparse.Namespace:
    """用给定 argv 调用 parse_arguments（替换 sys.argv）。"""
    with patch.object(sys, "argv", ["test"] + argv):
        return parse_arguments()


# ---------------------------------------------------------------------- #
# TestAutonomousFlagsDefaults: 默认值                                    #
# ---------------------------------------------------------------------- #


class TestAutonomousFlagsDefaults(unittest.TestCase):
    """测试 autonomous flag 的默认值。"""

    def test_01_autonomous_default_false(self):
        """--autonomous 默认 False。"""
        args = _parse([])
        self.assertFalse(args.autonomous)

    def test_02_auto_max_iterations_default(self):
        """--auto-max-iterations 默认 50。"""
        args = _parse([])
        self.assertEqual(args.auto_max_iterations, 50)

    def test_03_auto_max_tokens_default(self):
        """--auto-max-tokens 默认 0（不限制）。"""
        args = _parse([])
        self.assertEqual(args.auto_max_tokens, 0)

    def test_04_auto_stop_when_default(self):
        """--auto-stop-when 默认空字符串。"""
        args = _parse([])
        self.assertEqual(args.auto_stop_when, "")

    def test_05_auto_confirm_mode_default(self):
        """--auto-confirm-mode 默认 smart。"""
        args = _parse([])
        self.assertEqual(args.auto_confirm_mode, "smart")

    def test_06_auto_resume_default_none(self):
        """--auto-resume 默认 None。"""
        args = _parse([])
        self.assertIsNone(args.auto_resume)

    def test_07_auto_run_dir_default(self):
        """--auto-run-dir 默认 .gnhf/runs。"""
        args = _parse([])
        self.assertEqual(args.auto_run_dir, ".gnhf/runs")

    def test_08_auto_git_author_default(self):
        """git 作者默认值。"""
        args = _parse([])
        self.assertEqual(args.auto_git_author_name, "Ralph Autonomous Agent")
        self.assertEqual(args.auto_git_author_email, "ralph@trae-multi-agent.local")

    def test_09_auto_security_analyzer_default(self):
        """--auto-security-analyzer 默认 builtin。"""
        args = _parse([])
        self.assertEqual(args.auto_security_analyzer, "builtin")


# ---------------------------------------------------------------------- #
# TestAutonomousFlagsOverride: 覆盖默认值                                #
# ---------------------------------------------------------------------- #


class TestAutonomousFlagsOverride(unittest.TestCase):
    """测试用户传入 flag 后的行为。"""

    def test_10_autonomous_flag_true(self):
        """--autonomous → True。"""
        args = _parse(["--autonomous"])
        self.assertTrue(args.autonomous)

    def test_11_auto_max_iterations_override(self):
        """--auto-max-iterations N → N。"""
        args = _parse(["--auto-max-iterations", "10"])
        self.assertEqual(args.auto_max_iterations, 10)

    def test_12_auto_stage_order_csv(self):
        """--auto-stage-order CSV 解析。"""
        args = _parse(["--auto-stage-order", "dev,plan,fix,verify"])
        self.assertEqual(args.auto_stage_order, "dev,plan,fix,verify")

    def test_13_auto_resume_value(self):
        """--auto-resume r-xxx → 'r-xxx'。"""
        args = _parse(["--auto-resume", "r-abc123"])
        self.assertEqual(args.auto_resume, "r-abc123")

    def test_14_auto_resume_latest_flag(self):
        """--auto-resume-latest → True。"""
        args = _parse(["--auto-resume-latest"])
        self.assertTrue(args.auto_resume_latest)

    def test_15_auto_no_caffeinate_flag(self):
        """--auto-no-caffeinate → True。"""
        args = _parse(["--auto-no-caffeinate"])
        self.assertTrue(args.auto_no_caffeinate)

    def test_16_auto_no_commit_flag(self):
        """--auto-no-commit → True。"""
        args = _parse(["--auto-no-commit"])
        self.assertTrue(args.auto_no_commit)

    def test_17_auto_confirm_mode_value(self):
        """--auto-confirm-mode 接受合法值。"""
        for mode in ("smart", "whitelist-only", "blacklist-only"):
            args = _parse(["--auto-confirm-mode", mode])
            self.assertEqual(args.auto_confirm_mode, mode)

    def test_18_auto_confirm_mode_invalid(self):
        """--auto-confirm-mode 非法值 → SystemExit。"""
        with self.assertRaises(SystemExit):
            with patch.object(sys, "stderr", new=io.StringIO()):
                _parse(["--auto-confirm-mode", "invalid-mode"])


# ---------------------------------------------------------------------- #
# TestAutonomousFlagsCoexist: 与其他 flag 共存                           #
# ---------------------------------------------------------------------- #


class TestAutonomousFlagsCoexist(unittest.TestCase):
    """测试 autonomous flag 与其他 flag 一起使用。"""

    def test_19_autonomous_with_task(self):
        """--autonomous + --task 共存。"""
        args = _parse(["--autonomous", "--task", "实现 X"])
        self.assertTrue(args.autonomous)
        self.assertEqual(args.task, "实现 X")

    def test_20_autonomous_with_dry_run(self):
        """--autonomous + --dry-run 共存。"""
        args = _parse(["--autonomous", "--dry-run"])
        self.assertTrue(args.autonomous)
        self.assertTrue(args.dry_run)

    def test_21_autonomous_with_project_root(self):
        """--autonomous + --project-root 共存。"""
        args = _parse(["--autonomous", "--project-root", "/tmp"])
        self.assertTrue(args.autonomous)
        self.assertEqual(args.project_root, "/tmp")

    def test_22_autonomous_with_loop(self):
        """--autonomous + --loop 共存（虽然互斥，但 parser 允许）。"""
        args = _parse(["--autonomous", "--loop", "5"])
        self.assertTrue(args.autonomous)
        self.assertEqual(args.loop, 5)

    def test_23_all_autonomous_flags_together(self):
        """同时设置所有 autonomous flag。"""
        args = _parse([
            "--autonomous",
            "--auto-max-iterations", "20",
            "--auto-max-tokens", "100000",
            "--auto-stop-when", "all tests pass",
            "--auto-test-command", "pytest",
            "--auto-stage-order", "plan,dev,verify",
            "--auto-backoff-base", "2.0",
            "--auto-backoff-max", "120.0",
            "--auto-failure-abort", "5",
            "--auto-no-caffeinate",
            "--auto-confirm-mode", "whitelist-only",
            "--auto-run-dir", "/tmp/runs",
            "--auto-git-author-name", "Bot",
            "--auto-git-author-email", "bot@x.com",
            "--auto-security-analyzer", "bandit",
            "--auto-notes-path", "log.md",
            "--auto-max-size-kb", "512",
            "--auto-trim-keep-last-n", "10",
        ])
        # 验证所有值
        self.assertTrue(args.autonomous)
        self.assertEqual(args.auto_max_iterations, 20)
        self.assertEqual(args.auto_max_tokens, 100_000)
        self.assertEqual(args.auto_stop_when, "all tests pass")
        self.assertEqual(args.auto_test_command, "pytest")
        self.assertEqual(args.auto_stage_order, "plan,dev,verify")
        self.assertEqual(args.auto_backoff_base, 2.0)
        self.assertEqual(args.auto_backoff_max, 120.0)
        self.assertEqual(args.auto_failure_abort, 5)
        self.assertTrue(args.auto_no_caffeinate)
        self.assertEqual(args.auto_confirm_mode, "whitelist-only")
        self.assertEqual(args.auto_run_dir, "/tmp/runs")
        self.assertEqual(args.auto_git_author_name, "Bot")
        self.assertEqual(args.auto_git_author_email, "bot@x.com")
        self.assertEqual(args.auto_security_analyzer, "bandit")
        self.assertEqual(args.auto_notes_path, "log.md")
        self.assertEqual(args.auto_max_size_kb, 512)
        self.assertEqual(args.auto_trim_keep_last_n, 10)


# ---------------------------------------------------------------------- #
# TestAutonomousFlagsBackwardCompat: 旧 flag 仍可用                      #
# ---------------------------------------------------------------------- #


class TestAutonomousFlagsBackwardCompat(unittest.TestCase):
    """测试旧 CLI flag 仍可用。"""

    def test_24_old_flags_still_work(self):
        """旧 CLI flag（--task / --agent / --project-root）仍可用。"""
        args = _parse([
            "--task", "test",
            "--agent", "solo-coder",
            "--project-root", ".",
        ])
        self.assertEqual(args.task, "test")
        self.assertEqual(args.agent, "solo-coder")
        self.assertEqual(args.project_root, ".")

    def test_25_hot_reload_still_works(self):
        """Phase 17 hot-reload 仍可用。"""
        args = _parse(["--no-hot-reload"])
        self.assertFalse(args.hot_reload)

    def test_26_hot_reload_dir_validated(self):
        """--hot-reload-dir 仍校验绝对路径。"""
        with self.assertRaises(SystemExit):
            with patch.object(sys, "stderr", new=io.StringIO()):
                _parse(["--hot-reload-dir", "/abs/path"])


if __name__ == "__main__":
    unittest.main()
