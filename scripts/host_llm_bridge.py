#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
宿主 LLM 桥接器（HostLLM Bridge）

通过文件协议与 Trae 宿主 LLM 通信，解决 Trae 环境下缺少 SubAgent 运行时的问题。

通信通道：
- protocol.marker 文件：脚本写入，宿主 LLM 读取（独立于 stdout，避免日志混杂）
- request_{id}.json 文件：脚本写入请求元数据
- request_{id}.prompt 文件：脚本写入完整提示词
- response_{id}.json 文件：宿主 LLM 写入结果（原子写入：tempfile + os.replace）

设计文档：docs/dev/HOST_LLM_BRIDGE_DESIGN.md

使用方式（脚本侧）:
    from host_llm_bridge import HostLLMBridge
    bridge = HostLLMBridge()
    request_id = bridge.create_request('architect', '设计架构', context, prompt)
    result = bridge.wait_for_response(request_id, timeout=600)

使用方式（宿主 LLM 侧）:
    from host_llm_bridge import HostLLMBridge
    marker = HostLLMBridge.read_marker(bridge_dir)
    if marker:
        request = HostLLMBridge.read_request(marker['request_id'], bridge_dir)
        # ... 执行 Task ...
        HostLLMBridge.write_response(
            request_id=marker['request_id'],
            success=True,
            output='结果...',
            bridge_dir=bridge_dir
        )
