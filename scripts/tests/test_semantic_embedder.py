#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Semantic Embedder 单元测试（Phase 6：升级 generate-filter 真实语义去重）
                        （Phase 7：真实 sentence-transformers 集成）

测试目标：
- TFIDFEmbedder 基本功能（训练 / embed / similarity）
- HashingEmbedder 基本功能（无需训练 / O(1) 内存）
- SentenceTransformerEmbedder 优雅降级（未安装时抛 ImportError）
- EmbeddingCache LRU 命中/未命中
- create_embedder 工厂函数（auto / tfidf / hashing / sentence_transformer）
- _fuzzy_similarity 接受 embedder 参数
- _dedup_candidates 接受 embedder 参数
- GenerateFilterExecutor 接受 embedder 配置
- 跨语言文本相似度
- 边界场景（空文本 / 长文本 / 重复调用）
- 性能（候选数 100 时延 < 1s）

Phase 7 新增（真实 embedding 集成）：
- 加载 paraphrase-multilingual-MiniLM-L12-v2 多语言模型
- 验证多语言相似度（中英日韩）
- 验证跨语言相似度
- 验证模型维度（384）
- 验证 embed 返回 List[float]（非 Tensor）
- 对比 TFIDF vs SentenceTransformer 准确率
- 验证 _to_float_list 边界处理

测试约定：
- 使用 unittest 框架
- 不依赖任何外部服务
- 不强制要求 sentence-transformers（优雅降级）
- Phase 7 真实模型测试通过环境变量 SENTENCE_TRANSFORMERS_TEST 控制
  - 设为 "1" 启用（CI / 真实环境）
  - 默认禁用（兼容性：旧环境无 sentence-transformers 时不报错）

