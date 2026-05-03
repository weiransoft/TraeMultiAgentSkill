# 变更日志

本文档记录 Trae Multi-Agent Skill 的所有重要变更。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [2.4.1] - 2026-05-03

### Changed

#### 文档全面更新
- ✅ 更新 `IMPLEMENTATION_STATUS.md`：版本号升级至 2.4.1，新增 v2.4 组件列表和完整文件清单
- ✅ 更新 `skill-manifest.yaml`：版本号升级至 2.4.1，新增 v2.4 变更记录，新增 ai_capabilities 扩展
- ✅ 更新 `CHANGELOG.md`：添加 v2.4.0 和 v2.4.1 变更记录
- ✅ 更新 `README_EN.md`：添加 v2.4 Karpathy 原则更新标题

#### 文档对齐
- ✅ 所有文档版本号统一为 2.4.1
- ✅ 技能清单描述更新，反映最新功能
- ✅ 实现状态文档完全重写，按版本分组展示组件

## [2.4.0] - 2026-04-14

### Added

#### Karpathy 四大核心原则强制执行 (v2.4)

##### 原则执行检查器
- ✅ `KarpathyPrincipleEnforcer` 类 (`scripts/karpathy_principle_enforcer.py`)
- ✅ `PrincipleType` 枚举：THINK_BEFORE_CODING / SIMPLICITY_FIRST / SURGICAL_CHANGES / GOAL_DRIVEN
- ✅ `ViolationSeverity` 枚举：CRITICAL / HIGH / MEDIUM / LOW / INFO (5 级严重度)
- ✅ `PrincipleViolation` 数据类：原则违规记录
- ✅ `VerificationCheckpoint` 数据类：验证检查点
- ✅ `KarpathyEnforcementReport` 数据类：执行报告（含 to_dict 序列化）
- ✅ 原则合规性检查功能
- ✅ 违规检测与提醒功能
- ✅ 验证检查点管理功能
- ✅ 执行报告生成功能（JSON 导出）

##### Karpathy 原则融入角色 Prompt
- ✅ SKILL.md 新增 Karpathy 四大核心原则整体行为准则
- ✅ 每个角色（架构师、产品经理、测试专家、独立开发者、UI 设计师）新增 Karpathy 原则应用表
- ✅ 新增"Karpathy 原则应用速查"表
- ✅ 新增"LLM 常见问题"对照表
- ✅ 新增"通用行为准则"（所有角色必须遵守）

##### 四大原则详细说明
- ✅ Think Before Coding（三思而后行）：明确假设、呈现权衡、遇到不清就问
- ✅ Simplicity First（简单优先）：最小代码、无 speculative features、无过度抽象
- ✅ Surgical Changes（精准修改）：只改需要的、不改无关的、保持风格一致
- ✅ Goal-Driven Execution（目标驱动）：定义成功标准、验证检查点、迭代直到完成

#### Claude Code SubAgent 适配器

##### 跨平台适配
- ✅ `ClaudeCodeSubAgentAdapter` 类 (`scripts/claude_code_subagent_adapter.py`)
- ✅ 自动平台检测：`claude_code` / `trae` / `unknown`
- ✅ 统一 `invoke_agent()` 接口
- ✅ Claude Code 平台：通过 claude subagent 命令调用
- ✅ Trae IDE 平台：通过原有机制调用
- ✅ 未知平台：通用回退方法
- ✅ 环境变量检测：`CLAUDE_CODE_ENV` / `ANTHROPIC_ENV` / `TRAE_ENV` / `TRAE_AGENT_PATH`

## [2.3.0] - 2026-03-28

### Added

#### 代码地图增强 (v2.3)

##### 多项目 Workspace 支持
- ✅ 支持一个 workspace 包含多个项目的场景
- ✅ 自动识别项目所属 workspace
- ✅ 明确项目标识（项目名称、工作空间、相对路径）

##### 多角色代码走读
- ✅ `MultiRoleCodeWalkthrough` 类 (`scripts/multi_role_code_walkthrough.py`)
- ✅ 支持 5 种角色分析：架构师、产品经理、独立开发者、UI 设计师、测试专家
- ✅ 角色专属代码分析 prompt 模板
- ✅ 文档对齐机制，合并多角色分析结果
- ✅ 生成统一代码地图
- ✅ 生成代码走读审查报告 (`CodeReviewReportGenerator` 类)

