# Webfolks Sales Lead Generation & Follow-up Agent (backend MVP)

Django + DRF API, LangGraph per-lead pipeline, Celery for async/scheduled work, Groq LLM.

```
research -> (contacts found on site) -> qualify -> route by score -> draft -> [BDE approval interrupt] -> send -> follow-ups
```

## Run (dev)
```bash
pip install -r requirements.txt
cp .env.example .env        # set GROQ_API_KEY
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```
Dev defaults: SQLite, Celery runs inline (`CELERY_EAGER=1`), emails are logged not sent (`EMAIL_PROVIDER=console`), LangGraph checkpoints in memory.

**Prod:** set `POSTGRES_*` (also stores LangGraph checkpoints so approvals can wait days), `CELERY_EAGER=0`, run `celery -A config worker` and `celery -A config beat`, `EMAIL_PROVIDER=gmail` with a token file.

## Try it
```bash
TOKEN=$(curl -s -X POST localhost:8000/api/auth/login/ -d username=admin -d password=... | jq -r .token)
curl -H "Authorization: Token $TOKEN" -X POST localhost:8000/api/campaigns/ -H 'Content-Type: application/json' \
  -d '{"campaign_name":"US SaaS - AI","target_industry":"SaaS","target_country":"US","target_services":["AI Solutions and AI Agent Development"]}'
curl -H "Authorization: Token $TOKEN" -X POST localhost:8000/api/campaigns/1/import-csv/ -F file=@leads.csv   # company_name,website,industry,country,description,source_url
curl -H "Authorization: Token $TOKEN" -X POST localhost:8000/api/campaigns/1/start/
curl -H "Authorization: Token $TOKEN" "localhost:8000/api/outreach/"                 # drafts awaiting approval
curl -H "Authorization: Token $TOKEN" -X POST localhost:8000/api/outreach/1/approve/ # optional JSON: subject, body (edits)
```

## Built-in safety rules
- Nothing is sent without BDE approval; approved text can be edited at approval time.
- Emails are only taken from the company's own site (mailto/visible text), never guessed.
- robots.txt is honoured when scraping.
- No duplicate sends; duplicate leads blocked per campaign by domain.
- Reply / opt-out / not-interested / disqualified automatically cancel pending follow-ups; opt-outs are re-checked at send time.
- Follow-ups need approval by default (Level 2). `FOLLOWUP_AUTOSEND=1` enables Level 3.
- Every node run is logged in `AgentRun`.

## Gaps / next steps
- Discovery is CSV import; plug a provider into `apps/core/discovery.py`.
- Contacts have emails only; extend `research_node` to extract names/titles from About/Team pages.
- Gmail: create OAuth token with send + readonly scopes (`gmail_token.json`) for sending and reply polling.
- Lead assignment endpoint, Level-2 permission tightening, and the frontend.
