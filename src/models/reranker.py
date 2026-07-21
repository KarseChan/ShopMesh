"""Reranker — FlagEmbedding bge-reranker-v2-m3 cross-encoder for semantic reranking.

Used in the ranking pipeline to provide fine-grained relevance scoring
after Qdrant bi-encoder recall. Cross-encoder scores query-document pairs
directly, capturing nuances that cosine similarity misses.

Architecture position:
    Qdrant recall (top-50) → Reranker (this module) → Business Ranker (7-dim fusion)

Triggered conditionally: only for vague queries, multi-requirement scenarios,
or when candidate similarity scores are too close to distinguish.
"""

import asyncio
from functools import lru_cache

from src.config import config
from src.observability.logger import get_logger

logger = get_logger("reranker")

_MAX_RETRIES = 2
_BASE_DELAY = 1.0


class Reranker:
    """FlagEmbedding cross-encoder reranker with async interface."""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = None

    def _load_model(self):
        if self._model is None:
            from FlagEmbedding import FlagReranker
            logger.info("reranker_loading", model=self.model_name)
            self._model = FlagReranker(self.model_name, use_fp16=True)
            logger.info("reranker_loaded", model=self.model_name)
        return self._model

    def _rerank_sync(self, query: str, documents: list[str]) -> list[tuple[int, float]]:
        """Synchronous rerank. Returns [(index, score)] sorted by score desc."""
        model = self._load_model()
        pairs = [[query, doc] for doc in documents]
        scores = model.compute_score(pairs)
        if isinstance(scores, float):
            scores = [scores]
        indexed = list(enumerate(scores))
        indexed.sort(key=lambda x: x[1], reverse=True)
        return indexed

    async def rerank(self, query: str, documents: list[str]) -> list[tuple[int, float]]:
        """Async rerank with retry. Returns [(index, score)] sorted by score desc."""
        if not documents:
            return []
        if not query:
            return [(i, 0.5) for i in range(len(documents))]

        for attempt in range(_MAX_RETRIES):
            try:
                result = await asyncio.to_thread(self._rerank_sync, query, documents)
                logger.info("rerank_done",
                            query_len=len(query),
                            doc_count=len(documents),
                            top_score=result[0][1] if result else 0)
                return result
            except Exception as e:
                delay = _BASE_DELAY * (2 ** attempt)
                logger.warning("rerank_retry",
                               attempt=attempt + 1,
                               error=str(e),
                               delay=delay)
                await asyncio.sleep(delay)

        logger.error("rerank_failed", query_len=len(query), doc_count=len(documents))
        return [(i, 0.5) for i in range(len(documents))]


_reranker_instance: Reranker | None = None


def get_reranker() -> Reranker:
    """Get singleton reranker instance."""
    global _reranker_instance
    if _reranker_instance is None:
        cfg = config.get("reranker", {})
        model_name = cfg.get("model", "BAAI/bge-reranker-v2-m3")
        _reranker_instance = Reranker(model_name)
    return _reranker_instance


def is_reranker_enabled() -> bool:
    """Check if reranker is enabled in config."""
    cfg = config.get("reranker", {})
    return cfg.get("enabled", True)
