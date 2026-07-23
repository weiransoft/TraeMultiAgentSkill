# HostLLM 桥接协议设计文档

> **版本**: v2.8.4 (修订版 R1 — 架构师审查反馈修复)
> **日期**: 2026-07-22
> **状态**: 已修订

## 1. 问题背景

### 1.1 现状

`trae_agent_dispatch_v2.py` 的 autonomous 模式通过 `ClaudeCodeSubAgentAdapter` 调用 SubAgent。在 Trae IDE 环境中：
- 无 `claude` 命令行工具 → `_build_claude_command()` 返回 `None`
- `_fallback_no_subagent()` 返回 `success=False`（v2.7.1 诚实降级）
- autonomous 循环的 handler 收到 `False` → 返回 `StageResult(kind="retriable")`
- 循环无限重试，永远失败

### 1.2 需求

在 Trae 智能助手对话中，自动调用 Trae 宿主 LLM 的 Task 工具来执行 SubAgent。真实子代理由宿主 LLM（即 Trae 的 AI 助手）通过 Task 工具调度执行，脚本层负责生成调度请求和接收执行结果。

## 2. 方案设计

### 2.1 核心思路：协议文件 + 独立标记文件 + 非阻塞轮询

```
宿主 LLM (Trae 对话)
    │
    ├─1─> RunCommand(blocking=false, PYTHONUNBUFFERED=1):
    │     python3 -u scripts/trae_agent_dispatch_v2.py --autonomous ...
    │
    │     [脚本内部 — autonomous 循环]
    │     ├─> invoke_agent → _invoke_via_host_llm
    │     ├─> HostLLMBridge.create_request() → 写入 request_{id}.json + prompt
    │     ├─> 写入 protocol.marker 文件（独立通道，不与 stdout 日志混杂）
    │     ├─> HostLLMBridge.wait_for_response(id, timeout) → 轮询 response_{id}.json
    │     │
    │     │     [宿主 LLM — 并行执行]
    │     │     ├─2─> 每隔 5 秒读取 protocol.marker 文件（独立于 CheckCommandStatus）
    │     │     ├─3─> 解析 marker → 获取 request_id + request_file
    │     │     ├─4─> 读取 request_{id}.json 获取完整 prompt
    │     │     ├─5─> Task(subagent_type, query=task) → 执行子代理
    │     │     ├─6─> HostLLMBridge.write_response(id, result) → 原子写入 response_{id}.json
    │     │     ├─7─> 清除 protocol.marker 文件
    │     │     │
    │     │     [脚本继续]
    │     │     ├─8─> 检测到 response_{id}.json → 读取结果（含 JSON 容错）
    │     │     ├─9─> 返回 invoke_agent 结果
    │     └─10─> autonomous 循环继续下一步
    │
    └─11─> CheckCommandStatus → 脚本完成
```

### 2.2 死锁/活锁分析

**场景 1: protocol.marker 丢失** → 不会发生。marker 写入独立文件，不受 stdout 缓冲/截断影响。宿主 LLM 直接读文件，不依赖 CheckCommandStatus 的 stdout 窗口。

**场景 2: 宿主 LLM 不轮询** → 超时后返回 `success=False`。连续 2 次相同超时 → 升级 fatal（见 §6.2 熔断机制），终止循环。

**场景 3: stdout 缓冲** → protocol.marker 是文件写入，与 stdout 无关。stdout 仅用于日志，不承载协议。

**场景 4: Task 工具同步阻塞** → 宿主 LLM 在 Task 执行期间不需要 CheckCommandStatus。Task 完成后写入 response 文件即可。

### 2.3 文件布局

```
logs/host_llm_bridge/
├── protocol.marker               # 当前待处理请求的标记文件（单文件，覆盖写）
├── request_{request_id}.json     # 调度请求元数据（脚本生成）
├── request_{request_id}.prompt   # 完整提示词（脚本生成，供宿主 LLM 读取）
└── response_{request_id}.json    # 调度结果（宿主 LLM 生成，原子写入）
```

