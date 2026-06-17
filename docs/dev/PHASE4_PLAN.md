# Dynamic Workflows Phase 4 实施计划

**日期**：2026-06-03
**前序**：[PHASE3_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE3_FINAL_REPORT.md)（460 tests 通过）
**依据**：[DYNAMIC_WORKFLOWS_INTEGRATION.md v1.1](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md) + [PHASE3_FINAL_REPORT.md §3.4 集成点预留](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE3_FINAL_REPORT.md)

---

## 一、范围与目标

### Phase 4 范围

将 Phase 3 独立实现的 **ModelRouter** + **TokenBudgetGuard** 集成到 **PatternExecutor** 的 `_dispatch_subagent` 中，实现端到端的 subagent 调度：
- 路由决策 → 选中模型层级（haiku/sonnet/opus）
- Token 预算 → 强制上限 + 软超限降级建议
- 沙箱隔离 → Phase 2 的 SubagentSandbox（已集成）
- 画像反哺 → 路由决策 + 预算消耗记录

### 必须遵守的硬约束（架构师审查 §3.0 + Phase 1+2+3 沉淀）

| # | 约束 | 实施策略 |
|---|------|---------|
| 1 | 🔴 向后兼容 | 现有调用方（不传 router/budget_guard）行为零变化；走 Phase 1+2 路径 |
| 2 | 🔴 V2 不修改 | 不修改 V2 文件；通过 `dispatch_agent_v2` 调用时透传 `model_tier` |
| 3 | 🔴 一阶段一模块 | Phase 4 仅做"端到端集成"，不引入新功能（SkillDistribution / /loop+/goal 留到 Phase 5） |
| 4 | 🔴 安全 | router/budget_guard 异常 → 降级到默认（sonnet + 100k 预算） |
| 5 | 🔴 持久化 | 路由决策 + 预算消费 → PerformanceFingerprint（已实现，只需调用） |

### 核心问题与解法

1. **`dispatch_agent_v2` 不支持 `model_tier` 参数**
   - 解法：将 `model_tier` 写入 task dict 的 `_meta.model_tier` 字段；Phase 5 可在 cybernetics_bridge 中解析
   - 备选：当前 Phase 4 记录路由决策到画像，但**不实际切换模型**；Phase 5+ 引入 model_tier-aware dispatch
2. **Token 预算与沙箱的关系**
   - 解法：Phase 2 沙箱已有 token_budget 字段（来自 `subagent_sandbox.py`）；Phase 4 复用并可选覆盖
3. **路由决策的"使用"语义**
   - 解法：决策通过 task dict 透传给 dispatch_agent_v2（meta.model_tier）；失败时降级
4. **测试覆盖**
   - 解法：mock dispatch_agent_v2 + PerformanceFingerprint + Router + BudgetGuard 全套模拟

---

## 二、模块改动设计

### 2.1 `_dispatch_subagent` 扩展（核心）

**当前签名**：
```python
def _dispatch_subagent(
    agent_type: str,
    task: Any,
    task_id: Optional[str] = None,
    sandbox: Optional[Any] = None,
) -> bool:
```

**Phase 4 扩展后签名**：
```python
def _dispatch_subagent(
    agent_type: str,
    task: Any,
    task_id: Optional[str] = None,
    sandbox: Optional[Any] = None,
    router: Optional[Any] = None,         # ModelRouter（Phase 4）
    budget_guard: Optional[Any] = None,   # TokenBudgetGuard（Phase 4）
) -> bool:
```

**执行流程**：
```
1. 向后兼容检查：sandbox/router/budget_guard 都为 None → 走 Phase 1 _safe_dispatch

2. [Phase 4] 路由决策（如果 router 不为 None）
   - 从 task 提取 TaskFeature（_extract_task_feature）
   - router.route(feature) → RoutingDecision
   - decision.selected_tier / decision.reasoning 记录到日志
   - 将 model_tier 写入 task._meta（供 dispatch_agent_v2 后续消费）

3. [Phase 4] Token 预算预检（如果 budget_guard 不为 None）
   - 从 task 提取 token_budget（_extract_token_budget）
   - budget_guard.create_budget() + pre_execute_check()
   - 决策 = REJECT → 抛 DispatchError
   - 决策 = SOFT warning → 记录 warning，继续

4. [Phase 2] 沙箱执行（如果 sandbox 不为 None）
   - sandbox.spawn() → sandbox.execute() → sandbox.cleanup()
   - 沙箱内记录 token 消费
   - 异常隔离（TokenBudgetExceeded → False, GuardRejectError → DispatchError）

5. [Phase 1] 默认路径
   - _safe_dispatch() 直接调用 dispatch_agent_v2

6. [Phase 4] 后审（如果 budget_guard 不为 None）
   - budget_guard.post_execute_review() 写入画像

7. [Phase 4] 路由反哺（如果 router 不为 None）
   - router.record_decision() 写入画像
```

### 2.2 3 个执行器扩展

**当前签名**：
```python
class ClassifierDispatchExecutor:
    def __init__(self, fingerprint=None, sandbox=None): ...

class FanOutAggregateExecutor:
    def __init__(self, fingerprint=None, max_workers=10, sandbox=None): ...

class AdversarialVerifyExecutor:
    def __init__(self, fingerprint=None, sandbox=None): ...
```

**Phase 4 扩展后**：
```python
class ClassifierDispatchExecutor:
    def __init__(self, fingerprint=None, sandbox=None, router=None, budget_guard=None): ...

class FanOutAggregateExecutor:
    def __init__(self, fingerprint=None, max_workers=10, sandbox=None, router=None, budget_guard=None): ...

class AdversarialVerifyExecutor:
    def __init__(self, fingerprint=None, sandbox=None, router=None, budget_guard=None): ...
```

