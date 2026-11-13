from django.contrib.auth.models import User
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db.models import Count
from rest_framework import mixins, status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action, api_view
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated
from rest_framework.response import Response

from apps.agents import nodes, tasks
from apps.agents.followups import send_followup
from apps.agents.goals import parse_goal
from apps.agents.replies import process as process_reply

from .discovery import import_leads_csv, normalize_domain
from .models import (Campaign, CommunicationLog, Contact, FollowUp, InvalidTransition, Lead, LeadStatus,
                     OptOut, OutreachMessage, Reply)
from .serializers import (CampaignSerializer, FollowUpSerializer, LeadSerializer,
                          OutreachSerializer, ResearchSerializer)


def role_of(user):
    if user.is_superuser:
        return "admin"
    p = getattr(user, "profile", None)
    return p.role if p else "bde"


# Statuses a person may set by hand. 'opted_out' must go through /opt-out/ (it also records the opt-out list), and
# 'approved'/'contacted' only happen when an email is really approved and sent.
MANUAL_STATUSES = {"responded", "interested", "meeting_scheduled", "converted", "not_interested", "disqualified"}


class ManagerWrites(BasePermission):
    """Anyone signed in may read; only an admin or sales manager may change things."""

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        return request.method in SAFE_METHODS or role_of(request.user) in ("admin", "manager")


class CampaignViewSet(viewsets.ModelViewSet):
    queryset = Campaign.objects.all().order_by("-created_at")
    serializer_class = CampaignSerializer
    permission_classes = [ManagerWrites]

    def _require_manager(self, request):
        if role_of(request.user) not in ("admin", "manager"):
            return Response({"detail": "Admin or Sales Manager role required."}, status=403)

    def create(self, request, *a, **kw):
        return self._require_manager(request) or super().create(request, *a, **kw)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=False, methods=["post"], url_path="from-goal")
    def from_goal(self, request):
        """Step 1: turn 'Find 50 US startups that need AI development' into a draft campaign.
        Body: {"goal": "...", "create": true}. With create=false it only previews the parsed settings.
        The campaign is created as a DRAFT; nothing runs until /start/ is called."""
        denied = self._require_manager(request)
        if denied:
            return denied
        goal = (request.data.get("goal") or "").strip()
        if len(goal) < 10:
            return Response({"detail": "Describe the sales goal (at least a sentence)."}, status=400)
        try:
            parsed = parse_goal(goal[:2000])
        except Exception as e:  # LLM/network failure
            return Response({"detail": f"Could not parse goal: {e}"}, status=502)
        questions = parsed.pop("clarifying_questions")
        if request.data.get("create", True) in (False, "false", "0", 0):
            return Response({"preview": parsed, "clarifying_questions": questions})
        campaign = Campaign.objects.create(created_by=request.user, **parsed)
        return Response({"campaign": CampaignSerializer(campaign).data, "clarifying_questions": questions}, status=201)

    @action(detail=True, methods=["post"], url_path="import-csv")
    def import_csv(self, request, pk=None):
        f = request.FILES.get("file")
        if not f:
            return Response({"detail": "Upload a CSV as 'file'."}, status=400)
        return Response(import_leads_csv(self.get_object(), f))

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        denied = self._require_manager(request)
        if denied:
            return denied
        c = self.get_object()
        c.campaign_status = Campaign.Status.ACTIVE
        c.save(update_fields=["campaign_status"])
        tasks.start_campaign.delay(c.id)
        return Response({"status": "started"}, status=202)

    @action(detail=True, methods=["post"], url_path="find-leads")
    def find_leads(self, request, pk=None):
        """Step 2: search for companies matching the campaign. Body (optional): {"run_pipeline": true}"""
        denied = self._require_manager(request)
        if denied:
            return denied
        c = self.get_object()
        tasks.find_leads_task.delay(c.id, bool(request.data.get("run_pipeline")))
        return Response({"status": "queued"}, status=202)

    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        c = self.get_object()
        c.campaign_status = Campaign.Status.PAUSED
        c.save(update_fields=["campaign_status"])
        return Response({"status": "paused"})


