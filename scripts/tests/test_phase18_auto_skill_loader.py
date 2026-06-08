"""Phase 18: AutoSkillLoader 单元测试。

测试 AutoSkillLoader 的全部行为：
- detect() / detect_for_task() / format_for_prompt()
- JSON / YAML 解析
- 优先级排序
- 关键词匹配
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from autonomous.auto_skill_loader import AutoSkillLoader, SkillManifest


def _write_manifest(parent: Path, name: str, **kwargs) -> Path:
    """写入一个 skill manifest 文件。"""
    parent.mkdir(parents=True, exist_ok=True)
    manifest_path = parent / f"{name}.json"
    manifest_path.write_text(
        json.dumps({"name": name, **kwargs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest_path


class TestAutoSkillLoaderDetect(unittest.TestCase):
    """测试 detect() 行为。"""

    def setUp(self):
        """创建临时项目根，含 .trae/skills/ 和 plugins_extra/。"""
        self.tmpdir = Path(tempfile.mkdtemp())
        self.skills_dir = self.tmpdir / ".trae" / "skills"
        self.plugins_dir = self.tmpdir / "plugins_extra"
        self.loader = AutoSkillLoader(project_root=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_detect_empty(self):
        """无 skills 目录时返回空列表。"""
        result = self.loader.detect()
        self.assertEqual(result, [])

    def test_02_detect_single_skill(self):
        """检测单个 skill。"""
        _write_manifest(self.skills_dir, "my-skill", description="test")
        result = self.loader.detect()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, "my-skill")

    def test_03_detect_sorted_by_priority(self):
        """检测多个 skills 按 priority 升序。"""
        _write_manifest(self.skills_dir, "low", priority=100)
        _write_manifest(self.skills_dir, "high", priority=10)
        _write_manifest(self.skills_dir, "mid", priority=50)
        result = self.loader.detect()
        self.assertEqual(len(result), 3)
        # 第一个应是 priority 最小的
        self.assertEqual(result[0].name, "high")
        self.assertEqual(result[2].name, "low")

    def test_04_detect_dedup_by_name(self):
        """同名 skill 去重（先到先得）。"""
        _write_manifest(self.skills_dir, "dup", description="first")
        _write_manifest(self.plugins_dir, "dup", description="second")
        result = self.loader.detect()
        # 只保留第一个找到的
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].description, "first")

    def test_05_detect_invalid_json_skipped(self):
        """损坏的 manifest 被跳过。"""
        # 写入坏 JSON
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        (self.skills_dir / "bad.json").write_text("not json", encoding="utf-8")
        _write_manifest(self.skills_dir, "good")
        result = self.loader.detect()
        # 坏文件被跳过
        names = [s.name for s in result]
        self.assertIn("good", names)
        self.assertNotIn("bad", names)


class TestAutoSkillLoaderDetectForTask(unittest.TestCase):
    """测试 detect_for_task() 关键词匹配。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.skills_dir = self.tmpdir / ".trae" / "skills"
        self.loader = AutoSkillLoader(project_root=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_06_detect_for_task_empty_task(self):
        """空 task 返回空列表。"""
        _write_manifest(self.skills_dir, "s1")
        result = self.loader.detect_for_task("")
        self.assertEqual(result, [])

    def test_07_detect_for_task_keyword_match(self):
        """task 含 trigger 关键词时匹配。"""
        _write_manifest(self.skills_dir, "translation", triggers=["翻译", "translate"])
        _write_manifest(self.skills_dir, "review", triggers=["review"])
        result = self.loader.detect_for_task("请帮我翻译这段话")
        # 至少 translation 应匹配
        names = [s.name for s in result]
        self.assertIn("translation", names)

    def test_08_detect_for_task_no_match(self):
        """task 不含任何 trigger 时返回空。"""
        _write_manifest(self.skills_dir, "s1", triggers=["keyword1"])
        result = self.loader.detect_for_task("完全不相关的内容")
        self.assertEqual(result, [])


class TestAutoSkillLoaderFormat(unittest.TestCase):
    """测试 format_for_prompt() 行为。"""

    def test_09_format_empty(self):
        """空列表返回空字符串。"""
        loader = AutoSkillLoader(project_root=Path(tempfile.mkdtemp()))
        result = loader.format_for_prompt([])
        self.assertEqual(result, "")

    def test_10_format_includes_metadata(self):
        """format 输出包含 name/description/triggers。"""
        loader = AutoSkillLoader(project_root=Path(tempfile.mkdtemp()))
        skills = [
            SkillManifest(
                name="test-skill",
                path=Path("/tmp/skill.json"),
                description="A test skill",
                triggers=["t1", "t2"],
                priority=10,
                version="1.0.0",
            )
        ]
        result = loader.format_for_prompt(skills)
        self.assertIn("test-skill", result)
        self.assertIn("A test skill", result)
        self.assertIn("t1", result)
        self.assertIn("t2", result)


class TestAutoSkillLoaderYAML(unittest.TestCase):
    """测试 YAML manifest 解析。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.skills_dir = self.tmpdir / ".trae" / "skills"
        self.loader = AutoSkillLoader(project_root=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_11_parse_simple_yaml(self):
        """解析简单 YAML manifest。"""
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        yaml_content = """
name: yaml-skill
description: YAML test skill
priority: 20
version: 2.0.0
"""
        (self.skills_dir / "yaml-skill.yaml").write_text(yaml_content, encoding="utf-8")
        result = self.loader.detect()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, "yaml-skill")
        self.assertEqual(result[0].description, "YAML test skill")
        self.assertEqual(result[0].priority, 20)
        self.assertEqual(result[0].version, "2.0.0")

    def test_12_parse_yaml_list(self):
        """解析 YAML 中的列表字段（triggers）。"""
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        yaml_content = """name: list-skill
description: Has list
triggers:
  - trigger1
  - trigger2
  - trigger3
"""
        (self.skills_dir / "list-skill.yml").write_text(yaml_content, encoding="utf-8")
        result = self.loader.detect()
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0].triggers), 3)
        self.assertIn("trigger1", result[0].triggers)


class TestAutoSkillLoaderExtraDirs(unittest.TestCase):
    """测试 extra_dirs 自定义扫描目录。"""

    def test_13_extra_dirs_scanned(self):
        """额外目录中的 manifest 被扫描。"""
        tmpdir = Path(tempfile.mkdtemp())
        try:
            extra = tmpdir / "my_extra"
            _write_manifest(extra, "extra-skill", description="from extra")
            loader = AutoSkillLoader(project_root=tmpdir, extra_dirs=[extra])
            result = loader.detect()
            names = [s.name for s in result]
            self.assertIn("extra-skill", names)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