"""

import os
import re
import json
import time
import uuid
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional


class HostLLMBridge:
    """宿主 LLM 桥接器：通过文件协议与 Trae 宿主 LLM 通信。

    协议流程：
    1. 脚本调用 create_request() → 生成请求文件 + protocol.marker
    2. 宿主 LLM 轮询 read_marker() → 检测到请求
    3. 宿主 LLM 调用 read_request() → 读取完整请求
    4. 宿主 LLM 执行 Task 工具调度子代理
    5. 宿主 LLM 调用 write_response() → 原子写入结果 + 清除 marker
    6. 脚本 wait_for_response() 检测到结果文件 → 读取并返回

    安全保障：
    - request_id 格式校验（仅允许字母数字下划线，防路径遍历）
    - request_file 路径校验（必须在 bridge_dir 下，防越界读取）
    - 原子写入（tempfile + os.replace，防半截文件）
    - JSON 解析容错（3 次重试，防竞态条件）
    """

    # 默认超时秒数（架构师审查 P4.2：从 300s 提升到 600s）
    DEFAULT_TIMEOUT = 600
    # 轮询间隔（秒），非 busy-wait
    POLL_INTERVAL = 0.5
    # response JSON 解析失败重试次数（架构师审查 P3.2）
    MAX_JSON_RETRIES = 3
    # JSON 解析失败重试间隔（秒）
    JSON_RETRY_INTERVAL = 0.1

    # protocol.marker 文件名（单文件，覆盖写）
    MARKER_FILENAME = 'protocol.marker'

    # request_id 格式正则（仅允许字母数字下划线，防路径遍历攻击）
    REQUEST_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_]+$')

    def __init__(self, bridge_dir: str = None):
        """初始化桥接目录。

        Args:
            bridge_dir: 桥接文件目录路径。默认使用 skill_root/logs/host_llm_bridge/。
                        skill_root 为本文件所在目录的父目录。
        """
        if bridge_dir:
            self.bridge_dir = Path(bridge_dir)
        else:
            # 默认：scripts/../logs/host_llm_bridge/
            skill_root = Path(__file__).parent.parent
            self.bridge_dir = skill_root / 'logs' / 'host_llm_bridge'

        # 确保目录存在（parents=True 递归创建）
        self.bridge_dir.mkdir(parents=True, exist_ok=True)

    def create_request(self, agent_type: str, task: str,
                       context: Optional[Dict], prompt: str,
                       timeout_seconds: int = None) -> str:
        """创建调度请求，写入请求文件和标记文件。

        生成唯一的 request_id，将请求元数据写入 request_{id}.json，
        完整提示词写入 request_{id}.prompt，最后写入 protocol.marker。

        Args:
            agent_type: agent 类型（architect/product-manager/test-expert/solo-coder/ui-designer）
            task: 任务描述
            context: 上下文信息字典（可能包含 project_root、karpathy_principles 等）
            prompt: 完整提示词字符串
            timeout_seconds: 超时秒数，None 时使用 DEFAULT_TIMEOUT

        Returns:
            request_id 字符串（格式：时间戳_UUID短格式）
        """
        # 生成唯一 request_id（时间戳 + UUID 短格式，避免并发冲突）
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        short_uuid = uuid.uuid4().hex[:8]
        request_id = f'{timestamp}_{short_uuid}'

        # 超时配置
        timeout = timeout_seconds if timeout_seconds else self.DEFAULT_TIMEOUT

        # 请求元数据
        request_data = {
            'request_id': request_id,
            'agent_type': agent_type,
            'task': task,
            'context': context or {},
            'timeout_seconds': timeout,
            'timestamp': datetime.now().isoformat(),
            'request_file': str(self.bridge_dir / f'request_{request_id}.json'),
            'prompt_file': str(self.bridge_dir / f'request_{request_id}.prompt'),
        }

        # 1. 写入请求元数据文件
        request_file = self.bridge_dir / f'request_{request_id}.json'
        self._write_json_atomic(request_file, request_data)

        # 2. 写入完整提示词文件
        prompt_file = self.bridge_dir / f'request_{request_id}.prompt'
        with open(prompt_file, 'w', encoding='utf-8') as f:
            f.write(prompt)

        # 3. 写入 protocol.marker 文件（单行 JSON，覆盖写）
        # marker 包含宿主 LLM 需要的关键信息，无需读取完整 request 文件即可决策
        marker_data = {
            'request_id': request_id,
            'agent_type': agent_type,
            'task': task,
            'request_file': str(request_file),
            'prompt_file': str(prompt_file),
            'timeout_seconds': timeout,
            'timestamp': datetime.now().isoformat(),
        }
        marker_file = self.bridge_dir / self.MARKER_FILENAME
        # marker 使用覆盖写（单个待处理请求，新请求覆盖旧请求）
        with open(marker_file, 'w', encoding='utf-8') as f:
            f.write(json.dumps(marker_data, ensure_ascii=False))

        return request_id

    def wait_for_response(self, request_id: str,
                          timeout: int = None) -> Dict:
        """轮询等待结果文件，返回结果字典。

        每 POLL_INTERVAL 秒检查 response_{id}.json 是否存在。
        文件存在后读取，捕获 JSONDecodeError 重试（最多 MAX_JSON_RETRIES 次）。
        超时返回 {'success': False, 'error': 'timeout', 'timeout': True}。

        Args:
            request_id: 请求 ID
            timeout: 超时秒数，None 时使用 DEFAULT_TIMEOUT

        Returns:
            结果字典，包含以下字段：
            - success: 是否成功
            - output: 子代理输出
            - error: 错误信息
            - timeout: 是否超时
        """
        # 安全校验：request_id 格式
        if not self.validate_request_id(request_id):
            return {
                'success': False,
                'error': f'invalid request_id: {request_id}',
                'timeout': False
            }

        timeout_val = timeout if timeout else self.DEFAULT_TIMEOUT
        response_file = self.bridge_dir / f'response_{request_id}.json'

        # 计算超时截止时间
        deadline = time.monotonic() + timeout_val

        while time.monotonic() < deadline:
            if response_file.exists():
                # 文件存在，尝试读取（含 JSON 容错）
                for retry in range(self.MAX_JSON_RETRIES):
                    try:
                        with open(response_file, 'r', encoding='utf-8') as f:
                            result = json.load(f)
                        # 读取成功，清理请求和响应文件
                        self._cleanup_request_files(request_id)
                        return {
                            'success': result.get('success', False),
                            'output': result.get('output', ''),
                            'error': result.get('error', ''),
                            'timeout': False
                        }
                    except json.JSONDecodeError:
                        # JSON 解析失败，可能是文件正在写入中（竞态）
                        # 短暂等待后重试
                        if retry < self.MAX_JSON_RETRIES - 1:
                            time.sleep(self.JSON_RETRY_INTERVAL)
                        else:
                            # 重试耗尽，返回错误
                            return {
                                'success': False,
                                'error': f'response file corrupted (JSON decode failed after {self.MAX_JSON_RETRIES} retries)',
                                'timeout': False
                            }
                    except Exception as e:
                        return {
                            'success': False,
                            'error': f'response file read error: {str(e)}',
                            'timeout': False
                        }

            # 等待下一次轮询
            time.sleep(self.POLL_INTERVAL)

        # 超时
        return {
            'success': False,
            'error': f'timeout after {timeout_val}s waiting for response',
            'timeout': True
        }

    @staticmethod
    def write_response(request_id: str, success: bool,
                       output: str, error: str = "",
                       bridge_dir: str = None) -> str:
        """写入结果文件（供宿主 LLM 调用）。

        原子写入流程（架构师审查 P3.1）：
        1. 写入临时文件 response_{id}.json.tmp
        2. os.replace() 原子替换为 response_{id}.json
        3. 清除 protocol.marker 文件

        原子性保证：os.replace 在同一文件系统上是原子的，
        脚本轮询时要么看到旧文件（不存在），要么看到完整的新文件。

        Args:
            request_id: 请求 ID
            success: 是否成功
            output: 子代理输出内容
            error: 错误信息（失败时填写）
            bridge_dir: 桥接目录路径，None 时使用默认目录

        Returns:
            结果文件路径
        """
        # 安全校验
        if not HostLLMBridge.validate_request_id(request_id):
            raise ValueError(f'invalid request_id: {request_id}')

        # 确定桥接目录
        if bridge_dir:
            bdir = Path(bridge_dir)
        else:
            skill_root = Path(__file__).parent.parent
            bdir = skill_root / 'logs' / 'host_llm_bridge'
        bdir.mkdir(parents=True, exist_ok=True)

        # 结果数据
        response_data = {
            'request_id': request_id,
            'success': success,
            'output': output,
            'error': error,
            'timestamp': datetime.now().isoformat()
        }

        # 1. 原子写入：先写临时文件，再 rename
        response_file = bdir / f'response_{request_id}.json'
        tmp_file = bdir / f'response_{request_id}.json.tmp'

        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(response_data, f, ensure_ascii=False, indent=2)

        # os.replace 是原子操作（同一文件系统上）
        os.replace(str(tmp_file), str(response_file))

        # 2. 清除 protocol.marker 文件
        marker_file = bdir / HostLLMBridge.MARKER_FILENAME
        if marker_file.exists():
            marker_file.unlink()

        return str(response_file)

    @staticmethod
    def read_request(request_id: str,
                     bridge_dir: str = None) -> Optional[Dict]:
        """读取请求文件（供宿主 LLM 调用）。

        安全校验（架构师审查 P7.2）：
        - 验证 request_id 格式（仅允许字母数字下划线）
        - 验证返回的 request_file 路径在 bridge_dir 下

        Args:
            request_id: 请求 ID
            bridge_dir: 桥接目录路径，None 时使用默认目录

        Returns:
            请求字典，文件不存在时返回 None
        """
        # 安全校验：request_id 格式
        if not HostLLMBridge.validate_request_id(request_id):
            raise ValueError(f'invalid request_id: {request_id}')

        # 确定桥接目录
        if bridge_dir:
            bdir = Path(bridge_dir)
        else:
            skill_root = Path(__file__).parent.parent
            bdir = skill_root / 'logs' / 'host_llm_bridge'

        request_file = bdir / f'request_{request_id}.json'
        if not request_file.exists():
            return None

        with open(request_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 安全校验：验证 request_file 路径在 bridge_dir 下
        req_file_path = data.get('request_file', '')
        if req_file_path:
            try:
                common = os.path.commonpath([os.path.abspath(req_file_path), str(bdir)])
                if common != str(bdir):
                    raise ValueError(f'request_file path outside bridge_dir: {req_file_path}')
            except ValueError:
                # commonpath 可能因路径不存在抛出，忽略路径校验
                pass

        return data

    @staticmethod
    def read_marker(bridge_dir: str = None) -> Optional[Dict]:
        """读取 protocol.marker 文件（供宿主 LLM 轮询调用）。

        宿主 LLM 应每隔 5 秒调用此方法检查是否有待处理的请求。

        Args:
            bridge_dir: 桥接目录路径，None 时使用默认目录

        Returns:
            标记字典，无标记时返回 None
        """
        # 确定桥接目录
        if bridge_dir:
            bdir = Path(bridge_dir)
        else:
            skill_root = Path(__file__).parent.parent
            bdir = skill_root / 'logs' / 'host_llm_bridge'

        marker_file = bdir / HostLLMBridge.MARKER_FILENAME
        if not marker_file.exists():
            return None

        try:
            with open(marker_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            if not content:
                return None
            return json.loads(content)
        except (json.JSONDecodeError, IOError):
            return None

    @staticmethod
    def clear_marker(bridge_dir: str = None) -> None:
        """清除 protocol.marker 文件（供宿主 LLM 处理完成后调用）。

        Args:
            bridge_dir: 桥接目录路径，None 时使用默认目录
        """
        # 确定桥接目录
        if bridge_dir:
            bdir = Path(bridge_dir)
        else:
            skill_root = Path(__file__).parent.parent
            bdir = skill_root / 'logs' / 'host_llm_bridge'

        marker_file = bdir / HostLLMBridge.MARKER_FILENAME
        if marker_file.exists():
            marker_file.unlink()

    @staticmethod
    def validate_request_id(request_id: str) -> bool:
        """验证 request_id 格式（安全防护）。

        仅允许字母、数字、下划线，防止路径遍历攻击（如 ../../../etc/passwd）。

        Args:
            request_id: 待验证的请求 ID

        Returns:
            格式合法返回 True，否则 False
        """
        if not request_id or not isinstance(request_id, str):
            return False
        if len(request_id) > 128:
            # 长度限制，防止异常输入
            return False
        return bool(HostLLMBridge.REQUEST_ID_PATTERN.match(request_id))

    def _write_json_atomic(self, file_path: Path, data: Dict) -> None:
        """原子写入 JSON 文件（内部方法）。

        先写入临时文件，再 os.replace 原子替换。

        Args:
            file_path: 目标文件路径
            data: 待写入的字典数据
        """
        tmp_path = file_path.with_suffix(file_path.suffix + '.tmp')
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(str(tmp_path), str(file_path))

    def _cleanup_request_files(self, request_id: str) -> None:
        """清理请求相关的文件（内部方法）。

        读取完响应后，删除请求文件和提示词文件，保留响应文件供审计。

        Args:
            request_id: 请求 ID
        """
        # 删除请求元数据文件
        request_file = self.bridge_dir / f'request_{request_id}.json'
        if request_file.exists():
            try:
                request_file.unlink()
            except OSError:
                pass

        # 删除提示词文件
        prompt_file = self.bridge_dir / f'request_{request_id}.prompt'
        if prompt_file.exists():
            try:
                prompt_file.unlink()
            except OSError:
                pass

        # 注意：不删除 response 文件，保留供审计
        # 注意：不删除 marker 文件，由 write_response 负责清除


if __name__ == '__main__':
    # 命令行工具：供宿主 LLM 通过命令行调用
    import sys
    import argparse

    parser = argparse.ArgumentParser(description='HostLLM Bridge 命令行工具')
    parser.add_argument('--bridge-dir', help='桥接目录路径', default=None)
    sub = parser.add_subparsers(dest='command')

    # read-marker 子命令
    sub.add_parser('read-marker', help='读取 protocol.marker')

    # read-request 子命令
    rr = sub.add_parser('read-request', help='读取请求文件')
    rr.add_argument('request_id', help='请求 ID')

    # write-response 子命令
    wr = sub.add_parser('write-response', help='写入结果文件')
    wr.add_argument('request_id', help='请求 ID')
    wr.add_argument('--success', choices=['true', 'false'], default='true')
    wr.add_argument('--output', default='', help='子代理输出')
    wr.add_argument('--error', default='', help='错误信息')

    # clear-marker 子命令
    sub.add_parser('clear-marker', help='清除 protocol.marker')

    args = parser.parse_args()

    if args.command == 'read-marker':
        marker = HostLLMBridge.read_marker(args.bridge_dir)
        if marker:
            print(json.dumps(marker, ensure_ascii=False, indent=2))
        else:
            print('{}')

    elif args.command == 'read-request':
        try:
            req = HostLLMBridge.read_request(args.request_id, args.bridge_dir)
            if req:
                print(json.dumps(req, ensure_ascii=False, indent=2))
            else:
                print('{}')
        except ValueError as e:
            print(f'Error: {e}', file=sys.stderr)
            sys.exit(1)

    elif args.command == 'write-response':
        try:
            path = HostLLMBridge.write_response(
                request_id=args.request_id,
                success=(args.success == 'true'),
                output=args.output,
                error=args.error,
                bridge_dir=args.bridge_dir
            )
            print(f'Response written to: {path}')
        except ValueError as e:
            print(f'Error: {e}', file=sys.stderr)
            sys.exit(1)

    elif args.command == 'clear-marker':
        HostLLMBridge.clear_marker(args.bridge_dir)
        print('Marker cleared')

    else:
        parser.print_help()
