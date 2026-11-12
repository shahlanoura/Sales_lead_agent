import json
import logging
from functools import wraps

from django.conf import settings
from django.utils import timezone
from langgraph.types import interrupt

from apps.core.models import (AgentRun, CommunicationLog, Contact, Lead, LeadStatus,
                              OutreachMessage, ResearchReport, schedule_followups)

from . import email as mail
from . import people as people_mod
from .llm import structured
from .schemas import DraftOut, QualificationOut, ResearchOut
from .scoring import compute_score, priority_for
from .scraper import fetch_site

SYSTEM_RULES = (
    "You work for Webfolks, an IT services company. Use ONLY the provided website text. "
    "Never invent facts, names, emails or customer needs. Anything about needs is a *potential* "
    "opportunity, not a confirmed requirement."
)


def logged(name):
    def deco(fn):
        @wraps(fn)
        def wrapper(state):
            run = AgentRun.objects.create(lead_id=state["lead_id"], node=name)
            try:
                out = fn(state)
                run.status = "ok"
                run.save(update_fields=["status"])
                return out
            except Exception as e:  # recorded, then re-raised so RetryPolicy can act
                run.status, run.detail = "error", str(e)[:2000]
                run.save(update_fields=["status", "detail"])
                raise
        return wrapper
    return deco


def _services(lead):
    return lead.campaign.target_services or settings.WEBFOLKS_SERVICES


@logged("research")
def research_node(state):
    lead = Lead.objects.select_related("campaign").get(id=state["lead_id"])
    if lead.status != LeadStatus.QUALIFIED:  # a lead that stopped after qualifying can simply be re-run
        lead.transition_to(LeadStatus.RESEARCHING)
    site = fetch_site(lead.website, lead.domain)
    state_update = {"research_ok": bool(site.text)}
    if not site.text:
        ResearchReport.objects.create(lead=lead, business_summary="Website could not be fetched (blocked, down or disallowed by robots.txt).",
                                      research_confidence=0)
        return state_update
    out = structured(ResearchOut).invoke([
        ("system", SYSTEM_RULES),
        ("human", f"Company: {lead.company_name}\nIndustry hint: {lead.industry}\n"
                  f"Webfolks services: {', '.join(_services(lead))}\n\nWebsite text:\n{site.text}\n\n"
                  "Summarise the business, its model, technology observations, and potential Webfolks opportunities."),
    ])
    confidence = 0.8 if len(site.urls) >= 2 and len(site.text) > 1500 else 0.5
    ResearchReport.objects.create(
        lead=lead, business_summary=out.business_summary,
        identified_opportunities=[o.model_dump() for o in out.opportunities],
        relevant_services=out.relevant_services,
        evidence_sources=site.urls, research_confidence=confidence,
    )
    for email, src in site.emails.items():  # only emails actually found; never guessed
        Contact.objects.get_or_create(lead=lead, email=email,
                                      defaults=dict(verification_status="found_on_site", source_url=src))
    try:  # step 4: named decision-makers from the company's own pages
        people_mod.store_people(lead, people_mod.extract_people(site.text, structured))
    except Exception as e:
        logging.getLogger(__name__).warning("People extraction failed for %s: %s", lead.domain, e)
    return state_update


def route_after_research(state):
    return "qualify" if state.get("research_ok") else "manual"


@logged("manual_review")
def manual_review_node(state):
    lead = Lead.objects.get(id=state["lead_id"])
    lead.transition_to(LeadStatus.NEEDS_REVIEW)  # BDE handles sites we couldn't read
    return {}


@logged("qualify")
def qualify_node(state):
    lead = Lead.objects.select_related("campaign").get(id=state["lead_id"])
    report = lead.reports.latest("created_at")
    out = structured(QualificationOut).invoke([
        ("system", SYSTEM_RULES),
        ("human", f"Campaign target: industry={lead.campaign.target_industry}, country={lead.campaign.target_country}, "
                  f"size={lead.campaign.target_company_size}, services={_services(lead)}\n"
                  f"Ideal customer profile: {lead.campaign.ideal_customer_profile or 'not specified'}\n"
                  f"Company: {lead.company_name} ({lead.industry}, {lead.country})\n"
                  f"Research: {report.business_summary}\nOpportunities: {json.dumps(report.identified_opportunities)}\n\n"
                  "Score each criterion from 0 to 1 and explain briefly. RULES: if the research shows the company "
                  "ALREADY ships the capability we would sell (for example it already has AI features, when we sell AI "
                  "development), give identified_requirement and business_fit at most 0.4. Reward only a concrete, plausible "
                  "gap that the research supports; do not reward generic potential."),
    ])
    score = compute_score(out.model_dump(), has_contact=lead.primary_contact() is not None)
    lead.lead_score, lead.priority = score, priority_for(score)
    lead.save(update_fields=["lead_score", "priority"])
    lead.transition_to(LeadStatus.QUALIFIED)
    exclude = lead.priority == "low" and lead.campaign.exclude_low_priority
    if exclude:
        lead.transition_to(LeadStatus.DISQUALIFIED)
    return {"score": score, "priority": lead.priority, "exclude": exclude}


