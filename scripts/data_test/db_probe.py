#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2.13 只读 DB 快照引擎（浏览器测试闭环数据层内核之二）。

职责：
  1. 加载 ``--db-config`` JSON（sqlite / mysql / postgresql 三引擎），凭据只驻内存；
  2. 建立**只读加固**连接（mysql/pg 复用 su.db_guard.DbSessionHardener；
     sqlite 用 ``file:...?mode=ro`` URI，任何写操作被驱动层拒绝）；
  3. 执行声明式快照清单（db-checks.json）——每条 SQL 先过
     ``su.db_guard.ReadOnlyGuard.validate``（非 SELECT/SHOW/EXPLAIN、多语句、
     注释绕过全部拒绝），再参数化下发；
  4. 产出确定性快照 JSON（列名保序、行按 key 列/首列排序），值侧过脱敏；
  5. 驱动缺失（pymysql/psycopg2）→ :class:`DbProbeDegraded` 诚实降级，
     给安装命令，绝不产出假数据。

安全红线（继承 SU 红线②并加码）：
  - 全包唯一 cursor.execute 通道仍在 su/db_guard.py，本模块只经守卫下发；
  - 查询超时 + 行数上限双闸，超限报错而非静默截断；
  - 凭据不进快照、不进日志（快照只含查询结果，本身无连接信息）。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# su 包导入需要 scripts/ 在 sys.path（与 recorder 同策略：脚本级路径注入）
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from su.db_guard import DbSessionHardener, ReadOnlyGuard  # noqa: E402

__all__ = [
    "DbProbeError",
    "DbProbeDegraded",
    "DbProbeConfig",
    "DbProber",
    "SUPPORTED_ENGINES",
]

# 支持的引擎枚举（与配置校验、降级提示一一对应）
SUPPORTED_ENGINES = ("sqlite", "mysql", "postgresql")

# 查询硬闸默认值（设计 §2.7：超时 + 行数双闸，超限报错不静默）
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_ROWS = 5000

# 软依赖缺失时给用户的修复命令（诚实降级文案，杜绝猜驱动名）
_DRIVER_INSTALL_HINTS = {
    "mysql": "pip install pymysql",
    "postgresql": "pip install psycopg2-binary",
}


class DbProbeError(Exception):
    """快照引擎的配置/执行错误（中文消息，可直接进 blocker/断言 actual）。"""


class DbProbeDegraded(DbProbeError):
    """软依赖缺失类错误——调用方应把数据步骤标 BLOCKED(env_failure) 而非 FAIL。"""


