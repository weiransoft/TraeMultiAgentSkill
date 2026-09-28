"""站点 BFS 遍历器（架构 ARCH-SU-001 §2.3.6 / §4.2 时序，PRD REQ-SU-005~008）。

**职责**：UI 透镜的全部浏览器侧逻辑——单 Page 串行 BFS（AP-6）、url_key 去重、
route 拦截（红线④）、有界就绪等待、DOM 剪枝提签名（永不读 input value）、
动作分级执行（红线⑤：T3 零点击）、语义骨架快照、预算控制、页边界 drain 落库。

**复用已交付组件（不重复实现）**：
  - :func:`su.url_key.url_key` —— 去重键唯一口径；
  - :func:`su.action_tier.classify_action` / :func:`su.action_tier.fill_neutral` /
    :class:`su.action_tier.ElementSignature` —— 分级判定内核与 T2 中性值；
  - :mod:`su.wait_ready` —— 有界就绪等待（NetworkQuietTracker + wait_ready_bounded）；
  - :mod:`su.route_policy` —— route 决策纯函数（decide）与有界拦截队列
    （BlockedEventQueue）；
  - :class:`su.limiter.RateLimiter` / :class:`su.limiter.BudgetTracker`；
  - :class:`su.api_observer.ApiObserver` —— 放行响应的 shape 观测。

**红线④ handler 执行模型（2026-09-28 审查硬性约束，本文件的结构保证）**：
``install_route_guard`` 注册的 handler 体内**只有三类操作**：
① 调纯函数 :func:`su.route_policy.decide` 得决策 + 内存计数；
② :class:`su.route_policy.BlockedEventQueue`.put（有界、非阻塞、内部已净化）；
③ ``route.abort()`` / ``route.continue_()``。
零 Playwright sync API 调用（不 evaluate/不现查 URL/不 sleep）；
落库统一发生在 **BFS 页边界 drain**（:meth:`_drain_blocked`，与 heartbeat 同点）；
``triggered_from_page`` 由主线程维护的 ``current_page_id`` 注入。

**SPA hash 导航分支（§4.2 Note）**：hash-only 链接的点击是纯客户端行为——
零网络请求、不经 route、不产生 blocked_events、api_observations 不新增记录
**属预期**（不是漏采）；就绪判定只用 DOM 摘要 hash（wait_ready 同一算法）。

**不顶层 import playwright**：page/context 对象由编排层注入；类型注解只在
``typing.TYPE_CHECKING`` 下引用（REQ-SU-021）。
"""

import json
import logging
import os
import queue
import signal
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit, urlunsplit

from su.action_tier import (
    TIER_T1,
    TIER_T2,
    TIER_T3,  # 2026-09-28 e2e 修复：766 行 T3 判定缺 import 致 NameError
    ActionDecision,
    ElementSignature,
    classify_action,
    fill_neutral,
)
from su.api_observer import ApiObserver
from su.config import SuConfig, redact, scrub_text
from su.dto import (
    ActionDecisionRedacted,
    EdgeRedacted,
    PageNodeRedacted,
    RedactedDict,
)
from su.limiter import BudgetTracker, RateLimiter
from su.route_policy import BlockedEventQueue, decide, normalize_origin
from su.state_store import StateStore
from su.url_key import url_key
from su.wait_ready import (
    NetworkQuietTracker,
    WAIT_READY_TIMEOUT_REASON,
    WaitReadyOutcome,
    compute_dom_digest_from,
    wait_ready_bounded,
)

if TYPE_CHECKING:  # 仅类型注解引用，运行时不导入 playwright（软依赖红线）
    from playwright.sync_api import Browser, BrowserContext, Page

    from su.browser_login import BrowserLogin  # 重登协作者（类型注解专用）

__all__ = ["CrawlReport", "SiteCrawler", "redirected_to_login"]

# 模块日志器（调试诊断：页循环阶段打点经 --debug-log 的 DEBUG 文件通道落盘）
logger = logging.getLogger("su.crawler")

# 快照体积上限（NFR-SU-005：单快照 ≤64KB）
SNAPSHOT_MAX_BYTES = 64 * 1024

# 就绪等待参数（§2.3.6 有界算法口径：3 轮 / 静默 500ms / 硬超时 3s）
WAIT_READY_ROUNDS = 3
WAIT_READY_QUIET_MS = 500
WAIT_READY_HARD_TIMEOUT_MS = 3000

# 可交互元素提取上限（遗留大页防御：剪枝上限，超出部分不进分级，计数可见）
MAX_SIGNATURE_ELEMENTS = 300

# T2 表单执行上限（每页）：分类为 T2 的显式 GET 表单在同一页上**全部真实
# 提交**（数量封顶防意外表单风暴）。该预算**独立于** max_actions_per_page
# ——后者是"每页候选动作"总量预算，由导航链接（T1）优先消费；2026-09-28
# e2e 场景[2]实测教训：搜索/查询表单通常排在十余条导航链接之后，
# 与 T1 共用预算时 T2 分支 consume_action 必然先行耗尽，显式 GET 表单
# 永不真实执行（缺 T2 已执行动作断言的根因）。
_T2_MAX_FORMS_PER_PAGE = 8

# 被拦截下载/新窗口拒绝的 blocked_events kind（与 §3 DDL CHECK 枚举对齐）
KIND_DOWNLOAD = "download"
KIND_NEW_WINDOW = "new_window"

# T2 提交后"导航落定"轮询间隔（秒）：_t2_wait_navigation_settled 用
# page.url（driver 会话缓存读数，零渲染进程往返、实测导航中亦即时应答）
# 做有界轮询。200ms 粒度 = 导航完成感知延迟上限，总窗口受
# budget.page_timeout_ms 约束有界，不引入新配置面。
# （演进注记：旧口径"固定 1.5s 宽限 sleep 后继续循环"在慢导航下必然踩中
# deactivating 文档挂起——2026-09-28 e2e 场景[2]/[4]卡死根因，废弃。）
_T2_SETTLE_POLL_INTERVAL_SEC = 0.2

# blocked_origin 导航级探测（_probe_blocked_origins）的数量封顶：
# 白名单外 origin 首页逐个 goto 制造拦截证据，探测属额外对外请求，
# 必须限定规模——真实站点外链域名通常个位数，16 封顶足够且总耗时
# 受 delay_ms 限速约束有界。
_BLOCKED_ORIGIN_PROBE_LIMIT = 16

# 页面内导航等待：T1 链接直接用 page.goto（不 click 真实 <a>，理由见
# _visit_t1 docstring）；T2 用 form 原生 submit（GET 导航）。


@dataclass
class CrawlReport:
    """遍历结果汇总（§4.2 尾行：页面数/动作数/拦截数/frontier）。

    Attributes:
        pages_visited: 本次运行内完成探索的页面数（含 resume 前已完成的不重计）。
        pages_failed: timeout/error 收口的页面数。
        actions_total: 分级落库的候选动作总数。
        actions_executed: 实际执行的 T1/T2 动作数（T3 恒 0，红线⑤）。
        blocked_total: 落库的拦截事件数。
        blocked_dropped: 有界队列满丢弃数（非 0 需在报告第 10 节 d 项声明）。
        exhausted_dimension: 预算耗尽维度名（None=队列自然耗尽）。
        frontier: 未探索节点列表（store.frontier() 直传）。
    """

    pages_visited: int = 0
    pages_failed: int = 0
    actions_total: int = 0
    actions_executed: int = 0
    blocked_total: int = 0
    blocked_dropped: int = 0
    exhausted_dimension: Optional[str] = None
    frontier: List[RedactedDict] = field(default_factory=list)

    def to_redacted(self) -> RedactedDict:
        """转已脱敏 dict（summary.json / 第 10 节数据源，全为计数无敏感值）。

        Returns:
            RedactedDict: 报告 dict。
        """
        return redact({
            "pages_visited": self.pages_visited,
            "pages_failed": self.pages_failed,
            "actions_total": self.actions_total,
            "actions_executed": self.actions_executed,
            "blocked_total": self.blocked_total,
            "blocked_dropped": self.blocked_dropped,
            "exhausted_dimension": self.exhausted_dimension,
            "frontier": [dict(f) for f in self.frontier],
        })


class _HashNavigationRef:
    """SPA hash 导航记录（T1 判定为 hash-only 时的零网络导航标记）。

    语义（§4.2 Note）：目标 url_key 与当前页仅 hash 段不同——点击是纯前端
    路由切换，**预期**不产生任何网络请求/拦截事件/API 观测。
    """

    __slots__ = ("href", "target_key")

    def __init__(self, href: str, target_key: str) -> None:
        """初始化记录。

        Args:
            href: 原始 hash 链接（形如 '#/list?page=2' 或绝对 hash URL）。
            target_key: 归一后的目标 url_key。
        """
        self.href = href
        self.target_key = target_key


