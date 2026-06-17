"""Ponytail 决策梯规则集引擎。

设计目标（v2 修订）：
- 用 Python 常量定义规则（放弃 markdown 解析，降低复杂度）
- 按模式（lite/full/ultra）返回不同规则片段
- 按角色选择适用子集
- 与 Karpathy 原则叠加，不替换
- 线程安全：get_injection_prompt 接受参数，不修改实例状态

优先级（架构师评审 P0 修正）：
    项目规则 > Karpathy 原则 > Ponytail 决策梯 > 默认行为

Ponytail 决策梯定位为 Karpathy Simplicity First 原则的"执行手册"，
而非独立层级。
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional


class PonytailMode(str, Enum):
    """决策梯强度模式。

    - OFF: 关闭，不注入任何决策梯
    - LITE: 按要求构建，但用一行说明更懒的替代方案
    - FULL: 决策梯强制执行（默认）
    - ULTRA: YAGNI 极端主义，删除优先于添加
    """

    OFF = "off"
    LITE = "lite"
    FULL = "full"
    ULTRA = "ultra"


# 角色到决策梯强度的映射（架构师评审修订）
# - solo_coder: FULL（开发者需要完整决策梯）
# - architect: FULL（架构师在 plan 阶段也需考虑 YAGNI，否则下游救不回来）
# - test_expert: LITE（测试代码同样需要 YAGNI，但不强制）
# - product_manager: OFF（产品经理不写代码）
# - ui_designer: LITE（UI 设计师提示但不强制）
ROLE_INTENSITY = {
    "solo_coder": PonytailMode.FULL,
    "architect": PonytailMode.FULL,
    "test_expert": PonytailMode.LITE,
    "product_manager": PonytailMode.OFF,
    "ui_designer": PonytailMode.LITE,
}

# 决策梯主体（Python 常量，单一真实来源）
# 6 步决策梯：YAGNI → 标准库优先 → 平台原生 → 复用现有 → 一行优先 → 最小可行
_LADDER_BODY = """## 代码决策梯（Ponytail 风格）
写任何代码前，按顺序停在第一个满足的台阶上：

1. 【YAGNI】这东西真的需要存在吗？
   → 推测性需求 = 跳过，用一行注释说明为何跳过
   → 红线：用户明确要求的功能不可跳过；需求文档明确列出的功能不可跳过

2. 【标准库优先】语言标准库能搞定？
   → 直接用标准库，标注 `# ponytail: stdlib covers this`
   → 红线：标准库功能不满足安全/性能要求时不可用

3. 【平台原生】运行时平台自带功能能覆盖？
   → 用平台原生特性（如 <input type="date"> 替代 picker 库、CSS 替代 JS、DB 约束替代应用代码）
   → 红线：平台特性有已知 bug 或安全漏洞时不可用

4. 【复用现有】已安装的依赖能解决？
   → 复用现有依赖，绝不为几行能搞定的事新增依赖
   → 红线：现有依赖有 license 冲突或安全漏洞时不可用

5. 【一行优先】能写成一行？
   → 写成一行，但不可牺牲可读性到"只有自己看得懂"
   → 红线：涉及金钱/安全/并发的逻辑不可强行压缩

6. 【最小可行】以上都不行
   → 写最少能做工作的代码（minimum code that works）
   → 红线：项目规则"禁止简化、模拟、占位"优先级高于本台阶

