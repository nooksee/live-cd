# LiveUSB Creator agent instructions

## Repository workflow

- `main` is the integration-only branch. Read-only inspection may occur on
  `main`, but project files must not be changed there.
- Discussion, read-only investigation, and exact scope-setting may occur on
  `main`. After Kevin Thomas and the responsible agent lock the bounded work
  slice, that agent owns the complete repository lifecycle through branch
  creation, implementation, validation, commit, push, pull request, merge,
  branch cleanup, synchronization, pruning, and final verification.
- Before the first project write in every bounded work slice, verify that
  `main` is clean and synchronized with `origin/main`, then create a fresh
  branch named `agent/<concise-kebab-case-scope>-YYYY-MM-DD`.
- The branch date uses the `America/New_York` calendar date. A phase, task, or
  issue identifier may be included at the beginning of the scope when useful.
- One branch owns one bounded slice. A merged branch must not be reused for a
  later slice.
- Direct commits and direct pushes to `main` are prohibited. Integrate through
  a pull request after the bounded validation gates pass.
- After merge, return the working checkout to `main`, synchronize and prune
  remote references, delete the completed branch locally and remotely, and
  verify that `main` is clean with ahead/behind counts of `0/0`.
- If the starting checkout is dirty or `main` has diverged, preserve the
  evidence and resolve ownership before branching or writing. Do not absorb
  unrelated changes into a new work slice.

Example:

```text
agent/phase1e-b2-capacity-plan-2026-08-04
```
