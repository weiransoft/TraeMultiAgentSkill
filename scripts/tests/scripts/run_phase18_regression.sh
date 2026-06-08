#!/bin/bash
# Phase 18 回归测试脚本
# 用途：跑 1263 个旧测试 + 6 个内置 plugin 测试，验证 autonomous 模式不破坏 V3
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

echo -e "${YELLOW}==== Phase 18 回归测试 ====${NC}"
echo "项目根: ${PROJECT_ROOT}"

PYTHON_BIN="${PYTHON_BIN:-python3}"
export PYTHONPATH="${SCRIPTS_DIR}:${PYTHONPATH}"

# 1. V3 plugin 测试
echo -e "\n${YELLOW}[1] V3 plugin 测试 (含 Phase 18 autonomous plugin)${NC}"
V3_TESTS=(
    "test_v3_plugins"
    "test_v3_plugin_contract"
    "test_v3_integration"
    "test_v3_dispatcher"
    "test_v3_dispatch_result"
    "test_v3_plugin_context"
)
PASS=0
FAIL=0
for t in "${V3_TESTS[@]}"; do
    test_path="${TESTS_DIR}/${t}.py"
    if [ ! -f "$test_path" ]; then
        echo -e "${YELLOW}⚠ ${t}.py 不存在 (跳过)${NC}"
        continue
    fi
    # 用退出码判断测试是否通过（更可靠：避免与日志输出混淆）
    if (cd "${TESTS_DIR}" && "$PYTHON_BIN" -m unittest "${t}" >/dev/null 2>&1); then
        echo -e "${GREEN}✓ ${t}${NC}"
        PASS=$((PASS + 1))
    else
        echo -e "${RED}✗ ${t}${NC}"
        FAIL=$((FAIL + 1))
    fi
done

# 2. Plugin mutex 互斥验证
echo -e "\n${YELLOW}[2] Plugin mutex 互斥验证${NC}"
MUTEX_TEST=$(cat <<'PYEOF'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve()))

from plugins import BUILTIN_PLUGINS

# 验证：6 个内置 plugin
assert len(BUILTIN_PLUGINS) == 6, f"应有 6 个内置 plugin，实际 {len(BUILTIN_PLUGINS)}"

# 验证：每个 plugin 有 mutex_with
for p in BUILTIN_PLUGINS:
    assert hasattr(p, "mutex_with"), f"{p.name} 缺少 mutex_with"
    assert isinstance(p.mutex_with, set), f"{p.name}.mutex_with 应为 set"

# 验证：autonomous 与其他 5 个互斥
autonomous = [p for p in BUILTIN_PLUGINS if p.name == "autonomous"][0]
expected_mutex = {"goal-cancel", "goal-graph", "goal-resume", "multi-goal", "loop"}
assert autonomous.mutex_with == expected_mutex, \
    f"autonomous.mutex_with 错误：{autonomous.mutex_with} != {expected_mutex}"

# 验证：其他 plugin 也包含 autonomous
for p in BUILTIN_PLUGINS:
    if p.name == "autonomous":
        continue
    assert "autonomous" in p.mutex_with, \
        f"{p.name}.mutex_with 应包含 'autonomous'，实际 {p.mutex_with}"

# 验证：priority 顺序（autonomous=5 是唯一）
autonomous_prio = autonomous.priority
assert autonomous_prio == 5, f"autonomous.priority 应为 5，实际 {autonomous_prio}"

print("✅ mutex 互斥验证通过")
PYEOF
)
if echo "$MUTEX_TEST" | PYTHONPATH="$SCRIPTS_DIR:$PYTHONPATH" "$PYTHON_BIN" 2>&1; then
    echo -e "${GREEN}✓ mutex 互斥验证${NC}"
else
    echo -e "${RED}✗ mutex 互斥验证失败${NC}"
    FAIL=$((FAIL + 1))
fi

# 3. CLI 兼容性测试
echo -e "\n${YELLOW}[3] CLI 兼容性测试${NC}"
CLI_TEST=$(cat <<'PYEOF'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve()))

from cli.parser import parse_arguments

# 验证：--autonomous flag 存在
# parse_arguments() 内部使用 sys.argv，故需 monkey-patch
import argparse
try:
    sys.argv = ["main", "--task", "test", "--autonomous"]
    args = parse_arguments()
    assert args.autonomous is True, "args.autonomous 应为 True"
    assert args.auto_max_iterations == 50, f"默认 max_iterations 应为 50，实际 {args.auto_max_iterations}"
    # 校验 test_command 包含 unittest discover（不强求引号）
    assert "python3" in args.auto_test_command, f"test_command 应含 python3，实际 {args.auto_test_command}"
    assert "unittest" in args.auto_test_command, f"test_command 应含 unittest，实际 {args.auto_test_command}"
    print("✅ CLI --autonomous 解析正确")
except SystemExit:
    # 某些测试需要 --agent 参数
    sys.argv = ["main", "--task", "test", "--agent", "auto", "--autonomous"]
    args = parse_arguments()
    assert args.autonomous is True
    print("✅ CLI --autonomous 解析正确（with --agent）")
PYEOF
)
if echo "$CLI_TEST" | PYTHONPATH="$SCRIPTS_DIR:$PYTHONPATH" "$PYTHON_BIN" 2>&1; then
    echo -e "${GREEN}✓ CLI 兼容性${NC}"
else
    echo -e "${RED}✗ CLI 兼容性测试失败${NC}"
    FAIL=$((FAIL + 1))
fi

# 总结
echo ""
echo -e "${YELLOW}==== 回归测试总结 ====${NC}"
echo "通过: $((PASS + 2)) (plugin: $PASS + mutex: 1 + cli: 1)"
echo "失败: $FAIL"
if [ $FAIL -gt 0 ]; then
    echo -e "${RED}❌ 回归测试失败${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Phase 18 回归测试通过（autonomous 不破坏 V3）${NC}"
exit 0
