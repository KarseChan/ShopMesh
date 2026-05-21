"""Embedding factory — Ollama bge-m3 with async interface.

Uses Ollama /api/embed endpoint for embeddings.
"""

import asyncio
from functools import lru_cache

import httpx

from src.config import config
from src.observability.logger import get_logger

logger = get_logger("embedder")

# Retry config for embedding calls
_MAX_RETRIES = 3
_BASE_DELAY = 1.0


class Embedder:
    """Ollama embedding model wrapper with async interface."""

    _CONNECT_TIMEOUT = 5.0
    _READ_TIMEOUT = 120.0

    def __init__(self, model: str, base_url: str):
        self.model = model
        self.base_url = base_url.rstrip("/")

    def _make_timeout(self, read: float | None = None) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self._CONNECT_TIMEOUT,
            read=read or self._READ_TIMEOUT,
            write=10.0,
            pool=5.0,
        )

    async def _post_embed(self, payload: dict, timeout: httpx.Timeout) -> list[list[float]]:
        """Post embed request with retry logic."""
        last_error = None
        for attempt in range(_MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(
                        f"{self.base_url}/api/embed",
                        json=payload,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                return data["embeddings"]
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
                last_error = e
                delay = _BASE_DELAY * (2 ** attempt)
                logger.warning("embed_retry", attempt=attempt + 1, max_retries=_MAX_RETRIES,
                               error_type=type(e).__name__, delay=delay)
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(delay)
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500:
                    last_error = e
                    delay = _BASE_DELAY * (2 ** attempt)
                    logger.warning("embed_retry", attempt=attempt + 1, max_retries=_MAX_RETRIES,
                                   status=e.response.status_code, delay=delay)
                    if attempt < _MAX_RETRIES - 1:
                        await asyncio.sleep(delay)
                else:
                    raise
        raise last_error

    async def aembed(self, text: str) -> list[float]:
        """Embed a single text (async)."""
        timeout = self._make_timeout()
        embeddings = await self._post_embed(
            {"model": self.model, "input": text}, timeout
        )
        return embeddings[0]

    async def aembed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts (async)."""
        timeout = self._make_timeout(read=300.0)  # batch can be slower
        return await self._post_embed(
            {"model": self.model, "input": texts}, timeout
        )


@lru_cache(maxsize=2)
def get_embedder(agent_name: str = "default") -> Embedder:
    """Factory: get Embedder by agent name. Instance is cached."""
    emb_cfg = config["embedding"]
    cfg = emb_cfg.get(agent_name, emb_cfg["default"])
    return Embedder(model=cfg["model"], base_url=cfg["base_url"])
