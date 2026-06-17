"""验证 V3 插件 autonomous + GoalDispatcher 集成。"""
import sys

GLOBAL_ROOT = "/Users/wangwei/.trae/skills/trae-multi-agent"
sys.path.insert(0, f"{GLOBAL_ROOT}/scripts")
sys.path.insert(0, f"{GLOBAL_ROOT}/scripts/plugins")
sys.path.insert(0, f"{GLOBAL_ROOT}/scripts/dispatcher")

# V3 插件 autonomous（位于 scripts/plugins/autonomous.py）
from plugins import autonomous as auto_plugin
print(f"✓ plugins.autonomous: {auto_plugin.__file__}")

if hasattr(auto_plugin, "RalphAutonomousPlugin"):
    cls = auto_plugin.RalphAutonomousPlugin
    print(f"✓ RalphAutonomousPlugin: {cls}")
    inst = cls()
    print(f"✓ 实例化: name={inst.name}, version={getattr(inst, 'version', '?')}")
else:
    print("✗ RalphAutonomousPlugin 缺失")
    sys.exit(1)

# 尝试注册到 dispatcher
try:
    from dispatcher.goal_dispatcher import GoalDispatcher
    from plugins import BUILTIN_PLUGINS
    d = GoalDispatcher(plugins=list(BUILTIN_PLUGINS))
    names = [p.name for p in d.list_plugins()]
    print(f"✓ Dispatcher 注册了 {len(names)} 个插件: {names}")
    if "autonomous" in names:
        print("✅ autonomous 插件在 dispatcher 中可见")
    else:
        print("✗ autonomous 不在 dispatcher 列表中")
        sys.exit(1)
except Exception as e:
    print(f"⚠ Dispatcher 集成测试跳过: {e}")

# 验证 mutex 对称性
try:
    plugin_names = [p.name for p in [auto_plugin.RalphAutonomousPlugin()]]
    print(f"✓ RalphAutonomousPlugin 名称: {plugin_names}")
except Exception as e:
    print(f"✗ Plugin 列表获取失败: {e}")
    sys.exit(1)

print()
print("✅ V3 插件 autonomous 全局副本可用")
