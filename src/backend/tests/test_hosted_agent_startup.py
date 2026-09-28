import importlib.util
import logging
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def hosted(monkeypatch):
    class Host:
        def invoke_handler(self, handler):
            return handler

    module = ModuleType("azure.ai.agentserver.invocations")
    module.InvocationAgentServerHost = Host
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    spec = importlib.util.spec_from_file_location(
        "startup_hosted_fixture", Path(__file__).parents[2] / "hosted-agent" / "main.py"
    )
    hosted_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hosted_module)
    return hosted_module


@pytest.mark.parametrize(
    ("available", "reason", "expected_reason"),
    [
        (False, "TimeoutError", "TimeoutError"),
        (False, None, "not_configured"),
        (True, None, None),
    ],
)
@pytest.mark.asyncio
async def test_startup_blob_local_only_telemetry(hosted, monkeypatch, caplog, available, reason, expected_reason):
    class FakeBlobSkillService:
        def __init__(self, settings):
            pass

        @property
        def is_available(self):
            return available

        @property
        def unavailability_reason(self):
            return reason

        async def initialize(self):
            pass

        async def seed_from_local(self):
            return []

    class FakeCosmosService:
        def __init__(self, settings):
            pass

        async def initialize(self):
            pass

    class FakeCopilotAgent:
        def __init__(self, settings):
            pass

        def set_registries(self, registries):
            pass

        def set_cosmos_service(self, cosmos_service):
            pass

        async def start(self):
            pass

    settings = SimpleNamespace(environment="test", foundry_model_deployment="test-model")
    monkeypatch.setattr(hosted, "get_settings", lambda: settings)
    monkeypatch.setattr(hosted, "setup_telemetry", lambda value: None)
    monkeypatch.setattr(hosted, "BlobSkillService", FakeBlobSkillService)
    monkeypatch.setattr(hosted, "CosmosService", FakeCosmosService)
    monkeypatch.setattr(hosted, "CopilotAgent", FakeCopilotAgent)

    with caplog.at_level(logging.WARNING, logger=hosted.__name__):
        await hosted._startup()

    records = [
        record for record in caplog.records if getattr(record, "event_name", None) == "HOSTED_AGENT_BLOB_LOCAL_ONLY"
    ]
    assert len(records) == int(expected_reason is not None)
    if records:
        record = records[0]
        assert record.failure_reason == expected_reason
        assert record.environment == "test"
        assert record.model_deployment == "test-model"
        assert datetime.fromisoformat(record.event_timestamp).tzinfo is not None
