# -*- coding: utf-8 -*-
"""SU 能力单元测试：Redis 只读守卫与键模式聚类（REQ-SU-014/015，红线③）。

覆盖 su.redis_guard + su.redis_inspector（纯函数部分）：
- RedisGuard.call：白名单命令放行、拒绝集 100% 拒绝、两段命令第二段单独校验、
  客户端缺方法也拒绝、报错不含参数
- key_to_pattern：数字/UUID/日期/长 hex 段占位符归一，分隔符保留
- cluster_patterns：count / no_ttl_ratio / 类型分布 / 样例 ≤3 / 输出排序
- _ttl_bucket 分桶口径
- RedisInspector 构造防线：client None → ValueError

运行方式（项目根目录）：
    python3 -B -m unittest scripts.tests.test_su_redis_patterns
"""

import sys
import unittest
from pathlib import Path

# 将 scripts/ 目录注入 sys.path，使 `from su.xxx import ...` 生效
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from su.dto import RedisKeyRecordRedacted, SuRedisViolation  # noqa: E402
from su.redis_guard import READONLY_COMMANDS, RedisGuard  # noqa: E402
from su.redis_inspector import (  # noqa: E402
    RedisInspector,
    RedisPattern,
    cluster_patterns,
    key_to_pattern,
)
from su.redis_inspector import _ttl_bucket  # noqa: E402 分桶口径直测


class _FakeRedisClient:
    """redis 客户端替身：只实现白名单方法，记录调用序列。

    刻意不实现 keys/flushall/set/del/config 等写命令——守卫若放行未实现命令
    会因缺方法暴露（同时验证守卫"客户端缺方法也拒绝"分支）。
    """

    def __init__(self):
        self.calls = []  # [(方法名, args, kwargs), ...]

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if name == "scan":
            return (0, ["sess:abc:1"])
        if name == "ttl":
            return 300
        if name == "type":
            return "string"
        if name == "memory_usage":
            return 128
        if name == "object":
            return "embstr"
        return None

    def scan(self, *a, **k):
        return self._record("scan", *a, **k)

    def ttl(self, *a, **k):
        return self._record("ttl", *a, **k)

    def type(self, *a, **k):
        return self._record("type", *a, **k)

    def memory_usage(self, *a, **k):
        return self._record("memory_usage", *a, **k)

    def object(self, *a, **k):
        return self._record("object", *a, **k)


class TestGuardWhitelist(unittest.TestCase):
    """REQ-SU-014 AC1：白名单命令全部放行且按 redis-py 方法名下发。"""

    def setUp(self):
        self.guard = RedisGuard()
        self.client = _FakeRedisClient()

    def test_whitelist_constant(self):
        """白名单 frozenset 口径锁定（KEYS/FLUSHALL 等绝不在内）。"""
        self.assertIsInstance(READONLY_COMMANDS, frozenset)
        for cmd in ("SCAN", "TYPE", "TTL", "PTTL", "MEMORY USAGE", "GET",
                    "HGETALL", "HSCAN", "LRANGE", "SMEMBERS", "ZRANGE",
                    "OBJECT ENCODING"):
            self.assertIn(cmd, READONLY_COMMANDS)
        for bad in ("KEYS", "FLUSHALL", "SET", "DEL", "CONFIG", "DEBUG", "EVAL"):
            self.assertNotIn(bad, READONLY_COMMANDS)

    def test_single_word_dispatch(self):
        """单词命令：TTL → client.ttl(key)（大小写不敏感）。"""
        self.assertEqual(self.guard.call(self.client, "ttl", "k1"), 300)
        self.assertEqual(self.client.calls[-1], ("ttl", ("k1",), {}))

    def test_memory_usage_dispatch(self):
        """MEMORY USAGE → client.memory_usage(key)。"""
        self.assertEqual(self.guard.call(self.client, "MEMORY USAGE", "k"), 128)
        self.assertEqual(self.client.calls[-1], ("memory_usage", ("k",), {}))

    def test_object_encoding_dispatch(self):
        """OBJECT ENCODING → client.object(key, 'encoding')（第二段作编码名）。"""
        self.assertEqual(self.guard.call(self.client, "OBJECT ENCODING", "k"), "embstr")
        self.assertEqual(self.client.calls[-1], ("object", ("k", "encoding"), {}))

    def test_whitespace_flattened(self):
        """命令名内部多余空白压平：'memory  usage' 放行。"""
        self.guard.call(self.client, "memory  usage", "k")
        self.assertEqual(self.client.calls[-1][0], "memory_usage")


