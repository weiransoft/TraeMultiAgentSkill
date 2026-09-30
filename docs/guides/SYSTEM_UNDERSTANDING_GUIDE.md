# 既有系统理解能力（SU）使用指南

- **能力版本**：v2.9.2（2026-09-30）
- **上游文档**：`docs/dev/SYSTEM_UNDERSTANDING_PRD.md`（REQ-SU-001~021）、`docs/dev/SYSTEM_UNDERSTANDING_ARCHITECTURE.md`（ARCH-SU-001）；SFD 阶段：`docs/dev/SYSTEM_FUNCTION_DOC_PRD.md`（PRD-SFD-001）、`docs/dev/SYSTEM_FUNCTION_DOC_ARCHITECTURE.md`（ARCH-SFD-001）
- **LLM 契约**：`docs/spec/role-prompts/su-llm-backfill.md`（PROMPT-SU-001）；SFD 五专家提示词：`docs/spec/role-prompts/su-detailed-*.md`（PROMPT-SFD 系列）
- **CLI 入口**：`scripts/system_understanding.py`（能力包 `scripts/su/`，19 个模块文件）

---

## 1. 能力概述

SU（System Understanding，既有系统反向理解）面向**无文档、无源码**的遗留黑盒 Web 系统（配套 MySQL/PostgreSQL 数据库与 Redis 缓存），在**全程只读、零副作用**的前提下自动完成：

1. **Playwright 自动登录**：显式选择器或启发式识别登录表单，会话维持与自动重登（≤3 次），验证码/2FA 场景支持 `--storage-state` 人工已登录态旁路；
2. **图式 BFS 页面遍历**：`url_key` 规范化去重、DOM 剪枝快照、动作三级分级（T1 放行 / T2 显式 GET 表单 / T3 危险操作只记录）、`page.route` 网络层拦截全部非 GET 请求与白名单外域；
3. **DB 只读内省**：表/列/注释/PK/显式 FK/索引/行数估算/采样（逐值脱敏）+ 隐式外键推断；会话级只读 + SQL 语句白名单校验器；
4. **Redis 只读采集**：SCAN 游标 + 命令硬编码只读白名单 + 键模式聚类（数量/TTL 分布/类型）；
5. **三角关联分析**：页面 ↔ API ↔ 表（↔ Redis 键模式）的确定性关联证据（重合度/包含度数值），供 LLM 与文档引用；
6. **10 节《系统功能理解文档》渲染**：系统概览 / 功能地图 / 导航图 / 数据模型 / UI↔数据映射 / 缓存与中间件 / 业务规则汇编 / API 面 / 证据附录 / 未验证推断与未覆盖清单，另有机读 `understanding.json` 与 Mermaid 图源。

设计红线：**脚本层零 LLM 调用、不出语义结论**；语义结论由宿主 LLM 按契约回填、CLI 校验收口——证据与结论严格分离。

## 2. 两阶段工作流

```
阶段 A（确定性采集）
  python3 scripts/system_understanding.py --config config.json --skip-llm-phase
  → 登录 + BFS 遍历 + DB/Redis 内省 + 三角关联全部完成
  → UNDERSTANDING.md 骨架（第 5/7 节写"待 LLM 语义回填"占位，不虚构结论）
  → understanding.json 就绪（全脱敏，供宿主 LLM 消费）

阶段 B（宿主 LLM 语义回填）
  宿主 LLM（TRAE 各角色）按 docs/spec/role-prompts/su-llm-backfill.md 契约：
  读取 understanding.json（全部脱敏）→ 产出 findings JSON 数组 → 写回该文件 findings 段

阶段 C（渲染收口）
  python3 scripts/system_understanding.py --out <同目录> --system-id <id> --render-only
  → 不启动浏览器、不连 DB/Redis；校验 findings → 入库（status=proposed）→ 重渲染全部产物
  → 校验失败退出码 2 并中文逐条列出违约项；成功退出码 0（渲染幂等）
  → 成功收口后 understanding.json 的 meta.run_status 即为 completed（v2.9.2 D1 修复：
    渲染按"成功即 mark(completed)"注入终态投影，磁盘与 run_meta 一致；
    中断链路维持先 mark 后导出的原语义）
```

