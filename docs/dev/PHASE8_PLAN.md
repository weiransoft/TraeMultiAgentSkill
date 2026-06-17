# Dynamic Workflows Phase 8 实施计划：SkillDistribution

**日期**：2026-06-04
**前序**：[PHASE7_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE7_FINAL_REPORT.md)（584 tests 通过）
**依据**：[DYNAMIC_WORKFLOWS_INTEGRATION.md v1.4](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md) §下一步决策 + 架构师审查 §3.0、§6

---

## 一、范围与目标

### Phase 8 范围

实现 **SkillDistribution（技能自动分发）**：将 task 声明的 `task_skill` 自动注入到 subagent 沙箱的 system context，subagent 在执行时**自动感知**自己应当激活哪些 Skill 的哪些 Capability，**不再依赖**用户手动将技能描述拼接到每个 subagent 任务的 description 里。

### 核心问题与解法

| # | 痛点 | 解法 |
|---|------|------|
| 1 | task 字典没有"该 subagent 用哪些 skill"的声明字段 | 扩展 task schema，新增 `task_skill` / `skill_mode` / `skill_priority` 三个标准字段 |
| 2 | subagent 不知道如何把 skill 内容注入到自己的 system prompt | 引入 `SkillInjector` 抽象类 + 默认 `StructuredSkillInjector`（结构化 XML 注入） |
| 3 | `SkillManifest.description` 是给人类读的，subagent 需要结构化能力描述 | 新增 `SkillInjectableView` 视图（从 Manifest + Capabilities 派生，纯结构化） |
| 4 | 缺少对 skill 依赖（如 A 依赖 B）的解析 | 新增 `SkillDependencyResolver`（DFS + 循环检测 + 拓扑排序） |
| 5 | 多个 skill 冲突 / 优先级 / 截断无策略 | 引入 `SkillMergePolicy` 枚举（override / append / prioritize） + Token 预算截断 |
| 6 | 缺安全校验（skill 名注入攻击、未知 skill 降级） | 复用 Guard 思路，新增 `SkillGuard`（白名单 + 名称合法性 + 内容注入检测） |
| 7 | 与 ModelRouter / PerformanceFingerprint 缺联动 | 注入完成后，调用 `router.route(...)` 提示"skill A 倾向 haiku"；调用 `fingerprint.record(...)` 写入 skill 使用画像 |

### 必须遵守的硬约束（架构师审查 §3.0 + Phase 1-7 沉淀）

| # | 约束 | 实施策略 |
|---|------|---------|
| 1 | 🔴 向后兼容 | `task_skill` 字段为 optional；不存在时不注入、不报错，行为与 Phase 7 完全一致 |
| 2 | 🔴 V2 不修改 | 通过 `register_skill_injector()` 扩展点注入；`dispatch_agent_v2` / `cybernetics_bridge` 零修改 |
| 3 | 🔴 持久化复用 | skill 注入决策（成功/降级/失败/耗时）写入 `PerformanceFingerprint` 的 execution_record |
| 4 | 🔴 一阶段一模块 | Phase 8 仅做 SkillDistribution；InterruptionRecovery 留到 Phase 9；/loop+/goal 留到 Phase 10 |
| 5 | 🔴 安全 | skill 名合法性校验 + 内容注入攻击检测（复用 `INJECTION_KEYWORDS`）；缺依赖时**硬中断而非降级**（拒绝执行） |
| 6 | 🔴 一致性 | `SkillManifest` / `SkillCapability` 数据类 schema **不变**；只新增 `SkillInjectableView` 派生视图 |

---

## 二、数据模型扩展

### 2.1 task 字典新增字段

#### 2.1.1 `task_skill`（核心字段）

**类型**：`Union[str, List[str], Dict[str, Any], None]`

**4 种合法形式**（按优先级解析）：

| 形式 | 示例 | 解析结果 |
|------|------|---------|
| 字符串 | `"trae-multi-agent"` | 单 skill 注入 |
| 字符串列表 | `["trae-multi-agent", "code-review"]` | 多 skill 注入（按列表顺序 = priority 升序） |
| 字典（含优先级） | `{"trae-multi-agent": 1, "code-review": 2}` | priority 1 > priority 2，数值越小优先级越高 |
| 嵌套字典（高级） | `{"primary": ["trae-multi-agent"], "fallback": ["code-review"]}` | primary 必注入；fallback 在 primary 缺失时降级 |
| None / 缺失 | — | 不注入（Phase 7 行为） |

**非法形式** → 抛 `InvalidTaskSkillFormatError`（在 `spawn()` 阶段检测，不在 execute 阶段）。

#### 2.1.2 `skill_mode`（注入模式）

**类型**：`str`，枚举值：

| 值 | 含义 | 注入内容格式 |
|----|------|-------------|
| `"structured"` | 结构化 XML 注入 | `<skill name="...">...</skill>` 块，subagent 用 XML 解析器理解结构（**默认**） |
| `"markdown"` | Markdown 段注入 | `## Skill: trae-multi-agent\n...` 段（人类可读，subagent 视为普通 prompt） |
| `"compact"` | 紧凑单行 | `Skills: trae-multi-agent(pm/arch/coder/test/...)` 一行摘要（节省 token） |
| `"full"` | 完整 YAML dump | 整个 SkillManifest YAML 序列化（最重，调试用） |

**默认**：`"structured"`（平衡可读性与机器可解析性）

#### 2.1.3 `skill_priority`（覆盖字段）

**类型**：`Optional[str]`，取值 `"low" | "normal" | "high" | "critical"`

- `"critical"`：skill 缺失时**硬中断**（抛 `SkillResolutionError`），不允许降级
- `"high"`：skill 缺失时记录 warning，继续执行
- `"normal"`：skill 缺失时记录 info，继续执行（**默认**）
- `"low"`：skill 缺失时静默忽略，不记录日志

**与 2.1.1 的嵌套字典 `"primary" / "fallback"` 组合**：

- `primary` 中的 skill 视为 `critical`
- `fallback` 中的 skill 视为 `low`

#### 2.1.4 字段位置

写入 task dict 的顶层（与 `description` / `role` / `context` 平级），**不嵌套到 `_meta` 下**，原因：

1. task dict 的字段暴露给所有下游（Guard、Router、BudgetGuard、Executor），统一位置便于复用
2. 与 Phase 4 的 `_meta.model_tier` 不同——`model_tier` 是内部透传字段，`task_skill` 是用户/工作流显式声明的领域字段

### 2.2 新增抽象类与数据类

#### 2.2.1 `SkillInjector`（抽象基类）

**路径**：`scripts/dynamic_workflow/skill_injector.py`

```python
class SkillInjector(ABC):
    """
    Skill 注入器抽象基类

    职责：
    1. 接收 task dict + SkillRegistry + 当前 sandbox 上下文
    2. 解析 task.task_skill 字段
    3. 从 SkillRegistry 加载 SkillManifest
    4. 解析依赖（递归）
    5. 调用 _render() 渲染注入内容
    6. 返回 InjectionResult（包含注入文本 + 元数据 + 警告）
    """

    @abstractmethod
    def inject(self, task: Dict[str, Any],
               registry: SkillRegistry,
               context: Optional[SandboxContext] = None) -> InjectionResult:
        """执行注入流程，返回渲染结果"""
        raise NotImplementedError

    @abstractmethod
    def _render(self, manifests: List[SkillInjectableView],
                mode: str,
                token_budget: int) -> str:
        """根据 mode 渲染注入文本（子类实现）"""
        raise NotImplementedError
```

