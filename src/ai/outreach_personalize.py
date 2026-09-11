"""Unique outreach DMs via DeepSeek — based on the shared contact-form template."""

from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass

from src.ai.llm import LLMError, generate
from src.freelancermap.outreach_templates import first_name, render_outreach_body

logger = logging.getLogger(__name__)

# Bracket / angle placeholders users (or models) sometimes leave in templates.
_PLACEHOLDER_RE = re.compile(
    r"[\[\(\{<]\s*(?:specific\s+)?(?:detail|reason|experience|skill|something)"
    r"[^\]\)\}>]*[\]\)\}>]|"
    r"\[\s*[^\]]{3,80}\s*\]",
    re.IGNORECASE,
)

_ROLE_HINT = re.compile(
    r"\b(senior|lead|principal|staff|junior|engineer|developer|architect|"
    r"consultant|manager|designer|devops|backend|frontend|fullstack|"
    r"full[\s-]?stack|scientist|analyst|mlops|software|python|java|"
    r"react|angular|vue|fastapi|django|next\.?js|node|cloud|data|"
    r"mobile|ios|android|qa|tester|scrum|product|cto|freelancer)\b",
    re.I,
)

_DETAIL_SYSTEM = """You write one short phrase that explains why a recruiter is contacting a
freelancer, grounded only in their technical experience from the freelancermap profile.

Rules:
- Output ONLY the phrase (no quotes, no Hello, no full email).
- 6–18 words. Natural English.
- MUST be about technical skills, stack, role, domain, or past project work.
- NEVER mention the person's name, city, country, or location.
- Never invent employers, years, or skills not present in the facts.
- Never use brackets or placeholders.
- If facts are too thin, output exactly: your technical background"""

_BODY_SYSTEM = """You are a recruiter writing a one-off freelancermap contact DM.

You receive:
1) A SHARED contact-form template (the offer / facts to keep)
2) This freelancer's profile facts

Write ONE complete DM body for this freelancer only.

Hard rules:
- Keep the same offer: project type, tech stack, remote, hours/month, start timing,
  ask for availability, and the same sign-off name if present in the template.
- Do NOT invent salary, company secrets, or skills the freelancer does not have.
- Personalize the reason for writing using their technical experience / stack / role
  (never their personal name as the reason, never city/country as the reason).
- Greeting: use their first name when available (Hello FirstName,).
- Vary wording, sentence order, and phrasing so this message is clearly NOT a
  copy-paste of the template or of other DMs. Unique every time.
- Professional, concise (roughly 80–180 words). Plain text only.
- No markdown, no bullet lists unless the template uses them.
- No placeholders, brackets, {name}, {detail}, or phrases like
  "[specific detail from their profile]".
- Output ONLY the message body — no subject line, no commentary, no quotes around it.
"""


