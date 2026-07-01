#!/bin/bash
# Loop Engineering Phase 5-6 回归测试脚本
# 用途：运行 Loop Engineering 全部单元/集成/E2E 测试、V3 插件契约回归、代码质量门禁
# 退出码：0=成功，1=失败

set -e
set -o pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
SCRIPTS_DIR="${PROJECT_ROOT}/scripts"
TESTS_DIR="${SCRIPTS_DIR}/tests"

# Loop Engineering 相关 Python 文件（用于 ruff / mypy）
LOOP_FILES=(
    "${SCRIPTS_DIR}/loop_engineering"
    "${SCRIPTS_DIR}/plugins/loop_engineering.py"
    "${SCRIPTS_DIR}/plugins/autonomous.py"
    "${SCRIPTS_DIR}/plugins/loop.py"
    "${SCRIPTS_DIR}/plugins/cancel.py"
    "${SCRIPTS_DIR}/plugins/graph.py"
    "${SCRIPTS_DIR}/plugins/resume.py"
    "${SCRIPTS_DIR}/plugins/multi_goal.py"
    "${SCRIPTS_DIR}/cli/parser.py"
    "${SCRIPTS_DIR}/facade.py"
)

# Loop Engineering 测试模块
TEST_MODULES=(
    "tests.test_loop_engineering_models"
    "tests.test_loop_engineering_config"
    "tests.test_loop_engineering_scheduler"
    "tests.test_loop_engineering_memory"
    "tests.test_loop_engineering_kernel"
    "tests.test_loop_engineering_templates"
    "tests.test_loop_engineering_handoff"
    "tests.test_loop_engineering_dispatch_adapter"
    "tests.test_loop_engineering_plugin"
    "tests.test_loop_engineering_integration"
    "tests.test_loop_engineering_e2e_design"
    "tests.test_loop_engineering_e2e_coding"
    "tests.test_loop_engineering_e2e_testing"
    "tests.test_v3_plugin_contract"
)

echo -e "${BLUE}##########################################${NC}"
echo -e "${BLUE}# Loop Engineering Phase 5-6 回归测试   #${NC}"
echo -e "${BLUE}##########################################${NC}"
echo ""

PASS_COUNT=0
FAIL_COUNT=0
FAILED_STEPS=()

# ---------------------------------------------------------------------- #
# 1. 单元/集成/E2E 测试                                                  #
# ---------------------------------------------------------------------- #
echo -e "${YELLOW}==== [1/3] 单元/集成/E2E 测试 ====${NC}"

export PYTHONPATH="${SCRIPTS_DIR}:${PYTHONPATH}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

for module in "${TEST_MODULES[@]}"; do
    echo -e "${YELLOW}▶ 运行 ${module} ...${NC}"
    if (cd "${SCRIPTS_DIR}" && "$PYTHON_BIN" -m unittest "${module}" -v 2>&1 | tail -5); then
        echo -e "${GREEN}✓ ${module} 通过${NC}"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        echo -e "${RED}✗ ${module} 失败${NC}"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        FAILED_STEPS+=("${module}")
    fi
    echo ""
done

# ---------------------------------------------------------------------- #
# 2. ruff 代码质量门禁                                                   #
# ---------------------------------------------------------------------- #
echo -e "${YELLOW}==== [2/3] ruff 代码质量门禁 ====${NC}"

RUFF_OK=true

if command -v ruff &> /dev/null; then
    echo -e "${YELLOW}▶ ruff check ...${NC}"
    if ruff check "${LOOP_FILES[@]}" 2>&1; then
        echo -e "${GREEN}✓ ruff check 通过${NC}"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        echo -e "${RED}✗ ruff check 失败${NC}"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        FAILED_STEPS+=("ruff check")
        RUFF_OK=false
    fi
    echo ""

    echo -e "${YELLOW}▶ ruff format --check ...${NC}"
    if ruff format --check "${LOOP_FILES[@]}" 2>&1; then
        echo -e "${GREEN}✓ ruff format 检查通过${NC}"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        echo -e "${RED}✗ ruff format 检查失败（可运行 'ruff format ${LOOP_FILES[*]}' 自动修复）${NC}"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        FAILED_STEPS+=("ruff format --check")
        RUFF_OK=false
    fi
else
    echo -e "${YELLOW}⚠ ruff 未安装，跳过 ruff 门禁${NC}"
fi
echo ""

# ---------------------------------------------------------------------- #
# 3. mypy 类型检查（如可用）                                             #
# ---------------------------------------------------------------------- #
echo -e "${YELLOW}==== [3/3] mypy 类型检查 ====${NC}"

if command -v mypy &> /dev/null; then
    echo -e "${YELLOW}▶ mypy --strict scripts/loop_engineering ...${NC}"
    # mypy 会跟随导入检查依赖模块，但 Loop Engineering 验证范围只关心
    # scripts/loop_engineering/ 下的文件；依赖模块的既有类型错误不纳入本次门禁。
    MYPY_OUTPUT=$(cd "${PROJECT_ROOT}" && mypy --strict "${SCRIPTS_DIR}/loop_engineering" 2>&1)
    LOOP_ERRORS=$(echo "${MYPY_OUTPUT}" | grep -E "^scripts/loop_engineering/" || true)
    if [ -z "${LOOP_ERRORS}" ]; then
        echo -e "${GREEN}✓ mypy 通过（Loop Engineering 范围内无类型错误）${NC}"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        echo -e "${RED}✗ mypy 失败（Loop Engineering 范围内发现错误）${NC}"
        echo "${LOOP_ERRORS}" | tail -20
        FAIL_COUNT=$((FAIL_COUNT + 1))
        FAILED_STEPS+=("mypy --strict loop_engineering")
    fi
else
    echo -e "${YELLOW}⚠ mypy 未安装，跳过类型检查${NC}"
fi
echo ""

# ---------------------------------------------------------------------- #
# 总结                                                                   #
# ---------------------------------------------------------------------- #
echo -e "${BLUE}##########################################${NC}"
echo -e "${BLUE}# 测试总结                              #${NC}"
echo -e "${BLUE}##########################################${NC}"
echo "通过: ${PASS_COUNT}"
echo "失败: ${FAIL_COUNT}"

if [ ${FAIL_COUNT} -gt 0 ]; then
    echo -e "${RED}失败步骤:${NC}"
    for step in "${FAILED_STEPS[@]}"; do
        echo "  - ${step}"
    done
    echo ""
    echo -e "${RED}❌ Loop Engineering Phase 5-6 回归测试失败${NC}"
    exit 1
fi

echo -e "${GREEN}🎉 Loop Engineering Phase 5-6 回归测试全部通过${NC}"
exit 0
