"""Render attributed preparation evidence; never determine capacity or execute deeds."""

import argparse
import json
from pathlib import Path

from artifact import inline, required_text, validate_metadata, write_artifact

TOPICS = ("identity", "scanner", "understanding", "consequences", "free_will", "pressure", "authority", "approval")
TEXT = {
    "en": {
        "title": "Identity, capacity and execution preparation",
        "pending": "UNRESOLVED - human checks and responsible notary review required; no recommendation to proceed",
        "boundary": "No identity certification or chat-based capacity determination. Bevoegdheid (legal authority) is distinct from wilsbekwaamheid (decision-making capacity). This working document is never signed or executed.",
        "questions": "Agent-proposed questions and human/external tasks",
        "identity": "Inspect the original physical identity document and compare the person; an uploaded image is not certification.",
        "scanner": "Have an authorized human perform the official scanner check; record its source, date and limitations.",
        "understanding": "Ask the client to explain the supplied deed in their own words.",
        "consequences": "Ask what legal and practical consequences the client understands.",
        "free_will": "Ask whether the choice is voluntary and what alternatives the client considered.",
        "pressure": "Ask privately about pressure, threats, dependence or someone answering for the client.",
        "authority": "Separately review authority to act and any powers of attorney; this does not establish capacity.",
        "approval": "Ask the responsible notary which approvals and unresolved conditions remain before the appointment.",
        "observations": "Supplied observations (not agent findings)",
        "headers": "ID / correction of | Topic | Observation | Recorded by | Source",
        "missing": "Missing supplied evidence",
        "escalation": "Escalations - unresolved",
        "coercion": "Possible coercion",
        "capacity_uncertain": "Uncertain capacity",
        "contradiction": "Contradictory facts or instructions",
        "missing_approval": "Missing approval",
        "other": "Other uncertainty",
        "explanation": "Plain-language explanation of supplied deed - Netherlands context, not a certified instrument",
        "clause": "Supplied clause",
        "meaning": "Draft explanation",
        "unknown": "Unknown / ask responsible notary",
        "signing": "Supplied signing/execution claims (not independent verification)",
        "no_signing": "No explicit supplied signature/execution evidence; status unknown. Draft preparation never means signed.",
        "signed": "Reported signed",
        "executed": "Reported executed",
        "unsigned": "Reported unsigned",
        "actions": "Pending human actions",
        "followup": "Resolve discrepancies, assess capacity in person, review the deed and obtain missing approvals through authorized channels. Preserve original evidence and append corrections; review affected earlier drafts.",
    },
    "nl": {
        "title": "Voorbereiding identiteit, wilsbekwaamheid en passeren",
        "pending": "ONOPGELOST - menselijke controles en beoordeling door verantwoordelijke notaris vereist; geen advies om door te gaan",
        "boundary": "Geen identiteitscertificering of vaststelling van wilsbekwaamheid via chat. Bevoegdheid staat los van wilsbekwaamheid. Dit werkdocument is nooit ondertekend of gepasseerd.",
        "questions": "Door agent voorgestelde vragen en menselijke/externe taken",
        "identity": "Bekijk het originele fysieke identiteitsdocument en vergelijk de persoon; een geuploade afbeelding is geen certificering.",
        "scanner": "Laat een bevoegde medewerker de officiele scannercontrole uitvoeren; noteer bron, datum en beperkingen.",
        "understanding": "Vraag de client de aangeleverde akte in eigen woorden uit te leggen.",
        "consequences": "Vraag welke juridische en praktische gevolgen de client begrijpt.",
        "free_will": "Vraag of de keuze vrijwillig is en welke alternatieven de client heeft overwogen.",
        "pressure": "Vraag zonder derden naar druk, bedreiging, afhankelijkheid of iemand die voor de client antwoordt.",
        "authority": "Beoordeel afzonderlijk de bevoegdheid en eventuele volmachten; dit stelt wilsbekwaamheid niet vast.",
        "approval": "Vraag de verantwoordelijke notaris welke goedkeuringen en onopgeloste voorwaarden nog ontbreken.",
        "observations": "Aangeleverde observaties (geen bevindingen van de agent)",
        "headers": "ID / correctie van | Onderwerp | Observatie | Vastgelegd door | Bron",
        "missing": "Aangeleverd bewijs ontbreekt",
        "escalation": "Escalaties - onopgelost",
        "coercion": "Mogelijke dwang",
        "capacity_uncertain": "Onzekere wilsbekwaamheid",
        "contradiction": "Tegenstrijdige feiten of instructies",
        "missing_approval": "Ontbrekende goedkeuring",
        "other": "Overige onzekerheid",
        "explanation": "Uitleg aangeleverde akte in gewone taal - Nederlandse context, geen gewaarmerkte akte",
        "clause": "Aangeleverde bepaling",
        "meaning": "Conceptuitleg",
        "unknown": "Onbekend / vraag verantwoordelijke notaris",
        "signing": "Aangeleverde ondertekenings-/passeerverklaringen (niet onafhankelijk geverifieerd)",
        "no_signing": "Geen expliciet aangeleverd ondertekenings-/passeerbewijs; status onbekend. Een concept voorbereiden betekent nooit ondertekend.",
        "signed": "Volgens bron ondertekend",
        "executed": "Volgens bron gepasseerd",
        "unsigned": "Volgens bron niet ondertekend",
        "actions": "Openstaande menselijke acties",
        "followup": "Los verschillen op, beoordeel wilsbekwaamheid persoonlijk, controleer de akte en verkrijg ontbrekende goedkeuringen via bevoegde kanalen. Behoud origineel bewijs en voeg correcties toe; beoordeel eerdere getroffen concepten opnieuw.",
    },
}