##### 真正的多角色协作分析器 (v2.3)
- ✅ `MultiRoleCollaborativeAnalyzer` 类 (`scripts/multi_role_collaborative_analyzer.py`)
- ✅ 集成 Trae Agent 调度系统 (`trae_agent_dispatch_v2.py`)
- ✅ 每个角色使用专属 prompt 模板进行真实分析
- ✅ 真正的多角色协作：架构师、产品经理、独立开发者、UI 设计师、测试专家
- ✅ 支持并行/串行执行各角色分析任务
- ✅ 提取各角色的关键发现和建议

##### 角色专属 Prompt 模板
- ✅ 架构师代码分析模板 (`docs/spec/role-prompts/architect-code-analysis.md`)
- ✅ 产品经理代码分析模板 (`docs/spec/role-prompts/pm-code-analysis.md`)
- ✅ 独立开发者代码分析模板 (`docs/spec/role-prompts/coder-code-analysis.md`)
- ✅ UI 设计师代码分析模板 (`docs/spec/role-prompts/ui-code-analysis.md`)
- ✅ 测试专家代码分析模板 (`docs/spec/role-prompts/test-code-analysis.md`)

##### 代码地图生成器 v2.1
- ✅ `CodeMapGenerator` 类增强 (`scripts/code_map_generator_v2.py`)
- ✅ 支持多语言分析：Python, Java, JavaScript/TypeScript, Go 等
- ✅ 架构分层检测（API Layer, Service Layer, Data Layer 等）
- ✅ 函数和类详细信息提取
- ✅ 调用关系追踪
- ✅ 复杂度评估
- ✅ md 格式输出

##### 代码与文档分离 (v2.3)
- ✅ 代码地图仅保留核心结构内容（项目概览、架构视图、代码结构、多角色视角、分析共识）
- ✅ 审查报告包含完整风险评估和建议
- ✅ 移除代码地图中的"建议"和"快速参考"章节

##### 3D 代码地图可视化 (v2.3)
- ✅ `docs/code-map-visualizer.html`
- ✅ Three.js 3D 引擎，支持拖拽旋转、滚轮缩放
- ✅ 节点类型区分：模块（蓝色）、类（紫色）、函数（绿色）
- ✅ 调用关系可视化：节点间连线表示调用关系
- ✅ 动态流动效果：边使用虚线动画 + 流动粒子
- ✅ 深色/浅色主题一键切换
- ✅ 点击展开/折叠、双击高亮调用链路、搜索过滤

##### 任务可视化页面 (v2.3)
- ✅ `docs/task-visualizer.html`
- ✅ 概览统计面板：总任务数、待开始、进行中、已完成、被阻塞
- ✅ 角色任务卡片：任务列表、状态、进度
- ✅ 任务依赖关系和阻塞关系展示
- ✅ 任务交接记录时间线
- ✅ Canvas 绘制协同关系图
- ✅ 定时刷新机制（30秒自动刷新）
- ✅ 任务详情弹窗

##### 文档与代码一致性检查 (v2.3)
- ✅ `ProjectScanner` 支持文档文件扫描 (.md, .txt, .rst, .adoc)
- ✅ `CodeReviewReportGenerator` 新增 `_generate_doc_code_consistency_check()` 方法
- ✅ 文档覆盖概览统计
- ✅ 检查清单表格（README、API、配置、架构文档）
- ✅ 差异分析按严重程度分级（严重/中等/轻微）

## [2.2.0] - 2026-03-21

### Added

#### 长程 Agent 支持 (基于 Anthropic《Effective Harnesses for Long-Running Agents》)

##### Checkpoint 检查点管理器
- ✅ `CheckpointManager` 类 (`scripts/checkpoint_manager.py`)
  - 定期保存任务状态（像人类工程师 git commit）
  - 支持从任意断点恢复
  - 数据完整性校验（SHA256 哈希）
  - 自动过期清理机制
  - 交接文档生成

##### Handoff 交接班协议
- ✅ `HandoffDocument` 类
  - 标准化交接文档（JSON + Markdown）
  - 交接原因记录和信心度评估
  - 重要注意事项传递
  - 支持双智能体架构（Planner + Executor）
  - 交接历史追踪

##### TaskList 任务清单管理器
- ✅ `TaskListManager` 类 (`scripts/task_list_manager.py`)
  - 4 级优先级（CRITICAL/HIGH/MEDIUM/LOW）
  - 5 种状态（PENDING/IN_PROGRESS/COMPLETED/BLOCKED/CANCELLED）
  - 依赖关系管理（is_ready 检查）
  - 进度跟踪和工时估算
  - Markdown 导出功能

