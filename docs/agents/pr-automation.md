# PR automation

Four plain GitHub Actions carry a pull request from "opened" to "merged, with
every completed parent issue closed". None of them needs an LLM, so none is an
agentic workflow.

```
pr-copilot-review  ->  pr-reviewed  ->  pr-auto-merge  ->  close-parent-issues
```

| Workflow | Trigger | What it does |
| --- | --- | --- |
| `pr-copilot-review.yml` | `pull_request_target`: opened, reopened, ready_for_review, synchronize | Removes every other reviewer and requests `copilot-pull-request-reviewer[bot]`. Drops stale `reviewed` / `ready-to-merge` labels when the head commit moves. |
| `pr-reviewed.yml` | `pull_request_review`: submitted (by Copilot) | Labels the PR `reviewed` and approves the CI runs sitting in `action_required`. |
| `pr-auto-merge.yml` | `pull_request_review`, `workflow_run` on CI / CI Pipeline / Dependency compatibility, `pull_request_target`: labeled | Relabels `ready-to-merge` and squash-merges once Copilot has reviewed the current head and every CI check is green. |
| `close-parent-issues.yml` | `workflow_call` from `pr-auto-merge`, plus `pull_request_target`: closed | Walks up from each issue the PR closed and closes every ancestor whose sub-issues are now all closed: ticket -> spec -> origin issue. |

Every workflow also takes a `workflow_dispatch` with a `pr_number`, so any step
can be replayed by hand when something goes sideways.

## Prerequisites

- **`COPILOT_ASSIGN_TOKEN` needs the `repo` scope.** Approving a pending
  workflow run is a maintainer action; the default `GITHUB_TOKEN` acts as
  `github-actions[bot]` and is rejected. Without it, CI on Copilot coding agent
  PRs stays in `action_required` forever and nothing merges. The same secret
  already backs `assign-copilot.yml`.
- **Copilot code review must be enabled** for the repository, with AI credits
  budget remaining. `pr-copilot-review` fails loudly when the request is
  refused.
- **The workflows must be on `main`.** `pull_request_target`, `workflow_run` and
  `workflow_call` all resolve against the default branch, so none of this runs
  from a feature branch.

## Why the triggers look the way they do

Three GitHub recursion guards shape the whole design, and changing a trigger
without accounting for them silently breaks the chain:

- A label written with `GITHUB_TOKEN` does **not** raise a `labeled` event. So a
  label can never be the thing that advances the chain — it is an annotation for
  humans, not a signal.
- `check_suite: completed` raised by Actions does **not** trigger workflows.
  `workflow_run: completed` is the supported way to react to CI finishing.
- A merge performed with `GITHUB_TOKEN` does **not** raise `pull_request:
  closed`. That is why `pr-auto-merge` calls `close-parent-issues` through
  `workflow_call` rather than relying on the event; the event trigger only
  covers merges a human performs.

Two further consequences worth keeping in mind:

- **The merge gate is "Copilot reviewed this exact head commit", not "the PR
  carries the `reviewed` label".** The label lags, because it is written by
  `GITHUB_TOKEN` after the fact; the review's `commit.oid` does not. This also
  closes the hole where a push after review would otherwise merge unreviewed.
- **`pull_request_target`, not `pull_request`.** Runs for Copilot coding agent
  PRs land in `action_required`, and a gated workflow cannot be the thing that
  ungates the rest. `pull_request_target` runs in the base-branch context and is
  never gated. Nothing in these workflows checks out or executes PR code, which
  is what makes that safe — **keep it that way**.
- **`pr-auto-merge` excludes its own check runs** from the "all green" test by
  workflow name. Rename any of these four workflows and you must update the
  `ignore` list in `pr-auto-merge.yml`, or the gate will wait on itself forever.

## Labels

| Label | Applied by | Meaning |
| --- | --- | --- |
| `reviewed` | `pr-reviewed` | Copilot has submitted a review. |
| `ready-to-merge` | `pr-auto-merge` | Reviewed and green; being merged. Applying it by hand overrides the review gate. |

Both are removed by `pr-copilot-review` when a new commit invalidates them.
