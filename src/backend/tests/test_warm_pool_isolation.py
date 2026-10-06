"""Deterministic warm-pool ownership and first-post-deploy regressions."""

import asyncio
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI

from app import main
from app.config import Settings
from app.routers.health import router as health_router
from app.services.foundry_agent_proxy import FoundryAgentProxy


class FakePing:
    def __init__(self):
        self.sessions = deque(["synthetic-warm-1", "synthetic-warm-2"])
        self.provisioned = []
        self.refreshed = []

    async def __call__(self, session_id):
        if session_id is not None:
            self.refreshed.append(session_id)
            return True, session_id, 200, 0.0
        if not self.sessions:
            return False, None, 503, 0.0
        session_id = self.sessions.popleft()
        self.provisioned.append(session_id)
        return True, session_id, 200, 0.0


class FakeResponse:
    def __init__(self, session_id, status):
        self.status = status
        self.headers = {"x-agent-session-id": session_id}
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def text(self):
        return "synthetic invocation failure"

    async def iter_any(self):
        yield b'data: {"event": "done", "data": {}}\n\n'


class FakeTransport:
    def __init__(self):
        self.requests = []
        self.status = 200

    def post(self, endpoint, *, json, **_kwargs):
        session_id = parse_qs(urlsplit(endpoint).query).get("agent_session_id", [None])[0]
        self.requests.append((json["conversationId"], session_id))
        assigned = session_id or f"synthetic-cold-{len(self.requests)}"
        return FakeResponse(assigned, self.status)


@pytest.fixture
def pool(monkeypatch):
    settings = Settings(
        local_mode=False,
        keep_warm_enabled=True,
        warm_pool_size=2,
        foundry_agent_invocations_endpoint="http://fake/invocations",
        foundry_endpoint="http://fake/models",
        model_deployment_orchestrator="synthetic-orchestrator",
        model_deployment_deep_reasoning="synthetic-reasoning",
        model_deployment_fast="synthetic-fast",
    )
    monkeypatch.setattr("app.services.foundry_agent_proxy.DefaultAzureCredential", AsyncMock)
    proxy = FoundryAgentProxy(settings)
    ping = FakePing()
    transport = FakeTransport()
    monkeypatch.setattr(proxy, "_warmup_ping", ping)
    monkeypatch.setattr(proxy, "_get_token", AsyncMock(return_value=None))
    monkeypatch.setattr(proxy, "_http_session", transport)
    monkeypatch.setattr(proxy, "start", AsyncMock())
    monkeypatch.setattr(proxy, "stop", AsyncMock())
    claim = AsyncMock(wraps=proxy.claim_warm_session)
    monkeypatch.setattr(proxy, "claim_warm_session", claim)
    return proxy, ping, transport, claim, settings


@pytest.mark.asyncio
async def test_full_warmup_claims_nothing_and_first_claim_is_dedicated(pool):
    proxy, ping, _, claim, _ = pool

    assert await proxy.maintain_warm_pool() == (2, 2)
    assert await proxy.maintain_warm_pool() == (2, 2)
    claim.assert_not_awaited()

    first = await proxy.claim_warm_session()
    assert first in ping.provisioned
    remaining = await asyncio.gather(*(proxy.claim_warm_session() for _ in range(3)))
    warm_claims = [first, *(sid for sid in remaining if sid is not None)]
    assert set(warm_claims) == set(ping.provisioned)
    assert len(warm_claims) == len(set(warm_claims)) == 2
    assert remaining.count(None) == 2


@pytest.mark.asyncio
async def test_concurrent_claims_never_share_a_session(pool):
    proxy, ping, _, _, _ = pool
    assert await proxy.maintain_warm_pool() == (2, 2)
    start = asyncio.Event()

    async def claim():
        await start.wait()
        return await proxy.claim_warm_session()

    tasks = [asyncio.create_task(claim()) for _ in range(4)]
    start.set()
    claimed = await asyncio.gather(*tasks)
    warm_claims = [sid for sid in claimed if sid is not None]
    assert set(warm_claims) == set(ping.provisioned)
    assert len(warm_claims) == len(set(warm_claims)) == 2
    assert claimed.count(None) == 2


@pytest.mark.asyncio
async def test_partial_fill_remains_usable_and_cold_fallback_is_isolated(pool):
    proxy, ping, transport, claim, _ = pool
    ping.sessions = deque(["synthetic-partial"])
    await main._run_initial_warmup(proxy, timeout_s=45)
    assert proxy.warmup_state == "degraded"
    assert proxy.warm_pool_size == 1
    claim.assert_not_awaited()

    events = [[event async for event in proxy.invoke("hello", f"synthetic-conversation-{i}")] for i in range(2)]
    assert transport.requests == [
        ("synthetic-conversation-0", "synthetic-partial"),
        ("synthetic-conversation-1", None),
    ]
    sessions = [
        event["data"]["agentSessionId"]
        for invocation in events
        for event in invocation
        if event["event"] == "_gateway_session"
    ]
    assert len(sessions) == len(set(sessions)) == 2


