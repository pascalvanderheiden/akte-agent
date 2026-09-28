"""Deleting a conversation must also delete its persisted messages."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from azure.cosmos.exceptions import CosmosResourceNotFoundError

from app.config import Settings
from app.models import Conversation, Message
from app.services.cosmos_service import CosmosService


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

        await cosmos.delete_conversation("doomed", "default-user")

        assert await cosmos.get_conversation("doomed", "default-user") is None
        assert await cosmos.list_messages("doomed") == []
        assert [m.id for m in await cosmos.list_messages("kept")] == ["kept-u", "kept-a"]
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


async def test_cosmos_delete_conversation_deletes_message_partition_first():
    calls: list[tuple[str, str, str]] = []

    def delete_message(item, partition_key):
        calls.append(("messages", item, partition_key))
        if item == "m2":
            raise CosmosResourceNotFoundError()  # already gone: still a successful cascade

    messages = SimpleNamespace(
        query_items=lambda **kwargs: _Items([{"id": "m1"}, {"id": "m2"}]),
        delete_item=AsyncMock(side_effect=delete_message),
    )
    conversations = SimpleNamespace(
        delete_item=AsyncMock(
            side_effect=lambda item, partition_key: calls.append(("conversations", item, partition_key))
        )
    )
    cosmos = CosmosService(Settings(cosmos_db_endpoint="https://cosmos.invalid"))
    cosmos._messages_container = messages
    cosmos._conversations_container = conversations

    await cosmos.delete_conversation("c1", "default-user")

    assert calls == [
        ("messages", "m1", "c1"),
        ("messages", "m2", "c1"),
        ("conversations", "c1", "default-user"),
    ]
