# Claude Code SubAgent 使用指南

## 问题解决

本 skill 现已支持在 **Claude Code** 环境中调用 subagent，解决了以下问题：

### 原问题
- ❌ 在 Claude Code 中无法调用 subagent
- ❌ 团队成员收到消息但没有回复
- ❌ 没有返回实质性的审查报告

### 解决方案
- ✅ 新增 `claude_code_subagent_adapter.py` 适配器
- ✅ 自动检测运行环境（Claude Code / Trae IDE）
- ✅ 提供统一的 subagent 调用接口
- ✅ 支持降级方案（模拟调用）

---

## 使用方法

### 方法 1: 自动调用（推荐）

在 Claude Code 中直接使用 skill，系统会自动调用 subagent：

```bash
# 架构设计任务
claude "设计系统架构：包括模块划分、技术选型、部署方案"

# 产品需求定义
claude "定义产品需求：广告拦截功能，需要明确的验收标准"

# 测试策略制定
claude "制定测试策略：覆盖正常、异常、边界、性能场景"

# 功能开发
claude "实现广告拦截功能：完整代码，包含单元测试"
```

系统会自动：
1. 检测 Claude Code 环境
2. 使用 `ClaudeCodeSubAgentAdapter` 调用合适的 subagent
3. 返回结构化的结果

---

### 方法 2: 手动调用适配器

```python
from claude_code_subagent_adapter import invoke_subagent

# 调用架构师 subagent
result = invoke_subagent(
    agent_type='architect',
    task='设计系统架构，支持高并发和弹性扩展'
)

# 处理结果
if result['success']:
    print(f"输出：{result['output']}")
    print(f"平台：{result['platform']}")
else:
    print(f"错误：{result['error']}")
```

---

### 方法 3: 使用包装脚本

```bash
# 调用架构师
python3 scripts/claude_code_subagent_adapter.py architect "设计系统架构"

# 调用产品经理
python3 scripts/claude_code_subagent_adapter.py product-manager "定义产品需求"

# 调用测试专家
python3 scripts/claude_code_subagent_adapter.py tester "制定测试策略"

# 调用独立开发者
python3 scripts/claude_code_subagent_adapter.py solo-coder "实现用户登录功能"

# 调用 UI 设计师
python3 scripts/claude_code_subagent_adapter.py ui-designer "设计登录页面 UI"
```

---

## 工作原理

### 架构图

```
┌─────────────────────────────────────────────────┐
│           Claude Code 环境                       │
├─────────────────────────────────────────────────┤
│  用户输入                                        │
│    ↓                                             │
│  traef_agent_dispatch_v2.py                     │
│    ↓                                             │
│  ClaudeCodeSubAgentAdapter                      │
│    ↓                                             │
│  平台检测                                        │
│    ├─ Claude Code → 调用 claude 命令             │
│    ├─ Trae IDE → 使用 DualLayerContextManager  │
│    └─ Unknown → 模拟调用（降级方案）             │
│    ↓                                             │
│  SubAgent 执行                                   │
│    ↓                                             │
│  返回结果                                        │
└─────────────────────────────────────────────────┘
```

### 调用流程

1. **环境检测**
   - 检查 `CLAUDE_CODE_ENV` 或 `ANTHROPIC_ENV` 环境变量
   - 检查 `TRAE_ENV` 或 `TRAE_AGENT_PATH` 环境变量
   - 确定运行平台

2. **构建提示词**
   - 加载角色专属 prompt
   - 添加 Karpathy 四大核心原则
   - 注入任务上下文

3. **调用 subagent**
   - Claude Code: 使用 `claude` 命令
   - Trae IDE: 使用 `DualLayerContextManager`
   - 未知平台：保存到日志文件（降级）

4. **处理结果**
   - 成功：输出结果并更新任务状态
   - 失败：记录错误并提供降级方案

---

## 配置说明

### 环境变量（可选）