def _clip(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= n:
        return text
    return text[: n - 1].rstrip() + "…"


def has_title_separator(text: str) -> bool:
    return bool(re.search(r"[|■▪▫●•]", text or ""))


def looks_like_person_name(text: str) -> bool:
    s = re.sub(r"\s+", " ", (text or "").strip())
    if not s or len(s) > 60:
        return False
    if _ROLE_HINT.search(s) or has_title_separator(s):
        return False
    if re.search(r"\d|[/\\|@]", s):
        return False
    parts = s.split()
    if not (2 <= len(parts) <= 4):
        return False
    caps = sum(1 for p in parts if p[:1].isupper())
    return caps >= len(parts) - 1


def looks_like_job_title(text: str) -> bool:
    s = (text or "").strip()
    if not s or len(s) < 8 or looks_like_person_name(s):
        return False
    if has_title_separator(s) and len(s) >= 12:
        return True
    return bool(_ROLE_HINT.search(s))


def looks_like_location(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False
    low = s.lower()
    if looks_like_job_title(s) or looks_like_person_name(s):
        return False
    if re.search(
        r"\b(pakistan|india|germany|hungary|spain|france|italy|remote|"
        r"united|uk|cyprus|poland|lahore|berlin|london|munich|warsaw|"
        r"amsterdam|dubai|karachi|islamabad)\b",
        low,
    ):
        return True
    return bool(re.match(r"^[A-Za-z.\- ]+,\s*[A-Za-z.\- ]+$", s) and len(s) < 50)


def sanitize_title(title: str, display_name: str = "") -> str:
    t = (title or "").strip()
    if not t:
        return ""
    if looks_like_person_name(t) or looks_like_location(t):
        return ""
    name = (display_name or "").strip().lower()
    if name and t.lower() == name:
        return ""
    if name and name in t.lower() and not _ROLE_HINT.search(t):
        return ""
    return t


def strip_placeholders(text: str, *, fallback: str = "your technical background") -> str:
    out = text or ""
    out = out.replace("{detail}", fallback)
    out = out.replace("{DETAIL}", fallback)
    out = out.replace("{name}", "there")
    out = _PLACEHOLDER_RE.sub(fallback, out)
    out = re.sub(r"[^\S\n]{2,}", " ", out)
    out = re.sub(r"[^\S\n]+([,.;:])", r"\1", out)
    out = re.sub(r"specifically[^\S\n]+[—–-][^\S\n]+", "specifically ", out, flags=re.I)
    return out.strip()


def detail_is_bad(detail: str, *, display_name: str = "") -> bool:
    d = (detail or "").strip()
    if not d or len(d) < 8 or len(d) > 140:
        return True
    low = d.lower()
    if "[" in d or "]" in d or "{" in d:
        return True
    if "specific detail" in low or "their profile" in low or low.startswith("here"):
        return True
    if re.search(r"\byour (profile )?based in\b", low):
        return True
    if re.search(
        r"\b(lahore|karachi|islamabad|pakistan|berlin|germany)\b", low
    ) and not _ROLE_HINT.search(d):
        return True
    name = (display_name or "").strip()
    if name:
        if name.lower() in low:
            return True
        for part in name.split():
            if len(part) >= 3 and re.search(
                rf"\bas\s+{re.escape(part)}\b", low, re.I
            ):
                return True
    m = re.search(r"\byour work as\s+(.+)$", d, flags=re.I)
    if m and looks_like_person_name(m.group(1).split("(")[0].strip()):
        return True
    return False


def fallback_detail(
    *,
    title: str = "",
    location: str = "",
    skills: str = "",
    display_name: str = "",
    experience: str = "",
) -> str:
    t = sanitize_title(title, display_name)
    sk = (skills or "").strip()
    exp = (experience or "").strip()

    skill_parts = [s.strip() for s in re.split(r"[,|/]", sk) if s.strip()][:4]
    if not skill_parts and exp:
        tech = re.findall(
            r"\b(?:FastAPI|Django|Flask|Next\.?js|React|Angular|Vue|Node\.?js|"
            r"TypeScript|Python|PostgreSQL|Stripe|AWS|Docker|Kubernetes|"
            r"GraphQL|MongoDB|Redis|Spring|Laravel|Rails)\b",
            exp,
            flags=re.I,
        )
        skill_parts = []
        for x in tech:
            if x not in skill_parts:
                skill_parts.append(x)
            if len(skill_parts) >= 3:
                break

    if t and skill_parts:
        return f"your experience as {t} with {', '.join(skill_parts)}"
    if skill_parts:
        return f"your experience with {', '.join(skill_parts)}"
    if t and looks_like_job_title(t):
        return f"your background as {t}"
    found = _ROLE_HINT.search(exp or t)
    if found:
        return f"your {found.group(0).lower()} experience"
    _ = location
    return "your technical background"


def generate_outreach_detail(
    *,
    display_name: str = "",
    title: str = "",
    location: str = "",
    skills: str = "",
    experience: str = "",
    project_hint: str = "",
) -> str:
    clean_title = sanitize_title(title, display_name)
    fb = fallback_detail(
        title=clean_title,
        location=location,
        skills=skills,
        display_name=display_name,
        experience=experience,
    )
    facts = {
        "name": _clip(display_name, 80),
        "title": _clip(clean_title, 200),
        "skills": _clip(skills, 400),
        "experience_excerpt": _clip(experience, 1200),
        "project_context": _clip(project_hint, 300),
    }
    if not any(facts[k] for k in ("title", "skills", "experience_excerpt")):
        return fb

    user = f"""Profile facts (technical fields only for the reason):
- Person name (DO NOT use in the phrase): {facts['name'] or '(unknown)'}
- Job title / headline: {facts['title'] or '(none)'}
- Skills: {facts['skills'] or '(none)'}
- Experience / about / projects excerpt: {facts['experience_excerpt'] or '(none)'}
- Our project (optional context): {facts['project_context'] or '(billing / FastAPI / Next.js contract)'}

Write the technical detail phrase now:"""

    try:
        raw = generate(_DETAIL_SYSTEM, user, max_tokens=120, temperature=0.55)
    except LLMError as e:
        logger.warning("Outreach detail LLM failed: %s", e)
        return fb

    detail = (raw or "").strip().strip('"').strip("'")
    detail = re.sub(r"^(specifically|regarding|about)\s+", "", detail, flags=re.I)
    detail = strip_placeholders(detail, fallback="")
    detail = re.sub(r"\s+", " ", detail).strip(" .")
    if detail_is_bad(detail, display_name=display_name):
        return fb
    return detail


def _normalize_for_sim(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"\{[^}]+\}", " ", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _token_set(text: str) -> set[str]:
    return {w for w in _normalize_for_sim(text).split() if len(w) > 2}


def similarity_ratio(a: str, b: str) -> float:
    """Jaccard similarity over word tokens (0–1)."""
    sa, sb = _token_set(a), _token_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _required_facts_from_template(template: str) -> list[str]:
    """Pull concrete offer facts that the rewrite should preserve."""
    facts: list[str] = []
    for m in re.finditer(
        r"\b(FastAPI|Next\.?js|PostgreSQL|Stripe|React|Django|Python|TypeScript)\b",
        template or "",
        flags=re.I,
    ):
        tok = m.group(0)
        if tok.lower() not in {f.lower() for f in facts}:
            facts.append(tok)
    if re.search(r"\bremote\b", template or "", re.I):
        facts.append("remote")
    return facts[:6]


def body_is_valid(
    body: str,
    *,
    template: str,
    display_name: str = "",
) -> bool:
    text = (body or "").strip()
    if len(text) < 120 or len(text) > 2500:
        return False
    if not re.match(r"^(hello|hi|hey|dear)\b", text, flags=re.I):
        return False
    if "{" in text or "}" in text:
        return False
    if re.search(r"specific detail from their profile", text, re.I):
        return False
    if re.search(r"\[[^\]]{3,80}\]", text):
        return False
    # Must not be nearly identical to the shared template
    if similarity_ratio(text, template) > 0.92:
        return False
    # Preserve most key tech/offer facts from the template (allow 1 miss)
    required = _required_facts_from_template(template)
    if required:
        hits = 0
        for fact in required:
            if fact.lower() == "remote":
                if re.search(r"\bremote\b", text, re.I):
                    hits += 1
            else:
                alt = fact.replace(".", r"\.?")
                if re.search(alt, text, re.I):
                    hits += 1
        if hits < max(1, len(required) - 1):
            return False
    if display_name:
        for part in display_name.split():
            if len(part) >= 3 and re.search(
                rf"\bas\s+{re.escape(part)}\b", text, re.I
            ):
                return False
    return True


def _clean_generated_body(raw: str, *, display_name: str, detail: str) -> str:
    text = strip_placeholders((raw or "").strip().strip('"').strip("'"), fallback=detail)
    # Drop accidental "Subject:" / "Body:" prefixes
    text = re.sub(r"^(subject|body|message)\s*:\s*", "", text, flags=re.I).strip()
    text = re.sub(r"^```\w*\n?", "", text).strip()
    text = re.sub(r"\n?```$", "", text).strip()
    fn = first_name(display_name)
    if fn and fn != "there" and re.match(r"^Hello\s*,", text, flags=re.I):
        text = re.sub(r"^Hello\s*,", f"Hello {fn},", text, count=1, flags=re.I)
    return text.strip()


@dataclass
class OutreachPersonalization:
    detail: str
    body: str
    subject: str | None
    source: str  # deepseek | fallback


def _fallback_message(
    *,
    display_name: str,
    title: str,
    location: str,
    skills: str,
    experience: str,
    subject: str,
    message_body: str,
) -> OutreachPersonalization:
    detail = fallback_detail(
        title=title,
        location=location,
        skills=skills,
        display_name=display_name,
        experience=experience,
    )
    template = (message_body or "").strip()
    if template:
        out = template
        if "{name}" in out:
            out = out.replace("{name}", first_name(display_name))
        if "{detail}" in out:
            out = out.replace("{detail}", detail)
        out = strip_placeholders(out, fallback=detail)
        body = out
    else:
        body = render_outreach_body(full_name=display_name, detail=detail)
    subj = (subject or "").strip()
    if subj and "{name}" in subj:
        subj = subj.replace("{name}", first_name(display_name))
    return OutreachPersonalization(
        detail=detail, body=body, subject=subj or None, source="fallback"
    )


def generate_unique_outreach_dm(
    *,
    display_name: str = "",
    title: str = "",
    location: str = "",
    skills: str = "",
    experience: str = "",
    subject: str = "",
    message_body: str = "",
    project_hint: str = "",
) -> OutreachPersonalization:
    """
    Rewrite the shared contact-form template into a unique DM for this freelancer.
    Uses DeepSeek; falls back to filled template if generation fails validation.
    """
    clean_title = sanitize_title(title, display_name)
    detail = generate_outreach_detail(
        display_name=display_name,
        title=clean_title,
        location=location,
        skills=skills,
        experience=experience,
        project_hint=project_hint or subject,
    )
    fb = _fallback_message(
        display_name=display_name,
        title=clean_title,
        location=location,
        skills=skills,
        experience=experience,
        subject=subject,
        message_body=message_body,
    )
    # Prefer the detail we just generated in the fallback body too
    if message_body:
        fb_body = message_body
        if "{name}" in fb_body:
            fb_body = fb_body.replace("{name}", first_name(display_name))
        if "{detail}" in fb_body:
            fb_body = fb_body.replace("{detail}", detail)
        fb = OutreachPersonalization(
            detail=detail,
            body=strip_placeholders(fb_body, fallback=detail),
            subject=fb.subject,
            source="fallback",
        )

    template = (message_body or "").strip() or fb.body
    fn = first_name(display_name)
    nonce = secrets.token_hex(3)

    user = f"""Variation seed: {nonce} (use this only to diversify wording — do not print it)

SHARED contact-form template (keep the offer facts; rewrite the wording):
---
{template}
---

Freelancer profile:
- First name for greeting: {fn if fn != 'there' else '(unknown — use Hello,)'}
- Full name (do not use as the contact reason): {_clip(display_name, 80) or '(unknown)'}
- Job title / headline: {_clip(clean_title, 200) or '(none)'}
- Skills: {_clip(skills, 400) or '(none)'}
- Experience / about / projects: {_clip(experience, 1200) or '(none)'}
- Suggested technical hook (optional, rephrase freely): {detail}

Subject line context (do not output the subject): {_clip(subject or project_hint, 200) or '(none)'}

Write the unique DM body now:"""

    temps = (0.85, 0.95)
    for temp in temps:
        try:
            raw = generate(_BODY_SYSTEM, user, max_tokens=700, temperature=temp)
        except LLMError as e:
            logger.warning("Unique outreach body LLM failed (temp=%s): %s", temp, e)
            continue
        body = _clean_generated_body(raw, display_name=display_name, detail=detail)
        if body_is_valid(body, template=template, display_name=display_name):
            subj = (subject or "").strip() or None
            if subj and "{name}" in subj:
                subj = subj.replace("{name}", fn)
            return OutreachPersonalization(
                detail=detail,
                body=body,
                subject=subj,
                source="deepseek",
            )
        logger.info(
            "Unique outreach body rejected (temp=%s, sim=%.2f, len=%d)",
            temp,
            similarity_ratio(body, template),
            len(body),
        )

    logger.warning("Falling back to filled template for %s", display_name or "?")
    return fb
