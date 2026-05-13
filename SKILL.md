---
name: multi-agent-team
slug: multi-agent-team
description: 基于任务类型动态调度到合适的智能体角色（架构师、产品经理、测试专家、独立开发者、UI 设计师）。支持多智能体协作、共识机制、完整项目生命周期管理、规范驱动开发、代码走读审查和项目理解能力。支持中英文双语。v2.4 新增 Karpathy 四大核心原则，v2.5 新增 Cybernetics 工程控制论增强。
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

**v2.4 新增（Karpathy 四大核心原则）**:
- 🚀 代码走读与审查：多角色协作分析，生成统一代码地图和审查报告
- 🗺️ 代码地图 Workspace 支持：支持一个 workspace 包含多个项目
- 📊 文档对齐引擎：多角色分析结果对齐，生成共识代码地图
- 📋 任务可视化页面：实时展现各角色任务状态、进度、依赖关系、交接过程
- 🎨 3D 代码地图可视化：基于 Three.js 的交互式代码结构可视化，支持流动动画和主题切换
- ✅ 文档与代码一致性检查：审查报告中新增文档与代码差异检查清单

## Karpathy 四大核心原则（行为准则）

> **来源**: Andrej Karpathy 对 LLM 编程常见陷阱的观察
> **目的**: 减少 LLM 编程中的错误、过度复杂、无关修改等问题

### ⚠️ LLM 常见问题（The Problems）

| 问题类型 | 具体表现 |
|---------|---------|
| **错误假设** | 模型代替用户做出错误假设，不检查就执行 |
| **管理混乱** | 不管理自己的困惑，不寻求澄清 |
| **不一致** | 不表面不一致，不提出权衡方案 |
| **不反馈** | 应该推回时不推回 |
| **过度复杂** | 喜欢把代码和 API 过度复杂化 |
| **无关修改** | 改一处代码时溢出到不相关的区域 |

### 1️⃣ Think Before Coding（三思而后行）

**原则**: "Don't assume. Don't hide confusion. Surface tradeoffs."

**行为准则**:

| 要求 | 说明 |
|-----|------|
| 明确陈述假设 | 如果不确定，主动提问 |
| 呈现多种解释 | 不要默默选择一个方案 |
| 提出更简单的方法 | 适当时候推回 |
| 遇到不清楚就停止 | 说出哪里不清楚并提问 |

**触发场景**:
- 用户需求不明确时 → 先问清楚再实现
- 存在多种实现方案时 → 呈现权衡让用户选择
- 发现假设可能错误时 → 立即表面并验证

**示例**:
```
用户: "添加一个导出用户数据的功能"

❌ 错误做法（隐藏假设）:
def export_users(format='json'):
    # 假设用户只需要导出所有用户
    # 假设 JSON 是唯一需要的格式
    ...

✅ 正确做法（明确假设）:
"""
需要确认：
1. 是导出所有用户还是特定条件的用户？
2. 需要哪些字段？（全部还是部分）
3. 格式除了 JSON 还需要其他吗？（CSV、Excel等）
"""
```

### 2️⃣ Simplicity First（简单优先）

**原则**: "Minimum code that solves the problem. Nothing speculative."

**行为准则**:

| 要求 | 禁止 |
|-----|------|
| 无单次使用的抽象 | ❌ 不要过度设计类继承 |
| 无"灵活性"或"可配置性" | ❌ 不要添加 speculative features |
| 无未来可能用到的代码 | ❌ 不要写"以后可能会用"的代码 |
| 无不相关代码的"改进" | ❌ 不要顺手改别的地方 |

**触发场景**:
- 实现前先问："这个抽象真的需要吗？"
- 看到"为以后预留"的代码 → 删除
- 看到过度设计的类结构 → 简化为简单函数

### 3️⃣ Surgical Changes（精准修改）

**原则**: "Touch only what's needed."

**行为准则**:

| 要求 | 说明 |
|-----|------|
| 只改直接相关的行 | 不要"改善"周围的代码 |
| 保持风格一致 | 遵循项目的风格指南 |
| 模仿现有模式 | 与项目现有代码保持一致 |
| 不要溢出修改 | 改 A 功能时不碰 B 功能 |

**触发场景**:
- 修复 bug 时 → 只改必要的行，不改其他函数
- 代码格式化 → 单独的提交，不混在功能修改中
- 改注释 → 除非是修正错误，否则不碰

