# 既有系统理解能力（System Reverse Understanding）需求文档（PRD）

- **文档编号**：PRD-SU-001
- **所属项目**：TraeMultiAgentSkill
- **能力代号**：SU（System Understanding）
- **文档状态**：评审通过（已按 2026-09-28 架构审查修订）
- **编写角色**：产品经理
- **关联红线**：`CONSTITUTION`（禁 mock/占位实现、凭据不进 LLM 上下文、只读 DB 边界、先文档后代码）
- **关联既有模块**：`scripts/project_understanding.py`（静态项目理解，本能力沿用其脚本骨架风格）

---

## 1. 背景与目标

### 1.1 背景

TraeMultiAgentSkill 当前具备"静态代码/文档理解"能力（`project_understanding.py`），但面对**只有运行中的 Web 系统、没有源码**的既有系统（遗留系统、外采系统、黑盒 SaaS 后台等），多智能体团队缺乏系统性理解手段，只能靠人工点页面、口口相传，导致：

1. 接手既有系统做二次开发/迁移/审查时，理解成本高、遗漏多；
2. 页面功能与底层数据（表、缓存）之间的映射关系全靠猜，没有证据链；
3. 测试用例设计缺少业务规则依据。

### 1.2 用户故事

> **US-1（独立开发者）**：作为一名接手遗留 Web 系统的开发者，我希望在调用 skill 时提供系统登录账号密码、数据库账号密码、Redis 账号密码（如有），skill 就能自动登录系统、遍历所有页面学习功能、只读分析数据库结构与采样数据、采样 Redis 键模式，最终产出一份带证据链的《系统功能理解文档》，让我在不读源码的情况下快速建立对系统的完整心智模型。

> **US-2（测试专家）**：作为测试专家，我希望文档中包含"功能地图 + 页面↔API↔数据表映射 + 业务规则汇编 + 未覆盖清单"，据此设计覆盖真实业务规则的测试用例，并明确知道哪些区域 skill 没走到、置信度低，需要人工补测。

> **US-3（架构师）**：作为架构师，我希望全过程严格只读——不写库、不改缓存、不点击任何破坏性按钮、凭据不进入 LLM 上下文——使该能力可以安全地对外部生产/预发系统使用。

> **US-4（任何用户）**：作为长时间任务的使用者，我希望遍历中断（网络断开、手动终止、机器休眠）后可以从断点续跑，而不是从头再来。

### 1.3 目标（成功度量）

| 目标 | 度量方式 |
|---|---|
| G1 自动化黑盒理解 | 对一个中等规模后台（≤150 页面）一次运行产出完整文档，无需人工干预 |
| G2 证据可追溯 | 文档中每条 LLM 结论均带 `confidence` 与 `evidence_refs`，可回溯到采集快照 |
| G3 安全零事故 | 全过程对目标系统零写操作（以 API 观测与 DB 审计验证） |
| G4 可恢复 | 任意时刻终止后重启，能在已探索状态上继续，不重复已完成的探索 |
| G5 诚实降级 | 依赖缺失/凭据错误/目标不可达时给出明确中文报错与建议，绝不产出虚假结论 |

### 1.4 方法论依据（已调研确定，直接采用）

- **Web 遍历**：WebNavigator / Go-Browse 图式 BFS 遍历 + browser-use 式 DOM 剪枝（提取可交互元素签名而非全量 HTML）。
- **黑盒逆向**：Thoughtworks《Black-Box to Blueprint》多透镜（UI 流、数据流、领域模型）+ 证据链（lineage）+ 三角验证（UI 说法 ↔ API 报文 ↔ DB 实际数据）。
- **数据库文档化**：DBAutoDoc 式 schema 语义标注 + 隐式外键三重预筛（命名约定 / 类型匹配 / 值域包含度）。
- **Redis**：`SCAN` 游标增量采样 + 只读命令白名单 + 键模式聚类。
- **破坏性操作防护**：route 层拦截 POST/PUT/PATCH/DELETE、危险按钮识别后只记录不点击、域名白名单、全局限速。

---

## 2. 范围

### 2.1 In Scope（本期交付）

1. 系统凭据 / DB 凭据 / Redis 凭据的安全输入与处理（JSON 配置文件 / CLI / 环境变量三通道）。
2. 基于 Playwright（agent browser）的自动登录与会话维持。
3. 图式 BFS 全站遍历：页面节点去重、动作分级执行、覆盖预算、请求限速。
4. 网络层 API 观测（request/response 监听与结构化记录）。
5. 数据库只读内省：MySQL（`information_schema`）/ PostgreSQL（`information_schema` + `pg_catalog`）结构清单、表采样与脱敏、隐式 FK 三重预筛。
6. Redis 只读采集：`SCAN` 采样、白名单命令、键模式聚类与 TTL/类型/值样例记录。
7. UI ↔ API ↔ DB ↔ Redis 三角关联分析（确定性部分由脚本完成，语义部分交宿主 LLM）。
8. 结构化《系统功能理解文档》生成（10 节，含 Mermaid 导航图与 ER 图）。
9. SQLite 断点续跑状态机。
10. CLI 入口（`scripts/system_understanding.py`）与诚实降级（软依赖缺失时报错引导）。
11. 单元测试（`scripts/tests/`，unittest 框架）与集成测试脚本（`scripts/tests/scripts/run_*.sh`）。
12. 五个清单文件同步：`skill-manifest.yaml`、`skills-index.json`、`claude-code-skill.json`、`registry/skills.json`、`SKILL.md`。

### 2.2 Out of Scope（本期明确不做）

