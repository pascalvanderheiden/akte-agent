# Reconciliation input

Reuse common dossier/locale/sources from `contract.md`. Execute the trusted
`scripts/reconciliation.py INPUT.json [--export]` from the file inventory.
This is a review of supplied EUR settlement figures, not a bank ledger or invoice
posting. Other currencies need clarification; they are not silently converted.

Required `currency: "EUR"`, `rounding: "ROUND_HALF_UP"`: round each included line
to cents, ties away from zero, then add the rounded lines exactly. Preserve raw
strings beside rounded values. Example: two distinct EUR 0.005 lines become
EUR 0.01 each, summed EUR 0.02 (not aggregate-first EUR 0.01). Disclose/agree this
convention; it is not a tax/legal rule. Uses only shared `exact.py` operations.

`entries` has 1-1,000 objects:

- `id`, `description`, `source` (existing reference), `category`:
  `client_funds`, `tax`, `office_fee`, or `other_charge`.
- `amount`: nonnegative decimal string, never float. Both comma and point
  accepted, no grouping. Ambiguous `1,000`/`1.000` needs confirmed
  `decimal_separator` (`,`/`.`). Missing, invalid and ambiguous input remains
  visible with a reason and blocks all totals; it never becomes zero. Negative
  reversals require clarification/corrected records rather than implicit netting.
- `tax_treatment`: explicit supplied text for every tax/fee/charge. Specify
  included/excluded/separate tax or explicit not applicable. No assumed tax,
  rate or auto-derived tax. Flag overlapping inclusive-fee/separate-tax amounts
  with `review_reason` before totaling. A separate `tax` row is a supplied
  amount, not an instruction to apply that amount as a percentage.
- `payment_source`: explicit supplied confirmation source for receipt of client
  funds or payment of a charge. Missing evidence stays flagged and blocks any
  balanced status, even if numerical difference is zero. A source reference
  validates attribution, not payment authenticity.
- `review_reason`: semantic conflict or duplicate requiring user review.
  Exact category/normalized-description duplicates and zero amounts are also
  flagged automatically. `correction_of` references an earlier ID; preserve both.
- `decision`: `count`/`exclude` plus `confirmation` referencing a supplied user
  decision source. Every affected duplicate/correction/review row needs a
  decision before totals. Excluded rows remain displayed and validated.
  An assumed amount cannot be included merely by confirming it; append supplied
  evidence instead.

`coverage` maps each of the four category names to a source confirming its
complete supplied scope. Omission means unknown, not zero. A category with no
rows totals zero only with this explicit coverage evidence. Missing amounts
or tax treatment still block totals even on an excluded original: obtain valid
source input, retaining the earlier invalid text in the source record.

Output: `entries` with flags/raw/rounded values, `issues`, `review_status`.
`totals`/localized `display` appear only with fully specified arithmetic.
`balance = client_funds - (tax + office_fee + other_charge)`. Missing payment
evidence and a nonzero difference keep `pending`; otherwise `balanced` means
only the supplied figures balance, never official completion or clearance.
All amounts are string decimals, no JSON float or implicit currency conversion.

```json
{
  "dossier": "SYNTHETIC-AKTE-FUNDS",
  "locale": "en",
  "currency": "EUR",
  "rounding": "ROUND_HALF_UP",
  "sources": [{"reference": "SYNTHETIC complete statement A", "kind": "synthetic"}],
  "coverage": {"client_funds": "SYNTHETIC complete statement A", "tax": "SYNTHETIC complete statement A", "office_fee": "SYNTHETIC complete statement A", "other_charge": "SYNTHETIC complete statement A"},
  "entries": [
    {"id": "funds", "category": "client_funds", "description": "Supplied third-party funds", "amount": "484.00", "source": "SYNTHETIC complete statement A"},
    {"id": "fee", "category": "office_fee", "description": "Supplied office fee", "amount": "350.00", "tax_treatment": "Excludes supplied separate tax", "source": "SYNTHETIC complete statement A"},
    {"id": "cost", "category": "other_charge", "description": "Supplied other charge", "amount": "50.00", "tax_treatment": "Excludes supplied separate tax", "source": "SYNTHETIC complete statement A"},
    {"id": "tax", "category": "tax", "description": "Explicit supplied tax amount, no inferred rate", "amount": "84.00", "tax_treatment": "Separate supplied total tax", "source": "SYNTHETIC complete statement A"}
  ]
}
```

This example's numerical difference is EUR 0.00 but missing payment confirmations
keep it unresolved. `--export` uses the shared artifact writer; structural or
storage errors return `error`, exit 1, no path. Figure issues produce a useful
pending worksheet, not a success-shaped balanced result.
Export stdout contains only the artifact path/status, review issues and any
totals/display; full rows and sources remain in the file. This avoids the code
tool's stdout limit swallowing a successful download path. Python `calculate()`
and non-export JSON retain full rows; print only selected fields through tools.
