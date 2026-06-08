#!/bin/bash
# Phase 18 E2E 基础测试脚本
# 用途：在临时 git 仓库中跑 5 轮 autonomous 循环，验证 commits、notes 累积、run state
# 退出码：0=成功，1=失败

set -e
set -o pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
SCRIPTS_DIR="${PROJECT_ROOT}/scripts"

PYTHON_BIN="${PYTHON_BIN:-python3}"
export PYTHONPATH="${SCRIPTS_DIR}:${PYTHONPATH}"

echo -e "${YELLOW}==== Phase 18 E2E 基础测试 ====${NC}"

# 临时目录
TMPDIR_ROOT="$(mktemp -d -t phase18-e2e-basic-XXXXXX)"
trap "rm -rf '$TMPDIR_ROOT'" EXIT

# 1. 准备临时 git 仓库
TEST_REPO="${TMPDIR_ROOT}/test_repo"
mkdir -p "$TEST_REPO"
cd "$TEST_REPO"
git init -b main 2>&1 | grep -v "hint:" || true
git config user.email "ralph-e2e@test.local"
git config user.name "Ralph E2E"
# 初始化 README
echo "# Test" > README.md
git add README.md
git commit -m "init" --quiet
echo -e "${GREEN}✓ 创建临时仓库: $TEST_REPO${NC}"

# 2. 创建测试文件
mkdir -p .trae/skills
cat > .trae/skills/test_skill.json <<'EOF'
{
  "name": "test-skill",
  "description": "test skill for e2e",
  "triggers": ["test", "e2e"],
  "priority": 10,
  "version": "1.0.0"
}
EOF

# 3. 真实跑 5 轮 autonomous 循环（使用 stub dispatcher）
cat > /tmp/ralph_e2e_basic.py <<'PYEOF'
"""E2E 基础测试脚本：真实跑 5 轮 autonomous 循环。"""
import sys
import os
import subprocess
from pathlib import Path

# 准备路径
scripts_dir = Path(os.environ.get("SCRIPTS_DIR", "."))
sys.path.insert(0, str(scripts_dir))
os.chdir(os.environ["TEST_REPO"])

from autonomous.loop_controller import RalphLoopController, LoopConfig, StageKind
from autonomous.git_driver import GitDriver
from autonomous.notes_memory import NotesMemory
from autonomous.auto_skill_loader import AutoSkillLoader
from autonomous.smart_confirmation import SmartConfirmation
from autonomous.run_state import RunState
from autonomous.dispatcher_adapter import DispatcherAdapter
from autonomous.handlers.plan_handler import PlanHandler
from autonomous.handlers.dev_handler import DevHandler
from autonomous.handlers.verify_handler import VerifyHandler
from autonomous.handlers.fix_handler import FixHandler
from autonomous.handlers.base import StageResult


# Stub handler - 真实生成一些变更 + 跑测试
class StubDevHandler(DevHandler):
    def do_handle(self, iter_ctx):
        # 真实修改一个文件
        work_file = Path("app.py")
        work_file.write_text(f"# iteration {iter_ctx.iter_index}\nprint('hello iter-{iter_ctx.iter_index}')\n")
        return StageResult(
            kind="success",
            summary=f"iter {iter_ctx.iter_index} 写入 app.py",
            artifacts={"tokens": 100},
        )


class StubVerifyHandler(VerifyHandler):
    def __init__(self, **kwargs):
        # 禁用测试命令（避免依赖外部测试框架）
        kwargs["test_command"] = ""
        super().__init__(**kwargs)

    def do_handle(self, iter_ctx):
        # 真实统计 diff stats
        diff_stats_data = (0, 0, 0, 0)
        if self._git_driver is not None and self._git_driver.is_git_repo():
            stats = self._git_driver.diff_stats()
            diff_stats_data = (
                stats.files_changed,
                stats.lines_added,
                stats.lines_removed,
                stats.binary_files,
            )
        return StageResult(
            kind="success",
            summary="verify stub 通过",
            artifacts={
                "test_results": [1, 0, 0],
                "diff_stats": list(diff_stats_data),
                "security_issues": [],
            },
        )


