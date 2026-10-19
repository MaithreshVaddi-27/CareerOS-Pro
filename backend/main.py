"""FastAPI entrypoint. Phase 1 vertical slice: API -> normalize -> dedup -> MySQL -> filter."""

import asyncio
import contextlib
import hashlib
import json
import logging
import re
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.core.db import get_db
from backend.core.middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    _warn_if_unsafe_rate_limit_config,
    require_api_key,
)
from backend.models import Job
from backend.schemas import JobOut, MatchedJobOut, MatchRequest, SearchRequest
from backend.services.matching.filters import apply_hard_filters, build_sql_filters
from backend.services.sourcing.dedup import commit_batch, upsert_job
from backend.services.sourcing.job_api_adapter import get_adapter
from backend.services.sourcing.normalize import normalize_job

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("careeros")
settings = get_settings()

_warn_if_unsafe_rate_limit_config()


def _enforce_production_auth() -> None:
    """Refuse to serve an unauthenticated API when APP_ENV=production.

    `require_api_key` is a no-op when API_KEY is empty, and `.env.example`
    ships `API_KEY=` — so the documented `cp .env.example .env` path produced a
    production-flagged process that answered every request from anyone who could
    reach port 8000. An empty key is a legitimate dev posture and must stay one;
    it just cannot be a production one. Failing at import means a
    misconfiguration is a refused start, not a silent open door.
    """
    if settings.app_env == "production" and not settings.api_key.strip():
        raise RuntimeError(
            "APP_ENV=production requires a non-empty API_KEY. Refusing to start with an "
            "unauthenticated API. Set API_KEY in .env (and VITE_API_KEY in frontend/.env to "
            "match), or set APP_ENV=development for local work."
        )


_enforce_production_auth()

# Any string that reaches a log line or a checkpoint hash should be bounded. The
# caps are far above realistic search input but stop an unbounded body from
# inflating log volume and forging log lines via embedded newlines.
_CREDENTIAL_RE = re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)\s*[=:]\s*\S+")


def _safe_detail(exc: Exception) -> str:
    """One-line, credential-scrubbed summary for a public response body.

    Driver exception strings carry the internal hostname, port, username and
    sometimes the schema name. /health is unauthenticated, so it returns the
    class of failure and nothing an attacker can map the network with.
    """
    detail = f"{type(exc).__name__}: {exc}".splitlines()[0]
    detail = _CREDENTIAL_RE.sub(lambda m: f"{m.group(1)}=***", detail)
    return detail[:200]


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is managed externally (create tables from models or your DBA).
    # No startup DDL here — keeps environments from drifting silently.
    yield  # Application runs here
    # Shutdown: close reusable HTTP clients, the Qdrant client, and the
    # checkpointer's context manager. The saver was stored in
    # graph._checkpointer_singleton["cm"] and never exited, so every shutdown
    # leaked its connection pool.
    from backend.graph import _checkpointer_singleton
    from backend.services.matching.model_router import aclose_client as _close_gemini
    from backend.services.matching.vector_store import close_client as _close_qdrant
    from backend.services.sourcing.job_api_adapter import _http_client
    from backend.services.sourcing.verification import _firecrawl_client
    from backend.services.sourcing.verification import _http_client as _verify_client

    await _close_gemini()
    for client in (_http_client, _verify_client, _firecrawl_client):
        if client is not None:
            await client.aclose()

    _close_qdrant()

    cm = _checkpointer_singleton.pop("cm", None)
    if cm is not None:
        try:
            await cm.__aexit__(None, None, None)
        except Exception:  # pragma: no cover - best-effort teardown
            logger.warning("checkpointer shutdown failed", exc_info=True)
    _checkpointer_singleton.clear()


app = FastAPI(title="CareerOS", version="1.0.0", lifespan=lifespan)


# Human, professional error surface. Users never see raw stack traces or
# internal details; every failure answers with a clear, actionable message.
@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    logger.warning("request_id=%s validation_error path=%s", request_id, request.url.path)
    problems = []
    for err in exc.errors():
        loc = " -> ".join(str(p) for p in err.get("loc", []) if p not in ("body", "query", "path"))
        msg = err.get("msg", "invalid value")
        problems.append(f"{loc}: {msg}" if loc else msg)
    detail = " ".join(problems) if problems else "The request could not be understood."
    return JSONResponse(
        status_code=422,
        content={"error": "invalid_request", "detail": detail, "request_id": request_id},
    )


@app.exception_handler(HTTPException)
async def _http_handler(request: Request, exc: HTTPException) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict", 422: "invalid_request", 429: "rate_limited", 503: "service_unavailable"}.get(
                exc.status_code, "error"
            ),
            "detail": exc.detail,
            "request_id": request_id,
        },
    )


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    logger.exception("request_id=%s unhandled_error path=%s", request_id, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "detail": "Something went wrong on our end. Please try again, and contact support if it keeps happening.",
            "request_id": request_id,
        },
    )

