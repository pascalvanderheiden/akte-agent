# PR automation

Six plain GitHub Actions carry a pull request from "opened" to "merged, with
every completed parent issue closed". None of them needs an LLM, so none is an
agentic workflow — where reasoning is genuinely required, the work is handed to
the Copilot coding agent rather than done here.

```
pr-copilot-review  ->  pr-reviewed  ->  pr-auto-merge  ->  close-parent-issues
                            |               ^
                            v               |
                     pr-address-review ------+  (loops back via the coding agent)
                    approve-gated-runs (safety net)
```

| Workflow | Trigger | What it does |
| --- | --- | --- |
| `pr-copilot-review.yml` | `pull_request_target`: opened, reopened, ready_for_review, synchronize | Removes every other reviewer (including on drafts) and requests `copilot-pull-request-reviewer[bot]` once the PR is ready. Drops stale `reviewed` / `ready-to-merge` / `needs-fixes` labels when the head commit moves. |
| `pr-reviewed.yml` | `pull_request_review`: submitted (by Copilot) | Labels the PR `reviewed` and releases the CI runs sitting in `action_required`. **Does not fire on its own** — see "Copilot's review raises no event". |
| `pr-address-review.yml` | `pull_request_review`: submitted, `pull_request_target`: ready_for_review, synchronize | When Copilot's review of the current head left unresolved comments, asks the coding agent to fix them. See "Reviews that are not approvals". |
| `pr-auto-merge.yml` | `pull_request_review`, `workflow_run` on CI / CI Pipeline / Dependency compatibility, `pull_request_target`: labeled, `schedule` every 10 minutes | Relabels `ready-to-merge`, takes the PR out of draft if it still is one, and squash-merges once Copilot has reviewed the current head, left no unresolved comments, and every CI check is green. |
| `close-parent-issues.yml` | `workflow_call` from `pr-auto-merge`, plus `pull_request_target`: closed | Walks up from each issue the PR closed and closes every ancestor whose sub-issues are now all closed: ticket -> spec -> origin issue. |
| `approve-gated-runs.yml` | `schedule`, every 10 minutes, plus `workflow_dispatch` | Safety net. Takes any non-Copilot reviewer back out of the queue, applies the `reviewed` label, and releases held runs. **Its schedule does not fire reliably** — see "The schedule trigger is unreliable". |

Every workflow also takes a `workflow_dispatch` with a `pr_number`, so any step
can be replayed by hand when something goes sideways.

## Prerequisites

- **The Copilot workflow-approval gate must be off**, under Settings → Copilot →
  Cloud agent → Actions workflow approval → *Require approval for workflow
  runs*. Leave it on and every run on a Copilot PR is held in `action_required`,
  which stalls the whole chain. This is the single most important setting here;
  see "Approval gating" for why nothing can work around it. Repository admin
  only, and there is no API for it.
- **`GITHUB_TOKEN` with `actions: write` releases held runs.** Not a PAT. The
  repo's fine-grained `COPILOT_ASSIGN_TOKEN` has no Actions permission and
  fails with `Resource not accessible by personal access token`; a classic
  `repo`-scoped PAT is refused too, for a different reason (see below).
  `COPILOT_ASSIGN_TOKEN` is still used by `pr-copilot-review` and
  `assign-copilot.yml` for pull-request writes.
- **`COPILOT_ASSIGN_TOKEN` needs `Issues: Read and write`**, not just `Pull
  requests`. A pull-request comment is an issue comment to both REST and
  GraphQL, so `pr-address-review` cannot post its `@copilot` delegation without
  it — both transports answer `Resource not accessible by personal access
  token`. Falling back to `GITHUB_TOKEN` is not a fix: the comment posts, and
  wakes nothing.
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

**This is a Copilot-specific gate, not the fork one.** That distinction cost a
lot of time here, so it is worth stating plainly. There are two separate
approval mechanisms and they look identical from the outside:

| | Fork gate | Copilot cloud agent gate |
|---|---|---|
| Applies to | pull requests from forks | anything Copilot pushes, same-repo included |
| Setting | `actions/permissions/fork-pr-contributor-approval` | Settings → Copilot → Cloud agent → **Actions workflow approval** |
| REST API | yes | **none — UI only** |
| Released by | `POST /actions/runs/{id}/approve` | nothing; no approve endpoint exists |

Because these runs are held by the *second* mechanism,
`POST /actions/runs/{id}/approve` returns
`403 This run is not from a fork pull request or queued by the Actions bot` —
and it returns that to a repository **admin** holding a `repo`-scoped token. It
is not a permissions problem and no secret will fix it; it is simply the wrong
endpoint for this hold. Tightening `fork-pr-contributor-approval` does nothing
either, for the same reason.

**The fix is to turn the Copilot gate off**, under Settings → Copilot → Cloud
agent → Actions workflow approval → *Require approval for workflow runs*. That
requires repository admin, has no API, and carries a real trade-off that GitHub
states directly: unreviewed Copilot code may then run with write access to the
repository and access to Actions secrets. With it off, every workflow here runs
on its own events and the chain needs no intervention.

**If you leave the gate on, re-running is the only release.**
`POST /actions/runs/{id}/rerun` re-queues the jobs under the actor that
triggered the re-run rather than under Copilot, so the hold is not applied a
second time. Both `pr-reviewed` and `approve-gated-runs` try approve first and
fall back to rerun; neither treats a failure as fatal, because the next sweep
retries anyway. But this only works if something drives the sweep — see below.

## The schedule trigger is unreliable