### findings 回填示例（写入 `understanding.json` 的 `findings` 段，整体替换语义）

```json
{
  "findings": [
    {
      "claim": "订单列表页（pages:12）的 /api/orders（api_observations:3）读取 orders 表（db_tables:7）",
      "kind": "mapping",
      "confidence": "high",
      "evidence_refs": ["pages:12", "api_observations:3", "db_tables:7", "relations:5"],
      "status": "proposed"
    },
    {
      "claim": "orders.status 列取值 {0,1,2} 疑似对应 待支付/已支付/已取消 状态机",
      "kind": "business_rule",
      "confidence": "low",
      "evidence_refs": ["db_samples:41", "pages:12"],
      "status": "proposed"
    }
  ]
}
```

字段口径（详见 PROMPT-SU-001 §2/§3）：`kind` ∈ mapping/business_rule/redis_entity/semantic_name；`confidence` ∈ high/medium/low（low 渲染时自动标"⚠ 待人工确认"并汇总第 10 节）；`evidence_refs` ≥1 条 `'<表名>:<id>'` 且必须命中状态库现存记录；`status` 固定填 `proposed`。任一校验违例（非法枚举、无证据引用、claim 含键值对形态凭据等）→ **整批拒绝**，退出码 2。

## 3. 配置示例

### 3.1 JSON 配置文件（推荐，`chmod 600`）

```json
{
  "system": {
    "base_url": "https://admin.example.com",
    "login_url": "/login",
    "username": "<系统账号>",
    "password": "<系统密码>",
    "login_selectors": { "username": "input[name=user]", "password": "input[name=pwd]", "submit": "button[type=submit]" },
    "success_hint": ".layout-container"
  },
  "database": {
    "engine": "mysql",
    "host": "127.0.0.1", "port": 3306, "user": "ro_user", "password": "<只读密码>",
    "database": "app_db", "schemas": ["public"]
  },
  "redis": {
    "host": "127.0.0.1", "port": 6379, "password": "<密码>", "db": 0,
    "key_allowlist": ["app:*", "sess:*"]
  }
}
```

- `system` 必填；`database`、`redis` 可选，缺失则对应透镜自动跳过并在文档"未覆盖清单"登记。
- `login_selectors` 缺省时走自动登录表单启发式识别；`database.engine` 仅支持 `mysql` / `postgresql`。
- DB 账号只需 SELECT 权限；Redis 如支持 ACL，建议只授 keyspace 只读。

### 3.2 三级配置通道（优先级：CLI 显式参数 > 环境变量 > JSON 配置文件）

环境变量：`SU_SYSTEM_USERNAME` / `SU_SYSTEM_PASSWORD` / `SU_DB_PASSWORD` / `SU_REDIS_PASSWORD`。

### 3.3 连接串快捷通道

`--db-url mysql://user:pass@host:3306/db`（或 `postgresql://...`）整体覆盖 `database` 段；`--redis-url redis://:pass@host:6379/0` 整体覆盖 `redis` 段。注意：连接串出现在 shell 历史属用户自担风险，**推荐优先用配置文件/环境变量**。

## 4. CLI 参数表（与 `--help` 逐条对齐）

