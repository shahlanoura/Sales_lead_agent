from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from apps.agents.schemas import GoalOut
from apps.core.models import Campaign


def fake_goal(**over):
    base = dict(campaign_name="US Startups - AI", target_industry="SaaS", target_country="United States",
                target_company_size="early-stage startup", target_services=["ai solutions and ai agent development", "Made Up Service"],
                target_lead_count=50, ideal_customer_profile="Startups that could automate support.",
                assumptions=[], clarifying_questions=[])
    base.update(over)
    return GoalOut(**base)


class FakeLLM:
    def __init__(self, out): self.out = out
    def invoke(self, _): return self.out


class GoalTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_superuser("a", "a@a.com", "x"))

    def post(self, out, **body):
        with patch("apps.agents.goals.structured", return_value=FakeLLM(out)):
            return self.client.post("/api/campaigns/from-goal/", {"goal": "Find 50 US startups that may need AI development services", **body}, format="json")

    def test_creates_draft_campaign(self):
        r = self.post(fake_goal())
        self.assertEqual(r.status_code, 201)
        c = Campaign.objects.get()
        self.assertEqual(c.campaign_status, "draft")
        self.assertEqual(c.target_lead_count, 50)
        self.assertEqual(c.target_services, ["AI Solutions and AI Agent Development"])  # invalid service dropped, name normalised

    def test_default_and_cap_lead_count(self):
        self.post(fake_goal(target_lead_count=0))
        self.assertEqual(Campaign.objects.get().target_lead_count, 50)
        Campaign.objects.all().delete()
        r = self.post(fake_goal(target_lead_count=9999))
        self.assertEqual(Campaign.objects.get().target_lead_count, 500)
        self.assertTrue(any("capped" in a for a in r.data["campaign"]["goal_assumptions"]))

    def test_preview_creates_nothing(self):
        r = self.post(fake_goal(), create=False)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Campaign.objects.count(), 0)

    def test_short_goal_rejected(self):
        r = self.client.post("/api/campaigns/from-goal/", {"goal": "leads"}, format="json")
        self.assertEqual(r.status_code, 400)
