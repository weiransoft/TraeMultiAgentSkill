---
name: multi-agent-team
slug: multi-agent-team
description: 基于任务类型动态调度到合适的智能体角色（架构师、产品经理、测试专家、独立开发者、UI 设计师）。支持多智能体协作、共识机制、完整项目生命周期管理、规范驱动开发、代码走读审查和项目理解能力。支持中英文双语。v2.4 新增 Karpathy 四大核心原则，v2.5 新增 Cybernetics 工程控制论增强，v2.6 新增 Ponytail 决策梯（少写多余代码）、Autonomous 自主迭代模式、Dynamic Workflows 6 大模式、插件热加载，v2.7 新增 UI/UX 巡检分析与视觉回归测试脚本，v2.7.1 修订 AI 诚实降级、真实语义匹配、双宿主清单同步与 v1 死代码清算。
---

# Multi-Agent Team Dispatcher (AI-Enhanced)

基于任务类型和上下文，自动调度到最合适的智能体角色（架构师、产品经理、测试专家、Solo Coder、UI 设计师）。

> **能力实现方式说明（v2.7.1 诚实标注）**：
> - **提示词层 AI（宿主 LLM 完成）**：任务语义理解、角色智能匹配、多角色共识决策、架构设计审查——由 Trae/Claude 宿主大模型直接执行，脚本仅提供候选清单与规则约束。
> - **脚本层确定性工具（Python 实现）**：代码地图扫描、UI/UX 巡检、视觉回归对比、决策梯合规检查、TFIDF/Hashing 文本相似度——为无 LLM 的独立进程，提供可复现的确定性结果。
> - **降级模式说明**：当脚本层需要语义相似度但无网络/模型时，自动降级到 TFIDF/Hashing 本地算法；SubAgent 调用在无真实 claude 命令时返回错误（`fallback_mode: error`），真实并行子代理由宿主 Task 机制实现。

**v2.7.1 修订（AI 诚实化 + 代码清算 + 双宿主同步）**:
- 🤥→✅ AI 诚实降级：`ai_assistant.py` / `ai_semantic_matcher.py` 移除全部模拟响应，无 AI 客户端时明确抛错并降级到确定性匹配，不再伪装 AI 输出；`claude_code_subagent_adapter.py` 移除 `_simulate_subagent_call`，无真实 subagent 时返回 `success=False`
- 🔢 真实语义匹配：`role_matcher.py` 从关键词重叠升级为本地 embedder（TFIDF/Hashing）向量余弦相似度
- 🛡️ embedder 三级降级链：`goal_orchestrator.py` SentenceTransformer → TFIDF → Hashing，无网络/无模型时自动降级
- 🎭 Claude Code 真实 SubAgent：`.claude/agents/` 新增 5 角色定义文件，替代脚本模拟
- 🔄 双宿主清单同步：`sync_manifests.py` CI 校验三份 manifest（skill-manifest.yaml / skills-index.json / claude-code-skill.json）版本与命名一致
- 🧹 v1 死代码清算：删除 `workflow_engine.py` / `code_map_generator.py` / `test_v2_components.py`，`dispatch/legacy.py` 切换到 `WorkflowEngineV2`
- 📦 依赖显式化：`requirements.txt` 标注软依赖（playwright / Pillow / sentence-transformers 均为可选）

**v2.5 新增（Cybernetics 工程控制论增强）**:
> 参考来源：https://github.com/Jiaqi-Guo-0114/cybernetics-agent  
> 理论依据：钱学森工程控制论（系统工程、系统学）、ICLR 2026 Profile-Aware Maneuvering 架构、Norbert Wiener 控制论、Ashby 必要多样性定律  
> - 🔄 三环控制模型：战略层、战术层、执行层
> - 💫 反馈控制环：感知-决策-执行-反馈完整闭环
> - 📊 性能画像：执行案例记录、相似案例检索
> - 🛡️ 守护协调器：执行前验证、异常检测、AI增强风险评估

**v2.4 新增（Karpathy 四大核心原则）**:
- 🧠 **Think Before Coding（三思而后行）**: 明确假设、呈现权衡、遇到不清就问
- 🎯 **Simplicity First（简单优先）**: 最小代码、无 speculative features、无过度抽象
- 🔬 **Surgical Changes（精准修改）**: 只改需要的、不改无关的、保持风格一致
- ✅ **Goal-Driven Execution（目标驱动）**: 定义成功标准、验证检查点、迭代直到完成

**v2.6 新增（Ponytail 决策梯 + 自主编排 + 插件热加载）**:
- 🪜 Ponytail 决策梯：6 步决策梯 + 16 条不可简化红线，少写多余代码
- 🤖 Autonomous 自主迭代模式：plan→dev→verify→fix 4 阶段循环，Ralph 风格自主编排
- 🔄 Dynamic Workflows 6 大模式：分类并行、扇出聚合、对抗验证、生成筛选、锦标赛、循环直到完成
- 🔌 插件热加载 V3 架构：Drop-in 目录 / Hot Register API / HotReloadWatcher 3 种加载路径

**v2.7 新增（UI/UX 巡检 + 视觉回归测试）**:
- ♿ UI/UX 巡检分析：`scripts/uiux_analyzer.py`，4 大检测维度（可访问性 / 交互质量 / 布局响应式 / UX 反模式）
- 🖼️ 视觉回归测试：`scripts/visual_regression.py`，像素级 Diff + 数据显示不全 + 显示错误检测
- 🛡️ 前端质量门禁：UI 设计师交付前自检、测试专家 E2E 断言、Solo Coder CI 门禁

**v2.3 新增（代码走读与可视化）**:
- 🚀 代码走读与审查：多角色协作分析，生成统一代码地图和审查报告
- 🗺️ 代码地图 Workspace 支持：支持一个 workspace 包含多个项目
- 📊 文档对齐引擎：多角色分析结果对齐，生成共识代码地图
- 📋 任务可视化页面：实时展现各角色任务状态、进度、依赖关系、交接过程
- 🎨 3D 代码地图可视化：基于 Three.js 的交互式代码结构可视化，支持流动动画和主题切换
- ✅ 文档与代码一致性检查：审查报告中新增文档与代码差异检查清单

