from pydantic import BaseModel, Field


class Opportunity(BaseModel):
    service: str = Field(description="Webfolks service this could relate to")
    rationale: str = Field(description="Why, grounded in the website text. This is a potential opportunity, not a confirmed need.")


class ResearchOut(BaseModel):
    business_summary: str
    business_model: str = ""
    technology_observations: list[str] = []
    opportunities: list[Opportunity] = []
    relevant_services: list[str] = []


class QualificationOut(BaseModel):
    industry_relevance: float = Field(ge=0, le=1)
    business_fit: float = Field(ge=0, le=1)
    identified_requirement: float = Field(ge=0, le=1)
    service_relevance: float = Field(ge=0, le=1)
    reasoning: str


class DraftOut(BaseModel):
    subject: str
    body: str


class GoalOut(BaseModel):
    campaign_name: str = Field(description="Short campaign title, e.g. 'US SaaS Startups - AI Development'")
    target_industry: str = Field(description="Industry/sector of the target companies; empty if not stated")
    target_country: str = Field(description="Target country or region; empty if not stated")
    target_company_size: str = Field(description="Company stage/size, e.g. 'early-stage startup'; empty if not stated")
    target_services: list[str] = Field(description="Webfolks services to pitch, copied EXACTLY from the allowed list")
    target_lead_count: int = Field(description="How many leads were asked for; 0 if no number was given")
    ideal_customer_profile: str = Field(description="2-3 sentences describing the ideal customer and why they would need the service")
    assumptions: list[str] = Field(default=[], description="Anything you had to assume because the goal did not say it")
    clarifying_questions: list[str] = Field(default=[], description="Questions to ask ONLY if the goal is too vague to run a campaign")


class QueryPlan(BaseModel):
    queries: list[str]


class Verdict(BaseModel):
    index: int = Field(description="The [number] of the candidate this verdict is for")
    is_single_company_site: bool = Field(description=(
        "True only if the result belongs to ONE specific operating company (its own website, blog or docs). "
        "False for news, lists/rankings, directories, marketplaces, job boards, social or profile pages."))
    fits_campaign: bool = Field(description="True if that company matches the campaign's target customer (industry, stage/size, country)")
    company_name: str = ""
    industry: str = ""
    country: str = Field(default="", description="Only if stated or clearly implied; otherwise empty")
    description: str = Field(default="", description="One or two factual sentences taken from the snippet; empty if unknown")
    reason: str = ""


class VerdictBatch(BaseModel):
    verdicts: list[Verdict]