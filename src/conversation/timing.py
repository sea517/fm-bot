import random
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

EDT = ZoneInfo("America/New_York")

# freelancermap / email
DELAY_SHORT_MINUTES = 5
DELAY_LONG_MINUTES = 10

# Slack assessment — delay bands by how much the candidate wrote.
SLACK_DELAY_SHORT = (60, 120)    # 1–2 min
SLACK_DELAY_MEDIUM = (120, 180)  # 2–3 min
SLACK_DELAY_LONG = (180, 240)    # 3–4 min


SHORT_SENTENCE_THRESHOLD = 2

# Slack technical assessment window (America/New_York)
SLACK_WINDOW_START_HOUR = 10  # 10:00 a.m. EDT/EST
SLACK_WINDOW_END_HOUR = 17  # 5:00 p.m. EDT/EST
SLACK_WEEKDAYS = {0, 1, 2, 3, 4}  # Mon–Fri


def count_sentences(text: str) -> int:
    parts = re.split(r"[.!?]+\s+", text.strip())
    parts = [p for p in parts if p.strip()]
    return max(len(parts), 1)


def reply_delay_minutes(candidate_message: str) -> int:
    if count_sentences(candidate_message) <= SHORT_SENTENCE_THRESHOLD:
        return DELAY_SHORT_MINUTES
    return DELAY_LONG_MINUTES


def _slack_delay_band(candidate_message: str) -> tuple[int, int]:
    """Pick short / medium / long delay band from message size."""
    text = (candidate_message or "").strip()
    chars = len(text)
    words = len(text.split()) if text else 0
    sentences = count_sentences(text)

    # Short: brief acks / one-liners ("Okay", "Sure", "Yes")
    if chars <= 40 and words <= 8 and sentences <= 1:
        return SLACK_DELAY_SHORT
    # Long: intros, project write-ups, tech answers
    if chars >= 350 or words >= 60 or sentences >= 5:
        return SLACK_DELAY_LONG
    return SLACK_DELAY_MEDIUM


def slack_reply_delay_seconds(candidate_message: str) -> int:
    """
    Delay before Oliver replies on Slack, based on message size.

    Short  -> 1–2 min
    Medium -> 2–3 min
    Long   -> 3–4 min
    """
    low, high = _slack_delay_band(candidate_message)
    return random.randint(low, high)


