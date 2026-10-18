# Agent 的说明书，写一条就得能跑一条

用 LLM 做自动化测试最大的坑：**提示词里的命令是编的**。
prompt 写着 `recorder start-case`，实现里压根没有这个命令，
Agent 照着执行，一头撞在 argparse 上。

v2.14 做了一次彻底的**双向对齐**：

- **三个角色提示词**全部按实现重写：planner 的 Step 2.5 功能清单、
  review 的 FUNC 完备性一票否决、execution 的缺陷登记闭环六步——
  每一条命令、每一个参数名都和 recorder 的 CLI 逐项核对过
- **SKILL.md 指令清单全量刷新**：84 个 dispatcher flags、
  13 个 recorder 子命令、5 个配套工具，全部列表化；
  连文档里的示例命令都拿到真实 CLI 上跑过一遍——
  发现示例里 `defect DEF-001 --action` 这种写法根本不合法，改成 `--defect-id`
- 顺手修掉 3 处**文档-实现漂移**：路径写错、命令名不符、产物路径对不上

对 Agent 来说，提示词不是散文，是**契约**。
契约的正确性不靠 review 时"看起来没问题"，靠可执行验证。

写进文档的每一行命令，都值得先跑一次。

> v2.14 系列 5/5 · 完 · planner / review / execution 三角色 prompt × SKILL.md 指令清单
