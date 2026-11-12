from django.test import TestCase

from apps.agents.scoring import compute_score, priority_for
from apps.core.discovery import import_leads_rows
from apps.core.models import (Campaign, FollowUp, InvalidTransition, Lead, LeadStatus,
                              OutreachMessage, schedule_followups)


class ScoringTests(TestCase):
    def test_weights_sum_to_100(self):
        full = {k: 1 for k in ("industry_relevance", "business_fit", "identified_requirement", "service_relevance")}
        self.assertEqual(compute_score(full, True), 100)
        self.assertEqual(compute_score(full, False), 90)

    def test_priority_bands(self):
        self.assertEqual([priority_for(x) for x in (85, 80, 79, 60, 59)], ["high", "high", "medium", "medium", "low"])


class LeadFlowTests(TestCase):
    def setUp(self):
        self.c = Campaign.objects.create(campaign_name="t")
        import_leads_rows(self.c, [{"company_name": "A", "website": "https://www.a.com"}])
        self.lead = Lead.objects.get()

    def test_dedupe(self):
        res = import_leads_rows(self.c, [{"company_name": "A2", "website": "a.com"}])
        self.assertEqual(res["created"], 0)
        self.assertEqual(Lead.objects.count(), 1)

    def test_invalid_transition(self):
        with self.assertRaises(InvalidTransition):
            self.lead.transition_to(LeadStatus.CONTACTED)

    def test_reply_cancels_followups(self):
        om = OutreachMessage.objects.create(lead=self.lead, subject="s", message_body="b")
        for s in (LeadStatus.RESEARCHING, LeadStatus.QUALIFIED, LeadStatus.NEEDS_REVIEW, LeadStatus.APPROVED, LeadStatus.CONTACTED):
            self.lead.transition_to(s)
        schedule_followups(self.lead, om)
        self.assertEqual(FollowUp.objects.filter(status="pending").count(), 3)
        self.lead.transition_to(LeadStatus.RESPONDED)
        self.assertEqual(FollowUp.objects.filter(status="pending").count(), 0)

    def test_opt_out_from_any_state(self):
        self.lead.transition_to(LeadStatus.OPTED_OUT)
        self.assertFalse(self.lead.is_contactable)
