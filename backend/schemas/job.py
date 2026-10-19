"""Pydantic v2 schemas. Unknown data stays 'unknown' — never invented (spec 2.3)."""

import datetime as dt
from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# Bounds for the two free-form list fields on the request schemas. A list with
# no bound is an unbounded body: it reaches `Job.source.in_(req.sources)`, so
# every element becomes a bind parameter in the WHERE clause.
SourceName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]

# A Gemini model id. Constrained because it is interpolated into a request path
# server-side; the same pattern is enforced in model_router as the trust
# boundary. Kept permissive on the inside so fine-tune ids (tunedModels/...)
# are not rejected by a client that has them but this process does not.
GeminiModelName = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$")
]

# Job sources are a short closed set configured per deployment (JOB_SOURCES).
# Ten is above every realistic configuration; the cap stops a caller from
# sending thousands of strings to inflate the SQL and the checkpoint hash.
_MAX_SOURCES = 10


def _coerce_empty_to_none(data: dict) -> dict:
    """Frontend may send empty strings "" for unset numeric filters. Treat them
    as absent (None) instead of failing validation with a 422."""
    if isinstance(data, dict):
        for key in ("min_salary", "max_salary", "posted_within_days"):
            if data.get(key) == "":
                data[key] = None
    return data


class RawJobPosting(BaseModel):
    """Shape returned by any job-source adapter, before normalization."""

    source: str
    source_id: str
    title: str
    company: str
    location_raw: str = ""
    employment_type_raw: str = ""
    description: str = ""
    apply_url: str
    posted_at_raw: Optional[str] = None
    salary_raw: Optional[str] = None


class NormalizedJob(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: str
    source_id: str
    title: str
    company: str
    location_raw: str = ""
    location_normalized: str = "unknown"
    employment_type: str = "unknown"
    experience_level: str = "unknown"  # intern/fresher/no_experience/entry/mid/senior/unknown
    remote: str = "unknown"  # remote/onsite/hybrid/unknown
    description: str = ""
    skills_required: list[str] = Field(default_factory=list)
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: str = "unknown"
    apply_url: str
    posted_at: Optional[dt.datetime] = None
    url_hash: str
    content_hash: str


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    company: str
    location_normalized: str
    employment_type: str
    experience_level: str
    remote: str
    skills_required: list[str]
    salary_min: Optional[float]
    salary_max: Optional[float]
    salary_currency: str
    apply_url: str
    posted_at: Optional[dt.datetime]
    source: str
    is_active: bool
    verified: bool
    verified_at: Optional[dt.datetime]


class SearchRequest(BaseModel):
    # Free-text bounds. These are far above realistic search input; their job is
    # to stop an unbounded body from reaching the log line and the checkpoint
    # thread hash, and from forging log entries via embedded newlines.
    query: str = Field(default="", max_length=200)
    location: Optional[str] = Field(default=None, max_length=100)
    employment_type: Optional[str] = Field(default=None, max_length=50)
    experience_level: Optional[str] = Field(default=None, max_length=50)
    remote_only: bool = False
    min_salary: Optional[float] = None
    max_salary: Optional[float] = Field(default=None, ge=0)
    posted_within_days: Optional[int] = Field(default=None, ge=1, le=3650)
    # Bounded: each entry becomes a bind parameter in `Job.source.in_(...)`.
    # Was `Optional[list[str]]` with no bound at all.
    sources: Optional[list[SourceName]] = Field(default=None, max_length=_MAX_SOURCES)
    llm_provider: Optional[str] = Field(default=None, max_length=16)
    model_name: Optional[GeminiModelName] = None

    # Pagination. Capped so one request can never pull an unbounded result set.
    # There is no total count in the response — the list is simply this page.
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _coerce_empty_filters(cls, data):
        return _coerce_empty_to_none(data)


class MatchRequest(BaseModel):
    query: str = Field(max_length=200)
    candidate_skills: list[str] = Field(default_factory=list, max_length=100)
    location: Optional[str] = Field(default=None, max_length=100)
    employment_type: Optional[str] = Field(default=None, max_length=50)
    experience_level: Optional[str] = Field(default=None, max_length=50)  # intern/fresher/no_experience/entry/mid/senior
    remote_only: bool = False
    min_salary: Optional[float] = None
    max_salary: Optional[float] = Field(default=None, ge=0)
    posted_within_days: Optional[int] = Field(default=None, ge=1, le=3650)
    # top_k is the page size. It was previously unbounded, which made it both a
    # `LIMIT -n` on negative input and an unbounded row pull on large input.
    top_k: int = Field(default=10, ge=1, le=100)
    # Skip this many eligible candidates before ranking. Lets a caller walk past
    # the first page of an unranked-by-score ordering without re-sending filters.
    offset: int = Field(default=0, ge=0)
    llm_provider: Optional[str] = Field(default=None, max_length=16)
    model_name: Optional[GeminiModelName] = None
    # Bounded, same as SearchRequest. This field did not exist while the graph
    # already read `req.sources` to narrow the adapter fan-out — so every
    # /jobs/match request died with AttributeError before reaching a single job.
    # Restored so the intent in graph.py is expressible, and so match can be
    # narrowed by source the same way search can.
    sources: Optional[list[SourceName]] = Field(default=None, max_length=_MAX_SOURCES)

    @model_validator(mode="before")
    @classmethod
    def _coerce_empty_filters(cls, data):
        return _coerce_empty_to_none(data)


class MatchedJobOut(BaseModel):
    job: JobOut
    keyword_score: float
    semantic_score: float
    hybrid_score: float
    evidence: str = ""  # skills quoted verbatim from the JD, for auditing
    matched_skills: list[str]
    missing_skills: list[str]
    explanation: str
    confidence: float
