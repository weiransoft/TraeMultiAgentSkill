#!/bin/bash
# Phase 19 Ponytail 测试套件运行脚本
# 用途：运行所有 Ponytail 相关测试（Phase 19.1~19.4），验证决策梯注入、
#       红线检测、债务台账、需求追踪、回归兼容性全部通过
# 退出码：0=成功，1=失败

set -e
set -o pipefail

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 项目根目录（trae-multi-agent）
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
# 脚本目录（trae-multi-agent/scripts）
SCRIPTS_DIR="${PROJECT_ROOT}/scripts"
# 测试目录
TESTS_DIR="${SCRIPTS_DIR}/tests"

echo -e "${YELLOW}==== Phase 19 Ponytail 测试套件 ====${NC}"
echo "项目根: ${PROJECT_ROOT}"
echo "脚本目录: ${SCRIPTS_DIR}"
echo "测试目录: ${TESTS_DIR}"
echo ""

# 检查 Python
PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" &> /dev/null; then
    echo -e "${RED}❌ $PYTHON_BIN 未找到${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Python: $($PYTHON_BIN --version)${NC}"

# 设置 PYTHONPATH
export PYTHONPATH="${SCRIPTS_DIR}:${PYTHONPATH}"

# Ponytail 测试文件列表（10 个文件，覆盖 98 个测试用例）
# Phase 19.1: 规则集引擎 + 模式追踪
# Phase 19.2: 注入点改造 + 债务台账
# Phase 19.3: 红线检测 + 需求追踪 + enforcer 扩展
# Phase 19.4: 集成测试 + 回归测试 + 适配器测试
TEST_FILES=(
    # Phase 19.1: 规则集引擎 + 模式追踪（33 个测试）
    "test_ponytail_ruleset"
    "test_ponytail_mode_tracker"
    # Phase 19.2: 债务台账（10 个测试）
    "test_ponytail_debt_collector"
    # Phase 19.3: 红线检测 + enforcer 扩展（18 个测试）
    "test_ponytail_redline"
    "test_ponytail_enforcer_extension"
    "test_ponytail_ultra_guard"
    # Phase 19.4: 集成测试 + 回归测试 + 适配器测试（37 个测试）
    "test_ponytail_integration"
    "test_ponytail_regression_phase18"
    "test_ponytail_regression_v4_legacy"
    "test_claude_code_subagent_adapter_prompt"
)

PASS_COUNT=0
FAIL_COUNT=0
FAILED_FILES=()

for test_file in "${TEST_FILES[@]}"; do
    test_path="${TESTS_DIR}/${test_file}.py"
    if [ ! -f "$test_path" ]; then
        echo -e "${YELLOW}⚠ 测试文件不存在: ${test_file}.py (跳过)${NC}"
        continue
    fi
    echo -e "${YELLOW}▶ 跑 ${test_file} ...${NC}"
    # 从 TESTS_DIR 切到 tests/ 目录，使用 pytest 调用（支持更丰富的断言输出）
    if (cd "${TESTS_DIR}" && "$PYTHON_BIN" -m pytest "${test_file}.py" -v --tb=short 2>&1 | tail -10); then
        echo -e "${GREEN}✓ ${test_file} 通过${NC}"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        echo -e "${RED}✗ ${test_file} 失败${NC}"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        FAILED_FILES+=("$test_file")
    fi
    echo ""
done

echo -e "${YELLOW}==== 测试总结 ====${NC}"
echo "通过文件数: ${PASS_COUNT}/${#TEST_FILES[@]}"
echo "失败文件数: ${FAIL_COUNT}"
if [ $FAIL_COUNT -gt 0 ]; then
    echo -e "${RED}失败文件:${NC}"
    for f in "${FAILED_FILES[@]}"; do
        echo "  - $f"
    done
    exit 1
fi
echo -e "${GREEN}✅ 全部 Ponytail 测试通过（${PASS_COUNT} 个文件）${NC}"

# 额外：运行全量 ponytail 测试统计总数
echo ""
echo -e "${YELLOW}==== 全量统计 ====${NC}"
TOTAL_TESTS=$("$PYTHON_BIN" -m pytest "${TESTS_DIR}"/test_ponytail_*.py "${TESTS_DIR}"/test_claude_code_subagent_adapter_prompt.py --co -q 2>/dev/null | tail -1)
echo "总测试用例数: ${TOTAL_TESTS}"

exit 0
