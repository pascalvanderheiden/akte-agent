"""Persona import API — create a Kratos use-case from a threadlight manifest.

``POST /api/use-cases/import`` accepts a threadlight-design-compatible persona
manifest (the primary, deterministic path — no LLM) and maps it onto the three
core persona files Kratos already understands:

* ``SYSTEM_PROMPT.md`` — frontmatter (including supported manifest metadata)
  plus the manifest ``instructions`` as the body.
* ``.mcp.json``        — Copilot MCP config built from ``mcpServers``.

The persona is persisted (blob + local mirror) and registered live in
``app.state.registries`` so it is immediately selectable in the UI.

A secondary natural-language ``prompt`` path is supported: the prompt is
expanded into a manifest via the Foundry model and then runs through the exact
same deterministic mapping. See design spec §5.
"""

from __future__ import annotations

import json
import logging
import re

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import require_authenticated_user
from app.models import (
    ImportMcpServer,
    PersonaImportRequest,
    PersonaImportResponse,
    PersonaManifest,
)
from app.personas import require_not_retired
from app.services.blob_skill_service import BlobSkillService
from app.services.skill_registry import SkillRegistry

logger = logging.getLogger(__name__)

router = APIRouter()

_USE_CASE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

_auth_dep = Depends(require_authenticated_user)


# ─── Slug helpers ────────────────────────────────────────────────────────────


def _slugify(value: str) -> str:
    """Normalise an arbitrary string into a valid use-case slug.

    Produces a value matching ``^[a-z0-9][a-z0-9-]{0,63}$`` or ``"persona"``
    when nothing usable remains.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")[:64].strip("-")
    if not slug or not slug[0].isalnum():
        slug = f"persona-{slug}".strip("-")[:64] if slug else "persona"
    return slug or "persona"


async def _resolve_slug(
    base: str,
    *,
    registries: dict[str, SkillRegistry],
    blob_service: BlobSkillService,
    overwrite: bool,
    dedupe: bool,
) -> str:
    """Resolve the final slug, honouring overwrite / dedupe semantics.

    * ``overwrite`` → return ``base`` unchanged (existing persona replaced).
    * ``dedupe``    → return the first free ``base``, ``base-2``, ``base-3`` …
    * otherwise     → return ``base``; the caller raises 409 if it exists.
    """
    if overwrite:
        return base

    async def _taken(name: str) -> bool:
        return name in registries or await blob_service.use_case_exists(name)

    if not dedupe:
        return base

    if not await _taken(base):
        return base
    for n in range(2, 1000):
        candidate = f"{base}-{n}"[:64].strip("-")
        if not await _taken(candidate):
            return candidate
    raise HTTPException(status_code=409, detail="Unable to allocate a unique persona slug")


# ─── Manifest → persona files mapping ────────────────────────────────────────


def _build_system_prompt(manifest: PersonaManifest, slug: str) -> str:
    """Render SYSTEM_PROMPT.md (frontmatter + instructions body)."""
    display = manifest.displayName or manifest.name or slug.replace("-", " ").title()
    frontmatter: dict = {
        "name": display,
        "description": manifest.description,
        "sampleQuestions": list(manifest.sampleQuestions),
        "curated": True,
        "traits": list(manifest.traits),
        "workflow_model": manifest.workflow_model,
        "skills": [skill.model_dump(exclude_none=True, exclude_defaults=True) for skill in manifest.skills],
    }
    if manifest.localizations:
        frontmatter["localizations"] = {
            locale: localization.model_dump(exclude_unset=True)
            for locale, localization in manifest.localizations.items()
        }
    if manifest.routing is not None:
        frontmatter["routing"] = manifest.routing.model_dump(exclude_none=True)
    fm_yaml = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    body = manifest.instructions.strip() or f"You are {display}, an enterprise AI assistant."
    return f"---\n{fm_yaml}\n---\n\n{body}\n"


def _build_mcp_json(servers: list[ImportMcpServer]) -> str:
    """Render .mcp.json (Copilot MCP config) from the manifest MCP servers.

    ``.mcp.json`` is loaded verbatim into the Copilot SDK session config, so every
    entry must be directly runnable and match the runtime's ``MCPServerConfig``
    shape: command-backed servers always use ``type: "local"`` (the manifest's
    ``transport`` — e.g. an MCP-spec ``"stdio"`` hint — is not a runnable Kratos
    type), while URL-backed servers must declare a supported remote transport.
    """
    config: dict[str, dict] = {}
    for server in servers:
        if server.command:
            entry: dict[str, str | list[str]] = {"type": "local", "command": server.command}
            if server.args:
                entry["args"] = server.args
        elif server.url:
            if server.transport not in ("http", "sse"):
                raise HTTPException(status_code=422, detail={"code": "UNSUPPORTED_MCP_TRANSPORT"})
            entry = {"type": server.transport, "url": server.url}
        else:
            raise HTTPException(status_code=422, detail={"code": "UNSUPPORTED_PACKAGE_DEPENDENCY"})
        config[server.name] = entry
    return json.dumps(config, indent=2) + "\n"


# ─── Natural-language (secondary) path ───────────────────────────────────────

_NL_SYSTEM_PROMPT = """\
You convert a short natural-language description of an AI agent persona into a
strict JSON manifest. Respond with ONLY a JSON object, no prose, matching:

