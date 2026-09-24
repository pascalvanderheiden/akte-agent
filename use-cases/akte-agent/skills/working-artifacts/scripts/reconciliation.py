"""Review supplied settlement figures, without banking or accounting actions."""

import argparse
import json
from decimal import ROUND_HALF_UP, Decimal, DecimalException
from pathlib import Path

from artifact import inline, required_text, validate_metadata, write_artifact
from exact import currency, exact_product, exact_sum, parse_decimal

CATEGORIES = ("client_funds", "tax", "office_fee", "other_charge")
TEXT = {
    "en": {
        "title": "Reconciliation worksheet",
        "pending": "UNRESOLVED - responsible notary review required",
        "balanced": "Supplied figures balance - draft only, not payment or execution clearance",
        "rule": "EUR; ROUND_HALF_UP per included line to cents (ties away from zero), then exact sum of rounded lines. Original precision retained.",
        "boundary": "Client funds on the third-party account are not office revenue. Office fees below are supplied billed charges, not posted revenue. No transfer, signature, deed execution or official confirmation performed.",
        "headers": "ID | Category | Description | Source amount | Rounded EUR | Tax treatment | Source | Payment evidence | Decision / confirmation | Review",
        "client_funds": "Client funds (third-party account)",
        "tax": "Taxes",
        "office_fee": "Office fees",
        "other_charge": "Other charges",
        "missing": "Missing / unknown",
        "coverage": "Completeness evidence",
        "totals": "Supplied totals (not bank-verified)",
        "charges": "Total supplied charges",
        "balance": "Difference: client funds minus charges",
        "amount": "Missing or ambiguous amount; clarify",
        "treatment": "Missing explicit tax treatment",
        "payment": "Missing supplied payment confirmation",
        "duplicate": "Possible duplicate: confirm every affected row",
        "correction": "Correction: retain and confirm original and replacement",
        "zero": "Zero amount: confirm",
        "incomplete": "Category completeness not confirmed",
        "imbalance": "Imbalanced settlement: unresolved",
        "count": "Include",
        "exclude": "Exclude",
        "unconfirmed": "Unconfirmed",
        "blocked": "No totals: unresolved amounts, treatment, coverage or row decisions",
    },
    "nl": {
        "title": "Reconciliatiewerkblad",
        "pending": "ONOPGELOST - beoordeling door verantwoordelijke notaris vereist",
        "balanced": "Aangeleverde bedragen sluiten aan - alleen concept, geen betaal- of passeergoedkeuring",
        "rule": "EUR; ROUND_HALF_UP per meegetelde regel op centen (halve cent van nul af), daarna exacte som van afgeronde regels. Oorspronkelijke precisie behouden.",
        "boundary": "Clientgelden op de derdengeldenrekening zijn geen kantooromzet. Kantoorkosten hieronder zijn aangeleverde gedeclareerde kosten, geen geboekte omzet. Geen overboeking, ondertekening, passeren of officiele bevestiging uitgevoerd.",
        "headers": "ID | Categorie | Omschrijving | Bronbedrag | Afgerond EUR | Fiscale behandeling | Bron | Betaalbewijs | Besluit / bevestiging | Beoordeling",
        "client_funds": "Clientgelden (derdengeldenrekening)",
        "tax": "Belastingen",
        "office_fee": "Kantoorkosten",
        "other_charge": "Overige kosten",
        "missing": "Ontbreekt / onbekend",
        "coverage": "Bewijs van volledigheid",
        "totals": "Aangeleverde totalen (niet door bank geverifieerd)",
        "charges": "Totaal aangeleverde kosten",
        "balance": "Verschil: clientgelden minus kosten",
        "amount": "Ontbrekend of ambigu bedrag; verduidelijk",
        "treatment": "Expliciete fiscale behandeling ontbreekt",
        "payment": "Aangeleverde betaalbevestiging ontbreekt",
        "duplicate": "Mogelijk dubbel: bevestig elke betrokken regel",
        "correction": "Correctie: behoud en bevestig origineel en vervanging",
        "zero": "Nulbedrag: bevestiging vereist",
        "incomplete": "Volledigheid categorie niet bevestigd",
        "imbalance": "Afwikkeling sluit niet aan: onopgelost",
        "count": "Meetellen",
        "exclude": "Uitsluiten",
        "unconfirmed": "Niet bevestigd",
        "blocked": "Geen totalen: onopgeloste bedragen, fiscale behandeling, volledigheid of regelbesluiten",
    },
}


