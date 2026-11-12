import base64
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.agents import email as mail
from apps.agents import tasks
from apps.core.models import (Campaign, Contact, FollowUp, Lead, LeadStatus, OptOut, OutreachMessage, Reply,
                              schedule_followups)


def b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode()


class TextHelpersTests(TestCase):
    def test_extract_prefers_plain_text_in_multipart(self):
        payload = {"mimeType": "multipart/alternative", "parts": [
            {"mimeType": "text/html", "body": {"data": b64("<p>html</p>")}},
            {"mimeType": "text/plain", "body": {"data": b64("plain text")}}]}
        self.assertEqual(mail.extract_text(payload), "plain text")

    def test_extract_falls_back_to_html(self):
        payload = {"mimeType": "text/html", "body": {"data": b64("<p>Hello <b>there</b></p>")}}
        self.assertIn("Hello", mail.extract_text(payload))
        self.assertNotIn("<p>", mail.extract_text(payload))

    def test_quoted_history_is_cut(self):
        text = "Sounds good, call Tuesday?\n\nOn Tue, 11 Nov 2026, Webfolks wrote:\n> Hi there\n> original email"
        self.assertEqual(mail.clean_reply_text(text), "Sounds good, call Tuesday?")


class SendSafetyTests(TestCase):
    @override_settings(EMAIL_PROVIDER="gmail", COMPANY_ADDRESS="")
    def test_real_sending_refused_without_postal_address(self):
        with self.assertRaises(RuntimeError) as cm:
            mail.send_email("x@a.com", "s", "b")
        self.assertIn("COMPANY_ADDRESS", str(cm.exception))

    @override_settings(EMAIL_PROVIDER="console", COMPANY_NAME="Webfolks", COMPANY_ADDRESS="1 Main St, Kochi, India")
    def test_footer_has_address_and_optout(self):
        f = mail.footer()
        self.assertIn("1 Main St, Kochi, India", f)
        self.assertIn("unsubscribe", f)


@override_settings(EMAIL_PROVIDER="gmail")
class PollRepliesTests(TestCase):
    def setUp(self):
        c = Campaign.objects.create(campaign_name="t")
        self.lead = Lead.objects.create(campaign=c, company_name="A", website="https://a.com", domain="a.com")
        Contact.objects.create(lead=self.lead, email="sales@a.com")
        self.om = OutreachMessage.objects.create(lead=self.lead, subject="s", message_body="b", sent_status="sent",
                                                 sent_at="2026-11-11T10:00:00Z", gmail_thread_id="T1")
        for s in (LeadStatus.RESEARCHING, LeadStatus.QUALIFIED, LeadStatus.NEEDS_REVIEW, LeadStatus.APPROVED, LeadStatus.CONTACTED):
            self.lead.transition_to(s)
        schedule_followups(self.lead, self.om)

    def fake_mailbox(self, messages):
        def fn(thread_id, known_ids):
            return [m for m in messages if m["id"] not in known_ids]
        return patch("apps.agents.tasks.mail.list_new_replies", side_effect=fn)

    def msg(self, id, body, sender="boss@a.com", bounce=False):
        return {"id": id, "from": sender, "subject": "Re", "body": body, "raw": body, "bounce": bounce}

    def test_unsubscribe_reply_opts_out_and_is_not_stored_twice(self):
        with self.fake_mailbox([self.msg("m1", "Please unsubscribe me")]):
            self.assertEqual(tasks.poll_replies(), 1)
            self.assertEqual(tasks.poll_replies(), 0)  # same message again: ignored
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "opted_out")
        self.assertEqual(Reply.objects.count(), 1)
        self.assertTrue(OptOut.objects.filter(email="sales@a.com").exists())
        self.assertEqual(FollowUp.objects.filter(status="pending").count(), 0)

    def test_bounce_marks_address_unusable(self):
        bounce = self.msg("b1", "Delivery failed: sales@a.com does not exist", sender="mailer-daemon@googlemail.com", bounce=True)
        with self.fake_mailbox([bounce]):
            tasks.poll_replies()
        self.assertEqual(Contact.objects.get().verification_status, "bounced")
        self.assertIsNone(self.lead.primary_contact())
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "contacted")  # a bounce is not a reply

    def test_unprocessed_reply_is_retried_on_next_poll(self):
        r = Reply.objects.create(lead=self.lead, body="Remove me from this list", gmail_message_id="old")
        with self.fake_mailbox([]):
            tasks.poll_replies()
        r.refresh_from_db()
        self.assertTrue(r.applied)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "opted_out")