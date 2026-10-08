"""Kratos Agent — Hosted Agent entry point using the Invocations protocol.

Runs the same CopilotClient + SkillRegistry + CosmosService engine as the
Container App backend, but hosted on Microsoft Foundry via the
``azure-ai-agentserver-invocations`` protocol adapter on port 8088.

Key differences from the FastAPI backend:
- HTTP layer: InvocationAgentServerHost (port 8088) instead of FastAPI+uvicorn (8000)
- Compute: Foundry-managed auto-provision/deprovision instead of always-on Container App
- Identity: Dedicated Entra agent identity (injected by the platform)
- Protocol: Invocations (arbitrary JSON in, SSE out) — preserves our event schema
"""

import asyncio
import base64
import json
import logging
import os
import re
import sys
import time
import uuid
from typing import Literal

from azure.ai.agentserver.invocations import InvocationAgentServerHost
from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ServiceRequestError,
    ServiceResponseError,
)
from azure.cosmos.exceptions import CosmosHttpResponseError
from opentelemetry import trace
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

# Foundry reserves all FOUNDRY_* env vars; remap our non-reserved names.
# The platform auto-injects FOUNDRY_PROJECT_ENDPOINT but our Settings class reads FOUNDRY_ENDPOINT.
if "MODEL_DEPLOYMENT_NAME" in os.environ and "FOUNDRY_MODEL_DEPLOYMENT" not in os.environ:
    os.environ["FOUNDRY_MODEL_DEPLOYMENT"] = os.environ["MODEL_DEPLOYMENT_NAME"]
if "FOUNDRY_ENDPOINT" not in os.environ:
    # Platform injects FOUNDRY_PROJECT_ENDPOINT (e.g. https://host/api/projects/proj).
    # The CopilotClient provider needs just the base URL (https://host) without the
    # project path, since it appends /openai/deployments/<model> itself.
    project_ep = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "")
    if project_ep:
        # Strip /api/projects/... suffix to get the AI Services base URL
        idx = project_ep.find("/api/projects")
        os.environ["FOUNDRY_ENDPOINT"] = project_ep[:idx] if idx > 0 else project_ep
    # Also try AI_SERVICES_ENDPOINT set in agent.yaml
    elif "AI_SERVICES_ENDPOINT" in os.environ:
        os.environ["FOUNDRY_ENDPOINT"] = os.environ["AI_SERVICES_ENDPOINT"]

# Add the backend app to the Python path so we can reuse all existing modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from datetime import UTC, datetime

from app.config import Settings, get_settings
from app.hosted_agent_invoke import extract_invoke_locale, parse_invoke_payload
from app.locale import Locale
from app.models import (
    ContentEvent,
    DoneEvent,
    ErrorEvent,
    ThoughtEvent,
    ToolCallEvent,
    UsageEvent,
    UserInputRequestEvent,
)
from app.observability import (
    ReplicaInvocationOutcome,
    pre_handler_delay_ms,
    record_hosted_agent_initialization_phases,
    set_invocation_span_attributes,
    setup_telemetry,
)
from app.personas import (
    DEFAULT_USE_CASE,
    RETIRED_PERSONAS,
    PersonaMismatch,
    PersonaUnavailable,
    require_identified_history,
    require_not_retired,
    require_persona_match,
    resolve_use_case,
)
from app.services.blob_skill_service import BlobSkillService
from app.services.copilot_agent import CopilotAgent, InvocationTelemetry
from app.services.cosmos_service import (
    NETWORK_DENIAL_SIGNATURE,
    CosmosService,
    _denial_signature,
    cosmos_persistence_budget,
)
from app.services.skill_registry import SkillRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("azure.cosmos").setLevel(logging.WARNING)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure.identity").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# ─── Global state (initialised in startup) ──────────────────────────────────

_copilot_agent: CopilotAgent | None = None
_cosmos_service: CosmosService | None = None
_blob_service: BlobSkillService | None = None
_registries: dict[str, SkillRegistry] = {}
_settings: Settings | None = None
_telemetry_setup_started = False
_startup_state: Literal["not_started", "in_progress", "ready", "failed"] = "not_started"
_startup_task: asyncio.Task[None] | None = None
_startup_lock = asyncio.Lock()
_startup_readiness_source: Literal["early_init", "first_invocation_fallback"] = "first_invocation_fallback"

# Lazy per-use-case loading. A pre-warmed sandbox is unclaimed, so at warm time
# we don't yet know which use-case it will serve — loading all use-cases up
# front (each does a serial blob sync + skill parse) is the
# dominant cold-start cost. Instead we warm only the shared core (Cosmos, blob,
# Copilot agent) and load a single use-case's skills the first time it is
# actually requested, caching it in ``_registries`` thereafter.
_registry_lock = asyncio.Lock()

# Cold-start timing — populated by _startup() and surfaced in the warmup
# response so the backend can log the hosted-agent's own startup cost (vs the
# platform microVM boot) without needing App Insights access.
_startup_total_ms: float = 0.0
_startup_phases: dict[str, float] = {}

app = InvocationAgentServerHost()


