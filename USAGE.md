# Trae Multi-Agent Skill 使用说明

Trae Multi-Agent Skill 是一个强大的多智能体协作工具，能够根据任务类型自动调度到合适的智能体角色，支持完整项目生命周期管理。

## 核心功能

### 智能角色调度
- **5 个专业角色**：架构师、产品经理、测试专家、独立开发者、UI 设计师
- **自动识别**：基于关键词匹配和位置权重算法
- **置信度评估**：确保角色选择的准确性

### 八阶段标准工作流程
1. **需求分析**（产品经理）- 定义用户价值和验收标准
2. **架构设计**（架构师）- 设计系统架构和技术选型
3. **UI 设计**（UI 设计师）- 创建独特、生产级的 UI 界面
4. **测试设计**（测试专家）- 制定测试策略和用例
5. **任务分解**（独立开发者）- 分解开发任务
6. **开发实现**（独立开发者）- 编写高质量代码
7. **测试验证**（测试专家）- 验证质量和功能
8. **发布评审**（多角色）- 多角色共识评审

### 核心特性
- **Karpathy 四大核心原则** (v2.4)：Think Before Coding / Simplicity First / Surgical Changes / Goal-Driven Execution 强制执行
- **跨平台适配** (v2.4)：Claude Code / Trae IDE 统一 subagent 调用接口
- **多角色代码走读** (v2.3)：5 个角色多视角分析代码，生成对齐后的统一代码地图
- **3D 代码地图可视化** (v2.3)：Three.js 交互式可视化，动态流动效果
- **任务可视化** (v2.3)：实时任务状态、进度、依赖关系、交接记录
- **长程 Agent 支持** (v2.2)：Checkpoint 检查点、Handoff 交接班、TaskList 任务清单
- **规范驱动开发**：基于项目规范和文档进行开发
- **代码地图生成**：自动生成项目代码结构映射
- **项目理解**：快速读取项目文档和代码
- **多角色协同**：组织多个角色共同完成复杂任务
- **共识机制**：复杂任务自动组织多角色评审
- **中英文双语**：支持自动语言识别和切换

### v2.4 新功能

#### Karpathy 原则检查
```bash
# 对项目执行 Karpathy 原则合规性检查
python3 scripts/karpathy_principle_enforcer.py /path/to/project

# 生成原则执行报告
python3 scripts/karpathy_principle_enforcer.py /path/to/project --report
```

#### Claude Code 平台调用
```python
from claude_code_subagent_adapter import ClaudeCodeSubAgentAdapter

adapter = ClaudeCodeSubAgentAdapter()
result = adapter.invoke_agent('architect', '设计系统架构')

## 快速开始

```bash
# 自动调度（推荐）
python3 scripts/trae_agent_dispatch.py --task "设计系统架构"

# 指定角色
python3 scripts/trae_agent_dispatch.py --task "实现功能" --agent solo_coder

# 多角色共识
python3 scripts/trae_agent_dispatch.py --task "启动新项目" --consensus true

# 完整项目流程
python3 scripts/trae_agent_dispatch.py --task "启动项目" --project-full-lifecycle
```

## 使用场景

- **项目启动**：自动组织多角色共识，启动完整项目
- **功能开发**：单角色调度，快速开发功能
- **代码审查**：多角色代码审查，确保代码质量
- **架构设计**：架构师设计系统架构和技术选型
- **UI 设计**：UI 设计师创建独特、生产级的 UI 界面

## 技术栈

- **Python 3.8+**：核心调度脚本
- **Trae IDE**：集成开发环境
- **Markdown**：文档格式
- **JSON**：配置格式

## 文档资源

- [README.md](README.md) - 完整项目说明
- [SKILL.md](SKILL.md) - 技能详细说明
- [USAGE_GUIDE.md](docs/guides/USAGE_GUIDE.md) - 使用指南
- [EXAMPLES.md](EXAMPLES.md) - 使用示例

## GitHub 仓库

访问我们的 GitHub 仓库获取更多信息：
https://github.com/weiransoft/TraeMultiAgentSkill

## 许可证

MIT License - 详见 [LICENSE](LICENSE) 文件
