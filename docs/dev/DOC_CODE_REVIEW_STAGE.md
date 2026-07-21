# 阶段 8 设计文档：文档对照代码审查（Doc-Code Review）

> **文档类型**：技术方案 spec（v1 — 初版设计）
> **日期**：2026-07-21
> **状态**：⏳ v1 设计稿，待架构师复核 + 用户批准
> **前序**：七阶段标准工作流程（需求→架构→UI→测试设计→任务分解→开发→测试验证）
> **方向**：在七阶段工作流末尾追加"文档对照代码审查"阶段，确保文档设计完整落地
> **核心价值**：开发完成后逐项对照文档检查功能完成情况、集成情况、测试正确性，杜绝"文档写了但代码没实现"的遗漏

---

## 0. 变更履历

### 0.1 v1 初版

| # | 项 | 状态 | 说明 |
|---|----|------|------|
| 0.1.1 | 审查阶段定位与检查维度 | ✅ | 详见 §2 |
| 0.1.2 | 输入输出规范 | ✅ | 详见 §3 |
| 0.1.3 | 核心检查器接口设计 | ✅ | 详见 §4 |
| 0.1.4 | ReviewHandler 集成方案 | ✅ | 详见 §5 |
| 0.1.5 | 工作流 definitions 集成 | ✅ | 详见 §6 |
| 0.1.6 | 测试策略 | ✅ | 详见 §7 |
| 0.1.7 | 风险评估 | ✅ | 详见 §8 |

---

## 1. 背景与目标

### 1.1 用户痛点

当前七阶段标准工作流在阶段 7（测试验证）结束后即视为完成，但存在以下遗漏风险：

```
阶段 1-7 完成后：
❌ 文档中列出的功能 F-001~F-010，代码实际只实现了 F-001~F-008
❌ 架构设计文档定义了模块 A→B→C 的调用链，代码中 B→C 的集成缺失
❌ 测试全部 PASS，但测试用例只覆盖了已实现功能，遗漏了文档中定义但未实现的功能
❌ 需求文档中的验收标准有 10 条，代码只满足了 7 条
❌ TODO/FIXME 注释残留，没有对应实现
```

### 1.2 设计目标

在七阶段工作流末尾追加**阶段 8: 文档对照代码审查**，满足以下强约束：

| 约束 | 描述 | 优先级 |
|------|------|--------|
| **逐项对照** | 将文档中的每个功能点、验收标准、集成点逐项与代码实现对照 | P0 |
| **功能完成度** | 检查文档列出的所有功能是否都有对应代码实现 | P0 |
| **集成完整性** | 检查文档定义的模块间集成关系是否在代码中真实连通 | P0 |
| **测试正确性** | 执行全部测试，确保零失败；检查测试是否覆盖文档定义的功能 | P0 |
| **文档意图遵从** | 检查代码是否完整遵照文档意图开发，无偏离 | P0 |
| **Review 环节** | 生成结构化审查报告，包含通过/不通过判定和改进建议 | P0 |
| **不模拟/不占位** | 所有检查逻辑真实实现，禁 mock/简化 | P0 |
| **中文注释** | 所有关键逻辑中文注释，符合 Python 代码规范 | P0 |

### 1.3 范围与不范围

**范围内**：

| 能力 | 描述 |
|------|------|
| ✅ 文档解析 | 解析 PRD、架构设计、SPEC 中的功能列表和验收标准 |
| ✅ 代码扫描 | 扫描源码中的函数、类、模块、API 端点 |
| ✅ 功能对照 | 逐项检查文档功能点是否有对应代码实现 |
| ✅ 集成检查 | 检查文档定义的模块依赖关系是否在代码中体现 |
| ✅ 测试验证 | 执行测试命令，收集通过/失败/跳过统计 |
| ✅ TODO/FIXME 检测 | 扫描代码中残留的 TODO/FIXME 注释 |
| ✅ 审查报告 | 生成结构化的文档对照审查报告 |

**不范围**：

| 不实现 | 原因 |
|--------|------|
| ❌ 语义级需求理解 | 需要 LLM 能力，由宿主模型在 review prompt 中完成 |
| ❌ 代码质量评分 | 已由 v2.3 代码走读模块覆盖 |
| ❌ 性能基准测试 | 不在本阶段范围 |