# ─── Lifecycle ───────────────────────────────────────────────────────────────


def _is_blob_unreachable(exc: BaseException) -> bool:
    """True when *exc* means this instance cannot use the storage account at all.

    Network failures, credential failures and outright authorization denials are
    account-level: this compute simply cannot talk to the account (the hosted
    agent's Foundry-managed compute typically sits outside the storage account's
    private endpoint). Anything else — a 404, a conflict, a 5xx — is treated as
    per-request and leaves blob enabled.

    This is only applied to a *read* (list) call. ``seed_from_local`` also
    uploads, and a read-capable identity without write permission can 403 on
    an upload alone — that says nothing about whether reads work, so a write
    failure must never disable blob (see :func:`_seed_or_disable_blob`).
    """
    if isinstance(
        exc,
        TimeoutError | ServiceRequestError | ServiceResponseError | ClientAuthenticationError,
    ):
        return True
    return isinstance(exc, HttpResponseError) and exc.status_code in (401, 403)


async def _seed_or_disable_blob(blob_service: BlobSkillService) -> None:
    """Seed local use-cases into blob, disabling blob only if reads are unreachable.

    ``list_use_cases`` is called first, on its own, purely to establish
    reachability: it is a read, so a failure here means this compute cannot
    reach the account at all (the hosted agent's Foundry-managed compute
    typically sits outside the storage account's private endpoint). Reusing
    ``BlobSkillService._disable()`` records that verdict once — in the same
    place ``initialize()`` records it — so :func:`_ensure_registry` goes
    straight to the baked-in ``use-cases/`` directory from then on.

    Once reads are confirmed to work, ``seed_from_local`` (which lists again,
    then uploads any missing use-case) is attempted separately. A failure
    there — e.g. a read-capable identity denied on upload — is a write-only
    problem and must not disable blob: later reads still have a good chance
    of succeeding.
    """
    try:
        await blob_service.list_use_cases()
    except Exception as exc:
        if _is_blob_unreachable(exc):
            logger.warning(
                "Blob storage unreachable at startup (%s) — loading use-cases from local disk only",
                type(exc).__name__,
            )
            if hasattr(blob_service, "_unavailability_reason"):
                blob_service._unavailability_reason = type(exc).__name__  # noqa: SLF001
            await blob_service._disable()  # noqa: SLF001
        else:
            logger.exception("Failed to verify blob reachability at startup")
        return

    try:
        seeded = await blob_service.seed_from_local()
        if seeded:
            logger.info("Seeded %d use-case(s) into blob", len(seeded))
    except Exception:
        # Reads just succeeded above, so the account is reachable; this is a
        # write-scoped failure. Leave blob enabled — later reads (and thus the
        # per-use-case lazy loads) still have a chance to succeed.
        logger.exception("Failed to seed use-cases into blob storage")


def _emit_blob_local_only_telemetry(blob_service: BlobSkillService, settings: Settings) -> None:
    failure_reason = blob_service.unavailability_reason or "not_configured"
    timestamp = datetime.now(UTC).isoformat()
    model_deployment = settings.foundry_model_deployment or "(empty)"
    logger.warning(
        "HOSTED_AGENT_BLOB_LOCAL_ONLY reason=%s timestamp=%s environment=%s model=%s",
        failure_reason,
        timestamp,
        settings.environment,
        model_deployment,
        extra={
            "event_name": "HOSTED_AGENT_BLOB_LOCAL_ONLY",
            "failure_reason": failure_reason,
            "event_timestamp": timestamp,
            "environment": settings.environment,
            "model_deployment": model_deployment,
        },
    )