## Karpathy 四大核心原则（行为准则）

> **来源**: Andrej Karpathy 对 LLM 编程常见陷阱的观察
> **目的**: 减少 LLM 编程中的错误、过度复杂、无关修改等问题

所有角色必须遵守以下四大原则，详见各角色介绍中的「Karpathy 原则应用」表格。

| 原则 | 核心要求 | 禁止行为 |
|------|---------|---------|
| 🧠 Think Before Coding | 明确假设、问清楚、不隐藏困惑 | ❌ 假设用户意图、默默选择方案 |
| 🎯 Simplicity First | 最小代码、无 speculative features | ❌ 过度抽象、预留未来代码 |
| 🔬 Surgical Changes | 只改必要的、保持风格一致 | ❌ 溢出修改、顺手改无关代码 |
| ✅ Goal-Driven | 定义成功标准、验证检查点 | ❌ 不知道何时完成、跳过验证 |

**速查**:
- 需求不明确 → 停下来问清楚
- 考虑添加抽象 → 问"真的需要吗？"
- 修改代码 → 只改必要的行
- 开始实现 → 定义成功标准

详细说明和示例见 `docs/guides/KARPATHY_PRINCIPLES.md`。

## 多语言支持 (Multi-Language Support)

### 语言识别规则
**自动识别用户语言**:
- 用户使用中文 → 所有响应使用中文
- 用户使用英文 → 所有响应使用英文
- 用户混合使用 → 以首次使用的语言为准
- 用户明确要求切换 → 立即切换到目标语言

### 响应语言规则
**所有输出必须使用用户相同的语言**:
- 角色定义和 Prompt
- 状态更新和进度提示
- 审查报告和问题清单
- 错误信息和成功提示
- 文档和注释

**示例**:
```
用户（中文）: "设计系统架构"
AI（中文）: "📋 已接收任务，开始分析..."

用户（English）: "Design system architecture"
AI (English): "📋 Task received, starting analysis..."
```

### 角色名称映射
**中文 → 英文**:
- 架构师 → Architect
- 产品经理 → Product Manager
- 测试专家 → Test Expert
- 独立开发者 → Solo Coder
- UI 设计师 → UI Designer

## 核心能力

### AI 增强能力 (v2.1 新增)

1. **AI 语义理解驱动的角色匹配**: 使用大模型理解任务的深层语义，而非简单关键词匹配
2. **可解释的智能决策**: 提供匹配原因和置信度评分，决策过程透明可解释
3. **上下文感知的智能推理**: 基于历史经验和领域知识进行智能推理
4. **自然语言交互界面**: 支持自然语言对话，理解用户意图

### 基础能力

1. **智能角色调度**: 根据任务描述自动识别需要的角色
2. **多角色协同**: 组织多个角色共同完成复杂任务
3. **上下文感知**: 根据项目阶段和历史上下文选择角色
4. **共识机制**: 组织多角色评审和决策
5. **自动继续**: 思考次数超限后自动保存进度并继续执行
6. **任务管理**: 完整的任务生命周期管理和进度追踪
7. **代码地图生成**: 自动生成项目代码结构映射
8. **项目理解**: 快速读取项目文档和代码，生成项目理解文档
9. **规范驱动开发**: 基于项目规范和文档进行开发
10. **八阶段标准工作流程**: 需求分析→架构设计→UI设计→测试设计→任务分解→开发实现→测试验证→文档对照代码审查
11. **UI 设计**: 创建独特、生产级的 UI 界面，避免通用的 AI "slop" 美学

## 核心入口与门面（v2.8.3 确认）

> **架构收敛**：facade.py 是统一调度门面，所有外部调用入口最终都汇聚到 facade。

### 入口层次（从外到内）

| 入口 | 状态 | 用途 |
|------|------|------|
| `scripts/trae_agent_dispatch.py` | ⚠️ v2.8.3 起弃用（DeprecationWarning） | v1 兼容入口，仅向后保留 |
| `scripts/trae_agent_dispatch_v2.py` | ✅ 推荐 CLI 入口 | V3 薄壳（< 50 行），委托给 facade.main_compat |
| `scripts/facade.py` | ✅ 统一门面（Python 入口） | re-export 11 个旧符号 + main_compat + hot reload watcher |
| `scripts/dispatch/legacy.py` | 内部实现层 | 5 个 dispatch 函数 + 助手（god module 搬迁产物） |
| `scripts/dispatcher/goal_dispatcher.py` | 内部 V3 dispatcher | 插件化调度核心 |

### Python 集成推荐

```python
# 推荐：直接从 facade 导入
from facade import main_compat, dispatch_agent_v2, log

# 或从薄壳导入（同样可用，会 re-export 自 facade）
from trae_agent_dispatch_v2 import main_compat, dispatch_agent_v2
```

### 绝对禁止

❌ 业务代码直接 import `dispatch.legacy`（内部实现层，可能被重构）
❌ 业务代码直接 import `dispatcher.goal_dispatcher`（内部实现层）
❌ 新代码使用 `trae_agent_dispatch.py`（v1 已弃用）

## Trae 宿主 LLM SubAgent 调度协议（v2.8.4 新增）

> **问题**：Trae 环境下无 `claude` 命令行工具，`ClaudeCodeSubAgentAdapter` 的 `_fallback_no_subagent` 返回 `success=False`，导致 autonomous 循环无限重试。
>
> **解决方案**：通过文件协议（`HostLLMBridge`）桥接宿主 LLM 的 Task 工具，脚本生成调度请求 → 宿主 LLM 通过 Task 执行子代理 → 结果回传。

### 工作流程

