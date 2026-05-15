"""Embedding factory — sentence-transformers with async interface.

Uses asyncio.to_thread to avoid blocking the event loop.
Model is loaded once at startup and cached.
"""

import asyncio
from functools import lru_cache

from src.config import config


class Embedder:
    """BGE-M3 embedding model wrapper with async interface."""

    def __init__(self, model_name: str, device: str = "cpu"):
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(model_name, device=device)

    async def aembed(self, text: str) -> list[float]:
        """Embed a single text (async, non-blocking)."""
        return await asyncio.to_thread(self._model.encode, text).tolist()

    async def aembed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts (async, non-blocking)."""
        return await asyncio.to_thread(self._model.encode, texts).tolist()


@lru_cache(maxsize=2)
def get_embedder(agent_name: str = "default") -> Embedder:
    """Factory: get Embedder by agent name. Instance is cached."""
    emb_cfg = config["embedding"]
    cfg = emb_cfg.get(agent_name, emb_cfg["default"])
    return Embedder(model_name=cfg["model"], device=cfg.get("device", "cpu"))
