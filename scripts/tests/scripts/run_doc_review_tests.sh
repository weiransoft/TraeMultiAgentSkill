#!/bin/bash
# 文档对照代码审查阶段测试脚本
# 运行 v2.8 / v2.8.1 新增的全部测试

set -e

# 切换到 skill 根目录
# 脚本位于 multi-agent-team/scripts/tests/scripts/，需要返回 3 层
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$SKILL_ROOT"

echo "=========================================="
echo "  v2.8 / v2.8.1 文档对照代码审查 - 测试套件"
echo "=========================================="
echo ""

# 确保使用 Python3
PYTHON="${PYTHON:-python3}"
echo "Python: $($PYTHON --version 2>&1)"
echo "工作目录: $(pwd)"
echo ""

# 测试文件列表
TEST_FILES=(
    "scripts/tests/test_doc_code_consistency_checker.py"
    "scripts/tests/test_review_handler.py"
    "scripts/tests/test_workflow_loop_controller.py"
)

# 运行测试
PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0

for test_file in "${TEST_FILES[@]}"; do
    echo "------------------------------------------"
    echo "运行: $test_file"
    echo "------------------------------------------"
    if $PYTHON -m pytest "$test_file" -v --tb=short 2>&1; then
        echo "✅ $test_file 通过"
        ((PASS_COUNT++))
    else
        echo "❌ $test_file 失败"
        ((FAIL_COUNT++))
    fi
    echo ""
done

# 汇总
echo "=========================================="
echo "  测试汇总"
echo "=========================================="
echo "  通过: $PASS_COUNT / ${#TEST_FILES[@]}"
echo "  失败: $FAIL_COUNT / ${#TEST_FILES[@]}"
echo ""

if [ "$FAIL_COUNT" -gt 0 ]; then
    echo "❌ 存在失败的测试"
    exit 1
else
    echo "✅ 全部测试通过"
    exit 0
fi
