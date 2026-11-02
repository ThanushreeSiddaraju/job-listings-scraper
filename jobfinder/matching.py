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

# Words that carry no meaning in a job search ("jobs for python developer").
STOP_WORDS = {"job", "jobs", "role", "roles", "position", "positions", "for", "in", "a", "an", "the", "and", "of"}


def terms(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9+#.]+", text.lower())
    return [w.strip(".") for w in words if w.strip(".") and w not in STOP_WORDS]


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
    return any(c in loc for c in candidates)
