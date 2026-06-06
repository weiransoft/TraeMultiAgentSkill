#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Loop Goal Executor（/loop + /goal 集成模块）

Phase 11 实现：为 trae-multi-agent 增加 /loop 和 /goal 两个用户命令。

核心职责：
1. Goal 数据模型：目标 ID、描述、验收标准、状态、迭代历史
2. LoopConfig 数据模型：最大迭代次数、收敛窗口、成功后停止
3. GoalRegistry：目标 CRUD + 磁盘持久化（.trae/goals/<goal_id>.json）
4. ConvergenceDetector：连续 N 次无新产出 → 提前退出
5. GoalVerifier：关键词 / 可调用对象 两种模式校验验收标准
6. LoopGoalExecutor：解析 + 执行 /loop + /goal 流程

设计约束（来自 DYNAMIC_WORKFLOWS_INTEGRATION.md §3.0）：
- 🔴 持久化复用：使用文件 + 原子写，不引入新存储
- 🔴 V2 不修改：本模块独立运行；只通过 dispatch_agent_v2 接口调用
- 🔴 安全：max_iterations 硬上限 100；goal_id 强制 kebab-case
- 🔴 一阶段一模块：仅做 loop+goal 编排，不引入新调度逻辑
- 🔴 可选注入：Karpathy 联动可选；缺 Karpathy 时仅记录日志

参考来源：
- [PHASE11_PLAN.md v1.0]
- [DYNAMIC_WORKFLOWS_INTEGRATION.md v1.6]
- [AUTO_CONTINUE_EXAMPLES.md]

作者：trae-multi-agent 融合 Phase 11
创建日期：2026-06-05
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil  # Phase 12 修复（Issue 2）：从函数内局部 import 移至文件顶部，保持模块级导入风格一致
import threading
import time
import uuid
from copy import deepcopy  # Phase 12 修复（Issue 4）：用于 _save_goal_atomic_with_lock 避免修改入参
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union  # Phase 12 修复（Issue 3）：Union 用于 dispatch_fn 返回值类型


# ============================================================================
# 日志配置
# ============================================================================

logger = logging.getLogger("loop_goal")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# Phase 11 P0-2 修复：跨进程并发（fcntl.flock）
# 注意：fcntl 在 Windows 不可用，try-import 优雅降级
try:
    import fcntl  # type: ignore[import-not-found]
    FCNTL_AVAILABLE = True
except ImportError:
    FCNTL_AVAILABLE = False
    logger.debug("fcntl 不可用（Windows 平台？），跨进程并发退化为进程内 RLock 保护")


# ============================================================================
# 常量定义
# ============================================================================

# 路径处理：loop_goal.py 在 scripts/ 下
SCRIPTS_DIR = Path(__file__).resolve().parent

# Goal ID 命名规范（kebab-case）
GOAL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$")

# max_iterations 安全上限
MAX_ITERATIONS_LIMIT = 100
MIN_ITERATIONS = 1

# 默认收敛窗口
DEFAULT_CONVERGENCE_WINDOW = 3

# 持久化文件名
GOAL_FILENAME = "goal.json"


# ============================================================================
# Phase 13.1: Schema 版本与多 Goal 编排枚举
# ============================================================================

# Goal JSON schema 版本
# - "12.0"：Phase 12 及之前（无多 Goal 编排字段）
# - "13.0"：Phase 13 起（多 Goal 编排字段，Optional 向后兼容）
SCHEMA_VERSION = "13.0"


class GoalAggregationStrategy(str, Enum):
    """父 Goal 聚合子 Goal 验收的策略（Phase 13.1）。

    - AND：所有子 Goal ACHIEVED → 父 Goal 满足
    - OR：任一子 Goal ACHIEVED → 父 Goal 满足
    - MAJORITY：≥半数子 Goal ACHIEVED → 父 Goal 满足
    """
    AND = "AND"
    OR = "OR"
    MAJORITY = "MAJORITY"

    @classmethod
    def from_str(cls, value: str) -> "GoalAggregationStrategy":
        """从字符串解析（大小写敏感；Phase 13 约定大写）。"""
        if not isinstance(value, str):
            raise LoopGoalError(
                f"GoalAggregationStrategy 必须是字符串：{type(value).__name__}"
            )
        normalized = value.strip()
        for s in cls:
            if s.value == normalized:
                return s
        raise LoopGoalError(
            f"未知 GoalAggregationStrategy：{value!r}（有效值：{[s.value for s in cls]}）"
        )


# ============================================================================
# 异常定义
# ============================================================================

class LoopGoalError(Exception):
    """loop_goal 模块异常基类"""


class InvalidGoalIdError(LoopGoalError):
    """非法的 goal_id（不符合 kebab-case）"""


class InvalidLoopConfigError(LoopGoalError):
    """非法的 LoopConfig 参数"""


class GoalNotFoundError(LoopGoalError):
    """目标不存在"""


class GoalRegistryError(LoopGoalError):
    """目标注册表 IO/解析异常"""


class GoalStatusTransitionError(LoopGoalError):
    """非法的状态转换"""


# ============================================================================
# 枚举定义
# ============================================================================

class GoalStatus(str, Enum):
    """
    目标状态枚举

    状态机：
        ACTIVE       ─ 创建后初始状态
        IN_PROGRESS  ─ 第一次 iteration 开始后
        ACHIEVED     ─ 全部 success_criteria 满足
        ABANDONED    ─ 用户主动放弃
        FAILED       ─ 超过 max_iterations 仍未达成
    """
    ACTIVE      = "active"
    IN_PROGRESS = "in_progress"
    ACHIEVED    = "achieved"
    ABANDONED   = "abandoned"
    FAILED      = "failed"

    @classmethod
    def from_str(cls, value: str) -> "GoalStatus":
        """从字符串解析（大小写不敏感）"""
        if not isinstance(value, str):
            raise LoopGoalError(f"GoalStatus 必须是字符串：{type(value).__name__}")
        normalized = value.lower().strip()
        for status in cls:
            if status.value == normalized:
                return status
        raise LoopGoalError(f"未知 GoalStatus：{value}（有效值：{[s.value for s in cls]}）")


# 合法状态转换表
ALLOWED_STATUS_TRANSITIONS: Dict[GoalStatus, List[GoalStatus]] = {
    GoalStatus.ACTIVE: [GoalStatus.IN_PROGRESS, GoalStatus.ABANDONED],
    GoalStatus.IN_PROGRESS: [
        GoalStatus.ACHIEVED, GoalStatus.FAILED, GoalStatus.ABANDONED,
    ],
    GoalStatus.ACHIEVED: [],  # 终态
    GoalStatus.ABANDONED: [],  # 终态
    GoalStatus.FAILED: [GoalStatus.IN_PROGRESS],  # 允许重启
}


# ============================================================================
# 数据类定义
# ============================================================================

