# 变更日志
本文档记录 Trae Multi-Agent Skill 的所有重要变更。
格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [2.9.2] - 2026-09-30

### Fixed
- ✅ **D1 遗留修复：understanding.json 的 meta.run_status 不再冻结 running**——`--render-only` 与完整流水线正常收口时，渲染环节按"成功即 mark(completed)"的事实向磁盘投影注入终态（StateStore 新增 `run_status_override` 参数 + `validate_run_status_override` 终态校验，仅放行 completed/interrupted，running 等非法值 ValueError 拒绝）；中断链路维持先 mark 后导出的原语义（e2e 场景[4]零回归）
- ✅ 新增 3 个 D1 回归用例（completed 投影 / 默认 None 维持库值 / interrupted 投影 + 校验非法域），SU 单测聚合 20 模块全绿；e2e [0]-[7]、[9]-[13] 全 PASS、[8] SKIP

## [2.9.1] - 2026-09-30

### Added

#### v2.9.1 — 专家详说阶段（SFD，System Function Doc）

在 SU 两阶段（采集 + 回填）之后新增第三阶段"专家详说"：以回填收口后的
`understanding.json` 为唯一事实源，前置校验通过后生成五视角素材包与 8 节大纲骨架，
由宿主 LLM 派发五位专家子代理分节撰写，最终 `--assemble` 装配为
《系统功能详说文档》（SYSTEM_FUNCTION_DOC.md）+ 装配报告（assembly-report.json）。
零网络、零凭据、零新依赖；不启动浏览器、不连库。

##### SFD 核心能力

- ✅ 新增 `scripts/su/detailed_doc.py`（`scripts/su/` 能力包 18 → 19 个模块文件）
  - `--detailed-doc`：前置校验（锚定 `understanding.json` 的 `meta.run_id`、findings 双源一致性）→ 生成五视角素材包（`detailed/inputs/`，白名单提取 + scrub 复核 + 锚点 manifest）→ 输出 8 节大纲骨架
  - `--assemble`：装配五专家分节稿（`detailed/sections/0N-xxx.doc.md`）为终稿 `SYSTEM_FUNCTION_DOC.md` + `assembly-report.json`（终稿原子写；已存在终稿时需 `--force`）
  - 详说模式拒绝与 `--fresh` / `--resume` / `--skip-llm-phase` 组合（CLI 显式报错）
- ✅ 契约要点：E-n 引用采用 (seq, ref) 双键并做锚点漂移检测；四判据凭据扫描（C1 scrub 差集 / C2 URL userinfo 带脱敏豁免 / C3 键值对 / C4 扩展敏感键名 × 高熵值）
- ✅ 新增 5 个专家派发提示词：`docs/spec/role-prompts/su-detailed-{architect,product,walkthrough,ui,qa}.md`（架构师 / 产品经理 / 走读开发 / UI / 测试）
- ✅ 新增手工冒烟脚本 `scripts/tests/scripts/sfd_smoke.sh`（S-1/S-5 最小链路：造库 → render-only → detailed-doc → 五草稿 → assemble → 幂等/保护/CLI 违例速查）
- ✅ 设计文档：`docs/dev/SYSTEM_FUNCTION_DOC_PRD.md`（PRD-SFD-001）/ `docs/dev/SYSTEM_FUNCTION_DOC_ARCHITECTURE.md`（ARCH-SFD-001）

##### SFD 测试

- ✅ 新增 6 个单测模块（`scripts/tests/test_su_detailed_{precheck,packages,outline,scan,assemble,cli}.py`，112 个测试用例），全部通过
- ✅ SU 单测聚合 14 → 20 个模块（`scripts/tests/test_su_*.py` 实测 520 个用例，500+ 表述）
- ✅ 新增 e2e 场景 [9]-[13]（对应 S-1~S-5；其中 [10]-[13] 零浏览器依赖恒执行）

## [2.9.0] - 2026-09-28

### Added

#### v2.9 — 既有系统理解能力（SU，System Understanding）

面向无文档、无源码的遗留黑盒 Web 系统（配套 MySQL/PostgreSQL + Redis），
在**全程只读、零副作用**前提下自动完成反向理解，产出固定 10 节
《系统功能理解文档》（UNDERSTANDING.md）+ 机读 understanding.json + Mermaid 图。
采用两阶段工作流：脚本层零 LLM 调用、不出语义结论；语义结论由宿主 LLM
按提示词契约回填，CLI 校验收口——证据与结论严格分离。

##### SU 核心能力

- ✅ 新增 `scripts/system_understanding.py` CLI 入口 + `scripts/su/` 能力包（18 个模块文件）
  - `browser_login.py` — Playwright 自动登录（显式选择器/启发式识别、会话维持与自动重登 ≤3 次、`--storage-state` 人工已登录态旁路）
  - `site_crawler.py` / `url_key.py` / `action_tier.py` / `route_policy.py` — 图式 BFS 页面遍历（url_key 规范化去重、DOM 剪枝快照、动作三级分级 T1/T2/T3、page.route 网络层拦截全部非 GET 与白名单外域）
  - `db_inspector.py` / `db_guard.py` — MySQL/PostgreSQL 只读内省（表/列/注释/PK/显式 FK/索引/行数估算/采样逐值脱敏）+ 隐式 FK 三重预筛推断；会话级只读 + SQL 语句白名单校验器
  - `redis_inspector.py` / `redis_guard.py` — Redis SCAN 游标采集 + 命令硬编码只读白名单 + 键模式聚类（数量/TTL 分布/类型）
  - `relation_analyzer.py` / `api_observer.py` — UI↔API↔DB 三角关联（页面↔表↔API↔Redis 键模式确定性关联证据：重合度/包含度数值）
  - `document_renderer.py` — 10 节《系统功能理解文档》渲染（含证据附录、未验证推断与未覆盖清单）+ understanding.json + Mermaid 图源
  - `state_store.py` — SQLite（WAL）断点续跑状态库（SIGINT 退出码 130 后 `--resume` 继承已完成页面/表/键，`--fresh` 归档重跑）
  - `config.py` / `preflight.py` / `limiter.py` / `deps.py` / `dto.py` / `wait_ready.py` — 三级配置通道、预检、预算/限速、软依赖运行时探测
- ✅ 五条安全红线（不可协商）：凭据不落盘明文（统一 redact 管线 + 日志全程脱敏）、DB 会话只读 + 语句白名单、Redis 命令只读白名单、浏览器网络层拦截全部非 GET 与白名单外域、危险按钮零点击只记录
- ✅ 诚实降级：playwright/pymysql/psycopg2/redis 缺失时按透镜显式声明未采集并给出安装命令，绝不以假数据冒充
- ✅ LLM 契约：`docs/spec/role-prompts/su-llm-backfill.md`（PROMPT-SU-001，findings 字段口径与整批拒绝校验）
- ✅ 文档：`docs/guides/SYSTEM_UNDERSTANDING_GUIDE.md` / `docs/dev/SYSTEM_UNDERSTANDING_PRD.md` / `docs/dev/SYSTEM_UNDERSTANDING_ARCHITECTURE.md`

