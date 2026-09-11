import email as email_lib
import imaplib
import json
import logging
import re
import smtplib
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr

from src.config import (
    DATA_DIR,
    INBOX_EMAIL,
    INBOX_FROM_EMAIL,
    INBOX_FROM_NAME,
    INBOX_IMAP_HOST,
    INBOX_PASSWORD,
    INBOX_SMTP_HOST,
    INBOX_SMTP_PORT,
    email_api_enabled,
)
from src.mail.api_send import send_via_api

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
RESUME_EXTS = (".pdf", ".doc", ".docx", ".rtf", ".txt")
# Whole-word-ish resume cues. Do NOT use bare "resume" / "sabrina" alone —
# newsletters and freelancermap notifications would false-positive.
RESUME_WORDS = (
    "curriculum vitae",
    "lebenslauf",
    "referred you",
    "referred by",
    "sabrina referred",
    "referred by sabrina",
    "attached my cv",
    "attached my resume",
    "attached resume",
    "my resume",
    "my cv",
    "mein lebenslauf",
    "please find attached",
    "find my cv",
    "find my resume",
)
IGNORE_FROM = {
    "noreply@freelancermap.de",
    "no-reply@freelancermap.de",
    "noreply@freelancermap.com",
    "info@freelancermap.com",
    "mailer-daemon@",
    "notification@slack.com",
    "no-reply@email.slackhq.com",
}
IGNORE_SUBJECT_HINTS = (
    "new message in your freelancermap",
    "neue nachricht in ihrem freelancermap",
    "pobox_benachrichtigung",
)
IGNORE_BODY_HINTS = (
    "you have a new message in your freelancermap inbox",
    "sie haben eine neue nachricht in ihrem freelancermap",
    "utm_campaign=pobox_benachrichtigung",
)
OUR_ADDRESSES = {
    (INBOX_EMAIL or "").lower(),
    (INBOX_FROM_EMAIL or "").lower(),
    "info@kontrora.com",
    "oliver@kontrora.com",
    "sabrina@kontrora.com",
}
PROCESSED_FILE = DATA_DIR / "processed_emails.json"
SMTP_TIMEOUT = 15
IMAP_TIMEOUT = 60


@dataclass
class InboundEmail:
    message_id: str
    from_email: str
    from_name: str | None
    subject: str
    body: str
    has_resume: bool
    in_reply_to: str | None
    emails_in_body: list[str]


def is_system_notification(item: InboundEmail) -> bool:
    """freelancermap 'new message in inbox' alerts and other automated mail."""
    from_addr = (item.from_email or "").lower()
    if any(from_addr == x or from_addr.startswith(x) for x in IGNORE_FROM if x.endswith("@")):
        return True
    if from_addr in IGNORE_FROM:
        return True
    if "noreply@" in from_addr or "no-reply@" in from_addr:
        if "freelancermap" in from_addr:
            return True
    subject = (item.subject or "").lower()
    body = (item.body or "").lower()
    if any(h in subject for h in IGNORE_SUBJECT_HINTS):
        return True
    if any(h in body for h in IGNORE_BODY_HINTS):
        return True
    return False


def inbox_configured() -> bool:
    return bool(INBOX_EMAIL and INBOX_PASSWORD)


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    decoded: list[str] = []
    for text, charset in parts:
        if isinstance(text, bytes):
            decoded.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(text)
    return "".join(decoded)


