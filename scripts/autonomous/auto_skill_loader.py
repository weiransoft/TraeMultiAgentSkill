"""Ralph 风格自动 skill 加载。

设计目标：
- 扫描 .trae/skills/ 和 plugins_extra/ 目录
- 解析 skill manifest（YAML / JSON）
- 不修改 dispatcher（仅"提示"，避免污染 V3 行为）
- 按 task 关键词过滤相关 skills
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


@dataclass
class SkillManifest:
    """自动加载的 skill manifest。

    字段说明：
    - name: skill 名称（kebab-case）
    - path: 完整路径
    - description: 简短描述
    - triggers: 触发关键词列表
    - priority: 优先级（数字越小越优先）
    - version: 版本字符串
    - author: 作者
    - requires: 依赖列表
    """

    name: str
    path: Path
    description: str = ""
    triggers: List[str] = field(default_factory=list)
    priority: int = 100
    version: str = "0.0.0"
    author: str = ""
    requires: List[str] = field(default_factory=list)


class AutoSkillLoader:
    """Ralph 风格的自动 skill 加载。

    设计原则：
    1. 不修改 dispatcher（V3 插件不感知 auto-loaded skills）
    2. 仅做"提示"：将 detected skills 写入 PluginContext.extra
    3. dispatcher 的智能体调用时可在 prompt 中看到这些 skills
    """

    # 支持的 manifest 文件后缀
    _MANIFEST_SUFFIXES = (".json", ".yaml", ".yml")

    def __init__(
        self,
        project_root: Path,
        extra_dirs: Optional[List[Path]] = None,
    ):
        """构造 AutoSkillLoader。

        Args:
            project_root: 项目根目录（扫描 .trae/skills/）
            extra_dirs: 额外扫描目录（默认 + plugins_extra/）
        """
        self._project_root = Path(project_root).resolve()
        # 默认扫描目录
        self._scan_dirs: List[Path] = [
            self._project_root / ".trae" / "skills",
            self._project_root / "plugins_extra",
        ]
        if extra_dirs:
            self._scan_dirs.extend(Path(d) for d in extra_dirs)
        # 缓存
        self._cache: List[SkillManifest] = []
        self._cache_dirty = True

    # ------------------------------------------------------------------ #
    # 公共 API                                                            #
    # ------------------------------------------------------------------ #

    def detect(self) -> List[SkillManifest]:
        """扫描所有配置的目录，检测可用 skills。

        Returns:
            List[SkillManifest]: 检测到的 skills（按 priority 升序）
        """
        results: List[SkillManifest] = []
        seen_names: Set[str] = set()
        for scan_dir in self._scan_dirs:
            if not scan_dir.exists() or not scan_dir.is_dir():
                continue
            # 查找 manifest 文件
            for manifest_path in self._iter_manifests(scan_dir):
                manifest = self._parse_manifest(manifest_path)
                if manifest is None:
                    continue
                # 去重（同名 skill 取先到先得）
                if manifest.name in seen_names:
                    continue
                seen_names.add(manifest.name)
                results.append(manifest)
        # 按 priority 升序排序
        results.sort(key=lambda m: (m.priority, m.name))
        self._cache = results
        self._cache_dirty = False
        return results

    def detect_for_task(self, task: str) -> List[SkillManifest]:
        """根据 task 描述过滤相关 skills。

        Args:
            task: 任务描述

        Returns:
            List[SkillManifest]: 与 task 相关的 skills
        """
        if not task or not task.strip():
            return []
        all_skills = self.detect() if self._cache_dirty else self._cache
        # 关键词提取：中文按字符 + 英文按单词
        task_lower = task.lower()
        # 拆词
        task_tokens = self._tokenize(task_lower)
        scored: List[tuple] = []
        for skill in all_skills:
            # 统计 triggers 与 task 的交集
            intersect = 0
            for trigger in skill.triggers:
                trigger_lower = trigger.lower()
                if trigger_lower in task_lower:
                    intersect += 1
                else:
                    # 按 token 重叠
                    trigger_tokens = self._tokenize(trigger_lower)
                    if task_tokens & trigger_tokens:
                        intersect += 1
            if intersect > 0:
                # (priority 升序, -intersect_size 降序) → priority 越小越优先，交集越多越优先
                scored.append((skill.priority, -intersect, skill))
        scored.sort()
        return [s for _, _, s in scored]

    def format_for_prompt(self, skills: List[SkillManifest]) -> str:
        """格式化为可注入 prompt 的字符串。

        Args:
            skills: skills 列表

        Returns:
            str: 多行 markdown 列表
        """
        if not skills:
            return ""
        lines = ["## Available Auto-Loaded Skills"]
        for s in skills:
            triggers = ", ".join(s.triggers) if s.triggers else "(no triggers)"
            desc = s.description or "(no description)"
            lines.append(f"- **{s.name}** (priority={s.priority}, v{s.version}): {desc}")
            lines.append(f"  - Triggers: {triggers}")
            lines.append(f"  - Path: {s.path}")
        return "\n".join(lines)

    def invalidate_cache(self) -> None:
        """手动失效缓存（重新扫描时使用）。"""
        self._cache_dirty = True

    # ------------------------------------------------------------------ #
    # 内部辅助                                                            #
    # ------------------------------------------------------------------ #

    def _iter_manifests(self, scan_dir: Path):
        """遍历目录下所有 manifest 文件。"""
        for p in scan_dir.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() in self._MANIFEST_SUFFIXES:
                # 过滤 SKILL.md 等非 manifest 文件
                if p.name.lower() in ("skill.md", "readme.md", "changelog.md"):
                    continue
                yield p

    def _parse_manifest(self, manifest_path: Path) -> Optional[SkillManifest]:
        """解析单个 manifest 文件。

        Args:
            manifest_path: manifest 路径

        Returns:
            SkillManifest: 解析成功；None = 失败
        """
        try:
            content = manifest_path.read_text(encoding="utf-8")
        except OSError:
            return None
        data: Optional[Dict[str, Any]] = None
        if manifest_path.suffix.lower() == ".json":
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                return None
        else:
            # YAML 解析（极简实现，仅支持 key: value 列表形式）
            data = self._parse_simple_yaml(content)
        if not isinstance(data, dict):
            return None
        # 必需字段
        name = data.get("name")
        if not name or not isinstance(name, str):
            # 用文件名作为 name
            name = manifest_path.stem
        return SkillManifest(
            name=name,
            path=manifest_path,
            description=str(data.get("description", "")),
            triggers=list(data.get("triggers", [])) if isinstance(data.get("triggers"), list) else [],
            priority=int(data.get("priority", 100)),
            version=str(data.get("version", "0.0.0")),
            author=str(data.get("author", "")),
            requires=list(data.get("requires", [])) if isinstance(data.get("requires"), list) else [],
        )

    @staticmethod
    def _parse_simple_yaml(content: str) -> Dict[str, Any]:
        """极简 YAML 解析（仅支持一级 key: value，不支持嵌套）。

        支持格式：
            name: foo
            description: bar
            triggers:
              - aaa
              - bbb
            priority: 10
        """
        result: Dict[str, Any] = {}
        lines = content.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                i += 1
                continue
            # 匹配 "key: value" 或 "key:" 单独行
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", stripped)
            if not m:
                i += 1
                continue
            key = m.group(1)
            value = m.group(2).strip()
            if not value:
                # 可能是列表或多行值
                list_items: List[str] = []
                j = i + 1
                while j < len(lines):
                    next_line = lines[j]
                    next_stripped = next_line.strip()
                    if not next_stripped or next_stripped.startswith("#"):
                        j += 1
                        continue
                    if next_stripped.startswith("- "):
                        list_items.append(next_stripped[2:].strip())
                        j += 1
                        continue
                    # 不是列表项 → 退出
                    break
                if list_items:
                    result[key] = list_items
                else:
                    result[key] = ""
                i = j
            else:
                # 去掉可能的引号
                if (value.startswith('"') and value.endswith('"')) or (
                    value.startswith("'") and value.endswith("'")
                ):
                    value = value[1:-1]
                result[key] = value
                i += 1
        return result

    @staticmethod
    def _tokenize(text: str) -> Set[str]:
        """拆词：中文按字 + 英文按词。"""
        if not text:
            return set()
        # 英文按非字母数字拆分
        english_words = set(re.findall(r"[a-z0-9]+", text))
        # 中文按字符拆分（仅保留 >= 2 的连续片段）
        chinese_chars = set(re.findall(r"[\u4e00-\u9fff]+", text))
        tokens: Set[str] = set()
        tokens.update(english_words)
        for c in chinese_chars:
            # 中文按 2-gram 拆分
            for k in range(len(c) - 1):
                tokens.add(c[k : k + 2])
        return tokens


__all__ = ["SkillManifest", "AutoSkillLoader"]
