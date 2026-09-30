# Dynamic Workflows 6 大模式参考手册

> **文档类型**：模式参考手册（Phase 0' 配套）  
> **配套方案**：[DYNAMIC_WORKFLOWS_INTEGRATION.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md) v1.1  
> **配套审查**：[ARCHITECT_REVIEW_DYNAMIC_WORKFLOWS.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/ARCHITECT_REVIEW_DYNAMIC_WORKFLOWS.md)  
> **参考来源**：[Anthropic Dynamic Workflows（Claude Opus 4.8）](https://mp.weixin.qq.com/s/ZGOlA1IPSQaK3MXv_5fStQ)  
> **状态**：📚 概念手册，不含代码实现（Phase 0'）

---

## 0. 手册说明

### 0.1 用途

本手册是 trae-multi-agent 融合 Dynamic Workflows 的**模式定义文档**，为后续 Phase 0 代码实施提供：

1. **6 大模式的标准定义**（模式 ID / 描述 / 参数 / 适用场景 / 反模式）
2. **模式选择决策树**（任务特征 → 推荐模式）
3. **模式使用提示词模板**（用户与模式库交互的"自然语言契约"）
4. **模式间关系图**（哪些模式可组合）

### 0.2 模式总览

| # | 模式 ID | 中文名 | 一句话 | 适用 task 数 | Phase 0 优先级 |
|---|---------|--------|-------|-------------|----------------|
| 1 | `classifier-dispatch` | 分类并行动 | 分类器路由任务到不同子流程 | 中 | 🔴 P0 |
| 2 | `fan-out-aggregate` | 扇出与聚合 | 任务拆 N 份并行 → 屏障等待 → 合并 | 高 | 🔴 P0 |
| 3 | `adversarial-verify` | 对抗性验证 | 生成 + 独立 context 验证者 | 中 | 🔴 P0 |
| 4 | `generate-filter` | 生成与筛选 | 大量生成 → 标准筛选 → 去重 | 中 | 🟡 P1 |
| 5 | `tournament` | 锦标赛模式 | N 个候选两两 PK → 选冠军 | 低 | 🟡 P1 |
| 6 | `loop-until-done` | 循环直到完成 | 动态生成 subagent 直至停止条件 | 低 | 🟢 P2 |

**Phase 0 实现范围**：🔴 P0 的 3 个核心模式（1/2/3）  
**Phase 1+ 实现范围**：按需追加 P1/P2 模式

### 0.3 阅读指引

- **架构师 / 评审者**：重点看 §1 模式定义 §3 模式选择决策树
- **产品经理 / 用户**：重点看 §2 模式使用场景 §4 提示词模板
- **开发者（Phase 0+）**：重点看 §5 模式数据结构 §6 模式执行接口

---

## 1. 6 大模式标准定义

### 模式 1：classifier-dispatch（分类并行动）

#### 1.1.1 模式定义

```yaml
pattern_id: classifier-dispatch
name: 分类并行动
one_liner: 用分类器判断任务类型 → 路由到不同子流程或子智能体
core_problem: 单流程无法同时服务多种异构任务类型
key_insight: "分类" 是路由的元能力
article_reference: "1 分类并行动"（Dynamic Workflows 6 大模式 #1）
```

#### 1.1.2 适用场景

✅ **适合**：
- 任务入口混杂多种类型（同时有"代码审查 / 文档生成 / 测试编写"）
- 单一流程无法高效处理所有类型
- 不同类型任务需要不同角色或工具

❌ **不适合**：
- 任务类型单一（直接顺序执行即可）
- 分类器本身准确率低（错误分类比不分类更糟）

#### 1.1.3 参数 Schema

```json
{
  "classifier_role": "string",       // 分类器角色（必填）
  "route_table": {
    "<class_label>": {
      "target_pattern": "string",    // 路由到的工作流/模式
      "target_role": "string",       // 路由到的角色
      "priority": "number"           // 同 label 多路由时的优先级
    }
  },
  "fallback_route": "string",        // 未知分类时的兜底
  "classification_confidence_threshold": 0.7  // 低于此值走兜底
}
```

#### 1.1.4 失败模式

| 失败 | 触发条件 | 缓解 |
|------|---------|------|
| 分类不准确 | 训练样本不足 / 任务表达歧义 | 保留兜底路由 + 反馈回流 |
| 路由死循环 | route_table 互指 | 静态校验禁止环 |
| 分类开销过大 | 任务量极大 | 引入分类缓存 |

---

### 模式 2：fan-out-aggregate（扇出与聚合）

#### 1.2.1 模式定义

```yaml
pattern_id: fan-out-aggregate
name: 扇出与聚合
one_liner: 任务拆 N 份并行处理 → 屏障等待 → 聚合为单一结果
core_problem: 大量同质子任务，顺序执行效率低
key_insight: "屏障" 是并行的元能力
article_reference: "2 扇出与聚合"（Dynamic Workflows 6 大模式 #2）
```

#### 1.2.2 适用场景

✅ **适合**：
- 大量同质子任务（50+ 文件审查 / 100+ 工单分类）
- 每个子任务可独立 context
- 子任务间无强依赖

❌ **不适合**：
- 子任务间强依赖（必须等前一个）
- 子任务数过少（< 3，扇出开销大于收益）
- 资源受限（无 worktree 隔离能力时）

#### 1.2.3 参数 Schema

```json
{
  "fanout_count": "number",          // 并行度（1-10，Phase 0 硬上限 10）
  "fanout_strategy": "static | dynamic",  // 固定数 vs 动态数
  "subagent_role": "string",         // 每个子任务的角色
  "subagent_isolation": "worktree | context | full",
  "barrier_timeout_seconds": 3600,   // 屏障超时
  "aggregator_role": "string",       // 聚合者角色
  "aggregation_strategy": "concat | vote | rank | merge",
  "partial_failure_policy": "fail | skip | retry"
}
```

#### 1.2.4 失败模式

| 失败 | 触发条件 | 缓解 |
|------|---------|------|
| 屏障超时 | 部分子任务死锁 | 硬超时 + 部分失败策略 |
| 资源耗尽 | fanout_count 过大 | 硬上限 10 + 资源监控 |
| 聚合冲突 | 子结果格式不一致 | 聚合前 schema 校验 |
| subagent 崩溃污染 | 异常隔离不完整 | worktree 隔离 + finally 清理 |

---

### 模式 3：adversarial-verify（对抗性验证）

#### 1.3.1 模式定义

```yaml
pattern_id: adversarial-verify
name: 对抗性验证
one_liner: 生成者产出 → 独立 context 验证者对照评估准则验证
core_problem: 模型验证自己产出时存在 self-preferential bias
key_insight: "独立 context" 是对抗的元能力
article_reference: "3 对抗性验证"（Dynamic Workflows 6 大模式 #3）
```

#### 1.3.2 适用场景

✅ **适合**：
- 高风险产出（安全审查 / 架构决策 / 合规检查）
- 容易自我欺骗的任务（"我生成的方案显然没问题"）
- 有明确评估准则（"通过/不通过"二元判定）

❌ **不适合**：
- 主观性强的任务（设计审美）
- 没有评估准则的任务
- 简单任务（增加成本无收益）

#### 1.3.3 参数 Schema

```json
{
  "generator_role": "string",        // 生成者角色
  "verifier_role": "string",         // 验证者角色（**必须与生成者独立 context**）
  "verifier_isolation": "context | full",  // 至少 context 隔离
  "evaluation_criteria": ["string"], // 评估准则列表
  "verification_depth": "shallow | deep",  // 浅验证 vs 多轮对抗
  "max_rounds": 3,                   // 对抗轮次上限
  "pass_threshold": 0.8,             // 通过分数阈值
  "fallback_on_reject": "string"     // 不通过时的兜底（如重新生成）
}
```

#### 1.3.4 失败模式

| 失败 | 触发条件 | 缓解 |
|------|---------|------|
| 验证者与生成者共享偏见 | 隔离失效 | **强约束：必须独立 context** |
| 评估准则不明确 | criteria 是模糊描述 | schema 校验 + 必填 |
| 对抗无限循环 | 双方不断找理由 | max_rounds 硬上限 |
| 验证者过度严苛 | 通过率 < 10% | pass_threshold 动态调整 |

---

### 模式 4：generate-filter（生成与筛选）

#### 1.4.1 模式定义

```yaml
pattern_id: generate-filter
name: 生成与筛选
one_liner: 大量生成候选 → 评估筛选 → 去重 → 仅返回通过项
core_problem: 一次性生成质量不可控；大量生成后筛选更可靠
key_insight: "概率质量" 通过数量换
article_reference: "4 生成与筛选"（Dynamic Workflows 6 大模式 #4）
```

#### 1.4.2 适用场景

✅ **适合**：
- 创意探索（命名 / 标语 / 方案）
- 容忍重复候选（去重器可处理）
- 评估标准可量化

❌ **不适合**：
- 候选不能重复（每生成都贵）
- 评估标准主观（筛选结果不稳定）

#### 1.4.3 参数 Schema

```json
{
  "generator_role": "string",
  "generator_count": "number",       // 生成数量
  "filter_criteria": ["string"],     // 筛选标准
  "dedup_strategy": "exact | fuzzy | semantic",
  "dedup_threshold": 0.85,           // 模糊去重阈值
  "output_top_n": "number",          // 返回前 N 个
  "quality_floor": 0.6               // 低于此分数丢弃
}
```

---

### 模式 5：tournament（锦标赛模式）

#### 1.5.1 模式定义

```yaml
pattern_id: tournament
name: 锦标赛模式
one_liner: N 个候选 → 两两 PK → 逐步淘汰 → 决出冠军
core_problem: 多个候选方案难以一次性排序
key_insight: "两两对比" 比 "绝对打分" 更可靠
article_reference: "5 锦标赛模式"（Dynamic Workflows 6 大模式 #5）
```

#### 1.5.2 适用场景

✅ **适合**：
- 多方案选型（架构 / 库选择）
- 候选数 3-8（太少无需锦标赛，太多成本爆炸）
- 有明确裁判标准

❌ **不适合**：
- 候选无对比性（完全不同的产物）
- 评估需要全局视角（PK 信息不足）

#### 1.5.3 参数 Schema

```json
{
  "candidate_count": "number",       // 候选数（3-8）
  "candidate_generator": "string",   // 候选生成器角色
  "judge_role": "string",            // 裁判角色
  "ranking_method": "elo | knockout | round-robin",
  "judge_criteria": ["string"],
  "judge_context_isolation": true    // 裁判必须独立 context
}
```

---

### 模式 6：loop-until-done（循环直到完成）

#### 1.6.1 模式定义

```yaml
pattern_id: loop-until-done
name: 循环直到完成
one_liner: 动态生成 subagent → 直至满足停止条件
core_problem: 未知工作量的任务，固定次数不适用
key_insight: "停止条件" 比 "次数上限" 更优雅
article_reference: "6 循环直到完成"（Dynamic Workflows 6 大模式 #6）
```

#### 1.6.2 适用场景

✅ **适合**：
- 未知工作量的调查（根因分析 / 大规模数据处理）
- 有清晰停止信号（无新发现 / 无错误日志）
- 每次迭代可积累上下文

❌ **不适合**：
- 工作量已知（用顺序即可）
- 停止条件模糊（容易死循环）

#### 1.6.3 参数 Schema

```json
{
  "max_iterations": "number",        // 硬上限（避免死循环）
  "stop_conditions": {
    "no_new_findings": "boolean",    // 无新发现
    "no_error_logs": "boolean",      // 日志无新错误
    "quality_threshold_met": "boolean",
    "convergence_detected": "boolean"
  },
  "iteration_executor": "string",    // 每轮执行器
  "state_persistence": "checkpoint | memory"  // 跨迭代状态
}
```

---

## 2. 模式使用场景对照表

| 场景 | 推荐模式 | 理由 |
|------|---------|------|
| 50+ 文件安全审查 | **fan-out-aggregate** | 大量同质子任务 |
| 多方案架构选型 | **tournament** | 多候选择优 |
| 编写高风险代码并审查 | **adversarial-verify** | 防止 self-bias |
| 客服工单自动分流 | **classifier-dispatch** | 异构任务分类 |
| 命名/标语头脑风暴 | **generate-filter** | 大量生成后筛选 |
| 根因调查 | **loop-until-done** | 未知工作量 |
| 一次性文档编写 | ❌ 不需要模式 | 顺序即可 |
| 简单 bug 修复 | ❌ 不需要模式 | 顺序即可 |
| UI 配色方案对比 | **tournament** | 主观但需对比 |
| 用户反馈聚类 | **classifier-dispatch + fan-out-aggregate** | 复合模式 |

---

## 3. 模式选择决策树

```
                    [任务]
                       │
              ┌────────┴────────┐
              │ 任务类型数？    │
              └────────┬────────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
   单一类型         2-3 类型         多种
        │              │              │
        ▼              ▼              ▼
  ┌─────────┐   ┌─────────┐    ┌──────────────┐
  │ 子任务  │   │ 工作量  │    │classifier-   │
  │ 数量？  │   │ 评估    │    │dispatch      │
  └────┬────┘   └────┬────┘    └──────┬───────┘
       │             │                │
   ┌───┴───┐     ┌───┴────┐      [路由到]
   │       │     │        │         │
  >10    <=10  未知     已知        │
   │       │     │        │         ▼
   ▼       ▼     ▼        ▼      (递归)
fan-   顺序  loop-   顺序         决策
out-   执行  until-  执行         节点
agg.         done
```

**简化决策规则**（Phase 0 使用）：

```python
def select_pattern(task: Task) -> PatternID:
    # 规则 1：多种异构任务 → classifier-dispatch
    if task.type_variants >= 3:
        return "classifier-dispatch"
    
    # 规则 2：大量同质子任务 → fan-out-aggregate
    if task.subtask_count >= 10 and task.subtask_homogeneous:
        return "fan-out-aggregate"
    
    # 规则 3：高风险 + 有评估准则 → adversarial-verify
    if task.risk_level == "high" and task.has_evaluation_criteria:
        return "adversarial-verify"
    
    # 规则 4：未知工作量 + 清晰停止条件 → loop-until-done
    if task.workload_unknown and task.has_stop_condition:
        return "loop-until-done"
    
    # 规则 5：多方案 + 主观选择 → tournament
    if task.candidate_count >= 3 and task.comparison_based:
        return "tournament"
    
    # 规则 6：创意探索 + 大量生成 → generate-filter
    if task.is_creative and task.tolerates_duplicates:
        return "generate-filter"
    
    # 默认：顺序执行（不需要 Dynamic Workflows）
    return None
```

---

## 4. 模式使用提示词模板

### 4.1 通用模式调用模板（用户视角）

```markdown
使用 [模式名称] 处理 [任务描述]：
- 模式 ID: [pattern_id]
- 关键参数: [parameters]
- 期望结果: [expected_output]
- 成功标准: [success_criteria]

如果 [模式不适用条件]，请回退到顺序执行。
```

### 4.2 模式选择反推模板（如果用户描述模糊）

```markdown
请基于任务描述推荐 Dynamic Workflows 模式：
- 任务描述: {task}
- 关键约束: {constraints}
- 期望结果: {expected}

请输出 JSON：
{
  "recommended_pattern": "<pattern_id>",
  "rationale": "<选择理由>",
  "parameters": { ... },
  "estimated_token_budget": <number>,
  "fallback_pattern": "<sequential>"  // 不适用时的回退
}
```

### 4.3 各模式调用示例

**classifier-dispatch**：
```markdown
使用 classifier-dispatch 模式处理用户工单：
- 分类器角色：test-expert
- 路由表：
  - "bug": solo-coder（修复）
  - "feature_request": product-manager（需求分析）
  - "question": architect（技术答疑）
- 兜底路由：solo-coder（通用处理）
- 分类置信度阈值：0.7
```

**fan-out-aggregate**：
```markdown
使用 fan-out-aggregate 模式审查 50 个源文件：
- fanout_count: 10（每次并行 10 个）
- subagent_role: test-expert
- 隔离级别: worktree
- barrier_timeout: 3600s
- aggregator_role: architect
- 聚合策略: merge（合并所有审查结果）
- 部分失败策略: skip
```

**adversarial-verify**：
```markdown
使用 adversarial-verify 模式生成并验证新架构设计：
- generator_role: architect（生成方案）
- verifier_role: test-expert（独立 context 验证）
- 验证者隔离: full（context + worktree）
- 评估准则: ["满足性能需求", "无单点故障", "符合现有规范"]
- 验证深度: deep（多轮对抗）
- max_rounds: 3
- pass_threshold: 0.8
- 不通过兜底: 重新生成
```

---

## 5. 模式数据结构（Phase 0+ 代码设计参考）

> **本节为后续 Phase 0 代码实施提供数据结构参考。当前 Phase 0' 阶段不写代码。**

### 5.1 模式定义

```python
@dataclass
class WorkflowPattern:
    """工作流模式：声明式可复用模板"""
    pattern_id: str                              # 例："fan-out-aggregate"
    name: str                                    # 例："扇出与聚合"
    description: str                             # 一句话描述
    applicable_scenarios: List[str]              # 适用场景关键词
    not_applicable_scenarios: List[str]          # 不适用场景
    parameters_schema: Dict[str, Any]            # 参数 schema（JSON Schema 风格）
    failure_modes: List[FailureMode]             # 已知失败模式
    success_criteria: List[str]                  # 成功标准
    applicable_roles: List[str]                  # 适用角色列表
    isolation_requirement: str                   # 隔离要求：none/context/worktree/full
    default_token_budget: int                    # 默认 token 预算
    priority: int                                # 优先级（数值越小越高）
    version: str = "1.0"                         # 模式定义版本
```

### 5.2 模式选择结果

```python
@dataclass
class PatternSelection:
    """模式选择结果"""
    pattern_id: str
    confidence: float                            # 0-1 置信度
    rationale: str                               # 选择理由
    parameters: Dict[str, Any]                   # 实例化后的参数
    estimated_token_budget: int                  # 预估 token 预算
    fallback_pattern: Optional[str] = None       # 不适用时的回退
    applicable: bool = True                      # 是否适用
    rejection_reason: Optional[str] = None       # 不适用时的原因
```

### 5.3 模式选择画像（反哺机制）

> 复用 PerformanceFingerprint 的现有结构：
> - `ExecutionRecord`：记录每次模式选择和执行结果
> - `SuccessPattern` / `FailurePattern`：沉淀成功/失败模式
> - `context_outcome_map`：任务特征 → 适用模式 的反哺数据

```python
# Phase 0 实施时，PerformanceFingerprint 增加：
{
    "pattern_selection_history": [
        {
            "task_features": {...},
            "selected_pattern": "fan-out-aggregate",
            "outcome": "success",
            "execution_time": 45.2,
            "token_used": 8500,
            "timestamp": "2026-06-03T..."
        }
    ]
}
```

---

## 6. 模式执行接口（Phase 0+ 协议设计）

> **本节为后续 Phase 0 代码实施提供 Protocol 设计参考。**

### 6.1 PatternExecutor Protocol

```python
from typing import Protocol, Any, Dict
from dataclasses import dataclass

@dataclass
class PatternExecutionContext:
    """模式执行上下文（注入式）"""
    task: Task
    parameters: Dict[str, Any]
    fingerprint_store: PerformanceFingerprint  # 注入画像
    guard_coordinator: GuardCoordinator        # 注入 Guard
    token_budget: int
    isolation_level: str  # none/context/worktree/full

class PatternExecutor(Protocol):
    """模式执行器协议"""
    
    @property
    def pattern_id(self) -> str:
        """模式 ID"""
        ...
    
    def validate(self, ctx: PatternExecutionContext) -> ValidationResult:
        """执行前验证（输入 schema / 资源 / 风险）"""
        ...
    
    def execute(self, ctx: PatternExecutionContext) -> ExecutionResult:
        """执行模式"""
        ...
    
    def record_outcome(self, ctx: PatternExecutionContext, result: ExecutionResult) -> None:
        """记录执行结果到画像"""
        ...
```

### 6.2 模式执行器注册（V2 扩展点）

> **不修改 V2 文件**。通过现有 `register_executor` 机制接入：

```python
# V2 现有 API（不修改）
engine.register_executor("analyze_requirements", analyzer_func)

# Phase 0+ 新增：模式作为特殊 action 注册
engine.register_executor(
    "pattern:fan-out-aggregate",
    PatternExecutorAdapter(FanOutAggregateExecutor())
)
```

WorkflowStep 现有字段不变，新增 `pattern_executor: Optional[str]` 字段（向后兼容）。

---

## 7. 模式间关系图

```
                    ┌─────────────────────┐
                    │ classifier-dispatch │
                    └──────────┬──────────┘
                               │ 路由到
            ┌──────────────────┼──────────────────┐
            ▼                  ▼                  ▼
     ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
     │ fan-out-     │  │ adversarial- │  │ tournament   │
     │ aggregate    │  │ verify       │  │              │
     └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
            │                 │                 │
            └─────────────────┼─────────────────┘
                              ▼
                    ┌──────────────────┐
                    │ loop-until-done  │  （外层循环）
                    └──────────────────┘
```

**可组合模式**（Phase 1+ 实现）：
- `classifier-dispatch` 路由到 `fan-out-aggregate`（分类后并行处理）
- `adversarial-verify` 内嵌 `generate-filter`（生成多个方案，筛选后验证）
- `tournament` 的候选生成可走 `fan-out-aggregate`
- `loop-until-done` 的每轮可以是任意模式

---

## 8. 反模式与"禁用边界"

> **直接对齐文章核心警告："不是每个任务都需要使用 Dynamic Workflows"**

| 反模式 | 风险 | 应对 |
|--------|------|------|
| 所有任务都用模式 | 简单任务被复杂化，Token 浪费 | 默认顺序，按需升级 |
| 过度扇出 | 资源耗尽，并发冲突 | fanout_count 硬上限 10 |
| 过度锦标赛 | N 选 1 消耗巨大 | candidate_count 硬上限 8 |
| 验证者与生成者共享 context | 失去对抗意义 | **强校验：必须独立 context** |
| 模式库臃肿 | 6 大模式变 20 大 | **架构师审核新增** |
| 模式选择不稳定 | 相同任务选不同模式 | 缓存 + 画像反哺 |
| 滥用 loop-until-done | 死循环 | max_iterations 硬上限 |
| 模式选择无理由 | 不可解释 | 强制输出 rationale |

---

## 9. 验收与度量

### 9.1 模式库健康度指标

| 指标 | 计算 | 目标 |
|------|------|------|
| 模式选择可解释率 | 有 rationale 的选择 / 总选择 | 100% |
| 模式选择准确率 | 用户确认推荐 / 总推荐（人工抽样） | ≥ 80% |
| 模式不适用率 | recommend=null 的比例 | 30%-50%（说明默认顺序仍占主导） |
| 模式执行成功率 | 各模式成功执行 / 总执行 | ≥ 85% |
| 模式选择耗时 | end-to-end 选择时间 | < 100ms |

### 9.2 Phase 0 验收

- [ ] 3 个核心模式（1/2/3）数据结构定义完整
- [ ] 模式选择决策树覆盖 80% 真实场景
- [ ] 提示词模板通过用户评审
- [ ] 模式反模式清单完整
- [ ] 与 PerformanceFingerprint 对齐的数据结构

### 9.3 Phase 1+ 验收

- [ ] 6 大模式全部有可运行实现
- [ ] 模式选择画像回流有效（> 50 次执行后画像显著改善）
- [ ] 与 V2 引擎无缝集成（V2 回归测试零失败）
- [ ] 模式执行成功率 ≥ 85%
- [ ] Token 消耗与质量平衡达标

---

## 10. 参考资料

- [DYNAMIC_WORKFLOWS_INTEGRATION.md v1.1](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md) - 融合方案主文档
- [ARCHITECT_REVIEW_DYNAMIC_WORKFLOWS.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/ARCHITECT_REVIEW_DYNAMIC_WORKFLOWS.md) - 架构师审查报告
- [CYBERNETICS_INTEGRATION_PLAN.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/CYBERNETICS_INTEGRATION_PLAN.md) - Cybernetics 集成方案（参考）
- [Anthropic Dynamic Workflows 原文](https://mp.weixin.qq.com/s/ZGOlA1IPSQaK3MXv_5fStQ) - 文章原文

---

*手册版本：v1.0（Phase 0' 配套）*  
*创建日期：2026-06-03*  
*下一步：用户/架构师评审 → 启动 Phase 0 代码实施*  
*配套文件：模式选择 JSON 示例（待 Phase 0' 补完）+ 提示词模板（待 Phase 0' 补完）*
