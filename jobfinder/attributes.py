"""Experience level, job type, work mode and salary, normalized across sources so the UI can filter on them.

Sources describe these differently (LinkedIn's "Mid-Senior level", Naukri's "2-5 Yrs", Ashby's "FullTime",
Lever's "Permanent"), and many give nothing at all, so the job title is used as a fallback.
The same rules are mirrored in docs/index.html for the GitHub Pages version.
"""

import math
import re

EXPERIENCE_LEVELS = ["Internship", "Entry level", "Mid level", "Senior", "Lead / Principal", "Director / Executive"]
JOB_TYPES = ["Full-time", "Part-time", "Contract", "Internship", "Temporary"]
WORK_MODES = ["Remote", "Hybrid", "On-site"]

# Checked in order: the first group with a word in the title wins ("Senior Director" is a director).
# Roman numerals are levels ("Engineer II"); bare digits are not ("Python 3 Developer", "Tier 1 Support").
_TITLE_LEVELS = [
    ("Internship", r"intern|internship|apprentice|apprenticeship|co-?op"),
    ("Director / Executive", r"director|vp|svp|evp|vice president|head of|chief|cto|ceo|cfo|coo|cio|ciso|president"),
    ("Lead / Principal", r"lead|principal|staff|architect|distinguished|fellow|iv|v"),
    ("Senior", r"senior|sr|snr|iii"),
    ("Mid level", r"mid|mid-level|intermediate|ii"),
    ("Entry level", r"junior|jr|jnr|entry|entry-level|graduate|grad|fresher|freshers|trainee|associate|i|new grad|early career"),
]
_TITLE_LEVEL_RES = [(level, re.compile(rf"(?<![\w-])(?:{words})(?![\w-])")) for level, words in _TITLE_LEVELS]

# LinkedIn's job_level values (and similar wording on other boards).
_LEVEL_HINTS = {
    "internship": "Internship", "intern": "Internship",
    "entry level": "Entry level", "entry": "Entry level", "associate": "Entry level", "junior": "Entry level",
    "mid-senior level": "Senior", "mid senior level": "Senior", "mid level": "Mid level", "senior": "Senior",
    "director": "Director / Executive", "executive": "Director / Executive",
}

_TYPE_WORDS = [
    ("Internship", r"intern|internship"),
    ("Part-time", r"part[\s_-]?time|parttime"),
    ("Contract", r"contract|contractor|freelance|consultant"),
    ("Temporary", r"temporary|temp|short[\s_-]?term|seasonal|fixed[\s_-]?term"),
    ("Full-time", r"full[\s_-]?time|fulltime|permanent|regular"),
]
_TYPE_RES = [(kind, re.compile(rf"\b(?:{words})\b")) for kind, words in _TYPE_WORDS]
# In a title, only the unambiguous words count ("Contract Manager" is a full-time job about contracts).
_TITLE_TYPE_RES = [(kind, re.compile(rf"(?<!\w)(?:{words})(?!\w)")) for kind, words in [
    ("Internship", r"intern|internship"),
    ("Part-time", r"part[\s-]?time"),
    ("Contract", r"contract role|contractor|freelance|\(contract\)|- contract"),
    ("Temporary", r"temporary|seasonal|fixed[\s-]?term"),
]]


def years_to_level(min_years: int) -> str:
    if min_years <= 1:
        return "Entry level"
    if min_years <= 4:
        return "Mid level"
    if min_years <= 7:
        return "Senior"
    return "Lead / Principal"


def experience_level(title: str, hint: str | None = None, years: str | None = None) -> str | None:
    """Level from the title, a board's own level label, or a years range like Naukri's "2-5 Yrs".
    An internship title always wins; otherwise the board's label, then years, then the title's words."""
    # "Engineer [Office of the CTO]" names a team, not the job's level.
    text = re.sub(r"office of the \w+", "", (title or "").lower())
    if _TITLE_LEVEL_RES[0][1].search(text):
        return "Internship"
    if hint and (level := _LEVEL_HINTS.get(hint.strip().lower().replace("_", " "))):
        return level
    if years and (m := re.search(r"\d+", years)):
        return years_to_level(int(m.group()))
    for level, pattern in _TITLE_LEVEL_RES[1:]:
        if pattern.search(text):
            return level
    return None


def job_type(raw=None, title: str = "") -> str | None:
    """Employment type from a board's value (str, or a list like JobSpy's), falling back to the title."""
    values = raw if isinstance(raw, (list, tuple)) else [raw]
    for value in values:
        text = str(getattr(value, "value", value) or "").lower()
        if not text or text in {"nan", "none"}:
            continue
        for kind, pattern in _TYPE_RES:
            if pattern.search(text):
                return kind
    text = (title or "").lower()
    for kind, pattern in _TITLE_TYPE_RES:
        if pattern.search(text):
            return kind
    return None


def work_mode(raw: str | None = None, remote: bool | None = None, *texts: str | None) -> str | None:
    """Remote / Hybrid / On-site from a board's value, then the location or title text, then a remote flag."""
    for text in [raw, *texts]:
        text = (text or "").lower()
        if "hybrid" in text:
            return "Hybrid"
        if re.search(r"remote|work from home|wfh|telecommute|anywhere", text):
            return "Remote"
        if re.search(r"on[\s_-]?site|in[\s_-]?office|work from office", text):
            return "On-site"
    return "Remote" if remote else None


_SYMBOLS = {"USD": "$", "INR": "₹", "EUR": "€", "GBP": "£", "CAD": "CA$", "AUD": "A$", "SGD": "S$"}
_INTERVALS = {"yearly": "yr", "year": "yr", "1 year": "yr", "annual": "yr", "monthly": "mo", "month": "mo",
              "1 month": "mo", "weekly": "wk", "week": "wk", "daily": "day", "day": "day",
              "hourly": "hr", "hour": "hr", "1 hour": "hr"}


def _num(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or number <= 0 else number


def _short(n: float) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if n >= 1000:
        return f"{n / 1000:.0f}K"
    return f"{n:.0f}"


def salary(min_amount=None, max_amount=None, currency: str | None = None, interval: str | None = None) -> str | None:
    """A short pay range such as "$207K–$285K / yr", or None when the source gives no numbers."""
    low, high = _num(min_amount), _num(max_amount)
    if low is None and high is None:
        return None
    code = str(currency or "").upper()
    symbol = _SYMBOLS.get(code, f"{code} " if code and code != "NAN" else "")
    amount = (f"{symbol}{_short(low)}–{symbol}{_short(high)}" if low and high and low != high
              else f"{symbol}{_short(low or high)}")
    per = _INTERVALS.get(str(getattr(interval, "value", interval) or "").lower())
    return f"{amount} / {per}" if per else amount
