"""SU 状态库种子 builder——为渲染类 e2e 场景构造带真实种子数据的 sqlite。

用途：--render-only 场景（e2e 场景[5]）需要一个"此前运行产物"状态库。本
builder 复用生产代码 su.state_store.StateStore 建库（schema 与生产逐字节
一致，绝非手工 DDL 仿制品），再经 INSERT 注入各表最小可渲染种子数据。

红线口径：本模块是 **fixture 数据构造器**（测试站/测试库本就是被测对象自
身的一部分），不是 mock——产出的状态库会被真实的 system_understanding.py
--render-only 全流程消费校验。

编程入口：build_state_db(db_path, system_id=...) -> dict
    返回注入数据的 id 映射（供 findings evidence_refs 引用真实行 id）。
"""

import sys
import time
from pathlib import Path

# 复用生产 schema：把 scripts/ 加入 sys.path 后 import su.state_store
_SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from su.state_store import StateStore  # noqa: E402 - sys.path 注入后方可导入

__all__ = ["build_state_db", "finalize_state_db",
           "SEED_DB_TABLES", "SEED_PAGES", "SEED_ENDPOINTS"]

# 种子页面（url_key 归一化形态：路径小写、query 字典序——与 su.url_key 口径一致）
SEED_PAGES = [
    # (url_key, url, title, depth, status)
    ("/dashboard", "http://127.0.0.1:9/dashboard", "控制台", 0, "done"),
    ("/orders", "http://127.0.0.1:9/orders", "订单列表", 1, "done"),
    ("/products", "http://127.0.0.1:9/products", "商品总览", 1, "done"),
    ("/orders#/orders/1", "http://127.0.0.1:9/orders#/orders/1", "订单明细 1", 2, "done"),
    # timeout 页：SFD 测试视角盲区素材（REQ-SFD-010 要求覆盖 timeout/error 页
    # 场景）——status='timeout' 不参与 done 页渲染口径，既有断言按 status 过滤
    # 的语义不受影响。
    ("/reports", "http://127.0.0.1:9/reports", "报表中心", 1, "timeout"),
]

# 种子 API 端点（/api/orders 键名与 orders 表列名高合合作关联证据素材）
SEED_ENDPOINTS = [
    # (url_path, method, status, response_shape)
    ("/api/orders", "GET", 200,
     '{"orders":[{"customer_name":"str","order_id":"int","status":"str","total_amount":"float"}]}'),
]

# 种子数据表（模拟 fixture 站背后的业务库：orders 列与 /api/orders 键重合）
SEED_DB_TABLES = [
    # (schema, table, kind, columns: [(name, type, family, is_pk)])
    ("fixturedb", "orders", "BASE TABLE", [
        ("order_id", "int", "int", 1),
        ("customer_name", "varchar(64)", "string", 0),
        ("total_amount", "decimal(10,2)", "other", 0),
        ("status", "varchar(16)", "string", 0),
    ]),
    ("fixturedb", "customers", "BASE TABLE", [
        ("customer_id", "int", "int", 1),
        ("customer_name", "varchar(64)", "string", 0),
    ]),
]


