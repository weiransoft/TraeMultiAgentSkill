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
| 7 | `vue-dual-stack-migration` | React→Vue3 双栈迁移 | React 仓逐块迁移到 Vue3，后端 API+类型共享保 parity | 高 | 🔴 P0（实测） |

**Phase 0 实现范围**：🔴 P0 的 3 个核心模式（1/2/3） + 🔴 P0（实测）的 vue-dual-stack-migration（B5/B6/B7 三批次验证，20+ 组件交付）
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

### 模式 7：vue-dual-stack-migration（React→Vue3 双栈迁移）

#### 7.1.1 模式定义

```yaml
pattern_id: vue-dual-stack-migration
name: React→Vue3 双栈迁移
one_liner: React 仓逐块迁移到 Vue3，后端 API+TypeScript 类型共享，保持功能 parity
core_problem: React 仓已有完整实现 + 后端 API + 类型定义，但 Vue 仓只有 lazyView 占位
key_insight: "后端类型即事实契约"——先 grep 锁定真实 API/类型，再写代码，避免臆造字段
phase: B5-B7 来源批次验证（参考，非规范正文）
priority: 🔴 P0（高频复用）
```

#### 7.1.2 适用场景

✅ **适合**：
- 后端 API 已稳定，React 仓是参照源，Vue 仓待迁移
- 双栈要求功能 parity，后端 TypeScript 类型共享
- 分批迁移（每次一个功能模块），逐块替换 lazyView 占位

❌ **不适合**：
- 后端 API 还在频繁变动（类型契约不稳定）
- 无 React 仓参照源（Vue 仓需从零实现）
- Vue 仓 API/类型模块尚未就绪（需先补 API 层）

#### 7.1.3 参数 Schema

```json
{
  "react_repo_path": { "type": "string", "required": true },
  "vue_repo_path": { "type": "string", "required": true },
  "target_routes": { "type": "array", "items": { "type": "string" }, "required": true },
  "batch_label": { "type": "string", "required": false, "default": "B-new" },
  "component_split_strategy": {
    "type": "string",
    "required": false,
    "enum": ["auto", "single-file", "parent-children", "max-3-drawers"],
    "default": "auto"
  }
}
```

#### 7.1.4 工作流 6 步（必须按序执行）

| # | 步骤 | 核心动作 | 门禁 |
|---|------|---------|------|
| 1 | 情报收集 | grep Vue 仓 API 模块 + grep services/types 真实字段 + 读 React 源文件 + 确认路由 meta | API/类型全齐才能继续 |
| 2 | 路由替换 | lazyView('Xxx') → () => import('@/views/xxx/Xxx.vue') | meta.requireAdmin 确认 |
| 3 | 组件创建 | 按复杂度拆分子组件，**自管 mutation 模式** | 组件拆分决策树 |
| 4 | typecheck | 每次写完立即跑，优先 grep 真实类型 | 零错误才能继续 |
| 5 | 门禁回归 | typecheck → vitest run → build | 全绿才能进下一批 |
| 6 | 运行时验证 | 浏览器导航 + Network + Console | **待执行项（诚实标记）** |

#### 7.1.5 核心模式：自管 mutation 组件通信

```
父组件只负责：
  - v-model:visible 双向绑定 Drawer/Dialog 开关
  - :user-record 传递目标记录
  - @success 监听 → listReq.refetch() 刷新列表

子组件自管：
  - useRequest（按需加载，watch props.xxx 触发）
  - useMutation（create/update/delete/reset）
  - 表单校验 + 403 错误处理
```

#### 7.1.6 踩坑 Checklist（14 条，跨项目通用）

> **分类说明**：按根因分类，每条给出**通用 pattern** 和**通用解法方法论**。每个新项目迁移时，需对照该项目的后端类型定义文件锁定真实字段名。

**类型契约类（4 条）**

| # | 根因分类 | 通用 pattern | 通用解法方法论 |
|---|---------|------------|--------------|
| T1 | 命名风格不一致 | 后端序列化/反序列化使用 snake_case（如 `is_active`），前端习惯 camelCase（如 `enabled`），直接用前端习惯名导致 TypeScript 类型错误 | 每次引用后端字段前，**先 grep 后端类型定义文件**确认真实字段名，不要凭习惯猜测 |
| T2 | 嵌套数据结构误判 | 后端返回的关联字段是**完整对象数组**（如 `roles: RoleRecord[]`），前端误以为是简单 ID 数组（`roles: string[]`），解构/遍历时报类型错误 | 看后端类型定义的**嵌套层级**，不要简化；需要简单 ID 时显式 `.map(r => r.id)` 提取 |
| T3 | 请求体字段边界误判 | 后端 Update 类型只暴露**部分可写字段**（如只有 email/status/scenario），前端误以为 Create 类型的所有字段都可用于 Update | grep Update/Edit 专用类型定义，对比 Create 类型，**缺失的字段就是不可更新的边界** |
| T4 | 分页参数命名不一致 | 不同项目后端约定不同：有的 `page_size`、有的 `size`、有的 `limit`；前端照搬另一个项目的参数名导致请求 400 | 查 Query 参数专用类型定义，**以定义文件为准**，不要凭经验猜 |

