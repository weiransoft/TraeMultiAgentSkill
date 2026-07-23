#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HostLLMBridge 单元测试

测试宿主 LLM 桥接器的核心功能：
- 请求创建（create_request）
- 响应写入（write_response）
- 响应等待（wait_for_response）
- 标记读取/清除（read_marker / clear_marker）
- 请求读取（read_request）
- 安全校验（validate_request_id）
- 并发请求
- 损坏 JSON 容错

不使用 mock，全部使用真实文件系统操作。
"""

import os
import json
import time
import shutil
import tempfile
import threading
from pathlib import Path

import pytest
import sys

# 将 scripts 目录加入 sys.path
SCRIPTS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from host_llm_bridge import HostLLMBridge


@pytest.fixture
def temp_bridge_dir():
    """每个测试使用独立的临时桥接目录，测试后自动清理。"""
    tmp_dir = tempfile.mkdtemp(prefix='host_llm_bridge_test_')
    yield tmp_dir
    shutil.rmtree(tmp_dir, ignore_errors=True)


class TestCreateRequest:
    """测试 create_request 方法"""

    def test_create_request_generates_all_files(self, temp_bridge_dir):
        """验证 create_request 生成 request 文件、prompt 文件和 marker 文件"""
        bridge = HostLLMBridge(bridge_dir=temp_bridge_dir)
        request_id = bridge.create_request(
            agent_type='architect',
            task='设计系统架构',
            context={'project_root': '/test/project'},
            prompt='你是一位资深架构师...',
            timeout_seconds=300
        )

        # 验证 request_id 格式
        assert HostLLMBridge.validate_request_id(request_id), \
            f'request_id 格式不合法: {request_id}'

        # 验证请求文件存在
        request_file = Path(temp_bridge_dir) / f'request_{request_id}.json'
        assert request_file.exists(), f'请求文件不存在: {request_file}'

        # 验证请求文件内容
        with open(request_file, 'r', encoding='utf-8') as f:
            req_data = json.load(f)
        assert req_data['agent_type'] == 'architect'
        assert req_data['task'] == '设计系统架构'
        assert req_data['timeout_seconds'] == 300

        # 验证 prompt 文件存在
        prompt_file = Path(temp_bridge_dir) / f'request_{request_id}.prompt'
        assert prompt_file.exists(), f'prompt 文件不存在: {prompt_file}'

        # 验证 prompt 文件内容
        with open(prompt_file, 'r', encoding='utf-8') as f:
            prompt_content = f.read()
        assert '资深架构师' in prompt_content

        # 验证 marker 文件存在
        marker_file = Path(temp_bridge_dir) / HostLLMBridge.MARKER_FILENAME
        assert marker_file.exists(), f'marker 文件不存在: {marker_file}'

        # 验证 marker 文件内容
        with open(marker_file, 'r', encoding='utf-8') as f:
            marker_data = json.load(f)
        assert marker_data['request_id'] == request_id
        assert marker_data['agent_type'] == 'architect'

    def test_create_request_default_timeout(self, temp_bridge_dir):
        """验证未指定超时时使用默认值 600 秒"""
        bridge = HostLLMBridge(bridge_dir=temp_bridge_dir)
        request_id = bridge.create_request(
            agent_type='solo-coder',
            task='实现功能',
            context=None,
            prompt='提示词'
        )

        request_file = Path(temp_bridge_dir) / f'request_{request_id}.json'
        with open(request_file, 'r', encoding='utf-8') as f:
            req_data = json.load(f)
        assert req_data['timeout_seconds'] == HostLLMBridge.DEFAULT_TIMEOUT


class TestWriteResponse:
    """测试 write_response 方法"""

    def test_write_response_creates_file(self, temp_bridge_dir):
        """验证 write_response 创建结果文件"""
        bridge = HostLLMBridge(bridge_dir=temp_bridge_dir)
        request_id = bridge.create_request(
            'architect', '任务', None, 'prompt'
        )

        # 写入响应
        response_path = HostLLMBridge.write_response(
            request_id=request_id,
            success=True,
            output='架构设计方案...',
            error='',
            bridge_dir=temp_bridge_dir
        )

        # 验证文件存在
        assert Path(response_path).exists()

        # 验证文件内容
        with open(response_path, 'r', encoding='utf-8') as f:
            resp_data = json.load(f)
        assert resp_data['success'] is True
        assert resp_data['output'] == '架构设计方案...'
        assert resp_data['request_id'] == request_id

    def test_write_response_clears_marker(self, temp_bridge_dir):
        """验证 write_response 清除 marker 文件"""
        bridge = HostLLMBridge(bridge_dir=temp_bridge_dir)
        request_id = bridge.create_request('architect', '任务', None, 'prompt')

        # 确认 marker 存在
        marker_file = Path(temp_bridge_dir) / HostLLMBridge.MARKER_FILENAME
        assert marker_file.exists()

        # 写入响应后 marker 应被清除
        HostLLMBridge.write_response(
            request_id, True, 'output', '', temp_bridge_dir
        )
        assert not marker_file.exists(), 'marker 文件应被清除'

    def test_write_response_atomic_no_tmp_left(self, temp_bridge_dir):
        """验证原子写入后无临时文件残留"""
        bridge = HostLLMBridge(bridge_dir=temp_bridge_dir)
        request_id = bridge.create_request('architect', '任务', None, 'prompt')

        HostLLMBridge.write_response(
            request_id, True, 'output', '', temp_bridge_dir
        )

        # 不应有 .tmp 文件残留
        tmp_files = list(Path(temp_bridge_dir).glob('*.tmp'))
        assert len(tmp_files) == 0, f'有临时文件残留: {tmp_files}'


class TestWaitForResponse:
    """测试 wait_for_response 方法"""

    def test_wait_for_response_success(self, temp_bridge_dir):
        """验证正常接收结果"""
        bridge = HostLLMBridge(bridge_dir=temp_bridge_dir)
        request_id = bridge.create_request('architect', '任务', None, 'prompt')

        # 模拟宿主 LLM 在 1 秒后写入响应
        def write_delayed():
            time.sleep(1)
            HostLLMBridge.write_response(
                request_id, True, '架构方案', '', temp_bridge_dir
            )

        thread = threading.Thread(target=write_delayed)
        thread.start()

        # 等待响应（超时 10 秒）
        result = bridge.wait_for_response(request_id, timeout=10)
        thread.join()

        assert result['success'] is True
        assert result['output'] == '架构方案'
        assert result['timeout'] is False

    def test_wait_for_response_timeout(self, temp_bridge_dir):
        """验证超时机制"""
        bridge = HostLLMBridge(bridge_dir=temp_bridge_dir)
        # 覆盖轮询间隔为 0.1 秒，加速测试
        bridge.POLL_INTERVAL = 0.1

        request_id = bridge.create_request('architect', '任务', None, 'prompt')

        # 不写入响应，等待超时
        start = time.monotonic()
        result = bridge.wait_for_response(request_id, timeout=2)
        elapsed = time.monotonic() - start

        assert result['success'] is False
        assert result['timeout'] is True
        assert 'timeout' in result['error']
        # 验证实际等待了约 2 秒（允许 ±0.5 秒误差）
        assert 1.5 <= elapsed <= 3.0, f'超时时间不符: {elapsed:.2f}s'

    def test_wait_for_response_corrupted_json(self, temp_bridge_dir):
        """验证 response 文件损坏时重试后返回错误"""
        bridge = HostLLMBridge(bridge_dir=temp_bridge_dir)
        bridge.POLL_INTERVAL = 0.1
        bridge.JSON_RETRY_INTERVAL = 0.05

        request_id = bridge.create_request('architect', '任务', None, 'prompt')

        # 写入损坏的 JSON
        response_file = Path(temp_bridge_dir) / f'response_{request_id}.json'
        with open(response_file, 'w', encoding='utf-8') as f:
            f.write('{invalid json content')

        result = bridge.wait_for_response(request_id, timeout=5)

        # 重试 3 次后应返回错误
        assert result['success'] is False
        assert 'corrupted' in result['error'].lower() or 'failed' in result['error'].lower()


class TestReadMarker:
    """测试 read_marker 方法"""

    def test_read_marker_returns_data(self, temp_bridge_dir):
        """验证 read_marker 返回标记数据"""
        bridge = HostLLMBridge(bridge_dir=temp_bridge_dir)
        bridge.create_request('architect', '设计架构', None, 'prompt')

        marker = HostLLMBridge.read_marker(temp_bridge_dir)
        assert marker is not None
        assert marker['agent_type'] == 'architect'
        assert marker['task'] == '设计架构'

    def test_read_marker_returns_none_when_no_marker(self, temp_bridge_dir):
        """验证无 marker 时返回 None"""
        marker = HostLLMBridge.read_marker(temp_bridge_dir)
        assert marker is None

    def test_clear_marker(self, temp_bridge_dir):
        """验证 clear_marker 清除标记文件"""
        bridge = HostLLMBridge(bridge_dir=temp_bridge_dir)
        bridge.create_request('architect', '任务', None, 'prompt')

        marker_file = Path(temp_bridge_dir) / HostLLMBridge.MARKER_FILENAME
        assert marker_file.exists()

        HostLLMBridge.clear_marker(temp_bridge_dir)
        assert not marker_file.exists()


class TestReadRequest:
    """测试 read_request 方法"""

    def test_read_request_returns_data(self, temp_bridge_dir):
        """验证 read_request 返回请求数据"""
        bridge = HostLLMBridge(bridge_dir=temp_bridge_dir)
        request_id = bridge.create_request(
            'solo-coder', '实现功能', {'key': 'value'}, 'prompt'
        )

        req = HostLLMBridge.read_request(request_id, temp_bridge_dir)
        assert req is not None
        assert req['agent_type'] == 'solo-coder'
        assert req['task'] == '实现功能'
        assert req['context']['key'] == 'value'

    def test_read_request_returns_none_when_not_exists(self, temp_bridge_dir):
        """验证请求文件不存在时返回 None"""
        req = HostLLMBridge.read_request('nonexistent_id', temp_bridge_dir)
        assert req is None


class TestValidateRequestId:
    """测试 validate_request_id 方法"""

    @pytest.mark.parametrize('valid_id', [
        '20260722_153000_abc12345',
        'simple_id',
        'ID_WITH_UNDERSCORES',
        '123456',
        'a_b_c_1_2_3'
    ])
    def test_valid_request_ids(self, valid_id):
        """验证合法 request_id 通过校验"""
        assert HostLLMBridge.validate_request_id(valid_id) is True

    @pytest.mark.parametrize('invalid_id', [
        '../../../etc/passwd',      # 路径遍历
        'id/with/slashes',          # 斜杠
        'id with spaces',           # 空格
        'id;rm -rf /',              # 命令注入
        '',                         # 空字符串
        None,                       # None
        'a' * 200,                  # 过长
    ])
    def test_invalid_request_ids(self, invalid_id):
        """验证非法 request_id 被拒绝"""
        assert HostLLMBridge.validate_request_id(invalid_id) is False


class TestConcurrentRequests:
    """测试并发请求"""

    def test_concurrent_requests_unique_ids(self, temp_bridge_dir):
        """验证并发创建请求时 ID 不冲突"""
        bridge = HostLLMBridge(bridge_dir=temp_bridge_dir)
        request_ids = []
        lock = threading.Lock()

        def create_request():
            rid = bridge.create_request('architect', '任务', None, 'prompt')
            with lock:
                request_ids.append(rid)

        threads = [threading.Thread(target=create_request) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 验证所有 ID 唯一
        assert len(request_ids) == 5
        assert len(set(request_ids)) == 5, f'有重复 ID: {request_ids}'


class TestEndToEnd:
    """端到端集成测试"""

    def test_e2e_normal_flow(self, temp_bridge_dir):
        """端到端正常流程：创建请求 → 写入响应 → 等待并接收"""
        bridge = HostLLMBridge(bridge_dir=temp_bridge_dir)
        bridge.POLL_INTERVAL = 0.1

        # 1. 脚本侧创建请求
        request_id = bridge.create_request(
            'architect', '设计微服务架构', {'project': 'test'}, 'prompt'
        )

        # 2. 宿主 LLM 侧读取 marker
        marker = HostLLMBridge.read_marker(temp_bridge_dir)
        assert marker is not None
        assert marker['request_id'] == request_id

        # 3. 宿主 LLM 侧读取请求
        req = HostLLMBridge.read_request(request_id, temp_bridge_dir)
        assert req['agent_type'] == 'architect'

        # 4. 宿主 LLM 侧写入响应
        HostLLMBridge.write_response(
            request_id, True, '微服务架构设计方案', '', temp_bridge_dir
        )

        # 5. 脚本侧等待并接收响应
        result = bridge.wait_for_response(request_id, timeout=5)
        assert result['success'] is True
        assert result['output'] == '微服务架构设计方案'

        # 6. 验证 marker 已清除
        assert HostLLMBridge.read_marker(temp_bridge_dir) is None

    def test_e2e_timeout_flow(self, temp_bridge_dir):
        """端到端超时流程：创建请求 → 不写入响应 → 超时"""
        bridge = HostLLMBridge(bridge_dir=temp_bridge_dir)
        bridge.POLL_INTERVAL = 0.1

        request_id = bridge.create_request('architect', '任务', None, 'prompt')

        # 不写入响应，等待超时
        result = bridge.wait_for_response(request_id, timeout=2)

        assert result['success'] is False
        assert result['timeout'] is True

    def test_e2e_multiple_sequential_requests(self, temp_bridge_dir):
        """端到端多请求顺序执行"""
        bridge = HostLLMBridge(bridge_dir=temp_bridge_dir)
        bridge.POLL_INTERVAL = 0.1

        for i in range(3):
            request_id = bridge.create_request(
                'solo-coder', f'任务_{i}', None, f'prompt_{i}'
            )
            HostLLMBridge.write_response(
                request_id, True, f'输出_{i}', '', temp_bridge_dir
            )
            result = bridge.wait_for_response(request_id, timeout=5)
            assert result['success'] is True
            assert result['output'] == f'输出_{i}'


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
