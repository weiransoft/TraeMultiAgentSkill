# Phase 13 最终报告：多 Goal 编排（Multi-Goal Orchestration）

> 任务：实现 Phase 13 — 多 Goal 编排（父子 Goal + DAG 依赖 + 跨 Goal 复用），含完整 CLI、续跑、跨 Goal 语义复用。V2 零修改集成。

**实施日期**：2026-06-06
**状态**：✅ 已完成
**测试结果**：83/83 Phase 13 测试通过；201/201 跨 Phase 测试通过（loop_goal + checkpoint + workflow_engine_v2 + Phase 13 全套）

---

## 1. 实施总览

| 组件 | 描述 | 状态 |
|------|------|------|
| **Goal 数据模型** | `schema_version=13.0` + `parent_goal_id` / `depends_on` / `aggregation_strategy` / `resume_count` / `max_resume_count` | ✅ |
| **GoalGraph（DAG）** | DFS 加载 + 拓扑排序（Kahn）+ DFS 三色环检测 + 完整性 / 大小 / 深度校验 | ✅ |
| **GoalScheduler** | ProcessPoolExecutor 并发（替代 ThreadPoolExecutor）+ barrier 同步 + cancel / pause 事件 | ✅ |
| **GoalResumeManager** | 5 状态机（ACTIVE/IN_PROGRESS/ACHIEVED/FAILED/ABANDONED）+ `--force` 强制重置 | ✅ |
| **GoalIterationReuser** | 跨语言 embedder（paraphrase-multilingual-MiniLM-L12-v2）+ top-K + 完整审计链 | ✅ |
| **GoalOrchestrator** | 顶层门面：5 阶段管线（DAG→Resume→Reuse→Schedule→Report） | ✅ |
| **GoalOrchestratorReport** | JSON + Markdown 双格式 + D5 截断（>50 节点） | ✅ |
| **register_goal_executor** | V2 零修改集成（通过 `register_executor` 公共 API） | ✅ |
| **CLI 9 标志** | `--multi-goal` / `--goal-parent` / `--goal-depends` / `--goal-aggregation` / `--goal-resume` / `--goal-resume-force` / `--goal-max-resume-count` / `--reuse-threshold` / `--disable-iteration-reuse` / `--max-concurrent` / `--goal-report` | ✅ |
| **端到端集成测试** | 4 个 E2E + 性能基线 | ✅ |
| **DAG 失败路径测试** | 6 个失败场景（root 缺失 / depends_on 缺失 / 环 / 环路径 / 深度超限 / 大小超限） | ✅ |
| **CLI 解析测试** | 12 个 flag 解析验证 | ✅ |

**测试新增**：68 个单元测试（test_goal_orchestrator.py）+ 15 个集成测试（test_goal_orchestrator_integration.py）= 83 个 Phase 13 测试。

---

## 2. 关键架构决策

### 2.1 强约束：V2 零修改

**决策**：通过 `register_executor` 公共 API 桥接，不修改 `WorkflowEngineV2` 一行代码。