class SiteCrawler:
    """BFS 站点遍历器（§2.3.6 全量方法，红线④⑤的代码落点）。

    典型接线（CLI 编排层，route 守卫先于任何导航安装，§5.1 静态审查项）::

        crawler = SiteCrawler(cfg, page, store, observer, limiter)
        crawler.install_route_guard(context)   # crawl() 首行也会兜底安装
        report = crawler.crawl()
    """

    def __init__(
        self,
        cfg: SuConfig,
        page: "Page",
        store: StateStore,
        observer: ApiObserver,
        limiter: RateLimiter,
        context: Optional["BrowserContext"] = None,
        login: Optional["BrowserLogin"] = None,
    ) -> None:
        """初始化遍历器。

        Args:
            cfg: 完整运行配置（budget/allowed_origins/base_url）。
            page: Playwright Page（单 Page 串行，AP-6）。
            store: SQLite 状态机（幂等写入/resume）。
            observer: API 观测器（attach 由本类在 crawl 内完成）。
            limiter: 全局限速器（NFR-SU-001：所有对外请求前先 wait）。
            context: 浏览器上下文（可选）。**login 在位时必传**——会话
                失效重登要用同一 context（cookie 面）；单独传入而无 login
                时不产生任何行为（不构成未用参数负担）。
            login: BrowserLogin 协作者（可选，REQ-SU-004.4 会话失效自动
                重登的接线点）。crawl 检测到导航回跳登录页时调用其
                ``relogin_if_needed(page, context)``（≤3 次、重登自身过
                同一限速器）；缺省 None = 不启用重登（离线单测/无凭据
                采集形态）。
        """
        self._cfg = cfg
        self._page = page
        self._store = store
        self._observer = observer
        self._limiter = limiter
        self._context = context
        self._login = login
        # 重登成功后的本轮补采计数（防御环：同一轮重登循环内每成功一次
        # +1，超过 LOGIN_MAX_RETRY 视为"页面永远回跳登录页"，按会话失效
        # 超限同口径终止；每轮真实探索开始时归零）
        self._relogin_retried = 0
        self._budget = BudgetTracker(cfg.budget)

        # ---- route 层状态（红线④）----
        # handler 允许的全部共享面：有界队列 + 整型计数（无锁需求：CPython
        # 整型 += 在回调单线程派发模型下安全；Playwright sync 回调与主线程
        # 同 greenlet 调度，不存在真并行）
        self._blocked_queue = BlockedEventQueue()
        self._blocked_counter = 0
        self._blocked_origins_seen: Dict[str, int] = {}
        self._route_installed = False

        # ---- 主线程维护的"当前页"上下文（handler/回调注入源，不现查 URL）----
        self._current_page_id: int = 0
        self._current_url_key: str = ""

        # ---- 网络静默跟踪（wait_ready 数据源；回调只写一个 float）----
        self._quiet_tracker = NetworkQuietTracker()
        # 慢页宽限登记（page_timeout 场景心跳稳健性，2026-09-28 e2e 场景[4]
        # 根因修复）：goto 抛 TimeoutError 后，服务端后台线程往往仍在生成
        # 响应；fixture 这类同步单线程服务会**独占处理该请求直至完成**
        # （/slow sleep 40s），后续导航全部被拖到挂满页超时——单页合法耗时
        # 上界不再成立，纯按心跳年龄判挂起会误杀（把"排队等慢页"当挂起）。
        # goto 超时 = 确凿的慢页事实：此后一段有界窗口内心跳停滞属预期，
        # 看门狗通道二在该窗口内改用 page_timeout 派生的宽容阈值。
        self._slow_page_until: float = 0.0

        # 遍历统计（CrawlReport 数据源）
        self._actions_total = 0
        self._actions_executed = 0
        self._blocked_db_total = 0

        # 主文档 server 响应头（_goto_and_wait 主线程写入，技术栈指纹数据源）
        self._main_server_header: str = ""

        # 已入队 url_key 集合（内存去重快路径；pages 表为最终事实源）
        self._enqueued: set = set()
        # BFS 队列：元素 (absolute_url, url_key, depth, discover_from_page_id)
        self._queue: deque = deque()

        # SIGINT 看门狗（2026-09-28 e2e 场景[3]根因修复）：sync Playwright 的
        # 阻塞调用（goto/wait_ready/requestSubmit）把主线程挂在 C 层
        # _dispatcher_fiber.switch() 里，Python 信号处理要等调用返回才执行——
        # 挂起窗口内 SIGINT 被无限期搁置。双通道：编排层信号处理器置位标志
        # （主线程有字节码边界时的快路径）；心跳停滞轮询（挂起窗口唯一可用
        # 通道——心跳停更超上界即断开 playwright driver 管道解挂，待决
        # SIGINT 处理器随即执行）。
        # 解挂手段说明（探针实证，勿改回 browser.close()）：sync Playwright
        # 的对象方法有 greenlet 线程亲和性，从看门狗线程调用 browser.close()
        # 要么被 greenlet 错误吞掉（解挂无效），要么与主线程 dispatcher 调度
        # 竞争导致进程级原生崩溃（Bus error）。安全手段是从后台线程向
        # playwright driver 子进程发信号断开 transport——探针验证：挂起中的
        # goto 抛 Python 级 Error、进程存活、后续 close() 正常。
        # 心跳口径：crawl 每个 **BFS 轮次**（含 resume done 页幂等跳过）
        # 至少刷新一次，单页合法耗时另由 page_timeout/wait_ready 有界——
        # 采集中心跳年龄存在确定上界（详见 _watch_interrupt 通道二注释）。
        self._interrupt_requested = threading.Event()
        # playwright driver 子进程 pid 列表（看门狗解挂专用，见
        # _force_disconnect_playwright）；由编排层在 launch 后登记
        self._driver_pids: list = []
        # 看门狗线程句柄（daemon，进程退出自动回收）
        self._interrupt_watcher: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # SIGINT 看门狗（中断稳健性，与编排层信号处理器协作）
    # ------------------------------------------------------------------

    def register_interrupt_browser(self, driver_pids: list) -> None:
        """登记 playwright driver 子进程 pid 并启动 SIGINT 看门狗线程。

        编排层在 launch 完成后调用（早于 crawl）。看门狗为 daemon 线程：
        平时零开销轮询，仅在信号置位且 run 未收口、或心跳停滞超上界时，
        向 driver 子进程发 SIGKILL 断开 CDP 管道解挂主线程。driver 死后
        浏览器随之消亡、编排层收口路径中的 close() 全部容错，无泄漏。

        Args:
            driver_pids: playwright driver 子进程 pid 列表（通常单元素）。
        """
        self._driver_pids = list(driver_pids)
        watcher = threading.Thread(
            target=self._watch_interrupt, name="su-sigint-watcher", daemon=True)
        self._interrupt_watcher = watcher
        watcher.start()

    # 信号置位后的"未收口"容忍时长（秒）：信号处理器苏醒后收口三步 <1s，
    # 4s 足以覆盖 goto 自然返回的宽限而仍远早于 /slow 40s 挂起
    _INTERRUPT_STALL_SECONDS = 4.0
    # 心跳停滞容忍**下限**（秒，独立于信号通道）：crawler 每个页边界刷新
    # 心跳，单页最大合法耗时上界 ≈ page_timeout(默认 30s，goto 挂满超时) +
    # wait_ready(3s) + T2 提交宽限(1.5s) ≈ 35s——45s 停滞在任何配置下都
    # 只可能是主线程挂在 CDP recv（或开放阻塞）上，绝非正常慢页。
    # 信号处理器在被挂起的主线程上永远不会执行，本通道是挂起窗口内唯一
    # 可依赖的解挂机制（e2e 场景[3]根因修复）。误杀代价也仅为该页记
    # error 继续 BFS（crawler 既有降级路径）。
    _HEARTBEAT_STALL_SECONDS = 45.0

    def _channel_stall_limit(self) -> float:
        """通道二（无信号）心跳停滞容忍（秒，配置自适应）。

        2026-09-28 e2e 场景[4]根因修复：固定 45s 容忍隐含"page_timeout
        默认 30s"的前提。真实配置可能显式放宽导航超时（fixture e2e 用
        15000ms、生产常见 60000ms+）——单页合法耗时上界随 page_timeout
        线性移动（慢页 goto 挂满超时 + wait_ready 3s + T2 宽限 1.5s），
        固定 45s 会把"合法慢页"误判为主线程挂起而误杀遍历。改为
        ``max(45, page_timeout*2 + 15)``：page_timeout=8000 时仍取 45s
        （上界 12.5s，32s 余量防误杀）；page_timeout=60000 时取 135s
        （上界 64.5s，70s 余量）。

        信号已置位时本方法不参与——中断语义下停滞 4s 即解挂（快通道），
        中断意图明确，不存在误杀顾虑。
        """
        try:
            timeout_s = max(0, int(self._cfg.budget.page_timeout_ms)) / 1000.0
        except (AttributeError, TypeError, ValueError):
            timeout_s = 30.0
        return max(self._HEARTBEAT_STALL_SECONDS, timeout_s * 2.0 + 15.0)

    def _watch_interrupt(self) -> None:
        """看门狗主体（双通道）：信号标志快路径 + 心跳停滞兜底通道。

        通道一（快路径，主线程未挂起）：编排层信号处理器置位标志 →
        有界轮询 run 状态收口进度；窗口内仍 running = 收口被后续阻塞
        调用再次挂起 → 断开 playwright driver 强制解挂。随后转入通道二
        继续以收紧阈值值守——通道一时序若与编排层正常收口竞速误断
        driver，主线程恢复后处理待决 SIGINT 仍走 interrupted 收口
        （语义不变差；正常收口竞态在 0.05s 轮询粒度下属毫秒级罕见窗口）。

        通道二（挂起窗口唯一可用通道）：主线程真挂在 C 层
        _dispatcher_fiber.switch() 时，CPython 信号处理得不到字节码边界、
        信号处理器与通道一全部失效（曾评估 signal.set_wakeup_fd——信号
        分发本身同样依赖字节码边界，等价不可行）。心跳由 crawler 每个
        页边界刷新，单页合法耗时存在上界（见 :meth:`_channel_stall_limit`
        注释）：run=running 且心跳年龄超阈值 = 主线程必然不在页边界推进
        = 断开 playwright driver——阻塞调用抛 Python 级异常，主线程恢复
        字节码，待决 SIGINT 的处理器随即执行（interrupted 收口 + exit 130）。

        诚实性口径：通道二误杀正常页处理的唯一前提是"单页耗时超阈值上界"
        ——阈值按 page_timeout 自适应后该前提在默认与放宽配置下均不可能
        成立；即便极端环境误杀，driver 断开后导航异常只是把该页记 error
        继续 BFS（crawler 既有降级路径）。
        """
        # 通道一：信号标志若已置位（主线程有字节码边界——收口大概率顺畅），
        # 先走短轮询快路径；未置位则直接落通道二长期值守
        if self._interrupt_requested.is_set():
            deadline = time.monotonic() + self._INTERRUPT_STALL_SECONDS
            while time.monotonic() < deadline:
                if self._run_settled():
                    return  # 主线程已收口，无需强制解挂
                time.sleep(0.05)
            self._force_disconnect_playwright()
        # 通道二：轮询心跳停滞（进程整个采集期常驻，0.5s 粒度只读查询）。
        # 信号已置位 = 中断意图明确，心跳阈值收紧到快路径容忍值——
        # 收口被阻塞时 4s 即解挂；未收到信号则用完整上界 45s 防误杀
        while True:
            if self._run_settled():
                return  # run 已收口（completed/failed——crawl 自然结束）
            try:
                age = self._store.heartbeat_age_of_current()
            except Exception:  # noqa: BLE001 - 轮询读失败保守跳过本轮
                age = None
            # 无信号分支用配置自适应阈值（慢页宽限期再额外放宽）：
            # 固定 45s 在显式放宽 page_timeout 的配置下会误杀合法慢页
            if self._interrupt_requested.is_set():
                stall_limit = self._INTERRUPT_STALL_SECONDS
            else:
                stall_limit = self._channel_stall_limit()
                if time.monotonic() < self._slow_page_until:
                    # 慢页宽限：刚发生 goto 超时（fixture 独占处理拖住
                    # 后续导航），阈值额外加一个 page_timeout 宽容窗
                    stall_limit += self._channel_stall_limit()
            if age is not None and age > stall_limit:
                self._force_disconnect_playwright()
                # 解挂后等主线程处理待决 SIGINT 收口（或异常路径自行收口）；
                # 15s 仍 running = 进程确实不可恢复，退出看门狗（daemon，
                # 不阻碍进程退出；此时剩余兜底在发送侧 SIGINT 重发）
                deadline = time.monotonic() + 15.0
                while time.monotonic() < deadline:
                    if self._run_settled():
                        return
                    time.sleep(0.2)
                return
            time.sleep(0.5)

    def _force_disconnect_playwright(self) -> None:
        """断开 playwright driver 子进程（解挂主线程的唯一线程安全手段）。

        探针实证（2026-09-28，勿改回 browser.close()）：sync Playwright
        方法有 greenlet 亲和性，跨线程 close 无效甚至原生崩溃；而杀掉
        driver 子进程会让 transport 断开，挂起中的调用抛 Python 级异常、
        进程存活、后续所有 Playwright 调用安全失败（收口路径全部容错）。

        SIGKILL 而非 SIGTERM：driver 若自身挂起则可能不响应 TERM；
        driver 被杀后其托管的浏览器进程随之消亡，无孤儿泄漏。
        """
        for pid in self._driver_pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                # 进程已自然退出 / 无权限（罕见）：管道已断或将由其它
                # 通道兜底，保守跳过该 pid
                continue

    def _run_settled(self) -> bool:
        """查本进程持锁 run 是否已离开 running（收口完成=无需强制解挂）。"""
        try:
            row = self._store.run_status_of_current()
        except Exception:  # noqa: BLE001 - 轮询读失败保守按"未收口"处理
            return False
        return row is not None and str(row) != "running"

    def notify_sigint(self) -> None:
        """编排层信号处理器调用：置位中断标志（看门狗与下一轮循环感知）。"""
        self._interrupt_requested.set()

    # ------------------------------------------------------------------
    # route 守卫安装（红线④，crawl 首行强制先于任何导航）
    # ------------------------------------------------------------------

    def install_route_guard(self, context: "BrowserContext") -> None:
        """安装 route 拦截处理器 + 新窗口拒绝（§2.3.6 / §4.2）。

        **handler 执行模型（硬性约束）**：体内仅允许 ①纯函数决策+计数
        ②有界队列 put(block=False) ③abort/continue_。以下实现逐行遵守——
        决策走 :func:`su.route_policy.decide` 纯函数，url/post_data 净化在
        队列 put 内部完成，队列满自动丢弃计数（dropped），全程零 sync
        Playwright API。

        下载拒绝说明：route 层读不到**响应**头（Content-Disposition 属响应面，
        读取需等待响应 = 违反 handler 模型），故下载判定走双通道：
        ① 请求侧启发式（URL 文件名后缀命中下载类扩展名 → handler 内 abort）；
        ② Playwright 原生 ``page.on('download')`` 事件（回调只写内存队列，
        kind='download'，页边界 drain）。两通道均只记录，绝不落文件。

        Args:
            context: 浏览器上下文（route/new_page 注册面）。
        """
        if self._route_installed:
            return
        allowed_origins = list(self._cfg.system.allowed_origins)
        # 登录 POST 豁免路径（REQ-SU-004.4）：route 守卫先于任何导航安装且
        # 不可动态摘除，会话失效自动重登（crawl 运行期）要以 POST 重新提交
        # 登录表单——不放行则重登必然失败。豁免面由 decide 纯函数钉死为
        # 同源 + path 精确等于配置登录路径；未注入 login 时不启用豁免
        # （无重登协作者，登录 POST 不可能合法出现在采集期，保持红线原貌）。
        login_path_for_guard = (
            urlsplit(self._absolute_login_url()).path if self._login is not None
            else None)

        def _handle_route(route: Any) -> None:
            """route 回调（红线④ handler——三类操作之外的每一行都是违例）。"""
            # ① 决策：纯函数（request.url/method 为事件对象属性读取，非 API 调用）
            request = route.request
            decision = decide(request.method, request.url, allowed_origins,
                              login_path=login_path_for_guard)
            if decision.action == "abort":
                self._blocked_counter += 1
                # ② 有界队列（put 内部完成 url/post_data 净化；满→丢弃计数；
                #    page_id 取主线程维护值，不现查 URL）
                self._blocked_queue.put(
                    kind=decision.kind or "aborted_method",
                    url=request.url,
                    method=request.method,
                    post_data=getattr(request, "post_data", None),
                    page_id=self._current_page_id,
                )
                # blocked_origins 观测集（origin 解析是纯字符串操作，非 API 调用）
                if decision.kind == "blocked_origin":
                    origin = _origin_of_url(request.url)
                    if origin:
                        self._blocked_origins_seen[origin] = (
                            self._blocked_origins_seen.get(origin, 0) + 1)
                # ③ 拦截
                route.abort()
                return
            # 下载类请求（请求侧后缀启发式）：abort + 记 download
            if _download_like(request.url):
                self._blocked_counter += 1
                self._blocked_queue.put(
                    kind=KIND_DOWNLOAD, url=request.url, method=request.method,
                    page_id=self._current_page_id)
                route.abort()
                return
            # ③ 放行（GET/HEAD/OPTIONS + 白名单域）
            route.continue_()

        context.route("**/*", _handle_route)

        # 新窗口拒绝：context 级 'page' 回调同理只写内存队列（不碰 Playwright API，
        # 连 close() 都不调——close 是 sync API；窗口留空壳不访问不探索，
        # 真正的资源收口发生在 context 销毁时）
        def _handle_new_page(new_page: Any) -> None:
            """context 'page' 事件回调（只计数 + 入队，§2.3.6 同款约束）。"""
            self._blocked_counter += 1
            url_text = ""
            try:
                url_text = new_page.url or ""
            except Exception:  # noqa: BLE001 - 事件对象读取容错
                url_text = ""
            self._blocked_queue.put(
                kind=KIND_NEW_WINDOW, url=url_text or "about:blank",
                method=None, page_id=self._current_page_id)

        context.on("page", _handle_new_page)

        # 下载事件（响应侧通道）：同样只写内存队列
        def _handle_download(download: Any) -> None:
            """page 'download' 回调（只入队，不保存文件、不调 suggested_filename 之外 API）。"""
            self._blocked_counter += 1
            try:
                download_url = download.url or ""
            except Exception:  # noqa: BLE001
                download_url = ""
            self._blocked_queue.put(
                kind=KIND_DOWNLOAD, url=download_url, method="GET",
                page_id=self._current_page_id)

        self._page.on("download", _handle_download)

        # 网络静默跟踪接线（§2.3.6 实现口径：回调只写一个 float，非 Playwright API）
        self._page.on("request", lambda _r: self._quiet_tracker.mark_request())
        self._page.on("response", lambda _r: self._quiet_tracker.mark_request())

        # API 观测器接线（放行响应 → shape 观测，双通道划分见 api_observer 头注）
        self._observer.attach(self._page)

        self._route_installed = True

    # ------------------------------------------------------------------
    # BFS 主循环
    # ------------------------------------------------------------------

    def crawl(self) -> CrawlReport:
        """BFS 主循环（§2.3.6 / §4.2 全量步骤）。

        每页流程：限速 → 预算 → resume 幂等（pages 表已 done 则跳过）→
        导航（普通 goto / SPA hash-only goto——零网络请求属预期）→
        current_url_key 注入 → wait_ready → 签名剪枝 + 骨架快照 →
        动作分级（T1/T2 入边队列、T3 只记录）→
        页边界 drain（blocked + observer.flush + blocked_origins.json）→
        heartbeat。

        **route 守卫先于任何导航（§5.1 静态审查项）**：编排层必须在启动
        浏览器后、调用 crawl() 前执行 :meth:`install_route_guard`——
        route 注册在 **context** 上，crawl 内部无法获知 context，故本方法
        不兜底安装（静默不装比错误兜底更可审计；集成测试 §11.3 断言
        "首个导航请求前 route 已安装"）。

        Returns:
            CrawlReport: 遍历汇总（frontier/耗尽维度均在内）。
        """
        start_url = self._cfg.system.base_url
        # resume 队列重建（2026-09-29 e2e 场景[4]根因修复）：BFS 队列是
        # 进程内存对象，SIGINT 收口时队列剩余（pending 页）随进程消失；
        # --resume 轮若只入队 start_url，start_url 在库中已 done → 幂等
        # 跳过 → 队列空 → 采集 0 页秒收口（REQ-SU-019 续采语义失效）。
        # 修复：入队起点前从 pages 表按 BFS 发现序重放未采清单——
        # 非 done 行（pending/error/timeout/exploring，acquire_lock 已把
        # exploring 重置 pending）逐条重新入队，depth/discover_from/url
        # 全部用库内既有值，天然保持发现顺序与深度连续性。
        self._enqueue(start_url, depth=0, discover_from=None)
        if self._cfg.resume:
            rejoin = self._requeue_unfinished()
            if rejoin:
                logger.info("resume 队列重建：重放未采页面 %s 个", rejoin)
        # BFS 起点即"已导航 URL"基准（首轮 _current_navigation_base 即可用）
        self._last_visited_absolute = start_url

        while self._queue:
            if not self._budget.check_time():
                break
            # SIGINT 中断（看门狗已关浏览器 / 信号处理器已置位）：立即停止
            # BFS，收口报告由编排层信号处理器接管（interrupted 态可 --resume）
            if self._interrupt_requested.is_set():
                break
            absolute_url, key, depth, discover_from = self._queue.popleft()

            # 深度规则：超深节点不展开（节点级跳过，不计 exhausted，REQ-SU-008 AC3）。
            # 判定必须先于 consume_page（2026-09-29 e2e 场景[5]根因修复）：
            # 旧实现先扣页预算再判超深 continue——超深节点白白消耗页预算，
            # 小预算 + --max-depth 场景（如 --max-pages 3 --max-depth 1）下
            # 深度 2 节点逐个吃掉仅剩的页额度，第 4 页消费即耗尽 break，
            # done 页远小于预算声明值、frontier pending 恒 0，预算截断语义
            # 与第 10 节 frontier 声明双双失真。超深节点本就"不展开、不
            # 探索"，不应占用任何页预算。
            if not self._budget.depth_allowed(depth):
                continue
            # 预算：页数耗尽即停（frontier = 队列剩余，第 10 节 c 项）
            if not self._budget.consume_page():
                break
            # resume 幂等：已 done 页面直接跳过（AC1 不重复探索）。跳过轮次
            # 同样刷新心跳——SIGINT 看门狗通道二以"心跳年龄上界"判定挂起，
            # resume 重访 done 页是纯库操作（无 goto），心跳必须持续前进，
            # 否则看门狗会把"快速幂等跳过期"误判为主线程挂起
            if self._store.page_done(key):
                self._store.heartbeat()
                continue

            # 限速（NFR-SU-001：每一次对外请求前）
            self._limiter.wait("crawler")

            # 登记页面节点（upsert_page 幂等返回 page_id）
            page_id = self._store.upsert_page(PageNodeRedacted(
                url_key=key,
                url=scrub_text(_strip_creds(absolute_url)),
                depth=depth,
                status="exploring",
                discover_from=discover_from,
            ))
            self._store.set_page_status(page_id, "exploring")

            # 主线程"当前页"注入点（route handler / observer 回调的唯一 page_id 源）
            self._current_page_id = page_id
            self._current_url_key = key
            self._observer.set_current_page(page_id)
            self._budget.reset_page_actions()

            error_text: Optional[str] = None
            # 诊断日志（2026-09-28 e2e 场景[2]卡死定位）：页循环各阶段边界
            # 打点，卡死现场可据日志精确归因到具体调用（goto/采集/T2/分级）
            logger.debug("crawl 页轮次开始 key=%s depth=%s", key, depth)
            try:
                outcome = self._goto_and_wait_with_relogin(absolute_url)
                logger.debug("crawl goto 完成 key=%s stable=%s", key, outcome.stable)
                if not outcome.stable:
                    # 就绪超时：记 error 字段但**继续**采集（不阻塞 BFS，§2.3.6）
                    error_text = outcome.reason
            except Exception as exc:  # noqa: BLE001 - 导航失败记 error 收口本页继续
                # 慢页宽限登记：goto 显式抛 TimeoutError = 确凿的"服务端响应
                # 慢于页预算"事实（如 fixture /slow 独占处理拖住后续导航）。
                # 登记一个 page_timeout 派生的宽容窗口——窗口内看门狗通道二
                # 放宽心跳阈值，防止把"排队等慢页"误判为主线程挂起而误杀
                if type(exc).__name__ == "TimeoutError":
                    try:
                        pt_s = max(0, int(self._cfg.budget.page_timeout_ms)) / 1000.0
                    except (AttributeError, TypeError, ValueError):
                        pt_s = 30.0
                    self._slow_page_until = time.monotonic() + pt_s + 10.0
                # 看门狗已因 SIGINT 关闭浏览器：阻塞调用抛出的连接类异常
                # 不是普通导航失败，中断传播交给主线程信号处理（剩余页
                # 留 pending，--resume 续采），绝不当作 error 页继续 BFS
                if self._interrupt_requested.is_set():
                    break
                error_text = "navigate_failed: {0}".format(
                    " ".join(str(exc).split())[:160])
                self._store.set_page_status(
                    page_id, "error", error=scrub_text(error_text))
                self._drain_blocked()
                self._observer.flush()
                self._store.heartbeat()
                self._pages_failed += 1
                continue

            # 签名剪枝 + 语义骨架快照（永不读 input value，§5.1 静态审查项）
            signatures: List[Dict[str, Any]] = []
            snapshot_path: Optional[str] = None
            title: Optional[str] = None
            tech_fingerprint: Optional[str] = None
            try:
                title = (self._page.title() or None)
                signatures = self.extract_signature(self._page)
                skeleton = self.semantic_skeleton(self._page)
                snapshot_path = self._write_snapshot(page_id, skeleton)
                tech_fingerprint = self._tech_fingerprint()
                logger.debug("crawl 采集完成 key=%s signatures=%s", key, len(signatures))
            except Exception as exc:  # noqa: BLE001 - 快照失败降级：骨架缺失不拖垮遍历
                error_text = (error_text + "; " if error_text else "") + \
                    "snapshot_failed: {0}".format(" ".join(str(exc).split())[:120])

            # T2 执行点（红线⑤安全形态）：本页上把分级为 T2 的显式 GET 表单
            # 用**中性值**填写后走 form.requestSubmit()（等价显式 GET 提交，
            # fill_neutral 恒为 'test'/''，^[A-Za-z0-9]{0,20}$ 断言保证）；
            # 表单导航随即把页面带到查询结果态，后续采集在该状态上继续。
            try:
                n_t2 = self._execute_t2_forms(signatures, page_id)
                self._actions_executed += n_t2
                logger.debug("crawl T2 完成 key=%s executed=%s", key, n_t2)
            except Exception:  # noqa: BLE001 - T2 提交失败不拖垮遍历（动作仍按 T2 落库）
                pass

            # 动作分级（含入边/入队与落库；单页动作预算在 _classify_and_dispatch 内控制）
            self._classify_and_dispatch(page_id, key, depth, signatures)
            logger.debug("crawl 分级完成 key=%s", key)

            # 页边界收口：blocked drain + API flush + 心跳（§2.3.6 同点）
            self._drain_blocked()
            self._observer.flush()
            self._store.set_page_status(
                page_id,
                "timeout" if error_text == WAIT_READY_TIMEOUT_REASON else "done",
                error=error_text,
                snapshot_path=snapshot_path,
            )
            # upsert_page 的 COALESCE 刷新通道回填 title / 技术栈指纹（已脱敏）
            self._store.upsert_page(PageNodeRedacted(
                url_key=key,
                url=scrub_text(_strip_creds(absolute_url)),
                depth=depth,
                status="done",
                title=title,
                tech_fingerprint=tech_fingerprint,
            ))
            self._store.heartbeat()
            self._pages_visited += 1

        # blocked_origin 导航级显式探测（BFS 收口后执行；interrupted 收口
        # 不探测——中断语义优先，剩余工作留给 --resume）
        if not self._interrupt_requested.is_set():
            try:
                self._probe_blocked_origins()
            except Exception:  # noqa: BLE001 - 探测失败不影响采集收口（best-effort）
                logger.debug("blocked_origin 探测异常（忽略，不影响采集报告）",
                             exc_info=True)

        # 收口报告
        report = CrawlReport(
            pages_visited=self._pages_visited,
            pages_failed=self._pages_failed,
            actions_total=self._actions_total,
            actions_executed=self._actions_executed,
            blocked_total=self._blocked_db_total,
            blocked_dropped=self._blocked_queue.dropped,
            exhausted_dimension=self._budget.exhausted,
            frontier=self._store.frontier(),
        )
        return report

    # crawl 内部计数别名（声明为普通属性，避免 dataclass 字段与实例对象混淆）
    _pages_visited = 0
    _pages_failed = 0

    # ------------------------------------------------------------------
    # 白名单外 origin 拦截证据采集
    # ------------------------------------------------------------------

    def _probe_blocked_origins(self) -> None:
        """导航级显式探测：真实采到外链的白名单外 origin 逐个 goto，制造
        blocked_origin 拦截证据（§2.3.6 blocked 采集面的补全通道）。

        背景（2026-09-28 e2e 场景[2]实测）：白名单外 <a href> 按红线⑤只判
        T3 记录、绝不点击；跨域 GET 表单的 requestSubmit 若真实执行，导航被
        route guard abort 后页面停留原位，会污染后续页采集（整页采错文档）。
        两条既有通道都无法在"不破坏遍历"的前提下产生 blocked_origin 事件。
        本方法以**导航级探测**补全证据链：对采集期真实发现的白名单外 origin
        首页执行 ``page.goto``——请求必然经过 route handler，由守卫裁决
        abort 并记 blocked_origin（探测不绕过、不弱化守卫；abort 后 goto
        抛错属预期，捕获吞掉）。

        范围约束（保守、可审计）：
          - 只探采集期**真实采到**的 origin（外链或表单 action 出现过），
            绝不构造猜测性目标；
          - 每 origin 仅探首页一次、数量封顶 _BLOCKED_ORIGIN_PROBE_LIMIT；
          - 探测在 BFS 自然收口后执行，不消耗页预算、不产生 pages/edges
            行（纯拦截证据采集）；
          - 探测前后显式静默 API 观测器：外域探测的失败请求/子资源不产生
            api_observations 行（场景[3]"api_observations 外域零入库"红线
            口径——blocked_events 才是本通道唯一落盘面）。
        """
        base_origin = _origin_of_url(self._cfg.system.base_url)
        targets: List[str] = []
        for origin in sorted(self._blocked_origins_seen):
            if not origin or origin == base_origin:
                continue
            targets.append(origin)
            if len(targets) >= _BLOCKED_ORIGIN_PROBE_LIMIT:
                break
        if not targets:
            return
        logger.info("blocked_origin 探测开始：targets=%s", targets)
        for url in targets:
            if self._interrupt_requested.is_set():
                return
            # 慢页后外部状态检查：探测发生在 BFS 自然收口之后，BFS 期间
            # 收到的 SIGINT（handler 置位 → BFS break → 编排层收口三步在
            # 途）必须让位——探测的 goto 会与中断收口的浏览器栈关闭竞速，
            # 触发 driver 管道半断状态下的原生崩溃（2026-09-28 e2e 场景[4]
            # Bus error 教训）。收口三步本身不调用 Playwright，探测循环
            # 主动让位即可消除该竞速窗口。
            if self._store.run_status_of_current() not in (None, "running"):
                return
            # 限速：探测同样是对外的每一次请求（NFR-SU-001 无例外通道）
            self._limiter.wait("crawler")
            # 观测基准备份：探测导航会覆写 _last_visited_absolute（hash 拼接
            # 基准），用后原样恢复——探测不产生 pages/edges，绝不污染真实遍历
            saved_visited = self._last_visited_absolute
            self._observer.set_silent(True)
            try:
                self._page.goto(url, wait_until="domcontentloaded",
                                timeout=self._cfg.budget.page_timeout_ms)
            except Exception:  # noqa: BLE001 - abort/超时致 goto 抛错属预期证据形态
                pass
            # 探测导航后页面停在错误页（或被守卫拦成错误态）：显式归位
            # about:blank，避免下一轮探测/收口时 page 停留在半途文档
            try:
                self._page.goto("about:blank", wait_until="domcontentloaded",
                                timeout=self._cfg.budget.page_timeout_ms)
            except Exception:  # noqa: BLE001 - 归位失败无关紧要（context 收口兜底）
                pass
            finally:
                self._observer.set_silent(False)
            self._last_visited_absolute = saved_visited
            # 页边界同款收口：blocked 队列 drain 落库（page_id 注入 None——
            # 探测不属于任何已采页面）+ blocked_origins.json 重写
            self._current_page_id = None
            try:
                self._drain_blocked()
                self._store.heartbeat()
            except Exception:  # noqa: BLE001 - 状态库已被收口路径关闭等：放弃剩余探测
                return
        # 探测计数（收口报告可审计：blocked_total 含探测产生的拦截事件）
        self._blocked_probe_count = len(targets)

    # ------------------------------------------------------------------
    # 导航与就绪
    # ------------------------------------------------------------------

    def _absolute_login_url(self) -> str:
        """配置的 login_url 绝对化（相对路径与 base_url 拼接）。

        与 :meth:`su.browser_login.BrowserLogin._absolute_login_url` 同口径
        （保留各自的公开协作面独立性：crawler 的判定只依赖配置文本，不
        依赖 login 协作者是否注入——login 缺省时回跳检测仍可观测降级）。

        Returns:
            str: 登录页绝对 URL（login_url 缺省 '/login'，与 BrowserLogin
                缺省一致）。
        """
        from urllib.parse import urlunsplit as _urlunsplit

        login_url = (self._cfg.system.login_url or "/login").strip()
        parts = urlsplit(login_url)
        if parts.scheme and parts.netloc:
            return login_url
        base = urlsplit(self._cfg.system.base_url)
        path = login_url if login_url.startswith("/") else "/{0}".format(login_url)
        return _urlunsplit((base.scheme, base.netloc, path, "", ""))

    def _goto_and_wait_with_relogin(self, absolute_url: str) -> WaitReadyOutcome:
        """goto + 就绪 + 会话失效回跳登录页自动重登（REQ-SU-004.4 接线点）。

        流程：goto 并就绪后检测 ``page.url`` 是否回跳登录页（纯函数
        :func:`redirected_to_login`，同源 + path 归一比较，SPA hash 路由
        覆盖）——命中且注入了 login 协作者时：

          1. 调 ``login.relogin_if_needed(page, context)``（重登上限
             LOGIN_MAX_RETRY=3 由协作者内部计数；重登导航经其内部
             ``_limiter.wait('login')`` 走同一全局限速阀门）；协作者超限
             抛出的 SuLoginError 属致命错误，**原样上抛**（CLI 已按
             exit 4 收口，本方法绝不让其被透镜隔离降级）；
          2. 重登成功 → **当前页重新 goto 采集本轮**（不消耗额外页预算：
             BFS 头部的 consume_page 本轮只发生一次；goto 前刷新限速
             阀门——NFR-SU-001 每一次对外导航无一例外）；
          3. 重登失败（返回 False，次数额度未用尽）→ 本轮按 error 页收口
             继续 BFS（下一个受保护页会再次尝试，直至协作者内部超限）；
          4. 防御环：补采后仍回跳（登录态与 fixture 之外的病态形态——
             服务端登录后仍永远 302 回登录页）时，同轮重登成功次数超过
             LOGIN_MAX_RETRY → 抛 SuLoginError（与协作者超限同文案口径），
             杜绝 goto↔relogin 无限循环。

        未注入 login（离线单测 / 无凭据形态）：仅打一条 warning 观测日志
        （每轮至多一次），按就绪结果照常返回，不改变既有采集语义。

        Args:
            absolute_url: 目标页绝对 URL。

        Returns:
            WaitReadyOutcome: 最终一次导航的就绪判定（重登成功后为补采
                导航的结果）。

        Raises:
            RuntimeError: 重登返回 False（会话失效且单次重登未成功）——
                本轮按导航失败收口（crawl 既有 error 页降级路径），
                会话反复失效的最终判定在 login 协作者内部（超限抛
                SuLoginError 原样上抛，CLI exit 4 收口）。
            SuLoginError: login 协作者重登超限（REQ-SU-004.4 AC4），或
                同轮补采防御环超限（致命——上抛至 CLI exit 4，不被
                "透镜失败隔离"降级）。
        """
        outcome = self._goto_and_wait(absolute_url)
        if self._login is None:
            return outcome
        login_url = self._absolute_login_url()
        # 每轮真实探索开始：防御环计数归零（page_done 幂等跳过的轮次
        # 不进本方法，不存在跨轮累计污染）
        self._relogin_retried = 0
        while redirected_to_login(self._page.url, login_url):
            if self._interrupt_requested.is_set():
                # 中断语义优先：不再发起重登导航，按既有结果收口
                break
            logger.warning(
                "检测到回跳登录页（url=%s login=%s），触发会话失效自动重登",
                _strip_creds(self._page.url), login_url)
            # relogin_if_needed 语义（browser_login 契约）：超限抛
            # SuLoginError；单次失败返回 False；成功返回 True。
            # 协作者 SuLoginError 不在任何 except 捕获面内——原样上抛
            relogged = self._login.relogin_if_needed(self._page, self._context)
            if not relogged:
                logger.warning("自动重登未成功（次数额度未用尽），本轮按导航失败收口")
                raise RuntimeError("relogin_failed: 会话失效且自动重登未成功")
            self._relogin_retried += 1
            if self._relogin_retried > 3:
                # 防御环：重登"成功"但补采仍回跳登录页（登录态永远无效的
                # 病态服务端形态）——与 LOGIN_MAX_RETRY 同口径终止
                from su.dto import SuLoginError as _SuLoginError

                raise _SuLoginError(
                    "会话反复失效：重登后页面仍回跳登录页，请检查账号风控/验证码",
                    hints=["改用 --storage-state 注入人工已登录态后重跑（--resume 续跑）"],
                )
            logger.info(
                "重登成功，重新导航当前页补采本轮（url=%s）", absolute_url)
            # 补采导航也是一次对外请求：显式过同一全局限速阀门
            # （BrowserLogin 的重登导航已刷新阀门时间戳，wait 保证间隔
            # ≥ delay_ms，NFR-SU-001 无例外通道）
            self._limiter.wait("crawler")
            outcome = self._goto_and_wait(absolute_url)
        return outcome

    def _goto_and_wait(self, absolute_url: str) -> WaitReadyOutcome:
        """goto + 有界就绪等待（§2.3.6 wait_ready 的 Playwright 薄壳）。

        Args:
            absolute_url: 目标页绝对 URL。

        Returns:
            WaitReadyOutcome: 稳定/超时判定（超时不抛，调用方记 error 继续）。
        """
        response = self._page.goto(absolute_url, wait_until="domcontentloaded",
                                   timeout=self._cfg.budget.page_timeout_ms)
        # 主线程捕获主文档 server 响应头（技术栈指纹数据源；只取单键，
        # 回调/handler 零采集——红线④不涉及，这里是主线程导航返回值）
        self._main_server_header = _server_header_of(response)
        # 记录实际导航的原始 URL（hash 链接拼接基准；creds 剥离版走落库通道）
        self._last_visited_absolute = absolute_url
        return self._wait_ready(self._page)

    def _wait_ready(self, page: "Page") -> WaitReadyOutcome:
        """有界就绪等待（判定内核在 wait_ready.py，本方法只是注入薄壳）。

        evaluator：一次 evaluate 同时取 main/body 文本与可交互元素数——注入的
        JS 表达式是模块级常量（零外部输入），**只读 textContent 与元素计数，
        绝不读取任何表单控件 value**。

        Args:
            page: 已导航的 Page。

        Returns:
            WaitReadyOutcome: stable/reason/轮次。
        """
        def _evaluator() -> Tuple[str, int]:
            """取样：(main/body 文本 hash, 可交互元素数)（evaluate 为文本常量 JS）。"""
            payload = page.evaluate(_JS_DOM_SNAPSHOT) or {}
            text = payload.get("text") or ""
            count = int(payload.get("interactive") or 0)
            return compute_dom_digest_from(text, count)

        return wait_ready_bounded(
            evaluator=_evaluator,
            tracker=self._quiet_tracker,
            rounds=WAIT_READY_ROUNDS,
            quiet_ms=WAIT_READY_QUIET_MS,
            hard_timeout_ms=WAIT_READY_HARD_TIMEOUT_MS,
        )

    # ------------------------------------------------------------------
    # T2 表单执行（红线⑤安全形态）与技术栈指纹
    # ------------------------------------------------------------------

    def _execute_t2_forms(
        self,
        signatures: List[Dict[str, Any]],
        page_id: int,
    ) -> int:
        """在当前页执行分级为 T2 的显式 GET 表单（安全只读查询提交）。

        安全边界（红线⑤ + §2.3.6）：
          - 只对 :func:`classify_action` 判为 **T2** 的表单控件执行——T2 唯一
            准入条件是"表单显式 ``method=GET`` 且不含危险语义关键词"，判定
            内核与 :meth:`_classify_and_dispatch` 落库用同一纯函数，不存在
            "先执行后分级"的路径；
          - 填写值只来自 :func:`fill_neutral` 纯函数（恒为 ``'test'``/``''``，
            ``^[A-Za-z0-9]{0,20}$`` 断言兜底），**绝不读取任何已有 value**、
            绝不使用任何凭据（凭据只在 browser_login 的 fill 边界出现）；
          - 提交走 ``form.requestSubmit()``（等价显式 GET 提交，触发 submit
            校验与浏览器原生序列化），不点击任何按钮——T3 分支在本方法
            **不存在任何调用路径**（静态审查项）；
          - 每个 form 只提交一次：去重键取控件所属 **form 的 CSS 路径**
            （签名 ``form_selector`` 字段，JS 剪枝与控件路径同法生成，同表单
            控件必得同值）。2026-09-28 e2e 场景[2]根因修复：旧口径按控件
            name/路径生成去重键，同一表单的 input(name=q)+hidden(name=page)
            +button 各成一键，"一个表单一份预算"实际失效——同表单被重复
            提交，且第二次提交的文档级调用必然踩中 deactivating 内核挂起。
            提交预算用 T2 专用页内封顶（:data:`_T2_MAX_FORMS_PER_PAGE`），
            不复用 consume_action（原因见下方"T2 专用页内预算"注释）；
          - **同域目标预判**：目标地址经 :meth:`_form_action_for` 解析显式
            action（缺省 = 当前页，HTML 规范）后与 base_url 同域才真实提交；
            跨域表单只记录分级、绝不 requestSubmit——早先"执行跨域 GET 表单、
            由 route guard 拦截产生 blocked_origin"是初版设计口径，实测
            （2026-09-28 e2e 场景[2]）证明 requestSubmit 被 abort 后页面停留
            原位，后续页采集全部发生在错误文档上、污染遍历图。blocked_origin
            的产生通道改为 :meth:`_probe_blocked_origins` 的导航级显式探测
            （同样真实经过 route handler，abort 由守卫裁决，零弱化）。

        主线程 Playwright 操作合法性：红线④只约束 **route handler 回调体内**
        禁调 sync Playwright API；本方法在 crawl 主线程执行，fill/evaluate
        合法。提交引发的页面跳转由随后的 :meth:`_wait_ready`（下一次 goto）
        或页边界收口自然吸收；本方法内每次提交后只做有界 sleep 让导航起飞，
        不做额外就绪判定（查询结果态的采集属于该表单目标页自己的 BFS 轮次）。

        提交后导航落定防护（2026-09-28 e2e 场景[2]/[4]卡死根因）：
        requestSubmit 触发的 GET 导航一旦起飞，当前文档立即进入
        "deactivating" 状态——此后任何等待渲染进程应答的 Playwright 调用
        （``get_attribute`` / ``evaluate`` / ``query_selector``）都会被阻塞
        直至导航完成才应答。真实站点导航亚秒完成无感；而测试 fixture 这类
        同步阻塞服务端（GET /search 在响应送达前不结束请求处理循环）会把
        这一阻塞放大到服务端请求处理的全程（实测：提交后的第一次
        ``get_attribute`` 挂 31.5s ≈ 服务端 keep-alive 超时窗口，且该挂起
        会占死 greenlet 派发循环、连 sleep 完的宽限也救不回来）。
        防护协议（实测探针 2026-09-28，详见
        :meth:`_t2_wait_navigation_settled`）：提交后只用 ``page.url``
        （driver 会话缓存读数，导航中亦即时应答零阻塞）做有界轮询确认
        导航落定，落定才允许循环继续；未落定收束本轮 T2。
        "确认导航落定后才继续"而非"sleep 完就继续"是正确性要求：s2/s4
        实测教训——宽限 sleep（1.5s）不足以覆盖慢导航，deactivating 文档上
        直接 ``get_attribute`` 会放大挂起到服务端超时窗口，把 BFS 卡死。

        Args:
            signatures: :meth:`extract_signature` 产物（未脱敏原始 dict 列表）。
            page_id: 表单所在页 id（crawl 主循环注入）——executed=1 回写目标。

        Returns:
            int: 实际提交的表单数（计入 CrawlReport.actions_executed）。
        """
        executed = 0
        # 去重集合：同一 form 的多个控件（输入框+隐藏域+提交钮）只触发一次
        # 提交。键 = 控件所属 form 的 CSS 路径（签名 form_selector，cssPath
        # 生成器产物，文档内稳定唯一；见 _form_key_for docstring 的缺陷注记）
        submitted_forms: set = set()
        base_url = self._cfg.system.base_url
        # 导航前预解析的表单 action（el.selector → 绝对 action URL）：
        # _form_action_for 内含 get_attribute/evaluate 文档级调用，必须
        # 全部发生在**首次提交之前**——requestSubmit 导航起飞后当前文档
        # deactivating，任何文档级调用被内核挂起直至导航完成（同步服务端
        # 下实测挂满 30s；实测 2026-09-28）。这里对全部 T2 候选一次性解析
        # （同域预判 + 跨域观测集登记在页面仍活跃时完成），提交循环只用
        # 缓存结果。解析失败的控件不进 map（提交前同域预判失败即跳过，
        # 保守不执行）。
        action_cache: Dict[str, str] = {}
        # 预解析全部走 _form_action_for——显式 action/formaction 命中签名
        # 数据通道（el.formaction/el.form_action，剪枝期已读好的结构属性）
        # 时为纯字符串解析、零 Playwright 调用（2026-09-28 e2e 场景[2]
        # 40s 挂起根因修复：悬挂导航期间逐控件文档级调用被内核占位 10s+）；
        # 仅"签名未携带 action 信息"的控件保留 DOM 兜底（内部异常自吞、
        # 退化规范缺省，不再需要外层 try）。
        for raw in signatures:
            el = _signature_from_raw(raw)
            if el is None:
                continue
            form_ctx = raw.get("form") if isinstance(raw.get("form"), dict) else None
            decision = classify_action(el, form_ctx=form_ctx, base_url=base_url)
            if decision.tier != TIER_T2:
                # T1 是导航（BFS 入队处理）、T3 红线零执行——都不解析 action
                continue
            action_cache[el.selector] = self._form_action_for(el, base_url)
        logger.debug("T2 预解析完成 candidates=%s cached=%s",
                     sum(1 for raw in signatures
                         if _signature_from_raw(raw) is not None), len(action_cache))
        # 本次调用内已提交表单的控件签名：全部提交完成后统一回写 executed=1
        executed_controls: List[ElementSignature] = []
        # T2 专用页内预算（每页最多 _T2_MAX_FORMS_PER_PAGE 个表单真实提交）。
        # **不复用** consume_action——该预算是"每页候选动作"总量，同页十几条
        # 导航链接（T1）在后续 _classify_and_dispatch 中优先消费，T2 排后必然
        # 先行耗尽，显式 GET 表单永不执行（2026-09-28 e2e 场景[2]根因）
        t2_budget = _T2_MAX_FORMS_PER_PAGE
        for raw in signatures:
            el = _signature_from_raw(raw)
            if el is None:
                continue
            form_ctx = raw.get("form") if isinstance(raw.get("form"), dict) else None
            decision = classify_action(el, form_ctx=form_ctx, base_url=base_url)
            if decision.tier != TIER_T2:
                # T1 是导航（BFS 入队处理）、T3 红线零执行——都直接跳过
                # （预算不在此扣减：T1/T3 的预算归属 _classify_and_dispatch）
                continue
            # 跨域 GET 表单不执行（详见方法 docstring"同域目标预判"）：
            # 只保留分级落库记录，外域拦截证据由 _probe_blocked_origins 提供。
            # 同域预判用**导航前预解析缓存**（action_cache）——提交导航起飞
            # 后任何文档级调用（_form_action_for 的 get_attribute/evaluate）
            # 都会被 deactivating 内核挂起（2026-09-28 e2e 场景[2]卡死根因）
            action_url = action_cache.get(el.selector)
            if action_url is None:
                logger.debug("T2 跳过（无 action 缓存）selector=%s", el.selector)
                continue  # 预解析失败/缺失：保守不执行（无目标不提交）
            if _origin_of_url(action_url) != _origin_of_url(base_url):
                logger.debug("T2 跳过（跨域 action=%s）selector=%s",
                             action_url, el.selector)
                continue
            # 去重判定必须在预算扣减**之前**（2026-09-28 e2e 场景[2]根因
            # 修复）：旧口径逐控件计量，同一表单的 input+button 各扣一份，
            # 首个控件 requestSubmit 引发页面跳转后，第二个控件 query_selector
            # 必然落空——预算白耗、同页第二个表单永远轮不到执行。改为
            # "一个表单一份预算"
            form_key = _form_key_for(el)
            if form_key in submitted_forms:
                continue  # 本 form 已提交过（不耗预算）
            if t2_budget <= 0:
                break  # 本页 T2 表单执行封顶（防意外表单风暴）
            try:
                control = self._page.query_selector(el.selector)
                if control is None:
                    logger.debug("T2 跳过（控件已消失）selector=%s", el.selector)
                    continue  # 剪枝后 DOM 已变，跳过（动作记录仍在，只是未执行）
                neutral = fill_neutral(el)
                if neutral:
                    # 中性值 fill：只写 fill_neutral 常量，不读不猜任何现有内容
                    control.fill(neutral)
                # 提交前文档 URL 基准（page.url 为会话缓存读数，导航中亦
                # 即时应答零阻塞——_t2_wait_navigation_settled 的比对基准）
                url_before_submit = self._page.url
                # requestSubmit：等价显式 GET 提交（含 HTML5 校验、原生 query
                # 序列化）；表达式是文本常量 + 传参（arg 仅为选择器字符串，
                # 不进 JS 字符串拼接，无注入面）。返回值 = JS 是否找到控件并
                # 调用提交方法（False = DOM 已变/控件消失，不算执行）
                submitted = bool(
                    self._page.evaluate(_JS_SUBMIT_FORM_BY_CONTROL, el.selector))
                if not submitted:
                    logger.debug("T2 跳过（requestSubmit 未命中控件）selector=%s",
                                 el.selector)
                    continue  # 未真实提交：不占预算、不记 executed、不等待导航
                submitted_forms.add(form_key)
                t2_budget -= 1
                executed_controls.append(el)
                executed += 1
                # 提交后导航落定防护（deactivating 内核挂起的根治点）：
                # GET 导航起飞后当前文档进入 deactivating 态，此后所有等待
                # 渲染进程应答的文档级调用（get_attribute/query_selector/
                # evaluate）被内核挂起直至导航完成——同步服务端把挂起放大
                # 到 31.5s 并占死 greenlet 派发循环（s2/s4 卡死根因，实测
                # 见 _t2_wait_navigation_settled docstring）。page.url 是
                # driver 会话缓存读数（导航中亦即时应答零阻塞，实测 73 次
                # 轮询/16.6s 全即时），以它做有界轮询确认导航结束；
                # 未落定则收束本轮 T2，绝不带着 deactivating 文档继续
                # 同页循环（下一轮迭代的文档级调用必然被挂起）。
                # BFS 存活依据（实测 2026-09-28）： crawl 下一轮迭代的 goto
                # 能抢占卡死中的进行中导航（0.0s 接管新导航目标、旧请求被
                # 内核抛弃、接管后 evaluate 即时可用），旧导航在服务端的余下
                # 耗时由 ThreadingHTTPServer 型服务端隔离，不再拖住遍历。
                if not self._t2_wait_navigation_settled(url_before_submit):
                    logger.debug("T2 settled 未落定 selector=%s（收束本轮）", el.selector)
                    # 导航未在窗口内落定（慢/卡死导航）：当前文档仍是
                    # deactivating 态，同页任何后续文档级调用必然被内核挂
                    # 起——本轮 T2 就此收束（已提交表单照常计数、统一回写
                    # executed）。剩余未提交候选不丢：_classify_and_dispatch
                    # 把 T2 目标照常入 BFS 队列（action_url 由 decision 独立
                    # 解析、不依赖本方法状态），该页在 BFS 中也有自己的访问
                    # 轮次（page_done 幂等），届时将重新执行本方法的完整协议
                    break
            except Exception as exc:  # noqa: BLE001 - 单表单提交失败不拖垮整页遍历
                # 区分性诊断：提交循环内任何文档级调用异常（fill/evaluate/
                # query_selector）都记录——T2 静默零执行时的唯一归因通道
                logger.debug("T2 提交异常 selector=%s err=%s", el.selector, exc)
                continue
        # executed=1 回写（页边界真值）：真实 requestSubmit 成功的表单把
        # page_actions.executed 置 1——executed 列语义 = "本 run 内实际提交过"
        # （红线⑤口径不变：T3 永不进本通道，mark_action_executed 内还有
        # tier='T2' 硬约束兜底）。放在全部提交完成后统一回写：提交引发的
        # 页面跳转不影响 SQLite 写入。
        if executed_controls:
            for el in executed_controls:
                element_sig = json.dumps(
                    dict(redact(_signature_public_dict(el))),
                    ensure_ascii=False, sort_keys=True)
                try:
                    updated = self._store.mark_action_executed(page_id, element_sig)
                    if updated == 0:
                        # 静默 0 行 = element_sig 与 insert_action 落库形态漂移
                        # （同一次 crawl 内同管线仍 0 行属事实异常，必须可观测）
                        logger.debug("T2 executed 回写 0 行 page_id=%s sig=%s",
                                     page_id, element_sig[:200])
                except Exception as exc:  # noqa: BLE001 - 回写失败不回滚已执行事实
                    # 区分性诊断：回写抛错是"T2 执行了但 DB executed=0"
                    # 断链（2026-09-28 e2e 场景[2]）的唯一归因通道
                    logger.debug("T2 executed 回写失败 page_id=%s err=%s sig=%s",
                                 page_id, exc, element_sig[:200])
                    continue
        return executed

    def _t2_wait_navigation_settled(self, url_before_submit: str) -> bool:
        """T2 提交后确认导航落定（成功或失败），页面回到可交互文档态。

        确认协议（背景见 :meth:`_execute_t2_forms` docstring"提交后文档失效
        防护"）——全部判定只允许使用**实测即时应答**的调用面：

          1. ``page.url`` 是 driver 会话缓存读数（实测导航中亦即时应答、
             零渲染进程往返），在 Python 侧做**有界轮询**（不用
             ``wait_for_url``——该 API 走文档级等待通道，deactivating 期间
             应答时机不可靠，实测同类调用被内核挂起 31.5s）；
          2. URL ≠ 提交前值 = 导航已完成（新 URL 的文档由 driver 导航完成
             事件后才更新到会话缓存；新文档可能仍在加载，但内核已脱离
             deactivating 态，后续文档级调用不再被无界挂起）→ True；
          3. **同 URL 完成导航**（2026-09-28 e2e 场景[2]根因）：302 回同页
             /200 同 URL/表单回显当前页等形态下导航真实完结但 URL 恒等，
             单靠 URL 比对必然"settled 不落定"。窗口尾段补第二判据：有界
             ``page.wait_for_load_state('load')``——文档未完结时该调用被内
             核占位到导航完成（即"已完结"信号本身），timeout 参数保证有界；
             返回即证明内核脱离 deactivating 态 → True。轮询前段不插该调用
             的理由：deactivating 期间的占位行为不可靠（可能整窗挂住），只在
             URL 判据耗尽后作为终结判定使用；
          4. 双判据均耗尽：导航未起飞（脚本 preventDefault 阻止提交）或
             导航极慢/不可达（实测：同步服务端把 GET /search 拖到 25s+）→
             False（no_navigation），调用方收束本轮 T2。BFS 存活依据
             （实测 2026-09-28）：下一轮迭代的 goto 抢占卡死中的导航
             （0.0s 接管、旧请求被内核抛弃），页面对象即刻恢复可用；个别
             服务端形态下 goto 若仍被挂起，由其自身 page_timeout_ms 超时
             兜底（crawl 主循环既有 error 页降级 + 慢页宽限登记）。

        诚实性说明：evaluate/get_attribute 等文档级调用在 deactivating 旧
        上下文期间的应答时机**不可靠**（实测既不立即拒绝、也可能被占位到
        导航完成），本方法因此不使用任何文档级调用做确认；轮询粒度内也不做
        就绪判定——导航目标页自己的 BFS 轮次负责其内容采集。

        Args:
            url_before_submit: 提交前的 ``page.url``（导航判定基准）。

        Returns:
            bool: True=导航已落定到新 URL，循环可继续；False=收束本轮。
        """
        started = time.monotonic()
        # 有界轮询窗口：min(page_timeout, 5s)。不用完整 page_timeout 的原因
        # （2026-09-28 e2e 场景[2]实测教训）：同步服务端下被抢占的卡死导航
        # 永远不落地，URL 恒为提交前值——长窗口会让每个含 T2 表单的页白等
        # 满页预算，整站采集时间成倍恶化甚至撞 run 总预算。5s 取值依据：
        # 正常导航毫秒级落定；fixture 独占处理形态下 /search 请求真正到达
        # 服务端的时刻 = 上一挂起请求被 goto 抢占之后（实测 route 事件在
        # 提交后 ~8s 才触发），窗口再长也等不到本次轮次的落定，纯属白等。
        # 未落定收束本轮 T2 的语义不变（导航已被计数），代价上限恒 5s。
        deadline = time.monotonic() + min(
            5.0, max(1, int(self._cfg.budget.page_timeout_ms)) / 1000.0)
        while time.monotonic() < deadline:
            try:
                if self._page.url != url_before_submit:
                    return True  # 导航落定（成功页或错误页），内核已解挂
            except Exception:  # noqa: BLE001 - 会话异常：保守收束
                return False
            time.sleep(_T2_SETTLE_POLL_INTERVAL_SEC)
        # 第二判据（docstring 第 3 条，2026-09-28 e2e 场景[2]根因修复）：
        # URL 恒等 ≠ 未导航——302 回同页 / 表单回显当前页等"同 URL 完成导航"
        # 形态下文档已完结但 page.url 永不变。有界 wait_for_load_state('load')
        # 是"文档完结"的直接信号：未完结时内核把该调用占位到导航完成才应答
        # （实测同 URL 完成导航下立即返回），timeout 保证任何形态有界。
        # 卡死导航形态：本调用占位至 timeout 抛错 → 落入下方收束分支，
        # 代价恒有界（≤ window_tail）。
        window_tail_ms = max(1000, int(self._cfg.budget.page_timeout_ms) // 3)
        try:
            self._page.wait_for_load_state("load", timeout=window_tail_ms)
            # load 完成：URL 仍等 = "提交完成但无导航/同 URL 完成导航"
            # （no_navigation 语义——表单确实提交过、executed 计数照常），
            # 文档可交互，调用方收束本轮 T2 后页边界收口全程有界。
            logger.debug("T2 settled 同URL完成判定（load 落定）url=%s", url_before_submit)
            return True
        except Exception:  # noqa: BLE001 - 超时=导航真未完结（卡死导航）
            pass
        _ = started  # 观测位（当前仅调试用，保留窗口耗时口径说明的锚点）
        return False  # 窗口内导航未落定（no_navigation）：收束本轮 T2

    def _tech_fingerprint(self) -> Optional[str]:
        """技术栈指纹（§2.3.6 pages.tech_fingerprint 列，元数据级采集）。

        来源全部是**响应/文档元数据**（server 响应头、generator 元标签、
        静态资源路径特征），不采集任何业务内容：
          - ``server``：主文档响应头 server（如 nginx/1.24.0）；
          - ``meta_generator``：``<meta name=generator>``（如 WordPress 版本）；
          - ``asset_hints``：script/img src 路径中的常见框架特征词（wp-content、
            /static/admin、/django、/vue、/react 等，只做布尔存在性标记）。

        产物过 :func:`redact` 后 JSON 序列化落库（版本字符串可能被安全团队
        视为敏感指纹——统一走脱敏管线，长度截 512）。

        Returns:
            str | None: 指纹 JSON 文本；页面已关闭等异常返回 None（降级）。
        """
        fingerprint: Dict[str, Any] = {}
        # 主文档 server 响应头：由 crawl 主线程在 goto 返回后捕获（见
        # _goto_and_wait），handler/回调零采集——api_observer "不落 headers"
        # 同口径，这里只保留 server 单键做技术栈指纹
        server_header = self._main_server_header
        if server_header:
            fingerprint["server"] = server_header[:120]
        try:
            meta = self._page.evaluate(_JS_TECH_FINGERPRINT)
        except Exception:  # noqa: BLE001 - 页面状态异常时指纹缺失属预期降级
            meta = None
        if isinstance(meta, dict):
            generator = meta.get("generator")
            if generator:
                fingerprint["meta_generator"] = str(generator)[:120]
            hints = meta.get("asset_hints")
            if isinstance(hints, list) and hints:
                fingerprint["asset_hints"] = [str(h)[:60] for h in hints[:12]]
        if not fingerprint:
            return None
        return json.dumps(
            dict(redact(fingerprint)), ensure_ascii=False, sort_keys=True)[:512]

    # ------------------------------------------------------------------
    # DOM 剪枝与骨架
    # ------------------------------------------------------------------

    def extract_signature(self, page: "Page") -> List[Dict[str, Any]]:
        """DOM 剪枝提签名（§2.3.6；返回**未脱敏原始 dict 列表**，落库前分级链逐字段过 redact）。

        **隐私硬约束（2026-09-28 审查）**：注入的 JS（:data:`JS_EXTRACT_SIGNATURES`）
        只读 tag/role/text/aria-label/href/type/name/id 与 form 的
        method/action/聚合文本，**永不读取 input/textarea 的 value 属性**
        （用户可能已输入敏感内容；§5.1 静态审查项 grep 'value' 白名单核对）。

        Args:
            page: 就绪后的 Page。

        Returns:
            list[dict]: 原始签名 dict（上限 :data:`MAX_SIGNATURE_ELEMENTS`，
                每项含 el 字段 + form 上下文；截断量在 JS 侧不可见，以
                长度上限保证单快照可控）。
        """
        raw = page.evaluate(_JS_EXTRACT_SIGNATURES) or []
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)][:MAX_SIGNATURE_ELEMENTS]

    def semantic_skeleton(self, page: "Page") -> RedactedDict:
        """语义骨架：标题层级 + main/body 文本摘要（scrub 脱敏，≤64KB，§2.3.6）。

        Args:
            page: 就绪后的 Page。

        Returns:
            RedactedDict: 骨架 dict（redact 管线产物，写 snapshots/<id>.json）。
        """
        payload = page.evaluate(_JS_SEMANTIC_SKELETON) or {}
        headings_raw = payload.get("headings") if isinstance(payload, dict) else None
        headings: List[RedactedDict] = []
        total = 0
        for item in (headings_raw or []):
            if not isinstance(item, dict):
                continue
            text = scrub_text(str(item.get("text") or "")[:200])
            entry = RedactedDict({"level": _safe_int(item.get("level")) or 1, "text": text})
            cost = len(text) + 8
            if total + cost > SNAPSHOT_MAX_BYTES // 2:
                break  # 标题预算 ≤32KB（其余留给正文）
            total += cost
            headings.append(entry)
        body_text = scrub_text(str(payload.get("main_text") or ""))
        # 正文截断到剩余预算（字符数近似字节数：scrub 后以 UTF-8 长度精测）
        budget_chars = (SNAPSHOT_MAX_BYTES - total) // 3  # 中文 3 字节/字保守
        skeleton = redact({
            "url_key": self._current_url_key,
            "title": scrub_text(str(payload.get("title") or ""))[:200],
            "headings": [dict(h) for h in headings],
            "main_text_excerpt": body_text[:max(0, budget_chars)],
            "interactive_count": _safe_int(payload.get("interactive")) or 0,
            "generated_at": time.time(),
        })
        return skeleton

    def _write_snapshot(self, page_id: int, skeleton: RedactedDict) -> str:
        """写 ``snapshots/<page_id>.json`` 并返回相对路径（≤64KB 硬截断）。

        Args:
            page_id: 页面 id。
            skeleton: 语义骨架（RedactedDict）。

        Returns:
            str: 相对输出根目录的快照路径（pages.snapshot_path 列）。
        """
        snap_dir = self._cfg.out_dir / self._cfg.system_id / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        text = json.dumps(skeleton, ensure_ascii=False, sort_keys=True)
        if len(text.encode("utf-8")) > SNAPSHOT_MAX_BYTES:
            # 精确截断：丢正文excerpt 重序列化（骨架结构仍在，标注截断）
            shrunk = redact({
                "url_key": skeleton.get("url_key"),
                "title": skeleton.get("title"),
                "headings": [dict(h) for h in (skeleton.get("headings") or [])][:50],
                "main_text_excerpt": "",
                "snapshot_truncated": True,
                "interactive_count": skeleton.get("interactive_count"),
                "generated_at": skeleton.get("generated_at"),
            })
            text = json.dumps(shrunk, ensure_ascii=False, sort_keys=True)
        relative = "snapshots/{0}.json".format(page_id)
        with (self._cfg.out_dir / self._cfg.system_id / relative).open(
                "w", encoding="utf-8") as fh:
            fh.write(text)
        return relative

    # ------------------------------------------------------------------
    # 动作分级与执行分发
    # ------------------------------------------------------------------

    def _classify_and_dispatch(
        self,
        page_id: int,
        current_key: str,
        depth: int,
        signatures: List[Dict[str, Any]],
    ) -> None:
        """逐签名分级并分发（§4.2：T1/T2 入边队列、T3 只记录，红线⑤）。

        Args:
            page_id: 当前页 id。
            current_key: 当前页 url_key（edge.from_key）。
            depth: 当前 BFS 深度（子节点 depth+1）。
            signatures: extract_signature 产物。
        """
        base_url = self._cfg.system.base_url
        for raw in signatures:
            el = _signature_from_raw(raw)
            if el is None:
                continue
            form_ctx = raw.get("form") if isinstance(raw.get("form"), dict) else None
            decision = classify_action(el, form_ctx=form_ctx, base_url=base_url)
            # 白名单外 origin 发现登记（blocked_origin 证据链入口，
            # 2026-09-28 e2e 场景[2]根因修复）：外域 <a href>（T3）、跨域
            # 表单 action（T2/T3）永远不会产生真实请求（红线⑤零点击/
            # 同域预判拦截），route handler 观测集因此恒空——必须在分级
            # 落库点统一登记"页面真实存在的白名单外目标"，BFS 收口后由
            # _probe_blocked_origins 导航级探测制造真实拦截证据。
            # 本调用只做纯字符串解析 + 内存字典写入（零 Playwright API，
            # 非点击/非导航——红线⑤ T3 零执行口径不变）。
            self._register_discovered_origin(el, decision, form_ctx, base_url)
            # element_sig 落库形态：签名关键字段的 redact JSON（无 value，可解释）
            element_sig = json.dumps(
                dict(redact(_signature_public_dict(el))),
                ensure_ascii=False, sort_keys=True)
            self._store.insert_action(ActionDecisionRedacted(
                page_id=page_id,
                element_sig=element_sig,
                tier=decision.tier,
                rule_name=scrub_text(decision.rule_name),
                executed=0,
            ))
            self._actions_total += 1

            if decision.tier == TIER_T3:
                # 红线⑤：T3 分支**不存在任何 click/fill 调用路径**（静态审查项）。
                # 白名单外目标已在循环顶部 _register_discovered_origin
                # 统一登记（blocked_origin 发现通道），此处直接跳过
                continue
            # 页内动作预算（REQ-SU-008.1 max_actions_per_page）
            if not self._budget.consume_action():
                continue
            # 计算目标 URL（T1=href 绝对化；T2=显式 GET 表单 action+query 键名化）
            target = self._resolve_target(el, decision.tier, base_url)
            if target is None:
                continue
            target_key, absolute = target
            if absolute is None and target_key == current_key:
                # hash-only 链接指向当前页自身归一键：无新状态，不入边不入队
                continue
            if decision.tier == TIER_T2 and absolute is not None \
                    and _origin_of_url(absolute) != _origin_of_url(base_url):
                # 跨域 GET 表单（T2）不会真实提交（_execute_t2_forms 同域
                # 预判），永远不产生真实请求：origin 记入观测集交由
                # _probe_blocked_origins 导航级探测补全拦截证据
                origin = _origin_of_url(absolute)
                if origin:
                    self._blocked_origins_seen[origin] = (
                        self._blocked_origins_seen.get(origin, 0) + 1)
                # 跨域表单目标不入 BFS 队列/不写入边（错误页状态泄漏防线，
                # 与 _execute_t2_forms"同域目标预判"同口径）
                continue
            # 入边 + 入队（edges 幂等；页面跳过判定在 BFS 头部做）。
            # SPA hash-only 分支（§4.2 Note）：absolute 为"同 URL 换 hash"的
            # 目标绝对 URL——goto 它是纯前端路由切换（零网络请求、不经 route、
            # 不产生 blocked/api 记录**属预期**，不是漏采）。
            self._store.insert_edge(EdgeRedacted(
                from_key=current_key, to_key=target_key, via_action=None))
            if absolute is not None:
                self._enqueue(absolute, depth=depth + 1, discover_from=page_id)

    def _resolve_target(
        self,
        el: ElementSignature,
        tier: str,
        base_url: str,
    ) -> Optional[Tuple[str, Optional[str]]]:
        """T1/T2 动作 → ``(目标 url_key, 可 goto 的绝对 URL | None=hash)``。

        Args:
            el: 元素签名。
            tier: T1/T2。
            base_url: 站点入口（相对链接解析基准）。

        Returns:
            tuple | None: 无法定位目标返回 None（动作只记录不入队）。
        """
        if tier == TIER_T1:
            href = (el.href or "").strip()
            if not href:
                return None
            if _is_hash_link(href):
                # SPA hash-only 导航分支（§4.2 Note）：目标 = 当前页 URL 换 hash。
                # goto 该 URL 是纯前端路由切换——零网络请求、不经 route、
                # 不产生 blocked_events/api_observations 记录**属预期**。
                current_absolute = self._current_navigation_base()
                if not current_absolute:
                    return None
                target_url = _apply_hash(current_absolute, href)
                if target_url is None:
                    return None
                return (url_key(target_url), target_url)
            absolute = urljoin(base_url, href)
            if _origin_of_url(absolute) != _origin_of_url(base_url):
                # 双保险：分级器已判非同域，此处再拦一道（防御纵深）
                return None
            return (url_key(absolute), absolute)
        # T2：显式 GET 表单。提交目标 = 表单 action（缺省 = 当前页 URL，HTML 规范）。
        # 中性值不进 url_key / 落库文本：真实 GET 提交由 crawl 在访问该目标时
        # 经 execute_t2_form（fill_neutral 现填现交）完成；api 观测面的 url_path
        # 只有键名化 query（api_observer._path_with_keynames 同口径）。
        action_url = self._form_action_for(el, base_url)
        return (url_key(action_url), action_url)

    def _register_discovered_origin(
        self,
        el: ElementSignature,
        decision: ActionDecision,
        form_ctx: Optional[Dict[str, Any]],
        base_url: str,
    ) -> None:
        """T3 白名单外目标 → 记入 blocked_origins 观测集（发现通道）。

        背景（2026-09-28 e2e 场景[2] blocked_origin=0 根因）：route handler
        只能记录**真实发生过的被拦请求**，而白名单外 <a href> 按红线⑤判 T3
        零点击、永不产生请求——handler 观测集恒空，_probe_blocked_origins
        没有任何探测目标。本方法在分级落库的同一路径上把"页面上真实存在的
        白名单外链接 / 跨域表单 action"origin 登记进同一观测集：计数语义为
        "采集期真实发现的白名单外目标出现次数"，探测面仍受
        :data:`_BLOCKED_ORIGIN_PROBE_LIMIT` 与 origin 去重约束。

        纯内存字典写入 + 纯字符串解析（urljoin/urlsplit），零 Playwright
        API（T3 红线零点击不变——本方法不产生任何导航）。

        Args:
            el: 候选元素签名。
            decision: 该元素的分级结果（表单 action 发现通道的分流依据）。
            form_ctx: 表单上下文（保留入参与 classify_action 调用面同形；
                显式 action 发现已改走签名数据通道 el.form_action/
                formaction，本参数不再参与 action 解析）。
            base_url: 站点入口（同域判定基准 + 相对 href 解析基准）。
        """
        base_origin = _origin_of_url(base_url)
        allowed_norm = {normalize_origin(o)
                        for o in self._cfg.system.allowed_origins if o}
        candidates: List[str] = []
        href = (el.href or "").strip()
        if href and not href.lower().startswith("javascript:"):
            # <a>/<area> 的白名单外 href（T3"非同域链接"分支的主要形态）
            candidates.append(urljoin(base_url, href))
        # 表单 action 发现必须按**显式声明 + 分级结果**双重分流
        # （2026-09-28 e2e 场景[2]误登/挂起双根因修复）：
        # 1) 只用签名数据通道（el.formaction / el.form_action，剪枝期已读
        #    好的结构属性），绝不调用 _form_action_for——该方法对"无显式
        #    action 的控件"按 HTML 规范缺省返回当前文档 URL，且含文档级
        #    DOM 调用：无条件解析会把同域缺省表单误登记为白名单外 origin
        #    （http/https 协议不一致时 base origin 比对失效），悬挂导航期
        #    还会被内核逐控件占位 10s+（分级阶段 40s 白挂根因）；
        # 2) T2 分支不在此登记——同域预判在 _classify_and_dispatch 的 T2
        #    分支单独处理，控件显式 formaction 已由 candidates 的 href/
        #    formaction 通道覆盖；
        # 3) T3 且 is_form_control：只在元素/表单**显式声明** action/
        #    formaction 时登记（未声明=缺省当前页，保守跳过）。
        if el.is_form_control and decision.tier == TIER_T3:
            declared = (el.formaction or el.form_action or "").strip()
            if declared and not declared.lower().startswith("javascript:"):
                candidates.append(urljoin(
                    self._current_navigation_base() or base_url, declared))
        # T3 表单控件的**显式跨域 action**：表单 action 不是元素 href，
        # href 通道覆盖不到（2026-09-28 e2e 场景[2] blocked_origin 发现
        # 通道教训）。缺省 action（未声明）= 当前页，属同域，不登记——
        # 保守过滤避免同域缺省表单误登（协议归一失真时 base 比对失效）。
        for url in candidates:
            origin = _origin_of_url(url)
            # allowed_origins 白名单域不记（route 会放行）；base origin 不记
            if not origin or origin == base_origin:
                continue
            if normalize_origin(origin) in allowed_norm:
                continue
            self._blocked_origins_seen[origin] = (
                self._blocked_origins_seen.get(origin, 0) + 1)

    def _form_action_for(self, el: ElementSignature, base_url: str) -> str:
        """T2 表单控件 → 表单 action 绝对 URL。

        目标解析优先级：
          1. **签名数据通道**（2026-09-28 e2e 场景[2] 40s 挂起根因修复）：
             控件 ``formaction`` > 所属 form 的 ``form_action``——两者都是
             JS 剪枝期（页面活跃时）已读好的**结构属性**（目标地址，与任何
             用户数据无关，零 value 面），绝对化是纯字符串操作；此前逐控件
             走 ``get_attribute``/``evaluate`` 文档级调用，在 T2 提交导航
             悬挂期间会被内核逐条占位 10s+，分级阶段整体白挂 40s；
          2. DOM 兜底（签名未携带 action 信息且控件在 DOM 中仍可即时应答时）：
             经 ``el.selector`` 读 formaction/form.action 单属性，只读结构、
             绝不触碰 value；
          3. 签名 ``href`` 兜底（按钮携带 formaction 时浏览器剪枝可能落入
             href 字段——与显式 action 同语义）；
          4. 均缺失 → 按 HTML 规范退回**当前文档 URL**（``_last_visited_absolute``
             优先、base_url 兜底）：``<form>`` 无 action 属性时提交目标 =
             当前页 URL。

        历史缺陷（2026-09-28 e2e 场景[2]根因之一）：此前只实现规范缺省级，
        ``action="https://example.invalid/gateway"`` 的外域表单被误判为
        同域入队 goto（错误页状态泄漏进 BFS），且 /downloads 的外域导出
        表单、/reports 的外域查询表单等"显式 action"目标全部采错。

        Args:
            el: T2 表单控件签名。
            base_url: 站点入口。

        Returns:
            str: 绝对 action URL（GET 表单提交的目标页）。
        """
        # ① 签名数据通道（零 DOM 调用；控件 formaction 优先 form action，
        # 与 HTML5 语义一致）
        raw_action: Optional[str] = el.formaction or el.form_action
        if not raw_action:
            # ② DOM 兜底：仅签名未携带 action 信息时才问 DOM（同页面活跃
            # 采集期以外的补救通道；异常=文档不可用，退化规范缺省）
            try:
                raw_action = self._page.get_attribute(el.selector, "formaction")
                if not raw_action:
                    raw_action = self._page.evaluate(
                        _JS_FORM_ACTION_OF_CONTROL, el.selector)
            except Exception:  # noqa: BLE001 - DOM 已变/控件消失：退化到规范缺省
                raw_action = None
        if not raw_action:
            raw_action = el.href
        action_text = (raw_action or "").strip()
        if action_text and not action_text.startswith("javascript:"):
            return urljoin(self._current_navigation_base() or base_url, action_text)
        # ③ 无显式 action：HTML 规范缺省 = 当前文档 URL
        return self._current_navigation_base() or base_url

    # ------------------------------------------------------------------
    # 页边界 drain（红线④落库点）
    # ------------------------------------------------------------------

    def _drain_blocked(self) -> None:
        """页边界把拦截队列批量落库 + 刷新 blocked_origins.json（§2.3.6）。

        与 heartbeat 同点调用（crawl 主循环内）。blocked_events 逐条
        ``insert_blocked_event``（元素已在队列 put 时净化）；
        被拦域名汇总写 ``state/blocked_origins.json``（RedactedDict 落盘）。
        """
        drained = self._blocked_queue.drain()
        for event in drained:
            self._store.insert_blocked_event(event)
        self._blocked_db_total += len(drained)
        if self._blocked_origins_seen:
            state_dir = self._cfg.out_dir / self._cfg.system_id / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            payload = redact({
                "blocked_origins": dict(sorted(self._blocked_origins_seen.items())),
                "queue_stats": self._blocked_queue.stats(),
                "updated_at": time.time(),
            })
            with (state_dir / "blocked_origins.json").open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, sort_keys=True, indent=2)

    # ------------------------------------------------------------------
    # 队列辅助
    # ------------------------------------------------------------------

    def _enqueue(self, absolute_url: str, depth: int, discover_from: Optional[int]) -> None:
        """URL 入 BFS 队列（内存去重 + pages 表 pending 登记 + 循环头最终判重）。

        入队即落 pages 行（status='pending'）：frontier 语义 = "已发现未探索"
        的库内可审计集合（§2.3.6 frontier / 第 10 节 c 项 / REQ-SU-019
        resume）。此前节点只进内存队列、消费时才落库——预算截断（budget
        break）时队列剩余永不及落库，frontier 查询恒空，页预算耗尽的
        frontier 声明与 --resume 待采清单双双失去数据基础。
        upsert_page 幂等（UNIQUE url_key），pending 行不覆盖既有进度行
        （done/exploring 状态由消费路径改写）。

        Args:
            absolute_url: 绝对 URL。
            depth: 目标深度。
            discover_from: 父页 page_id（edges/upsert_page 溯源）。
        """
        key = url_key(absolute_url)
        if key in self._enqueued:
            return
        self._enqueued.add(key)
        self._store.upsert_page(PageNodeRedacted(
            url_key=key,
            url=scrub_text(_strip_creds(absolute_url)),
            depth=depth,
            status="pending",
            discover_from=discover_from,
        ))
        self._queue.append((absolute_url, key, depth, discover_from))

    def _requeue_unfinished(self) -> int:
        """resume 队列重建：把 pages 表未采完的行按发现顺序重放进 BFS 队列。

        根因背景（2026-09-29 e2e 场景[4]）：BFS 队列是进程内存对象，
        SIGINT 收口时"已发现未探索"的队列剩余随进程消失，库里只有
        status='pending' 的登记行。--resume 轮若仅入队 start_url，
        start_url 已 done 被幂等跳过 → 队列空 → 采集 0 页秒收口，
        REQ-SU-019"续采 pending 页"语义完全失效。

        口径：
          - 重放非 done 行（pending/error/timeout——exploring 已由
            acquire_lock 重置 pending）；error/timeout 页重试属
            "interrupted 库可 --resume"的既有语义（goto 失败页重采
            路径本就存在，见 crawl 导航异常分支）；
          - 排序 page_id ASC = BFS 发现序（入队即落库，page_id 单调
            即发现序），深度/父子溯源（discover_from）沿用库内值；
          - 去重复用 _enqueue 的 _enqueued 内存集（start_url 若已
            在库中非 done 也不会双入队）；
          - 队列元素 url 用库内 scrub 后 URL（无凭据形态，重采时
            goto 它即可——fixture/真实站点登录态均走 context cookie，
            与首轮采集同一凭据通道）。

        Returns:
            int: 实际重放入队的页面数（不含起点）。
        """
        pending_rows = self._store.unfinished_pages()
        requeued = 0
        for row in pending_rows:
            url_text = str(row["url"] or "")
            if not url_text:
                # 无 URL 的行无法重放导航（理论不可达：_enqueue 落库必带
                # url）——保守跳过并留痕，不静默吞掉
                logger.warning("resume 队列重建跳过无 URL 行 page_id=%s", row["page_id"])
                continue
            before = len(self._enqueued)
            self._enqueue(url_text, depth=int(row["depth"] or 0),
                          discover_from=row["discover_from"])
            if len(self._enqueued) > before:
                requeued += 1
        return requeued

    def _current_navigation_base(self) -> str:
        """当前实际导航基准 URL（hash 拼接用原始 URL 而非归一键）。

        Returns:
            str: 当前页原始绝对 URL（未导航过则 base_url）。
        """
        base = getattr(self, "_last_visited_absolute", "") or ""
        return base or self._cfg.system.base_url

    # crawl 中 goto 后记录的原始 URL（hash 拼接基准）
    _last_visited_absolute = ""


