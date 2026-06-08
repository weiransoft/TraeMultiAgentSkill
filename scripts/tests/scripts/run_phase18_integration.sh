#!/bin/bash
# Phase 18 集成测试运行脚本
# 用途：跑 Phase 18 集成测试，验证 4 阶段流程、git 操作、notes 累积等真实场景
# 退出码：0=成功，1=失败

set -e
set -o pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
SCRIPTS_DIR="${PROJECT_ROOT}/scripts"
TESTS_DIR="${SCRIPTS_DIR}/tests"

echo -e "${YELLOW}==== Phase 18 集成测试 ====${NC}"
echo "项目根: ${PROJECT_ROOT}"

PYTHON_BIN="${PYTHON_BIN:-python3}"
export PYTHONPATH="${SCRIPTS_DIR}:${PYTHONPATH}"

# 跑集成测试
INTEGRATION_TEST="${TESTS_DIR}/test_phase18_integration.py"
if [ ! -f "$INTEGRATION_TEST" ]; then
    echo -e "${RED}❌ 集成测试文件不存在: $INTEGRATION_TEST${NC}"
    exit 1
fi

echo -e "${YELLOW}▶ 跑集成测试 ...${NC}"
if (cd "${TESTS_DIR}" && "$PYTHON_BIN" -m unittest "test_phase18_integration" -v 2>&1); then
    echo -e "${GREEN}✅ Phase 18 集成测试通过${NC}"
    exit 0
else
    echo -e "${RED}❌ Phase 18 集成测试失败${NC}"
    exit 1
fi
