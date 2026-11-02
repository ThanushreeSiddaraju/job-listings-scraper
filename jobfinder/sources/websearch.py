"""Web search through a self-hosted SearXNG instance (https://github.com/searxng/searxng).

SearXNG sends one query to many engines (Google, Bing, DuckDuckGo, Brave, ...) and merges the results.
Results on Greenhouse/Lever/Ashby are turned into company boards and read through their APIs;
other job pages are opened and kept only if they contain JobPosting data.
Enabled when SEARXNG_URL is set (e.g. http://localhost:8888).
"""

import os
from urllib.parse import urlsplit

from ..http import get_json, get_page
from ..jobpage import extract_jobs
from ..matching import location_matches, title_matches
from ..models import Job, SearchParams
from .ats import search_boards

ATS_HOSTS = {
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.ashbyhq.com": "ashby",
}
MAX_PAGES = 10  # job pages opened per search, to stay polite and fast


def base_url() -> str | None:
    return os.environ.get("SEARXNG_URL", "").rstrip("/") or None


def board_from_url(url: str) -> dict | None:
    parts = urlsplit(url)
    ats = ATS_HOSTS.get(parts.netloc.lower())
    slug = parts.path.strip("/").split("/")[0] if parts.path.strip("/") else ""
    if not ats or not slug or slug in {"embed", "v1"}:
        return None
    return {"ats": ats, "slug": slug.lower()}


def _searx(query: str) -> list[dict]:
    data = get_json(f"{base_url()}/search", {"q": query, "format": "json", "time_range": "month"})
    return data.get("results", [])


def search(params: SearchParams, known_boards: list[dict]) -> tuple[list[Job], list[str]]:
    if not base_url():
        raise RuntimeError("SearXNG is not configured (set SEARXNG_URL)")

    where = f" {params.location}" if params.location else ""
    ats_query = f'"{params.query}"{where} (site:boards.greenhouse.io OR site:jobs.lever.co OR site:jobs.ashbyhq.com)'
    general_query = f'"{params.query}" job{where}'
    results = _searx(ats_query) + _searx(general_query)

    known = {(b["ats"], b["slug"]) for b in known_boards}
    boards, pages = {}, []
    for result in results:
        url = result.get("url", "")
        board = board_from_url(url)
        if board:
            if (board["ats"], board["slug"]) not in known:
                boards[(board["ats"], board["slug"])] = board
        elif url and len(pages) < MAX_PAGES and title_matches(result.get("title", ""), params.query):
            pages.append(url)

    jobs, errors = search_boards(list(boards.values()), params)
    for url in pages:
        try:
            html = get_page(url)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{urlsplit(url).netloc}: {exc}")
            continue
        if html is None:
            continue  # robots.txt says no
        for job in extract_jobs(html, url, source="Web search"):
            if title_matches(job.title, params.query) and location_matches(job.location, params.location, job.remote):
                jobs.append(job)
    return jobs, errors