async def _startup() -> None:
    """Initialise the shared core — mirrors the FastAPI lifespan startup.

    Only use-case-agnostic services are initialised here (telemetry, Cosmos,
    blob, the Copilot SDK agent). Individual use-case skill registries are
    loaded lazily by :func:`_ensure_registry` on first use, so a pre-warmed
    sandbox becomes ready without paying to load all use-cases up front.
    """
    global _copilot_agent, _cosmos_service, _blob_service, _settings, _telemetry_setup_started
    global _startup_total_ms, _startup_phases

    import time as _time

    t_start = _time.monotonic()
    phases: dict[str, float] = {}

    def _mark(name: str, t0: float) -> None:
        phases[name] = round((_time.monotonic() - t0) * 1000, 1)

    settings = get_settings()

    # Setup OpenTelemetry
    t0 = _time.monotonic()
    # Telemetry is process-owned, not attempt-owned: retries must not add
    # duplicate exporters or logging handlers.
    if not _telemetry_setup_started:
        _telemetry_setup_started = True
        setup_telemetry(settings)
    _mark("telemetry", t0)

    # Cosmos, blob storage, and the Copilot SDK agent are mutually independent,
    # so initialise them concurrently. Cosmos init and the Copilot agent's token
    # pre-warm each cost ~1.7s serially; running them in parallel roughly halves
    # the shared-core warm time.
    t0 = _time.monotonic()
    cosmos_service = blob_service = copilot_agent = None
    tasks: list[asyncio.Task[None]] = []
    try:
        cosmos_service = CosmosService(settings)
        blob_service = BlobSkillService(settings)
        copilot_agent = CopilotAgent(settings)
        # The agent shares the (initially empty) _registries dict; lazy loads add
        # keys to it in place so the agent sees them without a reset.
        copilot_agent.set_registries(_registries)
        tasks = [
            asyncio.create_task(cosmos_service.initialize()),
            asyncio.create_task(blob_service.initialize()),
            asyncio.create_task(copilot_agent.start()),
        ]
        await asyncio.gather(*tasks)
        copilot_agent.set_cosmos_service(cosmos_service)
        _mark("core_parallel", t0)

        emitted_blob_local_only = False
        if not blob_service.is_available:
            _emit_blob_local_only_telemetry(blob_service, settings)
            emitted_blob_local_only = True

        # Seed local use-cases into blob if the container is empty. This only
        # uploads use-cases that are missing (a fast list + skip when already
        # seeded by a prior deploy / the backend), and is required so that the
        # lazy per-use-case loads below can pull skills from blob.
        if blob_service.is_available:
            t0 = _time.monotonic()
            await _seed_or_disable_blob(blob_service)
            _mark("seed", t0)
            if not blob_service.is_available and not emitted_blob_local_only:
                _emit_blob_local_only_telemetry(blob_service, settings)
    except BaseException:
        # gather() does not cancel siblings on failure. Drain them before
        # closing resources, and before the single-flight task allows a retry.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await _close_shared_core(copilot_agent, cosmos_service, blob_service)
        raise

    _copilot_agent = copilot_agent
    _cosmos_service = cosmos_service
    _blob_service = blob_service
    _settings = settings

    _startup_phases = phases
    _startup_total_ms = round((_time.monotonic() - t_start) * 1000, 1)
    try:
        record_hosted_agent_initialization_phases(phases)
    except Exception:
        logger.warning("Failed to record hosted-agent initialization metrics", exc_info=True)

    logger.info(
        "Kratos Hosted Agent core started in %.0fms (phases=%s) — environment=%s model=%s",
        _startup_total_ms,
        phases,
        _settings.environment,
        _settings.foundry_model_deployment or "(empty)",
    )


async def _run_shared_core_startup() -> None:
    """Run startup once and publish its outcome for all waiting callers."""
    global _startup_state, _startup_task

    task = asyncio.current_task()
    try:
        await _startup()
    except BaseException:
        async with _startup_lock:
            if _startup_task is task:
                _startup_state = "failed"
                _startup_task = None
        raise
    else:
        async with _startup_lock:
            if _startup_task is task:
                _startup_state = "ready"
                _startup_task = None


async def _ensure_shared_core(
    readiness_source: Literal["early_init", "first_invocation_fallback"] = "first_invocation_fallback",
) -> ReplicaInvocationOutcome:
    """Ensure shared startup completes, returning this caller's startup outcome."""
    global _startup_state, _startup_task, _startup_readiness_source

    outcome: ReplicaInvocationOutcome
    async with _startup_lock:
        if _startup_state == "ready":
            return "warm"
        if _startup_state == "in_progress":
            task = _startup_task
            outcome = "joined-in-flight-initialization"
        else:
            task = asyncio.create_task(_run_shared_core_startup())
            _startup_task = task
            _startup_state = "in_progress"
            _startup_readiness_source = readiness_source
            outcome = "cold-initialized-by-this-request"

    if task is None:
        raise RuntimeError("Shared-core startup is in progress without a task")
    await asyncio.shield(task)
    return outcome


async def _ensure_registry(use_case: str) -> None:
    """Lazily load a single use-case's skill registry on first use.

    Loading is guarded by a lock and cached in ``_registries`` so concurrent or
    repeat requests for the same use-case load it only once. Falls back to the
    baked-in local ``use-cases/`` directory when blob is unavailable.

    Reachability is decided once, at startup (:func:`_startup`), and recorded on
    the blob service itself — so when blob is known-unreachable this makes no
    blob call at all and loads at local-disk speed.
    """
    require_not_retired(use_case)
    if use_case in _registries:
        return

    import time as _time

    async with _registry_lock:
        if use_case in _registries:
            return
        t0 = _time.monotonic()

        local_root = _settings.use_cases_root if _settings else "use-cases"

        # Prefer blob (so use-cases uploaded post-deploy are picked up), but ALWAYS
        # fall back to the baked-in local use-cases/ directory if blob is
        # unavailable OR the blob load fails / yields nothing. The hosted-agent's
        # Foundry-managed compute may not be able to reach the storage account
        # (private-endpoint only), so the local fallback is what keeps the
        # correct skills + system prompt loaded.
        registry: SkillRegistry | None = None
        if _blob_service is not None and _blob_service.is_available:
            try:
                candidate = SkillRegistry()
                await candidate.load(use_case, _blob_service)
                if candidate.system_prompt or candidate.skills:
                    registry = candidate
                else:
                    logger.warning(
                        "Blob load for '%s' returned no skills/prompt — falling back to local disk",
                        use_case,
                    )
            except Exception:
                logger.warning(
                    "Blob load failed for '%s' — falling back to local disk",
                    use_case,
                    exc_info=True,
                )

        if registry is None:
            try:
                candidate = SkillRegistry()
                await candidate.load(use_case, local_root=local_root)
                registry = candidate
            except Exception:
                logger.exception("Failed to lazy-load use-case '%s' from local disk", use_case)
                raise

        if not registry.system_prompt:
            raise PersonaUnavailable(status_code=404)
        _registries[use_case] = registry
        logger.info(
            "Lazy-loaded use-case '%s' (%d skills, prompt=%s) in %.0fms",
            use_case,
            len(getattr(registry, "skills", {}) or {}),
            bool(getattr(registry, "system_prompt", "")),
            (_time.monotonic() - t0) * 1000,
        )