##### WorkflowEngineV2 增强版
- ✅ `WorkflowEngineV2` 类 (`scripts/workflow_engine_v2.py`)
  - 集成 Checkpoint + TaskList + Handoff
  - 智能任务拆分（基于关键词识别）
  - 定期自动保存检查点
  - 支持 Agent 交接班
  - 断点恢复机制

##### 完整测试套件
- ✅ 24 个测试全部通过
  - `TestCheckpointManager`: 7 个测试
  - `TestHandoffDocument`: 3 个测试
  - `TestTaskListManager`: 9 个测试
  - `TestWorkflowEngineV2`: 5 个测试

### Fixed

#### 角色匹配问题
- ✅ 修复角色匹配总是匹配到 UI 设计师的问题
  - 优化关键词区分度
  - 添加 AI 语义匹配
  - 增强优先级权重

#### JSON 序列化问题
- ✅ 修复枚举类型 JSON 序列化错误
  - Checkpoint 状态枚举转换
  - TaskList 状态和优先级枚举转换
  - WorkflowEngine 步骤状态枚举转换
  - 数据完整性哈希校验

## [1.3.0] - 2026-03-12

### Fixed

#### Agent Loop 思考循环问题
- ✅ 修复 `is_all_tasks_completed()` 方法
  - 优先从任务文件中检查实际完成状态
  - 遍历所有测试用例，检查是否有待实现的标记
  - 出错时使用进度文件作为备选方案

- ✅ 优化 `agent_loop_controller.py` 循环逻辑
  - 新增连续无进展计数器（防止无限循环）
  - 连续 3 次迭代无进展时强制退出
  - 增加任务执行成功/失败的计数器管理
  - 确保循环在各种情况下都能正确退出

- ✅ 改进任务状态同步机制
  - 以任务文件状态为准，确保同步
  - 正确处理已完成和待完成任务的列表更新
  - 避免状态冲突和不一致

- ✅ 修复路径问题
  - 从 skill 目录导入检查器脚本
  - 使用相对路径定位进度文件

## [1.2.0] - 2026-03-11

### Added

#### 规范驱动开发功能
- ✅ 完整的规范工具链（scripts/spec_tools.py）
  - `spec_tools.py init` - 初始化规范环境
  - `spec_tools.py analyze` - 分析规范完整性和一致性
  - `spec_tools.py update` - 更新规范文档
  - `spec_tools.py validate` - 验证规范执行情况

- ✅ 项目宪法（CONSTITUTION.md）
  - 项目核心价值观和原则
  - 技术栈约束和决策
  - 代码规范和标准
  - 多角色共识制定流程

- ✅ 项目规范（SPEC.md）
  - 需求规范（产品经理负责）
  - 技术规范（架构师负责）
  - 测试规范（测试专家负责）
  - 开发规范（独立开发者负责）

- ✅ 规范分析报告（SPEC_ANALYSIS.md）
  - 规范完整性分析
  - 规范一致性检查
  - 规范可行性评估
  - 改进建议

- ✅ 规范模板库
  - CONSTITUTION_TEMPLATE.md - 项目宪法模板
  - SPEC_TEMPLATE.md - 项目规范模板
  - SPEC_ANALYSIS_TEMPLATE.md - 规范分析模板
  - PROJECT_STRUCTURE_TEMPLATE.md - 项目结构模板

#### 代码地图生成功能
- ✅ 代码地图生成器（scripts/code_map_generator.py）
  - 自动扫描项目代码结构
  - 识别核心组件和入口文件
  - 分析模块依赖关系
  - 生成技术栈统计

- ✅ 输出格式支持
  - JSON 格式（code_map.json）- 机器可读
  - Markdown 格式（PROJECT_STRUCTURE.md）- 人类可读
  - 可视化项目结构树
  - 组件职责说明

- ✅ 代码地图内容
  - 项目概览和统计信息
  - 目录结构树
  - 核心组件和入口文件
  - 模块依赖关系图
  - 技术栈分析（语言、框架、库）

#### 项目理解功能
- ✅ 项目理解生成器（scripts/project_understanding.py）
  - 快速读取项目文档和代码
  - 为各角色生成定制化理解文档
  - 提供项目概览和技术栈分析
  - 作为工作初始化上下文

- ✅ 角色特定理解文档
  - project_understanding.json - 整体项目信息
  - architect_understanding.md - 架构师理解（技术栈、架构模式、部署结构）
  - product_manager_understanding.md - 产品经理理解（功能列表、用户价值、竞品分析）
  - test_expert_understanding.md - 测试专家理解（测试覆盖、质量风险、自动化策略）
  - solo_coder_understanding.md - 独立开发者理解（代码结构、开发规范、技术债务）

