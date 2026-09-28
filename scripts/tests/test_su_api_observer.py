# -*- coding: utf-8 -*-
"""SU 能力单元测试：API 观测器完结闸门（REQ-SU-009 / 2026-09-28 e2e 场景[2]）。

覆盖 su.api_observer 模块（真临时 SQLite 落库 + duck-typing 事件对象，
不 mock 业务逻辑——被测对象 ApiObserver/StateStore 全部真实运行）：
- 完结闸门：requestfinished/requestfailed 未登记的响应**绝不取体**
  （sync ``response.body()`` 对未完结响应会无界挂起并占死 greenlet
  派发循环——e2e 场景[2]卡死根因；替身 response 的 body() 一旦被调用
  即置位标记并抛错，双重证明"闸门封死后 flush 全程不触体"）
- 取体失败诚实降级：已过完结判定但 body() 抛错 → body_fetch_errors
  计数、不落任何观测（禁 mock 红线：绝不写入编造的 shape）
- shape 归一三分类：JSON 键路径 / 文本采样 / 二进制只记元数据
- URL 观测形态：path 保留、query 值键名化置 KEY

运行方式（项目根目录）：
    python3 -B -m unittest scripts.tests.test_su_api_observer
"""

import sys
import tempfile
import unittest
from pathlib import Path

# 将 scripts/ 目录注入 sys.path，使 `from su.xxx import ...` 生效
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from su.api_observer import ApiObserver  # noqa: E402
from su.dto import PageNodeRedacted  # noqa: E402
from su.state_store import StateStore  # noqa: E402


class _FakeRequest:
    """Playwright Request 事件对象的离线替身（duck-typing 面）。

    只提供 api_observer 回调实际读取的属性面：method/url/resource_type/
    post_data。真实类可弱引用、可哈希，与 Playwright Request 行为一致
    （完结标记 WeakSet 的键语义依赖这一点）。
    """

    def __init__(self, url="https://a.test/api/x", method="GET",
                 resource_type="xhr", post_data=None):
        self.url = url
        self.method = method
        self.resource_type = resource_type
        self.post_data = post_data


class _RecordingResponse:
    """Response 事件对象的离线替身——body() 被调用即记录并抛错。

    与 sync Playwright 的挂起语义等价替身：未完结响应上 ``body()`` 会
    无限阻塞，测试里无法真的挂起，改为"调用即失败"——任何泄漏取体都会
    立即显形（标记位 + 异常），比 sleep 断言更严格。
    """

    def __init__(self, request, body=b"{}", status=200,
                 content_type="application/json"):
        self.request = request
        self.status = status
        self.headers = {"content-type": content_type}
        self._body = body
        # body() 调用次数（完结闸门断言锚：闸门关闭期间恒 0）
        self.body_calls = 0

    def body(self):
        """取体（替身语义：记录调用并抛错，证明调用方越过了闸门）。"""
        self.body_calls += 1
        raise AssertionError("未完结响应不得取体（flush 完结闸门失守）")


class _SettledResponse(_RecordingResponse):
    """已完结响应的替身：body() 返回真实字节（模拟体缓存仍在的常态）。"""

    def body(self):
        """取体成功形态（已过完结判定的合法调用）。"""
        self.body_calls += 1
        return self._body


class _BrokenBodyResponse(_RecordingResponse):
    """已过完结判定但体不可得的替身（响应被 GC / 缓存释放的失败形态）。"""

    def body(self):
        """取体抛错（flush 须计数 body_fetch_errors 并跳过，不造假）。"""
        self.body_calls += 1
        raise RuntimeError("Response has been disposed")


def _make_observer(tmpdir):
    """构造挂真实临时 SQLite 状态库的 ApiObserver（已持 run 锁）。"""
    store = StateStore(Path(tmpdir) / "s.sqlite", "sys-observer-test")
    store.acquire_lock(resume=False)
    return ApiObserver(store), store


