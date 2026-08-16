"""Gmail API helpers for agent-initiated email actions.

One-time setup:
  1. Google Cloud Console → OAuth 2.0 用戶端憑證 → 下載 JSON → 存為 gmail_credentials.json
  2. python scripts/gmail_auth.py   (opens browser, stores gmail_token.json)
"""

from __future__ import annotations

import asyncio
import base64
import email.mime.text
from pathlib import Path

from loguru import logger

from src.config import settings

_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
]


def _get_service():
    """Build an authenticated Gmail API service (blocking — run in threadpool)."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    token_path = Path(settings.gmail_token_file)
    creds_path = Path(settings.gmail_credentials_file)

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not creds_path.exists():
                raise FileNotFoundError(
                    f"Gmail credentials not found at '{creds_path}'. "
                    "Download OAuth client JSON from Google Cloud Console and run: "
                    "python scripts/gmail_auth.py"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), _SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _build_raw(to: str, subject: str, body: str) -> str:
    msg = email.mime.text.MIMEText(body, "plain", "utf-8")
    msg["to"] = to
    msg["subject"] = subject
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def _create_draft_sync(to: str, subject: str, body: str) -> dict:
    service = _get_service()
    draft = service.users().drafts().create(
        userId="me",
        body={"message": {"raw": _build_raw(to, subject, body)}},
    ).execute()
    return {"success": True, "draft_id": draft["id"], "to": to, "subject": subject}


def _send_email_sync(to: str, subject: str, body: str) -> dict:
    service = _get_service()
    sent = service.users().messages().send(
        userId="me",
        body={"raw": _build_raw(to, subject, body)},
    ).execute()
    return {"success": True, "message_id": sent["id"], "to": to}


async def create_draft(to: str, subject: str, body: str) -> dict:
    """Create a Gmail draft. Returns draft_id for later sending."""
    try:
        return await asyncio.to_thread(_create_draft_sync, to, subject, body)
    except Exception as exc:
        logger.warning(f"gmail create_draft failed [{to}]: {exc}")
        return {"error": str(exc), "to": to}


async def send_email(to: str, subject: str, body: str) -> dict:
    """Send an email immediately via Gmail API."""
    try:
        return await asyncio.to_thread(_send_email_sync, to, subject, body)
    except Exception as exc:
        logger.warning(f"gmail send_email failed [{to}]: {exc}")
        return {"error": str(exc), "to": to}