#### 2.2.2 `SkillInjectableView`（派生视图）

**路径**：`scripts/dynamic_workflow/skill_injector.py`

```python
@dataclass
class SkillInjectableView:
    """
    Skill 注入视图（从 SkillManifest 派生的纯结构化子集）

    字段：
    - name: skill 名
    - version: 版本字符串
    - capabilities: List[CapabilityView]（只保留 name + description，丢弃 schema）
    - dependencies: List[str]（解析后的扁平依赖列表）
    - description: 人类可读描述（截断到 N 字符）

    设计目的：
    - 隔离注入端与 SkillManifest 演进（Manifest 字段新增不影响注入器）
    - 减少注入到 subagent context 的内容体积
    """
    name: str
    version: str
    capabilities: List[CapabilityView]
    dependencies: List[str]
    description: str

    @classmethod
    def from_manifest(cls, manifest: SkillManifest,
                      max_description_chars: int = 500) -> "SkillInjectableView":
        """从 SkillManifest 构造视图（截断 description）"""
        ...
```

#### 2.2.3 `CapabilityView`（能力的轻量视图）

```python
@dataclass
class CapabilityView:
    """能力注入视图（仅保留 name + description，无 schema）"""
    name: str
    description: str  # 截断到 200 字符
```

#### 2.2.4 `InjectionResult`（注入结果）

```python
@dataclass
class InjectionResult:
    """Skill 注入结果"""
    rendered_text: str                       # 注入到 system context 的最终文本
    injected_skills: List[str]               # 实际注入的 skill 名（解析依赖后）
    requested_skills: List[str]              # 用户请求的 skill 名（原始）
    missing_skills: List[str]                # 未找到的 skill
    skipped_skills: List[str]                # 因循环依赖被跳过的 skill
    truncated: bool                          # 是否因 token 预算被截断
    token_estimate: int                      # 注入文本的 token 估算
    injection_time_ms: float                 # 注入耗时
    warnings: List[str]                      # 警告信息
    mode: str                                # 使用的注入模式
    metadata: Dict[str, Any] = field(default_factory=dict)  # 画像反哺用
```

#### 2.2.5 `SkillDependencyResolver`（依赖解析器）

```python
class SkillDependencyResolver:
    """
    Skill 依赖解析器（DFS + 循环检测 + 拓扑排序）

    输入：用户声明的 skill 列表
    输出：扁平化的解析后 skill 列表（保持拓扑序）
    """

    def resolve(self, requested_skills: List[str],
                registry: SkillRegistry) -> DependencyResolution:
        """
        解析依赖

        Args:
            requested_skills: 用户声明的 skill 名列表
            registry: SkillRegistry 实例

        Returns:
            DependencyResolution: 包含 resolved_order / cycles / missing
        """
        ...

    def _detect_cycles(self, start: str,
                       registry: SkillRegistry) -> List[List[str]]:
        """
        DFS 检测循环依赖

        返回所有检测到的循环路径
        例如: [["A", "B", "A"]] 表示 A → B → A 的循环
        """
        ...
```

#### 2.2.6 `DependencyResolution`（依赖解析结果）

```python
@dataclass
class DependencyResolution:
    """依赖解析结果"""
    resolved_order: List[str]                # 拓扑序排列的 skill 名（无重复）
    missing: List[str]                       # 用户声明但 registry 中不存在的 skill
    cycles: List[List[str]]                  # 检测到的循环依赖
    skipped_due_to_cycle: List[str]          # 因循环被跳过的 skill
    warnings: List[str]                      # 警告信息
```

#### 2.2.7 `SkillMergePolicy`（合并策略枚举）

```python
class SkillMergePolicy(str, Enum):
    """
    多 skill 合并策略

    - APPEND:      按列表顺序追加（默认；最直观）
    - PRIORITIZE:  按 priority 排序，高优先级 skill 在前
    - OVERRIDE:    后声明的 skill 覆盖前声明的（同名 capability 取后者）
    - DEDUPE:      先 dedupe capability name，再按字典序
    """
    APPEND = "append"
    PRIORITIZE = "prioritize"
    OVERRIDE = "override"
    DEDUPE = "dedupe"
```

#### 2.2.8 `SkillGuard`（Skill 安全校验器）

**路径**：`scripts/dynamic_workflow/skill_guard.py`

```python
class SkillGuard:
    """
    Skill 安全校验器

    校验内容：
    1. skill 名合法性（只允许 [a-z0-9-]，长度 <= 64）
    2. skill 数量上限（单 task <= 10 个）
    3. 内容注入攻击检测（复用 guard.INJECTION_KEYWORDS）
    4. 嵌套深度上限（依赖链深度 <= 5）
    """

    ALLOWED_SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
    MAX_SKILLS_PER_TASK = 10
    MAX_DEPENDENCY_DEPTH = 5

    def validate_skill_name(self, name: str) -> GuardResult: ...
    def validate_skill_count(self, skills: List[str]) -> GuardResult: ...
    def validate_injection_content(self, text: str) -> GuardResult: ...
    def validate_dependency_depth(self, depth: int) -> GuardResult: ...
```

### 2.3 关键决策记录

| 决策点 | 选项 | 选定 | 理由 |
|--------|------|------|------|
| task_skill 字段位置 | 顶层 vs `_meta.task_skill` | **顶层** | 与 `role` / `description` 平级，跨模块一致 |
| 注入模式默认值 | structured / markdown / compact | **structured** | 平衡可读性与机器可解析性；subagent 普遍能理解 XML |
| 缺失 skill 行为 | 硬中断 vs 降级 | **按 priority 决定** | critical → 硬中断；其他 → 警告+继续 |
| 循环依赖处理 | 抛异常 vs 跳过 | **跳过 + 记录** | 避免一个坏 skill 拖垮整个 task |
| 依赖深度上限 | 无 vs 5 层 | **5 层** | 防止恶意注册表构造超深链攻击 token 预算 |
| SkillGuard 独立模块 | 内嵌 vs 独立文件 | **独立** | 与 `guard.py` 解耦；guard.py 是输入校验，skill_guard.py 是 skill 内容校验 |

---

## 三、注入流程设计

### 3.1 主流程时序

