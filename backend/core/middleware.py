"""Request-id tracing + optional API-key auth + rate limiting.

Auth is opt-in: set API_KEY in env to require an X-API-Key header on every
request. No API_KEY set -> open, same as before (dev-friendly default, but
now there's a real switch to flip for prod instead of nothing at all).

Rate limiting is a fixed window per client IP. Two backends:

- REDIS_URL set   -> shared counters, correct across workers and instances.
- REDIS_URL empty -> in-process dicts. Fine for one worker; with N workers the
  effective limit is N x RATE_LIMIT_PER_MINUTE, so startup warns about it.
"""

import logging
import secrets
import time
import uuid
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.core.config import get_settings

settings = get_settings()
logger = logging.getLogger("careeros")

_request_counts: dict[str, deque] = defaultdict(deque)
_burst_counts: dict[str, deque] = defaultdict(deque)
_RATE_LIMIT_WINDOW_SECONDS = 60

# Created lazily on first use so importing this module never touches Redis.
_redis = None
# Set only when importing the client itself fails (bad REDIS_URL, missing redis
# package) — that will not fix itself, so we stop retrying. A *connection*
# failure is different: it is reported per request and retried, because a
# transient outage must not permanently demote a multi-worker deploy to
# per-process counters. The previous code set this on any exception and its
# docstring claimed the opposite, so a single blip at startup silently left the
# deploy running the exact multi-worker bug this backend exists to prevent.
_redis_client_broken = False


def _get_redis():
    """Return a Redis client, or None if REDIS_URL is unset or unusable."""
    global _redis, _redis_client_broken
    if _redis is None and settings.redis_url and not _redis_client_broken:
        try:
            import redis.asyncio as aioredis

            _redis = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        except Exception as exc:
            _redis_client_broken = True
            logger.error("REDIS_URL is set but redis client init failed (%s)", exc)
    return _redis


async def _redis_limit(client, key: str, limit: int, window: int) -> bool:
    """Increment `key` and return True if the caller is now over `limit`.

    INCR + EXPIRE is atomic per key, so concurrent workers cannot both read
    "count == limit-1" and both admit.
    """
    count = await client.incr(key)
    if count == 1:
        await client.expire(key, window)
    return count > limit


def _memory_limit(client_ip: str, store: dict, limit: int, window: int) -> bool:
    now = time.monotonic()
    bucket = store[client_ip]
    while bucket and now - bucket[0] > window:
        bucket.popleft()
    if not bucket:
        # The window expired, so this client is under the limit either way. Drop
        # the key and start a fresh deque: store is a defaultdict, so a client
        # that stops sending would otherwise leave its deque in the dict forever
        # and the dict would grow for the life of the process. Only an *empty*
        # bucket is reclaimed — deleting a full one would hand an over-limit
        # client a free reset on every rejected request.
        store.pop(client_ip, None)
        bucket = store[client_ip]
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    return False


def _warn_if_unsafe_rate_limit_config() -> None:
    """Log a warning if the configured rate limit is self-defeating, or if
    per-process counters are in use where a shared counter is needed."""
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

    if (
        settings.rate_limit_per_minute > 0
        and not settings.redis_url
        and settings.api_key
        and settings.app_env == "production"
    ):
        # API_KEY + production both indicate real deployment intent, which is
        # exactly where a per-process counter under-limits.
        logger.warning(
            "Rate limiting uses in-process counters (REDIS_URL is not set). "
            "With multiple workers each worker counts independently, so the "
            "effective limit is workers x RATE_LIMIT_PER_MINUTE. Set REDIS_URL "
            "to share counters, or run a single worker."
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

    @staticmethod
    def _throttled(request: Request, reason: str, client_ip: str) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        logger.warning("request_id=%s rate_limited ip=%s (%s)", request_id, client_ip, reason)
        return JSONResponse(
            status_code=429,
            content={"detail": "rate limit exceeded, try again shortly"},
            headers={"Retry-After": str(_RATE_LIMIT_WINDOW_SECONDS)},
        )

    async def dispatch(self, request: Request, call_next):
        # Both probes are exempt. /health/ready was not, which is the worst one to
        # get wrong: the container HEALTHCHECK hits it every 30s, and an
        # orchestrator polls it hardest exactly when the DB is down — so a
        # rate-limited readiness probe answers 429 to the one caller that needs a
        # truthful answer, and Docker marks the container unhealthy.
        if request.url.path in ("/health", "/health/ready"):
            return await call_next(request)
        if self.limit <= 0:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        redis = _get_redis()

        if redis is not None:
            try:
                # Minute-aligned buckets so the counter resets on a wall-clock
                # boundary shared by every worker, rather than per-worker TTLs.
                bucket = int(time.time() // _RATE_LIMIT_WINDOW_SECONDS)
                if await _redis_limit(redis, f"rl:min:{client_ip}:{bucket}", self.limit, _RATE_LIMIT_WINDOW_SECONDS * 2):
                    return self._throttled(request, "standard limit", client_ip)
                burst = int(time.time() // self.burst_window)
                if await _redis_limit(redis, f"rl:burst:{client_ip}:{burst}", self.burst_limit, self.burst_window * 2):
                    return self._throttled(request, "burst limit", client_ip)
                return await call_next(request)
            except Exception as exc:
                # Fail open rather than 500 every request because the counter store
                # is briefly unreachable, but say so loudly — this is exactly the
                # state the Redis backend exists to avoid.
                logger.error("redis rate-limit backend failed (%s); failing open", exc)

        if _memory_limit(client_ip, _request_counts, self.limit, _RATE_LIMIT_WINDOW_SECONDS):
            return self._throttled(request, "standard limit", client_ip)
        if _memory_limit(client_ip, _burst_counts, self.burst_limit, self.burst_window):
            return self._throttled(request, "burst limit", client_ip)
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
    # Constant-time: a plain `!=` on a shared secret leaks its length and prefix
    # through response timing. Compared as bytes because compare_digest() rejects
    # non-ASCII str, and an API key with a non-ASCII character would otherwise
    # turn every request into a 500 instead of a 401.
    if not secrets.compare_digest(provided.encode(), settings.api_key.encode()):
        raise HTTPException(status_code=401, detail="invalid X-API-Key — value doesn't match backend API_KEY")
