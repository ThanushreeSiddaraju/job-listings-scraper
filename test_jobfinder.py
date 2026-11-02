import unittest
from datetime import date
from unittest import mock

from jobfinder import search as search_module
from jobfinder.jobpage import extract_jobs
from jobfinder.matching import location_matches, title_matches
from jobfinder.models import Job, SearchParams
from jobfinder.sources import ats, websearch

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
     "categories": {"location": "Bangalore"}, "createdAt": 1792767640768, "workplaceType": "hybrid"},
]
ASHBY = {"jobs": [
    {"title": "Data Analyst", "jobUrl": "https://jobs.ashbyhq.com/acme/1", "location": "Remote",
     "secondaryLocations": [{"location": "London"}], "publishedAt": "2026-09-01T00:00:00Z",
     "isRemote": True, "isListed": True},
    {"title": "Data Analyst (unlisted)", "jobUrl": "https://jobs.ashbyhq.com/acme/2", "location": "Remote",
     "isListed": False},
]}


class MatchingTest(unittest.TestCase):
    def test_title_needs_every_keyword(self):
        self.assertTrue(title_matches("Senior Data Analyst", "data analyst"))
        self.assertTrue(title_matches("Analyst, Data Platform", "data analyst jobs"))
        self.assertFalse(title_matches("Business Analyst", "data analyst"))
        self.assertTrue(title_matches("C++ Developer", "c++ developer"))

    def test_location_aliases_and_remote(self):
        self.assertTrue(location_matches("Bangalore, KA", "Bengaluru"))
        self.assertTrue(location_matches(None, "Pune", remote=True))
        self.assertFalse(location_matches("Mumbai", "Pune"))
        self.assertTrue(location_matches("Anywhere", ""))


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


class SearchTest(unittest.TestCase):
    def test_dedupe_keeps_first(self):
        a = Job("Data Analyst", "Stripe", "Bengaluru, India", "https://stripe.com/jobs/1", "Greenhouse")
        b = Job("Data Analyst", "Stripe", "Bengaluru, KA", "https://in.indeed.com/viewjob?jk=1", "Indeed")
        c = Job("Data Analyst", "Other", "Bengaluru", "https://stripe.com/jobs/1/", "LinkedIn")
        self.assertEqual(search_module.dedupe([a, b, c]), [a])

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
