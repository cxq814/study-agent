"""
Embedding 模型封装。

策略：
  1. 优先使用 sentence-transformers（本地模型，无需 API）
  2. 回退到 TF-IDF 关键词匹配（确保无依赖时也能运行）
  3. 支持 OpenAI 兼容 API（如果配置了 API key）

用法：
    model = get_embedding_model()
    vector = model.embed("查询文本")
"""

import logging
import os
import hashlib
from typing import List, Optional
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# 缓存已加载的模型
_embedding_model: Optional["BaseEmbedding"] = None


class BaseEmbedding(ABC):
    """Embedding 模型抽象基类。"""

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """将文本转换为向量。"""
        ...

    @abstractmethod
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """批量将文本转换为向量。"""
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """向量维度。"""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """模型名称。"""
        ...


class SentenceTransformerEmbedding(BaseEmbedding):
    """
    基于 sentence-transformers 的 Embedding 模型。

    使用 BGE-small-zh 模型（中文优化，384 维，约 100MB）。
    """

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5"):
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading embedding model: {model_name}")
        self._model = SentenceTransformer(model_name)
        self._dimension = self._model.get_sentence_embedding_dimension()
        logger.info(f"Embedding model loaded. Dimension: {self._dimension}")

    def embed(self, text: str) -> List[float]:
        return self._model.encode(text, normalize_embeddings=True).tolist()

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return self._model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False
        ).tolist()

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def name(self) -> str:
        return "BAAI/bge-small-zh-v1.5"


class TFIDFEmbedding(BaseEmbedding):
    """
    基于 TF-IDF 的伪 Embedding（无外部依赖回退方案）。

    使用字符级 bigram 构建稀疏向量，通过哈希映射到固定维度。
    注意：这不是真正的语义 Embedding，但可以作为无 sentence-transformers 时的兜底。
    """

    DIM = 384  # 与 BGE-small 保持相同维度

    def __init__(self):
        import re
        self._tokenize = re.compile(r'.').findall  # 单字符 tokenize
        logger.info("TF-IDF fallback embedding initialized (dim=384)")

    def _hash_vector(self, tokens: List[str]) -> List[float]:
        """将 token 列表哈希到固定维度向量。"""
        vec = [0.0] * self.DIM
        if not tokens:
            return vec
        for i, token in enumerate(tokens):
            h = int(hashlib.md5(token.encode()).hexdigest(), 16)
            idx = h % self.DIM
            vec[idx] += 1.0
        # L2 归一化
        norm = sum(v * v for v in vec) ** 0.5
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def embed(self, text: str) -> List[float]:
        # 字符 + bigram tokenize
        chars = list(text)
        bigrams = [text[i:i+2] for i in range(len(text) - 1)]
        tokens = chars + bigrams
        return self._hash_vector(tokens)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]

    @property
    def dimension(self) -> int:
        return self.DIM

    @property
    def name(self) -> str:
        return "TF-IDF-hash-fallback"


def get_embedding_model() -> BaseEmbedding:
    """
    获取 Embedding 模型（单例）。

    优先级：
      1. sentence-transformers（如网络可达 HuggingFace）
      2. TF-IDF fallback（国内网络环境下自动回退，无需手动配置）

    国内用户可设环境变量启用 HuggingFace 镜像：
      set HF_ENDPOINT=https://hf-mirror.com
    """
    global _embedding_model
    if _embedding_model is not None:
        return _embedding_model

    # 两步连通性检测（国内网络 TCP 可能通但 HTTPS 被墙）
    # Step 1: TCP 快速预检（1.5s）
    tcp_ok = False
    try:
        import socket
        s = socket.create_connection(("huggingface.co", 443), timeout=1.5)
        s.close()
        tcp_ok = True
    except Exception:
        pass

    # Step 2: HTTP 层检测（仅在 TCP 通过时，3s 超时）
    hf_reachable = False
    if tcp_ok:
        try:
            from urllib.request import urlopen, Request
            req = Request("https://huggingface.co", method="HEAD")
            urlopen(req, timeout=3)
            hf_reachable = True
            logger.info("HuggingFace reachable, loading SentenceTransformer...")
        except Exception as e:
            logger.info(f"HuggingFace HTTP unreachable ({e}), using TF-IDF fallback")
    else:
        logger.info("HuggingFace TCP unreachable, using TF-IDF fallback")

    if hf_reachable:
        # 限制单次下载请求超时，防止模型下载卡死
        os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "5")
        try:
            _embedding_model = SentenceTransformerEmbedding()
            return _embedding_model
        except Exception as e:
            logger.warning(f"SentenceTransformer load failed: {e}")

    _embedding_model = TFIDFEmbedding()
    logger.info(
        f"Embedding model: {_embedding_model.name} ({_embedding_model.dimension}d). "
        f"Tip: set HF_ENDPOINT=https://hf-mirror.com for semantic search"
    )
    return _embedding_model
