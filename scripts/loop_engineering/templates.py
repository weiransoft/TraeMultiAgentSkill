"""Loop Engineering 业务 Loop 模板。

本模块定义设计、编码、测试三类业务场景的 Loop 模板：
- 设计 Loop：产出架构/需求/接口设计文档，强调 adversarial-verify。
- 编码 Loop：完成代码实现、测试、提交，强调客观指标通过。
- 测试 Loop：补充/运行/修复测试并提升覆盖率，强调新增测试真实性。

模板是声明式 + 策略式的：模板不直接执行代码，而是根据 Discovery 结果生成
HandoffItem（工作项），并提供验收标准建议。执行仍由 HandoffAdapter 完成。
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional

from loop_engineering.models import DiscoveryResult, HandoffItem, LoopType


class LoopTemplate(ABC):
    """业务 Loop 模板抽象基类。

    子类需要实现：
    - name: 模板名称
    - loop_type: 对应的 LoopType
    - create_work_items: 根据 Discovery 结果生成工作项
    - default_acceptance_criteria: 返回默认验收标准
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """模板名称（人类可读）。"""
        ...

    @property
    @abstractmethod
    def loop_type(self) -> LoopType:
        """对应的 Loop 类型。"""
        ...

    @abstractmethod
    def create_work_items(
        self,
        discovery: DiscoveryResult,
    ) -> List[HandoffItem]:
        """根据 Discovery 结果生成工作项列表。

        Args:
            discovery: Discovery 阶段结果。

        Returns:
            List[HandoffItem]: 工作项列表。
        """
        ...

    def default_acceptance_criteria(self, objective: str) -> List[str]:
        """返回默认验收标准。

        Args:
            objective: 本轮目标。

        Returns:
            List[str]: 验收标准列表。
        """
        return [
            f"目标 '{objective[:80]}' 已完成",
            "无 blocker 级别问题",
        ]

    def _generate_item_id(self) -> str:
        """生成工作项唯一标识。"""
        return f"wi-{uuid.uuid4().hex[:8]}"

    def _build_task_prompt(
        self,
        objective: str,
        context: str,
        deliverables: str,
        criteria: List[str],
    ) -> str:
        """构建发送给子代理的任务提示。

        Args:
            objective: 目标。
            context: 上下文信息。
            deliverables: 期望产出。
            criteria: 验收标准。

        Returns:
            str: 完整任务提示。
        """
        criteria_text = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(criteria))
        return (
            f"## 目标\n{objective}\n\n"
            f"## 上下文\n{context}\n\n"
            f"## 期望产出\n{deliverables}\n\n"
            f"## 验收标准\n{criteria_text}\n\n"
            "请在完成后以结构化方式报告结果，包括：是否成功、产出文件列表、"
            "测试结果摘要、lint/typecheck 结果、安全扫描结果。"
        )


