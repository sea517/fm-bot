"""
Send mail over an HTTPS email API instead of SMTP.

Some networks/ISPs block outbound SMTP (465/587/25) entirely, which makes
smtplib time out. These providers accept mail over port 443, so they work
wherever normal web traffic works.

Configure in .env:
    EMAIL_API_PROVIDER=resend|sendgrid|brevo
    EMAIL_API_KEY=<api key>
The From address stays INBOX_FROM_EMAIL and must be on a domain verified with
the provider.
"""

import logging

import requests

from src.config import (
    EMAIL_API_KEY,
    EMAIL_API_PROVIDER,
    INBOX_EMAIL,
    INBOX_FROM_EMAIL,
    INBOX_FROM_NAME,
)

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30


class EmailAPIError(RuntimeError):
    pass


def _from_address() -> str:
    return INBOX_FROM_EMAIL or INBOX_EMAIL


def _send_resend(to_email: str, subject: str, body: str, headers: dict) -> None:
    payload = {
        "from": f"{INBOX_FROM_NAME} <{_from_address()}>",
        "to": [to_email],
        "subject": subject,
        "text": body,
    }
    if headers:
        payload["headers"] = headers
    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {EMAIL_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code >= 300:
        raise EmailAPIError(f"Resend HTTP {response.status_code}: {response.text[:300]}")


def _send_sendgrid(to_email: str, subject: str, body: str, headers: dict) -> None:
    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": _from_address(), "name": INBOX_FROM_NAME},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
    }
    if headers:
        payload["headers"] = headers
    response = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={
            "Authorization": f"Bearer {EMAIL_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code >= 300:
        raise EmailAPIError(
            f"SendGrid HTTP {response.status_code}: {response.text[:300]}"
        )


def _send_brevo(to_email: str, subject: str, body: str, headers: dict) -> None:
    payload = {
        "sender": {"email": _from_address(), "name": INBOX_FROM_NAME},
        "to": [{"email": to_email}],
        "subject": subject,
        "textContent": body,
    }
    if headers:
        payload["headers"] = headers
    response = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={"api-key": EMAIL_API_KEY, "Content-Type": "application/json"},
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code >= 300:
        raise EmailAPIError(f"Brevo HTTP {response.status_code}: {response.text[:300]}")


_SENDERS = {
    "resend": _send_resend,
    "sendgrid": _send_sendgrid,
    "brevo": _send_brevo,
}


def send_via_api(
    to_email: str,
    body: str,
    subject: str,
    in_reply_to: str | None = None,
) -> bool:
    sender = _SENDERS.get(EMAIL_API_PROVIDER)
    if not sender:
        logger.error(
            "Unknown EMAIL_API_PROVIDER=%r — use one of: %s",
            EMAIL_API_PROVIDER,
            ", ".join(sorted(_SENDERS)),
        )
        return False

    # Keeps the reply threaded under the candidate's original message.
    headers = {}
    if in_reply_to:
        headers["In-Reply-To"] = in_reply_to
        headers["References"] = in_reply_to

    try:
        sender(to_email, subject, body, headers)
    except (requests.RequestException, EmailAPIError) as e:
        logger.error("Email API (%s) failed for %s: %s", EMAIL_API_PROVIDER, to_email, e)
        return False

    logger.info("Email sent to %s via %s API", to_email, EMAIL_API_PROVIDER)
    return True
