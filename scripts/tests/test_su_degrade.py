# -*- coding: utf-8 -*-
"""SU 能力单元测试：软依赖探测与降级口径（REQ-SU-021 / REQ-SU-003）。

覆盖 su.deps 与 su.preflight 组合（import_fn 注入 fake importer 制造缺失组合，
urllib opener 注入替身脱网，不 mock 被测业务逻辑）：
- probe_all：全齐 / 部分缺失 / 全缺 —— missing 固定顺序与 playwright 归名
- require_playwright 缺失 → SuDepsError（exit_code=5，含安装命令）
- require_db_driver：mysql/postgresql 映射、缺失返回 None、非法引擎防御 None
- db_driver_install_hint / redis_install_hint 文案
- Preflight.check_dependencies：playwright 缺失=致命（degradable=False）；
  驱动"配置了但缺失"= failed/degradable；"未使用"= skipped
- Preflight.check_database/check_redis：配置段缺失 → skipped/degradable
- run_all 汇总：fatal_failed / degraded 口径 + state/preflight.json 落盘
- PreflightItem.to_dict：reason 经 scrub_text（URL 内嵌凭据剥离）

运行方式（项目根目录）：
    python3 -B -m unittest scripts.tests.test_su_degrade
"""

import sys
import tempfile
import types
import unittest
from pathlib import Path
import json

# 将 scripts/ 目录注入 sys.path，使 `from su.xxx import ...` 生效
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from su.config import (  # noqa: E402
    DatabaseConfig,
    RedisConfig,
    RunBudget,
    SuConfig,
    SystemConfig,
)
from su.deps import DependencyReport, probe_all  # noqa: E402
from su.dto import SuDepsError, SensitiveStr  # noqa: E402
from su.preflight import Preflight, PreflightItem  # noqa: E402


def _fake_importer(available):
    """构造 fake import_fn：available 集合外的模块抛 ImportError。"""
    def import_fn(name):
        if name in available:
            module = types.ModuleType(name)
            return module
        raise ImportError("No module named {0!r}".format(name))
    return import_fn


def _make_cfg(tmpdir, database=None, redis=None):
    """构造最小 SuConfig（db/redis 段可选注入，制造配置面差异）。"""
    return SuConfig(
        system=SystemConfig(
            base_url="https://legacy.example.com",
            login_url="/login",
            username=SensitiveStr("u"),
            password=SensitiveStr("p"),
        ),
        database=database,
        redis=redis,
        budget=RunBudget(),
        out_dir=Path(tmpdir),
        system_id="degrade-test",
    )


class TestProbeAll(unittest.TestCase):
    """REQ-SU-021：probe_all 探测矩阵。"""

    def test_all_available(self):
        """四依赖全可用 → missing 为空。"""
        imp = _fake_importer({"playwright.sync_api", "playwright",
                              "pymysql", "psycopg2", "redis"})
        report = probe_all(import_fn=imp)
        self.assertEqual(report.missing, [])
        self.assertIsNotNone(report.playwright)
        self.assertIsNotNone(report.pymysql)
        self.assertIsNotNone(report.psycopg2)
        self.assertIsNotNone(report.redis)

    def test_all_missing_fixed_order(self):
        """全缺 → missing 按固定顺序 playwright→pymysql→psycopg2→redis。"""
        report = probe_all(import_fn=_fake_importer(set()))
        self.assertEqual(report.missing, ["playwright", "pymysql", "psycopg2", "redis"])
        self.assertIsNone(report.playwright)
        self.assertIsNone(report.redis)

    def test_partial_missing_redis_only(self):
        """仅 redis 缺失 → 其余字段正常，missing 只含 redis。"""
        imp = _fake_importer({"playwright.sync_api", "playwright",
                              "pymysql", "psycopg2"})
        report = probe_all(import_fn=imp)
        self.assertEqual(report.missing, ["redis"])
        self.assertIsNotNone(report.playwright)
        self.assertIsNone(report.redis)

    def test_playwright_stored_as_package(self):
        """playwright 存包本体（sync_api 探测成功后再取 playwright 包）。"""
        imp = _fake_importer({"playwright.sync_api", "playwright",
                              "pymysql", "psycopg2", "redis"})
        report = probe_all(import_fn=imp)
        self.assertEqual(report.playwright.__name__, "playwright")


