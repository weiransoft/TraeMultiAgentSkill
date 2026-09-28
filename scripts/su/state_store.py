"""SU SQLite 状态机：schema 迁移、锁/心跳、resume、幂等写入（§2.3.11 / §3 DDL）。

核心设计（REQ-SU-019）：
- 唯一事实源：所有采集先落本库，文档是状态库的纯函数视图（AP-5）；
- 进程互斥**唯一机制** = WAL + `BEGIN IMMEDIATE` 事务内对 run_meta 的条件更新
  （2026-09-28 审查修订：删除 fcntl/run.lock 双机制，锁状态即数据库行状态）；
- 心跳 >60s 视为陈旧锁，条件 UPDATE 天然满足即原子接管；
- 全部采集写入 INSERT OR IGNORE / UPSERT 幂等（采集先查后采）；
- 写盘入口运行时 isinstance 断言只接受 RedactedDict / *Redacted
  （REQ-SU-002 AC3 四层组合之②）；
- SIGINT：flush() + mark('interrupted') 后以 130 退出（§8.2）。
"""

import json
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from su.dto import (
    ActionDecisionRedacted,
    ApiObservationRedacted,
    BlockedEventRedacted,
    DbTableRedacted,
    EdgeRedacted,
    ImplicitFkCandidateRedacted,
    PageNodeRedacted,
    RedactedDict,
    RedisKeyRecordRedacted,
    RelationRedacted,
    SuConfigError,
    SuLockHeldError,
)

__all__ = ["SCHEMA_VERSION", "HEARTBEAT_STALE_SECONDS", "StateStore"]

# 迁移版本号（当前 1，schema_meta 表记录）
SCHEMA_VERSION = 1
# 心跳陈旧阈值（秒）：>60s 未更新 = 陈旧锁，可被条件 UPDATE 接管
HEARTBESTALE_GUARD = 60
HEARTBEAT_STALE_SECONDS = HEARTBESTALE_GUARD

# mark_action_executed 补录路径的 rule_name 占位文本（2026-09-28 e2e 场景[2]
# 根因修复）：T2 执行时序先于分级落库（crawler 主循环 _execute_t2_forms 在
# _classify_and_dispatch 之前），executed 回写时目标行可能尚未插入。
# "已真实执行"是事实、必须落库；rule_name 属分级器解释面，补录行用本占位
# 标注来源，后续分级轮次的 insert_action（INSERT OR IGNORE）不会覆盖它。
MARK_EXECUTED_BACKFILL_RULE = "T2 执行回写补录（分级行缺失）"

# ---------------------------------------------------------------------------
# 完整 DDL（§3 全量建表；page_actions 无 result 列——2026-09-28 审查删除）
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    version     INTEGER NOT NULL             -- 迁移版本号，当前 1
);

