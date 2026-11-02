"""Run every selected source in parallel, then merge, de-duplicate and sort the results."""

import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field

from .matching import title_matches
from .models import Job, SearchParams
from .sources import ats, boards, websearch

SOURCE_TIMEOUT = 90  # seconds; a slow source is reported, the rest are still shown

# Shown in the UI. "companies" = the employer watchlist in companies.json.
SOURCES = {
    **{key: label for key, label in boards.SITES.items()},
    "companies": "Company career pages",
    "websearch": "Web search (SearXNG)",
}
DEFAULT_SOURCES = ["indeed", "linkedin", "google", "naukri", "companies", "websearch"]


@dataclass
class SourceResult:
    key: str
    label: str
    count: int = 0
    seconds: float = 0.0
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


def available_sources() -> list[dict]:
    return [
        {
            "key": key,
            "label": label,
            "default": key in DEFAULT_SOURCES,
            "enabled": key != "websearch" or websearch.base_url() is not None,
        }
        for key, label in SOURCES.items()
    ]


def _run(key: str, params: SearchParams, watchlist: list[dict]) -> tuple[list[Job], list[str], float]:
    started = time.perf_counter()
    if key in boards.SITES:
        found, warnings = boards.search(key, params), []
        if params.exact_title:
            found = [job for job in found if title_matches(job.title, params.query)]
    elif key == "companies":
        found, warnings = ats.search_boards(watchlist, params)
    elif key == "websearch":
        found, warnings = websearch.search(params, watchlist)
    else:
        raise ValueError(f"Unknown source: {key}")
    return found, warnings, round(time.perf_counter() - started, 1)


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def dedupe(jobs: list[Job]) -> list[Job]:
    """Drop repeats of the same URL, or the same title at the same company and city, keeping the first seen."""
    seen_urls, seen_keys, unique = set(), set(), []
    for job in jobs:
        url = job.url.split("#")[0].rstrip("/")
        city = _norm((job.location or "").split(",")[0].split(";")[0])
        key = (_norm(job.title), _norm(job.company), city)
        if url in seen_urls or (job.company and key in seen_keys):
            continue
        seen_urls.add(url)
        seen_keys.add(key)
        unique.append(job)
    return unique


def search(params: SearchParams, source_keys: list[str]) -> dict:
    watchlist = ats.load_watchlist()
    results = {key: SourceResult(key, SOURCES.get(key, key)) for key in source_keys}
    jobs_by_source: dict[str, list[Job]] = {}

    with ThreadPoolExecutor(max_workers=len(source_keys) or 1) as pool:
        started = time.perf_counter()
        futures = {key: pool.submit(_run, key, params, watchlist) for key in source_keys}
        for key, future in futures.items():
            result = results[key]
            remaining = max(1.0, SOURCE_TIMEOUT - (time.perf_counter() - started))
            try:
                found, warnings, result.seconds = future.result(timeout=remaining)
                jobs_by_source[key] = found
                result.count = len(found)
                result.warnings = warnings
            except FutureTimeout:
                result.error = f"timed out after {SOURCE_TIMEOUT}s"
            except Exception as exc:  # noqa: BLE001 - one failing source must not break the search
                result.error = str(exc) or exc.__class__.__name__
        pool.shutdown(wait=False, cancel_futures=True)

    # Employer pages first, so a duplicate keeps the direct company link rather than a job-board copy.
    order = ["companies", "websearch"] + [k for k in source_keys if k not in ("companies", "websearch")]
    merged = [job for key in order for job in jobs_by_source.get(key, [])]
    unique = dedupe(merged)
    unique.sort(key=lambda j: j.date_posted or "", reverse=True)
    return {
        "jobs": [job.to_dict() for job in unique],
        "sources": [vars(results[key]) for key in source_keys],
        "duplicates_removed": len(merged) - len(unique),
    }
