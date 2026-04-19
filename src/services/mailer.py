from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from src.shared.config import settings

logger = logging.getLogger(__name__)


def send_password_reset_email(email: str, reset_token: str) -> None:
    """Send password reset link via SMTP if configured; otherwise log a warning."""
    reset_link = f"{settings.password_reset_base_url}?token={reset_token}"

    if not settings.smtp_host:
        logger.warning(
            "SMTP not configured; password reset email for %s was not sent. Configure SMTP_HOST.",
            email,
        )
        return

    message = EmailMessage()
    message["Subject"] = "VerdictCouncil Password Reset"
    message["From"] = settings.smtp_from_address
    message["To"] = email
    message.set_content(
        "A password reset was requested for your VerdictCouncil account.\n\n"
        f"Reset link: {reset_link}\n\n"
        f"This link expires in {settings.reset_token_ttl_minutes} minutes.\n"
        "If you did not request this, you can ignore this email."
    )

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as client:
        client.starttls()
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(message)
