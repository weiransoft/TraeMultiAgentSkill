# -*- coding: utf-8 -*-
"""SU 能力单元测试：crawler 层修复语义（2026-09-28/29 e2e 场景[2]/[5]回归锚）。

覆盖 su.site_crawler / su.limiter 的离线可测面（不启动 playwright，
Page/依赖全部用最小 duck-typing 替身驱动被测真实方法，零业务逻辑 mock）：
- _form_key_for：form_selector 协议——同表单控件（input+隐藏域+按钮）必得
  同键（"一个表单一份预算"去重与 HTML 表单一一对应，e2e 场景[2]根因修复）
- BudgetTracker 判定顺序协议：depth_allowed 只读不消耗、consume_page 才
  计数——crawler 主循环以 "depth_allowed 先于 consume_page" 的顺序使用
  两者（e2e 场景[5]根因修复：超深节点不得白扣页预算）
- _t2_wait_navigation_settled：settled 双判据语义——URL 变化即落定 /
  同 URL 经有界 wait_for_load_state 落定（no_navigation 语义）/ 双判据
  耗尽返回 False 收束本轮 T2
- redirected_to_login：回跳登录页判定纯函数（REQ-SU-004.4，P1-1 修复）——
  同源 path 归一比较 / query·hash 变体 / 非同源不误判 / SPA hash 路由
- _goto_and_wait_with_relogin：会话失效自动重登接线——检测回跳 →
  login.relogin_if_needed → 成功则当前页重新 goto 补采本轮（relogin 计数
  生效、补采导航过限速器）；relogin 返回 False → RuntimeError（error 页
  收口）；协作者超限 SuLoginError 原样上抛（致命，不被隔离降级）

运行方式（项目根目录）：
    python3 -B -m unittest scripts.tests.test_su_site_crawler
"""

import sys
import time
import unittest
from pathlib import Path
from typing import List, Optional, Tuple

# 将 scripts/ 目录注入 sys.path，使 `from su.xxx import ...` 生效
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from su.action_tier import ElementSignature  # noqa: E402
from su.config import RunBudget  # noqa: E402
from su.dto import SuLoginError  # noqa: E402
from su.limiter import BudgetTracker, RateLimiter  # noqa: E402
from su.site_crawler import (  # noqa: E402
    SiteCrawler,
    _form_key_for,
    redirected_to_login,
)
from su.wait_ready import WaitReadyOutcome  # noqa: E402


class TestFormKeyProtocol(unittest.TestCase):
    """form_selector 去重协议（2026-09-29 e2e 场景[2]根因修复回归锚）。"""

    def test_same_form_controls_share_key(self):
        """同表单的 input/隐藏域/按钮（form_selector 同值）→ 同一去重键。

        旧口径按控件自身属性生成键（name/id/控件路径），同一表单三控件
        得三个键——"一个表单一份预算"失效，同表单被连续 requestSubmit，
        第二控件的文档级调用被 deactivating 内核挂起，BFS 卡死。
        """
        common = dict(is_form_control=True, form_method="get",
                      form_selector="html > body > form:nth-of-type(1)")
        key_input = _form_key_for(ElementSignature(
            tag="input", name="q", selector="#q", **common))
        key_hidden = _form_key_for(ElementSignature(
            tag="input", name="page", input_type="hidden",
            selector="#page", **common))
        key_button = _form_key_for(ElementSignature(
            tag="button", selector='form button[type="submit"]', **common))
        self.assertEqual(key_input, key_hidden)
        self.assertEqual(key_hidden, key_button)
        self.assertTrue(key_input.startswith("form::"))

    def test_different_forms_distinct_keys(self):
        """不同表单（form_selector 不同值）→ 不同去重键（预算逐表单计量）。"""
        k1 = _form_key_for(ElementSignature(
            tag="input", name="q", form_selector="html > body > form:nth-of-type(1)"))
        k2 = _form_key_for(ElementSignature(
            tag="input", name="q", form_selector="html > body > form:nth-of-type(2)"))
        self.assertNotEqual(k1, k2)

    def test_fallback_chain_without_form_selector(self):
        """form_selector 缺失（旧签名/脏数据）→ 退化链 id → 控件全路径。"""
        by_id = _form_key_for(ElementSignature(
            tag="input", element_id="search-box", selector="#search-box"))
        by_path = _form_key_for(ElementSignature(
            tag="input", selector="html > body > div > input"))
        self.assertEqual(by_id, "id::search-box")
        self.assertEqual(by_path, "path::html > body > div > input")


