"""Step 4 - find decision-makers: named people + job titles from the company's OWN pages.
Emails are never guessed: a person only gets an email if one found on the site clearly belongs to them."""
import re

from pydantic import BaseModel, Field

from apps.core.models import Contact

from .llm import structured as default_structured

KEY_TITLE = re.compile(r"founder|owner|ceo|cto|cio|coo|cmo|cpo|chief|president|managing director|vp\b|"
                       r"vice president|head of|director|partner", re.I)


class Person(BaseModel):
    name: str = Field(description="Full name exactly as written on the page")
    title: str = Field(description="Job title exactly as written on the page")


class PeopleOut(BaseModel):
    people: list[Person] = []


def email_matches(email: str, name: str) -> bool:
    """True if a mailbox like jane@ / jane.doe@ / jdoe@ clearly belongs to this person."""
    lp = email.split("@")[0].lower()
    parts = [re.sub(r"[^a-z]", "", p.lower()) for p in name.split() if p]
    if len(parts) < 2:
        return False
    first, last = parts[0], parts[-1]
    return lp in {first, f"{first}.{last}", f"{first}{last}", f"{first[0]}{last}", f"{first[0]}.{last}",
                  f"{first}_{last}", f"{first}-{last}", f"{last}.{first}", f"{first}{last[0]}"}


def extract_people(text: str, llm=default_structured) -> list[tuple[str, str]]:
    out = llm(PeopleOut).invoke([
        ("system", "Extract people who are explicitly named WITH a job title in the text (founders, executives, "
                   "department heads). Copy names and titles exactly as written. Do not guess or infer. "
                   "Return an empty list if there are none."),
        ("human", text),
    ])
    haystack = re.sub(r"\s+", " ", text.lower())
    people = []
    for p in out.people:
        name, title = " ".join(p.name.split()), " ".join(p.title.split())
        # anti-hallucination: a full name must literally appear on the page, and the title must look like a decision-maker
        if len(name.split()) >= 2 and name.lower() in haystack and KEY_TITLE.search(title):
            people.append((name, title))
    return people[:8]


def store_people(lead, people: list[tuple[str, str]]) -> int:
    emails = list(lead.contacts.exclude(email="").values_list("id", "email"))
    created = 0
    for name, title in people:
        match = next((cid for cid, e in emails if email_matches(e, name)), None)
        if match:
            Contact.objects.filter(id=match).update(contact_name=name, job_title=title)
        else:  # name only: a BDE can find the verified email later
            _, was_created = Contact.objects.get_or_create(
                lead=lead, contact_name=name, email="",
                defaults=dict(job_title=title, verification_status="name_only", source_url=lead.website))
            created += was_created
    return created