```
宿主 LLM (Trae 对话)
    │
    ├─1─> RunCommand(blocking=false): python3 -u trae_agent_dispatch_v2.py --autonomous ...
    │
    │     [脚本内部]
    │     ├─> _invoke_via_host_llm → HostLLMBridge.create_request()
    │     │   → 写入 request_{id}.json + protocol.marker
    │     ├─> HostLLMBridge.wait_for_response(id, timeout=600)
    │     │   → 轮询等待 response_{id}.json
    │     │
    │     │     [宿主 LLM — 并行执行]
    │     │     ├─2─> 每 5 秒读取 protocol.marker 文件
    │     │     ├─3─> 读取 request_{id}.json 获取完整提示词
    │     │     ├─4─> Task(subagent_type, query=task) → 执行子代理
    │     │     ├─5─> HostLLMBridge.write_response(id, result) → 原子写入
    │     │     │
    │     │     [脚本继续]
    │     │     └─6─> 读取 response_{id}.json → 返回结果
    │     └─7─> autonomous 循环继续
    │
    └─8─> CheckCommandStatus → 脚本完成
```

### 宿主 LLM 轮询契约

当通过 `RunCommand(blocking=false)` 运行 autonomous 脚本后，宿主 LLM **必须**遵循以下轮询契约：

1. **启动后 2 秒**首次检查：读取 `logs/host_llm_bridge/protocol.marker` 文件
2. **之后每 5 秒**检查一次 `protocol.marker` 文件
3. **检查到 marker** 后：
   a. 读取 marker JSON 获取 `request_id` 和 `request_file`
   b. 读取 `request_file` 获取完整提示词
   c. 通过 Task 工具执行子代理（subagent_type 根据角色选择）
   d. 调用 `HostLLMBridge.write_response()` 写入结果
   e. 调用 `HostLLMBridge.clear_marker()` 清除标记
4. **marker 不存在**时：继续等待（脚本可能还在执行前序步骤）
5. **脚本结束**时：停止轮询

**重要**：protocol.marker 是独立文件，**不依赖 CheckCommandStatus 的 stdout**。使用 `Read` 工具直接读取文件。

### Task 工具 subagent_type 映射

| skill 角色名 | Task subagent_type | 说明 |
|-------------|-------------------|------|
| architect | search | 架构分析需要代码搜索 |
| product-manager | general_purpose_task | 产品分析 |
| test-expert | general_purpose_task | 测试设计 |
| solo-coder | general_purpose_task | 代码实现 |
| ui-designer | general_purpose_task | UI 设计 |

### 写入结果（强制原子写入）

宿主 LLM **必须**使用 `HostLLMBridge.write_response()` 写入结果，**禁止**直接 `echo > response.json`：

```bash
# 通过命令行调用（推荐）
python3 scripts/host_llm_bridge.py write-response <request_id> \
    --success true --output "结果内容" --bridge-dir logs/host_llm_bridge
```

### 熔断机制（连续失败保护）

当连续 2 次迭代均为 `retriable` 且原因包含超时/dispatch 失败关键词时，`loop_controller` 自动终止循环（升级为 fatal），避免无限重试浪费资源。

### 平台检测优先级

| 环境变量 | 平台 | 调度方式 |
|---------|------|---------|
| `TRAE_ENV` 或 `TRAE_AGENT_PATH` | `host_llm` | 文件协议桥接宿主 LLM Task 工具 |
| `CLAUDE_CODE_ENV` 或 `ANTHROPIC_ENV` | `claude_code` | claude CLI subprocess |
| 无 | `unknown` | `_fallback_no_subagent`（诚实降级） |

### 设计文档

详细设计见 `docs/dev/HOST_LLM_BRIDGE_DESIGN.md`。

## 快速开始

### 基础使用
```bash
# 自动调度（推荐）
python3 scripts/trae_agent_dispatch_v2.py \
    --task "设计系统架构"

# 指定角色
python3 scripts/trae_agent_dispatch_v2.py \
    --task "实现功能" \
    --agent solo_coder

# 多角色共识
python3 scripts/trae_agent_dispatch_v2.py \
    --task "启动新项目" \
    --consensus true
```

### 完整项目流程
```bash
# 启动完整项目（自动执行 7 个阶段）
python3 scripts/trae_agent_dispatch_v2.py \
    --task "启动项目：安全浏览器广告拦截功能" \
    --project-full-lifecycle
```

### AI 增强模式 (v2.1 新增)
```bash
# 使用 AI 语义匹配（默认）
python3 scripts/trae_agent_dispatch_v2.py \
    --task "设计微服务架构，支持高并发和弹性扩展" \
    --agent auto  # AI 会自动匹配最合适的角色

# 查看 AI 匹配结果和解释
python3 scripts/trae_agent_dispatch_v2.py \
    --task "实现用户认证和权限管理" \
    --agent auto \
    --explain  # 显示 AI 匹配原因和置信度

# 使用传统关键词匹配（向后兼容）
python3 scripts/trae_agent_dispatch_v2.py \
    --task "编写单元测试" \
    --agent test-expert \
    --match-strategy keyword
```

## AI 集成说明 (v2.1)

> **宿主 LLM 调度指令（提示词层，Trae/Claude 主 Agent 必读）**：
> 以下匹配决策与拓扑路由由你（宿主大模型）直接执行，脚本层仅提供确定性工具。
> 不要调用 `ai_semantic_matcher.py` 的模拟路径——它已在 v2.7.0 改造为诚实降级。

### 任务规模分级门禁（v2.8.2 — 第一道路由）

> **Karpathy Simplicity First 的前置执行**：在角色匹配之前，先判断任务规模，避免杀鸡用牛刀。

接到任务后，**首先**按以下信号评估任务规模，选择执行路径：

| 档位 | 判定信号 | 执行路径 | 禁止行为 |
|------|---------|---------|---------|
| **S（分钟级）** | 单文件修改、问答、小修复、单函数重构 | 直接派单角色执行，完成后验证 | ❌ 禁止启动工作流/循环/共识/代码地图 |
| **M（小时级）** | 单功能开发、Bug 修复+验证、模块重构 | 三阶段迷你流：设计要点 → 开发 → 测试验证 | ❌ 禁止启动八阶段流程/autonomous 模式 |
| **L（天级）** | 新项目、跨模块改造、架构迁移、用户明确要求完整流程 | 完整八阶段 Loop（WorkflowLoopController） | — |

