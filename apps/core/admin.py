from django.conf import settings
from django.contrib import admin, messages
from django.utils import timezone

from . import models


def _safe(modeladmin, request, label, fn):
    """Run an action; show a readable message instead of a 500 page if it fails."""
    try:
        return fn()
    except Exception as e:
        modeladmin.message_user(request, f"{label}: {type(e).__name__}: {e}", messages.ERROR)


@admin.register(models.Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ("campaign_name", "campaign_status", "target_lead_count", "lead_total", "created_at")
    actions = ["find_leads", "run_pipeline_on_discovered"]

    @admin.display(description="Leads")
    def lead_total(self, obj):
        return obj.leads.count()

    @admin.action(description="Find leads (Tavily search)")
    def find_leads(self, request, queryset):
        from apps.agents import tasks
        for c in queryset:
            res = tasks.find_leads_task.delay(c.id)
            if not settings.CELERY_TASK_ALWAYS_EAGER:
                self.message_user(request, f"{c}: lead search queued.")
                continue
            s = res.result if isinstance(res.result, dict) else {}
            if s.get("status") == "already_running":
                self.message_user(request, f"{c}: a search is already running. Wait for it to finish (watch Finder runs).",
                                  messages.WARNING)
            elif s.get("status") == "error":
                self.message_user(request, f"{c}: {s.get('detail')}", messages.ERROR)
            else:
                self.message_user(request, f"{c}: {s.get('leads_created', 0)} new leads "
                                           f"({s.get('total_leads')}/{s.get('target')}). {s.get('detail', '')}")

    @admin.action(description="Run pipeline on discovered leads (research, score, draft)")
    def run_pipeline_on_discovered(self, request, queryset):
        from apps.agents import tasks
        for c in queryset:
            n = c.leads.filter(status="discovered").count()
            _safe(self, request, str(c), lambda: tasks.start_campaign.delay(c.id))
            self.message_user(request, f"{c}: pipeline ran for {n} lead(s) in 'discovered' status. "
                                       "Check Leads (status/score), Outreach messages and Agent runs.")


@admin.register(models.FinderRun)
class FinderRunAdmin(admin.ModelAdmin):
    list_display = ("campaign", "status", "leads_created", "candidates_checked", "created_at")


@admin.register(models.Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ("company_name", "status", "lead_score", "priority", "assigned_to", "industry", "country", "domain", "campaign")
    list_filter = ("status", "priority", "assigned_to", "campaign")
    search_fields = ("company_name", "domain")
    actions = ["run_pipeline", "mark_replied", "mark_interested", "mark_meeting", "mark_converted",
               "mark_not_interested", "opt_out", "assign_bde", "check_gmail"]

    def _move(self, request, queryset, target, note=""):
        for lead in queryset:
            try:
                if target == "interested" and lead.status in ("contacted", "followup_due"):
                    lead.transition_to("responded")
                lead.transition_to(target)
            except Exception as e:
                self.message_user(request, f"{lead.company_name}: {e}", messages.ERROR)
                continue
            if target == "responded":
                models.CommunicationLog.objects.create(lead=lead, direction="in", response_status="reply_marked")
            left = lead.followups.filter(status__in=["pending", "awaiting_approval"]).count()
            self.message_user(request, f"{lead.company_name}: now '{lead.status}'. Follow-ups still scheduled: {left}. {note}")

    @admin.action(description="Mark as REPLIED (stops follow-ups)")
    def mark_replied(self, request, queryset):
        self._move(request, queryset, "responded")

    @admin.action(description="Mark as INTERESTED (stops follow-ups)")
    def mark_interested(self, request, queryset):
        self._move(request, queryset, "interested")

    @admin.action(description="Mark as MEETING SCHEDULED")
    def mark_meeting(self, request, queryset):
        self._move(request, queryset, "meeting_scheduled")

    @admin.action(description="Mark as CONVERTED (became a customer)")
    def mark_converted(self, request, queryset):
        self._move(request, queryset, "converted")

    @admin.action(description="Assign to the least-busy BDE")
    def assign_bde(self, request, queryset):
        from apps.agents.replies import pick_bde
        bde = pick_bde()
        if not bde:
            self.message_user(request, "No user has the BDE role. Add one under Profiles (role: Business Development Executive).",
                              messages.WARNING)
            return
        for lead in queryset:
            lead.assigned_to = bde
            lead.save(update_fields=["assigned_to"])
            self.message_user(request, f"{lead.company_name}: assigned to {bde}.")

    @admin.action(description="Check Gmail for replies now (all live leads)")
    def check_gmail(self, request, queryset):
        from apps.agents import tasks
        if settings.EMAIL_PROVIDER != "gmail":
            self.message_user(request, "EMAIL_PROVIDER is 'console', so there is no mailbox to check.", messages.WARNING)
            return
        res = _safe(self, request, "Gmail", lambda: tasks.poll_replies.delay())
        if res is not None:
            self.message_user(request, f"New replies found and processed: {res.result}. See Replies and Handoffs.")

    @admin.action(description="Mark as NOT INTERESTED (stops follow-ups)")
    def mark_not_interested(self, request, queryset):
        self._move(request, queryset, "not_interested")

    @admin.action(description="OPT OUT: never contact again")
    def opt_out(self, request, queryset):
        for lead in queryset:
            for c in lead.contacts.exclude(email=""):
                models.OptOut.objects.get_or_create(email=c.email.lower(), defaults={"reason": "marked in admin"})
        self._move(request, queryset, "opted_out", "Their email addresses are on the opt-out list.")

    @admin.action(description="Run pipeline on selected leads (research, score, draft)")
    def run_pipeline(self, request, queryset):
        from apps.agents import tasks
        for lead in queryset:
            _safe(self, request, lead.company_name, lambda: tasks.run_lead_pipeline.delay(lead.id))
            lead.refresh_from_db()
            self.message_user(request, f"{lead.company_name}: status is now '{lead.status}', score {lead.lead_score}.")


@admin.register(models.Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("lead", "contact_name", "job_title", "email", "verification_status")
    list_filter = ("verification_status",)


@admin.register(models.ResearchReport)
class ResearchReportAdmin(admin.ModelAdmin):
    list_display = ("lead", "research_confidence", "created_at")


@admin.register(models.AgentRun)
class AgentRunAdmin(admin.ModelAdmin):
    list_display = ("lead", "node", "status", "created_at", "detail")
    list_filter = ("status", "node")
    ordering = ("-id",)


@admin.register(models.OutreachMessage)
class OutreachMessageAdmin(admin.ModelAdmin):
    list_display = ("lead", "recipient", "subject", "approval_status", "sent_status", "created_at")
    list_filter = ("approval_status", "sent_status")
    actions = ["approve_and_send", "reject_drafts"]

    @admin.display(description="Will be sent to")
    def recipient(self, obj):
        c = obj.lead.primary_contact()
        return c.email if c else "- none -"

    @admin.action(description="Approve and SEND selected drafts")
    def approve_and_send(self, request, queryset):
        from apps.agents import tasks
        for om in queryset:
            if om.approval_status != "pending":
                self.message_user(request, f"{om.lead}: already {om.approval_status}.", messages.WARNING)
                continue
            om.approval_status = "queued"
            om.save(update_fields=["approval_status"])
            decision = {"approved": True, "approver_id": request.user.id, "subject": None, "body": None}
            try:
                tasks.resume_lead_pipeline.delay(om.lead_id, decision)
            except Exception as e:
                om.approval_status = "pending"
                om.save(update_fields=["approval_status"])
                self.message_user(request, f"{om.lead}: {type(e).__name__}: {e}", messages.ERROR)
                continue
            om.refresh_from_db()
            level = messages.SUCCESS if om.sent_status == "sent" else messages.WARNING
            note = " (EMAIL_PROVIDER=console: printed in the terminal, not really sent)" if settings.EMAIL_PROVIDER != "gmail" else ""
            self.message_user(request, f"{om.lead}: approval={om.approval_status}, send={om.sent_status}{note}", level)

    @admin.action(description="Reject selected drafts")
    def reject_drafts(self, request, queryset):
        from apps.agents import tasks
        for om in queryset.filter(approval_status="pending"):
            om.approval_status = "rejected"
            om.save(update_fields=["approval_status"])
            _safe(self, request, str(om.lead), lambda: tasks.resume_lead_pipeline.delay(
                om.lead_id, {"approved": False, "approver_id": request.user.id}))
            self.message_user(request, f"{om.lead}: draft rejected.")


@admin.register(models.FollowUp)
class FollowUpAdmin(admin.ModelAdmin):
    list_display = ("lead", "scheduled_at", "status", "sent_at")
    list_filter = ("status",)
    actions = ["make_due_now", "process_due_now", "approve_and_send_followups"]

    @admin.action(description="TEST: make selected pending follow-ups due now")
    def make_due_now(self, request, queryset):
        n = queryset.filter(status="pending").update(scheduled_at=timezone.now())
        self.message_user(request, f"{n} follow-up(s) are now due. Next: 'Process due follow-ups now'.")

    @admin.action(description="Process ALL due follow-ups now (drafts them for approval)")
    def process_due_now(self, request, queryset):
        from apps.agents.followups import process_due
        n = _safe(self, request, "Process due", process_due)
        if n is not None:
            self.message_user(request, f"{n} follow-up(s) drafted and waiting for approval "
                                       "(status: awaiting_approval). Edit the text if you like, then approve.")

    @admin.action(description="Approve and SEND selected follow-ups (status: awaiting_approval)")
    def approve_and_send_followups(self, request, queryset):
        from apps.agents.followups import send_followup
        for fu in queryset.filter(status="awaiting_approval"):
            ok = _safe(self, request, str(fu.lead), lambda: send_followup(fu))
            self.message_user(request, f"{fu.lead}: follow-up {'sent' if ok else 'not sent'}.")


@admin.register(models.Reply)
class ReplyAdmin(admin.ModelAdmin):
    """Paste a prospect's reply here (Add reply). It is analysed and the lead is updated automatically."""
    list_display = ("lead", "classification", "short_summary", "applied", "created_at")
    list_filter = ("classification", "applied")
    fields = ("lead", "from_email", "body", "classification", "summary", "suggested_reply", "analysis", "applied")
    readonly_fields = ("classification", "summary", "suggested_reply", "analysis", "applied")
    actions = ["analyze_selected"]

    @admin.display(description="Summary")
    def short_summary(self, obj):
        return (obj.summary or "")[:90]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if change:
            return
        from apps.agents.replies import process
        outcome = _safe(self, request, str(obj.lead), lambda: process(obj))
        obj.refresh_from_db()
        obj.lead.refresh_from_db()
        if outcome:
            self.message_user(request, f"{obj.lead}: classified '{obj.classification}'; {outcome}. "
                                       f"Lead status is now '{obj.lead.status}'.")

    @admin.action(description="Analyse / apply selected replies (retry)")
    def analyze_selected(self, request, queryset):
        from apps.agents.replies import process
        for r in queryset:
            outcome = _safe(self, request, str(r.lead), lambda: process(r))
            if outcome:
                self.message_user(request, f"{r.lead}: {outcome}.")


@admin.register(models.Handoff)
class HandoffAdmin(admin.ModelAdmin):
    list_display = ("lead", "assigned_to", "reply_type", "status", "created_at")
    list_filter = ("status", "assigned_to")
    actions = ["mark_done"]

    @admin.display(description="Reply type")
    def reply_type(self, obj):
        return obj.reply.classification if obj.reply else "-"

    @admin.action(description="Mark handoff as done")
    def mark_done(self, request, queryset):
        self.message_user(request, f"{queryset.update(status='done')} handoff(s) marked done.")


for m in (models.Profile, models.CommunicationLog, models.OptOut):
    admin.site.register(m)


    