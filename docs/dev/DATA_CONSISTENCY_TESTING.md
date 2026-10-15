# v2.13 数据一致性校验测试设计（数据契约 → DB 预期 → 业务规则 → 严格对照）

> **版本**：v2.13 · **状态**：设计定稿
> **上游**：v2.12 浏览器端页面功能与视觉测试闭环（本文档是其数据层深化，不推翻其结构）
> **核心命题**：页面断言通过 ≠ 数据落对了。UI 显示、API 响应、DB 落库三方必须逐字段严格一致，
> 且业务数据规则（唯一性/不变式/状态机/金额守恒）必须在每一笔写操作后被确定性校验。

---

## 1. 需求与问题定义

v2.12 已解决"操作点级功能测试 + 全程留痕"，但对**数据正确性**只有弱校验：
断言停留在 UI 文本/DOM/网络状态码层面，没有回答三个问题：

| # | 问题 | v2.12 现状 | v2.13 目标 |
|---|------|-----------|-----------|
| Q1 | 页面输入的数据，落库后字段值是否逐字段一致？ | 无 DB 层校验 | DC 契约三方对照（UI ⇄ API ⇄ DB） |
| Q2 | 操作前后 DB 状态变化是否符合预期（行数/字段值/关联行）？ | 无 | before/after 快照 + 行级 diff 断言 |
| Q3 | 业务数据规则（唯一性、金额守恒、状态迁移合法性）是否被破坏？ | 无 | 规则确定性求值，落盘可复核 |

**深入业务流程**的具体含义：沿每条 FLOW 的写操作追踪 `表单字段 → API payload → 服务层
处理 → DB 列 → 回读展示` 全链路，在每个环节声明数据预期，测试时做严格一致性对照。

## 2. 架构决策（架构师审查结论）

### 2.1 分层与复用边界

```
┌─────────────────────────────────────────────────────────────┐
│ 提示词层（宿主 LLM）                                          │
│  browser-test-planner.md（细化 DC-* 数据契约 + DB 预期）        │
│  browser-test-execution.md（DB 快照采集 + 对照执行协议）        │
├─────────────────────────────────────────────────────────────┤
│ 确定性脚本层（本次新增/扩展，纯函数可单测）                       │
│  scripts/data_test/db_probe.py  —— 只读快照引擎（新建）         │
│  scripts/data_test/data_rules.py —— 业务规则求值器（新建）       │
│  scripts/browser_test_recorder.py —— db-snapshot/dc-check/    │
│       rule 断言（扩展）                                        │
├─────────────────────────────────────────────────────────────┤
│ 安全复用（零修改）                                             │
│  su/db_guard.py::ReadOnlyGuard —— 唯一语句执行通道（红线②）     │
│  su/config.py::DatabaseConfig  —— 连接配置模型                 │
│  su/deps.py                    —— 软依赖探测与诚实降级          │
└─────────────────────────────────────────────────────────────┘
```

**决策依据**：
- ReadOnlyGuard 是全包唯一 cursor.execute 通道且带完整防绕过状态机（注释剥离、
  INTO OUTFILE 封堵、多语句拒绝、参数化传参），db_probe 自建连接层等于复制安全面，
  必须复用。
- db_probe / data_rules 为独立纯函数库（与 recorder 解耦），既供 recorder 内部调用，
  也可被宿主 LLM 以 CLI 直接驱动。
- **不修改** su/ 包任何文件（SU 是独立采集能力，测试闭环只消费其守卫）。

### 2.2 新增 ID 维度：DC-*（数据契约，Data Contract）

在 v2.12 的 FLOW/OP/DT/TC 之上增加数据契约维度：

```
DC-{FLOW编号}-{序号}
  环节链:   {表单字段} → {API 字段} → {DB 表.列} → {回读展示位}
  字段映射: [{ui_field, api_field, db_column, transform}]
            # transform 是显式声明的合法变换（如 trim/大小写/金额分↔元），
            # 必须给出服务层转换函数 文件:行号 证据，禁止隐式默认
  DB 预期:  {表, 操作类型(insert/update/soft-delete), 预期行谓词, 预期列值集合}
  规则引用: [DR-*]     # 本契约涉及的业务规则
  断言点:   UI 回读 / API 响应体 / DB 快照 三层各自的可断言描述
```

**强制约束**：
- 每条写操作 OP-*（提交/保存/删除/审批）必须挂 ≥1 条 DC-*，否则规划评审 BLOCKER
- transform 必须显式声明并给出代码证据——"看起来不一样但其实有转换"是
  数据对照最常见的假阳性来源

### 2.3 快照模型（db_probe）

声明式快照清单 db-checks.json（规划阶段产出，执行阶段消费）：

```json
{
  "check_id": "DC-F01-01",
  "phase": "after",
  "queries": [
    {
      "label": "order_row",
      "sql": "SELECT id, status, amount_cents FROM orders WHERE order_no = %s",
      "params": ["ORD-20261015-001"]
    }
  ]
}
```

- 每条 SQL 必须通过 ReadOnlyGuard.validate（非 SELECT/SHOW/EXPLAIN 直接拒绝）
- 快照结果 = 确定性 JSON（列名排序、行按主键/首列排序后落盘），before/after
  两份快照做**行级 diff**（added / removed / changed，changed 精确到列值）
