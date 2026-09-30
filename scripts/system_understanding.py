#!/usr/bin/env python3
"""SU（System Understanding）CLI 入口与六阶段主编排（架构 ARCH-SU-001 §2.3.1，PRD REQ-SU-020）。

**职责**（composition root，骨架与 project_understanding.py 同构）：
  - argparse 全参数表（REQ-SU-020，全部中文 help，AC1）；
  - 六阶段流水线编排：preflight → login → collect（四透镜失败隔离）→
    relations → llm_bridge → render；
  - 退出码状态机收口（§8.2：0/2/3/4/5/130）；
  - SIGINT → store.flush + mark('interrupted') + 释放锁 → exit 130；
  - ``--skip-llm-phase``：跳过 findings 直接骨架渲染（第 5/7 节"待 LLM 语义回填"）；
  - ``--render-only``：不要求 playwright/凭据完整预检——仅需输出目录存在
    completed/interrupted 状态库 + understanding.json 的 findings 段，
    校验入库 findings 后仅渲染（违例 exit 2）；
  - 预算摘要打印（NFR-SU-006）；日志 RedactingFormatter（复用 config.scrub_text，
    --verbose 亦不例外，§5.5）。

红线口径：本文件是 ``StateStore.replace_findings()`` 的**唯一调用方**
（§1.2 边界规则 2 单一入口契约——模块名必须为 'system_understanding'，
state_store 侧以调用栈断言强制）。

软依赖红线（REQ-SU-021）：playwright/pymysql/psycopg2/redis 一律经
``su.deps.probe_all()`` 运行时探测注入，本模块顶层零软依赖 import——
``--help`` 与 ``--render-only`` 在无 playwright 环境下必须可用。
"""

import argparse
import json
import logging
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# sys.path 自举：支持 `python3 scripts/system_understanding.py` 直接执行
# （su 包以 scripts/ 为包根导入）
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from su.api_observer import ApiObserver  # noqa: E402
from su.browser_login import BrowserLogin  # noqa: E402
from su.config import SuConfig, load_config, scrub_text  # noqa: E402
from su.db_inspector import DbInspector  # noqa: E402
from su.deps import DependencyReport, probe_all  # noqa: E402
from su.detailed_doc import (  # noqa: E402
    check_existing_final as _sfd_check_existing_final,
    build_paths as _sfd_build_paths,
    run_assemble,
    run_detailed_doc,
)
from su.document_renderer import DocumentRenderer  # noqa: E402
from su.dto import SuConfigError, SuDepsError, SuError, SuLoginError  # noqa: E402
from su.limiter import BudgetTracker, RateLimiter  # noqa: E402
from su.preflight import Preflight, PreflightReport  # noqa: E402
from su.relation_analyzer import RelationAnalyzer  # noqa: E402
from su.redis_inspector import RedisInspector  # noqa: E402
from su.site_crawler import CrawlReport, SiteCrawler  # noqa: E402
from su.state_store import StateStore  # noqa: E402

__all__ = ["SystemUnderstanding", "main"]

# ---------------------------------------------------------------------------
# 退出码常量（§2.3.1 / §8.2 / PRD REQ-SU-020）
# ---------------------------------------------------------------------------
EXIT_OK = 0            # 成功（含预算耗尽收尾、透镜降级完成）
EXIT_CONFIG = 2        # 配置/参数错误（含 --render-only findings 校验失败）
EXIT_UNREACHABLE = 3   # system 不可达（preflight 唯一致命项）
EXIT_LOGIN = 4         # 登录失败 / 会话反复失效
EXIT_DEPS = 5          # playwright 缺失（唯一依赖类致命码）
EXIT_INTERRUPTED = 130  # SIGINT（128+SIGINT；状态库已存 interrupted 可 --resume）


class RedactingFormatter(logging.Formatter):
    """日志脱敏 Formatter（§5.5 / §6.2 三层命中动作表"日志"层）。

    复用 :func:`su.config.scrub_text`：先剥离 URL 内嵌 user:pass@ 凭据，
    再按 PII 值形态正则（手机号/身份证/邮箱/银行卡）替换为 <REDACTED:类型>。
    --verbose 调试日志同样经过本 Formatter（REQ-SU-020：调试日志仍全程脱敏）。
    """

    def format(self, record: logging.LogRecord) -> str:
        """格式化日志行并整行过 scrub_text。

        Args:
            record: logging 日志记录。

        Returns:
            str: 脱敏后的日志文本。
        """
        return scrub_text(super().format(record))


