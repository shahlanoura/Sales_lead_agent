from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Read Gmail for new prospect replies and process them. Run every few minutes (Task Scheduler / cron)."

    def handle(self, *args, **opts):
        from apps.agents.tasks import poll_replies
        self.stdout.write(f"New replies processed: {poll_replies()}")

        