def slack_reply_delay_minutes(candidate_message: str) -> int:
    """Legacy helper — prefer slack_reply_delay_seconds for Slack."""
    return max(1, (slack_reply_delay_seconds(candidate_message) + 59) // 60)


def schedule_after_candidate_message(
    candidate_message: str, from_time: datetime | None = None
) -> datetime:
    base = from_time or datetime.now(timezone.utc)
    minutes = reply_delay_minutes(candidate_message)
    return base + timedelta(minutes=minutes)


def _as_edt(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(EDT)


def is_slack_business_hours(when: datetime | None = None) -> bool:
    local = _as_edt(when or datetime.now(timezone.utc))
    if local.weekday() not in SLACK_WEEKDAYS:
        return False
    return SLACK_WINDOW_START_HOUR <= local.hour < SLACK_WINDOW_END_HOUR


def next_slack_business_open(from_time: datetime | None = None) -> datetime:
    """Next Monday–Friday 10:00 America/New_York as UTC."""
    local = _as_edt(from_time or datetime.now(timezone.utc))
    candidate = local.replace(
        hour=SLACK_WINDOW_START_HOUR, minute=0, second=0, microsecond=0
    )
    if local >= candidate:
        candidate += timedelta(days=1)
    while candidate.weekday() not in SLACK_WEEKDAYS:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def snap_to_slack_window(when: datetime) -> datetime:
    """
    If `when` falls inside Mon–Fri 10:00–17:00 America/New_York, keep it.
    Otherwise move it to the next window open.
    """
    local = _as_edt(when)
    if local.weekday() in SLACK_WEEKDAYS and SLACK_WINDOW_START_HOUR <= local.hour < SLACK_WINDOW_END_HOUR:
        return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return next_slack_business_open(when)


def schedule_slack_after_message(
    candidate_message: str,
    from_time: datetime | None = None,
    *,
    require_business_hours: bool = True,
) -> datetime:
    """
    Slack reply delay scaled by how much the candidate wrote (seconds).
    Assessment steps are snapped into Mon–Fri 10:00–17:00 EDT.
    """
    base = from_time or datetime.now(timezone.utc)
    due = base + timedelta(seconds=slack_reply_delay_seconds(candidate_message))
    if require_business_hours:
        return snap_to_slack_window(due)
    return due


def next_edt_10am(from_time: datetime | None = None) -> datetime:
    """Next 10:00 AM America/New_York as UTC (legacy helper)."""
    return next_slack_business_open(from_time)


def schedule_hours_later(
    min_hours: float = 3,
    max_hours: float = 4,
    from_time: datetime | None = None,
) -> datetime:
    """Reply 3–4 hours later (never immediately)."""
    base = from_time or datetime.now(timezone.utc)
    minutes = random.randint(int(min_hours * 60), int(max_hours * 60))
    return base + timedelta(minutes=minutes)


def three_days_later(from_time: datetime | None = None) -> datetime:
    base = from_time or datetime.now(timezone.utc)
    return base + timedelta(days=3)


# After Oliver posts the take-home tech questions, wait this long before
# reading the answer and sending the first follow-up.
TECH_ANSWER_WAIT_MINUTES = 15


def hours_later(hours: float, from_time: datetime | None = None) -> datetime:
    """Schedule an action a fixed number of hours from now."""
    base = from_time or datetime.now(timezone.utc)
    return base + timedelta(hours=hours)


def schedule_tech_answer_check(from_time: datetime | None = None) -> datetime:
    """15 minutes after the tech questions are asked (snapped to Slack hours)."""
    base = from_time or datetime.now(timezone.utc)
    due = base + timedelta(minutes=TECH_ANSWER_WAIT_MINUTES)
    return snap_to_slack_window(due)


_WEEKDAY_NAMES = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

# Candidate-named zones → IANA. IST here means India (recruiting candidates), not Irish.
_TZ_ALIASES: dict[str, ZoneInfo] = {
    "ist": ZoneInfo("Asia/Kolkata"),
    "india": ZoneInfo("Asia/Kolkata"),
    "edt": EDT,
    "est": EDT,
    "et": EDT,
    "eastern": EDT,
    "utc": ZoneInfo("UTC"),
    "gmt": ZoneInfo("UTC"),
    "cet": ZoneInfo("Europe/Berlin"),
    "cest": ZoneInfo("Europe/Berlin"),
    "pst": ZoneInfo("America/Los_Angeles"),
    "pdt": ZoneInfo("America/Los_Angeles"),
    "pt": ZoneInfo("America/Los_Angeles"),
    "cst": ZoneInfo("America/Chicago"),
    "cdt": ZoneInfo("America/Chicago"),
    "bst": ZoneInfo("Europe/London"),
    "wet": ZoneInfo("Europe/London"),
}

# Echo of Oliver's assessment window inside the candidate's reply — never book from this.
_OLIVER_WINDOW_ECHO = re.compile(
    r"(?:which\s+falls\s+)?within\s+(?:your|the)\s+"
    r"\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\s*[–\-—to]+\s*"
    r"\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\s*(?:edt|est)\s*window"
    r"|"
    r"\b(?:your|the)\s+"
    r"\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\s*[–\-—to]+\s*"
    r"\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\s*(?:edt|est)\s*window\b"
    r"|"
    r"\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\s*[–\-—to]+\s*"
    r"\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\s*(?:edt|est)\s*window\b",
    re.I,
)

_AMPM_CLOCK = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)\b",
    re.I,
)
_TZ_TOKEN = re.compile(
    r"\s*(ist|edt|est|et|utc|gmt|cet|cest|pst|pdt|pt|cst|cdt|bst|india|eastern)\b",
    re.I,
)


def _strip_oliver_window_echo(text: str) -> str:
    """Remove Oliver's quoted 10–5 EDT window so we don't book from it."""
    return " ".join(_OLIVER_WINDOW_ECHO.sub(" ", text or "").split())


def _ampm_to_hour24(hour: int, ampm: str) -> int:
    ampm = ampm.replace(".", "").lower()
    if ampm.startswith("p") and hour < 12:
        return hour + 12
    if ampm.startswith("a") and hour == 12:
        return 0
    return hour


def _zone_after(text: str, end: int) -> ZoneInfo | None:
    m = _TZ_TOKEN.match(text, end)
    if not m:
        return None
    return _TZ_ALIASES.get(m.group(1).lower())


