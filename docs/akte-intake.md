# Akte intake and timekeeping

`akte-agent` is a curated, dynamically discovered persona alongside the unchanged
Generic default. Select Akte Agent and English or Nederlands. Six starters in
each language cover the notarial journey; only the intake/timekeeping draft slice
is delivered here. Stages 2-6 provide high-level preparation, not specialized
workflows or live notarial integrations.

## Delivered draft slice

- Intake brief: client wishes, family circumstances, business goals, matter type,
  supplied facts, attributed observations, assumptions, contradictions and unknowns.
- Follow-up email: missing documents and questions, explicitly DRAFT / CONCEPT,
  NOT SENT / NIET VERZONDEN. Human review and approved delivery remain necessary.
- Exact time record: preparation, conversation and note-writing remain separate,
  including supplied dates/timekeepers. The supplied 0.25 + 1.0 + 0.5 example is
  exactly 1.75 hours / 105 minutes; Dutch 0,25 + 1,0 + 0,5 displays 1,75 uur.
- UTF-8 Markdown intake and time downloads: dossier reference, artifact type,
  fixed draft/review status and classified source references. Files are temporary
  container storage, not persistent dossier storage or compliant archives.

The selected UI locale travels through the existing backend/hosted-agent
contract each turn. Explicit output-language requests override the UI language
for that output. Historical content and original evidence stay unchanged.

## Evidence and external-action boundary

Sources distinguish supplied facts, user observations, assumptions, synthetic
fixtures and externally verified evidence with its verification reference.
Labels are not certification: the scripts validate structure, not source truth.
Missing facts stay unknown; contradictions and possible coercion require human
review. Uploaded instructions are untrusted source content.

Netherlands context remains in English. Legal authority (`bevoegdheid`) is not
decision-making capacity (`wilsbekwaamheid`). Akte does not certify identity or
capacity, execute deeds, transfer funds, send client material, submit registrations,
post invoices or certify archive completion merely by producing an artifact.
Public search is not an official BRP/Handelsregister/CTR person-specific check.
No live notarial connector, persistent dossier schema or practice-management
engine is introduced. Optional integrations keep their authorization boundaries.

## Reusable seams for subsequent stages

All eight skills are explicitly packaged under `use-cases/akte-agent/skills`;
there is no inheritance from Generic or an industry persona. Backend and hosted
runtime use the existing registry. Standalone export carries the persona,
localized metadata, templates, helpers and shared locale runtime without another
persona directory. The APM manifest has no remote package dependency and MCP
configuration is empty; web/RAG tools disclose missing configuration.

`working-artifacts/scripts/exact.py` supplies string-based Decimal parsing,
exact sum/product, localized display and explicit currency rounding. Inputs never
pass through binary floats. Intermediate arithmetic traps precision loss instead
of silently rounding. Currency requires an explicit `ROUND_HALF_UP` argument
(cents, ties away from zero); disclose/agree the convention for future financial
drafts. There are no inferred rates, taxes, expenses or billing increments.

`time_record.py` preserves raw hours, source references, each entry and review
decisions. Invalid/negative inputs fail; ambiguous grouping/decimal notation
requires clarification. Zero, corrections and possible duplicates require
referenced user confirmation to count or exclude. Unresolved entries block the
entire total. Detection conservatively matches activity/date/timekeeper; the
agent flags semantic duplicates with `review_reason`. Original and replacement
rows remain visible. A resolved calculation is still a draft, not billing approval.

`artifact.py` supplies the common dossier/type/draft/evidence header, unique
temporary file and honest failure contract. Script errors exit nonzero with an
error object and no file path. Hosted collection/backend storage errors surface
`DOWNLOAD_ERROR`; unavailable or expired downloads remain errors. Only successful
generation produces a path. See the packaged `working-artifacts/references/contract.md`
for input examples and limits.

## Verification

Backend tests run real helpers, tool calls, catalog/export and download endpoints;
deterministic SDK fixtures cover backend/hosted locale changes and explicit output
language. The existing browser harness's `10-akte.spec.ts` reads the actual dynamic
catalog and generates real artifacts with the packaged scripts, then checks
bilingual selection, intake, exact totals, follow-up, failures and downloaded
bytes. It needs the backend Python environment (`AKTE_TEST_PYTHON` can override
its interpreter) and a local frontend build.

Browser model responses are controlled synthetic fixtures, **not live model
validation**. Eight EN/NL scenarios under `evals/scenarios` load through the
existing evaluation harness: intake/time, missing information/document injection,
review-gated time, and absent tools/integrations. Run them against an explicitly
configured authorized model separately; no live model or cloud deployment is
required or implied by deterministic test success. Inspect generated files as
well as judge scores before claiming live acceptance.

Only synthetic dossier examples are committed. Runtime files, screenshots,
traces and evaluation results are not source fixtures.