# ---------------------------------------------------------------------------
# 模块级纯函数与 JS 常量
# ---------------------------------------------------------------------------

def _normalize_path(path: str) -> str:
    """URL path 归一（尾斜杠折叠；空串视为 '/'）。

    Args:
        path: urlsplit 产物 path 段（可能为空串）。

    Returns:
        str: 归一后的比较用路径（'/' 或无尾斜杠形态）。
    """
    normalized = (path or "").rstrip("/")
    return normalized or "/"


def redirected_to_login(current_url: str, login_url: str) -> bool:
    """判定当前页面 URL 是否"回跳登录页"（REQ-SU-004.4 会话失效检测）。

    判定口径（2026-09-29 P1-1 修复；可测纯函数，零浏览器依赖）：
      1. **同源前置**：current_url 与 login_url 的 origin
         （scheme://host[:port]）必须相同——不同源（如外链站点的登录页）
         一律 False，绝不误判；
      2. **路径比较**：两边 path 归一（尾斜杠折叠、空 path 视为 '/'）后，
         当前路径 == 登录路径，或当前路径以"登录路径 + '/'"为前缀
         （覆盖 /login 与 /login/sso 之类的子路由形态）；
      3. **SPA hash 路由覆盖**：hash 内容形如 '#/login'、'#/login?x=1'、
         '#!login' 时，取 hash 首段（剥离前导 !// 与尾部 query）按相对
         路径规则与登录路径比较——hash 路由站点回跳登录页时 URL 的 path
         恒为 '/'，真实登录路径藏在 fragment 里。

    query（?next=/dashboard）、fragment 尾段不参与 path 比较；两边为空、
    不可解析（缺 scheme/host）一律 False（保守不误伤正常采集）。

    Args:
        current_url: 浏览器当前页面 URL（page.url 读数）。
        login_url: 配置的登录页 URL（可为相对路径，需已绝对化）。

    Returns:
        bool: True=当前页疑似被重定向回登录页（调用方触发重登流程）。
    """
    current = (current_url or "").strip()
    login = (login_url or "").strip()
    if not current or not login:
        return False
    try:
        current_parts = urlsplit(current)
        login_parts = urlsplit(login)
    except ValueError:
        # 不可解析 URL（如非法端口形态）：保守判非登录回跳
        return False
    # 条件 1：origin 必须同源（_origin_of_url 对缺 scheme/host 返回空串）
    current_origin = _origin_of_url(current)
    if not current_origin or current_origin != _origin_of_url(login):
        return False
    login_path = _normalize_path(login_parts.path)
    # 条件 2：path 段比较（query/fragment 不参与）
    current_path = _normalize_path(current_parts.path)
    if current_path == login_path:
        return True
    if login_path != "/" and current_path.startswith(login_path + "/"):
        return True
    # 条件 3：SPA hash 路由——fragment 首段当相对路径与登录路径比
    fragment = current_parts.fragment or ""
    if fragment:
        # 剥离 hash 前导修饰（'#!/login' → '/login'；'#/login' 保持）
        hash_path = fragment.lstrip("!/")
        # 剥离 hash 内 query（'#/login?next=/x' → '/login'）
        hash_path = hash_path.split("?", 1)[0]
        if not hash_path:
            return False
        # 相对形态（'/login'、'login'）按 base_url 规则绝对化后取 path；
        # 绝对形态（少见：fragment 里挂完整 URL）urljoin 原样返回
        joined = urlsplit(urljoin(login, "/" + hash_path))
        hashed = _normalize_path(joined.path)
        if hashed == login_path:
            return True
        if login_path != "/" and hashed.startswith(login_path + "/"):
            return True
    return False