@pytest.mark.parametrize("status", [200, 500], ids=["completed-invocation", "failed-invocation"])
@pytest.mark.asyncio
async def test_claimed_session_is_not_returned_after_invocation_or_maintenance(pool, status):
    proxy, ping, transport, _, _ = pool
    assert await proxy.maintain_warm_pool() == (2, 2)
    transport.status = status
    events = [event async for event in proxy.invoke("hello", "synthetic-first")]
    owned = transport.requests[0][1]
    assert owned in ping.provisioned
    assert any(event["event"] == ("done" if status == 200 else "error") for event in events)

    ping.sessions.append("synthetic-replacement")
    assert await proxy.maintain_warm_pool() == (2, 2)
    assert owned not in ping.refreshed
    claims = await asyncio.gather(*(proxy.claim_warm_session() for _ in range(3)))
    assert owned not in claims
    assert {sid for sid in claims if sid is not None} == set(ping.provisioned) - {owned}


@pytest.mark.parametrize("outcome", ["failed", "cancelled", "timed-out"])
@pytest.mark.asyncio
async def test_interrupted_warmup_preserves_exclusive_conversation_ownership(pool, monkeypatch, outcome):
    proxy, ping, transport, claim, _ = pool
    ping.sessions = deque(["synthetic-survivor"])
    blocked = asyncio.Event()
    release = asyncio.Event()
    exhausted = False

    async def interrupted_ping(session_id):
        nonlocal exhausted
        if session_id is None and not ping.sessions and not exhausted:
            exhausted = True
            blocked.set()
            await release.wait()
            raise RuntimeError("synthetic dependency failure")
        return await ping(session_id)

    monkeypatch.setattr(proxy, "_warmup_ping", interrupted_ping)
    if outcome == "timed-out":

        async def timeout(awaitable, *, timeout):
            task = asyncio.ensure_future(awaitable)
            await blocked.wait()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise TimeoutError

        monkeypatch.setattr(main.asyncio, "wait_for", timeout)

    warmup = asyncio.create_task(main._run_initial_warmup(proxy, timeout_s=45))
    try:
        await blocked.wait()
        claim.assert_not_awaited()
        if outcome == "cancelled":
            warmup.cancel()
            with pytest.raises(asyncio.CancelledError):
                await warmup
        else:
            if outcome == "failed":
                release.set()
            await warmup
    finally:
        warmup.cancel()
        await asyncio.gather(warmup, return_exceptions=True)

    assert proxy.warmup_state == "degraded"
    assert proxy.warm_pool_size == 1
    first_events = [event async for event in proxy.invoke("hello", "synthetic-first")]
    cold_events = [event async for event in proxy.invoke("hello", "synthetic-second")]
    ping.sessions.extend(["synthetic-recovered-1", "synthetic-recovered-2"])
    assert await proxy.maintain_warm_pool() == (2, 2)
    recovered_events = [event async for event in proxy.invoke("hello", "synthetic-third")]
    sessions = [
        event["data"]["agentSessionId"]
        for invocation in (first_events, cold_events, recovered_events)
        for event in invocation
        if event["event"] == "_gateway_session"
    ]
    assert len(sessions) == len(set(sessions)) == 3
    assert transport.requests[0] == ("synthetic-first", "synthetic-survivor")
    assert transport.requests[1] == ("synthetic-second", None)
    assert transport.requests[2][1] in {"synthetic-recovered-1", "synthetic-recovered-2"}
    assert "synthetic-survivor" not in ping.refreshed


@pytest.mark.asyncio
async def test_first_post_deploy_invocation_claims_warm_session_after_lifespan_ready(pool, monkeypatch, tmp_path):
    proxy, ping, transport, claim, settings = pool
    settings.persona_assets_root = str(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()
    warmup_finished = asyncio.Event()
    periodic_wait = asyncio.Event()

    async def controlled_ping(session_id):
        if session_id is None:
            started.set()
            await release.wait()
        return await ping(session_id)

    def set_state(state):
        FoundryAgentProxy.set_warmup_state(proxy, state)
        if state in {"ready", "degraded"}:
            warmup_finished.set()

    async def wait_for_next_maintenance(_interval):
        await periodic_wait.wait()

    monkeypatch.setattr(proxy, "_warmup_ping", controlled_ping)
    monkeypatch.setattr(proxy, "set_warmup_state", set_state)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "setup_telemetry", lambda _settings: None)
    monkeypatch.setattr(main, "FoundryAgentProxy", lambda _settings: proxy)
    monkeypatch.setattr(main, "CosmosService", lambda _settings: AsyncMock())
    monkeypatch.setattr(
        main,
        "BlobSkillService",
        lambda *_args, **_kwargs: SimpleNamespace(is_available=False, initialize=AsyncMock(), close=AsyncMock()),
    )
    monkeypatch.setattr(main, "EvalStorage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "EvalService", lambda *_args, **_kwargs: AsyncMock())
    monkeypatch.setattr(main, "TracesService", lambda _settings: AsyncMock())
    monkeypatch.setattr(main.asyncio, "sleep", wait_for_next_maintenance)
    app = FastAPI(lifespan=main.lifespan)
    app.include_router(health_router)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fake") as client,
    ):
        await started.wait()
        response = await client.get("/health/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "warming"}
        claim.assert_not_awaited()
        release.set()
        await warmup_finished.wait()
        response = await client.get("/health/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}
        claim.assert_not_awaited()
        assert proxy.warm_pool_size == proxy.warm_pool_target == 2

        events = [event async for event in app.state.foundry_proxy.invoke("hello", "synthetic-first-after-deploy")]
        claim.assert_awaited_once()
        assert transport.requests == [("synthetic-first-after-deploy", ping.provisioned[0])]
        assert {"event": "_gateway_session", "data": {"agentSessionId": ping.provisioned[0]}} in events
