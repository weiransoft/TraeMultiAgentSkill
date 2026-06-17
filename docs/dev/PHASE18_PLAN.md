# Phase 18 设计文档：Ralph 风格 Autonomous 自主迭代执行器

> **文档类型**：技术方案 spec（v1 — 初版设计）
> **日期**：2026-06-07
> **状态**：⏳ v1 设计稿，待架构师复核 + 用户批准
> **前序**：[PHASE17_PLAN.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE17_PLAN.md)（V3 插件热加载）
> **方向**：在 V3 插件架构之上叠加 Ralph 风格 autonomous 自主迭代执行器
> **调研对象**：[gnhf (good night, have fun)](https://github.com/kunchenguid/gnhf) — Ralph/autoresearch 风格 orchestrator
> **核心价值**：让多角色团队在用户睡眠时自动完成全部任务（自动运行、自动确认、自动使用 skill、自动测试、自动提交）
> **实现范围**：完整实现 5/6 核心能力（Git 驱动 / Sleep 防护 / notes.md / 智能确认 / Auto-skill）；不实现 worktree 并行模式

---

## 0. 变更履历

### 0.1 v1 初版（待评审）

| # | 项 | 状态 | 说明 |
|---|----|------|------|
| 0.1.1 | 8 个核心组件接口设计 | ✅ | 详见 §3 |
| 0.1.2 | CLI 集成 flag 列表 | ✅ | 详见 §4 |
| 0.1.3 | config.yml schema | ✅ | 详见 §5 |
| 0.1.4 | 错误处理矩阵 | ✅ | 详见 §7 |
| 0.1.5 | 测试策略 | ✅ | 详见 §8 |
| 0.1.6 | 迁移路径（5 阶段） | ✅ | 详见 §9 |
| 0.1.7 | 风险评估 | ✅ | 详见 §10 |

---

## 1. 背景与目标

### 1.1 用户痛点

当前 trae-multi-agent 调度是 **同步 + 一次性** 模式：

```bash
# 用户必须守着终端
python3 scripts/trae_agent_dispatch.py --task "实现 XX 功能" --agent solo-coder
# ❌ 任务执行中用户不能离开
# ❌ 智能体询问"是否确认" → 用户必须手动确认
# ❌ 多轮迭代时用户必须逐次触发
# ❌ 用户睡觉时任务无人值守
```

### 1.2 行业参照：Ralph / gnhf 风格

[gnhf](https://github.com/kunchenguid/gnhf)（"good night, have fun"）展示的范式：

> **睡前给 agent 一个目标，醒来看到完成的 commits**

关键设计：
- 每轮一个小改动 → 成功则 `git commit`，失败则 `git reset --hard`（但 commit 失败的 uncommitted work 保留供修复）
- 跨轮记忆：`notes.md` 累积
- 失败处理：agent 报告失败 → 立即继续；硬错误 → 指数退避；连续 3 次失败 → abort
- Runtime caps：`--max-iterations` / `--max-tokens` / `--stop-when "<自然语言停止条件>"`
- 防休眠：macOS `caffeinate -i`
- Resume：re-run 继续上次的 `.gnhf/runs/<runId>/` 历史
- 多 agent：worktree 模式（本 Phase 不实现）

### 1.3 设计目标

在 **V3 插件架构** 之上叠加 Ralph 风格 autonomous 执行器，满足以下强约束：

| 约束 | 描述 | 优先级 |
|------|------|--------|
| **不破坏 V3** | 现有 5 个内置插件 + hot reload + facade 三层结构 100% 保留 | P0 |
| **不替代 dispatcher** | autonomous 编排器 **调用** GoalDispatcher 而不是替代 | P0 |
| **不修改技术栈** | 不引入新的运行时依赖（仅允许 git / caffeinate / 标准库） | P0 |
| **不模拟/不占位** | 所有逻辑真实实现，禁 mock/简化 | P0 |
| **真实提交** | 用真实 `git` 命令，错误真实处理（不假装成功） | P0 |
| **断点续跑** | 进程崩溃后重入可继续（基于 RunState 持久化） | P0 |
| **中文注释** | 所有关键逻辑中文注释，符合 Java/Rust 规范 | P0 |

### 1.4 范围与不范围

**范围内（5 项核心能力 + 4 阶段 handler）**：

| 能力 | 描述 |
|------|------|
| ✅ Git 驱动提交/回滚 | 成功则 commit，失败则 `git reset --hard` 但保留 uncommitted work |
| ✅ Sleep 防护 | macOS `caffeinate -i` 包装器（Linux 平台 no-op 降级） |
| ✅ notes.md 跨轮记忆 | 每轮写入累积，restart 时自动加载 |
| ✅ 智能确认跳过 | 白名单/黑名单/风险评估决定是否自动确认 |
| ✅ Auto-skill 加载 | 自动检测并加载 `.trae/skills/` 和 `plugins_extra/` |
| ✅ 4 阶段 handler | plan_handler / dev_handler / verify_handler / fix_handler |
| ✅ 断点续跑 | RunState 持久化 + ResumeContext |

**范围外（明确不做）**：

| 不做项 | 理由 |
|--------|------|
| ❌ Worktree 并行模式 | 用户已选定不实现（复杂度高、风险大） |
| ❌ 多 agent 协作调度 | 保留给后续 Phase |
| ❌ 修改 V3 插件 ABC 接口 | 仅新增 plugin（不破坏兼容） |
| ❌ 替换 dispatcher | autonomous 编排器 **上层** 调用 dispatcher |

---

## 2. 架构设计

### 2.1 三层架构（与 V3 共存）

```
┌────────────────────────────────────────────────────────────────────┐
│  Layer 4 (新)：Autonomous Orchestrator  (Phase 18)                │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  RalphLoopController (主循环)                                │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │  │
│  │  │ Plan     │  │ Dev      │  │ Verify   │  │ Fix      │    │  │
│  │  │ Handler  │→ │ Handler  │→ │ Handler  │→ │ Handler  │    │  │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │  │
│  └──────────────────────────────────────────────────────────────┘  │
│       │                                                           │
│       │ 协作组件（4 个）                                          │
│       ├─ GitDriver (提交/回滚/恢复 uncommitted work)              │
│       ├─ NotesMemory (跨轮 notes.md)                              │
│       ├─ AutoSkillLoader (自动加载 .trae/skills/ + plugins_extra/)│
│       ├─ SmartConfirmation (白名单/黑名单/风险评估)              │
│       ├─ SleepGuard (caffeinate 包装器)                           │
│       └─ RunState / ResumeContext (持久化 + 断点续跑)            │
└───────────────────────────────────┬────────────────────────────────┘
                                    │ 调用 (不替代)
                                    ▼
┌────────────────────────────────────────────────────────────────────┐
│  Layer 3 (V3 现有)：Dispatcher + Plugins                          │
│  ┌──────────────────────┐  ┌────────────────────────────────────┐  │
│  │ GoalDispatcher       │  │ Plugins (5 个内置)                  │  │
│  │ (mutex / 中间件 /    │  │ - loop, multi_goal, resume,         │  │
│  │  DispatchResult)     │  │   cancel, graph                     │  │
│  └──────────────────────┘  └────────────────────────────────────┘  │
│       ▲                                                           │
│       │ 调用 (不替代)                                              │
│       │                                                           │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │ 新增 Phase 18 内置插件：RalphAutonomousPlugin (priority=5) │    │
│  │ → 作为 plugin 注册到 dispatcher，由 --autonomous flag 触发│    │
│  └────────────────────────────────────────────────────────────┘    │
└────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────┐
│  Layer 1-2 (V3 现有)：CLI + Dispatch Legacy                       │
│  cli/parser.py (新加 --autonomous 互斥组)                         │
│  dispatch/legacy.py (被 plugin 调用)                              │
│  facade.py (V3 兼容薄壳)                                          │
└────────────────────────────────────────────────────────────────────┘
```

**关键不变量**：

1. **不破坏 V3 三层结构** — `facade` / `dispatcher` / `plugin` 三层职责不变
2. **不替代 dispatcher** — Autonomous 是 **上层编排器**，每轮调一次 `dispatcher.dispatch()`
3. **不修改现有 plugin** — 仅新增 1 个 `RalphAutonomousPlugin` 作为入口

### 2.2 核心调用流（ASCII 流程图）

```
              ┌─────────────────────────────────┐
              │  CLI: --autonomous --task "..." │
              └───────────────┬─────────────────┘
                              │
                              ▼
              ┌─────────────────────────────────┐
              │  RalphAutonomousPlugin.matches │
              │  (检查 args.autonomous flag)    │
              └───────────────┬─────────────────┘
                              │ True
                              ▼
              ┌─────────────────────────────────┐
              │  plugin.execute()               │
              │  ├─ SleepGuard.acquire()        │  ← caffeinate -i
              │  ├─ RunState.load_or_init()     │  ← .gnhf/runs/<id>/
              │  └─ RalphLoopController.run()   │
              └───────────────┬─────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │         RalphLoopController.run()           │
        │  ┌─────────────────────────────────────┐    │
        │  │  while not should_stop():           │    │
        │  │   iter_result = run_one_iteration() │    │
        │  │   if iter_result.kind == SUCCESS:   │    │
        │  │     GitDriver.commit()              │    │
        │  │   elif iter_result.kind == FAILED:  │    │
        │  │     GitDriver.rollback()            │    │
        │  │   elif iter_result.kind == RETRY:   │    │
        │  │     backoff_sleep()                 │    │
        │  │   RunState.persist()                │    │
        │  └─────────────────────────────────────┘    │
        └──────────┬──────────────────────────────────┘
                   │ run_one_iteration()
                   ▼
        ┌─────────────────────────────────────────────┐
        │  4 阶段 Handler 管线（每轮只走 1 阶段）       │
        │                                              │
        │  stage = current_stage                       │
        │  handler = HANDLERS[stage]                   │
        │  return handler.handle(iter_ctx)             │
        │                                              │
        │  ┌──────────────────────────────────────┐   │
        │  │  PlanHandler:  生成 / 加载计划       │   │
        │  │   ├─ AutoSkillLoader.detect()        │   │
        │  │   ├─ NotesMemory.read()              │   │
        │  │   └─ PlanGenerator.generate()        │   │
        │  ├──────────────────────────────────────┤   │
        │  │  DevHandler:  执行开发              │   │
        │  │   ├─ SmartConfirmation.check()       │   │
        │  │   └─ DispatcherAdapter.invoke()      │   │
        │  ├──────────────────────────────────────┤   │
        │  │  VerifyHandler:  验证                │   │
        │  │   ├─ TestRunner.run()                │   │
        │  │   └─ SecurityAnalyzer.run()          │   │
        │  ├──────────────────────────────────────┤   │
        │  │  FixHandler:  修复                  │   │
        │  │   ├─ ErrorClassifier.classify()      │   │
        │  │   └─ FixStrategy.apply()             │   │
        │  └──────────────────────────────────────┘   │
        └──────────┬──────────────────────────────────┘
                   │ DispatcherAdapter.invoke() 关键调用
                   ▼
        ┌─────────────────────────────────────────────┐
        │  DispatcherAdapter (新组件，Phase 18)        │
        │  ├─ 构造 PluginContext (复用 V3)             │
        │  ├─ GoalDispatcher.dispatch() (复用 V3)      │
        │  └─ 包装为 IterationResult 返回              │
        └──────────┬──────────────────────────────────┘
                   │
                   ▼
        ┌─────────────────────────────────────────────┐
        │  GoalDispatcher.dispatch(args, ctx)         │
        │  → 命中某个 plugin（loop / solo-coder 等）   │
        │  → 真实执行智能体调用                       │
        └──────────┬──────────────────────────────────┘
                   │
                   ▼
        ┌─────────────────────────────────────────────┐
        │  退出时 SleepGuard.release()                 │  ← kill caffeinate
        │  退出时 RunState.mark_complete()              │  ← 持久化
        └─────────────────────────────────────────────┘
```

### 2.3 数据流（每轮迭代）

```
┌──────────────────────────────────────────────────────────────┐
│  迭代 N 入口                                                  │
│                                                              │
│  1. IterationContext 创建                                    │
│     ├─ run_id, iter_index, stage, current_notes             │
│     ├─ 加载 RunState (from .gnhf/runs/<id>/state.json)      │
│     └─ 加载 NotesMemory (from .gnhf/runs/<id>/notes.md)      │
│                                                              │
│  2. Handler.handle(iter_ctx)                                  │
│     ├─ 调用 AutoSkillLoader.detect() (扫描 skills/plugins)   │
│     ├─ 调用 SmartConfirmation.check() (决定是否 auto-confirm)│
│     └─ 调用 DispatcherAdapter.invoke() (真实调度)           │
│                                                              │
│  3. IterationResult 返回                                     │
│     ├─ kind: SUCCESS | FAILED | RETRIABLE | FATAL           │
│     ├─ diff_stats: lines added/removed                       │
│     ├─ test_results: pass/fail/skip counts                  │
│     └─ agent_output: 智能体原始输出 (供 notes)              │
│                                                              │
│  4. 提交/回滚                                                │
│     ├─ SUCCESS → GitDriver.commit("iter-N: <summary>")      │
│     ├─ FAILED → GitDriver.rollback() (保留 uncommitted)     │
│     └─ FATAL → 连续 3 次 → 整个 run abort                   │
│                                                              │
│  5. 状态更新                                                  │
│     ├─ NotesMemory.append(iter_summary)                     │
│     └─ RunState.persist()                                   │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. 核心组件详细设计

### 3.1 RalphLoopController（主循环）

**文件位置**：`scripts/autonomous/loop_controller.py`

**职责**：
- 编排每轮迭代：plan → dev → verify → fix 阶段机
- 与 GitDriver / NotesMemory / RunState 协作
- 强制 runtime caps（max-iterations / max-tokens / stop-when）
- 失败重试与退避（指数退避 + 连续 3 次失败 abort）

**接口设计**：

```python
from typing import Optional, List, Callable
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import time


# 阶段枚举
class StageKind(str, Enum):
    """Ralph 4 阶段。
    
    PLAN: 制定计划（生成 / 加载 plan）
    DEV: 实际开发（调用 dispatcher）
    VERIFY: 验证（跑测试 + 安全分析）
    FIX: 修复（基于 verify 错误）
    """
    PLAN = "plan"
    DEV = "dev"
    VERIFY = "verify"
    FIX = "fix"


@dataclass
class LoopConfig:
    """Ralph 循环配置。
    
    字段说明：
    - max_iterations: 硬上限，强制退出（防失控）
    - max_tokens: token 预算（估算，触发后退出）
    - stop_when: 自然语言停止条件（由 LLM 评估输出匹配）
    - stage_order: 阶段顺序（默认 PLAN→DEV→VERIFY→FIX，可配置为 DEV-only）
    - backoff_base_sec: 失败退避基数（默认 1.0）
    - backoff_max_sec: 退避上限（默认 60.0）
    - consecutive_failure_abort: 连续失败次数阈值（默认 3）
    - git_author_name: commit 作者名
    - git_author_email: commit 作者邮箱
    - test_command: 测试命令（默认 "python3 -m unittest discover -s tests -p 'test_*.py'"）
    - security_analyzer: 安全分析器名称（默认 "builtin"）
    """
    max_iterations: int = 50
    max_tokens: int = 500_000
    stop_when: str = ""
    stage_order: List[StageKind] = field(
        default_factory=lambda: [StageKind.PLAN, StageKind.DEV, StageKind.VERIFY, StageKind.FIX]
    )
    backoff_base_sec: float = 1.0
    backoff_max_sec: float = 60.0
    consecutive_failure_abort: int = 3
    git_author_name: str = "Ralph Autonomous Agent"
    git_author_email: str = "ralph@trae-multi-agent.local"
    test_command: str = "python3 -m unittest discover -s tests -p 'test_*.py'"
    security_analyzer: str = "builtin"


@dataclass
class IterationContext:
    """单次迭代上下文。
    
    字段说明：
    - run_id: 本次 Ralph run 的唯一 ID
    - iter_index: 当前迭代索引（从 1 开始）
    - stage: 当前阶段
    - current_plan: 加载 / 生成的当前 plan (markdown 字符串)
    - notes_snapshot: 当前 notes.md 完整内容
    - prev_results: 历史迭代结果列表
    - project_root: 项目根目录
    - worktree_path: 工作路径（Phase 18 默认 = project_root）
    """
    run_id: str
    iter_index: int
    stage: StageKind
    current_plan: str
    notes_snapshot: str
    prev_results: List["IterationResult"]
    project_root: Path
    worktree_path: Path


@dataclass
class IterationResult:
    """单次迭代结果（4 类判定）。
    
    字段说明：
    - kind: 4 类判定（SUCCESS/FAILED/RETRIABLE/FATAL）
    - summary: 人类可读摘要
    - agent_output: 智能体原始输出（用于写入 notes）
    - diff_stats: (lines_added, lines_removed) 来自 git diff --stat
    - test_results: (passed, failed, skipped) 测试统计
    - security_issues: 安全问题列表
    - duration_sec: 本轮耗时
    - token_used: 本轮 token 消耗估算
    - error: 异常对象（如果有）
    """
    kind: str  # "success" | "failed" | "retriable" | "fatal"
    summary: str = ""
    agent_output: str = ""
    diff_stats: tuple = (0, 0)
    test_results: tuple = (0, 0, 0)
    security_issues: List[dict] = field(default_factory=list)
    duration_sec: float = 0.0
    token_used: int = 0
    error: Optional[BaseException] = None


class RalphLoopController:
    """Ralph 风格自主迭代主循环。"""

    def __init__(
        self,
        config: LoopConfig,
        project_root: Path,
        git_driver: "GitDriver",
        notes_memory: "NotesMemory",
        auto_skill_loader: "AutoSkillLoader",
        smart_confirmation: "SmartConfirmation",
        run_state: "RunState",
        dispatcher_adapter: "DispatcherAdapter",
        stage_handlers: dict,  # Dict[StageKind, StageHandler]
        log: Callable[[str, str], None],
    ):
        """构造 RalphLoopController。
        
        Args:
            config: 循环配置（见 LoopConfig）
            project_root: 项目根目录
            git_driver: git 操作封装
            notes_memory: notes.md 读写
            auto_skill_loader: skill 自动加载
            smart_confirmation: 智能确认
            run_state: run 状态持久化
            dispatcher_adapter: dispatcher 适配器
            stage_handlers: 4 阶段 handler 字典
            log: 日志函数
        """
        ...

    def run(self) -> int:
        """主循环入口。
        
        Returns:
            int: 退出码（0 = 全部成功；1 = 部分失败；2 = fatal abort；3 = 命中 stop_when）
        
        行为：
        1. 加载 RunState（如 resume 模式）
        2. 循环 while not should_stop():
           a. iter_result = run_one_iteration()
           b. 根据 result.kind 处理（commit/rollback/retry）
           c. RunState.persist()
        3. 退出前 final commit + summary
        """
        ...

    def run_one_iteration(self) -> IterationResult:
        """执行一次完整迭代。
        
        Returns:
            IterationResult: 4 类判定之一
        
        行为：
        1. 构造 IterationContext
        2. 按 stage_order 执行 handler
        3. 任一阶段 FATAL → 立即返回
        4. 收集所有阶段结果聚合成 IterationResult
        """
        ...

    def should_stop(self) -> bool:
        """判断是否应停止。
        
        Returns:
            bool: True = 停止
        
        判定顺序（短路求值）：
        1. iter_index >= max_iterations → True
        2. cumulative_tokens >= max_tokens → True
        3. stop_when 匹配最近 N 次 agent_output → True
        4. RunState.marked_complete → True
        """
        ...

    def backoff_sleep(self, attempt: int) -> None:
        """指数退避（attempt: 0/1/2...）。
        
        公式：min(backoff_max_sec, backoff_base_sec * (2 ** attempt)) + jitter
        jitter: ± 10% 随机扰动（避免多进程同时重试）
        """
        ...


__all__ = [
    "StageKind",
    "LoopConfig",
    "IterationContext",
    "IterationResult",
    "RalphLoopController",
]
```

**关键方法实现要求**：

1. **`run()`**：
   - 必须 try/finally 包裹 SleepGuard.release()（即使异常也释放 caffeinate）
   - RunState.persist() 在每轮结束后调用（崩溃可恢复）
   - 退出前调用 `NotesMemory.write_final_summary()`

2. **`run_one_iteration()`**：
   - 真实调用 4 阶段 handler（不模拟）
   - 阶段间状态通过 IterationContext 传递
   - 任意阶段返回 FATAL → 立即返回（不继续后续阶段）

3. **`should_stop()`**：
   - 短路求值（避免不必要计算）
   - `stop_when` 评估由 LLM 完成（调用 dispatcher 调出 LLM 评估最近 5 次输出）

4. **`backoff_sleep()`**：
   - 真实使用 `time.sleep()`（不假装 sleep）
   - jitter 用 `random.uniform(0.9, 1.1)` 实现

---

### 3.2 GitDriver（Git 操作封装）

**文件位置**：`scripts/autonomous/git_driver.py`

**职责**：
- 提交 / 回滚 / 恢复 uncommitted work
- 状态探测（clean / dirty / untracked）
- diff 统计
- 真实 git 命令（不模拟）

**接口设计**：

```python
from pathlib import Path
from typing import Optional, Tuple, List
from dataclasses import dataclass
import subprocess
import shutil
import time


@dataclass
class GitOpResult:
    """git 操作结果。
    
    字段说明：
    - success: 是否成功
    - stdout: 命令 stdout
    - stderr: 命令 stderr
    - returncode: git 命令返回码
    - error_message: 失败时的可读错误（中文）
    """
    success: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    error_message: str = ""


@dataclass
class DiffStats:
    """diff 统计。
    
    字段说明：
    - files_changed: 变更文件数
    - lines_added: 新增行数
    - lines_removed: 删除行数
    - binary_files: 二进制文件数
    """
    files_changed: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    binary_files: int = 0


class GitDriver:
    """Ralph 风格 Git 操作封装。
    
    设计原则：
    1. 真实调用 git 命令（不模拟）
    2. 失败有详细错误（不假装成功）
    3. rollback 保留 uncommitted work 到 .gnhf/runs/<id>/uncommitted/
    """

    def __init__(
        self,
        repo_root: Path,
        run_id: str,
        author_name: str = "Ralph Autonomous Agent",
        author_email: str = "ralph@trae-multi-agent.local",
        run_dir: Optional[Path] = None,
        git_timeout_sec: float = 30.0,
    ):
        """构造 GitDriver。
        
        Args:
            repo_root: git 仓库根目录
            run_id: 本次 run 的 ID（用于 uncommitted work 隔离目录）
            author_name: commit 作者名
            author_email: commit 作者邮箱
            run_dir: .gnhf/runs/<run_id>/ 路径（默认 <repo_root>/.gnhf/runs/<run_id>）
            git_timeout_sec: 单个 git 命令超时
        """
        ...

    def is_git_repo(self) -> bool:
        """检测 repo_root 是否为 git 仓库。
        
        Returns:
            bool: True = 是 git 仓库
        
        实现：git rev-parse --is-inside-work-tree
        """
        ...

    def status(self) -> GitOpResult:
        """git status --porcelain。
        
        Returns:
            GitOpResult: stdout 含 porcelain 输出（每行一个文件状态）
        """
        ...

    def diff_stats(self, since_commit: Optional[str] = None) -> DiffStats:
        """获取 diff 统计。
        
        Args:
            since_commit: 起始 commit（None = 与 HEAD 相比；或 commit hash）
        
        Returns:
            DiffStats: 文件数 / 行数统计
        
        实现：
        - git diff --numstat since_commit..HEAD（统计行数）
        - git diff --name-only since_commit..HEAD | wc -l（文件数）
        - git diff --numstat --diff-filter=A since_commit..HEAD（新增文件）
        """
        ...

    def add_all(self) -> GitOpResult:
        """git add -A。
        
        Returns:
            GitOpResult: 操作结果
        """
        ...

    def commit(self, message: str) -> GitOpResult:
        """git commit -m "<message>"。
        
        Args:
            message: commit message
        
        Returns:
            GitOpResult: 操作结果（含 commit hash 在 stdout）
        
        行为：
        1. 先 git status --porcelain 检查是否有变更（无变更 → 跳过 + warning）
        2. git add -A
        3. GIT_AUTHOR_NAME / GIT_AUTHOR_EMAIL 环境变量注入作者
        4. git commit -m "<message>"
        5. 返回 commit hash
        """
        ...

    def rollback(self) -> GitOpResult:
        """回滚工作区（保留 uncommitted work）。
        
        Returns:
            GitOpResult: 操作结果
        
        行为（参照 gnhf 的关键设计）：
        1. 如果有 uncommitted 变更：
           a. 创建 .gnhf/runs/<run_id>/uncommitted/<timestamp>/ 目录
           b. 用 `git diff` 和 `git status --porcelain` 收集所有变更
           c. cp 所有 untracked/modified 文件到 uncommitted 目录
           d. git checkout -- .  撤销 tracked 变更
           e. 保留 untracked 文件（避免误删）
        2. 记录 uncommitted 清单到 .gnhf/runs/<run_id>/uncommitted/manifest.json
        3. 返回成功
        """
        ...

    def restore_uncommitted(self, manifest_path: Path) -> GitOpResult:
        """从 manifest 恢复 uncommitted work（供 fix_handler 使用）。
        
        Args:
            manifest_path: manifest.json 路径
        
        Returns:
            GitOpResult: 操作结果
        
        行为：
        1. 读取 manifest.json
        2. cp 所有文件回原位置
        3. git add -A（让 git 重新跟踪）
        4. 不自动 commit（留给下一轮迭代决定）
        """
        ...

    def log_last_n(self, n: int = 10) -> List[str]:
        """git log -n --oneline。
        
        Args:
            n: 取最近 N 条
        
        Returns:
            List[str]: commit hash + message 列表
        """
        ...


__all__ = [
    "GitOpResult",
    "DiffStats",
    "GitDriver",
]
```

**关键方法实现要求**：

1. **`is_git_repo()`**：
   - 真实执行 `git rev-parse --is-inside-work-tree`
   - 失败返回 False（不抛异常）
   - 首次执行前检测一次，结果缓存到 `self._is_repo` 避免重复 fork

2. **`commit()`**：
   - 必须先 `git status --porcelain` 检查（空 commit 不允许）
   - 作者通过 `GIT_AUTHOR_NAME` / `GIT_AUTHOR_EMAIL` / `GIT_COMMITTER_*` 环境变量注入（不修改全局 git config）
   - 失败时 stderr 必须暴露（不掩盖）

3. **`rollback()`**：
   - 关键设计：**保留 uncommitted work**（不丢失工作）
   - 用 `cp -p` 保留文件权限
   - manifest.json 记录原路径 + sha256 校验和（供恢复时校验）

4. **`restore_uncommitted()`**：
   - 校验 sha256（文件损坏 → 警告 + 跳过该文件）
   - cp 失败 → 详细错误（不静默忽略）

---

### 3.3 NotesMemory（跨轮 notes.md）

**文件位置**：`scripts/autonomous/notes_memory.py`

**职责**：
- 跨轮累积 notes.md（每轮 append 一段）
- restart 时自动加载历史 notes
- 支持 markdown 结构（标题 / 列表 / 代码块）
- 提供 LLM 友好的 token 估算

**接口设计**：

```python
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, field
from datetime import datetime
import re


@dataclass
class NotesSection:
    """单个 notes 段落。
    
    字段说明：
    - title: 段标题（如 "## Iteration 5: Fix typo in parser"）
    - body: 段内容（markdown）
    - timestamp: ISO 8601 时间戳
    - iter_index: 所属迭代索引
    - tags: 标签列表（如 ["success", "test-passed"]）
    """
    title: str
    body: str
    timestamp: str
    iter_index: int
    tags: List[str] = field(default_factory=list)


class NotesMemory:
    """跨轮 notes.md 记忆。
    
    设计原则：
    1. 文件格式：标准 markdown，LLM 可直接消费
    2. 原子写入：先写 .tmp，fsync 后 rename（避免半写）
    3. 段落式：每轮一个 section，标题含 iter_index
    4. token 估算：粗略按 char/4 估算（不依赖 tiktoken）
    """

    def __init__(
        self,
        notes_path: Path,
        max_size_kb: int = 1024,
        trim_keep_last_n: int = 20,
    ):
        """构造 NotesMemory。
        
        Args:
            notes_path: notes.md 完整路径
            max_size_kb: 最大文件大小（KB），超过则 trim
            trim_keep_last_n: trim 时保留最近 N 个段落
        """
        ...

    def load(self) -> str:
        """加载完整 notes.md。
        
        Returns:
            str: 完整 markdown 内容（文件不存在返回空字符串）
        """
        ...

    def append(self, section: NotesSection) -> None:
        """追加一个段落。
        
        Args:
            section: 段落对象
        
        行为：
        1. 序列化 section 为 markdown
        2. 检查文件大小（超 max_size_kb → trim）
        3. 原子写入（先 .tmp，再 rename）
        """
        ...

    def list_sections(self) -> List[NotesSection]:
        """解析 notes.md 为段落列表。
        
        Returns:
            List[NotesSection]: 按文件顺序排列的段落
        
        解析规则：
        - 段标题：'## ' 开头
        - 段元数据：紧随标题的注释行（'<!-- iter=N tags=... -->'）
        - 段内容：元数据之后到下一个 '## ' 之前
        """
        ...

    def get_recent_sections(self, n: int = 5) -> List[NotesSection]:
        """获取最近 N 个段落。
        
        Args:
            n: 取最近 N 个
        
        Returns:
            List[NotesSection]: 最近的 N 个段落
        """
        ...

    def estimate_tokens(self) -> int:
        """粗略 token 估算。
        
        Returns:
            int: 估算 token 数（按 char/4）
        """
        ...

    def write_final_summary(self, summary: str) -> None:
        """写入最终总结（追加到末尾）。
        
        Args:
            summary: 总结内容
        """
        ...

    def _serialize_section(self, section: NotesSection) -> str:
        """序列化单个段落为 markdown 字符串。"""
        ...

    def _trim_if_needed(self) -> None:
        """检查并 trim（超过 max_size_kb 时保留最近 N 个段落）。"""
        ...

    def _atomic_write(self, content: str) -> None:
        """原子写入（先 .tmp，fsync，rename）。"""
        ...


__all__ = [
    "NotesSection",
    "NotesMemory",
]
```

**关键方法实现要求**：

1. **`load()`**：
   - 文件不存在 → 返回空字符串（不抛异常）
   - 真实 `read_text(encoding='utf-8')`（不模拟）

2. **`append()`**：
   - 真实原子写入：`Path.write_text()` + `os.replace()`（跨平台 rename）
   - 不持锁（Phase 18 单进程，V2.6 后续可加 fcntl）

3. **`list_sections()`**：
   - 真实正则解析：`re.split(r'^## ', content, flags=re.MULTILINE)`
   - 容忍格式错误（解析失败 → 跳过该段 + warning）

4. **`_trim_if_needed()`**：
   - 按 file_size_kb 判断（`Path.stat().st_size / 1024`）
   - 保留最近 N 段（`sections[-N:]`）+ 一个"trimmed"提示段

---

### 3.4 AutoSkillLoader（自动加载 skills / plugins）

**文件位置**：`scripts/autonomous/auto_skill_loader.py`

**职责**：
- 扫描 `.trae/skills/` 和 `plugins_extra/` 目录
- 解析 skill manifest（YAML / JSON）
- 缓存到 PluginContext.extra['auto_loaded_skills']
- 不实际注入到 dispatcher（仅做"提示"，避免污染 V3 行为）

**接口设计**：

```python
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass, field
import json


@dataclass
class SkillManifest:
    """自动加载的 skill manifest。
    
    字段说明：
    - name: skill 名称（kebab-case）
    - path: 完整路径
    - description: 简短描述
    - triggers: 触发关键词列表
    - priority: 优先级（数字越小越优先）
    - version: 版本字符串
    - author: 作者
    - requires: 依赖列表
    """
    name: str
    path: Path
    description: str = ""
    triggers: List[str] = field(default_factory=list)
    priority: int = 100
    version: str = "0.0.0"
    author: str = ""
    requires: List[str] = field(default_factory=list)


class AutoSkillLoader:
    """Ralph 风格的自动 skill 加载。
    
    设计原则：
    1. 不修改 dispatcher（V3 插件不感知 auto-loaded skills）
    2. 仅做"提示"：将 detected skills 写入 PluginContext.extra
    3. dispatcher 的智能体调用时可在 prompt 中看到这些 skills
    """

    def __init__(
        self,
        project_root: Path,
        extra_dirs: Optional[List[Path]] = None,
    ):
        """构造 AutoSkillLoader。
        
        Args:
            project_root: 项目根目录（扫描 .trae/skills/）
            extra_dirs: 额外扫描目录（默认 + plugins_extra/）
        """
        ...

    def detect(self) -> List[SkillManifest]:
        """扫描所有配置的目录，检测可用 skills。
        
        Returns:
            List[SkillManifest]: 检测到的 skills（按 priority 升序）
        
        行为：
        1. 扫描 project_root/.trae/skills/**/*.json (或 .yaml)
        2. 扫描 project_root/plugins_extra/**/*.json
        3. 解析 manifest（解析失败 → 跳过 + warning）
        4. 按 priority 升序排序
        """
        ...

    def detect_for_task(self, task: str) -> List[SkillManifest]:
        """根据 task 描述过滤相关 skills。
        
        Args:
            task: 任务描述
        
        Returns:
            List[SkillManifest]: 与 task 相关的 skills（按优先级 + 关键词匹配）
        
        算法：
        1. detect() 获取所有 skills
        2. 对每个 skill，统计 triggers 与 task 的交集大小
        3. 交集 > 0 的视为相关，按 (priority, -intersect_size) 排序
        """
        ...

    def format_for_prompt(self, skills: List[SkillManifest]) -> str:
        """格式化为可注入 prompt 的字符串。
        
        Args:
            skills: skills 列表
        
        Returns:
            str: 格式化的 markdown 文本（可作为 system prompt 片段）
        
        格式：
        ```
        ## Auto-detected Skills
        
        - [name] description (triggers: kw1, kw2)
        - ...
        ```
        """
        ...


__all__ = [
    "SkillManifest",
    "AutoSkillLoader",
]
```

**关键方法实现要求**：

1. **`detect()`**：
   - 真实 glob 扫描：`Path.glob('**/*.json')`
   - 真实 JSON 解析：`json.loads()`
   - 不存在的目录 → 跳过（不抛异常）
   - name 必须 kebab-case（否则跳过 + warning）

2. **`detect_for_task()`**：
   - 真实关键词交集计算（不模拟匹配度）
   - 区分大小写匹配（task 关键词统一转小写）
   - 返回的 skills 仍按 priority 排序（同优先级按匹配数降序）

3. **`format_for_prompt()`**：
   - 真实生成 markdown 字符串
   - 包含 name / description / triggers 三个关键字段

---

### 3.5 SmartConfirmation（智能确认跳过）

**文件位置**：`scripts/autonomous/smart_confirmation.py`

**职责**：
- 白名单：自动放行的确认
- 黑名单：禁止自动放行（强制 abort）
- 风险评估：基于命令特征打分
- 三态判定：AUTO_APPROVE / PROMPT_USER / REJECT

**接口设计**：

```python
from typing import Tuple, List, Optional
from enum import Enum
from dataclasses import dataclass
import re


class ConfirmationDecision(str, Enum):
    """智能确认决策。"""
    AUTO_APPROVE = "auto_approve"  # 自动放行
    PROMPT_USER = "prompt_user"    # 需用户确认（不适用 autonomous 模式）
    REJECT = "reject"              # 拒绝（强制 abort）


@dataclass
class ConfirmationConfig:
    """智能确认配置。
    
    字段说明：
    - whitelist_patterns: 白名单正则（命中即放行）
    - blacklist_patterns: 黑名单正则（命中即拒绝）
    - risk_weights: 风险特征权重（dict: feature_name → weight）
    - risk_threshold: 风险评分上限（超过则 PROMPT_USER）
    """
    whitelist_patterns: List[str] = field(default_factory=lambda: [
        r"^git add ",
        r"^git commit -m ",
        r"^python3 -m unittest",
        r"^pytest",
        r"^npm test",
        r"^git status$",
        r"^git diff",
    ])
    blacklist_patterns: List[str] = field(default_factory=lambda: [
        r"rm -rf /",
        r"rm -rf ~",
        r":\(\)\{ :\|:& \};:",  # fork bomb
        r"^sudo ",
        r"^curl .* \| bash",    # pipe to bash
        r"^wget .* \| sh",
        r"mkfs\.",
        r"dd if=.* of=/dev/",   # destructive dd
    ])
    risk_weights: dict = field(default_factory=lambda: {
        "writes_outside_repo": 10,
        "deletes_files": 5,
        "network_request": 3,
        "modifies_git_config": 8,
        "force_push": 20,
        "package_install": 4,
    })
    risk_threshold: int = 5


@dataclass
class ConfirmationRequest:
    """确认请求。
    
    字段说明：
    - command: 待执行命令
    - context: 上下文（如 "agent_requested_confirmation"）
    - agent_role: 请求的 agent role
    - iter_index: 所属迭代索引
    """
    command: str
    context: str = ""
    agent_role: str = ""
    iter_index: int = 0


@dataclass
class ConfirmationResult:
    """确认结果。
    
    字段说明：
    - decision: 决策（AUTO_APPROVE / PROMPT_USER / REJECT）
    - reason: 决策原因（中文）
    - risk_score: 风险评分（0-100）
    - matched_pattern: 命中的白/黑名单正则
    """
    decision: ConfirmationDecision
    reason: str
    risk_score: int = 0
    matched_pattern: Optional[str] = None


class SmartConfirmation:
    """智能确认决策器。"""

    def __init__(self, config: Optional[ConfirmationConfig] = None):
        """构造 SmartConfirmation。
        
        Args:
            config: 确认配置（None = 使用默认 config）
        """
        ...

    def check(self, request: ConfirmationRequest) -> ConfirmationResult:
        """评估确认请求。
        
        Args:
            request: 确认请求
        
        Returns:
            ConfirmationResult: 决策结果
        
        算法（短路求值）：
        1. 黑名单匹配 → REJECT（立即返回）
        2. 白名单匹配 → AUTO_APPROVE（立即返回）
        3. 风险评分：
           a. 检测特征（rm / curl / sudo / force / etc）
           b. 加权求和
        4. 风险分 >= 阈值 → PROMPT_USER
        5. 否则 → AUTO_APPROVE
        """
        ...

    def _match_pattern(self, command: str, patterns: List[str]) -> Optional[str]:
        """正则匹配（命中返回模式字符串，否则 None）。"""
        ...

    def _compute_risk(self, command: str) -> Tuple[int, List[str]]:
        """风险评分。
        
        Returns:
            (总分, 命中的特征列表)
        """
        ...


__all__ = [
    "ConfirmationDecision",
    "ConfirmationConfig",
    "ConfirmationRequest",
    "ConfirmationResult",
    "SmartConfirmation",
]
```

**关键方法实现要求**：

1. **`check()`**：
   - 黑名单优先（即使白名单也命中黑名单 → REJECT）
   - 真实正则匹配（不简化）
   - PROMPT_USER 决策在 autonomous 模式下特殊处理：视为 AUTO_APPROVE（因为无用户在场）但记录 warning 到 notes

2. **`_match_pattern()`**：
   - 用 `re.match()` 而非 `re.search()`（要求命令以模式开头）
   - 编译过的 pattern 缓存到 `self._compiled`

3. **`_compute_risk()`**：
   - 真实特征检测（不假装）：
     - `writes_outside_repo`: 检测 `cd /` 或 `> /` 或路径含 `..`
     - `deletes_files`: 检测 `rm ` 或 `rmdir `
     - `network_request`: 检测 `curl ` / `wget ` / `nc `
     - `modifies_git_config`: 检测 `git config `
     - `force_push`: 检测 `push --force` / `push -f`
     - `package_install`: 检测 `pip install` / `npm install` / `brew install`

---

### 3.6 SleepGuard（caffeinate 包装器）

**文件位置**：`scripts/autonomous/sleep_guard.py`

**职责**：
- macOS: 包装 `caffeinate -i` 子进程
- Linux: 抑制 systemd sleep（`systemd-inhibit`）
- Windows: no-op（Windows 不会自动睡眠）
- acquire / release 成对

**接口设计**：

```python
from typing import Optional
from pathlib import Path
import subprocess
import platform
import time
import signal
import os


class SleepGuardError(Exception):
    """SleepGuard 操作异常。"""
    pass


class SleepGuard:
    """防系统休眠守护。
    
    设计原则：
    1. acquire() 启动 caffeinate 子进程，release() 终止
    2. 跨平台：macOS caffeinate / Linux systemd-inhibit / Windows no-op
    3. 进程级守护：caffeinate 作为子进程，父进程退出自动清理
    4. 幂等：多次 acquire 不会启动多个子进程
    """

    def __init__(self, timeout_sec: Optional[int] = None):
        """构造 SleepGuard。
        
        Args:
            timeout_sec: 防休眠超时（None = 无限期）
        
        平台检测：
        - platform.system() == "Darwin" → caffeinate
        - platform.system() == "Linux" → systemd-inhibit
        - 其他 → no-op
        """
        ...

    def acquire(self) -> bool:
        """启动防休眠。
        
        Returns:
            bool: True = 成功启动；False = 平台不支持（no-op 模式）
        
        行为：
        - macOS: subprocess.Popen(["caffeinate", "-i", "-t", str(timeout_sec or 0)])
        - Linux: subprocess.Popen(["systemd-inhibit", ...])
        - 其他: return False（不抛异常）
        """
        ...

    def release(self) -> bool:
        """终止防休眠。
        
        Returns:
            bool: True = 成功；False = 未启动或已终止
        
        行为：
        - 找到子进程 PID
        - SIGTERM 优雅退出（caffeinate 接受 SIGTERM）
        - 等待最多 5s，超时则 SIGKILL
        """
        ...

    def is_active(self) -> bool:
        """检查防休眠是否激活。"""
        ...

    def __enter__(self):
        """with 语句支持。"""
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """with 语句退出。"""
        self.release()


__all__ = [
    "SleepGuardError",
    "SleepGuard",
]
```

**关键方法实现要求**：

1. **`acquire()`**：
   - 真实启动子进程（不模拟）
   - macOS: `caffeinate -i -s` （`-i` 防系统空闲睡眠， `-s` 防系统睡眠，仅在 AC 电源时）
   - 进程以 `start_new_session=True` 启动（避免父进程组信号）
   - 失败 → 抛 SleepGuardError（不假装成功）

2. **`release()`**：
   - 真实 `os.kill(pid, signal.SIGTERM)`
   - 等待进程退出（最多 5s）
   - 超时则 `os.kill(pid, signal.SIGKILL)`
   - 异常隔离（try/except 包裹，避免清理阶段崩溃）

3. **`is_active()`**：
   - 真实 `self._process.poll() is None` 检查

4. **`__enter__` / `__exit__`**：
   - 支持 `with SleepGuard() as sg:` 用法
   - `__exit__` 异常安全（即使内部异常也 release）

---

### 3.7 RunState / ResumeContext（断点续跑）

**文件位置**：`scripts/autonomous/run_state.py`

**职责**：
- 持久化 run 状态（迭代索引 / 阶段 / 已用 token / 累计结果）
- resume 时自动加载并校验
- 持久化到 `.gnhf/runs/<run_id>/state.json`

**接口设计**：

```python
from pathlib import Path
from typing import Optional, Dict, List
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
import json
import uuid


class RunStatus(str, Enum):
    """run 状态。"""
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABORTED = "aborted"
    CRASHED = "crashed"  # 异常退出（resume 时检测）


@dataclass
class IterationRecord:
    """单次迭代的持久化记录。
    
    字段说明：
    - iter_index: 迭代索引
    - stage: 阶段
    - started_at: ISO 8601
    - finished_at: ISO 8601
    - result_kind: "success" / "failed" / "retriable" / "fatal"
    - commit_hash: git commit hash（如果有）
    - diff_stats: (lines_added, lines_removed)
    - test_results: (passed, failed, skipped)
    - error_message: 错误信息（如果有）
    """
    iter_index: int
    stage: str
    started_at: str
    finished_at: str
    result_kind: str
    commit_hash: Optional[str] = None
    diff_stats: tuple = (0, 0)
    test_results: tuple = (0, 0, 0)
    error_message: str = ""


@dataclass
class RunStateData:
    """run 完整状态。
    
    字段说明：
    - run_id: 唯一 ID
    - task: 任务描述
    - status: run 状态
    - created_at: 创建时间
    - updated_at: 更新时间
    - completed_at: 完成时间（如果有）
    - iter_index: 当前迭代索引
    - cumulative_tokens: 累计 token
    - current_stage: 当前阶段
    - stop_when_matched: stop_when 是否匹配
    - iterations: 历史迭代记录
    - config_snapshot: LoopConfig 序列化
    - notes_md_path: notes.md 路径
    - uncommitted_manifests: uncommitted work manifest 路径列表
    """
    run_id: str
    task: str
    status: RunStatus
    created_at: str
    updated_at: str
    completed_at: Optional[str] = None
    iter_index: int = 0
    cumulative_tokens: int = 0
    current_stage: str = "plan"
    stop_when_matched: bool = False
    iterations: List[IterationRecord] = field(default_factory=list)
    config_snapshot: dict = field(default_factory=dict)
    notes_md_path: str = ""
    uncommitted_manifests: List[str] = field(default_factory=list)


class RunState:
    """Ralph run 状态持久化。
    
    设计原则：
    1. JSON 持久化（人类可读 + 易调试）
    2. 原子写入：先 .tmp，rename
    3. 并发安全：单进程（Phase 18），V2.6 加 fcntl
    4. resume 校验：run_id 存在 + status=in_progress + task 匹配
    """

    def __init__(self, run_dir: Path):
        """构造 RunState。
        
        Args:
            run_dir: .gnhf/runs/<run_id>/ 目录
        """
        ...

    @staticmethod
    def init_new(
        run_root: Path,
        task: str,
        config: dict,
    ) -> "RunState":
        """创建新 run。
        
        Args:
            run_root: .gnhf/runs/ 根目录
            task: 任务描述
            config: LoopConfig 序列化
        
        Returns:
            RunState: 新创建的 run state（已 persist）
        """
        ...

    @staticmethod
    def load(run_dir: Path) -> "RunState":
        """加载已有 run。
        
        Args:
            run_dir: .gnhf/runs/<run_id>/ 目录
        
        Returns:
            RunState: 加载的 run state
        
        Raises:
            FileNotFoundError: state.json 不存在
            ValueError: state.json 格式错误
        """
        ...

    def persist(self) -> None:
        """原子写入 state.json。"""
        ...

    def record_iteration(self, record: IterationRecord) -> None:
        """记录一次迭代完成。"""
        ...

    def mark_complete(self, status: RunStatus = RunStatus.COMPLETED) -> None:
        """标记 run 完成。"""
        ...

    def is_resumable(self) -> bool:
        """检测是否可 resume（status=in_progress 或 crashed）。"""
        ...

    @staticmethod
    def list_resumable_runs(run_root: Path) -> List[Path]:
        """列出所有可 resume 的 run 目录。
        
        Returns:
            List[Path]: 可 resume 的 run 目录列表（按 updated_at 降序）
        """
        ...


@dataclass
class ResumeContext:
    """断点续跑上下文。
    
    字段说明：
    - run_state: 加载的 RunState
    - notes_memory: 加载的 NotesMemory
    - git_driver: 构造的 GitDriver
    - prev_iter_count: 历史迭代次数
    - resume_iter_index: 下一轮迭代索引
    """
    run_state: RunState
    notes_memory: NotesMemory
    git_driver: "GitDriver"
    prev_iter_count: int
    resume_iter_index: int


__all__ = [
    "RunStatus",
    "IterationRecord",
    "RunStateData",
    "RunState",
    "ResumeContext",
]
```

**关键方法实现要求**：

1. **`init_new()`**：
   - 生成 UUID（uuid.uuid4().hex[:16]）
   - 创建目录 `run_root / <run_id>/`
   - 写入 state.json（status=in_progress）
   - 返回已初始化的 RunState

2. **`load()`**：
   - 真实 `json.loads(state_path.read_text())`
   - 失败 → 抛 ValueError（不假装成功）
   - 不存在的 run_dir → 抛 FileNotFoundError

3. **`persist()`**：
   - 原子写入（先 .tmp，再 rename）
   - 包含 `updated_at` 字段

4. **`is_resumable()`**：
   - status in {in_progress, crashed} → True
   - 否则 False

5. **`list_resumable_runs()`**：
   - 真实 `glob` + 过滤
   - 按 `updated_at` 降序排序

---

### 3.8 4 阶段 Handler

**文件位置**：`scripts/autonomous/handlers/`

**目录结构**：

```
handlers/
├── __init__.py
├── base.py           # StageHandler ABC
├── plan_handler.py   # PlanHandler 实现
├── dev_handler.py    # DevHandler 实现
├── verify_handler.py # VerifyHandler 实现
└── fix_handler.py    # FixHandler 实现
```

#### 3.8.1 StageHandler ABC（base.py）

```python
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autonomous.loop_controller import IterationContext, IterationResult


class StageHandler(ABC):
    """阶段处理器抽象基类。"""

    @property
    @abstractmethod
    def stage(self) -> str:
        """返回 stage 名称。"""

    @abstractmethod
    def handle(self, ctx: "IterationContext") -> "IterationResult":
        """处理单次迭代。
        
        Args:
            ctx: 迭代上下文
        
        Returns:
            IterationResult: 处理结果
        """
        ...

    def pre_check(self, ctx: "IterationContext") -> bool:
        """前置检查（默认 True）。"""
        return True

    def post_check(self, ctx: "IterationContext", result: "IterationResult") -> bool:
        """后置检查（默认 True）。"""
        return True


__all__ = ["StageHandler"]
```

#### 3.8.2 PlanHandler（plan_handler.py）

**职责**：
- 检测可用 skills（调用 AutoSkillLoader）
- 读取历史 notes
- 生成 / 加载 plan
- 决定下一轮迭代的具体目标

**关键方法**：

```python
class PlanHandler(StageHandler):
    @property
    def stage(self) -> str:
        return "plan"

    def handle(self, ctx: IterationContext) -> IterationResult:
        """生成 / 加载 plan。
        
        行为：
        1. 调用 AutoSkillLoader.detect_for_task(ctx.task) 获取相关 skills
        2. 调用 NotesMemory.list_sections() 读取历史
        3. 调用 DispatcherAdapter.invoke_agent(
             role="architect",
             prompt=PLAN_PROMPT.format(skills, notes, task),
             timeout=120
           )
        4. 解析 LLM 输出，提取 plan（markdown）
        5. 写回 ctx.current_plan
        6. 写一段到 notes（"## Iteration N: Plan"）
        7. 返回 SUCCESS
        """
        ...
```

#### 3.8.3 DevHandler（dev_handler.py）

**职责**：
- 实际调用 dispatcher 执行开发
- 智能确认（白名单 / 黑名单）

**关键方法**：

```python
class DevHandler(StageHandler):
    @property
    def stage(self) -> str:
        return "dev"

    def handle(self, ctx: IterationContext) -> IterationResult:
        """执行开发。
        
        行为：
        1. 解析 ctx.current_plan 提取本轮具体任务
        2. 调用 SmartConfirmation.check() 评估 agent 可能的命令
        3. 调用 DispatcherAdapter.invoke_agent(
             role="solo-coder",
             prompt=DEV_PROMPT.format(plan, skills),
             timeout=600
           )
        4. 检查 git status（如有 uncommitted 变更 → FAILED，提示 commit）
        5. 收集 agent 输出 + diff stats
        6. 写回 notes
        7. 返回 IterationResult
        """
        ...
```

#### 3.8.4 VerifyHandler（verify_handler.py）

**职责**：
- 跑测试
- 安全分析
- 性能分析

**关键方法**：

```python
class VerifyHandler(StageHandler):
    @property
    def stage(self) -> str:
        return "verify"

    def handle(self, ctx: IterationContext) -> IterationResult:
        """验证结果。
        
        行为：
        1. 调用 TestRunner.run(ctx.config.test_command) 执行测试
        2. 调用 SecurityAnalyzer.run() 执行安全分析
        3. 调用 PerformanceAnalyzer.run() 性能分析（可选）
        4. 聚合成 VerifyReport
        5. 如果有失败 → 返回 FAILED（含 failed tests 列表）
        6. 写回 notes
        7. 返回 SUCCESS 或 FAILED
        """
        ...
```

#### 3.8.5 FixHandler（fix_handler.py）

**职责**：
- 错误分类（编译错误 / 运行时错误 / 测试失败 / 性能问题）
- 修复策略选择（重做 / 调整 / 回滚）
- 错误计数器（连续失败 abort）

**关键方法**：

```python
class FixHandler(StageHandler):
    @property
    def stage(self) -> str:
        return "fix"

    def handle(self, ctx: IterationContext) -> IterationResult:
        """修复错误。
        
        行为：
        1. 调用 ErrorClassifier.classify(verify_report)
        2. 根据分类选择策略：
           - 编译错误 → 回退到 DevHandler
           - 测试失败 → 调用 LLM 修复（solo-coder）
           - 性能问题 → 提示优化建议
        3. 连续失败计数（self._consecutive_failures）
        4. 达到阈值 → 返回 FATAL
        5. 写回 notes
        6. 返回 IterationResult
        """
        ...
```

### 3.9 DispatcherAdapter（dispatcher 适配器）

**文件位置**：`scripts/autonomous/dispatcher_adapter.py`

**职责**：
- 包装 GoalDispatcher 调用（不替代）
- 构造 PluginContext（复用 V3）
- 解析 DispatchResult
- 提供给 Handler 调用的统一接口

**接口设计**：

```python
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass


@dataclass
class AgentInvocation:
    """Agent 调用配置。
    
    字段说明：
    - role: 角色（architect / product-manager / solo-coder / test-expert / ui-designer）
    - task: 任务描述
    - prompt: 完整 prompt（可覆盖 task）
    - timeout_sec: 超时（默认 600）
    - extra_args: 额外参数（注入到 PluginContext.extra）
    """
    role: str
    task: str
    prompt: Optional[str] = None
    timeout_sec: int = 600
    extra_args: Dict[str, Any] = None


@dataclass
class AgentInvocationResult:
    """Agent 调用结果。
    
    字段说明：
    - success: 是否成功
    - output: 智能体输出
    - error: 错误信息（如果有）
    - duration_sec: 耗时
    - token_used: 估算 token
    """
    success: bool
    output: str = ""
    error: str = ""
    duration_sec: float = 0.0
    token_used: int = 0


class DispatcherAdapter:
    """Dispatcher 适配器（Phase 18 关键组件）。
    
    设计原则：
    1. 不修改 dispatcher：仅作为上层封装
    2. 不破坏 V3 行为：plugin 仍由 dispatcher 调度
    3. 复用 PluginContext：避免重复构造
    """

    def __init__(
        self,
        project_root: Path,
        run_id: str,
        iter_index: int,
        plugin_context: "PluginContext",
    ):
        """构造 DispatcherAdapter。
        
        Args:
            project_root: 项目根目录
            run_id: run ID
            iter_index: 迭代索引
            plugin_context: 复用的 PluginContext
        """
        ...

    def invoke_agent(self, invocation: AgentInvocation) -> AgentInvocationResult:
        """调用智能体（封装 dispatcher）。
        
        Args:
            invocation: 调用配置
        
        Returns:
            AgentInvocationResult: 调用结果
        
        行为：
        1. 构造模拟的 argparse.Namespace（包含 task / agent / project_root / iter_index）
        2. 调用 dispatcher.dispatch(args, ctx) 真实调度
        3. 解析 DispatchResult
        4. 读取 agent 输出（从 output 文件或 stdout）
        5. 返回封装结果
        """
        ...

    def parse_args(self, invocation: AgentInvocation) -> "argparse.Namespace":
        """构造模拟 args（供 dispatcher 使用）。"""
        ...


__all__ = [
    "AgentInvocation",
    "AgentInvocationResult",
    "DispatcherAdapter",
]
```

**关键实现要求**：

1. **`invoke_agent()`**：
   - 必须真实调用 `dispatcher.dispatch()`（不模拟）
   - 通过 `subprocess.run` 调用 `trae_agent_dispatch.py` 子进程（隔离当前 Ralph 进程的异常）
   - 解析子进程 stdout / stderr / returncode
   - timeout 严格（超时 → SIGKILL + 标记 failed）

---

## 4. CLI 设计

### 4.1 新增 CLI Flag 列表

参考 gnhf 的 CLI 设计，适配 trae-agent 风格。新增 flag 全部在 `cli/parser.py` 中定义：

| Flag | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `--autonomous` | bool | False | 启用 Ralph 风格 autonomous 模式 |
| `--auto-max-iterations` | int | 50 | 最大迭代次数（硬上限） |
| `--auto-max-tokens` | int | 500000 | 最大 token 预算 |
| `--auto-stop-when` | str | "" | 自然语言停止条件（如 "all tests pass and no warnings"） |
| `--auto-test-command` | str | "python3 -m unittest discover -s tests -p 'test_*.py'" | 测试命令 |
| `--auto-stage-order` | str | "plan,dev,verify,fix" | 阶段顺序（CSV） |
| `--auto-backoff-base` | float | 1.0 | 失败退避基数（秒） |
| `--auto-backoff-max` | float | 60.0 | 退避上限（秒） |
| `--auto-failure-abort` | int | 3 | 连续失败 abort 阈值 |
| `--auto-resume` | str | None | resume 指定 run_id（None = 新建） |
| `--auto-resume-latest` | bool | False | resume 最新可续跑的 run |
| `--auto-no-caffeinate` | bool | False | 禁用 caffeinate（CI 环境） |
| `--auto-no-commit` | bool | False | 禁用 auto-commit（仅记录，不 git commit） |
| `--auto-confirm-mode` | str | "smart" | 确认模式（smart/whitelist-only/blacklist-only） |
| `--auto-run-dir` | str | ".gnhf/runs" | run 状态目录（相对 project_root） |
| `--auto-git-author-name` | str | "Ralph Autonomous Agent" | git commit 作者名 |
| `--auto-git-author-email` | str | "ralph@trae-multi-agent.local" | git commit 作者邮箱 |

### 4.2 互斥约束（mutex group）

```python
# Phase 18：autonomous 模式与其他模式的互斥
autonomous_group = parser.add_mutually_exclusive_group()
autonomous_group.add_argument(
    '--autonomous',
    dest='autonomous',
    action='store_true',
    default=False,
    help='Phase 18 启用 Ralph 风格 autonomous 模式（与 --goal-cancel / --goal-graph / --goal-resume 互斥）',
)
# 注：--goal-cancel / --goal-graph / --goal-resume 已在 Phase 14/15 定义
#     新增的 --autonomous 必须与它们互斥
```

### 4.3 示例命令

```bash
# 1. 基础用法：autonomous 模式跑 50 轮
python3 scripts/trae_agent_dispatch.py \
    --autonomous \
    --task "实现 Phase 18 autonomous 框架" \
    --agent auto

# 2. 限制迭代 + 自定义停止条件
python3 scripts/trae_agent_dispatch.py \
    --autonomous \
    --task "为 Phase 18 写单元测试" \
    --auto-max-iterations 20 \
    --auto-stop-when "all tests pass with coverage > 80%"

# 3. Resume 上次未完成的 run
python3 scripts/trae_agent_dispatch.py \
    --autonomous \
    --auto-resume-latest

# 4. Resume 指定 run
python3 scripts/trae_agent_dispatch.py \
    --autonomous \
    --auto-resume abc123def456

# 5. CI 模式（禁用 caffeinate + auto-commit）
python3 scripts/trae_agent_dispatch.py \
    --autonomous \
    --task "..." \
    --auto-no-caffeinate \
    --auto-no-commit

# 6. 自定义阶段顺序（仅 dev + verify，跳过 plan + fix）
python3 scripts/trae_agent_dispatch.py \
    --autonomous \
    --task "..." \
    --auto-stage-order "dev,verify"
```

### 4.4 plugin 注册（plugins/autonomous.py）

新增 1 个 V3 插件（`RalphAutonomousPlugin`），作为 autonomous 模式的入口：

```python
# scripts/plugins/autonomous.py
"""RalphAutonomousPlugin — Phase 18 入口插件（priority=5）。"""
import argparse
from typing import Set
from plugins.base import GoalCommandPlugin
from dispatcher.plugin_context import PluginContext


class RalphAutonomousPlugin(GoalCommandPlugin):
    """Phase 18 引入的 Ralph 风格 autonomous 入口插件（priority=5，DESTROY-like）。"""

    @property
    def name(self) -> str:
        return "autonomous"

    @property
    def priority(self) -> int:
        return 5  # DESTROY-like（高优先级）

    @property
    def mutex_with(self) -> Set[str]:
        return {"goal-cancel", "goal-graph", "goal-resume", "multi-goal", "loop"}

    @property
    def requires_task(self) -> bool:
        return False  # resume 模式可无 --task

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "autonomous", False) or getattr(args, "auto_resume", None) is not None or getattr(args, "auto_resume_latest", False)

    def execute(self, args: argparse.Namespace, ctx: PluginContext) -> bool:
        from autonomous.loop_controller import RalphLoopController, LoopConfig
        from autonomous.git_driver import GitDriver
        from autonomous.notes_memory import NotesMemory, NotesSection
        from autonomous.auto_skill_loader import AutoSkillLoader
        from autonomous.smart_confirmation import SmartConfirmation
        from autonomous.run_state import RunState, RunStatus
        from autonomous.dispatcher_adapter import DispatcherAdapter
        from autonomous.handlers import (
            PlanHandler, DevHandler, VerifyHandler, FixHandler
        )
        from autonomous.sleep_guard import SleepGuard
        from datetime import datetime
        from pathlib import Path

        # 1. 构造配置
        config = LoopConfig(
            max_iterations=args.auto_max_iterations,
            max_tokens=args.auto_max_tokens,
            stop_when=args.auto_stop_when,
            test_command=args.auto_test_command,
            consecutive_failure_abort=args.auto_failure_abort,
            backoff_base_sec=args.auto_backoff_base,
            backoff_max_sec=args.auto_backoff_max,
            git_author_name=args.auto_git_author_name,
            git_author_email=args.auto_git_author_email,
        )

        # 2. 初始化 run state
        run_root = ctx.project_root / args.auto_run_dir
        run_root.mkdir(parents=True, exist_ok=True)
        if args.auto_resume:
            run_dir = run_root / args.auto_resume
            run_state = RunState.load(run_dir)
        elif args.auto_resume_latest:
            resumable = RunState.list_resumable_runs(run_root)
            if not resumable:
                ctx.log("❌ 无可 resume 的 run", "ERROR")
                return False
            run_dir = resumable[0]
            run_state = RunState.load(run_dir)
        else:
            run_state = RunState.init_new(run_root, args.task, asdict(config))
            run_dir = run_root / run_state.data.run_id

        # 3. 构造组件
        notes_path = run_dir / "notes.md"
        notes_memory = NotesMemory(notes_path)
        git_driver = GitDriver(
            repo_root=ctx.project_root,
            run_id=run_state.data.run_id,
            author_name=config.git_author_name,
            author_email=config.git_author_email,
            run_dir=run_dir,
        )
        auto_skill_loader = AutoSkillLoader(project_root=ctx.project_root)
        smart_confirmation = SmartConfirmation()
        dispatcher_adapter = DispatcherAdapter(
            project_root=ctx.project_root,
            run_id=run_state.data.run_id,
            iter_index=run_state.data.iter_index,
            plugin_context=ctx,
        )

        # 4. 构造 4 阶段 handlers
        stage_handlers = {
            "plan": PlanHandler(dispatcher_adapter, auto_skill_loader, notes_memory, ctx.log),
            "dev": DevHandler(dispatcher_adapter, smart_confirmation, notes_memory, ctx.log),
            "verify": VerifyHandler(dispatcher_adapter, notes_memory, ctx.log),
            "fix": FixHandler(dispatcher_adapter, notes_memory, ctx.log),
        }

        # 5. 构造主循环
        loop = RalphLoopController(
            config=config,
            project_root=ctx.project_root,
            git_driver=git_driver,
            notes_memory=notes_memory,
            auto_skill_loader=auto_skill_loader,
            smart_confirmation=smart_confirmation,
            run_state=run_state,
            dispatcher_adapter=dispatcher_adapter,
            stage_handlers=stage_handlers,
            log=ctx.log,
        )

        # 6. 执行（with SleepGuard）
        sleep_guard = SleepGuard()
        try:
            if not args.auto_no_caffeinate:
                sleep_guard.acquire()
            exit_code = loop.run()
            run_state.mark_complete(RunStatus.COMPLETED if exit_code == 0 else RunStatus.ABORTED)
            return exit_code == 0
        finally:
            if not args.auto_no_caffeinate:
                sleep_guard.release()
```

**注册到 BUILTIN_PLUGINS**（修改 `scripts/plugins/__init__.py`）：

```python
# 在 BUILTIN_PLUGINS 列表中添加
BUILTIN_PLUGINS: list = [
    RalphAutonomousPlugin(),  # Phase 18 新增
    GoalCancelPlugin(),
    GoalGraphPlugin(),
    GoalResumePlugin(),
    MultiGoalPlugin(),
    LoopGoalPlugin(),
]
```

---

## 5. 配置设计（config.yml）

### 5.1 配置文件位置

- 用户级：`~/.trae/autonomous.yml`（个人偏好）
- 项目级：`<project_root>/.trae/autonomous.yml`（项目共享）
- 优先级：项目级 > 用户级 > 默认值

### 5.2 Schema

```yaml
# Ralph Autonomous 配置（Phase 18）
# 注意：本文件为真实配置，非占位/非模拟

# 1. 全局配置
global:
  # 默认 agent 角色（auto = 自动识别）
  default_agent: auto
  
  # 日志级别
  log_level: INFO  # DEBUG / INFO / WARNING / ERROR
  
  # Token 估算粒度（每字符 token 数）
  token_per_char: 0.25  # 约等于 1 token / 4 char

# 2. 循环配置
loop:
  # 最大迭代次数（硬上限，防失控）
  max_iterations: 50
  
  # 最大 token 预算
  max_tokens: 500000
  
  # 自然语言停止条件（LLM 评估）
  stop_when: ""  # 例: "all tests pass and no warnings"
  
  # 阶段顺序
  stage_order:
    - plan
    - dev
    - verify
    - fix
  
  # 失败退避
  backoff_base_sec: 1.0
  backoff_max_sec: 60.0
  consecutive_failure_abort: 3

# 3. Git 配置
git:
  # commit 作者
  author_name: "Ralph Autonomous Agent"
  author_email: "ralph@trae-multi-agent.local"
  
  # 是否允许 force push（默认 false）
  allow_force_push: false
  
  # commit 模板
  commit_template: "iter-{iter_index} [{stage}]: {summary}"
  
  # 是否自动 commit（CI 环境可禁用）
  auto_commit: true

# 4. Sleep 防护配置
sleep_guard:
  # 是否启用（默认 true）
  enabled: true
  
  # 平台特定配置
  macos:
    caffeinate_args: ["-i"]  # -i = 防系统空闲睡眠
  linux:
    systemd_inhibit_args: ["--what=sleep", "--mode=block"]
  windows:
    enabled: false  # Windows 不自动睡眠

# 5. Notes 配置
notes:
  # 路径模板（相对 run_dir）
  path: "notes.md"
  
  # 最大文件大小（KB），超过则 trim
  max_size_kb: 1024
  
  # trim 时保留最近 N 个段落
  trim_keep_last_n: 20
  
  # 是否在每次迭代都写
  write_per_iteration: true

# 6. Auto-skill 加载配置
auto_skill:
  # 扫描目录
  scan_dirs:
    - ".trae/skills"
    - "plugins_extra"
    - "~/.trae/skills"  # 用户级
  
  # 是否根据 task 自动过滤
  filter_by_task: true
  
  # 最大加载数量（防 prompt 爆炸）
  max_skills_per_iteration: 10

# 7. 智能确认配置
smart_confirmation:
  # 确认模式
  mode: smart  # smart / whitelist-only / blacklist-only
  
  # 白名单（自动放行）
  whitelist_patterns:
    - "^git add "
    - "^git commit -m "
    - "^python3 -m unittest"
    - "^pytest"
    - "^npm test"
  
  # 黑名单（强制拒绝）
  blacklist_patterns:
    - "rm -rf /"
    - "rm -rf ~"
    - "^sudo "
    - "curl .* \\| bash"
  
  # 风险特征权重
  risk_weights:
    writes_outside_repo: 10
    deletes_files: 5
    network_request: 3
    modifies_git_config: 8
    force_push: 20
    package_install: 4
  
  # 风险阈值
  risk_threshold: 5

# 8. 测试配置
test:
  # 测试命令
  command: "python3 -m unittest discover -s tests -p 'test_*.py'"
  
  # 超时（秒）
  timeout_sec: 300
  
  # 是否在 verify 阶段失败时 abort
  abort_on_test_failure: true

# 9. 安全分析配置
security:
  # 是否启用
  enabled: true
  
  # 分析器（builtin / bandit / semgrep）
  analyzer: builtin
  
  # 严重度阈值（>= 此值时 abort）
  severity_threshold: high  # low / medium / high / critical

# 10. 性能分析配置（可选）
performance:
  enabled: false
  # 启用后会在 verify 阶段跑性能测试
  # 命令
  command: "python3 -m pytest tests/performance/ -v --benchmark-only"
  # 超时
  timeout_sec: 600

# 11. Run 状态配置
run_state:
  # 状态目录（相对 project_root）
  dir: ".gnhf/runs"
  
  # 保留已完成 run 的天数（0 = 不清理）
  retain_days: 30
```

### 5.3 默认值（代码内 fallback）

如果用户未提供 config.yml，所有配置使用以下默认值（与上面一致）：

```python
DEFAULT_AUTONOMOUS_CONFIG = {
    "global": {
        "default_agent": "auto",
        "log_level": "INFO",
        "token_per_char": 0.25,
    },
    "loop": {
        "max_iterations": 50,
        "max_tokens": 500_000,
        "stop_when": "",
        "stage_order": ["plan", "dev", "verify", "fix"],
        "backoff_base_sec": 1.0,
        "backoff_max_sec": 60.0,
        "consecutive_failure_abort": 3,
    },
    # ... 其他省略
}
```

---

## 6. 与现有模块的集成点

### 6.1 集成点总览

| 集成点 | 涉及文件 | 关系 |
|--------|---------|------|
| **CLI 解析** | `scripts/cli/parser.py` | 新增 17 个 `--auto-*` flag |
| **V3 Plugin 注册** | `scripts/plugins/__init__.py` | 新增 `RalphAutonomousPlugin` |
| **Plugin 实现** | `scripts/plugins/autonomous.py` | 新增插件（薄壳，调用 autonomous 模块） |
| **Facade 兼容** | `scripts/facade.py` | 无改动（通过 plugin 间接工作） |
| **Dispatcher 调用** | `scripts/dispatcher/goal_dispatcher.py` | 无改动（被 DispatcherAdapter 调用） |
| **PluginContext** | `scripts/dispatcher/plugin_context.py` | 无改动（复用现有字段） |
| **DispatchResult** | `scripts/dispatcher/dispatch_result.py` | 无改动（被 DispatcherAdapter 解析） |
| **Loop Goal（/loop + /goal）** | `scripts/loop_goal.py` | 无改动（不互通，autonomous 自带循环） |
| **CheckPoint Manager** | `scripts/checkpoint_manager.py` | 无改动（autonomous 自带 RunState） |

### 6.2 关键不变量

1. **不修改 V3 任何代码**（除 cli/parser.py 新增 flag + plugins/__init__.py 注册新 plugin）
2. **不破坏现有 5 个内置 plugin**（priority 不冲突：autonomous=5，cancel=0，graph=10，loop=40）
3. **不破坏 1263 个现有测试**（autonomous 走新代码路径，不影响旧 dispatch 流程）
4. **dispatcher.dispatch() 仍为单一调度入口**（autonomous 通过 DispatcherAdapter 复用）

### 6.3 启动时序

```
python3 trae_agent_dispatch.py --autonomous --task "..."
    │
    ├─ 1. parse_arguments()  ← 解析 --autonomous + 17 个 --auto-* flag
    ├─ 2. _start_hot_reload_if_enabled()  ← V3 hot reload (autonomous 模式仍启用)
    ├─ 3. dispatcher.validate_mutex()  ← V3 mutex 校验（autonomous 与其他模式互斥）
    ├─ 4. dispatcher.dispatch()  ← 匹配 RalphAutonomousPlugin
    └─ 5. RalphAutonomousPlugin.execute()  ← Phase 18 入口
         ├─ 构造 LoopConfig
         ├─ RunState.init_new() / load()
         ├─ 构造 6 个组件（GitDriver / NotesMemory / AutoSkillLoader / ...）
         ├─ 构造 4 阶段 handlers
         ├─ 构造 RalphLoopController
         └─ SleepGuard.acquire() + loop.run() + finally: release()
```

---

## 7. 错误处理矩阵

### 7.1 4 类错误分类

| 错误类型 | 来源 | 处理策略 | 状态转换 |
|---------|------|----------|----------|
| **agent 报告失败** | 智能体显式声明 FAILED（如测试不通过） | 立即继续下一轮 | iter_result.kind = FAILED → rollback + continue |
| **重试硬错误** | subprocess timeout / 网络错误 | 指数退避（attempt 0/1/2） | iter_result.kind = RETRIABLE → backoff_sleep + retry |
| **永久错误** | git 损坏 / Python 语法错误 | 立即 abort（不重试） | iter_result.kind = FATAL → abort run |
| **commit 失败** | git commit 失败但 uncommitted work 保留 | rollback 阶段保留 manifest | iter_result.kind = FAILED → rollback (保留 manifest) |

### 7.2 错误处理伪代码

```python
# 在 RalphLoopController.run_one_iteration() 中
def run_one_iteration(self) -> IterationResult:
    try:
        # 1. 执行 4 阶段
        ctx = self._build_iteration_context()
        results = []
        for stage in self.config.stage_order:
            handler = self.stage_handlers[stage]
            result = handler.handle(ctx)
            results.append(result)
            if result.kind == "fatal":
                # 永久错误：立即返回，不继续
                return self._aggregate_result(results)
        
        # 2. 聚合成最终结果
        return self._aggregate_result(results)
    
    except subprocess.TimeoutExpired as e:
        # 重试硬错误：分类为 RETRIABLE
        self._consecutive_retriable_failures += 1
        return IterationResult(
            kind="retriable",
            error=e,
            summary=f"Subprocess timeout: {e}",
        )
    
    except GitCommandError as e:
        # Git 永久错误：分类为 FATAL（git 损坏不能继续）
        return IterationResult(
            kind="fatal",
            error=e,
            summary=f"Git 永久错误: {e}",
        )
    
    except SyntaxError as e:
        # Python 语法错误：分类为 FATAL
        return IterationResult(
            kind="fatal",
            error=e,
            summary=f"代码语法错误: {e}",
        )
    
    except Exception as e:
        # 未知错误：分类为 RETRIABLE（先重试，不行再 FATAL）
        self._consecutive_retriable_failures += 1
        return IterationResult(
            kind="retriable",
            error=e,
            summary=f"未分类错误: {e}",
        )
```

### 7.3 在主循环中的处理

```python
# 在 RalphLoopController.run() 中
def run(self) -> int:
    while not self.should_stop():
        iter_result = self.run_one_iteration()
        self.iter_index += 1
        
        if iter_result.kind == "success":
            # 成功：commit + 重置失败计数
            self.git_driver.commit(f"iter-{self.iter_index}: {iter_result.summary}")
            self._consecutive_retriable_failures = 0
        
        elif iter_result.kind == "failed":
            # 失败：rollback + 重置失败计数
            self.git_driver.rollback()
            self._consecutive_retriable_failures = 0
        
        elif iter_result.kind == "retriable":
            # 重试：检查连续失败次数
            if self._consecutive_retriable_failures >= self.config.consecutive_failure_abort:
                # 达到阈值：abort
                self.notes_memory.append(
                    NotesSection(
                        title=f"## FATAL: 连续 {self._consecutive_retriable_failures} 次失败",
                        body=f"达到连续失败阈值，run abort",
                        timestamp=datetime.now().isoformat(),
                        iter_index=self.iter_index,
                        tags=["fatal", "abort"],
                    )
                )
                return 2  # FATAL_ABORT
            
            # 未达到：指数退避
            self.backoff_sleep(self._consecutive_retriable_failures)
        
        elif iter_result.kind == "fatal":
            # 永久错误：立即 abort
            self.notes_memory.append(
                NotesSection(
                    title=f"## FATAL: 永久错误",
                    body=str(iter_result.error),
                    timestamp=datetime.now().isoformat(),
                    iter_index=self.iter_index,
                    tags=["fatal"],
                )
            )
            return 2  # FATAL_ABORT
        
        # 持久化
        self.run_state.record_iteration(
            IterationRecord(
                iter_index=self.iter_index,
                stage=",".join(self.config.stage_order),
                started_at=iter_result.started_at,
                finished_at=datetime.now().isoformat(),
                result_kind=iter_result.kind,
                diff_stats=iter_result.diff_stats,
                test_results=iter_result.test_results,
                error_message=str(iter_result.error) if iter_result.error else "",
            )
        )
        self.run_state.persist()
    
    return 0  # 正常退出（命中 stop_when 或 max_iterations）
```

### 7.4 退出码语义

| 退出码 | 含义 | 触发条件 |
|--------|------|----------|
| 0 | 全部成功 | 命中 stop_when 或 max_iterations 且所有迭代 SUCCESS |
| 1 | 部分失败 | 部分迭代 FAILED 但未达到 FATAL 阈值 |
| 2 | FATAL abort | 达到连续失败阈值或遇到永久错误 |
| 3 | 命中 stop_when | LLM 评估 stop_when 匹配 |

---

## 8. 测试策略

### 8.1 测试金字塔

按 trae-multi-agent 既有测试规范（参考 PHASE16/17 测试设计）：

| 测试层级 | 占比 | 目标覆盖率 | 工具 |
|---------|------|------------|------|
| 单元测试 | 70% | >85% | `unittest`（标准库） |
| 集成测试 | 20% | 关键流程 | `unittest` + `subprocess` |
| E2E 测试 | 10% | 真实场景 | `tests/scripts/run_phase18_*.sh` |

### 8.2 单元测试（Unit Tests）

**位置**：`scripts/tests/test_phase18_*.py`

**覆盖矩阵**：

| 测试文件 | 覆盖组件 | 测试数（预估） |
|---------|---------|----------------|
| `test_phase18_loop_controller.py` | RalphLoopController | 15 |
| `test_phase18_git_driver.py` | GitDriver | 12 |
| `test_phase18_notes_memory.py` | NotesMemory | 10 |
| `test_phase18_auto_skill_loader.py` | AutoSkillLoader | 8 |
| `test_phase18_smart_confirmation.py` | SmartConfirmation | 12 |
| `test_phase18_sleep_guard.py` | SleepGuard | 6 |
| `test_phase18_run_state.py` | RunState / ResumeContext | 12 |
| `test_phase18_handlers.py` | 4 阶段 Handlers | 16 |
| `test_phase18_dispatcher_adapter.py` | DispatcherAdapter | 8 |
| `test_phase18_autonomous_plugin.py` | RalphAutonomousPlugin | 6 |
| `test_phase18_cli.py` | CLI flag 解析 | 10 |
| `test_phase18_config.py` | config.yml 加载 | 8 |
| **合计** | | **123** |

**单元测试样例（test_phase18_git_driver.py）**：

```python
import unittest
from pathlib import Path
import tempfile
import subprocess
from autonomous.git_driver import GitDriver, GitOpResult, DiffStats


class TestGitDriver(unittest.TestCase):
    """GitDriver 单元测试。"""

    def setUp(self):
        """创建临时 git 仓库。"""
        self.tmpdir = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-b", "main"], cwd=self.tmpdir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=self.tmpdir, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.tmpdir, check=True)
        # 首次 commit
        (self.tmpdir / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=self.tmpdir, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.tmpdir, check=True)
        
        self.run_id = "test-run-001"
        self.driver = GitDriver(
            repo_root=self.tmpdir,
            run_id=self.run_id,
            author_name="Test Ralph",
            author_email="ralph@test.local",
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_is_git_repo_returns_true(self):
        """真实 git 仓库应返回 True。"""
        self.assertTrue(self.driver.is_git_repo())

    def test_status_returns_clean(self):
        """无变更时 status 应返回 clean。"""
        result = self.driver.status()
        self.assertTrue(result.success)
        self.assertEqual(result.stdout.strip(), "")

    def test_commit_empty_raises(self):
        """空 commit 应失败（避免误创建空 commit）。"""
        result = self.driver.commit("empty commit")
        self.assertFalse(result.success)
        self.assertIn("nothing to commit", result.stderr.lower())

    def test_commit_with_changes_succeeds(self):
        """有变更时 commit 应成功。"""
        (self.tmpdir / "new.txt").write_text("hello")
        result = self.driver.commit("add new.txt")
        self.assertTrue(result.success)
        self.assertIn("new.txt", result.stdout)

    def test_diff_stats(self):
        """diff 统计应正确。"""
        (self.tmpdir / "new.txt").write_text("hello\nworld\n")
        result = self.driver.commit("add new.txt")
        self.assertTrue(result.success)
        stats = self.driver.diff_stats()
        self.assertEqual(stats.files_changed, 1)
        self.assertEqual(stats.lines_added, 2)
        self.assertEqual(stats.lines_removed, 0)

    def test_rollback_preserves_uncommitted(self):
        """rollback 应保留 uncommitted work。"""
        # 1. 创建一个 uncommitted 变更
        (self.tmpdir / "modified.txt").write_text("modified content")
        # 2. rollback
        result = self.driver.rollback()
        self.assertTrue(result.success)
        # 3. 验证：uncommitted work 保留
        manifest = self.tmpdir / ".gnhf" / "runs" / self.run_id / "uncommitted"
        self.assertTrue(manifest.exists())

    def test_git_command_error_handling(self):
        """git 命令失败应返回详细错误（不假装成功）。"""
        # 删除 .git 后再操作
        import shutil
        shutil.rmtree(self.tmpdir / ".git")
        result = self.driver.status()
        self.assertFalse(result.success)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(result.error_message, "")
```

### 8.3 集成测试（Integration Tests）

**位置**：`scripts/tests/test_phase18_integration.py`

**覆盖场景**：

| 场景 | 描述 | 验证点 |
|------|------|--------|
| 全流程成功 | 跑 5 轮，每轮 commit | notes.md 有 5 段、git log 有 5 个 commit |
| 失败回滚 | 第 3 轮 FAILED | git log 仍为 2 个 commit、uncommitted manifest 存在 |
| 断点续跑 | 跑 3 轮后 kill，重启 resume | run_state.iter_index 恢复为 3、继续从 4 轮开始 |
| caffeinate | macOS 平台 verify | 子进程存活、release 后终止 |
| 智能确认 | 黑名单命令 | 拒绝 + abort |
| 智能确认 | 白名单命令 | 自动放行 |
| 阶段跳过 | `--auto-stage-order dev,verify` | 跳过 plan + fix |

### 8.4 E2E 测试（Shell 脚本）

**位置**：`tests/scripts/run_phase18_*.sh`

| 脚本 | 场景 | 验证 |
|------|------|------|
| `run_phase18_unit.sh` | 跑所有 Phase 18 单元测试 | 123 tests 全通过 |
| `run_phase18_integration.sh` | 跑所有 Phase 18 集成测试 | 全部 PASS |
| `run_phase18_e2e_basic.sh` | 真实跑 5 轮 autonomous | git log 有 5 个 commit |
| `run_phase18_e2e_resume.sh` | 中断 + resume | 继续执行 |
| `run_phase18_safety.sh` | 安全测试（黑名单命令） | 拒绝 + abort |
| `run_phase18_regression.sh` | 跑 1263 个旧测试 | 100% 通过（无破坏） |

### 8.5 性能测试

**位置**：`scripts/tests/test_phase18_performance.py`

| 指标 | 目标 | 验证方法 |
|------|------|----------|
| 单轮迭代耗时 | < 120s（不包含 agent 调用） | 真实跑 10 轮取中位数 |
| Resume 加载时间 | < 5s | 真实 run 状态加载 |
| notes.md 读写 | < 100ms（< 100KB） | timeit 测量 |
| git commit 耗时 | < 1s | timeit 测量 |

### 8.6 安全测试

**位置**：`scripts/tests/test_phase18_security.py`

| 测试 | 验证 |
|------|------|
| 路径穿越防护 | `--auto-run-dir ../etc` 被拒绝 |
| 黑名单命令拦截 | `rm -rf /` 被拦截 |
| 风险评分准确性 | `sudo apt install xxx` 评分为 10+ |
| git config 保护 | `git config --global` 风险评分高 |

---

## 9. 实施步骤（5 阶段）

### 9.1 阶段 1：基础组件（Week 1，3-4 天）

**目标**：实现 4 个无依赖组件 + 单元测试

| 任务 | 文件 | 验证 |
|------|------|------|
| 实现 NotesMemory | `scripts/autonomous/notes_memory.py` | 单元测试 10 个全通过 |
| 实现 GitDriver | `scripts/autonomous/git_driver.py` | 单元测试 12 个全通过 |
| 实现 RunState | `scripts/autonomous/run_state.py` | 单元测试 12 个全通过 |
| 实现 SleepGuard | `scripts/autonomous/sleep_guard.py` | 单元测试 6 个全通过 |
| **合计** | | **40 tests pass** |

**验收**：
- `python3 -m unittest tests.test_phase18_notes_memory tests.test_phase18_git_driver tests.test_phase18_run_state tests.test_phase18_sleep_guard -v` 全通过
- 现有 1263 tests 仍通过（无破坏）

### 9.2 阶段 2：智能组件（Week 1-2，3-4 天）

**目标**：实现 AutoSkillLoader + SmartConfirmation + 单元测试

| 任务 | 文件 | 验证 |
|------|------|------|
| 实现 AutoSkillLoader | `scripts/autonomous/auto_skill_loader.py` | 单元测试 8 个全通过 |
| 实现 SmartConfirmation | `scripts/autonomous/smart_confirmation.py` | 单元测试 12 个全通过 |
| **合计** | | **20 tests pass** |

**验收**：
- 60 tests pass（阶段 1 + 2）
- 智能确认黑名单测试 100% 拦截

### 9.3 阶段 3：Dispatcher 适配 + Handlers（Week 2，4-5 天）

**目标**：实现 DispatcherAdapter + 4 阶段 Handlers + 单元测试

| 任务 | 文件 | 验证 |
|------|------|------|
| 实现 DispatcherAdapter | `scripts/autonomous/dispatcher_adapter.py` | 单元测试 8 个全通过 |
| 实现 StageHandler ABC | `scripts/autonomous/handlers/base.py` | 单元测试 4 个全通过 |
| 实现 PlanHandler | `scripts/autonomous/handlers/plan_handler.py` | 单元测试 4 个全通过 |
| 实现 DevHandler | `scripts/autonomous/handlers/dev_handler.py` | 单元测试 4 个全通过 |
| 实现 VerifyHandler | `scripts/autonomous/handlers/verify_handler.py` | 单元测试 4 个全通过 |
| 实现 FixHandler | `scripts/autonomous/handlers/fix_handler.py` | 单元测试 4 个全通过 |
| **合计** | | **28 tests pass** |

**验收**：
- 88 tests pass（阶段 1+2+3）
- 4 个 Handler 单元测试全部通过

### 9.4 阶段 4：主循环 + Plugin 注册（Week 3，3-4 天）

**目标**：实现 RalphLoopController + RalphAutonomousPlugin + CLI 集成

| 任务 | 文件 | 验证 |
|------|------|------|
| 实现 RalphLoopController | `scripts/autonomous/loop_controller.py` | 单元测试 15 个全通过 |
| 新增 CLI flags | `scripts/cli/parser.py` | 单元测试 10 个全通过 |
| 实现 RalphAutonomousPlugin | `scripts/plugins/autonomous.py` | 单元测试 6 个全通过 |
| 注册到 BUILTIN_PLUGINS | `scripts/plugins/__init__.py` | 启动测试通过 |
| config.yml 加载 | `scripts/autonomous/config_loader.py` | 单元测试 8 个全通过 |
| **合计** | | **39 tests pass** |

**验收**：
- 127 tests pass（阶段 1+2+3+4）
- 现有 1263 tests 仍通过（CLI 兼容性 + plugin 注册）
- `python3 trae_agent_dispatch.py --autonomous --task "test"` 启动成功

### 9.5 阶段 5：集成测试 + E2E + 文档（Week 3-4，3-4 天）

**目标**：集成测试 + E2E shell 脚本 + 性能/安全测试 + 文档

| 任务 | 文件 | 验证 |
|------|------|------|
| 集成测试 | `scripts/tests/test_phase18_integration.py` | 7 个场景全通过 |
| E2E 脚本 | `tests/scripts/run_phase18_*.sh` | 6 个脚本全通过 |
| 性能测试 | `scripts/tests/test_phase18_performance.py` | 性能指标达标 |
| 安全测试 | `scripts/tests/test_phase18_security.py` | 安全场景全通过 |
| Phase 18 文档 | `docs/dev/PHASE18_PLAN.md`（本文件） | 架构师复核通过 |
| 用户文档 | `docs/guides/AUTONOMOUS_MODE_GUIDE.md` | 用户可读 |
| **合计** | | **15 tests pass** |

**验收**：
- 142 tests pass（全部 Phase 18 测试）
- 现有 1263 tests 仍通过（兼容性）
- Phase 18 P0/P1 风险全部解决
- 架构师 review 通过

### 9.6 总实施周期

- **Week 1**：阶段 1 + 2（基础 + 智能组件）
- **Week 2**：阶段 3（Dispatcher 适配 + Handlers）
- **Week 3**：阶段 4（主循环 + Plugin + CLI）
- **Week 3-4**：阶段 5（集成 + E2E + 文档）
- **总周期**：3-4 周

---

## 10. 风险评估

### 10.1 风险表

| 风险 ID | 等级 | 描述 | 影响 | 缓解措施 |
|---------|------|------|------|----------|
| **R-1** | P0 | autonomous 模式破坏现有 V3 dispatch 流程 | 1263 tests 失败 | 阶段 1-4 每步跑全量回归测试；plugin 走独立路径 |
| **R-2** | P0 | git commit 失败导致工作丢失 | 用户工作丢失 | rollback 保留 uncommitted work 到 .gnhf/runs/<id>/uncommitted/ |
| **R-3** | P0 | 智能确认被绕过（agent 执行 rm -rf） | 灾难性数据丢失 | 黑名单 + 风险评分双层防护；黑名单命中 → 立即 abort |
| **R-4** | P0 | caffeinate 子进程未清理（僵尸进程） | 电池耗尽 | SleepGuard try/finally 严格 release；atexit 兜底 |
| **R-5** | P0 | run state 损坏导致 resume 异常 | 无法恢复 | atomic write + sha256 校验 + 损坏检测 |
| **R-6** | P1 | LLM 评估 stop_when 不准确 | 过早/过晚停止 | 多种 stop_when 模式（OR/AND/THRESHOLD）；用户可关闭 |
| **R-7** | P1 | 4 阶段 handler 死循环 | 资源耗尽 | max_iterations 硬上限；同阶段失败计数 |
| **R-8** | P1 | 智能体调用 subprocess timeout | 卡死 | subprocess.run(timeout=严格) + SIGKILL 兜底 |
| **R-9** | P1 | notes.md 无限增长 | 磁盘占满 | max_size_kb + trim_keep_last_n 自动 trim |
| **R-10** | P1 | config.yml 解析失败 | autonomous 启动失败 | 严格 YAML schema + 启动时校验；失败 fallback 默认值 |
| **R-11** | P1 | 跨平台 caffeinate 不可用 | 无防休眠 | Linux fallback systemd-inhibit；Windows no-op；其他平台 no-op |
| **R-12** | P1 | concurrent run 状态冲突 | 数据损坏 | Phase 18 单进程限制（文档化）；V2.6 加 fcntl |
| **R-13** | P2 | 大型项目 commit message 太长 | git log 不可读 | commit_template 限制长度（截断 > 100 字符） |
| **R-14** | P2 | uncommitted work 恢复后冲突 | 文件冲突 | manifest 记录 sha256；恢复时校验 |
| **R-15** | P2 | performance test 超时导致 verify 失败 | false positive | timeout 单独配置（默认 600s） |
| **R-16** | P2 | LLM 评估 stop_when 消耗 token | token 预算浪费 | stop_when 评估限制为最近 5 次输出 |
| **R-17** | P2 | autonomous 模式与 multi-goal 冲突 | 互斥未配置 | mutex_with 添加 multi-goal；validate_mutex 启动期校验 |
| **R-18** | P2 | hot reload 与 autonomous 冲突 | dispatcher reload 时 autonomous 异常 | autonomous 模式下禁用 hot reload（--no-hot-reload 自动启用） |
| **R-19** | P2 | 日志文件无限增长 | 磁盘占满 | 日志轮转（按 size）；run 完成时归档 |
| **R-20** | P2 | 用户在 autonomous 运行中误操作 | 状态不一致 | autonomous 模式运行期间锁住 project_root（可选） |

### 10.2 风险缓解策略

**P0 风险**（5 项）：
- 每个 P0 风险都有自动化测试覆盖
- 阶段 5 E2E 测试必须覆盖所有 P0 风险
- 文档化"已知限制"段（用户必须知道的边界）

**P1 风险**（8 项）：
- 每个 P1 风险至少 1 个单元测试覆盖
- 配置项允许用户禁用/调整行为

**P2 风险**（7 项）：
- 不强求测试覆盖（可选）
- 文档化 + 后续 V2.6 改进

### 10.3 已知限制

明确文档化以下限制（用户必须知道）：

1. **不支持 worktree 并行**：本 Phase 6 不做 worktree，多 agent 并行需要 V2.6+
2. **不支持跨平台 sleep 防护完整实现**：macOS 完整，Linux 部分，Windows no-op
3. **不支持 concurrent run**：同一 project_root 只能跑 1 个 autonomous run
4. **不支持 multi-goal 编排**：autonomous 与 multi-goal 互斥
5. **不支持 LLM 调用优化**：dispatch_agent 仍走原有 V2/V3 路径

---

## 11. 验收标准

### 11.1 功能验收

| 项 | 验证方法 | 通过标准 |
|---|---------|----------|
| autonomous 模式可启动 | 跑 `--autonomous --task "test"` 1 轮 | 退出码 0；notes.md 创建 |
| Git commit 自动执行 | 跑 5 轮成功场景 | git log 有 5 个 commit |
| Git rollback 保留 uncommitted | 跑 1 轮失败场景 | .gnhf/runs/<id>/uncommitted/ 存在 |
| 智能确认白名单放行 | 单元测试 | 100% 通过 |
| 智能确认黑名单拦截 | 单元测试 | 100% 拦截 |
| 4 阶段 handler 顺序执行 | 单元测试 | 按 plan→dev→verify→fix 顺序 |
| 断点续跑恢复 | E2E 测试 | iter_index 正确恢复 |
| SleepGuard 启停 | 单元测试 | macOS 子进程可启动/停止 |
| Auto-skill 加载 | 单元测试 | 扫描 + 解析正确 |
| NotesMemory 累积 | 单元测试 | 段落按顺序追加 |
| RunState 持久化 | 单元测试 | state.json 格式正确 |
| DispatcherAdapter 复用 V3 | 集成测试 | 不破坏 1263 旧 tests |

### 11.2 性能验收

| 项 | 目标 | 实测 |
|---|------|------|
| 单轮迭代（无 agent 调用） | < 60s | 验证 |
| 单轮迭代（含 agent 调用） | < 300s | 验证 |
| Resume 加载时间 | < 5s | 验证 |
| notes.md 读写（< 100KB） | < 100ms | 验证 |
| git commit 耗时 | < 1s | 验证 |

### 11.3 兼容性验收

| 项 | 验证方法 | 通过标准 |
|---|---------|----------|
| 现有 1263 tests | 跑全量 | 100% 通过 |
| 现有 5 个内置 plugin | 跑 plugin 测试 | 100% 通过 |
| 现有 CLI flags | 跑全量 | 100% 兼容 |
| 现有 dispatcher 行为 | 跑 dispatcher 测试 | 100% 不变 |
| V3 facade 兼容 | 跑 facade 测试 | 100% 通过 |

### 11.4 安全性验收

| 项 | 验证方法 | 通过标准 |
|---|---------|----------|
| 黑名单命令拦截 | 安全测试 | 100% 拦截 |
| 路径穿越防护 | 安全测试 | `--auto-run-dir ../etc` 拒绝 |
| git config 保护 | 安全测试 | 全局 git config 不被修改 |
| uncommitted work 保留 | E2E 测试 | 100% 保留 |
| run state 加密（可选） | — | V2.6 改进 |

### 11.5 文档验收

| 文档 | 要求 |
|------|------|
| `PHASE18_PLAN.md`（本文件） | 架构师 review 通过 |
| `AUTONOMOUS_MODE_GUIDE.md` | 用户可读 + 完整示例 |
| `AUTONOMOUS_MODE_API.md` | 开发者 API 文档 |
| `CHANGELOG.md` | 新增 Phase 18 条目 |
| 代码注释 | 中文、详细、符合规范 |

---

## 12. 附录

### 12.1 文件结构（新增）

```
scripts/
├── autonomous/                      # 新增目录
│   ├── __init__.py
│   ├── loop_controller.py           # RalphLoopController
│   ├── git_driver.py                # GitDriver
│   ├── notes_memory.py              # NotesMemory
│   ├── auto_skill_loader.py         # AutoSkillLoader
│   ├── smart_confirmation.py        # SmartConfirmation
│   ├── sleep_guard.py               # SleepGuard
│   ├── run_state.py                 # RunState / ResumeContext
│   ├── dispatcher_adapter.py        # DispatcherAdapter
│   ├── config_loader.py             # config.yml 加载
│   └── handlers/
│       ├── __init__.py
│       ├── base.py                  # StageHandler ABC
│       ├── plan_handler.py          # PlanHandler
│       ├── dev_handler.py           # DevHandler
│       ├── verify_handler.py        # VerifyHandler
│       └── fix_handler.py           # FixHandler
├── plugins/
│   ├── __init__.py                  # 修改：注册 RalphAutonomousPlugin
│   └── autonomous.py                # 新增：RalphAutonomousPlugin
├── cli/
│   └── parser.py                    # 修改：新增 17 个 --auto-* flag
└── tests/
    ├── test_phase18_loop_controller.py
    ├── test_phase18_git_driver.py
    ├── test_phase18_notes_memory.py
    ├── test_phase18_auto_skill_loader.py
    ├── test_phase18_smart_confirmation.py
    ├── test_phase18_sleep_guard.py
    ├── test_phase18_run_state.py
    ├── test_phase18_handlers.py
    ├── test_phase18_dispatcher_adapter.py
    ├── test_phase18_autonomous_plugin.py
    ├── test_phase18_cli.py
    ├── test_phase18_config.py
    ├── test_phase18_integration.py
    ├── test_phase18_performance.py
    └── test_phase18_security.py

tests/scripts/
├── run_phase18_unit.sh
├── run_phase18_integration.sh
├── run_phase18_e2e_basic.sh
├── run_phase18_e2e_resume.sh
├── run_phase18_safety.sh
└── run_phase18_regression.sh
```

### 12.2 关键依赖

| 依赖 | 必需 | 用途 |
|------|------|------|
| `git` | 是 | 真实 git 命令 |
| `caffeinate` | macOS only | 防休眠 |
| `systemd-inhibit` | Linux only | 防休眠 |
| Python 标准库 | 是 | `subprocess` / `pathlib` / `json` / `re` / `uuid` |
| trae-multi-agent V3 | 是 | GoalDispatcher / PluginContext |
| 不引入新依赖 | — | 严格遵守约束 |

### 12.3 参考资料

- [gnhf (good night, have fun)](https://github.com/kunchenguid/gnhf) — Ralph/autoresearch 风格 orchestrator
- [PHASE16_PLAN.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE16_PLAN.md) — V3 插件架构
- [PHASE17_PLAN.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE17_PLAN.md) — 插件热加载
- [CONSTITUTION.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/spec/CONSTITUTION.md) — Karpathy 四大核心原则
- [Anthropic: Effective Harnesses for Long-Running Agents](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents) — Checkpoint + Handoff

---

## 13. 文档信息

| 项 | 内容 |
|---|------|
| 文档名称 | Phase 18 设计文档 |
| 文档类型 | 技术方案 spec |
| 项目名称 | trae-multi-agent |
| 版本号 | v1.0 |
| 创建日期 | 2026-06-07 |
| 最后更新 | 2026-06-07 |
| 起草人 | 架构师 (Architect Role) |
| 审核人 | 待定（架构师 + 产品经理） |
| 状态 | ⏳ v1 设计稿，待评审 |
| 预计实施周期 | 3-4 周 |
| 预计新增测试 | 142 tests |
