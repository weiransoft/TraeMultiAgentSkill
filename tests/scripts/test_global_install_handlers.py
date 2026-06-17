"""验证全局副本的 autonomous 包结构及 handlers 实际可被 autonomous 模块加载。"""
import sys
import os

GLOBAL_ROOT = "/Users/wangwei/.trae/skills/trae-multi-agent"
SCRIPTS = f"{GLOBAL_ROOT}/scripts"
sys.path.insert(0, SCRIPTS)

# 直接 import autonomous 包（应该触发其 __init__ 和 handlers 子模块）
import autonomous
print(f"✓ autonomous 包导入成功: {autonomous.__file__}")

# 检查 autonomous 的关键 API
if hasattr(autonomous, "RalphAutonomousPlugin"):
    print("✓ RalphAutonomousPlugin 存在")
else:
    print("✗ RalphAutonomousPlugin 缺失")

# 尝试以包方式 import handlers
try:
    from autonomous import handlers
    print(f"✓ autonomous.handlers 子包: {handlers.__file__}")
    # 然后逐个 handler
    from autonomous.handlers import base
    print(f"✓ autonomous.handlers.base: {base.__file__}")
    if hasattr(base, "BaseHandler"):
        print("  ✓ BaseHandler 类存在")
    from autonomous.handlers import plan_handler
    print(f"✓ autonomous.handlers.plan_handler: {plan_handler.__file__}")
    if hasattr(plan_handler, "PlanHandler"):
        print("  ✓ PlanHandler 类存在")
    from autonomous.handlers import dev_handler
    print(f"✓ autonomous.handlers.dev_handler: {dev_handler.__file__}")
    if hasattr(dev_handler, "DevHandler"):
        print("  ✓ DevHandler 类存在")
    from autonomous.handlers import verify_handler
    print(f"✓ autonomous.handlers.verify_handler: {verify_handler.__file__}")
    if hasattr(verify_handler, "VerifyHandler"):
        print("  ✓ VerifyHandler 类存在")
    from autonomous.handlers import fix_handler
    print(f"✓ autonomous.handlers.fix_handler: {fix_handler.__file__}")
    if hasattr(fix_handler, "FixHandler"):
        print("  ✓ FixHandler 类存在")
except Exception as e:
    print(f"✗ handlers 加载失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()
print("✅ 全局副本 autonomous 包完整可用")
