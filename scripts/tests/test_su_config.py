# -*- coding: utf-8 -*-
"""
SU 配置加载与脱敏管线单元测试（REQ-SU-001 / REQ-SU-002）

覆盖 PRD §6.1 测试矩阵中 config 模块的用例：
1. 三级优先级合并：CLI > 环境变量 > JSON 文件；
2. system 段必填字段缺失时报错消息包含字段名；
3. database.engine 非法值被 validate 拒绝；
4. redact 管线：敏感键名、嵌套 dict、URL 内 user:pass@、PII 文本、SensitiveStr；
5. strip_url_credentials / scrub_text 独立函数行为；
6. parse_db_url / parse_redis_url 解析与非法输入报错。

测试原则：不 mock 被测业务逻辑本身，全部使用真实函数 + 临时文件构造输入。
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# 将 scripts/ 目录加入模块搜索路径，使 `from su.xxx import ...` 绝对导入生效
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from su.config import (
    ENV_OVERRIDES,
    LoginSelectors,
    RunBudget,
    SuConfig,
    SuConfigError,
    SystemConfig,
    DatabaseConfig,
    RedisConfig,
    load_config,
    parse_db_url,
    parse_redis_url,
    redact,
    scrub_text,
    strip_url_credentials,
    validate,
)
from su.dto import REDACTED_PLACEHOLDER, RedactedDict, SensitiveStr


class _Args:
    """极简 argparse.Namespace 替身：只提供 load_config 关心的属性。

    load_config 通过 getattr(args, name, None) 读取参数，因此未设置的属性
    会自动取默认值 None，无需完整 argparse 对象。
    """

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def _minimal_config_dict():
    """构造一份最小合法 JSON 配置（dict），供各优先级测试复用。"""
    return {
        "system": {
            "base_url": "https://app.example.com",
            "username": "json_user",
            "password": "json_pass",
        },
        "database": {
            "engine": "mysql",
            "host": "db.example.com",
            "port": 3306,
            "user": "reader",
            "password": "db_pass",
            "database": "appdb",
        },
        "redis": {"host": "r.example.com", "port": 6379},
    }


class TestThreeTierPriority(unittest.TestCase):
    """REQ-SU-001：配置三级优先级 CLI > env > JSON。"""

    def setUp(self):
        # 记录并清理 SU_* 环境变量，避免测试间串扰
        self._env_backup = {k: os.environ.get(k) for k in ENV_OVERRIDES.values()}
        for k in ENV_OVERRIDES.values():
            os.environ.pop(k, None)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def tearDown(self):
        # 恢复环境变量原值
        for k, v in self._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _write_json(self, data):
        """把配置 dict 写入临时 JSON 文件并返回路径。"""
        p = Path(self._tmp.name) / "su_config.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return str(p)

    def test_json_baseline(self):
        """仅有 JSON 文件时，各段取 JSON 值（最低优先级基线）。"""
        cfg_path = self._write_json(_minimal_config_dict())
        cfg = load_config(_Args(config=cfg_path))
        self.assertEqual(cfg.system.base_url, "https://app.example.com")
        self.assertEqual(cfg.system.username.reveal(), "json_user")
        self.assertEqual(cfg.system.password.reveal(), "json_pass")

    def test_env_overrides_json(self):
        """环境变量覆盖 JSON 中的同名字段（中优先级）。"""
        data = _minimal_config_dict()
        cfg_path = self._write_json(data)
        os.environ["SU_SYSTEM_USERNAME"] = "env_user"
        os.environ["SU_DB_PASSWORD"] = "env_db_pass"
        cfg = load_config(_Args(config=cfg_path))
        self.assertEqual(cfg.system.username.reveal(), "env_user")
        self.assertEqual(cfg.database.password.reveal(), "env_db_pass")
        # 未被覆盖的字段保持 JSON 值
        self.assertEqual(cfg.system.password.reveal(), "json_pass")

    def test_cli_overrides_env_and_json(self):
        """CLI 参数同时压过 env 与 JSON（最高优先级）。"""
        data = _minimal_config_dict()
        cfg_path = self._write_json(data)
        os.environ["SU_SYSTEM_PASSWORD"] = "env_pass"
        cfg = load_config(_Args(
            config=cfg_path,
            system_url="https://cli.example.com",
            username="cli_user",
            password="cli_pass",
        ))
        self.assertEqual(cfg.system.base_url, "https://cli.example.com")
        self.assertEqual(cfg.system.username.reveal(), "cli_user")
        # CLI 密码优先于 env 密码
        self.assertEqual(cfg.system.password.reveal(), "cli_pass")

    def test_full_priority_matrix(self):
        """subTest 矩阵：同一字段在 无JSON值/JSON/env/CLI 组合下的最终来源。"""
        cases = [
            # (json值, env值, cli值, 期望最终值)
            ("json_v", None, None, "json_v"),
            ("json_v", "env_v", None, "env_v"),
            ("json_v", None, "cli_v", "cli_v"),
            ("json_v", "env_v", "cli_v", "cli_v"),
            (None, "env_v", "cli_v", "cli_v"),
        ]
        for idx, (json_v, env_v, cli_v, expected) in enumerate(cases):
            with self.subTest(case=idx, json=json_v, env=env_v, cli=cli_v):
                data = _minimal_config_dict()
                if json_v is None:
                    del data["system"]["username"]
                else:
                    data["system"]["username"] = json_v
                cfg_path = self._write_json(data)
                if env_v is not None:
                    os.environ["SU_SYSTEM_USERNAME"] = env_v
                cfg = load_config(_Args(config=cfg_path, username=cli_v))
                self.assertEqual(cfg.system.username.reveal(), expected)
                os.environ.pop("SU_SYSTEM_USERNAME", None)


class TestRequiredFields(unittest.TestCase):
    """REQ-SU-001：system 段必填字段缺失报错须含字段名。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # 清理可能干扰的环境变量
        self._env_backup = {k: os.environ.get(k) for k in ENV_OVERRIDES.values()}
        for k in ENV_OVERRIDES.values():
            os.environ.pop(k, None)
        self.addCleanup(lambda: [
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
            for k, v in self._env_backup.items()
        ])

    def _load_with(self, mutate):
        """按 mutator 修改最小配置后加载，返回 (cfg或None, 异常或None)。"""
        data = _minimal_config_dict()
        mutate(data)
        p = Path(self._tmp.name) / "c.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        try:
            return load_config(_Args(config=str(p))), None
        except SuConfigError as exc:
            return None, exc

    def test_missing_base_url(self):
        """缺 base_url：报错消息包含字段名 base_url。"""
        _, err = self._load_with(lambda d: d["system"].pop("base_url"))
        self.assertIsNotNone(err)
        self.assertIn("base_url", str(err))

    def test_missing_username(self):
        """缺 username：报错消息包含字段名 username。"""
        _, err = self._load_with(lambda d: d["system"].pop("username"))
        self.assertIsNotNone(err)
        self.assertIn("username", str(err))

    def test_missing_password(self):
        """缺 password：报错消息包含字段名 password。"""
        _, err = self._load_with(lambda d: d["system"].pop("password"))
        self.assertIsNotNone(err)
        self.assertIn("password", str(err))

    def test_invalid_engine_rejected(self):
        """engine 非法值（oracle）被 validate 拒绝，错误含 engine 字样。"""
        data = _minimal_config_dict()
        data["database"]["engine"] = "oracle"
        p = Path(self._tmp.name) / "c2.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(SuConfigError) as ctx:
            load_config(_Args(config=str(p)))
        self.assertIn("engine", str(ctx.exception))

    def test_login_url_default(self):
        """login_url 缺省时应回填 /login。"""
        cfg, _ = self._load_with(lambda d: d["system"].pop("login_url", None))
        self.assertEqual(cfg.system.login_url, "/login")

    def test_allowed_origins_defaults_to_base(self):
        """allowed_origins 为空时默认取 base_url 同源。"""
        cfg, _ = self._load_with(lambda d: None)
        self.assertIn("https://app.example.com", cfg.system.allowed_origins)


