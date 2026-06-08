# Autonomous 模式使用指南

> **目标**：让多角色团队在你睡觉时也一直在干活 —— 不再需要频繁点击"运行"或"确认"，自主完成 plan → dev → verify → fix 四阶段，自动测试、自动提交、自动恢复。

本指南基于 [kunchenguid/gnhf](https://github.com/kunchenguid/gnhf)（Ralph 风格自主循环）的核心理念，落地在 trae-multi-agent Phase 18 自主模式。

---

## 1. 5 分钟上手

### 1.1 一行命令启动自主模式

```bash
python3 -m scripts.cli --autonomous --task "实现 X 功能并补全测试"
```

执行后，CLI 会：

1. 调度 `RalphAutonomousPlugin`（与 `--loop / --multi-goal / --goal-cancel / --goal-graph / --goal-resume` 互斥）。
2. 创建一个 `run_id`（如 `r-20260607-153012-abc123`），状态写入 `.gnhf/runs/<run_id>/state.json`。
3. 启用 `caffeinate`（macOS）/ `systemd-inhibit`（Linux）防止系统休眠。
4. 循环执行四阶段：`plan → dev → verify → fix`，直到命中 `stop_when` / `max_iterations` / 致命错误。

### 1.2 默认行为

| 维度 | 默认值 | 含义 |
| --- | --- | --- |
| `max_iterations` | 50 | 硬上限，超过即停 |
| `max_tokens` | 500000 | 累计 token 预算 |
| `stage_order` | plan,dev,verify,fix | 阶段顺序 |
| `test_command` | `python3 -m unittest discover -s tests -p "test_*.py"` | 测试命令 |
| `auto_commit` | True | 每轮自动 commit |
| `sleep_guard_enabled` | True | caffeinate / systemd-inhibit |
| `confirm_mode` | smart | 黑/白名单 + 风险评分 |
| `risk_threshold` | 5 | 评分 ≤ 阈值自动批准 |
| `consecutive_failure_abort` | 3 | 连续失败 3 次 abort |
| `notes_path` | `notes.md` | 跨轮记忆文件 |
| `run_dir` | `.gnhf/runs` | run 状态目录 |

### 1.3 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | 全部成功 |
| `1` | 部分失败（达到 `max_iterations` 仍有失败） |
| `2` | 致命错误 abort |
| `3` | 命中 `stop_when` 条件 |

---

## 2. 17 个 CLI 标志详解

> 所有 `--auto-*` 标志仅在 `--autonomous` 同时启用时生效。

### 2.1 启用开关

| 标志 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `--autonomous` | flag | False | 启用 Ralph 风格自主模式 |

### 2.2 循环控制

| 标志 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `--auto-max-iterations N` | int | 50 | 最大迭代次数（1-1000） |
| `--auto-max-tokens N` | int | 500000 | 累计 token 预算 |
| `--auto-stop-when "phrase"` | str | `""` | 自然语言停止条件（如 `"all tests pass"`） |
| `--auto-stage-order "a,b,c"` | CSV | `plan,dev,verify,fix` | 阶段顺序（支持任意排列） |

### 2.3 测试与重试

| 标志 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `--auto-test-command CMD` | str | `python3 -m unittest discover` | 测试命令 |
| `--auto-backoff-base SEC` | float | 1.0 | 失败退避基数 |
| `--auto-backoff-max SEC` | float | 60.0 | 退避上限 |
| `--auto-failure-abort N` | int | 3 | 连续失败 abort 阈值 |

### 2.4 Run 生命周期

| 标志 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `--auto-resume RUN_ID` | str | None | resume 指定 run |
| `--auto-resume-latest` | flag | False | resume 最新可续跑的 run |

### 2.5 系统行为

| 标志 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `--auto-no-caffeinate` | flag | False | 禁用 caffeinate / systemd-inhibit（CI 环境） |
| `--auto-no-commit` | flag | False | 禁用自动 commit（仅记录，不提交） |

### 2.6 确认与安全

| 标志 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `--auto-confirm-mode MODE` | enum | smart | smart / whitelist-only / blacklist-only |

### 2.7 路径与作者

| 标志 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `--auto-run-dir PATH` | str | `.gnhf/runs` | run 状态目录（相对 project_root） |
| `--auto-git-author-name NAME` | str | `Ralph Autonomous Agent` | commit 作者名 |
| `--auto-git-author-email EMAIL` | str | `ralph@trae-multi-agent.local` | commit 作者邮箱 |

### 2.8 安全与 notes

| 标志 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `--auto-security-analyzer NAME` | enum | builtin | builtin / bandit / semgrep |
| `--auto-notes-path FILE` | str | `notes.md` | notes 文件名 |
| `--auto-max-size-kb N` | int | 1024 | notes.md 最大 KB |
| `--auto-trim-keep-last-n N` | int | 20 | trim 时保留段落数 |

### 2.9 完整示例

```bash
python3 -m scripts.cli \
  --autonomous \
  --task "实现用户登录接口并补全单测" \
  --auto-max-iterations 20 \
  --auto-stop-when "all tests pass" \
  --auto-test-command "python3 -m unittest discover -s tests -p 'test_*.py'" \
  --auto-backoff-base 2.0 \
  --auto-backoff-max 120.0 \
  --auto-failure-abort 5 \
  --auto-confirm-mode whitelist-only \
  --auto-run-dir .gnhf/runs \
  --auto-git-author-name "Night Owl" \
  --auto-git-author-email "owl@example.com" \
  --auto-notes-path log.md
```

---

## 3. 配置文件（YAML）

### 3.1 路径与覆盖

| 路径 | 范围 | 优先级 |
| --- | --- | --- |
| `~/.trae/autonomous.yml` | 用户级 | 低 |
| `<project_root>/.trae/autonomous.yml` | 项目级 | **高**（覆盖用户级） |

项目级同名键会覆盖用户级；嵌套 dict 递归合并。

### 3.2 完整 schema

```yaml
# ~/.trae/autonomous.yml 或 <project_root>/.trae/autonomous.yml

# 循环控制
max_iterations: 50              # int, [1, 1000]
max_tokens: 500000              # int
stop_when: "all tests pass"     # str（自然语言）
stage_order:                    # list[str]
  - plan
  - dev
  - verify
  - fix

# 重试
backoff_base_sec: 1.0           # float
backoff_max_sec: 60.0           # float
consecutive_failure_abort: 3    # int

# 测试
test_command: "python3 -m unittest discover -s tests -p 'test_*.py'"
test_timeout_sec: 600.0

# 安全
security_analyzer: builtin      # builtin | bandit | semgrep
confirm_mode: smart             # smart | whitelist-only | blacklist-only
risk_threshold: 5               # int, 0-100

# Git
git_author_name: "Ralph Autonomous Agent"
git_author_email: "ralph@trae-multi-agent.local"
auto_commit: true

# 系统
sleep_guard_enabled: true
run_dir: ".gnhf/runs"

# notes
notes_path: "notes.md"
max_size_kb: 1024
trim_keep_last_n: 20

# 未知字段会落入 .extra（不会丢失，便于后续扩展）
custom_flag: 42
```

### 3.3 YAML 解析能力

内置 `SimpleYAMLParser`，**不依赖 PyYAML**：

- ✅ 键值对（`key: value`）
- ✅ 嵌套 dict（2 空格缩进）
- ✅ 列表（`- item`）
- ✅ 标量：int / float / bool / null / string
- ❌ 不支持：anchor/alias、多行 block scalar、复杂 mapping

如需 anchor/alias，请把配置写入 JSON（通过 `extra` 字段或扩展 config loader）。

---

## 4. 四阶段工作流

### 4.1 阶段顺序

```
┌───────┐    ┌─────┐    ┌─────────┐    ┌─────┐
│ PLAN  │───▶│ DEV │───▶│ VERIFY  │───▶│ FIX │──┐
└───────┘    └─────┘    └─────────┘    └─────┘  │
                                               │
                              ┌────────────────┘
                              ▼
                       （进入下一轮 iter）
```

每轮迭代都会跑完所有四个阶段。任意阶段产出 `fatal` 错误 → 整轮 abort。

### 4.2 各阶段职责

| 阶段 | 任务 | 输出 |
| --- | --- | --- |
| **PLAN** | 拆解目标、识别依赖、生成执行计划 | `plan.md` / `artifacts["plan"]` |
| **DEV** | 写代码、跑测试 skill 改写 | `patches` / diff |
| **VERIFY** | 执行 `test_command`、安全分析 | `pass/fail` + 测试日志 |
| **FIX** | 根据 verify 失败修复 | 修复后的 patches |

### 4.3 自定义 stage_order

```bash
# 只要 dev + verify
python3 -m scripts.cli --autonomous --task "X" --auto-stage-order "dev,verify"
```

注意：空 `stage_order` 会 raise `ValueError`。

---

## 5. 安全：智能确认（SmartConfirmation）

### 5.1 三种确认模式

| 模式 | 行为 |
| --- | --- |
| `smart` | 默认。黑名单 + 白名单 + 风险评分；低风险自动批准，高风险拒绝 |
| `whitelist-only` | 仅白名单命令自动批准；其他一律 ASK |
| `blacklist-only` | 仅黑名单命令拒绝；其他自动批准 |

### 5.2 决策结果

```python
from autonomous.smart_confirmation import SmartConfirmation
sc = SmartConfirmation()
result = sc.check("rm -rf /")  # → Decision.DENY（黑名单）
result = sc.check("git status")  # → Decision.AUTO（白名单/低风险）
result = sc.check("npm install foo")  # → Decision.ASK（未知，询问用户）
```

### 5.3 风险评分（0-100）

- `0`：完全安全（白名单）
- `1-5`（默认阈值）：低风险，自动批准
- `6-30`：中等风险，ASK
- `31-100`：高风险，DENY

可通过 `--auto-risk-threshold` 调整（需修改 config_loader；CLI 当前不暴露）。

### 5.4 黑名单示例

内置黑名单包括但不限于：

- `rm -rf /`、`rm -rf /*`
- `DROP DATABASE`、`TRUNCATE TABLE`
- `git push --force origin main`
- `mkfs`、`dd if=`、`shutdown`、`reboot`
- `chmod 777 /` 等

详见 [smart_confirmation.py](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/scripts/autonomous/smart_confirmation.py) 的 `_DEFAULT_BLACKLIST`。

---

## 6. 防休眠：SleepGuard

### 6.1 平台适配

| 平台 | 机制 | 命令 |
| --- | --- | --- |
| macOS | `caffeinate -di` | `caffeinate -di -w $$ sleep infinity` |
| Linux | `systemd-inhibit` | `systemd-inhibit --what=sleep:idle --why=...` |
| Windows / 其他 | noop | 直接返回 |

### 6.2 模式

- `AUTO`：自动检测平台（默认）
- `ON`：强制启用
- `OFF`：禁用（CI / 远程服务器）

### 6.3 禁用方法

```bash
# CI 环境推荐
python3 -m scripts.cli --autonomous --task "X" --auto-no-caffeinate
```

或配置文件：

```yaml
sleep_guard_enabled: false
```

---

## 7. 跨轮记忆：notes.md

### 7.1 文件结构

```markdown
# Autonomous Run Notes

> Generated by Ralph Autonomous Agent
> Run ID: r-20260607-153012
> Objective: 实现 X 功能

---

## Iteration 1: 初始化项目结构
<!-- iter=1 tags=plan,success -->
- 拆解任务：创建 ... 
- 识别依赖：...

## Iteration 2: 实现核心逻辑
<!-- iter=2 tags=dev,success -->
- 新增 module foo.py
- 修复 bar.py 中 NPE

## Final Summary
- 成功完成 5/5 子任务
- 测试全部通过
- 提交 12 次 commit
```

### 7.2 关键能力

| 能力 | 说明 |
| --- | --- |
| 段落追踪 | 每轮一个 `## ` 段，标题含 `iter_index` |
| 原子写入 | 先写 `.tmp` + `fsync` + `rename`（避免半写） |
| 自动 trim | 超过 `max_size_kb` 时按 `trim_keep_last_n` 裁剪 |
| 元数据注释 | `<!-- iter=N tags=... -->`，可被 LLM 直接解析 |
| token 估算 | 粗略 `chars/4`，不依赖 tiktoken |

详见 [notes_memory.py](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/scripts/autonomous/notes_memory.py)。

---

## 8. Run 状态与恢复

### 8.1 状态文件布局

```
<project_root>/.gnhf/runs/<run_id>/
├── state.json           # 当前状态（含 SHA256 校验和）
├── state.json.bak       # 上一次成功持久化的备份
└── notes.md             # 该 run 的 notes（可选；默认在 project_root）
```

### 8.2 state.json 字段

```json
{
  "run_id": "r-20260607-153012-abc123",
  "objective": "实现 X 功能",
  "status": "running",          // pending | running | success | failed | aborted
  "iter_index": 5,
  "commits_made": 3,
  "consecutive_failures": 0,
  "cumulative_tokens": 12345,
  "uncommitted_paths": [],       // 上一轮未提交的路径（用于 rollback 后恢复）
  "stop_when": "all tests pass",
  "started_at": "2026-06-07T15:30:12Z",
  "updated_at": "2026-06-07T15:45:33Z"
}
```

### 8.3 完整性保护

- **SHA256 校验**：每次 `persist()` 写入 `checksum` 字段；加载时 `verify_integrity()` 校验。
- **backup + restore**：`state.json.bak` 保留上一次成功状态。若 `state.json` 损坏，调用 `restore_from_backup()` 自动恢复。

### 8.4 Resume 流程

```bash
# 1) 查看所有 run
ls .gnhf/runs/

# 2) resume 指定 run
python3 -m scripts.cli --autonomous --auto-resume r-20260607-153012-abc123 --task "继续 X"

# 3) resume 最新可续跑的 run
python3 -m scripts.cli --autonomous --auto-resume-latest --task "继续 X"
```

**Resume 上下文**：

- ✅ iter_index：已完成的轮次
- ✅ 累计 commits
- ✅ uncommitted 路径列表（避免 rollback 时丢失未提交 work）
- ❌ 不在 pending 状态：pending 不能 resume（需先启动）

---

## 9. Git 集成与回滚

### 9.1 自动 commit

- 每轮 dev/fix 完成后，若 `auto_commit=true`，执行 `git commit -m "..." --author "..."`。
- 失败时不抛异常，状态标记为 `committed=False` 继续下一轮。

### 9.2 安全回滚

当 SmartConfirmation DENY 某个高危命令时：

1. `git_driver.rollback()` 还原到上一轮 commit。
2. `state.json` 中 `uncommitted_paths` 字段记录被还原的文件路径。
3. 下一轮 iter 启动时，`dispatcher_adapter` 读取 `uncommitted_paths` 并重新注入上下文。

详见 [git_driver.py](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/scripts/autonomous/git_driver.py)。

### 9.3 非 git 项目

- `is_git_repo() == False` 时，所有 `commit/rollback/diff_stats` 返回 `success=True, stdout=""`，不抛异常。
- 此时仅靠 `notes.md` + `state.json` 记录工作流。

---

## 10. 自动技能加载（AutoSkillLoader）

### 10.1 工作原理

每轮 plan 阶段前，扫描 `~/.trae/skills/` 与 `<project_root>/.trae/skills/`，根据任务关键词匹配相关 skill。

### 10.2 优先级

| 来源 | 优先级 |
| --- | --- |
| 项目内 `.trae/skills/` | **高**（覆盖用户级） |
| 用户 `~/.trae/skills/` | 低 |
| 内置 skill（trae-multi-agent 自带） | 最低 |

### 10.3 匹配规则

- skill 的 `SKILL.md` / `skill.yml` 中声明的 `keywords` 与任务描述做大小写不敏感子串匹配。
- 按命中数排序，**最多返回 5 个 skill**。

详见 [auto_skill_loader.py](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/scripts/autonomous/auto_skill_loader.py)。

---

## 11. Dispatcher 适配

### 11.1 架构关系

```
RalphAutonomousPlugin
        ↓
DispatcherAdapter
        ↓
GoalDispatcher (V3 现有调度器)
        ↓
角色 plugins: architect / pm / solo-coder / test-expert / ui-designer
```

Autonomous 是**上层编排**，不替代 V3 dispatcher。

### 11.2 注入字段

`dispatcher_adapter` 在每次 invoke 时自动注入：

```python
{
  "autonomous": True,
  "run_id": "r-xxx",
  "iter_index": 3,
  "loop": False,        # 关闭内层 V3 --loop（避免双重循环）
  "max_iterations": 1,  # V3 dispatcher 单次
  "uncommitted_paths": [...],
}
```

详见 [dispatcher_adapter.py](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/scripts/autonomous/dispatcher_adapter.py)。

### 11.3 错误分类

| 类型 | 行为 |
| --- | --- |
| `success` | 本轮 OK，进入下一轮 |
| `retriable` | 触发 `backoff_sleep(attempt)`，consecutive_failures+1 |
| `fatal` | 整轮 abort，exit_code=2 |

---

## 12. 完整运行示例

### 12.1 准备工作

```bash
# 1. 克隆 trae-multi-agent 技能
git clone <repo-url> .trae/skills/trae-multi-agent
cd .trae/skills/trae-multi-agent

# 2. 安装依赖
pip install -r requirements.txt

# 3. （可选）创建项目级配置
mkdir -p .trae
cat > .trae/autonomous.yml <<'EOF'
max_iterations: 30
test_command: "python3 -m unittest discover -s tests -p 'test_*.py'"
auto_commit: true
sleep_guard_enabled: true
EOF
```

### 12.2 启动自主模式

```bash
# 启动并跑过夜
python3 -m scripts.cli \
  --autonomous \
  --project-root /path/to/your/project \
  --task "为项目添加 OpenAPI 文档生成、补全所有 API 的单元测试、修复 lint 错误" \
  --auto-stop-when "all tests pass"
```

### 12.3 查看进度

```bash
# 1. 查看 run_id
ls .gnhf/runs/

# 2. 查看 state.json
cat .gnhf/runs/r-xxx/state.json

# 3. 查看 notes.md
cat notes.md
```

### 12.4 中断后恢复

```bash
# 第二天早上接着跑
python3 -m scripts.cli \
  --autonomous \
  --project-root /path/to/your/project \
  --auto-resume-latest
```

### 12.5 关闭自主模式

- 正常退出：完成 `stop_when` 或达到 `max_iterations`。
- 强制停止：`Ctrl-C` → sleep_guard 释放 → state 持久化为 `aborted`。
- 紧急 abort：连续失败达到 `consecutive_failure_abort` → 退出码 `2`。

---

## 13. 测试与验证

### 13.1 单元测试

```bash
cd .trae/skills/trae-multi-agent/scripts

# Phase 18 单元测试（约 100+ 用例）
python3 -m unittest tests.test_phase18_config \
                   tests.test_phase18_git_driver \
                   tests.test_phase18_notes_memory \
                   tests.test_phase18_run_state \
                   tests.test_phase18_sleep_guard \
                   tests.test_phase18_smart_confirmation \
                   tests.test_phase18_auto_skill_loader \
                   tests.test_phase18_handlers \
                   tests.test_phase18_dispatcher_adapter \
                   tests.test_phase18_loop_controller \
                   tests.test_phase18_autonomous_plugin \
                   tests.test_phase18_cli \
                   -v
```

### 13.2 集成测试（12 个端到端场景）

```bash
python3 -m unittest tests.test_phase18_integration -v
```

### 13.3 测试脚本

```bash
# 一键运行
bash tests/scripts/run_phase18_all.sh
# 包含：unit、integration、e2e basic、e2e resume、e2e safety、regression
```

### 13.4 回归测试

```bash
# V3 现有插件测试
bash tests/scripts/run_v3_plugin_tests.sh

# V2 回归
bash tests/scripts/run_v2_regression.sh
```

### 13.5 覆盖率

```bash
python3 -m coverage run --source=autonomous -m unittest discover tests
python3 -m coverage report
python3 -m coverage html  # → htmlcov/index.html
```

详见 `tests/coverage_analysis.py`。

---

## 14. 故障排查

| 现象 | 排查 |
| --- | --- |
| `--autonomous` 启动后立刻退出 | 检查 `state.json` 状态：是否处于 `aborted/failed` 且不可 resume |
| 一直停在同一轮 | `consecutive_failures` 累计达到 `consecutive_failure_abort` → 检查 test_command 是否正确 |
| notes.md 增长失控 | 调小 `--auto-max-size-kb`（默认 1024KB）或增大 `--auto-trim-keep-last-n` |
| 远程仓库 push 失败 | 检查 `git_author_name/email`；非 git 仓库时 `is_git_repo` 判定 |
| macOS caffeinate 报权限 | 在系统设置 → 隐私与安全 → 允许 caffeinate 终端控制 |
| 调度器死锁 | 检查 `GoalDispatcher` 的 `mutex_with` 是否对称；`autonomous` 必须与 `loop / multi-goal / goal-cancel / goal-graph / goal-resume` 互斥 |
| Resume 失败：`can_resume=False` | 状态为 `pending` 时不可 resume；需用 `--auto-resume RUN_ID` 而不是 `--auto-resume-latest` |

---

## 15. 架构与扩展

### 15.1 模块清单

```
scripts/autonomous/
├── __init__.py                # 公共导出
├── loop_controller.py         # RalphLoopController（主循环）
├── config_loader.py           # AutonomousConfig + SimpleYAMLParser
├── git_driver.py              # GitDriver（commit/rollback/diff_stats）
├── notes_memory.py            # NotesMemory（跨轮记忆）
├── run_state.py               # RunState（持久化 + 完整性 + 恢复）
├── sleep_guard.py             # SleepGuard（caffeinate/systemd-inhibit）
├── smart_confirmation.py      # SmartConfirmation（黑/白名单 + 风险评分）
├── auto_skill_loader.py       # AutoSkillLoader（自动加载 skill）
├── dispatcher_adapter.py      # DispatcherAdapter（适配 V3 dispatcher）
└── handlers/                  # 4 阶段 handler
    ├── base.py                # StageHandler / StageResult / StageContext
    ├── plan_handler.py
    ├── dev_handler.py
    ├── verify_handler.py
    └── fix_handler.py
```

### 15.2 与 V3 plugin 体系集成

- 入口：`plugins/autonomous.py: RalphAutonomousPlugin`（`priority=5`）
- 注册：`plugins/__init__.py: PLUGINS` 列表追加
- 互斥：所有 V3 插件的 `mutex_with` 已包含 `"autonomous"`

### 15.3 扩展点

| 想做什么 | 改哪里 |
| --- | --- |
| 新增 CLI flag | `cli/parser.py` + `plugins/autonomous.py: _apply_args` |
| 新增配置字段 | `config_loader.py: AutonomousConfig` + `SimpleYAMLParser` |
| 替换 stage handler | `plugins/autonomous.py: _build_handlers` |
| 新增 sleep backend | `sleep_guard.py: SleepGuard._acquire_<platform>` |
| 新增 security analyzer | `security/` 目录（builtin/bandit/semgrep） |
| 自定义风险评分 | `smart_confirmation.py: SmartConfirmation.score` |

### 15.4 不修改现有代码的边界

Autonomous 模式**不修改** V3 dispatcher、V2 workflow engine、任何现有 plugin。V3 插件的 `mutex_with` 是通过测试脚本验证的最小改动（追加一个字符串）。

---

## 16. 最佳实践

1. **必设 `stop_when`**：明确结束条件，避免无限循环。
2. **CI 环境关闭 caffeinate**：`--auto-no-caffeinate`。
3. **小步迭代**：把大任务拆为多个 `--task` 调用，每次只跑 autonomous 模式处理一段。
4. **定期查看 notes.md**：避免最后才发现方向偏了。
5. **保留 `state.json.bak`**：完整 state 在 resume 时可避免丢失上下文。
6. **设置 `consecutive_failure_abort` 较小值**（如 3-5）：避免无意义重试烧 token。
7. **不要在 autonomous 模式下手动改文件**：会破坏 git 状态和 uncommitted_paths 追踪。
8. **始终用项目级 `.trae/autonomous.yml`**：避免污染用户级配置。

---

## 17. 常见问答（FAQ）

**Q: 和 `--loop` 的区别？**
A: `--loop` 是 V3 dispatcher 内的循环（单角色重复）；`--autonomous` 是四阶段（plan→dev→verify→fix）的全流程循环 + 跨轮记忆 + 自动 commit + 安全确认。

**Q: 跑过夜会不会烧很多 token？**
A: 默认 `max_tokens=500_000`。可在 YAML 中调小，或通过 `stop_when` 提前结束。

**Q: 必须用 git 吗？**
A: 不必须。`GitDriver` 在非 git 仓库下退化为 no-op，仍能跑完整工作流（只不自动 commit）。

**Q: 如何看每轮发生了什么？**
A: `notes.md`（人类/LLM 可读） + `state.json`（程序可读） + git log（如果开了 commit）。

**Q: 能跑多个 autonomous run 并行吗？**
A: 不建议。每个 run 有自己的 `run_id` + 独立 `state.json`，但 `notes_path` 默认共享，可能产生写入竞争。如需并行，可为每个 run 指定不同的 `--auto-run-dir` 和 `--auto-notes-path`。

**Q: resume 是精确恢复还是近似？**
A: 精确恢复 `iter_index / commits / uncommitted_paths`；LLM 上下文由 `notes.md` 重建。

---

## 18. 参考资料

- 原始设计：[kunchenguid/gnhf](https://github.com/kunchenguid/gnhf)（Ralph 风格自主循环）
- 开发计划：[PHASE18_PLAN.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE18_PLAN.md)
- 架构对比：[ARCHITECTURE_COMPARISON.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/ARCHITECTURE_COMPARISON.md)
- V3 插件集成：[DYNAMIC_WORKFLOWS_INTEGRATION.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md)
- 整体使用：[USAGE_GUIDE.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/guides/USAGE_GUIDE.md)

---

> **最后更新**：2026-06-07 · Phase 18