class TestBudgetOrderProtocol(unittest.TestCase):
    """depth_allowed / consume_page 语义协议（2026-09-29 e2e 场景[5]基础）。"""

    def test_depth_allowed_does_not_consume_pages(self):
        """depth_allowed 纯只读：放行/拒绝均不消耗页预算。"""
        tracker = BudgetTracker(RunBudget(max_pages=3, max_depth=1))
        # 超深节点反复判定不消耗任何预算
        for _ in range(10):
            self.assertFalse(tracker.depth_allowed(2))
        # 深度维度不计 exhausted（节点级跳过规则，REQ-SU-008 AC3）
        self.assertIsNone(tracker.exhausted)
        # 3 页预算仍然完整可用
        self.assertTrue(tracker.consume_page())
        self.assertTrue(tracker.consume_page())
        self.assertTrue(tracker.consume_page())
        self.assertFalse(tracker.consume_page())
        self.assertEqual(tracker.exhausted, BudgetTracker.DIM_PAGES)

    def test_crawl_loop_order_semantics(self):
        """crawl 主循环协议复现：深度判定先于页预算消费。

        模拟 --max-pages 3 --max-depth 1 + 深度 2 节点混排队列：旧顺序
        （先 consume 后判深）下超深节点白扣页预算，done 页远小于预算声明
        值；新顺序下 3 页预算全部留给深度合规节点。
        """
        tracker = BudgetTracker(RunBudget(max_pages=3, max_depth=1))
        queue_depths = [0, 1, 2, 2, 2, 1, 1, 1]
        explored = 0
        for depth in queue_depths:
            if not tracker.depth_allowed(depth):
                continue  # 超深：节点级跳过，零预算消耗
            if not tracker.consume_page():
                break
            explored += 1
        # 深度合规节点共 5 个（0/1 层），预算 3 → 恰好探索 3 页
        self.assertEqual(explored, 3)
        # 超深节点没有蚕食页预算：探索数 = 预算声明值（旧顺序只有 1 页）
        self.assertEqual(tracker.summary().pages_consumed, 3)


class _FakeSettledPage:
    """_t2_wait_navigation_settled 所需最小 Page 面替身。

    只实现被测方法实际使用的两个调用面：url 属性（driver 会话缓存读数
    语义）与 wait_for_load_state（文档级等待，可配置为落定/超时抛错），
    其余一律不提供——被测方法若使用其它调用面会立即 AttributeError 显形。
    """

    def __init__(self, url, load_settles=True, load_timeout_raises=False):
        self.url = url
        self._load_settles = load_settles      # wait_for_load_state 是否落定
        self._load_timeout_raises = load_timeout_raises  # 模拟卡死导航抛错
        self.load_calls = 0                    # 文档级调用次数（时序断言用）

    def wait_for_load_state(self, state, timeout):
        """有界文档完结等待（sync Playwright 语义替身）。"""
        self.load_calls += 1
        if self._load_timeout_raises:
            # 卡死导航：内核占位至 timeout 抛错
            raise TimeoutError("wait_for_load_state timeout")
        if not self._load_settles:
            raise RuntimeError("document unavailable")
        return None


class _NavigatingPage(_FakeSettledPage):
    """url 在第 N 次读取后"变化"的替身（模拟轮询窗口内导航落定）。

    sync Playwright 的 page.url 是 driver 会话缓存读数——每次读取都是
    一次属性访问；用读取计数模拟"导航恰在第 2 轮轮询间完成"，验证
    URL 判据命中即返回、全程零文档级调用。
    """

    def __init__(self, url_before, url_after, flip_after_reads=1):
        # 不经父类 __init__（其 self.url= 赋值与 url property 冲突）：
        # 本替身用 _url_before/_url_after 承载 URL 面，其余字段手工初始化
        self._url_before = url_before
        self._url_after = url_after
        self._load_settles = True
        self._load_timeout_raises = False
        self.load_calls = 0
        self._flip_after_reads = flip_after_reads
        self._reads = 0

    # url 以 property 覆盖父类实例属性（描述符优先级高于实例 __dict）
    @property
    def url(self):
        """第 flip_after_reads 次读取后返回新 URL（导航完成缓存更新语义）。"""
        self._reads += 1
        if self._reads > self._flip_after_reads:
            return self._url_after
        return self._url_before