**实现**：[`register_goal_executor`](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/scripts/goal_orchestrator.py#L1420-L1505) 注册一个名为 `execute_goal_subgraph` 的 executor。V2 工作流可在 step 中通过此名称调用多 Goal 编排。

**收益**：
- 严格遵守架构师 review 的 "V2 不修改" 强约束
- V2 仍是 action-based 工作流，Phase 13 是可选的上层编排能力
- 后续 Phase 14+ 可继续扩展 executor 而无需触碰 V2 核心

### 2.2 进程隔离：ProcessPoolExecutor

**决策**：使用 `ProcessPoolExecutor` 替代 `ThreadPoolExecutor`。

**原因**：
- 跨进程 `fcntl.flock` 锁 + GIL 抢占 → 死锁风险
- ProcessPool 真正的并行（不共享 GIL）
- 子进程独立 GoalRegistry 实例（避免跨进程 fcntl 锁）

**B1 修复**：子进程入口函数 `_execute_goal_in_subprocess` 是模块级函数（pickle 兼容），且在子进程内独立创建 `GoalRegistry`。

### 2.3 入参隔离：所有状态修改首行 `deepcopy(goal)`

**决策**：所有修改 Goal 状态的方法（`resume` / `reuse_into` 等）首行 `deepcopy(goal)`。

**原因**：
- 避免对调用方传入的 Goal 对象产生副作用
- 防止外部修改污染磁盘持久化数据
- B5 修复：`GoalResumeManager.resume()` 和 `GoalIterationReuser.reuse_into()` 都遵循

**测试覆盖**：`test_10_resume_b5_does_not_mutate_input` / `test_08_reuse_into_b5_no_mutation` / `test_11_get_resumable_goals_returns_deepcopy` 验证入参隔离。

### 2.4 Schema 向后兼容

**决策**：`schema_version=13.0` + 旧 Goal JSON 无字段时自动迁移。

**实现**：[`Goal.__post_init__`](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/scripts/loop_goal.py#L363-L368) 在字段缺失时使用 dataclass 默认值（`parent_goal_id=None` / `depends_on=[]` / `resume_count=0` / `max_resume_count=3` / `aggregation_strategy=AND`）。

**收益**：Phase 12 之前的所有 Goal JSON 无需迁移即可参与多 Goal 编排。

### 2.5 强类型：跨语言 Embedder

**决策**：默认使用 `paraphrase-multilingual-MiniLM-L12-v2`，而非英文-only 模型。

**原因**：
- 项目支持中英文双语任务描述
- 跨语言相似度对中英混排的 Goal 描述至关重要
- 可通过 `--reuse-threshold` 调优

**B2 修复**：完整审计链 `CrossGoalReuseEntry` 记录 `source_goal_id` / `target_goal_id` / `similarity` / `threshold` / `decision` / `reused_iteration_no` / `timestamp` / `notes`。

---

## 3. 关键 Bug 修复路径

| 编号 | 严重度 | 标题 | 修复 |
|------|--------|------|------|
| **A1** | Major | 缺 GoalRegistry APIs（`list_children` / `get_goal_status`） | ✅ 在 `loop_goal.py` 中新增方法 |
| **A2** | Major | V2 修改约束 | ✅ 通过 `register_executor` 桥接（V2 0 行修改） |
| **A3** | Minor | GoalAggregator 与 Parent-Child 树混淆 | ✅ 明确语义：`aggregation_strategy` 仅描述子 Goal 合并规则 |
| **A4** | Major | GoalGraph forward reference | ✅ `_load_recursive` 递归加载 `depends_on` 引用 + `_validate_edge_integrity` 校验 |
| **A5** | Minor | ABANDONED + force=True 边界 | ✅ `GoalResumeManager.resume()` 显式处理 A5 + N10 两个分支 |
| **B1** | Major | ThreadPoolExecutor 死锁风险 | ✅ 替换为 `ProcessPoolExecutor` + 模块级子进程入口 |
| **B2** | Major | 跨 Goal 复用 | ✅ `--reuse-threshold` / 多语言 embedder / `CrossGoalReuseEntry` 审计 / `--disable-iteration-reuse` |
| **B5** | Major | Resume 副作用 | ✅ 所有修改首行 `deepcopy(goal)` |
| **C1** | Minor | 深度信息混入 Goal 字段 | ✅ 引入 `_GraphNode` 包装器（goal.depth 不存在） |
| **D5** | Major | 报告截断缺失 | ✅ `GoalOrchestratorReport._count_nodes` + 截断阈值 50 |
| **N9** | Minor | GoalOrchestrator 缺参数 | ✅ 构造器接受 `reuse_threshold` / `reuse_enabled` |
| **N10** | Minor | FAILED + force=True 边界 | ✅ 同 A5 显式分支处理 |
| **N11** | Major | GoalOrchestrator.run 不完整 | ✅ 完整 5 阶段管线（DAG→Resume→Reuse→Schedule→Report） |
| **N12** | Major | generate_report 不完整 | ✅ 完整 JSON + Markdown 序列化 + D5 截断 |
| **N13** | Minor | reuse_into 修改入参 | ✅ 同 B5 处理（首行 `deepcopy`） |
| **N14** | Minor | 测试数不对 | ✅ 调整至 83 个 Phase 13 测试 |

---

## 4. 测试覆盖

### 4.1 单元测试（test_goal_orchestrator.py — 68 个）

| 测试类 | 用例数 | 覆盖内容 |
|--------|--------|----------|
| `TestGoalOrchestratorImports` | 2 | 异常类与数据类导入 |
| `TestGoalGraphBasics` | 8 | DAG 加载 / 拓扑 / 环检测 / 完整性 / 大小 / 深度 |
| `TestGoalSchedulerBasics` | 10 | ProcessPool / cancel / pause / shutdown / 配置 |
| `TestGoalResumeManager` | 11 | 5 种状态续跑 / force / B5 |
| `TestGoalIterationReuser` | 11 | skip_* 路径 / top-K / embedder / B5 |
| `TestGoalOrchestratorFacade` | 11 | 构造器 / list_active / report JSON/MD / 截断 |
| `TestCosineSimilarity` | 3 | 余弦相似度边界 |
| `TestPhase13CLIFlags` | 12 | CLI 9 flag 解析验证 |

### 4.2 集成测试（test_goal_orchestrator_integration.py — 15 个）

| 测试类 | 用例数 | 覆盖内容 |
|--------|--------|----------|
| `TestEndToEndIntegration` | 4 | 单 root / 父子树 / 50 节点 perf / 51 节点截断 perf |
| `TestDAGFailurePaths` | 6 | root 缺失 / depends_on 缺失 / 自环 / 3 节点环 / 深度超限 / 大小超限 |
| `TestResumeEndToEnd` | 3 | 续跑后磁盘持久化 / force 重置 / 超限标记 ABANDONED |
| `TestIterationReuseEndToEnd` | 2 | 禁用复用 / 审计日志结构 |

### 4.3 性能基线

- **50 节点报告生成**：< 1.0s（实测 0.05s 以内）
- **51 节点报告截断**：< 0.5s（实测 0.01s 以内）
- **DAG 拓扑排序**：50 节点 < 50ms
- **跨 Goal 余弦相似度**：O(n) per embedding

---

## 5. 使用示例

### 5.1 创建根 + 子 Goal（通过 registry API）

```python
from loop_goal import GoalRegistry
from goal_orchestrator import GoalOrchestrator, GoalAggregationStrategy

registry = GoalRegistry(storage_root=".trae/goals")

# 创建根 Goal
root = registry.create_goal(
    description="Phase 13 多 Goal 编排示例",
    criteria=["所有子 Goal 达成"],
    goal_id="phase-13-demo",
    max_iterations=5,
)

# 创建子 Goal（parent_goal_id 指向根）
child = registry.create_goal(
    description="子任务 1：写文档",
    goal_id="phase-13-child-1",
    task_template="编写 Phase 13 最终报告",
)
child.parent_goal_id = "phase-13-demo"
registry._save_goal_atomic(child)

# 编排执行
orch = GoalOrchestrator(registry=registry, max_concurrent=5)
report = orch.generate_report("phase-13-demo", format="json")
print(report)
```

### 5.2 CLI：多 Goal 编排

```bash
# 执行多 Goal 编排（以 root-id 为入口）
python3 trae_agent_dispatch_v2.py \
  --task "执行 Phase 13 多 Goal 编排" \
  --multi-goal phase-13-demo \
  --max-concurrent 5 \
  --reuse-threshold 0.90 \
  --goal-report json

# 续跑 FAILED / ABANDONED Goal
python3 trae_agent_dispatch_v2.py \
  --task "续跑失败目标" \
  --goal-resume phase-13-demo \
  --goal-resume-force

# 创建带依赖的子 Goal
python3 trae_agent_dispatch_v2.py \
  --task "新子任务" \
  --goal new-child \
  --goal-desc "子任务 2" \
  --goal-parent phase-13-demo \
  --goal-depends phase-13-child-1 \
  --goal-aggregation AND
```

### 5.3 V2 集成：在 V2 工作流中嵌入多 Goal 编排

```python
from workflow_engine_v2 import WorkflowEngineV2
from goal_orchestrator import GoalOrchestrator, register_goal_executor

v2 = WorkflowEngineV2()
orch = GoalOrchestrator(registry=...)
register_goal_executor(v2, orch)

# V2 工作流可使用 "execute_goal_subgraph" 作为 step action
# 无需修改 V2 任何代码
```

---

## 6. 文件清单

### 6.1 新增文件

| 文件 | 行数 | 描述 |
|------|------|------|
| `scripts/goal_orchestrator.py` | 1505 | Phase 13 核心模块（异常 / 数据类 / Graph / Scheduler / Resume / Reuser / Orchestrator / Report / register_goal_executor） |
| `scripts/tests/test_goal_orchestrator.py` | 944 | 68 个 Phase 13 单元测试 |
| `scripts/tests/test_goal_orchestrator_integration.py` | 411 | 15 个 Phase 13 集成测试 |
| `docs/dev/PHASE13_PLAN.md` | (existing) | Phase 13 实施计划 |
| `docs/superpowers/plans/2026-06-06-phase13-multi-goal-orchestration.md` | (existing) | Phase 13 详细任务分解 |

### 6.2 修改文件

| 文件 | 变更描述 |
|------|----------|
| `scripts/loop_goal.py` | 新增 `Goal` 字段（`schema_version` / `parent_goal_id` / `depends_on` / `aggregation_strategy` / `resume_count` / `max_resume_count`）；扩展 `list_goals` API（`statuses` / `parent_goal_id` / `include_root_only`）；新增 `list_children` / `get_goal_status` 方法 |
| `scripts/trae_agent_dispatch_v2.py` | 新增 9 个 Phase 13 CLI flag + 2 个 dispatch 函数（`dispatch_agent_v2_with_goal_resume` / `dispatch_agent_v2_with_multi_goal`） |

---

## 7. 已知限制与后续优化

### 7.1 限制

1. **DAG 节点上限 50**：硬上限（MAX_NODES=50）；超过则抛 `GoalGraphSizeError`
2. **DAG 深度上限 5**：硬上限（MAX_DEPTH=5）；超过则抛 `GoalGraphDepthError`
3. **DAG 整超时 60min / 单 Goal 30min**：默认配置，可通过 `scheduler.dag_timeout_seconds` 调
4. **复用 top-K=3**：跨 Goal 复用最多取 3 个 sibling
5. **跨语言 embedder 加载失败**：`GoalIterationReuser` 优雅降级（禁用复用并记录 `skip_embedder_error`）

### 7.2 Phase 14+ 优化方向

- **GoalCheckpoint**：将 Goal 状态快照到独立 checkpoint（与 Phase 5 复用）
- **GoalCancel**：级联取消子 Goal（已实现 cancel_event 框架，缺持久化）
- **GoalReplan**：失败时自动重排 DAG 拓扑
- **DAG 节点上限可配置**：通过环境变量或 CLI flag 调整

---

## 8. Git 标签建议

```bash
git tag -a phase-13-multi-goal-orchestration -m "Phase 13: Multi-Goal Orchestration (Parent-Child + DAG + Cross-Goal Reuse)"
```

---

## 9. 总结

Phase 13 实现了 trae-multi-agent 的多 Goal 编排能力，核心特性：

1. **DAG 调度**：拓扑排序 + 环检测 + barrier 同步
2. **续跑状态机**：5 状态 + force 标志 + 自动 ABANDONED 标记
3. **跨 Goal 语义复用**：多语言 embedder + 完整审计链
4. **V2 零修改集成**：通过 `register_executor` 公共 API 桥接
5. **进程隔离**：ProcessPoolExecutor 避免 fcntl / GIL 死锁
6. **Schema 向后兼容**：自动从 v12 迁移到 v13
7. **完整 CLI**：9 个新 flag 支持所有编排能力
8. **报告生成**：JSON + Markdown 双格式 + 50 节点截断

**测试覆盖**：83/83 Phase 13 测试通过；201/201 跨 Phase 测试通过。

**下一步**：根据用户优先级选择 Phase 14 方向（DAG 节点上限可配置 / GoalCheckpoint / GoalReplan / 跨 Agent 协作）。