class TestValidate(unittest.TestCase):
    """REQ-SU-001：validate 对各字段的独立校验。"""

    def _mk_cfg(self):
        """构造一份合法 SuConfig（不经 load_config，直接对象构造）。"""
        return SuConfig(
            system=SystemConfig(
                base_url="https://x.example.com",
                login_url="/login",
                username=SensitiveStr("u"),
                password=SensitiveStr("p"),
            ),
            database=DatabaseConfig(
                engine="mysql", host="h", port=3306,
                user="u", password=SensitiveStr("p"), database="d",
            ),
            redis=RedisConfig(host="h", port=6379, password=SensitiveStr("")),
            budget=RunBudget(),
            out_dir=Path("/tmp/out"),
            system_id="t",
        )

    def test_valid_config_no_errors(self):
        """合法配置 validate 返回空列表。"""
        self.assertEqual(validate(self._mk_cfg()), [])

    def test_bad_port_and_engine(self):
        """端口越界 + 非法引擎都出现在错误列表中。"""
        cfg = self._mk_cfg()
        cfg.database.port = 70000
        cfg.database.engine = "sqlite"
        errors = validate(cfg)
        joined = "\n".join(errors)
        self.assertIn("port", joined)
        self.assertIn("engine", joined)

    def test_budget_must_be_positive_int(self):
        """budget 字段非正整数（0 / bool / 负数）被拒绝。"""
        for bad in (0, -1, True):
            with self.subTest(bad=bad):
                cfg = self._mk_cfg()
                cfg.budget.max_pages = bad
                self.assertTrue(any("max_pages" in e for e in validate(cfg)))

    def test_sample_rows_upper_bound(self):
        """sample_rows 超过 50 上限被拒绝。"""
        cfg = self._mk_cfg()
        cfg.budget.sample_rows = 51
        self.assertTrue(any("sample_rows" in e for e in validate(cfg)))


