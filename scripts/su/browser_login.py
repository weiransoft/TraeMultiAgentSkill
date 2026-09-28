"""自动登录（架构 ARCH-SU-001 §2.3.5 / §4.1 时序，PRD REQ-SU-004）。

**职责**：打开登录页 → 显式选择器或启发式定位表单 → 填写提交 → 三判据判定
登录结果；``--storage-state`` 人工已登录态旁路注入；storage_state 0600 落盘；
crawler 检测到回跳登录页时的重登（≤3 次，超限 :class:`SuLoginError` exit 4）。

安全口径：
  - 凭据只以 :class:`su.dto.SensitiveStr` 形态在内存持有，**仅在 fill 边界**
    经 ``.reveal()`` 落地（§5.1 reveal 白名单位置之一）；
  - 疑似验证码 / 2FA（页面含 captcha/otp 特征且判定失败）→ 直接终止并提示
    ``--storage-state`` 旁路，**绝不尝试破解**（PRD OUT-4）；
  - 判定依据写 ``state/login_session.json``（RedactedDict 落盘）；
    ``state/storage_state.json`` 副本 ``os.chmod(0o600)``（NFR-SU-003 / §5.5）；
  - 注入人工态：先把源文件**复制**进 state/ 并对副本 chmod 0600
    （源文件不动、不改权限，§5.5）；副本严禁进入 snapshots/understanding.json。

**不顶层 import playwright**：pw/page/context 对象全部由编排层注入
（模块对象来自 :class:`su.deps.DependencyReport`），类型注解仅在
``typing.TYPE_CHECKING`` 下引用（REQ-SU-021）。
"""

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional
from urllib.parse import urlsplit

from su.config import LoginSelectors, SuConfig, redact, scrub_text
from su.dto import RedactedDict, SuLoginError
from su.limiter import RateLimiter

if TYPE_CHECKING:  # 仅类型注解引用（字符串注解 + TYPE_CHECKING 双保险）
    from playwright.sync_api import BrowserContext, Page, sync_playwright

__all__ = ["LOGIN_MAX_RETRY", "LoginOutcome", "BrowserLogin"]

# 会话反复失效重登上限（§2.3.5 常量口径，REQ-SU-004 AC4）
LOGIN_MAX_RETRY = 3

# 登录结果判定超时（毫秒）——三判据在此窗口内轮询
_LOGIN_JUDGE_TIMEOUT_MS = 15000
# 判定轮询步进（毫秒）
_LOGIN_JUDGE_POLL_MS = 500

# storage_state 落盘文件名（state/ 下，敏感文件清单一员，§5.5）
_STORAGE_STATE_FILENAME = "storage_state.json"
# 登录判定依据落盘文件名（脱敏后写 state/login_session.json）
_LOGIN_SESSION_FILENAME = "login_session.json"

# 验证码 / 2FA 特征（URL 形态 + 页面文本/DOM 特征；命中且判定失败 → 终止提示旁路）
_CAPTCHA_URL_HINTS = ("captcha", "recaptcha", "geetest", "tcaptcha", "2fa", "two_factor", "otp")
_CAPTCHA_TEXT_HINTS = (
    "验证码", "滑块", "图形验证", "人机验证", "短信验证", "动态口令", "双重验证",
    "captcha", "recaptcha", "hcaptcha", "verify you are human", "one-time",
    "enter the code", "verification code",
)
_CAPTCHA_SELECTOR = (
    "img[src*='captcha'],iframe[src*='captcha'],iframe[src*='recaptcha'],"
    "iframe[src*='hcaptcha'],div[class*='captcha'],input[name*='captcha'],"
    "input[name*='otp'],input[name*='verify_code'],input[autocomplete='one-time-code']"
)

# 会话 cookie 判定：登录前快照与判定时快照的差集即"新增会话 cookie"。
# 名称含以下片段者视为会话 cookie（高置信）；其余新增 name=value 长度达阈值也计入。
_SESSION_COOKIE_HINTS = ("session", "sess", "sid", "token", "auth", "jwt", "jsessionid", "phpsessid")
_SESSION_COOKIE_MIN_VALUE_LEN = 12

# 启发式表单定位的候选选择器（§2.3.5：password 框同 form 最近文本框 + submit 按钮）
_HEURISTIC_PASSWORD_SELECTOR = (
    "form input[type='password'], input[type='password']"
)


