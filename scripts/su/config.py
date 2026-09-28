"""SU 三级配置合并、校验与统一 redact 脱敏管线（架构 ARCH-SU-001 §2.3.3）。

职责：
- 三级配置合并（JSON 文件 ← env(SU_*) ← CLI 显式参数，优先级递增）；
- 必填/枚举/正整数校验（validate 返回中文错误列表）；
- 统一 redact() 管线（REQ-SU-002 / NFR-SU-002 红线①）：键名命中敏感模式 →
  ***REDACTED***；字符串值过 PII 值形态正则（值形态优先）；URL 内嵌
  user:pass@ 先剥离（strip_url_credentials）；单值截断 max_str_len；
- URL 解析辅助：--db-url / --redis-url（urllib.parse）。

安全口径：本模块是全 SU 包唯一脱敏入口（§5.2），所有落盘数据必须经
redact() 产出 RedactedDict。明文凭据仅以 SensitiveStr 形态在内存中持有。
"""

import argparse
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import parse_qs, urlparse

from su.dto import (
    REDACTED_PLACEHOLDER,
    RedactedDict,
    SensitiveStr,
    SuConfigError,
)

__all__ = [
    "ENV_OVERRIDES",
    "LoginSelectors",
    "SystemConfig",
    "DatabaseConfig",
    "RedisConfig",
    "RunBudget",
    "SuConfig",
    "load_config",
    "validate",
    "SENSITIVE_KEY_PATTERN",
    "PII_VALUE_PATTERNS",
    "redact",
    "strip_url_credentials",
    "scrub_text",
    "parse_db_url",
    "parse_redis_url",
]

# ---------------------------------------------------------------------------
# 环境变量覆盖表（REQ-SU-001 AC2）：(配置路径元组) → 环境变量名
# ---------------------------------------------------------------------------
ENV_OVERRIDES: Dict[tuple, str] = {
    # 系统登录账号
    ("system", "username"): "SU_SYSTEM_USERNAME",
    # 系统登录密码
    ("system", "password"): "SU_SYSTEM_PASSWORD",
    # 数据库密码
    ("database", "password"): "SU_DB_PASSWORD",
    # Redis 密码
    ("redis", "password"): "SU_REDIS_PASSWORD",
}

# database.engine 合法枚举（3.8 兼容：不用 typing.Literal，运行时校验，ADR-7）
VALID_ENGINES = ("mysql", "postgresql")

# ---------------------------------------------------------------------------
# redact 正则（§2.3.3 / §5.2 / §6.2 三层命中动作表"落盘管线"层）
# ---------------------------------------------------------------------------

# 键名命中即脱敏：键名**完整**等于敏感键（避免误杀如 "password_policy_name"
# 这类含敏感词但非凭据的键；PRD §8"值形态优先"口径）。
SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)^(password|passwd|pwd|secret|token|api_?key|authorization|cookie|session(_?id)?)$"
)

# PII 值形态正则（PRD §8：值形态优先于键名）——手机号/身份证/邮箱/银行卡
PII_VALUE_PATTERNS: Dict[str, "re.Pattern"] = {
    # 中国大陆手机号：1[3-9] 开头 11 位，前后不得粘连数字
    "phone": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    # 中国大陆 18 位身份证（末位可为 X/x）；15 位旧证一并覆盖
    "id_card": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)|(?<!\d)\d{14}[\dXx](?!\d)"),
    # 邮箱（RFC 简化形态）
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    # 银行卡：16~19 位连续数字（前后不得粘连数字）
    "bank_card": re.compile(r"(?<!\d)\d{16,19}(?!\d)"),
}

# PII 替换标签形态：<REDACTED:类型>（与 DataMasker 口径一致，保留可解释性）
_PII_PLACEHOLDER = "<REDACTED:{0}>"

# URL authinfo 段：scheme:// 之后、第一个 '/'（或 '?'/'#'）之前若出现 '@'
# 即为 user:pass@ 凭据形态。
# 凭据字面值按 RFC 3986 不含 '/'，"先截到首个 '/'" 保证 path/query 中的 '@'
# （如 https://host/?next=a@b）不会被误伤。
_URL_AUTHINFO_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*://)([^/?#]*)(.*)$")