**protocol.marker 格式**（单行 JSON，覆盖写）：
```json
{"request_id":"20260722_153000_abc123","agent_type":"architect","task":"设计系统架构","request_file":"/path/to/request_xxx.json","timeout_seconds":600}
```

## 3. 详细设计

### 3.1 新增模块: `scripts/host_llm_bridge.py`

```python
class HostLLMBridge:
    """宿主 LLM 桥接器：通过文件协议与 Trae 宿主 LLM 通信

    通信通道：
    - protocol.marker 文件：脚本写入，宿主 LLM 读取（独立于 stdout，避免日志混杂）
    - request_{id}.json 文件：脚本写入请求元数据
    - request_{id}.prompt 文件：脚本写入完整提示词
    - response_{id}.json 文件：宿主 LLM 写入结果（原子写入：tempfile + os.replace）
    """

    # 默认超时秒数（架构师审查 P4.2：从 300s 提升到 600s）
    DEFAULT_TIMEOUT = 600
    # 轮询间隔（秒）
    POLL_INTERVAL = 0.5
    # response JSON 解析失败重试次数（架构师审查 P3.2）
    MAX_JSON_RETRIES = 3
    # JSON 解析失败重试间隔（秒）
    JSON_RETRY_INTERVAL = 0.1

    def __init__(self, bridge_dir: str = None):
        """初始化桥接目录，默认 skill_root/logs/host_llm_bridge/"""

    def create_request(self, agent_type: str, task: str,
                       context: dict, prompt: str,
                       timeout_seconds: int = None) -> str:
        """创建调度请求

        1. 生成 request_id（时间戳 + UUID 短格式）
        2. 写入 request_{id}.json（元数据）
        3. 写入 request_{id}.prompt（完整提示词）
        4. 写入 protocol.marker（单行 JSON，覆盖写）

        Returns:
            request_id 字符串
        """

    def wait_for_response(self, request_id: str,
                          timeout: int = None) -> dict:
        """轮询等待结果文件

        1. 每 POLL_INTERVAL 秒检查 response_{id}.json 是否存在
        2. 文件存在后读取，捕获 JSONDecodeError 重试（最多 MAX_JSON_RETRIES 次）
        3. 超时返回 {'success': False, 'error': 'timeout', 'timeout': True}

        Returns:
            结果字典，包含 success/output/error/timeout 字段
        """

    @staticmethod
    def write_response(request_id: str, success: bool,
                       output: str, error: str = "",
                       bridge_dir: str = None) -> str:
        """写入结果文件（供宿主 LLM 调用）

        原子写入流程（架构师审查 P3.1）：
        1. 写入临时文件 response_{id}.json.tmp
        2. os.replace() 原子替换为 response_{id}.json
        3. 清除 protocol.marker 文件

        Returns:
            结果文件路径
        """

    @staticmethod
    def read_request(request_id: str,
                     bridge_dir: str = None) -> dict:
        """读取请求文件（供宿主 LLM 调用）

        安全校验（架构师审查 P7.2）：
        - 验证 request_id 格式（仅允许字母数字下划线）
        - 验证返回的 request_file 路径在 bridge_dir 下

        Returns:
            请求字典
        """

    @staticmethod
    def read_marker(bridge_dir: str = None) -> dict:
        """读取 protocol.marker 文件（供宿主 LLM 轮询调用）

        Returns:
            标记字典，无标记时返回 None
        """

    @staticmethod
    def clear_marker(bridge_dir: str = None) -> None:
        """清除 protocol.marker 文件（供宿主 LLM 处理完成后调用）"""

    @staticmethod
    def validate_request_id(request_id: str) -> bool:
        """验证 request_id 格式（安全防护）"""
```

### 3.2 修改 `claude_code_subagent_adapter.py`

