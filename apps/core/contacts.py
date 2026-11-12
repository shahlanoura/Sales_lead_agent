"""Which address should a cold email go to? Pure functions, no database access."""
import re

DECISION_TITLES = [
    (r"\b(founder|co-?founder|owner|ceo|chief executive|managing director|president)\b", 100),
    (r"\b(cto|cio|coo|cpo|cmo|chief)\b", 90),
    (r"\b(vp|vice president|head of|director)\b", 70),
    (r"\b(manager|lead)\b", 40),
]
GOOD_INBOXES = {"founders": 80, "founder": 80, "ceo": 80, "sales": 60, "partnerships": 60, "partners": 60,
                "business": 55, "bd": 55, "hello": 50, "hi": 45, "contact": 50, "info": 45, "team": 45,
                "enquiries": 40, "inquiries": 40, "general": 35, "office": 30}
LAST_RESORT = {"support", "help", "helpdesk", "service", "customerservice", "care", "feedback"}
NEVER = {"hr", "jobs", "job", "careers", "career", "recruiting", "recruitment", "recruiter", "talent", "people",
         "privacy", "legal", "abuse", "security", "billing", "accounts", "accounting", "finance", "press", "media",
         "compliance", "gdpr", "dpo", "unsubscribe", "webmaster", "postmaster", "mailer-daemon"}
NO_REPLY = {"noreply", "donotreply"}


def _local(email: str) -> str:
    return email.split("@")[0].lower()


def inbox_kind(email: str) -> str:
    """'never' (HR, privacy, no-reply...), 'good', 'last_resort', 'other' (role we don't know) or 'personal'."""
    lp = _local(email)
    squashed = re.sub(r"[._+-]", "", lp)
    tokens = [t for t in re.split(r"[._+-]", lp) if t]
    if squashed in NO_REPLY or any(t in NEVER for t in tokens):
        return "never"
    if lp in GOOD_INBOXES:
        return "good"
    if lp in LAST_RESORT or (tokens and tokens[0] in LAST_RESORT):
        return "last_resort"
    if re.fullmatch(r"[a-z]{2,}([._-][a-z]{2,})?", lp) and lp not in GOOD_INBOXES:
        return "personal"
    return "other"


def rank(email: str, name: str = "", title: str = ""):
    """Higher = better recipient. None = never email this address."""
    kind = inbox_kind(email)
    if kind == "never":
        return None
    score = {"personal": 60, "good": GOOD_INBOXES.get(_local(email), 40), "other": 20, "last_resort": 5}[kind]
    for pattern, points in DECISION_TITLES:
        if title and re.search(pattern, title.lower()):
            score += points
            break
    if name:
        score += 10
    return score