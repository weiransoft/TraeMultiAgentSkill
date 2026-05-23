#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkflowEngineV2 - 增强版工作流引擎

基于 Anthropic 文章《Effective Harnesses for Long-Running Agents》的核心思想：
- 双智能体架构（初始化 Agent + 执行 Agent）
- 像人类工程师一样定期保存进度（Checkpoint）
- 标准化交接班机制（Handoff）
- 任务清单管理（TaskList）

这个增强版工作流引擎集成了：
1. CheckpointManager - 定期保存任务状态
2. TaskListManager - 任务清单管理
3. HandoffProtocol - 智能体交接班协议
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable
from datetime import datetime
from dataclasses import dataclass, field, asdict
from enum import Enum
import copy
import uuid
import sys

# 导入我们新创建的管理器
from checkpoint_manager import CheckpointManager, Checkpoint, CheckpointStatus, HandoffDocument
from task_list_manager import TaskListManager, TaskList, TaskItem, TaskStatus, TaskPriority


class WorkflowStatus(Enum):
    """工作流状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    WAITING_HANDOVER = "waiting_handover"  # 等待交接


class StepStatus(Enum):
    """步骤状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING_HANDOVER = "waiting_handover"


@dataclass
class WorkflowStep:
    """工作流步骤"""
    step_id: str
    name: str
    description: str
    role_id: str  # 负责的角色
    action: str  # 执行的动作
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    conditions: Dict[str, Any] = field(default_factory=dict)
    timeout: int = 3600  # 超时时间（秒）
    retry_count: int = 3  # 重试次数
    status: StepStatus = StepStatus.PENDING
    result: Any = None
    error: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


@dataclass
class WorkflowDefinition:
    """工作流定义"""
    workflow_id: str
    name: str
    description: str
    steps: List[WorkflowStep] = field(default_factory=list)
    variables: Dict[str, Any] = field(default_factory=dict)
    conditions: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class WorkflowInstance:
    """工作流实例"""
    instance_id: str
    workflow_id: str
    status: WorkflowStatus = WorkflowStatus.PENDING
    current_step: Optional[str] = None
    completed_steps: List[str] = field(default_factory=list)
    failed_steps: List[str] = field(default_factory=list)
    variables: Dict[str, Any] = field(default_factory=dict)
    results: Dict[str, Any] = field(default_factory=dict)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: str = ""
    
    # 新增：支持长程任务的字段
    checkpoint_id: Optional[str] = None  # 当前检查点 ID
    tasklist_id: Optional[str] = None  # 关联的任务清单 ID
    current_agent_id: Optional[str] = None  # 当前负责的 Agent
    handoff_history: List[str] = field(default_factory=list)  # 交接历史


