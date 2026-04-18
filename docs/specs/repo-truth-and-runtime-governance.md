# Repo Truth and Runtime Governance

Status: Draft baseline-governance rail
Date: 2026-04-18
Owner: Merlin

## Purpose

Prevent Hermes from drifting back into the state we just corrected: live runtime behavior that is real in production or in a blessed deploy lane, but not honestly represented in repo history.

## Core rule

**A live runtime is not allowed to become the long-term source of truth. Repo history is the source of truth.**

If a deploy lane or runtime worktree contains behavior that matters, that behavior must be promoted into a reviewable repo branch and verified there before the workstream is considered closed.

## What counts as a governance failure

Any of the following should be treated as a governance miss:

1. The running gateway depends on code that only exists in a sandbox or deploy lane.
2. A feature is described as landed, but the corresponding repo branch/commit does not contain the actual live behavior.
3. Verification only exists in the live lane and is not re-run in the repo candidate lane.
4. `CURRENT.md`, `.hermes/`, local plans, caches, or environment directories are treated as canonical source artifacts.
5. A future feature starts from stale repo state when the actual runtime truth lives elsewhere.

## Required closure bar for runtime-affecting changes

Before any runtime-affecting lane is considered truly closed:

1. **Promotion path exists**
   - The code lives on a reviewable repo branch or worktree rooted from the canonical repo.

2. **Ephemeral junk stays out**
   - Do not commit `.venv/`, `.pytest_cache/`, `.hermes/`, `CURRENT.md`, local scratch handoffs, or other runtime-only state.

3. **Verification is repeated in the repo candidate lane**
   - Re-run the focused suite in the repo candidate branch/worktree, not only in the live/deploy lane.

4. **Runtime/source distinction is explicit**
   - If the running gateway is executing from a non-canonical lane, say so plainly until reconciliation is complete.

5. **Commit exists before closure language**
   - Do not call the work “landed,” “baseline,” or “canonical” unless there is an actual commit carrying the promoted state.

## Approved exception path

If live reality diverges from repo truth badly enough that ordinary incremental sync would be dishonest, one bounded reset is allowed:

1. Freeze the actual working live lane.
2. Create a dedicated reset branch/worktree from the canonical repo.
3. Promote the live source tree into that lane, excluding ephemeral/runtime-only artifacts.
4. Re-run focused verification in the reset lane.
5. Preserve the result as a commit.
6. Treat that commit as the new baseline candidate.

This is a mulligan, not a recurring workflow.

## Operational checklist for future Hermes runtime work

Use this checklist before closing any runtime-adjacent lane:

- What path is the gateway actually running from?
- Does repo history contain that behavior yet?
- Did verification run in the repo candidate lane?
- Are the promoted files source artifacts rather than runtime artifacts?
- Is the final claimed baseline represented by a commit?

If any answer is no, the lane is not closed.

## Current baseline reset reference

The 2026-04-18 mulligan baseline candidate was preserved on:
- branch: `chore/hermes-repo-baseline-reset`
- commit: `5052d2ad`
- message: `chore: reset repo baseline to verified live runtime`

That snapshot is the template for how to recover from drift honestly, not permission to let drift happen again.
