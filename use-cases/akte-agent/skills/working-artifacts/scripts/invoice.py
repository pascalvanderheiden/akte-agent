"""Exact, evidence-gated invoice drafts. No posting, sending or revenue recognition."""

import argparse
import json
from decimal import ROUND_HALF_UP, Decimal, DecimalException
from pathlib import Path

from artifact import required_text, validate_metadata, write_artifact
from exact import currency, exact_product, exact_sum, format_decimal, parse_decimal
from legal_record import objects, safe
from time_record import as_artifact as time_artifact
from time_record import calculate as time_calculate


def source_map(data: dict) -> dict:
    validate_metadata(data)
    sources = {row["reference"]: row for row in data["sources"]}
    if len(sources) != len(data["sources"]):
        raise ValueError("Source references must be unique")
    return sources


def evidence(value: object, sources: dict) -> str:
    reference = required_text(value, "supplied evidence reference")
    if reference not in sources or sources[reference]["kind"] == "assumption":
        raise ValueError("Evidence must reference a supplied source, not an assumption")
    return reference


def envelope(data: dict, title: str, body: str) -> dict:
    return {
        "dossier": safe(data["dossier"]),
        "locale": data["locale"],
        "sources": [
            {
                **row,
                "reference": safe(row["reference"]),
                **({"verification": safe(row["verification"])} if row.get("verification") else {}),
            }
            for row in data["sources"]
        ],
        "artifact_type": title,
        "body": body,
    }