##### SU 测试

- ✅ 新增 14 个单测模块（`scripts/tests/test_su_*.py`，400+ 测试用例），全部通过
- ✅ 新增 e2e 场景 [0]-[7]（playwright 缺失时浏览器场景显式 SKIP；场景 [8] 需外部注入 `SU_TEST_MYSQL_DSN`/`SU_TEST_REDIS_URL`，缺省 SKIP）
- ✅ 测试脚本：`scripts/tests/scripts/run_system_understanding.sh` / `run_system_understanding_e2e.sh`（均已接入 `run_all.sh` 聚合）

### Fixed

- ✅ 修复 `claude-code-skill.json` 既有 JSON 语法错误

## [2.8.4] - 2026-07-23

### Added — Trae 宿主 LLM SubAgent 调度协议（HostLLMBridge）

> **背景**：Trae 环境下无 `claude` CLI，`ClaudeCodeSubAgentAdapter._fallback_no_subagent` 返回 `success=False`，导致 autonomous 循环无限重试。真实子代理只能由宿主 LLM 通过 Task 工具调度。本版本通过文件协议桥接这一鸿沟。

#### 核心新增：HostLLMBridge 文件协议
- 新增 `scripts/host_llm_bridge.py`（525 行）：解耦 Python 脚本与宿主 LLM 的 Task 工具
  - `create_request()`：写入 `request_{id}.json` + `protocol.marker`（含 request_id / agent_type / task / prompt / 超时）
  - `wait_for_response()`：轮询 `response_{id}.json`，JSON 解析失败重试 3 次（0.1s 间隔），超时返回 `timeout=True`
  - `write_response()`：原子写入（`tempfile` + `os.replace`），写入后清除 `protocol.marker`
  - `read_marker()` / `read_request()` / `clear_marker()`：供宿主 LLM 轮询使用
  - `validate_request_id()`：正则 `^[a-zA-Z0-9_]+$` 防路径遍历攻击
  - CLI 子命令：`read-marker` / `read-request` / `write-response` / `clear-marker`
- 新增设计文档 `docs/dev/HOST_LLM_BRIDGE_DESIGN.md`（v2.8.4 修订版 R1）

#### 平台检测与适配
- `claude_code_subagent_adapter.py` 平台检测优先级调整：`host_llm` > `claude_code` > `unknown`
  - `TRAE_ENV` / `TRAE_AGENT_PATH` → `host_llm`（文件协议桥接）
  - `CLAUDE_CODE_ENV` / `ANTHROPIC_ENV` → `claude_code`（claude CLI subprocess）
  - 无 → `unknown`（诚实降级 `_fallback_no_subagent`）
- 新增 `_invoke_via_host_llm()`：调用 `HostLLMBridge.create_request` → `wait_for_response`
- `_invoke_via_trae()` 标记 deprecated，委托给 `_invoke_via_host_llm`（删除旧递归实现）

#### 失败熔断与超时透传
- `autonomous/loop_controller.py::_should_stop()` 新增连续失败熔断：
  - 连续 2 次迭代均为 `retriable` 且原因包含相同关键词（timeout / host_llm_timeout / dispatch_failed / 无可用 subagent）→ 升级为 `fatal` 终止循环
  - 避免宿主 LLM 未启动轮询时脚本无限重试浪费资源
- `autonomous/handlers/dev_handler.py` summary 包含 `dispatch_failed` 关键词供熔断检测
- `dispatch/legacy.py::_dispatch_via_claude_code` 失败路径透传 `timed_out` 信息（`host_llm_timeout:` 前缀）

#### SKILL.md 协议章节
- 新增「Trae 宿主 LLM SubAgent 调度协议（v2.8.4 新增）」章节，包含：
  - 工作流程图（8 步）：RunCommand(blocking=false) → 脚本写请求 → 宿主 LLM 轮询 marker → Task 执行子代理 → 写 response → 脚本读结果
  - 宿主 LLM 轮询契约：启动后 2 秒首检，之后每 5 秒检查 `protocol.marker`
  - Task 工具 subagent_type 映射表（architect → search，其余 → general_purpose_task）
  - 强制原子写入要求（禁止 `echo > response.json`）
  - 熔断机制说明
  - 平台检测优先级表

### Tested

- 新增 29 个单元测试（`scripts/tests/test_host_llm_bridge.py`）全部通过：
  - TestCreateRequest(2) / TestWriteResponse(3) / TestWaitForResponse(3) / TestReadMarker(3)
  - TestReadRequest(2) / TestValidateRequestId(12) / TestConcurrentRequests(1) / TestEndToEnd(3)
- 回归测试 83 个全部通过：test_host_llm_bridge + test_workflow_engine_v2 + test_v3_dispatcher + test_claude_code_subagent_adapter_prompt + test_workflow_loop_controller
- 回归测试 98 个全部通过：test_phase18_loop_controller + test_phase18_handlers + test_phase18_dispatcher_adapter + test_phase18_integration + test_v3_integration
- sync_manifests.py 校验通过（三份 manifest 版本一致）
- 全部修改的 Python 文件语法编译通过

### Architecture Review

经架构师审查（Task general_purpose_task）发现 3 个阻断性问题，已全部修订：
1. **P2.1 stdout 标记被日志淹没** → 改为独立 `protocol.marker` 文件，不依赖 stdout
2. **P2.2 宿主 LLM 轮询契约未定义** → 定义 2s 首检、5s 间隔的轮询契约
3. **P4.1 超时后无限 retriable** → 增加连续 2 次相同原因熔断逻辑

## [2.8.3] - 2026-07-22

### Removed — P2 架构收敛：死代码清理

#### 批次 1：dynamic_workflow 包整体删除（12 个模块，~12000 行）
- **零外部引用确认**：12 个模块中 11 个零外部引用，1 个（semantic_embedder）仅 2 个引用方
- `semantic_embedder.py` 移至 `scripts/` 根目录（保留 TFIDF/Hashing/SentenceTransformer 确定性工具）
- 更新 `role_matcher.py` + `goal_orchestrator.py` 的 import 路径
- 删除 `scripts/dynamic_workflow/` 目录（含 12 个 .py 文件）
- 删除 14 个对应测试文件 + 1 个测试脚本
- 更新 `run_all.sh` / `coverage_analysis.py` 移除 dynamic_workflow 引用

#### 批次 2：低耦合 cybernetics 文件归档（5 个文件，~3000 行）
- `cybernetics_integration.py`（3 引用）— 删除，更新 `update_docs.py` 文档引用
- `cybernetics_bridge.py`（2 引用）— 删除，更新 `dispatch/legacy.py` 移除桥接代码
- `hierarchical_control.py`（2 引用）— 删除（引用方均已被删除）
- `context_fingerprint_integration.py`（1 引用）— 删除（引用方为已删除的测试）
- `agent_loop_controller_v2.py`（2 引用）— 删除（引用方均为已归档的 cybernetics 文件）
- 删除 3 个对应测试文件
- **保留** 3 个高耦合核心：`performance_fingerprint.py`(23引用) / `feedback_control_loop.py`(11引用) / `guard_coordinator.py`(5引用)

