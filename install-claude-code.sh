#!/bin/bash
# Claude Code Multi-Agent Skill 安装脚本
# 用于将 skill 安装到 Claude Code 全局 skill 目录

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_NAME="multi-agent-team"

# 默认安装路径
DEFAULT_CLAUDE_SKILL_DIR="$HOME/.claude/skills"
TARGET_SKILL_DIR="$DEFAULT_CLAUDE_SKILL_DIR/$SKILL_NAME"

echo -e "${BLUE}╔════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   Claude Code Multi-Agent Skill 安装脚本              ║${NC}"
echo -e "${BLUE}║   版本：v2.7.0 (支持 SubAgent 调用)                    ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════╝${NC}"
echo ""

# 检查 Python 是否安装
check_python() {
    if ! command -v python3 &> /dev/null; then
        echo -e "${RED}❌ 错误：未找到 Python 3${NC}"
        echo "请先安装 Python 3.8 或更高版本"
        exit 1
    fi
    
    PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
    echo -e "${GREEN}✅ Python 版本：$PYTHON_VERSION${NC}"
}

# 检查 Claude Code 是否安装
check_claude() {
    if command -v claude &> /dev/null; then
        CLAUDE_VERSION=$(claude --version 2>&1 || echo "unknown")
        echo -e "${GREEN}✅ Claude Code 已安装：$CLAUDE_VERSION${NC}"
    else
        echo -e "${YELLOW}⚠️  警告：未找到 claude 命令${NC}"
        echo "技能仍然可以安装，但可能无法通过 claude 命令直接调用"
    fi
}

# 创建目标目录
create_directories() {
    echo -e "\n${BLUE}📁 创建技能目录...${NC}"
    
    if [ ! -d "$DEFAULT_CLAUDE_SKILL_DIR" ]; then
        echo "创建目录：$DEFAULT_CLAUDE_SKILL_DIR"
        mkdir -p "$DEFAULT_CLAUDE_SKILL_DIR"
    fi
    
    if [ -d "$TARGET_SKILL_DIR" ]; then
        echo -e "${YELLOW}⚠️  技能目录已存在，将覆盖安装${NC}"
        rm -rf "$TARGET_SKILL_DIR"
    fi
    
    mkdir -p "$TARGET_SKILL_DIR"
    echo -e "${GREEN}✅ 技能目录已创建：$TARGET_SKILL_DIR${NC}"
}