def _make_crawler_for_settled(page, page_timeout_ms=2000):
    """构造仅注入 page/budget 配置的最小 crawler（不经 __init__）。

    被测方法 :meth:`_t2_wait_navigation_settled` 的依赖面只有
    ``self._page`` 与 ``self._cfg.budget.page_timeout_ms``——用
    ``__new__`` 绕开 __init__ 的 playwright/StateStore 装配，属被测
    方法真实代码路径（非 mock：方法体本身完整真实执行）。
    """

    class _Cfg:
        """预算配置替身（仅提供被测方法读取的 page_timeout_ms）。"""

        class budget:  # noqa: N801 - 对齐 SuConfig.budget 属性形态
            pass

    cfg = _Cfg()
    cfg.budget.page_timeout_ms = page_timeout_ms
    crawler = SiteCrawler.__new__(SiteCrawler)
    crawler._page = page
    crawler._cfg = cfg
    return crawler


class TestT2NavigationSettled(unittest.TestCase):
    """settled 双判据语义（2026-09-29 e2e 场景[2]根因修复回归锚）。"""

    def test_url_change_returns_true(self):
        """判据一：URL ≠ 提交前值 → 导航落定 True（不触文档级调用）。"""
        # 第 2 次读取时 URL 变化：模拟导航在轮询第二轮间完成
        page = _NavigatingPage(
            url_before="https://a.test/search?q=test",
            url_after="https://a.test/results?q=test",
            flip_after_reads=1)
        crawler = _make_crawler_for_settled(page, page_timeout_ms=1000)
        settled = crawler._t2_wait_navigation_settled(
            "https://a.test/search?q=test")
        self.assertTrue(settled)
        # URL 判据先命中：全程零文档级调用（deactivating 期安全）
        self.assertEqual(page.load_calls, 0)

    def test_same_url_load_settles_returns_true(self):
        """判据二：URL 恒等但 load 落定 → 同 URL 完成导航 True。

        302 回同页 / 200 同 URL / 表单回显当前页形态：导航真实完结但
        page.url 永不变，URL 判据耗尽后 wait_for_load_state('load')
        立即返回即"文档完结"直接信号（no_navigation 语义——表单确实
        提交过、executed 计数照常，调用方收束本轮 T2 后全程有界）。
        """
        page = _FakeSettledPage("https://a.test/search?q=test",
                                load_settles=True)
        # 窗口取最小（1s）：URL 轮询耗满后快速进入第二判据
        crawler = _make_crawler_for_settled(page, page_timeout_ms=1000)
        started = time.monotonic()
        settled = crawler._t2_wait_navigation_settled("https://a.test/search?q=test")
        self.assertTrue(settled)
        self.assertEqual(page.load_calls, 1)  # 恰好一次有界文档级调用
        # 有界性：总耗时 ≤ URL 窗口(1s) + 尾窗(1s) + 调度余量
        self.assertLess(time.monotonic() - started, 3.0)

    def test_stuck_navigation_returns_false(self):
        """双判据耗尽（卡死导航）→ False 收束本轮 T2，代价恒有界。"""
        page = _FakeSettledPage("https://a.test/search?q=test",
                                load_timeout_raises=True)
        crawler = _make_crawler_for_settled(page, page_timeout_ms=1000)
        started = time.monotonic()
        settled = crawler._t2_wait_navigation_settled("https://a.test/search?q=test")
        self.assertFalse(settled)  # no_navigation：收束本轮（BFS 存活靠下轮 goto 抢占）
        self.assertEqual(page.load_calls, 1)
        # URL 窗口 1s + load 尾窗 1s（timeout 替身即刻抛）+ 余量
        self.assertLess(time.monotonic() - started, 3.0)


# ---------------------------------------------------------------------------
# REQ-SU-004.4（P1-1）：回跳登录页判定 + 会话失效自动重登接线
# ---------------------------------------------------------------------------