**判定信号清单**（按顺序检查，停在第一个满足的）：
1. 用户明确要求完整流程 / 启动项目 → **L**
2. 涉及 ≥ 3 个模块或 ≥ 5 个文件 → **L**
3. 涉及 2 个模块或 3-4 个文件 → **M**
4. 涉及 1 个文件或纯问答 → **S**
5. 不确定 → 默认 **M**（宁可多验证，不可遗漏）

**与拓扑路由的关系**：S/M/L 分级是第一道路由（决定流程规模），角色匹配是第二道路由（决定谁来执行），拓扑路由是第三道路由（决定执行模式）。

### 角色智能匹配规则（宿主 LLM 执行）

接到任务后，按以下多维度评分选择角色（无需调用脚本）：

| 维度 | 权重 | 评估要点 |
|------|------|---------|
| 能力匹配 | 50% | 任务核心需求与角色职责的重合度（见下方角色触发关键词） |
| 技能匹配 | 30% | 任务所需技术栈与角色技能列表的重合度 |
| 语义相关 | 20% | 任务描述与角色描述的深层语义关联（同义、上下位、因果） |

**决策规则**：
1. 单角色置信度 ≥ 0.7 → 直接调度该角色
2. 多角色均 ≥ 0.6 且任务可分解 → 多角色协作（见拓扑路由）
3. 所有角色 < 0.5 → 向用户澄清需求，不要强行匹配
4. 任务跨多个领域（如"设计+实现+测试"）→ 按八阶段流程依次调度

### 动态工作流拓扑路由决策表（宿主 LLM 执行）

根据任务 DAG 特征选择执行模式（对齐 2026 拓扑自适应编排结论）：

| 任务特征 | 路由模式 | 执行方式 |
|---------|---------|---------|
| 可分解为独立子任务（并行宽 ≥ 2，无相互依赖） | fan-out-aggregate | 并行派发 → 聚合结果 |
| 需要质量验证/对抗审查（如代码审查、安全审计） | adversarial-verify | 生成 → 审查 → 修复循环 |
| 需要多方案竞争（如架构选型、文案创作） | tournament | 多方案并行 → 最优胜出 |
| 需批量生成后筛选（如测试用例、候选实现） | generate-filter | 批量生成 → 质量过滤 |
| 任务类型明确且单一（如纯测试、纯文档） | classifier-dispatch | 分类 → 单角色执行 |
| 成功标准可量化但未达成（如性能优化、缺陷修复） | loop-until-done | 迭代直到满足标准 |
| **强顺序依赖、链式推理**（如调试根因分析、数学推导、分步重构） | **单角色链** | ⚠️ **禁用多 Agent 并行**——实证显示此类任务多 Agent 反降 39–70% 性能，由单一角色顺序完成 |

**降级红线**：当任务本质是顺序推理（步骤 N 依赖步骤 N-1 的结果）时，禁止为了"多角色"而强行并行，应退化为单角色链式执行。

### AI 能力（脚本层工具）

- **语义理解**: 宿主 LLM 提示词层完成；脚本层提供 TFIDF/Hashing 本地相似度（降级用）
- **智能匹配**: 宿主 LLM 按上表执行；脚本 `role_matcher.py` 提供关键词+embedder 降级
- **匹配策略**: `ai_enhanced`（宿主提示词层）、`semantic`（脚本 embedder）、`keyword`（脚本关键词）
- **代码审查**: 宿主 LLM 执行；脚本层提供代码地图、guard 校验等确定性输入
- **知识问答**: 宿主 LLM 直接回答

### AI 配置

```yaml
ai_integration:
  enabled: true
  provider: host_llm_prompt_layer   # v2.7.0 修正：AI 在宿主提示词层
  features:
    - semantic_matching      # 宿主 LLM
    - intelligent_reasoning  # 宿主 LLM
    - context_understanding  # 宿主 LLM
  script_layer_tools:        # 脚本层确定性工具（非 AI）
    - tfidf_similarity
    - hashing_similarity
    - keyword_matching
    - code_map_scan
    - uiux_audit
    - visual_regression
  config:
    max_tokens: null          # null = 不限制（默认，让模型按自身最大输出能力生成）；正整数 = 显式上限（如 4096）
    temperature: 0.7
    top_p: 0.9
    use_cache: true
    fallback_to_keyword: true  # 脚本层降级链：embedder → 关键词
```

### 性能优化

- **缓存机制**: 相同请求直接返回缓存结果
- **降级策略**: 脚本层 embedder 不可用时自动降级到关键词匹配；AI 语义理解始终由宿主 LLM 提供，无降级
- **批量处理**: 支持批量请求，减少 API 调用次数

## 角色介绍

### 通用行为准则（所有角色必须遵守）

**Karpathy 四大核心原则**:

| 原则 | 核心要求 | 禁止行为 |
|------|---------|---------|
| 🧠 Think Before Coding | 明确假设、问清楚、不隐藏困惑 | ❌ 假设用户意图、默默选择方案 |
| 🎯 Simplicity First | 最小代码、无 speculative features | ❌ 过度抽象、预留未来代码 |
| 🔬 Surgical Changes | 只改必要的、保持风格一致 | ❌ 溢出修改、顺手改无关代码 |
| ✅ Goal-Driven | 定义成功标准、验证检查点 | ❌ 不知道何时完成、跳过验证 |

### 1. 架构师 (Architect)
**职责**: 设计系统性、前瞻性、可落地、可验证的架构
**触发关键词**: 架构、设计、选型、审查、性能、瓶颈、模块、接口、部署
**详细 prompt**: `docs/roles/architect/ARCHITECTURE_DESIGN_TEMPLATE.md`

### 2. 产品经理 (Product Manager)
**职责**: 定义用户价值清晰、需求明确、可落地、可验收的产品
**触发关键词**: 需求、产品、PRD、用户故事、验收标准、竞品分析
**详细 prompt**: `docs/roles/product-manager/PRD_TEMPLATE.md`

