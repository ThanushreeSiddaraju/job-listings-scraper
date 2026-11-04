"""Run every selected source in parallel, then merge, de-duplicate, filter and rank the results."""

import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher

from . import store
from .matching import normalize_company, normalize_title, relevance, title_matches
from .models import Job, SearchParams
from .sources import ats, boards, websearch

SOURCE_TIMEOUT = 90  # seconds; a slow source is reported, the rest are still shown
TITLE_SIMILARITY = 0.9  # titles this similar (after normalizing) at the same company count as one job
LEVEL_WORDS = {"i", "ii", "iii", "iv", "v", "intern", "junior", "associate", "senior", "staff", "principal",
               "lead", "head", "manager", "director"}
CANONICAL_CITIES = {"bangalore": "bengaluru", "gurgaon": "gurugram", "bombay": "mumbai", "madras": "chennai",
                    "bengaluru east": "bengaluru", "bengaluru north": "bengaluru", "bengaluru south": "bengaluru"}

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


def _city(location: str | None) -> str | None:
    """First part of the location, or None when it's only a state code like Indeed's "KA, India"."""
    first = re.sub(r"[^a-z ]+", " ", (location or "").split(",")[0].split(";")[0].lower()).strip()
    if len(first) <= 2:
        return None
    return CANONICAL_CITIES.get(first, first)


def _levels(title: str) -> set[str]:
    return {w for w in title.split() if w.isdigit() or w in LEVEL_WORDS}


def _same_title(a: str, b: str) -> bool:
    if a == b:
        return True
    # "Engineer II" vs "Engineer III" are nearly identical strings but different jobs.
    return _levels(a) == _levels(b) and SequenceMatcher(None, a, b).ratio() >= TITLE_SIMILARITY


def dedupe(jobs: list[Job]) -> list[Job]:
    """Drop repeats, keeping the first seen. A repeat is the same URL, or the same job listed on another site:
    same company, near-identical title ("Sr. Python Dev" ~ "Senior Python Developer") and the same city
    (or one site gives no city). Jobs from the same source are never merged: a company board listing
    "Software Engineer" five times has five openings."""
    seen_urls: set[str] = set()
    kept_by_company: dict[str, list[tuple[str, str | None, str]]] = {}
    unique = []
    for job in jobs:
        url = job.url.split("#")[0].rstrip("/")
        if url in seen_urls:
            continue
        company = normalize_company(job.company)
        title, city = normalize_title(job.title), _city(job.location)
        kept = kept_by_company.setdefault(company, []) if company else []
        if any(source != job.source and _same_title(title, t) and (city is None or c is None or city == c)
               for t, c, source in kept):
            continue
        seen_urls.add(url)
        kept.append((title, city, job.source))
        unique.append(job)
    return unique


def _age_days(job: Job, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(job.date_posted[:10])).days if job.date_posted else None
    except ValueError:
        return None


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

    today = date.today()
    blocked = {normalize_company(name) for name in store.blocked_companies()}
    kept, too_old, hidden = [], 0, 0
    for job in unique:
        age = _age_days(job, today)
        if params.max_age_days is not None and age is not None and age > params.max_age_days:
            too_old += 1
        elif job.company and normalize_company(job.company) in blocked:
            hidden += 1
        else:
            kept.append((job, age))

    new_urls = store.mark_seen([job.url for job, _ in kept])
    rows = [
        {**job.to_dict(), "score": relevance(job.title, params.query, age), "is_new": job.url in new_urls}
        for job, age in kept
    ]
    rows.sort(key=lambda r: (r["score"], r["date_posted"] or ""), reverse=True)
    return {
        "jobs": rows,
        "sources": [vars(results[key]) for key in source_keys],
        "duplicates_removed": len(merged) - len(unique),
        "too_old": too_old,
        "hidden_companies": hidden,
    }
