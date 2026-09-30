# PRD：系统功能与业务流程详说文档（SYSTEM_FUNCTION_DOC）

- **文档编号**: PRD-SFD-001
- **版本**: v1.1（评审稿）
- **日期**: 2026-09-30
- **上游依据**: PRD-SU-001（`docs/dev/SYSTEM_UNDERSTANDING_PRD.md`）、ARCH-SU-001（`docs/dev/SYSTEM_UNDERSTANDING_ARCHITECTURE.md`）、PROMPT-SU-001（`docs/spec/role-prompts/su-llm-backfill.md`）
- **作者角色**: 产品经理（multi-agent-team）
- **状态**: 评审通过（已按 2026-09-30 架构审查修订）

---

## 1. 背景与问题

SU 能力（v2.9.0）已能产出 10 节《系统功能理解文档》（`UNDERSTANDING.md`）：
导航图、数据模型、UI↔API↔表映射、API 面、证据附录等。但它本质是**证据结构化汇编**——
面向"审计者视角"的可追溯清单，而不是面向"新成员/接手者视角"的**叙述性系统功能与
业务流程详说**。

用户诉求：SU 跑完（findings 已回填、UNDERSTANDING.md 已渲染）之后，**调用多专家角色**
（架构师 / 产品经理 / 独立开发者·代码走读 / UI 设计师 / 测试专家），基于：

- 《系统功能理解文档》（UNDERSTANDING.md）+ 机读全量（understanding.json）
- 采集走读过程（snapshots、api_observations、blocked_events、edges）
- 数据库结构（db_tables / db_columns / db_samples / implicit_fk_candidates）
- 页面功能（pages / page_actions）
- API（api_observations / relations）

产出一份**完整的、详细说明的《系统功能与业务流程详说》**，包含：

1. 系统定位与功能全景（干什么的、给谁用、每个模块做什么）
2. 端到端业务流程（用户旅程 → 页面操作 → API 调用 → 数据落表 的完整链路叙述）
3. 数据视角的业务逻辑（表/字段业务语义、状态机、隐式关联揭示的实体关系）
4. 页面功能详说（逐页：用途、元素、动作、权限迹象）
5. 接口契约说明（端点用途、请求/响应形状、被哪些页面消费、写哪些表）
6. 未验证推断、盲区与建议（诚实边界）

## 2. 目标与非目标

### 2.1 目标（G）

| # | 目标 |
|---|------|
| G1 | 在 SU 两阶段工作流之后追加第三阶段"专家详说"，输入仅为 SU 已脱敏产物，零新增凭据面 |
| G2 | 五个专家角色各自视角产出章节草稿，聚合为单一 `SYSTEM-FUNCTION.doc.md` 分段交接文档 |
| G3 | 脚本层提供确定性支撑：章节大纲生成、素材切片（素材包）、证据引用校验、终稿装配 |
| G4 | 详说文档每条业务结论可回溯证据编号（E-n），与 UNDERSTANDING.md 证据附录互链；因 seq 编号仅在单次 render 内稳定，引用契约升级为 (seq, ref) 双键 + run_id/sha256 锚点（见 REQ-SFD-004，2026-09-30 审查修订） |
| G5 | 全程继承 SU 五条安全红线（凭据零明文、只读、脱敏），详说阶段零网络写操作 |

### 2.2 非目标（OUT）

| # | 非目标 |
|---|--------|
| OUT-1 | 脚本层不调用任何 LLM（与 SU 相同：专家分析由宿主 LLM 经 Task 子代理执行） |
| OUT-2 | 不重新爬取、不重连 DB/Redis（详说阶段只消费 SU 已落盘产物） |
| OUT-3 | 不产出可执行代码、不做性能/安全渗透评估 |
| OUT-4 | 不替代 UNDERSTANDING.md（两者并存：一个是证据汇编，一个是叙述详说） |
| OUT-5 | 不引入新第三方依赖 |
| OUT-6 | 不强制五角色全跑：素材缺失的角色章节允许诚实降级为"素材不足"声明 |

## 3. 用户故事

- US-1：作为接手遗留系统的开发者，我要一份能从零读懂系统功能与核心业务流程的详说文档，而不是只有清单。
- US-2：作为架构评审者，我要详说中每条业务结论都能点开对应证据编号，判断结论可信度。
- US-3：作为宿主 LLM 编排者，我要有清晰的专家分工 prompt 与分段文件契约，使五个子代理可并行产出、可校验、可重装配。
- US-4：作为安全负责人，我要确认详说阶段产物中不出现明文凭据。

## 4. 功能需求（REQ-SFD-xxx）

### 模块 A：脚本层确定性支撑

