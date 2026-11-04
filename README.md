# Job Finder

Type a role ("python developer", "data analyst") and get matching job links from many sources at once,
with duplicates removed and direct company links preferred.

**Live version: https://thanushreesiddaraju.github.io/job-listings-scraper/**: searches company career pages
(Greenhouse, Lever, Ashby) directly from your browser. Those APIs allow requests from any website, so no server is needed;
the page lives in [`docs/`](docs). Job boards (Indeed, LinkedIn, Naukri, …) block browser requests and need the full app below.

It started as the [roadmap.sh Job Listings Scraper project](https://roadmap.sh/projects/job-listings-scraper),
which is still included as a practice scraper (see [Practice scraper](#practice-scraper-roadmapsh)).

## Sources

| Source | How | Notes |
|---|---|---|
| Indeed, LinkedIn, Google Jobs, Naukri, Glassdoor, ZipRecruiter | [JobSpy](https://github.com/speedyapply/JobSpy) | Scrapes the boards. LinkedIn rate-limits after a few searches; blocked sites are reported, not worked around. Indeed/Glassdoor search one country at a time. |
| Company career pages | Public Greenhouse, Lever and Ashby APIs | No key needed. Companies are listed in [`docs/companies.json`](docs/companies.json) (shared with the live site); add your own. |
| Web search | Self-hosted [SearXNG](https://github.com/searxng/searxng) | Queries Google, Bing, DuckDuckGo, Brave and more. Finds new companies on Greenhouse/Lever/Ashby and reads `JobPosting` data from other job pages (respecting robots.txt). Optional; off until `SEARXNG_URL` is set. |

Every source runs in parallel and is optional: if one fails, the others still return results and the UI shows what went wrong.

## Setup

Requires Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python app.py                  # then open http://127.0.0.1:5050
```

Search by keyword, optionally a city and country, then filter, sort and download the results as CSV.
**Exact title match** (on by default) keeps only titles containing every keyword, since job boards also return loosely related roles.

- **Match score:** results are ranked by how closely the title matches your search (exact phrase, no extra words) plus a bonus for recent posts.
- **Posted within:** skip jobs older than 24 hours / 3 / 7 / 30 days. Jobs without a date are kept.
- **NEW badge / Only new:** jobs you haven't seen in any earlier search, so repeat searches show what's changed.
- **Hide company:** hides a company (e.g. a staffing agency) from all future searches; un-hide it from the list above the results.
- **Duplicates:** the same job on two sites is shown once, even when the title is written differently ("Sr. Python Dev" / "Senior Python Developer") or one site only gives a state. Separate openings on one company board, and different levels ("Engineer II" / "III"), are never merged.

### Caching

Company boards are cached in `data/jobfinder.db` (SQLite, not committed), so they survive restarts. Within 10 minutes the saved copy is used
directly; after that it is revalidated with the board's ETag, and an unchanged board costs an empty `304 Not Modified` reply instead of a
full download (OpenAI's board alone is ~13 MB). If a revalidation stalls or fails, the saved copy is used.

### Adding companies

Find the company's careers page. If it is hosted on `boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>` or
`jobs.ashbyhq.com/<slug>`, add an entry to `docs/companies.json`:

```json
{"name": "Stripe", "ats": "greenhouse", "slug": "stripe"}
```

### Enabling web search (SearXNG)

Needs [Docker](https://www.docker.com/products/docker-desktop/).

```bash
cd searxng && docker compose up -d
export SEARXNG_URL=http://localhost:8888
python app.py
```

Change `secret_key` in `searxng/settings.yml` first. The provided settings enable the JSON output the app needs.

## Practice scraper (roadmap.sh)

[`scraper.py`](scraper.py) scrapes the [Fake Python Jobs](https://realpython.github.io/fake-jobs/) practice site
(title, company, location, date posted, job URL) into a CSV:

```bash
python scraper.py              # writes jobs.csv
python scraper.py -o out.csv   # custom output path
```

It also has a UI at http://127.0.0.1:5050/practice.

## Tests

```bash
python -m unittest -v
```

The tests use saved sample responses, so they don't need internet access.
