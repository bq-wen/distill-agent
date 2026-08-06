"""Local, replaceable embedding providers for personal knowledge retrieval."""

import hashlib
import math
import re
from abc import ABC, abstractmethod
from threading import Lock


class EmbeddingProvider(ABC):
    """Maps text into one stable vector space."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Return the fixed output vector dimension."""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Embed one non-empty piece of text."""

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


class HashEmbeddingProvider(EmbeddingProvider):
    """Deterministic offline vectors for tests, not semantic production retrieval."""

    def __init__(self, dimensions: int = 64) -> None:
        if dimensions < 2:
            raise ValueError("HashEmbeddingProvider dimensions 必须至少为 2")
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, text: str) -> list[float]:
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]+|\d+", text.lower())
        if not tokens:
            raise ValueError("无法为不含有效 token 的文本生成向量")
        vector = [0.0] * self.dimensions
        for token in tokens:
            value = int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big")
            vector[value % self.dimensions] += -1.0 if (value >> 8) & 1 else 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector]


class SentenceTransformersEmbeddingProvider(EmbeddingProvider):
    """A lazily loaded local Sentence Transformers model for container deployment."""

    def __init__(self, model_name: str, *, device: str | None = "cpu") -> None:
        if not model_name.strip():
            raise ValueError("sentence-transformers model_name 不能为空")
        self.model_name = model_name
        self.device = device
        self._model = None
        self._dimensions: int | None = None
        self._load_lock = Lock()

    @property
    def dimensions(self) -> int:
        self._ensure_loaded()
        assert self._dimensions is not None
        return self._dimensions

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if any(not text.strip() for text in texts):
            raise ValueError("不能为纯空白文本生成 embedding")
        vectors = self._ensure_loaded().encode(
            texts, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False
        )
        return [vector.astype(float).tolist() for vector in vectors]

    def _ensure_loaded(self):
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as error:
                raise RuntimeError("缺少 sentence-transformers；请安装完整应用依赖后再建立本地语义索引") from error
            model = SentenceTransformer(self.model_name, device=self.device)
            get_dimensions = getattr(model, "get_embedding_dimension", model.get_sentence_embedding_dimension)
            dimensions = get_dimensions()
            if dimensions is None or dimensions < 1:
                raise RuntimeError(f"Embedding 模型未返回有效维度: {self.model_name}")
            self._model = model
            self._dimensions = dimensions
            return model
