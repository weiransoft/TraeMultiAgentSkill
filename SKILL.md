---
name: multi-agent-team
slug: multi-agent-team
description: 基于任务类型动态调度到合适的智能体角色（架构师、产品经理、测试专家、独立开发者、UI 设计师）。支持多智能体协作、共识机制、完整项目生命周期管理、规范驱动开发、代码走读审查和项目理解能力。支持中英文双语。v2.4 新增 Karpathy 四大核心原则，v2.5 新增 Cybernetics 工程控制论增强，v2.6 新增 Ponytail 决策梯（少写多余代码）、Autonomous 自主迭代模式、Dynamic Workflows 6 大模式、插件热加载，v2.7 新增 UI/UX 巡检分析与视觉回归测试脚本，v2.7.1 修订 AI 诚实降级、真实语义匹配、双宿主清单同步与 v1 死代码清算，v2.9 新增既有系统理解能力（黑盒 Web 系统反向理解：Playwright 登录 + BFS 遍历 + DB/Redis 只读内省 + 10 节理解文档），v2.9.1 新增 SU 专家详说阶段（SFD：五专家分节撰写并装配 SYSTEM_FUNCTION_DOC.md），v2.9.2 修复 D1（understanding.json 的 meta.run_status 渲染收口即终态，与 run_meta 一致），v2.10 新增方案撰写全流程（业界调研→方案撰写→多角色交叉评审→共识收敛→方案修改闭环），v2.11 新增业务动线分析与页面设计闭环（SU 页面发现→断点检测→漏洞扫描→缺失页面设计→proposal-review 评审→开发→UI/UX 测试），v2.12 新增浏览器端页面功能与视觉测试闭环（操作点细化→六类数据测试集→用例多角色评审→agent-browser/Trae内置浏览器/Chrome DevTools MCP 三宿主逐一执行→全程步骤级记录与阻碍闭环）。
---
# Multi-Agent Team Dispatcher (AI-Enhanced)
基于任务类型和上下文，自动调度到最合适的智能体角色（架构师、产品经理、测试专家、Solo Coder、UI 设计师）。

> **能力实现方式说明（v2.7.1 诚实标注）**：
> - **提示词层 AI（宿主 LLM 完成）**：任务语义理解、角色智能匹配、多角色共识决策、架构设计审查——由 Trae/Claude 宿主大模型直接执行，脚本仅提供候选清单与规则约束。
> - **脚本层确定性工具（Python 实现）**：代码地图扫描、UI/UX 巡检、视觉回归对比、决策梯合规检查、TFIDF/Hashing 文本相似度——为无 LLM 的独立进程，提供可复现的确定性结果。
> - **降级模式说明**：当脚本层需要语义相似度但无网络/模型时，自动降级到 TFIDF/Hashing 本地算法；SubAgent 调用在无真实 claude 命令时返回错误（`fallback_mode: error`），真实并行子代理由宿主 Task 机制实现。

**v2.7.1 修订（AI 诚实化 + 代码清算 + 双宿主同步）**:
- 🤥→✅ AI 诚实降级：`ai_assistant.py` / `ai_semantic_matcher.py` 移除全部模拟响应，无 AI 客户端时明确抛错并降级到确定性匹配，不再伪装 AI 输出；`claude_code_subagent_adapter.py` 移除 `_simulate_subagent_call`，无真实 subagent 时返回 `success=False`
- 🔢 真实语义匹配：`role_matcher.py` 从关键词重叠升级为本地 embedder（TFIDF/Hashing）向量余弦相似度
- 🛡️ embedder 三级降级链：`goal_orchestrator.py` SentenceTransformer → TFIDF → Hashing，无网络/无模型时自动降级
- 🎭 Claude Code 真实 SubAgent：`.claude/agents/` 新增 5 角色定义文件，替代脚本模拟
- 🔄 双宿主清单同步：`sync_manifests.py` CI 校验三份 manifest（skill-manifest.yaml / skills-index.json / claude-code-skill.json）版本与命名一致
- 🧹 v1 死代码清算：删除 `workflow_engine.py` / `code_map_generator.py` / `test_v2_components.py`，`dispatch/legacy.py` 切换到 `WorkflowEngineV2`
- 📦 依赖显式化：`requirements.txt` 标注软依赖（playwright / Pillow / sentence-transformers 均为可选）

**v2.9 新增（既有系统理解能力 SU）**:
- 🔍 黑盒 Web 系统反向理解：Playwright 自动登录 + 图式 BFS 页面遍历 + DB/Redis 只读内省 + UI↔API↔DB 三角关联，产出 10 节《系统功能理解文档》
- 🚫 五条安全红线：凭据不落盘明文、DB 严格只读、Redis 只读白名单、浏览器网络层拦截全部非 GET 与白名单外域、危险按钮零点击
- 🤖 两阶段工作流：脚本层零 LLM 采集证据，宿主 LLM 按 `docs/spec/role-prompts/su-llm-backfill.md` 契约回填语义结论，CLI `--render-only` 校验收口

**v2.9.1 新增（SU 专家详说 SFD）**:
- 🧾 `--detailed-doc` 前置校验（锚定 run_id、findings 双源一致）→ 五视角素材包 + 8 节大纲骨架；宿主 LLM 派发五专家（`docs/spec/role-prompts/su-detailed-*.md`）；`--assemble` 装配 `SYSTEM_FUNCTION_DOC.md` + `assembly-report.json`
- 🔒 E-n 引用 (seq,ref) 双键 + 锚点漂移检测 + 四判据凭据扫描；终稿原子写、`--force` 保护；零网络零凭据零新依赖（`scripts/su/detailed_doc.py`）

**v2.5 新增（Cybernetics 工程控制论增强）**:
> 参考来源：https://github.com/Jiaqi-Guo-0114/cybernetics-agent  
> 理论依据：钱学森工程控制论（系统工程、系统学）、ICLR 2026 Profile-Aware Maneuvering 架构、Norbert Wiener 控制论、Ashby 必要多样性定律  
> - 🔄 三环控制模型：战略层、战术层、执行层
> - 💫 反馈控制环：感知-决策-执行-反馈完整闭环
> - 📊 性能画像：执行案例记录、相似案例检索
> - 🛡️ 守护协调器：执行前验证、异常检测、AI增强风险评估
**v2.4 新增（Karpathy 四大核心原则）**:
- 🧠 **Think Before Coding（三思而后行）**: 明确假设、呈现权衡、遇到不清就问
- 🎯 **Simplicity First（简单优先）**: 最小代码、无 speculative features、无过度抽象
- 🔬 **Surgical Changes（精准修改）**: 只改需要的、不改无关的、保持风格一致
- ✅ **Goal-Driven Execution（目标驱动）**: 定义成功标准、验证检查点、迭代直到完成

**v2.6 新增（Ponytail 决策梯 + 自主编排 + 插件热加载）**:
- 🪜 Ponytail 决策梯：6 步决策梯 + 16 条不可简化红线，少写多余代码
- 🤖 Autonomous 自主迭代模式：plan→dev→verify→fix 4 阶段循环，Ralph 风格自主编排
- 🔄 Dynamic Workflows 6 大模式：分类并行、扇出聚合、对抗验证、生成筛选、锦标赛、循环直到完成
- 🔌 插件热加载 V3 架构：Drop-in 目录 / Hot Register API / HotReloadWatcher 3 种加载路径

**v2.7 新增（UI/UX 巡检 + 视觉回归测试）**:
- ♿ UI/UX 巡检分析：`scripts/uiux_analyzer.py`，4 大检测维度（可访问性 / 交互质量 / 布局响应式 / UX 反模式）
- 🖼️ 视觉回归测试：`scripts/visual_regression.py`，像素级 Diff + 数据显示不全 + 显示错误检测
- 🛡️ 前端质量门禁：UI 设计师交付前自检、测试专家 E2E 断言、Solo Coder CI 门禁

**v2.3 新增（代码走读与可视化）**:
- 🚀 代码走读与审查：多角色协作分析，生成统一代码地图和审查报告
- 🗺️ 代码地图 Workspace 支持：支持一个 workspace 包含多个项目
- 📊 文档对齐引擎：多角色分析结果对齐，生成共识代码地图
- 📋 任务可视化页面：实时展现各角色任务状态、进度、依赖关系、交接过程
- 🎨 3D 代码地图可视化：基于 Three.js 的交互式代码结构可视化，支持流动动画和主题切换
- ✅ 文档与代码一致性检查：审查报告中新增文档与代码差异检查清单
## Karpathy 四大核心原则（行为准则）
> **来源**: Andrej Karpathy 对 LLM 编程常见陷阱的观察
> **目的**: 减少 LLM 编程中的错误、过度复杂、无关修改等问题
所有角色必须遵守以下四大原则，详见各角色介绍中的「Karpathy 原则应用」表格。
| 原则 | 核心要求 | 禁止行为 |
|------|---------|---------|
| 🧠 Think Before Coding | 明确假设、问清楚、不隐藏困惑 | ❌ 假设用户意图、默默选择方案 |
| 🎯 Simplicity First | 最小代码、无 speculative features | ❌ 过度抽象、预留未来代码 |
| 🔬 Surgical Changes | 只改必要的、保持风格一致 | ❌ 溢出修改、顺手改无关代码 |
| ✅ Goal-Driven | 定义成功标准、验证检查点 | ❌ 不知道何时完成、跳过验证 |
**速查**:
- 需求不明确 → 停下来问清楚
- 考虑添加抽象 → 问"真的需要吗？"
- 修改代码 → 只改必要的行
- 开始实现 → 定义成功标准
详细说明和示例见 `docs/guides/KARPATHY_PRINCIPLES.md`。
## 多语言支持 (Multi-Language Support)
### 语言识别规则
**自动识别用户语言**:
- 用户使用中文 → 所有响应使用中文
- 用户使用英文 → 所有响应使用英文
- 用户混合使用 → 以首次使用的语言为准
- 用户明确要求切换 → 立即切换到目标语言
### 响应语言规则
**所有输出必须使用用户相同的语言**:
- 角色定义和 Prompt
- 状态更新和进度提示
- 审查报告和问题清单
- 错误信息和成功提示
- 文档和注释
**示例**:
```
用户（中文）: "设计系统架构"
AI（中文）: "📋 已接收任务，开始分析..."
用户（English）: "Design system architecture"
AI (English): "📋 Task received, starting analysis..."
```
### 角色名称映射
**中文 → 英文**:
- 架构师 → Architect
- 产品经理 → Product Manager
- 测试专家 → Test Expert
- 独立开发者 → Solo Coder
- UI 设计师 → UI Designer
## 核心能力
### AI 增强能力 (v2.1 新增)
1. **AI 语义理解驱动的角色匹配**: 使用大模型理解任务的深层语义，而非简单关键词匹配
2. **可解释的智能决策**: 提供匹配原因和置信度评分，决策过程透明可解释
3. **上下文感知的智能推理**: 基于历史经验和领域知识进行智能推理
4. **自然语言交互界面**: 支持自然语言对话，理解用户意图
### 基础能力
1. **智能角色调度**: 根据任务描述自动识别需要的角色
2. **多角色协同**: 组织多个角色共同完成复杂任务
3. **上下文感知**: 根据项目阶段和历史上下文选择角色
4. **共识机制**: 组织多角色评审和决策
5. **自动继续**: 思考次数超限后自动保存进度并继续执行
6. **任务管理**: 完整的任务生命周期管理和进度追踪
7. **代码地图生成**: 自动生成项目代码结构映射
8. **项目理解**: 快速读取项目文档和代码，生成项目理解文档
9. **规范驱动开发**: 基于项目规范和文档进行开发
10. **八阶段标准工作流程**: 需求分析→架构设计→UI设计→测试设计→任务分解→开发实现→测试验证→文档对照代码审查
11. **UI 设计**: 创建独特、生产级的 UI 界面，避免通用的 AI "slop" 美学
12. **既有系统理解（黑盒 Web 系统）**: Playwright 登录 + BFS 页面遍历 + DB/Redis 只读内省 + 三角关联 + 10 节《系统功能理解文档》

