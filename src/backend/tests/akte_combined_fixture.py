"""Synthetic cross-stage inputs through real packaged CLIs, never model output."""

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from tests.test_akte_artifacts import time_input


def create_journey(persona: Path, locale: str) -> list[dict]:
    legal = json.loads((persona / "skills/legal-preparation/references/synthetic-legal.json").read_text())[locale]
    preparation = json.loads(
        (persona / "skills/execution-preparation/references/synthetic-preparation.json").read_text()
    )[locale]
    scripts = persona / "skills/working-artifacts/scripts"
    documents = []

    def export(name: str, script: str, data: dict) -> dict:
        with tempfile.TemporaryDirectory(prefix="akte-combined-input-") as directory:
            input_path = Path(directory) / "input.json"
            input_path.write_text(json.dumps(data), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(scripts / script), str(input_path), "--export"],
                capture_output=True,
                text=True,
                check=True,
            )
        receipt = json.loads(result.stdout)
        path = Path(receipt["artifact"]["path"])
        document = {"name": name, "path": str(path), "text": path.read_text(), "receipt": receipt, "input": data}
        documents.append(document)
        return document

    try:
        time = time_input(locale)
        time["dossier"] = legal["dossier"]
        export("time", "time_record.py", time)
        export("research", "legal_record.py", {**legal, "kind": "research"})
        deed = export("deed", "legal_record.py", {**legal, "kind": "deed"})
        draft_ref = f"SYNTHETIC generated legal deed {legal['version']}, DRAFT, not approval"
        draft_source = {"reference": draft_ref, "kind": "synthetic"}
        execution = copy.deepcopy(preparation["execution"])
        execution.update(
            dossier=legal["dossier"],
            sources=[*execution["sources"], *legal["sources"], draft_source],
            clauses=[
                {
                    "reference": legal["version"],
                    "excerpt": deed["text"],
                    "explanation": (
                        "Generated draft: only the supplied name was approved. Amount and provisions remain unknown; no authority or capacity finding."
                        if locale == "en"
                        else "Gegenereerd concept: alleen de aangeleverde naam is goedgekeurd. Bedrag en bepalingen onbekend; geen vaststelling bevoegdheid of wilsbekwaamheid."
                    ),
                    "source": draft_ref,
                }
            ],
            signing_evidence=[],
        )
        export("execution", "execution_record.py", execution)
        missing = copy.deepcopy(preparation["reconciliation"])
        missing.update(dossier=legal["dossier"], sources=[*missing["sources"], draft_source])
        missing["entries"][0].update(
            amount=None,
            source=draft_ref,
            review_reason="SYNTHETIC draft amount remains unknown; request supplied funds evidence",
        )
        export("missing-amount", "reconciliation.py", missing)
        supplied = copy.deepcopy(preparation["reconciliation"])
        supplied.update(dossier=legal["dossier"], sources=[*supplied["sources"], draft_source])
        export("supplied-funds", "reconciliation.py", supplied)
        generated_sources = [
            {"reference": f"SYNTHETIC generated {document['name']}, DRAFT", "kind": "synthetic"}
            for document in documents
        ]
        index = {
            **legal,
            "kind": "index",
            "sources": [*legal["sources"], *generated_sources],
            "items": [
                *legal["items"],
                *[
                    {
                        "id": f"generated-{document['name']}",
                        "title": f"SYNTHETIC generated {document['name']}",
                        "source": source["reference"],
                        "category": "artifact",
                        "availability": "generated",
                        "path": document["path"],
                        "version": legal["version"] if document["name"] == "deed" else "SYNTHETIC working 1",
                        "summary": "DRAFT only; no official completion evidence",
                    }
                    for document, source in zip(documents, generated_sources, strict=True)
                ],
            ],
        }
        export("index", "legal_record.py", index)
        return documents
    except Exception:
        for document in documents:
            Path(document["path"]).unlink(missing_ok=True)
        raise
