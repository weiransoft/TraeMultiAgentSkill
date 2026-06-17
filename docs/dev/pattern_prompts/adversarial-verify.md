# adversarial-verify 模式使用提示词模板

> **模式 ID**：`adversarial-verify`  
> **适用**：高风险产出的独立验证  
> **配套示例**：[adversarial-verify.json](../pattern_examples/adversarial-verify.json)  
> **配套手册**：[PATTERNS_REFERENCE.md §1.3](../PATTERNS_REFERENCE.md#模式-3adversarial-verify对抗性验证)

---

## 1. 用户调用模式（直接使用）

### 1.1 基础模板

```markdown
使用 adversarial-verify 模式处理 [任务描述]：

## 生成任务
- 任务: [详细任务描述]
- generator_role: <角色>

## 验证配置
- verifier_role: <角色，**必须与生成者独立 context**>
- verifier_isolation: <context | full>（高风险必须 full）
- evaluation_criteria:
  - [可机器/人工验证的准则 1]
  - [可机器/人工验证的准则 2]
  - [至少 3 条]

## 对抗深度
- verification_depth: <shallow | deep>
- max_rounds: <1-5>
- pass_threshold: <0.0-1.0>

## 兜底
- fallback_on_reject: <regenerate | human_review | abort>

## 期望结果
- 生成者产出方案
- 验证者独立 context 验证
- 通过/不通过的明确判定
- 不通过时根据 fallback 处理

## 成功标准
- [可验证标准 1]
- [可验证标准 2]
- [可验证标准 3]

如果对抗轮次超限或验证者无法判定，请执行 fallback_on_reject。
```

### 1.2 完整示例：架构方案验证

```markdown
使用 adversarial-verify 模式为新功能设计架构方案：

## 生成任务
- 任务: 为"实时通知中心"功能设计架构方案
- generator_role: architect

## 验证配置
- verifier_role: test-expert
- verifier_isolation: full（context + worktree 双重隔离）
- evaluation_criteria:
  - 满足性能需求（10K 用户同时在线，P99 < 200ms）
  - 无单点故障（任意服务实例宕机不影响整体）
  - 符合现有 Spring Boot 3 + Java 21 代码规范
  - 通过 OWASP Top 10 安全检查
  - 可测试性（依赖注入可替换，单元测试覆盖率 ≥ 80%）
  - 可观测性（关键指标有 metric / log / trace）

## 对抗深度
- verification_depth: deep
- max_rounds: 3
- pass_threshold: 0.8

## 兜底
- fallback_on_reject: regenerate

## 期望结果
- architect 给出完整的架构方案
- test-expert 在独立 context 中对照 6 项准则验证
- 通过率 ≥ 80% 才算 pass
- 不通过时回到生成者重新设计（最多 3 轮）
- 3 轮仍未通过 → 升级到 human_review

## 成功标准
- 6 项准则 100% 都有明确评估（通过/不通过）
- 验证者平均发现 ≥ 2 个生成者未意识到的潜在问题
- 整体对抗轮次 ≤ 2
- 最终方案的"独立验证通过率"比"自我评估通过率"低 ≤ 10%
```

### 1.3 反例（什么时候不要用）

```markdown
❌ 不要用 adversarial-verify 的场景：
- 任务主观性极强（设计审美、艺术创作）
- 没有评估准则（无法判定通过/不通过）
- 简单任务（修复 typo、写一行代码）
- 验证者与生成者能力差距过大（验证无意义）
- 资源极度紧张（验证者 context 隔离成本高）
```

---

## 2. 模式反推模板（用户描述模糊时使用）

```markdown
基于以下任务描述推荐 Dynamic Workflows 模式：

## 任务
{task}

## 关键约束
- 风险等级: {risk_level: low | medium | high}
- 是否有评估准则: {has_criteria: true | false}
- 准则可测量性: {measurable: true | false}
- 影响范围: {impact_scope: <一句话说明>}

请输出 JSON（严格遵循 [adversarial-verify.json](../pattern_examples/adversarial-verify.json) 的 example_selection_output 格式）。
```

---

## 3. 关键设计决策

### 3.1 evaluation_criteria 怎么写好？

**铁律**：
- ✅ **可机器/人工验证**：含数字、阈值、测试名或标准名
- ❌ **模糊描述**：避免"性能要好"、"代码要优雅"

**反例 → 正例**：

| 反例 | 正例 |
|------|------|
| "性能要好" | "P99 响应时间 < 200ms" |
| "代码要优雅" | "函数圈复杂度 ≤ 10，单函数行数 ≤ 50" |
| "安全性达标" | "通过 OWASP Top 10 全部 10 项检查" |
| "可测试" | "单元测试覆盖率 ≥ 80%，集成测试覆盖关键路径" |
| "可观测" | "关键指标有 metric / log / trace 三个维度" |

**最少 3 条**。少于 3 条 → GuardCoordinator 启动前警告。

### 3.2 verification_depth 怎么选？

| 深度 | 适用 | 成本 | 效果 |
|------|------|------|------|
| `shallow`（单轮验证） | 中风险任务 | 1× | 一般 |
| `deep`（多轮对抗） | 高风险任务 | 2-3× | 显著 |

**Phase 0 推荐**：
- risk_level=low → shallow
- risk_level=medium → shallow
- risk_level=high → deep

### 3.3 pass_threshold 怎么定？

```python
# 推荐公式（Phase 0+ 实施时使用）
def recommend_pass_threshold(
    risk_level: str,
    historical_pass_rate: float = None
) -> float:
    if risk_level == "low":
        return 0.7
    elif risk_level == "medium":
        return 0.8
    elif risk_level == "high":
        return 0.85
    elif risk_level == "critical":
        return 0.95
    
    # 数据驱动模式（推荐）：根据历史 50 次执行的 P50
    if historical_pass_rate:
        return max(0.7, min(0.95, historical_pass_rate - 0.1))
```

### 3.4 fallback_on_reject 怎么选？

| 兜底 | 适用 | 副作用 |
|------|------|--------|
| `regenerate` | 生成者有能力改进（最常见） | 增加 1-2 轮成本 |
| `human_review` | 高风险且 regenerate 无效 | 阻塞 |
| `abort` | 任务可放弃 | 直接失败 |

**Phase 0 默认**：**`regenerate`**

---

## 4. 失败处理模板

### 4.1 验证者共享 context（🔴 强失败）

```markdown
🔴 强失败处理（不可降级）：
- GuardCoordinator 启动前校验 verifier_isolation 字段
- 如果是 context 隔离但实际共享 context → 直接拒绝启动
- 错误信息：'adversarial-verify requires verifier_isolation ∈ {context, full}'
- 记录到 FailurePattern：'isolation_violation'
```

### 4.2 评估准则不明确

```markdown
⚠️ 准则不明确处理：
- schema 校验时检查每条准则：
  - 是否含数字/阈值/标准名/测试名？
  - 如果 4 项都没有 → 警告并要求用户重写
- 软警告：仍可执行，但记录到 FailurePattern：'ambiguous_criteria'
```

### 4.3 对抗无限循环

```markdown
⚠️ 无限循环防护：
- max_rounds 硬上限（默认 3，最高 5）
- 达到上限后强制执行 fallback_on_reject
- 记录到 FailurePattern：'max_rounds_exceeded'
```

### 4.4 验证者过度严苛

```markdown
⚠️ 严苛度异常处理：
- 监控通过率，如果 < 10% → 触发自动调整 pass_threshold
- 调整公式：pass_threshold = max(0.6, 通过率 + 0.1)
- 记录到 FailurePattern：'verifier_overly_strict'
```

---

## 5. 与 trae-multi-agent 现有组件的集成点

| 集成点 | 现有组件 | 配合方式 |
|--------|---------|---------|
| 生成者 | `trae_agent_dispatch_v2` | 复用现有角色调度 |
| 验证者隔离 | 新增 `SubagentSandbox`（Phase 2） | 独立 context/worktree |
| 评估准则 | 不需要新组件 | 用户在 prompt 中声明 |
| 对抗轮次 | V2 现有 `retry_count` | 概念复用，max_rounds 不同 |
| 反馈回流 | `performance_fingerprint` | 每次对抗结果记录为 1 条 ExecutionRecord |
| 角色能力匹配 | V2 现有 `role_matcher` | 验证者角色需能力 ≥ 生成者 |

---

## 6. 验证清单（部署前自检）

- [ ] verifier_role 与 generator_role **角色不同**（避免同角色偏见）
- [ ] verifier_isolation ∈ {context, full}（🔴 强约束）
- [ ] evaluation_criteria ≥ 3 条（少于警告）
- [ ] 每条准则可测量（含数字/阈值/标准名/测试名）
- [ ] max_rounds ∈ [1, 5]（防止死循环）
- [ ] pass_threshold 合理（基于风险等级）
- [ ] fallback_on_reject 明确（默认 regenerate）
- [ ] GuardCoordinator 已配置隔离校验
- [ ] 反馈回流通道已配置
- [ ] 与 PerformanceFingerprint 的对接已就绪

---

## 7. Self-Preferential Bias 防护原理（深入）

> 用户/评审者可能不理解"为什么要独立 context"，此处解释原理。

### 7.1 痛点演示

```python
# ❌ 反例：让生成者自己验证
generator_output = "我的架构方案很好，没有问题"  # self-bias
self_verification = agent.verify(generator_output)  
# 结果：80% 通过率（虚高 30%+）

# ✅ 正例：独立 context 验证
generator_output = "我的架构方案很好，没有问题"
isolated_verification = independent_agent.verify(generator_output, criteria)
# 结果：实际通过率 50%（接近真实）
```

### 7.2 独立 context 的关键

> 关键不是"不同模型"，而是"**不同上下文窗口**"。

| 隔离级别 | 效果 | 适用 |
|---------|------|------|
| 共享 context | self-bias 100% | ❌ 不可用 |
| 独立 context | self-bias 降低 60-70% | 中风险 |
| 独立 context + worktree | self-bias 降低 80%+ | 高风险（推荐） |

### 7.3 验证者的"质量"也很重要

> 验证者能力 < 生成者 → 验证无意义（生成者可以糊弄过去）

**Phase 0 规则**：
- 验证者角色能力评分 ≥ 生成者角色能力评分
- 可在 `role_matcher.py` 中加入"能力下界"校验

---

*模板版本：v1.0（Phase 0' 配套）*  
*创建日期：2026-06-03*