| 编号 | 排除项 | 说明 |
|---|---|---|
| OUT-1 | 受控写 diff（write-behavior diffing） | 不通过"提交表单前后对比"验证写路径语义；本期对写操作只观测、只记录，绝不执行。后续版本可评估在隔离环境开启。 |
| OUT-2 | 真正的 CDC（Change Data Capture） | 不部署 binlog/WAL 逻辑复制、不做实时变更流采集；DB 理解仅基于一次性结构内省 + 有限采样。 |
| OUT-3 | 任何 DDL/DML 执行 | 严禁 `DROP`/`TRUNCATE`/`INSERT`/`UPDATE`/`DELETE`/`ALTER`/`CREATE`/`VACUUM` 等（含 `drop_all()` 类 ORM 便捷方法）。 |
| OUT-4 | 密码找回、验证码绕过、2FA 自动破解 | 无法自动登录时如实报告并请求人工介入（提供已登录 storage_state 的旁路）。 |
| OUT-5 | 生成可运行代码/修复缺陷 | 本能力只产"理解文档"，不产代码变更。 |
| OUT-6 | MongoDB / Oracle / SQL Server / 消息队列（Kafka/MQ）等其余中间件内省 | 本期 DB 仅 MySQL/PostgreSQL，缓存仅 Redis；其余留待后续。 |
| OUT-7 | 移动端 App / 桌面客户端遍历 | 仅覆盖 Web（Playwright 可达）界面。 |
| OUT-8 | 多租户系统的租户切换遍历 | 仅以所提供账号可见范围内数据为准。 |
| OUT-9 | LLM 语义判断的自建模型调用层 | 语义标注由宿主 LLM（提示词层）完成，脚本层不内置任何模型 API 调用。 |

---

## 3. 功能需求清单

> 编号规则：`[REQ-SU-XXX]`。每条含描述与验收标准（AC）。标注 **MUST**（必须）/ **SHOULD**（应当）。

### 3.1 配置与凭据输入

#### [REQ-SU-001] 三级凭据配置输入（MUST）

支持以三种通道之一（优先级：CLI 显式参数 > 环境变量 > JSON 配置文件）提供：

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
    "engine": "mysql | postgresql",
    "host": "...", "port": 3306, "user": "...", "password": "...",
    "database": "...", "schemas": ["public"]
  },
  "redis": {
    "host": "...", "port": 6379, "password": "...", "db": 0,
    "key_allowlist": ["app:*", "sess:*"]
  }
}
```

- `system` 必填；`database`、`redis` 可选，缺失则对应透镜自动跳过并在文档"未覆盖清单"中登记。
- `login_selectors` 缺省时走自动登录表单启发式识别（`input[type=password]` 邻近文本框 + 最近 submit 按钮）。

**验收标准**：
- AC1：仅传 `--config config.json` 可完整运行；CLI 参数 `--system-password` 可覆盖配置文件同名字段（覆盖优先级正确，有单测断言）。
- AC2：环境变量通道支持 `SU_SYSTEM_USERNAME` / `SU_SYSTEM_PASSWORD` / `SU_DB_PASSWORD` / `SU_REDIS_PASSWORD` 覆盖。
- AC3：`database.engine` 不在 `{mysql, postgresql}` 内、或必填字段缺失 → 启动即报结构化中文错误（含缺失字段名），不进入采集阶段。

#### [REQ-SU-002] 凭据安全处理（MUST）

1. **凭据不进 LLM 上下文**：所有含明文凭据的对象在进入提示词/LLM 前必须经过 `redact()` 统一脱敏（密码类字段替换为 `***REDACTED***`）；传递给宿主 LLM 的中间产物只允许引用脱敏后的快照。
2. **凭据不落盘明文**：SQLite 状态库与 `state/` 产物中禁止出现密码字段明文；JSON 配置必须由用户自行放置，工具读取后仅在内存中持有；文档与日志中 URL 内嵌的 `user:pass@` 形态必须先剥离。
3. **日志脱敏**：日志输出、API 观测记录（headers/cookie/authorization/body 中的 `password`、`token`、`secret` 类键）统一走同一 `redact()` 管线。

**验收标准**：
- AC1：单测构造含 `password/token/secret/api_key` 的 dict/URL/文本，断言 `redact()` 后无明文残留。
- AC2：集成测试运行结束后，对输出目录全部文件 + SQLite 文件做敏感串 grep 扫描，断言 0 命中。
- AC3：写盘入口的类型约束采用**四层组合**保证（非单一"结构约束"）：① `RedactedDict` 标记类型（写盘函数入参声明为该类型）；② 写盘函数入口运行时 `isinstance` 断言（非标记类型直接抛错）；③ CI 静态审查（写盘签名清单核对，见架构 §5.1/§5.2）；④ 集成测试全目录敏感串 grep 0 命中收口（AC2 管线）。四层任一缺失即视为 AC3 不达标。**实现口径注记**：第③层"CI 静态审查"在当前工程以等效方式落地——由 `StateStore._assert_redacted` 在全部写盘入口运行时强制（凡入 `state/` 与 SQLite 的对象必经该断言，未脱敏直接抛错）+ e2e 全目录敏感串 grep 0 命中收口，两者共同覆盖静态清单核对的防护目标。

#### [REQ-SU-003] 配置校验与预检（preflight）（MUST）

启动时执行预检并输出预检报告：目标站点可达性（HEAD 请求）、DB 连通性（仅握手 + `SELECT 1` 级别探测）、Redis `PING`、软依赖可用性检测。任一预检失败 → 明确区分"致命失败（system 不可达）终止"与"可降级失败（redis 不可达→跳过 Redis 透镜）"。

**验收标准**：
- AC1：预检结果结构化（每项 `name/status/reason/degradable`），写入 `state/preflight.json`。
- AC2：Redis 不可达时流程继续，最终文档"缓存与中间件"节标注"未采集：Redis 不可达（原因）"。

### 3.2 自动登录

#### [REQ-SU-004] Playwright 自动登录（MUST）

1. 使用 Playwright（Chromium headless，可 `--headed` 调试）打开 `login_url`，按显式选择器或启发式规则填写账号密码并提交。
2. 登录成功判定（满足其一即成功，判定结果记录到状态库）：a) 出现 `success_hint` 选择器；b) URL 离开登录路径；c) 会话 Cookie 新增。
3. 登录成功后将浏览器 `storage_state`（Cookie + localStorage）持久化到本地 `state/storage_state.json`，该文件按 0600 权限落盘，并纳入"用户显式声明不含 DB/Redis 凭据"的范围（会话令牌本身按敏感处理：不进 LLM 上下文）。
4. 会话失效自动重登：遍历过程中检测到被重定向回登录页时，自动重登 ≤ 3 次，仍失败则保存断点并终止，报"会话反复失效，请检查账号风控/验证码"。
5. 登录页存在验证码或 2FA 时不尝试破解，提供 `--storage-state <path>` 旁路：接受人工预先导出的已登录 storage_state 直接注入。

**验收标准**：
- AC1：对本地测试站（tests 内置 fixture 静态登录页）自动登录成功并进入遍历，`state/login_session.json` 记录成功判定依据。
- AC2：错误密码 → 明确报"登录失败：疑似凭据错误（未跳转且无 success_hint）"，退出码非 0，不产生半成品文档。
- AC3：`--storage-state` 注入路径可用（测试中以人工构造 cookie 文件验证）。
- AC4：重登逻辑单测：模拟会话失效 3 次内恢复 / 超过 3 次终止两条分支。

### 3.3 图式 BFS 遍历

#### [REQ-SU-005] 图式 BFS 页面遍历（MUST）

1. 以登录后的落地页为根节点做 BFS：每个页面建模为节点 `page_node{url_key, url, title, depth, discover_from}`，每条可交互元素（链接/按钮/表单/菜单项）建模为候选边。
2. **节点去重**：`url_key` = 规范化 URL（协议归一、忽略追踪参数白名单如 `utm_*`/`_t`/`timestamp`、hash 路由保留 `#/path` 部分、数字/UUID 路径段归一为 `{id}`），同一 `url_key` 只完整探索一次。
3. **DOM 剪枝**（browser-use 式）：不存全量 HTML，只提取可交互元素签名表（tag、role、text/aria-label、href、是否为表单）+ 页面语义骨架（标题层级、主内容区文本摘要），供 LLM 与去重使用。
4. SPA 支持：点击后就绪判定采用**有界算法**（架构 §2.3.6 wait_ready：最多 3 轮"domcontentloaded + 网络静默 500ms + DOM 摘要 hash/可交互元素数比对"，超限记 `wait_ready_timeout` 不阻塞）。
5. 遍历产出写入状态库（`pages`/`edges` 表），供断点续跑与文档渲染。