```python
class ClaudeCodeSubAgentAdapter:
    def _detect_platform(self) -> str:
        """
        平台检测优先级（架构师审查 P5.1）：
        host_llm > claude_code > unknown

        Trae 环境优先检测：TRAE_ENV / TRAE_AGENT_PATH
        Claude Code 环境次之：CLAUDE_CODE_ENV / ANTHROPIC_ENV
        """
        # 优先检测 Trae 环境（host_llm 桥接）
        if os.environ.get('TRAE_ENV') or os.environ.get('TRAE_AGENT_PATH'):
            return 'host_llm'
        # Claude Code 环境
        if os.environ.get('CLAUDE_CODE_ENV') or os.environ.get('ANTHROPIC_ENV'):
            return 'claude_code'
        return 'unknown'

    def invoke_agent(self, agent_type, task, context=None):
        if self.platform == 'host_llm':
            return self._invoke_via_host_llm(agent_type, task, context)
        elif self.platform == 'claude_code':
            return self._invoke_via_claude_code(agent_type, task, context)
        else:
            return self._invoke_generic(agent_type, task, context)

    def _invoke_via_host_llm(self, agent_type, task, context):
        """通过宿主 LLM 桥接协议调用 SubAgent

        流程：
        1. 构建 prompt
        2. 创建调度请求（写入文件 + protocol.marker）
        3. 轮询等待结果文件
        4. 返回结果
        """
        from host_llm_bridge import HostLLMBridge

        bridge = HostLLMBridge()
        prompt = self._build_agent_prompt(agent_type, task, context)

        # 从 context 读取超时配置，默认 600 秒
        timeout = 600
        if context and isinstance(context, dict):
            timeout = context.get('timeout_seconds', 600)

        request_id = bridge.create_request(
            agent_type, task, context, prompt, timeout_seconds=timeout
        )
        result = bridge.wait_for_response(request_id, timeout=timeout)
        return {
            'success': result.get('success', False),
            'output': result.get('output', ''),
            'error': result.get('error', ''),
            'platform': 'host_llm',
            'request_id': request_id,
            'timed_out': result.get('timeout', False)
        }

    # _invoke_via_trae 标记为 deprecated（架构师审查 P5.3）
    def _invoke_via_trae(self, agent_type, task, context=None):
        """[DEPRECATED v2.8.4] 使用 _invoke_via_host_llm 替代"""
        import warnings
        warnings.warn(
            "_invoke_via_trae 已弃用，请使用 _invoke_via_host_llm",
            DeprecationWarning, stacklevel=2
        )
        return self._invoke_via_host_llm(agent_type, task, context)
```

### 3.3 修改 `autonomous/loop_controller.py` — 连续失败熔断

在 `_should_stop` 方法中增加连续 retriable 熔断逻辑（架构师审查 P4.1）：

```python
class WorkflowLoopController:
    def __init__(self, ...):
        # ...
        self._consecutive_retriable_count = 0
        self._last_retriable_reason = None

    def _should_stop(self, iter_result) -> bool:
        # 原有逻辑...

        # 新增：连续 retriable 熔断（架构师审查 P4.1）
        if iter_result.kind == 'retriable':
            reason = iter_result.reason or ''
            if reason == self._last_retriable_reason:
                self._consecutive_retriable_count += 1
                if self._consecutive_retriable_count >= 2:
                    log(f'❌ 连续 {self._consecutive_retriable_count} 次相同错误，升级为 fatal: {reason}', 'ERROR')
                    return True  # 终止循环
            else:
                self._consecutive_retriable_count = 1
                self._last_retriable_reason = reason
        else:
            self._consecutive_retriable_count = 0
            self._last_retriable_reason = None

        return False
```

### 3.4 修改 `dispatch/legacy.py` — 超时信息传递

`_dispatch_via_claude_code` 需将超时信息传递给 handler，使其能设置 `reason`：