class TestRedirectedToLogin(unittest.TestCase):
    """redirected_to_login 纯函数多分支（同源/路径/查询/hash/非同源）。"""

    def test_same_path_true(self):
        """同源同路径（含尾斜杠/大小写主机形态）→ True。"""
        self.assertTrue(redirected_to_login(
            "http://127.0.0.1:8000/login", "http://127.0.0.1:8000/login"))
        # 尾斜杠归一：/login/ == /login
        self.assertTrue(redirected_to_login(
            "http://a.test/login/", "http://a.test/login"))
        # 主机大小写归一（origin 小写比较）
        self.assertTrue(redirected_to_login(
            "http://A.Test:8080/login", "http://a.test:8080/login"))
        # 登录路径子路由前缀（/login/sso 属登录域）
        self.assertTrue(redirected_to_login(
            "http://a.test/login/sso", "http://a.test/login"))

    def test_with_query_true(self):
        """带 query 的回跳（?next=/dashboard）→ True（query 不参与比较）。"""
        self.assertTrue(redirected_to_login(
            "http://a.test/login?next=%2Fdashboard", "http://a.test/login"))
        # 登录 URL 自身带 query 也同样归一比较
        self.assertTrue(redirected_to_login(
            "http://a.test/login?x=1", "http://a.test/login?from=crawler"))

    def test_other_path_false(self):
        """同域非登录路径 → False（正常采集页不误判）。"""
        self.assertFalse(redirected_to_login(
            "http://a.test/dashboard", "http://a.test/login"))
        # 前缀相似但非子路由（/loginfoo 不是 /login/ 的扩展）不误判
        self.assertFalse(redirected_to_login(
            "http://a.test/loginfoo", "http://a.test/login"))

    def test_different_origin_false(self):
        """非同源（协议/主机/端口任一不同）→ False，绝不误判。"""
        # 不同主机
        self.assertFalse(redirected_to_login(
            "https://other.test/login", "http://a.test/login"))
        # 不同端口
        self.assertFalse(redirected_to_login(
            "http://a.test:9999/login", "http://a.test:8080/login"))
        # 不同协议
        self.assertFalse(redirected_to_login(
            "https://a.test/login", "http://a.test/login"))

    def test_empty_and_invalid_false(self):
        """空串 / 不可解析 URL → False（保守不误伤）。"""
        self.assertFalse(redirected_to_login("", "http://a.test/login"))
        self.assertFalse(redirected_to_login("http://a.test/login", ""))
        self.assertFalse(redirected_to_login("not a url", "http://a.test/login"))
        # 缺 scheme/host 的相对路径不构成同源比较
        self.assertFalse(redirected_to_login("/login", "/login"))

    def test_spa_hash_routes(self):
        """SPA hash 路由：path 恒为 '/'、登录路径藏在 fragment → True。"""
        # '#/login' 形态
        self.assertTrue(redirected_to_login(
            "http://a.test/#/login", "http://a.test/login"))
        # hash 内带 query（'#/login?next=/x'）
        self.assertTrue(redirected_to_login(
            "http://a.test/#/login?next=%2Fhome", "http://a.test/login"))
        # angular '#!/login' 形态
        self.assertTrue(redirected_to_login(
            "http://a.test/#!/login", "http://a.test/login"))
        # hash 指向其它前端路由（#/dashboard）不误判
        self.assertFalse(redirected_to_login(
            "http://a.test/#/dashboard", "http://a.test/login"))
        # 非同源的 hash 回跳同样不误判
        self.assertFalse(redirected_to_login(
            "https://other.test/#/login", "http://a.test/login"))


class _FakeReloginPage:
    """重登接线测试的 Page 替身：goto 按脚本回放落地 URL。

    只实现被测路径实际用到的调用面（goto / url 读数）；url 由最近一次
    goto 的落地 URL 决定（脚本耗尽后恒为最后一项），语义与真实 driver
    会话缓存一致——302 链的最终落地页就是 goto 完成后的 page.url。
    """

    def __init__(self, landings: List[str]) -> None:
        """初始化。

        Args:
            landings: 每次 goto 的落地 URL 脚本（如先 '/orders' 被 302 回
                '/login'，重登后重新 goto 落回 '/orders'）。
        """
        self._landings = list(landings)
        self.goto_calls: List[str] = []   # 每次 goto 的目标 URL（断言重goto用）

    def goto(self, url: str, **_kwargs) -> None:
        """回放脚本：消费一个落地 URL 并记录 goto 目标。"""
        self.goto_calls.append(url)
        if self._landings:
            self._landing = self._landings.pop(0)

    @property
    def url(self) -> str:
        """最近一次 goto 的落地 URL（脚本耗尽后保持末值）。"""
        return getattr(self, "_landing", "about:blank")


