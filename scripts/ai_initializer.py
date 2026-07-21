#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 助手配置和初始化模块

提供 AI 助手的统一配置、初始化和生命周期管理。

功能：
- 从配置文件加载 AI 配置
- 初始化 AI 组件（语义匹配器、AI 助手）
- 管理 AI 组件的生命周期
- 提供 AI 状态检查和健康监控
"""

import os
import json
import yaml
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field


# ============================================================================
# LLM 输出 token 上限配置语义
# ----------------------------------------------------------------------------
# - None  : 不限制（默认值）。表示让模型按自身最大输出能力生成，
#           适用于长文本生成、代码审查、复杂推理等需要完整输出的场景。
#           实现层会从请求体中省略 max_tokens 字段，由模型使用其默认上限。
# - int>0 : 显式上限（单位：token）。用于强约束输出长度的场景，
#           如角色匹配、摘要、分类等需要短输出的任务。
# - 0/负数: 非法值，from_dict 会抛 ValueError。
# ============================================================================
# 配置示例（YAML）：
#   config:
#     max_tokens: null      # null = 不限制（默认）
#     max_tokens: 4096      # 显式上限 4096 token
# ============================================================================
MAX_TOKENS_UNLIMITED: Optional[int] = None  # 全局常量：不限制 token


@dataclass
class AIConfig:
    """AI 配置（max_tokens=None 表示不限，默认不限）"""
    enabled: bool = True
    provider: str = "trae"
    features: List[str] = field(default_factory=list)
    # None 表示不限 token（默认），int>0 表示显式上限
    max_tokens: Optional[int] = MAX_TOKENS_UNLIMITED
    temperature: float = 0.7
    top_p: float = 0.9
    use_cache: bool = True
    fallback_enabled: bool = True
    timeout_ms: int = 30000
    log_level: str = "info"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AIConfig':
        """从字典创建配置

        max_tokens 语义：
        - 缺省 / null  → None（不限）
        - 正整数        → 显式上限
        - 0 / 负数 / 非数值 → 抛 ValueError
        """
        config = cls()

        if 'ai_integration' in data:
            ai_data = data['ai_integration']
            config.enabled = ai_data.get('enabled', True)
            config.provider = ai_data.get('provider', 'trae')
            config.features = ai_data.get('features', [])

            if 'config' in ai_data:
                cfg = ai_data['config']
                # max_tokens 默认 None（不限）；显式 null 也视为不限
                raw_max_tokens = cfg.get('max_tokens', MAX_TOKENS_UNLIMITED)
                if raw_max_tokens is None:
                    config.max_tokens = MAX_TOKENS_UNLIMITED
                else:
                    try:
                        tokens_int = int(raw_max_tokens)
                    except (TypeError, ValueError) as e:
                        raise ValueError(
                            f"max_tokens 必须是正整数或 null（不限），"
                            f"当前值：{raw_max_tokens!r}"
                        ) from e
                    if tokens_int <= 0:
                        raise ValueError(
                            f"max_tokens 必须是正整数或 null（不限），"
                            f"当前值：{tokens_int}"
                        )
                    config.max_tokens = tokens_int
                config.temperature = cfg.get('temperature', 0.7)
                config.top_p = cfg.get('top_p', 0.9)
                config.use_cache = cfg.get('use_cache', True)
                config.fallback_enabled = cfg.get('fallback_enabled', True)
                config.timeout_ms = cfg.get('timeout_ms', 30000)

        return config


class AIInitializer:
    """
    AI 初始化器
    
    负责 AI 组件的初始化和配置管理
    """
    
    def __init__(self, skill_root: str = "."):
        """
        初始化 AI 初始化器
        
        Args:
            skill_root: 技能根目录
        """
        self.skill_root = Path(skill_root)
        self.config: Optional[AIConfig] = None
        self.ai_assistant = None
        self.semantic_matcher = None
        self.initialized = False
        
        # 组件状态
        self.status = {
            'ai_enabled': False,
            'assistant_ready': False,
            'matcher_ready': False,
            'cache_enabled': False,
            'fallback_enabled': False
        }
    
    def load_config(self, manifest_file: str = "skill-manifest.yaml") -> bool:
        """
        加载 AI 配置
        
        Args:
            manifest_file: 技能清单文件名
            
        Returns:
            bool: 加载是否成功
        """
        manifest_path = self.skill_root / manifest_file
        
        if not manifest_path.exists():
            print(f"⚠️  技能清单不存在：{manifest_path}")
            return False
        
        try:
            with open(manifest_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            
            # 解析配置
            self.config = AIConfig.from_dict(data)
            
            # 更新状态
            self.status['ai_enabled'] = self.config.enabled
            self.status['cache_enabled'] = self.config.use_cache
            self.status['fallback_enabled'] = self.config.fallback_enabled
            
            print(f"✅ AI 配置加载成功")
            print(f"   提供商：{self.config.provider}")
            print(f"   特性：{', '.join(self.config.features)}")
            print(f"   缓存：{'启用' if self.config.use_cache else '禁用'}")
            
            return True
            
        except Exception as e:
            print(f"❌ 加载 AI 配置失败：{e}")
            return False
    
    def initialize(self) -> bool:
        """
        初始化 AI 组件
        
        Returns:
            bool: 初始化是否成功
        """
        if not self.config:
            print("❌ 配置未加载，无法初始化")
            return False
        
        if not self.config.enabled:
            print("ℹ️  AI 功能已禁用")
            return True
        
        print("\n" + "="*80)
        print("初始化 AI 组件")
        print("="*80)
        
        try:
            # 1. 初始化 AI 助手
            if 'ai_assistant' in self.config.features or True:  # 默认总是初始化
                self._init_ai_assistant()
            
            # 2. 初始化语义匹配器
            if 'semantic_matching' in self.config.features or True:  # 默认总是初始化
                self._init_semantic_matcher()
            
            self.initialized = True
            
            # 3. 显示状态
            self.print_status()
            
            print("\n✅ AI 组件初始化完成")
            return True
            
        except Exception as e:
            print(f"❌ AI 组件初始化失败：{e}")
            return False
    
    def _init_ai_assistant(self):
        """初始化 AI 助手"""
        try:
            print("\n1. 初始化 AI 助手...")
            
            from ai_assistant import AIAssistant
            
            # 准备配置
            ai_config = {
                'max_tokens': self.config.max_tokens,
                'temperature': self.config.temperature,
                'top_p': self.config.top_p,
                'timeout': self.config.timeout_ms,
                'use_cache': self.config.use_cache,
                'fallback_enabled': self.config.fallback_enabled
            }
            
            # 创建实例
            self.ai_assistant = AIAssistant(
                provider=self.config.provider,
                config=ai_config
            )
            
            self.status['assistant_ready'] = True
            print("   ✅ AI 助手已就绪")
            
        except ImportError as e:
            print(f"   ⚠️  AI 助手模块不可用：{e}")
            self.status['assistant_ready'] = False
        except Exception as e:
            print(f"   ❌ AI 助手初始化失败：{e}")
            self.status['assistant_ready'] = False
    
    def _init_semantic_matcher(self):
        """初始化语义匹配器"""
        try:
            print("\n2. 初始化语义匹配器...")
            
            from ai_semantic_matcher import AISemanticMatcher
            
            # 创建实例（如果有 AI 助手，传入；否则使用模拟）
            if self.ai_assistant:
                self.semantic_matcher = AISemanticMatcher(
                    ai_client=self.ai_assistant
                )
            else:
                self.semantic_matcher = AISemanticMatcher()
            
            self.status['matcher_ready'] = True
            print("   ✅ 语义匹配器已就绪")
            
        except ImportError as e:
            print(f"   ⚠️  语义匹配器模块不可用：{e}")
            self.status['matcher_ready'] = False
        except Exception as e:
            print(f"   ❌ 语义匹配器初始化失败：{e}")
            self.status['matcher_ready'] = False
    
    def print_status(self):
        """打印状态"""
        print("\n" + "="*80)
        print("AI 组件状态")
        print("="*80)
        
        for key, value in self.status.items():
            status = "✅" if value else "⚠️ " if key != 'ai_enabled' else "❌"
            print(f"{status} {key}: {value}")
        
        print("="*80)
    
    def get_ai_assistant(self):
        """获取 AI 助手实例"""
        if not self.initialized:
            raise RuntimeError("AI 组件未初始化")
        
        if not self.ai_assistant:
            raise RuntimeError("AI 助手未就绪")
        
        return self.ai_assistant
    
    def get_semantic_matcher(self):
        """获取语义匹配器实例"""
        if not self.initialized:
            raise RuntimeError("AI 组件未初始化")
        
        if not self.semantic_matcher:
            raise RuntimeError("语义匹配器未就绪")
        
        return self.semantic_matcher
    
    def shutdown(self):
        """关闭 AI 组件"""
        print("\n关闭 AI 组件...")
        
        # 清理缓存
        if self.ai_assistant:
            self.ai_assistant.clear_cache()
        
        # 重置状态
        self.initialized = False
        self.ai_assistant = None
        self.semantic_matcher = None
        
        print("✅ AI 组件已关闭")
    
    def health_check(self) -> Dict[str, Any]:
        """
        健康检查
        
        Returns:
            Dict: 健康状态
        """
        return {
            'initialized': self.initialized,
            'config_loaded': self.config is not None,
            'assistant_ready': self.status['assistant_ready'],
            'matcher_ready': self.status['matcher_ready'],
            'cache_size': len(self.ai_assistant.cache) if self.ai_assistant else 0,
            'stats': self.ai_assistant.get_stats() if self.ai_assistant else {}
        }


# 全局 AI 初始化器实例
_ai_initializer: Optional[AIInitializer] = None


def get_ai_initializer(skill_root: str = ".") -> AIInitializer:
    """
    获取全局 AI 初始化器实例
    
    Args:
        skill_root: 技能根目录
        
    Returns:
        AIInitializer: 初始化器实例
    """
    global _ai_initializer
    
    if _ai_initializer is None:
        _ai_initializer = AIInitializer(skill_root)
    
    return _ai_initializer


def initialize_ai(skill_root: str = ".", force: bool = False) -> bool:
    """
    初始化 AI 组件（全局）
    
    Args:
        skill_root: 技能根目录
        force: 是否强制重新初始化
        
    Returns:
        bool: 初始化是否成功
    """
    global _ai_initializer
    
    if _ai_initializer and _ai_initializer.initialized and not force:
        print("ℹ️  AI 组件已初始化，跳过")
        return True
    
    _ai_initializer = AIInitializer(skill_root)
    
    # 加载配置
    if not _ai_initializer.load_config():
        return False
    
    # 初始化组件
    return _ai_initializer.initialize()


def get_ai_assistant():
    """获取 AI 助手实例（全局）"""
    if _ai_initializer is None:
        raise RuntimeError("AI 组件未初始化")
    return _ai_initializer.get_ai_assistant()


def get_semantic_matcher():
    """获取语义匹配器实例（全局）"""
    if _ai_initializer is None:
        raise RuntimeError("AI 组件未初始化")
    return _ai_initializer.get_semantic_matcher()


def main():
    """示例用法"""
    print("="*80)
    print("AI 助手配置和初始化示例")
    print("="*80)
    
    # 创建初始化器
    initializer = AIInitializer(skill_root="..")
    
    # 加载配置
    if not initializer.load_config():
        return 1
    
    # 初始化组件
    if not initializer.initialize():
        return 1
    
    # 健康检查
    health = initializer.health_check()
    print("\n健康检查:")
    for key, value in health.items():
        print(f"  {key}: {value}")
    
    # 使用 AI 助手
    if initializer.ai_assistant:
        print("\n使用 AI 助手:")
        response = initializer.ai_assistant.complete("什么是微服务？")
        print(f"响应：{response.content}")
    
    # 关闭
    initializer.shutdown()
    
    return 0


if __name__ == "__main__":
    exit(main())