async def _close_shared_core(
    copilot_agent: CopilotAgent | None,
    cosmos_service: CosmosService | None,
    blob_service: BlobSkillService | None,
) -> None:
    for service, method in ((copilot_agent, "stop"), (cosmos_service, "close"), (blob_service, "close")):
        if service is not None:
            try:
                await getattr(service, method)()
            except Exception:
                logger.warning("Failed to close %s", type(service).__name__, exc_info=True)


async def _shutdown() -> None:
    """Cleanup on shutdown."""
    await _close_shared_core(_copilot_agent, _cosmos_service, _blob_service)
    logger.info("Kratos Hosted Agent stopped")


# ─── Invocation Handler ─────────────────────────────────────────────────────

_TMP_FILE_PATTERN = re.compile(r"/tmp/([\w.\- /]+\.[a-zA-Z0-9]{1,10})")


def _collect_generated_files(response_text: str) -> list[tuple[str, bytes]]:
    """Scan the agent response for /tmp/ file paths and collect their contents.

    Returns a list of (relative_path, file_bytes) tuples for files that exist locally.
    relative_path may include subdirectories (e.g. 'pete_report/file.pdf').
    These will be streamed to the backend proxy via SSE events.
    """
    matches = _TMP_FILE_PATTERN.findall(response_text)
    if not matches:
        return []

    files: list[tuple[str, bytes]] = []
    for rel_path in set(matches):
        local_path = f"/tmp/{rel_path}"
        if not os.path.isfile(local_path):
            logger.warning("Referenced file not found locally: %s", local_path)
            raise FileNotFoundError("Referenced generated file is unavailable")
        try:
            with open(local_path, "rb") as f:
                data = f.read()
            files.append((rel_path, data))
            logger.info("Collected generated file: %s (%d bytes)", local_path, len(data))
        except OSError:
            logger.warning("Failed to read generated file %s", local_path, exc_info=True)
            raise
    return files


