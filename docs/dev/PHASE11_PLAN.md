# Phase 11 PLAN v1.0 — /loop + /goal 集成

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 trae-multi-agent 增加 `/loop` 和 `/goal` 两个用户命令，让长程任务具备"目标定义 → 循环迭代 → 收敛退出"的能力。

**Architecture:** 1 个新模块 `loop_goal.py`（独立）+ 2 个 CLI 参数（--loop / --goal）+ 1 个 dispatch 包装器 + 磁盘持久化（`.trae/goals/`）。所有改动 additive；旧命令行为零变化。

**Tech Stack:** Python 3.x、argparse、json（持久化）、现有 `dispatch_agent_v2`（循环包装）。

---

## 一、动机

### 1.1 现状

- 当前 `trae_agent_dispatch_v2.py` 一次调用 = 一次 dispatch
- 长程任务（"修复所有测试" / "完成代码评审"）需要 **多次迭代** + **目标可追溯**
- 用户缺少 ① 显式目标定义 ② 迭代上限 ③ 收敛检测 ④ 跨迭代进度查看

### 1.2 目标

1. **`/loop <N>`**：循环执行 dispatch N 次（默认 1 = 不循环）
2. **`/goal <id> <description>`**：显式定义目标 + 验收标准（持久化）
3. **收敛检测**：连续 N 次无新产出 / 全部 criterion 满足 → 提前退出
4. **跨迭代可见**：每次 iteration 落盘到 `.trae/goals/<goal_id>/iter-<n>.json`
5. **Karpathy 联动**：`/goal` 注册时自动 verify `cp_goal_1`，完成时 verify `cp_goal_2`

### 1.3 不动

- V2 引擎、`PatternExecutor`、Phase 1-10 全部已交付模块
- 旧 `trae_agent_dispatch` 命令调用（不传 --loop/--goal 时行为零变化）

---

## 二、架构设计

### 2.1 新模块

**文件**：`scripts/loop_goal.py`

**核心组件**：

| 组件 | 职责 |
|------|------|
| `Goal` (dataclass) | 目标数据模型（id / description / criteria / status / iterations） |
| `LoopConfig` (dataclass) | 循环配置（max_iterations / convergence_window / stop_on_success） |
| `GoalStatus` (enum) | 目标状态：`ACTIVE` / `IN_PROGRESS` / `ACHIEVED` / `ABANDONED` / `FAILED` |
| `IterationResult` (dataclass) | 单次迭代结果（iteration_no / success / outputs / started_at / finished_at） |
| `GoalRegistry` | 目标 CRUD + 持久化（`.trae/goals/<goal_id>.json`） |
| `LoopGoalExecutor` | 解析 + 执行 /loop + /goal 流程 |
| `ConvergenceDetector` | 连续 N 次无新产出 → 提前退出 |
| `GoalVerifier` | 校验所有 criteria（可由调用方实现） |

### 2.2 核心类签名

```python
@dataclass
class Goal:
    goal_id: str                       # 目标 ID（kebab-case）
    description: str                   # 目标描述
    success_criteria: List[str]        # 验收标准列表
    status: GoalStatus = GoalStatus.ACTIVE
    iterations: List[IterationResult] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    convergence_window: int = 3        # 收敛窗口：连续 N 次无新产出则停止
    max_iterations: int = 10           # 安全上限

@dataclass
class LoopConfig:
    max_iterations: int = 1            # /loop N
    convergence_window: int = 3
    stop_on_success: bool = True
    convergence_check_outputs: bool = True

class GoalRegistry:
    """目标注册表（磁盘持久化到 .trae/goals/<goal_id>.json）"""
    def __init__(self, storage_root: str = ".trae/goals"): ...
    def create_goal(self, description: str, criteria: List[str], 
                    goal_id: Optional[str] = None, **kwargs) -> Goal: ...
    def get_goal(self, goal_id: str) -> Optional[Goal]: ...
    def update_goal(self, goal: Goal) -> None: ...
    def list_goals(self, status: Optional[GoalStatus] = None) -> List[Goal]: ...
    def save_iteration(self, goal_id: str, iteration: IterationResult) -> None: ...

class LoopGoalExecutor:
    """解析 + 执行 /loop + /goal"""
    def execute_with_loop_goal(
        self,
        task: str,
        agent_type: str,
        dispatch_fn: Callable[..., bool],
        project_root: str,
        loop_config: Optional[LoopConfig] = None,
        goal: Optional[Goal] = None,
    ) -> Dict[str, Any]: ...
```

### 2.3 决策流程

