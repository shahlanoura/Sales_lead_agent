import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-change-me")
DEBUG = os.environ.get("DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "apps.core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]

# ---- Database -------------------------------------------------------------
PG_DB = os.environ.get("POSTGRES_DB", "")
if PG_DB:
    PG = {
        "NAME": PG_DB,
        "USER": os.environ.get("POSTGRES_USER", "postgres"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
    DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", **PG}}
    PG_CONNINFO = (
        f"postgresql://{PG['USER']}:{PG['PASSWORD']}@{PG['HOST']}:{PG['PORT']}/{PG['NAME']}"
    )
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
    PG_CONNINFO = ""

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "Asia/Kolkata"
STATIC_URL = "static/"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.TokenAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
}

# ---- Celery ---------------------------------------------------------------
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_TASK_ALWAYS_EAGER = os.environ.get("CELERY_EAGER", "1") == "1"
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BEAT_SCHEDULE = {
    "process-due-followups": {"task": "apps.agents.tasks.process_due_followups", "schedule": 15 * 60},
    "poll-replies": {"task": "apps.agents.tasks.poll_replies", "schedule": 10 * 60},
}

# ---- Agent / integration config -------------------------------------------
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
FINDER_MAX_ROUNDS = int(os.environ.get("FINDER_MAX_ROUNDS", "6"))
FINDER_QUERIES_PER_ROUND = int(os.environ.get("FINDER_QUERIES_PER_ROUND", "5"))
FINDER_RESULTS_PER_QUERY = int(os.environ.get("FINDER_RESULTS_PER_QUERY", "10"))
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
EMAIL_PROVIDER = os.environ.get("EMAIL_PROVIDER", "console")
GMAIL_TOKEN_FILE = os.environ.get("GMAIL_TOKEN_FILE", "gmail_token.json")
GMAIL_SENDER = os.environ.get("GMAIL_SENDER", "")
FOLLOWUP_AUTOSEND = os.environ.get("FOLLOWUP_AUTOSEND", "0") == "1"
SENDER_NAME = os.environ.get("SENDER_NAME", "The Webfolks team")
COMPANY_NAME = os.environ.get("COMPANY_NAME", "Webfolks")
COMPANY_ADDRESS = os.environ.get("COMPANY_ADDRESS", "")  # postal address: required in commercial email footers
GMAIL_CLIENT_SECRET_FILE = os.environ.get("GMAIL_CLIENT_SECRET_FILE", "client_secret.json")
OPT_OUT_FOOTER = "\n\n--\nIf you'd prefer not to hear from us, just reply with 'unsubscribe' and we won't contact you again."
WEBFOLKS_SERVICES = [
    "Website Development", "Mobile Application Development",
    "AI Solutions and AI Agent Development", "Digital Marketing",
    "Dedicated Developers", "Custom Software Development",
]

