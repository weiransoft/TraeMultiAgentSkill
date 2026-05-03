#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Karpathy 四大核心原则执行检查器

用于在代码审查和任务执行过程中强制执行 Karpathy 四大核心原则：
1. Think Before Coding（三思而后行）
2. Simplicity First（简单优先）
3. Surgical Changes（精准修改）
4. Goal-Driven Execution（目标驱动执行）

本工具提供：
- 原则合规性检查
- 违规检测与提醒
- 验证检查点管理
- 执行报告生成
"""

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass, field, asdict
from enum import Enum


class PrincipleType(Enum):
    """Karpathy 原则类型"""
    THINK_BEFORE_CODING = "think_before_coding"
    SIMPLICITY_FIRST = "simplicity_first"
    SURGICAL_CHANGES = "surgical_changes"
    GOAL_DRIVEN = "goal_driven"


class ViolationSeverity(Enum):
    """违规严重程度"""
    CRITICAL = "critical"      # 严重违规，必须立即修复
    HIGH = "high"              # 高风险违规，需要修复
    MEDIUM = "medium"          # 中等风险，建议修复
    LOW = "low"                # 低风险，可以优化
    INFO = "info"              # 提示信息


@dataclass
class PrincipleViolation:
    """原则违规记录"""
    principle: PrincipleType
    severity: ViolationSeverity
    file_path: str
    line_number: int
    description: str
    suggestion: str
    evidence: str = ""  # 违规证据代码片段


@dataclass
class VerificationCheckpoint:
    """验证检查点"""
    checkpoint_id: str
    principle: PrincipleType
    description: str
    criteria: List[str]  # 验证标准列表
    verified: bool = False
    verified_at: Optional[str] = None
    verified_by: str = ""
    notes: str = ""


@dataclass
class KarpathyEnforcementReport:
    """Karpathy 原则执行报告"""
    report_id: str
    project_path: str
    timestamp: str
    violations: List[PrincipleViolation] = field(default_factory=list)
    checkpoints: List[VerificationCheckpoint] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "report_id": self.report_id,
            "project_path": self.project_path,
            "timestamp": self.timestamp,
            "violations": [
                {
                    "principle": v.principle.value,
                    "severity": v.severity.value,
                    "file_path": v.file_path,
                    "line_number": v.line_number,
                    "description": v.description,
                    "suggestion": v.suggestion,
                    "evidence": v.evidence
                }
                for v in self.violations
            ],
            "checkpoints": [
                {
                    "checkpoint_id": cp.checkpoint_id,
                    "principle": cp.principle.value,
                    "description": cp.description,
                    "criteria": cp.criteria,
                    "verified": cp.verified,
                    "verified_at": cp.verified_at,
                    "verified_by": cp.verified_by,
                    "notes": cp.notes
                }
                for cp in self.checkpoints
            ],
            "summary": self.summary
        }


class KarpathyPrincipleEnforcer:
    """
    Karpathy 四大核心原则执行检查器

    用于在代码审查和任务执行过程中强制执行 Karpathy 原则。
    """

    # 违规模式定义
    VIOLATION_PATTERNS = {
        PrincipleType.THINK_BEFORE_CODING: [
            {
                "pattern": r"TODO|FIXME|HACK|XXX",
                "severity": ViolationSeverity.MEDIUM,
                "description": "发现 TODO/FIXME/HACK 标记，可能存在未明确的假设或临时方案",
                "suggestion": "在编码前明确所有假设，移除临时方案，使用明确的实现"
            },
            {
                "pattern": r"#.*假设|#.*assume|#.*可能|#.*maybe",
                "severity": ViolationSeverity.LOW,
                "description": "发现未验证的假设注释",
                "suggestion": "将假设转化为明确的验证逻辑或文档化"
            }
        ],
        PrincipleType.SIMPLICITY_FIRST: [
            {
                "pattern": r"class.*Factory|class.*Builder|class.*Strategy",
                "severity": ViolationSeverity.LOW,
                "description": "发现复杂的设计模式使用，可能过度设计",
                "suggestion": "评估是否真的需要这些模式，优先考虑简单函数"
            },
            {
                "pattern": r"#.*以后|#.*future|#.*预留|#.*reserve",
                "severity": ViolationSeverity.HIGH,
                "description": "发现为未来预留的代码（speculative code）",
                "suggestion": "删除未使用的代码，只在需要时添加"
            },
            {
                "pattern": r"interface.*\{|abstract.*class",
                "severity": ViolationSeverity.LOW,
                "description": "发现抽象类或接口，评估是否必要",
                "suggestion": "确保抽象有实际用途，避免为抽象而抽象"
            }
        ],
        PrincipleType.SURGICAL_CHANGES: [
            {
                "pattern": r"pass.*#.*占位|pass.*#.*placeholder|pass.*#.*TODO",
                "severity": ViolationSeverity.CRITICAL,
                "description": "发现占位符代码（mock/占位/简化实现）",
                "suggestion": "严禁使用占位符，必须实现真实逻辑"
            },
            {
                "pattern": r"mock|Mock|stub|Stub",
                "severity": ViolationSeverity.HIGH,
                "description": "发现 mock/stub 代码，可能不是真实实现",
                "suggestion": "在生产代码中移除 mock，使用真实实现"
            },
            {
                "pattern": r"#.*顺手|#.*顺便|#.*改.*其他",
                "severity": ViolationSeverity.MEDIUM,
                "description": "发现可能涉及无关修改的注释",
                "suggestion": "只修改直接相关的代码，不碰无关功能"
            }
        ],
        PrincipleType.GOAL_DRIVEN: [
            {
                "pattern": r"def.*test.*\(.*\):\s*\n\s*pass",
                "severity": ViolationSeverity.CRITICAL,
                "description": "发现空的测试函数，未实现验证",
                "suggestion": "为所有功能编写完整的测试用例"
            },
            {
                "pattern": r"#.*未测试|#.*未验证|#.*跳过",
                "severity": ViolationSeverity.HIGH,
                "description": "发现未测试或未验证的代码标记",
                "suggestion": "为代码添加完整的测试和验证"
            },
            {
                "pattern": r"print\(|console\.log|logger\.debug",
                "severity": ViolationSeverity.LOW,
                "description": "发现调试输出，可能影响生产环境",
                "suggestion": "移除调试代码，使用正式的日志机制"
            }
        ]
    }

    def __init__(self, project_root: str = "."):
        """
        初始化执行检查器

        Args:
            project_root: 项目根目录
        """
        self.project_root = Path(project_root)
        self.violations: List[PrincipleViolation] = []
        self.checkpoints: List[VerificationCheckpoint] = []
        self._init_default_checkpoints()

    def _init_default_checkpoints(self):
        """初始化默认验证检查点"""
        self.checkpoints = [
            VerificationCheckpoint(
                checkpoint_id="cp_think_1",
                principle=PrincipleType.THINK_BEFORE_CODING,
                description="需求理解检查点",
                criteria=[
                    "已明确所有业务需求",
                    "已识别所有技术约束",
                    "已确认输入输出边界",
                    "已文档化所有假设"
                ]
            ),
            VerificationCheckpoint(
                checkpoint_id="cp_think_2",
                principle=PrincipleType.THINK_BEFORE_CODING,
                description="方案评估检查点",
                criteria=[
                    "已评估至少 2 种实现方案",
                    "已明确各方案的权衡",
                    "已选择最简单的可行方案"
                ]
            ),
            VerificationCheckpoint(
                checkpoint_id="cp_simple_1",
                principle=PrincipleType.SIMPLICITY_FIRST,
                description="简单性检查点",
                criteria=[
                    "无单次使用的抽象",
                    "无 speculative features",
                    "无未来可能用到的代码",
                    "代码量最小化"
                ]
            ),
            VerificationCheckpoint(
                checkpoint_id="cp_surgical_1",
                principle=PrincipleType.SURGICAL_CHANGES,
                description="精准修改检查点",
                criteria=[
                    "只修改直接相关的代码",
                    "保持原有代码风格一致",
                    "未修改无关功能",
                    "无格式化混杂"
                ]
            ),
            VerificationCheckpoint(
                checkpoint_id="cp_goal_1",
                principle=PrincipleType.GOAL_DRIVEN,
                description="目标定义检查点",
                criteria=[
                    "已定义明确的成功标准",
                    "已设定可验证的指标",
                    "已确定完成边界"
                ]
            ),
            VerificationCheckpoint(
                checkpoint_id="cp_goal_2",
                principle=PrincipleType.GOAL_DRIVEN,
                description="验证完成检查点",
                criteria=[
                    "所有测试用例通过",
                    "代码审查通过",
                    "功能符合需求",
                    "无已知缺陷"
                ]
            )
        ]

    def scan_file(self, file_path: str) -> List[PrincipleViolation]:
        """
        扫描单个文件的违规情况

        Args:
            file_path: 文件路径

        Returns:
            List[PrincipleViolation]: 违规列表
        """
        violations = []
        path = Path(file_path)

        if not path.exists():
            return violations

        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                lines = content.split('\n')

            for principle, patterns in self.VIOLATION_PATTERNS.items():
                for pattern_def in patterns:
                    pattern = pattern_def["pattern"]
                    severity = pattern_def["severity"]
                    description = pattern_def["description"]
                    suggestion = pattern_def["suggestion"]

                    for line_num, line in enumerate(lines, 1):
                        if re.search(pattern, line, re.IGNORECASE):
                            # 获取上下文（前后各2行）
                            start = max(0, line_num - 3)
                            end = min(len(lines), line_num + 2)
                            evidence = '\n'.join(lines[start:end])

                            violation = PrincipleViolation(
                                principle=principle,
                                severity=severity,
                                file_path=str(path),
                                line_number=line_num,
                                description=description,
                                suggestion=suggestion,
                                evidence=evidence
                            )
                            violations.append(violation)

        except Exception as e:
            print(f"扫描文件失败 {file_path}: {e}")

        return violations

    def scan_project(self, file_extensions: Optional[List[str]] = None) -> List[PrincipleViolation]:
        """
        扫描整个项目的违规情况

        Args:
            file_extensions: 要扫描的文件扩展名列表（默认常见代码文件）

        Returns:
            List[PrincipleViolation]: 违规列表
        """
        if file_extensions is None:
            file_extensions = ['.py', '.java', '.js', '.ts', '.jsx', '.tsx', '.go', '.rs', '.c', '.cpp']

        all_violations = []

        for ext in file_extensions:
            for file_path in self.project_root.rglob(f'*{ext}'):
                # 跳过常见排除目录
                if any(skip in str(file_path) for skip in ['node_modules', '.git', '__pycache__', 'venv', '.venv']):
                    continue

                violations = self.scan_file(str(file_path))
                all_violations.extend(violations)

        self.violations = all_violations
        return all_violations

    def verify_checkpoint(self, checkpoint_id: str, verified: bool, verified_by: str = "", notes: str = ""):
        """
        验证检查点

        Args:
            checkpoint_id: 检查点 ID
            verified: 是否通过验证
            verified_by: 验证人
            notes: 备注
        """
        for cp in self.checkpoints:
            if cp.checkpoint_id == checkpoint_id:
                cp.verified = verified
                cp.verified_at = datetime.now().isoformat()
                cp.verified_by = verified_by
                cp.notes = notes
                return True
        return False

    def get_checkpoint_status(self, principle: Optional[PrincipleType] = None) -> Dict[str, Any]:
        """
        获取检查点状态

        Args:
            principle: 指定原则类型（可选）

        Returns:
            Dict: 检查点状态统计
        """
        checkpoints = self.checkpoints
        if principle:
            checkpoints = [cp for cp in checkpoints if cp.principle == principle]

        total = len(checkpoints)
        verified = sum(1 for cp in checkpoints if cp.verified)

        return {
            "total": total,
            "verified": verified,
            "pending": total - verified,
            "completion_rate": round(verified / total * 100, 2) if total > 0 else 0,
            "checkpoints": [
                {
                    "id": cp.checkpoint_id,
                    "principle": cp.principle.value,
                    "description": cp.description,
                    "verified": cp.verified,
                    "verified_at": cp.verified_at
                }
                for cp in checkpoints
            ]
        }

    def generate_report(self, output_path: Optional[str] = None) -> str:
        """
        生成执行报告

        Args:
            output_path: 输出文件路径（可选）

        Returns:
            str: 报告内容
        """
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        report_id = f"KARPATHY-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        # 统计违规
        critical_count = sum(1 for v in self.violations if v.severity == ViolationSeverity.CRITICAL)
        high_count = sum(1 for v in self.violations if v.severity == ViolationSeverity.HIGH)
        medium_count = sum(1 for v in self.violations if v.severity == ViolationSeverity.MEDIUM)
        low_count = sum(1 for v in self.violations if v.severity == ViolationSeverity.LOW)

        # 检查点状态
        cp_status = self.get_checkpoint_status()

        report = KarpathyEnforcementReport(
            report_id=report_id,
            project_path=str(self.project_root),
            timestamp=timestamp,
            violations=self.violations,
            checkpoints=self.checkpoints,
            summary={
                "total_violations": len(self.violations),
                "critical": critical_count,
                "high": high_count,
                "medium": medium_count,
                "low": low_count,
                "checkpoint_completion_rate": cp_status["completion_rate"],
                "checkpoint_verified": cp_status["verified"],
                "checkpoint_total": cp_status["total"]
            }
        )

        # 生成 Markdown 报告
        md_content = f"""# Karpathy 四大核心原则执行报告

