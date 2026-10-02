"""Deleting a conversation must also delete its persisted messages."""

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceExistsError, CosmosResourceNotFoundError

from app.config import Settings
from app.models import Conversation, Message
from app.services.cosmos_service import ConversationLeaseContentionError, CosmosService, cosmos_persistence_budget


async def test_sqlite_delete_conversation_removes_its_messages_only(tmp_path):
    cosmos = CosmosService(Settings(cosmos_db_endpoint="", local_data_dir=str(tmp_path)))
    await cosmos.initialize()
    now = datetime.now(UTC)
    try:
        for cid in ("doomed", "kept"):
            await cosmos.upsert_conversation(
                Conversation(id=cid, userId="default-user", title=cid, createdAt=now, updatedAt=now)
            )
            await cosmos.upsert_message(
                Message(id=f"{cid}-u", conversationId=cid, role="user", content="hi", createdAt=now)
            )
            await cosmos.upsert_message(
                Message(
                    id=f"{cid}-a",
                    conversationId=cid,
                    role="assistant",
                    content="ok",
                    createdAt=now + timedelta(seconds=1),
                )
            )
            await cosmos.upsert_session_mapping(cid, f"{cid}-session")

        await cosmos.delete_conversation("doomed", "default-user")

        assert await cosmos.get_conversation("doomed", "default-user") is None
        assert await cosmos.list_messages("doomed") == []
        assert await cosmos.get_session_mapping("doomed") is None
        assert [m.id for m in await cosmos.list_messages("kept")] == ["kept-u", "kept-a"]
        assert await cosmos.get_session_mapping("kept") == "kept-session"
    finally:
        await cosmos.close()


class _Items:
    def __init__(self, items):
        self._items = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration from None


class _SharedMessages:
    def __init__(self, items=None, calls=None):
        self.items = {
            (item["conversationId"], item["id"]): {**item, "_etag": str(index)}
            for index, item in enumerate(items or [], start=1)
        }
        self.calls = calls if calls is not None else []
        self._next_etag = len(self.items) + 1

    async def create_item(self, item):
        key = (item["conversationId"], item["id"])
        if key in self.items:
            raise CosmosResourceExistsError(status_code=409)
        self.items[key] = {**item, "_etag": str(self._next_etag)}
        self._next_etag += 1

    async def read_item(self, item, partition_key):
        try:
            return dict(self.items[(partition_key, item)])
        except KeyError:
            raise CosmosResourceNotFoundError(status_code=404) from None

    async def replace_item(self, item, body, etag, match_condition):
        key = (body["conversationId"], item)
        existing = self.items.get(key)
        if existing is None or existing["_etag"] != etag:
            raise CosmosHttpResponseError(status_code=412)
        self.items[key] = {**body, "_etag": str(self._next_etag)}
        self._next_etag += 1

    async def delete_item(self, item, partition_key, etag=None, match_condition=None):
        key = (partition_key, item)
        existing = self.items.get(key)
        if existing is None:
            raise CosmosResourceNotFoundError(status_code=404)
        if etag is not None and existing["_etag"] != etag:
            raise CosmosHttpResponseError(status_code=412)
        del self.items[key]
        if item != "__conversation_lease__":
            self.calls.append(("messages", item, partition_key))

    def query_items(self, query, parameters, partition_key):
        return _Items(
            [
                {"id": item["id"]}
                for (cid, _), item in self.items.items()
                if cid == partition_key and "lockToken" not in item
            ]
        )


async def test_cosmos_delete_conversation_deletes_message_partition_first():
    calls: list[tuple[str, str, str]] = []

    messages = _SharedMessages(
        [{"id": "m1", "conversationId": "c1"}, {"id": "m2", "conversationId": "c1"}],
        calls,
    )
    conversations = SimpleNamespace(
        delete_item=AsyncMock(
            side_effect=lambda item, partition_key: calls.append(("conversations", item, partition_key))
        )
    )
    sessions = SimpleNamespace(
        delete_item=AsyncMock(side_effect=lambda item, partition_key: calls.append(("sessions", item, partition_key)))
    )
    cosmos = CosmosService(Settings(cosmos_db_endpoint="https://cosmos.invalid"))
    cosmos._messages_container = messages
    cosmos._conversations_container = conversations
    cosmos._sessions_container = sessions

    await cosmos.delete_conversation("c1", "default-user")

    assert calls == [
        ("messages", "m1", "c1"),
        ("messages", "m2", "c1"),
        ("sessions", "c1", "c1"),
        ("conversations", "c1", "default-user"),
    ]