### 3. 测试专家 (Test Expert)
**职责**: 确保全面、深入、自动化、可量化的质量保障
**触发关键词**: 测试、质量、验收、自动化、性能测试、缺陷、评审、门禁
**详细 prompt**: `docs/roles/test-expert/TEST_PLAN_TEMPLATE.md`

### 4. 独立开发者 (Solo Coder)
**职责**: 编写完整、高质量、可维护、可测试的代码
**触发关键词**: 实现、开发、代码、修复、优化、重构、单元测试、文档
**详细 prompt**: `docs/roles/solo-coder/DEVELOPMENT_TEMPLATE.md`

### 5. UI 设计师 (UI Designer)
**职责**: 创建独特、生产级的 UI 界面，具有高设计质量，避免通用的 AI "slop" 美学
**触发关键词**: UI设计、界面设计、前端设计、视觉设计、UI/UX、UI原型、界面美化、UI优化、UI重构
**详细 prompt**: `docs/roles/ui-designer/UI_DESIGNER_PROMPT.md`

## 八阶段标准工作流程（v2.8 新增阶段 8：文档对照代码审查）

```
阶段 1: 需求分析（产品经理）
    ↓ 评审通过
阶段 2: 架构设计（架构师）
    ↓ 评审通过
阶段 3: UI 设计（UI 设计师）
    ↓ 评审通过
阶段 4: 测试设计（测试专家）
    ↓ 评审通过
阶段 5: 任务分解（独立开发者）
    ↓
阶段 6: 开发实现（独立开发者）
    ↓
阶段 7: 测试验证（测试专家）
    ↓
阶段 8: 文档对照代码审查（多角色）★ v2.8 新增
    ↓ 审查通过
发布
```

### 阶段 8: 文档对照代码审查（v2.8 新增）

开发完成后，对照设计文档逐项检查功能完成情况、集成情况、测试正确性，确保文档设计完整落地。

**六大检查维度**：

| 维度 | 检查内容 | 通过条件 |
|------|----------|----------|
| D1 功能完成度 | 文档中每个功能点是否有对应代码实现 | 实现率 = 100% |
| D2 集成完整性 | 文档定义的模块间集成关系是否在代码中体现 | 集成率 = 100% |
| D3 测试正确性 | 全部测试通过，无失败；测试覆盖文档功能 | failed = 0, passed > 0 |
| D4 验收标准满足 | 文档中每条验收标准是否被代码满足 | 满足率 = 100% |
| D5 TODO/FIXME 清零 | 代码中无残留的 TODO/FIXME 注释 | 未实现 = 0 |
| D6 文档意图遵从 | 代码实现未偏离文档设计意图 | 偏离数 = 0 |

**审查判定**：
- 全部通过 → 审查通过，可发布
- 任一不通过 → 审查不通过，列出缺口清单，回退到阶段 6 修复

### 八阶段整体构建为一个 Loop（v2.8.1 新增）

八阶段标准工作流整体构建为一个完整的循环（`WorkflowLoopController`），支持审查失败后回退到对应阶段修复，避免一次性失败导致整个流程作废。

**核心机制**：
- **审查驱动**：阶段 8 审查结果是循环的核心驱动：通过则结束，不通过则回退
- **精准回退**：根据缺口维度（D1-D6）决定回退到哪个阶段

| 缺口维度 | 回退到阶段 | 理由 |
|----------|-----------|------|
| D1 功能完成度 | 阶段 6（开发） | 功能缺失需补开发 |
| D2 集成完整性 | 阶段 6（开发） | 集成缺失需补开发 |
| D3 测试正确性 | 阶段 7（测试验证） | 测试失败需修复测试 |
| D4 验收标准 | 阶段 6（开发） | 验收未满足需补开发 |
| D5 TODO/FIXME | 阶段 6（开发） | TODO 未实现需补开发 |
| D6 文档意图 | 阶段 6（开发） | 文档偏离需调整代码 |

- **迭代上限**：最大迭代次数限制（默认 3 次，可配置），防止无限循环
- **上下文累计**：跨迭代保留产出（`_accumulated_artifacts`），后续阶段可访问前序产出
- **真实执行**：所有阶段真实执行，禁 mock/占位/简化

**核心组件**：
- 检查器：`scripts/doc_code_consistency_checker.py`（DocCodeConsistencyChecker）
- Handler：`scripts/autonomous/handlers/review_handler.py`（ReviewHandler）
- **循环控制器**：`scripts/workflow_loop_controller.py`（WorkflowLoopController + RollbackStrategy）
- **CLI 入口**：`scripts/run_workflow_loop.py`（命令行入口脚本）
- Prompt 模板：`docs/spec/role-prompts/doc-code-review.md`
- 报告模板：`docs/roles/doc-code-review/DOC_CODE_REVIEW_TEMPLATE.md`
- 设计文档：`docs/dev/DOC_CODE_REVIEW_STAGE.md`（§10 八阶段循环章节）

**CLI 使用示例**：

```bash
# 基本用法
python3 scripts/run_workflow_loop.py \
  --project-root /path/to/project \
  --prd-path docs/prd.md \
  --architecture-path docs/architecture.md \
  --test-command "python3 -m pytest -v"

# 自定义最大迭代次数
python3 scripts/run_workflow_loop.py \
  --project-root /path/to/project \
  --max-iterations 5 \
  --verbose
```

**与 RalphLoopController 的关系**：
- 外层使用 `WorkflowLoopController` 编排八阶段
- 内层阶段 6（开发）可注入 `RalphLoopController` 做 plan→dev→verify→fix 小循环
- `WorkflowStage.to_stage_kind()` 提供两者之间的映射（详见设计文档 §10.3.2）

