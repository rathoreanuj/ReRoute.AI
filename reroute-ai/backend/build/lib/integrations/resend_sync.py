"""Synchronous Resend send for Celery workers (httpx sync)."""

from __future__ import annotations

import httpx

from config import get_settings
from integrations.http_timeout import integration_timeout


def send_email_html_sync(*, to_email: str, subject: str, html: str) -> dict:
    settings = get_settings()
    if not settings.EMAIL_ENABLED or not settings.RESEND_API_KEY:
        return {"sent": False, "reason": "email_disabled_or_missing_key"}

    payload = {
        "from": settings.RESEND_FROM_EMAIL,
        "to": [to_email],
        "subject": subject,
        "html": html,
    }
    try:
        with httpx.Client(timeout=integration_timeout()) as client:
            r = client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
                json=payload,
            )
            if r.status_code >= 400:
                return {"sent": False, "reason": f"resend_http_{r.status_code}", "body": r.text[:2000]}
            return {"sent": True, "response": r.json()}
    except Exception as e:
        return {"sent": False, "reason": f"resend_error:{type(e).__name__}"}