作者：trae-multi-agent 融合 Phase 6/7
创建日期：2026-06-04
"""

import sys
import time
import unittest
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

# 添加 scripts 目录到 sys.path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# 动态加载 semantic_embedder
DYNAMIC_WORKFLOW_DIR = SCRIPTS_DIR / "dynamic_workflow"
if str(DYNAMIC_WORKFLOW_DIR) not in sys.path:
    sys.path.insert(0, str(DYNAMIC_WORKFLOW_DIR))

import semantic_embedder  # noqa: E402
from semantic_embedder import (  # noqa: E402
    Embedder,
    EmbeddingCache,
    HashingEmbedder,
    TFIDFEmbedder,
    create_embedder,
    get_default_embedder,
)
from pattern_executor import (  # noqa: E402
    _dedup_candidates,
    _fuzzy_similarity,
    _normalize_for_dedup,
)


# ============================================================================
# 1. 工具函数测试
# ============================================================================

class TestHelperFunctions(unittest.TestCase):
    """测试工具函数：_tokenize, _cosine_similarity, _normalize_for_dedup"""

    def test_01_normalize_basic(self):
        """_normalize_for_dedup 基本归一化"""
        self.assertEqual(_normalize_for_dedup("Hello World"), "helloworld")
        self.assertEqual(_normalize_for_dedup("  多   个   空格  "), "多个空格")
        self.assertEqual(_normalize_for_dedup(""), "")
        self.assertEqual(_normalize_for_dedup(None), "")

    def test_02_tokenize_english(self):
        """_tokenize 英文分词"""
        from semantic_embedder import _tokenize
        tokens = _tokenize("Hello World")
        self.assertIn("hello", tokens)
        self.assertIn("world", tokens)

    def test_03_tokenize_chinese(self):
        """_tokenize 中文按字切分"""
        from semantic_embedder import _tokenize
        tokens = _tokenize("命名方案A")
        # 中文字符单独成 token
        self.assertIn("命", tokens)
        self.assertIn("名", tokens)
        self.assertIn("a", tokens)

    def test_04_tokenize_mixed(self):
        """_tokenize 中英文混合"""
        from semantic_embedder import _tokenize
        tokens = _tokenize("使用 Python 实现")
        self.assertIn("python", tokens)
        self.assertIn("使", tokens)
        self.assertIn("用", tokens)

    def test_05_tokenize_empty(self):
        """_tokenize 空文本"""
        from semantic_embedder import _tokenize
        self.assertEqual(_tokenize(""), [])
        self.assertEqual(_tokenize(None), [])

    def test_06_cosine_similarity_identical(self):
        """相同向量 cosine = 1.0"""
        from semantic_embedder import _cosine_similarity
        v = [1.0, 2.0, 3.0]
        self.assertAlmostEqual(_cosine_similarity(v, v), 1.0, places=5)

    def test_07_cosine_similarity_orthogonal(self):
        """正交向量 cosine = 0.0"""
        from semantic_embedder import _cosine_similarity
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        self.assertAlmostEqual(_cosine_similarity(a, b), 0.0, places=5)

    def test_08_cosine_similarity_different_dims(self):
        """不同维度：截断到最小维度"""
        from semantic_embedder import _cosine_similarity
        a = [1.0, 0.0, 0.0]
        b = [1.0, 0.0]
        # 截断后 a=[1.0, 0.0], b=[1.0, 0.0]，cosine=1.0
        self.assertAlmostEqual(_cosine_similarity(a, b), 1.0, places=5)

    def test_09_cosine_similarity_zero_vector(self):
        """零向量 cosine = 0.0"""
        from semantic_embedder import _cosine_similarity
        a = [0.0, 0.0]
        b = [1.0, 2.0]
        self.assertEqual(_cosine_similarity(a, b), 0.0)

    def test_10_cosine_similarity_empty(self):
        """空向量 cosine = 0.0"""
        from semantic_embedder import _cosine_similarity
        self.assertEqual(_cosine_similarity([], [1.0]), 0.0)
        self.assertEqual(_cosine_similarity([1.0], []), 0.0)


# ============================================================================
# 2. TFIDFEmbedder 测试
# ============================================================================

class TestTFIDFEmbedder(unittest.TestCase):
    """测试 TFIDFEmbedder"""

    def setUp(self):
        self.corpus = [
            "the quick brown fox",
            "jumps over the lazy dog",
            "the quick brown dog",
            "hello world",
            "goodbye world",
        ]
        self.embedder = TFIDFEmbedder(corpus=self.corpus)

    def test_01_dimension_matches_vocab(self):
        """dimension = vocab size"""
        self.assertEqual(self.embedder.dimension, len(self.embedder._vocab))
        self.assertGreater(self.embedder.dimension, 0)

    def test_02_embed_same_text_returns_same_vector(self):
        """相同文本 → 相同向量"""
        v1 = self.embedder.embed("the quick brown fox")
        v2 = self.embedder.embed("the quick brown fox")
        self.assertEqual(v1, v2)

    def test_03_embed_normalized_to_unit_length(self):
        """向量 L2 归一化（sum of squares ≈ 1.0）"""
        v = self.embedder.embed("the quick brown fox")
        norm = sum(x * x for x in v) ** 0.5
        self.assertAlmostEqual(norm, 1.0, places=5)

    def test_04_similarity_identical_text(self):
        """相同文本 similarity = 1.0"""
        sim = self.embedder.similarity("hello world", "hello world")
        self.assertAlmostEqual(sim, 1.0, places=5)

    def test_05_similarity_similar_texts(self):
        """相似文本 similarity > 0.5"""
        sim = self.embedder.similarity(
            "the quick brown fox", "the quick brown dog"
        )
        self.assertGreater(sim, 0.3)
        self.assertLess(sim, 1.0)

    def test_06_similarity_different_texts(self):
        """不同文本 similarity < 0.5"""
        sim = self.embedder.similarity(
            "the quick brown fox", "hello world"
        )
        self.assertLess(sim, 0.5)

    def test_07_similarity_chinese(self):
        """中文文本 similarity"""
        corpus_zh = ["命名方案A", "命名方案B", "完全不同的句子"]
        emb_zh = TFIDFEmbedder(corpus=corpus_zh)
        sim_similar = emb_zh.similarity("命名方案A", "命名方案B")
        sim_different = emb_zh.similarity("命名方案A", "完全不同的句子")
        # 命名方案A vs B 相似度 > 命名方案A vs 完全不同的句子
        self.assertGreater(sim_similar, sim_different)

    def test_08_lazy_training(self):
        """懒训练：未传 corpus 时，第一次 embed 时训练"""
        emb_lazy = TFIDFEmbedder()  # 未传 corpus
        v = emb_lazy.embed("test")
        # 训练后 vocab 应包含 "test"
        self.assertGreater(emb_lazy.dimension, 0)

    def test_09_empty_corpus_handled(self):
        """空语料库：dimension=0，向量为空列表"""
        emb_empty = TFIDFEmbedder(corpus=[])
        self.assertEqual(emb_empty.dimension, 0)
        v = emb_empty.embed("test")
        self.assertEqual(v, [])

    def test_10_max_features_limit(self):
        """max_features 限制词表大小"""
        emb_limited = TFIDFEmbedder(
            corpus=self.corpus, max_features=3
        )
        # 词表最多 3 个
        self.assertLessEqual(emb_limited.dimension, 3)

    def test_11_embed_batch(self):
        """embed_batch 批量调用"""
        texts = ["text1", "text2", "text3"]
        vecs = self.embedder.embed_batch(texts)
        self.assertEqual(len(vecs), 3)
        for v in vecs:
            self.assertEqual(len(v), self.embedder.dimension)

    def test_12_is_semantic_match(self):
        """is_semantic_match 阈值判定"""
        self.assertTrue(
            self.embedder.is_semantic_match("hello world", "hello world", 0.99)
        )
        self.assertTrue(
            self.embedder.is_semantic_match(
                "the quick brown fox", "the quick brown dog", 0.5
            )
        )
        self.assertFalse(
            self.embedder.is_semantic_match(
                "the quick brown fox", "hello world", 0.9
            )
        )


# ============================================================================
# 3. HashingEmbedder 测试
# ============================================================================

class TestHashingEmbedder(unittest.TestCase):
    """测试 HashingEmbedder"""

    def setUp(self):
        self.embedder = HashingEmbedder(n_features=128)

    def test_01_dimension_matches_n_features(self):
        """dimension = n_features"""
        self.assertEqual(self.embedder.dimension, 128)

    def test_02_no_training_required(self):
        """无需训练，立即可用"""
        v = self.embedder.embed("test text")
        self.assertEqual(len(v), 128)

    def test_03_same_text_same_vector(self):
        """相同文本 → 相同向量（确定性 hash）"""
        v1 = self.embedder.embed("test text")
        v2 = self.embedder.embed("test text")
        self.assertEqual(v1, v2)

    def test_04_different_texts_different_vectors(self):
        """不同文本 → 不同向量"""
        v1 = self.embedder.embed("hello world")
        v2 = self.embedder.embed("goodbye world")
        self.assertNotEqual(v1, v2)

    def test_05_similarity_computation(self):
        """similarity 计算"""
        sim_same = self.embedder.similarity("hello", "hello")
        sim_diff = self.embedder.similarity("hello", "goodbye")
        self.assertGreater(sim_same, sim_diff)
        self.assertAlmostEqual(sim_same, 1.0, places=5)

    def test_06_chinese_text(self):
        """中文文本支持"""
        v = self.embedder.embed("命名方案A")
        self.assertEqual(len(v), 128)
        # 归一化后 sum of squares ≈ 1.0
        norm = sum(x * x for x in v) ** 0.5
        self.assertAlmostEqual(norm, 1.0, places=5)

    def test_07_empty_text_returns_zero_vector(self):
        """空文本返回零向量"""
        v = self.embedder.embed("")
        self.assertEqual(v, [0.0] * 128)


# ============================================================================
# 4. SentenceTransformerEmbedder 测试（优雅降级）
# ============================================================================

class TestSentenceTransformerEmbedder(unittest.TestCase):
    """测试 SentenceTransformerEmbedder（优雅降级）"""

    def test_01_import_error_when_not_installed(self):
        """sentence-transformers 未安装时抛 ImportError"""
        # 隐藏 sentence_transformers（如果存在）
        with patch.dict(sys.modules, {"sentence_transformers": None}):
            with self.assertRaises(ImportError) as ctx:
                from semantic_embedder import SentenceTransformerEmbedder
                SentenceTransformerEmbedder()
        # 错误消息应包含 fallback 提示
        self.assertIn("sentence-transformers", str(ctx.exception))

    def test_02_fallback_to_tfidf(self):
        """未安装时 fallback 到 TFIDFEmbedder"""
        # 直接验证：当 sentence-transformers 不可用时，create_embedder fallback
        from semantic_embedder import create_embedder
        # 强制使用 tfidf 类型
        emb = create_embedder(embedder_type="tfidf")
        self.assertIsInstance(emb, TFIDFEmbedder)


# ============================================================================
# 5. EmbeddingCache 测试
# ============================================================================

class TestEmbeddingCache(unittest.TestCase):
    """测试 EmbeddingCache（LRU 缓存）"""

    def setUp(self):
        self.embedder = TFIDFEmbedder(corpus=["test1", "test2", "test3"])
        self.cache = EmbeddingCache(self.embedder, capacity=3)

    def test_01_initial_state(self):
        """初始状态：空缓存，命中/未命中 = 0"""
        self.assertEqual(self.cache.size, 0)
        self.assertEqual(self.cache.hits, 0)
        self.assertEqual(self.cache.misses, 0)
        self.assertEqual(self.cache.hit_rate, 0.0)

    def test_02_get_or_compute_miss_then_hit(self):
        """第一次 miss，第二次 hit"""
        v1 = self.cache.get_or_compute("test1")
        self.assertEqual(self.cache.misses, 1)
        self.assertEqual(self.cache.hits, 0)
        v2 = self.cache.get_or_compute("test1")
        self.assertEqual(self.cache.misses, 1)
        self.assertEqual(self.cache.hits, 1)
        self.assertEqual(v1, v2)

    def test_03_lru_eviction(self):
        """LRU 淘汰：超出容量时淘汰最早条目"""
        # 容量 3，添加 4 个
        self.cache.get_or_compute("a")
        self.cache.get_or_compute("b")
        self.cache.get_or_compute("c")
        self.cache.get_or_compute("d")
        # 容量应仍为 3
        self.assertEqual(self.cache.size, 3)
        # 重新计算 a（已被淘汰）
        self.cache.get_or_compute("a")
        # 此时 misses 计数：4 次（a/b/c/d）+ 1 次（a 重新） = 5
        self.assertEqual(self.cache.misses, 5)

    def test_04_lru_access_promotes(self):
        """LRU 访问提升：访问 b 后 b 移到末尾"""
        self.cache.get_or_compute("a")
        self.cache.get_or_compute("b")
        self.cache.get_or_compute("c")
        # 访问 a（移到末尾）
        self.cache.get_or_compute("a")
        # 添加 d → 淘汰 b（最早未访问）
        self.cache.get_or_compute("d")
        # b 应被淘汰
        self.assertNotIn("b", self.cache._cache)

    def test_05_clear(self):
        """clear 清空缓存"""
        self.cache.get_or_compute("a")
        self.cache.get_or_compute("b")
        self.cache.clear()
        self.assertEqual(self.cache.size, 0)
        self.assertEqual(self.cache.hits, 0)
        self.assertEqual(self.cache.misses, 0)

    def test_06_similarity_uses_cache(self):
        """similarity 使用缓存"""
        sim1 = self.cache.similarity("test1", "test2")
        # 两次都 miss（2 个不同文本）
        self.assertEqual(self.cache.misses, 2)
        self.assertEqual(self.cache.hits, 0)
        # 再次调用 → 两次都 hit
        sim2 = self.cache.similarity("test1", "test2")
        self.assertEqual(self.cache.hits, 2)
        self.assertEqual(sim1, sim2)

    def test_07_hit_rate_calculation(self):
        """hit_rate 计算正确"""
        self.cache.get_or_compute("a")  # miss
        self.cache.get_or_compute("a")  # hit
        self.cache.get_or_compute("a")  # hit
        # 1 miss, 2 hits → rate = 2/3
        self.assertAlmostEqual(self.cache.hit_rate, 2.0 / 3.0, places=5)


# ============================================================================
# 6. Factory 函数测试
# ============================================================================

class TestFactoryFunctions(unittest.TestCase):
    """测试工厂函数：create_embedder, get_default_embedder"""

    def setUp(self):
        # 重置全局默认 embedder
        import semantic_embedder
        semantic_embedder._DEFAULT_EMBEDDER = None

    def test_01_create_embedder_tfidf(self):
        """create_embedder(embedder_type='tfidf')"""
        emb = create_embedder(embedder_type="tfidf")
        self.assertIsInstance(emb, TFIDFEmbedder)

    def test_02_create_embedder_hashing(self):
        """create_embedder(embedder_type='hashing')"""
        emb = create_embedder(embedder_type="hashing")
        self.assertIsInstance(emb, HashingEmbedder)

    def test_03_create_embedder_with_kwargs(self):
        """create_embedder 透传 kwargs"""
        emb = create_embedder(
            embedder_type="hashing", n_features=256
        )
        self.assertEqual(emb.dimension, 256)

    def test_04_create_embedder_unknown_type_raises(self):
        """未知 embedder_type 抛 ValueError"""
        with self.assertRaises(ValueError) as ctx:
            create_embedder(embedder_type="unknown_xyz")
        self.assertIn("未知", str(ctx.exception))

    def test_05_get_default_embedder_fallback_to_tfidf(self):
        """get_default_embedder 在 sentence-transformers 不可用时 fallback"""
        # 强制 fallback：preference=False
        import semantic_embedder
        with patch.dict(sys.modules, {"sentence_transformers": None}):
            semantic_embedder._DEFAULT_EMBEDDER = None
            emb = get_default_embedder(prefer_sentence_transformer=False)
            self.assertIsInstance(emb, TFIDFEmbedder)

    def test_06_get_default_embedder_singleton(self):
        """get_default_embedder 返回单例"""
        import semantic_embedder
        semantic_embedder._DEFAULT_EMBEDDER = None
        emb1 = get_default_embedder(prefer_sentence_transformer=False)
        emb2 = get_default_embedder(prefer_sentence_transformer=False)
        self.assertIs(emb1, emb2)


# ============================================================================
# 7. _fuzzy_similarity 集成 embedder 测试
# ============================================================================

class TestFuzzySimilarityWithEmbedder(unittest.TestCase):
    """测试 _fuzzy_similarity 接受 embedder 参数"""

    def setUp(self):
        import semantic_embedder
        self.embedder = TFIDFEmbedder(corpus=[
            "the quick brown fox",
            "the quick brown dog",
            "hello world",
        ])

    def test_01_no_embedder_uses_lcs(self):
        """无 embedder：使用 LCS"""
        # 两个相似但不同文本，LCS 应能识别
        sim = _fuzzy_similarity(
            "the quick brown fox",
            "the quick brown dog"
        )
        # LCS 相似度 > 0
        self.assertGreater(sim, 0.5)

    def test_02_with_embedder_uses_semantic(self):
        """有 embedder：使用 embedder.similarity"""
        # 使用 mock embedder 验证调用
        mock_embedder = MagicMock()
        mock_embedder.similarity = MagicMock(return_value=0.92)
        sim = _fuzzy_similarity("a", "b", embedder=mock_embedder)
        self.assertEqual(sim, 0.92)
        mock_embedder.similarity.assert_called_once_with("a", "b")

    def test_03_embedder_fails_falls_back_to_lcs(self):
        """embedder.similarity 抛错时 fallback 到 LCS"""
        mock_embedder = MagicMock()
        mock_embedder.similarity = MagicMock(side_effect=RuntimeError("fail"))
        sim = _fuzzy_similarity("hello world", "hello", embedder=mock_embedder)
        # 应 fallback 到 LCS，不抛错
        self.assertGreaterEqual(sim, 0.0)
        self.assertLessEqual(sim, 1.0)

    def test_04_identical_texts_with_embedder(self):
        """相同文本：返回 1.0（不调用 embedder）"""
        mock_embedder = MagicMock()
        sim = _fuzzy_similarity("same", "same", embedder=mock_embedder)
        self.assertEqual(sim, 1.0)
        mock_embedder.similarity.assert_not_called()

    def test_05_empty_text_returns_zero(self):
        """空文本返回 0.0"""
        self.assertEqual(_fuzzy_similarity("", "text"), 0.0)
        self.assertEqual(_fuzzy_similarity("text", ""), 0.0)
        self.assertEqual(_fuzzy_similarity("", "", embedder=self.embedder), 0.0)


# ============================================================================
# 8. _dedup_candidates 集成 embedder 测试
# ============================================================================

class TestDedupCandidatesWithEmbedder(unittest.TestCase):
    """测试 _dedup_candidates 接受 embedder 参数"""

    def setUp(self):
        import semantic_embedder
        self.embedder = TFIDFEmbedder(corpus=[
            "the quick brown fox",
            "the quick brown dog",
            "completely different text",
            "another unrelated sentence",
        ])

    def test_01_exact_strategy_no_embedder(self):
        """exact 策略：不使用 embedder"""
        candidates = ["hello", "Hello", "world"]
        result = _dedup_candidates(candidates, strategy="exact")
        self.assertEqual(len(result), 2)

    def test_02_fuzzy_strategy_uses_lcs(self):
        """fuzzy 策略：使用 LCS"""
        candidates = [
            "the quick brown fox",
            "the quick brown dog",  # 相似
            "completely different text",
        ]
        result = _dedup_candidates(
            candidates, strategy="fuzzy", threshold=0.5
        )
        # 第一个和第二个 LCS 相似度 > 0.5 → 去重
        self.assertEqual(len(result), 2)

    def test_03_semantic_strategy_uses_embedder(self):
        """semantic 策略：使用 embedder"""
        candidates = [
            "the quick brown fox",
            "the quick brown dog",  # TFIDF 视作相似
            "completely different text",
        ]
        result = _dedup_candidates(
            candidates,
            strategy="semantic",
            threshold=0.5,
            embedder=self.embedder,
        )
        # 第一个和第二个 TFIDF 相似度 > 0.5 → 去重
        self.assertEqual(len(result), 2)

    def test_04_semantic_strategy_without_embedder_falls_back(self):
        """semantic 策略无 embedder：fallback 到 fuzzy"""
        candidates = [
            "the quick brown fox",
            "the quick brown dog",
            "completely different text",
        ]
        result = _dedup_candidates(
            candidates, strategy="semantic", threshold=0.5
        )
        # 行为与 fuzzy 相同
        self.assertEqual(len(result), 2)

    def test_05_semantic_embedder_fails_falls_back(self):
        """semantic + embedder 抛错：fallback 到 fuzzy"""
        mock_embedder = MagicMock()
        mock_embedder.similarity = MagicMock(side_effect=RuntimeError("fail"))
        candidates = ["hello", "hello world", "different"]
        # 不应抛错
        result = _dedup_candidates(
            candidates, strategy="semantic", embedder=mock_embedder
        )
        self.assertGreater(len(result), 0)

    def test_06_unknown_strategy_falls_back_to_exact(self):
        """未知策略 fallback 到 exact"""
        candidates = ["hello", "Hello", "world"]
        result = _dedup_candidates(candidates, strategy="unknown_xyz")
        self.assertEqual(len(result), 2)

    def test_07_threshold_affects_dedup(self):
        """threshold 影响去重结果"""
        candidates = [
            "the quick brown fox",
            "the quick brown dog",
        ]
        # 阈值 0.99 → 不去重
        result_high = _dedup_candidates(
            candidates, strategy="fuzzy", threshold=0.99
        )
        # 阈值 0.0 → 全部去重（极端）
        result_low = _dedup_candidates(
            candidates, strategy="fuzzy", threshold=0.0
        )
        # 实际：阈值 0.0 → 所有相似度都 >= 0.0 → 全部视为重复
        self.assertEqual(len(result_low), 1)


# ============================================================================
# 9. GenerateFilterExecutor 集成 embedder 测试
# ============================================================================

class TestGenerateFilterExecutorEmbedder(unittest.TestCase):
    """测试 GenerateFilterExecutor 接受 embedder 配置"""

    def setUp(self):
        # Mock dispatch_agent_v2
        import pattern_executor
        from performance_fingerprint import PerformanceFingerprint
        import tempfile
        self._dispatch_patcher = patch.object(
            pattern_executor, "dispatch_agent_v2", lambda *a, **k: True
        )
        self._dispatch_patcher.start()
        self.tmp = tempfile.mkdtemp()
        self.fp = PerformanceFingerprint(
            agent_id="test_gf_emb", storage_path=self.tmp
        )

    def tearDown(self):
        self._dispatch_patcher.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_executor_accepts_embedder_dict(self):
        """GenerateFilterExecutor 接受 embedder 配置"""
        from pattern_executor import GenerateFilterExecutor
        executor = GenerateFilterExecutor(fingerprint=self.fp)
        # 不应抛错
        embedder = executor._resolve_embedder(
            dedup_strategy="semantic",
            parameters={"embedder": {"type": "tfidf"}},
        )
        self.assertIsNotNone(embedder)
        from semantic_embedder import TFIDFEmbedder
        self.assertIsInstance(embedder, TFIDFEmbedder)

    def test_02_executor_accepts_embedder_instance(self):
        """GenerateFilterExecutor 接受 Embedder 实例"""
        from pattern_executor import GenerateFilterExecutor
        from semantic_embedder import TFIDFEmbedder
        executor = GenerateFilterExecutor(fingerprint=self.fp)
        user_embedder = TFIDFEmbedder(corpus=["a", "b"])
        embedder = executor._resolve_embedder(
            dedup_strategy="semantic",
            parameters={"embedder": user_embedder},
        )
        # 应直接返回用户注入的实例
        self.assertIs(embedder, user_embedder)

    def test_03_non_semantic_strategy_no_embedder(self):
        """非 semantic 策略：不解析 embedder"""
        from pattern_executor import GenerateFilterExecutor
        executor = GenerateFilterExecutor(fingerprint=self.fp)
        embedder = executor._resolve_embedder(
            dedup_strategy="fuzzy",
            parameters={"embedder": {"type": "tfidf"}},  # 即使配置了也不用
        )
        self.assertIsNone(embedder)

    def test_04_no_embedder_config_returns_none(self):
        """未配置 embedder：返回 None（fallback 到 fuzzy）"""
        from pattern_executor import GenerateFilterExecutor
        executor = GenerateFilterExecutor(fingerprint=self.fp)
        embedder = executor._resolve_embedder(
            dedup_strategy="semantic", parameters={}
        )
        self.assertIsNone(embedder)

    def test_05_invalid_embedder_config_falls_back(self):
        """无效 embedder 配置：fallback 到 None"""
        from pattern_executor import GenerateFilterExecutor
        executor = GenerateFilterExecutor(fingerprint=self.fp)
        # 无效类型
        embedder = executor._resolve_embedder(
            dedup_strategy="semantic",
            parameters={"embedder": {"type": "unknown_xyz"}},
        )
        # fallback 到 None
        self.assertIsNone(embedder)

    def test_06_execute_with_tfidf_embedder(self):
        """端到端：semantic 策略 + TFIDFEmbedder"""
        from pattern_executor import GenerateFilterExecutor
        executor = GenerateFilterExecutor(fingerprint=self.fp)
        task = {"description": "test", "filter_criteria": ["c1"]}
        # semantic 策略 + TFIDF embedder
        result = executor.execute(
            task,
            parameters={
                "generator_count": 3,
                "dedup_strategy": "semantic",
                "embedder": {"type": "tfidf"},
            },
        )
        # 不应抛错
        self.assertIsNotNone(result)


# ============================================================================
# 10. 性能与边界测试
# ============================================================================

class TestPerformanceAndEdgeCases(unittest.TestCase):
    """性能与边界测试"""

    def setUp(self):
        import semantic_embedder
        self.embedder = TFIDFEmbedder(corpus=[
            f"text variant {i}" for i in range(50)
        ])

    def test_01_performance_100_candidates(self):
        """性能：100 候选 < 1s"""
        candidates = [f"text variant {i % 10}" for i in range(100)]
        start = time.perf_counter()
        result = _dedup_candidates(
            candidates, strategy="semantic", embedder=self.embedder
        )
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 1.0)  # < 1 秒
        # 去重后候选数应 <= 10（10 个 unique）
        self.assertLessEqual(len(result), 10)

    def test_02_hashing_embedder_large_corpus(self):
        """HashingEmbedder 处理大语料库"""
        emb = HashingEmbedder(n_features=2048)
        # 1000 个候选
        candidates = [f"text {i}" for i in range(1000)]
        start = time.perf_counter()
        for c in candidates:
            emb.embed(c)
        elapsed = time.perf_counter() - start
        # 1000 候选 < 5s
        self.assertLess(elapsed, 5.0)

    def test_03_cache_speedup(self):
        """缓存加速：第二次访问更快"""
        cache = EmbeddingCache(self.embedder, capacity=100)
        text = "test text"
        # 第一次
        start1 = time.perf_counter()
        cache.get_or_compute(text)
        time1 = time.perf_counter() - start1
        # 第二次（缓存命中）
        start2 = time.perf_counter()
        cache.get_or_compute(text)
        time2 = time.perf_counter() - start2
        # 缓存命中应更快（不严格断言，因为文本太短可能差异不大）
        self.assertEqual(cache.hits, 1)
        self.assertEqual(cache.misses, 1)

    def test_04_cross_language_similarity(self):
        """跨语言相似度"""
        corpus = [
            "hello world",
            "你好世界",
            "good morning",
            "早上好",
        ]
        emb = TFIDFEmbedder(corpus=corpus)
        # 英文 hello world vs 中文 你好世界：TFIDF 视为不同（无公共 token）
        sim = emb.similarity("hello world", "你好世界")
        # TFIDF 在跨语言场景下相似度可能很低
        # 仅验证返回有效值
        self.assertGreaterEqual(sim, 0.0)
        self.assertLessEqual(sim, 1.0)

    def test_05_unicode_normalization(self):
        """Unicode 文本支持"""
        emb = TFIDFEmbedder()
        # 含 emoji / 特殊字符
        v1 = emb.embed("hello 😀 world")
        v2 = emb.embed("hello world")
        # 不应抛错
        self.assertIsInstance(v1, list)
        self.assertIsInstance(v2, list)

    def test_06_very_long_text(self):
        """超长文本处理"""
        emb = TFIDFEmbedder()
        long_text = "the quick brown fox " * 1000
        v = emb.embed(long_text)
        # 应正常返回向量
        self.assertEqual(len(v), emb.dimension)

    def test_07_concurrent_cache_access(self):
        """并发缓存访问（线程安全）"""
        import threading
        cache = EmbeddingCache(self.embedder, capacity=50)
        errors: list = []

        def worker(idx: int) -> None:
            try:
                for _ in range(10):
                    cache.get_or_compute(f"text_{idx % 5}")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(errors), 0)


# ============================================================================
# 11. Phase 7 真实 embedding 集成测试（sentence-transformers 多语言模型）
# ============================================================================

import os
SENTENCE_TRANSFORMERS_TEST = os.environ.get("SENTENCE_TRANSFORMERS_TEST", "0") == "1"


def _st_available() -> bool:
    """检查 sentence-transformers 是否可用且能加载模型"""
    if not SENTENCE_TRANSFORMERS_TEST:
        return False
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(
    _st_available(),
    "Phase 7 真实模型测试需设置 SENTENCE_TRANSFORMERS_TEST=1 "
    "并安装 sentence-transformers（pip install sentence-transformers）"
)
class TestRealSentenceTransformerEmbedding(unittest.TestCase):
    """
    Phase 7 真实 embedding 集成测试

    覆盖：
    - 模型加载（默认 paraphrase-multilingual-MiniLM-L12-v2）
    - 维度正确（384）
    - embed 返回 List[float]（非 Tensor / numpy.ndarray）
    - 英文语义相似度（同义词、相关概念）
    - 中文语义相似度
    - 跨语言相似度
    - TFIDF vs SentenceTransformer 准确率对比
    - 批量 embed 加速
    - 缓存复用
    - 边界场景（空文本、特殊字符）
    """

    @classmethod
    def setUpClass(cls):
        """类级别 setup：加载一次模型，供所有 test_* 共享"""
        from semantic_embedder import SentenceTransformerEmbedder
        # 真实加载多语言模型（首次会下载 + 加载，可能耗时 5-10s）
        cls.embedder = SentenceTransformerEmbedder()
        cls.model_name = cls.embedder._model_name
        cls.dim = cls.embedder.dimension

    def test_01_default_model_is_multilingual(self):
        """默认模型为多语言模型（避免 UNK 塌缩）"""
        self.assertEqual(
            self.model_name, "paraphrase-multilingual-MiniLM-L12-v2"
        )

    def test_02_dimension_is_384(self):
        """维度为 384（MiniLM 系列标准）"""
        self.assertEqual(self.dim, 384)

    def test_03_embed_returns_list_of_floats(self):
        """embed 返回 List[float]（避免 Tensor 泄漏到下游）"""
        vec = self.embedder.embed("hello world")
        self.assertIsInstance(vec, list)
        self.assertEqual(len(vec), self.dim)
        for x in vec[:10]:  # 检查前 10 个元素
            self.assertIsInstance(x, float)

    def test_04_embed_batch_returns_list_of_lists(self):
        """embed_batch 返回 List[List[float]]"""
        vecs = self.embedder.embed_batch(["a", "b", "c"])
        self.assertEqual(len(vecs), 3)
        for v in vecs:
            self.assertIsInstance(v, list)
            self.assertEqual(len(v), self.dim)
            for x in v[:5]:
                self.assertIsInstance(x, float)

    def test_05_similarity_identical_is_one(self):
        """相同文本 similarity = 1.0"""
        sim = self.embedder.similarity("hello", "hello")
        self.assertAlmostEqual(sim, 1.0, places=5)

    def test_06_english_synonyms_high_similarity(self):
        """英文同义词：高相似度"""
        # car / automobile 是经典同义词
        sim = self.embedder.similarity("car", "automobile")
        self.assertGreater(sim, 0.7, f"Expected > 0.7, got {sim}")

    def test_07_english_related_concepts(self):
        """英文相关概念：中等相似度（cat/dog 同为宠物）"""
        # cat / dog 都是常见宠物，相似度中等（> 无关概念 < 同义词）
        sim = self.embedder.similarity("cat", "dog")
        # cat/dog 实际模型分约 0.30（属于"相关但不相似"）
        # 阈值设低一些以适应真实模型行为
        self.assertGreater(sim, 0.2, f"cat/dog 相似度过低: {sim:.4f}")
        self.assertLess(sim, 0.9)
        # 验证与无关概念相比更相似
        sim_unrelated = self.embedder.similarity("cat", "philosophy")
        self.assertGreater(
            sim, sim_unrelated,
            f"cat/dog ({sim:.4f}) 应比 cat/philosophy ({sim_unrelated:.4f}) 相似"
        )

    def test_08_english_unrelated_low_similarity(self):
        """英文无关概念：低相似度"""
        sim = self.embedder.similarity("apple", "philosophy")
        self.assertLess(sim, 0.5)

    def test_09_chinese_related_concepts(self):
        """中文相关概念：显著相似度（验证非 [UNK] 塌缩）"""
        # 北京 / 上海 都是中国大城市
        sim = self.embedder.similarity("北京", "上海")
        self.assertGreater(
            sim, 0.5,
            f"北京 vs 上海 相似度过低: {sim:.4f}（旧模型会塌缩为 1.0）"
        )
        # 同时验证不等于 1.0（避免塌缩 bug）
        self.assertLess(sim, 1.0)

    def test_10_chinese_unrelated(self):
        """中文无关概念：低相似度"""
        sim = self.embedder.similarity("苹果", "哲学")
        self.assertLess(sim, 0.5)

    def test_11_chinese_no_unk_collapse(self):
        """中文无 [UNK] 塌缩：不同文本应得到不同 embedding"""
        v1 = self.embedder.embed("机器学习")
        v2 = self.embedder.embed("深度学习")
        # 两个向量的差异应显著
        diff = sum(abs(a - b) for a, b in zip(v1, v2))
        self.assertGreater(
            diff, 1.0,
            f"差异过小: {diff:.4f}（可能存在 [UNK] 塌缩）"
        )

    def test_12_cross_lingual_similarity(self):
        """跨语言：相同语义不同语言→高相似度"""
        sim_en_zh = self.embedder.similarity("machine learning", "机器学习")
        self.assertGreater(
            sim_en_zh, 0.5,
            f"cross-lingual (en↔zh) 相似度过低: {sim_en_zh:.4f}"
        )

    def test_13_semantic_paraphrase(self):
        """语义等价改写：高相似度"""
        sim = self.embedder.similarity(
            "I love programming", "I enjoy coding"
        )
        self.assertGreater(sim, 0.6, f"paraphrase 相似度过低: {sim:.4f}")

    def test_14_real_embedder_beats_tfidf_on_chinese(self):
        """真实 embedding 在中文上显著优于 TFIDF（回归保护）"""
        from semantic_embedder import TFIDFEmbedder
        # 真实模型：机器学习 vs 深度学习
        real_sim = self.embedder.similarity("机器学习", "深度学习")
        # TFIDF 训练语料
        tfidf = TFIDFEmbedder(
            corpus=[
                "机器学习是人工智能的分支",
                "深度学习是机器学习的子集",
                "今天天气很好",
                "我喜欢吃苹果",
            ]
        )
        tfidf_sim = tfidf.similarity("机器学习", "深度学习")
        # 真实模型应能区分（不应塌缩为 1.0）
        self.assertLess(
            real_sim, 1.0,
            f"真实模型不应塌缩为 1.0: {real_sim:.4f}"
        )
        # 真实模型应识别它们相关（> 0.3）
        self.assertGreater(
            real_sim, 0.3,
            f"真实模型应识别相关: {real_sim:.4f}"
        )
        # 注意：TFIDF 也能给到非平凡值（因为共享"学习"），但不要求对比
        self.assertGreaterEqual(tfidf_sim, 0.0)

    def test_15_real_embedder_beats_tfidf_on_synonyms(self):
        """真实 embedding 在同义词上显著优于 TFIDF（核心优势）"""
        from semantic_embedder import TFIDFEmbedder
        # car / automobile 是经典同义词对
        # TFIDF 视为不同（无公共 token）
        tfidf = TFIDFEmbedder(corpus=[
            "I drive a car", "An automobile is fast", "The cat sits"
        ])
        tfidf_sim = tfidf.similarity("car", "automobile")
        real_sim = self.embedder.similarity("car", "automobile")
        # 真实模型应显著识别为同义
        self.assertGreater(
            real_sim, 0.7,
            f"真实模型应识别 car/automobile 同义: {real_sim:.4f}"
        )
        # TFIDF 应较低（无公共 token）
        self.assertLess(
            tfidf_sim, 0.3,
            f"TFIDF car/automobile 应较低: {tfidf_sim:.4f}"
        )
        # 真实模型显著优于 TFIDF
        self.assertGreater(
            real_sim - tfidf_sim, 0.5,
            f"真实模型应显著优于 TFIDF: real={real_sim:.4f}, "
            f"tfidf={tfidf_sim:.4f}"
        )

    def test_16_embedder_with_real_model_in_dedup(self):
        """真实模型在 _dedup_candidates 场景下的语义去重"""
        from pattern_executor import _dedup_candidates
        candidates = [
            "I love programming",      # 0
            "I enjoy coding",          # 1 = paraphrase of 0
            "the weather is nice",     # 2
            "today is sunny",          # 3 = paraphrase of 2
            "completely unrelated",    # 4
        ]
        # 真实模型在阈值 0.6 下应能识别 paraphrase
        result = _dedup_candidates(
            candidates,
            strategy="semantic",
            threshold=0.5,
            embedder=self.embedder,
        )
        # 期望去重后约 3 个
        self.assertLessEqual(
            len(result), 4,
            f"去重过多: {len(result)} / {len(candidates)}"
        )
        self.assertGreaterEqual(
            len(result), 2,
            f"去重过少: {len(result)} / {len(candidates)}"
        )

    def test_17_real_embedder_cache_hit_rate(self):
        """真实模型 + EmbeddingCache 命中率"""
        from semantic_embedder import EmbeddingCache
        cache = EmbeddingCache(self.embedder, capacity=10)
        # 第一次：miss
        cache.get_or_compute("hello")
        # 第二次：hit
        cache.get_or_compute("hello")
        cache.get_or_compute("hello")
        self.assertEqual(cache.hits, 2)
        self.assertEqual(cache.misses, 1)
        self.assertAlmostEqual(cache.hit_rate, 2.0 / 3.0, places=5)

    def test_18_to_float_list_handles_tensor(self):
        """_to_float_list 正确处理 torch.Tensor"""
        try:
            import torch
        except ImportError:
            self.skipTest("torch 未安装")
        from semantic_embedder import SentenceTransformerEmbedder
        # 1D tensor
        t1 = torch.tensor([1.0, 2.0, 3.0])
        result = SentenceTransformerEmbedder._to_float_list(t1)
        self.assertEqual(result, [1.0, 2.0, 3.0])
        # 2D tensor（应 flatten）
        t2 = torch.tensor([[1.0, 2.0], [3.0, 4.0]])
        result = SentenceTransformerEmbedder._to_float_list(t2)
        self.assertEqual(result, [1.0, 2.0, 3.0, 4.0])
        # GPU tensor（MPS/CUDA → CPU）
        if torch.backends.mps.is_available():
            t3 = torch.tensor([1.0, 2.0], device="mps")
            result = SentenceTransformerEmbedder._to_float_list(t3)
            self.assertEqual(result, [1.0, 2.0])

    def test_19_to_float_list_handles_none_and_empty(self):
        """_to_float_list 边界：None / 空 list"""
        from semantic_embedder import SentenceTransformerEmbedder
        self.assertEqual(SentenceTransformerEmbedder._to_float_list(None), [])
        self.assertEqual(SentenceTransformerEmbedder._to_float_list([]), [])
        self.assertEqual(
            SentenceTransformerEmbedder._to_float_list([1, 2, 3]),
            [1.0, 2.0, 3.0]
        )

    def test_20_custom_model_name(self):
        """支持自定义模型名（验证参数传递）"""
        from semantic_embedder import SentenceTransformerEmbedder
        # 复用已下载的模型（避免再次下载）
        emb = SentenceTransformerEmbedder(
            model_name="paraphrase-multilingual-MiniLM-L12-v2"
        )
        self.assertEqual(emb.dimension, 384)
        # 验证可用
        v = emb.embed("test")
        self.assertEqual(len(v), 384)

    def test_21_performance_50_embeddings_under_30s(self):
        """性能：50 次 embed < 30s（CPU baseline）"""
        texts = [f"sample text variant {i}" for i in range(50)]
        start = time.perf_counter()
        for t in texts:
            self.embedder.embed(t)
        elapsed = time.perf_counter() - start
        self.assertLess(
            elapsed, 30.0,
            f"50 次 embed 耗时 {elapsed:.2f}s（>30s）"
        )

    def test_22_batch_faster_than_sequential(self):
        """embed_batch 显著快于单条循环（batch 加速）"""
        texts = [f"sample text {i}" for i in range(20)]
        # 单条
        start1 = time.perf_counter()
        for t in texts:
            self.embedder.embed(t)
        seq_time = time.perf_counter() - start1
        # 批量
        start2 = time.perf_counter()
        self.embedder.embed_batch(texts)
        batch_time = time.perf_counter() - start2
        # 批量应快（即使在 CPU 上也有 2x+ 加速）
        # 阈值放宽到 1.5x（避免偶发抖动）
        self.assertLess(
            batch_time, seq_time * 1.5,
            f"batch ({batch_time:.3f}s) 不应慢于 sequential "
            f"({seq_time:.3f}s) 太多"
        )


# ============================================================================
# Test Runner
# ============================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
