# fan-out-aggregate 模式使用提示词模板

> **模式 ID**：`fan-out-aggregate`  
> **适用**：大量同质子任务并行处理  
> **配套示例**：[fan-out-aggregate.json](../pattern_examples/fan-out-aggregate.json)  
> **配套手册**：[PATTERNS_REFERENCE.md §1.2](../PATTERNS_REFERENCE.md#模式-2fan-out-aggregate扇出与聚合)

---

## 1. 用户调用模式（直接使用）

### 1.1 基础模板

```markdown
使用 fan-out-aggregate 模式处理 [任务描述]：

## 任务
[详细任务描述]

## 扇出配置
- fanout_count: <1-10>（并行度）
- fanout_strategy: <static | dynamic>
- subagent_role: <角色>
- subagent_isolation: <worktree | context | full>
- barrier_timeout_seconds: <秒>

## 输入分块
- 策略: <by_file | by_module | by_record | by_range>
- 分块列表: [chunk1, chunk2, ...]

## 聚合
- aggregator_role: <角色>
- aggregation_strategy: <concat | vote | rank | merge>
- partial_failure_policy: <fail | skip | retry>

## 期望结果
- 所有子任务并行处理完成后聚合
- 整体耗时 < 顺序执行的 <X>%
- 覆盖率 100%（无 Agentic laziness）

## 成功标准
- [可验证标准 1：覆盖率]
- [可验证标准 2：耗时]
- [可验证标准 3：结果一致性]

如果屏障超时或子任务失败，根据 partial_failure_policy 处理。
```

### 1.2 完整示例：50 文件安全审查

```markdown
使用 fan-out-aggregate 模式审查项目源文件：

## 任务
对项目 50 个源文件进行安全审查（CWE Top 25 + 输入验证），每个文件独立审查后合并报告。

## 扇出配置
- fanout_count: 10（每次并行 10 个）
- fanout_strategy: static
- subagent_role: test-expert
- subagent_isolation: worktree
- barrier_timeout_seconds: 3600

## 输入分块
- 策略: by_file
- 分块列表: 50 个 .py / .java 文件路径

## 聚合
- aggregator_role: architect
- aggregation_strategy: merge
- partial_failure_policy: skip（失败的子任务跳过，不阻塞整体）

## 期望结果
- 50 个文件 100% 全部审查（避免 Agentic laziness）
- 扇出 + 聚合总耗时 < 顺序执行（50 文件 × 30s = 25min）的 50%
- 所有审查结果合并为一份统一报告

## 成功标准
- 覆盖 50/50 文件 = 100%
- 总耗时 < 12 分钟
- 报告 schema 100% 一致
- 发现的安全问题数 ≥ 顺序审查的 1.2x
```

### 1.3 反例（什么时候不要用）

```markdown
❌ 不要用 fan-out-aggregate 的场景：
- 子任务数 < 3（扇出开销 > 收益）
- 子任务间强依赖（A 必须等 B 完成）
- 目标环境非 Git 仓库（worktree 隔离不可用）
- 资源受限（机器无法支撑 10 个并行）
```

---

## 2. 模式反推模板（用户描述模糊时使用）

```markdown
基于以下任务描述推荐 Dynamic Workflows 模式：

## 任务
{task}

## 关键约束
- 子任务数: {subtask_count}
- 子任务同质性: {homogeneous: true | false}
- 子任务独立性: {independent: true | false}
- 目标环境: {git: true | false}

请输出 JSON（严格遵循 [fan-out-aggregate.json](../pattern_examples/fan-out-aggregate.json) 的 example_selection_output 格式）。
```

---

## 3. 关键设计决策

### 3.1 fanout_count 选多大？

```python
# 推荐公式（Phase 0+ 实施时使用）
def recommend_fanout_count(
    subtask_count: int,
    available_cpu: int,
    avg_subtask_duration_s: int,
    isolation_overhead_s: int = 5
) -> int:
    """
    根据子任务数、CPU 数、平均子任务时长、隔离开销推荐并行度
    """
    # 硬上限 10
    HARD_LIMIT = 10
    
    # 资源约束
    by_cpu = max(1, available_cpu - 1)  # 留 1 个 CPU 给主流程
    
    # 时长约束：子任务越短，并行度可以越高
    if avg_subtask_duration_s < isolation_overhead_s * 2:
        by_duration = 2
    else:
        by_duration = min(5, subtask_count)
    
    # 取最小值，且不超过硬上限
    return min(HARD_LIMIT, by_cpu, by_duration, subtask_count)
```

### 3.2 aggregation_strategy 怎么选？

| 策略 | 适用场景 | 示例 |
|------|---------|------|
| `concat` | 子结果独立，直接拼接 | 50 文件的审查报告（每文件 1 个发现） |
| `vote` | 子结果有重叠，取多数 | 100 工单的严重性评级（多 subagent 评，取众数） |
| `rank` | 子结果可比较，取 top N | 100 命名候选，排序取前 3 |
| `merge` | 子结果有结构，按 key 合并 | 50 模块的 API 清单（按模块名合并） |

### 3.3 partial_failure_policy 怎么选？

| 策略 | 适用 | 风险 |
|------|------|------|
| `fail` | 任何子任务失败都需立即中止 | 严格但容易因单点失败而全盘失败 |
| `skip` | 子任务失败不阻塞整体 | 容忍部分失败（推荐默认） |
| `retry` | 失败可重试的场景 | 资源消耗大，可能死循环 |

**Phase 0 默认**：**`skip`**

---

## 4. 失败处理模板

### 4.1 屏障超时

```markdown
⚠️ 屏障超时处理：
- barrier_timeout 触发后，收集已完成的子任务结果
- 未完成的子任务按 partial_failure_policy 处理：
  - fail: 整体失败
  - skip: 标记为未完成，继续聚合
  - retry: 重新入队（最多 1 次）
- 记录超时事件到 FailurePattern
```

### 4.2 资源耗尽

```markdown
⚠️ 资源耗尽处理：
- WorktreeManager 检测磁盘 / 内存不足 → 拒绝新 subagent 创建
- 已运行的 subagent 完成后立即清理 worktree
- 触发降级：fanout_count 自动减半（10 → 5 → 3 → 1）
- 触发阈值告警（用户感知）
```

### 4.3 聚合冲突

```markdown
⚠️ 聚合冲突处理：
- 子结果 schema 不一致 → 丢弃异常 schema 的子结果
- 关键字段缺失 → 标记为 partial_result，不阻塞整体
- 冲突率 > 30% → 触发异常告警，可能需要重新执行
```

---

## 5. 与 trae-multi-agent 现有组件的集成点

| 集成点 | 现有组件 | 配合方式 |
|--------|---------|---------|
| 扇出执行 | `workflow_engine_v2` + 新增 `PatternExecutor` | 通过 register_executor 注入 |
| 隔离机制 | 新增 `WorktreeManager`（Phase 2） | 不修改 V2，独立模块 |
| 屏障同步 | Python `concurrent.futures.ThreadPoolExecutor` | 标准库，无外部依赖 |
| 子任务记录 | `task_list_manager` | 每个 subagent 1 个 task |
| 反馈回流 | `performance_fingerprint` | 每次扇出结果记录为 1 条 ExecutionRecord |
| Token 预算 | 新增 `TokenBudgetGuard`（Phase 3） | 子任务级监控 |
| 失败重试 | V2 现有 `retry_count` | 复用，无需新机制 |

---

## 6. 验证清单（部署前自检）

- [ ] fanout_count ≤ 10（Phase 0 硬上限）
- [ ] 隔离级别 ≥ worktree（避免 subagent 互相污染）
- [ ] barrier_timeout 设置合理（基于子任务数 × 平均时长 × 1.5）
- [ ] partial_failure_policy 明确（默认 skip）
- [ ] aggregation_strategy 与子结果结构匹配
- [ ] 输入分块无遗漏（覆盖率 100%）
- [ ] 资源监控就位（CPU/内存/磁盘）
- [ ] 失败反馈回流通道已配置
- [ ] 与 PerformanceFingerprint 的对接已就绪

---

*模板版本：v1.0（Phase 0' 配套）*  
*创建日期：2026-06-03*
