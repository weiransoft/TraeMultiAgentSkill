# Trae Multi-Agent Skill v2.14 发布说明

**版本**: 2.14.0
**发布日期**: 2026-10-16
**提交**: `f28da00` — feat(v2.14): 功能覆盖驱动测试与缺陷台账——FUNC 清单 / feature_gate / defect 状态机 / 登记闸

---

## 🎯 一句话总结

在 v2.13 数据校验闭环之上补齐**覆盖盲区**——用例全过不等于功能都测到了：
没为某个按钮写用例，就永远不会有任何失败来暴露它。
v2.14 让**「测没测全」和「缺陷修没修好」都由确定性脚本判定**：
FUNC-* 每页功能清单做分母 → finalize 现算覆盖矩阵 feature_gate →
SHA256 清单锚定防篡改；跨 run 缺陷台账把"疑似缺陷"从一次性标记
升级为有状态、有修复 commit、有回归证据的正式工单。

---

## ✨ 核心特性

### 1. FUNC-* 每页功能清单（覆盖矩阵的分母）

- 规划期逐页列全功能项，产出 `page-feature-inventory.json`（测试计划八件套之一）
- kind 五类，各有覆盖要求：

| kind | 含义 | 覆盖要求 |
|------|------|----------|
| interactive | 按钮/开关/拖拽等交互 | **必须 P0 用例覆盖，禁止豁免** |
| form | 表单填写与提交 | **必须 P0 用例覆盖，禁止豁免** |
| display | 数据展示/列表/详情 | 须覆盖，或书面 waive_reason |
| nav | 导航/跳转/路由 | 须覆盖，或书面 waive_reason |
| branch | 条件分支渲染 | 须覆盖对应分支条件 |

- 每项 evidence 强制 `文件:行号` 代码证据（校验器正则核验行号格式）
- display/nav 豁免必须有 `waive_reason` 书面理由——**豁免不是空白通行证**

### 2. feature_gate 覆盖矩阵门禁（finalize 现算，不信任自报）

- finalize 从执行记录**现算** FUNC × PASS 断言关联矩阵：
  功能项被覆盖 = 至少关联 1 条 PASS 断言（fail 断言不算覆盖）
- interactive/form 任一未覆盖 → **整体 gate=FAIL**，summary.md 渲染缺口清单
  （含归属页面与 kind）与 waived 溯源（covered_by）
- **SHA256 清单锚定**：清单原件哈希在 `run` 时写入 run 元数据，
  finalize 重新核对——执行期篡改清单"移动球门"直接 FAIL
- 用例与步骤用 `--funcs FUNC-01-01,...` 归因；`case --funcs` 引用未知
  FUNC ID 直接拒绝

### 3. 跨 run 缺陷台账（defects.json 全局工单）

**信号-工单模型**：执行期的"疑似缺陷"与正式工单分离——

- `error --suspected-bug true` 是执行期**信号**（落在用例 errors 数组）
- `defect --action add` 是正式**工单**，`--bug-ref` 强制关联真实信号的
  error_seq，杜绝凭空造 bug
- 状态机（全部转移写 history，DEF-NNN 扫描台账条目 + history add 记录防已删 ID 复用）：

```
open ──update --commit（登记修复 commit+摘要）──▶ fixed
fixed ──close --regression-run（回归 run 关联用例全 PASS 才放行）──▶ closed
closed ──回归再犯（audit/回归发现）──▶ regressed ──update --commit──▶ fixed
任意状态 ──reopen --reason（强制书面理由，清 fix/regression）──▶ open
```

- **回归证据强制**：fixed 关闭必须提供 `--regression-run` + `--regression-cases`，
  核对回归 run 的 summary.json 关联用例结果；误报关闭必须给 `--reason`
- `defect --action audit`：汇总未闭环工单（open/fixed 未回归），供收尾审计

### 4. 缺陷登记闸（finalize）

- 台账启用后，任何 `suspected_bug` 信号未登记 DEF-* 工单 → **finalize gate=FAIL**
- 与既有 P0 门禁、v2.13 data_gate 并列为收尾四道闸：
  `gate（P0/一致性）× data_gate × feature_gate × 登记闸`

### 5. 记录器扩展（browser_test_recorder.py，+585 行）