def _origin_of_url(url: str) -> str:
    """URL → origin 小写文本（scheme://host[:port]；不可解析返回空串）。

    route handler 内可用的**纯字符串操作**（非 Playwright API）。

    Args:
        url: 完整 URL。

    Returns:
        str: origin 文本（不可解析时空串）。
    """
    parts = urlsplit(url or "")
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        return ""
    try:
        port = parts.port
    except ValueError:
        return ""
    if port is not None:
        return "{0}://{1}:{2}".format(scheme, host, port)
    return "{0}://{1}".format(scheme, host)


def _strip_creds(url: str) -> str:
    """剥离 URL 内嵌 user:pass@（落 pages.url 前必经，红线①）。

    与 :func:`su.config.strip_url_credentials` 同口径——route 主线程路径直接
    复用 config 实现（非 handler 内，允许 import 已是模块级）。

    Args:
        url: 原始 URL。

    Returns:
        str: 无凭据 URL。
    """
    from su.config import strip_url_credentials  # 语义集中：红线①入口唯一

    return strip_url_credentials(url)


def _is_hash_link(href: str) -> bool:
    """判定 href 是否 hash-only 链接（SPA 前端路由导航）。

    形态：``#/...``、``#name``，或"同页 + 仅 hash"的绝对 URL。

    Args:
        href: 链接原文。

    Returns:
        bool: True=hash-only。
    """
    if not href:
        return False
    stripped = href.strip()
    if stripped.startswith("#"):
        return True
    parts = urlsplit(stripped)
    # 绝对 URL 且 path 为空/仅 '/'：等价 hash-only 导航
    return bool(parts.fragment) and parts.path in ("", "/")


