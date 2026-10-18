"""Hard eligibility filters. Pure Python/logic. LLMs must never override these (spec 2.1).

Two implementations of the same rules:

- `passes_hard_filters` / `apply_hard_filters` — the authoritative Python predicate,
  applied to already-loaded `Job` objects.
- `build_sql_filters` — a translation to SQL WHERE clauses so the database does the
  filtering instead of the process. This exists because `/jobs/match` used to load the
  entire jobs table per request (audit A4).

The SQL form is a deliberate *superset* of the Python predicate, never narrower:
`apply_hard_filters` still runs over the SQL result, so the Python rules stay
authoritative. Where the SQL cannot express a Python rule exactly (location aliases,
word-boundary matching) the SQL widens instead of guessing. `tests/test_filters.py`
asserts the two agree.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.sql.elements import ColumnElement

from backend.models import Job
from backend.schemas import SearchRequest

# Location aliases, shared by both filter implementations. Hoisted out of
# passes_hard_filters so the SQL translation cannot drift from the Python one.
_LOCATION_ALIASES: dict[str, list[str]] = {
    "uk": ["united kingdom", "great britain"],
    "usa": ["united states", "united states of america", "us"],
    "us": ["united states", "united states of america", "usa"],
    "uae": ["united arab emirates"],
    "dubai": ["dubai, united arab emirates"],
    # "in" is the country code this system already uses for India
    # (job_api_adapter.country_map maps "india" -> "in", and
    # Settings.adzuna_country defaults to "in"). It is not a substring of
    # "hyderabad, telangana, india", so a bare substring search cannot find it.
    "in": ["india", "bharat"],
    "india": ["india", "bharat"],
}


def _like(column, needle: str) -> ColumnElement[bool]:
    """Case-insensitive substring match with LIKE wildcards escaped.

    `query` and `location` are user input: a literal `%` must not become a
    wildcard and silently widen the result set.
    """
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape="\\")


def build_sql_filters(
    req: SearchRequest, now: datetime | None = None
) -> list[ColumnElement[bool]]:
    """Translate the hard filters into SQL WHERE clauses.

    Guaranteed to return a superset of what `passes_hard_filters` accepts, so it is
    safe to use as a prefilter with the Python predicate still applied afterwards.
    """
    clauses: list[ColumnElement[bool]] = [Job.is_active.is_(True)]

    if req.employment_type and req.employment_type != "unknown":
        clauses.append(Job.employment_type == req.employment_type)

    if req.experience_level and req.experience_level != "unknown":
        clauses.append(Job.experience_level == req.experience_level)

    if req.remote_only:
        clauses.append(Job.remote == "remote")

    if req.sources:
        clauses.append(Job.source.in_(req.sources))

    if req.query:
        # Same rule as Python: a substring of the title or the description.
        clauses.append(
            or_(_like(Job.title, req.query), _like(Job.description, req.query))
        )

    if req.location:
        needle = req.location.lower().strip()
        if needle:
            # Aliases matter: "uk" must find "London, United Kingdom", and a plain
            # substring would miss it. Expand to every alias so the SQL is at least
            # as permissive as the Python matcher.
            needles = [needle, *_LOCATION_ALIASES.get(needle, [])]
            location_hits = or_(
                *(
                    or_(_like(Job.location_normalized, n), _like(Job.location_raw, n))
                    for n in needles
                )
            )
            # Remote jobs match any location search, mirroring the Python escape.
            clauses.append(or_(Job.remote == "remote", location_hits))

    if req.min_salary is not None:
        # Unknown salary is not excluded (spec 2.3): unknown != ineligible.
        clauses.append(or_(Job.salary_max.is_(None), Job.salary_max >= req.min_salary))

    if req.posted_within_days is not None:
        reference = now or datetime.now(timezone.utc)
        # posted_at is a naive DATETIME holding UTC, so compare against naive UTC.
        cutoff = reference.replace(tzinfo=None) - timedelta(days=req.posted_within_days)
        # Unknown posted_at is not excluded, same rule.
        clauses.append(or_(Job.posted_at.is_(None), Job.posted_at >= cutoff))

    return clauses


def passes_hard_filters(job: Job, req: SearchRequest) -> bool:
    if not job.is_active:
        return False

    if req.employment_type and req.employment_type != "unknown":
        if job.employment_type != req.employment_type:
            return False

    if req.experience_level and req.experience_level != "unknown":
        if job.experience_level != req.experience_level:
            return False

    if req.remote_only and job.remote != "remote":
        return False

    if req.sources and job.source not in req.sources:
        # Previously only the SQL layer applied this, so the Python and SQL
        # implementations disagreed and the SQL prefilter could exclude a row
        # the authoritative predicate would have kept.
        return False

    if req.location:
        loc = req.location.lower().strip()
        job_loc = job.location_normalized.lower().strip()

        # Handle remote jobs - if job is remote, it matches any location search
        # (remote jobs can be done from anywhere)
        if job.remote == "remote":
            return True  # Remote jobs match any location search

        # If location is empty, no filtering
        if not loc:
            return True

        # Enhanced location matching with better handling of abbreviations and partial matches
        location_match = False

        location_aliases = _LOCATION_ALIASES

        # Check direct matches including aliases
        def check_location_match(search_term, target_location, _seen=None):
            """Check if search_term matches target_location with various strategies"""
            # Aliases are not a tree: 'us' -> 'usa' -> 'us'. Without a visited
            # set that recurses until the interpreter gives up, so a search for
            # "us" or "usa" raised RecursionError and the endpoint 500'd.
            _seen = set() if _seen is None else _seen
            if search_term in _seen:
                return False
            _seen.add(search_term)

            # Direct equality
            if search_term == target_location:
                return True

            # Check if search_term is a distinct term in target_location
            # Patterns: " term ", " term,", ", term", "term ", " term", or exact match with boundaries
            if (f' {search_term} ' in f' {target_location} ' or
                f'{search_term},' in target_location or
                f',{search_term}' in target_location or
                target_location.startswith(f'{search_term} ') or
                target_location.endswith(f' {search_term}') or
                target_location == search_term or
                # Handle start/end with comma
                (target_location.startswith(search_term + ',') and len(target_location) > len(search_term) + 1) or
                (target_location.endswith(',' + search_term) and len(target_location) > len(search_term) + 1)):
                return True

            # Check aliases
            if search_term in location_aliases:
                for alias in location_aliases[search_term]:
                    if check_location_match(alias, target_location, _seen):
                        return True
            # Also check reverse aliases (if target is an alias of search)
            for key, aliases in location_aliases.items():
                if key == search_term and target_location in aliases:
                    return True
                if target_location == key and search_term in aliases:
                    return True

            return False

        # Primary check: direct match with aliases
        if check_location_match(loc, job_loc):
            location_match = True
        else:
            # Secondary check: comma-separated parts matching
            # e.g., user searches "India" and job location is "Hyderabad, Telangana, India"
            job_location_parts = [part.strip().lower() for part in job_loc.split(',') if part.strip()]
            user_location_parts = [part.strip().lower() for part in loc.split(',') if part.strip()]

            # Check if any user location part matches any job location part
            for user_part in user_location_parts:
                if not user_part or len(user_part) < 2:  # Skip empty or single character terms
                    continue
                for job_part in job_location_parts:
                    if not job_part or len(job_part) < 2:
                        continue
                    if check_location_match(user_part, job_part):
                        location_match = True
                        break
                if location_match:
                    break

            # Tertiary check: substring matching for longer terms (to catch cases like "London" in "Central London")
            # But only for terms longer than 3 characters to avoid false positives like "lon" matching "London"
            if not location_match and len(loc) >= 3:
                # Only do substring search if the search term is substantial enough
                # And only if it's not just a single common word that could match too broadly
                common_words = {'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'any', 'can', 'had', 'her', 'was', 'one', 'our', 'out', 'day', 'get', 'has', 'him', 'his', 'how', 'man', 'new', 'now', 'old', 'see', 'two', 'way', 'who', 'boy', 'did', 'its', 'let', 'put', 'say', 'she', 'too', 'use'}
                if loc not in common_words and loc in job_loc:
                    # Additional check: make sure it's not matching inside a longer word unnaturally
                    # Find positions where it occurs and check boundaries
                    pos = job_loc.find(loc)
                    while pos != -1:
                        # Check if it's a word boundary (start/end of string or non-letter chars)
                        before_ok = (pos == 0) or not job_loc[pos-1].isalpha()
                        after_ok = (pos + len(loc) == len(job_loc)) or not job_loc[pos+len(loc)].isalpha()
                        if before_ok and after_ok:
                            location_match = True
                            break
                        # Look for next occurrence
                        pos = job_loc.find(loc, pos + 1)

        if not location_match:
            return False

    # Check if the query string matches in title or description
    if req.query:
        query_low = req.query.lower()
        if query_low not in job.title.lower() and query_low not in job.description.lower():
            return False

    if req.min_salary is not None:
        # unknown salary is not excluded — unknown != ineligible (spec 2.3), just unscored on this axis
        if job.salary_max is not None and job.salary_max < req.min_salary:
            return False

    if req.posted_within_days is not None and job.posted_at is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=req.posted_within_days)
        posted = job.posted_at if job.posted_at.tzinfo else job.posted_at.replace(tzinfo=timezone.utc)
        if posted < cutoff:
            return False
        # posted_at unknown (None) is not excluded — same unknown != ineligible rule

    return True


def apply_hard_filters(jobs: list[Job], req: SearchRequest) -> list[Job]:
    return [j for j in jobs if passes_hard_filters(j, req)]