**Vue3 框架类（2 条）**

| # | 根因分类 | 通用 pattern | 通用解法方法论 |
|---|---------|------------|--------------|
| V1 | 内置组件 v-model 冲突 | Element Plus 的 Drawer/Dialog 等内置组件的 `v-model` 绑定的是组件自身的 `modelValue` prop，当子组件**自定义了同名 visible prop** 时，vite build 阶段编译器报错 | 用显式桥接写法：`:model-value="visible" @update:model-value="(v) => emit('update:visible', v)"` 替代 `v-model` |
| V2 | 编译器类型安全限制 | `defineEmits` 混用对象签名和函数签名会导致 TypeScript 类型推断混乱 | 统一用**对象形式**声明 emits |

**Composable 类（5 条）**

| # | 根因分类 | 通用 pattern | 通用解法方法论 |
|---|---------|------------|--------------|
| C1 | 方法名记忆错误 | useRequest 暴露的是 `refetch()` 不是 `refresh()` | 每次用新 composable 时，**先 grep 它的导出 API 签名** |
| C2 | Ref 嵌套不解包 | useRequest/useMutation 的 `loading` 都是 `Ref<boolean>`，script 里需要 `.value` 或 `computed` 解包 | script 里写 `computed(() => xxxReq.loading.value)`，template 直接用 |
| C3 | onSuccess 参数数量不确定 | 不同 composable 的 `onSuccess` 回调参数数量不同，前端凭旧项目习惯写多参数导致 TS 错误 | **读 composable 源码或类型定义文件**确认回调签名 |
| C4 | ComputedRef 类型不兼容 | useRequest 的 `params` 选项期望普通 Ref，但前端传了 `computed(...)`，泛型签名不兼容 | 不用 params 选项，改用 `watch(trigger, () => req.run(trigger), { immediate: true })` 手动触发 |
| C5 | mutation loading 在 button 上直接用 | template 里 `:loading="mutation.loading"` 会报 TS 错误 | 统一 `computed(() => mutation.loading.value)` 解包 |

**Element Plus 类（2 条）**

| # | 根因分类 | 通用 pattern | 通用解法方法论 |
|---|---------|------------|--------------|
| E1 | ElTable slot row 类型 | `#default="{ row }"` 中 row 的类型是 Element Plus 内部 DefaultRow，不是业务类型 | 用 `(row as any).fieldName` 显式断言访问业务字段 |
| E2 | ElForm :rules 类型严格 | ElForm 的 :rules prop 期望类型签名与通用数组推导不匹配，TS 报错 | 用 `:rules="rules as any"` 强制转换（Element Plus 社区公认 workaround） |

**构建类（1 条）**

| # | 根因分类 | 通用 pattern | 通用解法方法论 |
|---|---------|------------|--------------|
| B1 | vite build 不能动态 import 不存在的文件 | `import('./Xxx.vue').catch(...)` 在 vite build 阶段尝试解析，文件不存在直接报错 | 未实现组件**直接 import 占位组件**，不用 `.catch()` 降级 |

#### 7.1.7 类型映射速查规则（通用分类）

| 规则 | 分类 | 通用描述 | 反例 |
|---|---|---|---|
| 1 | 命名风格 | 后端序列化 snake_case → 前端需原样使用 | 后端 `is_active` ≠ 前端习惯 `enabled` |
| 2 | 数据结构 | 后端嵌套对象数组 → 前端显式 `.map(r => r.id)` 提取 | 后端 `RoleRecord[]` ≠ 前端误判 `string[]` |
| 3 | 枚举值 | 后端 Union type → 前端配置 Tag type + 中文标签映射表 | Element Plus Tag type 只能是 success/warning/danger/info/primary |
| 4 | API 边界 | 后端 Update 类型字段有限 → 前端只传可写字段 | 对比 Create/Update 类型，缺失字段 = 不可更新边界 |

#### 7.1.8 来源案例（参考，非规范正文）

> 以下批次是本模式的**来源验证**——pattern 已被实战检验过，但规范正文中的类型名/字段名是**抽象描述**，不是具体项目的真实类型。每个新项目迁移时，必须重新 grep 该项目的后端类型定义文件锁定真实字段名。

| 批次 | 迁移范围 | 交付组件数 | 踩坑命中 |
|---|---|---|---|
| B5 | 审计/红队/合规/评测 | 10 | 3 |
| B6 | 引擎配置/LoRA/灰度发布 | 3 | 4 |
| B7 | 系统管理/用户管理/个人信息 | 11 | 7 |
| **合计** | | **24** | **14 条（全部覆盖）** |

**通用 pattern 不依赖这些项目专属细节**，仅作为 pattern 已被实战验证的证据。

---

*手册版本：v1.0（Phase 0' 配套）*  
*创建日期：2026-06-03*  
*最近更新：2026-10-11（追加模式 7 vue-dual-stack-migration，B5/B6/B7 实测验证）*  
*下一步：用户/架构师评审 → 启动 Phase 0 代码实施*  
*配套文件：模式选择 JSON 示例（待 Phase 0' 补完）+ 提示词模板（待 Phase 0' 补完）*