**验收标准**：
- AC1：对 fixture 多页站点（含重复链接、查询参数乱序、hash 路由），访问次数 == 唯一 `url_key` 数，无重复展开。
- AC2：`url_key` 规范化函数单测覆盖：参数排序、追踪参数剥离、数字段归一、hash 路由、大小写主机名。
- AC3：BFS 顺序正确性单测：depth 单调不减。

#### [REQ-SU-006] 动作分级执行（action tiering）（MUST）

对页面上的每个候选动作分级，按级处置：

| 级别 | 判定 | 处置 |
|---|---|---|
| T1 放行 | `<a href>` 同域链接、GET 型导航 | 直接访问（计入限速预算） |
| T2 只读表单提交 | **仅**显式 `<form method="GET">` 且该 form 内文本（按钮/标签/字段名）无任何危险动词的表单提交 | 可实际执行，用中性测试值（如 `test`、空串）填充必填项 |
| T3 危险操作**只记录** | POST/PUT/PATCH/DELETE 目标（由 route 拦截判定）、裸按钮点击（含文本命中 搜索/查询/筛选/查看/详情/下一页 等安全动词白名单的按钮）、按钮文本含 删除/提交/发布/审批/支付/重置/导出全部/清空/停用/启用 等黑名单动词、`<form method=post>`、文件上传控件、登出按钮 | **绝不点击**；完整记录（选择器、文本、所在页面、预估语义）进"未执行动作清单" |

- **T2 收窄理由**（2026-09-28 架构审查确定）：① `method` 是客户端自报属性，服务端 GET 端点完全可能实现状态变更（遗留系统常见）；② 裸按钮点击的真实行为由所绑 JS 决定，其副作用（发请求、改 localStorage、开新窗口）无法撤销；③ 因此"SAFE_VERBS（搜索/查询/筛选/查看/详情/下一页 等）命中但非显式 GET 表单"的裸按钮**一律降为 T3 只记录**，动词表仅作证据采集与语义解释之用，不作为执行依据。
- T2 执行同样受 route 拦截保护（见 REQ-SU-007），拦截即降级为 T3 记录。
- 分级结果与依据（命中的规则名）逐条落库，文档可解释"为什么没点这个按钮"。

**验收标准**：
- AC1：分级器单测：给定 fixture HTML，T1/T2/T3 分类与预期表逐条一致——含显式 GET 表单（T2）、form 内含危险动词的 GET 表单（T3）、`<form method=post>`（T3）、SAFE_VERBS 命中的裸按钮（T3）、DANGER_VERBS 命中（T3）的中英文动词表用例。
- AC2：遍历全程零 T3 执行——以 route 拦截日志断言 POST/PUT/PATCH/DELETE 请求发出数 == 0（被拦截的除外，且拦截数单独计数进报告）。
- AC3：T2 表单填充不使用危险值（值长度 ≤ 20、纯字母数字），单测断言。
- AC4：SAFE_VERBS 命中但不满足"显式 GET 表单"条件的裸按钮点击，分级结果必须为 T3，单测断言（动词命中不得提升为可执行档位）。

#### [REQ-SU-007] 破坏性请求网络级拦截（MUST）

Playwright `page.route("**/*")` 层实现最后防线：凡 method ∈ {POST, PUT, PATCH, DELETE}（仅 REQ-SU-006 定义的 T2 显式 GET 表单天然不属于此集合）一律 `route.abort()` 并记录 `{url(只存 path、query 值键名化), method, post_data(脱敏), triggered_from_page}`。同时：

1. **域名白名单**：仅允许访问 `system.allowed_origins`（默认 = base_url 同源 + 显式配置项）；白名单外跳转一律拦截并记录，防止遍历把 agent 引到外部站点。
2. 下载（`Content-Disposition`）、新窗口（`context.on("page")`）默认拒绝并记录。

