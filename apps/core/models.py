from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from .contacts import rank


class Profile(models.Model):
    ROLES = [("admin", "Admin"), ("bde", "Business Development Executive"), ("manager", "Sales Manager")]
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=ROLES, default="bde")

    def __str__(self):
        return f"{self.user} ({self.role})"


def default_followup_days():
    return [4, 8, 14]


class Campaign(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft"
        ACTIVE = "active"
        PAUSED = "paused"
        COMPLETED = "completed"

    campaign_name = models.CharField(max_length=200)
    target_industry = models.CharField(max_length=200, blank=True)
    target_country = models.CharField(max_length=100, blank=True)
    target_company_size = models.CharField(max_length=100, blank=True)
    target_services = models.JSONField(default=list, blank=True)
    campaign_status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    followup_days = models.JSONField(default=default_followup_days)  # Day 1 = initial outreach
    exclude_low_priority = models.BooleanField(default=False)
    # Filled by the goal parser (step 1: understand the sales goal)
    goal_text = models.TextField(blank=True)
    target_lead_count = models.PositiveIntegerField(default=50)
    ideal_customer_profile = models.TextField(blank=True)
    goal_assumptions = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.campaign_name


class LeadStatus(models.TextChoices):
    DISCOVERED = "discovered"
    RESEARCHING = "researching"
    QUALIFIED = "qualified"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    CONTACTED = "contacted"
    FOLLOWUP_DUE = "followup_due"
    RESPONDED = "responded"
    INTERESTED = "interested"
    MEETING_SCHEDULED = "meeting_scheduled"
    CONVERTED = "converted"
    NOT_INTERESTED = "not_interested"
    DISQUALIFIED = "disqualified"
    OPTED_OUT = "opted_out"


S = LeadStatus
ALLOWED_TRANSITIONS = {
    S.DISCOVERED: {S.RESEARCHING, S.DISQUALIFIED},
    S.RESEARCHING: {S.QUALIFIED, S.NEEDS_REVIEW, S.DISQUALIFIED},
    S.QUALIFIED: {S.NEEDS_REVIEW, S.DISQUALIFIED},
    S.NEEDS_REVIEW: {S.APPROVED, S.DISQUALIFIED, S.RESEARCHING},  # RESEARCHING = re-run
    S.APPROVED: {S.CONTACTED, S.NEEDS_REVIEW},
    S.CONTACTED: {S.FOLLOWUP_DUE, S.RESPONDED, S.NOT_INTERESTED},
    S.FOLLOWUP_DUE: {S.CONTACTED, S.RESPONDED, S.NOT_INTERESTED},
    S.RESPONDED: {S.INTERESTED, S.NOT_INTERESTED},
    S.INTERESTED: {S.MEETING_SCHEDULED, S.NOT_INTERESTED},
    S.MEETING_SCHEDULED: {S.CONVERTED, S.NOT_INTERESTED},
    S.CONVERTED: set(), S.NOT_INTERESTED: set(), S.DISQUALIFIED: set(), S.OPTED_OUT: set(),
}
# Once a lead reaches any of these, automated follow-ups must stop.
STOP_FOLLOWUP_STATUSES = {
    S.RESPONDED, S.INTERESTED, S.MEETING_SCHEDULED, S.CONVERTED,
    S.NOT_INTERESTED, S.DISQUALIFIED, S.OPTED_OUT,
}


class InvalidTransition(ValueError):
    pass


class Lead(models.Model):
    class Priority(models.TextChoices):
        HIGH = "high"
        MEDIUM = "medium"
        LOW = "low"

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="leads")
    company_name = models.CharField(max_length=255)
    website = models.CharField(max_length=500)
    domain = models.CharField(max_length=255, db_index=True)
    industry = models.CharField(max_length=200, blank=True)
    country = models.CharField(max_length=100, blank=True)
    company_description = models.TextField(blank=True)
    lead_score = models.IntegerField(null=True, blank=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, blank=True)
    status = models.CharField(max_length=20, choices=LeadStatus.choices, default=LeadStatus.DISCOVERED)
    source_url = models.TextField(blank=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["campaign", "domain"], name="uniq_lead_domain_per_campaign")]

    def __str__(self):
        return self.company_name

    def transition_to(self, new_status, save=True):
        new_status = LeadStatus(new_status)
        if new_status == self.status:
            return
        if new_status == LeadStatus.OPTED_OUT and self.status != LeadStatus.OPTED_OUT:
            pass  # opt-out is always allowed
        elif new_status not in ALLOWED_TRANSITIONS.get(LeadStatus(self.status), set()):
            raise InvalidTransition(f"{self.status} -> {new_status} is not allowed")
        self.status = new_status
        if save:
            self.save(update_fields=["status"])
        if new_status in STOP_FOLLOWUP_STATUSES:
            self.followups.filter(status__in=["pending", "awaiting_approval"]).update(status="cancelled")

    @property
    def is_contactable(self):
        return self.status not in STOP_FOLLOWUP_STATUSES

    def primary_contact(self):
        """Best address to email: decision-maker > sales/hello/info inboxes > support. Never HR/privacy/no-reply."""
        optouts = {e.lower() for e in OptOut.objects.values_list("email", flat=True)}
        best, best_rank = None, -1
        for c in self.contacts.exclude(email=""):
            if c.email.lower() in optouts or c.verification_status == "bounced":
                continue
            r = rank(c.email, c.contact_name, c.job_title)
            if r is not None and r > best_rank:
                best, best_rank = c, r
        return best