```python
def _dispatch_via_claude_code(agent_type, task, task_id, project_root, progress, ponytail_prompt=""):
    # ...
    result = adapter.invoke_agent(agent_type, task, context)
    if result.get('success'):
        # 成功逻辑...
        return True
    else:
        # 失败逻辑：将失败原因传递给上层
        error_msg = result.get('error', '未知错误')
        timed_out = result.get('timed_out', False)
        if timed_out:
            error_msg = f'host_llm_timeout: {error_msg}'
        log(f'❌ SubAgent 调用失败：{error_msg}', 'ERROR')
        # 更新进度时传递 reason
        if actual_task_id:
            from trae_agent_dispatch import update_task_status
            update_task_status(progress, actual_task_id, '❌ 失败', error_msg, project_root)
        return False
```

### 3.5 修改 `autonomous/handlers/dev_handler.py` — reason 传递

```python
def do_handle(self, iter_ctx):
    # ...
    success = _dispatch_via_claude_code(...)
    if not success:
        return StageResult(
            kind='retriable',
            reason='dispatch_failed',  # 新增：统一 reason 标识
            message='SubAgent dispatch failed'
        )
    # ...
```

## 4. 宿主 LLM 集成指南（SKILL.md 新增章节）

### 4.1 宿主 LLM 轮询契约（架构师审查 P2.2 修复）

当通过 `RunCommand(blocking=false)` 运行 autonomous 脚本后，宿主 LLM 必须遵循以下轮询契约：

1. **启动后 2 秒**进行首次检查：读取 `logs/host_llm_bridge/protocol.marker` 文件
2. **之后每 5 秒**检查一次 `protocol.marker` 文件
3. **检查到 marker** 后：
   a. 读取 marker JSON 获取 `request_id` 和 `request_file`
   b. 读取 `request_file` 获取完整提示词
   c. 通过 Task 工具执行子代理
   d. 调用 `HostLLMBridge.write_response()` 写入结果
   e. 调用 `HostLLMBridge.clear_marker()` 清除标记
4. **marker 不存在**时：继续等待（脚本可能还在执行前序步骤）
5. **脚本结束**时（CheckCommandStatus 显示 done）：停止轮询

**重要**：protocol.marker 是独立文件，**不依赖 CheckCommandStatus 的 stdout 输出**。宿主 LLM 应使用 `Read` 工具直接读取文件。

### 4.2 写入结果文件（强制原子写入）

宿主 LLM **必须**使用 `HostLLMBridge.write_response()` 写入结果，**禁止**直接 `echo > response.json`：

```python
# 正确方式（原子写入）
from host_llm_bridge import HostLLMBridge
HostLLMBridge.write_response(
    request_id="20260722_153000_abc123",
    success=True,
    output="子代理执行结果...",
    bridge_dir="/path/to/logs/host_llm_bridge"
)

# 错误方式（非原子，禁止使用）
# echo '{"...":"..."}' > response_xxx.json
```

或通过命令行调用：
```bash
python3 -c "
from host_llm_bridge import HostLLMBridge
HostLLMBridge.write_response(
    request_id='20260722_153000_abc123',
    success=True,
    output='子代理执行结果...',
    bridge_dir='/path/to/logs/host_llm_bridge'
)
"
```

## 5. 测试设计

### 5.1 单元测试 (`tests/test_host_llm_bridge.py`)

| 测试用例 | 验证内容 |
|---------|---------|
| `test_create_request` | 请求文件生成、prompt 文件生成、marker 文件生成 |
| `test_write_response` | 原子写入（临时文件 → rename）、marker 清除 |
| `test_wait_for_response_timeout` | 超时机制正确触发，返回 timeout=True |
| `test_wait_for_response_success` | 正常接收结果，JSON 解析正确 |
| `test_format_stdout_marker` | marker JSON 格式正确（虽然不输出到 stdout，但格式仍需验证） |
| `test_read_request` | 请求文件读取正确 |
| `test_read_marker` | marker 文件读取正确 |
| `test_clear_marker` | marker 文件清除正确 |
| `test_concurrent_requests` | 并发请求 ID 不冲突（多线程创建） |
| `test_response_corrupted_json` | response 文件损坏时重试 3 次后返回错误 |
| `test_marker_amid_logs` | marker 文件不受 stdout 日志影响（独立文件验证） |
| `test_timeout_boundary` | 599s/600s/601s 超时边界条件 |
| `test_validate_request_id` | request_id 格式校验（拒绝路径遍历攻击） |
| `test_request_file_path_validation` | request_file 路径在 bridge_dir 下（拒绝越界读取） |

