# Dynamic Workflows Phase 3 实施计划

**日期**：2026-06-03
**前序**：[PHASE2_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE2_FINAL_REPORT.md)（364 tests 通过）
**依据**：[DYNAMIC_WORKFLOWS_INTEGRATION.md v1.1 §七.Phase 3](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md)

---

## 一、范围与目标

### Phase 3 范围

实现 **ModelRouter**（基于 subagent 能力 / 成本的任务路由）与 **TokenBudgetGuard**（执行期 Token 监控 + 自动降级），让 PatternExecutor 中的 subagent 拥有资源动态调度能力。

### 必须遵守的硬约束（架构师审查 §3.0 + Phase 2 沉淀）

| # | 约束 | 实施策略 |
|---|------|---------|
| 1 | 🔴 持久化复用 | 路由决策历史 / Token 消耗记录写入 `PerformanceFingerprint.execution_record` |
| 2 | 🔴 V2 不修改 | 不修改 V2 文件；通过 `register_router()` / `register_budget_guard()` 扩展点注入 |
| 3 | 🔴 Phase 拆分 | Phase 3 仅做 ModelRouter + TokenBudgetGuard，其余能力（Skill 分发、/loop + /goal）留到 Phase 4 |
| 4 | 🔴 安全补强 | Token 硬上限（不是软警告）；路由决策可解释；降级策略可审计 |
| 5 | 🔴 提示词注入防护 | 任务特征提取做 schema 校验（不允许任务描述直接决定模型） |

### 必须解决的关键问题

1. **V2 不修改** 与 **subagent 模型路由** 的矛盾
   - 解法：ModelRouter 是"装饰层"，内部调用 dispatch_agent_v2 时通过 model_id 字段透传；不修改 V2
2. **Token 失控** 的工程化防护
   - 解法：TokenBudgetGuard 在任务启动前 + 执行中 + 完成时三阶段校验；超限触发"切换 haiku 继续"而非中断
3. **路由决策可解释性**
   - 解法：每个路由决策返回 `RoutingDecision` 数据类，包含 reasoning 字段（中文），写入画像
4. **画像冷启动**（无历史数据时）
   - 解法：使用默认决策表（基于 task_complexity 静态规则），同时记录新数据为后续决策提供依据

---

## 二、模块设计

### 2.1 ModelRouter（独立模块）

**路径**：`scripts/dynamic_workflow/model_router.py`

**职责**：
- 根据任务特征（复杂度 / 角色 / Token 预算 / 截止时间）选择最合适的模型
- 路由决策可解释（返回决策理由）
- 路由历史写入 `PerformanceFingerprint`
- 冷启动降级（无历史数据时使用静态决策表）

**关键类**：

```python
class ModelTier(str, Enum):
    """模型层级枚举"""
    HAIKU  = "haiku"   # 轻量：低成本、低延迟、低质量
    SONNET = "sonnet"  # 平衡：标准成本、中等延迟、中等质量
    OPUS   = "opus"    # 重量：高成本、高延迟、高质量

@dataclass
class ModelProfile:
    """模型画像：成本 / 质量 / 速度"""
    tier: ModelTier
    cost_per_1k_tokens: float  # 每 1k token 成本（相对值）
    quality_score: float       # 质量分 (0-1)
    speed_score: float         # 速度分 (0-1, 越大越快)
    max_context_tokens: int    # 最大上下文 token
    description: str           # 适用场景

@dataclass
class TaskFeature:
    """任务特征（路由决策输入）"""
    task_complexity: int                  # 复杂度 1-10
    estimated_tokens: int                 # 预计 token 消耗
    role: Optional[str] = None            # 角色（架构师/产品/...）
    deadline_ms: Optional[int] = None     # 截止时间（毫秒）
    quality_threshold: float = 0.85       # 质量阈值
    budget_remaining: float = 1.0         # 预算剩余比例 (0-1)
    is_critical: bool = False             # 是否关键任务

@dataclass
class RoutingDecision:
    """路由决策（带可解释性）"""
    selected_tier: ModelTier
    confidence: float                     # 决策置信度 0-1
    reasoning: str                        # 决策理由（中文）
    alternatives: List[ModelTier]         # 备选方案
    feature_snapshot: Dict[str, Any]      # 决策时的特征快照

class ModelRouter:
    def __init__(self, fingerprint=None, custom_profiles=None): ...
    def route(self, feature: TaskFeature) -> RoutingDecision: ...
    def record_decision(self, decision, actual_outcome=None): ...  # 写入画像
    def get_profiles(self) -> Dict[ModelTier, ModelProfile]: ...
```

**决策算法（默认静态规则）**：