**红线**:
```
❌ 改了一个函数，顺便改了其他3个函数
❌ 修复bug时，同时做了代码重构
❌ 改了配置，顺便改了不相关的常量
```

### 4️⃣ Goal-Driven Execution（目标驱动执行）

**原则**: "Define success criteria and loop until verified."

**行为准则**:

| 要求 | 说明 |
|-----|------|
| 定义成功标准 | 明确什么是"完成" |
| 设定验证检查点 | 每个阶段都有验证点 |
| 迭代直到验证通过 | 不满足就继续调整 |
| 使用可验证的目标 | "添加验证" → "为无效输入写测试，让它们通过" |

**验证检查点机制**:
```
阶段 1: 需求理解 → 用户确认 ✓
    ↓
阶段 2: 方案设计 → 评审通过 ✓
    ↓
阶段 3: 代码实现 → 自测通过 ✓
    ↓
阶段 4: 集成测试 → 测试通过 ✓
```

**成功指标**:
- ✅ 更少的不必要 diff 变更
- ✅ 更少的因过度复杂而重写
- ✅ 澄清问题在实现之前而非之后
- ✅ 更精准的代码修改

### Karpathy 原则应用速查

| 场景 | 应用原则 | 具体行动 |
|------|---------|---------|
| 需求不明确 | Think Before Coding | 停下来问清楚 |
| 多种方案可选 | Think Before Coding | 呈现权衡让用户选 |
| 考虑添加抽象 | Simplicity First | 问"真的需要吗？" |
| 看到复杂代码 | Simplicity First | 简化到最小可用 |
| 修改代码 | Surgical Changes | 只改必要的行 |
| 准备提交代码 | Surgical Changes | 检查是否有多余修改 |
| 开始实现 | Goal-Driven | 定义成功标准 |
| 完成后 | Goal-Driven | 验证是否达标 |

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

#### 1. 语义理解
- **深层语义分析**: 理解任务的真实意图，而非表面关键词
- **上下文感知**: 基于历史经验和领域知识理解任务
- **多义词消歧**: 准确理解多义词在特定上下文中的含义

**示例**:
```
任务："设计一个高可用的系统"
AI 理解：
- "高可用" → 需要冗余设计、故障转移、负载均衡
- "系统" → 可能是分布式系统、微服务架构
推荐角色：架构师 (置信度：92%)
```

#### 2. 智能匹配
- **多维度评分**: 能力匹配 (50%) + 技能匹配 (30%) + 语义相关 (20%)
- **可解释结果**: 提供详细的匹配原因和推理过程
- **置信度评估**: 0-1 的置信度评分，辅助决策

**匹配策略**:
- `ai_enhanced`: AI 增强混合匹配（推荐，默认）
- `semantic`: 纯 AI 语义匹配
- `keyword`: 传统关键词匹配
- `hybrid`: 传统混合匹配

#### 3. 代码审查
- **质量评估**: 代码结构、可读性、可维护性
- **性能分析**: 性能瓶颈、优化建议
- **安全检查**: 常见安全漏洞检测
- **最佳实践**: 行业标准和最佳实践建议

#### 4. 知识问答
- **技术咨询**: 解答技术问题
- **架构建议**: 提供架构设计建议
- **工具推荐**: 推荐合适的工具和库

### AI 配置

在 `skill-manifest.yaml` 中配置 AI 参数：

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

**Karpathy 原则应用**:

| 阶段 | 应用的 Karpathy 原则 | 具体行为 |
|------|---------------------|---------|
| 理解需求 | Think Before Coding | 明确系统边界、技术约束、假设条件 |
| 设计方案 | Simplicity First | 最小必要的抽象、避免过度设计 |
| 评审方案 | Goal-Driven | 定义成功标准、验证设计是否满足需求 |
| 代码审查 | Surgical Changes | 只改必要的架构问题、不碰业务逻辑 |

**核心原则**:
- ✅ 系统性思维 - 设计前回答 4 个关键问题
- ✅ 5-Why 分析法 - 连续追问找到根因
- ✅ 零容忍清单 - 禁止 mock、硬编码、简化
- ✅ 验证驱动设计 - 完整验收标准

