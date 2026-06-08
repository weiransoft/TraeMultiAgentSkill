#!/bin/bash
# Phase 18 全部测试运行脚本
# 用途：跑全部 Phase 18 相关测试（unit + integration + e2e + safety + regression）
# 退出码：0=成功，1=失败

set -e
set -o pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${BLUE}##########################################${NC}"
echo -e "${BLUE}# Phase 18 全部测试 (142 tests target) #${NC}"
echo -e "${BLUE}##########################################${NC}"
echo ""

PASS_COUNT=0
FAIL_COUNT=0
FAILED_SCRIPTS=()

# 1. 单元测试
echo -e "${YELLOW}==== [1/5] 单元测试 ====${NC}"
if bash "${SCRIPT_DIR}/run_phase18_unit.sh" 2>&1; then
    echo -e "${GREEN}✅ 单元测试通过${NC}"
    PASS_COUNT=$((PASS_COUNT + 1))
else
    echo -e "${RED}❌ 单元测试失败${NC}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAILED_SCRIPTS+=("run_phase18_unit.sh")
fi
echo ""

# 2. 集成测试
echo -e "${YELLOW}==== [2/5] 集成测试 ====${NC}"
if bash "${SCRIPT_DIR}/run_phase18_integration.sh" 2>&1; then
    echo -e "${GREEN}✅ 集成测试通过${NC}"
    PASS_COUNT=$((PASS_COUNT + 1))
else
    echo -e "${RED}❌ 集成测试失败${NC}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAILED_SCRIPTS+=("run_phase18_integration.sh")
fi
echo ""

# 3. E2E 基础测试
echo -e "${YELLOW}==== [3/5] E2E 基础测试 ====${NC}"
if bash "${SCRIPT_DIR}/run_phase18_e2e_basic.sh" 2>&1; then
    echo -e "${GREEN}✅ E2E 基础测试通过${NC}"
    PASS_COUNT=$((PASS_COUNT + 1))
else
    echo -e "${RED}❌ E2E 基础测试失败${NC}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAILED_SCRIPTS+=("run_phase18_e2e_basic.sh")
fi
echo ""

# 4. E2E 断点续跑测试
echo -e "${YELLOW}==== [4/5] E2E 断点续跑测试 ====${NC}"
if bash "${SCRIPT_DIR}/run_phase18_e2e_resume.sh" 2>&1; then
    echo -e "${GREEN}✅ E2E 断点续跑测试通过${NC}"
    PASS_COUNT=$((PASS_COUNT + 1))
else
    echo -e "${RED}❌ E2E 断点续跑测试失败${NC}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAILED_SCRIPTS+=("run_phase18_e2e_resume.sh")
fi
echo ""

# 5. 安全性 + 回归测试
echo -e "${YELLOW}==== [5/5] 安全性 + 回归测试 ====${NC}"
if bash "${SCRIPT_DIR}/run_phase18_e2e_safety.sh" 2>&1 && \
   bash "${SCRIPT_DIR}/run_phase18_regression.sh" 2>&1; then
    echo -e "${GREEN}✅ 安全性 + 回归测试通过${NC}"
    PASS_COUNT=$((PASS_COUNT + 1))
else
    echo -e "${RED}❌ 安全性 + 回归测试失败${NC}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAILED_SCRIPTS+=("safety+regression")
fi
echo ""

# 总结
echo -e "${BLUE}##########################################${NC}"
echo -e "${BLUE}# 测试总结                            #${NC}"
echo -e "${BLUE}##########################################${NC}"
echo "通过: ${PASS_COUNT}/5"
echo "失败: ${FAIL_COUNT}/5"
if [ $FAIL_COUNT -gt 0 ]; then
    echo -e "${RED}失败脚本:${NC}"
    for s in "${FAILED_SCRIPTS[@]}"; do
        echo "  - $s"
    done
    echo ""
    echo -e "${RED}❌ Phase 18 全部测试失败${NC}"
    exit 1
fi
echo -e "${GREEN}🎉 Phase 18 全部测试通过 (142 tests target)${NC}"
exit 0