## 核心入口与门面（v2.8.3 确认）

> **架构收敛**：facade.py 是统一调度门面，所有外部调用入口最终都汇聚到 facade。

### 入口层次（从外到内）

| 入口 | 状态 | 用途 |
|------|------|------|
| `scripts/trae_agent_dispatch.py` | ⚠️ v2.8.3 起弃用（DeprecationWarning） | v1 兼容入口，仅向后保留 |
| `scripts/trae_agent_dispatch_v2.py` | ✅ 推荐 CLI 入口 | V3 薄壳（< 50 行），委托给 facade.main_compat |
| `scripts/facade.py` | ✅ 统一门面（Python 入口） | re-export 11 个旧符号 + main_compat + hot reload watcher |
| `scripts/dispatch/legacy.py` | 内部实现层 | 5 个 dispatch 函数 + 助手（god module 搬迁产物） |
| `scripts/dispatcher/goal_dispatcher.py` | 内部 V3 dispatcher | 插件化调度核心 |

### Python 集成推荐

```python
# 推荐：直接从 facade 导入
from facade import main_compat, dispatch_agent_v2, log

# 或从薄壳导入（同样可用，会 re-export 自 facade）
from trae_agent_dispatch_v2 import main_compat, dispatch_agent_v2
```

### 绝对禁止

❌ 业务代码直接 import `dispatch.legacy`（内部实现层，可能被重构）
❌ 业务代码直接 import `dispatcher.goal_dispatcher`（内部实现层）
❌ 新代码使用 `trae_agent_dispatch.py`（v1 已弃用）

## CLI 指令清单（v2.12 整理 · 与 scripts/cli/parser.py 逐项核对）

> 统一入口：`python3 scripts/trae_agent_dispatch_v2.py <flags>`
> 互斥总则（与 facade.py `_dispatch_through_v3` 前置校验逐项核对）：
> 1. 三大专属模式族互斥：proposal-review（v2.10）/ business-flow-analysis（v2.11）/ browser-test（v2.12）任意组合互斥
> 2. 三族各自与 `--autonomous` / `--loop > 1` / `--multi-goal` 互斥
> 3. 族内互斥：proposal 三模式、business-flow 两模式、browser-test 三模式各只能选一个
> 4. `--hot-reload` 与 `--no-hot-reload` argparse 级互斥

### 1. 基础调度

| Flag | 取值/默认 | 说明 |
|------|----------|------|
| `--task` | 字符串（非必填） | 任务描述 |
| `--agent` | auto（默认）/ architect / product-manager / test-expert / solo-coder / ui-designer | 指定角色 |
| `--project-root` | `.` | 项目根目录 |
| `--task-file` | 路径 | 从文件读取任务 |
| `--output` | 无 | 输出文件路径 |
| `--verbose` | - | 详细输出 |
| `--dry-run` | - | 仅模拟，不实际调度 |
| `--use-v1` | - | 使用 v1 逻辑（弃用兼容） |
| `--consensus` | false | 多角色共识汇总（true/false） |
| `--explain` | false | 输出 AI 匹配解释（true/false） |
| `--match-strategy` | auto（默认）/ exact / fuzzy / semantic | 角色匹配策略 |

### 2. 项目与模式开关

| Flag | 说明 |
|------|------|
| `--project-full-lifecycle` | 八阶段完整项目流程（需求→架构→UI→测试→任务→开发→测试→文档对照审查） |
| `--loop-engineering` | Loop Engineering 五步闭环模式 |
| `--loop-type` | design / coding（默认）/ testing |
| `--loop-discovery` | auto（默认）/ manual / off |
| `--loop-evaluator` | strict（默认）/ standard / off |
| `--loop-human-checkpoint-every` | 人类检查点间隔轮数（0=关闭；默认 5） |
| `--loop-max-iterations` | 最大迭代（默认 50） |
| `--loop-max-tokens` | Token 预算（0=不限；默认 0） |
| `--loop-sampling-read-ratio` | 抽样阅读比例（默认 0.1） |
| `--loop-stop-when` | 自然语言停止条件 |

### 3. Goal 编排（Phase 11-15）

| Flag | 说明 |
|------|------|
| `--loop` | 循环次数（默认 1=不循环；>1 与特殊模式互斥） |
| `--goal` / `--goal-desc` | 目标 ID（kebab-case）/ 描述 |
| `--criteria` | 验收标准（可多次传入） |
| `--convergence-window` | 收敛窗口（默认 3） |
| `--multi-goal` | 以 root Goal 入口执行 DAG 编排 |
| `--goal-parent` / `--goal-depends` | Goal 树父节点 / 依赖（可多次） |
| `--goal-aggregation` | AND（默认）/ OR / MAJORITY |
| `--goal-resume` / `--goal-resume-force` / `--goal-max-resume-count` | 续跑 / 强制续跑 / 续跑上限（默认 3） |
| `--reuse-threshold` / `--disable-iteration-reuse` | 跨 Goal 复用阈值（默认 0.85）/ 禁用复用 |
| `--max-concurrent` | DAG 并发 worker（默认 10） |
| `--goal-report` | json / md 报告 |
| `--goal-cancel` | 取消 root Goal 及子 Goal |
| `--goal-graph` / `--goal-graph-format` / `--goal-graph-output` / `--goal-graph-desc-max` | DAG 可视化（mermaid 默认 / json / dot） |

### 4. Autonomous 自主模式（Phase 18）

| Flag | 说明 |
|------|------|
| `--autonomous` | Ralph 风格自主迭代（与特殊模式互斥） |
| `--auto-max-iterations` / `--auto-max-tokens` | 迭代上限（默认 50）/ token 预算（默认 0=不限） |
| `--auto-stop-when` | 自然语言停止条件 |
| `--auto-test-command` | 测试命令（默认 unittest discover） |
| `--auto-stage-order` | 阶段顺序 CSV（默认 plan,dev,verify,fix） |
| `--auto-backoff-base` / `--auto-backoff-max` / `--auto-failure-abort` | 退避 1.0s / 上限 60.0s / 连续失败阈值 3 |
| `--auto-resume` / `--auto-resume-latest` | 续跑指定 run / 最新可续 run |
| `--auto-no-caffeinate` / `--auto-no-commit` | CI 禁用抑制 / 禁用自动提交 |
| `--auto-confirm-mode` | smart（默认）/ whitelist-only / blacklist-only |
| `--auto-run-dir` | run 状态目录（默认 .gnhf/runs） |
| `--auto-git-author-name` / `--auto-git-author-email` | 自动提交作者信息 |
| `--auto-security-analyzer` | builtin（默认）/ bandit / semgrep |
| `--auto-notes-path` / `--auto-max-size-kb` / `--auto-trim-keep-last-n` | notes.md / 1024KB / 保留 20 段 |

### 5. 插件热加载（Phase 17）

| Flag | 说明 |
|------|------|
| `--hot-reload` / `--no-hot-reload` | 启用（默认）/ 禁用（二者互斥） |
| `--hot-reload-dir` | drop-in 目录（相对路径、禁 `..`；默认 plugins_extra） |
| `--hot-reload-interval` | 轮询秒数（默认 5.0，范围 [0.5, 60]） |

### 6. 方案撰写全流程（v2.10）

| Flag | 说明 |
|------|------|
| `--proposal-review` | 完整流程：调研→撰写→四角色评审→共识收敛→修改 |
| `--industry-research-only` | 仅业界调研（Phase 0） |
| `--proposal-review-only` + `--proposal-doc` | 仅评审已有方案文档 |
| `--proposal-max-iterations` | 评审闭环上限（默认 3，范围 [1,10]） |
| `--proposal-consensus-threshold` | 共识判定阈值（默认 2） |

### 7. 业务动线分析与页面设计闭环（v2.11）

| Flag | 说明 |
|------|------|
| `--business-flow-analysis` | 完整六阶段：SU→断点/漏洞分析→GAP 设计→评审→开发→UI/UX 测试 |
| `--skip-design` | 仅分析，跳设计/评审/开发/测试 |
| `--business-flow-design-only` + `--analysis-report` | 跳过分析，对已有报告做 GAP 设计 |
| `--skip-su` + `--su-json-path` | 跳过 SU 采集，直接引用已有 understanding.json |

### 8. 浏览器端测试闭环（v2.12）

| Flag | 说明 |
|------|------|
| `--browser-test` | 完整四阶段：规划→四角色评审→三宿主执行→汇总门禁 |
| `--browser-test-plan-only` | 仅规划（产出四件套） |
| `--browser-test-exec-only` | 仅执行已有计划（必填 `--test-plan-dir` + `--target-url`） |
| `--test-plan-dir` | 计划四件套目录 |
| `--target-url` | 被测系统 URL（执行阶段必填） |
| `--browser-hosts` | agent-browser / trae-internal / chrome-devtools / auto（默认 auto 降级） |
| `--reuse-flow-analysis` | 复用 v2.11 动线报告做增量校验 |

### 9. 记录器子命令（v2.12 · `scripts/browser_test_recorder.py --out <dir> <子命令>`）

| 子命令 | 关键参数 | 说明 |
|--------|---------|------|
| `run` | `--run-id --url --hosts --env --note --plan-ref` | 初始化运行 |
| `case` | `--case-id --action start/end [--title --priority --host --flows --datasets --status]` | 用例开始/收口（priority: P0/P1/P2） |
| `step` | `--case-id --action-name --target [--dataset --duration-ms --status --path --note]` | 步骤记录（动作：navigate/click/fill/submit/upload/select/keypress/wait/screenshot/evaluate） |
| `assert` | `--case-id [--seq] --kind --expected --actual [--pass]` | 断言（kind：url/dom/network/console/text/state） |
| `error` | `--case-id --source --message [--detail --url --suspected-bug]` | 错误记录（source：console/network/bug） |
| `shot` | `--case-id --path [--seq --label]` | 截图登记（文件必须真实存在） |
| `blocker` | `--case-id --blocker-type --description [--resolved --resolution]` | 阻碍记录（selector_fix/data_prep/env_failure/host_downgrade/case_fix/other） |
| `verify` | `--run-id [--case-id \| --all]` | schema 校验 |
| `finalize` | `--run-id` | 一致性核对 + summary.json/md + P0 门禁 |

### 典型组合示例

