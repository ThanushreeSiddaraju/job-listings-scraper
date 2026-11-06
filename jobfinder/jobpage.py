"""Read schema.org JobPosting data embedded in job pages (the same data Google Jobs uses)."""

import json
from datetime import date, datetime

from bs4 import BeautifulSoup

from . import attributes
from .models import Job


def _iter_objects(data):
    if isinstance(data, list):
        for item in data:
            yield from _iter_objects(item)
    elif isinstance(data, dict):
        yield data
        if "@graph" in data:
            yield from _iter_objects(data["@graph"])


def _is_job_posting(obj: dict) -> bool:
    kind = obj.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return "JobPosting" in kinds


def _name(value) -> str | None:
    if isinstance(value, dict):
        value = value.get("name")
    if isinstance(value, list):
        value = value[0] if value else None
    return str(value).strip() if value else None


def _location(value) -> str | None:
    places = value if isinstance(value, list) else [value]
    names = []
    for place in places:
        if not isinstance(place, dict):
            continue
        address = place.get("address", place)
        if isinstance(address, str):
            names.append(address)
            continue
        parts = [address.get(k) for k in ("addressLocality", "addressRegion", "addressCountry")]
        parts = [_name(p) for p in parts if p]
        if parts:
            names.append(", ".join(parts))
    return "; ".join(dict.fromkeys(names)) or None


def _to_date(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None


def _salary(value) -> str | None:
    """schema.org baseSalary: {"currency": "USD", "value": {"minValue": .., "maxValue": .., "unitText": "YEAR"}}."""
    if not isinstance(value, dict):
        return None
    amount = value.get("value")
    if not isinstance(amount, dict):
        amount = {"value": amount}
    return attributes.salary(amount.get("minValue") or amount.get("value"), amount.get("maxValue"),
                             value.get("currency"), amount.get("unitText"))


def extract_jobs(html: str, url: str, source: str, today: date | None = None) -> list[Job]:
    """Return the open JobPosting entries on a page; expired ones (validThrough in the past) are dropped."""
    today = today or date.today()
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except json.JSONDecodeError:
            continue
        for obj in _iter_objects(data):
            if not _is_job_posting(obj) or not obj.get("title"):
                continue
            valid_through = _to_date(obj.get("validThrough"))
            if valid_through and valid_through < today:
                continue
            posted = _to_date(obj.get("datePosted"))
            title = BeautifulSoup(str(obj["title"]), "html.parser").get_text(strip=True)
            location = _location(obj.get("jobLocation"))
            telecommute = str(obj.get("jobLocationType", "")).upper() == "TELECOMMUTE"
            mode = attributes.work_mode(None, telecommute, location, title)
            jobs.append(Job(
                title=title,
                company=_name(obj.get("hiringOrganization")),
                location=location,
                url=obj.get("url") or url,
                source=source,
                date_posted=posted.isoformat() if posted else None,
                remote=mode == "Remote" or None,
                experience=attributes.experience_level(title),
                job_type=attributes.job_type(obj.get("employmentType"), title),
                work_mode=mode,
                salary=_salary(obj.get("baseSalary")),
            ))
    return jobs