**验收标准**：
- AC1：fixture 页面放置一个"删除"按钮（真实会发 POST），运行后断言该请求被 abort 且出现在拦截日志与 T3 清单中，后端（fixture server 计数端点）收到 POST 数为 0。
- AC2：白名单外链接被拦截且记入 `state/blocked_origins.json`。

#### [REQ-SU-008] 覆盖预算与请求限速（MUST）

1. 预算参数：`--max-pages`（默认 100）、`--max-depth`（默认 6）、`--max-actions-per-page`（默认 30）、`--time-budget-minutes`（默认 60）。任一预算耗尽 → 优雅停止、正常产出文档并在"未覆盖清单"标注"因预算耗尽未探索的 frontier 节点数与示例"。
2. 全局限速：相邻页面动作间隔 ≥ `--delay-ms`（默认 1500ms）；对同域并发页签固定为 1（避免打垮老系统）。
3. 单页面硬超时 `--page-timeout-ms`（默认 30000），超时记录为 `page_timeout` 不阻塞整体。

**验收标准**：
- AC1：fixture 站点 30 页、`--max-pages 5` → 恰好 5 页完成、frontier 剩余数正确写入报告。
- AC2：以时间戳断言相邻动作间隔 ≥ 配置值（允许 ±10% 抖动）。
- AC3：预算耗尽路径与"正常完成"路径产出文档均完整（第 10 节内容不同）。

### 3.4 API 观测

#### [REQ-SU-009] request/response 监听与结构化记录（MUST）

1. 遍历全程监听 `request` / `response` 事件，仅记录白名单资源类型（XHR/fetch、document），忽略图片字体等静态资源正文。
2. 每条记录：`{url_path(去 query 值保留键名), method, status, request_shape, response_shape, observed_on_page, ts}`；**headers 整段不落盘**（2026-09-28 架构审查：shape 仅含 body + status + content-type 三要素，规避 Authorization/cookie 经 headers 快照泄露）；`request_shape/response_shape` 为**结构摘要**（JSON → 键路径 + 类型 + 示例值脱敏截断，非 JSON → content-type + 前 200 字符脱敏文本），单条上限 8KB。
3. 同源聚合：同一 `path + method` 归并为一个 API 端点，多次观测合并样本（≤5 样本/端点）。
4. 全部记录脱敏后落 SQLite `api_observations` 表。

**验收标准**：
- AC1：fixture 页面触发一次带 JSON 响应的 GET fetch，断言端点记录含正确 path/shape，Authorization/cookie 值不存在于任何落盘记录。
- AC2：同端点多次观测被聚合为 1 端点 + 多样本，样本数 ≤ 5。
- AC3：response 为图片/二进制时只记 content-type 与大小，不落正文。

### 3.5 数据库只读内省

#### [REQ-SU-010] DB 连接只读边界（MUST）

1. 连接建立后立即执行会话级只读加固：MySQL `SET SESSION TRANSACTION READ ONLY`（并验证 `tx_read_only`/`information_schema` 生效）、PostgreSQL `SET default_transaction_read_only = on` + `BEGIN READ ONLY`。
2. 语句执行走**白名单前置校验器**：仅允许以 `SELECT` / `SHOW` / `EXPLAIN` 开头且不含多语句（拒绝 `;` 拼接，注释剥离后校验）的语句；一切 DDL/DML/DCL/TCL 语句在客户端即拒绝，严禁 `drop_all()` 及任何 DROP/TRUNCATE 路径。
3. 采样查询一律带 `LIMIT`（默认 ≤ 20 行/表）且仅选取必要列。

**验收标准**：
- AC1：只读校验器单测：黑名单语句（INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/GRANT/CALL/多语句注入 `SELECT 1; DROP TABLE x`）100% 拒绝，白名单语句 100% 通过。
- AC2：对真实 fixture DB（docker 或 sqlite 替身不适用于 mysql 方言，测试用真实 MySQL/PG 容器或 skip 标记 + CI 说明）执行完整内省，审计日志确认服务端仅收到只读语句。
- AC3：代码中不存在任何绕过校验器的直连 `cursor.execute`（静态扫描/审查项）。

#### [REQ-SU-011] Schema 结构清单采集（MUST）

- MySQL：`information_schema.{TABLES,COLUMNS,KEY_STATISTICS,REFERENTIAL_CONSTRAINTS,STATISTICS,VIEWS,ROUTINES}`；
- PostgreSQL：`information_schema` + `pg_catalog`（`pg_class`/`pg_attribute`/`pg_constraint`/`pg_index`/`pg_description` 注释）。

采集：表/视图清单、列（类型、可空、默认值、注释）、主键、显式外键、唯一/普通索引、行数估算（`information_schema.TABLES.table_rows` / `pg_class.reltuples`）。配置了 `schemas` 列表则限定范围，否则排除系统库（`mysql`/`information_schema`/`performance_schema`/`sys`/`pg_catalog`/`pg_toast`）。

**验收标准**：
- AC1：fixture DB（≥8 表、含显式 FK、含视图、含列注释）→ 清单完整：表数、列数、FK 数与预期一致；PG 注释被采集。
- AC2：跨 schema 隔离：限定 `schemas=["app"]` 时不含其他 schema 的表。

#### [REQ-SU-012] 表采样与数据脱敏（MUST）

1. 每表采样 `--sample-rows`（默认 10，上限 50）行，按主键排序取头部 +（无主键则顺序）采样；行数 < 采样数的表全量采样。
2. **脱敏规则（落盘前逐列执行）**：列名或注释命中敏感词典（password/passwd/pwd/secret/token/api_key/phone/mobile/email/id_card/bank/card/address/身份证/手机号/邮箱/银行卡/地址）→ 值替换为 `<REDACTED:类型>` 并保留格式信息（长度、字符类别）；BLOB/超长文本截断为 `<BLOB size=N>`。
3. 采样目的：供 LLM 判定枚举语义（如 `status` 列实际值域）、时间语义、软删除模式（`deleted_at`/`is_deleted` 分布）。

