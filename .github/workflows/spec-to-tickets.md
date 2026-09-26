---
emoji: 🎟️
description: Break a `to-ticket` labelled spec issue into tracer-bullet ticket sub-issues.
intent: Every spec issue a maintainer labels `to-ticket` is broken into tracer-bullet ticket sub-issues labelled `ticket` and `ready-for-agent`, linked under the spec with their blocking edges recorded, and the spec chain advances from `specced` to `planned`.
on:
  label_command:
    name: to-ticket
    events: [issues]
    remove_label: false
concurrency:
  job-discriminator: ${{ github.event.issue.number || github.run_id }}
permissions:
  contents: read
  issues: read
skills:
  - .github/skills/to-tickets
tools:
  github:
    mode: gh-proxy
    toolsets: [issues]
safe-outputs:
  create-issue:
    max: 12
    labels: [ticket, ready-for-agent]
    allowed-labels: [ticket, ready-for-agent, feature, bug]
    require-temporary-id: true
  link-sub-issue:
    max: 12
  remove-labels:
    allowed: [to-ticket]
    target: triggering
    max: 1
  replace-label:
    allowed-remove: [specced]
    allowed-add: [planned]
    target: "*"
    max: 1
---

# Spec To Tickets

## Context

- Triggering issue: `#${{ github.event.issue.number }}` in `${{ github.repository }}`. This is the **spec issue** — it carries the `spec` label and holds the spec written by the `issue-to-spec` workflow.
- The spec issue is itself a sub-issue of an **origin issue**: the request a maintainer originally filed, which carries `specced`.
- Three issues are in play. Keep them straight:

  | Role | Which issue | What happens to it |
  | --- | --- | --- |
  | Origin issue | parent of the triggering issue | `specced` → `planned` |
  | Spec issue | the triggering issue `#${{ github.event.issue.number }}` | loses `to-ticket`; becomes parent of the tickets |
  | Tickets | the issues you create | labelled `ticket` + `ready-for-agent` |

- The spec body is the sole statement of intent. There is no user to interview — nobody will answer a question you ask.

## Task

1. **Read the spec issue.** Use `gh issue view ${{ github.event.issue.number }} --json number,title,body,labels,comments,parent` to get its title, body, comments, current labels **and its parent**. Read the comments too: they often refine or correct the spec.

   Record the parent's number from the `parent` field — that is the origin issue, needed in step 8. If `parent` is `null` the spec has no origin issue; note that and skip step 8 only.

2. **Decide whether there is enough to break down.** If the body is empty, a placeholder, or not actually a spec, call `noop` with a one-line reason and stop. Do not create issues, do not relabel. A short-but-concrete spec is ticketable; a contentless one is not.

3. **Understand the codebase before slicing.** Read the root `CONTEXT.md`, any relevant records under `docs/adr/`, and the areas of `src/` the spec touches. Ticket titles and bodies must use the project's own domain vocabulary and respect existing ADRs in the area being changed.

4. **Break the spec into tickets.** Follow `.github/skills/to-tickets/SKILL.md` — the `to-tickets` skill installed for this run. Honour its `<vertical-slice-rules>`: each ticket is a tracer bullet cutting a narrow but complete path through every layer, independently demoable, sized for a single fresh context window, with prefactoring sequenced first. Use its expand–contract sequencing for wide refactors rather than forcing them into a vertical slice.

   Use its `<issue-template>` verbatim as the ticket body structure: Parent, What to build, Acceptance criteria, Blocked by.

   Three deviations from that skill, because this runs unattended:
   - Skip step 4 ("Quiz the user") entirely. There is nobody to approve the breakdown. Instead, commit to your breakdown and make the blocking edges explicit so a human can disagree on review.
   - Skip its step 5 publishing instructions. Do not publish with `gh issue create`, do not apply labels with `gh issue edit`, and do not call the dependencies API yourself. Publishing happens through the safe outputs below.
   - Ignore its local-files tracker option. This repository's tracker is GitHub; see `docs/agents/issue-tracker.md`.

   Prefer a handful of substantial tickets over a long tail of trivial ones. Do not exceed 12 tickets; if the spec genuinely needs more, ticket the first coherent tranche and say so in the last ticket's body.

5. **Emit one `create_issue` per ticket**, in dependency order (blockers first), each with:
   - a `temporary_id` (required),
   - a title that reads as a unit of work, in the project's domain vocabulary,
   - the ticket body, opening with a line linking back to the spec: `Part of #${{ github.event.issue.number }}`,
   - `blocked_by` set to the `temporary_id`s of the tickets that genuinely gate this one. Omit `blocked_by` for tickets that can start immediately. Temporary IDs resolve regardless of emission order, but keep the order dependency-first anyway so the breakdown reads correctly.
   - labels: `ticket` and `ready-for-agent` on every ticket, plus `feature` or `bug` **only if** that label is present on the spec issue. If the spec issue carries neither, fall back to whichever of the two the origin issue carries. Copy at most one of the two, and copy nothing if neither issue carries either.

   The `Blocked by` section of the body must agree with the `blocked_by` field; write `None (can start immediately)` when there are no blockers.

6. **Link every ticket to the spec issue.** Emit one `link_sub_issue` per ticket, with `parent_issue_number` set to `${{ github.event.issue.number }}` and `sub_issue_number` set to that ticket's `temporary_id` from step 5.

7. **Clear the trigger.** Emit `remove_labels` removing `to-ticket` from the triggering issue.

8. **Advance the origin issue.** Emit `replace_label` on the origin issue number recorded in step 1, removing `specced` and adding `planned`. This is the only output that targets an issue other than the triggering one — set its issue number explicitly. Skip this step, and only this step, when the spec issue has no parent.

Steps 5-8 are a set: emit all of them or none. If you reach step 5 you must complete 6, 7 and 8 in the same run, otherwise the spec is left labelled `to-ticket` with orphaned tickets hanging off it.

## Safe Outputs

- `create_issue` — one per ticket (body must be a real ticket, 20-65000 characters; never a placeholder).
- `link_sub_issue` — attaches each ticket to the triggering spec issue.
- `remove_labels` — drops `to-ticket` from the triggering spec issue.
- `replace_label` — swaps `specced` for `planned` on the origin issue.
- `noop` — use with a short reason when the spec carries too little signal to break down. A successful no-op run is a valid outcome.

Do not mutate GitHub directly with `gh` write commands; all writes go through the safe outputs above.
