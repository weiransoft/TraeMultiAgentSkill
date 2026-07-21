#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能角色匹配器（AI 增强版）

基于任务需求和角色能力，智能匹配最适合的角色

v2.1 新增：
- 集成 AI 语义匹配器，使用大模型理解深层语义
- 支持多种匹配策略：关键词、AI 语义、混合
- 提供可解释的匹配结果和置信度评分
- 缓存机制优化性能
"""

import re
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

# 导入 AI 语义匹配器
try:
    from ai_semantic_matcher import AISemanticMatcher, SemanticMatchResult
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False
    print("⚠️  AI 语义匹配器不可用，将使用传统匹配方法")

# 导入本地确定性 embedder（TFIDF/Hashing，无网络依赖）
try:
    from dynamic_workflow.semantic_embedder import TFIDFEmbedder, HashingEmbedder
    EMBEDDER_AVAILABLE = True
except ImportError:
    EMBEDDER_AVAILABLE = False
    print("⚠️  本地 embedder 不可用，语义匹配将降级到关键词重叠")


class MatchStrategy(Enum):
    """匹配策略"""
    KEYWORD = "keyword"  # 关键词匹配
    SEMANTIC = "semantic"  # 语义匹配（AI 驱动）
    HYBRID = "hybrid"  # 混合匹配
    AI_ENHANCED = "ai_enhanced"  # AI 增强混合匹配（推荐）


@dataclass
class RoleDefinition:
    """角色定义"""
    role_id: str
    name: str
    description: str
    capabilities: List[str] = field(default_factory=list)  # 能力列表
    skills: List[str] = field(default_factory=list)  # 技能列表
    keywords: List[str] = field(default_factory=list)  # 关键词
    priority: int = 0  # 优先级
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskRequirement:
    """任务需求"""
    task_id: str
    title: str
    description: str
    required_capabilities: List[str] = field(default_factory=list)
    preferred_skills: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    priority: str = "medium"


@dataclass
class MatchResult:
    """匹配结果"""
    role_id: str
    role_name: str
    confidence: float  # 置信度 (0-1)
    matched_capabilities: List[str] = field(default_factory=list)
    matched_skills: List[str] = field(default_factory=list)
    missing_capabilities: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    score_breakdown: Dict[str, float] = field(default_factory=dict)


class RoleMatcher:
    """
    智能角色匹配器（AI 增强版）
    
    功能：
    - 基于关键词的角色匹配
    - 基于 AI 语义理解的角色匹配
    - 混合匹配策略
    - 置信度评分
    - 多角色推荐
    - 可解释的匹配结果
    """
    
    def __init__(self, strategy: MatchStrategy = MatchStrategy.AI_ENHANCED):
        """
        初始化角色匹配器
        
        Args:
            strategy: 匹配策略
        """
        self.strategy = strategy
        self.roles: Dict[str, RoleDefinition] = {}
        
        # 角色能力权重
        self.capability_weights: Dict[str, float] = {}
        
        # 历史匹配记录（用于学习）
        self.match_history: List[Dict[str, Any]] = []
        
        # AI 语义匹配器
        if AI_AVAILABLE:
            self.ai_matcher = AISemanticMatcher()
            print("✅ AI 语义匹配器已初始化")
        else:
            self.ai_matcher = None
            print("⚠️  AI 语义匹配器未初始化")
        
        # 本地确定性 embedder（用于无 LLM 时的语义相似度计算）
        self._embedder = None
        if EMBEDDER_AVAILABLE:
            try:
                self._embedder = TFIDFEmbedder()
            except Exception:
                try:
                    self._embedder = HashingEmbedder(n_features=512)
                except Exception:
                    self._embedder = None
    
    def register_role(self, role: RoleDefinition):
        """
        注册角色
        
        Args:
            role: 角色定义
        """
        self.roles[role.role_id] = role
        print(f"✅ 角色已注册：{role.name} ({role.role_id})")
    
    def unregister_role(self, role_id: str) -> bool:
        """
        注销角色
        
        Args:
            role_id: 角色 ID
            
        Returns:
            bool: 是否注销成功
        """
        if role_id in self.roles:
            del self.roles[role_id]
            print(f"✅ 角色已注销：{role_id}")
            return True
        return False
    
    def match(self, requirement: TaskRequirement, top_k: int = 3) -> List[MatchResult]:
        """
        匹配最适合的角色（AI 驱动）
        
        Args:
            requirement: 任务需求
            top_k: 返回前 K 个匹配结果
            
        Returns:
            List[MatchResult]: 匹配结果列表，按置信度降序排列
        """
        print(f"🧠 开始 AI 智能角色匹配...")
        print(f"   任务：{requirement.title}")
        print(f"   描述：{requirement.description[:100]}...")
        
        # 使用 AI 语义匹配
        if self._should_use_ai() and self.ai_matcher:
            return self._ai_semantic_match(requirement, top_k)
        
        # 降级到混合匹配
        print("⚠️  降级到混合匹配模式")
        return self._hybrid_match(requirement, top_k)
    
    def _should_use_ai(self) -> bool:
        """判断是否应该使用 AI 匹配"""
        return self.strategy in [MatchStrategy.SEMANTIC, MatchStrategy.AI_ENHANCED, MatchStrategy.HYBRID]
    
    def _ai_enhanced_match(self, requirement: TaskRequirement) -> List[MatchResult]:
        """
        AI 增强混合匹配
        
        结合 AI 语义匹配和关键词匹配的优势
        
        Args:
            requirement: 任务需求
            
        Returns:
            List[MatchResult]: 匹配结果列表
        """
        if not self.ai_matcher:
            return []
        
        # 准备角色数据
        roles_data = []
        for role_id, role in self.roles.items():
            roles_data.append({
                'id': role.role_id,
                'name': role.name,
                'description': role.description,
                'capabilities': role.capabilities,
                'skills': role.skills,
                'keywords': role.keywords
            })
        
        # 调用 AI 语义匹配
        ai_results = self.ai_matcher.match(
            task_title=requirement.title,
            task_description=requirement.description,
            roles=roles_data,
            required_capabilities=requirement.required_capabilities,
            preferred_skills=requirement.preferred_skills
        )
        
        # 转换为 MatchResult
        results = []
        for ai_result in ai_results:
            role = self.roles.get(ai_result.role_id)
            if role:
                match_result = MatchResult(
                    role_id=ai_result.role_id,
                    role_name=ai_result.role_name,
                    confidence=ai_result.confidence,
                    matched_capabilities=ai_result.matched_capabilities,
                    matched_skills=[],
                    missing_capabilities=[],
                    reasons=[ai_result.reasoning],
                    score_breakdown={
                        'semantic': ai_result.relevance_score,
                        'ai_confidence': ai_result.confidence
                    }
                )
                results.append(match_result)
        
        return results
    
    def _ai_semantic_match(self, requirement: TaskRequirement, top_k: int = 3) -> List[MatchResult]:
        """
        AI 语义匹配（使用 AI 助手）
        
        利用大模型 AI 助手的语义理解能力，深度分析任务需求与角色能力的匹配度
        
        Args:
            requirement: 任务需求
            top_k: 返回前 K 个匹配结果
            
        Returns:
            List[MatchResult]: 匹配结果列表
        """
        print("\n🤖 使用 AI 助手进行智能角色匹配...")
        
        # 准备角色数据
        roles_data = []
        for role_id, role in self.roles.items():
            roles_data.append({
                'id': role.role_id,
                'name': role.name,
                'description': role.description,
                'capabilities': role.capabilities,
                'skills': role.skills,
                'priority': role.priority
            })
        
        # 构建 AI 提示词
        prompt = self._build_ai_match_prompt(requirement, roles_data)
        
        # 调用 AI 助手
        try:
            ai_response = self._call_ai_assistant(prompt)
            
            # 解析 AI 响应
            results = self._parse_ai_response(ai_response, requirement)
            
            if results:
                print(f"✅ AI 匹配成功，返回 {len(results)} 个匹配结果")
                return results[:top_k]
            
        except Exception as e:
            print(f"⚠️  AI 匹配失败：{e}")
            print("   降级到传统匹配方法")
        
        # 降级到混合匹配
        return self._hybrid_match(requirement, top_k)
    
    def _build_ai_match_prompt(self, requirement: TaskRequirement, 
                               roles_data: List[Dict]) -> str:
        """
        构建 AI 匹配提示词
        
        Args:
            requirement: 任务需求
            roles_data: 角色数据列表
            
        Returns:
            str: AI 提示词
        """
        # 构建角色描述
        role_descriptions = "\n\n".join([
            f"{i+1}. **{role['name']}** ({role['id']})\n"
            f"   描述：{role['description']}\n"
            f"   能力：{', '.join(role['capabilities'])}\n"
            f"   技能：{', '.join(role['skills'])}\n"
            f"   优先级：{role['priority']}"
            for i, role in enumerate(roles_data)
        ])
        
        prompt = f"""你是一个智能角色匹配专家。请深入分析以下任务需求，匹配最适合的角色。

