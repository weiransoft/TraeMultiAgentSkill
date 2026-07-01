"""Loop Engineering 独立 Evaluator。

实现 Generator/Evaluator 分离，确保 Loop 中有一个"说'不'"的独立角色。

评估策略：
- STRICT：完全基于 generator_result 中的客观指标（测试通过、lint 通过、安全扫描通过）
         进行独立判定；若缺少客观指标，则判定为不通过（保守策略）。
- STANDARD：允许结合 generator 自评，但对关键指标必须独立核验。
- OFF：直接通过（仅调试用，生产不推荐）。

抽样阅读：
- 对 artifacts 列表按 sampling_read_ratio 随机抽样（确定性的，基于文件名哈希），
  模拟人类"抽样阅读"安全纪律。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from loop_engineering.models import (
    EvaluationVerdict,
    EvaluatorMode,
    HandoffItem,
    LoopEngineeringConfig,
)


class IndependentEvaluator:
    """独立 Evaluator：Generator/Evaluator 分离。"""

    def __init__(
        self,
        config: LoopEngineeringConfig,
        log: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """构造独立 Evaluator。

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

    def evaluate(
        self,
        handoff_items: List[HandoffItem],
        generator_result: Dict[str, Any],
        context: Dict[str, Any],
    ) -> EvaluationVerdict:
        """对 Generator 产出进行独立评估。

        Args:
            handoff_items: 工作项列表。
            generator_result: Generator 执行结果。
            context: 上下文信息（objective / loop_type 等）。

        Returns:
            EvaluationVerdict: 独立判定结果。
        """
        if self._config.evaluator_mode == EvaluatorMode.OFF:
            return EvaluationVerdict(
                passed=True,
                evaluator_id="independent-evaluator-off",
                reason="Evaluator 已关闭（调试模式）",
                severity="warning",
            )

        # 1. 收集待评估的 artifacts
        artifacts = self._extract_artifacts(handoff_items, generator_result)

        # 2. 抽样阅读
        sampled = self._sample_artifacts(artifacts, self._config.sampling_read_ratio)

        # 3. 客观指标检查
        findings: List[str] = []

        # 检查 generator_result 是否声明成功
        success = bool(generator_result.get("success", False))
        if not success:
            findings.append("Generator 未声明执行成功")

        # 检查测试命令结果
        test_result = generator_result.get("test_result", {})
        if test_result:
            if not test_result.get("passed", False):
                findings.append(f"测试未通过：{test_result.get('summary', '')}")
        elif self._config.evaluator_mode == EvaluatorMode.STRICT:
            # STRICT 模式缺少测试指标视为不通过
            findings.append("缺少客观测试指标")

        # 检查 lint / typecheck 结果
        lint_result = generator_result.get("lint_result", {})
        if lint_result and not lint_result.get("passed", True):
            findings.append(f"静态检查未通过：{lint_result.get('summary', '')}")

        # 检查安全扫描结果
        security_result = generator_result.get("security_result", {})
        if security_result:
            severity = security_result.get("severity", "info")
            if severity in ("blocker", "critical", "high"):
                findings.append(
                    f"安全扫描发现严重问题：{security_result.get('summary', '')}"
                )

        # 4. 抽样阅读发现
        if sampled:
            findings.append(
                f"抽样阅读了 {len(sampled)} 个工件：{[str(p) for p in sampled]}"
            )

        # 5. 最终判定
        if not findings:
            return EvaluationVerdict(
                passed=True,
                evaluator_id="independent-evaluator",
                reason="所有客观指标通过，抽样阅读无异常",
                sampled_artifacts=sampled,
            )

        # STRICT 模式下，任何 finding 都导致不通过
        if self._config.evaluator_mode == EvaluatorMode.STRICT:
            return EvaluationVerdict(
                passed=False,
                evaluator_id="independent-evaluator",
                reason=f"STRICT 模式下发现 {len(findings)} 个问题",
                findings=findings,
                severity="blocker",
                suggested_fix="请修复上述问题后重试",
                sampled_artifacts=sampled,
            )

        # STANDARD 模式下，只有 blocker/critical 才判定不通过
        has_blocker = any(
            "安全" in f or "测试未通过" in f or "Generator 未声明" in f
            for f in findings
        )
        if has_blocker:
            return EvaluationVerdict(
                passed=False,
                evaluator_id="independent-evaluator",
                reason="发现 blocker 级别问题",
                findings=findings,
                severity="blocker",
                suggested_fix="请优先修复 blocker 问题",
                sampled_artifacts=sampled,
            )

        return EvaluationVerdict(
            passed=True,
            evaluator_id="independent-evaluator",
            reason="STANDARD 模式下未发现 blocker 问题",
            findings=findings,
            severity="warning",
            sampled_artifacts=sampled,
        )

    def _extract_artifacts(
        self,
        handoff_items: List[HandoffItem],
        generator_result: Dict[str, Any],
    ) -> List[Path]:
        """从工作项和 generator_result 中提取工件路径。"""
        artifacts: List[Path] = []

        # 从 handoff_items 的 worktree_path
        for item in handoff_items:
            if item.worktree_path:
                artifacts.append(item.worktree_path)

        # 从 generator_result 的 modified_files / artifacts
        modified_files = generator_result.get("modified_files", [])
        for path in modified_files:
            try:
                artifacts.append(Path(path))
            except (TypeError, ValueError):
                continue

        # 去重并保持顺序
        seen: set[str] = set()
        unique: List[Path] = []
        for p in artifacts:
            key = str(p)
            if key not in seen:
                seen.add(key)
                unique.append(p)
        return unique

    def _sample_artifacts(
        self,
        artifacts: List[Path],
        read_ratio: float,
    ) -> List[Path]:
        """按 read_ratio 确定性抽样 artifacts。

        使用文件名 SHA256 哈希的前 4 字节作为确定性随机源，
        保证相同输入集多次调用结果一致。

        Args:
            artifacts: 工件路径列表。
            read_ratio: 抽样比例（0.0-1.0）。

        Returns:
            List[Path]: 抽样后的工件路径列表。
        """
        if not artifacts or read_ratio <= 0:
            return []
        if read_ratio >= 1.0:
            return list(artifacts)

        sample_count = max(1, int(len(artifacts) * read_ratio + 0.5))
        # 按哈希值排序后取前 sample_count 个
        scored = []
        for path in artifacts:
            hash_value = hashlib.sha256(str(path).encode("utf-8")).hexdigest()
            score = int(hash_value[:8], 16)
            scored.append((score, path))
        scored.sort(key=lambda x: x[0])
        return [path for _, path in scored[:sample_count]]


__all__ = ["IndependentEvaluator"]