# CORS middleware - allow frontend origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.rate_limit_per_minute, burst_limit=settings.rate_limit_per_minute * 2, burst_window_seconds=10)
app.add_middleware(RequestContextMiddleware)


@app.get("/health")
def health():
    """Detailed health check endpoint per spec section 8.
    Checks database connectivity, API status, and system readiness.
    Returns overall status and individual component health.
    """

    health_status = {"status": "healthy", "checks": {}}

    # db.close() has to live in `finally`. It used to sit at the end of the try
    # block, so when the DB was down — the one case this probe exists to detect,
    # polled by Docker every 30s — the session was abandoned still holding a
    # checked-out connection for the garbage collector.
    db = None
    try:
        db = next(get_db())
        _ = db.execute(text("SELECT 1")).all()
        health_status["checks"]["database"] = {"status": "ok", "detail": "MySQL connection successful"}
    except Exception as e:
        health_status["checks"]["database"] = {"status": "degraded", "detail": _safe_detail(e)}
        health_status["status"] = "degraded"
    finally:
        if db is not None:
            db.close()

    # Rate limiting: report which counter store is actually in use. Per-process
    # counters under-limit by the worker count, so that is a degraded production
    # posture rather than a config detail.
    shared = bool(settings.redis_url)
    health_status["checks"]["rate_limit"] = {
        "status": "ok" if (shared or settings.app_env != "production") else "degraded",
        "backend": "redis" if shared else "in-process",
        "detail": (
            f"rate_limit_per_minute={settings.rate_limit_per_minute}, "
            f"backend={'redis (shared)' if shared else 'in-process (per-worker)'}"
        ),
    }
    if health_status["checks"]["rate_limit"]["status"] == "degraded":
        health_status["status"] = "degraded"

    # Checkpointer: a silent MemorySaver fallback looks identical to a healthy
    # durable setup from the outside, so surface which one is actually running.
    from backend.graph import checkpoint_status

    cp = checkpoint_status()
    cp_detail = _CREDENTIAL_RE.sub(lambda m: f"{m.group(1)}=***", cp["detail"].splitlines()[0])
    health_status["checks"]["checkpoint"] = {
        "status": "ok" if cp["kind"] in ("mysql", "memory") and not cp["detail"].startswith("DEGRADED") else "degraded",
        "kind": cp["kind"],
        "detail": cp_detail,
    }
    if health_status["checks"]["checkpoint"]["status"] == "degraded":
        health_status["status"] = "degraded"

    # Check embedding model availability
    from backend.services.matching.embeddings import get_embedding_provider
    try:
        provider = get_embedding_provider()
        _ = provider.embed("test")
        health_status["checks"]["embedding_model"] = {"status": "ok", "detail": "HashingVectorizer embedding ready"}
    except Exception as e:
        health_status["checks"]["embedding_model"] = {"status": "degraded", "detail": _safe_detail(e)}
        if health_status["status"] == "healthy":
            health_status["status"] = "degraded"

    # Check Qdrant vector store (local mode)
    from backend.services.matching.vector_store import get_client
    try:
        _ = get_client()
        # Just verify client can be created (collection check is separate)
        health_status["checks"]["vector_store"] = {"status": "ok", "detail": "Qdrant client initialized"}
    except Exception as e:
        health_status["checks"]["vector_store"] = {"status": "degraded", "detail": _safe_detail(e)}
        if health_status["status"] == "healthy":
            health_status["status"] = "degraded"

    return health_status


@app.get("/health/ready")
def readiness():
    """Readiness probe - 200 only when the app can actually serve a request.

    Gating on the database alone reported READY for a process that had silently
    degraded to in-memory checkpoints, which is exactly the failure
    CHECKPOINT_STRICT exists to prevent — so a deploy that lost checkpoint
    durability still looked green to Docker and to the load balancer. Outside
    development, a non-MySQL checkpointer is not ready.
    """
    db = None
    try:
        db = next(get_db())
        _ = db.execute(text("SELECT 1")).all()
    except Exception:
        logger.error("readiness database check failed", exc_info=True)
        raise HTTPException(status_code=503, detail="Not ready - database unavailable")
    finally:
        if db is not None:
            db.close()

    if settings.app_env != "development":
        from backend.graph import checkpoint_status

        kind = checkpoint_status()["kind"]
        if kind == "unknown":
            # Lazy init: no /jobs/match has run yet, so we cannot claim either
            # way. Reporting NOT READY here would deadlock a cold container.
            logger.info("readiness: checkpointer not initialised yet (kind=unknown) — admitting")
        elif kind != "mysql":
            raise HTTPException(
                status_code=503,
                detail=f"Not ready - checkpointer degraded to {kind!r}, checkpoints are not restart-durable",
            )

    return {"status": "ready"}