---

## 2. 审查阶段定位与检查维度

### 2.1 工作流定位

```
阶段 1: 需求分析（产品经理）     → 产出 PRD（功能列表 + 验收标准）
    ↓ 评审通过
阶段 2: 架构设计（架构师）       → 产出架构文档（模块划分 + 集成关系）
    ↓ 评审通过
阶段 3: UI 设计（UI 设计师）     → 产出 UI 设计稿
    ↓ 评审通过
阶段 4: 测试设计（测试专家）     → 产出测试计划（测试用例 + 覆盖矩阵）
    ↓ 评审通过
阶段 5: 任务分解（独立开发者）   → 产出任务清单
    ↓
阶段 6: 开发实现（独立开发者）   → 产出代码
    ↓
阶段 7: 测试验证（测试专家）     → 执行测试，产出测试报告
    ↓
阶段 8: 文档对照代码审查（多角色）→ 对照文档逐项检查，产出审查报告 ★新增
    ↓ 审查通过
发布
```

### 2.2 六大检查维度

| # | 维度 | 检查内容 | 输入文档 | 检查方法 |
|---|------|----------|----------|----------|
| D1 | **功能完成度** | 文档中每个功能点是否有对应代码实现 | PRD 功能列表、SPEC 功能需求 | 解析文档功能 ID → 代码扫描函数/类/API → 匹配 |
| D2 | **集成完整性** | 文档定义的模块间集成关系是否在代码中体现 | 架构设计文档、模块依赖图 | 解析模块依赖 → 代码扫描 import/call → 匹配 |
| D3 | **测试正确性** | 全部测试通过，无失败；测试覆盖文档功能 | 测试计划、测试报告 | 执行测试命令 → 解析结果 → 对照功能覆盖 |
| D4 | **验收标准满足** | 文档中每条验收标准是否被代码满足 | PRD 验收标准、SPEC 验收标准 | 解析验收标准 → 代码+测试双重验证 |
| D5 | **TODO/FIXME 清零** | 代码中无残留的 TODO/FIXME 注释 | 源码 | 正则扫描 TODO/FIXME → 逐项确认有对应实现 |
| D6 | **文档意图遵从** | 代码实现未偏离文档设计意图 | 全部文档 | 多角色 review（架构师+开发者+测试专家） |

### 2.3 审查判定规则

```
判定逻辑（全部满足才通过）：

D1 功能完成度:  实现率 = 已实现功能数 / 文档功能总数 ≥ 100%
D2 集成完整性:  集成率 = 已实现集成数 / 文档集成总数 ≥ 100%
D3 测试正确性:  失败数 = 0（passed > 0, failed = 0）
D4 验收标准:    满足率 = 已满足标准数 / 验收标准总数 ≥ 100%
D5 TODO/FIXME:  残留数 = 0（所有 TODO/FIXME 都有对应实现）
D6 文档意图:    偏离数 = 0（多角色 review 无偏离项）

最终判定:
- 全部通过 → 审查通过，可发布
- 任一不通过 → 审查不通过，列出缺口清单，回退到阶段 6 修复
```

---

## 3. 输入输出规范

### 3.1 输入

| 输入 | 来源 | 格式 | 必需 |
|------|------|------|------|
| 项目根目录 | 阶段 1-7 的工作目录 | `Path` | ✅ |
| PRD 文档 | 阶段 1 产出 | Markdown（含功能列表表格） | ✅ |
| 架构设计文档 | 阶段 2 产出 | Markdown（含模块划分、集成关系） | ✅ |
| SPEC 文档 | 阶段 1-2 共识 | Markdown（含功能需求、验收标准） | ✅ |
| 测试计划 | 阶段 4 产出 | Markdown（含测试用例、覆盖矩阵） | ✅ |
| 测试命令 | LoopConfig.test_command | 字符串 | ✅ |
| 代码仓库 | 阶段 6 产出 | 源码文件 | ✅ |

### 3.2 输出

| 输出 | 格式 | 说明 |
|------|------|------|
| 审查报告 | Markdown 文件 `<project>-DOC-CODE-REVIEW-REPORT.md` | 结构化报告，含六大维度检查结果 |
| 审查结果数据 | `dict`（StageResult.artifacts） | 供下游消费的结构化数据 |
| 缺口清单 | 报告内嵌表格 | 不通过时列出每个缺口的功能 ID、描述、位置 |