class DesignLoopTemplate(LoopTemplate):
    """设计 Loop 模板。

    适用场景：
    - 产出或更新架构/需求/接口设计文档
    - 评审现有设计是否满足约束

    推荐智能体：architect / product-manager
    产出位置：docs/design/ 或 docs/spec/
    """

    @property
    def name(self) -> str:
        return "design-loop-template"

    @property
    def loop_type(self) -> LoopType:
        return LoopType.DESIGN

    def default_acceptance_criteria(self, objective: str) -> List[str]:
        """设计 Loop 验收标准。"""
        return [
            f"已产出针对 '{objective[:80]}' 的设计文档",
            "文档中明确定义了成功标准和验收条件",
            "设计未违反项目技术栈约束（检查 .trae/autonomous.yml 与 SKILL.md）",
            "关键设计决策经过 adversarial-verify 评审",
            "设计范围符合 YAGNI 原则，无过度工程",
        ]

    def create_work_items(
        self,
        discovery: DiscoveryResult,
    ) -> List[HandoffItem]:
        """生成设计 Loop 工作项。

        设计 Loop 通常拆分为两个工作项：
        1. architect 产出设计方案
        2. product-manager 进行 adversarial-verify 评审

        Args:
            discovery: Discovery 结果。

        Returns:
            List[HandoffItem]: 设计工作项列表。
        """
        objective = discovery.objective or discovery.inferred_goal
        criteria = self.default_acceptance_criteria(objective)
        context_parts: List[str] = []

        if discovery.detected_risks:
            context_parts.append(
                "识别到以下风险，请优先处理：\n- "
                + "\n- ".join(discovery.detected_risks)
            )
        if discovery.relevant_skills:
            context_parts.append("相关 skills：" + ", ".join(discovery.relevant_skills))
        if discovery.artifacts_to_read:
            context_parts.append(
                "建议阅读工件：\n- "
                + "\n- ".join(str(a) for a in discovery.artifacts_to_read)
            )

        context = "\n\n".join(context_parts) or "无额外上下文"
        deliverables = (
            "在 docs/design/ 或 docs/spec/ 下创建或更新设计文档，"
            "包含架构图、接口定义、数据模型、错误处理策略。"
        )

        architect_item = HandoffItem(
            item_id=self._generate_item_id(),
            agent_type="architect",
            task=self._build_task_prompt(
                objective=objective,
                context=context,
                deliverables=deliverables,
                criteria=criteria,
            ),
            acceptance_criteria=criteria,
            worktree_path=Path(discovery.context_features.get("project_root", ".")),
            metadata={
                "loop_type": self.loop_type.value,
                "template": self.name,
                "risks": discovery.detected_risks,
                "inferred_goal": discovery.inferred_goal,
            },
        )

        # 如果 Discovery 推荐 adversarial-verify，增加评审工作项
        review_item: Optional[HandoffItem] = None
        if "adversarial-verify" in discovery.suggested_patterns:
            review_criteria = [
                "设计方案满足原始目标",
                "已识别并缓解主要风险",
                "接口定义无歧义",
                "未引入不必要的技术债务",
            ]
            review_item = HandoffItem(
                item_id=self._generate_item_id(),
                agent_type="product-manager",
                task=self._build_task_prompt(
                    objective=f"对以下设计方案进行 adversarial-verify 评审：{objective}",
                    context="你是评审者，必须独立、批判性地检查设计方案。",
                    deliverables="产出评审意见：通过/不通过，并列出不通过的具体问题。",
                    criteria=review_criteria,
                ),
                acceptance_criteria=review_criteria,
                worktree_path=Path(discovery.context_features.get("project_root", ".")),
                dependencies=[architect_item.item_id],
                metadata={
                    "loop_type": self.loop_type.value,
                    "template": self.name,
                    "role": "reviewer",
                },
            )

        if review_item is not None:
            return [architect_item, review_item]
        return [architect_item]


class CodingLoopTemplate(LoopTemplate):
    """编码 Loop 模板。

    适用场景：
    - 根据设计文档或需求完成代码实现
    - 修复 bug 或新增 feature
    - 运行测试并提交

    推荐智能体：solo-coder
    阶段顺序：plan → dev → verify → fix
    """

    @property
    def name(self) -> str:
        return "coding-loop-template"

    @property
    def loop_type(self) -> LoopType:
        return LoopType.CODING

    def default_acceptance_criteria(self, objective: str) -> List[str]:
        """编码 Loop 验收标准。"""
        return [
            f"已实现 '{objective[:80]}' 并通过本地验证",
            "新增/修改代码有对应的单元测试覆盖",
            "测试命令成功通过（python3 -m unittest discover）",
            "ruff / mypy 等静态检查无 blocker 问题",
            "安全扫描无 critical/high 级别问题",
            "变更已提交到 git（若 auto_commit 启用）",
        ]

    def create_work_items(
        self,
        discovery: DiscoveryResult,
    ) -> List[HandoffItem]:
        """生成编码 Loop 工作项。

        编码 Loop 通常由 solo-coder 使用 RalphLoopController 执行 plan/dev/verify/fix
        四阶段。HandoffItem 携带完整上下文和验收标准。

        Args:
            discovery: Discovery 结果。

        Returns:
            List[HandoffItem]: 编码工作项列表（通常为 1 个）。
        """
        objective = discovery.objective or discovery.inferred_goal
        criteria = self.default_acceptance_criteria(objective)
        context_parts: List[str] = []

        if discovery.detected_risks:
            context_parts.append(
                "识别到以下风险，请在实现中优先处理：\n- "
                + "\n- ".join(discovery.detected_risks)
            )
        if discovery.relevant_skills:
            context_parts.append(
                "可使用的 skills：" + ", ".join(discovery.relevant_skills)
            )
        if discovery.artifacts_to_read:
            context_parts.append(
                "必须阅读的设计/规范工件：\n- "
                + "\n- ".join(str(a) for a in discovery.artifacts_to_read)
            )

        context = "\n\n".join(context_parts) or "无额外上下文"
        deliverables = (
            "1. 制定实现计划\n"
            "2. 编写/修改代码\n"
            "3. 运行测试与静态检查\n"
            "4. 修复发现的问题\n"
            "5. 提交变更（若允许）"
        )

        return [
            HandoffItem(
                item_id=self._generate_item_id(),
                agent_type="solo-coder",
                task=self._build_task_prompt(
                    objective=objective,
                    context=context,
                    deliverables=deliverables,
                    criteria=criteria,
                ),
                acceptance_criteria=criteria,
                worktree_path=Path(discovery.context_features.get("project_root", ".")),
                metadata={
                    "loop_type": self.loop_type.value,
                    "template": self.name,
                    "stage_order": ["plan", "dev", "verify", "fix"],
                    "risks": discovery.detected_risks,
                    "inferred_goal": discovery.inferred_goal,
                },
            )
        ]


