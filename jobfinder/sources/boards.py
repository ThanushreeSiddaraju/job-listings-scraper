"""Job boards via JobSpy (https://github.com/speedyapply/JobSpy): Indeed, LinkedIn, Google Jobs, Naukri, ...

Each site is searched separately so the UI can report which ones worked. Sites that block or
rate-limit requests are reported as errors; we don't route around blocking with proxies.
"""

import logging
import math
from urllib.parse import urlsplit

from jobspy import scrape_jobs

from .. import attributes
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
    except (ValueError, KeyError, IndexError, AttributeError) as exc:
        # JobSpy parses the sites' pages; when a site changes its layout this surfaces as a parsing error.
        raise RuntimeError(f"couldn't read {SITES.get(site, site)}'s results (the site may have changed)") from exc
    finally:
        logger.removeHandler(collector)
    # "no job data returned" just means zero results; anything else (blocked, HTTP errors) is a failure.
    failures = [m for m in collector.messages if "no job data" not in m]
    if df.empty and failures:
        raise RuntimeError(_friendly(failures[-1]))

    jobs = []
    for row in df.to_dict("records"):
        title = _clean(row.get("title"))
        board_url, direct_url = _clean(row.get("job_url")), _clean(row.get("job_url_direct"))
        url = board_url or direct_url  # the listing on the job board itself
        if not title or not url:
            continue
        # Many listings are copied from the company's careers site; keep that link too when it's elsewhere.
        company_url = direct_url if direct_url and urlsplit(direct_url).netloc != urlsplit(url).netloc else None
        posted = _clean(row.get("date_posted"))
        location = _clean(row.get("location"))
        is_remote = row.get("is_remote")
        is_remote = bool(is_remote) if is_remote is not None and _clean(is_remote) is not None else None
        mode = attributes.work_mode(_clean(row.get("work_from_home_type")), is_remote, location, title)
        jobs.append(Job(
            title=title,
            company=_clean(row.get("company")),
            location=location,
            url=url,
            source=SITES.get(site, site),
            date_posted=posted[:10] if posted else None,
            remote=True if mode == "Remote" else is_remote,
            experience=attributes.experience_level(title, _clean(row.get("job_level")), _clean(row.get("experience_range"))),
            job_type=attributes.job_type(row.get("job_type"), title),
            work_mode=mode,
            salary=attributes.salary(row.get("min_amount"), row.get("max_amount"), _clean(row.get("currency")),
                                     _clean(row.get("interval"))),
            department=_clean(row.get("job_function")),
            company_url=company_url,
        ))
    return jobs
