"""End-to-end graph test with a fake LLM and fake website: research -> qualify -> draft -> PAUSE -> approve -> send -> follow-ups."""
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.agents import nodes, tasks
from apps.agents.scraper import Site
from apps.agents.schemas import DraftOut, QualificationOut, ResearchOut
from apps.core.discovery import import_leads_rows
from apps.core.models import Campaign, FollowUp, Lead, OutreachMessage


class FakeStructured:
    def __init__(self, schema):
        self.schema = schema

    def invoke(self, _msgs):
        if self.schema is ResearchOut:
            return ResearchOut(business_summary="SaaS for clinics", opportunities=[], relevant_services=["Mobile Application Development"])
        if self.schema is QualificationOut:
            return QualificationOut(industry_relevance=1, business_fit=1, identified_requirement=0.8, service_relevance=1, reasoning="ok")
        return DraftOut(subject="Hello", body="Hi there")


FAKE_SITE = Site(text="x" * 2000, urls=["https://acme.com", "https://acme.com/about"],
                 emails={"hello@acme.com": "https://acme.com/contact"})


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, EMAIL_PROVIDER="console")
class GraphTests(TestCase):
    @patch("apps.agents.nodes.fetch_site", return_value=FAKE_SITE)
    @patch("apps.agents.nodes.structured", side_effect=lambda s: FakeStructured(s))
    def test_full_flow(self, _s, _f):
        c = Campaign.objects.create(campaign_name="t")
        import_leads_rows(c, [{"company_name": "Acme", "website": "https://acme.com"}])
        lead = Lead.objects.get()

        tasks.run_lead_pipeline(lead.id)
        lead.refresh_from_db()
        self.assertEqual(lead.status, "needs_review")      # paused at the approval gate
        self.assertEqual(lead.lead_score, 94)               # 20+20+24+20+10, computed in code
        om = OutreachMessage.objects.get()
        self.assertEqual(om.sent_status, "unsent")           # nothing is sent before approval

        tasks.resume_lead_pipeline(lead.id, {"approved": True, "approver_id": None, "subject": None, "body": "Edited body"})
        lead.refresh_from_db(); om.refresh_from_db()
        self.assertEqual(lead.status, "contacted")
        self.assertEqual(om.sent_status, "sent")
        self.assertEqual(om.message_body, "Edited body")
        self.assertEqual(FollowUp.objects.filter(status="pending").count(), 3)
