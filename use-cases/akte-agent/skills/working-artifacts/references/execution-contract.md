# Execution preparation input

Use common dossier/locale/sources metadata from `contract.md`. Run the trusted
`scripts/execution_record.py INPUT.json [--export]` from this skill inventory.
No other persona, services or credentials are needed. Every output is draft
and unresolved for human review, even with supplied completion claims.

Optional arrays (missing arrays remain visible unknowns), at most 1,000 rows:

- `observations`: `id`, `topic`, `text`, `recorded_by`, `source`; optional
  `correction_of` references an earlier observation ID. Topics: `identity`,
  `scanner`, `understanding`, `consequences`, `free_will`, `pressure`, `authority`,
  `approval`. Original and correction both remain. Observations are supplied,
  not answers invented to fill agent-proposed questions.
- `concerns`: `category`, `text`, `source`. Categories: `coercion`,
  `capacity_uncertain`, `contradiction`, `missing_approval`, `other`.
  Identify semantic concerns during evidence review; the renderer is not a
  classifier or professional judgment engine. All concerns remain unresolved.
- `clauses`: `reference` (clause/page/version), `excerpt` (original supplied
  text), `explanation` (requested-language draft), `source`. Preserve all material
  provisions and Dutch legal meaning; mark unresolved terms in the explanation.
  The script renders the prepared explanation; it does not interpret law.
- `signing_evidence`: `claim` (`signed`, `executed`, `unsigned`), `text`, `source`.
  This records attributed reports only, including contradictions. Assumptions
  cannot support signing claims. Missing evidence means unknown, not signed.

All source references must exist and be unique in `sources`; dates/versions
belong in supplied source references or text, never invented defaults.
The helper validates structure, not authenticity. User-described scanner,
signature and payment results remain user/supplied evidence, never upgraded to
`verified_external`. Questions and observations appear in separate sections.

Direct-entry example (synthetic):

```json
{
  "dossier": "SYNTHETIC-AKTE-EXEC",
  "locale": "en",
  "sources": [{"reference": "SYNTHETIC appointment note A", "kind": "user_observation"}],
  "observations": [{"id": "obs1", "topic": "pressure", "text": "Relative answers for client.", "recorded_by": "SYNTHETIC Notary A", "source": "SYNTHETIC appointment note A"}],
  "concerns": [{"category": "coercion", "text": "Discuss privately; escalate before proceeding.", "source": "SYNTHETIC appointment note A"}]
}
```

`--export` uses `artifact.py` for the fixed draft header, source classifications,
temporary-storage notice and actual file path. Errors exit 1 with an error object
and no path. Supplied evidence and text cannot execute code or official actions.
Export stdout is a compact status/path summary so the code tool's output limit
does not hide the download path. Full observations and explanation stay in the
artifact. When using Python `prepare()` or non-export JSON output, select only
needed fields for tool stdout rather than printing a whole dossier.