def _apply_hash(base_url: str, href: str) -> Optional[str]:
    """当前页 URL + hash 链接 → 目标绝对 URL（零网络 SPA 导航分支专用）。

    Args:
        base_url: 当前页绝对 URL（去旧 hash）。
        href: hash 链接原文（'#...' 或绝对 hash URL）。

    Returns:
        str | None: 目标绝对 URL；href 不含有效 hash 时 None。
    """
    stripped = (href or "").strip()
    if stripped.startswith("#"):
        fragment = stripped[1:]
    else:
        fragment = urlsplit(stripped).fragment
    if not fragment:
        return None
    # 去掉当前页旧 fragment 再挂新 fragment（urlunsplit 保证结构正确）
    parts = urlsplit(base_url or "")
    if not parts.scheme or not parts.netloc:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, fragment))


# 下载类扩展名（请求侧启发式；与 §2.3.6 "Content-Disposition 下载"互补）
_DOWNLOAD_EXTENSIONS = (
    ".zip", ".gz", ".tgz", ".bz2", ".7z", ".rar", ".tar",
    ".xls", ".xlsx", ".csv", ".doc", ".docx", ".ppt", ".pptx",
    ".pdf", ".iso", ".dmg", ".exe", ".msi", ".apk",
)


def _server_header_of(response: Any) -> str:
    """从 goto 返回的 Response 取 ``server`` 单键响应头（技术栈指纹源）。

    主线程调用（非 route handler 内，允许 header 读取）。只取 server 一键，
    其余头一律不读——与 api_observer "不落 headers" 的采集最小化口径一致。

    Args:
        response: ``page.goto`` 返回值（可能为 None，如 hash-only 导航）。

    Returns:
        str: server 头值（缺失/异常返回空串）。
    """
    if response is None:
        return ""
    try:
        return str(response.header_value("server") or "")
    except Exception:  # noqa: BLE001 - 头读取失败降级为空（指纹可选字段）
        return ""


