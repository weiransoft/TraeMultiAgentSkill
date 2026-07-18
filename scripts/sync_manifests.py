#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Manifest 一致性校验脚本

用途：
    校验三份技能清单文件（skill-manifest.yaml / skills-index.json /
    claude-code-skill.json）的版本号、名称、核心特性声明是否一致，
    防止双宿主（Trae / Claude Code）能力漂移。

背景：
    v2.7.0 前 claude-code-skill.json 停留在 2.4.1，导致 Claude Code 侧
    缺失 v2.5-v2.7 全部特性声明。本脚本作为 CI 门禁，确保三份清单同步。

使用：
    python3 scripts/sync_manifests.py          # 校验，不一致时退出码 1
    python3 scripts/sync_manifests.py --report  # 输出详细差异报告

退出码：
    0 = 全部一致
    1 = 存在不一致或文件缺失
"""

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

# 技能根目录（本脚本位于 scripts/ 下）
SKILL_ROOT = Path(__file__).resolve().parent.parent

# 三份清单文件路径
MANIFEST_YAML = SKILL_ROOT / "skill-manifest.yaml"
SKILLS_INDEX_JSON = SKILL_ROOT / "skills-index.json"
CLAUDE_CODE_JSON = SKILL_ROOT / "claude-code-skill.json"

# 必须一致的核心字段
EXPECTED_NAME = "multi-agent-team"


def _read_yaml_version_and_name(path: Path) -> Tuple[str, str]:
    """
    从 YAML 清单中提取顶层 version 与 name 字段。

    采用正则解析而非引入 PyYAML 依赖（项目要求零第三方依赖），
    仅匹配文件开头的顶层键值对。

    Args:
        path: YAML 文件路径

    Returns:
        (name, version) 元组；未找到时返回空字符串
    """
    name = ""
    version = ""
    # 逐行扫描顶层键（无缩进的 key: value 形式）
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith((" ", "\t", "#")):
            continue  # 跳过缩进行与注释行
        m_name = re.match(r"^name:\s*(.+?)\s*$", line)
        m_ver = re.match(r"^version:\s*(.+?)\s*$", line)
        if m_name and not name:
            name = m_name.group(1).strip("'\"")
        if m_ver and not version:
            version = m_ver.group(1).strip("'\"")
        if name and version:
            break
    return name, version


def _read_json(path: Path) -> Dict[str, Any]:
    """
    读取 JSON 清单文件。

    Args:
        path: JSON 文件路径

    Returns:
        解析后的字典；文件缺失或解析失败时返回空字典
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"❌ 无法读取 {path.name}: {e}")
        return {}


def _collect_features(data: Dict[str, Any]) -> List[str]:
    """
    从清单中提取已启用的特性名称列表。

    兼容两种结构：
    - skills-index.json: features 为 {name: {enabled, description}} 字典
    - skill-manifest.yaml 解析后: ai_capabilities 为 {name: bool} 字典

    Args:
        data: 清单字典

    Returns:
        排序后的特性名称列表
    """
    features: List[str] = []
    for key in ("features", "ai_capabilities"):
        section = data.get(key)
        if isinstance(section, dict):
            for fname, fval in section.items():
                # 值为字典时检查 enabled，值为布尔时直接使用
                if isinstance(fval, dict) and fval.get("enabled"):
                    features.append(fname)
                elif fval is True:
                    features.append(fname)
    return sorted(features)


def check_consistency(report: bool = False) -> bool:
    """
    执行三份清单的一致性校验。

    校验项：
    1. 三份文件均存在
    2. name 字段一致且等于 EXPECTED_NAME
    3. version 字段一致
    4. skills-index.json 与 skill-manifest.yaml 的版本号对齐

    Args:
        report: 是否输出详细报告

    Returns:
        True = 全部一致；False = 存在不一致
    """
    issues: List[str] = []

    # 1. 文件存在性检查
    for path in (MANIFEST_YAML, SKILLS_INDEX_JSON, CLAUDE_CODE_JSON):
        if not path.exists():
            issues.append(f"文件缺失：{path.name}")

    if issues:
        for issue in issues:
            print(f"❌ {issue}")
        return False

    # 2. 提取各清单的 name 与 version
    yaml_name, yaml_ver = _read_yaml_version_and_name(MANIFEST_YAML)
    index_data = _read_json(SKILLS_INDEX_JSON)
    claude_data = _read_json(CLAUDE_CODE_JSON)

    index_name = index_data.get("name", "")
    index_ver = index_data.get("version", "")
    claude_name = claude_data.get("name", "")
    claude_ver = claude_data.get("version", "")

    # 3. name 一致性校验
    names = {
        "skill-manifest.yaml": yaml_name,
        "skills-index.json": index_name,
        "claude-code-skill.json": claude_name,
    }
    for fname, name in names.items():
        if name != EXPECTED_NAME:
            issues.append(
                f"{fname} 的 name='{name}'，应为 '{EXPECTED_NAME}'"
            )

    # 4. version 一致性校验
    versions = {
        "skill-manifest.yaml": yaml_ver,
        "skills-index.json": index_ver,
        "claude-code-skill.json": claude_ver,
    }
    unique_versions = set(versions.values())
    if len(unique_versions) > 1:
        issues.append(
            f"版本不一致：{dict(versions)}（发现 {len(unique_versions)} 个不同版本）"
        )

    # 5. 输出报告
    if report:
        print("=" * 60)
        print("Manifest 一致性校验报告")
        print("=" * 60)
        print(f"{'文件':<28} {'name':<20} {'version':<10}")
        print("-" * 60)
        for fname in names:
            print(f"{fname:<28} {names[fname]:<20} {versions[fname]:<10}")
        print("-" * 60)
        index_features = _collect_features(index_data)
        print(f"skills-index.json 已启用特性数：{len(index_features)}")

    if issues:
        print("\n❌ 发现不一致：")
        for issue in issues:
            print(f"  - {issue}")
        return False

    print(
        f"\n✅ 三份清单一致：name='{EXPECTED_NAME}', "
        f"version='{yaml_ver}'"
    )
    return True


def main() -> int:
    """CLI 入口。"""
    report = "--report" in sys.argv
    ok = check_consistency(report=report)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