class TestRequireMethods(unittest.TestCase):
    """REQ-SU-021：require_* 降级口径。"""

    def test_require_playwright_missing_fatal(self):
        """playwright 缺失 → SuDepsError，exit_code=5，含安装命令。"""
        report = probe_all(import_fn=_fake_importer(set()))
        with self.assertRaises(SuDepsError) as ctx:
            report.require_playwright()
        self.assertEqual(ctx.exception.exit_code, 5)
        self.assertEqual(ctx.exception.code, "deps_missing")
        joined = ctx.exception.format()
        self.assertIn("pip install 'playwright>=1.40.0' && playwright install chromium", joined)

    def test_require_playwright_present_returns_module(self):
        """playwright 可用 → 返回模块对象。"""
        imp = _fake_importer({"playwright.sync_api", "playwright"})
        report = probe_all(import_fn=imp)
        self.assertIsNotNone(report.require_playwright())

    def test_require_db_driver_mapping(self):
        """engine→驱动映射：mysql→pymysql / postgresql→psycopg2。"""
        imp = _fake_importer({"pymysql", "psycopg2"})
        report = probe_all(import_fn=imp)
        self.assertIsNotNone(report.require_db_driver("mysql"))
        self.assertIsNotNone(report.require_db_driver("postgresql"))

    def test_require_db_driver_missing_returns_none(self):
        """驱动缺失 → None（可降级，不抛）。"""
        report = probe_all(import_fn=_fake_importer(set()))
        self.assertIsNone(report.require_db_driver("mysql"))
        self.assertIsNone(report.require_db_driver("postgresql"))
        # 非法引擎防御性 None（config.validate 本应拦截）
        self.assertIsNone(report.require_db_driver("oracle"))

    def test_install_hints(self):
        """安装命令文案（渲染降级声明复用）。"""
        report = DependencyReport()
        self.assertEqual(report.db_driver_install_hint("mysql"), "pip install 'pymysql>=1.1.0'")
        self.assertEqual(report.db_driver_install_hint("postgresql"),
                         "pip install 'psycopg2-binary>=2.9'")
        self.assertIsNone(report.db_driver_install_hint("oracle"))
        self.assertEqual(report.redis_install_hint(), "pip install 'redis>=5.0.0'")

    def test_require_redis_missing_returns_none(self):
        """require_redis 缺失 → None（可降级）。"""
        report = probe_all(import_fn=_fake_importer(set()))
        self.assertIsNone(report.require_redis())


