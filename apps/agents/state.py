from typing import Literal, TypedDict


class SalesState(TypedDict, total=False):
    campaign_id: int
    lead_id: int
    research_ok: bool
    score: int
    priority: str
    exclude: bool
    outreach_id: int
    approval: Literal["approved", "rejected"]
    approver_id: int | None
    edited_subject: str | None
    edited_body: str | None
