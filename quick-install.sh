#!/bin/bash
# Claude Code Multi-Agent Skill 快速安装脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_NAME="multi-agent-team"
TARGET_DIR="$HOME/.claude/skills/$SKILL_NAME"

echo "🚀 安装 Claude Code Multi-Agent Skill v2.7.0..."
echo ""

# 1. 创建目标目录
echo "📁 创建目录..."
mkdir -p "$TARGET_DIR"

# 2. 复制核心文件
echo "📋 复制核心文件..."
cp "$SCRIPT_DIR/SKILL.md" "$TARGET_DIR/"
cp "$SCRIPT_DIR/README.md" "$TARGET_DIR/"
cp "$SCRIPT_DIR/skills-index.json" "$TARGET_DIR/"
cp "$SCRIPT_DIR/claude-code-skill.json" "$TARGET_DIR/"

# 3. 复制必要的 Python 脚本
echo "📋 复制 Python 脚本..."
mkdir -p "$TARGET_DIR/scripts"

ESSENTIAL_SCRIPTS=(
    "trae_agent_dispatch_v2.py"
    "claude_code_subagent_adapter.py"
    "dual_layer_context_manager.py"
    "role_matcher.py"
    "workflow_engine_v2.py"
    "skill_registry.py"
    "task_completion_checker.py"
    "task_list_manager.py"
    "checkpoint_manager.py"
    "ai_assistant.py"
    "ai_semantic_matcher.py"
    "agent_loop_controller_v2.py"
    "cybernetics_integration.py"
    "cybernetics_bridge.py"
    "strategy_resolver.py"
    "feedback_control_loop.py"
    "guard_coordinator.py"
    "hierarchical_control.py"
    "performance_fingerprint.py"
    "karpathy_principle_enforcer.py"
    "context_fingerprint_integration.py"
    "project_understanding.py"
    "multi_role_code_walkthrough.py"
    "multi_role_collaborative_analyzer.py"
    "spec_tools.py"
    "code_map_generator_v2.py"
    "ai_initializer.py"
    "update_docs.py"
)

for script in "${ESSENTIAL_SCRIPTS[@]}"; do
    if [ -f "$SCRIPT_DIR/scripts/$script" ]; then
        cp "$SCRIPT_DIR/scripts/$script" "$TARGET_DIR/scripts/"
        echo "  ✓ $script"
    fi
done

# 4. 复制 docs 目录
echo "📋 复制文档..."
cp -r "$SCRIPT_DIR/docs" "$TARGET_DIR/" 2>/dev/null || echo "  ⚠️ 跳过 docs"

# 5. 设置权限
echo "🔐 设置权限..."
chmod +x "$TARGET_DIR/scripts"/*.py

# 6. 验证安装
echo ""
echo "✅ 验证安装..."
if [ -f "$TARGET_DIR/SKILL.md" ] && [ -f "$TARGET_DIR/scripts/claude_code_subagent_adapter.py" ]; then
    echo "✅ 安装成功!"
else
    echo "❌ 安装失败"
    exit 1
fi

# 7. 显示使用说明
cat << EOF

✅ 技能已安装到：$TARGET_DIR

📖 使用方法:
1. 自动调用:
   claude "设计系统架构"
   claude "定义产品需求"
   claude "制定测试策略"

2. 手动调用:
   python3 $TARGET_DIR/scripts/claude_code_subagent_adapter.py architect "设计系统架构"

🎭 可用角色:
   - architect       : 架构师
   - product-manager : 产品经理
   - tester          : 测试专家
   - solo-coder      : 独立开发者
   - ui-designer     : UI 设计师

📚 详细文档：$TARGET_DIR/docs/guides/CLAUDE_CODE_SUBAGENT_GUIDE.md

EOF