| 参数 | 默认 | 说明 |
|---|---|---|
| `--config PATH` | 无 | 凭据/目标 JSON 配置文件（优先级 CLI > env > 文件） |
| `--system-url` | 无 | 目标系统入口 URL（覆盖配置文件 system.base_url） |
| `--username` | 无 | 系统登录账号（亦可用 `SU_SYSTEM_USERNAME`） |
| `--password` | 无 | 系统登录密码（亦可用 `SU_SYSTEM_PASSWORD`；命令行传入进入 shell 历史，风险自担） |
| `--db-url URL` | 无 | 数据库连接串 `mysql://...` / `postgresql://...`（整体覆盖 database 段） |
| `--redis-url URL` | 无 | Redis 连接串 `redis://:pass@host:6379/0`（整体覆盖 redis 段） |
| `--out DIR` | `docs/system-understanding/` | 输出根目录（产物落 `<out>/<system_id>/`；`--render-only` 时必须显式提供并指向前次运行目录） |
| `--system-id` | 由目标主机名派生 | 输出子目录名 |
| `--max-pages` | 100 | 页面预算上限 |
| `--max-depth` | 6 | BFS 深度上限 |
| `--max-actions-per-page` | 30 | 单页候选动作上限 |
| `--time-budget-minutes` | 60 | 墙钟时间预算分钟数 |
| `--delay-ms` | 1500 | 全局限速间隔毫秒（等效 QPS ≤ 1/delay） |
| `--page-timeout-ms` | 30000 | 单页导航超时毫秒 |
| `--sample-rows` | 10 | DB 每表采样行数（上限 50） |
| `--redis-max-keys` | 5000 | Redis 键采集预算 |
| `--allowed-origins ORIGINS` | base_url 同源 | 追加浏览器白名单域（逗号分隔） |
| `--headed` | false | 有头调试模式（观察遍历过程；生产建议关闭） |
| `--storage-state PATH` | 无 | 人工已登录态 storage_state 文件旁路注入（验证码/2FA 场景） |
| `--resume` / `--fresh` | resume | 断点续跑：复用 interrupted 进度（默认）/ 归档旧状态后全新重跑 |
| `--skip-llm-phase` | false | 只跑确定性采集，产出"待 LLM 语义回填"骨架文档 |
| `--render-only` | false | 仅渲染：校验入库 findings 并重渲染全部产物（findings 校验失败退出码 2） |
| `--detailed-doc` | false | 专家详说（第三阶段，v2.9.1）：前置校验 SU 产物 → 生成五专家素材包与 8 节大纲骨架 → 打印派发指引（要求 findings 已回填且锚定 run ∈ {completed, interrupted}；与 `--render-only`/`--assemble` 互斥） |
| `--assemble` | false | 装配详说终稿（v2.9.1）：读取 `detailed/sections/` 专家草稿 → E-n 引用校验 → 凭据扫描 → 原子写 `SYSTEM_FUNCTION_DOC.md` + `assembly-report.json`（可独立于 `--detailed-doc` 反复执行） |
| `--force` | false | 仅与 `--detailed-doc`/`--assemble` 配合：覆盖既有终稿（头部 `status: final`）时跳过 exit 2 保护 |
| `--verbose` | false | 调试日志（DEBUG 级；全程仍脱敏） |

**退出码**：0 成功（含预算耗尽收尾、透镜降级完成）；2 参数/配置错误（含 `--render-only` findings 校验失败）；3 目标系统不可达；4 登录失败/会话反复失效；5 playwright 缺失（UI 透镜致命降级）；130 收到 SIGINT（状态库已存 `interrupted`，可 `--resume` 续跑）。

## 5. 输出产物目录树（PRD §5）

```
docs/system-understanding/<system_id>/
├── UNDERSTANDING.md              # 《系统功能理解文档》主文档（10 节）
├── understanding.json            # 结构化全量结果（机读、脱敏后；findings 回填目标）
├── summary.json                  # 运行摘要（预算消耗、透镜完成度、confidence 分布）
├── state/
│   ├── understanding.sqlite      # 断点续跑状态库（SQLite WAL；进程互斥即库内 run_meta 条件更新，无独立锁文件/run.lock）
│   ├── storage_state.json        # 浏览器会话态（0600，敏感，用后清理）
│   ├── preflight.json            # 预检报告
│   └── blocked_origins.json      # 白名单外域/被拦截请求记录（API 形态观测即 api_observations 表 + understanding.json endpoints，无独立 api-shapes 文件）
├── snapshots/
│   └── <page_id>.json            # 页面语义骨架快照（剪枝脱敏，≤64KB/页）
├── diagrams/
│   ├── navigation.mmd            # 导航图 Mermaid 源
│   └── er.mmd                    # ER 图 Mermaid 源（隐式 FK 标"推断"）
├── evidence/
│   └── evidence-index.json       # 证据编号 → 状态库记录映射
└── logs/
    └── run-<timestamp>.log       # 脱敏运行日志
```

## 6. 安全红线与诚实降级

**五条安全红线（不可协商，违反即 P0 缺陷）**：