### 3.3 审查报告结构

```markdown
# 文档对照代码审查报告

## 审查概览
- 项目名称、审查时间、审查角色
- 最终判定：通过 / 不通过

## D1 功能完成度
| 功能 ID | 功能名称 | 文档来源 | 代码位置 | 状态 |
|---------|----------|----------|----------|------|
| F-001   | 登录     | PRD §2.1 | auth.py  | ✅ 已实现 |
| F-002   | 注册     | PRD §2.2 | -        | ❌ 未实现 |

## D2 集成完整性
| 集成关系 | 文档来源 | 代码位置 | 状态 |
|----------|----------|----------|------|
| A→B      | 架构 §3.1 | a.py import b | ✅ |
| B→C      | 架构 §3.2 | -        | ❌ 缺失 |

## D3 测试正确性
- 测试命令: python3 -m pytest
- 通过: 45  失败: 0  跳过: 2
- 覆盖功能: F-001~F-008（遗漏 F-009, F-010）

## D4 验收标准满足
| 验收标准 | 文档来源 | 验证方式 | 状态 |
|----------|----------|----------|------|
| 登录响应 < 200ms | PRD §2.1 | 测试 | ✅ |

## D5 TODO/FIXME 清零
| 文件 | 行号 | 内容 | 状态 |
|------|------|------|------|
| -    | -    | -    | ✅ 无残留 |

## D6 文档意图遵从
- 架构师 review: 无偏离
- 开发者 review: 无偏离
- 测试专家 review: 无偏离

## 缺口清单（审查不通过时）
| # | 维度 | 缺口描述 | 优先级 | 建议修复方式 |
|---|------|----------|--------|-------------|

## 改进建议
```

---

## 4. 核心检查器接口设计

### 4.1 DocCodeConsistencyChecker 类

```python
class DocCodeConsistencyChecker:
    """文档对照代码一致性检查器。

    职责：
    1. 解析文档（PRD/SPEC/架构/测试计划）中的功能点、验收标准、集成关系
    2. 扫描代码中的函数、类、模块、API、import 依赖
    3. 逐项对照检查六大维度
    4. 生成结构化审查报告
    """

    def __init__(self, project_root: Path, doc_paths: dict, test_command: str):
        """构造检查器。

        Args:
            project_root: 项目根目录
            doc_paths: 文档路径字典 {
                "prd": Path, "architecture": Path,
                "spec": Path, "test_plan": Path
            }
            test_command: 测试执行命令
        """

    def check_all(self) -> ConsistencyReport:
        """执行全部六大维度检查，返回完整报告。"""

    def check_feature_completeness(self) -> list[FeatureCheckItem]:
        """D1: 功能完成度检查。"""

    def check_integration_completeness(self) -> list[IntegrationCheckItem]:
        """D2: 集成完整性检查。"""

    def check_test_correctness(self) -> TestCheckResult:
        """D3: 测试正确性检查。"""

    def check_acceptance_criteria(self) -> list[AcceptanceCheckItem]:
        """D4: 验收标准满足检查。"""

    def check_todo_fixme(self) -> list[TodoItem]:
        """D5: TODO/FIXME 清零检查。"""

    def check_doc_intent_alignment(self) -> list[DeviationItem]:
        """D6: 文档意图遵从检查（基于代码-文档关键词匹配）。"""

    def generate_report(self, report: ConsistencyReport) -> str:
        """生成 Markdown 审查报告。"""
```

### 4.2 数据结构

