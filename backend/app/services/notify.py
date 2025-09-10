import os
from typing import Optional


# --- Twilio SMS ---
def twilio_enabled() -> bool:
    return bool(
        os.getenv("TWILIO_ACCOUNT_SID")
        and os.getenv("TWILIO_AUTH_TOKEN")
        and os.getenv("TWILIO_FROM")
    )


def send_sms_sync(to: str, body: str) -> None:
    if not twilio_enabled():
        raise RuntimeError("Twilio not configured: set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM")
    from twilio.rest import Client  # type: ignore

    client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
    client.messages.create(body=body, from_=os.getenv("TWILIO_FROM"), to=to)


async def send_sms(to: str, body: str) -> None:
    # keep simple: run sync client (BackgroundTasks runs it off-request)
    send_sms_sync(to, body)


# --- Solapi (CoolSMS) ---
def solapi_enabled() -> bool:
    return bool(
        os.getenv("SOLAPI_API_KEY")
        and os.getenv("SOLAPI_API_SECRET")
        and os.getenv("SOLAPI_FROM")
    )


def _send_sms_solapi_sync(to: str, body: str) -> None:
    if not solapi_enabled():
        raise RuntimeError("Solapi not configured: set SOLAPI_API_KEY, SOLAPI_API_SECRET, SOLAPI_FROM")
    # Lazy import to avoid dependency unless enabled
    from solapi import SolapiMessageService  # type: ignore
    from solapi.model import RequestMessage  # type: ignore

    # Solapi requires numbers without '+' / '-' (e.g., 01012345678)
    def digits_only(s: str) -> str:
        return "".join(ch for ch in s if ch.isdigit())

    from_num = digits_only(os.getenv("SOLAPI_FROM", ""))
    to_digits = digits_only(to)
    # If target is an E.164 KR number (82...), convert to domestic format (prepend trunk '0') for Solapi
    if to_digits.startswith("82") and len(to_digits) > 2:
        local = to_digits[2:]
        if not local.startswith("0"):
            local = "0" + local
        to_num = local
    else:
        to_num = to_digits
    if not from_num or not to_num:
        raise RuntimeError("Invalid phone numbers for Solapi: ensure digits only and env SOLAPI_FROM set")

    svc = SolapiMessageService(api_key=os.getenv("SOLAPI_API_KEY"), api_secret=os.getenv("SOLAPI_API_SECRET"))
    msg = RequestMessage(from_=from_num, to=to_num, text=body)
    svc.send(msg)


def sms_enabled() -> bool:
    return twilio_enabled() or solapi_enabled()


def send_sms_sync(to: str, body: str) -> None:
    """Generic SMS send via configured provider (Twilio preferred, else Solapi)."""
    if twilio_enabled():
        # Prefer Twilio if both configured for backwards compatibility
        from twilio.rest import Client  # type: ignore

        client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
        client.messages.create(body=body, from_=os.getenv("TWILIO_FROM"), to=to)
        return
    if solapi_enabled():
        _send_sms_solapi_sync(to, body)
        return
    raise RuntimeError("No SMS provider configured (set Twilio or Solapi env vars)")


# --- SendGrid Email ---
def sendgrid_enabled() -> bool:
    return bool(os.getenv("SENDGRID_API_KEY") and os.getenv("SENDGRID_FROM"))


def send_email_sync(to: str, subject: str, content_text: Optional[str] = None, content_html: Optional[str] = None) -> None:
    if not sendgrid_enabled():
        raise RuntimeError("SendGrid not configured: set SENDGRID_API_KEY and SENDGRID_FROM")
    from sendgrid import SendGridAPIClient  # type: ignore
    from sendgrid.helpers.mail import Mail, Email, To, Content  # type: ignore

    from_email = Email(os.getenv("SENDGRID_FROM"))
    to_email = To(to)
    # prefer HTML if provided
    if content_html:
        content = Content("text/html", content_html)
    else:
        content = Content("text/plain", content_text or "")

    mail = Mail(from_email, to_email, subject, content)
    sg = SendGridAPIClient(os.getenv("SENDGRID_API_KEY"))
    sg.send(mail)


async def send_email(to: str, subject: str, content_text: Optional[str] = None, content_html: Optional[str] = None) -> None:
    send_email_sync(to, subject, content_text, content_html)
