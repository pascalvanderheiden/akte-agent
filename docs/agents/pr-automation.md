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
| `pr-copilot-review.yml` | `pull_request_target`: opened, reopened, ready_for_review, synchronize | Removes every other reviewer (including on drafts) and requests `copilot-pull-request-reviewer[bot]` once the PR is ready. Drops stale `reviewed` / `ready-to-merge` labels when the head commit moves. |
| `pr-reviewed.yml` | `pull_request_review`: submitted (by Copilot) | Labels the PR `reviewed` and approves the CI runs sitting in `action_required`. |
| `pr-auto-merge.yml` | `pull_request_review`, `workflow_run` on CI / CI Pipeline / Dependency compatibility, `pull_request_target`: labeled | Relabels `ready-to-merge` and squash-merges once Copilot has reviewed the current head and every CI check is green. |
| `close-parent-issues.yml` | `workflow_call` from `pr-auto-merge`, plus `pull_request_target`: closed | Walks up from each issue the PR closed and closes every ancestor whose sub-issues are now all closed: ticket -> spec -> origin issue. |
| `approve-gated-runs.yml` | `schedule`, every 10 minutes | Approves runs left in `action_required` on the head commit of an open PR, catching anything queued after the review landed. See "Approval gating" below. |

Every workflow also takes a `workflow_dispatch` with a `pr_number`, so any step
can be replayed by hand when something goes sideways.

## Prerequisites

- **`COPILOT_ASSIGN_TOKEN` needs the `repo` scope.** Approving a pending
  workflow run is a maintainer action; the default `GITHUB_TOKEN` acts as
  `github-actions[bot]` and is rejected. Both `pr-reviewed` and
  `approve-gated-runs` use it. The same secret already backs
  `assign-copilot.yml`.
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

**This is the one thing that stops the chain running unattended, and it cannot
be fixed from inside a workflow.**

GitHub holds workflow runs on the Copilot coding agent's pull requests in
`action_required` until someone with write access clicks **Approve workflows to
run**. Every trigger is affected — `pull_request` CI *and* the
`pull_request_target` workflows here — even though the branches are in this
repository and not a fork:

```
CI              | pull_request        | completed/action_required | copilot/...
PR review by Copilot | pull_request_target | completed/action_required | copilot/...
```

And the obvious escape hatch is closed:
`POST /actions/runs/{id}/approve` only accepts runs from **fork** pull requests
and runs queued by the Actions bot. For these runs it returns
`403 This run is not from a fork pull request or queued by the Actions bot` —
and it returns that to a repository **admin** holding a `repo`-scoped token, so
it is not a permissions problem and no secret will fix it.

The practical consequences:

- The first approval on each Copilot PR is manual. Once approved, later pushes
  to that same PR run automatically.
- `approve-gated-runs` and the approve step in `pr-reviewed` still handle the
  cases the API *does* accept, and treat that specific 403 as nothing-to-do so
  they never fail the chain over something they cannot change.

The only way to remove the gate is to loosen the repository policy, which has
exactly three values and no "off":

```bash
gh api repos/{owner}/{repo}/actions/permissions/fork-pr-contributor-approval
# first_time_contributors_new_to_github | first_time_contributors | all_external_contributors
```

That is a security decision about fork pull requests on a public repository, so
it is deliberately not automated here.

## Draft pull requests

The coding agent opens its PR as a draft, pushes to it for a while, and asks the
person who delegated the task to review it. Two GitHub behaviours shape how
`pr-copilot-review` handles that:

- **Requesting a review on a draft silently fails.** The API answers `200 OK`
  and the reviewer simply does not appear. So the workflow verifies the reviewer
  afterwards rather than trusting the status code, and does not bother asking
  until the PR is ready.
- **Removing a reviewer from a draft works normally.** So the human review
  request is cleared as soon as the PR is opened, rather than sitting in
  someone's queue for however long the draft lasts.

Pushes to a draft are ignored — there is no review to refresh yet, and the
reviewer cleanup already happened when the PR was opened. `ready_for_review`
brings the PR back to request the review.

## Labels

| Label | Applied by | Meaning |
| --- | --- | --- |
| `reviewed` | `pr-reviewed` | Copilot has submitted a review. |
| `ready-to-merge` | `pr-auto-merge` | Reviewed and green; being merged. Applying it by hand overrides the review gate. |

Both are removed by `pr-copilot-review` when a new commit invalidates them.
