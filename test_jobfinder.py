import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

from jobfinder import attributes, http, store
from jobfinder import search as search_module
from jobfinder.jobpage import extract_jobs
from jobfinder.matching import location_matches, normalize_company, normalize_title, relevance, title_matches
from jobfinder.models import Job, SearchParams
from jobfinder.sources import ats, websearch


def setUpModule():
    # Keep tests away from the real data/jobfinder.db.
    global _tmp
    _tmp = tempfile.TemporaryDirectory()
    store.DB_PATH = os.path.join(_tmp.name, "test.db")
    store._ready = False


def tearDownModule():
    _tmp.cleanup()

JOB_PAGE = """
<html><head>
<script type="application/ld+json">
{"@context": "https://schema.org", "@graph": [
  {"@type": "Organization", "name": "Acme"},
  {"@type": "JobPosting", "title": "Senior Data Analyst", "datePosted": "2026-10-20",
   "validThrough": "2026-12-31T00:00:00Z",
   "hiringOrganization": {"@type": "Organization", "name": "Acme"},
   "jobLocation": {"@type": "Place", "address": {"addressLocality": "Bengaluru", "addressCountry": "IN"}}}
]}
</script>
<script type="application/ld+json">
{"@type": "JobPosting", "title": "Old Data Analyst", "validThrough": "2026-01-01",
 "hiringOrganization": "Acme"}
</script>
<script type="application/ld+json">not json</script>
</head></html>
"""

GREENHOUSE = {"jobs": [
    {"title": "Data Analyst", "company_name": "Stripe", "absolute_url": "https://stripe.com/jobs/1",
     "location": {"name": "Bengaluru, India"}, "first_published": "2026-10-01T10:00:00-04:00"},
    {"title": "Account Executive", "company_name": "Stripe", "absolute_url": "https://stripe.com/jobs/2",
     "location": {"name": "Bengaluru, India"}, "first_published": "2026-10-01T10:00:00-04:00"},
]}
LEVER = [
    {"text": "Senior Data Analyst", "hostedUrl": "https://jobs.lever.co/acme/1",
     "categories": {"location": "Bangalore", "commitment": "Permanent", "department": "Analytics"},
     "createdAt": 1792767640768, "workplaceType": "hybrid"},
]
ASHBY = {"jobs": [
    {"title": "Data Analyst", "jobUrl": "https://jobs.ashbyhq.com/acme/1", "location": "Remote",
     "secondaryLocations": [{"location": "London"}], "publishedAt": "2026-09-01T00:00:00Z",
     "isRemote": True, "isListed": True, "employmentType": "Contract", "department": "Finance",
     "compensation": {"summaryComponents": [
         {"compensationType": "Salary", "interval": "1 YEAR", "currencyCode": "USD", "minValue": 90000, "maxValue": 120000}]}},
    {"title": "Data Analyst (unlisted)", "jobUrl": "https://jobs.ashbyhq.com/acme/2", "location": "Remote",
     "isListed": False},
]}


class MatchingTest(unittest.TestCase):
    def test_title_needs_every_keyword(self):
        self.assertTrue(title_matches("Senior Data Analyst", "data analyst"))
        self.assertTrue(title_matches("Analyst, Data Platform", "data analyst jobs"))
        self.assertFalse(title_matches("Business Analyst", "data analyst"))
        self.assertTrue(title_matches("C++ Developer", "c++ developer"))

    def test_normalizing(self):
        self.assertEqual(normalize_title("Sr. Software Engg - Python"), "senior software engineer python")
        self.assertEqual(normalize_company("Infosys Pvt. Ltd."), "infosys")
        self.assertEqual(normalize_company("Amazon.com"), "amazon")
        self.assertTrue(title_matches("Sr. Python Dev", "senior python developer"))

    def test_relevance_prefers_exact_focused_recent_titles(self):
        exact = relevance("Data Analyst", "data analyst", age_days=1)
        wordy = relevance("Data Analyst & Ops Lead, APAC", "data analyst", age_days=1)
        old = relevance("Data Analyst", "data analyst", age_days=90)
        partial = relevance("Analyst, Data Platform", "data analyst", age_days=1)
        self.assertGreater(exact, wordy)
        self.assertGreater(exact, old)
        self.assertGreater(wordy, partial - 1)
        self.assertEqual(relevance("", "data analyst", None), 0)

    def test_location_aliases_and_remote(self):
        self.assertTrue(location_matches("Bangalore, KA", "Bengaluru"))
        self.assertTrue(location_matches(None, "Pune", remote=True))
        self.assertFalse(location_matches("Mumbai", "Pune"))
        self.assertTrue(location_matches("Anywhere", ""))


