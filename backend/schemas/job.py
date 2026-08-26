"""Pydantic v2 schemas. Unknown data stays 'unknown' — never invented (spec 2.3)."""

import datetime as dt
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


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
    query: str = ""
    location: Optional[str] = None
    employment_type: Optional[str] = None
    experience_level: Optional[str] = None  # intern/fresher/no_experience/entry/mid/senior
    remote_only: bool = False
    min_salary: Optional[float] = None
    posted_within_days: Optional[int] = None
    sources: Optional[list[str]] = None  # restrict results to these job sources (e.g. ["jsearch","company"])
    llm_provider: Optional[str] = None  # "auto" | "llama" | "gemini" | "openrouter" | "nvidia" | "none"
    model_name: Optional[str] = None


class MatchRequest(BaseModel):
    query: str
    candidate_skills: list[str] = Field(default_factory=list)
    location: Optional[str] = None
    employment_type: Optional[str] = None
    experience_level: Optional[str] = None
    remote_only: bool = False
    min_salary: Optional[float] = None
    posted_within_days: Optional[int] = None
    top_k: int = 10
    llm_provider: Optional[str] = None  # "auto" | "llama" | "gemini" | "openrouter" | "nvidia" | "none"
    model_name: Optional[str] = None


class MatchedJobOut(BaseModel):
    job: JobOut
    keyword_score: float
    semantic_score: float
    hybrid_score: float
    matched_skills: list[str]
    missing_skills: list[str]
    explanation: str
    confidence: float
