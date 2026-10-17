"""LangGraph = master workflow/state orchestration ONLY (spec section 5).
Not a place for business logic — every node below just calls existing
deterministic/service functions. Human-in-the-loop uses an interrupt before
anything gets marked ready-to-apply, matching the ARCHITECTURE flow:
TOP 10-20 -> HUMAN REVIEW -> APPROVE/REJECT (spec section 4).

Checkpointing: MySQL-backed (AIOMySQLSaver from langgraph-checkpoint-mysql) in
production — no Postgres dependency, reuses the same MySQL instance that's
already the system of record (spec 2.5). Falls back to in-memory MemorySaver
for local dev (APP_ENV=development), since that's dev-only by
definition and doesn't need to survive a restart.
"""

import asyncio
from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.models import Job
from backend.schemas import MatchRequest, SearchRequest
from backend.services.dedup import upsert_job
from backend.services.filters import apply_hard_filters, build_sql_filters
from backend.services.job_api_adapter import get_adapter
from backend.services.model_router import get_model_router_for_request
from backend.services.normalize import normalize_job
from backend.services.reranker import ScoredJob, keyword_score, rerank
from backend.services.vector_store import get_client, index_job, semantic_search

settings = get_settings()
_checkpointer_singleton: dict = {}

# What the checkpointer actually resolved to, recorded so /health can report it
# without awaiting. A silent fall back to MemorySaver previously looked identical
# to a healthy durable setup from the outside; now it is visible.
_checkpoint_status: dict = {"kind": "unknown", "detail": "checkpointer not initialised yet"}

# Fan-out cap for per-job LLM explanations. High enough to hide provider
# latency, low enough not to trip provider rate limits on a large top_k.
_EXPLAIN_CONCURRENCY = 5

# How many eligible candidates to pull per requested result. Ranking is cheap;
# the expensive part is the per-job LLM explanation, which only runs on top_k.
_CANDIDATE_OVERSAMPLE = 10


def checkpoint_status() -> dict:
    """Snapshot of the active checkpointer. Safe to call from sync code."""
    return dict(_checkpoint_status)


def _mysql_dsn() -> str:
    # Use mysql+aiomysql:// scheme for AIOMySQLSaver compatibility
    return (
        f"mysql+aiomysql://{settings.mysql_user}:{settings.mysql_password}"
        f"@{settings.mysql_host}:{settings.mysql_port}/{settings.mysql_database}"
    )


async def get_checkpointer():
    """MySQL-backed in production, in-memory for local dev.

    Falls back to MemorySaver if MySQL checkpointing fails (e.g. MySQL 9.x
    disallows MD5 in generated columns used by AIOMySQLSaver.setup()).
    MemorySaver is fine for dev — checkpoints survive the request, not a restart.

    Set CHECKPOINT_STRICT=true to refuse that fallback in production: there,
    a silent downgrade means human-review state vanishes on restart.
    """
    # Use MemorySaver for local development
    if settings.app_env == "development":
        _checkpoint_status.update(
            kind="memory", detail="MemorySaver (APP_ENV=development, not restart-durable)"
        )
        return MemorySaver()

    if "instance" in _checkpointer_singleton:
        return _checkpointer_singleton["instance"]

    try:
        from langgraph.checkpoint.mysql.aio import AIOMySQLSaver

        cm = AIOMySQLSaver.from_conn_string(_mysql_dsn())
        checkpointer = await cm.__aenter__()
        await checkpointer.setup()  # idempotent — creates tables first time only
        _checkpointer_singleton["cm"] = cm
        _checkpointer_singleton["instance"] = checkpointer
        _checkpoint_status.update(
            kind="mysql", detail="AIOMySQLSaver (restart-durable)"
        )
        return checkpointer
    except Exception as exc:
        import logging

        log = logging.getLogger("careeros")
        if settings.checkpoint_strict:
            _checkpoint_status.update(kind="unavailable", detail=f"MySQL setup failed: {exc}")
            raise
        log.warning(
            "MySQL checkpointer setup failed (%s) — falling back to MemorySaver. "
            "Checkpoints will NOT survive a restart. Use MySQL 8.x (9.x disallows "
            "the MD5 used in generated columns), or set CHECKPOINT_STRICT=true to "
            "fail fast instead of degrading.",
            exc,
        )
        saver = MemorySaver()
        _checkpointer_singleton["instance"] = saver
        _checkpoint_status.update(
            kind="memory",
            detail=f"DEGRADED - MySQL setup failed, checkpoints not restart-durable: {exc}",
        )
        return saver


class CareerOSState(TypedDict):
    request: dict
    candidate_ids: list[str]
    ranked: list[dict]
    approved_ids: list[str]
    rejected_ids: list[str]


async def _discover_normalize_dedup_filter(state: CareerOSState, db: Session) -> CareerOSState:
    req = MatchRequest(**state["request"])
    adapter = get_adapter()
    raw_postings = await adapter.search(req.query, req.location)

    for raw in raw_postings:
        normalized = normalize_job(raw)
        row, created = upsert_job(db, normalized)
        if created:
            client = get_client()
            index_job(client, row.id, row.title, row.description, row.skills_required or [])

    # Filter in SQL rather than loading every row: this used to be
    # `select(Job)` + a Python pass over the whole table (audit A4). The Python
    # predicate still runs over the (much smaller) SQL result, so it remains the
    # authority and the SQL clauses only have to be a superset.
    search_req = SearchRequest(
        query=req.query, location=req.location, employment_type=req.employment_type,
        experience_level=req.experience_level, remote_only=req.remote_only,
        min_salary=req.min_salary, posted_within_days=req.posted_within_days,
    )
    candidates = db.execute(
        select(Job)
        .where(*build_sql_filters(search_req))
        .order_by(Job.posted_at.desc().nullslast(), Job.created_at.desc())
        .offset(req.offset)
        .limit(req.top_k * _CANDIDATE_OVERSAMPLE)
    ).scalars().all()
    filtered = apply_hard_filters(candidates, search_req)
    state["candidate_ids"] = [j.id for j in filtered]
    return state