- ✅ 项目理解内容
  - 项目概览（名称、描述、目标）
  - 技术栈分析（编程语言、框架、数据库、中间件）
  - 代码结构分析（目录组织、模块划分、代码统计）
  - 文档分析（README、API 文档、设计文档）
  - 依赖分析（package.json、pom.xml、Cargo.toml 等）
  - 角色特定见解和建议

#### 增强版角色 Prompt 系统
- ✅ 规范相关职责
  - 架构师：负责制定和维护技术规范
  - 产品经理：负责制定和维护需求规范
  - 测试专家：负责制定和维护测试规范
  - 独立开发者：负责遵循规范并反馈改进建议

- ✅ 规范驱动开发流程
  - 所有开发工作必须基于已评审的规范
  - 规范变更必须经过多角色共识
  - 规范执行情况必须定期检查
  - 规范文档必须保持最新状态

### Changed

- ✅ 更新 README.md
  - 添加 2026 年 3 月最新更新说明
  - 添加规范驱动开发详细说明
  - 添加代码地图生成详细说明
  - 添加项目理解详细说明
  - 更新功能特性列表

- ✅ 更新 SKILL.md
  - 添加规范驱动开发职责
  - 添加代码地图生成职责
  - 添加项目理解职责
  - 更新角色定义和触发关键词

- ✅ 更新 EXAMPLES.md
  - 添加规范驱动开发示例
  - 添加代码地图生成示例
  - 添加项目理解示例
  - 更新场景示例

### Improved

- ✅ 文档驱动开发流程优化
  - 明确文档依赖关系
  - 添加检查点机制
  - 强化评审流程
  - 完善违规处理

- ✅ 多角色协作机制
  - 优化共识决策流程
  - 改进角色间沟通
  - 增强上下文共享
  - 提升协作效率

## [1.1.0] - 2024-03-05

### Added

#### 新功能/功能变更标准工作流程
- ✅ 七阶段标准工作流程
  - 阶段 1: 需求分析（产品经理）
  - 阶段 2: 架构设计（架构师）
  - 阶段 3: 测试设计（测试专家）
  - 阶段 4: 任务分解（独立开发者）
  - 阶段 5: 开发实现（独立开发者）
  - 阶段 6: 测试验证（测试专家）
  - 阶段 7: 发布评审（多角色）

- ✅ 核心原则：先设计、先写文档、再开发
  - 绝对禁止：未设计直接编码、文档未完成就开发、未评审直接实施
  - 必须遵循：所有新功能必须先设计、所有设计必须先写文档、所有文档必须经过评审

- ✅ 跨角色设计评审机制
  - PRD 评审流程（产品经理 → 架构师 + 测试专家）
  - 架构设计评审流程（架构师 → 产品经理 + 测试专家 + 开发者）
  - 测试计划评审流程（测试专家 → 产品经理 + 架构师 + 开发者）
  - 开发计划评审流程（开发者 → 架构师 + 测试专家）

- ✅ 文档依赖关系管理
  - PRD → 架构设计 → 测试计划 → 开发任务 → 测试报告 → 发布决策
  - 明确各阶段的输入输出和检查点

- ✅ 违规处理机制
  - 发现未按流程执行的应对措施
  - 回溯到上一个检查点
  - 补充缺失的文档或评审

#### 基于文档的任务分解与执行规则
- ✅ 所有角色的文档驱动任务分解规范
  - 架构师：基于架构设计文档分解任务
  - 产品经理：基于 PRD 文档分解任务
  - 测试专家：基于测试计划文档分解任务
  - 独立开发者：基于所有技术文档分解任务

- ✅ 任务依赖关系定义
  - 明确定义阶段间的依赖关系
  - 下游任务必须等待上游任务完成
  - 文档编写任务必须在设计/实现完成后开始

- ✅ 检查点机制
  - 每个阶段设置检查点（CP-1, CP-2, ...）
  - 检查内容包括完整性和质量要求
  - 通过标准明确，不通过需修复

- ✅ 独立开发者前置条件检查
  - 必须确认 PRD 文档已评审通过
  - 必须确认架构设计文档已评审通过
  - 必须确认测试计划文档已评审通过
  - 文档阅读确认输出要求

#### 标准化文档模板
- ✅ 架构师文档模板
  - ARCHITECTURE_DESIGN_TEMPLATE.md - 架构设计文档模板
  - 包含更新履历、系统概述、模块设计、接口定义等章节

