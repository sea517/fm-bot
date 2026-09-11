"""Outbound freelancermap.com DM outreach templates."""

OUTREACH_SUBJECT = (
    "FastAPI/Next.js billing module – remote contract, ~1 month"
)

OUTREACH_BODY = """Hello {name},

Your profile came up in my search on freelancermap — specifically {detail} — so I wanted to send this your way.

We're hiring a contractor to build the billing and revenue layer of a live multi-tenant PSA platform (FastAPI, Next.js, PostgreSQL, Stripe). Remote, ~80–100 hours/month, starting Oct 1st.

The project brief is attached. If you're open to it, reply with your availability and we'll arrange a technical conversation.

Best regards,
David
"""


def first_name(full_name: str) -> str:
    parts = (full_name or "").strip().split()
    return parts[0] if parts else "there"


def render_outreach_body(*, full_name: str, detail: str) -> str:
    return OUTREACH_BODY.format(
        name=first_name(full_name),
        detail=(detail or "your background").strip(),
    )
