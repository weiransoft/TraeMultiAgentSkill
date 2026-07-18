#!/usr/bin/env python3
"""W2 代码改造验证脚本（临时）"""
import sys
sys.path.insert(0, 'scripts')

import ai_assistant
import ai_semantic_matcher
import role_matcher

print("OK: 三个模块导入成功")

# 1. ai_semantic_matcher 无客户端时诚实降级
m = ai_semantic_matcher.AISemanticMatcher()
try:
    m._call_ai_assistant('test')
    print("FAIL: 应该抛出 RuntimeError")
    sys.exit(1)
except RuntimeError as e:
    print("OK: _call_ai_assistant 诚实降级: " + str(e)[:60])

# 2. match() 完整降级链路（AI 不可用 → _fallback_match 关键词匹配）
roles = [{
    'id': 'architect', 'name': '架构师', 'description': '系统设计',
    'capabilities': ['架构'], 'skills': ['设计'], 'keywords': ['架构', '设计']
}]
results = m.match('设计系统架构', '需要设计微服务架构', roles)
assert len(results) > 0, "降级匹配应返回结果"
print("OK: match() 降级链路: %d 个结果, reasoning=%s" % (len(results), results[0].reasoning[:40]))

# 3. role_matcher 真实 embedder
rm = role_matcher.RoleMatcher()
embedder_name = type(rm._embedder).__name__ if rm._embedder else None
print("OK: RoleMatcher embedder: %s" % embedder_name)

from role_matcher import TaskRequirement, RoleDefinition
role = RoleDefinition(
    role_id='architect', name='架构师', description='系统架构设计',
    capabilities=['架构设计'], skills=['系统设计']
)
req = TaskRequirement(task_id='t1', title='架构设计', description='设计微服务架构')
r = rm._semantic_match(req, role)
print("OK: _semantic_match: confidence=%.3f, reason=%s" % (r.confidence, r.reasons[0][:50]))
assert r.confidence > 0, "embedder 相似度应大于 0"

# 4. ai_assistant trae 诚实不可用
a = ai_assistant.AIAssistant(provider='trae')
resp = a.complete('测试')
assert resp.metadata.get('unavailable') is True, "trae 应标注 unavailable"
print("OK: _call_trae_ai 诚实降级: %s" % resp.content[:50])

print("ALL PASS")