- ✅ 产品经理文档模板
  - PRD_TEMPLATE.md - 产品需求文档模板
  - 包含更新履历、需求分析、功能需求、非功能需求等章节

- ✅ 测试专家文档模板
  - TEST_PLAN_TEMPLATE.md - 测试计划文档模板
  - 包含更新履历、测试策略、测试用例设计、测试执行计划等章节

#### 文档更新履历规范
- ✅ 所有文档必须包含更新履历章节
- ✅ 统一更新履历表格格式
- ✅ 要求记录版本号、日期、更新人、更新内容、审核状态

### Changed

- ✅ 更新 README.md
  - 添加新功能/功能变更标准工作流程说明
  - 添加文档依赖关系图示

- ✅ 更新 SKILL.md
  - 添加七阶段标准工作流程详细说明
  - 添加跨角色设计评审机制
  - 添加基于文档的任务分解与执行规则
  - 更新独立开发者的前置条件检查要求

## [1.0.0] - 2024-03-04

### Added

#### 核心功能
- ✅ 智能角色调度系统
  - 基于关键词匹配的角色识别算法
  - 位置权重计算（越靠前权重越高）
  - 置信度评估机制
  - 支持 4 种角色自动识别

- ✅ 多角色协同机制
  - 共识组织算法
  - 冲突检测和解决
  - 多角色评审流程
  - 角色间上下文共享

- ✅ 完整项目生命周期支持
  - 8 阶段项目流程
  - 从需求到部署全流程
  - 质量门禁和评审机制
  - 项目阶段感知调度

- ✅ 上下文感知调度
  - 历史上下文智能继承
  - 项目阶段识别
  - 任务链自动关联
  - 上下文优先级管理

#### 角色系统
- ✅ 架构师 (Architect)
  - 系统性思维规则
  - 5-Why 分析法
  - 零容忍清单（6 项禁止）
  - 验证驱动设计
  - 完整输出模板

- ✅ 产品经理 (Product Manager)
  - 需求三层挖掘规则
  - SMART 验收标准
  - 竞品分析规则
  - 用户调研方法
  - PRD 文档规范

- ✅ 测试专家 (Test Expert)
  - 测试金字塔规则
  - 正交分析法
  - 5 类测试场景设计
  - 真机测试规则
  - 自动化测试规范

- ✅ 独立开发者 (Solo Coder)
  - 零容忍清单（10 项禁止）
  - 完整性检查规则（4 维度）
  - 自测规则（3 层测试）
  - 代码质量规范
  - 错误处理规范

#### 调度脚本
- ✅ `trae_agent_dispatch.py`
  - 命令行界面
  - 自动角色识别
  - 手动角色指定
  - 共识机制触发
  - 完整项目流程
  - 代码审查模式
  - 紧急修复通道

#### 文档系统
- ✅ 技能定义文件 (SKILL.md)
  - 34KB 完整 Prompt
  - 4 角色详细规则
  - 工作原则和流程
  - 检查清单

- ✅ 用户指南
  - 快速开始
  - 使用示例
  - 最佳实践
  - 常见问题

- ✅ 安装指南
  - 多种安装方式
  - 验证步骤
  - 故障排查

- ✅ 角色配置文档
  - 角色定义
  - 协作机制
  - 触发时机

#### 工具脚本
- ✅ `install-global.sh`
  - 自动化安装脚本
  - 备份机制
  - 验证流程

- ✅ `schedule_agent.py`
  - 调度执行脚本
  - 共识组织
  - 结果处理

### Changed

- 无（初始版本）

### Fixed

- 无（初始版本）

### Deprecated

- 无（初始版本）

### Removed

- 无（初始版本）

### Security

- ✅ 安全特性
  - 敏感配置加密存储
  - 权限检查机制
  - 安全测试场景覆盖
  - OWASP Top 10 检测支持

---

## 版本说明

### 版本号格式

遵循语义化版本规范：`MAJOR.MINOR.PATCH`


## 未来计划

### [1.1.0] - 计划中

#### 新增角色
- 🔄 运维专家 (DevOps Engineer)
- 🔄 数据分析师 (Data Analyst)
- 🔄 UI/UX 设计师 (UI/UX Designer)

#### 增强功能
- 🔄 角色学习能力（基于历史反馈优化）
- 🔄 多语言支持（英文、日文等）
- 🔄 自定义角色配置
- 🔄 角色技能市场


## 贡献者

感谢所有为这个项目做出贡献的人！

📝 查看 [CONTRIBUTING.md](CONTRIBUTING.md) 了解如何贡献。

---

**Made with ❤️ by weiansoft **