```bash
# Claude Code 环境
export CLAUDE_CODE_ENV=1
# 或
export ANTHROPIC_ENV=1

# Trae IDE 环境
export TRAE_ENV=1
# 或
export TRAE_AGENT_PATH=/path/to/trae/agent
```

### 权限要求

在 Claude Code 中使用时，需要允许执行以下命令：

```bash
# 检查 claude 命令是否存在
which claude

# 执行 claude 命令
claude <prompt>
```

如果 claude 命令不可用，系统会自动降级到模拟模式。

---

## 故障排查

### 问题 1: SubAgent 调用失败

**症状**: 提示 "SubAgent 调用失败"

**解决方案**:
1. 检查是否在 Claude Code 环境中
2. 确认 `claude` 命令可用：`which claude`
3. 查看日志文件：`logs/subagent_call_*.txt`

### 问题 2: 团队成员没有回复

**症状**: 团队成员收到了消息但没有回复

**解决方案**:
1. 使用 `invoke_subagent()` 直接调用 subagent
2. 检查任务描述是否清晰
3. 添加更多上下文信息

```python
result = invoke_subagent(
    agent_type='architect',
    task='设计系统架构',
    context={
        'project_type': '电商系统',
        'tech_stack': ['Java 21', 'Spring Boot 3'],
        'requirements': ['高并发', '弹性扩展']
    }
)
```

### 问题 3: 没有返回实质性报告

**症状**: 返回的报告内容空洞

**解决方案**:
1. 确保任务描述具体明确
2. 使用 Karpathy 原则指导 subagent：
   - Think Before Coding: 明确假设
   - Goal-Driven: 定义成功标准

```python
task = """
设计用户认证系统

成功标准：
1. 支持 SSO 单点登录
2. 支持 OAuth2.0 协议
3. 认证延迟 < 100ms
4. 支持水平扩展

请先澄清以下内容：
1. 用户数据存储方案
2. Token 刷新机制
3. 安全要求等级
"""

result = invoke_subagent('architect', task)
```

---

## 示例

### 示例 1: 架构设计

```python
from claude_code_subagent_adapter import invoke_subagent

task = """
设计电商系统架构

要求：
1. 支持 10 万并发用户
2. 响应时间 < 200ms
3. 99.99% 可用性
4. 支持水平扩展

请提供：
1. 系统架构图
2. 技术选型及理由
3. 部署方案
"""

result = invoke_subagent('architect', task)
print(result['output'])
```

### 示例 2: 产品需求

```python
task = """
定义用户登录功能需求

背景：电商平台需要支持多种登录方式

请明确：
1. 支持的登录方式（密码、短信、第三方）
2. 安全要求（密码强度、登录限制）
3. 用户体验要求（登录流程、错误提示）
4. 验收标准（SMART 原则）
"""

result = invoke_subagent('product-manager', task)
print(result['output'])
```

### 示例 3: 代码审查

```python
task = """
审查用户认证模块代码

审查重点：
1. 架构合规性（是否符合分层架构）
2. 代码质量（可读性、可维护性）
3. 性能问题（潜在瓶颈）
4. 安全隐患（常见漏洞）
5. 测试覆盖（单元测试完整性）

请提供：
1. 问题清单
2. 风险等级评估
3. 改进建议
4. 优先级排序
"""

result = invoke_subagent('architect', task)
print(result['output'])
```

---

## 最佳实践

### 1. 明确任务描述

```python
# ❌ 不好的做法
task = "实现登录功能"

# ✅ 好的做法
task = """
实现用户登录功能

功能要求：
1. 支持账号密码登录
2. 支持短信验证码登录
3. 支持微信第三方登录

技术要求：
1. 使用 JWT Token
2. Token 有效期 2 小时
3. 支持 Token 刷新

安全要求：
1. 密码加密存储（BCrypt）
2. 连续 5 次登录失败锁定账号 15 分钟
3. 登录日志记录

测试要求：
1. 单元测试覆盖率 > 80%
2. 包含异常场景测试
"""
```