# 复制文件
copy_files() {
    echo -e "\n${BLUE}📋 复制技能文件...${NC}"
    
    # 核心文件
    CORE_FILES=(
        "SKILL.md"
        "README.md"
        "skills-index.json"
        "claude-code-skill.json"
    )
    
    for file in "${CORE_FILES[@]}"; do
        if [ -f "$SCRIPT_DIR/$file" ]; then
            cp "$SCRIPT_DIR/$file" "$TARGET_SKILL_DIR/"
            echo -e "${GREEN}  ✓${NC} $file"
        else
            echo -e "${YELLOW}  ⚠️  $file (不存在，跳过)${NC}"
        fi
    done
    
    # 复制 scripts 目录（排除 __pycache__ 和递归问题）
    if [ -d "$SCRIPT_DIR/scripts" ]; then
        echo -e "\n${BLUE}📋 复制 scripts 目录...${NC}"
        
        # 创建目标 scripts 目录
        mkdir -p "$TARGET_SKILL_DIR/scripts"
        
        # 复制所有 .py 文件
        find "$SCRIPT_DIR/scripts" -maxdepth 1 -name "*.py" -type f -exec cp {} "$TARGET_SKILL_DIR/scripts/" \;
        
        # 复制子目录（排除 __pycache__）
        for dir in "$SCRIPT_DIR/scripts"/*/; do
            if [ -d "$dir" ] && [[ ! "$(basename "$dir")" =~ __pycache__ ]]; then
                cp -r "$dir" "$TARGET_SKILL_DIR/scripts/"
            fi
        done
        
        # 设置执行权限
        chmod +x "$TARGET_SKILL_DIR/scripts"/*.py 2>/dev/null || true
        echo -e "${GREEN}✅ Scripts 目录已复制，权限已设置${NC}"
    fi
    
    # 复制 docs 目录（可选）
    if [ -d "$SCRIPT_DIR/docs" ]; then
        echo -e "\n${BLUE}📋 复制 docs 目录...${NC}"
        # 排除 .git 和其他不需要的文件
        rsync -av --exclude='.git' --exclude='*.svg' "$SCRIPT_DIR/docs/" "$TARGET_SKILL_DIR/docs/" 2>/dev/null || \
        cp -r "$SCRIPT_DIR/docs" "$TARGET_SKILL_DIR/"
        echo -e "${GREEN}✅ Docs 目录已复制${NC}"
    fi
    
    # 复制 requirements.txt（如果存在）
    if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
        cp "$SCRIPT_DIR/requirements.txt" "$TARGET_SKILL_DIR/"
        echo -e "${GREEN}  ✓ requirements.txt${NC}"
    fi
    
    # 安装 Claude Code SubAgent 定义文件到全局 agents 目录（v2.7.0 新增）
    # 让 Claude Code 宿主可用真实 Task 子代理执行 5 个角色，替代脚本模拟
    if [ -d "$SCRIPT_DIR/.claude/agents" ]; then
        echo -e "\n${BLUE}🎭 安装 SubAgent 定义（5 角色）...${NC}"
        GLOBAL_AGENTS_DIR="$HOME/.claude/agents"
        mkdir -p "$GLOBAL_AGENTS_DIR"
        AGENT_COUNT=0
        for agent_file in "$SCRIPT_DIR/.claude/agents"/*.md; do
            if [ -f "$agent_file" ]; then
                cp "$agent_file" "$GLOBAL_AGENTS_DIR/"
                echo -e "${GREEN}  ✓${NC} $(basename "$agent_file")"
                AGENT_COUNT=$((AGENT_COUNT + 1))
            fi
        done
        echo -e "${GREEN}✅ 已安装 $AGENT_COUNT 个 SubAgent 定义到 $GLOBAL_AGENTS_DIR${NC}"
    fi
}

# 安装 Python 依赖
install_dependencies() {
    if [ -f "$TARGET_SKILL_DIR/requirements.txt" ]; then
        echo -e "\n${BLUE}📦 安装 Python 依赖...${NC}"
        cd "$TARGET_SKILL_DIR"
        python3 -m pip install -r requirements.txt
        echo -e "${GREEN}✅ 依赖安装完成${NC}"
    else
        echo -e "\n${BLUE}📦 未发现 requirements.txt，跳过依赖安装${NC}"
    fi
}

# 验证安装
verify_installation() {
    echo -e "\n${BLUE}🔍 验证安装...${NC}"
    
    # 检查关键文件
    REQUIRED_FILES=(
        "$TARGET_SKILL_DIR/SKILL.md"
        "$TARGET_SKILL_DIR/scripts/trae_agent_dispatch_v2.py"
        "$TARGET_SKILL_DIR/scripts/claude_code_subagent_adapter.py"
    )
    
    ALL_OK=true
    for file in "${REQUIRED_FILES[@]}"; do
        if [ -f "$file" ]; then
            echo -e "${GREEN}  ✓${NC} $(basename $file)"
        else
            echo -e "${RED}  ✗${NC} $(basename $file) (缺失)"
            ALL_OK=false
        fi
    done
    
    if [ "$ALL_OK" = true ]; then
        echo -e "\n${GREEN}✅ 安装验证通过${NC}"
        return 0
    else
        echo -e "\n${RED}❌ 安装验证失败${NC}"
        return 1
    fi
}

# 显示使用说明
show_usage() {
    echo -e "\n${BLUE}╔════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║              安装完成！使用指南                         ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════╝${NC}"
    
    cat << EOF

${GREEN}✅ 技能已成功安装到：${NC}
   $TARGET_SKILL_DIR

${BLUE}📖 使用方法：${NC}

1. **自动调用** (推荐)
   在 Claude Code 中直接使用自然语言描述任务：
   
   ${YELLOW}claude "设计系统架构：包括模块划分、技术选型"${NC}
   ${YELLOW}claude "定义产品需求：广告拦截功能"${NC}
   ${YELLOW}claude "制定测试策略：覆盖正常、异常场景"${NC}
   ${YELLOW}claude "实现用户登录功能：完整代码，包含单元测试"${NC}

2. **指定角色调用**
   使用 --agent 参数指定角色：
   
   ${YELLOW}python3 $TARGET_SKILL_DIR/scripts/trae_agent_dispatch_v2.py \\${NC}
     --task "设计系统架构" \\
     --agent architect

3. **使用 SubAgent 适配器**
   直接调用 SubAgent：
   
   ${YELLOW}python3 $TARGET_SKILL_DIR/scripts/claude_code_subagent_adapter.py \\${NC}
     architect "设计系统架构"

${BLUE}🎭 可用角色：${NC}
   - architect      : 架构师（系统架构设计、技术选型）
   - product-manager: 产品经理（需求分析、PRD 编写）
   - tester         : 测试专家（测试策略、质量保障）
   - solo-coder     : 独立开发者（功能开发、代码实现）
   - ui-designer    : UI 设计师（界面设计、UI/UX）

${BLUE}📚 核心原则（Karpathy 四大原则）：${NC}
   1. Think Before Coding    - 三思而后行
   2. Simplicity First       - 简单优先
   3. Surgical Changes       - 精准修改
   4. Goal-Driven Execution  - 目标驱动

${BLUE}📖 详细文档：${NC}
   - 主文档：$TARGET_SKILL_DIR/README.md
   - 使用指南：$TARGET_SKILL_DIR/docs/guides/CLAUDE_CODE_SUBAGENT_GUIDE.md
   - 示例：$TARGET_SKILL_DIR/EXAMPLES.md

${YELLOW}⚠️  注意事项：${NC}
   - 确保 Python 3.8+ 已安装
   - 确保 claude 命令可用（可选，用于自动调用）
   - 如遇权限问题，请运行：chmod +x $TARGET_SKILL_DIR/scripts/*.py

${GREEN}🎉 安装完成！现在可以在 Claude Code 中使用 Multi-Agent Skill 了！${NC}

EOF
}

# 主函数
main() {
    echo -e "${BLUE}开始安装 Claude Code Multi-Agent Skill...${NC}"
    echo ""
    
    # 1. 检查环境
    check_python
    check_claude
    
    # 2. 创建目录
    create_directories
    
    # 3. 复制文件
    copy_files
    
    # 4. 安装依赖
    install_dependencies
    
    # 5. 验证安装
    if verify_installation; then
        # 6. 显示使用说明
        show_usage
        exit 0
    else
        echo -e "\n${RED}❌ 安装失败，请检查错误信息${NC}"
        exit 1
    fi
}

# 运行主函数
main "$@"
