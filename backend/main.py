"""FastAPI entrypoint. Phase 1 vertical slice: API -> normalize -> dedup -> MySQL -> filter."""

import contextlib
import logging
from datetime import datetime, timezone

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
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
from backend.services.dedup import upsert_job
from backend.services.filters import apply_hard_filters, build_sql_filters
from backend.services.job_api_adapter import get_adapter
from backend.services.normalize import normalize_job

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("careeros")
settings = get_settings()

_warn_if_unsafe_rate_limit_config()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # No schema DDL here. Alembic is the single source of truth for schema —
    # run `alembic upgrade head` as a deploy step. Startup DDL (create_all +
    # a widen-VARCHAR patch) was what let environments drift apart silently.
    yield  # Application runs here
    # Shutdown: close reusable HTTP clients
    from backend.services.job_api_adapter import _http_client
    from backend.services.verification import _firecrawl_client
    from backend.services.verification import _http_client as _verify_client
    for client in (_http_client, _verify_client, _firecrawl_client):
        if client is not None:
            await client.aclose()


app = FastAPI(title="CareerOS", version="0.2.0", lifespan=lifespan)

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

    # Check database connectivity
    try:
        db_gen = get_db()
        db = next(db_gen)
        # Test basic query
        from sqlalchemy import text
        _ = db.execute(text("SELECT 1")).all()
        health_status["checks"]["database"] = {"status": "ok", "detail": "MySQL connection successful"}
        db.close()
    except Exception as e:
        health_status["checks"]["database"] = {"status": "degraded", "detail": str(e)}
        health_status["status"] = "degraded"

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
    health_status["checks"]["checkpoint"] = {
        "status": "ok" if cp["kind"] in ("mysql", "memory") and not cp["detail"].startswith("DEGRADED") else "degraded",
        "kind": cp["kind"],
        "detail": cp["detail"],
    }
    if health_status["checks"]["checkpoint"]["status"] == "degraded":
        health_status["status"] = "degraded"

    # Check embedding model availability
    from backend.services.embeddings import get_embedding_provider
    try:
        provider = get_embedding_provider()
        _ = provider.embed("test")
        health_status["checks"]["embedding_model"] = {"status": "ok", "detail": "HashingVectorizer embedding ready"}
    except Exception as e:
        health_status["checks"]["embedding_model"] = {"status": "degraded", "detail": str(e)}
        if health_status["status"] == "healthy":
            health_status["status"] = "degraded"

    # Check Qdrant vector store (local mode)
    from backend.services.vector_store import get_client
    try:
        _ = get_client()
        # Just verify client can be created (collection check is separate)
        health_status["checks"]["vector_store"] = {"status": "ok", "detail": "Qdrant client initialized"}
    except Exception as e:
        health_status["checks"]["vector_store"] = {"status": "degraded", "detail": str(e)}
        if health_status["status"] == "healthy":
            health_status["status"] = "degraded"

    return health_status


@app.get("/health/ready")
def readiness():
    """Readiness probe - returns 200 if the app can serve requests, 503 otherwise."""
    from fastapi import HTTPException

    # Quick database check
    try:
        db_gen = get_db()
        db = next(db_gen)
        _ = db.execute(text("SELECT 1")).all()
        db.close()
        return {"status": "ready"}
    except Exception:
        raise HTTPException(status_code=503, detail="Not ready - database unavailable")


@app.get("/agents/health", dependencies=[Depends(require_api_key)])
def agents_health():
    """Supervisor view over every agent CareerOS runs — LLM providers
    (llama/nvidia/openrouter/gemini, see services/model_router.py) and job
    source agents (jsearch/adzuna/remotive/remoteok/arbeitnow/company
    scraper, see services/job_api_adapter.py). Each entry is per-process,
    in-memory, reset on restart — see core/monitoring.py.

    status per agent: 'unknown' (no calls yet), 'healthy', 'degraded'
    (>20% failure rate), or 'down' (every call so far failed).
    """
    from backend.core.monitoring import agent_monitor
    return agent_monitor.snapshot()


@app.post("/jobs/search", response_model=list[JobOut], dependencies=[Depends(require_api_key)])
async def search_jobs(req: SearchRequest, db: Session = Depends(get_db)):
    """Discover -> Normalize -> Dedup -> Filter. Ranking/matching/LLM layers are Phase 2+."""
    adapter = get_adapter()
    raw_postings = await adapter.search(req.query, req.location)
    logger.info("search fetched=%d query=%s", len(raw_postings), req.query)

    for raw in raw_postings:
        try:
            normalized = normalize_job(raw)
            _, created = upsert_job(db, normalized)
            if created:
                logger.info("job inserted source=%s source_id=%s", normalized.source, normalized.source_id)
        except Exception:
            db.rollback()
            logger.exception("skipping job source=%s source_id=%s (insert failed)", raw.source, raw.source_id)

    # Filter in SQL, then apply the authoritative Python predicate over the
    # result. build_sql_filters is a superset, so this is a narrowing second pass.
    # Previously this hand-rolled the same clauses, duplicating filter logic.
    candidate_jobs = db.execute(
        select(Job)
        .where(*build_sql_filters(req))
        .order_by(Job.posted_at.desc().nullslast(), Job.created_at.desc())
        .offset(req.offset)
        .limit(req.limit)
    ).scalars().all()

    filtered = apply_hard_filters(candidate_jobs, req)
    return filtered


@app.get("/jobs/{job_id}", response_model=JobOut, dependencies=[Depends(require_api_key)])
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="job not found")
    return job


@app.post("/jobs/match", response_model=list[MatchedJobOut], dependencies=[Depends(require_api_key)])
async def match_jobs(req: MatchRequest, db: Session = Depends(get_db)):
    """Full pipeline (spec section 4): discover -> normalize -> dedup -> filter
    -> hybrid retrieve -> rerank -> evidence-based explain. Stops before human
    review — nothing here marks anything as applied without a separate approval call.
    """
    from backend.graph import run_match_workflow

    thread_id = f"match-{id(req)}"
    # Pass llm_provider and model_name to the workflow
    ranked = await run_match_workflow(db, req, thread_id, req.llm_provider, req.model_name)

    out = []
    for entry in ranked:
        job = db.get(Job, entry["job_id"])
        if not job:
            continue
        out.append(
            MatchedJobOut(
                job=JobOut.model_validate(job),
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
    confirmed result — reachability alone is not enough (spec 2.3: never guess)."""

    from fastapi import HTTPException

    from backend.services.verification import verify_job_posting

    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")

    result = await verify_job_posting(job.apply_url, job.title, job.company)
    job.verified = result.verified
    job.verified_at = datetime.now(timezone.utc) if result.verified else job.verified_at
    if not result.verified and ("closed" in result.reason.lower() or "filled" in result.reason.lower()):
        job.is_active = False
    db.commit()
    db.refresh(job)
    return job