#### 批次 3：SKILL.md 更新 + facade.py 确认 + v1 deprecation
- Dynamic Workflows 章节：标注脚本层已归档，6 大模式作为提示词层概念保留
- Cybernetics 章节：标注 4 个低耦合文件已归档，3 个核心组件保留并标注引用数
- **facade.py 确认为唯一门面**：SKILL.md 新增「核心入口与门面」章节，明确入口层次（v1 弃用 → v2 薄壳 → facade 门面 → legacy/dispatcher 内部层）
- **v1 入口 deprecation**：`trae_agent_dispatch.py` 添加 `DeprecationWarning`（仅 `__main__` 时输出到 stderr），docstring 标注弃用状态，引导使用 v2 或 facade
- **SKILL.md CLI 示例更新**：11 处 `scripts/trae_agent_dispatch.py` → `scripts/trae_agent_dispatch_v2.py`（避免触发 deprecation warning）
- **skill-manifest.yaml 角色残留修复**：3 处 `role: tester` → `role: test-expert`（standard-dev-workflow 的 testing 步骤 + bug-fix-workflow 的 analysis/verification 步骤）

### Tested

- 198 个核心测试通过（test_semantic_embedder / test_workflow_engine_v2 / test_v3_dispatcher / test_v3_plugins / test_phase18_cli / test_phase18_handlers / test_claude_code_subagent_adapter_prompt / test_review_handler / test_workflow_loop_controller）
- 6 个预先存在的失败（test_v3_plugins::test_mutex_with）与本次改动无关
- sync_manifests.py 校验通过
- 全部修改的 Python 文件语法编译通过

### Impact

| 指标 | v2.8.2 | v2.8.3 | 变化 |
|------|--------|--------|------|
| dynamic_workflow 模块 | 12 个 | 0（semantic_embedder 移出） | -12 |
| cybernetics 根目录文件 | 7 个 | 3 个 | -4 |
| 循环控制器 | 6+ | 5 | -1 |
| 删除代码行 | — | ~15000 行 | -15k |
| 删除测试文件 | — | 17 个 | -17 |
| 零引用确认 | — | 17/17 文件 | 100% |

## [2.8.2] - 2026-07-22

### Fixed — 一致性修复（P0）

#### 版本统一
- 三份 manifest（skill-manifest.yaml / skills-index.json / claude-code-skill.json）版本从 2.7.0/2.7.1 统一到 2.8.1，通过 sync_manifests.py 校验

#### 角色名统一
- 全局 5 角色 ID 标准化：`architect` / `product-manager` / `test-expert` / `solo-coder` / `ui-designer`
- `tester` → `test-expert`（role_matcher.py / skill_registry.py / claude_code_subagent_adapter.py / workflow_engine_v2.py / cli/parser.py / dual_layer_context_manager.py / test_ai_components.py / test_workflow_engine_v2.py）
- 删除 `devops` 角色（半成品：无 prompt、无文档、无模板）
- 删除 `developer` capability（与 `solo-coder` schema 完全重复）
- workflow_engine_v2.py 删除 devops 部署步骤

#### 死链修复
- SKILL.md 5 个角色 prompt 死链修复：`docs/roles/*/prompt.md` → 指向实际存在的模板文件
- claude-code-skill.json lazy_load 5 个死链同步修复

#### Workflow 定义修复
- definitions.json：补 bug-fix-workflow（manifest 有 3 个，definitions.json 原 only 2 个）
- definitions.json：`tester` → `test-expert`
- definitions.json：`${spec}` / `${test_plan}` / `${test_command}` 变量声明为 workflow-level variables（原为断裂引用）

#### Simulation 诚实化
- `claude_code_subagent_adapter.py`：`_simulate_subagent_call` → `_fallback_no_subagent`，返回 `success=False` + `platform: 'none'`（不再伪装成功）
- claude-code-skill.json：`fallback_mode: simulation` → `fallback_mode: error`
- SKILL.md / skill-manifest.yaml：更新降级模式说明

### Added — S/M/L 任务规模分级门禁（P1）

- SKILL.md 新增「任务规模分级门禁」章节（v2.8.2 — 第一道路由）
- S/M/L 三档分流：S=单角色直达、M=三阶段迷你流、L=八阶段完整 Loop
- 含判定信号清单和禁止行为规则
- 与拓扑路由的关系明确：S/M/L 是第一道路由（流程规模），角色匹配是第二道路由（谁执行），拓扑路由是第三道路由（执行模式）

### Removed — 过期文档清理

- 删除 docs/dev/ 下 27 个过期文档（11 个 PHASE*_PLAN.md + 16 个过程性/集成计划文档）
- 删除根目录 5 个过期文档（README_IMPROVEMENT.md / INSTALLATION_COMPLETE.md / wechat_article_v2.4.md / wechat_article_code_map.drawio / wechat_article_code_map.md）

### Tested

- sync_manifests.py 校验通过（三份 manifest 一致）
- 64 个直接相关测试通过（test_workflow_engine_v2 / test_workflow_loop_controller / test_claude_code_subagent_adapter_prompt / test_phase18_cli / test_review_handler）
- 2 个预先存在的失败（test_goal_orchestrator_integration embedder 导入问题 / test_v3_plugins mutex 配置问题）与本次改动无关
- 全部 JSON 文件语法校验通过
- 全部修改的 Python 文件语法编译通过

## [2.8.1] - 2026-07-21

### Added

#### v2.8.1 — 八阶段整体构建为 Loop + 回退策略

在 v2.8.0 阶段 8（文档对照代码审查）基础上，将整个八阶段工作流构建为一个完整的循环（WorkflowLoopController），支持审查失败后回退到对应阶段修复，避免一次性失败导致整个流程作废。

##### 新增核心组件

- ✅ `scripts/workflow_loop_controller.py`（WorkflowLoopController + RollbackStrategy）
  - 八阶段工作流整体构建为一个 loop，支持审查失败后回退修复
  - `WorkflowStage` 枚举（8 个阶段，附带 stage_number/role_name/output_name 属性）
  - `to_stage_kind()` 方法建立与 Ralph StageKind 的映射（详见设计文档 §10.3.2）
  - 回退策略：D1/D2/D4/D5/D6 → DEVELOPMENT，D3 → TEST_VERIFICATION
  - 最大迭代次数限制（默认 3 次，可配置）
  - 累计上下文跨迭代传递（`_accumulated_artifacts`）
  - 工作流执行结果摘要生成（`WorkflowRunResult.summary()`）
  - run() 方法拆分为 4 个子方法（`_calculate_start_stage_idx` / `_execute_stages` / `_execute_single_stage` / `_handle_review_result` / `_build_run_result`），单一职责
