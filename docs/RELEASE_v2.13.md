# Trae Multi-Agent Skill v2.13 发布说明

**版本**: 2.13.0
**发布日期**: 2026-10-15
**提交**: `3796d49` — feat(v2.13): 数据校验闭环——DC 数据契约 / DB 快照对照 / DR 业务规则 / data_gate 数据门禁

---

## 🎯 一句话总结

在 v2.12 浏览器测试闭环之上向下扎到**数据层**——接口 200、页面提示"成功"不等于数据真的落对了库。
v2.13 让**每一条数据一致性结论只能由确定性脚本落盘**：DC 数据契约 → 操作前后 DB 只读快照 →
行级 diff 严格对照 → DR 五类业务规则求值 → data_gate 数据门禁。

---

## ✨ 核心特性

### 1. DC-* 数据契约（表单 → API → DB → 回读 全链路映射）

- 每条字段映射带**代码位置证据**（文件:行号）；任何变换（哈希/脱敏/大小写/拆分/精度）必须
  显式声明 transform 并给证据——**未声明 transform 的字段差异一律判不一致**
- DB 预期：期望表 / 行 match 谓词 / 列值 / 关联行 / 状态迁移
- 快照查询清单 `db-checks.json`：纯只读 SELECT，参数化占位符
- 产出：`test-data-contracts.json` + `db-checks.json` + `data-rules.json`（规划七件套扩展）

### 2. DB 快照采集与行级 diff（确定性内核 `scripts/data_test/`）

- **`db_probe.py`** 只读快照引擎：复用 `su/db_guard.ReadOnlyGuard` 作为**唯一语句通道**
  （仅 SELECT/SHOW/EXPLAIN、多语句拒绝、参数化传参）+ 连接级只读加固
- **`data_rules.py`** 主键对齐行级 diff：added / removed / changed + 列级新旧值 + 聚合对照
- 驱动缺失 / 连接失败**诚实降级** `env_failure`——数据断言 BLOCKED，而非谎报 FAIL/PASS

### 3. DR-* 五类业务数据规则求值

| 类型 | 含义 | 示例 |
|------|------|------|
| UNIQ | 唯一性 | 手机号在 users 表不重复 |
| INV | 不变式 | 订单金额 = Σ 明细金额（表达式求值） |
| CONS | 守恒 | 转账前后账户总额不变 |
| SM | 状态机 | 订单状态迁移只允许合法路径 |
| REF | 引用完整 | 订单明细必关联存在的主订单 |

受限表达式求值器为**自研 tokenizer + 递归下降解析器（非 eval）**，白名单文法：
字段引用 / 字面量 / 比较 / 四则 / `len()` / 聚合 `count/sum/min/max/exists`。

### 4. 记录器扩展（browser_test_recorder.py）

- 新增子命令：`db-snapshot`（快照采集落盘登记）/ `dc-check`（diff 对照 DC 预期）/
  `rule`（DR-* 求值）——数据断言全部由脚本确定性生成并落盘
- `run --db-config` 启用数据层（engine = sqlite / mysql / postgres）
- `case` 新增 `--db-check-before / --db-check-after / --dcs` 声明
- **数据校验不允许静默跳过**：声明 `--db-check-*` 却未落盘快照 → `case end` 直接拒绝收尾；
  声明 DC 未跑 `dc-check` → `finalize` 校验报 problems

### 5. data_gate 数据门禁（finalize）

- `dc_fail` / `rule_fail` > 0、声明的校验缺失、数据通道阻断（env_failure）→ **整体 gate=FAIL**
- summary 新增数据维度：DC 覆盖率 / 数据断言通过率 / env_failure 次数

### 6. prompt 细化与文档

- `browser-test-planner.md`：Step 3 DT 数据组新增 `db_expectation`；新增 Step 3.5 数据契约
  设计、Step 3.6 业务数据规则声明
- `browser-test-execution.md`：DB 数据层预检、单用例循环数据步骤（before 快照 → 动作 →
  after 快照 → dc-check → rule）、数据不一致三查协议（绝不改 DB 预期放行）、DB 通道故障阻碍处理
- 设计文档：`docs/dev/DATA_CONSISTENCY_TESTING.md`（三层架构 / ID 规范 / schema / 只读安全面 /
  门禁与降级语义）

---

## 🧪 测试

- 新增 `tests/test_v213_data_consistency.py`：**11 用例全绿**
  - diff 与规则内核 8 项（含非法表达式拒绝、未声明 transform 判不一致）
  - 真实 SQLite 全链路 4 场景：前后快照 diff 落 PASS / 注入列值偏差落 FAIL 且传导 gate /
    env_failure 诚实降级 / v2.12 纯 UI 回归零破坏
