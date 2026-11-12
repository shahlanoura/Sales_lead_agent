"""Step 1 - understand the sales goal: free text -> structured campaign settings."""
from django.conf import settings

from .llm import structured
from .schemas import GoalOut

DEFAULT_LEAD_COUNT = 50
MAX_LEAD_COUNT = 500

PROMPT = (
    "You configure sales campaigns for Webfolks, an IT services company. Convert the sales goal into campaign "
    "settings. Rules: pick target_services ONLY from this list, copied exactly: {services}. If the goal does not "
    "name a service, infer the best-fitting one(s) from the need described and say so in assumptions. Never invent "
    "numbers, countries or industries the goal does not imply; leave them empty and note it in assumptions. "
    "Only add clarifying_questions if the goal is too vague to search for companies at all."
)


def parse_goal(goal_text: str) -> dict:
    goal_text = goal_text.strip()
    out = structured(GoalOut).invoke([
        ("system", PROMPT.format(services="; ".join(settings.WEBFOLKS_SERVICES))),
        ("human", goal_text),
    ])
    allowed = {s.lower(): s for s in settings.WEBFOLKS_SERVICES}
    services = [allowed[s.strip().lower()] for s in out.target_services if s.strip().lower() in allowed]
    assumptions = list(out.assumptions)
    count = out.target_lead_count
    if count <= 0:
        count = DEFAULT_LEAD_COUNT
        assumptions.append(f"No lead count given; defaulted to {DEFAULT_LEAD_COUNT}.")
    if count > MAX_LEAD_COUNT:
        count = MAX_LEAD_COUNT
        assumptions.append(f"Lead count capped at {MAX_LEAD_COUNT} per campaign.")
    return {
        "campaign_name": out.campaign_name[:200] or "Untitled campaign",
        "target_industry": out.target_industry, "target_country": out.target_country,
        "target_company_size": out.target_company_size, "target_services": services,
        "target_lead_count": count, "ideal_customer_profile": out.ideal_customer_profile,
        "goal_text": goal_text, "goal_assumptions": assumptions,
        "clarifying_questions": list(out.clarifying_questions),
    }
