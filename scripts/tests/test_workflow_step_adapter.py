#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
workflow_step_adapter.py 单元测试

测试目标：
- pattern action 字符串解析（is_pattern_action / parse_pattern_action）
- V2 WorkflowStep → PatternExecutor 输入转换
- V2 WorkflowStep → pattern parameters 提取
- execute_workflow_step 端到端路由
- make_pattern_step 构造辅助
- V2 引擎集成（不修改 V2 文件）

测试约定：
- 使用 unittest 框架
- 不修改任何 V2 文件
- 通过 mock pattern_executor 来测试适配器逻辑

作者：trae-multi-agent 融合 Phase 1
创建日期：2026-06-03
"""

import sys
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

# 添加 scripts 目录到 sys.path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# 动态加载模块
DYNAMIC_WORKFLOW_DIR = SCRIPTS_DIR / "dynamic_workflow"
if str(DYNAMIC_WORKFLOW_DIR) not in sys.path:
    sys.path.insert(0, str(DYNAMIC_WORKFLOW_DIR))

import workflow_step_adapter  # noqa: E402
from workflow_step_adapter import (  # noqa: E402
    PATTERN_ACTION_PATTERN,
    execute_workflow_step,
    is_pattern_action,
    make_pattern_step,
    parse_pattern_action,
    workflow_step_to_pattern_input,
    workflow_step_to_pattern_parameters,
)
import pattern_executor  # noqa: E402
from pattern_executor import (  # noqa: E402
    ExecutionResult,
    ExecutionStatus,
    PatternExecutorRegistry,
)


# ============================================================================
# 1. pattern action 解析测试
# ============================================================================

class TestPatternActionParsing(unittest.TestCase):
    """pattern:<pattern_id> 字符串解析"""

    def test_01_valid_classifier_dispatch(self):
        """valid: pattern:classifier-dispatch"""
        self.assertTrue(is_pattern_action("pattern:classifier-dispatch"))
        self.assertEqual(parse_pattern_action("pattern:classifier-dispatch"),
                         "classifier-dispatch")

    def test_02_valid_fan_out_aggregate(self):
        """valid: pattern:fan-out-aggregate"""
        self.assertTrue(is_pattern_action("pattern:fan-out-aggregate"))
        self.assertEqual(parse_pattern_action("pattern:fan-out-aggregate"),
                         "fan-out-aggregate")

    def test_03_valid_adversarial_verify(self):
        """valid: pattern:adversarial-verify"""
        self.assertTrue(is_pattern_action("pattern:adversarial-verify"))
        self.assertEqual(parse_pattern_action("pattern:adversarial-verify"),
                         "adversarial-verify")

    def test_04_non_pattern_action_v2_native(self):
        """非 pattern action（V2 原生）"""
        self.assertFalse(is_pattern_action("execute_command"))
        self.assertFalse(is_pattern_action("read_file"))
        self.assertIsNone(parse_pattern_action("read_file"))

    def test_05_non_string_input(self):
        """非字符串输入"""
        self.assertFalse(is_pattern_action(None))
        self.assertFalse(is_pattern_action(123))
        self.assertFalse(is_pattern_action({}))
        self.assertFalse(is_pattern_action([]))
        self.assertIsNone(parse_pattern_action(None))
        self.assertIsNone(parse_pattern_action(123))

    def test_06_malformed_pattern_action(self):
        """格式错误的 pattern action"""
        # 缺少 prefix
        self.assertFalse(is_pattern_action("classifier-dispatch"))
        self.assertFalse(is_pattern_action(":classifier-dispatch"))
        # prefix 错误
        self.assertFalse(is_pattern_action("Pattern:classifier-dispatch"))
        self.assertFalse(is_pattern_action("PATTERN:classifier-dispatch"))
        # pattern_id 含大写
        self.assertFalse(is_pattern_action("pattern:Classifier-Dispatch"))
        # pattern_id 含特殊字符
        self.assertFalse(is_pattern_action("pattern:my_pattern"))
        self.assertFalse(is_pattern_action("pattern:my.pattern"))

    def test_07_empty_pattern_id(self):
        """空 pattern_id"""
        self.assertFalse(is_pattern_action("pattern:"))
        self.assertFalse(is_pattern_action("pattern: "))

    def test_08_kebab_case_only(self):
        """pattern_id 必须是 kebab-case（≥ 2 字符）"""
        # 边界：2 字符
        self.assertTrue(is_pattern_action("pattern:ab"))
        # 边界：数字结尾
        self.assertTrue(is_pattern_action("pattern:step-1"))
        # 不允许：单字符（regex 限制至少 2 字符）
        self.assertFalse(is_pattern_action("pattern:a"))
        # 不允许：下划线
        self.assertFalse(is_pattern_action("pattern:my_pattern"))
        # 不允许：以 - 开头
        self.assertFalse(is_pattern_action("pattern:-abc"))
        # 不允许：以 - 结尾
        self.assertFalse(is_pattern_action("pattern:abc-"))


# ============================================================================
# 2. WorkflowStep → PatternExecutor 输入转换测试
# ============================================================================

class TestWorkflowStepToPatternInput(unittest.TestCase):
    """WorkflowStep 转 PatternExecutor 输入"""

    def test_01_dict_step_with_description_in_inputs(self):
        """dict step，description 在 inputs 中"""
        step = {
            "step_id": "step1",
            "step_name": "审查代码",
            "action": "pattern:fan-out-aggregate",
            "role_id": "test_expert",
            "inputs": {"description": "审查 50 个文件", "chunks": ["a", "b", "c"]},
            "outputs": {},
        }
        result = workflow_step_to_pattern_input(step)
        self.assertEqual(result["description"], "审查 50 个文件")
        self.assertEqual(result["step_id"], "step1")
        self.assertEqual(result["step_name"], "审查代码")
        self.assertEqual(result["role_id"], "test_expert")
        self.assertEqual(result["inputs"], {"description": "审查 50 个文件", "chunks": ["a", "b", "c"]})

    def test_02_dict_step_with_no_description_falls_back_to_step_name(self):
        """dict step 无 description，回退到 step_name"""
        step = {
            "step_id": "step1",
            "step_name": "回退测试",
            "action": "pattern:foo",
            "role_id": "r",
            "inputs": {},
            "outputs": {},
        }
        result = workflow_step_to_pattern_input(step)
        self.assertEqual(result["description"], "回退测试")

    def test_03_dict_step_fallback_to_step_id(self):
        """dict step 无 step_name，回退到 step_id"""
        step = {
            "step_id": "step1",
            "step_name": None,
            "action": "pattern:foo",
            "role_id": "r",
            "inputs": {},
            "outputs": {},
        }
        result = workflow_step_to_pattern_input(step)
        self.assertEqual(result["description"], "step1")

    def test_04_task_type_passthrough(self):
        """task_type 透传"""
        step = {
            "step_id": "s1",
            "step_name": "n1",
            "action": "pattern:classifier-dispatch",
            "role_id": "r",
            "inputs": {"description": "x", "task_type": "code_review"},
            "outputs": {},
        }
        result = workflow_step_to_pattern_input(step)
        self.assertEqual(result["task_type"], "code_review")

    def test_05_chunks_passthrough(self):
        """chunks 透传（fan-out 用）"""
        chunks = ["a", "b", "c"]
        step = {
            "step_id": "s1",
            "step_name": "n1",
            "action": "pattern:fan-out-aggregate",
            "role_id": "r",
            "inputs": {"description": "x", "chunks": chunks},
            "outputs": {},
        }
        result = workflow_step_to_pattern_input(step)
        self.assertEqual(result["chunks"], chunks)

    def test_06_evaluation_criteria_passthrough(self):
        """evaluation_criteria 透传（adversarial-verify 用）"""
        criteria = ["c1", "c2", "c3"]
        step = {
            "step_id": "s1",
            "step_name": "n1",
            "action": "pattern:adversarial-verify",
            "role_id": "r",
            "inputs": {"description": "x", "evaluation_criteria": criteria},
            "outputs": {},
        }
        result = workflow_step_to_pattern_input(step)
        self.assertEqual(result["evaluation_criteria"], criteria)

    def test_07_instance_context(self):
        """instance 上下文透传"""
        step = {
            "step_id": "s1",
            "step_name": "n1",
            "action": "pattern:foo",
            "role_id": "r",
            "inputs": {"description": "x"},
            "outputs": {},
        }
        instance = {"instance_id": "inst_1", "workflow_id": "wf_1"}
        result = workflow_step_to_pattern_input(step, instance)
        self.assertEqual(result["instance_id"], "inst_1")
        self.assertEqual(result["workflow_id"], "wf_1")

    def test_08_object_step_duck_typed(self):
        """object step（duck-typed）支持"""

        class FakeStep:
            step_id = "s1"
            step_name = "n1"
            action = "pattern:foo"
            role_id = "r"
            inputs = {"description": "x"}
            outputs = {}

        result = workflow_step_to_pattern_input(FakeStep())
        self.assertEqual(result["description"], "x")
        self.assertEqual(result["step_id"], "s1")
        self.assertEqual(result["role_id"], "r")


# ============================================================================
# 3. WorkflowStep → pattern parameters 提取测试
# ============================================================================

class TestWorkflowStepToPatternParameters(unittest.TestCase):
    """WorkflowStep 转 pattern parameters"""

    def test_01_extract_pattern_prefixed_fields(self):
        """提取以 pattern_ 开头的字段"""
        step = {
            "step_id": "s1",
            "action": "pattern:fan-out-aggregate",
            "role_id": "r",
            "inputs": {
                "description": "x",
                "pattern_fanout_count": 5,
                "pattern_subagent_role": "test_expert",
                "pattern_aggregator_role": "architect",
            },
            "outputs": {},
        }
        result = workflow_step_to_pattern_parameters(step)
        self.assertEqual(result["fanout_count"], 5)
        self.assertEqual(result["subagent_role"], "test_expert")
        self.assertEqual(result["aggregator_role"], "architect")
        # description 不应被包含
        self.assertNotIn("description", result)

    def test_02_no_pattern_prefixed_fields(self):
        """无 pattern_ 字段时返回空 dict"""
        step = {
            "step_id": "s1",
            "action": "pattern:foo",
            "role_id": "r",
            "inputs": {"description": "x", "task_type": "general"},
            "outputs": {},
        }
        result = workflow_step_to_pattern_parameters(step)
        self.assertEqual(result, {})

    def test_03_empty_inputs(self):
        """inputs 为空"""
        step = {
            "step_id": "s1",
            "action": "pattern:foo",
            "role_id": "r",
            "inputs": {},
            "outputs": {},
        }
        result = workflow_step_to_pattern_parameters(step)
        self.assertEqual(result, {})

    def test_04_object_step_duck_typed(self):
        """object step 支持"""

        class FakeStep:
            step_id = "s1"
            inputs = {"description": "x", "pattern_token_budget": 5000}

        result = workflow_step_to_pattern_parameters(FakeStep())
        self.assertEqual(result, {"token_budget": 5000})


# ============================================================================
# 4. execute_workflow_step 端到端测试
# ============================================================================

class TestExecuteWorkflowStep(unittest.TestCase):
    """execute_workflow_step 端到端测试"""

    def setUp(self):
        """mock pattern_executor.execute_pattern（在 workflow_step_adapter 模块中）"""
        self._original_execute = workflow_step_adapter.execute_pattern
        self._captured_args = []

        def capture_execute(pattern_id, task, parameters, registry=None):
            self._captured_args.append({
                "pattern_id": pattern_id,
                "task": task,
                "parameters": parameters,
            })
            return ExecutionResult(
                pattern_id=pattern_id,
                status=ExecutionStatus.SUCCESS,
                aggregated_output={"mocked": True},
            )

        workflow_step_adapter.execute_pattern = capture_execute

    def tearDown(self):
        """清理"""
        workflow_step_adapter.execute_pattern = self._original_execute

    def test_01_non_pattern_action_returns_none(self):
        """非 pattern action 返回 None（让 V2 走原生）"""
        step = {
            "step_id": "s1",
            "action": "execute_command",
            "role_id": "r",
            "inputs": {},
        }
        result = execute_workflow_step(step)
        self.assertIsNone(result)
        # execute_pattern 不应被调用
        self.assertEqual(len(self._captured_args), 0)

    def test_02_pattern_action_routes_to_executor(self):
        """pattern action 路由到 executor"""
        step = {
            "step_id": "s1",
            "step_name": "n1",
            "action": "pattern:fan-out-aggregate",
            "role_id": "test_expert",
            "inputs": {
                "description": "审查文件",
                "chunks": ["a", "b", "c"],
                "pattern_fanout_count": 3,
                "pattern_subagent_role": "test_expert",
            },
            "outputs": {},
        }
        result = execute_workflow_step(step)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        # 验证 execute_pattern 被调用 1 次
        self.assertEqual(len(self._captured_args), 1)
        call = self._captured_args[0]
        self.assertEqual(call["pattern_id"], "fan-out-aggregate")
        self.assertEqual(call["task"]["description"], "审查文件")
        self.assertEqual(call["parameters"]["fanout_count"], 3)
        self.assertEqual(call["parameters"]["subagent_role"], "test_expert")

    def test_03_pattern_id_override(self):
        """pattern_id 参数可覆盖 action 解析的 pattern_id"""
        step = {
            "step_id": "s1",
            "action": "pattern:default-pattern",
            "role_id": "r",
            "inputs": {
                "description": "x",
                "pattern_id": "actual-pattern",  # 显式覆盖
            },
            "outputs": {},
        }
        execute_workflow_step(step)
        # 实际使用的 pattern_id 来自参数
        self.assertEqual(self._captured_args[0]["pattern_id"], "actual-pattern")

    def test_04_invalid_action_returns_none(self):
        """无效 action 返回 None"""
        step = {
            "step_id": "s1",
            "action": "pattern:",  # 空 pattern_id
            "role_id": "r",
            "inputs": {},
        }
        result = execute_workflow_step(step)
        self.assertIsNone(result)

    def test_05_object_step_duck_typed(self):
        """object step（duck-typed）支持"""

        class FakeStep:
            step_id = "s1"
            step_name = "n1"
            action = "pattern:fan-out-aggregate"
            role_id = "r"
            inputs = {"description": "x", "chunks": ["a", "b"]}
            outputs = {}

        result = execute_workflow_step(FakeStep())
        self.assertIsNotNone(result)
        self.assertEqual(self._captured_args[0]["pattern_id"], "fan-out-aggregate")

    def test_06_with_instance(self):
        """带 instance 时透传上下文"""
        step = {
            "step_id": "s1",
            "action": "pattern:foo",
            "role_id": "r",
            "inputs": {"description": "x"},
            "outputs": {},
        }
        instance = {"instance_id": "inst_1", "workflow_id": "wf_1"}
        execute_workflow_step(step, instance)
        self.assertEqual(self._captured_args[0]["task"]["instance_id"], "inst_1")
        self.assertEqual(self._captured_args[0]["task"]["workflow_id"], "wf_1")


# ============================================================================
# 5. make_pattern_step 构造辅助测试
# ============================================================================

class TestMakePatternStep(unittest.TestCase):
    """make_pattern_step 构造辅助"""

    def test_01_minimal(self):
        """最小构造"""
        step = make_pattern_step(
            step_id="s1",
            pattern_id="fan-out-aggregate",
            description="审查文件",
        )
        self.assertEqual(step["step_id"], "s1")
        self.assertEqual(step["action"], "pattern:fan-out-aggregate")
        self.assertEqual(step["role_id"], "solo_coder")  # 默认值
        self.assertEqual(step["inputs"]["description"], "审查文件")

    def test_02_with_chunks(self):
        """带 chunks"""
        chunks = ["a", "b", "c"]
        step = make_pattern_step(
            step_id="s1",
            pattern_id="fan-out-aggregate",
            description="x",
            chunks=chunks,
        )
        self.assertEqual(step["inputs"]["chunks"], chunks)

    def test_03_with_evaluation_criteria(self):
        """带 evaluation_criteria"""
        criteria = ["c1", "c2", "c3"]
        step = make_pattern_step(
            step_id="s1",
            pattern_id="adversarial-verify",
            description="x",
            evaluation_criteria=criteria,
        )
        self.assertEqual(step["inputs"]["evaluation_criteria"], criteria)

    def test_04_with_task_type(self):
        """带 task_type"""
        step = make_pattern_step(
            step_id="s1",
            pattern_id="classifier-dispatch",
            description="x",
            task_type="code_review",
        )
        self.assertEqual(step["inputs"]["task_type"], "code_review")

    def test_05_with_pattern_parameters(self):
        """带 pattern_parameters（自动加 pattern_ 前缀）"""
        step = make_pattern_step(
            step_id="s1",
            pattern_id="fan-out-aggregate",
            description="x",
            pattern_parameters={
                "fanout_count": 5,
                "subagent_role": "test_expert",
                "aggregator_role": "architect",
            },
        )
        self.assertEqual(step["inputs"]["pattern_fanout_count"], 5)
        self.assertEqual(step["inputs"]["pattern_subagent_role"], "test_expert")
        self.assertEqual(step["inputs"]["pattern_aggregator_role"], "architect")

    def test_06_with_custom_role_and_name(self):
        """自定义 role_id 和 step_name"""
        step = make_pattern_step(
            step_id="s1",
            pattern_id="fan-out-aggregate",
            description="x",
            role_id="architect",
            step_name="Custom Step Name",
        )
        self.assertEqual(step["role_id"], "architect")
        self.assertEqual(step["step_name"], "Custom Step Name")

    def test_07_default_step_name(self):
        """默认 step_name 自动生成"""
        step = make_pattern_step(
            step_id="s1",
            pattern_id="fan-out-aggregate",
            description="x",
        )
        self.assertEqual(step["step_name"], "Pattern: fan-out-aggregate")

    def test_08_v2_compatible_structure(self):
        """生成的 step 符合 V2 WorkflowStep 结构"""
        step = make_pattern_step(
            step_id="s1",
            pattern_id="fan-out-aggregate",
            description="x",
        )
        # V2 WorkflowStep 必填字段
        self.assertIn("step_id", step)
        self.assertIn("step_name", step)
        self.assertIn("action", step)
        self.assertIn("role_id", step)
        self.assertIn("inputs", step)
        self.assertIn("outputs", step)
        self.assertIn("conditions", step)
        self.assertIn("retry_count", step)
        # 默认值
        self.assertEqual(step["outputs"], {})
        self.assertEqual(step["conditions"], {})
        self.assertEqual(step["retry_count"], 0)


# ============================================================================
# 6. V2 引擎集成测试（不修改 V2）
# ============================================================================

class TestV2EngineIntegration(unittest.TestCase):
    """V2 引擎集成测试（不修改 V2 文件）"""

    def setUp(self):
        """mock execute_pattern（在 adapter 模块中）"""
        self._original_execute = workflow_step_adapter.execute_pattern
        workflow_step_adapter.execute_pattern = MagicMock(
            return_value=ExecutionResult(
                pattern_id="mocked",
                status=ExecutionStatus.SUCCESS,
            )
        )

    def tearDown(self):
        """清理"""
        workflow_step_adapter.execute_pattern = self._original_execute

    def test_01_simulation_v2_engine_loop(self):
        """模拟 V2 引擎主循环：识别 pattern action 并路由"""
        # 模拟 V2 工作流步骤
        steps = [
            {
                "step_id": "s1",
                "action": "execute_command",  # V2 原生
                "role_id": "r",
                "inputs": {"description": "原生命令"},
            },
            {
                "step_id": "s2",
                "action": "pattern:fan-out-aggregate",  # Pattern
                "role_id": "test_expert",
                "inputs": {"description": "扇出", "chunks": ["a", "b"]},
            },
            {
                "step_id": "s3",
                "action": "read_file",  # V2 原生
                "role_id": "r",
                "inputs": {"description": "读文件"},
            },
        ]

        results = []
        for step in steps:
            if is_pattern_action(step["action"]):
                # pattern action → 走适配器
                result = execute_workflow_step(step)
                results.append(("pattern", result))
            else:
                # V2 原生 → 走原生逻辑（这里只是标记）
                results.append(("v2_native", None))

        # s1 → v2_native
        self.assertEqual(results[0][0], "v2_native")
        # s2 → pattern + SUCCESS
        self.assertEqual(results[1][0], "pattern")
        self.assertIsNotNone(results[1][1])
        # s3 → v2_native
        self.assertEqual(results[2][0], "v2_native")

    def test_02_step_construction_to_execution_flow(self):
        """完整流程：make_pattern_step → is_pattern_action → execute_workflow_step"""
        # 1. 构造 step
        step = make_pattern_step(
            step_id="s1",
            pattern_id="fan-out-aggregate",
            description="审查",
            chunks=["a", "b", "c"],
            pattern_parameters={"fanout_count": 3, "subagent_role": "test_expert"},
        )

        # 2. 识别 pattern action
        self.assertTrue(is_pattern_action(step["action"]))

        # 3. 解析 pattern_id
        self.assertEqual(parse_pattern_action(step["action"]), "fan-out-aggregate")

        # 4. 转换输入
        pattern_input = workflow_step_to_pattern_input(step)
        self.assertEqual(pattern_input["description"], "审查")
        self.assertEqual(pattern_input["chunks"], ["a", "b", "c"])

        # 5. 提取 parameters
        params = workflow_step_to_pattern_parameters(step)
        self.assertEqual(params["fanout_count"], 3)
        self.assertEqual(params["subagent_role"], "test_expert")

        # 6. 执行
        result = execute_workflow_step(step)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
