---
name: multi-agent-team
slug: multi-agent-team
description: 基于任务类型动态调度到合适的智能体角色（架构师、产品经理、测试专家、独立开发者、UI 设计师）。支持多智能体协作、共识机制、完整项目生命周期管理、规范驱动开发、代码走读审查和项目理解能力。支持中英文双语。v2.4 新增 Karpathy 四大核心原则，v2.5 新增 Cybernetics 工程控制论增强，v2.6 新增 Ponytail 决策梯（少写多余代码）、Autonomous 自主迭代模式、Dynamic Workflows 6 大模式、插件热加载，v2.7 新增 UI/UX 巡检分析与视觉回归测试脚本。
---

# Multi-Agent Team Dispatcher (AI-Enhanced)

基于任务类型和上下文，自动调度到最合适的智能体角色（架构师、产品经理、测试专家、Solo Coder、UI 设计师）。

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
10. **七阶段标准工作流程**: 需求分析→架构设计→测试设计→任务分解→开发实现→测试验证→发布评审
11. **UI 设计**: 创建独特、生产级的 UI 界面，避免通用的 AI "slop" 美学

## 快速开始

### 基础使用
```bash
# 自动调度（推荐）
python3 scripts/trae_agent_dispatch.py \
    --task "设计系统架构"

# 指定角色
python3 scripts/trae_agent_dispatch.py \
    --task "实现功能" \
    --agent solo_coder

# 多角色共识
python3 scripts/trae_agent_dispatch.py \
    --task "启动新项目" \
    --consensus true
```

### 完整项目流程
```bash
# 启动完整项目（自动执行 7 个阶段）
python3 scripts/trae_agent_dispatch.py \
    --task "启动项目：安全浏览器广告拦截功能" \
    --project-full-lifecycle
```

### AI 增强模式 (v2.1 新增)
```bash
# 使用 AI 语义匹配（默认）
python3 scripts/trae_agent_dispatch.py \
    --task "设计微服务架构，支持高并发和弹性扩展" \
    --agent auto  # AI 会自动匹配最合适的角色

# 查看 AI 匹配结果和解释
python3 scripts/trae_agent_dispatch.py \
    --task "实现用户认证和权限管理" \
    --agent auto \
    --explain  # 显示 AI 匹配原因和置信度

# 使用传统关键词匹配（向后兼容）
python3 scripts/trae_agent_dispatch.py \
    --task "编写单元测试" \
    --agent test_expert \
    --match-strategy keyword
```

## AI 集成说明 (v2.1)

### AI 能力

- **语义理解**: 深层语义分析、上下文感知、多义词消歧
- **智能匹配**: 多维度评分（能力 50% + 技能 30% + 语义 20%），可解释结果
- **匹配策略**: `ai_enhanced`（默认）、`semantic`、`keyword`、`hybrid`
- **代码审查**: 质量评估、性能分析、安全检查、最佳实践建议
- **知识问答**: 技术咨询、架构建议、工具推荐

### AI 配置

```yaml
ai_integration:
  enabled: true
  provider: trae_ai_assistant
  features:
    - semantic_matching
    - intelligent_reasoning
    - context_understanding
  config:
    max_tokens: 4096
    temperature: 0.7
    top_p: 0.9
    use_cache: true
    fallback_to_keyword: true
```

### 性能优化

- **缓存机制**: 相同请求直接返回缓存结果
- **降级策略**: AI 不可用时自动降级到关键词匹配
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
**详细 prompt**: `docs/roles/architect/prompt.md`

### 2. 产品经理 (Product Manager)
**职责**: 定义用户价值清晰、需求明确、可落地、可验收的产品
**触发关键词**: 需求、产品、PRD、用户故事、验收标准、竞品分析
**详细 prompt**: `docs/roles/product-manager/prompt.md`

### 3. 测试专家 (Test Expert)
**职责**: 确保全面、深入、自动化、可量化的质量保障
**触发关键词**: 测试、质量、验收、自动化、性能测试、缺陷、评审、门禁
**详细 prompt**: `docs/roles/test-expert/prompt.md`