@dataclass
class LoginOutcome:
    """登录结果（§2.3.4 数据结构，落 state/login_session.json）。

    Attributes:
        success: 是否登录成功。
        judged_by: 判定依据（架构 Literal 口径）：``success_hint`` /
            ``url_left_login`` / ``session_cookie`` / ``injected_state``；
            失败时为 ``failed``。
        detail: 中文判定依据说明（scrub 后落盘，绝不回显凭据）。
    """

    success: bool
    judged_by: str
    detail: str

    def to_redacted(self) -> RedactedDict:
        """转出参 RedactedDict（redact 管线产物，落盘唯一合法形态）。

        Returns:
            RedactedDict: 登录结果出参。
        """
        return redact({
            "success": self.success,
            "judged_by": self.judged_by,
            "detail": scrub_text(self.detail),
            "ts": time.time(),
        })


class BrowserLogin:
    """自动登录器（§2.3.5 全量方法，REQ-SU-004）。

    典型接线（CLI 编排层，§4.1 时序）::

        pw = deps.require_playwright()          # 缺失 → SuDepsError exit 5
        login = BrowserLogin(cfg, pw, limiter)
        with pw.sync_playwright().start() as p:
            browser = p.chromium.launch(headless=not cfg.headed)
            context = browser.new_context()
            outcome = login.login(page, context)
    """

    def __init__(self, cfg: SuConfig, pw: "sync_playwright", limiter: RateLimiter) -> None:
        """初始化登录器。

        Args:
            cfg: 完整运行配置（system 段凭据 / login_url / success_hint /
                storage_state_path 旁路）。
            pw: playwright 模块对象（DependencyReport 注入；本类只用其
                ``sync_playwright`` 入口与错误类型，不顶层 import）。
            limiter: 全局限速器——登录动作也是对外请求，必须过同一阀门
                （NFR-SU-001：任何循环不得绕过）。
        """
        self._cfg = cfg
        self._pw = pw
        self._limiter = limiter
        # relogin 计数（crawler 每检测一次回跳登录页 +1；超限抛 SuLoginError）
        self._relogin_attempts = 0
        # 登录前 cookie 名称快照（session_cookie 判据的比对基准）
        self._cookie_baseline: Optional[set] = None
        # 同名重新签发判据的提交前值快照（_auto_login 填表前刷新；
        # wait_login_result 据此在重登场景识别"同名不同值"的新会话）
        self._session_reissue_pre_submit: dict = {}
        # wait_login_result 生效的同名值基线（由提交前快照过滤会话名得到）
        self._session_reissue_baseline: dict = {}

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def login(self, page: "Page", context: "BrowserContext") -> LoginOutcome:
        """执行登录（§4.1 时序：storage_state 旁路优先，否则自动登录）。

        Args:
            page: 已创建的 Playwright Page（编排层持有，单 Page 串行 AP-6）。
            context: 浏览器上下文（storage_state 存取面）。

        Returns:
            LoginOutcome: 成功结果（judged_by 说明依据）。

        Raises:
            SuLoginError: 自动登录失败（退出码 4）——报错文案二分：
                疑似验证码/2FA → 提示 ``--storage-state`` 旁路（不破解）；
                否则提示疑似凭据错误。
        """
        if self._cfg.storage_state_path is not None:
            # 旁路分支：注入人工已登录态并验证（AC3）
            outcome = self.inject_storage_state(context)
            self._wait_and_verify_injected(page)
            self._write_login_session(outcome)
            return outcome
        return self._auto_login(page, context)

    def _auto_login(self, page: "Page", context: "BrowserContext") -> LoginOutcome:
        """自动登录主流程（goto → 定位表单 → fill+click → 三判据判定）。

        Args:
            page: Playwright Page。
            context: 浏览器上下文（cookie 快照面）。

        Returns:
            LoginOutcome: 成功结果。

        Raises:
            SuLoginError: 判定失败（含表单定位失败、验证码特征、凭据疑似错误）。
        """
        system = self._cfg.system
        login_url = self._absolute_login_url()

        # cookie 基线快照（session_cookie 判据的比对起点；快照失败不致命，判据降级）
        self._cookie_baseline = self._cookie_names(context)
        # 同名重新签发判据的**提交前**值快照（REQ-SU-004.4 重登补判据）：
        # 必须在填表提交之前取——此刻上下文携带的是待失效/已撤销的旧会话
        # token；POST 重签的新 token 同名不同值，wait_login_result 判据 3
        # 据此命中 session_cookie。首次登录上下文无会话 cookie，快照为空，
        # 判据 3 走"新增名称"原路径（零行为变化）。
        self._session_reissue_pre_submit = self._session_cookie_values(context)

        self._limiter.wait("login")
        page.goto(login_url, wait_until="domcontentloaded",
                  timeout=self._cfg.budget.page_timeout_ms)

        # 表单定位：显式选择器优先，缺失项走启发式（§2.3.5）
        selectors = self.detect_form_heuristically(page)

        # 填写边界：reveal() 只出现在下面两行 fill 参数位（§5.1 白名单）
        username_input = page.query_selector(selectors.username)
        password_input = page.query_selector(selectors.password)
        if username_input is None or password_input is None:
            raise SuLoginError(
                "登录页无法定位账号/密码输入框（显式选择器与启发式均未命中）",
                hints=[
                    "配置 system.login_selectors 显式指定 username/password/submit 选择器",
                    "或改用 --storage-state 注入人工已登录态（不破解验证码/2FA）",
                ],
            )
        username_input.fill(system.username.reveal())   # reveal 边界①：登录填表
        password_input.fill(system.password.reveal())   # reveal 边界①：登录填表

        submit = page.query_selector(selectors.submit) if selectors.submit else None
        if submit is not None:
            submit.click()
        else:
            # 启发式兜底未找到按钮时在密码框回车提交（常见表单行为，等价点击 submit）
            password_input.press("Enter")

        # 三判据判定
        judged_by = self.wait_login_result(page, context)
        if judged_by is not None:
            detail = "登录成功（判据：{0}）".format(judged_by)
            outcome = LoginOutcome(success=True, judged_by=judged_by, detail=detail)
            self.save_storage_state(context)
            self._write_login_session(outcome)
            return outcome

        # 判定失败：先分诊验证码/2FA（终止提示旁路，绝不破解）
        if self._looks_like_captcha(page):
            raise SuLoginError(
                "登录失败且页面存在验证码/2FA 特征：本能力不尝试破解（OUT-4），"
                "请人工在浏览器完成登录后导出 storage_state 并以 --storage-state 注入",
                hints=["用法：--storage-state <path>（注入前自动复制进 state/ 并 chmod 0600）"],
            )
        raise SuLoginError(
            "登录失败：三判据（success_hint/离开登录路径/会话 Cookie 新增）均未满足，"
            "疑似凭据错误或登录接口异常",
            hints=[
                "核对 system.username/password（或 SU_SYSTEM_USERNAME/SU_SYSTEM_PASSWORD 环境变量）",
                "配置 system.login_selectors 与 system.success_hint 提高判定精度",
                "如目标启用验证码/2FA，请改用 --storage-state 旁路",
            ],
        )

    # ------------------------------------------------------------------
    # 表单定位与判定
    # ------------------------------------------------------------------

    def detect_form_heuristically(self, page: "Page") -> LoginSelectors:
        """启发式定位登录表单（§2.3.5；显式选择器逐字段优先覆盖）。

        启发式口径：
          - password 框：``input[type=password]``（页面级首个）；
          - username 框：同 form 内、password 之前最近的 text/email/tel 输入框；
            无 form 时取页面级 password 之前最近的同类框；
          - submit：form 内 ``button[type=submit]`` / ``input[type=submit]``；
            再无则退回回车提交（返回 None，由调用方按 None 处理）。

        实现说明：定位逻辑走 Playwright **locator 链 + count/nth**（结构化查询），
        不注入 JS 读任何 value（隐私红线与 extract_signature 同口径）。

        Args:
            page: 已打开登录页的 Page 对象。

        Returns:
            LoginSelectors: 生效选择器三元组（username/password 必非 None
                ——无法定位时返回启发式候选，由 fill 前的存在性检查兜底报错）。
        """
        configured = self._cfg.system.login_selectors
        password_selector = configured.password or _HEURISTIC_PASSWORD_SELECTOR

        username_selector = configured.username
        if username_selector is None:
            username_selector = self._heuristic_username_selector(page)

        submit_selector = configured.submit
        if submit_selector is None:
            submit_selector = self._heuristic_submit_selector(page)

        return LoginSelectors(
            username=username_selector,
            password=password_selector,
            submit=submit_selector,
        )

    def _heuristic_username_selector(self, page: "Page") -> str:
        """启发式推导账号输入框 CSS 选择器。

        策略：取 password 框的 form 祖先（:has 组合），在其中定位 password
        之前的文本类输入框。CSS 无法表达"之前最近"，采用 **evaluate 结构化
        查询**：只读 name/id/type/placeholder 等静态属性推导 CSS 候选（
        优先 ``input[name=...]`` 形态），**绝不读取 value**。

        Args:
            page: 登录页 Page。

        Returns:
            str: CSS 选择器（推导失败时退回首文本框候选）。
        """
        # page.evaluate 传纯字符串表达式（无外部输入拼接；表达式内只读
        # 静态属性——name/id/type，不读 value，§5.1 静态审查项）
        try:
            best: Optional[str] = page.evaluate(_JS_DETECT_USERNAME)
        except Exception:  # noqa: BLE001 - evaluate 失败退回通用候选
            best = None
        if best:
            return best
        # 兜底：常见登录框名（遗留系统高频命名），交由 query_selector 存在性裁决
        return (
            "form input[name='username'],form input[name='user'],form input[name='account'],"
            "form input[name='login'],form input[type='email'],form input[type='text'],"
            "input[type='email'],input[type='text']"
        )

    def _heuristic_submit_selector(self, page: "Page") -> Optional[str]:
        """启发式推导提交按钮选择器（找不到返回 None → 回车提交兜底）。

        Args:
            page: 登录页 Page。

        Returns:
            str | None: CSS 选择器或 None。
        """
        for candidate in (
            "form button[type='submit']",
            "form input[type='submit']",
            "form button",
        ):
            try:
                if page.query_selector(candidate) is not None:
                    return candidate
            except Exception:  # noqa: BLE001 - 选择器异常视为未命中
                continue
        return None

    def wait_login_result(
        self,
        page: "Page",
        context: "BrowserContext",
        timeout_ms: int = _LOGIN_JUDGE_TIMEOUT_MS,
    ) -> Optional[str]:
        """三判据等待登录结果（§2.3.5，REQ-SU-004 AC1）。

        判据（任一满足即成功，返回对应 judged_by）：
          1. ``success_hint``：配置的登录成功标志选择器出现；
          2. ``url_left_login``：当前 URL 已离开登录路径（不同 path 且页面
             不再含 password 输入框——防"重定向回同 path 的已登录首页"误判，
             故附加 password 检查）；
          3. ``session_cookie``：相对登录前基线新增会话语义 cookie
             （名称命中 _SESSION_COOKIE_HINTS，或新增 cookie 值长度达标）。

        Args:
            page: 提交后的 Page。
            context: 浏览器上下文（cookie 面）。
            timeout_ms: 判定窗口（默认 15s）。

        Returns:
            str | None: 命中的判据名；窗口内均未满足返回 None。
        """
        deadline = time.monotonic() + timeout_ms / 1000.0
        success_hint = self._cfg.system.success_hint
        login_path = urlsplit(self._absolute_login_url()).path.rstrip("/") or "/login"

        # 重登场景的 cookie 基线修正（REQ-SU-004.4）：crawler 检测到回跳
        # 登录页触发重登时，会话 cookie 名已在首轮登录时进入上下文——
        # "相对基线**新增**"判据在此场景结构性失效（同名重新签发不算新增）。
        # 基线中存在会话语义 cookie 名 ⇒ 本次调用必为重登（首次自动登录
        # 前上下文无会话 cookie，基线不含 hint 命中项）：判据 3 改用
        # "同名会话 cookie 值与提交前不同"——值基线快照取在 _auto_login
        # 填表提交**之前**（此刻上下文携带的是待失效/已撤销的旧 token），
        # 服务端 POST 重签的新 token 值与之不同即命中。
        # 口径说明：旧 token 值仅作为"新会话已签发"的比对参照，不作成功
        # 依据本身；若服务端重签发恰好复用同值（真实世界极罕见——会话
        # token 是高熵随机串），本判据不命中，判据 1/2 仍独立可用。
        # 首次登录路径基线无会话名，不触发本分支，零行为变化。
        baseline_names = set(self._cookie_baseline or set())
        session_names = {n for n in baseline_names
                         if any(hint in n.lower() for hint in _SESSION_COOKIE_HINTS)}
        if session_names:
            # 基线摘除会话名（同名重新签发不再被 name in baseline 短路）；
            # 值基线沿用 _auto_login 提交前快照中属于会话名的部分
            self._cookie_baseline = baseline_names - session_names
            pre_submit = getattr(self, "_session_reissue_pre_submit", None) or {}
            self._session_reissue_baseline = {
                k: v for k, v in pre_submit.items() if k in session_names}
        else:
            self._session_reissue_baseline = {}

        while time.monotonic() < deadline:
            # 判据 1：显式成功提示选择器
            if success_hint:
                try:
                    if page.query_selector(success_hint) is not None:
                        return "success_hint"
                except Exception:  # noqa: BLE001 - 选择器异常按未命中处理
                    pass
            # 判据 2：离开登录路径（且 password 框已消失）
            try:
                current_path = urlsplit(page.url).path.rstrip("/")
                if current_path and current_path != login_path:
                    if page.query_selector("input[type='password']") is None:
                        return "url_left_login"
            except Exception:  # noqa: BLE001 - page.url 读取异常按未命中处理
                pass
            # 判据 3：会话 cookie 新增
            if self._session_cookie_added(context):
                return "session_cookie"
            time.sleep(_LOGIN_JUDGE_POLL_MS / 1000.0)
        return None

    # ------------------------------------------------------------------
    # storage_state 存取（0600 边界，§5.5）
    # ------------------------------------------------------------------

    def save_storage_state(self, context: "BrowserContext") -> Path:
        """保存当前上下文 storage_state 到 ``state/storage_state.json``（0600）。

        Args:
            context: 已登录的浏览器上下文。

        Returns:
            Path: 落盘文件路径（权限 0600）。
        """
        target = self._state_dir() / _STORAGE_STATE_FILENAME
        # Playwright 落盘先写内容；os.chmod 在写完立即收紧权限。
        # 注意：storage_state 本身含会话 cookie，属敏感文件清单（§5.5），
        # 严禁进入 snapshots/ / understanding.json（渲染器路径不含此文件）。
        context.storage_state(path=str(target))
        os.chmod(str(target), 0o600)
        return target

    def inject_storage_state(self, context: "BrowserContext") -> LoginOutcome:
        """``--storage-state`` 人工已登录态注入（AC3 旁路，§5.5 复制语义）。

        流程：源文件**复制**进 ``state/storage_state.json`` 并对副本 chmod 0600
        （源文件不动、不改权限）→ ``context.add_init_script``? 否—— Playwright
        的注入点在 context 创建期，运行中 context 采用 **cookies + localStorage**
        两通道灌入（storage_state JSON 的两个组成面），等价于创建期注入。

        Args:
            context: 待注入的浏览器上下文。

        Returns:
            LoginOutcome: judged_by='injected_state' 的**初步**结果（调用方
                :meth:`login` 随后在 base_url 上执行一次登录成功判定收口）。

        Raises:
            SuLoginError: 源文件不存在/非法 JSON（注入不可用属配置错误面）。
        """
        source = self._cfg.storage_state_path
        if source is None or not Path(source).is_file():
            raise SuLoginError(
                "--storage-state 指定的文件不存在：{0}".format(source),
                hints=["请确认人工导出的 storage_state JSON 文件路径"],
            )
        target = self._state_dir() / _STORAGE_STATE_FILENAME
        # 复制（源文件不动，§5.5）；copyfile 不携带权限，落盘后立即 chmod 0600
        shutil.copyfile(str(source), str(target))
        os.chmod(str(target), 0o600)

        try:
            with target.open("r", encoding="utf-8") as fh:
                state = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            raise SuLoginError(
                "storage_state 文件解析失败：{0}".format(exc),
                hints=["文件必须是 Playwright storage_state 形态的 JSON 对象"],
            ) from exc

        self._apply_state_to_context(context, state)
        return LoginOutcome(
            success=True,
            judged_by="injected_state",
            detail="已注入人工 storage_state（cookies={0} / origins={1}），"
                   "待在 base_url 上验证判据".format(
                       len(state.get("cookies") or []),
                       len(state.get("origins") or []),
                   ),
        )

    def _wait_and_verify_injected(self, page: "Page") -> None:
        """注入后验证：打开 base_url 并跑三判据（判据 3 因基线同源天然易命中）。

        验证失败不直接报错——人工态注入的语义是"信任 + 复核"：三判据全不满足
        时仅记录（crawler 运行中若回跳登录页仍可由 relogin_if_needed 兜底）。

        Args:
            page: Playwright Page。
        """
        try:
            self._limiter.wait("login")
            page.goto(self._cfg.system.base_url, wait_until="domcontentloaded",
                      timeout=self._cfg.budget.page_timeout_ms)
        except Exception:  # noqa: BLE001 - 打开失败交由后续 relogin 机制兜底
            return

    def relogin_if_needed(self, page: "Page", context: "BrowserContext") -> bool:
        """crawler 检测到回跳登录页后的重登（§2.3.5，≤ LOGIN_MAX_RETRY）。

        Args:
            page: 当前 Page（已发现被重定向回登录页）。
            context: 浏览器上下文。

        Returns:
            bool: True=重登成功（crawler 可重试当前页）。

        Raises:
            SuLoginError: 重登尝试超过 :data:`LOGIN_MAX_RETRY` 次——
                '会话反复失效，请检查账号风控/验证码'（退出码 4，AC4）。
        """
        self._relogin_attempts += 1
        if self._relogin_attempts > LOGIN_MAX_RETRY:
            raise SuLoginError(
                "会话反复失效：重登 {0} 次仍被回跳登录页，请检查账号风控/验证码".format(
                    LOGIN_MAX_RETRY),
                hints=["改用 --storage-state 注入人工已登录态后重跑（--resume 续跑）"],
            )
        try:
            outcome = self._auto_login(page, context)
        except SuLoginError:
            # 单次重登失败不直接终止（次数额度未用尽时 crawler 可继续判断），
            # 返回 False 由调用方决定重试当前页还是放弃
            return False
        return outcome.success

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _absolute_login_url(self) -> str:
        """login_url 归一为绝对 URL（相对路径与 base_url 拼接）。

        Returns:
            str: 可直接 goto 的登录页绝对 URL。
        """
        login_url = self._cfg.system.login_url or "/login"
        parts = urlsplit(login_url)
        if parts.scheme and parts.netloc:
            return login_url
        base = urlsplit(self._cfg.system.base_url)
        # 相对路径拼接：保留 base 的 scheme://netloc
        path = login_url if login_url.startswith("/") else "/{0}".format(login_url)
        return "{0}://{1}{2}".format(base.scheme, base.netloc, path)

    def _state_dir(self) -> Path:
        """返回（并确保存在的）``<out>/<system_id>/state`` 目录。

        Returns:
            Path: state 目录。
        """
        state_dir = self._cfg.out_dir / self._cfg.system_id / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        return state_dir

    def _write_login_session(self, outcome: LoginOutcome) -> None:
        """登录判定依据脱敏落 ``state/login_session.json``（§4.1 时序）。

        Args:
            outcome: 登录结果（to_redacted 已过 redact 管线）。
        """
        target = self._state_dir() / _LOGIN_SESSION_FILENAME
        with target.open("w", encoding="utf-8") as fh:
            json.dump(outcome.to_redacted(), fh, ensure_ascii=False,
                      sort_keys=True, indent=2)
        # 判定依据含 cookie 计数等运行时信息，同样收紧权限（与 storage_state 同级对待）
        os.chmod(str(target), 0o600)

    def _cookie_names(self, context: "BrowserContext") -> set:
        """当前上下文 cookie 名称集合（session_cookie 判据基线快照）。

        Args:
            context: 浏览器上下文。

        Returns:
            set: cookie 名称集合；读取失败返回空集合（判据保守失效不报错）。
        """
        try:
            return {str(c.get("name", "")) for c in (context.cookies() or [])}
        except Exception:  # noqa: BLE001 - cookie 读取失败 → 判据 3 降级
            return set()

    def _session_cookie_values(self, context: "BrowserContext") -> dict:
        """当前上下文会话语义 cookie 的 name→value 快照（重登补判据用）。

        Args:
            context: 浏览器上下文。

        Returns:
            dict: 名称命中 _SESSION_COOKIE_HINTS 的 cookie 值映射；
                读取失败返回空 dict（判据 3 的重登分支保守降级不报错）。
        """
        try:
            cookies = context.cookies() or []
        except Exception:  # noqa: BLE001 - cookie 读取失败 → 判据 3 降级
            return {}
        snapshot = {}
        for cookie in cookies:
            name = str(cookie.get("name", ""))
            lowered = name.lower()
            if name and any(hint in lowered for hint in _SESSION_COOKIE_HINTS):
                snapshot[name] = str(cookie.get("value", "") or "")
        return snapshot

    def _session_cookie_added(self, context: "BrowserContext") -> bool:
        """判据 3：相对基线是否新增会话语义 cookie（重登场景改判"重新签发"）。

        常规路径（首次登录，`_session_reissue_baseline` 为空）：基线外新
        增会话名 cookie（或值长达标的新增 cookie）即命中。

        重登路径（`_session_reissue_baseline` 非空——基线修正分支已把旧
        会话名从基线摘除）：服务端撤销旧 token 后重新签发**同名** cookie，
        "新增名称"判据结构性失效；此时只要任一会话名 cookie 的值与撤销前
        快照不同即视为获得新会话。快照中不存在的会话名（撤销后全新签发）
        同样视为命中。

        Args:
            context: 浏览器上下文。

        Returns:
            bool: True=出现新会话 cookie（新增或同名重新签发）。
        """
        baseline = self._cookie_baseline
        if baseline is None:
            return False
        try:
            cookies = context.cookies() or []
        except Exception:  # noqa: BLE001
            return False
        reissue_baseline = getattr(self, "_session_reissue_baseline", None) or {}
        for cookie in cookies:
            name = str(cookie.get("name", ""))
            if not name or name in baseline:
                continue
            lowered = name.lower()
            hint_hit = any(hint in lowered for hint in _SESSION_COOKIE_HINTS)
            if reissue_baseline:
                # 重登路径：同名会话 cookie 值变化 = 服务端重新签发
                if hint_hit:
                    value = str(cookie.get("value", "") or "")
                    if name not in reissue_baseline or value != reissue_baseline[name]:
                        return True
                continue
            if hint_hit:
                return True
            # 无名可辨但值足够长的新增 cookie：遗留系统常见（随机名会话票）
            value = str(cookie.get("value", "") or "")
            if len(value) >= _SESSION_COOKIE_MIN_VALUE_LEN:
                return True
        return False

    def _looks_like_captcha(self, page: "Page") -> bool:
        """验证码/2FA 特征检测（URL 形态 ∪ 页面文本 ∪ 专用 DOM 选择器）。

        命中即触发"终止并提示旁路"路径——本能力**绝不尝试破解**（OUT-4），
        检测结果只影响报错文案分诊，不驱动任何自动绕过逻辑。

        Args:
            page: 判定失败后的登录页。

        Returns:
            bool: True=存在验证码/2FA 特征。
        """
        current_url = ""
        try:
            current_url = (page.url or "").lower()
        except Exception:  # noqa: BLE001
            pass
        if any(hint in current_url for hint in _CAPTCHA_URL_HINTS):
            return True
        try:
            if page.query_selector(_CAPTCHA_SELECTOR) is not None:
                return True
        except Exception:  # noqa: BLE001 - 选择器异常按未命中处理
            pass
        try:
            # inner_text 只取 body 可见文本（不含表单 value，隐私安全）
            body_text = (page.inner_text("body", timeout=2000) or "").lower()
        except Exception:  # noqa: BLE001
            body_text = ""
        return any(hint in body_text for hint in _CAPTCHA_TEXT_HINTS)

    def _apply_state_to_context(
        self,
        context: "BrowserContext",
        state: dict,
    ) -> None:
        """把 storage_state JSON 灌入运行中的 context（cookies + localStorage）。

        Playwright 的 ``storage_state`` 在 ``new_context`` 期一次性生效；本能力
        的 context 由编排层统一创建（route 守卫已装），故按 JSON 两个组成面
        手工灌入：cookies → ``context.add_cookies``；origins.localStorage →
        逐 origin 打开页面后 ``add_init_script`` 预写入。

        Args:
            context: 目标上下文。
            state: storage_state JSON（dict）。
        """
        cookies: List[dict] = []
        for cookie in state.get("cookies") or []:
            if not isinstance(cookie, dict):
                continue
            # 只搬运 Playwright 认识的字段（防注入方塞入未知键）
            entry = {k: cookie[k] for k in (
                "name", "value", "domain", "path", "expires",
                "httpOnly", "secure", "sameSite") if k in cookie}
            # expires 缺失/非法时省略该键（缺省=会话 cookie，Playwright 接受形态）
            if "expires" in entry:
                try:
                    entry["expires"] = float(entry["expires"])
                except (TypeError, ValueError):
                    entry.pop("expires")
            if entry.get("name") and entry.get("domain"):
                cookies.append(entry)
        if cookies:
            context.add_cookies(cookies)

        # localStorage：注入 init script（页面加载时按 origin 写键，
        # 只写源 JSON 中存在的 origin/键，不执行任何外部代码）。
        # Playwright 的 add_init_script 接收 JS 字符串；这里构造一段自包含 JS
        # （origin 匹配后逐项 localStorage.setItem），存储数据经 json 序列化为
        # 字面量内联——数据不是代码，模板中无 eval/动态函数调用。
        origins = state.get("origins") or []
        pending_scripts = []
        for origin in origins:
            if not isinstance(origin, dict):
                continue
            origin_url = str(origin.get("origin") or "")
            entries = origin.get("localStorage") or []
            if not origin_url or not entries:
                continue
            kv = {
                str(item.get("name")): str(item.get("value", ""))
                for item in entries if isinstance(item, dict) and item.get("name")
            }
            if not kv:
                continue
            # json.dumps 生成字面量（数据不是代码：JS 侧逐项 setItem，无 eval）
            payload = json.dumps(kv, ensure_ascii=False)
            origin_prefix = origin_url.rstrip("/")
            pending_scripts.append((origin_prefix, payload))
        if not pending_scripts:
            return
        js_pairs = ", ".join(
            "[{0}, {1}]".format(json.dumps(prefix), payload)
            for prefix, payload in pending_scripts
        )
        init_js = (
            "(() => {{ try {{ const store = [{0}];"
            " for (const [origin, kv] of store) {{"
            " if (location.origin === origin || location.href.startsWith(origin)) {{"
            " for (const [k, v] of Object.entries(kv)) localStorage.setItem(k, String(v));"
            " }}}}}} }} catch (e) {{}} }})();"
        ).format(js_pairs)
        context.add_init_script(init_js)


