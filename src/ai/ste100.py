"""ASD-STE100 (Simplified Technical English) guidance for all bot-written text.

STE has writing rules + a controlled dictionary. We enforce the writing rules in
prompts and fixed copy. Technical names/verbs for our stack (FastAPI, Next.js,
PostgreSQL, Stripe, GitHub, etc.) are allowed as project technical terms.
"""

from __future__ import annotations

# Reusable block appended / embedded in LLM system prompts.
STE100_RULES = """
Language standard: ASD-STE100 (Simplified Technical English).

Follow these STE writing rules in EVERY sentence you write:
1) One word — one meaning. Prefer simple approved words (start not begin/commence;
   make not create/generate when “make” is enough; tell not inform; show not display).
2) Use American English spelling.
3) Use the active voice. Prefer simple present or simple past.
4) Keep sentences short: max ~20 words for instructions, max ~25 words for description.
5) One topic or one instruction per sentence. Split long ideas.
6) Do not use slang, idioms, phrasal verbs, or figurative language
   (no “stood out”, “solid match”, “send this your way”, “dig into”).
7) Do not use unclear “-ing” forms as the main verb when a simple form works.
8) Do not omit articles (a / an / the) when they make the sentence clear.
9) Keep noun clusters short (max three nouns in a row when possible).
10) Be consistent: use the same technical term for the same thing every time.
11) You MAY use technical names/verbs for this project: FastAPI, Next.js, React,
    PostgreSQL, Stripe, Python, TypeScript, GitHub, freelancermap, billing,
    multi-tenant, PSA, remote, contract, assignment, repository.
12) No markdown. No filler. No marketing tone.
""".strip()


def with_ste100(system_prompt: str) -> str:
    """Append STE100 rules to an existing system prompt."""
    base = (system_prompt or "").rstrip()
    if "ASD-STE100" in base:
        return base
    return f"{base}\n\n{STE100_RULES}"