### 4. 独立开发者 (Solo Coder)
**职责**: 编写完整、高质量、可维护、可测试的代码
**触发关键词**: 实现、开发、代码、修复、优化、重构、单元测试、文档
**详细 prompt**: `docs/roles/solo-coder/prompt.md`

### 5. UI 设计师 (UI Designer)
**职责**: 创建独特、生产级的 UI 界面，具有高设计质量，避免通用的 AI "slop" 美学
**触发关键词**: UI设计、界面设计、前端设计、视觉设计、UI/UX、UI原型、界面美化、UI优化、UI重构
**详细 prompt**: `docs/roles/ui-designer/prompt.md`

## 七阶段标准工作流程

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
阶段 8: 发布评审（多角色）
```

**绝对禁止**：
❌ 未经过设计阶段直接开始编码
❌ 文档未编写或未完成就开始开发
❌ 未经过设计评审直接实施
❌ 使用通用的 AI 美学（AI slop）

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
python3 scripts/trae_agent_dispatch.py \
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

**6 大模式**:
| 模式 | 适用场景 | 执行方式 |
|------|---------|---------|
| classifier-dispatch | 任务分类后分发 | 分类器 → 角色分发 |
| fan-out-aggregate | 并行处理 + 汇总 | 扇出 N 个子任务 → 聚合结果 |
| adversarial-verify | 对抗式验证 | 生成 → 审查 → 修复循环 |
| generate-filter | 生成 + 过滤 | 批量生成 → 质量过滤 |
| tournament | 锦标赛选择 | 多方案竞争 → 最优胜出 |
| loop-until-done | 循环直到完成 | 迭代直到成功标准满足 |

**12 个实现模块**（`scripts/dynamic_workflow/`）:
- `pattern_composer.py` — 模式组合器
- `pattern_executor.py` — 模式执行器
- `pattern_tier_resolver.py` — 模式层级解析器
- `subagent_sandbox.py` — Subagent 沙箱
- `model_router.py` — 模型路由器
- `token_budget_guard.py` — Token 预算守护
- `semantic_embedder.py` — 语义嵌入器（TFIDF/Hashing/SentenceTransformer）
- `skill_injector.py` — Skill 注入器
- `interruption_recovery.py` — 中断恢复
- `workflow_step_adapter.py` — 工作流步骤适配器
- `worktree_manager.py` — Worktree 管理器
- `guard.py` — 守护组件

详细方案见 `docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md`。

### Cybernetics 工程控制论（v2.5 — 三环控制模型）

> **来源**: 钱学森工程控制论 + ICLR 2026 Profile-Aware Maneuvering
> **目的**: 通过反馈控制环实现自适应执行

**6 个核心组件**（`scripts/` 根目录）:
| 组件 | 功能 |
|------|------|
| `feedback_control_loop.py` | 反馈控制环（感知-决策-执行-反馈） |
| `performance_fingerprint.py` | 性能画像（执行案例记录 + 相似案例检索） |
| `guard_coordinator.py` | 守护协调器（执行前验证 + 异常检测） |
| `hierarchical_control.py` | 分层控制（战略层/战术层/执行层） |
| `cybernetics_integration.py` | Cybernetics 集成入口 |
| `context_fingerprint_integration.py` | 上下文画像集成 |

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
python3 scripts/trae_agent_dispatch.py \
    --task "..." \
    --agent architect
```

### 共识未触发
```bash
# 显式要求共识
python3 scripts/trae_agent_dispatch.py \
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
- ✅ 七阶段标准工作流程
- ✅ Karpathy 四大核心原则强制执行（v2.4）
- ✅ Cybernetics 工程控制论三环控制（v2.5）
- ✅ Ponytail 决策梯 + 16 条不可简化红线（v2.6）
- ✅ Autonomous Mode 自主编排（Phase 18，4 阶段循环 + 9 核心组件）
- ✅ Dynamic Workflows 6 大动态工作流模式（v1.7）
- ✅ 插件热加载 V3 架构（Phase 17）
- ✅ 代码走读与审查 + 3D 代码地图可视化
- ✅ UI 设计（避免 AI slop）
- ✅ UI/UX 巡检分析与视觉回归测试（v2.7，前端质量门禁）

通过智能调度 + 自主编排 + 决策梯约束，减少用户干预，提升协作效率！