```python
@dataclass
class FeatureCheckItem:
    """功能完成度检查项。"""
    feature_id: str          # 功能 ID（如 F-001）
    feature_name: str        # 功能名称
    doc_source: str          # 文档来源（如 "PRD §2.1"）
    code_location: str       # 代码位置（如 "auth.py:login()"），空表示未找到
    status: str              # "implemented" / "missing"
    evidence: str            # 证据描述

@dataclass
class IntegrationCheckItem:
    """集成完整性检查项。"""
    integration_desc: str    # 集成关系描述（如 "模块A→模块B"）
    doc_source: str          # 文档来源
    code_location: str       # 代码位置（如 "a.py: import b"），空表示未找到
    status: str              # "connected" / "missing"

@dataclass
class TestCheckResult:
    """测试正确性检查结果。"""
    test_command: str        # 执行的测试命令
    passed: int              # 通过数
    failed: int              # 失败数
    skipped: int             # 跳过数
    covered_features: list   # 测试覆盖的功能 ID 列表
    uncovered_features: list # 未覆盖的功能 ID 列表
    test_output_tail: str    # 测试输出末尾（诊断用）

@dataclass
class AcceptanceCheckItem:
    """验收标准检查项。"""
    criteria_id: str         # 验收标准 ID
    criteria_desc: str       # 验收标准描述
    doc_source: str          # 文档来源
    verification: str        # 验证方式（"test" / "code" / "manual"）
    status: str              # "satisfied" / "unsatisfied"

@dataclass
class TodoItem:
    """TODO/FIXME 检查项。"""
    file_path: str           # 文件路径
    line_number: int         # 行号
    content: str             # TODO/FIXME 内容
    has_implementation: bool # 是否有对应实现

@dataclass
class DeviationItem:
    """文档意图偏离项。"""
    dimension: str           # 偏离维度
    doc_intent: str          # 文档意图
    code_reality: str        # 代码实际情况
    severity: str            # "high" / "medium" / "low"

@dataclass
class ConsistencyReport:
    """一致性检查完整报告。"""
    project_name: str
    check_time: str
    feature_checks: list     # list[FeatureCheckItem]
    integration_checks: list # list[IntegrationCheckItem]
    test_result: object      # TestCheckResult
    acceptance_checks: list  # list[AcceptanceCheckItem]
    todo_items: list         # list[TodoItem]
    deviation_items: list    # list[DeviationItem]
    overall_passed: bool     # 最终判定
    gap_list: list           # 缺口清单
```

---

## 5. ReviewHandler 集成方案

### 5.1 ReviewHandler 设计

ReviewHandler 继承 StageHandler，作为 Ralph 循环的可选第 5 阶段：

```python
class ReviewHandler(StageHandler):
    """文档对照代码审查阶段 handler。

    行为：
    1. 收集阶段 1-7 的文档产出路径
    2. 调用 DocCodeConsistencyChecker 执行六大维度检查
    3. 生成审查报告文件
    4. 根据审查结果返回 StageResult
       - 全部通过 → success
       - 有缺口 → retriable（回退到 dev 修复）
       - 检查器异常 → fatal
    """
    name = "review"
    kind = "review"
```

### 5.2 StageKind 扩展

在 `loop_controller.py` 的 `StageKind` 枚举中新增：

```python
class StageKind(str, Enum):
    PLAN = "plan"
    DEV = "dev"
    VERIFY = "verify"
    FIX = "fix"
    REVIEW = "review"  # ★新增：文档对照代码审查
```

### 5.3 阶段顺序配置

默认 Ralph 循环不包含 review（保持向后兼容），需要显式启用：

```python
# 启用 review 的阶段顺序
stage_order = [PLAN, DEV, VERIFY, FIX, REVIEW]

# 或在 verify 成功后执行 review
stage_order = [PLAN, DEV, VERIFY, REVIEW]
```

---

## 6. 工作流 definitions 集成

### 6.1 workflows/definitions.json 更新

在 `standard-dev-workflow` 的 steps 数组末尾追加：

```json
{
  "step_id": "doc-code-review",
  "name": "文档对照代码审查",
  "description": "对照文档逐项检查功能完成情况、集成情况、测试正确性",
  "role_id": "multi-role",
  "action": "doc_code_review",
  "inputs": {
    "prd": "${prd}",
    "architecture": "${architecture}",
    "test_plan": "${test_plan}",
    "code": "${code}"
  },
  "outputs": {
    "review_report": null
  },
  "conditions": {
    "requires_previous_step_pass": true
  },
  "timeout": 3600,
  "retry_count": 2,
  "status": "pending"
}
```

---

## 7. 测试策略

### 7.1 单元测试

| 测试文件 | 覆盖范围 |
|----------|----------|
| `test_doc_code_consistency_checker.py` | 文档解析、代码扫描、六大维度检查、报告生成 |
| `test_review_handler.py` | ReviewHandler 的 handle/do_handle、成功/失败/异常路径 |