```bash
# 完整业务动线闭环（含 SU 采集）
python3 scripts/trae_agent_dispatch_v2.py --task "梳理订单业务动线" --business-flow-analysis

# 已有 SU 产物，仅做断点分析
python3 scripts/trae_agent_dispatch_v2.py --task "分析页面断点" \
    --business-flow-analysis --skip-design --skip-su \
    --su-json-path project-understanding/understanding.json

# 浏览器测试完整闭环（自动宿主降级）
python3 scripts/trae_agent_dispatch_v2.py --task "测试登录与下单" \
    --browser-test --target-url http://localhost:3000 --reuse-flow-analysis business-flow/analysis.md

# 仅执行已评审的测试计划（指定 Chrome DevTools MCP）
python3 scripts/trae_agent_dispatch_v2.py --task "执行测试计划" \
    --browser-test-exec-only --test-plan-dir browser-test/plan \
    --target-url http://localhost:3000 --browser-hosts chrome-devtools
```

## Trae 宿主 LLM SubAgent 调度协议（v2.8.4 新增）

> **问题**：Trae 环境下无 `claude` 命令行工具，`ClaudeCodeSubAgentAdapter` 的 `_fallback_no_subagent` 返回 `success=False`，导致 autonomous 循环无限重试。
>
> **解决方案**：通过文件协议（`HostLLMBridge`）桥接宿主 LLM 的 Task 工具，脚本生成调度请求 → 宿主 LLM 通过 Task 执行子代理 → 结果回传。

### 工作流程

```
宿主 LLM (Trae 对话)
    │
    ├─1─> RunCommand(blocking=false): python3 -u trae_agent_dispatch_v2.py --autonomous ...
    │
    │     [脚本内部]
    │     ├─> _invoke_via_host_llm → HostLLMBridge.create_request()
    │     │   → 写入 request_{id}.json + protocol.marker
    │     ├─> HostLLMBridge.wait_for_response(id, timeout=600)
    │     │   → 轮询等待 response_{id}.json
    │     │
    │     │     [宿主 LLM — 并行执行]
    │     │     ├─2─> 每 5 秒读取 protocol.marker 文件
    │     │     ├─3─> 读取 request_{id}.json 获取完整提示词
    │     │     ├─4─> Task(subagent_type, query=task) → 执行子代理
    │     │     ├─5─> HostLLMBridge.write_response(id, result) → 原子写入
    │     │     │
    │     │     [脚本继续]
    │     │     └─6─> 读取 response_{id}.json → 返回结果
    │     └─7─> autonomous 循环继续
    │
    └─8─> CheckCommandStatus → 脚本完成
```

### 宿主 LLM 轮询契约

当通过 `RunCommand(blocking=false)` 运行 autonomous 脚本后，宿主 LLM **必须**遵循以下轮询契约：

1. **启动后 2 秒**首次检查：读取 `logs/host_llm_bridge/protocol.marker` 文件
2. **之后每 5 秒**检查一次 `protocol.marker` 文件
3. **检查到 marker** 后：
   a. 读取 marker JSON 获取 `request_id` 和 `request_file`
   b. 读取 `request_file` 获取完整提示词
   c. 通过 Task 工具执行子代理（subagent_type 根据角色选择）
   d. 调用 `HostLLMBridge.write_response()` 写入结果
   e. 调用 `HostLLMBridge.clear_marker()` 清除标记
4. **marker 不存在**时：继续等待（脚本可能还在执行前序步骤）
5. **脚本结束**时：停止轮询

**重要**：protocol.marker 是独立文件，**不依赖 CheckCommandStatus 的 stdout**。使用 `Read` 工具直接读取文件。

### Task 工具 subagent_type 映射

| skill 角色名 | Task subagent_type | 说明 |
|-------------|-------------------|------|
| architect | search | 架构分析需要代码搜索 |
| product-manager | general_purpose_task | 产品分析 |
| test-expert | general_purpose_task | 测试设计 |
| solo-coder | general_purpose_task | 代码实现 |
| ui-designer | general_purpose_task | UI 设计 |

### 写入结果（强制原子写入）

宿主 LLM **必须**使用 `HostLLMBridge.write_response()` 写入结果，**禁止**直接 `echo > response.json`：

```bash
# 通过命令行调用（推荐）
python3 scripts/host_llm_bridge.py write-response <request_id> \
    --success true --output "结果内容" --bridge-dir logs/host_llm_bridge
```

### 熔断机制（连续失败保护）

当连续 2 次迭代均为 `retriable` 且原因包含超时/dispatch 失败关键词时，`loop_controller` 自动终止循环（升级为 fatal），避免无限重试浪费资源。

### 平台检测优先级

| 环境变量 | 平台 | 调度方式 |
|---------|------|---------|
| `TRAE_ENV` 或 `TRAE_AGENT_PATH` | `host_llm` | 文件协议桥接宿主 LLM Task 工具 |
| `CLAUDE_CODE_ENV` 或 `ANTHROPIC_ENV` | `claude_code` | claude CLI subprocess |
| 无 | `unknown` | `_fallback_no_subagent`（诚实降级） |

### 设计文档

详细设计见 `docs/dev/HOST_LLM_BRIDGE_DESIGN.md`。

## 快速开始
### 基础使用
```bash
# 自动调度（推荐）
python3 scripts/trae_agent_dispatch_v2.py \
    --task "设计系统架构"
# 指定角色
python3 scripts/trae_agent_dispatch_v2.py \
    --task "实现功能" \
    --agent solo_coder
# 多角色共识
python3 scripts/trae_agent_dispatch_v2.py \
    --task "启动新项目" \
    --consensus true
```
### 完整项目流程
```bash
# 启动完整项目（自动执行 7 个阶段）
python3 scripts/trae_agent_dispatch_v2.py \
    --task "启动项目：安全浏览器广告拦截功能" \
    --project-full-lifecycle
```
### AI 增强模式 (v2.1 新增)
```bash
# 使用 AI 语义匹配（默认）
python3 scripts/trae_agent_dispatch_v2.py \
    --task "设计微服务架构，支持高并发和弹性扩展" \
    --agent auto  # AI 会自动匹配最合适的角色
# 查看 AI 匹配结果和解释
python3 scripts/trae_agent_dispatch_v2.py \
    --task "实现用户认证和权限管理" \
    --agent auto \
    --explain  # 显示 AI 匹配原因和置信度
# 使用传统关键词匹配（向后兼容）
python3 scripts/trae_agent_dispatch_v2.py \
    --task "编写单元测试" \
    --agent test-expert \
    --match-strategy keyword
```
## AI 集成说明 (v2.1)

> **宿主 LLM 调度指令（提示词层，Trae/Claude 主 Agent 必读）**：
> 以下匹配决策与拓扑路由由你（宿主大模型）直接执行，脚本层仅提供确定性工具。
> 不要调用 `ai_semantic_matcher.py` 的模拟路径——它已在 v2.7.0 改造为诚实降级。

### 任务规模分级门禁（v2.8.2 — 第一道路由）

> **Karpathy Simplicity First 的前置执行**：在角色匹配之前，先判断任务规模，避免杀鸡用牛刀。

接到任务后，**首先**按以下信号评估任务规模，选择执行路径：

| 档位 | 判定信号 | 执行路径 | 禁止行为 |
|------|---------|---------|---------|
| **S（分钟级）** | 单文件修改、问答、小修复、单函数重构 | 直接派单角色执行，完成后验证 | ❌ 禁止启动工作流/循环/共识/代码地图 |
| **M（小时级）** | 单功能开发、Bug 修复+验证、模块重构 | 三阶段迷你流：设计要点 → 开发 → 测试验证 | ❌ 禁止启动八阶段流程/autonomous 模式 |
| **L（天级）** | 新项目、跨模块改造、架构迁移、用户明确要求完整流程 | 完整八阶段 Loop（WorkflowLoopController） | — |

**判定信号清单**（按顺序检查，停在第一个满足的）：
1. 用户明确要求完整流程 / 启动项目 → **L**
2. 涉及 ≥ 3 个模块或 ≥ 5 个文件 → **L**
3. 涉及 2 个模块或 3-4 个文件 → **M**
4. 涉及 1 个文件或纯问答 → **S**
5. 不确定 → 默认 **M**（宁可多验证，不可遗漏）

**与拓扑路由的关系**：S/M/L 分级是第一道路由（决定流程规模），角色匹配是第二道路由（决定谁来执行），拓扑路由是第三道路由（决定执行模式）。

### 角色智能匹配规则（宿主 LLM 执行）

接到任务后，按以下多维度评分选择角色（无需调用脚本）：

| 维度 | 权重 | 评估要点 |
|------|------|---------|
| 能力匹配 | 50% | 任务核心需求与角色职责的重合度（见下方角色触发关键词） |
| 技能匹配 | 30% | 任务所需技术栈与角色技能列表的重合度 |
| 语义相关 | 20% | 任务描述与角色描述的深层语义关联（同义、上下位、因果） |

**决策规则**：
1. 单角色置信度 ≥ 0.7 → 直接调度该角色
2. 多角色均 ≥ 0.6 且任务可分解 → 多角色协作（见拓扑路由）
3. 所有角色 < 0.5 → 向用户澄清需求，不要强行匹配
4. 任务跨多个领域（如"设计+实现+测试"）→ 按八阶段流程依次调度

### 动态工作流拓扑路由决策表（宿主 LLM 执行）

根据任务 DAG 特征选择执行模式（对齐 2026 拓扑自适应编排结论）：

| 任务特征 | 路由模式 | 执行方式 |
|---------|---------|---------|
| 可分解为独立子任务（并行宽 ≥ 2，无相互依赖） | fan-out-aggregate | 并行派发 → 聚合结果 |
| 需要质量验证/对抗审查（如代码审查、安全审计） | adversarial-verify | 生成 → 审查 → 修复循环 |
| 需要多方案竞争（如架构选型、文案创作） | tournament | 多方案并行 → 最优胜出 |
| 需批量生成后筛选（如测试用例、候选实现） | generate-filter | 批量生成 → 质量过滤 |
| 任务类型明确且单一（如纯测试、纯文档） | classifier-dispatch | 分类 → 单角色执行 |
| 成功标准可量化但未达成（如性能优化、缺陷修复） | loop-until-done | 迭代直到满足标准 |
| **强顺序依赖、链式推理**（如调试根因分析、数学推导、分步重构） | **单角色链** | ⚠️ **禁用多 Agent 并行**——实证显示此类任务多 Agent 反降 39–70% 性能，由单一角色顺序完成 |

**降级红线**：当任务本质是顺序推理（步骤 N 依赖步骤 N-1 的结果）时，禁止为了"多角色"而强行并行，应退化为单角色链式执行。

### AI 能力（脚本层工具）

- **语义理解**: 宿主 LLM 提示词层完成；脚本层提供 TFIDF/Hashing 本地相似度（降级用）
- **智能匹配**: 宿主 LLM 按上表执行；脚本 `role_matcher.py` 提供关键词+embedder 降级
- **匹配策略**: `ai_enhanced`（宿主提示词层）、`semantic`（脚本 embedder）、`keyword`（脚本关键词）
- **代码审查**: 宿主 LLM 执行；脚本层提供代码地图、guard 校验等确定性输入
- **知识问答**: 宿主 LLM 直接回答

