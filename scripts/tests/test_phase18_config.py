"""Phase 18: AutonomousConfig / SimpleYAMLParser / load_config 单元测试。

测试 config_loader.py 的全部行为：
- AutonomousConfig 默认值
- SimpleYAMLParser 解析（inline / list / dict / 标量）
- load_config 加载用户级 + 项目级配置（项目级覆盖用户级）
- 未知字段落入 extra
- 类型强制（int / float / bool / list / str）
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from autonomous.config_loader import (
    AutonomousConfig,
    DEFAULT_CONFIG,
    SimpleYAMLParser,
    load_config,
)


# ---------------------------------------------------------------------- #
# TestAutonomousConfigDefaults: 默认值                                   #
# ---------------------------------------------------------------------- #


class TestAutonomousConfigDefaults(unittest.TestCase):
    """测试 AutonomousConfig 默认值。"""

    def test_01_default_values(self):
        """默认值符合预期。"""
        c = AutonomousConfig()
        self.assertEqual(c.max_iterations, 50)
        # max_tokens 默认 0（表示不限制）
        self.assertEqual(c.max_tokens, 0)
        self.assertEqual(c.test_command, "python3 -m unittest discover -s tests -p 'test_*.py'")
        self.assertTrue(c.auto_commit)
        self.assertTrue(c.sleep_guard_enabled)
        self.assertEqual(c.confirm_mode, "smart")
        self.assertEqual(c.extra, {})

    def test_02_to_dict(self):
        """to_dict() 返回可序列化 dict。"""
        c = AutonomousConfig()
        d = c.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["max_iterations"], 50)
        self.assertIn("extra", d)

    def test_03_default_config_singleton(self):
        """DEFAULT_CONFIG 是单一实例。"""
        self.assertIsInstance(DEFAULT_CONFIG, AutonomousConfig)


# ---------------------------------------------------------------------- #
# TestSimpleYAMLParser: 解析                                              #
# ---------------------------------------------------------------------- #


class TestSimpleYAMLParser(unittest.TestCase):
    """测试 SimpleYAMLParser。"""

    def setUp(self):
        self.parser = SimpleYAMLParser()

    def test_04_parse_inline(self):
        """解析 inline key: value。"""
        text = "name: foo\nage: 30\nactive: true\n"
        result = self.parser.parse(text)
        self.assertEqual(result["name"], "foo")
        self.assertEqual(result["age"], 30)
        self.assertEqual(result["active"], True)

    def test_05_parse_list(self):
        """解析 list 字段。"""
        text = """items:
  - aaa
  - bbb
  - ccc
"""
        result = self.parser.parse(text)
        self.assertEqual(result["items"], ["aaa", "bbb", "ccc"])

    def test_06_parse_nested_dict(self):
        """解析嵌套 dict。"""
        text = """parent:
  child1: v1
  child2: v2
"""
        result = self.parser.parse(text)
        self.assertIn("parent", result)
        self.assertEqual(result["parent"]["child1"], "v1")
        self.assertEqual(result["parent"]["child2"], "v2")

    def test_07_parse_scalars(self):
        """解析各种标量类型。"""
        text = """a: 42
b: 3.14
c: true
d: false
e: null
f: "quoted"
"""
        result = self.parser.parse(text)
        self.assertEqual(result["a"], 42)
        self.assertEqual(result["b"], 3.14)
        self.assertEqual(result["c"], True)
        self.assertEqual(result["d"], False)
        self.assertIsNone(result["e"])
        self.assertEqual(result["f"], "quoted")

    def test_08_parse_with_comments(self):
        """解析带行首注释的 YAML（极简解析器不支持行尾注释剥离）。"""
        text = """# 注释
