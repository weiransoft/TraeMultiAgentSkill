"""Loop Engineering 集成测试。

测试目标：
使用真实 LoopKernel + 真实 DiscoveryProbe / IndependentEvaluator / LoopScheduler /
UnifiedMemoryLayer，仅对 HandoffAdapter 注入确定性 executor（避免调用外部 LLM），
验证完整五步闭环能够正常完成，并产生预期的事件序列。

本测试不 mock LoopKernel 内部组件，所有 Memory 层组件均使用真实实现。
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from autonomous.notes_memory import NotesMemory
from autonomous.run_state import RunState
from feedback_control_loop import FeedbackControlLoop
from loop_engineering.config_loader import build_loop_config
from loop_engineering.discovery_probe import DiscoveryProbe
from loop_engineering.handoff_adapter import HandoffAdapter
from loop_engineering.independent_evaluator import IndependentEvaluator
from loop_engineering.kernel import LoopKernel
from loop_engineering.loop_scheduler import LoopScheduler
from loop_engineering.models import (
    DiscoveryMode,
    EvaluatorMode,
    LoopEngineeringConfig,
    LoopEventType,
    LoopType,
)
from loop_engineering.unified_memory import UnifiedMemoryLayer
from performance_fingerprint import PerformanceFingerprint


# ---------------------------------------------------------------------- #
# 工具函数                                                               #
# ---------------------------------------------------------------------- #


def _make_config(tmpdir: Path, **overrides) -> LoopEngineeringConfig:
    """构造用于集成测试的 LoopEngineeringConfig。

    默认配置：
    - loop_type=CODING
    - discovery_mode=OFF（避免扫描真实项目）
    - evaluator_mode=STRICT
    - max_iterations=1（单轮即停止）
    - human_checkpoint_every=0（关闭人类检查点）
    - test_command 为必定成功的命令
    - auto_commit=False（避免真实 git commit）
    """
    defaults = {
        "loop_type": LoopType.CODING,
        "discovery_mode": DiscoveryMode.OFF,
        "evaluator_mode": EvaluatorMode.STRICT,
        "max_iterations": 1,
        "max_tokens": 100_000,
        "human_checkpoint_every": 0,
        "sampling_read_ratio": 0.0,
        "project_root": tmpdir,
        "test_command": "python3 -c \"print('integration-test-pass')\"",
        "test_timeout_sec": 10.0,
        "auto_commit": False,
        "security_analyzer": "builtin",
    }
    defaults.update(overrides)
    return LoopEngineeringConfig(**defaults)


def _make_memory(tmpdir: Path, run_id: str) -> UnifiedMemoryLayer:
    """构造真实的 UnifiedMemoryLayer 组件。"""
    run_dir = tmpdir / ".gnhf" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    notes_memory = NotesMemory(notes_path=run_dir / "notes.md")
    run_state = RunState(run_dir=run_dir, run_id=run_id, objective="集成测试")
    fingerprint = PerformanceFingerprint(
        agent_id=run_id,
        storage_path=str(run_dir / "fingerprint"),
    )
    feedback_loop = FeedbackControlLoop(
        agent_id=run_id,
        storage_path=str(run_dir / "feedback"),
    )

    return UnifiedMemoryLayer(
        notes_memory=notes_memory,
        run_state=run_state,
        fingerprint=fingerprint,
        feedback_loop=feedback_loop,
        run_id=run_id,
    )


def _make_kernel(
    config: LoopEngineeringConfig,
    memory: UnifiedMemoryLayer,
    executor=None,
) -> LoopKernel:
    """构造真实 LoopKernel，HandoffAdapter 使用自定义 executor。"""
    log = lambda msg, level: None  # 测试时静默日志

    discovery_probe = DiscoveryProbe(config=config, log=log)
    handoff_adapter = HandoffAdapter(
        config=config,
        executor=executor,
        log=log,
    )
    evaluator = IndependentEvaluator(config=config, log=log)
    scheduler = LoopScheduler(config=config, log=log)

    return LoopKernel(
        config=config,
        discovery_probe=discovery_probe,
        handoff_adapter=handoff_adapter,
        evaluator=evaluator,
        memory=memory,
        scheduler=scheduler,
        log=log,
    )


# ---------------------------------------------------------------------- #
# TestLoopEngineeringIntegration: 集成测试                                #
# ---------------------------------------------------------------------- #


class TestLoopEngineeringIntegration(unittest.TestCase):
    """Loop Engineering 端到端集成测试（使用真实组件）。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _success_executor(self, item):
        """确定性 executor：模拟 generator 成功。"""
        return {
            "success": True,
            "output": f"executed {item.agent_type}",
            "summary": "ok",
            "skills_used": ["loop-engineering"],
            "error": "",
        }

    def test_01_full_cycle_completes(self):
        """完整五步闭环在 max_iterations=1 时完成。"""
        config = _make_config(self.tmpdir)
        memory = _make_memory(self.tmpdir, run_id="le-int-001")
        kernel = _make_kernel(config, memory, executor=self._success_executor)

        report = kernel.run(objective="实现一个 hello 函数")

        self.assertEqual(report.final_status, "completed")
        self.assertEqual(report.loop_type, LoopType.CODING)
        self.assertGreaterEqual(report.total_iterations, 1)

    def test_02_event_sequence(self):
        """验证事件序列包含五步闭环关键事件。"""
        config = _make_config(self.tmpdir)
        memory = _make_memory(self.tmpdir, run_id="le-int-002")
        kernel = _make_kernel(config, memory, executor=self._success_executor)

        report = kernel.run(objective="实现一个 hello 函数")

        event_types = [e.event_type for e in report.events]
        required_types = [
            LoopEventType.DISCOVERY_STARTED,
            LoopEventType.DISCOVERY_COMPLETED,
            LoopEventType.HANDOFF_CREATED,
            LoopEventType.HANDOFF_DISPATCHED,
            LoopEventType.VERIFICATION_STARTED,
            LoopEventType.VERIFICATION_PASSED,
            LoopEventType.PERSISTENCE_WRITTEN,
            LoopEventType.SCHEDULING_DECISION,
            LoopEventType.LOOP_COMPLETED,
        ]
        for required in required_types:
            self.assertIn(
                required,
                event_types,
                f"事件序列缺少 {required.value}",
            )

    def test_03_generator_result_has_objective_metrics(self):
        """generator_result 包含客观指标字段。"""
        config = _make_config(self.tmpdir)
        memory = _make_memory(self.tmpdir, run_id="le-int-003")
        kernel = _make_kernel(config, memory, executor=self._success_executor)

        report = kernel.run(objective="实现一个 hello 函数")

        # 找到 HANDOFF_DISPATCHED 事件，检查 payload
        dispatched = None
        for event in report.events:
            if event.event_type == LoopEventType.HANDOFF_DISPATCHED:
                dispatched = event
                break
        self.assertIsNotNone(dispatched)
        self.assertIn("success", dispatched.payload)
        self.assertTrue(dispatched.payload["success"])

    def test_04_run_state_persisted(self):
        """RunState 被持久化到磁盘。"""
        config = _make_config(self.tmpdir)
        memory = _make_memory(self.tmpdir, run_id="le-int-004")
        kernel = _make_kernel(config, memory, executor=self._success_executor)

        kernel.run(objective="实现一个 hello 函数")

        run_state_path = self.tmpdir / ".gnhf" / "runs" / "le-int-004" / "state.json"
        self.assertTrue(run_state_path.exists())
        content = run_state_path.read_text(encoding="utf-8")
        self.assertIn("completed", content)

    def test_05_notes_memory_persisted(self):
        """notes.md 被持久化到磁盘。"""
        config = _make_config(self.tmpdir)
        memory = _make_memory(self.tmpdir, run_id="le-int-005")
        kernel = _make_kernel(config, memory, executor=self._success_executor)

        kernel.run(objective="实现一个 hello 函数")

        notes_path = self.tmpdir / ".gnhf" / "runs" / "le-int-005" / "notes.md"
        self.assertTrue(notes_path.exists())
        content = notes_path.read_text(encoding="utf-8")
        self.assertIn("loop_completed", content)

    def test_06_evaluator_strict_rejects_missing_metrics(self):
        """STRICT 模式下缺少客观指标会被拒绝。"""
        config = _make_config(self.tmpdir)
        memory = _make_memory(self.tmpdir, run_id="le-int-006")

        # executor 返回成功，但 config.test_command 设置为必定失败的命令，
        # 使得 HandoffAdapter 生成的 test_result.passed=False，
        # 在 STRICT 模式下被 IndependentEvaluator 拒绝。
        config.test_command = "python3 -c \"exit(1)\""
        kernel = _make_kernel(
            config,
            memory,
            executor=lambda item: {
                "success": True,
                "output": "ok",
                "summary": "ok",
                "skills_used": [],
                "error": "",
            },
        )

        report = kernel.run(objective="实现一个 hello 函数")

        # 由于缺少测试指标，验证应被拒绝，最终状态为 failed
        self.assertEqual(report.final_status, "failed")
        event_types = [e.event_type for e in report.events]
        self.assertIn(LoopEventType.VERIFICATION_REJECTED, event_types)


if __name__ == "__main__":
    unittest.main()
