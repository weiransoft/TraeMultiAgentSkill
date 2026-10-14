# 浏览器测试执行器（Browser Test Executor）— v2.12

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

## 单用例执行循环（TC 级）

```
for TC in execution_plan（按依赖序）:
  1. recorder start-case {TC-ID}
  2. 前置构造（登录 / 数据准备）→ 每步记录
  3. for step in TC.steps:
       a. 执行浏览器动作（按 TC.宿主 或 auto 分派）
       b. recorder step：动作类型 / 目标 / 输入值(DT-*) / 耗时 / 结果状态
       c. 执行断言 → recorder assert（预期 vs 实际，逐条）
       d. 到达截图点 / 断言失败 → 截图并 recorder screenshot
       e. 采集 console error + 非预期网络响应 → recorder error
  4. TC 收尾：PASS / FAIL / BLOCKED / SKIPPED → recorder end-case
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
summary.json           # 汇总：通过率、错误分类统计、疑似缺陷清单、blocker 清单
summary.md             # 人读报告（recorder 从 summary.json 渲染）
```

步骤记录字段（全部必填）：
`seq / action(navigate|click|fill|submit|upload|select|keypress|wait|screenshot|evaluate) / target / input_dataset(DT-*) / duration_ms / status(pass|fail|blocked) / assertions[{expected, actual, pass}] / console_errors[] / network_errors[] / screenshot(可选路径)`

## 执行完成后（闭环收尾）

1. `recorder finalize` → 生成 summary.json + summary.md
2. 疑似缺陷清单（suspected_bug）→ 逐条给出：复现步骤、页面/后台错误、涉及代码位置（若可定位）
3. 通过率门禁：P0 通过率必须 100%；有 P0 FAIL → 整体判 FAIL，回到修复环节
4. 视觉基线：通过的截图转交 `visual_regression.py` 建立/更新基线
5. 将本次 run 与 v2.11 动线分析衔接：新发现的操作断点回填 business-flow 报告的 BREAK 清单

## 绝对禁止

- ❌ 跳步骤执行（每步都必须 recorder step 留痕）
- ❌ 断言失败只写"失败"不记录 expected/actual 对比
- ❌ 疑似产品缺陷被当作"用例问题"悄悄改掉断言让测试通过
- ❌ 环境故障谎报为用例 FAIL
- ❌ 无截图的断言失败记录
- ❌ summary 与逐条 case 日志数据不一致（recorder 校验，不一致即拒绝 finalize）
