#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 助手工具类

提供通用的大模型 AI 助手能力，包括：
- 文本生成和补全
- 语义理解和分析
- 代码审查和建议
- 知识问答
- 上下文感知对话

支持多种 AI 后端：
- Trae AI 助手（默认）
- 自定义 AI 服务
- 本地模型
"""

import os
import json
from typing import Dict, List, Any, Optional
from datetime import datetime
from dataclasses import dataclass, field


@dataclass
class AIResponse:
    """AI 响应"""
    content: str
    confidence: float = 1.0
    reasoning: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    tokens_used: int = 0
    latency_ms: int = 0


class AIAssistant:
    """
    AI 助手工具类
    
    提供统一的 AI 能力接口，支持多种后端
    
    使用示例：
    ```python
    ai = AIAssistant()
    
    # 文本生成
    response = ai.complete("请解释什么是微服务架构")
    print(response.content)
    
    # 代码审查
    response = ai.review_code(code, language="python")
    print(response.content)
    
    # 语义分析
    response = ai.analyze_text(text)
    print(response.metadata)
    ```
    """
    
    def __init__(self, provider: str = "trae", config: Dict[str, Any] = None):
        """
        初始化 AI 助手
        
        Args:
            provider: AI 提供商（trae, custom, local）
            config: 配置参数
        """
        self.provider = provider
        self.config = config or {}
        
        # 默认配置：max_tokens=None 表示不限（让模型按自身最大输出能力生成）。
        # 可由 config['max_tokens'] 覆盖为正整数以强约束输出长度。
        self.default_config = {
            'max_tokens': None,  # None = 不限制（默认）；正整数 = 显式上限
            'temperature': 0.7,
            'top_p': 0.9,
            'timeout': 30000,  # 毫秒
            'use_cache': True,
            'fallback_enabled': True
        }
        
        # 合并配置
        self.default_config.update(self.config)
        
        # 响应缓存
        self.cache: Dict[str, AIResponse] = {}
        
        # 使用统计
        self.stats = {
            'total_requests': 0,
            'cache_hits': 0,
            'errors': 0,
            'total_tokens': 0
        }
        
        print(f"✅ AI 助手已初始化 (provider={provider})")
    
    def complete(self, prompt: str, context: str = None, 
                use_cache: bool = True) -> AIResponse:
        """
        文本生成/补全
        
        Args:
            prompt: 提示词
            context: 上下文
            use_cache: 是否使用缓存
            
        Returns:
            AIResponse: AI 响应
        """
        # 检查缓存
        cache_key = f"complete:{hash(prompt)}"
        if use_cache and cache_key in self.cache:
            self.stats['cache_hits'] += 1
            print("✅ 使用缓存的 AI 响应")
            return self.cache[cache_key]
        
        try:
            # 调用 AI
            response = self._call_ai("complete", prompt, context)
            
            # 缓存
            if use_cache:
                self.cache[cache_key] = response
            
            self.stats['total_requests'] += 1
            self.stats['total_tokens'] += response.tokens_used
            
            return response
            
        except Exception as e:
            self.stats['errors'] += 1
            print(f"❌ AI 调用失败：{e}")
            
            # 降级处理
            if self.default_config.get('fallback_enabled', True):
                return self._fallback_complete(prompt, context)
            raise
    
    def analyze_text(self, text: str, analysis_type: str = "general") -> AIResponse:
        """
        文本分析
        
        Args:
            text: 待分析文本
            analysis_type: 分析类型（general, sentiment, topic, entity）
            
        Returns:
            AIResponse: AI 响应
        """
        prompt = f"请分析以下文本（{analysis_type}）：\n\n{text}"
        return self.complete(prompt)
    
    def review_code(self, code: str, language: str = "python",
                   focus: List[str] = None) -> AIResponse:
        """
        代码审查
        
        Args:
            code: 源代码
            language: 编程语言
            focus: 关注点（quality, performance, security, style）
            
        Returns:
            AIResponse: AI 响应
        """
        focus_str = ", ".join(focus) if focus else "code quality"
        prompt = f"""
