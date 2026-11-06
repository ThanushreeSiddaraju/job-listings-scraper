"""Public job-board APIs of the three most common applicant tracking systems. No API key needed.

- Greenhouse: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs
- Lever:      https://api.lever.co/v0/postings/{slug}?mode=json
- Ashby:      https://api.ashbyhq.com/posting-api/job-board/{slug}
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from .. import attributes
from ..http import get_json
from ..matching import location_matches, title_matches
from ..models import Job, SearchParams

# Shared with the GitHub Pages site (docs/), which reads the same list.
WATCHLIST_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "docs", "companies.json")


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):  # Lever uses epoch milliseconds
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
    return str(value)[:10]


def fetch_greenhouse(slug: str, company: str | None = None) -> list[Job]:
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    jobs = []
    for j in data.get("jobs", []):
        title, location = j["title"].strip(), (j.get("location") or {}).get("name")
        mode = attributes.work_mode(None, None, location, title)
        jobs.append(Job(
            title=title,
            company=j.get("company_name") or company or slug,
            location=location,
            url=j["absolute_url"],
            source="Greenhouse",
            date_posted=_iso(j.get("first_published") or j.get("updated_at")),
            remote=mode == "Remote" or None,
            experience=attributes.experience_level(title),
            job_type=attributes.job_type(None, title),
            work_mode=mode,
        ))
    return jobs


def fetch_lever(slug: str, company: str | None = None) -> list[Job]:
    data = get_json(f"https://api.lever.co/v0/postings/{slug}", {"mode": "json"})
    jobs = []
    for j in data:
        title, categories = j["text"].strip(), j.get("categories") or {}
        workplace = j.get("workplaceType") if j.get("workplaceType") != "unspecified" else None
        mode = attributes.work_mode(workplace, None, categories.get("location"), title)
        pay = j.get("salaryRange") or {}
        jobs.append(Job(
            title=title,
            company=company or slug.title(),
            location=categories.get("location"),
            url=j["hostedUrl"],
            source="Lever",
            date_posted=_iso(j.get("createdAt")),
            remote=mode == "Remote" or None,
            experience=attributes.experience_level(title),
            job_type=attributes.job_type(categories.get("commitment"), title),
            work_mode=mode,
            salary=attributes.salary(pay.get("min"), pay.get("max"), pay.get("currency"), pay.get("interval")),
            department=categories.get("department") or categories.get("team"),
        ))
    return jobs


def fetch_ashby(slug: str, company: str | None = None) -> list[Job]:
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", {"includeCompensation": "true"})
    jobs = []
    for j in data.get("jobs", []):
        if not j.get("isListed", True):
            continue
        locations = [j.get("location")] + [s.get("location") for s in j.get("secondaryLocations") or []]
        location = "; ".join(loc for loc in locations if loc) or None
        title = j["title"].strip()
        # isRemote is also true for hybrid roles, so workplaceType decides when it's given.
        mode = attributes.work_mode(j.get("workplaceType"), j.get("isRemote"), location, title)
        pay = next((c for c in (j.get("compensation") or {}).get("summaryComponents") or []
                    if c.get("compensationType") == "Salary"), {})
        jobs.append(Job(
            title=title,
            company=company or slug.title(),
            location=location,
            url=j["jobUrl"],
            source="Ashby",
            date_posted=_iso(j.get("publishedAt")),
            remote=mode == "Remote" or None,
            experience=attributes.experience_level(title),
            job_type=attributes.job_type(j.get("employmentType"), title),
            work_mode=mode,
            salary=attributes.salary(pay.get("minValue"), pay.get("maxValue"), pay.get("currencyCode"), pay.get("interval")),
            department=j.get("department") or j.get("team"),
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