@app.get("/agents/health", dependencies=[Depends(require_api_key)])
def agents_health():
    """Supervisor view over every agent CareerOS runs — LLM providers
    (Gemini, see services/matching/model_router.py) and job
    source agents (jsearch/adzuna/remotive/remoteok/arbeitnow/company
    scraper, see services/sourcing/job_api_adapter.py). Each entry is per-process,
    in-memory, reset on restart — see core/monitoring.py.

    status per agent: 'unknown' (no calls yet), 'healthy', 'degraded'
    (>20% failure rate), or 'down' (every call so far failed).
    """
    from backend.core.monitoring import agent_monitor
    return agent_monitor.snapshot()


def _ingest_filter_and_select(db: Session, req: SearchRequest, raw_postings: list) -> list[JobOut]:
    """The blocking half of POST /jobs/search. Runs in a worker thread, not on the loop.

    Two things live here that used to be in the handler.

    The N+1: rollback() expires every ORM instance in the session, so reading the
    rows back afterwards re-SELECTs them one at a time. The handler returned
    `filtered` and let FastAPI's response_model validation touch those attributes
    — after the handler returned, on the event loop. Measured: 20 rows cost 20
    extra round-trips. Serialising to JobOut while the rows are still loaded
    removes them.

    The event loop: this is ~1000 blocking round-trips for a 200-posting ingest
    (SAVEPOINT + 2 SELECTs + INSERT + flush, per row, then a commit fsync). Inline
    in an async def, that stalls every other in-flight request for the duration.
    asyncio.to_thread moves the whole block off the loop without migrating the
    data layer to async SQLAlchemy.
    """
    for raw in raw_postings:
        try:
            # SAVEPOINT per posting: one bad row must not discard the other
            # 199 in the batch. A bare rollback() would now unwind every
            # unflushed insert since the last commit.
            with db.begin_nested():
                normalized = normalize_job(raw)
                _, created = upsert_job(db, normalized)
            if created:
                logger.info("job inserted source=%s source_id=%s", normalized.source, normalized.source_id)
        except Exception:
            logger.exception("skipping job source=%s source_id=%s (insert failed)", raw.source, raw.source_id)
    # One commit for the batch. upsert_job only flushes; MySQL fsyncs on commit.
    commit_batch(db)

    # Filter in SQL, then apply the authoritative Python predicate over the
    # result. build_sql_filters is a superset, so this is a narrowing second pass.
    # Previously this hand-rolled the same clauses, duplicating filter logic.
    #
    # One `now` shared by both passes. They must agree on the reference instant,
    # or the SQL pass runs against an earlier cutoff than the Python pass and can
    # be *stricter* — silently dropping a boundary row the Python predicate would
    # have kept, which is exactly what the superset contract forbids.
    now = datetime.now(timezone.utc)
    candidate_jobs = db.execute(
        select(Job)
        .where(*build_sql_filters(req, now=now))
        .order_by(
            Job.posted_at.is_(None), Job.posted_at.desc(), Job.created_at.desc(), Job.id.desc()
        )
        .offset(req.offset)
        .limit(req.limit)
    ).scalars().all()

    filtered = apply_hard_filters(candidate_jobs, req, now=now)
    # Materialise BEFORE rollback — after it, every attribute access is a SELECT.
    out = [JobOut.model_validate(job) for job in filtered]
    # Read-only tail: hand the pooled connection back before the response is
    # serialised. The ingest loop above already committed its own work.
    db.rollback()
    return out


@app.post("/jobs/search", response_model=list[JobOut], dependencies=[Depends(require_api_key)])
async def search_jobs(req: SearchRequest, db: Session = Depends(get_db)):
    """Discover -> Normalize -> Dedup -> Filter. Ranking/matching/LLM layers are Phase 2+."""
    adapter = get_adapter(req.sources)
    raw_postings = await adapter.search(req.query, req.location)
    logger.info("search fetched=%d query=%s", len(raw_postings), req.query)

    return await asyncio.to_thread(_ingest_filter_and_select, db, req, raw_postings)


@app.get("/jobs/{job_id}", response_model=JobOut, dependencies=[Depends(require_api_key)])
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(
            status_code=404,
            detail=f"We couldn't find a role with ID {job_id}. It may have been removed or expire — try searching again.",
        )
    return job


def _match_thread_id(req: MatchRequest) -> str:
    """Stable checkpoint thread id for a match request.

    This was `f"match-{id(req)}"` — id() of a Pydantic model is its memory
    address, so every request got a brand-new thread. With the MySQL checkpointer
    that writes checkpoint rows, /jobs/match grew the checkpoint table without
    bound in production, and an identical query never reused its prior state.

    A hash of the meaningful fields does both jobs properly: same query reuses
    the same thread, different query gets its own.
    """
    payload = json.dumps(req.model_dump(), sort_keys=True, default=str)
    return f"match-{hashlib.sha256(payload.encode()).hexdigest()[:32]}"


