# 你标记的"疑似缺陷"，后来呢？

执行测试时标了个 suspected_bug，然后呢？
然后就没有然后了——标记散落在各处，没人知道它修没修、回归没回归。

v2.14 把缺陷变成一个**跨 run 的全局工单**（defects.json），
核心是"信号-工单"两层分离：

- `error --suspected-bug` 只是**信号**：执行期顺手记一笔
- `defect add` 才是**工单**：`--bug-ref` 强制关联真实信号的序号，凭空造不出 bug

工单有严格的状态机，每一次转移都写 history：

- **open → fixed**：必须 `--commit` 登记真实修复 commit
- **fixed → closed**：必须给 `--regression-run`，脚本核对回归 run 里
  关联用例**全部 PASS** 才放行——嘴上说修好了不算
- **closed → regressed**：回归再犯，audit 自动汇总漏修工单
- **任意状态 → open**：reopen 强制书面 reason

连 DEF-001 这种工单号都做了防复用：删了旧工单，编号也不会再发出去，
历史引用永远可追溯。

缺陷不怕多，怕的是没有下文。

> v2.14 系列 3/5 · defect add/update/close/reopen/audit/list
