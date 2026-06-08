# Autonomous 模式使用指南

> 让多角色团队在你睡觉时自动完成全部任务 —— 自动运行、自动确认、自动使用 skill、自动测试、自动提交。

本文档面向 trae-multi-agent 的 Autonomous 模式（Phase 18，借鉴 [gnhf](https://github.com/kunchenguid/gnhf) 的 Ralph 风格自主迭代思想）。读者应当已经熟悉 trae-multi-agent 的基本调度、CLI、角色机制。

## 1. 概述

### 1.1 什么是 Autonomous 模式

Autonomous 模式（内部代号 `Ralph`）是一种"无人值守迭代"工作流：给定一个目标后，多角色团队（架构师 / 产品经理 / 独立开发者 / 测试专家 / UI 设计师）会按照 `plan → dev → verify → fix` 四阶段循环执行，直到满足停止条件或触发硬上限（max iterations / max tokens）。

适合的场景：

- 夜间长跑需求（例如补齐测试、重构大型模块、批量迁移）
- 需要反复重试直到全绿的任务
- 用户希望"挂上后不必再点确认"的场景

不适合的场景：

- 涉及生产数据库写操作（建议手动）
- 涉及不可逆硬件操作（同样建议手动）
- 跨主机的强一致性同步（建议走专用工具）

### 1.2 核心组件一览

| 组件 | 路径 | 职责 |
| --- | --- | --- |
| `RalphAutonomousPlugin` | `scripts/plugins/autonomous.py` | 插件入口（priority=5，CLI flag `--autonomous`） |
| `RalphLoopController` | `scripts/autonomous/loop_controller.py` | 主循环（Plan / Dev / Verify / Fix） |
| `RunState` | `scripts/autonomous/run_state.py` | 状态持久化（SHA256 校验、备份恢复） |
| `NotesMemory` | `scripts/autonomous/notes_memory.py` | 跨轮 `notes.md` 累积 |
| `GitDriver` | `scripts/autonomous/git_driver.py` | 原子 commit / 滚动回滚 |
| `SleepGuard` | `scripts/autonomous/sleep_guard.py` | 跨平台防休眠（caffeinate / systemd-inhibit） |
| `SmartConfirmation` | `scripts/autonomous/smart_confirmation.py` | 三态确认（白名单 + 风险评分 + 黑名单） |
| `AutoSkillLoader` | `scripts/autonomous/auto_skill_loader.py` | 自动按任务特征加载相关 skill |
| `DispatcherAdapter` | `scripts/autonomous/dispatcher_adapter.py` | 与 V3 dispatcher 解耦适配 |
| `load_config` | `scripts/autonomous/config_loader.py` | YAML 配置加载（用户级 + 项目级） |

### 1.3 与其他 V3 插件的互斥

Autonomous 模式与下列插件互斥（`mutex_with={"autonomous"}`）：

- `loop`（`--loop`）
- `multi-goal`（`--multi-goal`）
- `cancel`（`--goal-cancel`）
- `graph`（`--goal-graph`）
- `resume`（`--goal-resume`）

互斥检查在 `GoalDispatcher` 启动时执行：若同时启用多个，会拒绝并提示。

## 2. 快速上手

### 2.1 最小化启动

```bash
# 在项目根目录执行：让 Ralph 自动完成"实现一个 LRU 缓存"
python -m cli.main \
    --autonomous \
    --task "实现一个线程安全的 LRU 缓存" \
    --project-root .
```

退出码含义：

| 退出码 | 含义 |
| --- | --- |
| `0` | 全部迭代成功（`consecutive_failures == 0`） |
| `1` | 部分迭代失败，但未达 fatal 阈值 |
| `2` | fatal 错误（达到 `consecutive_failure_abort`） |
| `3` | 命中 `stop_when`，主动停止 |

### 2.2 完整推荐启动命令

```bash
python -m cli.main \
    --autonomous \
    --task "为 parser 模块补齐边界测试" \
    --project-root . \
    --auto-max-iterations 30 \
    --auto-stop-when "all tests pass" \
    --auto-test-command "python3 -m unittest discover -s tests -p 'test_*.py'" \
    --auto-confirm-mode smart \
    --auto-git-author-name "Ralph Bot" \
    --auto-git-author-email "ralph@example.com"
```

## 3. CLI Flags 全参考

所有 autonomous 专属 flag 都以 `--auto-` 前缀命名，避免与其它插件冲突。

### 3.1 主开关

| Flag | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `--autonomous` | bool | `False` | 启用 autonomous 模式 |

### 3.2 运行时硬上限

| Flag | 类型 | 默认 | 范围 | 说明 |
| --- | --- | --- | --- | --- |
| `--auto-max-iterations` | int | `50` | `[1, 1000]` | 最大迭代次数 |
| `--auto-max-tokens` | int | `500000` | `>=0` | token 预算 |
| `--auto-stop-when` | str | `""` | — | 自然语言停止条件（按空格拆分做 substring 命中） |
| `--auto-failure-abort` | int | `3` | `>=1` | 连续失败 abort 阈值 |

### 3.3 阶段与节奏

| Flag | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `--auto-stage-order` | CSV str | `plan,dev,verify,fix` | 阶段顺序（CSV 解析为 list） |
| `--auto-test-command` | str | `python3 -m unittest discover -s tests -p "test_*.py"` | 每轮 verify 阶段跑的测试命令 |
| `--auto-backoff-base` | float | `1.0` | 退避基数（秒；指数退避基数） |
| `--auto-backoff-max` | float | `60.0` | 退避上限（秒） |

### 3.4 续跑 / 状态

| Flag | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `--auto-resume` | str | `None` | resume 指定 `run_id` |
| `--auto-resume-latest` | bool | `False` | resume 最新可续跑的 run（与 `--auto-resume` 互斥） |
| `--auto-run-dir` | str | `.gnhf/runs` | run 状态目录（相对 `project_root`） |

### 3.5 安全与防休眠

| Flag | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `--auto-no-caffeinate` | bool | `False` | 禁用 `caffeinate` / `systemd-inhibit`（CI 环境） |
| `--auto-no-commit` | bool | `False` | 禁用自动 git commit（只跑不交） |
| `--auto-confirm-mode` | str | `smart` | `smart` / `whitelist-only` / `blacklist-only` |
| `--auto-security-analyzer` | str | `builtin` | `builtin` / `bandit` / `semgrep` |

### 3.6 Git 与作者

| Flag | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `--auto-git-author-name` | str | `Ralph Autonomous Agent` | commit 作者名 |
| `--auto-git-author-email` | str | `ralph@trae-multi-agent.local` | commit 作者邮箱 |

### 3.7 Notes（跨轮记忆）

| Flag | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `--auto-notes-path` | str | `notes.md` | notes 文件名（相对 `project_root`） |
| `--auto-max-size-kb` | int | `1024` | notes.md 最大大小（KB；超过则 trim） |
| `--auto-trim-keep-last-n` | int | `20` | trim 时保留最近 N 段 |

## 4. 配置文件（autonomous.yml）

Autonomous 配置采用两级合并：用户级 `~/.trae/autonomous.yml` + 项目级 `<project_root>/.trae/autonomous.yml`，项目级覆盖用户级。

### 4.1 完整示例

`./.trae/autonomous.yml`

```yaml
# 运行时硬上限
max_iterations: 30
max_tokens: 200000
stop_when: "all tests pass"
consecutive_failure_abort: 5

# 阶段与节奏
stage_order:
  - plan
  - dev
  - verify
  - fix
test_command: "python3 -m unittest discover -s tests -p 'test_*.py'"
test_timeout_sec: 600
backoff_base_sec: 2.0
backoff_max_sec: 120.0

# Git
git_author_name: "Ralph Bot"
git_author_email: "ralph@example.com"
auto_commit: true

# 防休眠
sleep_guard_enabled: true

# run 状态
run_dir: ".gnhf/runs"

# notes
max_size_kb: 512
trim_keep_last_n: 15
notes_path: "notes.md"

# 安全
confirm_mode: smart   # smart / whitelist-only / blacklist-only
risk_threshold: 5
security_analyzer: builtin   # builtin / bandit / semgrep
```

### 4.2 字段说明

详见 [`AutonomousConfig`](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/scripts/autonomous/config_loader.py#L18-L66)。未识别的字段会落入 `extra` dict，不影响主流程。

### 4.3 优先级

CLI flag > 项目级 `autonomous.yml` > 用户级 `autonomous.yml` > 默认值（`AutonomousConfig()` 字段默认值）

## 5. 四阶段工作流

每轮迭代按以下顺序执行 4 个阶段，每阶段有独立 handler，可在 `scripts/autonomous/handlers/` 下扩展。

```
+-------------------+    +-------------------+    +-------------------+    +-------------------+
|       PLAN        | -> |        DEV        | -> |      VERIFY       | -> |        FIX        |
| 规划本轮要做的子任务 |    | 实施子任务（写代码）|    | 跑 test_command   |    | 修复 verify 失败项 |
+-------------------+    +-------------------+    +-------------------+    +-------------------+
```

### 5.1 阶段返回值（StageResult）

```python
@dataclass
class StageResult:
    kind: str          # "success" / "retriable" / "fatal" / "ask"
    summary: str       # 简短描述（会写进 notes.md 与 state）
    artifacts: dict    # 阶段产物（diff stats / test output / 等）
```

### 5.2 阶段间短路

- 若 `plan` 返回 `ask`（需用户确认），整个迭代暂停等待。
- 若 `dev` 返回 `fatal`，直接 abort，退出码 = 2。
- 若 `verify` 返回 `retriable` 但本轮 `dev` 没产出 diff，自动跳到下一阶段（避免空跑）。
- `fix` 总是最后一阶段，调用 dispatcher adapter 让 LLM 看 verify 输出做最小修改。

## 6. 安全机制

### 6.1 SmartConfirmation 三态决策

`SmartConfirmation.check(cmd)` 返回 `ConfirmationResult`，决策如下：

| 命令特征 | 决策 | 含义 |
| --- | --- | --- |
| 命中黑名单（`rm -rf /`、`DROP DATABASE`、`git push --force` 等） | `DENY` | 立即拒绝，不会执行 |
| 风险分 ≤ 30 | `AUTO` | 自动放行 |
| 风险分 31-70 且命中白名单 | `AUTO` | 放行 |
| 风险分 31-70 且未命中白名单 | `ASK` | 暂停迭代等待用户 |
| 风险分 ≥ 71 | `ASK` | 强制人工确认 |

### 6.2 风险评分（0-100）

- 黑名单命令 → `CRITICAL`（直接 DENY）
- 删除/格式化/重置 → +60
- 网络写操作（push、deploy） → +40
- 大范围查找/扫描（find /、grep -r） → +20
- 状态查询（git status、ls、cat） → -30
- 写测试文件且在 `tests/` 目录 → -20

### 6.3 自动加载 Skill

`AutoSkillLoader.detect_for_task(task_text)` 根据任务文本中的关键词匹配 `~/.trae/skills/*/skills-index.json`，返回优先级排序的 skill 列表。匹配规则：

- 文件路径类（`tests/`、`docs/`、`scripts/`）→ 加载对应领域 skill
- 关键词类（"安全"、"性能"、"重构"）→ 加载专题 skill
- 默认加载：`trae-multi-agent` 自身 + `multi-agent-team` 协调

每轮 dev 阶段会把命中的 skill 注入 dispatcher 的 prompt，从而实现"自动使用 skill"。

## 7. 状态持久化与续跑

### 7.1 RunState 文件结构

```
<project_root>/.gnhf/runs/<run_id>/
├── state.json         # 当前状态
├── state.json.bak     # 最近一次备份
├── notes.md           # 跨轮 notes（每个 run 独立）
└── manifest.json      # 列出 uncommitted files
```

`state.json` 字段：

```json
{
  "schema_version": 1,
  "run_id": "r-20260607-xxx",
  "objective": "实现 LRU 缓存",
  "status": "running",
  "iter_index": 3,
  "consecutive_failures": 0,
  "commits_made": 3,
  "cumulative_tokens": 12345,
  "created_at": "2026-06-07T01:23:45Z",
  "updated_at": "2026-06-07T01:25:11Z",
  "integrity_sha256": "abc123...",
  "stop_when": "all tests pass"
}
```

### 7.2 完整性校验

`RunState.verify_integrity()` 用 SHA256 校验内存中缓存的摘要与磁盘上的 `state.json` 是否一致。**不通过**会自动从 `state.json.bak` 恢复（`restore_from_backup()`）。

### 7.3 Resume

两种方式续跑：

```bash
# 指定 run_id
python -m cli.main --autonomous --auto-resume r-20260607-xxx ...

# 续最新一个
python -m cli.main --autonomous --auto-resume-latest ...
```

可续跑的前提：`get_resume_context().can_resume == True`，即状态非 `pending` / `completed` / `aborted`。

### 7.4 Crash Recovery

若进程被 SIGKILL 或机器断电，下次启动时：

1. 加载 `state.json`（或 backup）
2. 校验 SHA256；不通过则用 backup
3. 续跑：跳过已完成 stage（`manifest.json` 中记录 uncommitted files）

## 8. 防休眠（Slee pGuard）

`SleepGuard` 在主循环入口 `acquire()`，出口 `release()`：

| 系统 | 后端 | 说明 |
| --- | --- | --- |
| macOS | `caffeinate -i -s` | 阻止 idle 与系统睡眠 |
| Linux | `systemd-inhibit` | 阻止 idle / sleep / shutdown |
| Windows / 其它 | `noop` | 不做事（仅记录） |

可通过 `--auto-no-caffeinate` 关闭（CI 环境必关）。

## 9. Notes（跨轮记忆）

`NotesMemory` 维护 `notes.md`：

- 每轮结束追加一个 `## Iteration N` 段落
- 段落中包含：`<!-- iter=N tags=... -->` 元注释
- 原子写入：先 `.tmp`、fsync、`rename`（避免半写）
- 自动 trim：超过 `--auto-max-size-kb` 时保留最近 N 段

LLM 在下一轮 dev 阶段会被 prompt 读取 `notes.md` 末尾段，从而"记得"上一轮做了什么。

## 10. 常见场景

### 10.1 夜间跑回归测试

```bash
python -m cli.main --autonomous \
    --task "把 tests/ 下所有失败用例修到通过" \
    --auto-stop-when "all tests pass" \
    --auto-max-iterations 100 \
    --auto-failure-abort 5
```

### 10.2 批量重构 + 自动 commit

```bash
python -m cli.main --autonomous \
    --task "把所有 print 替换为 logging.info" \
    --auto-test-command "python3 -m unittest discover" \
    --auto-stage-order "plan,dev,verify"
```

每轮 dev 完成后会自动 `git add` + `git commit -m "Iteration N: ..."`（前提：当前是 git 仓库）。

### 10.3 Resume 续跑

第一次跑崩了：

```bash
python -m cli.main --autonomous --auto-resume-latest ...
```

### 10.4 CI 集成

```bash
# CI 环境：禁用防休眠、关闭自动 commit、降低 token 预算
python -m cli.main --autonomous \
    --auto-no-caffeinate \
    --auto-no-commit \
    --auto-max-iterations 5 \
    --auto-failure-abort 2
```

## 11. 故障排查

| 现象 | 排查方向 |
| --- | --- |
| 启动时直接 abort | 检查 `--auto-failure-abort` 是否过小；查看 `state.json.status` |
| 每轮都 `ASK` | 风险分偏高 → 改 `--auto-confirm-mode whitelist-only` |
| 不写 commit | `--auto-no-commit` 是否为 True；当前目录是否 git 仓库 |
| `stop_when` 永远不命中 | 关键字用空格分隔的 substring 匹配；确认 LLM 输出的 summary 包含这些词 |
| notes.md 暴涨 | 调小 `--auto-max-size-kb`；或调小 `--auto-trim-keep-last-n` |
| resume 失败 | `state.json` 损坏 → 看 `state.json.bak`；backup 都没了则需 `--auto-resume` 新 run |
| macOS 仍然睡眠 | 确认 `caffeinate` 可执行；`pmset -g` 查看 assertions |

## 12. 架构概览

```
                  +--------------------+
   --autonomous   |  RalphAutonomous-  |
   ----------->   |       Plugin       |  priority=5
                  +---------+----------+
                            |
                            v
                  +--------------------+
                  |  RalphLoopController|
                  +----+----+----+-----+
                       |    |    |
       +---------------+    |    +----------------+
       |                    |                     |
       v                    v                     v
  +---------+         +-----------+         +-----------+
  |  PLAN   |  --->   |    DEV    |  --->   |  VERIFY   |  --->  FIX
  +---------+         +-----------+         +-----------+
       |                    |                     |
       +-- StageHandler ----+-- StageHandler -----+-- StageHandler
                            |
                            v
                    +---------------+
                    | Dispatcher-   |   <-- 注入 autonomous=True
                    |   Adapter     |       + 自动加载 skills
                    +-------+-------+
                            |
                            v
                    +---------------+
                    |   V3 Goal     |
                    |  Dispatcher   |
                    +---------------+
```

每轮迭代内：

1. `LoopController.run_one_iteration(iter_index)`
2. 依次调用 4 个 stage handler
3. 每个 handler 通过 `DispatcherAdapter.invoke()` 调用 V3 dispatcher
4. 阶段返回 `StageResult` → 聚合 → `RunState.record_iteration()` 持久化
5. 触发 `GitDriver.commit()`（若 dev 阶段有 diff）
6. 追加 `NotesSection` 到 `notes.md`
7. 检查 `stop_when` / 硬上限 → 决定是否继续下一轮

## 13. 进阶：扩展自定义 Stage

```python
# scripts/autonomous/handlers/my_handler.py
from autonomous.handlers.base import StageHandler, StageResult, StageContext

class MyHandler(StageHandler):
    name = "my"
    kind = "my-stage"

    def handle(self, ctx: StageContext) -> StageResult:
        # 你的逻辑
        return StageResult(kind="success", summary="done", artifacts={})
```

注册（在 `RalphAutonomousPlugin._build_handlers` 中）：

```python
from autonomous.handlers.my_handler import MyHandler

handlers = {
    StageKind.PLAN: ...,
    StageKind.DEV: ...,
    StageKind.VERIFY: MyHandler(),  # 覆盖默认
    StageKind.FIX: ...,
}
```

## 14. 进阶：自定义风险规则

```python
from autonomous.smart_confirmation import SmartConfirmation, RiskLevel

sc = SmartConfirmation()

# 加入自定义白名单
sc.whitelist_patterns.add(r"^my-safe-cmd\s+--dry-run$")

# 检查
result = sc.check("my-safe-cmd --dry-run")
assert result.decision.value == "auto"
```

详见 [`SmartConfirmation`](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/scripts/autonomous/smart_confirmation.py)。

## 15. 与 V3 Dispatcher 的对接

`DispatcherAdapter` 把 autonomous 的"轮次上下文"翻译成 V3 dispatcher 期望的输入：

```python
adapter.invoke(
    task=objective,
    project_root=project_root,
    loop=False,            # autonomous 自己跑循环，dispatcher 不再 loop
    max_iterations=1,      # 单次 invoke 不递归
    autonomous=True,       # 注入 autonomous 上下文（让 LLM 知道自己在 autonomous 中）
    skills=auto_skill_loader.detect_for_task(objective),
    iter_index=iter_index,
    notes=notes_memory.tail(n=3),  # 最近 3 段 notes
    prior_summary=state.last_summary,
)
```

返回的 `AdapterInvokeResult` 会被翻译成 `StageResult`，从而供下一阶段使用。

## 16. 版本与兼容

- 引入版本：Phase 18（v2.5+）
- 状态 schema：`schema_version = 1`（后续可能升级，向后兼容）
- 配置文件：YAML 子集（不支持 anchor/alias、多行 block scalar）
- Python：3.10+（依赖 `dataclasses`、`match/case`、`typing.ParamSpec`）

## 17. 相关文档

- [PHASE18_PLAN.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE18_PLAN.md) - Phase 18 设计文档
- [USAGE_GUIDE.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/guides/USAGE_GUIDE.md) - 总使用指南
- [DYNAMIC_WORKFLOWS_INTEGRATION.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md) - 与动态工作流的集成
- [gnhf (GitHub)](https://github.com/kunchenguid/gnhf) - 借鉴的设计灵感