- 参数化传参走 DB-API 原生占位符（%s / ?），杜绝字符串拼接

### 2.4 业务规则模型（data_rules）

规则以声明式 JSON 表达，recorder 确定性求值（不依赖宿主 LLM 口算）：

| 规则类型 | 代号 | 语义 | 求值输入 |
|---------|------|------|---------|
| 唯一性 | UNIQ | 列值在表内唯一（按谓词范围） | 快照查询结果 |
| 不变式 | INV | 列满足表达式（金额>=0、非空、枚举域） | 快照查询结果 |
| 守恒 | CONS | 跨表/跨列等式（余额=收入-支出） | 多组快照结果 |
| 状态机 | SM | status 迁移 ∈ 合法迁移表 | before/after 快照对 |
| 引用完整 | REF | 外键指向行存在（软删除场景） | 快照查询结果 |

表达式求值为受限安全子集（比较、算术、in/null/regex），**不使用 eval()**，
自研 tokenizer + 递归下降求值，杜绝注入面。

### 2.5 recorder 扩展（向后兼容）

- 断言 kind 枚举扩展：db（快照对照）与 rule（规则求值），旧 kind 不变
- 新子命令：
  - db-snapshot：执行快照清单 → 落盘 db-snapshots/{case-id}-{phase}.json
    （经 db_probe，连接配置走 --db-config 文件，凭据不进命令行与日志）
  - dc-check：before/after 行级 diff 对照 DC 预期 → 生成数据断言写入对应用例
  - rule：data-rules.json DR-* 规则求值 → 生成规则断言写入对应用例
- verify / finalize 校验面扩展：数据断言计入 assertions_total/failed 与门禁

### 2.6 引擎与降级矩阵

| 引擎 | 驱动 | 占位符 | 缺失时行为 |
|------|------|--------|-----------|
| sqlite | 标准库 sqlite3 | ? | 不可能缺失 |
| mysql | pymysql（软依赖） | %s | 诚实降级：标记 BLOCKED(env_failure)，给安装命令，绝不假数据 |
| postgresql | psycopg2（软依赖） | %s | 同上 |

### 2.7 安全红线（继承 SU 红线②并加码）

1. 连接会话一律只读加固（复用 DbSessionHardener；sqlite 用 file:...?mode=ro URI）
2. 所有 SQL 先过 ReadOnlyGuard.validate 再 execute——执行文本 == 校验文本
3. DB 凭据只经 --db-config JSON 文件（chmod 600 提示）进入内存，不落日志、
   不进快照文件、不进 summary 报告（落盘前过 redact）
4. 查询超时 + 行数上限双闸（默认 30s / 5000 行），超限报错而非静默截断

## 3. 工作流嵌入点（v2.12 四阶段内增量）

```
Phase 1 规划：Step 3 数据测试集 → 扩展"DB 预期数据"（期望行数/列值/不变式）
             新增 Step 3.5 数据契约 DC-* + db-checks.json 快照清单
Phase 2 评审：测试专家一票否决项 +1：任一写操作 FLOW 无 DC 契约覆盖
             架构师维度 +1：DB 预期是否可只读查询表达、凭据管理合规
Phase 3 执行：写操作步骤循环 → 操作前 db-snapshot(before) → 浏览器操作 →
             操作后 db-snapshot(after) → dc-check（diff）+ rule（规则）→ 数据断言落盘
             数据不一致阻碍协议：先区分"用例预期错误 vs 疑似缺陷"，
             以代码+文档为准；疑似缺陷绝不改 DB 预期放行
Phase 4 汇总：summary 增加数据维度（DC 覆盖率、数据断言通过率、diff 明细链接）
             门禁扩展：P0 数据断言失败 = 整体 FAIL
```

## 4. 产出物增量

| 文件 | 阶段 | 说明 |
|------|------|------|
| test-data-contracts.json | Phase 1 | DC-* 契约清单（字段映射+DB 预期+规则引用） |
| db-checks.json | Phase 1 | 声明式快照清单（全部只读 SQL） |
| data-rules.json | Phase 1 | DR-* 业务规则声明 |
| {run}/db-snapshots/{TC}-{phase}.json | Phase 3 | 前后快照（脱敏后） |
| {run}/db-diff/{TC}.json | Phase 3 | 行级 diff + 规则求值明细 |

## 5. 测试策略

- data_rules.py：表达式求值全边界（类型不匹配、null、除零、非法字符注入串）
- db_probe.py：sqlite 全真实（tempfile 建库：正确/多行/谓词无命中/SQL 违规拒绝/
  只读 URI 拒写）；mysql/psycopg2 路径以"驱动缺失→诚实降级 BLOCKED"验证
- recorder 扩展：db-snapshot/dc-check/rule 断言的 schema 强制、fail 无 diff 拒绝、
  finalize 数据维度一致性
- 端到端：临时 sqlite + 静态页面流程，完整跑 before→操作→after→diff→门禁

## 6. 兼容与迁移

- v2.12 产物（无 DC 字段）在 v2.13 recorder 下仍合法（DC 字段全部可选），
  finalize 仅对存在 DC 的用例启用数据门禁
- CLI：--db-config 缺省时数据层整体 SKIPPED（登记原因），浏览器功能测试照常进行
