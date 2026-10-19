"""In-process agent monitor (spec: 'agent monitoring other agents').

CareerOS already runs several independent agents that can each fail on
their own without taking the system down:
  - The Gemini LLM provider agent in services/matching/model_router.py.
  - Job-source agents (JSearch / Adzuna / Remotive / RemoteOK / Arbeitnow /
    company-career-page scraper) in services/sourcing/job_api_adapter.py,
    fanned out in parallel.

Both already catch-and-continue on a single agent failing (that was part of
the earlier fix — one bad source shouldn't 500 the whole search). What was
missing: no visibility into *which* agent is failing, how often, or how slow
it is. This module is a tiny supervisor — every agent call reports in here,
and GET /agents/health on the API exposes it.

Deliberately NOT a queue, NOT a separate process, NOT a new dependency —
in-memory counters behind a lock, good enough for a single-process deploy.
For multi-worker/multi-instance production, back this with Redis the same
way the rate limiter needs it (see README Troubleshooting).

`scrub_secrets` below is load-bearing security, not cosmetics — see its
docstring for the leak it closes.
"""

from __future__ import annotations

import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

# Query parameters that carry a provider credential. httpx stringifies the full
# request URL into HTTPStatusError, so any agent that authenticates by query
# param puts the secret straight into the exception string.
#
#   Gemini  ->  ...models/gemini-flash-latest:generateContent?key=AIza...
#   Adzuna  ->  .../search/1?app_id=...&app_key=...
#
# Both reach this module. Adzuna was scrubbed at its own call site; Gemini was
# not, and its `last_error` was readable on GET /agents/health.
#
# The leading [?&] is what keeps this from over-redacting: it only matches a
# value that is genuinely in a query string, never a bare word like "monkey=".
_SECRET_QUERY_KEYS = (
    "key", "api_key", "apikey", "app_id", "app_key",
    "token", "access_token", "api_secret", "secret",
)
_SECRET_PARAM_RE = re.compile(
    r"(?i)([?&](?:" + "|".join(_SECRET_QUERY_KEYS) + r")=)[^&\s'\"]+"
)


def scrub_secrets(text: str, *, limit: int = 300) -> str:
    """Redact provider credentials from a string bound for telemetry or a log.

    Why this is a security control and not tidiness: the only thing guarding
    GET /agents/health is `require_api_key`, and the bundled frontend bakes
    `VITE_API_KEY` into its shipped JS bundle (frontend/src/services/api.js).
    Anyone who loads the page can read that key from devtools and call the
    endpoint. So a Gemini 401 — the single most common failure, and the one a
    rotated or over-quota key produces — handed the live GEMINI_API_KEY to
    every visitor, because `raise_for_status()` puts `?key=<secret>` in the
    message and the monitor stored the message verbatim.

    Applied in `AgentMonitor.record` — the one place every agent funnels
    through — so a new agent cannot reintroduce the leak by forgetting.
    """
    return _SECRET_PARAM_RE.sub(r"\1***", text)[:limit]


@dataclass
class AgentStats:
    calls: int = 0
    successes: int = 0
    failures: int = 0
    total_latency_ms: float = 0.0
    last_error: str | None = None
    last_error_at: float | None = None
    last_success_at: float | None = None

    @property
    def avg_latency_ms(self) -> float:
        return round(self.total_latency_ms / self.calls, 1) if self.calls else 0.0

    @property
    def failure_rate(self) -> float:
        return round(self.failures / self.calls, 3) if self.calls else 0.0

    @property
    def status(self) -> str:
        """down: every call so far has failed. degraded: failing >20% of the
        time. healthy: everything else, including 'no calls yet'."""
        if self.calls == 0:
            return "unknown"
        if self.failures == self.calls:
            return "down"
        if self.failure_rate > 0.2:
            return "degraded"
        return "healthy"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "calls": self.calls,
            "successes": self.successes,
            "failures": self.failures,
            "failure_rate": self.failure_rate,
            "avg_latency_ms": self.avg_latency_ms,
            "last_error": self.last_error,
            "last_error_at": self.last_error_at,
            "last_success_at": self.last_success_at,
        }


class AgentMonitor:
    """Thread-safe registry of AgentStats keyed by agent name (e.g.
    'llm:gemini', 'source:jsearch', 'source:company')."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._agents: dict[str, AgentStats] = {}

    def record(self, agent: str, ok: bool, latency_ms: float, error: str | None = None) -> None:
        with self._lock:
            stats = self._agents.setdefault(agent, AgentStats())
            stats.calls += 1
            stats.total_latency_ms += latency_ms
            now = time.time()
            if ok:
                stats.successes += 1
                stats.last_success_at = now
            else:
                stats.failures += 1
                # Scrub at the sink, not at each call site. Every agent reports
                # here, and snapshot() is a public endpoint — so this is the one
                # place that can guarantee a new provider never leaks its key by
                # forgetting to redact. See scrub_secrets.
                stats.last_error = scrub_secrets(error) if error else None
                stats.last_error_at = now

    @contextmanager
    def track(self, agent: str):
        """Usage: with monitor.track('source:jsearch'): await adapter.search(...)
        Records success/failure + latency automatically, re-raises any exception."""
        start = time.monotonic()
        try:
            yield
        except Exception as exc:
            self.record(agent, ok=False, latency_ms=(time.monotonic() - start) * 1000, error=str(exc))
            raise
        else:
            self.record(agent, ok=True, latency_ms=(time.monotonic() - start) * 1000)

    def snapshot(self) -> dict:
        with self._lock:
            return {name: stats.to_dict() for name, stats in sorted(self._agents.items())}


# Process-wide singleton — every service imports this instead of building its own.
agent_monitor = AgentMonitor()
