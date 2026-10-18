"""Local reranker (spec 3.3: prefer local reranker, benchmark don't assume).

This is a transparent weighted-sum hybrid, not a cross-encoder — swap in a real
local cross-encoder reranker once benchmarked against CareerOS's own eval set,
per spec's explicit warning not to hard-code a model just because it's popular.
"""

from dataclasses import dataclass


@dataclass
class ScoredJob:
    job_id: str
    keyword_score: float
    semantic_score: float

    @property
    def hybrid_score(self) -> float:
        # equal weighting until benchmarked otherwise — documented, not hidden
        return 0.5 * self.keyword_score + 0.5 * self.semantic_score


def keyword_score(query: str, title: str, description: str) -> float:
    """Simple deterministic term-overlap score, 0..1."""
    q_terms = {t.lower() for t in query.split() if t}
    if not q_terms:
        return 0.0
    hay = f"{title} {description}".lower()
    hits = sum(1 for t in q_terms if t in hay)
    return hits / len(q_terms)


def rerank(scored: list[ScoredJob], top_k: int = 20) -> list[ScoredJob]:
    return sorted(scored, key=lambda s: s.hybrid_score, reverse=True)[:top_k]