### AI 配置
```yaml
ai_integration:
  enabled: true
  provider: host_llm_prompt_layer   # v2.7.0 修正：AI 在宿主提示词层
  features:
    - semantic_matching      # 宿主 LLM
    - intelligent_reasoning  # 宿主 LLM
    - context_understanding  # 宿主 LLM
  script_layer_tools:        # 脚本层确定性工具（非 AI）
    - tfidf_similarity
    - hashing_similarity
    - keyword_matching
    - code_map_scan
    - uiux_audit
    - visual_regression
  config:
    max_tokens: null          # null = 不限制（默认，让模型按自身最大输出能力生成）；正整数 = 显式上限（如 4096）
    temperature: 0.7
    top_p: 0.9
    use_cache: true
    fallback_to_keyword: true  # 脚本层降级链：embedder → 关键词
```
### 性能优化
- **缓存机制**: 相同请求直接返回缓存结果
- **降级策略**: 脚本层 embedder 不可用时自动降级到关键词匹配；AI 语义理解始终由宿主 LLM 提供，无降级
- **批量处理**: 支持批量请求，减少 API 调用次数
## 角色介绍
### 通用行为准则（所有角色必须遵守）
**Karpathy 四大核心原则**:
| 原则 | 核心要求 | 禁止行为 |
|------|---------|---------|
| 🧠 Think Before Coding | 明确假设、问清楚、不隐藏困惑 | ❌ 假设用户意图、默默选择方案 |
| 🎯 Simplicity First | 最小代码、无 speculative features | ❌ 过度抽象、预留未来代码 |
| 🔬 Surgical Changes | 只改必要的、保持风格一致 | ❌ 溢出修改、顺手改无关代码 |
| ✅ Goal-Driven | 定义成功标准、验证检查点 | ❌ 不知道何时完成、跳过验证 |
### 1. 架构师 (Architect)
**职责**: 设计系统性、前瞻性、可落地、可验证的架构
**触发关键词**: 架构、设计、选型、审查、性能、瓶颈、模块、接口、部署
**详细 prompt**: `docs/roles/architect/ARCHITECTURE_DESIGN_TEMPLATE.md`

### 2. 产品经理 (Product Manager)
**职责**: 定义用户价值清晰、需求明确、可落地、可验收的产品
**触发关键词**: 需求、产品、PRD、用户故事、验收标准、竞品分析
**详细 prompt**: `docs/roles/product-manager/PRD_TEMPLATE.md`

### 3. 测试专家 (Test Expert)
**职责**: 确保全面、深入、自动化、可量化的质量保障
**触发关键词**: 测试、质量、验收、自动化、性能测试、缺陷、评审、门禁
**详细 prompt**: `docs/roles/test-expert/TEST_PLAN_TEMPLATE.md`

### 4. 独立开发者 (Solo Coder)
**职责**: 编写完整、高质量、可维护、可测试的代码
**触发关键词**: 实现、开发、代码、修复、优化、重构、单元测试、文档
**详细 prompt**: `docs/roles/solo-coder/DEVELOPMENT_TEMPLATE.md`

### 5. UI 设计师 (UI Designer)
**职责**: 创建独特、生产级的 UI 界面，具有高设计质量，避免通用的 AI "slop" 美学
**触发关键词**: UI设计、界面设计、前端设计、视觉设计、UI/UX、UI原型、界面美化、UI优化、UI重构
**详细 prompt**: `docs/roles/ui-designer/UI_DESIGNER_PROMPT.md`

## 八阶段标准工作流程（v2.8 新增阶段 8：文档对照代码审查）

```
阶段 1: 需求分析（产品经理）
    ↓ 评审通过
阶段 2: 架构设计（架构师）
    ↓ 评审通过
阶段 3: UI 设计（UI 设计师）
    ↓ 评审通过
阶段 4: 测试设计（测试专家）
    ↓ 评审通过
阶段 5: 任务分解（独立开发者）
    ↓
阶段 6: 开发实现（独立开发者）
    ↓
阶段 7: 测试验证（测试专家）
    ↓
阶段 8: 文档对照代码审查（多角色）★ v2.8 新增
    ↓ 审查通过
发布
```

### 阶段 8: 文档对照代码审查（v2.8 新增）

开发完成后，对照设计文档逐项检查功能完成情况、集成情况、测试正确性，确保文档设计完整落地。

**六大检查维度**：

| 维度 | 检查内容 | 通过条件 |
|------|----------|----------|
| D1 功能完成度 | 文档中每个功能点是否有对应代码实现 | 实现率 = 100% |
| D2 集成完整性 | 文档定义的模块间集成关系是否在代码中体现 | 集成率 = 100% |
| D3 测试正确性 | 全部测试通过，无失败；测试覆盖文档功能 | failed = 0, passed > 0 |
| D4 验收标准满足 | 文档中每条验收标准是否被代码满足 | 满足率 = 100% |
| D5 TODO/FIXME 清零 | 代码中无残留的 TODO/FIXME 注释 | 未实现 = 0 |
| D6 文档意图遵从 | 代码实现未偏离文档设计意图 | 偏离数 = 0 |

**审查判定**：
- 全部通过 → 审查通过，可发布
- 任一不通过 → 审查不通过，列出缺口清单，回退到阶段 6 修复

### 八阶段整体构建为一个 Loop（v2.8.1 新增）

八阶段标准工作流整体构建为一个完整的循环（`WorkflowLoopController`），支持审查失败后回退到对应阶段修复，避免一次性失败导致整个流程作废。

**核心机制**：
- **审查驱动**：阶段 8 审查结果是循环的核心驱动：通过则结束，不通过则回退
- **精准回退**：根据缺口维度（D1-D6）决定回退到哪个阶段

| 缺口维度 | 回退到阶段 | 理由 |
|----------|-----------|------|
| D1 功能完成度 | 阶段 6（开发） | 功能缺失需补开发 |
| D2 集成完整性 | 阶段 6（开发） | 集成缺失需补开发 |
| D3 测试正确性 | 阶段 7（测试验证） | 测试失败需修复测试 |
| D4 验收标准 | 阶段 6（开发） | 验收未满足需补开发 |
| D5 TODO/FIXME | 阶段 6（开发） | TODO 未实现需补开发 |
| D6 文档意图 | 阶段 6（开发） | 文档偏离需调整代码 |

- **迭代上限**：最大迭代次数限制（默认 3 次，可配置），防止无限循环
- **上下文累计**：跨迭代保留产出（`_accumulated_artifacts`），后续阶段可访问前序产出
- **真实执行**：所有阶段真实执行，禁 mock/占位/简化

**核心组件**：
- 检查器：`scripts/doc_code_consistency_checker.py`（DocCodeConsistencyChecker）
- Handler：`scripts/autonomous/handlers/review_handler.py`（ReviewHandler）
- **循环控制器**：`scripts/workflow_loop_controller.py`（WorkflowLoopController + RollbackStrategy）
- **CLI 入口**：`scripts/run_workflow_loop.py`（命令行入口脚本）
- Prompt 模板：`docs/spec/role-prompts/doc-code-review.md`
- 报告模板：`docs/roles/doc-code-review/DOC_CODE_REVIEW_TEMPLATE.md`
- 设计文档：`docs/dev/DOC_CODE_REVIEW_STAGE.md`（§10 八阶段循环章节）

**CLI 使用示例**：

```bash
# 基本用法
python3 scripts/run_workflow_loop.py \
  --project-root /path/to/project \
  --prd-path docs/prd.md \
  --architecture-path docs/architecture.md \
  --test-command "python3 -m pytest -v"

# 自定义最大迭代次数
python3 scripts/run_workflow_loop.py \
  --project-root /path/to/project \
  --max-iterations 5 \
  --verbose
```

**与 RalphLoopController 的关系**：
- 外层使用 `WorkflowLoopController` 编排八阶段
- 内层阶段 6（开发）可注入 `RalphLoopController` 做 plan→dev→verify→fix 小循环
- `WorkflowStage.to_stage_kind()` 提供两者之间的映射（详见设计文档 §10.3.2）

**绝对禁止**：
❌ 未经过设计阶段直接开始编码
❌ 文档未编写或未完成就开始开发
❌ 未经过设计评审直接实施
❌ 使用通用的 AI 美学（AI slop）
❌ 开发完成后不对照文档检查就发布（v2.8 新增）
❌ 代码中残留 TODO/FIXME 未实现就发布（v2.8 新增）

## 方案撰写全流程（v2.10 新增 — 业界调研 → 多角色交叉评审闭环）

> **设计理念**: 好的方案需要先看清业界怎么做（调研）、结合自己项目的实际情况（代码扫描）、再被不同角度的人反复挑刺（多角色交叉评审）、最终收敛到共识方案。这个五阶段闭环确保方案不是"想当然"拍出来的，而是经过真实验证和多维度审视的。
>
> **v2.10 核心机制**: 复用现有 adversarial-verify 动态工作流模式 + WorkflowLoopController 循环机制做「评审→修改→再评审」闭环；业界调研由宿主 LLM 配合浏览器插件（WebSearch + browser_use）真实执行；所有评审 prompt 强制要求**结合项目最新代码**进行评审。

### 五阶段工作流

```
Phase 0: 业界调研（浏览器搜索 + 项目代码扫描）
    ↓ 产出调研报告
Phase 1: 方案撰写（结合调研结论 + 项目最新代码）
    ↓ 产出初版方案文档
Phase 2: 多角色交叉评审（4 角色并行评审）
    ├─ 架构师视角评审
    ├─ 产品经理视角评审
    ├─ 测试专家视角评审
    └─ 独立开发者视角评审
    ↓ 产出 4 份独立评审意见
Phase 3: 共识收敛（提取共识点 + 分歧点）
    ↓ 产出共识结论
Phase 4: 根据共识修改方案
    ↓ 修改后回到 Phase 2 再评审
    ↑─────────── 循环直到共识达成 ───────────┘
```

### Phase 0：业界调研（真实执行，禁止凭空生成）

**执行方式**: 宿主 LLM 作为调研专家，配合浏览器插件执行真实搜索

**详细步骤**:

```
Step 1 — 关键词提取（从任务描述中提取核心技术关键词）
    ↓
Step 2 — 多路径并行搜索（WebSearch 查询 + 浏览器深度阅读）
    - 中文查询："{topic} 最佳实践" / "{topic} 架构设计" / "{topic} 生产环境"
    - 英文查询："{topic} best practices 2026" / "{topic} at scale"
    - 使用 site:github.com / site:techblog.google 限定权威来源
    ↓
Step 3 — 信息交叉验证（至少 2-3 个独立来源确认同一结论）
    ↓
Step 4 — 结构化整理（主流方案对比矩阵 + 正反意见 + 项目适配分析）
    ↓
Step 5 — 产出调研报告（docs/proposal/{task-id}/industry-research.md）
```

**强制约束**:
- 所有结论必须有**真实来源支撑**（URL 可追溯）
- 优先 **近 2 年内容**（2024-2026）
- 必须包含**与当前项目的对比分析**（现有技术栈、约束条件、适配路径）
- 必须给出**推荐决策**（推荐方案 + 备选方案 + 避坑清单）

**Prompt 模板**: `docs/spec/role-prompts/industry-research-prompt.md`

