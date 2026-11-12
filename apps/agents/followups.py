import re

from django.conf import settings
from django.utils import timezone

from apps.core.models import CommunicationLog, FollowUp, LeadStatus

from . import email as mail
from .llm import get_llm


FOLLOWUP_GUIDE = {
    1: ("Add ONE new, concrete angle that was not in the first email, for example what the idea would let their team "
        "do, framed as an easy question. 35-60 words."),
    2: "Be shorter (under 40 words). Offer something easy: a 15-minute call, or a one-page outline you can send. 25-40 words.",
    3: "This is the LAST message. Say so politely, say you will not follow up again, and leave the door open. Under 35 words.",
}


def draft_followup(fu: FollowUp) -> str:
    n = list(fu.outreach.followups.order_by("scheduled_at").values_list("id", flat=True)).index(fu.id) + 1
    contact = fu.lead.primary_contact()
    first = contact.contact_name.split()[0] if contact and contact.contact_name else ""
    salutation = f"Hi {first}," if first else "Hi there,"
    sender = getattr(settings, "SENDER_NAME", "The Webfolks team")
    prompt = (
        "Write the MIDDLE paragraph of a follow-up email from Webfolks to a company that has not replied to the "
        "email below. Output ONLY that paragraph: no greeting, no sign-off, no subject. "
        f"{FOLLOWUP_GUIDE.get(n, FOLLOWUP_GUIDE[3])} "
        "Rules: plain English; never use the phrases 'just checking in', 'circling back', 'bumping this' or "
        "'touching base'; no pressure or fake urgency; no invented facts, numbers, customers or results; "
        f"do not repeat the first email's wording.\n\nFirst email subject: {fu.outreach.subject}\n\n{fu.outreach.message_body}"
    )
    middle = get_llm(0.4).invoke(prompt).content.strip()
    middle = re.sub(r"^(hi|hello|dear)\b[^\n]*,\s*\n+", "", middle, flags=re.I).strip()  # drop a stray greeting
    return f"{salutation}\n\n{middle}\n\n{sender}"


def send_followup(fu: FollowUp) -> bool:
    lead = fu.lead
    contact = lead.primary_contact()  # re-checks opt-outs at send time
    if not lead.is_contactable or not contact:
        fu.status = "cancelled"
        fu.save(update_fields=["status"])
        return False
    mail.send_email(contact.email, "Re: " + fu.outreach.subject, fu.message_body, fu.outreach.gmail_thread_id)
    fu.status, fu.sent_at = "sent", timezone.now()
    fu.save(update_fields=["status", "sent_at"])
    CommunicationLog.objects.create(lead=lead, direction="out", message_content=fu.message_body, response_status="followup_sent")
    lead.transition_to(LeadStatus.CONTACTED)
    return True


def process_due():
    now = timezone.now()
    handled = 0
    for fu in FollowUp.objects.filter(status="pending", scheduled_at__lte=now).select_related("lead", "outreach"):
        lead = fu.lead
        if not lead.is_contactable or not lead.primary_contact():
            fu.status = "cancelled"
            fu.save(update_fields=["status"])
            continue
        if not fu.message_body:
            fu.message_body = draft_followup(fu)
        if settings.FOLLOWUP_AUTOSEND:  # Level 3 (controlled automation)
            fu.save(update_fields=["message_body"])
            send_followup(fu)
        else:                           # Level 2 (human-approved) - default
            fu.status = "awaiting_approval"
            fu.save(update_fields=["message_body", "status"])
            lead.transition_to(LeadStatus.FOLLOWUP_DUE)
        handled += 1
    return handled