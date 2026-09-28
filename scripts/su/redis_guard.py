"""Redis 只读命令白名单守卫（红线③）——架构 ARCH-SU-001 §2.3.9 / §5.4，PRD REQ-SU-014。

**本模块的 :meth:`RedisGuard.call` 是整个 ``su/`` 包唯一的 redis 命令出口**
（§5.4 原文口径）。redis_inspector（后续交付）以
``from su.redis_guard import RedisGuard, READONLY_COMMANDS`` 复用——独立成文件的
理由同 db_guard：白名单是**纯判定逻辑**，可在无 Redis 容器环境下完成全部拒绝集
单测（§11 ``test_su_redis_patterns``：KEYS/FLUSHALL/SET/DEBUG/EVAL/MEMORY DOCTOR
等必须 100% 拒绝，REQ-SU-014 AC1）。

**不 import redis**：``client`` 参数按 duck-typing 使用（redis 库对象由调用方
注入），遵守"软依赖绝不顶层硬 import"红线（REQ-SU-021；探测统一在 deps.py）。

两词命令处理（§5.4 审查口径）：``MEMORY USAGE`` / ``OBJECT ENCODING`` 以**两词
整体**为 key 匹配，且**第二段单独校验**——这样 ``MEMORY DOCTOR`` 之类"第一段合法、
第二段危险"的混入会被拒绝（DOCTOR 会泄露内存布局等运行时信息，不属只读内面白名单）。
"""

from typing import Any, Tuple

from su.dto import SuRedisViolation

__all__ = ["READONLY_COMMANDS", "RedisGuard"]

# 只读命令白名单（§2.3.9 硬编码 frozenset；KEYS/FLUSHALL/SET/DEL/CONFIG/DEBUG/
# EVAL 等一律"not in → raise"隐式拒绝，不设黑名单以杜绝黑名单遗漏面）。
# 两词命令以整串为条目（Redis 的 ``MEMORY USAGE`` / ``OBJECT ENCODING`` 形态）。
READONLY_COMMANDS = frozenset({
    "SCAN",           # 游标枚举（禁 KEYS：KEYS 会阻塞单线程服务端）
    "TYPE",
    "TTL",
    "PTTL",
    "MEMORY USAGE",   # 两词命令：序列化大小
    "GET",
    "HGETALL",
    "HSCAN",          # 大 hash 降级用（REQ-SU-014.1）
    "LRANGE",
    "SMEMBERS",
    "ZRANGE",
    "OBJECT ENCODING",  # 两词命令：内部编码
})


