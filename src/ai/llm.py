"""Single entry point for text generation, backed by OpenRouter or Gemini.

Provider is chosen by src.config.active_ai_provider():
  * OPENROUTER_API_KEY set  -> OpenRouter (OpenAI-compatible chat completions)
  * otherwise               -> Google Gemini
Force one with AI_PROVIDER=openrouter|gemini.
"""

import logging
import re

import requests

from src.config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODEL,
    active_ai_provider,
)

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 90
# Reasoning models spend most of the budget thinking before writing, so this
# has to cover hidden reasoning tokens plus the short reply we actually want.
MAX_TOKENS = 1200


class LLMError(RuntimeError):
    pass


# DeepSeek-style models narrate their planning before the actual reply.
_REASONING_TAGS = re.compile(
    r"<(think|thinking|reasoning|analysis)>.*?</\1>", re.IGNORECASE | re.DOTALL
)
_REASONING_MARKERS = (
    "the user wants",
    "the user is asking",
    "we need to write",
    "i need to write",
    "let me analyze",
    "letme analyze",
    "let me think",
    "let's analyze",
    "first, let me",
    "he wrote",
    "she wrote",
    "they wrote",
    "so the reply",
    "now i'll write",
    "okay, so",
)
# Where the real message starts once the narration ends.
_REPLY_HANDOFF = re.compile(
    r"^\s*(here'?s (?:the|my) (?:reply|response|message|draft)|"
    r"reply|response|draft|final(?: reply| answer| message)?|message)\s*[:\-]\s*$",
    re.IGNORECASE,
)


def _is_reasoning_block(block: str) -> bool:
    low = block.strip().lower()
    if not low:
        return True
    return any(low.startswith(m) or f" {m}" in low[:160] for m in _REASONING_MARKERS)


def strip_reasoning(text: str) -> str:
    """
    Return only the reply, dropping any chain-of-thought the model emitted.

    Handles <think> tags, a "Here's the reply:" handoff line, and leading
    paragraphs that narrate the model's own planning.
    """
    text = _REASONING_TAGS.sub("", text or "").strip()
    if not text:
        return ""

    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _REPLY_HANDOFF.match(line):
            tail = "\n".join(lines[i + 1 :]).strip()
            if tail:
                return tail

    blocks = re.split(r"\n\s*\n", text)
    kept = [b for b in blocks if not _is_reasoning_block(b)]
    if kept and len(kept) != len(blocks):
        return "\n\n".join(kept).strip()
    return text.strip()


def active_model() -> str:
    return OPENROUTER_MODEL if active_ai_provider() == "openrouter" else GEMINI_MODEL


def _generate_openrouter(system: str, user: str, max_tokens: int = MAX_TOKENS) -> str:
    if not OPENROUTER_API_KEY:
        raise LLMError("OPENROUTER_API_KEY is required")

    try:
        response = requests.post(
            f"{OPENROUTER_BASE_URL.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                # OpenRouter attribution headers (optional but recommended)
                "HTTP-Referer": "https://kontrora.com",
                "X-Title": "Kontrora recruiting bot",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.2,
                # Keep reasoning tiny so classification tokens are not eaten.
                "reasoning": {"effort": "low", "exclude": True},
                "include_reasoning": False,
            },
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        raise LLMError(f"OpenRouter request failed: {e}") from e

    if response.status_code != 200:
        raise LLMError(
            f"OpenRouter HTTP {response.status_code}: {response.text[:400]}"
        )

    try:
        payload = response.json()
    except ValueError as e:
        raise LLMError(f"OpenRouter returned non-JSON: {response.text[:200]}") from e

    if "error" in payload and not payload.get("choices"):
        raise LLMError(f"OpenRouter error: {str(payload['error'])[:400]}")

    choices = payload.get("choices") or []
    if not choices:
        raise LLMError(f"OpenRouter returned no choices: {str(payload)[:300]}")

    text = (choices[0].get("message") or {}).get("content") or ""
    text = strip_reasoning(str(text))
    if not text:
        finish = choices[0].get("finish_reason")
        usage = payload.get("usage") or {}
        reasoning_tokens = (usage.get("completion_tokens_details") or {}).get(
            "reasoning_tokens"
        )
        raise LLMError(
            f"OpenRouter returned an empty message (finish_reason={finish}, "
            f"max_tokens={max_tokens}, reasoning_tokens={reasoning_tokens}) — "
            "the model likely spent the whole budget on reasoning"
        )
    return text


def _generate_gemini(system: str, user: str) -> str:
    if not GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY is required")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user,
        config=types.GenerateContentConfig(system_instruction=system),
    )
    text = (response.text or "").strip()
    if not text:
        raise LLMError("Gemini returned an empty message")
    return text


def generate(system: str, user: str, max_tokens: int | None = None) -> str:
    provider = active_ai_provider()
    last_error: Exception | None = None
    tokens = max_tokens if max_tokens is not None else MAX_TOKENS
    for attempt in (1, 2, 3):
        try:
            if provider == "openrouter":
                return _generate_openrouter(system, user, max_tokens=tokens)
            return _generate_gemini(system, user)
        except LLMError as e:
            last_error = e
            logger.warning("AI generate attempt %d failed: %s", attempt, e)
    raise last_error or LLMError("AI generate failed")
