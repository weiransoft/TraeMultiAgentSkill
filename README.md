# Trae Multi-Agent Skill

🎭 基于任务类型动态调度到合适的智能体角色（架构师、产品经理、测试专家、独立开发者）。支持多智能体协作、共识机制和完整项目生命周期管理。支持中英文双语。

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
- [配置说明](#-配置说明)
- [示例场景](#-示例场景)
- [技术架构](#-技术架构)
- [贡献指南](#-贡献指南)
- [常见问题](#-常见问题)
- [许可证](#-许可证)

## ✨ 功能特性

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

### 角色 Prompt 系统

每个角色都配备完整的工作规则和质量标准：

- ✅ **系统性思维规则** - 确保设计完整性
- ✅ **深度思考规则** - 5-Why 分析法找根因
- ✅ **零容忍清单** - 禁止 mock、硬编码、简化
- ✅ **验证驱动设计** - 完整验收标准
- ✅ **完整性检查** - 多维度检查清单
- ✅ **自测规则** - 3 层测试验证

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

## 📦 安装说明

### 方式一：全局安装（推荐）

```bash
# 运行安装脚本
cd /path/to/claw/.trae/skills
./install-global.sh

# 验证安装
ls -lh ~/.trae/skills/trae-multi-agent/

# 重启 Trae 应用
```

### 方式二：项目级安装

技能已包含在项目目录中，Trae 会自动加载：

```
项目目录/.trae/skills/trae-multi-agent/
```

### 方式三：手动安装

```bash
# 1. 创建技能目录
mkdir -p ~/.trae/skills/trae-multi-agent

# 2. 复制技能文件
cp -r /path/to/claw/.trae/skills/trae-multi-agent/* \
      ~/.trae/skills/trae-multi-agent/

# 3. 验证安装
ls -lh ~/.trae/skills/trae-multi-agent/SKILL.md

# 4. 重启 Trae
```

### 验证安装

```bash
# 检查技能文件
ls -lh ~/.trae/skills/trae-multi-agent/SKILL.md
# 应显示：34K SKILL.md

# 测试调度脚本
python3 scripts/trae_agent_dispatch.py --task "设计系统架构"
# 应显示：🎯 自动识别为：架构师
```

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