def calculate(data: dict) -> dict:
    sources = source_map(data)
    version = required_text(data.get("version"), "invoice version")
    if len(version) > 200:
        raise ValueError("Invoice version must be at most 200 characters")
    correction_ids = data.get("correction_ids", [])
    if not isinstance(correction_ids, list) or any(
        not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value for value in correction_ids
    ):
        raise ValueError("correction_ids must be nonempty single-line strings")
    if data.get("currency") != "EUR" or data.get("rounding") != ROUND_HALF_UP:
        raise ValueError("Supply EUR and explicit ROUND_HALF_UP")
    time = data.get("time")
    if not isinstance(time, dict) or time.get("dossier") != data["dossier"]:
        raise ValueError("Time input must identify the same dossier")
    source_map(time)
    for row in time.get("sources", []):
        if sources.get(row["reference"]) != row:
            raise ValueError("Preserve time source metadata in invoice sources")
    reviewed = time_calculate({**time, "locale": data["locale"]})
    issues = []
    if reviewed["review_status"] != "ready":
        issues.append("time")
    if data.get("review_source") is None:
        issues.append("review")
    else:
        evidence(data["review_source"], sources)
    for row in reviewed["entries"]:
        evidence(row["source"], sources)
        if row.get("decision"):
            evidence(row.get("confirmation"), sources)
    rates = data.get("rates", {})
    if not isinstance(rates, dict) or rates.keys() - {row["id"] for row in reviewed["entries"]}:
        raise ValueError("rates must map time entry IDs to supplied rates")
    lines = []

    def amount(row: dict, field: str, issue: str) -> Decimal | None:
        try:
            value = parse_decimal(row.get(field), row.get("decimal_separator"))
            if value.is_signed():
                raise ValueError("Negative values require clarification, not silent netting")
            evidence(row.get("source"), sources)
            return value
        except ValueError as exc:
            issues.append(issue)
            row["error"] = str(exc)
            return None

    for row in reviewed["entries"]:
        rate = rates.get(row["id"])
        if rate is not None and not isinstance(rate, dict):
            raise ValueError("Each rate must be an object")
        line = {"id": row["id"], "activity": row["activity"], "rate": dict(rate or {}), "decision": row.get("decision")}
        value = amount(line["rate"], "amount", "rates") if row.get("decision") != "exclude" else None
        if value is not None:
            raw = exact_product(parse_decimal(row["hours"], row.get("decimal_separator")), value)
            line.update(raw=format_decimal(raw, "en"), rounded=currency(raw, "en", rounding=ROUND_HALF_UP))
        lines.append(line)

    costs = [dict(row) for row in objects(data.get("costs", []), "costs")]
    if len(costs) > 1000:
        raise ValueError("At most 1000 costs")
    if data.get("costs_source") is None:
        issues.append("costs_scope")
    else:
        evidence(data["costs_source"], sources)
    ids = {}
    groups: dict[tuple, list[dict]] = {}
    for row in costs:
        for field in ("rounded", "error", "flags"):
            row.pop(field, None)
        identity = required_text(row.get("id"), "cost id")
        if identity in ids:
            raise ValueError("Cost IDs must be unique")
        category = row.get("category")
        if category not in ("register_cost", "other_charge"):
            raise ValueError("Costs must be register_cost or other_charge; client funds are not invoice revenue")
        description = required_text(row.get("description"), "cost description")
        row["flags"] = []
        if row.get("correction_of") is not None:
            original = row["correction_of"]
            if not isinstance(original, str) or original not in ids:
                raise ValueError("Cost correction_of must identify an earlier cost")
            row["flags"].append("correction")
            ids[original]["flags"].append("correction")
        ids[identity] = row
        groups.setdefault((category, description.strip().casefold()), []).append(row)
        value = amount(row, "amount", "costs")
        if value is not None:
            row["rounded"] = currency(value, "en", rounding=ROUND_HALF_UP)
            if value == 0:
                row["flags"].append("zero")
        if row.get("review_reason"):
            required_text(row["review_reason"], "review_reason")
            row["flags"].append("review")
        if row.get("decision") not in (None, "count", "exclude"):
            raise ValueError("Cost decision must be count or exclude")
        if row.get("decision"):
            evidence(row.get("confirmation"), sources)
    for group in groups.values():
        if len(group) > 1:
            for row in group:
                row["flags"].append("duplicate")
    if any(row["flags"] and not row.get("decision") for row in costs):
        issues.append("cost_decisions")

    totals = {}
    if not set(issues) & {"time", "review", "rates"}:
        totals["fees"] = exact_sum([Decimal(row["rounded"]) for row in lines if row.get("decision") != "exclude"])
    if not set(issues) & {"costs_scope", "costs", "cost_decisions"}:
        totals["costs"] = exact_sum([Decimal(row["rounded"]) for row in costs if row.get("decision") != "exclude"])
    if "fees" in totals and "costs" in totals:
        totals["pretax"] = exact_sum([totals["fees"], totals["costs"]])

    tax = data.get("tax")
    if tax is None:
        issues.append("tax")
    elif not isinstance(tax, dict):
        raise ValueError("tax must be an object")
    else:
        tax = dict(tax)
        required_text(tax.get("treatment"), "explicit tax treatment")
        if ("rate_percent" in tax) == ("amount" in tax):
            raise ValueError("Supply exactly one tax amount or uniform rate_percent")
        field = "rate_percent" if "rate_percent" in tax else "amount"
        value = amount(tax, field, "tax")
        if value is not None and "pretax" in totals:
            if field == "rate_percent":
                # Explicit uniform tax is applied to each rounded taxable line.
                factor = exact_product(value, Decimal("0.01"))
                tax_lines = [
                    Decimal(currency(exact_product(Decimal(row["rounded"]), factor), "en", rounding=ROUND_HALF_UP))
                    for row in [*lines, *costs]
                    if row.get("decision") != "exclude"
                ]
                totals["tax"] = exact_sum(tax_lines)
            else:
                totals["tax"] = Decimal(currency(value, "en", rounding=ROUND_HALF_UP))
            totals["total"] = exact_sum([totals["pretax"], totals["tax"]])
    return {
        "dossier": data["dossier"],
        "locale": data["locale"],
        "sources": data["sources"],
        "version": version,
        "correction_ids": correction_ids,
        "time": reviewed,
        "lines": lines,
        "costs": costs,
        "tax": tax,
        "review_source": data.get("review_source"),
        "costs_source": data.get("costs_source"),
        "issues": sorted(set(issues)),
        "review_status": "pending" if issues else "arithmetic_ready",
        "totals": {key: currency(value, "en", rounding=ROUND_HALF_UP) for key, value in totals.items()},
    }