请审查以下 {language} 代码，重点关注：{focus_str}

```{language}
{code}
```

请提供：
1. 代码质量评估
2. 潜在问题
3. 改进建议
4. 最佳实践
"""
        return self.complete(prompt)
    
    def answer_question(self, question: str, context: str = None) -> AIResponse:
        """
        知识问答
        
        Args:
            question: 问题
            context: 上下文信息
            
        Returns:
            AIResponse: AI 响应
        """
        if context:
            prompt = f"基于以下上下文回答问题：\n\n上下文：{context}\n\n问题：{question}"
        else:
            prompt = question
        
        return self.complete(prompt)
    
    def summarize(self, text: str, length: str = "medium") -> AIResponse:
        """
        文本摘要
        
        Args:
            text: 待摘要文本
            length: 摘要长度（short, medium, long）
            
        Returns:
            AIResponse: AI 响应
        """
        prompt = f"请总结以下文本（{length} 长度）：\n\n{text}"
        return self.complete(prompt)
    
    def translate(self, text: str, target_language: str, 
                 source_language: str = "auto") -> AIResponse:
        """
        翻译
        
        Args:
            text: 待翻译文本
            target_language: 目标语言
            source_language: 源语言（auto 表示自动检测）
            
        Returns:
            AIResponse: AI 响应
        """
        prompt = f"将以下文本从 {source_language} 翻译到 {target_language}：\n\n{text}"
        return self.complete(prompt)
    
    def _call_ai(self, task_type: str, prompt: str, 
                context: str = None) -> AIResponse:
        """
        调用 AI 后端
        
        Args:
            task_type: 任务类型
            prompt: 提示词
            context: 上下文
            
        Returns:
            AIResponse: AI 响应
        """
        start_time = datetime.now()
        
        if self.provider == "trae":
            # Trae AI 助手
            response = self._call_trae_ai(prompt, context)
        elif self.provider == "custom":
            # 自定义 AI 服务
            response = self._call_custom_ai(prompt, context)
        elif self.provider == "local":
            # 本地模型
            response = self._call_local_ai(prompt, context)
        else:
            raise ValueError(f"不支持的 AI 提供商：{self.provider}")
        
        # 计算延迟
        latency = int((datetime.now() - start_time).total_seconds() * 1000)
        response.latency_ms = latency
        
        return response
    
    def _call_trae_ai(self, prompt: str, context: str = None) -> AIResponse:
        """
        调用 Trae AI 助手
        
        脚本层限制说明：Trae IDE 的 AI 助手 API 运行在宿主进程内，
        独立 Python 脚本进程无法直接访问。
        
        因此本方法返回诚实的"不可用"降级响应，真正的 AI 语义理解
        应由宿主 LLM 在提示词层完成（见 SKILL.md 能力实现方式说明）。
        """
        return AIResponse(
            content=(
                "[脚本层不可用] Trae AI 助手 API 位于宿主 IDE 进程内，"
                "独立脚本无法直接调用。请通过宿主 LLM 提示词层使用 AI 能力，"
                "或配置 custom/local 提供商后重试。"
            ),
            confidence=0.0,
            reasoning="脚本进程无法访问宿主 IDE AI API（架构限制，非错误）",
            metadata={
                'provider': 'trae',
                'unavailable': True,
                'unavailable_reason': 'host_process_api_not_accessible_from_script',
                'suggestion': 'use_host_llm_prompt_layer_or_custom_provider'
            },
            tokens_used=0
        )
    
    def _call_custom_ai(self, prompt: str, context: str = None) -> AIResponse:
        """
        调用自定义 AI 服务（OpenAI 兼容 API 格式）
        
        真实 HTTP 实现：使用标准库 urllib 调用 OpenAI 兼容的
        chat/completions 端点（vLLM、OpenAI、DeepSeek 等均兼容）。
        
        配置项（.env 或 config dict）：
        - api_url: API 端点（如 http://localhost:8000/v1/chat/completions）
        - api_key: 认证密钥（可选，本地 vLLM 通常不需要）
        - model: 模型名称（默认 default）
        """
        import urllib.request
        import urllib.error
        
        api_url = self.config.get('api_url') or os.environ.get('AI_API_URL')
        api_key = self.config.get('api_key') or os.environ.get('AI_API_KEY', '')
        model = self.config.get('model') or os.environ.get('AI_MODEL_NAME', 'default')
        timeout_ms = self.default_config.get('timeout', 30000)
        
        if not api_url:
            raise ValueError(
                "自定义 AI 服务未配置 api_url。"
                "请在 config 或环境变量 AI_API_URL 中设置 OpenAI 兼容端点"
            )
        
        # 构建 OpenAI 兼容请求体
        # max_tokens=None（不限）时省略该字段，由模型使用自身默认上限；
        # max_tokens 为正整数时显式包含该字段以强约束输出长度。
        messages = []
        if context:
            messages.append({"role": "system", "content": context})
        messages.append({"role": "user", "content": prompt})

        max_tokens_cfg = self.default_config.get('max_tokens')
        request_body = {
            "model": model,
            "messages": messages,
            "temperature": self.default_config.get('temperature', 0.7),
            "top_p": self.default_config.get('top_p', 0.9),
        }
        # 仅在显式配置正整数上限时携带 max_tokens 字段
        if isinstance(max_tokens_cfg, int) and max_tokens_cfg > 0:
            request_body["max_tokens"] = max_tokens_cfg
        
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        
        req = urllib.request.Request(
            api_url,
            data=json.dumps(request_body).encode('utf-8'),
            headers=headers,
            method='POST'
        )
        
        try:
            with urllib.request.urlopen(req, timeout=timeout_ms / 1000) as resp:
                result = json.loads(resp.read().decode('utf-8'))
            
            choice = result.get('choices', [{}])[0]
            content = choice.get('message', {}).get('content', '')
            usage = result.get('usage', {})
            
            return AIResponse(
                content=content,
                confidence=0.9,
                reasoning=f"来自自定义 AI 服务（{model}）的真实响应",
                metadata={
                    'provider': 'custom',
                    'model': model,
                    'finish_reason': choice.get('finish_reason', '')
                },
                tokens_used=usage.get('total_tokens', 0)
            )
        except urllib.error.URLError as e:
            raise ConnectionError(f"自定义 AI 服务连接失败（{api_url}）：{e}") from e
    
    def _call_local_ai(self, prompt: str, context: str = None) -> AIResponse:
        """
        调用本地模型
        
        真实实现：使用 transformers 加载本地模型进行推理。
        若 transformers 未安装或模型路径无效，诚实抛出异常（由上层降级处理）。
        
        配置项：
        - model_path: 本地模型路径（HuggingFace 格式目录）
        """
        model_path = self.config.get('model_path') or os.environ.get('AI_LOCAL_MODEL_PATH')
        
        if not model_path:
            raise ValueError(
                "本地模型未配置 model_path。"
                "请在 config 或环境变量 AI_LOCAL_MODEL_PATH 中设置模型目录"
            )
        
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
            import torch
        except ImportError as e:
            raise ImportError(
                "本地模型推理需要安装 transformers 和 torch。"
                "请运行：pip install transformers torch"
            ) from e
        
        tokenizer = AutoTokenizer.from_pretrained(model_path)
        model = AutoModelForCausalLM.from_pretrained(
            model_path,
            torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
        )
        
        full_prompt = f"{context}\n\n{prompt}" if context else prompt
        inputs = tokenizer(full_prompt, return_tensors="pt")

        # transformers 不接受 None（max_new_tokens 必须是正整数）。
        # 当 max_tokens=None（不限）时使用 4096 作为本地模型的安全默认上限，
        # 避免无界生成导致 OOM；用户可显式配置更大值。
        max_tokens_cfg = self.default_config.get('max_tokens')
        if isinstance(max_tokens_cfg, int) and max_tokens_cfg > 0:
            max_new_tokens = max_tokens_cfg
        else:
            max_new_tokens = 4096  # 不限时的本地模型安全默认上限

        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                temperature=self.default_config.get('temperature', 0.7),
                top_p=self.default_config.get('top_p', 0.9),
                do_sample=True,
            )
        
        content = tokenizer.decode(
            outputs[0][inputs['input_ids'].shape[1]:],
            skip_special_tokens=True
        )
        
        return AIResponse(
            content=content,
            confidence=0.85,
            reasoning=f"来自本地模型（{model_path}）的真实推理结果",
            metadata={'provider': 'local', 'model_path': model_path},
            tokens_used=int(outputs.shape[1])
        )
    
    def _fallback_complete(self, prompt: str, context: str = None) -> AIResponse:
        """
        降级处理
        
        当 AI 调用失败时使用
        """
        print("⚠️  使用降级响应")
        
        # 简单的基于规则的响应
        return AIResponse(
            content=f"[降级模式] 已收到请求，但 AI 服务不可用。提示词长度：{len(prompt)}",
            confidence=0.3,
            reasoning="AI 服务不可用，使用降级响应",
            metadata={'fallback': True}
        )
    
    def get_stats(self) -> Dict[str, Any]:
        """获取使用统计"""
        return {
            **self.stats,
            'cache_size': len(self.cache),
            'provider': self.provider
        }
    
    def clear_cache(self):
        """清除缓存"""
        self.cache.clear()
        print("✅ AI 缓存已清除")
    
    def explain(self) -> str:
        """解释 AI 助手能力"""
        return f"""
