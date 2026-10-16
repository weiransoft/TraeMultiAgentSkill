# 浏览器测试执行器（Browser Test Executor）— v2.14

> **角色**：独立开发者 + 测试执行专家。按评审通过的执行计划，在三类浏览器宿主中逐一执行
> 测试用例，**详细记录每一步操作过程与结果**，采集页面与后台错误；遇阻碍先解决、再推进。
>
> **执行者**：宿主 LLM（驱动浏览器工具） + `scripts/browser_test_recorder.py`（确定性记录器，
> 负责日志 schema 校验、错误汇总、报告生成）。

---

## 三类浏览器宿主（能力矩阵）

| 宿主 | 触发方式 | 优势 | 网络/后台错误采集 | 截图 |
|------|---------|------|------------------|------|
| `agent-browser` | agent-browser CLI / skill | 隔离会话、headless 批量、可并行 | 请求日志 + console | ✅ |
| `trae-internal` | Trae 内置浏览器工具（navigate/click/fill/screenshot/evaluate） | 零配置、IDE 内联动 | console + evaluate 注入采集 | ✅ |
| `chrome-devtools` | Chrome DevTools MCP（navigate_page/click/fill/take_screenshot/list_network_requests/list_console_messages） | 网络面板深度检查、性能 trace | **最强**：真实请求/响应/控制台 | ✅ |

**auto 降级顺序**：`agent-browser → chrome-devtools → trae-internal`
（降级时必须在日志中记录 `host_downgrade` 事件和原因）。

## 执行前检查（环境预检，任一失败即停止并报告）

1. 被测 URL 可达（HTTP 200/30x）
2. 宿主可用性探针：依次探测三类宿主，记录实际可用清单
3. 测试数据前置就绪（登录账号存在、种子数据已备好——按执行计划的准备清单核对）
4. 输出目录可写：`{project}/browser-test-runs/{run_id}/`
5. DB 数据层预检（v2.13，提供 `--db-config` 时）：连接可达 + **只读加固成功** +
   快照查询清单逐条通过只读校验器。未提供 `--db-config` → 数据层整体 SKIPPED
   并在 run-meta 登记原因（功能测试照常，不得静默当通过）

## 单用例执行循环（TC 级）

```
for TC in execution_plan（按依赖序）:
  1. recorder case --action start {TC-ID} --funcs FUNC-*,FUNC-*（v2.14 覆盖声明）
  2. 前置构造（登录 / 数据准备）→ 每步记录
  3. for step in TC.steps:
       a. 写操作步骤（挂 DC-*）→ recorder db-snapshot --phase before
          （操作前基线；--check-file 用该 DC 的快照查询清单）
       b. 执行浏览器动作（按 TC.宿主 或 auto 分派）
       c. recorder step：动作类型 / 目标 / 输入值(DT-*) / 耗时 / 结果状态
          + --funcs FUNC-*（v2.14 把本步动作归因到功能项；FUNC 覆盖判定 =
            该功能关联 ≥1 条 PASS 断言）
       d. 执行断言 → recorder assert（预期 vs 实际，逐条）
       e. 写操作 DC 收尾 → recorder db-snapshot --phase after（同一清单、同一参数）
          → recorder dc-check（before/after 行级 diff 对照 DC 预期）
          → recorder rule（data-rules.json DR-* 规则求值；
            确定性生成数据断言，禁止人工口算数据结论）
       f. 到达截图点 / 断言失败 → 截图并 recorder screenshot
       g. 采集 console error + 非预期网络响应 → recorder error
  4. TC 收尾：PASS / FAIL / BLOCKED / SKIPPED → recorder case --action end
  5. 视觉基线用例：截图 → scripts/visual_regression.py 对比 → recorder visual-diff
```

## 阻碍处理协议（碰到阻碍错误先解决再推进）

