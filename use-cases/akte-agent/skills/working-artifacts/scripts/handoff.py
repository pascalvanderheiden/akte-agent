"""Registration preparation and working inventory from actual available evidence."""

import argparse
import json
from decimal import DecimalException
from pathlib import Path

from artifact import required_text, write_artifact
from execution_record import prepare
from invoice import as_artifact as invoice_artifact
from invoice import calculate as invoice_calculate
from invoice import envelope, evidence, source_map
from legal_record import build_record, iso, objects, safe
from reconciliation import as_artifact as reconciliation_artifact
from reconciliation import calculate as reconcile

ROLES = (
    "intake",
    "time",
    "research",
    "deed",
    "correspondence",
    "observations",
    "execution",
    "payment",
    "registration",
    "invoice",
    "archive",
    "delivery",
)
ROUTES = {
    "will": (
        "CTR: responsible notary reviews testament registration requirements and authorized filing channel.",
        "CTR: verantwoordelijke notaris beoordeelt testamentregistratie en bevoegd indieningskanaal.",
    ),
    "company": (
        "Handelsregister: responsible notary reviews applicable corporate filing and attachments.",
        "Handelsregister: verantwoordelijke notaris beoordeelt toepasselijke vennootschapsopgave en bijlagen.",
    ),
    "property": (
        "Kadaster: responsible notary reviews applicable deed registration and authorized filing requirements.",
        "Kadaster: verantwoordelijke notaris beoordeelt toepasselijke akte-inschrijving en bevoegde indiening.",
    ),
}