CREATE TABLE IF NOT EXISTS run_meta (
    run_id          TEXT PRIMARY KEY,
    system_id       TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN
                      ('running','interrupted','completed','failed')),
    locked_by       TEXT,
    heartbeat_ts    REAL,
    started_at      REAL NOT NULL,
    finished_at     REAL,
    exit_reason     TEXT,
    config_snapshot TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pages (
    page_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    url_key       TEXT NOT NULL UNIQUE,
    url           TEXT NOT NULL,
    title         TEXT,
    depth         INTEGER NOT NULL,
    discover_from INTEGER REFERENCES pages(page_id),
    status        TEXT NOT NULL CHECK (status IN
                    ('pending','exploring','done','timeout','error')),
    snapshot_path TEXT,
    tech_fingerprint TEXT,
    error         TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pages_status_depth ON pages(status, depth);
CREATE INDEX IF NOT EXISTS idx_pages_parent ON pages(discover_from);

CREATE TABLE IF NOT EXISTS page_actions (
    action_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id     INTEGER NOT NULL REFERENCES pages(page_id),
    element_sig TEXT NOT NULL,
    tier        TEXT NOT NULL CHECK (tier IN ('T1','T2','T3')),
    rule_name   TEXT NOT NULL,
    executed    INTEGER NOT NULL DEFAULT 0,
    UNIQUE(page_id, element_sig)
);

CREATE TABLE IF NOT EXISTS edges (
    edge_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    from_key TEXT NOT NULL,
    to_key   TEXT NOT NULL,
    via_action INTEGER REFERENCES page_actions(action_id),
    UNIQUE(from_key, to_key, via_action)
);

CREATE TABLE IF NOT EXISTS api_observations (
    endpoint_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    url_path     TEXT NOT NULL,
    method       TEXT NOT NULL,
    latest_status INTEGER,
    request_shape  TEXT,
    response_shape TEXT,
    sample_count INTEGER NOT NULL DEFAULT 0,
    samples      TEXT,
    observed_on_pages TEXT NOT NULL,
    UNIQUE(url_path, method)
);

CREATE TABLE IF NOT EXISTS blocked_events (
    block_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    kind      TEXT NOT NULL CHECK (kind IN
                ('aborted_method','blocked_origin','download','new_window')),
    url       TEXT NOT NULL,
    method    TEXT,
    post_data TEXT,
    page_id   INTEGER,
    ts        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_blocked_kind ON blocked_events(kind);

CREATE TABLE IF NOT EXISTS db_tables (
    table_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_name TEXT NOT NULL,
    table_name  TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('BASE TABLE','VIEW')),
    row_estimate INTEGER,
    comment     TEXT,
    columns_json TEXT NOT NULL,
    UNIQUE(schema_name, table_name)
);

CREATE TABLE IF NOT EXISTS db_columns (
    column_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id   INTEGER NOT NULL REFERENCES db_tables(table_id),
    name       TEXT NOT NULL,
    data_type  TEXT NOT NULL,
    type_family TEXT NOT NULL CHECK (type_family IN ('int','string','uuid','other')),
    is_pk      INTEGER NOT NULL DEFAULT 0,
    fk_target  TEXT,
    comment    TEXT,
    UNIQUE(table_id, name)
);
CREATE INDEX IF NOT EXISTS idx_dbcol_name ON db_columns(name);

CREATE TABLE IF NOT EXISTS db_samples (
    sample_id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id  INTEGER NOT NULL REFERENCES db_tables(table_id),
    row_json  TEXT NOT NULL,
    row_no    INTEGER NOT NULL,
    UNIQUE(table_id, row_no)
);

CREATE TABLE IF NOT EXISTS implicit_fk_candidates (
    cand_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    child_table  TEXT NOT NULL,
    child_column TEXT NOT NULL,
    parent_table TEXT NOT NULL,
    parent_column TEXT NOT NULL,
    prescreen_score REAL NOT NULL,
    stopped_at_rule INTEGER NOT NULL,
    containment REAL,
    evidence_json TEXT NOT NULL,
    UNIQUE(child_table, child_column, parent_table, parent_column)
);

CREATE TABLE IF NOT EXISTS redis_keys (
    key_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    key_name  TEXT NOT NULL UNIQUE,
    key_type  TEXT NOT NULL,
    ttl_ms    INTEGER,
    encoding  TEXT,
    mem_bytes INTEGER,
    value_sample TEXT
);

CREATE TABLE IF NOT EXISTS redis_patterns (
    pattern_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern      TEXT NOT NULL UNIQUE,
    key_count    INTEGER NOT NULL,
    no_ttl_ratio REAL NOT NULL,
    ttl_summary  TEXT,
    type_summary TEXT,
    sample_keys  TEXT
);

CREATE TABLE IF NOT EXISTS relations (
    relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    rtype       TEXT NOT NULL CHECK (rtype IN ('page_api','api_table','redis_entity')),
    left_ref    TEXT NOT NULL,
    right_ref   TEXT NOT NULL,
    score       REAL NOT NULL,
    evidence_json TEXT NOT NULL,
    UNIQUE(rtype, left_ref, right_ref)
);

CREATE TABLE IF NOT EXISTS findings (
    finding_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    claim         TEXT NOT NULL,
    confidence    TEXT NOT NULL CHECK (confidence IN ('high','medium','low')),
    evidence_refs TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'proposed'
                    CHECK (status IN ('proposed','rendered','human_confirmed','rejected')),
    kind          TEXT NOT NULL CHECK (kind IN
                    ('mapping','business_rule','redis_entity','semantic_name')),
    created_at    REAL NOT NULL
);
"""

# evidence_refs '<table>:<id>' 的表名 → 实际表与主键列映射（存在性校验用）
_EVIDENCE_TABLES: Dict[str, str] = {
    "pages": "page_id",
    "page_actions": "action_id",
    # 表名与引用前缀同名（api_observations），避免别名指向不存在的物理表
    "api_observations": "endpoint_id",
    "db_tables": "table_id",
    "db_columns": "column_id",
    "db_samples": "sample_id",
    "implicit_fk_candidates": "cand_id",
    "redis_keys": "key_id",
    "redis_pattern": "pattern_id",
    "redis_patterns": "pattern_id",
    "relations": "relation_id",
    "blocked_events": "block_id",
    "findings": "finding_id",
}

# claim 键值对形态凭据正则（§6.2 收窄口径：仅"敏感键名 + 冒号/等号 + 非空值"，
# 不做全文 PII 正则——业务文本合法提及"token 列"等词组不构成泄露）
_CLAIM_CREDENTIAL_RE = re.compile(r"(?i)(password|token|api[_\-]?key)\s*[:=]\s*\S+")

# findings 合法枚举（§6.2）
_FINDING_CONFIDENCES = ("high", "medium", "low")
_FINDING_KINDS = ("mapping", "business_rule", "redis_entity", "semantic_name")


def _assert_redacted(value: Any, arg_name: str) -> None:
    """写盘入口运行时 isinstance 断言（REQ-SU-002 AC3 四层组合之②）。

    Args:
        value: 待落盘的 dict 值。
        arg_name: 参数名（报错定位用）。

    Raises:
        TypeError: value 不是 RedactedDict——普通 dict 未经 redact() 管线
            进入写盘入口即违例，必须抛出而非静默放行。
    """
    if not isinstance(value, RedactedDict):
        raise TypeError(
            "落盘参数 {0} 必须是 RedactedDict（唯一合法构造途径 = config.redact()），"
            "收到 {1}：未脱敏数据禁止落库".format(arg_name, type(value).__name__)
        )


def _json_dumps_redacted(value: Any) -> str:
    """RedactedDict/list → 稳定 JSON 文本（键排序，渲染幂等 §7.3 前提）。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class StateStore:
    """SQLite 状态机：schema/锁/resume/幂等写入/导出（§2.3.11 全量方法）。"""

    def __init__(self, db_path: Path, system_id: str) -> None:
        """建库连接并全量建表。

        连接级配置：
          - PRAGMA journal_mode=WAL：写互斥 + BEGIN IMMEDIATE 互斥的前提；
          - PRAGMA busy_timeout=5000：双进程竞态时给对端 5s 等待窗口，
            超时后 BEGIN IMMEDIATE 立即失败（acquire_lock 按持锁报错处理）；
          - isolation_level=None（autocommit）：事务全部由本类显式 BEGIN/COMMIT
            管理，避免 sqlite3 模块隐式事务干扰 BEGIN IMMEDIATE 语义。

        Args:
            db_path: 状态库文件路径（state/understanding.sqlite）；父目录自动创建。
            system_id: 系统标识（run_meta.system_id）。
        """
        self._db_path = Path(db_path)
        self._system_id = system_id
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False：SIGINT 看门狗线程要轮询本连接（见 _gate），
        # 但并发访问由 _gate 互斥锁串行化，绝不允许两线程同时在连接上执行
        self._conn = sqlite3.connect(
            str(self._db_path), timeout=5.0, isolation_level=None,
            check_same_thread=False,
        )
        # 连接级互斥锁：libsqlite3 连接在并发 execute 下会原生崩溃
        # （SIGSEGV/SIGBUS——2026-09-28 e2e 崩溃报告实证：看门狗线程与
        # 主线程共用连接随机崩在 sqlite3Prepare/sqlite3VdbeExec）。
        # 所有连接访问（含事务序列、PRAGMA、close）必须持本锁。
        # RLock：同线程嵌套安全（acquire_lock 事务内调用 _run_meta_row 等）。
        self._gate = threading.RLock()
        self._conn.row_factory = sqlite3.Row
        with self._gate:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA foreign_keys=ON")
            # 全量建表（IF NOT EXISTS，重复初始化无害）
            self._conn.executescript(_DDL)
            # schema_meta 只保留当前版本单行（版本迁移入口）
            row = self._conn.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,)
                )
            elif row["version"] != SCHEMA_VERSION:
                raise RuntimeError(
                    "状态库 schema 版本不兼容：库内 {0}，当前代码 {1}（请归档后重跑）".format(
                        row["version"], SCHEMA_VERSION)
                )
        # 当前 run 上下文（acquire_lock 成功后填充）
        self._run_id: Optional[str] = None
        self._locked_by: Optional[str] = None

    # ------------------------------------------------------------------
    # 生命周期与锁（互斥唯一机制 = WAL + BEGIN IMMEDIATE 条件更新，无锁文件）
    # ------------------------------------------------------------------

    def acquire_lock(self, resume: bool) -> RedactedDict:
        """获取运行锁并返回 run 元信息（REQ-SU-019 / §4.3 时序）。

        流程（单 BEGIN IMMEDIATE 事务内完成，locked_by/heartbeat 原子改写）：
          1. 读最新一行 run_meta：
             - status='running' 且心跳 ≤60s → 他进程持锁且新鲜 → SuLockHeldError；
             - status='interrupted'：resume=True 复用（保留 pages/observations，
               'exploring' 态重置 'pending' 重跑）；resume=False 归档后新建；
             - status='completed'/'failed'：总是新建 run（语义=重跑）；
             - 陈旧 running（心跳 >60s）：条件 UPDATE 天然满足即接管。
          2. 新建：INSERT 一行 status='running', locked_by='pid:<pid>',
             heartbeat_ts=now，config_snapshot 待编排层 set_config_snapshot 回填。
          3. 接管/复用：条件 UPDATE locked_by/heartbeat/status（心跳新鲜即 0 行=拒绝）。

        Args:
            resume: True=--resume（interrupted 复用）；False=--fresh（先归档）。

        Returns:
            RedactedDict: run_meta 行（config_snapshot 已 JSON 解析；
            本身即含 SensitiveStr 脱敏后内容，无明文凭据）。

        Raises:
            SuLockHeldError: 存在心跳新鲜的 running 行被其他进程持有。
        """
        pid_tag = "pid:{0}".format(os.getpid())
        now = time.time()
        stale_boundary = now - HEARTBEAT_STALE_SECONDS

        # 整个"BEGIN IMMEDIATE→…→COMMIT"事务序列原子持锁：期间任何
        # 看门狗线程轮询都必须等待，杜绝连接级并发
        with self._gate:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                latest = self._conn.execute(
                    "SELECT * FROM run_meta ORDER BY started_at DESC LIMIT 1"
                ).fetchone()

                if latest is not None:
                    status = latest["status"]
                    hb = latest["heartbeat_ts"]
                    fresh_running = (
                        status == "running"
                        and hb is not None and hb >= stale_boundary
                        and latest["locked_by"] != pid_tag
                    )
                    if fresh_running:
                        # 他进程持锁且心跳新鲜 → 双进程互斥：第二个进程明确报错
                        self._conn.execute("ROLLBACK")
                        raise SuLockHeldError(
                            "状态库已被其他进程持有（locked_by={0}，心跳 {1:.0f}s 前，"
                            "阈值 {2}s）：同一状态库同一时刻只允许一个 SU 进程".format(
                                latest["locked_by"], now - hb, HEARTBEAT_STALE_SECONDS),
                            hints=[
                                "如确认对端进程已死，请等待心跳超过 {0}s 后重试（陈旧锁自动接管）".format(
                                    HEARTBEAT_STALE_SECONDS),
                                "或改用 --fresh 归档旧状态后全新运行",
                            ],
                        )
                    if status == "interrupted" and resume:
                        # 复用：改写锁 + 心跳，'exploring' 中断态页面重置 pending 重跑
                        self._conn.execute(
                            "UPDATE run_meta SET locked_by=?, heartbeat_ts=?, status='running',"
                            " exit_reason=NULL WHERE run_id=?",
                            (pid_tag, now, latest["run_id"]),
                        )
                        self._conn.execute(
                            "UPDATE pages SET status='pending', updated_at=? WHERE status='exploring'",
                            (now,),
                        )
                        self._run_id = latest["run_id"]
                        self._locked_by = pid_tag
                        self._conn.execute("COMMIT")
                        return self._run_meta_row(self._run_id)
                    if status == "running" and hb is not None and hb < stale_boundary:
                        # 陈旧锁接管：locked_by/heartbeat/status 同句原子改写
                        self._conn.execute(
                            "UPDATE run_meta SET locked_by=?, heartbeat_ts=?, status='running'"
                            " WHERE run_id=?",
                            (pid_tag, now, latest["run_id"]),
                        )
                        # 陈旧 running 可能残留 exploring 页，同样重置
                        self._conn.execute(
                            "UPDATE pages SET status='pending', updated_at=? WHERE status='exploring'",
                            (now,),
                        )
                        self._run_id = latest["run_id"]
                        self._locked_by = pid_tag
                        self._conn.execute("COMMIT")
                        return self._run_meta_row(self._run_id)
                    # status='running'（本进程重入/心跳为空）、completed、
                    # failed：新建 run
                    if status == "interrupted" and not resume:
                        # --fresh 归档（REQ-SU-019 AC2 / 架构 §4.3 时序
                        # Note；2026-09-29 e2e 场景[4]根因修复）：interrupted
                        # 库在非续跑路径下必须先 rename 为
                        # state.archive.<ts>/ 再新建空库运行。此前该路径
                        # 直接新建 run 并把 interrupted 进度遗留在同一库
                        # （页数跨 run 污染、归档目录缺失）——归档语义只在
                        # CLI 层实现时又因"interrupted 已被 resume 消费"
                        # 的时序问题实际不可达。收敛到本分支后：interrupted
                        # 事实的两种归宿（resume 复用 / fresh 归档）在此
                        # 唯一判定点完成。归档 commit 先行：rename 不能
                        # 发生在持写锁事务内（旧 WAL 句柄随 rename 走）。
                        self._conn.execute("COMMIT")
                        self.archive_and_reset(self._db_path.parent.parent)
                        # 本实例连接仍指向已 rename 的旧 inode：关闭并按
                        # 原路径重建（state/ 已被 rename 走，构造时 mkdir
                        # 重建空 state/ 并全量建表——__init__ 语义）
                        self._conn.close()
                        self._db_path.parent.mkdir(parents=True, exist_ok=True)
                        self._conn = sqlite3.connect(
                            str(self._db_path), timeout=5.0, isolation_level=None,
                            check_same_thread=False,
                        )
                        self._conn.row_factory = sqlite3.Row
                        self._conn.execute("PRAGMA journal_mode=WAL")
                        self._conn.execute("PRAGMA busy_timeout=5000")
                        self._conn.execute("PRAGMA foreign_keys=ON")
                        self._conn.executescript(_DDL)
                        self._conn.execute(
                            "INSERT INTO schema_meta(version) VALUES (?)",
                            (SCHEMA_VERSION,),
                        )
                        # 事务重开：函数契约是"BEGIN IMMEDIATE→…→COMMIT"
                        # 成对收口，归档路径提前 COMMIT 后必须重新开启，
                        # 否则下方新建 run 的 INSERT 裸奔 autocommit、
                        # 结尾 COMMIT 报"no transaction is active"
                        # （2026-09-29 e2e 场景[4]修复引入的次生缺陷）
                        self._conn.execute("BEGIN IMMEDIATE")
                # 新建 run
                run_id = str(uuid.uuid4())
                self._conn.execute(
                    "INSERT INTO run_meta(run_id, system_id, status, locked_by, heartbeat_ts,"
                    " started_at, exit_reason, config_snapshot)"
                    " VALUES (?,?,'running',?,?,?,NULL,'{}')",
                    (run_id, self._system_id, pid_tag, now, now),
                )
                self._run_id = run_id
                self._locked_by = pid_tag
                self._conn.execute("COMMIT")
                return self._run_meta_row(run_id)
            except SuLockHeldError:
                raise
            except sqlite3.OperationalError as exc:
                # BEGIN IMMEDIATE 获取写锁超时/失败：视同他进程持锁
                self._safe_rollback()
                raise SuLockHeldError(
                    "获取状态库写锁失败（BEGIN IMMEDIATE 冲突：{0}）：存在并发 SU 进程".format(exc),
                    hints=["等待对端进程结束后重试，或使用 --fresh 归档重跑"],
                )
            except Exception:
                self._safe_rollback()
                raise

    def _safe_rollback(self) -> None:
        """尽力回滚当前事务（无活动事务时静默，供异常路径统一调用）。"""
        with self._gate:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass

    def _require_run(self) -> None:
        """校验当前连接已持锁（run_id 已设置），否则报使用顺序错误。"""
        if self._run_id is None:
            raise RuntimeError("请先调用 acquire_lock() 再执行本操作（当前连接未持锁）")

    def _run_meta_row(self, run_id: str) -> RedactedDict:
        """按 run_id 读取 run_meta 行并转 RedactedDict（config_snapshot 解析为内嵌 dict）。

        config_snapshot 在落库前已由编排层过 redact()（REQ-SU-002），读出时
        重新包 RedactedDict 以保持"已脱敏"标记语义连续。
        """
        row = self._conn.execute(
            "SELECT * FROM run_meta WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise RuntimeError("run_meta 行不存在：run_id={0}".format(run_id))
        data = dict(row)
        # config_snapshot JSON 文本 → dict（再标 RedactedDict：库内数据必已脱敏）
        try:
            data["config_snapshot"] = json.loads(data.get("config_snapshot") or "{}")
        except json.JSONDecodeError:
            data["config_snapshot"] = {}
        return RedactedDict(data)

    def set_config_snapshot(self, snapshot: RedactedDict) -> None:
        """回填当前 run 的 config_snapshot（编排层在 acquire_lock 后调用一次）。

        Args:
            snapshot: **必须为 config.redact() 产出的 RedactedDict**——
                运行参数快照绝无明文凭据（REQ-SU-002，入口断言）。
        """
        self._require_run()
        _assert_redacted(snapshot, "snapshot")
        with self._gate:
            self._conn.execute(
                "UPDATE run_meta SET config_snapshot=? WHERE run_id=?",
                (_json_dumps_redacted(snapshot), self._run_id),
            )

    def heartbeat(self) -> None:
        """刷新心跳（crawler 每完成一个页面 / 每页边界 drain 时调用）。"""
        self._require_run()
        with self._gate:
            self._conn.execute(
                "UPDATE run_meta SET heartbeat_ts=? WHERE run_id=?",
                (time.time(), self._run_id),
            )

    def mark(self, status: str, exit_reason: Optional[str] = None) -> None:
        """流转 run 状态机（running/interrupted/completed/failed）。

        Args:
            status: 目标状态（run_meta CHECK 约束强制枚举）。
            exit_reason: 退出原因（'budget_exhausted:pages'/'sigint'/'login_failed'…）。
        """
        self._require_run()
        if status not in ("running", "interrupted", "completed", "failed"):
            raise ValueError("run_meta.status 非法值：{0}".format(status))
        finished = time.time() if status in ("completed", "failed", "interrupted") else None
        with self._gate:
            self._conn.execute(
                "UPDATE run_meta SET status=?, exit_reason=?, finished_at=? WHERE run_id=?",
                (status, exit_reason, finished, self._run_id),
            )

    def release_lock(self) -> None:
        """释放 run_meta 锁（locked_by 置空；SIGINT/completed 路径调用）。"""
        self._require_run()
        with self._gate:
            self._conn.execute(
                "UPDATE run_meta SET locked_by=NULL WHERE run_id=?", (self._run_id,)
            )

    def run_status_of_current(self) -> Optional[str]:
        """读本进程当前 run 的 status（SIGINT 看门狗轮询收口进度专用）。

        只读查询：未持锁（run_id 未设置）返回 None，供调用方保守判定；
        WAL 下读连接能立即看到其它连接的已提交写（看门狗线程轮询依赖）。

        Returns:
            Optional[str]: 'running'/'interrupted'/'completed'/'failed'；
                无当前 run 时为 None。
        """
        if self._run_id is None:
            return None
        with self._gate:
            row = self._conn.execute(
                "SELECT status FROM run_meta WHERE run_id=?", (self._run_id,)
            ).fetchone()
        return None if row is None else str(row["status"])

    def heartbeat_age_of_current(self) -> Optional[float]:
        """本进程当前 run 距上次心跳的秒数（SIGINT 看门狗停滞检测专用）。

        主线程挂在 CDP 阻塞调用期间 heartbeat() 停更；看门狗线程独立轮询
        本方法即可在无信号通道的情况下推断挂起（ crawler 每页边界刷新心跳，
        停滞超阈值 = 主线程不在页边界推进 = 大概率挂在 CDP recv）。

        Returns:
            Optional[float]: 心跳年龄（秒）；未持锁 / 心跳为空时 None。
        """
        if self._run_id is None:
            return None
        with self._gate:
            row = self._conn.execute(
                "SELECT heartbeat_ts FROM run_meta WHERE run_id=?", (self._run_id,)
            ).fetchone()
        if row is None or row["heartbeat_ts"] is None:
            return None
        return time.time() - float(row["heartbeat_ts"])

    def read_latest_run(self) -> Optional[RedactedDict]:
        """只读返回最新一行 run_meta（不取锁、不新建——前置校验专用）。

        acquire_lock 对 completed/failed 历史 run 会**新建** running 行，
        因此"校验历史 run 状态"绝不能建立在 acquire 返回值上（校验语义
        必须与写副作用解耦——2026-09-28 e2e 场景[5]教训的根因收口）。

        Returns:
            Optional[RedactedDict]: 最新 run 行（config_snapshot 已解析）；
                空库返回 None。
        """
        with self._gate:
            row = self._conn.execute(
                "SELECT run_id FROM run_meta ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            return self._run_meta_row(row["run_id"])

    def flush(self) -> None:
        """SIGINT 路径收口：WAL checkpoint 落盘（状态先于产物，AP-5）。"""
        with self._gate:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                # checkpoint 失败（他进程持读锁）不影响数据已提交的事实
                pass

    def archive_and_reset(self, out_dir: Path) -> None:
        """--fresh：state/ → state.archive.<ts>/ 后重建（REQ-SU-019 AC2）。

        归档采用 rename（原子移动）；目标名冲突时追加毫秒后缀。调用方在
        归档后需新建 StateStore 实例（本实例连接仍指向旧 inode，安全废弃）。

        Args:
            out_dir: 输出根目录（state/ 所在目录，即 <out>/<system_id>/）。
        """
        out_dir = Path(out_dir)
        state_dir = out_dir / "state"
        if not state_dir.exists():
            return
        # 先释放自身句柄，避免 macOS 下 rename 后旧连接写入已移动文件
        with self._gate:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
        ts = int(time.time())
        archive_dir = out_dir / "state.archive.{0}".format(ts)
        suffix = 0
        while archive_dir.exists():
            suffix += 1
            archive_dir = out_dir / "state.archive.{0}_{1}".format(ts, suffix)
        state_dir.rename(archive_dir)

    def close(self) -> None:
        """关闭连接（进程退出/渲染完成后调用）。"""
        with self._gate:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    # ------------------------------------------------------------------
    # 幂等写入（全部 INSERT OR IGNORE / UPSERT，采集先查后采）
    # ------------------------------------------------------------------

    def upsert_page(self, node: PageNodeRedacted) -> int:
        """幂等写入页面节点（pages.url_key UNIQUE）。

        语义：首次发现 → INSERT（status 取 node.status）；已存在 → 仅刷新
        title/snapshot_path/tech_fingerprint/updated_at（不覆盖 done 等进度状态，
        保证 resume 不重复探索，REQ-SU-019.2）。

        Args:
            node: 页面节点 DTO（url 已 strip_url_credentials）。

        Returns:
            int: page_id（已存在返回既有 id，新插入返回自增 id）。
        """
        now = time.time()
        with self._gate:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO pages(url_key, url, title, depth, discover_from,"
                " status, snapshot_path, tech_fingerprint, error, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (node.url_key, node.url, node.title, node.depth, node.discover_from,
                 node.status, node.snapshot_path, node.tech_fingerprint, node.error,
                 now, now),
            )
            if cur.rowcount == 0:
                # 已存在：只补刷新非进度字段（title 快照可能首采时为空）
                self._conn.execute(
                    "UPDATE pages SET title=COALESCE(?, title),"
                    " snapshot_path=COALESCE(?, snapshot_path),"
                    " tech_fingerprint=COALESCE(?, tech_fingerprint), updated_at=?"
                    " WHERE url_key=?",
                    (node.title, node.snapshot_path, node.tech_fingerprint, now, node.url_key),
                )
            row = self._conn.execute(
                "SELECT page_id FROM pages WHERE url_key=?", (node.url_key,)
            ).fetchone()
        return int(row["page_id"])

    def set_page_status(
        self,
        page_id: int,
        status: str,
        error: Optional[str] = None,
        snapshot_path: Optional[str] = None,
    ) -> None:
        """更新页面状态机（pending/exploring/done/timeout/error）。

        Args:
            page_id: 目标页面。
            status: 新状态（DDL CHECK 约束强制枚举）。
            error: 超时/错误说明（含 wait_ready_timeout，进第 10 节 d 项）。
            snapshot_path: 语义骨架快照路径（done 时回填）。
        """
        if status not in ("pending", "exploring", "done", "timeout", "error"):
            raise ValueError("pages.status 非法值：{0}".format(status))
        with self._gate:
            self._conn.execute(
                "UPDATE pages SET status=?, error=COALESCE(?, error),"
                " snapshot_path=COALESCE(?, snapshot_path), updated_at=?"
                " WHERE page_id=?",
                (status, error, snapshot_path, time.time(), page_id),
            )

    def page_done(self, url_key: str) -> bool:
        """判定 url_key 对应页面是否已完成（crawler 采集前查询，幂等/resume）。"""
        with self._gate:
            row = self._conn.execute(
                "SELECT status FROM pages WHERE url_key=?", (url_key,)
            ).fetchone()
        return row is not None and row["status"] == "done"

    def insert_edge(self, e: EdgeRedacted) -> None:
        """幂等写入导航边（UNIQUE(from_key,to_key,via_action)）。"""
        with self._gate:
            self._conn.execute(
                "INSERT OR IGNORE INTO edges(from_key, to_key, via_action) VALUES (?,?,?)",
                (e.from_key, e.to_key, e.via_action),
            )

    def insert_action(self, a: ActionDecisionRedacted) -> None:
        """幂等写入候选动作（UNIQUE(page_id, element_sig)，同页重复发现不重复入库）。

        T3 恒 executed=0（红线⑤落库约束；调用方传入非 0 的 T3 视为编程错误）。
        """
        if a.tier == "T3" and a.executed:
            raise ValueError("红线⑤违例：T3 动作禁止标记 executed（红线即约束，§5.1）")
        if a.tier not in ("T1", "T2", "T3"):
            raise ValueError("page_actions.tier 非法值：{0}".format(a.tier))
        with self._gate:
            self._conn.execute(
                "INSERT OR IGNORE INTO page_actions(page_id, element_sig, tier, rule_name,"
                " executed) VALUES (?,?,?,?,?)",
                (a.page_id, a.element_sig, a.tier, a.rule_name, a.executed),
            )

    def mark_action_executed(self, page_id: int, element_sig: str) -> int:
        """回写候选动作为已执行（T2 真实提交后调用；红线⑤口径不变）。

        element_sig 与 :meth:`insert_action` 落库形态完全一致——
        ``json.dumps(dict(redact(_signature_public_dict(el))), sort_keys=True)``，
        调用方（crawler._execute_t2_forms）复用同一条序列化管线。

        执行事实优先（2026-09-28 e2e 场景[2]根因修复）：调用语义是
        **"该动作已真实执行"**（executed 列口径 = "本 run 内实际提交过"），
        目标行因分级时序缺失（如页轮次中断未及分级落库）时**补录**
        T2/executed=1 行——执行过的事实必须落库，静默 0 行会制造
        "已执行却查无记录"的观测断链。目标行已存在且 tier='T3' 仍拒绝
        （红线⑤：T3 动作永不标记 executed——T2 执行通道的入口预判已排除
        T3，命中即口径漂移，必须显式报错）。rule_name 缺省占位仅用于补录
        路径（真实执行是事实、rule_name 属分级器解释面，crawler 分级轮次
        会经 insert_action 的 INSERT OR IGNORE + 后续审计补全）。

        Args:
            page_id: 动作所属页面。
            element_sig: 与 insert_action 同口径的签名文本。

        Returns:
            int: 本次写入实际影响的行数（UPDATE 命中数或补录 =1；
                调用方据此打诊断日志，0 理论上不再出现）。

        Raises:
            ValueError: 目标行 tier='T3'（红线⑤：T3 动作永不标记 executed）。
        """
        with self._gate:
            row = self._conn.execute(
                "SELECT tier FROM page_actions WHERE page_id=? AND element_sig=?",
                (page_id, element_sig),
            ).fetchone()
            if row is not None and row["tier"] == "T3":
                raise ValueError("红线⑤违例：T3 动作禁止标记 executed（红线即约束，§5.1）")
            if row is None:
                # 执行事实补录（见 docstring"执行事实优先"）：分级行缺失
                # 不改变"已真实执行"的事实，executed=1 必须可观测
                self._conn.execute(
                    "INSERT OR IGNORE INTO page_actions(page_id, element_sig,"
                    " tier, rule_name, executed) VALUES (?,?,'T2',?,1)",
                    (page_id, element_sig, MARK_EXECUTED_BACKFILL_RULE),
                )
                cur = self._conn.execute(
                    "UPDATE page_actions SET executed=1"
                    " WHERE page_id=? AND element_sig=? AND tier='T2'",
                    (page_id, element_sig),
                )
                return cur.rowcount
            cur = self._conn.execute(
                "UPDATE page_actions SET executed=1"
                " WHERE page_id=? AND element_sig=? AND tier='T2'",
                (page_id, element_sig),
            )
            return cur.rowcount

    def insert_blocked_event(self, b: BlockedEventRedacted) -> None:
        """幂等写入 route 拦截事件（blocked_events 无 UNIQUE，按内容去重兜底）。"""
        if b.kind not in ("aborted_method", "blocked_origin", "download", "new_window"):
            raise ValueError("blocked_events.kind 非法值：{0}".format(b.kind))
        with self._gate:
            self._conn.execute(
                "INSERT OR IGNORE INTO blocked_events(kind, url, method, post_data,"
                " page_id, ts) VALUES (?,?,?,?,?,?)",
                (b.kind, b.url, b.method, b.post_data, b.page_id, b.ts),
            )

    def upsert_api_endpoint(self, o: ApiObservationRedacted) -> None:
        """聚合写入 API 端点（UNIQUE(url_path,method)，同端点多观测合并样本 ≤5）。

        样本策略（REQ-SU-009.3）：样本数组 ≤5，**超出丢弃最旧**（FIFO）；
        latest_status/request_shape/response_shape 以最新观测覆盖；
        observed_on_pages 页 id 集合追加（保序去重，首次观测页为首元素）。

        Args:
            o: 观测 DTO；request_shape/response_shape 若非 None 必须是
                RedactedDict（入口运行时断言，四层组合之②）。
        """
        if o.request_shape is not None:
            _assert_redacted(o.request_shape, "o.request_shape")
        if o.response_shape is not None:
            _assert_redacted(o.response_shape, "o.response_shape")
        with self._gate:
            self._upsert_api_endpoint_locked(o)

    def _upsert_api_endpoint_locked(self, o: ApiObservationRedacted) -> None:
        """upsert_api_endpoint 的加锁内层（调用方持 _gate，读写同事务窗口）。"""
        row = self._conn.execute(
            "SELECT endpoint_id, sample_count, samples, observed_on_pages"
            " FROM api_observations WHERE url_path=? AND method=?",
            (o.url_path, o.method),
        ).fetchone()
        sample = RedactedDict({
            "ts": o.ts,
            "status": o.status,
            "request_shape": dict(o.request_shape) if o.request_shape is not None else None,
            "response_shape": dict(o.response_shape) if o.response_shape is not None else None,
            "page_id": o.observed_on_page,
        })
        if row is None:
            self._conn.execute(
                "INSERT INTO api_observations(url_path, method, latest_status,"
                " request_shape, response_shape, sample_count, samples, observed_on_pages)"
                " VALUES (?,?,?,?,?,1,?,?)",
                (o.url_path, o.method, o.status,
                 _json_dumps_redacted(o.request_shape) if o.request_shape is not None else None,
                 _json_dumps_redacted(o.response_shape) if o.response_shape is not None else None,
                 _json_dumps_redacted([sample]),
                 str(o.observed_on_page)),
            )
            return
        # 已有端点：样本 FIFO 截断 ≤5（丢最旧），页集合保序去重追加
        try:
            samples: List[Any] = json.loads(row["samples"] or "[]")
        except json.JSONDecodeError:
            samples = []
        samples.append(dict(sample))
        samples = samples[-5:]
        pages_seen = [p for p in (row["observed_on_pages"] or "").split(",") if p]
        if str(o.observed_on_page) not in pages_seen:
            pages_seen.append(str(o.observed_on_page))
        self._conn.execute(
            "UPDATE api_observations SET latest_status=?, request_shape=?, response_shape=?,"
            " sample_count=?, samples=?, observed_on_pages=? WHERE endpoint_id=?",
            (o.status,
             _json_dumps_redacted(o.request_shape) if o.request_shape is not None else None,
             _json_dumps_redacted(o.response_shape) if o.response_shape is not None else None,
             len(samples), _json_dumps_redacted(samples),
             ",".join(pages_seen), row["endpoint_id"]),
        )

    def upsert_db_table(self, t: DbTableRedacted) -> None:
        """幂等写入表清单 + 列平表（重跑内省不重复，REQ-SU-011）。

        columns 每项必须为 RedactedDict（列名/类型/注释已过脱敏，入口断言）。
        """
        for idx, col in enumerate(t.columns):
            _assert_redacted(col, "t.columns[{0}]".format(idx))
        with self._gate:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO db_tables(schema_name, table_name, kind,"
                " row_estimate, comment, columns_json) VALUES (?,?,?,?,?,?)",
                (t.schema_name, t.table_name, t.kind, t.row_estimate, t.comment,
                 _json_dumps_redacted(list(t.columns))),
            )
            row = self._conn.execute(
                "SELECT table_id FROM db_tables WHERE schema_name=? AND table_name=?",
                (t.schema_name, t.table_name),
            ).fetchone()
            table_id = int(row["table_id"])
            if cur.rowcount == 0:
                # 已存在：刷新列明细/行数估算/注释（内省结果可能更完整）
                self._conn.execute(
                    "UPDATE db_tables SET columns_json=?, row_estimate=?, comment=? WHERE table_id=?",
                    (_json_dumps_redacted(list(t.columns)), t.row_estimate, t.comment, table_id),
                )
            # 列级平表（关联分析/隐式 FK 匹配索引源）
            for col in t.columns:
                self._conn.execute(
                    "INSERT OR IGNORE INTO db_columns(table_id, name, data_type, type_family,"
                    " is_pk, fk_target, comment) VALUES (?,?,?,?,?,?,?)",
                    (table_id, col.get("name"), col.get("data_type") or "unknown",
                     col.get("type_family") or "other",
                     1 if col.get("is_pk") else 0, col.get("fk_target"), col.get("comment")),
                )

    def insert_samples(self, table: str, rows: List[RedactedDict]) -> None:
        """幂等写入表采样行（DataMasker 逐列处理后的 RedactedDict 行）。

        Args:
            table: 'schema.table' 形式的目标表名。
            rows: 采样行（每行必须 RedactedDict，未脱敏即抛 TypeError）。
        """
        schema_name, _, table_name = table.partition(".")
        for row_no, data in enumerate(rows):
            _assert_redacted(data, "rows[{0}]".format(row_no))
        with self._gate:
            if not table_name:
                # 未限定 schema 时按默认（MySQL=库名，PG=public）查找第一个匹配
                row = self._conn.execute(
                    "SELECT table_id FROM db_tables WHERE table_name=? ORDER BY table_id LIMIT 1",
                    (schema_name,),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT table_id FROM db_tables WHERE schema_name=? AND table_name=?",
                    (schema_name, table_name),
                ).fetchone()
            if row is None:
                raise ValueError(
                    "db_tables 中不存在目标表：{0}（请先 upsert_db_table）".format(table))
            table_id = int(row["table_id"])
            # 幂等：先清该表旧采样再写新采样（采样是一次性动作，重采以最新为准）
            self._conn.execute("DELETE FROM db_samples WHERE table_id=?", (table_id,))
            for row_no, data in enumerate(rows):
                self._conn.execute(
                    "INSERT OR IGNORE INTO db_samples(table_id, row_json, row_no) VALUES (?,?,?)",
                    (table_id, _json_dumps_redacted(data), row_no),
                )

    def insert_implicit_fk(self, c: ImplicitFkCandidateRedacted) -> None:
        """幂等写入隐式 FK 候选（四元组 UNIQUE，REQ-SU-013）。"""
        with self._gate:
            self._conn.execute(
                "INSERT OR IGNORE INTO implicit_fk_candidates(child_table, child_column,"
                " parent_table, parent_column, prescreen_score, stopped_at_rule,"
                " containment, evidence_json) VALUES (?,?,?,?,?,?,?,?)",
                (c.child_table, c.child_column, c.parent_table, c.parent_column,
                 c.prescreen_score, c.stopped_at_rule, c.containment, c.evidence_json),
            )

    def upsert_redis_key(self, k: RedisKeyRecordRedacted) -> None:
        """幂等写入 Redis 键记录（key_name UNIQUE，重跑 SCAN 更新最新观测）。"""
        with self._gate:
            self._conn.execute(
                "INSERT INTO redis_keys(key_name, key_type, ttl_ms, encoding, mem_bytes,"
                " value_sample) VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(key_name) DO UPDATE SET key_type=excluded.key_type,"
                " ttl_ms=excluded.ttl_ms, encoding=excluded.encoding,"
                " mem_bytes=excluded.mem_bytes, value_sample=excluded.value_sample",
                (k.key_name, k.key_type, k.ttl_ms, k.encoding, k.mem_bytes, k.value_sample),
            )

    def replace_redis_patterns(self, patterns: List[RedactedDict]) -> None:
        """以"快照"语义整体替换 redis_patterns 表（pattern UNIQUE，聚类是当轮观测汇总）。

        redis_inspector.collect() 的唯一模式写入口：先清全表再逐行写入本轮
        聚类结果（跨轮合并无意义——聚类分母是本轮 SCAN 键集）。
        每行必须为 RedactedDict（pattern 列非敏感值；ttl/type 摘要来自计数），
        入口断言兜底（红线①统一口径）。

        Args:
            patterns: RedisPattern.to_dict() 产物列表；键集合对齐 DDL 列
                （pattern/key_count/no_ttl_ratio/ttl_summary/type_summary/sample_keys）。

        Raises:
            TypeError: 任一行不是 RedactedDict（未脱敏即拒写）。
        """
        for idx, row in enumerate(patterns):
            _assert_redacted(row, "patterns[{0}]".format(idx))
        with self._gate:
            self._conn.execute("DELETE FROM redis_patterns")
            for row in patterns:
                self._conn.execute(
                    "INSERT OR REPLACE INTO redis_patterns(pattern, key_count,"
                    " no_ttl_ratio, ttl_summary, type_summary, sample_keys)"
                    " VALUES (?,?,?,?,?,?)",
                    (row.get("pattern"), row.get("key_count"),
                     row.get("no_ttl_ratio"),
                     None if row.get("ttl_summary") is None
                     else _json_dumps_redacted(row.get("ttl_summary")),
                     None if row.get("type_summary") is None
                     else _json_dumps_redacted(row.get("type_summary")),
                     None if row.get("sample_keys") is None
                     else _json_dumps_redacted(row.get("sample_keys"))),
                )

    def insert_relation(self, r: RelationRedacted) -> None:
        """幂等写入关联证据（UNIQUE(rtype,left_ref,right_ref)；score 取最新最大值）。"""
        if r.evidence is not None:
            _assert_redacted(r.evidence, "r.evidence")
        with self._gate:
            self._conn.execute(
                "INSERT INTO relations(rtype, left_ref, right_ref, score, evidence_json)"
                " VALUES (?,?,?,?,?)"
                " ON CONFLICT(rtype, left_ref, right_ref) DO UPDATE SET"
                " score=MAX(score, excluded.score), evidence_json=excluded.evidence_json",
                (r.rtype, r.left_ref, r.right_ref, r.score,
                 _json_dumps_redacted(dict(r.evidence) if r.evidence is not None else {})),
            )

    # ------------------------------------------------------------------
    # findings 校验与入库（LLM 回填唯一入库原语，§6.2）
    # ------------------------------------------------------------------

    def validate_findings_schema(self, findings: List[Dict[str, Any]]) -> List[str]:
        """校验宿主 LLM 回填的 findings 列表（§6.2 校验规则，整批拒绝口径）。

        校验项（全部违反项一次性收集返回，供 CLI 编排层中文报错列出违约项）：
          1. confidence 缺失或非枚举（high/medium/low）→ 拒绝；
          2. evidence_refs 空数组/非列表/引用不存在的记录 id → 拒绝
             （存在性校验按 '<table>:<id>' 查对应表，_EVIDENCE_TABLES 映射）；
          3. claim 含键值对形态凭据（仅
             (?i)(password|token|api[_-]?key)\\s*[:=]\\s*\\S+ 收窄正则）→ 拒绝
             （不静默替换，语义须由 LLM 修正）；
          4. kind 缺失或非枚举（mapping/business_rule/redis_entity/semantic_name）→ 拒绝；
          5. claim 缺失/非字符串/空白 → 拒绝。

        Args:
            findings: findings JSON 数组（每项 dict）。

        Returns:
            list[str]: 中文违约描述列表（空列表=全部通过）。
        """
        errors: List[str] = []
        for idx, item in enumerate(findings):
            if not isinstance(item, dict):
                errors.append("findings[{0}]：必须是对象（收到 {1}）".format(
                    idx, type(item).__name__))
                continue
            # 规则 5：claim 必填非空白
            claim = item.get("claim")
            if not isinstance(claim, str) or not claim.strip():
                errors.append("findings[{0}]：claim 缺失或为空白".format(idx))
            else:
                # 规则 3：claim 键值对形态凭据（收窄正则，不做全文 PII）
                m = _CLAIM_CREDENTIAL_RE.search(claim)
                if m:
                    errors.append(
                        "findings[{0}]：claim 含键值对形态凭据（'{1}…'），"
                        "请删除明文凭据后重新回填".format(idx, m.group(0)[:24]))
            # 规则 1：confidence 必填枚举
            confidence = item.get("confidence")
            if confidence not in _FINDING_CONFIDENCES:
                errors.append(
                    "findings[{0}]：confidence 缺失或非法（合法值 high/medium/low，"
                    "收到 {1!r}）".format(idx, confidence))
            # 规则 4：kind 必填枚举
            kind = item.get("kind")
            if kind not in _FINDING_KINDS:
                errors.append(
                    "findings[{0}]：kind 缺失或非法（合法值 {1}，收到 {2!r}）".format(
                        idx, "/".join(_FINDING_KINDS), kind))
            # 规则 2：evidence_refs ≥1 且 id 存在
            refs = item.get("evidence_refs")
            if not isinstance(refs, list) or len(refs) == 0:
                errors.append("findings[{0}]：evidence_refs 必须为 ≥1 条的数组".format(idx))
            else:
                for ref in refs:
                    problem = self._check_evidence_ref(ref)
                    if problem is not None:
                        errors.append("findings[{0}]：evidence_ref '{1}' 非法（{2}）".format(
                            idx, ref, problem))
        return errors

    def _check_evidence_ref(self, ref: Any) -> Optional[str]:
        """校验单条 evidence_ref：'<table>:<id>' 且 id 存在于对应表。

        Returns:
            str | None: 违约原因（None=合法）。
        """
        if not isinstance(ref, str):
            return "必须是字符串 '<table>:<id>'"
        table_name, sep, id_text = ref.rpartition(":")
        if not sep or not table_name or not id_text:
            return "形态必须是 '<table>:<id>'"
        pk_column = _EVIDENCE_TABLES.get(table_name)
        if pk_column is None:
            return "未知引用表 '{0}'（合法表：{1}）".format(
                table_name, "/".join(sorted(_EVIDENCE_TABLES)))
        if not id_text.isdigit():
            return "id 必须是数字"
        # 按主键查存在性（evidence_refs 存在性校验专用通道）
        with self._gate:
            row = self._conn.execute(
                "SELECT 1 FROM {0} WHERE {1}=? LIMIT 1".format(table_name, pk_column),
                (int(id_text),),
            ).fetchone()
        if row is None:
            return "引用 {0} 在库中不存在".format(ref)
        return None

    def replace_findings(self, findings: List[Dict[str, Any]]) -> None:
        """LLM 回填唯一入库原语：全量替换 findings 表（§1.2 边界规则 2）。

        **唯一调用方 = CLI 编排层 `_phase_llm_bridge()`**（§6.3 两阶段工作流
        收口）——本方法以调用栈断言强制单一入口：直接调用方模块名必须是
        'system_understanding'（§2.3.1 契约"replace_findings 的唯一调用方"）。

        流程：validate_findings_schema() 有任一违约 → 抛 SuConfigError
        整批拒绝（中文报错列出全部违约项，REQ-SU-016 AC2）；
        全部合法 → DELETE 全表后逐条 INSERT（status='proposed'，created_at=now）。

        Args:
            findings: findings JSON 数组。

        Raises:
            SuConfigError: schema 校验失败（整批拒绝，exit=2）。
            PermissionError: 调用方不是 CLI 编排层（单一入口契约违例）。
        """
        # 单一入口断言：直接调用帧的模块名必须是 CLI 编排层。
        # 接受两种等价形态：'system_understanding'（作为模块被 import）与
        # '__main__'（直接执行 scripts/system_understanding.py——CLI 的常规
        # 运行形态，§8.2 退出码收口即以此形态发生）。两种形态下编排层源码
        # 文件必须真是 system_understanding.py，否则仍属契约违例。
        import inspect
        import os as _os
        stack = inspect.stack()
        caller_module = ""
        caller_file = ""
        if len(stack) > 1:
            caller_module = str(stack[1].frame.f_globals.get("__name__", ""))
            caller_file = str(stack[1].frame.f_globals.get("__file__", ""))
        caller_ok = caller_module == "system_understanding" or (
            caller_module == "__main__"
            and _os.path.basename(caller_file) == "system_understanding.py"
        )
        if not caller_ok:
            raise PermissionError(
                "replace_findings() 唯一合法调用方为 CLI 编排层 system_understanding"
                "（_phase_llm_bridge，§1.2 边界规则 2），实际调用方：'{0}'".format(
                    caller_module or "<unknown>")
            )
        errors = self.validate_findings_schema(findings)
        if errors:
            raise SuConfigError(
                "findings 校验未通过（共 {0} 项违约，整批拒绝）：{1}".format(
                    len(errors), "；".join(errors)),
                hints=[
                    "修正 understanding.json 的 findings 段后重跑 --render-only",
                    "回填契约见 docs/spec/role-prompts/su-llm-backfill.md",
                ],
            )
        now = time.time()
        # 与 acquire_lock 同口径：整个 DELETE+INSERT 事务序列原子持锁
        with self._gate:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute("DELETE FROM findings")
                for item in findings:
                    self._conn.execute(
                        "INSERT INTO findings(claim, confidence, evidence_refs, status, kind,"
                        " created_at) VALUES (?,?,?,'proposed',?,?)",
                        (item["claim"], item["confidence"],
                         _json_dumps_redacted(item["evidence_refs"]),
                         item["kind"], now),
                    )
                self._conn.execute("COMMIT")
            except Exception:
                self._safe_rollback()
                raise

    # ------------------------------------------------------------------
    # 读取（供渲染，§6.1 / §7.3：全部 ORDER BY 主键、字典 sorted → 幂等）
    # ------------------------------------------------------------------

    def export_understanding(self) -> RedactedDict:
        """导出 understanding.json 数据源（§6.1 schema 结构，已全脱敏）。

        组合：run_meta（status/budget 占位由编排层注入）、pages+page_actions、
        api_observations、db_tables(+columns/samples/implicit_fk)、redis_patterns、
        relations。透镜缺失的 status/skip_reason 字段由编排层按 preflight 结果
        后处理填入（脚本层各采集单元只写事实，不做缺省语义）。

        Returns:
            RedactedDict: 顶层已脱敏 dict（落盘/入 LLM 上下文唯一合法形态）。
        """
        with self._gate:
            return self._export_understanding_locked()

    def _export_understanding_locked(self) -> RedactedDict:
        """export_understanding 的加锁内层（调用方持 _gate，全表读取一致快照）。"""
        out: Dict[str, Any] = {}
        # meta：run 级信息（渲染幂等：时间戳统一取 started_at，§7.3）
        run = None
        if self._run_id is not None:
            run = self._run_meta_row(self._run_id)
        out["meta"] = RedactedDict({
            "system_id": self._system_id,
            "run_id": run["run_id"] if run else None,
            "run_status": run["status"] if run else None,
            "started_at": run["started_at"] if run else None,
            "config_snapshot": run["config_snapshot"] if run else {},
        })
        # UI 透镜：pages（主键序）+ 每页 actions（主键序）
        pages: List[RedactedDict] = []
        for p in self._conn.execute(
                "SELECT * FROM pages ORDER BY page_id").fetchall():
            actions: List[RedactedDict] = []
            for a in self._conn.execute(
                    "SELECT * FROM page_actions WHERE page_id=? ORDER BY action_id",
                    (p["page_id"],)).fetchall():
                actions.append(RedactedDict({
                    "action_id": a["action_id"],
                    "tier": a["tier"],
                    "rule_name": a["rule_name"],
                    "executed": a["executed"],
                    "element_sig": a["element_sig"],
                }))
            pages.append(RedactedDict({
                "page_id": p["page_id"], "url_key": p["url_key"], "url": p["url"],
                "title": p["title"], "depth": p["depth"],
                "discover_from": p["discover_from"], "status": p["status"],
                "snapshot_path": p["snapshot_path"],
                "tech_fingerprint": p["tech_fingerprint"], "error": p["error"],
                "actions": actions,
            }))
        out["pages"] = pages
        # 边表（渲染器 build_navigation_mermaid 数据源）
        out["edges"] = [
            RedactedDict({"from_key": r["from_key"], "to_key": r["to_key"],
                          "via_action": r["via_action"]})
            for r in self._conn.execute("SELECT * FROM edges ORDER BY edge_id").fetchall()
        ]
        # API 透镜：端点聚合（shape/samples JSON 文本解析回 dict/list 再标 RedactedDict）
        endpoints: List[RedactedDict] = []
        for r in self._conn.execute(
                "SELECT * FROM api_observations ORDER BY endpoint_id").fetchall():
            try:
                samples = json.loads(r["samples"] or "[]")
            except json.JSONDecodeError:
                samples = []
            endpoints.append(RedactedDict({
                "endpoint_id": r["endpoint_id"], "url_path": r["url_path"],
                "method": r["method"], "latest_status": r["latest_status"],
                "request_shape": _parse_json_object(r["request_shape"]),
                "response_shape": _parse_json_object(r["response_shape"]),
                "sample_count": r["sample_count"],
                "samples": [RedactedDict(s) if isinstance(s, dict) else s for s in samples],
                "observed_on_pages": [int(x) for x in (r["observed_on_pages"] or "").split(",") if x],
            }))
        out["endpoints"] = endpoints
        # DB 透镜：表卡片（columns_json + samples + implicit_fk 候选）
        tables: List[RedactedDict] = []
        for t in self._conn.execute(
                "SELECT * FROM db_tables ORDER BY table_id").fetchall():
            try:
                columns = json.loads(t["columns_json"] or "[]")
            except json.JSONDecodeError:
                columns = []
            samples = [
                RedactedDict(json.loads(s["row_json"]))
                for s in self._conn.execute(
                    "SELECT row_json FROM db_samples WHERE table_id=?"
                    " ORDER BY row_no", (t["table_id"],)).fetchall()
                if _is_json_object(s["row_json"])
            ]
            tables.append(RedactedDict({
                "table_id": t["table_id"], "schema": t["schema_name"],
                "name": t["table_name"], "kind": t["kind"],
                "row_estimate": t["row_estimate"], "comment": t["comment"],
                "columns": [RedactedDict(c) if isinstance(c, dict) else c for c in columns],
                "samples": samples,
            }))
        out["db_tables"] = tables
        out["implicit_fk_candidates"] = [
            RedactedDict({
                "cand_id": c["cand_id"], "child_table": c["child_table"],
                "child_column": c["child_column"], "parent_table": c["parent_table"],
                "parent_column": c["parent_column"],
                "prescreen_score": c["prescreen_score"],
                "stopped_at_rule": c["stopped_at_rule"], "containment": c["containment"],
                "evidence": c["evidence_json"],
            })
            for c in self._conn.execute(
                "SELECT * FROM implicit_fk_candidates ORDER BY cand_id").fetchall()
        ]
        # Redis 透镜：patterns（聚类）与 keys 明细
        out["redis_patterns"] = [
            RedactedDict({
                "pattern_id": r["pattern_id"], "pattern": r["pattern"],
                "key_count": r["key_count"], "no_ttl_ratio": r["no_ttl_ratio"],
                "ttl_summary": r["ttl_summary"], "type_summary": r["type_summary"],
                "sample_keys": r["sample_keys"],
            })
            for r in self._conn.execute(
                "SELECT * FROM redis_patterns ORDER BY pattern_id").fetchall()
        ]
        out["redis_keys"] = [
            RedactedDict({
                "key_id": r["key_id"], "key_name": r["key_name"],
                "key_type": r["key_type"], "ttl_ms": r["ttl_ms"],
                "encoding": r["encoding"], "mem_bytes": r["mem_bytes"],
                "value_sample": r["value_sample"],
            })
            for r in self._conn.execute(
                "SELECT * FROM redis_keys ORDER BY key_id").fetchall()
        ]
        # 关联证据
        relations: List[RedactedDict] = []
        for r in self._conn.execute(
                "SELECT * FROM relations ORDER BY relation_id").fetchall():
            try:
                ev = json.loads(r["evidence_json"] or "{}")
            except json.JSONDecodeError:
                ev = {}
            relations.append(RedactedDict({
                "relation_id": r["relation_id"], "rtype": r["rtype"],
                "left_ref": r["left_ref"], "right_ref": r["right_ref"],
                "score": r["score"], "evidence": RedactedDict(ev),
            }))
        out["relations"] = relations
        # findings（已入库的 LLM 回填结论）
        out["findings"] = [
            RedactedDict({
                "finding_id": f["finding_id"], "claim": f["claim"],
                "confidence": f["confidence"],
                "evidence_refs": json.loads(f["evidence_refs"] or "[]"),
                "status": f["status"], "kind": f["kind"],
            })
            for f in self._conn.execute(
                "SELECT * FROM findings ORDER BY finding_id").fetchall()
        ]
        # 拦截事件（第 8 节写请求观测清单数据源）
        out["blocked_events"] = [
            RedactedDict({
                "block_id": b["block_id"], "kind": b["kind"], "url": b["url"],
                "method": b["method"], "post_data": b["post_data"],
                "page_id": b["page_id"], "ts": b["ts"],
            })
            for b in self._conn.execute(
                "SELECT * FROM blocked_events ORDER BY block_id").fetchall()
        ]
        return RedactedDict(out)

    def frontier(self) -> List[RedactedDict]:
        """返回预算耗尽时未探索的 frontier 节点（第 10 节 c 项，REQ-SU-008）。"""
        with self._gate:
            return [
                RedactedDict({
                    "page_id": p["page_id"], "url_key": p["url_key"], "url": p["url"],
                    "depth": p["depth"], "status": p["status"],
                })
                for p in self._conn.execute(
                    "SELECT * FROM pages WHERE status IN ('pending','exploring')"
                    " ORDER BY depth, page_id").fetchall()
            ]

    def unfinished_pages(self) -> List[RedactedDict]:
        """返回未采完页面行（--resume 队列重建数据源，REQ-SU-019）。

        口径：status ≠ 'done' 的全部行（pending/error/timeout；exploring
        在 acquire_lock(resume=True) 已重置 pending）。排序 page_id ASC =
        BFS 发现序（_enqueue 入队即落库，page_id 单调即发现顺序），resume
        轮据此重放队列可保持首轮发现顺序与深度/父子溯源连续性。

        Returns:
            List[RedactedDict]: 每行含 page_id/url_key/url/depth/status/
                discover_from（url 为落库时已 scrub 的无凭据形态）。
        """
        with self._gate:
            return [
                RedactedDict({
                    "page_id": p["page_id"], "url_key": p["url_key"], "url": p["url"],
                    "depth": p["depth"], "status": p["status"],
                    "discover_from": p["discover_from"],
                })
                for p in self._conn.execute(
                    "SELECT page_id, url_key, url, depth, status, discover_from"
                    " FROM pages WHERE status != 'done' ORDER BY page_id").fetchall()
            ]

    def stats(self) -> RedactedDict:
        """状态库统计摘要（NFR-SU-006 预算消耗打印 + summary.json 数据源）。"""
        def count(table: str) -> int:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM {0}".format(table)).fetchone()
            return int(row["n"])

        with self._gate:
            return RedactedDict({
                "pages_total": count("pages"),
                "pages_done": count("pages") and self._conn.execute(
                    "SELECT COUNT(*) AS n FROM pages WHERE status='done'").fetchone()["n"],
                "actions_total": count("page_actions"),
                "actions_t3_unexecuted": self._conn.execute(
                    "SELECT COUNT(*) AS n FROM page_actions WHERE tier='T3'").fetchone()["n"],
                "edges_total": count("edges"),
                "endpoints_total": count("api_observations"),
                "blocked_events_total": count("blocked_events"),
                "db_tables_total": count("db_tables"),
                "implicit_fk_total": count("implicit_fk_candidates"),
                "redis_keys_total": count("redis_keys"),
                "redis_patterns_total": count("redis_patterns"),
                "relations_total": count("relations"),
                "findings_total": count("findings"),
            })


def _is_json_object(text: Optional[str]) -> bool:
    """判定文本是否为 JSON 对象（db_samples.row_json 解析防御）。"""
    if not text:
        return False
    try:
        return isinstance(json.loads(text), dict)
    except json.JSONDecodeError:
        return False


def _parse_json_object(text: Optional[str]) -> Optional[RedactedDict]:
    """JSON 文本 → RedactedDict（None/非法/非对象 → None）。

    export 读取路径专用：库内 JSON 文本落库前已过 redact()（§5.2 管线），
    解析回 dict 后重新标 RedactedDict 保持"已脱敏"标记语义连续。
    """
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return RedactedDict(data) if isinstance(data, dict) else None
