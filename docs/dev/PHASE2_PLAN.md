# Dynamic Workflows Phase 2 实施计划

**日期**：2026-06-03
**前序**：[PHASE1_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE1_FINAL_REPORT.md)（194 + 85 测试通过）
**依据**：[DYNAMIC_WORKFLOWS_INTEGRATION.md v1.1 §七.Phase 2](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md) + 架构师审查 §3、§6

---

## 一、范围与目标

### Phase 2 范围

实现 **Subagent 沙箱** 与 **Worktree 隔离** 能力，让 PatternExecutor 中的 subagent 拥有物理隔离的执行环境。

### 必须遵守的硬约束（架构师审查 §10 Top 5）

| # | 约束 | 实施策略 |
|---|------|---------|
| 1 | 🔴 持久化复用 | 不新建并行存储；subagent 沙箱元数据写入 `PerformanceFingerprint` 的 execution_record |
| 2 | 🔴 V2 不修改 | 完全不修改任何 V2 文件；通过 `register_sandbox()` 扩展点注入 |
| 3 | 🔴 Phase 拆分 | Phase 2 仅做 WorktreeManager + SubagentSandbox，ModelRouter / TokenBudgetGuard 留到 Phase 3 |
| 4 | 🔴 安全补强 | subagent 输入先经 Guard 校验；worktree 路径白名单；异常隔离 |
| 5 | 🔴 提示词注入防护 | SubagentSandbox.spawn() 强制 Guard 校验（不绕过） |

### 必须解决的关键问题

1. **V2 不修改** 与 **subagent context 隔离** 的矛盾
   - 解法：SubagentSandbox 是"包装层"，内部仍调用 DualLayerContextManager 但传入 task_instance_id 区分，不动 V2
2. **git worktree 在非 Git 环境的可用性**
   - 解法：自动检测 `git` 可用性 + `is_git_repo()` 校验；不可用时降级为 `isolation_level="none"`
3. **Token 预算"硬上限"**
   - 解法：SubagentSandbox 在 execute() 入口校验 + 在执行过程中检查，超限抛 `TokenBudgetExceeded`

---

## 二、模块设计

### 2.1 WorktreeManager（独立模块）

**路径**：`scripts/dynamic_workflow/worktree_manager.py`

**职责**：
- 封装 `git worktree add` / `remove` / `list` 命令
- 路径白名单（禁止 worktree 创建在 `/` / `~` / 项目外）
- 自动清理（finally 块）
- 跨平台（macOS / Linux 优先，Windows 降级）

**关键类**：

```python
class WorktreeManager:
    """
    worktree 隔离管理器

    核心职责：
    1. 为每个 subagent 创建独立 worktree
    2. 路径白名单校验（防止越权创建）
    3. 自动清理（finally 块）
    4. 降级策略：非 Git 环境返回 None（调用方处理）
    """

    def __init__(self, base_path: str, allow_paths: List[str]):
        """
        Args:
            base_path: worktree 父目录（如 .dw_worktrees/）
            allow_paths: 允许的根路径白名单（防越权）
        """

    def create(self, agent_id: str, base_branch: str = "main") -> Optional[WorktreeInfo]:
        """创建 worktree；非 Git 环境返回 None"""

    def remove(self, worktree_path: str) -> bool:
        """移除 worktree"""

    def list_active(self) -> List[WorktreeInfo]:
        """列出所有活跃 worktree"""

    def cleanup_all(self) -> int:
        """清理所有 worktree（异常路径）"""
```

**关键数据结构**：

```python
@dataclass
class WorktreeInfo:
    """worktree 元数据"""
    worktree_id: str           # 唯一 ID（如 wt_a1b2c3d4）
    agent_id: str              # 所属 subagent
    worktree_path: str         # 绝对路径
    base_branch: str           # 来源分支
    created_at: str            # ISO 时间
    git_available: bool        # Git 是否可用
```

### 2.2 SubagentSandbox（独立模块）

