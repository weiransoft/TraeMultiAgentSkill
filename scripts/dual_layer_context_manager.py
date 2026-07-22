#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双层动态上下文管理器

基于双层动态上下文工程架构：
- 上层：全局上下文层（长期记忆）
- 下层：任务上下文层（工作记忆）
- 动态同步器：两层之间实时联动
- 上下文构建器：按需构建完整上下文

参考：https://mp.weixin.qq.com/s/Jw9Rr-0t7MNF_NJJybidIQ
"""

import os
import json
import hashlib
from pathlib import Path
from typing import Dict, List, Any, Optional, Set
from datetime import datetime
from dataclasses import dataclass, asdict, field
from enum import Enum
import copy


class ContextLevel(Enum):
    """上下文层级"""
    GLOBAL = "global"  # 全局上下文（长期记忆）
    TASK = "task"      # 任务上下文（工作记忆）


class SyncDirection(Enum):
    """同步方向"""
    TASK_TO_GLOBAL = "task_to_global"  # 任务经验沉淀到全局
    GLOBAL_TO_TASK = "global_to_task"  # 全局知识注入到任务


@dataclass
class UserProfile:
    """用户画像"""
    user_id: str
    identity: str = ""  # 身份（如：架构师、开发者）
    preferences: Dict[str, Any] = field(default_factory=dict)  # 偏好
    habits: Dict[str, Any] = field(default_factory=dict)  # 习惯
    expertise: List[str] = field(default_factory=list)  # 专业领域
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class KnowledgeItem:
    """知识项"""
    id: str
    category: str  # 类别（如：技术栈、设计模式、最佳实践）
    title: str
    content: Dict[str, Any]
    tags: List[str] = field(default_factory=list)
    source: str = ""  # 来源（任务 ID 或手动添加）
    confidence: float = 1.0  # 置信度
    usage_count: int = 0  # 使用次数
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ExperienceItem:
    """经验项"""
    id: str
    task_id: str
    task_type: str
    success: bool  # 成功或失败
    description: str
    lessons_learned: List[str] = field(default_factory=list)  # 经验教训
    best_practices: List[str] = field(default_factory=list)  # 最佳实践
    patterns: List[str] = field(default_factory=list)  # 模式
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class CollaborationRecord:
    """协作记录"""
    id: str
    participants: List[str]  # 参与者（角色 ID）
    task_id: str
    collaboration_type: str
    effectiveness: float  # 协作效果评分 (0-1)
    notes: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class CapabilityModel:
    """能力模型"""
    entity_id: str  # 用户或角色 ID
    entity_type: str  # "user" 或 "role"
    capabilities: Dict[str, float] = field(default_factory=dict)  # 能力评分
    strengths: List[str] = field(default_factory=list)  # 优势
    weaknesses: List[str] = field(default_factory=list)  # 弱点
    improvements: List[str] = field(default_factory=list)  # 改进建议
    evaluated_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class TaskDefinition:
    """任务定义"""
    task_id: str
    title: str
    description: str
    goals: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    requirements: List[str] = field(default_factory=list)
    priority: str = "medium"  # high, medium, low
    estimated_effort: str = ""  # 预估工作量
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class TaskStatus:
    """任务状态"""
    task_id: str
    status: str = "pending"  # pending, in_progress, completed, failed
    progress: float = 0.0  # 进度 (0-1)
    current_step: str = ""
    risks: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ThoughtRecord:
    """思考记录"""
    id: str
    task_id: str
    role: str
    thought_type: str  # analysis, decision, question, insight
    content: str
    context: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


class ContextSynchronizer:
    """
    上下文同步器
    
    负责全局上下文层和任务上下文层之间的双向同步
    """
    
    def __init__(self):
        """初始化同步器"""
        self.sync_history: List[Dict[str, Any]] = []
    
    def sync_task_to_global(self, global_ctx: 'GlobalContext', 
                           task_ctx: 'TaskContext',
                           task_id: str) -> Dict[str, Any]:
        """
        任务完成时，将经验沉淀到全局上下文
        
        Args:
            global_ctx: 全局上下文
            task_ctx: 任务上下文
            task_id: 任务 ID
            
        Returns:
            Dict: 同步结果
        """
        sync_result = {
            'direction': SyncDirection.TASK_TO_GLOBAL.value,
            'task_id': task_id,
            'timestamp': datetime.now().isoformat(),
            'updates': []
        }
        
        # 1. 提取成功经验或失败教训
        task_status = task_ctx.get_status()
        if task_status and task_status.status == 'completed':
            experience = self._extract_experience(task_ctx)
            if experience:
                global_ctx.add_experience(experience)
                sync_result['updates'].append({
                    'type': 'experience',
                    'id': experience.id,
                    'action': 'added'
                })
        
        # 2. 提取新知识
        artifacts = task_ctx.get_artifacts()
        for artifact_type, artifact_data in artifacts.items():
            knowledge = self._extract_knowledge(artifact_type, artifact_data, task_id)
            if knowledge:
                global_ctx.add_knowledge(knowledge)
                sync_result['updates'].append({
                    'type': 'knowledge',
                    'id': knowledge.id,
                    'action': 'added'
                })
        
        # 3. 更新用户画像（如果有新的偏好或习惯）
        user_preferences = task_ctx.get_user_preferences()
        if user_preferences:
            global_ctx.update_user_profile(preferences=user_preferences)
            sync_result['updates'].append({
                'type': 'user_profile',
                'action': 'updated'
            })
        
        # 4. 记录协作经验
        collaboration = task_ctx.get_collaboration_record()
        if collaboration:
            global_ctx.add_collaboration_record(collaboration)
            sync_result['updates'].append({
                'type': 'collaboration',
                'id': collaboration.id,
                'action': 'added'
            })
        
        # 记录同步历史
        self.sync_history.append(sync_result)
        
        return sync_result
    
    def sync_global_to_task(self, global_ctx: 'GlobalContext',
                           task_ctx: 'TaskContext',
                           task_definition: TaskDefinition) -> Dict[str, Any]:
        """
        任务开始时，将相关知识注入到任务上下文
        
        Args:
            global_ctx: 全局上下文
            task_ctx: 任务上下文
            task_definition: 任务定义
            
        Returns:
            Dict: 同步结果
        """
        sync_result = {
            'direction': SyncDirection.GLOBAL_TO_TASK.value,
            'task_id': task_definition.task_id,
            'timestamp': datetime.now().isoformat(),
            'injections': []
        }
        
        # 1. 注入相关领域知识
        task_keywords = self._extract_keywords(task_definition)
        relevant_knowledge = global_ctx.search_knowledge(task_keywords)
        for knowledge in relevant_knowledge[:5]:  # 最多注入 5 条相关知识
            task_ctx.add_knowledge_reference(knowledge)
            sync_result['injections'].append({
                'type': 'knowledge',
                'id': knowledge.id,
                'title': knowledge.title
            })
        
        # 2. 注入相似任务的历史经验
        similar_experiences = global_ctx.find_similar_experiences(
            task_definition.task_type if hasattr(task_definition, 'task_type') else 'general'
        )
        for experience in similar_experiences[:3]:  # 最多注入 3 条经验
            task_ctx.add_experience_reference(experience)
            sync_result['injections'].append({
                'type': 'experience',
                'id': experience.id,
                'success': experience.success
            })
        
        # 3. 注入用户偏好
        user_profile = global_ctx.get_user_profile()
        if user_profile:
            task_ctx.set_user_preferences(user_profile.preferences)
            sync_result['injections'].append({
                'type': 'user_preferences',
                'count': len(user_profile.preferences)
            })
        
        # 4. 注入能力评估（用于任务分配参考）
        capability_model = global_ctx.get_capability_model("default")
        if capability_model:
            task_ctx.set_capability_reference(capability_model)
            sync_result['injections'].append({
                'type': 'capability_model',
                'entity': capability_model.entity_id
            })
        
        # 记录同步历史
        self.sync_history.append(sync_result)
        
        return sync_result
    
    def _extract_experience(self, task_ctx: 'TaskContext') -> Optional[ExperienceItem]:
        """从任务上下文提取经验"""
        task_def = task_ctx.get_definition()
        task_status = task_ctx.get_status()
        
        if not task_def or not task_status:
            return None
        
        # 提取经验教训
        lessons = []
        best_practices = []
        
        # 从思考历史中提取
        thoughts = task_ctx.get_thoughts()
        for thought in thoughts:
            if 'lesson' in thought.content.lower() or '教训' in thought.content:
                lessons.append(thought.content)
            if 'best practice' in thought.content.lower() or '最佳实践' in thought.content:
                best_practices.append(thought.content)
        
        # 创建经验项
        experience = ExperienceItem(
            id=f"exp-{task_def.task_id}",
            task_id=task_def.task_id,
            task_type=getattr(task_def, 'task_type', 'general'),
            success=(task_status.status == 'completed'),
            description=f"任务：{task_def.title}",
            lessons_learned=lessons,
            best_practices=best_practices
        )
        
        return experience
    
    def _extract_knowledge(self, artifact_type: str, 
                          artifact_data: Dict, 
                          task_id: str) -> Optional[KnowledgeItem]:
        """从工件中提取知识"""
        # 根据工件类型提取知识
        if artifact_type == 'ARCHITECTURE':
            knowledge = KnowledgeItem(
                id=f"know-{task_id}-arch",
                category="architecture",
                title=f"架构设计知识 - {task_id}",
                content=artifact_data,
                tags=["architecture", "design"],
                source=task_id
            )
            return knowledge
        
        elif artifact_type == 'PRD':
            knowledge = KnowledgeItem(
                id=f"know-{task_id}-prd",
                category="requirements",
                title=f"需求知识 - {task_id}",
                content=artifact_data,
                tags=["requirements", "business"],
                source=task_id
            )
            return knowledge
        
        elif artifact_type == 'TEST_PLAN':
            knowledge = KnowledgeItem(
                id=f"know-{task_id}-test",
                category="testing",
                title=f"测试知识 - {task_id}",
                content=artifact_data,
                tags=["testing", "quality"],
                source=task_id
            )
            return knowledge
        
        return None
    
    def _extract_keywords(self, task_def: TaskDefinition) -> List[str]:
        """从任务定义提取关键词"""
        keywords = []
        
        # 从标题和描述提取
        text = f"{task_def.title} {task_def.description}"
        
        # 简单关键词提取（实际应该使用 NLP 技术）
        important_words = ['架构', '设计', '测试', '开发', '需求', 'UI', '数据库', 'API']
        for word in important_words:
            if word in text:
                keywords.append(word)
        
        return keywords
    
    def get_sync_history(self, limit: int = 10) -> List[Dict]:
        """获取同步历史"""
        return self.sync_history[-limit:]


class GlobalContext:
    """
    全局上下文（长期记忆）
    
    包含：
    - 用户画像
    - 领域知识库
    - 历史经验库
    - 协作网络
    - 能力模型
    """
    
    def __init__(self, storage_path: str = "."):
        """
        初始化全局上下文
        
        Args:
            storage_path: 存储路径
        """
        self.storage_path = Path(storage_path) / "context" / "global"
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        # 核心组件
        self.user_profile: Optional[UserProfile] = None
        self.knowledge_base: Dict[str, KnowledgeItem] = {}
        self.experience_db: Dict[str, ExperienceItem] = {}
        self.collaboration_net: Dict[str, CollaborationRecord] = {}
        self.capability_models: Dict[str, CapabilityModel] = {}
        
        # 版本控制
        self.version: int = 0
        self.version_history: List[Dict[str, Any]] = []
        
        # 加载现有数据
        self._load()
    
    def _load(self):
        """从磁盘加载数据"""
        data_file = self.storage_path / "global_context.json"
        if data_file.exists():
            try:
                with open(data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # 恢复数据
                self.version = data.get('version', 0)
                
                # 恢复用户画像
                if data.get('user_profile'):
                    self.user_profile = UserProfile(**data['user_profile'])
                
                # 恢复知识库
                for k_id, k_data in data.get('knowledge_base', {}).items():
                    self.knowledge_base[k_id] = KnowledgeItem(**k_data)
                
                # 恢复经验库
                for e_id, e_data in data.get('experience_db', {}).items():
                    self.experience_db[e_id] = ExperienceItem(**e_data)
                
                # 恢复协作网络
                for c_id, c_data in data.get('collaboration_net', {}).items():
                    self.collaboration_net[c_id] = CollaborationRecord(**c_data)
                
                # 恢复能力模型
                for m_id, m_data in data.get('capability_models', {}).items():
                    self.capability_models[m_id] = CapabilityModel(**m_data)
                
                # 恢复版本历史
                self.version_history = data.get('version_history', [])
                
            except Exception as e:
                print(f"加载全局上下文失败：{e}")
    
    def _save(self):
        """保存到磁盘"""
        data_file = self.storage_path / "global_context.json"
        
        # 递增版本
        self.version += 1
        
        # 记录版本历史
        version_record = {
            'version': self.version,
            'timestamp': datetime.now().isoformat(),
            'stats': {
                'knowledge_count': len(self.knowledge_base),
                'experience_count': len(self.experience_db),
                'collaboration_count': len(self.collaboration_net)
            }
        }
        self.version_history.append(version_record)
        
        # 保存数据
        data = {
            'version': self.version,
            'user_profile': asdict(self.user_profile) if self.user_profile else None,
            'knowledge_base': {k: asdict(v) for k, v in self.knowledge_base.items()},
            'experience_db': {k: asdict(v) for k, v in self.experience_db.items()},
            'collaboration_net': {k: asdict(v) for k, v in self.collaboration_net.items()},
            'capability_models': {k: asdict(v) for k, v in self.capability_models.items()},
            'version_history': self.version_history[-100:]  # 保留最近 100 个版本
        }
        
        try:
            with open(data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存全局上下文失败：{e}")
    
    # ========== 用户画像管理 ==========
    
    def set_user_profile(self, profile: UserProfile):
        """设置用户画像"""
        self.user_profile = profile
        self._save()
    
    def update_user_profile(self, 
                           identity: str = None,
                           preferences: Dict = None,
                           habits: Dict = None,
                           expertise: List = None):
        """更新用户画像"""
        if not self.user_profile:
            self.user_profile = UserProfile(user_id="default")
        
        if identity:
            self.user_profile.identity = identity
        if preferences:
            self.user_profile.preferences.update(preferences)
        if habits:
            self.user_profile.habits.update(habits)
        if expertise:
            self.user_profile.expertise.extend(expertise)
        
        self.user_profile.updated_at = datetime.now().isoformat()
        self._save()
    
    def get_user_profile(self) -> Optional[UserProfile]:
        """获取用户画像"""
        return self.user_profile
    
    # ========== 知识库管理 ==========
    
    def add_knowledge(self, knowledge: KnowledgeItem):
        """添加知识"""
        self.knowledge_base[knowledge.id] = knowledge
        self._save()
    
    def get_knowledge(self, knowledge_id: str) -> Optional[KnowledgeItem]:
        """获取知识"""
        return self.knowledge_base.get(knowledge_id)
    
    def search_knowledge(self, keywords: List[str], limit: int = 10) -> List[KnowledgeItem]:
        """搜索知识"""
        results = []
        for knowledge in self.knowledge_base.values():
            # 简单关键词匹配
            match = False
            text = f"{knowledge.title} {knowledge.category} {' '.join(knowledge.tags)}"
            for keyword in keywords:
                if keyword in text:
                    match = True
                    break
            
            if match:
                results.append(knowledge)
        
        # 按置信度和使用次数排序
        results.sort(key=lambda k: (k.confidence, k.usage_count), reverse=True)
        return results[:limit]
    
    # ========== 经验库管理 ==========
    
    def add_experience(self, experience: ExperienceItem):
        """添加经验"""
        self.experience_db[experience.id] = experience
        self._save()
    
    def find_similar_experiences(self, task_type: str, limit: int = 5) -> List[ExperienceItem]:
        """查找相似任务的经验"""
        results = [
            exp for exp in self.experience_db.values()
            if exp.task_type == task_type
        ]
        results.sort(key=lambda e: e.created_at, reverse=True)
        return results[:limit]
    
    # ========== 协作网络管理 ==========
    
    def add_collaboration_record(self, record: CollaborationRecord):
        """添加协作记录"""
        self.collaboration_net[record.id] = record
        self._save()
    
    # ========== 能力模型管理 ==========
    
    def set_capability_model(self, model: CapabilityModel):
        """设置能力模型"""
        self.capability_models[model.entity_id] = model
        self._save()
    
    def get_capability_model(self, entity_id: str) -> Optional[CapabilityModel]:
        """获取能力模型"""
        return self.capability_models.get(entity_id)
    
    # ========== 版本控制 ==========
    
    def get_version(self) -> int:
        """获取当前版本"""
        return self.version


class TaskContext:
    """
    任务上下文（工作记忆）
    
    包含：
    - 任务定义
    - 任务状态
    - 工作记忆
    - 中间结果
    - 思考历史
    """
    
    def __init__(self, task_id: str, storage_path: str = "."):
        """
        初始化任务上下文
        
        Args:
            task_id: 任务 ID
            storage_path: 存储路径
        """
        self.task_id = task_id
        self.storage_path = Path(storage_path) / "context" / "tasks" / task_id
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        # 核心组件
        self.definition: Optional[TaskDefinition] = None
        self.status: Optional[TaskStatus] = None
        self.working_memory: Dict[str, Any] = {}
        self.artifacts: Dict[str, Any] = {}
        self.thoughts: List[ThoughtRecord] = []
        
        # 引用（从全局上下文注入）
        self.knowledge_references: List[KnowledgeItem] = []
        self.experience_references: List[ExperienceItem] = []
        self.user_preferences: Dict[str, Any] = {}
        self.capability_reference: Optional[CapabilityModel] = None
        
        # 加载现有数据
        self._load()
    
    def _load(self):
        """从磁盘加载数据"""
        data_file = self.storage_path / "task_context.json"
        if data_file.exists():
            try:
                with open(data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # 恢复数据
                if data.get('definition'):
                    self.definition = TaskDefinition(**data['definition'])
                
                if data.get('status'):
                    self.status = TaskStatus(**data['status'])
                
                self.working_memory = data.get('working_memory', {})
                self.artifacts = data.get('artifacts', {})
                
                # 恢复思考历史
                for t_data in data.get('thoughts', []):
                    self.thoughts.append(ThoughtRecord(**t_data))
                
            except Exception as e:
                print(f"加载任务上下文失败：{e}")
    
    def _save(self):
        """保存到磁盘"""
        data_file = self.storage_path / "task_context.json"
        
        data = {
            'task_id': self.task_id,
            'definition': asdict(self.definition) if self.definition else None,
            'status': asdict(self.status) if self.status else None,
            'working_memory': self.working_memory,
            'artifacts': self.artifacts,
            'thoughts': [asdict(t) for t in self.thoughts],
            'references': {
                'knowledge': [asdict(k) for k in self.knowledge_references],
                'experience': [asdict(e) for e in self.experience_references]
            },
            'user_preferences': self.user_preferences
        }
        
        try:
            with open(data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存任务上下文失败：{e}")
    
    # ========== 任务定义管理 ==========
    
    def set_definition(self, definition: TaskDefinition):
        """设置任务定义"""
        self.definition = definition
        self._save()
    
    def get_definition(self) -> Optional[TaskDefinition]:
        """获取任务定义"""
        return self.definition
    
    # ========== 任务状态管理 ==========
    
    def update_status(self, 
                     status: str = None,
                     progress: float = None,
                     current_step: str = None,
                     risks: List = None,
                     blockers: List = None):
        """更新任务状态"""
        if not self.status:
            self.status = TaskStatus(task_id=self.task_id)
        
        if status:
            self.status.status = status
        if progress is not None:
            self.status.progress = progress
        if current_step:
            self.status.current_step = current_step
        if risks:
            self.status.risks.extend(risks)
        if blockers:
            self.status.blockers.extend(blockers)
        
        self.status.updated_at = datetime.now().isoformat()
        self._save()
    
    def get_status(self) -> Optional[TaskStatus]:
        """获取任务状态"""
        return self.status
    
    # ========== 工作记忆管理 ==========
    
    def set_working_memory(self, key: str, value: Any):
        """设置工作记忆"""
        self.working_memory[key] = value
        self._save()
    
    def get_working_memory(self, key: str = None) -> Any:
        """获取工作记忆"""
        if key:
            return self.working_memory.get(key)
        return self.working_memory
    
    def clear_working_memory(self):
        """清空工作记忆"""
        self.working_memory = {}
        self._save()
    
    # ========== 工件管理 ==========
    
    def add_artifact(self, artifact_type: str, artifact_data: Any, role: str = None, file_name: str = None):
        """
        添加工件
        
        Args:
            artifact_type: 工件类型（如 ARCHITECTURE, PRD, TEST_PLAN, UI_DESIGN, CODE, DEV_DOC 等）
            artifact_data: 工件数据
            role: 角色 ID（可选，用于保存到对应角色目录）
            file_name: 文件名（可选，如果不提供则自动生成）
        """
        self.artifacts[artifact_type] = artifact_data
        
        # 如果提供了角色信息，则保存到对应的角色目录
        if role:
            self._save_artifact_to_role_dir(artifact_type, artifact_data, role, file_name)
        
        self._save()
    
    def _save_artifact_to_role_dir(self, artifact_type: str, artifact_data: Any, role: str, file_name: str = None):
        """
        将工件保存到对应的角色目录
        
        Args:
            artifact_type: 工件类型
            artifact_data: 工件数据
            role: 角色 ID
            file_name: 文件名
        """
        # 角色目录映射
        role_dir_map = {
            'architect': 'docs/roles/architect',
            'product-manager': 'docs/roles/product-manager',
            'solo-coder': 'docs/roles/solo-coder',
            'test-expert': 'docs/roles/test-expert',
            'ui-designer': 'docs/roles/ui-designer'
        }
        
        # 获取角色目录
        role_dir = role_dir_map.get(role)
        if not role_dir:
            print(f"⚠️  未找到角色目录映射：{role}")
            return
        
        # 构建完整路径
        # storage_path 格式: <skill_root>/context/tasks/<task_id>
        # skill_root 传入时就是 <skill_root>（如 /path/to/skill）
        # parent 1: <skill_root>/context/tasks
        # parent 2: <skill_root>/context
        # parent 3: <skill_root>
        # 所以需要 parent.parent.parent 才能回到 skill_root
        # 但由于 TaskContext 的 storage_path 已经是 skill_root / "context" / "tasks" / task_id
        # 所以这里直接用 self.storage_path.parent.parent.parent 就能回到 skill_root
        skill_root = self.storage_path.parent.parent.parent
        artifact_dir = skill_root / role_dir
        
        # 确保目录存在
        artifact_dir.mkdir(parents=True, exist_ok=True)
        
        # 确定文件名（清理 task_id 中的特殊字符）
        if not file_name:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            # 清理 task_id：移除特殊字符，只保留安全的文件名字符
            safe_task_id = self.task_id[:40] if len(self.task_id) > 40 else self.task_id
            safe_task_id = ''.join(c for c in safe_task_id if c.isalnum() or c in ('_', '-', ' ')).strip()
            safe_task_id = safe_task_id.replace(' ', '_')
            file_name = f"{artifact_type.lower()}_{safe_task_id}_{timestamp}.md"
        
        file_path = artifact_dir / file_name
        
        # 根据工件类型生成内容
        content = self._generate_artifact_content(artifact_type, artifact_data)
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"✅ 工件已保存到：{file_path}")
        except Exception as e:
            print(f"❌ 保存工件失败：{e}")
    
    def _generate_artifact_content(self, artifact_type: str, artifact_data: Any) -> str:
        """
        根据工件类型生成内容
        
        Args:
            artifact_type: 工件类型
            artifact_data: 工件数据
            
        Returns:
            str: 格式化后的内容
        """
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 如果是字符串，直接返回
        if isinstance(artifact_data, str):
            return artifact_data
        
        # 如果是字典，格式化输出
        if isinstance(artifact_data, dict):
            content = f"# {artifact_type.replace('_', ' ').title()}\n\n"
            content += f"**任务 ID**: {self.task_id}\n"
            content += f"**生成时间**: {timestamp}\n\n"
            
            # 根据工件类型添加特定内容
            if artifact_type == 'ARCHITECTURE':
                content += self._format_architecture_content(artifact_data)
            elif artifact_type == 'PRD':
                content += self._format_prd_content(artifact_data)
            elif artifact_type == 'TEST_PLAN':
                content += self._format_test_plan_content(artifact_data)
            elif artifact_type == 'UI_DESIGN':
                content += self._format_ui_design_content(artifact_data)
            elif artifact_type == 'CODE':
                content += self._format_code_content(artifact_data)
            elif artifact_type == 'DEV_DOC':
                content += self._format_dev_doc_content(artifact_data)
            else:
                # 通用格式
                content += self._format_generic_content(artifact_data)
            
            return content
        
        # 其他类型转换为字符串
        return str(artifact_data)
    
    def _format_architecture_content(self, data: Dict) -> str:
        """格式化架构设计内容"""
        content = "## 架构设计\n\n"
        
        if 'style' in data:
            content += f"**架构风格**: {data['style']}\n"
        if 'components' in data:
            content += "\n### 组件列表\n"
            for comp in data['components']:
                content += f"- {comp}\n"
        if 'technologies' in data:
            content += "\n### 技术选型\n"
            for tech in data['technologies']:
                content += f"- {tech}\n"
        if 'description' in data:
            content += f"\n### 描述\n{data['description']}\n"
        
        return content
    
    def _format_prd_content(self, data: Dict) -> str:
        """格式化需求文档内容"""
        content = "## 需求文档\n\n"
        
        if 'title' in data:
            content += f"**标题**: {data['title']}\n"
        if 'goals' in data:
            content += "\n### 目标\n"
            for goal in data['goals']:
                content += f"- {goal}\n"
        if 'requirements' in data:
            content += "\n### 需求\n"
            for req in data['requirements']:
                content += f"- {req}\n"
        if 'user_stories' in data:
            content += "\n### 用户故事\n"
            for story in data['user_stories']:
                content += f"- {story}\n"
        
        return content
    
    def _format_test_plan_content(self, data: Dict) -> str:
        """格式化测试计划内容"""
        content = "## 测试计划\n\n"
        
        if 'test_cases' in data:
            content += "\n### 测试用例\n"
            for tc in data['test_cases']:
                content += f"- {tc}\n"
        if 'test_strategy' in data:
            content += f"\n### 测试策略\n{data['test_strategy']}\n"
        if 'coverage' in data:
            content += f"\n### 覆盖率目标\n{data['coverage']}\n"
        
        return content
    
    def _format_ui_design_content(self, data: Dict) -> str:
        """格式化 UI 设计内容"""
        content = "## UI 设计\n\n"
        
        if 'layout' in data:
            content += f"**布局**: {data['layout']}\n"
        if 'color_scheme' in data:
            content += f"**配色方案**: {data['color_scheme']}\n"
        if 'components' in data:
            content += "\n### 组件\n"
            for comp in data['components']:
                content += f"- {comp}\n"
        if 'description' in data:
            content += f"\n### 描述\n{data['description']}\n"
        
        return content
    
    def _format_code_content(self, data: Dict) -> str:
        """格式化代码内容"""
        content = "## 代码实现\n\n"
        
        if 'language' in data:
            content += f"**语言**: {data['language']}\n"
        if 'files' in data:
            content += "\n### 文件\n"
            for file in data['files']:
                content += f"- {file}\n"
        if 'description' in data:
            content += f"\n### 描述\n{data['description']}\n"
        
        return content
    
    def _format_dev_doc_content(self, data: Dict) -> str:
        """格式化开发文档内容"""
        content = "## 开发文档\n\n"
        
        if 'title' in data:
            content += f"**标题**: {data['title']}\n"
        if 'overview' in data:
            content += f"\n### 概述\n{data['overview']}\n"
        if 'installation' in data:
            content += f"\n### 安装\n{data['installation']}\n"
        if 'usage' in data:
            content += f"\n### 使用\n{data['usage']}\n"
        if 'api' in data:
            content += f"\n### API\n{data['api']}\n"
        
        return content
    
    def _format_generic_content(self, data: Dict) -> str:
        """格式化通用内容"""
        content = ""
        for key, value in data.items():
            if isinstance(value, (list, dict)):
                content += f"\n### {key.replace('_', ' ').title()}\n"
                if isinstance(value, list):
                    for item in value:
                        content += f"- {item}\n"
                else:
                    for k, v in value.items():
                        content += f"- {k}: {v}\n"
            else:
                content += f"**{key.replace('_', ' ').title()}**: {value}\n"
        return content
    
    def get_artifact(self, artifact_type: str) -> Optional[Any]:
        """获取工件"""
        return self.artifacts.get(artifact_type)
    
    def get_artifacts(self) -> Dict[str, Any]:
        """获取所有工件"""
        return self.artifacts
    
    # ========== 思考历史管理 ==========
    
    def add_thought(self, role: str, thought_type: str, content: str, 
                   context: Dict = None):
        """添加思考记录"""
        thought = ThoughtRecord(
            id=f"thought-{len(self.thoughts) + 1}",
            task_id=self.task_id,
            role=role,
            thought_type=thought_type,
            content=content,
            context=context or {}
        )
        self.thoughts.append(thought)
        self._save()
    
    def get_thoughts(self) -> List[ThoughtRecord]:
        """获取思考历史"""
        return self.thoughts
    
    # ========== 引用管理 ==========
    
    def add_knowledge_reference(self, knowledge: KnowledgeItem):
        """添加知识引用"""
        self.knowledge_references.append(knowledge)
        self._save()
    
    def add_experience_reference(self, experience: ExperienceItem):
        """添加经验引用"""
        self.experience_references.append(experience)
        self._save()
    
    def set_user_preferences(self, preferences: Dict[str, Any]):
        """设置用户偏好"""
        self.user_preferences = preferences
        self._save()
    
    def get_user_preferences(self) -> Dict[str, Any]:
        """获取用户偏好"""
        return self.user_preferences
    
    def set_capability_reference(self, model: CapabilityModel):
        """设置能力模型引用"""
        self.capability_reference = model
        self._save()
    
    def get_collaboration_record(self) -> Optional[CollaborationRecord]:
        """获取协作记录（如果有）"""
        # 从思考历史或其他地方提取协作信息
        # 这里是简化实现
        return None


class DualLayerContextManager:
    """
    双层上下文管理器
    
    统一管理全局上下文层和任务上下文层
    """
    
    def __init__(self, project_root: str = ".", skill_root: str = "."):
        """
        初始化双层上下文管理器
        
        Args:
            project_root: 项目根目录
            skill_root: 技能根目录
        """
        self.project_root = Path(project_root)
        self.skill_root = Path(skill_root)
        
        # 双层上下文
        self.global_context = GlobalContext(str(self.skill_root))
        self.task_contexts: Dict[str, TaskContext] = {}
        
        # 同步器
        self.synchronizer = ContextSynchronizer()
        
        # 当前任务
        self.current_task_id: Optional[str] = None
        
        # 初始化项目结构（如果不存在）
        self._ensure_project_structure()
    
    def _ensure_project_structure(self):
        """确保项目目录结构存在"""
        # 角色目录映射
        role_dirs = [
            'docs/roles/architect',
            'docs/roles/product-manager',
            'docs/roles/solo-coder',
            'docs/roles/test-expert',
            'docs/roles/ui-designer',
            'docs/roles/devops'
        ]
        
        for role_dir in role_dirs:
            full_path = self.project_root / role_dir
            full_path.mkdir(parents=True, exist_ok=True)
        
        # 创建 docs/spec 目录
        (self.project_root / 'docs/spec').mkdir(parents=True, exist_ok=True)
    
    def start_task(self, task_definition: TaskDefinition) -> TaskContext:
        """
        开始新任务
        
        Args:
            task_definition: 任务定义
            
        Returns:
            TaskContext: 任务上下文
        """
        task_id = task_definition.task_id
        
        # 创建任务上下文
        task_ctx = TaskContext(task_id, str(self.skill_root))
        task_ctx.set_definition(task_definition)
        
        # 从全局上下文注入相关知识
        self.synchronizer.sync_global_to_task(
            self.global_context,
            task_ctx,
            task_definition
        )
        
        # 保存任务上下文
        self.task_contexts[task_id] = task_ctx
        self.current_task_id = task_id
        
        print(f"✅ 任务 {task_id} 已启动，相关知识已注入")
        return task_ctx
    
    def complete_task(self, task_id: str) -> bool:
        """
        完成任务
        
        Args:
            task_id: 任务 ID
            
        Returns:
            bool: 是否成功完成
        """
        if task_id not in self.task_contexts:
            print(f"❌ 任务不存在：{task_id}")
            return False
        
        task_ctx = self.task_contexts[task_id]
        
        # 更新任务状态
        task_ctx.update_status(status='completed')
        
        # 将经验沉淀到全局上下文
        sync_result = self.synchronizer.sync_task_to_global(
            self.global_context,
            task_ctx,
            task_id
        )
        
        print(f"✅ 任务 {task_id} 已完成，经验已沉淀")
        print(f"   同步更新：{len(sync_result['updates'])} 项")
        
        return True
    
    def get_current_context(self) -> Optional[TaskContext]:
        """获取当前任务上下文"""
        if self.current_task_id and self.current_task_id in self.task_contexts:
            return self.task_contexts[self.current_task_id]
        return None
    
    def get_global_context(self) -> GlobalContext:
        """获取全局上下文"""
        return self.global_context
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            'global_context': {
                'version': self.global_context.get_version(),
                'knowledge_count': len(self.global_context.knowledge_base),
                'experience_count': len(self.global_context.experience_db),
                'collaboration_count': len(self.global_context.collaboration_net)
            },
            'task_contexts': len(self.task_contexts),
            'current_task': self.current_task_id,
            'sync_history_count': len(self.synchronizer.sync_history)
        }


def main():
    """示例用法"""
    # 创建双层上下文管理器
    manager = DualLayerContextManager(
        project_root=".",
        skill_root="."
    )
    
    # 初始化用户画像
    manager.global_context.set_user_profile(
        UserProfile(
            user_id="default",
            identity="架构师",
            preferences={"language": "zh", "detail_level": "high"},
            expertise=["Java", "Spring Boot", "微服务"]
        )
    )
    
    # 开始新任务
    task_def = TaskDefinition(
        task_id="TASK-001",
        title="设计系统架构",
        description="设计一个微服务架构",
        goals=["高可用", "可扩展", "易维护"],
        constraints=["Java 21", "Spring Boot 3"]
    )
    
    task_ctx = manager.start_task(task_def)
    
    # 添加思考记录
    task_ctx.add_thought(
        role="architect",
        thought_type="analysis",
        content="考虑到系统需要高可用，建议采用微服务架构",
        context={"alternatives": ["单体架构", "SOA"]}
    )
    
    # 添加工件
    task_ctx.add_artifact("ARCHITECTURE", {
        "style": "微服务",
        "components": ["API Gateway", "Service Registry", "Config Server"]
    })
    
    # 完成任务
    manager.complete_task("TASK-001")
    
    # 显示统计
    stats = manager.get_statistics()
    print(f"\n📊 统计信息:")
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
