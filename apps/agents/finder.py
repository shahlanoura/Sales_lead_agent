"""Step 2 - find potential customers.

campaign -> LLM search queries -> Tavily -> drop junk/duplicates/opt-outs -> LLM verifies each candidate
-> save Leads -> repeat with fresh queries until the campaign's target count is reached."""
import logging
import re
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.utils import timezone

from apps.core.discovery import normalize_domain
from apps.core.models import Campaign, FinderRun, Lead, OptOut, OutreachMessage

from .llm import structured
from .schemas import QueryPlan, VerdictBatch

log = logging.getLogger(__name__)
TAVILY_URL = "https://api.tavily.com/search"
VERIFY_BATCH = 8

# Not customers: social, directories, news, aggregators, big tech. The first 25 are also sent to Tavily as exclude_domains.
BLOCKED = [
    "linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com", "tiktok.com",
    "reddit.com", "quora.com", "medium.com", "substack.com", "wikipedia.org", "github.com", "crunchbase.com",
    "pitchbook.com", "zoominfo.com", "apollo.io", "g2.com", "capterra.com", "clutch.co", "glassdoor.com",
    "indeed.com", "wellfound.com", "angel.co", "ycombinator.com", "producthunt.com", "techcrunch.com",
    "forbes.com", "bloomberg.com", "businessinsider.com", "venturebeat.com", "prnewswire.com", "businesswire.com",
    "globenewswire.com", "builtin.com", "owler.com", "tracxn.com", "dealroom.co", "cbinsights.com",
    "trustpilot.com", "amazon.com", "google.com", "apple.com", "microsoft.com", "openai.com", "nvidia.com",
    "ibm.com", "salesforce.com",
]


# Words that make a search engine return companies that SELL AI instead of companies that NEED it.
AI_TERMS = re.compile(r"\b(ai|a\.i\.|artificial intelligence|machine learning|ml|llms?|gpt|chatbots?|agents?|copilots?)\b", re.I)


def _sells_ai(c) -> bool:
    return any(re.search(r"\bai\b", s, re.I) for s in (c.target_services or [])) or bool(re.search(r"\bAI\b", c.goal_text or ""))


class FinderError(RuntimeError):
    """Fatal problem (missing/invalid key, quota). Ends the run with a clear message."""


def is_blocked(domain: str) -> bool:
    return any(domain == b or domain.endswith("." + b) for b in BLOCKED)


def tavily_search(query: str, max_results: int) -> list[dict]:
    key = settings.TAVILY_API_KEY
    if not key:
        raise FinderError("TAVILY_API_KEY is not set in .env")
    try:
        r = requests.post(TAVILY_URL, headers={"Authorization": f"Bearer {key}"}, timeout=30, json={
            "query": query, "search_depth": "basic", "max_results": max_results,
            "exclude_domains": BLOCKED[:25],
        })
    except requests.RequestException as e:
        log.warning("Tavily request failed for %r: %s", query, e)
        return []
    if r.status_code in (401, 403):
        raise FinderError("Tavily rejected the API key. Check TAVILY_API_KEY in .env.")
    if r.status_code in (429, 432, 433):
        raise FinderError(f"Tavily limit reached (HTTP {r.status_code}). Check your plan/credits.")
    if r.status_code >= 400:
        log.warning("Tavily error %s for %r: %s", r.status_code, query, r.text[:200])
        return []
    return r.json().get("results", [])


def _campaign_brief(c: Campaign) -> str:
    return (f"Goal: {c.goal_text or c.campaign_name}\nIndustry: {c.target_industry or 'any'}\n"
            f"Country: {c.target_country or 'any'}\nCompany size/stage: {c.target_company_size or 'any'}\n"
            f"Services we sell: {', '.join(c.target_services) or 'any'}\n"
            f"Ideal customer: {c.ideal_customer_profile or 'not specified'}")


def plan_queries(c: Campaign, previous: list[str], n: int) -> list[str]:
    sells_ai = _sells_ai(c)
    ban = (" Never use any of these words in a query, because they find vendors instead of customers: AI, artificial "
           "intelligence, machine learning, ML, LLM, GPT, chatbot, agent, copilot.") if sells_ai else ""
    out = structured(QueryPlan).invoke([
        ("system", (
            "You write web search queries that surface the WEBSITES of individual companies that could buy our service. "
            "Describe the kind of company the way it would describe itself on its homepage (what it makes, who it serves, "
            "its stage, country). Do NOT use words like 'top', 'best', 'list of', 'ranking' - those return articles. "
            "Cover different sub-segments and angles each time. Never repeat a previous query. "
            "IMPORTANT: target companies that do NOT already have the capability we sell. If we sell AI/software "
            "development, do not search for 'AI companies', 'AI startups' or LLM products: they already build it in-house. "
            "Instead search for non-AI-native businesses whose workflows are manual, data-heavy or support-heavy "
            "(vertical SaaS, fintech, healthtech, edtech, e-commerce, logistics, marketplaces, HR/recruiting tools, etc.). "
            "Prefer early-stage signals: pre-seed/seed/Series A, recently launched, small team, hiring first engineers. "
            "Make every query about a DIFFERENT industry vertical, e.g. 'seed stage <vertical> software startup <country>'." + ban)),
        ("human", f"{_campaign_brief(c)}\n\nPrevious queries (do not repeat):\n" + "\n".join(previous[-30:] or ["none"])
                  + f"\n\nWrite {n * 2} new queries."),
    ])
    seen = {p.lower() for p in previous}
    fresh = []
    for q in out.queries:
        q = q.strip()
        if sells_ai and AI_TERMS.search(q):
            continue  # code-level guard: AI wording attracts AI vendors and agencies
        if q and q.lower() not in seen:
            seen.add(q.lower())
            fresh.append(q)
    return fresh[:n]