### 7.2 测试用例设计

**DocCodeConsistencyChecker 测试用例**：

| # | 用例 | 验证点 |
|---|------|--------|
| T1 | 解析 PRD 功能列表表格 | 正确提取功能 ID、名称、描述 |
| T2 | 解析架构文档模块依赖 | 正确提取模块间集成关系 |
| T3 | 扫描 Python 代码函数/类 | 正确提取函数名、类名、文件位置 |
| T4 | 功能完成度：全部实现 | 返回所有 status=implemented |
| T5 | 功能完成度：部分缺失 | 返回缺失功能 status=missing |
| T6 | 集成完整性：import 匹配 | 正确匹配模块间 import 关系 |
| T7 | 测试正确性：全部通过 | passed>0, failed=0 |
| T8 | 测试正确性：有失败 | failed>0 |
| T9 | TODO/FIXME 扫描 | 正确提取 TODO/FIXME 及位置 |
| T10 | 验收标准解析 | 正确提取验收标准条目 |
| T11 | 报告生成：通过场景 | overall_passed=True |
| T12 | 报告生成：不通过场景 | overall_passed=False, gap_list 非空 |

**ReviewHandler 测试用例**：

| # | 用例 | 验证点 |
|---|------|--------|
| H1 | 审查通过 → success | kind=success, 报告文件生成 |
| H2 | 审查不通过 → retriable | kind=retriable, 缺口清单非空 |
| H3 | 检查器异常 → fatal | kind=fatal, error 非空 |
| H4 | 文档缺失 → retriable | kind=retriable, 提示文档缺失 |

### 7.3 测试脚本

```bash
# scripts/tests/scripts/run_doc_review_tests.sh
python3 -m pytest scripts/tests/test_doc_code_consistency_checker.py -v
python3 -m pytest scripts/tests/test_review_handler.py -v
```

---

## 8. 风险评估

| # | 风险 | 概率 | 影响 | 缓解措施 |
|---|------|------|------|----------|
| R1 | 文档格式不统一导致解析失败 | 中 | 中 | 支持多种 Markdown 表格格式 + 容错解析 |
| R2 | 代码扫描误判功能是否实现 | 中 | 中 | 关键词匹配 + 函数名/类名多重验证 |
| R3 | 测试执行超时 | 低 | 低 | 可配置超时时间，默认 600s |
| R4 | review 阶段增加循环时间 | 高 | 低 | 仅在 verify 成功后执行，且可配置关闭 |
| R5 | 向后兼容性 | 低 | 高 | StageKind.REVIEW 可选，默认 stage_order 不含 review |

---

## 9. 实施步骤

| 步骤 | 内容 | 产出文件 |
|------|------|----------|
| 1 | 编写设计文档（本文档） | `docs/dev/DOC_CODE_REVIEW_STAGE.md` |
| 2 | 编写 review prompt 模板 | `docs/spec/role-prompts/doc-code-review.md` |
| 3 | 编写 review 输出模板 | `docs/roles/doc-code-review/DOC_CODE_REVIEW_TEMPLATE.md` |
| 4 | 更新 SKILL.md 工作流 | `SKILL.md` |
| 5 | 更新 README.md + definitions.json | `README.md`, `workflows/definitions.json` |
| 6 | 实现核心检查器 | `scripts/doc_code_consistency_checker.py` |
| 7 | 实现 ReviewHandler | `scripts/autonomous/handlers/review_handler.py` |
| 8 | 更新 loop_controller + handlers/__init__ | `scripts/autonomous/loop_controller.py`, `handlers/__init__.py` |
| 9 | 编写单元测试 | `scripts/tests/test_doc_code_consistency_checker.py`, `test_review_handler.py` |
| 10 | 编写测试脚本并执行 | `scripts/tests/scripts/run_doc_review_tests.sh` |
| 11 | 架构师审查（含 loop 方案） | 架构审查报告 |
| 12 | 实现 WorkflowLoopController | `scripts/workflow_loop_controller.py` |
| 13 | 编写 WorkflowLoopController 测试 | `scripts/tests/test_workflow_loop_controller.py` |
| 14 | 提供 CLI 入口脚本 | `scripts/run_workflow_loop.py` |
| 15 | 更新 SKILL.md / CHANGELOG.md | `SKILL.md`, `CHANGELOG.md` |