1. **凭据安全**：凭据不进 LLM 上下文、不落盘明文——统一 `redact()` 管线（敏感键名 → `***REDACTED***`，PII 值形态 → `<REDACTED:类型>`）、URL 内嵌 `user:pass@` 先剥离、日志经 RedactingFormatter（`--verbose` 亦不例外）、`storage_state` 0600 落盘；
2. **DB 只读**：会话级只读 + SQL 语句白名单校验器，严禁任何 DDL/DML/DROP；
3. **Redis 只读**：命令硬编码只读白名单（SCAN/TYPE/TTL/MEMORY USAGE 等）；
4. **网络拦截**：`page.route` 拦截全部 POST/PUT/PATCH/DELETE 与白名单外域，下载与新窗口默认拒绝，被拦截写请求作为"系统有写能力但未验证"的观测清单进文档第 8 节；
5. **零点击危险操作**：T3 动作（裸按钮、POST 表单、危险动词等）绝不执行，完整记录进"未执行动作清单"（第 10 节）。

**诚实降级（缺失即声明缺失，禁 mock）**：

| 缺失依赖 | 行为 |
|---|---|
| playwright | 致命降级：无法执行 UI 透镜，中文报错含安装命令，退出码 5，不产出任何编造的 UI 结论 |
| pymysql / psycopg2 | 按 `database.engine` 判定：跳过 DB 透镜，文档数据模型节标"未采集：驱动缺失（给出安装命令）" |
| redis | 跳过 Redis 透镜，文档"缓存与中间件"节显式声明未采集原因 |

四透镜（UI/API/DB/Redis）故障互相隔离，单透镜失败不终止整体；文档"未覆盖清单"与实际能力严格一致。

软依赖安装（按需）：`pip install 'playwright>=1.40.0' && playwright install chromium`；`pip install pymysql psycopg2-binary redis`（版本口径见 `requirements.txt` 注释行）。

## 7. FAQ

**Q1：登录页有验证码 / 2FA 怎么办？**
不尝试破解。先在浏览器人工登录并导出 `storage_state`（Playwright `context.storage_state(path=...)` 或浏览器插件导出同构 JSON），再以 `--storage-state <path>` 注入，跳过自动登录直接进遍历。

**Q2：遍历中途中断了要重头再来吗？**
不用。默认策略即 `--resume`：SIGINT（退出码 130）或崩溃后重启，interrupted 状态库的已完成页面/表/键自动继承，只补做未完成部分。要彻底重跑用 `--fresh`（旧状态归档为 `state.archive.<ts>/` 后重跑）。

**Q3：只想改 findings 重新出文档，不想重新采集？**
用 `--render-only`：直接编辑输出目录的 `understanding.json` 的 `findings` 段（整体替换语义），然后运行 `python3 scripts/system_understanding.py --out <前次输出根目录> --system-id <id> --render-only`。该分支不启动浏览器、不连库，只做 findings 校验入库 + 幂等重渲染。

**Q4：没装 playwright 能跑吗？**
`--help` 与 `--render-only` 无 playwright 也可用。完整采集则致命降级（退出码 5）并给出安装命令——这是红线设计，不以假数据冒充 UI 采集结果。

**Q5：DB 密码怎么给最安全？**
优先级：JSON 配置文件（`chmod 600`）> 环境变量（`SU_DB_PASSWORD` 等）> `--db-url`（进 shell 历史，自担风险）。任何通道进来的凭据都只在内存中持有，落盘一律过脱敏管线。

**Q6：怎么验证产物没有泄露敏感信息？**
集成测试口径即"输出目录全文件 + SQLite 文件 grep 凭据/PII 明文 0 命中"（REQ-SU-002 AC2）。可自查：`grep -rEi 'password|token' docs/system-understanding/<id>/ --include='*'` 并人工复核命中上下文是否为脱敏占位。

**Q7：如何跑本能力测试？**
单测：`bash scripts/tests/scripts/run_system_understanding.sh`（20 模块，含 SFD 6 模块）；e2e：`bash scripts/tests/scripts/run_system_understanding_e2e.sh`（场景[0]-[7] 为 SU 场景，[8] 需外部注入 `SU_TEST_MYSQL_DSN`/`SU_TEST_REDIS_URL` 缺省 SKIP，[9]-[13] 为 SFD 场景；playwright 缺失时浏览器场景显式 SKIP，[10]-[13] 零浏览器依赖恒执行）。两者均已接入 `run_all.sh` 聚合。