**绝对禁止**：
❌ 未经过设计阶段直接开始编码
❌ 文档未编写或未完成就开始开发
❌ 未经过设计评审直接实施
❌ 使用通用的 AI 美学（AI slop）
❌ 开发完成后不对照文档检查就发布（v2.8 新增）
❌ 代码中残留 TODO/FIXME 未实现就发布（v2.8 新增）

## 高级功能

### 代码走读与审查 (v2.3)

```bash
# 执行真正的多角色协作代码走读（使用 Trae Agent 调度）
python3 scripts/multi_role_collaborative_analyzer.py /path/to/project --workspace /workspace

# 简化的多角色代码走读
python3 scripts/multi_role_code_walkthrough.py /path/to/project --workspace /workspace
```

**真正的多角色协作分析流程**:

1. **阶段一：项目扫描**
   - 递归扫描项目目录
   - 识别源代码文件、配置文件、文档文件
   - 统计项目基本信息
   - 检测技术栈和框架
   - 识别项目模块

2. **阶段二：调用 Trae Agent 调度**
   - 使用 `trae_agent_dispatch_v2.py` 分发任务
   - 每个角色使用专属 prompt 模板
   - 角色包括：架构师、产品经理、独立开发者、UI 设计师、测试专家
   - 各角色独立执行真实分析

3. **阶段三：文档对齐**
   - 收集各角色分析结果
   - 识别共识点与差异点
   - 合并统一的代码地图
   - 生成代码走读审查报告

**输出文档**:

| 文档 | 内容 |
|------|------|
| `<project>-ALIGNED-CODE-MAP.md` | 统一代码地图：项目概览、架构分层、多角色分析结果、对齐结果 |
| `<project>-CODE-REVIEW-REPORT.md` | 代码走读审查报告：审查概述、架构评审、代码质量评估、文档一致性检查、改进建议 |

**代码地图内容** (核心结构，不含审查风险):
- 项目概览
- 架构视图
- 代码结构
- 多角色视角摘要
- 分析共识

**审查报告内容** (含风险和建议):
- 审查概览
- 架构评审
- 代码质量评估
- 多角色共识
- **文档与代码一致性检查清单** ← v2.3 新增
- 改进建议
- 附录

### 3D 代码地图可视化 (v2.3)

基于 Three.js 的交互式代码结构可视化，支持流动动画和主题切换。

```bash
# 生成可视化数据
python3 scripts/code_map_generator_v2.py /path/to/project --visual
# 打开可视化页面
~/.trae/skills/docs/code-map-visualizer.html
```

**功能**: 3D 场景渲染、前后端分层、调用链路、动态流动效果、主题切换
**详细说明**: `docs/guides/VISUALIZATION.md`

### 任务可视化页面 (v2.3)

实时展现各角色任务状态、进度、依赖关系、交接过程。

```bash
~/.trae/skills/docs/task-visualizer.html
```

**功能**: 任务统计、角色卡片、依赖关系、交接时间线、协同关系图
**详细说明**: `docs/guides/VISUALIZATION.md`

### 代码地图生成

```bash
python3 scripts/code_map_generator_v2.py /path/to/project --workspace /workspace

# 输出: <project>-CODE_MAP.md
```

### 项目理解

```bash
python3 scripts/project_understanding.py /path/to/project
```

### 规范驱动开发
```bash
python3 scripts/spec_tools.py init
python3 scripts/spec_tools.py analyze
python3 scripts/spec_tools.py update --spec-file SPEC.md
```

### Ponytail 决策梯（v2.6 新增 — 少写多余代码）

> **来源**: Ponytail 项目（6 步决策梯）+ 项目规则（16 条不可简化红线）
> **目的**: 在 Karpathy Simplicity First 原则之上，提供可执行的"写代码前先停一停"决策梯

**6 步决策梯**（按顺序停在第一个满足的台阶）:
1. **YAGNI** — 这东西真的需要存在吗？→ 推测性需求 = 跳过
2. **标准库优先** — 语言标准库能搞定？→ 直接用
3. **平台原生** — 运行时平台自带功能能覆盖？→ 用平台特性
4. **复用现有** — 已安装的依赖能解决？→ 复用，不新增依赖
5. **一行优先** — 能写成一行？→ 写成一行（不牺牲可读性）
6. **最小可行** — 以上都不行 → 写最少能做工作的代码

**三种强度模式**:
| 模式 | 说明 | 适用角色 |
|------|------|---------|
| `lite` | 精简版决策梯 | test_expert, ui_designer |
| `full`（默认） | 完整 6 步 + 16 条红线 | solo_coder, architect |
| `ultra` | YAGNI 极端主义（autonomous 模式自动降级为 full） | 手动指定 |

**16 条不可简化红线**:
- 原始 Ponytail 6 条（输入校验、错误处理、安全、无障碍、用户要求、硬件校准）
- 项目规则 10 条（真实业务逻辑、需求文档功能、非平凡逻辑检查、并发安全、错误处理、日志审计、配置密钥、事务边界、API 契约、隐私数据）

**使用方式**:
```bash
# 在 autonomous 模式中自动注入（默认 full 模式）
# 手动切换模式
/ponytail ultra    # 切换到 ULTRA 模式
/ponytail lite     # 切换到 LITE 模式
/ponytail off      # 关闭决策梯
/ponytail          # 查看当前模式
```

**债务台账**:
- 代码中标记 `# ponytail: <说明>` 或 `# ponytail: <上限>, <升级路径>` 记录故意简化
- `DebtCollector` 自动扫描，识别无升级路径的债务（`no_trigger`）超过阈值时告警
- `RequirementTracer` 追踪需求文档 `[REQ-XXX]` 标记到代码实现的覆盖情况

**测试**:
```bash
# 运行全部 Ponytail 测试（10 个文件，98 个测试用例）
bash scripts/tests/scripts/run_ponytail_tests.sh
```

详细指南见 `docs/guides/PONYTAIL_GUIDE.md`。

### Autonomous Mode（v2.6 / Phase 18 — Ralph 风格自主编排）

> **来源**: Ralph 自主编排框架 + gnhf 库启发
> **目的**: 让多角色团队在用户睡觉时自主完成完整项目生命周期