## 任务需求
**标题**: {requirement.title}
**描述**: {requirement.description}
**所需能力**: {', '.join(requirement.required_capabilities) if requirement.required_capabilities else '未指定'}
**偏好技能**: {', '.join(requirement.preferred_skills) if requirement.preferred_skills else '未指定'}

## 可用角色
{role_descriptions}

## 匹配要求
1. **深度理解任务语义**：分析任务的核心需求、关键能力要求、工作性质
2. **评估角色匹配度**：考虑角色的专业能力、经验、职责范围
3. **区分相似角色**：
   - 架构师 vs UI 设计师：架构师负责系统架构设计，UI 设计师负责界面视觉设计
   - 开发工程师 vs 测试工程师：开发负责实现功能，测试负责质量保障
   - 产品经理 vs 架构师：产品经理负责需求分析，架构师负责技术实现
4. **提供推理过程**：解释为什么这个角色最适合
5. **给出置信度**：0-1 之间的评分，考虑匹配程度

请以严格的 JSON 格式返回匹配结果（不要有任何其他文字）：
{{
    "matches": [
        {{
            "role_id": "角色 ID",
            "role_name": "角色名称",
            "confidence": 0.95,
            "reasoning": "详细的匹配推理过程，说明为什么这个角色适合",
            "matched_capabilities": ["匹配的能力 1", "匹配的能力 2"],
            "relevance_score": 0.9
        }}
    ],
    "best_match": "最佳匹配角色 ID",
    "analysis": "整体任务分析和匹配策略说明"
}}

