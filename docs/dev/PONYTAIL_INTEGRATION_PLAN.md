# Ponytail 风格"少写多余代码"方案设计（v2 评审修订版）

> **目标**：借鉴 [ponytail](https://github.com/DietrichGebert/ponytail) 的 6 步决策梯思想，在 TraeMultiAgentSkill 中实现"让 AI 像最懒的资深工程师一样写代码"——少写、复用、标准库优先、YAGNI。
>
> **状态**：多角色评审已完成，v2 修订版（待实施）
> **作者**：TraeMultiAgentSkill 团队
> **日期**：2026-06-17
> **评审记录**：架构师（有条件是）+ 测试专家（不充分）+ 独立开发者（有条件是）

---

## 一、背景与动机

### 1.1 问题现状（Phase 0 验证后更新）

对 TraeMultiAgentSkill 现有 prompt 体系的全面审计 + Phase 0 链路验证发现以下关键问题：

| 问题 | 严重程度 | 说明 |
|------|---------|------|
| **autonomous dev 链路完全断裂** | 🔴 致命 | Phase 0 验证：`auto_context` **不被 facade 消费**；DevHandler 二次调用 `DispatcherAdapter.invoke()` 会**无限递归** autonomous plugin（`args.autonomous=True` 再次匹配）。autonomous 模式的 dev/fix 阶段**从未真正调用过 LLM** |
| **"少写代码"指令缺失** | 🔴 高 | 全项目无 YAGNI、无"标准库优先"、无"复用现有代码"、无"一行能搞定就一行"等指令 |
| **Karpathy 原则注入路径断裂** | 🟡 中 | `legacy.py` 中的 `karpathy_principles` context 和 `claude_code_subagent_adapter.py` 的硬编码 prompt **都不在 autonomous 调用链路上** |
| **约束多为"事后审查"** | 🟡 中 | `karpathy_principle_enforcer.py` 是事后扫描工具，不是事前约束 |
| **角色 prompt 引用悬空** | 🟢 低 | `SKILL.md` 引用的 `docs/roles/{role}/prompt.md` 不存在，实际只有 TEMPLATE 文档 |

### 1.2 Phase 0 验证结论（关键发现）

```
验证脚本输出：
  facade available: True
  auto_context in facade source: False      ← 注入链路断裂
  auto_skills in facade source: False       ← 同样断裂
  auto_context in dispatcher source: False  ← dispatcher 也不消费
  autonomous plugin matches args.autonomous=True: True  ← 递归确认
```

**这意味着**：
1. Ponytail 注入**不能走** `extra_context → auto_context` 这条死路
2. 必须先**修复 DevHandler 的调用路径**，让它真正调用 `_dispatch_via_claude_code`（那里有真正的 prompt 注入点：`context` dict → `json.dumps` → prompt）
3. 修复后的 DevHandler 直接构造 `context` dict 并调用 `_dispatch_via_claude_code`，Ponytail 决策梯注入到这个 `context` 中

### 1.3 ponytail 的核心启示

ponytail（21.9k Star）用一套 **6 步决策梯** 实现了 80-94% 更少代码、47-77% 更便宜、3-6 倍更快。其核心不是黑科技，而是让 AI 在写代码前先停下来想六个问题：

1. 这东西真的需要存在吗？→ 不需要就跳过（YAGNI）
2. 标准库能搞定？→ 直接用
3. 浏览器/平台自带功能？→ 直接用
4. 已安装的依赖能用？→ 复用
5. 一行代码能搞定？→ 一行搞定
6. 以上都不行，才写最少能做工作的代码

**关键洞察**：ponytail 把"能删的东西全删掉"的思维塞进了 AI 的脑子里，而我们当前的 prompt 体系完全没有这个层级的约束。

### 1.4 为什么不能直接用 ponytail

| 原因 | 说明 |
|------|------|
| 架构不兼容 | ponytail 是 Node.js 生命周期钩子（SessionStart / UserPromptSubmit），我们是 Python dispatcher 架构 |
| 多角色协作 | ponytail 面向单 Agent，我们有 5 个角色（architect/pm/solo-coder/test-expert/ui-designer），需要差异化约束 |
| autonomous 模式 | ponytail 无 autonomous 循环概念，我们需要在 Plan/Dev/Verify/Fix 四阶段注入不同约束 |
| 技术栈约束 | 我们的项目规则明确"禁止简化、模拟、占位"，需要与 ponytail 的"懒"哲学精确平衡 |

---

## 二、设计目标

### 2.1 核心目标

1. **事前约束**：在 AI 写代码**之前**注入决策梯，而非事后扫描
2. **全链路覆盖**：修复 autonomous dev/fix 链路断裂 + 覆盖 legacy 调用路径 + 代码审查 prompt
3. **角色差异化**：不同角色应用不同强度的决策梯
4. **不可简化红线**：与项目规则"禁止简化、模拟、占位"精确平衡——决策梯的"懒"不能越过红线
5. **可度量**：提供 benchmark 机制量化"少写代码"效果

### 2.2 非目标

- ❌ 不替换 Karpathy 原则，而是作为 Karpathy `Simplicity First` 的**可执行步骤**
- ❌ 不引入 Node.js 依赖
- ❌ 不改变现有 V3 插件架构的 plugin 注册/调度机制
- ❌ 不为"懒"而牺牲项目规则要求的"真实实现"

---

## 三、核心设计：决策梯规则集

### 3.1 决策梯定义（中文版，适配 TraeMultiAgentSkill）

```
## 代码决策梯（Ponytail 风格）
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
第一个能工作的懒方案就是正确方案。
```

### 3.2 不可简化红线（16 条，与项目规则对齐）

ponytail 原版红线 + 项目规则叠加 + 架构师评审追加：

| # | 红线类别 | 来源 | 说明 |
|---|---------|------|------|
| 1 | 信任边界输入校验 | ponytail | 不可简化 |
| 2 | 防数据丢失的错误处理 | ponytail | 不可简化 |
| 3 | 安全措施 | ponytail | 不可简化 |
| 4 | 无障碍基础 | ponytail | 不可简化 |
| 5 | 用户明确要求 | ponytail | 不可简化 |
| 6 | 硬件校准 | ponytail | 真实硬件校准旋钮不可删 |
| 7 | **真实业务逻辑** | 项目规则 | 🔴 禁止用 mock/占位/stub 替代 |
| 8 | **需求文档规定的功能** | 项目规则 | 🔴 禁止跳过或简化 |
| 9 | **测试覆盖** | 项目规则 | 🔴 非平凡逻辑必须留一个可运行检查 |
| 10 | **并发安全代码** | 架构师评审 | 🔴 Lock/Atomic/synchronized 不可简化 |
| 11 | **真实错误处理** | 架构师评审 | 🔴 禁止 `except: pass` 吞异常 |
| 12 | **日志与审计** | 架构师评审 | 🔴 关键路径日志不可删除 |
| 13 | **配置与密钥管理** | 架构师评审 | 🔴 密钥读取、配置校验不可简化 |
| 14 | **数据库事务边界** | 架构师评审 | 🔴 事务提交/回滚不可简化 |
| 15 | **API 契约** | 架构师评审 | 🔴 公开 API 签名/返回格式不可单方面简化 |
| 16 | **隐私数据处理** | 架构师评审 | 🔴 PII 数据处理不可简化 |

### 3.3 `ponytail:` 注释标记规范

```
# ponytail: <简化说明>
# ponytail: <已知上限>, <升级路径>
```

**示例**：
```python
# ponytail: stdlib covers this
from email.utils import parseaddr
"@" in parseaddr(email)[1]
```

```python
# ponytail: global lock, per-account locks if throughput matters
lock = threading.Lock()
```

**债务收割**：提供 `grep -rnE '(#|//) ?ponytail:' .` 脚本，生成技术债台账。缺少升级触发条件的标记打上 `no-trigger` 标签（腐烂风险）。

### 3.4 三档强度（lite / full / ultra）

| 档位 | 行为 | 适用场景 |
|------|------|---------|
| **lite** | 按要求构建，但用一行说明更懒的替代方案，让用户选 | 需求不明确时 |
| **full**（默认） | 决策梯强制执行。标准库和原生优先。最短 diff、最短解释 | 常规开发 |
| **ultra** | YAGNI 极端主义。删除优先于添加。交付 one-liner 的同时挑战需求 | 重构/技术债清理 |

**ultra 模式安全加固**（架构师评审 P0）：
- ultra 模式必须 `--auto-ponytail-ultra` 显式启用 + 二次确认
- **autonomous 模式下禁止 ultra**（强制降级为 full）—— 无人值守 + 激进删除 = 高风险
- ultra 模式每次启用只对**单个任务**生效，不持久化
- ultra 模式下若触发红线，**硬阻断**（降级到 full 并告警）
- ultra 模式下 `debt_collector` 在 dev 阶段后强制运行，`no_trigger` 项 ≥ 3 即中止

---

## 四、架构设计（v2 修订：注入路径重构）

### 4.1 注入路径重构（Phase 0 验证后的核心变更）

**原方案（已废弃）**：`extra_context → auto_context → facade` ← **链路断裂**

**v2 方案**：DevHandler **直接调用** `_dispatch_via_claude_code`，绕过递归陷阱

```
┌─────────────────────────────────────────────────────────────┐
│                    PonytailRulesetEngine                     │
│  (Python 常量定义，按模式/角色返回规则片段)                    │
└────────────────────────┬────────────────────────────────────┘
                         │ get_injection_prompt(role, mode)
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
   ┌───────────┐  ┌───────────┐  ┌───────────┐
   │  Legacy   │  │ Autonomous│  │   Review  │
   │  注入点   │  │  注入点   │  │  注入点   │
   └─────┬─────┘  └─────┬─────┘  └─────┬─────┘
         │              │              │
         ▼              ▼              ▼
   legacy.py       DevHandler    role-prompts/
   context dict    直接调用       coder-code-
   → json.dumps    _dispatch_     analysis.md
   → prompt        via_claude_
                   code
                   context dict
                   → json.dumps
                   → prompt
```

### 4.2 DevHandler 调用路径修复（关键）

**问题**：DevHandler 调用 `DispatcherAdapter.invoke()` → `facade._dispatch_through_v3(args)` → `args.autonomous=True` → 再次匹配 autonomous plugin → **无限递归**

**修复**：DevHandler 直接调用 `_dispatch_via_claude_code`，构造 `context` dict 注入 Ponytail 决策梯

```python
# scripts/autonomous/handlers/dev_handler.py（v2 修复）

def do_handle(self, iter_ctx) -> StageResult:
    # ... 现有 skill 检测逻辑 ...
    
    # 2. 构造任务描述
    task = iter_ctx.current_plan or f"完成 Objective: {iter_ctx.run_id}"
    
    # 3. 【修复】直接调用 _dispatch_via_claude_code（绕过递归）
    #    而非调用 DispatcherAdapter.invoke()（会无限递归）
    from dispatch.legacy import _dispatch_via_claude_code
    
    # 4. 构造 context（Ponytail 决策梯注入点）
    ponytail_prompt = self._ponytail_engine.get_injection_prompt(
        role="solo_coder",
        mode=self._ponytail_mode,
    )
    
    context = {
        'task_id': iter_ctx.run_id,
        'project_root': str(self._project_root),
        'timestamp': datetime.now().isoformat(),
        'iter_index': iter_ctx.iter_index,
        'karpathy_principles': {
            'think_before_coding': '明确假设、问清楚、不隐藏困惑',
            'simplicity_first': '最小代码、无 speculative features',
            'surgical_changes': '只改必要的、不改无关的',
            'goal_driven': '定义成功标准、验证检查点'
        },
        'ponytail_decision_ladder': ponytail_prompt,  # 【新增】决策梯注入
        'auto_skills': skills_payload,                # 【新增】skills 注入
    }
    
    # 5. 调用（agent_type 由 plan 阶段决定或默认 solo_coder）
    success = _dispatch_via_claude_code(
        agent_type="solo_coder",
        task=task,
        task_id=iter_ctx.run_id,
        project_root=str(self._project_root),
        progress={},
    )
```

**`_dispatch_via_claude_code` 的 prompt 注入点**（[legacy.py:174-184](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/scripts/dispatch/legacy.py#L174-184)）：
```python
context = {
    'task_id': actual_task_id,
    'project_root': project_root,
    ...
    'ponytail_decision_ladder': ponytail_prompt,  # 注入到 context
}
result = adapter.invoke_agent(agent_type, task, context)
# → _build_agent_prompt 把 context 用 json.dumps 拼到 prompt 末尾
```

### 4.3 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                    PonytailRulesetEngine                     │
│  (Python 常量：Dict[PonytailMode, str] + 角色子集选择)       │
└────────────────────────┬────────────────────────────────────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
   ┌───────────┐  ┌───────────┐  ┌───────────┐
   │  Legacy   │  │ Autonomous│  │   Review  │
   │  注入点   │  │  注入点   │  │  注入点   │
   └─────┬─────┘  └─────┬─────┘  └─────┬─────┘
         │              │              │
         ▼              ▼              ▼
   legacy.py       DevHandler    role-prompts/
   context dict    FixHandler    coder-code-
   → json.dumps    PlanHandler   analysis.md
   → prompt        VerifyHandler
                   直接调用
                   _dispatch_
                   via_claude_
                   code
```

### 4.4 新增文件清单

| 文件 | 类型 | 职责 |
|------|------|------|
| `scripts/ponytail/ruleset.py` | Python | 决策梯规则集引擎（Python 常量，无 markdown 解析） |
| `scripts/ponytail/mode_tracker.py` | Python | 模式跟踪（lite/full/ultra/off） |
| `scripts/ponytail/debt_collector.py` | Python | `ponytail:` 注释债务台账收割 |
| `scripts/ponytail/requirement_tracer.py` | Python | 需求文档功能点追溯（红线检测） |
| `scripts/ponytail/__init__.py` | Python | 包初始化 |
| `scripts/tests/test_ponytail_ruleset.py` | Python | 规则集引擎单元测试（15 用例） |
| `scripts/tests/test_ponytail_mode_tracker.py` | Python | 模式跟踪单元测试（15 用例） |
| `scripts/tests/test_ponytail_debt_collector.py` | Python | 债务收割单元测试（10 用例） |
| `scripts/tests/test_ponytail_redline.py` | Python | 红线违规检测（10 用例） |
| `scripts/tests/test_ponytail_ultra_guard.py` | Python | ultra 模式守护（6 用例） |
| `scripts/tests/test_ponytail_enforcer_extension.py` | Python | enforcer 扩展检测（含白名单） |
| `scripts/tests/test_ponytail_regression_phase18.py` | Python | Phase 18 回归专项 |
| `scripts/tests/test_ponytail_regression_v4_legacy.py` | Python | V4 legacy 回归 |
| `scripts/tests/test_ponytail_integration.py` | Python | 集成测试（全链路注入验证） |
| `scripts/tests/test_claude_code_subagent_adapter_prompt.py` | Python | adapter prompt 注入 + 线程安全 |
| `tests/scripts/run_ponytail_tests.sh` | Shell | 测试运行脚本 |

### 4.5 修改文件清单

| 文件 | 修改内容 |
|------|---------|
| `scripts/autonomous/handlers/dev_handler.py` | **修复递归**：直接调用 `_dispatch_via_claude_code`；注入决策梯到 context |
| `scripts/autonomous/handlers/fix_handler.py` | 同上修复 + 注入"只改必要的"约束 |
| `scripts/autonomous/handlers/plan_handler.py` | 注入 YAGNI 规划约束到 plan 文本 |
| `scripts/autonomous/handlers/verify_handler.py` | **新增**：ponytail 债务检测 + 红线违规检测 + 空 diff 检测 |
| `scripts/dispatch/legacy.py` | `context` dict 追加 `ponytail_decision_ladder` 字段 |
| `scripts/claude_code_subagent_adapter.py` | `_build_agent_prompt` 按角色注入决策梯（参数化，非改私有字段） |
| `scripts/karpathy_principle_enforcer.py` | 追加决策梯违规检测（按现有 `Dict[PrincipleType, List[Dict]]` 结构） |
| `scripts/plugins/autonomous.py` | `_build_stage_handlers` / `_build_components` 注入 ponytail_engine |
| `docs/spec/role-prompts/coder-code-analysis.md` | 追加决策梯审查检查表 |
| `docs/spec/CONSTITUTION.md` | 追加决策梯为不可协商项 |
| `SKILL.md` | 文档更新 |
| `skills-index.json` | 新增 `ponytailDecisionLadder` feature flag |

---

## 五、详细设计

### 5.1 PonytailRulesetEngine（Python 常量版，无 markdown 解析）

```python
# scripts/ponytail/ruleset.py

"""
Ponytail 决策梯规则集引擎。

设计目标（v2 修订）：
- 用 Python 常量定义规则（放弃 markdown 解析，降低复杂度）
- 按模式（lite/full/ultra）返回不同规则片段
- 按角色选择适用子集
- 与 Karpathy 原则叠加，不替换
- 线程安全：get_injection_prompt 接受参数，不修改实例状态
"""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class PonytailMode(str, Enum):
    """决策梯强度模式。"""
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
        """获取红线清单（供 verify_handler 检测使用）。"""
        return _RED_LINES


__all__ = ["PonytailRulesetEngine", "PonytailMode", "ROLE_INTENSITY"]
```

### 5.2 DevHandler 修复 + 注入（v2 核心变更）

```python
# scripts/autonomous/handlers/dev_handler.py（v2 修复）

class DevHandler(StageHandler):
    name = "dev"
    kind = "dev"

    def __init__(
        self,
        dispatcher_adapter=None,
        smart_confirmation=None,
        auto_skill_loader=None,
        ponytail_engine=None,        # 【新增】
        project_root=None,            # 【新增】_dispatch_via_claude_code 需要
        ponytail_mode=None,           # 【新增】可选模式覆盖
    ):
        self._dispatcher_adapter = dispatcher_adapter
        self._smart_confirmation = smart_confirmation
        self._auto_skill_loader = auto_skill_loader
        self._ponytail_engine = ponytail_engine
        self._project_root = project_root
        self._ponytail_mode = ponytail_mode

    def do_handle(self, iter_ctx) -> StageResult:
        """实际处理：直接调用 _dispatch_via_claude_code（修复递归）。"""
        # 1. 检测相关 skills
        skills_payload = []
        if self._auto_skill_loader is not None:
            # ... 现有逻辑 ...
            pass
        
        # 2. 构造任务描述
        task = iter_ctx.current_plan or f"完成 Objective: {iter_ctx.run_id}"
        
        # 3. 【修复】直接调用 _dispatch_via_claude_code（绕过递归）
        from dispatch.legacy import _dispatch_via_claude_code
        
        # 4. 构造 context（Ponytail 决策梯注入点）
        ponytail_prompt = ""
        if self._ponytail_engine is not None:
            ponytail_prompt = self._ponytail_engine.get_injection_prompt(
                role="solo_coder",
                mode=self._ponytail_mode,
            )
        
        context = {
            'task_id': iter_ctx.run_id,
            'project_root': str(self._project_root or "."),
            'timestamp': datetime.now().isoformat(),
            'iter_index': iter_ctx.iter_index,
            'karpathy_principles': {
                'think_before_coding': '明确假设、问清楚、不隐藏困惑',
                'simplicity_first': '最小代码、无 speculative features',
                'surgical_changes': '只改必要的、不改无关的',
                'goal_driven': '定义成功标准、验证检查点'
            },
            'ponytail_decision_ladder': ponytail_prompt,
            'auto_skills': skills_payload,
        }
        
        # 5. 调用
        success = _dispatch_via_claude_code(
            agent_type="solo_coder",
            task=task,
            task_id=iter_ctx.run_id,
            project_root=str(self._project_root or "."),
            progress={},
        )
        
        if success:
            return StageResult(
                kind="success",
                summary=f"dev 执行成功",
                artifacts={"output": "", "tokens": 0, "skills_used": []},
            )
        return StageResult(
            kind="retriable",
            summary="dev 执行失败（可重试）",
        )
```

### 5.3 FixHandler 修复 + 注入

```python
# scripts/autonomous/handlers/fix_handler.py（v2 修复）

def do_handle(self, iter_ctx) -> StageResult:
    # ... 构造 fix_task ...
    fix_task = f"修复以下错误：\n\n"
    for cat in categories:
        fix_task += f"- [{cat.kind}] {cat.message}\n"
    
    # 【新增】注入"只改必要的"约束
    fix_task += "\n## 修复约束\n"
    fix_task += "- 只修改导致错误的代码，不要溢出修改\n"
    fix_task += "- 不要顺手重构无关代码\n"
    fix_task += "- 修复后标记：`# ponytail: fix-only, no refactor`\n"
    
    # 【修复】直接调用 _dispatch_via_claude_code
    from dispatch.legacy import _dispatch_via_claude_code
    
    ponytail_prompt = ""
    if self._ponytail_engine is not None:
        ponytail_prompt = self._ponytail_engine.get_injection_prompt(role="solo_coder")
    
    context = {
        'task_id': iter_ctx.run_id,
        'project_root': str(self._project_root or "."),
        'ponytail_decision_ladder': ponytail_prompt,
    }
    
    success = _dispatch_via_claude_code(
        agent_type="solo_coder",
        task=fix_task,
        task_id=iter_ctx.run_id,
        project_root=str(self._project_root or "."),
        progress={},
    )
```

### 5.4 VerifyHandler 新增 ponytail 检测

```python
# scripts/autonomous/handlers/verify_handler.py（v2 新增）

def do_handle(self, iter_ctx) -> StageResult:
    # ... 现有测试执行 + 安全扫描 ...
    
    # 【新增】Ponytail 债务台账检测
    if self._debt_collector is not None:
        entries = self._debt_collector.collect(self._project_root)
        no_trigger_count = sum(1 for e in entries if e.no_trigger)
        if no_trigger_count >= 3:
            return StageResult(
                kind="retriable",
                summary=f"ponytail 债务 no_trigger 项 {no_trigger_count} 个，需清理",
            )
    
    # 【新增】红线违规检测
    if self._ponytail_engine is not None:
        red_lines = self._ponytail_engine.get_red_lines()
        # 调用 karpathy_principle_enforcer 扫描
        # ...
    
    # 【新增】空 diff 检测（架构师评审 P0）
    if iter_ctx.agent_output and not iter_ctx.agent_output.strip():
        return StageResult(
            kind="retriable",
            summary="dev 阶段产出为空，跳过 fix，进入下一轮",
        )
```

### 5.5 Legacy 路径注入

```python
# scripts/dispatch/legacy.py 修改（_dispatch_via_claude_code 内）

context = {
    'task_id': actual_task_id,
    'project_root': project_root,
    'timestamp': datetime.now().isoformat(),
    'karpathy_principles': {
        'think_before_coding': '明确假设、问清楚、不隐藏困惑',
        'simplicity_first': '最小代码、无 speculative features',
        'surgical_changes': '只改必要的、不改无关的',
        'goal_driven': '定义成功标准、验证检查点'
    },
    # 【新增】Ponytail 决策梯（从 context 参数透传，或 lazy 初始化）
    'ponytail_decision_ladder': context.get('ponytail_decision_ladder', '') if context else '',
}
```

### 5.6 角色差异化注入（线程安全版）

```python
# scripts/claude_code_subagent_adapter.py _build_agent_prompt 修改

def _build_agent_prompt(self, agent_type: str, task: str,
                       context: Optional[Dict] = None) -> str:
    role_prompt = self._get_role_prompt(agent_type)
    
    # 【新增】按角色注入决策梯（参数化，非改私有字段）
    ponytail_injection = ""
    if context and 'ponytail_decision_ladder' in context:
        # context 中已有决策梯（由 DevHandler/Legacy 注入）
        ponytail_injection = context['ponytail_decision_ladder']
    elif context and '_ponytail_engine' in context:
        # 兜底：从 engine 按角色生成
        engine = context['_ponytail_engine']
        ponytail_injection = engine.get_injection_prompt(role=agent_type)
    
    prompt = f"""{role_prompt}

## 任务
{task}

## 要求
1. 遵循 Karpathy 四大核心原则：
   - Think Before Coding: 明确假设，问清楚，不隐藏困惑
   - Simplicity First: 最小代码，无 speculative features
   - Surgical Changes: 只改必要的，不改无关的
   - Goal-Driven: 定义成功标准，验证检查点

{ponytail_injection}
"""
    if context:
        prompt += f"\n## 上下文\n{json.dumps(context, indent=2, ensure_ascii=False)}\n"
    
    return prompt
```

### 5.7 模式跟踪

```python
# scripts/ponytail/mode_tracker.py（完整实现，无 pass）

import json
import os
import re
from pathlib import Path


class ModeTracker:
    """模式跟踪器（线程安全文件操作）。"""
    
    _FLAG_FILE = Path.home() / ".trae" / ".ponytail-active"
    _CONFIG_FILE = Path.home() / ".trae" / "ponytail.json"
    
    VALID_MODES = {"off", "lite", "full", "ultra"}
    
    @classmethod
    def get_default_mode(cls) -> str:
        """获取默认模式（env > config file > full）。"""
        # 1. 环境变量
        env_mode = os.environ.get("PONYTAIL_DEFAULT_MODE", "").lower()
        if env_mode in cls.VALID_MODES:
            return env_mode
        # 2. 配置文件
        try:
            if cls._CONFIG_FILE.exists():
                config = json.loads(cls._CONFIG_FILE.read_text(encoding="utf-8"))
                mode = str(config.get("defaultMode", "")).lower()
                if mode in cls.VALID_MODES:
                    return mode
        except (json.JSONDecodeError, OSError):
            pass
        # 3. 默认
        return "full"
    
    @classmethod
    def set_mode(cls, mode: str) -> None:
        """设置当前模式（原子写入）。"""
        if mode not in cls.VALID_MODES:
            return
        cls._FLAG_FILE.parent.mkdir(parents=True, exist_ok=True)
        # 原子写入：先写 tmp 再 rename
        tmp = cls._FLAG_FILE.with_suffix(".tmp")
        tmp.write_text(mode, encoding="utf-8")
        tmp.replace(cls._FLAG_FILE)
    
    @classmethod
    def get_current_mode(cls) -> str:
        """获取当前模式。"""
        try:
            if cls._FLAG_FILE.exists():
                mode = cls._FLAG_FILE.read_text(encoding="utf-8").strip().lower()
                if mode in cls.VALID_MODES:
                    return mode
        except OSError:
            pass
        return cls.get_default_mode()
    
    @classmethod
    def clear_mode(cls) -> None:
        """清除模式（回到默认）。"""
        try:
            if cls._FLAG_FILE.exists():
                cls._FLAG_FILE.unlink()
        except OSError:
            pass
    
    @classmethod
    def parse_user_command(cls, user_input: str) -> str:
        """解析用户输入中的 /ponytail 命令。
        
        Returns:
            str: 解析出的模式（off/lite/full/ultra），无命令返回当前模式
        """
        if not user_input:
            return cls.get_current_mode()
        
        # /ponytail ultra → ultra
        m = re.match(r'^[/@$]ponytail\s+(lite|full|ultra|off)\b', user_input, re.IGNORECASE)
        if m:
            return m.group(1).lower()
        
        # /ponytail（无参数）→ 当前模式
        if re.match(r'^[/@$]ponytail\b', user_input, re.IGNORECASE):
            return cls.get_current_mode()
        
        # stop ponytail / normal mode → off
        if re.search(r'\b(stop\s+ponytail|normal\s+mode)\b', user_input, re.IGNORECASE):
            return "off"
        
        return cls.get_current_mode()
```

### 5.8 债务台账收割（完整实现）

```python
# scripts/ponytail/debt_collector.py

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set


@dataclass
class DebtEntry:
    """单条债务记录。"""
    file: str
    line: int
    content: str
    has_ceiling: bool       # 是否标注了已知上限
    has_upgrade_path: bool  # 是否标注了升级路径
    no_trigger: bool        # 是否缺少升级触发条件（腐烂风险）


class DebtCollector:
    """债务台账收割器。"""
    
    # 匹配 ponytail: 注释（支持 # 和 //）
    _DEBT_RE = re.compile(r'(#|//)\s*ponytail:\s*(.+)')
    
    # 默认排除目录
    _DEFAULT_EXCLUDE = {"node_modules", ".git", "build", "__pycache__", ".venv", "venv"}
    
    # 已知上限关键词
    _CEILING_KEYWORDS = {"lock", "o(n", "o(n²", "o(n^2", "scan", "heuristic", "naive", "global"}
    
    # 升级路径关键词
    _UPGRADE_KEYWORDS = {"upgrade", "if", "when", "switch", "replace", "migrate", "per-account"}
    
    def collect(
        self,
        project_root: Path,
        exclude_dirs: Optional[Set[str]] = None,
    ) -> List[DebtEntry]:
        """扫描项目中的 ponytail: 注释。
        
        Args:
            project_root: 项目根目录
            exclude_dirs: 排除目录（默认 node_modules/.git/build 等）
            
        Returns:
            List[DebtEntry]: 债务记录列表
        """
        excludes = exclude_dirs or self._DEFAULT_EXCLUDE
        entries: List[DebtEntry] = []
        
        for path in project_root.rglob("*"):
            # 排除目录
            if any(part in excludes for part in path.parts):
                continue
            if not path.is_file():
                continue
            # 只扫描代码文件
            if path.suffix not in {".py", ".js", ".ts", ".jsx", ".tsx", ".java",
                                   ".go", ".rs", ".c", ".cpp", ".h", ".sh"}:
                continue
            
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            
            for i, line in enumerate(lines, 1):
                m = self._DEBT_RE.search(line)
                if m:
                    content = m.group(2).strip()
                    content_lower = content.lower()
                    has_ceiling = any(kw in content_lower for kw in self._CEILING_KEYWORDS)
                    has_upgrade = any(kw in content_lower for kw in self._UPGRADE_KEYWORDS)
                    no_trigger = not has_upgrade
                    
                    entries.append(DebtEntry(
                        file=str(path.relative_to(project_root)),
                        line=i,
                        content=content,
                        has_ceiling=has_ceiling,
                        has_upgrade_path=has_upgrade,
                        no_trigger=no_trigger,
                    ))
        
        return entries
    
    def format_report(self, entries: List[DebtEntry]) -> str:
        """格式化债务报告。"""
        if not entries:
            return "No ponytail: debt. Clean ledger."
        
        lines = []
        no_trigger_count = 0
        for e in entries:
            lines.append(f"{e.file}:{e.line} — {e.content}")
            if e.no_trigger:
                no_trigger_count += 1
        
        lines.append(f"\n{len(entries)} markers, {no_trigger_count} with no trigger.")
        return "\n".join(lines)
```

### 5.9 karpathy_principle_enforcer 追加检测（按现有结构）

```python
# scripts/karpathy_principle_enforcer.py 追加（按 Dict[PrincipleType, List[Dict]] 结构）

# 在现有 VIOLATION_PATTERNS 的 SIMPLICITY_FIRST 列表中追加：
{
    "pattern": r"class\s+\w*(Manager|Handler|Controller|Service)\b.*#\s*ponytail",
    "severity": "MEDIUM",
    "message": "疑似 YAGNI 违规：创建了未被要求的抽象类",
},
{
    "pattern": r"(import|from)\s+\w+.*#\s*ponytail:\s*new\s+dep",
    "severity": "HIGH",
    "message": "疑似新增不必要依赖",
},

# 在 SURGICAL_CHANGES 列表中追加（带白名单，避免误报合法 pass）：
{
    "pattern": r"^\s*pass\s*$",
    "severity": "LOW",
    "message": "疑似占位代码（pass 无 ponytail 标记）",
    "whitelist_context": ["class ", "def ", "except", "try:"],  # 这些上下文中的 pass 合法
},

# 新增 PrincipleType.PONYTAIL_REDLINE（如果枚举允许扩展）
# 或追加到 SIMPLICITY_FIRST：
{
    "pattern": r"from\s+unittest\.mock\s+import\s+Mock.*(?!\s*#\s*ponytail)",
    "severity": "CRITICAL",
    "message": "真实业务逻辑被 mock 替代（红线违规）",
    "file_whitelist": ["tests/", "test_"],  # 测试文件白名单
},
```

---

## 六、与项目规则的冲突调和

### 6.1 优先级规则（架构师评审 P0 修正）

**正确优先级**：
```
项目规则（woagent.md / user_rules）
  > Karpathy 原则（CONSTITUTION.md 不可协商项）
    > Ponytail 决策梯（作为 Simplicity First 的可执行步骤）
      > 默认行为
```

Ponytail 决策梯定位为 **Karpathy `Simplicity First` 原则的"执行手册"**，而非独立层级。

### 6.2 核心冲突调和

| 项目规则 | ponytail 理念 | 冲突点 | 调和方案 |
|---------|-------------|-------|---------|
| "禁止简化、模拟、占位" | "能删的全删掉" | ponytail 可能鼓励"简化" | 决策梯红线第 7-16 条明确不可简化项 |
| "完全需要根据需求实现具体的功能" | "YAGNI：不需要就跳过" | YAGNI 可能跳过需求功能 | YAGNI 只适用于"推测性需求"，**需求文档显式列出**是唯一判据 |
| "代码函数和关键逻辑都需要注释" | "解释比代码长 → 删解释" | ponytail 鼓励少注释 | "删解释"仅针对"未要求的散文式说明"，不针对"代码注释" |
| "严禁未得到批准的简化实现" | "第一个能工作的懒方案就是正确方案" | 直接冲突 | 懒方案不可越过"禁止简化"红线；ultra 需显式启用 |
| "测试全部放到 tests 目录" | "非平凡逻辑留一个可运行检查" | 测试粒度差异 | 保留项目规则（完整测试套件），ponytail 的"一个检查"是下限 |

### 6.3 冲突时的显式声明

当 user_rules 与决策梯冲突时，**默认按 user_rules 执行，并在输出中显式声明**：
```
[项目规则覆盖] 已触发项目规则覆盖，跳过决策梯第 N 阶
```
而非静默执行。

---

## 七、Token 预算控制（架构师评审 P1）

### 7.1 Token 预算

- Ponytail 注入 prompt **不超过总 prompt 的 15%**
- 单次注入上限：**1200 tokens**
- ultra 模式压缩版：**600 tokens**（只注入红线 + 一行决策梯）

### 7.2 注入位置

- **system prompt**（会被缓存，降低成本）：决策梯主体 + 红线
- **user prompt**（每次变化）：模式标记 + 角色标记

### 7.3 跨迭代去重

同一 `run_id` 内决策梯内容不变，缓存为 system prompt 一次，后续迭代不重复注入。

---

## 八、Benchmark 机制（测试专家评审修订）

### 8.1 测试任务（修订：移除错误任务）

| 任务 | 语言 | 预期效果 |
|------|------|---------|
| 邮箱校验函数 | Python | 自定义正则 → `email.utils.parseaddr` |
| 防抖函数 | JS | 自定义实现 → 标准库/平台特性 |
| CSV 求和 | Python | pandas → `sum(float(r['amount']) for r in csv.DictReader(...))` |
| 日期选择器 | React | 自定义组件 → `<input type="date">` |
| 限流器 | Python | 自定义类 → `functools.lru_cache` 或标准库 |

### 8.2 对照组（新增 lite arm）

| Arm | 说明 |
|-----|------|
| baseline | 无决策梯注入 |
| karpathy_only | 仅 Karpathy 原则（现有） |
| ponytail_lite | 决策梯 lite 模式 |
| ponytail_full | 决策梯 full 模式 |
| ponytail_ultra | 决策梯 ultra 模式 |

### 8.3 指标（修订：可量化）

| 指标 | 类型 | 说明 |
|------|------|------|
| `code_loc` | 确定性 | **逻辑行数**（扣除空行/注释/纯括号行） |
| `correct` | 确定性 | 功能正确性（**固化测试用例集**） |
| `tokens` | API 遥测 | 总 token 消耗 |
| `cost` | API 遥测 | 总成本 |
| `latency` | API 遥测 | 端到端延迟 |
| `red_line_violations` | 确定性 | 红线违规次数（用扩展后的 enforcer + requirement_tracer 检测） |
| `injection_token_ratio` | 确定性 | 注入 prompt token 占比（阈值 ≤ 15%） |

### 8.4 统计显著性

每个 arm 至少跑 **10 次**，报告均值 ± 标准差，用 Mann-Whitney U 检验验证差异显著性。

---

## 九、实施计划（v2 修订）

### Phase 19.0：基线测试 + 回滚预案（新增）

| 步骤 | 内容 | 产出 |
|------|------|------|
| 0.1 | 跑现有 Phase 18 全部测试，建立基线 | 基线报告 |
| 0.2 | 制定回滚预案（git revert 策略 + feature flag） | 回滚文档 |

### Phase 19.1：规则集引擎（Python 常量版）

| 步骤 | 内容 | 产出 |
|------|------|------|
| 1.1 | 实现 `scripts/ponytail/ruleset.py`（Python 常量，无 pass） | 规则集引擎 |
| 1.2 | 实现 `scripts/ponytail/mode_tracker.py`（完整实现） | 模式跟踪 |
| 1.3 | 编写 `test_ponytail_ruleset.py`（15 用例） | 单元测试 |
| 1.4 | 编写 `test_ponytail_mode_tracker.py`（15 用例） | 单元测试 |

### Phase 19.2：注入点改造（先 DevHandler，端到端验证后再铺开）

| 步骤 | 内容 | 产出 |
|------|------|------|
| 2.1 | **修复 DevHandler 递归**：改为直接调用 `_dispatch_via_claude_code` | 修复文件 |
| 2.2 | DevHandler 注入决策梯到 context | 修改文件 |
| 2.3 | 端到端验证：跑一次 autonomous dev，确认 LLM 收到决策梯 | 验证报告 |
| 2.4 | 改造 FixHandler（同上修复 + 注入） | 修改文件 |
| 2.5 | 改造 PlanHandler（注入 YAGNI 约束） | 修改文件 |
| 2.6 | 改造 VerifyHandler（债务检测 + 红线检测 + 空 diff 检测） | 修改文件 |
| 2.7 | 改造 `legacy.py`（context 追加决策梯） | 修改文件 |
| 2.8 | 改造 `claude_code_subagent_adapter.py`（角色差异化，参数化） | 修改文件 |
| 2.9 | 改造 `plugins/autonomous.py`（_build_stage_handlers 注入 ponytail_engine） | 修改文件 |
| 2.10 | 编写 `test_ponytail_integration.py`（全链路注入验证） | 集成测试 |
| 2.11 | 编写 `test_ponytail_regression_phase18.py` | 回归测试 |
| 2.12 | 编写 `test_ponytail_regression_v4_legacy.py` | 回归测试 |
| 2.13 | 编写 `test_claude_code_subagent_adapter_prompt.py` | 单元测试 |

### Phase 19.3：债务台账与红线检测

| 步骤 | 内容 | 产出 |
|------|------|------|
| 3.1 | 实现 `scripts/ponytail/debt_collector.py`（完整实现） | 债务收割 |
| 3.2 | 实现 `scripts/ponytail/requirement_tracer.py` | 需求追溯 |
| 3.3 | 追加 `karpathy_principle_enforcer.py` 检测模式（按现有结构） | 违规扫描 |
| 3.4 | 编写 `test_ponytail_debt_collector.py`（10 用例） | 单元测试 |
| 3.5 | 编写 `test_ponytail_redline.py`（10 用例） | 单元测试 |
| 3.6 | 编写 `test_ponytail_ultra_guard.py`（6 用例） | 单元测试 |
| 3.7 | 编写 `test_ponytail_enforcer_extension.py` | 单元测试 |
| 3.8 | 更新 `coder-code-analysis.md`（审查检查表） | 审查 prompt |
| 3.9 | 更新 `CONSTITUTION.md`（不可协商项） | 宪法 |

### Phase 19.4：文档

| 步骤 | 内容 | 产出 |
|------|------|------|
| 4.1 | 更新 `SKILL.md` + `skills-index.json` | 特性文档 |
| 4.2 | 编写 `docs/guides/PONYTAIL_GUIDE.md` | 用户指南 |

### Phase 19.5：全量测试 + 回归验证 + 提交评审

| 步骤 | 内容 | 产出 |
|------|------|------|
| 5.1 | 运行全部 ponytail 测试 | 测试报告 |
| 5.2 | 运行 Phase 18 回归测试 | 回归报告 |
| 5.3 | 运行 V2/V3 回归测试 | 回归报告 |
| 5.4 | 运行 lint + typecheck | 质量报告 |
| 5.5 | 生成测试报告 + 提交评审（**不自动 commit/push**） | 评审材料 |

---

## 十、风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| DevHandler 递归修复引入新 bug | 中 | 高 | Phase 19.2 步骤 2.3 端到端验证 |
| 决策梯导致 AI 跳过需求功能 | 中 | 高 | 红线第 8 条 + requirement_tracer 检测 |
| ultra 模式过度删除代码 | 中 | 高 | ultra 需显式启用 + autonomous 禁用 ultra + 红线保护 |
| 注入 prompt 过长导致 token 膨胀 | 低 | 中 | token 预算 ≤ 15% + 压缩版 + 跨迭代去重 |
| 与现有 Karpathy 原则重复/冲突 | 低 | 低 | 决策梯是 Karpathy 的可执行步骤 |
| `PONYTAIL_NO_COMMENT` 正则误报 | 中 | 中 | 白名单机制（class/except/try 上下文） |
| 多角色并发注入竞态 | 低 | 中 | 参数化注入，不修改实例状态 |
| ModeTracker 标志文件跨会话残留 | 低 | 低 | ultra 不持久化（单任务生效） |

---

## 十一、验收标准（测试专家评审修订，全部可量化）

### 11.1 功能验收

1. `PonytailRulesetEngine.get_injection_prompt(role="solo_coder", mode=FULL)` 返回非空决策梯 prompt，长度 > 200 字符，包含 6 个台阶标题
2. `PonytailRulesetEngine.get_injection_prompt(role="test_expert", mode=OFF)` 返回空字符串
3. `DevHandler.do_handle()` 调用 `_dispatch_via_claude_code`（非 `DispatcherAdapter.invoke`），context 包含 `ponytail_decision_ladder` 字段且非空
4. `FixHandler` 的 `fix_task` 包含"只改必要的"约束
5. `VerifyHandler` 检测 ponytail 债务 + 红线违规 + 空 diff
6. `legacy.py` 的 context 包含 `ponytail_decision_ladder` 字段
7. 角色差异化：solo_coder=FULL, architect=FULL, test_expert=LITE, product_manager=OFF, ui_designer=LITE

### 11.2 红线验收

1. 决策梯 prompt 包含"不可简化红线"段落
2. 红线包含 16 条（含"真实业务逻辑禁止 mock/占位/stub"）
3. `karpathy_principle_enforcer` 能检测 mock 在非测试文件中的使用（TC-REDLINE-DETECT-01）
4. `requirement_tracer` 能检测需求文档功能未实现（TC-REDLINE-DETECT-02）
5. ultra 模式下红线违反触发硬阻断

### 11.3 测试验收

1. `test_ponytail_ruleset.py`：15 用例，行覆盖 + 分支覆盖 ≥ 90%
2. `test_ponytail_mode_tracker.py`：15 用例
3. `test_ponytail_debt_collector.py`：10 用例
4. `test_ponytail_redline.py`：10 用例
5. `test_ponytail_ultra_guard.py`：6 用例
6. `test_ponytail_enforcer_extension.py`：含白名单验证
7. `test_ponytail_integration.py`：验证 Plan→Dev→Verify→Fix 4 阶段均注入 ponytail
8. `test_ponytail_regression_phase18.py`：Phase 18 全部通过
9. `test_ponytail_regression_v4_legacy.py`：V4 legacy 全部通过
10. `test_claude_code_subagent_adapter_prompt.py`：角色差异化 + 线程安全（100 次并发）
11. 现有 Phase 18 测试全部通过（无回归）
12. V2/V3 回归测试全部通过

### 11.4 Benchmark 验收

1. ponytail_full arm 的 `code_loc`（逻辑行数）比 baseline 减少 ≥ 50%，**前置条件：功能正确性 100%**
2. `red_line_violations` = 0（用扩展后的 enforcer + requirement_tracer 检测）
3. `injection_token_ratio` ≤ 15%
4. 每个 arm 至少 10 次，Mann-Whitney U 检验 p < 0.05

---

## 十二、参考

- [ponytail GitHub](https://github.com/DietrichGebert/ponytail) - 原项目
- [ponytail SKILL.md](https://github.com/DietrichGebert/ponytail/blob/main/skills/ponytail/SKILL.md) - 决策梯原文
- [Karpathy 四大核心原则](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/guides/KARPATHY_PRINCIPLES.md) - 现有原则
- [CONSTITUTION.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/spec/CONSTITUTION.md) - 项目宪法
- [AUTONOMOUS_MODE_GUIDE.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/guides/AUTONOMOUS_MODE_GUIDE.md) - autonomous 模式指南
- [dispatcher_adapter.py](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/scripts/autonomous/dispatcher_adapter.py) - Phase 0 验证焦点
- [legacy.py](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/scripts/dispatch/legacy.py) - 真正的 prompt 注入点