def _form_key_for(el: ElementSignature) -> str:
    """T2 去重键：同一 form 的多个控件归并成"一个表单"。

    首选 ``form_selector``（JS 剪枝产出的**所属 form 的 CSS 路径**，同表单
    控件必得同值——去重语义与 HTML 表单一一对应）。

    历史缺陷（2026-09-28 e2e 场景[2]根因）：旧口径按控件自身属性生成键
    （name 优先、id 次之、控件路径兜底），同一表单的 ``input name=q`` /
    ``input name=page``（隐藏域）/ 无 name 的 ``button`` 三控件得到三个
    不同键——"一个表单一份预算"失效，同表单被连续 requestSubmit 多次；
    首次提交导航起飞后，第二控件的文档级调用（query_selector/evaluate）
    被 deactivating 内核挂起，BFS 卡死。

    退化链（仅 ``form_selector`` 缺失的旧签名/脏数据路径使用，宁可少提交
    不误并）：控件 id → 控件全路径。

    Args:
        el: T2 表单控件签名。

    Returns:
        str: 表单去重键。
    """
    if el.form_selector:
        return "form::{0}".format(el.form_selector)
    if el.element_id:
        return "id::{0}".format(el.element_id)
    return "path::{0}".format(el.selector)


def _download_like(url: str) -> bool:
    """请求 URL 是否呈下载形态（path 尾缀命中下载扩展名，query 不参与）。

    Args:
        url: 请求 URL。

    Returns:
        bool: True=下载类请求（route handler 内 abort + 记 download）。
    """
    path = (urlsplit(url or "").path or "").lower()
    return any(path.endswith(ext) for ext in _DOWNLOAD_EXTENSIONS)


