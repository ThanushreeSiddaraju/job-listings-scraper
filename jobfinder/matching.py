"""Keyword and location matching for sources that return a company's whole job list."""

import re

# Cities that are commonly written two ways.
LOCATION_ALIASES = {
    "bengaluru": "bangalore",
    "bangalore": "bengaluru",
    "gurugram": "gurgaon",
    "gurgaon": "gurugram",
    "mumbai": "bombay",
    "chennai": "madras",
    "nyc": "new york",
    "sf": "san francisco",
}

# State codes Indeed India uses instead of city names.
INDIAN_CITY_STATES = {
    "bengaluru": "ka", "bangalore": "ka", "mysuru": "ka", "mysore": "ka",
    "mumbai": "mh", "pune": "mh", "nagpur": "mh", "hyderabad": "ts", "chennai": "tn", "coimbatore": "tn",
    "delhi": "dl", "new delhi": "dl", "gurugram": "hr", "gurgaon": "hr", "noida": "up", "kolkata": "wb",
    "ahmedabad": "gj", "kochi": "kl", "trivandrum": "kl", "thiruvananthapuram": "kl", "jaipur": "rj", "chandigarh": "ch",
}

# Words that carry no meaning in a job search ("jobs for python developer").
STOP_WORDS = {"job", "jobs", "role", "roles", "position", "positions", "for", "in", "a", "an", "the", "and", "of"}


# Abbreviations job titles use interchangeably ("Sr. Software Engg" = "Senior Software Engineer").
TITLE_ABBREVIATIONS = {
    "sr": "senior", "snr": "senior", "jr": "junior", "jnr": "junior",
    "eng": "engineer", "engg": "engineer", "engr": "engineer", "dev": "developer",
    "mgr": "manager", "mgmt": "management", "assoc": "associate", "asst": "assistant",
    "admin": "administrator", "exec": "executive", "intl": "international", "ml": "machine learning",
}

# Legal suffixes that vary between sources ("Infosys Ltd" = "Infosys Limited" = "Infosys").
COMPANY_SUFFIXES = {"inc", "llc", "ltd", "limited", "pvt", "private", "corp", "corporation", "co", "gmbh", "plc", "com"}


def terms(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9+#.]+", text.lower())
    words = [w.strip(".") for w in words]
    expanded = " ".join(TITLE_ABBREVIATIONS.get(w, w) for w in words if w)
    return [w for w in expanded.split() if w not in STOP_WORDS]


def normalize_title(title: str) -> str:
    return " ".join(terms(title))


def normalize_company(company: str | None) -> str:
    words = re.findall(r"[a-z0-9]+", (company or "").lower())
    while words and words[-1] in COMPANY_SUFFIXES:
        words.pop()
    return " ".join(words)


def relevance(title: str, query: str, age_days: int | None) -> int:
    """0-100 score: how closely the title matches the query, plus a bonus for recent postings."""
    wanted, have = terms(query), terms(title)
    if not wanted or not have:
        return 0
    if " ".join(wanted) in " ".join(have):
        match = 50  # exact phrase, in order
    else:
        match = round(40 * sum(w in have for w in wanted) / len(wanted))
    focus = round(20 * min(1, len(wanted) / len(have)))  # "Data Analyst" beats "Data Analyst & Ops Lead, APAC"
    recency = 0 if age_days is None else round(30 * max(0, 1 - age_days / 60))
    return match + focus + recency


def title_matches(title: str, query: str) -> bool:
    """True when every meaningful query word appears in the title ("python developer" ⊂ "Senior Python Developer")."""
    wanted = terms(query)
    have = set(terms(title))
    return bool(wanted) and all(word in have for word in wanted)


def location_matches(location: str | None, wanted: str, remote: bool | None = None) -> bool:
    if not wanted:
        return True
    if not location:
        return bool(remote)
    loc = location.lower()
    wanted = wanted.lower().strip()
    candidates = {wanted, LOCATION_ALIASES.get(wanted, wanted)}
    if any(c in loc for c in candidates):
        return True
    # Indeed India gives only the state ("KA, India" for a Bengaluru job), so accept the city's state code.
    state = INDIAN_CITY_STATES.get(wanted)
    return bool(state) and state in {part.strip() for part in re.split(r"[,;]", loc)}