# 自由文本内嵌 URL 的 userinfo 掩码正则（scrub_text 专用）。
# FIX(2026-09-28 自测发现)：scrub_text 旧版完全不做 URL 凭据处理，任意
# scheme（含非 http/https 的 redis/mysql/data 等协议、以及嵌在其它协议
# 体内的 URL）的 user:pass@ 凭据随日志/reason 落盘泄露。修正：任意
# scheme 通用正则把 scheme:// 与 authority 内最后一个 '@' 之间的 userinfo
# 整体替换为 ***REDACTED***（.sub 最左不可重叠匹配天然取最后一个 '@'，
# 密码含 '@' 的畸形形态也能整体剥净，不留凭据残渣）。
# 误伤防线：① scheme 正则要求 '://'，mailto:a@b.com 天然不匹配（邮箱交
# PII email 正则）；② userinfo 字符类禁 '/' '?' '#' 与空白，'@' 出现在
# path/query（如 https://host/?next=a@b）不构成 userinfo。
_INLINE_USERINFO_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/?#\s]*@")


# ---------------------------------------------------------------------------
# 配置数据结构（§2.3.3）
# ---------------------------------------------------------------------------

@dataclass
class LoginSelectors:
    """登录页显式选择器（REQ-SU-004；全缺省时走启发式识别）。"""

    username: Optional[str] = None    # 账号输入框 CSS 选择器
    password: Optional[str] = None    # 密码输入框 CSS 选择器
    submit: Optional[str] = None      # 提交按钮 CSS 选择器


@dataclass
class SystemConfig:
    """目标系统配置（必填段，REQ-SU-001）。"""

    base_url: str                     # 系统入口（协议+主机[+端口][+路径]）
    login_url: str                    # 登录页（可为相对路径）
    username: SensitiveStr            # 明文凭据仅内存持有（SensitiveStr 屏蔽 repr）
    password: SensitiveStr            # 明文凭据仅内存持有
    login_selectors: LoginSelectors = field(default_factory=LoginSelectors)
    success_hint: Optional[str] = None            # 登录成功判据选择器（三判据之一）
    allowed_origins: List[str] = field(default_factory=list)
                                           # 域名白名单；默认 = base_url 同源（REQ-SU-007）


@dataclass
class DatabaseConfig:
    """数据库配置（可选段，缺失则 DB 透镜跳过并在文档登记）。"""

    engine: str                       # 'mysql' | 'postgresql'（运行时枚举校验）
    host: str
    port: int
    user: str
    password: SensitiveStr            # 明文凭据仅内存持有
    database: str
    schemas: List[str] = field(default_factory=list)  # 限定 schema；空=排除系统库


@dataclass
class RedisConfig:
    """Redis 配置（可选段，缺失则 Redis 透镜跳过并在文档登记）。"""

    host: str
    port: int
    password: SensitiveStr            # 明文凭据仅内存持有（空密码=SensitiveStr("")）
    db: int = 0
    key_allowlist: List[str] = field(default_factory=list)  # SCAN MATCH 前缀白名单


@dataclass
class RunBudget:
    """覆盖预算与限速参数（REQ-SU-008，§2.3.3 默认值）。"""

    max_pages: int = 100
    max_depth: int = 6
    max_actions_per_page: int = 30
    time_budget_minutes: int = 60
    delay_ms: int = 1500
    page_timeout_ms: int = 30000
    sample_rows: int = 10
    redis_max_keys: int = 5000


@dataclass
class SuConfig:
    """SU 完整运行配置（三级合并后的最终形态）。"""

    system: SystemConfig
    database: Optional[DatabaseConfig]
    redis: Optional[RedisConfig]
    budget: RunBudget
    out_dir: Path                     # 输出根目录（默认 docs/system-understanding/）
    system_id: str                    # 输出子目录名
    resume: bool = True               # 断点策略：True=续跑，False=fresh 归档
    headed: bool = False              # 有头调试模式
    skip_llm_phase: bool = False      # 只跑确定性采集（两阶段工作流阶段A）
    storage_state_path: Optional[Path] = None  # 人工已登录态旁路注入文件


# ---------------------------------------------------------------------------
# 配置加载与合并（REQ-SU-001：CLI > env > JSON）
# ---------------------------------------------------------------------------

