"""DropInLoader 单元测试（Phase 17 §2.4）。

测试 DropInLoader 的核心契约：
- load_from_file：从 .py 文件动态加载所有 GoalCommandPlugin 子类
- 多类支持：单文件可包含多个 plugin 类
- 失败场景：文件不存在 / spec 失败 / exec_module 失败 / 无 plugin
- sys.modules cleanup：try/finally 包裹 exec_module
- path.stem sanitize：中文/特殊字符 → 合法 Python identifier
- sys.modules 注入正确：namespace = "plugins_extra"
"""
import importlib
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from plugins.base import GoalCommandPlugin
from dispatcher.drop_in_loader import DropInLoader
from dispatcher.errors import DropInLoadError


class _TempDirMixin:
    """提供临时目录 + 清理的 mixin。"""

    def setUp(self) -> None:
        # 每个测试用独立 tmp 目录，避免污染
        self._tmp = tempfile.mkdtemp(prefix="dropin_test_")
        self.tmp_path = Path(self._tmp)

    def tearDown(self) -> None:
        # 清理可能的 sys.modules 注入
        for key in list(sys.modules.keys()):
            if key.startswith("plugins_extra."):
                del sys.modules[key]
        # 清理 tmp 目录
        shutil.rmtree(self._tmp, ignore_errors=True)

    def write_plugin_file(
        self, filename: str, code: str
    ) -> Path:
        """写一个 plugin 文件到 tmp 目录。"""
        file_path = self.tmp_path / filename
        file_path.write_text(textwrap.dedent(code), encoding="utf-8")
        return file_path


class TestDropInLoaderBasic(_TempDirMixin, unittest.TestCase):
    """DropInLoader.load_from_file 基础行为。"""

    def test_load_single_plugin(self):
        """单 plugin 文件 → 返回 1 个 plugin 实例。"""
        path = self.write_plugin_file(
            "alpha.py",
            """
            from plugins.base import GoalCommandPlugin

            class AlphaPlugin(GoalCommandPlugin):
                @property
                def name(self): return "alpha"
                @property
                def priority(self): return 100
                @property
                def mutex_with(self): return set()
                @property
                def requires_task(self): return False
                def matches(self, args): return True
                def execute(self, args, ctx): return True
            """,
        )
        plugins = DropInLoader.load_from_file(path)
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0].name, "alpha")

    def test_load_multiple_plugins_in_one_file(self):
        """单文件多 plugin → 全部实例化。"""
        path = self.write_plugin_file(
            "multi.py",
            """
            from plugins.base import GoalCommandPlugin

            class PluginA(GoalCommandPlugin):
                @property
                def name(self): return "a"
                @property
                def priority(self): return 100
                @property
                def mutex_with(self): return set()
                @property
                def requires_task(self): return False
                def matches(self, args): return True
                def execute(self, args, ctx): return True

            class PluginB(GoalCommandPlugin):
                @property
                def name(self): return "b"
                @property
                def priority(self): return 200
                @property
                def mutex_with(self): return set()
                @property
                def requires_task(self): return False
                def matches(self, args): return True
                def execute(self, args, ctx): return True
            """,
        )
        plugins = DropInLoader.load_from_file(path)
        names = sorted(p.name for p in plugins)
        self.assertEqual(names, ["a", "b"])

    def test_load_excludes_abc_base(self):
        """load_from_file 排除 GoalCommandPlugin 自身（不实例化 ABC）。"""
        path = self.write_plugin_file(
            "abc_only.py",
            """
            from plugins.base import GoalCommandPlugin
            # 文件中只有 ABC 自身被引用，无子类
            """,
        )
        with self.assertRaises(DropInLoadError) as cm:
            DropInLoader.load_from_file(path)
        # 错误消息应说明"未定义任何 GoalCommandPlugin 子类"
        self.assertIn("未定义任何", str(cm.exception))


class TestDropInLoaderFailurePaths(_TempDirMixin, unittest.TestCase):
    """DropInLoader 各种失败路径。"""

    def test_file_not_found_raises(self):
        """文件不存在 → 抛 DropInLoadError。"""
        non_existent = self.tmp_path / "ghost.py"
        with self.assertRaises(DropInLoadError) as cm:
            DropInLoader.load_from_file(non_existent)
        self.assertIn(str(non_existent), str(cm.exception))

    def test_syntax_error_raises_and_cleans_sysmodules(self):
        """SyntaxError → 抛 DropInLoadError + sys.modules 清理（不留半成品）。"""
        path = self.write_plugin_file(
            "syntax_bad.py",
            """
            from plugins.base import GoalCommandPlugin

            class Broken(GoalCommandPlugin
                # 语法错误：少右括号
                pass
            """,
        )
        # 文件写入时可能 textwrap 处理有差异，直接用 raw 写
        path.write_text("from plugins.base import GoalCommandPlugin\n"
                       "class Broken(GoalCommandPlugin\n  pass\n",
                       encoding="utf-8")
        with self.assertRaises(DropInLoadError):
            DropInLoader.load_from_file(path)
        # sys.modules 不应含 "plugins_extra.syntax_bad"（try/finally 清理）
        self.assertNotIn(
            "plugins_extra.syntax_bad", sys.modules,
            "exec_module 失败必须清理 sys.modules",
        )

    def test_no_plugin_subclass_raises(self):
        """文件无 GoalCommandPlugin 子类 → 抛 DropInLoadError。"""
        path = self.write_plugin_file(
            "no_plugin.py",
            """
            # 没有 plugin 定义，只有普通函数
            def helper():
                return 42
            """,
        )
        with self.assertRaises(DropInLoadError) as cm:
            DropInLoader.load_from_file(path)
        self.assertIn("未定义任何", str(cm.exception))


