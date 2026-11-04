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
