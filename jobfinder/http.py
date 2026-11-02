"""Shared HTTP session with a short in-memory cache and a robots.txt check."""

import threading
import time
from urllib import robotparser
from urllib.parse import urlsplit

import requests

USER_AGENT = "job-finder/1.0 (personal job search; +https://roadmap.sh/projects/job-listings-scraper)"
TIMEOUT = 15
CACHE_SECONDS = 600

_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT
_cache: dict[str, tuple[float, object]] = {}
_robots: dict[str, robotparser.RobotFileParser | None] = {}
_lock = threading.Lock()


def get_json(url: str, params: dict | None = None):
    """GET a JSON endpoint, reusing a cached copy for CACHE_SECONDS (company job boards rarely change faster)."""
    key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    with _lock:
        hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    # Some company boards are several MB (every posting's description included), so allow longer than for pages.
    response = _session.get(url, params=params, timeout=TIMEOUT * 2)
    response.raise_for_status()
    data = response.json()
    with _lock:
        _cache[key] = (time.time(), data)
    return data


def allowed_by_robots(url: str) -> bool:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    with _lock:
        known = origin in _robots
        parser = _robots.get(origin)
    if not known:
        parser = robotparser.RobotFileParser()
        try:
            response = _session.get(origin + "/robots.txt", timeout=5)
            # No robots.txt (or an error page) means no stated restrictions.
            parser.parse(response.text.splitlines() if response.ok else [])
        except requests.RequestException:
            parser = None
        with _lock:
            _robots[origin] = parser
    return parser is None or parser.can_fetch(USER_AGENT, url)


def get_page(url: str) -> str | None:
    """Fetch an HTML page if robots.txt allows it; None otherwise."""
    if not allowed_by_robots(url):
        return None
    response = _session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    return response.text
