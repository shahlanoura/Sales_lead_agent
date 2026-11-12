"""Lead discovery. MVP = CSV import from one approved source.
Add a provider (Apollo/Hunter/directory API) by returning rows with the same keys."""
import csv
import io
from urllib.parse import urlparse

from .models import Lead


def normalize_domain(url: str) -> str:
    url = url.strip()
    if "//" not in url:
        url = "https://" + url
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def import_leads_rows(campaign, rows):
    created = skipped = 0
    for r in rows:
        website = (r.get("website") or "").strip()
        name = (r.get("company_name") or "").strip()
        if not website or not name:
            skipped += 1
            continue
        _, was_created = Lead.objects.get_or_create(
            campaign=campaign, domain=normalize_domain(website),
            defaults=dict(
                company_name=name,
                website=website if "//" in website else "https://" + website,
                industry=r.get("industry", ""), country=r.get("country", ""),
                company_description=r.get("description", ""), source_url=r.get("source_url", ""),
            ),
        )
        created += was_created
        skipped += not was_created
    return {"created": created, "skipped_duplicates_or_invalid": skipped}


def import_leads_csv(campaign, fileobj):
    text = fileobj.read().decode("utf-8-sig")
    return import_leads_rows(campaign, csv.DictReader(io.StringIO(text)))