class AttributesTest(unittest.TestCase):
    def test_experience_from_title(self):
        cases = {
            "Data Analyst Intern": "Internship", "Summer Internship - Data": "Internship",
            "Junior Data Analyst": "Entry level", "Graduate Software Engineer": "Entry level",
            "Software Engineer I": "Entry level", "Software Engineer II": "Mid level",
            "Sr. Data Analyst": "Senior", "Software Engineer III": "Senior",
            "Staff Engineer": "Lead / Principal", "Principal Data Scientist": "Lead / Principal",
            "Senior Director, Analytics": "Director / Executive", "VP of Engineering": "Director / Executive",
            "Forward Deployed Engineer [Office of the CTO]": None, "Data Analyst": None, "Python 3 Developer": None, "Staffing Coordinator": None,
        }
        for title, level in cases.items():
            self.assertEqual(attributes.experience_level(title), level, title)

    def test_experience_from_board_hints(self):
        self.assertEqual(attributes.experience_level("Data Analyst", hint="Mid-Senior level"), "Senior")
        self.assertEqual(attributes.experience_level("Data Analyst", hint="Entry level"), "Entry level")
        self.assertEqual(attributes.experience_level("Data Analyst", years="2-5 Yrs"), "Mid level")
        self.assertEqual(attributes.experience_level("Data Analyst", years="0-1 Yrs"), "Entry level")
        self.assertEqual(attributes.experience_level("Data Analyst Intern", hint="Entry level"), "Internship")
        self.assertEqual(attributes.experience_level("Senior Analyst", hint="Not Applicable"), "Senior")

    def test_job_type(self):
        self.assertEqual(attributes.job_type("FullTime"), "Full-time")
        self.assertEqual(attributes.job_type("Permanent"), "Full-time")
        self.assertEqual(attributes.job_type(["contract"]), "Contract")
        self.assertEqual(attributes.job_type("Short Term"), "Temporary")
        self.assertEqual(attributes.job_type(None, "Data Analyst Intern"), "Internship")
        self.assertEqual(attributes.job_type(None, "Contract Manager"), None)
        self.assertEqual(attributes.job_type(float("nan"), "Data Analyst (Contract)"), "Contract")

    def test_work_mode(self):
        self.assertEqual(attributes.work_mode("Hybrid", True), "Hybrid")
        self.assertEqual(attributes.work_mode("OnSite"), "On-site")
        self.assertEqual(attributes.work_mode(None, None, "Remote - India"), "Remote")
        self.assertEqual(attributes.work_mode("Work from office"), "On-site")
        self.assertEqual(attributes.work_mode(None, True, "Bengaluru"), "Remote")
        self.assertEqual(attributes.work_mode(None, None, "Bengaluru"), None)

    def test_salary(self):
        self.assertEqual(attributes.salary(207000, 285000, "USD", "1 YEAR"), "$207K–$285K / yr")
        self.assertEqual(attributes.salary(1200000, 1800000, "INR", "yearly"), "₹1.2M–₹1.8M / yr")
        self.assertEqual(attributes.salary(40, None, "USD", "hourly"), "$40 / hr")
        self.assertEqual(attributes.salary(float("nan"), None), None)


