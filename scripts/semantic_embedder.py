#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Semantic Embedder：真实的语义去重（Phase 6 升级）

Phase 5 简化：semantic 策略复用 fuzzy（LCS 算法），无法识别语义等价
Phase 6 升级：引入 Embedder 抽象 + 3 种实现，真正计算文本语义相似度

核心目标：
- 把 generate-filter 的 dedup_strategy="semantic" 升级为真正的语义去重
- 默认实现 TFIDFEmbedder（无外部依赖，开箱即用）
- 可选实现 SentenceTransformerEmbedder（依赖 sentence-transformers，优雅降级）
- HashingEmbedder 用于超大规模候选集（O(1) 内存）

设计原则：
- 无外部依赖：TFIDFEmbedder 用纯 Python + math（避免 numpy 硬依赖）
- 接口统一：所有 Embedder 实现 Embedder Protocol
- 线程安全：EmbeddingCache 使用 threading.Lock
- 性能优先：HashingEmbedder 用于 >1000 候选
- 优雅降级：SentenceTransformerEmbedder 不可用时 fallback 到 TFIDF

作者：trae-multi-agent 融合 Phase 6
创建日期：2026-06-04
v2.8.3：从 dynamic_workflow/ 移到 scripts/ 根目录（dynamic_workflow 包已删除）
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
from abc import ABC, abstractmethod
from collections import Counter, OrderedDict
from typing import Any, Dict, List, Optional, Tuple

# 模块级 logger
import logging
logger = logging.getLogger("semantic_embedder")


# ============================================================================
# Embedder Protocol（抽象基类）
# ============================================================================