class LeadViewSet(viewsets.ModelViewSet):
    serializer_class = LeadSerializer

    def get_permissions(self):
        # create/edit/delete: managers only. Working a lead (run, replies, set-status...) is open to its BDE.
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [ManagerWrites()]
        return [IsAuthenticated()]

    def get_queryset(self):
        qs = Lead.objects.select_related("campaign").prefetch_related("contacts")
        if role_of(self.request.user) == "bde":
            qs = qs.filter(assigned_to=self.request.user)
        q = self.request.query_params
        for key, field in (("campaign", "campaign_id"), ("status", "status"), ("priority", "priority")):
            if q.get(key):
                qs = qs.filter(**{field: q[key]})
        return qs.order_by("-lead_score", "-created_at")

    def perform_create(self, serializer):
        domain = normalize_domain(serializer.validated_data["website"])
        if Lead.objects.filter(campaign=serializer.validated_data["campaign"], domain=domain).exists():
            raise ValidationError({"website": "A lead with this website already exists in this campaign."})
        serializer.save(domain=domain)

    @action(detail=True, methods=["post"])
    def run(self, request, pk=None):
        tasks.run_lead_pipeline.delay(self.get_object().id)
        return Response({"status": "queued"}, status=202)

    @action(detail=True, methods=["get"])
    def research(self, request, pk=None):
        r = self.get_object().reports.order_by("-created_at").first()
        return Response(ResearchSerializer(r).data) if r else Response(status=404)

    @action(detail=True, methods=["post"], url_path="set-status")
    def set_status(self, request, pk=None):
        lead = self.get_object()
        target = request.data.get("status", "")
        if target == "opted_out":
            return Response({"detail": "Use /opt-out/ so the address is added to the opt-out list."}, status=400)
        if target not in MANUAL_STATUSES:
            return Response({"detail": f"Status can be set by hand to: {', '.join(sorted(MANUAL_STATUSES))}."}, status=400)
        try:
            if target == "interested" and lead.status in ("contacted", "followup_due"):
                lead.transition_to("responded")
            lead.transition_to(target)
        except (InvalidTransition, ValueError) as e:
            return Response({"detail": str(e)}, status=400)
        return Response(LeadSerializer(lead).data)

    @action(detail=True, methods=["post"])
    def replies(self, request, pk=None):
        """Steps 8-9: submit a prospect's reply. Body: {"body": "...", "from_email": "optional"}"""
        lead = self.get_object()
        text = (request.data.get("body") or "").strip()
        if not text:
            return Response({"detail": "Provide the reply text as 'body'."}, status=400)
        reply = Reply.objects.create(lead=lead, body=text[:8000], from_email=request.data.get("from_email", ""))
        try:
            outcome = process_reply(reply)
        except Exception as e:
            return Response({"detail": f"Could not analyse the reply: {e}", "reply_id": reply.id}, status=502)
        reply.refresh_from_db()
        lead.refresh_from_db()
        return Response({"classification": reply.classification, "summary": reply.summary, "outcome": outcome,
                         "suggested_reply": reply.suggested_reply, "lead_status": lead.status,
                         "assigned_to": str(lead.assigned_to) if lead.assigned_to else None})

    @action(detail=True, methods=["post"])
    def contacts(self, request, pk=None):
        """Add a recipient to a lead. Body: {"email": "...", "contact_name": "", "job_title": ""}"""
        lead = self.get_object()
        addr = (request.data.get("email") or "").strip().lower()
        try:
            validate_email(addr)
        except DjangoValidationError:
            return Response({"detail": "Enter a valid email address."}, status=400)
        if OptOut.objects.filter(email__iexact=addr).exists():
            return Response({"detail": "This address has opted out and can never be contacted."}, status=400)
        c, created = Contact.objects.get_or_create(
            lead=lead, email=addr,
            defaults={"contact_name": (request.data.get("contact_name") or "")[:200],
                      "job_title": (request.data.get("job_title") or "")[:200],
                      "verification_status": "manual", "source_url": f"added by {request.user.username}"})
        if not created and c.verification_status == "bounced":
            return Response({"detail": "That address bounced earlier. Use a different one."}, status=400)
        fresh = Lead.objects.prefetch_related("contacts").get(pk=lead.pk)  # the list fetched earlier is stale now
        return Response(LeadSerializer(fresh).data, status=201 if created else 200)

    @action(detail=True, methods=["post"], url_path="mark-replied")
    def mark_replied(self, request, pk=None):
        lead = self.get_object()
        try:
            lead.transition_to(LeadStatus.RESPONDED)
        except InvalidTransition as e:
            return Response({"detail": str(e)}, status=400)
        CommunicationLog.objects.create(lead=lead, direction="in", response_status="reply_marked")
        return Response(LeadSerializer(lead).data)

    @action(detail=True, methods=["post"], url_path="opt-out")
    def opt_out(self, request, pk=None):
        lead = self.get_object()
        for c in lead.contacts.exclude(email=""):
            OptOut.objects.get_or_create(email=c.email.lower(), defaults={"reason": request.data.get("reason", "")})
        lead.transition_to(LeadStatus.OPTED_OUT)  # also cancels pending follow-ups
        return Response(LeadSerializer(lead).data)