async def _retrieve_and_rerank(state: CareerOSState, db: Session) -> CareerOSState:
    req = MatchRequest(**state["request"])
    client = get_client()
    semantic_hits = dict(semantic_search(client, req.query, limit=50))

    # Batch fetch all jobs at once to avoid N+1 query problem
    # Only fetch active jobs to avoid loading inactive postings
    jobs_map = {}
    if state["candidate_ids"]:
        jobs = db.execute(
            select(Job).where(Job.id.in_(state["candidate_ids"]), Job.is_active == True)  # noqa: E712 — SQLAlchemy column comparison, not a Python bool check
        ).scalars().all()
        jobs_map = {job.id: job for job in jobs}

    scored = []
    for job_id in state["candidate_ids"]:
        job = jobs_map.get(job_id)
        if not job:
            continue
        kw = keyword_score(req.query, job.title, job.description)
        sem = semantic_hits.get(job_id, 0.0)
        scored.append(ScoredJob(job_id=job_id, keyword_score=kw, semantic_score=sem))

    top = rerank(scored, top_k=req.top_k)
    state["ranked"] = [{"job_id": s.job_id, "keyword_score": s.keyword_score,
                         "semantic_score": s.semantic_score, "hybrid_score": s.hybrid_score} for s in top]
    return state


async def _evidence_and_explain(state: CareerOSState, db: Session) -> CareerOSState:
    req = MatchRequest(**state["request"])
    # Use per-request provider/model if specified, otherwise use defaults
    router = get_model_router_for_request(
        llm_provider=req.llm_provider,
        model_name=req.model_name,
    )

    async def _explain_one(entry: dict) -> tuple[dict, object]:
        # db.get is synchronous and completes before the await, so the shared
        # Session is never touched across a suspension point.
        job = db.get(Job, entry["job_id"])
        if not job:
            return entry, None
        return entry, await router.explain_match(
            job.title, job.description, req.candidate_skills
        )

    semaphore = asyncio.Semaphore(_EXPLAIN_CONCURRENCY)

    async def _explain_limited(entry: dict) -> tuple[dict, object]:
        async with semaphore:
            return await _explain_one(entry)

    # Bounded fan-out: one LLM round-trip per job, but in parallel. A serial
    # loop here made /jobs/match latency O(top_k x provider latency).
    explained = await asyncio.gather(
        *(_explain_limited(entry) for entry in state["ranked"])
    )

    for entry, explanation in explained:
        if explanation is None:
            continue
        entry["evidence"] = explanation.evidence
        entry["matched_skills"] = explanation.matched_skills
        entry["missing_skills"] = explanation.missing_skills
        entry["explanation"] = explanation.explanation
        entry["confidence"] = explanation.confidence
    return state


def _human_review(state: CareerOSState) -> CareerOSState:
    """Interrupt point. Graph pauses here — caller resumes with approve/reject via Command."""
    return state


def build_graph(db: Session, checkpointer):
    graph = StateGraph(CareerOSState)

    async def node_discover(s: CareerOSState) -> CareerOSState:
        return await _discover_normalize_dedup_filter(s, db)

    async def node_retrieve(s: CareerOSState) -> CareerOSState:
        return await _retrieve_and_rerank(s, db)

    async def node_evidence(s: CareerOSState) -> CareerOSState:
        return await _evidence_and_explain(s, db)

    graph.add_node("discover_normalize_dedup_filter", node_discover)
    graph.add_node("retrieve_and_rerank", node_retrieve)
    graph.add_node("evidence_and_explain", node_evidence)
    graph.add_node("human_review", _human_review)

    graph.set_entry_point("discover_normalize_dedup_filter")
    graph.add_edge("discover_normalize_dedup_filter", "retrieve_and_rerank")
    graph.add_edge("retrieve_and_rerank", "evidence_and_explain")
    graph.add_edge("evidence_and_explain", "human_review")
    graph.add_edge("human_review", END)

    return graph.compile(checkpointer=checkpointer, interrupt_before=["human_review"])


async def run_match_workflow(db: Session, req: MatchRequest, thread_id: str, llm_provider: str | None = None, model_name: str | None = None) -> list[dict]:
    checkpointer = await get_checkpointer()
    app_graph = build_graph(db, checkpointer)
    config = {"configurable": {"thread_id": thread_id}}

    # Include llm_provider and model_name in request for the workflow
    req_dict = req.model_dump()
    req_dict["llm_provider"] = llm_provider
    req_dict["model_name"] = model_name

    initial: CareerOSState = {
        "request": req_dict, "candidate_ids": [], "ranked": [], "approved_ids": [], "rejected_ids": [],
    }
    result = await app_graph.ainvoke(initial, config=config)
    return result["ranked"]