class _FakeRelogin:
    """BrowserLogin 协作者替身：按脚本回放 relogin_if_needed 返回值。

    被测代码路径只消费 relogin_if_needed 的三种契约结果——True（重登成功）/
    False（单次失败）/ 抛 SuLoginError（超限）——本替身逐次回放脚本并
    记录调用参数，替代真实 playwright 登录流程（契约替身，非业务 mock：
    被测方法自身的检测/重goto/计数/限速逻辑全部真实执行）。
    """

    def __init__(self, results: List[Optional[bool]]) -> None:
        """初始化。

        Args:
            results: 逐次返回值脚本；元素为异常实例时该次直接抛出。
        """
        self._results = list(results)
        self.calls: List[Tuple[object, object]] = []   # (page, context) 入参

    def relogin_if_needed(self, page: object, context: object) -> bool:
        """按脚本回放一次重登结果并记录调用面。"""
        self.calls.append((page, context))
        if not self._results:
            raise AssertionError("relogin_if_needed 调用超出脚本长度")
        item = self._results.pop(0)
        if isinstance(item, BaseException):
            raise item
        return bool(item)


def _make_relogin_crawler(page, landings, login_results, page_timeout_ms=1000):
    """构造注入 page/limiter/login 的最小 crawler（不经 __init__）。

    被测方法 :meth:`SiteCrawler._goto_and_wait_with_relogin` 依赖面：
    _page/_login/_context/_limiter/_interrupt_requested/_cfg.system
    （base_url/login_url）与 _wait_ready。其中 _goto_and_wait、_wait_ready
    用桩固定"就绪恒稳定"（被测对象是重登接线协议本身，非就绪算法），
    其余（回跳检测、协作者调用、重 goto、限速、防御环）全走真实代码。

    Args:
        page: 测试用 Page 替身（goto 回放落地 URL）。
        landings: page 的 goto 落地脚本。
        login_results: login 协作者的 relogin 返回脚本（None=不注入协作者）。
        page_timeout_ms: 预算配置替身值。

    Returns:
        tuple: (crawler, login_stub_or_None)——crawler 已装配完毕可直接调用
            被测方法；login_stub 供断言 relogin 调用次数与入参。
    """
    page._landings = list(landings)

    class _SystemCfg:
        """system 段配置替身（提供 base_url / login_url 两个文本）。"""

        base_url = "http://site.test"
        login_url = "/login"

    class _Cfg:
        """完整配置替身（仅提供被测路径读取的 system 段）。"""

        system = _SystemCfg()

    crawler = SiteCrawler.__new__(SiteCrawler)
    crawler._page = page
    crawler._cfg = _Cfg()
    crawler._login = _FakeRelogin(login_results) if login_results is not None else None
    crawler._context = object()          # 协作者入参身份断言用哨兵对象
    crawler._limiter = RateLimiter(0)    # delay=0：不限速但真实走 wait 通道
    crawler._relogin_retried = 0
    crawler._interrupt_requested = _NeverInterrupted()
    # _goto_and_wait 桩：goto 仍真实驱动 page 替身（302 回跳通过落地 URL
    # 反映到 page.url，回跳检测走真实 while 判定），只把与 wait_ready
    # 内核无关的就绪判定钉为恒稳定（被测语义：重登后 outcome 来自
    # **补采导航**）
    def _goto_stub(url):
        page.goto(url, wait_until="domcontentloaded", timeout=page_timeout_ms)
        return WaitReadyOutcome(stable=True, reason="stable", rounds_used=1)

    crawler._goto_and_wait = _goto_stub
    return crawler, crawler._login


class _NeverInterrupted:
    """threading.Event 的最小只读替身（is_set 恒 False——非中断场景）。"""

    def is_set(self) -> bool:
        """恒 False：被测路径的中断短路不触发。"""
        return False