## 7. 专家详说阶段（SFD，第三阶段，v2.9.1）

SU 采集 + findings 回填 + `--render-only` 渲染收口完成之后，可追加**第三阶段"专家详说"（SFD，System Function Doc）**：以已脱敏落盘的 SU 产物为唯一事实源，由五位专家分节撰写叙述性《系统功能详说文档》（`SYSTEM_FUNCTION_DOC.md`，8 节），与证据汇编 `UNDERSTANDING.md` 并存互链。实现模块 `scripts/su/detailed_doc.py`，**零网络、零凭据、零新依赖**——不启动浏览器、不连 DB/Redis。

### 7.1 触发前提

- `UNDERSTANDING.md`、`understanding.json`、`evidence/evidence-index.json` 三件套存在（即 `--render-only` 已至少成功收口一次）；
- `understanding.json` 的 `findings` 段非空（宿主 LLM 已回填，PROMPT-SU-001），且与状态库计数双源一致；
- 锚定 run（`understanding.json` 的 `meta.run_id` 对应行，**非最新行**）状态 ∈ {completed, interrupted}；
- 详说模式拒绝与 `--fresh` / `--resume` / `--skip-llm-phase` 组合（显式报错，退出码 2）；与 `--render-only` / `--assemble` 互斥（argparse 互斥组，同时给出退出码 2）。

### 7.2 三命令工作流

```
第 1 步（脚本层，确定性）
  python3 scripts/system_understanding.py --out <out> --system-id <id> --detailed-doc
  → 前置校验（findings 非空 + 双源一致 + 锚定 run 状态，全程只读）
  → 五视角素材包 detailed/inputs/*.json（字段白名单 + scrub 复核 + 锚点 manifest）
  → 8 节大纲骨架 SYSTEM_FUNCTION_DOC.md（status: outline）+ 创建空 detailed/sections/
  → stdout 打印五专家派发指引（prompt/素材包/输出文件绝对路径）与装配命令

第 2 步（宿主 LLM，脚本外）
  按派发指引将五条任务并行交给专家子代理（Task 机制）：
  各自读素材包 + UNDERSTANDING.md，按 PROMPT-SFD 契约产出分段草稿
  detailed/sections/0N-xxx.doc.md（首行节头必须与大纲一致；E-n 引用推荐
  E0012(pages:3) 锚注形态；无证据推断显式标 [推断]）

第 3 步（脚本层，确定性）
  python3 scripts/system_understanding.py --out <out> --system-id <id> --assemble
  → 逐节归属校验 + E-n 引用 (seq,ref) 双键校验（锚点失配时漂移检测）
  → 降级声明与 low findings 汇总（第 7 节自动生成）+ 证据索引附录（第 8 节）
  → 四判据凭据扫描（命中 → 终稿与报告均不落盘，退出码 2）
  → 原子写终稿 SYSTEM_FUNCTION_DOC.md（status: final）+ detailed/assembly-report.json
```

`--assemble` 独立于 `--detailed-doc`：草稿返工、findings 修订后可反复重跑；既有终稿（头部 `status: final`）默认拒绝覆盖，需显式加 `--force`。

### 7.3 产物布局

```
<out>/<system_id>/
├── UNDERSTANDING.md                  # SU 既有——详说阶段字节级不变
├── understanding.json                # SU 既有——详说阶段只读
├── evidence/evidence-index.json      # SU 既有——E-n 引用合法集合源
├── SYSTEM_FUNCTION_DOC.md            # 终稿（--detailed-doc 产骨架 → --assemble 覆盖终稿）
└── detailed/
    ├── inputs/                       # 五视角素材包（--detailed-doc 原子写）
    │   ├── architect.json  ├── product.json  ├── dev.json
    │   ├── ui.json         └── qa.json
    ├── sections/                     # 专家草稿（宿主 LLM 写入；装配时读取）
    │   ├── 01-architecture.doc.md    ├── 02-product.doc.md
    │   ├── 03-pages.doc.md           ├── 04-data-semantics.doc.md
    │   └── 05-quality.doc.md
    └── assembly-report.json          # 装配报告（与终稿同批落盘）
```