**验收标准**：
- AC1：fixture 表含 phone/email/password 列 → 落盘采样中对应列值全部为 `<REDACTED:...>` 形态，原始值 0 残留。
- AC2：采样行数断言：大表 ≤ 配置值、小表全量。
- AC3：脱敏保留信息断言：测试可验证长度保留规则（如电话只保留长度区间）。

#### [REQ-SU-013] 隐式外键三重预筛（SHOULD）

对**无显式 FK** 的列执行三重预筛（全部脚本层确定性计算）：

1. **命名约定**：列名匹配 `{X}_id` / `{X}Id` / `id_{X}` → 候选目标表 `X`（含单复数归一）。
2. **类型兼容**：两侧列类型族一致（整型族/字符串族/UUID）。
3. **值域包含度**：子表非空去重值中，能在父表键中找到的比例 ≥ 阈值（默认 0.85）。

包含度计算用分批 `WHERE NOT EXISTS (SELECT 1 FROM parent p WHERE p.key = c.col)` 形态的计数版本控制采样规模（子侧 ≤ 1000 个去重值；2026-09-28 架构审查：禁用 `NOT IN (SELECT …)`，规避子查询含 NULL 时三值逻辑导致包含度恒 0 的陷阱）。输出候选隐式 FK 列表，每条带 `prescreen_score`（三重命中数加权）与证据（命中规则、包含度），全部标注为"推断，需确认"。

**验收标准**：
- AC1：fixture 设计 3 例——真隐式 FK（order.user_id→users.id，包含度 1.0）、假候选（name 列恰好同名但类型不符）、部分重叠（包含度 0.5 不达标）→ 输出恰好只有第 1 例达标，第 2 例止步于规则 2，第 3 例止步于规则 3，各有规则命中记录。
- AC2：包含度查询语句通过 REQ-SU-010 校验器（只读性）。

### 3.6 Redis 只读采集

#### [REQ-SU-014] Redis SCAN 采样与白名单命令（MUST）

1. 仅使用只读命令白名单：`SCAN`、`TYPE`、`TTL`/`PTTL`、`MEMORY USAGE`、`GET`、`HGETALL`(≤ `hscan` 大 hash 降级)、`LRANGE`(截断)、`SMEMBERS`(截断)、`Z RANGE`→`ZRANGE`(截断)、`OBJECT ENCODING`。实现层以命令名硬编码白名单校验，其余命令（含 `KEYS`、`FLUSHALL`、`SET`、`DEL`、`CONFIG`、`DEBUG`、`EVAL`）一律拒绝。
2. 键枚举：`SCAN count=200 MATCH <白名单前缀>` 游标遍历，`key_allowlist` 未配置时全 SCAN 但受 `--redis-max-keys`（默认 5000）预算限制；SCAN 期间不持锁、不 `MONITOR`。
3. 每键采集：类型、TTL、encoding、序列化大小、值样例（截断 512B 并过统一 `redact()`——缓存值常含会话用户信息）。

**验收标准**：
- AC1：命令白名单单测：白名单外命令 100% 拒绝（含 KEYS）。
- AC2：fixture Redis 灌入 3 类键（string/hash/list，含 `sess:{uuid}` 500 个）→ SCAN 完整收敛（游标归 0）、预算截断行为正确、样例值经脱敏。
- AC3：Redis 不可达/凭据错误 → 记降级、不阻塞其他透镜（衔接 AC3 of REQ-SU-003）。

#### [REQ-SU-015] 键模式聚类（SHOULD）

将枚举出的键名做模式聚类：UUID/数字/日期/长 hex 段替换为占位符（`{uuid}`/`{n}`/`{date}`/`{hex}`），按冒号/下划线分段对齐生成模式（如 `sess:{uuid}`、`cache:user:{n}:profile`）。每模式输出：键数量、TTL 分布（无 TTL 占比单列——潜在泄漏信号）、类型分布、脱敏值样例 ≤3 条。

**验收标准**：
- AC1：fixture 键集 → 聚类结果与预期模式表一致（数量、占位符正确）。
- AC2：模式输出不含任何完整原始键值中的敏感内容（仅键名模式 + 脱敏样例）。

### 3.7 关联分析

#### [REQ-SU-016] 页面 ↔ API ↔ 表三角关联（MUST）

脚本层产出**确定性关联证据**（不做语义结论）：

1. 页面→API：`api_observations.observed_on_page` 直接得到页面触达的端点集合；按读（GET）/观测到的非 GET 被拦截请求分组。
2. API→表（启发证据）：端点 path 段与表名/列名做归一匹配（`/api/orders` ↔ `orders`，含单复数/驼峰下划线归一）；响应 JSON 键名与列名重合度 ≥ 阈值（默认 0.5）记一条证据。
3. 最终"页面功能 ↔ 业务实体 ↔ 数据表"的结论由宿主 LLM 基于上述证据 + 采样数据做出，每条结论必须带 `confidence ∈ {high, medium, low}` 与 `evidence_refs`（指向 `api_observations`、`db_tables`、`pages` 表记录 id）；`confidence=low` 或缺证据的结论在文档中显式标注 **"⚠ 待人工确认"**。

**验收标准**：
- AC1：fixture（页面→fetch `/api/orders`→返回键与 `orders` 表列高度重合）→ 确定性证据表中含正确配对与重合度值。
- AC2：结论 schema 校验：LLM 回填的每条结论缺 `confidence` 或 `evidence_refs` 时文档生成器拒绝并报错（不允许无证据结论入文档）。

#### [REQ-SU-017] Redis 键模式 ↔ 实体关联（SHOULD）

以命名匹配为证据：键模式段（`user`/`order`）↔ 表名/实体名；值样例中的 JSON 键 ↔ 列名。输出模式级关联候选（同样带 confidence 与 evidence_refs），并标注无 TTL 常驻键、疑似缓存穿透旁路等观察项（仅观察，不断言）。