**路径**：`scripts/dynamic_workflow/subagent_sandbox.py`

**职责**：
- 提供"独立 worktree + 独立 context + Token 预算"的 subagent 执行环境
- 强制 Guard 校验
- 异常隔离（一个 subagent 失败不影响父）
- 生命周期管理（spawn → execute → cleanup）

**关键类**：

```python
class SubagentSandbox:
    """
    subagent 执行沙箱

    核心能力：
    1. worktree 隔离（可选，通过 isolation_level 控制）
    2. context 隔离（通过 task_instance_id 区分）
    3. Token 预算硬上限（执行中实时检查）
    4. Guard 强制校验（不可绕过）
    5. 异常隔离（finally 块清理 worktree）
    """

    # 隔离级别
    ISOLATION_NONE = "none"           # 无隔离（顺序 subagent）
    ISOLATION_CONTEXT = "context"     # 仅 context 隔离
    ISOLATION_WORKTREE = "worktree"   # worktree 隔离
    ISOLATION_FULL = "full"           # worktree + context 双重隔离

    def __init__(self, worktree_manager, fingerprint, guard=None):
        ...

    def spawn(self, agent_id: str, task: Dict, isolation_level: str = "context",
              token_budget: int = 10000) -> str:
        """
        创建 subagent 沙箱

        Returns:
            str: sandbox_id

        Raises:
            GuardRejectError: 输入校验失败
            TokenBudgetExceeded: token 预算超限
        """

    def execute(self, sandbox_id: str, executor: Callable) -> SandboxResult:
        """
        在沙箱中执行任务

        Args:
            sandbox_id: spawn 返回的 ID
            executor: 实际执行函数（接收 SandboxContext）

        Returns:
            SandboxResult: 包含 status/output/token_used

        异常处理：
        - TokenBudgetExceeded → 优雅降级
        - 其他异常 → 隔离，不传播给父
        """

    def cleanup(self, sandbox_id: str) -> bool:
        """
        清理沙箱（移除 worktree + 释放 context）
        """

    def cleanup_all(self) -> int:
        """
        清理所有活跃沙箱（异常路径）
        """
```

**关键数据结构**：

```python
@dataclass
class SandboxContext:
    """沙箱执行上下文（传给 executor）"""
    sandbox_id: str
    agent_id: str
    isolation_level: str
    worktree_path: Optional[str]
    context_instance_id: Optional[str]   # task_instance_id for DualLayerContextManager
    token_used: int
    token_budget: int
    created_at: str

@dataclass
class SandboxResult:
    """沙箱执行结果"""
    sandbox_id: str
    status: str          # success / failure / rejected / token_exceeded / timeout
    output: Any
    token_used: int
    execution_time_seconds: float
    error: Optional[str]
    worktree_cleaned: bool
    isolated: bool       # 是否被异常隔离

class TokenBudgetExceeded(Exception):
    """Token 预算硬上限异常"""
    pass
```

### 2.3 与 PatternExecutor 的集成

**集成点**：`scripts/dynamic_workflow/pattern_executor.py`

**集成方式**（不修改 V2 文件）：

```python
# 现有：直接调用 _safe_dispatch
def _safe_dispatch(agent_type, task, task_id=None) -> bool:
    return dispatch_agent_v2(...)

# 新增：可选的 sandbox 包装
def _safe_dispatch_with_sandbox(sandbox, agent_id, agent_type, task, task_id=None) -> bool:
    """
    在沙箱中调用 subagent
    - 校验 → execute() → cleanup()
    - 异常隔离
    """
    sandbox_id = sandbox.spawn(agent_id, task, isolation_level="context")
    try:
        result = sandbox.execute(sandbox_id, lambda ctx: dispatch_agent_v2(...))
        return result.success
    finally:
        sandbox.cleanup(sandbox_id)
```

**使用方式**：