```text
trae --loop 5 --goal "fix-tests" --criteria "tests pass" --task "..."
  │
  ├─ argparse 解析：loop=5, goal="fix-tests", criteria=["tests pass"], task="..."
  │
  ├─ GoalRegistry.create_goal(description="fix-tests", criteria=["tests pass"])
  │   └─ 持久化到 .trae/goals/fix-tests.json
  │   └─ KarpathyEnforcer.verify_checkpoint("cp_goal_1", True, "goal defined")
  │
  ├─ LoopGoalExecutor.execute_with_loop_goal(...)
  │   │
  │   └─ for iteration in range(1, loop_config.max_iterations + 1):
  │       │
  │       ├─ IterationResult(iteration_no=iteration, started_at=now)
  │       │
  │       ├─ dispatch_fn(agent_type, task, ...)  # 一次 dispatch
  │       │   └─ dispatch_agent_v2(...)
  │       │       └─ CyberneticsBridge
  │       │           └─ PerformanceFingerprint
  │       │
  │       ├─ IterationResult.success = dispatch_result
  │       ├─ IterationResult.outputs = 收集执行产出（文件修改数 / 测试结果）
  │       ├─ IterationResult.finished_at = now
  │       │
  │       ├─ GoalRegistry.save_iteration(goal_id, iteration)
  │       │
  │       ├─ ConvergenceDetector.check(...)  # 连续 N 次无新产出？
  │       │   └─ True → break（收敛）
  │       │
  │       └─ GoalVerifier.check(goal, iteration)
  │           └─ all_criteria_met → break（达成）
  │
  ├─ GoalRegistry.update_goal(status=ACHIEVED/IN_PROGRESS/FAILED)
  ├─ KarpathyEnforcer.verify_checkpoint("cp_goal_2", True, "goal achieved")
  │
  └─ 返回 {goal_id, status, iterations, total_iterations, achieved_at}
```

### 2.4 CLI 集成

`scripts/trae_agent_dispatch_v2.py` `parse_arguments()` 增强：

```python
parser.add_argument('--loop', type=int, default=1, 
                    help='循环执行次数（默认 1 = 不循环；建议 1-50）')
parser.add_argument('--goal', type=str, default=None, 
                    help='目标 ID（kebab-case）')
parser.add_argument('--goal-desc', type=str, default=None, 
                    help='目标描述（创建新目标时必填）')
parser.add_argument('--criteria', action='append', default=[], 
                    help='验收标准（可多次传入）')
```

`dispatch_agent_v2()` 包装：

```python
def dispatch_agent_v2(agent_type, task, task_id=None, project_root=".", 
                      progress=None, cybernetics_enabled=True,
                      loop_config=None, goal=None):
    if loop_config and loop_config.max_iterations > 1:
        executor = LoopGoalExecutor(project_root=project_root)
        return executor.execute_with_loop_goal(
            task=task, agent_type=agent_type,
            dispatch_fn=lambda: _dispatch_inner(...),
            project_root=project_root,
            loop_config=loop_config, goal=goal,
        )
    # 原行为：单次 dispatch
    return _dispatch_inner(...)
```

### 2.5 持久化 Schema

**`{project_root}/.trae/goals/{goal_id}.json`**：

```json
{
  "goal_id": "fix-tests",
  "description": "修复所有单元测试",
  "success_criteria": ["所有测试通过", "无新增警告"],
  "status": "in_progress",
  "max_iterations": 5,
  "convergence_window": 3,
  "iterations": [
    {
      "iteration_no": 1,
      "success": true,
      "outputs": {"files_modified": 3, "tests_passed": 12, "tests_failed": 2},
      "started_at": "2026-06-05T10:30:00",
      "finished_at": "2026-06-05T10:32:15",
      "execution_time_seconds": 135.2,
      "error": null
    }
  ],
  "created_at": "2026-06-05T10:30:00",
  "updated_at": "2026-06-05T10:32:15"
}
```

### 2.6 收敛检测算法

```python
class ConvergenceDetector:
    """连续 N 次 iteration 无新产出 → 提前退出"""
    def is_converged(self, recent_iterations: List[IterationResult]) -> bool:
        if len(recent_iterations) < self.config.convergence_window:
            return False
        # 取最近 N 次 iteration 的 outputs
        recent = recent_iterations[-self.config.convergence_window:]
        # 比较产出指纹（文件修改数 + 测试结果数）
        fingerprints = [self._fingerprint(i.outputs) for i in recent]
        # 全部相同 → 收敛
        return len(set(fingerprints)) == 1
    
    def _fingerprint(self, outputs: Dict) -> str:
        """产出指纹（用于判断是否变化）"""
        if not outputs:
            return "empty"
        return f"{outputs.get('files_modified', 0)}|{outputs.get('tests_passed', 0)}|{outputs.get('tests_failed', 0)}"
```

### 2.7 验收标准校验

`GoalVerifier` 提供两种模式：

1. **关键词匹配（默认）**：每个 criterion 是字符串，检查 iteration.outputs 中是否包含
2. **可调用对象（高级）**：criterion 是 `callable(iteration) -> bool`

