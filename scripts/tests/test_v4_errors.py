"""V4 异常类单元测试（Phase 17 §5.1 阶段 0）。

测试目标：验证 dispatcher.errors 中新增 4 个异常类
- PluginNotFoundError：hot_unregister 时 plugin 不存在
- PluginBusyError：plugin 正在执行，无法立即 unregister
- DropInLoadError：drop-in 文件加载失败
- DropInPathError：drop-in 路径不安全

每个异常类至少 1 个测试，覆盖：
1. 异常可被 raise
2. 异常消息含具体信息（plugin name / 文件路径）
3. 异常继承 DispatcherError 基类
4. DropIn*Error 继承关系（属于 DispatcherError 体系）
"""
import unittest

from dispatcher.errors import (
    DispatcherError,
    PluginNotFoundError,
    PluginBusyError,
    DropInLoadError,
    DropInPathError,
)


class TestPluginNotFoundError(unittest.TestCase):
    """v3 新增：hot_unregister 时 plugin 不存在。"""

    def test_raises_with_plugin_name(self):
        """抛出异常时错误消息含被引用的 plugin name。"""
        with self.assertRaises(PluginNotFoundError) as cm:
            raise PluginNotFoundError("ghost-plugin")
        self.assertIn("ghost-plugin", str(cm.exception))

    def test_inherits_dispatcher_error(self):
        """PluginNotFoundError 必须继承 DispatcherError 基类（统一捕获）。"""
        self.assertTrue(issubclass(PluginNotFoundError, DispatcherError))

    def test_caught_by_dispatcher_error_handler(self):
        """上层用 except DispatcherError 可捕获（统一错误处理路径）。"""
        with self.assertRaises(DispatcherError):
            raise PluginNotFoundError("missing-plugin")


class TestPluginBusyError(unittest.TestCase):
    """v3 新增：plugin 正在执行 execute()，无法立即 unregister。"""

    def test_raises_with_plugin_name(self):
        """抛出异常时错误消息含正在执行的 plugin name。"""
        with self.assertRaises(PluginBusyError) as cm:
            raise PluginBusyError("busy-plugin")
        self.assertIn("busy-plugin", str(cm.exception))

    def test_inherits_dispatcher_error(self):
        """PluginBusyError 必须继承 DispatcherError 基类。"""
        self.assertTrue(issubclass(PluginBusyError, DispatcherError))

    def test_distinct_from_plugin_not_found(self):
        """PluginBusyError 与 PluginNotFoundError 是不同异常类（语义不同）。"""
        self.assertFalse(issubclass(PluginBusyError, PluginNotFoundError))
        self.assertFalse(issubclass(PluginNotFoundError, PluginBusyError))


class TestDropInLoadError(unittest.TestCase):
    """v3 新增：drop-in 文件加载失败。"""

    def test_raises_with_file_path(self):
        """抛出异常时错误消息含失败的文件路径。"""
        with self.assertRaises(DropInLoadError) as cm:
            raise DropInLoadError("/tmp/evil.py")
        self.assertIn("/tmp/evil.py", str(cm.exception))

    def test_inherits_dispatcher_error(self):
        """DropInLoadError 必须继承 DispatcherError 基类。"""
        self.assertTrue(issubclass(DropInLoadError, DispatcherError))


class TestDropInPathError(unittest.TestCase):
    """v3 新增：drop-in 路径不安全（P0-7 路径穿越防护）。"""

    def test_raises_with_path_info(self):
        """抛出异常时错误消息含不安全路径信息。"""
        with self.assertRaises(DropInPathError) as cm:
            raise DropInPathError("/etc/passwd")
        # 错误消息应含路径（具体实现可能扩展为 "必须在 project_root 内" 等）
        # 这里只断言路径出现在消息中（用于调试定位）
        self.assertIn("/etc/passwd", str(cm.exception))

    def test_inherits_dispatcher_error(self):
        """DropInPathError 必须继承 DispatcherError 基类。"""
        self.assertTrue(issubclass(DropInPathError, DispatcherError))

    def test_distinct_from_drop_in_load_error(self):
        """DropInPathError 与 DropInLoadError 是不同异常类（语义不同：路径 vs 加载）。"""
        self.assertFalse(issubclass(DropInPathError, DropInLoadError))
        self.assertFalse(issubclass(DropInLoadError, DropInPathError))


if __name__ == "__main__":
    unittest.main()
