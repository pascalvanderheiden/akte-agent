---
emoji: 📋
description: Turn a `to-spec` labelled issue into a full spec published as a linked sub-issue.
intent: Every issue a maintainer labels `to-spec` gains a specced sub-issue holding an implementable spec derived from that issue's body, and the parent is marked `specced`.
on:
  label_command:
    name: to-spec
    events: [issues]
    remove_label: false
concurrency:
  job-discriminator: ${{ github.event.issue.number || github.run_id }}
permissions:
  contents: read
  issues: read
skills:
  - .github/skills/to-spec
tools:
  github:
    mode: gh-proxy
    toolsets: [issues]
safe-outputs:
  create-issue:
    max: 1
    labels: [spec, to-ticket]
    allowed-labels: [spec, to-ticket, feature, bug]
    require-temporary-id: true
  link-sub-issue:
    max: 1
  replace-label:
    allowed-remove: [to-spec]
    allowed-add: [specced]
    target: triggering
    max: 1
---

# Issue To Spec

## Context

- Triggering issue: `#${{ github.event.issue.number }}` in `${{ github.repository }}`.
- The issue body is the sole statement of intent. There is no user to interview — nobody will answer a question you ask.

## Task

1. **Read the triggering issue.** Use `gh issue view ${{ github.event.issue.number }} --json number,title,body,labels,comments` to get its title, body, comments and current labels. Read the comments too: they often refine the request.

2. **Decide whether there is enough to spec.** If the body is empty, a placeholder, or so vague that the spec would be invented rather than derived, call `noop` with a one-line reason and stop. Do not create an issue, do not relabel. A thin-but-clear request is specable; a contentless one is not.

3. **Understand the codebase before writing.** Read the root `CONTEXT.md`, any relevant records under `docs/adr/`, and the areas of `src/` the request touches. Use the project's own domain vocabulary throughout the spec and respect existing ADRs in the area you are changing.

4. **Write the spec.** Follow `.github/skills/to-spec/SKILL.md` — the `to-spec` skill installed for this run. Use its `<spec-template>` verbatim as the section structure: Problem Statement, Solution, User Stories, Implementation Decisions, Testing Decisions, Out of Scope, Further Notes.

   Two deviations from that skill, because this runs unattended:
   - Skip its interview and its "check with the user that these seams match their expectations" step. Instead, state the chosen test seams explicitly under **Testing Decisions** and name the alternatives you rejected, so a human can disagree on review.
   - Do not publish with `gh issue create` and do not apply `ready-for-agent`. Publishing happens through the safe outputs below.

   Keep the user-story list long and exhaustive. Avoid file paths and code snippets in Implementation Decisions — they go stale — except where a prototype-style snippet (schema, type shape, state machine) encodes a decision more precisely than prose.

5. **Publish the spec as a sub-issue.** Emit one `create_issue` with:
   - a `temporary_id` (required),
   - a title derived from the parent issue's title,
   - the spec as the body, opening with a line linking back to the parent: `Spec for #${{ github.event.issue.number }}`,
   - labels: `spec` and `to-ticket`, plus `feature` or `bug` **only if** that label is present on the parent issue. Copy at most one of the two, and copy nothing if the parent carries neither.

6. **Link it to the parent.** Emit `link_sub_issue` with `parent_issue_number` set to `${{ github.event.issue.number }}` and `sub_issue_number` set to the `temporary_id` from step 5.

7. **Mark the parent specced.** Emit `replace_label` removing `to-spec` and adding `specced` on the triggering issue.

Steps 5-7 are a set: emit all three or none. If you reach step 5 you must complete 6 and 7 in the same run, otherwise the parent is left labelled `to-spec` and will be re-processed.

## Safe Outputs

- `create_issue` — the spec sub-issue (body must be a real spec, 20-65000 characters; never a placeholder).
- `link_sub_issue` — attaches the spec to the triggering issue.
- `replace_label` — swaps `to-spec` for `specced` on the triggering issue.
- `noop` — use with a short reason when the issue body carries too little signal to derive a spec. A successful no-op run is a valid outcome.

Do not mutate GitHub directly with `gh` write commands; all writes go through the safe outputs above.