def calculate(data: dict) -> dict:
    validate_metadata(data)
    if data.get("currency") != "EUR" or data.get("rounding") != ROUND_HALF_UP:
        raise ValueError("Supply currency EUR and explicit rounding ROUND_HALF_UP")
    refs = {source["reference"]: source for source in data["sources"]}
    if len(refs) != len(data["sources"]):
        raise ValueError("Source references must be unique")
    entries = data.get("entries")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
        raise ValueError("Supply between 1 and 1000 separate entries")
    coverage = data.get("coverage", {})
    if not isinstance(coverage, dict) or set(coverage) - set(CATEGORIES):
        raise ValueError("coverage must map known categories to supplied source references")
    for source in coverage.values():
        if not isinstance(source, str) or source not in refs or refs[source]["kind"] == "assumption":
            raise ValueError("Coverage requires a supplied source, not an assumption")
    rows: list[dict] = []
    ids: dict[str, int] = {}
    groups: dict[tuple, list[int]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Each entry must be an object")
        row = dict(entry)
        for field in ("flags", "rounded", "amount_display", "amount_error"):
            row.pop(field, None)
        entry_id = required_text(row.get("id"), "entry id")
        if entry_id in ids:
            raise ValueError("Duplicate entry id; give each version its own id")
        ids[entry_id] = len(rows)
        category = row.get("category")
        if category not in CATEGORIES:
            raise ValueError("Unknown category")
        label = required_text(row.get("description"), "description")
        source = required_text(row.get("source"), "source")
        if source not in refs:
            raise ValueError("Entry requires a reference from sources")
        flags = []
        amount = None
        try:
            raw_amount = row.get("amount")
            if not isinstance(raw_amount, str):
                raise ValueError("Supply an explicit decimal amount string, not a float or missing value")
            amount = parse_decimal(raw_amount, row.get("decimal_separator"))
            if amount.is_signed():
                raise ValueError("Negative amount: clarify reversal rather than netting it")
        except ValueError as exc:
            flags.append("amount")
            row["amount_error"] = str(exc)
            amount = None
        if amount is not None:
            row["rounded"] = currency(amount, "en", rounding=ROUND_HALF_UP)
            row["amount_display"] = currency(amount, data["locale"], rounding=ROUND_HALF_UP)
            if amount == 0:
                flags.append("zero")
        treatment = row.get("tax_treatment")
        if treatment is not None and not isinstance(treatment, str):
            raise ValueError("tax_treatment must be supplied text")
        if category != "client_funds" and (not isinstance(treatment, str) or not treatment.strip()):
            flags.append("treatment")
        payment = row.get("payment_source")
        if payment is None:
            flags.append("payment")
        elif not isinstance(payment, str) or payment not in refs or refs[payment]["kind"] == "assumption":
            raise ValueError("Payment confirmation requires supplied evidence from sources")
        if refs[source]["kind"] == "assumption":
            row["review_reason"] = "Assumed amount requires supplied evidence"
        if row.get("review_reason"):
            required_text(row["review_reason"], "review_reason")
            flags.append("review_reason")
        if row.get("decision") not in (None, "count", "exclude"):
            raise ValueError("decision must be count or exclude")
        if row.get("decision"):
            confirmation = required_text(row.get("confirmation"), "confirmation source")
            if confirmation not in refs or refs[confirmation]["kind"] == "assumption":
                raise ValueError("Decision requires a supplied confirmation source")
        groups.setdefault((category, label.strip().casefold()), []).append(len(rows))
        row["flags"] = flags
        rows.append(row)
    for group in groups.values():
        if len(group) > 1:
            for index in group:
                rows[index]["flags"].append("duplicate")
    for index, row in enumerate(rows):
        if "correction_of" in row:
            original = row["correction_of"]
            if not isinstance(original, str) or original not in ids or ids[original] >= index:
                raise ValueError("correction_of must identify an earlier entry")
            row["flags"].append("correction")
            rows[ids[original]]["flags"].append("correction")
    issues = [f"incomplete:{category}" for category in CATEGORIES if category not in coverage]
    unresolved_rows = any(
        "amount" in row["flags"]
        or "treatment" in row["flags"]
        or (set(row["flags"]) - {"payment"} and not row.get("decision"))
        or (refs[row["source"]]["kind"] == "assumption" and row.get("decision") != "exclude")
        for row in rows
    )
    result = {
        "dossier": data["dossier"],
        "locale": data["locale"],
        "sources": data["sources"],
        "currency": "EUR",
        "rounding": ROUND_HALF_UP,
        "entries": rows,
        "coverage": coverage,
        "issues": issues,
        "review_status": "pending",
    }
    if issues or unresolved_rows:
        issues.append("blocked")
    else:
        totals = {
            category: exact_sum(
                [
                    Decimal(row["rounded"])
                    for row in rows
                    if row["category"] == category and row.get("decision") != "exclude"
                ]
            )
            for category in CATEGORIES
        }
        charges = exact_sum([totals[category] for category in CATEGORIES if category != "client_funds"])
        difference = exact_sum([totals["client_funds"], exact_product(charges, Decimal("-1"))])
        totals.update(charges=charges, balance=difference)
        result["totals"] = {key: currency(value, "en", rounding=ROUND_HALF_UP) for key, value in totals.items()}
        result["display"] = {
            key: currency(value, data["locale"], rounding=ROUND_HALF_UP) for key, value in totals.items()
        }
        if difference != 0:
            issues.append("imbalance")
    if any("payment" in row["flags"] for row in rows if row.get("decision") != "exclude"):
        issues.append("payment")
    if not issues:
        result["review_status"] = "balanced"
    return result


def as_artifact(result: dict) -> dict:
    labels = TEXT[result["locale"]]
    lines = [
        labels[result["review_status"]],
        "",
        labels["boundary"],
        "",
        labels["rule"],
        "",
        f"| {labels['headers']} |",
        "| " + " | ".join(["---"] * 10) + " |",
    ]
    for row in result["entries"]:
        review = "; ".join(
            row["review_reason"] if flag == "review_reason" else labels[flag] for flag in dict.fromkeys(row["flags"])
        )
        if row.get("amount_error"):
            review += f": {row['amount_error']}"
        if row.get("correction_of"):
            review += f" ({row['correction_of']})"
        decision = labels[row["decision"]] if row.get("decision") else labels["unconfirmed"]
        if row.get("confirmation"):
            decision += f": {row['confirmation']}"
        cells = [
            row["id"],
            labels[row["category"]],
            row["description"],
            str(row.get("amount")) if row.get("amount") is not None else labels["missing"],
            row.get("amount_display", labels["missing"]),
            row.get("tax_treatment") or labels["missing"],
            row["source"],
            row.get("payment_source") or labels["missing"],
            decision,
            review,
        ]
        lines.append("| " + " | ".join(inline(cell) for cell in cells) + " |")
    lines.extend(["", f"## {labels['coverage']}"])
    for category in CATEGORIES:
        lines.append(f"- {labels[category]}: {inline(result['coverage'].get(category, labels['missing']))}")
    if "display" in result:
        lines.extend(["", f"## {labels['totals']}"])
        for key, amount in result["display"].items():
            lines.append(f"- {labels[key]}: EUR {amount}")
    for issue in result["issues"]:
        code, _, category = issue.partition(":")
        lines.append(f"- {labels[code]}" + (f": {labels[category]}" if category else ""))
    return {**result, "artifact_type": labels["title"], "body": "\n".join(lines)}


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
            artifact = write_artifact(as_artifact(result))
            result = {
                "artifact": artifact,
                "review_status": result["review_status"],
                "issues": result["issues"],
                **{key: result[key] for key in ("totals", "display") if key in result},
            }
    except (OSError, ValueError, TypeError, DecimalException) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from exc
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
