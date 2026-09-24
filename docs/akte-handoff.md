# Akte: complete draft-only dossier handoff

Select Akte Agent and English/Nederlands. Continue one dossier through intake
and separate time records, sourced legal research, draft deeds/correspondence,
human identity/capacity observations, execution/funds preparation, then
registration preparation, invoice draft and closure inventory. Direct later-stage
entry is supported: supply the known dossier facts and sources; missing earlier
work stays missing rather than being invented.

The `billing-handoff` skill adds stage six to the eleven existing self-contained
skills. It uses the established code execution, temporary download, source and
locale contracts. No new API, persistent dossier database, workflow engine or
live notarial connector.

## Registration and closure

Supply the matter, Netherlands jurisdiction, actual executed-deed copy/version
and attributable signature evidence. The package identifies candidate follow-up
for the responsible notary: CTR for a testament, Handelsregister for company
matters, Kadaster for property. The notary confirms applicability, current
requirements, attachments, fees, deadlines and the authorized filing channel.
Other matters require clarification. These routes are not a legal checklist
library or certification of current law.

An approved draft is not a signature; generated checklists are not executed
deeds. Identity/capacity uncertainty, pressure/coercion and missing approvals
remain unresolved even with a reported execution. Actual supplied receipts may
be listed with literal references and source attribution, including refusals or
contradictions. The helper labels these **reports for review**, never independent
verification or a completed action by the agent.

Closure remains pending human review. Execution, payment, registration, billing,
archive and client-delivery evidence are separately visible. A generated invoice
cannot serve as invoice-posting evidence, nor an inventory as archive evidence.

## Exact invoices, separate client funds

Use reviewed separate activities and original durations. The bilingual synthetic
fixture supplies 0.25 + 1.0 + 0.5 hours, EUR 200/hour and EUR 50 register costs:

| Quantity | English | Nederlands |
| --- | --- | --- |
| Time | 1.75 hours / 105 minutes | 1,75 uur / 105 minuten |
| Office fees | EUR 350.00 | EUR 350,00 |
| Costs | EUR 50.00 | EUR 50,00 |
| Pre-tax | EUR 400.00 | EUR 400,00 |
| Final total | Unknown until tax treatment supplied | Onbekend tot fiscale behandeling aangeleverd |

`invoice.py` calls the unchanged exact time helper. All financial inputs are
decimal strings. Fees are exact hours times supplied rates, rounded per line
to cents using explicit `ROUND_HALF_UP` (ties away from zero), then summed
exactly. Costs follow the same rule. An explicitly supplied uniform tax rate
applies per rounded fee/cost line, each tax rounded to cents before summing.
Alternatively supply an explicit reviewed tax amount/treatment. No tax/rate,
billing increment or expense is inferred; differing tax treatments require a
reviewed explicit tax amount rather than an invented blended rate.

Missing rates/activity review block fees; missing expense evidence or confirmed
scope blocks pre-tax totals. Missing tax permits a clearly labeled pre-tax
subtotal, not a final total. Possible duplicate/corrected/zero time or costs need
referenced decisions for every affected row. Originals and replacements remain
visible, including exclusions. Ambiguous decimal/grouping strings require
clarification; negative durations and invalid input return explicit errors.

Client funds are **not** billed office fees or recognized revenue.
`handoff.py` recomputes the original reconciliation input using
`reconciliation.calculate`, carrying discrepancies and payment gaps forward.
Even zero difference is not proof of payment, authority to transfer or billing
approval. The sample combined handoff deliberately retains a EUR -84 discrepancy.

## Inventory, corrections and storage

Inventory rows share the dossier ID, source classification, role and version:
intake, time, research, deed versions, correspondence, observations,
execution/payment, registration, invoice, archive and delivery evidence.
Generated entries must identify an actual nonempty temporary file with matching
dossier header. Supplied entries need an actual excerpt and locator, not merely
a plausible title. Missing/failed/expired files are explicit, not attachments.

Corrections preserve original and replacement evidence. The helper
conservatively flags all prior generated versions stale after any client-fact,
deed-term, time or settlement change. The old invoice and signing checklist
cannot silently remain current. A new actual file must record correction IDs
before being identified as a regenerated working draft. Originals are retained;
uncertainty about earlier versions prompts review. Source review deadlines are
supplied review thresholds, not invented statutory expiry dates.

Downloads may expire or disappear on restart. Persistent conversation history
is **not an approved legal archive**. The notary saves reviewed artifacts and
original evidence through the approved office process and retains actual
archival/delivery confirmations. No automatic submission, posting, transfer,
delivery, external-system update or archival-completion claim.

An explicit output-language request overrides interface language for that output.
Switching the UI affects future turns only; original sources, raw messages and
prior artifacts stay in their original language. Uploaded instructions are
untrusted content, never authority to bypass human checks.

## Portable helpers and verification

Read `working-artifacts/references/handoff-contract.md` for the exact schema.
`invoice.py INPUT.json --export` and `handoff.py INPUT.json --export` return
compact receipts that fit the existing code tool's stdout limit. Full evidence
stays in the real files. Failed calculation/generation/download yields an
explicit limitation and no fabricated link.

`test_akte_handoff.py` covers bilingual arithmetic, missing evidence, corrections,
rounding, receipts, expired inventory, inert uploaded content and actual code-tool
exports. `test_akte_combined.py` runs the real complete CLI journey in repository,
standalone assembly and authenticated ZIP contexts and inspects download bytes.
Original helper interfaces and Generic/custom-persona behavior remain unchanged.

`13-akte-handoff.spec.ts` extends the existing Playwright harness: complete
EN/NL six-stage journeys, actual downloaded deed/invoice/handoff bytes,
corrections, language overrides, unchanged history, direct entry and failures.
Run with the existing locale/settings/eval/legal/execution and retirement suites,
at root and with `NEXT_PUBLIC_BASE_PATH`. These control model/transport responses:
**deterministic browser passes are not live agent compliance or integration proof**.

Eight bilingual `handoff-*` scenarios use the existing evaluation loader for
cross-stage consistency, correction impact, source honesty, output language and
human boundaries. Live model execution requires separately authorized configured
access; adding/loading scenarios is not a live evaluation pass. No credentials,
live model calls, deployment or destructive cleanup are needed for local checks.
