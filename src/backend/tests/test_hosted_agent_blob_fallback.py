"""Hosted-agent lazy loading when blob storage is unreachable.

The hosted agent's Foundry-managed compute often sits outside the storage
account's private endpoint. Once startup has established that the account is
unreachable, every later per-use-case lazy load must go straight to the
baked-in ``use-cases/`` directory — no blob round-trip, no added latency.

``src/hosted-agent/main.py`` is not an installed module (it is the entry point
of a separate image), so it is loaded here by path with the agentserver host
stubbed out.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ServiceRequestError

REPO = Path(__file__).resolve().parents[3]
HOSTED_AGENT_MAIN = REPO / "src" / "hosted-agent" / "main.py"


@pytest.fixture(scope="module")
def hosted_main() -> Iterator[ModuleType]:
    """Import ``src/hosted-agent/main.py`` with the agentserver runtime stubbed."""
    invocations = types.ModuleType("azure.ai.agentserver.invocations")

    class _Host:
        def invoke_handler(self, fn):
            return fn

        def run(self) -> None:  # pragma: no cover - never started in tests
            raise AssertionError("the agentserver host must not run during tests")

    invocations.InvocationAgentServerHost = _Host
    agentserver = types.ModuleType("azure.ai.agentserver")
    agentserver.invocations = invocations

    stubs = {"azure.ai.agentserver": agentserver, "azure.ai.agentserver.invocations": invocations}
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)

    spec = importlib.util.spec_from_file_location("hosted_agent_main", HOSTED_AGENT_MAIN)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["hosted_agent_main"] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop("hosted_agent_main", None)
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


class FakeBlobSkillService:
    """Minimal stand-in that counts every blob round-trip it is asked to make."""

    def __init__(
        self,
        *,
        available: bool,
        local_base_dir: Path | None = None,
        list_error: Exception | None = None,
        seed_error: Exception | None = None,
    ):
        self._available = available
        self._local_base_dir = local_base_dir or Path("use-cases")
        self._list_error = list_error
        self._seed_error = seed_error
        self.calls: list[str] = []

    @property
    def is_available(self) -> bool:
        return self._available

    async def _disable(self) -> None:
        self._available = False

    async def list_use_cases(self) -> list[str]:
        self.calls.append("list_use_cases")
        if self._list_error is not None:
            raise self._list_error
        return []

    async def seed_from_local(self) -> list[str]:
        self.calls.append("seed_from_local")
        if self._seed_error is not None:
            raise self._seed_error
        return []

    async def sync_to_local(self, use_case: str) -> None:
        self.calls.append("sync_to_local")

    def local_dir(self, use_case: str) -> Path:
        return self._local_base_dir / use_case

    async def list_skill_names(self, use_case: str) -> list[str]:
        self.calls.append("list_skill_names")
        return []


@pytest.fixture
def local_use_cases(tmp_path: Path) -> Path:
    """A baked-in ``use-cases/`` directory with one use-case."""
    uc = tmp_path / "use-cases" / "akte-agent"
    (uc / "skills" / "web-search").mkdir(parents=True)
    (uc / "SYSTEM_PROMPT.md").write_text(
        "---\ndisplayName: Akte Agent\ndescription: Local prompt\n---\n\nYou are a helpful assistant."
    )
    (uc / "skills" / "web-search" / "SKILL.md").write_text(
        "---\nname: web-search\ndescription: Real-time internet search\nenabled: true\n---\n\n# Web Search"
    )
    return tmp_path / "use-cases"


@pytest.fixture
def isolated_registries(hosted_main: ModuleType, local_use_cases: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hosted_main, "_registries", {})
    monkeypatch.setattr(hosted_main, "_settings", SimpleNamespace(use_cases_root=str(local_use_cases)))


@pytest.mark.usefixtures("isolated_registries")
async def test_unreachable_blob_makes_no_blob_call_on_lazy_load(
    hosted_main: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Startup determined blob is unreachable, so is_available is False.
    blob = FakeBlobSkillService(available=False)
    monkeypatch.setattr(hosted_main, "_blob_service", blob)

    await hosted_main._ensure_registry("akte-agent")

    assert blob.calls == []
    registry = hosted_main._registries["akte-agent"]
    assert "You are a helpful assistant." in registry.system_prompt
    assert "web-search" in registry.skills


@pytest.mark.usefixtures("isolated_registries")
async def test_reachable_blob_with_no_content_still_tries_blob_then_falls_back(
    hosted_main: ModuleType, local_use_cases: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Content gap for one use-case — not an account-level failure — so this
    # use-case must still attempt blob first (unchanged behavior).
    blob = FakeBlobSkillService(available=True, local_base_dir=local_use_cases.parent / "blob-cache")
    monkeypatch.setattr(hosted_main, "_blob_service", blob)

    await hosted_main._ensure_registry("akte-agent")

    assert "sync_to_local" in blob.calls
    assert blob.is_available
    registry = hosted_main._registries["akte-agent"]
    assert "You are a helpful assistant." in registry.system_prompt
    assert "web-search" in registry.skills


@pytest.mark.parametrize(
    "error",
    [
        HttpResponseError(response=SimpleNamespace(status_code=403, headers={}, reason="Forbidden")),
        ClientAuthenticationError("no token"),
        ServiceRequestError("connection dropped"),
        TimeoutError(),
    ],
)
async def test_startup_list_failure_disables_blob(hosted_main: ModuleType, error: Exception) -> None:
    blob = FakeBlobSkillService(available=True, list_error=error)

    await hosted_main._seed_or_disable_blob(blob)

    assert not blob.is_available
    # An account-level failure on the reachability probe must short-circuit
    # before ever attempting the seed/upload.
    assert blob.calls == ["list_use_cases"]


async def test_startup_list_keeps_blob_for_transient_errors(hosted_main: ModuleType) -> None:
    blob = FakeBlobSkillService(
        available=True,
        list_error=HttpResponseError(response=SimpleNamespace(status_code=503, headers={}, reason="Unavailable")),
    )

    await hosted_main._seed_or_disable_blob(blob)

    assert blob.is_available


async def test_startup_upload_failure_leaves_blob_enabled(hosted_main: ModuleType) -> None:
    """A read-capable identity denied on upload alone must not disable blob.

    ``list_use_cases`` (the reachability probe) succeeds here — only the
    subsequent seed/upload fails with 403 — so this is a write-scoped
    permission gap, not proof the account is unreachable.
    """
    blob = FakeBlobSkillService(
        available=True,
        seed_error=HttpResponseError(response=SimpleNamespace(status_code=403, headers={}, reason="Forbidden")),
    )

    await hosted_main._seed_or_disable_blob(blob)

    assert blob.is_available
    assert blob.calls == ["list_use_cases", "seed_from_local"]


@pytest.mark.usefixtures("isolated_registries")
async def test_startup_disable_then_lazy_load_makes_no_further_blob_calls(
    hosted_main: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full regression sequence: startup seed fails → disabled → no later blob call."""
    blob = FakeBlobSkillService(
        available=True,
        list_error=HttpResponseError(response=SimpleNamespace(status_code=403, headers={}, reason="Forbidden")),
    )
    monkeypatch.setattr(hosted_main, "_blob_service", blob)

    await hosted_main._seed_or_disable_blob(blob)
    assert not blob.is_available
    calls_after_seed = list(blob.calls)

    await hosted_main._ensure_registry("akte-agent")

    assert blob.calls == calls_after_seed
    registry = hosted_main._registries["akte-agent"]
    assert "You are a helpful assistant." in registry.system_prompt
    assert "web-search" in registry.skills