### 5.2 集成测试 (`tests/test_host_llm_bridge_integration.py`)

| 测试场景 | 验证内容 |
|---------|---------|
| 端到端正常流程 | create_request → 模拟宿主 write_response → wait_for_response 返回成功 |
| 端到端超时流程 | create_request → 不写入 response → wait_for_response 超时 |
| 端到端损坏 response | create_request → 写入损坏 JSON → wait_for_response 重试后返回错误 |
| 并发多请求 | 3 个并发请求，各自独立完成 |
| 熔断机制验证 | 连续 2 次相同超时 → loop_controller 终止 |

### 5.3 端到端测试（真实 RunCommand 链路）

| 测试场景 | 验证内容 |
|---------|---------|
| Trae 环境真实调度 | 设置 TRAE_ENV=1，运行脚本，宿主 LLM 通过文件协议响应 |
| Claude Code 环境不受影响 | 不设置 TRAE_ENV，走原有 claude_code 路径 |
| 独立终端不受影响 | 不设置任何环境变量，走 _fallback_no_subagent |

## 6. 风险评估与缓解

### 6.1 文件系统风险

| 风险 | 缓解措施 |
|------|---------|
| 脚本轮询占用 CPU | 间隔 0.5 秒轮询（time.sleep），非 busy-wait |
| 结果文件写入不完整 | **强制**使用 `HostLLMBridge.write_response`（tempfile + os.replace 原子写入） |
| response JSON 解析失败 | `wait_for_response` 捕获 JSONDecodeError，sleep 0.1s 重试，最多 3 次 |
| 并发请求 ID 冲突 | request_id = 时间戳 + UUID 短格式（8 字符） |
| bridge_dir 权限问题 | 默认使用 skill_root/logs/host_llm_bridge/，自动创建（parents=True） |

### 6.2 超时与熔断（架构师审查 P4.1 修复）

| 风险 | 缓解措施 |
|------|---------|
| 超时后无限重试 | 连续 2 次相同超时错误 → 升级 fatal，终止循环 |
| 600 秒超时不够 | 从 request 文件 `timeout_seconds` 字段读取，可自定义 |
| 宿主 LLM 不响应 | 第 1 次超时 retriable，第 2 次相同超时 fatal |

**熔断逻辑**：
- `_consecutive_retriable_count`：记录连续相同原因的 retriable 次数
- 阈值 2：连续 2 次相同错误即终止（避免 10 次 × 600 秒 = 100 分钟的浪费）
- reason 变化时重置计数器（不同错误不累计）

### 6.3 安全风险

| 风险 | 缓解措施 |
|------|---------|
| request_id 路径遍历 | `validate_request_id` 仅允许 `[a-zA-Z0-9_]` |
| request_file 越界读取 | `read_request` 校验路径在 bridge_dir 下 |
| 协议标记注入 | marker 使用 `json.dumps` 生成，禁止字符串拼接 |
| output 字段注入 | 脚本侧将 output 严格作为数据处理，不执行 |

### 6.4 兼容性

| 环境 | 行为 |
|------|------|
| Trae（TRAE_ENV 存在） | 走 `_invoke_via_host_llm` 桥接协议 |
| Claude Code（CLAUDE_CODE_ENV 存在） | 走 `_invoke_via_claude_code`，不受影响 |
| 独立终端（无环境变量） | 走 `_invoke_generic` → `_fallback_no_subagent`，不受影响 |
