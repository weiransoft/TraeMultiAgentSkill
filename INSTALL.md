# Trae Multi-Agent Skill 安装指南

> **版本**: v2.6 | **更新日期**: 2026-06-17

## 快速安装

### 方法 1：设置环境变量（推荐）

将以下行添加到你的 shell 配置文件（`~/.zshrc` 或 `~/.bashrc`）:

```bash
export TRAE_MULTI_AGENT_SKILL_PATH="$HOME/claw/.trae/skills/trae-multi-agent"
```

然后重新加载配置：

```bash
source ~/.zshrc
```

### 方法 2：创建符号链接（推荐）

```bash
# 创建全局可执行的符号链接
ln -s /Users/wangwei/claw/.trae/skills/trae-multi-agent/scripts/trae_agent.py /usr/local/bin/trae-agent

# 或者使用 brew link 方式（如果有 brew）
brew link --force trae-multi-agent
```

### 方法 3：使用包装脚本

在任何项目中，直接使用包装脚本的绝对路径：

```bash
python3 /Users/wangwei/claw/.trae/skills/trae-multi-agent/scripts/trae_agent.py \
  --task "你的任务描述" \
  --agent architect
```

### 方法 4：安装到全局 skill 目录

```bash
# 使用 rsync 同步到全局 skill 目录（推荐，避免沙箱限制）
rsync -av --exclude='__pycache__' --exclude='.git' --exclude='*.pyc' \
  --exclude='.trae' --exclude='context' --exclude='logs' --exclude='fingerprints' \
  /Users/wangwei/claw/.trae/skills/trae-multi-agent/ \
  /Users/wangwei/.trae/skills/trae-multi-agent/

# 或者使用安装脚本（需要 sudo 权限）
cd /Users/wangwei/claw/.trae/skills/trae-multi-agent && bash install.sh
```

## 使用方法

安装完成后，可以在任何项目目录下直接使用：

```bash
# 调用架构师
trae-agent --task "设计系统架构" --agent architect

# 调用产品经理
trae-agent --task "分析需求" --agent product_manager

# 调用测试专家
trae-agent --task "制定测试策略" --agent test_expert

# 调用独立开发者
trae-agent --task "实现功能" --agent solo_coder

# 调用 UI 设计师
trae-agent --task "设计登录页面" --agent ui_designer
```

## 命令行参数

### 基础参数

- `--task`: 任务描述（必需）
- `--agent`: 智能体角色（可选，默认：auto）
  - `architect` - 架构师
  - `product_manager` - 产品经理
  - `test_expert` - 测试专家
  - `solo_coder` - 独立开发者
  - `ui_designer` - UI 设计师
- `--project-root`: 项目根目录（可选，默认：当前目录）
- `--task-file`: 任务文件路径（可选）
- `--output`: 输出文件路径（可选）
- `--verbose`: 启用详细输出模式
- `--dry-run`: 仅模拟执行，不实际调用智能体

### Autonomous Mode 参数（v2.6 / Phase 18 新增）

- `--auto-mode`: 启用自主编排模式
- `--auto-goal`: 自主模式目标描述
- `--auto-max-iterations`: 最大迭代次数（默认：10）
- `--auto-confirmation`: 确认策略（smart/whitelist-only/blacklist-only）
- `--auto-git-enabled`: 启用 Git 自动提交
- `--auto-skill-injection`: 启用自动 skill 注入
- `--auto-notes-memory`: 启用 Notes 跨轮记忆
- `--auto-sleep-guard`: 启用防休眠守护
- `--auto-resume`: 启用断点续跑
- `--auto-ponytail-mode`: Ponytail 决策梯模式（lite/full/ultra/off）

详细 flag 列表见 `docs/guides/AUTONOMOUS_MODE_GUIDE.md`。

### Ponytail 决策梯命令（v2.6 新增）

在对话中使用 `/ponytail` 命令切换模式：

