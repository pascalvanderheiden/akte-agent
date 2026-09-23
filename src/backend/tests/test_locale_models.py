"""Locale request and persona metadata contracts."""

import pytest
from pydantic import ValidationError

from app.models import AgentRequest, PersonaManifest


def test_agent_request_accepts_supported_locale():
    request = AgentRequest(conversationId="conversation", message="Hallo", locale="nl")
    assert request.locale == "nl"


def test_agent_request_rejects_unsupported_locale():
    with pytest.raises(ValidationError):
        AgentRequest(conversationId="conversation", message="Hello", locale="fr")


def test_persona_manifest_accepts_optional_localizations():
    manifest = PersonaManifest(
        name="generic",
        localizations={"nl": {"displayName": "Algemene assistent"}},
    )
    assert manifest.localizations["nl"].displayName == "Algemene assistent"