class WorkflowEngineV2:
    """
    增强版工作流编排引擎（V2）
    
    基于 Anthropic 的长程 Agent 思想，增强功能：
    1. **Checkpoint 机制**：定期保存任务状态，支持断点恢复
    2. **TaskList 集成**：内置任务清单管理，支持任务拆分
    3. **Handoff 协议**：标准化交接班流程，多 Agent 协作
    4. **双层上下文**：项目级 + 任务级上下文管理
    5. **Cybernetics 增强**：Guard 预验证、反馈收集、性能画像（v2.5 新增）
    
    使用示例：
    ```python
    engine = WorkflowEngineV2("./workflows")
    
    # 创建工作流
    workflow = engine.create_workflow_from_task("实现电商系统")
    
    # 启动执行
    instance = engine.start_workflow(workflow.workflow_id)
    
    # 保存检查点（模拟定期保存）
    engine.save_checkpoint(instance.instance_id)
    
    # 交接班（模拟 Agent 换岗）
    engine.handoff(instance.instance_id, from_agent="architect", to_agent="solo-coder")
    
    # 恢复执行
    engine.resume_workflow(instance.instance_id)
    ```
    """
    
    def __init__(self, storage_path: str = "./workflows_v2",
                 cybernetics=None):
        """
        初始化增强版工作流引擎
        
        Args:
            storage_path: 存储路径
            cybernetics: CyberneticsIntegration 实例（可选，用于 Guard 验证和反馈收集）
        """
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        # 工作流定义
        self.definitions: Dict[str, WorkflowDefinition] = {}
        
        # 工作流实例
        self.instances: Dict[str, WorkflowInstance] = {}
        
        # 步骤执行器
        self.executors: Dict[str, Callable] = {}
        
        # 集成 CheckpointManager（检查点管理）
        self.checkpoint_manager = CheckpointManager(
            storage_path=str(self.storage_path / "checkpoints")
        )
        
        # 集成 TaskListManager（任务清单管理）
        self.tasklist_manager = TaskListManager(
            storage_path=str(self.storage_path / "tasklists")
        )
        
        # Cybernetics 增强集成（v2.5 新增）
        self.cybernetics = cybernetics
        
        # Checkpoint 保存间隔（每隔 N 个步骤保存一次）
        self.checkpoint_interval = 2
        
        # 加载现有数据
        self._load()
    
    def _load(self):
        """从磁盘加载工作流定义"""
        workflows_file = self.storage_path / "definitions.json"
        if workflows_file.exists():
            try:
                with open(workflows_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                for wf_id, wf_data in data.get('definitions', {}).items():
                    steps = []
                    for step_data in wf_data.get('steps', []):
                        step = WorkflowStep(
                            step_id=step_data['step_id'],
                            name=step_data['name'],
                            description=step_data['description'],
                            role_id=step_data['role_id'],
                            action=step_data['action'],
                            inputs=step_data.get('inputs', {}),
                            outputs=step_data.get('outputs', {}),
                            conditions=step_data.get('conditions', {}),
                            timeout=step_data.get('timeout', 3600),
                            retry_count=step_data.get('retry_count', 3),
                            status=StepStatus(step_data.get('status', 'pending'))
                        )
                        steps.append(step)
                    
                    definition = WorkflowDefinition(
                        workflow_id=wf_data['workflow_id'],
                        name=wf_data['name'],
                        description=wf_data['description'],
                        steps=steps,
                        variables=wf_data.get('variables', {}),
                        conditions=wf_data.get('conditions', {}),
                        metadata=wf_data.get('metadata', {})
                    )
                    self.definitions[wf_id] = definition
                
                print(f"✅ 已加载 {len(self.definitions)} 个工作流定义")
                
            except Exception as e:
                print(f"⚠️  加载工作流定义失败：{e}")
    
    def _save(self):
        """保存到磁盘"""
        workflows_file = self.storage_path / "definitions.json"
        
        data = {
            'version': '2.0',
            'updated_at': datetime.now().isoformat(),
            'definitions': {}
        }
        
        for wf_id, definition in self.definitions.items():
            wf_dict = {
                'workflow_id': definition.workflow_id,
                'name': definition.name,
                'description': definition.description,
                'steps': [],
                'variables': definition.variables,
                'conditions': definition.conditions,
                'metadata': definition.metadata,
                'created_at': definition.created_at
            }
            
            # 转换步骤，处理枚举类型
            for step in definition.steps:
                step_dict = {
                    'step_id': step.step_id,
                    'name': step.name,
                    'description': step.description,
                    'role_id': step.role_id,
                    'action': step.action,
                    'inputs': step.inputs,
                    'outputs': step.outputs,
                    'conditions': step.conditions,
                    'timeout': step.timeout,
                    'retry_count': step.retry_count,
                    'status': step.status.value if isinstance(step.status, StepStatus) else step.status,
                    'result': step.result,
                    'error': step.error,
                    'started_at': step.started_at,
                    'completed_at': step.completed_at
                }
                wf_dict['steps'].append(step_dict)
            
            data['definitions'][wf_id] = wf_dict
        
        try:
            with open(workflows_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️  保存工作流定义失败：{e}")
    
    def register_executor(self, action: str, executor: Callable):
        """
        注册步骤执行器
        
        Args:
            action: 动作名称
            executor: 执行函数
        """
        self.executors[action] = executor
        print(f"✅ 执行器已注册：{action}")
    
    def create_workflow_from_task(
        self,
        task_title: str,
        task_description: str = "",
        target_agent: str = None
    ) -> WorkflowDefinition:
        """
        从任务创建工作流（智能体初始化 Agent 的功能）
        
        这是一个便捷方法，根据任务描述自动拆分为多个步骤
        
        Args:
            task_title: 任务标题
            task_description: 任务描述
            target_agent: 目标 Agent（可选）
            
        Returns:
            WorkflowDefinition: 创建的工作流定义
        """
        workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
        
        # 智能任务拆分（基于关键词分析）
        steps = self._split_task_into_steps(task_title, task_description)
        
        definition = WorkflowDefinition(
            workflow_id=workflow_id,
            name=task_title,
            description=task_description,
            steps=steps,
            metadata={
                'target_agent': target_agent,
                'created_by': 'WorkflowEngineV2',
                'version': '2.0'
            }
        )
        
        self.definitions[workflow_id] = definition
        self._save()
        
        # 同时创建任务清单
        tasklist = self.tasklist_manager.create_tasklist(
            name=f"任务清单 - {task_title}",
            description=task_description,
            created_by=target_agent or 'auto'
        )
        
        # 将工作流步骤转换为任务清单
        for step in steps:
            self.tasklist_manager.add_task(
                tasklist_id=tasklist.tasklist_id,
                title=step.name,
                description=step.description,
                priority=TaskPriority.MEDIUM,
                assigned_agent=step.role_id,
                category="工作流任务"
            )
        
        # 关联工作流和任务清单
        definition.variables['tasklist_id'] = tasklist.tasklist_id
        
        print(f"✅ 工作流已创建：{workflow_id}（包含 {len(steps)} 个步骤）")
        print(f"📋 关联任务清单：{tasklist.tasklist_id}")
        
        return definition
    
    def _split_task_into_steps(
        self,
        task_title: str,
        task_description: str
    ) -> List[WorkflowStep]:
        """
        将任务智能拆分为多个步骤
        
        基于关键词识别任务类型，生成标准的工作流步骤
        
        Args:
            task_title: 任务标题
            task_description: 任务描述
            
        Returns:
            List[WorkflowStep]: 步骤列表
        """
        steps = []
        task_text = f"{task_title} {task_description}".lower()
        
        # 检测任务类型
        is_architecture = any(kw in task_text for kw in ['架构', '设计', '系统', '技术选型'])
        is_ui_design = any(kw in task_text for kw in ['界面', 'UI', '前端', '视觉'])
        is_development = any(kw in task_text for kw in ['开发', '实现', '编码', '功能'])
        is_testing = any(kw in task_text for kw in ['测试', '验证', '质量'])
        is_product = any(kw in task_text for kw in ['需求', '产品', 'PRD', '用户故事'])
        is_deployment = any(kw in task_text for kw in ['部署', '发布', '上线', '发布'])
        
        step_id = 1
        
        # 1. 需求分析（如果涉及产品/架构）
        if is_product or is_architecture:
            steps.append(WorkflowStep(
                step_id=f"step_{step_id}",
                name="需求分析",
                description="分析任务需求，制定详细的需求文档",
                role_id="product-manager",
                action="analyze_requirements",
                conditions={}
            ))
            step_id += 1
        
        # 2. 架构设计（如果涉及架构）
        if is_architecture:
            steps.append(WorkflowStep(
                step_id=f"step_{step_id}",
                name="架构设计",
                description="设计系统架构和技术选型",
                role_id="architect",
                action="design_architecture",
                conditions={}
            ))
            step_id += 1
        
        # 3. UI 设计（如果涉及 UI）
        if is_ui_design:
            steps.append(WorkflowStep(
                step_id=f"step_{step_id}",
                name="UI 设计",
                description="设计用户界面和交互流程",
                role_id="ui-designer",
                action="design_ui",
                conditions={}
            ))
            step_id += 1
        
        # 4. 测试设计（如果涉及测试）
        if is_testing:
            steps.append(WorkflowStep(
                step_id=f"step_{step_id}",
                name="测试设计",
                description="制定测试策略和测试用例",
                role_id="tester",
                action="design_tests",
                conditions={}
            ))
            step_id += 1
        
        # 5. 开发实现
        if is_development:
            steps.append(WorkflowStep(
                step_id=f"step_{step_id}",
                name="开发实现",
                description="实现功能代码",
                role_id="solo-coder",
                action="develop",
                conditions={}
            ))
            step_id += 1
        
        # 6. 测试验证
        if is_testing and is_development:
            steps.append(WorkflowStep(
                step_id=f"step_{step_id}",
                name="测试验证",
                description="执行测试用例，验证功能",
                role_id="tester",
                action="execute_tests",
                conditions={}
            ))
            step_id += 1
        
        # 7. 部署发布
        if is_deployment or (is_development and not is_testing):
            steps.append(WorkflowStep(
                step_id=f"step_{step_id}",
                name="部署发布",
                description="部署和发布系统",
                role_id="devops",
                action="deploy",
                conditions={}
            ))
        
        # 如果没有识别到任何类型，添加通用开发步骤
        if not steps:
            steps.append(WorkflowStep(
                step_id="step_1",
                name="任务执行",
                description=task_description or task_title,
                role_id=target_agent or "solo-coder",
                action="execute",
                conditions={}
            ))
        
        return steps
    
    def start_workflow(
        self,
        workflow_id: str,
        variables: Dict[str, Any] = None,
        auto_checkpoint: bool = True
    ) -> Optional[WorkflowInstance]:
        """
        启动工作流
        
        Args:
            workflow_id: 工作流 ID
            variables: 初始变量
            auto_checkpoint: 是否自动启用检查点
            
        Returns:
            Optional[WorkflowInstance]: 工作流实例
        """
        definition = self.definitions.get(workflow_id)
        if not definition:
            print(f"❌ 工作流不存在：{workflow_id}")
            return None
        
        # 创建实例
        instance_id = f"{workflow_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        instance = WorkflowInstance(
            instance_id=instance_id,
            workflow_id=workflow_id,
            variables=variables or {},
            status=WorkflowStatus.RUNNING,
            started_at=datetime.now().isoformat()
        )
        
        # 如果有任务清单，关联
        if 'tasklist_id' in definition.variables:
            instance.tasklist_id = definition.variables['tasklist_id']
        
        self.instances[instance_id] = instance
        
        print(f"🚀 工作流已启动：{instance_id}")
        print(f"   工作流：{definition.name}")
        print(f"   步骤数：{len(definition.steps)}")
        
        # 首次创建检查点
        if auto_checkpoint:
            self._create_checkpoint(instance, f"工作流启动")
        
        # 执行第一步
        self._execute_next_step(instance)
        
        return instance
    
    def _create_checkpoint(self, instance: WorkflowInstance, reason: str = ""):
        """
        创建检查点（内部方法）
        
        Args:
            instance: 工作流实例
            reason: 创建检查点的原因
        """
        definition = self.definitions.get(instance.workflow_id)
        if not definition:
            return
        
        # 找到当前步骤
        current_step = None
        for step in definition.steps:
            if step.step_id == instance.current_step:
                current_step = step
                break
        
        step_name = current_step.name if current_step else "未知"
        completed_ids = [s.step_id for s in definition.steps 
                        if s.status == StepStatus.COMPLETED]
        remaining_ids = [s.step_id for s in definition.steps 
                        if s.step_id not in completed_ids and s.step_id != instance.current_step]
        
        # 创建检查点
        checkpoint = self.checkpoint_manager.create_checkpoint_from_workflow(
            task_id=instance.instance_id,
            step_id=instance.current_step or "init",
            step_name=step_name,
            agent_id=instance.current_agent_id or "system",
            workflow_state={
                'completed_steps': completed_ids,
                'remaining_steps': remaining_ids,
                'context_snapshot': instance.variables,
                'variables': instance.variables,
                'outputs': instance.results
            }
        )
        
        instance.checkpoint_id = checkpoint.checkpoint_id
        print(f"💾 检查点已创建: {reason or '定期保存'}")
    
    def save_checkpoint(self, instance_id: str, reason: str = "") -> bool:
        """
        手动保存检查点
        
        Args:
            instance_id: 实例 ID
            reason: 保存原因
            
        Returns:
            bool: 是否保存成功
        """
        instance = self.instances.get(instance_id)
        if not instance:
            return False
        
        self._create_checkpoint(instance, reason or "手动保存")
        return True
    
    def handoff(
        self,
        instance_id: str,
        from_agent: str,
        to_agent: str,
        completed_work: List[str] = None,
        next_steps: List[str] = None,
        pending_issues: List[str] = None,
        notes: List[str] = None
    ) -> Optional[HandoffDocument]:
        """
        交接班（模拟 Anthropic 的双智能体架构）
        
        当一个 Agent 需要将任务交接给另一个 Agent 时调用此方法
        
        Args:
            instance_id: 实例 ID
            from_agent: 交出任务的 Agent
            to_agent: 接收任务的 Agent
            completed_work: 已完成的工作列表
            next_steps: 下一步骤列表
            pending_issues: 待处理问题
            notes: 注意事项
            
        Returns:
            Optional[HandoffDocument]: 交接文档
        """
        instance = self.instances.get(instance_id)
        if not instance:
            print(f"❌ 工作流实例不存在：{instance_id}")
            return None
        
        definition = self.definitions.get(instance.workflow_id)
        if not definition:
            return None
        
        # 获取当前状态
        current_state = {
            'instance_id': instance.instance_id,
            'workflow_id': instance.workflow_id,
            'current_step': instance.current_step,
            'completed_steps': instance.completed_steps,
            'variables': instance.variables,
            'results': instance.results
        }
        
        # 生成交接文档
        handoff = self.checkpoint_manager.create_handoff_document(
            task_id=instance.instance_id,
            from_agent=from_agent,
            to_agent=to_agent,
            completed_work=completed_work or [f"已完成步骤: {s}" for s in instance.completed_steps],
            current_state=current_state,
            next_steps=next_steps or self._get_remaining_steps_description(instance),
            pending_issues=pending_issues or [],
            important_notes=notes or [],
            context_for_next={
                'variables': instance.variables,
                'results': instance.results
            }
        )
        
        # 保存交接历史
        instance.handoff_history.append(handoff.handoff_id)
        
        # 更新当前 Agent
        old_agent = instance.current_agent_id
        instance.current_agent_id = to_agent
        
        print(f"🔄 交接班完成：{from_agent} → {to_agent}")
        print(f"   交接文档：{handoff.handoff_id}")
        
        # 保存交接后的检查点
        self._create_checkpoint(instance, f"交接班 {from_agent} → {to_agent}")
        
        return handoff
    
    def _get_remaining_steps_description(self, instance: WorkflowInstance) -> List[str]:
        """获取剩余步骤描述"""
        definition = self.definitions.get(instance.workflow_id)
        if not definition:
            return []
        
        remaining = []
        for step in definition.steps:
            if step.step_id not in instance.completed_steps:
                remaining.append(f"{step.name} ({step.role_id})")
        
        return remaining
    
    def restore_from_checkpoint(self, checkpoint_id: str) -> bool:
        """
        从检查点恢复工作流
        
        Args:
            checkpoint_id: 检查点 ID
            
        Returns:
            bool: 是否恢复成功
        """
        checkpoint = self.checkpoint_manager.load_checkpoint(checkpoint_id)
        if not checkpoint:
            print(f"❌ 检查点不存在：{checkpoint_id}")
            return False
        
        # 查找对应的实例
        instance = None
        for inst in self.instances.values():
            if inst.instance_id == checkpoint.task_id:
                instance = inst
                break
        
        if not instance:
            print(f"❌ 工作流实例不存在：{checkpoint.task_id}")
            return False
        
        # 恢复状态
        instance.variables = checkpoint.variables
        instance.results = checkpoint.outputs
        instance.current_step = checkpoint.step_id
        
        # 恢复已完成步骤
        instance.completed_steps = checkpoint.completed_steps
        
        print(f"♻️  已从检查点恢复：{checkpoint_id}")
        print(f"   恢复步骤：{checkpoint.step_name}")
        print(f"   进度：{checkpoint.progress_percentage:.1%}")
        
        return True
    
    def _execute_next_step(self, instance: WorkflowInstance):
        """
        执行下一步
        
        Args:
            instance: 工作流实例
        """
        definition = self.definitions.get(instance.workflow_id)
        if not definition:
            return
        
        # 找到下一步
        next_step = None
        for step in definition.steps:
            if step.step_id not in instance.completed_steps and \
               step.step_id not in instance.failed_steps:
                next_step = step
                break
        
        if not next_step:
            self._complete_workflow(instance)
            return
        
        # 检查条件
        if not self._check_conditions(next_step.conditions, instance):
            next_step.status = StepStatus.SKIPPED
            instance.completed_steps.append(next_step.step_id)
            self._execute_next_step(instance)
            return
        
        # 更新当前 Agent
        instance.current_agent_id = next_step.role_id
        
        # 执行步骤
        instance.current_step = next_step.step_id
        next_step.status = StepStatus.RUNNING
        next_step.started_at = datetime.now().isoformat()
        
        print(f"▶️  执行步骤：{next_step.name} (角色: {next_step.role_id})")
        
        try:
            result = self._execute_step(next_step, instance)
            next_step.status = StepStatus.COMPLETED
            next_step.result = result
            next_step.completed_at = datetime.now().isoformat()
            instance.completed_steps.append(next_step.step_id)
            
            # 更新任务清单
            if instance.tasklist_id:
                task = self._find_task_by_step(instance.tasklist_id, next_step.step_id)
                if task:
                    self.tasklist_manager.update_task_status(
                        instance.tasklist_id,
                        task.task_id,
                        TaskStatus.COMPLETED
                    )
            
            # 定期保存检查点
            if len(instance.completed_steps) % self.checkpoint_interval == 0:
                self._create_checkpoint(instance, f"定期保存 (已完成 {len(instance.completed_steps)} 步)")
            
            # 继续执行下一步
            self._execute_next_step(instance)
        
        except Exception as e:
            print(f"❌ 步骤执行失败：{next_step.name} - {str(e)}")
            next_step.status = StepStatus.FAILED
            next_step.error = str(e)
            instance.failed_steps.append(next_step.step_id)
            
            # 保存失败检查点
            self._create_checkpoint(instance, f"步骤失败: {next_step.name}")
            
            if next_step.retry_count > 0:
                next_step.retry_count -= 1
                print(f"🔄 重试步骤：{next_step.name} (剩余{next_step.retry_count}次)")
                self._execute_next_step(instance)
            else:
                instance.status = WorkflowStatus.FAILED
                instance.error = f"步骤 {next_step.name} 执行失败：{str(e)}"
    
    def _execute_step(self, step: WorkflowStep, instance: WorkflowInstance) -> Any:
        """执行单个步骤（含 Cybernetics 增强）"""
        executor = self.executors.get(step.action)
        if not executor:
            raise Exception(f"未找到执行器：{step.action}")
        
        # Cybernetics 增强：Guard 预验证
        if self.cybernetics and self.cybernetics.guard:
            try:
                task_dict = {
                    'id': step.step_id,
                    'type': step.action,
                    'complexity': 5,
                    'description': step.description
                }
                validation = self.cybernetics.pre_execute_validation(task_dict)
                if not validation.get('passed', True):
                    step.error = f"Guard warning: {validation.get('warnings', [])}"
            except Exception as e:
                print(f"⚠️  Guard 预验证异常：{e}")
        
        inputs = copy.deepcopy(step.inputs)
        for key, value in inputs.items():
            if isinstance(value, str) and value.startswith('${'):
                var_name = value[2:-1]
                inputs[key] = instance.variables.get(var_name)
        
        result = executor(step, inputs, instance)
        step.outputs = result if isinstance(result, dict) else {'result': result}
        
        for key, value in step.outputs.items():
            instance.variables[key] = value
        
        if isinstance(result, dict):
            instance.results.update(result)
        
        # Cybernetics 增强：反馈收集和性能画像更新
        if self.cybernetics:
            try:
                task_dict = {
                    'id': step.step_id,
                    'type': step.action,
                    'complexity': 5,
                    'description': step.description
                }
                self.cybernetics.execute_with_feedback(
                    task_dict,
                    executor=lambda t: {'success': True, 'step_id': step.step_id}
                )
            except Exception as e:
                print(f"⚠️  Cybernetics 反馈收集异常：{e}")
        
        return result
    
    def _check_conditions(self, conditions: Dict[str, Any], instance: WorkflowInstance) -> bool:
        """检查条件是否满足"""
        if not conditions:
            return True
        
        for key, expected in conditions.items():
            actual = instance.variables.get(key)
            if actual != expected:
                return False
        
        return True
    
    def _complete_workflow(self, instance: WorkflowInstance):
        """完成工作流"""
        instance.status = WorkflowStatus.COMPLETED
        instance.completed_at = datetime.now().isoformat()
        instance.current_step = None
        
        # 最终检查点
        self._create_checkpoint(instance, "工作流完成")
        
        # 导出最终任务清单
        if instance.tasklist_id:
            self.tasklist_manager.export_to_markdown(
                instance.tasklist_id,
                str(self.storage_path / f"tasklist_{instance.tasklist_id}.md")
            )
        
        print(f"✅ 工作流已完成：{instance.instance_id}")
        print(f"   完成时间：{instance.completed_at}")
        print(f"   总步骤：{len(instance.completed_steps)}")
    
    def _find_task_by_step(self, tasklist_id: str, step_id: str) -> Optional[TaskItem]:
        """根据步骤 ID 查找对应的任务"""
        tasklist = self.tasklist_manager.load_tasklist(tasklist_id)
        if not tasklist:
            return None
        
        for task in tasklist.tasks:
            if task.task_id == step_id or task.title.lower() in step_id.lower():
                return task
        
        return None
    
    def pause_workflow(self, instance_id: str) -> bool:
        """暂停工作流"""
        instance = self.instances.get(instance_id)
        if not instance or instance.status != WorkflowStatus.RUNNING:
            return False
        
        instance.status = WorkflowStatus.PAUSED
        self._create_checkpoint(instance, "工作流暂停")
        print(f"⏸️  工作流已暂停：{instance_id}")
        return True
    
    def resume_workflow(self, instance_id: str) -> bool:
        """恢复工作流"""
        instance = self.instances.get(instance_id)
        if not instance or instance.status != WorkflowStatus.PAUSED:
            return False
        
        instance.status = WorkflowStatus.RUNNING
        print(f"▶️  工作流已恢复：{instance_id}")
        self._execute_next_step(instance)
        return True
    
    def get_instance(self, instance_id: str) -> Optional[WorkflowInstance]:
        """获取工作流实例"""
        return self.instances.get(instance_id)
    
    def list_instances(self) -> List[WorkflowInstance]:
        """列出所有工作流实例"""
        return list(self.instances.values())
    
    def get_workflow_progress(self, instance_id: str) -> Dict[str, Any]:
        """
        获取工作流进度信息
        
        Args:
            instance_id: 实例 ID
            
        Returns:
            Dict[str, Any]: 进度信息
        """
        instance = self.instances.get(instance_id)
        if not instance:
            return {}
        
        definition = self.definitions.get(instance.workflow_id)
        if not definition:
            return {}
        
        total_steps = len(definition.steps)
        completed = len(instance.completed_steps)
        failed = len(instance.failed_steps)
        
        return {
            'instance_id': instance_id,
            'workflow_name': definition.name,
            'status': instance.status.value,
            'total_steps': total_steps,
            'completed_steps': completed,
            'failed_steps': failed,
            'progress_percentage': (completed / total_steps * 100) if total_steps > 0 else 0,
            'current_step': instance.current_step,
            'current_agent': instance.current_agent_id,
            'checkpoint_id': instance.checkpoint_id,
            'handoff_count': len(instance.handoff_history)
        }


if __name__ == "__main__":
    print("=" * 60)
    print("WorkflowEngineV2 演示 - 基于 Anthropic 长程 Agent 思想")
    print("=" * 60)
    
    # 创建增强版工作流引擎
    engine = WorkflowEngineV2("./test_workflows_v2")
    
    # 注册执行器
    engine.register_executor("analyze_requirements", lambda s, i, inst: {"requirements": "分析完成"})
    engine.register_executor("design_architecture", lambda s, i, inst: {"architecture": "架构设计完成"})
    engine.register_executor("develop", lambda s, i, inst: {"code": "开发完成"})
    
    # 从任务创建工作流
    workflow = engine.create_workflow_from_task(
        task_title="实现电商系统",
        task_description="开发一个完整的电商系统，包含用户、商品、订单模块",
        target_agent="architect"
    )
    
    print(f"\n📋 工作流详情:")
    print(f"   名称: {workflow.name}")
    print(f"   步骤数: {len(workflow.steps)}")
    for i, step in enumerate(workflow.steps, 1):
        print(f"   {i}. {step.name} (角色: {step.role_id})")
    
    # 注意：这里不实际执行，只是演示工作流创建
    print("\n✅ 演示完成")
    print("\n要实际执行工作流，请调用:")
    print("  instance = engine.start_workflow(workflow.workflow_id)")
    
    # 清理测试数据
    import shutil
    shutil.rmtree("./test_workflows_v2", ignore_errors=True)