```python
def route(self, feature: TaskFeature) -> RoutingDecision:
    # 1. 极端 case：关键任务 → 必须 opus
    if feature.is_critical:
        return self._decide(OPUS, "关键任务强制使用 opus", 0.95)

    # 2. 预算耗尽（< 10%）→ 强制 haiku
    if feature.budget_remaining < 0.1:
        return self._decide(HAIKU, "预算耗尽，强制使用 haiku", 0.90)

    # 3. 截止时间紧（< 5s）+ 质量阈值 < 0.8 → sonnet
    if feature.deadline_ms and feature.deadline_ms < 5000 and feature.quality_threshold < 0.8:
        return self._decide(SONNET, "截止时间紧且质量阈值宽松", 0.85)

    # 4. 基于复杂度分级
    if feature.task_complexity <= 3:
        return self._decide(HAIKU, "低复杂度任务，haiku 即可", 0.80)
    elif feature.task_complexity <= 6:
        return self._decide(SONNET, "中等复杂度任务，sonnet 平衡", 0.80)
    else:
        return self._decide(OPUS, "高复杂度任务，opus 必需", 0.80)
```

**画像反哺**（冷启动优化）：

- 当 `PerformanceFingerprint.records` 超过 10 条时启用历史检索
- 检索"同复杂度 + 同角色"的历史决策 → 取最近成功的 model_tier
- 加权：历史权重 0.6 + 静态规则权重 0.4

### 2.2 TokenBudgetGuard（独立模块）

**路径**：`scripts/dynamic_workflow/token_budget_guard.py`

**职责**：
- 三阶段 Token 校验：pre_execute / during_execute / post_execute
- 超限触发降级（切换 haiku）而非中断
- 与 GuardCoordinator 兼容（`validate()` 接口对齐）
- 降级历史写入 `PerformanceFingerprint`

**关键类**：

```python
class BudgetEnforcementMode(str, Enum):
    """预算执行模式"""
    HARD   = "hard"    # 硬上限：超限立即抛 TokenBudgetExceeded
    SOFT   = "soft"    # 软上限：超限警告 + 切换 haiku 继续
    HYBRID = "hybrid"  # 混合：>= 100% → hard；>= 80% → soft

@dataclass
class TokenBudget:
    """Token 预算"""
    total_budget: int                    # 总预算
    consumed: int = 0                    # 已消耗
    reserved: int = 0                    # 预留（并行任务）
    soft_threshold: float = 0.8          # 软阈值（达到 80% 触发降级）
    hard_threshold: float = 1.0          # 硬阈值（达到 100% 触发中断）

    @property
    def consumption_ratio(self) -> float: ...

@dataclass
class BudgetDecision:
    """预算决策"""
    allow_continue: bool                 # 是否允许继续
    enforcement: BudgetEnforcementMode   # 执行模式
    recommendation: Optional[str]        # 建议（switch_to_haiku/split_task/abort）
    remaining: int                       # 剩余 token
    warnings: List[str]                  # 警告列表

class TokenBudgetExceeded(Exception):
    """Token 预算超限异常"""
    def __init__(self, consumed: int, budget: int): ...

class TokenBudgetGuard:
    def __init__(self, fingerprint=None, default_mode=HARD): ...
    def create_budget(self, total: int) -> TokenBudget: ...
    def pre_execute_check(self, budget, estimated_tokens) -> BudgetDecision: ...
    def record_consumption(self, budget, consumed) -> BudgetDecision: ...
    def post_execute_review(self, budget, success) -> None: ...
    def validate(self, task: Dict) -> "ValidationResult-like": ...  # 对齐 GuardCoordinator 接口
```

**三阶段校验逻辑**：

```python
# 阶段 1：pre_execute_check
if estimated_tokens > budget.total_budget * (1 - budget.soft_threshold):
    return BudgetDecision(
        allow_continue=True,  # 仍允许
        enforcement=SOFT,
        recommendation="switch_to_haiku",
        warnings=[f"预估将消耗 {estimated_tokens} tokens, 超过软阈值"],
    )
if estimated_tokens > budget.total_budget:
    return BudgetDecision(
        allow_continue=False,
        enforcement=HARD,
        recommendation="split_task",
        warnings=[f"预估 {estimated_tokens} 超过总预算 {budget.total_budget}"],
    )

# 阶段 2：record_consumption
new_consumed = budget.consumed + consumed
if new_consumed > budget.total_budget * budget.hard_threshold:
    raise TokenBudgetExceeded(new_consumed, budget.total_budget)
if new_consumed > budget.total_budget * budget.soft_threshold:
    return BudgetDecision(
        allow_continue=True,
        enforcement=SOFT,
        recommendation="switch_to_haiku",
    )

# 阶段 3：post_execute_review
fingerprint.record(
    task_type=...,
    strategy=budget.recommendation or "default",
    success=success,
    context_features={"tokens_consumed": budget.consumed, "total_budget": budget.total_budget},
)
```

