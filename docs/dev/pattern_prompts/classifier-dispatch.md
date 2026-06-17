# classifier-dispatch 模式使用提示词模板

> **模式 ID**：`classifier-dispatch`  
> **适用**：异构任务分类路由  
> **配套示例**：[classifier-dispatch.json](../pattern_examples/classifier-dispatch.json)  
> **配套手册**：[PATTERNS_REFERENCE.md §1.1](../PATTERNS_REFERENCE.md#模式-1classifier-dispatch分类并行动)

---

## 1. 用户调用模式（直接使用）

### 1.1 基础模板

```markdown
使用 classifier-dispatch 模式处理 [任务描述]：

## 任务
[详细任务描述]

## 分类器
- 角色: <product-manager | test-expert | architect | solo-coder>
- 分类置信度阈值: <0.0-1.0，默认 0.7>

## 路由表
| 分类标签 | 目标角色 | 目标模式 | 优先级 |
|---------|---------|---------|--------|
| <label1> | <role> | <pattern> | <1-N> |
| <label2> | <role> | <pattern> | <1-N> |
| ... |

## 兜底
- 兜底路由: <role>
- 兜底模式: <sequential>

## 期望结果
- 每个分类标签的子任务被路由到对应流程处理
- 整体结果在 [时间] 内完成
- 分类准确率 ≥ <X>%

## 成功标准
- [可验证标准 1]
- [可验证标准 2]

如果分类失败或置信度低，请回退到兜底路由。
```

### 1.2 完整示例：客服工单分流

```markdown
使用 classifier-dispatch 模式处理客服系统今日的工单：

## 任务
客服系统收到 200 条用户工单，需要自动分类并分发到对应处理流程。

## 分类器
- 角色: test-expert
- 分类置信度阈值: 0.7

## 路由表
| 分类标签 | 目标角色 | 目标模式 | 优先级 |
|---------|---------|---------|--------|
| bug | solo-coder | sequential | 1 |
| feature_request | product-manager | sequential | 1 |
| question | architect | sequential | 1 |
| incident | solo-coder | adversarial-verify | 0 |

## 兜底
- 兜底路由: solo-coder
- 兜底模式: sequential

## 期望结果
- 200 条工单全部被分类（无遗漏）
- bug/feature_request/question 类用普通流程处理
- incident 类（生产事故）启用对抗验证（独立验证者）
- 整体处理在 4 小时内完成

## 成功标准
- 分类准确率 ≥ 90%
- incident 类的对抗验证发现 ≥ 3 个潜在风险
- 零工单被错误路由到生产事故流程
```

### 1.3 反例（什么时候不要用）

```markdown
❌ 不要用 classifier-dispatch 的场景：
- 任务类型单一（直接顺序执行）
- 子任务数 < 5（分类开销 > 收益）
- 分类器准确率 < 70%（错误分类比不分类更糟）
```

---

## 2. 模式反推模板（用户描述模糊时使用）

当用户给出任务但未指定模式时，可用此模板让 AI 反推：

```markdown
基于以下任务描述推荐 Dynamic Workflows 模式：

## 任务
{task}

## 关键约束
- 任务类型数: {type_variants}
- 子任务数: {subtask_count}
- 风险等级: {risk_level}
- 时间预算: {time_budget}

## 期望结果
{expected_output}

请输出 JSON（严格遵循 [classifier-dispatch.json](../pattern_examples/classifier-dispatch.json) 的 example_selection_output 格式）：

{
  "pattern_id": "<classifier-dispatch | fan-out-aggregate | adversarial-verify | ... | null>",
  "applicable": <true | false>,
  "confidence": <0.0-1.0>,
  "rationale": "<选择理由，说明为什么这个模式最优>",
  "parameters": { ... },
  "estimated_token_budget": <integer>,
  "fallback_pattern": "<sequential | null>"
}

如果该模式不适用（任务类型单一等），请返回 pattern_id=null 并说明 rejection_reason。
```

---

## 3. 模式选择理由模板（写给代码/AI 看的）

> 供 Phase 0+ 的 pattern_composer 使用。

```python
# rationale 字段的常见模板
RATIONALE_TEMPLATES = {
    "type_variants>=3": "任务存在 {N} 种异构类型，单一流程无法高效处理；启用分类器路由。",
    "type_variants>=3+high_risk": "任务存在 {N} 种异构类型，且 {M} 类为高风险；启用分类器 + 高风险类对抗验证。",
    "type_variants<3": "任务类型 {N} < 3，无需分类器，顺序执行即可。"
}
```

---

## 4. 失败处理模板

### 4.1 分类失败

```markdown
⚠️ 分类失败处理：
- 分类器返回置信度 < 0.7 → 路由到兜底角色 solo-coder
- 分类器异常 → 标记为 unclassified，单独队列人工处理
- 分类路由冲突（同一任务多种高优先级路由）→ 优先级最高的胜出
```

### 4.2 路由失败

```markdown
⚠️ 路由失败处理：
- 目标角色不可用 → 路由到兜底角色
- 目标模式初始化失败 → 降级为 sequential
- 路由表本身有错误 → GuardCoordinator 启动前校验（静态检查）
```

### 4.3 死循环防护

```markdown
🛡️ 死循环防护：
- 路由表加载时静态校验：禁止环（A → B → A）
- 单任务路由次数上限：3 次（超出标记为异常）
- 路由历史记录：保留最近 100 次路由，便于事后审计
```

---

## 5. 与 trae-multi-agent 现有组件的集成点

| 集成点 | 现有组件 | 配合方式 |
|--------|---------|---------|
| 分类器本身 | `trae_agent_dispatch_v2` 的 AI 语义匹配 | 复用其 ai_enhanced / semantic 匹配能力 |
| 路由目标 | `workflow_engine_v2` 的 register_executor | 通过扩展点注册 pattern 路由 |
| 失败反馈 | `performance_fingerprint` 的 FailurePattern | 分类不准确事件回流为 FailurePattern |
| 路由历史 | `task_list_manager` | 每次路由产生 1 个 task，方便可视化 |

---

## 6. 验证清单（部署前自检）

- [ ] 分类器 schema 通过 `pattern_library.validate()`
- [ ] 路由表无环（静态检查通过）
- [ ] 兜底路由已设置
- [ ] 置信度阈值合理（基于历史数据）
- [ ] 失败处理策略明确
- [ ] 反馈回流通道已配置
- [ ] 与 PerformanceFingerprint 的对接已就绪

---

*模板版本：v1.0（Phase 0' 配套）*  
*创建日期：2026-06-03*