**触发关键词**: 架构、设计、选型、审查、性能、瓶颈、模块、接口、部署

**典型任务**:
- 项目启动阶段的架构设计
- 关键代码的架构审查和代码评审
- 技术难题攻关和性能优化

### 2. 产品经理 (Product Manager)
**职责**: 定义用户价值清晰、需求明确、可落地、可验收的产品

**Karpathy 原则应用**:

| 阶段 | 应用的 Karpathy 原则 | 具体行为 |
|------|---------------------|---------|
| 需求挖掘 | Think Before Coding | 问清楚真实需求、明确假设条件 |
| 方案评估 | Think Before Coding | 呈现多个方案及其权衡 |
| 需求定义 | Simplicity First | 最小可行产品MVP、避免过度功能 |
| 验收标准 | Goal-Driven | 定义清晰可验证的完成标准 |

**核心原则**:
- ✅ 需求三层挖掘 - 表面→真实→本质
- ✅ SMART 验收标准 - 具体、可衡量、可实现
- ✅ 竞品分析规则 - 至少 5 个竞品对比

### 3. 测试专家 (Test Expert)
**职责**: 确保全面、深入、自动化、可量化的质量保障

**Karpathy 原则应用**:

| 阶段 | 应用的 Karpathy 原则 | 具体行为 |
|------|---------------------|---------|
| 理解需求 | Think Before Coding | 明确测试范围、假设条件、验证标准 |
| 设计测试 | Simplicity First | 最小必要的测试用例、避免冗余测试 |
| 编写测试 | Surgical Changes | 只改需要的测试、不碰其他测试用例 |
| 验证结果 | Goal-Driven | 定义明确的通过/失败标准 |

**核心原则**:
- ✅ 测试金字塔 - 70% 单元 +20% 集成 +10%E2E
- ✅ 正交分析法 - 5 类场景全覆盖
- ✅ 真机测试规则 - 真实环境验证

**触发关键词**: 测试、质量、验收、自动化、性能测试、缺陷、评审、门禁

**典型任务**:
- 测试策略制定和测试用例设计
- 自动化测试方案
- 质量评估和测试报告

### 4. 独立开发者 (Solo Coder)
**职责**: 编写完整、高质量、可维护、可测试的代码

**Karpathy 原则应用**:

| 阶段 | 应用的 Karpathy 原则 | 具体行为 |
|------|---------------------|---------|
| 理解需求 | Think Before Coding | 明确功能边界、输入输出、异常处理 |
| 方案设计 | Simplicity First | 最小必要实现、无 speculative code |
| 编码实现 | Surgical Changes | 只改必要的代码、不碰无关代码 |
| 自测验证 | Goal-Driven | 定义成功标准、确保测试通过 |

**Karpathy 红线（绝对禁止）**:
```
❌ 隐藏假设 - 不验证就假设输入格式一定是这样
❌ 过度复杂 - 为一个简单函数创建复杂的类继承
❌ 溢出修改 - 修复一个bug时改了其他3个函数
❌ 跳过验证 - 写完代码不测试就交付
❌ speculative - 添加"以后可能用到"的代码
```

**核心原则**:
- ✅ 零容忍清单 - 10 项绝对禁止
- ✅ 完整性检查 - 4 维度检查清单
- ✅ 自测规则 - 3 层测试验证

**触发关键词**: 实现、开发、代码、修复、优化、重构、单元测试、文档

**典型任务**:
- 功能实现和单元测试编写
- 代码重构和优化
- 开发文档编写

### 5. UI 设计师 (UI Designer)
**职责**: 创建独特、生产级的 UI 界面，具有高设计质量，避免通用的 AI "slop" 美学

**Karpathy 原则应用**:

| 阶段 | 应用的 Karpathy 原则 | 具体行为 |
|------|---------------------|---------|
| 理解需求 | Think Before Coding | 明确用户群体、使用场景、设计约束 |
| 方案设计 | Simplicity First | 最小必要的组件、避免过度设计 |
| 设计实现 | Surgical Changes | 只改需要的设计元素、不碰无关部分 |
| 验收评审 | Goal-Driven | 定义设计成功标准、验证是否满足需求 |