def as_artifact(result: dict) -> dict:
    nl = result["locale"] == "nl"

    def tr(en: str, dutch: str) -> str:
        return dutch if nl else en

    labels = {
        "fees": tr("Office fees", "Kantoorhonorarium"),
        "costs": tr("Costs", "Kosten"),
        "pretax": tr("Pre-tax", "Voor belasting"),
        "tax": tr("Explicit tax", "Expliciete belasting"),
        "total": tr("Total", "Totaal"),
    }
    issue_labels = {
        "time": tr("Resolve duplicate/corrected time decisions", "Los dubbele/gecorrigeerde tijdregels op"),
        "review": tr("Supply review of billable activities", "Lever beoordeling declarabele activiteiten aan"),
        "rates": tr(
            "Clarify missing/ambiguous rates and rate evidence",
            "Verduidelijk ontbrekende/ambigue tarieven en tariefbewijs",
        ),
        "costs_scope": tr(
            "Confirm expense completeness, including explicit no further costs",
            "Bevestig volledigheid kosten, ook expliciet geen verdere kosten",
        ),
        "costs": tr("Clarify costs and missing expense evidence", "Verduidelijk kosten en ontbrekend kostenbewijs"),
        "cost_decisions": tr("Confirm duplicate/corrected/zero costs", "Bevestig dubbele/gecorrigeerde/nulkosten"),
        "tax": tr(
            "Supply explicit tax treatment and amount/rate; final total unavailable",
            "Lever expliciete fiscale behandeling en bedrag/tarief; eindtotaal ontbreekt",
        ),
    }
    body = [
        tr(
            "NOT POSTED / NOT SENT. Client funds are not office fees or recognized revenue.",
            "NIET GEBOEKT / NIET VERZONDEN. Clientgelden zijn geen honorarium of geboekte omzet.",
        ),
        f"{tr('Version', 'Versie')}: {safe(result['version'])}",
        *[f"Correction ID: {safe(value)}\n" for value in result["correction_ids"]],
        tr(
            "ROUND_HALF_UP: exact hours x supplied rate; round each fee/cost to cents, then sum. Explicit uniform tax rates apply per rounded line, rounded again to cents. No tax/rate/expense inferred.",
            "ROUND_HALF_UP: exacte uren x aangeleverd tarief; rond elke honorarium-/kostenregel op centen af, dan optellen. Expliciete uniforme belasting per afgeronde regel, opnieuw afgerond op centen. Geen belasting/tarief/kosten aangenomen.",
        ),
        tr(
            "Half cents round away from zero; original precision retained.",
            "Halve centen ronden van nul af; oorspronkelijke precisie behouden.",
        ),
        f"{tr('Activity review', 'Activiteitenbeoordeling')}: {safe(result['review_source'] or tr('MISSING', 'ONTBREEKT'))}",
        f"{tr('Expense scope', 'Kostenscope')}: {safe(result['costs_source'] or tr('MISSING', 'ONTBREEKT'))}",
        safe(time_artifact(result["time"])["body"]),
        f"## {tr('Itemized invoice evidence', 'Gespecificeerd declaratiebewijs')}",
    ]
    for row in result["lines"]:
        body.extend(safe(json.dumps(source, ensure_ascii=False)) for source in result["sources"])
        body.append(
            f"- {safe(row['id'])} / {safe(row['activity'])}: "
            f"{tr('raw rate and source', 'brontarief en bron')} {safe(json.dumps(row['rate'], ensure_ascii=False))}; "
            f"{tr('unrounded fee', 'onafgerond honorarium')}: {row.get('raw', '?')}; "
            f"EUR {format_decimal(Decimal(row['rounded']), result['locale']) if 'rounded' in row else '?'}; "
            f"{tr('decision', 'besluit')}: {row.get('decision') or '-'}"
        )
    for row in result["costs"]:
        body.append(f"- {safe(json.dumps(row, ensure_ascii=False))}")
    body.append(
        f"{tr('Supplied tax', 'Aangeleverde belasting')}: {safe(json.dumps(result['tax'], ensure_ascii=False))}"
    )
    for key, value in result["totals"].items():
        body.append(f"- {labels[key]}: EUR {format_decimal(Decimal(value), result['locale'])}")
    body.extend(f"- {issue_labels[issue]}" for issue in result["issues"])
    body.append(tr("Human billing approval remains pending.", "Menselijke declaratiegoedkeuring blijft open."))
    return envelope(result, tr("Invoice draft", "Conceptdeclaratie"), "\n\n".join(body))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--export", action="store_true")
    args = parser.parse_args()
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Input must be a JSON object")
        result = calculate(data)
        if args.export:
            result = {
                "artifact": write_artifact(as_artifact(result)),
                "version": result["version"],
                "review_status": result["review_status"],
                "issues": result["issues"],
                "totals": result["totals"],
            }
    except (OSError, ValueError, TypeError, DecimalException) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from exc
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