def prepare(data: dict) -> dict:
    validate_metadata(data)
    labels = TEXT[data["locale"]]
    sources = {source["reference"]: source for source in data["sources"]}
    if len(sources) != len(data["sources"]):
        raise ValueError("Source references must be unique")

    def records(field: str) -> list:
        value = data.get(field, [])
        if not isinstance(value, list) or len(value) > 1000 or any(not isinstance(row, dict) for row in value):
            raise ValueError(f"{field} must be a list of at most 1000 objects")
        return value

    def source_of(row: dict) -> str:
        source = required_text(row.get("source"), "source")
        if source not in sources:
            raise ValueError("Record requires a reference from sources")
        return source

    lines = [labels["pending"], "", labels["boundary"], "", f"## {labels['questions']}"]
    lines.extend(f"- {labels[topic]}" for topic in TOPICS)
    lines.extend(["", f"## {labels['observations']}", f"| {labels['headers']} |", "| --- | --- | --- | --- | --- |"])
    seen: set[str] = set()
    supplied_topics: set[str] = set()
    for row in records("observations"):
        entry_id = required_text(row.get("id"), "observation id")
        if entry_id in seen:
            raise ValueError("Observation IDs must be unique")
        topic = row.get("topic")
        if topic not in TOPICS:
            raise ValueError("Unknown observation topic")
        source = source_of(row)
        correction = row.get("correction_of")
        if correction is not None and (not isinstance(correction, str) or correction not in seen):
            raise ValueError("correction_of must reference an earlier observation")
        seen.add(entry_id)
        if sources[source]["kind"] != "assumption":
            supplied_topics.add(topic)
        cells = [
            entry_id + (f" / {correction}" if correction else ""),
            labels[topic],
            required_text(row.get("text"), "observation text"),
            required_text(row.get("recorded_by"), "recorded_by"),
            source,
        ]
        lines.append("| " + " | ".join(inline(cell) for cell in cells) + " |")
    lines.extend(["", f"## {labels['escalation']}"])
    missing = [topic for topic in TOPICS if topic not in supplied_topics]
    lines.extend(f"- {labels['missing']}: {labels[topic]}" for topic in missing)
    for row in records("concerns"):
        category = row.get("category")
        if category not in ("coercion", "capacity_uncertain", "contradiction", "missing_approval", "other"):
            raise ValueError("Unknown concern category")
        lines.append(
            f"- {labels[category]}: {inline(required_text(row.get('text'), 'concern text'))} ({inline(source_of(row))})"
        )
    lines.extend(["", f"## {labels['explanation']}"])
    clauses = records("clauses")
    if not clauses:
        lines.append(labels["unknown"])
    for row in clauses:
        source = source_of(row)
        reference = required_text(row.get("reference"), "clause reference")
        excerpt = required_text(row.get("excerpt"), "supplied excerpt")
        explanation = required_text(row.get("explanation"), "draft explanation")
        lines.extend(
            [
                f"- {labels['clause']}: {inline(reference)} ({inline(source)})",
                f"  > {inline(excerpt)}",
                f"- {labels['meaning']}: {inline(explanation)}",
            ]
        )
    lines.extend(["", f"## {labels['signing']}"])
    signing = records("signing_evidence")
    if not signing:
        lines.append(labels["no_signing"])
    for row in signing:
        source = source_of(row)
        if sources[source]["kind"] == "assumption":
            raise ValueError("Signing claims require explicit supplied evidence, not assumptions")
        claim = row.get("claim")
        if claim not in ("signed", "executed", "unsigned"):
            raise ValueError("Signing claim must be signed, executed or unsigned")
        lines.append(
            f"- {labels[claim]}: {inline(required_text(row.get('text'), 'signing evidence text'))} ({inline(source)})"
        )
    if len({row["claim"] for row in signing}) > 1 and any(row["claim"] == "unsigned" for row in signing):
        lines.append(labels["contradiction"] + ": " + labels["pending"])
    lines.extend(["", f"## {labels['actions']}", labels["followup"]])
    return {
        "dossier": data["dossier"],
        "locale": data["locale"],
        "sources": data["sources"],
        "artifact_type": labels["title"],
        "body": "\n".join(lines),
        "review_status": "unresolved",
        "document_status": "draft",
        "missing_evidence": missing,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--export", action="store_true")
    args = parser.parse_args()
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Input must be a JSON object")
        result = prepare(data)
        if args.export:
            artifact = write_artifact(result)
            result = {
                "artifact": artifact,
                "review_status": result["review_status"],
                "document_status": result["document_status"],
                "missing_evidence": result["missing_evidence"],
            }
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from exc
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
