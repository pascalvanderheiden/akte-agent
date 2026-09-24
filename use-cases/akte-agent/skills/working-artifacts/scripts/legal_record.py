"""Render supplied legal working records. No legal checks or external actions."""

import argparse
import difflib
import html
import json
import re
from datetime import date
from pathlib import Path
from string import Template

from artifact import required_text, validate_metadata, write_artifact

TYPES = {
    "research": ("Legal research brief", "Juridisch onderzoeksverslag"),
    "deed": ("Draft deed", "Conceptakte"),
    "translation": ("Working translation", "Werkvertaling"),
    "comparison": ("Supplied-version comparison", "Vergelijking aangeleverde versies"),
    "index": ("Dossier index and correspondence log", "Dossierindex en correspondentielog"),
    "delivery": ("Client-delivery draft - NOT SENT", "Concept aan client - NIET VERZONDEN"),
}
TOPICS = {
    "feasibility": ("Legal feasibility", "Juridische haalbaarheid"),
    "authority": ("Legal authority (bevoegdheid)", "Bevoegdheid"),
    "conflicts": ("Possible conflicts with law", "Mogelijke strijd met de wet"),
    "assumptions": ("Assumptions", "Aannames"),
}
FIELDS = {
    "parties": ("parties/names", "partijen/namen"),
    "date": ("date", "datum"),
    "provisions": ("provisions", "bepalingen"),
    "amount": ("amount", "bedrag"),
}


def safe(value: str) -> str:
    # Evidence must remain inert even when Markdown is rendered in a browser.
    text = html.escape(value, quote=False)
    text = re.sub(r"([\\`*_{}\[\]()#|])", r"\\\1", text)
    return re.sub(r"(?m)^(\s*)([-+]|\d+[.])(?=\s)", r"\1\\\2", text)


def iso(value: object, field: str) -> str | None:
    if value is None:
        return None
    text = required_text(value, field)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError(f"{field} must be an ISO date")
    date.fromisoformat(text)
    return text


def objects(value: object, field: str) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{field} must be an array of objects")
    return value