> **报告 ID**: {report_id}
> **项目路径**: {self.project_root}
> **生成时间**: {timestamp}

---

## 1. 执行摘要

### 1.1 违规统计

| 严重程度 | 数量 | 说明 |
|---------|------|------|
| 🔴 严重 (Critical) | {critical_count} | 必须立即修复 |
| 🟠 高 (High) | {high_count} | 需要修复 |
| 🟡 中 (Medium) | {medium_count} | 建议修复 |
| 🟢 低 (Low) | {low_count} | 可以优化 |
| **总计** | **{len(self.violations)}** | - |

### 1.2 检查点完成度

| 指标 | 数值 |
|------|------|
| 已验证检查点 | {cp_status['verified']} / {cp_status['total']} |
| 完成率 | {cp_status['completion_rate']}% |

---

## 2. 违规详情

"""

        if self.violations:
            for principle in PrincipleType:
                principle_violations = [v for v in self.violations if v.principle == principle]
                if principle_violations:
                    md_content += f"\n### {self._get_principle_name(principle)}\n\n"
                    for v in principle_violations:
                        severity_emoji = {
                            ViolationSeverity.CRITICAL: "🔴",
                            ViolationSeverity.HIGH: "🟠",
                            ViolationSeverity.MEDIUM: "🟡",
                            ViolationSeverity.LOW: "🟢",
                            ViolationSeverity.INFO: "ℹ️"
                        }.get(v.severity, "⚪")

                        md_content += f"""