@dataclass
class IterationResult:
    """
    单次迭代结果

    字段：
    - iteration_no: 迭代序号（从 1 开始）
    - success: dispatch 是否成功
    - outputs: 产出指纹字典（文件修改数 / 测试结果 / 警告数等）
    - started_at: ISO 格式开始时间
    - finished_at: ISO 格式结束时间
    - execution_time_seconds: 执行耗时（秒）
    - error: 错误信息（成功时为 None）
    - criteria_met: 本次 iteration 满足的 criterion 列表
    """
    iteration_no: int
    success: bool
    outputs: Dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""
    execution_time_seconds: float = 0.0
    error: Optional[str] = None
    criteria_met: List[str] = field(default_factory=list)

    def __post_init__(self):
        """字段合法性校验"""
        if self.iteration_no < 1:
            raise LoopGoalError(f"iteration_no 必须 >= 1：{self.iteration_no}")
        if self.execution_time_seconds < 0:
            raise LoopGoalError(
                f"execution_time_seconds 不能为负：{self.execution_time_seconds}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """转字典（用于持久化）"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IterationResult":
        """从字典反序列化"""
        return cls(**data)

    def fingerprint(self) -> str:
        """
        计算产出指纹（用于收敛检测）

        指纹格式：files_modified|tests_passed|tests_failed|warnings_count|errors_count
        """
        return (
            f"{self.outputs.get('files_modified', 0)}|"
            f"{self.outputs.get('tests_passed', 0)}|"
            f"{self.outputs.get('tests_failed', 0)}|"
            f"{self.outputs.get('warnings_count', 0)}|"
            f"{self.outputs.get('errors_count', 0)}"
        )


@dataclass
class LoopConfig:
    """
    循环执行配置

    字段：
    - max_iterations: 最大迭代次数（1 = 不循环；上限 100）
    - convergence_window: 收敛检测窗口（连续 N 次无新产出则提前退出）
    - stop_on_success: 全部 criterion 满足时是否提前停止
    - inter_iteration_delay_seconds: 两次 iteration 间隔（避免过快消耗资源）
    """
    max_iterations: int = 1
    convergence_window: int = DEFAULT_CONVERGENCE_WINDOW
    stop_on_success: bool = True
    inter_iteration_delay_seconds: float = 0.0

    def __post_init__(self):
        """字段合法性校验"""
        if not (MIN_ITERATIONS <= self.max_iterations <= MAX_ITERATIONS_LIMIT):
            raise InvalidLoopConfigError(
                f"max_iterations 必须在 [{MIN_ITERATIONS}, {MAX_ITERATIONS_LIMIT}] 范围内："
                f"{self.max_iterations}"
            )
        if self.convergence_window < 1:
            raise InvalidLoopConfigError(
                f"convergence_window 必须 >= 1：{self.convergence_window}"
            )
        if self.inter_iteration_delay_seconds < 0:
            raise InvalidLoopConfigError(
                f"inter_iteration_delay_seconds 不能为负：{self.inter_iteration_delay_seconds}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """转字典"""
        return asdict(self)


@dataclass
class Goal:
    """
    目标数据模型

    字段：
    - goal_id: 目标 ID（kebab-case）
    - description: 目标描述
    - success_criteria: 验收标准列表
    - status: 当前状态
    - iterations: 迭代历史
    - max_iterations: 循环配置（冗余存储便于恢复）
    - convergence_window: 收敛窗口
    - created_at: ISO 格式创建时间
    - updated_at: ISO 格式更新时间
    - achieved_at: ISO 格式达成时间
    - created_by: 创建者（agent_type）
    - task_template: 每次 iteration 执行的 task 描述

    Phase 13.1 新增字段（多 Goal 编排）：
    - schema_version: Goal JSON schema 版本（B3 修复，向后兼容）
    - parent_goal_id: 父 Goal ID（单亲），None 表示 root goal
    - depends_on: DAG 边列表（本 Goal 必须等待这些 Goal 完成后才能启动）
    - aggregation_strategy: 父 Goal 聚合子 Goal 验收的策略（AND/OR/MAJORITY）
    - resume_count: 已续跑次数
    - max_resume_count: 续跑次数上限（默认 3）
    """
    goal_id: str
    description: str
    success_criteria: List[str] = field(default_factory=list)
    status: GoalStatus = GoalStatus.ACTIVE
    iterations: List[IterationResult] = field(default_factory=list)
    max_iterations: int = 10
    convergence_window: int = DEFAULT_CONVERGENCE_WINDOW
    created_at: str = ""
    updated_at: str = ""
    achieved_at: Optional[str] = None
    created_by: str = "user"
    task_template: str = ""

    # Phase 13.1: schema_version（B3 修复 - 向后兼容）
    # 旧 JSON 无此字段时，从 from_dict 入口补默认 "13.0"。
    schema_version: str = SCHEMA_VERSION

    # Phase 13.1: 多 Goal 编排字段（全部 Optional / 有默认值 → 100% 向后兼容）
    parent_goal_id: Optional[str] = None
    """父 Goal ID（单亲）。None 表示 root goal。"""

    depends_on: List[str] = field(default_factory=list)
    """DAG 边列表：本 Goal 必须等待这些 Goal 完成后才能启动。"""

    aggregation_strategy: GoalAggregationStrategy = GoalAggregationStrategy.AND
    """父 Goal 聚合子 Goal 验收的策略（枚举；默认 AND）。"""

    resume_count: int = 0
    """已续跑次数。超过 max_resume_count → 标记 ABANDONED。"""

    max_resume_count: int = 3
    """续跑次数上限（默认 3）。"""

    def __post_init__(self):
        """字段合法性校验。"""
        if not isinstance(self.goal_id, str) or not GOAL_ID_PATTERN.match(self.goal_id):
            raise InvalidGoalIdError(
                f"goal_id '{self.goal_id}' 不符合 kebab-case 命名规范（{GOAL_ID_PATTERN.pattern}）"
            )
        if not self.description:
            raise LoopGoalError("description 不能为空")
        if not isinstance(self.success_criteria, list):
            raise LoopGoalError("success_criteria 必须是 list")
        if not (MIN_ITERATIONS <= self.max_iterations <= MAX_ITERATIONS_LIMIT):
            raise LoopGoalError(
                f"max_iterations 必须在 [{MIN_ITERATIONS}, {MAX_ITERATIONS_LIMIT}] 范围内："
                f"{self.max_iterations}"
            )
        if not isinstance(self.status, GoalStatus):
            # 尝试从字符串转换
            if isinstance(self.status, str):
                self.status = GoalStatus.from_str(self.status)
            else:
                raise LoopGoalError(
                    f"status 必须是 GoalStatus 枚举：{type(self.status).__name__}"
                )

        # Phase 13.1: aggregation_strategy 字符串 → 枚举转换 + 校验
        if isinstance(self.aggregation_strategy, str):
            try:
                self.aggregation_strategy = GoalAggregationStrategy.from_str(
                    self.aggregation_strategy
                )
            except LoopGoalError as e:
                # 包装为 LoopGoalError（已经是 LoopGoalError 子类，但保留原 chain）
                raise LoopGoalError(
                    f"aggregation_strategy 非法：{e}"
                ) from e
        elif not isinstance(self.aggregation_strategy, GoalAggregationStrategy):
            raise LoopGoalError(
                f"aggregation_strategy 必须是 GoalAggregationStrategy 枚举或字符串，"
                f"收到 {type(self.aggregation_strategy).__name__}"
            )

        # Phase 13.1: depends_on 必须是 list[str]
        if not isinstance(self.depends_on, list):
            raise LoopGoalError(
                f"depends_on 必须是 list[str]，收到 {type(self.depends_on).__name__}"
            )
        for i, dep in enumerate(self.depends_on):
            if not isinstance(dep, str):
                raise LoopGoalError(
                    f"depends_on[{i}] 必须是 str，收到 {type(dep).__name__}"
                )

    def transition_to(self, new_status: GoalStatus) -> None:
        """
        状态转换（带合法性校验）

        合法转换见 ALLOWED_STATUS_TRANSITIONS。

        Args:
            new_status: 目标新状态

        Raises:
            GoalStatusTransitionError: 非法状态转换
        """
        if not isinstance(new_status, GoalStatus):
            raise GoalStatusTransitionError(
                f"new_status 必须是 GoalStatus：{type(new_status).__name__}"
            )
        allowed = ALLOWED_STATUS_TRANSITIONS.get(self.status, [])
        if new_status not in allowed:
            raise GoalStatusTransitionError(
                f"非法状态转换：{self.status.value} → {new_status.value}。"
                f"{self.status.value} 允许转换到：{[s.value for s in allowed]}"
            )
        self.status = new_status
        self.updated_at = datetime.now().isoformat()
        if new_status == GoalStatus.ACHIEVED and not self.achieved_at:
            self.achieved_at = self.updated_at

    def get_latest_iteration(self) -> Optional[IterationResult]:
        """获取最近一次 iteration"""
        return self.iterations[-1] if self.iterations else None

    def get_progress(self) -> Dict[str, Any]:
        """
        计算目标进度统计

        Returns:
            包含 total_iterations / successful_iterations / convergence_detected 等
        """
        total = len(self.iterations)
        successful = sum(1 for i in self.iterations if i.success)
        return {
            "goal_id": self.goal_id,
            "status": self.status.value,
            "total_iterations": total,
            "successful_iterations": successful,
            "failed_iterations": total - successful,
            "convergence_detected": False,  # 由 ConvergenceDetector 计算
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_dict(self) -> Dict[str, Any]:
        """转字典（用于持久化）"""
        d = asdict(self)
        # status 转为字符串
        d["status"] = self.status.value
        # Phase 13.1: aggregation_strategy 枚举 → 字符串
        d["aggregation_strategy"] = self.aggregation_strategy.value
        # Phase 13.1: schema_version 已经是 str 类型，无需转换
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Goal":
        """从字典反序列化。

        Phase 13.1 B3 修复：缺 schema_version 时默认为 "13.0"（向后兼容）。
        """
        data = dict(data)  # 复制避免修改原数据
        # status 字符串 → 枚举
        if "status" in data and isinstance(data["status"], str):
            data["status"] = GoalStatus.from_str(data["status"])
        # iterations 字典列表 → IterationResult 列表
        if "iterations" in data and isinstance(data["iterations"], list):
            data["iterations"] = [
                IterationResult.from_dict(i) for i in data["iterations"]
            ]
        # Phase 13.1 B3 修复：schema_version 缺省时填 "13.0"
        if "schema_version" not in data or data["schema_version"] in (None, ""):
            data["schema_version"] = SCHEMA_VERSION
        # Phase 13.1: parent_goal_id 缺省为 None（由 dataclass 默认值处理，
        #   但仍显式处理：旧 JSON 中可能缺失）
        if "parent_goal_id" not in data:
            data["parent_goal_id"] = None
        # Phase 13.1: depends_on 缺省为 []
        if "depends_on" not in data:
            data["depends_on"] = []
        # Phase 13.1: aggregation_strategy 缺省为 AND（字符串）
        if "aggregation_strategy" not in data or data["aggregation_strategy"] in (None, ""):
            data["aggregation_strategy"] = "AND"
        # Phase 13.1: resume_count / max_resume_count 缺省为 0 / 3
        if "resume_count" not in data:
            data["resume_count"] = 0
        if "max_resume_count" not in data:
            data["max_resume_count"] = 3
        return cls(**data)


# ============================================================================
# 收敛检测器
# ============================================================================

class ConvergenceDetector:
    """
    收敛检测器

    算法：取最近 N 次 iteration 的产出指纹；
    若全部相同 → 视为收敛，提前退出循环。
    """

    def __init__(self, window: int = DEFAULT_CONVERGENCE_WINDOW):
        """
        初始化收敛检测器

        Args:
            window: 收敛窗口（连续 N 次无新产出则触发）
        """
        if window < 1:
            raise LoopGoalError(f"window 必须 >= 1：{window}")
        self._window = window

    @property
    def window(self) -> int:
        """返回收敛窗口"""
        return self._window

    def is_converged(self, iterations: List[IterationResult]) -> bool:
        """
        判断是否已收敛

        Args:
            iterations: 全部 iteration 历史

        Returns:
            True 表示已收敛（应提前退出）；False 表示继续
        """
        if not iterations or len(iterations) < self._window:
            return False
        recent = iterations[-self._window:]
        fingerprints = [i.fingerprint() for i in recent]
        # 去重后若仅 1 个 → 全部相同 → 收敛
        return len(set(fingerprints)) == 1

    def get_convergence_info(self, iterations: List[IterationResult]) -> Dict[str, Any]:
        """
        获取收敛诊断信息（用于日志和报告）

        Args:
            iterations: 全部 iteration 历史

        Returns:
            包含 converged / window / fingerprints / unique_count
        """
        if not iterations:
            return {
                "converged": False,
                "window": self._window,
                "recent_count": 0,
                "unique_fingerprints": 0,
                "reason": "no_iterations",
            }
        recent = iterations[-self._window:]
        fingerprints = [i.fingerprint() for i in recent]
        unique = set(fingerprints)
        return {
            "converged": len(unique) == 1 and len(recent) == self._window,
            "window": self._window,
            "recent_count": len(recent),
            "unique_fingerprints": len(unique),
            "fingerprints": fingerprints,
            "reason": "all_same" if len(unique) == 1 else "different",
        }


# ============================================================================
# 目标验证器
# ============================================================================

class GoalVerifier:
    """
    目标验证器：检查 success_criteria 是否满足

    支持两种模式：
    1. 关键词模式：criterion 是字符串，根据关键词匹配 outputs 字段
    2. 可调用对象模式：criterion 是 callable(iteration) -> bool

    默认关键词规则：
    - "tests pass" / "all tests pass" / "测试通过" → outputs.tests_failed == 0
    - "no warnings" / "无警告" → outputs.warnings_count == 0
    - "no errors" / "无错误" → outputs.errors_count == 0
    - "code committed" / "代码已提交" → outputs.git_committed == True
    - "files modified" / "有代码改动" → outputs.files_modified > 0

    Phase 11 P0-3 修复：
    - 移除"all criteria met" / "目标达成"占位规则（永远通过 → 误判根因）
    - 否定词检测：criterion 包含否定词（不/没/未/非/无/拒绝 等）→ 返回 False
    - 兜底逻辑保守化：未命中规则时按"不满足"处理，不再按 pass/成功 关键词盲目通过

    Phase 11 P0-4 修复：
    - 模糊匹配改为严格子串：仅当 rule_key 是 criterion 的子串（criterion 更长）才匹配
      —— 避免"all"误匹配"all tests pass"导致长 criterion 被截断
    - 多 criterion 之间使用 AND 语义：所有 criterion 满足才整体满足
    """

    # 否定词黑名单（出现即视为"不满足"语义）
    # Phase 11 P0-3：未命中规则但包含否定词 → 直接返回 False
    NEGATION_WORDS: Tuple[str, ...] = (
        # 英文否定词
        "not ", "no ", "never ", "none ", "neither ", "nor ",
        "without ", "lack ", "missing ", "fail", "failed",
        # 中文否定词
        "不", "没", "未", "非", "无", "拒绝", "失败", "错误", "异常",
    )

    # 关键词 → 验证函数映射（默认）
    # Phase 11 P0-3：删除 "all criteria met" / "目标达成" 占位规则
    DEFAULT_KEYWORD_RULES: Dict[str, Callable[[Dict[str, Any]], bool]] = {
        "tests pass": lambda o: o.get("tests_failed", -1) == 0,
        "all tests pass": lambda o: o.get("tests_failed", -1) == 0
        and o.get("tests_run", 0) > 0,
        "测试通过": lambda o: o.get("tests_failed", -1) == 0,
        "no warnings": lambda o: o.get("warnings_count", 0) == 0,
        "无警告": lambda o: o.get("warnings_count", 0) == 0,
        "no errors": lambda o: o.get("errors_count", 0) == 0,
        "无错误": lambda o: o.get("errors_count", 0) == 0,
        "code committed": lambda o: o.get("git_committed", False) is True,
        "代码已提交": lambda o: o.get("git_committed", False) is True,
        "files modified": lambda o: o.get("files_modified", 0) > 0,
        "有代码改动": lambda o: o.get("files_modified", 0) > 0,
    }

    def __init__(
        self,
        custom_rules: Optional[Dict[str, Callable[[Dict[str, Any]], bool]]] = None,
    ):
        """
        初始化验证器

        Args:
            custom_rules: 自定义关键词规则（覆盖默认）
        """
        self._rules: Dict[str, Callable[[Dict[str, Any]], bool]] = dict(
            self.DEFAULT_KEYWORD_RULES
        )
        if custom_rules:
            self._rules.update(custom_rules)

    def _has_negation(self, criterion_lower: str) -> bool:
        """
        检测 criterion 是否包含否定语义（Phase 11 P0-3）

        Args:
            criterion_lower: 小写化后的 criterion

        Returns:
            True 表示包含否定词
        """
        return any(neg in criterion_lower for neg in self.NEGATION_WORDS)

    def check_criterion(
        self, criterion: str, iteration: IterationResult
    ) -> bool:
        """
        检查单个 criterion 是否满足

        Phase 11 P0-3 + P0-4 修复：
        1. 严格子串匹配：双向支持（rule_key ⊂ criterion 或 criterion 是 rule_key 有效前缀）
        2. 否定词检测（兜底）：仅在未命中任何规则时启用
           —— 避免误判"no warnings" / "无警告"等合法规则（含"no"/"无"前缀）
        3. 兜底保守化：未命中规则 + 含否定词 → False；未命中规则 + 不含否定词 → False
           （不再按 pass/成功 关键词盲目通过）

        Args:
            criterion: 验收标准字符串
            iteration: 本次 iteration 结果

        Returns:
            True 表示满足；False 表示不满足
        """
        if not isinstance(criterion, str):
            return False
        criterion_lower = criterion.lower().strip()
        if not criterion_lower:
            return False

        # P0-4 修复：双向子串匹配 + 长度门控
        # 原逻辑"criterion_lower in rule_key"过于宽松：
        #   "all" in "all tests pass" → True（误匹配）
        # 新逻辑：仅当 criterion 是 rule_key 的"有效前缀"（长度差 <= 30%）时才匹配
        # 严格子串（rule_key ⊂ criterion）仍可正常匹配
        matched = False
        for rule_key, rule_fn in self._rules.items():
            # 精确匹配优先
            if criterion_lower == rule_key:
                try:
                    return bool(rule_fn(iteration.outputs))
                except Exception as e:
                    logger.warning(
                        f"criterion '{criterion}' 验证函数异常：{e}"
                    )
                    return False
            # 严格子串：rule_key ⊂ criterion_lower（criterion 包含规则关键词）
            if rule_key in criterion_lower:
                try:
                    if bool(rule_fn(iteration.outputs)):
                        return True
                    matched = True
                except Exception as e:
                    logger.warning(
                        f"criterion '{criterion}' 验证函数异常：{e}"
                    )
                    # 单规则失败 → 继续尝试其他规则
                continue
            # P0-4 长度门控：criterion 是 rule_key 的有效前缀（差值 <= 30%）
            if (
                rule_key.startswith(criterion_lower)
                and len(rule_key) <= len(criterion_lower) * 1.3 + 3
            ):
                try:
                    if bool(rule_fn(iteration.outputs)):
                        return True
                    matched = True
                except Exception as e:
                    logger.warning(
                        f"criterion '{criterion}' 验证函数异常：{e}"
                    )

        if matched:
            # 命中规则但验证函数全部返回 False → 视为不满足
            return False

        # 已尝试所有规则但未命中
        # P0-3 修复 #2：未命中规则 → 检测否定词 → 视为不满足
        # 仅在兜底时检测否定词，避免误判合法规则（如"no warnings"含"no "）
        if self._has_negation(criterion_lower):
            logger.debug(
                f"criterion '{criterion}' 未命中规则且包含否定词，视为不满足"
            )
            return False

        # P0-3 修复 #3：兜底保守化
        # 原逻辑：包含 "pass/通过/成功/complete" → True（过于乐观，易误判）
        # 新逻辑：未命中任何规则 → 直接返回 False
        logger.debug(
            f"criterion '{criterion}' 未匹配任何规则，视为不满足"
        )
        return False

    def check_all_criteria(
        self, goal: Goal, iteration: IterationResult
    ) -> Tuple[bool, List[str]]:
        """
        检查所有 criterion 是否满足

        Args:
            goal: 目标（含 success_criteria）
            iteration: 本次 iteration 结果

        Returns:
            (all_met, met_list) → all_met 表示是否全部满足；
            met_list 是本次满足的 criterion 列表
        """
        if not goal.success_criteria:
            # 无 criterion → 视为满足（空目标）
            return True, []

        met = []
        for criterion in goal.success_criteria:
            if self.check_criterion(criterion, iteration):
                met.append(criterion)
        return len(met) == len(goal.success_criteria), met

    def is_criterion_met(self, criterion: str, iteration: IterationResult) -> bool:
        """
        公开 API：检查单个 criterion（check_criterion 的别名）

        Args:
            criterion: 验收标准字符串
            iteration: 本次 iteration 结果

        Returns:
            True 表示满足
        """
        return self.check_criterion(criterion, iteration)


# ============================================================================
# Goal Registry（目标注册表 + 持久化）
# ============================================================================

class GoalRegistry:
    """
    目标注册表

    持久化路径：{storage_root}/{goal_id}/{GOAL_FILENAME}
    使用临时文件 + os.replace 实现原子写。

    线程安全：所有读/写均通过 _lock 保护。
    """

    def __init__(self, storage_root: str = ".trae/goals"):
        """
        初始化注册表

        Args:
            storage_root: 存储根目录（相对或绝对路径）
        """
        self._storage_root = Path(storage_root)
        self._lock = threading.RLock()
        self._ensure_storage_dir()

        logger.info(
            f"GoalRegistry 初始化完成：storage_root={self._storage_root.absolute()}"
        )

    def _ensure_storage_dir(self) -> None:
        """确保存储根目录存在"""
        self._storage_root.mkdir(parents=True, exist_ok=True)

    def _get_goal_dir(self, goal_id: str) -> Path:
        """获取目标的目录路径"""
        if not GOAL_ID_PATTERN.match(goal_id):
            raise InvalidGoalIdError(
                f"goal_id '{goal_id}' 不符合 kebab-case 命名规范"
            )
        return self._storage_root / goal_id

    def _get_goal_file(self, goal_id: str) -> Path:
        """获取目标 JSON 文件路径"""
        return self._get_goal_dir(goal_id) / GOAL_FILENAME

    def create_goal(
        self,
        description: str,
        criteria: Optional[List[str]] = None,
        goal_id: Optional[str] = None,
        max_iterations: int = 10,
        convergence_window: int = DEFAULT_CONVERGENCE_WINDOW,
        created_by: str = "user",
        task_template: str = "",
    ) -> Goal:
        """
        创建新目标并持久化

        Args:
            description: 目标描述
            criteria: 验收标准列表（None → 空列表）
            goal_id: 目标 ID（None → 自动生成）
            max_iterations: 最大迭代次数
            convergence_window: 收敛窗口
            created_by: 创建者
            task_template: 每次 iteration 执行的 task 描述

        Returns:
            创建的 Goal 实例
        """
        if not description:
            raise LoopGoalError("description 不能为空")
        # 允许 None 默认为空列表
        if criteria is None:
            criteria = []
        if not isinstance(criteria, list):
            raise LoopGoalError("criteria 必须是 list")
        if not (MIN_ITERATIONS <= max_iterations <= MAX_ITERATIONS_LIMIT):
            raise LoopGoalError(
                f"max_iterations 必须在 [{MIN_ITERATIONS}, {MAX_ITERATIONS_LIMIT}] 范围内："
                f"{max_iterations}"
            )

        # 生成 goal_id
        if goal_id is None:
            goal_id = f"goal-{uuid.uuid4().hex[:8]}"
        elif not GOAL_ID_PATTERN.match(goal_id):
            raise InvalidGoalIdError(
                f"goal_id '{goal_id}' 不符合 kebab-case 命名规范"
            )

        # 防止覆盖现有目标
        with self._lock:
            goal_file = self._get_goal_file(goal_id)
            if goal_file.exists():
                raise LoopGoalError(f"goal_id '{goal_id}' 已存在")

            now = datetime.now().isoformat()
            goal = Goal(
                goal_id=goal_id,
                description=description,
                success_criteria=criteria or [],
                status=GoalStatus.ACTIVE,
                iterations=[],
                max_iterations=max_iterations,
                convergence_window=convergence_window,
                created_at=now,
                updated_at=now,
                created_by=created_by,
                task_template=task_template,
            )
            self._save_goal_atomic(goal)
            logger.info(
                f"创建目标：{goal_id} (criteria={len(goal.success_criteria)}, "
                f"max_iterations={max_iterations})"
            )
            return goal

    def get_goal(self, goal_id: str) -> Optional[Goal]:
        """
        读取目标

        Args:
            goal_id: 目标 ID

        Returns:
            Goal 实例；不存在时返回 None
        """
        with self._lock:
            goal_file = self._get_goal_file(goal_id)
            if not goal_file.exists():
                return None
            try:
                with open(goal_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return Goal.from_dict(data)
            except (json.JSONDecodeError, OSError) as e:
                raise GoalRegistryError(
                    f"读取目标文件失败 {goal_file}：{e}"
                ) from e

    def get_goal_or_raise(self, goal_id: str) -> Goal:
        """
        读取目标（不存在时抛异常）

        Args:
            goal_id: 目标 ID

        Returns:
            Goal 实例

        Raises:
            GoalNotFoundError: 目标不存在
        """
        goal = self.get_goal(goal_id)
        if goal is None:
            raise GoalNotFoundError(f"目标 '{goal_id}' 不存在")
        return goal

    def update_goal(self, goal: Goal) -> None:
        """
        更新目标（写回磁盘）

        Args:
            goal: Goal 实例
        """
        if not isinstance(goal, Goal):
            raise LoopGoalError(f"goal 必须是 Goal：{type(goal).__name__}")
        with self._lock:
            self._save_goal_atomic(goal)
            logger.debug(f"更新目标：{goal.goal_id} (status={goal.status.value})")

    def save_iteration(self, goal_id: str, iteration: IterationResult) -> Goal:
        """
        保存一次 iteration（原子更新 + 进程间排他锁 + 状态合并）

        Phase 11 P1-4 修复：返回更新后的 Goal 引用，避免调用方再次 IO。

        Phase 11 P0-2 修复：
        1. fcntl.flock 跨进程排他锁（Linux/macOS）
        2. 写入前从磁盘读取最新版本，与本地 goal 合并 iteration（read-modify-write 冲突解决）
        3. 父目录 fsync（POSIX 要求：rename 后必须 fsync 父目录才能保证元数据持久）

        Phase 11 P1-5 修复：先把 new_iteration 加入 goal.iterations，
        再与磁盘版本合并（避免 P0-2 修复中"new_iteration 丢失"缺陷）。

        Args:
            goal_id: 目标 ID
            iteration: 迭代结果

        Returns:
            更新后的 Goal 实例（包含本次 iteration + 远端合并的 iteration）
        """
        if not isinstance(iteration, IterationResult):
            raise LoopGoalError(
                f"iteration 必须是 IterationResult：{type(iteration).__name__}"
            )
        with self._lock:
            # 读最新 → 本地追加 new_iteration → 远端合并 → 写回
            goal = self.get_goal_or_raise(goal_id)
            # P1-5 修复：先在内存中加入 new_iteration（避免在 _save_goal_atomic_with_lock 中丢失）
            local_iter_nos = {i.iteration_no for i in goal.iterations}
            if iteration.iteration_no not in local_iter_nos:
                goal.iterations.append(iteration)
            goal.updated_at = datetime.now().isoformat()
            goal = self._save_goal_atomic_with_lock(goal, new_iteration=iteration)
            logger.debug(
                f"保存 iteration：{goal_id}#{iteration.iteration_no} "
                f"(success={iteration.success})"
            )
            return goal

    def _save_goal_atomic(self, goal: Goal) -> None:
        """
        原子写：先写临时文件，再 os.replace + 父目录 fsync

        Phase 11 P0-2 修复：增加父目录 fsync，确保 os.replace 的目录项变更也落盘。
        """
        self._save_goal_atomic_with_lock(goal, new_iteration=None)

    def _save_goal_atomic_with_lock(
        self, goal: Goal, new_iteration: Optional[IterationResult]
    ) -> Goal:
        """
        原子写 + 跨进程排他锁 + 状态合并（Phase 11 P0-2 修复核心方法）

        流程：
        1. fcntl.flock 获取排他锁（阻塞等待其他进程释放）
        2. 从磁盘读取最新 goal 版本（read-modify-write 起点）
        3. 合并 iteration：本地 + 远端，去重 + 排序
        4. 写临时文件 → fsync → os.replace
        5. 父目录 fsync（POSIX 要求）
        6. fcntl.flock 释放

        Phase 12 修复（Issue 4 + 6 + 7）：
        - **入参隔离（Issue 4）**：使用 `deepcopy(goal)` 避免对入参 goal 对象的隐式状态修改。
          现在函数返回的是合并后**新对象**（除非未发生合并），调用方的入参不被污染。
        - **状态合并语义显式化（Issue 6）**（"先写者赢"）：
          - 远端终态（ACHIEVED/FAILED/ABANDONED）+ 本地非终态 → **覆盖本地**（保护远端判定）
          - 远端非终态 + 本地终态 → **不覆盖远端**（不冲突；本地胜出写入）
          - 远端非终态 + 本地非终态 → **不冲突**；merged_iterations 合并
          - 远端终态 + 本地终态 → **不冲突**；以本地为准
          - updated_at 取较新者

        P2 优化：no_merge 隐式行为风险 —— Docstring 明确化
        --------------------------------------------------------------
        本方法在两条执行路径上对入参 `goal` 的处理方式**不同**，且返回值
        与入参的引用关系**取决于 need_merge 路径分支**。为避免调用方误用，
        此处显式声明两种路径的语义契约：

        ┌────────────────────────────────────────────────────────────────┐
        │  路径 A：need_merge = False（无远端新 iteration）              │
        ├────────────────────────────────────────────────────────────────┤
        │  · 触发条件：磁盘无文件 / 磁盘 goal_id 不匹配 / 磁盘版本未包含  │
        │    本地未持有的 iteration / new_iteration 参数为 None          │
        │  · 入参 goal 行为：**完全不被修改**（无 deepcopy 开销）         │
        │  · 返回值：与入参**同一对象引用**（returned_goal is goal）     │
        │  · 性能特征：跳过 deepcopy 节省内存（多数情况下均为此路径）     │
        │                                                                │
        │  注意：调用方因此可以安全地：                                  │
        │    1) 用 `is` 运算符判断是否发生合并（False 即无合并）         │
        │    2) 在调用后再读取入参 goal 字段（保证是入参的原始值）         │
        └────────────────────────────────────────────────────────────────┘

        ┌────────────────────────────────────────────────────────────────┐
        │  路径 B：need_merge = True（远端有新的 iteration）             │
        ├────────────────────────────────────────────────────────────────┤
        │  · 触发条件：磁盘 goal 包含本地未持有的 iteration_no            │
        │  · 入参 goal 行为：**完全不被修改**（先 deepcopy 再合并）      │
        │  · 返回值：与入参**不同对象**（returned_goal is not goal）     │
        │  · 性能特征：产生一次 deepcopy 开销（仅在合并场景下）           │
        │  · 合并操作在新对象上执行：                                     │
        │    - iterations：本地 + 远端去重并按 iteration_no 排序         │
        │    - updated_at：取较新者                                       │
        │    - status：远端终态保护（不覆盖本地终态）                     │
        └────────────────────────────────────────────────────────────────┘

        ⚠️ 调用方契约（Caller's Contract）：
           1. 不要假设返回值与入参是同一对象；如需引用比较请用 `is` 区分
           2. 不要假设入参会被修改（两条路径都不修改入参；这是设计契约）
           3. 如需合并后的引用，应**始终使用返回值**，不要继续使用入参
           4. 旧调用方假定"返回值==入参"会因路径 B 触发而错乱

        Args:
            goal: 当前内存中的 Goal 实例（**永远不会被修改**——Phase 12 修复）
                  行为契约：函数内部对 goal 的任何写操作都发生在 deepcopy 后的
                  副本上，原始入参对象引用保持不变。
            new_iteration: 本次新增的 iteration（None 表示仅写 goal 状态）

        Returns:
            合并 + 写入后的 Goal 实例。

            返回值引用语义（**关键**）：
            - 路径 A (need_merge=False)：返回**入参同一对象**（is 判定为 True）
            - 路径 B (need_merge=True) ：返回**新对象**（is 判定为 False）
        """
        # Phase 12 修复（Issue 4）：deepcopy 入参避免隐式状态修改
        # 注意：仅在确实需要修改时使用 deepcopy；无远端冲突时仍可使用原对象以节省内存
        # 实现策略：先读磁盘；如有冲突再 deepcopy；无冲突则用入参（性能优化）
        goal_file = self._get_goal_file(goal.goal_id)
        goal_dir = goal_file.parent
        goal_dir.mkdir(parents=True, exist_ok=True)

        # 临时文件（同目录，确保 os.replace 原子）
        # 命名：.{filename}.tmp.{pid}.{ts_ms}.{rand4} —— 跨进程防冲突
        tmp_file = goal_dir / (
            f".{GOAL_FILENAME}.tmp.{os.getpid()}.{int(time.time() * 1000)}"
            f".{uuid.uuid4().hex[:4]}"
        )
        # 进程间排他锁文件
        lock_file = goal_dir / f".{GOAL_FILENAME}.lock"
        lock_fd: Optional[int] = None
        try:
            # 1. 跨进程排他锁（仅在 fcntl 可用时启用）
            if FCNTL_AVAILABLE:
                lock_fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o644)
                fcntl.flock(lock_fd, fcntl.LOCK_EX)  # 阻塞等待

            # 2. 读取最新版本（处理 read-modify-write 冲突）
            # Phase 12 优化（P1）：disk_goal 变量在锁内单次读取，避免重复 IO
            # 之前实现：第一个 try 读取一次判断 need_merge；第二个 try 又读取一次
            # 做合并 —— 多余磁盘 IO。现统一为单次读取 + 复用 disk_goal。
            disk_goal: Optional[Goal] = None
            need_merge = False
            if goal_file.exists():
                try:
                    with open(goal_file, "r", encoding="utf-8") as f:
                        disk_data = json.load(f)
                    disk_goal = Goal.from_dict(disk_data)
                    # 3. 状态合并：保留远端更新的 iteration
                    if new_iteration is not None and disk_goal.goal_id == goal.goal_id:
                        local_iter_nos = {i.iteration_no for i in goal.iterations}
                        remote_new_iters = [
                            it for it in disk_goal.iterations
                            if it.iteration_no not in local_iter_nos
                        ]
                        if remote_new_iters:
                            need_merge = True
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(
                        f"读取远端目标失败，使用内存版本：{e}"
                    )
                    disk_goal = None  # 显式置空，避免后续误用

            # Phase 12 修复（Issue 4）：仅在需要合并远端新 iteration 时才对入参 deepcopy
            # 其他情况下（无远端新内容 / 写入失败重试等）保持原对象引用不变
            if need_merge:
                goal = deepcopy(goal)

            # 3+. 状态合并：仅在 need_merge=True 时执行（性能优化）
            # Phase 12 优化（P1）：复用上面已读取的 disk_goal，不再重复 IO
            if need_merge and disk_goal is not None:
                # disk_goal 已在上面读取并赋值；这里直接复用
                if disk_goal.goal_id == goal.goal_id:
                    local_iter_nos = {i.iteration_no for i in goal.iterations}
                    merged_iterations = list(goal.iterations)
                    for it in disk_goal.iterations:
                        if it.iteration_no not in local_iter_nos:
                            merged_iterations.append(it)
                    merged_iterations.sort(key=lambda x: x.iteration_no)
                    goal.iterations = merged_iterations
                    # updated_at 取较新
                    if disk_goal.updated_at > goal.updated_at:
                        goal.updated_at = disk_goal.updated_at
                    # 状态机保护：如果远端已经 ACHIEVED/FAILED/ABANDONED，
                    # 本地不能轻易回退（避免覆盖远端的判定结果）
                    terminal_states = {
                        GoalStatus.ACHIEVED, GoalStatus.ABANDONED, GoalStatus.FAILED
                    }
                    if (disk_goal.status in terminal_states
                            and goal.status not in terminal_states):
                        logger.warning(
                            f"目标 {goal.goal_id} 远端已是终态 {disk_goal.status.value}，"
                            f"本地状态 {goal.status.value} 被覆盖（避免覆盖远端判定）"
                        )
                        goal.status = disk_goal.status

            # 4. 写临时文件
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(goal.to_dict(), f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())  # 文件内容已落盘

            # 5. 原子替换
            os.replace(tmp_file, goal_file)

            # 6. 父目录 fsync（POSIX 要求：rename 后必须 fsync 父目录）
            # macOS/Linux 通过打开目录 fd 实现
            try:
                dir_fd = os.open(str(goal_dir), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except (OSError, AttributeError) as e:
                # Windows 不支持目录 fsync；其他平台异常时仅记录
                logger.debug(f"父目录 fsync 跳过（{e}）")

        except Exception as e:
            # 清理临时文件
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except OSError:
                    pass
            raise GoalRegistryError(f"保存目标失败 {goal_file}：{e}") from e
        finally:
            # 释放排他锁
            if FCNTL_AVAILABLE and lock_fd is not None:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                try:
                    os.close(lock_fd)
                except OSError:
                    pass
            # Phase 12 优化（P2）：清理 lock_file 避免长期残留
            # 之前实现：lock_file 创建后从不 unlink，导致 .trae/goals/<id>/.goal.json.lock
            #   长期残留（即使锁已释放）；开发者调试时可能误读 lock 文件存在 = 锁定状态。
            # 新实现：在 finally 中 unlink(missing_ok=True) 清理。
            # 注意：必须在 fcntl.flock 释放锁之后才能 unlink（否则其他进程无法加锁）。
            # 锁释放（fcntl.flock LOCK_UN）会在 close(lock_fd) 时自动释放（POSIX 语义），
            #   但为保险起见，已先 flock(LOCK_UN) 显式释放。
            try:
                lock_file.unlink(missing_ok=True)
            except OSError:
                # unlink 失败不应中断流程（锁已释放）
                pass

        return goal

    def list_goals(
        self,
        status: Optional[GoalStatus] = None,
        statuses: Optional[List[GoalStatus]] = None,
        parent_goal_id: Optional[str] = None,
        include_root_only: bool = False,
    ) -> List[Goal]:
        """
        列出所有目标（多条件过滤，Phase 13.1 N1 修复）。

        Args:
            status: 单状态过滤（Phase 11/12 旧 API；向后兼容保留）
            statuses: 多状态过滤（Phase 13.1 新增；优先级高于 status）
            parent_goal_id: 按父 Goal ID 过滤（Phase 13.1 新增；None 表示不过滤）
            include_root_only: 是否仅返回 root goal（parent_goal_id 为 None 的；Phase 13.1 新增）

        Returns:
            Goal 列表（按 created_at 倒序）
        """
        # N1 修复：合并 status 与 statuses，优先级：statuses > status > 无过滤
        effective_statuses: Optional[List[GoalStatus]] = None
        if statuses is not None:
            effective_statuses = statuses
        elif status is not None:
            effective_statuses = [status]

        goals: List[Goal] = []
        with self._lock:
            if not self._storage_root.exists():
                return goals
            for entry in sorted(self._storage_root.iterdir(), reverse=True):
                if not entry.is_dir():
                    continue
                goal_file = entry / GOAL_FILENAME
                if not goal_file.exists():
                    continue
                try:
                    with open(goal_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    goal = Goal.from_dict(data)
                    # 多状态过滤
                    if effective_statuses is not None and goal.status not in effective_statuses:
                        continue
                    # 仅 root goal 过滤
                    if include_root_only and goal.parent_goal_id is not None:
                        continue
                    # 父 Goal ID 过滤
                    if parent_goal_id is not None and goal.parent_goal_id != parent_goal_id:
                        continue
                    goals.append(goal)
                except (json.JSONDecodeError, OSError, KeyError) as e:
                    logger.warning(f"读取目标 {entry.name} 失败：{e}")
                    continue
        return goals

    def list_children(self, parent_goal_id: str) -> List[str]:
        """
        列出指定父 Goal 的所有子 Goal ID（Phase 13.1 A1 修复）。

        Args:
            parent_goal_id: 父 Goal ID

        Returns:
            子 Goal ID 列表（不递归；不保证顺序）
        """
        children: List[str] = []
        with self._lock:
            if not self._storage_root.exists():
                return children
            for entry in self._storage_root.iterdir():
                if not entry.is_dir():
                    continue
                goal_file = entry / GOAL_FILENAME
                if not goal_file.exists():
                    continue
                try:
                    with open(goal_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if data.get("parent_goal_id") == parent_goal_id:
                        children.append(data["goal_id"])
                except (json.JSONDecodeError, OSError, KeyError) as e:
                    logger.warning(f"读取子目标 {entry.name} 失败：{e}")
                    continue
        return children

    def get_goal_status(self, goal_id: str) -> Optional[GoalStatus]:
        """
        快速获取 Goal 状态（Phase 13.1 A1 修复）。

        Args:
            goal_id: Goal ID

        Returns:
            GoalStatus 枚举；Goal 不存在返回 None
        """
        try:
            goal = self.get_goal_or_raise(goal_id)
            return goal.status
        except (GoalRegistryError, GoalNotFoundError, LoopGoalError):
            # Goal 不存在 → 返回 None（不抛错）
            return None

    def delete_goal(self, goal_id: str) -> bool:
        """
        删除目标

        Args:
            goal_id: 目标 ID

        Returns:
            True 表示删除成功；False 表示目标不存在
        """
        with self._lock:
            goal_dir = self._get_goal_dir(goal_id)
            if not goal_dir.exists():
                return False
            # 递归删除（shutil 已提升至文件顶部 import，Phase 12 Issue 2 修复）
            shutil.rmtree(goal_dir)
            logger.info(f"删除目标：{goal_id}")
            return True

    def count(self, status: Optional[GoalStatus] = None) -> int:
        """
        统计目标数量

        Args:
            status: 可选状态过滤

        Returns:
            目标数量
        """
        return len(self.list_goals(status=status))

    def __len__(self) -> int:
        """支持 len(registry)"""
        return self.count()


# ============================================================================
# Loop Goal Executor（主调度器）
# ============================================================================

class LoopGoalExecutor:
    """
    /loop + /goal 执行器

    串联 GoalRegistry、ConvergenceDetector、GoalVerifier，
    实现"循环 dispatch + 目标验证 + 收敛退出"的端到端流程。

    使用方式：
    ```python
    registry = GoalRegistry(storage_root=".trae/goals")
    executor = LoopGoalExecutor(registry=registry)

    goal = registry.create_goal(
        description="修复所有测试",
        criteria=["tests pass", "no warnings"],
        goal_id="fix-tests",
        max_iterations=5,
    )

    result = executor.execute_with_loop_goal(
        task="运行 pytest 并修复失败用例",
        agent_type="solo-coder",
        dispatch_fn=my_dispatch_fn,
        project_root=".",
        loop_config=LoopConfig(max_iterations=5),
        goal=goal,
    )
    print(result["status"])  # "achieved" / "converged" / "max_iterations_reached" / "failed"
    ```
    """

    def __init__(
        self,
        registry: Optional[GoalRegistry] = None,
        convergence_detector: Optional[ConvergenceDetector] = None,
        verifier: Optional[GoalVerifier] = None,
        karpathy_enforcer: Optional[Any] = None,
    ):
        """
        初始化执行器

        Args:
            registry: 目标注册表（None → 使用默认 .trae/goals）
            convergence_detector: 收敛检测器（None → 使用默认 window=3）
            verifier: 目标验证器（None → 使用默认关键词规则）
            karpathy_enforcer: 可选 Karpathy 联动（缺则仅记录日志）
        """
        self._registry = registry or GoalRegistry()
        self._verifier = verifier or GoalVerifier()
        self._karpathy_enforcer = karpathy_enforcer
        # 默认 convergence_detector 在 execute 时根据 goal 创建
        self._default_convergence_detector = convergence_detector

        logger.info(
            f"LoopGoalExecutor 初始化完成：registry={self._registry is not None}, "
            f"verifier={self._verifier is not None}, "
            f"karpathy={'enabled' if karpathy_enforcer else 'disabled'}"
        )

    @property
    def registry(self) -> GoalRegistry:
        """返回目标注册表"""
        return self._registry

    @property
    def verifier(self) -> GoalVerifier:
        """返回验证器"""
        return self._verifier

    # ============================================================================
    # 类型别名（Phase 12 修复 Issue 7：收紧 dispatch_fn 类型签名）
    # ============================================================================
    # DispatchFn 返回值可以是：
    # - bool: 旧式 API（向后兼容）→ True 表示成功，False 表示失败
    # - Dict[str, Any]: 新式 API（推荐）→ 至少包含 "success" 键，可选 "outputs" 键
    #                   （outputs 是 Dict[str, Any]，将合并到 iteration.outputs）
    DispatchFnReturn = Union[bool, Dict[str, Any]]  # type: ignore[misc]

    def execute_with_loop_goal(
        self,
        task: str,
        agent_type: str,
        dispatch_fn: Callable[..., DispatchFnReturn],
        project_root: str = ".",
        loop_config: Optional[LoopConfig] = None,
        goal: Optional[Goal] = None,
    ) -> Dict[str, Any]:
        """
        执行 /loop + /goal 流程

        Phase 12 修复（Issue 3 + 7）：
        - dispatch_fn 现在支持返回 Dict[str, Any]（含 success + outputs）；
          返回 bool 仍兼容（向后兼容）。
        - outputs 字典会被合并到 iteration.outputs，使 GoalVerifier 能读到
          真实的执行结果（files_modified / tests_failed / warnings_count 等）。
        - 类型签名收紧为 DispatchFnReturn（Union[bool, Dict[str, Any]]）。

        Args:
            task: 任务描述（每次 iteration 都使用）
            agent_type: 智能体类型
            dispatch_fn: 实际 dispatch 函数
                返回值语义：
                - bool: True=成功 / False=失败（向后兼容）
                - dict: 至少含 "success" 键（bool）；可选 "outputs" 键（Dict）将
                  合并到 iteration.outputs；可选 "error" 键（str）将写入
                  iteration.error
            project_root: 项目根目录
            loop_config: 循环配置（None → 不循环 max=1）
            goal: 目标（None → 仅 loop 无 goal）

        Returns:
            包含 status / iterations / total_iterations / achieved_at 等字段
        """
        if not callable(dispatch_fn):
            raise LoopGoalError("dispatch_fn 必须是 callable")
        if loop_config is None:
            loop_config = LoopConfig(max_iterations=1)
        if not isinstance(loop_config, LoopConfig):
            raise LoopGoalError(
                f"loop_config 必须是 LoopConfig：{type(loop_config).__name__}"
            )

        # 创建收敛检测器（按 goal.convergence_window 或 loop_config.convergence_window）
        window = goal.convergence_window if goal else loop_config.convergence_window
        detector = self._default_convergence_detector or ConvergenceDetector(window=window)

        # 初始化目标状态
        if goal is not None:
            # P1-3 修复：支持 ACTIVE 和 FAILED 状态启动
            # 原逻辑：仅 ACTIVE → IN_PROGRESS
            # 现逻辑：ACTIVE / FAILED → IN_PROGRESS（FAILED → IN_PROGRESS 允许重启）
            if goal.status in (GoalStatus.ACTIVE, GoalStatus.FAILED):
                try:
                    goal.transition_to(GoalStatus.IN_PROGRESS)
                except GoalStatusTransitionError as e:
                    logger.warning(f"目标状态转换失败（忽略）：{e}")
            self._registry.update_goal(goal)
            # Karpathy 联动：cp_goal_1（目标定义）
            self._verify_karpathy_checkpoint("cp_goal_1", True, f"goal {goal.goal_id} defined")

        total_iterations = 0
        converged_early = False
        success_early = False
        last_error: Optional[str] = None

        # 主循环
        for iteration_no in range(1, loop_config.max_iterations + 1):
            logger.info(
                f"[{goal.goal_id if goal else 'no-goal'}] "
                f"开始 iteration {iteration_no}/{loop_config.max_iterations}"
            )

            # 间隔（可选）
            if loop_config.inter_iteration_delay_seconds > 0 and iteration_no > 1:
                time.sleep(loop_config.inter_iteration_delay_seconds)

            # 构造 IterationResult
            iter_started = datetime.now().isoformat()
            iter_start_time = time.time()
            iteration = IterationResult(
                iteration_no=iteration_no,
                success=False,
                outputs={},
                started_at=iter_started,
                finished_at="",
                execution_time_seconds=0.0,
            )

            # 实际执行 dispatch
            try:
                raw_result = dispatch_fn(
                    agent_type=agent_type,
                    task=task,
                    task_id=f"{goal.goal_id}-iter-{iteration_no}" if goal else None,
                    project_root=project_root,
                    progress={},
                )
                # Phase 12 修复（Issue 3）：规范化 dispatch_fn 返回值
                # 旧式 bool → 新式 dict
                # 约定：
                #   bool: True=成功 / False=失败
                #   dict: {"success": bool, "outputs": {...}, "error": str?}
                success, returned_outputs, returned_error = self._normalize_dispatch_result(
                    raw_result
                )
                iteration.success = bool(success)
                # 合并 dispatch_fn 返回的 outputs（覆盖默认空 outputs）
                if returned_outputs:
                    # 注意：returned_outputs 优先级 > 默认空字典
                    iteration.outputs = dict(returned_outputs)
                if not iteration.success:
                    err_msg = returned_error or "dispatch_fn 返回 False"
                    iteration.error = err_msg
                    last_error = err_msg
            except Exception as e:
                iteration.success = False
                iteration.error = f"{type(e).__name__}: {e}"
                last_error = iteration.error
                logger.error(f"iteration {iteration_no} 异常：{e}")

            # 兜底：如果 dispatch_fn 未提供 outputs，使用默认值
            # （保留必要的字段，便于 ConvergenceDetector 收敛检测 + GoalVerifier 关键词匹配）
            default_outputs = {
                "files_modified": 0,
                "tests_passed": 0,
                "tests_failed": 0,
                "warnings_count": 0,
                "errors_count": 0,
            }
            for k, v in default_outputs.items():
                iteration.outputs.setdefault(k, v)

            iteration.execution_time_seconds = time.time() - iter_start_time
            iteration.finished_at = datetime.now().isoformat()
            total_iterations = iteration_no

            # 保存 iteration（Phase 12 修复 Issue 5）
            # 原实现：save_iteration 后再 get_goal_or_raise 重读磁盘（多余 IO）
            # 新实现：直接使用 save_iteration 返回的最新 Goal 引用（已含本次 iteration + 远端合并）
            # 注意：save_iteration 内部使用 fcntl 跨进程锁 + deepcopy，
            # 返回的新对象与入参 goal 引用隔离（Issue 4 已修复）。
            if goal is not None:
                goal = self._registry.save_iteration(goal.goal_id, iteration)

            # 收敛检测
            if goal is not None and detector.is_converged(goal.iterations):
                logger.info(
                    f"目标 {goal.goal_id} 在 iteration {iteration_no} 检测到收敛，提前退出"
                )
                converged_early = True
                break

            # 成功检测（仅 goal 有 criterion + stop_on_success=True 时）
            if (
                goal is not None
                and goal.success_criteria  # 仅当存在 criterion 时才检查成功
                and loop_config.stop_on_success
                and self._verifier is not None
            ):
                all_met, met_list = self._verifier.check_all_criteria(goal, iteration)
                iteration.criteria_met = met_list
                # P1-2 修复：成功判定前先把 criteria_met 持久化
                # 注意：save_iteration 已将 iteration 写入磁盘（但 criteria_met=[]），
                # 此处需要更新 goal.iterations[-1].criteria_met 并重新写盘
                if goal is not None and met_list:
                    # 找到对应的 iteration 引用并更新 criteria_met
                    for iter_in_goal in goal.iterations:
                        if iter_in_goal.iteration_no == iteration.iteration_no:
                            iter_in_goal.criteria_met = met_list
                            break
                    self._registry.update_goal(goal)
                if all_met:
                    logger.info(
                        f"目标 {goal.goal_id} 全部 criterion 在 iteration {iteration_no} 满足"
                    )
                    success_early = True
                    try:
                        goal.transition_to(GoalStatus.ACHIEVED)
                    except GoalStatusTransitionError as e:
                        logger.warning(f"目标状态转换 ACHIEVED 失败：{e}")
                    self._registry.update_goal(goal)
                    # Karpathy 联动：cp_goal_2（验证完成）
                    self._verify_karpathy_checkpoint(
                        "cp_goal_2", True,
                        f"goal {goal.goal_id} achieved at iteration {iteration_no}"
                    )
                    break

        # 循环结束：决定最终状态
        if goal is not None:
            if not success_early and not converged_early:
                # 用尽 max_iterations 但未达成
                if goal.success_criteria:
                    # 有 criterion 但未满足 → FAILED
                    try:
                        goal.transition_to(GoalStatus.FAILED)
                    except GoalStatusTransitionError as e:
                        logger.warning(f"目标状态转换 FAILED 失败：{e}")
                else:
                    # 无 criterion → 视为 IN_PROGRESS（可后续重试）
                    pass
                self._registry.update_goal(goal)

        # 返回汇总
        result: Dict[str, Any] = {
            "total_iterations": total_iterations,
            "converged_early": converged_early,
            "success_early": success_early,
            "max_iterations_reached": total_iterations >= loop_config.max_iterations,
            "agent_type": agent_type,
            "task": task,
            "project_root": project_root,
        }
        if goal is not None:
            result["goal_id"] = goal.goal_id
            result["status"] = goal.status.value
            result["achieved_at"] = goal.achieved_at
            result["iterations"] = [i.to_dict() for i in goal.iterations]
            result["convergence_info"] = detector.get_convergence_info(goal.iterations)
            # P1-1 修复：暴露 has_criteria 给 CLI 判定层（避免 _is_overall_success 无法区分）
            result["has_criteria"] = bool(goal.success_criteria)
        if last_error and not goal:
            result["last_error"] = last_error
        return result

    @staticmethod
    def _normalize_dispatch_result(
        raw_result: Any,
    ) -> Tuple[bool, Dict[str, Any], Optional[str]]:
        """
        规范化 dispatch_fn 返回值（Phase 12 修复 Issue 3）

        支持 3 种返回类型：
        1. bool: 旧式 API（向后兼容）→ (bool, {}, None)
        2. dict: 新式 API（推荐）→ 从中提取 success / outputs / error
        3. None: 视为失败（保守策略，避免误判成功）→ (False, {}, "dispatch_fn 返回 None")

        异常处理：
        - dict 缺少 "success" 键 → 视为失败
        - dict 的 "success" 非 bool → 强制转 bool
        - dict 的 "outputs" 非 dict → 忽略（视为空）
        - dict 的 "error" 非 str → 忽略

        Args:
            raw_result: dispatch_fn 的原始返回值

        Returns:
            (success, outputs, error) 三元组
            - success: 是否成功
            - outputs: 产出字典（可能为空）
            - error: 错误信息（成功时为 None）
        """
        # 类型 1：bool
        if isinstance(raw_result, bool):
            return raw_result, {}, None
        # 类型 2：dict
        if isinstance(raw_result, dict):
            success_value = raw_result.get("success", False)
            success_bool = bool(success_value) if success_value is not None else False
            outputs_value = raw_result.get("outputs", {})
            outputs_dict: Dict[str, Any] = (
                dict(outputs_value) if isinstance(outputs_value, dict) else {}
            )
            error_value = raw_result.get("error")
            error_str: Optional[str] = (
                str(error_value) if isinstance(error_value, str) else None
            )
            return success_bool, outputs_dict, error_str
        # 类型 3：None 或其它类型
        if raw_result is None:
            return False, {}, "dispatch_fn 返回 None"
        # 未知类型：视为失败并记录
        logger.warning(
            f"dispatch_fn 返回未知类型 {type(raw_result).__name__}，视为失败"
        )
        return False, {}, f"dispatch_fn 返回未知类型：{type(raw_result).__name__}"

    def _verify_karpathy_checkpoint(
        self, checkpoint_id: str, verified: bool, notes: str = ""
    ) -> None:
        """
        验证 Karpathy 检查点（缺 enforcer 时仅记录日志）

        Args:
            checkpoint_id: 检查点 ID
            verified: 是否通过
            notes: 备注
        """
        if self._karpathy_enforcer is None:
            logger.debug(
                f"[Karpathy 联动未启用] checkpoint={checkpoint_id}, "
                f"verified={verified}, notes={notes}"
            )
            return
        try:
            self._karpathy_enforcer.verify_checkpoint(
                checkpoint_id,
                verified=verified,
                verified_by="loop_goal_executor",
                notes=notes,
            )
        except Exception as e:
            logger.warning(
                f"Karpathy 检查点 {checkpoint_id} 验证异常：{e}"
            )


# ============================================================================
# 便捷函数
# ============================================================================

def create_default_executor(project_root: str = ".") -> LoopGoalExecutor:
    """
    创建默认配置的 LoopGoalExecutor

    Args:
        project_root: 项目根目录

    Returns:
        LoopGoalExecutor 实例
    """
    storage_root = os.path.join(project_root, ".trae", "goals")
    registry = GoalRegistry(storage_root=storage_root)

    # 尝试加载 Karpathy enforcer（可选）
    karpathy_enforcer = None
    try:
        from karpathy_principle_enforcer import KarpathyPrincipleEnforcer
        karpathy_enforcer = KarpathyPrincipleEnforcer(project_root)
    except ImportError:
        logger.debug("KarpathyPrincipleEnforcer 未加载，跳过联动")

    return LoopGoalExecutor(
        registry=registry,
        karpathy_enforcer=karpathy_enforcer,
    )


# ============================================================================
# 模块导出
# ============================================================================

__all__ = [
    # 异常
    "LoopGoalError",
    "InvalidGoalIdError",
    "InvalidLoopConfigError",
    "GoalNotFoundError",
    "GoalRegistryError",
    "GoalStatusTransitionError",
    # 枚举
    "GoalStatus",
    "ALLOWED_STATUS_TRANSITIONS",
    # 数据类
    "Goal",
    "LoopConfig",
    "IterationResult",
    # 核心类
    "ConvergenceDetector",
    "GoalVerifier",
    "GoalRegistry",
    "LoopGoalExecutor",
    # 便捷函数
    "create_default_executor",
    # 常量
    "MAX_ITERATIONS_LIMIT",
    "MIN_ITERATIONS",
    "DEFAULT_CONVERGENCE_WINDOW",
    "GOAL_ID_PATTERN",
]
