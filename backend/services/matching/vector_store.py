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
_collection_ready = False
_client_lock = threading.Lock()


def get_client() -> QdrantClient:
    global _client_singleton, _collection_ready
    if _client_singleton is not None:
        return _client_singleton
    with _client_lock:
        if _client_singleton is not None:
            return _client_singleton
        qdrant_url = getattr(settings, "qdrant_url", "") or ""
        if qdrant_url and (qdrant_url.startswith("http://") or qdrant_url.startswith("https://")):
            client = QdrantClient(url=qdrant_url)
        else:
            # Local file mode (default: ./qdrant_local)
            client = QdrantClient(path=qdrant_url or "./qdrant_local")
        # Publish the singleton only after the collection is confirmed. Assigning
        # first and calling ensure_collection after meant a failure there left a
        # live `_client_singleton` with `_collection_ready` still False — and the
        # fast path at the top of this function returns on the client alone, so
        # every later call short-circuited and never retried. One transient
        # startup blip then permanently broke indexing and search for the life of
        # the process, with no error on the second call to explain it.
        try:
            ensure_collection(client)
        except Exception:
            client.close()
            raise
        _client_singleton = client
        _collection_ready = True
    return _client_singleton


def close_client() -> None:
    """Release the Qdrant client. Called from the app lifespan on shutdown.

    In local file mode this matters more than it looks: QdrantClient registers a
    __del__ that closes the storage directory, and if the process exits without
    it, the close runs during interpreter teardown when sys.meta_path is already
    torn down. That import raises ImportError inside __del__, which Python cannot
    report and prints as an "Exception ignored" traceback on every shutdown —
    alarming noise on a deploy log that looks like a real failure.

    Local mode also holds a directory lock, so closing properly matters when a
    process is restarted in place.
    """
    global _client_singleton, _collection_ready
    with _client_lock:
        client, _client_singleton = _client_singleton, None
        _collection_ready = False
    if client is None:
        return
    try:
        client.close()
    except Exception:  # pragma: no cover - best-effort teardown
        pass


def ensure_collection(client: QdrantClient) -> None:
    """Idempotent, and called exactly once per process.

    It was previously called from both index_job and semantic_search, i.e. on
    every indexed job and every search. get_collections() is an HTTP round-trip
    (or a locked-directory read in local mode), so a 200-posting ingest issued
    200 of them before doing any useful work.
    """
    global _collection_ready
    if _collection_ready:
        return
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION not in existing:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=qm.VectorParams(size=_embedder.dim, distance=qm.Distance.COSINE),
        )


def index_job(client: QdrantClient, job_id: str, title: str, description: str, skills: list[str]) -> None:
    text = f"{title}\n{description}\n{' '.join(skills)}"
    vector = _embedder.embed(text)
    client.upsert(
        collection_name=COLLECTION,
        points=[qm.PointStruct(id=job_id, vector=vector, payload={"job_id": job_id})],
    )


def semantic_search(client: QdrantClient, query: str, limit: int = 20) -> list[tuple[str, float]]:
    vector = _embedder.embed(query)
    hits = client.query_points(collection_name=COLLECTION, query=vector, limit=limit).points
    return [(hit.payload["job_id"], hit.score) for hit in hits]
