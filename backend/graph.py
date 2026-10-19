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
import logging
from datetime import datetime, timezone
from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.models import Job
from backend.schemas import MatchRequest, SearchRequest
from backend.services.matching.filters import apply_hard_filters, build_sql_filters
from backend.services.matching.model_router import get_model_router_for_request
from backend.services.matching.reranker import ScoredJob, keyword_score, rerank
from backend.services.matching.vector_store import get_client, index_job, semantic_search
from backend.services.sourcing.dedup import commit_batch, upsert_job
from backend.services.sourcing.job_api_adapter import get_adapter
from backend.services.sourcing.normalize import normalize_job

settings = get_settings()
logger = logging.getLogger("careeros")
_checkpointer_singleton: dict = {}

# What the checkpointer actually resolved to, recorded so /health can report it
# without awaiting. A silent fall back to MemorySaver previously looked identical
# to a healthy durable setup from the outside; now it is visible.
_checkpoint_status: dict = {"kind": "unknown", "detail": "checkpointer not initialised yet"}

# Fan-out cap for per-job LLM explanations. High enough to hide provider
# latency, low enough not to trip provider rate limits on a large top_k.
_EXPLAIN_CONCURRENCY = 5

# How many ranked jobs get an LLM explanation. Ranking scales to top_k (100);
# explaining does not. At concurrency 5 and a 30s provider timeout, explaining
# all of top_k meant ceil(100/5) x 30s = 10 minutes on the request path, with a
# pooled DB connection held for the whole of it. Rank on top_k, explain the
# head of the list, and let the tail carry scores with the static explanation.
_MAX_EXPLAIN_JOBS = 20

# How many eligible candidates to pull per requested result. Ranking is cheap;
# the expensive part is the per-job LLM explanation, which only runs on top_k.
_CANDIDATE_OVERSAMPLE = 10


def checkpoint_status() -> dict:
    """Snapshot of the active checkpointer. Safe to call from sync code."""
    return dict(_checkpoint_status)


