"""Ponytail 模式跟踪器单元测试。

覆盖 15 个用例：
- TC-MT-01~03: 默认模式优先级（env > config > full）
- TC-MT-04~07: set/get/clear 模式
- TC-MT-08~12: 命令解析
- TC-MT-13~14: 损坏文件容错
- TC-MT-15: 并发安全
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

# 确保 scripts 目录在 path 中
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from ponytail.mode_tracker import ModeTracker


class TestModeTracker(unittest.TestCase):
    """ModeTracker 单元测试。"""

    def setUp(self):
        """每个测试前清理标志文件和环境变量。"""
        # 清理标志文件
        try:
            ModeTracker._FLAG_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        # 清理环境变量
        os.environ.pop("PONYTAIL_DEFAULT_MODE", None)

    def tearDown(self):
        """每个测试后清理。"""
        try:
            ModeTracker._FLAG_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        os.environ.pop("PONYTAIL_DEFAULT_MODE", None)

    # ==================== 默认模式优先级测试 ====================

    def test_01_env_var_highest_priority(self):
        """TC-MT-01: 环境变量优先级最高。"""
        os.environ["PONYTAIL_DEFAULT_MODE"] = "ultra"
        self.assertEqual(ModeTracker.get_default_mode(), "ultra")

    def test_02_config_file_second_priority(self):
        """TC-MT-02: 配置文件次之（无 env 时）。"""
        # 写入配置文件
        ModeTracker._CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ModeTracker._CONFIG_FILE.write_text(
            json.dumps({"defaultMode": "lite"}), encoding="utf-8"
        )
        try:
            self.assertEqual(ModeTracker.get_default_mode(), "lite")
        finally:
            ModeTracker._CONFIG_FILE.unlink(missing_ok=True)

    def test_03_default_is_full(self):
        """TC-MT-03: 无 env 无配置时默认 full。"""
        self.assertEqual(ModeTracker.get_default_mode(), "full")

    # ==================== set/get/clear 测试 ====================

    def test_04_set_mode_persists_to_flag_file(self):
        """TC-MT-04: set_mode 持久化到标志文件。"""
        ModeTracker.set_mode("ultra")
        self.assertTrue(ModeTracker._FLAG_FILE.exists())
        self.assertEqual(
            ModeTracker._FLAG_FILE.read_text(encoding="utf-8"), "ultra"
        )

    def test_05_invalid_mode_not_persisted(self):
        """TC-MT-05: 非法模式不持久化。"""
        ModeTracker.set_mode("invalid")
        self.assertFalse(ModeTracker._FLAG_FILE.exists())

    def test_06_get_current_mode_reads_flag_file(self):
        """TC-MT-06: get_current_mode 优先读标志文件。"""
        ModeTracker.set_mode("lite")
        self.assertEqual(ModeTracker.get_current_mode(), "lite")

    def test_07_clear_mode_deletes_flag_file(self):
        """TC-MT-07: clear_mode 删除标志文件。"""
        ModeTracker.set_mode("ultra")
        self.assertTrue(ModeTracker._FLAG_FILE.exists())
        ModeTracker.clear_mode()
        self.assertFalse(ModeTracker._FLAG_FILE.exists())

    # ==================== 命令解析测试 ====================

    def test_08_parse_ponytail_ultra_command(self):
        """TC-MT-08: 解析 /ponytail ultra 命令。"""
        self.assertEqual(ModeTracker.parse_user_command("/ponytail ultra"), "ultra")

    def test_09_parse_ponytail_off_command(self):
        """TC-MT-09: 解析 /ponytail off 命令。"""
        self.assertEqual(ModeTracker.parse_user_command("/ponytail off"), "off")

    def test_10_parse_stop_ponytail_command(self):
        """TC-MT-10: 解析 stop ponytail 命令。"""
        self.assertEqual(ModeTracker.parse_user_command("stop ponytail"), "off")

    def test_11_parse_normal_mode_command(self):
        """TC-MT-11: 解析 normal mode 命令。"""
        self.assertEqual(ModeTracker.parse_user_command("normal mode"), "off")

    def test_12_no_command_returns_current_mode(self):
        """TC-MT-12: 无命令返回当前模式。"""
        ModeTracker.set_mode("lite")
        self.assertEqual(ModeTracker.parse_user_command("普通输入"), "lite")

    # ==================== 损坏文件容错测试 ====================

    def test_13_corrupted_config_file_falls_back(self):
        """TC-MT-13: 配置文件 JSON 损坏不崩溃，回退到默认。"""
        ModeTracker._CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ModeTracker._CONFIG_FILE.write_text("invalid json", encoding="utf-8")
        try:
            self.assertEqual(ModeTracker.get_default_mode(), "full")
        finally:
            ModeTracker._CONFIG_FILE.unlink(missing_ok=True)

    def test_14_corrupted_flag_file_falls_back(self):
        """TC-MT-14: 标志文件内容损坏不崩溃，回退到默认。"""
        ModeTracker._FLAG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ModeTracker._FLAG_FILE.write_text("garbage", encoding="utf-8")
        try:
            self.assertEqual(ModeTracker.get_current_mode(), "full")
        finally:
            ModeTracker._FLAG_FILE.unlink(missing_ok=True)

    # ==================== 并发安全测试 ====================

    def test_15_concurrent_set_mode_no_corruption(self):
        """TC-MT-15: 并发 set_mode 不损坏文件。"""
        errors = []

        def set_mode_worker(mode):
            """线程函数：设置模式。"""
            try:
                for _ in range(20):
                    ModeTracker.set_mode(mode)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=set_mode_worker, args=("lite",)),
            threading.Thread(target=set_mode_worker, args=("full",)),
            threading.Thread(target=set_mode_worker, args=("ultra",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 验证无错误
        self.assertEqual(len(errors), 0, f"并发写入产生错误: {errors}")

        # 验证文件内容是某个合法模式
        if ModeTracker._FLAG_FILE.exists():
            content = ModeTracker._FLAG_FILE.read_text(encoding="utf-8").strip()
            self.assertIn(content, ModeTracker.VALID_MODES)


if __name__ == "__main__":
    unittest.main()
