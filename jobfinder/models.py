from dataclasses import asdict, dataclass


@dataclass
class Job:
    title: str
    company: str | None
    location: str | None
    url: str
    source: str
    date_posted: str | None = None  # ISO date, YYYY-MM-DD
    remote: bool | None = None
    experience: str | None = None  # one of attributes.EXPERIENCE_LEVELS
    job_type: str | None = None  # one of attributes.JOB_TYPES
    work_mode: str | None = None  # one of attributes.WORK_MODES
    salary: str | None = None  # display text, e.g. "$207K–$285K / yr"
    department: str | None = None
    company_url: str | None = None  # the company's own posting, when a job board copied it from there

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SearchParams:
    query: str
    location: str = ""
    country: str = "india"  # used by Indeed/Glassdoor, which search one country at a time
    remote_only: bool = False
    exact_title: bool = True  # job boards return loosely related jobs; keep only titles containing every keyword
    max_age_days: int | None = None  # drop jobs posted longer ago than this (undated jobs are kept)
    limit_per_source: int = 25