- ✅ `scripts/run_workflow_loop.py`（CLI 入口脚本）
  - `DefaultStageExecutor` 提供真实阶段执行逻辑（禁 mock）
  - 阶段 1-5：检查文档是否存在
  - 阶段 6：假设代码已就绪
  - 阶段 7：真实执行测试命令（subprocess.run + 多格式解析）
  - 阶段 8：调用真实 ReviewHandler
  - 支持 `--project-root` / `--max-iterations` / `--prd-path` / `--architecture-path` / `--spec-path` / `--test-plan-path` / `--test-command` / `--verbose` 参数
  - 输出 JSON 格式结果文件（`<project>-WORKFLOW-LOOP-RESULT.json`）

##### 文档更新

- 设计文档 `docs/dev/DOC_CODE_REVIEW_STAGE.md` 追加 §10 八阶段循环章节
  - §10.1 设计目标 / §10.2 WorkflowLoopController 定位 / §10.3 核心数据结构
  - §10.4 RollbackStrategy 回退策略 / §10.5 最大迭代次数限制
  - §10.6 累计上下文传递机制 / §10.7 工作流执行流程
  - §10.8 与 autonomous 模块的集成路径 / §10.9 风险与缓解 / §10.10 测试策略
- SKILL.md：新增"八阶段整体构建为一个 Loop"章节，含回退映射表、CLI 示例、与 RalphLoopController 的关系
- workflows/definitions.json：`doc-code-review` 步骤输入字段统一为 `_path` 后缀，新增 `spec_path` 和 `test_command`

##### 测试

- `scripts/tests/test_workflow_loop_controller.py`：从 12 个测试扩展到 16 个
  - 新增 W8: 累计上下文跨迭代传递测试
  - 新增 W9: 端到端集成测试（真实 ReviewHandler + 真实项目目录）
  - 新增 W10: D3 测试失败回退到 TEST_VERIFICATION 完整流程
  - 新增 W11: WorkflowStage.to_stage_kind() 映射方法
- `scripts/tests/test_review_handler.py`：移除 MagicMock，改用真实 `StageKind.REVIEW`
- 全部 31 个测试通过（27 原有 + 4 新增）

##### 架构师审查

- 已通过架构师 review，识别并修复 3 个 P0 阻断问题：
  - P0-1: 设计文档缺失 §10 八阶段循环章节 → 已补充
  - P0-2: test_review_handler.py 使用 MagicMock → 已替换为真实枚举
  - P0-3: WorkflowLoopController 是孤立组件 → 已提供 CLI 入口脚本
- 修复 6 个 P1 重要问题：文档同步 / 字段统一 / 映射方法 / 子方法抽取 / 测试补充

## [2.8.0] - 2026-07-21

### Added

#### v2.8 — 八阶段工作流新增阶段 8：文档对照代码审查

在七阶段标准工作流末尾追加"文档对照代码审查"阶段，确保开发完成后逐项对照文档检查功能完成情况、集成情况、测试正确性，杜绝"文档写了但代码没实现"的遗漏。

##### 新增核心组件

- ✅ `scripts/doc_code_consistency_checker.py`（DocCodeConsistencyChecker）
  - 六大维度检查：功能完成度(D1) / 集成完整性(D2) / 测试正确性(D3) / 验收标准满足(D4) / TODO-FIXME清零(D5) / 文档意图遵从(D6)
  - 文档解析：支持 PRD / SPEC / 架构文档 / 测试计划的 Markdown 表格解析
  - 代码扫描：Python / JavaScript / TypeScript / Java / Go / Rust 多语言函数/类/import 扫描
  - 测试执行：真实执行测试命令，解析 passed/failed/skipped
  - 报告生成：结构化 Markdown 审查报告
- ✅ `scripts/autonomous/handlers/review_handler.py`（ReviewHandler）
  - 继承 StageHandler，作为 Ralph 循环可选第 5 阶段
  - 审查通过 → success，审查不通过 → retriable，检查器异常 → fatal
- ✅ `scripts/autonomous/loop_controller.py` StageKind 新增 REVIEW 枚举值
- ✅ `docs/dev/DOC_CODE_REVIEW_STAGE.md` 设计文档
- ✅ `docs/spec/role-prompts/doc-code-review.md` Prompt 模板
- ✅ `docs/roles/doc-code-review/DOC_CODE_REVIEW_TEMPLATE.md` 报告模板

##### 文档更新

- SKILL.md：七阶段 → 八阶段，新增阶段 8 详细说明
- README.md：工作流同步更新，新增六大检查维度说明
- workflows/definitions.json：standard-dev-workflow 新增 doc-code-review 步骤

##### 测试

- `scripts/tests/test_doc_code_consistency_checker.py`：12 个测试用例覆盖全部六大维度
- `scripts/tests/test_review_handler.py`：4 个测试用例覆盖 handler 成功/失败/异常路径
- `scripts/tests/scripts/run_doc_review_tests.sh`：测试脚本

## [2.7.1] - 2026-07-18

### Changed

#### v2.7.1 — AI 诚实化 + 真实语义匹配 + 双宿主同步 + v1 死代码清算

本修订版本针对多角色团队 review 发现的问题进行集中修复：消除脚本层模拟 AI 响应、
升级语义匹配为真实向量实现、统一双宿主（Trae / Claude Code）清单、清算 v1 遗留死代码。

##### AI 诚实降级（消除模拟响应）

- ✅ `scripts/ai_assistant.py` 改造为诚实降级
  - `_call_trae_ai`：脚本进程无法访问宿主 IDE AI API，返回明确"不可用"标注（`unavailable: true`），不再伪装 AI 输出
  - `_call_custom_ai`：实现真实 HTTP 调用（urllib 标准库，OpenAI 兼容端点）
  - `_call_local_ai`：实现真实本地模型加载（transformers 软依赖），未安装时明确报错
- ✅ `scripts/ai_semantic_matcher.py` 移除模拟路径
  - 删除 `_simulate_ai_response` 方法（此前生成伪装成 AI 响应的模拟 JSON）
  - 无 AI 客户端时抛出 `RuntimeError`，由上层 `match()` 捕获后降级到 `_fallback_match()` 确定性关键词匹配

##### 真实语义匹配升级

- ✅ `scripts/role_matcher.py` `_semantic_match` 从 Jaccard 关键词重叠升级为本地 embedder 向量余弦相似度（TFIDF/Hashing，确定性可复现），embedder 计算失败时降级到关键词重叠
- ✅ `scripts/goal_orchestrator.py` embedder 三级降级链
  - 第一层 SentenceTransformer（高精度语义，需网络/本地缓存）
  - 第二层 TFIDF（纯本地，不联网）
  - 第三层 HashingEmbedder（纯本地，零外部依赖）
  - 扩展异常捕获范围（SentenceTransformer 构造器在无网络时抛网络异常而非 ImportError）

##### 双宿主清单同步

