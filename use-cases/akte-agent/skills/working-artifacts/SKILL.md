---
name: working-artifacts
description: Calculate exact durations or supplied financial amounts and create labeled temporary dossier drafts, including intake summaries and time records
enabled: true
---

## Calculate, review, export

1. Read `references/contract.md` before constructing input. Reuse the trusted
   scripts listed in this skill's file inventory via `code_interpreter`; scripts
   use only Python's standard library and need no other persona.
2. Preserve source strings and individual activities. Obtain confirmation for
   zero, corrections and possible duplicates. An ambiguous/negative/invalid
   value is an error, never zero. Pending review means **no total**, not an
   implied exclusion. A user's confirmation needs its actual message reference.
3. Run `scripts/time_record.py INPUT.json` for exact time calculation. Inspect
   JSON output before presenting it. To export, run the same command with
   `--export`; pending records remain visibly pending without a total.
   `0.25`, `1.0`, `0.5` (or `0,25`, `1,0`, `0,5`) yield exactly
   `1.75` hours / `105` minutes; Dutch display is `1,75` uur / `105` minuten.
4. For intake/email/other draft text, run `scripts/artifact.py INPUT.json`.
   It adds the common dossier, type, review and evidence header. Use the intake
   template's complete sections, not just a list of facts.
5. Only a successful result's `path` can become a download link. Confirm the
   file exists and is nonempty. Show storage limitations in the response:
   temporary container storage may disappear on restart/expiry; save reviewed
   files to the approved office system. Errors mean generation failed: provide
   draft text inline if useful, without claiming a downloadable file exists.

For research, deeds, translation, comparison, dossier records and delivery,
read `references/legal-record.md` and use `scripts/legal_record.py`. Load
`legal-preparation` for the evidence and human-review process first.

## Later financial drafts

`scripts/exact.py` provides `parse_decimal`, `exact_sum`, `exact_product`,
`format_decimal` and `currency`. Import by the inventory's absolute script path
using `runpy.run_path`, or add that script directory to `sys.path`.
Amounts enter as strings, never binary floats. Use only user-supplied rates,
costs and tax rules. `currency` requires an explicit rounding argument;
`ROUND_HALF_UP` rounds to cents, ties away from zero. Disclose that rule and
obtain agreement when needed. It is a calculation convention, not tax advice.
Keep unrounded values for intermediate arithmetic and client funds separate
from office fees. This helper does not implement invoice posting or banking.

For stage 4/5 drafts, load `execution-preparation` or `reconciliation` and read
`references/execution-contract.md` or `references/reconciliation-contract.md`
respectively. Run the matching packaged script; reconciliation explicitly
rounds each line before summing payable cents, retaining original precision.