def main():
    # 准备组件
    project_root = Path(os.environ["TEST_REPO"])
    run_id = "e2e-basic-test-001"
    run_dir = project_root / ".gnhf" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # 构造 LoopConfig
    config = LoopConfig(
        max_iterations=5,
        max_tokens=10_000_000,
        stop_when="",
        stage_order=[StageKind.PLAN, StageKind.DEV, StageKind.VERIFY, StageKind.FIX],
        backoff_base_sec=0.01,
        backoff_max_sec=0.1,
        consecutive_failure_abort=10,
    )

    # RunState
    run_state = RunState(run_dir, run_id, objective="E2E 基础测试")

    # NotesMemory
    notes_memory = NotesMemory(run_dir / "notes.md", max_size_kb=1024, trim_keep_last_n=20)

    # GitDriver
    git_driver = GitDriver(
        repo_root=project_root,
        run_id=run_id,
        author_name="Ralph E2E",
        author_email="ralph-e2e@test.local",
        run_dir=run_dir,
    )

    # AutoSkillLoader
    auto_skill_loader = AutoSkillLoader(project_root=project_root)

    # SmartConfirmation
    smart_confirmation = SmartConfirmation()

    # DispatcherAdapter (stub)
    dispatcher_adapter = DispatcherAdapter(facade_module=None)

    # Handlers
    plan_handler = PlanHandler(auto_skill_loader=auto_skill_loader, notes_memory=notes_memory)
    dev_handler = StubDevHandler(
        dispatcher_adapter=dispatcher_adapter,
        smart_confirmation=smart_confirmation,
        auto_skill_loader=auto_skill_loader,
    )
    verify_handler = StubVerifyHandler(git_driver=git_driver)
    fix_handler = FixHandler(dispatcher_adapter=dispatcher_adapter, max_fix_attempts=1)

    stage_handlers = {
        StageKind.PLAN: plan_handler,
        StageKind.DEV: dev_handler,
        StageKind.VERIFY: verify_handler,
        StageKind.FIX: fix_handler,
    }

    # 跑循环
    loop = RalphLoopController(
        config=config,
        project_root=project_root,
        git_driver=git_driver,
        notes_memory=notes_memory,
        auto_skill_loader=auto_skill_loader,
        smart_confirmation=smart_confirmation,
        run_state=run_state,
        dispatcher_adapter=dispatcher_adapter,
        stage_handlers=stage_handlers,
        objective="E2E 基础测试",
        log=lambda level, msg: print(f"[{level}] {msg}", file=sys.stderr),
        sleep_guard=None,  # 跳过 sleep guard
    )

    print(f"开始跑 {config.max_iterations} 轮 autonomous 循环")
    exit_code = loop.run()
    print(f"循环结束，退出码: {exit_code}")

    # 验证 1: git log 应该有 5 个 commit
    result = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    commits = [l for l in result.stdout.splitlines() if l.strip()]
    print(f"git log 行数: {len(commits)}")
    for c in commits:
        print(f"  {c}")

    assert len(commits) >= 6, f"应有至少 6 个 commit（1 init + 5 iterations），实际 {len(commits)}"
    print(f"✅ 验证 1 通过：git log 有 {len(commits)} 个 commit")

    # 验证 2: notes.md 应有 5 个段落
    notes_path = run_dir / "notes.md"
    notes_content = notes_path.read_text(encoding="utf-8")
    section_count = notes_content.count("## Iteration")
    print(f"notes.md 段落数: {section_count}")
    assert section_count == 5, f"应有 5 个段落，实际 {section_count}"
    print(f"✅ 验证 2 通过：notes.md 有 {section_count} 个段落")

    # 验证 3: state.json 状态应为 completed
    import json
    state_path = run_dir / "state.json"
    state_data = json.loads(state_path.read_text(encoding="utf-8"))
    status = state_data["state"]["status"]
    iter_index = state_data["state"]["iter_index"]
    print(f"state.json status: {status}, iter_index: {iter_index}")
    assert status == "completed", f"status 应为 completed，实际 {status}"
    assert iter_index == 5, f"iter_index 应为 5，实际 {iter_index}"
    print(f"✅ 验证 3 通过：state.json 状态 completed，iter_index=5")

    # 验证 4: app.py 真实生成
    app_path = project_root / "app.py"
    assert app_path.exists(), "app.py 应存在"
    app_content = app_path.read_text()
    assert "hello iter-5" in app_content, "app.py 应包含 iter-5"
    print(f"✅ 验证 4 通过：app.py 真实生成并包含最新内容")

    print("\n🎉 E2E 基础测试全部通过！")
    return 0


if __name__ == "__main__":
    sys.exit(main())
PYEOF

# 设置环境变量并跑测试
export SCRIPTS_DIR
export TEST_REPO
echo -e "${YELLOW}▶ 跑 E2E 基础测试 ...${NC}"
if "$PYTHON_BIN" /tmp/ralph_e2e_basic.py 2>&1; then
    echo ""
    echo -e "${GREEN}✅ E2E 基础测试通过${NC}"
    exit 0
else
    echo ""
    echo -e "${RED}❌ E2E 基础测试失败${NC}"
    exit 1
fi