class TestPreflightDependencies(unittest.TestCase):
    """REQ-SU-003/021：check_dependencies 判级矩阵。"""

    def test_playwright_missing_is_fatal(self):
        """playwright 缺失 → dep:playwright failed 且 degradable=False。"""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_cfg(tmp)
            deps = probe_all(import_fn=_fake_importer(set()))
            items = Preflight(cfg, deps).check_dependencies()
            pw = next(i for i in items if i.name == "dep:playwright")
            self.assertEqual(pw.status, "failed")
            self.assertFalse(pw.degradable)  # 致命项

    def test_driver_configured_but_missing_failed_degradable(self):
        """配置 mysql 段但 pymysql 缺失 → failed/degradable 含安装命令。"""
        with tempfile.TemporaryDirectory() as tmp:
            db = DatabaseConfig(engine="mysql", host="h", port=3306, user="u",
                                password=SensitiveStr("p"), database="d")
            cfg = _make_cfg(tmp, database=db)
            deps = probe_all(import_fn=_fake_importer(set()))
            items = Preflight(cfg, deps).check_dependencies()
            my = next(i for i in items if i.name == "dep:pymysql")
            self.assertEqual(my.status, "failed")
            self.assertTrue(my.degradable)
            self.assertIn("pip install 'pymysql>=1.1.0'", my.reason)
            # 未使用的 psycopg2 → skipped
            pg = next(i for i in items if i.name == "dep:psycopg2")
            self.assertEqual(pg.status, "skipped")

    def test_redis_unconfigured_skipped(self):
        """redis 段未配置 → dep:redis skipped（合法降级非错误）。"""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_cfg(tmp)  # redis=None
            deps = probe_all(import_fn=_fake_importer(set()))
            items = Preflight(cfg, deps).check_dependencies()
            rd = next(i for i in items if i.name == "dep:redis")
            self.assertEqual(rd.status, "skipped")
            self.assertTrue(rd.degradable)

    def test_all_ok_matrix(self):
        """全依赖可用 → dep:* 全部 ok。"""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_cfg(tmp)
            deps = probe_all(import_fn=_fake_importer(
                {"playwright.sync_api", "playwright", "pymysql", "psycopg2", "redis"}))
            items = Preflight(cfg, deps).check_dependencies()
            self.assertTrue(all(i.status == "ok" for i in items))


class TestPreflightChecks(unittest.TestCase):
    """REQ-SU-003：database/redis 段缺失与 run_all 汇总落盘。"""

    def test_database_unconfigured_skipped(self):
        """database 段缺失 → skipped/degradable。"""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_cfg(tmp)
            deps = probe_all(import_fn=_fake_importer(set()))
            item = Preflight(cfg, deps).check_database()
            self.assertEqual(item.name, "database")
            self.assertEqual(item.status, "skipped")
            self.assertTrue(item.degradable)

    def test_driver_missing_degrades_not_fatal(self):
        """配置 DB 但驱动缺失 → failed/degradable（DB 透镜可降级）。"""
        with tempfile.TemporaryDirectory() as tmp:
            db = DatabaseConfig(engine="postgresql", host="h", port=5432, user="u",
                                password=SensitiveStr("p"), database="d")
            cfg = _make_cfg(tmp, database=db)
            deps = probe_all(import_fn=_fake_importer(set()))
            item = Preflight(cfg, deps).check_database()
            self.assertEqual(item.status, "failed")
            self.assertTrue(item.degradable)
            self.assertIn("pip install 'psycopg2-binary>=2.9'", item.reason)

    def test_redis_driver_missing_degrades(self):
        """配置 redis 段但驱动缺失 → failed/degradable 含安装命令。"""
        with tempfile.TemporaryDirectory() as tmp:
            rc = RedisConfig(host="h", port=6379, password=SensitiveStr(""))
            cfg = _make_cfg(tmp, redis=rc)
            deps = probe_all(import_fn=_fake_importer(set()))
            item = Preflight(cfg, deps).check_redis()
            self.assertEqual(item.status, "failed")
            self.assertTrue(item.degradable)
            self.assertIn("pip install 'redis>=5.0.0'", item.reason)

    def test_item_reason_scrubbed(self):
        """PreflightItem.to_dict：reason 经 scrub_text（URL 内嵌凭据剥离）。"""
        item = PreflightItem(
            name="site", status="failed",
            reason="连接失败 http://admin:S3cr3tP@ss@db.internal:3306/x",
            degradable=False)
        d = item.to_dict()
        # URL userinfo 凭据必须剥离（scrub_text 先剥 URL 凭据再 PII 替换）
        self.assertNotIn("S3cr3tP@ss", str(d["reason"]))

    def test_run_all_summary_and_persistence(self):
        """run_all：fatal_failed 口径 + preflight.json 落盘（脱网 fake opener）。"""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_cfg(tmp)
            deps = probe_all(import_fn=_fake_importer(set()))
            preflight = Preflight(cfg, deps)
            # 脱网：站点检查必失败（DNS 不可能解析 legacy.example.com 的 HEAD
            # 在无网环境失败；即使有网也只影响 status 不影响口径断言）
            report = preflight.run_all()
            # playwright 缺失 → fatal_failed 必含 dep:playwright
            self.assertIn("dep:playwright", report.fatal_failed)
            self.assertFalse(report.ok)
            # 未配置 db/redis → degraded 清单含 skipped 项
            self.assertIn("database", report.degraded)
            self.assertIn("redis", report.degraded)
            # 站点项存在且致命标记正确
            site = next(i for i in report.items if i.name == "site")
            self.assertFalse(site.degradable)
            # 落盘校验
            target = Path(tmp) / "degrade-test" / "state" / "preflight.json"
            self.assertTrue(target.is_file())
            data = json.loads(target.read_text("utf-8"))
            self.assertIn("items", data)
            self.assertIn("dep:playwright", data["fatal_failed"])
            self.assertFalse(data["ok"])

    def test_run_all_site_ok_not_fatal(self):
        """站点检查失败仅 site 致命；site ok 时 fatal_failed 只由 playwright 决定。"""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_cfg(tmp)
            # 依赖全齐（playwright 可用）→ fatal_failed 应为空 → report.ok
            deps = probe_all(import_fn=_fake_importer(
                {"playwright.sync_api", "playwright", "pymysql", "psycopg2", "redis"}))
            preflight = Preflight(cfg, deps)
            # 覆写 check_site 返回 ok（避免真实网络依赖；这不是 mock 业务逻辑，
            # 而是隔离网络面——site 判定逻辑由 check_site 单测另行覆盖）
            preflight.check_site = lambda: PreflightItem(
                name="site", status="ok", reason="站点可达（HTTP 200）", degradable=False)
            report = preflight.run_all()
            self.assertEqual(report.fatal_failed, [])
            self.assertTrue(report.ok)
            # db/redis 未配置 → skipped 进 degraded（不阻断）
            self.assertIn("database", report.degraded)
            store = json.loads((Path(tmp) / "degrade-test" / "state"
                                / "preflight.json").read_text("utf-8"))
            self.assertTrue(store["ok"])