class TestingLoopTemplate(LoopTemplate):
    """测试 Loop 模板。

    适用场景：
    - 根据代码变更补充测试用例
    - 提升覆盖率
    - 识别并修复 flaky 测试

    推荐智能体：test-expert
    """

    @property
    def name(self) -> str:
        return "testing-loop-template"

    @property
    def loop_type(self) -> LoopType:
        return LoopType.TESTING

    def default_acceptance_criteria(self, objective: str) -> List[str]:
        """测试 Loop 验收标准。"""
        return [
            f"已针对 '{objective[:80]}' 补充或修复测试用例",
            "新增测试真实覆盖代码变更路径（非重复/无意义测试）",
            "测试套件稳定运行，无 flaky 表现",
            "覆盖率未下降，目标提升达到预期",
            "测试命名清晰，断言意图明确",
        ]

    def create_work_items(
        self,
        discovery: DiscoveryResult,
    ) -> List[HandoffItem]:
        """生成测试 Loop 工作项。

        测试 Loop 由 test-expert 分析代码变更、生成并筛选测试用例，
        运行测试并修复 flaky 问题。

        Args:
            discovery: Discovery 结果。

        Returns:
            List[HandoffItem]: 测试工作项列表（通常为 1 个）。
        """
        objective = discovery.objective or discovery.inferred_goal
        criteria = self.default_acceptance_criteria(objective)
        context_parts: List[str] = []

        if discovery.detected_risks:
            context_parts.append(
                "识别到以下风险，请在测试中覆盖：\n- "
                + "\n- ".join(discovery.detected_risks)
            )
        if discovery.artifacts_to_read:
            context_parts.append(
                "需阅读的代码/测试工件：\n- "
                + "\n- ".join(str(a) for a in discovery.artifacts_to_read)
            )

        context = "\n\n".join(context_parts) or "无额外上下文"
        deliverables = (
            "1. 分析代码变更和现有测试缺口\n"
            "2. 生成候选测试用例并筛选高价值用例\n"
            "3. 运行测试并检查覆盖率\n"
            "4. 修复 flaky 测试\n"
            "5. 提交通过的测试变更"
        )

        return [
            HandoffItem(
                item_id=self._generate_item_id(),
                agent_type="test-expert",
                task=self._build_task_prompt(
                    objective=objective,
                    context=context,
                    deliverables=deliverables,
                    criteria=criteria,
                ),
                acceptance_criteria=criteria,
                worktree_path=Path(discovery.context_features.get("project_root", ".")),
                metadata={
                    "loop_type": self.loop_type.value,
                    "template": self.name,
                    "pattern": "generate-filter",
                    "risks": discovery.detected_risks,
                    "inferred_goal": discovery.inferred_goal,
                },
            )
        ]


class LoopTemplateRegistry:
    """Loop 模板注册表。

    提供从 LoopType 到 LoopTemplate 的映射，便于 HandoffAdapter 统一选择模板。
    """

    def __init__(self) -> None:
        """初始化注册表，注册所有内置模板。"""
        self._templates: Dict[LoopType, LoopTemplate] = {}
        self.register(DesignLoopTemplate())
        self.register(CodingLoopTemplate())
        self.register(TestingLoopTemplate())

    def register(self, template: LoopTemplate) -> None:
        """注册模板。

        Args:
            template: 业务 Loop 模板实例。
        """
        self._templates[template.loop_type] = template

    def get_template(self, loop_type: LoopType) -> LoopTemplate:
        """根据 Loop 类型获取模板。

        Args:
            loop_type: Loop 类型。

        Returns:
            LoopTemplate: 对应的模板实例。

        Raises:
            ValueError: 未找到对应模板时抛出。
        """
        if loop_type not in self._templates:
            raise ValueError(f"未找到 Loop 类型 {loop_type.value} 对应的模板")
        return self._templates[loop_type]

    def list_templates(self) -> List[LoopTemplate]:
        """列出所有已注册模板。

        Returns:
            List[LoopTemplate]: 模板列表。
        """
        return list(self._templates.values())


__all__ = [
    "LoopTemplate",
    "DesignLoopTemplate",
    "CodingLoopTemplate",
    "TestingLoopTemplate",
    "LoopTemplateRegistry",
]
