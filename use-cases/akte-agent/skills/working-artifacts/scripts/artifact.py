"""Create temporary labeled Markdown drafts; never report failed writes as success."""

import argparse
import json
import os
import tempfile
from pathlib import Path

LABELS = {
    "en": {
        "type": "Artifact type",
        "status": "DRAFT - human notarial review required",
        "sources": "Sources and evidence",
        "temporary": "Temporary working download; may disappear on restart or expiry. Save reviewed material in the approved office system. Not a compliant archive or proof of an official action.",
        "supplied_fact": "Supplied fact (not independently verified)",
        "user_observation": "Attributed user observation",
        "assumption": "Assumption",
        "synthetic": "SYNTHETIC fixture",
        "verified_external": "External evidence (review verification reference)",
    },
    "nl": {
        "type": "Documenttype",
        "status": "CONCEPT - menselijke notariële beoordeling vereist",
        "sources": "Bronnen en bewijs",
        "temporary": "Tijdelijke werkdownload; kan verdwijnen bij herstart of verlopen opslag. Bewaar beoordeeld materiaal in het goedgekeurde kantoorsysteem. Geen conform archief of bewijs van een officiële handeling.",
        "supplied_fact": "Aangeleverd feit (niet onafhankelijk geverifieerd)",
        "user_observation": "Toegeschreven gebruikersobservatie",
        "assumption": "Aanname",
        "synthetic": "SYNTHETIC testgegevens",
        "verified_external": "Extern bewijs (controleer verificatiereferentie)",
    },
}
KINDS = {"supplied_fact", "user_observation", "assumption", "synthetic", "verified_external"}


def required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def inline(value: str) -> str:
    return value.replace("\n", " ").replace("\r", " ").replace("|", "\\|")


def validate_metadata(data: dict) -> None:
    required_text(data.get("dossier"), "dossier")
    if data.get("locale") not in LABELS:
        raise ValueError("locale must be en or nl")
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("At least one source reference and evidence kind is required")
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("Each source must be an object")
        required_text(source.get("reference"), "source reference")
        if source.get("kind") not in KINDS:
            raise ValueError("Invalid evidence kind")
        if source["kind"] == "verified_external":
            required_text(source.get("verification"), "external verification reference")


def render_artifact(data: dict) -> str:
    validate_metadata(data)
    artifact_type = required_text(data.get("artifact_type"), "artifact_type")
    body = required_text(data.get("body"), "body")
    labels = LABELS[data["locale"]]
    sources = []
    for source in data["sources"]:
        verification = f" - {inline(source['verification'])}" if source.get("verification") else ""
        sources.append(f"- {labels[source['kind']]}: {inline(source['reference'])}{verification}")
    return (
        f"# {inline(artifact_type)}\n\nDossier: {inline(data['dossier'])}\n\n"
        f"{labels['type']}: {inline(artifact_type)}\n\nStatus: {labels['status']}\n\n"
        f"## {labels['sources']}\n\n" + "\n".join(sources) + f"\n\n{body}\n\n---\n{labels['temporary']}\n"
    )


def write_artifact(data: dict) -> dict:
    content = render_artifact(data)
    fd, name = tempfile.mkstemp(prefix="akte-draft-", suffix=".md", dir="/tmp")
    path = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(content)
        if path.stat().st_size == 0:
            raise OSError("Generated artifact is empty")
    except OSError:
        path.unlink(missing_ok=True)
        raise
    return {"path": name, "status": "draft", "storage": "temporary", "bytes": path.stat().st_size}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Input must be a JSON object")
        result = write_artifact(data)
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from exc
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
