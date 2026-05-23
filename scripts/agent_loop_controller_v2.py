#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能体循环控制器 v2.0（双层上下文增强版）

用于控制 trae-multi-agent 的 agent loop，确保所有任务都完成后再退出
支持自动检查、续传、进度跟踪和任务完成验证

新增功能（v2.0）:
- 双层动态上下文管理
- 任务经验自动沉淀
- 全局知识注入
- 上下文版本控制
"""

import os
import sys
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

# 导入双层上下文组件
try:
    from dual_layer_context_manager import (
        DualLayerContextManager,
        TaskDefinition,
        UserProfile
    )
    CONTEXT_MANAGER_AVAILABLE = True
except ImportError as e:
    CONTEXT_MANAGER_AVAILABLE = False
    print(f"⚠️  无法导入双层上下文组件：{e}")


class AgentLoopControllerV2:
    """
    智能体循环控制器 v2.0（双层上下文增强版）
    
    功能：
    1. 控制 agent loop 的执行和退出
    2. 自动检查任务完成状态
    3. 支持断点续传
    4. 验证所有任务完成后才退出
    5. 记录和跟踪执行进度
    6. 双层上下文管理（新增）
    7. 经验自动沉淀（新增）
    8. 知识动态注入（新增）
    9. Cybernetics 反馈控制环集成（v2.5 新增）
    """
    
    def __init__(self, project_root: str = ".", max_iterations: int = 100, 
                 task_file: Optional[str] = None,
                 cybernetics=None):
        """
        初始化控制器
        
        Args:
            project_root: 项目根目录
            max_iterations: 最大迭代次数
            task_file: 任务文件路径
            cybernetics: CyberneticsIntegration 实例（可选，用于反馈控制环）
        """
        self.project_root = Path(project_root)
        self.max_iterations = max_iterations
        self.task_file = task_file
        
        # Cybernetics 反馈控制环集成（v2.5 新增）
        self.cybernetics = cybernetics
        
        # 技能目录
        self.skill_root = Path(__file__).parent.parent
        
        # 初始化双层上下文管理器
        if CONTEXT_MANAGER_AVAILABLE:
            self.context_manager = DualLayerContextManager(
                project_root=str(self.project_root),
                skill_root=str(self.skill_root)
            )
            print(f"✅ 双层上下文管理器已初始化")
        else:
            self.context_manager = None
            print(f"⚠️  双层上下文管理器未初始化")
        
        # 进度文件
        self.progress_file = self.skill_root / "progress" / "agent_loop.json"
        self.progress_file.parent.mkdir(parents=True, exist_ok=True)
        
        # 加载进度
        self.loop_progress = self._load_loop_progress()
        
        # 迭代计数器
        self.iteration_count = self.loop_progress.get("iteration_count", 0)
        
        # 当前任务上下文
        self.current_task_ctx = None
    
    def _load_loop_progress(self) -> Dict:
        """加载循环进度"""
        if self.progress_file.exists():
            try:
                with open(self.progress_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"加载循环进度失败：{e}")
                return self._create_empty_loop_progress()
        return self._create_empty_loop_progress()
    
    def _create_empty_loop_progress(self) -> Dict:
        """创建空的循环进度"""
        return {
            "iteration_count": 0,
            "start_time": None,
            "last_update": None,
            "current_task": None,
            "tasks_completed": [],
            "tasks_failed": [],
            "tasks_pending": [],
            "last_action": None,
            "context": {},
            "exit_reason": None
        }
    
    def save_loop_progress(self):
        """保存循环进度"""
        self.loop_progress["last_update"] = datetime.now().isoformat()
        self.loop_progress["iteration_count"] = self.iteration_count
        
        try:
            with open(self.progress_file, 'w', encoding='utf-8') as f:
                json.dump(self.loop_progress, f, indent=2, ensure_ascii=False)
            print(f"✅ 循环进度已保存：{self.progress_file}")
        except Exception as e:
            print(f"❌ 保存循环进度失败：{e}")
    
    def start_task(self, task_id: str, task_description: str) -> bool:
        """
        开始任务（v2.0 - 使用双层上下文）
        
        Args:
            task_id: 任务 ID
            task_description: 任务描述
            
        Returns:
            bool: 是否成功启动
        """
        print(f"\n🚀 开始任务：{task_id}")
        print(f"📝 任务描述：{task_description}")
        
        # 更新进度
        self.loop_progress["current_task"] = task_id
        if task_id not in self.loop_progress["tasks_pending"]:
            self.loop_progress["tasks_pending"].append(task_id)
        self.save_loop_progress()
        
        # 使用双层上下文管理器
        if self.context_manager:
            # 创建任务定义
            task_def = TaskDefinition(
                task_id=task_id,
                title=task_description,
                description=task_description,
                goals=[task_description],
                constraints=[]
            )
            
            # 启动任务（自动注入相关知识）
            self.current_task_ctx = self.context_manager.start_task(task_def)
            
            # 记录到上下文
            if self.current_task_ctx:
                self.current_task_ctx.add_thought(
                    role="controller",
                    thought_type="analysis",
                    content=f"任务 {task_id} 已启动",
                    context={
                        "iteration": self.iteration_count,
                        "timestamp": datetime.now().isoformat()
                    }
                )
            
            print(f"✅ 任务上下文已创建，相关知识已注入")
            return True
        else:
            # 不使用上下文管理器
            print(f"⚠️  使用简化模式，未创建任务上下文")
            return True
    
    def complete_task(self, task_id: str, success: bool = True, 
                     artifacts: Dict = None) -> bool:
        """
        完成任务（v2.0 - 自动沉淀经验）
        
        Args:
            task_id: 任务 ID
            success: 是否成功完成
            artifacts: 任务产出
            
        Returns:
            bool: 是否成功完成
        """
        print(f"\n{'✅' if success else '❌'} 完成任务：{task_id}")
        
        # 更新进度
        if task_id in self.loop_progress["tasks_pending"]:
            self.loop_progress["tasks_pending"].remove(task_id)
        
        if success:
            if task_id not in self.loop_progress["tasks_completed"]:
                self.loop_progress["tasks_completed"].append(task_id)
        else:
            if task_id not in self.loop_progress["tasks_failed"]:
                self.loop_progress["tasks_failed"].append(task_id)
        
        self.loop_progress["current_task"] = None
        self.save_loop_progress()
        
        # 使用双层上下文管理器
        if self.context_manager and self.current_task_ctx:
            # 添加工件
            if artifacts:
                for artifact_type, artifact_data in artifacts.items():
                    self.current_task_ctx.add_artifact(artifact_type, artifact_data)
            
            # 记录完成思考
            self.current_task_ctx.add_thought(
                role="controller",
                thought_type="decision",
                content=f"任务 {'成功完成' if success else '失败'}",
                context={
                    "success": success,
                    "artifacts_count": len(artifacts) if artifacts else 0
                }
            )
            
            # 完成任务（自动沉淀经验）
            self.context_manager.complete_task(task_id)
            
            print(f"✅ 任务经验已沉淀到全局上下文")
            
            # 显示统计
            stats = self.context_manager.get_statistics()
            print(f"📊 全局上下文版本：{stats['global_context']['version']}")
            print(f"📚 知识库条目：{stats['global_context']['knowledge_count']}")
            print(f"💡 经验库条目：{stats['global_context']['experience_count']}")
            
            self.current_task_ctx = None
            
            return True
        else:
            print(f"⚠️  使用简化模式，未沉淀经验")
            return True
    
    def check_all_tasks_completed(self) -> bool:
        """
        检查所有任务是否完成
        
        Returns:
            bool: 是否全部完成
        """
        pending_count = len(self.loop_progress.get("tasks_pending", []))
        return pending_count == 0
    
    def run_loop(self, tasks: List[Dict], task_executor=None) -> Dict:
        """
        运行循环（v2.5 - 集成 Cybernetics 反馈控制环）
        
        完整流程：
        1. 感知阶段：收集当前状态（Cybernetics 增强）
        2. 决策阶段：基于案例选择策略（Cybernetics 增强）
        3. 执行阶段：执行任务并记录结果
        4. 反馈阶段：收集反馈并更新案例库（Cybernetics 增强）
        
        Args:
            tasks: 任务列表
            task_executor: 任务执行函数
            
        Returns:
            Dict: 执行结果
        """
        print(f"\n🚀 Trae Multi-Agent 循环控制器 v2.5 启动")
        print(f"📋 任务数量：{len(tasks)}")
        print(f"🔄 最大迭代次数：{self.max_iterations}")
        
        if self.context_manager:
            print(f"✅ 双层上下文管理器：已启用")
        else:
            print(f"⚠️  双层上下文管理器：未启用")
        
        if self.cybernetics:
            print(f"✅ Cybernetics 反馈控制环：已启用")
        else:
            print(f"⚠️  Cybernetics 反馈控制环：未启用")
        
        self.loop_progress["start_time"] = datetime.now().isoformat()
        self.loop_progress["tasks_pending"] = [t["id"] for t in tasks]
        self.loop_progress["tasks_completed"] = []
        self.loop_progress["tasks_failed"] = []
        
        start_time = time.time()
        
        for task in tasks:
            task_id = task["id"]
            task_description = task.get("description", task_id)
            
            # 检查迭代次数
            if self.iteration_count >= self.max_iterations:
                print(f"\n❌ 达到最大迭代次数：{self.max_iterations}")
                self.loop_progress["exit_reason"] = "max_iterations_reached"
                self.save_loop_progress()
                break
            
            # Cybernetics 增强：执行前验证
            if self.cybernetics:
                task_dict = {
                    'id': task_id,
                    'type': task.get('type', 'unknown'),
                    'complexity': task.get('complexity', 5),
                    'description': task_description
                }
                try:
                    validation = self.cybernetics.pre_execute_validation(task_dict)
                    if not validation.get('passed', True):
                        print(f"⚠️  Guard 验证警告：{validation.get('warnings', [])}")
                except Exception as e:
                    print(f"⚠️  Guard 预验证异常：{e}")
            
            # 开始任务
            self.start_task(task_id, task_description)
            self.iteration_count += 1
            
            # 执行任务
            if task_executor:
                success, artifacts = task_executor(task_id, task_description)
            else:
                # 默认执行器（模拟）
                print(f"▶️  执行任务：{task_id}")
                time.sleep(0.1)  # 模拟执行
                success = True
                artifacts = {}
            
            # Cybernetics 增强：反馈收集
            if self.cybernetics:
                try:
                    task_dict = {
                        'id': task_id,
                        'type': task.get('type', 'unknown'),
                        'complexity': task.get('complexity', 5),
                        'description': task_description
                    }
                    self.cybernetics.execute_with_feedback(
                        task_dict,
                        executor=lambda t: {
                            'success': success,
                            'task_id': task_id,
                            'artifacts': artifacts
                        }
                    )
                except Exception as e:
                    print(f"⚠️  Cybernetics 反馈收集异常：{e}")
            
            # 完成任务
            self.complete_task(task_id, success, artifacts)
            
            # 检查是否全部完成
            if self.check_all_tasks_completed():
                print(f"\n✅ 所有任务已完成！")
                self.loop_progress["exit_reason"] = "all_tasks_completed"
                break
        
        end_time = time.time()
        elapsed_time = end_time - start_time
        
        # 最终统计
        result = {
            "success": self.check_all_tasks_completed(),
            "tasks_total": len(tasks),
            "tasks_completed": len(self.loop_progress["tasks_completed"]),
            "tasks_failed": len(self.loop_progress["tasks_failed"]),
            "tasks_pending": len(self.loop_progress["tasks_pending"]),
            "iterations": self.iteration_count,
            "elapsed_time": f"{elapsed_time:.2f}s",
            "exit_reason": self.loop_progress.get("exit_reason", "unknown")
        }
        
        # 显示最终统计
        print(f"\n📊 最终统计:")
        print(f"  总任务数：{result['tasks_total']}")
        print(f"  已完成：{result['tasks_completed']}")
        print(f"  失败：{result['tasks_failed']}")
        print(f"  待处理：{result['tasks_pending']}")
        print(f"  迭代次数：{result['iterations']}")
        print(f"  耗时：{result['elapsed_time']}")
        print(f"  退出原因：{result['exit_reason']}")
        
        if self.context_manager:
            stats = self.context_manager.get_statistics()
            print(f"\n🧠 上下文统计:")
            print(f"  全局上下文版本：{stats['global_context']['version']}")
            print(f"  知识库条目：{stats['global_context']['knowledge_count']}")
            print(f"  经验库条目：{stats['global_context']['experience_count']}")
        
        # Cybernetics 增强统计
        if self.cybernetics:
            try:
                cyber_stats = self.cybernetics.get_statistics()
                print(f"\n🔄 Cybernetics 统计:")
                print(f"  反馈循环调用：{cyber_stats['metrics']['feedback_loop_calls']}")
                print(f"  反馈成功率：{cyber_stats['metrics']['feedback_success_rate']:.1%}")
                print(f"  Guard 验证次数：{cyber_stats['metrics']['guard_validations']}")
                print(f"  性能画像记录：{cyber_stats['metrics']['fingerprint_records']}")
            except Exception:
                pass
        
        self.save_loop_progress()
        
        return result


def main():
    """示例用法"""
    # 创建控制器
    controller = AgentLoopControllerV2(
        project_root=".",
        max_iterations=100
    )
    
    # 示例任务
    tasks = [
        {"id": "TASK-001", "description": "需求分析"},
        {"id": "TASK-002", "description": "架构设计"},
        {"id": "TASK-003", "description": "代码实现"},
        {"id": "TASK-004", "description": "测试验证"}
    ]
    
    # 运行循环
    result = controller.run_loop(tasks)
    
    print(f"\n{'✅' if result['success'] else '❌'} 循环执行完成")
    
    return 0 if result['success'] else 1


if __name__ == '__main__':
    sys.exit(main())
