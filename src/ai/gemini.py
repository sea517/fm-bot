import logging
import re
from collections import Counter

from src.ai.llm import LLMError
from src.ai.llm import generate as _llm_generate

logger = logging.getLogger(__name__)

ROLE_CONTEXT = """You are a professional recruiter for Kontrora hiring via freelancermap.

Always stay grounded in the project brief below. This role is Staff Engineer (contract) on a LIVE PSA platform, building the revenue/billing layer — not a greenfield AI SaaS build.

Kontrora already has production: multi-tenant architecture, 40+ permission RBAC, project and capacity management, workflow builder, time tracking, client portal, and LLM features on live project data.
The work to hire for: proposals, estimates, budgeting, Stripe/QuickBooks, tenancy/RBAC for financial data.

Match the applicant's language (German or English). No markdown. Do not say you are an AI."""

FIRST_REPLY_PROMPT = ROLE_CONTEXT + """

Write a short, friendly first reply to a freelancer who applied.

Rules:
- 2-4 sentences
- Thank them for applying
- Briefly acknowledge something specific from their message if possible, in the context of this billing/revenue role
- Do NOT mention Slack, email, or Oliver yet
- The LAST sentence must ask them to share their relevant projects or experience.
  Use wording close to one of these:
    "Please share your relevant projects."
    "Please share your resume or relevant experience."
  Do not ask for anything else and do not add any text after that request."""

FOLLOWUP_REPLY_PROMPT = ROLE_CONTEXT + """

Write a natural, brief follow-up (2-4 sentences) to keep the conversation moving professionally.
Stay on this role (revenue/billing, multi-tenant PSA, FastAPI/Next.js/PostgreSQL).
Do NOT mention Slack, email, or Oliver yet."""

SECOND_REPLY_PROMPT = ROLE_CONTEXT + """

The candidate has just described their projects or experience. Write the second recruiter reply.

Rules:
- 2-3 sentences
- React to what they actually wrote: name the specific project, system, or
  technology they mentioned
- The LAST sentence must ask them to expand on ONE concrete part of it,
  phrased like:
    "Could you tell me more about the <specific thing> part?"
  Replace <specific thing> with the real detail from their message, for example
  the billing integration part, the multi-tenant permissions part, the Stripe
  webhook part, the production migration part.
- Pick the part most relevant to revenue/billing, multi-tenancy, or RBAC
- Ask only ONE question, and end the message with it
- Do NOT mention Slack, email, Oliver, assignments, or interviews yet"""

TECH_FOLLOWUP_PROMPT = ROLE_CONTEXT + """

You are evaluating a candidate's answers to technical interview questions about this live PSA platform.
Ask ONE focused follow-up question based on their response.
Prefer probing: billing/payments (idempotency, webhooks, reconciliation), multi-tenant isolation, RBAC, or extending an existing production codebase.
Keep it concise (1-3 sentences)."""


def _generate(system: str, user: str) -> str:
    return _llm_generate(system, user)


def _clip(text: str, limit: int = 1800) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


_MORE_ASK_HINTS = (
    "tell me more",
    "tell us more",
    "more about",
    "walk me through",
    "walk us through",
    "elaborate",
    "mehr über",
    "mehr zu",
    "näher erläutern",
    "genauer beschreiben",
)


def _looks_like_garbage(text: str) -> bool:
    """Reject model output that is not a short recruiter message."""
    t = (text or "").strip()
    if not t:
        return True
    # Real first/second replies are 2–4 sentences; anything this long is a loop.
    if len(t) > 700:
        return True
    if t[0] in ",.;:)]}":
        return True

    low = t.lower()
    junk_markers = (
        "</",
        "<div",
        "<grid",
        "{self.",
        "gettablegettable",
        "password code",
        "xxxxxxxx",
        "template 1",
        "sleepworkshop",
        "datarace",
        "[email protected]",
        "end_of_sentence",
        "end▁of▁sentence",
        "<|",
        "|>",
        "## ",
        "### ",
        "reply on behalf",
        "the following text:",
        "i'm not a recruiter",
        "always-normal",
        "braille",
    )
    if any(m in low for m in junk_markers):
        return True
    if t.count(")") > 8 or t.count("{") > 3 or t.count("#") > 2:
        return True
    if t.count("[") > 3 or t.count("]") > 3:
        return True

    words = re.findall(r"[a-zA-ZäöüÄÖÜß]{3,}", t)
    if words:
        _word, count = Counter(w.lower() for w in words).most_common(1)[0]
        if count >= 6:
            return True
        # Repetitive filler phrases ("a good", "to be a")
        if low.count(" a good ") >= 3 or low.count(" to be ") >= 4:
            return True

    # Must look like a normal message: at least one sentence end, few newlines.
    if t.count("\n") > 6:
        return True
    if not re.search(r"[.!?]", t):
        return True
    return False


