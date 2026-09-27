# PR automation

Four plain GitHub Actions carry a pull request from "opened" to "merged, with
every completed parent issue closed", and a fifth keeps them unblocked. None of
them needs an LLM, so none is an agentic workflow.

```
pr-copilot-review  ->  pr-reviewed  ->  pr-auto-merge  ->  close-parent-issues
                    approve-gated-runs (schedule, safety net)
```

| Workflow | Trigger | What it does |
| --- | --- | --- |
| `pr-copilot-review.yml` | `pull_request_target`: opened, reopened, ready_for_review, synchronize | Removes every other reviewer and requests `copilot-pull-request-reviewer[bot]`. Drops stale `reviewed` / `ready-to-merge` labels when the head commit moves. |
| `pr-reviewed.yml` | `pull_request_review`: submitted (by Copilot) | Labels the PR `reviewed` and approves the CI runs sitting in `action_required`. |
| `pr-auto-merge.yml` | `pull_request_review`, `workflow_run` on CI / CI Pipeline / Dependency compatibility, `pull_request_target`: labeled | Relabels `ready-to-merge` and squash-merges once Copilot has reviewed the current head and every CI check is green. |
| `close-parent-issues.yml` | `workflow_call` from `pr-auto-merge`, plus `pull_request_target`: closed | Walks up from each issue the PR closed and closes every ancestor whose sub-issues are now all closed: ticket -> spec -> origin issue. |
| `approve-gated-runs.yml` | `schedule`, every 10 minutes | Approves every run sitting in `action_required` on a same-repo branch, including gated runs of the workflows above. See "Approval gating" below. |

Every workflow also takes a `workflow_dispatch` with a `pr_number`, so any step
can be replayed by hand when something goes sideways.

## Prerequisites

- **`COPILOT_ASSIGN_TOKEN` needs the `repo` scope.** Approving a pending
  workflow run is a maintainer action; the default `GITHUB_TOKEN` acts as
  `github-actions[bot]` and is rejected. Both `pr-reviewed` and
  `approve-gated-runs` depend on it. Without it, runs on Copilot coding agent
  PRs stay in `action_required` forever and nothing merges. The same secret
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
- **`pull_request_target`, not `pull_request`.** These workflows must keep
  working on Copilot coding agent PRs, and `pull_request_target` runs in the
  base-branch context. Nothing here checks out or executes PR code, which is
  what makes that safe — **keep it that way**.
- **`pr-auto-merge` excludes its own check runs** from the "all green" test by
  workflow name. Rename any of these four workflows and you must update the
  `ignore` list in `pr-auto-merge.yml`, or the gate will wait on itself forever.

## Approval gating

GitHub gates workflow runs by **actor**, and the Copilot coding agent counts as
a first-time contributor under this repository's
`fork-pr-contributor-approval: first_time_contributors` policy. Every run it
causes therefore lands in `action_required` and waits for a human.

This is not limited to events that execute PR code. It was confirmed here: at a
Copilot-authored commit the `workflow_run` children of `pr-auto-merge` came back
`action_required`, while at a maintainer-authored commit the identical workflow
ran normally — even though `workflow_run` always executes in the base context.

That matters because **a gated workflow cannot ungate itself**, so the chain
would stall on exactly the pull requests it exists to handle. Two things address
it:

- `pr-reviewed` approves whatever is pending when the review lands — the fast
  path, but it only sees runs that exist at that moment.
- `approve-gated-runs` sweeps on a schedule. A scheduled run's actor is the last
  person to touch the workflow file, never Copilot, so it is the one trigger
  guaranteed never to be gated.

The sweeper only releases runs whose head repository is this repository. Runs
from forks stay gated deliberately — releasing those automatically would discard
the protection the gate exists for, and this repository is public.

If you would rather remove the gating than work around it, change the policy:

```bash
gh api --method PUT repos/{owner}/{repo}/actions/permissions/fork-pr-contributor-approval \
  -f approval_policy=...
```

That is a security decision about fork pull requests on a public repository, so
it is deliberately not automated here.

## Labels

| Label | Applied by | Meaning |
| --- | --- | --- |
| `reviewed` | `pr-reviewed` | Copilot has submitted a review. |
| `ready-to-merge` | `pr-auto-merge` | Reviewed and green; being merged. Applying it by hand overrides the review gate. |

Both are removed by `pr-copilot-review` when a new commit invalidates them.
