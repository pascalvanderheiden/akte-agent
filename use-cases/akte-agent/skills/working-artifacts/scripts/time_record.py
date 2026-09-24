"""Exact, confirmation-gated time drafts using the existing code execution seam."""

import argparse
import json
from decimal import Decimal, DecimalException
from pathlib import Path

from artifact import inline, required_text, validate_metadata, write_artifact
from exact import exact_product, exact_sum, format_decimal, parse_decimal

TEXT = {
    "en": {
        "title": "Time record",
        "unknown": "Unknown",
        "pending": "PENDING REVIEW - no total until all flagged entries are confirmed",
        "ready": "Arithmetic resolved - DRAFT, not approval to bill",
        "headers": "Entry | Activity | Date | Timekeeper | Source hours | Hours | Minutes | Source | Review | Decision / confirmation",
        "total": "Total",
        "units": ("hours", "minutes"),
        "zero": "Zero duration: confirm",
        "duplicate": "Possible duplicate: confirm both entries",
        "correction": "Correction: confirm original and replacement",
        "count": "Count",
        "exclude": "Exclude",
        "unconfirmed": "Unconfirmed",
    },
    "nl": {
        "title": "Urenregistratie",
        "unknown": "Onbekend",
        "pending": "BEOORDELING NODIG - geen totaal voordat alle gemarkeerde regels zijn bevestigd",
        "ready": "Berekening afgerond - CONCEPT, geen goedkeuring om te declareren",
        "headers": "Regel | Activiteit | Datum | Tijdschrijver | Bronuren | Uren | Minuten | Bron | Beoordeling | Besluit / bevestiging",
        "total": "Totaal",
        "units": ("uur", "minuten"),
        "zero": "Nulduur: bevestiging vereist",
        "duplicate": "Mogelijk dubbel: bevestig beide regels",
        "correction": "Correctie: bevestig origineel en vervanging",
        "count": "Meetellen",
        "exclude": "Uitsluiten",
        "unconfirmed": "Niet bevestigd",
    },
}


def calculate(data: dict) -> dict:
    validate_metadata(data)
    locale = data["locale"]
    entries = data.get("entries")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
        raise ValueError("Supply between 1 and 1000 separate entries")
    rows = []
    values = []
    ids: dict[str, int] = {}
    groups: dict[tuple, list[int]] = {}
    source_refs = {source["reference"] for source in data["sources"]}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Each entry must be an object")
        row = dict(entry)
        entry_id = required_text(row.get("id"), "entry id")
        if entry_id in ids:
            raise ValueError(f"Duplicate entry id: {entry_id}; give each version its own id")
        ids[entry_id] = len(rows)
        activity = required_text(row.get("activity"), "activity")
        if row.get("source") not in source_refs:
            raise ValueError(f"Entry {entry_id} requires a reference from sources")
        for field in ("date", "timekeeper"):
            if row.get(field) is not None:
                required_text(row[field], field)
        if row.get("date"):
            from datetime import date

            if date.fromisoformat(row["date"]).isoformat() != row["date"]:
                raise ValueError("Supply dates as YYYY-MM-DD")
        hours = parse_decimal(row.get("hours"), row.get("decimal_separator"))
        if hours < 0 or hours.is_signed():
            raise ValueError(f"Negative duration in {entry_id}; clarify rather than silently changing it")
        decision = row.get("decision")
        if decision not in (None, "count", "exclude"):
            raise ValueError("decision must be count or exclude")
        if decision:
            required_text(row.get("confirmation"), "user confirmation reference")
        flags = ["zero"] if hours == 0 else []
        if row.get("review_reason"):
            required_text(row["review_reason"], "review_reason")
            flags.append("review_reason")
        row.update(
            flags=flags,
            hours_display=format_decimal(hours, locale),
            minutes_display=format_decimal(exact_product(hours, Decimal("60")), locale, trim=True),
        )
        key = (activity.strip().casefold(), row.get("date"), (row.get("timekeeper") or "").strip().casefold())
        groups.setdefault(key, []).append(len(rows))
        rows.append(row)
        values.append(hours)
    for group in groups.values():
        if len(group) > 1:
            for index in group:
                rows[index]["flags"].append("duplicate")
    for index, row in enumerate(rows):
        if "correction_of" in row:
            original = row["correction_of"]
            if original not in ids or ids[original] >= index:
                raise ValueError("correction_of must identify an earlier entry")
            row["flags"].append("correction")
            rows[ids[original]]["flags"].append("correction")
    pending = any(row["flags"] and not row.get("decision") for row in rows)
    result = {
        "dossier": data["dossier"],
        "locale": locale,
        "sources": data["sources"],
        "entries": rows,
        "review_status": "pending" if pending else "ready",
    }
    if not pending:
        total = exact_sum([value for row, value in zip(rows, values, strict=True) if row.get("decision") != "exclude"])
        minutes = exact_product(total, Decimal("60"))
        result.update(
            total_hours=format_decimal(total, "en", trim=True),
            total_minutes=format_decimal(minutes, "en", trim=True),
            hours_display=format_decimal(total, locale, trim=True),
            minutes_display=format_decimal(minutes, locale, trim=True),
        )
    return result


def as_artifact(result: dict) -> dict:
    labels = TEXT[result["locale"]]
    lines = [
        labels[result["review_status"]],
        "",
        f"| {labels['headers']} |",
        "| " + " | ".join(["---"] * 10) + " |",
    ]
    for row in result["entries"]:
        review = "; ".join(
            row["review_reason"] if flag == "review_reason" else labels[flag] for flag in dict.fromkeys(row["flags"])
        )
        if row.get("correction_of"):
            review += f" ({row['correction_of']})"
        decision = labels[row["decision"]] if row.get("decision") else labels["unconfirmed"] if row["flags"] else "-"
        if row.get("confirmation"):
            decision += f": {row['confirmation']}"
        cells = [
            row["id"],
            row["activity"],
            row.get("date") or labels["unknown"],
            row.get("timekeeper") or labels["unknown"],
            row["hours"],
            row["hours_display"],
            row["minutes_display"],
            row["source"],
            review,
            decision,
        ]
        lines.append("| " + " | ".join(inline(cell) for cell in cells) + " |")
    if result["review_status"] == "ready":
        hours_unit, minutes_unit = labels["units"]
        lines.extend(
            [
                "",
                f"{labels['total']}: {result['hours_display']} {hours_unit} / {result['minutes_display']} {minutes_unit}",
            ]
        )
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
            result["artifact"] = write_artifact(as_artifact(result))
    except (OSError, ValueError, TypeError, DecimalException) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from exc
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