**默认实现**（关键词匹配）：
- `criterion = "所有测试通过"` → 检查 `outputs.tests_failed == 0`
- `criterion = "无新增警告"` → 检查 `outputs.warnings_count == 0`
- `criterion = "代码已提交"` → 检查 `outputs.git_committed == True`

**可扩展**：用户可继承 `GoalVerifier` 实现自定义逻辑。

---

## 三、文件变更清单

| 文件 | 类型 | 改动量 |
|------|------|--------|
| `scripts/loop_goal.py` | 新增 | ~600 行 |
| `scripts/trae_agent_dispatch_v2.py` | 修改 | +60 行（argparse + dispatch 包装） |
| `scripts/tests/test_loop_goal.py` | 新增 | ~500 行（20+ tests） |

**V2 文件零修改**：仅调用 `dispatch_agent_v2` 接口。

---

## 四、向后兼容保证

| 旧调用方 | 新行为 |
|---------|--------|
| `--task X`（无 --loop / --goal） | 完全等同 Phase 10 行为 |
| `--loop 1` | 等同不传 --loop（max_iterations=1 → 不循环） |
| `--goal "x"` 不传 --goal-desc | 尝试读取现有目标；不存在则报错 |
| 旧测试（715+ tests） | 零修改，零回归 |

**零破坏性变更** ✅

---

## 五、测试计划（20+ tests）

| # | 测试名 | 验证点 |
|---|--------|--------|
| 1 | `test_goal_creation` | Goal 字段正确 |
| 2 | `test_goal_registry_persistence` | create → save → load 完整路径 |
| 3 | `test_goal_registry_list_filter` | 按 status 过滤 |
| 4 | `test_goal_status_lifecycle` | ACTIVE → IN_PROGRESS → ACHIEVED |
| 5 | `test_loop_config_validation` | max_iterations 范围 [1, 100] |
| 6 | `test_iteration_result_to_dict` | 序列化字段完整 |
| 7 | `test_convergence_detector_no_convergence` | 3 次不同产出 → 不收敛 |
| 8 | `test_convergence_detector_converged` | 3 次相同产出 → 收敛 |
| 9 | `test_convergence_window_below_threshold` | 2 次相同产出（窗口 3）→ 不收敛 |
| 10 | `test_goal_verifier_keyword_match` | "tests pass" + tests_failed=0 → 通过 |
| 11 | `test_goal_verifier_all_criteria_met` | 多 criteria 全部通过 |
| 12 | `test_goal_verifier_partial_fail` | 部分 criterion 不满足 |
| 13 | `test_loop_goal_executor_single_iteration` | max=1 → 1 次 dispatch |
| 14 | `test_loop_goal_executor_max_iterations` | max=5 → 5 次 dispatch（mock） |
| 15 | `test_loop_goal_executor_convergence_exit` | 第 4 次收敛 → 提前退出 |
| 16 | `test_loop_goal_executor_success_exit` | 第 2 次达标 → 提前退出 |
| 17 | `test_loop_goal_executor_no_goal` | 仅 /loop 无 /goal → 正常运行 |
| 18 | `test_loop_goal_executor_iteration_persistence` | 每次 iteration 落盘 |
| 19 | `test_cli_parse_loop_argument` | --loop 5 正确解析 |
| 20 | `test_cli_parse_goal_and_criteria` | --goal x --criteria a --criteria b |
| 21 | `test_dispatch_with_loop_wrapper` | 端到端：mock dispatch + 3 次 loop |
| 22 | `test_goal_registry_atomic_write` | 写入失败时回滚（不破坏） |
| 23 | `test_invalid_goal_id_raises` | goal_id 不符合 kebab-case → 报错 |
| 24 | `test_goal_status_transitions_invalid` | 非法状态转换 → 抛异常 |

**合计**：24 个测试

---

## 六、风险与回滚

| 风险 | 应对 |
|------|------|
| 循环死循环（用户传 --loop 1000） | `max_iterations` 硬上限 100（CLI 层校验） |
| 持久化文件冲突（并发写） | 临时文件 + os.replace 原子写 |
| 验收标准匹配失败 → 永远 ACHIEVED=False | 状态显式区分 IN_PROGRESS / FAILED |
| dispatch 失败时 iteration 状态 | IterationResult.error 字段记录；status=FAILED 不再继续 |
| 旧的 dispatch 调用未升级 | `--loop 1` 默认值兼容所有旧调用 |

**回滚策略**：所有改动为 additive（新模块、新参数、可选注入），删除 `loop_goal.py` 即可回滚。

---

## 七、验收标准

- [ ] 24 个新测试 100% 通过
- [ ] V1/V2 文件零修改
- [ ] 旧测试零回归（715+ tests）
- [ ] `--loop` / `--goal` / `--criteria` 三个参数解析正确
- [ ] 持久化文件可读、可写、可恢复
- [ ] 收敛检测在 3 次相同产出时触发
- [ ] Karpathy `cp_goal_1` / `cp_goal_2` 验证联动