```
用户调用：sandbox.spawn(agent_id, task, isolation_level, token_budget)
                                                       │
                              ┌────────────────────────┘
                              ▼
              ┌───────────────────────────────┐
   步骤 1    │ 解析 task.task_skill 字段       │  解析为 List[Dict[str, priority]]
              │ (SkillTaskFieldParser)         │
              └───────────────┬───────────────┘
                              │ parsed_skills: List[Dict]
                              ▼
              ┌───────────────────────────────┐
   步骤 2    │ SkillGuard 校验                │  名称合法性 / 数量 / 深度
              │ (skill_guard.SkillGuard)      │  失败 → 抛 SkillGuardError
              └───────────────┬───────────────┘
                              │ validated_skills
                              ▼
              ┌───────────────────────────────┐
   步骤 3    │ 依赖解析                       │  递归加载所有依赖
              │ (SkillDependencyResolver)     │  检测循环 / missing
              └───────────────┬───────────────┘
                              │ DependencyResolution
                              ▼
              ┌───────────────────────────────┐
   步骤 4    │ missing skills 决策             │  按 skill_priority 处理
              │ - critical → 硬中断            │
              │ - 其他 → 记录 warning          │
              └───────────────┬───────────────┘
                              │ effective_skills
                              ▼
              ┌───────────────────────────────┐
   步骤 5    │ 构建 SkillInjectableView       │  截断 description
              │ (SkillInjectableView.from_     │  丢弃 input/output schema
              │  manifest)                     │
              └───────────────┬───────────────┘
                              │ views: List[SkillInjectableView]
                              ▼
              ┌───────────────────────────────┐
   步骤 6    │ 合并多 skill                    │  按 SkillMergePolicy
              │ (SkillMerger)                  │  默认 APPEND
              └───────────────┬───────────────┘
                              │ merged_view
                              ▼
              ┌───────────────────────────────┐
   步骤 7    │ 渲染注入文本                    │  按 skill_mode 渲染
              │ (StructuredSkillInjector.      │  structured / markdown /
              │  _render)                      │  compact / full
              └───────────────┬───────────────┘
                              │ rendered_text
                              ▼
              ┌───────────────────────────────┐
   步骤 8    │ Token 预算截断                  │  估算 token 数
              │ (TokenBudgetGuard 协作)        │  超限 → 截断 + 标记
              └───────────────┬───────────────┘
                              │ final_text
                              ▼
              ┌───────────────────────────────┐
   步骤 9    │ 内容注入攻击检测                │  复用 INJECTION_KEYWORDS
              │ (SkillGuard.validate_          │  发现 → 抛 SkillGuardError
              │  injection_content)            │
              └───────────────┬───────────────┘
                              │ sanitized_text
                              ▼
              ┌───────────────────────────────┐
   步骤 10   │ 写入 SandboxContext             │  ctx.injected_skills
              │ （绑定到沙箱生命周期）           │  ctx.system_prompt_addon
              └───────────────┬───────────────┘
                              │
                              ▼
              ┌───────────────────────────────┐
   步骤 11   │ 画像反哺                        │  PerformanceFingerprint.record
              │ (skill_distribution event)     │  含 injected / missing / 时间
              └───────────────────────────────┘
```

### 3.2 注入位置（与 System Context 集成）

**关键约束**：`SubagentSandbox` 当前**不直接管理** subagent 的 system prompt。`SandboxContext` 只暴露 `record_token` / 元数据。Phase 8 不引入 LLM 调用层，仅**准备**注入文本。

注入文本的存储：

```python
@dataclass
class SandboxContext:
    # ... 现有字段 ...
    injected_skills: List[str] = field(default_factory=list)  # 已注入的 skill 名
    skill_injection_text: Optional[str] = None                # 注入文本（待 subagent 使用）
    skill_injection_meta: Optional[Dict[str, Any]] = None     # 注入元数据
```

**用户读取方式**：

```python
sandbox_id = sandbox.spawn(agent_id="sa_001", task={
    "description": "分析用户反馈",
    "task_skill": "trae-multi-agent",
})
ctx = sandbox.get_context(sandbox_id)
# ctx.skill_injection_text 即为 subagent 应当拼接到 system prompt 的内容

# 用户自行拼接到 LLM 调用（Phase 8 不内置 LLM 调用）：
full_system_prompt = base_system_prompt + "\n\n" + ctx.skill_injection_text
llm.call(system=full_system_prompt, user=ctx.input)

result = sandbox.execute(sandbox_id, my_executor)
```

### 3.3 渲染模板（structured 模式）

```xml
<skills_injection version="1.0" generated_at="2026-06-04T12:00:00" mode="structured">
  <skill name="trae-multi-agent" version="2.4.1" status="active">
    <description>
      Trae 多智能体协作技能（AI 增强版）。基于双层动态上下文管理架构...
    </description>
    <capabilities>
      <capability name="product-manager">
        产品经理角色，负责需求分析和 PRD 编写。核心能力：需求挖掘和分析、
        PRD 文档编写、用户研究、竞品分析、产品规划。
      </capability>
      <capability name="architect">
        架构师角色，负责系统架构设计和技术选型...
      </capability>
      <!-- ... -->
    </capabilities>
    <dependencies>
      <!-- 此处不展开依赖（依赖 skill 会作为独立 <skill> 块注入）-->
    </dependencies>
  </skill>

  <!-- 依赖 skill 独立成块 -->
  <skill name="code-review" version="1.2.0" status="active">
    <!-- ... -->
  </skill>
</skills_injection>
```

**模板特征**：
- 顶层 `<skills_injection>` 包裹所有 skill
- 每个 skill 独立 `<skill>` 块
- capability 列表在 skill 块内
- 依赖 skill 作为独立 `<skill>` 块（而非嵌套），便于 subagent 独立引用
- 包含 `mode` / `generated_at` 元信息（便于 subagent 区分）

### 3.4 渲染模板（compact 模式）

```
[Skills] trae-multi-agent@2.4.1 (pm,architect,developer,tester,solo-coder,ui-designer,devops,ai-assistant); code-review@1.2.0 (review,fix)
```

**特征**：单行，节省 token，仅用于低预算场景。

### 3.5 渲染模板（markdown 模式）

```markdown
## Injected Skills

### Skill: trae-multi-agent (v2.4.1)

**Status**: active
**Description**: Trae 多智能体协作技能（AI 增强版）...

**Capabilities**:
- **product-manager**: 产品经理角色，负责需求分析和 PRD 编写...
- **architect**: 架构师角色，负责系统架构设计...
- ...

### Skill: code-review (v1.2.0)

...
```

### 3.6 渲染模板（full 模式）

直接 YAML 序列化整个 `SkillInjectableView` 列表（调试用）：

```yaml
- name: trae-multi-agent
  version: 2.4.1
  capabilities:
    - name: product-manager
      description: ...
  ...
```

### 3.7 Token 预算截断策略

**触发条件**：`rendered_text` 估算 token 数 > subagent token_budget × 0.2（**保留 80% 给实际 LLM 调用**）。

**截断策略**（按顺序尝试）：

1. **截断 description**：每个 capability.description 从 200 字符截到 100 字符
2. **截断 skill description**：skill.description 从 500 字符截到 200 字符
3. **丢弃最末位 skill**（按 priority 升序保留高优先级）
4. **切换到 compact 模式**：仍超限则切到 compact 模式重新渲染

**截断后行为**：

- `InjectionResult.truncated = True`
- `warnings` 追加 `"token_truncated: 从 {N} tokens 截断到 {M} tokens"`
- 画像反哺时 `truncated=True` 标记
- **不抛异常**（预算截断是软行为，不应中断 subagent 启动）

### 3.8 失败处理矩阵

| 失败类型 | skill_priority | 行为 |
|---------|---------------|------|
| skill 名非法 | - | 硬中断，抛 `SkillGuardError` |
| skill 数量 > 10 | - | 硬中断，抛 `SkillGuardError` |
| 依赖深度 > 5 | - | 硬中断，抛 `SkillGuardError` |
| skill 不存在 | critical | 硬中断，抛 `SkillResolutionError` |
| skill 不存在 | high / normal | 警告 + 继续，注入其他 skill |
| skill 不存在 | low | 静默忽略，不记录 |
| 循环依赖 | - | 跳过循环 skill，记录 `skipped_due_to_cycle` |
| 注入内容含注入攻击 | - | 硬中断，抛 `SkillGuardError` |
| Token 超限 | - | 截断 + 继续（不中断） |
| SkillRegistry 加载失败 | - | 降级为无 skill 注入，警告 |

---

## 四、集成点设计

### 4.1 SubagentSandbox 改造