- v2.13 + v2.12 聚合回归：**31 passed**
- CLI 一致性核验：`db-snapshot` / `dc-check` / `rule` 子命令、`run --db-config`、
  `case --db-check-before / --dcs` 全部就位

## 📊 关键数据

- **文件变更**: 14 个文件，+2234 / −45
- **新增代码**: `data_rules.py` 682 行 / `db_probe.py` 310 行 / 端到端测试 275 行 /
  记录器扩展 +465 行 / 设计文档 177 行
- **新增依赖**: 无（纯标准库；mysql/postgres 驱动可选缺失即诚实降级）
- **向后兼容**: 是（不传 `--db-config` 的 v2.12 纯 UI 用例零回归）

## 🔗 架构复用

| 复用对象 | 来源 | 用途 |
|----------|------|------|
| `su/db_guard.ReadOnlyGuard` | v2.9 | DB 快照唯一语句通道（安全面零复制） |
| `browser_test_recorder.py` 闭环骨架 | v2.12 | 断言/门禁/一致性校验全部复用 |
| FLOW/OP/DT/TC ID 族 | v2.11 / v2.12 | 之下扩展 DC/DR 两级 ID |
| 共识收敛机制 | v2.10 | DC 完备性纳入测试用例四角色评审 BLOCKER 项 |

## 🚫 绝对禁止（v2.13 新增 4 条）

- ❌ 数据一致性结论人工口算（必须经 db-snapshot → dc-check / rule 确定性落盘）
- ❌ 用写 SQL 修数据"让测试通过"（数据通道严格只读，校验器直接拒绝）
- ❌ 声明 DC 的用例不跑数据对照、DB 通道故障谎报 PASS 或静默跳过
- ❌ 字段映射的 transform 无代码证据就默认"应该一致"；DB 预期凭猜测填写

---

## 📁 文件清单

```
新增
├── scripts/data_test/data_rules.py             # 规则求值 + 行级 diff（682 行）
├── scripts/data_test/db_probe.py               # 只读快照引擎（310 行）
├── scripts/tests/test_v213_data_consistency.py # 端到端测试（275 行）
└── docs/dev/DATA_CONSISTENCY_TESTING.md        # 设计文档（177 行）

修改
├── scripts/browser_test_recorder.py            # db-snapshot/dc-check/rule + data_gate（+465）
├── docs/spec/role-prompts/browser-test-planner.md    # Step 3.5/3.6 + db_expectation
├── docs/spec/role-prompts/browser-test-execution.md  # 数据步骤 + 两类阻碍协议
├── SKILL.md / README.md / CHANGELOG.md         # v2.13 章节
└── skill-manifest.yaml / skills-index.json / claude-code-skill.json  # 2.12.0 → 2.13.0
```

## 🚀 快速上手

```bash
REC="python3 scripts/browser_test_recorder.py --out browser-test-runs"

# 1. 初始化运行：--db-config 启用数据层，--db-checks 挂 planner 产出的快照查询清单
$REC run --run-id r1 --url http://localhost:3000 \
    --db-config db.json --db-checks db-checks.json

# 2. 用例声明数据校验（--action start；--dcs 关联 DC 契约，
#    --db-check-before/--db-check-after 指向快照清单文件——声明即强制校验）
$REC case --run-id r1 --case-id TC-001 --action start --priority P0 \
    --dcs DC-001 \
    --db-check-before checks-before.json --db-check-after checks-after.json

# 3. 操作前后只读快照（--check-file 清单 + --phase；参数占位用 --params 注入）
$REC db-snapshot --run-id r1 --case-id TC-001 \
    --check-file checks-before.json --phase before
# 浏览器动作照常留痕（示例）：
# $REC step   --run-id r1 --case-id TC-001 --action-name click --target "#submit-order"
# $REC assert --run-id r1 --case-id TC-001 --kind text --expected "提交成功" --actual "提交成功"
# $REC shot   --run-id r1 --case-id TC-001 --path shots/after-submit.png
$REC db-snapshot --run-id r1 --case-id TC-001 \
    --check-file checks-after.json --phase after

# 4. 数据对照 + 规则求值（确定性生成 dc / rule 断言落盘）
$REC dc-check --run-id r1 --case-id TC-001 \
    --contract-id DC-001 --expectation expectations.json --key-columns id
$REC rule --run-id r1 --case-id TC-001 --rules data-rules.json

# 5. 用例收尾（声明了 --db-check-* 而未落盘快照 → 此处直接拒绝收尾）
$REC case --run-id r1 --case-id TC-001 --action end --status pass

# 6. 运行 finalize（data_gate：dc/rule 失败、声明校验缺失、env_failure → 整体 FAIL）
$REC finalize --run-id r1
```