{
  "name": "short human name",
  "description": "one sentence",
  "instructions": "the system prompt body — how the agent should behave",
  "sampleQuestions": ["3-5 example user questions"],
  "skills": [{"name": "kebab-name", "description": "what it does"}],
  "mcpServers": [],
  "traits": ["optional descriptive traits"],
  "workflow_model": "agent"
}

Keep instructions concise and actionable. Do not invent MCP servers or skill
packages the user did not ask for.\
"""


async def _expand_prompt_to_manifest(prompt: str) -> PersonaManifest:
    """Expand a natural-language prompt into a PersonaManifest via the model."""
    from app.routers.admin_analysis import _call_llm

    raw = await _call_llm(_NL_SYSTEM_PROMPT, prompt, json_mode=True)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("NL manifest expansion returned invalid JSON: %s", raw[:500])
        raise HTTPException(status_code=502, detail="Model returned invalid manifest JSON") from exc
    try:
        return PersonaManifest.model_validate(data)
    except Exception as exc:  # noqa: BLE001 — surface validation as 502 (model output)
        logger.error("NL manifest failed validation: %s", exc)
        raise HTTPException(status_code=502, detail=f"Model manifest failed validation: {exc}") from exc


# ─── Endpoint ────────────────────────────────────────────────────────────────


@router.post("/import", response_model=PersonaImportResponse, status_code=201)
async def import_persona(
    request: Request,
    body: PersonaImportRequest,
    _principal: dict = _auth_dep,
) -> PersonaImportResponse:
    """Create a Kratos persona from a threadlight manifest (or NL prompt).

    Returns ``201`` with the created persona summary. ``409`` when the slug is
    taken and neither ``overwrite`` nor ``dedupe`` apply. ``422`` for an invalid
    request body. ``502`` when the optional NL expansion fails.
    """
    blob_service: BlobSkillService | None = getattr(request.app.state, "blob_skill_service", None)
    registries: dict[str, SkillRegistry] | None = getattr(request.app.state, "registries", None)
    if blob_service is None or registries is None:
        raise HTTPException(status_code=503, detail="Persona storage is not initialised")

    manifest = body.manifest or await _expand_prompt_to_manifest(body.prompt or "")
    if any(skill.package for skill in manifest.skills) or any(
        server.registry and not (server.url or server.command) for server in manifest.mcpServers
    ):
        raise HTTPException(status_code=422, detail={"code": "UNSUPPORTED_PACKAGE_DEPENDENCY"})

    base_slug = _slugify(body.name or manifest.name)
    require_not_retired(base_slug)
    if not _USE_CASE_NAME_RE.match(base_slug):
        raise HTTPException(status_code=422, detail="Could not derive a valid persona name")

    already_exists = base_slug in registries or await blob_service.use_case_exists(base_slug)
    if already_exists and not body.overwrite and not body.dedupe:
        raise HTTPException(status_code=409, detail=f"Persona '{base_slug}' already exists")

    slug = await _resolve_slug(
        base_slug,
        registries=registries,
        blob_service=blob_service,
        overwrite=body.overwrite,
        dedupe=body.dedupe,
    )
    replaced = body.overwrite and (slug in registries or await blob_service.use_case_exists(slug))

    system_prompt_md = _build_system_prompt(manifest, slug)
    mcp_json = _build_mcp_json(manifest.mcpServers)

    try:
        files = await blob_service.create_use_case(
            slug,
            system_prompt_md=system_prompt_md,
            mcp_json=mcp_json,
            overwrite=body.overwrite,
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=f"Persona '{slug}' already exists") from exc

    registry = SkillRegistry()
    await registry.load(
        slug,
        blob_service,
        local_root=str(blob_service.local_base_dir),
    )
    registries[slug] = registry

    display = manifest.displayName or manifest.name or slug.replace("-", " ").title()
    logger.info("Imported persona '%s' (replaced=%s, skills=%d)", slug, replaced, len(registry.skills))
    return PersonaImportResponse(
        name=slug,
        displayName=display,
        description=manifest.description,
        skillCount=len(registry.skills),
        created=not replaced,
        files=files,
    )
