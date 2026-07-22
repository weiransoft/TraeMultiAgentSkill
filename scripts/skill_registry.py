#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
技能注册中心

提供技能的注册、发现、管理和版本控制功能
"""

import os
import json
import yaml
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime
from dataclasses import dataclass, asdict, field
from enum import Enum


class SkillStatus(Enum):
    """技能状态"""
    ACTIVE = "active"
    INACTIVE = "inactive"
    DEPRECATED = "deprecated"


@dataclass
class SkillCapability:
    """技能能力描述"""
    name: str
    description: str
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillManifest:
    """技能清单"""
    name: str
    version: str
    description: str
    author: str
    status: str = "active"
    capabilities: List[SkillCapability] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())


class SkillRegistry:
    """
    技能注册中心
    
    功能：
    - 技能注册和注销
    - 技能发现和查询
    - 版本管理
    - 依赖管理
    """
    
    def __init__(self, registry_path: str = "."):
        """
        初始化技能注册中心
        
        Args:
            registry_path: 注册中心路径
        """
        self.registry_path = Path(registry_path) / "registry"
        self.registry_path.mkdir(parents=True, exist_ok=True)
        
        # 技能注册表
        self.skills: Dict[str, SkillManifest] = {}
        
        # 加载现有技能
        self._load()
    
    def _load(self):
        """从磁盘加载技能注册表"""
        registry_file = self.registry_path / "skills.json"
        if registry_file.exists():
            try:
                with open(registry_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                for skill_name, skill_data in data.get('skills', {}).items():
                    # 恢复 capabilities
                    capabilities = []
                    for cap_data in skill_data.get('capabilities', []):
                        capabilities.append(SkillCapability(**cap_data))
                    
                    skill = SkillManifest(
                        name=skill_data['name'],
                        version=skill_data['version'],
                        description=skill_data['description'],
                        author=skill_data['author'],
                        status=skill_data.get('status', 'active'),
                        capabilities=capabilities,
                        dependencies=skill_data.get('dependencies', []),
                        metadata=skill_data.get('metadata', {}),
                        created_at=skill_data.get('created_at'),
                        updated_at=skill_data.get('updated_at')
                    )
                    self.skills[skill_name] = skill
                
            except Exception as e:
                print(f"加载技能注册表失败：{e}")
    
    def _save(self):
        """保存到磁盘"""
        registry_file = self.registry_path / "skills.json"
        
        data = {
            'version': '1.0',
            'updated_at': datetime.now().isoformat(),
            'skills': {}
        }
        
        for skill_name, skill in self.skills.items():
            skill_dict = asdict(skill)
            # 转换 capabilities
            skill_dict['capabilities'] = [asdict(cap) for cap in skill.capabilities]
            data['skills'][skill_name] = skill_dict
        
        try:
            with open(registry_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存技能注册表失败：{e}")
    
    def register(self, manifest: SkillManifest) -> bool:
        """
        注册技能
        
        Args:
            manifest: 技能清单
            
        Returns:
            bool: 是否注册成功
        """
        # 检查是否已存在
        if manifest.name in self.skills:
            existing = self.skills[manifest.name]
            # 检查版本
            if self._compare_versions(manifest.version, existing.version) <= 0:
                print(f"⚠️  技能 {manifest.name} 已存在，当前版本：{existing.version}")
                return False
        
        # 注册技能
        self.skills[manifest.name] = manifest
        self._save()
        
        print(f"✅ 技能已注册：{manifest.name} v{manifest.version}")
        return True
    
    def unregister(self, skill_name: str) -> bool:
        """
        注销技能
        
        Args:
            skill_name: 技能名称
            
        Returns:
            bool: 是否注销成功
        """
        if skill_name not in self.skills:
            print(f"❌ 技能不存在：{skill_name}")
            return False
        
        del self.skills[skill_name]
        self._save()
        
        print(f"✅ 技能已注销：{skill_name}")
        return True
    
    def get_skill(self, skill_name: str) -> Optional[SkillManifest]:
        """
        获取技能信息
        
        Args:
            skill_name: 技能名称
            
        Returns:
            Optional[SkillManifest]: 技能清单
        """
        return self.skills.get(skill_name)
    
    def list_skills(self, status: str = None) -> List[SkillManifest]:
        """
        列出所有技能
        
        Args:
            status: 过滤状态（active, inactive, deprecated）
            
        Returns:
            List[SkillManifest]: 技能列表
        """
        skills = list(self.skills.values())
        
        if status:
            skills = [s for s in skills if s.status == status]
        
        return skills
    
    def search_skills(self, keywords: List[str]) -> List[SkillManifest]:
        """
        搜索技能
        
        Args:
            keywords: 关键词列表
            
        Returns:
            List[SkillManifest]: 匹配的技能列表
        """
        results = []
        
        for skill in self.skills.values():
            # 在名称、描述、能力中搜索关键词
            match = False
            text = f"{skill.name} {skill.description}"
            
            for capability in skill.capabilities:
                text += f" {capability.name} {capability.description}"
            
            for keyword in keywords:
                if keyword in text:
                    match = True
                    break
            
            if match:
                results.append(skill)
        
        return results
    
    def update_status(self, skill_name: str, status: str) -> bool:
        """
        更新技能状态
        
        Args:
            skill_name: 技能名称
            status: 新状态
            
        Returns:
            bool: 是否更新成功
        """
        if skill_name not in self.skills:
            print(f"❌ 技能不存在：{skill_name}")
            return False
        
        self.skills[skill_name].status = status
        self.skills[skill_name].updated_at = datetime.now().isoformat()
        self._save()
        
        print(f"✅ 技能状态已更新：{skill_name} -> {status}")
        return True
    
    def check_dependencies(self, skill_name: str) -> Dict[str, Any]:
        """
        检查技能依赖
        
        Args:
            skill_name: 技能名称
            
        Returns:
            Dict: 依赖检查结果
        """
        skill = self.skills.get(skill_name)
        if not skill:
            return {'satisfied': False, 'missing': [], 'message': '技能不存在'}
        
        missing = []
        for dependency in skill.dependencies:
            if dependency not in self.skills:
                missing.append(dependency)
        
        return {
            'satisfied': len(missing) == 0,
            'missing': missing,
            'message': '依赖满足' if len(missing) == 0 else f'缺少依赖：{missing}'
        }
    
    def _compare_versions(self, v1: str, v2: str) -> int:
        """比较版本号"""
        parts1 = [int(x) for x in v1.split('.')]
        parts2 = [int(x) for x in v2.split('.')]
        
        for i in range(max(len(parts1), len(parts2))):
            p1 = parts1[i] if i < len(parts1) else 0
            p2 = parts2[i] if i < len(parts2) else 0
            
            if p1 > p2:
                return 1
            elif p1 < p2:
                return -1
        
        return 0
    
    def export_manifest(self, skill_name: str, output_path: str) -> bool:
        """
        导出技能清单到 YAML 文件
        
        Args:
            skill_name: 技能名称
            output_path: 输出路径
            
        Returns:
            bool: 是否导出成功
        """
        skill = self.skills.get(skill_name)
        if not skill:
            print(f"❌ 技能不存在：{skill_name}")
            return False
        
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                yaml.dump(asdict(skill), f, allow_unicode=True, default_flow_style=False)
            
            print(f"✅ 技能清单已导出：{output_path}")
            return True
        
        except Exception as e:
            print(f"导出技能清单失败：{e}")
            return False
    
    def import_manifest(self, manifest_path: str) -> bool:
        """
        从 YAML 文件导入技能清单
        
        Args:
            manifest_path: 清单文件路径
            
        Returns:
            bool: 是否导入成功
        """
        try:
            with open(manifest_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            
            # 创建技能清单
            capabilities = []
            for cap_data in data.get('capabilities', []):
                capabilities.append(SkillCapability(**cap_data))
            
            manifest = SkillManifest(
                name=data['name'],
                version=data['version'],
                description=data['description'],
                author=data['author'],
                status=data.get('status', 'active'),
                capabilities=capabilities,
                dependencies=data.get('dependencies', []),
                metadata=data.get('metadata', {})
            )
            
            # 注册技能
            return self.register(manifest)
        
        except Exception as e:
            print(f"导入技能清单失败：{e}")
            return False
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        skills = list(self.skills.values())
        
        return {
            'total_skills': len(skills),
            'active_skills': len([s for s in skills if s.status == 'active']),
            'inactive_skills': len([s for s in skills if s.status == 'inactive']),
            'deprecated_skills': len([s for s in skills if s.status == 'deprecated']),
            'total_capabilities': sum(len(s.capabilities) for s in skills)
        }


def main():
    """示例用法"""
    # 创建技能注册中心
    registry = SkillRegistry(registry_path=".")
    
    # 注册技能示例
    manifest = SkillManifest(
        name="trae-multi-agent",
        version="1.0.0",
        description="多智能体协作技能",
        author="Trae Team",
        status="active",
        capabilities=[
            SkillCapability(
                name="product-manager",
                description="产品经理角色，负责需求分析和 PRD 编写",
                input_schema={"type": "object", "properties": {"requirements": {"type": "string"}}},
                output_schema={"type": "object", "properties": {"prd": {"type": "object"}}}
            ),
            SkillCapability(
                name="architect",
                description="架构师角色，负责系统架构设计",
                input_schema={"type": "object", "properties": {"requirements": {"type": "object"}}},
                output_schema={"type": "object", "properties": {"architecture": {"type": "object"}}}
            ),
            SkillCapability(
                name="solo-coder",
                description="独立开发者角色，负责代码实现",
                input_schema={"type": "object", "properties": {"design": {"type": "object"}}},
                output_schema={"type": "object", "properties": {"code": {"type": "string"}}}
            ),
            SkillCapability(
                name="test-expert",
                description="测试专家角色，负责测试用例设计和执行",
                input_schema={"type": "object", "properties": {"requirements": {"type": "object"}}},
                output_schema={"type": "object", "properties": {"test_results": {"type": "array"}}}
            )
        ],
        dependencies=[],
        metadata={
            "category": "productivity",
            "tags": ["ai", "agent", "collaboration"]
        }
    )
    
    registry.register(manifest)
    
    # 列出所有技能
    skills = registry.list_skills()
    print(f"\n📋 已注册技能：{len(skills)}")
    for skill in skills:
        print(f"  - {skill.name} v{skill.version} ({skill.status})")
    
    # 搜索技能
    results = registry.search_skills(["agent", "协作"])
    print(f"\n🔍 搜索结果：{len(results)}")
    for result in results:
        print(f"  - {result.name}")
    
    # 显示统计
    stats = registry.get_statistics()
    print(f"\n📊 统计信息:")
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
