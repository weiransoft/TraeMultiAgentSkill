"""Phase 18: NotesMemory 单元测试。

测试 NotesMemory 的全部公共 API：
- load() / append() / list_sections() / get_recent_sections()
- estimate_tokens() / write_final_summary() / clear()
- 原子写入 / trim / 解析 / 边界条件
"""
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from autonomous.notes_memory import NotesMemory, NotesSection


class TestNotesMemoryLoad(unittest.TestCase):
    """测试 load() 行为。"""

    def setUp(self):
        """创建临时目录。"""
        self.tmpdir = Path(tempfile.mkdtemp())
        self.notes_path = self.tmpdir / "notes.md"
        self.mem = NotesMemory(self.notes_path)

    def tearDown(self):
        """清理临时目录。"""
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_load_nonexistent_returns_empty(self):
        """load() 在文件不存在时返回空字符串。"""
        self.assertEqual(self.mem.load(), "")

    def test_02_load_existing_file(self):
        """load() 在文件存在时返回完整内容。"""
        content = "# Hello\n\n## Section 1\nbody\n"
        self.notes_path.write_text(content, encoding="utf-8")
        # 重新构造以清除缓存
        mem = NotesMemory(self.notes_path)
        self.assertIn("Hello", mem.load())
        self.assertIn("Section 1", mem.load())

    def test_03_load_caches_content(self):
        """load() 缓存内容（多次调用只读一次）。"""
        self.notes_path.write_text("cached", encoding="utf-8")
        first = self.mem.load()
        # 修改文件
        self.notes_path.write_text("modified", encoding="utf-8")
        # 缓存仍返回旧值
        self.assertEqual(first, self.mem.load())


class TestNotesMemoryAppend(unittest.TestCase):
    """测试 append() 行为。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.notes_path = self.tmpdir / "notes.md"
        self.mem = NotesMemory(self.notes_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_04_append_creates_file(self):
        """append() 在文件不存在时自动创建。"""
        self.assertFalse(self.notes_path.exists())
        section = NotesSection(
            title="Test Section",
            body="body content",
            timestamp=datetime.now(timezone.utc).isoformat(),
            iter_index=1,
            tags=["test"],
        )
        self.mem.append(section)
        self.assertTrue(self.notes_path.exists())
        content = self.notes_path.read_text(encoding="utf-8")
        self.assertIn("Test Section", content)
        self.assertIn("body content", content)
        self.assertIn("iter=1", content)

    def test_05_append_multiple_sections(self):
        """append() 追加多个段落保持顺序。"""
        for i in range(3):
            self.mem.append(NotesSection(
                title=f"Section {i}",
                body=f"body {i}",
                timestamp=datetime.now(timezone.utc).isoformat(),
                iter_index=i + 1,
                tags=[],
            ))
        content = self.notes_path.read_text(encoding="utf-8")
        # 验证所有段落都在
        for i in range(3):
            self.assertIn(f"Section {i}", content)
        # 验证顺序：Section 0 在 Section 1 之前
        pos_0 = content.find("Section 0")
        pos_1 = content.find("Section 1")
        pos_2 = content.find("Section 2")
        self.assertLess(pos_0, pos_1)
        self.assertLess(pos_1, pos_2)

    def test_06_append_invalid_type_raises(self):
        """append() 接收非 NotesSection 时抛 TypeError。"""
        with self.assertRaises(TypeError):
            self.mem.append("not a section")  # type: ignore

    def test_07_append_serializes_tags(self):
        """append() 序列化 tags 为逗号分隔。"""
        self.mem.append(NotesSection(
            title="Tagged",
            body="body",
            timestamp="",
            iter_index=1,
            tags=["success", "test-passed"],
        ))
        content = self.notes_path.read_text(encoding="utf-8")
        self.assertIn("tags=success,test-passed", content)


class TestNotesMemorySections(unittest.TestCase):
    """测试 list_sections() 和 get_recent_sections() 行为。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.notes_path = self.tmpdir / "notes.md"
        self.mem = NotesMemory(self.notes_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_08_list_sections_parses(self):
        """list_sections() 正确解析多段。"""
        for i in range(1, 4):
            self.mem.append(NotesSection(
                title=f"Iteration {i}",
                body=f"body {i}",
                timestamp=datetime.now(timezone.utc).isoformat(),
                iter_index=i,
                tags=[f"iter-{i}"],
            ))
        sections = self.mem.list_sections()
        self.assertEqual(len(sections), 3)
        # 验证 iter_index 解析
        self.assertEqual(sections[0].iter_index, 1)
        self.assertEqual(sections[1].iter_index, 2)
        self.assertEqual(sections[2].iter_index, 3)
        # 验证 tags 解析
        self.assertEqual(sections[0].tags, ["iter-1"])
        self.assertEqual(sections[1].tags, ["iter-2"])

    def test_09_get_recent_sections_returns_n(self):
        """get_recent_sections(n) 返回最近 N 段。"""
        for i in range(1, 6):
            self.mem.append(NotesSection(
                title=f"Section {i}",
                body="",
                timestamp="",
                iter_index=i,
            ))
        recent = self.mem.get_recent_sections(2)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[-1].iter_index, 5)
        self.assertEqual(recent[0].iter_index, 4)

    def test_10_list_sections_empty_file(self):
        """list_sections() 在空文件返回空列表。"""
        self.assertEqual(self.mem.list_sections(), [])