def _read_json_dict(path: Path) -> Dict[str, Any]:
    """读取 JSON 配置文件并断言为顶层对象。

    Args:
        path: 配置文件路径。

    Returns:
        dict: 顶层配置对象。

    Raises:
        SuConfigError: 文件不存在/非法 JSON/顶层非对象。
    """
    if not path.is_file():
        raise SuConfigError(
            "配置文件不存在：{0}".format(path),
            hints=["请检查 --config 路径，或改用 CLI 参数 / 环境变量提供凭据"],
        )
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        raise SuConfigError(
            "配置文件读取失败：{0}（{1}）".format(path, exc),
            hints=["请确保文件为 UTF-8 编码的合法 JSON 对象（顶层为 {}）"],
        )
    if not isinstance(data, dict):
        raise SuConfigError(
            "配置文件顶层必须是 JSON 对象：{0}".format(path),
            hints=["顶层应形如 {\"system\": {...}, \"database\": {...}, \"redis\": {...}}"],
        )
    return data


def _merge_json_system(section: Dict[str, Any]) -> Dict[str, Any]:
    """从 JSON 的 system 段构造合并用 dict（凭据值保持字符串，最后包 SensitiveStr）。"""
    return {
        "base_url": section.get("base_url"),
        "login_url": section.get("login_url"),
        "username": section.get("username"),
        "password": section.get("password"),
        "login_selectors": section.get("login_selectors") or {},
        "success_hint": section.get("success_hint"),
        "allowed_origins": list(section.get("allowed_origins") or []),
    }


def _merge_json_database(section: Dict[str, Any]) -> Dict[str, Any]:
    """从 JSON 的 database 段构造合并用 dict。"""
    return {
        "engine": section.get("engine"),
        "host": section.get("host"),
        "port": section.get("port"),
        "user": section.get("user"),
        "password": section.get("password"),
        "database": section.get("database"),
        "schemas": list(section.get("schemas") or []),
    }


def _merge_json_redis(section: Dict[str, Any]) -> Dict[str, Any]:
    """从 JSON 的 redis 段构造合并用 dict。"""
    return {
        "host": section.get("host"),
        "port": section.get("port"),
        "password": section.get("password"),
        "db": section.get("db", 0),
        "key_allowlist": list(section.get("key_allowlist") or []),
    }


def _build_env_overrides(section: Dict[str, Any], path: tuple) -> None:
    """将环境变量覆盖到 section 对应字段（ENV_OVERRIDES 表驱动，AC2）。

    Args:
        section: 待覆盖的段 dict（system/database/redis 合并中间态）。
        path: 字段路径元组，如 ("system", "password")。
    """
    env_name = ENV_OVERRIDES.get(path)
    if env_name is None:
        return
    value = os.environ.get(env_name)
    if value is not None:
        section[path[1]] = value


def _build_budget(args: argparse.Namespace) -> RunBudget:
    """按 CLI 显式参数覆盖构建 RunBudget（未提供的保持默认值）。"""
    budget = RunBudget()
    # 逐项检查 args 中是否存在对应属性且非 None，才做 CLI 覆盖
    cli_map = {
        "max_pages": "max_pages",
        "max_depth": "max_depth",
        "max_actions_per_page": "max_actions_per_page",
        "time_budget_minutes": "time_budget_minutes",
        "delay_ms": "delay_ms",
        "page_timeout_ms": "page_timeout_ms",
        "sample_rows": "sample_rows",
        "redis_max_keys": "redis_max_keys",
    }
    for attr, budget_field in cli_map.items():
        value = getattr(args, attr, None)
        if value is not None:
            setattr(budget, budget_field, int(value))
    return budget


