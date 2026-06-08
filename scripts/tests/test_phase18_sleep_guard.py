"""Phase 18: SleepGuard 单元测试。

测试 SleepGuard 的全部行为：
- acquire() / release() / is_active()
- 平台检测（macOS / Linux / Windows）
- OFF 模式（no-op）
- atexit 兜底
- 异常隔离
"""
import platform
import shutil
import signal
import unittest
from unittest.mock import patch

from autonomous.sleep_guard import SleepGuard, SleepGuardMode, SleepGuardHandle


class TestSleepGuardDetect(unittest.TestCase):
    """测试平台检测。"""

    def test_01_detect_darwin_caffeinate(self):
        """macOS 平台 + caffeinate 可用时返回 caffeinate。"""
        with patch.object(platform, "system", return_value="Darwin"), \
             patch.object(shutil, "which", return_value="/usr/bin/caffeinate"):
            backend = SleepGuard.detect_platform_backend()
            self.assertEqual(backend, "caffeinate")

    def test_02_detect_darwin_no_caffeinate(self):
        """macOS 平台 + caffeinate 不可用时返回 noop。"""
        with patch.object(platform, "system", return_value="Darwin"), \
             patch.object(shutil, "which", return_value=None):
            backend = SleepGuard.detect_platform_backend()
            self.assertEqual(backend, "noop")

    def test_03_detect_linux_systemd(self):
        """Linux 平台 + systemd-inhibit 可用时返回 systemd-inhibit。"""
        with patch.object(platform, "system", return_value="Linux"), \
             patch.object(shutil, "which", return_value="/usr/bin/systemd-inhibit"):
            backend = SleepGuard.detect_platform_backend()
            self.assertEqual(backend, "systemd-inhibit")

    def test_04_detect_windows_noop(self):
        """Windows 平台返回 noop。"""
        with patch.object(platform, "system", return_value="Windows"):
            backend = SleepGuard.detect_platform_backend()
            self.assertEqual(backend, "noop")


class TestSleepGuardOffMode(unittest.TestCase):
    """测试 OFF 模式（不启动子进程）。"""

    def test_05_off_mode_noop(self):
        """OFF 模式 acquire() 返回 noop handle。"""
        sg = SleepGuard(mode=SleepGuardMode.OFF)
        handle = sg.acquire()
        self.assertEqual(handle.backend, "noop")
        self.assertIsNone(handle.process)
        self.assertFalse(sg.is_active())
        sg.release()

    def test_06_off_mode_release_safe(self):
        """OFF 模式 release() 不抛异常。"""
        sg = SleepGuard(mode=SleepGuardMode.OFF)
        sg.acquire()
        sg.release()  # 应正常返回
        sg.release()  # 多次 release 不抛异常


class TestSleepGuardAcquire(unittest.TestCase):
    """测试 acquire() 行为。"""

    def test_07_acquire_with_noop_backend(self):
        """当后端为 noop 时 acquire() 返回 noop handle。"""
        with patch.object(SleepGuard, "detect_platform_backend", return_value="noop"):
            sg = SleepGuard()
            handle = sg.acquire()
            self.assertEqual(handle.backend, "noop")
            self.assertFalse(sg.is_active())
            sg.release()

    def test_08_acquire_darwin_starts_caffeinate(self):
        """macOS 平台 + caffeinate 可用时启动 caffeinate 子进程。"""
        with patch.object(SleepGuard, "detect_platform_backend", return_value="caffeinate"), \
             patch("subprocess.Popen") as mock_popen:
            # 模拟 Popen 返回一个 alive 进程
            mock_proc = unittest.mock.MagicMock()
            mock_proc.poll.return_value = None
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc

            sg = SleepGuard()
            handle = sg.acquire()
            self.assertEqual(handle.backend, "caffeinate")
            self.assertTrue(sg.is_active())
            self.assertEqual(handle.process, mock_proc)
            sg.release()

    def test_09_acquire_popen_failure_falls_back_to_noop(self):
        """Popen 失败时降级为 noop（不抛异常）。"""
        with patch.object(SleepGuard, "detect_platform_backend", return_value="caffeinate"), \
             patch("subprocess.Popen", side_effect=OSError("caffeinate not found")):
            sg = SleepGuard()
            handle = sg.acquire()
            self.assertEqual(handle.backend, "noop")
            self.assertFalse(sg.is_active())
            sg.release()


class TestSleepGuardRelease(unittest.TestCase):
    """测试 release() 行为。"""

    def test_10_release_with_no_handle(self):
        """release() 在 handle 为 None 时为 no-op。"""
        sg = SleepGuard()
        # 未 acquire
        sg.release()  # 不抛异常

    def test_11_release_terminates_process(self):
        """release() 应调用 process.terminate()。"""
        with patch.object(SleepGuard, "detect_platform_backend", return_value="caffeinate"), \
             patch("subprocess.Popen") as mock_popen:
            mock_proc = unittest.mock.MagicMock()
            mock_proc.poll.return_value = None
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc

            sg = SleepGuard()
            sg.acquire()
            sg.release()
            # 验证：terminate 被调用
            mock_proc.terminate.assert_called_once()
            # 验证：handle 被清空
            self.assertIsNone(sg._handle)

    def test_12_release_handles_dead_process(self):
        """release() 在进程已死时不抛异常。"""
        with patch.object(SleepGuard, "detect_platform_backend", return_value="caffeinate"), \
             patch("subprocess.Popen") as mock_popen:
            mock_proc = unittest.mock.MagicMock()
            # 模拟进程已死（poll 返回 0）
            mock_proc.poll.return_value = 0
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc

            sg = SleepGuard()
            sg.acquire()
            sg.release()  # 不抛异常


class TestSleepGuardIsActive(unittest.TestCase):
    """测试 is_active() 行为。"""

    def test_13_is_active_false_initially(self):
        """is_active() 初始为 False。"""
        sg = SleepGuard()
        self.assertFalse(sg.is_active())

    def test_14_is_active_after_release(self):
        """release() 后 is_active() 返回 False。"""
        with patch.object(SleepGuard, "detect_platform_backend", return_value="noop"):
            sg = SleepGuard()
            sg.acquire()
            sg.release()
            self.assertFalse(sg.is_active())


class TestSleepGuardBackendName(unittest.TestCase):
    """测试 backend_name() 行为。"""

    def test_15_backend_name_uninitialized(self):
        """未 acquire 时返回 'uninitialized'。"""
        sg = SleepGuard()
        self.assertEqual(sg.backend_name(), "uninitialized")

    def test_16_backend_name_after_acquire(self):
        """acquire() 后返回实际后端名称。"""
        with patch.object(SleepGuard, "detect_platform_backend", return_value="noop"):
            sg = SleepGuard()
            sg.acquire()
            self.assertEqual(sg.backend_name(), "noop")
            sg.release()


if __name__ == "__main__":
    unittest.main()