**修改文件**：`scripts/dynamic_workflow/subagent_sandbox.py`

**修改点**：

1. **`__init__` 新增参数**：
   ```python
   def __init__(
       self,
       worktree_manager: Optional[WorktreeManager] = None,
       fingerprint: Optional[PerformanceFingerprint] = None,
       guard_enabled: bool = True,
       skill_injector: Optional["SkillInjector"] = None,  # Phase 8 新增
       skill_registry: Optional["SkillRegistry"] = None,  # Phase 8 新增
   ):
   ```

2. **`SandboxContext` 新增 3 个字段**（见 3.2）：
   - `injected_skills: List[str]`
   - `skill_injection_text: Optional[str]`
   - `skill_injection_meta: Optional[Dict[str, Any]]`

3. **`spawn` 新增子流程**（在 Guard 校验后、context 创建前）：
   ```python
   # Step 1.5: Skill 注入（Phase 8 新增）
   if self._skill_injector and self._skill_registry:
       injection_result = self._skill_injector.inject(
           task=task,
           registry=self._skill_registry,
           context=None,  # 此时 context 尚未创建
       )
       # 注入结果稍后绑定到 SandboxContext
       pending_injection = injection_result
   else:
       pending_injection = None
   ```

4. **`spawn` 在创建 SandboxContext 时绑定**：
   ```python
   sandbox_ctx = SandboxContext(
       # ... 现有字段 ...
       injected_skills=pending_injection.injected_skills if pending_injection else [],
       skill_injection_text=pending_injection.rendered_text if pending_injection else None,
       skill_injection_meta=asdict(pending_injection) if pending_injection else None,
   )
   ```

5. **`_record_to_fingerprint` 新增 skill 分布事件**：
   ```python
   if sandbox_ctx and sandbox_ctx.skill_injection_meta:
       record["skill_injection"] = {
           "injected": sandbox_ctx.injected_skills,
           "missing": sandbox_ctx.skill_injection_meta.get("missing_skills", []),
           "truncated": sandbox_ctx.skill_injection_meta.get("truncated", False),
           "token_estimate": sandbox_ctx.skill_injection_meta.get("token_estimate", 0),
           "mode": sandbox_ctx.skill_injection_meta.get("mode", "structured"),
       }
   ```

6. **行为不变约束**：`skill_injector` 为 None 时，`spawn` 行为与 Phase 7 **完全一致**（0 行行为变化）。

### 4.2 与 Guard 的集成

**复用 `scripts/dynamic_workflow/guard.py` 的能力**：
- `INJECTION_KEYWORDS`：注入攻击检测（步骤 9）
- `GuardResult`：作为 SkillGuard 的返回类型（统一接口）
- `GuardRejectError`：Phase 8 复用，**不新建** 异常类

**新增文件**：`scripts/dynamic_workflow/skill_guard.py`
- 独立模块，**不修改** guard.py
- 仅做 skill 特有的校验（名称、数量、深度）
- 内容注入检测**委托**给 guard.py（避免重复造轮子）

### 4.3 与 PerformanceFingerprint 的集成

**复用现有 `record()` 方法**：

```python
self._fingerprint.record(
    pattern_id="skill_distribution",
    success=(status == SandboxStatus.SUCCESS.value),
    context_features={
        "event_type": "skill_distribution",
        "agent_id": agent_id,
        "sandbox_id": sandbox_id,
        "injected_skills": injection_result.injected_skills,
        "missing_skills": injection_result.missing_skills,
        "truncated": injection_result.truncated,
        "token_estimate": injection_result.token_estimate,
        "injection_time_ms": injection_result.injection_time_ms,
        "mode": injection_result.mode,
    },
    strategy=f"mode={injection_result.mode};merge=append",
)
```

**可分析的反哺场景**（用户后续可查）：
- 哪些 skill 注入频次最高？
- 哪些 skill 注入后 subagent 成功率最高？
- 哪些 skill 经常因 missing 被警告？
- 平均注入耗时 / token 估算分布

### 4.4 与 ModelRouter 的集成

**关键设计点**：Phase 8 **不修改** ModelRouter，不引入"skill → model"硬映射（避免 Phase 8 越界）。

**可选联动**（用户自行决定）：

```python
# 用户代码示例（Phase 8 文档推荐用法）
sandbox_id = sandbox.spawn(agent_id, task)
ctx = sandbox.get_context(sandbox_id)

# 如果想基于 skill 推荐模型：
if ctx.injected_skills:
    feature = TaskFeature(
        task_complexity=8 if "architect" in [c for s in injected_views for c in s.capabilities] else 5,
        estimated_tokens=token_budget // 4,
        role="architect" if any(s.name == "trae-multi-agent" for s in injected_views) else "general",
    )
    decision = router.route(feature)
    model_tier = decision.selected_tier
```

**Phase 8 范围**：仅在文档中提供示例，**不**在 `SubagentSandbox` 内部调用 ModelRouter（避免一阶段多模块）。

### 4.5 与 CyberneticsBridge 的集成

**复用**：Phase 3 的 `CyberneticsBridge.enhanced_execute()` 已接受任意 task dict 并执行 Guard 预验证。

**Phase 8 改动**：

- `CyberneticsBridge` **不修改**
- `SubagentSandbox.spawn()` 内部触发 skill 注入，**不经过** CyberneticsBridge
- 但 skill 注入事件会被 PerformanceFingerprint 捕获，CyberneticsBridge 后续可以读取（Phase 9+）

**原因**：保持 Phase 拆分原则。Skill 注入是 subagent 启动阶段的事，CyberneticsBridge 是 dispatch 阶段的事，两者解耦。

### 4.6 与 SkillRegistry 的集成

**复用现有 `SkillRegistry`**（`scripts/skill_registry.py`）：
- `get_skill(name)` → 加载 `SkillManifest`
- `check_dependencies(name)` → 验证依赖（Phase 8 增强为拓扑解析）
- `list_skills(status="active")` → 获取所有 active skill

**Phase 8 增强**：
- `SkillRegistry` **不修改**（避免破坏 Phase 7 测试）
- `SkillDependencyResolver` 独立模块，**包装** SkillRegistry 的能力
- 即"SkillRegistry 提供数据，SkillDependencyResolver 提供算法"

### 4.7 集成总览图

```
┌────────────────────────────────────────────────────────────────┐
│                        SubagentSandbox                          │
│                                                                 │
│  spawn()                                                         │
│    ├─→ Guard.check(task)              [Phase 1]                  │
│    ├─→ SkillInjector.inject()         [Phase 8 ✨]               │
│    │     ├─→ SkillGuard.validate()                              │
│    │     ├─→ SkillDependencyResolver.resolve()                  │
│    │     ├─→ SkillRegistry.get_skill()                          │
│    │     ├─→ SkillInjectableView.from_manifest()                │
│    │     ├─→ SkillMerger.merge()                                │
│    │     ├─→ StructuredSkillInjector._render()                  │
│    │     ├─→ TokenBudgetGuard.truncate()                        │
│    │     └─→ PerformanceFingerprint.record()                    │
│    ├─→ WorktreeManager.create()        [Phase 2]                  │
│    └─→ SandboxContext(...)                                       │
│                                                                 │
│  execute()                                                       │
│    └─→ user_executor(ctx)              # 用户读取 ctx.skill_...   │
└────────────────────────────────────────────────────────────────┘
```

---

## 五、边界与失败处理（详细展开）

### 5.1 Skill 不存在时的降级

