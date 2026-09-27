# PR automation

Five plain GitHub Actions carry a pull request from "opened" to "merged, with
every completed parent issue closed". None of them needs an LLM, so none is an
agentic workflow.

```
pr-copilot-review  ->  pr-reviewed  ->  pr-auto-merge  ->  close-parent-issues
                    approve-gated-runs (schedule, drives the chain)
```

| Workflow | Trigger | What it does |
| --- | --- | --- |
| `pr-copilot-review.yml` | `pull_request_target`: opened, reopened, ready_for_review, synchronize | Removes every other reviewer (including on drafts) and requests `copilot-pull-request-reviewer[bot]` once the PR is ready. Drops stale `reviewed` / `ready-to-merge` labels when the head commit moves. |
| `pr-reviewed.yml` | `pull_request_review`: submitted (by Copilot) | Labels the PR `reviewed` and releases the CI runs sitting in `action_required`. **Does not fire on its own** — see "Copilot's review raises no event". |
| `pr-auto-merge.yml` | `pull_request_review`, `workflow_run` on CI / CI Pipeline / Dependency compatibility, `pull_request_target`: labeled | Relabels `ready-to-merge` and squash-merges once Copilot has reviewed the current head and every CI check is green. |
| `close-parent-issues.yml` | `workflow_call` from `pr-auto-merge`, plus `pull_request_target`: closed | Walks up from each issue the PR closed and closes every ancestor whose sub-issues are now all closed: ticket -> spec -> origin issue. |
| `approve-gated-runs.yml` | `schedule`, every 10 minutes | **Load-bearing, not a safety net.** Applies the `reviewed` label, and releases held runs — both `pull_request` CI on an open PR head and this chain's own held `workflow_run` runs. See "Approval gating". |

Every workflow also takes a `workflow_dispatch` with a `pr_number`, so any step
can be replayed by hand when something goes sideways.

## Prerequisites

- **`GITHUB_TOKEN` with `actions: write` releases held runs.** Not a PAT. The
  repo's fine-grained `COPILOT_ASSIGN_TOKEN` has no Actions permission and
  fails with `Resource not accessible by personal access token`; a classic
  `repo`-scoped PAT is refused too, for a different reason (see below).
  `COPILOT_ASSIGN_TOKEN` is still used by `pr-copilot-review` and
  `assign-copilot.yml` for pull-request writes.
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

**Re-running the run does work.** `POST /actions/runs/{id}/rerun` re-queues the
jobs under the actor that triggered the re-run rather than under Copilot, so the
approval policy does not hold them a second time. Both `pr-reviewed` and
`approve-gated-runs` try approve first and fall back to rerun; neither treats a
failure as fatal, because the next sweep retries anyway.

This repository's policy is set to the narrowest value:

```bash
gh api repos/{owner}/{repo}/actions/permissions/fork-pr-contributor-approval
# first_time_contributors_new_to_github | first_time_contributors | all_external_contributors
```

There is no "off" value — it is a security setting about fork pull requests on a
public repository. With `first_time_contributors_new_to_github`, fresh
`pull_request` and `pull_request_target` runs on Copilot branches are no longer
held. **`workflow_run` runs still are**, which matters because `pr-auto-merge`
is triggered that way.

Two details about held `workflow_run` runs, both learned the hard way:

- They report the **default branch** SHA, not the PR head, so any sweep that
  matches held runs against open PR heads will silently skip them. The sweeper
  matches them on event type instead.
- Because of that, the sweeper is load-bearing: without it, every PR stalls
  fully reviewed and fully green but unmerged.

## Copilot's review raises no event

Copilot's automatic code review does **not** raise a workflow-triggering
`pull_request_review` event. Observed on two consecutive PRs: the review was
submitted, and no run of `pr-reviewed` was created at all — not a held run, no
run. So `pull_request_review` cannot be relied on as the entry point, and
`pr-reviewed` in practice only ever runs via `workflow_dispatch`.

`approve-gated-runs` therefore reconciles the `reviewed` label itself on every
sweep. It keys the label to the reviewed **commit**, not to the PR, so a push
landing after a review does not leave a stale label behind.

`pr-auto-merge` was already immune to this, because its gate is "Copilot
reviewed this exact head commit", not "the PR carries the `reviewed` label".

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
