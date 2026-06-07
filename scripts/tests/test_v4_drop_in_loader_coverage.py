"""drop_in_loader.py 覆盖度补全测试（RED→GREEN 阶段）。

针对覆盖率分析中 dispatcher/drop_in_loader.py 的缺失行（82% → 目标 100%）：
- 114-115: spec_from_file_location 抛 OSError/ValueError
- 145: 路径存在但 spec 返回 None（loader=None）
- 166-169: 文件无任何 GoalCommandPlugin 子类
- 177-179: plugin 类构造失败

TDD 流程：
1. RED：写测试 → 验证失败或覆盖目标行
2. GREEN：验证全部通过
"""
import importlib
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

# 路径设置
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from dispatcher.drop_in_loader import DropInLoader
from dispatcher.errors import DropInLoadError


class _TempDirMixin:
    """提供临时目录 + 清理的 mixin。"""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="loader_cov_")
        self.tmp_path = Path(self._tmp)

    def tearDown(self) -> None:
        for key in list(sys.modules.keys()):
            if key.startswith("plugins_extra."):
                del sys.modules[key]
        shutil.rmtree(self._tmp, ignore_errors=True)

    def write_file(self, filename: str, content: str) -> Path:
        """写入文件。"""
        file_path = self.tmp_path / filename
        file_path.write_text(content, encoding="utf-8")
        return file_path


class TestLoaderSpecFromFileLocationFailure(_TempDirMixin, unittest.TestCase):
    """spec_from_file_location 抛异常 → DropInLoadError（line 114-115）。"""

    def test_spec_oserror_wrapped_in_dropinloaderror(self):
        """importlib.util.spec_from_file_location 抛 OSError → DropInLoadError。"""
        path = self.write_file(
            "dummy.py",
            "from plugins.base import GoalCommandPlugin\n"
            "class P(GoalCommandPlugin):\n"
            "    @property\n    def name(self): return 'p'\n"
            "    @property\n    def priority(self): return 100\n"
            "    @property\n    def mutex_with(self): return set()\n"
            "    @property\n    def requires_task(self): return False\n"
            "    def matches(self, args): return True\n"
            "    def execute(self, args, ctx): return True\n",
        )

        # mock importlib.util.spec_from_file_location 抛 OSError
        import importlib.util
        original = importlib.util.spec_from_file_location
        try:
            def fake_spec(name, location, *args, **kwargs):
                raise OSError("mock: filesystem error")

            importlib.util.spec_from_file_location = fake_spec
            with self.assertRaises(DropInLoadError) as cm:
                DropInLoader.load_from_file(path)
            self.assertIn("spec_from_file_location 失败", str(cm.exception))
        finally:
            importlib.util.spec_from_file_location = original

    def test_spec_valueerror_wrapped_in_dropinloaderror(self):
        """importlib.util.spec_from_file_location 抛 ValueError → DropInLoadError。"""
        path = self.write_file(
            "dummy2.py",
            "from plugins.base import GoalCommandPlugin\n"
            "class Q(GoalCommandPlugin):\n"
            "    @property\n    def name(self): return 'q'\n"
            "    @property\n    def priority(self): return 100\n"
            "    @property\n    def mutex_with(self): return set()\n"
            "    @property\n    def requires_task(self): return False\n"
            "    def matches(self, args): return True\n"
            "    def execute(self, args, ctx): return True\n",
        )

        import importlib.util
        original = importlib.util.spec_from_file_location
        try:
            def fake_spec(name, location, *args, **kwargs):
                raise ValueError("mock: invalid name")

            importlib.util.spec_from_file_location = fake_spec
            with self.assertRaises(DropInLoadError) as cm:
                DropInLoader.load_from_file(path)
            self.assertIn("spec_from_file_location 失败", str(cm.exception))
        finally:
            importlib.util.spec_from_file_location = original


class TestLoaderSpecReturnsNone(_TempDirMixin, unittest.TestCase):
    """spec_from_file_location 返回 None → DropInLoadError（line 145）。"""

    def test_spec_none_raises_dropinloaderror(self):
        """spec_from_file_location 返回 None → 抛 DropInLoadError。"""
        path = self.write_file(
            "none_spec.py",
            "from plugins.base import GoalCommandPlugin\n"
            "class R(GoalCommandPlugin):\n"
            "    @property\n    def name(self): return 'r'\n"
            "    @property\n    def priority(self): return 100\n"
            "    @property\n    def mutex_with(self): return set()\n"
            "    @property\n    def requires_task(self): return False\n"
            "    def matches(self, args): return True\n"
            "    def execute(self, args, ctx): return True\n",
        )

        import importlib.util
        original = importlib.util.spec_from_file_location
        try:
            def fake_spec(name, location, *args, **kwargs):
                return None  # spec 解析失败

            importlib.util.spec_from_file_location = fake_spec
            with self.assertRaises(DropInLoadError) as cm:
                DropInLoader.load_from_file(path)
            # 错误消息应含"返回 None"
            self.assertIn("返回 None", str(cm.exception))
        finally:
            importlib.util.spec_from_file_location = original


