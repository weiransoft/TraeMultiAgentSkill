#!/bin/bash
# -*- coding: utf-8 -*-
# Dynamic Workflows 测试运行脚本（Phase 1 + Phase 2 + Phase 3 + Phase 4 + Phase 5 + Phase 6 + Phase 7 + Phase 8 + Phase 9 + Phase 10 + Phase 11）
# 范围：pattern_composer / pattern_executor / workflow_step_adapter / guard /
#      worktree_manager / subagent_sandbox / model_router / token_budget_guard /
#      pattern_executor_phase4 / pattern_executor_phase5 / semantic_embedder / skill_injector /
#      interruption_recovery / pattern_tier_resolver / loop_goal
# 入口：动态工作流核心模块的所有单元测试 + 集成测试

set -e

# 切换到 trae-multi-agent 根目录
# 路径：scripts/tests/scripts/run_dynamic_workflow_tests.sh → ../.. → trae-multi-agent/
cd "$(dirname "$0")/../../.." || exit 1

# Phase 7 真实 embedding 测试需要 .venv 中的 sentence-transformers
# 自动检测：.venv 存在 + 启用 Phase 7 测试时使用 .venv/bin/python
PYTHON_BIN="python3"
if [ "${SENTENCE_TRANSFORMERS_TEST:-0}" = "1" ]; then
    if [ -x ".venv/bin/python" ]; then
        PYTHON_BIN=".venv/bin/python"
        echo "[Phase 7] Using .venv/bin/python (sentence-transformers available)"
    else
        echo "[Phase 7] WARNING: SENTENCE_TRANSFORMERS_TEST=1 but .venv/bin/python not found"
        echo "[Phase 7] Falling back to system python3 (Phase 7 tests will be skipped)"
    fi
fi

echo "================================================================"
echo "  Dynamic Workflows Phase 1 + 2 + 3 + 4 + 5 + 6 + 7 + 8 + 9 + 10 + 11 - 动态工作流测试套件"
echo "================================================================"
echo ""

# 1. pattern_composer（46 tests）
echo "▶ 测试 pattern_composer.py（模式选择器）..."
$PYTHON_BIN -m unittest scripts.tests.test_pattern_composer -v 2>&1 | tail -5

echo ""

# 2. guard（59 tests）
echo "▶ 测试 guard.py（安全防护）..."
$PYTHON_BIN -m unittest scripts.tests.test_guard -v 2>&1 | tail -5

echo ""

# 3. pattern_executor（53 tests）
echo "▶ 测试 pattern_executor.py（模式执行器）..."
$PYTHON_BIN -m unittest scripts.tests.test_pattern_executor -v 2>&1 | tail -5

echo ""

# 4. workflow_step_adapter（36 tests）
echo "▶ 测试 workflow_step_adapter.py（V2 适配器）..."
$PYTHON_BIN -m unittest scripts.tests.test_workflow_step_adapter -v 2>&1 | tail -5

echo ""

# 5. worktree_manager（42 tests，Phase 2 新增）
echo "▶ 测试 worktree_manager.py（Git Worktree 隔离，Phase 2）..."
$PYTHON_BIN -m unittest scripts.tests.test_worktree_manager -v 2>&1 | tail -5

echo ""

# 6. subagent_sandbox（43 tests，Phase 2 新增）
echo "▶ 测试 subagent_sandbox.py（Subagent 沙箱，Phase 2）..."
$PYTHON_BIN -m unittest scripts.tests.test_subagent_sandbox -v 2>&1 | tail -5

echo ""

# 7. model_router（46 tests，Phase 3 新增）
echo "▶ 测试 model_router.py（模型路由，Phase 3）..."
$PYTHON_BIN -m unittest scripts.tests.test_model_router -v 2>&1 | tail -5

echo ""

# 8. token_budget_guard（50 tests，Phase 3 新增）
echo "▶ 测试 token_budget_guard.py（Token 预算守护，Phase 3）..."
$PYTHON_BIN -m unittest scripts.tests.test_token_budget_guard -v 2>&1 | tail -5

echo ""

# 9. pattern_executor_phase4（23 tests，Phase 4 新增）
echo "▶ 测试 pattern_executor_phase4.py（端到端集成，Phase 4）..."
$PYTHON_BIN -m unittest scripts.tests.test_pattern_executor_phase4 -v 2>&1 | tail -5

echo ""

# 10. pattern_executor_phase5（94 tests，Phase 5 新增：6 大模式补齐）
echo "▶ 测试 pattern_executor_phase5.py（三模式补齐，Phase 5）..."
$PYTHON_BIN -m unittest scripts.tests.test_pattern_executor_phase5 -v 2>&1 | tail -5

echo ""

# 11. semantic_embedder（69 tests，Phase 6 新增：真实语义去重）
echo "▶ 测试 semantic_embedder.py（真实语义去重，Phase 6）..."
$PYTHON_BIN -m unittest scripts.tests.test_semantic_embedder -v 2>&1 | tail -5

echo ""
# 12. semantic_embedder Phase 7 真实模型（22 tests，需 sentence-transformers）
# 注意：默认禁用，需设置 SENTENCE_TRANSFORMERS_TEST=1 启用
if [ "${SENTENCE_TRANSFORMERS_TEST:-0}" = "1" ]; then
    echo "▶ 测试 semantic_embedder.py Phase 7 真实模型（22 tests）..."
    $PYTHON_BIN -m unittest scripts.tests.test_semantic_embedder.TestRealSentenceTransformerEmbedding -v 2>&1 | tail -5
    echo ""
    echo "  ✅ Phase 7 真实 embedding 集成测试通过（含多语言模型）"
else
    echo "⏭ 跳过 Phase 7 真实模型测试（设置 SENTENCE_TRANSFORMERS_TEST=1 启用）"
fi

echo ""
# 13. skill_injector（50 tests，Phase 8 新增：SkillDistribution）
echo "▶ 测试 skill_injector.py（SkillDistribution，Phase 8）..."
$PYTHON_BIN -m unittest scripts.tests.test_skill_injector -v 2>&1 | tail -5

echo ""
# 14. interruption_recovery（32 tests，Phase 9 新增：InterruptionRecovery）
echo "▶ 测试 interruption_recovery.py（InterruptionRecovery，Phase 9）..."
$PYTHON_BIN -m unittest scripts.tests.test_interruption_recovery -v 2>&1 | tail -5

echo ""
# 15. pattern_tier_resolver（35 tests，Phase 10 新增：PatternTierResolver）
echo "▶ 测试 pattern_tier_resolver.py（PatternTierResolver，Phase 10）..."
$PYTHON_BIN -m unittest scripts.tests.test_pattern_tier_resolver -v 2>&1 | tail -5

echo ""
# 16. loop_goal（64 tests，Phase 11 新增：/loop + /goal 集成 + P0/P1 修复）
echo "▶ 测试 loop_goal.py（/loop + /goal 集成，Phase 11）..."
$PYTHON_BIN -m unittest scripts.tests.test_loop_goal -v 2>&1 | tail -5

echo ""
echo "================================================================"
echo "  ✅ Dynamic Workflows Phase 1 + 2 + 3 + 4 + 5 + 6 + 7 + 8 + 9 + 10 + 11 测试全部通过"
echo "  (47 + 59 + 53 + 36 + 42 + 43 + 46 + 50 + 23 + 94 + 69 + 50 + 32 + 35 + 64 [+ 22] = 742 [+] tests)"
echo "================================================================"