async def test_cosmos_delete_fails_closed_when_lease_acquisition_is_throttled():
    messages = _SharedMessages()
    messages.create_item = AsyncMock(side_effect=CosmosHttpResponseError(status_code=429))
    conversations = SimpleNamespace(delete_item=AsyncMock())
    sessions = SimpleNamespace(delete_item=AsyncMock())
    cosmos = CosmosService(Settings(cosmos_db_endpoint="https://cosmos.invalid"))
    cosmos._messages_container = messages
    cosmos._conversations_container = conversations
    cosmos._sessions_container = sessions

    with pytest.raises(CosmosHttpResponseError):
        await cosmos.delete_conversation("c1", "default-user")

    assert conversations.delete_item.await_count == 0
    assert sessions.delete_item.await_count == 0
    assert messages.calls == []


async def test_cosmos_delete_waits_for_chat_lease_from_another_service_instance():
    messages = _SharedMessages()
    chat_service = CosmosService(Settings(cosmos_db_endpoint="https://cosmos.invalid"))
    delete_service = CosmosService(Settings(cosmos_db_endpoint="https://cosmos.invalid"))
    chat_service._messages_container = messages
    delete_service._messages_container = messages
    delete_service._conversations_container = SimpleNamespace(delete_item=AsyncMock())
    delete_service._sessions_container = SimpleNamespace(delete_item=AsyncMock())
    now = datetime.now(UTC)
    run_lock = await chat_service.acquire_conversation_lock("active")

    with pytest.raises(RuntimeError, match="Conversation lease is not owned"):
        await delete_service.upsert_message(
            Message(id="late", conversationId="active", role="assistant", content="done", createdAt=now),
            lock_token=str(uuid.uuid4()),
        )
    assert ("active", "late") not in messages.items

    delete_task = asyncio.create_task(delete_service.delete_conversation("active", "default-user"))
    await asyncio.sleep(0.05)
    assert not delete_task.done()

    await chat_service.release_conversation_lock("active", run_lock)
    await delete_task

    assert delete_service._conversations_container.delete_item.await_count == 1
    with pytest.raises(RuntimeError, match="Conversation lease is not owned"):
        await chat_service.upsert_message(
            Message(id="after-delete", conversationId="active", role="assistant", content="late", createdAt=now),
            lock_token=run_lock.lease_token,
        )
    assert ("active", "after-delete") not in messages.items


async def test_degraded_chat_cannot_write_after_another_replica_deletes_conversation():
    messages = _SharedMessages()
    chat_service = CosmosService(Settings(cosmos_db_endpoint="https://cosmos.invalid"))
    delete_service = CosmosService(Settings(cosmos_db_endpoint="https://cosmos.invalid"))
    chat_service._messages_container = messages
    delete_service._messages_container = messages
    delete_service._conversations_container = SimpleNamespace(delete_item=AsyncMock())
    delete_service._sessions_container = SimpleNamespace(delete_item=AsyncMock())
    create_item = messages.create_item
    messages.create_item = AsyncMock(side_effect=CosmosHttpResponseError(status_code=429))

    chat_lock = await chat_service.acquire_conversation_lock("degraded", allow_degraded=True)
    messages.create_item = create_item
    assert chat_lock.lease_acquisition_failed

    await delete_service.delete_conversation("degraded", "default-user")

    with pytest.raises(RuntimeError, match="Conversation lease acquisition failed"):
        await chat_service.upsert_message(
            Message(
                id="late-assistant",
                conversationId="degraded",
                role="assistant",
                content="late response",
                createdAt=datetime.now(UTC),
            ),
            lock_token=chat_lock.lease_token,
            lease_acquisition_failed=chat_lock.lease_acquisition_failed,
        )
    assert ("degraded", "late-assistant") not in messages.items

    await chat_service.release_conversation_lock("degraded", chat_lock)


