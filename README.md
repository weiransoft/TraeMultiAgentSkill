# Trae Multi-Agent Skill

🎭 基于任务类型动态调度到合适的智能体角色（架构师、产品经理、测试专家、独立开发者、UI 设计师）。支持多智能体协作、共识机制、完整项目生命周期管理、规范驱动开发、代码地图生成、项目理解能力和 UI 设计能力。支持中英文双语。

## 🎉 2026 年 5 月最新更新 (v2.5)

> 来源：https://github.com/Jiaqi-Guo-0114/cybernetics-agent  
> 理论依据：ICLR 2026 Profile-Aware Maneuvering 架构、钱学森工程控制论（系统工程、系统学）、Norbert Wiener 控制论、Ashby 必要多样性定律

- ✅ **Cybernetics 工程控制论增强 (v2.5)** - 基于 cybernetics-agent 项目引入反馈闭环、自适应和可观测性增强
  - 🔄 **三环控制模型**：战略层（任务规划、AI动态规划）、战术层（Guard验证、异常检测）、执行层（任务执行、反馈收集）
  - 💫 **反馈控制环**：感知-决策-执行-反馈完整闭环，基于案例的策略选择（非PID，适配认知任务）
  - 📊 **性能画像**：执行案例记录、失败/成功模式提取、相似案例检索（非预测）、冷启动优雅降级
  - 🛡️ **守护协调器**：执行前预验证、实时异常检测、执行后审查、AI增强风险评估
  - 🎯 **预期收益**：执行成功率 +8%，方差 -67%，人工介入 -70%
  - 核心组件：`scripts/feedback_control_loop.py`、`scripts/performance_fingerprint.py`、`scripts/guard_coordinator.py`、`scripts/hierarchical_control.py`

## 🎉 2026 年 4 月最新更新 (v2.4)

- ✅ **Karpathy 四大核心原则** - 融入 Andrej Karpathy 的编程智慧：Think Before Coding、Simplicity First、Surgical Changes、Goal-Driven Execution
- ✅ **行为准则体系** - 所有角色统一的 LLM 编程行为准则，减少错误、过度复杂、无关修改
- ✅ **验证检查点机制** - 目标驱动的验证流程，确保每个阶段都有明确的成功标准
- ✅ **角色专属应用指南** - 每个角色都有 Karpathy 原则的具体应用场景和行为准则
- ✅ **多角色代码走读 (v2.3)** - 架构师、产品经理、独立开发者、UI 设计师、测试专家多视角分析代码，生成对齐后的统一代码地图
- ✅ **代码地图 Workspace 支持 (v2.3)** - 支持一个 workspace 包含多个项目的场景，明确项目标识
- ✅ **3D 代码地图可视化 (v2.3)** - 基于 Three.js 的交互式代码结构可视化，动态流动效果，深色/浅色主题切换
- ✅ **任务可视化页面 (v2.3)** - 实时展现各角色任务状态、进度、依赖关系、交接过程、协同关系图
- ✅ **文档与代码一致性检查 (v2.3)** - 代码走读审查报告中新增文档与代码差异检查清单
- ✅ **长程 Agent 支持 (v2.2)** - 基于 Anthropic《Effective Harnesses for Long-Running Agents》核心思想，支持 Checkpoint 检查点、Handoff 交接班、TaskList 任务清单
- ✅ **AI 语义理解驱动的角色匹配 (v2.1)** - 使用大模型理解任务深层语义，提供可解释的匹配结果和置信度评分
- ✅ **AI 助手深度集成 (v2.1)** - 集成大模型 AI 助手能力，支持代码审查，知识问答、文本分析等功能
- ✅ **智能缓存和降级策略 (v2.1)** - 性能优化，AI 不可用时自动降级到关键词匹配
- ✅ **UI 设计师角色** - 添加 UI 设计师角色，创建独特、生产级的 UI 界面，避免通用的 AI "slop" 美学
- ✅ **Agent Loop 思考循环修复** - 修复 is_all_tasks_completed 方法，增加连续无进展检测保护机制
- ✅ **规范驱动开发** - 完整的规范工具链，统一的文档管理体系，多角色共识制定规范
- ✅ **代码地图生成** - 自动生成项目代码结构映射，支持 JSON 和 Markdown 格式，识别核心组件和模块依赖
- ✅ **项目理解** - 快速读取项目文档和代码，为各角色生成定制化理解文档，提供项目概览和技术栈分析
- ✅ **八阶段标准工作流程** - 需求分析→架构设计→UI 设计→测试设计→任务分解→开发实现→测试验证→发布评审
- ✅ **跨角色设计评审机制** - PRD 评审、架构评审、UI 设计评审、测试计划评审，开发计划评审
- ✅ **基于文档的任务分解** - 所有角色基于文档进行任务分解，确保文档驱动开发

## 🌍 多语言支持 / Multi-Language Support

本技能支持中英文双语自动切换 / This skill supports automatic Chinese-English language switching:

- **自动识别** / **Auto-detection**: 根据用户语言自动切换响应语言
- **完全覆盖** / **Full Coverage**: 所有输出内容都支持多语言
- **智能匹配** / **Smart Matching**: 代码注释自动匹配现有语言
- **灵活切换** / **Flexible Switching**: 支持会话中切换语言