- ✅ 新增 `scripts/sync_manifests.py` CI 校验脚本
  - 校验三份清单（`skill-manifest.yaml` / `skills-index.json` / `claude-code-skill.json`）的 name / version 一致性
  - 防止双宿主能力漂移（此前 claude-code-skill.json 停留在 2.4.1）
- ✅ 三份清单统一 `name=multi-agent-team`、`version=2.7.1`

##### Claude Code 真实 SubAgent 定义

- ✅ 新增 `.claude/agents/` 5 个角色定义文件（architect / product-manager / test-expert / solo-coder / ui-designer）
  - Claude Code 侧可通过宿主 Task 机制调用真实并行子代理，替代脚本模拟
- ✅ `install-claude-code.sh` 新增 SubAgent 定义安装逻辑（复制到 `~/.claude/agents/`）

##### v1 死代码清算

- ✅ 删除 `scripts/workflow_engine.py`（v1，588 行）、`scripts/code_map_generator.py`（v1，297 行）、`scripts/test_v2_components.py`（310 行）
- ✅ `scripts/dispatch/legacy.py` 切换到 `WorkflowEngineV2`（别名保持 `WorkflowEngine`，业务代码零改动）
- ✅ 同步更新 `quick-install.sh` / `claude-code-skill.json` / `skills-index.json` / `trae-agent` / `INSTALLATION_COMPLETE.md` / `CONFIGURATION.md` 中的 v1 文件引用
- ✅ `trae_agent_dispatch.py` 作为向后兼容命令行入口（薄壳包装器）保留

##### 依赖显式化

- ✅ `requirements.txt` 重写：核心运行时零第三方硬依赖（纯标准库），软依赖（playwright / Pillow / sentence-transformers）显式标注为可选并说明降级行为

### Fixed

- ✅ `scripts/tests/test_v3_integration.py` 插件数量断言 6 → 7（LoopEngineeringPlugin 新增后未同步）
- ✅ `claude-code-skill.json` JSON 语法错误（对象缺闭合 `}`、`]` 重复）

### 测试

- 单元测试：193 通过 / 22 跳过 / 0 失败（run_tests.py 24/24、workflow_engine_v2 等核心 60/60、v3_integration 23/23、semantic_embedder+ponytail+phase18 86 通过 22 跳过）
- `sync_manifests.py --report` 三清单一致校验通过

## [2.7.0] - 2026-06-20

### Added

#### v2.7 — UI/UX 巡检分析 + 视觉回归测试

本版本新增 2 个独立的 E2E 视觉质量保障脚本，作为多智能体团队在「UI 设计师」与
「测试专家」角色下执行前端质量门禁的标准工具。脚本以可独立 import / CLI 调用的
形式提供，可与现有 Autonomous 模式、E2E 测试套件无缝集成。

##### 新增 `scripts/uiux_analyzer.py`（UI/UX 巡检分析器）

- ✅ 4 大检测维度
  - **可访问性 (A11y)**: WCAG AA 对比度、img alt、form label、语义化标签、键盘可达
  - **交互质量**: 按钮最小尺寸（Apple HIG ≥44px）、焦点可见性、加载反馈
  - **布局与响应式**: 元素重叠、文字截断（text-overflow）、视口溢出
  - **UX 反模式**: 强制注册、破坏性操作无确认、表单无校验
- ✅ 关键类：`UIUXIssue`（dataclass）/ `UIUXAnalyzer`（核心）
- ✅ Playwright 单次综合探针 JS（一次 evaluate 取齐所有数据，避免多次往返）
- ✅ WCAG AA 阈值常量（`CONTRAST_AA_NORMAL=4.5` / `CONTRAST_AA_LARGE=3.0`）
- ✅ 失败安全：任一检查项异常不影响其他检查（try/except 隔离）
- ✅ 设计原则：标准库优先（纯 Playwright JS 注入 + 规则引擎，零三方依赖）

##### 新增 `scripts/visual_regression.py`（视觉回归 + 显示完整性）

- ✅ 3 大检测维度
  - **视觉回归**: 像素级 Diff（PIL `ImageChops`）+ 简化 SSIM 区域级 Diff
  - **数据显示不全检测**: 文本截断、元素溢出视口、图片未加载、骨架屏 >10s、长表格横向滚动
  - **显示错误检测**: 红色文字/背景（HSV 检测）、错误关键词、Ant Design / Arco Design / Element UI 错误 Toast、浏览器原生 dialog
- ✅ 关键类：`ChangedRegion` / `DiffResult` / `VisualRegressionChecker`
- ✅ 软依赖：Pillow（必需），numpy（可选，更好的 SSIM）
- ✅ 阈值可配：默认 `pixel_diff_ratio < 1%`
- ✅ YAGNI：只实现最常用的 3 类检测，不造大而全框架

##### 角色集成

- **UI 设计师**: 在交付前端稿前使用 `uiux_analyzer.audit(page)` 验证设计质量
- **测试专家**: 在 E2E 套件中调用 `VisualRegressionChecker` 进行像素级断言
- **Solo Coder**: 作为 PR 门禁脚本集成到 CI（pyppeteer/playwright + 本脚本）

##### 文档更新

- ✅ `SKILL.md` 新增「UI/UX 巡检与视觉回归」章节
- ✅ `README.md` 顶部公告升级至 v2.7
- ✅ `skills-index.json` 版本号 2.6.0 → 2.7.0，新增 2 个 feature 描述
- ✅ `IMPLEMENTATION_STATUS.md` 新增 v2.7 实现状态章节

## [2.6.0] - 2026-06-15

### Added

#### v2.6 — Ponytail 决策梯 + Autonomous Mode + 插件热加载

本版本围绕「让 Agent 自主编排更可控、更可观测」演进，落地三大能力：
Ponytail 决策梯（少写多余代码）、Autonomous Mode（Ralph 风格自主编排）、
插件热加载（V3 插件架构）。共新增 30+ 模块文件、400+ 测试用例，
配套完整文档与单元/集成测试。

##### Phase 19: Ponytail 决策梯完整实现

- ✅ 新增 `scripts/ponytail/` 模块（4 个文件）
  - `ruleset.py` — 6 步决策梯 + 16 条不可简化红线 + 三种模式（lite/full/ultra）
  - `mode_tracker.py` — 线程安全模式追踪（Lock + 原子文件操作）
  - `debt_collector.py` — 债务台账扫描（自动扫描 `# ponytail:` 标记 + 上限关键词识别）
  - `requirement_tracer.py` — 需求追踪（[REQ-XXX] 标记 + 覆盖率检查）
- ✅ 修改 4 个 handler（dev/fix/plan/verify）
  - 修复 DevHandler/FixHandler 无限递归（直接调用 `_dispatch_via_claude_code`）
  - 参数化决策梯注入（线程安全，100 并发测试通过）
  - ULTRA 模式在 autonomous 场景自动降级为 FULL
  - VerifyHandler 新增债务检测 + 空 diff 检测
