"""页面就绪等待（有界算法）——架构 ARCH-SU-001 §2.3.6 ``wait_ready``
（2026-09-28 审查修订版），PRD REQ-SU-005 / REQ-SU-008.3。

**为什么要有界**：架构审查明确否掉了"网络静默 + DOM 稳定双等待"的**开放描述**
写法——遗留系统的长轮询/SSE 会让"网络静默"永不成立，open-ended 等待会把整个
BFS 卡死。本模块实现的是**有界**版本：最多 ``rounds`` 轮、总时长硬上限
``hard_timeout_ms``，超限即返回 ``stable=False, reason='wait_ready_timeout'``，
crawler 记 ``pages.error='wait_ready_timeout'`` 后**继续** BFS，绝不阻塞。

**拆分说明（相对架构文件清单的合理微调）**：架构把 wait_ready 列为 SiteCrawler
的方法。本模块把其中的**判定内核纯函数化**，crawler 保留 Playwright 薄壳
（``page.wait_for_load_state`` / ``page.evaluate``），理由：
① 判定逻辑（DOM 摘要比对 + 静默判定 + 轮次/超时收口）与 Playwright 完全解耦，
   可用注入的 ``evaluator`` 替身做确定性单测，无需起浏览器；
② ``NetworkQuietTracker`` 由 request/response 事件回调喂时间戳（回调只写一个
   float，不碰 Playwright API，符合 §2.3.6 handler 执行模型约束），独立类便于
   单测直接调 :meth:`NetworkQuietTracker.mark_request` 而不必 mock 事件总线。

crawler 侧的接线方式（后续交付）::

    tracker = NetworkQuietTracker()
    page.on("request", lambda _r: tracker.mark_request())   # 回调只写 float
    page.on("response", lambda _r: tracker.mark_request())
    outcome = wait_ready_bounded(
        tracker=tracker,
        evaluator=lambda: compute_dom_digest_from(page.evaluate(JS_SNIPPET)),
    )

判定内核的**时钟与睡眠均可注入**（``clock`` / ``sleep`` 参数），单测可用假时钟
零等待跑完全部轮次与超时分支，断言值完全确定。
"""

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

__all__ = [
    "WAIT_READY_TIMEOUT_REASON",
    "DomSnapshot",
    "WaitReadyOutcome",
    "compute_dom_digest",
    "NetworkQuietTracker",
    "wait_ready_bounded",
]

# 超时原因常量（写入 pages.error，与 §3 DDL / 报告口径逐字对齐）
WAIT_READY_TIMEOUT_REASON = "wait_ready_timeout"

# evaluator 返回类型别名：(DOM 文本摘要 hash, 可交互元素数)
DomSnapshot = Tuple[str, int]


@dataclass
class WaitReadyOutcome:
    """就绪等待结果。

    Attributes:
        stable: True=相邻两轮 (hash, 元素数) 相同，判定 DOM 已稳定；
            False=轮次用尽或硬超时（调用方记 error 后继续，不阻塞）。
        reason: 稳定时为 ``'stable'``；否则为 :data:`WAIT_READY_TIMEOUT_REASON`。
        rounds_used: 实际消耗的轮次数（观测值，供报告解释等待成本）。
    """

    stable: bool
    reason: str
    rounds_used: int


def compute_dom_digest(text_content: Optional[str], interactive_count: int) -> str:
    """计算 DOM 稳定指纹（纯函数）——main/body 文本摘要 + 可交互元素数。

    指纹由 ``sha1(文本长度 + '\\x00' + 文本 + '\\x00' + 可交互元素数)`` 得到。
    把 ``interactive_count`` 编进指纹（而非只 hash 文本）是必要的：SPA 常见
    "文本不变但按钮/表单陆续渲染出来"的情形，只 hash 文本会误判为已稳定。

    Args:
        text_content: 页面 main/body 的 textContent（None 视为空串）。
        interactive_count: 可交互元素数量（a/button/input/select/textarea）。

    Returns:
        str: 40 位十六进制 sha1 摘要。

    Note:
        **指纹只含文本与计数，绝不含任何 input value**（§5.1 静态审查项：
        wait_ready/extract_signature 一律不读取表单控件 value）。
    """
    text = text_content or ""
    payload = "{0}\x00{1}\x00{2}".format(len(text), text, int(interactive_count))
    return hashlib.sha1(payload.encode("utf-8", errors="replace")).hexdigest()


def compute_dom_digest_from(text_content: Optional[str], interactive_count: int) -> DomSnapshot:
    """把 :func:`compute_dom_digest` 组装成 evaluator 需要的 ``(hash, count)`` 元组。

    存在意义：crawler 注入的 evaluator 一行即可写完
    （``lambda: compute_dom_digest_from(page.evaluate(JS), count)``），
    避免薄壳里重复拼装元组。

    Args:
        text_content: 页面文本内容。
        interactive_count: 可交互元素数。

    Returns:
        tuple[str, int]: ``(sha1 摘要, 可交互元素数)``。
    """
    return (compute_dom_digest(text_content, interactive_count), int(interactive_count))