**验收标准**：
- AC1：fixture `cache:user:{n}:profile` + `users` 表 → 产出该模式↔users 关联候选且证据含两个引用；不存在的实体名模式 → 不产出关联。

### 3.8 文档生成

#### [REQ-SU-018] 《系统功能理解文档》生成（MUST）

从状态库 + LLM 语义回填结果渲染 Markdown 主文档，**固定 10 节结构**：

1. **系统概览**：系统名/入口/技术栈指纹观察（server header、HTML 特征）/本次运行参数与预算消耗统计/预检摘要。
2. **功能地图**：按导航层级组织的功能清单（页面 → 可执行动作 → 预估业务语义），标注动作分级。
3. **导航图**：页面节点-边 Mermaid `graph TD`（>60 节点时按模块聚类折叠成子图），附孤点清单。
4. **数据模型**：逐表卡片（列/类型/注释/PK/显式 FK/索引/行数估算/隐式 FK 候选）+ Mermaid `erDiagram`（显式 FK 实线、隐式推断虚线并标"推断"）。
5. **UI ↔ 数据映射**：页面/功能 ↔ API ↔ 表（↔ Redis 键模式）映射矩阵表，每条带 confidence 与证据引用。
6. **缓存与中间件**：Redis 键模式表（数量/TTL 分布/类型/关联实体）、未采集说明。
7. **业务规则汇编**：LLM 从采样数据与页面文案归纳的规则（枚举语义、状态机候选、软删除、唯一性现象），逐条带证据与 confidence，低置信标"待人工确认"。
8. **API 面**：端点清单（path/method/触达页面/请求响应结构摘要/匹配到的表）；被拦截的写请求观测清单（这是"系统有写能力但我们未验证"的显式声明）。
9. **证据附录**：证据索引（编号 → 来源表/采集时间/脱敏状态）、脱敏声明、运行环境与方法论声明。
10. **未验证推断与未覆盖清单**：a) 全部低置信/推断结论汇总表；b) 未点击的 T3 动作清单；c) 预算耗尽 frontier；d) 因 4xx/5xx/超时未成功页面；e) 缺失透镜（无 DB/Redis 配置）。

**验收标准**：
- AC1：对完整 fixture 运行产物做章节断言：10 节标题齐全、顺序正确。
- AC2：Mermaid 语法校验（正则级 + mermaid-cli 可选校验），ER 图中隐式 FK 边带"推断"标注。
- AC3：文档全文 grep 无敏感值（复用 REQ-SU-002 AC2 管线）；每条 confidence=low 结论可见"待人工确认"标记。
- AC4：同一状态库重复渲染幂等（除时间戳外内容稳定），保证可重生成。

### 3.9 断点续跑

#### [REQ-SU-019] SQLite 状态机与断点续跑（MUST）

1. 状态库存放 `docs/system-understanding/<system_id>/state/understanding.sqlite`（标准库 sqlite3，WAL 模式），核心表：`run_meta`、`pages`、`page_actions`、`edges`、`api_observations`、`db_tables`、`db_samples`、`implicit_fk_candidates`、`redis_keys`、`redis_patterns`、`relations`、`blocked_events`、`findings`（LLM 结论回填）。
2. 所有采集先查状态库去重（已完成的页面/表/键不再重复执行）——幂等。
3. 中断恢复：启动时若存在未完成 run（`status=interrupted/running` 且非本次进程持有），询问策略：`--resume`（默认继续）/ `--fresh`（归档旧状态后重跑）。SIGINT 捕获 → flush 状态、`status=interrupted` 后以退出码 **130**（128+SIGINT 惯例）退出，使调用方可确定性区分"被中断"与"正常完成（0）"。
4. 进程互斥：**唯一机制**为 SQLite WAL 模式下对 `run_meta` 的条件更新——在 `BEGIN IMMEDIATE` 事务内原子检查并改写 `locked_by`（pid）与 `heartbeat_ts`，锁状态即数据库行状态，不存在独立锁文件（2026-09-28 审查：废除 fcntl/run.lock 双机制，消除"锁文件与库状态不一致"）；心跳超时（>60s 未更新）视为陈旧锁可接管。

**验收标准**：
- AC1：fixture 运行中途 SIGINT → 重启 `--resume`：已完成页面数正确继承、被中断页重跑、总动作数 < 两次全新运行之和（证明未重复）。
- AC2：`--fresh` 归档旧 state 目录为 `state.archive.<ts>/` 后重跑。
- AC3：陈旧锁接管单测（伪造心跳过期记录）；双进程并发启动第二个明确报错退出。

### 3.10 CLI 接口

#### [REQ-SU-020] CLI 入口与参数（MUST）

新增 `scripts/system_understanding.py`，沿用 `project_understanding.py` 骨架风格（argparse + 单一主类 `SystemUnderstanding` + `generate()` / `save()` 方法，输出 JSON + Markdown）。参数清单：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--config <path>` | 无 | 凭据/目标 JSON 配置文件（见 REQ-SU-001） |
| `--system-url` / `--username` / `--password` | - | 系统凭据 CLI 通道（覆盖配置文件） |
| `--db-url` | 无 | `mysql://user:pass@host:3306/db` 或 `postgresql://...`（覆盖配置文件；出现在 shell 历史属用户自担风险，文档提示优先用配置文件/env） |
| `--redis-url` | 无 | `redis://:pass@host:6379/0` |
| `--out <dir>` | `docs/system-understanding/` | 输出根目录 |
| `--system-id` | 由 host 派生 | 输出子目录名 |
| `--max-pages` / `--max-depth` / `--max-actions-per-page` / `--time-budget-minutes` | 100/6/30/60 | 覆盖预算 |
| `--delay-ms` / `--page-timeout-ms` | 1500/30000 | 限速与超时 |
| `--sample-rows` | 10 | DB 采样行数 |
| `--redis-max-keys` | 5000 | Redis 键预算 |
| `--allowed-origins` | base_url 同源 | 追加白名单域（逗号分隔） |
| `--headed` | false | 有头调试模式 |
| `--storage-state <path>` | 无 | 人工已登录态旁路 |
| `--resume` / `--fresh` | resume | 断点策略 |
| `--skip-llm-phase` | false | 只跑确定性采集，产出"待 LLM 回填"骨架文档（配合宿主 LLM 两阶段工作流） |
| `--render-only` | false | 仅渲染：输出目录已存在 `completed`/`interrupted` 状态库、且 `understanding.json` 含 `findings` 段时，跳过阶段 0~3（不启动浏览器、不连库），直接校验并入库 findings（全量替换，`status=proposed`）+ 重渲染全部产物；findings 校验失败 → 退出码 2。用于宿主 LLM 回填后的收口渲染 |
| `--verbose` | false | 调试日志（仍全程脱敏） |

