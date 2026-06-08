"""Ralph 风格智能确认跳过（白名单 + 风险评估双层防护）。

设计目标：
- 白名单：明确允许自动确认的操作（测试、lint、状态检查等）
- 黑名单：永远不自动确认（rm -rf、git push --force、drop table 等）
- 风险评分：基于命令特征计算 0-100 风险分
- 三态决策：AUTO / ASK / DENY
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import FrozenSet, List, Optional, Sequence, Tuple


class RiskLevel(str, Enum):
    """风险等级。"""

    LOW = "low"          # 0-30：自动确认
    MEDIUM = "medium"    # 31-70：仅白名单可自动确认
    HIGH = "high"        # 71-100：永远需要用户确认
    CRITICAL = "critical"  # 命中黑名单 → 立即 DENY


class ConfirmationDecision(str, Enum):
    """确认决策三态。"""

    AUTO = "auto"  # 自动确认并执行
    ASK = "ask"    # 需要用户确认
    DENY = "deny"  # 拒绝执行


@dataclass
class ConfirmationResult:
    """确认决策结果。

    字段说明：
    - decision: AUTO / ASK / DENY
    - reason: 决策原因（人类可读）
    - risk_level: 风险等级
    - risk_score: 0-100 风险分
    - matched_pattern: 命中的模式（白名单/黑名单）
    """

    decision: ConfirmationDecision
    reason: str
    risk_level: RiskLevel
    risk_score: int
    matched_pattern: str = ""


# 公共辅助：把风险分映射为风险等级（供外部测试使用）
def score_to_level(score: int) -> RiskLevel:
    """将分值映射到风险等级。"""
    if score <= 30:
        return RiskLevel.LOW
    if score <= 70:
        return RiskLevel.MEDIUM
    return RiskLevel.HIGH


class SmartConfirmation:
    """Ralph 风格智能确认。

    设计原则：
    1. 黑名单优先（一旦命中 → 立即 DENY）
    2. 白名单次之（命中 → AUTO + LOW 风险）
    3. 风险评分兜底（基于命令特征）
    4. 保守策略：任何不确定 → ASK
    """

    # 黑名单模式（永远 DENY）。
    # 注意：
    # - 大小写不敏感（在编译时加 IGNORECASE）
    # - 路径结尾使用 (\s|$) 而非 \b，因为 / 不是 word char，\b 在 / 之后无法匹配字符串结尾
    DEFAULT_BLACKLIST: Tuple[str, ...] = (
        # rm -rf 危险路径
        r"\brm\s+-rf\s+/\s",                # rm -rf /  (后面必须有空白或结尾)
        r"\brm\s+-rf\s+/\s*$",              # rm -rf / 字符串结尾
        r"\brm\s+-rf\s+~",                  # rm -rf ~
        r"\brm\s+-rf\s+\*",                 # rm -rf *
        r"\brm\s+-rf\s+/etc",               # rm -rf /etc 等系统目录
        r"\brm\s+-rf\s+/var",
        r"\brm\s+-rf\s+/usr",
        r"\brm\s+-rf\s+/bin",
        r"\brm\s+-rf\s+/sbin",
        r"\brm\s+-rf\s+/boot",
        r"\brm\s+-rf\s+/lib",
        r"\brm\s+-rf\s+/lib64",
        r"\brm\s+-rf\s+/opt",
        r"\brm\s+-rf\s+/root",
        r"\brm\s+-rf\s+/home",
        # git 危险操作
        r"\bgit\s+push\s+(--force|-f)\b",   # git push --force
        r"\bgit\s+reset\s+--hard\s+origin", # git reset --hard origin/main
        r"\bgit\s+clean\s+-fd\b",           # git clean -fd
        r"\bgit\s+clean\s+-fdx\b",
        # 磁盘与系统破坏
        r"\bdd\s+if=",                      # dd if=...
        r"\bmkfs\b",                        # mkfs.*
        r">\s*/dev/sd[a-z]",                # > /dev/sda
        r">\s*/dev/nvme",
        # 数据库破坏
        r"\bdrop\s+(database|table|schema|view|index|function|procedure|trigger)\b",
        r"\btruncate\s+table\b",
        r"\btruncate\s+\w+\s*;",
        # 远程脚本执行（curl/wget | bash/sh）
        r"\bcurl\s+.*\|\s*bash\b",          # curl | bash
        r"\bcurl\s+.*\|\s*sh\b",
        r"\bwget\s+.*\|\s*bash\b",
        r"\bwget\s+.*\|\s*sh\b",
        # 权限破坏
        r"\bchmod\s+-R\s+777\s+/",          # chmod -R 777 /
        r"\bchmod\s+777\s+/",
        # fork bomb
        r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;\s*:",
        # sudo + 危险命令
        r"\bsudo\s+rm\b",
        r"\bsudo\s+dd\b",
        r"\bsudo\s+mkfs\b",
        r"\bsudo\s+chmod\s+777\s+/",
        r"\bsudo\s+chown\s+-R\s+",
        r"\bsudo\s+kill\b",
        r"\bsudo\s+killall\b",
        r"\bsudo\s+shutdown\b",
        r"\bsudo\s+reboot\b",
        r"\bsudo\s+halt\b",
        r"\bsudo\s+init\b",
        r"\bsudo\s+apt\b",                # sudo apt install/remove
        r"\bsudo\s+apt-get\b",
        r"\bsudo\s+yum\b",
        r"\bsudo\s+dnf\b",
        r"\bsudo\s+pip\b",
        r"\bsudo\s+npm\b",
        r"\bsudo\s+bash\b",
        r"\bsudo\s+sh\b",
        # 杀 init / 关键进程
        r"\bkill\s+-9\s+1\b",               # kill -9 1 (init)
        r"\bkill\s+-9\s+0\b",
        r"\bkill\s+-\s*SIGKILL\s+1\b",
        r"\bkillall\s+-9\s+init\b",
        r"\bkillall\s+-9\s+(init|sshd|systemd|kthreadd)\b",
        r"\bkillall\s+-9\s+python\b",
        r"\bkillall\s+-9\s+node\b",
        # 强制重装 / 危险包管理
        r"\bpip\s+install\s+.*--force-reinstall",
        r"\bpip\s+install\s+.*--ignore-installed",
        r"\bnpm\s+install\s+-g\s+.*--force",
        r"\bapt[(-get)?]\s+install\s+.*-y\s+--force-yes",
        # 系统控制
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bhalt\b",
        r"\bpoweroff\b",
        r"\bsystemctl\s+(stop|disable|mask)\s+(ssh|sshd|network|systemd-resolved)\b",
        # eval 反向 shell
        r"\bbash\s+-i\s+>&\s*/dev/tcp/",
        r"\bnc\s+-l\s+-p\s+",
        r"\bnetcat\s+-l\s+-p\s+",
    )

    # 白名单模式（直接 AUTO + LOW）。
    # 始终用 re.IGNORECASE 编译。
    DEFAULT_WHITELIST: Tuple[str, ...] = (
        r"^python3?\s+-m\s+pytest",          # pytest
        r"^python3?\s+-m\s+unittest",
        r"^pytest\b",
        r"^npm\s+test\b",
        r"^npm\s+run\s+(test|lint|build|check)",
        r"^yarn\s+test\b",
        r"^pnpm\s+test\b",
        r"^cargo\s+test\b",
        r"^go\s+test\b",
        r"^mvn\s+test\b",
        r"^gradle\s+test\b",
        r"^ruff\b",
        r"^black\b",
        r"^flake8\b",
        r"^mypy\b",
        r"^eslint\b",
        r"^prettier\b",
        r"^git\s+status\b",
        r"^git\s+log\b",
        r"^git\s+diff\b",
        r"^git\s+branch\b",
        r"^git\s+add\b",
        r"^git\s+commit\b",
        r"^git\s+fetch\b",
        r"^ls\b",
        r"^cat\b",
        r"^head\b",
        r"^tail\b",
        r"^find\b",
        r"^grep\b",
        r"^rg\b",
        r"^tree\b",
        r"^pwd\b",
        r"^echo\b",
        r"^wc\b",
    )

    # 风险加分项（每个匹配 +N 分）。大小写不敏感。
    _RISK_PATTERNS: List[Tuple[str, int]] = [
        (r"\brm\s+-rf\b", 50),
        (r"\brm\s+-r\b", 30),
        (r"\brm\s+-f\b", 20),
        (r"\bsudo\b", 30),
        (r"\bchmod\s+", 15),
        (r"\bchown\s+", 15),
        (r"\bgit\s+push\b", 20),
        (r"\bgit\s+reset\b", 20),
        (r"\bgit\s+clean\b", 20),
        (r"\bpip\s+install\b", 10),
        (r"\bnpm\s+install\b", 10),
        (r">\s*/", 20),
        (r"\|\s*bash\b", 40),
        (r"\|\s*sh\b", 40),
        (r"\bsystemctl\b", 20),
        (r"\bkill\s+-9\b", 20),
        (r"\bkillall\b", 20),
        (r"--force\b", 25),
        (r"--hard\b", 25),
    ]

    def __init__(
        self,
        blacklist: Optional[Sequence[str]] = None,
        whitelist: Optional[Sequence[str]] = None,
        auto_threshold: int = 0,
    ):
        """构造 SmartConfirmation。

        Args:
            blacklist: 自定义黑名单正则（None = 用默认）。允许 list / tuple / frozenset。
            whitelist: 自定义白名单正则（None = 用默认）。允许 list / tuple / frozenset。
            auto_threshold: 风险分低于此值可 AUTO（默认 30）
        """
        # 用 tuple 保持顺序（frozenset 不支持下标）
        self._blacklist: Tuple[str, ...] = tuple(blacklist) if blacklist else self.DEFAULT_BLACKLIST
        self._whitelist: Tuple[str, ...] = tuple(whitelist) if whitelist else self.DEFAULT_WHITELIST
        self._auto_threshold = max(0, min(100, auto_threshold))
        # 预编译正则（黑名单 / 白名单：大小写不敏感）
        # 使用 re.IGNORECASE 避免 DROP DATABASE 大小写问题
        self._blacklist_re: List[re.Pattern] = [
            re.compile(p, re.IGNORECASE) for p in self._blacklist
        ]
        self._whitelist_re: List[re.Pattern] = [
            re.compile(p, re.IGNORECASE) for p in self._whitelist
        ]
        self._risk_re: List[Tuple[re.Pattern, int]] = [
            (re.compile(p, re.IGNORECASE), score) for p, score in self._RISK_PATTERNS
        ]

    # ------------------------------------------------------------------ #
    # 公共 API                                                            #
    # ------------------------------------------------------------------ #

    def check(self, command: str) -> ConfirmationResult:
        """检查命令，决定是否自动确认。

        Args:
            command: 完整命令字符串

        Returns:
            ConfirmationResult: 决策结果
        """
        if not command or not command.strip():
            return ConfirmationResult(
                decision=ConfirmationDecision.DENY,
                reason="空命令",
                risk_level=RiskLevel.LOW,
                risk_score=0,
            )
        cmd_stripped = command.strip()
        # 1. 黑名单优先
        for i, pattern in enumerate(self._blacklist_re):
            if pattern.search(cmd_stripped):
                return ConfirmationResult(
                    decision=ConfirmationDecision.DENY,
                    reason=f"命令命中黑名单模式：{self._blacklist[i]}（永远禁止）",
                    risk_level=RiskLevel.CRITICAL,
                    risk_score=100,
                    matched_pattern=self._blacklist[i],
                )
        # 2. 白名单 → AUTO
        for i, pattern in enumerate(self._whitelist_re):
            if pattern.search(cmd_stripped):
                return ConfirmationResult(
                    decision=ConfirmationDecision.AUTO,
                    reason=f"白名单操作：{self._whitelist[i]}",
                    risk_level=RiskLevel.LOW,
                    risk_score=0,
                    matched_pattern=self._whitelist[i],
                )
        # 3. 风险评分
        risk_score = self._calculate_risk(cmd_stripped)
        if risk_score <= self._auto_threshold:
            return ConfirmationResult(
                decision=ConfirmationDecision.AUTO,
                reason=f"风险分 {risk_score} 低于阈值 {self._auto_threshold}",
                risk_level=score_to_level(risk_score),
                risk_score=risk_score,
            )
        if risk_score >= 71:
            return ConfirmationResult(
                decision=ConfirmationDecision.ASK,
                reason=f"风险分 {risk_score} >= 71，需要用户确认",
                risk_level=RiskLevel.HIGH,
                risk_score=risk_score,
            )
        # 中等风险：默认 ASK（保守）
        return ConfirmationResult(
            decision=ConfirmationDecision.ASK,
            reason=f"风险分 {risk_score} 中等，需要用户确认",
            risk_level=RiskLevel.MEDIUM,
            risk_score=risk_score,
        )

    def check_batch(self, commands: List[str]) -> List[ConfirmationResult]:
        """批量检查多个命令。

        Args:
            commands: 命令列表

        Returns:
            List[ConfirmationResult]: 与 commands 一一对应
        """
        return [self.check(cmd) for cmd in commands]

    def is_destructive(self, command: str) -> bool:
        """快速判断命令是否破坏性（不返回完整结果）。"""
        result = self.check(command)
        return result.decision == ConfirmationDecision.DENY

    # ------------------------------------------------------------------ #
    # 内部辅助                                                            #
    # ------------------------------------------------------------------ #

    def _calculate_risk(self, command: str) -> int:
        """计算风险分（0-100）。"""
        score = 0
        for pattern, weight in self._risk_re:
            if pattern.search(command):
                score += weight
        return min(100, score)

    # 保留向后兼容：旧的私有方法
    @staticmethod
    def _score_to_level(score: int) -> RiskLevel:
        """将分值映射到风险等级（私有兼容层）。"""
        return score_to_level(score)


__all__ = [
    "SmartConfirmation",
    "ConfirmationDecision",
    "ConfirmationResult",
    "RiskLevel",
    "score_to_level",
]