def _looks_like_recruiter_reply(text: str, kind: str) -> bool:
    """Positive check: first/second replies must sound like a recruiter."""
    t = (text or "").strip()
    if _looks_like_garbage(t):
        return False
    low = t.lower()
    if kind == "first":
        openers = ("thank", "thanks", "hi ", "hello", "danke", "vielen dank")
        if not any(low.startswith(o) or f" {o}" in f" {low[:80]}" for o in openers):
            # Allow name-first "Hi Name," style already covered by hi
            if not re.match(r"^(hi|hello|dear|hallo|guten)\b", low):
                return False
        if not any(h in low for h in ("project", "experience", "resume", "cv", "projekt", "erfahrung")):
            return False
    if kind == "second":
        if not any(h in low for h in _MORE_ASK_HINTS):
            return False
    return True


def _generate_clean(system: str, user: str, kind: str = "generic") -> str:
    last = ""
    for attempt in (1, 2, 3):
        last = _generate(system, user)
        if kind in ("first", "second"):
            ok = _looks_like_recruiter_reply(last, kind)
        else:
            ok = not _looks_like_garbage(last)
        if ok:
            return last
        logger.warning(
            "AI reply rejected as unusable (attempt %d, kind=%s, %d chars): %r",
            attempt,
            kind,
            len(last),
            last[:120],
        )
    raise LLMError("AI produced unusable recruiter text after 3 attempts")


def _brief(project_title: str, project_description: str) -> str:
    return f"""Project title: {project_title}

Full project brief:
{_clip(project_description, 2500)}
"""


SHARE_REQUEST_EN = "Please share your relevant projects."
SHARE_REQUEST_DE = "Bitte teilen Sie Ihre relevanten Projekte mit."

# Any of these in the closing sentence means the model already asked.
_SHARE_ASK_HINTS = (
    "share your",
    "send your",
    "send us your",
    "send me your",
    "tell us about your",
    "teilen sie",
    "senden sie",
    "schicken sie",
)
_SHARE_TOPIC_HINTS = (
    "project",
    "experience",
    "resume",
    "cv",
    "background",
    "projekt",
    "erfahrung",
    "lebenslauf",
    "werdegang",
)


def _looks_german(text: str) -> bool:
    lowered = f" {text.lower()} "
    markers = (" und ", " ihre ", " sie ", " wir ", " für ", " nicht ", " mit ")
    return sum(m in lowered for m in markers) >= 2


def _ensure_share_request(reply: str) -> str:
    """
    Guarantee the first reply closes by asking for projects/experience.

    The prompt asks for this, but models drop instructions, so append it when
    the closing sentence does not already make that request.
    """
    reply = (reply or "").strip()
    if not reply:
        return SHARE_REQUEST_EN

    tail = reply[-220:].lower()
    already = any(a in tail for a in _SHARE_ASK_HINTS) and any(
        t in tail for t in _SHARE_TOPIC_HINTS
    )
    if already:
        return reply

    request = SHARE_REQUEST_DE if _looks_german(reply) else SHARE_REQUEST_EN
    logger.info("First reply had no share request — appending it")
    separator = " " if reply[-1] in ".!?" else ". "
    return f"{reply}{separator}{request}"


def generate_first_reply(
    applicant_message: str,
    project_title: str,
    project_description: str,
    applicant_name: str | None = None,
) -> str:
    name_line = f"Applicant: {applicant_name}" if applicant_name else ""
    prompt = f"""{_brief(project_title, project_description)}
{name_line}
Applicant message:
{_clip(applicant_message, 1800)}

Write first reply:"""
    reply = _ensure_share_request(_generate_clean(FIRST_REPLY_PROMPT, prompt, kind="first"))
    if not _looks_like_recruiter_reply(reply, "first"):
        raise LLMError("First reply failed recruiter quality check after cleanup")
    logger.info("AI first reply (%d chars)", len(reply))
    return reply


TELL_ME_MORE_EN = "Could you tell me more about that part?"
TELL_ME_MORE_DE = "Könnten Sie mir mehr über diesen Teil erzählen?"


