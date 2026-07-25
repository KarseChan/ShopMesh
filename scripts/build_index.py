"""Build Qdrant vector index from mock product data.

Usage:
    python scripts/build_index.py
    python scripts/build_index.py --data data/mock_products_5k.json --collection products_5k

Reads products -> generates embeddings via Ollama bge-m3 -> upserts to Qdrant.
"""

import argparse
import asyncio
import json
from pathlib import Path

from src.config import config
from src.models.embedder import get_embedder
from src.retrieval.vector_store import get_vector_store


async def build_index(data_path: str, collection: str, batch_size: int = 32):
    with open(data_path, encoding="utf-8") as f:
        raw = json.load(f)

    # Handle both formats: list or {products: [...]}
    if isinstance(raw, list):
        products = raw
    else:
        products = raw.get("products", raw)

    print(f"Loaded {len(products)} products from {data_path}")

    embedder = get_embedder()
    store = get_vector_store()
    vdb_cfg = config["vector_db"]
    dimension = config["embedding"]["default"]["dimensions"]

    # Create collection (drop if exists)
    try:
        await store.create_collection(collection, dimension)
        print(f"Created collection '{collection}' (dim={dimension})")
    except Exception:
        print(f"Collection '{collection}' may already exist, continuing...")

    # Vectorize and upsert in batches
    total = len(products)
    for i in range(0, total, batch_size):
        batch = products[i : i + batch_size]
        texts = [p.get("embedding_text", p["name"]) for p in batch]
        ids = [i + j + 1 for j in range(len(batch))]
        payloads = [
            {
                "product_id": p["product_id"],
                "name": p["name"],
                "category": p.get("category", ""),
                "product_type": p.get("product_type", ""),
                "brand": p.get("brand", ""),
                "price": p.get("price", 0),
                "stock": p.get("stock", 0),
                "reputation": p.get("reputation", 0.5),
                "platform_id": p.get("platform_id", ""),
                "promotion_id": p.get("promotion_id"),
                "features": p.get("features", []),
                "rating": p.get("rating", 0),
                "delivery_minutes": p.get("delivery_minutes", 0),
                "image_url": p.get("image_url", ""),
            }
            for p in batch
        ]

        vectors = await embedder.aembed_batch(texts)
        await store.upsert(collection, ids, vectors, payloads)
        print(f"  Upserted {min(i + batch_size, total)}/{total}")

    print(f"Done. {total} products indexed in '{collection}'.")


def main():
    parser = argparse.ArgumentParser(description="Build Qdrant vector index")
    parser.add_argument("--data", default="data/mock_data.json", help="Product data path")
    parser.add_argument("--collection", default=None, help="Qdrant collection name")
    parser.add_argument("--batch-size", type=int, default=32, help="Embedding batch size")
    args = parser.parse_args()

    collection = args.collection or config["vector_db"]["collection"]
    asyncio.run(build_index(args.data, collection, args.batch_size))


if __name__ == "__main__":
    main()