def _safe_int(value: Any) -> Optional[int]:
    """宽松 int 转换（JS 返回值容错；失败返回 None）。

    Args:
        value: 原始值。

    Returns:
        int | None: 整数或 None。
    """
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _signature_from_raw(raw: Dict[str, Any]) -> Optional[ElementSignature]:
    """JS 剪枝产物 dict → :class:`ElementSignature`（字段白名单搬运）。

    **不搬运任何 value 字段**（JS 侧也不产出；此处再设一道白名单防线，
    未知键一律丢弃）。

    Args:
        raw: 单条剪枝签名。

    Returns:
        ElementSignature | None: 签名对象；tag 缺失视为脏数据返回 None。
    """
    tag = str(raw.get("tag") or "").strip().lower()
    if not tag:
        return None
    return ElementSignature(
        tag=tag,
        role=_optional_str(raw.get("role")),
        text=_optional_str(raw.get("text")),
        aria_label=_optional_str(raw.get("aria_label")),
        href=_optional_str(raw.get("href")),
        is_form_control=bool(raw.get("is_form_control")),
        form_method=_optional_str(raw.get("form_method")),
        form_text=_optional_str(raw.get("form_text")),
        selector=str(raw.get("selector") or ""),
        input_type=_optional_str(raw.get("input_type")),
        name=_optional_str(raw.get("name")),
        element_id=_optional_str(raw.get("element_id")),
        # 所属 form 的 CSS 路径（T2 去重键；结构属性，无 value 泄露面）
        form_selector=_optional_str(raw.get("form_selector")),
        # form 显式 action / 控件 formaction（结构属性；action 目标解析的
        # 签名数据通道，2026-09-28 e2e 场景[2] 40s 挂起根因修复）
        form_action=_optional_str(raw.get("form_action")),
        formaction=_optional_str(raw.get("formaction")),
    )


