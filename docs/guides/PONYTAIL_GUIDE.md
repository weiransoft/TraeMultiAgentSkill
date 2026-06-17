# Ponytail 决策梯用户指南

> **版本**: v2.6 | **状态**: Phase 19 完整实现
> **来源**: Ponytail 项目 6 步决策梯 + 项目规则 16 条不可简化红线
> **目的**: 在 Karpathy Simplicity First 原则之上，提供可执行的"写代码前先停一停"决策梯

## 1. 核心理念

**"少写多余代码"** — 每写一行代码都是一笔债务。决策梯强制你在写代码前先问 6 个问题，停在第一个能解决问题的台阶上。

**优先级层次**（高 → 低）:
1. 项目规则（16 条不可简化红线）
2. Karpathy 四大核心原则
3. Ponytail 决策梯（6 步）
4. 默认行为

## 2. 6 步决策梯

写任何代码前，按顺序停在第一个满足的台阶上：

| 台阶 | 问题 | 行动 | 红线（不可跳过） |
|------|------|------|-----------------|
| 1. YAGNI | 这东西真的需要存在吗？ | 推测性需求 = 跳过，用一行注释说明 | 用户明确要求的功能；需求文档列出的功能 |
| 2. 标准库优先 | 语言标准库能搞定？ | 直接用标准库，标注 `# ponytail: stdlib covers this` | 标准库功能不满足安全/性能要求 |
| 3. 平台原生 | 运行时平台自带功能能覆盖？ | 用平台原生特性（如 `<input type="date">` 替代 picker 库） | 平台特性有已知 bug 或安全漏洞 |
| 4. 复用现有 | 已安装的依赖能解决？ | 复用现有依赖，不新增依赖 | 现有依赖有 license 冲突或安全漏洞 |
| 5. 一行优先 | 能写成一行？ | 写成一行，不牺牲可读性 | 涉及金钱/安全/并发的逻辑 |
| 6. 最小可行 | 以上都不行 | 写最少能做工作的代码 | 项目规则"禁止简化、模拟、占位"优先级高于本台阶 |

**决策原则**: 决策梯是反射，不是研究项目。两个台阶都成立 → 取更高的那个继续。第一个能工作的懒方案就是正确方案。

## 3. 16 条不可简化红线

以下内容永远不在决策梯的"可跳过"范围内：

### 原始 Ponytail 红线（6 条）
1. 信任边界的输入校验
2. 防止数据丢失的错误处理
3. 安全措施
4. 无障碍基础
5. 用户明确要求保留的功能
6. 真实硬件的校准旋钮

### 项目规则红线（10 条）
7. 真实业务逻辑 — 禁止用 mock/占位/stub 替代
8. 需求文档规定的功能 — 禁止跳过或简化
9. 非平凡逻辑必须留一个可运行检查
10. 并发安全代码 — Lock/Atomic/synchronized 不可简化
11. 真实错误处理 — 禁止 `except: pass` 吞异常
12. 日志与审计 — 关键路径日志不可删除
13. 配置与密钥管理 — 密钥读取、配置校验不可简化
14. 数据库事务边界 — 事务提交/回滚不可简化
15. API 契约 — 公开 API 签名/返回格式不可单方面简化
16. 隐私数据处理 — PII 数据处理不可简化

## 4. 三种强度模式

| 模式 | 说明 | 注入内容 | 适用角色 |
|------|------|---------|---------|
| `lite` | 精简版 | 6 步决策梯（无红线详情） | test_expert, ui_designer |
| `full`（默认） | 完整版 | 6 步 + 16 条红线 + 输出规范 | solo_coder, architect |
| `ultra` | YAGNI 极端主义 | full + 额外约束（无争论、硬阻断红线） | 手动指定（autonomous 自动降级为 full） |

### 角色强度映射

