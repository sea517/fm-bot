"""Outbound freelancermap.com DM outreach templates."""

from __future__ import annotations

import re

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
    if not parts:
        return "there"
    first = parts[0]
    # Avoid "Hello Full," when a job title was misread as the name
    if re.search(
        r"\b(senior|lead|engineer|developer|architect|full[\s-]?stack|software)\b",
        full_name or "",
        re.I,
    ) and len(parts) <= 3:
        return "there"
    if first.lower() in {"full", "senior", "lead", "principal", "staff", "junior"}:
        return "there"
    return first


def render_outreach_body(*, full_name: str, detail: str) -> str:
    from src.ai.outreach_personalize import strip_placeholders

    d = (detail or "your technical background").strip()
    d = strip_placeholders(d, fallback="your technical background")
    if re.search(r"specific detail from their profile", d, re.I) or "[" in d:
        d = "your technical background"
    body = OUTREACH_BODY.format(
        name=first_name(full_name),
        detail=d,
    )
    return strip_placeholders(body, fallback=d)
