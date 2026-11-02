"""Public job-board APIs of the three most common applicant tracking systems. No API key needed.

- Greenhouse: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs
- Lever:      https://api.lever.co/v0/postings/{slug}?mode=json
- Ashby:      https://api.ashbyhq.com/posting-api/job-board/{slug}
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from ..http import get_json
from ..matching import location_matches, title_matches
from ..models import Job, SearchParams

WATCHLIST_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "companies.json")


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):  # Lever uses epoch milliseconds
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
    return str(value)[:10]


def fetch_greenhouse(slug: str, company: str | None = None) -> list[Job]:
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    return [
        Job(
            title=j["title"].strip(),
            company=j.get("company_name") or company or slug,
            location=(j.get("location") or {}).get("name"),
            url=j["absolute_url"],
            source="Greenhouse",
            date_posted=_iso(j.get("first_published") or j.get("updated_at")),
            remote="remote" in ((j.get("location") or {}).get("name") or "").lower() or None,
        )
        for j in data.get("jobs", [])
    ]


def fetch_lever(slug: str, company: str | None = None) -> list[Job]:
    data = get_json(f"https://api.lever.co/v0/postings/{slug}", {"mode": "json"})
    return [
        Job(
            title=j["text"].strip(),
            company=company or slug.title(),
            location=(j.get("categories") or {}).get("location"),
            url=j["hostedUrl"],
            source="Lever",
            date_posted=_iso(j.get("createdAt")),
            remote=(j.get("workplaceType") == "remote") or None,
        )
        for j in data
    ]


def fetch_ashby(slug: str, company: str | None = None) -> list[Job]:
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    jobs = []
    for j in data.get("jobs", []):
        if not j.get("isListed", True):
            continue
        locations = [j.get("location")] + [s.get("location") for s in j.get("secondaryLocations") or []]
        jobs.append(Job(
            title=j["title"].strip(),
            company=company or slug.title(),
            location="; ".join(loc for loc in locations if loc) or None,
            url=j["jobUrl"],
            source="Ashby",
            date_posted=_iso(j.get("publishedAt")),
            remote=bool(j.get("isRemote")) or None,
        ))
    return jobs


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby}


def load_watchlist(path: str = WATCHLIST_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def filter_jobs(jobs: list[Job], params: SearchParams) -> list[Job]:
    return [
        job for job in jobs
        if title_matches(job.title, params.query)
        and location_matches(job.location, params.location, job.remote)
        and (not params.remote_only or job.remote)
    ]


def search_boards(boards: list[dict], params: SearchParams) -> tuple[list[Job], list[str]]:
    """Fetch each company's board and keep matching jobs. Returns (jobs, errors); one bad board doesn't stop the rest."""
    def fetch(board):
        return FETCHERS[board["ats"]](board["slug"], board.get("name"))

    jobs, errors = [], []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch, board): board for board in boards}
        for future in as_completed(futures):
            board = futures[future]
            try:
                jobs.extend(filter_jobs(future.result(), params))
            except Exception as exc:  # noqa: BLE001 - report and move on to the next company
                errors.append(f"{board.get('name') or board['slug']}: {exc}")
    return jobs, errors
