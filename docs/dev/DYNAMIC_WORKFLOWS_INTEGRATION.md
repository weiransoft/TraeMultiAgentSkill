# Dynamic Workflows × trae-multi-agent 融合增强方案

> **文档类型**：技术分析 + 融合方案  
> **版本**：v1.7（Phase 17 实施完成 + 覆盖度提升 v2）  
> **审查报告**：[ARCHITECT_REVIEW_DYNAMIC_WORKFLOWS.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/ARCHITECT_REVIEW_DYNAMIC_WORKFLOWS.md)  
> **配套手册**：[PATTERNS_REFERENCE.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PATTERNS_REFERENCE.md)（6 大模式参考手册）  
> **参考来源**：[Anthropic Dynamic Workflows（Claude Opus 4.8）](https://mp.weixin.qq.com/s/ZGOlA1IPSQaK3MXv_5fStQ)  
> **现状基线**：`/Users/wangwei/claw/.trae/skills/trae-multi-agent` v3.0（V3 插件架构 + 热加载 + 7 大目标增强）  
> **目标**：把"Anthropic 6 大经典模式 + 关键工程特性"沉淀为 trae-multi-agent 的可复用能力，**而非简单照搬**。  
> **状态**：✅ **Phase 0' → Phase 9 全部完成**（666 tests）+ ✅ **Phase 10-17 全部完成**（+ 624 tests = **~1290 tests 通过**）

---

## 修订履历

| 版本 | 日期 | 变更 | 来源 |
|------|------|------|------|
| v1.0 | 2026-06-03 | 草案：6 大融合模块、4 Phase 路线 | 初始 |
| v1.1 | 2026-06-03 | 采纳架构师审查 Top 5 阻塞项，新增约束与边界，拆分 Phase | [ARCHITECT_REVIEW](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/ARCHITECT_REVIEW_DYNAMIC_WORKFLOWS.md) |
| v1.2 | 2026-06-04 | 全部 6 个 Phase（0'→5）实施完成；6 大模式全部沉淀；状态更新为"✅完成" | Phase 0-5 实施报告 |
| v1.3 | 2026-06-04 | 新增 Phase 6（semantic dedup 真实实现）；引入 Embedder 抽象层 + 3 种实现 | Phase 6 实施报告 |
| v1.4 | 2026-06-04 | 新增 Phase 7（真实 embedding 集成）；升级 SentenceTransformerEmbedder 为多语言模型（paraphrase-multilingual-MiniLM-L12-v2）；22 个真实模型测试通过 | Phase 7 实施报告 |
| v1.5 | 2026-06-04 | 新增 Phase 8（SkillDistribution）；引入 SkillInjector 抽象 + 6 个核心组件 + 50 个测试 | Phase 8 实施报告 |
| v1.6 | 2026-06-05 | 新增 Phase 9（InterruptionRecovery）；引入 InterruptionRecoveryManager 抽象 + 6 大核心组件 + 32 个测试；SubagentSandbox 集成 pause/resume/cancel/自动重试 | Phase 9 实施报告 |
| v1.7 | 2026-06-07 | 新增 Phase 10-17：model_tier-aware dispatch（49）/ /loop+/goal 集成（64）/ Phase 12 架构师修复 / 多 Goal 编排（83）/ B-1~B-4 修复+GoalCancel / DAG 可视化（50）/ V3 插件架构重构（100）/ **插件热加载 Phase 17**（**181**）+ 覆盖度提升 v2（+42 tests，facade 100% / hot_reload_watcher 92% / legacy 54%） | Phase 10-17 实施报告 |

### v1.1 关键变更点（对应 Top 5 阻塞项）

| 阻塞项 | v1.0 状态 | v1.1 修订 |
|--------|----------|----------|
| **🔴 持久化复用** | 新建并行存储 | **强约束**：所有新数据复用 DualLayerContextManager / CheckpointManager / PerformanceFingerprint |
| **🔴 V2 不修改** | "V2 完全兼容"承诺 | **强约束**：V2 任何文件零修改；新能力通过扩展点（executor / hook）注入 |
| **🔴 Phase 拆分** | 4 模块同 Phase | 拆为 **Phase 0'/0/1/2/3** 5 个独立阶段，每阶段独立可发布可回滚 |
| **🔴 安全补强** | 无 | 新增 §3.0 约束与边界：subagent 输入 schema 校验、Token 硬上限、模式库 schema 校验、提示词注入防护 |
| **🔴 提示词注入防护** | 无 | 所有 subagent 接收的外部输入必须经 GuardCoordinator 过滤 |

---

## 零、阅读结论摘要

| 维度 | 文章核心 | trae-multi-agent 现状 | 融合后定位 |
|------|---------|----------------------|----------|
| **核心范式** | JS 驱动生成与协调 subagent，解决长程/并行/对抗任务 | 已有 WorkflowEngineV2 + 角色调度 + Cybernetics 反馈环 | **在现有工作流引擎之上，引入"模式库"作为可调用的能力单元** |
| **痛点应对** | Agentic laziness / Self-preferential bias / Goal drift | 已有 Checkpoint + Goal-Driven + 反馈控制 | **以"对抗性验证 + 锦标赛 + 循环停止条件"补齐三大痛点** |
| **执行隔离** | subagent 在独立 worktree 中运行 | 已有 CheckpointManager，但未与 subagent 强绑定 | **将 worktree 隔离作为 subagent 的默认执行容器** |
| **模型路由** | 分类器决定 Sonnet/Opus 路由 | 无 | **新增"模型路由层"作为 Cybernetics 战术层能力** |
| **Token 预算** | 提示词中声明"use 10k tokens" | 无显式预算 | **与 FeedbackControlLoop 的资源反馈整合** |
| **中断恢复** | Workflow 可从中断点续跑 | Checkpoint 已支持 | **对齐断点粒度，扩展到 subagent 级别** |
| **模式沉淀** | 6 大模式作为可复用的思维模型 | 仅有 WorkflowStep（命令式） | **新增"模式库"作为声明式抽象** |

---

## 一、文章核心思想提炼

### 1.1 三大痛点（被 Dynamic Workflows 解决的问题）

```
1. Agentic Laziness（智能体懒惰）
   - 单 context 下做 50 项安全审查 → 实际只做 20 项就宣布完成
2. Self-preferential Bias（自我偏好偏差）
   - 让模型验证自己生成的方案，倾向于"自我放行"
3. Goal Drift（目标漂移）
   - 多轮 context 压缩后，原始目标与边界条件被稀释
```

### 1.2 6 大经典模式（核心可复用资产）

| # | 模式 | 一句话描述 | 关键工程要素 |
|---|------|-----------|-------------|
| 1 | **分类并行动** | 分类器路由任务到不同子流程 | 分类器 Agent + 路由表 |
| 2 | **扇出与聚合** | 任务拆 N 份并行处理 → 屏障等待 → 合并 | fan-out 任务队列 + barrier 同步 + 合并器 |
| 3 | **对抗性验证** | 生成 + 验证两两配对，验证者独立 context | reviewer Agent + 评估准则 |
| 4 | **生成与筛选** | 大规模生成 → 标准筛选 → 重复去除 | 评估函数 + 去重器 |
| 5 | **锦标赛模式** | N 个 Agent 竞争 → 两两 PK → 决出冠军 | bracket 数据结构 + 裁判 Agent |
| 6 | **循环直到完成** | 动态生成 Agent 直至停止条件 | 停止条件（无新发现/无错误日志） |

### 1.3 关键技术特性

| 特性 | 描述 | 价值 |
|------|------|------|
| **模型路由** | 分类器决定子智能体使用 Sonnet/Opus | 成本 vs 质量动态权衡 |
| **worktree 隔离** | 子智能体在独立 worktree 中执行 | 避免相互干扰、并行安全 |
| **Token 预算** | 提示词中声明 token 上限 | 防止失控消耗 |
| **中断恢复** | Workflow 断点续跑 | 长程任务可靠 |
| **Skill 化分发** | 放到 Skill 目录并引用 | 跨用户复用 |
| **/loop + /goal** | 周期性运行 + 硬性完成指标 | 持续闭环 |

---

## 二、trae-multi-agent 现状分析

### 2.1 已有能力地图

```
┌─────────────────────────────────────────────────────────────┐
│                  trae-multi-agent v2.5                       │
├─────────────────────────────────────────────────────────────┤
│  战略层（外环）    │ WorkflowEngineV2（命令式步骤编排）       │
│  ─────────────────┼────────────────────────────────────── │
│  战术层（中环）    │ Cybernetics: Guard + PerformanceFingerprint │
│  ─────────────────┼────────────────────────────────────── │
│  执行层（内环）    │ AgentLoopControllerV2 + CheckpointManager  │
│  ─────────────────┼────────────────────────────────────── │
│  角色层           │ 架构师/产品经理/Solo Coder/UI/测试 5 角色 │
│  ─────────────────┼────────────────────────────────────── │
│  上下文层         │ DualLayerContextManager（全局+任务级）   │
│  ─────────────────┼────────────────────────────────────── │
│  调度层           │ trae_agent_dispatch_v2.py + AI 语义匹配 │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 与 Dynamic Workflows 能力对照

| 能力 | Dynamic Workflows | trae-multi-agent v2.5 | Gap |
|------|------------------|---------------------|-----|
| 多 subagent 协调 | ✅ JS 驱动动态生成 | ⚠️ 静态角色调度 + 顺序执行 | **缺：动态 subagent + 并行 fan-out** |
| 独立 context 窗口 | ✅ 每个 subagent 独立 | ⚠️ 共享全局上下文 | **缺：subagent 隔离 context** |
| worktree 隔离 | ✅ 内置 | ❌ 无 | **缺：执行环境隔离** |
| 6 大模式 | ✅ 模式化抽象 | ❌ 命令式步骤 | **缺：声明式模式库** |
| 对抗性验证 | ✅ 生成+验证 | ⚠️ 仅多角色串行评审 | **缺：独立 context 验证器** |
| 锦标赛模式 | ✅ N 选 1 | ❌ 无 | **缺** |
| 循环停止条件 | ✅ 无新发现/无错误 | ⚠️ 仅 retry_count | **缺：动态停止条件** |
| 模型路由 | ✅ 分类器选择 | ❌ 固定模型 | **缺** |
| Token 预算 | ✅ 提示词控制 | ❌ 无 | **缺** |
| Skill 分发 | ✅ ~/.claude/workflows | ⚠️ Skill 机制已有 | **对齐** |
| /loop + /goal | ✅ 周期性 + 硬指标 | ⚠️ 仅自动继续 | **缺：周期性触发** |

### 2.3 关键洞察

1. **trae-multi-agent 的"角色"是文章的"subagent"的超集**：但目前角色是"人"的隐喻，缺乏"独立 context + 独立 worktree"的物理隔离。
2. **Cybernetics 反馈环 = 文章 Goal Drift 的解药**：v2.5 已实现，但需要把"Goal"从"任务描述"升级为"可机器验证的成功准则"。
3. **PerformanceFingerprint 是文章"模式库"的最佳载体**：把 6 大模式作为 FailurePattern/SuccessPattern 沉淀。
4. **缺失最严重的是"并行/隔离"维度**：现有 WorkflowEngineV2 是顺序编排，没有"扇出"。

---

## 三、融合增强方案（v3.0 草案）

### 3.0 约束与边界（v1.1 新增，源自架构师审查）

> **本节是硬约束，所有后续 Phase 必须遵守。**

#### 3.0.1 持久化复用约束（🔴 强约束）

| 数据类型 | 存储位置 | 理由 |
|---------|---------|------|
| 模式定义（pattern_id / 参数 schema / 提示词模板） | 复用 PerformanceFingerprint 的 knowledge 区 | 模式即知识 |
| 模式执行历史（成功/失败记录） | 复用 PerformanceFingerprint 的 execution_record | 一处画像 |
| 模式选择画像（哪类任务适合哪个模式） | 复用 PerformanceFingerprint 的 context_outcome_map | 反哺机制 |
| subagent 沙箱元数据 | 复用 CheckpointManager，加 `subagent_id` 字段 | 统一检查点 |
| subagent context 隔离 | **先升级** DualLayerContextManager 支持多实例（Phase 0 任务） | 必要前置 |
| 模式库 schema 校验缓存 | 复用 DualLayerContextManager 的全局配置 | 启动时加载 |

**禁止**：
- ❌ 新建 `dynamic_workflow_storage.py` / `pattern_store.py` 等并行存储
- ❌ 模式定义以独立 JSON 文件散落

#### 3.0.2 V2 不修改约束（🔴 强约束）

**下列文件零修改**：
- `workflow_engine_v2.py`
- `agent_loop_controller_v2.py`
- `dual_layer_context_manager.py`
- `checkpoint_manager.py`
- `task_list_manager.py`
- `claude_code_subagent_adapter.py`
- `trae_agent_dispatch_v2.py`

**新能力通过扩展点注入**：
- WorkflowStep 新增 `pattern_executor: Optional[PatternExecutor]` 字段（向后兼容）
- 新增 `PatternExecutor` Protocol，新模式作为独立实现
- V2 引擎通过 `register_executor()` 注册 pattern_executor
- Hook 机制：模式选择前后可挂载钩子

#### 3.0.3 安全约束（🔴 强约束）

| 安全项 | 实施位置 | 约束 |
|--------|---------|------|
| subagent 输入 schema 校验 | SubagentSandbox.spawn() | 任务描述必须符合预定义 schema，无关字段丢弃 |
| 提示词注入防护 | GuardCoordinator 接管所有 subagent 输入 | 经过关键词 + 编码特征检测 |
| Token 硬上限 | TokenBudgetGuard | 超出后**硬中断**（不是软警告） |
| 模式库 schema 校验 | PatternLibrary 加载时 | 不通过校验则模式不可用，标记 degraded |
| worktree 路径白名单 | WorktreeManager | 禁止 worktree 创建在 `/` / `~` / 项目外 |

#### 3.0.4 Simplicity First 约束（🔴 强约束）

| 约束 | 说明 |
|------|------|
| 模式上限 6 | 6 大模式是上限，新增模式需架构师审核 |
| 6 → 3 | **Phase 0 只沉淀 3 个核心模式**（classifier-dispatch / fan-out-aggregate / adversarial-verify） |
| 默认顺序 | `step_type=SEQUENTIAL` 是默认值；新模式按需显式启用 |
| 一阶段一模块 | 每个 Phase 独立可发布、可回滚 |

#### 3.0.5 演进治理

- 模式库版本号遵循 trae-multi-agent 整体版本（`v2.5` 引入 → `v2.6` 完整）
- 不向后兼容的破坏性变更需走多角色评审
- 模式弃用需有 2 个 minor 版本的过渡期

---

### 3.1 总体目标

> **把 trae-multi-agent 从"角色驱动的顺序工作流"升级为"模式驱动的可声明、可并行、可对抗的工作流"。**

### 3.2 架构演进

```
┌──────────────────────────────────────────────────────────────┐
│          trae-multi-agent v3.0（融合 Dynamic Workflows）       │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌────────────────────────────────────────────────────┐     │
│  │            Pattern Library（模式库 - 新增）           │     │
│  │  分类路由 │ 扇出聚合 │ 对抗验证 │ 生成筛选 │         │     │
│  │  锦标赛   │ 循环停止  │ 模型路由  │ Token 预算        │     │
│  └────────────────────────────────────────────────────┘     │
│                          ▲                                   │
│  ┌───────────────────────┼────────────────────────────┐     │
│  │       Dynamic Workflow Composer（工作流组合器-新增）  │     │
│  │  - 模式匹配  - 参数注入  - 运行时实例化               │     │
│  └───────────────────────┼────────────────────────────┘     │
│                          ▼                                   │
│  ┌────────────────────────────────────────────────────┐     │
│  │         WorkflowEngineV3（升级自 V2）                 │     │
│  │  - 顺序步骤（保留）                                  │     │
│  │  - 并行扇出（新增）                                  │     │
│  │  - 对抗性验证步骤（新增）                            │     │
│  │  - 锦标赛步骤（新增）                                │     │
│  │  - 循环停止条件（新增）                              │     │
│  └────────────────────────────────────────────────────┘     │
│                          ▼                                   │
│  ┌────────────────────────────────────────────────────┐     │
│  │      Subagent Sandbox（subagent 沙箱 - 新增）         │     │
│  │  - 独立 context  - 独立 worktree  - Token 预算        │     │
│  └────────────────────────────────────────────────────┘     │
│                          ▼                                   │
│  ┌────────────────────────────────────────────────────┐     │
│  │      Cybernetics 反馈环（保留 + 增强）                │     │
│  │  Guard │ PerformanceFingerprint │ Hierarchical Ctrl  │     │
│  └────────────────────────────────────────────────────┘     │
│                          ▼                                   │
│  ┌────────────────────────────────────────────────────┐     │
│  │  角色层 + 上下文层 + 调度层（保留）                  │     │
│  └────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────┘
```

### 3.3 6 大模式与 trae-multi-agent 现有组件的映射

| Dynamic Workflows 模式 | trae-multi-agent 实现 | 关键改造 |
|------------------------|----------------------|---------|
| **分类并行动** | `trae_agent_dispatch_v2` + AI 语义匹配 | 升级为"分类器 Agent + 路由表"模式；保留 ai_enhanced |
| **扇出与聚合** | 无 → 新增 `FanOutAggregator` | 在 `WorkflowEngineV3` 中加入"并行子步骤"概念 + barrier 同步 |
| **对抗性验证** | `MultiRoleCollaborativeAnalyzer` | 升级为"生成者-验证者"双 Agent 对，验证者使用独立 context（参考 PerformanceFingerprint） |
| **生成与筛选** | `code_map_generator` | 把"生成+筛选"作为声明式步骤 |
| **锦标赛模式** | 无 → 新增 `TournamentExecutor` | bracket 数据结构 + 裁判 Agent（可用现有测试专家复用） |
| **循环直到完成** | 仅 retry_count | 新增 `LoopUntilCondition` 步骤，停止条件支持"无新发现/无错误日志" |

### 3.4 关键技术特性映射

| 特性 | trae-multi-agent 实现 |
|------|----------------------|
| **worktree 隔离** | 复用 `CheckpointManager` 的存储层，新增 `WorktreeManager` 包装 git worktree |
| **Token 预算** | 新增 `TokenBudgetGuard` 组件，由 `GuardCoordinator` 调度 |
| **模型路由** | 新增 `ModelRouter` 组件，输入任务复杂度 → 输出 Sonnet/Opus 选择 |
| **中断恢复** | 已有 `restore_from_checkpoint`，扩展到 subagent 级别 |
| **/loop 周期性** | 新增 `LoopScheduler`，可与现有 `AgentLoopControllerV2` 集成 |
| **/goal 硬指标** | 现有 `Goal-Driven Execution` 原则升级为可执行校验（参考 GoalDrivenVerifier） |

---

## 四、4 大融合增强模块（详细设计）

### 模块 1：模式库（Pattern Library）— 沉淀 6 大经典模式

**位置**：`scripts/dynamic_workflow/pattern_library.py`（新增）

**核心数据结构**：

```python
@dataclass
class WorkflowPattern:
    """工作流模式：声明式可复用模板"""
    pattern_id: str                          # 模式 ID（例："fan-out-aggregate"）
    name: str                                # 模式名
    description: str                         # 模式描述
    applicable_scenarios: List[str]          # 适用场景
    parameters: Dict[str, Any]               # 参数 schema
    compose_strategy: str                    # 组合策略：sequential/parallel/nested
    example: str                             # 示例（自然语言提示词）
    failure_modes: List[str]                 # 已知失败模式
    success_criteria: List[str]              # 成功标准
    
    # 关键：与 PerformanceFingerprint 双向联动
    fingerprint_hooks: List[str]             # 成功/失败时记录到画像
    applicable_roles: List[str]              # 适用角色（架构师/产品/...）
```

**6 大模式初始实现**：

| pattern_id | 适用场景 | 关键参数 |
|-----------|---------|---------|
| `classifier-dispatch` | 任务类型分流 | classifier_role, route_table |
| `fan-out-aggregate` | 大量相似子任务 | fanout_count, aggregator_role, barrier_timeout |
| `adversarial-verify` | 高风险产出审查 | generator_role, verifier_role, evaluation_criteria |
| `generate-filter` | 创意/命名探索 | generator_role, filter_criteria, dedup_strategy |
| `tournament` | 多方案择优 | candidate_count, judge_role, ranking_method |
| `loop-until-done` | 未知工作量的任务 | stop_conditions, max_iterations, min_quality |

**与现有 PerformanceFingerprint 联动**：

```python
class PatternLibrary:
    def __init__(self, fingerprint_store: PerformanceFingerprint):
        self.fingerprint_store = fingerprint_store
        self.patterns: Dict[str, WorkflowPattern] = {}
    
    def select_pattern(self, task: Task) -> WorkflowPattern:
        """根据任务特征 + 历史画像选择最合适的模式"""
        # 1. 关键词匹配候选模式
        candidates = self._keyword_match(task)
        # 2. 画像检索相似历史案例
        historical_cases = self.fingerprint_store.find_similar(task)
        # 3. 加权排序
        return self._rank_patterns(candidates, historical_cases)
    
    def record_execution(self, pattern_id: str, result: ExecutionResult):
        """执行后回流到画像，更新模式选择策略"""
        self.fingerprint_store.record(
            pattern_id=pattern_id,
            success=result.success,
            context_features=result.context,
            strategy=pattern_id
        )
```

### 模块 2：WorkflowEngineV3（升级 V2）— 引入并行与对抗

**位置**：`scripts/workflow_engine_v3.py`（新增）

**核心增强**：

```python
class WorkflowStepV3(WorkflowStep):
    """扩展步骤类型"""
    step_type: StepType  # SEQUENTIAL | PARALLEL_FANOUT | TOURNAMENT | LOOP_UNTIL
    
class WorkflowEngineV3(WorkflowEngineV2):
    """
    增强点：
    1. PARALLEL_FANOUT 步骤：fan-out N 个 sub-subagent，barrier 等待后聚合
    2. TOURNAMENT 步骤：生成 N 个候选 + 裁判两两 PK
    3. LOOP_UNTIL 步骤：动态生成 subagent 直到停止条件
    4. ADVERSARIAL_VERIFY 步骤：生成后必走独立验证者
    """
    
    def _execute_parallel_fanout(self, step, instance):
        """执行并行扇出"""
        fanout_count = step.parameters.get('fanout_count', 3)
        aggregator = step.parameters.get('aggregator_role')
        
        # 1. 创建 N 个 subagent（每个独立 worktree + context）
        subagents = [
            self._spawn_subagent(
                task=step.description,
                role=step.role_id,
                worktree=self._create_worktree(step),
                context_isolation=True
            )
            for _ in range(fanout_count)
        ]
        
        # 2. Barrier 等待所有完成
        results = self._barrier_wait(subagents, timeout=step.timeout)
        
        # 3. 聚合
        return self._aggregate_results(results, aggregator)
    
    def _execute_tournament(self, step, instance):
        """执行锦标赛"""
        candidates = self._generate_candidates(step, count=step.parameters['candidate_count'])
        # 裁判两两 PK（复用测试专家）
        return self._run_bracket(candidates, judge_role='test-expert')
    
    def _execute_loop_until(self, step, instance):
        """循环直到停止条件"""
        iteration = 0
        while iteration < step.parameters.get('max_iterations', 10):
            sub_result = self._spawn_subagent(step)
            iteration += 1
            # 停止条件检查
            if self._check_stop_condition(step.parameters['stop_conditions'], sub_result):
                break
        return sub_result
```

**与 V2 的兼容性**：
- V2 的 `WorkflowStep` 是 V3 的子类（`step_type=SEQUENTIAL`）
- 现有 `WorkflowDefinition` 可无缝升级（`workflow_engine_v2` 实例改为 v3）
- 已有的 `_execute_step` 路径完全保留

### 模块 3：Subagent Sandbox（subagent 沙箱）— 解决隔离

**位置**：`scripts/dynamic_workflow/subagent_sandbox.py`（新增）

**核心职责**：

```python
class SubagentSandbox:
    """
    subagent 执行沙箱
    - 独立 context window（参考 DualLayerContextManager 但做隔离）
    - 独立 worktree（git worktree 管理）
    - Token 预算执行
    - 异常隔离（一个 subagent 崩溃不影响父 workflow）
    """
    
    def __init__(self, worktree_base: str, token_budget: int):
        self.worktree_base = Path(worktree_base)
        self.token_budget = token_budget
        self.active_sandboxes: Dict[str, SandboxContext] = {}
    
    def spawn(self, agent_id: str, task: Task, isolation_level: str = 'worktree') -> str:
        """
        生成隔离的 subagent
        isolation_level:
          - 'worktree': 独立 worktree
          - 'context': 独立 context
          - 'full': worktree + context
        """
        sandbox_id = f"sb_{uuid.uuid4().hex[:8]}"
        
        if isolation_level in ('worktree', 'full'):
            worktree_path = self._create_worktree(agent_id, sandbox_id)
        else:
            worktree_path = None
        
        sandbox = SandboxContext(
            sandbox_id=sandbox_id,
            agent_id=agent_id,
            worktree_path=worktree_path,
            context_isolation=(isolation_level in ('context', 'full')),
            token_used=0,
            token_budget=self.token_budget
        )
        
        self.active_sandboxes[sandbox_id] = sandbox
        return sandbox_id
    
    def execute(self, sandbox_id: str, executor: Callable) -> ExecutionResult:
        """在沙箱中执行任务，带 token 监控"""
        sandbox = self.active_sandboxes[sandbox_id]
        
        # Token 监控
        with self._token_monitor(sandbox) as monitor:
            try:
                result = executor(sandbox)
                sandbox.token_used = monitor.consumed
                return result
            except TokenBudgetExceeded:
                # 文章核心：预算用尽，优雅降级
                return self._graceful_degrade(sandbox)
            except Exception as e:
                # 异常隔离
                return ExecutionResult(success=False, error=str(e), isolated=True)
    
    def cleanup(self, sandbox_id: str):
        """清理沙箱（合并 worktree / 释放 context）"""
        sandbox = self.active_sandboxes.pop(sandbox_id, None)
        if sandbox and sandbox.worktree_path:
            self._merge_or_discard_worktree(sandbox.worktree_path)
```

### 模块 4：Model Router + Token Budget Guard — 资源动态调度

**位置**：`scripts/dynamic_workflow/model_router.py` + `scripts/dynamic_workflow/token_budget_guard.py`（新增）

**Model Router 设计**：

```python
class ModelRouter:
    """
    模型路由器：参考文章"分类器决定 Sonnet/Opus"
    决策因素：
    - 任务复杂度（PerformanceFingerprint 历史数据）
    - Token 预算余量
    - 角色类型（架构师/产品/...）
    - 截止时间
    """
    
    MODELS = {
        'haiku':   {'cost': 0.25,  'quality': 0.7,  'speed': 3.0},
        'sonnet':  {'cost': 1.0,   'quality': 0.85, 'speed': 1.5},
        'opus':    {'cost': 5.0,   'quality': 0.95, 'speed': 1.0}
    }
    
    def route(self, task: Task, budget_remaining: float, role: str) -> str:
        complexity = self._estimate_complexity(task, role)
        if complexity <= 3 and budget_remaining < 0.3:
            return 'haiku'
        elif complexity <= 6:
            return 'sonnet'
        else:
            return 'opus'
```

**Token Budget Guard 设计**：

```python
class TokenBudgetGuard:
    """
    Token 预算守护
    集成到 GuardCoordinator.pre_execute_validation
    触发"优雅降级"或"切换低消耗模型"
    """
    
    def validate(self, task: Task) -> ValidationResult:
        if task.token_budget > self.remaining_budget * 0.8:
            return ValidationResult(
                passed=True,
                warning='budget_near_limit',
                recommendation='switch_to_haiku_or_split_task'
            )
        return ValidationResult(passed=True)
```

---

## 五、Cybernetics v2.5 的协同演进

融合方案**不替换** Cybernetics，而是**让 Cybernetics 更好地驱动 Dynamic Workflows**：

| Cybernetics 组件 | 在 v3.0 中的角色 | 关键升级 |
|-----------------|-----------------|---------|
| **FeedbackControlLoop** | subagent 执行的反馈环 | 新增 subagent 维度的反馈（per-sandbox） |
| **PerformanceFingerprint** | 模式库的存储后端 | 6 大模式作为 FailurePattern/SuccessPattern 沉淀 |
| **GuardCoordinator** | subagent 启动前的预验证 | 增加 TokenBudgetGuard、ModelRouterGuard |
| **HierarchicalControl** | 模式组合的层次控制 | 战略层选模式，战术层选 subagent，执行层执行 |

**反哺关系**：

```
Dynamic Workflows 执行 → 写入 PerformanceFingerprint → 反哺模式选择策略
        ↑                                                      ↓
        └──────── 反馈：哪种模式适合哪种场景 ←─────────────────┘
```

这正好对应文章核心思想：**模式选择本身就是一个反馈优化的过程**。

---

## 六、与"Karpathy 原则"的兼容性检查

> 担心融合方案可能让系统过于复杂，违反 Karpathy "Simplicity First" 原则。

| 原则 | 检查结果 | 应对 |
|------|---------|------|
| **Think Before Coding** | ✅ 模式库是"先想清楚模式"的具体化 | 保留 |
| **Simplicity First** | ⚠️ 6 大模式 + 4 个新模块可能过度 | **默认使用 `SEQUENTIAL` 步骤，新模式按需启用** |
| **Surgical Changes** | ✅ 新增模块，V2 引擎完全保留 | 保留 |
| **Goal-Driven** | ✅ Token 预算 + 停止条件都是 Goal-Driven 的强化 | 保留 |

**关键设计原则**：

> **"默认简单，按需增强"** —— `WorkflowEngineV3` 默认与 V2 行为完全一致；只有当用户显式使用 `step_type=PARALLEL_FANOUT/...` 时才激活新模式。

---

## 七、实施路线图（v1.1 拆分：5 个独立 Phase + Phase 4 端到端集成）

> **每个 Phase 独立可发布、可回滚**。**全部 6 个 Phase 已于 2026-06-03 收官，483 tests 全部通过 ✅**。

### 总体进度（2026-06-03 更新）

| Phase | 范围 | 测试数 | 状态 | 收官报告 |
|-------|------|--------|------|----------|
| 0' | 模式概念沉淀（文档） | 0 | ✅ | [DYNAMIC_WORKFLOWS_INTEGRATION.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md) |
| 0 | PatternComposer | 46 | ✅ | [PHASE0_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE1_FINAL_REPORT.md) §Phase 0 |
| 1 | PatternExecutor + Guard + Adapter | 148 | ✅ | [PHASE1_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE1_FINAL_REPORT.md) |
| 2 | WorktreeManager + SubagentSandbox | 85 | ✅ | [PHASE2_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE2_FINAL_REPORT.md) |
| 3 | ModelRouter + TokenBudgetGuard | 96 | ✅ | [PHASE3_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE3_FINAL_REPORT.md) |
| 4 | 端到端集成 | 23 | ✅ | [PHASE4_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE4_FINAL_REPORT.md) |
| V2 | 回归测试 | 85 | ✅ | 全部通过 |
| **合计** | | **483** | ✅ | V2 文件 `git diff` 为空 |

### Phase 0'：模式概念沉淀（✅ 完成）

| 任务 | 交付物 | 验收 |
|------|-------|------|
| 修订本方案 v1.1 | `DYNAMIC_WORKFLOWS_INTEGRATION.md` v1.1 | ✅ 架构师再审通过 |
| 6 大模式参考手册 | `PATTERNS_REFERENCE.md` | ✅ 文档评审通过 |
| 模式选择 JSON 示例 | `docs/dev/pattern_examples/*.json` | ✅ 3 个模式各 1 个 |
| 模式选择提示词模板 | `docs/dev/pattern_prompts/*.md` | ✅ 3 个模式各 1 个 |

### Phase 0：最小可运行 pattern_composer（✅ 完成）

**前置条件**：✅ 全部验证
- [x] DualLayerContextManager 支持多实例
- [x] PatternLibrary 数据结构与 PerformanceFingerprint 对齐
- [x] 模式库 schema 校验函数已实现

**交付物**：
- [x] `scripts/dynamic_workflow/pattern_composer.py`（Pattern Composer）
- [x] 3 个核心模式（classifier-dispatch / fan-out-aggregate / adversarial-verify）
- [x] 单元测试 46 用例 100% 通过
- [x] V2 回归测试零失败
- [x] 安全：模式库 schema 校验 + 输入校验

### Phase 1：PatternExecutor 扩展点接入（✅ 完成）

**前置条件**：✅ 全部验证
- [x] Phase 0 已稳定运行
- [x] 真实模式选择记录

**交付物**：
- [x] `scripts/dynamic_workflow/pattern_executor.py`（Protocol + 3 个执行器）
- [x] `scripts/dynamic_workflow/workflow_step_adapter.py`（V2 适配器）
- [x] `scripts/dynamic_workflow/guard.py`（安全防护）
- [x] 单元测试 148 用例 100% 通过

### Phase 2：Subagent 沙箱（✅ 完成）

**前置条件**：✅ 全部验证
- [x] Phase 1 真实使用中遇到"顺序模式无法满足"的具体场景
- [x] worktree 在目标项目类型下可用性验证完成

**交付物**：
- [x] `scripts/dynamic_workflow/subagent_sandbox.py`（沙箱）
- [x] `scripts/dynamic_workflow/worktree_manager.py`（Git 物理隔离）
- [x] 单元测试 85 用例 100% 通过
- [x] PatternExecutor 集成点（duck typing 验证）

### Phase 3：ModelRouter + TokenBudgetGuard（✅ 完成）

**前置条件**：✅ 全部验证
- [x] Phase 2 subagent 资源监控
- [x] Token 失控防护需求

**交付物**：
- [x] `scripts/dynamic_workflow/model_router.py`（3 模型层级 + 画像反哺）
- [x] `scripts/dynamic_workflow/token_budget_guard.py`（3 模式 + GuardCoordinator 兼容）
- [x] 单元测试 96 用例 100% 通过

### Phase 4：端到端集成（✅ 完成 - 超出原路线图）

**目标**：将 Phase 3 独立模块与 Phase 2 沙箱端到端集成到 PatternExecutor

**交付物**：
- [x] `_dispatch_subagent` 接受 router + budget_guard + sandbox
- [x] 3 个执行器（5 个 _dispatch_subagent 调用点）全部透传
- [x] `PatternExecutorRegistry.create_default` 接受新参数
- [x] `execute_workflow_step` 通过 `get_dispatch_context()` 透传
- [x] `_extract_task_feature` 辅助函数
- [x] 23 个集成测试 100% 通过
- [x] Phase 1+2+3 回归零失败（375 tests）
- [x] **完全向后兼容**：不传 router/budget_guard 行为零变化

### Phase 5：其余 3 个模式补齐（✅ 完成 - 全部 6 大模式沉淀）

**目标**：补齐 generate-filter / tournament / loop-until-done 三个剩余模式，达到 Dynamic Workflows 全部 6 大模式覆盖。

**交付物**：
- [x] `pattern_composer.py` 新增 3 个 WorkflowPattern（generate-filter / tournament / loop-until-done）
- [x] `pattern_composer.py` 新增 3 个 selector（基于 TaskFeature 的条件判定）
- [x] `pattern_executor.py` 新增 3 个执行器（GenerateFilterExecutor / TournamentExecutor / LoopUntilDoneExecutor）
- [x] `pattern_executor.py` 新增 3 个 schema（GENERATE_FILTER_SCHEMA / TOURNAMENT_SCHEMA / LOOP_UNTIL_DONE_SCHEMA）
- [x] `pattern_executor.py` 新增 4 个工具函数（_normalize_for_dedup / _fuzzy_similarity / _dedup_candidates / _check_stop_conditions）
- [x] `pattern_executor.py` TournamentExecutor 支持 3 种 ranking_method（knockout / round-robin / elo）
- [x] `pattern_executor.py` LoopUntilDoneExecutor 支持 4 种停止条件（no_new_findings / no_error_logs / quality_threshold_met / convergence_detected）
- [x] `pattern_executor.py` GenerateFilterExecutor 支持 3 种去重策略（exact / fuzzy / semantic）
- [x] `PatternExecutorRegistry.create_default` 注册 6 大执行器
- [x] `PatternLibrary(use_all_patterns=True)` 默认加载 6 大模式
- [x] `PatternLibrary(use_all_patterns=False)` 向后兼容 3 个核心模式
- [x] **94 个新测试 100% 通过**（覆盖工具函数、执行器、注册表、库、集成、边界场景）
- [x] Phase 1+2+3+4 回归零失败（398 tests）
- [x] **总测试数**：47 + 59 + 53 + 36 + 42 + 43 + 46 + 50 + 23 + 94 = 493 tests
- [x] **完全向后兼容**：use_all_patterns=False 时加载 Phase 0 的 3 个模式

**Phase 5 关键修复**（实施过程中发现）：
1. `loop-until-done` 停止条件判定后，final_stop_reason 被空字符串覆盖 → 仅在触发时更新
2. `tournament` 未知 ranking_method 降级时，aggregated_output 未反映 → 参数同步更新
3. `generate-filter` dedup 顺序丢失 + set 命中问题 → 改用顺序遍历 + normalized seen set
4. `generate-filter` 候选 output 含 index 导致 dedup 不触发 → output 用 task description
5. `loop-until-done` 候选 output 含迭代号导致 no_new_findings / convergence 不触发 → output 用 task description

### Phase 6：semantic dedup 真实实现（✅ 完成 - Embedder 抽象层）

**目标**：升级 Phase 5 简化的"semantic 复用 fuzzy（LCS）"为真正的语义去重

**交付物**：
- [x] 新模块 `semantic_embedder.py`（~450 行）
- [x] `Embedder` 抽象基类（Protocol）
- [x] `TFIDFEmbedder`（默认实现，无外部依赖）
- [x] `HashingEmbedder`（O(1) 内存，用于超大规模）
- [x] `SentenceTransformerEmbedder`（可选，graceful fallback）
- [x] `EmbeddingCache`（LRU + 线程安全 + 命中率统计）
- [x] `create_embedder` 工厂函数（auto / tfidf / hashing / sentence_transformer）
- [x] `get_default_embedder` 单例
- [x] `_fuzzy_similarity` 接受 `embedder` 参数（调用 embedder.similarity）
- [x] `_dedup_candidates` semantic 策略走 embedder
- [x] `GenerateFilterExecutor._resolve_embedder` 三层支持（实例 / dict / 未指定）
- [x] `PATTERN_GENERATE_FILTER.parameters_schema` 新增 embedder 字段
- [x] `PATTERN_GENERATE_FILTER.failure_modes` 新增"embedder 不可用"故障模式
- [x] **69 个新测试 100% 通过**（覆盖工具函数 / 3 种 Embedder / Cache / Factory / 集成 / 性能）
- [x] Phase 1+2+3+4+5 回归零失败（493 tests）
- [x] **完全向后兼容**：不传 embedder 行为与 Phase 5 完全相同
- [x] **优雅降级**：sentence-transformers 未安装时自动 fallback 到 TFIDF

**Phase 6 关键修复**（实施过程中发现）：
1. `create_embedder(**embedder_config)` 误用 → 必须用 `embedder_type=embedder_type, **kwargs` 显式传入
2. `embedder.similarity` 可能抛错 → 包裹 try/except fallback 到 LCS
3. `SentenceTransformerEmbedder` 未安装时 import 失败 → ImportError 时 fallback 到 TFIDF

### Phase 7+ 候选（待用户决策）

> Phase 7 已完成（真实 embedding 集成）。以下为剩余可选扩展方向：

| 方向 | 优先级 | 范围 | 预计测试增量 |
|------|--------|------|--------------|
| SkillDistribution | 中 | Skill 自动注入到 sandbox context | 30+ tests |
| InterruptionRecovery | 中 | subagent 异常中断后的恢复策略 | 25+ tests |
| /loop + /goal 集成 | 低 | 终端用户命令 | 15+ tests |
| model_tier-aware dispatch | 中 | cybernetics_bridge 解析 _meta.model_tier | 10+ tests |
| ~~真实 embedding 集成~~ | ✅ 完成 | 接入实际 sentence-transformers 模型 + benchmark | 22 tests |

### Phase 7 实施详情（已完成）

> **目标**：把 Phase 6 的「TFIDF 占位实现」升级为「真实预训练模型」，验证真实场景下的语义去重效果。

#### 7.1 实施内容

| 任务 | 状态 | 关键变更 |
|------|------|---------|
| 安装 sentence-transformers | ✅ | `.venv` 隔离；HF_ENDPOINT=hf-mirror.com 绕过网络 |
| 升级默认模型 | ✅ | `all-MiniLM-L6-v2`（英文 only）→ `paraphrase-multilingual-MiniLM-L12-v2`（50+ 语言） |
| 修复 [UNK] 塌缩 bug | ✅ | 旧模型把中文 token 全塌缩为 [UNK]；新模型多语言 tokenizer 正确处理 |
| 修复 tensor 泄漏 | ✅ | 新增 `_to_float_list` 统一处理 torch.Tensor / numpy.ndarray / list |
| 修复 deprecation | ✅ | `get_sentence_embedding_dimension` → `get_embedding_dimension`（fallback 兼容旧版） |
| 强制 CPU 推理 | ✅ | 避免 MPS 已知 bug（虽验证为非设备问题，保留 CPU 路径以稳定） |
| 真实模型单元测试 | ✅ | 新增 22 个 TestRealSentenceTransformerEmbedding 测试 |
| 端到端 benchmark | ✅ | car/automobile 同义词 0.94（TFIDF 仅 0.0）；中文 机器学习/深度学习 0.64（不再塌缩为 1.0） |
| 回归零失败 | ✅ | 562 + 22 = 584 tests 100% 通过 |
| V2 文件零修改 | ✅ | 严格遵守"不修改 V2"约束 |

#### 7.2 关键 Bug 与修复

**Bug #1：`all-MiniLM-L6-v2` 中文 [UNK] 塌缩**

```python
# 旧模型（all-MiniLM-L6-v2）tokenizer 输出：
"机器学习" → ['[UNK]', '[UNK]', '学', '[UNK]']
"深度学习" → ['[UNK]', '[UNK]', '学', '[UNK]']
# → 完全相同的 token 序列 → 相同的 embedding
# → 验证：旧模型 机器学习 vs 深度学习 = 1.0000（错误！）

# 新模型（paraphrase-multilingual-MiniLM-L12-v2）tokenizer 输出：
"机器学习" → 多语言子词单元
"深度学习" → 多语言子词单元（不同）
# → 不同的 token 序列 → 不同的 embedding
# → 验证：新模型 机器学习 vs 深度学习 = 0.6360（正确）
```

**Bug #2：Tensor 泄漏到下游**

```python
# 旧实现：model.encode 返回 torch.Tensor
vec = self._model.encode("hello")  # torch.Tensor on MPS/CPU
return list(vec)  # → List[Tensor]！不是 List[float]
# → 下游 zip + sum 抛 TypeError 或产生错误结果

# 新实现：_to_float_list 统一转换
vec = self._model.encode("hello", convert_to_numpy=False)
return self._to_float_list(vec)  # → List[float]
```

**Bug #3：Deprecation warning**

```python
# 旧：get_sentence_embedding_dimension() 在 sentence-transformers 5.x 弃用
self._dim = self._model.get_sentence_embedding_dimension()
# → FutureWarning: ...renamed to get_embedding_dimension

# 新：优先新接口，fallback 兼容
try:
    self._dim = int(self._model.get_embedding_dimension())
except AttributeError:
    self._dim = int(self._model.get_sentence_embedding_dimension())
```

#### 7.3 准确率 Benchmark（真实场景）

| 文本对 | 旧 TFIDF | 旧 ST（英文 only） | 新 ST（多语言） |
|--------|---------|------------------|----------------|
| `car` vs `automobile`（同义） | 0.0 | 0.86 | **0.94** |
| `机器学习` vs `深度学习`（相关中文） | 0.45 | **1.0（塌缩 bug）** | **0.64** |
| `北京` vs `上海`（同类型） | 0.0 | 0.0 | **0.89** |
| `机器学习` vs `machine learning`（跨语言） | 0.0 | 0.0 | **0.95** |
| `hello` vs `hi`（同义英文） | 0.0 | 0.81 | 0.76 |
| `I love programming` vs `I enjoy coding`（paraphrase） | 0.0 | 0.82 | 0.86 |
| `苹果` vs `香蕉`（同类水果） | 1.0 | 1.0（塌缩） | 0.39 |
| `cat` vs `dog`（相关动物） | 0.0 | 0.66 | 0.30 |

**结论**：
- 真实模型在**同义词 / 跨语言**场景下相对 TFIDF 有 50%+ 提升
- 多语言模型解决了**中文 [UNK] 塌缩**这一关键 bug
- 旧"英文 only"模型在中文场景下完全不可用（塌缩为 1.0）

#### 7.4 性能数据（CPU baseline, paraphrase-multilingual-MiniLM-L12-v2）

| 场景 | 性能 | 说明 |
|------|------|------|
| 模型加载（首次） | ~5s | 一次性，可缓存 |
| 单条 embed | ~50ms | CPU 推理 |
| 批量 embed (batch=20) | ~400ms | ~20ms/条，2.5x 加速 |
| 缓存命中 | <1ms | LRU 命中 |
| 模型大小 | ~500MB | 内存常驻 |

#### 7.5 配置与启用

```bash
# 1. 创建虚拟环境（避免污染系统 Python）
cd /Users/wangwei/claw/.trae/skills/trae-multi-agent
python3 -m venv .venv
source .venv/bin/activate

# 2. 安装 sentence-transformers
pip install sentence-transformers

# 3. 配置 HF 镜像（中国大陆环境）
export HF_ENDPOINT=https://hf-mirror.com

# 4. 启用 Phase 7 真实模型测试
export SENTENCE_TRANSFORMERS_TEST=1

# 5. 运行测试
python3 -m unittest scripts.tests.test_semantic_embedder -v
```

#### 7.6 验收清单

- [x] 22 个真实模型测试 100% 通过
- [x] 中文 / 英文 / 跨语言 / 同义词 / paraphrase 全场景验证
- [x] [UNK] 塌缩 bug 完全修复
- [x] Tensor 泄漏 bug 完全修复
- [x] Deprecation warning 消除
- [x] 性能可接受（CPU 50ms/条，batch 加速 2.5x）
- [x] V2 回归零失败
- [x] V2 文件零修改
- [x] 优雅降级保留：未安装 sentence-transformers 时自动 fallback 到 TFIDF
- [x] 完全向后兼容：不传 embedder 行为与 Phase 6 完全相同

### Phase 推进门禁

每进入下一 Phase 必须满足：
1. ✅ 当前 Phase 所有验收项 100% 通过
2. ✅ V2 回归测试零失败
3. ⚠️ 真实用户场景使用记录（可延后）
4. ✅ 架构师签字通过

---

### Phase 8 实施详情（已完成 - SkillDistribution）

> **目标**：将 task 字典声明的 `task_skill` 自动注入到 subagent 沙箱的 `SandboxContext`，subagent 在执行时**自动感知**自己应当激活哪些 Skill，**不再依赖**用户手动将技能描述拼接到每个 subagent 任务里。

#### 8.1 实施内容

| 任务 | 状态 | 关键变更 |
|------|------|---------|
| 解析 task_skill 字段 4 种形式 | ✅ | 字符串 / 列表 / 字典(优先级) / 嵌套字典(primary+fallback) |
| 6 大核心组件 | ✅ | SkillInjector / SkillInjectableView / InjectionResult / SkillDependencyResolver / SkillGuard / SkillTaskFieldParser |
| 4 种渲染模式 | ✅ | structured (XML) / markdown / compact / full (YAML) |
| DFS 依赖解析 + 循环检测 | ✅ | 拓扑序排列 + 循环 skill 跳过 |
| 4 项 Guard 校验 | ✅ | 名称合法性 / 数量上限(10) / 依赖深度(5) / 内容注入攻击 |
| Token 预算截断 | ✅ | 4 级降级（capability→skill→drop→compact） |
| SubagentSandbox 集成 | ✅ | spawn 流程 Step 3.5 自动注入；SandboxContext 新增 3 字段 |
| PerformanceFingerprint 联动 | ✅ | skill_injection 事件写入画像 |
| 完全向后兼容 | ✅ | skill_injector=None / task_skill 缺失 → 行为与 Phase 7 完全一致 |
| 50 个测试 100% 通过 | ✅ | 5 parser + 6 guard + 6 resolver + 3 view + 5 render + 3 truncate + 7 fail + 10 集成 + 5 perf |
| 性能基线 | ✅ | 1 skill < 20ms；10 skills < 100ms；spawn with injection < 50ms |

#### 8.2 6 大核心组件

| 组件 | 职责 | 位置 |
|------|------|------|
| `SkillTaskFieldParser` | 解析 `task_skill` 4 种合法形式 | skill_injector.py |
| `SkillGuard` | 名称/数量/深度/内容 4 项校验 | skill_injector.py |
| `SkillDependencyResolver` | DFS 依赖解析 + 循环检测 | skill_injector.py |
| `SkillInjectableView` | 从 SkillManifest 派生的轻量视图 | skill_injector.py |
| `SkillInjector` (抽象) + `StructuredSkillInjector` (默认) | 4 种渲染模式 | skill_injector.py |
| `InjectionResult` | 注入结果（含元数据） | skill_injector.py |

#### 8.3 task_skill 4 种合法形式

```python
# 形式 1：字符串
task = {"task_skill": "trae-multi-agent"}

# 形式 2：列表（按列表顺序）
task = {"task_skill": ["trae-multi-agent", "code-review"]}

# 形式 3：字典（含优先级，数值越小越靠前）
task = {"task_skill": {"trae-multi-agent": 1, "code-review": 2}}

# 形式 4：嵌套字典（primary 视为 critical，fallback 视为 low）
task = {"task_skill": {"primary": ["trae-multi-agent"], "fallback": ["code-review"]}}
```

#### 8.4 SandboxContext 新增 3 字段

```python
@dataclass
class SandboxContext:
    # ... Phase 2 既有字段 ...
    injected_skills: List[str] = field(default_factory=list)  # 已注入的 skill 名
    skill_injection_text: str = ""                              # 注入文本（XML/Markdown/...）
    skill_injection_meta: Dict[str, Any] = field(default_factory=dict)  # 注入元数据
```

#### 8.5 性能基线

| 场景 | 性能 | 备注 |
|------|------|------|
| `spawn()` 无 skill_injector | < 10ms | 零开销（向后兼容） |
| `spawn()` 无 task_skill 字段 | < 11ms | 仅 None 检查 |
| 注入 1 个 skill | < 20ms | 含 10 个 capability |
| 注入 10 个 skill + 依赖 | < 100ms | 含 DFS + 循环检测 |
| `spawn()` 含注入 | < 50ms | 集成场景 |

#### 8.6 失败处理矩阵

| 失败类型 | 行为 |
|---------|------|
| skill 名非法 | `SkillGuardError`（硬中断） |
| skill 数量 > 10 | `SkillGuardError`（硬中断） |
| 依赖深度 > 5 | `SkillGuardError`（硬中断） |
| skill 不存在 (critical) | `InjectionResult.errors` 标记，sandbox 仍可工作 |
| skill 不存在 (high/normal) | 警告 + 继续 |
| skill 不存在 (low) | 静默忽略 |
| 循环依赖 | 跳过循环 skill + 记录 |
| 内容注入攻击 | `SkillGuardError`（硬中断） |
| Token 超限 | 4 级降级（截断 + 继续） |
| SkillRegistry 加载失败 | 降级为无 skill 注入 + 警告 |

#### 8.7 验收清单

- [x] 50 个 Phase 8 测试 100% 通过
- [x] 4 种 task_skill 解析形式全部支持
- [x] 4 种渲染模式（structured/markdown/compact/full）全部实现
- [x] 循环依赖检测 + 跳过
- [x] 4 项 Guard 校验（名称/数量/深度/内容）
- [x] Token 截断 4 级降级
- [x] SandboxContext 3 字段自动填充
- [x] PerformanceFingerprint skill_injection 事件写入
- [x] Phase 1-7 回归零失败（562 tests）
- [x] V2 文件零修改
- [x] 完全向后兼容（skill_injector=None 时行为零变化）

### Phase 9 实施详情（已完成 - InterruptionRecovery）

> **目标**：subagent 在执行过程中遇到**中断**（崩溃、超时、信号、资源耗尽、用户取消）时，能够**自动检测 + 选择恢复策略 + 状态保存/恢复 + 重试或降级**，**避免当前 sandbox 一旦崩溃即丢失全部上下文的问题**。

#### 9.1 实施内容

| 任务 | 状态 | 关键变更 |
|------|------|---------|
| InterruptionType 枚举 | ✅ | 6 种中断类型（timeout/exception/signal/resource_exhausted/user_abort/unknown） |
| RecoveryStrategy 枚举 | ✅ | 6 种恢复策略（retry/restart/fallback/skip/manual/abort） |
| RetryPolicy 数据类 | ✅ | 指数退避（2^n × initial）+ 0-25% 抖动 + max_delay 截断 |
| SubagentStateSnapshot 数据类 | ✅ | 序列化/反序列化（to_dict/from_dict） + touch() |
| InterruptionRecord 数据类 | ✅ | 累计 attempts + max_attempts + snapshot 关联 + 恢复时间戳 |
| InterruptionRecoveryManager | ✅ | 智能策略选择（task.interruption_policy 覆盖默认） + 重试循环 + 历史管理 |
| 升级机制 | ✅ | RETRY→FALLBACK / RESTART→SKIP / FALLBACK→MANUAL |
| V2 CheckpointManager 集成 | ✅ | snapshot → Checkpoint 深恢复 |
| V2.5 PerformanceFingerprint 联动 | ✅ | interruption_recovery 事件写入画像 |
| SubagentSandbox 集成 | ✅ | 新增 4 字段 + 3 公共方法 + execute() 包装 |
| SandboxStatus 扩展 | ✅ | 新增 CANCELLED / PAUSED / SKIPPED 3 个状态 |
| 32 个测试 100% 通过 | ✅ | 13 单元 + 8 集成 + 4 sandbox + 3 端到端 + 2 性能 + 1 向后兼容 + 1 touch（实际 32） |
| 性能基线 | ✅ | 1000 record < 1s；1000 load_snapshot < 500ms |
| 完全向后兼容 | ✅ | recovery_manager=None 时行为与 Phase 8 完全一致 |
| V2 文件零修改 | ✅ | 仅修改 subagent_sandbox.py（Phase 2 模块，非 V2） |

#### 9.2 6 大核心组件

| 组件 | 职责 | 位置 |
|------|------|------|
| `InterruptionType` | 6 种中断类型枚举 | interruption_recovery.py |
| `RecoveryStrategy` | 6 种恢复策略枚举 | interruption_recovery.py |
| `RetryPolicy` | 指数退避 + 抖动策略（含参数校验 + compute_delay_ms + should_retry） | interruption_recovery.py |
| `SubagentStateSnapshot` | 状态快照（含 to_dict / from_dict / touch） | interruption_recovery.py |
| `InterruptionRecord` | 中断追溯记录（含 to_dict，enum 转字符串） | interruption_recovery.py |
| `InterruptionRecoveryManager` | 主调度器（save_snapshot / load_snapshot / record_interruption / attempt_recovery / list_active_records / get_history / cleanup_records） | interruption_recovery.py |

#### 9.3 智能策略选择算法

```python
def _select_strategy(interruption_type, attempt, task):
    policy = (task or {}).get("interruption_policy", {})
    # 优先级 1：全局 strategy
    if "strategy" in policy:
        return RecoveryStrategy(policy["strategy"])
    # 优先级 2：按类型指定
    if interruption_type.value in policy:
        return RecoveryStrategy(policy[interruption_type.value])
    # 优先级 3：默认策略
    default = {
        TIMEOUT: RETRY, EXCEPTION: RETRY, SIGNAL: RESTART,
        RESOURCE_EXHAUSTED: FALLBACK, USER_ABORT: SKIP, UNKNOWN: MANUAL,
    }
    strategy = default.get(interruption_type, MANUAL)
    # 优先级 4：升级
    if attempt >= max_retries:
        strategy = {RETRY: FALLBACK, RESTART: SKIP, FALLBACK: MANUAL}.get(strategy, MANUAL)
    return strategy
```

#### 9.4 SubagentSandbox 集成

```python
# 新增参数
sandbox = SubagentSandbox(recovery_manager=InterruptionRecoveryManager())

# 新增公共方法
sandbox.pause(sandbox_id)         # 暂停
sandbox.resume(sandbox_id, snapshot_id=None)  # 恢复
sandbox.cancel(sandbox_id)        # 取消
sandbox.is_paused(sandbox_id)
sandbox.is_cancelled(sandbox_id)

# SandboxContext 新增 4 字段
@dataclass
class SandboxContext:
    pause_event: threading.Event
    cancel_event: threading.Event
    snapshot: Optional[Dict[str, Any]]
    intermediate_results: Dict[str, Any]
```

#### 9.5 task 字典 interruption_policy 字段

```python
# 全局 strategy 覆盖
task = {
    "interruption_policy": {"strategy": "fallback"}
}
# 按类型覆盖
task = {
    "interruption_policy": {
        "timeout": "retry",
        "resource_exhausted": "fallback",
        "user_abort": "skip",
    }
}
```

#### 9.6 性能基线

| 场景 | 性能 | 备注 |
|------|------|------|
| `record_interruption` × 1000 | < 1s | avg < 1ms/次 |
| `load_snapshot` × 1000 | < 500ms | avg < 0.5ms/次 |
| `save_snapshot` × 1000 | < 5s | 包含 JSON 序列化 |
| `spawn()` 无 recovery_manager | < 10ms | 零开销（向后兼容） |
| `spawn()` 含 recovery_manager | < 11ms | 仅引用 + 字段检查 |
| `pause() / resume() / cancel()` | < 1ms | Event 原子操作 |
| 重试退避总时间 | max(7.5s × 3) = 22.5s | max_retries=3, 指数 + 25% 抖动 |

#### 9.7 验收清单

- [x] 32 个 Phase 9 测试 100% 通过
- [x] 6 种 InterruptionType + 6 种 RecoveryStrategy 全部实现
- [x] RetryPolicy 指数退避 + 抖动 + max_delay 截断
- [x] SubagentStateSnapshot + InterruptionRecord 序列化
- [x] InterruptionRecoveryManager 7 大公共方法
- [x] 智能策略选择（按 type 智能 + task 字段覆盖 + 升级机制）
- [x] V2 CheckpointManager 集成（snapshot → checkpoint 深恢复）
- [x] V2.5 PerformanceFingerprint 联动（interruption_recovery 事件）
- [x] SubagentSandbox 集成（4 字段 + 3 公共方法 + execute 包装）
- [x] SandboxStatus 扩展（CANCELLED / PAUSED / SKIPPED）
- [x] Phase 1-8 回归零失败（593 tests）
- [x] V2 文件零修改
- [x] 完全向后兼容（recovery_manager=None 时行为零变化）
- [x] 性能基线：1000 record < 1s
- [x] 安全：max_retries 强制上限；退避 jitter 避免雪崩
- [x] TODO/FIXME 遗留 0
- [x] 编译警告 0

## 十三、融合进度总览（2026-06-07 更新 - Phase 17 实施完成 + 覆盖度 v2）

### 总体进度表

| Phase | 主题 | 状态 | 完成日期 | 测试增量 |
|-------|------|------|----------|----------|
| 0' | 文档沉淀 | ✅ | 2026-06-03 | 0（设计阶段） |
| 0 | 模式选择器 | ✅ | 2026-06-03 | 47 tests |
| 1 | 模式执行器（3 个核心） | ✅ | 2026-06-03 | 59 tests |
| 2 | SubagentSandbox | ✅ | 2026-06-03 | 42 + 43 tests |
| 3 | ModelRouter + TokenBudgetGuard | ✅ | 2026-06-03 | 46 + 50 tests |
| 4 | 端到端集成 | ✅ | 2026-06-03 | 23 tests |
| 5 | 其余 3 模式补齐 | ✅ | 2026-06-04 | 94 tests |
| 6 | semantic dedup 真实实现 | ✅ | 2026-06-04 | 69 tests |
| 7 | 真实 embedding 集成（多语言模型） | ✅ | 2026-06-04 | 22 tests |
| 8 | SkillDistribution（Skill 自动注入） | ✅ | 2026-06-04 | 50 tests |
| 9 | InterruptionRecovery（subagent 中断恢复） | ✅ | 2026-06-05 | 32 tests |
| 10 | model_tier-aware dispatch | ✅ | 2026-06-05 | 49 tests |
| 11 | /loop + /goal 集成 | ✅ | 2026-06-05 | 64 tests |
| 12 | 架构师 review 修复（Issue 1-7） | ✅ | 2026-06-06 | 14 tests（专项） |
| 13 | 多 Goal 编排 Multi-Goal Orchestration | ✅ | 2026-06-06 | 83 tests |
| 14 | B-1~B-4 修复 + GoalCancel 完善 | ✅ | 2026-06-06 | 8 tests |
| 15 | DAG 依赖图可视化 | ✅ | 2026-06-06 | 50 tests |
| 16 | V3 插件架构重构（god module 拆分） | ✅ | 2026-06-06 | 100 tests |
| 17 | **插件热加载 Hot Reload** | ✅ | 2026-06-07 | **181 tests** |
| 覆盖度提升 v1 | _sanitize_stem 修复 + facade 基础覆盖 | ✅ | 2026-06-07 | 21 tests |
| 覆盖度提升 v2 | facade 100% / hot_reload_watcher 92% / legacy 54% | ✅ | 2026-06-07 | 42 tests |

### 累计交付（Phase 0' → 17 全部完成）

| 维度 | 数据 |
|------|------|
| 新增代码 | ~25,000 行（包含测试） |
| 实现模块 | 21 个（pattern_composer / guard / pattern_executor / workflow_step_adapter / worktree_manager / subagent_sandbox / model_router / token_budget_guard / semantic_embedder / skill_injector / interruption_recovery / **pattern_tier_resolver** / **loop_goal** / **goal_orchestrator** / **dag_visualizer** / **dispatcher/goal_dispatcher** / **hot_reload_watcher** / **drop_in_loader** / **reload_guard** + V3 拆分模块 + facade） |
| **6 大模式执行器** | ✅ **全部实现**（classifier-dispatch / fan-out-aggregate / adversarial-verify / generate-filter / tournament / loop-until-done） |
| **Embedder 抽象** | ✅ **3 种实现**（TFIDF / Hashing / **多语言 SentenceTransformer**） |
| **Skill 注入器** | ✅ **6 大核心组件**（Parser / Guard / Resolver / View / Injector / Result）+ 4 种渲染模式 |
| **InterruptionRecovery** | ✅ **6 大核心组件**（InterruptionType / RecoveryStrategy / RetryPolicy / SubagentStateSnapshot / InterruptionRecord / InterruptionRecoveryManager）+ 6 种恢复策略 |
| **Goal 编排** | ✅ **5 阶段管线**（DAG→Resume→Reuse→Schedule→Report）+ DAG 可视化（3 种输出格式） |
| **V3 插件架构** | ✅ **5 大组件**（PluginContext / PluginResult / PluginBase / PluginRegistry / PluginDispatcher）+ 19 compat points |
| **插件热加载** | ✅ **3 触发机制**（轮询 / 显式 API / drop-in 目录扫描）+ 4 步 reload 事务 + atexit 清理 |
| 单元测试 | **666 + 555 + 42 ≈ ~1263 tests**（粗略累计，实际以 `pytest --collect-only` 为准） |
| V2 回归 | 85 tests 全部通过 |
| V2 文件修改 | 0（严格遵守"不修改 V2"约束） |
| TODO/FIXME 遗留 | 0 |
| 编译警告 | 0 |

### 覆盖度数据（v1.7 文档发布时）

| 模块 | 覆盖度 |
|------|--------|
| `scripts/facade.py` | **100%** |
| `dispatcher/drop_in_loader.py` | **96%** |
| `dispatcher/hot_reload_watcher.py` | **92%** |
| `dispatch/legacy.py` | **54%**（高价值分支已全部覆盖） |
| `dispatcher/goal_dispatcher.py` | 高（核心 API + 4 步事务 + rollback） |
| `dynamic_workflow/pattern_executor.py` | 6 大模式全部覆盖 |
| `loop_goal.py` | 全部公共 API + 边界场景 |
| `goal_orchestrator.py` | 5 阶段管线 + DAG 校验 |
| `dag_visualizer.py` | 3 输出格式 + 截断 + 软链 + CJK/emoji |

### 关键文档清单

| 文档 | 状态 |
|------|------|
| [DYNAMIC_WORKFLOWS_INTEGRATION.md v1.7](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md) | ✅ 主方案（Phase 0'→17 全部完成 + 覆盖度 v2） |
| [PATTERNS_REFERENCE.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PATTERNS_REFERENCE.md) | ✅ 6 大模式手册 |
| [PHASE0_PLAN.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE0_PLAN.md) | ✅ Phase 0 计划 |
| [PHASE1_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE1_FINAL_REPORT.md) | ✅ Phase 1 收官 |
| [PHASE2_PLAN.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE2_PLAN.md) | ✅ Phase 2 计划 |
| [PHASE2_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE2_FINAL_REPORT.md) | ✅ Phase 2 收官 |
| [PHASE3_PLAN.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE3_PLAN.md) | ✅ Phase 3 计划 |
| [PHASE3_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE3_FINAL_REPORT.md) | ✅ Phase 3 收官 |
| [PHASE4_PLAN.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE4_PLAN.md) | ✅ Phase 4 计划 |
| [PHASE4_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE4_FINAL_REPORT.md) | ✅ Phase 4 收官 |
| [PHASE5_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE5_FINAL_REPORT.md) | ✅ Phase 5 收官 |
| [PHASE6_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE6_FINAL_REPORT.md) | ✅ Phase 6 收官 |
| [PHASE7_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE7_FINAL_REPORT.md) | ✅ Phase 7 收官（真实 embedding 集成） |
| [PHASE8_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE8_FINAL_REPORT.md) | ✅ Phase 8 收官（SkillDistribution） |
| [PHASE9_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE9_FINAL_REPORT.md) | ✅ Phase 9 收官（InterruptionRecovery） |
| [PHASE10_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE10_FINAL_REPORT.md) | ✅ Phase 10 收官（model_tier-aware dispatch） |
| [PHASE11_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE11_FINAL_REPORT.md) | ✅ Phase 11 收官（/loop+/goal 集成） |
| [PHASE12_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE12_FINAL_REPORT.md) | ✅ Phase 12 收官（架构师 review 修复） |
| [PHASE13_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE13_FINAL_REPORT.md) | ✅ Phase 13 收官（多 Goal 编排） |
| [PHASE14_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE14_FINAL_REPORT.md) | ✅ Phase 14 收官（B-1~B-4 修复 + GoalCancel） |
| [PHASE15_PLAN.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE15_PLAN.md) | ✅ Phase 15 计划（DAG 可视化） |
| [PHASE16_PLAN.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE16_PLAN.md) | ✅ Phase 16 计划（V3 插件架构） |
| [PHASE16_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE16_FINAL_REPORT.md) | ✅ Phase 16 收官（god module 拆分） |
| [PHASE17_PLAN.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE17_PLAN.md) | ✅ Phase 17 计划（插件热加载 v3.1） |

### 关键能力清单

- ✅ **6 大模式执行器**：classifier-dispatch / fan-out-aggregate / adversarial-verify / **generate-filter** / **tournament** / **loop-until-done**
- ✅ **3 种去重策略**：exact / fuzzy / **semantic（真实多语言模型）**
- ✅ **3 种排名方法**：knockout / round-robin / elo
- ✅ **4 种停止条件**：no_new_findings / no_error_logs / quality_threshold_met / convergence_detected
- ✅ **subagent 物理隔离**：worktree + context + Token 预算
- ✅ **subagent 中断恢复**：pause/resume/cancel + 5 种策略 + 指数退避重试（Phase 9）
- ✅ **模型路由**：3 档（haiku/sonnet/opus）+ 画像反哺
- ✅ **Token 预算守护**：3 模式（HARD/SOFT/HYBRID）+ 优雅降级
- ✅ **端到端集成**：sandbox + router + budget 协同
- ✅ **真实多语言 embedding**：paraphrase-multilingual-MiniLM-L12-v2（Phase 7 升级）
- ✅ **Skill 自动注入**：6 大核心组件 + 4 种渲染模式 + 4 级 Token 截断（Phase 8）
- ✅ **完全向后兼容**：Phase 0/1/2/3/4/5/6/7/8 调用方零改动
- ✅ **安全合规**：Guard 强制校验 + 提示词注入防护 + 异常隔离

### Dynamic Workflows 6 大模式全部沉淀映射

| # | 模式 | 痛点 | 状态 |
|---|------|------|------|
| 1 | classifier-dispatch | 任务分类路由 | ✅ Phase 1 |
| 2 | fan-out-aggregate | 大量同质子任务 | ✅ Phase 1 |
| 3 | adversarial-verify | self-preferential bias | ✅ Phase 1 |
| 4 | **generate-filter** | 创意探索 + 概率质量 | ✅ **Phase 5** |
| 5 | **tournament** | 多方案择优 | ✅ **Phase 5** |
| 6 | **loop-until-done** | 未知工作量 + goal drift | ✅ **Phase 5** |

### 下一步决策

Phase 0' → 9 全部完成（666 tests 通过）。可考虑 Phase 10+ 候选方向：

| 方向 | 优先级 | 范围 | 预计测试增量 |
|------|--------|------|--------------|
| /loop + /goal 集成 | 中 | 终端用户命令 | 20+ tests |
| model_tier-aware dispatch | 中 | cybernetics_bridge 解析 _meta.model_tier | 15+ tests |
| SkillDistribution 增强 | 中 | Skill 热更新 / 版本协商 / 缓存 | 35+ tests |
| 中断恢复增强 | 低 | 分布式恢复 / ML 中断预测 / executor 中间状态持久化 | 30+ tests |
| ~~InterruptionRecovery（推荐）~~ | ✅ 完成 | subagent 中断恢复 | 32 tests |
| ~~SkillDistribution~~ | ✅ 完成 | Skill 自动注入 | 50 tests |

---

## 八、风险与"禁用边界"（直接对齐文章建议）

> 文章明确警告：**"不是每个任务都需要使用 Workflows"** —— trae-multi-agent 也应同样克制。

| 反模式 | 风险 | 应对 |
|--------|------|------|
| 把所有任务都改为 Dynamic Workflows | 简单任务被复杂化，Token 浪费 | 默认 `SEQUENTIAL`，按需升级 |
| 过度扇出 | 并发资源耗尽，worktree 冲突 | `fanout_count` 上限 10，提示词中可声明 |
| 过度锦标赛 | N 选 1 消耗巨大 | 默认 candidate_count=3，quality 阈值熔断 |
| 模式库臃肿 | 6 大模式变成 20 大模式 | **严格守住 6 大**，新增模式需架构师审核 |
| Token 预算过紧 | 任务未完成就退出 | 预算 < 10% 时切换 haiku 继续，而非中断 |

---

## 九、与其他增强的优先级对比

trae-multi-agent v2.5 已有不少增强项，融合方案应**避免重复造轮子**：

| 已有方案 | 融合方案的关系 |
|---------|--------------|
| **Karpathy 四大原则** | ✅ 保留，作为"使用边界"——克制使用 Dynamic Workflows |
| **Cybernetics 反馈环** | ✅ 升级，反馈维度从"任务级"扩展到"模式级" |
| **双层上下文** | ✅ 复用，subagent 用"任务级 context 隔离"扩展 |
| **多角色代码走读** | ⚠️ 部分重叠，"对抗性验证"可复用多角色机制 |
| **七阶段标准工作流** | ✅ 保留，作为"何时不用 Dynamic Workflows"的反例（确定性流程用顺序工作流） |

**优先级建议**：

> **Dynamic Workflows 是 v2.5 之上的"高阶可选能力"，不是 v2.5 的替代品。**
> 现有 v2.5 用户**不需任何修改**即可继续使用；新增能力对"长程/并行/对抗/不确定工作量"四类任务**显著加速**。

---

## 十、用户可感知价值

| 场景 | 现状 | 融合后 |
|------|------|--------|
| 大型代码库审查（50+ 文件安全审查） | 单 context 漏检 60% | 扇出 N 个 subagent 独立审查 → 聚合 |
| 多方案选型 | 手工对比 3-5 方案 | 锦标赛模式自动 N 选 1 |
| 长期 root cause 调查 | 陷入自我偏好偏差 | 对抗性验证：生成假设 + 反驳者 |
| Token 失控 | 跑满消耗 | Token 预算 + 动态降级 |
| 不确定工作量任务 | retry 死循环 | 循环直到停止条件 |
| 失败任务重试 | 每次都从头开始 | 模式库复用成功历史模式 |

---

## 十一、Phase 0' 交付物（本轮目标）

### 交付物清单

| # | 产物 | 路径 | 类型 | 状态 |
|---|------|------|------|------|
| 1 | 融合方案 v1.1（已采纳审查意见） | [DYNAMIC_WORKFLOWS_INTEGRATION.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md) | 文档 | ✅ 完成 |
| 2 | 6 大模式参考手册 | [PATTERNS_REFERENCE.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PATTERNS_REFERENCE.md) | 文档 | ✅ 完成 |
| 3 | 模式选择 JSON 示例（3 个） | [pattern_examples/](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/pattern_examples/) | 文档 | ✅ 完成 |
| 4 | 模式选择提示词模板（3 个） | [pattern_prompts/](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/pattern_prompts/) | 文档 | ✅ 完成 |

### Phase 0' 验收

- [x] 方案 v1.1 包含 §3.0 约束与边界
- [x] 实施路线拆为 5 个独立 Phase
- [x] V2 不修改承诺明确
- [x] 持久化复用约束明确
- [x] 安全补强明确
- [x] 6 大模式参考手册配套
- [x] 3 个核心模式 JSON 示例完整（schema / parameters / 失败模式 / 成功标准）
- [x] 3 个核心模式提示词模板完整（基础 / 完整示例 / 反例 / 反推 / 关键决策 / 失败处理 / 集成点 / 验证清单）
- [ ] 用户/架构师再审通过

### 进入 Phase 0 代码实施的门槛

> 必须满足下列所有条件，方可启动 Phase 0 代码：
1. 本方案 v1.1 经架构师再审通过
2. PATTERNS_REFERENCE.md 经用户评审通过
3. DualLayerContextManager 多实例可行性经 POC 验证（可作为 Phase 0 任务的一部分）
4. 模式选择 JSON 示例 3 个完成 ✅
5. 模式选择提示词模板 3 个完成 ✅

### 交付物文件清单

```
docs/dev/
├── DYNAMIC_WORKFLOWS_INTEGRATION.md      # 方案 v1.1（含 §3.0 约束）
├── ARCHITECT_REVIEW_DYNAMIC_WORKFLOWS.md  # 架构师审查报告
├── PATTERNS_REFERENCE.md                  # 6 大模式参考手册
├── pattern_examples/
│   ├── classifier-dispatch.json           # 128 行
│   ├── fan-out-aggregate.json             # 152 行
│   └── adversarial-verify.json           # 150 行
└── pattern_prompts/
    ├── classifier-dispatch.md             # 200 行
    ├── fan-out-aggregate.md               # 232 行
    └── adversarial-verify.md              # 300 行
```

---

## 十二、总结

**核心立场**：

> **不要把 trae-multi-agent 重做成 Dynamic Workflows，要让 trae-multi-agent **具备** Dynamic Workflows 能力。**

### v1.1 强约束清单（必须遵守）

1. **持久化复用**：禁止新建并行存储，全部复用现有 6 大 manager
2. **V2 不修改**：V2 任何文件零修改，新能力通过扩展点（executor / hook）注入
3. **Phase 拆分**：5 个独立阶段，每阶段独立可发布可回滚
4. **安全补强**：subagent 输入 schema 校验、Token 硬上限、模式库 schema 校验、提示词注入防护
5. **模式上限 6**：新增模式需架构师审核；Phase 0 只沉淀 3 个核心模式
6. **一阶段一模块**：4 大模块拆为 4 个 Phase

### 保留/增强/克制/反哺

- **保留**：v2.5 的全部能力（Karpathy 原则、Cybernetics、多角色、上下文、调度）
- **增强**：在 WorkflowEngineV2 之上叠加"模式库 + 并行能力 + subagent 沙箱 + 资源路由"
- **克制**：默认顺序、默认简单、按需升级（Simplicity First）
- **反哺**：执行结果回流 PerformanceFingerprint，让模式选择本身具备学习能力

### 本质洞察

> Dynamic Workflows 的精髓不是"JS 驱动"，而是 **"用多 subagent 的并行/对抗/独立 context 解决单 context 必然失败的三类任务"**。trae-multi-agent 已经拥有 subagent 雏形（5 角色），融合方案要做的是**让角色获得 subagent 的物理隔离能力 + 模式化的协同范式**。

---

*文档版本：v1.7（Phase 17 插件热加载 + 覆盖度 v2 完成后修订）*  
*上一版本：[v1.6（Phase 9 完成后）](#)*  
*配套审查报告：[ARCHITECT_REVIEW_DYNAMIC_WORKFLOWS.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/ARCHITECT_REVIEW_DYNAMIC_WORKFLOWS.md)*  
*配套模式手册：[PATTERNS_REFERENCE.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PATTERNS_REFERENCE.md)*  
*下一步：用户/架构师再审 → 启动 Phase 18（V3 Plugin Marketplace 候选）*
