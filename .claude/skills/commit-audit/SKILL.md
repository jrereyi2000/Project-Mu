---
name: commit-audit
description: Run the report-only commit auditor (tools/commit_audit.py) now on a range of main, show its status, or read the latest audit report. Use for "/commit-audit", "audit the last commits", "what did the commit audit find".
---

# Commit audit

Report-only reviewer for commits on `main` (docs/reference/COMMIT_AUDIT.md). Never edit files or
fix findings from here unless the user asks; just run it and relay the report.

Run everything from the repo root with the venv's python (`python3` when the venv is active).

- **Status** (no argument or `status`): `python3 -m tools.commit_audit status` — last audited sha,
  pending commits, whether a worker is running, latest report, hooks installed.
- **Latest report** (`report` / `latest`): read the newest `data/commit_audits/*.md` of the main
  worktree (path printed by `status`) and summarize it: range, fsck, test summary, each finding.
- **Audit a range now** (`A..B`, a sha, or `last N`):
  `python3 -m tools.commit_audit run --range A..B --no-notify` (for `last N` use `main~N..main`).
  Runs in the foreground (unit tests can take several minutes; the review up to 20 minutes), so run
  it in the background and wait. It doesn't move the last-audited sha.
- **Catch up now** (`now`): `python3 -m tools.commit_audit run --now` — audits last-audited..main
  immediately and updates the state (exits at once if a worker already holds the lock).
- Flags: `--no-tests`, `--no-review`, `--test-timeout S`, `--review-timeout S`.
- Install/remove hooks only when asked: `python3 -m tools.commit_audit install|uninstall`.

When done, give the report path and its one-line summary; list findings with `file:line`.