# ---------------------------------------------------------------------------
# 浏览器侧 JS 常量（纯结构化查询：只读 name/id/type/placeholder 静态属性，
# 永不读 value——§5.1 静态审查项；无外部输入拼接）
# ---------------------------------------------------------------------------

_JS_DETECT_USERNAME = """
(() => {
  const pwd = document.querySelector("input[type='password']");
  if (!pwd) return null;
  const form = pwd.closest('form');
  const scope = form || document;
  const inputs = Array.from(scope.querySelectorAll(
    "input[type='text'],input[type='email'],input[type='tel'],input:not([type])"));
  // 同 form 内、DOM 顺序在 password 之前、可见的最近一个文本框
  const candidates = inputs.filter(el => {
    // 只保留文本类输入框（未声明 type 的 input 浏览器默认 text，HTML 规范）
    const declared = el.getAttribute('type');
    const t = (declared || 'text').toLowerCase();
    if (['text', 'email', 'tel'].indexOf(t) === -1) return false;
    // 可见性粗判（隐藏字段不参与"最近文本框"推导）
    if (el.offsetParent === null && el.offsetWidth === 0) return false;
    return form ? (pwd.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_PRECEDING) : true;
  });
  const pick = candidates.length ? candidates[candidates.length - 1] : inputs[0];
  if (!pick) return null;
  if (pick.name) return "form input[name='" + CSS.escape(pick.name) + "']";
  if (pick.id) return "#" + CSS.escape(pick.id);
  return null;
})()
"""