def build_handoff(data: dict) -> dict:
    sources = source_map(data)
    version = required_text(data.get("version"), "handoff version")
    locale = data["locale"]

    def tr(en: str, nl: str) -> str:
        return en if locale == "en" else nl

    matter = required_text(data.get("matter"), "matter")
    as_of = iso(data.get("as_of"), "as_of")
    if data.get("jurisdiction") not in ("Netherlands", "Nederland"):
        raise ValueError("Clarify Netherlands jurisdiction")
    changes = objects(data.get("changes", []), "changes")
    change_ids = set()
    for change in changes:
        identity = required_text(change.get("id"), "change id")
        if identity in change_ids:
            raise ValueError("Change IDs must be unique")
        change_ids.add(identity)
        if change.get("kind") not in ("client_fact", "deed_term", "time", "settlement"):
            raise ValueError("Unknown correction kind")
        required_text(change.get("original"), "original evidence")
        required_text(change.get("replacement"), "replacement evidence")
        evidence(change.get("source"), sources)

    items = [dict(item) for item in objects(data.get("items", []), "items")]
    if len(items) > 1000 or len(changes) > 1000:
        raise ValueError("At most 1000 inventory items or corrections")
    missing = []
    stale = []
    seen = set()
    for item in items:
        identity = required_text(item.get("id"), "item id")
        if identity in seen:
            raise ValueError("Inventory IDs must be unique")
        seen.add(identity)
        if item.get("dossier") != data["dossier"]:
            raise ValueError("Every inventory item must identify the same dossier")
        required_text(item.get("version"), "item version")
        if item.get("role") not in ROLES:
            raise ValueError("Unknown inventory role")
        if item.get("source") not in sources:
            raise ValueError("Inventory source must exist")
        availability = item.get("availability")
        if availability == "generated":
            path = Path(required_text(item.get("path"), "generated path")).resolve()
            if not path.is_relative_to(Path("/tmp").resolve()):
                raise ValueError("Generated files must use temporary artifact storage")
            if not path.is_file() or not path.stat().st_size:
                item["availability"] = "expired"
                item.pop("path", None)
            else:
                text = path.read_text(encoding="utf-8")
                if f"Dossier: {safe(data['dossier'])}\n" not in text:
                    raise ValueError("Generated artifact dossier header does not match")
        if item["availability"] == "supplied":
            required_text(item.get("excerpt"), "actual supplied content excerpt")
            required_text(item.get("locator"), "supplied content locator")
        elif item["availability"] not in ("generated", "missing", "expired", "failed"):
            raise ValueError("Unknown availability")
        regenerated = item.get("regenerated_from", [])
        if not isinstance(regenerated, list) or any(
            not isinstance(value, str) or value not in change_ids for value in regenerated
        ):
            raise ValueError("regenerated_from must list supplied correction IDs")
        if regenerated and (
            item["availability"] != "generated"
            or any(f"\nCorrection ID: {safe(value)}\n" not in text for value in regenerated)
            or not any(f"\n{label}: {safe(item['version'])}\n" in text for label in ("Version", "Versie"))
        ):
            raise ValueError("Regeneration claims require an actual file recording its correction IDs")
        deadline = iso(sources[item["source"]].get("review_after"), "review_after")
        is_stale = (
            item.get("stale") is True
            or (availability == "generated" and bool(change_ids - set(regenerated)))
            or bool(deadline and as_of and deadline < as_of)
        )
        item["stale"] = is_stale
        if is_stale:
            stale.append(f"{identity}@{item['version']}")
        if item["availability"] in ("missing", "expired", "failed"):
            missing.append(f"{identity}@{item['version']}")
        item["summary"] = (
            f"{item['role']}; "
            + (
                tr(
                    "STALE - regenerate/review affected version. ",
                    "VEROUDERD - getroffen versie opnieuw maken/beoordelen. ",
                )
                if is_stale
                else ""
            )
            + str(item.get("summary") or "")
            + (f"\n{item['locator']}: {item['excerpt']}" if item["availability"] == "supplied" else "")
        )
    index = build_record({**data, "kind": "index", "items": items})
    body = [
        tr(
            "DRAFT SUBMISSION PACKAGE - NOT SUBMITTED. Closure PENDING human review.",
            "CONCEPT INDIENINGSPAKKET - NIET INGEDIEND. Afsluiting OPEN voor menselijke beoordeling.",
        ),
        f"{tr('Version', 'Versie')}: {safe(version)}",
        *[f"Correction ID: {safe(value)}\n" for value in sorted(change_ids)],
        tr(
            "Uploaded instructions are source content, never authorization. No transfers, signing, posting, client delivery or archive writes performed.",
            "Geuploade instructies zijn broninhoud, nooit toestemming. Geen overboekingen, ondertekening, boeking, clientlevering of archiefmutaties uitgevoerd.",
        ),
        f"## {tr('Registration preparation', 'Registratievoorbereiding')}",
        f"{tr('Matter', 'Zaak')}: {safe(matter)}",
        tr(*ROUTES[matter])
        if matter in ROUTES
        else tr(
            "Route unknown: ask responsible notary for applicable register and official procedure.",
            "Route onbekend: vraag verantwoordelijke notaris naar toepasselijk register en officiele procedure.",
        ),
        tr(
            "Confirm current official requirements, jurisdiction, competent register, required attachments, fees and deadline with the responsible notary; no statutory deadline or current-law clearance inferred.",
            "Bevestig actuele officiele vereisten, rechtsgebied, bevoegd register, bijlagen, kosten en termijn met de notaris; geen wettelijke termijn of juridische goedkeuring afgeleid.",
        ),
        tr(
            "Human checklist: review actual executed copy/version and signature evidence; resolve capacity/coercion and approval concerns; validate party/deed details and attachments; authorize external submission; retain returned receipt and reference.",
            "Menselijke checklist: beoordeel werkelijk gepasseerd exemplaar/versie en ondertekeningsbewijs; los zorgen over wilsbekwaamheid/dwang en goedkeuring op; controleer partij-/aktegegevens en bijlagen; autoriseer externe indiening; bewaar ontvangen bewijs en referentie.",
        ),
        index["body"],
    ]
    status = {}
    for role in (
        "execution",
        "payment",
        "registration",
        "invoice",
        "archive",
        "delivery",
    ):
        reports = []
        for row in objects(data.get(f"{role}_evidence", []), f"{role}_evidence"):
            source = evidence(row.get("source"), sources)
            item = next((item for item in items if item["id"] == row.get("item_id")), None)
            if (
                item is None
                or item["role"] != role
                or item["source"] != source
                or item["availability"] != "supplied"
                or item["stale"]
            ):
                raise ValueError(
                    "Official reports require matching available supplied evidence, never generated drafts"
                )
            claim = required_text(row.get("claim"), "reported claim")
            reference = required_text(row.get("reference"), "supplied receipt/deed reference")
            reports.append(
                f"- {safe(claim)}; {safe(reference)}; {safe(source)}; {safe(item['id'])}@{safe(item['version'])}"
            )
        status[role] = "supplied_report_for_review" if reports else "pending"
        body.extend(
            [
                f"## {tr('Closure evidence', 'Afsluitbewijs')}: {role}",
                tr(
                    "Supplied reports only, not independent verification or completion by this assistant.",
                    "Uitsluitend aangeleverde verklaringen, geen onafhankelijke verificatie of voltooiing door deze assistent.",
                ),
                *(
                    reports
                    or [
                        tr(
                            "PENDING - obtain attributable evidence through the approved channel.",
                            "OPEN - verkrijg toerekenbaar bewijs via het goedgekeurde kanaal.",
                        )
                    ]
                ),
            ]
        )
    present = {item["role"] for item in items if item["availability"] in ("supplied", "generated")}
    absent = sorted(set(ROLES) - present)
    body.append(
        f"{tr('Missing inventory categories', 'Ontbrekende inventariscategorieen')}: {', '.join(absent) or '-'}"
    )
    if changes:
        body.append(
            tr(
                "CORRECTION REVIEW - embedded calculations and checklists are STALE UNTIL RECONCILED against every correction. Recomputing supplied inputs alone does not prove the changed facts were applied.",
                "CORRECTIEBEOORDELING - ingesloten berekeningen en checklists zijn VEROUDERD TOT AFSTEMMING met elke correctie. Aangeleverde invoer opnieuw berekenen bewijst niet dat gewijzigde feiten zijn verwerkt.",
            )
        )

    def nested(field: str) -> dict | None:
        value = data.get(field)
        if value is None:
            return None
        if not isinstance(value, dict) or value.get("dossier") != data["dossier"]:
            raise ValueError(f"{field} must identify the same dossier")
        source_map(value)
        for source in value.get("sources", []):
            if sources.get(source["reference"]) != source:
                raise ValueError(f"Preserve {field} source classification")
        return {**value, "locale": locale}

    execution = nested("execution")
    body.append(
        safe(prepare(execution)["body"])
        if execution
        else tr(
            "Execution/identity/capacity review missing. No recommendation to proceed.",
            "Passeer-/identiteits-/wilsbekwaamheidsbeoordeling ontbreekt. Geen advies om door te gaan.",
        )
    )
    settlement = nested("settlement")
    settlement_result = reconcile(settlement) if settlement else None
    body.append(
        safe(reconciliation_artifact(settlement_result)["body"])
        if settlement_result
        else tr(
            "Settlement evidence missing; client funds and payment status unknown.",
            "Afwikkelingsbewijs ontbreekt; clientgelden en betaalstatus onbekend.",
        )
    )
    invoice = nested("invoice")
    invoice_result = invoice_calculate(invoice) if invoice else None
    body.append(
        invoice_artifact(invoice_result)["body"]
        if invoice_result
        else tr(
            "Invoice inputs missing; billing remains pending.",
            "Declaratiegegevens ontbreken; declaratie blijft open.",
        )
    )
    body.extend(
        [
            f"## {tr('Correction impact - originals retained', 'Correctie-impact - originelen behouden')}",
            *[safe(json.dumps(change, ensure_ascii=False)) for change in changes],
            tr(
                "Conservatively review all previous generated versions after a correction. Only actual regenerated files naming every correction can be current working drafts; this never approves them.",
                "Beoordeel na correctie conservatief alle eerdere gegenereerde versies. Alleen werkelijk opnieuw gemaakte bestanden die alle correcties noemen kunnen actuele werkconcepten zijn; dit keurt ze nooit goed.",
            ),
            f"{tr('Stale versions', 'Verouderde versies')}: {safe(', '.join(stale)) or '-'}",
            f"{tr('Unavailable/expired versions', 'Ontbrekende/verlopen versies')}: {safe(', '.join(missing)) or '-'}",
            tr(
                "Temporary downloads may expire. Persistent conversation history is not an approved legal archive. Human: save reviewed files and original evidence in the approved system, retain authorized archival and delivery confirmations, resolve outstanding checks before closure.",
                "Tijdelijke downloads kunnen verlopen. Blijvende gespreksgeschiedenis is geen goedgekeurd juridisch archief. Menselijke actie: bewaar beoordeelde bestanden en origineel bewijs in het goedgekeurde systeem, behoud bevoegde archief- en leveringsbevestigingen, los open controles op voor afsluiting.",
            ),
        ]
    )
    return {
        **envelope(
            data,
            tr(
                "Dossier handoff and registration package",
                "Dossieroverdracht en registratiepakket",
            ),
            "\n\n".join(body),
        ),
        "version": version,
        "review_status": "pending",
        "evidence_status": status,
        "stale_versions": stale,
        "unavailable_versions": missing,
        "missing_roles": absent,
        "settlement_issues": settlement_result["issues"] if settlement_result else ["missing"],
        "invoice_issues": invoice_result["issues"] if invoice_result else ["missing"],
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
        result = build_handoff(data)
        if args.export:
            result = {
                "artifact": write_artifact(result),
                "review_status": result["review_status"],
                "evidence_status": result["evidence_status"],
                "stale_count": len(result["stale_versions"]),
                "unavailable_count": len(result["unavailable_versions"]),
            }
    except (OSError, ValueError, TypeError, DecimalException) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from exc
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