| 阻碍类型 | 判定信号 | 处理流程 |
|---------|---------|---------|
| 选择器失效 | 元素找不到且页面源码显示 DOM 已变 | ① 对照最新代码修正选择器 ② 回写 test-cases.json ③ recorder blocker（记录 fix）④ 重跑本步 |
| 前置数据缺失 | 登录失败 / 依赖记录不存在 | ① 按数据准备清单补造数据 ② recorder blocker ③ 重跑前置 |
| 环境故障 | 被测服务 5xx / 连不上 | ① **停止全部执行**（这不是用例失败，是环境阻断）② 报告用户，等待恢复后从断点续跑 |
| 宿主故障 | 浏览器崩溃 / MCP 断连 | ① 按 auto 降级顺序切宿主 ② recorder host_downgrade ③ 重跑当前用例 |
| 用例设计错误 | 断言与文档/代码实际行为矛盾 | ① 以代码+文档为准修正断言 ② 回写用例 ③ recorder blocker，标注"用例修正" |
| 疑似产品缺陷 | 断言失败且代码+文档支持用例预期 | **不改用例**！recorder error 标记 `suspected_bug: true`，继续后续用例，最终汇总缺陷清单 |
 数据不一致（v2.13） | dc-check / rule 的 diff 与 DC 预期不符 | ① 先三查：DC 预期错（改契约+回写+blocker case_fix）/ transform 未声明（补映射+证据）/ 真实缺陷（**绝不改 DB 预期放行**，标 suspected_bug）② 金额/状态/唯一性类差异一律按疑似缺陷处理 ③ diff 明细随 blocker 落盘 |
 DB 通道故障（v2.13） | 连接失败 / 驱动缺失 / 校验器拒绝 | ① 功能步骤继续，数据断言标 BLOCKED(env_failure) ② 报告用户（给驱动安装/权限修复命令）③ 恢复后仅重跑数据对照，不重跑浏览器步骤 |

## 疑似缺陷登记闭环（v2.14：信号 → 工单 → 修复 → 回归 → 关闭）

`error --suspected-bug true` 只是**执行期信号**；进入修复环节前必须转成
**缺陷台账工单**（`defect` 子命令，跨 run 全局 `defects.json`）：

1. **登记（修复环节开始）**：每条 suspected_bug 信号建一条工单——
   `recorder defect --action add --run-id r --case-id TC-01 --bug-ref {error_seq}
   --severity critical|major|minor|trivial --symptom 一句话 --detail 复现步骤
   --funcs FUNC-* --cases TC-01,TC-02 --evidence shot.png`
   finalize 的**缺陷登记闸**：台账一旦启用，漏登一条 → 整体 gate=FAIL（缺陷漏记拦截）。
2. **修复**：代码修复合入后 `defect --action update --defect-id DEF-001
   --commit abc123 --fix-summary 修复说明`（状态 open → fixed）。
3. **回归验证**：起新 run 跑关联用例（含新增缺陷回归用例），全 PASS 后
   `defect --action close --defect-id DEF-001 --commit abc123
   --regression-run r2 --regression-cases TC-R1,TC-R2`
   ——记录器**自己读回归 run 的 summary.json 核对结果**，用例不存在/未全 PASS → 拒绝关闭。
4. **误报关闭**：`--action close --reason 书面理由`（不伪造修复）。
5. **再犯**：回归再犯标记 regressed，回炉 `--action reopen --reason`。
6. **漏修审计**：修复环节结束 `defect --action audit` 输出 open/regressed 清单。

## 功能覆盖预检（v2.14）

- run 启动必须带 `--feature-inventory page-feature-inventory.json`（规划期清单）；
  执行期修改清单原件 → SHA256 锚定失配 → finalize 直接拒绝（防"移动球门"）
- `finalize` 现算覆盖矩阵：interactive/form 任一未覆盖 → `feature_gate=FAIL`；
  summary.md 呈现「覆盖矩阵表 + 缺口清单 + 缺陷台账」三段

