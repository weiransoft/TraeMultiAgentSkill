#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 语义匹配器

基于大模型 AI 助手的语义理解能力，实现智能角色匹配。

核心功能：
1. 使用大模型理解任务需求的深层语义
2. 基于角色能力描述进行智能匹配
3. 提供匹配原因和置信度解释
4. 支持多轮对话优化匹配结果

技术优势：
- 超越简单文本相似度，理解语义关联
- 支持上下文感知的匹配
- 可解释的匹配结果
- 持续学习和优化
"""

import os
import json
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import hashlib


@dataclass
class SemanticMatchResult:
    """语义匹配结果"""
    role_id: str
    role_name: str
    confidence: float  # 置信度 (0-1)
    reasoning: str  # 匹配推理过程
    matched_capabilities: List[str] = field(default_factory=list)
    relevance_score: float = 0.0  # 相关性评分
    explanation: str = ""  # 详细解释
    metadata: Dict[str, Any] = field(default_factory=dict)


class AISemanticMatcher:
    """
    AI 语义匹配器
    
    利用大模型 AI 助手的语义理解能力，实现智能角色匹配。
    
    工作原理：
    1. 分析任务需求，提取关键语义信息
    2. 理解角色能力和职责
    3. 基于语义相似度进行匹配
    4. 生成可解释的匹配结果
    """
    
    def __init__(self, ai_client: Any = None):
        """
        初始化 AI 语义匹配器
        
        Args:
            ai_client: AI 客户端实例（如 Trae AI 助手）
        """
        self.ai_client = ai_client
        self.match_cache: Dict[str, SemanticMatchResult] = {}
        self.match_history: List[Dict[str, Any]] = []
        
        # 匹配提示词模板
        self.match_prompt_template = """
你是一个智能角色匹配专家。请分析以下任务需求，并匹配最适合的角色。

## 任务需求
**标题**: {task_title}
**描述**: {task_description}
**所需能力**: {required_capabilities}
**偏好技能**: {preferred_skills}

## 可用角色
{role_descriptions}

## 匹配要求
1. 分析任务的核心需求和关键能力要求
2. 评估每个角色与任务的匹配度
3. 考虑角色的专业能力和经验
4. 提供匹配原因和置信度评分

请以 JSON 格式返回匹配结果：
{{
    "matches": [
        {{
            "role_id": "角色 ID",
            "role_name": "角色名称",
            "confidence": 0.0-1.0,
            "reasoning": "匹配推理过程",
            "matched_capabilities": ["匹配的能力列表"],
            "relevance_score": 0.0-1.0,
            "explanation": "详细解释"
        }}
    ],
    "best_match": "最佳匹配角色 ID",
    "analysis": "整体分析"
}}
"""
    
    def match(self, 
              task_title: str,
              task_description: str,
              roles: List[Dict[str, Any]],
              required_capabilities: List[str] = None,
              preferred_skills: List[str] = None,
              use_cache: bool = True) -> List[SemanticMatchResult]:
        """
        使用 AI 进行智能角色匹配
        
        Args:
            task_title: 任务标题
            task_description: 任务描述
            roles: 角色列表，每个角色包含 id, name, description, capabilities
            required_capabilities: 必需能力列表
            preferred_skills: 偏好技能列表
            use_cache: 是否使用缓存
            
        Returns:
            List[SemanticMatchResult]: 匹配结果列表
        """
        # 生成缓存键
        cache_key = self._generate_cache_key(
            task_title, task_description, roles
        )
        
        # 检查缓存
        if use_cache and cache_key in self.match_cache:
            print(f"✅ 使用缓存的匹配结果")
            return [self.match_cache[cache_key]]
        
        # 构建角色描述
        role_descriptions = self._build_role_descriptions(roles)
        
        # 构建提示词
        prompt = self.match_prompt_template.format(
            task_title=task_title,
            task_description=task_description,
            required_capabilities=required_capabilities or [],
            preferred_skills=preferred_skills or [],
            role_descriptions=role_descriptions
        )
        
        # 调用 AI 助手
        try:
            ai_response = self._call_ai_assistant(prompt)
            
            # 解析 AI 响应
            results = self._parse_ai_response(ai_response, roles)
            
            # 缓存结果
            if results and use_cache:
                self.match_cache[cache_key] = results[0]
            
            # 记录历史
            self._record_match(task_title, roles, results)
            
            return results
            
        except Exception as e:
            print(f"❌ AI 匹配失败：{e}")
            # 降级到传统匹配
            return self._fallback_match(task_description, roles)
    
    def _build_role_descriptions(self, roles: List[Dict[str, Any]]) -> str:
        """构建角色描述文本"""
        descriptions = []
        
        for i, role in enumerate(roles, 1):
            desc = f"""
{i}. **{role.get('name', 'Unknown')}** ({role.get('id', 'unknown')})
   - 职责：{role.get('description', '')}
   - 能力：{', '.join(role.get('capabilities', []))}
   - 技能：{', '.join(role.get('skills', []))}