AI 助手能力说明
==============

提供商：{self.provider}
配置：{json.dumps(self.default_config, indent=2)}

核心能力:
- 文本生成和补全
- 语义理解和分析
- 代码审查和建议
- 知识问答
- 文本摘要
- 翻译

使用统计:
- 总请求数：{self.stats['total_requests']}
- 缓存命中：{self.stats['cache_hits']}
- 错误数：{self.stats['errors']}
- 总 token 数：{self.stats['total_tokens']}

缓存大小：{len(self.cache)}
"""


def main():
    """示例用法"""
    print("="*80)
    print("AI 助手工具类示例")
    print("="*80)
    
    # 创建 AI 助手
    ai = AIAssistant(provider="trae")
    
    # 示例 1: 文本生成
    print("\n1. 文本生成示例")
    print("-" * 80)
    response = ai.complete("请解释什么是微服务架构")
    print(f"响应：{response.content}")
    print(f"置信度：{response.confidence}")
    print(f"延迟：{response.latency_ms}ms")
    
    # 示例 2: 代码审查
    print("\n2. 代码审查示例")
    print("-" * 80)
    code = """
def calculate_sum(numbers):
    total = 0
    for num in numbers:
        total += num
    return total
"""
    response = ai.review_code(code, language="python", 
                             focus=["quality", "performance"])
    print(f"审查结果：{response.content}")
    
    # 示例 3: 文本分析
    print("\n3. 文本分析示例")
    print("-" * 80)
    text = "这个产品质量很好，但是价格有点贵"
    response = ai.analyze_text(text, analysis_type="sentiment")
    print(f"分析结果：{response.content}")
    
    # 示例 4: 知识问答
    print("\n4. 知识问答示例")
    print("-" * 80)
    response = ai.answer_question("Python 中什么是装饰器？")
    print(f"回答：{response.content}")
    
    # 显示统计
    print("\n5. 使用统计")
    print("-" * 80)
    stats = ai.get_stats()
    for key, value in stats.items():
        print(f"{key}: {value}")
    
    # 显示能力说明
    print("\n6. 能力说明")
    print("-" * 80)
    print(ai.explain())


if __name__ == "__main__":
    main()