- 新增子命令：`defect`（add / update / close / reopen / audit / list）
- `run --feature-inventory` 启用功能覆盖门禁（不传 = 纯 UI 语义，向后兼容）
- `case --funcs` / `step --funcs` 功能归因；`case start` 落盘 priority
- finalize 增补 `feature_coverage`（total/covered/waived/missing）与
  `defect_gate` 摘要段；summary.md 渲染功能覆盖矩阵 / 缺陷台账 / 覆盖缺口三段

### 6. prompt 细化与文档

- `browser-test-planner.md`：新增 **Step 2.5 每页功能清单**（FUNC 结构 +
  kind 覆盖要求表 + JSON 示例）；Step 4 用例关联 FUNC-*；数量要求新增
  FUNC 覆盖完备性（interactive/form 100% P0 覆盖）；绝对禁止 +3 条
- `browser-test-execution.md`：新增**疑似缺陷登记闭环六步**（信号→工单→修复→
  回归→误报关闭→再犯→审计）与**功能覆盖预检**（SHA256 锚定核对）；
  case/step `--funcs` 归因；收尾门禁与绝对禁止同步
- `browser-test-review.md`：追加 FUNC 覆盖完备性与清单真实性评审项；
  一票否决扩展为「FLOW 主路径无 P0 **或** interactive/form 功能项无 P0」双维度；
  清单 SHA256 写入评审记录冻结纳入通过门禁
- `MULTI_ROLE_PROMPTS_INDEX.md`：三角色职责表同步 v2.13/v2.14 能力
- `SKILL.md`：**CLI 指令清单章节全量刷新**（10 组 84 个 dispatcher flags、
  记录器 13 子命令全参数表、配套 5 个确定性工具、v2.14 缺陷闭环命令示例，
  全部与 parser.py / recorder 实现逐项核对）

---

## 🛠️ 缺陷修复（测试驱动发现，8 项）

| 缺陷 | 影响 | 修复 |
|------|------|------|
| evidence 正则双反斜杠 `":\\d+$"` 永假 | 全部 FUNC 项误判格式错误 | 改为 `":\d+$"` |
| `_next_defect_id` 只扫台账条目 | 已删条目 DEF ID 被复用 | 追加扫描 history add 记录 |
| `update --commit` 未实现转移 fixed | 修复无法登记、状态机断裂 | 补齐 open\|regressed→fixed + `--commit` dest 绑定 |
| 状态机缺 `regressed→open` 通道 | 回归再犯无法回炉 | 补 reopen 回炉通道 |
| `case start` 未落盘 priority | 回归 run P0 判定失真 | start 块记录 priority（缺省 P2） |
| 登记闸核对键缺 run_id | 跨 run 同 case_id 信号误判已登记 | bug_list 补 run_id |
| `run` 子命令两参数 help 交叉错乱 | 文档误导 | 修正 help 文案 |
| gate 在空 run 上 p0 判定失真 | 零用例 run 误判 | `stats["total"] > 0` 兜底 |

另有 3 处**文档-实现漂移**修复（planner 路径、execution 命令名与快照产物路径、
DATA_CONSISTENCY_TESTING.md 同类），详见 CHANGELOG `Fixed` 小节。

---

## 🧪 测试

- 新增 `tests/test_v214_feature_coverage.py`：**19 用例全绿**（529 行）
  - 清单 schema 7 项：evidence 格式 / kind 枚举 / interactive 禁豁免 /
    豁免缺 reason / ID 唯一与格式 / case 引用未知 FUNC / SHA 锚定篡改
  - 覆盖门禁 3 项：fail 断言不算覆盖 / 部分覆盖 missing 清单 / 全覆盖 PASS +
    waived 溯源 + summary.md 呈现
  - 台账状态机 7 项：bug_ref 必须真实信号 / DEF 递增绑定 / 漏登 finalize FAIL /
    fixed 缺回归拒绝 / 回归未全 PASS 拒绝 / reopen 路径与 history / audit 漏修
  - 向后兼容 1 项：纯 UI run（无清单无台账）gate/data_gate/feature_gate 语义零回归
- recorder 域三版聚合回归（v2.12 记录器 20 + v2.13 数据 11 + v2.14 功能 19）：
  **50 passed**
- CLI 一致性核验：`defect` 六种 action、`run --feature-inventory`、
  `case/step --funcs` 全部就位；SKILL.md 示例命令经实际 CLI 验证合法

## 📊 关键数据

- **文件变更**: 12 个文件，+1482 / −59
- **新增代码**: 端到端测试 529 行 / 记录器扩展 +585 行 / prompt 细化 +123 行
- **新增依赖**: 无（纯标准库）
- **向后兼容**: 是（不传 `--feature-inventory`、不建台账的 v2.12/v2.13 用例零回归）

