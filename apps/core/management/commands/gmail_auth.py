from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "One-time Gmail login: opens a browser and saves a token for sending mail and reading replies."

    def handle(self, *args, **opts):
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        from apps.agents.email import SCOPES
        secret = Path(settings.GMAIL_CLIENT_SECRET_FILE)
        if not secret.exists():
            raise CommandError(f"{secret} not found. Download the OAuth 'Desktop app' client JSON from Google Cloud "
                               "and save it in the project folder with that name.")
        creds = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES).run_local_server(port=0)
        Path(settings.GMAIL_TOKEN_FILE).write_text(creds.to_json())
        me = build("gmail", "v1", credentials=creds, cache_discovery=False).users().getProfile(userId="me").execute()
        self.stdout.write(self.style.SUCCESS(
            f"Connected as {me['emailAddress']}. Token saved to {settings.GMAIL_TOKEN_FILE}. "
            "Keep that file and client_secret.json private (never commit them)."))




        