### Phase 1：方案撰写（结合调研 + 项目代码）

**执行方式**: 宿主 LLM 结合 Phase 0 调研报告和项目最新代码撰写方案

**强制约束**:
- 必须**扫描项目最新代码**（文件列表、模块结构、依赖关系、现有实现）
- 必须**引用业界调研结论**（说明方案与业界最佳实践的关系）
- 必须**定义清晰的验收标准**（可量化、可验证）
- 必须**标注与现有代码的集成路径**（哪些文件需要修改、哪些需要新增）

### Phase 2：多角色交叉评审（4 角色并行）

**执行方式**: 宿主 LLM 以 4 种不同角色的视角分别评审方案，**必须结合项目最新代码**

| 评审角色 | Prompt 模板 | 核心评审维度 |
|----------|-------------|-------------|
| 架构师 | [proposal-architect-review.md](../spec/role-prompts/proposal-architect-review.md) | 架构可行性、技术选型、可扩展性、安全性、代码集成路径 |
| 产品经理 | [proposal-pm-review.md](../spec/role-prompts/proposal-pm-review.md) | 用户价值、需求覆盖、商业可行性、产品集成路径、验收标准 |
| 测试专家 | [proposal-test-review.md](../spec/role-prompts/proposal-test-review.md) | 可测试性、测试策略、验收标准可验证性、质量风险 |
| 独立开发者 | [proposal-coder-review.md](../spec/role-prompts/proposal-coder-review.md) | 可实现性、代码复杂度、实现路径、工作量、Ponytail 合规 |

**每个评审必须包含**:
1. 总体评分（X/10）和评审结论（通过/有条件通过/不通过）
2. 各维度详细评分
3. 🔴 **阻断性问题清单**（必须修改才能通过）
4. 🟡 建议性改进
5. 🟢 亮点
6. 与**项目最新代码**结合的具体证据（文件路径、行号、现有实现）

### Phase 3：共识收敛

**执行方式**: 宿主 LLM 汇总 4 份评审意见，识别共识点和分歧点

**收敛规则**:

```
共识判定：
- 🔴 阻断性问题：≥ 2 个角色同时提出 → 共识问题（必须修改）
- 🟡 建议性问题：≥ 3 个角色同时提出 → 建议采纳
- 分歧点：不同角色有相反意见 → 标记为分歧，需进一步讨论

收敛输出：
- 共识问题清单（必须修改的问题，按优先级排序）
- 分歧点清单（不同意见，附各角色论据）
- 当前方案总体结论（共识达成？还有哪些差距？）
```

### Phase 4：根据共识修改 + 闭环判定

**执行方式**: 宿主 LLM 根据共识问题清单修改方案文档，然后回到 Phase 2 再评审

**闭环判定**:
- 若所有角色评审通过（无 🔴 阻断性问题）→ ✅ 共识达成，方案定稿
- 若仍有 🔴 阻断性问题 → 继续修改循环
- **最大循环次数**: 默认 3 次，防止无限循环
- 超过上限 → 输出当前方案 + 剩余问题清单，交由用户裁决

### CLI 使用方式

```bash
# 方式 1：完整方案撰写全流程
python3 scripts/trae_agent_dispatch_v2.py \
    --task "设计实时协作编辑架构方案" \
    --proposal-review

# 方式 2：仅执行业界调研（用于预研）
python3 scripts/trae_agent_dispatch_v2.py \
    --task "调研分布式任务队列业界最佳实践" \
    --industry-research-only

# 方式 3：仅执行多角色评审（已有方案文档）
python3 scripts/trae_agent_dispatch_v2.py \
    --task "评审 ./docs/proposals/real-time-edit.md" \
    --proposal-review-only \
    --proposal-doc ./docs/proposals/real-time-edit.md
```

### 产出物

| 阶段 | 产出物 | 位置 |
|------|--------|------|
| Phase 0 | 业界调研报告 | `docs/proposals/{task-id}/industry-research.md` |
| Phase 1 | 初版方案文档 | `docs/proposals/{task-id}/proposal-v1.md` |
| Phase 2 | 4 份评审意见 | `docs/proposals/{task-id}/reviews/{role}-review.md` |
| Phase 3 | 共识收敛报告 | `docs/proposals/{task-id}/consensus-report.md` |
| Phase 4 | 最终方案文档 | `docs/proposals/{task-id}/proposal-final.md` |

### 架构设计

此工作流本质是 **adversarial-verify** 动态工作流模式的一个专门化变体（生成→多角色审查→修复循环），通过 WorkflowLoopController 管理「评审→修改→再评审」闭环。

```
┌───────────────────────────────────────────────────────────────────┐
│                    ProposalReviewWorkflow                          │
│                                                                   │
│  Phase 0 ──> Phase 1 ──> Phase 2 ──> Phase 3 ──> Phase 4         │
│ (research) (proposal)   (review)   (consensus)  (modify)         │
│                              │                     │              │
│                              └─────────────────────┘              │
│                                       │                           │
│                               loop until consensus achieved       │
│                               max 3 iterations                     │
└───────────────────────────────────────────────────────────────────┘
```

**核心特性**:
- ✅ **真实调研**: 使用 WebSearch + 浏览器插件做真实业界调研，拒绝凭空生成
- ✅ **代码结合**: 每个评审模板强制要求扫描项目最新代码并结合评审
- ✅ **多角色交叉**: 4 个不同角色独立评审，避免单点视角偏见
- ✅ **共识机制**: 多角色共同提出的问题升级为"共识问题"，必须解决
- ✅ **闭环收敛**: 修改后再评审，直到所有角色通过或达到上限
- ✅ **Karpathy 对齐**: 每个阶段都遵循 Karpathy 四大原则
- ✅ **Ponytail 合规**: 独立开发者评审包含完整的 Ponytail 决策梯检查

**绝对禁止**:
❌ 跳过业界调研直接开始写方案（除非用户明确要求）
❌ 凭空捏造"业界最佳实践"（必须有真实来源）
❌ 评审时不结合项目代码（必须扫描最新代码）
❌ 跳过共识收敛直接修改（必须先达成共识）
❌ 无限循环不收敛（3 次迭代上限，超过则输出当前方案交由用户裁决）

## 业务动线分析与页面设计闭环（v2.11 新增 — SU 发现 → 断点检测 → 漏洞修补 → 设计 → 评审 → 开发 → 测试）

> **设计理念**: 一个运行中的 Web 系统，页面间的流转路径、数据衔接、逻辑守卫经常"长歪"——有页面没入口、有入口没出口、数据字段不匹配、权限守卫漏了。这个工作流从需求文档出发，结合项目代码和 SU 页面遍历产物，系统性地发现这些断点和漏洞，然后补设计、评审、开发、测试，形成完整闭环。
>
> **v2.11 架构**：复用三大既有能力 + 新建 2 个 prompt 模板
> - **SU（v2.9）**：BFS 页面遍历 + understanding.json → 页面全景盘点的权威数据源
> - **proposal-review（v2.10）**：4 角色交叉评审 + 共识收敛 → 设计方案的质量门禁
> - **UI/UX 测试（v2.7）**：uiux_analyzer + visual_regression → 修改后的视觉和交互验证
> - **新建 prompt**：business-flow-analysis.md（断点+漏洞检测方法论）、page-gap-design.md（缺失页面详细设计模板）

### 六阶段工作流

```
Phase 0: SU 页面发现（复用 v2.9，已有产物则跳过）
    ↓ 产出 understanding.json（含 navigation/pages）
Phase 1: 业务动线分析（需求对照 + 断点检测 + 漏洞扫描）
    ↓ 产出 business-flow-analysis-report.md
Phase 2: 缺失页面详细设计（GAP-DESIGN-001 ~ N）
    ↓ 产出 page-gap-design-doc.md
Phase 3: 多角色评审（复用 v2.10 proposal-review 闭环）
    ↓ 产出 consensus-report.md + 修改后的方案
Phase 4: 执行开发（独立开发者按设计实现）
    ↓ 产出代码改动 + 单元测试
Phase 5: UI/UX 视觉测试（复用 v2.7 工具）
    ↓ 产出 uiux_report.json + visual_diff_report.json
```

### Phase 0：SU 页面发现

**执行方式**: 复用 v2.9 SU 能力

```bash
# 如果已有 SU 产物 → 直接使用，跳过采集
# 如果没有 → 执行 SU 采集：
python3 scripts/system_understanding.py --config config.json --skip-llm-phase
```

**关键产物**：
- `understanding.json` → `ui.pages`（页面清单）+ `navigation`（导航结构）
- `SYSTEM_FUNCTION_DOC.md` → 业务域和功能章节（SFD 详说产物，如有）

### Phase 1：业务动线分析

**执行方式**: 宿主 LLM 作为业务动线分析师

**强制输入**（必须全部获取）：
1. 需求文档（PRD/SPEC）或从 README/docs/issue tracker 中提取的业务描述
2. SU 产物中的页面清单（understanding.json）
3. 项目最新代码（前端路由/后端 API/DB 模型）

**分析方法论**（5 个 Step，详见 [business-flow-analysis.md](../spec/role-prompts/business-flow-analysis.md)）：

```
Step 1 — 页面全景盘点（从 SU + 代码扫描 → 完整页面清单 + 业务域分组）
Step 2 — 流转路径梳理（入口 → 操作 → API → DB → 出口，标注数据传递方式）
Step 3 — 逻辑断点检测（5 类断点）
    BREAK-1: 入口断裂（页面存在但无可达入口）
    BREAK-2: 出口断裂（操作跳转目标不存在/不通）
    BREAK-3: 前置条件缺失（权限/数据/状态守卫遗漏）
    BREAK-4: 数据衔接断裂（API 字段 ≠ 前端消费字段）
    BREAK-5: 业务闭环断裂（需求定义的链路跑不通）
Step 4 — 业务线漏洞扫描（5 类漏洞）
    VULN-1: 数据泄漏路径（URL 参数遍历）
    VULN-2: 状态不一致风险
    VULN-3: 操作不可达（无反馈）
    VULN-4: 边界条件处理缺失
    VULN-5: 导航不一致（入口名称不统一）
Step 5 — 欠缺页面清单（GAP-001 ~ N，对比需求和实际）
```

**强制约束**：
- 每个页面必须有真实证据（SU 采集 / 路由配置 / 代码文件）
- 每个断点必须精准到具体页面/组件/API（不能只说"有断点"）
- 每个漏洞必须标注影响范围和修复优先级（P0/P1/P2/P3）

### Phase 2：缺失页面详细设计

**执行方式**: 宿主 LLM 作为页面设计员

**输入**：Phase 1 产出的业务动线分析报告

**设计框架**（每个缺失页面/断点的设计结构，详见 [page-gap-design.md](../spec/role-prompts/page-gap-design.md)）：

```markdown
GAP-DESIGN-{n}：
  1. 页面目标（填补哪个断点？核心价值？）
  2. 用户旅程（来自现有页面 {xxx} → 动作 → 跳转回 {yyy}）
  3. 页面信息架构（独立页 / 对话框 / 侧边抽屉？）
  4. 交互流程（加载/空/正常/异常 4 状态）
  5. 数据模型（前端 state + API 契约 + DB 变更）
  6. 路由守卫与权限
  7. 与现有页面的衔接点（★ 强制：入口/出口/组件复用，精准到代码位置）
  8. 改动范围评估（新增文件数/修改文件数/行数/风险等级）
  9. 验收标准（可测的 checklist）
```

**Karpathy 约束**：
- 🎯 Simplicity First：优先复用现有组件/API/类型，能用对话框解决就不新建独立页面
- 🔬 Surgical Changes：修改现有文件不超过 5 处，精准到路径和行号范围
- 每个设计必须标注来源（GAP/BREAK 编号 + 需求文档章节）

### Phase 3：多角色评审

**执行方式**: 复用 v2.10 proposal-review 闭环

```
4 角色交叉评审（架构师/产品经理/测试专家/独立开发者）
    ↓ 共识收敛（≥2 角色同时提出的问题 = 必须修改）
    ↓ 修改后再评审（最多 3 轮）
    ↓ 共识达成 → 进入开发
```

### Phase 4：执行开发

**执行方式**: 独立开发者按 GAP-DESIGN 逐条实现

**强制要求**：
- 每条 GAP-DESIGN 对应的验收标准必须全部通过
- 新增代码必须有单元测试
- 修改现有文件必须遵循项目编码规范
- 禁止设计之外的过度实现

### Phase 5：UI/UX 视觉测试

**执行方式**: 复用 v2.7 工具链

```bash
# UI/UX 巡检（4 大维度）
python3 scripts/uiux_analyzer.py --url {test_url} --out uiux_report.json

# 视觉回归（如果有 baseline）
python3 scripts/visual_regression.py \
    --baseline screenshots/baseline/login.png \
    --current screenshots/current/login.png \
    --out visual_diff_report.json
```

**测试维度**：
- ✅ 可访问性（WCAG AA 对比度 / img alt / form label）
- ✅ 交互质量（按钮最小尺寸 / 焦点可见性 / 加载反馈）
- ✅ 布局与响应式（元素重叠 / 文字截断 / 视口溢出）
- ✅ UX 反模式（强制注册 / 破坏性操作无确认 / 表单无校验）
- ✅ 视觉回归（像素级 Diff / 红色文字错误检测）

### CLI 使用方式

```bash
# 完整六阶段业务动线分析闭环
python3 scripts/trae_agent_dispatch_v2.py \
    --task "分析用户订单域的业务动线，发现断点并补设计" \
    --business-flow-analysis

# 已有 SU 产物 + 仅做分析（不进入设计/开发/测试）
python3 scripts/trae_agent_dispatch_v2.py \
    --task "只分析订单域业务动线" \
    --business-flow-analysis \
    --skip-design

# 跳过分析，直接对已有动线报告做设计
python3 scripts/trae_agent_dispatch_v2.py \
    --task "根据分析报告设计审批页面" \
    --business-flow-design-only \
    --analysis-report ./docs/analysis/report.md
```

### 产出物

| 阶段 | 产出物 | 位置 |
|------|--------|------|
| Phase 0 | SU 产物（已有则跳过） | `docs/system-understanding/{system_id}/understanding.json` |
| Phase 1 | 业务动线分析报告 | `docs/analysis/{task-id}/business-flow-analysis-report.md` |
| Phase 1 | 页面盘点清单 | `docs/analysis/{task-id}/page_inventory.json` |
| Phase 1 | 断点清单 | `docs/analysis/{task-id}/breaks.json`（BREAK-001 ~ N） |
| Phase 1 | 漏洞清单 | `docs/analysis/{task-id}/vulns.json`（VULN-001 ~ N） |
| Phase 1 | 欠缺页面清单 | `docs/analysis/{task-id}/gaps.json`（GAP-001 ~ N） |
| Phase 2 | 缺失页面详细设计 | `docs/analysis/{task-id}/page-gap-design-doc.md` |
| Phase 3 | 共识收敛报告 | `docs/analysis/{task-id}/consensus-report.md` |
| Phase 4 | 代码改动 | 项目代码（按 GAP-DESIGN 改动） |
| Phase 5 | UI/UX 测试报告 | `docs/analysis/{task-id}/uiux_report.json` + `visual_diff_report.json` |

### 架构复用关系图

```
┌──────────────────────────────────────────────────────────────────────┐
│                  业务动线分析六阶段工作流（v2.11）                      │
│                                                                      │
│  Phase 0 ──> Phase 1 ──> Phase 2 ──> Phase 3 ──> Phase 4 ──> Phase 5│
│  (SU 发现)  (动线分析)   (页面设计)   (多角色评审)  (执行开发)  (视觉测试)│
│                                                                      │
│  ┌─────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │ SU v2.9 │  │   新建 prompt │  │ proposal-     │  │ UI/UX v2.7  │ │
│  │ BFS遍历  │  │ business-    │  │ review v2.10 │  │ visual-      │ │
│  │ pages   │  │ flow-analysis │  │ 4角色评审+    │  │ regression  │ │
│  │ nav     │  │ page-gap-     │  │ 共识收敛      │  │ uiux-       │ │
│  │         │  │ design        │  │ 闭环修改      │  │ analyzer    │ │
│  └─────────┘  └──────────────┘  └──────────────┘  └──────────────┘ │
│                                                                      │
│  ←────────── 复用已有能力，最小新增代码 ──────────→                    │
└──────────────────────────────────────────────────────────────────────┘
```

**核心特性**：
- ✅ **真实证据驱动**：每个页面/断点/漏洞必须有 SU 采集产物或代码证据，禁止凭空分析
- ✅ **5 类断点检测**：入口断裂 / 出口断裂 / 前置条件缺失 / 数据衔接断裂 / 业务闭环断裂
- ✅ **5 类漏洞扫描**：数据泄漏 / 状态不一致 / 操作不可达 / 边界缺失 / 导航不一致
- ✅ **精准设计模板**：每个缺失页面的设计强制标注与现有页面的衔接点（入口/出口/组件复用，精准到代码位置）
- ✅ **proposal-review 质量门禁**：设计方案必须经过 4 角色交叉评审 + 共识收敛
- ✅ **视觉和交互验证**：修改完成后用 uiux_analyzer + visual_regression 自动检测
- ✅ **Karpathy + Ponytail 约束**：每个设计必须遵循 Karpathy 四大原则和 Ponytail 决策梯

### 绝对禁止

❌ 凭空列出"应该有的页面"——必须有需求文档来源或代码证据
❌ 不引用 SU 产物或代码直接做动线分析
❌ 断点/漏洞描述模糊不清（必须精准到具体页面/组件/API）
❌ 设计不标注与现有页面的衔接点（入口/出口必须到代码位置）
❌ 复用路径上新增不必要的组件/API（Ponytail 决策梯强制检查）
❌ 跳过 UI/UX 测试就认定完成

## 高级功能
### 代码走读与审查 (v2.3)
```bash
# 执行真正的多角色协作代码走读（使用 Trae Agent 调度）
python3 scripts/multi_role_collaborative_analyzer.py /path/to/project --workspace /workspace
# 简化的多角色代码走读
python3 scripts/multi_role_code_walkthrough.py /path/to/project --workspace /workspace
```
**真正的多角色协作分析流程**:
1. **阶段一：项目扫描**
   - 递归扫描项目目录
   - 识别源代码文件、配置文件、文档文件
   - 统计项目基本信息
   - 检测技术栈和框架
   - 识别项目模块
2. **阶段二：调用 Trae Agent 调度**
   - 使用 `trae_agent_dispatch_v2.py` 分发任务
   - 每个角色使用专属 prompt 模板
   - 角色包括：架构师、产品经理、独立开发者、UI 设计师、测试专家
   - 各角色独立执行真实分析
3. **阶段三：文档对齐**
   - 收集各角色分析结果
   - 识别共识点与差异点
   - 合并统一的代码地图
   - 生成代码走读审查报告
**输出文档**:
| 文档 | 内容 |
|------|------|
| `<project>-ALIGNED-CODE-MAP.md` | 统一代码地图：项目概览、架构分层、多角色分析结果、对齐结果 |
| `<project>-CODE-REVIEW-REPORT.md` | 代码走读审查报告：审查概述、架构评审、代码质量评估、文档一致性检查、改进建议 |
**代码地图内容** (核心结构，不含审查风险):
- 项目概览
- 架构视图
- 代码结构
- 多角色视角摘要
- 分析共识
**审查报告内容** (含风险和建议):
- 审查概览
- 架构评审
- 代码质量评估
- 多角色共识
- **文档与代码一致性检查清单** ← v2.3 新增
- 改进建议
- 附录
### 3D 代码地图可视化 (v2.3)
基于 Three.js 的交互式代码结构可视化，支持流动动画和主题切换。
```bash
# 生成可视化数据
python3 scripts/code_map_generator_v2.py /path/to/project --visual
# 打开可视化页面
~/.trae/skills/docs/code-map-visualizer.html
```
**功能**: 3D 场景渲染、前后端分层、调用链路、动态流动效果、主题切换
**详细说明**: `docs/guides/VISUALIZATION.md`
### 任务可视化页面 (v2.3)
实时展现各角色任务状态、进度、依赖关系、交接过程。
```bash
~/.trae/skills/docs/task-visualizer.html
```
**功能**: 任务统计、角色卡片、依赖关系、交接时间线、协同关系图
**详细说明**: `docs/guides/VISUALIZATION.md`
### 代码地图生成
```bash
python3 scripts/code_map_generator_v2.py /path/to/project --workspace /workspace
# 输出: <project>-CODE_MAP.md
```
### 项目理解
```bash
python3 scripts/project_understanding.py /path/to/project
```
### 规范驱动开发
```bash
python3 scripts/spec_tools.py init
python3 scripts/spec_tools.py analyze
python3 scripts/spec_tools.py update --spec-file SPEC.md
```

### Ponytail 决策梯（v2.6 新增 — 少写多余代码）

> **来源**: Ponytail 项目（6 步决策梯）+ 项目规则（16 条不可简化红线）
> **目的**: 在 Karpathy Simplicity First 原则之上，提供可执行的"写代码前先停一停"决策梯

**6 步决策梯**（按顺序停在第一个满足的台阶）:
1. **YAGNI** — 这东西真的需要存在吗？→ 推测性需求 = 跳过
2. **标准库优先** — 语言标准库能搞定？→ 直接用
3. **平台原生** — 运行时平台自带功能能覆盖？→ 用平台特性
4. **复用现有** — 已安装的依赖能解决？→ 复用，不新增依赖
5. **一行优先** — 能写成一行？→ 写成一行（不牺牲可读性）
6. **最小可行** — 以上都不行 → 写最少能做工作的代码

**三种强度模式**:
| 模式 | 说明 | 适用角色 |
|------|------|---------|
| `lite` | 精简版决策梯 | test_expert, ui_designer |
| `full`（默认） | 完整 6 步 + 16 条红线 | solo_coder, architect |
| `ultra` | YAGNI 极端主义（autonomous 模式自动降级为 full） | 手动指定 |

**16 条不可简化红线**:
- 原始 Ponytail 6 条（输入校验、错误处理、安全、无障碍、用户要求、硬件校准）
- 项目规则 10 条（真实业务逻辑、需求文档功能、非平凡逻辑检查、并发安全、错误处理、日志审计、配置密钥、事务边界、API 契约、隐私数据）

**使用方式**:
```bash
# 在 autonomous 模式中自动注入（默认 full 模式）
# 手动切换模式
/ponytail ultra    # 切换到 ULTRA 模式
/ponytail lite     # 切换到 LITE 模式
/ponytail off      # 关闭决策梯
/ponytail          # 查看当前模式
```

**债务台账**:
- 代码中标记 `# ponytail: <说明>` 或 `# ponytail: <上限>, <升级路径>` 记录故意简化
- `DebtCollector` 自动扫描，识别无升级路径的债务（`no_trigger`）超过阈值时告警
- `RequirementTracer` 追踪需求文档 `[REQ-XXX]` 标记到代码实现的覆盖情况

**测试**:
```bash
# 运行全部 Ponytail 测试（10 个文件，98 个测试用例）
bash scripts/tests/scripts/run_ponytail_tests.sh
```

详细指南见 `docs/guides/PONYTAIL_GUIDE.md`。

### Autonomous Mode（v2.6 / Phase 18 — Ralph 风格自主编排）

> **来源**: Ralph 自主编排框架 + gnhf 库启发
> **目的**: 让多角色团队在用户睡觉时自主完成完整项目生命周期

**4 阶段循环**（plan → dev → verify → fix）:
```
┌─────────────────────────────────────────────────┐
│  Plan（规划）→ Dev（开发）→ Verify（验证）→ Fix（修复）│
│       ↑                                          │
│       └──────────── 循环直到完成 ────────────────┘
└─────────────────────────────────────────────────┘
```

**9 个核心组件**:
| 组件 | 功能 |
|------|------|
| LoopController | Ralph 循环控制器（4 阶段调度） |
| RunState | 运行状态持久化（SHA256 校验 + Resume） |
| NotesMemory | Notes 跨轮记忆（notes.md 读写） |
| GitDriver | Git 驱动（自动 commit + 分支管理） |
| SleepGuard | 防休眠守护（阻止系统休眠） |
| SmartConfirmation | 智能确认（三态：auto-approve/ask-user/fail-closed） |
| AutoSkillLoader | Auto-skill 加载器（自动加载所需 skill） |
| DispatcherAdapter | Dispatcher 适配器（Claude Code / Trae） |
| ConfigLoader | 配置加载器（autonomous.yml） |

**17 个 CLI flag**:
```bash
python3 scripts/trae_agent_dispatch_v2.py \
    --auto-mode \
    --auto-goal "实现用户登录功能" \
    --auto-max-iterations 10 \
    --auto-confirmation smart \
    --auto-git-enabled \
    --auto-skill-injection \
    --auto-notes-memory \
    --auto-sleep-guard \
    --auto-resume \
    --auto-ponytail-mode full \
    # ... 更多 flag 见 AUTONOMOUS_MODE_GUIDE.md
```

详细指南见 `docs/guides/AUTONOMOUS_MODE_GUIDE.md`。

### Dynamic Workflows（v1.7 — 6 大动态工作流模式）

> **来源**: Anthropic Multi-Agent Research + 项目实践
> **目的**: 根据任务特征自动选择最优工作流模式
> **v2.8.3 更新**: 6 大模式作为提示词层概念保留，由宿主 LLM 执行路由决策。脚本层 12 个实现模块（`scripts/dynamic_workflow/`）已全部归档——零外部引用，确认为死代码。仅保留 `semantic_embedder.py`（移至 `scripts/` 根目录）提供 TFIDF/Hashing 确定性相似度。

**6 大模式**（宿主 LLM 执行）:
| 模式 | 适用场景 | 执行方式 |
|------|---------|---------|
| classifier-dispatch | 任务分类后分发 | 分类器 → 角色分发 |
| fan-out-aggregate | 并行处理 + 汇总 | 扇出 N 个子任务 → 聚合结果 |
| adversarial-verify | 对抗式验证 | 生成 → 审查 → 修复循环 |
| generate-filter | 生成 + 过滤 | 批量生成 → 质量过滤 |
| tournament | 锦标赛选择 | 多方案竞争 → 最优胜出 |
| loop-until-done | 循环直到完成 | 迭代直到成功标准满足 |

详细方案见 `docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md`。

### Cybernetics 工程控制论（v2.5 — 三环控制模型）

> **来源**: 钱学森工程控制论 + ICLR 2026 Profile-Aware Maneuvering
> **目的**: 通过反馈控制环实现自适应执行
> **v2.8.3 更新**: 4 个低耦合文件已归档（cybernetics_integration / cybernetics_bridge / hierarchical_control / context_fingerprint_integration）。3 个高耦合核心组件保留。

**保留的核心组件**（`scripts/` 根目录）:
| 组件 | 功能 | 引用数 |
|------|------|--------|
| `performance_fingerprint.py` | 性能画像（执行案例记录 + 相似案例检索） | 23 |
| `feedback_control_loop.py` | 反馈控制环（感知-决策-执行-反馈） | 11 |
| `guard_coordinator.py` | 守护协调器（执行前验证 + 异常检测） | 5 |

**三环控制模型**:
- **战略层**: 长期目标 + 资源规划
- **战术层**: 中期策略 + 任务分解
- **执行层**: 短期动作 + 实时反馈

详细分析见 `docs/dev/CYBERNETICS_ANALYSIS.md`。

### 插件热加载（v2.6 / Phase 17 — V3 插件架构）

> **目的**: 支持运行时动态加载/卸载插件，无需重启

**3 种加载路径**:
1. **Drop-in 目录加载**: 自动扫描 `drop-in/` 目录下的插件
2. **Hot Register API**: 通过 `hot_register()` API 动态注册
3. **HotReloadWatcher**: 文件监视器自动检测变更并重载

**核心模块**（`scripts/dispatcher/`）:
- `goal_dispatcher.py` — Goal 调度器（DAG 依赖图）
- `plugin_context.py` — 插件上下文
- `drop_in_loader.py` — Drop-in 目录加载器
- `hot_reload_watcher.py` — 热加载监视器
- `reload_guard.py` — 重载守护（Condition 替代 Event）

**V3 插件实现**（`scripts/plugins/`）:
- `autonomous.py` — Ralph Autonomous 插件
- `multi_goal.py` — 多 Goal 编排插件（DAG + Resume + Reuse + Schedule + Report）
- `graph.py` — 图编排插件
- `loop.py` — 循环编排插件
- `resume.py` — 断点续跑插件
- `cancel.py` — 取消插件

详细设计见 `docs/dev/PHASE17_PLAN.md`。

### UI/UX 巡检与视觉回归（v2.7 新增 — 前端质量门禁工具）

> **目的**: 在 E2E 测试阶段提供可复用的 UI/UX 质量与视觉回归检测脚本，
> 作为「UI 设计师」与「测试专家」角色的标准前端质量门禁。
> **设计原则**: 标准库优先（Playwright + PIL）、YAGNI、失败安全。

#### `scripts/uiux_analyzer.py`（UI/UX 巡检分析器）

4 大检测维度：
| 维度 | 检测项 | 关键阈值 |
|------|--------|---------|
| 可访问性 (A11y) | WCAG AA 对比度、img alt、form label、语义化标签、键盘可达 | 正常文本 4.5:1 / 大文本 3:1 |
| 交互质量 | 按钮最小尺寸、焦点可见性、加载反馈 | 最小可点击 ≥44px（Apple HIG） |
| 布局与响应式 | 元素重叠、文字截断、视口溢出 | — |
| UX 反模式 | 强制注册、破坏性操作无确认、表单无校验 | — |

**关键类**:
- `UIUXIssue`（dataclass）— 包含 `severity` (HIGH/MEDIUM/LOW) / `category` (a11y/interaction/layout/ux) / `rule` / `element` / `message` / `fix` / `metric`
- `UIUXAnalyzer`（核心）— `audit(page)` → `list[UIUXIssue]`，`dump(path)` 输出 JSON

**用法示例**:
```python
from uiux_analyzer import UIUXAnalyzer

analyzer = UIUXAnalyzer()
page.goto("https://example.com/login")
issues = analyzer.audit(page)
analyzer.dump(Path("reports/uiux.json"))
for issue in issues:
    if issue.severity == "HIGH":
        print(f"[{issue.category}] {issue.message} → {issue.fix}")
```

**Playwright 综合探针**: 一次 `page.evaluate` 取齐所有探针数据（图片/表单/按钮/链接/标题/错误），避免多次往返。

#### `scripts/visual_regression.py`（视觉回归 + 显示完整性）

3 大检测维度：
| 维度 | 检测项 | 实现 |
|------|--------|------|
| 视觉回归 | 像素级 Diff、SSIM 区域级 Diff | PIL `ImageChops` + 简化 SSIM |
| 数据显示不全 | 文本截断、元素溢出视口、图片未加载、骨架屏 >10s、长表格横向滚动 | Playwright DOM 检查 |
| 显示错误 | 红色文字/背景、错误关键词、Ant Design / Arco / Element UI error Toast、浏览器原生 dialog | HSV 检测 + 关键词 + 类名匹配 |

**关键类**:
- `ChangedRegion`（dataclass）— `x/y/width/height/pixel_count/severity`
- `DiffResult`（dataclass）— 完整 diff 结果（含 `pixel_diff_ratio` / `ssim_score` / `changed_regions` / `data_incomplete` / `display_errors`）
- `VisualRegressionChecker`（核心）— `compare(baseline, current, step) → DiffResult`

**软依赖**:
- Pillow（必需）
- numpy（可选，启用更好的 SSIM）
- playwright（必需，DOM 检查）

**用法示例**:
```python
from visual_regression import VisualRegressionChecker

checker = VisualRegressionChecker(pixel_diff_threshold=0.01)
result = checker.compare(
    baseline_path="baseline/login.png",
    current_path="current/login.png",
    test_id="TC-001",
    step="submit_form",
    page=page,  # 用于 DOM 检查
)
if result.pixel_diff_ratio > 0.01:
    print(f"⚠️ 像素差异 {result.pixel_diff_ratio:.2%}")
    for region in result.changed_regions:
        print(f"  变化区域: {region.severity} ({region.width}x{region.height})")
```

**CLI 用法**:
```bash
# UI/UX 巡检（需要先启动浏览器/Playwright session）
python3 scripts/uiux_analyzer.py --url https://example.com --out report.json

# 视觉回归
python3 scripts/visual_regression.py \
    --baseline baseline/login.png \
    --current current/login.png \
    --threshold 0.01 \
    --out diff_report.json
```

**角色集成**:
- **UI 设计师** — 交付稿前自检：`uiux_analyzer.audit(page)` 输出 `reports/uiux.json` 供评审
- **测试专家** — E2E 套件像素级断言：替代人工对比基线截图
- **Solo Coder** — PR 门禁：CI 中调用 CLI，输出 JUnit XML 报告

**失败安全设计**: 任一检查器异常被 try/except 隔离，不影响主流程与其他检查；返回的 `error` 字段记录异常原因供排查。

### 既有系统理解能力（v2.9 新增 — 黑盒系统反向理解）

对无文档、无源码的遗留黑盒 Web 系统（配套 MySQL/PostgreSQL + Redis）做**全只读**反向理解：Playwright 自动登录后以图式 BFS 遍历页面（url_key 去重、动作分级、破坏性请求网络级拦截），并行进行 DB 只读内省（表/列/索引/采样/隐式外键推断）与 Redis 只读键模式采集，再做 UI↔API↔表↔Redis 三角关联分析，最终渲染固定 10 节《系统功能理解文档》（UNDERSTANDING.md）与机读 understanding.json + Mermaid 图。采用两阶段工作流：确定性脚本层零 LLM 调用、不出语义结论，语义结论由宿主 LLM 按提示词契约回填后经 `--render-only` 校验收口。

```bash
# 阶段 A：确定性采集（凭据推荐 JSON 配置文件，chmod 600）
python3 scripts/system_understanding.py --config config.json --skip-llm-phase
# 阶段 B：宿主 LLM 读取脱敏产物 understanding.json，按
#   docs/spec/role-prompts/su-llm-backfill.md 契约产出 findings 写回该文件
# 阶段 C：收口渲染（不启动浏览器、不连库，校验 findings 后重渲染全部产物）
python3 scripts/system_understanding.py --out docs/system-understanding --system-id <id> --render-only
```

**五条安全红线（不可协商）**: ① 凭据不进 LLM 上下文、不落盘明文（统一 redact 管线 + 日志全程脱敏）；② DB 会话只读 + 语句白名单校验器，严禁任何 DDL/DML；③ Redis 命令硬编码只读白名单；④ 浏览器网络层拦截全部非 GET 请求与白名单外域；⑤ 危险按钮（删除/提交/支付等）零点击、只记录。软依赖（playwright/pymysql/psycopg2/redis）缺失时诚实降级并给出安装命令，绝不以假数据冒充已采集结果。

详细使用指南见 `docs/guides/SYSTEM_UNDERSTANDING_GUIDE.md`。

#### 专家详说（v2.9.1 — SU 第三阶段 SFD）

SU 采集+回填收口后的第三阶段（`scripts/su/detailed_doc.py`，零网络/零凭据/零新依赖，不启动浏览器不连库）：

```bash
# 1) 前置校验 + 五视角素材包 + 8 节大纲骨架
python3 scripts/system_understanding.py --out docs/system-understanding --system-id <id> --detailed-doc
# 2) 宿主 LLM 派发五专家（su-detailed-architect/product/walkthrough/ui/qa.md），
#    产出 detailed/sections/0N-xxx.doc.md
# 3) 装配终稿 SYSTEM_FUNCTION_DOC.md + assembly-report.json
python3 scripts/system_understanding.py --out docs/system-understanding --system-id <id> --assemble
```

契约：E-n 引用 (seq,ref) 双键 + 锚点漂移检测；四判据凭据扫描；终稿原子写、`--force` 终稿保护；详说模式拒绝 `--fresh/--resume/--skip-llm-phase` 组合。

## 文档结构

```
docs/
├── guides/                 # 用户指南（Ponytail/Autonomous/Karpathy/可视化）
├── spec/                   # 规范文档（CONSTITUTION/SPEC/role-prompts）
│   └── role-prompts/       # 5 个核心角色 + 逻辑漏洞审查专家的代码分析模板
├── dev/                    # 开发文档（各 Phase 设计方案 + 最终报告）
├── roles/                  # 角色执行记录（architect/product-manager/solo-coder/test-expert/ui-designer）
└── project-understanding/  # 项目理解文档
```

## 浏览器端页面功能与视觉测试闭环（v2.12 新增 — 操作点细化 → 数据测试集 → 用例评审 → 三宿主执行 → 全程记录）

> **设计理念**: 页面做完了不等于能用——路由通不代表操作点通，接口 200 不代表用户看到的结果对。这个工作流从前后端代码与文档出发（可复用 v2.11 业务动线分析结果），把每条业务流程细化到**页面操作点**级别，制定**六类多样化数据测试集**，编写可断言的测试用例并经四角色评审，然后在真实浏览器中逐一执行——**每一步操作、每一个断言、每一条页面/后台错误全部留痕**，碰到阻碍先解决再推进。
>
> **三宿主**（auto 模式按序降级）：
> - **agent-browser**：隔离会话、headless 批量、并行回归
> - **Trae 内置浏览器**：零配置、IDE 内单流程快速验证
> - **Chrome DevTools MCP**：网络面板深度检查、console 全量采集、性能 trace
>
> **v2.12 架构**：复用三大既有能力 + 新建 3 prompt + 1 确定性记录脚本
> - **v2.11 business-flow**：FLOW/OP 清单直接复用为测试覆盖对象
> - **v2.10 proposal-review**：共识收敛机制复用于测试用例四角色评审
> - **v2.7 visual_regression / uiux_analyzer**：视觉基线对比与深度巡检衔接
> - **新建**：browser-test-planner.md（规划方法论）、browser-test-review.md（测试评审）、browser-test-execution.md（执行+阻碍协议）、`scripts/browser_test_recorder.py`（schema 强制的记录器）

### 四阶段工作流

```
Phase 1: 测试规划（Planner）
    输入: 前后端代码 + 文档 +（可选）v2.11 动线分析结果
    Step 1 功能与流程梳理（复用 FLOW-*/PAGE-*，做增量校验）
    Step 2 操作点细化 OP-*（一次用户可感知动作 + 代码位置 + 可断言预期）
    Step 3 六类数据测试集 DT-*（正向P/边界B/非法N/空E/权限A/时序T）
    Step 4 测试用例 TC-*（P0 覆盖全部流程主路径）
    Step 5 执行序 + 宿主分派 + 破坏性用例回滚方案
    产出: test-plan.md / test-datasets.json / test-cases.json / test-execution-plan.json
        ↓
Phase 2: 测试用例多角色评审（复用 v2.10 共识收敛，最多 3 轮）
    测试专家（一票否决：任一 FLOW 主路径无 P0 覆盖）
    独立开发者（选择器真实性抽查 / 前置可构造性）
    产品经理（业务真实性 / 用户视角断言）
    架构师（环境依赖 / 数据隔离性 / 维护成本）
    门禁: 共识问题清零 + BLOCKER 清零
        ↓
Phase 3: 浏览器逐一执行 + 全程记录（Executor）
    recorder run → 逐用例 start → 每步 step/assert/error/shot → case end
    阻碍协议（六类，先解决再推进）:
      选择器失效→修选择器回写用例 / 数据缺失→补数据 / 环境故障→停止报告
      宿主故障→降级切宿主 / 用例设计错误→以代码文档为准修正 / 疑似缺陷→不改断言，标记 bug
    视觉基线用例 → scripts/visual_regression.py
        ↓
Phase 4: 汇总与闭环衔接
    recorder finalize → summary.json + summary.md
    门禁: P0 通过率 100%，否则整体 FAIL
    疑似缺陷清单 → 修复环节；新断点 → 回填 v2.11 BREAK 清单
```

### CLI 使用方式

```bash
# 完整闭环（规划→评审→执行→记录）
python3 scripts/trae_agent_dispatch_v2.py \
    --task "测试订单全流程" --project-root /path/to/proj \
    --browser-test --target-url http://localhost:3000 \
    --reuse-flow-analysis docs/business-flow-report.md

# 仅规划（产出四件套，不评审不执行）
python3 scripts/trae_agent_dispatch_v2.py \
    --task "梳理支付流程测试点" --browser-test-plan-only

# 仅执行已有计划（需计划四件套目录 + 被测 URL）
python3 scripts/trae_agent_dispatch_v2.py \
    --task "执行回归" --browser-test-exec-only \
    --test-plan-dir ./browser-test-plan --target-url http://localhost:3000 \
    --browser-hosts chrome-devtools
```

### 记录器（确定性工具，纯标准库）

```bash
python3 scripts/browser_test_recorder.py --out browser-test-runs run --run-id r1 --url ...
# 子命令: run / case / step / assert / error / shot / blocker / verify / finalize
# schema 强制: fail 无原因拒绝、截图文件必须真实存在、finalize 做 summary-逐条一致性校验
```

### 产出物清单

| 产物 | 阶段 | 说明 |
|------|------|------|
| `test-plan.md` | Phase 1 | 功能-页面对照表 + OP-* 操作点清单 |
| `test-datasets.json` | Phase 1 | 六类数据测试集 |
| `test-cases.json` | Phase 1 | TC-* 用例（关联 FLOW/OP/DT） |
| `test-execution-plan.json` | Phase 1 | 执行序 + 宿主分派 + 破坏性标注 |
| `browser-test-runs/{run_id}/` | Phase 3-4 | run-meta / cases/*.json / screenshots / summary.json / summary.md |

### 绝对禁止（v2.12）

- ❌ 凭空编造页面/路由/选择器（必须标注代码文件:行号证据）
- ❌ 预期结果写成"页面正常""功能可用"等不可断言描述
- ❌ 数据测试集只有正向数据（六类缺一必须书面说明理由）
- ❌ P0 用例未覆盖全部流程主路径就提交评审 / 进入执行
- ❌ 疑似产品缺陷被当作"用例问题"悄悄改断言放行
- ❌ 环境故障（服务 5xx/连不上）谎报为用例 FAIL 或跳过硬跑
- ❌ 跳步骤执行、无 expected/actual 对比的失败记录、失败无截图

## 故障排查

### 角色识别错误
```bash
# 明确指定角色
python3 scripts/trae_agent_dispatch_v2.py \
    --task "..." \
    --agent architect
```

### 添加新角色
1. 在 `roles.json` 中添加角色配置
2. 更新关键词列表
3. 调整调度规则

### 自定义调度规则
修改 `AgentDispatcher.analyze_task()` 方法。

## 总结

Trae Multi-Agent Dispatcher 提供了：
- ✅ 智能角色识别（5 个核心角色 + AI 语义匹配）
- ✅ 多角色协同 + 共识机制
- ✅ 八阶段标准工作流程（v2.8 新增阶段 8：文档对照代码审查）
- ✅ 方案撰写全流程（v2.10，业界调研→方案撰写→4 角色交叉评审→共识收敛→闭环修改）
- ✅ 业务动线分析与页面设计闭环（v2.11，SU 页面发现→断点检测→漏洞扫描→缺失页面设计→proposal-review 评审→开发→UI/UX 视觉测试）
- ✅ 浏览器端页面功能与视觉测试闭环（v2.12，操作点细化→六类数据测试集→用例四角色评审→三宿主浏览器执行→步骤级全程记录与阻碍闭环）
- ✅ Karpathy 四大核心原则强制执行（v2.4）
- ✅ Cybernetics 工程控制论三环控制（v2.5）
- ✅ Ponytail 决策梯 + 16 条不可简化红线（v2.6）
- ✅ Autonomous Mode 自主编排（Phase 18，4 阶段循环 + 9 核心组件）
- ✅ Dynamic Workflows 6 大动态工作流模式（v1.7）
- ✅ 插件热加载 V3 架构（Phase 17）
- ✅ 代码走读与审查 + 3D 代码地图可视化
- ✅ UI 设计（避免 AI slop）
- ✅ 既有系统理解能力 SU（v2.9，黑盒 Web 系统全只读反向理解 + 10 节理解文档）

通过智能调度 + 自主编排 + 决策梯约束，减少用户干预，提升协作效率！