def _build_database(cfg_dict: Dict[str, Any]) -> Optional[DatabaseConfig]:
    """由合并中间态构建 DatabaseConfig（段缺失或显式为 null 时返回 None）。

    Raises:
        SuConfigError: 段存在但必填字段缺失（结构化列出字段名，AC3）。
    """
    if cfg_dict is None:
        return None
    missing = [k for k in ("engine", "host", "port", "user", "database")
               if cfg_dict.get(k) in (None, "")]
    # 密码允许为空字符串（无密码数据库），但 None 视为缺失
    if cfg_dict.get("password") is None:
        missing.append("password")
    if missing:
        raise SuConfigError(
            "database 配置段缺少必填字段：{0}".format("、".join(missing)),
            hints=["在 JSON 配置或 --db-url 中补齐 database.{0} 字段".format(missing[0])],
        )
    return DatabaseConfig(
        engine=str(cfg_dict["engine"]).strip().lower(),
        host=str(cfg_dict["host"]),
        port=int(cfg_dict["port"]),
        user=str(cfg_dict["user"]),
        password=SensitiveStr(cfg_dict["password"]),
        database=str(cfg_dict["database"]),
        schemas=[str(s) for s in (cfg_dict.get("schemas") or [])],
    )


def _build_redis(cfg_dict: Dict[str, Any]) -> Optional[RedisConfig]:
    """由合并中间态构建 RedisConfig（段缺失或显式为 null 时返回 None）。"""
    if cfg_dict is None:
        return None
    missing = [k for k in ("host", "port") if cfg_dict.get(k) in (None, "")]
    if missing:
        raise SuConfigError(
            "redis 配置段缺少必填字段：{0}".format("、".join(missing)),
            hints=["在 JSON 配置或 --redis-url 中补齐 redis.{0} 字段".format(missing[0])],
        )
    return RedisConfig(
        host=str(cfg_dict["host"]),
        port=int(cfg_dict["port"]),
        password=SensitiveStr(cfg_dict.get("password") or ""),
        db=int(cfg_dict.get("db", 0)),
        key_allowlist=[str(p) for p in (cfg_dict.get("key_allowlist") or [])],
    )


