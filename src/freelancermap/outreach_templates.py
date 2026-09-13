"""Outbound freelancermap.com DM outreach templates."""

from __future__ import annotations

import re

OUTREACH_SUBJECT = (
    "FastAPI/Next.js billing module – remote contract, about 1 month"
)

OUTREACH_BODY = """Hello {name},

I found your profile on freelancermap. Your work with {detail} is relevant to this role.

We need a contractor for the billing and revenue layer of a live multi-tenant PSA platform. The stack is FastAPI, Next.js, PostgreSQL, and Stripe. The work is remote. Plan for about 80 to 100 hours each month. Start date is 1 October.

The project brief is attached. If you can do this work, reply with your availability. Then we can set a technical call.

Best regards,
David
"""


def first_name(full_name: str) -> str:
    parts = (full_name or "").strip().split()
    if not parts:
        return "there"
    first = parts[0]
    # Avoid "Hello Full," / "Hello Only," when UI chrome was misread as the name
    blocked = {
        "full",
        "senior",
        "lead",
        "principal",
        "staff",
        "junior",
        "only",
        "remote",
        "available",
        "verified",
        "premium",
        "contact",
        "watchlist",
        "find",
        "the",
        "freelancer",
        "profile",
        "hello",
        "dear",
        "hi",
        "hey",
    }
    if first.lower() in blocked:
        return "there"
    if re.search(
        r"\b(senior|lead|engineer|developer|architect|full[\s-]?stack|software|"
        r"only\s+remote|available)\b",
        full_name or "",
        re.I,
    ) and (len(parts) <= 3 or first.lower() in blocked):
        return "there"
    if not re.match(r"^[A-Za-zÀ-ÖØ-öø-ÿ'’-]+$", first):
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
