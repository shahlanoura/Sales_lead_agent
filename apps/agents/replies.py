"""Steps 8 and 9 - understand a prospect's reply, update the lead, and hand it to a BDE.
Nothing here sends email: the suggested reply is a draft for a human."""
import re
from typing import Literal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from pydantic import BaseModel

from apps.core.models import (CommunicationLog, Handoff, InvalidTransition, Lead, LeadStatus, OptOut, Reply)

from .llm import structured as default_structured

UNSUB = re.compile(r"\b(unsubscribe|remove me|stop (emailing|contacting|sending|writing)|do not (email|contact)|"
                   r"don'?t (email|contact)|opt[- ]?out|take me off)\b", re.I)
AUTO = re.compile(r"\b(out of (the )?office|automatic reply|auto-?reply|autoreply|on (annual )?leave|on vacation)\b", re.I)
ACTIVE_LOAD = ["responded", "interested", "meeting_scheduled"]


class ReplyOut(BaseModel):
    classification: Literal["interested", "question", "objection", "not_interested", "unsubscribe", "auto_reply", "unclear"]
    summary: str
    wants_meeting: bool = False
    objection: str = ""
    questions: list[str] = []
    suggested_reply: str = ""


def classify(reply: Reply, llm=default_structured) -> ReplyOut:
    body = reply.body
    if UNSUB.search(body):  # deterministic: honour opt-outs without depending on a model
        return ReplyOut(classification="unsubscribe", summary="The prospect asked not to be contacted again.")
    if AUTO.search(body) and len(body) < 700:
        return ReplyOut(classification="auto_reply", summary="Automatic reply (out of office); not a real response.")
    sent = reply.lead.outreach.filter(sent_status="sent").order_by("-sent_at").first()
    sender = getattr(settings, "SENDER_NAME", "The Webfolks team")
    return llm(ReplyOut).invoke([
        ("system", (
            "You help a sales team at Webfolks (an IT services company) read prospect replies. Classify the reply: "
            "interested (wants to talk or learn more), question (asks something before deciding), objection (raises a "
            "concern: price, timing, already has a vendor, not a priority), not_interested (clear no), unsubscribe, "
            "auto_reply, unclear. Summarise in 1-2 sentences. List any questions asked and the objection if any. "
            "wants_meeting = true only if they ask for or agree to a call/meeting. Write suggested_reply as a short, "
            "honest, plain-English draft for a human to review: answer only from what the original email says; if you "
            "do not know something, say you will find out; never invent prices, timelines, results or customers; do not "
            f"pressure. Sign off exactly as: {sender}. Leave suggested_reply empty for unsubscribe, auto_reply or "
            "not_interested.")),
        ("human", f"Our email (subject: {sent.subject if sent else 'n/a'}):\n{sent.message_body if sent else 'n/a'}\n\n"
                  f"Prospect's reply:\n{body[:3000]}"),
    ])


def analyze(reply: Reply, llm=default_structured) -> Reply:
    out = classify(reply, llm)
    reply.classification, reply.summary, reply.suggested_reply = out.classification, out.summary, out.suggested_reply
    reply.analysis = {"wants_meeting": out.wants_meeting, "objection": out.objection, "questions": out.questions}
    reply.analyzed_at = timezone.now()
    reply.save()
    return reply


def pick_bde():
    """Least-busy active BDE (role 'bde' on the user's Profile); None if nobody has that role yet."""
    bdes = list(get_user_model().objects.filter(profile__role="bde", is_active=True).order_by("id"))
    if not bdes:
        return None
    load = {u.id: Lead.objects.filter(assigned_to=u, status__in=ACTIVE_LOAD).count() for u in bdes}
    return min(bdes, key=lambda u: (load[u.id], u.id))


def _advance(lead: Lead, *targets):
    for t in targets:
        try:
            lead.transition_to(t)
        except InvalidTransition:
            pass