def load_config(args: argparse.Namespace) -> SuConfig:
    """三级配置合并主入口：JSON 文件 → env 覆盖 → CLI 覆盖，返回前调用 validate()。

    合并语义（REQ-SU-001 AC1/AC2，优先级递增）：
      1. JSON 文件（--config）提供基础值；
      2. 环境变量（ENV_OVERRIDES 四项）覆盖同名字段；
      3. CLI 显式参数（--system-url/--username/--password/--db-url/--redis-url 等）
         覆盖前两层；
      4. --db-url/--redis-url 解析后整体覆盖对应配置段（URL 通道视为最高层显式输入）。

    Args:
        args: argparse 解析结果（字段缺失时按"未提供"处理，见 §8.1 映射表）。

    Returns:
        SuConfig: 合并并通过 validate() 的最终配置。

    Raises:
        SuConfigError: system 段必填缺失 / database·redis 段字段缺失 /
            validate() 返回非空错误列表（结构化中文列出全部问题字段）。
    """
    # ---- 第 1 层：JSON 文件 ----
    json_data: Dict[str, Any] = {}
    config_path = getattr(args, "config", None)
    if config_path:
        json_data = _read_json_dict(Path(config_path))

    system_section = _merge_json_system(json_data.get("system") or {})
    database_section = _merge_json_database(json_data.get("database") or {}) \
        if json_data.get("database") is not None else None
    redis_section = _merge_json_redis(json_data.get("redis") or {}) \
        if json_data.get("redis") is not None else None

    # ---- 第 2 层：环境变量覆盖 ----
    _build_env_overrides(system_section, ("system", "username"))
    _build_env_overrides(system_section, ("system", "password"))
    if database_section is not None:
        _build_env_overrides(database_section, ("database", "password"))
    if redis_section is not None:
        _build_env_overrides(redis_section, ("redis", "password"))

    # ---- 第 3 层：CLI 显式参数覆盖 ----
    # 系统入口（相对路径登录 URL 与 base_url 拼接由后续阶段处理，此处原样保留）
    if getattr(args, "system_url", None):
        system_section["base_url"] = str(args.system_url)
    if getattr(args, "username", None):
        system_section["username"] = str(args.username)
    if getattr(args, "password", None):
        system_section["password"] = str(args.password)

    # --db-url 整体覆盖 database 段（通道说明见 §8.1：shell 历史风险自担）
    db_url = getattr(args, "db_url", None)
    if db_url:
        database_section = dict(parse_db_url(str(db_url)))
    # --redis-url 整体覆盖 redis 段
    redis_url = getattr(args, "redis_url", None)
    if redis_url:
        redis_section = dict(parse_redis_url(str(redis_url)))

    # 若 system 段完全缺失（无 JSON 且无 CLI 凭据参数），也允许 base_url 缺省到后面统一报错
    system_required = ["base_url", "username", "password"]
    missing = [k for k in system_required
               if system_section.get(k) in (None, "")]
    if missing:
        raise SuConfigError(
            "system 配置段缺少必填字段：{0}".format("、".join(missing)),
            hints=[
                "通过 --config JSON 文件 / --system-url --username --password CLI 参数 / "
                "SU_SYSTEM_USERNAME SU_SYSTEM_PASSWORD 环境变量之一补齐",
            ],
        )

    selectors_raw = system_section.get("login_selectors") or {}
    if not isinstance(selectors_raw, dict):
        raise SuConfigError(
            "system.login_selectors 必须是对象（username/password/submit 选择器）",
            hints=["示例：\"login_selectors\": {\"username\": \"input[name=user]\"}"],
        )

    # 默认 allowed_origins = base_url 同源（REQ-SU-007）
    allowed_origins = list(system_section.get("allowed_origins") or [])
    if not allowed_origins:
        parsed_base = urlparse(str(system_section["base_url"]))
        if parsed_base.scheme and parsed_base.netloc:
            allowed_origins = ["{0}://{1}".format(parsed_base.scheme, parsed_base.netloc)]

    # CLI 追加白名单域（--allowed-origins 逗号分隔）
    extra_origins = getattr(args, "allowed_origins", None)
    if extra_origins:
        for item in str(extra_origins).split(","):
            item = item.strip()
            if item and item not in allowed_origins:
                allowed_origins.append(item)

    system_cfg = SystemConfig(
        base_url=str(system_section["base_url"]),
        login_url=str(system_section.get("login_url") or "/login"),
        username=SensitiveStr(system_section["username"]),
        password=SensitiveStr(system_section["password"]),
        login_selectors=LoginSelectors(
            username=selectors_raw.get("username"),
            password=selectors_raw.get("password"),
            submit=selectors_raw.get("submit"),
        ),
        success_hint=selectors_raw.get("success_hint") or system_section.get("success_hint"),
        allowed_origins=allowed_origins,
    )

    database_cfg = _build_database(database_section)
    redis_cfg = _build_redis(redis_section)
    budget = _build_budget(args)

    # 输出目录 / 系统名 / 运行策略（CLI 层参数，默认值对齐 REQ-SU-020 参数表）
    out_dir = Path(getattr(args, "out", None) or "docs/system-understanding")
    system_id = str(getattr(args, "system_id", None) or _derive_system_id(system_cfg.base_url))
    resume = bool(getattr(args, "resume", True))
    fresh = bool(getattr(args, "fresh", False))
    headed = bool(getattr(args, "headed", False))
    skip_llm_phase = bool(getattr(args, "skip_llm_phase", False))
    storage_state = getattr(args, "storage_state", None)

    cfg = SuConfig(
        system=system_cfg,
        database=database_cfg,
        redis=redis_cfg,
        budget=budget,
        out_dir=out_dir,
        system_id=system_id,
        # --fresh 显式给出时强制归档重跑，覆盖 --resume
        resume=(not fresh) and resume,
        headed=headed,
        skip_llm_phase=skip_llm_phase,
        storage_state_path=Path(storage_state) if storage_state else None,
    )

    # ---- 统一校验（失败抛 SuConfigError，结构化列出字段名，AC3）----
    errors = validate(cfg)
    if errors:
        raise SuConfigError(
            "配置校验未通过（共 {0} 项）：{1}".format(len(errors), "；".join(errors)),
            hints=["修正上述字段后重试；字段位置见 system/database/redis 配置段"],
        )
    return cfg


def _derive_system_id(base_url: str) -> str:
    """由 base_url 派生默认 system_id（主机名小写 + 去端口，REQ-SU-020）。

    Returns:
        str: 形如 'admin.example.com' 的目录名（非法字符替换为下划线）。
    """
    host = urlparse(base_url).netloc.split(":")[0].lower() or "system"
    # 目录名安全化：非 [A-Za-z0-9._-] 一律替换为下划线
    return re.sub(r"[^A-Za-z0-9._-]", "_", host)