### 2. 应用 Karpathy 原则

```python
task = """
实现数据导出功能

在开始之前，请先澄清：
1. 导出哪些数据？（全部还是部分字段）
2. 导出格式？（JSON、CSV、Excel）
3. 数据量级？（影响实现方案）
4. 性能要求？（导出时间限制）

请遵循 Simplicity First 原则：
- 只实现当前需要的功能
- 不要添加 speculative features
- 避免过度抽象
"""
```

### 3. 定义成功标准

```python
task = """
优化搜索性能

成功标准（必须量化）：
1. 搜索延迟从 500ms 降低到 < 100ms
2. 支持 1000 并发搜索请求
3. 搜索结果相关性 > 90%

验证方式：
1. 压力测试报告
2. 性能对比数据
3. 用户满意度调查
"""
```

---

## 版本历史

### v2.6 (2026-06-17)
- ✅ 新增 Ponytail 决策梯注入（参数化，线程安全）
- ✅ `_build_agent_prompt` 支持从 context 读取决策梯（优先 `ponytail_decision_ladder`，兜底 `_ponytail_engine`）
- ✅ 修复 `json.dumps` 非 serializable 对象崩溃（添加 `default=str`）
- ✅ 100 并发调用线程安全测试通过
- ✅ 不同角色注入不同强度决策梯（solo_coder=FULL, test_expert=LITE, product_manager=OFF）

### v2.5 (2026-05-20)
- ✅ 集成 Cybernetics 工程控制论（反馈控制环 + 性能画像）
- ✅ 支持 Dynamic Workflows 6 大模式调用

### v2.4.1 (2026-04-14)
- ✅ 新增 Claude Code SubAgent 适配器
- ✅ 自动检测运行环境
- ✅ 支持降级方案
- ✅ 修复 subagent 调用问题

### v2.4 (2026-04-14)
- ✅ 集成 Karpathy 四大核心原则
- ✅ 所有角色新增行为准则

---

## Ponytail 决策梯注入（v2.6 新增）

### 注入机制

`ClaudeCodeSubAgentAdapter._build_agent_prompt()` 在构建 prompt 时，会从 `context` 字典中读取 Ponytail 决策梯：

```python
# 优先级 1: context['ponytail_decision_ladder']（预生成的决策梯文本）
# 优先级 2: context['_ponytail_engine']（engine 实例，按角色生成）
# 优先级 3: 不注入（向后兼容）
```

### 角色强度映射

| 角色 | 默认强度 | 说明 |
|------|---------|------|
| solo_coder | FULL | 完整 6 步决策梯 + 16 条红线 |
| architect | FULL | 完整 6 步决策梯 + 16 条红线 |
| test_expert | LITE | 精简版决策梯 |
| ui_designer | LITE | 精简版决策梯 |
| product_manager | OFF | 不注入决策梯 |

### 线程安全保证

- `get_injection_prompt()` 是纯函数，不修改实例状态
- `_build_agent_prompt()` 通过参数接收决策梯，不修改实例字段
- 100 并发调用测试验证无竞争条件

### Autonomous Mode 下的调用

在 Autonomous Mode 中，`DevHandler` 和 `FixHandler` 直接调用 `_dispatch_via_claude_code`，并通过 `context['ponytail_decision_ladder']` 传递预生成的决策梯文本，避免递归调用 `DispatcherAdapter.invoke()`。

详细指南见 `PONYTAIL_GUIDE.md`。

---

## 参考资料

- [Karpathy 四大核心原则](KARPATHY_PRINCIPLES.md)
- [Ponytail 决策梯指南](PONYTAIL_GUIDE.md)
- [Autonomous Mode 指南](AUTONOMOUS_MODE_GUIDE.md)
- [角色介绍](../../README.md#角色介绍)
- [使用示例](../../EXAMPLES.md)

---

**最后更新**: 2026-06-17
**作者**: Claw Team