def _load_processed() -> set[str]:
    if not PROCESSED_FILE.exists():
        return set()
    try:
        return set(json.loads(PROCESSED_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def mark_processed(message_id: str) -> None:
    ids = _load_processed()
    ids.add(message_id)
    PROCESSED_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROCESSED_FILE.write_text(json.dumps(sorted(ids)), encoding="utf-8")


USE_FROM_PHRASES = (
    "use this email",
    "use my email",
    "this email",
    "same email",
    "current email",
    "meine email",
    "diese email",
)


def extract_candidate_slack_email(body: str, from_email: str) -> str | None:
    """
    Invite the address the candidate wrote in the message.
    If they say something like "please use this email" with no other address, use From.
    """
    body = body or ""
    found = [e.lower() for e in EMAIL_RE.findall(body)]
    for addr in found:
        if addr not in OUR_ADDRESSES:
            return addr

    lower = body.lower()
    from_email = (from_email or "").lower()
    if from_email and from_email not in OUR_ADDRESSES:
        if any(p in lower for p in USE_FROM_PHRASES):
            return from_email
    return None


def _walk_body(msg: email_lib.message.Message) -> tuple[str, bool]:
    text_parts: list[str] = []
    has_resume = False
    for part in msg.walk():
        filename = part.get_filename() or ""
        ctype = (part.get_content_type() or "").lower()
        if filename and filename.lower().endswith(RESUME_EXTS):
            has_resume = True
        if ctype in ("application/pdf", "application/msword"):
            has_resume = True
        if part.get_content_maintype() == "multipart":
            continue
        if ctype == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                text_parts.append(payload.decode(charset, errors="replace"))
        elif ctype == "text/html" and not text_parts:
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                html = payload.decode(charset, errors="replace")
                text_parts.append(re.sub(r"<[^>]+>", " ", html))
    body = "\n".join(text_parts).strip()
    lower = f"{body} {filename}".lower()
    if any(w in lower for w in RESUME_WORDS):
        has_resume = True
    return body, has_resume


def _parse_message(raw: bytes) -> InboundEmail | None:
    msg = email_lib.message_from_bytes(raw)
    message_id = (msg.get("Message-ID") or "").strip()
    if not message_id:
        return None
    name, addr = parseaddr(msg.get("From", ""))
    body, has_resume = _walk_body(msg)
    return InboundEmail(
        message_id=message_id,
        from_email=addr.lower(),
        from_name=_decode_header_value(name) or None,
        subject=_decode_header_value(msg.get("Subject")),
        body=body,
        has_resume=has_resume,
        in_reply_to=(msg.get("In-Reply-To") or "").strip() or None,
        emails_in_body=[e.lower() for e in EMAIL_RE.findall(body)],
    )


def _connect_imap() -> imaplib.IMAP4:
    """Namecheap Private Email: SSL:993 preferred, STARTTLS:143 fallback."""
    last_error: Exception | None = None
    try:
        client = imaplib.IMAP4_SSL(INBOX_IMAP_HOST, 993, timeout=IMAP_TIMEOUT)
        client.login(INBOX_EMAIL, INBOX_PASSWORD)
        logger.info("IMAP connected via SSL %s:993 as %s", INBOX_IMAP_HOST, INBOX_EMAIL)
        return client
    except Exception as e:
        last_error = e
        logger.warning("IMAP SSL:993 failed: %s", e)

    try:
        client = imaplib.IMAP4(INBOX_IMAP_HOST, 143, timeout=IMAP_TIMEOUT)
        client.starttls()
        client.login(INBOX_EMAIL, INBOX_PASSWORD)
        logger.info("IMAP connected via STARTTLS %s:143 as %s", INBOX_IMAP_HOST, INBOX_EMAIL)
        return client
    except Exception as e:
        last_error = e
        logger.error(
            "IMAP login failed for %s. If webmail works but IMAP fails, use a Namecheap "
            "App Password (privateemail.com → Settings → Security → Manage app passwords), "
            "not the webmail master password. Last error: %s",
            INBOX_EMAIL,
            last_error,
        )
        raise


def _header_message_id(raw_headers: bytes) -> str | None:
    msg = email_lib.message_from_bytes(raw_headers)
    mid = (msg.get("Message-ID") or "").strip()
    return mid or None


def _header_from_address(raw_headers: bytes) -> str:
    msg = email_lib.message_from_bytes(raw_headers)
    _name, addr = parseaddr(msg.get("From", ""))
    return (addr or "").lower()


def fetch_new_emails(limit: int = 40, lookback_days: int = 5) -> list[InboundEmail]:
    """
    Load recent inbox mail and skip anything already in processed_emails.json.

    Do NOT use IMAP UNSEEN — opening mail in webmail marks it Seen and the bot
    would miss those applicants. Message-ID tracking is the source of truth.

    Headers are fetched first so already-processed mail is skipped without
    downloading the full RFC822 body (avoids long fetches that EOF the socket).
    """
    if not inbox_configured():
        logger.warning("Oliver inbox not configured (INBOX_EMAIL / INBOX_PASSWORD)")
        return []

    processed = _load_processed()
    results: list[InboundEmail] = []
    skipped = 0
    client = _connect_imap()
    try:
        try:
            client.select("INBOX")
            since = (
                datetime.now(timezone.utc) - timedelta(days=lookback_days)
            ).strftime("%d-%b-%Y")
            status, data = client.search(None, f"(SINCE {since})")
            if status != "OK" or not data or not data[0]:
                return []
            uids = data[0].split()[-limit:]
            logger.info(
                "IMAP: scanning %d recent messages (lookback=%dd, processed=%d)",
                len(uids),
                lookback_days,
                len(processed),
            )

            for uid in uids:
                try:
                    status, fetched = client.fetch(uid, "(BODY.PEEK[HEADER])")
                    if status != "OK" or not fetched or not fetched[0]:
                        continue
                    header_raw = fetched[0][1]
                    if not isinstance(header_raw, (bytes, bytearray)):
                        continue
                    message_id = _header_message_id(header_raw)
                    if not message_id or message_id in processed:
                        skipped += 1
                        continue
                    from_addr = _header_from_address(header_raw)
                    if from_addr in OUR_ADDRESSES:
                        mark_processed(message_id)
                        skipped += 1
                        continue

                    status, fetched = client.fetch(uid, "(RFC822)")
                    if status != "OK" or not fetched or not fetched[0]:
                        continue
                    raw = fetched[0][1]
                    parsed = _parse_message(raw)
                    if not parsed or parsed.message_id in processed:
                        continue
                    if parsed.from_email in OUR_ADDRESSES:
                        mark_processed(parsed.message_id)
                        continue
                    results.append(parsed)
                except imaplib.IMAP4.abort as e:
                    logger.warning(
                        "IMAP connection dropped mid-fetch (%s) — reconnecting", e
                    )
                    try:
                        client.logout()
                    except Exception:
                        pass
                    client = _connect_imap()
                    client.select("INBOX")
                    continue
        except imaplib.IMAP4.abort as e:
            logger.error(
                "IMAP aborted (%s) — returning %d emails collected so far",
                e,
                len(results),
            )
        logger.info(
            "IMAP: %d new messages (%d already processed/skipped)",
            len(results),
            skipped,
        )
        return results
    finally:
        try:
            client.logout()
        except Exception:
            pass


def send_email_reply(
    to_email: str,
    body: str,
    subject: str = "Re: Kontrora — next steps",
    in_reply_to: str | None = None,
) -> bool:
    # HTTPS API first when configured — required on networks blocking SMTP.
    if email_api_enabled():
        if send_via_api(to_email, body, subject, in_reply_to):
            return True
        logger.warning("Email API send failed for %s — trying SMTP", to_email)

    if not inbox_configured():
        logger.error("Cannot send email — INBOX_EMAIL / INBOX_PASSWORD missing")
        return False
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = formataddr((INBOX_FROM_NAME, INBOX_FROM_EMAIL or INBOX_EMAIL))
    msg["To"] = to_email
    msg["Subject"] = subject
    if INBOX_FROM_EMAIL and INBOX_FROM_EMAIL.lower() != INBOX_EMAIL.lower():
        msg["Reply-To"] = INBOX_FROM_EMAIL
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    # Try the configured port first, then the other standard ones. Networks that
    # block outbound SMTP hang until timeout, so keep the timeout short.
    ports: list[int] = []
    for port in (INBOX_SMTP_PORT, 465, 587, 25):
        if port and port not in ports:
            ports.append(port)

    last_error: Exception | None = None
    for port in ports:
        try:
            if port == 465:
                with smtplib.SMTP_SSL(INBOX_SMTP_HOST, port, timeout=SMTP_TIMEOUT) as smtp:
                    smtp.login(INBOX_EMAIL, INBOX_PASSWORD)
                    smtp.sendmail(INBOX_EMAIL, [to_email], msg.as_string())
            else:
                with smtplib.SMTP(INBOX_SMTP_HOST, port, timeout=SMTP_TIMEOUT) as smtp:
                    smtp.starttls()
                    smtp.login(INBOX_EMAIL, INBOX_PASSWORD)
                    smtp.sendmail(INBOX_EMAIL, [to_email], msg.as_string())
            logger.info("Email sent to %s via port %d", to_email, port)
            return True
        except (OSError, smtplib.SMTPException) as e:
            last_error = e
            logger.warning("SMTP port %d failed for %s: %s", port, to_email, e)

    if isinstance(last_error, (TimeoutError, socket.timeout, ConnectionError)):
        logger.error(
            "Failed to send email to %s — every SMTP port (%s) timed out on %s. "
            "Outbound SMTP looks blocked on this network/ISP; IMAP still works. "
            "Use a VPN, another network, or an HTTPS email API to send.",
            to_email,
            ", ".join(str(p) for p in ports),
            INBOX_SMTP_HOST,
        )
    else:
        logger.error(
            "Failed to send email to %s — last error: %s. If auth failed, use a "
            "Namecheap App Password instead of the webmail password.",
            to_email,
            last_error,
        )
    return False