| 角色 | 默认强度 | 理由 |
|------|---------|------|
| solo_coder | FULL | 主要代码编写者，需要完整决策梯 |
| architect | FULL | 架构决策需要完整红线意识 |
| test_expert | LITE | 测试代码允许 mock，精简决策梯即可 |
| product_manager | OFF | 不写代码，无需注入 |
| ui_designer | LITE | UI 代码偏向样式，精简决策梯即可 |

### ULTRA 模式安全机制

在 autonomous 模式下，ULTRA 会被自动降级为 FULL，原因：
- ULTRA 模式的 YAGNI 极端主义可能与"需求文档规定的功能不可跳过"冲突
- autonomous 模式无法人工干预，需要保守策略

## 5. 使用方式

### 5.1 自动注入（autonomous 模式）

在 autonomous 模式中，决策梯自动注入到所有 dev/fix/plan 阶段的 prompt 中：

```python
# plugins/autonomous.py 中自动创建 PonytailRulesetEngine
# 并传递给所有 handler（dev/fix/plan/verify）
# 无需手动配置
```

### 5.2 手动切换模式

在对话中使用 `/ponytail` 命令：

```bash
/ponytail ultra    # 切换到 ULTRA 模式（YAGNI 极端主义）
/ponytail full     # 切换到 FULL 模式（默认）
/ponytail lite     # 切换到 LITE 模式（精简）
/ponytail off      # 关闭决策梯注入
/ponytail          # 查看当前模式
```

### 5.3 环境变量

```bash
# 环境变量优先级最高
export PONYTAIL_MODE=ultra
```

### 5.4 配置文件

在项目根目录创建 `.ponytail_mode` 文件：

```bash
echo "full" > .ponytail_mode
```

**优先级**: 环境变量 > 配置文件 > 默认值（full）

## 6. 债务台账

### 6.1 标记故意简化

在代码中使用 `# ponytail:` 注释标记故意简化：

```python
# 简单标记
cache = {}  # ponytail: 临时内存缓存，单进程场景

# 带上限和升级路径
results = []  # ponytail: O(n) scan, upgrade to index when n > 10000
```

### 6.2 DebtCollector 自动扫描

`DebtCollector` 在 verify 阶段自动扫描项目中的 `ponytail:` 注释：

- **有升级路径**（包含 `upgrade`/`if`/`when`/`switch`/`replace`/`migrate`/`trigger` 等关键词）→ 正常债务
- **无升级路径**（`no_trigger`）→ 腐烂风险债务
- **阈值告警**: 当 `no_trigger` 债务超过 3 条时，verify 阶段返回 retriable，要求清理

### 6.3 上限关键词识别

以下关键词被视为"有上限"的债务（非 `no_trigger`）：
`lock`, `o(n)`, `o(n²)`, `o(n^2)`, `scan`, `heuristic`, `naive`, `global`

## 7. 需求追踪

### 7.1 标记需求

在需求文档中使用 `[REQ-XXX]` 标记：

```markdown
## 功能需求

[REQ-001] 用户登录功能
[REQ-002] 密码重置功能
[REQ-003] 数据导出功能
```

### 7.2 RequirementTracer 追踪

`RequirementTracer` 解析需求文档中的 `[REQ-XXX]` 标记，并在代码中搜索实现：

- **中文关键词提取**: 自动提取 2-3 字子串（如"用户登录功能" → "用户"、"登录"、"功能"等）
- **实现检测**: 代码中匹配 ≥50% 的关键词视为已实现
- **报告**: 输出已实现/未实现/无需求三条目列表

## 8. 注入点架构

### 8.1 注入链路

