# Commit audit

`tools/commit_audit.py` is a report-only reviewer for commits that land on `main`. Several Claude
sessions commit and merge to `main` concurrently, so this is a post-hoc audit, not a gate: it
never blocks a commit, edits a working tree, moves a ref or commits.

## How it works

1. **Hook.** `tools/git-hooks/post-commit` is installed as both `post-commit` and `post-merge` in
   `<git-common-dir>/hooks` (merges into `main`, including fast-forwards, fire `post-merge`, not
   `post-commit`). Every worktree shares that folder. The hook exits unless the current branch is
   `main`, then runs `python3 -m tools.commit_audit hook`, which spawns a detached `run` worker
   (new session, `GIT_*` env stripped, output to `<common>/commit_audit/worker.log`) and returns.
   Python is `$MU_PYTHON`, else the main worktree's `.venv/bin/python3`, else `python3`.
2. **Debounce.** The worker waits until `refs/heads/main` hasn't moved for `--debounce` seconds
   (default 90, `MU_AUDIT_DEBOUNCE`), so a burst of commits becomes one batch and nothing is read
   while a commit is still being written. Main's last move comes from its reflog.
3. **Lock.** A non-blocking `flock` on `<common>/commit_audit/lock` (holds the pid). A second
   worker exits at once. After releasing the lock the worker re-checks `main` and loops if a
   commit landed meanwhile, so a hook that lost the race doesn't leave commits unaudited.
4. **Range.** `last_audited..main` from `<common>/commit_audit/state.json`; with no state (or a
   last-audited sha that is no longer an ancestor of main) just `HEAD~1..HEAD`.
5. **Checks**, all read-only against the main worktree (`git worktree list`'s first entry):
   - `git fsck --connectivity-only` (retried once after 30 s). If it still fails the report says
     so, tests and review are skipped, and the state isn't advanced.
   - Unit tests: `pytest -m "not integration" -q -p no:cacheprovider --continue-on-collection-errors -rfE`
     on a `git archive` snapshot of main's head in a temp dir (exactly the committed tree, not
     anyone's uncommitted work), timeout `--test-timeout` (900 s, `MU_AUDIT_TEST_TIMEOUT`; the
     unit tier takes ~25 s). Untracked inputs some tests read (`data/*` entries the snapshot lacks,
     `pipeline/save_reader/node_modules`) are symlinked in from the main worktree
     (`SNAPSHOT_LINKS`). Failing test ids are re-run on a snapshot of the range's base; those that
     also fail there are listed as pre-existing (status `passed (pre-existing failures)`), the rest
     as new. On 2026-10-30, 12 unit tests already failed on main (macOS-only `Quartz` imports, OCR
     and injury-classifier tests), so only new failures make the notice high priority.
   - Headless Claude review of the range (below), timeout `--review-timeout` (1200 s).
6. **Report** `data/commit_audits/<YYYY-MM-DD_HHMMSS>_<base7>..<head7>.md` in the main worktree
   (gitignored by `data/*`; `MU_AUDIT_REPORT_DIR` overrides), then one ntfy notice through
   `pipeline.notify` (`NTFY_TOPIC` / `NTFY_SERVER`; high priority when fsck/tests fail or the
   review has findings). No topic: nothing is sent.

## Install

```bash
python3 -m tools.commit_audit install     # refuses if a post-commit/post-merge hook that isn't ours exists
python3 -m tools.commit_audit status      # last audited sha, pending commits, worker, latest report, hooks
python3 -m tools.commit_audit uninstall   # removes only hooks carrying the mu-commit-audit marker
```

The hook is copied (not symlinked), so re-run `install` after changing `tools/git-hooks/post-commit`.
`core.hooksPath` isn't used; `install` warns if it is set (git would then ignore the folder).

Manual runs: `run --now` (catch up immediately, updates state), `run --range A..B` (no lock, no
debounce, state untouched), `--no-tests`, `--no-review`, `--no-notify`. Or `/commit-audit`.

## The claude binary

Resolved in order: `MU_CLAUDE_BIN` (a path or a command line, split with shlex, e.g.
`MU_CLAUDE_BIN="npx -y @anthropic-ai/claude-code"`), `claude` on `PATH`, then
`npx -y @anthropic-ai/claude-code` (probed with `--version`; the first run downloads it). If none
works the report says "AI review was skipped" and why; fsck and tests still run. The CLI must be
logged in (or have `ANTHROPIC_API_KEY`) for the user the hook runs as; a failed run is reported
with its exit code and stderr.

Headless invocation, run with cwd = main worktree and stdin closed:

```
claude -p "<explicit prompt: review base..head for correctness bugs, report-only, never edit>" \
  --permission-mode dontAsk \
  --allowedTools Read Grep Glob "Bash(git log:*)" "Bash(git show:*)" "Bash(git diff:*)" \
                 "Bash(git blame:*)" "Bash(git rev-parse:*)" "Bash(git ls-files:*)" "Bash(git cat-file:*)" \
  --disallowedTools Edit Write NotebookEdit \
  --output-format text \
  --tools Read,Grep,Glob,Bash --permission-prompts none --no-session-persistence
```

`dontAsk` denies every tool use not pre-approved by `--allowedTools`; `--tools` removes the edit
tools entirely. The last three flags are added only when the CLI's `--help` lists them (they exist
in 2.1.314). Optional: `MU_AUDIT_CLAUDE_MODEL` (`--model`), `MU_AUDIT_MAX_BUDGET_USD`
(`--max-budget-usd`). The prompt is explicit rather than `/code-review`, so the skill's `--fix`
mode can't come into play. The review ends with a `FINDINGS: <n>` line, used for the summary.
Residual risk: the `git diff/log/show` prefixes would also allow `--output=<file>`; the prompt
forbids writing, and nothing else can write.
