#!/bin/bash
# Phase 18 单元测试运行脚本
# 用途：运行所有 Phase 18 单元测试，验证 autonomous 模块的 142 个测试用例全部通过
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

echo -e "${YELLOW}==== Phase 18 单元测试 ====${NC}"
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

# Phase 18 单元测试文件列表（13 个文件，覆盖 142 个测试）
TEST_FILES=(
    "test_phase18_loop_controller"
    "test_phase18_git_driver"
    "test_phase18_notes_memory"
    "test_phase18_auto_skill_loader"
    "test_phase18_smart_confirmation"
    "test_phase18_sleep_guard"
    "test_phase18_run_state"
    "test_phase18_handlers"
    "test_phase18_dispatcher_adapter"
    "test_phase18_autonomous_plugin"
    "test_phase18_cli"
    "test_phase18_config"
    "test_phase18_integration"
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
    # 从 TESTS_DIR 切到 tests/ 目录，使用模块名调用 unittest
    if (cd "${TESTS_DIR}" && "$PYTHON_BIN" -m unittest "${test_file}" -v 2>&1 | tail -5); then
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
echo "通过: ${PASS_COUNT}"
echo "失败: ${FAIL_COUNT}"
if [ $FAIL_COUNT -gt 0 ]; then
    echo -e "${RED}失败文件:${NC}"
    for f in "${FAILED_FILES[@]}"; do
        echo "  - $f"
    done
    exit 1
fi
echo -e "${GREEN}✅ 全部 Phase 18 单元测试通过${NC}"
exit 0
