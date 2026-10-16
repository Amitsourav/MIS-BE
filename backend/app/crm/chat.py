"""Partner chat, over the CRM's HTTP API (BE-CRM `/api/v1/mis/*`).

The chat lives in the CRM, where the counsellor and admins reply; the MIS only
relays a partner's reads and posts. This is the one place the MIS writes
anything to a CRM, and it does so through the CRM's API with a `mis`-scoped
key — never through the database.

Scoping: every call sends the caller's own source ids (from the JWT), and the
CRM 404s any lead outside them, so a partner can only reach their own leads.

Tests replace these functions with in-memory fakes (see tests/conftest.py), so
always call them as `chat.thread(...)`, never `from ... import thread`.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

from app.config import settings
from app.core import timing
from app.models.enums import Brand

logger = logging.getLogger("mis.crm.chat")


class ChatUnavailable(RuntimeError):
    """The CRM chat API isn't configured or can't be reached right now."""


class ChatError(RuntimeError):
    """The CRM refused the request in a way the partner should hear about."""

    def __init__(self, status: int, detail: Any) -> None:
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


# CRM answers passed on as-is; anything else (401 bad key, 409, 5xx) is our
# problem, not the partner's, and reads as "chat unavailable".
_PASS_THROUGH = {404, 422, 429}


async def _call(
    brand: Brand, demo: bool, method: str, path: str, *,
    params: dict[str, Any] | None = None, json: dict[str, Any] | None = None,
) -> Any:
    api = settings.crm_api(brand.value, demo)
    if api is None:
        raise ChatUnavailable("Chat isn't set up for this company yet")
    base, key = api
    try:
        with timing.span("crm_chat"):
            async with httpx.AsyncClient(timeout=settings.crm_api_timeout_sec) as client:
                r = await client.request(
                    method, f"{base}{path}", params=params, json=json,
                    headers={"X-API-Key": key},
                )
    except httpx.HTTPError as exc:
        logger.warning("CRM chat %s %s failed: %s", method, path, exc)
        raise ChatUnavailable("Chat is unavailable right now") from exc
    if r.status_code in (200, 201):
        return r.json()
    if r.status_code in _PASS_THROUGH:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = None
        raise ChatError(r.status_code, detail or "Chat request refused")
    logger.warning("CRM chat %s %s -> %s: %s", method, path, r.status_code, r.text[:300])
    raise ChatUnavailable("Chat is unavailable right now")


def _ids(source_ids: list[uuid.UUID]) -> str:
    return ",".join(str(s) for s in source_ids)


async def thread(
    brand: Brand, demo: bool, source_ids: list[uuid.UUID], lead_id: uuid.UUID
) -> dict[str, Any]:
    """{counsellor_name, items: [...]} for one lead, oldest message first."""
    return await _call(
        brand, demo, "GET", f"/mis/leads/{lead_id}/messages",
        params={"lead_source_ids": _ids(source_ids)},
    )


async def post(
    brand: Brand, demo: bool, source_ids: list[uuid.UUID], lead_id: uuid.UUID, *,
    message_id: uuid.UUID, partner_name: str, partner_user_email: str | None,
    body: str, tags: list[str],
) -> dict[str, Any]:
    """Post a partner message. The same `message_id` again returns the stored
    message (no duplicate, no second notification)."""
    return await _call(
        brand, demo, "POST", f"/mis/leads/{lead_id}/messages",
        json={
            "lead_source_ids": [str(s) for s in source_ids],
            "mis_message_id": str(message_id),
            "partner_name": partner_name,
            "partner_user_email": partner_user_email,
            "body": body,
            "tags": tags,
        },
    )


async def latest(
    brand: Brand, demo: bool, source_ids: list[uuid.UUID]
) -> list[dict[str, Any]]:
    """[{lead_id, last_message_at, last_staff_message_at}] per lead with messages."""
    if not source_ids:
        return []
    return await _call(
        brand, demo, "GET", "/mis/messages/latest",
        params={"lead_source_ids": _ids(source_ids)},
    )
