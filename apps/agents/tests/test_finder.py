import re
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from apps.agents import finder
from apps.agents.schemas import QueryPlan, Verdict, VerdictBatch
from apps.core.models import Campaign, FinderRun, Lead, OptOut


class FakeLLM:
    counter = 0

    def __init__(self, schema, empty_plan=False):
        self.schema, self.empty_plan = schema, empty_plan

    def invoke(self, msgs):
        if self.schema is QueryPlan:
            FakeLLM.counter += 1
            return QueryPlan(queries=[] if self.empty_plan else [f"query {FakeLLM.counter}-{i}" for i in range(2)])
        text = msgs[1][1]
        verdicts = []
        for idx, url in re.findall(r"\[(\d+)\] (\S+)", text):
            verdicts.append(Verdict(index=int(idx), is_single_company_site="article" not in url,
                                    fits_campaign="reject" not in url, company_name=f"Co {idx}",
                                    industry="SaaS", description="Does things."))
        return VerdictBatch(verdicts=verdicts)


def results(*domains):
    return [{"url": f"https://{d}/", "title": d, "content": "snippet"} for d in domains]


@override_settings(TAVILY_API_KEY="tvly-test", FINDER_MAX_ROUNDS=4, FINDER_QUERIES_PER_ROUND=2, FINDER_RESULTS_PER_QUERY=10)
class FinderTests(TestCase):
    def setUp(self):
        FakeLLM.counter = 0
        self.c = Campaign.objects.create(campaign_name="t", target_lead_count=3, target_country="US")
        p = patch("apps.agents.finder.structured", side_effect=lambda s: FakeLLM(s))
        p.start()
        self.addCleanup(p.stop)

    def run_finder(self, search_side_effect):
        with patch("apps.agents.finder.tavily_search", side_effect=search_side_effect):
            return finder.find_leads(self.c.id)

    def test_stops_at_target(self):
        s = self.run_finder(lambda q, n: results(*[f"{q.replace(' ', '')}-{i}.com" for i in range(5)]))
        self.assertEqual(Lead.objects.count(), 3)
        self.assertEqual(s["status"], "done")
        self.assertEqual(s["leads_created"], 3)

    def test_filters_junk_dupes_optouts_and_rejects(self):
        Lead.objects.create(campaign=self.c, company_name="Old", website="https://old.com", domain="old.com")
        OptOut.objects.create(email="x@optout.com")
        self.c.target_lead_count = 10
        self.c.save()
        batch = results("linkedin.com", "old.com", "optout.com", "reject-me.com", "news-article.com", "good1.com", "www.good2.com")
        self.run_finder(lambda q, n: batch)
        domains = set(Lead.objects.values_list("domain", flat=True))
        self.assertEqual(domains, {"old.com", "good1.com", "good2.com"})  # www stripped, junk/optout/rejected/article excluded

    def test_missing_key_gives_clear_error(self):
        with override_settings(TAVILY_API_KEY=""):
            s = finder.find_leads(self.c.id)
        self.assertEqual(s["status"], "error")
        self.assertIn("TAVILY_API_KEY", s["detail"])
        self.assertEqual(FinderRun.objects.get().status, "error")

    def test_no_new_queries_ends_run(self):
        with patch("apps.agents.finder.structured", side_effect=lambda s: FakeLLM(s, empty_plan=True)):
            s = self.run_finder(lambda q, n: [])
        self.assertEqual(s["status"], "done")
        self.assertEqual(s["leads_created"], 0)

    def test_rerun_does_not_repeat_queries(self):
        self.run_finder(lambda q, n: [])
        self.run_finder(lambda q, n: [])
        first, second = [set(r.queries) for r in FinderRun.objects.order_by("id")]
        self.assertTrue(first and second)
        self.assertTrue(first.isdisjoint(second))


class TavilyClientTests(TestCase):
    @override_settings(TAVILY_API_KEY="tvly-test")
    def test_error_handling(self):
        def resp(code, body=None):
            m = MagicMock(status_code=code, text="x")
            m.json.return_value = body or {}
            return m
        with patch("apps.agents.finder.requests.post", return_value=resp(401)):
            with self.assertRaises(finder.FinderError):
                finder.tavily_search("q", 5)
        with patch("apps.agents.finder.requests.post", return_value=resp(432)):
            with self.assertRaises(finder.FinderError):
                finder.tavily_search("q", 5)
        with patch("apps.agents.finder.requests.post", return_value=resp(500)):
            self.assertEqual(finder.tavily_search("q", 5), [])
        with patch("apps.agents.finder.requests.post", return_value=resp(200, {"results": [{"url": "https://a.com"}]})) as post:
            self.assertEqual(len(finder.tavily_search("q", 5)), 1)
            self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer tvly-test")