def _setup_logging(verbose: bool, log_dir: Optional[Path] = None) -> None:
    """配置结构化日志（NFR-SU-006：key=value 风格中文消息 + 全程脱敏）。

    Args:
        verbose: True=控制台 DEBUG 级；False=INFO 级。
        log_dir: 提供时追加 ``logs/run-<timestamp>.log`` 文件 handler
            （DEBUG 级、同样挂 RedactingFormatter）。
    """
    formatter = RedactingFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if log_dir is not None else logging.INFO)
    # 清空重复 handler（幂等：单测/多次调用不叠加输出）
    for handler in list(root.handlers):
        root.removeHandler(handler)
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(formatter)
    root.addHandler(console)
    if log_dir is not None:
        import time as _time
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(
            log_dir / "run-{0}.log".format(int(_time.time())), encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


logger = logging.getLogger("su.cli")


# ---------------------------------------------------------------------------
# 透镜采集报告（阶段 2 汇总 → understanding.json lenses 段 / 第 10 节 e 项）
# ---------------------------------------------------------------------------

class _LensReport:
    """四透镜采集状态汇总（失败隔离：单透镜异常只降级自己，NFR-SU-004）。

    每个透镜一个 (status, skip_reason) 二元组；status ∈ collected/skipped/failed
    （与渲染器 set_lens_status / understanding.json §6.1 枚举一致）。
    """

    def __init__(self) -> None:
        """初始化为四透镜全 skipped（采集未发生的保守默认）。"""
        self._status: Dict[str, Dict[str, Optional[str]]] = {
            lens: {"status": "skipped", "skip_reason": "采集阶段未执行"}
            for lens in ("ui", "api", "db", "redis")
        }
        # UI 遍历报告（预算摘要/summary 数据源，可为 None）
        self.crawl_report: Optional[CrawlReport] = None
        # 本次运行的预算追踪器（预算摘要打印数据源，NFR-SU-006）
        self.budget_tracker: Optional[BudgetTracker] = None

    def set(self, lens: str, status: str, reason: Optional[str] = None) -> None:
        """登记单透镜状态（skipped/failed 必须带中文原因，AP-3）。

        Args:
            lens: ui/api/db/redis。
            status: collected/skipped/failed。
            reason: 非 collected 的中文原因（含安装命令）。
        """
        self._status[lens] = {"status": status, "skip_reason": reason}

    def get(self, lens: str) -> Dict[str, Optional[str]]:
        """读取单透镜状态。"""
        return dict(self._status[lens])

    def apply_to(self, renderer: DocumentRenderer) -> None:
        """把状态注入渲染器（渲染层缺失声明的唯一事实来源）。

        Args:
            renderer: 目标渲染器。
        """
        for lens in ("ui", "api", "db", "redis"):
            entry = self._status[lens]
            renderer.set_lens_status(lens, entry["status"], entry["skip_reason"])


# ---------------------------------------------------------------------------
# playwright driver 子进程 pid 捕获（SIGINT 看门狗解挂专用）
# ---------------------------------------------------------------------------

def _install_driver_pid_hook(sink: list):
    """临时挂钩 playwright driver 子进程 spawn 点，捕获 driver pid。

    playwright 的 driver 子进程（node/打包二进制）在 ``sync_playwright().
    start()`` 内部 spawn。spawn 位置随 playwright 版本漂移——本钩子按
    候选顺序探测（2026-09-28 e2e 场景[4]根因修复：playwright 1.60 中
    ``Transport`` 是无 ``init`` 的 ABC，旧挂钩目标恒失败致看门狗降级、
    SIGINT 落入 CDP 挂起窗口被吞）：

      1. ``playwright._impl._transport.PipeTransport.connect``（现行结构：
         connect 内 ``asyncio.create_subprocess_exec`` 后挂到 ``self._proc``，
         实测 playwright 1.60.0）；
      2. ``playwright._impl._transport.Transport.init``（旧版结构兜底）。

    钩子在 spawn 完成后从 ``self._proc.pid`` 读取 pid 追加进 ``sink``——
    比"start 前后子进程快照差集"精确（不受其它子进程干扰）。

    钩子失败（playwright 内部结构再次变更等）时静默降级：sink 保持为空，
    看门狗退化为纯信号快路径值守（发送侧 SIGINT 重发仍是最终兜底），
    绝不影响主流程。

    Args:
        sink: 收集 driver pid 的列表（原地追加）。

    Returns:
        Tuple[contextmanager, Callable]: ``(上下文管理器, 还原函数)``——
        上下文管理器包裹 ``start()`` 调用；还原函数幂等、须在 start 后
        立即调用以解除挂钩（尽力而为，重复调用无害）。
    """
    import contextlib
    import importlib

    # 挂钩目标候选：(模块名, 类名, 方法名)，按 playwright 版本新旧排序，
    # 第一个"模块/类/方法全部存在"的候选即当前版本真实 spawn 点
    _candidates = [
        ("playwright._impl._transport", "PipeTransport", "connect"),
        ("playwright._impl._transport", "Transport", "init"),
    ]
    # 已挂钩的 (module, cls, method_name, original) 四元组（还原用）
    state: dict = {"hooked": None}

    def _patch() -> None:
        """安装挂钩；任何异常静默（降级口径见 docstring）。"""
        for module_name, cls_name, method_name in _candidates:
            try:
                module = importlib.import_module(module_name)
                cls = getattr(module, cls_name, None)
                original = getattr(cls, method_name, None)
                if original is None or not callable(original):
                    continue

                def _make_wrapper(func):  # noqa: ANN001, ANN202
                    """构造方法包装器：完成后记录 driver pid。

                    Args:
                        func: 原始 connect/init 协程方法。

                    Returns:
                        Callable: 同签名异步包装函数。
                    """
                    async def _patched(self, *args, **kwargs):  # noqa: ANN001, ANN202
                        """包装 spawn 方法：完成后从 self._proc 记录 pid。"""
                        await func(self, *args, **kwargs)
                        proc = getattr(self, "_proc", None)
                        pid = getattr(proc, "pid", None)
                        if pid:
                            sink.append(int(pid))

                    return _patched

                setattr(cls, method_name, _make_wrapper(original))
                state["hooked"] = (module, cls, method_name, original)
                return
            except Exception:  # noqa: BLE001 - 单候选失败尝试下一候选
                continue
        logger.debug("playwright driver pid 挂钩失败（看门狗降级运行）",
                     exc_info=True)

    def _restore() -> None:
        """解除挂钩（幂等；失败静默——进程即将正常收尾时钩子无害）。"""
        hooked = state["hooked"]
        if hooked is None:
            return
        _module, cls, method_name, original = hooked
        try:
            if getattr(cls, method_name) is not original:
                setattr(cls, method_name, original)
        except Exception:  # noqa: BLE001 - 还原失败不影响主流程
            pass

    @contextlib.contextmanager
    def _hooked():
        """上下文管理器：进入时挂钩，异常路径也保证还原。"""
        _patch()
        try:
            yield
        finally:
            _restore()

    return _hooked(), _restore


# ---------------------------------------------------------------------------
# 主编排类
# ---------------------------------------------------------------------------

class SystemUnderstanding:
    """SU 能力主编排类：六阶段流水线的组合根（§2.3.1）。

    典型调用（main）::

        app = SystemUnderstanding(args)
        code = app.run()   # 退出码收口（0/2/3/4/5/130）
    """

    def __init__(self, args: argparse.Namespace) -> None:
        """初始化编排器。

        Args:
            args: argparse 解析结果（render_only/verbose 等 CLI 标志原样保留，
                完整 SuConfig 在 :meth:`run` 内 load_config 构造——
                --render-only 分支不需要凭据，配置加载必须分支化）。
        """
        self._args = args
        self._render_only = bool(getattr(args, "render_only", False))
        self._skip_llm_phase = bool(getattr(args, "skip_llm_phase", False))
        self._cfg: Optional[SuConfig] = None
        self._deps: Optional[DependencyReport] = None
        self._store: Optional[StateStore] = None
        self._preflight_report: Optional[PreflightReport] = None
        # REQ-SU-004.4：阶段 1 登录成功后挂上的 BrowserLogin 协作者，
        # 阶段 2 注入 SiteCrawler 供"回跳登录页 → 自动重登"接线
        # （_phase_login 赋值；--render-only 路径恒 None，不触发登录）
        self._login = None
        self._lens_report = _LensReport()
        self._renderer: Optional[DocumentRenderer] = None
        # SIGINT 落点标志（2026-09-28 e2e 场景[4]根因修复）：信号处理器
        # 执行收口三步时置位——即使主线程此刻正处在 generate() 之后的
        # 渲染/收口段（信号处理器的 sys.exit(130) 要等当前字节码序列让出
        # 才生效），run() 收口点读到本标志即不再覆写 completed、以 130
        # 退出。与 run_meta 状态复读构成双保险
        self._interrupt_exit_requested = threading.Event()

    # ------------------------------------------------------------------
    # 门面方法（与 project_understanding.py 骨架对齐）
    # ------------------------------------------------------------------

    def generate(self) -> Dict[str, Any]:
        """执行阶段 0~3（预检/登录/采集/关联），返回脱敏理解结果数据。

        Returns:
            dict: understanding 数据（= store.export_understanding() 产物，
                已全脱敏；--skip-llm-phase 时 findings 段为空属预期）。

        Raises:
            SuError 族: 各阶段致命错误（由 run() 按 exit_code 收口）。
        """
        assert self._cfg is not None and self._store is not None
        self._phase_preflight()
        self._phase_login(self._store)
        self._phase_collect(self._store)
        self._phase_relations(self._store)
        return dict(self._store.export_understanding())

    def save(self, output_dir: str, run_status_override: Optional[str] = None) -> None:
        """执行阶段 5：从状态库渲染全部产物（UNDERSTANDING.md/json/图）。

        Args:
            output_dir: 输出根目录（覆盖配置的 out_dir——main 恒传 CLI --out 值，
                保证两阶段工作流（§6.3）阶段 C 可指回同一目录）。
            run_status_override: 透传给渲染器的 run 终态覆盖值（completed/
                interrupted，2026-09-30 D1 修复）——调用方保证"渲染成功后即
                mark 同值收口"时传入，使磁盘 understanding.json 的 meta 段
                与 DB 收口一致，不再冻结在导出瞬间的 running。
        """
        assert self._store is not None
        cfg = self._cfg
        assert cfg is not None
        # 输出目录以显式入参为准（幂等重渲染收口路径）
        out_path = Path(output_dir)
        if str(out_path) != str(cfg.out_dir):
            self._cfg = _cfg_with_out(cfg, out_path)
        renderer = DocumentRenderer(self._store, self._cfg)
        self._lens_report.apply_to(renderer)
        renderer.render(run_status_override=run_status_override)
        self._renderer = renderer

    # ------------------------------------------------------------------
    # 运行主入口（退出码收口，§8.2）
    # ------------------------------------------------------------------

    def run(self) -> int:
        """CLI 运行主入口：分支编排 + 退出码状态机收口。

        分支判定顺序（ARCH-SFD-001 §3.2）：SFD 详说两模式先于 --render-only
        （三者互斥由 argparse mode_group 保证——同给时 argparse 标准
        SystemExit(2)，根本到不了本方法；getattr 兜底属既有惯例，防御
        测试替身/编程式构造 args 时缺属性，非笔误）。

        Returns:
            int: 0/2/3/4/5/130（--render-only 与 SFD 两模式只可能返回 0/2/130）。
        """
        try:
            if getattr(self._args, "detailed_doc", False):
                return self._run_detailed_doc()
            if getattr(self._args, "assemble", False):
                return self._run_assemble()
            if self._render_only:
                return self._run_render_only()
            return self._run_full_pipeline()
        except SuError as exc:
            # 结构化错误族：打印中文 message + hints，按自带 exit_code 收口
            logger.error(exc.format())
            return int(exc.exit_code)
        except KeyboardInterrupt:
            # 兜底：信号处理器未注册（非主线程）时的 Ctrl-C
            self._handle_interrupt_exit()
            return EXIT_INTERRUPTED

    def _run_full_pipeline(self) -> int:
        """完整六阶段运行（阶段 0~5，含 LLM 桥）。

        Returns:
            int: 退出码（0 成功 / 2 配置错 / 3 不可达 / 4 登录失败 / 5 依赖缺失 / 130 中断）。

        Raises:
            SuConfigError: load_config 校验失败（exit 2）。
            SuDepsError: playwright 缺失（exit 5）。
        """
        args = self._args
        # 先以 console-only 模式启用日志（scrub 保证任何早期日志不泄敏感串）
        _setup_logging(bool(getattr(args, "verbose", False)))
        # 软依赖探测（本进程内只做一次；playwright 致命性在 preflight 收口）
        self._deps = probe_all()
        # 完整配置（三级合并 + validate；失败 SuConfigError exit 2）
        self._cfg = load_config(args)
        # 日志文件落 <out>/<system_id>/logs（run 级产物目录；重配 handler）
        _setup_logging(
            bool(getattr(args, "verbose", False)),
            self._cfg.out_dir / self._cfg.system_id / "logs")

        # --render-only 之外的路径 playwright 属致命依赖（REQ-SU-021 第一行）：
        # 提前收口 exit 5，报错含安装命令（preflight 的 dep:playwright 项同口径）
        if self._deps.playwright is None:
            self._deps.require_playwright()  # 恒抛 SuDepsError（exit_code=5）

        out_root = self._cfg.out_dir / self._cfg.system_id
        # --fresh 归档语义收敛至 StateStore.acquire_lock(resume=False) 的
        # interrupted 分支（REQ-SU-019 AC2 / 架构 §4.3 时序 Note；
        # 2026-09-29 e2e 场景[4]根因修复）：interrupted 事实的两种归宿
        # ——resume=True 复用续采 / resume=False（--fresh）rename 为
        # state.archive.<ts>/ 后新建空库——在 acquire_lock 唯一判定点完成。
        # CLI 层不再预读 legacy 库判 interrupted（此前"legacy 库文件在位
        # 即归档"会把 completed 旧库也错误归档；改为 interrupted 判据后，
        # resume 已消费 interrupted 的时序又使 CLI 判据永远落空——归档
        # 语义实际不可达）。CLI 仅保留**旧库损坏**兜底：构造失败时 rename
        # 旧 state/ 后重建（rename 失败让构造异常照常上抛收口）。
        try:
            store = StateStore(out_root / "state" / "understanding.sqlite",
                               self._cfg.system_id)
        except Exception as exc:  # noqa: BLE001 - 旧库损坏时先归档再重建
            try:
                (out_root / "state").rename(
                    out_root / "state.archive.{0}".format(int(time.time())))
            except OSError:
                pass
            store = StateStore(out_root / "state" / "understanding.sqlite",
                               self._cfg.system_id)
            logger.warning("旧状态库不可用（%s），已尝试归档后重建", type(exc).__name__)
        self._store = store
        try:
            run_meta = store.acquire_lock(resume=self._cfg.resume)
            store.set_config_snapshot(self._config_snapshot())
            self._install_signal_handlers(store)
            logger.info("run_id=%s status=%s 采集开始", run_meta.get("run_id"), run_meta.get("status"))

            # 阶段 0~3（预检/登录/采集/关联）——六阶段流水线的采集主体，
            # 必须先行于 LLM 桥与渲染收口（阶段 4/5）
            self.generate()

            # 中断落点追踪（2026-09-28 e2e 场景[4]根因修复）：信号处理器的
            # 收口三步把 run 标 interrupted + 释放锁后，主线程从挂起的 CDP
            # 调用苏醒——若它正处于 generate() 内，crawler 的中断检查会
            # 正常 break 收口；但若信号恰落在 generate() **返回之后**的
            # 渲染阶段（save/_print_budget_summary 无 Playwright 阻塞、
            # 毫秒级完成），后续"completed 收口"会把已标的 interrupted
            # 无条件覆写回 completed（实测：SIGINT 处理 WARNING 已落日志、
            # run 却以 completed/8.2s 收口，exit 0）——中断事实被吞，e2e
            # 场景[4] 的 130/interrupted/归档断言全部落空。收口前复读
            # run 状态：非 running（= 中断处理器已标 interrupted）时不
            # 覆写状态、以 130 退出（状态先于产物原则的收口侧延伸）。
            interrupted_during_run = (
                self._interrupt_exit_requested.is_set()
                or store.run_status_of_current() == "interrupted")

            # LLM 桥（完整流水线语义：产出 understanding.json 供宿主 LLM 回填；
            # --skip-llm-phase 时 findings 段保持空 → 骨架渲染，AC3）
            self._phase_llm_bridge(store)

            # 渲染收口（阶段 5）+ run 状态收口。D1 修复（2026-09-30）：
            # 未中断链路传 completed 覆盖——渲染成功后紧接 mark("completed")
            # 收口，磁盘 understanding.json 的 meta.run_status 自此与 DB 终态
            # 一致（此前冻结在导出瞬间的 running，宿主/SFD 前置校验读磁盘
            # 投影时永远违例）。中断链路**不传**覆盖：中断处理器已先
            # mark("interrupted")，导出值天然就是 interrupted，渲染与 DB 无
            # 时序差，无需投影修正（保持既有 e2e 场景[4]语义零变化）。
            self.save(str(self._cfg.out_dir),
                      run_status_override=(None if interrupted_during_run
                                           else "completed"))
            if interrupted_during_run:
                # 中断后产物照常渲染落盘（已采进度不浪费——interrupted
                # 库可 --resume），但 run 状态保持 interrupted、退出码 130
                logger.warning("渲染期间收到 SIGINT：产物已落盘，"
                               "run 保持 interrupted 以 130 退出（不覆写 completed）")
                return EXIT_INTERRUPTED
            exit_reason = None
            if self._lens_report.budget_tracker is not None:
                exhausted = self._lens_report.budget_tracker.exhausted
                if exhausted:
                    exit_reason = "budget_exhausted:{0}".format(exhausted)
            store.mark("completed", exit_reason)
            store.release_lock()
            self._print_budget_summary()
            logger.info("运行完成 out=%s", str(out_root))
            return EXIT_OK
        finally:
            store.close()

    def _run_render_only(self) -> int:
        """--render-only 分支（PRD REQ-SU-020 / 架构 §6.3 阶段 C）。

        **不要求 playwright / 凭据完整预检**：仅要求
          1. ``<out>/<system_id>/state/understanding.sqlite`` 存在（输出目录必填）；
          2. 最新 run_meta.status ∈ {completed, interrupted}；
          3. 同目录 understanding.json 存在且含 findings 段（数组）；
        任一违例 → 中文报错 exit 2。满足则：acquire_lock（陈旧锁/被中断锁
        天然可接管）→ replace_findings 全量替换 → 仅渲染 → 释放锁 exit 0。

        Returns:
            int: 0 成功 / 2 前置违例或 findings 校验失败 / 130 中断。

        Raises:
            SuConfigError: 全部前置违例（exit_code=2，中文 message+hints）。
        """
        args = self._args
        out_raw = getattr(args, "out", None)
        if not out_raw:
            raise SuConfigError(
                "--render-only 必须显式提供 --out <目录>（指向此前运行的输出根目录）",
                hints=["示例：python scripts/system_understanding.py --out docs/system-understanding "
                       "--system-id <id> --render-only"])
        _setup_logging(bool(getattr(args, "verbose", False)))
        out_root = Path(out_raw)
        system_id = getattr(args, "system_id", None)
        if not system_id:
            # system_id 默认派生需要 base_url；--render-only 无凭据面，唯一合法
            # 来源是显式 --system-id（此前运行目录名），缺失属参数错误
            raise SuConfigError(
                "--render-only 必须显式提供 --system-id <输出子目录名>（此前运行的目录）",
                hints=["输出目录结构为 <out>/<system_id>/，--system-id 即其中的目录名"])
        system_id = str(system_id)
        sys_root = out_root / system_id
        db_path = sys_root / "state" / "understanding.sqlite"
        understanding_path = sys_root / "understanding.json"
        if not db_path.is_file():
            raise SuConfigError(
                "--render-only 前置违例：输出目录不存在状态库（{0}）——"
                "此前必须有一次 completed/interrupted 的完整运行".format(db_path),
                hints=["先跑完整采集（可配 --skip-llm-phase），回填 findings 后再 --render-only"])
        if not understanding_path.is_file():
            raise SuConfigError(
                "--render-only 前置违例：缺少 {0}（此前运行的产物）".format(understanding_path),
                hints=["--render-only 用于回填后收口：findings 应写回该文件的 findings 段"])
        try:
            understanding_data = json.loads(understanding_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SuConfigError(
                "--render-only 前置违例：understanding.json 无法解析（{0}）".format(exc),
                hints=["请确保文件为 UTF-8 JSON（此前运行产物 + 回填的 findings 段）"])
        if not isinstance(understanding_data, dict) or "findings" not in understanding_data:
            raise SuConfigError(
                "--render-only 前置违例：understanding.json 不含 findings 段",
                hints=["回填契约见 docs/spec/role-prompts/su-llm-backfill.md；"
                       "findings 段必须是 JSON 数组（可为空数组=撤回全部结论）"])
        findings = understanding_data["findings"]
        if not isinstance(findings, list):
            raise SuConfigError(
                "--render-only 前置违例：findings 段必须是 JSON 数组，实际 {0}".format(
                    type(findings).__name__),
                hints=["形态示例见 docs/spec/role-prompts/su-llm-backfill.md"])

        # 状态库最新 run 必须是 completed/interrupted（running=他进程在跑/崩溃未收口）
        store = StateStore(db_path, system_id)
        self._store = store
        # 渲染器配置：--render-only 不需要凭据，构造"渲染参数壳"SuConfig——
        # budget 取 CLI 覆盖/默认值（渲染不消费），system 段用占位（永不落盘，
        # 文档 config_snapshot 读的是 run_meta 内此前运行的真实快照）
        self._cfg = self._render_only_config(system_id, out_root)
        try:
            # 前置校验**只读**最新历史 run——绝不在校验通过前 acquire_lock：
            # acquire_lock 对 completed/failed 历史 run 走新建分支（返回的
            # 永远是 'running'），若先锁后校验，违例路径必然留下自建的
            # running 残行（2026-09-28 e2e 场景[5]教训：曾以"违例后清算"
            # 兜底，但正常路径同样会新建行——校验语义本身就必须基于历史
            # run 而非本次新建行）。
            history = store.read_latest_run()
            if history is None:
                raise SuConfigError(
                    "--render-only 前置违例：状态库无任何 run 记录（{0}）".format(db_path),
                    hints=["先跑完整采集（可配 --skip-llm-phase），回填 findings 后再 --render-only"])
            status = str(history.get("status"))
            if status not in ("completed", "interrupted"):
                raise SuConfigError(
                    "--render-only 前置违例：状态库最新 run 状态为 {0}（要求 completed/interrupted）".format(status),
                    hints=["running 状态请等待对端进程收口（心跳 >60s 后陈旧可接管），"
                           "或确认库文件来自一次完整运行的产物"])
            # 校验通过后才取锁：历史 completed → 新建 render run（设计语义，
            # 渲染属新运行）；历史 interrupted → resume 复用原 run
            run_meta = store.acquire_lock(resume=True)
            self._install_signal_handlers(store)
            try:
                # findings 校验 + 全量替换入库（SuConfigError exit 2 由 run() 收口）
                self._phase_llm_bridge(store, findings_override=findings)
                # 状态库历史透镜状态不可考：全部按 collected 之外的保守值会误报
                # "未采集"——--render-only 语义是"重渲染既有事实"，四透镜据库内
                # 数据有无登记：有数据=collected，无数据=skipped（诚实口径）
                self._infer_lens_status(store)
                # D1 修复（2026-09-30）：acquire_lock 后本次 render run 处于
                # running，渲染成功后紧接 mark("completed") 收口——传终态覆盖
                # 使 understanding.json 的 meta.run_status 与 DB 收口一致
                # （此前冻结为 running，SFD 前置校验读磁盘投影时永远违例）。
                self.save(str(out_root), run_status_override="completed")
            except BaseException:
                # 取锁后任何失败（findings 校验 exit 2 / 其它异常）都必须清算
                # 本次自建 run——否则 running 残行 + 指向已退出进程的 locked_by
                # 会毒化状态库（下次 render-only 前置校验永违例）。2026-09-28
                # e2e 场景[5]完整闭环：校验读历史 run + 失败清算 + 成功 completed
                try:
                    store.mark("failed", "render_only_error")
                    store.release_lock()
                except Exception:  # noqa: BLE001 - 清算尽力而为，不掩盖原始异常
                    logger.warning("render-only 失败收口：run %s 清算失败（状态库实际值为准）",
                                   run_meta.get("run_id"))
                raise
            # render run 收口：渲染产物已落盘，本次"渲染运行"自身也必须离开
            # running（否则任何后续 render-only / 读库方都会把最新行当成
            # "他进程在跑"——状态库任何时刻无歧义）
            store.mark("completed", "render_only")
            store.release_lock()
            self._print_budget_summary()
            logger.info("--render-only 收口完成 out=%s", str(sys_root))
            return EXIT_OK
        finally:
            store.close()

    def _infer_lens_status(self, store: StateStore) -> None:
        """--render-only 时按库内数据有无推断四透镜状态（诚实声明，非猜测）。

        口径：
          - ui：pages 表非空 → collected（遍历事实存在）；
          - api：endpoints 非空 → collected；pages 非空但 endpoints 空也按
            collected（纯 SSR 站点零端点属采集成功事实）——保守无法区分，
            以 pages 有无代 UI/API 联合事实，ui/pages 空时 skipped；
          - db / redis：对应表非空 → collected，否则 skipped
            （无法从状态库还原当年是"未配置"还是"驱动缺失"，
            skip_reason 如实声明'历史状态不可考'）。

        Args:
            store: 已持锁状态库。
        """
        stats = store.stats()
        if int(stats.get("pages_total") or 0) > 0:
            self._lens_report.set("ui", "collected")
            self._lens_report.set("api", "collected")
        else:
            self._lens_report.set("ui", "skipped", "历史采集状态不可考（--render-only 不重探测）")
            self._lens_report.set("api", "skipped", "历史采集状态不可考（--render-only 不重探测）")
        if int(stats.get("db_tables_total") or 0) > 0:
            self._lens_report.set("db", "collected")
        else:
            self._lens_report.set("db", "skipped", "DB 无采集数据（历史原因不可考：未配置/驱动缺失/连接失败）")
        if int(stats.get("redis_patterns_total") or 0) + int(stats.get("redis_keys_total") or 0) > 0:
            self._lens_report.set("redis", "collected")
        else:
            self._lens_report.set("redis", "skipped", "Redis 无采集数据（历史原因不可考：未配置/驱动缺失/连接失败）")

    # ------------------------------------------------------------------
    # SFD 专家详说分支（ARCH-SFD-001 §3.2 薄封装，REQ-SFD-005）
    # ------------------------------------------------------------------

    def _run_detailed_doc(self) -> int:
        """--detailed-doc 薄封装：组合拒绝判定 → 必填校验 → 终稿保护 → 详说编排。

        判定顺序（ARCH §3.2 规范条款，逐步收口绝不静默忽略）：
          1. --fresh/--resume/--skip-llm-phase 任一为真 → SuConfigError exit 2
             （详说只消费既有落盘产物，与采集生命周期参数组合无意义；
             互斥违例中 argparse 只拦三模式互斥，本三项属代码层显式拒绝）；
          2. --out / --system-id 必填（两 SFD 模式同口径，P1-5b/P2-12）；
          3. 既有终稿 status: final 且未 --force → exit 2（P1-5d，
             detailed_doc.check_existing_final 判定）；
          4. 打开状态库（只读用途：precheck 只调 read_latest_run/stats），
             库缺失时注入 None 走"run 状态不可考"降级口径（AP-3）。
        本分支**不要求 playwright/凭据/配置**（REQ-SFD-005），不构造
        SuConfig，不 acquire_lock。SIGINT：详说阶段全部是原子写文件操作，
        无锁无状态库写面，KeyboardInterrupt 由 run() 兜底以 130 退出
        （半成品不覆写上一版由 write_text_atomic 保证，PRD E-6）。

        Returns:
            int: 0 成功 / 2 前置违例（DetailedDocError=SuConfigError 收口）。
        """
        args = self._args
        self._reject_collection_lifecycle_flags("--detailed-doc")
        out_root, system_id = self._require_out_and_system_id("--detailed-doc")
        _setup_logging(bool(getattr(args, "verbose", False)))
        # 既有终稿 status: final 保护（先于编排——避免无效路径上白跑素材包）
        paths = _sfd_build_paths(out_root, system_id)
        _sfd_check_existing_final(
            paths, bool(getattr(args, "force", False)))
        store = self._open_readonly_store(paths)
        try:
            return run_detailed_doc(out_root, system_id, store=store)
        finally:
            if store is not None:
                store.close()

    def _run_assemble(self) -> int:
        """--assemble 薄封装：组合拒绝判定 → 必填校验 → 装配编排（轻校验在层内）。

        判定顺序与 _run_detailed_doc 一致（P1-5a/P1-5b）：
          1. --fresh/--resume/--skip-llm-phase 组合 → 显式 exit 2；
          2. --out / --system-id 必填；
          3. 装配编排 run_assemble（detailed/ 缺项、既有终稿 status: final
             保护与漂移锚点判定均在 detailed_doc.run_assemble 轻校验内收口）。

        Returns:
            int: 0 成功（含降级出稿）/ 2 前置违例或凭据扫描命中。
        """
        args = self._args
        self._reject_collection_lifecycle_flags("--assemble")
        out_root, system_id = self._require_out_and_system_id("--assemble")
        _setup_logging(bool(getattr(args, "verbose", False)))
        return run_assemble(out_root, system_id,
                            force=bool(getattr(args, "force", False)))

    def _reject_collection_lifecycle_flags(self, mode: str) -> None:
        """SFD 模式与采集生命周期参数的组合拒绝（ARCH §3.2 判定第 1 步）。

        显式性判定（绝不静默忽略三参数，同时不误杀默认值）：
          - --skip-llm-phase：store_true，args 为真即用户显式给出；
          - --fresh：store_false(dest=resume)，args.resume 为假即显式给出；
          - --resume：dest=resume default=True，无法从解析结果值区分显式
            与否——改用 sys.argv 扫描判定（main 以 parse_args() 默认 argv
            解析 sys.argv[1:]，同进程同口径成立）；显式给出同样拒绝。

        Args:
            mode: 当前模式名（--detailed-doc / --assemble，进报错文案）。

        Raises:
            SuConfigError: 任一参数显式给出（exit_code=2，message 前缀
            [SFD]，ARCH §3.2 "绝不静默忽略"红线）。
        """
        args = self._args
        conflict: List[str] = []
        if bool(getattr(args, "skip_llm_phase", False)):
            conflict.append("--skip-llm-phase")
        if getattr(args, "resume", True) is False:
            # --fresh 翻转 resume→False（互斥组内 --resume 恒 True，不误报）
            conflict.append("--fresh")
        if "--resume" in sys.argv[1:]:
            conflict.append("--resume")
        if conflict:
            raise SuConfigError(
                "[SFD] {0} 与采集生命周期参数（{1}）组合无意义，拒绝执行".format(
                    mode, "、".join(conflict)),
                hints=["详说阶段只消费既有落盘产物；采集/重跑参数请先完成 "
                       "SU 流水线（含 --render-only 收口）再进入详说模式"])

    def _require_out_and_system_id(self, mode: str) -> "tuple":
        """SFD 两模式 --out / --system-id 必填校验（P1-5b/P2-12 同口径）。

        Args:
            mode: 当前模式名（进报错文案，仿 _run_render_only :498-510 口径）。

        Returns:
            tuple[Path, str]: (输出根目录, 系统标识)。

        Raises:
            SuConfigError: 任一缺失（exit_code=2）。
        """
        args = self._args
        out_raw = getattr(args, "out", None)
        if not out_raw:
            raise SuConfigError(
                "{0} 必须显式提供 --out <目录>（指向 SU 已收口的输出根目录）".format(mode),
                hints=["示例：python scripts/system_understanding.py "
                       "--out docs/system-understanding --system-id <id> {0}".format(mode)])
        system_id = getattr(args, "system_id", None)
        if not system_id:
            # SFD 模式无凭据面，system_id 唯一合法来源是显式 --system-id
            raise SuConfigError(
                "{0} 必须显式提供 --system-id <输出子目录名>（SU 运行的目录名）".format(mode),
                hints=["输出目录结构为 <out>/<system_id>/，--system-id 即其中的目录名"])
        return Path(out_raw), str(system_id)

    @staticmethod
    def _open_readonly_store(paths) -> Optional[StateStore]:
        """打开详说只读状态库（read_latest_run/stats 两个只读方法面）。

        状态库缺失（拷贝/归档场景）返回 None → precheck 走"run 状态不可考"
        宽松口径（ARCH §2.2.1 第 5 步，AP-3 缺失即声明）；损坏库同样按
        不可考降级（详说不做任何状态库写操作，保守放行由文件面校验兜底）。

        Args:
            paths: detailed_doc.DetailedPaths（取 state_db 路径）。

        Returns:
            Optional[StateStore]: 可用状态库（调用方负责 close()）；
            缺失/损坏时 None。
        """
        db_path = paths.state_db
        if not db_path.is_file():
            return None
        try:
            # 构造即建 schema（IF NOT EXISTS 语义），对既有 completed 库
            # 零副作用——详说不持锁（precheck 只读，REQ-SFD-001 AC3）
            return StateStore(db_path, paths.sys_root.name)
        except Exception:  # noqa: BLE001 - 损坏库按"状态不可考"降级（AP-3）
            logger.warning("详说只读状态库打开失败（%s），按 run 状态不可考降级",
                           str(db_path))
            return None

    # ------------------------------------------------------------------
    # 阶段 0：预检
    # ------------------------------------------------------------------

    def _phase_preflight(self) -> PreflightReport:
        """阶段 0：三级配置校验后的连通性预检（REQ-SU-003）。

        致命项失败（site / dep:playwright）→ 中文报错 exit 3；
        可降级项 → 对应透镜登记 skip_reason（渲染层显式声明），流程继续。

        Returns:
            PreflightReport: 预检报告（state/preflight.json 已由 Preflight 落盘）。

        Raises:
            SuError(exit=3): 致命预检项失败（site 不可达）。
        """
        assert self._cfg is not None and self._deps is not None
        report = Preflight(self._cfg, self._deps).run_all()
        self._preflight_report = report
        if not report.ok:
            reasons = "；".join(
                "{0}：{1}".format(item.name, item.reason or "无详情")
                for item in report.items
                if item.name in report.fatal_failed)
            raise SuError(
                message="预检致命项失败，流水线终止（{0}）".format(reasons),
                code="preflight_fatal",
                exit_code=EXIT_UNREACHABLE,
                hints=[
                    "确认目标系统入口可从本机访问（VPN/防火墙/服务状态）",
                    "playwright 缺失请按报错安装命令安装",
                    "详见 state/preflight.json",
                ],
            )
        # playwright 缺失：deps 层报错（含安装命令，exit 5）
        self._deps.require_playwright()
        logger.info("preflight ok=%s fatal=%s degraded=%s",
                    report.ok, report.fatal_failed, report.degraded)
        return report

    # ------------------------------------------------------------------
    # 阶段 1：登录
    # ------------------------------------------------------------------

    def _phase_login(self, store: StateStore) -> None:
        """阶段 1：Playwright 自动登录 + storage_state 落盘（REQ-SU-004）。

        打开 chromium context/page 并执行登录；page/context 与 playwright
        实例句柄挂到 self 供阶段 2 复用（单 Page 串行，AP-6）。

        Args:
            store: 状态库（BrowserLogin 写 login_session 判定依据，脱敏）。

        Raises:
            SuLoginError: 登录失败（exit 4，无半成品文档）。
            SuDepsError: playwright 缺失（exit 5）。
        """
        assert self._cfg is not None and self._deps is not None
        pw_module = self._deps.require_playwright()
        # 兼容两种注入形态：playwright 包本体（sync_playwright 属性入口）
        # 或 sync_playwright 函数本身（单测注入 fake 模块时常见）
        sync_entry = getattr(pw_module, "sync_playwright", pw_module)
        # 2026-09-28 e2e 修复：真实 import playwright 包本体时，`playwright`
        # 属性是同名**子模块**（不可调用）而非 sync_playwright 函数——
        # getattr 会错误命中该子模块导致 `sync_entry().start()` TypeError。
        # 不可调用时回退 importlib 显式解析 sync_api.sync_playwright 函数。
        if not callable(sync_entry):
            import importlib
            sync_entry = importlib.import_module(
                "playwright.sync_api").sync_playwright
        limiter = RateLimiter(self._cfg.budget.delay_ms)
        login = BrowserLogin(self._cfg, pw_module, limiter)
        # playwright driver 子进程 pid（SIGINT 看门狗解挂专用）：跨线程
        # browser.close() 因 greenlet 亲和性不可用（探针实证会原生崩溃），
        # 看门狗唯一安全的解挂手段是向 driver 子进程发信号断开 transport
        # （见 site_crawler._force_disconnect_playwright 注释）。钩子必须在
        # start() 之前安装——driver 正是在 start() 内被 transport spawn。
        # 取不到时为空列表——看门狗相应退化为纯信号快路径值守，
        # 发送侧 SIGINT 重发仍是最终兜底。
        driver_pids: list = []
        _pw_hook, _pw_hook_restore = _install_driver_pid_hook(driver_pids)
        try:
            # sync_playwright() 返回可 start() 的上下文对象；start() 产出可 launch 实例
            with _pw_hook:
                self._pw_instance = sync_entry().start()
        finally:
            _pw_hook_restore()
        self._driver_pids = list(driver_pids)
        try:
            self._browser = self._pw_instance.chromium.launch(
                headless=not self._cfg.headed)
            self._context = self._browser.new_context()
            self._page = self._context.new_page()
            outcome = login.login(self._page, self._context)
            logger.info("登录成功 judged_by=%s", outcome.judged_by)
        except Exception:
            # 登录失败/异常：立即释放浏览器（不产出半成品文档，exit 4 收口）
            self._close_browser()
            raise
        # REQ-SU-004.4 接线：login 协作者挂到 self 供阶段 2 注入 crawler
        # ——遍历期检测到回跳登录页时由 crawler 调
        # login.relogin_if_needed(page, context) 自动重登（≤3 次，超限
        # SuLoginError exit 4；重登导航走 BrowserLogin 内同一限速阀门）
        self._login = login

    # ------------------------------------------------------------------
    # 阶段 2：采集（四透镜失败隔离，NFR-SU-004）
    # ------------------------------------------------------------------

    def _phase_collect(self, store: StateStore) -> None:
        """阶段 2：UI 遍历 + API 观测 + DB 内省 + Redis 扫描（失败互相隔离）。

        隔离矩阵（§8.3）：
          - UI+API（crawler/observer 同进程共生）异常 → 双双 failed，
            流程继续跑 DB/Redis 透镜；
          - DB：配置缺失/驱动缺失 → skipped（原因含安装命令）；运行时异常 →
            failed；
          - Redis：同上。
        任何透镜失败**都不终止**其它透镜（NFR-SU-004）。

        Args:
            store: 已持锁状态库。
        """
        assert self._cfg is not None and self._deps is not None
        cfg = self._cfg
        budget = BudgetTracker(cfg.budget)
        self._lens_report.budget_tracker = budget
        limiter = RateLimiter(cfg.budget.delay_ms)

        # ---- UI + API 透镜（共生：observer 挂在 crawler 页面上）----
        try:
            observer = ApiObserver(store)
            # login/context 注入（REQ-SU-004.4）：login 为阶段 1 成功后挂上
            # 的 BrowserLogin 协作者（_phase_login 恒先于本阶段执行，正常
            # 流水线必非 None；显式 getattr 兜底仅为属性缺位的极端形态
            # 保守降级为"不启用重登"，不引入新失败面）
            crawler = SiteCrawler(
                cfg, self._page, store, observer, limiter,
                context=self._context,
                # REQ-SU-004.4：阶段 1 成功即赋值 self._login（_phase_login
                # 恒先于本阶段执行，异常路径已 exit 4 收口到不了这里）
                login=self._login,
            )
            crawler.install_route_guard(self._context)  # 红线④：先于任何导航
            # SIGINT 看门狗接线（中断稳健性）：登记 playwright driver 子进程
            # pid——/slow 等 CDP 挂起窗口内信号处理被拖住时，看门狗有界等待
            # 后杀掉 driver 断开管道解挂主线程（跨线程 browser.close() 因
            # greenlet 亲和性不可用，探针实证会原生崩溃）
            crawler.register_interrupt_browser(self._driver_pids)
            self._crawler = crawler
            report = crawler.crawl()
            self._lens_report.crawl_report = report
            self._lens_report.set("ui", "collected")
            self._lens_report.set("api", "collected")
            logger.info("UI/API 采集完成 pages=%s actions=%s blocked=%s exhausted=%s",
                        report.pages_visited, report.actions_total,
                        report.blocked_total, report.exhausted_dimension)
        except (SuLoginError, SuError):
            # 登录类/结构化致命错误不属"透镜失败"——上抛按各自退出码收口
            self._lens_report.set("ui", "failed", "遍历阶段结构化错误终止")
            self._lens_report.set("api", "failed", "遍历阶段结构化错误终止")
            raise
        except Exception as exc:  # noqa: BLE001 - 透镜失败隔离（NFR-SU-004）
            self._lens_report.set("ui", "failed", "UI 遍历运行时失败：{0}".format(_brief(exc)))
            self._lens_report.set("api", "failed", "UI 遍历失败导致 API 观测中止")
            logger.error("UI/API 透镜失败（已隔离，继续其它透镜）：%s", _brief(exc))

        # ---- DB 透镜 ----
        if cfg.database is None:
            self._lens_report.set("db", "skipped", "未配置 database 段（DB 透镜跳过）")
        else:
            driver = self._deps.require_db_driver(cfg.database.engine) if self._deps else None
            if driver is None:
                hint = (self._deps.db_driver_install_hint(cfg.database.engine)
                        if self._deps else "安装对应驱动")
                self._lens_report.set(
                    "db", "skipped",
                    "{0} 驱动缺失（{1}），DB 透镜降级跳过".format(cfg.database.engine, hint))
            else:
                inspector = DbInspector(cfg.database, store, driver)
                try:
                    inspector.connect()
                    n_tables = inspector.collect_schema()
                    inspector.sample_tables(cfg.budget.sample_rows)
                    inspector.prescreen_implicit_fks()
                    self._lens_report.set("db", "collected")
                    logger.info("DB 透镜完成 tables=%s", n_tables)
                except Exception as exc:  # noqa: BLE001 - 透镜失败隔离
                    self._lens_report.set("db", "failed",
                                          "DB 内省运行时失败：{0}".format(_brief(exc)))
                    logger.error("DB 透镜失败（已隔离）：%s", _brief(exc))
                finally:
                    inspector.close()

        # ---- Redis 透镜 ----
        if cfg.redis is None:
            self._lens_report.set("redis", "skipped", "未配置 redis 段（Redis 透镜跳过）")
        else:
            redis_module = self._deps.require_redis() if self._deps else None
            if redis_module is None:
                hint = self._deps.redis_install_hint() if self._deps else "pip install 'redis>=5.0.0'"
                self._lens_report.set(
                    "redis", "skipped",
                    "redis 驱动缺失（{0}），Redis 透镜降级跳过".format(hint))
            else:
                client = None
                try:
                    client = redis_module.Redis(
                        host=cfg.redis.host, port=cfg.redis.port,
                        # reveal 边界②：Redis 建连（§5.1 白名单位置）
                        password=cfg.redis.password.reveal() or None,
                        db=cfg.redis.db, decode_responses=True,
                        socket_timeout=10, socket_connect_timeout=10)
                    inspector = RedisInspector(
                        cfg.redis, store, client, max_keys=cfg.budget.redis_max_keys)
                    result = inspector.collect()
                    self._lens_report.set("redis", "collected")
                    logger.info("Redis 透镜完成 keys=%s patterns=%s truncated=%s",
                                result.get("keys_total"), result.get("patterns_total"),
                                result.get("truncated"))
                except Exception as exc:  # noqa: BLE001 - 透镜失败隔离
                    self._lens_report.set("redis", "failed",
                                          "Redis 采集运行时失败：{0}".format(_brief(exc)))
                    logger.error("Redis 透镜失败（已隔离）：%s", _brief(exc))
                finally:
                    if client is not None:
                        try:
                            client.close()
                        except Exception:  # noqa: BLE001 - 关闭失败不影响结论
                            pass

    # ------------------------------------------------------------------
    # 阶段 3：关联分析
    # ------------------------------------------------------------------

    def _phase_relations(self, store: StateStore) -> None:
        """阶段 3：三角关联确定性证据（REQ-SU-016/017，AP-1 只出证据）。

        分析器异常同样隔离（证据缺失只影响第 5/8 节证据密度，不致命）。

        Args:
            store: 已持锁状态库。
        """
        analyzer = RelationAnalyzer(store)
        try:
            total = analyzer.persist(
                analyzer.page_to_api() + analyzer.api_to_table() + analyzer.redis_to_entity())
            logger.info("关联证据完成 count=%s", total)
        except Exception as exc:  # noqa: BLE001 - 证据生成失败不终止渲染
            logger.error("关联分析失败（已隔离，文档证据节将缺失）：%s", _brief(exc))

    # ------------------------------------------------------------------
    # 阶段 4：LLM 回填桥（replace_findings 唯一调用方，§1.2 边界规则 2）
    # ------------------------------------------------------------------

    def _phase_llm_bridge(self, store: StateStore,
                          findings_override: Optional[List[Dict[str, Any]]] = None) -> None:
        """findings 回填的单一入口（§2.3.1 / §6.3）。

        分支语义：
          - ``--skip-llm-phase``（完整流水线）：跳过 findings 处理直接骨架渲染
            ——findings 表保持为空，第 5/7 节渲染"待 LLM 语义回填"（AC3）；
          - ``--render-only``（findings_override 提供）：读 understanding.json
            的 findings 段 → validate → replace_findings 全量替换（status=proposed）；
            校验失败抛 SuConfigError（exit 2，整批拒绝中文列项）。

        Args:
            store: 已持锁状态库。
            findings_override: --render-only 分支从 understanding.json 读入的
                findings 数组；None 表示完整流水线（不触碰 findings 表）。

        Raises:
            SuConfigError: findings schema 校验失败（exit 2，由 run() 收口）。
        """
        if findings_override is None:
            if self._skip_llm_phase:
                logger.info("--skip-llm-phase：跳过 findings，产出骨架文档（第 5/7 节待回填）")
            else:
                logger.info("完整流水线：findings 表保持既有状态（两阶段工作流由宿主 LLM 回填）")
            return
        errors = store.validate_findings_schema(findings_override)
        if errors:
            raise SuConfigError(
                "findings 校验未通过（共 {0} 项违约，整批拒绝）：{1}".format(
                    len(errors), "；".join(errors)),
                hints=[
                    "修正 understanding.json 的 findings 段后重跑 --render-only",
                    "回填契约见 docs/spec/role-prompts/su-llm-backfill.md",
                ])
        # 本调用点必须位于模块 'system_understanding'（state_store 侧栈断言契约）
        store.replace_findings(findings_override)
        logger.info("findings 入库完成 count=%s status=proposed", len(findings_override))

    # ------------------------------------------------------------------
    # SIGINT（§8.2 / REQ-SU-019.3）
    # ------------------------------------------------------------------

    def _install_signal_handlers(self, store: StateStore) -> None:
        """注册 SIGINT 处理器：flush → mark('interrupted') → 释放锁 → exit 130。

        仅主线程可注册信号（acquire_lock 之后立即调用——run() 主线程保证）；
        handler 内只做状态库收口三件事 + 进程退出，绝不做采集/渲染等可中断工作。

        Args:
            store: 已持锁状态库。
        """
        def _handler(signum: int, frame: Any) -> None:  # noqa: ANN001 - 签名固定
            """SIGINT 处理器（状态先于产物原则：先落 interrupted 再退出）。"""
            # 落点标志先行置位：run() 收口点据此拒绝把 interrupted 覆写回
            # completed（渲染/收口段收到信号的形态，见 run() 中断落点追踪）
            self._interrupt_exit_requested.set()
            # 通知采集看门狗置位中断标志：本处理器只在主线程从 CDP 阻塞
            # 调用苏醒时才会执行，正常收口路径下看门狗轮询到 run 状态收口
            # 即退出；若收口三步被后续阻塞调用（_close_browser 等）再次
            # 挂起，看门狗在容忍窗口后强制断开浏览器保证进程可退出
            crawler = getattr(self, "_crawler", None)
            if crawler is not None:
                try:
                    crawler.notify_sigint()
                except Exception:  # noqa: BLE001 - 通知失败不影响收口三步
                    pass
            logger.warning("收到 SIGINT：flush 状态并标记 interrupted（可 --resume 续跑）")
            self._handle_interrupt_exit()
            sys.exit(EXIT_INTERRUPTED)

        try:
            signal.signal(signal.SIGINT, _handler)
        except ValueError:
            # 非主线程（单测场景）：信号注册不可用，由 KeyboardInterrupt 兜底
            logger.debug("当前线程非主线程，SIGINT 由 KeyboardInterrupt 兜底处理")

    def _handle_interrupt_exit(self) -> None:
        """中断收口三步（flush → interrupted → 释放锁；全部容错尽力而为）。"""
        store = self._store
        if store is None:
            return
        try:
            store.flush()
        except Exception:  # noqa: BLE001 - 收口尽力而为
            logger.error("SIGINT 收口：wal_checkpoint 失败（数据已提交性不受影响）")
        try:
            store.mark("interrupted", "sigint")
            store.release_lock()
        except Exception:  # noqa: BLE001 - 未持锁（锁竞态已失）等场景
            logger.error("SIGINT 收口：run_meta 状态流转失败（请以状态库实际值为准）")
        self._close_browser()

    def _close_browser(self) -> None:
        """尽力关闭浏览器栈（page/context/browser/playwright 实例）。"""
        for attr in ("_page", "_context", "_browser"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.close()
                except Exception:  # noqa: BLE001 - 收口尽力而为
                    pass
        instance = getattr(self, "_pw_instance", None)
        if instance is not None:
            try:
                instance.stop()
            except Exception:  # noqa: BLE001 - 收口尽力而为
                pass

    # ------------------------------------------------------------------
    # 预算摘要与配置辅助
    # ------------------------------------------------------------------

    def _print_budget_summary(self) -> None:
        """打印预算消耗摘要（NFR-SU-006：页面数/动作数/拦截数/耗时）。"""
        if self._store is None:
            return
        stats = self._store.stats()
        tracker = self._lens_report.budget_tracker
        parts = [
            "pages={0}(done={1})".format(stats.get("pages_total"), stats.get("pages_done")),
            "actions={0}".format(stats.get("actions_total")),
            "t3_unexecuted={0}".format(stats.get("actions_t3_unexecuted")),
            "blocked={0}".format(stats.get("blocked_events_total")),
            "db_tables={0}".format(stats.get("db_tables_total")),
            "redis_keys={0}".format(stats.get("redis_keys_total")),
            "endpoints={0}".format(stats.get("endpoints_total")),
            "findings={0}".format(stats.get("findings_total")),
        ]
        if tracker is not None:
            summary = tracker.summary()
            parts.append("elapsed={0:.1f}s".format(summary.elapsed_seconds))
            if summary.exhausted_dimension:
                parts.append("exhausted={0}".format(summary.exhausted_dimension))
        print("预算消耗摘要：" + " ".join(parts))

    def _config_snapshot(self):
        """构造 config_snapshot（run_meta 用，经 redact 绝无明文凭据，REQ-SU-002）。

        Returns:
            RedactedDict: 运行参数快照（预算/引擎/入口等事实字段；
            SensitiveStr 凭据经 redact 自动落 ***REDACTED***）。
        """
        from su.config import redact  # 局部导入：仅本方法使用
        cfg = self._cfg
        assert cfg is not None
        return redact({
            "base_url": cfg.system.base_url,
            "login_url": cfg.system.login_url,
            "allowed_origins": list(cfg.system.allowed_origins),
            "db_engine": cfg.database.engine if cfg.database else None,
            "db_host": cfg.database.host if cfg.database else None,
            "db_database": cfg.database.database if cfg.database else None,
            "redis_host": cfg.redis.host if cfg.redis else None,
            "redis_db": cfg.redis.db if cfg.redis else None,
            "max_pages": cfg.budget.max_pages,
            "max_depth": cfg.budget.max_depth,
            "max_actions_per_page": cfg.budget.max_actions_per_page,
            "time_budget_minutes": cfg.budget.time_budget_minutes,
            "delay_ms": cfg.budget.delay_ms,
            "page_timeout_ms": cfg.budget.page_timeout_ms,
            "sample_rows": cfg.budget.sample_rows,
            "redis_max_keys": cfg.budget.redis_max_keys,
            "resume": cfg.resume,
            "headed": cfg.headed,
            "skip_llm_phase": cfg.skip_llm_phase,
        })

    def _render_only_config(self, system_id: str, out_root: Path) -> SuConfig:
        """--render-only 的渲染参数壳配置（不要求凭据——渲染层只消费这些字段）。

        system 段仅 out_dir/system_id/budget 被渲染器读取；base_url 等运行期
        事实展示读的是 run_meta.config_snapshot（此前运行的真实快照），
        本壳中的占位值不落任何产物。

        Args:
            system_id: 输出子目录名。
            out_root: --out 指定的输出根目录。

        Returns:
            SuConfig: 渲染壳配置。
        """
        from su.config import DatabaseConfig, LoginSelectors, RunBudget, SystemConfig  # noqa: F401
        from su.dto import SensitiveStr
        budget = _budget_from_args(self._args)
        system = SystemConfig(
            base_url="(render-only 无目标系统)",
            login_url="/login",
            username=SensitiveStr(""),
            password=SensitiveStr(""),
            login_selectors=LoginSelectors(),
        )
        return SuConfig(
            system=system,
            database=None,
            redis=None,
            budget=budget,
            out_dir=out_root,
            system_id=system_id,
            resume=True,
            headed=False,
            skip_llm_phase=True,
        )


def _cfg_with_out(cfg: SuConfig, out_path: Path) -> SuConfig:
    """返回仅替换 out_dir 的配置副本（save(output_dir) 门面语义）。

    Args:
        cfg: 原配置。
        out_path: 新输出根目录。

    Returns:
        SuConfig: dataclasses.replace 产物（浅拷贝，budget 等共享只读）。
    """
    import dataclasses
    return dataclasses.replace(cfg, out_dir=out_path)


def _budget_from_args(args: argparse.Namespace):
    """从 CLI 参数构造 RunBudget（--render-only 壳配置用，未给参数保持默认）。

    Args:
        args: argparse 解析结果。

    Returns:
        RunBudget: 预算对象（渲染层不消费，仅满足 SuConfig 结构完整性）。
    """
    from su.config import RunBudget
    budget = RunBudget()
    for field_name in ("max_pages", "max_depth", "max_actions_per_page",
                       "time_budget_minutes", "delay_ms", "page_timeout_ms",
                       "sample_rows", "redis_max_keys"):
        value = getattr(args, field_name, None)
        if value is not None:
            setattr(budget, field_name, int(value))
    return budget


def _brief(exc: BaseException) -> str:
    """异常 → 单行摘要文本（进透镜 skip_reason 前由渲染落盘管线再 scrub）。

    Args:
        exc: 运行时异常。

    Returns:
        str: ``类型名: 消息`` 形态（压平空白、截断 200 字符）。
    """
    message = " ".join(str(exc).split())[:200]
    return "{0}: {1}".format(type(exc).__name__, message)


# ---------------------------------------------------------------------------
# argparse（REQ-SU-020 全参数表，全部中文 help，AC1）
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器（参数表与 PRD REQ-SU-020 逐行对齐）。

    Returns:
        argparse.ArgumentParser: 已配置全部参数与中文 help 的解析器
            （非法参数由 argparse 标准报错，退出码 2）。
    """
    parser = argparse.ArgumentParser(
        prog="system_understanding.py",
        description=(
            "既有系统反向理解（SU）：对遗留 Web 系统 + MySQL/PostgreSQL + Redis 做只读"
            "反向采集，产出《系统功能理解文档》（10 节）与机读 understanding.json。"
            "凭据推荐 --config JSON 文件（chmod 600）或环境变量（SU_SYSTEM_USERNAME/"
            "SU_SYSTEM_PASSWORD/SU_DB_PASSWORD/SU_REDIS_PASSWORD）；--db-url/--redis-url "
            "会进入 shell 历史，泄露风险自担。DB 账号只需 SELECT 权限。"
        ),
    )
    parser.add_argument("--config", metavar="PATH", default=None,
                        help="凭据/目标 JSON 配置文件（REQ-SU-001；优先级 CLI > env > 文件）")
    parser.add_argument("--system-url", default=None,
                        help="目标系统入口 URL（覆盖配置文件 system.base_url）")
    parser.add_argument("--username", default=None,
                        help="系统登录账号（覆盖配置文件；亦可用 SU_SYSTEM_USERNAME）")
    parser.add_argument("--password", default=None,
                        help="系统登录密码（覆盖配置文件；亦可用 SU_SYSTEM_PASSWORD；"
                             "命令行传入进入 shell 历史，风险自担）")
    parser.add_argument("--db-url", default=None, metavar="URL",
                        help="数据库连接串 mysql://user:pass@host:3306/db 或 "
                             "postgresql://...（整体覆盖 database 段；shell 历史风险自担，"
                             "推荐配置文件/环境变量）")
    parser.add_argument("--redis-url", default=None, metavar="URL",
                        help="Redis 连接串 redis://:pass@host:6379/0（整体覆盖 redis 段）")
    parser.add_argument("--out", default=None, metavar="DIR",
                        help="输出根目录（默认 docs/system-understanding/，产物落 <out>/<system_id>/；"
                             "--render-only 时必须显式提供并指向前次运行目录）")
    parser.add_argument("--system-id", default=None,
                        help="输出子目录名（默认由目标主机名派生）")
    parser.add_argument("--max-pages", type=int, default=None,
                        help="页面预算上限（默认 100）")
    parser.add_argument("--max-depth", type=int, default=None,
                        help="BFS 深度上限（默认 6）")
    parser.add_argument("--max-actions-per-page", type=int, default=None,
                        help="单页候选动作上限（默认 30）")
    parser.add_argument("--time-budget-minutes", type=int, default=None,
                        help="墙钟时间预算分钟数（默认 60）")
    parser.add_argument("--delay-ms", type=int, default=None,
                        help="全局限速间隔毫秒（默认 1500；等效 QPS ≤ 1/delay）")
    parser.add_argument("--page-timeout-ms", type=int, default=None,
                        help="单页导航超时毫秒（默认 30000）")
    parser.add_argument("--sample-rows", type=int, default=None,
                        help="DB 每表采样行数（默认 10，上限 50）")
    parser.add_argument("--redis-max-keys", type=int, default=None,
                        help="Redis 键采集预算（默认 5000）")
    parser.add_argument("--allowed-origins", default=None, metavar="ORIGINS",
                        help="追加浏览器白名单域（逗号分隔；默认 base_url 同源）")
    parser.add_argument("--headed", action="store_true",
                        help="有头调试模式（观察遍历过程；生产建议关闭）")
    parser.add_argument("--storage-state", default=None, metavar="PATH",
                        help="人工已登录态 storage_state 文件旁路注入（验证码/2FA 场景）")
    resume_group = parser.add_mutually_exclusive_group()
    resume_group.add_argument("--resume", dest="resume", action="store_true", default=True,
                              help="断点续跑：interrupted 状态复用既有进度（默认策略）")
    resume_group.add_argument("--fresh", dest="resume", action="store_false",
                              help="归档旧状态目录 state.archive.<ts>/ 后全新重跑")
    parser.add_argument("--skip-llm-phase", action="store_true",
                        help="只跑确定性采集，产出『待 LLM 语义回填』骨架文档"
                             "（配合宿主 LLM 两阶段工作流）")
    # SFD 三模式互斥组（ARCH-SFD-001 §3.1）：--render-only 为既有行迁入组内，
    # 参数名/help/语义零变化（仅参数容器位置变化，属既有 CLI 行为面唯一改动点）；
    # 两个 flag 同给时 argparse 标准报错 SystemExit(2)——恰为 REQ-SFD-005 AC1
    # 要求的 exit 2，无需代码层重复互斥判断
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--render-only", action="store_true",
                            help="仅渲染：输出目录已有 completed/interrupted 状态库且 "
                                 "understanding.json 含 findings 段时，跳过采集直接校验入库 "
                                 "findings 并重渲染全部产物（findings 校验失败退出码 2）")
    mode_group.add_argument("--detailed-doc", action="store_true",
                            help="专家详说（第三阶段）：前置校验 SU 产物 → 生成五专家"
                                 "素材包与 8 节大纲骨架 → 打印派发指引（REQ-SFD-001~005；"
                                 "要求 findings 已回填且锚定 run（understanding.json "
                                 "meta.run_id 行）∈ {completed, interrupted}——2026-09-30 "
                                 "审查修订 P0-2：锚定口径非最新行）")
    mode_group.add_argument("--assemble", action="store_true",
                            help="装配详说终稿：读取 detailed/sections/ 专家草稿 → "
                                 "E-n 引用校验 → 凭据扫描 → 原子写 SYSTEM_FUNCTION_DOC.md "
                                 "+ assembly-report.json（可独立于 --detailed-doc 反复执行）")
    # 互斥组之外新增独立开关（2026-09-30 审查修订 P1-5d）
    parser.add_argument("--force", action="store_true",
                        help="仅与 --detailed-doc/--assemble 配合：覆盖既有终稿"
                             "（头部 status: final）时跳过 exit 2 保护；仅此场景使用")
    parser.add_argument("--verbose", action="store_true",
                        help="调试日志（DEBUG 级；全程仍过 RedactingFormatter 脱敏）")
    return parser


def main() -> int:
    """CLI 主入口：argparse 解析 → SystemUnderstanding 编排 → 退出码返回。

    Returns:
        int: 进程退出码（0/2/3/4/5/130；argparse 非法参数自带 SystemExit(2)）。
    """
    parser = build_arg_parser()
    args = parser.parse_args()
    app = SystemUnderstanding(args)
    try:
        return app.run()
    except SuError as exc:  # run() 内部未捕获的漏网结构化错误（防御收口）
        print(exc.format(), file=sys.stderr)
        return int(exc.exit_code)


# 浏览器栈句柄类型注解占位（阶段 1 建连后挂载；避免无类型属性访问的静态告警）
SystemUnderstanding._pw_instance = None  # type: ignore[attr-defined]
SystemUnderstanding._browser = None  # type: ignore[attr-defined]
SystemUnderstanding._context = None  # type: ignore[attr-defined]
SystemUnderstanding._page = None  # type: ignore[attr-defined]
SystemUnderstanding._driver_pids = []  # type: ignore[attr-defined]


if __name__ == "__main__":
    sys.exit(main())