```python
# 在 PatternExecutor.__init__ 中可选接收 sandbox
class FanOutAggregateExecutor:
    def __init__(self, fingerprint, sandbox=None, max_workers=5):
        self.sandbox = sandbox  # 可选
        ...

    def execute(self, task, parameters):
        # 如果 sandbox 存在，每个 subagent 都跑在独立 context
        # 如果 sandbox 不存在，沿用旧的 _safe_dispatch 路径
        if self.sandbox:
            return self._execute_with_sandbox(task, parameters)
        else:
            return self._execute_legacy(task, parameters)
```

**关键设计原则**：
- **向后兼容**：没有 sandbox 时，行为与 Phase 1 完全一致
- **可选启用**：调用方显式传入 sandbox 才启用隔离
- **不修改 V2**：所有改动在 PatternExecutor 内部

---

## 三、测试用例设计

### 3.1 WorktreeManager 测试（tests/test_worktree_manager.py）

| # | 用例 | 覆盖点 |
|---|------|--------|
| W01 | 创建 worktree（Git 环境） | git worktree add 成功 |
| W02 | 非 Git 环境 → 返回 None | 降级策略 |
| W03 | 路径白名单校验 | 不在白名单 → WorktreePathError |
| W04 | 重复创建同名 → WorktreeAlreadyExistsError | 冲突检测 |
| W05 | remove 成功 | git worktree remove |
| W06 | list_active 正确返回 | 状态查询 |
| W07 | cleanup_all 清理所有 | 异常路径 |
| W08 | 并发安全：同一 agent_id 同时 create | 锁机制 |
| W09 | 无效 base_branch → 失败 | 参数校验 |
| W10 | 创建超时（5s 未返回）→ WorktreeTimeoutError | 熔断 |

### 3.2 SubagentSandbox 测试（tests/test_subagent_sandbox.py）

| # | 用例 | 覆盖点 |
|---|------|--------|
| S01 | spawn 成功返回 sandbox_id | 基本流程 |
| S02 | spawn 触发 Guard REJECT → GuardRejectError | 强制 Guard |
| S03 | spawn 触发 Token 超限 → TokenBudgetExceeded | 硬上限 |
| S04 | execute 成功返回 SandboxResult | 执行路径 |
| S05 | execute 中 executor 抛异常 → 隔离，返回 isolated=True | 异常隔离 |
| S06 | execute 中 Token 耗尽 → graceful_degrade | 降级策略 |
| S07 | cleanup 移除 worktree + 释放 context | 资源回收 |
| S08 | cleanup_all 清理所有活跃沙箱 | 异常路径 |
| S09 | isolation_level="worktree" 创建 worktree | 隔离级别 |
| S10 | isolation_level="none" 不创建 worktree | 隔离级别 |
| S11 | isolation_level="full" 双重隔离 | 隔离级别 |
| S12 | context_instance_id 唯一性 | 多实例隔离 |
| S13 | 并发 5 个 sandbox，无冲突 | 并发安全 |
| S14 | worktree 路径不在白名单 → 拒绝 | 路径白名单 |
| S15 | fingerprint 记录沙箱创建/清理 | 画像反哺 |
| S16 | SandboxResult.to_dict 序列化完整 | 可观测性 |
| S17 | 沙箱创建超时 → WorktreeTimeoutError | 熔断 |
| S18 | 重复 cleanup 同一 sandbox → 幂等 | 幂等性 |

### 3.3 PatternExecutor × Sandbox 集成测试（追加到 test_pattern_executor.py）

| # | 用例 | 覆盖点 |
|---|------|--------|
| P01 | FanOutAggregateExecutor 启用 sandbox → 5 个 subagent 各自有 context_instance_id | 集成 |
| P02 | Sandbox 内 Guard 拒绝 → 该 subagent 失败但其他继续 | 隔离 + 容错 |
| P03 | Sandbox 异常 → SubagentResult.isolated=True | 异常隔离 |
| P04 | Sandbox 清理时 worktree 路径正确移除 | 资源回收 |
| P05 | 不传 sandbox 时行为与 Phase 1 完全一致 | 向后兼容 |

