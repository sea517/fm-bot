import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"

# override=True so .env wins over empty shell exports of the same keys
load_dotenv(ROOT_DIR / ".env", override=False)


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _optional(name: str, default: str = "") -> str:
    return os.getenv(name, default)


FREELANCERMAP_EMAIL = _optional("FREELANCERMAP_EMAIL")
FREELANCERMAP_PASSWORD = _optional("FREELANCERMAP_PASSWORD")
FREELANCERMAP_BASE_URL = _optional("FREELANCERMAP_BASE_URL", "https://www.freelancermap.de")

SLACK_BOT_TOKEN = _optional("SLACK_BOT_TOKEN")
# Oliver's user OAuth token so assessment chat posts as Oliver, not the Kontrora app.
# Accepts classic xoxp-... or rotated xoxe.xoxp-1-... tokens (user scope chat:write).
SLACK_USER_TOKEN = _optional("SLACK_USER_TOKEN").strip().strip('"').strip("'")
# Required when SLACK_USER_TOKEN is a rotating xoxe.xoxp- token (expires ~12h).
SLACK_USER_REFRESH_TOKEN = _optional("SLACK_USER_REFRESH_TOKEN").strip().strip('"').strip("'")
SLACK_CLIENT_ID = _optional("SLACK_CLIENT_ID").strip().strip('"').strip("'")
SLACK_CLIENT_SECRET = _optional("SLACK_CLIENT_SECRET").strip().strip('"').strip("'")
SLACK_TEAM_ID = _optional("SLACK_TEAM_ID")
# Recruiters added to each candidate's private channel (Slack user IDs, comma-separated)
# Include Oliver's user id here so his messages are never treated as candidate replies.
SLACK_TEAM_MEMBER_IDS = [
    u.strip() for u in _optional("SLACK_TEAM_MEMBER_IDS").split(",") if u.strip()
]

FREELANCERMAP_MESSAGES_URL = _optional("FREELANCERMAP_MESSAGES_URL")

# Primary mailbox login (aliases like oliver@ / sabrina@ cannot authenticate via IMAP)
INBOX_EMAIL = _optional("INBOX_EMAIL", "info@kontrora.com")  # IMAP/SMTP login user
# Address shown on outgoing replies (alias is fine for From if allowed by provider)
INBOX_FROM_EMAIL = _optional("INBOX_FROM_EMAIL", "oliver@kontrora.com")
INBOX_FROM_NAME = _optional("INBOX_FROM_NAME", "Oliver")
# Keep Namecheap app-password hyphens (stripping them breaks IMAP)
INBOX_PASSWORD = _optional("INBOX_PASSWORD").strip().strip('"').strip("'")
INBOX_IMAP_HOST = _optional("INBOX_IMAP_HOST", "mail.privateemail.com")
INBOX_SMTP_HOST = _optional("INBOX_SMTP_HOST", "mail.privateemail.com")
INBOX_SMTP_PORT = int(_optional("INBOX_SMTP_PORT", "465"))

# HTTPS email API — used instead of SMTP when a key is set. Needed on networks
# that block outbound SMTP ports (465/587/25); these APIs run over port 443.
# Provider: resend | sendgrid | brevo
EMAIL_API_PROVIDER = _optional("EMAIL_API_PROVIDER", "resend").strip().lower()
EMAIL_API_KEY = _optional("EMAIL_API_KEY").strip().strip('"').strip("'")


def email_api_enabled() -> bool:
    return bool(EMAIL_API_KEY)

GEMINI_API_KEY = _optional("GEMINI_API_KEY")
GEMINI_MODEL = _optional("GEMINI_MODEL", "gemini-3.6-flash")

