# Akte execution preparation and reconciliation

Stages 4/5 are draft-only preparation using supplied evidence in English or
Dutch. Enter directly with a supplied deed/settlement or continue the same
dossier. No scanner, register, bank, signature, office accounting or cloud
integration is introduced.

`execution-preparation` separates agent-proposed questions from attributed
observations. Physical identity and official scanner checks remain human tasks;
understanding, consequences, free will and pressure questions support, but do
not replace, the notary's capacity assessment. Authority is distinct from
capacity. Coercion, uncertainty, contradictions and missing approvals remain
unresolved. Supplied signing reports identify their sources; generated documents
always remain unsigned drafts. Plain-language deed explanations preserve Dutch
legal concepts and clause references without certifying law or translation.

`reconciliation` separates client funds on the third-party account from taxes,
office fees and other charges. Its EUR worksheet uses shared exact Decimal
arithmetic: explicit ROUND_HALF_UP per included line to cents, ties away from
zero, then exact sum. Raw precision remains visible. Only explicitly supplied
tax amounts/treatment are used; no tax rate inferred.

Missing/ambiguous amounts, missing tax treatment, incomplete category scope and
unconfirmed duplicates/corrections block totals. Missing payment evidence and
nonzero differences remain unresolved even when numerical totals are available.
Zero difference means only supplied figures balance, not payment verification
or execution clearance. Corrections preserve original records and source IDs.

## Packaged seams

All skills/templates are local to Akte and included by its normal registry and
standalone exporter. `working-artifacts/scripts/execution_record.py` renders
structured observations, concerns, supplied clauses and signing reports;
`reconciliation.py` calculates supplied figures. Both reuse `artifact.py` for
fixed dossier/type/draft/evidence headers and temporary-storage notices.
`exact.py` and `time_record.py` remain unchanged.

Read `working-artifacts/references/execution-contract.md` and
`reconciliation-contract.md` for inputs, limits and failures. The code helpers
validate structure/arithmetic, not authenticity, legal meaning or semantic
coercion detection; evidence review and live model behavior need separate
evaluation. No new per-stage service API or persistent dossier database.

For stage 6 billing/handoff, reuse `exact.py` and common source metadata.
Reconciliation returns string-decimal `totals`/localized `display` only when
arithmetic inputs resolve, `entries` with original evidence/review decisions,
and independent `issues` for missing payments/imbalance. Do not treat
`review_status: balanced` as billing approval or registration evidence.

## Verification boundaries

`test_akte_execution.py` covers actual helper behavior, code-tool execution,
standalone exports and downloaded bytes. `12-akte-execution.spec.ts` extends the
existing browser harness for both stages/languages, continued dossier corrections,
and missing-generation/download paths. Browser model responses are deterministic
synthetic fixtures, **not live model proof**.

Eight additional bilingual scenarios load through the existing evaluation
harness for normal preparation, coercion/uncertainty/contradiction, document
injection and exact reconciliation/corrections. They require a separately
authorized configured model to establish live behavior; deterministic passes do
not mean live scanner/banking/signing or model evaluation succeeded.

Generated files are temporary, not an official or compliant archive. Artifact
paths are returned only after successful writes; tool/storage failures remain
explicit errors, never fabricated operational success.
