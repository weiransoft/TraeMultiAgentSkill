# Trae Multi-Agent Skill 配置指南

## 问题说明

当你尝试运行 `python3 scripts/trae_agent_dispatch.py` 时出现以下错误：

```
can't open file '/Users/xxx/your-project/scripts/trae_agent_dispatch.py': [Errno 2] No such file or directory
```

这是因为 `trae_agent_dispatch.py` 脚本位于 skill 目录中，而不是你的项目目录中。

## 解决方案

### 方案 1：使用包装脚本（推荐）

1. **复制包装脚本到你的项目**

   ```bash
   cp ~/.trae/skills/trae-multi-agent/scripts/trae_agent_dispatch_wrapper.sh /your/project/path/
   ```

2. **编辑包装脚本**

   打开 `trae_agent_dispatch_wrapper.sh`，修改 `SKILL_DIR` 为你的 skill 实际路径：

   ```bash
   SKILL_DIR="/Users/your-username/.trae/skills/trae-multi-agent"
   ```

3. **添加执行权限**

   ```bash
   chmod +x trae_agent_dispatch_wrapper.sh
   ```

4. **使用包装脚本调用 skill**

   ```bash
   ./trae_agent_dispatch_wrapper.sh --task "美化登录界面，创建现代化的登录页面" --agent auto
   ```

### 方案 2：使用绝对路径

直接调用 skill 脚本：

```bash
python3 ~/.trae/skills/trae-multi-agent/scripts/trae_agent_dispatch.py \
  --task "美化登录界面，创建现代化的登录页面" \
  --agent auto
```

### 方案 3：创建别名（推荐用于频繁使用）

在你的 `~/.zshrc` 或 `~/.bashrc` 中添加：

```bash
alias trae-agent='python3 ~/.trae/skills/trae-multi-agent/scripts/trae_agent_dispatch.py'
```

然后重新加载配置：

```bash
source ~/.zshrc
```

之后可以在任何项目中使用：

```bash
trae-agent --task "美化登录界面" --agent auto
```

## 使用方法

### 基本用法

```bash
# 自动选择角色
./trae_agent_dispatch.sh --task "实现用户登录功能" --agent auto

# 指定角色
./trae_agent_dispatch.sh --task "设计系统架构" --agent architect
./trae_agent_dispatch.sh --task "编写 PRD" --agent product-manager
./trae_agent_dispatch.sh --task "编写测试用例" --agent tester
./trae_agent_dispatch.sh --task "实现功能代码" --agent solo-coder
```

### 可用参数

- `--task`: 任务描述（必需）
- `--agent`: 指定角色（可选，默认：auto）
  - `architect`: 架构师
  - `product-manager`: 产品经理
  - `tester`: 测试专家
  - `solo-coder`: 独立开发者
  - `ui-designer`: UI 设计师
  - `devops`: DevOps 工程师
  - `auto`: 自动匹配（默认）
- `--project-root`: 项目根目录（可选，默认：当前目录）
- `--verbose`: 启用详细输出模式
- `--dry-run`: 仅模拟执行，不实际调用智能体

### 示例

```bash
# 自动选择角色实现功能
./trae_agent_dispatch.sh --task "实现用户注册和登录功能" --agent auto

# 指定架构师设计架构
./trae_agent_dispatch.sh --task "设计微服务架构，使用 Spring Boot 和 Docker" --agent architect

# 指定产品经理编写 PRD
./trae_agent_dispatch.sh --task "为电商系统编写产品需求文档" --agent product-manager

# 指定测试专家编写测试
./trae_agent_dispatch.sh --task "为用户服务编写单元测试和集成测试" --agent tester

# 指定独立开发者编写代码
./trae_agent_dispatch.sh --task "实现 RESTful API 端点" --agent solo-coder

# 指定 UI 设计师设计界面
./trae_agent_dispatch.sh --task "设计现代化的登录页面" --agent ui-designer

# 指定 DevOps 工程师配置 CI/CD
./trae_agent_dispatch.sh --task "配置 GitHub Actions 自动部署" --agent devops

# 启用详细模式
./trae_agent_dispatch.sh --task "实现功能" --verbose
```

## 故障排除

### 问题 1：找不到脚本

**错误**: `can't open file '.../trae_agent_dispatch.py': [Errno 2] No such file or directory`

**解决**: 确保 `SKILL_DIR` 配置正确，指向 skill 的实际路径。

### 问题 2：权限错误

**错误**: `Permission denied`

**解决**: 
```bash
chmod +x trae_agent_dispatch_wrapper.sh
```

### 问题 3：Python 模块导入错误

**错误**: `ModuleNotFoundError: No module named 'xxx'`

**解决**: 确保已更新 `trae_agent_dispatch.py`，它会自动将 skill 目录添加到 Python 路径。

## 技术说明

### 路径处理机制

`trae_agent_dispatch.py` 脚本会自动：

1. 获取脚本所在的绝对路径
2. 将该路径添加到 Python 的 `sys.path` 中
3. 导入 skill 的其他模块

这样确保无论从哪个目录调用，都能正确找到 skill 的模块。

### 核心组件

- `trae_agent_dispatch.py`: 主调度脚本（包装器）
- `trae_agent_dispatch_v2.py`: v2.0 实现（包含所有新功能）
- `dual_layer_context_manager.py`: 双层上下文管理器
- `skill_registry.py`: 技能注册和发现
- `role_matcher.py`: AI 增强的角色匹配器
- `workflow_engine_v2.py`: 工作流编排引擎（v2 增强版）

## 更新日志

### v2.1.0
- ✅ 修复路径问题，支持从任何目录调用
- ✅ 添加包装脚本，简化配置
- ✅ 改进错误处理和诊断信息

### v2.0.0
- ✅ 双层动态上下文管理
- ✅ AI 语义匹配
- ✅ 工作流编排引擎
- ✅ 技能注册和发现

## 支持

如有问题，请查看：
- [技能文档](../SKILL.md)
- [发布总结](../docs/RELEASE_SUMMARY.md)
- [特性介绍](../docs/v2.1_features.svg)
