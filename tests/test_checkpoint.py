"""Checkpointer resolution and visibility (audit A3).

The original defect: a silent fall back to MemorySaver looked identical to a
healthy durable setup from the outside. /health now reports it, and
CHECKPOINT_STRICT can refuse the downgrade entirely.
"""

import pytest
from langgraph.checkpoint.memory import MemorySaver

from backend import graph as g
from backend.graph import checkpoint_status, get_checkpointer

# Instant connection-refused: port 1 is never listening.
DEAD_DSN = "mysql+aiomysql://u:p@127.0.0.1:1/nodb"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    g._checkpointer_singleton.clear()
    g._checkpoint_status.update(kind="unknown", detail="checkpointer not initialised yet")
    yield
    g._checkpointer_singleton.clear()


async def test_development_uses_memory_and_says_so(monkeypatch):
    monkeypatch.setattr(g.settings, "app_env", "development")
    saver = await get_checkpointer()
    assert isinstance(saver, MemorySaver)
    assert checkpoint_status()["kind"] == "memory"
    assert "development" in checkpoint_status()["detail"]


async def test_status_is_a_copy(monkeypatch):
    """Callers must not be able to corrupt the recorded status."""
    monkeypatch.setattr(g.settings, "app_env", "development")
    await get_checkpointer()
    snapshot = checkpoint_status()
    snapshot["kind"] = "lies"
    assert checkpoint_status()["kind"] == "memory"


async def test_mysql_failure_degrades_but_is_flagged(monkeypatch):
    monkeypatch.setattr(g.settings, "app_env", "production")
    monkeypatch.setattr(g.settings, "checkpoint_strict", False)
    monkeypatch.setattr(g, "_mysql_dsn", lambda: DEAD_DSN)

    saver = await get_checkpointer()

    assert isinstance(saver, MemorySaver)
    status = checkpoint_status()
    assert status["kind"] == "memory"
    # This is the whole point of A3: the downgrade is not silent.
    assert status["detail"].startswith("DEGRADED")


async def test_strict_mode_refuses_to_degrade(monkeypatch):
    """In production, losing human-review state across a restart is worse than
    not starting."""
    monkeypatch.setattr(g.settings, "app_env", "production")
    monkeypatch.setattr(g.settings, "checkpoint_strict", True)
    monkeypatch.setattr(g, "_mysql_dsn", lambda: DEAD_DSN)

    with pytest.raises(Exception):
        await get_checkpointer()

    assert checkpoint_status()["kind"] == "unavailable"


async def test_failed_strict_setup_is_not_cached(monkeypatch):
    """Otherwise a transient MySQL outage would poison the singleton and every
    later request would re-raise even after MySQL recovered."""
    monkeypatch.setattr(g.settings, "app_env", "production")
    monkeypatch.setattr(g.settings, "checkpoint_strict", True)
    monkeypatch.setattr(g, "_mysql_dsn", lambda: DEAD_DSN)

    for _ in range(2):
        with pytest.raises(Exception):
            await get_checkpointer()

    assert "instance" not in g._checkpointer_singleton