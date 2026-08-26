"""FastAPI entrypoint. Phase 1 vertical slice: API -> normalize -> dedup -> MySQL -> filter."""

import contextlib
import logging
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.core.db import Base, engine, get_db, sync_schema
from backend.core.middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    _warn_if_unsafe_rate_limit_config,
    require_api_key,
)
from backend.models import Job
from backend.schemas import JobOut, MatchedJobOut, MatchRequest, SearchRequest
from backend.services.dedup import upsert_job
from backend.services.filters import apply_hard_filters
from backend.services.job_api_adapter import get_adapter
from backend.services.normalize import normalize_job

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("careeros")
settings = get_settings()

_warn_if_unsafe_rate_limit_config()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create database tables, then widen any drifted columns
    Base.metadata.create_all(bind=engine)
    sync_schema()
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

    # Check rate limiting configuration
    health_status["checks"]["rate_limit"] = {
        "status": "ok" if not (settings.api_key and settings.app_env == "production") else "configurable",
        "detail": f"rate_limit_per_minute={settings.rate_limit_per_minute}"
    }
    if health_status["checks"]["rate_limit"]["status"] == "degraded":
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

    # Build base query with filters that can be applied at the database level
    query = select(Job).where(Job.is_active == True)  # noqa: E712 — SQLAlchemy column comparison, not a Python bool check

    # Apply employment type filter if specified
    if req.employment_type and req.employment_type != "unknown":
        query = query.where(Job.employment_type == req.employment_type)

    # Apply experience level filter if specified
    if req.experience_level and req.experience_level != "unknown":
        query = query.where(Job.experience_level == req.experience_level)

    # Apply remote only filter if specified
    if req.remote_only:
        query = query.where(Job.remote == "remote")

    # Apply source filter if specified (e.g. only "company" or only paid-API sources)
    if req.sources:
        query = query.where(Job.source.in_(req.sources))

    # Apply minimum salary filter if specified
    if req.min_salary is not None:
        # Only exclude jobs where we know the max salary is less than required
        # Unknown salaries are not excluded (spec 2.3)
        query = query.where(
            (Job.salary_max.is_(None)) | (Job.salary_max >= req.min_salary)
        )

    # Apply posted within days filter if specified
    if req.posted_within_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=req.posted_within_days)
        # Only exclude jobs where we know the posted date is before cutoff
        # Unknown dates are not excluded (spec 2.3)
        query = query.where(
            (Job.posted_at.is_(None)) | (Job.posted_at >= cutoff)
        )

    # Execute the query to get candidate jobs
    candidate_jobs = db.execute(query).scalars().all()

    # Apply remaining filters (location and query string) in Python
    # These are more complex to do efficiently in SQL with our current implementation
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
    from datetime import datetime, timezone

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