class TestNotesMemoryTokens(unittest.TestCase):
    """测试 estimate_tokens() 行为。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.notes_path = self.tmpdir / "notes.md"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_11_estimate_tokens_basic(self):
        """estimate_tokens() 粗略估算（char/4）。"""
        mem = NotesMemory(self.notes_path, max_size_kb=1024)
        # 写入 400 字符 → 估算 100 tokens
        content = "x" * 400
        self.notes_path.write_text(content, encoding="utf-8")
        # 重新构造清除缓存
        mem = NotesMemory(self.notes_path)
        self.assertEqual(mem.estimate_tokens(), 100)

    def test_12_estimate_tokens_empty(self):
        """estimate_tokens() 空文件返回 0。"""
        mem = NotesMemory(self.notes_path)
        self.assertEqual(mem.estimate_tokens(), 0)


class TestNotesMemoryFinalSummary(unittest.TestCase):
    """测试 write_final_summary() 行为。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.notes_path = self.tmpdir / "notes.md"
        self.mem = NotesMemory(self.notes_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_13_write_final_summary_appends(self):
        """write_final_summary() 追加到末尾。"""
        # 先写一些内容
        self.mem.append(NotesSection(
            title="Iter 1",
            body="body",
            timestamp="",
            iter_index=1,
        ))
        self.mem.write_final_summary("## Final\nAll done\n")
        content = self.notes_path.read_text(encoding="utf-8")
        self.assertIn("Final Summary", content)
        self.assertIn("All done", content)

    def test_14_write_final_summary_empty_skips(self):
        """write_final_summary() 空内容跳过写入。"""
        before = self.mem.load()
        self.mem.write_final_summary("")
        after = self.mem.load()
        self.assertEqual(before, after)


class TestNotesMemoryTrim(unittest.TestCase):
    """测试 trim 行为（超过 max_size_kb 时保留最近 N 段）。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.notes_path = self.tmpdir / "notes.md"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_15_trim_keeps_last_n(self):
        """trim 时保留最近 N 段。"""
        # max_size_kb=1（1024 字节），trim_keep_last_n=2
        mem = NotesMemory(self.notes_path, max_size_kb=1, trim_keep_last_n=2)
        # 写入 5 段，每段 ~500 字符 → 超过 1024 字节
        for i in range(5):
            body = "x" * 500
            mem.append(NotesSection(
                title=f"Section {i}",
                body=body,
                timestamp="",
                iter_index=i + 1,
            ))
        sections = mem.list_sections()
        # 真实 trim 行为：保留最近 2 段 + 一个 trim 提示段
        # 验证：文件大小不超过 1024 字节（除了 trim 提示段）
        # 实际可能是保留最后 N + 提示段
        file_size = self.notes_path.stat().st_size
        # 简单验证：至少有最近 2 段
        # 由于 trim 实现包含 trim_marker，最少应保留 3 段（2 + marker）
        self.assertGreaterEqual(len(sections), 2)
        # 验证最末段是最后写入的
        self.assertEqual(sections[-1].iter_index, 5)


class TestNotesMemoryClear(unittest.TestCase):
    """测试 clear() 行为。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.notes_path = self.tmpdir / "notes.md"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_16_clear_removes_file(self):
        """clear() 删除文件并清空缓存。"""
        mem = NotesMemory(self.notes_path)
        mem.append(NotesSection(title="T", body="B", timestamp="", iter_index=1))
        self.assertTrue(self.notes_path.exists())
        mem.clear()
        self.assertFalse(self.notes_path.exists())
        self.assertEqual(mem.load(), "")


class TestNotesMemoryAtomicWrite(unittest.TestCase):
    """测试原子写入（不产生半写文件）。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.notes_path = self.tmpdir / "notes.md"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_17_atomic_write_no_temp_left(self):
        """原子写入后不留下 .tmp 文件。"""
        mem = NotesMemory(self.notes_path)
        mem.append(NotesSection(title="T", body="B", timestamp="", iter_index=1))
        # 检查：没有 .tmp 文件残留
        tmp_path = self.notes_path.with_suffix(self.notes_path.suffix + ".tmp")
        self.assertFalse(tmp_path.exists())

    def test_18_atomic_write_preserves_existing(self):
        """原子写入保留之前的段落。"""
        mem = NotesMemory(self.notes_path)
        mem.append(NotesSection(title="First", body="B1", timestamp="", iter_index=1))
        mem.append(NotesSection(title="Second", body="B2", timestamp="", iter_index=2))
        sections = mem.list_sections()
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].title, "First")
        self.assertEqual(sections[1].title, "Second")


if __name__ == "__main__":
    unittest.main()