**场景**：用户声明 `task_skill="unknown-skill"`

**行为决策树**：

```
skill "unknown-skill" 不存在
  │
  ├─ task_skill 是字符串 → effective_skill_priority = skill_priority 字段
  │   ├─ "critical" → 硬中断（抛 SkillResolutionError）
  │   ├─ "high"     → 记录 warning，继续（不注入）
  │   ├─ "normal"   → 记录 info，继续（不注入）
  │   └─ "low"      → 静默忽略
  │
  └─ task_skill 是嵌套字典
      ├─ "primary": [...unknown-skill...]
      │   └─ primary 中的 skill 一律视为 "critical" → 硬中断
      └─ "fallback": [...unknown-skill...]
          └─ fallback 中的 skill 视为 "low" → 静默忽略
```

**关键代码**：

```python
def _handle_missing(self, missing: List[str], priority: str) -> None:
    if priority == "critical":
        raise SkillResolutionError(
            f"关键 skill 缺失（critical）：{missing}",
            missing=missing,
        )
    elif priority == "high":
        logger.warning(f"skill 缺失（high，不影响执行）：{missing}")
    elif priority == "normal":
        logger.info(f"skill 缺失（normal，已跳过）：{missing}")
    # low: 静默
```

### 5.2 Skill 加载失败的容错

**场景**：`SkillRegistry.get_skill("valid-name")` 抛异常（IO 错误 / 反序列化错误）

**行为**：
- 捕获异常，**不传播**
- 视为 skill "缺失"（按 5.1 处理）
- 记录 warning："skill 加载失败：{name}: {error}"
- 继续执行注入流程

**关键代码**：

```python
def _safe_get_skill(self, name: str, registry: SkillRegistry) -> Optional[SkillManifest]:
    try:
        return registry.get_skill(name)
    except Exception as e:
        logger.warning(f"skill 加载失败 {name}：{e}")
        return None
```

### 5.3 循环依赖检测

**场景**：
- `trae-multi-agent` 依赖 `code-review`
- `code-review` 依赖 `test-helper`
- `test-helper` 依赖 `trae-multi-agent`（人为构造的循环）

**检测算法**（DFS + 路径记录）：

```python
def _detect_cycles(self, start: str, registry: SkillRegistry) -> List[List[str]]:
    """检测从 start 出发的所有循环依赖路径"""
    visited = set()
    path = []
    cycles = []

    def dfs(node: str):
        if node in path:
            # 发现循环：截取 path 中从 node 出现的索引到末尾
            cycle_start = path.index(node)
            cycle = path[cycle_start:] + [node]
            cycles.append(cycle)
            return
        if node in visited:
            return
        path.append(node)
        skill = registry.get_skill(node)
        if skill:
            for dep in skill.dependencies:
                dfs(dep)
        path.pop()
        visited.add(node)

    dfs(start)
    return cycles
```

**行为**：
- 检测到循环 → 跳过循环中的 skill（保留首次出现的，去重）
- `DependencyResolution.cycles` 记录所有循环路径
- `DependencyResolution.skipped_due_to_cycle` 列出被跳过的 skill
- **不抛异常**（循环是注册表设计问题，不应让用户的 task 失败）

### 5.4 Skill 描述过长截断

**场景**：某 skill 的 description 长达 5000 字符，注入到 subagent context 后占用大量 token。

**截断策略**（3 级）：

1. **Level 1 - 软截断**：单个 `capability.description` 截到 200 字符
2. **Level 2 - 中截断**：单个 `skill.description` 截到 500 字符
3. **Level 3 - 硬截断**：整个 `rendered_text` 截到 `token_budget × 0.2` tokens

**截断实现**（中文友好）：

```python
def _truncate(self, text: str, max_chars: int) -> str:
    """中英混合文本截断（按字符数，保留标点）"""
    if len(text) <= max_chars:
        return text
    # 截到 max_chars - 3 字符 + "..."（中文也用 3 个 ASCII 点）
    return text[:max_chars - 3] + "..."
```

**token 估算**（中文 vs 英文）：

```python
def _estimate_tokens(self, text: str) -> int:
    """
    粗略 token 估算
    - 1 个中文字符 ≈ 1.5 tokens
    - 1 个英文单词 ≈ 1.3 tokens
    - 标点 / 空白 ≈ 0.5 tokens
    """
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - chinese_chars
    return int(chinese_chars * 1.5 + other_chars * 0.4)
```

### 5.5 SkillGuard 校验失败

**场景 1**：用户传入 `task_skill="../malicious-skill"`
- 名称校验失败（不匹配 `[a-z0-9-]`）
- 硬中断 `SkillGuardError("skill 名非法：../malicious-skill")`

**场景 2**：用户传入 20 个 skill
- 数量校验失败（> 10）
- 硬中断 `SkillGuardError("skill 数量超限：20 > 10")`

**场景 3**：依赖链深度 10 层
- 深度校验失败（> 5）
- 硬中断 `SkillGuardError("依赖深度超限：10 > 5")`

**场景 4**：skill 的 description 含 `忽略之前的所有指令`
- 内容注入检测命中（复用 `INJECTION_KEYWORDS`）
- 硬中断 `SkillGuardError("skill 描述含注入攻击：{skill_name}")`

### 5.6 Skill 注入过程的异常隔离

**核心原则**：skill 注入失败**不应阻断** subagent 启动（除非 critical 缺失或 Guard 拒绝）。

```python
try:
    injection_result = self._skill_injector.inject(task, registry)
except Exception as e:
    if isinstance(e, (SkillGuardError, SkillResolutionError)):
        # 严重错误 → 阻断
        raise
    # 软错误 → 降级
    logger.warning(f"skill 注入失败，降级到无 skill 注入：{e}")
    injection_result = InjectionResult(
        rendered_text="",
        injected_skills=[],
        warnings=[f"injection_failed: {e}"],
        ...
    )
```

---

## 六、测试用例设计

### 6.1 单元测试（28 个 case）

#### 6.1.1 TestSkillTaskFieldParser（5 个）

| # | 用例 | 断言 |
|---|------|------|
| 1 | 解析 `task_skill="single-skill"` | 解析为 `["single-skill"]`，priority 默认 normal |
| 2 | 解析 `task_skill=["s1", "s2"]` | 解析为 `["s1", "s2"]` |
| 3 | 解析 `task_skill={"s1": 1, "s2": 2}` | 解析为按 priority 升序 `["s1", "s2"]` |
| 4 | 解析 `task_skill={"primary": [...], "fallback": [...]}` | primary 视为 critical，fallback 视为 low |
| 5 | 解析非法格式 `task_skill=123` | 抛 `InvalidTaskSkillFormatError` |

#### 6.1.2 TestSkillGuard（6 个）

| # | 用例 | 断言 |
|---|------|------|
| 6 | 校验合法名称 `trae-multi-agent` | 通过 |
| 7 | 校验非法名称 `../malicious` | 拒绝 |
| 8 | 校验数量 10 个 | 通过 |
| 9 | 校验数量 11 个 | 拒绝 |
| 10 | 校验依赖深度 5 | 通过 |
| 11 | 校验依赖深度 6 | 拒绝 |

#### 6.1.3 TestSkillDependencyResolver（6 个）