- ✅ 修改 `claude_code_subagent_adapter.py`
  - 参数化决策梯注入（`context['ponytail_decision_ladder']` 优先，兜底 `context['_ponytail_engine']`）
  - 修复 `json.dumps` 非 serializable 对象崩溃（添加 `default=str`）
- ✅ 修改 `karpathy_principle_enforcer.py`
  - 扩展红线检测模式（YAGNI 违规 + 新依赖 + standalone pass + `unittest.mock`）
  - 新增 `file_whitelist` 和 `context_whitelist` 避免误报
- ✅ 新增 10 个测试文件，98 个测试用例
- ✅ 新增/更新文档：`PONYTAIL_GUIDE.md` / `CONSTITUTION.md` 更新 / `coder-code-analysis.md` 更新

##### Phase 18: Autonomous Mode（Ralph 风格自主编排）

- ✅ 新增 `scripts/autonomous/` 模块（10 个文件）
  - `loop_controller.py` — Ralph 循环控制器（4 阶段 plan→dev→verify→fix）
  - `run_state.py` — 运行状态持久化（SHA256 校验 + `ResumeContext`）
  - `notes_memory.py` — Notes 跨轮记忆
  - `git_driver.py` — Git 驱动（自动 commit + 分支管理）
  - `sleep_guard.py` — 防休眠守护
  - `smart_confirmation.py` — 智能确认（三态：auto-approve / ask-user / fail-closed）
  - `auto_skill_loader.py` — Auto-skill 加载器
  - `dispatcher_adapter.py` — Dispatcher 适配器
  - `config_loader.py` — 配置加载器
  - `handlers/` — 4 个阶段处理器（plan/dev/verify/fix）
- ✅ 新增 `scripts/plugins/autonomous.py` — Ralph Autonomous 插件
- ✅ 新增 17 个 CLI flag（`--auto-mode` / `--auto-goal` / `--auto-max-iterations` 等）
- ✅ 新增 259 个测试用例
- ✅ 新增文档：`AUTONOMOUS_MODE_GUIDE.md` / `PHASE18_PLAN.md`

##### Phase 17: 插件热加载（V3 插件架构）

- ✅ 新增 `scripts/dispatcher/` 模块（7 个文件）
  - `goal_dispatcher.py` — Goal 调度器（DAG 依赖图）
  - `plugin_context.py` — 插件上下文
  - `drop_in_loader.py` — Drop-in 目录加载器
  - `hot_reload_watcher.py` — 热加载监视器
  - `reload_guard.py` — 重载守护（Condition 替代 Event）
  - `middleware.py` — 中间件
  - `dispatch_result.py` / `errors.py` — 结果和错误定义
- ✅ 新增 V3 插件实现（`scripts/plugins/`）
  - `multi_goal.py` — 多 Goal 编排插件
  - `graph.py` — 图编排插件
  - `loop.py` — 循环编排插件
  - `resume.py` — 断点续跑插件
  - `cancel.py` — 取消插件
- ✅ 3 种加载路径（Drop-in 目录 / Hot Register API / `HotReloadWatcher`）
- ✅ 新增 102+ 个测试用例
- ✅ 新增文档：`PHASE17_PLAN.md`

### Fixed

#### Phase 19 关键缺陷修复

- ✅ 修复 DevHandler / FixHandler 在自主编排场景下的无限递归
- ✅ 修复 `claude_code_subagent_adapter.py` 中 `json.dumps` 对非 serializable 对象崩溃
- ✅ 修复 Karpathy 原则执行器在测试场景下的误报（白名单机制）

## [2.5.0] - 2026-05-20

### Added

#### v2.5 — Cybernetics 工程控制论增强

本版本引入工程控制论（Cybernetics）思想，构建「感知-决策-执行-反馈」闭环，
并落地 Dynamic Workflows v1.7 动态工作流编排能力，使多 Agent 协作具备
自适应、自优化、自纠偏能力。

##### Cybernetics 核心组件

- ✅ 新增 6 个核心组件（`scripts/` 根目录）
  - `feedback_control_loop.py` — 反馈控制环（感知-决策-执行-反馈）
  - `performance_fingerprint.py` — 性能画像（执行案例记录 + 相似案例检索）
  - `guard_coordinator.py` — 守护协调器（执行前验证 + 异常检测）
  - `hierarchical_control.py` — 分层控制（战略层 / 战术层 / 执行层）
  - `cybernetics_integration.py` — Cybernetics 集成入口
  - `context_fingerprint_integration.py` — 上下文画像集成
- ✅ 三环控制模型：战略层（长期目标）+ 战术层（中期策略）+ 执行层（短期动作）
- ✅ 新增 70+ 个测试用例
- ✅ 新增文档：`CYBERNETICS_ANALYSIS.md`

##### Dynamic Workflows v1.7

- ✅ 新增 `scripts/dynamic_workflow/` 模块（12 个文件）
  - 6 大模式：classifier-dispatch / fan-out-aggregate / adversarial-verify / generate-filter / tournament / loop-until-done
  - 12 个实现模块：`pattern_composer` / `pattern_executor` / `pattern_tier_resolver` / `subagent_sandbox` / `model_router` / `token_budget_guard` / `semantic_embedder` / `skill_injector` / `interruption_recovery` / `workflow_step_adapter` / `worktree_manager` / `guard`
- ✅ 新增文档：`DYNAMIC_WORKFLOWS_INTEGRATION.md`

