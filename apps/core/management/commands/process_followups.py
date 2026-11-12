from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Draft follow-ups that are due (they wait for approval unless FOLLOWUP_AUTOSEND=1). Run about hourly."

    def handle(self, *args, **opts):
        from apps.agents.followups import process_due
        self.stdout.write(f"Follow-ups handled: {process_due()}")