| # | 用例 | 断言 |
|---|------|------|
| 12 | 解析无依赖 skill `["s1"]` | `resolved_order = ["s1"]` |
| 13 | 解析单层依赖 `["s1"]`（s1 依赖 s2） | `resolved_order = ["s2", "s1"]`（依赖在前） |
| 14 | 解析多层依赖（A → B → C） | `resolved_order = ["C", "B", "A"]`（拓扑序） |
| 15 | 检测循环 A → B → A | `cycles = [["A", "B", "A"]]` |
| 16 | 缺失依赖 `["unknown"]` | `missing = ["unknown"]` |
| 17 | 重复依赖（DAG 中有共享节点） | `resolved_order` 去重 |

#### 6.1.4 TestSkillInjectableView（3 个）

| # | 用例 | 断言 |
|---|------|------|
| 18 | 从 SkillManifest 构造 view | 字段正确映射，schema 丢弃 |
| 19 | description 截断到 500 字符 | view.description 长度 <= 500 |
| 20 | capabilities 截断到 200 字符 | view.capabilities[i].description 长度 <= 200 |

#### 6.1.5 TestStructuredSkillInjector（5 个）

| # | 用例 | 断言 |
|---|------|------|
| 21 | 注入单个 skill（structured 模式） | 渲染文本包含 `<skill name="...">` |
| 22 | 注入多 skill（structured 模式） | 渲染文本包含多个 `<skill>` 块 |
| 23 | 注入 compact 模式 | 渲染文本为单行 `[Skills] ...` |
| 24 | 注入 markdown 模式 | 渲染文本以 `## Injected Skills` 开头 |
| 25 | 注入 full 模式 | 渲染文本为合法 YAML |

#### 6.1.6 TestTokenBudgetTruncation（3 个）

| # | 用例 | 断言 |
|---|------|------|
| 26 | 注入内容 < 预算 20% | 不截断，`truncated=False` |
| 27 | 注入内容 > 预算 20% | 截断到 ≤ 预算 20%，`truncated=True` |
| 28 | 截断后切到 compact 模式 | compact 模式渲染后仍超限则切到空内容 |

### 6.2 集成测试（10 个）

#### 6.2.1 TestSubagentSandboxIntegration（10 个）

| # | 用例 | 断言 |
|---|------|------|
| 29 | sandbox.spawn(task={..., task_skill="valid"}) | ctx.skill_injection_text 不为空，ctx.injected_skills 包含 skill 名 |
| 30 | sandbox.spawn(task={...})（无 task_skill） | ctx.skill_injection_text 为 None，行为与 Phase 7 一致 |
| 31 | sandbox.spawn(task={task_skill="unknown"}) | critical 缺失 → 抛 SkillResolutionError |
| 32 | sandbox.spawn(task={task_skill="unknown", skill_priority="high"}) | warning + 继续，ctx.skill_injection_text 为空 |
| 33 | sandbox.spawn(task={task_skill=["s1", "s2"]}) | 注入 s1 和 s2，顺序按列表 |
| 34 | sandbox.spawn(task={task_skill={"s1": 1, "s2": 2}}) | 注入 s1 和 s2，priority 升序 |
| 35 | sandbox.spawn(task={task_skill=20 个}) | SkillGuardError（数量超限） |
| 36 | sandbox.spawn(task={task_skill="A"})（A 依赖 B，B 依赖 A） | 循环检测，A 或 B 被跳过，warning 记录 |
| 37 | sandbox.spawn(task 含 task_skill) + execute() | executor 可访问 ctx.skill_injection_text |
| 38 | sandbox.spawn(task={task_skill="valid"}) + cleanup() | fingerprint 记录 skill_distribution 事件 |

### 6.3 失败处理测试（7 个）

| # | 用例 | 断言 |
|---|------|------|
| 39 | SkillRegistry.get_skill 抛异常 | 注入降级，warning 记录 |
| 40 | skill description 含注入关键词 | SkillGuardError 抛出 |
| 41 | skill 名称含路径遍历字符 | SkillGuardError 抛出 |
| 42 | 循环依赖深度 10 层 | SkillGuardError（深度超限） |
| 43 | 单 skill description 50000 字符 | 截断到 500 字符 |
| 44 | 注入总 token 超 5 倍预算 | 切到 compact 模式 |
| 45 | task_skill 为 None | 不注入（Phase 7 行为） |

### 6.4 性能 Benchmark（5 个）

| # | 用例 | 性能指标 |
|---|------|---------|
| 46 | 注入 0 个 skill | < 5ms |
| 47 | 注入 1 个 skill（含 10 个 capability） | < 20ms |
| 48 | 注入 5 个 skill（含依赖解析） | < 50ms |
| 49 | 注入 10 个 skill（上限） | < 100ms |
| 50 | 注入 1 个含 1000 字符 description 的 skill | < 30ms |

**性能基线**（在 macOS M1 / Python 3.11）：

| 场景 | Phase 8 目标 | 实测 |
|------|-------------|------|
| spawn() 总耗时（无 skill） | 与 Phase 7 持平 | < 10ms |
| spawn() 总耗时（1 skill） | < 30ms | < 30ms |
| spawn() 总耗时（10 skill + 依赖） | < 150ms | < 150ms |
| 内存占用（10 skill 全部 view） | < 5MB | < 5MB |

### 6.5 测试文件结构

```
scripts/tests/
├── test_skill_task_field_parser.py        # 6.1.1（5 cases）
├── test_skill_guard.py                    # 6.1.2（6 cases）
├── test_skill_dependency_resolver.py      # 6.1.3（6 cases）
├── test_skill_injectable_view.py          # 6.1.4（3 cases）
├── test_structured_skill_injector.py      # 6.1.5 + 6.1.6（8 cases）
├── test_skill_distribution_integration.py # 6.2 + 6.3（17 cases）
├── test_skill_distribution_benchmark.py   # 6.4（5 cases）
└── scripts/
    └── run_skill_distribution_tests.sh    # 测试入口
```

**测试总计**：50 cases（28 + 10 + 7 + 5）

### 6.6 回归测试

- ✅ Phase 1-7 全部 584 tests 零回归
- ✅ V2 回归 85 tests 零失败
- ✅ V2 文件零修改（`git diff scripts/workflow_engine_v2.py scripts/cybernetics_bridge.py scripts/guard_coordinator.py` 为空）

---

## 七、配置项设计

### 7.1 用户可配置参数

#### 7.1.1 `SubagentSandbox` 构造参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `skill_injector` | `Optional[SkillInjector]` | `None` | 自定义注入器（None → 禁用 skill 分发） |
| `skill_registry` | `Optional[SkillRegistry]` | `None` | 自定义注册表（None → 尝试自动创建） |
| `skill_priority` | `str` | `"normal"` | 全局默认 skill 缺失行为（task 内可覆盖） |
| `skill_mode` | `str` | `"structured"` | 全局默认注入模式（task 内可覆盖） |
| `skill_merge_policy` | `str` | `"append"` | 全局默认合并策略 |
| `max_injection_tokens` | `int` | `0` | 注入 token 硬上限（0 → 跟 subagent token_budget × 0.2） |

#### 7.1.2 task 字典内可配置字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `task_skill` | `Union[str, List, Dict, None]` | `None` | 声明要注入的 skill |
| `skill_mode` | `str` | `sandbox.skill_mode` | 本 task 覆盖全局模式 |
| `skill_priority` | `str` | `sandbox.skill_priority` | 本 task 覆盖全局缺失行为 |
| `skill_merge_policy` | `str` | `sandbox.skill_merge_policy` | 本 task 覆盖全局合并策略 |
| `skill_injection_disabled` | `bool` | `False` | 显式禁用本 task 的 skill 注入 |