class Contact(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="contacts")
    contact_name = models.CharField(max_length=200, blank=True)
    job_title = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    professional_profile = models.TextField(blank=True)
    verification_status = models.CharField(max_length=30, default="unverified")  # found_on_site / provider / manual
    source_url = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["lead", "email"], condition=~models.Q(email=""), name="uniq_contact_email")]


class ResearchReport(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="reports")
    business_summary = models.TextField(blank=True)
    identified_opportunities = models.JSONField(default=list)
    relevant_services = models.JSONField(default=list)
    evidence_sources = models.JSONField(default=list)
    research_confidence = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)


class OutreachMessage(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="outreach")
    subject = models.CharField(max_length=300)
    message_body = models.TextField()
    channel = models.CharField(max_length=20, default="email")
    approval_status = models.CharField(max_length=20, default="pending")  # pending/queued/approved/rejected
    sent_status = models.CharField(max_length=20, default="unsent")  # unsent/sent/no_recipient
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    gmail_thread_id = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)


class FollowUp(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="followups")
    outreach = models.ForeignKey(OutreachMessage, on_delete=models.CASCADE, related_name="followups")
    scheduled_at = models.DateTimeField()
    status = models.CharField(max_length=20, default="pending")  # pending/awaiting_approval/sent/cancelled
    message_body = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)


class CommunicationLog(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="communications")
    channel = models.CharField(max_length=20, default="email")
    direction = models.CharField(max_length=10)  # out / in
    message_content = models.TextField(blank=True)
    response_status = models.CharField(max_length=30, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class OptOut(models.Model):
    email = models.EmailField(unique=True)
    reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class AgentRun(models.Model):
    """Execution log: one row per agent node run."""
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="agent_runs")
    node = models.CharField(max_length=50)
    status = models.CharField(max_length=20, default="running")
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class FinderRun(models.Model):
    """One execution of the Lead Finder for a campaign (also remembers queries so reruns try new ones)."""
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="finder_runs")
    status = models.CharField(max_length=20, default="running")  # running / done / error
    queries = models.JSONField(default=list)
    candidates_checked = models.PositiveIntegerField(default=0)
    leads_created = models.PositiveIntegerField(default=0)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)


class Reply(models.Model):
    """A reply from a prospect (pasted by a BDE today; filled automatically once Gmail is connected)."""
    CLASSES = ["interested", "question", "objection", "not_interested", "unsubscribe", "auto_reply", "unclear"]
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="replies")
    from_email = models.EmailField(blank=True)
    body = models.TextField()
    classification = models.CharField(max_length=20, blank=True)
    summary = models.TextField(blank=True)
    suggested_reply = models.TextField(blank=True)  # a DRAFT for the BDE; never sent automatically
    analysis = models.JSONField(default=dict, blank=True)
    analyzed_at = models.DateTimeField(null=True, blank=True)
    applied = models.BooleanField(default=False)  # lead status/opt-out already updated from this reply
    gmail_message_id = models.CharField(max_length=100, blank=True, db_index=True)  # avoids storing a reply twice
    created_at = models.DateTimeField(auto_now_add=True)


class Handoff(models.Model):
    """An interested (or needs-a-human) prospect passed to a BDE, with everything they need."""
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="handoffs")
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    reply = models.ForeignKey(Reply, null=True, blank=True, on_delete=models.SET_NULL)
    summary = models.TextField()
    suggested_reply = models.TextField(blank=True)
    status = models.CharField(max_length=10, default="open")  # open / done
    created_at = models.DateTimeField(auto_now_add=True)


def schedule_followups(lead, outreach, start=None):
    start = start or timezone.now()
    days = lead.campaign.followup_days or []
    FollowUp.objects.bulk_create([
        FollowUp(lead=lead, outreach=outreach, scheduled_at=start + timedelta(days=int(d) - 1))
        for d in days
    ])


    