class TestGuardDenyAll(unittest.TestCase):
    """REQ-SU-014 AC1：拒绝集 100% 拒绝（无降级放行）。"""

    def setUp(self):
        self.guard = RedisGuard()
        self.client = _FakeRedisClient()

    def test_write_and_admin_commands_denied(self):
        """KEYS/FLUSHALL/SET/DEL/CONFIG/DEBUG/EVAL 等一律 SuRedisViolation。"""
        denied = [
            "KEYS", "FLUSHALL", "FLUSHDB", "SET", "DEL", "CONFIG",
            "DEBUG", "EVAL", "SHUTDOWN", "SAVE", "BGSAVE", "RENAME",
            "EXPIRE", "HSET", "LPUSH", "SADD", "ZADD",
        ]
        for cmd in denied:
            with self.subTest(cmd=cmd):
                with self.assertRaises(SuRedisViolation):
                    self.guard.call(self.client, cmd, "k")
        # 拒绝的命令绝不下发到客户端
        self.assertEqual(self.client.calls, [])

    def test_two_word_second_segment_checked(self):
        """MEMORY DOCTOR / OBJECT FREQ：第一段合法第二段危险 → 拒绝。"""
        for cmd in ("MEMORY DOCTOR", "OBJECT FREQ", "OBJECT HELP", "MEMORY STATS"):
            with self.subTest(cmd=cmd):
                with self.assertRaises(SuRedisViolation):
                    self.guard.call(self.client, cmd, "k")

    def test_three_word_command_denied(self):
        """三段命令塞进命令位（'MEMORY USAGE key'）→ 拒绝。"""
        with self.assertRaises(SuRedisViolation):
            self.guard.call(self.client, "MEMORY USAGE key", "k")

    def test_empty_command_denied(self):
        """空命令名 → 拒绝。"""
        with self.assertRaises(SuRedisViolation):
            self.guard.call(self.client, "", "k")

    def test_client_missing_method_denied(self):
        """白名单命令但客户端缺方法（如 HGETALL）→ 拒绝而非放行。"""
        with self.assertRaises(SuRedisViolation):
            self.guard.call(self.client, "HGETALL", "k")

    def test_violation_message_no_args_leak(self):
        """报错文本不含命令参数（键值片段不随报错扩散）。"""
        secret_key = "sess:token=super-secret-value"
        with self.assertRaises(SuRedisViolation) as ctx:
            self.guard.call(self.client, "GET", secret_key)
        self.assertNotIn(secret_key, str(ctx.exception))
        # 但命令名与白名单清单要在报错里（可解释性）
        self.assertIn("GET", str(ctx.exception))


class TestKeyToPattern(unittest.TestCase):
    """REQ-SU-015 AC2：键名段占位符归一。"""

    def test_numeric_segment(self):
        """纯数字段 → {n}。"""
        self.assertEqual(key_to_pattern("sess:abc:123"), "sess:abc:{n}")

    def test_uuid_segment(self):
        """UUID 段 → {uuid}。"""
        self.assertEqual(
            key_to_pattern("sess:550e8400-e29b-41d4-a716-446655440000"),
            "sess:{uuid}",
        )

    def test_dash_date_segment(self):
        """带横杠日期段 → {date}。"""
        self.assertEqual(key_to_pattern("report:2026-09-28:daily"),
                         "report:{date}:daily")

    def test_compact_date_valid_range(self):
        """8 位紧凑日期且范围合法 → {date}。"""
        self.assertEqual(key_to_pattern("stat:20260928"), "stat:{date}")

    def test_compact_date_invalid_range(self):
        """8 位数字但月/日范围非法 → {n}（数字 id 不误判日期）。"""
        self.assertEqual(key_to_pattern("stat:00000000"), "stat:{n}")
        self.assertEqual(key_to_pattern("stat:20991340"), "stat:{n}")

    def test_long_hex_with_letters(self):
        """≥16 位含 a-f 的 hex 段 → {hex}。"""
        self.assertEqual(
            key_to_pattern("job:deadbeefcafebabe1234"),
            "job:{hex}",
        )

    def test_pure_digits_16_is_n_not_hex(self):
        """纯数字 16 位归 {n} 而非 {hex}（判定顺序：数字先于长 hex）。"""
        self.assertEqual(key_to_pattern("job:1234567890123456"), "job:{n}")

    def test_underscore_separator_preserved(self):
        """下划线分隔符保留。"""
        self.assertEqual(key_to_pattern("user_prefs_42"), "user_prefs_{n}")

    def test_semantic_segments_kept(self):
        """语义词段原样保留。"""
        self.assertEqual(key_to_pattern("cache:product:list"), "cache:product:list")

    def test_empty_key(self):
        """空键名 → 空串。"""
        self.assertEqual(key_to_pattern(""), "")


def _rec(name, key_type="string", ttl_ms=-1):
    """构造键记录替身（聚类输入）。"""
    return RedisKeyRecordRedacted(
        key_name=name, key_type=key_type, ttl_ms=ttl_ms)