class TestRedactPipeline(unittest.TestCase):
    """REQ-SU-002：redact 脱敏管线（安全红线①）。"""

    def test_sensitive_key_hit(self):
        """键名命中敏感词典：值替换为占位符且不递归。"""
        out = redact({"password": "hunter2", "token": {"nested": "x"}})
        self.assertEqual(out["password"], REDACTED_PLACEHOLDER)
        self.assertEqual(out["token"], REDACTED_PLACEHOLDER)

    def test_returns_redacted_dict(self):
        """dict 输入必返 RedactedDict 类型（供状态层强校验）。"""
        out = redact({"a": 1})
        self.assertIsInstance(out, RedactedDict)

    def test_nested_dict_scrub(self):
        """嵌套 dict 的值递归脱敏。"""
        out = redact({"outer": {"phone": "联系电话13800138000"}})
        self.assertIn("<REDACTED:phone>", out["outer"]["phone"])

    def test_sensitive_str_replaced(self):
        """SensitiveStr 值直接替换为占位符。"""
        out = redact({"cred": SensitiveStr("s3cret")})
        self.assertEqual(out["cred"], REDACTED_PLACEHOLDER)

    def test_url_credentials_in_string(self):
        """字符串内 URL 携带 user:pass@ 时被剥离。"""
        out = redact({"dsn": "mysql://root:pwd@db:3306/app"})
        self.assertNotIn("root:pwd", out["dsn"])
        self.assertIn("mysql://", out["dsn"])

    def test_pii_in_text(self):
        """文本中的手机号/邮箱被 PII 正则替换。"""
        out = redact("用户手机13800138000，邮箱 a@b.com")
        self.assertIn("<REDACTED:phone>", out)
        self.assertIn("<REDACTED:email>", out)

    def test_long_string_truncated(self):
        """超长字符串按 max_str_len 截断。"""
        out = redact("x" * 500, max_str_len=100)
        self.assertLessEqual(len(out), 200)  # 截断标记后仍受限

    def test_scalar_passthrough(self):
        """int/float/bool/None 原样返回。"""
        for v in (1, 2.5, True, None):
            with self.subTest(v=v):
                self.assertEqual(redact(v), v)

    def test_list_recursion(self):
        """list 内元素逐项递归。"""
        out = redact([{"password": "p"}, "13800138000"])
        self.assertEqual(out[0]["password"], REDACTED_PLACEHOLDER)
        self.assertIn("<REDACTED:phone>", out[1])


class TestStripUrlCredentials(unittest.TestCase):
    """REQ-SU-002：strip_url_credentials 精确行为。"""

    def test_strip_auth(self):
        """标准 URL userinfo 剥离。"""
        self.assertEqual(
            strip_url_credentials("postgres://u:p@h:5432/db"),
            "postgres://h:5432/db",
        )

    def test_at_in_path_kept(self):
        """'@' 出现在 path 中不得误伤。"""
        url = "https://host/user/@me?q=1"
        self.assertEqual(strip_url_credentials(url), url)

    def test_no_auth_unchanged(self):
        """无凭据 URL 原样返回。"""
        url = "https://host/a/b"
        self.assertEqual(strip_url_credentials(url), url)