def build_state_db(db_path: Path, system_id: str = "127.0.0.1",
                   run_status: str = "completed") -> dict:
    """建库并注入最小可渲染种子数据（幂等：目标文件存在即先删除重建）。

    Args:
        db_path: 状态库目标路径（如 <out>/<system_id>/state/understanding.sqlite）。
        system_id: 系统标识（run_meta.system_id，与输出目录名一致）。
        run_status: 注入的 run 终态（--render-only 要求 completed/interrupted）。

    Returns:
        dict: 种子数据 id 映射，形如
            {"page_ids": {url_key: page_id}, "endpoint_ids": {...},
             "table_ids": {table_name: table_id}, "column_ids": {...}}
            ——findings.evidence_refs 必须以这些真实 id 构造 '<table>:<id>'。
    """
    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    store = StateStore(db_path, system_id)
    ids = {"page_ids": {}, "endpoint_ids": {}, "table_ids": {}, "column_ids": {}}
    try:
        # acquire_lock（resume=False）：走生产建 run_meta 行路径。种子数据
        # 全部挂这条"种子 run"；CLI/调用方收口 finalize 后最新行=终态——
        # --render-only 的 acquire_lock(resume=True) 对 completed/interrupted
        # 历史 run 新建 run 行再校验，既不触碰种子 run 也必然通过前置
        store.acquire_lock(resume=False)
        conn = store._conn  # fixture builder 专用：生产 API 无批量 INSERT 口
        now = time.time()

        # -- pages：种子页面（depth/status 与 BFS 语义一致） ----------------
        for url_key, url, title, depth, status in SEED_PAGES:
            cur = conn.execute(
                "INSERT INTO pages(url_key,url,title,depth,discover_from,status,"
                "created_at,updated_at) VALUES(?,?,?,?,NULL,?,?,?)",
                (url_key, url, title, depth, status, now, now))
            ids["page_ids"][url_key] = int(cur.lastrowid)

        # -- edges：dashboard→orders→orders#hash 链（导航图素材） ------------
        dashboard_id = ids["page_ids"]["/dashboard"]
        conn.execute(
            "INSERT INTO edges(from_key,to_key,via_action) VALUES(?,?,NULL)",
            ("/dashboard", "/orders"))
        conn.execute(
            "INSERT INTO edges(from_key,to_key,via_action) VALUES(?,?,NULL)",
            ("/orders", "/orders#/orders/1"))

        # -- page_actions：T3 未执行动作（红线口径素材） ---------------------
        conn.execute(
            "INSERT INTO page_actions(page_id,element_sig,tier,rule_name,executed)"
            "VALUES(?,?,'T3','dangerous_keyword',0)",
            (ids["page_ids"]["/orders"], "button#delete-selected"))

        # -- api_observations：GET /api/orders（response_shape 列名素材） ----
        for path, method, status, shape in SEED_ENDPOINTS:
            cur = conn.execute(
                "INSERT INTO api_observations(url_path,method,latest_status,"
                "request_shape,response_shape,sample_count,samples,observed_on_pages)"
                "VALUES(?,?,?,NULL,?,1,?,?)",
                (path, method, status, shape,
                 '[{"orders":[{"order_id":1}]}]', str(dashboard_id)))
            ids["endpoint_ids"][f"{method}:{path}"] = int(cur.lastrowid)

        # -- blocked_events：aborted_method 一条（第 8 节观测清单素材） ------
        conn.execute(
            "INSERT INTO blocked_events(kind,url,method,post_data,page_id,ts)"
            "VALUES('aborted_method','/api/orders/delete','POST',NULL,?,?)",
            (ids["page_ids"]["/orders"], now))

        # -- db_tables / db_columns / db_samples（数据模型节素材） -----------
        for schema_name, table_name, kind, columns in SEED_DB_TABLES:
            cols_json = str([c[0] for c in columns])
            cur = conn.execute(
                "INSERT INTO db_tables(schema_name,table_name,kind,row_estimate,"
                "comment,columns_json) VALUES(?,?,?,10,NULL,?)",
                (schema_name, table_name, kind, cols_json))
            table_id = int(cur.lastrowid)
            ids["table_ids"][table_name] = table_id
            for col_name, data_type, family, is_pk in columns:
                cur = conn.execute(
                    "INSERT INTO db_columns(table_id,name,data_type,type_family,"
                    "is_pk,fk_target,comment) VALUES(?,?,?,?,?,NULL,NULL)",
                    (table_id, col_name, data_type, family, is_pk))
                ids["column_ids"][f"{table_name}.{col_name}"] = int(cur.lastrowid)

        # -- implicit_fk_candidates：orders.customer_name→customers（ER 素材）
        conn.execute(
            "INSERT INTO implicit_fk_candidates(child_table,child_column,"
            "parent_table,parent_column,prescreen_score,stopped_at_rule,"
            "containment,evidence_json) VALUES(?,?,?,?,?,3,1.0,'{}')",
            ("orders", "customer_name", "customers", "customer_name", 0.92))

        # -- redis_patterns / redis_keys：缓存节素材 --------------------------
        conn.execute(
            "INSERT INTO redis_keys(key_name,key_type,ttl_ms,encoding,mem_bytes,"
            "value_sample) VALUES('order:1:detail','string',3600000,'embstr',220,"
            "'{\"order_id\":1}')")
        conn.execute(
            "INSERT INTO redis_patterns(pattern,key_count,no_ttl_ratio,ttl_summary,"
            "type_summary,sample_keys) VALUES('order:*',1,0.0,'avg 3600s','string:1',"
            "'order:1:detail')")

        # -- relations：page_api / api_table 确定性证据 -----------------------
        conn.execute(
            "INSERT INTO relations(rtype,left_ref,right_ref,score,evidence_json)"
            "VALUES('page_api',?,?,1.0,'{\"observed_on_pages\":[\"/orders\"]}')",
            (f"page:{ids['page_ids']['/orders']}", "api:GET /api/orders"))
        conn.execute(
            "INSERT INTO relations(rtype,left_ref,right_ref,score,evidence_json)"
            "VALUES('api_table',?,?,0.8,'{\"shared_columns\":["
            "\"order_id\",\"customer_name\",\"total_amount\",\"status\"]}')",
            ("api:GET /api/orders", "table:orders"))

        # run 终态由调用方收口（--render-only 要求 acquire_lock 结束时最新 run
        # 状态 ∈ {completed, interrupted}：builder 若留 running，编排层会走
        # "陈旧锁接管→status 改回 running"，届时 render-only 前置校验必然失败）。
        # 因此 CLI/调用方拿到 ids 后必须执行 finalize_state_db()。
        store.heartbeat()
        conn.commit()
        return ids
    finally:
        store.close()