| # | 需求 | 验收标准（AC） |
|---|------|----------------|
| REQ-SFD-001 | **前置校验**：新子命令/参数进入详说流程前，校验 `<out>/<sid>/` 下 UNDERSTANDING.md、understanding.json 存在、findings 非空，且**锚定 run**（= understanding.json `meta.run_id` 对应的 run_meta 行，**非最新行**）status ∈ {completed, interrupted}；findings 双源一致性断言：understanding.json findings 条数 ≠ `stats().findings_total` → exit 2 提示"findings 与状态库不同步，请先 --render-only"（2026-09-30 审查修订）；违例 → exit=2 并给出缺项提示 | AC1 缺文件→exit 2；AC2 findings 为空→exit 2 且提示先完成回填——注意"findings 空数组"是 SFD 侧口径（SU 允许专家撤回全部结论，此时锚定 run 仍可为 completed），提示语须区分"未回填"与"已全部撤回"两种语义（2026-09-30 审查修订）；AC3 校验全程只读（不 acquire_lock 于校验通过前）；AC4 render-only 收口后最新 run 恒为 render run（acquire_lock 对 completed 行总是新建 running 行），属预期行为，锚定规则按 meta.run_id 取行而非取最新行（2026-09-30 审查修订） |
| REQ-SFD-002 | **素材包切片**：从 understanding.json 按专家视角切出五个素材包，写入 `<out>/<sid>/detailed/inputs/`，字段白名单过滤（禁入 fields：storage_state、value_sample 原文等；`config_snapshot` **不在禁入清单**——其随 `meta` 进架构师包，且 meta 为 SU redact 后形态（`document_renderer.render()` 对 understanding.json 整体过 `redact()`，敏感键名值已替换为占位符），口径以 ARCH §2.2.3 为准（2026-09-30 审查修订）），逐包登记来源计数；db_samples 采样值增加 C4 扩展键名+高熵值双因子判据拦截（见 ARCH §2.2.6，2026-09-30 审查修订） | AC1 五包齐备（架构/产品/走读/UI/测试）；AC2 包内不出现 `SensitiveStr` 形态残留（复用 redact 断言）；AC3 每包附 manifest（来源表、条数、生成时间=run started_at、锚定 `run_id`、evidence-index sha256、走读包另记 `db_samples_masked_by=DataMasker`，2026-09-30 审查修订） |
| REQ-SFD-003 | **大纲生成**：渲染《系统功能与业务流程详说》骨架 `SYSTEM_FUNCTION_DOC.md`（固定 8 节大纲 + 每节"待专家回填"占位 + 证据引用规约说明） | AC1 大纲节结构固定且与 PROMPT-SFD 系列文档一致；AC2 Mermaid 图预留块有语言标注（flowchart/sequenceDiagram/stateDiagram-v2）；AC3 幂等：同输入重渲染逐字节稳定（时间戳唯一真相源 = understanding.json `meta.started_at`，禁止从 run_meta 另取——render-only 后最新 run 与锚定 run 可能不同行，见 ARCH §6，2026-09-30 审查修订） |
| REQ-SFD-004 | **分段装配**：`--assemble` 读取 `detailed/sections/*.doc.md` 专家草稿，执行：①文件存在性与节归属校验 ②证据编号引用校验——**(seq, ref) 双键校验 + 派发锚点比对**：装配器读 evidence-index.json 的 `{seq, ref, description}` 条目建立 seq→ref 映射；若派发时锚点（manifest 记录的 `meta.run_id` + evidence-index.json sha256）与当前文件不一致，"同 seq 不同 ref"的引用报"证据编号已漂移"并标 `[漂移引用]`（2026-09-30 审查修订，背景：seq 按单次 render() 累加顺序分配，findings 空→非空重渲染后 seq 整体后移，seq 编号**不具备跨 render 稳定性**，原"编号稳定"表述删除） ③装配进大纲对应节 ④生成终稿 `SYSTEM_FUNCTION_DOC.md` + `detailed/assembly-report.json`（各节来源、引用合法率、降级节清单） | AC1 缺某节草稿→该节渲染"素材不足/角色未完成"声明并记入降级清单，终稿照常产出；AC2 非法 E-n 引用：保留原文但标记 `[未验证引用]` 并计数；漂移引用同样保留原文标 `[漂移引用]` 并计数，且第 7 节强制声明"编号可能漂移，引用需复核"（2026-09-30 审查修订）；AC3 装配幂等 |
| REQ-SFD-005 | **CLI 集成**：`system_understanding.py` 新增 `--detailed-doc`（进入详说编排：校验→素材包→大纲→打印专家派发指引）与 `--assemble`（装配终稿）；两者均要求显式 `--out`/`--system-id`、均不要求 playwright/凭据，退出码沿用 0/2/130 约定 | AC1 与既有 `--render-only` 互斥（同时给→exit 2）；AC2 校验通过后 stdout 输出结构化派发指引（五角色 prompt 文件路径 + 素材包路径 + 输出分段路径，均为生效路径的绝对路径，2026-09-30 审查修订）；AC3 与 `--fresh`/`--resume`/`--skip-llm-phase` 组合 → 显式 exit 2 拒绝；`--detailed-doc` 检测既有终稿（头部 `status: final`）→ 默认 exit 2 提示加 `--force`（2026-09-30 审查修订，详见 ARCH §3.2） |