def _load_ranked_jobs(db: Session, ids: list[str]) -> dict[str, Job]:
    """Batch-load ranked jobs and serialise them before the connection goes back.

    Same rollback-expiry trap as /jobs/search: JobOut is built here, while the
    rows are still loaded, so response validation never re-SELECTs.
    """
    if not ids:
        return {}
    jobs = db.execute(select(Job).where(Job.id.in_(ids))).scalars().all()
    out = {j.id: JobOut.model_validate(j) for j in jobs}
    db.rollback()
    return out


@app.post("/jobs/match", response_model=list[MatchedJobOut], dependencies=[Depends(require_api_key)])
async def match_jobs(req: MatchRequest, db: Session = Depends(get_db)):
    """Full pipeline (spec section 4): discover -> normalize -> dedup -> filter
    -> hybrid retrieve -> rerank -> evidence-based explain. Stops before human
    review — nothing here marks anything as applied without a separate approval call.
    """
    from backend.graph import run_match_workflow

    thread_id = _match_thread_id(req)
    # Pass llm_provider and model_name to the workflow
    ranked = await run_match_workflow(db, req, thread_id, req.llm_provider, req.model_name)

    # Batch-load the jobs the graph already fetched, instead of db.get() per
    # ranked entry — this loop was an N+1 bounded only by top_k.
    # On a worker thread: the SELECT is blocking PyMySQL, not async.
    ids = [entry["job_id"] for entry in ranked]
    jobs_map = await asyncio.to_thread(_load_ranked_jobs, db, ids)

    out = []
    for entry in ranked:
        job = jobs_map.get(entry["job_id"])
        if not job:
            continue
        out.append(
            MatchedJobOut(
                job=job,
                keyword_score=entry["keyword_score"],
                semantic_score=entry["semantic_score"],
                hybrid_score=entry["hybrid_score"],
                evidence=entry.get("evidence", ""),
                matched_skills=entry.get("matched_skills", []),
                missing_skills=entry.get("missing_skills", []),
                explanation=entry.get("explanation", "unknown"),
                confidence=entry.get("confidence", 0.0),
            )
        )
    return out


@app.post("/jobs/{job_id}/verify", response_model=JobOut, dependencies=[Depends(require_api_key)])
async def verify_job(job_id: str, db: Session = Depends(get_db)):
    """Firecrawl-backed verification pass. Sets verified=True only on a positive
    confirmed result — reachability alone is not enough (spec 2.3: never guess).

    Deactivation is driven by `result.posting_closed`, a structured signal, not by
    substring-matching the human-readable `reason`. This used to check for
    "closed"/"filled" in the reason string, which meant a reworded message
    silently stopped deactivating anything — and, worse, any Firecrawl
    mis-detection on a live posting set is_active=False, after which retention
    hard-deletes the row. Data loss reached by string matching.
    """

    from backend.services.sourcing.verification import verify_job_posting

    # Read on a worker thread: db.get() is a blocking round-trip on PyMySQL.
    # We take only the three scalar fields the verifier needs, so nothing holds a
    # live ORM instance across the await below — the session used to stay open
    # (holding a pooled connection) for the full duration of an outbound HTTP call.
    def _load() -> tuple[str, str, str] | None:
        job = db.get(Job, job_id)
        return (job.apply_url, job.title, job.company) if job else None

    loaded = await asyncio.to_thread(_load)
    if loaded is None:
        raise HTTPException(
            status_code=404,
            detail=f"We couldn't find a role with ID {job_id} to verify. It may have been removed — try searching again.",
        )
    apply_url, title, company = loaded

    result = await verify_job_posting(apply_url, title, company)

    def _persist() -> JobOut:
        # Re-fetch rather than trusting the earlier read: the outbound scrape above
        # can take seconds, and a concurrent retention run (or another verify) can
        # have deleted the row in between. db.get() then returned None and the next
        # line raised AttributeError — a 500 for what is really a 404.
        job = db.get(Job, job_id)
        if job is None:
            raise HTTPException(
                status_code=404,
                detail=f"We couldn't find a role with ID {job_id} to save your changes. It may have been removed — try searching again.",
            )
        job.verified = result.verified
        if result.verified:
            job.verified_at = datetime.now(timezone.utc)
        if result.posting_closed:
            job.is_active = False
        db.commit()
        db.refresh(job)
        # Serialise before rollback for the same reason as /jobs/search: rollback
        # expires the instance, and touching it afterwards costs a re-SELECT.
        out = JobOut.model_validate(job)
        db.rollback()
        return out

    return await asyncio.to_thread(_persist)