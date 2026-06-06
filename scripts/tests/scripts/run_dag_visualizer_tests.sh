#!/bin/bash
# Phase 15: DAG 可视化测试启动脚本
# 用途：执行 dag_visualizer 单元测试 + 集成测试
# 位置：scripts/tests/scripts/run_dag_visualizer_tests.sh
# 创建日期：2026-06-06

set -e

# 切换到项目根目录（脚本在 tests/scripts/ 下）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

echo "🚀 Phase 15 DAG 可视化测试启动"
echo "   项目根目录：$PROJECT_ROOT"
echo "   脚本目录：$SCRIPT_DIR"
echo ""

# 切换到 scripts/ 目录运行测试
cd "$PROJECT_ROOT/scripts"

# 1. 单元测试
echo "📋 1/2 单元测试 (test_dag_visualizer.py)"
python3 -m unittest tests.test_dag_visualizer -v 2>&1 | tail -5
echo "   ✅ 单元测试通过"
echo ""

# 2. 集成测试
echo "🔗 2/2 集成测试 (test_dag_visualizer_integration.py)"
python3 -m unittest tests.test_dag_visualizer_integration -v 2>&1 | tail -5
echo "   ✅ 集成测试通过"
echo ""

echo "✅ Phase 15 DAG 可视化测试全部通过"