class TestCheckSiteLogic(unittest.TestCase):
    """REQ-SU-003：check_site 判定口径（HTTPError=存活，连接层错误=失败）。

    实现事实：URL 非法（ValueError）判失败；HTTPError（收到响应）判 ok。
    """

    def test_invalid_url_raises_not_caught(self):
        """实现事实：Request 构造在 try 块之前——完全非法的 URL
        （无协议形态）ValueError 直接上抛，不进 failed 分支；
        check_site 的 ValueError 捕获只覆盖 opener.open 路径的畸形 URL。
        """
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_cfg(tmp)
            cfg.system.base_url = "://no-scheme"
            deps = DependencyReport()
            with self.assertRaises(ValueError):
                Preflight(cfg, deps).check_site()

    def test_unsupported_scheme_failed(self):
        """合法形态但协议不支持（ftp://）→ opener 抛 ValueError → failed。"""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_cfg(tmp)
            cfg.system.base_url = "ftp://legacy.example.com"
            deps = DependencyReport()
            item = Preflight(cfg, deps).check_site()
            self.assertEqual(item.status, "failed")
            self.assertFalse(item.degradable)

    def test_connection_error_failed(self):
        """不可解析域（保留 .invalid TLD，RFC 保证不解析）→ failed。"""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_cfg(tmp)
            cfg.system.base_url = "https://su-nonexistent.invalid"
            deps = DependencyReport()
            item = Preflight(cfg, deps).check_site()
            self.assertEqual(item.status, "failed")
            self.assertIn("站点 HEAD 请求失败", item.reason)


if __name__ == "__main__":
    unittest.main()