class JobPageTest(unittest.TestCase):
    def test_reads_open_postings_and_drops_expired(self):
        jobs = extract_jobs(JOB_PAGE, "https://acme.example/jobs/1", "Web search", today=date(2026, 11, 2))
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual((job.title, job.company, job.location, job.date_posted),
                         ("Senior Data Analyst", "Acme", "Bengaluru, IN", "2026-10-20"))
        self.assertEqual(job.url, "https://acme.example/jobs/1")


class AtsTest(unittest.TestCase):
    def test_parsers(self):
        with mock.patch.object(ats, "get_json", return_value=GREENHOUSE):
            gh = ats.fetch_greenhouse("stripe")
        with mock.patch.object(ats, "get_json", return_value=LEVER):
            lv = ats.fetch_lever("acme", "Acme")
        with mock.patch.object(ats, "get_json", return_value=ASHBY):
            ab = ats.fetch_ashby("acme")
        self.assertEqual(gh[0].date_posted, "2026-10-01")
        self.assertEqual(lv[0].company, "Acme")
        self.assertEqual(lv[0].date_posted, "2026-10-23")
        self.assertEqual(len(ab), 1)  # unlisted job skipped
        self.assertEqual(ab[0].location, "Remote; London")
        self.assertTrue(ab[0].remote)
        self.assertEqual((lv[0].experience, lv[0].job_type, lv[0].work_mode, lv[0].department),
                         ("Senior", "Full-time", "Hybrid", "Analytics"))
        self.assertIsNone(lv[0].remote)  # hybrid is not remote
        self.assertEqual((ab[0].job_type, ab[0].work_mode, ab[0].salary, ab[0].department),
                         ("Contract", "Remote", "$90K–$120K / yr", "Finance"))
        self.assertEqual(gh[0].work_mode, None)

    def test_search_boards_filters_and_survives_errors(self):
        def fake_get_json(url, params=None):
            if "greenhouse" in url:
                return GREENHOUSE
            raise ConnectionError("boom")

        boards = [{"name": "Stripe", "ats": "greenhouse", "slug": "stripe"},
                  {"name": "Broken", "ats": "lever", "slug": "broken"}]
        with mock.patch.object(ats, "get_json", side_effect=fake_get_json):
            jobs, errors = ats.search_boards(boards, SearchParams(query="data analyst", location="Bengaluru"))
        self.assertEqual([j.title for j in jobs], ["Data Analyst"])
        self.assertEqual(errors, ["Broken: boom"])


class BoardsTest(unittest.TestCase):
    def test_keeps_portal_link_and_company_link(self):
        import pandas as pd
        from jobfinder.sources import boards
        rows = pd.DataFrame([
            {"title": "Data Analyst", "company": "nVent", "location": "Bengaluru", "date_posted": "2026-11-08",
             "job_url": "https://www.naukri.com/job-listings-data-analyst-1",
             "job_url_direct": "https://nvent.wd5.myworkdayjobs.com/job/R1"},
            {"title": "Data Analyst", "company": "Acme", "location": "Pune", "date_posted": "2026-11-08",
             "job_url": "https://www.naukri.com/job-listings-data-analyst-2",
             "job_url_direct": "https://www.naukri.com/job-listings-data-analyst-2?src=apply"},  # same site
            {"title": "Data Analyst", "company": "Globex", "location": "Delhi", "date_posted": None,
             "job_url": "https://www.linkedin.com/jobs/view/3", "job_url_direct": None},
        ])
        with mock.patch.object(boards, "scrape_jobs", return_value=rows):
            jobs = boards.search("naukri", SearchParams(query="data analyst"))
        self.assertEqual([j.url for j in jobs], list(rows["job_url"]))
        self.assertEqual([j.company_url for j in jobs], ["https://nvent.wd5.myworkdayjobs.com/job/R1", None, None])


