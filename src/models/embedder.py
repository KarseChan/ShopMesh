"""Embedding factory — Ollama bge-m3 with async interface.

Uses Ollama /api/embed endpoint for embeddings.
"""

import asyncio
from functools import lru_cache

import httpx

from src.config import config


class Embedder:
    """Ollama embedding model wrapper with async interface."""

    def __init__(self, model: str, base_url: str):
        self.model = model
        self.base_url = base_url.rstrip("/")

    async def aembed(self, text: str) -> list[float]:
        """Embed a single text (async)."""
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base_url}/api/embed",
                json={"model": self.model, "input": text},
            )
            resp.raise_for_status()
            data = resp.json()
        return data["embeddings"][0]

    async def aembed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts (async)."""
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.base_url}/api/embed",
                json={"model": self.model, "input": texts},
            )
            resp.raise_for_status()
            data = resp.json()
        return data["embeddings"]


@lru_cache(maxsize=2)
def get_embedder(agent_name: str = "default") -> Embedder:
    """Factory: get Embedder by agent name. Instance is cached."""
    emb_cfg = config["embedding"]
    cfg = emb_cfg.get(agent_name, emb_cfg["default"])
    return Embedder(model=cfg["model"], base_url=cfg["base_url"])