退出码：0 成功；2 参数/配置错误（含 `--render-only` 时 findings 校验失败）；3 致命目标不可达；4 登录失败；5 依赖缺失；**130** 收到 SIGINT 中断（状态库已存 `status=interrupted`，可 `--resume` 续跑，见 REQ-SU-019.3）。

**验收标准**：
- AC1：`--help` 列出全部参数含中文说明；非法参数 argparse 标准报错。
- AC2：参数优先级单测（CLI > env > config）。
- AC3：`--skip-llm-phase` 产出骨架文档且第 5/7 节标注"待 LLM 语义回填"，不虚构结论。
- AC4：各退出码路径可由集成脚本触发验证（含 SIGINT → 130、`--render-only` findings 校验失败 → 2）。

### 3.11 降级行为（诚实报错，禁 mock）

#### [REQ-SU-021] 软依赖缺失降级（MUST）

按 `requirements.txt` 惯例注释化声明软依赖（`# playwright>=1.40.0`、`# pymysql>=1.1.0`、`# redis>=5.0.0`、`# psycopg2-binary>=2.9`），运行时 try-import：

| 缺失依赖 | 行为 |
|---|---|
| playwright | **致命降级**：无法执行任何 UI 透镜。中文报错："未安装 playwright（pip install 'playwright>=1.40.0' && playwright install chromium），UI 遍历与 API 观测不可用"；退出码 5，不产出任何编造的 UI 结论。 |
| pymysql / psycopg2 | 按 `database.engine` 判定：对应驱动缺失 → 跳过 DB 透镜，报错 + 文档数据模型节标"未采集：驱动缺失（给出安装命令）"；若仅有 Redis 且 playwright 可用，其余流程继续。 |
| redis | 跳过 Redis 透镜，同上。 |

通用原则：**缺失即声明缺失**——错误消息含安装命令与影响面；文档"未覆盖清单"与实际能力严格一致；绝不以空数据/假数据冒充已采集结果（CONSTITUTION 禁 mock 红线）。

**验收标准**：
- AC1：单测（monkeypatch import 失败路径）三种缺失组合的报错文案、退出码、文档标注均符合上表。
- AC2：playwright 缺失时断言输出目录中不存在 UI 章节的"正文"（只有显式缺失声明）。

## 4. 非功能需求

| 编号 | 需求 |
|---|---|
| [NFR-SU-001] 限速与礼貌性 | 全局同域串行 + `--delay-ms` 间隔 + 预算上限；对目标系统的等效 QPS ≤ `1/delay`（默认 ≤ 0.67 QPS 页面级动作），任何循环内不得绕过统一限速器。 |
| [NFR-SU-002] 安全红线（不可协商） | ① 凭据不进 LLM 上下文、不落盘明文（统一 redact 管线 + 0600 权限 + 落盘 DTO 类型约束）；② DB 会话只读 + 语句白名单校验器，严禁 DDL/DML/DROP/drop_all；③ Redis 命令硬编码只读白名单；④ 浏览器网络层拦截全部非 GET 与白名单外域；⑤ 危险按钮零点击。红线违反即视为 P0 缺陷，任何 AC 让位于红线。 |
| [NFR-SU-003] 凭据存储策略 | 运行期：内存持有，进程退出即消失。落盘：仅 storage_state（0600、含会话令牌→列入敏感文件清单、文档中提示用户用后清理）；配置文件由用户自管，文档明确推荐 `chmod 600` 与专用低权限只读 DB 账号（推荐口径：DB 账号只需 SELECT 权限，Redis 账号如支持 ACL 只读 keyspace 权限）。 |
| [NFR-SU-004] 失败隔离 | 四透镜（UI/API/DB/Redis）故障互相隔离：单透镜失败不终止整体；单页/单表/单键失败记录后跳过；失败均汇入第 10 节。 |
| [NFR-SU-005] 性能与资源 | 100 页遍历内存峰值 ≤ 1GB（DOM 剪枝后快照不落全量 HTML，单快照 ≤ 64KB）；SQLite 单 run ≤ 200MB（受观测记录 8KB/条、样本上限约束）。 |
| [NFR-SU-006] 可观测性 | 结构化日志（logging，key=value 风格，中文消息），运行结束打印预算消耗摘要（页面数/动作数/拦截数/耗时）。 |
| [NFR-SU-007] 兼容性 | Python ≥ 3.8；MySQL ≥ 5.7、PostgreSQL ≥ 11、Redis ≥ 5.0（依赖 `SCAN`/`MEMORY USAGE`）；macOS/Linux。 |
| [NFR-SU-008] 可解释性 | 每个"做了/没做"的决策（动作分级、跳过、拦截、推断）均有规则名 + 数据依据可查。 |

## 5. 输出产物清单

```
docs/system-understanding/<system_id>/
├── UNDERSTANDING.md              # 《系统功能理解文档》主文档（10 节，REQ-SU-018）
├── understanding.json            # 结构化全量结果（机读，脱敏后，供宿主 LLM/下游角色消费）
├── summary.json                  # 运行摘要（预算消耗、透镜完成度、confidence 分布统计）
├── state/
│   ├── understanding.sqlite      # 断点续跑状态库（REQ-SU-019）
│   ├── storage_state.json        # 浏览器会话态（0600，敏感，用后清理）
│   ├── preflight.json            # 预检报告（REQ-SU-003）
│   └── blocked_origins.json      # 白名单外域拦截记录（进程互斥由状态库 run_meta 条件更新实现，无独立锁文件）
├── snapshots/                    # 页面语义骨架快照（剪枝后，脱敏，≤64KB/页）
│   └── <page_id>.json
├── diagrams/
│   ├── navigation.mmd            # 导航图 Mermaid 源
│   └── er.mmd                    # ER 图 Mermaid 源
├── evidence/
│   └── evidence-index.json       # 证据编号 → 状态库记录映射
└── logs/
    └── run-<timestamp>.log       # 脱敏运行日志
```