class TestReloginWiring(unittest.TestCase):
    """_goto_and_wait_with_relogin 重登接线协议（REQ-SU-004.4 P1-1 修复锚）。"""

    def test_no_login_injected_returns_outcome_untouched(self):
        """未注入 login：回跳照常返回就绪结果（离线/无凭据形态零行为变化）。"""
        page = _FakeReloginPage(["http://site.test/login"])
        crawler, _ = _make_relogin_crawler(page, ["http://site.test/login"], None)
        outcome = crawler._goto_and_wait_with_relogin("http://site.test/orders")
        self.assertTrue(outcome.stable)
        # 只发生一次 goto（未触发任何重登补采）
        self.assertEqual(page.goto_calls, ["http://site.test/orders"])

    def test_relogin_success_re_goes_current_page(self):
        """回跳 → relogin 成功 → 当前页重新 goto 补采本轮（计数生效）。"""
        # 第一次 goto 落回登录页；重登成功后补采 goto 落回订单页
        page = _FakeReloginPage([
            "http://site.test/login",
            "http://site.test/orders",
        ])
        crawler, login = _make_relogin_crawler(
            page,
            ["http://site.test/login", "http://site.test/orders"],
            [True],
        )
        outcome = crawler._goto_and_wait_with_relogin("http://site.test/orders")
        # 断言 1：当前页被**重新 goto**（第二次 goto 目标 = 原目标 URL）
        self.assertEqual(page.goto_calls,
                         ["http://site.test/orders", "http://site.test/orders"])
        # 断言 2：relogin 调用计数生效（恰好一次，且 page/context 注入正确）
        self.assertEqual(len(login.calls), 1)
        self.assertIs(login.calls[0][0], crawler._page)
        self.assertIs(login.calls[0][1], crawler._context)
        # 断言 3：防御环计数随重登成功递增；补采导航落回业务页即收口
        self.assertEqual(crawler._relogin_retried, 1)
        # 断言 4：outcome 来自补采导航（恒稳定桩）
        self.assertTrue(outcome.stable)

    def test_relogin_returns_false_raises_runtime_error(self):
        """relogin 返回 False（额度未用尽的单次失败）→ RuntimeError 收口。

        crawl 主循环既有 error 页降级路径按 navigate_failed 记录并继续
        BFS——会话反复失效的最终判定在协作者内部（超限抛 SuLoginError）。
        """
        page = _FakeReloginPage(["http://site.test/login"])
        crawler, login = _make_relogin_crawler(
            page, ["http://site.test/login"], [False])
        with self.assertRaises(RuntimeError):
            crawler._goto_and_wait_with_relogin("http://site.test/orders")
        # 失败后不得补采导航（页面还停在登录页，goto 只会再次回跳）
        self.assertEqual(page.goto_calls, ["http://site.test/orders"])
        self.assertEqual(len(login.calls), 1)

    def test_relogin_exceeded_propagates_su_login_error(self):
        """协作者超限抛 SuLoginError → 原样上抛（致命，exit 4 语义）。

        被测点：crawler 的重登调用路径不存在任何捕获 SuLoginError 的
        except 面——异常穿到 CLI 收口（run() 按 exit_code=4 退出），
        绝不落入"透镜失败隔离"被降级为 ui/api failed 继续跑。
        """
        page = _FakeReloginPage(["http://site.test/login"])
        crawler, login = _make_relogin_crawler(
            page, ["http://site.test/login"],
            [SuLoginError("会话反复失效：重登 3 次仍被回跳登录页")])
        with self.assertRaises(SuLoginError) as ctx:
            crawler._goto_and_wait_with_relogin("http://site.test/orders")
        self.assertEqual(ctx.exception.exit_code, 4)
        # 超限异常同样不得触发补采导航
        self.assertEqual(page.goto_calls, ["http://site.test/orders"])
        self.assertEqual(len(login.calls), 1)

    def test_persistent_redirect_guard_ring(self):
        """防御环：重登恒"成功"但补采仍回跳 → 超限抛 SuLoginError。

        病态服务端形态（登录后仍永远 302 回登录页）下 goto↔relogin
        循环被钉死在 LOGIN_MAX_RETRY=3 次补采以内——第 4 次补采导航
        发起前（已重登 4 次仍回跳）抛 SuLoginError，不产生无限循环。
        """
        # 全部 goto 恒落回登录页（重登"成功"也不改变事实）
        page = _FakeReloginPage(["http://site.test/login"] * 10)
        crawler, login = _make_relogin_crawler(
            page, ["http://site.test/login"] * 10, [True, True, True, True])
        with self.assertRaises(SuLoginError):
            crawler._goto_and_wait_with_relogin("http://site.test/orders")
        # 防御环上界：relogin 至多 4 次（第 4 次成功后进入补采前超限抛出，
        # 与 LOGIN_MAX_RETRY=3 同口径——最多允许 3 次补采验证）
        self.assertEqual(len(login.calls), 4)
        # 初次 goto + 3 次补采 goto（每次重登成功各补采一轮，第 4 轮不补采）
        self.assertEqual(len(page.goto_calls), 4)
        self.assertEqual(crawler._relogin_retried, 4)


if __name__ == "__main__":
    unittest.main()