终稿固定 8 节：1 系统定位与技术架构 / 2 功能全景与业务流程（含 2.4 业务规则与状态机）/ 3 页面功能详说 / 4 数据模型业务语义 / 5 接口契约说明 / 6 质量盲区与风险建议 / 7 未验证推断与附录（装配层自动）/ 8 附录：证据索引与运行说明（装配层自动）。

### 7.4 五角色分工

| 角色 | 派发提示词（`docs/spec/role-prompts/`） | 素材包 | 输出草稿 | 承担终稿节 |
|---|---|---|---|---|
| 架构师 | `su-detailed-architect.md` | `inputs/architect.json` | `sections/01-architecture.doc.md` | 第 1 节 |
| 产品经理 | `su-detailed-product.md` | `inputs/product.json` | `sections/02-product.doc.md` | 第 2 节（含 2.4） |
| UI 设计师 | `su-detailed-ui.md` | `inputs/ui.json` | `sections/03-pages.doc.md` | 第 3 节 |
| 独立开发者·走读 | `su-detailed-walkthrough.md` | `inputs/dev.json` | `sections/04-data-semantics.doc.md` | 第 4、5 节（第 5 节以 `<!-- SFD-SECTION: 5 -->` 标记切分） |
| 测试专家 | `su-detailed-qa.md` | `inputs/qa.json` | `sections/05-quality.doc.md` | 第 6 节 |

### 7.5 降级与安全语义

- **降级不崩（退出码 0）**：缺草稿 / 首行节头不符 / 第 5 节标记缺失或多写 → 对应节替换为降级声明并在 `assembly-report.json` 登记 `degraded_sections`；非法 E-n 引用原文保留并改写为 `E0012 [未验证引用]`、锚点失配场景的同 seq 异 ref 引用改写为 `[漂移引用]`，均计入报告（合法率仅度量不拦截，达标线 ≥95%）。
- **凭据扫描收口（退出码 2）**：装配落盘前对终稿 + 五包执行四判据扫描（C1 scrub 差集 / C2 URL userinfo（脱敏形态豁免）/ C3 键值对 / C4 扩展敏感键名 × 高熵值），命中则终稿与报告**均不落盘**、上一版完好，stdout 只报位置类别不含原文。
- **原子写与幂等**：全部产物临时文件 + rename 落盘，SIGINT（退出码 130）不覆写上一版；时间戳统一取 `meta.started_at`（run 级常量），同输入重跑终稿逐字节一致。
- **诚实红线**：专家只读素材包与 UNDERSTANDING.md，禁止索取/猜测凭据、禁止虚构；发现素材疑似凭据残留立即停止报告，绝不转录（脚本扫描器为同一红线的机器镜像）。
- **前置校验口径（v2.9.2 D1 修复）**：SFD 前置校验直接读 understanding.json 的 `meta.run_status` 即可——v2.9.2 起，`--render-only` 成功收口后该字段即为 `completed`（渲染按"成功即 mark(completed)"注入终态投影，与 run_meta 收口一致）；v2.9.1 及更早版本该字段冻结在 `running`，需回读 run_meta 佐证。StateStore 仅接受 `completed` / `interrupted` 终态覆盖，`running` 等非法值 ValueError 拒绝，杜绝伪造终态。


### 7.6 退出码（详说模式，继承 SU 约定之子集）

| 退出码 | 场景 |
|---|---|
| 0 | 成功（含降级出稿） |
| 2 | 前置校验违例（缺文件/findings 空/双源不一致/run 状态违例）、互斥与生命周期参数组合违例、既有终稿未加 `--force`、凭据扫描命中 |
| 130 | SIGINT（临时文件 + rename 原子写保证上一版完好） |

> 完整设计口径见 `docs/dev/SYSTEM_FUNCTION_DOC_ARCHITECTURE.md`（ARCH-SFD-001 §3 CLI / §5 产物 / §8 安全）与 `docs/dev/SYSTEM_FUNCTION_DOC_PRD.md`（PRD-SFD-001）。