async def test_degraded_lock_fails_closed_after_observing_another_replica_lease(monkeypatch):
    monkeypatch.setattr("app.services.cosmos_service._COSMOS_REQUEST_PERSISTENCE_BUDGET_S", 0.02)
    messages = _SharedMessages()
    owner = CosmosService(Settings(cosmos_db_endpoint="https://cosmos.invalid"))
    contender = CosmosService(Settings(cosmos_db_endpoint="https://cosmos.invalid"))
    owner._messages_container = messages
    contender._messages_container = messages
    owner_lock = await owner.acquire_conversation_lock("contended")

    with cosmos_persistence_budget() as budget:
        budget.remaining_s = 0.01
        with pytest.raises(ConversationLeaseContentionError):
            await contender.acquire_conversation_lock("contended", allow_degraded=True)

    await owner.release_conversation_lock("contended", owner_lock)


async def test_lease_takeover_retries_etag_conflict_after_observed_contention():
    messages = _SharedMessages(
        [
            {
                "id": "__conversation_lease__",
                "conversationId": "racing",
                "lockToken": "previous-owner",
                "expiresAt": time.time() + 0.02,
            }
        ]
    )
    replace_item = messages.replace_item
    replacements = 0

    async def replace_with_one_race(*args, **kwargs):
        nonlocal replacements
        replacements += 1
        if replacements == 1:
            raise CosmosHttpResponseError(status_code=412)
        await replace_item(*args, **kwargs)

    messages.replace_item = replace_with_one_race
    service = CosmosService(Settings(cosmos_db_endpoint="https://cosmos.invalid"))
    service._messages_container = messages

    with cosmos_persistence_budget() as budget:
        budget.remaining_s = 1
        token = await service._acquire_cosmos_lease("racing")

    assert token
    assert replacements == 2


async def test_sqlite_delete_waits_for_active_chat_persistence(tmp_path):
    cosmos = CosmosService(Settings(cosmos_db_endpoint="", local_data_dir=str(tmp_path)))
    await cosmos.initialize()
    now = datetime.now(UTC)
    try:
        await cosmos.upsert_conversation(
            Conversation(id="active", userId="default-user", title="active", createdAt=now, updatedAt=now)
        )
        run_lock = await cosmos.acquire_conversation_lock("active")
        delete_task = asyncio.create_task(cosmos.delete_conversation("active", "default-user"))
        await asyncio.sleep(0)

        await cosmos.upsert_message(
            Message(id="assistant", conversationId="active", role="assistant", content="done", createdAt=now)
        )
        await cosmos.upsert_session_mapping("active", "session")
        delete_waited_for_run = not delete_task.done()
        await cosmos.release_conversation_lock("active", run_lock)
        await delete_task

        assert delete_waited_for_run
        assert await cosmos.get_conversation("active", "default-user") is None
        assert await cosmos.list_messages("active") == []
        assert await cosmos.get_session_mapping("active") is None
    finally:
        await cosmos.close()


async def test_cosmos_upsert_rollback_restores_previous_conversation_after_lease_loss():
    old = {"id": "c1", "userId": "default-user", "title": "old", "_etag": "1"}
    token = str(uuid.uuid4())
    conversations = SimpleNamespace(
        read_item=AsyncMock(return_value=old),
        replace_item=AsyncMock(
            side_effect=[
                {"id": "c1", "userId": "default-user", "title": "new", "_etag": "2"},
                None,
            ]
        ),
    )
    cosmos = CosmosService(Settings(cosmos_db_endpoint="https://cosmos.invalid"))
    cosmos._conversations_container = conversations
    cosmos._messages_container = SimpleNamespace(
        read_item=AsyncMock(
            side_effect=[
                {"lockToken": token, "expiresAt": time.time() + 60},
                CosmosResourceNotFoundError(status_code=404),
            ]
        )
    )

    with pytest.raises(RuntimeError, match="Conversation lease is not owned"):
        await cosmos.upsert_conversation(
            Conversation(
                id="c1",
                userId="default-user",
                title="new",
                createdAt=datetime.now(UTC),
                updatedAt=datetime.now(UTC),
            ),
            lock_token=token,
        )

    assert conversations.replace_item.await_args_list[1].kwargs["body"] == {
        "id": "c1",
        "userId": "default-user",
        "title": "old",
    }
    assert conversations.replace_item.await_args_list[1].kwargs["etag"] == "2"