def validate(cfg: SuConfig) -> List[str]:
    """配置最终校验，返回中文错误列表（空列表=通过）。

    校验项（REQ-SU-001 AC3）：
      - database.engine ∈ {mysql, postgresql}；
      - base_url 必须含协议与主机（可解析）；
      - budget 各值为正整数（delay_ms/page_timeout_ms 等）；
      - 端口范围 1~65535；sample_rows ≤ 50（PRD REQ-SU-012 上限）。

    Args:
        cfg: 待校验配置。

    Returns:
        list[str]: 错误描述列表（每项含字段名与合法值范围）。
    """
    errors: List[str] = []

    # base_url 可解析性
    parsed = urlparse(cfg.system.base_url)
    if not parsed.scheme or not parsed.netloc:
        errors.append("system.base_url 不可解析（需含协议与主机，如 https://admin.example.com）")

    # 端口范围校验
    if cfg.database is not None:
        if not (1 <= cfg.database.port <= 65535):
            errors.append("database.port 超出范围 1~65535：{0}".format(cfg.database.port))
        if cfg.database.engine not in VALID_ENGINES:
            errors.append(
                "database.engine 非法值：'{0}'（合法值：{1}）".format(
                    cfg.database.engine, "、".join(VALID_ENGINES))
            )
    if cfg.redis is not None and not (1 <= cfg.redis.port <= 65535):
        errors.append("redis.port 超出范围 1~65535：{0}".format(cfg.redis.port))

    # 预算正整数校验
    budget_fields = [
        "max_pages", "max_depth", "max_actions_per_page",
        "time_budget_minutes", "delay_ms", "page_timeout_ms",
        "sample_rows", "redis_max_keys",
    ]
    for name in budget_fields:
        value = getattr(cfg.budget, name)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            errors.append("budget.{0} 必须为正整数：{1!r}".format(name, value))

    # 采样行数上限（PRD REQ-SU-012：默认 10，上限 50）
    if isinstance(cfg.budget.sample_rows, int) and cfg.budget.sample_rows > 50:
        errors.append("budget.sample_rows 超出上限 50：{0}".format(cfg.budget.sample_rows))

    return errors


# ---------------------------------------------------------------------------
# URL 内嵌凭据剥离与自由文本脱敏（红线①，§5.2）
# ---------------------------------------------------------------------------

def strip_url_credentials(url: str) -> str:
    """剥离 URL 内嵌 user:pass@ 凭据段（红线①：文档与日志中禁止明文 URL 凭据）。

    实现：定位 '://' 之后、第一个 '/'（或字符串末尾）之前的 authinfo 段，
    若该段含 '@'（即 user:pass@ 形态）则截去 scheme:// 与 '@' 之间的整个
    凭据段；scheme/host 正常保留。scheme 通用（http/https/mysql/redis/
    postgres 等任意协议名均覆盖）。'@' 出现在 path/query 中不构成误伤
    （如 https://example.com/?next=a@b 中 '@' 在 '/' 之后，不属于 authinfo）。

    Args:
        url: 原始 URL（可能含 user:pass@，也可能已脱敏/无凭据）。

    Returns:
        str: 剥离凭据后的 URL；无凭据段时原样返回。
    """
    if not url:
        return ""
    match = _URL_AUTHINFO_RE.match(url)
    if not match:
        # 非 URL 形态（无 scheme://）原样返回
        return url
    scheme, authority, rest = match.group(1), match.group(2), match.group(3)
    if "@" not in authority:
        # authinfo 段无 '@'：无内嵌凭据，原样返回
        return url
    # authority = [user[:pass]@]host[:port] —— 丢弃最后一个 '@' 之前全部凭据段
    host = authority.rsplit("@", 1)[1]
    return scheme + host + rest


