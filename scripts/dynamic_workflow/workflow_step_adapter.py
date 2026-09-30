#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkflowStep 适配器（V2 引擎扩展点）

Phase 1+2+3+4 累计实现：
- 不修改 v2 任何文件
- 提供辅助函数让 V2 调用方识别 "pattern:<pattern_id>" 格式 action
- 提供 step → PatternExecutor 输入的转换
- Phase 2+3+4: PatternExecutorRegistry 内置 sandbox / router / budget_guard

V2 引擎集成方式（约定式）：
- V2 用户在 WorkflowStep.action 字段填入 "pattern:fan-out-aggregate" 等
- 在调用 engine.execute() 之前，本适配器提供 is_pattern_action() 判断
- 是 pattern action → 路由到 PatternExecutorRegistry
- 不是 pattern action → 走 V2 原生流程

命名约定（与 V2 完全兼容）：
- "pattern:<pattern_id>" - PatternExecutor 调用
- 其他字符串 - V2 原生处理

作者：trae-multi-agent 融合 Phase 1+2+3+4
创建日期：2026-06-03
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pattern_executor import (
    ExecutionResult,
    PatternExecutorRegistry,
    execute_pattern,
)

logger = logging.getLogger("dynamic_workflow.workflow_step_adapter")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ============================================================================
# 命名约定
# ============================================================================

# pattern action 的正则模式：pattern:<pattern_id>
# pattern_id 必须为 kebab-case（与 WorkflowPattern.pattern_id 一致）
PATTERN_ACTION_PATTERN = re.compile(
    r"^pattern:(?P<pattern_id>[a-z][a-z0-9-]*[a-z0-9])$"
)


def is_pattern_action(action: str) -> bool:
    """
    判断 action 字符串是否是 pattern action

    Args:
        action: WorkflowStep.action 字符串

    Returns:
        bool: True 表示是 pattern action
    """
    if not isinstance(action, str):
        return False
    return PATTERN_ACTION_PATTERN.match(action) is not None


def parse_pattern_action(action: str) -> Optional[str]:
    """
    从 action 字符串提取 pattern_id

    Args:
        action: WorkflowStep.action 字符串（"pattern:<pattern_id>" 格式）

    Returns:
        Optional[str]: pattern_id；不匹配返回 None
    """
    if not isinstance(action, str):
        return None
    match = PATTERN_ACTION_PATTERN.match(action)
    if match is None:
        return None
    return match.group("pattern_id")


# ============================================================================
# WorkflowStep → PatternExecutor 输入转换
# ============================================================================

def workflow_step_to_pattern_input(
    step: Any,
    instance: Any = None,
) -> Dict[str, Any]:
    """
    将 V2 的 WorkflowStep 转换为 PatternExecutor 输入

    V2 WorkflowStep 字段（参考 workflow_engine_v2.py）：
    - step_id: str
    - step_name: str
    - action: str
    - role_id: str
    - inputs: Dict
    - outputs: Dict
    - conditions: Dict
    - retry_count: int

    PatternExecutor 输入字段：
    - description: str  （必填）
    - 其他字段从 inputs 提取

    Args:
        step: V2 WorkflowStep 对象（或 duck-typed dict）
        instance: V2 WorkflowInstance 对象（可选，用于上下文信息）

    Returns:
        Dict: PatternExecutor 输入
    """
    # duck-typed 提取（兼容对象和字典）
    def _get(obj: Any, key: str, default: Any = None) -> Any:
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    step_inputs: Dict[str, Any] = _get(step, "inputs", {}) or {}
    step_outputs: Dict[str, Any] = _get(step, "outputs", {}) or {}

    # 合并：description 优先取 inputs.description
    description = (
        step_inputs.get("description")
        or _get(step, "step_name")
        or _get(step, "step_id")
        or "未提供任务描述"
    )

    # 构建 PatternExecutor 输入
    pattern_input: Dict[str, Any] = {
        "description": description,
        "step_id": _get(step, "step_id"),
        "step_name": _get(step, "step_name"),
        "role_id": _get(step, "role_id"),
        "inputs": step_inputs,
        "outputs": step_outputs,
    }

    # 透传 task_type（如果有）
    if "task_type" in step_inputs:
        pattern_input["task_type"] = step_inputs["task_type"]
    if "task_complexity" in step_inputs:
        pattern_input["task_complexity"] = step_inputs["task_complexity"]

    # 透传 chunks（fan-out 用）
    if "chunks" in step_inputs:
        pattern_input["chunks"] = step_inputs["chunks"]

    # 透传 evaluation_criteria（adversarial-verify 用）
    if "evaluation_criteria" in step_inputs:
        pattern_input["evaluation_criteria"] = step_inputs["evaluation_criteria"]

    # 透传 instance 上下文（如果有）
    if instance is not None:
        pattern_input["instance_id"] = _get(instance, "instance_id", None)
        pattern_input["workflow_id"] = _get(instance, "workflow_id", None)

    return pattern_input


def workflow_step_to_pattern_parameters(
    step: Any,
) -> Dict[str, Any]:
    """
    从 WorkflowStep 中提取 pattern 参数

    约定：
    - inputs 中以 "pattern_" 开头的字段 → 作为 pattern 参数
    - inputs 中无前缀的字段 → 不作为 pattern 参数（透传给 subagent 输入）

    Args:
        step: V2 WorkflowStep 对象

    Returns:
        Dict: pattern 参数
    """
    step_inputs: Dict[str, Any] = {}
    if isinstance(step, dict):
        step_inputs = step.get("inputs", {}) or {}
    else:
        step_inputs = getattr(step, "inputs", {}) or {}

    pattern_params: Dict[str, Any] = {}
    for key, value in step_inputs.items():
        if key.startswith("pattern_"):
            # 去除 "pattern_" 前缀
            pattern_params[key[len("pattern_"):]] = value

    return pattern_params


