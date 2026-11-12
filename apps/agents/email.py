"""Email transport. `console` prints instead of sending; `gmail` uses the Gmail API (send + read replies)."""
import base64
import logging
import re
import uuid
from email.mime.text import MIMEText
from email.utils import parseaddr

from django.conf import settings

log = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.readonly"]


PLACEHOLDERS = ("your full postal address", "your address", "postal address", "address here", "example", "todo", "xxx", "tbd")


def address_is_real() -> bool:
    a = (settings.COMPANY_ADDRESS or "").strip().lower()
    return len(a) >= 15 and not any(p in a for p in PLACEHOLDERS)


def footer() -> str:
    lines = ["", "--"]
    if settings.COMPANY_ADDRESS:
        addr = settings.COMPANY_ADDRESS.strip()
        starts_with_name = addr.lower().startswith(settings.COMPANY_NAME.lower())
        lines.append(addr if starts_with_name else f"{settings.COMPANY_NAME}, {addr}")
    lines.append("If you'd prefer not to hear from us, just reply with 'unsubscribe' and we won't contact you again.")
    return "\n".join(lines)


def _gmail():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(settings.GMAIL_TOKEN_FILE, SCOPES)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def send_email(to: str, subject: str, body: str, thread_id: str = "") -> dict:
    real = settings.EMAIL_PROVIDER == "gmail"
    if real and not address_is_real():
        raise RuntimeError("Set COMPANY_ADDRESS in .env to your real postal address before sending real email "
                           "(commercial email must carry a postal address and an opt-out line).")
    body = body.rstrip() + "\n" + footer()
    if not real:
        log.warning("[console email] to=%s subject=%s\n%s", to, subject, body)
        return {"message_id": uuid.uuid4().hex, "thread_id": thread_id or uuid.uuid4().hex}
    msg = MIMEText(body)
    msg["to"], msg["subject"] = to, subject
    if settings.GMAIL_SENDER:
        msg["from"] = settings.GMAIL_SENDER
    payload = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
    if thread_id:
        payload["threadId"] = thread_id
    res = _gmail().users().messages().send(userId="me", body=payload).execute()
    return {"message_id": res["id"], "thread_id": res["threadId"]}


def extract_text(payload: dict) -> str:
    """Plain text of a Gmail message payload (falls back to stripped HTML)."""
    def walk(part, want):
        if part.get("mimeType") == want and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"] + "==").decode("utf-8", "replace")
        for sub in part.get("parts", []) or []:
            found = walk(sub, want)
            if found:
                return found
        return ""
    text = walk(payload, "text/plain")
    if not text:
        html = walk(payload, "text/html")
        text = re.sub(r"<[^>]+>", " ", html)
    return text


def clean_reply_text(text: str) -> str:
    """Keep only what the prospect wrote: cut the quoted earlier email."""
    kept = []
    for line in text.replace("\r", "").split("\n"):
        s = line.strip()
        if s.startswith(">") or re.match(r"^On .+ wrote:\s*$", s) or s.startswith("-----Original Message") \
                or re.match(r"^(From|Sent): .+", s) and kept:
            break
        kept.append(line)
    cleaned = "\n".join(kept).strip()
    return cleaned or text.strip()[:2000]


def list_new_replies(thread_id: str, known_ids: set) -> list[dict]:
    """Messages in a thread that we did not send and have not stored yet."""
    if settings.EMAIL_PROVIDER != "gmail" or not thread_id:
        return []
    svc = _gmail()
    me = svc.users().getProfile(userId="me").execute()["emailAddress"].lower()
    thread = svc.users().threads().get(userId="me", id=thread_id, format="full").execute()
    out = []
    for m in thread.get("messages", []):
        if m["id"] in known_ids:
            continue
        headers = {h["name"].lower(): h["value"] for h in m["payload"].get("headers", [])}
        sender = parseaddr(headers.get("from", ""))[1].lower()
        if not sender or sender == me:
            continue
        raw = extract_text(m["payload"])
        out.append({"id": m["id"], "from": sender, "subject": headers.get("subject", ""),
                    "body": clean_reply_text(raw), "raw": raw,
                    "bounce": sender.startswith(("mailer-daemon", "postmaster"))})
    return out