def scrub_text(text: str) -> str:
    """自由文本（页面文案/日志/API 文本片段）PII 扫描替换。

    双通道策略（PRD §8"值形态优先"）：
      1. 按 PII_VALUE_PATTERNS 值形态正则替换为 <REDACTED:类型>
         （优先级更高，不依赖键名语境），键名语境由 redact() 在 dict
         结构中处理；
      2. URL 内嵌 user:pass@ 凭据掩码——任意 scheme 通用（见
         :data:`_INLINE_USERINFO_RE`，FIX 2026-09-28 自测发现：旧版仅
         覆盖顶层整串 http/https 判定，非 http 协议与嵌套协议文本的
         凭据随 reason/日志落盘泄露）。
      供 crawler/api_observer 对非结构化文本复用。

    Args:
        text: 原始自由文本。

    Returns:
        str: PII 值形态替换 + URL 凭据掩码后的文本；None/空串原样返回。
    """
    if not text:
        return text
    # PII 值形态双通道之一（值形态优先）：先替换，避免凭据段内的邮箱等
    # 形态被 URL 掩码吞掉类型信息
    for pii_type, pattern in PII_VALUE_PATTERNS.items():
        text = pattern.sub(_PII_PLACEHOLDER.format(pii_type), text)
    # 任意 scheme URL 内嵌凭据整体掩码（红线①：userinfo 不可读回）
    return _INLINE_USERINFO_RE.sub(lambda m: m.group(1) + REDACTED_PLACEHOLDER + "@", text)


# ---------------------------------------------------------------------------
# 统一 redact 管线（§5.2：全 SU 包唯一脱敏入口，NFR-SU-002 红线①）
# ---------------------------------------------------------------------------

def _redact_string(value: str, max_str_len: int) -> str:
    """对单个字符串执行值形态脱敏 + URL 凭据剥离 + 截断。

    顺序（值形态优先于键名，PRD §8）：
      1. 若字符串是 URL 形态（scheme:// 开头）→ 先 strip_url_credentials；
      2. 过 PII 值形态正则（手机号/身份证/邮箱/银行卡）→ <REDACTED:类型>；
      3. 截断到 max_str_len（单值上限，NFR-SU-005）。
    """
    if not isinstance(value, str):
        return value
    # URL 内嵌凭据先剥离（防凭据经 URL 落盘）
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        value = strip_url_credentials(value)
    # PII 值形态双通道之一（值形态优先）
    for pii_type, pattern in PII_VALUE_PATTERNS.items():
        value = pattern.sub(_PII_PLACEHOLDER.format(pii_type), value)
    # 单值截断
    if len(value) > max_str_len:
        value = value[:max_str_len]
    return value


def redact(obj: Any, *, max_str_len: int = 200) -> Union[RedactedDict, list, str]:
    """递归脱敏 dict/list/str（REQ-SU-002 全模块共用，NFR-SU-002 红线①）。

    规则（§5.2 落盘管线层）：
      - dict：键名完整命中 SENSITIVE_KEY_PATTERN → 值直接替换 ***REDACTED***
        （不递归其值，杜绝敏感值任何形态残留）；否则值递归脱敏；
        dict 输入必返 RedactedDict（落盘入口唯一合法 dict 类型）。
      - list：逐项递归，返回普通 list（顶层 list 由调用方自行包装）。
      - str：过 _redact_string（URL 凭据剥离 + PII 值形态 + 截断），返回 str。
      - 其他（int/float/bool/None）：原样返回。

    Args:
        obj: 任意来源数据（配置/HTTP body/DB 采样/Redis 值/页面文案/日志）。
        max_str_len: 单个字符串值截断上限，默认 200。

    Returns:
        RedactedDict | list | str | 原始标量：dict 输入必返 RedactedDict；
        list/str 顶层输入返回脱敏后的同形值（调用方再按需包装）。
    """
    if isinstance(obj, dict):
        result: Dict[str, Any] = {}
        for key, value in obj.items():
            key_str = str(key)
            if SENSITIVE_KEY_PATTERN.match(key_str):
                # 键名命中：整个值替换为占位符，不做任何形态保留
                result[key] = REDACTED_PLACEHOLDER
            else:
                result[key] = redact(value, max_str_len=max_str_len)
        return RedactedDict(result)
    if isinstance(obj, (list, tuple)):
        # 递归每个元素；tuple 归一为 list（JSON 语义）
        return [redact(item, max_str_len=max_str_len) for item in obj]
    if isinstance(obj, SensitiveStr):
        # SensitiveStr 绝不落盘明文：直接输出占位符
        return REDACTED_PLACEHOLDER
    if isinstance(obj, str):
        return _redact_string(obj, max_str_len)
    # 标量（int/float/bool/None）原样返回
    return obj


