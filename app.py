"""Web UI: multi-source job finder (/) and the roadmap.sh practice scraper (/practice).

Run with:  python app.py   then open http://127.0.0.1:5050
"""

import os
import time

import requests
from flask import Flask, jsonify, render_template, request, send_file

from jobfinder import store
from jobfinder.models import SearchParams
from jobfinder.search import DEFAULT_SOURCES, SOURCES, available_sources, search
from scraper import BASE_URL, fetch_html, parse_jobs, save_csv

CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobs.csv")

app = Flask(__name__)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/sources")
def sources():
    return jsonify(sources=available_sources())


@app.get("/api/search")
def job_search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify(error="Enter a job title or keyword."), 400

    keys = [k for k in request.args.get("sources", ",".join(DEFAULT_SOURCES)).split(",") if k in SOURCES]
    enabled = {s["key"] for s in available_sources() if s["enabled"]}
    keys = [k for k in keys if k in enabled]
    if not keys:
        return jsonify(error="Pick at least one source."), 400

    params = SearchParams(
        query=query,
        location=request.args.get("location", "").strip(),
        country=request.args.get("country", "india"),
        remote_only=request.args.get("remote") == "1",
        exact_title=request.args.get("exact", "1") == "1",
        max_age_days=int(request.args["days"]) if request.args.get("days", "").isdigit() else None,
    )
    started = time.perf_counter()
    result = search(params, keys)
    result["seconds"] = round(time.perf_counter() - started, 1)
    return jsonify(result)


@app.get("/api/blocked")
def blocked_list():
    return jsonify(companies=store.blocked_companies())


@app.post("/api/blocked")
def block():
    name = (request.get_json(silent=True) or {}).get("company", "").strip()
    if not name:
        return jsonify(error="Missing company name."), 400
    store.block_company(name)
    return jsonify(companies=store.blocked_companies())


@app.delete("/api/blocked")
def unblock():
    store.unblock_company((request.get_json(silent=True) or {}).get("company", ""))
    return jsonify(companies=store.blocked_companies())


@app.get("/practice")
def practice():
    return render_template("practice.html", source_url=BASE_URL)


@app.post("/api/practice/scrape")
def practice_scrape():
    started = time.perf_counter()
    try:
        html = fetch_html(BASE_URL)
    except requests.RequestException as exc:
        return jsonify(error=f"Failed to fetch {BASE_URL}: {exc}"), 502

    jobs = parse_jobs(html, BASE_URL)
    if not jobs:
        return jsonify(error="No job listings found; the page structure may have changed."), 502

    save_csv(jobs, CSV_PATH)
    return jsonify(jobs=jobs, seconds=round(time.perf_counter() - started, 2))


@app.get("/practice/download")
def practice_download():
    if not os.path.exists(CSV_PATH):
        return "Run a scrape first.", 404
    return send_file(CSV_PATH, mimetype="text/csv", as_attachment=True, download_name="jobs.csv")


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", 5050)), threaded=True)