---

## 10. 八阶段循环（Workflow Loop）

> **章节背景**：用户明确要求"八阶段 整体构建为一个 loop"，即整个八阶段工作流构建为一个完整的循环，支持审查失败后回退到对应阶段修复，避免一次性失败导致整个流程作废。

### 10.1 设计目标

| 目标 | 描述 |
|------|------|
| **整体 loop** | 八阶段构建为一个完整的循环（WorkflowLoopController），而非简单线性流程 |
| **审查驱动** | 阶段 8 审查结果是循环的核心驱动：通过则结束，不通过则回退 |
| **精准回退** | 根据缺口维度（D1-D6）决定回退到哪个阶段，而非一刀切回到起点 |
| **迭代上限** | 最大迭代次数限制（默认 3 次），防止无限循环 |
| **上下文累计** | 跨迭代保留产出（`_accumulated_artifacts`），后续阶段可访问前序产出 |
| **真实执行** | 所有阶段真实执行，禁 mock/占位/简化 |

### 10.2 WorkflowLoopController 定位

**与 RalphLoopController 的关系**：

| 维度 | RalphLoopController | WorkflowLoopController |
|------|---------------------|------------------------|
| 循环范围 | plan → dev → verify → fix 小循环 | 需求→架构→UI→测试设计→任务分解→开发→测试验证→文档审查 八阶段大循环 |
| 阶段数 | 4-5 个（可选 REVIEW） | 8 个（固定） |
| 触发场景 | 单一开发任务的自主迭代 | 完整项目生命周期的端到端流程 |
| 失败处理 | 退避重试 + 连续失败 abort | 回退到对应阶段 + max_iterations 限制 |
| 阶段接口 | `handler.handle(iter_ctx: IterationContext)` | `stage_executor(stage, context) -> StageExecutionResult` |
| 使用场景 | 阶段 6（开发）内部小循环 | 编排整个项目八阶段 |

**嵌套策略**：
- 外层使用 `WorkflowLoopController` 编排八阶段
- 内层阶段 6（开发）可注入 `RalphLoopController` 做 plan→dev→verify→fix 小循环
- ReviewHandler 同时被两个控制器使用（通过不同接口）

### 10.3 核心数据结构

#### 10.3.1 WorkflowStage 枚举

```python
class WorkflowStage(str, Enum):
    """八阶段工作流阶段枚举。"""
    REQUIREMENTS = "requirements"           # 阶段 1: 需求分析（产品经理）
    ARCHITECTURE = "architecture"           # 阶段 2: 架构设计（架构师）
    UI_DESIGN = "ui_design"                 # 阶段 3: UI 设计（UI 设计师）
    TEST_DESIGN = "test_design"             # 阶段 4: 测试设计（测试专家）
    TASK_BREAKDOWN = "task_breakdown"       # 阶段 5: 任务分解（独立开发者）
    DEVELOPMENT = "development"             # 阶段 6: 开发实现（独立开发者）
    TEST_VERIFICATION = "test_verification" # 阶段 7: 测试验证（测试专家）
    DOC_CODE_REVIEW = "doc_code_review"     # 阶段 8: 文档对照代码审查（多角色）
```

每个阶段附带三个属性：
- `stage_number`：阶段编号（1-8）
- `role_name`：对应角色名称（中文）
- `output_name`：阶段产出名称（中文）

#### 10.3.2 WorkflowStage ↔ StageKind 映射

为避免概念重叠混淆，建立以下映射关系（通过 `WorkflowStage.to_stage_kind()` 方法）：

| WorkflowStage | StageKind | 说明 |
|---------------|-----------|------|
| REQUIREMENTS | None | Ralph 小循环无对应阶段 |
| ARCHITECTURE | None | Ralph 小循环无对应阶段 |
| UI_DESIGN | None | Ralph 小循环无对应阶段 |
| TEST_DESIGN | None | Ralph 小循环无对应阶段 |
| TASK_BREAKDOWN | PLAN | 任务分解对应 Ralph 的 plan 阶段 |
| DEVELOPMENT | DEV | 开发对应 Ralph 的 dev 阶段 |
| TEST_VERIFICATION | VERIFY | 测试验证对应 Ralph 的 verify 阶段 |
| DOC_CODE_REVIEW | REVIEW | 文档审查对应 Ralph 的 review 阶段 |

