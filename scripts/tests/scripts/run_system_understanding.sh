#!/usr/bin/env bash
# =============================================================================
# SU（System Understanding）能力单元测试全量运行脚本
# 覆盖 PRD §6.1 测试矩阵 su_* 全部 11 个模块（REQ-SU-001~021）
#
# 分工互引：本脚本只跑纯单测（进程内，无 playwright 硬依赖）；
# 【fixture 测试站 + 真实 SU 进程】的端到端场景见同目录
# run_system_understanding_e2e.sh。两者均由 run_all.sh 统一调度。
#
# 用法：
#   bash scripts/tests/scripts/run_system_understanding.sh
# 退出码：0=全部通过；非 0=失败模块数（CI 直接以本脚本收口）
# =============================================================================
set -u

# 定位项目根目录（scripts/tests/scripts/ 上溯三级 = 项目根）
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$PROJECT_ROOT" || exit 1

PYTHON="${PYTHON:-python3}"

# 14 个 SU 测试模块（unittest 模块路径，tests/ 无 __init__.py 依赖 discovery）
SU_MODULES=(
  "tests.test_su_config"          # REQ-SU-001/002 配置与脱敏管线
  "tests.test_su_url_key"         # REQ-SU-005 URL 归一化
  "tests.test_su_action_tier"     # REQ-SU-006 动作分级
  "tests.test_su_route_guard"     # REQ-SU-007 route 拦截（红线④）
  "tests.test_su_readonly_guard"  # REQ-SU-010 DB 只读（红线②）
  "tests.test_su_implicit_fk"     # REQ-SU-012/013 隐式外键
  "tests.test_su_redis_patterns"  # REQ-SU-014/015 Redis（红线③）
  "tests.test_su_relation"        # REQ-SU-016/017 三角关联
  "tests.test_su_doc_render"      # REQ-SU-018 文档渲染
  "tests.test_su_state"           # REQ-SU-019 SQLite 状态机
  "tests.test_su_api_observer"    # REQ-SU-009 flush 完结闸门/shape 归一
  "tests.test_su_site_crawler"    # crawler 层协议（form 去重/预算顺序/settled）
  "tests.test_su_degrade"         # REQ-SU-021 软依赖降级
  "tests.test_su_e2e_site"        # fixture 测试站/状态库 builder 自测（防腐烂）
)

failed=0
for mod in "${SU_MODULES[@]}"; do
  echo "==> ${mod}"
  # -B：不写 .pyc；PYTHONPATH=scripts 使 unittest 能按 tests.* 定位模块
  # （与手工运行 `cd <root> && python3 -m unittest scripts.tests.xxx` 等价）
  if ! (cd "$PROJECT_ROOT" && PYTHONPATH="$PROJECT_ROOT/scripts${PYTHONPATH:+:$PYTHONPATH}" \
        "$PYTHON" -B -m unittest "$mod"); then
    failed=$((failed + 1))
    echo "!!! FAILED: ${mod}"
  fi
done

echo "----------------------------------------"
if [ "$failed" -eq 0 ]; then
  echo "SU 单元测试全部通过（${#SU_MODULES[@]} 个模块）"
else
  echo "SU 单元测试失败模块数：${failed}/${#SU_MODULES[@]}"
fi
exit "$failed"