class RedisGuard:
    """Redis 只读命令白名单守卫（红线③，su/ 包唯一命令出口）。

    用法（redis_inspector 唯一入口）::

        guard = RedisGuard()
        cursor, keys = guard.call(client, "SCAN", 0, "COUNT", 200, "MATCH", "sess:*")
        ktype = guard.call(client, "TYPE", key)

    Args 形态说明（与 redis-py 的调用习惯对齐）：
      - 单词命令：``call(client, "TTL", key)`` → ``client.ttl(key)``
      - 两词命令：``call(client, "MEMORY USAGE", key)`` → ``client.memory_usage(key)``
        即第一段映射为 redis-py 的方法名前缀（``memory`` / ``object``），
        第二段作为该方法的**首个位置参数**（redis-py 的 ``memory_usage(key)`` /
        ``object(key, "encoding")`` 形态在下方按方法名区分处理）。
    """

    # 两词命令的"合法第二段"表（§5.4：第二段也校验，防 MEMORY DOCTOR 混入）。
    # key = 命令第一段（如 'MEMORY'），value = 允许的第二段集合。
    _TWO_WORD_SECOND_ARG = {
        "MEMORY": frozenset({"USAGE"}),
        "OBJECT": frozenset({"ENCODING"}),
    }

    # 两词命令 → redis-py 方法名（第二段作为首参或子命令参数传入）
    _TWO_WORD_METHODS = {
        ("MEMORY", "USAGE"): "memory_usage",
        ("OBJECT", "ENCODING"): "object",
    }

    def call(self, client: Any, command: str, *args: Any) -> Any:
        """白名单校验后调用 redis 命令（未命中即抛，绝不"降级放行"）。

        Args:
            client: redis 库客户端对象（duck-typing，本模块不 import redis）。
            command: 命令名（大小写不敏感；两词命令写 ``"MEMORY USAGE"``）。
            *args: 命令参数（键名、游标、COUNT/MATCH 等）。

        Returns:
            Any: 底层命令的原始返回值（未脱敏——调用方负责按需 redact，
                如 redis_inspector 对值样例做 512B 截断 + config.redact）。

        Raises:
            SuRedisViolation: 命令名不在只读白名单 / 两词命令第二段非法 /
                客户端不支持该命令方法。
        """
        normalized = self._normalize(command)

        # ---- 单段命令：整串 ∈ 白名单 ----
        if " " not in normalized:
            if normalized not in READONLY_COMMANDS:
                raise self._violation(command, normalized, args)
            return self._invoke_single(client, normalized, args)

        # ---- 两词命令：整体匹配 + 第二段单独校验（§5.4）----
        tokens: Tuple[str, ...] = tuple(normalized.split())
        if len(tokens) != 2:
            # 三段及以上（如 "MEMORY USAGE key extra" 误填进命令位）一律拒绝
            raise self._violation(command, normalized, args)
        first, second = tokens
        if first not in self._TWO_WORD_SECOND_ARG:
            raise self._violation(command, normalized, args)
        if second not in self._TWO_WORD_SECOND_ARG[first]:
            # MEMORY DOCTOR / OBJECT FREQ 之类：第一段合法但第二段不在白名单
            raise SuRedisViolation(
                "Redis 命令 {0} 的第二段 {1} 不在只读白名单内，已拒绝（"
                "两段命令必须整体命中 MEMORY USAGE / OBJECT ENCODING）".format(
                    first, second
                ),
                hints=["只读内面仅允许：MEMORY USAGE、OBJECT ENCODING"],
            )
        return self._invoke_two_word(client, first, second, args)

    # ---- 内部辅助 ----

    @staticmethod
    def _normalize(command: str) -> str:
        """命令名归一：大写、压平内部空白（``"memory  usage"`` → ``"MEMORY USAGE"``）。"""
        return " ".join((command or "").upper().split())

    @staticmethod
    def _violation(command: str, normalized: str, args: Tuple[Any, ...]) -> SuRedisViolation:
        """构造白名单违例异常（中文报错，命令参数不进 message 防键名值泄露）。

        Args:
            command: 调用方原始命令名。
            normalized: 归一后的命令名。
            args: 命令参数——**仅用于判定是否发生过调用**，绝不出现在报错文本中
                （参数常含完整键名/键值片段，避免随报错扩散）。

        Returns:
            SuRedisViolation: 携带白名单清单的中文违例异常。
        """
        return SuRedisViolation(
            "Redis 命令 '{0}' 不在只读白名单内，已拒绝执行（红线③）".format(
                normalized or command
            ),
            hints=[
                "只读白名单：{0}".format("、".join(sorted(READONLY_COMMANDS))),
                "键枚举必须用 SCAN（游标式），禁用 KEYS（会阻塞服务端）",
            ],
        )

    @staticmethod
    def _invoke_single(client: Any, command: str, args: Tuple[Any, ...]) -> Any:
        """单词命令下发：命令名小写即 redis-py 方法名（ttl/type/scan/get/...）。

        Raises:
            SuRedisViolation: 客户端上找不到对应可调用方法（视为不可用而非放行）。
        """
        method_name = command.lower()
        method = getattr(client, method_name, None)
        if not callable(method):
            raise SuRedisViolation(
                "Redis 客户端不支持只读命令 '{0}'（找不到 {1}() 方法），已拒绝".format(
                    command, method_name
                ),
                hints=["请确认使用 redis>=5.0.0 标准客户端"],
            )
        return method(*args)

    @staticmethod
    def _invoke_two_word(
        client: Any,
        first: str,
        second: str,
        args: Tuple[Any, ...],
    ) -> Any:
        """两词命令下发（第二段已由 call 校验过白名单）。

        redis-py 对两段命令提供专用方法：
          - ``MEMORY USAGE k`` → ``client.memory_usage(k)``
          - ``OBJECT ENCODING k`` → ``client.object(k, "encoding")``
        因此 OBJECT 需把第二段作为编码名追加到参数尾部（redis-py 的签名形态）。
        """
        method_name = RedisGuard._TWO_WORD_METHODS[(first, second)]
        method = getattr(client, method_name, None)
        if not callable(method):
            raise SuRedisViolation(
                "Redis 客户端不支持只读命令 '{0} {1}'（找不到 {2}() 方法），已拒绝".format(
                    first, second, method_name
                ),
                hints=["请确认使用 redis>=5.0.0 标准客户端"],
            )
        if first == "OBJECT":
            # redis-py: object(name, encoding=None) —— 第二段即 encoding 取值
            return method(*args, second.lower())
        return method(*args)
