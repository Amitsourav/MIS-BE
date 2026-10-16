"""Partner chat on a lead: relayed to the CRM with the caller's own sources,
the right company key (demo partners use the Demo Loans key), and "new
reply" dots that clear when the chat is opened."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.config import settings
from app.crm import chat as chat_mod
from app.models.enums import Brand
from tests.factories import lead_row, make_provider, map_source, provider_headers

pytestmark = pytest.mark.asyncio

# The real client functions, taken before the autouse `chat` fixture swaps
# them for fakes.
REAL_THREAD, REAL_POST, REAL_LATEST = chat_mod.thread, chat_mod.post, chat_mod.latest


def _staff(body, at, role="counsellor", name="Deepanshi"):
    return {"id": str(uuid.uuid4()), "author_kind": "staff", "author_name": name,
            "author_role": role, "body": body, "is_hidden": False, "tags": [],
            "created_at": at.isoformat()}


async def test_partner_reads_and_posts_on_their_lead(client, db, crm, chat):
    p = await make_provider(db, "Altera", "amit15@x.com")
    src = await map_source(db, p)
    lead = lead_row(src)
    crm.leads[Brand.FMC] = [lead]
    lid = lead["crm_lead_id"]
    chat.threads[lid] = {"counsellor_name": "Deepanshi", "items": [
        _staff("Sanction expected Friday.", datetime.now(timezone.utc) - timedelta(hours=1)),
        {**_staff(None, datetime.now(timezone.utc), role="team", name="Ashmita"), "is_hidden": True},
    ]}
    h = provider_headers(p)

    body = (await client.get(f"/me/leads/{lid}/messages", headers=h)).json()
    assert body["counsellor_name"] == "Deepanshi"
    assert [(m["author_role"], m["body"]) for m in body["items"]] == [
        ("counsellor", "Sanction expected Friday."), ("team", None)]

    mid = str(uuid.uuid4())
    r = await client.post(f"/me/leads/{lid}/messages", headers=h, json={
        "body": "  Any update?  ", "tags": ["admin", "counsellor", "admin"],
        "client_message_id": mid})
    assert r.status_code == 201 and r.json()["body"] == "Any update?"
    sent = chat.posts[-1]
    assert (sent["partner_name"], sent["partner_user_email"]) == ("Altera", "amit15@x.com")
    assert sent["tags"] == ["admin", "counsellor"] and str(sent["message_id"]) == mid
    assert (sent["brand"], sent["demo"]) == (Brand.FMC, False)
    assert all(c[3] == [src] for c in chat.calls)  # only ever the caller's sources

    # A retry with the same id posts once.
    await client.post(f"/me/leads/{lid}/messages", headers=h, json={
        "body": "Any update?", "client_message_id": mid})
    assert len(chat.posts) == 1


async def test_bad_messages_are_422(client, db, crm):
    p = await make_provider(db, "Altera", "a@x.com")
    lead = lead_row(await map_source(db, p))
    crm.leads[Brand.FMC] = [lead]
    url, h = f"/me/leads/{lead['crm_lead_id']}/messages", provider_headers(p)
    for bad in ({"body": ""}, {"body": "   "}, {"body": "x" * 2001},
                {"body": "hi", "tags": ["ceo"]}):
        assert (await client.post(url, headers=h, json=bad)).status_code == 422


async def test_another_partners_lead_is_404(client, db, crm, chat):
    a = await make_provider(db, "A", "a@x.com")
    b = await make_provider(db, "B", "b@x.com")
    await map_source(db, a)
    hers = lead_row(await map_source(db, b))
    crm.leads[Brand.FMC] = [hers]
    chat.threads[hers["crm_lead_id"]] = {"counsellor_name": "X", "items": [
        _staff("private", datetime.now(timezone.utc))]}
    url = f"/me/leads/{hers['crm_lead_id']}/messages"
    r = await client.get(url, headers=provider_headers(a))
    assert r.status_code == 404 and "private" not in r.text
    assert (await client.post(url, headers=provider_headers(a),
                              json={"body": "hi"})).status_code == 404
    assert chat.posts == []
    latest = (await client.get("/me/messages/latest", headers=provider_headers(a))).json()
    assert latest == []


async def test_demo_partner_uses_the_demo_key(client, db, crm, chat):
    p = await make_provider(db, "Demo", "demo@x.com")
    p.is_demo = True
    await db.commit()
    lead = lead_row(await map_source(db, p))
    crm.leads[Brand.FMC] = [lead]
    await client.get(f"/me/leads/{lead['crm_lead_id']}/messages", headers=provider_headers(p))
    assert chat.calls[-1][:3] == ("thread", Brand.FMC, True)


async def test_unread_dot_clears_when_the_chat_is_opened(client, db, crm, chat):
    p = await make_provider(db, "Altera", "a@x.com")
    src = await map_source(db, p)
    replied, quiet = lead_row(src), lead_row(src)
    crm.leads[Brand.FMC] = [replied, quiet]
    chat.threads[replied["crm_lead_id"]] = {"counsellor_name": "D", "items": [
        _staff("Update", datetime.now(timezone.utc) - timedelta(minutes=5))]}
    chat.threads[quiet["crm_lead_id"]] = {"counsellor_name": "D", "items": [
        {**_staff("mine", datetime.now(timezone.utc)), "author_kind": "partner",
         "author_role": "partner"}]}
    h = provider_headers(p)

    latest = {r["lead_id"]: r for r in (await client.get("/me/messages/latest", headers=h)).json()}
    assert latest[str(replied["crm_lead_id"])]["unread"] is True
    assert latest[str(quiet["crm_lead_id"])]["unread"] is False  # no staff reply

    await client.get(f"/me/leads/{replied['crm_lead_id']}/messages", headers=h)
    latest = {r["lead_id"]: r for r in (await client.get("/me/messages/latest", headers=h)).json()}
    assert latest[str(replied["crm_lead_id"])]["unread"] is False


async def test_crm_chat_down_is_503(client, db, crm, chat):
    p = await make_provider(db, "Altera", "a@x.com")
    lead = lead_row(await map_source(db, p))
    crm.leads[Brand.FMC] = [lead]
    chat.down = True
    r = await client.get(f"/me/leads/{lead['crm_lead_id']}/messages", headers=provider_headers(p))
    assert r.status_code == 503
    # The lead page itself still works.
    assert (await client.get(f"/me/leads/{lead['crm_lead_id']}",
                             headers=provider_headers(p))).status_code == 200


# --- the real HTTP client, against a mock CRM ---
async def test_client_sends_the_mis_key_and_maps_errors(monkeypatch):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path.endswith("/latest"):
            return httpx.Response(401, json={"detail": "bad key"})
        if req.method == "POST":
            return httpx.Response(429, json={"detail": "slow down"})
        return httpx.Response(200, json={"counsellor_name": None, "items": []})

    real = httpx.AsyncClient
    monkeypatch.setattr(chat_mod.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(settings, "fmc_crm_api_url", "https://crm.test/api/v1/")
    monkeypatch.setattr(settings, "fmc_crm_api_key", "crmk_live_fmc")
    monkeypatch.setattr(settings, "fmc_demo_crm_api_key", "crmk_live_demo")
    src, lid = uuid.uuid4(), uuid.uuid4()

    await REAL_THREAD(Brand.FMC, True, [src], lid)
    assert seen[-1].headers["X-API-Key"] == "crmk_live_demo"
    assert str(seen[-1].url) == f"https://crm.test/api/v1/mis/leads/{lid}/messages?lead_source_ids={src}"

    with pytest.raises(chat_mod.ChatError) as e:
        await REAL_POST(Brand.FMC, False, [src], lid, message_id=uuid.uuid4(),
                             partner_name="A", partner_user_email=None, body="hi", tags=[])
    assert e.value.status == 429 and seen[-1].headers["X-API-Key"] == "crmk_live_fmc"

    with pytest.raises(chat_mod.ChatUnavailable):  # a bad key is our problem
        await REAL_LATEST(Brand.FMC, False, [src])

    monkeypatch.setattr(settings, "av_crm_api_url", None)
    with pytest.raises(chat_mod.ChatUnavailable):  # not configured
        await REAL_THREAD(Brand.AV, False, [src], lid)