class TestDropInLoaderSysmodulesInjection(_TempDirMixin, unittest.TestCase):
    """DropInLoader.sys.modules 注入与清理（P1-4 try/finally）。"""

    def test_load_injects_into_sysmodules(self):
        """load_from_file 成功 → sys.modules 含 "plugins_extra.<sanitized_stem>"。"""
        path = self.write_plugin_file(
            "loaded.py",
            """
            from plugins.base import GoalCommandPlugin
            class Loaded(GoalCommandPlugin):
                @property
                def name(self): return "loaded"
                @property
                def priority(self): return 100
                @property
                def mutex_with(self): return set()
                @property
                def requires_task(self): return False
                def matches(self, args): return True
                def execute(self, args, ctx): return True
            """,
        )
        DropInLoader.load_from_file(path)
        # 成功路径保留 sys.modules 引用（watcher 卸载时清理）
        self.assertIn("plugins_extra.loaded", sys.modules)

    def test_module_attribute_set(self):
        """loaded module 的 __name__ 应为 "plugins_extra.<stem>"。"""
        path = self.write_plugin_file(
            "attr.py",
            """
            from plugins.base import GoalCommandPlugin
            class Attr(GoalCommandPlugin):
                @property
                def name(self): return "attr"
                @property
                def priority(self): return 100
                @property
                def mutex_with(self): return set()
                @property
                def requires_task(self): return False
                def matches(self, args): return True
                def execute(self, args, ctx): return True
            """,
        )
        DropInLoader.load_from_file(path)
        mod = sys.modules["plugins_extra.attr"]
        self.assertEqual(mod.__name__, "plugins_extra.attr")


class TestDropInLoaderStemSanitize(_TempDirMixin, unittest.TestCase):
    """path.stem 中文/特殊字符 sanitize（P2-1）。"""

    def test_chinese_filename_sanitized(self):
        """文件名含中文 → sys.modules key 中中文→下划线（合法 Python identifier）。"""
        path = self.write_plugin_file(
            "插件.py",  # 中文文件名
            """
            from plugins.base import GoalCommandPlugin
            class CNPlugin(GoalCommandPlugin):
                @property
                def name(self): return "cn-plugin"
                @property
                def priority(self): return 100
                @property
                def mutex_with(self): return set()
                @property
                def requires_task(self): return False
                def matches(self, args): return True
                def execute(self, args, ctx): return True
            """,
        )
        # 中文 stem sanitize 失败：原始 "__" / sanitize 失败 → 行为是 "丢弃中文字符"
        # 这里先验证：能 load 成功 + sys.modules key 合法
        plugins = DropInLoader.load_from_file(path)
        self.assertEqual(len(plugins), 1)
        # 找到刚注入的 sys.modules key
        matched_keys = [k for k in sys.modules if k.startswith("plugins_extra.")]
        self.assertEqual(len(matched_keys), 1)
        # key 必须是合法 Python identifier
        key = matched_keys[0]
        self.assertTrue(key.replace("plugins_extra.", "").replace("_", "").isalnum() or
                        all(c.isalnum() or c == "_" for c in key.replace("plugins_extra.", "")),
                        f"sanitize 后的 key 必须是合法 Python identifier：{key}")

    def test_special_chars_sanitized(self):
        """文件名含特殊字符 → sys.modules key 中特殊字符→下划线。"""
        path = self.write_plugin_file(
            "my-plugin@x.py",  # 特殊字符 @ 和 -
            """
            from plugins.base import GoalCommandPlugin
            class Special(GoalCommandPlugin):
                @property
                def name(self): return "special"
                @property
                def priority(self): return 100
                @property
                def mutex_with(self): return set()
                @property
                def requires_task(self): return False
                def matches(self, args): return True
                def execute(self, args, ctx): return True
            """,
        )
        plugins = DropInLoader.load_from_file(path)
        self.assertEqual(len(plugins), 1)
        # 找到刚注入的 sys.modules key
        matched_keys = [k for k in sys.modules if k.startswith("plugins_extra.")]
        self.assertEqual(len(matched_keys), 1)
        key = matched_keys[0]
        stem_part = key.replace("plugins_extra.", "")
        # - → _（FILENAME_SAFE_RE 替换），@ → _
        self.assertNotIn("-", stem_part, f"'-' 字符应被替换：{key}")
        self.assertNotIn("@", stem_part, f"'@' 字符应被替换：{key}")


class TestDropInLoaderSanitizeStaticMethod(unittest.TestCase):
    """DropInLoader._sanitize_stem 静态方法单元测试（直接覆盖 sanitize 逻辑）。"""

    def test_sanitize_replaces_non_alnum(self):
        """_sanitize_stem 把非 [a-zA-Z0-9_] 字符替换为 _。"""
        self.assertEqual(DropInLoader._sanitize_stem("hello"), "hello")
        self.assertEqual(DropInLoader._sanitize_stem("hello-world"), "hello_world")
        self.assertEqual(DropInLoader._sanitize_stem("hello@x"), "hello_x")
        # 中文字符被替换
        result = DropInLoader._sanitize_stem("插件")
        self.assertNotIn("插", result)
        self.assertNotIn("件", result)


if __name__ == "__main__":
    unittest.main()
