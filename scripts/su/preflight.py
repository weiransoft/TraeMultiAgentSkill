"""SU 运行前预检（架构 ARCH-SU-001 §2.3.4，PRD REQ-SU-003）。

**职责**：在正式采集前对三类目标（站点 / 数据库 / Redis）与各软依赖做连通性
检查，产出结构化报告 ``state/preflight.json``；检查项按**致命 / 可降级**二分：

  - ``site`` / ``dep:playwright`` 失败 = 致命（UI 透镜不可用，上层按退出码 3 终止）；
  - ``database`` / ``redis`` / DB 驱动 / redis 驱动 失败 = 可降级
    （对应透镜登记 skip_reason，文档显式声明"未采集"，绝不以空数据冒充）。

安全口径（红线①②③）：
  - DB 预检自身的 ``SELECT 1`` 也经 :class:`su.db_guard.ReadOnlyGuard` 通道执行
    （预检自身也走白名单，不存在第二条执行路径）；
  - Redis 预检的 PING 不在只读命令白名单内（白名单是**采集面**口径），
    因此经 :func:`_guarded_ping` 注入的"仅 PING"受限客户端走
    :class:`su.redis_guard.RedisGuard`——守卫的 frozenset 硬约束保持完整，
    PING 的授权只存在于本文件的适配器内、且仅转发单条 PING；
  - 报告落盘前整段过 :func:`su.config.redact`（:meth:`Preflight.run_all`
    入参类型 RedactedDict + 写盘断言，§5.1 红线①四层组合）。

本模块**不顶层 import 任何软依赖**：playwright/pymysql/psycopg2/redis 模块对象
只经 :class:`su.deps.DependencyReport` 注入（REQ-SU-021）。
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from su.config import SuConfig, redact, scrub_text
from su.db_guard import ReadOnlyGuard
from su.deps import DependencyReport
from su.dto import RedactedDict, SensitiveStr
from su.redis_guard import RedisGuard

__all__ = ["PreflightItem", "PreflightReport", "Preflight"]

# 站点 HEAD 请求超时（秒，架构 §2.3.4 口径：跟随重定向、超时 10s）
_SITE_TIMEOUT_SECONDS = 10

# 预检伪装 UA（遗留 WAF 常屏蔽 Python-urllib UA，伪装浏览器 UA 降低误杀；
# 不携带任何凭据信息）
_SITE_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass
class PreflightItem:
    """单个预检项结果（§2.3.4 数据结构）。

    Attributes:
        name: 检查项名（``site`` / ``database`` / ``redis`` / ``dep:*`` …）。
        status: ``ok`` / ``failed`` / ``skipped``（架构用 Literal 表述，
            本项目兼容 Python 3.9，运行时常量口径与 §3 DDL CHECK 一致）。
        reason: 中文失败/跳过原因（成功时为 None；已 scrub 防凭据泄露）。
        degradable: True=可降级（对应透镜登记缺失声明）；
            False=致命（site/dep:playwright，非 skipped 的失败使上层终止）。
    """

    name: str
    status: str
    reason: Optional[str]
    degradable: bool

    def to_dict(self) -> RedactedDict:
        """转已脱敏 dict（run_all 报告的组成单元）。

        reason 是网络层原始报错，可能回显 URL 内嵌凭据
        （如 ``http://user:pass@host`` 的连接错误文本），入报告前必须过
        :func:`su.config.scrub_text`（其内部先剥 URL 凭据再做 PII 值形态替换）。

        Returns:
            RedactedDict: 单检查项的落盘形态。
        """
        return RedactedDict({
            "name": self.name,
            "status": self.status,
            "reason": scrub_text(self.reason) if self.reason else self.reason,
            "degradable": self.degradable,
        })


@dataclass
class PreflightReport:
    """预检汇总报告（``state/preflight.json`` 数据源）。

    Attributes:
        items: 全部检查项（按 run_all 固定顺序，报告稳定可 diff）。
        fatal_failed: 致命且失败（非 skipped）的检查项名列表——非空时上层终止。
        degraded: 可降级且未通过的检查项名列表——渲染器据此输出显式缺失声明。
    """

    items: List[PreflightItem]
    fatal_failed: List[str]
    degraded: List[str]

    @property
    def ok(self) -> bool:
        """是否可继续流水线（无致命失败即为 True，降级项不阻断）。"""
        return not self.fatal_failed

    def to_redacted(self) -> RedactedDict:
        """整段报告过 redact 管线（§5.2：落盘唯一合法 dict 形态来源）。

        Returns:
            RedactedDict: 顶层已脱敏报告 dict。
        """
        return redact({
            "items": [item.to_dict() for item in self.items],
            "fatal_failed": list(self.fatal_failed),
            "degraded": list(self.degraded),
            "ok": self.ok,
        })


class _PingOnlyClient:
    """仅暴露 PING 的受限 Redis 客户端适配器（预检专用，红线③纵深防御）。

    :class:`su.redis_guard.RedisGuard` 的白名单是**采集面**口径（不含 PING）；
    本适配器把客户端缩成单方法面——即便预检代码后续被改动，从 RedisGuard
    可达的命令面也只有 PING 一条（结构上杜绝预检越权执行其它命令）。
    """

    def __init__(self, client: Any) -> None:
        """包装真实 redis 客户端。

        Args:
            client: redis 库客户端对象（duck-typing，本模块不 import redis）。
        """
        self._client = client

    def ping(self) -> Any:
        """转发单条 PING（RedisGuard 白名单外唯一被本适配器暴露的命令）。"""
        return self._client.ping()


class _FollowRedirectOpener:
    """跟随 301/302/307/308 重定向的 HEAD opener 工厂。

    urllib 默认 opener 已支持常见重定向；此处显式构造以集中口径：
    HEAD 跟随重定向（REQ-SU-003 站点预检"跟随重定向"），最多跳转由 urllib
    内部限制（HTTPRedirectHandler max_redirections）。
    """

    @staticmethod
    def build() -> urllib.request.OpenerDirector:
        """构造带重定向处理与自定义 UA 的 opener（不含 cookie 处理器，预检无状态）。

        Returns:
            urllib.request.OpenerDirector: 预检专用 opener。
        """
        return urllib.request.build_opener(
            urllib.request.HTTPRedirectHandler(),
        )


class Preflight:
    """运行前预检器（§2.3.4 全量方法，REQ-SU-003）。

    典型用法（CLI 编排层）::

        preflight = Preflight(cfg, deps)
        report = preflight.run_all()
        if not report.ok:
            sys.exit(3)   # 致命项非空 → 退出码 3（§8.2）
    """

    def __init__(self, cfg: SuConfig, deps: DependencyReport) -> None:
        """初始化预检器。

        Args:
            cfg: 完整运行配置（system/database/redis 段决定检查面）。
            deps: 软依赖探测报告（模块对象唯一注入源，REQ-SU-021）。
        """
        self._cfg = cfg
        self._deps = deps
        # 守卫无状态，预检内复用单实例即可（与 db_inspector/redis_inspector 等效）
        self._db_guard = ReadOnlyGuard()
        self._redis_guard = RedisGuard()

    # ------------------------------------------------------------------
    # 站点检查（致命项）
    # ------------------------------------------------------------------

    def check_site(self) -> PreflightItem:
        """站点连通性检查：标准库 urllib HEAD（跟随重定向，超时 10s）。

        判定口径：凡服务器返回任意 **合法 HTTP 响应状态**（含 401/403——
        登录墙本身就是"站点存活"的证据，凭据正确性由登录阶段判定）即判 ok；
        连接层错误（DNS/拒绝连接/超时/SSL）才算失败。

        Returns:
            PreflightItem: name='site'，degradable=False（失败 → 致命，退出码 3）。
        """
        url = self._cfg.system.base_url
        request = urllib.request.Request(url, method="HEAD")
        request.add_header("User-Agent", _SITE_USER_AGENT)
        opener = _FollowRedirectOpener.build()
        try:
            response = opener.open(request, timeout=_SITE_TIMEOUT_SECONDS)
            # 拿到响应即站点可达（状态码只作 reason 附注，不判失败）
            status = getattr(response, "status", None) or response.getcode()
            try:
                response.close()
            except Exception:  # noqa: BLE001 - 关闭失败不影响连通性结论
                pass
            return PreflightItem(
                name="site", status="ok",
                reason="站点可达（HTTP {0}）".format(status),
                degradable=False,
            )
        except urllib.error.HTTPError as exc:
            # HTTPError 表示收到了响应（如 404/401/403/500）——站点是活的
            return PreflightItem(
                name="site", status="ok",
                reason="站点可达（HTTP {0}）".format(exc.code),
                degradable=False,
            )
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # URLError（DNS 失败/拒绝连接/SSL 错误）、裸 OSError（超时等）、
            # ValueError（URL 非法）→ 连接层失败（致命）
            reason = getattr(exc, "reason", None) or str(exc)
            return PreflightItem(
                name="site", status="failed",
                reason="站点 HEAD 请求失败：{0}".format(reason),
                degradable=False,
            )

    # ------------------------------------------------------------------
    # 数据库检查（可降级项）
    # ------------------------------------------------------------------

    def check_database(self) -> PreflightItem:
        """数据库预检：TCP 握手 + ``SELECT 1``（经 ReadOnlyGuard 通道执行）。

        流程（§2.3.4）：
          1. 配置段缺失 → skipped（degradable=True，文档登记"未配置"）；
          2. 驱动缺失 → failed/degradable（附安装命令，来自 DependencyReport）；
          3. 用 :meth:`_connect` 建连（会话只读加固 + 验证生效，红线②）；
          4. ``SELECT 1`` 走 :meth:`ReadOnlyGuard.execute`——**预检自身也走白名单**，
             不存在绕过校验器的第二条执行路径。

        失败一律 degradable=True：DB 透镜登记 skip_reason 后 UI/Redis 照常运行。

        Returns:
            PreflightItem: name='database'。
        """
        db_cfg = self._cfg.database
        if db_cfg is None:
            return PreflightItem(
                name="database", status="skipped",
                reason="未配置 database 段，DB 透镜跳过",
                degradable=True,
            )
        driver = self._deps.require_db_driver(db_cfg.engine)
        if driver is None:
            hint = self._deps.db_driver_install_hint(db_cfg.engine) or "安装对应驱动"
            return PreflightItem(
                name="database", status="failed",
                reason="{0} 驱动缺失，DB 透镜降级跳过（{1}）".format(db_cfg.engine, hint),
                degradable=True,
            )
        conn = None
        try:
            conn = self._connect_db(driver)
            rows = self._db_guard.execute(conn, "SELECT 1")
            if not rows:
                # SELECT 1 必然返回一行；空结果视为代理层异常响应（保守判失败）
                return PreflightItem(
                    name="database", status="failed",
                    reason="SELECT 1 无结果行（连接被代理层截断？）",
                    degradable=True,
                )
            return PreflightItem(
                name="database", status="ok",
                reason="握手 + SELECT 1 通过（会话只读加固已验证生效）",
                degradable=True,
            )
        except Exception as exc:  # noqa: BLE001 - 预检吸收全部驱动异常转结构化结论
            return PreflightItem(
                name="database", status="failed",
                reason="数据库握手/加固/SELECT 1 失败：{0}".format(_brief(exc)),
                degradable=True,
            )
        finally:
            # 预检连接即用即关（不泄漏到采集阶段；DbInspector 自行建连）
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001 - 关闭失败不改变预检结论
                    pass

    # ------------------------------------------------------------------
    # Redis 检查（可降级项）
    # ------------------------------------------------------------------

    def check_redis(self) -> PreflightItem:
        """Redis 预检：PING（经 RedisGuard 通道执行，红线③）。

        PING 不在采集面白名单 :data:`su.redis_guard.READONLY_COMMANDS` 内；
        为坚持"su/ 包唯一命令出口 = RedisGuard.call"，此处用 :class:`_PingOnlyClient`
        受限适配器（只暴露 ping()）交给守卫——守卫的白名单逻辑原样生效，
        PING 的额外授权封闭在本适配器内。

        Returns:
            PreflightItem: name='redis'，degradable=True。
        """
        redis_cfg = self._cfg.redis
        if redis_cfg is None:
            return PreflightItem(
                name="redis", status="skipped",
                reason="未配置 redis 段，Redis 透镜跳过",
                degradable=True,
            )
        redis_module = self._deps.require_redis()
        if redis_module is None:
            return PreflightItem(
                name="redis", status="failed",
                reason="redis 驱动缺失，Redis 透镜降级跳过（{0}）".format(
                    self._deps.redis_install_hint()),
                degradable=True,
            )
        client = None
        try:
            client = self._connect_redis(redis_module)
            pong = self._redis_guard.call(_PingOnlyClient(client), "PING")
            # redis-py PING 返回 True（decode_responses 开启时为 b'PONG'/'PONG'）
            if pong in (True, 1) or str(pong).upper().endswith("PONG"):
                return PreflightItem(
                    name="redis", status="ok",
                    reason="PING 通过（经 RedisGuard 通道）",
                    degradable=True,
                )
            return PreflightItem(
                name="redis", status="failed",
                reason="PING 返回异常响应：{0!r}".format(pong),
                degradable=True,
            )
        except Exception as exc:  # noqa: BLE001 - 预检吸收全部驱动异常转结构化结论
            return PreflightItem(
                name="redis", status="failed",
                reason="Redis 握手/PING 失败：{0}".format(_brief(exc)),
                degradable=True,
            )
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:  # noqa: BLE001 - 关闭失败不改变预检结论
                    pass

    # ------------------------------------------------------------------
    # 软依赖检查项（可降级；playwright 例外 = 致命）
    # ------------------------------------------------------------------

    def check_dependencies(self) -> List[PreflightItem]:
        """软依赖检查（REQ-SU-021 降级矩阵的检查面）。

        口径：
          - ``dep:playwright`` 缺失 = 致命（degradable=False；require_playwright
            的报错文案与退出码 5 由编排层统一收口，此处只登记事实）；
          - 各驱动按其**是否被配置使用**判级：未配置对应段则记 skipped；
            配置了但驱动缺失记 failed/degradable（与 check_database/redis 的
            结论一致，但 dep:* 项让报告里"缺什么依赖"独立可见，供渲染声明引用）。

        Returns:
            list[PreflightItem]: dep:* 检查项列表（顺序固定）。
        """
        items: List[PreflightItem] = []
        # playwright（致命依赖）
        if self._deps.playwright is not None:
            items.append(PreflightItem(
                name="dep:playwright", status="ok",
                reason="playwright 可用", degradable=False))
        else:
            items.append(PreflightItem(
                name="dep:playwright", status="failed",
                reason="playwright 缺失（UI 遍历不可用，"
                       "pip install 'playwright>=1.40.0' && playwright install chromium）",
                degradable=False))
        # pymysql / psycopg2（按 engine 使用面判级）
        db_cfg = self._cfg.database
        for module_name in ("pymysql", "psycopg2"):
            module_obj = getattr(self._deps, module_name, None)
            in_use = db_cfg is not None and self._engine_driver_name(db_cfg.engine) == module_name
            if module_obj is not None:
                items.append(PreflightItem(
                    name="dep:{0}".format(module_name), status="ok",
                    reason="{0} 可用".format(module_name), degradable=True))
            elif in_use:
                hint = self._deps.db_driver_install_hint(db_cfg.engine) if db_cfg else "安装驱动"
                items.append(PreflightItem(
                    name="dep:{0}".format(module_name), status="failed",
                    reason="{0} 缺失但 database.engine 需要（{1}）".format(module_name, hint),
                    degradable=True))
            else:
                items.append(PreflightItem(
                    name="dep:{0}".format(module_name), status="skipped",
                    reason="{0} 未使用（database 未配置或引擎不匹配）".format(module_name),
                    degradable=True))
        # redis
        if self._deps.redis is not None:
            items.append(PreflightItem(
                name="dep:redis", status="ok", reason="redis 可用", degradable=True))
        elif self._cfg.redis is not None:
            items.append(PreflightItem(
                name="dep:redis", status="failed",
                reason="redis 缺失但 redis 段已配置（{0}）".format(
                    self._deps.redis_install_hint()),
                degradable=True))
        else:
            items.append(PreflightItem(
                name="dep:redis", status="skipped",
                reason="redis 未使用（redis 段未配置）", degradable=True))
        return items

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------

    def run_all(self) -> PreflightReport:
        """执行全部检查并写 ``state/preflight.json``（§2.3.4）。

        检查顺序（固定，报告可 diff）：site → dep:* → database → redis。

        汇总规则：
          - fatal_failed：``not degradable and status == 'failed'`` 的项名
            （skipped 不算失败——"未配置"是合法降级场景，不是错误）；
          - degraded：``degradable and status != 'ok'`` 的项名。

        落盘（红线①）：报告先 :meth:`PreflightReport.to_redacted` 过 redact 管线
        得 RedactedDict，再写 ``<out_dir>/<system_id>/state/preflight.json``。

        Returns:
            PreflightReport: 汇总报告（fatal_failed 非空时上层按退出码 3 终止）。
        """
        items: List[PreflightItem] = [self.check_site()]
        items.extend(self.check_dependencies())
        items.append(self.check_database())
        items.append(self.check_redis())

        fatal_failed = [i.name for i in items if (not i.degradable) and i.status == "failed"]
        degraded = [i.name for i in items if i.degradable and i.status != "ok"]
        report = PreflightReport(items=items, fatal_failed=fatal_failed, degraded=degraded)

        # 写 state/preflight.json：RedactedDict 形态落盘（§5.2 管线，§5.5 路径边界）
        report_dict = report.to_redacted()
        state_dir = self._resolve_state_dir()
        target = state_dir / "preflight.json"
        with target.open("w", encoding="utf-8") as fh:
            json.dump(report_dict, fh, ensure_ascii=False, sort_keys=True, indent=2)
        return report

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _resolve_state_dir(self) -> Path:
        """创建并返回 ``<out_dir>/<system_id>/state`` 目录。

        Returns:
            Path: state 目录（已确保存在）。
        """
        state_dir = self._cfg.out_dir / self._cfg.system_id / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        return state_dir

    def _connect_db(self, driver: Any) -> Any:
        """按配置的 engine 建立**已加固**的数据库连接（红线②）。

        流程：
          1. 按 engine 用注入的驱动模块 ``connect``（凭据仅在此经
             :meth:`SensitiveStr.reveal` 落地——DB 建连是 §5.1 允许的 reveal 边界）；
          2. :class:`su.db_guard.DbSessionHardener` 会话只读加固并验证生效
             （加固语句仍经 ReadOnlyGuard 单一执行通道下发）。

        Args:
            driver: pymysql / psycopg2 模块对象（来自 DependencyReport 注入）。

        Returns:
            Any: DB-API 2.0 连接对象（会话已只读加固）。

        Raises:
            Exception: 驱动层连接错误原样上抛（由调用方预检逻辑吸收）。
        """
        # 延迟导入避免模块级依赖（config 的 db_guard 已是硬依赖，此处只补 hardener）
        from su.db_guard import DbSessionHardener

        db_cfg = self._cfg.database
        assert db_cfg is not None  # 调用方已判配置存在
        engine = db_cfg.engine.strip().lower()

        if engine == "mysql":
            conn = driver.connect(
                host=db_cfg.host,
                port=db_cfg.port,
                user=db_cfg.user,
                # reveal 边界②：DB 建连（§5.1 静态审查白名单位置）
                password=db_cfg.password.reveal(),
                database=db_cfg.database,
                charset="utf8mb4",
                autocommit=True,
                connect_timeout=_SITE_TIMEOUT_SECONDS,
            )
        else:
            conn = driver.connect(
                host=db_cfg.host,
                port=db_cfg.port,
                user=db_cfg.user,
                password=db_cfg.password.reveal(),  # reveal 边界②：DB 建连
                dbname=db_cfg.database,
                connect_timeout=_SITE_TIMEOUT_SECONDS,
            )
            # PG 默认非 autocommit；加固语句 BEGIN READ ONLY 需先落定当前事务边界
            conn.autocommit = True
        DbSessionHardener().harden(conn, engine, guard=self._db_guard)
        return conn

    def _connect_redis(self, redis_module: Any) -> Any:
        """按配置建立 redis 客户端（decode_responses 开启便于值样例处理）。

        Args:
            redis_module: redis 库模块对象（DependencyReport 注入）。

        Returns:
            Any: redis 客户端（duck-typing 使用）。
        """
        redis_cfg = self._cfg.redis
        assert redis_cfg is not None  # 调用方已判配置存在
        return redis_module.Redis(
            host=redis_cfg.host,
            port=redis_cfg.port,
            # reveal 边界②：Redis 建连（§5.1 允许的唯一 reveal 位置之一）
            password=(redis_cfg.password.reveal() or None),
            db=redis_cfg.db,
            decode_responses=True,
            socket_timeout=_SITE_TIMEOUT_SECONDS,
            socket_connect_timeout=_SITE_TIMEOUT_SECONDS,
        )

    @staticmethod
    def _engine_driver_name(engine: str) -> str:
        """engine → 驱动模块名映射（与 deps._DB_DRIVER_MAP 口径一致）。

        Args:
            engine: 'mysql' / 'postgresql'。

        Returns:
            str: 'pymysql' / 'psycopg2'（非法引擎返回空串）。
        """
        return {"mysql": "pymysql", "postgresql": "psycopg2"}.get(
            (engine or "").strip().lower(), "")


# RedisGuard 无法直接 call(client, "PING")（PING 属连接管理命令、不在采集面
# 白名单内），故 check_redis 使用 _PingOnlyClient 受限适配器：守卫的白名单
# 逻辑原样生效，PING 的额外授权封闭在适配器内（只暴露 ping() 一个方法）。
def _brief(exc: BaseException) -> str:
    """异常 → 单行摘要文本（进报告 reason 前由 PreflightItem.to_dict 统一 scrub）。

    Args:
        exc: 驱动层异常。

    Returns:
        str: ``类型名: 消息`` 形态的单行文本（消息压平空白并截断 200 字符）。
    """
    message = " ".join(str(exc).split())[:200]
    return "{0}: {1}".format(type(exc).__name__, message)
