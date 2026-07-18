# Claude Code 全局 Skill 安装完成

## ✅ 安装状态

**技能名称**: Multi-Agent Team Dispatcher  
**版本**: v2.4.1  
**安装位置**: `~/.claude/skills/trae-multi-agent`  
**安装时间**: 2026-04-15  
**状态**: ✅ 已成功安装

---

## 📦 安装内容

### 核心文件
- ✅ `SKILL.md` - Skill 主文档
- ✅ `README.md` - 项目说明
- ✅ `skills-index.json` - Skill 索引
- ✅ `claude-code-skill.json` - Claude Code 配置文件

### Python 脚本（12 个核心脚本）
- ✅ `trae_agent_dispatch_v2.py` - Agent 调度器
- ✅ `claude_code_subagent_adapter.py` - Claude Code SubAgent 适配器
- ✅ `dual_layer_context_manager.py` - 双层上下文管理器
- ✅ `role_matcher.py` - 角色匹配器
- ✅ `workflow_engine_v2.py` - 增强版工作流引擎
- ✅ `skill_registry.py` - Skill 注册表
- ✅ `task_completion_checker.py` - 任务完成检查器
- ✅ `task_list_manager.py` - 任务列表管理器
- ✅ `checkpoint_manager.py` - 检查点管理器
- ✅ `ai_assistant.py` - AI 助手
- ✅ `ai_semantic_matcher.py` - AI 语义匹配器
- ✅ `agent_loop_controller_v2.py` - Agent 循环控制器

### 文档
- ✅ `docs/` - 完整文档目录
  - `guides/CLAUDE_CODE_SUBAGENT_GUIDE.md` - Claude Code 使用指南
  - `roles/` - 各角色模板
  - `spec/` - 规范文档

---

## 🚀 使用方法

### 方法 1: 自动调用（推荐）

在 Claude Code 中直接使用自然语言：

```bash
# 架构设计
claude "设计系统架构：包括模块划分、技术选型、部署方案"

# 产品需求
claude "定义产品需求：广告拦截功能，需要明确的验收标准"

# 测试策略
claude "制定测试策略：覆盖正常、异常、边界、性能场景"

# 功能开发
claude "实现用户登录功能：完整代码，包含单元测试"

# 代码审查
claude "审查当前代码：评估架构合规性、代码质量、性能问题"
```

### 方法 2: 指定角色调用

```bash
# 调用架构师
python3 ~/.claude/skills/trae-multi-agent/scripts/trae_agent_dispatch_v2.py \
  --task "设计系统架构" \
  --agent architect

# 调用产品经理
python3 ~/.claude/skills/trae-multi-agent/scripts/trae_agent_dispatch_v2.py \
  --task "定义产品需求" \
  --agent product-manager

# 调用测试专家
python3 ~/.claude/skills/trae-multi-agent/scripts/trae_agent_dispatch_v2.py \
  --task "制定测试策略" \
  --agent tester

# 调用独立开发者
python3 ~/.claude/skills/trae-multi-agent/scripts/trae_agent_dispatch_v2.py \
  --task "实现用户登录功能" \
  --agent solo-coder

# 调用 UI 设计师
python3 ~/.claude/skills/trae-multi-agent/scripts/trae_agent_dispatch_v2.py \
  --task "设计登录页面 UI" \
  --agent ui-designer
```

### 方法 3: 使用 SubAgent 适配器

```bash
# 直接调用 SubAgent
python3 ~/.claude/skills/trae-multi-agent/scripts/claude_code_subagent_adapter.py \
  architect "设计系统架构，支持高并发和弹性扩展"

# 查看帮助
python3 ~/.claude/skills/trae-multi-agent/scripts/claude_code_subagent_adapter.py
```

---

## 🎭 可用角色

| 角色 | 英文名称 | 职责 | 触发关键词 |
|------|---------|------|-----------|
| 架构师 | architect | 设计系统性、前瞻性、可落地、可验证的架构 | 架构、设计、选型、审查、性能、瓶颈 |
| 产品经理 | product-manager | 定义用户价值清晰、需求明确、可落地、可验收的产品 | 需求、PRD、用户故事、竞品、市场 |
| 测试专家 | tester | 确保全面、深入、自动化、可量化的质量保障 | 测试、质量、验收、自动化、缺陷 |
| 独立开发者 | solo-coder | 编写完整、高质量、可维护、可测试的代码 | 实现、开发、代码、修复、优化、重构 |
| UI 设计师 | ui-designer | 创建独特、生产级的 UI 界面，避免 AI slop 美学 | UI 设计、界面设计、视觉设计、前端设计 |

---

## 📋 Karpathy 四大核心原则

所有角色都遵循 **Karpathy 四大核心原则**：

### 1️⃣ Think Before Coding（三思而后行）
> "Don't assume. Don't hide confusion. Surface tradeoffs."

- ✅ 明确陈述假设
- ✅ 呈现多种解释
- ✅ 提出更简单的方法
- ✅ 遇到不清楚就停止

### 2️⃣ Simplicity First（简单优先）
> "Minimum code that solves the problem. Nothing speculative."

- ✅ 无单次使用的抽象
- ✅ 无"灵活性"或"可配置性"
- ✅ 无未来可能用到的代码
- ✅ 无不相关代码的"改进"

### 3️⃣ Surgical Changes（精准修改）
> "Touch only what's needed."