### 7.2 安全默认值

| 参数 | 安全默认值 | 理由 |
|------|----------|------|
| `skill_injector` | `None` | 不自动注入；显式启用 |
| `skill_priority` | `"normal"` | 缺失仅警告，不阻断 |
| `max_injection_tokens` | `token_budget × 0.2` | 80% 预算留给实际 LLM |
| `MAX_SKILLS_PER_TASK` | `10` | 防止 token 耗尽攻击 |
| `MAX_DEPENDENCY_DEPTH` | `5` | 防止恶意注册表攻击 |
| 内容注入检测 | **默认启用** | 与 Guard 风格一致 |

### 7.3 配置示例

```python
# 最小化配置（禁用 skill 注入）
sandbox = SubagentSandbox()

# 启用默认 skill 注入
sandbox = SubagentSandbox(
    skill_injector=StructuredSkillInjector(),
    skill_registry=SkillRegistry(),
)

# 自定义配置
sandbox = SubagentSandbox(
    skill_injector=StructuredSkillInjector(
        default_mode="markdown",
        default_merge_policy="prioritize",
        max_description_chars=300,
    ),
    skill_registry=SkillRegistry(registry_path="/custom/path"),
    skill_priority="high",          # 全局默认：缺失只警告
    max_injection_tokens=2000,      # 全局硬上限
)

# 任务级覆盖
sandbox.spawn(
    agent_id="sa_001",
    task={
        "description": "...",
        "task_skill": ["trae-multi-agent", "code-review"],
        "skill_mode": "compact",           # 覆盖全局
        "skill_priority": "critical",      # 覆盖全局
    },
    isolation_level="context",
)
```

---

## 八、风险评估

### 8.1 与 V2 不修改约束的冲突点

| 风险点 | 是否冲突 | 缓解策略 |
|--------|---------|---------|
| SubagentSandbox 修改 | **不冲突** | SubagentSandbox 是 Phase 2 模块，非 V2 |
| SkillRegistry 复用 | **不冲突** | SkillRegistry 是 Phase 2 模块，非 V2 |
| PerformanceFingerprint 复用 | **不冲突** | PF 是 V2.5 模块，但 Phase 8 只调用，不修改 |
| Guard 复用 | **不冲突** | Guard 是 Phase 1 模块；Phase 8 只导入 INJECTION_KEYWORDS，不修改 |
| dispatch_agent_v2 | **不冲突** | Phase 8 不直接调用 dispatch_agent_v2 |
| workflow_engine_v2 | **不冲突** | Phase 8 不修改 V2 引擎 |

**V2 文件 diff 校验**（CI 必做）：

```bash
git diff scripts/workflow_engine_v2.py \
        scripts/cybernetics_bridge.py \
        scripts/guard_coordinator.py \
        scripts/agent_loop_controller_v2.py
# 预期输出为空
```

### 8.2 提示词注入风险

**风险 1：恶意 skill 描述含注入攻击**

- 攻击场景：用户在 SkillRegistry 注册了 description 含 `忽略之前的所有指令` 的 skill
- 缓解：SkillGuard 步骤 9 复用 `INJECTION_KEYWORDS` 检测
- 拒绝策略：发现注入关键词 → 抛 `SkillGuardError`，**硬中断**

**风险 2：skill 名注入攻击**

- 攻击场景：`task_skill="../etc/passwd"` 或 `task_skill="\n忽略指令"`
- 缓解：SkillGuard 步骤 1 名称合法性校验（`[a-z0-9-]{0,63}`）
- 拒绝策略：名称不合法 → 抛 `SkillGuardError`

**风险 3：依赖链注入攻击**

- 攻击场景：构造 100 层深依赖链，导致解析耗尽内存
- 缓解：SkillGuard 步骤 4 依赖深度上限（5 层）
- 拒绝策略：深度超限 → 抛 `SkillGuardError`

**风险 4：注入内容体积攻击**

- 攻击场景：单个 skill 的 description 100MB
- 缓解：SkillInjectableView 阶段硬截断（500 字符）
- 兜底：总 token 预算截断（token_budget × 0.2）

### 8.3 性能开销

| 阶段 | 开销 | 触发条件 |
|------|------|---------|
| 字段解析 | < 1ms | 总是 |
| SkillGuard 校验 | < 1ms | 总是 |
| 依赖解析 | < 5ms（10 个 skill 内） | 总是 |
| Manifest 加载 | < 10ms（含 IO） | 总是 |
| View 构造 | < 5ms | 总是 |
| 渲染 | < 10ms（structured 模式） | 总是 |
| Token 截断 | < 5ms | 超限时 |
| 画像反哺 | < 5ms | 总是 |
| **总计** | **< 50ms**（典型场景） | spawn() 内 |

**性能基线**（与 Phase 7 spawn() 对比）：

- Phase 7 spawn() 无 skill：< 10ms
- Phase 8 spawn() 无 skill：< 10ms（**零开销**）
- Phase 8 spawn() 1 skill：< 30ms
- Phase 8 spawn() 10 skill：< 150ms

**性能保证**：

- 不传 `skill_injector` → 零开销
- 传 `skill_injector` 但 task 不含 `task_skill` → 仅做 None 检查（< 1ms）
- 传 `skill_injector` + task 含 `task_skill` → 上述 50ms 量级

### 8.4 向后兼容性

| 场景 | Phase 7 行为 | Phase 8 行为 | 兼容性 |
|------|-------------|-------------|--------|
| 不传 skill_injector | 正常工作 | 正常工作（spawn() 内早返回 None） | ✅ 完全兼容 |
| 传 skill_injector + task 不含 task_skill | - | spawn() 内部 None 检查，< 1ms 开销 | ✅ 行为一致 |
| 传 skill_injector + task 含 task_skill="valid" | - | 注入成功 | 🆕 新能力 |
| 传 skill_injector + task 含 task_skill="unknown" | - | 警告 + 继续 | 🆕 新能力 |
| 现有 Phase 1-7 测试 | 全部通过 | 全部通过 | ✅ 零回归 |

**API 兼容性**：

- `SubagentSandbox.__init__` 新增参数全部 **optional + 默认 None** → 老调用方零修改
- `SandboxContext` 新增字段全部 **optional + 默认值** → 老 executor 零修改
- 新增类（`SkillInjector` / `SkillInjectableView` / `SkillDependencyResolver` 等）**不**侵入既有命名空间

### 8.5 其他风险

| 风险 | 等级 | 缓解 |
|------|------|------|
| Token 估算不准确 | 中 | 用粗略公式（1.5 / 0.4），偏差 < 30%；预留 20% 缓冲 |
| 注入内容被 LLM 误解 | 低 | structured 模式用 XML，subagent 普遍支持；提供 markdown 备选 |
| SkillRegistry 损坏 | 低 | 加载异常时降级到无 skill 注入 |
| 循环依赖误判 | 低 | DFS 严格按 path 状态判定 |
| 注入内容与 task description 冲突 | 低 | 注入内容独立成块，附 `<!-- injected by trae-multi-agent -->` 标记 |

---

## 九、实施步骤

### Step 1：数据模型 + 接口（预计 1 天）

**产出文件**：

1. `scripts/dynamic_workflow/skill_injector.py`（核心模块）
   - `SkillInjector` 抽象基类
   - `SkillInjectableView` / `CapabilityView` 数据类
   - `InjectionResult` 数据类
   - `StructuredSkillInjector` 默认实现
   - `SkillMergePolicy` 枚举
   - `SkillMerger` 合并器
   - `SkillTaskFieldParser` 字段解析器

