"""Job boards via JobSpy (https://github.com/speedyapply/JobSpy): Indeed, LinkedIn, Google Jobs, Naukri, ...

Each site is searched separately so the UI can report which ones worked. Sites that block or
rate-limit requests are reported as errors; we don't route around blocking with proxies.
"""

import logging
import math

from jobspy import scrape_jobs

from ..models import Job, SearchParams

SITES = {
    "indeed": "Indeed",
    "linkedin": "LinkedIn",
    "google": "Google Jobs",
    "naukri": "Naukri",
    "glassdoor": "Glassdoor",
    "zip_recruiter": "ZipRecruiter",
}

# Country values from the UI (JobSpy's Indeed names) -> place names the other boards understand.
COUNTRY_NAMES = {"usa": "United States", "uk": "United Kingdom", "united arab emirates": "United Arab Emirates"}


def _clean(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    return text if text and text.lower() not in {"nan", "nat", "none"} else None


class _ErrorCollector(logging.Handler):
    """JobSpy logs failures (e.g. HTTP 429) instead of raising; collect them so they can be reported."""

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def _friendly(message: str) -> str:
    if "429" in message:
        return "rate-limited by the site, try again in a few minutes"
    return message


def search(site: str, params: SearchParams) -> list[Job]:
    # Without a city, search the selected country (otherwise LinkedIn/Google return jobs worldwide).
    location = params.location or COUNTRY_NAMES.get(params.country, params.country.title())
    where = f" in {location}"
    logger = logging.getLogger(f"jobspy.{site}")
    collector = _ErrorCollector()
    logger.addHandler(collector)
    try:
        df = scrape_jobs(
            site_name=[site],
            search_term=params.query,
            google_search_term=f"{params.query} jobs{where} since last week",
            location=location,
            is_remote=params.remote_only,
            results_wanted=params.limit_per_source,
            country_indeed=params.country,
            hours_old=24 * 30,
            verbose=0,
        )
    finally:
        logger.removeHandler(collector)
    # "no job data returned" just means zero results; anything else (blocked, HTTP errors) is a failure.
    failures = [m for m in collector.messages if "no job data" not in m]
    if df.empty and failures:
        raise RuntimeError(_friendly(failures[-1]))

    jobs = []
    for row in df.to_dict("records"):
        title = _clean(row.get("title"))
        url = _clean(row.get("job_url_direct")) or _clean(row.get("job_url"))
        if not title or not url:
            continue
        posted = _clean(row.get("date_posted"))
        jobs.append(Job(
            title=title,
            company=_clean(row.get("company")),
            location=_clean(row.get("location")),
            url=url,
            source=SITES.get(site, site),
            date_posted=posted[:10] if posted else None,
            remote=bool(row.get("is_remote")) if row.get("is_remote") is not None else None,
        ))
    return jobs
