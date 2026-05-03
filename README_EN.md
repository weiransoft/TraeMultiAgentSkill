# Trae Multi-Agent Skill

🎭 Dynamically dispatches to appropriate agent roles (Architect, Product Manager, Test Expert, Solo Coder, UI Designer) based on task type. Supports multi-agent collaboration, consensus mechanism, complete project lifecycle management, specification-driven development, code map generation, project understanding, and Karpathy's Four Core Principles enforcement. Supports Chinese-English bilingual.

## 🎉 April 2026 Latest Updates (v2.4)

- ✅ **Karpathy Four Core Principles (v2.4)** - Enforcement of Andrej Karpathy's programming wisdom: Think Before Coding, Simplicity First, Surgical Changes, Goal-Driven Execution
- ✅ **Karpathy Principle Enforcer (v2.4)** - Principle compliance checks, violation detection with 5 severity levels, verification checkpoint management, execution report generation
- ✅ **Claude Code SubAgent Adapter (v2.4)** - Cross-platform agent adapter supporting Claude Code, Trae IDE, and generic fallback
- ✅ **Multi-Role Code Walkthrough (v2.3)** - Architect, PM, Solo Coder, UI Designer, Test Expert analyze code from multiple perspectives, generate aligned unified code map
- ✅ **Code Map Workspace Support (v2.3)** - Supports single workspace with multiple projects, clear project identification
- ✅ **3D Code Map Visualization (v2.3)** - Three.js interactive visualization with flowing animations and theme switching
- ✅ **Task Visualization Page (v2.3)** - Real-time display of role task status, progress, dependencies, handoffs, collaboration graph
- ✅ **Doc-Code Consistency Check (v2.3)** - New document and code consistency checklist in code review report
- ✅ **Long-Running Agent Support (v2.2)** - Based on Anthropic's "Effective Harnesses for Long-Running Agents", supports Checkpoint, Handoff, and TaskList
- ✅ **AI Semantic Role Matching (v2.1)** - Uses LLM to understand task deep semantics, provides explainable matching results and confidence scores
- ✅ **AI Assistant Deep Integration (v2.1)** - Integrated LLM capabilities, supports code review, knowledge Q&A, text analysis
- ✅ **Smart Cache and Fallback Strategy (v2.1)** - Performance optimization, auto-fallback to keyword matching when AI unavailable
- ✅ **UI Designer Role** - Creates unique, production-grade UI interfaces, avoids generic AI "slop" aesthetics

## 🌍 Multi-Language Support / Multi-Language Support

本技能支持中英文双语自动切换 / This skill supports automatic Chinese-English language switching:

- **Auto-detection**: Automatically switches response language based on user language
- **Full Coverage**: All output content supports multiple languages
- **Smart Matching**: Code comments automatically match existing language
- **Flexible Switching**: Supports language switching during conversation

📄 Detailed documentation / 详细文档: [MULTILINGUAL_GUIDE.md](MULTILINGUAL_GUIDE.md)

## 📖 Table of Contents / 目录

