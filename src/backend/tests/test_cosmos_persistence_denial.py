"""Cosmos persistence behaviour when the account refuses or never answers.

The 2026-09-28 incident had two properties worth keeping under test. A 403 can
mean two entirely different things — the account firewall rejected the network
path, or the identity lacks a data-plane role — and the two have different
owners, so they must never share a log signature. And when the account is
blackholed (no route to the private endpoint) the SDK retries for tens of
seconds, which would put that time on every response; each persistence call is
therefore bounded.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

import pytest
from azure.cosmos.exceptions import CosmosHttpResponseError

from app.config import Settings
from app.models import Message, MessageRole
from app.services import cosmos_service as cosmos_module
from app.services.cosmos_service import (
    NETWORK_DENIAL_SIGNATURE,
    RBAC_DENIAL_SIGNATURE,
    UNREACHABLE_SIGNATURE,
    CosmosService,
)

FIREWALL_DENIAL = (
    "Request originated from client IP 10.0.1.4 through public internet. "
    "This is blocked by your Cosmos DB account firewall settings."
)
RBAC_DENIAL = (
    "Request blocked by Auth example : Request is blocked because principal "
    "[00000000-0000-0000-0000-000000000000] does not have required RBAC permissions."
)


def _message() -> Message:
    return Message(
        id="message-1",
        conversationId="conversation-1",
        role=MessageRole.USER,
        content="hello",
        createdAt=datetime.now(UTC),
    )


class _RefusingContainer:
    """Cosmos container that answers every write with one HTTP error."""

    def __init__(self, status_code: int, message: str, sub_status: int | None = None) -> None:
        self._error = CosmosHttpResponseError(status_code=status_code, message=message)
        self._error.sub_status = sub_status

    async def upsert_item(self, *_args, **_kwargs):
        raise self._error


class _BlackholedContainer:
    """Cosmos container that never answers, like a blocked network path."""

    async def upsert_item(self, *_args, **_kwargs):
        await asyncio.sleep(3600)


def _cosmos_service(container) -> CosmosService:
    service = CosmosService(Settings(cosmos_db_endpoint="https://example.documents.azure.com:443/"))
    service._messages_container = container
    return service


@pytest.mark.parametrize(
    "detail,expected_signature,other_signature",
    [
        (FIREWALL_DENIAL, NETWORK_DENIAL_SIGNATURE, RBAC_DENIAL_SIGNATURE),
        (RBAC_DENIAL, RBAC_DENIAL_SIGNATURE, NETWORK_DENIAL_SIGNATURE),
    ],
)
async def test_network_and_rbac_denials_log_distinct_signatures(
    caplog: pytest.LogCaptureFixture, detail: str, expected_signature: str, other_signature: str
) -> None:
    service = _cosmos_service(_RefusingContainer(403, detail, sub_status=5301))

    with caplog.at_level(logging.ERROR), pytest.raises(CosmosHttpResponseError):
        await service.upsert_message(_message())

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert expected_signature in logged
    assert other_signature not in logged
    assert "upsert_message" in logged


async def test_blocked_cosmos_fails_within_the_operation_bound(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cosmos_module, "_COSMOS_OPERATION_TIMEOUT_S", 0.05)
    service = _cosmos_service(_BlackholedContainer())

    started = time.monotonic()
    with caplog.at_level(logging.ERROR), pytest.raises(TimeoutError):
        await service.upsert_message(_message())
    elapsed = time.monotonic() - started

    assert elapsed < 1, "a blocked Cosmos must not hold the response open"
    assert UNREACHABLE_SIGNATURE in "\n".join(record.getMessage() for record in caplog.records)


async def test_operation_bound_stays_below_the_startup_probe_budget() -> None:
    assert 0 < cosmos_module._COSMOS_OPERATION_TIMEOUT_S < cosmos_module._COSMOS_PROBE_TIMEOUT_S


async def test_reads_on_the_response_path_fail_open_when_cosmos_is_blocked(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cosmos_module, "_COSMOS_OPERATION_TIMEOUT_S", 0.05)

    class _BlackholedReads:
        async def read_item(self, *_args, **_kwargs):
            await asyncio.sleep(3600)

    service = CosmosService(Settings(cosmos_db_endpoint="https://example.documents.azure.com:443/"))
    service._conversations_container = _BlackholedReads()
    service._sessions_container = _BlackholedReads()

    with caplog.at_level(logging.ERROR):
        assert await service.get_conversation("conversation-1", "user-1") is None
        assert await service.get_session_mapping("conversation-1") is None

    # The read path swallows the timeout, so the log line is the only evidence
    # an operator gets that Cosmos stopped answering.
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert logged.count(UNREACHABLE_SIGNATURE) == 2
    assert "get_conversation" in logged
    assert "get_session_mapping" in logged