def _optional_str(value: Any) -> Optional[str]:
    """宽松转字符串（空串归一 None，非字符串 str() 化）。

    Args:
        value: 原始值。

    Returns:
        str | None: 文本或 None。
    """
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _signature_public_dict(el: ElementSignature) -> Dict[str, Any]:
    """签名的落库展示 dict（element_sig 列数据源；无 value，天然可 redact）。

    Args:
        el: 元素签名。

    Returns:
        dict: 公开展示字段。
    """
    # 注意：form_action / formaction / form_selector / form_text 均**不进**
    # 本展示 dict——element_sig 是 UNIQUE(page_id, element_sig) 去重键与
    # executed 回写匹配键，字段集变更会使既有签名口径漂移（form_* 结构属性
    # 属目标解析面，与"控件身份"无关；与 form_text 排除口径一致）。
    return {
        "tag": el.tag, "role": el.role, "text": el.text,
        "aria_label": el.aria_label, "href": el.href,
        "is_form_control": el.is_form_control, "form_method": el.form_method,
        "selector": el.selector, "input_type": el.input_type,
        "name": el.name, "element_id": el.element_id,
    }


# ---------------------------------------------------------------------------
# 浏览器侧 JS 常量（全部为模块级**文本常量**：零外部输入拼接；
# 只读静态属性/textContent，绝不读取任何表单控件的 value —— §5.1 静态审查项）
# ---------------------------------------------------------------------------

# wait_ready 取样：main/body 文本 + 可交互元素计数
_JS_DOM_SNAPSHOT = """
(() => {
  const main = document.querySelector('main') || document.body;
  const text = main ? (main.textContent || '') : '';
  const interactive = document.querySelectorAll(
    'a[href],button,input,select,textarea,[role=button],[role=link]').length;
  // 文本截 8000 字符：hash 只需要稳定指纹，不需要全文
  return { text: text.slice(0, 8000), interactive: interactive };
})()
"""

# 交互元素剪枝提取（可见性过滤 + 只取交互元素；form 上下文 method/text）。
# 隐私红线：本表达式对 input 仅调用 getAttribute 读取静态声明属性
# （type/name/id），用户输入内容在 DOM 中只存在于动态属性位——不可达。
_JS_EXTRACT_SIGNATURES = """
(() => {
  const MAX = %d;
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
  // CSS 路径生成：id 优先，其次 nth-of-type 链（T2/T3 记录用，稳定可复现）
  const cssPath = el => {
    if (el.id) return '#' + CSS.escape(el.id);
    const seg = [];
    let node = el;
    while (node && node.nodeType === Node.ELEMENT_NODE && seg.length < 6) {
      let part = node.tagName.toLowerCase();
      if (node.parentElement) {
        const same = Array.from(node.parentElement.children)
          .filter(c => c.tagName === node.tagName);
        if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(node) + 1) + ')';
      }
      seg.unshift(part);
      node = node.parentElement;
    }
    return seg.join(' > ');
  };
  const visible = el => !((el.offsetWidth === 0 && el.offsetHeight === 0) ||
                          getComputedStyle(el).visibility === 'hidden');
  const nodes = Array.from(document.querySelectorAll(
    'a[href],button,input,select,textarea,[role=button],[role=link],[onclick]'));
  const out = [];
  for (const el of nodes) {
    if (out.length >= MAX) break;
    if (!visible(el)) continue;
    const tag = el.tagName.toLowerCase();
    const form = el.closest('form');
    const item = {
      tag: tag,
      role: el.getAttribute('role'),
      text: clean(el.innerText || el.textContent).slice(0, 120),
      aria_label: clean(el.getAttribute('aria-label')).slice(0, 120),
      href: (tag === 'a' || tag === 'area') ? el.getAttribute('href') : null,
      is_form_control: ['input','select','textarea','button'].indexOf(tag) !== -1,
      form_method: form ? (form.getAttribute('method') || null) : null,
      // 所属 form 的显式 action 原始声明值（结构属性，与 method 同口径；
      // 2026-09-28 e2e 场景[2] 40s 挂起根因修复——action 目标解析改走
      // 签名数据通道，Python 侧不再逐控件文档级调用）
      form_action: form ? (form.getAttribute('action') || null) : null,
      form_text: form ? clean(form.innerText).slice(0, 300) : null,
      selector: cssPath(el),
      // input 字段全部走 getAttribute 静态声明属性读取（红线口径见上注）
      input_type: tag === 'input' ? (el.getAttribute('type') || 'text') : null,
      name: el.getAttribute('name'),
      element_id: el.getAttribute('id'),
      // 控件自身显式 formaction（HTML5 结构属性，控件级覆盖 form action；
      // getAttribute 读原始声明值，与 href/form_method 同口径零 value 面）。
      // 2026-09-28 e2e 场景[2] 40s 挂起根因修复：action 目标解析改走签名
      // 数据通道（Python 侧零文档级调用），悬挂导航期间不再逐控件问 DOM
      formaction: el.getAttribute('formaction'),
      // 所属 form 的 CSS 路径（T2 去重键数据源，结构属性零业务数据）：
      // form 有 id 用 id 选择器，否则用 body 起的 nth-of-type 链——同表单
      // 的多个控件必然得到同一值。2026-09-28 e2e 场景[2]根因修复：旧去重
      // 键按控件 name/路径生成，同一表单的 input(name=q)+hidden(name=page)
      // +button 各成一键，"一个表单一份预算"失效，同表单被重复提交、
      // 导航后的文档级调用被 deactivating 内核挂起
      form_selector: form ? (form.id ? '#' + CSS.escape(form.id) : cssPath(form)) : null,
    };
    out.push(item);
  }
  return out;
})()
""" % MAX_SIGNATURE_ELEMENTS

# 语义骨架：标题层级（前 80）+ main/body 正文（前 4000 字符）+ 计数
_JS_SEMANTIC_SKELETON = """
(() => {
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
  const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6'))
    .slice(0, 80)
    .map(h => ({ level: Number(h.tagName.slice(1)) || 1,
                 text: clean(h.innerText).slice(0, 200) }));
  const main = document.querySelector('main') || document.body;
  return {
    title: clean(document.title).slice(0, 200),
    headings: headings,
    main_text: main ? clean(main.innerText).slice(0, 4000) : '',
    interactive: document.querySelectorAll(
      'a[href],button,input,select,textarea').length,
  };
})()
"""

# T2 表单提交：按控件 CSS 路径找到其 form 祖先，requestSubmit()。
# 选择器经 evaluate **arg 传参**（不进 JS 字符串拼接，无注入面）；
# 表达式为模块级文本常量（§5.1 静态审查项）。requestSubmit 等价
# 显式提交（触发 submit 事件 + HTML5 校验 + 原生 query 序列化），
# 不存在按钮点击语义——红线⑤"T3 零点击"在本表达式无对应路径。
_JS_SUBMIT_FORM_BY_CONTROL = """
(selector) => {
  const el = document.querySelector(selector);
  if (!el) return false;
  const form = el.closest('form');
  if (!form) return false;
  if (typeof form.requestSubmit === 'function') {
    form.requestSubmit();
  } else {
    form.submit();  // 旧内核退化：同为 GET 序列化提交，仍非按钮点击
  }
  return true;
}
"""

# T2 表单目标解析（_form_action_for）：读控件所属 form 的 action **结构属性**。
# getAttribute 拿原始值（未浏览器绝对化），由 Python 侧 urljoin 统一口径；
# 只读 action 单属性——绝不触碰任何控件 value（§5.1 静态审查项）。
_JS_FORM_ACTION_OF_CONTROL = """
(selector) => {
  const el = document.querySelector(selector);
  if (!el) return null;
  const form = el.closest('form');
  if (!form) return null;
  return form.getAttribute('action');
}
"""

# 技术栈指纹（§2.3.6）：generator 元标签 + 静态资源路径特征词。
# 只读 <meta content> 与 <script src>/<link href> 的**路径特征**，
# 不读任何表单 value / 业务文本（§5.1 静态审查项）。
_JS_TECH_FINGERPRINT = """
(() => {
  const HINTS = ['wp-content', 'wp-includes', '/static/admin', '/django',
                 'django', 'next/static', '_next', 'nuxt', 'vue', 'react',
                 'angular', 'jsp', 'jsf', 'webwork', 'phpmyadmin',
                 '/wordpress', 'drupal', 'joomla', 'typo3'];
  const generator = document.querySelector("meta[name='generator']");
  const urls = [];
  document.querySelectorAll('script[src]').forEach(el => {
    const src = el.getAttribute('src'); if (src) urls.push(src);
  });
  document.querySelectorAll('link[href]').forEach(el => {
    const href = el.getAttribute('href'); if (href) urls.push(href);
  });
  const lower = urls.join('\\n').toLowerCase();
  const hints = HINTS.filter(h => lower.indexOf(h) !== -1);
  return {
    generator: generator ? (generator.getAttribute('content') || null) : null,
    asset_hints: hints,
  };
})()
"""