# OpenRouter (OpenAI-compatible). Used automatically when a key is present,
# unless AI_PROVIDER is set explicitly to "gemini" or "openrouter".
OPENROUTER_API_KEY = _optional("OPENROUTER_API_KEY").strip().strip('"').strip("'")
OPENROUTER_MODEL = _optional("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash-0731")
OPENROUTER_BASE_URL = _optional("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
AI_PROVIDER = _optional("AI_PROVIDER", "openrouter").strip().lower()


def active_ai_provider() -> str:
    if AI_PROVIDER in ("gemini", "openrouter"):
        return AI_PROVIDER
    return "openrouter" if OPENROUTER_API_KEY else "gemini"

# GitHub repo collaborator invites (use Personal Access Token, not password)
GITHUB_TOKEN = _optional("GITHUB_TOKEN")
GITHUB_REPO = _optional("GITHUB_REPO", "creativesolution999/full-stack-assignment")
# SPEC secrets (org invite + assessment repo). Fall back to GITHUB_REPO owner/name.
GITHUB_ORG = _optional("GITHUB_ORG", "").strip() or (
    GITHUB_REPO.split("/", 1)[0] if GITHUB_REPO and "/" in GITHUB_REPO else ""
)
ASSESSMENT_REPO = _optional("ASSESSMENT_REPO", "").strip() or (
    GITHUB_REPO.split("/", 1)[1] if GITHUB_REPO and "/" in GITHUB_REPO else ""
)

# Telegram handoff alerts (numeric chat id, not a username)
TELEGRAM_BOT_TOKEN = _optional("TELEGRAM_BOT_TOKEN").strip().strip('"').strip("'")
TELEGRAM_CHAT_ID = _optional("TELEGRAM_CHAT_ID").strip().strip('"').strip("'")

# Job posting from .env
PROJECT_TITLE = _optional(
    "PROJECT_TITLE",
    "Staff Engineer (Contract) — Revenue & Billing Systems on a Live PSA Platform",
)
PROJECT_DESCRIPTION_FILE = _optional(
    "PROJECT_DESCRIPTION_FILE",
    str(DATA_DIR / "descriptions" / "staff-engineer-revenue-billing.txt"),
)
PROJECT_EXTERNAL_ID = _optional("PROJECT_EXTERNAL_ID", "kontrora-staff-revenue-001")
PROJECT_DURATION_MONTHS = int(_optional("PROJECT_DURATION_MONTHS", "1"))

POLL_INTERVAL_SECONDS = int(_optional("POLL_INTERVAL_SECONDS", "10800"))
# Mon–Fri 10:00–17:00 America/New_York: Slack poll cadence while assessments are active.
SLACK_POLL_INTERVAL_SECONDS = int(_optional("SLACK_POLL_INTERVAL_SECONDS", "45"))
# Outside that window: Slack is only included in the slower full cycle.
SLACK_OFF_HOURS_POLL_SECONDS = int(
    _optional("SLACK_OFF_HOURS_POLL_SECONDS", str(3 * 60 * 60))
)
DATABASE_URL = _optional("DATABASE_URL", f"sqlite:///{DATA_DIR / 'bot.db'}")

# Supabase — ledger of freelancers already contacted on freelancermap (optional).
# Create table via supabase/contacted_freelancers.sql, then set:
SUPABASE_URL = _optional("SUPABASE_URL").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = _optional("SUPABASE_SERVICE_ROLE_KEY").strip()

# Control plane (dashboard on Vercel + Chrome extension workers)
DASHBOARD_API_TOKEN = _optional("DASHBOARD_API_TOKEN").strip()
BOT_TOKENS: dict[int, str] = {
    1: _optional("BOT_1_TOKEN").strip(),
    2: _optional("BOT_2_TOKEN").strip(),
    3: _optional("BOT_3_TOKEN").strip(),
}
CONTROL_HOST = _optional("CONTROL_HOST", "0.0.0.0")
CONTROL_PORT = int(_optional("CONTROL_PORT", "8090"))

# Outbound DMs on freelancermap.com freelancer search
OUTREACH_SEARCH_URL = _optional(
    "OUTREACH_SEARCH_URL", "https://www.freelancermap.com/freelancer"
).strip()
# Exact (or unique substring) label of the project in the Contact form dropdown
OUTREACH_PROJECT_NAME = _optional("OUTREACH_PROJECT_NAME").strip()

FEED_HOST = _optional("FEED_HOST", "0.0.0.0")
FEED_PORT = int(_optional("FEED_PORT", "8080"))
FEED_PUBLIC_URL = _optional("FEED_PUBLIC_URL")

SESSION_DIR = DATA_DIR / "browser_session"
JOBS_FILE = DATA_DIR / "jobs.json"
FEED_OUTPUT = DATA_DIR / "feed.xml"