def build_record(data: dict) -> dict:
    validate_metadata(data)
    locale = data["locale"]

    def tr(en: str, nl: str) -> str:
        return en if locale == "en" else nl

    def unknown(en: str, nl: str) -> str:
        return f"[{tr('UNKNOWN', 'ONBEKEND')}: {tr(en, nl)}]"

    def text(value: object, en="not supplied", nl="niet aangeleverd") -> str:
        return safe(required_text(value, en)) if value is not None else unknown(en, nl)

    kind = data.get("kind")
    if kind not in TYPES:
        raise ValueError("Unsupported legal record kind")
    jurisdiction = required_text(data.get("jurisdiction"), "jurisdiction")
    if jurisdiction not in ("Netherlands", "Nederland"):
        raise ValueError("Clarify Netherlands jurisdiction before rendering")
    matter = required_text(data.get("matter"), "matter")
    as_of = iso(data.get("as_of"), "as_of")
    sources = {}
    for source in data["sources"]:
        reference = source["reference"]
        if reference in sources:
            raise ValueError("Source references must be unique")
        sources[reference] = source

    def ref(value: object) -> str:
        name = required_text(value, "source")
        if name not in sources:
            raise ValueError(f"Unknown source reference: {name}")
        return safe(name)

    body = [
        f"## {tr('Scope', 'Reikwijdte')}",
        tr("Jurisdiction: Netherlands", "Rechtsgebied: Nederland"),
        f"{tr('Matter', 'Zaak')}: {safe(matter)}",
        f"{tr('Review date', 'Beoordelingsdatum')}: {text(as_of)}",
        tr(
            "Current law is not independently checked by this renderer. Review supplied/retrieved sources, versions and applicability with the notary; this is not legal clearance.",
            "Dit script controleert actueel recht niet zelfstandig. Beoordeel aangeleverde/geraadpleegde bronnen, versies en toepasselijkheid met de notaris; dit is geen juridische goedkeuring.",
        ),
        f"## {tr('Source provenance and currency', 'Bronherkomst en actualiteit')}",
    ]
    for source in sources.values():
        issued = iso(source.get("date"), "source date")
        deadline = iso(source.get("review_after"), "review_after")
        body.append(
            f"- {safe(source['reference'])}; {tr('date', 'datum')}: {text(issued)}; "
            f"{tr('version', 'versie')}: {text(source.get('version'))}; "
            f"{tr('locator', 'vindplaats')}: {text(source.get('locator'))}"
        )
        if deadline:
            body.append(
                tr(
                    "Supplied review deadline (not statutory expiry): ",
                    "Aangeleverde herbeoordelingsdatum (geen wettelijke vervaldatum): ",
                )
                + deadline
            )
        if as_of and deadline and deadline < as_of:
            body.append(
                tr(
                    "STALE FOR REVIEW: supplied deadline passed.",
                    "VEROUDERD VOOR BEOORDELING: aangeleverde termijn verstreken.",
                )
            )
        elif issued and as_of and issued > as_of:
            body.append(
                tr(
                    "CONTRADICTION: source date follows review date.",
                    "TEGENSTRIJDIG: brondatum ligt na beoordelingsdatum.",
                )
            )
        else:
            body.append(
                tr(
                    "Currency not established; notarial assessment pending.",
                    "Actualiteit niet vastgesteld; notariële beoordeling open.",
                )
            )

    if kind == "research":
        checks = objects(data.get("checks"), "checks")
        if sorted(check.get("register", "") for check in checks) != ["BRP", "CTR", "Handelsregister"]:
            raise ValueError("Provide one plan row each for BRP, Handelsregister and CTR")
        body.append(f"## {tr('Register-check plan', 'Registercontroleplan')}")
        body.append(
            tr(
                "NOT PERFORMED by the assistant. Authorized access required; public web research is not an official person-specific register check. Supplied extracts do not establish clearance.",
                "NIET UITGEVOERD door de assistent. Bevoegde toegang vereist; openbaar webonderzoek is geen officiële persoonsgerichte registercontrole. Aangeleverde uittreksels zijn geen goedkeuring.",
            )
        )
        for check in checks:
            body.extend(
                [
                    f"### {check['register']}",
                    f"{tr('Relevance', 'Relevantie')}: {text(required_text(check.get('relevance'), 'relevance'))}",
                    f"{tr('Evidence required', 'Benodigd bewijs')}: {text(required_text(check.get('required_evidence'), 'required_evidence'))}",
                    f"{tr('Source', 'Bron')}: {ref(check['source']) if check.get('source') else unknown('missing extract', 'ontbrekend uittreksel')}",
                    f"{tr('Pending action', 'Open actie')}: {text(required_text(check.get('action'), 'action'))}",
                ]
            )
        findings = objects(data.get("findings", []), "findings")
        for finding in findings:
            if finding.get("topic") not in TOPICS:
                raise ValueError("Unknown research topic")
            ref(finding.get("source"))
            required_text(finding.get("text"), "finding text")
        for topic, labels in TOPICS.items():
            body.append(f"## {tr(*labels)}")
            selected = [finding for finding in findings if finding["topic"] == topic]
            for finding in selected:
                body.append(f"- {text(finding['text'])} ({ref(finding['source'])}; {text(finding.get('locator'))})")
            if not selected:
                body.append(unknown("research required", "onderzoek nodig"))
        body.append(
            tr(
                "Research plan: obtain missing dated extracts through authorized channels; check the applicable current official legislation/judgments and source versions; resolve contradictions before notarial conclusions.",
                "Onderzoeksplan: verkrijg ontbrekende gedateerde uittreksels via bevoegde kanalen; controleer toepasselijke actuele officiële wetgeving/rechtspraak en bronversies; los tegenstrijdigheden op voor notariële conclusies.",
            )
        )
    elif kind == "deed":
        fields = data.get("fields", {})
        if not isinstance(fields, dict) or fields.keys() - FIELDS.keys():
            raise ValueError("fields must contain only parties, date, provisions and amount")
        values = {"version": text(required_text(data.get("version"), "version")), "matter": safe(matter)}
        notes = []
        for field, labels in FIELDS.items():
            values[field] = unknown(*labels)
            if field not in fields:
                continue
            item = fields[field]
            if not isinstance(item, dict):
                raise ValueError("Each deed field must be an object")
            content = required_text(item.get("text"), "field text")
            source = ref(item.get("source"))
            approval = item.get("approval")
            if approval is not None:
                values[field] = safe(content)
                notes.append(
                    f"- {tr(*labels)}: {source}; {tr('supplied approval reference', 'aangeleverde goedkeuringsreferentie')}: {text(approval)}"
                )
            else:
                notes.append(f"- {tr('UNAPPROVED', 'NIET GOEDGEKEURD')}: {safe(content)} ({source})")
        template = Path(__file__).resolve().parent.parent / "references" / f"deed-{locale}.md"
        body.append(Template(template.read_text(encoding="utf-8")).substitute(values))
        if data.get("template_source"):
            notes.append(f"- {tr('Supplied template', 'Aangeleverd model')}: {ref(data['template_source'])}")
        research = data.get("research_sources", [])
        if not isinstance(research, list):
            raise ValueError("research_sources must be a list")
        for source in research:
            notes.append(f"- {tr('Research', 'Onderzoek')}: {ref(source)}")
        body.extend([f"## {tr('Field provenance and review', 'Herkomst velden en beoordeling')}", *notes])
    elif kind == "translation":
        original = required_text(data.get("original"), "original")
        translated = required_text(data.get("text"), "translation text")
        placeholders = re.findall(r"\[(?:UNKNOWN|ONBEKEND):[^\]]+\]", original)
        if any(placeholder not in translated for placeholder in placeholders):
            raise ValueError("Preserve all original unknown placeholders in the working translation")
        body.extend(
            [
                f"## {tr('NONCERTIFIED working translation', 'NIET-GECERTIFICEERDE werkvertaling')}",
                tr(
                    "No legal-equivalence claim. Netherlands jurisdiction unchanged.",
                    "Geen aanspraak op juridische gelijkwaardigheid. Nederlands rechtsgebied ongewijzigd.",
                ),
                f"{ref(data.get('source'))}; {tr('source version', 'bronversie')}: {text(required_text(data.get('source_version'), 'source_version'))}",
                text(translated),
                f"### {tr('Original supplied text', 'Originele aangeleverde tekst')}",
                text(original),
                tr(
                    "Dutch terms: akte (notarial instrument/deed); comparant (person appearing before the notary); bevoegdheid (legal authority); wilsbekwaamheid (decision-making capacity); testament (will). Meaning remains subject to contextual notarial review.",
                    "Nederlandse termen: akte, comparant, bevoegdheid, wilsbekwaamheid en testament blijven behouden. Betekenis en vertaling vereisen contextuele notariële beoordeling.",
                ),
            ]
        )
    elif kind == "comparison":
        versions = objects(data.get("versions"), "versions")
        if len(versions) != 2:
            raise ValueError("Exactly two supplied versions are required")
        identities = []
        for version in versions:
            identities.append(required_text(version.get("id"), "version id"))
            required_text(version.get("text"), "version text")
            body.append(f"### {text(version['id'])} ({ref(version.get('source'))})")
        if identities[0] == identities[1]:
            raise ValueError("Version identities must differ")
        body.append(
            tr(
                "Literal comparison of supplied text only; no inferred history or legal-equivalence conclusion.",
                "Letterlijke vergelijking van uitsluitend aangeleverde tekst; geen afgeleide historie of conclusie over juridische gelijkwaardigheid.",
            )
        )
        diff = list(
            difflib.unified_diff(
                versions[0]["text"].splitlines(),
                versions[1]["text"].splitlines(),
                fromfile=identities[0],
                tofile=identities[1],
                lineterm="",
            )
        )
        body.extend("> " + safe(line) for line in diff)
        if not diff:
            body.append(tr("Supplied text is identical.", "Aangeleverde tekst is identiek."))
        body.append(
            f"## {tr('Substantive changes / conflicting terms for review', 'Inhoudelijke wijzigingen / tegenstrijdige bepalingen ter beoordeling')}"
        )
        changes = objects(data.get("changes", []), "changes")
        for change in changes:
            body.append(f"- {text(required_text(change.get('text'), 'change text'))} ({ref(change.get('source'))})")
        if not changes:
            body.append(unknown("substantive review required", "inhoudelijke beoordeling nodig"))
    else:
        items = objects(data.get("items"), "items")
        seen = set()
        rows = []
        for item in items:
            identity = required_text(item.get("id"), "item id")
            if identity in seen:
                raise ValueError("Item identities must be unique")
            seen.add(identity)
            category = item.get("category")
            if category not in ("email", "letter", "revision", "artifact"):
                raise ValueError("Invalid item category")
            available = item.get("availability")
            statuses = {
                "supplied": ("supplied, not independently verified", "aangeleverd, niet onafhankelijk geverifieerd"),
                "generated": (
                    "generated temporary file, not delivered",
                    "tijdelijk bestand aangemaakt, niet afgeleverd",
                ),
                "missing": ("MISSING", "ONTBREEKT"),
                "failed": ("GENERATION FAILED", "AANMAKEN MISLUKT"),
                "expired": ("EXPIRED / unavailable", "VERLOPEN / niet beschikbaar"),
            }
            if available not in statuses:
                raise ValueError("Invalid item availability")
            filename = None
            if available == "generated":
                filename = Path(required_text(item.get("path"), "generated file path")).resolve()
                if (
                    not filename.is_relative_to(Path("/tmp").resolve())
                    or not filename.is_file()
                    or not filename.stat().st_size
                ):
                    raise ValueError("Generated file unavailable; record missing/failed/expired state explicitly")
            when = iso(item.get("date"), "item date")
            row = (
                f"- {text(identity)}: {text(required_text(item.get('title'), 'title'))}; "
                f"{tr('source', 'bron')}: {ref(item.get('source'))}; "
                f"{tr('date', 'datum')}: {text(when)}; {tr('version', 'versie')}: {text(item.get('version'))}; "
                f"{tr(*statuses[available])}"
            )
            if filename:
                row += f"; {tr('temporary file', 'tijdelijk bestand')}: {safe(str(filename))}"
            row += f"\n  {tr('Summary', 'Samenvatting')}: {text(item.get('summary'))}"
            if category in ("email", "letter"):
                row += f"; {tr('from/to', 'van/aan')}: {text(item.get('sender'))} / {text(item.get('recipient'))}"
            rows.append((when or "9999-99-99", identity, category, row))
        rows.sort(key=lambda row: (row[0], row[1]))
        body.append(
            f"## {tr('Attachment checklist' if kind == 'delivery' else 'Dossier index', 'Bijlagenlijst' if kind == 'delivery' else 'Dossierindex')}"
        )
        body.extend(row[3] for row in rows)
        if not rows:
            body.append(unknown("no documents supplied", "geen documenten aangeleverd"))
        if kind == "index":
            body.append(
                f"## {tr('Chronological correspondence (undated last)', 'Chronologische correspondentie (ongedateerd achteraan)')}"
            )
            correspondence = [row[3] for row in rows if row[2] in ("email", "letter")]
            body.extend(correspondence or [unknown("no correspondence supplied", "geen correspondentie aangeleverd")])
            body.append(
                tr("Working index only; no office system updated.", "Alleen werkindex; geen kantoorsysteem bijgewerkt.")
            )
        else:
            body.extend(
                [
                    f"## {tr('DRAFT - NOT SENT', 'CONCEPT - NIET VERZONDEN')}",
                    f"{tr('Recipient', 'Ontvanger')}: {text(data.get('recipient'))}",
                    f"{tr('Subject', 'Onderwerp')}: {text(data.get('subject'))}",
                    text(data.get("message")),
                    tr(
                        "Listed files are not attached or securely delivered. Review missing attachments and open questions before human delivery through the approved channel.",
                        "Genoemde bestanden zijn niet bijgevoegd of veilig afgeleverd. Beoordeel ontbrekende bijlagen en open vragen voordat een mens via het goedgekeurde kanaal verzendt.",
                    ),
                ]
            )

    issues = data.get("issues", [])
    if not isinstance(issues, list):
        raise ValueError("issues must be a list")
    body.append(f"## {tr('Unresolved decisions and questions', 'Onbesliste punten en vragen')}")
    body.extend(f"- {text(required_text(issue, 'issue'))}" for issue in issues)
    if not issues:
        body.append(
            tr(
                "No decisions supplied; notarial completeness review remains pending.",
                "Geen beslissingen aangeleverd; notariële volledigheidsbeoordeling blijft open.",
            )
        )
    # Escape the common envelope too; the shared writer deliberately accepts Markdown bodies.
    escaped_sources = [
        {
            **source,
            "reference": safe(source["reference"]),
            **({"verification": safe(source["verification"])} if source.get("verification") else {}),
        }
        for source in data["sources"]
    ]
    return {
        "dossier": safe(data["dossier"]),
        "locale": locale,
        "sources": escaped_sources,
        "artifact_type": tr(*TYPES[kind]),
        "body": "\n\n".join(body),
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
        record = build_record(data)
        result = {"artifact": write_artifact(record)} if args.export else {"record": record}
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from exc
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
