"""Loop Engineering Handoff 阶段适配器。

HandoffAdapter 负责：
1. 根据 Discovery 结果和 Loop 类型选择模板，生成 HandoffItem（工作项）。
2. 将工作项分发给真实子代理执行（默认通过 DispatcherAdapter 调用 V3 调度器）。
3. 收集 Generator 输出后，运行客观指标检查（测试、静态检查、安全扫描）。
4. 构造结构化的 generator_result，供 IndependentEvaluator 独立评估。

设计约束：
- 禁止模拟：默认使用 DispatcherAdapter 调用真实子代理；测试时可注入 executor。
- 失败安全：DispatcherAdapter 不可用时，返回明确的失败结果，不伪造成功。
- 客观指标：测试命令、lint/typecheck、安全扫描结果必须真实运行后写入 generator_result。
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from loop_engineering.models import (
    DiscoveryResult,
    HandoffItem,
    LoopEngineeringConfig,
    LoopType,
)
from loop_engineering.protocols import HandoffAdapterProtocol
from loop_engineering.templates import LoopTemplateRegistry


class HandoffAdapter(HandoffAdapterProtocol):
    """Handoff 阶段适配器：生成工作项并调用 Generator 执行。"""

    def __init__(
        self,
        config: LoopEngineeringConfig,
        template_registry: Optional[LoopTemplateRegistry] = None,
        dispatcher_adapter: Optional[Any] = None,
        executor: Optional[Callable[[HandoffItem], Dict[str, Any]]] = None,
        log: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """构造 HandoffAdapter。

        Args:
            config: Loop Engineering 配置。
            template_registry: Loop 模板注册表（默认新建）。
            dispatcher_adapter: V3 DispatcherAdapter 实例（可选）。
            executor: 自定义执行器（可选），用于测试或绕过 DispatcherAdapter。
                签名为 (HandoffItem) -> Dict[str, Any]。
            log: 日志回调函数（可选）。
        """
        self._config = config
        self._template_registry = template_registry or LoopTemplateRegistry()
        self._dispatcher_adapter = dispatcher_adapter
        self._executor = executor
        self._log = log

    def _info(self, message: str) -> None:
        """输出 INFO 级别日志。"""
        if self._log:
            self._log(message, "INFO")

    def _warn(self, message: str) -> None:
        """输出 WARN 级别日志。"""
        if self._log:
            self._log(message, "WARN")

    def _generate_item_id(self) -> str:
        """生成工作项唯一标识。"""
        return f"wi-{uuid.uuid4().hex[:8]}"

    def create_work_items(
        self,
        discovery: DiscoveryResult,
        loop_type: str,
    ) -> List[HandoffItem]:
        """根据 Discovery 结果生成工作项列表。

        Args:
            discovery: Discovery 结果。
            loop_type: Loop 类型字符串（design / coding / testing）。

        Returns:
            List[HandoffItem]: 工作项列表。

        Raises:
            ValueError: loop_type 无法解析时抛出。
        """
        try:
            lt = LoopType(loop_type.lower())
        except ValueError as exc:
            raise ValueError(f"不支持的 loop_type: {loop_type}") from exc

        template = self._template_registry.get_template(lt)
        self._info(f"选择模板：{template.name} (loop_type={lt.value})")
        items = template.create_work_items(discovery)

        # 确保每个工作项都有 item_id（模板可能遗漏）
        for item in items:
            if not item.item_id:
                item.item_id = self._generate_item_id()

        return items

    def execute(
        self,
        items: List[HandoffItem],
        config: LoopEngineeringConfig,
    ) -> Dict[str, Any]:
        """执行工作项并构造 generator_result。

        执行流程：
        1. 若没有工作项，直接返回失败。
        2. 依次执行每个工作项（通过 DispatcherAdapter 或自定义 executor）。
        3. 汇总输出、成功状态和错误信息。
        4. 运行测试命令、静态检查、安全扫描（若配置允许）。
        5. 构造 generator_result。

        Args:
            items: 工作项列表。
            config: Loop Engineering 配置。

        Returns:
            Dict[str, Any]: Generator 执行结果，包含：
                - success: bool
                - output: str
                - test_result: dict
                - lint_result: dict
                - security_result: dict
                - modified_files: List[str]
                - committed_count: int
                - error: str
        """
        if not items:
            return {
                "success": False,
                "output": "",
                "error": "没有可执行的工作项",
                "test_result": {"passed": False, "summary": "无工作项"},
                "lint_result": {"passed": True, "summary": "未执行"},
                "security_result": {"severity": "info", "summary": "未执行"},
                "modified_files": [],
                "committed_count": 0,
            }

        outputs: List[str] = []
        all_success = True
        errors: List[str] = []
        skills_used: List[str] = []

        for item in items:
            self._info(f"执行工作项 {item.item_id}: {item.agent_type}")
            try:
                result = self._execute_single_item(item, config)
            except Exception as exc:
                self._warn(f"工作项 {item.item_id} 执行异常：{exc}")
                all_success = False
                errors.append(f"{item.agent_type}: {exc}")
                continue

            outputs.append(str(result.get("output", "")))
            if not result.get("success", False):
                all_success = False
                # 优先使用 result.error 描述，fallback 到 summary，确保错误信息完整
                error_detail = result.get("error", "") or result.get(
                    "summary", "未知错误"
                )
                errors.append(f"{item.agent_type} 执行未成功: {error_detail}")
            item_skills = result.get("skills_used", [])
            if isinstance(item_skills, list):
                skills_used.extend(item_skills)

        combined_output = "\n\n".join(outputs)

        # 执行客观指标检查（仅在至少一个工作项成功时运行，避免无意义检查）
        test_result = self._run_test_command(config)
        lint_result = self._run_lint_check(config)
        security_result = self._run_security_check(config)

        # 若 Generator 自评成功但测试未通过，整体视为未成功（诚实原则）
        if all_success and not test_result.get("passed", True):
            all_success = False
            errors.append(
                f"Generator 自评成功但测试未通过：{test_result.get('summary', '')}"
            )

        return {
            "success": all_success,
            "output": combined_output,
            "test_result": test_result,
            "lint_result": lint_result,
            "security_result": security_result,
            "modified_files": self._collect_modified_files(config),
            "committed_count": 1 if all_success and config.auto_commit else 0,
            "error": "; ".join(errors) if errors else "",
            "skills_used": list(set(skills_used)),
        }

    def _execute_single_item(
        self,
        item: HandoffItem,
        config: LoopEngineeringConfig,
    ) -> Dict[str, Any]:
        """执行单个工作项。

        优先级：
        1. 自定义 executor（测试/特殊场景）。
        2. DispatcherAdapter.invoke()（真实子代理调用）。
        3. 都不存在时返回失败。

        Args:
            item: 工作项。
            config: Loop 配置。

        Returns:
            Dict[str, Any]: 单工作项执行结果。
        """
        # 1. 自定义 executor
        if self._executor is not None:
            self._info(f"使用自定义 executor 执行 {item.agent_type}")
            return self._executor(item)

        # 2. DispatcherAdapter
        if self._dispatcher_adapter is not None:
            self._info(f"使用 DispatcherAdapter 调用 {item.agent_type}")
            adapter_result = self._dispatcher_adapter.invoke(
                task=item.task,
                agent=item.agent_type,
            )
            return {
                "success": adapter_result.success,
                "output": adapter_result.output,
                "summary": adapter_result.summary,
                "skills_used": adapter_result.skills_used,
                "error": str(adapter_result.error) if adapter_result.error else "",
            }

        # 3. 无可用执行器
        self._warn(f"没有可用的执行器执行 {item.agent_type}，返回失败")
        return {
            "success": False,
            "output": "",
            "summary": "无可用执行器",
            "error": "无可用执行器：DispatcherAdapter 和自定义 executor 都未配置",
        }

    def _run_test_command(self, config: LoopEngineeringConfig) -> Dict[str, Any]:
        """运行配置的测试命令。

        Args:
            config: Loop 配置。

        Returns:
            Dict[str, Any]: 测试结果，包含 passed / summary / returncode。
        """
        command = config.test_command
        if not command:
            return {"passed": True, "summary": "未配置测试命令", "returncode": 0}

        self._info(f"运行测试命令：{command}")
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=config.project_root,
                capture_output=True,
                text=True,
                timeout=config.test_timeout_sec,
            )
            passed = proc.returncode == 0
            summary = (
                f"returncode={proc.returncode}; "
                f"stdout={proc.stdout[:500]}; stderr={proc.stderr[:500]}"
            )
            return {
                "passed": passed,
                "summary": summary,
                "returncode": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        except subprocess.TimeoutExpired:
            return {
                "passed": False,
                "summary": f"测试命令超时（>{config.test_timeout_sec}s）",
                "returncode": -1,
            }
        except Exception as exc:
            return {
                "passed": False,
                "summary": f"测试命令执行异常：{exc}",
                "returncode": -1,
            }

    def _run_lint_check(self, config: LoopEngineeringConfig) -> Dict[str, Any]:
        """运行 lint / typecheck 检查。

        当前实现优先尝试 ruff，其次 mypy。命令不存在时返回 info 级别结果。

        Args:
            config: Loop 配置。

        Returns:
            Dict[str, Any]: lint 结果。
        """
        project_root = config.project_root
        commands = [
            ("ruff", "ruff check ."),
            ("mypy", "mypy --strict ."),
        ]
        for tool, cmd in commands:
            if not self._command_available(tool):
                continue
            self._info(f"运行静态检查：{cmd}")
            try:
                proc = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    timeout=120.0,
                )
                passed = proc.returncode == 0
                return {
                    "passed": passed,
                    "tool": tool,
                    "summary": (
                        f"{tool} returncode={proc.returncode}; "
                        f"output={proc.stdout[:500]}{proc.stderr[:500]}"
                    ),
                    "returncode": proc.returncode,
                }
            except Exception as exc:
                return {
                    "passed": False,
                    "tool": tool,
                    "summary": f"{tool} 执行异常：{exc}",
                    "returncode": -1,
                }

        return {
            "passed": True,
            "tool": "none",
            "summary": "未找到 ruff/mypy，跳过静态检查",
            "returncode": 0,
        }

    def _run_security_check(self, config: LoopEngineeringConfig) -> Dict[str, Any]:
        """运行安全扫描。

        Args:
            config: Loop 配置。

        Returns:
            Dict[str, Any]: 安全扫描结果。
        """
        analyzer = config.security_analyzer
        project_root = config.project_root

        if analyzer == "builtin":
            # builtin 模式：检查常见风险关键词（真实但轻量级）
            return self._run_builtin_security_check(project_root)

        if analyzer == "bandit":
            if not self._command_available("bandit"):
                return {
                    "severity": "warning",
                    "summary": "bandit 未安装，跳过安全扫描",
                    "tool": "bandit",
                }
            return self._run_external_security(
                project_root, "bandit", "bandit -r .", critical_returncodes={1}
            )

        if analyzer == "semgrep":
            if not self._command_available("semgrep"):
                return {
                    "severity": "warning",
                    "summary": "semgrep 未安装，跳过安全扫描",
                    "tool": "semgrep",
                }
            return self._run_external_security(
                project_root,
                "semgrep",
                "semgrep --config=auto .",
                critical_returncodes={1},
            )

        return {
            "severity": "info",
            "summary": f"未知安全分析器：{analyzer}",
            "tool": analyzer,
        }

    def _run_builtin_security_check(self, project_root: Path) -> Dict[str, Any]:
        """内置轻量级安全扫描。

        扫描项目根目录下常见风险文件或字符串（如硬编码密码）。
        这是一个真实的静态扫描，而非模拟。

        Args:
            project_root: 项目根目录。

        Returns:
            Dict[str, Any]: 安全扫描结果。
        """
        risk_patterns = ["password", "secret", "token", "api_key", "apikey"]
        findings: List[str] = []
        max_files = 50
        checked = 0

        try:
            for path in project_root.rglob("*.py"):
                if checked >= max_files:
                    break
                checked += 1
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                    lower = text.lower()
                    for pattern in risk_patterns:
                        if pattern in lower:
                            # 仅记录文件名，不记录敏感内容
                            findings.append(f"{path.name}: 包含 '{pattern}' 关键词")
                            break
                except OSError:
                    continue
        except Exception as exc:
            return {
                "severity": "warning",
                "summary": f"内置安全扫描异常：{exc}",
                "tool": "builtin",
            }

        if findings:
            return {
                "severity": "warning",
                "summary": f"builtin 安全扫描发现 {len(findings)} 个关键词风险：{findings[:5]}",
                "tool": "builtin",
                "findings": findings,
            }

        return {
            "severity": "info",
            "summary": f"builtin 安全扫描完成（检查 {checked} 个文件，无高风险关键词）",
            "tool": "builtin",
        }

    def _run_external_security(
        self,
        project_root: Path,
        tool: str,
        command: str,
        critical_returncodes: set[int],
    ) -> Dict[str, Any]:
        """运行外部安全扫描工具。

        Args:
            project_root: 项目根目录。
            tool: 工具名称。
            command: 命令字符串。
            critical_returncodes: 视为严重问题的返回码集合。

        Returns:
            Dict[str, Any]: 安全扫描结果。
        """
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=300.0,
            )
            if proc.returncode in critical_returncodes:
                severity = "high"
            elif proc.returncode != 0:
                severity = "warning"
            else:
                severity = "info"
            return {
                "severity": severity,
                "summary": (
                    f"{tool} returncode={proc.returncode}; "
                    f"output={proc.stdout[:500]}{proc.stderr[:500]}"
                ),
                "tool": tool,
                "returncode": proc.returncode,
            }
        except Exception as exc:
            return {
                "severity": "warning",
                "summary": f"{tool} 执行异常：{exc}",
                "tool": tool,
            }

    def _collect_modified_files(self, config: LoopEngineeringConfig) -> List[str]:
        """收集工作区中已修改的文件列表。

        使用 git status --porcelain 获取真实变更文件。非 git 仓库返回空列表。

        Args:
            config: Loop 配置。

        Returns:
            List[str]: 修改的文件路径列表。
        """
        try:
            proc = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=config.project_root,
                capture_output=True,
                text=True,
                timeout=30.0,
            )
            if proc.returncode != 0:
                return []
            files = []
            for line in proc.stdout.splitlines():
                if len(line) >= 3:
                    files.append(line[3:].strip())
            return files
        except Exception:
            return []

    def _command_available(self, command: str) -> bool:
        """检查命令是否可用。

        Args:
            command: 命令名称。

        Returns:
            bool: 是否可用。
        """
        try:
            proc = subprocess.run(
                ["which", command],
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            return proc.returncode == 0
        except Exception:
            return False


__all__ = ["HandoffAdapter"]