def verify(c: Campaign, items: list[tuple[str, dict]]) -> dict[int, object]:
    """items: [(domain, tavily_result)] -> {index: Verdict}"""
    lines = [f"[{i}] {res['url']} | {res.get('title', '')} | {(res.get('content') or '')[:400]}"
             for i, (_, res) in enumerate(items)]
    out = structured(VerdictBatch).invoke([
        ("system", ("You screen search results for a sales campaign using ONLY the snippets given. This is a first, "
                    "wide-net screen: a later step scores each company in depth, so do NOT be overly strict. "
                    "is_single_company_site = the result is the website of one operating company (its homepage, product, "
                    "blog or docs pages count). fits_campaign = the company plausibly belongs to the target customer type "
                    "(right kind of business, stage and country if visible) and could plausibly use the service. Set "
                    "fits_campaign=false when it clearly does not fit: a competitor/agency selling the same service; a "
                    "company that already builds or sells the very capability we are selling (e.g. if we sell AI development, "
                    "reject AI-native companies and AI product startups: they have that expertise in-house); a well-funded "
                    "or large company (Series C or later, hundreds of employees, household name); or an unrelated business. "
                    "Missing details (stage, country) are NOT a reason to reject. Give a short reason for every verdict. Never invent facts.")),
        ("human", f"{_campaign_brief(c)}\n\nCandidates:\n" + "\n".join(lines) + "\n\nReturn one verdict per candidate."),
    ])
    verdicts = out.verdicts
    if len(verdicts) == len(items) and {v.index for v in verdicts} != set(range(len(items))):
        return {i: v for i, v in enumerate(verdicts)}  # model numbered them differently (e.g. from 1): use order
    return {v.index: v for v in verdicts if 0 <= v.index < len(items)}


def _excluded_domains(campaign: Campaign) -> set[str]:
    domains = set(Lead.objects.filter(campaign=campaign).values_list("domain", flat=True))
    domains |= set(Lead.objects.filter(outreach__sent_status="sent").values_list("domain", flat=True))  # already contacted
    domains |= {e.split("@")[-1].lower() for e in OptOut.objects.values_list("email", flat=True)}
    return domains


def find_leads(campaign_id: int) -> dict:
    campaign = Campaign.objects.get(id=campaign_id)
    run = FinderRun.objects.create(campaign=campaign)
    previous = [q for r in campaign.finder_runs.exclude(id=run.id) for q in r.queries]
    seen = _excluded_domains(campaign)
    target = campaign.target_lead_count
    rejections: list[str] = []
    try:
        for _ in range(settings.FINDER_MAX_ROUNDS):
            if target - campaign.leads.count() <= 0:
                break
            queries = plan_queries(campaign, previous, settings.FINDER_QUERIES_PER_ROUND)
            if not queries:
                run.detail = "The planner could not produce new queries."
                break
            candidates: dict[str, dict] = {}
            for q in queries:
                previous.append(q)
                run.queries.append(q)
                for res in tavily_search(q, settings.FINDER_RESULTS_PER_QUERY):
                    domain = normalize_domain(res.get("url", ""))
                    if domain and not is_blocked(domain) and domain not in seen and domain not in candidates:
                        candidates[domain] = res
            items = list(candidates.items())
            for start in range(0, len(items), VERIFY_BATCH):
                need = target - campaign.leads.count()
                if need <= 0:
                    break
                chunk = items[start:start + VERIFY_BATCH]
                verdicts = verify(campaign, chunk)
                for i, (domain, res) in enumerate(chunk):
                    seen.add(domain)
                    run.candidates_checked += 1
                    v = verdicts.get(i)
                    if need <= 0:
                        continue
                    if not v:
                        if len(rejections) < 25:
                            rejections.append(f"{domain}: no verdict returned")
                        continue
                    if not (v.is_single_company_site and v.fits_campaign):
                        if len(rejections) < 25:
                            rejections.append(f"{domain}: single_company={v.is_single_company_site} fits={v.fits_campaign} - {v.reason[:140]}")
                        continue
                    p = urlparse(res["url"])
                    _, created = Lead.objects.get_or_create(
                        campaign=campaign, domain=domain,
                        defaults=dict(company_name=(v.company_name or domain)[:255], website=f"{p.scheme}://{p.netloc}",
                                      industry=v.industry[:200], country=v.country[:100],
                                      company_description=v.description, source_url=res["url"]))
                    if created:
                        run.leads_created += 1
                        need -= 1
                run.save()  # progress is visible live in the admin
            run.save()
        run.status = "done"
    except FinderError as e:
        run.status, run.detail = "error", str(e)
    except Exception as e:  # LLM/network surprises should not leave a run stuck as 'running'
        log.exception("Lead finder crashed")
        run.status, run.detail = "error", f"{type(e).__name__}: {e}"[:500]
    total = campaign.leads.count()
    if run.status == "done" and total < target and not run.detail:
        run.detail = f"Found {total} of {target}. Run again to try new queries."
    if rejections and run.leads_created < target:
        run.detail = (run.detail + "\n\nSample of rejected candidates:\n" + "\n".join(rejections)).strip()
    run.finished_at = timezone.now()
    run.save()
    return {"status": run.status, "leads_created": run.leads_created, "total_leads": total, "target": target,
            "candidates_checked": run.candidates_checked, "detail": run.detail}