注意：
- 必须区分"架构设计"（系统架构）和"界面设计"（UI 设计）
- 必须区分"功能开发"（写代码）和"需求分析"（产品规划）
- 优先考虑高优先级角色（优先级 8-10）
- 置信度应该反映真实的匹配程度，不要过高估计
"""
        return prompt
    
    def _call_ai_assistant(self, prompt: str) -> str:
        """
        调用 AI 助手

        Args:
            prompt: 提示词

        Returns:
            str: AI 响应

        max_tokens 语义（与 AIConfig 对齐）：
        - None  : 不限制，从请求体省略 max_tokens 字段，由模型使用自身默认上限
        - int>0 : 显式上限（单位：token）
        """
        # 从 self.options / 配置中读取 max_tokens（默认 None=不限）
        max_tokens_cfg = None
        if hasattr(self, 'options') and isinstance(self.options, dict):
            raw = self.options.get('max_tokens')
            if isinstance(raw, int) and raw > 0:
                max_tokens_cfg = raw

        def _build_kwargs(system_content: str) -> dict:
            """构建 chat.completions.create 的 kwargs，按需注入 max_tokens。"""
            kwargs = {
                "model": "gpt-4",
                "messages": [
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
            }
            # 仅在显式配置正整数上限时携带 max_tokens（None=不限，省略字段）
            if max_tokens_cfg is not None:
                kwargs["max_tokens"] = max_tokens_cfg
            return kwargs

        # 尝试使用 Trae AI 助手
        try:
            # 检查是否有 Trae AI 客户端
            if hasattr(self, 'ai_client') and self.ai_client:
                response = self.ai_client.chat.completions.create(
                    **_build_kwargs("你是一个专业的角色匹配专家，擅长分析任务需求并匹配最适合的角色。")
                )
                return response.choices[0].message.content

            # 尝试使用 ai_semantic_matcher
            if self.ai_matcher and hasattr(self.ai_matcher, 'ai_client'):
                response = self.ai_matcher.ai_client.chat.completions.create(
                    **_build_kwargs("你是一个专业的角色匹配专家。")
                )
                return response.choices[0].message.content

        except Exception as e:
            print(f"⚠️  AI 调用失败：{e}")
        
        # 降级方案：使用简化的规则匹配
        print("⚠️  使用规则-based 匹配作为降级方案")
        return self._rule_based_fallback(prompt)
    
    def _parse_ai_response(self, ai_response: str, 
                          requirement: TaskRequirement) -> List[MatchResult]:
        """
        解析 AI 响应
        
        Args:
            ai_response: AI 响应文本
            requirement: 任务需求
            
        Returns:
            List[MatchResult]: 匹配结果列表
        """
        import json
        import re
        
        try:
            # 尝试提取 JSON
            json_match = re.search(r'\{.*\}', ai_response, re.DOTALL)
            if json_match:
                json_str = json_match.group()
                data = json.loads(json_str)
            else:
                data = json.loads(ai_response)
            
            # 解析匹配结果
            results = []
            matches = data.get('matches', [])
            
            for match_data in matches:
                role_id = match_data.get('role_id')
                role = self.roles.get(role_id)
                
                if role:
                    result = MatchResult(
                        role_id=role_id,
                        role_name=match_data.get('role_name', role.name),
                        confidence=float(match_data.get('confidence', 0.5)),
                        matched_capabilities=match_data.get('matched_capabilities', []),
                        matched_skills=[],
                        missing_capabilities=[],
                        reasons=[match_data.get('reasoning', 'AI 匹配')],
                        score_breakdown={
                            'semantic': float(match_data.get('relevance_score', 0.5)),
                            'ai_confidence': float(match_data.get('confidence', 0.5))
                        }
                    )
                    results.append(result)
            
            # 按置信度排序
            results.sort(key=lambda r: r.confidence, reverse=True)
            return results
            
        except Exception as e:
            print(f"⚠️  解析 AI 响应失败：{e}")
            return []
    
    def _rule_based_fallback(self, prompt: str) -> str:
        """
        基于规则的降级方案

        Args:
            prompt: 原始提示词（未使用）

        Returns:
            str: 模拟的 AI 响应
        """
        # 这是一个简化的降级方案，当 AI 不可用时使用
        # 基于关键词和优先级进行简单匹配

        best_role = None
        best_score = 0

        for role_id, role in self.roles.items():
            # 简单评分：优先级权重 50%，关键词匹配 50%
            score = (role.priority / 10.0) * 0.5

            # 这里可以添加简单的关键词匹配逻辑
            # 但为了保持简洁，我们只使用优先级

            if score > best_score:
                best_score = score
                best_role = role

        if best_role:
            return '''
{
    "matches": [
        {
            "role_id": "%s",
            "role_name": "%s",
            "confidence": 0.7,
            "reasoning": "基于优先级的降级匹配",
            "matched_capabilities": ["%s"],
            "relevance_score": 0.7
        }
    ],
    "best_match": "%s",
    "analysis": "使用基于规则的降级方案"
}
''' % (best_role.role_id, best_role.name, best_role.capabilities[0], best_role.role_id)

        return '{"matches": [], "best_match": null, "analysis": "无匹配角色"}'

    
    def _keyword_match(self, requirement: TaskRequirement, 
                      role: RoleDefinition) -> MatchResult:
        """
        关键词匹配
        
        Args:
            requirement: 任务需求
            role: 角色定义
            
        Returns:
            MatchResult: 匹配结果
        """
        # 提取任务关键词
        task_keywords = self._extract_keywords(requirement)
        
        # 计算匹配度
        matched_capabilities = []
        matched_skills = []
        missing_capabilities = []
        
        # 能力匹配
        for capability in role.capabilities:
            if any(kw.lower() in capability.lower() for kw in task_keywords):
                matched_capabilities.append(capability)
            else:
                missing_capabilities.append(capability)
        
        # 技能匹配
        for skill in role.skills:
            if any(kw.lower() in skill.lower() for kw in task_keywords):
                matched_skills.append(skill)
        
        # 计算置信度
        capability_score = len(matched_capabilities) / max(len(role.capabilities), 1)
        skill_score = len(matched_skills) / max(len(role.skills), 1)
        keyword_overlap = len(set(role.keywords) & set(task_keywords))
        keyword_score = min(keyword_overlap / 5, 1.0)  # 最多 5 个关键词重叠
        
        # 优先级分数（归一化到 0-1，优先级范围 5-10）
        priority_score = (role.priority - 5) / 5.0  # 5->0, 10->1
        
        # 加权平均：能力 40%，技能 25%，关键词 20%，优先级 15%
        confidence = (
            capability_score * 0.40 +
            skill_score * 0.25 +
            keyword_score * 0.20 +
            priority_score * 0.15
        )
        
        # 生成原因
        reasons = []
        if matched_capabilities:
            reasons.append(f"匹配能力：{', '.join(matched_capabilities[:3])}")
        if matched_skills:
            reasons.append(f"匹配技能：{', '.join(matched_skills[:3])}")
        if keyword_overlap > 0:
            reasons.append(f"关键词重叠：{keyword_overlap} 个")
        if role.priority >= 8:
            reasons.append(f"高优先级角色（优先级：{role.priority}）")
        
        return MatchResult(
            role_id=role.role_id,
            role_name=role.name,
            confidence=confidence,
            matched_capabilities=matched_capabilities,
            matched_skills=matched_skills,
            missing_capabilities=missing_capabilities,
            reasons=reasons,
            score_breakdown={
                'capability': capability_score,
                'skill': skill_score,
                'keyword': keyword_score,
                'priority': priority_score
            }
        )
    
    def _semantic_match(self, requirement: TaskRequirement,
                       role: RoleDefinition) -> MatchResult:
        """
        语义匹配（基于本地确定性 embedder：TFIDF/Hashing）
        
        无 LLM 环境下使用确定性向量相似度替代语义理解，
        相比关键词重叠能捕捉部分词汇共现模式。
        
        Args:
            requirement: 任务需求
            role: 角色定义
            
        Returns:
            MatchResult: 匹配结果
        """
        task_text = f"{requirement.title} {requirement.description}"
        role_text = f"{role.name} {role.description} {' '.join(role.capabilities)} {' '.join(role.skills)}"
        
        if self._embedder is not None:
            # 使用本地 embedder 计算向量相似度（确定性，可复现）
            try:
                task_vec = self._embedder.embed(task_text)
                role_vec = self._embedder.embed(role_text)
                
                # 余弦相似度
                dot = sum(a * b for a, b in zip(task_vec, role_vec))
                norm_a = sum(a * a for a in task_vec) ** 0.5
                norm_b = sum(b * b for b in role_vec) ** 0.5
                similarity = dot / max(norm_a * norm_b, 1e-10)
                
                return MatchResult(
                    role_id=role.role_id,
                    role_name=role.name,
                    confidence=similarity,
                    reasons=[f"基于本地 embedder 向量相似度（{type(self._embedder).__name__}）"],
                    score_breakdown={'semantic': similarity}
                )
            except Exception as e:
                # embedder 计算失败，降级到关键词重叠
                print(f"⚠️  embedder 计算失败，降级到关键词重叠：{e}")
        
        # 降级：关键词重叠（embedder 不可用时的保底方案）
        task_words = set(task_text.lower().split())
        role_words = set(role_text.lower().split())
        intersection = len(task_words & role_words)
        union = len(task_words | role_words)
        similarity = intersection / max(union, 1)
        
        return MatchResult(
            role_id=role.role_id,
            role_name=role.name,
            confidence=similarity,
            reasons=["基于关键词重叠匹配（embedder 不可用降级）"],
            score_breakdown={'semantic': similarity}
        )
    
    def _hybrid_match(self, requirement: TaskRequirement,
                     top_k: int = 3) -> List[MatchResult]:
        """
        混合匹配（关键词 + 语义）
        
        Args:
            requirement: 任务需求
            top_k: 返回前 K 个匹配结果
            
        Returns:
            List[MatchResult]: 匹配结果列表
        """
        results = []
        
        # 遍历所有角色进行匹配
        for role_id, role in self.roles.items():
            # 关键词匹配
            keyword_result = self._keyword_match(requirement, role)
            
            # 语义匹配
            semantic_result = self._semantic_match(requirement, role)
            
            # 加权平均
            hybrid_confidence = (
                keyword_result.confidence * 0.7 +
                semantic_result.confidence * 0.3
            )
            
            # 合并原因
            reasons = keyword_result.reasons + semantic_result.reasons
            
            # 合并评分分解
            score_breakdown = {**keyword_result.score_breakdown, 
                              **semantic_result.score_breakdown}
            
            result = MatchResult(
                role_id=role.role_id,
                role_name=role.name,
                confidence=hybrid_confidence,
                matched_capabilities=keyword_result.matched_capabilities,
                matched_skills=keyword_result.matched_skills,
                missing_capabilities=keyword_result.missing_capabilities,
                reasons=reasons,
                score_breakdown=score_breakdown
            )
            
            # 只添加置信度大于阈值的结果
            if hybrid_confidence > 0.3:
                results.append(result)
        
        # 按置信度排序
        results.sort(key=lambda r: r.confidence, reverse=True)
        
        return results[:top_k]
    
    def _extract_keywords(self, requirement: TaskRequirement) -> List[str]:
        """
        从任务需求提取关键词
        
        Args:
            requirement: 任务需求
            
        Returns:
            List[str]: 关键词列表
        """
        keywords = []
        
        # 从标题和描述提取
        text = f"{requirement.title} {requirement.description}"
        
        # 简单关键词提取规则
        # 1. 提取大写字母开头的词（可能是专有名词）
        capitalized = re.findall(r'\b[A-Z][a-z]+\b', text)
        keywords.extend(capitalized)
        
        # 2. 提取技术术语
        tech_terms = ['架构', '设计', '开发', '测试', '需求', '数据库', 'API', 'UI']
        for term in tech_terms:
            if term in text:
                keywords.append(term)
        
        # 3. 添加需求中明确提到的能力
        keywords.extend(requirement.required_capabilities)
        
        return list(set(keywords))
    
    def _record_match(self, requirement: TaskRequirement, 
                     results: List[MatchResult]):
        """记录匹配历史"""
        record = {
            'task_id': requirement.task_id,
            'timestamp': datetime.now().isoformat(),
            'results': [
                {
                    'role_id': r.role_id,
                    'confidence': r.confidence
                } for r in results
            ]
        }
        self.match_history.append(record)
    
    def get_match_history(self, limit: int = 10) -> List[Dict]:
        """获取匹配历史"""
        return self.match_history[-limit:]
    
    def suggest_workflow(self, requirement: TaskRequirement) -> List[str]:
        """
        建议工作流（多角色协作顺序）
        
        Args:
            requirement: 任务需求
            
        Returns:
            List[str]: 角色 ID 列表（按推荐顺序）
        """
        # 匹配所有角色
        all_matches = self.match(requirement, top_k=len(self.roles))
        
        # 根据优先级和置信度排序
        workflow = []
        for match in all_matches:
            role = self.roles.get(match.role_id)
            if role and role.priority > 0:
                workflow.append(match.role_id)
        
        # 添加其他匹配的角色
        for match in all_matches:
            if match.role_id not in workflow:
                workflow.append(match.role_id)
        
        return workflow


def create_default_roles() -> List[RoleDefinition]:
    """创建默认角色"""
    return [
        RoleDefinition(
            role_id="product-manager",
            name="产品经理",
            description="负责需求分析、PRD 编写和产品规划",
            capabilities=[
                "需求分析",
                "PRD 编写",
                "用户研究",
                "竞品分析",
                "产品规划"
            ],
            skills=[
                "需求挖掘",
                "文档编写",
                "沟通协调",
                "数据分析"
            ],
            keywords=["需求", "产品", "PRD", "用户", "功能"],
            priority=10  # 高优先级，通常是第一个角色
        ),
        
        RoleDefinition(
            role_id="architect",
            name="架构师",
            description="负责系统架构设计和技术选型",
            capabilities=[
                "系统架构设计",
                "技术选型",
                "架构评审",
                "性能优化",
                "安全设计"
            ],
            skills=[
                "架构设计",
                "技术评估",
                "系统设计",
                "代码审查"
            ],
            keywords=["架构", "系统设计", "技术选型", "微服务", "性能优化", "安全架构"],
            priority=9  # 高优先级，通常在产品经理之后
        ),
        
        RoleDefinition(
            role_id="solo-coder",
            name="独立开发者",
            description="负责代码实现和功能开发",
            capabilities=[
                "代码实现",
                "功能开发",
                "代码优化",
                "Bug 修复",
                "单元测试"
            ],
            skills=[
                "Java",
                "Python",
                "Spring Boot",
                "数据库",
                "Git"
            ],
            keywords=["开发", "代码", "实现", "功能", "编程"],
            priority=8  # 中优先级，在架构师之后
        ),
        
        RoleDefinition(
            role_id="tester",
            name="测试工程师",
            description="负责测试用例设计和执行",
            capabilities=[
                "测试用例设计",
                "测试执行",
                "Bug 跟踪",
                "质量保障",
                "自动化测试"
            ],
            skills=[
                "测试设计",
                "测试工具",
                "Bug 分析",
                "质量评估"
            ],
            keywords=["测试", "质量", "Bug", "验证", "用例"],
            priority=7  # 中优先级，在开发之后
        ),
        
        RoleDefinition(
            role_id="ui-designer",
            name="UI 设计师",
            description="负责用户界面设计",
            capabilities=[
                "界面设计",
                "交互设计",
                "视觉设计",
                "原型设计",
                "设计系统"
            ],
            skills=[
                "Figma",
                "Sketch",
                "Photoshop",
                "原型设计",
                "色彩搭配"
            ],
            keywords=["UI", "界面设计", "交互设计", "视觉设计", "用户体验", "原型", "Figma", "界面美化"],
            priority=6
        ),
        
        RoleDefinition(
            role_id="devops",
            name="DevOps 工程师",
            description="负责部署和运维",
            capabilities=[
                "持续集成",
                "持续部署",
                "监控告警",
                "性能调优",
                "安全管理"
            ],
            skills=[
                "Docker",
                "Kubernetes",
                "CI/CD",
                "Linux",
                "监控工具"
            ],
            keywords=["部署", "运维", "CI/CD", "监控", "容器"],
            priority=5
        )
    ]


def main():
    """示例用法"""
    # 创建角色匹配器
    matcher = RoleMatcher(strategy=MatchStrategy.HYBRID)
    
    # 注册默认角色
    roles = create_default_roles()
    for role in roles:
        matcher.register_role(role)
    
    # 创建任务需求
    requirement = TaskRequirement(
        task_id="TASK-001",
        title="设计微服务架构",
        description="需要设计一个高可用的微服务架构，支持水平扩展",
        required_capabilities=["系统架构设计", "技术选型"],
        preferred_skills=["架构设计", "微服务"],
        constraints=["Java 21", "Spring Boot 3"]
    )
    
    # 匹配角色
    results = matcher.match(requirement, top_k=3)
    
    print(f"\n🎯 任务：{requirement.title}")
    print(f"\n✅ 推荐角色:")
    for i, result in enumerate(results, 1):
        print(f"\n{i}. {result.role_name} ({result.role_id})")
        print(f"   置信度：{result.confidence:.2%}")
        print(f"   匹配原因:")
        for reason in result.reasons:
            print(f"   - {reason}")
    
    # 建议工作流
    workflow = matcher.suggest_workflow(requirement)
    print(f"\n📋 建议工作流:")
    for i, role_id in enumerate(workflow, 1):
        role = matcher.roles.get(role_id)
        print(f"{i}. {role.name if role else role_id}")


if __name__ == "__main__":
    main()
