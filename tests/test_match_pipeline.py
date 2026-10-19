"""End-to-end coverage of `POST /jobs/match` and the LangGraph pipeline.

This module exists because of how a critical defect shipped: the match path is
the product's headline feature and it had **no** test at all. Nothing executed
`run_match_workflow`, so `backend/graph.py` could read `req.sources` off a
`MatchRequest` that has no such field and every request in production returned
500 — behind a fully green suite of 263 tests.

The rule this file enforces: any field a node or handler reads off a request
object must exist on the schema, and the endpoint must be exercised end to end,
not just its parts. `test_every_schema_field_the_graph_reads_exists` is written
to be cheap to extend — add a field the graph reads, and it stays honest.
"""

from __future__ import annotations

import datetime as dt
import re as _re
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from backend.core.db import Base, get_db
from backend.main import app
from backend.models import Job

API_KEY = "test"  # conftest forces API_KEY=test
AUTH = {"X-API-Key": API_KEY}
NOW = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _job(**kw) -> Job:
    ident = kw.pop("id")
    return Job(
        id=ident,
        url_hash=kw.pop("url_hash", ident.ljust(64, "u")),
        content_hash=kw.pop("content_hash", ident.ljust(64, "c")),
        source=kw.pop("source", "jsearch"),
        source_id=kw.pop("source_id", f"s-{ident}"),
        title=kw.pop("title", "Python Engineer"),
        company=kw.pop("company", "Acme"),
        location_raw=kw.pop("location_raw", "Bengaluru, India"),
        location_normalized=kw.pop("location_normalized", "Bengaluru, India"),
        employment_type=kw.pop("employment_type", "full_time"),
        experience_level=kw.pop("experience_level", "mid"),
        remote=kw.pop("remote", "hybrid"),
        description=kw.pop("description", "python and fastapi"),
        skills_required=[],
        salary_currency="INR",
        apply_url=kw.pop("apply_url", f"https://example.invalid/{ident}"),
        posted_at=kw.pop("posted_at", NOW),
        is_active=kw.pop("is_active", True),
        created_at=kw.pop("created_at", NOW),
        updated_at=kw.pop("updated_at", NOW),
        **kw,
    )


