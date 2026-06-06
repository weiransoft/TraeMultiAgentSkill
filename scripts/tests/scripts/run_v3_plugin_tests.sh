#!/bin/bash
# V3 插件架构测试运行器
# 运行顺序：V3 单元 → V3 集成 → Phase 13-15 回归
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SCRIPTS_DIR="$PROJECT_ROOT/scripts"

cd "$SCRIPTS_DIR"

echo "🧪 Phase 16 V3 插件架构 - V3 单元测试"
echo "======================================"
python3 -m unittest tests.test_v3_dispatcher -v
python3 -m unittest tests.test_v3_plugin_contract -v
python3 -m unittest tests.test_v3_plugin_context -v
python3 -m unittest tests.test_v3_dispatch_result -v
python3 -m unittest tests.test_v3_plugins -v

echo ""
echo "🔗 Phase 16 V3 插件架构 - V3 集成测试"
echo "======================================"
python3 -m unittest tests.test_v3_integration -v

echo ""
echo "📊 Phase 16 V3 插件架构 - Phase 13-15 回归"
echo "==========================================="
python3 -m unittest tests.test_loop_goal -v
python3 -m unittest tests.test_goal_orchestrator -v
python3 -m unittest tests.test_goal_orchestrator_integration -v
python3 -m unittest tests.test_dag_visualizer -v
python3 -m unittest tests.test_dag_visualizer_integration -v

echo ""
echo "✅ Phase 16 V3 插件架构 - 全部测试通过"