**{severity_emoji} [{v.severity.value}]** {v.description}

- **文件**: `{v.file_path}:{v.line_number}`
- **建议**: {v.suggestion}

```
{v.evidence}
```

"""
        else:
            md_content += "\n✅ 未发现违规，代码符合 Karpathy 原则！\n"

        md_content += f"""

---

## 3. 验证检查点

"""

        for principle in PrincipleType:
            principle_cps = [cp for cp in self.checkpoints if cp.principle == principle]
            if principle_cps:
                md_content += f"\n### {self._get_principle_name(principle)}\n\n"
                md_content += "| 检查点 | 状态 | 验证时间 | 验证人 |\n"
                md_content += "|--------|------|----------|--------|\n"
                for cp in principle_cps:
                    status = "✅ 已通过" if cp.verified else "⏳ 待验证"
                    verified_at = cp.verified_at or "-"
                    verified_by = cp.verified_by or "-"
                    md_content += f"| {cp.description} | {status} | {verified_at} | {verified_by} |\n"

        md_content += f"""

---

## 4. 改进建议

### 4.1 立即行动项

"""

        critical_and_high = [v for v in self.violations if v.severity in (ViolationSeverity.CRITICAL, ViolationSeverity.HIGH)]
        if critical_and_high:
            for v in critical_and_high[:10]:
                md_content += f"- [ ] **{v.file_path}:{v.line_number}** - {v.description}\n"
        else:
            md_content += "无立即行动项\n"

        md_content += f"""

