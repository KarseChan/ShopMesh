"""Build the Qdrant `merchants` vector index for instant-delivery retrieval.

Reads data/mock_merchants.json → embeds embedding_text via Ollama bge-m3 →
upserts to the `merchants` collection with a payload carrying the geo point and
the constraint fields (open_hour/close_hour/delivery_minutes/avg_price/category)
so build_merchant_filter can push them down into HNSW traversal.

Usage:
    python scripts/build_merchant_index.py
    python scripts/build_merchant_index.py --data data/mock_merchants.json --collection merchants
"""

import argparse
import asyncio
import json
from pathlib import Path

from src.config import config
from src.models.embedder import get_embedder
from src.retrieval.vector_store import get_vector_store

_DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data" / "mock_merchants.json"


def _merchant_collection() -> str:
    return config["vector_db"].get("merchant_collection", "merchants")


async def build_index(data_path: str, collection: str, batch_size: int = 32):
    with open(data_path, encoding="utf-8") as f:
        merchants = json.load(f)
    print(f"Loaded {len(merchants)} merchants from {data_path}")

    embedder = get_embedder()
    store = get_vector_store()
    dimension = config["embedding"]["default"]["dimensions"]

    try:
        await store.create_collection(collection, dimension)
        print(f"Created collection '{collection}' (dim={dimension})")
    except Exception:
        print(f"Collection '{collection}' may already exist, continuing...")

    # Payload indexes for constraint push-down (geo radius + range filters).
    for field, ftype in [
        ("location", "geo"),
        ("category", "keyword"),
        ("open_hour", "integer"),
        ("close_hour", "integer"),
        ("delivery_minutes", "integer"),
        ("avg_price", "float"),
    ]:
        try:
            await store.create_payload_index(collection, field, ftype)
        except Exception:
            pass  # index may already exist

    total = len(merchants)
    for i in range(0, total, batch_size):
        batch = merchants[i:i + batch_size]
        texts = [m.get("embedding_text", m["name"]) for m in batch]
        ids = [i + j + 1 for j in range(len(batch))]
        payloads = [
            {
                "merchant_id": m["merchant_id"],
                "name": m["name"],
                "city": m.get("city", ""),
                "category": m.get("category", ""),
                # Qdrant geo point: {"lat", "lon"}
                "location": {"lat": m["latitude"], "lon": m["longitude"]},
                "latitude": m["latitude"],
                "longitude": m["longitude"],
                "rating": m.get("rating", 0.0),
                "avg_price": m.get("avg_price", 0.0),
                "delivery_fee": m.get("delivery_fee", 0.0),
                "delivery_minutes": m.get("delivery_minutes", 0),
                "delivery_radius_km": m.get("delivery_radius_km", 3.0),
                "open_hour": m.get("open_hour", 0),
                "close_hour": m.get("close_hour", 24),
                "open_hours": m.get("open_hours", ""),
                "tags": m.get("tags", []),
                "image_url": m.get("image_url", ""),
            }
            for m in batch
        ]
        vectors = await embedder.aembed_batch(texts)
        await store.upsert(collection, ids, vectors, payloads)
        print(f"  Upserted {min(i + batch_size, total)}/{total}")

    print(f"Done. {total} merchants indexed in '{collection}'.")


def main():
    parser = argparse.ArgumentParser(description="Build Qdrant merchant index")
    parser.add_argument("--data", default=str(_DEFAULT_DATA))
    parser.add_argument("--collection", default=None)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()

    collection = args.collection or _merchant_collection()
    asyncio.run(build_index(args.data, collection, args.batch_size))


if __name__ == "__main__":
    main()
