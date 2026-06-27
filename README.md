# Trae Multi-Agent Skill

🎭 基于任务类型动态调度到合适的智能体角色（架构师、产品经理、测试专家、独立开发者、UI 设计师）。支持多智能体协作、共识机制、完整项目生命周期管理、规范驱动开发、代码地图生成、项目理解能力和 UI 设计能力。支持中英文双语。v2.6 新增 Ponytail 决策梯（少写多余代码）、Autonomous 自主迭代模式、Dynamic Workflows 6 大模式、插件热加载，v2.7 新增 UI/UX 巡检分析与视觉回归测试脚本。

## 🎉 2026 年 6 月最新更新 (v2.7)

> 设计原则：标准库优先（Playwright + PIL）、YAGNI、失败安全
> 适用角色：UI 设计师（交付前自检）/ 测试专家（E2E 像素级断言）/ Solo Coder（PR 门禁）

- ✅ **UI/UX 巡检分析 (v2.7)** - 4 大检测维度覆盖前端质量门禁
  - ♿ **可访问性 (A11y)**: WCAG AA 对比度（正常文本 4.5:1 / 大文本 3:1）、img alt、form label、语义化标签、键盘可达
  - 👆 **交互质量**: 按钮最小尺寸（Apple HIG ≥44px）、焦点可见性、加载反馈
  - 📐 **布局与响应式**: 元素重叠、文字截断（text-overflow）、视口溢出
  - ⚠️ **UX 反模式**: 强制注册、破坏性操作无确认、表单无校验
  - 🎯 **关键类**: `UIUXIssue`（dataclass，severity/category/rule/element/message/fix/metric）+ `UIUXAnalyzer`（核心，audit/dump）
  - 🚀 **Playwright 单次综合探针**: 一次 evaluate 取齐所有探针数据，避免多次往返
  - 🛡️ **失败安全**: 任一检查项异常被 try/except 隔离，不影响其他检查
  - 📄 详细章节：[SKILL.md](SKILL.md#uiux-巡检与视觉回归v27-新增--前端质量门禁工具)

- ✅ **视觉回归与显示完整性 (v2.7)** - 像素级 Diff + 显示错误检测
  - 🖼️ **视觉回归**: PIL `ImageChops` 像素级 Diff + 简化 SSIM 区域级 Diff
  - 📊 **数据显示不全检测**: 文本截断、元素溢出视口、图片未加载、骨架屏 >10s、长表格横向滚动
  - 🚨 **显示错误检测**: 红色文字/背景（HSV 检测）、错误关键词、Ant Design / Arco / Element UI 错误 Toast、浏览器原生 dialog
  - 🎯 **关键类**: `ChangedRegion` / `DiffResult` / `VisualRegressionChecker`
  - 📦 **软依赖**: Pillow（必需）、numpy（可选，更好的 SSIM）、playwright（DOM 检查）
  - ⚙️ **阈值可配**: 默认 `pixel_diff_ratio < 1%`
  - 🧘 **YAGNI**: 只实现最常用的 3 类检测，不造大而全框架
  - 📄 详细章节：[SKILL.md](SKILL.md#uiux-巡检与视觉回归v27-新增--前端质量门禁工具)

## 🎉 2026 年 6 月最新更新 (v2.6)

> 来源：Ponytail 项目决策梯、gnhf Ralph 自主迭代思想、Anthropic Dynamic Workflows（Claude Opus 4.8）、Phase 17 插件热加载方案
> 理论依据：YAGNI 原则、Karpathy Simplicity First、Ashby 必要多样性定律、控制论反馈闭环

- ✅ **Ponytail 决策梯 (v2.6)** - 在 Karpathy Simplicity First 原则之上，提供可执行的"写代码前先停一停"决策梯
  - 🪜 **6 步决策梯**：YAGNI → 标准库优先 → 平台原生 → 复用现有 → 一行优先 → 最小可行，停在第一个能解决问题的台阶上
  - 🚫 **16 条不可简化红线**：6 条原始 Ponytail 红线（输入校验、错误处理、安全、无障碍等）+ 10 条项目规则红线（真实业务逻辑、并发安全、API 契约等）
  - 🎚️ **三种强度模式**：`lite`（精简，测试/UI 角色）/ `full`（默认，开发者/架构师）/ `ultra`（YAGNI 极端主义，autonomous 自动降级为 full）
  - 📒 **债务台账**：`# ponytail:` 注释标记故意简化，`DebtCollector` 自动扫描区分"有升级路径"与"腐烂风险"债务
  - 🔍 **需求追踪**：`RequirementTracer` 解析 `[REQ-XXX]` 标记，中文关键词提取 + 实现检测
  - 💬 **使用方式**：`/ponytail ultra|full|lite|off` 命令切换，环境变量 / 配置文件覆盖
  - 🧪 **测试**：10 个测试文件，98 个测试用例全部通过
  - 核心组件：`scripts/ponytail/ruleset.py`、`scripts/ponytail/mode_tracker.py`、`scripts/ponytail/debt_collector.py`、`scripts/ponytail/requirement_tracer.py`
  - 📄 详细指南：[docs/guides/PONYTAIL_GUIDE.md](docs/guides/PONYTAIL_GUIDE.md)

- ✅ **Autonomous 自主迭代模式 (v2.6)** - 借鉴 gnhf Ralph 风格，让多角色团队在你睡觉时自动完成全部任务
  - 🔄 **4 阶段循环**：`plan → dev → verify → fix`，直到满足停止条件或触发硬上限
  - 🧩 **9 个核心组件**：`RalphAutonomousPlugin`、`RalphLoopController`、`RunState`、`NotesMemory`、`GitDriver`、`SleepGuard`、`SmartConfirmation`、`AutoSkillLoader`、`DispatcherAdapter`
  - 🚩 **17 个 CLI flag**：以 `--auto-` 前缀命名，覆盖运行时上限、阶段节奏、续跑状态、安全防休眠、Git 作者、Notes 记忆
  - 🤖 **智能确认三态决策**：`smart`（白名单 + 风险评分 + 黑名单）/ `whitelist-only` / `blacklist-only`
  - 💾 **断点续跑**：`--auto-resume` / `--auto-resume-latest`，SHA256 校验 + 备份恢复
  - ☕ **跨平台防休眠**：`caffeinate`（macOS）/ `systemd-inhibit`（Linux），CI 环境可关闭
  - 📄 详细指南：[docs/guides/AUTONOMOUS_MODE_GUIDE.md](docs/guides/AUTONOMOUS_MODE_GUIDE.md)

- ✅ **Dynamic Workflows 6 大模式 (v2.6)** - 融合 Anthropic Dynamic Workflows 思想，沉淀为可复用的声明式模式库
  - 🎯 **6 大模式**：分类并行动（classifier-dispatch）/ 扇出与聚合（fan-out-aggregate）/ 对抗性验证（adversarial-verify）/ 生成与筛选（generate-filter）/ 锦标赛（tournament）/ 循环直到完成（loop-until-done）
  - 📦 **12 个实现模块**：`guard`、`interruption_recovery`、`model_router`、`pattern_composer`、`pattern_executor`、`pattern_tier_resolver`、`semantic_embedder`、`skill_injector`、`subagent_sandbox`、`token_budget_guard`、`workflow_step_adapter`、`worktree_manager`
  - 🧠 **三大痛点应对**：Agentic Laziness（智能体懒惰）/ Self-preferential Bias（自我偏好偏差）/ Goal Drift（目标漂移）
  - 🔀 **模型路由层**：分类器决定 Sonnet/Opus 路由，成本 vs 质量动态权衡
  - 🌲 **worktree 隔离**：子智能体在独立 worktree 中执行，避免相互干扰、并行安全
  - 📄 详细文档：[docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md](docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md)

- ✅ **插件热加载 (v2.6)** - V3 插件架构之上的动态能力，零业务行为变化
  - 🛣️ **3 种加载路径**：BUILTIN_PLUGINS 静态注册 / 显式 API（`hot_register` / `hot_unregister`）/ drop-in 目录扫描（`plugins_extra/*.py`）
  - 🔄 **运行时轮询**：周期检查 drop-in 目录文件 mtime，变更时自动 reload
  - 🛡️ **生产安全**：reload 失败回滚到旧实例、路径穿越三层防护、`--no-hot-reload` 完全关闭动态能力
  - 🔌 **V3 插件实现**：Phase 16 重构 V3 插件架构（1464→42 行），Phase 17 叠加热加载
  - 📄 详细方案：[docs/dev/PHASE17_PLAN.md](docs/dev/PHASE17_PLAN.md)

## 🎉 2026 年 5 月最新更新 (v2.5)

> 来源：https://github.com/Jiaqi-Guo-0114/cybernetics-agent  
> 理论依据：ICLR 2026 Profile-Aware Maneuvering 架构、钱学森工程控制论（系统工程、系统学）、Norbert Wiener 控制论、Ashby 必要多样性定律

- ✅ ** 工程控制论增强 (v2.5)** - 基于 cybernetics-agent 项目引入反馈闭环、自适应和可观测性增强
  - 🔄 **三环控制模型**：战略层（任务规划、AI动态规划）、战术层（Guard验证、异常检测）、执行层（任务执行、反馈收集）
  - 💫 **反馈控制环**：感知-决策-执行-反馈完整闭环，基于案例的策略选择（非PID，适配认知任务）
  - 📊 **性能画像**：执行案例记录、失败/成功模式提取、相似案例检索（非预测）、冷启动优雅降级
  - 🛡️ **守护协调器**：执行前预验证、实时异常检测、执行后审查、AI增强风险评估
  - 🏗️ **6 个核心组件**：`feedback_control_loop.py`（反馈控制环）、`performance_fingerprint.py`（性能画像）、`guard_coordinator.py`（守护协调器）、`hierarchical_control.py`（层次化控制器）、`cybernetics_integration.py`（统一集成接口）、`context_fingerprint_integration.py`（上下文集成）
  - 🎯 **预期收益**：执行成功率 +8%，方差 -67%，人工介入 -70%
  - 📄 详细分析：[docs/dev/CYBERNETICS_ANALYSIS.md](docs/dev/CYBERNETICS_ANALYSIS.md)

## 🎉 2026 年 4 月最新更新 (v2.4)

- ✅ **Karpathy 四大核心原则** - 融入 Andrej Karpathy 的编程智慧：Think Before Coding、Simplicity First、Surgical Changes、Goal-Driven Execution
- ✅ **Karpathy 原则执行检查器** - 原则合规性检查、5 级严重度违规检测、验证检查点管理、执行报告生成
- ✅ **Claude Code SubAgent 适配器** - 跨平台 Agent 适配，统一 Claude Code / Trae IDE 的 subagent 调用接口
- ✅ **行为准则体系** - 所有角色统一的 LLM 编程行为准则，减少错误、过度复杂、无关修改
- ✅ **验证检查点机制** - 目标驱动的验证流程，确保每个阶段都有明确的成功标准
- ✅ **角色专属应用指南** - 每个角色都有 Karpathy 原则的具体应用场景和行为准则

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
  - [Ponytail 决策梯 (v2.6)](#ponytail-决策梯-v26-新增)
  - [Autonomous 自主迭代模式 (v2.6)](#autonomous-自主迭代模式-v26-新增)
  - [Dynamic Workflows 6 大模式 (v2.6)](#dynamic-workflows-6-大模式-v26-新增)
  - [插件热加载 (v2.6)](#插件热加载-v26-新增)
  - [Karpathy 四大核心原则 (v2.4)](#karpathy-四大核心原则-v24-新增)
  - [UI/UX 巡检与视觉回归 (v2.7)](#uiux-巡检与视觉回归-v27-新增)
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

### Ponytail 决策梯 (v2.6 新增)

在 Karpathy Simplicity First 原则之上，提供可执行的"写代码前先停一停"决策梯，强制开发者每写一行代码前先问 6 个问题：

1. **6 步决策梯** 🪜
   - 台阶 1 - YAGNI：这东西真的需要存在吗？推测性需求直接跳过
   - 台阶 2 - 标准库优先：语言标准库能搞定？直接用标准库
   - 台阶 3 - 平台原生：运行时平台自带功能能覆盖？用平台原生特性
   - 台阶 4 - 复用现有：已安装的依赖能解决？复用现有依赖，不新增
   - 台阶 5 - 一行优先：能写成一行？写成一行，不牺牲可读性
   - 台阶 6 - 最小可行：以上都不行，写最少能做工作的代码
   - 核心文件：`scripts/ponytail/ruleset.py`

2. **16 条不可简化红线** 🚫
   - 原始 Ponytail 红线（6 条）：信任边界输入校验、防数据丢失错误处理、安全措施、无障碍基础、用户明确要求保留功能、真实硬件校准旋钮
   - 项目规则红线（10 条）：真实业务逻辑禁 mock、需求文档功能禁跳过、非平凡逻辑留可运行检查、并发安全不可简化、真实错误处理禁吞异常、关键路径日志禁删除、密钥配置校验禁简化、数据库事务边界禁简化、API 契约禁单方面简化、隐私数据处理禁简化
   - 核心文件：`scripts/ponytail/ruleset.py`

3. **三种强度模式** 🎚️
   - `lite`：精简版，注入 6 步决策梯（无红线详情），适用 test_expert / ui_designer
   - `full`（默认）：完整版，注入 6 步 + 16 条红线 + 输出规范，适用 solo_coder / architect
   - `ultra`：YAGNI 极端主义，full + 额外约束，autonomous 模式自动降级为 full
   - 核心文件：`scripts/ponytail/mode_tracker.py`

4. **债务台账 + 需求追踪** 📒
   - `DebtCollector`：verify 阶段自动扫描 `# ponytail:` 注释，区分"有升级路径"与"腐烂风险"债务，超过 3 条 no_trigger 债务则告警
   - `RequirementTracer`：解析需求文档 `[REQ-XXX]` 标记，中文关键词提取 + 代码实现检测（≥50% 关键词匹配视为已实现）
   - 核心文件：`scripts/ponytail/debt_collector.py`、`scripts/ponytail/requirement_tracer.py`

**使用方式**:
```bash
# 在对话中切换模式
/ponytail ultra    # 切换到 ULTRA 模式（YAGNI 极端主义）
/ponytail full     # 切换到 FULL 模式（默认）
/ponytail lite     # 切换到 LITE 模式（精简）
/ponytail off      # 关闭决策梯注入
/ponytail          # 查看当前模式

# 环境变量（优先级最高）
export PONYTAIL_MODE=ultra

# 配置文件
echo "full" > .ponytail_mode
```

**测试**: 10 个测试文件，98 个测试用例全部通过 ✅
```bash
bash scripts/tests/scripts/run_ponytail_tests.sh
```

📄 详细指南：[docs/guides/PONYTAIL_GUIDE.md](docs/guides/PONYTAIL_GUIDE.md)

### Autonomous 自主迭代模式 (v2.6 新增)

借鉴 gnhf Ralph 风格的"无人值守迭代"工作流，给定目标后多角色团队按 4 阶段循环执行，直到满足停止条件或触发硬上限：

1. **4 阶段循环** 🔄
   - `plan`：规划阶段，分解任务、制定方案
   - `dev`：开发阶段，编写代码、实现功能
   - `verify`：验证阶段，运行测试、检查质量
   - `fix`：修复阶段，处理失败、迭代改进
   - 核心文件：`scripts/autonomous/loop_controller.py`

2. **9 个核心组件** 🧩
   - `RalphAutonomousPlugin`：插件入口（priority=5，CLI flag `--autonomous`）
   - `RalphLoopController`：主循环控制（Plan / Dev / Verify / Fix）
   - `RunState`：状态持久化（SHA256 校验、备份恢复）
   - `NotesMemory`：跨轮 `notes.md` 累积记忆
   - `GitDriver`：原子 commit / 滚动回滚
   - `SleepGuard`：跨平台防休眠（caffeinate / systemd-inhibit）
   - `SmartConfirmation`：三态确认（白名单 + 风险评分 + 黑名单）
   - `AutoSkillLoader`：自动按任务特征加载相关 skill
   - `DispatcherAdapter`：与 V3 dispatcher 解耦适配

3. **17 个 CLI flag** 🚩
   - 主开关：`--autonomous`
   - 运行时上限：`--auto-max-iterations`、`--auto-max-tokens`、`--auto-stop-when`、`--auto-failure-abort`
   - 阶段节奏：`--auto-stage-order`、`--auto-test-command`、`--auto-backoff-base`、`--auto-backoff-max`
   - 续跑状态：`--auto-resume`、`--auto-resume-latest`、`--auto-run-dir`
   - 安全防休眠：`--auto-no-caffeinate`、`--auto-no-commit`、`--auto-confirm-mode`、`--auto-security-analyzer`
   - Git 作者：`--auto-git-author-name`、`--auto-git-author-email`
   - Notes 记忆：`--auto-notes-path`、`--auto-max-size-kb`、`--auto-trim-keep-last-n`

4. **智能确认三态决策** 🤖
   - `smart`（默认）：白名单自动通过 + 风险评分 + 黑名单自动拒绝
   - `whitelist-only`：仅白名单操作自动通过，其余询问
   - `blacklist-only`：仅黑名单操作拒绝，其余自动通过
   - 核心文件：`scripts/autonomous/smart_confirmation.py`

**使用示例**:
```bash
# 最小化启动
python -m cli.main \
    --autonomous \
    --task "实现一个线程安全的 LRU 缓存" \
    --project-root .

# 完整推荐启动命令
python -m cli.main \
    --autonomous \
    --task "为 parser 模块补齐边界测试" \
    --project-root . \
    --auto-max-iterations 30 \
    --auto-stop-when "all tests pass" \
    --auto-test-command "python3 -m unittest discover -s tests -p 'test_*.py'" \
    --auto-confirm-mode smart \
    --auto-git-author-name "Ralph Bot" \
    --auto-git-author-email "ralph@example.com"
```

📄 详细指南：[docs/guides/AUTONOMOUS_MODE_GUIDE.md](docs/guides/AUTONOMOUS_MODE_GUIDE.md)

### Dynamic Workflows 6 大模式 (v2.6 新增)

融合 Anthropic Dynamic Workflows（Claude Opus 4.8）思想，将 6 大经典模式沉淀为可复用的声明式能力单元，解决长程/并行/对抗任务的三大痛点：

1. **6 大模式** 🎯
   - **分类并行动（classifier-dispatch）**：分类器路由任务到不同子流程
   - **扇出与聚合（fan-out-aggregate）**：任务拆 N 份并行处理 → 屏障等待 → 合并
   - **对抗性验证（adversarial-verify）**：生成 + 验证两两配对，验证者独立 context
   - **生成与筛选（generate-filter）**：大规模生成 → 标准筛选 → 重复去除
   - **锦标赛（tournament）**：N 个 Agent 竞争 → 两两 PK → 决出冠军
   - **循环直到完成（loop-until-done）**：动态生成 Agent 直至停止条件

2. **12 个实现模块** 📦
   - `guard.py`：模式库 schema 校验 + 提示词注入防护
   - `interruption_recovery.py`：中断恢复管理（pause/resume/cancel）
   - `model_router.py`：模型路由层（Sonnet/Opus 动态权衡）
   - `pattern_composer.py`：模式组合器（多模式串联/并联）
   - `pattern_executor.py`：模式执行器（调度 subagent）
   - `pattern_tier_resolver.py`：模式层级解析（tier-aware dispatch）
   - `semantic_embedder.py`：语义嵌入层（多语言去重）
   - `skill_injector.py`：Skill 注入器（6 个核心组件）
   - `subagent_sandbox.py`：子智能体沙箱（worktree 隔离）
   - `token_budget_guard.py`：Token 预算守卫
   - `workflow_step_adapter.py`：工作流步骤适配器
   - `worktree_manager.py`：worktree 管理器

3. **三大痛点应对** 🧠
   - **Agentic Laziness（智能体懒惰）**：对抗性验证 + 锦标赛模式强制全面执行
   - **Self-preferential Bias（自我偏好偏差）**：独立 context 验证者 + 评估准则
   - **Goal Drift（目标漂移）**：循环停止条件 + Token 预算 + 中断恢复

📄 详细文档：[docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md](docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md)

### 插件热加载 (v2.6 新增)

V3 插件架构之上的动态能力，保留 Phase 16 静态注册路径基础上叠加动态加载，零业务行为变化：

1. **3 种加载路径** 🛣️
   - **BUILTIN_PLUGINS 静态注册**：`scripts/plugins/__init__.py` 中静态构造 5 个内置插件
   - **显式 API**：`dispatcher.hot_register(plugin)` / `dispatcher.hot_unregister(name)`
   - **drop-in 目录扫描**：扫描 `plugins_extra/*.py`，自动 import + 注册 + 运行时轮询 reload

2. **V3 插件实现** 🔌
   - Phase 16 重构 V3 插件架构（1464→42 行），统一插件契约
   - Phase 17 叠加热加载，181 个测试覆盖
   - 5 个内置插件：`GoalCancelPlugin`、`GoalGraphPlugin`、`GoalResumePlugin`、`MultiGoalPlugin`、`LoopGoalPlugin`

3. **生产安全** 🛡️
   - reload 失败回滚到旧实例（不破坏现有调度）
   - 路径穿越三层防护（parser + watcher + facade 串联校验）
   - `--no-hot-reload` 完全关闭动态能力（生产模式）
   - reload 期间持锁的 plugin 等锁释放再 unregister
   - 核心文件：`scripts/dispatcher/hot_reload_watcher.py`、`scripts/dispatcher/drop_in_loader.py`、`scripts/dispatcher/reload_guard.py`

📄 详细方案：[docs/dev/PHASE17_PLAN.md](docs/dev/PHASE17_PLAN.md)

### UI/UX 巡检与视觉回归 (v2.7 新增)

作为「UI 设计师」与「测试专家」角色的标准前端质量门禁，提供可独立 import / CLI 调用的 E2E 视觉质量保障脚本。

1. **UI/UX 巡检分析** ♿
   - 4 大检测维度：可访问性（WCAG AA 对比度 / img alt / form label / 语义化标签 / 键盘可达）、交互质量（按钮最小尺寸 ≥44px / 焦点可见性 / 加载反馈）、布局与响应式（元素重叠 / 文字截断 / 视口溢出）、UX 反模式（强制注册 / 破坏性操作无确认 / 表单无校验）
   - 关键类：`UIUXIssue`（dataclass）/ `UIUXAnalyzer`（核心，提供 `audit(page)` / `dump(path)`）
   - Playwright 单次综合探针：一次 `page.evaluate` 取齐所有探针数据，避免多次往返
   - 失败安全：任一检查项异常被 try/except 隔离，不影响其他检查
   - 核心文件：`scripts/uiux_analyzer.py`

2. **视觉回归与显示完整性** 🖼️
   - 3 大检测维度：像素级 Diff（PIL `ImageChops`）+ 简化 SSIM 区域级 Diff、数据显示不全（文本截断 / 元素溢出 / 图片未加载 / 骨架屏 >10s / 长表格横向滚动）、显示错误（红色文字/背景 / 错误关键词 / 组件库错误 Toast / 浏览器原生 dialog）
   - 关键类：`ChangedRegion` / `DiffResult` / `VisualRegressionChecker`
   - 软依赖：Pillow（必需）、numpy（可选，更好的 SSIM）、playwright（DOM 检查）
   - 阈值可配：默认 `pixel_diff_ratio < 1%`
   - 核心文件：`scripts/visual_regression.py`

3. **角色集成** 🎭
   - **UI 设计师**：交付稿前自检 `uiux_analyzer.audit(page)`，输出 `reports/uiux.json`
   - **测试专家**：E2E 套件中调用 `VisualRegressionChecker.compare(...)` 替代人工截图对比
   - **Solo Coder**：PR 门禁中调用 CLI，输出 JUnit XML 报告

📄 详细章节：[SKILL.md](SKILL.md#uiux-巡检与视觉回归v27-新增--前端质量门禁工具)

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
| `--agent` | string | ❌ | auto | 指定角色：architect, product-manager, tester, solo-coder, ui-designer, auto |
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

### 📚 版本历程

| 版本 | 日期 | 核心特性 |
|------|------|---------|
| v2.7 | 2026 年 6 月 | UI/UX 巡检分析（`uiux_analyzer.py`，4 大检测维度）、视觉回归与显示完整性（`visual_regression.py`，3 大检测维度） |
| v2.6 | 2026 年 6 月 | Ponytail 决策梯（少写多余代码）、Autonomous 自主迭代模式、Dynamic Workflows 6 大模式、插件热加载 |
| v2.5 | 2026 年 5 月 | Cybernetics 工程控制论增强（三环控制模型、反馈控制环、性能画像、守护协调器） |
| v2.4 | 2026 年 4 月 | Karpathy 四大核心原则、行为准则体系、验证检查点机制、Claude Code SubAgent 适配器 |
| v2.3 | 2026 年 3 月 | 多角色代码走读、Workspace 支持、3D 代码地图可视化、任务可视化页面 |
| v2.2 | 2026 年 2 月 | 长程 Agent 支持（Checkpoint、Handoff、TaskList、WorkflowEngineV2） |
| v2.1 | 2026 年 1 月 | AI 语义理解驱动角色匹配、AI 助手深度集成、智能缓存和降级策略 |

### 🔗 v2.7 详细文档索引

- [SKILL.md - UI/UX 巡检与视觉回归](SKILL.md#uiux-巡检与视觉回归v27-新增--前端质量门禁工具) - 4+3 检测维度、关键类、用法示例、CLI 集成
- [CHANGELOG.md - v2.7.0](CHANGELOG.md) - 完整变更日志

### 🔗 v2.6 详细文档索引

- [Ponytail 决策梯指南](docs/guides/PONYTAIL_GUIDE.md) - 6 步决策梯、16 条红线、三种模式、债务台账
- [Autonomous 模式指南](docs/guides/AUTONOMOUS_MODE_GUIDE.md) - 4 阶段循环、9 个核心组件、17 个 CLI flag
- [Dynamic Workflows 融合方案](docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md) - 6 大模式、12 个实现模块
- [Cybernetics 增强分析](docs/dev/CYBERNETICS_ANALYSIS.md) - 6 个核心组件、三环控制模型
- [Phase 17 插件热加载方案](docs/dev/PHASE17_PLAN.md) - 3 种加载路径、V3 插件实现

---

**Made with ❤️ by Weiransoft**