📄 详细文档 / Detailed documentation:

- **中文文档** / **Chinese Documentation**: [README.md](README.md)
- **English Documentation**: [README_EN.md](README_EN.md)

### 📚 完整文档索引 / Complete Documentation Index

| 文档 / Document | 中文 / Chinese | English |
|----------------|---------------|---------|
| 主文档 / Main | [README.md](README.md) | [README_EN.md](README_EN.md) |
| 使用示例 / Examples | [EXAMPLES.md](EXAMPLES.md) | [EXAMPLES_EN.md](EXAMPLES_EN.md) |
| 进度追踪 / Progress | [progress.template.md](progress.template.md) | [progress_EN.md](progress_EN.md) |
| 依赖说明 / Dependencies | [requirements.txt](requirements.txt) | [requirements_EN.txt](requirements_EN.txt) |

## 📖 目录 / Table of Contents

- [功能特性](#-功能特性)
- [快速开始](#-快速开始)
- [角色介绍](#-角色介绍)
- [使用方法](#-使用方法)
- [安装说明](#-安装说明)
  - [方法 1: 直接使用包装脚本](#方法 -1-直接使用包装脚本)
  - [方法 2: 设置环境变量](#方法 -2-设置环境变量)
  - [方法 3: 创建符号链接](#方法 -3-创建符号链接)
  - [自动安装](#自动安装)
- [配置说明](#-配置说明)
- [示例场景](#-示例场景)
- [技术架构](#-技术架构)
- [贡献指南](#-贡献指南)
- [常见问题](#-常见问题)
- [许可证](#-许可证)

## ✨ 功能特性

### AI 增强能力 (v2.1 新增)

1. **AI 语义理解驱动的角色匹配** 🧠
   - 使用大模型理解任务的深层语义
   - 提供可解释的匹配结果和置信度评分
   - 支持多种匹配策略（AI 增强、语义、关键词、混合）
   - 智能缓存和降级策略

2. **AI 助手深度集成** 🤖
   - 代码审查和建议（ai_assistant.py）
   - 知识问答和技术咨询
   - 文本分析和摘要
   - 自然语言交互界面

3. **性能优化** ⚡
   - 智能缓存机制（减少 40-60% API 调用）
   - 自动降级策略（AI 不可用时使用关键词匹配）
   - 批量处理和异步请求支持

### 长程 Agent 支持 (v2.2 新增)

基于 Anthropic 文章《Effective Harnesses for Long-Running Agents》的核心思想，解决长程任务中的"断片"问题：

1. **Checkpoint 检查点机制** 💾
   - 定期保存任务状态（像人类工程师 git commit）
   - 支持从任意断点恢复
   - 数据完整性校验（SHA256 哈希）
   - 自动过期清理
   - 核心文件：`scripts/checkpoint_manager.py`

2. **Handoff 交接班协议** 🔄
   - 标准化交接文档（JSON + Markdown）
   - 交接原因记录
   - 信心度评估
   - 重要注意事项传递
   - 支持双智能体架构（Planner + Executor）

3. **TaskList 任务清单** 📋
   - 像人类工程师维护 TODO.md 一样管理任务
   - 任务拆解和优先级排序
   - 依赖关系管理
   - 进度跟踪
   - Markdown 导出
   - 核心文件：`scripts/task_list_manager.py`

4. **WorkflowEngineV2 增强版工作流** ⚙️
   - 集成 Checkpoint + TaskList + Handoff
   - 智能任务拆分
   - 定期自动保存检查点
   - 支持 Agent 交接班
   - 断点恢复机制
   - 核心文件：`scripts/workflow_engine_v2.py`

**使用示例**:
```bash
# 创建带长程支持的工作流
python3 scripts/workflow_engine_v2_demo.py \
    --task "实现完整电商系统"

# 运行测试
python3 scripts/tests/run_tests.py
```

**测试结果**: 24 个测试全部通过 ✅

### Karpathy 四大核心原则 (v2.4 新增)

基于 Andrej Karpathy 对 LLM 编程常见陷阱的观察，强制执行四大核心原则：

1. **Think Before Coding（三思而后行）** 🧠
   - 明确假设、呈现权衡、遇到不清就问
   - 核心文件：`scripts/karpathy_principle_enforcer.py`

2. **Simplicity First（简单优先）** 🎯
   - 最小代码、无 speculative features、无过度抽象

3. **Surgical Changes（精准修改）** 🔬
   - 只改需要的、不改无关的、保持风格一致

4. **Goal-Driven Execution（目标驱动）** ✅
   - 定义成功标准、验证检查点、迭代直到完成

**Karpathy 原则执行检查器**:
- 原则合规性检查
- 违规检测与提醒（5 级严重度：CRITICAL/HIGH/MEDIUM/LOW/INFO）
- 验证检查点管理
- 执行报告生成（JSON 导出）

### Claude Code SubAgent 适配器 (v2.4 新增)

跨平台 Agent 适配，统一 Claude Code / Trae IDE 的 subagent 调用接口：

- `ClaudeCodeSubAgentAdapter` 类（`scripts/claude_code_subagent_adapter.py`）
- 自动平台检测：`claude_code` / `trae` / `unknown`
- 统一 `invoke_agent()` 接口
- 环境变量检测：`CLAUDE_CODE_ENV` / `TRAE_ENV`

### 核心能力

1. **智能角色调度** 🎯
   - 根据任务描述自动识别需要的角色
   - 基于关键词匹配和位置权重算法
   - 置信度评估和最佳角色选择

2. **多角色协同** 🤝
   - 组织多个角色共同完成复杂任务
   - 共识机制确保决策质量
   - 角色间上下文共享

3. **上下文感知** 🧠
   - 根据项目阶段选择角色
   - 历史上下文智能继承
   - 任务链自动关联

4. **完整项目生命周期** 📊
   - 8 阶段项目流程支持
   - 从需求到部署全流程
   - 质量门禁和评审机制

5. **规范驱动开发** 📋
   - 完整的规范工具链（spec_tools.py）
   - 项目宪法（CONSTITUTION.md）制定
   - 项目规范（SPEC.md）自动生成
   - 规范分析报告（SPEC_ANALYSIS.md）
   - 规范一致性检查和验证
   - 多角色共识制定规范

6. **代码地图生成** 🗺️
   - 自动生成项目代码结构映射（code_map_generator_v2.py）
   - 支持 JSON 和 Markdown 格式输出
   - 识别核心组件和模块依赖
   - 可视化项目结构文档
   - 技术栈分析和统计
   - **多项目 Workspace 支持**（v2.3 新增）- 自动识别项目所属 workspace
   - **多角色代码走读**（v2.3 新增）- 架构师、产品经理、独立开发者、UI 设计师、测试专家多视角分析
   - **文档对齐机制**（v2.3 新增）- 多角色分析结果对齐，生成统一代码地图
   - **3D 代码地图可视化**（v2.3 新增）- Three.js 交互式可视化，动态流动效果，主题切换
   - **任务可视化页面**（v2.3 新增）- 各角色任务状态、进度、依赖关系、交接过程
   - 核心文件：`scripts/code_map_generator_v2.py`, `scripts/multi_role_code_walkthrough.py`, `docs/code-map-visualizer.html`, `docs/task-visualizer.html`

8. **项目理解** 📚
   - 快速读取项目文档和代码（project_understanding.py）
   - 为各角色生成定制化理解文档
   - 提供项目概览和技术栈分析
   - 作为工作初始化上下文
   - 角色特定见解和建议

9. **UI 设计** 🎨
   - 创建独特、生产级的 UI 界面（UI_DESIGNER_PROMPT.md）
   - 避免通用的 AI "slop" 美学
   - 详细的设计美学指南（字体、色彩、动画、布局）
   - 完整的设计系统和风格指南
   - 高保真原型创建

10. **八阶段标准工作流程** 📊
    - 阶段 1: 需求分析（产品经理）
    - 阶段 2: 架构设计（架构师）
    - 阶段 3: UI 设计（UI 设计师）
    - 阶段 4: 测试设计（测试专家）
    - 阶段 5: 任务分解（独立开发者）
    - 阶段 6: 开发实现（独立开发者）
    - 阶段 7: 测试验证（测试专家）
    - 阶段 8: 发布评审（多角色）

9. **跨平台兼容性** 🌍
   - 支持 Windows、Mac 和 Linux
   - 统一的路径处理和字符编码
   - 跨平台脚本执行

### 角色 Prompt 系统

每个角色都配备完整的工作规则和质量标准：

- ✅ **系统性思维规则** - 确保设计完整性
- ✅ **深度思考规则** - 5-Why 分析法找根因
- ✅ **零容忍清单** - 禁止 mock、硬编码、简化
- ✅ **验证驱动设计** - 完整验收标准
- ✅ **完整性检查** - 多维度检查清单
- ✅ **自测规则** - 3 层测试验证
- ✅ **UI 设计美学** - 避免 AI slop，创建独特设计

## 🚀 快速开始

### 前置要求

- Python 3.8+
- Trae IDE
- 基础命令行知识

### 基础使用

在 Trae 中直接使用，无需额外命令：

```
# 架构设计任务
设计系统架构：包括模块划分、技术选型、部署方案

# 产品需求定义
定义产品需求：广告拦截功能，需要明确的验收标准

# 测试策略制定
制定测试策略：覆盖正常、异常、边界、性能场景

# 功能开发
实现广告拦截功能：完整代码，包含单元测试
```

智能体会自动识别任务类型并调用对应角色！

### 高级使用

使用调度脚本进行更精细的控制：

```bash
# 自动识别角色
python3 scripts/trae_agent_dispatch.py \
    --task "设计系统架构"

# 指定角色
python3 scripts/trae_agent_dispatch.py \
    --task "实现功能" \
    --agent solo_coder

# 多角色共识
python3 scripts/trae_agent_dispatch.py \
    --task "启动新项目：安全浏览器" \
    --consensus true

# 完整项目流程
python3 scripts/trae_agent_dispatch.py \
    --task "安全浏览器广告拦截功能" \
    --project-full-lifecycle

# 项目全生命周期模式（8 阶段标准工作流程）
python3 scripts/trae_agent_dispatch.py \
    --task "实现电商系统用户登录功能" \
    --project-full-lifecycle
# 自动执行：需求分析→架构设计→UI 设计→测试设计→任务分解→开发实现→测试验证→发布评审

# 规范驱动开发
python3 scripts/spec_tools.py init
python3 scripts/spec_tools.py analyze
python3 scripts/spec_tools.py update --spec-file SPEC.md

# 代码地图生成
python3 scripts/code_map_generator_v2.py /path/to/project --workspace /workspace

# 多角色代码走读
python3 scripts/multi_role_code_walkthrough.py /path/to/project --workspace /workspace

# 项目理解
python3 scripts/project_understanding.py /path/to/project
```

## 🎭 角色介绍

### 1. 架构师 (Architect)

**职责**: 设计系统性、前瞻性、可落地、可验证的架构

**核心原则**:
- ✅ 系统性思维 - 设计前回答 4 个关键问题
- ✅ 5-Why 分析法 - 连续追问找到根因
- ✅ 零容忍清单 - 禁止 mock、硬编码、简化
- ✅ 验证驱动设计 - 完整验收标准

**典型输出**:
- 系统架构图（Mermaid）
- 模块职责清单
- 接口定义（输入/输出/异常）
- 数据模型设计
- 部署架构说明

**触发关键词**: 架构、设计、选型、审查、性能、瓶颈、模块、接口、部署

### 2. 产品经理 (Product Manager)

**职责**: 定义用户价值清晰、需求明确、可落地、可验收的产品

**核心原则**:
- ✅ 需求三层挖掘 - 表面→真实→本质
- ✅ SMART 验收标准 - 具体、可衡量、可实现
- ✅ 竞品分析规则 - 至少 5 个竞品对比

**典型输出**:
- 产品需求文档（PRD）
- 用户故事地图
- 验收标准（SMART）
- 竞品分析报告

**触发关键词**: 需求、PRD、用户故事、竞品、市场、调研、验收、UAT、体验

### 3. 测试专家 (Test Expert)

**职责**: 确保全面、深入、自动化、可量化的质量保障

**核心原则**:
- ✅ 测试金字塔 - 70% 单元 +20% 集成 +10%E2E
- ✅ 正交分析法 - 5 类场景全覆盖
- ✅ 真机测试规则 - 真实环境验证

**典型输出**:
- 测试策略文档
- 测试用例（正常/异常/边界/性能/安全）
- 自动化测试脚本
- 质量评估报告

**触发关键词**: 测试、质量、验收、自动化、性能测试、缺陷、评审、门禁

### 5. UI 设计师 (UI Designer)

**职责**: 创建独特、生产级的 UI 界面，具有高设计质量，避免通用的 AI "slop" 美学

**核心原则**:
- ✅ 设计思维规则 - 设计前回答 4 个关键问题
- ✅ UI 设计美学指南 - 字体、色彩、动画、布局
- ✅ 零容忍清单 - 禁止通用字体、陈旧配色、AI slop
- ✅ 验证驱动设计 - 完整验收标准
- ✅ 完整性检查 - 多维度检查清单

**典型输出**:
- 设计哲学文档
- 风格指南
- 高保真原型
- UI 设计文档

**触发关键词**: UI设计、界面设计、前端设计、视觉设计、UI/UX、UI原型、界面美化、UI优化、UI重构

### 4. 独立开发者 (Solo Coder)

**职责**: 编写完整、高质量、可维护、可测试的代码

**核心原则**:
- ✅ 零容忍清单 - 10 项绝对禁止
- ✅ 完整性检查 - 4 维度检查清单
- ✅ 自测规则 - 3 层测试验证

**典型输出**:
- 完整功能代码
- 单元测试（覆盖率>80%）
- 集成测试
- 技术文档

**触发关键词**: 实现、开发、代码、修复、优化、重构、单元测试、文档

## 💡 使用方法

### 场景 1: 项目启动

```bash
# 完整项目启动（多角色共识）
python3 scripts/trae_agent_dispatch.py \
    --task "启动新项目：安全浏览器广告拦截功能" \
    --consensus true \
    --priority high

# 自动组织：
#   1. 产品经理 - 需求定义
#   2. 架构师 - 架构设计
#   3. 测试专家 - 测试策略
#   4. 独立开发者 - 开发计划
```

### 场景 2: 功能开发

```bash
# 单角色调度（快速开发）
python3 scripts/trae_agent_dispatch.py \
    --task "实现广告拦截核心模块" \
    --agent solo_coder \
    --context "基于架构设计文档 v2.0"

# 自动包含：
#   - 架构设计文档作为上下文
#   - 完整性检查清单
#   - 自测要求
```

### 场景 3: 代码审查

```bash
# 多角色代码审查
python3 scripts/trae_agent_dispatch.py \
    --task "审查广告拦截核心模块" \
    --code-review \
    --files src/adblock/ tests/

# 参与角色：
#   - 架构师（架构合规性）
#   - 测试专家（测试覆盖率）
#   - 独立开发者（代码质量）
```

### 场景 4: 紧急 Bug 修复

```bash
# 紧急修复（快速通道）
python3 scripts/trae_agent_dispatch.py \
    --task "紧急修复：生产环境崩溃" \
    --priority critical \
    --fast-track

# 自动处理：
#   - 跳过常规流程
#   - 直接调度资深开发者
#   - 实时进度同步
```

### 场景 5: 规范驱动开发

```bash
# 初始化规范环境
python3 scripts/spec_tools.py init

# 分析规范
python3 scripts/spec_tools.py analyze

# 更新规范文档
python3 scripts/spec_tools.py update --spec-file SPEC.md

# 规范驱动的项目启动
python3 scripts/trae_agent_dispatch.py \
    --task "启动规范驱动项目：电商系统" \
    --spec-driven

# 自动执行：
#   1. 初始化规范环境
#   2. 多角色共识：制定项目宪法
#   3. 产品经理：编写需求规范
#   4. 架构师：编写技术规范
#   5. 规范评审（多角色共识）
#   6. 基于规范分解任务
#   7. 各角色执行任务
#   8. 规范验证和质量评审
```

### 场景 6: 代码地图生成与代码走读

```bash
# 生成代码地图（支持 workspace）
python3 scripts/code_map_generator_v2.py /path/to/project --workspace /workspace

# 输出：
# - Markdown 格式：<project>-CODE_MAP.md

# 真正的多角色协作代码走读（使用 Trae Agent 调度）
python3 scripts/multi_role_collaborative_analyzer.py /path/to/project --workspace /workspace

# 输出：
# - 统一代码地图：<project>-ALIGNED-CODE-MAP.md
# - 代码走读审查报告：<project>-CODE-REVIEW-REPORT.md

# 简化的多角色代码走读
python3 scripts/multi_role_code_walkthrough.py /path/to/project --workspace /workspace

# 生成的内容包括：
#   - 统一代码地图：项目概览、架构分层、多角色分析结果、对齐结果
#   - 审查报告：审查概述、架构评审、代码质量评估、风险点、改进建议
```

### 场景 7: 项目理解

```bash
# 生成项目理解文档
python3 scripts/project_understanding.py /path/to/project

# 输出：
# - 整体项目信息：project_understanding.json
# - 架构师理解：architect_understanding.md
# - 产品经理理解：product_manager_understanding.md
# - 测试专家理解：test_expert_understanding.md
# - 独立开发者理解：solo_coder_understanding.md

# 文档内容包括：
#   - 项目概览和技术栈
#   - 代码结构分析
#   - 文档和依赖分析
#   - 角色特定的见解和建议
```

## 📦 安装说明

### 快速使用（无需安装）

**最简单的方式** - 直接使用包装脚本，可以从任何位置调用：

```bash
# 在任何项目目录下直接调用
/Users/wangwei/claw/.trae/skills/trae-multi-agent/trae-agent \
  --task "你的任务描述" \
  --agent architect
```

### 方法 1: 直接使用包装脚本

包装脚本会自动定位 skill 位置并调用，无需任何配置：

```bash
# 方式 1: 使用完整路径
/Users/wangwei/claw/.trae/skills/trae-multi-agent/trae-agent \
  --task "设计系统架构" --agent architect

# 方式 2: 在项目中使用相对路径（如果 skill 在项目 .trae/skills 下）
./.trae/skills/trae-multi-agent/trae-agent \
  --task "制定测试策略" --agent tester
```

**优点**：
- ✅ 无需安装
- ✅ 无需配置
- ✅ 即开即用
- ✅ 自动定位 skill

### 方法 2: 设置环境变量

将 skill 添加到 PATH，便于全局访问：

```bash
# 添加到 ~/.zshrc 或 ~/.bashrc
export TRAE_MULTI_AGENT_SKILL_PATH="$HOME/claw/.trae/skills/trae-multi-agent"
export PATH="$TRAE_MULTI_AGENT_SKILL_PATH:$PATH"

# 重新加载配置
source ~/.zshrc  # 或 source ~/.bashrc

# 使用
trae-agent --task "设计系统架构" --agent architect
```

**优点**：
- ✅ 全局可用
- ✅ 简短命令
- ✅ 易于管理

### 方法 3: 创建符号链接

创建全局可执行命令：

```bash
# 需要 sudo 权限
sudo ln -s /Users/wangwei/claw/.trae/skills/trae-multi-agent/trae-agent \
           /usr/local/bin/trae-agent

# 使用
trae-agent --task "设计系统架构" --agent architect
```

**优点**：
- ✅ 系统级命令
- ✅ 任何终端可用
- ✅ 与系统命令集成

### 自动安装

运行自动安装脚本（推荐新手）：

```bash
cd /Users/wangwei/claw/.trae/skills/trae-multi-agent
./install.sh
```

安装脚本会：
- 🔧 自动检测 Shell 类型
- 🔧 创建符号链接（需要 sudo）
- 🔧 设置环境变量
- 🔧 创建便捷别名
- 🔧 设置执行权限

### 验证安装

```bash
# 检查是否能找到 skill
trae-agent --help

# 测试调用
trae-agent --task "测试" --agent architect --dry-run

# 检查版本
ls -lh /Users/wangwei/claw/.trae/skills/trae-multi-agent/trae-agent
```

### 故障排查

**问题**: 提示 "找不到 trae-multi-agent skill"

**解决方案**:
1. 检查环境变量：`echo $TRAE_MULTI_AGENT_SKILL_PATH`
2. 检查路径是否存在：`ls -la $TRAE_MULTI_AGENT_SKILL_PATH`
3. 重新加载配置：`source ~/.zshrc`

**问题**: 权限错误

**解决方案**:
```bash
chmod +x /Users/wangwei/claw/.trae/skills/trae-multi-agent/trae-agent
chmod +x /Users/wangwei/claw/.trae/skills/trae-multi-agent/scripts/*.py
```

📖 详细安装文档请查看：[INSTALL.md](INSTALL.md)

## ⚙️ 命令行参数说明

### 基本参数

```bash
python3 scripts/trae_agent_dispatch.py [参数]
```

| 参数 | 类型 | 必需 | 默认值 | 说明 |
|------|------|------|--------|------|
| `--task` | string | ✅ | - | 任务描述 |
| `--agent` | string | ❌ | auto | 指定角色：architect, product-manager, tester, solo-coder, ui-designer, devops, auto |
| `--project-root` | string | ❌ | . | 项目根目录路径 |
| `--task-file` | string | ❌ | - | 任务文件路径（从文件读取任务） |
| `--output` | string | ❌ | - | 输出文件路径 |
| `--verbose` | flag | ❌ | false | 启用详细输出模式 |
| `--dry-run` | flag | ❌ | false | 仅模拟执行，不实际调用智能体 |
| `--use-v1` | flag | ❌ | false | 使用 v1.0 版本逻辑 |
| `--project-full-lifecycle` | flag | ❌ | false | 启用项目全生命周期模式（8 阶段标准工作流程） |

### 角色选项

`--agent` 参数支持以下角色：

- `architect`: 架构师 - 负责系统架构设计、技术选型
- `product-manager`: 产品经理 - 负责需求分析、产品规划
- `tester`: 测试专家 - 负责测试策略、质量保障
- `solo-coder`: 独立开发者 - 负责功能开发、代码实现
- `ui-designer`: UI 设计师 - 负责界面设计、用户体验
- `devops`: DevOps 工程师 - 负责部署、运维
- `auto`: 自动匹配 - 根据任务内容自动识别最适合的角色（默认）

### 使用示例

#### 1. 自动识别角色
```bash
python3 scripts/trae_agent_dispatch.py \
    --task "设计微服务系统架构"
```

#### 2. 指定角色
```bash
python3 scripts/trae_agent_dispatch.py \
    --task "实现用户登录功能" \
    --agent solo-coder
```

#### 3. 项目全生命周期模式（8 阶段）
```bash
python3 scripts/trae_agent_dispatch.py \
    --task "实现电商系统用户登录功能" \
    --project-full-lifecycle
# 自动执行：需求分析→架构设计→UI 设计→测试设计→任务分解→开发实现→测试验证→发布评审
```

#### 4. 从文件读取任务
```bash
python3 scripts/trae_agent_dispatch.py \
    --task-file task.txt \
    --agent architect
```

#### 5. 详细输出模式
```bash
python3 scripts/trae_agent_dispatch.py \
    --task "设计系统架构" \
    --verbose
```

#### 6. 模拟执行（不实际调用）
```bash
python3 scripts/trae_agent_dispatch.py \
    --task "实现功能" \
    --dry-run
```

### 项目全生命周期模式详解

使用 `--project-full-lifecycle` 参数后，系统自动执行 8 个阶段：

1. **需求分析**（产品经理）
   - 需求三层挖掘
   - 用户故事地图
   - SMART 验收标准

2. **架构设计**（架构师）
   - 系统架构设计
   - 技术选型
   - 模块职责划分

3. **UI 设计**（UI 设计师）
   - 界面原型设计
   - 交互流程
   - 视觉风格指南

4. **测试设计**（测试专家）
   - 测试策略
   - 测试用例设计
   - 自动化测试方案

5. **任务分解**（开发工程师）
   - 任务拆分
   - 工作量评估
   - 优先级排序

6. **开发实现**（开发工程师）
   - 代码编写
   - 单元测试
   - 代码审查

7. **测试验证**（测试专家）
   - 集成测试
   - 性能测试
   - 缺陷修复验证

8. **发布评审**（架构师 + 产品经理）
   - 代码审查
   - 验收测试
   - 发布准备

## ⚙️ 配置说明

### 技能配置 (skills-index.json)

```json
{
  "version": "1.0.0",
  "name": "trae-multi-agent",
  "enabled": true,
  "global": true,
  "autoInvoke": true,
  "roles": {
    "architect": { "priority": 1 },
    "product_manager": { "priority": 2 },
    "test_expert": { "priority": 3 },
    "solo_coder": { "priority": 4 }
  }
}
```

### 角色识别算法

```python
def analyze_task(task: str):
    """
    分析任务，识别需要的角色
    
    Args:
        task: 任务描述
        
    Returns:
        (最佳角色，置信度，所有匹配的角色列表)
    """
    scores = {}
    matched_roles = []
    
    # 关键词匹配 + 位置权重
    for role, config in ROLES.items():
        score = 0.0
        for keyword in config["keywords"]:
            if keyword in task:
                score += 1.0
        
        # 位置权重：越靠前权重越高
        words = task.split()
        for i, word in enumerate(words):
            for keyword in config["keywords"]:
                if keyword in word:
                    score += 1.0 / (i + 1)
        
        scores[role] = score
    
    # 选择最佳角色
    best_role = max(scores, key=scores.get)
    confidence = min(scores[best_role] / len(keywords), 1.0)
    
    return best_role, confidence, matched_roles
```

### 共识触发条件

```python
def _needs_consensus(task, confidence, matched_roles):
    """判断是否需要多角色共识"""
    
    # 1. 置信度低于阈值
    if confidence < 0.6:
        return True
    
    # 2. 涉及多个专业领域
    if len(matched_roles) >= 2:
        return True
    
    # 3. 任务描述很长
    if len(task) > 200:
        return True
    
    # 4. 包含明确的共识请求
    if any(kw in task for kw in ["共识", "评审", "讨论"]):
        return True
    
    return False
```

## 📋 新功能/功能变更标准工作流程

### 核心原则：先设计、先写文档、再开发

**必须遵循的工作流程**：

```
阶段 1: 需求分析（产品经理）
    ↓ 评审通过
阶段 2: 架构设计（架构师）
    ↓ 评审通过
阶段 3: 测试设计（测试专家）
    ↓ 评审通过
阶段 4: 任务分解（独立开发者）
    ↓
阶段 5: 开发实现（独立开发者）
    ↓
阶段 6: 测试验证（测试专家）
    ↓
阶段 7: 发布评审（多角色）
```

**绝对禁止**：
❌ 未经过设计阶段直接开始编码
❌ 文档未编写或未完成就开始开发
❌ 未经过设计评审直接实施

**文档依赖关系**：
```
PRD 文档（产品经理）
    ↓ [依赖: PRD 评审通过]
架构设计文档（架构师）
    ↓ [依赖: 架构评审通过]
测试计划文档（测试专家）
    ↓ [依赖: 测试计划评审通过]
开发任务列表（开发者）
    ↓ [依赖: 开发完成]
测试报告（测试专家）
    ↓ [依赖: 测试通过]
发布决策（多角色）
```

详细流程说明：[SKILL.md](SKILL.md) - 新功能/功能变更标准工作流程

## 📚 示例场景

### 示例 1: 完整项目启动

**输入**:
```
启动新项目：安全浏览器广告拦截功能
- 支持拦截恶意广告和钓鱼网站
- 性能要求：页面加载延迟<100ms
- 需要完整的测试覆盖
```

**自动流程**:
```
🎯 识别为：多角色共识任务

📋 阶段 1: 需求定义 (产品经理)
   - 用户故事地图
   - 验收标准 (SMART)
   - 竞品分析

📋 阶段 2: 架构设计 (架构师)
   - 系统架构图
   - 技术选型
   - 部署方案

📋 阶段 3: 测试策略 (测试专家)
   - 测试金字塔
   - 自动化方案
   - 质量门禁

📋 阶段 4: 开发计划 (独立开发者)
   - 任务分解
   - 时间估算
   - 风险评估
```

### 示例 2: 功能开发

**输入**:
```
实现广告拦截核心模块
- 基于架构设计文档 v2.0
- 使用 SQLite 存储规则
- 需要完整单元测试
```

**自动处理**:
```
🎯 识别为：独立开发者任务
📊 置信度：0.85

✅ 加载上下文：架构设计文档 v2.0

📋 开发流程:
   1. 需求理解确认
   2. 技术方案设计
   3. 代码实现
      - 核心功能
      - 错误处理
      - 日志记录
   4. 单元测试
      - 覆盖率>80%
      - 边界条件
      - 异常场景
   5. 自测验证
```

### 示例 3: 架构审查

**输入**:
```
审查当前系统架构
- 评估性能瓶颈
- 识别技术债务
- 提出优化建议
```

**自动处理**:
```
🎯 识别为：架构师任务
📊 置信度：0.92

📋 审查清单:
   ✓ 系统边界清晰度
   ✓ 模块职责单一性
   ✓ 接口定义完整性
   ✓ 异常处理覆盖
   ✓ 性能瓶颈分析
   ✓ 安全风险评估
   ✓ 扩展点预留
   ✓ 监控方案

📋 输出:
   - 审查报告
   - 问题清单
   - 优化建议
   - 优先级排序
```

## 🏗️ 技术架构

### 系统架构

```
┌─────────────────────────────────────────┐
│         Trae Multi-Agent Skill          │
├─────────────────────────────────────────┤
│  用户界面层 (Trae IDE)                   │
│  - 自然语言输入                          │
│  - 智能响应输出                          │
├─────────────────────────────────────────┤
│  调度层 (Dispatcher)                     │
│  - 任务分析                              │
│  - 角色识别                              │
│  - 共识组织                              │
├─────────────────────────────────────────┤
│  角色层 (Agent Roles)                    │
│  - 架构师 (Architect)                    │
│  - 产品经理 (Product Manager)            │
│  - 测试专家 (Test Expert)                │
│  - 独立开发者 (Solo Coder)               │
├─────────────────────────────────────────┤
│  执行层 (Executor)                       │
│  - 任务执行                              │
│  - 上下文管理                            │
│  - 结果验证                              │
└─────────────────────────────────────────┘
```

### 数据流

```
用户输入
  ↓
任务分析 (关键词匹配 + 位置权重)
  ↓
角色识别 (置信度评估)
  ↓
单角色任务 → 直接调度
多角色任务 → 组织共识
  ↓
任务执行 (带完整 Prompt)
  ↓
结果验证 (检查清单)
  ↓
输出响应
```

### 核心算法

#### 1. 角色识别算法

```python
def analyze_task(task: str) -> Tuple[str, float, List[str]]:
    """
    分析任务，识别需要的角色
    
    算法:
    1. 关键词匹配
    2. 位置权重计算
    3. 分数累加
    4. 置信度评估
    """
    scores = {}
    matched_roles = []
    
    for role, config in ROLES.items():
        score = 0.0
        matched_keywords = []
        
        # 关键词匹配
        for keyword in config["keywords"]:
            if keyword in task:
                score += 1.0
                matched_keywords.append(keyword)
        
        # 位置权重
        words = task.split()
        for i, word in enumerate(words):
            for keyword in config["keywords"]:
                if keyword in word:
                    score += 1.0 / (i + 1)
        
        if score > 0:
            matched_roles.append(role)
        
        scores[role] = score
    
    # 选择最佳角色
    best_role = max(scores, key=scores.get)
    max_score = scores[best_role]
    
    # 计算置信度
    confidence = min(max_score / len(ROLES[best_role]["keywords"]), 1.0) \
                 if max_score > 0 else 0.0
    
    return best_role, confidence, matched_roles
```

#### 2. 共识决策算法

```python
def organize_consensus(task: str, agents: List[str]) -> Dict:
    """
    组织多角色共识
    
    流程:
    1. 确定主导角色
    2. 收集各角色意见
    3. 冲突检测
    4. 达成共识
    5. 生成决议
    """
    # 确定主导角色
    lead_role = determine_lead_role(task)
    
    # 收集意见
    opinions = {}
    for agent in agents:
        opinion = agent.analyze(task)
        opinions[agent.role] = opinion
    
    # 冲突检测
    conflicts = detect_conflicts(opinions)
    
    # 解决冲突
    if conflicts:
        resolved = resolve_conflicts(conflicts, opinions)
    
    # 生成决议
    consensus = generate_consensus(opinions)
    
    return consensus
```

## 🤝 贡献指南

### 开发环境设置

```bash
# 1. 克隆项目
git clone https://github.com/your-org/trae-multi-agent.git
cd trae-multi-agent

# 2. 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 运行测试
pytest tests/
```

### 提交流程

1. **Fork 项目**
2. **创建特性分支** (`git checkout -b feature/AmazingFeature`)
3. **提交更改** (`git commit -m 'Add some AmazingFeature'`)
4. **推送到分支** (`git push origin feature/AmazingFeature`)
5. **开启 Pull Request**

### 代码规范

- 遵循 PEP 8 规范
- 使用类型注解
- 编写单元测试
- 添加中文注释

### 测试要求

```bash
# 运行所有测试
pytest tests/ -v

# 测试覆盖率
pytest tests/ --cov=src --cov-report=html

# 覆盖率要求
# - 代码覆盖率 > 80%
# - 分支覆盖率 > 70%
```

## ❓ 常见问题

### Q1: 技能未生效？

**A**: 检查以下几点：
1. 技能文件是否在正确目录
2. 文件权限是否正确（可读）
3. 重启 Trae 应用
4. 检查 Trae 设置中是否启用了技能功能

### Q2: 角色识别不准确？

**A**: 可以尝试：
1. 使用更明确的任务描述
2. 使用 `--agent` 参数手动指定角色
3. 使用 `--consensus true` 组织多角色共识

### Q3: Python3 未找到？

**A**: 安装 Python3：
```bash
brew install python@3.11
```

### Q4: 如何更新技能？

**A**: 重新运行安装脚本：
```bash
~/.trae/skills/install-global.sh
```

### Q5: 如何自定义角色 Prompt？

**A**: 编辑 `SKILL.md` 文件中的角色 Prompt 部分，然后重启 Trae。

## 📄 许可证

MIT License

Copyright (c) 2026 Weiransoft

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## 📞 联系方式

- **项目主页**: https://github.com/weiransoft/TraeMultiAgentSkill.git
- **问题反馈**: https://github.com/weiransoft/TraeMultiAgentSkill.git/issues
- **文档**: https://weiransoft.github.io/TraeMultiAgentSkill/

## 🙏 致谢

感谢所有贡献者和用户的支持！

---

**Made with ❤️ by Weiransoft**