class WebSearchTest(unittest.TestCase):
    def test_board_from_url(self):
        self.assertEqual(websearch.board_from_url("https://jobs.lever.co/Acme/123"), {"ats": "lever", "slug": "acme"})
        self.assertEqual(websearch.board_from_url("https://job-boards.greenhouse.io/stripe/jobs/9"),
                         {"ats": "greenhouse", "slug": "stripe"})
        self.assertIsNone(websearch.board_from_url("https://example.com/careers"))

    def test_requires_configuration(self):
        with mock.patch.dict("os.environ", {"SEARXNG_URL": ""}):
            with self.assertRaises(RuntimeError):
                websearch.search(SearchParams(query="x"), [])


class StoreTest(unittest.TestCase):
    def test_mark_seen_reports_only_first_sighting(self):
        self.assertEqual(store.mark_seen(["https://x/1", "https://x/2"]), {"https://x/1", "https://x/2"})
        self.assertEqual(store.mark_seen(["https://x/2", "https://x/3"]), {"https://x/3"})

    def test_block_list(self):
        store.block_company("Acme Corp")
        self.assertIn("Acme Corp", store.blocked_companies())
        store.unblock_company("Acme Corp")
        self.assertNotIn("Acme Corp", store.blocked_companies())


class HttpCacheTest(unittest.TestCase):
    def test_revalidates_with_etag_and_reuses_body_on_304(self):
        first = mock.Mock(status_code=200, headers={"ETag": 'W/"v1"'}, content=b'{"jobs": [1]}')
        first.json.return_value = {"jobs": [1]}
        not_modified = mock.Mock(status_code=304, headers={}, content=b"")
        url = "https://boards.example/etag-test"
        with mock.patch.object(http._session, "get", side_effect=[first, not_modified]) as get:
            self.assertEqual(http.get_json(url), {"jobs": [1]})
            http._cache.clear()
            with mock.patch.object(http, "CACHE_SECONDS", 0):  # force a revalidation
                self.assertEqual(http.get_json(url), {"jobs": [1]})
        self.assertEqual(get.call_args_list[1].kwargs["headers"], {"If-None-Match": 'W/"v1"'})

    def test_serves_saved_copy_when_revalidation_fails(self):
        first = mock.Mock(status_code=200, headers={"ETag": '"v1"'}, content=b'{"ok": true}')
        first.json.return_value = {"ok": True}
        url = "https://boards.example/stale"
        with mock.patch.object(http._session, "get", side_effect=[first, http.requests.Timeout("stalled")]):
            http.get_json(url)
            http._cache.clear()
            with mock.patch.object(http, "CACHE_SECONDS", 0):
                self.assertEqual(http.get_json(url), {"ok": True})
        with mock.patch.object(http._session, "get", side_effect=http.requests.Timeout("stalled")):
            with self.assertRaises(http.requests.Timeout):  # nothing saved yet: the error is reported
                http.get_json("https://boards.example/never-fetched")

    def test_fresh_cache_skips_the_network(self):
        response = mock.Mock(status_code=200, headers={}, content=b"[]")
        response.json.return_value = []
        with mock.patch.object(http._session, "get", return_value=response) as get:
            http.get_json("https://boards.example/fresh")
            http.get_json("https://boards.example/fresh")
        self.assertEqual(get.call_count, 1)


