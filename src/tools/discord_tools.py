"""Discord REST API helpers for agent-initiated messages."""

from __future__ import annotations

import httpx
from loguru import logger

from src.config import settings

_BASE = "https://discord.com/api/v10"


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bot {settings.discord_bot_token}", "Content-Type": "application/json"}


async def send_channel_message(
    channel_id: str,
    content: str,
    mention_user_ids: list[str] | None = None,
) -> dict:
    """Send a message to a Discord channel, optionally mentioning users."""
    if mention_user_ids:
        mentions = " ".join(f"<@{uid}>" for uid in mention_user_ids)
        content = f"{mentions} {content}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{_BASE}/channels/{channel_id}/messages",
                headers=_headers(),
                json={"content": content},
            )
            resp.raise_for_status()
            data = resp.json()
            return {"success": True, "message_id": data.get("id"), "channel_id": channel_id}
    except Exception as exc:
        logger.warning(f"discord send_channel_message failed [{channel_id}]: {exc}")
        return {"error": str(exc), "channel_id": channel_id}


async def send_dm(user_id: str, content: str) -> dict:
    """Open a DM channel with a user and send a message."""
    if user_id == "owner":
        user_id = settings.discord_owner_user_id
    if not user_id:
        return {"error": "discord_owner_user_id not configured"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Open (or reuse) DM channel
            dm_resp = await client.post(
                f"{_BASE}/users/@me/channels",
                headers=_headers(),
                json={"recipient_id": user_id},
            )
            dm_resp.raise_for_status()
            dm_channel_id = dm_resp.json()["id"]

            # Send message
            msg_resp = await client.post(
                f"{_BASE}/channels/{dm_channel_id}/messages",
                headers=_headers(),
                json={"content": content},
            )
            msg_resp.raise_for_status()
            data = msg_resp.json()
            return {"success": True, "message_id": data.get("id"), "user_id": user_id}
    except Exception as exc:
        logger.warning(f"discord send_dm failed [user={user_id}]: {exc}")
        return {"error": str(exc), "user_id": user_id}