def _parse_candidate_local_clock(
    text: str,
) -> tuple[int, int, ZoneInfo] | None:
    """
    Best candidate-stated clock as (hour24, minute, timezone).

    Prefers an explicit range start ("from 8pm to 11pm IST") over stray clocks.
    Never uses Oliver's echoed assessment window.
    """
    low = _strip_oliver_window_echo(" ".join((text or "").lower().split()))
    if not low:
        return None

    # "from 8:00 PM to 11:00 PM IST" / "8:00 PM – 11:00 PM IST"
    range_re = re.compile(
        r"(?:from\s+)?"
        r"(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)"
        r"\s*(?:to|[–\-—])\s*"
        r"(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)"
        r"(?:\s*(ist|edt|est|et|utc|gmt|cet|cest|pst|pdt|pt|cst|cdt|bst|india|eastern))?",
        re.I,
    )
    m = range_re.search(low)
    if m:
        hour = _ampm_to_hour24(int(m.group(1)), m.group(3))
        minute = int(m.group(2) or 0)
        tz_name = (m.group(7) or "").lower()
        zone = _TZ_ALIASES.get(tz_name) if tz_name else None
        if zone is None:
            # TZ may sit right after the range via a separate token already in group;
            # also check a few chars after the match.
            zone = _zone_after(low, m.end()) or EDT
        return hour, minute, zone

    # Single clocks: prefer one with an explicit non-window timezone tag.
    tagged: list[tuple[int, int, ZoneInfo, int]] = []
    untagged: list[tuple[int, int, int]] = []
    for m in _AMPM_CLOCK.finditer(low):
        hour = _ampm_to_hour24(int(m.group(1)), m.group(3))
        minute = int(m.group(2) or 0)
        zone = _zone_after(low, m.end())
        if zone is not None:
            # Prefer candidate zones other than bare EDT only when tagged IST/etc.
            tagged.append((hour, minute, zone, m.start()))
        else:
            untagged.append((hour, minute, m.start()))

    if tagged:
        # Prefer IST / non-Eastern if present; else first tagged.
        for hour, minute, zone, _ in tagged:
            if zone is not EDT:
                return hour, minute, zone
        hour, minute, zone, _ = tagged[0]
        return hour, minute, zone

    if untagged:
        hour, minute, _ = untagged[0]
        return hour, minute, EDT

    m = re.search(r"\b([01]?\d|2[0-3])[:.](\d{2})\b", low)
    if m:
        return int(m.group(1)), int(m.group(2)), EDT
    return None


def _parse_clock_time(text: str) -> tuple[int, int] | None:
    """
    Legacy helper: wall-clock hour/minute in America/New_York terms.

    Converts IST/etc. into the equivalent EDT hour when a zone is present.
    """
    parsed = _parse_candidate_local_clock(text)
    if not parsed:
        return None
    hour, minute, zone = parsed
    # Build a dummy date in that zone, convert to EDT for the hour tuple.
    now_local = datetime.now(zone)
    local_dt = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    edt_dt = local_dt.astimezone(EDT)
    return edt_dt.hour, edt_dt.minute


def sanitize_scheduled_start(
    when: datetime, from_time: datetime | None = None
) -> datetime:
    """
    Fix model dates with the wrong calendar year (e.g. 2025 when it is 2026)
    and snap into the Slack assessment window.
    """
    now = from_time or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    local = _as_edt(when)
    local_now = _as_edt(now)

    if local.year != local_now.year:
        try:
            fixed = local.replace(year=local_now.year)
        except ValueError:
            fixed = local
        # If that lands more than ~2 days in the past, use next year.
        if fixed < local_now - timedelta(days=2):
            try:
                fixed = local.replace(year=local_now.year + 1)
            except ValueError:
                fixed = local
        local = fixed

    return snap_to_slack_window(local.astimezone(timezone.utc))


def looks_like_slot_confirmation(text: str) -> bool:
    """
    True when the candidate is confirming an already-proposed slot, not
    proposing a new day/time.
    """
    raw = (text or "").strip()
    if not raw:
        return False
    low = " ".join(raw.lower().split())

    # Explicit pushback / new preference → not a bare confirmation.
    if re.search(
        r"\b(?:but|instead|can we|could we|prefer|rather|reschedule|"
        r"different time|another day|not that)\b",
        low,
    ):
        return False

    # New schedule cues (day name / tomorrow / clock) → treat as proposal.
    has_day = any(re.search(rf"\b{re.escape(n)}\b", low) for n in _WEEKDAY_NAMES)
    has_relative = bool(re.search(r"\b(?:tomorrow|today|next week)\b", low))
    has_clock = _AMPM_CLOCK.search(_strip_oliver_window_echo(low)) is not None
    if has_day or has_relative or has_clock:
        return False

    cues = (
        "that works",
        "works for me",
        "works for us",
        "sounds good",
        "sounds great",
        "looking forward",
        "see you then",
        "see you at",
        "confirmed",
        "i confirm",
        "ok with that",
        "okay with that",
        "that's fine",
        "that is fine",
        "all good",
        "got it",
    )
    if any(c in low for c in cues):
        return True

    # Short ack with no schedule content.
    compact = re.sub(r"[^a-z\s]", "", low).strip()
    if compact in {
        "sure",
        "ok",
        "okay",
        "yes",
        "yep",
        "yeah",
        "great",
        "perfect",
        "thanks",
        "thank you",
        "awesome",
        "noted",
        "agreed",
    }:
        return True
    return False