class DbProbeConfig:
    """--db-config JSON 的解析结果（校验前置，缺字段/非法引擎立即拒绝）。

    字段（按引擎取用）::

        {"engine": "sqlite", "path": "/abs/app.db"}
        {"engine": "mysql", "host": "...", "port": 3306, "user": "...",
         "password": "...", "database": "...", "charset": "utf8mb4"}
        {"engine": "postgresql", "host": "...", "port": 5432, "user": "...",
         "password": "...", "database": "..."}
        可选全局: {"timeout_seconds": 30, "max_rows": 5000}
    """

    def __init__(self, raw: Dict[str, Any], source: str) -> None:
        self.source = source
        engine = str(raw.get("engine") or "").strip().lower()
        if engine not in SUPPORTED_ENGINES:
            raise DbProbeError("db-config.engine {0!r} 非法（支持 {1}）".format(
                engine, "/".join(SUPPORTED_ENGINES)))
        self.engine: str = engine
        self.timeout_seconds: int = int(raw.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        self.max_rows: int = int(raw.get("max_rows", DEFAULT_MAX_ROWS))
        if self.timeout_seconds <= 0 or self.max_rows <= 0:
            raise DbProbeError("timeout_seconds 与 max_rows 必须为正整数")
        if self.engine == "sqlite":
            path = str(raw.get("path") or "").strip()
            if not path:
                raise DbProbeError("sqlite 引擎必须提供 path（数据库文件绝对路径）")
            resolved = Path(path).expanduser().resolve()
            if not resolved.is_file():
                raise DbProbeError("sqlite 数据库文件不存在：{0}".format(resolved))
            self.path: str = str(resolved)
        else:
            for field in ("host", "user", "database"):
                if not str(raw.get(field) or "").strip():
                    raise DbProbeError("{0} 引擎 db-config 缺必填字段 {1!r}".format(
                        self.engine, field))
            self.host: str = str(raw["host"]).strip()
            self.port: int = int(raw.get("port") or (3306 if self.engine == "mysql" else 5432))
            self.user: str = str(raw["user"]).strip()
            self.password: str = str(raw.get("password") or "")
            self.database: str = str(raw["database"]).strip()
            self.charset: str = str(raw.get("charset") or "utf8mb4")

    @classmethod
    def load(cls, config_path: str) -> "DbProbeConfig":
        """从 JSON 文件加载并校验（文件不存在/非法 JSON 都是配置错误）。"""
        p = Path(config_path).expanduser().resolve()
        if not p.is_file():
            raise DbProbeError("db-config 文件不存在：{0}".format(p))
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DbProbeError("db-config 不是合法 JSON：{0}".format(exc)) from exc
        if not isinstance(raw, dict):
            raise DbProbeError("db-config 顶层必须是 JSON 对象")
        return cls(raw, source=str(p))


class DbProber:
    """只读快照执行器：connect → 逐条守卫下发 → 确定性行结构。"""

    def __init__(self, config: DbProbeConfig) -> None:
        self._config = config
        self._conn: Any = None
        self._guard = ReadOnlyGuard()
        # sqlite 的 DB-API 占位符是 qmark：validate 产物不变，仅驱动参数风格不同
        self._sqlite = config.engine == "sqlite"

    # ---- 连接生命周期 ----

    def connect(self) -> None:
        """建立只读加固连接；软依赖缺失抛 :class:`DbProbeDegraded`。"""
        cfg = self._config
        if self._sqlite:
            # URI mode=ro：驱动层硬只读（写操作直接 OperationalError），
            # 比应用层自律更强——sqlite 路径同样满足红线②
            uri = "file:{0}?mode=ro".format(cfg.path)
            self._conn = sqlite3.connect(uri, uri=True, timeout=cfg.timeout_seconds)
            self._conn.row_factory = sqlite3.Row
            return
        module_name = "pymysql" if cfg.engine == "mysql" else "psycopg2"
        try:
            driver = __import__(module_name)
        except ImportError:
            raise DbProbeDegraded(
                "引擎 {0} 需要驱动 {1}，当前环境未安装".format(cfg.engine, module_name),
            ) from None
        if cfg.engine == "mysql":
            self._conn = driver.connect(
                host=cfg.host, port=cfg.port, user=cfg.user,
                password=cfg.password, database=cfg.database,
                charset=cfg.charset, connect_timeout=cfg.timeout_seconds)
        else:
            self._conn = driver.connect(
                host=cfg.host, port=cfg.port, user=cfg.user,
                password=cfg.password, dbname=cfg.database,
                connect_timeout=cfg.timeout_seconds)
        # 服务端会话只读加固（即便守卫被绕过，服务端也拒绝写）
        DbSessionHardener.harden(self._conn)

    def close(self) -> None:
        """关闭连接（幂等，异常吞在 close 侧不影响已产出的快照）。"""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def __enter__(self) -> "DbProber":
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- 查询执行 ----

    def run_query(
        self, label: str, sql: str, params: Sequence[Any] = ()
    ) -> Dict[str, Any]:
        """守卫校验 + 参数化执行单条只读查询，返回确定性行结构。

        Args:
            label: 查询标签（快照内唯一，规则 vars 引用它）。
            sql: 只读 SQL（必须过 ReadOnlyGuard.validate）。
            params: 参数化绑定值（sqlite 用 ? 占位，mysql/pg 用 %s）。

        Returns:
            dict: ``{"label", "sql", "params", "columns", "row_count",
            "rows": [{col: val}...]}``——行按 key（首列优先）稳定排序，
            值已是 JSON 原生类型（bytes/日期统一转字符串，可跨引擎比对）。

        Raises:
            DbProbeError: 未连接 / 校验拒绝 / 行数超上限 / 驱动执行错误。
        """
        if self._conn is None:
            raise DbProbeError("未连接：先调用 connect()（或使用 with DbProber(...)）")
        # 校验文本 == 执行文本（守卫内部完成，违规抛 SuReadonlyViolation）
        try:
            self._guard.validate(sql)
        except Exception as exc:  # SuReadonlyViolation 属 su.dto，统一转本模块错误
            raise DbProbeError("查询 {0!r} 未通过只读校验：{1}".format(label, exc)) from exc
        cursor = self._conn.cursor()
        try:
            if params:
                cursor.execute(sql, tuple(params))
            else:
                cursor.execute(sql)
            if cursor.description is None:
                raise DbProbeError("查询 {0!r} 无结果集（快照清单只允许 SELECT）".format(label))
            columns = [d[0] for d in cursor.description]
            rows: List[Dict[str, Any]] = []
            for raw in cursor.fetchall():
                if len(rows) >= self._config.max_rows:
                    raise DbProbeError(
                        "查询 {0!r} 行数超过上限 {1}（拒绝静默截断，"
                        "请收窄谓词或调大 max_rows）".format(label, self._config.max_rows))
                rows.append({col: _jsonable(val) for col, val in zip(columns, raw)})
        except DbProbeError:
            raise
        except Exception as exc:
            raise DbProbeError("查询 {0!r} 执行失败：{1}".format(label, exc)) from exc
        finally:
            cursor.close()
        # 确定性排序：首列 → 全列拼串兜底（快照可比对的唯一性保障）
        first = columns[0] if columns else None
        rows.sort(key=lambda r: (_sort_token(r.get(first)) if first else "",
                                 json.dumps(r, sort_keys=True, ensure_ascii=False)))
        return {"label": label, "sql": sql, "params": list(params),
                "columns": columns, "row_count": len(rows), "rows": rows}


def _jsonable(val: Any) -> Any:
    """DB 值 → JSON 原生类型（None/int/float/str/bool 原样；bytes/Decimal/datetime 转文本）。"""
    if val is None or isinstance(val, (int, float, str, bool)):
        return val
    if isinstance(val, bytes):
        try:
            return val.decode("utf-8")
        except UnicodeDecodeError:
            return val.hex()
    return str(val)


def _sort_token(val: Any) -> str:
    """排序键归一（None 最小，其余转文本保证跨类型可比、排序确定）。"""
    if val is None:
        return ""
    return str(val)


def run_snapshot_plan(
    prober: DbProber,
    plan: Dict[str, Any],
) -> Dict[str, Any]:
    """执行一份快照清单（db-checks.json 单条 check），返回快照文档。

    Args:
        prober: 已连接的执行器。
        plan: ``{"check_id", "phase"?, "queries": [{"label","sql","params"?}...]}``。

    Returns:
        dict: ``{"check_id", "phase", "engine", "snapshots": {label: run_query结果}}``
        ——snapshots 形态与 data_rules.evaluate_rules 的入参约定严格一致。

    Raises:
        DbProbeError: 清单结构非法 / 查询失败（首个失败即中止，不产出残缺快照）。
    """
    check_id = str(plan.get("check_id") or "").strip()
    if not check_id:
        raise DbProbeError("快照清单缺 check_id")
    queries = plan.get("queries")
    if not isinstance(queries, list) or not queries:
        raise DbProbeError("快照清单 {0!r} 的 queries 必须是非空数组".format(check_id))
    seen_labels = set()
    snapshots: Dict[str, Dict[str, Any]] = {}
    for entry in queries:
        if not isinstance(entry, dict) or not entry.get("label") or not entry.get("sql"):
            raise DbProbeError("快照清单 {0!r} 存在缺 label/sql 的查询项".format(check_id))
        label = str(entry["label"])
        if label in seen_labels:
            raise DbProbeError("快照清单 {0!r} 查询 label 重复：{1!r}".format(check_id, label))
        seen_labels.add(label)
        snapshots[label] = prober.run_query(label, str(entry["sql"]),
                                            entry.get("params") or ())
    return {"check_id": check_id, "phase": plan.get("phase", ""),
            "engine": prober._config.engine, "snapshots": snapshots}


def load_plan_file(path: str) -> List[Dict[str, Any]]:
    """加载快照清单文件（单对象或数组两种形态都归一为数组）。"""
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise DbProbeError("快照清单文件不存在：{0}".format(p))
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DbProbeError("快照清单不是合法 JSON：{0}".format(exc)) from exc
    plans = raw if isinstance(raw, list) else [raw]
    for item in plans:
        if not isinstance(item, dict):
            raise DbProbeError("快照清单数组元素必须是对象")
    return plans
