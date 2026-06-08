"""跨轮 notes.md 记忆。

设计目标：
- 标准 markdown 格式，LLM 可直接消费
- 原子写入：先 .tmp，fsync 后 rename（避免半写）
- 段落式：每轮一个 section，标题含 iter_index
- token 估算：粗略按 char/4 估算（不依赖 tiktoken）
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


@dataclass
class NotesSection:
    """单个 notes 段落。

    字段说明：
    - title: 段标题（如 "## Iteration 5: Fix typo in parser"）
    - body: 段内容（markdown）
    - timestamp: ISO 8601 时间戳
    - iter_index: 所属迭代索引
    - tags: 标签列表（如 ["success", "test-passed"]）
    """

    title: str
    body: str
    timestamp: str
    iter_index: int
    tags: List[str] = field(default_factory=list)


class NotesMemory:
    """跨轮 notes.md 记忆。

    设计原则：
    1. 文件格式：标准 markdown，LLM 可直接消费
    2. 原子写入：先写 .tmp，fsync 后 rename（避免半写）
    3. 段落式：每轮一个 section，标题含 iter_index
    4. token 估算：粗略按 char/4 估算（不依赖 tiktoken）
    """

    # markdown 段标题正则：以 "## " 开头的行
    _SECTION_HEADER_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
    # 段元数据注释行：<!-- iter=N tags=tag1,tag2 -->
    _META_COMMENT_RE = re.compile(
        r"^<!--\s*iter=(\d+)(?:\s+tags=([^\s>]+))?\s*-->\s*$", re.MULTILINE
    )

    def __init__(
        self,
        notes_path: Path,
        max_size_kb: int = 1024,
        trim_keep_last_n: int = 20,
    ):
        """构造 NotesMemory。

        Args:
            notes_path: notes.md 完整路径
            max_size_kb: 最大文件大小（KB），超过则 trim
            trim_keep_last_n: trim 时保留最近 N 个段落
        """
        self._path = Path(notes_path)
        self._max_size_bytes = max(1, max_size_kb) * 1024
        self._trim_keep_last_n = max(1, trim_keep_last_n)
        # 缓存，避免重复读盘
        self._cached_content: Optional[str] = None
        self._cache_dirty: bool = True

    # ------------------------------------------------------------------ #
    # 公共 API                                                            #
    # ------------------------------------------------------------------ #

    def load(self) -> str:
        """加载完整 notes.md。

        Returns:
            str: 完整 markdown 内容（文件不存在返回空字符串）
        """
        if not self._cache_dirty and self._cached_content is not None:
            return self._cached_content
        if not self._path.exists():
            self._cached_content = ""
            self._cache_dirty = False
            return ""
        # 真实读取 UTF-8（不模拟）
        content = self._path.read_text(encoding="utf-8")
        self._cached_content = content
        self._cache_dirty = False
        return content

    def append(self, section: NotesSection) -> None:
        """追加一个段落。

        Args:
            section: 段落对象
        """
        if not isinstance(section, NotesSection):
            raise TypeError(f"section 必须是 NotesSection 实例，实际: {type(section)}")

        # 加载现有内容（用于 trim 决策）
        current = self.load()
        # 序列化新段落
        new_chunk = self._serialize_section(section)
        merged = current + new_chunk if current else new_chunk
        # 写回（_atomic_write 内部会判断是否需要 trim）
        self._atomic_write(merged)

    def list_sections(self) -> List[NotesSection]:
        """解析 notes.md 为段落列表。

        Returns:
            List[NotesSection]: 按文件顺序排列的段落
        """
        content = self.load()
        if not content.strip():
            return []
        sections: List[NotesSection] = []
        # 找到所有 "## " 标题的位置
        matches = list(self._SECTION_HEADER_RE.finditer(content))
        if not matches:
            return []
        for i, m in enumerate(matches):
            title_line = m.group(1).strip()
            # 段起始 = 标题行起始
            start = m.start()
            # 段结束 = 下一个标题起始 或 文件结尾
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            chunk = content[start:end]
            # 解析元数据注释行
            meta = self._META_COMMENT_RE.search(chunk)
            if meta:
                iter_index = int(meta.group(1))
                tags_str = meta.group(2) or ""
                tags = [t for t in tags_str.split(",") if t]
                # body = 注释行之后到下一个 ## 之前
                after_meta = chunk[meta.end():]
                body = after_meta.lstrip("\n").rstrip()
            else:
                # 没有元数据 → 推断 iter_index 为列表位置 + 1
                iter_index = i + 1
                tags = []
                body = chunk[len(f"## {title_line}"):].lstrip("\n").rstrip()
            sections.append(
                NotesSection(
                    title=title_line,
                    body=body,
                    timestamp="",
                    iter_index=iter_index,
                    tags=tags,
                )
            )
        return sections

    def get_recent_sections(self, n: int = 5) -> List[NotesSection]:
        """获取最近 N 个段落。

        Args:
            n: 取最近 N 个
        """
        all_sections = self.list_sections()
        if n <= 0:
            return []
        return all_sections[-n:]

    def estimate_tokens(self) -> int:
        """粗略 token 估算（char/4 启发式）。

        Returns:
            int: 估算 token 数
        """
        return len(self.load()) // 4

    def write_final_summary(self, summary: str) -> None:
        """写入最终总结（追加到末尾）。

        Args:
            summary: 总结内容
        """
        if not summary:
            return
        section = NotesSection(
            title="## Final Summary",
            body=summary.strip(),
            timestamp=datetime.now(timezone.utc).isoformat(),
            iter_index=0,
            tags=["final"],
        )
        self.append(section)

    def clear(self) -> None:
        """清空 notes.md（仅用于测试或显式重置）。"""
        if self._path.exists():
            self._path.unlink()
        self._cached_content = ""
        self._cache_dirty = False

    # ------------------------------------------------------------------ #
    # 内部辅助                                                            #
    # ------------------------------------------------------------------ #

    def _serialize_section(self, section: NotesSection) -> str:
        """序列化单个段落为 markdown 字符串。

        格式：
            ## <title>
            <!-- iter=<N> tags=t1,t2 -->
            <body>
        """
        ts = section.timestamp or datetime.now(timezone.utc).isoformat()
        tags_part = f" tags={','.join(section.tags)}" if section.tags else ""
        meta_line = f"<!-- iter={section.iter_index}{tags_part} -->"
        body = section.body.rstrip()
        # 段之间保留一个空行
        return f"## {section.title}\n{meta_line}\n\n{body}\n\n"

    def _atomic_write(self, content: str) -> None:
        """原子写入（先 .tmp，fsync，rename）。

        Args:
            content: 完整 markdown 内容
        """
        # 确保父目录存在
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # 检查是否需要 trim
        content = self._trim_content(content)
        # 写 .tmp
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            # 强制 fsync（确保数据落盘后再 rename）
            os.fsync(f.fileno())
        # 原子 rename（跨平台）
        os.replace(tmp_path, self._path)
        # 更新缓存
        self._cached_content = content
        self._cache_dirty = False

    def _trim_content(self, content: str) -> str:
        """检查并 trim（超过 max_size_kb 时保留最近 N 个段落）。"""
        encoded_size = len(content.encode("utf-8"))
        if encoded_size <= self._max_size_bytes:
            return content

        # 解析所有段，保留最近 N 段
        sections = self._split_into_raw_sections(content)
        if len(sections) <= self._trim_keep_last_n:
            return content  # 段数太少，无法 trim

        keep = sections[-self._trim_keep_last_n:]
        # 加一个 trim 提示段
        trim_marker = (
            f"## _trimmed_at_{datetime.now(timezone.utc).isoformat()}\n"
            f"<!-- iter=0 tags=trimmed -->\n\n"
            f"_Earlier {len(sections) - self._trim_keep_last_n} sections "
            f"were trimmed to stay under max_size_kb={self._max_size_bytes // 1024}._\n\n"
        )
        return trim_marker + "".join(keep)

    def _split_into_raw_sections(self, content: str) -> List[str]:
        """将完整 markdown 拆分为原始段字符串列表。"""
        matches = list(self._SECTION_HEADER_RE.finditer(content))
        if not matches:
            return [content]
        result: List[str] = []
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            result.append(content[start:end])
        return result


__all__ = ["NotesSection", "NotesMemory"]
