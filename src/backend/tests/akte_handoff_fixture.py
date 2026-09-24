"""Complete synthetic journey using real packaged CLI outputs and original evidence."""

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from tests.akte_combined_fixture import create_journey


def create_complete_journey(persona: Path, locale: str, *, correction=False, output_locale=None) -> list[dict]:
    scripts = persona / "skills/working-artifacts/scripts"
    invoice = json.loads((persona / "skills/billing-handoff/references/synthetic-billing.json").read_text())[locale]
    documents = []

    def export(name: str, script: str, data: dict) -> dict:
        with tempfile.TemporaryDirectory(prefix="akte-handoff-input-") as directory:
            input_path = Path(directory) / "input.json"
            input_path.write_text(json.dumps(data), encoding="utf-8")
            process = subprocess.run(
                [
                    sys.executable,
                    str(scripts / script),
                    str(input_path),
                    *([] if script == "artifact.py" else ["--export"]),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
        receipt = json.loads(process.stdout)
        artifact = receipt if script == "artifact.py" else receipt["artifact"]
        path = Path(artifact["path"])
        result = {"name": name, "path": str(path), "text": path.read_text(), "receipt": receipt, "input": data}
        documents.append(result)
        return result

    try:
        export(
            "intake",
            "artifact.py",
            {
                **invoice,
                "artifact_type": "Intake brief" if locale == "en" else "Intakeoverzicht",
                "body": (
                    "SYNTHETIC client wishes: prepare a will. Family status unknown; business goals not supplied. Request facts; no approval."
                    if locale == "en"
                    else "SYNTHETIC clientwens: testament voorbereiden. Gezinssituatie onbekend; ondernemingsdoelen niet aangeleverd. Vraag feiten; geen goedkeuring."
                ),
            },
        )
        documents.extend(create_journey(persona, locale))
        earlier_time = next(item for item in documents if item["name"] == "time")["input"]
        invoice["time"] = copy.deepcopy(earlier_time)
        invoice["sources"].extend(earlier_time["sources"])
        invoice["rates"] = {row["id"]: next(iter(invoice["rates"].values())) for row in earlier_time["entries"]}
        deed = next(item for item in documents if item["name"] == "deed")
        execution = next(item for item in documents if item["name"] == "execution")["input"]
        settlement = copy.deepcopy(next(item for item in documents if item["name"] == "supplied-funds")["input"])
        settlement["entries"][0]["amount"] = "400"
        export(
            "correspondence",
            "legal_record.py",
            {
                **deed["input"],
                "kind": "delivery",
                "recipient": "SYNTHETIC client",
                "subject": "SYNTHETIC review",
                "message": "SYNTHETIC: request missing approval. NOT SENT / NIET VERZONDEN.",
                "items": [],
            },
        )
        export("invoice", "invoice.py", invoice)
        sources = {}
        for document in documents:
            for source in document["input"]["sources"]:
                sources[source["reference"]] = source
        for source in invoice["sources"]:
            sources[source["reference"]] = source
        roles = {
            "intake": "intake",
            "time": "time",
            "research": "research",
            "deed": "deed",
            "execution": "observations",
            "missing-amount": "payment",
            "supplied-funds": "payment",
            "index": "deed",
            "correspondence": "correspondence",
            "invoice": "invoice",
        }
        items = []
        for document in documents:
            reference = f"SYNTHETIC generated {document['name']} source"
            sources[reference] = {"reference": reference, "kind": "synthetic"}
            items.append(
                {
                    "id": document["name"],
                    "title": f"SYNTHETIC {document['name']}",
                    "category": "artifact",
                    "role": roles[document["name"]],
                    "dossier": invoice["dossier"],
                    "source": reference,
                    "version": document["input"].get("version", "SYNTHETIC v1"),
                    "availability": "generated",
                    "path": document["path"],
                    "summary": "DRAFT / CONCEPT",
                }
            )
        data = {
            **deed["input"],
            "version": "SYNTHETIC handoff v1",
            "matter": "will",
            "items": items,
            "changes": [],
            "sources": list(sources.values()),
            "execution": execution,
            "settlement": settlement,
            "invoice": invoice,
        }
        previous_handoff = export("handoff", "handoff.py", data)
        if correction:
            corrected = copy.deepcopy(data)
            correction_source = {"reference": "SYNTHETIC correction C", "kind": "user_observation"}
            corrected["sources"].append(correction_source)
            bill = corrected["invoice"]
            bill["sources"].append(correction_source)
            bill["time"]["sources"].append(correction_source)
            original = bill["time"]["entries"][0]
            original.update(decision="exclude", confirmation=correction_source["reference"])
            bill["time"]["entries"].append(
                {
                    **original,
                    "id": "replacement",
                    "hours": "0.5" if locale == "en" else "0,5",
                    "correction_of": original["id"],
                    "decision": "count",
                    "source": correction_source["reference"],
                }
            )
            bill["rates"]["replacement"] = bill["rates"][original["id"]]
            bill["version"] = "SYNTHETIC corrected invoice v2 C"
            bill["correction_ids"] = ["C"]
            bill["locale"] = output_locale or locale
            replacement = export("corrected-invoice", "invoice.py", bill)
            corrected.update(
                version="SYNTHETIC handoff v2",
                locale=output_locale or locale,
                changes=[
                    {
                        "id": "C",
                        "kind": "time",
                        "source": correction_source["reference"],
                        "original": "prep 0.25",
                        "replacement": "prep 0.5; exclude old, include replacement",
                    }
                ],
            )
            for document, role, corrections in (
                (previous_handoff, "archive", []),
                (replacement, "invoice", ["C"]),
            ):
                reference = f"SYNTHETIC generated {document['name']} source"
                corrected["sources"].append({"reference": reference, "kind": "synthetic"})
                corrected["items"].append(
                    {
                        "id": document["name"],
                        "title": f"SYNTHETIC {document['name']}",
                        "category": "artifact",
                        "role": role,
                        "dossier": corrected["dossier"],
                        "source": reference,
                        "version": document["input"]["version"],
                        "availability": "generated",
                        "path": document["path"],
                        "regenerated_from": corrections,
                    }
                )
            export("corrected-handoff", "handoff.py", corrected)
        return documents
    except Exception:
        for document in documents:
            Path(document["path"]).unlink(missing_ok=True)
        raise