async def _stream_response_impl(
    invocation_id: str,
    conversation_id: str,
    message: str,
    use_case: str,
    mcp_access_tokens: dict[str, str] | None = None,
    token_source: dict | None = None,
    locale: Locale | None = None,
    model_selection: str = "auto",
    persistence_allowed: bool = True,
    persistence_observation: dict | None = None,
    cold_start: bool = False,
    replica_invocation_outcome: ReplicaInvocationOutcome | None = None,
    readiness_source: Literal["early_init", "first_invocation_fallback"] | None = None,
    pre_handler_ms: int | None = None,
):
    """Run the Copilot SDK agent and stream our SSE event schema."""
    invocation_telemetry = InvocationTelemetry(
        invocation_id=invocation_id,
        handler_started_at=time.monotonic(),
        cold_start=cold_start,
        replica_invocation_state=replica_invocation_outcome,
        readiness_source=readiness_source,
        pre_handler_ms=pre_handler_ms,
    )
    total_tool_calls = 0

    # Associate conversation with use-case
    _copilot_agent.set_conversation_use_case(conversation_id, use_case)
    # Register the signed-in user's per-MCP-server tokens so the SDK session
    # injects them as Authorization headers on the matching remote MCP servers.
    _copilot_agent.set_conversation_mcp_tokens(conversation_id, mcp_access_tokens or {})

    # Emit a keys-only diagnostic (no token values) so the backend can log which
    # channel delivered the OBO tokens. The proxy logs and drops this event; it
    # never reaches the user or the model.
    if token_source is not None:
        yield f"data: {json.dumps({'event': 'kratos_diag', 'data': token_source})}\n\n".encode()

    try:
        # Persist user message to Cosmos (non-fatal — agent works without persistence)
        from datetime import datetime

        from app.models import Message, MessageRole

        user_message = Message(
            id=str(uuid.uuid4()),
            conversationId=conversation_id,
            role=MessageRole.USER,
            content=message,
            createdAt=datetime.now(UTC),
        )
        if persistence_allowed:
            try:
                await _cosmos_service.upsert_message(user_message)
                if persistence_observation is not None:
                    persistence_observation["user_message_persisted"] = True
            except Exception as exc:
                if persistence_observation is not None and _persistence_failure_is_blocked(exc):
                    persistence_observation["blocked"] = True
                logger.warning("Failed to persist user message to Cosmos (non-fatal)", exc_info=True)

        # Stream events from CopilotAgent
        assistant_content_parts: list[str] = []
        collected_thoughts: list[str] = []
        collected_tool_calls: list[dict] = []
        produced_models: set[str] = set()
        orchestrator_model = ""

        async for event in _copilot_agent.run(
            message=message,
            conversation_id=conversation_id,
            locale=locale,
            use_case=use_case,
            model_selection=model_selection,
            invocation_telemetry=invocation_telemetry,
            persist_session_mapping=persistence_allowed,
        ):
            if isinstance(event, ThoughtEvent):
                collected_thoughts.append(event.content)
                yield f"data: {json.dumps({'event': 'thought', 'data': event.model_dump()})}\n\n".encode()
            elif isinstance(event, ToolCallEvent):
                if event.status == "completed":
                    total_tool_calls += 1
                collected_tool_calls.append(event.model_dump())
                yield f"data: {json.dumps({'event': 'tool_call', 'data': event.model_dump()})}\n\n".encode()
            elif isinstance(event, UsageEvent):
                if event.model:
                    produced_models.add(event.model)
                    if event.agentName == "orchestrator":
                        orchestrator_model = event.model
                yield f"data: {json.dumps({'event': 'usage', 'data': event.model_dump()})}\n\n".encode()
            elif isinstance(event, ContentEvent):
                assistant_content_parts.append(event.content)
                yield f"data: {json.dumps({'event': 'content', 'data': event.model_dump()})}\n\n".encode()
            elif isinstance(event, UserInputRequestEvent):
                yield f"data: {json.dumps({'event': 'user_input_request', 'data': event.model_dump()})}\n\n".encode()
            elif isinstance(event, ErrorEvent):
                yield f"data: {json.dumps({'event': 'error', 'data': event.model_dump()})}\n\n".encode()

        invocation_telemetry.mark_agent_stream_complete()

        # Persist assistant response
        full_response = "".join(assistant_content_parts)

        # Stream generated files to the backend proxy so it can serve them
        # from its own /tmp. This avoids needing blob access from the hosted
        # agent container (which is outside the VNet).
        try:
            generated_files = _collect_generated_files(full_response)
        except OSError:
            error = ErrorEvent(
                code="DOWNLOAD_ERROR",
                message="Generated file is unavailable for download. Request a new draft.",
            )
            yield f"data: {json.dumps({'event': 'error', 'data': error.model_dump()})}\n\n".encode()
            generated_files = []
        for filename, data in generated_files:
            file_event = {
                "event": "file_content",
                "data": {
                    "filename": filename,
                    "content": base64.b64encode(data).decode("ascii"),
                },
            }
            yield f"data: {json.dumps(file_event)}\n\n".encode()

        stats = _copilot_agent.get_run_stats(conversation_id)
        elapsed_ms = invocation_telemetry.complete()
        run_stats = {
            "totalDurationMs": elapsed_ms,
            "totalToolCalls": total_tool_calls,
            "promptTokens": stats["prompt_tokens"],
            "completionTokens": stats["completion_tokens"],
            "reasoningTokens": stats.get("reasoning_tokens", 0),
            "totalTokens": stats["total_tokens"],
            "timeToFirstTokenMs": stats["time_to_first_token_ms"],
            "modelLatencyMs": stats["model_latency_ms"],
        }
        assistant_message = Message(
            id=str(uuid.uuid4()),
            conversationId=conversation_id,
            role=MessageRole.ASSISTANT,
            content=full_response,
            metadata={
                "thoughts": collected_thoughts,
                "toolCalls": collected_tool_calls,
                "runStats": run_stats,
                "model": orchestrator_model or model_selection,
                "models": sorted(produced_models),
            },
            createdAt=datetime.now(UTC),
        )
        if persistence_allowed:
            try:
                await _cosmos_service.upsert_message(assistant_message)
                if persistence_observation is not None:
                    persistence_observation["assistant_message_persisted"] = True
            except Exception as exc:
                if persistence_observation is not None and _persistence_failure_is_blocked(exc):
                    persistence_observation["blocked"] = True
                logger.warning(
                    "Failed to persist assistant message to Cosmos (non-fatal)",
                    exc_info=True,
                )

        # Done event
        done = DoneEvent(
            conversationId=conversation_id,
            totalDurationMs=elapsed_ms,
            totalToolCalls=total_tool_calls,
            promptTokens=run_stats["promptTokens"],
            completionTokens=run_stats["completionTokens"],
            reasoningTokens=run_stats["reasoningTokens"],
            totalTokens=run_stats["totalTokens"],
            timeToFirstTokenMs=run_stats["timeToFirstTokenMs"],
            modelLatencyMs=run_stats["modelLatencyMs"],
        )
        done_payload = done.model_dump()
        yield f"data: {json.dumps({'event': 'done', 'data': done_payload})}\n\n".encode()

    except Exception:
        logger.exception("Agent failed for conversation=%s", conversation_id)
        error = ErrorEvent(message="An internal error occurred", code="AGENT_ERROR")
        yield f"data: {json.dumps({'event': 'error', 'data': error.model_dump()})}\n\n".encode()
    finally:
        invocation_telemetry.complete()

    # Final done signal for the invocations protocol
    yield f"event: done\ndata: {json.dumps({'invocation_id': invocation_id, 'conversation_id': conversation_id})}\n\n".encode()