def _mysql_dsn() -> str:
    # mysql+aiomysql:// scheme for AIOMySQLSaver compatibility. The password is
    # quoted: an unencoded '@' or ':' in it silently truncates the DSN and the
    # saver connects to the wrong host — or nowhere. settings.resolved_database_url
    # already does this for the main engine; this path had drifted.
    from urllib.parse import quote_plus

    return (
        f"mysql+aiomysql://{settings.mysql_user}:{quote_plus(settings.mysql_password)}"
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


def _ingest_and_filter(state: CareerOSState, db: Session, req: MatchRequest, raw_postings: list) -> None:
    """Blocking half of the discover node. Runs in a worker thread, not on the loop.

    ~1000 PyMySQL round-trips for a 200-posting ingest, plus a local-Qdrant
    upsert per new row. Inline in an async node that stalls every other
    in-flight request for the duration.
    """
    for raw in raw_postings:
        # Same isolation as /jobs/search: one malformed posting from one adapter
        # must not fail the whole match. Without this a single bad row failed the
        # node, and the request 500'd — the match path was stricter than search.
        try:
            with db.begin_nested():  # SAVEPOINT: a failure unwinds only this row
                normalized = normalize_job(raw)
                row, created = upsert_job(db, normalized)
            if created:
                client = get_client()
                index_job(client, row.id, row.title, row.description, row.skills_required or [])
        except Exception:
            logger.exception(
                "skipping job source=%s source_id=%s (ingest failed)", raw.source, raw.source_id
            )
    commit_batch(db)

    # Filter in SQL rather than loading every row: this used to be
    # `select(Job)` + a Python pass over the whole table (audit A4). The Python
    # predicate still runs over the (much smaller) SQL result, so it remains the
    # authority and the SQL clauses only have to be a superset.
    search_req = SearchRequest(
        query=req.query, location=req.location, employment_type=req.employment_type,
        experience_level=req.experience_level, remote_only=req.remote_only,
        min_salary=req.min_salary, max_salary=req.max_salary,
        posted_within_days=req.posted_within_days, sources=req.sources,
    )
    # One `now` shared by both filter passes — see the same note in
    # main._ingest_filter_and_select. Separate instants let the SQL pass be
    # stricter than the Python one, which breaks the superset contract.
    now = datetime.now(timezone.utc)
    candidates = db.execute(
        select(Job)
        .where(*build_sql_filters(search_req, now=now))
        .order_by(
            Job.posted_at.is_(None), Job.posted_at.desc(), Job.created_at.desc(), Job.id.desc()
        )
        .offset(req.offset)
        .limit(req.top_k * _CANDIDATE_OVERSAMPLE)
    ).scalars().all()
    filtered = apply_hard_filters(candidates, search_req, now=now)
    # Read ids out BEFORE the rollback — touching `j.id` after it would re-SELECT.
    state["candidate_ids"] = [j.id for j in filtered]
    # Release the pooled connection before the LLM fan-out. Without this the
    # session held a transaction open — and under MySQL's REPEATABLE READ, a
    # read view open for the whole request, which stalls the InnoDB purge thread
    # and grows the undo log.
    db.rollback()


async def _discover_normalize_dedup_filter(state: CareerOSState, db: Session) -> CareerOSState:
    req = MatchRequest(**state["request"])
    adapter = get_adapter(req.sources)
    raw_postings = await adapter.search(req.query, req.location)

    await asyncio.to_thread(_ingest_and_filter, state, db, req, raw_postings)
    return state


def _fetch_and_score(state: CareerOSState, db: Session, req: MatchRequest, semantic_hits: dict) -> None:
    """Blocking half of the retrieve node. Runs in a worker thread, not on the loop."""
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
    # Read-only node — same reasoning as _discover_normalize_dedup_filter.
    db.rollback()


async def _retrieve_and_rerank(state: CareerOSState, db: Session) -> CareerOSState:
    req = MatchRequest(**state["request"])

    def _search() -> dict:
        client = get_client()
        # This limit used to be a hardcoded 50, which silently capped the semantic
        # channel: a request with top_k > 50 could never surface more than 50
        # semantic candidates no matter how many the DB channel fetched. Scale it
        # with the same oversample the candidate pool uses, keeping 50 as the floor.
        return dict(
            semantic_search(client, req.query, limit=max(50, req.top_k * _CANDIDATE_OVERSAMPLE))
        )

    semantic_hits = await asyncio.to_thread(_search)
    await asyncio.to_thread(_fetch_and_score, state, db, req, semantic_hits)
    return state


async def _evidence_and_explain(state: CareerOSState, db: Session) -> CareerOSState:
    req = MatchRequest(**state["request"])
    # Use per-request provider/model if specified, otherwise use defaults
    router = get_model_router_for_request(
        llm_provider=req.llm_provider,
        model_name=req.model_name,
    )

    # Batch-load the jobs to explain. This used to be one db.get() per ranked
    # job inside the fan-out — an N+1 that the two call sites above already
    # fixed, and that this one had missed. At top_k=100 it was 100 extra
    # round-trips per request.
    to_explain = state["ranked"][:_MAX_EXPLAIN_JOBS]

    def _load_for_explain() -> dict:
        if not to_explain:
            return {}
        rows = db.execute(
            select(Job).where(Job.id.in_([e["job_id"] for e in to_explain]))
        ).scalars().all()
        # Copy title/description into plain tuples while the rows are still
        # loaded. Holding the ORM objects here cost a lazy re-SELECT per job
        # below, because the rollback expires them — and those reloads ran
        # inside asyncio.gather, interleaved with the LLM round-trips.
        payload = {j.id: (j.title, j.description) for j in rows}
        db.rollback()
        return payload

    explain_payload = await asyncio.to_thread(_load_for_explain)

    async def _explain_one(entry: dict) -> tuple[dict, object]:
        fields = explain_payload.get(entry["job_id"])
        if not fields:
            return entry, None
        return entry, await router.explain_match(
            fields[0], fields[1], req.candidate_skills
        )

    semaphore = asyncio.Semaphore(_EXPLAIN_CONCURRENCY)

    async def _explain_limited(entry: dict) -> tuple[dict, object]:
        async with semaphore:
            return await _explain_one(entry)

    # Bounded fan-out: one LLM round-trip per explained job, in parallel. A
    # serial loop here made /jobs/match latency O(n x provider latency).
    explained = await asyncio.gather(
        *(_explain_limited(entry) for entry in to_explain)
    )

    # Anything past _MAX_EXPLAIN_JOBS is ranked but not explained.
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
