"""Deterministic detectors for opt-out and handoff triggers (not LLM)."""

from __future__ import annotations

import re


def looks_like_opt_out(text: str) -> bool:
    low = " ".join((text or "").lower().split())
    if not low:
        return False
    patterns = (
        r"\bstop (messaging|contacting|emailing|writing)\b",
        r"\bdo not contact\b",
        r"\bdon't contact\b",
        r"\bremove me\b",
        r"\bunsubscribe\b",
        r"\bnot interested\b",
        r"\bno thanks\b",
        r"\bplease stop\b",
        r"\bleave me alone\b",
        r"\bopt[ -]?out\b",
    )
    return any(re.search(p, low) for p in patterns)


def looks_like_hard_handoff(text: str) -> str | None:
    """Return trigger id if inbound requires handoff without relying on the model."""
    low = " ".join((text or "").lower().split())
    if not low:
        return None
    if re.search(
        r"\b(visa|work permit|sponsorship|tax(es)?|contract law|nda dispute|"
        r"discriminat|grievance|lawyer|attorney|gdpr|privacy complaint)\b",
        low,
    ):
        return "legal_or_visa_or_tax"
    if re.search(
        r"(ignore (all |previous )?instructions|system prompt|you are now|"
        r"developer mode|jailbreak|reveal (your |the )?prompt)",
        low,
    ):
        return "injection"
    if re.search(
        r"\b(password|seed phrase|private key|wallet address|bank (account|details)|"
        r"routing number|social security|date of birth|home address)\b",
        low,
    ):
        return "sensitive_data_request"
    return None


def looks_like_wants_human(text: str) -> bool:
    low = " ".join((text or "").lower().split())
    return bool(
        re.search(
            r"\b(real person|human recruiter|talk to (a |someone|a person)|"
            r"speak (to|with) (a )?(human|person|recruiter)|"
            r"connect me with (a )?person|flag me for (a )?human)\b",
            low,
        )
    )


def looks_like_call_request(text: str) -> bool:
    low = " ".join((text or "").lower().split())
    return bool(
        re.search(
            r"\b(call|zoom|video (call|interview)|phone (call|interview)|"
            r"schedule (a )?(call|meeting|interview))\b",
            low,
        )
    )


def looks_like_call_confirm(text: str) -> bool:
    low = " ".join((text or "").lower().split())
    if not looks_like_call_request(text) and not re.search(
        r"\b(yes|yeah|please do|go ahead|that works|schedule it)\b", low
    ):
        return False
    return bool(
        re.search(
            r"\b(yes|yeah|please|go ahead|schedule (it|me)|that works|i('d| would) like)\b",
            low,
        )
    ) and (
        looks_like_call_request(text)
        or "person" in low
        or "recruiter" in low
        or "human" in low
    )


def looks_like_no_github(text: str) -> bool:
    low = " ".join((text or "").lower().split())
    return bool(
        re.search(
            r"\b(no github|don't have (a )?github|do not have (a )?github|"
            r"only (use )?(gitlab|bitbucket)|gitlab only|bitbucket only)\b",
            low,
        )
    )


def looks_like_submission(text: str) -> bool:
    low = (text or "").lower()
    keys = (
        "submitted",
        "i submitted",
        "pull request",
        "opened a pr",
        "created a pr",
        "finished the assignment",
        "completed the assignment",
        "done with the assignment",
        "pushed my",
        "forked",
    )
    return any(k in low for k in keys)