def _persistence_failure_is_blocked(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ServiceRequestError, ServiceResponseError)):
        return True
    return (
        isinstance(exc, CosmosHttpResponseError)
        and exc.status_code == 403
        and _denial_signature(exc) == NETWORK_DENIAL_SIGNATURE
    )


async def _stream_response(
    invocation_id: str,
    conversation_id: str,
    message: str,
    use_case: str,
    mcp_access_tokens: dict[str, str] | None = None,
    token_source: dict | None = None,
    locale: Locale | None = None,
    model_selection: str = "auto",
    persistence_budget=None,
    persistence_allowed: bool = True,
    cold_start: bool = False,
    replica_invocation_outcome: ReplicaInvocationOutcome | None = None,
    readiness_source: Literal["early_init", "first_invocation_fallback"] | None = None,
    pre_handler_ms: int | None = None,
):
    """Run the hosted-agent stream under the invocation's shared Cosmos budget."""
    if persistence_budget is None:
        with cosmos_persistence_budget() as default_budget:
            persistence_budget = default_budget
    persistence_observation = {
        "persistence_mode": (
            "disabled"
            if not persistence_allowed
            else "unavailable"
            if _cosmos_service is None
            else "local"
            if getattr(_cosmos_service, "_messages_container", None) is None
            else "cosmos"
        ),
        "blocked": False,
        "user_message_persisted": False,
        "assistant_message_persisted": False,
    }
    stream = _stream_response_impl(
        invocation_id,
        conversation_id,
        message,
        use_case,
        mcp_access_tokens,
        token_source,
        locale,
        model_selection,
        persistence_allowed,
        persistence_observation,
        cold_start,
        replica_invocation_outcome,
        readiness_source,
        pre_handler_ms,
    )
    diagnostic_emitted = False

    def _diagnostic_event() -> bytes:
        return (
            "data: "
            + json.dumps(
                {
                    "event": "kratos_diag",
                    "data": {
                        **persistence_observation,
                        "persistence_budget_remaining_ms": max(0, int(persistence_budget.remaining_s * 1000)),
                    },
                }
            )
            + "\n\n"
        ).encode()

    try:
        while True:
            with cosmos_persistence_budget(persistence_budget):
                try:
                    event = await anext(stream)
                except StopAsyncIteration:
                    break
            if event.startswith(b"event: done"):
                yield _diagnostic_event()
                diagnostic_emitted = True
            yield event
    finally:
        with cosmos_persistence_budget(persistence_budget):
            await stream.aclose()
    if not diagnostic_emitted:
        yield _diagnostic_event()