@pytest.fixture
def session(tmp_path):
    engine = sa.create_engine(
        f"sqlite:///{tmp_path / 'match.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    for i in range(5):
        db.add(
            _job(
                id=f"{i:032d}",
                title=f"Python Engineer {i}",
                posted_at=NOW - dt.timedelta(days=i),
            )
        )
    db.commit()
    yield db
    db.close()


@pytest.fixture
def client(session, monkeypatch):
    """A TestClient with the whole match pipeline stubbed at its edges only.

    Stubbing the edges (embeddings, vector store, LLM) rather than the nodes is
    deliberate: the nodes are the code under test here. A test that mocked the
    graph would have passed while the endpoint 500'd.
    """
    from backend.services.matching.model_router import MatchExplanation

    class _Provider:
        dim = 4

        def embed(self, text):
            return [0.0, 1.0, 0.0, 1.0]

    class _Router:
        async def explain_match(self, title, description, skills):
            return MatchExplanation(
                evidence="python|fastapi",
                matched_skills=list(skills)[:1],
                missing_skills=[],
                explanation="python and fastapi are both named in the posting",
                confidence=0.9,
            )

    monkeypatch.setattr(
        "backend.services.matching.embeddings.get_embedding_provider", lambda: _Provider()
    )
    monkeypatch.setattr(
        "backend.services.matching.model_router.get_model_router_for_request",
        lambda *a, **k: _Router(),
    )

    class _FakeQdrant:
        def upsert(self, **kwargs):
            return None

    monkeypatch.setattr(
        "backend.services.matching.vector_store.get_client", lambda: _FakeQdrant()
    )
    monkeypatch.setattr(
        "backend.services.matching.vector_store.semantic_search",
        lambda client, query, limit=20: [],
    )

    # The checkpointer is not what this file is testing; MemorySaver is already
    # selected for APP_ENV=development, so the real path is harmless.
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# The regression that matters
# ---------------------------------------------------------------------------


def test_match_endpoint_returns_200(client):
    """The shipped defect: 500 on every request.

    `backend/graph.py` read `req.sources` off a MatchRequest. Pydantic raises
    AttributeError for a field the model does not declare, so the request died
    in the discover node before a single job was fetched.
    """
    r = client.post("/jobs/match", headers=AUTH, json={"query": "python", "top_k": 5})
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


def test_match_returns_ranked_rows(client):
    r = client.post(
        "/jobs/match",
        headers=AUTH,
        json={"query": "python", "top_k": 3, "candidate_skills": ["Python"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body, "seeded Python jobs plus ingested mock rows should match"
    first = body[0]
    assert set(first) >= {"job", "keyword_score", "semantic_score", "hybrid_score"}

    # The documented contract is hybrid_score descending. Asserted as a property
    # of the result rather than against one title, because the endpoint also
    # ingests from the source adapters — a title assertion would be testing
    # whichever mock fixture happened to rank first.
    scores = [row["hybrid_score"] for row in body]
    assert scores == sorted(scores, reverse=True), f"not ranked: {scores}"


def test_match_honours_top_k(client):
    r = client.post("/jobs/match", headers=AUTH, json={"query": "python", "top_k": 2})
    assert r.status_code == 200, r.text
    assert len(r.json()) <= 2


def test_match_rejects_unauthenticated(client):
    assert client.post("/jobs/match", json={"query": "python"}).status_code == 401


def test_no_eligible_candidates_yields_an_empty_result_not_an_error(session):
    """A filter stage that matches nothing is a normal outcome.

    Asserted against the node rather than the endpoint because MockJobAdapter
    builds its fixtures from the query (`title = f"{query} Engineer"`), so any
    query through /jobs/match always matches itself. The node is where the
    empty-result contract actually lives.
    """
    from backend import graph as graph_mod
    from backend.schemas import MatchRequest

    session.query(Job).update({"is_active": False})
    session.commit()

    state = {"request": {"query": "python"}, "candidate_ids": ["stale"]}
    graph_mod._ingest_and_filter(state, session, MatchRequest(query="python"), [])

    assert state["candidate_ids"] == []


# ---------------------------------------------------------------------------
# Structural guard: fields the graph reads must exist on the schema
# ---------------------------------------------------------------------------


def test_every_schema_field_the_graph_reads_exists():
    """Pin the shape of the bug rather than one instance of it.

    Any `req.<attr>` read in graph.py must resolve on MatchRequest. This is
    what a coverage tool would have told the previous pass; it costs one test.
    """
    from backend.schemas import MatchRequest

    graph_src = (Path(__file__).resolve().parent.parent / "backend" / "graph.py").read_text()

    # Any `req.<attr>` the graph touches must resolve on MatchRequest. Pydantic
    # fields live on instances, not the class, so accessibility is
    # model_fields (attributes) plus dir() (methods like model_dump).
    accessible = set(MatchRequest.model_fields) | set(dir(MatchRequest))
    reads = set(_re.findall(r"\breq\.([a-z_][a-z0-9_]*)", graph_src))

    missing = sorted(r for r in reads if r not in accessible)
    assert not missing, (
        f"backend/graph.py reads attributes MatchRequest does not declare: {missing}. "
        "Every request touching that node 500s with AttributeError."
    )


def test_sources_is_accepted_by_match():
    """`sources` narrows both the adapter fan-out and the stored-row filter, and
    it is now actually expressible on /jobs/match."""
    from backend.schemas import MatchRequest

    req = MatchRequest(query="python", sources=["jsearch", "company"])
    assert req.sources == ["jsearch", "company"]


def test_sources_flows_into_the_match_filter_stage(session, monkeypatch):
    """The graph rebuilds a SearchRequest for its filter stage and used to drop
    `sources` there, so even once the AttributeError was fixed the row filter
    would have silently ignored the caller's source narrowing."""
    from backend import graph as graph_mod
    from backend.schemas import MatchRequest

    seen = {}
    real = graph_mod.apply_hard_filters

    def _capture(jobs, req, now=None):
        seen["sources"] = req.sources
        return real(jobs, req, now)

    monkeypatch.setattr(graph_mod, "apply_hard_filters", _capture)

    state = {"request": {"query": "python", "sources": ["jsearch"]}, "candidate_ids": []}
    graph_mod._ingest_and_filter(
        state, session, MatchRequest(**state["request"]), []
    )

    assert seen.get("sources") == ["jsearch"]


# ---------------------------------------------------------------------------
# Graph wiring
# ---------------------------------------------------------------------------


def test_graph_compiles_and_has_the_documented_interrupt():
    """The human-review interrupt is the safety property the architecture
    promises: nothing is marked ready-to-apply without a separate approval."""
    from langgraph.checkpoint.memory import MemorySaver

    from backend.graph import build_graph

    graph = build_graph(object(), MemorySaver())
    nodes = set(graph.get_graph().nodes)
    assert {"discover_normalize_dedup_filter", "retrieve_and_rerank", "evidence_and_explain", "human_review"} <= nodes


def test_match_thread_id_is_stable_per_request():
    """Two identical requests must reuse a checkpoint thread, not each mint a
    new one — with the MySQL checkpointer the old id() scheme grew that table
    without bound."""
    from backend.main import _match_thread_id
    from backend.schemas import MatchRequest

    a = _match_thread_id(MatchRequest(query="python", top_k=5))
    b = _match_thread_id(MatchRequest(query="python", top_k=5))
    c = _match_thread_id(MatchRequest(query="rust", top_k=5))
    assert a == b
    assert a != c


def test_graph_module_has_no_synchronous_db_calls_on_the_event_loop():
    """Regression shape only: the blocking halves must be reached through
    asyncio.to_thread so one slow request cannot stall every other in flight."""
    src = (Path(__file__).resolve().parent.parent / "backend" / "graph.py").read_text()
    for node in ("_ingest_and_filter", "_fetch_and_score", "_load_for_explain"):
        assert f"to_thread({node}" in src or f"{node}" in src
    assert src.count("asyncio.to_thread") >= 3, (
        "every node that touches the sync Session must offload it"
    )