**核心原则**:
- ✅ 设计思维规则 - 设计前回答 4 个关键问题
- ✅ UI 设计美学指南 - 字体、色彩、动画、布局
- ✅ 零容忍清单 - 禁止通用字体、陈旧配色、AI slop
- ✅ 验证驱动设计 - 完整验收标准
- ✅ 完整性检查 - 多维度检查清单

**触发关键词**: UI设计、界面设计、前端设计、视觉设计、UI/UX、UI原型、界面美化、UI优化、UI重构

**典型任务**:
- Web 组件、页面、应用的 UI 设计
- UI 原型和视觉稿创建
- UI 美化和视觉优化
- 设计系统和设计规范制定
- UI 组件库设计

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

```bash
# 全局 skill 安装后，在 workspace 中使用
~/.trae/skills/docs/code-map-visualizer.html
```

**功能特性**:
- **Three.js 3D 引擎**：完整 3D 场景渲染，支持拖拽旋转、滚轮缩放
- **前后端分层展示**：前端层（蓝色）、后端层（红色）、共享层（灰色）
- **真实调用链路**：节点间的连线连接到实际代码节点
- **动态流动效果**：边使用虚线动画 + 流动粒子
- **深色/浅色主题**：一键切换

**JSON v2.0 数据结构**:

生成命令：
```bash
python3 scripts/code_map_generator_v2.py /path/to/project --visual
```

输出文件：`{project-name}-VISUAL-MAP.json`

```json
{
  "version": "2.0",
  "project": {
    "name": "项目名",
    "frontend": { "layers": ["frontend-ui", "frontend-service", "frontend-store"] },
    "backend": { "layers": ["api", "service", "domain", "data", "middleware"] }
  },
  "layers": [
    { "id": "frontend-ui", "name": "前端UI层", "side": "frontend" },
    { "id": "api", "name": "API层", "side": "backend" },
    { "id": "service", "name": "业务逻辑层", "side": "backend" }
  ],
  "nodes": [
    {
      "id": "file:path/to/file.py",
      "type": "file",
      "name": "文件名",
      "layerId": "service",
      "side": "backend",
      "calls": ["file:other.py"],
      "calledBy": []
    }
  ],
  "edges": [
    {
      "id": "e1",
      "source": "file:a.py",
      "target": "file:b.py",
      "type": "calls",
      "protocol": "local"
    }
  ]
}
```

**节点类型**:
- `module`: 模块节点
- `file`: 文件节点
- `class`: 类节点
- `function`: 函数/方法节点

**边类型**:
- `calls`: 方法调用
- `imports`: 导入关系
- `http`: HTTP API 调用（前后端通信）
- `layer-calls`: 层级间典型调用

**交互功能**:
- 点击展开/折叠模块、类、函数
- 双击函数高亮调用链路
- 调用链路面板展示关键流程
- 点击节点显示详情（层级、端、调用关系）

### 任务可视化页面 (v2.3)

```bash
# 全局 skill 安装后，在 workspace 中使用
~/.trae/skills/docs/task-visualizer.html
```

**功能特性**:
- **概览统计面板**：总任务数、待开始、进行中、已完成、被阻塞
- **角色任务卡片**：任务列表、状态、进度
- **任务依赖关系**：显示任务间的依赖和阻塞关系
- **任务交接记录时间线**：记录角色间的任务交接过程
- **Canvas 绘制协同关系图**：展示角色间的协作网络
- **定时刷新机制**：自动从 JSON 文件加载最新任务数据（默认30秒）

**交互功能**:
- 点击任务卡片查看详情
- 查看任务依赖和交接记录
- 实时更新任务状态

**Workspace 安装说明**:
安装 skill 后，可视化文件会自动符号链接到 `~/.trae/skills/docs/` 目录，在任意 workspace 中都可直接打开使用。

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

## 文档结构

```
docs/
├── project-understanding/  # 项目理解文档
├── spec/                   # 规范驱动开发文档
├── architect/              # 架构师文档
├── product-manager/        # 产品经理文档
├── tester/                 # 测试专家文档
├── solo-coder/              # 独立开发者文档
├── ui-designer/            # UI 设计师文档
└── devops/                 # DevOps 工程师文档
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
- ✅ 智能角色识别
- ✅ 多角色协同
- ✅ 上下文感知
- ✅ 完整项目流程
- ✅ 紧急任务处理
- ✅ UI 设计（避免 AI slop）

通过智能调度，减少用户干预，提升协作效率！
