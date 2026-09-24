"""Non-destructive retirement policy, independent of stored persona copies."""

from collections.abc import Mapping

from fastapi import HTTPException

RETIRED_PERSONAS = frozenset(
    {
        "generic",
        "clinician-visit-prep",
        "finance-close",
        "hr-onboarding",
        "insurance",
        "it-service-desk",
        "plant-floor-supervisor",
        "retail-banking",
        "sales-account-review",
        "wealth-management",
    }
)


class PersonaUnavailable(HTTPException):
    def __init__(self, status_code: int = 410) -> None:
        super().__init__(status_code=status_code, detail={"code": "PERSONA_UNAVAILABLE"})


class PersonaMismatch(HTTPException):
    """Raised when an explicit persona selection conflicts with a conversation's
    immutable stored identity. Distinct from ``PersonaUnavailable`` — the
    persona itself is fine, it just isn't the one this conversation belongs to.
    """

    def __init__(self) -> None:
        super().__init__(status_code=409, detail={"code": "PERSONA_MISMATCH"})


def require_not_retired(use_case: str) -> None:
    if use_case in RETIRED_PERSONAS:
        raise PersonaUnavailable()


def require_identified_history(use_case: str) -> None:
    """Reject continuations whose historical persona identity was never stored."""
    if not use_case.strip():
        raise PersonaUnavailable()


def require_available(use_case: str, registries: Mapping[str, object]) -> None:
    require_not_retired(use_case)
    if use_case not in registries:
        raise PersonaUnavailable(status_code=404)


def require_persona_match(requested: str | None, stored: str) -> None:
    """Reject an explicit persona selection that conflicts with a conversation's
    stored identity. An omitted selection (``None``) silently continues against
    the stored persona instead of being coerced to a different default.
    """
    if requested is not None and requested != stored:
        raise PersonaMismatch()


def resolve_use_case(requested: str | None, stored: str | None, default: str = "akte-agent") -> str:
    """Derive the persona identity execution must use for this turn.

    Continuations of an identified conversation always execute as the
    conversation's stored persona — an explicit mismatch is rejected by
    ``require_persona_match`` before this is called, so a stored identity is
    trusted as-is here. Genuinely new work (no stored conversation yet) uses
    the caller's explicit selection, falling back to ``default``.
    """
    if stored:
        return stored
    return requested or default