"""
            descriptions.append(desc)
        
        return "\n".join(descriptions)
    
    def _call_ai_assistant(self, prompt: str) -> str:
        """
        调用 AI 助手
        
        诚实降级策略：
        - 有 ai_client → 调用真实 AI
        - 无 ai_client → 抛出异常，由上层 match() 捕获后降级到 _fallback_match()
          （确定性关键词匹配），不再返回伪装成 AI 响应的模拟数据
        
        Args:
            prompt: 提示词
            
        Returns:
            str: AI 响应
            
        Raises:
            RuntimeError: 无 AI 客户端时抛出，触发上层降级
        """
        if self.ai_client:
            try:
                response = self.ai_client.complete(prompt)
                return response
            except Exception as e:
                # AI 调用失败同样触发降级
                raise RuntimeError(f"AI 客户端调用失败：{e}") from e
        
        # 无 AI 客户端：诚实告知不可用，由上层降级到确定性匹配
        raise RuntimeError(
            "AI 客户端未配置（脚本层无 LLM 能力）。"
            "请注入 ai_client 或使用降级匹配 _fallback_match()"
        )
    
    def _parse_ai_response(self, response: str, 
                          roles: List[Dict[str, Any]]) -> List[SemanticMatchResult]:
        """
        解析 AI 响应
        
        Args:
            response: AI 响应
            roles: 角色列表
            
        Returns:
            List[SemanticMatchResult]: 匹配结果
        """
        try:
            # 尝试解析 JSON
            if isinstance(response, str):
                data = json.loads(response)
            else:
                data = response
            
            results = []
            for match_data in data.get('matches', []):
                result = SemanticMatchResult(
                    role_id=match_data.get('role_id', ''),
                    role_name=match_data.get('role_name', ''),
                    confidence=float(match_data.get('confidence', 0.0)),
                    reasoning=match_data.get('reasoning', ''),
                    matched_capabilities=match_data.get('matched_capabilities', []),
                    relevance_score=float(match_data.get('relevance_score', 0.0)),
                    explanation=match_data.get('explanation', ''),
                    metadata={
                        'best_match': data.get('best_match'),
                        'analysis': data.get('analysis')
                    }
                )
                results.append(result)
            
            # 按置信度排序
            results.sort(key=lambda r: r.confidence, reverse=True)
            
            return results
            
        except Exception as e:
            print(f"❌ 解析 AI 响应失败：{e}")
            return []
    
    def _fallback_match(self, task_description: str, 
                       roles: List[Dict[str, Any]]) -> List[SemanticMatchResult]:
        """
        降级匹配策略
        
        当 AI 匹配失败时使用
        
        Args:
            task_description: 任务描述
            roles: 角色列表
            
        Returns:
            List[SemanticMatchResult]: 匹配结果
        """
        print("⚠️  使用降级匹配策略")
        
        # 简单的关键词匹配
        results = []
        task_lower = task_description.lower()
        
        for role in roles:
            # 计算关键词匹配度
            keywords = role.get('keywords', [])
            match_count = sum(1 for kw in keywords if kw.lower() in task_lower)
            
            if match_count > 0:
                confidence = min(match_count / 5.0, 0.8)
                result = SemanticMatchResult(
                    role_id=role.get('id', ''),
                    role_name=role.get('name', ''),
                    confidence=confidence,
                    reasoning=f"关键词匹配：{match_count} 个关键词匹配",
                    matched_capabilities=role.get('capabilities', [])[:3],
                    relevance_score=match_count / 10.0,
                    explanation="基于关键词的简单匹配"
                )
                results.append(result)
        
        results.sort(key=lambda r: r.confidence, reverse=True)
        return results
    
    def _generate_cache_key(self, task_title: str, 
                           task_description: str,
                           roles: List[Dict[str, Any]]) -> str:
        """生成缓存键"""
        content = f"{task_title}|{task_description}|{len(roles)}"
        return hashlib.md5(content.encode('utf-8')).hexdigest()
    
    def _record_match(self, task_title: str, 
                     roles: List[Dict[str, Any]],
                     results: List[SemanticMatchResult]):
        """记录匹配历史"""
        record = {
            'task_title': task_title,
            'timestamp': datetime.now().isoformat(),
            'roles_count': len(roles),
            'results': [
                {
                    'role_id': r.role_id,
                    'confidence': r.confidence,
                    'reasoning': r.reasoning
                } for r in results
            ]
        }
        self.match_history.append(record)
    
    def get_match_history(self, limit: int = 10) -> List[Dict]:
        """获取匹配历史"""
        return self.match_history[-limit:]
    
    def clear_cache(self):
        """清除缓存"""
        self.match_cache.clear()
        print("✅ 缓存已清除")
    
    def explain_match(self, result: SemanticMatchResult) -> str:
        """
        解释匹配结果
        
        Args:
            result: 匹配结果
            
        Returns:
            str: 解释文本
        """
        explanation = f"""