class TestLoaderNoPluginSubclass(_TempDirMixin, unittest.TestCase):
    """文件无 GoalCommandPlugin 子类 → DropInLoadError（line 166-169）。"""

    def test_file_with_only_non_plugin_classes_raises(self):
        """文件只有非 plugin 类（如继承自 object）→ 抛 DropInLoadError。"""
        path = self.write_file(
            "no_plugin.py",
            "class PlainClass:\n    pass\n\n"
            "class AnotherClass(object):\n    pass\n",
        )
        with self.assertRaises(DropInLoadError) as cm:
            DropInLoader.load_from_file(path)
        self.assertIn("未定义任何", str(cm.exception))

    def test_file_with_abstract_plugin_raises(self):
        """文件只有未实现的抽象 plugin 类 → 抛 DropInLoadError（被 isabstract 过滤）。"""
        path = self.write_file(
            "abstract_only.py",
            "from plugins.base import GoalCommandPlugin\n"
            "from abc import abstractmethod\n"
            "class AbstractPlugin(GoalCommandPlugin):\n"
            "    @abstractmethod\n    def execute(self, args, ctx):\n        pass\n",
        )
        # AbstractPlugin 是 abstract 子类，不应被实例化
        # 加载时因为没有具体 plugin 子类，应抛 DropInLoadError
        with self.assertRaises(DropInLoadError) as cm:
            DropInLoader.load_from_file(path)
        self.assertIn("未定义任何", str(cm.exception))

    def test_file_with_abstract_and_concrete_keeps_concrete(self):
        """文件含 1 个 abstract + 1 个 concrete → 只保留 concrete。"""
        path = self.write_file(
            "mixed.py",
            "from plugins.base import GoalCommandPlugin\n"
            "from abc import abstractmethod\n"
            "class AbstractP(GoalCommandPlugin):\n"
            "    @abstractmethod\n    def execute(self, args, ctx):\n        pass\n"
            "class ConcreteP(GoalCommandPlugin):\n"
            "    @property\n    def name(self): return 'concrete'\n"
            "    @property\n    def priority(self): return 100\n"
            "    @property\n    def mutex_with(self): return set()\n"
            "    @property\n    def requires_task(self): return False\n"
            "    def matches(self, args): return True\n"
            "    def execute(self, args, ctx): return True\n",
        )
        plugins = DropInLoader.load_from_file(path)
        names = [p.name for p in plugins]
        self.assertEqual(names, ["concrete"])


class TestLoaderConstructionFailure(_TempDirMixin, unittest.TestCase):
    """plugin 类构造失败 → DropInLoadError（line 177-179）。"""

    def test_plugin_constructor_raises_during_load(self):
        """plugin.__init__ 抛异常 → DropInLoadError。"""
        path = self.write_file(
            "broken_ctor.py",
            "from plugins.base import GoalCommandPlugin\n"
            "class BrokenCtor(GoalCommandPlugin):\n"
            "    def __init__(self):\n"
            "        raise RuntimeError('mock: ctor failure')\n"
            "    @property\n    def name(self): return 'broken'\n"
            "    @property\n    def priority(self): return 100\n"
            "    @property\n    def mutex_with(self): return set()\n"
            "    @property\n    def requires_task(self): return False\n"
            "    def matches(self, args): return True\n"
            "    def execute(self, args, ctx): return True\n",
        )
        with self.assertRaises(DropInLoadError) as cm:
            DropInLoader.load_from_file(path)
        self.assertIn("构造失败", str(cm.exception))


class TestLoaderDuplicateNames(_TempDirMixin, unittest.TestCase):
    """文件含重复 plugin name → DropInLoadError。"""

    def test_two_classes_with_same_name_raises(self):
        """两个 plugin 类返回相同 name → 抛 DropInLoadError。"""
        path = self.write_file(
            "dup_name.py",
            "from plugins.base import GoalCommandPlugin\n"
            "class First(GoalCommandPlugin):\n"
            "    @property\n    def name(self): return 'dup'\n"
            "    @property\n    def priority(self): return 100\n"
            "    @property\n    def mutex_with(self): return set()\n"
            "    @property\n    def requires_task(self): return False\n"
            "    def matches(self, args): return True\n"
            "    def execute(self, args, ctx): return True\n"
            "class Second(GoalCommandPlugin):\n"
            "    @property\n    def name(self): return 'dup'\n"
            "    @property\n    def priority(self): return 200\n"
            "    @property\n    def mutex_with(self): return set()\n"
            "    @property\n    def requires_task(self): return False\n"
            "    def matches(self, args): return True\n"
            "    def execute(self, args, ctx): return True\n",
        )
        with self.assertRaises(DropInLoadError) as cm:
            DropInLoader.load_from_file(path)
        self.assertIn("重复 plugin name", str(cm.exception))
        self.assertIn("dup", str(cm.exception))


class TestLoaderModuleNameSanitize(_TempDirMixin, unittest.TestCase):
    """_sanitize_stem 边界条件。"""

    def test_filename_starting_with_digit_sanitized(self):
        """文件名以数字开头（不合法 Python identifier）→ sanitize 加下划线。"""
        path = self.write_file(
            "123_numeric.py",
            "from plugins.base import GoalCommandPlugin\n"
            "class Num(GoalCommandPlugin):\n"
            "    @property\n    def name(self): return 'num'\n"
            "    @property\n    def priority(self): return 100\n"
            "    @property\n    def mutex_with(self): return set()\n"
            "    @property\n    def requires_task(self): return False\n"
            "    def matches(self, args): return True\n"
            "    def execute(self, args, ctx): return True\n",
        )
        # 加载应成功（sanitize 修复 identifier 合法性）
        plugins = DropInLoader.load_from_file(path)
        self.assertEqual(len(plugins), 1)
        # 验证：sanitize 后的 stem 必须是合法 Python identifier
        stem = DropInLoader._sanitize_stem(path.stem)
        self.assertTrue(stem.isidentifier(), f"sanitize 后不是合法 identifier: {stem}")

    def test_empty_after_sanitize_fallback(self):
        """全部字符被替换（如纯特殊字符）→ 应有 fallback。"""
        # 全部非字母数字字符
        result = DropInLoader._sanitize_stem("$$$")
        # 应替换为 _（或 fallback）
        self.assertTrue(result.isidentifier() or result == "_")


if __name__ == "__main__":
    unittest.main()