- [Features / 功能特性](#-features-功能特性)
- [Quick Start / 快速开始](#-quick-start-快速开始)
- [Agent Roles / 角色介绍](#-agent-roles-角色介绍)
- [Usage Methods / 使用方法](#-usage-methods-使用方法)
- [Installation / 安装说明](#-installation-安装说明)
- [Configuration / 配置说明](#-configuration-配置说明)
- [Example Scenarios / 示例场景](#-example-scenarios-示例场景)
- [Technical Architecture / 技术架构](#-technical-architecture-技术架构)
- [Contribution Guide / 贡献指南](#-contribution-guide-贡献指南)
- [FAQ / 常见问题](#-faq-常见问题)
- [License / 许可证](#-license-许可证)

## ✨ Features / 功能特性

### Core Capabilities / 核心能力

1. **Intelligent Role Dispatching** 🎯
   - Automatically identifies required roles based on task description
   - Based on keyword matching and position weight algorithm
   - Confidence evaluation and best role selection

2. **Multi-Agent Collaboration** 🤝
   - Organizes multiple agents to complete complex tasks together
   - Consensus mechanism ensures decision quality
   - Context sharing between agents

3. **Context Awareness** 🧠
   - Selects roles based on project phase
   - Intelligent inheritance of historical context
   - Automatic task chain association

4. **Complete Project Lifecycle** 📊
   - 8-stage project flow support
   - Full process from requirements to deployment
   - Quality gates and review mechanisms

5. **Specification-Driven Development** 📋
   - Complete specification toolchain (spec_tools.py)
   - Project Constitution (CONSTITUTION.md) development
   - Project Specification (SPEC.md) automatic generation
   - Specification Analysis Report (SPEC_ANALYSIS.md)
   - Specification consistency check and validation
   - Multi-agent consensus for specification development

6. **Code Map Generation** 🗺️
   - Automatically generates project code structure map (code_map_generator_v2.py)
   - Supports JSON and Markdown format output
   - Identifies core components and module dependencies
   - Visual project structure documentation
   - Technology stack analysis and statistics
   - **Multi-Project Workspace Support** (v2.3) - Auto-detects project workspace
   - **Multi-Role Code Walkthrough** (v2.3) - Architect, PM, Solo Coder, UI Designer, Test Expert analyze from multiple perspectives
   - **Document Alignment Mechanism** (v2.3) - Aligns multi-role analysis results, generates unified code map
   - **3D Code Map Visualization** (v2.3) - Three.js interactive visualization with flowing animations, theme switching
   - **Task Visualization Page** (v2.3) - Role task status, progress, dependencies, handoff process
   - Core files: `scripts/code_map_generator_v2.py`, `scripts/multi_role_code_walkthrough.py`, `docs/code-map-visualizer.html`, `docs/task-visualizer.html`

7. **Project Understanding** 📚
   - Quickly reads project documents and code (project_understanding.py)
   - Generates role-specific understanding documents
   - Provides project overview and technology stack analysis
   - Serves as work initialization context
   - Role-specific insights and recommendations

8. **8-Stage Standard Workflow** 📊
   - Stage 1: Requirements Analysis (Product Manager)
   - Stage 2: Architecture Design (Architect)
   - Stage 3: UI Design (UI Designer)
   - Stage 4: Test Design (Test Expert)
   - Stage 5: Task Breakdown (Solo Coder)
   - Stage 6: Development Implementation (Solo Coder)
   - Stage 7: Test Verification (Test Expert)
   - Stage 8: Release Review (Multi-Agent)

9. **Cross-Platform Compatibility** 🌍
   - Supports Windows, Mac, and Linux
   - Unified path handling and character encoding
   - Cross-platform script execution

### Agent Prompt System / 角色 Prompt 系统

Each role is equipped with complete work rules and quality standards:

- ✅ **Systematic Thinking Rules** - Ensures design completeness
- ✅ **Deep Thinking Rules** - 5-Why analysis to find root causes
- ✅ **Zero Tolerance Checklist** - Prohibits mock, placeholder, simplification
- ✅ **Verification-Driven Design** - Complete acceptance criteria
- ✅ **Completeness Check** - Multi-dimensional checklists
- ✅ **Self-Testing Rules** - 3-layer test validation
- ✅ **Karpathy Four Core Principles Enforcement** (v2.4) - Think Before Coding / Simplicity First / Surgical Changes / Goal-Driven Execution

### Karpathy Principles & Cross-Platform (v2.4)

**Karpathy Principle Enforcer** (`scripts/karpathy_principle_enforcer.py`):
- Principle compliance checking with 5 severity levels (CRITICAL/HIGH/MEDIUM/LOW/INFO)
- Violation detection and auto-reminder
- Verification checkpoint management
- Execution report generation (JSON export)

**Claude Code SubAgent Adapter** (`scripts/claude_code_subagent_adapter.py`):
- Unified interface for Claude Code / Trae IDE subagent invocation
- Auto platform detection via environment variables
- Generic fallback for unknown platforms

## 🚀 Quick Start / 快速开始

### Prerequisites / 前置要求

- Python 3.8+
- Trae IDE
- Basic command line knowledge

### Basic Usage / 基础使用

Use directly in Trae without additional commands:

```
# Architecture design task
设计系统架构：包括模块划分、技术选型、部署方案

# Product requirements definition
定义产品需求：广告拦截功能，需要明确的验收标准

# Test strategy formulation
制定测试策略：覆盖正常、异常、边界、性能场景

# Feature development
实现广告拦截功能：完整代码，包含单元测试
```

The agent will automatically identify the task type and dispatch the corresponding role!

### Advanced Usage / 高级使用

Use the dispatch script for more fine-grained control:

```bash
# Auto-identify role
python3 scripts/trae_agent_dispatch.py \
    --task "设计系统架构"

# Specify role
python3 scripts/trae_agent_dispatch.py \
    --task "实现功能" \
    --agent solo_coder

# Multi-agent consensus
python3 scripts/trae_agent_dispatch.py \
    --task "启动新项目：安全浏览器广告拦截功能" \
    --consensus true

# Complete project lifecycle
python3 scripts/trae_agent_dispatch.py \
    --task "安全浏览器广告拦截功能" \
    --project-full-lifecycle

# Specification-driven development
python3 scripts/spec_tools.py init
python3 scripts/spec_tools.py analyze
python3 scripts/spec_tools.py update --spec-file SPEC.md

# Code map generation
python3 scripts/code_map_generator_v2.py /path/to/project --workspace /workspace

# Multi-role code walkthrough
python3 scripts/multi_role_code_walkthrough.py /path/to/project --workspace /workspace

# Project understanding
python3 scripts/project_understanding.py /path/to/project
```

## 🎭 Agent Roles / 角色介绍

### 1. Architect / 架构师

**Responsibilities**: Design systematic, forward-looking, implementable, and verifiable architecture

**Core Principles**:
- ✅ Systematic Thinking - Answer 4 key questions before designing
- ✅ 5-Why Analysis - Continuous questioning to find root causes
- ✅ Zero Tolerance Checklist - Prohibits mock, hardcoding, simplification
- ✅ Verification-Driven Design - Complete acceptance criteria

**Typical Outputs**:
- System architecture diagram (Mermaid)
- Module responsibility list
- Interface definition (input/output/exceptions)
- Data model design
- Deployment architecture description

**Trigger Keywords**: 架构、设计、选型、审查、性能、瓶颈、模块、接口、部署

### 2. Product Manager / 产品经理

**Responsibilities**: Define products with clear user value, explicit requirements, implementable and verifiable

**Core Principles**:
- ✅ Three-Layer Requirements Mining - Surface → Real → Essential
- ✅ SMART Acceptance Criteria - Specific, Measurable, Achievable
- ✅ Competitive Analysis Rules - At least 5 competitive products comparison

**Typical Outputs**:
- Product Requirements Document (PRD)
- User story map
- Acceptance criteria (SMART)
- Competitive analysis report

**Trigger Keywords**: 需求、PRD、用户故事、竞品、市场、调研、验收、UAT、体验

### 3. Test Expert / 测试专家

**Responsibilities**: Ensure comprehensive, in-depth, automated, and quantifiable quality assurance

**Core Principles**:
- ✅ Test Pyramid - 70% Unit + 20% Integration + 10% E2E
- ✅ Orthogonal Analysis - 5 categories of scenarios fully covered
- ✅ Real Device Testing - Real environment verification

**Typical Outputs**:
- Test strategy document
- Test cases (normal/exception/boundary/performance/security)
- Automated test scripts
- Quality assessment report

**Trigger Keywords**: 测试、质量、验收、自动化、性能测试、缺陷、评审、门禁

### 4. Solo Coder / 独立开发者

**Responsibilities**: Write complete, high-quality, maintainable, and testable code

**Core Principles**:
- ✅ Zero Tolerance Checklist - 10 absolute prohibitions
- ✅ Completeness Check - 4-dimensional checklists
- ✅ Self-Testing Rules - 3-layer test validation

**Typical Outputs**:
- Complete feature code
- Unit tests (coverage > 80%)
- Integration tests
- Technical documentation

**Trigger Keywords**: 实现、开发、代码、修复、优化、重构、单元测试、文档

## 💡 Usage Methods / 使用方法

### Scenario 1: Project Startup / 场景 1: 项目启动

```bash
# Complete project startup (multi-agent consensus)
python3 scripts/trae_agent_dispatch.py \
    --task "启动新项目：安全浏览器广告拦截功能" \
    --consensus true \
    --priority high

# Automatic organization:
#   1. Product Manager - Requirements definition
#   2. Architect - Architecture design
#   3. Test Expert - Test strategy
#   4. Solo Coder - Development plan
```

### Scenario 2: Feature Development / 场景 2: 功能开发

```bash
# Single role dispatch (fast development)
python3 scripts/trae_agent_dispatch.py \
    --task "实现广告拦截核心模块" \
    --agent solo_coder \
    --context "基于架构设计文档 v2.0"

# Automatic includes:
#   - Architecture design document as context
#   - Completeness check checklist
#   - Self-testing requirements
```

### Scenario 3: Code Review / 场景 3: 代码审查

```bash
# Multi-agent code review
python3 scripts/trae_agent_dispatch.py \
    --task "审查广告拦截核心模块" \
    --code-review \
    --files src/adblock/ tests/

# Participating roles:
#   - Architect (architecture compliance)
#   - Test Expert (test coverage)
#   - Solo Coder (code quality)
```

### Scenario 4: Emergency Bug Fix / 场景 4: 紧急 Bug 修复

```bash
# Emergency fix (fast track)
python3 scripts/trae_agent_dispatch.py \
    --task "紧急修复：生产环境崩溃" \
    --priority critical \
    --fast-track

# Automatic handling:
#   - Skip regular process
#   - Directly dispatch senior developer
#   - Real-time progress synchronization
```

### Scenario 5: Specification-Driven Development / 场景 5: 规范驱动开发

```bash
# Initialize specification environment
python3 scripts/spec_tools.py init

# Analyze specifications
python3 scripts/spec_tools.py analyze

# Update specification documents
python3 scripts/spec_tools.py update --spec-file SPEC.md

# Specification-driven project startup
python3 scripts/trae_agent_dispatch.py \
    --task "启动规范驱动项目：电商系统" \
    --spec-driven

# Automatic execution:
#   1. Initialize specification environment
#   2. Multi-agent consensus: Formulate project constitution
#   3. Product Manager: Write requirements specification
#   4. Architect: Write technical specification
#   5. Specification review (multi-agent consensus)
#   6. Task breakdown based on specifications
#   7. Each role executes tasks
#   8. Specification verification and quality review
```

### Scenario 6: Code Map & Code Walkthrough / 场景 6: 代码地图与代码走读

```bash
# Generate code map (with workspace support)
python3 scripts/code_map_generator_v2.py /path/to/project --workspace /workspace

# Output:
# - Markdown format: <project>-CODE_MAP.md

# True multi-role collaborative code walkthrough (using Trae Agent dispatch)
python3 scripts/multi_role_collaborative_analyzer.py /path/to/project --workspace /workspace

# Output:
# - Unified code map: <project>-ALIGNED-CODE-MAP.md
# - Code review report: <project>-CODE-REVIEW-REPORT.md

# Simplified multi-role code walkthrough
python3 scripts/multi_role_code_walkthrough.py /path/to/project --workspace /workspace

# Generated content includes:
#   - Unified code map: project overview, architecture layers, multi-role analysis results
#   - Review report: review overview, architecture review, code quality assessment
```

### Scenario 7: Project Understanding / 场景 7: 项目理解

```bash
# Generate project understanding documents
python3 scripts/project_understanding.py /path/to/project

# Output:
# - Overall project information: project_understanding.json
# - Architect understanding: architect_understanding.md
# - Product Manager understanding: product_manager_understanding.md
# - Test Expert understanding: test_expert_understanding.md
# - Solo Coder understanding: solo_coder_understanding.md

# Document content includes:
#   - Project overview and technology stack
#   - Code structure analysis
#   - Document and dependency analysis
#   - Role-specific insights and recommendations
```

## 📦 Installation / 安装说明

### Method 1: Global Installation (Recommended) / 方式一：全局安装（推荐）

```bash
# Run installation script
cd /path/to/claw/.trae/skills
./install-global.sh

# Verify installation
ls -lh ~/.trae/skills/trae-multi-agent/

# Restart Trae application
```

### Method 2: Project-Level Installation / 方式二：项目级安装

Skill is included in project directory, Trae will automatically load:

```
项目目录/.trae/skills/trae-multi-agent/
```

### Method 3: Manual Installation / 方式三：手动安装

```bash
# 1. Create skill directory
mkdir -p ~/.trae/skills/trae-multi-agent

# 2. Copy skill files
cp -r /path/to/claw/.trae/skills/trae-multi-agent/* \
      ~/.trae/skills/trae-multi-agent/

# 3. Verify installation
ls -lh ~/.trae/skills/trae-multi-agent/SKILL.md

# 4. Restart Trae
```

### Verify Installation / 验证安装

```bash
# Check skill files
ls -lh ~/.trae/skills/trae-multi-agent/SKILL.md
# Should display: 34K SKILL.md

# Test dispatch script
python3 scripts/trae_agent_dispatch.py --task "设计系统架构"
# Should display: 🎯 自动识别为：架构师
```

## ⚙️ Configuration / 配置说明

### Skill Configuration (skills-index.json)

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

### Role Recognition Algorithm / 角色识别算法

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

### Consensus Trigger Conditions / 共识触发条件

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

## 📋 New Feature / Feature Change Standard Workflow / 新功能/功能变更标准工作流程

### Core Principle: Design First, Document First, Then Develop / 核心原则：先设计、先写文档、再开发

**Must Follow Workflow**:

```
Phase 1: Requirements Analysis (Product Manager)
    ↓ Review passed
Phase 2: Architecture Design (Architect)
    ↓ Review passed
Phase 3: Test Design (Test Expert)
    ↓ Review passed
Phase 4: Task Breakdown (Solo Coder)
    ↓
Phase 5: Development Implementation (Solo Coder)
    ↓
Phase 6: Test Verification (Test Expert)
    ↓
Phase 7: Release Review (Multi-Agent)
```

**Absolutely Prohibited**:
❌ Start coding without design phase
❌ Start development without writing or completing documentation
❌ Implement without design review

**Document Dependencies**:
```
PRD Document (Product Manager)
    ↓ [Depends on: PRD review passed]
Architecture Design Document (Architect)
    ↓ [Depends on: Architecture review passed]
Test Plan Document (Test Expert)
    ↓ [Depends on: Test plan review passed]
Development Task List (Developer)
    ↓ [Depends on: Development completed]
Test Report (Test Expert)
    ↓ [Depends on: Test passed]
Release Decision (Multi-Agent)
```

Detailed process description: [SKILL.md](SKILL.md) - New Feature / Feature Change Standard Workflow

## 📚 Example Scenarios / 示例场景

### Example 1: Complete Project Startup / 示例 1: 完整项目启动

**Input**:
```
启动新项目：安全浏览器广告拦截功能
- 支持拦截恶意广告和钓鱼网站
- 性能要求：页面加载延迟<100ms
- 需要完整的测试覆盖
```

**Automatic Process**:
```
🎯 Identified as: Multi-agent consensus task

📋 Phase 1: Requirements Definition (Product Manager)
   - User story map
   - Acceptance criteria (SMART)
   - Competitive analysis

📋 Phase 2: Architecture Design (Architect)
   - System architecture diagram
   - Technology selection
   - Deployment plan

📋 Phase 3: Test Strategy (Test Expert)
   - Test pyramid
   - Automation plan
   - Quality gates

📋 Phase 4: Development Plan (Solo Coder)
   - Task breakdown
   - Time estimation
   - Risk assessment
```

### Example 2: Feature Development / 示例 2: 功能开发

**Input**:
```
实现广告拦截核心模块
- 基于架构设计文档 v2.0
- 使用 SQLite 存储规则
- 需要完整单元测试
```

**Automatic Processing**:
```
🎯 Identified as: Solo Coder task
📊 Confidence: 0.85

✅ Context loaded: Architecture design document v2.0

📋 Development Process:
   1. Requirements understanding confirmation
   2. Technical solution design
   3. Code implementation
      - Core functionality
      - Error handling
      - Logging
   4. Unit tests
      - Coverage > 80%
      - Boundary conditions
      - Exception scenarios
   5. Self-testing verification
```

### Example 3: Architecture Review / 示例 3: 架构审查

**Input**:
```
审查当前系统架构
- 评估性能瓶颈
- 识别技术债务
- 提出优化建议
```

**Automatic Processing**:
```
🎯 Identified as: Architect task
📊 Confidence: 0.92

📋 Review Checklist:
   ✓ System boundary clarity
   ✓ Module responsibility singularity
   ✓ Interface definition completeness
   ✓ Exception handling coverage
   ✓ Performance bottleneck analysis
   ✓ Security risk assessment
   ✓ Expansion point reservation
   ✓ Monitoring plan

📋 Output:
   - Review report
   - Issue list
   - Optimization suggestions
   - Priority sorting
```

## 🏗️ Technical Architecture / 技术架构

### System Architecture / 系统架构

```
┌─────────────────────────────────────────┐
│         Trae Multi-Agent Skill          │
├─────────────────────────────────────────┤
│  User Interface Layer (Trae IDE)         │
│  - Natural language input                │
│  - Intelligent response output           │
├─────────────────────────────────────────┤
│  Dispatch Layer (Dispatcher)             │
│  - Task analysis                         │
│  - Role identification                   │
│  - Consensus organization                │
├─────────────────────────────────────────┤
│  Role Layer (Agent Roles)                │
│  - Architect                             │
│  - Product Manager                       │
│  - Test Expert                           │
│  - Solo Coder                            │
├─────────────────────────────────────────┤
│  Execution Layer (Executor)              │
│  - Task execution                        │
│  - Context management                    │
│  - Result verification                   │
└─────────────────────────────────────────┘
```

### Data Flow / 数据流

```
User Input
  ↓
Task Analysis (Keyword matching + Position weight)
  ↓
Role Identification (Confidence evaluation)
  ↓
Single role task → Direct dispatch
Multi role task → Organize consensus
  ↓
Task Execution (With complete Prompt)
  ↓
Result Verification (Checklist)
  ↓
Output Response
```

### Core Algorithms / 核心算法

#### 1. Role Recognition Algorithm / 角色识别算法

```python
def analyze_task(task: str) -> Tuple[str, float, List[str]]:
    """
    分析任务，识别需要的角色
    
    Algorithm:
    1. Keyword matching
    2. Position weight calculation
    3. Score accumulation
    4. Confidence evaluation
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

#### 2. Consensus Decision Algorithm / 共识决策算法

```python
def organize_consensus(task: str, agents: List[str]) -> Dict:
    """
    组织多角色共识
    
    Process:
    1. Determine lead role
    2. Collect opinions from each role
    3. Conflict detection
    4. Reach consensus
    5. Generate resolution
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

## 🤝 Contribution Guide / 贡献指南

### Development Environment Setup / 开发环境设置

```bash
# 1. Clone project
git clone https://github.com/your-org/trae-multi-agent.git
cd trae-multi-agent

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run tests
pytest tests/
```

### Submission Process / 提交流程

1. **Fork project**
2. **Create feature branch** (`git checkout -b feature/AmazingFeature`)
3. **Commit changes** (`git commit -m 'Add some AmazingFeature'`)
4. **Push to branch** (`git push origin feature/AmazingFeature`)
5. **Open Pull Request**

### Code Standards / 代码规范

- Follow PEP 8 standard
- Use type annotations
- Write unit tests
- Add Chinese comments

### Test Requirements / 测试要求

```bash
# Run all tests
pytest tests/ -v

# Test coverage
pytest tests/ --cov=src --cov-report=html

# Coverage requirements
# - Code coverage > 80%
# - Branch coverage > 70%
```

## ❓ FAQ / 常见问题

### Q1: Skill not working?

**A**: Check the following:
1. Skill files are in correct directory
2. File permissions are correct (readable)
3. Restart Trae application
4. Check if skill feature is enabled in Trae settings

### Q2: Role identification inaccurate?

**A**: Try:
1. Use more explicit task description
2. Use `--agent` parameter to manually specify role
3. Use `--consensus true` to organize multi-agent consensus

### Q3: Python3 not found?

**A**: Install Python3:
```bash
brew install python@3.11
```

### Q4: How to update skill?

**A**: Re-run installation script:
```bash
~/.trae/skills/install-global.sh
```

### Q5: How to customize role Prompt?

**A**: Edit role Prompt section in `SKILL.md` file, then restart Trae.

## 📄 License / 许可证

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

## 📞 Contact / 联系方式

- **Project Homepage**: https://github.com/weiransoft/TraeMultiAgentSkill.git
- **Issue Feedback**: https://github.com/weiransoft/TraeMultiAgentSkill.git/issues
- **Documentation**: https://weiransoft.github.io/TraeMultiAgentSkill/

## 🙏 Acknowledgments / 致谢

感谢所有贡献者和用户的支持！

---

**Made with ❤️ by Weiransoft**
