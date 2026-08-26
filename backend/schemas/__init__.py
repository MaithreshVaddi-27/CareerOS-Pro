"""Re-export so `from backend.schemas import X` keeps working after the
package split (job.py holds the actual pydantic schemas)."""
from backend.schemas.job import (
    JobOut,
    MatchedJobOut,
    MatchRequest,
    NormalizedJob,
    RawJobPosting,
    SearchRequest,
)

__all__ = [
    "RawJobPosting",
    "NormalizedJob",
    "JobOut",
    "SearchRequest",
    "MatchRequest",
    "MatchedJobOut",
]
