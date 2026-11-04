"""Shared HTTP session with an ETag-aware cache and a robots.txt check."""

import json
import threading
import time
from urllib import robotparser
from urllib.parse import urlsplit

import requests

from . import store

USER_AGENT = "job-finder/1.0 (personal job search; +https://roadmap.sh/projects/job-listings-scraper)"
TIMEOUT = 15
CACHE_SECONDS = 600
REVALIDATE_TIMEOUT = 8

_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT
_cache: dict[str, tuple[str | None, object]] = {}
_robots: dict[str, robotparser.RobotFileParser | None] = {}
_lock = threading.Lock()


def get_json(url: str, params: dict | None = None):
    """GET a JSON endpoint through a cache that survives restarts.

    Within CACHE_SECONDS the cached copy is used without any request. After that the copy is revalidated
    with its ETag: if the board hasn't changed the server answers "304 Not Modified" with an empty body
    instead of resending it (OpenAI's board alone is ~13 MB of JSON).
    """
    key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    with _lock:
        hit = _cache.get(key)  # (etag, parsed data) - saves re-parsing large bodies
    etag, checked_at, body = store.cache_get(key)
    if body is not None and time.time() - checked_at < CACHE_SECONDS:
        return hit[1] if hit and hit[0] == etag else _remember(key, etag, json.loads(body))

    headers = {"If-None-Match": etag} if etag and body is not None else {}
    try:
        # A first download can be several MB, so allow longer; a revalidation should be quick, and if it
        # stalls (Lever sometimes does) the saved copy is good enough, rather than holding up the whole search.
        response = _session.get(url, params=params, headers=headers, timeout=REVALIDATE_TIMEOUT if body else TIMEOUT * 2)
        if body is not None and response.status_code >= 500:
            raise requests.HTTPError(f"{response.status_code} from {url}")
    except requests.RequestException:
        if body is None:
            raise
        return hit[1] if hit and hit[0] == etag else _remember(key, etag, json.loads(body))
    if response.status_code == 304:
        store.cache_touch(key)
        return hit[1] if hit and hit[0] == etag else _remember(key, etag, json.loads(body))
    response.raise_for_status()
    new_etag = response.headers.get("ETag")
    store.cache_put(key, new_etag, response.content)
    return _remember(key, new_etag, response.json())


def _remember(key: str, etag: str | None, data):
    with _lock:
        _cache[key] = (etag, data)
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
