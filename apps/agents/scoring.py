"""Lead scoring. The LLM only produces per-criterion sub-scores (0..1);
the weighted total is computed here so it is deterministic and tunable."""
WEIGHTS = {
    "industry_relevance": 20,
    "business_fit": 20,
    "identified_requirement": 30,
    "service_relevance": 20,
    "contact_availability": 10,
}


def compute_score(sub: dict, has_contact: bool) -> int:
    parts = dict(sub)
    parts["contact_availability"] = 1.0 if has_contact else 0.0
    total = sum(WEIGHTS[k] * max(0.0, min(1.0, float(parts.get(k, 0)))) for k in WEIGHTS)
    return round(total)


def priority_for(score: int) -> str:
    return "high" if score >= 80 else "medium" if score >= 60 else "low"
