"""Command‑line interface for the CompanyScraper.

Usage:
    python -m backend.cli <company_name> [--location <loc>] [--url <custom_url>] [--salary-min <min_sal>] [--salary-max <max_sal>] [--max-concurrency <n>] [--jitter-max <f>]

The CLI is a thin wrapper around ``scrape_company_jobs`` that returns JSON
output suitable for piping into tools or for manual debugging.
"""
import argparse
import asyncio
import json
import re
from typing import List, Optional

from backend.services.sourcing.company_scraper import CompanyScraper


def _extract_salary_number(sal_str: Optional[str]) -> Optional[float]:
    """Extract the first numeric value from a salary string (e.g. '₹8,00,000-₹12,00,000' → 800000)."""
    if not sal_str:
        return None
    # Find the first number (including commas) in the text
    match = re.search(r'[\d,.]+', sal_str)
    if match:
        num_str = match.group(0).replace(',', '')
        try:
            return float(num_str)
        except ValueError:
            return None
    return None


async def _run():
    parser = argparse.ArgumentParser(description="Scrape jobs from a company career page")
    parser.add_argument("company_name", help="Name of the company to scrape")
    parser.add_argument(
        "--location",
        help="Optional location filter (e.g. 'United States' or 'Remote')",
    )
    parser.add_argument(
        "--url",
        help="Custom career‑page URL (overrides automatic URL construction)",
    )
    parser.add_argument(
        "--salary-min",
        type=float,
        help="Minimum monthly salary (numeric) to filter jobs",
    )
    parser.add_argument(
        "--salary-max",
        type=float,
        help="Maximum monthly salary (numeric) to filter jobs",
    )
    parser.add_argument("--max-concurrency", type=int, default=None, help="Override per‑domain concurrency limit (default: settings value)")
    parser.add_argument("--jitter-max", type=float, default=None, help="Override max jitter added to retry delays (default: settings value)")
    args = parser.parse_args()

    # Build scraper with optional overrides
    scraper = CompanyScraper(
        max_concurrency=args.max_concurrency,
        jitter_max=args.jitter_max,
    )

    # Fetch all postings from the scraper
    postings = await scraper.scrape_company_jobs(
        company_name=args.company_name,
        location=args.location,
        custom_url=args.url,
    )

    # -------------------------------------------------------------------------
    # Filtering / Suggestions
    # -------------------------------------------------------------------------
    filtered_postings: List[dict] = []
    if args.salary_min is not None or args.salary_max is not None:
        # Simple numeric salary filtering (salary strings are parsed roughly)
        def within_range(p: dict) -> bool:
            sal_num = _extract_salary_number(p.salary_raw)
            if sal_num is None:
                return False
            if args.salary_min is not None and sal_num < args.salary_min:
                return False
            if args.salary_max is not None and sal_num > args.salary_max:
                return False
            return True

        filtered_postings = [p.model_dump() for p in postings if within_range(p)]
    else:
        # No salary filter – use all postings
        filtered_postings = [p.model_dump() for p in postings]

    # If no explicit salary filter, provide “Suggested Jobs” section
    suggested_postings: List[dict] = []
    if not args.salary_min and not args.salary_max and filtered_postings:
        # Sort by description length (longer descriptions often more detailed) and take top 5
        suggested_postings = sorted(filtered_postings, key=lambda p: len(p.get("description", "")), reverse=True)[:5]

    # -------------------------------------------------------------------------
    # Output
    # -------------------------------------------------------------------------
    print(json.dumps(filtered_postings, indent=2), end="\n\n")
    if suggested_postings:
        print(json.dumps(suggested_postings, indent=2), end="\n\n")
        print("Suggested Jobs (based on description depth):")
        for job in suggested_postings:
            print(f"- {job.get('title')} at {job.get('company')} (Location: {job.get('location_raw')})")


if __name__ == "__main__":
    asyncio.run(_run())