清单同步项（交付时必须更新）：`skill-manifest.yaml`、`skills-index.json`、`claude-code-skill.json`、`registry/skills.json`、`SKILL.md` 增加 SU 能力条目；`requirements.txt` 追加注释化软依赖行。

## 6. 测试验收标准

### 6.1 单元测试（`scripts/tests/`，unittest 框架）

| 测试文件 | 覆盖需求 | 关键用例 |
|---|---|---|
| `test_su_config.py` | REQ-SU-001/002/003、NFR-SU-003 | 三级配置优先级、缺字段报错、engine 非法值、redact 管线（dict/URL/文本/嵌套）、预检降级分类 |
| `test_su_url_key.py` | REQ-SU-005 | url_key 规范化 12+ 边界（参数乱序/追踪参数/数字段/UUID/hash 路由/大小写） |
| `test_su_action_tier.py` | REQ-SU-006 | T1/T2/T3 分级表逐条断言、中英文动词表、form method 判定 |
| `test_su_readonly_guard.py` | REQ-SU-010、NFR-SU-002 | SQL 白名单校验器：60+ 语句正负例（含多语句注入、注释绕过） |
| `test_su_implicit_fk.py` | REQ-SU-013 | 三重预筛三例止步规则断言、包含度阈值边界（0.85） |
| `test_su_redis_patterns.py` | REQ-SU-014/015 | 命令白名单拒绝集、键模式聚类占位符、预算截断 |
| `test_su_relation.py` | REQ-SU-016/017 | path↔表名归一匹配、响应键↔列重合度、结论 schema（缺 confidence/evidence_refs 拒绝） |
| `test_su_state.py` | REQ-SU-019 | 状态库幂等写入、陈旧锁接管、resume 去重逻辑 |
| `test_su_route_guard.py` | REQ-SU-007/009 | route handler 执行模型：handler 体内零 Playwright API 调用、有界队列满丢弃计数、triggered_from_page 注入、SPA hash 导航观测面为空（2026-09-28 架构审查新增） |
| `test_su_doc_render.py` | REQ-SU-018 | 10 节完整性、Mermaid 基本语法、待人工确认标记、渲染幂等 |
| `test_su_degrade.py` | REQ-SU-021 | 三种软依赖缺失组合的报错/退出码/文档标注 |

### 6.2 集成测试（`scripts/tests/scripts/run_system_understanding.sh`）

场景矩阵（fixture：本地 aiohttp/Flask 测试站 + MySQL/PG 容器 + Redis 容器；无容器环境时 DB/Redis 场景输出显式 skip 声明而非假通过）：

1. **全链路**：fixture 站点（≥12 页、含登录、含 fetch API、含"删除"按钮陷阱）+ 双 DB + Redis → 一次运行产出完整 10 节文档；后端写请求计数器 == 0。
2. **安全红线回归**：运行后全目录（含 sqlite、日志、storage_state 以外文件）敏感串扫描 0 命中；拦截日志含被 abort 的 POST 记录。
3. **断点续跑**：运行中 SIGINT → `--resume` → 断言继承进度且动作不重复；`--fresh` 归档行为。
4. **预算**：`--max-pages 3` → 恰好 3 页 + frontier 报告。
5. **降级**：`--skip-llm-phase`；移除 redis 容器 → 文档"缓存"节显式缺失声明。
6. **凭据错误路径**：错误密码 → 退出码 4，无半成品文档。

### 6.3 文档级验收（先文档后代码闭环）

- 代码完成后逐条回勾本 PRD 全部 REQ/NFR 条目，未覆盖项必须给出书面理由或补齐；
- 全量单测 + 集成脚本通过；
- 五个清单文件 + `requirements.txt` 同步核对（脚本化 diff 校验）。

---

## 7. 与既有体系的关系

- **宿主 LLM 分工契约**：脚本层只做确定性采集/统计/脱敏/渲染；页面语义命名、业务规则归纳、映射结论由宿主 LLM 基于 `understanding.json` 回填 `findings` 表后重新渲染文档。脚本层绝不内置模型调用。
- **与 project_understanding.py 的关系**：并列互补——有源码用静态理解，黑盒运行系统用本能力；文档第 1 节建议两者证据合并审阅（如可获得源码）。
- **多智能体协作挂点**：UNDERSTANDING.md 面向角色分发——架构师读 1/3/4 节、独立开发者读 2/5/8 节、测试专家读 2/7/10 节、UI 设计师读 2/3 节。

## 8. 风险与开放问题

| 风险 | 缓解 |
|---|---|
| 老系统会话脆弱，遍历时被风控 | 默认 1500ms 间隔 + 串行 + 可配 UA；文档提示先在预发环境试跑 |
| hash 路由 SPA 去重不准 | url_key 保留 hash path；提供 `--max-depth` 兜底；孤点与疑似重复进第 10 节 |
| 采样数据本身含敏感 PII | 列名/注释词典 + 值形态（手机号/身份证正则）双通道脱敏，值形态命中优先级更高 |
| LLM 过度推断 | 结论 schema 强制 evidence_refs + confidence；第 10 节集中披露全部低置信项 |

## 9. 附录：需求条目索引

- 功能需求：REQ-SU-001 ~ REQ-SU-021，共 **21** 条（MUST 17 条 / SHOULD 4 条：013、015、017，及其余标注）。
- 非功能需求：NFR-SU-001 ~ NFR-SU-008，共 8 条。
- 排除项：OUT-1 ~ OUT-9，共 9 条。