def build_summary(lead: Lead, reply: Reply, assignee) -> str:
    report = lead.reports.order_by("-created_at").first()
    sent = lead.outreach.filter(sent_status="sent").order_by("-sent_at").first()
    contacts = "; ".join(f"{c.contact_name or 'unnamed'} <{c.email or 'no email'}> {c.job_title}".strip()
                         for c in lead.contacts.all()) or "none on file"
    a = reply.analysis or {}
    lines = [
        f"HANDOFF: {lead.company_name} ({lead.website})",
        f"Score: {lead.lead_score} ({lead.priority}) | Industry: {lead.industry or '-'} | Country: {lead.country or '-'}",
        f"Assigned to: {assignee or 'nobody yet (no user has the BDE role)'}",
        "", f"REPLY TYPE: {reply.classification}", f"Summary: {reply.summary}",
    ]
    if a.get("questions"):
        lines.append("Questions asked: " + " | ".join(a["questions"]))
    if a.get("objection"):
        lines.append(f"Objection: {a['objection']}")
    if a.get("wants_meeting"):
        lines.append("They want a call or meeting.")
    lines += ["", "Their reply:", reply.body[:1500], "", "Suggested reply (review before sending):", reply.suggested_reply or "(none)"]
    if sent:
        lines += ["", f"OUR OUTREACH (sent {sent.sent_at:%d %b %Y}): {sent.subject}",
                  f"Follow-ups sent: {lead.followups.filter(status='sent').count()}"]
    if report:
        lines += ["", "RESEARCH: " + report.business_summary]
        for o in (report.identified_opportunities or [])[:3]:
            lines.append(f"- {o.get('service', '')}: {o.get('rationale', '')}")
    lines += ["", "CONTACTS: " + contacts]
    return "\n".join(lines)


def create_handoff(lead: Lead, reply: Reply) -> Handoff:
    assignee = lead.assigned_to or pick_bde()  # keep the same BDE if the lead already has one
    if assignee and lead.assigned_to_id != assignee.id:
        lead.assigned_to = assignee
        lead.save(update_fields=["assigned_to"])
    return Handoff.objects.create(lead=lead, assigned_to=assignee, reply=reply,
                                  summary=build_summary(lead, reply, assignee), suggested_reply=reply.suggested_reply)


def apply(reply: Reply) -> str:
    """Update the lead from an analysed reply. Returns a short description of what happened."""
    if reply.applied:
        return "already applied"
    lead, c = reply.lead, reply.classification
    CommunicationLog.objects.create(lead=lead, direction="in", message_content=reply.body, response_status=c)
    reply.applied = True
    reply.save(update_fields=["applied"])
    if c == "auto_reply":
        return "auto-reply ignored; follow-ups continue"
    if c == "unsubscribe":
        emails = set(lead.contacts.exclude(email="").values_list("email", flat=True))
        if reply.from_email:
            emails.add(reply.from_email)
        for e in emails:
            OptOut.objects.get_or_create(email=e.lower(), defaults={"reason": "replied: unsubscribe"})
        lead.transition_to(LeadStatus.OPTED_OUT)
        return "opted out; no further contact"
    if c == "not_interested":
        _advance(lead, LeadStatus.RESPONDED, LeadStatus.NOT_INTERESTED)
        return "marked not interested; follow-ups stopped"
    if c == "interested" or reply.analysis.get("wants_meeting"):
        _advance(lead, LeadStatus.RESPONDED, LeadStatus.INTERESTED)
    else:  # question / objection / unclear: a human must answer
        _advance(lead, LeadStatus.RESPONDED)
    lead.refresh_from_db()
    h = create_handoff(lead, reply)
    return f"handed off to {h.assigned_to or 'nobody (no BDE users yet)'}"


def process(reply: Reply, llm=default_structured) -> str:
    if not reply.analyzed_at:
        analyze(reply, llm)
    return apply(reply)