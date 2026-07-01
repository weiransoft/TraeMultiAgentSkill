"""Loop Engineering E2E 测试：Testing Loop。

验证目标：
- loop_type=TESTING 时，Discovery 推荐 test-expert 工作项。
- HandoffAdapter 生成 test-expert 工作项。
- 完整 Loop 运行完成，最终状态为 completed。
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from autonomous.notes_memory import NotesMemory
from autonomous.run_state import RunState
from feedback_control_loop import FeedbackControlLoop
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


def _make_config(tmpdir: Path) -> LoopEngineeringConfig:
    """构造 testing loop E2E 配置。"""
    return LoopEngineeringConfig(
        loop_type=LoopType.TESTING,
        discovery_mode=DiscoveryMode.AUTO,
        evaluator_mode=EvaluatorMode.STANDARD,
        max_iterations=1,
        max_tokens=100_000,
        human_checkpoint_every=0,
        sampling_read_ratio=0.0,
        project_root=tmpdir,
        test_command="python3 -c \"print('testing-e2e-pass')\"",
        test_timeout_sec=10.0,
        auto_commit=False,
        security_analyzer="builtin",
    )


def _make_memory(tmpdir: Path, run_id: str) -> UnifiedMemoryLayer:
    """构造真实的 UnifiedMemoryLayer。"""
    run_dir = tmpdir / ".gnhf" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    return UnifiedMemoryLayer(
        notes_memory=NotesMemory(notes_path=run_dir / "notes.md"),
        run_state=RunState(run_dir=run_dir, run_id=run_id, objective="testing e2e"),
        fingerprint=PerformanceFingerprint(
            agent_id=run_id,
            storage_path=str(run_dir / "fingerprint"),
        ),
        feedback_loop=FeedbackControlLoop(
            agent_id=run_id,
            storage_path=str(run_dir / "feedback"),
        ),
        run_id=run_id,
    )


def _success_executor(item):
    """确定性 executor：返回成功。"""
    return {
        "success": True,
        "output": f"testing executed {item.agent_type}",
        "summary": "ok",
        "skills_used": ["testing"],
        "error": "",
    }


class TestLoopEngineeringE2ETesting(unittest.TestCase):
    """Testing Loop E2E 测试。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_testing_loop_generates_test_expert_item(self):
        """testing loop 生成 test-expert 工作项。"""
        config = _make_config(self.tmpdir)
        memory = _make_memory(self.tmpdir, run_id="le-e2e-testing-001")
        log = lambda msg, level: None

        discovery_probe = DiscoveryProbe(config=config, log=log)
        handoff_adapter = HandoffAdapter(
            config=config,
            executor=_success_executor,
            log=log,
        )
        evaluator = IndependentEvaluator(config=config, log=log)
        scheduler = LoopScheduler(config=config, log=log)

        kernel = LoopKernel(
            config=config,
            discovery_probe=discovery_probe,
            handoff_adapter=handoff_adapter,
            evaluator=evaluator,
            memory=memory,
            scheduler=scheduler,
            log=log,
        )

        report = kernel.run(objective="补充认证模块的单元测试")

        self.assertEqual(report.final_status, "completed")
        self.assertEqual(report.loop_type, LoopType.TESTING)

        # 验证 Discovery 推荐 test-expert
        discovery_event = None
        for event in report.events:
            if event.event_type == LoopEventType.DISCOVERY_COMPLETED:
                discovery_event = event
                break
        self.assertIsNotNone(discovery_event)
        self.assertIn("test-expert", discovery_event.payload.get("agents", []))

    def test_testing_loop_completes_with_coverage_objective(self):
        """testing loop 在提升覆盖率目标下完成。"""
        config = _make_config(self.tmpdir)
        memory = _make_memory(self.tmpdir, run_id="le-e2e-testing-002")
        log = lambda msg, level: None

        discovery_probe = DiscoveryProbe(config=config, log=log)
        handoff_adapter = HandoffAdapter(
            config=config,
            executor=_success_executor,
            log=log,
        )
        evaluator = IndependentEvaluator(config=config, log=log)
        scheduler = LoopScheduler(config=config, log=log)

        kernel = LoopKernel(
            config=config,
            discovery_probe=discovery_probe,
            handoff_adapter=handoff_adapter,
            evaluator=evaluator,
            memory=memory,
            scheduler=scheduler,
            log=log,
        )

        report = kernel.run(objective="提升登录模块的测试覆盖率")

        self.assertEqual(report.final_status, "completed")

        # 验证事件序列完整
        event_types = [e.event_type for e in report.events]
        self.assertIn(LoopEventType.HANDOFF_DISPATCHED, event_types)
        self.assertIn(LoopEventType.VERIFICATION_PASSED, event_types)
        self.assertIn(LoopEventType.LOOP_COMPLETED, event_types)


if __name__ == "__main__":
    unittest.main()