### 4.2 原则应用速查

| 场景 | 应用原则 | 具体行动 |
|------|---------|---------|
| 需求不明确 | Think Before Coding | 停下来问清楚 |
| 多种方案可选 | Think Before Coding | 呈现权衡让用户选 |
| 考虑添加抽象 | Simplicity First | 问"真的需要吗？" |
| 看到复杂代码 | Simplicity First | 简化到最小可用 |
| 修改代码 | Surgical Changes | 只改必要的行 |
| 准备提交代码 | Surgical Changes | 检查是否有多余修改 |
| 开始实现 | Goal-Driven | 定义成功标准 |
| 完成后 | Goal-Driven | 验证是否达标 |

---

*本报告由 Karpathy 原则执行检查器生成*
*生成时间: {timestamp}*
"""

        if output_path:
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(md_content, encoding='utf-8')
            print(f"报告已保存: {output_path}")

        return md_content

    def _get_principle_name(self, principle: PrincipleType) -> str:
        """获取原则中文名称"""
        names = {
            PrincipleType.THINK_BEFORE_CODING: "🧠 Think Before Coding（三思而后行）",
            PrincipleType.SIMPLICITY_FIRST: "🎯 Simplicity First（简单优先）",
            PrincipleType.SURGICAL_CHANGES: "🔬 Surgical Changes（精准修改）",
            PrincipleType.GOAL_DRIVEN: "✅ Goal-Driven Execution（目标驱动执行）"
        }
        return names.get(principle, principle.value)

    def has_critical_violations(self) -> bool:
        """是否有严重违规"""
        return any(v.severity == ViolationSeverity.CRITICAL for v in self.violations)

    def get_violations_by_principle(self, principle: PrincipleType) -> List[PrincipleViolation]:
        """获取指定原则的违规列表"""
        return [v for v in self.violations if v.principle == principle]

    def get_violations_by_severity(self, severity: ViolationSeverity) -> List[PrincipleViolation]:
        """获取指定严重程度的违规列表"""
        return [v for v in self.violations if v.severity == severity]


def main():
    """主函数 - 命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(description="Karpathy 四大核心原则执行检查器")
    parser.add_argument("--project-root", "-p", default=".", help="项目根目录")
    parser.add_argument("--output", "-o", default=None, help="输出报告路径")
    parser.add_argument("--file", "-f", default=None, help="扫描单个文件")
    parser.add_argument("--extensions", "-e", default=None, help="文件扩展名（逗号分隔）")

    args = parser.parse_args()

    enforcer = KarpathyPrincipleEnforcer(args.project_root)

    if args.file:
        print(f"扫描文件: {args.file}")
        violations = enforcer.scan_file(args.file)
    else:
        extensions = args.extensions.split(",") if args.extensions else None
        print(f"扫描项目: {args.project_root}")
        violations = enforcer.scan_project(extensions)

    print(f"发现 {len(violations)} 个违规")

    output_path = args.output or f"karpathy-enforcement-report-{datetime.now().strftime('%Y%m%d%H%M%S')}.md"
    enforcer.generate_report(output_path)

    # 如果有严重违规，返回非零退出码
    if enforcer.has_critical_violations():
        print("\n🔴 发现严重违规，请立即修复！")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