注：FIX 阶段是 Ralph 特有的修复阶段，在八阶段大循环中通过回退机制实现。

### 10.4 RollbackStrategy 回退策略

**缺口维度到回退阶段的映射表**：

| 缺口维度 | 回退到阶段 | 理由 |
|----------|-----------|------|
| D1 功能完成度 | DEVELOPMENT（阶段 6） | 功能缺失需补开发 |
| D2 集成完整性 | DEVELOPMENT（阶段 6） | 集成缺失需补开发 |
| D3 测试正确性 | TEST_VERIFICATION（阶段 7） | 测试失败需修复测试或代码 |
| D4 验收标准 | DEVELOPMENT（阶段 6） | 验收未满足需补开发 |
| D5 TODO/FIXME | DEVELOPMENT（阶段 6） | TODO 未实现需补开发 |
| D6 文档意图 | DEVELOPMENT（阶段 6） | 文档偏离需调整代码 |

**优先级规则**：
- 多缺口同时存在时，优先回退到更早的阶段（DEVELOPMENT < TEST_VERIFICATION）
- 无缺口时返回 None，表示审查通过，无需回退
- 默认回退到 DEVELOPMENT（兜底）

**实现位置**：`scripts/workflow_loop_controller.py` 的 `RollbackStrategy` 类

### 10.5 最大迭代次数限制

- 默认 `max_iterations=3`
- 可通过构造函数参数 `max_iterations` 配置
- 达到上限后终止循环，返回 `WorkflowRunResult`，包含 `final_gaps` 剩余缺口清单
- **不会无限循环**：max_iterations 是硬上限，无论审查是否通过都会终止

### 10.6 累计上下文传递机制

- `WorkflowLoopController._accumulated_artifacts: Dict[str, Any]` 字段跨迭代保留
- 每次阶段执行后，`result.artifacts` 会被 `update` 到 `_accumulated_artifacts`
- 下一次迭代的 `exec_context["accumulated_artifacts"]` 包含之前所有迭代的产出
- 用途：让后续阶段访问前序阶段的产出（如阶段 8 通过 `prd_path` 访问阶段 1 的 PRD 路径）

### 10.7 工作流执行流程

```
WorkflowLoopController.run()
    │
    ├─ for iter_idx in 1..max_iterations:
    │    │
    │    ├─ 计算起始阶段索引
    │    │   - 第 1 次迭代：从阶段 1 开始
    │    │   - 后续迭代：从上次回退目标开始
    │    │
    │    ├─ for stage in stage_order[start_idx..end]:
    │    │    │
    │    │    ├─ 构建 exec_context（含 iteration_index, accumulated_artifacts, doc_paths 等）
    │    │    ├─ 调用 stage_executor(stage, exec_context) -> StageExecutionResult
    │    │    ├─ 更新 _accumulated_artifacts
    │    │    │
    │    │    ├─ if 阶段失败 and 不是审查阶段:
    │    │    │    └─ 终止本次迭代，记录失败
    │    │    │
    │    │    └─ if 是审查阶段 (DOC_CODE_REVIEW):
    │    │         ├─ 提取 overall_passed 和 gap_list
    │    │         ├─ if 审查通过:
    │    │         │    └─ overall_success = True，退出循环
    │    │         └─ if 审查不通过:
    │    │              └─ RollbackStrategy.determine_rollback(gaps) -> 回退阶段
    │    │
    │    └─ 记录本次迭代 (WorkflowIterationRecord)
    │
    └─ 返回 WorkflowRunResult（含迭代历史、最终缺口、累计产出）
```

### 10.8 与 autonomous 模块的集成路径

**集成方式**：WorkflowLoopController 作为独立组件，通过 `stage_executor` 回调注入阶段执行逻辑，与 RalphLoopController 解耦。

**集成路径**：

