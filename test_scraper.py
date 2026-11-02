import unittest

from scraper import parse_jobs

SAMPLE = """
<div class="card">
  <h2 class="title is-5">Senior Python Developer</h2>
  <h3 class="subtitle is-6 company">Payne, Roberts and Davis</h3>
  <p class="location">
    Stewartbury, AA
  </p>
  <time datetime="2021-04-08">2021-04-08</time>
  <footer>
    <a href="https://www.realpython.com">Learn</a>
    <a href="jobs/senior-python-developer-0.html">Apply</a>
  </footer>
</div>
<div class="card">
  <h2 class="title is-5">Energy engineer</h2>
</div>
<div class="card">
  <h3 class="company">No Title Inc</h3>
</div>
"""


class ParseJobsTest(unittest.TestCase):
    def setUp(self):
        self.jobs = parse_jobs(SAMPLE, "https://realpython.github.io/fake-jobs/")

    def test_full_card(self):
        self.assertEqual(
            self.jobs[0],
            {
                "title": "Senior Python Developer",
                "company": "Payne, Roberts and Davis",
                "location": "Stewartbury, AA",
                "date_posted": "2021-04-08",
                "url": "https://realpython.github.io/fake-jobs/jobs/senior-python-developer-0.html",
            },
        )

    def test_missing_fields_become_none(self):
        job = self.jobs[1]
        self.assertEqual(job["title"], "Energy engineer")
        self.assertIsNone(job["company"])
        self.assertIsNone(job["location"])
        self.assertIsNone(job["url"])

    def test_card_without_title_is_skipped(self):
        self.assertEqual(len(self.jobs), 2)


if __name__ == "__main__":
    unittest.main()