### 3.4 混沌测试（test_subagent_sandbox_chaos.py）

| # | 用例 | 覆盖点 |
|---|------|--------|
| C01 | worktree 创建失败（磁盘满） → 优雅降级 | IO 故障 |
| C02 | executor 死循环 → 沙箱超时强制结束 | 死循环 |
| C03 | 10 个并发 sandbox，部分失败部分成功 | 并发故障 |
| C04 | 进程被 kill -9 → 沙箱残留清理 | 进程崩溃 |
| C05 | Token 预算耗尽瞬间 executor 还在写 → 不溢出 | 资源边界 |

---

## 四、性能基线

| 操作 | 基线 | 说明 |
|------|------|------|
| spawn（无 worktree） | < 50ms | 仅 Guard + 状态记录 |
| spawn（含 worktree） | < 1000ms | git worktree 通常 100-500ms |
| execute（mock executor） | < 100ms | 不含真实 subagent |
| cleanup | < 500ms | git worktree remove |
| 5 并发 sandbox | < 2000ms | 并发吞吐 |

---

## 五、交付清单

| 路径 | 类型 | 行数目标 |
|------|------|---------|
| `scripts/dynamic_workflow/worktree_manager.py` | 实现 | ~250 行 |
| `scripts/dynamic_workflow/subagent_sandbox.py` | 实现 | ~400 行 |
| `scripts/dynamic_workflow/pattern_executor.py` | 改动 | +100 行（sandbox 集成） |
| `scripts/tests/test_worktree_manager.py` | 测试 | ~200 行（10+ 用例） |
| `scripts/tests/test_subagent_sandbox.py` | 测试 | ~300 行（18+ 用例） |
| `scripts/tests/test_pattern_executor.py` | 追加 | +80 行（5+ 集成用例） |
| `scripts/tests/test_subagent_sandbox_chaos.py` | 测试 | ~150 行（5+ 混沌用例） |
| `docs/dev/PHASE2_FINAL_REPORT.md` | 文档 | 收官报告 |

**总计**：~1480 行（实现 ~750 + 测试 ~730）

---

## 六、风险与回滚

### 风险

| 风险 | 应对 |
|------|------|
| git worktree 在某些环境失败 | 降级为 isolation_level="context" |
| Worktree 残留（崩溃路径） | SubagentSandbox 启动时调用 cleanup_all 扫描 |
| Token 估算偏差大 | 提供 token_used 实时反馈，调整预算 |
| 并发 sandbox 锁竞争 | WorktreeManager 内部用 threading.Lock |

### 回滚策略

- **代码回滚**：删除 `worktree_manager.py` / `subagent_sandbox.py`，PatternExecutor 移除 sandbox 分支
- **数据回滚**：沙箱元数据写入 PerformanceFingerprint 的 execution_record，删除 sandbox 记录不会影响其他数据
- **V2 影响**：零（所有改动在 dynamic_workflow/ 目录内）

---

## 七、Phase 2 验收标准

- [ ] WorktreeManager 单元测试 10+ 用例，100% 通过
- [ ] SubagentSandbox 单元测试 18+ 用例，100% 通过
- [ ] 集成测试 5+ 用例，100% 通过
- [ ] 混沌测试 5+ 用例，100% 通过
- [ ] V2 回归测试零失败（7 个 V2 套件）
- [ ] Phase 1 回归测试零失败（194 个测试）
- [ ] 性能基线达标（spawn < 1000ms）
- [ ] V2 文件未修改（`git diff scripts/*.py` 为空）
- [ ] worktree 路径白名单生效（越权创建失败）
- [ ] Guard 强制校验（无法绕过）
- [ ] Token 预算硬上限（超限抛 TokenBudgetExceeded）
- [ ] 异常隔离（executor 异常不传播）
- [ ] 安全/性能/可靠性分析完成

---

*Phase 2 计划版本：v1.0（基于 v1.1 主方案 + 架构师审查修订）*
