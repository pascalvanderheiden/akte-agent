# Stage-six inputs

Both scripts accept common `dossier`, `locale`, classified `sources`, and a
nonempty `version`. Sources have unique references; retain originals unchanged.
Each decision/rate/cost/tax reference must name a non-assumption source.
The helpers validate structure and arithmetic, not authenticity or legal meaning.

## Invoice

`invoice.py INPUT.json [--export]` uses:

- `currency: "EUR"`, `rounding: "ROUND_HALF_UP"` explicitly agreed.
- `time`: complete original `time_record.py` input for the same dossier, including
  separate entries, original precision and referenced count/exclude decisions.
  Its source metadata must also appear unchanged in the invoice's sources.
- `review_source`: supplied review of billable activities. Arithmetic readiness
  of the time record alone is not billing review.
- `rates`: map each included time entry ID to `{amount: "200", source: "..."}`
  with optional confirmed `decimal_separator`. No rate inferred from settlement.
- `costs`: array of `{id, category, description, amount, source}`; category is
  `register_cost` or `other_charge`. Preserve duplicates/corrections with unique
  IDs, `correction_of`, `decision: "count" | "exclude"`, and `confirmation`.
  Semantic duplicates use `review_reason`. Zero costs also require confirmation.
- `costs_source`: supplied confirmation that expenses are complete, including
  explicit no further costs. Empty array without this reference is not zero costs.
- Optional `tax`: `{treatment, source, amount}` OR
  `{treatment, source, rate_percent}` with decimal strings and optional
  `decimal_separator`. Rate means explicitly uniform over all included fee/cost
  lines. For differing treatments request a reviewed explicit total tax amount.

Calculate exact hours times rate, round each fee and cost line to cents with
ROUND_HALF_UP (ties away from zero), then exact sum. Uniform tax multiplies each
rounded included line by rate/100, rounds each tax line and sums. Explicit tax
amount is rounded once. Raw amounts and decisions remain in the file. Missing
tax leaves fees/costs/pre-tax available when otherwise resolved, but no final
total. Missing rate/time review blocks fees; missing costs/scope blocks pre-tax.
Unresolved duplicates/corrections block the affected subtotal, not silently
excluded. Invalid duration is an explicit error requiring clarification.

Result: `time`, `lines`, `costs`, `tax`, `totals` (only known subtotals),
`issues`, `review_status` (`pending` or `arithmetic_ready`). Neither status
authorizes posting. `as_artifact(result)` adds localized review guidance.
`--export` returns only artifact receipt, version, issues/status and totals;
full individual evidence is in the file, avoiding tool stdout truncation.

## Handoff

`handoff.py INPUT.json [--export]` uses:

- `jurisdiction: "Netherlands" | "Nederland"`, `matter` (`will`, `company`,
  `property`, or supplied text requiring route clarification), optional ISO `as_of`.
- `items`: legal-record index rows with `id`, `title`, `category`
  (`artifact`, `email`, `letter`, `revision`), `source`, `version`, `dossier`,
  `role`, and `availability`. Roles: `intake`, `time`, `research`, `deed`,
  `correspondence`, `observations`, `execution`, `payment`, `registration`,
  `invoice`, `archive`, `delivery`.
- `generated` availability requires a nonempty UTF-8 file in temporary storage,
  with matching dossier header. Missing generated files become `expired` and
  lose their path. `supplied` requires actual content `excerpt` and `locator`;
  a title alone is not available evidence. `missing`, `failed`, `expired` are
  explicit unavailable entries, not attachments. Source `review_after` compared
  with supplied `as_of` marks overdue items stale; not a statutory expiry claim.
- Optional `execution_evidence`, `payment_evidence`, `registration_evidence`,
  `invoice_evidence`, `archive_evidence`, `delivery_evidence`: arrays of
  `{item_id, source, claim, reference}`. The item must match the role/source,
  be `supplied` and non-stale. Preserve literal receipt/reference and claim,
  including negative/conflicting reports. These are **reports for review**,
  never a completed status. Generated invoice/deed/index files cannot support
  official completion claims.
- Optional `execution`, `settlement`, `invoice`: complete original input for
  `execution_record.prepare`, `reconciliation.calculate`, `invoice.calculate`.
  Same dossier; source metadata appears unchanged in outer sources. Handoff
  recomputes these, never trusts cached totals. Missing inputs remain visible.
- `changes`: array `{id, kind, original, replacement, source}`, kind
  `client_fact`, `deed_term`, `time`, `settlement`. Preserve both evidence
  versions. Every prior generated inventory item is conservatively stale after
  any change, unless its `regenerated_from` lists all change IDs and its actual
  new file records those IDs. Explicit `stale: true` stays stale. Supply both
  old and regenerated items with distinct IDs/versions; never overwrite originals.
  A source deadline or explicit stale flag cannot be bypassed by regeneration.

Result includes common renderable artifact fields, always `review_status:
pending`, per-role evidence statuses, missing roles/unavailable/stale versions,
and recomputed settlement/invoice issues. `--export` returns compact receipt,
evidence statuses and counts; detailed versions/evidence stay in the file.
Errors exit 1 with `error` and no success path. Helpers have no external-action
or archive-writing code. Temporary generation does not make an inventory a
compliant legal archive or prove any earlier official action.
