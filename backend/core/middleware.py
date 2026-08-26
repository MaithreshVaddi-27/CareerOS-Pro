"""Request-id tracing + optional API-key auth + in-memory rate limiting.

Auth is opt-in: set API_KEY in env to require an X-API-Key header on every
request. No API_KEY set -> open, same as before (dev-friendly default, but
now there's a real switch to flip for prod instead of nothing at all).

Rate limiting is a simple in-memory sliding window per client IP — good
enough for a single-process deploy; swap for a Redis-backed limiter if you
run multiple workers/instances and need a shared counter.
"""

import logging
import time
import uuid
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

from backend.core.config import get_settings

settings = get_settings()
logger = logging.getLogger("careeros")

_request_counts: dict[str, deque] = defaultdict(deque)
_burst_counts: dict[str, deque] = defaultdict(deque)
_RATE_LIMIT_WINDOW_SECONDS = 60


def _warn_if_unsafe_rate_limit_config() -> None:
    """Log a warning if multi-worker deployment with in-memory rate limiting."""
    # In production with workers > 1, in-memory rate limiting is per-process
    # and won't be shared across workers. This is a common misconfiguration.
    if settings.rate_limit_per_minute <= 0:
        logger.warning(
            f"rate_limit_per_minute must be positive, got {settings.rate_limit_per_minute}. "
            "Using default of 60."
        )
    elif settings.rate_limit_per_minute < 10:
        logger.warning(
            f"rate_limit_per_minute is very low ({settings.rate_limit_per_minute}). "
            "This may block legitimate traffic. Consider a minimum of 10-20 requests/minute."
        )
    elif settings.rate_limit_per_minute > 1000:
        logger.warning(
            f"rate_limit_per_minute is very high ({settings.rate_limit_per_minute}). "
            "This may be ineffective as a rate limit. Consider a maximum of 100-200 requests/minute for most APIs."
        )
    elif settings.rate_limit_per_minute and settings.app_env == "production":
        # Granian sets WORKER_COUNT env or can be detected via process count
        # We can't reliably detect worker count here, so warn if API_KEY is set
        # (indicating prod intent) and rate limiting is enabled
        if settings.api_key:
            logger.warning(
                "Rate limiting enabled with in-memory backend in production. "
                "With multiple workers, each worker has a separate counter. "
                "Use a Redis-backed limiter for shared rate limiting."
            )


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, logs method/path/status/duration for every call,
    and sets baseline security response headers (none of these were being
    sent before — a fresh deploy had zero of the usual browser hardening
    headers, e.g. easy clickjacking via iframe embed with no X-Frame-Options)."""

    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())[:8]
        request.state.request_id = request_id
        start = time.monotonic()

        response = await call_next(request)

        duration_ms = (time.monotonic() - start) * 1000
        response.headers["X-Request-ID"] = request_id

        # Baseline hardening — cheap, no downside for an API, standard practice.
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if settings.app_env == "production":
            # Only send HSTS in prod — sending it over plain HTTP in local dev
            # would get cached by the browser and break http://localhost.
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"

        logger.info(
            "request_id=%s method=%s path=%s status=%d duration_ms=%.1f",
            request_id, request.method, request.url.path, response.status_code, duration_ms,
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, requests_per_minute: int = 60, burst_limit: int = None, burst_window_seconds: int = 10):
        super().__init__(app)
        self.limit = requests_per_minute
        self.burst_limit = burst_limit if burst_limit is not None else requests_per_minute
        self.burst_window = burst_window_seconds
        # Separate tracking for burst window - use module-level variable
        pass  # _burst_counts is accessed as a global variable in dispatch method

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()

        # Check standard rate limit (60-second window)
        window = _request_counts[client_ip]
        while window and now - window[0] > _RATE_LIMIT_WINDOW_SECONDS:
            window.popleft()

        if len(window) >= self.limit:
            request_id = getattr(request.state, "request_id", "unknown")
            logger.warning("request_id=%s rate_limited ip=%s (standard limit)", request_id, client_ip)
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=429, content={"detail": "rate limit exceeded, try again shortly"})

        # Check burst limit (shorter window)
        burst_window = _burst_counts[client_ip]
        while burst_window and now - burst_window[0] > self.burst_window:
            burst_window.popleft()

        if len(burst_window) >= self.burst_limit:
            request_id = getattr(request.state, "request_id", "unknown")
            logger.warning("request_id=%s rate_limited ip=%s (burst limit)", request_id, client_ip)
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=429, content={"detail": "rate limit exceeded, try again shortly"})

        # Add request to both windows
        window.append(now)
        burst_window.append(now)
        return await call_next(request)


async def require_api_key(request: Request):
    """FastAPI dependency. No-op when API_KEY isn't configured (dev default)."""
    if not settings.api_key:
        return
    provided = request.headers.get("X-API-Key")
    if provided is None:
        raise HTTPException(
            status_code=401,
            detail=(
                "missing X-API-Key header — backend API_KEY is set, but this request sent none. "
                "If you're using the bundled frontend, set VITE_API_KEY in frontend/.env to match "
                "backend's API_KEY and restart the dev server."
            ),
        )
    if provided != settings.api_key:
        raise HTTPException(status_code=401, detail="invalid X-API-Key — value doesn't match backend API_KEY")