**4 阶段循环**（plan → dev → verify → fix）:
```
┌─────────────────────────────────────────────────┐
│  Plan（规划）→ Dev（开发）→ Verify（验证）→ Fix（修复）│
│       ↑                                          │
│       └──────────── 循环直到完成 ────────────────┘
└─────────────────────────────────────────────────┘
```

**9 个核心组件**:
| 组件 | 功能 |
|------|------|
| LoopController | Ralph 循环控制器（4 阶段调度） |
| RunState | 运行状态持久化（SHA256 校验 + Resume） |
| NotesMemory | Notes 跨轮记忆（notes.md 读写） |
| GitDriver | Git 驱动（自动 commit + 分支管理） |
| SleepGuard | 防休眠守护（阻止系统休眠） |
| SmartConfirmation | 智能确认（三态：auto-approve/ask-user/fail-closed） |
| AutoSkillLoader | Auto-skill 加载器（自动加载所需 skill） |
| DispatcherAdapter | Dispatcher 适配器（Claude Code / Trae） |
| ConfigLoader | 配置加载器（autonomous.yml） |

**17 个 CLI flag**:
```bash
python3 scripts/trae_agent_dispatch_v2.py \
    --auto-mode \
    --auto-goal "实现用户登录功能" \
    --auto-max-iterations 10 \
    --auto-confirmation smart \
    --auto-git-enabled \
    --auto-skill-injection \
    --auto-notes-memory \
    --auto-sleep-guard \
    --auto-resume \
    --auto-ponytail-mode full \
    # ... 更多 flag 见 AUTONOMOUS_MODE_GUIDE.md
```

详细指南见 `docs/guides/AUTONOMOUS_MODE_GUIDE.md`。

### Dynamic Workflows（v1.7 — 6 大动态工作流模式）

> **来源**: Anthropic Multi-Agent Research + 项目实践
> **目的**: 根据任务特征自动选择最优工作流模式
> **v2.8.3 更新**: 6 大模式作为提示词层概念保留，由宿主 LLM 执行路由决策。脚本层 12 个实现模块（`scripts/dynamic_workflow/`）已全部归档——零外部引用，确认为死代码。仅保留 `semantic_embedder.py`（移至 `scripts/` 根目录）提供 TFIDF/Hashing 确定性相似度。

**6 大模式**（宿主 LLM 执行）:
| 模式 | 适用场景 | 执行方式 |
|------|---------|---------|
| classifier-dispatch | 任务分类后分发 | 分类器 → 角色分发 |
| fan-out-aggregate | 并行处理 + 汇总 | 扇出 N 个子任务 → 聚合结果 |
| adversarial-verify | 对抗式验证 | 生成 → 审查 → 修复循环 |
| generate-filter | 生成 + 过滤 | 批量生成 → 质量过滤 |
| tournament | 锦标赛选择 | 多方案竞争 → 最优胜出 |
| loop-until-done | 循环直到完成 | 迭代直到成功标准满足 |

详细方案见 `docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md`。

### Cybernetics 工程控制论（v2.5 — 三环控制模型）

> **来源**: 钱学森工程控制论 + ICLR 2026 Profile-Aware Maneuvering
> **目的**: 通过反馈控制环实现自适应执行
> **v2.8.3 更新**: 4 个低耦合文件已归档（cybernetics_integration / cybernetics_bridge / hierarchical_control / context_fingerprint_integration）。3 个高耦合核心组件保留。

**保留的核心组件**（`scripts/` 根目录）:
| 组件 | 功能 | 引用数 |
|------|------|--------|
| `performance_fingerprint.py` | 性能画像（执行案例记录 + 相似案例检索） | 23 |
| `feedback_control_loop.py` | 反馈控制环（感知-决策-执行-反馈） | 11 |
| `guard_coordinator.py` | 守护协调器（执行前验证 + 异常检测） | 5 |

**三环控制模型**:
- **战略层**: 长期目标 + 资源规划
- **战术层**: 中期策略 + 任务分解
- **执行层**: 短期动作 + 实时反馈

详细分析见 `docs/dev/CYBERNETICS_ANALYSIS.md`。

### 插件热加载（v2.6 / Phase 17 — V3 插件架构）

> **目的**: 支持运行时动态加载/卸载插件，无需重启

**3 种加载路径**:
1. **Drop-in 目录加载**: 自动扫描 `drop-in/` 目录下的插件
2. **Hot Register API**: 通过 `hot_register()` API 动态注册
3. **HotReloadWatcher**: 文件监视器自动检测变更并重载

**核心模块**（`scripts/dispatcher/`）:
- `goal_dispatcher.py` — Goal 调度器（DAG 依赖图）
- `plugin_context.py` — 插件上下文
- `drop_in_loader.py` — Drop-in 目录加载器
- `hot_reload_watcher.py` — 热加载监视器
- `reload_guard.py` — 重载守护（Condition 替代 Event）

**V3 插件实现**（`scripts/plugins/`）:
- `autonomous.py` — Ralph Autonomous 插件
- `multi_goal.py` — 多 Goal 编排插件（DAG + Resume + Reuse + Schedule + Report）
- `graph.py` — 图编排插件
- `loop.py` — 循环编排插件
- `resume.py` — 断点续跑插件
- `cancel.py` — 取消插件

详细设计见 `docs/dev/PHASE17_PLAN.md`。

### UI/UX 巡检与视觉回归（v2.7 新增 — 前端质量门禁工具）

> **目的**: 在 E2E 测试阶段提供可复用的 UI/UX 质量与视觉回归检测脚本，
> 作为「UI 设计师」与「测试专家」角色的标准前端质量门禁。
> **设计原则**: 标准库优先（Playwright + PIL）、YAGNI、失败安全。

#### `scripts/uiux_analyzer.py`（UI/UX 巡检分析器）

