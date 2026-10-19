"""Local embeddings (spec 3.3 'prefer local embedding model for cost/privacy').

Sandbox note: real sentence-transformer weights need a Hugging Face download,
not reachable from this environment's network allowlist. HashingVectorizer
gives deterministic, dependency-free local vectors so the retrieval pipeline
is real and testable now. Swap in a real local model (e.g. bge-small via
sentence-transformers) when running somewhere with model-hub access — the
EmbeddingProvider interface below doesn't change either way.
"""

import functools
from typing import Protocol

from sklearn.feature_extraction.text import HashingVectorizer


class EmbeddingProvider(Protocol):
    dim: int

    def embed(self, text: str) -> list[float]: ...
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


_VECTOR_SIZE = 256


@functools.lru_cache(maxsize=1)
def _get_vectorizer() -> HashingVectorizer:
    """Create and cache the HashingVectorizer instance.

    The docstring always claimed this was cached and the code never cached it.
    Construction runs sklearn's param validation and rebuilds the internal
    feature hasher, so this happened once per embed() call — and embed() is
    called per indexed job plus per explained job, i.e. hundreds of times on a
    single /jobs/match request.
    """
    return HashingVectorizer(
        n_features=_VECTOR_SIZE,
        alternate_sign=False,
        norm="l2",
    )


class LocalHashingEmbedding:
    dim = _VECTOR_SIZE

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        matrix = _get_vectorizer().transform(texts)
        return matrix.toarray().tolist()


@functools.lru_cache(maxsize=1)
def get_embedding_provider() -> EmbeddingProvider:
    """Get cached embedding provider instance."""
    return LocalHashingEmbedding()