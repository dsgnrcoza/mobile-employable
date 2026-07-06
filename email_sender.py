"""
email_sender.py
----------------
Sends the 6-digit codes used for email-based 2FA and password reset.

Configured entirely through environment variables so it works with any
standard SMTP relay (Gmail app password, SendGrid, Mailgun, Postmark,
AWS SES, etc.) without code changes -- set SMTP_HOST/SMTP_PORT/
SMTP_USERNAME/SMTP_PASSWORD/SMTP_FROM_EMAIL and it sends real mail.

Until those are set, codes are logged server-side instead of emailed,
so the whole 2FA/reset flow is still fully testable in development
without needing real credentials -- there is no working "send a real
email with no configuration" option, so this is the safe default
rather than a silent no-op.
"""

import os
import smtplib
import ssl
from email.message import EmailMessage

SUBJECTS = {
    "2fa_login": "Your Employable sign-in code",
    "password_reset": "Reset your Employable password",
}


def _body_for(code: str, purpose: str) -> str:
    if purpose == "2fa_login":
        intro = "Here's the verification code to finish signing in:"
    else:
        intro = "Here's the verification code to reset your password:"
    return (
        f"{intro}\n\n"
        f"    {code}\n\n"
        "This code expires in 10 minutes and can only be used once.\n\n"
        "If you didn't request this, you can safely ignore this email --"
        " your account is still secure."
    )


def send_code_email(to_email: str, code: str, purpose: str) -> None:
    subject = SUBJECTS.get(purpose, "Your Employable verification code")
    body = _body_for(code, purpose)

    host = os.environ.get("SMTP_HOST")
    if not host:
        # Dev/no-config fallback: logged, not sent. Still lets the full
        # 2FA/reset flow be tested end to end before real credentials
        # are wired up in production.
        print(f"[email_sender] SMTP not configured -- logging instead of sending.\n"
              f"  To: {to_email}\n  Subject: {subject}\n  Code: {code}")
        return

    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ.get("SMTP_USERNAME")
    password = os.environ.get("SMTP_PASSWORD")
    from_email = os.environ.get("SMTP_FROM_EMAIL") or username or "noreply@employable.app"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=10) as server:
        server.starttls(context=context)
        if username and password:
            server.login(username, password)
        server.send_message(msg)