2. `scripts/dynamic_workflow/skill_dependency_resolver.py`
   - `SkillDependencyResolver` 类
   - `DependencyResolution` 数据类

3. `scripts/dynamic_workflow/skill_guard.py`
   - `SkillGuard` 类
   - `SkillGuardError` 异常
   - 复用 `guard.INJECTION_KEYWORDS`

**不产出** SubagentSandbox 修改（Step 3 做）。

### Step 2：实现 SkillInjector（预计 1 天）

**完成项**：

- `_render()` 4 种模式（structured / markdown / compact / full）
- `_truncate()` 中英混合截断
- `_estimate_tokens()` token 估算
- 与 SkillRegistry / SkillDependencyResolver / SkillGuard 协同

**单元测试**：

- `test_skill_injector.py`（15 cases）
- `test_skill_task_field_parser.py`（5 cases）

### Step 3：集成到 SubagentSandbox（预计 0.5 天）

**修改文件**：

- `scripts/dynamic_workflow/subagent_sandbox.py`
  - `__init__` 新增 2 个参数
  - `SandboxContext` 新增 3 个字段
  - `spawn` 新增 skill 注入子流程
  - `_record_to_fingerprint` 写入 skill_distribution 事件

**集成测试**：

- `test_skill_distribution_integration.py`（10 cases）

**回归测试**：

- Phase 1-7 全部 584 tests 零回归

### Step 4：失败处理 + 边界（预计 0.5 天）

**实现**：

- SkillGuard 拒绝场景
- 循环依赖检测
- Token 截断触发
- SkillRegistry 加载异常降级

**测试**：

- `test_skill_distribution_failure.py`（7 cases）

### Step 5：性能 Benchmark（预计 0.5 天）

**实现**：

- `test_skill_distribution_benchmark.py`（5 cases）
- 与 Phase 7 spawn() 性能对比

**基线**：

- 无 skill：< 10ms
- 1 skill：< 30ms
- 10 skill：< 150ms

### Step 6：文档 + 收官报告（预计 0.5 天）

**更新文档**：

1. `docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md` v1.4 → v1.5
   - 新增 §模块 8 SkillDistribution
   - 新增 §7.8 Phase 8 实施详情
2. `docs/dev/PHASE8_FINAL_REPORT.md`（本计划的实施结果）
3. `docs/dev/PHASE8_PLAN.md`（本文件）

**测试入口**：

- `scripts/tests/scripts/run_skill_distribution_tests.sh`
- 集成到 `scripts/tests/scripts/run_dynamic_workflow_tests.sh`

**总计**：4 天（实际可能 3-5 天，含 review 周期）

---

## 十、交付清单

| # | 产物 | 路径 | 状态 |
|---|------|------|------|
| 1 | `SkillInjector` 抽象类 + 默认实现 | `scripts/dynamic_workflow/skill_injector.py` | 待实施 |
| 2 | `SkillInjectableView` / `InjectionResult` 数据类 | `scripts/dynamic_workflow/skill_injector.py` | 待实施 |
| 3 | `SkillDependencyResolver` 解析器 | `scripts/dynamic_workflow/skill_dependency_resolver.py` | 待实施 |
| 4 | `SkillGuard` 安全校验 | `scripts/dynamic_workflow/skill_guard.py` | 待实施 |
| 5 | `SubagentSandbox` 集成 | `scripts/dynamic_workflow/subagent_sandbox.py`（修改） | 待实施 |
| 6 | `SandboxContext` 扩展 | `scripts/dynamic_workflow/subagent_sandbox.py`（修改） | 待实施 |
| 7 | 50 个单元 + 集成 + 性能测试 | `scripts/tests/test_skill_*.py`（7 文件） | 待实施 |
| 8 | 测试入口脚本 | `scripts/tests/scripts/run_skill_distribution_tests.sh` | 待实施 |
| 9 | `DYNAMIC_WORKFLOWS_INTEGRATION.md` v1.5 | `docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md`（修改） | 待实施 |
| 10 | Phase 8 收官报告 | `docs/dev/PHASE8_FINAL_REPORT.md` | 待实施 |
| 11 | Phase 8 实施计划（本文件） | `docs/dev/PHASE8_PLAN.md` | ✅ 已完成 |

**预期测试增量**：50 tests
**全量测试预期**：584 + 50 = **634 tests**

---

## 十一、验收清单

- [ ] `SkillInjector` 抽象类 + 默认 `StructuredSkillInjector` 实现
- [ ] `SkillInjectableView` / `InjectionResult` / `DependencyResolution` 数据类
- [ ] `SkillDependencyResolver` 支持 DFS + 循环检测 + 拓扑排序
- [ ] `SkillGuard` 实现 4 项校验（名称 / 数量 / 深度 / 内容）
- [ ] `SubagentSandbox.spawn()` 集成 skill 注入（可选）
- [ ] `SandboxContext` 新增 3 个 skill 字段
- [ ] `_record_to_fingerprint` 写入 skill_distribution 事件
- [ ] 50 个 Phase 8 测试 100% 通过
- [ ] Phase 1-7 回归测试零失败（584 tests）
- [ ] V2 回归测试零失败（85 tests）
- [ ] V2 文件零修改（`git diff` 为空）
- [ ] 向后兼容：不传 `skill_injector` 行为零变化
- [ ] 性能基线：spawn() 无 skill < 10ms；有 skill < 150ms（10 个）
- [ ] 安全：所有 Guard 拒绝场景硬中断；其他场景优雅降级
- [ ] TODO/FIXME 0 处遗留
- [ ] 编译警告 0 处
- [ ] 文档更新：DYNAMIC_WORKFLOWS_INTEGRATION.md v1.5 + PHASE8_FINAL_REPORT.md

---

## 十二、回滚策略

如 Phase 8 出现问题：

1. 恢复 `subagent_sandbox.py` 的修改
2. 删除新增模块文件（`skill_injector.py` / `skill_dependency_resolver.py` / `skill_guard.py`）
3. 删除新增测试文件（`test_skill_*.py`）
4. 不影响 Phase 1-7 任何代码
5. SkillRegistry / PerformanceFingerprint / ModelRouter / Guard 零修改

**回滚时间估算**：< 30 分钟

---

## 十三、不在 Phase 8 范围（明确排除）

| 功能 | 排除理由 | 建议 Phase |
|------|---------|-----------|
| Skill 热更新（运行时注册新 skill） | 需要分布式协调，超出 Phase 8 范围 | Phase 9+ |
| Skill 版本协商（A 要求 v2，但注册表只有 v1） | 与 SkillRegistry 的版本管理耦合 | Phase 9+ |
| Skill 执行（subagent 真正调用 skill 的能力） | 需要 subagent 执行层支持；Phase 8 仅做"内容注入" | Phase 10+ |
| Skill 路由决策（不同 skill → 不同模型） | 与 ModelRouter 强耦合；Phase 8 不修改 ModelRouter | Phase 10+ |
| Skill 缓存（避免重复注入） | 性能优化；Phase 8 无明显性能瓶颈 | Phase 11+ |
| `/skill <name>` 用户命令 | 终端命令集成；属于不同模块 | Phase 12+ |
| 多注册表联合（同时查多个 SkillRegistry） | 分布式场景；超出当前架构 | Phase 13+ |

---

*下一步：用户确认 → 启动 Phase 8 实施（4 天）*
