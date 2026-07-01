"""UnifiedMemoryLayer 单元测试。"""

import shutil
import tempfile
import unittest
from pathlib import Path

from autonomous.notes_memory import NotesMemory
from autonomous.run_state import RunState
from feedback_control_loop import FeedbackControlLoop
from loop_engineering.models import LoopEvent, LoopEventType, MemoryQuery
from loop_engineering.unified_memory import UnifiedMemoryLayer
from performance_fingerprint import PerformanceFingerprint


class TestUnifiedMemoryLayer(unittest.TestCase):
    """测试 UnifiedMemoryLayer。"""

    def setUp(self):
        """每个测试用例前创建临时目录和组件。"""
        self.tmpdir = Path(tempfile.mkdtemp())
        self.run_id = "test-run-001"
        self.notes = NotesMemory(notes_path=self.tmpdir / "notes.md")
        self.run_state = RunState(
            run_dir=self.tmpdir / "runs" / self.run_id,
            run_id=self.run_id,
            objective="测试目标",
        )
        self.fingerprint = PerformanceFingerprint(
            agent_id="loop-engineering-test",
            storage_path=str(self.tmpdir / "fingerprints"),
        )
        self.feedback_loop = FeedbackControlLoop(
            agent_id="loop-engineering-test",
            storage_path=str(self.tmpdir / "feedback"),
        )
        self.memory = UnifiedMemoryLayer(
            notes_memory=self.notes,
            run_state=self.run_state,
            fingerprint=self.fingerprint,
            feedback_loop=self.feedback_loop,
            run_id=self.run_id,
        )

    def tearDown(self):
        """清理临时目录。"""
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_persist_event_writes_notes(self):
        """persist_event 写入 NotesMemory。"""
        event = LoopEvent(
            event_id="e1",
            event_type=LoopEventType.DISCOVERY_STARTED,
            phase="discovery",
            run_id=self.run_id,
            iter_index=0,
            payload={"objective": "测试"},
        )
        self.memory.persist_event(event)

        content = self.notes.load()
        self.assertIn("discovery_started", content)
        self.assertIn("测试", content)

    def test_persist_event_writes_run_state(self):
        """persist_event 写入 RunState。"""
        event = LoopEvent(
            event_id="e2",
            event_type=LoopEventType.VERIFICATION_PASSED,
            phase="verification",
            run_id=self.run_id,
            iter_index=0,
            payload={},
        )
        self.memory.persist_event(event)

        self.assertTrue(self.run_state.state_path.exists())
        self.assertEqual(self.run_state.state.iter_index, 0)
        self.assertEqual(self.run_state.state.status, "running")

    def test_query_recent(self):
        """查询最近事件。"""
        for i in range(3):
            event = LoopEvent(
                event_id=f"e{i}",
                event_type=LoopEventType.DISCOVERY_STARTED,
                phase="discovery",
                run_id=self.run_id,
                iter_index=i,
                payload={},
            )
            self.memory.persist_event(event)

        results = self.memory.query(MemoryQuery(query_type="recent", limit=2))
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["source"], "notes_memory")

    def test_estimate_token_usage(self):
        """估算 token 使用量增加。"""
        before = self.memory.estimate_token_usage()
        event = LoopEvent(
            event_id="e1",
            event_type=LoopEventType.DISCOVERY_STARTED,
            phase="discovery",
            run_id=self.run_id,
            iter_index=0,
            payload={"objective": "测试"},
        )
        self.memory.persist_event(event)
        after = self.memory.estimate_token_usage()
        self.assertGreater(after, before)

    def test_get_recent_notes(self):
        """获取最近 notes 摘要。"""
        event = LoopEvent(
            event_id="e1",
            event_type=LoopEventType.DISCOVERY_STARTED,
            phase="discovery",
            run_id=self.run_id,
            iter_index=0,
            payload={"objective": "测试"},
        )
        self.memory.persist_event(event)

        summary = self.memory.get_recent_notes(1)
        self.assertIn("discovery_started", summary)


if __name__ == "__main__":
    unittest.main()
