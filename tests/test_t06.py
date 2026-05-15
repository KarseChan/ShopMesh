"""T0.6 vector store + embedding tests."""

import pytest

from src.models.embedder import get_embedder
from src.retrieval.vector_store import get_vector_store


def test_embedder_factory():
    """Embedder factory returns a valid instance."""
    emb = get_embedder()
    assert emb.model == "bge-m3"


def test_vector_store_factory():
    """VectorStore factory returns QdrantVectorStore."""
    store = get_vector_store()
    assert store is not None


@pytest.mark.asyncio
async def test_embedding_produces_vector():
    """Embedding returns a 1024-dim vector."""
    emb = get_embedder()
    vec = await emb.aembed("test text")
    assert len(vec) == 1024
    assert all(isinstance(v, float) for v in vec)


@pytest.mark.asyncio
async def test_vector_search_returns_results():
    """Qdrant search returns results from indexed collection."""
    emb = get_embedder()
    store = get_vector_store()
    vec = await emb.aembed("奶茶")
    results = await store.search("products", vec, limit=3)
    assert len(results) > 0
    assert all("score" in r and "payload" in r for r in results)


@pytest.mark.asyncio
async def test_vector_search_with_filter():
    """Qdrant search with payload filter works."""
    emb = get_embedder()
    store = get_vector_store()
    vec = await emb.aembed("skincare")
    results = await store.search("products", vec, limit=5, filters={"category": "护肤"})
    for r in results:
        assert r["payload"]["category"] == "护肤"