def _bde_filter(qs, request, path):
    """A BDE only sees items that belong to leads assigned to them."""
    return qs.filter(**{path: request.user}) if role_of(request.user) == "bde" else qs


class OutreachViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = OutreachMessage.objects.all().order_by("-created_at")
    serializer_class = OutreachSerializer

    def get_queryset(self):
        return _bde_filter(super().get_queryset(), self.request, "lead__assigned_to")

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        om = self.get_object()
        if om.approval_status == "approved" and om.sent_status == "no_recipient":  # retry once a contact exists
            try:
                nodes.send_node({"lead_id": om.lead_id, "outreach_id": om.id, "approver_id": request.user.id})
            except Exception as e:
                return Response({"detail": f"Could not send: {e}"}, status=502)
            om.refresh_from_db()
            if om.sent_status != "sent":
                return Response({"detail": "Still no recipient. Add a contact with an email first."}, status=409)
            return Response({"approval_status": om.approval_status, "sent_status": om.sent_status})
        if om.approval_status != "pending":
            return Response({"detail": f"Already {om.approval_status}."}, status=400)
        om.approval_status = "queued"
        om.save(update_fields=["approval_status"])
        try:
            tasks.resume_lead_pipeline.delay(om.lead_id, {
                "approved": True, "approver_id": request.user.id,
                "subject": request.data.get("subject"), "body": request.data.get("body"),
            })
        except Exception as e:  # never leave a draft stuck in 'queued'
            om.approval_status = "pending"
            om.save(update_fields=["approval_status"])
            return Response({"detail": f"Could not send: {e}"}, status=502)
        om.refresh_from_db()
        return Response({"approval_status": om.approval_status, "sent_status": om.sent_status}, status=202)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        om = self.get_object()
        if om.approval_status != "pending":
            return Response({"detail": f"Already {om.approval_status}."}, status=400)
        om.approval_status = "rejected"
        om.save(update_fields=["approval_status"])
        try:
            tasks.resume_lead_pipeline.delay(om.lead_id, {"approved": False, "approver_id": request.user.id})
        except Exception:
            pass  # the draft is rejected either way
        return Response({"status": "rejected"})


class FollowUpViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = FollowUp.objects.select_related("lead").order_by("scheduled_at")
    serializer_class = FollowUpSerializer

    def get_queryset(self):
        qs = _bde_filter(super().get_queryset(), self.request, "lead__assigned_to")
        s = self.request.query_params.get("status")
        return qs.filter(status=s) if s else qs

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        fu = self.get_object()
        if fu.status != "awaiting_approval":
            return Response({"detail": f"Status is {fu.status}."}, status=400)
        if request.data.get("body"):
            fu.message_body = request.data["body"]
            fu.save(update_fields=["message_body"])
        try:
            sent = send_followup(fu)
        except Exception as e:
            return Response({"detail": f"Could not send: {e}"}, status=502)
        return Response({"sent": sent})

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        fu = self.get_object()
        fu.status = "cancelled"
        fu.save(update_fields=["status"])
        return Response({"status": "cancelled"})


@api_view(["GET"])
def analytics_overview(request):
    by_status = dict(Lead.objects.values_list("status").annotate(n=Count("id")))
    total = sum(by_status.values())
    contacted = OutreachMessage.objects.filter(sent_status="sent").count()
    responded = (CommunicationLog.objects.filter(direction="in")
                 .exclude(response_status="auto_reply").values("lead").distinct().count())  # leads that really replied
    return Response({
        "campaigns": Campaign.objects.count(),
        "leads_total": total,
        "leads_by_status": by_status,
        "qualified_or_beyond": total - by_status.get("discovered", 0) - by_status.get("disqualified", 0),
        "high_priority": Lead.objects.filter(priority="high").count(),
        "outreach_sent": contacted,
        "responses": responded,
        "response_rate": round(responded / contacted, 3) if contacted else None,
    })


@api_view(["POST"])
def logout(request):
    Token.objects.filter(user=request.user).delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET"])
def profile(request):
    u: User = request.user
    return Response({"id": u.id, "username": u.username, "email": u.email, "role": role_of(u)})