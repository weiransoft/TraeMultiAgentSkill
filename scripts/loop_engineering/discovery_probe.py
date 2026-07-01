"""Loop Engineering Discovery 阶段。

负责感知项目上下文、历史记录、相关 skills 和潜在风险，
回答"本轮 Loop 该做什么"。

感知来源：
- 用户输入的 objective
- 项目根目录结构（README、docs/spec、SKILL.md）
- 历史 notes / state（通过 UnifiedMemoryLayer.query）
- 失败模式库（PerformanceFingerprint）

输出 DiscoveryResult，包含：
- 明确后的 objective
- 推荐 agent / pattern
- 检测到的风险
- 建议读取的 artifacts
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from loop_engineering.models import (
    DiscoveryMode,
    DiscoveryResult,
    LoopEngineeringConfig,
    LoopEvent,
    LoopType,
    MemoryQuery,
)
from loop_engineering.protocols import UnifiedMemoryLayerProtocol


class DiscoveryProbe:
    """Discovery 阶段实现：感知需求、上下文、风险、可用 skill。"""

    # 关键词到 loop type 的映射
    _LOOP_TYPE_KEYWORDS: Dict[LoopType, List[str]] = {
        LoopType.DESIGN: [
            "设计",
            "架构",
            "方案",
            "PRD",
            "需求",
            "接口",
            "design",
            "architecture",
        ],
        LoopType.CODING: [
            "实现",
            "编码",
            "开发",
            "修复",
            "bug",
            "feature",
            "code",
            "implement",
            "fix",
        ],
        LoopType.TESTING: [
            "测试",
            "用例",
            "覆盖率",
            "test",
            "coverage",
            "testing",
            "unit test",
        ],
    }

    # 风险关键词
    _RISK_KEYWORDS: Dict[str, List[str]] = {
        "大规模重构": ["重构", "重写", "重构", "大规模", "整体改造"],
        "跨文件修改": ["跨文件", "多处", "多个文件", "全局", "全链路"],
        "安全敏感": ["安全", "认证", "鉴权", "密码", "token", "加密", "权限"],
        "缺少测试": ["缺少测试", "无测试", "未测试", "测试不足"],
        "缺少设计文档": ["无 PRD", "无设计", "未设计", "缺少文档"],
        "技术栈变更": ["新框架", "新技术", "更换", "引入", "依赖升级"],
    }

    # loop type 到推荐 agent 的映射
    _AGENT_RECOMMENDATIONS: Dict[LoopType, List[str]] = {
        LoopType.DESIGN: ["architect", "product-manager", "ui-designer"],
        LoopType.CODING: ["solo-coder", "architect"],
        LoopType.TESTING: ["test-expert", "solo-coder"],
    }

    # loop type 到推荐 pattern 的映射
    _PATTERN_RECOMMENDATIONS: Dict[LoopType, List[str]] = {
        LoopType.DESIGN: ["adversarial-verify"],
        LoopType.CODING: ["loop-until-done"],
        LoopType.TESTING: ["generate-filter", "fan-out-aggregate"],
    }

    def __init__(
        self,
        config: LoopEngineeringConfig,
        log: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """构造 DiscoveryProbe。

        Args:
            config: Loop Engineering 配置。
            log: 日志回调函数（可选）。
        """
        self._config = config
        self._log = log

    def _info(self, message: str) -> None:
        """输出 INFO 级别日志。"""
        if self._log:
            self._log(message, "INFO")

    def discover(
        self,
        objective: str,
        prev_events: List[LoopEvent],
        memory: UnifiedMemoryLayerProtocol,
    ) -> DiscoveryResult:
        """执行 Discovery。

        Args:
            objective: 用户输入的目标。
            prev_events: 历史事件列表。
            memory: 统一 Memory 层。

        Returns:
            DiscoveryResult: Discovery 结果。
        """
        # MANUAL / OFF 模式：仅使用 objective，不扫描项目
        if self._config.discovery_mode == DiscoveryMode.OFF:
            return self._build_manual_result(objective)

        # 1. 推断 loop type
        inferred_loop_type = self._infer_loop_type(objective)

        # 2. 读取项目 artifacts
        artifacts = self._discover_artifacts(self._config.project_root)

        # 3. 检测风险
        risks = self._detect_risks(objective, artifacts)

        # 4. 查询历史记忆
        recent_notes = ""
        similar_cases: List[Dict[str, Any]] = []
        try:
            recent = memory.query(MemoryQuery(query_type="recent", limit=3))
            recent_notes = "\n".join(str(r.get("title", "")) for r in recent)
            similar = memory.query(
                MemoryQuery(query_type="similar", objective=objective, limit=3)
            )
            similar_cases = similar
        except Exception as exc:
            self._info(f"Memory 查询失败，继续 Discovery：{exc}")

        # 5. 根据 loop type 推荐 agents / patterns
        suggested_agents = self._AGENT_RECOMMENDATIONS.get(
            inferred_loop_type, ["solo-coder"]
        )
        suggested_patterns = self._PATTERN_RECOMMENDATIONS.get(inferred_loop_type, [])

        # 6. 构建 DiscoveryResult
        inferred_goal = self._build_inferred_goal(objective, inferred_loop_type)
        context_features = {
            "project_root": str(self._config.project_root),
            "has_readme": any("README" in str(a) for a in artifacts),
            "has_skill_md": any("SKILL.md" in str(a) for a in artifacts),
            "has_docs_spec": any("docs/spec" in str(a) for a in artifacts),
            "recent_notes_count": len(recent_notes.splitlines()) if recent_notes else 0,
            "similar_cases_count": len(similar_cases),
        }

        # 7. 如果存在高风险，建议先处理风险
        if risks:
            suggested_patterns.insert(0, "adversarial-verify")

        result = DiscoveryResult(
            objective=objective,
            inputs={"original_objective": objective},
            context_features=context_features,
            relevant_skills=self._detect_relevant_skills(objective, inferred_loop_type),
            detected_risks=risks,
            inferred_goal=inferred_goal,
            worktree_required=inferred_loop_type == LoopType.CODING,
            suggested_agents=suggested_agents,
            suggested_patterns=suggested_patterns,
            artifacts_to_read=artifacts[:5],
        )
        self._info(
            f"Discovery 完成：loop_type={inferred_loop_type.value} "
            f"risks={risks} agents={suggested_agents}"
        )
        return result

    def _build_manual_result(self, objective: str) -> DiscoveryResult:
        """MANUAL / OFF 模式返回最小 DiscoveryResult。"""
        return DiscoveryResult(
            objective=objective,
            inputs={
                "original_objective": objective,
                "mode": self._config.discovery_mode.value,
            },
            inferred_goal=objective,
            worktree_required=False,
            suggested_agents=["solo-coder"],
            detected_risks=[],
        )

    def _infer_loop_type(self, objective: str) -> LoopType:
        """根据 objective 推断 loop type。

        如果配置中已显式指定 loop_type，则优先使用配置值。
        否则基于关键词匹配。
        """
        # 配置显式指定时优先
        if self._config.loop_type in (
            LoopType.DESIGN,
            LoopType.CODING,
            LoopType.TESTING,
        ):
            # 但如果 objective 强烈暗示另一种类型，仍以 objective 为准
            objective_scores = self._score_loop_types(objective)
            max_score = max(objective_scores.values()) if objective_scores else 0
            if max_score >= 2:
                # 使用 items() 避免 mypy 对 dict.get 作为 key 的泛型推断问题
                return max(objective_scores.items(), key=lambda x: x[1])[0]
            return self._config.loop_type

        objective_scores = self._score_loop_types(objective)
        if objective_scores:
            return max(objective_scores.items(), key=lambda x: x[1])[0]
        return LoopType.CODING

    def _score_loop_types(self, text: str) -> Dict[LoopType, int]:
        """计算文本与各类 Loop 的关键词匹配分数。"""
        text_lower = text.lower()
        scores: Dict[LoopType, int] = {}
        for loop_type, keywords in self._LOOP_TYPE_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw.lower() in text_lower)
            if score > 0:
                scores[loop_type] = score
        return scores

    def _discover_artifacts(self, project_root: Path) -> List[Path]:
        """发现项目中重要的 artifacts 路径。

        Args:
            project_root: 项目根目录。

        Returns:
            List[Path]: 建议读取的工件路径列表。
        """
        artifacts: List[Path] = []
        if not project_root.exists():
            return artifacts

        candidates = [
            "README.md",
            "README_EN.md",
            "SKILL.md",
            "docs/spec/SPEC.md",
            "docs/spec/CONSTITUTION.md",
            ".trae/autonomous.yml",
        ]
        for rel in candidates:
            path = project_root / rel
            if path.exists() and path.is_file():
                artifacts.append(path)

        # 尝试读取代码地图（如果存在）
        code_map = project_root / "code_map.md"
        if code_map.exists():
            artifacts.append(code_map)

        return artifacts

    def _detect_risks(self, objective: str, artifacts: List[Path]) -> List[str]:
        """基于 objective 和 artifacts 检测风险。"""
        text_lower = objective.lower()
        risks: List[str] = []
        for risk_name, keywords in self._RISK_KEYWORDS.items():
            if any(kw.lower() in text_lower for kw in keywords):
                risks.append(risk_name)

        # 检查是否缺少关键文档
        has_prd = any("PRD" in str(a) or "prd" in str(a).lower() for a in artifacts)
        has_spec = any(
            "SPEC.md" in str(a) or "CONSTITUTION.md" in str(a) for a in artifacts
        )

        if self._config.loop_type == LoopType.CODING and not has_prd and not has_spec:
            # 编码 Loop 但没有 PRD/Spec，提示风险
            if "缺少设计文档" not in risks:
                risks.append("缺少设计文档")

        return risks

    def _detect_relevant_skills(self, objective: str, loop_type: LoopType) -> List[str]:
        """基于 objective 和 loop type 检测相关 skills。"""
        text_lower = objective.lower()
        skills: List[str] = []

        skill_keywords: Dict[str, List[str]] = {
            "architecture": ["架构", "设计", "architecture", "design"],
            "spec-driven-dev": ["PRD", "规范", "spec", "需求"],
            "testing": ["测试", "test", "coverage", "用例"],
            "git": ["commit", "提交", "分支", "merge"],
            "security": ["安全", "认证", "鉴权", "权限", "security"],
            "ui-design": ["UI", "界面", "样式", "frontend", "vue", "react"],
        }
        for skill_name, keywords in skill_keywords.items():
            if any(kw.lower() in text_lower for kw in keywords):
                skills.append(skill_name)

        # loop type 默认 skill
        if loop_type == LoopType.DESIGN and "architecture" not in skills:
            skills.append("architecture")
        if loop_type == LoopType.TESTING and "testing" not in skills:
            skills.append("testing")

        return skills

    def _build_inferred_goal(self, objective: str, loop_type: LoopType) -> str:
        """构建可验证的推断目标。"""
        prefixes = {
            LoopType.DESIGN: "完成设计文档：",
            LoopType.CODING: "完成代码实现并通过验证：",
            LoopType.TESTING: "完成测试补充并提升覆盖率：",
        }
        prefix = prefixes.get(loop_type, "完成目标：")
        return f"{prefix}{objective}"


__all__ = ["DiscoveryProbe"]