def route_after_qualify(state):
    return "end" if state.get("exclude") else "draft"


@logged("draft")
def draft_node(state):
    lead = Lead.objects.select_related("campaign").get(id=state["lead_id"])
    existing = lead.outreach.filter(approval_status="pending").first()
    if existing:  # reuse the pending draft, but the lead must still go back to 'needs_review'
        lead.transition_to(LeadStatus.NEEDS_REVIEW)
        return {"outreach_id": existing.id}
    report = lead.reports.latest("created_at")
    contact = lead.primary_contact()
    greeting = contact.contact_name if contact and contact.contact_name else "the team"
    first = contact.contact_name.split()[0] if contact and contact.contact_name else ""
    salutation = f"Hi {first}," if first else "Hi there,"
    out = structured(DraftOut).invoke([
        ("system", SYSTEM_RULES + (
            " Write a short, plain-English cold email (60-100 words) from Webfolks, an IT services company. Rules: "
            f"0) Begin the body with exactly this greeting on its own line: {salutation} "
            "1) Then open with ONE specific fact from the research about what they do or ship; no flattery such as 'impressed' "
            "or 'love what you do'. "
            "2) Suggest ONE concrete, modest idea tied to a gap the research supports. Never offer something the research "
            "says they already have, and never claim they lack something unless the research says so. Do not propose a "
            "variation of anything the research names (if they already have Slack integrations or AI analysis, do not "
            "propose a Slack or AI-analysis feature); pick a different gap, or ask a question about it instead. "
            "3) No buzzwords or jargon (no 'leverage', 'cutting-edge', 'synergy', 'roadmap', 'LLM pipeline', 'unlock'). "
            "4) End with one low-pressure question, for example whether a short chat would be worthwhile. "
            "5) Subject: under 8 words, specific to them; do not use 'Exploring' or 'possibilities'. "
            f"6) Sign off exactly as: {getattr(settings, 'SENDER_NAME', 'The Webfolks team')}. "
            "No fake urgency, no claim of a prior relationship, and no invented facts, numbers or customers.")),
        ("human", f"To: {greeting} at {lead.company_name}\n"
                  f"Research summary: {report.business_summary}\nPotential opportunities: {json.dumps(report.identified_opportunities)}\n"
                  f"Services to mention: {report.relevant_services}"),
    ])
    om = OutreachMessage.objects.create(lead=lead, subject=out.subject, message_body=out.body)
    lead.transition_to(LeadStatus.NEEDS_REVIEW)
    return {"outreach_id": om.id}


def approval_node(state):
    """Human-in-the-loop gate. Nothing before this line has side effects, because
    LangGraph re-runs the node from the top when it resumes."""
    decision = interrupt({"lead_id": state["lead_id"], "outreach_id": state["outreach_id"]})
    return {
        "approval": "approved" if decision.get("approved") else "rejected",
        "approver_id": decision.get("approver_id"),
        "edited_subject": decision.get("subject"),
        "edited_body": decision.get("body"),
    }


def route_after_approval(state):
    return "send" if state.get("approval") == "approved" else "end"


@logged("send")
def send_node(state):
    om = OutreachMessage.objects.select_related("lead__campaign").get(id=state["outreach_id"])
    lead = om.lead
    if om.sent_status == "sent":  # never send twice
        return {}
    if state.get("edited_subject"):
        om.subject = state["edited_subject"]
    if state.get("edited_body"):
        om.message_body = state["edited_body"]
    om.approval_status, om.approved_by_id = "approved", state.get("approver_id")
    om.save()
    lead.transition_to(LeadStatus.APPROVED)

    already = OutreachMessage.objects.filter(sent_status="sent", lead__domain=lead.domain).exclude(lead=lead).exists()
    if already:  # the same company was already emailed from another campaign: never contact twice
        om.sent_status = "duplicate"
        om.save(update_fields=["sent_status"])
        return {}
    contact = lead.primary_contact()
    if not contact:
        om.sent_status = "no_recipient"
        om.save(update_fields=["sent_status"])
        return {}
    res = mail.send_email(contact.email, om.subject, om.message_body)
    om.sent_status, om.sent_at, om.gmail_thread_id = "sent", timezone.now(), res["thread_id"]
    om.save(update_fields=["sent_status", "sent_at", "gmail_thread_id"])
    CommunicationLog.objects.create(lead=lead, direction="out", message_content=om.message_body, response_status="sent")
    lead.transition_to(LeadStatus.CONTACTED)
    schedule_followups(lead, om, start=om.sent_at)
    return {}