@app.invoke_handler
async def handle_invoke(request: Request) -> Response:
    """Handle invocation requests — accepts the same payload as the FastAPI /api/agent/chat endpoint."""
    core_ready_before_ping = _startup_state == "ready"
    request.state.pre_handler_ms = pre_handler_delay_ms(request.headers)
    request.state.replica_invocation_state = await _ensure_shared_core()
    request.state.shared_core_startup = request.state.replica_invocation_state
    request.state.readiness_source = _startup_readiness_source
    request.state.cold_start = request.state.replica_invocation_state == "cold-initialized-by-this-request"

    # Read the request body exactly once and normalise it. The hosted agent is
    # invoked through several paths that frame the body differently:
    #   * ``azd ai agent invoke "msg"`` posts the message as text/plain (NOT JSON)
    #   * the Kratos backend proxy posts a JSON object
    #   * the platform keep-alive posts ``{"warmup": true}`` or an empty body
    # parse_invoke_payload coerces all of these into a dict (empty body → warmup),
    # so a plain-text CLI invoke no longer 400s on json.loads.
    data = parse_invoke_payload(await request.body())

    # Keep-warm fast-path: the backend pings the hosted agent periodically with
    # ``{"warmup": true}`` to stop the Foundry platform from scaling the container
    # to zero (which causes multi-second cold starts and gateway 408 timeouts on
    # the next real request). Running _startup() above already re-provisions and
    # initialises every service, so we return immediately without invoking the
    # model or persisting anything — this resets the platform idle timer cheaply.
    if data.get("warmup") is True:
        return JSONResponse(
            status_code=200,
            content={
                "status": "warm",
                "ready": _copilot_agent is not None,
                "core_ready_before_ping": core_ready_before_ping,
                "source_revision": os.environ.get("KRATOS_SOURCE_REVISION", "unknown"),
                "startup_ms": _startup_total_ms,
                "phases": _startup_phases,
                "loaded_use_cases": sorted(set(_registries) - RETIRED_PERSONAS),
            },
        )

    try:
        message = data.get("message") or data.get("input")
        if not isinstance(message, str) or not message.strip():
            raise ValueError('missing or empty "message" (or "input") field')

        conversation_id = data.get("conversationId", str(uuid.uuid4()))
        use_case = data.get("useCase")
        model_selection = data.get("selectedModelId") or data.get("modelSelection", "auto")
        runtime_foundry_endpoint = str(data.get("foundryEndpoint") or "")
        runtime_foundry_deployment = str(data.get("foundryModelDeployment") or "")
        persistence_allowed = data.get("persistenceAllowed") is not False
        raw_persistence_budget_ms = data.get("persistenceBudgetMs")
        persistence_budget_ms = max(0, raw_persistence_budget_ms) if type(raw_persistence_budget_ms) is int else None
        raw_preflight_use_case = data.get("preflightConversationUseCase")
        preflight_conversation_use_case = raw_preflight_use_case if isinstance(raw_preflight_use_case, str) else None
        preflight_conversation_checked = data.get("preflightConversationChecked") is True

        # Per-MCP-server user tokens for On-Behalf-Of (kept out of the message
        # text so they are never visible to the model). Coerce to a clean
        # {str: str} map and ignore anything malformed.
        raw_tokens = data.get("mcpAccessTokens")
        mcp_access_tokens: dict[str, str] = (
            {str(k): v for k, v in raw_tokens.items() if isinstance(v, str) and v}
            if isinstance(raw_tokens, dict)
            else {}
        )
        body_token_keys = sorted(mcp_access_tokens.keys())
        tag_token_keys: list[str] = []

        # The proxy may embed metadata tags in the input when the Invocations
        # gateway strips custom JSON fields.  Parse them and remove from the
        # message so the CopilotAgent receives a clean user message.
        if isinstance(message, str):
            # Parse <use_case> tag (fallback when gateway strips useCase field)
            uc_match = re.search(r"<use_case>\s*(\S+?)\s*</use_case>", message)
            if uc_match:
                if use_case is None or use_case == DEFAULT_USE_CASE:
                    use_case = uc_match.group(1)
                    logger.info(
                        "Parsed useCase='%s' from input tag (gateway fallback)",
                        use_case,
                    )
                message = message[: uc_match.start()] + message[uc_match.end() :]

            # Strip <system_instructions> — the hosted agent sets the system
            # prompt via the registry, so the prepended copy is redundant.
            message = re.sub(
                r"<system_instructions>\s*.*?\s*</system_instructions>",
                "",
                message,
                flags=re.DOTALL,
            )

            persistence_match = re.search(
                r"<persistence_allowed>\s*(true|false)\s*</persistence_allowed>",
                message,
                flags=re.IGNORECASE,
            )
            if persistence_match:
                persistence_allowed = persistence_allowed and persistence_match.group(1).lower() == "true"
                message = message[: persistence_match.start()] + message[persistence_match.end() :]

            budget_match = re.search(
                r"<persistence_budget_ms>\s*(\d+)\s*</persistence_budget_ms>",
                message,
                flags=re.IGNORECASE,
            )
            if budget_match:
                if persistence_budget_ms is None:
                    persistence_budget_ms = int(budget_match.group(1))
                message = message[: budget_match.start()] + message[budget_match.end() :]

            preflight_match = re.search(
                r"<preflight_conversation_use_case>\s*(\S+?)\s*</preflight_conversation_use_case>",
                message,
            )
            if preflight_match:
                if preflight_conversation_use_case is None:
                    preflight_conversation_use_case = preflight_match.group(1)
                message = message[: preflight_match.start()] + message[preflight_match.end() :]

            checked_match = re.search(
                r"<preflight_conversation_checked>\s*true\s*</preflight_conversation_checked>",
                message,
                flags=re.IGNORECASE,
            )
            if checked_match:
                preflight_conversation_checked = True
                message = message[: checked_match.start()] + message[checked_match.end() :]

            # STRIP and IGNORE any <mcp_access_tokens> tag. OBO bearers are
            # delivered ONLY via the mcpAccessTokens JSON body field; a token in
            # the prompt would reach the model and GenAI message-content traces,
            # so the proxy never emits one. The ENTIRE tag block is always removed
            # so nothing reaches the model even if some other caller injects it,
            # and any keys found are logged (for observability) but never honored.
            def _consume_mcp_tag(m: "re.Match[str]") -> str:
                payload = m.group(1)
                try:
                    parsed = json.loads(payload)
                except (json.JSONDecodeError, ValueError):
                    parsed = None
                    logger.warning("Failed to parse <mcp_access_tokens> input tag")
                if isinstance(parsed, dict):
                    for k, v in parsed.items():
                        if isinstance(v, str) and v:
                            tag_token_keys.append(str(k))
                return ""

            message = re.sub(
                r"<mcp_access_tokens>\s*(.*?)\s*</mcp_access_tokens>",
                _consume_mcp_tag,
                message,
                flags=re.DOTALL,
            )
            tag_token_keys = sorted(set(tag_token_keys))

            # Clean up leading/trailing whitespace from tag removal
            message = message.strip()

        conversation_match = re.match(r"^\s*<conversation_id>(.*?)</conversation_id>", message, re.DOTALL)
        if conversation_match:
            from html import unescape

            if "conversationId" not in data:
                conversation_id = unescape(conversation_match.group(1))
            message = message[conversation_match.end() :].strip()
        locale, message = extract_invoke_locale(data, message)
        logger.info(
            "handle_invoke: useCase=%s conversation=%s registries=%s message_len=%d mcp_tokens=%s (body=%s tag=%s)",
            use_case,
            conversation_id,
            list(_registries.keys()),
            len(message),
            sorted(mcp_access_tokens.keys()),
            body_token_keys,
            tag_token_keys,
        )

    except (json.JSONDecodeError, ValueError) as e:
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_request",
                "message": str(e),
            },
        )

    # Lazy-load this conversation's use-case (cached after first use). A
    # pre-warmed sandbox warms only the shared core, so the first real request
    # for a given use-case pays a small one-time load instead of every sandbox
    # loading all use-cases up front.
    with cosmos_persistence_budget(
        persistence_budget_ms / 1000 if persistence_budget_ms is not None else None
    ) as persistence_budget:
        try:
            if runtime_foundry_endpoint and (
                runtime_foundry_endpoint != _copilot_agent.settings.foundry_endpoint
                or runtime_foundry_deployment != _copilot_agent.settings.foundry_model_deployment
            ):
                await _copilot_agent.update_config(runtime_foundry_endpoint, runtime_foundry_deployment)
            stored_use_case = None
            # A persistence-disabled backend run carries the identity result of
            # its successful preflight, avoiding an unbudgeted second lookup.
            if not persistence_allowed and preflight_conversation_checked:
                stored_use_case = preflight_conversation_use_case
                if stored_use_case:
                    require_identified_history(stored_use_case)
                    require_not_retired(stored_use_case)
                    require_persona_match(use_case, stored_use_case)
            elif _cosmos_service is not None:
                try:
                    existing = await _cosmos_service.get_conversation(
                        conversation_id, "default-user", raise_on_error=True
                    )
                except Exception as exc:
                    blocked = _persistence_failure_is_blocked(exc)
                    headers = {
                        "x-kratos-persistence-budget-remaining-ms": str(
                            max(0, int(persistence_budget.remaining_s * 1000))
                        )
                    }
                    if blocked:
                        headers["x-kratos-cosmos-path"] = "blocked"
                    return JSONResponse(
                        status_code=503,
                        headers=headers,
                        content={
                            "error": (
                                "Hosted-agent Cosmos private path is unavailable"
                                if blocked
                                else "Hosted-agent conversation identity could not be verified"
                            )
                        },
                    )
                if existing:
                    require_identified_history(existing.useCase)
                    require_not_retired(existing.useCase)
                    require_persona_match(use_case, existing.useCase)
                    stored_use_case = existing.useCase
            use_case = resolve_use_case(use_case, stored_use_case)
            require_not_retired(use_case)
            await _ensure_registry(use_case)
            set_invocation_span_attributes(
                trace.get_current_span(),
                use_case=use_case,
                cold_start=request.state.cold_start,
                replica_invocation_state=request.state.replica_invocation_state,
                readiness_source=request.state.readiness_source,
                pre_handler_ms=request.state.pre_handler_ms,
            )
        except (PersonaUnavailable, PersonaMismatch) as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return StreamingResponse(
        _stream_response(
            request.state.invocation_id,
            conversation_id,
            message,
            use_case,
            mcp_access_tokens,
            locale=locale,
            model_selection=model_selection,
            persistence_budget=persistence_budget,
            persistence_allowed=persistence_allowed,
            cold_start=request.state.cold_start,
            replica_invocation_outcome=request.state.replica_invocation_state,
            readiness_source=request.state.readiness_source,
            pre_handler_ms=request.state.pre_handler_ms,
            token_source={
                "mcp_token_body_keys": body_token_keys,
                "mcp_token_tag_keys": tag_token_keys,
                "mcp_token_effective_keys": sorted(mcp_access_tokens.keys()),
                # Confirms the OBO server URL is present in THIS sandbox's runtime
                # env (Foundry injects only env declared in agent.yaml). Without it
                # _apply_mcp_tokens cannot auto-attach the graph-obo MCP tool.
                "obo_env_url_present": bool(os.environ.get("OBO_MCP_SERVER_MCP_URL")),
                "obo_env_name": os.environ.get("OBO_MCP_SERVER_NAME", "graph-obo"),
                # Confirms the agent's LLM calls are routed through the APIM AI
                # gateway (so prompts/completions are captured). Empty => the
                # sandbox calls Foundry directly.
                "llm_gateway_host": (os.environ.get("LLM_GATEWAY_BASE_URL", "").split("//")[-1].split("/")[0]),
            },
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


async def _run_host() -> None:
    """Initialise before serving, keeping async services on the host's event loop."""
    try:
        await _ensure_shared_core(readiness_source="early_init")
    except Exception:
        logger.warning("Early shared-core initialization failed — next invocation will retry", exc_info=True)

    try:
        await app.run_async()
    finally:
        await _shutdown()


if __name__ == "__main__":
    asyncio.run(_run_host())
