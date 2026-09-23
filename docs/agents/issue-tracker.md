# Issue tracker: GitHub

Issues and specs live in GitHub Issues for `pascalvanderheiden/akte-agent`.
Infer the repository from `git remote -v`; `gh` resolves it inside this clone.

## Conventions

- Create: use the integrated `create_issue` tool when available, preserving its confirmation flow. Otherwise use `gh issue create --title "..." --body-file <file>`.
- Before creating: check existing issues for duplicates and follow applicable repository issue templates.
- Read: `gh issue view <number> --comments`; fetch labels with `--json` when needed.
- List: `gh issue list --state open --json number,title,body,labels,comments`, adding label/state filters as needed.
- Comment: `gh issue comment <number> --body "..."`.
- Label: `gh issue edit <number> --add-label "..."` or `--remove-label "..."`.
- Close: `gh issue close <number> --reason completed --comment "..."`; use `--reason "not planned"` for declined work.

Publishing to the issue tracker means creating a GitHub issue. Fetching a ticket means reading the issue and its comments.

## Pull requests as a triage surface

**PRs as a request surface: no.**

If explicitly changed to `yes`, use the same triage states for external PRs.
Read with `gh pr view <number> --comments` and `gh pr diff <number>`.
Queue PRs whose author association is CONTRIBUTOR, FIRST_TIME_CONTRIBUTOR, or NONE; exclude OWNER, MEMBER, and COLLABORATOR.
Use `gh pr comment`, `gh pr edit --add-label/--remove-label`, and `gh pr close` for triage operations.
Issues and PRs share numbers: resolve an ambiguous reference with `gh pr view <number>`, then `gh issue view <number>` if it is not a PR.

## Wayfinding operations

- Map: one issue labelled `wayfinder:map`, containing Notes, Decisions-so-far, and Fog.
- Children: GitHub sub-issues labelled `wayfinder:<type>` (research, prototype, grilling, task). If unavailable, use a task list in the map and `Part of #<map>` in each child.
- Blocking: native issue dependencies. Add with `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`. Obtain the database ID with `gh api repos/<owner>/<repo>/issues/<number> --jq .id`; it is not the issue number or node ID.
- If dependencies are unavailable, record `Blocked by: #<number>` in the child body. A ticket is unblocked when all blockers are closed.
- Frontier: open children in map order, with no assignee and no open blockers (`issue_dependencies_summary.blocked_by` equals zero, or all textual blockers are closed).
- Claim: `gh issue edit <number> --add-assignee @me`, before implementing the ticket.
- Resolve: comment with the answer, close as completed, and append a concise result and ticket link to the map's Decisions-so-far.