def parse_availability_start(
    text: str, from_time: datetime | None = None
) -> datetime | None:
    """
    Best-effort start time from an availability reply, as UTC.

    Returns None when the candidate seems free now / soon (caller should
    start after the normal short Slack delay).
    """
    raw = (text or "").strip()
    if not raw:
        return None
    if looks_like_slot_confirmation(raw):
        return None

    low = " ".join(_strip_oliver_window_echo(raw.lower()).split())
    now = from_time or datetime.now(timezone.utc)
    local_edt = _as_edt(now)

    # Relative: after/in N hours
    m = re.search(r"\b(?:after|in)\s+(\d+)\s*hours?\b", low)
    if m:
        return snap_to_slack_window(now + timedelta(hours=int(m.group(1))))
    m = re.search(r"\b(?:after|in)\s+(\d+)\s*mins?(?:utes)?\b", low)
    if m:
        return snap_to_slack_window(now + timedelta(minutes=int(m.group(1))))
    if "after an hour" in low or "in an hour" in low:
        return snap_to_slack_window(now + timedelta(hours=1))

    clock = _parse_candidate_local_clock(low)

    day_offset: int | None = None
    if "tomorrow" in low:
        day_offset = 1
    elif "today" in low:
        day_offset = 0
    else:
        for name, wd in _WEEKDAY_NAMES.items():
            if re.search(rf"\b{re.escape(name)}\b", low):
                ahead = (wd - local_edt.weekday()) % 7
                day_offset = ahead
                break

    if day_offset is None and clock is None:
        # No day/time cue — treat as available now.
        return None

    if day_offset is None:
        day_offset = 0

    if clock is not None:
        hour, minute, zone = clock
        # Anchor the calendar day in EDT, then apply the clock in its own zone
        # on that same civil date (or the zone's matching date).
        anchor = (local_edt + timedelta(days=day_offset)).date()
        target_local = datetime(
            anchor.year,
            anchor.month,
            anchor.day,
            hour,
            minute,
            tzinfo=zone,
        )
        # If the zone is behind/ahead such that the EDT calendar day differs,
        # still honor the civil date the candidate meant relative to "today".
        target_utc = target_local.astimezone(timezone.utc)
    else:
        target_local = local_edt.replace(
            hour=SLACK_WINDOW_START_HOUR, minute=0, second=0, microsecond=0
        ) + timedelta(days=day_offset)
        target_utc = target_local.astimezone(timezone.utc)

    target_edt = _as_edt(target_utc)
    if target_edt <= local_edt:
        if any(re.search(rf"\b{re.escape(n)}\b", low) for n in _WEEKDAY_NAMES):
            target_utc = target_utc + timedelta(days=7)
        elif "tomorrow" in low:
            pass
        else:
            # "today at 2pm" but it's already 3pm → next business open
            return next_slack_business_open(now)

    return sanitize_scheduled_start(target_utc, now)


def format_availability_when(when: datetime) -> str:
    """Human label for scheduled start, in America/New_York."""
    local = _as_edt(when)
    return local.strftime("%A, %B %-d at %-I:%M %p").replace("  ", " ")


def availability_is_later(
    start_at: datetime | None,
    from_time: datetime | None = None,
    *,
    grace_minutes: int = 45,
) -> bool:
    """True when the candidate's window is meaningfully in the future."""
    if start_at is None:
        return False
    now = from_time or datetime.now(timezone.utc)
    if start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=timezone.utc)
    return start_at > now + timedelta(minutes=grace_minutes)


def agreement_slot_label(start_at: datetime) -> str:
    """Stable key for de-duplicating 'we'll begin around …' messages."""
    return format_availability_when(sanitize_scheduled_start(start_at))