```
用户输入
  ↓
plugins/autonomous.py（_build_components）
  ├─ 创建 PonytailRulesetEngine
  ├─ 解析 /ponytail 命令
  ├─ ULTRA → FULL 降级（autonomous 安全）
  └─ 创建 DebtCollector
  ↓
_build_stage_handlers
  ├─ PlanHandler(ponytail_engine)    → 注入 YAGNI 规划约束
  ├─ DevHandler(ponytail_engine)     → 注入完整决策梯
  ├─ FixHandler(ponytail_engine)     → 注入"只改必要的"修复约束
  └─ VerifyHandler(debt_collector)   → 债务检测 + 红线检测
  ↓
_dispatch_via_claude_code（ponytail_prompt 参数）
  ↓
ClaudeCodeSubAgentAdapter._build_agent_prompt
  ├─ 优先: context['ponytail_decision_ladder']
  └─ 兜底: context['_ponytail_engine'].get_injection_prompt(role)
  ↓
LLM Prompt（包含 Karpathy 原则 + Ponytail 决策梯 + 上下文）
```

### 8.2 线程安全保证

- `PonytailRulesetEngine.get_injection_prompt()` 是纯函数，不修改实例状态
- `ClaudeCodeSubAgentAdapter._build_agent_prompt()` 通过参数接收决策梯，不修改实例字段
- `ModeTracker.set_mode()` 使用 `threading.Lock()` + 线程唯一临时文件保证原子性
- 100 并发调用测试验证无竞争条件

## 9. 测试

### 9.1 运行全部测试

```bash
# 运行全部 Ponytail 测试（10 个文件，98 个测试用例）
bash scripts/tests/scripts/run_ponytail_tests.sh
```

### 9.2 测试文件清单

| 文件 | 测试数 | 覆盖内容 |
|------|--------|---------|
| test_ponytail_ruleset.py | 18 | 规则集引擎核心逻辑 |
| test_ponytail_mode_tracker.py | 15 | 模式追踪 + 并发安全 |
| test_ponytail_debt_collector.py | 10 | 债务台账扫描 |
| test_ponytail_redline.py | 10 | 16 条红线完整性 |
| test_ponytail_enforcer_extension.py | 8 | enforcer 扩展模式 |
| test_ponytail_ultra_guard.py | 6 | ULTRA 模式安全降级 |
| test_ponytail_integration.py | 8 | 注入链路集成测试 |
| test_ponytail_regression_phase18.py | 6 | Phase 18 回归兼容 |
| test_ponytail_regression_v4_legacy.py | 5 | v4 legacy 回归兼容 |
| test_claude_code_subagent_adapter_prompt.py | 12 | 适配器注入 + 线程安全 |

## 10. 故障排查

### 10.1 决策梯未注入

**症状**: LLM 输出中看不到 Ponytail 决策梯内容

**排查**:
1. 检查 `ModeTracker.get_current_mode()` 是否返回 `off`
2. 检查角色是否为 `product_manager`（OFF 模式不注入）
3. 检查 `context['ponytail_decision_ladder']` 是否为空字符串

### 10.2 ULTRA 模式不生效

**症状**: 设置了 `/ponytail ultra` 但行为像 FULL

**原因**: autonomous 模式下 ULTRA 自动降级为 FULL（安全机制）

**解决**: 如需 ULTRA 行为，在非 autonomous 模式下使用

### 10.3 债务告警频繁

**症状**: verify 阶段频繁返回 retriable，提示 `no_trigger` 债务过多

**解决**:
1. 为 `# ponytail:` 注释添加升级路径（如 `# ponytail: temp, upgrade to Redis when scale`）
2. 或添加上限标记（如 `# ponytail: O(n) scan, lock until n > 10000`）
3. 清理已不再需要的简化标记

## 11. 相关文档

- [PONYTAIL_INTEGRATION_PLAN.md](../dev/PONYTAIL_INTEGRATION_PLAN.md) — 完整实现计划
- [KARPATHY_PRINCIPLES.md](KARPATHY_PRINCIPLES.md) — Karpathy 四大核心原则
- [CONSTITUTION.md](../spec/CONSTITUTION.md) — 不可妥协项
- [AUTONOMOUS_MODE_GUIDE.md](AUTONOMOUS_MODE_GUIDE.md) — 自主模式指南
