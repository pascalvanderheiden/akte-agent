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
