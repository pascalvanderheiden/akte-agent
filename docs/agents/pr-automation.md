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

The `reviewed` step approves workflow runs because GitHub can hold them in
`action_required` until a maintainer clicks **Approve workflows to run**, and a
held run blocks the merge gate indefinitely.

Two things about that mechanism are worth knowing before changing it, because
both were established the hard way here:

- **`POST /actions/runs/{id}/approve` is narrower than it looks.** It accepts
  runs from fork pull requests and runs queued by the Actions bot. Anything else
  comes back `403 This run is not from a fork pull request or queued by the
  Actions bot` — including for a repository admin holding a `repo`-scoped token.
  So a 403 carrying that message is not a permissions problem and no token will
  fix it. Both workflows treat it as "nothing to do" and carry on.
- **Copilot coding agent `pull_request` runs are not held.** They execute
  normally. The runs that did pile up in `action_required` here were
  base-context `workflow_run` runs on `main`, which have no pull request to
  gate, cannot be approved, and only existed because post-merge CI was spawning
  them. `pr-auto-merge` now carries `branches-ignore: [main]` so they are never
  created.

`approve-gated-runs` is the backstop. `pr-reviewed` only sees the runs that
exist at the moment the review lands, so anything queued afterwards — a re-run,
a slow check suite — would otherwise sit there with no event to release it. The
sweeper runs on a schedule, whose actor is the last person to touch the workflow
file, and it looks only at the head commits of open pull requests. Runs on
branches with no open PR are neither approvable nor blocking, so sweeping them
would only generate noise.

If you would rather remove the gating at source than work around it, the policy
is settable:

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