### 模块 B：专家角色契约（提示词层，宿主 LLM 执行）

| # | 需求 | 验收标准（AC） |
|---|------|----------------|
| REQ-SFD-006 | **架构师视角**（PROMPT-SFD-ARCH）：系统定位、架构分层推断（前端/服务/数据）、模块划分、技术栈指纹解读、跨模块依赖叙述 | 产出 `sections/01-architecture.doc.md`；每条架构推断标注 confidence 与 E-n |
| REQ-SFD-007 | **产品经理视角**（PROMPT-SFD-PM）：用户角色识别、功能全景矩阵（模块×功能×页面）、核心价值流、端到端业务流程叙述（每条流程附 Mermaid flowchart + sequenceDiagram） | 产出 `sections/02-product.doc.md`；流程须引用页面 url_key 链与 API 端点，不得虚构未见过的功能 |
| REQ-SFD-008 | **独立开发者·代码走读视角**（PROMPT-SFD-DEV）：数据库业务语义走读——表/字段业务含义、状态字段枚举语义（基于采样值）、隐式 FK 揭示的实体生命周期、数据流链路（写路径从 API→表推断） | 产出 `sections/04-data-semantics.doc.md`；对 implicit_fk_candidates 逐条给出"接受/存疑/拒绝+理由" |
| REQ-SFD-009 | **UI 设计师视角**（PROMPT-SFD-UI）：信息架构、逐页功能详说（用途/关键元素/动作语义）、导航流合理性观察 | 产出 `sections/03-pages.doc.md`；逐页清单覆盖 pages 表全部 done 页 |
| REQ-SFD-010 | **测试专家视角**（PROMPT-SFD-QA）：质量与盲区——T3 未触发动作风险、blocked_events 揭示的写面、timeout/error 页、未覆盖清单、低置信 findings 复核建议 | 产出 `sections/05-quality.doc.md`；每个盲区给出风险级别与建议 |
| REQ-SFD-011 | **诚实红线**（PROMPT-SFD 系列共有）：只读素材包与 UNDERSTANDING.md；禁止索取凭据；每条业务结论带证据引用或显式标注 `[推断]`；发现素材含疑似凭据残留 → 停止并报告而非转录 | 5 份 prompt 文档均含红线章节；宿主按契约执行 |

### 模块 C：产物与安全

| # | 需求 | 验收标准（AC） |
|---|------|----------------|
| REQ-SFD-012 | **产物布局**：全部详说产物落在 `<out>/<sid>/detailed/`（inputs/、sections/、assembly-report.json）+ 终稿 `<out>/<sid>/SYSTEM_FUNCTION_DOC.md`；不污染 SU 既有产物 | AC1 路径固定；AC2 UNDERSTANDING.md 等既有文件字节级不变 |
| REQ-SFD-013 | **凭据零泄漏收口**：装配后对终稿与素材包执行脱敏扫描（复用 config.scrub_text 判据 + 键值对凭据正则），命中 → 终稿不落盘 + exit 2 + 报告命中位置类别 | AC1 e2e 注入假凭据→拦截且 exit 2；AC2 正常场景 0 命中 |

## 5. 非功能需求（NFR-SFD-xxx）

| # | 需求 |
|---|------|
| NFR-SFD-001 | 脚本层零 LLM、零网络：详说脚本模块纯本地文件操作，可离线单测 |
| NFR-SFD-002 | 幂等：素材包/大纲/终稿同输入重跑逐字节稳定（时间戳 run 级常量） |
| NFR-SFD-003 | 复用优先：脱敏、证据索引读取、退出码、SuError 家族全部复用 su/ 既有实现，不复制粘贴造轮子 |
| NFR-SFD-004 | 五角色可并行亦可串行，装配对任意子集完成态都能出稿（降级不崩） |
| NFR-SFD-005 | 中文详细注释、函数级 docstring，与 su/ 现有代码风格一致 |

## 6. 错误与边界

