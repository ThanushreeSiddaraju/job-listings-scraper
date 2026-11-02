"""Scrape job listings from the Fake Python Jobs site and save them to CSV.

Project spec: https://roadmap.sh/projects/job-listings-scraper
"""

import argparse
import csv
import sys
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://realpython.github.io/fake-jobs/"
FIELDS = ["title", "company", "location", "date_posted", "url"]


def fetch_html(url: str, timeout: float = 10.0) -> str:
    response = requests.get(url, timeout=timeout, headers={"User-Agent": "job-listings-scraper/1.0"})
    response.raise_for_status()
    return response.text


def text_or_none(parent, selector: str) -> str | None:
    """Return the stripped text of the first match, or None if missing/empty."""
    element = parent.select_one(selector)
    if element is None:
        return None
    text = element.get_text(strip=True)
    return text or None


def find_apply_url(card, page_url: str) -> str | None:
    # Each card has two footer links ("Learn" and "Apply"); only "Apply" points to the job page.
    for link in card.select("footer a[href]"):
        if link.get_text(strip=True).lower() == "apply":
            return urljoin(page_url, link["href"])
    return None


def parse_jobs(html: str, page_url: str = BASE_URL) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for card in soup.select("div.card"):
        time_tag = card.select_one("time")
        job = {
            "title": text_or_none(card, "h2.title"),
            "company": text_or_none(card, "h3.company"),
            "location": text_or_none(card, "p.location"),
            "date_posted": time_tag.get("datetime") if time_tag else None,
            "url": find_apply_url(card, page_url),
        }
        # A card with no title is not a usable listing.
        if job["title"] is None:
            continue
        jobs.append(job)
    return jobs


def save_csv(jobs: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for job in jobs:
            writer.writerow({field: job.get(field) or "" for field in FIELDS})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", default="jobs.csv", help="CSV file to write (default: jobs.csv)")
    parser.add_argument("--url", default=BASE_URL, help="page to scrape")
    args = parser.parse_args()

    try:
        html = fetch_html(args.url)
    except requests.RequestException as exc:
        print(f"Failed to fetch {args.url}: {exc}", file=sys.stderr)
        return 1

    jobs = parse_jobs(html, args.url)
    if not jobs:
        print("No job listings found; the page structure may have changed.", file=sys.stderr)
        return 1

    save_csv(jobs, args.output)
    missing = sum(1 for job in jobs for field in FIELDS if not job.get(field))
    print(f"Saved {len(jobs)} jobs to {args.output} ({missing} missing field values)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
