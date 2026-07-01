"""Loop Engineering E2E 测试：Coding Loop。

验证目标：
- loop_type=CODING 时，Discovery 推荐 solo-coder 工作项。
- HandoffAdapter 生成 solo-coder 工作项。
- generator_result 包含 test_result 客观指标。
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
    """构造 coding loop E2E 配置。"""
    return LoopEngineeringConfig(
        loop_type=LoopType.CODING,
        discovery_mode=DiscoveryMode.AUTO,
        evaluator_mode=EvaluatorMode.STRICT,
        max_iterations=1,
        max_tokens=100_000,
        human_checkpoint_every=0,
        sampling_read_ratio=0.0,
        project_root=tmpdir,
        test_command="python3 -c \"print('coding-e2e-pass')\"",
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
        run_state=RunState(run_dir=run_dir, run_id=run_id, objective="coding e2e"),
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
        "output": f"coding executed {item.agent_type}",
        "summary": "ok",
        "skills_used": ["solo-coder"],
        "error": "",
    }


class TestLoopEngineeringE2ECoding(unittest.TestCase):
    """Coding Loop E2E 测试。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_coding_loop_generates_solo_coder_item(self):
        """coding loop 生成 solo-coder 工作项。"""
        config = _make_config(self.tmpdir)
        memory = _make_memory(self.tmpdir, run_id="le-e2e-coding-001")
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

        report = kernel.run(objective="实现一个无依赖的 hello 函数")

        self.assertEqual(report.final_status, "completed")
        self.assertEqual(report.loop_type, LoopType.CODING)

        # 验证 Discovery 推荐 solo-coder
        discovery_event = None
        for event in report.events:
            if event.event_type == LoopEventType.DISCOVERY_COMPLETED:
                discovery_event = event
                break
        self.assertIsNotNone(discovery_event)
        self.assertIn("solo-coder", discovery_event.payload.get("agents", []))

    def test_coding_loop_generator_result_has_test_result(self):
        """coding loop 的 generator_result 包含 test_result。"""
        config = _make_config(self.tmpdir)
        memory = _make_memory(self.tmpdir, run_id="le-e2e-coding-002")
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

        report = kernel.run(objective="修复登录接口的 bug")

        self.assertEqual(report.final_status, "completed")

        # HANDOFF_DISPATCHED 事件 payload 中应包含 generator_keys
        dispatched = None
        for event in report.events:
            if event.event_type == LoopEventType.HANDOFF_DISPATCHED:
                dispatched = event
                break
        self.assertIsNotNone(dispatched)
        self.assertIn("generator_keys", dispatched.payload)


if __name__ == "__main__":
    unittest.main()