| # | 场景 | 期望 |
|---|------|------|
| E-1 | understanding.json 缺失/损坏 JSON | exit 2，提示先跑 SU 采集与回填 |
| E-2 | findings 数组为空 | exit 2，提示先完成 PROMPT-SU-001 回填 |
| E-3 | 专家草稿中引用不存在的 E-n | 装配继续，引用标 `[未验证引用]`，report 计数 |
| E-4 | sections/ 目录为空 | 终稿所有专家节渲染降级声明，exit 0（不阻塞出稿） |
| E-5 | 素材含疑似凭据 | REQ-SFD-013 拦截，exit 2 |
| E-6 | SIGINT | 130，半成品不覆写上一版终稿（先写临时文件后原子 rename） |

## 7. 判定与度量

- 专家派发指引一条命令可得：`python3 scripts/system_understanding.py --out <dir> --system-id <id> --detailed-doc`
- 终稿 8 节完整率：正常路径 8/8；降级路径 = 完成角色数 + 降级声明节
- 证据引用合法率 ≥ 95%（assembly-report 度量）——**仅度量不拦截**；口径 = 专家草稿原文 token（改写前统计，不含第 7/8 节装配层自动生成内容，避免装配器自产引用自污染统计），见 ARCH ADR-4（2026-09-30 审查修订）

## 8. 验收场景（供测试设计）

| # | 场景 |
|---|------|
| S-1 | 全链路：SU 跑完 fixture 站点 → --detailed-doc 产出素材包+大纲 → 人工放置五份合规草稿 → --assemble 出终稿，8/8 节、0 非法引用、0 凭据命中 |
| S-2 | 降级链路：只放 2 份草稿 → 终稿照常出，降级节=3、report 正确 |
| S-3 | 安全链路：终稿输入含假凭据 → 拦截 exit 2 且终稿未更新。**必须覆盖"DB 采样非敏感键名 + 真实形态凭据值"向量**（如 `{"auth_code":"<12+位字母数字混合串>"} `，现有三判据不命中、由 C4 扩展判据拦截，见 ARCH §2.2.6），另含"脱敏形态自引用"反例（userinfo 已是 `***REDACTED***`/`<REDACTED:*>` 形态不算 C2 命中）（2026-09-30 审查修订） |
| S-4 | 校验链路：无 findings / 缺文件 / 与 --render-only 互斥违例 → 各自 exit 2 |
| S-5 | 幂等链路：同一起点（同一 understanding.json + 同一批草稿）下 `--assemble` 重跑终稿字节一致（`outline_modified` 在终稿上直接重装配时按设计为 true，比较前以 `--detailed-doc --force` 重置骨架同起点，2026-09-30 审查修订口径，见 ARCH P1-7a/P2-14） |

---

## 修订记录

### 2026-09-30 对抗审查修订（v1.0 → v1.1）

按 2026-09-30 架构审查结论（4 P0 + 6 P1 + 5 P2）逐条落实，全部采纳审查推荐项，
修订处均标注"（2026-09-30 审查修订）"。PRD 侧落点：

| 审查条目 | PRD 落点 |
|---|---|
| P0-1 证据编号不稳定 | G4、REQ-SFD-004（删除"编号稳定"语义，改为 (seq, ref) 双键 + run_id/sha256 锚点契约；代码事实已核实：`document_renderer._index_evidence` 的 seq = `len(self._evidence_index) + 1` 单次 render 累加） |
| P0-2 run 状态口径 | REQ-SFD-001（锚定 `meta.run_id` 行而非最新行；AC4 render-only 后最新 run 属预期；findings 空数组口径区分）、REQ-SFD-003 AC3（时间戳唯一真相源 = understanding.json `meta.started_at`） |
| P0-3 素材包安全 | REQ-SFD-002（config_snapshot 移出禁入清单、注明 meta 为 SU redact 后形态，口径统一以 ARCH 为准；manifest 增补 run_id/evidence-index sha256/db_samples_masked_by）、§8 S-3（新增 DB 采样非敏感键名+凭据值向量、脱敏形态自引用反例） |
| P1-6 findings 双源 | REQ-SFD-001（precheck 双源一致性断言，findings 计数复用 `stats().findings_total`——代码事实已核实：`su/state_store.py:1432`） |
| P1-5 CLI 组合 | REQ-SFD-005 AC2/AC3（--out/--system-id 必填、绝对路径指引、--fresh/--resume/--skip-llm-phase 组合拒绝、既有终稿需 --force） |
| P2-15 合法率口径 | §7（≥95% 仅度量不拦截，口径 = 专家草稿原文 token，不含第 7/8 节自动内容） |

ARCH 侧对应修订见 `SYSTEM_FUNCTION_DOC_ARCHITECTURE.md` 文末修订记录。
本次审查无 PRD 侧不采纳项（P2-11 不成立项属 ARCH 侧，见 ARCH 修订记录）。
