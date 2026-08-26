"""Hard eligibility filters. Pure Python/logic. LLMs must never override these (spec 2.1)."""

from datetime import datetime, timedelta, timezone

from backend.models import Job
from backend.schemas import SearchRequest


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

        # Common location aliases mapping
        location_aliases = {
            'uk': ['united kingdom', 'great britain'],
            'usa': ['united states', 'united states of america', 'us'],
            'us': ['united states', 'united states of america', 'usa'],
            'uae': ['united arab emirates'],
            'dubai': ['dubai, united arab emirates'],
        }

        # Check direct matches including aliases
        def check_location_match(search_term, target_location):
            """Check if search_term matches target_location with various strategies"""
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
                    if check_location_match(alias, target_location):
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
