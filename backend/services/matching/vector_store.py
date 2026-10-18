"""Qdrant = index, not canonical (spec 2.5). MySQL row id is stored as payload for join-back.

Local on-disk mode used here (no server needed) — swap `path=` for `url=QDRANT_URL`
against a real Qdrant instance in prod (configured via QDRANT_URL env var).

Local file-mode Qdrant locks its storage dir to a single process — get_client()
returns a process-wide singleton so multiple calls in one request don't collide.
A real Qdrant server has no such restriction (that's the concurrent-access case
the client's error message points at).
"""

import threading

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from backend.core.config import get_settings
from backend.services.matching.embeddings import EmbeddingProvider, get_embedding_provider

COLLECTION = "jobs"

settings = get_settings()
_embedder: EmbeddingProvider = get_embedding_provider()


_client_singleton: QdrantClient | None = None
_client_lock = threading.Lock()


def get_client() -> QdrantClient:
    global _client_singleton
    if _client_singleton is not None:
        return _client_singleton
    with _client_lock:
        if _client_singleton is not None:
            return _client_singleton
        qdrant_url = getattr(settings, "qdrant_url", "") or ""
        if qdrant_url and (qdrant_url.startswith("http://") or qdrant_url.startswith("https://")):
            _client_singleton = QdrantClient(url=qdrant_url)
        else:
            # Local file mode (default: ./qdrant_local)
            _client_singleton = QdrantClient(path=qdrant_url or "./qdrant_local")
    return _client_singleton


def ensure_collection(client: QdrantClient) -> None:
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION not in existing:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=qm.VectorParams(size=_embedder.dim, distance=qm.Distance.COSINE),
        )


def index_job(client: QdrantClient, job_id: str, title: str, description: str, skills: list[str]) -> None:
    ensure_collection(client)
    text = f"{title}\n{description}\n{' '.join(skills)}"
    vector = _embedder.embed(text)
    client.upsert(
        collection_name=COLLECTION,
        points=[qm.PointStruct(id=job_id, vector=vector, payload={"job_id": job_id})],
    )


def semantic_search(client: QdrantClient, query: str, limit: int = 20) -> list[tuple[str, float]]:
    ensure_collection(client)
    vector = _embedder.embed(query)
    hits = client.query_points(collection_name=COLLECTION, query=vector, limit=limit).points
    return [(hit.payload["job_id"], hit.score) for hit in hits]