## [2.4.1] - 2026-05-03
### Changed
#### 文档全面更新
- ✅ 更新 `IMPLEMENTATION_STATUS.md`：版本号升级至 2.4.1，新增 v2.4 组件列表和完整文件清单
- ✅ 更新 `skill-manifest.yaml`：版本号升级至 2.4.1，新增 v2.4 变更记录，新增 ai_capabilities 扩展
- ✅ 更新 `CHANGELOG.md`：添加 v2.4.0 和 v2.4.1 变更记录
- ✅ 更新 `README_EN.md`：添加 v2.4 Karpathy 原则更新标题
#### 文档对齐
- ✅ 所有文档版本号统一为 2.4.1
- ✅ 技能清单描述更新，反映最新功能
- ✅ 实现状态文档完全重写，按版本分组展示组件
## [2.4.0] - 2026-04-14
### Added
#### Karpathy 四大核心原则强制执行 (v2.4)
##### 原则执行检查器
- ✅ `KarpathyPrincipleEnforcer` 类 (`scripts/karpathy_principle_enforcer.py`)
- ✅ `PrincipleType` 枚举：THINK_BEFORE_CODING / SIMPLICITY_FIRST / SURGICAL_CHANGES / GOAL_DRIVEN
- ✅ `ViolationSeverity` 枚举：CRITICAL / HIGH / MEDIUM / LOW / INFO (5 级严重度)
- ✅ `PrincipleViolation` 数据类：原则违规记录
- ✅ `VerificationCheckpoint` 数据类：验证检查点
- ✅ `KarpathyEnforcementReport` 数据类：执行报告（含 to_dict 序列化）
- ✅ 原则合规性检查功能
- ✅ 违规检测与提醒功能
- ✅ 验证检查点管理功能
- ✅ 执行报告生成功能（JSON 导出）
##### Karpathy 原则融入角色 Prompt
- ✅ SKILL.md 新增 Karpathy 四大核心原则整体行为准则
- ✅ 每个角色（架构师、产品经理、测试专家、独立开发者、UI 设计师）新增 Karpathy 原则应用表
- ✅ 新增"Karpathy 原则应用速查"表
- ✅ 新增"LLM 常见问题"对照表
- ✅ 新增"通用行为准则"（所有角色必须遵守）
##### 四大原则详细说明
- ✅ Think Before Coding（三思而后行）：明确假设、呈现权衡、遇到不清就问
- ✅ Simplicity First（简单优先）：最小代码、无 speculative features、无过度抽象
- ✅ Surgical Changes（精准修改）：只改需要的、不改无关的、保持风格一致
- ✅ Goal-Driven Execution（目标驱动）：定义成功标准、验证检查点、迭代直到完成
#### Claude Code SubAgent 适配器
##### 跨平台适配
- ✅ `ClaudeCodeSubAgentAdapter` 类 (`scripts/claude_code_subagent_adapter.py`)
- ✅ 自动平台检测：`claude_code` / `trae` / `unknown`
- ✅ 统一 `invoke_agent()` 接口
- ✅ Claude Code 平台：通过 claude subagent 命令调用
- ✅ Trae IDE 平台：通过原有机制调用
- ✅ 未知平台：通用回退方法
- ✅ 环境变量检测：`CLAUDE_CODE_ENV` / `ANTHROPIC_ENV` / `TRAE_ENV` / `TRAE_AGENT_PATH`
## [2.3.0] - 2026-03-28
### Added
#### 代码地图增强 (v2.3)
##### 多项目 Workspace 支持
- ✅ 支持一个 workspace 包含多个项目的场景
- ✅ 自动识别项目所属 workspace
- ✅ 明确项目标识（项目名称、工作空间、相对路径）
##### 多角色代码走读
- ✅ `MultiRoleCodeWalkthrough` 类 (`scripts/multi_role_code_walkthrough.py`)
- ✅ 支持 5 种角色分析：架构师、产品经理、独立开发者、UI 设计师、测试专家
- ✅ 角色专属代码分析 prompt 模板
- ✅ 文档对齐机制，合并多角色分析结果
- ✅ 生成统一代码地图
- ✅ 生成代码走读审查报告 (`CodeReviewReportGenerator` 类)
##### 真正的多角色协作分析器 (v2.3)
- ✅ `MultiRoleCollaborativeAnalyzer` 类 (`scripts/multi_role_collaborative_analyzer.py`)
- ✅ 集成 Trae Agent 调度系统 (`trae_agent_dispatch_v2.py`)
- ✅ 每个角色使用专属 prompt 模板进行真实分析
- ✅ 真正的多角色协作：架构师、产品经理、独立开发者、UI 设计师、测试专家
- ✅ 支持并行/串行执行各角色分析任务
- ✅ 提取各角色的关键发现和建议
##### 角色专属 Prompt 模板
- ✅ 架构师代码分析模板 (`docs/spec/role-prompts/architect-code-analysis.md`)
- ✅ 产品经理代码分析模板 (`docs/spec/role-prompts/pm-code-analysis.md`)
- ✅ 独立开发者代码分析模板 (`docs/spec/role-prompts/coder-code-analysis.md`)
- ✅ UI 设计师代码分析模板 (`docs/spec/role-prompts/ui-code-analysis.md`)
- ✅ 测试专家代码分析模板 (`docs/spec/role-prompts/test-code-analysis.md`)
##### 代码地图生成器 v2.1
- ✅ `CodeMapGenerator` 类增强 (`scripts/code_map_generator_v2.py`)
- ✅ 支持多语言分析：Python, Java, JavaScript/TypeScript, Go 等
- ✅ 架构分层检测（API Layer, Service Layer, Data Layer 等）
- ✅ 函数和类详细信息提取
- ✅ 调用关系追踪
- ✅ 复杂度评估
- ✅ md 格式输出
##### 代码与文档分离 (v2.3)
- ✅ 代码地图仅保留核心结构内容（项目概览、架构视图、代码结构、多角色视角、分析共识）
- ✅ 审查报告包含完整风险评估和建议
- ✅ 移除代码地图中的"建议"和"快速参考"章节
##### 3D 代码地图可视化 (v2.3)
- ✅ `docs/code-map-visualizer.html`
- ✅ Three.js 3D 引擎，支持拖拽旋转、滚轮缩放
- ✅ 节点类型区分：模块（蓝色）、类（紫色）、函数（绿色）
- ✅ 调用关系可视化：节点间连线表示调用关系
- ✅ 动态流动效果：边使用虚线动画 + 流动粒子
- ✅ 深色/浅色主题一键切换
- ✅ 点击展开/折叠、双击高亮调用链路、搜索过滤
##### 任务可视化页面 (v2.3)
- ✅ `docs/task-visualizer.html`
- ✅ 概览统计面板：总任务数、待开始、进行中、已完成、被阻塞
- ✅ 角色任务卡片：任务列表、状态、进度
- ✅ 任务依赖关系和阻塞关系展示
- ✅ 任务交接记录时间线
- ✅ Canvas 绘制协同关系图
- ✅ 定时刷新机制（30秒自动刷新）
- ✅ 任务详情弹窗
##### 文档与代码一致性检查 (v2.3)
- ✅ `ProjectScanner` 支持文档文件扫描 (.md, .txt, .rst, .adoc)
- ✅ `CodeReviewReportGenerator` 新增 `_generate_doc_code_consistency_check()` 方法
- ✅ 文档覆盖概览统计
- ✅ 检查清单表格（README、API、配置、架构文档）
- ✅ 差异分析按严重程度分级（严重/中等/轻微）
## [2.2.0] - 2026-03-21
### Added
#### 长程 Agent 支持 (基于 Anthropic《Effective Harnesses for Long-Running Agents》)
##### Checkpoint 检查点管理器
- ✅ `CheckpointManager` 类 (`scripts/checkpoint_manager.py`)
  - 定期保存任务状态（像人类工程师 git commit）
  - 支持从任意断点恢复
  - 数据完整性校验（SHA256 哈希）
  - 自动过期清理机制
  - 交接文档生成
##### Handoff 交接班协议
- ✅ `HandoffDocument` 类
  - 标准化交接文档（JSON + Markdown）
  - 交接原因记录和信心度评估
  - 重要注意事项传递
  - 支持双智能体架构（Planner + Executor）
  - 交接历史追踪