def _ensure_tell_me_more(reply: str) -> str:
    """
    Guarantee the second reply ends by asking the candidate to expand on a part.

    The prompt supplies the specific detail; this only backstops the case where
    the model answers without asking anything.
    """
    reply = (reply or "").strip()
    if not reply:
        return TELL_ME_MORE_EN

    tail = reply[-260:].lower()
    if any(h in tail for h in _MORE_ASK_HINTS):
        return reply

    request = TELL_ME_MORE_DE if _looks_german(reply) else TELL_ME_MORE_EN
    logger.info("Second reply had no 'tell me more' question — appending it")
    separator = " " if reply[-1] in ".!?" else ". "
    return f"{reply}{separator}{request}"


def generate_second_reply(
    applicant_message: str,
    project_title: str,
    project_description: str,
    conversation_context: str = "",
) -> str:
    prompt = f"""{_brief(project_title, project_description)}
Latest applicant message (this is what they just wrote — react only to this):
{_clip(applicant_message, 1800)}

Write second recruiter reply:"""
    reply = _ensure_tell_me_more(_generate_clean(SECOND_REPLY_PROMPT, prompt, kind="second"))
    if not _looks_like_recruiter_reply(reply, "second"):
        raise LLMError("Second reply failed recruiter quality check after cleanup")
    logger.info("AI second reply (%d chars)", len(reply))
    return reply


def generate_third_reply(
    applicant_message: str,
    project_title: str,
    project_description: str,
) -> str:
    prompt = f"""{_brief(project_title, project_description)}
Latest applicant message: {applicant_message}

Write third recruiter reply. Briefly thank them and mention they'll receive a Slack Connect invite to a private channel as an external connection shortly."""
    reply = _generate(FOLLOWUP_REPLY_PROMPT, prompt)
    logger.info("AI third reply (%d chars)", len(reply))
    return reply


def generate_tech_followup(
    tech_questions: str,
    candidate_answer: str,
    followup_number: int,
    project_title: str = "",
    project_description: str = "",
    prior_followups: list[str] | None = None,
) -> str:
    prior = "\n".join(prior_followups or [])
    prompt = f"""{_brief(project_title, project_description)}
Technical questions asked:
{tech_questions}

Candidate's answer:
{candidate_answer}

Prior follow-ups already asked:
{prior}

This is follow-up question #{followup_number} of 3. Ask one new question:"""
    reply = _generate(TECH_FOLLOWUP_PROMPT, prompt)
    logger.info("AI tech followup #%d (%d chars)", followup_number, len(reply))
    return reply


SLACK_EMAIL_RESOLVE_PROMPT = """You help a recruiter decide which email to use for a Slack invite.

The candidate was asked to reply with the email address for their Slack invitation.
You receive their reply and the From address of their email.

Decide ONE of:
1. EMAIL:<address> — they clearly gave a different email address to use
2. USE_FROM — they clearly mean their current From address (e.g. "use this email", "invite me here")
3. ASK — unclear; write a short polite reply asking them to send the Slack invite email

Output format (strict):
First line: EMAIL:user@example.com  OR  USE_FROM  OR  ASK
If ASK, then 1-3 more sentences for the email body (no markdown). Acknowledge their message briefly, then ask for the Slack invite email."""


def resolve_slack_email_with_gemini(applicant_message: str, from_email: str) -> tuple[str | None, str | None]:
    """
    Returns (slack_email, ask_again_message).
    Exactly one of the two is set.
    """
    prompt = f"""From address: {from_email}

Candidate message:
{applicant_message}

Decide:"""
    raw = _generate(SLACK_EMAIL_RESOLVE_PROMPT, prompt).strip()
    logger.info("AI slack-email resolve: %s", raw[:200])
    first = (raw.splitlines()[0] if raw else "").strip()
    upper = first.upper()

    if upper.startswith("EMAIL:"):
        addr = first.split(":", 1)[1].strip().lower()
        if "@" in addr and "kontrora.com" not in addr:
            return addr, None
    if upper.startswith("USE_FROM") and from_email and "@" in from_email:
        return from_email.lower(), None

    ask = raw
    if upper.startswith("ASK"):
        rest = "\n".join(raw.splitlines()[1:]).strip()
        ask = rest or (
            "Thanks for your reply. Please send the email address you would like us "
            "to use for the Slack invitation."
        )
    elif not ask:
        ask = (
            "Thanks for your reply. Please send the email address you would like us "
            "to use for the Slack invitation."
        )
    return None, ask