class SearchTest(unittest.TestCase):
    def test_dedupe_keeps_first(self):
        a = Job("Data Analyst", "Stripe", "Bengaluru, India", "https://stripe.com/jobs/1", "Greenhouse")
        b = Job("Data Analyst", "Stripe", "Bengaluru, KA", "https://in.indeed.com/viewjob?jk=1", "Indeed")
        c = Job("Data Analyst", "Other", "Bengaluru", "https://stripe.com/jobs/1/", "LinkedIn")
        self.assertEqual(search_module.dedupe([a, b, c]), [a])

    def test_dedupe_catches_near_identical_titles(self):
        a = Job("Senior Python Developer", "Infosys", "Bengaluru, Karnataka, India", "https://a/1", "LinkedIn")
        b = Job("Sr. Python Developer", "Infosys Limited", "KA, India", "https://b/1", "Indeed")  # no city given
        c = Job("Sr Python Developer", "Infosys", "Bangalore", "https://c/1", "Naukri")  # alias of Bengaluru
        d = Job("Senior Python Developer", "Infosys", "Pune, MH, India", "https://d/1", "Naukri")  # other city
        e = Job("Python Developer Intern", "Infosys", "Bengaluru", "https://e/1", "Naukri")  # different role
        self.assertEqual(search_module.dedupe([a, b, c, d, e]), [a, d, e])

    def test_dedupe_keeps_separate_openings(self):
        # Same company board listing similar titles = separate openings.
        same_board = [Job("Software Engineer", "Stripe", "Seattle", f"https://stripe.com/jobs/{i}", "Greenhouse")
                      for i in range(3)]
        self.assertEqual(len(search_module.dedupe(same_board)), 3)
        # Different levels are different jobs, even across sites.
        ii = Job("Software Engineer II", "HP", "Bengaluru", "https://a/2", "LinkedIn")
        iii = Job("Software Engineer III", "HP", "Bengaluru", "https://b/3", "Indeed")
        senior = Job("Senior Software Engineer", "HP", "Bengaluru", "https://c/s", "Naukri")
        plain = Job("Software Engineer", "HP", "Bengaluru", "https://d/p", "Naukri")
        self.assertEqual(len(search_module.dedupe([ii, iii, senior, plain])), 4)

    def test_age_filter_block_list_new_flags_and_ranking(self):
        today = date.today()
        jobs = [
            Job("Data Analyst", "Acme", "Pune", "https://s/1", "Indeed", (today - timedelta(days=40)).isoformat()),
            Job("Senior Data Analyst", "Acme", "Pune", "https://s/2", "Indeed", (today - timedelta(days=2)).isoformat()),
            Job("Data Analyst", "Spammy Staffing", "Pune", "https://s/3", "Indeed", today.isoformat()),
            Job("Data Analyst", "Globex", "Pune", "https://s/4", "Indeed", None),
        ]
        store.block_company("Spammy Staffing Pvt Ltd")  # matches "Spammy Staffing" after normalizing
        store.mark_seen(["https://s/4"])
        with mock.patch.object(search_module.boards, "search", return_value=jobs), \
             mock.patch.object(search_module.ats, "load_watchlist", return_value=[]):
            result = search_module.search(SearchParams(query="data analyst", max_age_days=30), ["indeed"])
        store.unblock_company("Spammy Staffing Pvt Ltd")

        self.assertEqual([j["url"] for j in result["jobs"]], ["https://s/2", "https://s/4"])
        self.assertEqual((result["too_old"], result["hidden_companies"]), (1, 1))
        self.assertEqual([j["is_new"] for j in result["jobs"]], [True, False])
        self.assertGreater(result["jobs"][0]["score"], result["jobs"][1]["score"])

    def test_failing_source_is_reported_not_raised(self):
        good = [Job("Data Analyst", "Acme", "Pune", "https://a/1", "Indeed", "2026-10-01"),
                Job("Business Analyst", "Acme", "Pune", "https://a/2", "Indeed", "2026-10-02")]

        def fake_board_search(site, params):
            if site == "linkedin":
                raise RuntimeError("rate-limited")
            return good

        with mock.patch.object(search_module.boards, "search", side_effect=fake_board_search), \
             mock.patch.object(search_module.ats, "load_watchlist", return_value=[]):
            result = search_module.search(SearchParams(query="data analyst"), ["indeed", "linkedin"])

        self.assertEqual([j["title"] for j in result["jobs"]], ["Data Analyst"])  # exact title filter
        by_key = {s["key"]: s for s in result["sources"]}
        self.assertEqual(by_key["linkedin"]["error"], "rate-limited")
        self.assertEqual(by_key["indeed"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