##### TaskList 任务清单管理器
- ✅ `TaskListManager` 类 (`scripts/task_list_manager.py`)
  - 4 级优先级（CRITICAL/HIGH/MEDIUM/LOW）
  - 5 种状态（PENDING/IN_PROGRESS/COMPLETED/BLOCKED/CANCELLED）
  - 依赖关系管理（is_ready 检查）
  - 进度跟踪和工时估算
  - Markdown 导出功能
##### WorkflowEngineV2 增强版
- ✅ `WorkflowEngineV2` 类 (`scripts/workflow_engine_v2.py`)
  - 集成 Checkpoint + TaskList + Handoff
  - 智能任务拆分（基于关键词识别）
  - 定期自动保存检查点
  - 支持 Agent 交接班
  - 断点恢复机制
##### 完整测试套件
- ✅ 24 个测试全部通过
  - `TestCheckpointManager`: 7 个测试
  - `TestHandoffDocument`: 3 个测试
  - `TestTaskListManager`: 9 个测试
  - `TestWorkflowEngineV2`: 5 个测试
### Fixed
#### 角色匹配问题
- ✅ 修复角色匹配总是匹配到 UI 设计师的问题
  - 优化关键词区分度
  - 添加 AI 语义匹配
  - 增强优先级权重
#### JSON 序列化问题
- ✅ 修复枚举类型 JSON 序列化错误
  - Checkpoint 状态枚举转换
  - TaskList 状态和优先级枚举转换
  - WorkflowEngine 步骤状态枚举转换
  - 数据完整性哈希校验
## [1.3.0] - 2026-03-12
### Fixed
#### Agent Loop 思考循环问题
- ✅ 修复 `is_all_tasks_completed()` 方法
  - 优先从任务文件中检查实际完成状态
  - 遍历所有测试用例，检查是否有待实现的标记
  - 出错时使用进度文件作为备选方案
- ✅ 优化 `agent_loop_controller.py` 循环逻辑
  - 新增连续无进展计数器（防止无限循环）
  - 连续 3 次迭代无进展时强制退出
  - 增加任务执行成功/失败的计数器管理
  - 确保循环在各种情况下都能正确退出
- ✅ 改进任务状态同步机制
  - 以任务文件状态为准，确保同步
  - 正确处理已完成和待完成任务的列表更新
  - 避免状态冲突和不一致
- ✅ 修复路径问题
  - 从 skill 目录导入检查器脚本
  - 使用相对路径定位进度文件
## [1.2.0] - 2026-03-11
### Added
#### 规范驱动开发功能
- ✅ 完整的规范工具链（scripts/spec_tools.py）
  - `spec_tools.py init` - 初始化规范环境
  - `spec_tools.py analyze` - 分析规范完整性和一致性
  - `spec_tools.py update` - 更新规范文档
  - `spec_tools.py validate` - 验证规范执行情况
- ✅ 项目宪法（CONSTITUTION.md）
  - 项目核心价值观和原则
  - 技术栈约束和决策
  - 代码规范和标准
  - 多角色共识制定流程
- ✅ 项目规范（SPEC.md）
  - 需求规范（产品经理负责）
  - 技术规范（架构师负责）
  - 测试规范（测试专家负责）
  - 开发规范（独立开发者负责）
- ✅ 规范分析报告（SPEC_ANALYSIS.md）
  - 规范完整性分析
  - 规范一致性检查
  - 规范可行性评估
  - 改进建议
- ✅ 规范模板库
  - CONSTITUTION_TEMPLATE.md - 项目宪法模板
  - SPEC_TEMPLATE.md - 项目规范模板
  - SPEC_ANALYSIS_TEMPLATE.md - 规范分析模板
  - PROJECT_STRUCTURE_TEMPLATE.md - 项目结构模板
#### 代码地图生成功能
- ✅ 代码地图生成器（scripts/code_map_generator.py）
  - 自动扫描项目代码结构
  - 识别核心组件和入口文件
  - 分析模块依赖关系
  - 生成技术栈统计
- ✅ 输出格式支持
  - JSON 格式（code_map.json）- 机器可读
  - Markdown 格式（PROJECT_STRUCTURE.md）- 人类可读
  - 可视化项目结构树
  - 组件职责说明
- ✅ 代码地图内容
  - 项目概览和统计信息
  - 目录结构树
  - 核心组件和入口文件
  - 模块依赖关系图
  - 技术栈分析（语言、框架、库）
#### 项目理解功能
- ✅ 项目理解生成器（scripts/project_understanding.py）
  - 快速读取项目文档和代码
  - 为各角色生成定制化理解文档
  - 提供项目概览和技术栈分析
  - 作为工作初始化上下文
- ✅ 角色特定理解文档
  - project_understanding.json - 整体项目信息
  - architect_understanding.md - 架构师理解（技术栈、架构模式、部署结构）
  - product_manager_understanding.md - 产品经理理解（功能列表、用户价值、竞品分析）
  - test_expert_understanding.md - 测试专家理解（测试覆盖、质量风险、自动化策略）
  - solo_coder_understanding.md - 独立开发者理解（代码结构、开发规范、技术债务）
- ✅ 项目理解内容
  - 项目概览（名称、描述、目标）
  - 技术栈分析（编程语言、框架、数据库、中间件）
  - 代码结构分析（目录组织、模块划分、代码统计）
  - 文档分析（README、API 文档、设计文档）
  - 依赖分析（package.json、pom.xml、Cargo.toml 等）
  - 角色特定见解和建议
#### 增强版角色 Prompt 系统
- ✅ 规范相关职责
  - 架构师：负责制定和维护技术规范
  - 产品经理：负责制定和维护需求规范
  - 测试专家：负责制定和维护测试规范
  - 独立开发者：负责遵循规范并反馈改进建议
- ✅ 规范驱动开发流程
  - 所有开发工作必须基于已评审的规范
  - 规范变更必须经过多角色共识
  - 规范执行情况必须定期检查
  - 规范文档必须保持最新状态
### Changed
- ✅ 更新 README.md
  - 添加 2026 年 3 月最新更新说明
  - 添加规范驱动开发详细说明
  - 添加代码地图生成详细说明
  - 添加项目理解详细说明
  - 更新功能特性列表
- ✅ 更新 SKILL.md
  - 添加规范驱动开发职责
  - 添加代码地图生成职责
  - 添加项目理解职责
  - 更新角色定义和触发关键词
- ✅ 更新 EXAMPLES.md
  - 添加规范驱动开发示例
  - 添加代码地图生成示例
  - 添加项目理解示例
  - 更新场景示例
### Improved
- ✅ 文档驱动开发流程优化
  - 明确文档依赖关系
  - 添加检查点机制
  - 强化评审流程
  - 完善违规处理
- ✅ 多角色协作机制
  - 优化共识决策流程
  - 改进角色间沟通
  - 增强上下文共享
  - 提升协作效率
## [1.1.0] - 2024-03-05
### Added
#### 新功能/功能变更标准工作流程
- ✅ 七阶段标准工作流程
  - 阶段 1: 需求分析（产品经理）
  - 阶段 2: 架构设计（架构师）
  - 阶段 3: