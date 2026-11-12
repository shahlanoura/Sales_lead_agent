from rest_framework import serializers

from .models import Campaign, Contact, FollowUp, Lead, OutreachMessage, ResearchReport


class CampaignSerializer(serializers.ModelSerializer):
    class Meta:
        model = Campaign
        fields = "__all__"
        read_only_fields = ["campaign_status", "created_by", "created_at", "goal_assumptions"]


class ContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contact
        fields = "__all__"


class LeadSerializer(serializers.ModelSerializer):
    contacts = ContactSerializer(many=True, read_only=True)

    class Meta:
        model = Lead
        fields = "__all__"
        read_only_fields = ["domain", "lead_score", "priority", "status", "created_at"]


class ResearchSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResearchReport
        fields = "__all__"


class OutreachSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutreachMessage
        fields = "__all__"
        read_only_fields = ["approval_status", "sent_status", "approved_by", "gmail_thread_id", "sent_at", "created_at"]


class FollowUpSerializer(serializers.ModelSerializer):
    class Meta:
        model = FollowUp
        fields = "__all__"
