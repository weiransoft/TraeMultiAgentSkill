# Dynamic Workflows Phase 9 实施计划：InterruptionRecovery

**日期**：2026-06-04
**前序**：[PHASE8_FINAL_REPORT.md](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/PHASE8_FINAL_REPORT.md)（634 tests 通过）
**依据**：[DYNAMIC_WORKFLOWS_INTEGRATION.md v1.5](file:///Users/wangwei/claw/.trae/skills/trae-multi-agent/docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md) §下一步决策 + 架构师审查 §3.0、§6

---

## 一、范围与目标

### Phase 9 范围

实现 **InterruptionRecovery（中断恢复）**：subagent 在执行过程中遇到 **中断**（崩溃、超时、信号、资源耗尽、用户取消）时，能够 **自动检测 + 选择恢复策略 + 状态保存/恢复 + 重试或降级**，**避免当前 sandbox 一旦崩溃即丢失全部上下文的问题**。

### 核心问题与解法

| # | 痛点 | 解法 |
|---|------|------|
| 1 | sandbox.execute() 捕获异常后只返回 SandboxResult，父 workflow 不知道能否恢复 | 新增 `InterruptionRecoveryManager` 统一调度 |
| 2 | 没有 subagent 暂停 / 恢复机制 | 引入 `threading.Event` 实现 pause/resume |
| 3 | 重试仅在 pattern_executor 有 no-op placeholder | 新增 `RetryPolicy` 真实退避算法 |
| 4 | 多次失败后无法选择降级策略 | 新增 `RecoveryStrategy` 枚举（5 种） |
| 5 | subagent 崩溃后无状态保留 | 新增 `SubagentStateSnapshot` 数据类 |
| 6 | 无法区分不同中断类型 | 新增 `InterruptionType` 枚举（5 种） |
| 7 | 与 CheckpointManager 缺联动 | 复用 V2 `CheckpointManager` 做深恢复 |

### 必须遵守的硬约束（架构师审查 §3.0 + Phase 1-8 沉淀）

| # | 约束 | 实施策略 |
|---|------|---------|
| 1 | 🔴 向后兼容 | `recovery_manager=None` 时行为与 Phase 8 完全一致 |
| 2 | 🔴 V2 不修改 | 通过 `SubagentSandbox.__init__(recovery_manager=...)` 扩展点注入；V2 文件零修改 |
| 3 | 🔴 持久化复用 | 复用 V2 `CheckpointManager` 的 `save_checkpoint` / `load_checkpoint`；状态写入 `PerformanceFingerprint` |
| 4 | 🔴 一阶段一模块 | Phase 9 仅做 InterruptionRecovery；/loop+/goal 留到 Phase 10 |
| 5 | 🔴 安全 | 重试有上限（默认 3 次）；失败后写沙箱元数据，不静默吞错 |
| 6 | 🔴 一致性 | 复用 Phase 8 `SandboxContext` 的字段，**不重复**；扩展而非替换 |

---

## 二、数据模型扩展

### 2.1 新增枚举与数据类

#### 2.1.1 `InterruptionType`（中断类型）

```python
class InterruptionType(str, Enum):
    """subagent 中断类型"""
    TIMEOUT = "timeout"               # 执行超时
    EXCEPTION = "exception"           # 业务异常
    SIGNAL = "signal"                 # 外部信号（SIGINT/SIGTERM）
    RESOURCE_EXHAUSTED = "resource_exhausted"  # Token/内存/CPU 耗尽
    USER_ABORT = "user_abort"         # 用户主动取消
    UNKNOWN = "unknown"               # 未知
```

#### 2.1.2 `RecoveryStrategy`（恢复策略）

```python
class RecoveryStrategy(str, Enum):
    """subagent 中断恢复策略"""
    RETRY = "retry"           # 原地重试（同一 sandbox 状态恢复）
    RESTART = "restart"       # 全新 sandbox 重启（state 持久化但 sandbox 重建）
    FALLBACK = "fallback"     # 降级（切到 haiku / 减小 token_budget）
    SKIP = "skip"             # 跳过此 subagent，标记为失败
    MANUAL = "manual"         # 需人工介入（返回待决策状态）
    ABORT = "abort"           # 整体 workflow 终止
```

#### 2.1.3 `RetryPolicy`（重试策略）

```python
@dataclass
class RetryPolicy:
    """重试退避策略"""
    max_retries: int = 3                      # 最大重试次数
    initial_delay_ms: int = 1000              # 初始延迟
    backoff_factor: float = 2.0               # 退避因子
    max_delay_ms: int = 30000                 # 最大延迟
    jitter: bool = True                       # 是否加随机抖动
    retry_on: Tuple[InterruptionType, ...] = ( # 可重试的中断类型
        InterruptionType.TIMEOUT,
        InterruptionType.EXCEPTION,
        InterruptionType.RESOURCE_EXHAUSTED,
    )

    def compute_delay_ms(self, attempt: int) -> int:
        """计算第 N 次重试的延迟（毫秒）"""
        delay = self.initial_delay_ms * (self.backoff_factor ** attempt)
        delay = min(delay, self.max_delay_ms)
        if self.jitter:
            # 加 0-25% 抖动
            delay = int(delay * (1.0 + random.uniform(0, 0.25)))
        return int(delay)

    def should_retry(self, attempt: int, interruption_type: InterruptionType) -> bool:
        """是否应该重试"""
        return (
            attempt < self.max_retries
            and interruption_type in self.retry_on
        )
```

#### 2.1.4 `SubagentStateSnapshot`（状态快照）

```python
@dataclass
class SubagentStateSnapshot:
    """subagent 状态快照（用于中断恢复）"""
    snapshot_id: str                          # 唯一 ID
    sandbox_id: str                           # 所属沙箱
    agent_id: str                             # 所属 subagent
    task: Dict[str, Any]                      # 原始 task 字典
    progress: float = 0.0                     # 进度 0-100
    intermediate_results: Dict[str, Any]      # 中间结果（可序列化）
    executor_state: Dict[str, Any]            # executor 状态（可序列化）
    checkpoint_id: Optional[str] = None       # 关联的 V2 Checkpoint
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict（用于持久化）"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SubagentStateSnapshot":
        """从 dict 反序列化"""
        return cls(**data)
```

#### 2.1.5 `InterruptionRecord`（中断记录）

```python
@dataclass
class InterruptionRecord:
    """subagent 中断记录（用于追溯）"""
    record_id: str                            # 唯一 ID
    sandbox_id: str                           # 沙箱 ID
    agent_id: str                             # subagent ID
    interruption_type: InterruptionType       # 中断类型
    strategy: RecoveryStrategy                # 选定的恢复策略
    attempts: int = 0                         # 已尝试次数
    max_attempts: int = 3                     # 最大尝试次数
    last_error: Optional[str] = None          # 最后一次错误
    snapshot_id: Optional[str] = None         # 状态快照 ID
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    recovered_at: Optional[str] = None        # 恢复成功时间

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "sandbox_id": self.sandbox_id,
            "agent_id": self.agent_id,
            "interruption_type": self.interruption_type.value,
            "strategy": self.strategy.value,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "last_error": self.last_error,
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at,
            "recovered_at": self.recovered_at,
        }
```

### 2.2 关键决策记录

| 决策点 | 选项 | 选定 | 理由 |
|--------|------|------|------|
| 重试机制 | threading.Timer / Event | **Event** | 支持外部触发暂停/恢复，不依赖定时器 |
| Snapshot 存储 | 内存 / 文件 / Checkpoint | **内存 + 可选 Checkpoint** | 默认内存高性能，可选持久化到 V2 Checkpoint |
| 退避算法 | 固定 / 线性 / 指数 | **指数 + 抖动** | 业界最佳实践，避免雪崩 |
| 策略选择 | 硬编码 / 智能 / 外部注入 | **外部注入（task 字段）** | 用户可按任务定制；默认智能选择 |
| 恢复执行器 | 同一函数 / 包装函数 | **用户可注入** | 部分场景需要状态注入，恢复时传新参数 |
| 重试上限 | 3 / 5 / 无限 | **默认 3，用户可改** | 平衡灵活性和安全性 |
| 并发安全 | Lock / RLock | **Lock + Event** | Event 自带线程安全 |

---

## 三、核心组件设计

### 3.1 6 大核心组件

| # | 组件 | 文件 | 职责 |
|---|------|------|------|
| 1 | `InterruptionType` 枚举 | interruption_recovery.py | 5 种中断类型分类 |
| 2 | `RecoveryStrategy` 枚举 | interruption_recovery.py | 5 种恢复策略 |
| 3 | `RetryPolicy` 数据类 | interruption_recovery.py | 指数退避 + 抖动 |
| 4 | `SubagentStateSnapshot` 数据类 | interruption_recovery.py | 序列化 / 反序列化 |
| 5 | `InterruptionRecord` 数据类 | interruption_recovery.py | 中断追溯 |
| 6 | `InterruptionRecoveryManager` 类 | interruption_recovery.py | 主调度器 |

### 3.2 `InterruptionRecoveryManager` 接口

```python
class InterruptionRecoveryManager:
    """
    中断恢复管理器

    核心职责：
    1. 记录中断事件（record_interruption）
    2. 智能选择恢复策略（基于 task.interruption_policy 字段）
    3. 状态快照保存/恢复（save_snapshot / load_snapshot）
    4. 调度恢复动作（attempt_recovery）
    5. 恢复历史追溯（get_history）
    """

    def __init__(
        self,
        retry_policy: Optional[RetryPolicy] = None,
        checkpoint_manager: Optional["CheckpointManager"] = None,  # V2
        fingerprint: Optional["PerformanceFingerprint"] = None,    # V2
        default_strategy: RecoveryStrategy = RecoveryStrategy.RETRY,
    ):
        self.retry_policy = retry_policy or RetryPolicy()
        self.checkpoint_manager = checkpoint_manager
        self.fingerprint = fingerprint
        self.default_strategy = default_strategy
        # 状态：活跃中断记录 + 恢复历史
        self._active_records: Dict[str, InterruptionRecord] = {}
        self._snapshots: Dict[str, SubagentStateSnapshot] = {}
        self._recovery_history: List[InterruptionRecord] = []
        self._lock = threading.Lock()

    # 核心 API
    def save_snapshot(
        self,
        sandbox_id: str,
        agent_id: str,
        task: Dict[str, Any],
        progress: float = 0.0,
        intermediate_results: Optional[Dict[str, Any]] = None,
        executor_state: Optional[Dict[str, Any]] = None,
    ) -> SubagentStateSnapshot:
        """保存状态快照（executor 主动调用）"""
        ...

    def record_interruption(
        self,
        sandbox_id: str,
        agent_id: str,
        interruption_type: InterruptionType,
        error: Optional[str] = None,
        task: Optional[Dict[str, Any]] = None,
    ) -> InterruptionRecord:
        """
        记录中断事件 + 选择恢复策略

        策略选择逻辑：
        1. 读 task["interruption_policy"]（如果存在）
        2. 读 task["recovery_strategy"]（如果存在）
        3. 根据 interruption_type 智能选择：
           - TIMEOUT → RETRY（指数退避）
           - EXCEPTION → RETRY（同上）
           - SIGNAL → RESTART（信号通常意味着需要重置）
           - RESOURCE_EXHAUSTED → FALLBACK（切到低资源）
           - USER_ABORT → SKIP（用户主动取消不再继续）
           - UNKNOWN → MANUAL（保守）
        4. 重试次数超限 → 升级到 FALLBACK / SKIP / MANUAL
        """
        ...

    def attempt_recovery(
        self,
        record: InterruptionRecord,
        executor: Callable[[SandboxContext, Optional[SubagentStateSnapshot]], Any],
        sandbox_id: str,
    ) -> Tuple[bool, Optional[SandboxResult]]:
        """
        尝试恢复（带重试）

        Returns:
            (success, result) 元组
        """
        ...

    def load_snapshot(self, snapshot_id: str) -> Optional[SubagentStateSnapshot]:
        """加载快照（恢复时使用）"""
        ...

    def list_active_records(self) -> List[InterruptionRecord]:
        """列出所有活跃中断记录"""
        ...

    def get_history(self, sandbox_id: Optional[str] = None) -> List[InterruptionRecord]:
        """获取恢复历史（可按 sandbox_id 过滤）"""
        ...

    def cleanup_records(self, sandbox_id: str) -> int:
        """清理 sandbox 的所有记录（sandbox 清理时调用）"""
        ...
```

### 3.3 SubagentSandbox 集成

**新增 3 个方法 + 4 个 SandboxContext 字段**：

```python
class SubagentSandbox:
    def __init__(
        self,
        ...,
        recovery_manager: Optional["InterruptionRecoveryManager"] = None,  # Phase 9 新增
    ):
        ...

    # 新增方法
    def pause(self, sandbox_id: str, reason: str = "user_request") -> bool:
        """
        暂停正在执行的 subagent

        行为：
        1. 设置 sandbox.pause_event
        2. executor 内部循环检测到事件 → 抛 PauseRequest
        3. 返回 True

        注意：python 的 GIL 限制，pause 不可能瞬间生效；executor 必须配合检查
        """
        ...

    def resume(self, sandbox_id: str, snapshot_id: Optional[str] = None) -> bool:
        """
        恢复暂停的 subagent

        行为：
        1. 清除 pause_event
        2. 如果有 snapshot_id → 调用 executor(sandbox_ctx, snapshot)
        3. 否则 → 重新调用原 executor
        """
        ...

    def cancel(self, sandbox_id: str, reason: str = "user_request") -> bool:
        """
        主动取消 subagent

        行为：
        1. 设置 sandbox.cancel_event
        2. executor 检测到事件 → 抛 UserAbort
        3. sandbox.execute() 捕获并返回 CANCELLED 状态
        """
        ...
```

**SandboxContext 新增 4 字段**：

```python
@dataclass
class SandboxContext:
    # ... 既有字段 ...
    pause_event: threading.Event = field(default_factory=threading.Event)  # 暂停信号
    cancel_event: threading.Event = field(default_factory=threading.Event)  # 取消信号
    snapshot: Optional["SubagentStateSnapshot"] = None                      # 当前快照
    intermediate_results: Dict[str, Any] = field(default_factory=dict)      # 中间结果
```

**`execute()` 改造**：

```python
def execute(self, sandbox_id, executor, ...):
    ...
    try:
        # 检查是否需要从快照恢复
        snapshot = None
        if self._recovery_manager and sandbox_ctx.snapshot:
            snapshot = self._recovery_manager.load_snapshot(sandbox_ctx.snapshot.snapshot_id)

        # 调用 executor（支持 pause/cancel 轮询）
        def wrapped_executor(ctx):
            # 轮询 pause / cancel
            while True:
                if ctx.cancel_event.is_set():
                    raise UserAbort("user_abort")
                if ctx.pause_event.is_set():
                    # 暂停时挂起
                    ctx.pause_event.wait()  # 阻塞直到 resume
                try:
                    if snapshot:
                        return executor(ctx, snapshot)
                    return executor(ctx)
                except PauseRequest:
                    # 用户主动暂停
                    ctx.pause_event.wait()
                    continue  # 恢复后重试
                except _RetryableError as e:
                    # 触发恢复
                    if self._recovery_manager:
                        record = self._recovery_manager.record_interruption(
                            sandbox_id=sandbox_id,
                            agent_id=ctx.agent_id,
                            interruption_type=InterruptionType.EXCEPTION,
                            error=str(e),
                            task=ctx.__dict__,  # 简化
                        )
                        # 根据策略决定是否重试
                        if record.strategy == RecoveryStrategy.RETRY:
                            time.sleep(self._recovery_manager.retry_policy.compute_delay_ms(record.attempts) / 1000)
                            continue
                    raise

        output = wrapped_executor(sandbox_ctx)
        ...
```

**完全向后兼容**：
- `recovery_manager=None` → 行为与 Phase 8 完全一致
- executor 签名兼容：`(ctx) -> Any` 或 `(ctx, snapshot) -> Any` 均可

### 3.4 集成点

#### 3.4.1 与 `pattern_executor._dispatch_subagent` 集成

```python
# pattern_executor.py (Phase 9 新增代码，不修改 V2 路径)
def _dispatch_subagent(...):
    if sandbox is None and router is None and budget_guard is None and recovery_manager is None:
        return _safe_dispatch(...)  # 完全兼容

    # 调用 sandbox.spawn
    sandbox_id = sandbox.spawn(...)
    try:
        result = sandbox.execute(sandbox_id, executor_fn)
    except ...:
        # 触发恢复
        if recovery_manager:
            record = recovery_manager.record_interruption(
                sandbox_id=sandbox_id,
                agent_id=agent_type,
                interruption_type=InterruptionType.EXCEPTION,
                error=str(e),
            )
            recovered, recovered_result = recovery_manager.attempt_recovery(
                record=record,
                executor=executor_fn,
                sandbox_id=sandbox_id,
            )
            if recovered:
                result = recovered_result
        ...
```

**`pattern_executor` 新增 `recovery_manager` 参数**（不破坏 V2，向后兼容）。

#### 3.4.2 与 V2 `CheckpointManager` 集成

复用 V2 的 `save_checkpoint` / `load_checkpoint` 用于深恢复：

```python
# interruption_recovery.py
def _save_checkpoint(self, snapshot: SubagentStateSnapshot) -> Optional[str]:
    """将 snapshot 关联到 V2 checkpoint（深恢复）"""
    if self.checkpoint_manager is None:
        return None
    checkpoint = Checkpoint(
        checkpoint_id=f"cp_snapshot_{snapshot.snapshot_id}",
        task_id=snapshot.agent_id,
        step_id="interruption_recovery",
        step_name=f"Snapshot for {snapshot.sandbox_id}",
        agent_id=snapshot.agent_id,
        status=CheckpointStatus.ACTIVE,
        progress_percentage=snapshot.progress,
        context_snapshot=snapshot.to_dict(),
    )
    if self.checkpoint_manager.save_checkpoint(checkpoint):
        return checkpoint.checkpoint_id
    return None
```

**CheckpointManager 不修改**（V2 文件），只调用。

#### 3.4.3 与 `PerformanceFingerprint` 集成

```python
# interruption_recovery.py
def _record_to_fingerprint(self, record: InterruptionRecord, snapshot: Optional[SubagentStateSnapshot]):
    """记录中断/恢复事件到画像"""
    if self.fingerprint is None:
        return
    try:
        self.fingerprint.record(
            pattern_id="interruption_recovery",
            success=(record.recovered_at is not None),
            context_features={
                "event_type": "interruption_recovery",
                "sandbox_id": record.sandbox_id,
                "agent_id": record.agent_id,
                "interruption_type": record.interruption_type.value,
                "strategy": record.strategy.value,
                "attempts": record.attempts,
                "max_attempts": record.max_attempts,
                "snapshot_id": record.snapshot_id,
            },
            strategy=record.strategy.value,
        )
    except Exception as e:
        logger.debug(f"画像写入失败（已忽略）：{e}")
```

---

## 四、注入流程设计

### 4.1 主流程时序

```
subagent 启动 → executor 循环执行
                  │
                  ▼
        每次循环检查 pause_event / cancel_event
                  │
       ┌──────────┴──────────┐
       ▼                     ▼
  cancel_event?         pause_event?
       │                     │
       ▼                     ▼
  抛 UserAbort          抛 PauseRequest
  终止 sandbox          executor.wait()
       │                     │
       ▼                     ▼
  业务异常/超时            恢复后继续
       │
       ▼
  _dispatch_subagent 捕获
       │
       ▼
  InterruptionRecoveryManager.record_interruption
       │
       ├─→ 智能选择 strategy（task.interruption_policy 优先）
       ├─→ 创建 InterruptionRecord
       └─→ 写入 fingerprint
       │
       ▼
  attempt_recovery(record, executor, sandbox_id)
       │
       ├─→ 加载 snapshot（如果有）
       ├─→ 计算退避延迟
       ├─→ sleep(delay_ms / 1000)
       ├─→ 调用 executor(ctx, snapshot) 重新执行
       │
       ├─→ 成功 → 标记 record.recovered_at，返回 (True, result)
       └─→ 失败 → 增加 attempts，超限升级 strategy，重试
```

### 4.2 智能策略选择算法

```python
def _select_strategy(
    self,
    interruption_type: InterruptionType,
    attempt: int,
    task: Optional[Dict[str, Any]] = None,
) -> RecoveryStrategy:
    """
    智能策略选择

    优先级：
    1. task["interruption_policy"]["strategy"] - 最高优先
    2. task["interruption_policy"][interruption_type.value] - 按类型指定
    3. 基于类型的默认策略
    4. 升级策略（attempt 超限时）
    """
    policy = (task or {}).get("interruption_policy", {})

    # 优先级 1：显式 strategy
    if isinstance(policy, dict) and "strategy" in policy:
        return RecoveryStrategy(policy["strategy"])

    # 优先级 2：按类型指定
    if isinstance(policy, dict) and interruption_type.value in policy:
        return RecoveryStrategy(policy[interruption_type.value])

    # 优先级 3：默认
    default_map = {
        InterruptionType.TIMEOUT: RecoveryStrategy.RETRY,
        InterruptionType.EXCEPTION: RecoveryStrategy.RETRY,
        InterruptionType.SIGNAL: RecoveryStrategy.RESTART,
        InterruptionType.RESOURCE_EXHAUSTED: RecoveryStrategy.FALLBACK,
        InterruptionType.USER_ABORT: RecoveryStrategy.SKIP,
        InterruptionType.UNKNOWN: RecoveryStrategy.MANUAL,
    }
    strategy = default_map.get(interruption_type, RecoveryStrategy.MANUAL)

    # 优先级 4：升级策略
    if attempt >= self.retry_policy.max_retries:
        escalation_map = {
            RecoveryStrategy.RETRY: RecoveryStrategy.FALLBACK,
            RecoveryStrategy.RESTART: RecoveryStrategy.SKIP,
            RecoveryStrategy.FALLBACK: RecoveryStrategy.MANUAL,
        }
        strategy = escalation_map.get(strategy, RecoveryStrategy.MANUAL)

    return strategy
```

### 4.3 task 字典新增字段

```python
{
    # ... 既有字段 ...
    "interruption_policy": {
        "strategy": "retry",                          # 全局策略
        "timeout": "retry",                            # 按类型覆盖
        "exception": "retry",
        "resource_exhausted": "fallback",
        "user_abort": "skip",
    },
    "interruption_max_retries": 5,                    # 覆盖全局 max_retries
    "interruption_initial_delay_ms": 500,             # 覆盖默认退避
}
```

---

## 五、边界与失败处理

### 5.1 暂停/恢复的边界

| 场景 | 行为 |
|------|------|
| executor 不配合检查 pause_event | pause 无效（已知限制，文档化） |
| resume 时 sandbox 已被 cleanup | 抛 SandboxNotFoundError |
| pause 后 30 分钟未恢复 | 自动 resume + 记录警告 |
| 多次 pause | 幂等（重复 pause 等于 1 次） |
| 多次 resume | 幂等（重复 resume 无副作用） |

### 5.2 取消的边界

| 场景 | 行为 |
|------|------|
| executor 忽略 cancel_event | cancel 失败（已知限制） |
| cancel 时 executor 已结束 | 静默忽略（已无效果） |
| cancel 后 cleanup | 正常清理 |
| 多次 cancel | 幂等 |

### 5.3 重试退避边界

| 场景 | 行为 |
|------|------|
| 第一次重试（attempt=0） | 延迟 = initial_delay_ms（1000ms） |
| 第二次重试（attempt=1） | 延迟 = 1000 × 2.0 = 2000ms |
| 第三次重试（attempt=2） | 延迟 = 1000 × 4.0 = 4000ms |
| 第 N 次超 max_delay_ms | 截断到 max_delay_ms |
| jitter=True | 实际 = delay × (1 + 0~0.25) |
| 重试次数 > max_retries | 升级到 FALLBACK / SKIP / MANUAL |

### 5.4 状态快照边界

| 场景 | 行为 |
|------|------|
| snapshot 不可 JSON 序列化 | 抛 TypeError，recovery 降级为无状态 |
| snapshot 超过 10MB | 警告 + 截断到 10MB |
| snapshot 引用已 cleanup 的 sandbox | 标记为 orphaned + 跳过 |
| snapshot_id 不存在 | 返回 None（视为首次执行） |

### 5.5 恢复失败的边界

| 场景 | 行为 |
|------|------|
| recovery_manager 自身异常 | 隔离到 try/except，记录 warning |
| 重试 N 次后仍失败 | 返回 (False, last_error) |
| 用户指定 MANUAL 策略 | 返回待决策状态（不自动恢复） |
| 整体 workflow 因 SKIP 失败 | 标记 failure，workflow 可继续 |

---

## 六、测试用例设计

### 6.1 单元测试（13 个 case）

#### 6.1.1 TestInterruptionType（2 个）

| # | 用例 | 断言 |
|---|------|------|
| 1 | 5 种中断类型枚举值 | 值符合 spec（timeout/exception/signal/resource_exhausted/user_abort/unknown） |
| 2 | InterruptionType 字符串解析 | `InterruptionType("timeout") == InterruptionType.TIMEOUT` |

#### 6.1.2 TestRecoveryStrategy（2 个）

| # | 用例 | 断言 |
|---|------|------|
| 3 | 5 种恢复策略枚举值 | 值符合 spec |
| 4 | RecoveryStrategy 默认值 | default = RETRY |

#### 6.1.3 TestRetryPolicy（4 个）

| # | 用例 | 断言 |
|---|------|------|
| 5 | 默认 RetryPolicy 参数 | max_retries=3, initial_delay_ms=1000, backoff=2.0, jitter=True |
| 6 | compute_delay_ms 指数退避 | attempt=0→1000, attempt=1→2000, attempt=2→4000 |
| 7 | compute_delay_ms 截断到 max_delay | 超限后等于 max_delay_ms |
| 8 | should_retry 决策 | attempt<max 且 type in retry_on → True |

#### 6.1.4 TestSubagentStateSnapshot（3 个）

| # | 用例 | 断言 |
|---|------|------|
| 9 | to_dict 序列化 | 包含所有字段 |
| 10 | from_dict 反序列化 | 字段完整还原 |
| 11 | 默认 factory | created_at / updated_at 自动填充 |

#### 6.1.5 TestInterruptionRecord（2 个）

| # | 用例 | 断言 |
|---|------|------|
| 12 | to_dict 序列化 | 包含所有字段（含 enum 字符串） |
| 13 | 默认 factory | created_at 自动填充 |

### 6.2 集成测试（8 个）

#### 6.2.1 TestInterruptionRecoveryManagerCore（5 个）

| # | 用例 | 断言 |
|---|------|------|
| 14 | save_snapshot + load_snapshot | snapshot 内容一致 |
| 15 | record_interruption 默认策略 | TIMEOUT → RETRY |
| 16 | record_interruption 升级 | attempt 超限 → FALLBACK |
| 17 | task.interruption_policy 覆盖默认 | task 字段优先生效 |
| 18 | list_active_records + get_history | 记录可查询 |

#### 6.2.2 TestAttemptRecovery（3 个）

| # | 用例 | 断言 |
|---|------|------|
| 19 | 首次重试成功 | attempts=1, recovered_at 不为空 |
| 20 | 重试 N 次后仍失败 | 返回 (False, last_error) |
| 21 | FALLBACK 策略调用低资源 executor | 触发 fallback 路径 |

### 6.3 Sandbox 集成测试（4 个）

#### 6.3.1 TestSandboxPauseResumeCancel（4 个）

| # | 用例 | 断言 |
|---|------|------|
| 22 | pause + executor 检查 → 挂起 | pause_event.is_set() 为 True |
| 23 | resume → executor 继续 | pause_event.clear() 后 executor 返回 |
| 24 | cancel → UserAbort | SandboxResult.status == CANCELLED |
| 25 | sandbox 集成 recovery_manager | 异常时自动重试 |

### 6.4 端到端故障注入（3 个）

#### 6.4.1 TestEndToEndFailureInjection（3 个）

| # | 用例 | 断言 |
|---|------|------|
| 26 | 注入 TimeoutError → RETRY → 成功 | 第 2 次调用成功 |
| 27 | 注入连续 3 次失败 → 升级到 FALLBACK | 第 4 次 fallback |
| 28 | USER_ABORT → SKIP | status == SKIPPED |

### 6.5 性能 Benchmark（2 个）

#### 6.5.1 TestPerformance（2 个）

| # | 用例 | 性能指标 |
|---|------|---------|
| 29 | 1000 次 record_interruption | < 1s（< 1ms/次） |
| 30 | 1000 次 save_snapshot + load_snapshot | < 500ms（< 0.5ms/次） |

### 6.6 测试总计

- 单元测试 13 个
- 集成测试 8 个
- Sandbox 集成 4 个
- 端到端 3 个
- 性能 2 个
- **合计 30 cases**

### 6.7 测试文件结构

```
scripts/tests/
├── test_interruption_recovery.py            # 全部 30 tests（单文件足够）
└── scripts/
    └── run_interruption_recovery_tests.sh   # 入口（可选）
```

**集成到**：`scripts/tests/scripts/run_dynamic_workflow_tests.sh` 新增 Phase 9 段。

---

## 七、配置项设计

### 7.1 SubagentSandbox 新增参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `recovery_manager` | `Optional[InterruptionRecoveryManager]` | `None` | 中断恢复管理器 |

### 7.2 task 字典新增字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `interruption_policy` | `Dict[str, str]` | `{}` | 中断策略表 |
| `interruption_max_retries` | `int` | 3 | 最大重试次数 |
| `interruption_initial_delay_ms` | `int` | 1000 | 初始延迟 |
| `interruption_backoff_factor` | `float` | 2.0 | 退避因子 |
| `interruption_max_delay_ms` | `int` | 30000 | 最大延迟 |
| `interruption_jitter` | `bool` | `True` | 是否加抖动 |

### 7.3 RetryPolicy 默认值

```python
RetryPolicy(
    max_retries=3,
    initial_delay_ms=1000,
    backoff_factor=2.0,
    max_delay_ms=30000,
    jitter=True,
    retry_on=(TIMEOUT, EXCEPTION, RESOURCE_EXHAUSTED),
)
```

### 7.4 安全默认值

| 参数 | 安全默认值 | 理由 |
|------|----------|------|
| `recovery_manager` | `None` | 不自动启用；显式开启 |
| `max_retries` | 3 | 避免无限重试 |
| `max_delay_ms` | 30000 | 单次最大等待 30s |
| `pause_event` | 默认 clear | 不主动暂停 |
| `cancel_event` | 默认 clear | 不主动取消 |
| 重试 on type | `(TIMEOUT, EXCEPTION, RESOURCE)` | 不重试 USER_ABORT / SIGNAL（避免无意义重试） |

### 7.5 配置示例

```python
# 最小化配置（禁用恢复）
sandbox = SubagentSandbox()

# 启用默认恢复
sandbox = SubagentSandbox(
    recovery_manager=InterruptionRecoveryManager(),
)

# 自定义重试策略
sandbox = SubagentSandbox(
    recovery_manager=InterruptionRecoveryManager(
        retry_policy=RetryPolicy(
            max_retries=5,
            initial_delay_ms=500,
            backoff_factor=1.5,
        ),
        checkpoint_manager=CheckpointManager("./recovery_checkpoints"),
        fingerprint=PerformanceFingerprint(),
    ),
)

# 任务级覆盖
sandbox.spawn(
    agent_id="sa_001",
    task={
        "description": "...",
        "interruption_policy": {
            "strategy": "retry",
            "timeout": "retry",
            "resource_exhausted": "fallback",
        },
        "interruption_max_retries": 5,
    },
)
```

---

## 八、风险评估

### 8.1 与 V2 不修改约束的冲突点

| 风险点 | 是否冲突 | 缓解策略 |
|--------|---------|---------|
| SubagentSandbox 修改 | **不冲突** | SubagentSandbox 是 Phase 2 模块，非 V2 |
| CheckpointManager 复用 | **不冲突** | CheckpointManager 是 V2 模块，Phase 9 只调用，不修改 |
| PerformanceFingerprint 复用 | **不冲突** | PF 是 V2.5 模块，Phase 9 只调用 |
| workflow_engine_v2 | **不冲突** | Phase 9 不修改 V2 引擎 |
| pattern_executor 修改 | **不冲突** | pattern_executor 是 Phase 1 模块；新增参数向后兼容 |

**V2 文件 diff 校验**（CI 必做）：

```bash
git diff scripts/workflow_engine_v2.py \
        scripts/cybernetics_bridge.py \
        scripts/guard_coordinator.py \
        scripts/agent_loop_controller_v2.py \
        scripts/checkpoint_manager.py
# 预期输出为空
```

### 8.2 并发与竞态

| 风险 | 缓解 |
|------|------|
| 多个线程同时 pause/resume 同一 sandbox | `_lock` 保护 + Event 原子操作 |
| 恢复时 sandbox 已被清理 | 检查 sandbox 是否仍活跃 |
| snapshot 中间结果被并发修改 | executor_state 必须是不可变快照 |
| recovery_manager 自身异常 | try/except 隔离到 record_interruption |

### 8.3 性能开销

| 阶段 | 开销 | 触发条件 |
|------|------|---------|
| record_interruption | < 1ms | 中断时 |
| save_snapshot | < 5ms（10KB 内） | executor 主动调用 |
| load_snapshot | < 2ms（10KB 内） | 恢复时 |
| 退避 sleep | 1000-30000ms | 重试间 |
| 策略选择 | < 0.1ms | 总是 |

**性能基线**（与 Phase 8 对比）：
- Phase 8 `spawn()` 无恢复：< 10ms
- Phase 9 `spawn()` 无恢复：< 10ms（**零开销**）
- Phase 9 中断 + 恢复：1000-30000ms（按 retry policy 退避）

### 8.4 向后兼容性

| 场景 | Phase 8 行为 | Phase 9 行为 | 兼容性 |
|------|-------------|-------------|--------|
| 不传 `recovery_manager` | 正常工作 | 正常工作（异常由 sandbox 内部 try/except 隔离） | ✅ 完全兼容 |
| 传 `recovery_manager` + task 不含 `interruption_policy` | - | 使用默认策略（按 type 智能选择） | 🆕 新能力 |
| 现有 Phase 1-8 测试 | 全部通过 | 全部通过 | ✅ 零回归 |
| executor 签名 `def executor(ctx)` | 正常工作 | 正常工作（snapshot=None 路径） | ✅ 兼容 |
| executor 签名 `def executor(ctx, snapshot)` | 报错 | 正常工作 | 🆕 新能力（可选） |

### 8.5 其他风险

| 风险 | 等级 | 缓解 |
|------|------|------|
| pause 不生效（executor 不配合） | 中 | 文档化要求；提供 `check_pause` 工具方法 |
| recovery 进入死循环 | 中 | max_retries 上限 |
| snapshot 包含敏感数据 | 低 | 序列化时脱敏（密码/Token） |
| 退避抖动导致总时间不可预测 | 低 | 文档化最长总时间 = sum(max_delay × 1.25 × max_retries) |
| 恢复后 subagent 行为与首次不同 | 中 | 提供 snapshot_diff 工具方法 |

---

## 九、实施步骤

### Step 1：数据模型 + 枚举（预计 0.5 天）

**产出**：
- `scripts/dynamic_workflow/interruption_recovery.py`（核心模块）
  - `InterruptionType` 枚举
  - `RecoveryStrategy` 枚举
  - `RetryPolicy` 数据类（含 `compute_delay_ms` / `should_retry`）
  - `SubagentStateSnapshot` 数据类（含 `to_dict` / `from_dict`）
  - `InterruptionRecord` 数据类（含 `to_dict`）

**单元测试**：
- `test_interruption_recovery.py`（13 cases）

### Step 2：InterruptionRecoveryManager（预计 1 天）

**完成项**：
- `__init__` + 状态字段
- `save_snapshot` / `load_snapshot` / `delete_snapshot`
- `record_interruption`（含 `_select_strategy` 智能策略选择）
- `attempt_recovery`（含重试循环 + 退避）
- `list_active_records` / `get_history` / `cleanup_records`
- V2 CheckpointManager 集成（`_save_checkpoint` / `_load_checkpoint`）
- PerformanceFingerprint 集成

**集成测试**：
- `test_interruption_recovery.py` 新增 8 cases

### Step 3：SubagentSandbox 集成（预计 0.5 天）

**修改文件**：
- `scripts/dynamic_workflow/subagent_sandbox.py`
  - `__init__` 新增 1 参数 `recovery_manager`
  - `SandboxContext` 新增 4 字段
  - `execute()` 改造：支持 pause/cancel 轮询 + 触发恢复
  - 新增 `pause()` / `resume()` / `cancel()` 3 个方法
  - `_record_to_fingerprint` 新增 interruption_recovery 事件

**集成测试**：
- `test_interruption_recovery.py` 新增 4 cases

### Step 4：端到端故障注入（预计 0.5 天）

**完成项**：
- 构造可控故障（注入 TimeoutError / ValueError / ResourceExhausted）
- 验证恢复策略自动选择
- 验证多次失败后的升级
- 验证 USER_ABORT → SKIP 路径

**测试**：
- `test_interruption_recovery.py` 新增 3 cases

### Step 5：性能 Benchmark（预计 0.25 天）

**完成项**：
- 1000 次 record_interruption 吞吐量
- 1000 次 snapshot 序列化/反序列化

**测试**：
- `test_interruption_recovery.py` 新增 2 cases

### Step 6：文档 + 收官报告（预计 0.25 天）

**更新文档**：
1. `DYNAMIC_WORKFLOWS_INTEGRATION.md` v1.5 → v1.6
   - 新增 §Phase 9 实施详情
2. `PHASE9_FINAL_REPORT.md`（本计划的实施结果）

**测试入口**：
- `scripts/tests/scripts/run_dynamic_workflow_tests.sh` 集成 Phase 9 测试

**总计**：3 天（实际可能 2-4 天）

---

## 十、交付清单

| # | 产物 | 路径 | 状态 |
|---|------|------|------|
| 1 | `InterruptionRecoveryManager` 主调度器 | `scripts/dynamic_workflow/interruption_recovery.py` | 待实施 |
| 2 | 6 大核心组件（枚举 + 数据类 + Manager） | `scripts/dynamic_workflow/interruption_recovery.py` | 待实施 |
| 3 | `SubagentSandbox` 集成 | `scripts/dynamic_workflow/subagent_sandbox.py`（修改） | 待实施 |
| 4 | `SandboxContext` 新增 4 字段 | `scripts/dynamic_workflow/subagent_sandbox.py`（修改） | 待实施 |
| 5 | 30 个测试 | `scripts/tests/test_interruption_recovery.py` | 待实施 |
| 6 | 测试入口集成 | `scripts/tests/scripts/run_dynamic_workflow_tests.sh`（修改） | 待实施 |
| 7 | `DYNAMIC_WORKFLOWS_INTEGRATION.md` v1.6 | `docs/dev/DYNAMIC_WORKFLOWS_INTEGRATION.md`（修改） | 待实施 |
| 8 | Phase 9 收官报告 | `docs/dev/PHASE9_FINAL_REPORT.md` | 待实施 |
| 9 | Phase 9 实施计划（本文件） | `docs/dev/PHASE9_PLAN.md` | ✅ 已完成 |

**预期测试增量**：30 tests
**全量测试预期**：634 + 30 = **664 tests**

---

## 十一、验收清单

- [ ] `InterruptionType` / `RecoveryStrategy` / `RetryPolicy` / `SubagentStateSnapshot` / `InterruptionRecord` 全部实现
- [ ] `InterruptionRecoveryManager` 实现 5 项核心 API
- [ ] 智能策略选择算法（按 type 智能 + task 字段覆盖 + 升级机制）
- [ ] 指数退避 + 抖动退避算法
- [ ] V2 `CheckpointManager` 集成（深恢复）
- [ ] `PerformanceFingerprint` 联动（interruption_recovery 事件）
- [ ] `SubagentSandbox` 集成 `recovery_manager` 参数
- [ ] `SandboxContext` 新增 pause/cancel/snapshot/intermediate_results 4 字段
- [ ] `SubagentSandbox` 新增 `pause()` / `resume()` / `cancel()` 3 个方法
- [ ] `execute()` 支持 pause/cancel 轮询 + 自动恢复
- [ ] 30 个 Phase 9 测试 100% 通过
- [ ] Phase 1-8 回归测试零失败（634 tests）
- [ ] V2 回归测试零失败（85 tests）
- [ ] V2 文件零修改（`git diff` 为空）
- [ ] 向后兼容：不传 `recovery_manager` 行为零变化
- [ ] 性能基线：1000 次 record < 1s
- [ ] 安全：max_retries 强制上限；退避 jitter 避免雪崩
- [ ] TODO/FIXME 0 处遗留
- [ ] 编译警告 0 处
- [ ] 文档更新：DYNAMIC_WORKFLOWS_INTEGRATION.md v1.6 + PHASE9_FINAL_REPORT.md

---

## 十二、回滚策略

如 Phase 9 出现问题：

1. 恢复 `subagent_sandbox.py` 的修改
2. 删除新增模块文件（`interruption_recovery.py`）
3. 删除新增测试文件（`test_interruption_recovery.py`）
4. 不影响 Phase 1-8 任何代码
5. CheckpointManager / PerformanceFingerprint / WorktreeManager 零修改

**回滚时间估算**：< 20 分钟

---

## 十三、不在 Phase 9 范围（明确排除）

| 功能 | 排除理由 | 建议 Phase |
|------|---------|-----------|
| `/loop + /goal` 集成 | 终端用户命令集成；超出 Phase 9 范围 | Phase 10 |
| model_tier-aware dispatch | cybernetics_bridge 解析；与 Phase 9 解耦 | Phase 10 |
| SkillDistribution 增强 | 在 Phase 8 基础上扩展 | Phase 11 |
| 分布式中断恢复 | 需要多机协调；超出单进程架构 | Phase 12+ |
| AI 智能中断预测 | 需要 ML 模型；超出当前架构 | Phase 13+ |
| 断点续跑（保存 executor 中间状态到文件） | 需要 executor 主动配合 | Phase 14+ |

---

*下一步：用户确认 → 启动 Phase 9 实施（3 天）*