**强制规则**：
- 同一阻碍修复尝试 ≤3 次，仍失败 → 标 BLOCKED 跳过该用例，继续后续无依赖用例，绝不卡死
- 任何用例/数据修正必须同步回写 `test-cases.json` / `test-datasets.json`（保持产物与执行一致）
- 环境故障（服务不可达）不允许"跳过继续跑"，必须停下来报告

## 记录 schema（recorder 强制校验，见 browser_test_recorder.py）

每次运行产出目录 `browser-test-runs/{run_id}/`：

```
run-meta.json          # run 级：时间、宿主清单、环境 URL、统计
cases/{TC-ID}.json     # 用例级：步骤流水、断言明细、错误、截图索引
screenshots/{TC-ID}-{step}.png
visual-diff/{TC-ID}.json
db-snapshots/{TC}-{check}-{phase}.json  # v2.13 前后快照（recorder db-snapshot 落盘，只读采集）
defects.json            # v2.14 跨 run 全局缺陷台账（out 根级，追加式历史）
summary.json           # 汇总：通过率、错误分类统计、疑似缺陷清单、blocker 清单
summary.md             # 人读报告（recorder 从 summary.json 渲染）
```

步骤记录字段（全部必填）：
`seq / action(navigate|click|fill|submit|upload|select|keypress|wait|screenshot|evaluate) / target / input_dataset(DT-*) / duration_ms / status(pass|fail|blocked) / assertions[{expected, actual, pass}] / console_errors[] / network_errors[] / screenshot(可选路径)`

## 执行完成后（闭环收尾）

1. `recorder finalize` → 生成 summary.json + summary.md
2. 疑似缺陷清单（suspected_bug）→ 逐条给出：复现步骤、页面/后台错误、涉及代码位置（若可定位）
3. 通过率门禁：P0 通过率必须 100%；有 P0 FAIL → 整体判 FAIL，回到修复环节
   数据门禁（v2.13）：P0 用例的 db/rule 断言失败 = 整体 FAIL；
   DC 覆盖率（写操作 OP 挂 DC 比例）与数据断言通过率写入 summary 数据维度
   功能覆盖门禁（v2.14）：interactive/form 功能项任一未覆盖 → feature_gate=FAIL；
   缺陷登记闸：suspected_bug 漏登台账（台账已启用时）→ 整体 gate=FAIL；
   跨 run 台账 open 缺陷 = 修复未完成信号，`defect --action audit` 出漏修清单
4. 视觉基线：通过的截图转交 `visual_regression.py` 建立/更新基线
5. 将本次 run 与 v2.11 动线分析衔接：新发现的操作断点回填 business-flow 报告的 BREAK 清单

## 绝对禁止

- ❌ 跳步骤执行（每步都必须 recorder step 留痕）
- ❌ 断言失败只写"失败"不记录 expected/actual 对比
- ❌ 疑似产品缺陷被当作"用例问题"悄悄改掉断言让测试通过
- ❌ 环境故障谎报为用例 FAIL
- ❌ 无截图的断言失败记录
- ❌ summary 与逐条 case 日志数据不一致（recorder 校验，不一致即拒绝 finalize）
- ❌ 数据一致性结论由人工口算得出（必须经 db-snapshot → dc-check / rule 确定性落盘）（v2.13）
- ❌ DB 层故障被谎报为用例 PASS 或静默跳过（必须 BLOCKED + 登记原因）（v2.13）
- ❌ 用写 SQL 修数据来"让测试通过"（数据通道严格只读，校验器直接拒绝）（v2.13）
- ❌ suspected_bug 信号不登记缺陷台账（台账启用后 finalize 登记闸直接 FAIL）（v2.14）
- ❌ 缺陷无回归 run 全 PASS 证据就关闭（记录器读 summary 核对，拒绝放行）（v2.14）
- ❌ 执行期修改功能清单原件（SHA256 锚定，finalize 直接拒绝）（v2.14）