- ✅ 只改直接相关的行
- ✅ 保持风格一致
- ✅ 模仿现有模式
- ✅ 不要溢出修改

### 4️⃣ Goal-Driven Execution（目标驱动执行）
> "Define success criteria and loop until verified."

- ✅ 定义成功标准
- ✅ 设定验证检查点
- ✅ 迭代直到验证通过
- ✅ 使用可验证的目标

---

## 🔧 技术特性

### 环境自动检测
- ✅ 自动检测 Claude Code 环境
- ✅ 自动检测 Trae IDE 环境
- ✅ 支持降级方案（模拟调用）

### SubAgent 支持
- ✅ 原生支持 Claude Code SubAgent
- ✅ 统一调用接口
- ✅ 智能环境适配

### 多 Agent 协作
- ✅ 支持多角色协作
- ✅ 共识机制
- ✅ 完整项目生命周期管理

### 代码地图
- ✅ 多角色代码走读
- ✅ 统一代码地图生成
- ✅ 3D 可视化支持

---

## 📖 详细文档

### 使用指南
- [CLAUDE_CODE_SUBAGENT_GUIDE.md](docs/guides/CLAUDE_CODE_SUBAGENT_GUIDE.md) - Claude Code 使用指南
- [EXAMPLES.md](EXAMPLES.md) - 使用示例
- [USAGE.md](USAGE.md) - 使用说明

### 角色文档
- [架构师模板](docs/roles/architect/ARCHITECTURE_DESIGN_TEMPLATE.md)
- [产品经理模板](docs/roles/product-manager/PRD_TEMPLATE.md)
- [测试专家模板](docs/roles/test-expert/TEST_PLAN_TEMPLATE.md)
- [独立开发者模板](docs/roles/solo-coder/DEVELOPMENT_TEMPLATE.md)
- [UI 设计师模板](docs/roles/ui-designer/UI_DESIGN_TEMPLATE.md)

### 规范文档
- [项目宪法](docs/spec/CONSTITUTION.md)
- [代码地图规范](docs/spec/CODE_MAP_SPEC.md)
- [多角色提示词索引](docs/spec/MULTI_ROLE_PROMPTS_INDEX.md)

---

## ⚙️ 配置说明

### Claude Code 配置（可选）

如果需要自定义 Claude Code 的行为，可以编辑：
```bash
~/.claude/skills/trae-multi-agent/claude-code-skill.json
```

### 环境变量（可选）

```bash
# Claude Code 环境
export CLAUDE_CODE_ENV=1

# Trae IDE 环境
export TRAE_ENV=1

# 自定义 Skill 路径
export TRAE_AGENT_PATH=~/.claude/skills/trae-multi-agent
```

---

## 🔍 故障排查

### 问题 1: 找不到 claude 命令

**症状**: `command not found: claude`

**解决方案**:
```bash
# 检查 Claude Code 是否安装
which claude

# 如果未安装，请先安装 Claude Code
# https://claude.ai/download
```

### 问题 2: SubAgent 调用失败

**症状**: 提示 "SubAgent 调用失败"

**解决方案**:
1. 检查 Python 版本：`python3 --version`（需要 3.8+）
2. 检查脚本权限：`chmod +x ~/.claude/skills/trae-multi-agent/scripts/*.py`
3. 查看详细日志：查看 `logs/` 目录下的日志文件

### 问题 3: 团队成员没有回复

**症状**: 团队成员收到了消息但没有回复

**解决方案**:
使用 `invoke_subagent()` 直接调用 subagent：
```python
from claude_code_subagent_adapter import invoke_subagent

result = invoke_subagent(
    agent_type='architect',
    task='设计系统架构',
    context={
        'project_type': '电商系统',
        'tech_stack': ['Java 21', 'Spring Boot 3']
    }
)
```

---

## 📊 版本历史

### v2.4.1 (2026-04-15)
- ✅ 新增 Claude Code 全局 Skill 安装支持
- ✅ 新增快速安装脚本
- ✅ 新增 Claude Code 配置文件
- ✅ 优化 SubAgent 调用机制

### v2.4 (2026-04-14)
- ✅ 集成 Karpathy 四大核心原则
- ✅ 新增 Claude Code SubAgent 适配器
- ✅ 所有角色新增行为准则
- ✅ 修复 SubAgent 调用问题

### v2.3 (2026-03-25)
- ✅ 多角色代码走读
- ✅ 代码地图 Workspace 支持
- ✅ 3D 代码地图可视化
- ✅ 任务可视化页面

---

## 🎉 快速开始

```bash
# 1. 确认安装
ls -la ~/.claude/skills/trae-multi-agent/

# 2. 测试调用
python3 ~/.claude/skills/trae-multi-agent/scripts/claude_code_subagent_adapter.py \
  architect "设计一个简单的待办事项系统架构"

# 3. 使用 Claude Code
claude "设计一个电商系统架构，支持高并发和弹性扩展"
```

---

## 📞 支持与反馈

- **GitHub**: https://github.com/weiransoft/TraeMultiAgentSkill
- **问题反馈**: https://github.com/weiransoft/TraeMultiAgentSkill/issues
- **文档**: ~/.claude/skills/trae-multi-agent/docs/

---

**最后更新**: 2026-04-15  
**版本**: v2.4.1  
**作者**: Claw Team