```bash
/ponytail ultra    # 切换到 ULTRA 模式（YAGNI 极端主义）
/ponytail full     # 切换到 FULL 模式（默认）
/ponytail lite     # 切换到 LITE 模式（精简）
/ponytail off      # 关闭决策梯注入
/ponytail          # 查看当前模式
```

详细指南见 `docs/guides/PONYTAIL_GUIDE.md`。

## 验证安装

```bash
# 检查是否能找到 skill
trae-agent --help

# 测试调用
trae-agent --task "测试" --agent architect --dry-run

# 验证 Ponytail 决策梯（v2.6）
PYTHONPATH=scripts python3 -c "from ponytail.ruleset import PonytailRulesetEngine; e=PonytailRulesetEngine(); print('Ponytail v2.6 OK')"

# 运行全部测试（647+ 个测试用例）
cd /Users/wangwei/claw/.trae/skills/trae-multi-agent
PYTHONPATH=scripts python3 -m pytest scripts/tests/ --tb=short
```

## 故障排查

### 找不到 skill

如果提示 "找不到 trae-multi-agent skill"，请检查：

1. 环境变量是否正确设置：
   ```bash
   echo $TRAE_MULTI_AGENT_SKILL_PATH
   ```

2. skill 路径是否存在：
   ```bash
   ls -la $TRAE_MULTI_AGENT_SKILL_PATH
   ```

3. 重新加载 shell 配置：
   ```bash
   source ~/.zshrc
   ```

### 权限问题

如果遇到权限错误，确保脚本有执行权限：

```bash
chmod +x /Users/wangwei/claw/.trae/skills/trae-multi-agent/scripts/trae_agent.py
chmod +x /Users/wangwei/claw/.trae/skills/trae-multi-agent/scripts/trae_agent_dispatch.py
chmod +x /Users/wangwei/claw/.trae/skills/trae-multi-agent/scripts/trae_agent_dispatch_v2.py
```

## v2.6 新功能

### Ponytail 决策梯（Phase 19）

6 步决策梯（YAGNI→标准库→平台原生→复用现有→一行优先→最小可行）+ 16 条不可简化红线 + 三种强度模式 + 债务台账 + 需求追踪。

```bash
# 运行 Ponytail 测试（10 个文件，98 个测试用例）
bash scripts/tests/scripts/run_ponytail_tests.sh
```

详细指南见 `docs/guides/PONYTAIL_GUIDE.md`。

### Autonomous Mode（Phase 18）

Ralph 风格自主编排，4 阶段循环（plan→dev→verify→fix）+ 9 核心组件 + 17 CLI flag。

```bash
# 启动自主模式
python3 scripts/trae_agent_dispatch.py \
    --auto-mode \
    --auto-goal "实现用户登录功能" \
    --auto-max-iterations 10
```

详细指南见 `docs/guides/AUTONOMOUS_MODE_GUIDE.md`。

### 插件热加载（Phase 17）

V3 插件架构，3 种加载路径（Drop-in 目录 / Hot Register API / HotReloadWatcher）。

详细设计见 `docs/dev/PHASE17_PLAN.md`。

### Cybernetics 工程控制论（v2.5）

三环控制模型（战略/战术/执行）+ 反馈控制环 + 性能画像 + 守护协调器。

详细分析见 `docs/dev/CYBERNETICS_ANALYSIS.md`。

### Dynamic Workflows（v1.7）

6 大动态工作流模式（classifier-dispatch / fan-out-aggregate / adversarial-verify / generate-filter / tournament / loop-until-done）。

详细方案见 `docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md`。

## 卸载

```bash
# 如果创建了符号链接
rm /usr/local/bin/trae-agent

# 删除环境变量（从 ~/.zshrc 或 ~/.bashrc 中移除）
unset TRAE_MULTI_AGENT_SKILL_PATH

# 如果使用了全局 skill 目录同步，删除同步的目录
rm -rf /Users/wangwei/.trae/skills/trae-multi-agent
```