每个执行器内的 `_dispatch_subagent` 调用新增 `router` 和 `budget_guard` 透传。

### 2.3 PatternExecutorRegistry.create_default 扩展

**当前签名**：
```python
@classmethod
def create_default(cls, fingerprint=None, sandbox=None) -> "PatternExecutorRegistry":
```

**Phase 4 扩展后**：
```python
@classmethod
def create_default(
    cls,
    fingerprint: Optional[PerformanceFingerprint] = None,
    sandbox: Optional[Any] = None,
    router: Optional[Any] = None,         # Phase 4
    budget_guard: Optional[Any] = None,   # Phase 4
) -> "PatternExecutorRegistry":
```

### 2.4 execute_workflow_step 透传

`execute_workflow_step` 不直接接受 `router` / `budget_guard`，而是从 `registry` 推断：
```python
def execute_workflow_step(step, instance=None, registry=None) -> Optional[ExecutionResult]:
    # registry 内置 router / budget_guard（来自 create_default）
    # 透传给 execute_pattern
    ...
```

`execute_pattern` 接受 `registry`；registry 内部已经绑定了 router / budget_guard。

### 2.5 新增辅助函数

```python
def _extract_task_feature(task: Any) -> TaskFeature:
    """
    从 task dict 提取 TaskFeature

    支持字段：
    - task_complexity (1-10)
    - estimated_tokens
    - role
    - deadline_ms
    - quality_threshold
    - budget_remaining
    - is_critical
    - task_type

    找不到时使用合理默认值
    """
    task_dict = task if isinstance(task, dict) else {"description": str(task)}
    return TaskFeature(
        task_complexity=task_dict.get("task_complexity", 5),
        estimated_tokens=task_dict.get(
            "estimated_tokens",
            task_dict.get("token_budget", 5000) // 4,  # 默认预估算
        ),
        role=task_dict.get("role"),
        deadline_ms=task_dict.get("deadline_ms"),
        quality_threshold=task_dict.get("quality_threshold", 0.85),
        budget_remaining=task_dict.get("budget_remaining", 1.0),
        is_critical=task_dict.get("is_critical", False),
        task_type=task_dict.get("task_type", "general"),
    )
```

---

## 三、测试用例设计

### 3.1 Phase 4 集成测试（≥ 15 用例）

| 测试类 | 覆盖范围 | 用例数 |
|--------|---------|--------|
| TestPhase4BackwardCompat | 不传 router/budget_guard → Phase 1 行为 | 3 |
| TestPhase4RouterIntegration | routing 决策被使用 / 记录 | 3 |
| TestPhase4BudgetGuardIntegration | budget 预检 / 消费 / 后审 | 3 |
| TestPhase4FullIntegration | sandbox + router + budget 协同 | 2 |
| TestPhase4ErrorPaths | router 异常 / budget 异常 / 沙箱异常 | 2 |
| TestPhase4RegistryCreation | PatternExecutorRegistry.create_default 接受新参数 | 2 |

**合计：15 tests**

### 3.2 Phase 1+2+3 回归（0 修改）

- test_pattern_executor：53 tests
- test_worktree_manager：42 tests
- test_subagent_sandbox：43 tests
- test_model_router：46 tests
- test_token_budget_guard：50 tests

---

## 四、交付清单

| # | 产物 | 路径 | 状态 |
|---|------|------|------|
| 1 | `_dispatch_subagent` 扩展 | `scripts/dynamic_workflow/pattern_executor.py` | 待实施 |
| 2 | 3 个执行器扩展 | `scripts/dynamic_workflow/pattern_executor.py` | 待实施 |
| 3 | `PatternExecutorRegistry.create_default` 扩展 | `scripts/dynamic_workflow/pattern_executor.py` | 待实施 |
| 4 | `_extract_task_feature` 辅助函数 | `scripts/dynamic_workflow/pattern_executor.py` | 待实施 |
| 5 | `execute_workflow_step` 透传 | `scripts/dynamic_workflow/workflow_step_adapter.py` | 待实施 |
| 6 | Phase 4 集成测试 | `tests/test_pattern_executor_phase4.py` | 待实施 |
| 7 | 测试入口更新 | `tests/scripts/run_dynamic_workflow_tests.sh` | 待实施 |
| 8 | Phase 4 收官报告 | `docs/dev/PHASE4_FINAL_REPORT.md` | 待实施 |

**预期测试增量**：15 tests
**全量测试预期**：460 + 15 = **475 tests**

---

## 五、验收清单

- [ ] `_dispatch_subagent` 支持 router + budget_guard
- [ ] 3 个执行器接受并透传 router + budget_guard
- [ ] `PatternExecutorRegistry.create_default` 接受新参数
- [ ] 15 个 Phase 4 集成测试 100% 通过
- [ ] Phase 1+2+3 回归测试零失败
- [ ] V2 回归测试零失败
- [ ] V2 文件零修改（`git diff` 为空）
- [ ] 向后兼容：不传 router/budget_guard 行为零变化
- [ ] 性能基线：`_dispatch_subagent` (无 router/budget) < 10ms
- [ ] TODO/FIXME 0 处遗留
- [ ] 编译警告 0 处

---

## 六、回滚策略

如 Phase 4 出现问题：
1. 恢复 `pattern_executor.py` 和 `workflow_step_adapter.py` 的修改
2. 删除新增测试文件 `test_pattern_executor_phase4.py`
3. 不影响 Phase 1+2+3 任何代码

---

*下一步：用户确认 → 启动 Phase 4 实施*