class NetworkQuietTracker:
    """网络静默跟踪器（§2.3.6 实现口径的具体化）。

    实现方式（架构写明）：由 request/response 事件回调调用 :meth:`mark_request`
    更新 ``last_request_ts``（回调体内**只写一个 float**，不碰任何 Playwright API），
    主线程用 :meth:`quiet` 轮询该时间戳，距今 ≥ quiet_ms 即视为网络静默。

    线程安全：回调线程写、主线程读，用锁保护单个 float 的读写
    （CPython 下 float 赋值本身原子，加锁是为了跨实现可移植 + 语义自证）。
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """初始化跟踪器。

        Args:
            clock: 单调时钟函数，默认 :func:`time.monotonic`。
                可注入以便单测用假时钟精确控制"静默是否达成"（无 sleep 抖动）。
        """
        self._clock = clock
        self._lock = threading.Lock()
        # 构造即视为"刚有活动"：避免页面一进来就误判已静默而提前取样
        self._last_request_ts = clock()

    def mark_request(self) -> None:
        """标记"刚刚发生一次网络请求/响应"（事件回调唯一允许的动作）。"""
        with self._lock:
            self._last_request_ts = self._clock()

    def quiet(self, quiet_ms: int) -> bool:
        """判定是否已静默 ``quiet_ms`` 毫秒。

        Args:
            quiet_ms: 静默阈值（毫秒）。≤0 视为"立即静默"。

        Returns:
            bool: True=距上次请求已 ≥ quiet_ms（无进行中的网络活动）。
        """
        if quiet_ms <= 0:
            return True
        with self._lock:
            last = self._last_request_ts
        elapsed_ms = (self._clock() - last) * 1000.0
        return elapsed_ms >= float(quiet_ms)

    def idle_ms(self) -> float:
        """距上次请求已流逝的毫秒数（观测/调试用）。"""
        with self._lock:
            last = self._last_request_ts
        return (self._clock() - last) * 1000.0


def wait_ready_bounded(
    evaluator: Callable[[], DomSnapshot],
    tracker: NetworkQuietTracker,
    delay_poll_ms: int = 100,
    rounds: int = 3,
    quiet_ms: int = 500,
    hard_timeout_ms: int = 3000,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> WaitReadyOutcome:
    """有界就绪等待（取代开放式的"双等待"描述）。

    算法（§2.3.6，每轮三步）：
      ① 等网络静默 ``quiet_ms``（轮询 ``delay_poll_ms`` 步进，受硬超时约束）；
      ② 调 ``evaluator()`` 取 ``(text_digest, interactive_count)``；
      ③ 与上一轮指纹比对：**相邻两轮相同 → 判定稳定并返回**。
    收口：``rounds`` 轮用尽或墙钟超 ``hard_timeout_ms`` →
    ``stable=False`` / ``reason='wait_ready_timeout'``，调用方记 error 后继续。

    Args:
        evaluator: 注入的取样函数，返回 ``(DOM 文本摘要 hash, 可交互元素数)``。
            生产环境由 crawler 包 ``page.evaluate``；单测注入替身即可确定性驱动
            各分支（本参数存在就是为了让等待逻辑脱离浏览器可测）。
        tracker: 网络静默跟踪器。
        delay_poll_ms: 轮询步进（毫秒）。
        rounds: 最大轮次（默认 3；相邻两轮相同即提前返回）。
        quiet_ms: 静默判定阈值（毫秒，默认 500）。
        hard_timeout_ms: 整段等待的墙钟硬上限（毫秒，默认 3000）。
        sleep: 睡眠函数（可注入假 sleep 供单测零等待跑完全部轮次）。
        clock: 单调时钟函数（默认 :func:`time.monotonic`），与 ``sleep`` 配套注入
            即可让整段等待在假时间轴上确定性地跑完（无真实等待、断言值稳定）。

    Returns:
        WaitReadyOutcome: ``stable`` + ``reason`` + 实际轮次。

    Note:
        本函数**不调用任何 Playwright API**（evaluate 由注入的 evaluator 承担），
        因此单测可完全离线运行；同时它保证有限步内返回，不存在死循环路径。
    """
    started = clock()
    previous: Optional[DomSnapshot] = None
    rounds_used = 0

    def elapsed_ms() -> float:
        """自本次等待开始的墙钟毫秒数。"""
        return (clock() - started) * 1000.0

    for round_index in range(max(1, int(rounds))):
        # ---- 硬超时优先判定：超时即收口，不再取样（防 evaluator 自身很慢时超预算）----
        if elapsed_ms() >= hard_timeout_ms:
            return WaitReadyOutcome(
                stable=False,
                reason=WAIT_READY_TIMEOUT_REASON,
                rounds_used=rounds_used,
            )

        # ---- ① 等网络静默（同样受硬超时约束；静默等不到就走超时收口）----
        while not tracker.quiet(quiet_ms):
            if elapsed_ms() >= hard_timeout_ms:
                return WaitReadyOutcome(
                    stable=False,
                    reason=WAIT_READY_TIMEOUT_REASON,
                    rounds_used=rounds_used,
                )
            sleep(delay_poll_ms / 1000.0)

        # ---- ② 取样 ----
        snapshot = evaluator()
        rounds_used = round_index + 1

        # ---- ③ 与上一轮比对：相邻两轮指纹相同 → 稳定 ----
        if previous is not None and previous == snapshot:
            return WaitReadyOutcome(stable=True, reason="stable", rounds_used=rounds_used)
        previous = snapshot

    # 轮次用尽仍未出现"相邻两轮相同" → 超时（crawler 记 wait_ready_timeout 后继续 BFS）
    return WaitReadyOutcome(
        stable=False,
        reason=WAIT_READY_TIMEOUT_REASON,
        rounds_used=rounds_used,
    )