class TestScrubText(unittest.TestCase):
    """REQ-SU-002：scrub_text PII 值形态替换。"""

    def test_phone_email_id_card(self):
        """手机号、邮箱、身份证三类 PII 均被替换。"""
        text = "手机13912345678 邮箱 bob@example.com 身份证110101199001011234"
        out = scrub_text(text)
        self.assertIn("<REDACTED:phone>", out)
        self.assertIn("<REDACTED:email>", out)
        self.assertIn("<REDACTED:id_card>", out)
        self.assertNotIn("13912345678", out)

    def test_empty_and_none(self):
        """None 与空串原样返回。"""
        self.assertIsNone(scrub_text(None))
        self.assertEqual(scrub_text(""), "")

    def test_any_scheme_url_credentials_masked(self):
        """回归（2026-09-28 自测发现）：任意 scheme 的 user:pass@ 凭据
        整体掩码——旧版只覆盖顶层整串 http/https，非 http 协议
        （redis/mysql）与文本内嵌 URL 的凭据原样漏网。"""
        out = scrub_text("连接失败 redis://user:p@ss@h:6379/0 请检查")
        self.assertNotIn("user:p@ss", out)
        self.assertIn("***REDACTED***@h:6379/0", out)
        # 整串非 http 协议同样覆盖
        out2 = scrub_text("mysql://root:pwd@db:3306/app")
        self.assertNotIn("root:pwd", out2)

    def test_plain_at_and_mailto_not_masked(self):
        """不误伤：普通含 '@' 文本与 mailto 不构成 URL userinfo
        （无 '://' 前缀），邮箱仍走 PII email 通道。"""
        out = scrub_text("联系 admin 邮箱 a@b.com")
        self.assertNotIn("***REDACTED***@", out)
        self.assertIn("<REDACTED:email>", out)
        # query 内 '@'（'/' 之后）不构成 userinfo
        url = "https://host/?next=a@b"
        self.assertEqual(scrub_text(url), url)


class TestParseDbUrl(unittest.TestCase):
    """REQ-SU-001：parse_db_url 解析与报错。"""

    def test_mysql_defaults(self):
        """mysql:// 默认端口 3306，字段完整。"""
        parts = parse_db_url("mysql://reader:pw@db.example.com/appdb")
        self.assertEqual(parts["engine"], "mysql")
        self.assertEqual(parts["port"], 3306)
        self.assertEqual(parts["user"], "reader")
        self.assertEqual(parts["password"], "pw")
        self.assertEqual(parts["database"], "appdb")

    def test_postgres_alias_and_schemas(self):
        """postgres 别名归一为 postgresql；query schemas 逗号拆分。"""
        parts = parse_db_url("postgres://u@h:5433/d?schemas=public,biz")
        self.assertEqual(parts["engine"], "postgresql")
        self.assertEqual(parts["port"], 5433)
        self.assertEqual(parts["schemas"], ["public", "biz"])

    def test_bad_scheme(self):
        """非法 scheme（oracle://）报 SuConfigError。"""
        with self.assertRaises(SuConfigError):
            parse_db_url("oracle://u@h/d")

    def test_missing_database(self):
        """缺库名报 SuConfigError。"""
        with self.assertRaises(SuConfigError):
            parse_db_url("mysql://u@h")


class TestParseRedisUrl(unittest.TestCase):
    """REQ-SU-001：parse_redis_url 解析与报错。"""

    def test_full_url(self):
        """完整 redis URL：密码/端口/db 全解析。"""
        parts = parse_redis_url("redis://:rpass@r.example.com:6380/2")
        self.assertEqual(parts["host"], "r.example.com")
        self.assertEqual(parts["port"], 6380)
        self.assertEqual(parts["password"], "rpass")
        self.assertEqual(parts["db"], 2)

    def test_default_port_db(self):
        """缺省端口 6379、db 0。"""
        parts = parse_redis_url("redis://localhost")
        self.assertEqual(parts["port"], 6379)
        self.assertEqual(parts["db"], 0)

    def test_bad_db_index(self):
        """路径非数字报 SuConfigError。"""
        with self.assertRaises(SuConfigError):
            parse_redis_url("redis://localhost/abc")


if __name__ == "__main__":
    unittest.main()
