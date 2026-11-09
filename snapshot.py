"""Scrape job portals (Naukri, Indeed, LinkedIn, Google Jobs) for a list of roles and save the results as JSON.

The GitHub Pages site can't search these portals from the browser (they block requests from other websites),
so .github/workflows/pages.yml runs this every few hours and publishes the file with the site.

    python snapshot.py --config portal_roles.json --out docs/data/portal-jobs.json [--previous old.json]
"""

import argparse
import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from jobfinder.models import Job, SearchParams
from jobfinder.search import dedupe
from jobfinder.sources import boards

PAUSE_SECONDS = (2, 4)  # between searches on the same portal, to stay polite
GIVE_UP_AFTER = 3  # consecutive rate-limit errors before skipping the rest of a portal's searches
REUSE_PREVIOUS_DAYS = 3  # if a portal fails completely, keep its jobs from the last run if this recent


def scrape_site(site: str, cfg: dict) -> tuple[list[Job], list[str]]:
    jobs, errors, limited_in_a_row = [], [], 0
    for i, role in enumerate(cfg["roles"]):
        if i:
            time.sleep(random.uniform(*PAUSE_SECONDS))
        params = SearchParams(query=role, location=cfg.get("location", ""), country=cfg.get("country", "india"),
                              limit_per_source=cfg.get("results_per_search", 30))
        try:
            jobs.extend(boards.search(site, params))
            limited_in_a_row = 0
        except Exception as exc:  # noqa: BLE001 - record and carry on with the next role
            errors.append(f"{role}: {exc}")
            limited_in_a_row = limited_in_a_row + 1 if "rate-limited" in str(exc) else 0
            if limited_in_a_row >= GIVE_UP_AFTER:
                errors.append(f"stopped after {GIVE_UP_AFTER} rate-limit errors in a row")
                break
    return jobs, errors


def load_previous(path: str | None) -> dict:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def previous_jobs_for(site_label: str, previous: dict) -> list[Job]:
    generated = previous.get("generated_at")
    if not generated:
        return []
    age = datetime.now(timezone.utc) - datetime.fromisoformat(generated)
    if age > timedelta(days=REUSE_PREVIOUS_DAYS):
        return []
    fields = Job.__dataclass_fields__
    return [Job(**{k: v for k, v in j.items() if k in fields}) for j in previous.get("jobs", []) if j.get("source") == site_label]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="portal_roles.json")
    parser.add_argument("--out", default="docs/data/portal-jobs.json")
    parser.add_argument("--previous", help="last published snapshot, reused for portals that fail completely")
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    previous = load_previous(args.previous)
    sites = cfg["sites"]

    with ThreadPoolExecutor(max_workers=len(sites)) as pool:  # portals in parallel, roles one at a time per portal
        results = dict(zip(sites, pool.map(lambda s: scrape_site(s, cfg), sites)))

    status, ordered = {}, []
    for site in sites:
        label = boards.SITES.get(site, site)
        found, errors = results[site]
        reused = False
        if not found:
            found = previous_jobs_for(label, previous)
            reused = bool(found)
        status[site] = {"label": label, "count": len(found), "searches": len(cfg["roles"]),
                        "failed_searches": len([e for e in errors if ":" in e]), "errors": errors[:5],
                        "reused_previous": reused}
        ordered.extend(found)
        print(f"{label:12} {len(found):5} jobs  {len(errors)} errors{'  (reused previous run)' if reused else ''}")

    cutoff = (date.today() - timedelta(days=cfg.get("max_age_days", 14))).isoformat()
    unique = [j for j in dedupe(ordered) if not j.date_posted or j.date_posted >= cutoff]
    unique.sort(key=lambda j: j.date_posted or "", reverse=True)
    if not unique:
        print("No jobs from any portal; not writing an empty snapshot.", file=sys.stderr)
        return 1

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "country": cfg.get("country", "india"),
        "roles": cfg["roles"],
        "sites": status,
        "jobs": [{k: v for k, v in j.to_dict().items() if v is not None} for j in unique],
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Saved {len(unique)} jobs to {args.out} ({os.path.getsize(args.out) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
