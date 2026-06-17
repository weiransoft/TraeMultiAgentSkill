"""验证全局安装的 trae-multi-agent 全部模块可正常加载。"""
import sys

# 添加全局副本的 scripts 路径
GLOBAL_ROOT = "/Users/wangwei/.trae/skills/trae-multi-agent"
sys.path.insert(0, f"{GLOBAL_ROOT}/scripts")
sys.path.insert(0, f"{GLOBAL_ROOT}/scripts/autonomous")
sys.path.insert(0, f"{GLOBAL_ROOT}/scripts/plugins")
sys.path.insert(0, f"{GLOBAL_ROOT}/scripts/dispatcher")

failures = []
loaded = []


def check_import(name, module_path=None):
    """导入模块并验证关键符号。"""
    try:
        if module_path:
            mod = __import__(module_path, fromlist=["__name__"])
        else:
            mod = __import__(name, fromlist=["__name__"])
        loaded.append(name)
        print(f"  ✓ {name}")
        return True
    except Exception as exc:
        failures.append((name, str(exc)))
        print(f"  ✗ {name}: {exc}")
        return False


print("=" * 60)
print("全局副本模块加载测试")
print("=" * 60)

print("\n[1/3] 核心模块")
check_import("ai_assistant", "ai_assistant")
check_import("ai_semantic_matcher", "ai_semantic_matcher")
check_import("claude_code_subagent_adapter", "claude_code_subagent_adapter")
check_import("dual_layer_context_manager", "dual_layer_context_manager")
check_import("role_matcher", "role_matcher")
check_import("workflow_engine", "workflow_engine")
check_import("workflow_engine_v2", "workflow_engine_v2")
check_import("skill_registry", "skill_registry")

print("\n[2/3] Phase 18 autonomous 模块")
check_import("loop_controller", "loop_controller")
check_import("git_driver", "git_driver")
check_import("notes_memory", "notes_memory")
check_import("sleep_guard", "sleep_guard")
check_import("smart_confirmation", "smart_confirmation")
check_import("run_state", "run_state")
check_import("config_loader", "config_loader")
check_import("auto_skill_loader", "auto_skill_loader")
check_import("dispatcher_adapter", "dispatcher_adapter")
check_import("autonomous plugin", "autonomous")

print("\n[3/3] Handlers (autonomous)")
import importlib.util
import os

handlers_dir = f"{GLOBAL_ROOT}/scripts/autonomous/handlers"
for fname in sorted(os.listdir(handlers_dir)):
    if fname.endswith(".py") and fname != "__init__.py":
        module_name = fname[:-3]
        spec = importlib.util.spec_from_file_location(
            f"handlers.{module_name}",
            f"{handlers_dir}/{fname}",
        )
        if spec and spec.loader:
            try:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                loaded.append(f"handlers.{module_name}")
                print(f"  ✓ handlers.{module_name}")
            except Exception as exc:
                failures.append((f"handlers.{module_name}", str(exc)))
                print(f"  ✗ handlers.{module_name}: {exc}")

print("\n" + "=" * 60)
print(f"结果: {len(loaded)} 成功, {len(failures)} 失败")
print("=" * 60)

if failures:
    print("\n失败详情:")
    for name, err in failures:
        print(f"  - {name}: {err}")
    sys.exit(1)
else:
    print("\n✅ 全局副本所有模块加载成功")
    sys.exit(0)
