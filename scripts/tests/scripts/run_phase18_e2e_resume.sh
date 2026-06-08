#!/bin/bash
# Phase 18 E2E 断点续跑测试脚本
# 用途：跑 3 轮后中断，resume 后继续从第 4 轮开始
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

echo -e "${YELLOW}==== Phase 18 E2E 断点续跑测试 ====${NC}"

# 临时目录
TMPDIR_ROOT="$(mktemp -d -t phase18-e2e-resume-XXXXXX)"
trap "rm -rf '$TMPDIR_ROOT'" EXIT

# 1. 准备临时 git 仓库
TEST_REPO="${TMPDIR_ROOT}/test_repo"
mkdir -p "$TEST_REPO"
cd "$TEST_REPO"
git init -b main 2>&1 | grep -v "hint:" || true
git config user.email "ralph-e2e@test.local"
git config user.name "Ralph E2E"
echo "# Test" > README.md
git add README.md
git commit -m "init" --quiet
echo -e "${GREEN}✓ 创建临时仓库: $TEST_REPO${NC}"

# 2. 创建断点续跑测试脚本
cat > /tmp/ralph_e2e_resume.py <<'PYEOF'
"""E2E 断点续跑测试：跑 3 轮后中断，resume 后继续。"""
import sys
import os
import subprocess
import json
from pathlib import Path

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


class StubDevHandler(DevHandler):
    def do_handle(self, iter_ctx):
        work_file = Path("app.py")
        work_file.write_text(f"# iter {iter_ctx.iter_index}\nprint('iter-{iter_ctx.iter_index}')\n")
        return StageResult(
            kind="success",
            summary=f"iter {iter_ctx.iter_index}",
            artifacts={"tokens": 50},
        )


class StubVerifyHandler(VerifyHandler):
    def __init__(self, **kwargs):
        kwargs["test_command"] = ""
        super().__init__(**kwargs)

    def do_handle(self, iter_ctx):
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
            summary="verify ok",
            artifacts={"test_results": [1, 0, 0], "diff_stats": list(diff_stats_data), "security_issues": []},
        )


def build_components(project_root, run_id, run_dir):
    """构造一组组件（共享给两次运行）。"""
    config = LoopConfig(
        max_iterations=10,
        max_tokens=10_000_000,
        stage_order=[StageKind.PLAN, StageKind.DEV, StageKind.VERIFY, StageKind.FIX],
        backoff_base_sec=0.01,
        backoff_max_sec=0.1,
        consecutive_failure_abort=10,
    )
    run_state = RunState(run_dir, run_id, objective="Resume E2E")
    notes_memory = NotesMemory(run_dir / "notes.md", max_size_kb=1024)
    git_driver = GitDriver(
        repo_root=project_root,
        run_id=run_id,
        author_name="Ralph",
        author_email="ralph@test.local",
        run_dir=run_dir,
    )
    auto_skill_loader = AutoSkillLoader(project_root=project_root)
    smart_confirmation = SmartConfirmation()
    dispatcher_adapter = DispatcherAdapter(facade_module=None)
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
    return config, run_state, notes_memory, git_driver, stage_handlers, dispatcher_adapter, smart_confirmation, auto_skill_loader


def run_n_iters(n, project_root, run_id, run_dir):
    """跑 N 轮。"""
    (config, run_state, notes_memory, git_driver, stage_handlers,
     dispatcher_adapter, smart_confirmation, auto_skill_loader) = build_components(
        project_root, run_id, run_dir
    )
    config.max_iterations = run_state.state.iter_index + n
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
        objective="Resume E2E",
        log=lambda level, msg: None,
        sleep_guard=None,
    )
    return loop.run()


def main():
    project_root = Path(os.environ["TEST_REPO"])
    run_id = "e2e-resume-001"
    run_dir = project_root / ".gnhf" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # 第一次跑 3 轮
    print("[Step 1] 第一次跑 3 轮...")
    exit_code = run_n_iters(3, project_root, run_id, run_dir)
    print(f"  退出码: {exit_code}")

    # 验证 state.json
    state_path = run_dir / "state.json"
    state_data = json.loads(state_path.read_text(encoding="utf-8"))
    iter_after_first = state_data["state"]["iter_index"]
    print(f"  第一次跑后 iter_index: {iter_after_first}")
    assert iter_after_first == 3, f"第一次跑后 iter_index 应为 3，实际 {iter_after_first}"
    print(f"  ✅ 第一次跑成功，iter_index=3")

    # 验证 notes.md
    notes_path = run_dir / "notes.md"
    notes_content = notes_path.read_text(encoding="utf-8")
    section_count = notes_content.count("## Iteration")
    print(f"  notes.md 段落数: {section_count}")
    assert section_count == 3, f"第一次跑后 notes.md 应有 3 段，实际 {section_count}"
    print(f"  ✅ 第一次跑后 notes.md 有 3 段")

    # 第二次跑：resume 模式 + 跑 5 轮（应该从 4 开始）
    print("\n[Step 2] Resume 跑 5 轮...")
    exit_code = run_n_iters(5, project_root, run_id, run_dir)
    print(f"  退出码: {exit_code}")

    # 验证 state.json
    state_data = json.loads(state_path.read_text(encoding="utf-8"))
    iter_after_second = state_data["state"]["iter_index"]
    print(f"  Resume 跑后 iter_index: {iter_after_second}")
    # 注：第一次跑 3 轮后 iter_index=3；第二次 max_iterations=iter+5=3+5=8，
    # 所以应再跑 5 轮（iter 4,5,6,7,8），最终 iter_index=8
    assert iter_after_second == 8, f"Resume 后 iter_index 应为 8（3+5 跑 4-8），实际 {iter_after_second}"
    print(f"  ✅ Resume 跑成功，iter_index=8")

    # 验证 notes.md 累计
    notes_content = notes_path.read_text(encoding="utf-8")
    section_count = notes_content.count("## Iteration")
    print(f"  notes.md 总段落数: {section_count}")
    assert section_count >= 3, f"Resume 后 notes.md 应至少 3 段，实际 {section_count}"
    print(f"  ✅ Resume 后 notes.md 累计 {section_count} 段")

    print("\n🎉 E2E 断点续跑测试通过！")
    return 0


if __name__ == "__main__":
    sys.exit(main())
PYEOF

# 设置环境变量并跑测试
export SCRIPTS_DIR
export TEST_REPO
echo -e "${YELLOW}▶ 跑 E2E 断点续跑测试 ...${NC}"
if "$PYTHON_BIN" /tmp/ralph_e2e_resume.py 2>&1; then
    echo ""
    echo -e "${GREEN}✅ E2E 断点续跑测试通过${NC}"
    exit 0
else
    echo ""
    echo -e "${RED}❌ E2E 断点续跑测试失败${NC}"
    exit 1
fi
