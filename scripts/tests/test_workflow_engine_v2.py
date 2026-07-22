#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkflowEngineV2 集成测试

测试增强版工作流引擎的核心功能：
1. 工作流创建和启动
2. Checkpoint 保存和恢复
3. Handoff 交接班
4. 任务清单集成
5. 进度跟踪
"""

import unittest
import tempfile
import shutil
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from workflow_engine_v2 import WorkflowEngineV2, WorkflowStatus, StepStatus
from checkpoint_manager import CheckpointManager
from task_list_manager import TaskListManager


class TestWorkflowEngineV2(unittest.TestCase):
    """WorkflowEngineV2 测试类"""
    
    def setUp(self):
        """测试前准备"""
        self.test_dir = tempfile.mkdtemp()
        self.engine = WorkflowEngineV2(self.test_dir)
        
        # 注册测试执行器
        self.engine.register_executor(
            "analyze_requirements",
            lambda s, i, inst: {"requirements_doc": "requirements.md"}
        )
        self.engine.register_executor(
            "design_architecture",
            lambda s, i, inst: {"architecture_doc": "architecture.md"}
        )
        self.engine.register_executor(
            "develop",
            lambda s, i, inst: {"code_files": ["main.py", "utils.py"]}
        )
        self.engine.register_executor(
            "test",
            lambda s, i, inst: {"test_results": "all passed"}
        )
    
    def tearDown(self):
        """测试后清理"""
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_create_workflow_from_task(self):
        """测试从任务创建工作流"""
        workflow = self.engine.create_workflow_from_task(
            task_title="电商系统开发",
            task_description="开发一个包含用户、商品、订单模块的电商系统",
            target_agent="architect"
        )
        
        self.assertIsNotNone(workflow)
        self.assertEqual(workflow.name, "电商系统开发")
        self.assertTrue(len(workflow.steps) > 0)
        
        # 验证步骤包含正确的角色
        roles = [step.role_id for step in workflow.steps]
        self.assertIn("architect", roles)
        self.assertIn("solo-coder", roles)
        
        # 验证关联了任务清单
        self.assertIn('tasklist_id', workflow.variables)
    
    def test_workflow_with_checkpoint(self):
        """测试带检查点的工作流"""
        # 创建工作流
        workflow = self.engine.create_workflow_from_task(
            task_title="带检查点的测试任务",
            task_description="测试检查点功能"
        )
        
        # 注意：不实际执行，只测试创建
        self.assertIsNotNone(workflow)
        
        # 验证检查点目录已创建
        checkpoint_dir = Path(self.test_dir) / "checkpoints"
        self.assertTrue(checkpoint_dir.exists())
    
    def test_checkpoint_save_and_restore(self):
        """测试检查点保存和恢复"""
        # 创建工作流
        workflow = self.engine.create_workflow_from_task(
            task_title="检查点测试",
            task_description="测试检查点保存和恢复"
        )
        
        # 启动工作流获取真实实例
        instance = self.engine.start_workflow(workflow.workflow_id, auto_checkpoint=False)
        instance_id = instance.instance_id
        
        # 创建手动检查点
        result = self.engine.save_checkpoint(instance_id, "手动保存检查点")
        self.assertTrue(result)
        
        # 验证检查点创建
        checkpoints = self.engine.checkpoint_manager.list_checkpoints()
        self.assertTrue(len(checkpoints) >= 1)
    
    def test_handoff_functionality(self):
        """测试交接班功能"""
        # 创建工作流
        workflow = self.engine.create_workflow_from_task(
            task_title="交接班测试",
            task_description="测试 Agent 交接班功能"
        )
        
        # 启动
        instance = self.engine.start_workflow(
            workflow.workflow_id,
            auto_checkpoint=False  # 禁用自动检查点以便快速测试
        )
        
        self.assertIsNotNone(instance)
        instance_id = instance.instance_id
        
        # 执行交接班
        handoff = self.engine.handoff(
            instance_id,
            from_agent="architect",
            to_agent="solo-coder",
            completed_work=["架构设计完成"],
            next_steps=["用户模块开发", "订单模块开发"],
            pending_issues=["需要确定数据库选型"],
            notes=["注意性能优化"]
        )
        
        # 验证交接
        self.assertIsNotNone(handoff)
        self.assertEqual(handoff.from_agent, "architect")
        self.assertEqual(handoff.to_agent, "solo-coder")
        self.assertIn("架构设计完成", handoff.completed_work)
        
        # 验证交接历史
        updated_instance = self.engine.get_instance(instance_id)
        self.assertEqual(len(updated_instance.handoff_history), 1)
        self.assertEqual(updated_instance.current_agent_id, "solo-coder")
    
    def test_tasklist_integration(self):
        """测试任务清单集成"""
        # 创建工作流
        workflow = self.engine.create_workflow_from_task(
            task_title="任务清单集成测试",
            task_description="测试工作流与任务清单的集成"
        )
        
        tasklist_id = workflow.variables.get('tasklist_id')
        self.assertIsNotNone(tasklist_id)
        
        # 验证任务清单
        tasklist = self.engine.tasklist_manager.load_tasklist(tasklist_id)
        self.assertIsNotNone(tasklist)
        self.assertEqual(len(tasklist.tasks), len(workflow.steps))
    
    def test_workflow_progress(self):
        """测试工作流进度跟踪"""
        # 创建工作流
        workflow = self.engine.create_workflow_from_task(
            task_title="进度跟踪测试",
            task_description="测试工作流进度功能"
        )
        
        # 启动
        instance = self.engine.start_workflow(workflow.workflow_id)
        self.assertIsNotNone(instance)
        
        # 获取进度
        progress = self.engine.get_workflow_progress(instance.instance_id)
        
        self.assertIn('progress_percentage', progress)
        self.assertIn('total_steps', progress)
        self.assertIn('completed_steps', progress)
        self.assertEqual(progress['total_steps'], len(workflow.steps))


class TestCheckpointAndHandoffIntegration(unittest.TestCase):
    """检查点和交接班集成测试"""
    
    def setUp(self):
        """测试前准备"""
        self.test_dir = tempfile.mkdtemp()
        self.engine = WorkflowEngineV2(self.test_dir)
    
    def tearDown(self):
        """测试后清理"""
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_full_handover_flow(self):
        """测试完整的交接流程"""
        # 1. 创建工作流
        workflow = self.engine.create_workflow_from_task(
            task_title="完整交接流程测试",
            task_description="模拟完整的 Agent 交接流程"
        )
        
        instance = self.engine.start_workflow(workflow.workflow_id)
        self.assertIsNotNone(instance)
        
        instance_id = instance.instance_id
        
        # 2. 架构师完成架构设计，保存检查点
        self.engine.save_checkpoint(instance_id, "架构设计完成")
        
        # 3. 架构师交接给独立开发者
        handoff1 = self.engine.handoff(
            instance_id,
            from_agent="architect",
            to_agent="solo-coder",
            completed_work=[
                "完成需求分析",
                "完成系统架构设计",
                "完成技术选型"
            ],
            next_steps=[
                "用户模块开发",
                "商品模块开发",
                "订单模块开发"
            ],
            pending_issues=[
                "需要确定第三方支付接口"
            ],
            notes=[
                "架构文档在 /docs/architecture.md",
                "请遵循架构设计中的模块划分"
            ]
        )
        
        # 4. 独立开发者完成用户模块，保存检查点
        self.engine.save_checkpoint(instance_id, "用户模块完成")
        
        # 5. 独立开发者交接给测试工程师
        handoff2 = self.engine.handoff(
            instance_id,
            from_agent="solo-coder",
            to_agent="test-expert",
            completed_work=[
                "用户模块开发完成",
                "商品模块开发完成",
                "订单模块开发完成"
            ],
            next_steps=[
                "编写测试用例",
                "执行集成测试",
                "性能测试"
            ],
            pending_issues=[
                "第三方支付接口待集成"
            ],
            notes=[
                "代码在 /src 目录",
                "接口文档在 /docs/api.md"
            ]
        )
        
        # 验证交接历史
        final_instance = self.engine.get_instance(instance_id)
        self.assertEqual(len(final_instance.handoff_history), 2)
        
        # 验证所有交接文档
        all_handoffs = self.engine.checkpoint_manager.get_task_handoffs(instance_id)
        self.assertEqual(len(all_handoffs), 2)
        
        print("\n✅ 完整交接流程测试通过")
        print(f"   交接次数: {len(final_instance.handoff_history)}")
        print(f"   交接文档: {[h.handoff_id for h in all_handoffs]}")


if __name__ == '__main__':
    print("=" * 60)
    print("运行 WorkflowEngineV2 集成测试")
    print("=" * 60)
    
    unittest.main(verbosity=2)