class TestFlushCompletionGate(unittest.TestCase):
    """flush 完结闸门：未完结响应绝不取体（e2e 场景[2]卡死根因回归锚）。"""

    def test_pending_response_never_fetches_body(self):
        """requestfinished 未登记 → 跳过取体、计数 pending、零落库。"""
        with tempfile.TemporaryDirectory() as tmp:
            observer, store = _make_observer(tmp)
            store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/p", url="https://a.test/p", depth=0))
            observer.set_current_page(1)
            req = _FakeRequest(url="https://a.test/api/hang?token=abc123")
            resp = _RecordingResponse(req)
            observer._on_response(resp)
            self.assertEqual(observer.pending_count(), 1)
            # 不登记完结即 flush：闸门必须封死取体通道
            written = observer.flush()
            self.assertEqual(written, 0)
            self.assertEqual(resp.body_calls, 0)
            self.assertEqual(observer.stats["body_fetch_skipped_pending"], 1)
            # 缓冲清空（观测丢弃是设计语义：api_observations 恒有真实 shape）
            self.assertEqual(observer.pending_count(), 0)
            data = store.export_understanding()
            self.assertEqual(len(data["endpoints"]), 0)
            store.close()

    def test_finished_response_flushes_shape(self):
        """requestfinished 已登记 → 正常取体并聚合落库。"""
        with tempfile.TemporaryDirectory() as tmp:
            observer, store = _make_observer(tmp)
            store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/p", url="https://a.test/p", depth=0))
            observer.set_current_page(1)
            req = _FakeRequest(url="https://a.test/api/list?page=2")
            resp = _SettledResponse(req, body=b'{"items": [1, 2]}')
            observer._on_response(resp)
            observer._on_request_finished(req)  # 完结信号登记
            written = observer.flush()
            self.assertEqual(written, 1)
            self.assertEqual(resp.body_calls, 1)
            data = store.export_understanding()
            self.assertEqual(len(data["endpoints"]), 1)
            ep = data["endpoints"][0]
            # query 值键名化：page=2 → page=KEY（值不落盘，红线①）
            self.assertEqual(ep["url_path"], "/api/list?page=KEY")
            self.assertEqual(ep["method"], "GET")
            self.assertEqual(ep["latest_status"], 200)
            store.close()

    def test_requestfailed_counts_as_finished(self):
        """requestfailed 同样是完结信号（请求终止后响应体必已定型）。"""
        with tempfile.TemporaryDirectory() as tmp:
            observer, store = _make_observer(tmp)
            store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/p", url="https://a.test/p", depth=0))
            observer.set_current_page(1)
            req = _FakeRequest(url="https://a.test/api/aborted")
            resp = _SettledResponse(req, body=b"", status=204)
            observer._on_response(resp)
            # failed 与 finished 走同一回调（attach 时双事件同绑）
            observer._on_request_finished(req)
            written = observer.flush()
            self.assertEqual(written, 1)
            self.assertEqual(resp.body_calls, 1)
            store.close()

    def test_body_error_counts_and_skips(self):
        """已完结但取体抛错 → body_fetch_errors 计数、零落库（不造假）。"""
        with tempfile.TemporaryDirectory() as tmp:
            observer, store = _make_observer(tmp)
            store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/p", url="https://a.test/p", depth=0))
            observer.set_current_page(1)
            req = _FakeRequest(url="https://a.test/api/disposed")
            resp = _BrokenBodyResponse(req)
            observer._on_response(resp)
            observer._on_request_finished(req)
            written = observer.flush()
            self.assertEqual(written, 0)
            self.assertEqual(observer.stats["body_fetch_errors"], 1)
            self.assertEqual(observer.stats["body_fetch_skipped_pending"], 0)
            data = store.export_understanding()
            self.assertEqual(len(data["endpoints"]), 0)
            store.close()

    def test_unhashable_request_conservative_pending(self):
        """request 不可哈希（异常对象形态）→ 保守判未完结、零取体。

        _is_request_finished 的 TypeError 兜底分支：WeakSet 成员判定对
        不可哈希对象抛 TypeError，方向安全 = 不取体。
        """
        with tempfile.TemporaryDirectory() as tmp:
            observer, store = _make_observer(tmp)
            store.upsert_page(PageNodeRedacted(
                url_key="https://a.test/p", url="https://a.test/p", depth=0))
            observer.set_current_page(1)
            req = _FakeRequest()
            resp = _RecordingResponse(req)
            observer._on_response(resp)
            # 绕过回调直接把不可哈希哨兵塞进查表键路径：capture.request
            # 换成 list（不可哈希）复现异常形态
            observer._pending[0].request = ["unhashable"]
            written = observer.flush()
            self.assertEqual(written, 0)
            self.assertEqual(resp.body_calls, 0)
            self.assertEqual(observer.stats["body_fetch_skipped_pending"], 1)
            store.close()


class TestShapeSummarize(unittest.TestCase):
    """summarize_shape 三分类归一（纯函数面，落库形态红线①）。"""

    def test_json_shape_redacted(self):
        """JSON 体 → kind=json + 键路径 shape（RedactedDict 形态）。"""
        with tempfile.TemporaryDirectory() as tmp:
            observer, store = _make_observer(tmp)
            shape = observer.summarize_shape(
                b'{"user": {"name": "n1"}, "count": 3}',
                "application/json")
            self.assertEqual(shape["kind"], "json")
            self.assertEqual(shape["content_type"], "application/json")
            self.assertEqual(shape["size"], len(b'{"user": {"name": "n1"}, "count": 3}'))
            self.assertEqual(shape["shape"]["type"], "object")
            self.assertIn("user", shape["shape"]["keys"])
            store.close()

    def test_binary_body_no_content(self):
        """二进制体 → 只记 content-type + size（AC3 不落正文）。"""
        with tempfile.TemporaryDirectory() as tmp:
            observer, store = _make_observer(tmp)
            shape = observer.summarize_shape(b"\x00\x01\x02\xff",
                                             "application/octet-stream")
            self.assertEqual(shape["kind"], "binary")
            self.assertEqual(shape["size"], 4)
            self.assertNotIn("text_sample", shape)
            store.close()

    def test_empty_body_skeleton(self):
        """空体 → 仅三要素骨架（204/HEAD 语义）。"""
        with tempfile.TemporaryDirectory() as tmp:
            observer, store = _make_observer(tmp)
            shape = observer.summarize_shape(b"", "")
            self.assertEqual(shape["size"], 0)
            self.assertNotIn("kind", shape)
            store.close()


if __name__ == "__main__":
    unittest.main()