name: foo
# 另一个注释
age: 30
"""
        result = self.parser.parse(text)
        # 极简解析器：行首 # 注释被剥离，但行尾 # 不剥离
        self.assertEqual(result["name"], "foo")
        self.assertEqual(result["age"], 30)

    def test_09_parse_empty(self):
        """空文本返回空 dict。"""
        result = self.parser.parse("")
        self.assertEqual(result, {})

    def test_10_parse_only_comments(self):
        """仅注释返回空 dict。"""
        result = self.parser.parse("# 仅注释\n# 还是注释\n")
        self.assertEqual(result, {})


# ---------------------------------------------------------------------- #
# TestLoadConfig: 加载配置                                               #
# ---------------------------------------------------------------------- #


class TestLoadConfig(unittest.TestCase):
    """测试 load_config()。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_project_config(self, content: str) -> None:
        """写入项目级 .trae/autonomous.yml。"""
        cfg_dir = self.tmpdir / ".trae"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "autonomous.yml").write_text(content, encoding="utf-8")

    def _write_user_config(self, content: str) -> Path:
        """写入用户级配置（指定路径）。"""
        user_path = self.tmpdir / "user_config.yml"
        user_path.write_text(content, encoding="utf-8")
        return user_path

    def test_11_no_config_returns_default(self):
        """无任何配置文件 → 默认值。"""
        cfg = load_config(self.tmpdir)
        self.assertEqual(cfg.max_iterations, 50)

    def test_12_project_config_loads(self):
        """仅项目级配置 → 项目级值。"""
        self._write_project_config("max_iterations: 10\n")
        cfg = load_config(self.tmpdir)
        self.assertEqual(cfg.max_iterations, 10)

    def test_13_project_overrides_user(self):
        """项目级覆盖用户级。"""
        self._write_user_config("max_iterations: 100\n")
        self._write_project_config("max_iterations: 10\n")
        cfg = load_config(self.tmpdir, user_config_path=self.tmpdir / "user_config.yml")
        # 项目级优先
        self.assertEqual(cfg.max_iterations, 10)

    def test_14_user_only_fallback(self):
        """仅用户级配置生效。"""
        user_path = self._write_user_config("max_iterations: 77\n")
        cfg = load_config(self.tmpdir, user_config_path=user_path)
        self.assertEqual(cfg.max_iterations, 77)

    def test_15_unknown_field_goes_to_extra(self):
        """未知字段落入 extra。"""
        self._write_project_config("my_custom_field: hello\n")
        cfg = load_config(self.tmpdir)
        self.assertEqual(cfg.extra.get("my_custom_field"), "hello")

    def test_16_type_coercion_int(self):
        """int 类型强制。"""
        self._write_project_config("max_iterations: '42'\n")
        cfg = load_config(self.tmpdir)
        self.assertEqual(cfg.max_iterations, 42)
        self.assertIsInstance(cfg.max_iterations, int)

    def test_17_type_coercion_bool(self):
        """bool 类型强制。"""
        self._write_project_config("auto_commit: 'true'\n")
        cfg = load_config(self.tmpdir)
        self.assertTrue(cfg.auto_commit)
        self.assertIsInstance(cfg.auto_commit, bool)

    def test_18_type_coercion_list(self):
        """list 类型强制。"""
        self._write_project_config("stage_order: 'plan,dev,fix'\n")
        cfg = load_config(self.tmpdir)
        # CSV 字符串被拆为 list
        self.assertEqual(cfg.stage_order, ["plan", "dev", "fix"])

    def test_19_complex_yaml_loads(self):
        """复杂 YAML 配置加载。"""
        self._write_project_config("""
max_iterations: 20
max_tokens: 100000
stage_order:
  - plan
  - dev
  - verify
  - fix
test_command: pytest
auto_commit: true
sleep_guard_enabled: false
confirm_mode: whitelist-only
""")
        cfg = load_config(self.tmpdir)
        self.assertEqual(cfg.max_iterations, 20)
        self.assertEqual(cfg.max_tokens, 100_000)
        self.assertEqual(cfg.stage_order, ["plan", "dev", "verify", "fix"])
        self.assertEqual(cfg.test_command, "pytest")
        self.assertTrue(cfg.auto_commit)
        self.assertFalse(cfg.sleep_guard_enabled)
        self.assertEqual(cfg.confirm_mode, "whitelist-only")


if __name__ == "__main__":
    unittest.main()