class TestClusterPatterns(unittest.TestCase):
    """REQ-SU-015 AC1/AC2：模式聚类输出。"""

    def test_grouping_and_count(self):
        """同模式键归并，count 正确。"""
        recs = [
            _rec("sess:abc:1"), _rec("sess:abc:2"), _rec("sess:abc:3"),
            _rec("cfg:theme"),
        ]
        out = cluster_patterns(recs)
        by_pattern = {p.pattern: p for p in out}
        self.assertEqual(by_pattern["sess:abc:{n}"].key_count, 3)
        self.assertEqual(by_pattern["cfg:theme"].key_count, 1)

    def test_no_ttl_ratio(self):
        """无 TTL 占比：-1 与 None 都计无 TTL。"""
        recs = [
            _rec("a:1", ttl_ms=-1),
            _rec("a:2", ttl_ms=None),
            _rec("a:3", ttl_ms=60_000),
            _rec("a:4", ttl_ms=1000),
        ]
        out = cluster_patterns(recs)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0].no_ttl_ratio, 0.5)

    def test_sample_keys_limit_three(self):
        """样例键 ≤3（输入序前 3）。"""
        recs = [_rec("k:{0}".format(i)) for i in range(10)]
        out = cluster_patterns(recs)
        self.assertEqual(out[0].sample_keys, ["k:0", "k:1", "k:2"])
        self.assertLessEqual(len(out[0].sample_keys), 3)

    def test_type_summary_distribution(self):
        """类型分布计数正确且键排序稳定。"""
        recs = [
            _rec("h:1", key_type="hash"), _rec("h:2", key_type="hash"),
            _rec("h:3", key_type="string"),
        ]
        out = cluster_patterns(recs)
        self.assertEqual(dict(out[0].type_summary), {"hash": 2, "string": 1})

    def test_ttl_summary_buckets(self):
        """TTL 分桶计数与 _ttl_bucket 口径一致。"""
        recs = [
            _rec("b:1", ttl_ms=-1),        # no_ttl
            _rec("b:2", ttl_ms=-2),        # expired_or_missing
            _rec("b:3", ttl_ms=59_999),    # lt_1min
            _rec("b:4", ttl_ms=3_599_999),  # lt_1h
            _rec("b:5", ttl_ms=86_399_999),  # lt_1d
            _rec("b:6", ttl_ms=86_400_000),  # gte_1d
        ]
        out = cluster_patterns(recs)
        summary = dict(out[0].ttl_summary)
        self.assertEqual(summary.get("no_ttl"), 1)
        self.assertEqual(summary.get("expired_or_missing"), 1)
        self.assertEqual(summary.get("lt_1min"), 1)
        self.assertEqual(summary.get("lt_1h"), 1)
        self.assertEqual(summary.get("lt_1d"), 1)
        self.assertEqual(summary.get("gte_1d"), 1)

    def test_output_order_count_desc_pattern_asc(self):
        """输出序：count 降序 → pattern 升序（渲染幂等前提）。"""
        recs = [
            _rec("zzz:1"), _rec("zzz:2"),
            _rec("aaa:1"), _rec("aaa:2"),
            _rec("mmm:1"),
        ]
        out = cluster_patterns(recs)
        patterns = [p.pattern for p in out]
        # aaa 与 zzz 同 count=2 → 字母序 aaa 在前；mmm count=1 最后
        self.assertEqual(patterns, ["aaa:{n}", "zzz:{n}", "mmm:{n}"])

    def test_empty_input(self):
        """空输入 → 空列表。"""
        self.assertEqual(cluster_patterns([]), [])

    def test_to_dict_json_shapes(self):
        """RedisPattern.to_dict 三列 JSON 文本序列化（sort_keys 稳定）。"""
        pat = RedisPattern(
            pattern="s:{n}", key_count=2, no_ttl_ratio=0.5,
            ttl_summary={"no_ttl": 1, "lt_1min": 1},
            type_summary={"string": 2},
            sample_keys=["s:1", "s:2"],
        )
        d = pat.to_dict()
        self.assertEqual(d["pattern"], "s:{n}")
        self.assertIn('"no_ttl": 1', d["ttl_summary"])
        self.assertIn("s:1", d["sample_keys"])


class TestTtlBucket(unittest.TestCase):
    """REQ-SU-015：TTL 分桶边界。"""

    def test_boundaries(self):
        """各分桶边界值归桶正确。"""
        self.assertEqual(_ttl_bucket(None), "no_ttl")
        self.assertEqual(_ttl_bucket(-1), "no_ttl")
        self.assertEqual(_ttl_bucket(-2), "expired_or_missing")
        self.assertEqual(_ttl_bucket(0), "lt_1min")
        self.assertEqual(_ttl_bucket(59_999), "lt_1min")
        self.assertEqual(_ttl_bucket(60_000), "lt_1h")
        self.assertEqual(_ttl_bucket(3_599_999), "lt_1h")
        self.assertEqual(_ttl_bucket(3_600_000), "lt_1d")
        self.assertEqual(_ttl_bucket(86_399_999), "lt_1d")
        self.assertEqual(_ttl_bucket(86_400_000), "gte_1d")


class TestInspectorConstruction(unittest.TestCase):
    """REQ-SU-021：客户端注入缺失快速失败。"""

    def test_none_client_rejected(self):
        """client=None → ValueError（绝不静默 mock）。"""
        from su.config import RedisConfig
        from su.dto import SensitiveStr
        cfg = RedisConfig(host="localhost", port=6379,
                          password=SensitiveStr(""), db=0)
        with self.assertRaises(ValueError):
            RedisInspector(cfg, store=None, client=None)


if __name__ == "__main__":
    unittest.main()
