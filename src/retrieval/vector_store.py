"""VectorStore abstraction + Qdrant implementation."""

from abc import ABC, abstractmethod

from src.config import config


class VectorStore(ABC):
    """Abstract vector store interface."""

    @abstractmethod
    async def create_collection(self, collection: str, dimension: int) -> None:
        ...

    @abstractmethod
    async def upsert(self, collection: str, ids: list[str],
                     vectors: list[list[float]], payloads: list[dict]) -> None:
        ...

    @abstractmethod
    async def search(self, collection: str, query_vector: list[float],
                     limit: int = 10, filters: dict | None = None) -> list[dict]:
        """Search with optional payload filters. Returns list of {id, score, payload}."""
        ...


class QdrantVectorStore(VectorStore):
    """Qdrant implementation of VectorStore."""

    def __init__(self, url: str):
        from qdrant_client import AsyncQdrantClient
        self._client = AsyncQdrantClient(url=url)

    async def create_collection(self, collection: str, dimension: int) -> None:
        from qdrant_client.models import Distance, VectorParams
        await self._client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
        )

    async def upsert(self, collection: str, ids: list[str],
                     vectors: list[list[float]], payloads: list[dict]) -> None:
        from qdrant_client.models import PointStruct
        points = [
            PointStruct(id=id_, vector=vec, payload=pl)
            for id_, vec, pl in zip(ids, vectors, payloads)
        ]
        await self._client.upsert(collection_name=collection, points=points)

    async def search(self, collection: str, query_vector: list[float],
                     limit: int = 10, filters: dict | None = None) -> list[dict]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue
        query_filter = None
        if filters:
            conditions = []
            for key, value in filters.items():
                conditions.append(FieldCondition(key=key, match=MatchValue(value=value)))
            query_filter = Filter(must=conditions)

        results = await self._client.query_points(
            collection_name=collection,
            query=query_vector,
            limit=limit,
            query_filter=query_filter,
        )
        return [{"id": r.id, "score": r.score, "payload": r.payload} for r in results.points]


def get_vector_store() -> VectorStore:
    """Factory: get VectorStore from config."""
    vdb_cfg = config["vector_db"]
    provider = vdb_cfg.get("provider", "qdrant")
    if provider == "qdrant":
        return QdrantVectorStore(url=vdb_cfg["url"])
    raise ValueError(f"Unknown vector store provider: {provider}")
