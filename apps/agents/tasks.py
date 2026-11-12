import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone
from langgraph.types import Command

from apps.core.models import Lead, LeadStatus, OutreachMessage, Reply

from . import email as mail
from . import finder, followups, nodes
from . import replies as replies_mod
from .graph import get_graph


def _cfg(lead_id):
    return {"configurable": {"thread_id": f"lead-{lead_id}"}}


@shared_task
def run_lead_pipeline(lead_id):
    lead = Lead.objects.get(id=lead_id)
    get_graph().invoke({"lead_id": lead.id, "campaign_id": lead.campaign_id}, _cfg(lead_id))


@shared_task
def resume_lead_pipeline(lead_id, decision):
    graph = get_graph()
    if graph.get_state(_cfg(lead_id)).next:  # paused at the approval gate: resume it
        graph.invoke(Command(resume=decision), _cfg(lead_id))
        return
    # The paused state is gone (e.g. the dev server restarted while the draft waited, since dev keeps it in memory).
    # Finish the approval straight from the database so the draft is never stuck.
    om = OutreachMessage.objects.filter(lead_id=lead_id, approval_status__in=["pending", "queued"]).order_by("-id").first()
    if om and decision.get("approved"):
        nodes.send_node({"lead_id": lead_id, "outreach_id": om.id, "approver_id": decision.get("approver_id"),
                         "edited_subject": decision.get("subject"), "edited_body": decision.get("body")})


@shared_task
def start_campaign(campaign_id):
    for lead in Lead.objects.filter(campaign_id=campaign_id, status=LeadStatus.DISCOVERED):
        run_lead_pipeline.delay(lead.id)


@shared_task
def process_due_followups():
    return followups.process_due()


@shared_task
def send_followup_task(followup_id):
    from apps.core.models import FollowUp
    followups.send_followup(FollowUp.objects.select_related("lead", "outreach").get(id=followup_id))


log = logging.getLogger(__name__)
LIVE = [LeadStatus.CONTACTED, LeadStatus.FOLLOWUP_DUE, LeadStatus.RESPONDED, LeadStatus.INTERESTED,
        LeadStatus.MEETING_SCHEDULED]


@shared_task
def poll_replies():
    """Read Gmail threads of live leads; store new replies, analyse them, update the leads (steps 8-9)."""
    for r in Reply.objects.filter(applied=False).select_related("lead")[:20]:  # retry anything stored but unprocessed
        try:
            replies_mod.process(r)
        except Exception as e:
            log.warning("Could not process reply %s: %s", r.id, e)
    created = 0
    for lead in Lead.objects.filter(status__in=LIVE):
        known = set(Reply.objects.filter(lead=lead).exclude(gmail_message_id="").values_list("gmail_message_id", flat=True))
        for om in lead.outreach.filter(sent_status="sent").exclude(gmail_thread_id=""):
            try:
                new = mail.list_new_replies(om.gmail_thread_id, known)
            except Exception as e:
                log.warning("Gmail read failed for %s: %s", lead.company_name, e)
                continue
            for m in new:
                known.add(m["id"])
                if m["bounce"]:  # delivery failure: never use that address again
                    for c in lead.contacts.exclude(email=""):
                        if c.email.lower() in m["raw"].lower():
                            c.verification_status = "bounced"
                            c.save(update_fields=["verification_status"])
                    Reply.objects.create(lead=lead, from_email=m["from"], body=m["body"][:8000], gmail_message_id=m["id"],
                                         classification="bounce", summary="Delivery failure; address marked bounced.",
                                         analyzed_at=timezone.now(), applied=True)
                    continue
                r = Reply.objects.create(lead=lead, from_email=m["from"], body=m["body"][:8000], gmail_message_id=m["id"])
                created += 1
                try:
                    replies_mod.process(r)
                except Exception as e:
                    log.warning("Could not analyse reply %s: %s", r.id, e)
    return created


@shared_task
def find_leads_task(campaign_id, run_pipeline=False):
    from apps.core.models import FinderRun
    stale = timezone.now() - timedelta(minutes=10)
    FinderRun.objects.filter(campaign_id=campaign_id, status="running", created_at__lt=stale).update(
        status="error", detail="Interrupted (server restarted or request cancelled).")
    if FinderRun.objects.filter(campaign_id=campaign_id, status="running").exists():
        return {"status": "already_running"}
    summary = finder.find_leads(campaign_id)
    if run_pipeline and summary["leads_created"]:
        start_campaign.delay(campaign_id)
    return summary