def finalize_state_db(db_path: Path, run_status: str = "completed") -> None:
    """种子库收口：把**全部**未收口 run 置为终态并释放锁（render-only 前置）。

    build_state_db 建库后保持最新 run='running'（模拟"采集进行中"）；调用方
    注入完 findings 等附加数据后调用本函数收口。--render-only 的
    acquire_lock(resume=True) 对 completed/interrupted 最新行**新建 run**
    （既不触碰种子 run，也不会把历史 running 锁接管成最新行），随后校验
    最新行状态必然通过——前提就是 finalize 把最新行置成了终态。

    历史 running 行（如早期版本 builder 的锁残留）一并清算：render-only 的
    acquire_lock 会"接管最新 running 行（status 改写回 running）"，残留行
    未收口时前置校验必然失败，故这里按 locked_by 残留统一置 failed。

    Args:
        db_path: 状态库路径。
        run_status: 目标终态（completed / interrupted）。
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        # 1. 种子 run（最新一行）置目标终态 + 释放锁 + 记完成时间
        conn.execute(
            "UPDATE run_meta SET status=?, locked_by=NULL, finished_at=?"
            " WHERE run_id=(SELECT run_id FROM run_meta"
            " ORDER BY started_at DESC LIMIT 1)",
            (run_status, time.time()),
        )
        # 2. 其余仍为 running 的历史行（builder 锁残留）清算为 failed——
        #    render-only acquire_lock 的接管分支只针对"最新一行"，但任何
        #    未收口 running 行都会破坏"最新 run=终态"的可预期性
        conn.execute(
            "UPDATE run_meta SET status='failed', locked_by=NULL, finished_at=?"
            " WHERE status='running'",
            (time.time(),),
        )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    # 命令行模式：python su_state_builder.py <db_path> [system_id] [run_status]
    if len(sys.argv) < 2:
        print("用法：python su_state_builder.py <db_path> [system_id] [run_status]")
        sys.exit(2)
    seed_ids = build_state_db(
        Path(sys.argv[1]),
        system_id=sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1",
        run_status=sys.argv[3] if len(sys.argv) > 3 else "completed",
    )
    # CLI 模式注入完即收口（无附加回填需求时直接置终态）
    finalize_state_db(Path(sys.argv[1]),
                      sys.argv[3] if len(sys.argv) > 3 else "completed")
    import json as _json

    print(_json.dumps(seed_ids, ensure_ascii=False, sort_keys=True))