4 大检测维度：
| 维度 | 检测项 | 关键阈值 |
|------|--------|---------|
| 可访问性 (A11y) | WCAG AA 对比度、img alt、form label、语义化标签、键盘可达 | 正常文本 4.5:1 / 大文本 3:1 |
| 交互质量 | 按钮最小尺寸、焦点可见性、加载反馈 | 最小可点击 ≥44px（Apple HIG） |
| 布局与响应式 | 元素重叠、文字截断、视口溢出 | — |
| UX 反模式 | 强制注册、破坏性操作无确认、表单无校验 | — |

**关键类**:
- `UIUXIssue`（dataclass）— 包含 `severity` (HIGH/MEDIUM/LOW) / `category` (a11y/interaction/layout/ux) / `rule` / `element` / `message` / `fix` / `metric`
- `UIUXAnalyzer`（核心）— `audit(page)` → `list[UIUXIssue]`，`dump(path)` 输出 JSON

**用法示例**:
```python
from uiux_analyzer import UIUXAnalyzer

analyzer = UIUXAnalyzer()
page.goto("https://example.com/login")
issues = analyzer.audit(page)
analyzer.dump(Path("reports/uiux.json"))
for issue in issues:
    if issue.severity == "HIGH":
        print(f"[{issue.category}] {issue.message} → {issue.fix}")
```

**Playwright 综合探针**: 一次 `page.evaluate` 取齐所有探针数据（图片/表单/按钮/链接/标题/错误），避免多次往返。

#### `scripts/visual_regression.py`（视觉回归 + 显示完整性）

3 大检测维度：
| 维度 | 检测项 | 实现 |
|------|--------|------|
| 视觉回归 | 像素级 Diff、SSIM 区域级 Diff | PIL `ImageChops` + 简化 SSIM |
| 数据显示不全 | 文本截断、元素溢出视口、图片未加载、骨架屏 >10s、长表格横向滚动 | Playwright DOM 检查 |
| 显示错误 | 红色文字/背景、错误关键词、Ant Design / Arco / Element UI error Toast、浏览器原生 dialog | HSV 检测 + 关键词 + 类名匹配 |

**关键类**:
- `ChangedRegion`（dataclass）— `x/y/width/height/pixel_count/severity`
- `DiffResult`（dataclass）— 完整 diff 结果（含 `pixel_diff_ratio` / `ssim_score` / `changed_regions` / `data_incomplete` / `display_errors`）
- `VisualRegressionChecker`（核心）— `compare(baseline, current, step) → DiffResult`

**软依赖**:
- Pillow（必需）
- numpy（可选，启用更好的 SSIM）
- playwright（必需，DOM 检查）

**用法示例**:
```python
from visual_regression import VisualRegressionChecker

checker = VisualRegressionChecker(pixel_diff_threshold=0.01)
result = checker.compare(
    baseline_path="baseline/login.png",
    current_path="current/login.png",
    test_id="TC-001",
    step="submit_form",
    page=page,  # 用于 DOM 检查
)
if result.pixel_diff_ratio > 0.01:
    print(f"⚠️ 像素差异 {result.pixel_diff_ratio:.2%}")
    for region in result.changed_regions:
        print(f"  变化区域: {region.severity} ({region.width}x{region.height})")
```

**CLI 用法**:
```bash
# UI/UX 巡检（需要先启动浏览器/Playwright session）
python3 scripts/uiux_analyzer.py --url https://example.com --out report.json

# 视觉回归
python3 scripts/visual_regression.py \
    --baseline baseline/login.png \
    --current current/login.png \
    --threshold 0.01 \
    --out diff_report.json
```

**角色集成**:
- **UI 设计师** — 交付稿前自检：`uiux_analyzer.audit(page)` 输出 `reports/uiux.json` 供评审
- **测试专家** — E2E 套件像素级断言：替代人工对比基线截图
- **Solo Coder** — PR 门禁：CI 中调用 CLI，输出 JUnit XML 报告

**失败安全设计**: 任一检查器异常被 try/except 隔离，不影响主流程与其他检查；返回的 `error` 字段记录异常原因供排查。

## 文档结构

```
docs/
├── guides/                 # 用户指南（Ponytail/Autonomous/Karpathy/可视化）
├── spec/                   # 规范文档（CONSTITUTION/SPEC/role-prompts）
│   └── role-prompts/       # 5 个核心角色 + 逻辑漏洞审查专家的代码分析模板
├── dev/                    # 开发文档（各 Phase 设计方案 + 最终报告）
├── roles/                  # 角色执行记录（architect/product-manager/solo-coder/test-expert/ui-designer）
└── project-understanding/  # 项目理解文档
```

## 故障排查

### 角色识别错误
```bash
# 明确指定角色
python3 scripts/trae_agent_dispatch_v2.py \
    --task "..." \
    --agent architect
```

### 共识未触发
```bash
# 显式要求共识
python3 scripts/trae_agent_dispatch_v2.py \
    --task "..." \
    --consensus true
```

## 扩展开发

### 添加新角色
1. 在 `roles.json` 中添加角色配置
2. 更新关键词列表
3. 调整调度规则

### 自定义调度规则
修改 `AgentDispatcher.analyze_task()` 方法。

## 总结

Trae Multi-Agent Dispatcher 提供了：
- ✅ 智能角色识别（5 个核心角色 + AI 语义匹配）
- ✅ 多角色协同 + 共识机制
- ✅ 八阶段标准工作流程（v2.8 新增阶段 8：文档对照代码审查）
- ✅ Karpathy 四大核心原则强制执行（v2.4）
- ✅ Cybernetics 工程控制论三环控制（v2.5）
- ✅ Ponytail 决策梯 + 16 条不可简化红线（v2.6）
- ✅ Autonomous Mode 自主编排（Phase 18，4 阶段循环 + 9 核心组件）
- ✅ Dynamic Workflows 6 大动态工作流模式（v1.7）
- ✅ 插件热加载 V3 架构（Phase 17）
- ✅ 代码走读与审查 + 3D 代码地图可视化
- ✅ UI 设计（避免 AI slop）

通过智能调度 + 自主编排 + 决策梯约束，减少用户干预，提升协作效率！