**与 GuardCoordinator 集成**：

`TokenBudgetGuard.validate(task)` 返回与 `GuardCoordinator.validate(task)` 兼容的结果：
- `passed`: bool
- `risk_level`: RiskLevel
- `warnings`: List[ValidationWarning]
- `recommended_compensations`: List[CompensationStrategy]

---

## 三、测试用例

### 3.1 ModelRouter 测试（≥ 30 用例）

| 测试类 | 覆盖范围 | 用例数 |
|--------|---------|--------|
| TestModelProfile | tier / cost / quality / speed / max_context | 4 |
| TestTaskFeature | 默认值 / 必填字段 / 序列化 | 3 |
| TestRoutingDecision | 构造 / 字段 / 序列化 | 3 |
| TestModelRouterBasic | 3 个 tier 路由 / 决策理由 / 置信度 | 6 |
| TestModelRouterCriticalPath | is_critical / 预算耗尽 / 截止时间紧 | 5 |
| TestModelRouterComplexityRules | 1-3 / 4-6 / 7-10 三档 | 3 |
| TestModelRouterFingerprintIntegration | 写入画像 / 检索历史 / 加权决策 | 4 |
| TestModelRouterErrorPaths | 越界复杂度 / 负预算 / 负截止时间 | 3 |
| TestModelRouterConcurrency | 线程安全 / 并发路由 | 2 |
| TestModelRouterPerformance | 路由决策 < 10ms | 2 |

**合计：35 tests**

### 3.2 TokenBudgetGuard 测试（≥ 30 用例）

| 测试类 | 覆盖范围 | 用例数 |
|--------|---------|--------|
| TestTokenBudget | 消费比 / 软硬阈值 / 序列化 | 4 |
| TestBudgetDecision | 构造 / 字段 / 序列化 | 3 |
| TestTokenBudgetExceeded | 异常信息 / 字段 | 2 |
| TestGuardBasic | 创建预算 / 预检 / 消费记录 / 后审 | 6 |
| TestGuardEnforcement | HARD / SOFT / HYBRID 三模式 | 5 |
| TestGuardFingerprintIntegration | 写入画像 / 决策反哺 | 3 |
| TestGuardCoordinatorCompatibility | validate(task) 对齐 | 3 |
| TestGuardErrorPaths | 负预算 / 超额消费 / 非法操作 | 3 |
| TestGuardConcurrency | 线程安全 / 并发消费 | 2 |
| TestGuardPerformance | 校验 < 5ms | 2 |

**合计：33 tests**

### 3.3 集成测试（Phase 1+2 回归）

- `test_pattern_executor.py` 新增 2 个用例：路由决策透传 + Token 预算超限降级
- 验证 sandbox 路径下 ModelRouter / TokenBudgetGuard 协同工作

---

## 四、交付清单

| # | 产物 | 路径 | 状态 |
|---|------|------|------|
| 1 | ModelRouter 实现 | `scripts/dynamic_workflow/model_router.py` | 待实施 |
| 2 | TokenBudgetGuard 实现 | `scripts/dynamic_workflow/token_budget_guard.py` | 待实施 |
| 3 | ModelRouter 单元测试 | `tests/test_model_router.py` | 待实施 |
| 4 | TokenBudgetGuard 单元测试 | `tests/test_token_budget_guard.py` | 待实施 |
| 5 | 测试入口更新 | `tests/scripts/run_dynamic_workflow_tests.sh` | 待实施 |
| 6 | Phase 3 收官报告 | `docs/dev/PHASE3_FINAL_REPORT.md` | 待实施 |

**预期测试增量**：68 tests（35 + 33）
**全量测试预期**：364 + 68 = **432 tests**

---

## 五、验收清单

- [ ] ModelRouter 实现 100%（无 TODO/FIXME）
- [ ] TokenBudgetGuard 实现 100%（无 TODO/FIXME）
- [ ] 35 个 ModelRouter 单元测试 100% 通过
- [ ] 33 个 TokenBudgetGuard 单元测试 100% 通过
- [ ] Phase 1+2 回归测试零失败
- [ ] V2 回归测试零失败
- [ ] V2 文件零修改（`git diff` 为空）
- [ ] 性能基线：路由决策 < 10ms，Token 校验 < 5ms
- [ ] TODO/FIXME 0 处遗留
- [ ] 编译警告 0 处

---

## 六、回滚策略

如 Phase 3 出现问题：
1. 删除新增 4 个文件
2. 不影响 V2 + Phase 1 + Phase 2 任何代码
3. `git checkout scripts/dynamic_workflow/pattern_executor.py` 撤销集成改动（如有）

---

*下一步：用户确认 → 启动 Phase 3 实施*