```
用户目标
    ↓
WorkflowLoopController.run()
    │
    ├─ 阶段 1-5（规划阶段）：stage_executor 回调（由上层 CLI / dispatcher 实现）
    │   - 可调用产品经理/架构师/UI 设计师/测试专家/独立开发者角色
    │   - 产出 PRD/架构/UI 设计稿/测试计划/任务清单
    │
    ├─ 阶段 6（开发）：stage_executor 回调
    │   - 可注入 RalphLoopController 做 plan→dev→verify→fix 内部小循环
    │   - 产出代码实现
    │
    ├─ 阶段 7（测试验证）：stage_executor 回调
    │   - 执行测试命令，产出测试报告
    │
    ├─ 阶段 8（文档对照审查）：ReviewHandler.do_handle(iter_ctx)
    │   - 调用 DocCodeConsistencyChecker 执行六大维度检查
    │   - 产出审查报告 + 缺口清单
    │   - 审查通过 → success
    │   - 审查不通过 → retriable + 缺口清单
    │
    └─ 审查失败 → RollbackStrategy 决定回退阶段 → 下一次迭代
```

**入口脚本**：`scripts/run_workflow_loop.py` 提供 CLI 入口，支持：
- `--project-root`：项目根目录
- `--max-iterations`：最大迭代次数（默认 3）
- `--doc-paths`：文档路径字典（JSON 字符串）
- `--test-command`：测试命令
- `--verbose`：详细日志

### 10.9 风险与缓解

| # | 风险 | 概率 | 影响 | 缓解措施 |
|---|------|------|------|----------|
| R1 | max_iterations 设置过小导致未收敛 | 中 | 中 | 默认 3 次，可根据项目复杂度调整 |
| R2 | 回退策略震荡（A→B→A→B） | 低 | 中 | max_iterations 硬上限兜底；回退映射不存在环路 |
| R3 | 累计上下文过大导致内存膨胀 | 低 | 低 | artifacts 应仅保存必要字段（路径、摘要），避免保存大文本 |
| R4 | 阶段执行异常未捕获 | 低 | 中 | run() 方法对 stage_executor 异常做 try/except，记录为失败 |
| R5 | 与 RalphLoopController 职责重叠 | 中 | 低 | 通过 to_stage_kind() 映射明确关系，文档说明使用场景 |

### 10.10 测试策略

| 测试 ID | 用例 | 验证点 |
|---------|------|--------|
| W1 | 全部阶段通过 | overall_success=True，单次迭代 |
| W2 | 审查失败回退到 DEVELOPMENT | 回退后第二次迭代从 DEVELOPMENT 开始 |
| W3 | 达到 max_iterations 限制 | 终止循环，返回 final_gaps |
| W4a-e | RollbackStrategy 各维度回退 | D1/D2/D3/D5/D6 + 混合缺口的回退阶段 |
| W5 | 执行结果摘要生成 | summary() 输出包含迭代历史和缺口 |
| W6 | 阶段失败终止迭代 | 非审查阶段失败时立即终止本次迭代 |
| W7 | WorkflowStage 枚举属性 | stage_number/role_name/output_name 正确 |
| W8 | 累计上下文跨迭代传递 | 第二次迭代 exec_context["accumulated_artifacts"] 包含第一次产出 |
| W9 | 端到端集成（真实 ReviewHandler） | WorkflowLoopController + ReviewHandler 完整流程 |
| W10 | D3 测试失败回退到 TEST_VERIFICATION | 验证回退后下一次迭代从 TEST_VERIFICATION 开始 |

---

## 11. 变更履历（v2 追加）

### 11.1 v2 — 八阶段整体构建为 loop

| # | 项 | 状态 | 说明 |
|---|----|------|------|
| 11.1.1 | WorkflowLoopController 循环控制器 | ✅ | 详见 §10.2 |
| 11.1.2 | RollbackStrategy 回退策略 | ✅ | 详见 §10.4 |
| 11.1.3 | 最大迭代次数限制 | ✅ | 详见 §10.5 |
| 11.1.4 | 累计上下文传递机制 | ✅ | 详见 §10.6 |
| 11.1.5 | WorkflowStage ↔ StageKind 映射 | ✅ | 详见 §10.3.2 |
| 11.1.6 | CLI 入口脚本 | ✅ | `scripts/run_workflow_loop.py` |
| 11.1.7 | W8/W9/W10 测试补充 | ✅ | 详见 §10.10 |
| 11.1.8 | 架构师审查 | ✅ | 本审查报告 |