`approve-gated-runs` is scheduled `*/10`. That cadence is fiction. The schedule
was once observed producing **zero** runs in this repository; it does fire now,
but heavily throttled — five runs in the twelve hours to 2026-09-28T02:48Z, so
roughly one every two to three hours against a requested six per hour, then a
four-hour gap. `workflow_dispatch` runs of the same file succeed immediately.
This is consistent with GitHub's own documented behaviour: scheduled runs are
best-effort, and *"if the load is sufficiently high enough, some queued jobs may
be dropped"*.

The consequence is that **no sweeper may be load-bearing for latency**. A
schedule is fine as a backstop that eventually catches a stalled PR, and useless
as the thing that makes a PR merge promptly. `pr-auto-merge` is built that way
on purpose: the events are the fast path and the sweep only exists for the
ordering no event covers.

Both sweepers can be driven by hand, and `pr-auto-merge` takes an empty
`pr_number` to sweep every open PR — which is also the only way to exercise the
scheduled code path without waiting for a tick:

```bash
gh workflow run approve-gated-runs.yml
gh workflow run pr-auto-merge.yml          # sweep every open PR
gh workflow run pr-auto-merge.yml -f pr_number=123   # just one
```

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

`pr-auto-merge`'s **gate** was already immune to this, because it asks "did
Copilot review this exact head commit", not "does the PR carry the `reviewed`
label". Its **triggers** were not. With no `pull_request_review` event, the only
event left was `workflow_run` on CI — so a review landing *after* CI had already
finished fired nothing at all, and the PR sat approved and green indefinitely.
PR #85 did exactly that: CI green at 12:13, review approved at 15:08, no further
run, no merge.

That is why `pr-auto-merge` now also runs on a ten-minute `schedule`. The sweep
re-evaluates every open PR against the identical gate and merges at most one per
run. It is the backstop; the events remain the fast path.

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
- **Copilot's automatic code review runs on drafts anyway.** So a draft can be
  approved, green, and fully qualified while still being unmergeable, because
  `gh pr merge` refuses a draft outright.

That last point used to strand PRs permanently. The coding agent does not
reliably take its own PR out of draft — PRs #79–#82 merged, then #83–#87 all
stalled as approved drafts — and nothing else in the chain un-drafts. So
`pr-auto-merge` no longer treats draft as disqualifying: it evaluates the PR
normally and, only once every other gate has passed, calls `gh pr ready` just
before merging. A PR that fails a gate is left exactly as the agent had it.

`pr-copilot-review` deliberately runs on a draft's pushes too. An earlier
version skipped `synchronize` while draft, reasoning that the cleanup had
already happened at `opened`. It had not, and the failure mode was the one that
prompted this whole document: the coding agent adds the reviewer partway through
drafting, the `opened` run is held in `action_required`, and so the only run
that would ever have reached the cleanup was a `synchronize` one — the run being
skipped. A human then sat in the review queue for hours. `approve-gated-runs`
strips foreign reviewers as well, so the relief does not depend on a held run
being released.

## Reviews that are not approvals

Copilot's review comes back `APPROVED` when it is happy and `COMMENTED` when it
is not. `pr-auto-merge` originally asked only "did Copilot review this head",
never "what did it say", so a PR with open review comments merged anyway. It now
also requires that no review thread is left unresolved.

That gate alone would just stall the PR forever, because the thing that clears a
review comment is a code change, and nothing was making one:

- The coding agent does pick up review feedback and push fixes — but only from
  **humans**. A review by `copilot-pull-request-reviewer[bot]` is bot-to-bot and
  does not wake it.
- So the PR sits `reviewed`, unmergeable, with nobody assigned to the comments.

`pr-address-review` closes that loop. When Copilot has reviewed the current head
and left unresolved comments, it posts an `@copilot` comment listing them, which
does wake the coding agent. The agent pushes a fix, `pr-copilot-review` drops the
stale labels and re-requests review, and the cycle repeats until the review comes
back clean.

Three things keep that loop from running away:

- **Only live comments count.** A thread is ignored once it is resolved, and once
  it is `isOutdated` — outdated means the lines it was anchored to are gone, so
  the comment is about code that no longer exists and would never clear.
- **One delegation per head commit.** The comment it posts carries a
  `<!-- pr-address-review: <sha> -->` marker, and the workflow exits if that
  marker is already present. Re-runs and duplicate triggers are free.
- **A round cap.** After `MAX_ROUNDS` (3) delegations on one PR it stops, applies
  `needs-human`, and leaves it alone. Three failed attempts means the review
  comment needs a person, not another lap.

It must post with `COPILOT_ASSIGN_TOKEN`. An `@copilot` mention written with
`GITHUB_TOKEN` hits the same recursion guard as everything else here and wakes
nothing.

Unlike the rest of the chain, this one runs on drafts, because that is exactly
where the coding agent's PRs spend their review cycles.

## Labels

| Label | Applied by | Meaning |
| --- | --- | --- |
| `reviewed` | `pr-reviewed` | Copilot has submitted a review. |
| `needs-fixes` | `pr-address-review` | The review left unresolved comments; the coding agent has been asked to fix them. |
| `needs-human` | `pr-address-review` | `MAX_ROUNDS` delegations did not clear the comments. Automation has stopped; a person needs to look. |
| `ready-to-merge` | `pr-auto-merge` | Reviewed, green and no open comments; being merged. Applying it by hand overrides both the review gate and the unresolved-comments gate. |

`reviewed`, `ready-to-merge` and `needs-fixes` are removed by
`pr-copilot-review` when a new commit invalidates them. `needs-human` is not —
it is deliberately sticky, and has to be taken off by hand.