# ---------------------------------------------------------------------------
# URL 解析辅助（--db-url / --redis-url，§8.1：urllib.parse，shell 风险自担提示）
# ---------------------------------------------------------------------------

def parse_db_url(url: str) -> Dict[str, Any]:
    """解析 --db-url 为 database 配置段 dict（urllib.parse 实现）。

    支持 scheme：
      - mysql://user:pass@host:3306/dbname[?charset=utf8mb4]
      - postgresql://user:pass@host:5432/dbname（postgres:// 同义）
    默认端口：mysql=3306、postgresql=5432；query 参数中若含 schemas=
    （逗号分隔）则解析进 schemas 列表。

    Args:
        url: 用户提供的数据库连接串。

    Returns:
        dict: {engine, host, port, user, password, database, schemas}。

    Raises:
        SuConfigError: scheme 不合法 / 缺主机 / 缺数据库名（结构化中文报错）。
    """
    scheme_alias = {"postgres": "postgresql"}
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    scheme = scheme_alias.get(scheme, scheme)
    if scheme not in VALID_ENGINES:
        raise SuConfigError(
            "--db-url 协议非法：'{0}'（合法值：mysql://、postgresql://）".format(parsed.scheme),
            hints=["示例：mysql://<user>:<pass>@host:3306/<db>"],
        )
    if not parsed.hostname:
        raise SuConfigError(
            "--db-url 缺少主机：{0}".format(url),
            hints=["示例：mysql://<user>:<pass>@127.0.0.1:3306/<db>"],
        )
    if not parsed.path or parsed.path == "/":
        raise SuConfigError(
            "--db-url 缺少数据库名：{0}".format(url),
            hints=["在路径部分指定数据库名，如 mysql://<user>:<pass>@host:3306/appdb"],
        )
    default_port = 3306 if scheme == "mysql" else 5432
    # query 中可选 schemas 参数（逗号分隔），其余参数忽略
    query_schemas: List[str] = []
    for key, values in parse_qs(parsed.query).items():
        if key == "schemas" and values:
            query_schemas = [s.strip() for s in values[0].split(",") if s.strip()]
    return {
        "engine": scheme,
        "host": parsed.hostname,
        "port": parsed.port or default_port,
        "user": parsed.username or "",
        "password": parsed.password or "",
        "database": parsed.path.lstrip("/"),
        "schemas": query_schemas,
    }


def parse_redis_url(url: str) -> Dict[str, Any]:
    """解析 --redis-url 为 redis 配置段 dict（urllib.parse 实现）。

    支持形态：
      - redis://:pass@host:6379/0      （密码在前、用户省略）
      - redis://user:pass@host:6379/2  （含用户名，并入密码段兼容旧版 AUTH user pass 之外的场景）
      - redis://host:6379/0            （无密码）
    默认端口 6379；路径为 db 序号（默认 0）。

    Args:
        url: 用户提供的 Redis 连接串。

    Returns:
        dict: {host, port, password, db}。

    Raises:
        SuConfigError: scheme 不是 redis/rediss / 缺主机（结构化中文报错）。
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("redis", "rediss"):
        raise SuConfigError(
            "--redis-url 协议非法：'{0}'（合法值：redis://）".format(parsed.scheme),
            hints=["示例：redis://:***@host:6379/0"],
        )
    if not parsed.hostname:
        raise SuConfigError(
            "--redis-url 缺少主机：{0}".format(url),
            hints=["示例：redis://:***@127.0.0.1:6379/0"],
        )
    # db 序号：路径形如 '/0'；空路径视为 0
    db = 0
    if parsed.path and parsed.path != "/":
        db_raw = parsed.path.lstrip("/")
        if not db_raw.isdigit():
            raise SuConfigError(
                "--redis-url 路径必须是 db 序号（数字）：'{0}'".format(parsed.path),
                hints=["示例：redis://:***@host:6379/0"],
            )
        db = int(db_raw)
    return {
        "host": parsed.hostname,
        "port": parsed.port or 6379,
        "password": parsed.password or "",
        "db": db,
    }