# ============================================================================
# V2 调用入口
# ============================================================================

def execute_workflow_step(
    step: Any,
    instance: Any = None,
    registry: Optional[PatternExecutorRegistry] = None,
) -> Optional[ExecutionResult]:
    """
    执行 V2 WorkflowStep（如果是 pattern action）

    调用方在 V2 引擎主循环中：
    ```python
    # 在 v2 引擎的 _execute_step 中
    if is_pattern_action(step.action):
        result = execute_workflow_step(step, instance, registry)
        if result is not None:
            # 写入 step.outputs 或 instance 状态
            return result
    # 否则走 V2 原生逻辑
    ```

    Phase 4 行为：
    - 如果 registry 内置了 sandbox / router / budget_guard（来自 create_default），
      PatternExecutor 会自动使用，无需本函数额外处理
    - registry.get_dispatch_context() 可获取所有 dispatch 资源

    Args:
        step: V2 WorkflowStep
        instance: V2 WorkflowInstance（可选）
        registry: PatternExecutorRegistry

    Returns:
        Optional[ExecutionResult]: pattern action 时返回 ExecutionResult；
                                    非 pattern action 时返回 None（让 V2 走原生逻辑）
    """
    action = step.get("action") if isinstance(step, dict) else getattr(step, "action", None)
    if not is_pattern_action(action):
        return None  # 非 pattern action，让 V2 走原生

    pattern_id = parse_pattern_action(action)
    if pattern_id is None:
        logger.warning(f"action 解析失败：{action!r}")
        return None

    # 转换输入
    pattern_input = workflow_step_to_pattern_input(step, instance)
    pattern_params = workflow_step_to_pattern_parameters(step)

    # 优先使用 step 中显式指定的 pattern_id 参数（覆盖从 action 解析的）
    # 支持两种写法：
    #   - inputs.pattern_id = "x"           （直接）
    #   - inputs.pattern_pattern_id = "x"  （pattern_ 前缀）
    if "pattern_id" in pattern_params:
        pattern_id = pattern_params["pattern_id"]
    else:
        step_inputs = (
            step.get("inputs", {}) if isinstance(step, dict)
            else getattr(step, "inputs", {}) or {}
        )
        direct_pid = step_inputs.get("pattern_id")
        if isinstance(direct_pid, str) and is_pattern_action(f"pattern:{direct_pid}"):
            pattern_id = direct_pid

    # Phase 4: 记录 dispatch 上下文（用于审计/调试）
    if registry is not None:
        ctx = registry.get_dispatch_context()
        logger.debug(
            f"执行 pattern step: pattern_id={pattern_id}, "
            f"step={pattern_input.get('step_id')}, "
            f"dispatch_context_keys={list(ctx.keys())}"
        )
    else:
        logger.info(
            f"执行 pattern step: pattern_id={pattern_id}, "
            f"step={pattern_input.get('step_id')} (no registry)"
        )

    # 执行（registry 已绑定 sandbox/router/budget_guard）
    result = execute_pattern(
        pattern_id=pattern_id,
        task=pattern_input,
        parameters=pattern_params,
        registry=registry,
    )

    return result


# ============================================================================
# V2 WorkflowStep 构造辅助
# ============================================================================

def make_pattern_step(
    step_id: str,
    pattern_id: str,
    description: str,
    pattern_parameters: Optional[Dict[str, Any]] = None,
    role_id: Optional[str] = None,
    step_name: Optional[str] = None,
    chunks: Optional[list] = None,
    evaluation_criteria: Optional[list] = None,
    task_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    构造一个 pattern step 字典（V2 WorkflowStep duck-typed）

    方便 V2 用户快速创建 pattern step 而不用手写字典。

    Args:
        step_id: 步骤 ID
        pattern_id: 模式 ID（classifier-dispatch / fan-out-aggregate / adversarial-verify）
        description: 任务描述
        pattern_parameters: 模式参数（以 pattern_ 为前缀填入 inputs）
        role_id: 角色 ID（V2 必填）
        step_name: 步骤名
        chunks: fan-out 用分块
        evaluation_criteria: adversarial-verify 用准则
        task_type: 任务类型

    Returns:
        Dict: 符合 V2 WorkflowStep 结构的字典

    Example:
        ```python
        step = make_pattern_step(
            step_id="step1",
            pattern_id="fan-out-aggregate",
            description="审查 50 个文件",
            chunks=["file1.py", "file2.py"],
            pattern_parameters={
                "fanout_count": 5,
                "subagent_role": "test_expert",
                "aggregator_role": "architect",
                "aggregation_strategy": "merge",
            },
            role_id="test_expert",
        )
        # 生成的 step.action = "pattern:fan-out-aggregate"
        ```
    """
    inputs: Dict[str, Any] = {
        "description": description,
    }
    if chunks is not None:
        inputs["chunks"] = chunks
    if evaluation_criteria is not None:
        inputs["evaluation_criteria"] = evaluation_criteria
    if task_type is not None:
        inputs["task_type"] = task_type

    if pattern_parameters:
        for key, value in pattern_parameters.items():
            inputs[f"pattern_{key}"] = value

    return {
        "step_id": step_id,
        "step_name": step_name or f"Pattern: {pattern_id}",
        "action": f"pattern:{pattern_id}",
        "role_id": role_id or "solo_coder",
        "inputs": inputs,
        "outputs": {},
        "conditions": {},
        "retry_count": 0,
    }