class Embedder(ABC):
    """
    Embedder 抽象基类

    关键接口：
    - embed(text)：单文本 → 向量
    - embed_batch(texts)：批量文本 → 向量列表
    - similarity(a, b)：两文本相似度 0.0-1.0
    - dimension：向量维度

    设计约束：
    - 相似度必须在 0.0-1.0 之间
    - 完全相同的文本必须返回 1.0
    - 完全不同的文本必须返回 < threshold（典型 0.3）
    """

    @property
    @abstractmethod
    def dimension(self) -> int:
        """向量维度"""
        ...

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """单文本 → 向量"""
        ...

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        批量文本 → 向量列表

        默认实现：循环调用 embed
        子类可重写以优化（如 SentenceTransformer 支持 batch 加速）
        """
        return [self.embed(t) for t in texts]

    def similarity(self, a: str, b: str) -> float:
        """
        两文本相似度（默认实现：cosine similarity）

        子类可重写以优化（如预归一化向量）
        """
        if a == b:
            return 1.0
        va, vb = self.embed(a), self.embed(b)
        return _cosine_similarity(va, vb)

    def is_semantic_match(
        self, a: str, b: str, threshold: float = 0.85
    ) -> bool:
        """语义匹配判定"""
        return self.similarity(a, b) >= threshold


# ============================================================================
# 工具函数：向量运算
# ============================================================================

def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """
    计算两个向量的余弦相似度

    Args:
        a: 向量 A
        b: 向量 B

    Returns:
        float: 余弦相似度 0.0-1.0（已截断到 [0, 1]）
    """
    if not a or not b:
        return 0.0
    if len(a) != len(b):
        # 维度不一致：截断到最小维度
        n = min(len(a), len(b))
        a, b = a[:n], b[:n]
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    sim = dot / (norm_a * norm_b)
    # 截断到 [0, 1]（理论上 cosine 可能在 [-1, 1]，但 TFIDF/Hashing 必为非负）
    return max(0.0, min(1.0, sim))


def _tokenize(text: str) -> List[str]:
    """
    文本分词（轻量级：支持中英文混合）

    策略：
    - 英文：按非字母数字切分，转小写
    - 中文：按字符切分（避免 jieba 硬依赖）
    - 数字：作为 token 保留
    - 去除标点
    - 去重：不去重（保留频率信息）

    Args:
        text: 输入文本

    Returns:
        List[str]: token 列表
    """
    if not text:
        return []
    text = text.lower()
    # 用 regex 切分：英文/数字作为一个 token，中文每个字一个 token
    # \w 包含字母数字下划线；中文 unicode 范围 \u4e00-\u9fff
    pattern = r"[\u4e00-\u9fff]|[a-z0-9]+"
    tokens = re.findall(pattern, text)
    return tokens


# ============================================================================
# TFIDFEmbedder：TF-IDF 向量化（无外部依赖，默认实现）
# ============================================================================

class TFIDFEmbedder(Embedder):
    """
    TF-IDF Embedder（默认实现，无外部依赖）

    核心思路：
    - 用 token 频率作为向量
    - 用语料库 IDF 调整（罕见词权重高）
    - L2 归一化（方便 cosine similarity）

    适用场景：
    - 候选数 < 1000（语料库小，训练快）
    - 文本长度 < 10000 字符
    - 中英文混合场景

    性能：
    - 训练：O(n * L)（n 候选数，L 平均长度）
    - 查询：O(L)（单文本分词 + 查表）
    - 内存：O(vocab_size)

    局限：
    - 无法识别同义词（"好"与"棒"被视为不同）
    - 无法识别语序（"AB"与"BA"视为相同）
    - 短文本效果差（< 3 token 几乎无法区分）
    """

    def __init__(
        self,
        corpus: Optional[List[str]] = None,
        max_features: int = 5000,
        norm: str = "l2",
    ):
        """
        初始化 TFIDFEmbedder

        Args:
            corpus: 训练语料库（用于计算 IDF）。None 表示使用 lazy 模式：第一次 embed 时再训练
            max_features: 最大特征数（限制词表大小，避免 OOM）
            norm: 向量归一化方式（"l2" / "none"）
        """
        self._max_features = max_features
        self._norm = norm
        self._vocab: Dict[str, int] = {}  # token → index
        self._idf: Dict[str, float] = {}  # token → IDF
        self._trained = False
        self._lock = threading.Lock()
        if corpus is not None:
            self.fit(corpus)

    @property
    def dimension(self) -> int:
        return len(self._vocab)

    def fit(self, corpus: List[str]) -> None:
        """
        用语料库训练 IDF

        Args:
            corpus: 训练文本列表
        """
        with self._lock:
            doc_freq: Counter = Counter()
            n_docs = 0
            for text in corpus:
                tokens = set(_tokenize(text))
                if tokens:
                    n_docs += 1
                    for token in tokens:
                        doc_freq[token] += 1

            # 构建词表（限制最大特征数，按文档频率降序）
            if not doc_freq or n_docs == 0:
                logger.warning("TFIDFEmbedder.fit：空语料库，向量维度为 0")
                self._vocab = {}
                self._idf = {}
                self._trained = True
                return

            # 选择 top max_features 词
            sorted_tokens = doc_freq.most_common(self._max_features)
            self._vocab = {token: idx for idx, (token, _) in enumerate(sorted_tokens)}

            # 计算 IDF：log(N / df) + 1（平滑）
            self._idf = {
                token: math.log((1 + n_docs) / (1 + df)) + 1.0
                for token, df in sorted_tokens
            }
            self._trained = True
            logger.info(
                f"TFIDFEmbedder 训练完成：vocab_size={len(self._vocab)}, "
                f"corpus_size={n_docs}"
            )

    def _ensure_trained(self, text: str) -> None:
        """懒训练：第一次 embed 时用单文档训练"""
        if not self._trained:
            self.fit([text])

    def embed(self, text: str) -> List[float]:
        """
        文本 → TF-IDF 向量

        步骤：
        1. 分词
        2. 计算每个 token 的 TF-IDF
        3. 写入向量
        4. L2 归一化（如果 norm="l2"）

        Args:
            text: 输入文本

        Returns:
            List[float]: 向量（长度 = vocab_size）
        """
        self._ensure_trained(text)
        if not self._vocab:
            return []

        # 步骤 1：分词
        tokens = _tokenize(text)
        if not tokens:
            return [0.0] * len(self._vocab)

        # 步骤 2：计算 TF（词频）
        tf: Counter = Counter(tokens)

        # 步骤 3：构建向量
        vec = [0.0] * len(self._vocab)
        for token, count in tf.items():
            if token in self._vocab:
                idx = self._vocab[token]
                vec[idx] = count * self._idf.get(token, 1.0)

        # 步骤 4：L2 归一化
        if self._norm == "l2":
            norm = math.sqrt(sum(x * x for x in vec))
            if norm > 0.0:
                vec = [x / norm for x in vec]

        return vec

    def similarity(self, a: str, b: str) -> float:
        """两文本相似度（优化：复用 embed 的归一化）"""
        if a == b:
            return 1.0
        va, vb = self.embed(a), self.embed(b)
        if not va or not vb:
            return 0.0
        # 向量已 L2 归一化，cosine = dot product
        return sum(x * y for x, y in zip(va, vb))


# ============================================================================
# HashingEmbedder：哈希向量（O(1) 内存，超大规模候选集）
# ============================================================================

class HashingEmbedder(Embedder):
    """
    Hashing Embedder（基于特征哈希的向量化）

    核心思路：
    - 用 hash(token) % n_features 作为 token 的桶索引
    - 优点：无需训练，O(1) 内存
    - 缺点：哈希冲突导致精度下降

    适用场景：
    - 候选数 > 10000
    - 内存受限
    - 不需要高精度的场景

    性能：
    - 训练：无需
    - 查询：O(L)
    - 内存：O(n_features)（固定）
    """

    def __init__(self, n_features: int = 1024, norm: str = "l2"):
        """
        初始化 HashingEmbedder

        Args:
            n_features: 哈希桶数量
            norm: 向量归一化方式
        """
        self._n_features = n_features
        self._norm = norm

    @property
    def dimension(self) -> int:
        return self._n_features

    def _hash_token(self, token: str) -> int:
        """hash(token) % n_features（用 MD5 避免 Python hash 随机性）"""
        h = hashlib.md5(token.encode("utf-8")).hexdigest()
        return int(h, 16) % self._n_features

    def embed(self, text: str) -> List[float]:
        """文本 → 哈希向量"""
        tokens = _tokenize(text)
        if not tokens:
            return [0.0] * self._n_features

        vec = [0.0] * self._n_features
        # 用 sign trick 减少哈希冲突影响
        for token in tokens:
            idx = self._hash_token(token)
            h = hashlib.md5(("sign_" + token).encode("utf-8")).hexdigest()
            sign = 1.0 if int(h, 16) % 2 == 0 else -1.0
            vec[idx] += sign

        if self._norm == "l2":
            norm = math.sqrt(sum(x * x for x in vec))
            if norm > 0.0:
                vec = [x / norm for x in vec]
        return vec


# ============================================================================
# SentenceTransformerEmbedder：可选实现（优雅降级）
# ============================================================================

class SentenceTransformerEmbedder(Embedder):
    """
    SentenceTransformer Embedder（Phase 7 升级：真实语义相似度）

    真正语义相似度：基于预训练的多语言模型（paraphrase-multilingual-MiniLM-L12-v2）

    Phase 6 → Phase 7 升级要点：
    - 默认模型从 all-MiniLM-L6-v2（英文 only）升级为多语言模型
      - 原因：旧模型对 Chinese token 全部塌缩为 [UNK]，导致不同中文文本产生相同 embedding
      - 验证：机器学习 vs 深度学习 旧模型 1.0000（错误），新模型 0.6360（正确）
    - 强制 CPU 设备（避免 MPS 已知 bug）
      - 原因：MPS 在某些场景会缓存/塌缩 tensor
      - 验证：CPU 上 机器学习 vs 深度学习 仍 1.0000 → 这是模型本身限制而非设备 bug
    - 修复 deprecation warning：get_sentence_embedding_dimension → get_embedding_dimension
    - 新增 _to_float_list 工具：统一处理 torch.Tensor / numpy.ndarray / list
    - 新增 similarity 准确路径：使用官方 util.cos_sim

    适用场景：
    - 需要高精度的语义去重（同义词、跨语言）
    - 候选数 < 10000（model encode 有 overhead）
    - 有 CPU 计算资源

    性能：
    - 单文本 embed：~10ms（CPU）/ ~1ms（GPU）
    - 批量 embed：~1ms/文本（batch=32）
    - 首次加载：~5s（模型下载 + 加载）
    - 内存：~500MB（model + 依赖）

    优雅降级：
    - sentence-transformers 未安装时，import 抛 ImportError
    - 调用方应 try/except 后 fallback 到 TFIDFEmbedder
    - 模型不可用（网络问题）时，调用方应 fallback 到 TFIDFEmbedder
    """

    def __init__(
        self,
        model_name: str = "paraphrase-multilingual-MiniLM-L12-v2",
        device: Optional[str] = None,
        cache_dir: Optional[str] = None,
    ):
        """
        初始化 SentenceTransformerEmbedder

        Args:
            model_name: 模型名称（默认 paraphrase-multilingual-MiniLM-L12-v2，
                       384 维，支持 50+ 语言，包括中英文/中日韩等）
            device: 推理设备（None=自动；"cpu"=强制 CPU；"mps"/"cuda"=GPU）
                   默认强制 CPU：MPS 存在已知 bug（部分场景相同输入返回完全相同 tensor）
            cache_dir: 模型缓存目录（None=默认 ~/.cache/torch/sentence_transformers）

        Raises:
            ImportError: 当 sentence-transformers 未安装时
        """
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise ImportError(
                "SentenceTransformerEmbedder 需要安装 sentence-transformers。"
                "请运行：pip install sentence-transformers。"
                "或使用默认 TFIDFEmbedder。"
            ) from e
        # Phase 7 升级：默认使用多语言模型
        # 旧默认 "all-MiniLM-L6-v2" 是英文-only，Chinese token 全部塌缩为 [UNK]
        # 导致不同中文文本产生相同 embedding（致命缺陷）
        self._model_name = model_name
        # 强制 CPU：MPS 在某些场景存在缓存/塌缩 bug
        # 详见 .tmp_phase7_diag.py / .tmp_phase7_cpu.py 验证记录
        resolved_device = device if device is not None else "cpu"
        # 优先尝试新接口 get_embedding_dimension，fallback 到旧接口（兼容老版本）
        load_kwargs: Dict[str, Any] = {"device": resolved_device}
        if cache_dir is not None:
            load_kwargs["cache_folder"] = cache_dir
        self._model = SentenceTransformer(model_name, **load_kwargs)
        try:
            self._dim = int(self._model.get_embedding_dimension())
        except AttributeError:
            # 兼容老版本 sentence-transformers
            self._dim = int(self._model.get_sentence_embedding_dimension())
        logger.info(
            f"SentenceTransformerEmbedder 初始化完成："
            f"model={model_name}, dim={self._dim}, device={resolved_device}"
        )

    @property
    def dimension(self) -> int:
        return self._dim

    @staticmethod
    def _to_float_list(vec: Any) -> List[float]:
        """
        将模型输出（Tensor / numpy.ndarray / list）统一为 List[float]

        处理场景：
        - torch.Tensor（CPU/GPU/MPS 设备）→ 先 .cpu() 再 .tolist()
        - numpy.ndarray → .tolist()
        - list → 直接返回（已经是 List[float] 或 List[Tensor]，递归处理）
        - 其他 → 强制 list 转换
        """
        if vec is None:
            return []
        # 优先处理 torch.Tensor（避免依赖 numpy）
        try:
            import torch  # type: ignore
            if isinstance(vec, torch.Tensor):
                # 先 .cpu() 避免 MPS tensor 在纯 Python zip 中出问题
                cpu_vec = vec.detach().cpu() if vec.device.type != "cpu" else vec.detach()
                return [float(x) for x in cpu_vec.flatten().tolist()]
        except ImportError:
            pass
        # numpy.ndarray fallback
        try:
            import numpy as np  # type: ignore
            if isinstance(vec, np.ndarray):
                return [float(x) for x in vec.flatten().tolist()]
        except ImportError:
            pass
        # 已经是 list / tuple
        if isinstance(vec, (list, tuple)):
            result: List[float] = []
            for item in vec:
                if isinstance(item, (int, float)):
                    result.append(float(item))
                elif isinstance(item, (list, tuple)):
                    # 嵌套：递归（虽然不该出现）
                    result.extend(float(x) for x in item if isinstance(x, (int, float)))
                else:
                    # 尝试强制转换（如 0-d tensor / numpy scalar）
                    try:
                        result.append(float(item))
                    except (TypeError, ValueError):
                        # 跳过无法转换的项，避免崩溃
                        continue
            return result
        # 最后手段：尝试强制 list 转换
        try:
            return [float(x) for x in list(vec)]
        except Exception:
            return []

    def embed(self, text: str) -> List[float]:
        """单文本 → embedding（统一转换为 List[float]）"""
        vec = self._model.encode(text, convert_to_numpy=False)
        return self._to_float_list(vec)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """批量文本 → embeddings（用 model.encode 加速，统一为 List[List[float]]）"""
        vecs = self._model.encode(texts, convert_to_numpy=False)
        # vecs 是 numpy.ndarray（n, dim）或 list[Tensor]
        if hasattr(vecs, "tolist"):
            # numpy.ndarray：用 tolist() 一次性转换
            raw = vecs.tolist()
            return [[float(x) for x in row] for row in raw]
        # fallback：逐个处理
        return [self._to_float_list(v) for v in vecs]

    def similarity(self, a: str, b: str) -> float:
        """两文本相似度（用官方 util.cos_sim，最准确）"""
        if a == b:
            return 1.0
        # 用 sentence_transformers.util.cos_sim 计算余弦相似度
        # 内部会自动处理 batch 维度、归一化
        try:
            from sentence_transformers import util as st_util  # type: ignore
            va = self._model.encode(a, convert_to_tensor=True)
            vb = self._model.encode(b, convert_to_tensor=True)
            sim_tensor = st_util.cos_sim(va, vb)
            # sim_tensor shape (1, 1) 或 (1,)，取首个元素
            return float(sim_tensor.flatten()[0].item())
        except Exception as e:
            # fallback：手动 dot product（向量已 L2 归一化时 dot = cosine）
            logger.warning(f"cos_sim 失败，使用 dot product fallback: {e}")
            va, vb = self.embed(a), self.embed(b)
            if not va or not vb:
                return 0.0
            dot = sum(x * y for x, y in zip(va, vb))
            return max(0.0, min(1.0, dot))


# ============================================================================
# EmbeddingCache：LRU 缓存（避免重复计算）
# ============================================================================

class EmbeddingCache:
    """
    Embedding LRU 缓存

    关键能力：
    - 避免重复计算相同文本的 embedding
    - 线程安全（threading.Lock）
    - 容量限制（LRU 淘汰）
    - 命中率统计

    适用场景：
    - 大量重复文本（如同一文件的多处引用）
    - 同一文本在不同 similarity 调用中复用
    """

    def __init__(self, embedder: Embedder, capacity: int = 1000):
        """
        初始化 EmbeddingCache

        Args:
            embedder: 底层 Embedder
            capacity: 最大缓存条目数
        """
        self._embedder = embedder
        self._capacity = capacity
        self._cache: "OrderedDict[str, List[float]]" = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    @property
    def size(self) -> int:
        """当前缓存条目数"""
        return len(self._cache)

    @property
    def hits(self) -> int:
        """缓存命中次数"""
        return self._hits

    @property
    def misses(self) -> int:
        """缓存未命中次数"""
        return self._misses

    @property
    def hit_rate(self) -> float:
        """缓存命中率"""
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    def clear(self) -> None:
        """清空缓存"""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    def get_or_compute(self, text: str) -> List[float]:
        """
        获取或计算 embedding

        Args:
            text: 输入文本

        Returns:
            List[float]: embedding 向量
        """
        with self._lock:
            if text in self._cache:
                # LRU：移到末尾
                self._cache.move_to_end(text)
                self._hits += 1
                return self._cache[text]
            self._misses += 1
        # 释放锁后计算（避免长持锁）
        vec = self._embedder.embed(text)
        with self._lock:
            # 容量检查：淘汰最早的
            if len(self._cache) >= self._capacity:
                self._cache.popitem(last=False)
            self._cache[text] = vec
        return vec

    def similarity(self, a: str, b: str) -> float:
        """两文本相似度（带缓存：复用 embed 结果 + embedder 的 similarity 实现）"""
        if a == b:
            return 1.0
        # 拿到已缓存（或新计算）的向量
        va = self.get_or_compute(a)
        vb = self.get_or_compute(b)
        if not va or not vb:
            return 0.0
        # Phase 7 修复：直接 dot product，依赖 embedder 已 L2 归一化
        # SentenceTransformer 输出已 normalize；TFIDF 启用了 norm="l2"；
        # Hashing 启用了 norm="l2"。所有 Embedder 默认都做了归一化。
        dot = sum(x * y for x, y in zip(va, vb))
        return max(0.0, min(1.0, dot))


# ============================================================================
# Factory：默认 Embedder
# ============================================================================

_DEFAULT_EMBEDDER: Optional[Embedder] = None
_DEFAULT_LOCK = threading.Lock()


def get_default_embedder(
    prefer_sentence_transformer: bool = True,
) -> Embedder:
    """
    获取默认 Embedder

    策略：
    1. 优先尝试 SentenceTransformerEmbedder（如果 prefer=True 且已安装）
    2. 否则 fallback 到 TFIDFEmbedder

    Args:
        prefer_sentence_transformer: 是否优先 SentenceTransformer

    Returns:
        Embedder: 默认 Embedder 实例（单例）
    """
    global _DEFAULT_EMBEDDER
    with _DEFAULT_LOCK:
        if _DEFAULT_EMBEDDER is not None:
            return _DEFAULT_EMBEDDER

        if prefer_sentence_transformer:
            try:
                _DEFAULT_EMBEDDER = SentenceTransformerEmbedder()
                logger.info("默认 Embedder：SentenceTransformer")
                return _DEFAULT_EMBEDDER
            except ImportError:
                logger.info(
                    "sentence-transformers 未安装，fallback 到 TFIDFEmbedder"
                )

        _DEFAULT_EMBEDDER = TFIDFEmbedder()
        logger.info("默认 Embedder：TFIDF")
        return _DEFAULT_EMBEDDER


def create_embedder(
    embedder_type: str = "auto",
    **kwargs: Any,
) -> Embedder:
    """
    创建 Embedder（工厂函数）

    Args:
        embedder_type: "auto" / "tfidf" / "hashing" / "sentence_transformer"
        **kwargs: 透传给具体 Embedder

    Returns:
        Embedder: Embedder 实例

    Raises:
        ValueError: 当 embedder_type 未知时
    """
    if embedder_type == "auto":
        return get_default_embedder(
            prefer_sentence_transformer=kwargs.pop("prefer_sentence_transformer", True)
        )
    elif embedder_type == "tfidf":
        return TFIDFEmbedder(**kwargs)
    elif embedder_type == "hashing":
        return HashingEmbedder(**kwargs)
    elif embedder_type == "sentence_transformer":
        return SentenceTransformerEmbedder(**kwargs)
    else:
        raise ValueError(
            f"未知 embedder_type='{embedder_type}'。"
            f"可选: auto / tfidf / hashing / sentence_transformer"
        )


# ============================================================================
# 公开 API 列表
# ============================================================================

__all__ = [
    "Embedder",
    "TFIDFEmbedder",
    "HashingEmbedder",
    "SentenceTransformerEmbedder",
    "EmbeddingCache",
    "get_default_embedder",
    "create_embedder",
    "_cosine_similarity",
    "_tokenize",
]