🎯 匹配结果：{result.role_name} ({result.role_id})
置信度：{result.confidence:.1%}
相关性：{result.relevance_score:.1%}

匹配原因:
{result.reasoning}

详细说明:
{result.explanation}

匹配的能力:
{', '.join(result.matched_capabilities) if result.matched_capabilities else '无'}
"""
        return explanation.strip()


def main():
    """示例用法"""
    # 创建匹配器
    matcher = AISemanticMatcher()
    
    # 定义角色
    roles = [
        {
            'id': 'architect',
            'name': '架构师',
            'description': '负责系统架构设计和技术选型',
            'capabilities': ['系统架构设计', '技术选型', '架构评审'],
            'skills': ['架构设计', '系统设计'],
            'keywords': ['架构', '设计', '系统', '选型']
        },
        {
            'id': 'product-manager',
            'name': '产品经理',
            'description': '负责需求分析和产品规划',
            'capabilities': ['需求分析', '产品规划', '用户研究'],
            'skills': ['需求挖掘', '文档编写'],
            'keywords': ['需求', '产品', '用户', '功能']
        },
        {
            'id': 'developer',
            'name': '开发工程师',
            'description': '负责代码实现和功能开发',
            'capabilities': ['代码实现', '功能开发', '代码优化'],
            'skills': ['编程', '调试'],
            'keywords': ['开发', '实现', '代码', '编程']
        }
    ]
    
    # 测试任务
    tasks = [
        {
            'title': '设计微服务架构',
            'description': '设计一个高可用的微服务架构，需要考虑服务拆分、通信机制和部署方案',
            'required_capabilities': ['系统架构设计'],
            'preferred_skills': ['架构设计']
        },
        {
            'title': '实现用户管理功能',
            'description': '实现用户注册、登录、权限管理等功能',
            'required_capabilities': ['代码实现'],
            'preferred_skills': ['编程']
        },
        {
            'title': '分析用户需求',
            'description': '分析用户需求，编写 PRD 文档，定义产品功能',
            'required_capabilities': ['需求分析'],
            'preferred_skills': ['文档编写']
        }
    ]
    
    # 执行匹配
    print("="*80)
    print("AI 语义匹配测试")
    print("="*80)
    
    for task in tasks:
        print(f"\n📋 任务：{task['title']}")
        print("-" * 80)
        
        results = matcher.match(
            task_title=task['title'],
            task_description=task['description'],
            roles=roles,
            required_capabilities=task.get('required_capabilities'),
            preferred_skills=task.get('preferred_skills')
        )
        
        if results:
            best = results[0]
            print(f"✅ 最佳匹配：{best.role_name} ({best.role_id})")
            print(f"   置信度：{best.confidence:.1%}")
            print(f"   相关性：{best.relevance_score:.1%}")
            print(f"   原因：{best.reasoning}")
            print(f"\n📝 详细说明:")
            print(f"   {best.explanation}")
        else:
            print("❌ 未找到匹配的角色")
    
    # 显示匹配历史
    print("\n" + "="*80)
    print("匹配历史")
    print("="*80)
    history = matcher.get_match_history()
    for record in history:
        print(f"任务：{record['task_title']}")
        print(f"时间：{record['timestamp']}")
        print(f"结果：{len(record['results'])} 个匹配")
        print()


if __name__ == "__main__":
    main()