## 🔗 架构复用

| 复用对象 | 来源 | 用途 |
|----------|------|------|
| `browser_test_recorder.py` 闭环骨架 | v2.12 | step/assert/error 记录与 finalize 一致性校验全复用 |
| `data_gate` 门禁模式 | v2.13 | feature_gate / 登记闸沿用同一"声明必执行、缺失即 FAIL"语义 |
| FLOW/OP/DT/TC/DC ID 族 | v2.11-v2.13 | 之上扩展 FUNC / DEF 两级 ID |
| 共识收敛机制 | v2.10 | FUNC 完备性纳入测试用例四角色评审 BLOCKER 项 |
| `error --suspected-bug` 信号 | v2.12 | 台账 bug_ref 直接锚定 errors 数组序号，零新表 |

## 🚫 绝对禁止（v2.14 新增 4 条）

- ❌ 功能清单漏列页面交互/表单项，或 interactive/form 功能项无 P0 用例覆盖
- ❌ 执行期篡改功能清单原件"移动球门"（SHA256 锚定，篡改即 FAIL）
- ❌ suspected_bug 不登记 DEF-* 工单、无回归 run 全 PASS 证据就 close 缺陷
- ❌ 豁免 display/nav 功能项不给书面 waive_reason（沿用清单 schema 强制）

---

## 📁 文件清单

```
新增
└── scripts/tests/test_v214_feature_coverage.py  # 端到端测试（529 行）

修改
├── scripts/browser_test_recorder.py             # defect 台账 + feature_gate + 修复（+585）
├── docs/spec/role-prompts/browser-test-planner.md     # Step 2.5 + FUNC 覆盖完备性
├── docs/spec/role-prompts/browser-test-execution.md   # 缺陷闭环六步 + 覆盖预检
├── docs/spec/role-prompts/browser-test-review.md      # FUNC 评审项 + 一票否决扩展
├── docs/spec/MULTI_ROLE_PROMPTS_INDEX.md        # 角色职责表同步
├── SKILL.md                                     # v2.14 章节 + 指令清单全量刷新
├── README.md / CHANGELOG.md                     # v2.14 高亮块 / 2.14.0 小节
└── skill-manifest.yaml / skills-index.json / claude-code-skill.json  # 2.13.0 → 2.14.0
```

## 🚀 快速上手

```bash
REC="python3 scripts/browser_test_recorder.py --out browser-test-runs"

# 1. 初始化运行：--feature-inventory 启用功能覆盖门禁（清单 SHA256 即锚定）
$REC run --run-id r1 --url http://localhost:3000 \
    --feature-inventory browser-test-plan/page-feature-inventory.json

# 2. 用例声明关联功能项（--funcs 归因；引用未知 FUNC 直接拒绝）
$REC case --run-id r1 --case-id TC-001 --action start --priority P0 \
    --funcs FUNC-01-01,FUNC-02-01

# 3. 步骤照常留痕（步骤级可再归因）
# $REC step   --run-id r1 --case-id TC-001 --action-name submit \
#     --target "#order-form" --funcs FUNC-01-01
# $REC assert --run-id r1 --case-id TC-001 --kind text \
#     --expected "提交成功" --actual "提交成功" --pass true
$REC case --run-id r1 --case-id TC-001 --action end --status pass

# 4. 发现疑似缺陷：先发信号，再登记工单（bug-ref = 该用例 errors 数组序号）
$REC error --run-id r1 --case-id TC-001 --source console \
    --message "提交后列表未刷新" --suspected-bug true
$REC defect --action add --run-id r1 --case-id TC-001 --bug-ref 1 \
    --symptom "提交后列表未刷新" --funcs FUNC-02-01 --cases TC-001 \
    --severity major --evidence browser-test-runs/r1/screenshots/TC-001-03.png

# 5. 修复后登记 commit（open|regressed → fixed）→ 回归 run 全 PASS 才允许关闭
$REC defect --action update --defect-id DEF-001 \
    --commit a1b2c3d --fix-summary "列表刷新缺 SSE 订阅"
$REC defect --action close --defect-id DEF-001 \
    --regression-run r2 --regression-cases TC-001

# 6. 收尾：四道闸（P0 × data_gate × feature_gate × 缺陷登记闸）
$REC finalize --run-id r1
# 台账审计（open/fixed 未闭环工单一览）：
$REC defect --action audit
$REC defect --action list
```