决策梯是反射，不是研究项目。两个台阶都成立 → 取更高的那个继续。
第一个能工作的懒方案就是正确方案。"""

# 不可简化红线（16 条）
# ponytail 原版红线（1-6）+ 项目规则追加（7-9）+ 架构师评审追加（10-16）
_RED_LINES = """## 不可简化红线
以下内容永远不在决策梯的"可跳过"范围内：
1. 信任边界的输入校验
2. 防止数据丢失的错误处理
3. 安全措施
4. 无障碍基础
5. 用户明确要求保留的功能
6. 真实硬件的校准旋钮
7. 【项目规则】真实业务逻辑——禁止用 mock/占位/stub 替代
8. 【项目规则】需求文档规定的功能——禁止跳过或简化
9. 【项目规则】非平凡逻辑必须留一个可运行检查
10. 【项目规则】并发安全代码——Lock/Atomic/synchronized 不可简化
11. 【项目规则】真实错误处理——禁止 except: pass 吞异常
12. 【项目规则】日志与审计——关键路径日志不可删除
13. 【项目规则】配置与密钥管理——密钥读取、配置校验不可简化
14. 【项目规则】数据库事务边界——事务提交/回滚不可简化
15. 【项目规则】API 契约——公开 API 签名/返回格式不可单方面简化
16. 【项目规则】隐私数据处理——PII 数据处理不可简化"""

# 输出规范
_OUTPUT_SPEC = """## 输出规范
代码优先。然后最多三行短说明：跳过了什么、何时该加。
解释比代码长 → 删解释。
标记故意简化：`# ponytail: <说明>` 或 `# ponytail: <上限>, <升级路径>`"""

# ultra 模式追加条款
_ULTRA_EXTRA = """## Ultra 模式追加条款
- YAGNI 极端主义：删除优先于添加
- 交付 one-liner 的同时挑战需求："Did X; Y covers it. Need full X? Say so."
- 红线违反时硬阻断（降级到 full 并告警）
- 用户明确要求完整实现时，必须构建完整版本，不可 re-arguing"""

# lite 模式追加条款
_LITE_EXTRA = """## Lite 模式追加条款
- 按要求构建，但用一行说明更懒的替代方案
- 让用户选择是否采用更懒的方案"""


class PonytailRulesetEngine:
    """决策梯规则集引擎（线程安全，无状态修改）。

    职责：
    1. 按模式返回规则片段（Python 常量，无 IO）
    2. 按角色选择适用子集
    3. 生成注入 prompt 片段
    4. 与 Karpathy 原则叠加

    线程安全保证：
    - get_injection_prompt 是纯函数，不修改实例状态
    - 所有规则常量是模块级不可变字符串
    - 无共享可变状态
    """

    def __init__(self, skill_root: Optional[Path] = None):
        """构造规则集引擎。

        Args:
            skill_root: skill 根目录（保留参数，当前未使用，为未来扩展预留）
        """
        self._skill_root = skill_root

    def get_injection_prompt(
        self,
        role: str = "solo_coder",
        mode: Optional[PonytailMode] = None,
    ) -> str:
        """获取注入到 LLM prompt 的决策梯片段（线程安全）。

        Args:
            role: 当前角色（architect/pm/solo_coder/test_expert/ui_designer）
            mode: 覆盖模式（None 则用角色默认强度）

        Returns:
            str: 决策梯 prompt 片段（若模式为 OFF 返回空字符串）
        """
        # 确定模式：显式参数 > 角色默认
        effective_mode = mode if mode is not None else ROLE_INTENSITY.get(role, PonytailMode.OFF)

        # OFF 模式不注入
        if effective_mode == PonytailMode.OFF:
            return ""

        # 组装 prompt（纯函数，不修改实例状态）
        parts = [_LADDER_BODY, _RED_LINES, _OUTPUT_SPEC]

        if effective_mode == PonytailMode.ULTRA:
            parts.append(_ULTRA_EXTRA)
        elif effective_mode == PonytailMode.LITE:
            parts.append(_LITE_EXTRA)

        header = f"## Ponytail 决策梯（模式：{effective_mode.value}，角色：{role}）\n"
        return header + "\n\n".join(parts)

    def get_red_lines(self) -> str:
        """获取红线清单（供 verify_handler 检测使用）。

        Returns:
            str: 16 条不可简化红线的完整文本
        """
        return _RED_LINES

    def get_ladder_body(self) -> str:
        """获取决策梯主体（6 步）。

        Returns:
            str: 6 步决策梯的完整文本
        """
        return _LADDER_BODY


__all__ = ["PonytailRulesetEngine", "PonytailMode", "ROLE_INTENSITY"]
