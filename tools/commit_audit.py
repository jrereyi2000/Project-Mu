"""Report-only audit of commits that land on main (docs/reference/COMMIT_AUDIT.md).

A post-commit / post-merge hook calls `hook`, which spawns a detached `run` worker and returns.
The worker waits until main has been quiet for --debounce seconds, takes a lock under the git
common dir (shared by every worktree), and audits last-audited-sha..main as one batch:
`git fsck --connectivity-only`, the unit tests (pytest -m "not integration", on a `git archive`
snapshot of main's tree) and a headless, read-only Claude review. It writes a markdown report to
data/commit_audits/ in the main worktree and sends one ntfy notice. It never edits a working tree,
moves a ref or commits.

    python3 -m tools.commit_audit install | uninstall   # hooks in <git-common-dir>/hooks
    python3 -m tools.commit_audit status
    python3 -m tools.commit_audit run [--now] [--range A..B] [--no-tests] [--no-review] [--no-notify]
    python3 -m tools.commit_audit hook                  # what the git hook calls

Env: MU_AUDIT_DEBOUNCE (s, default 90), MU_CLAUDE_BIN (claude binary or command line),
MU_AUDIT_TEST_TIMEOUT (s, 900), MU_AUDIT_REVIEW_TIMEOUT (s, 1200), MU_AUDIT_CLAUDE_MODEL,
MU_AUDIT_MAX_BUDGET_USD, MU_AUDIT_REPORT_DIR; NTFY_TOPIC / NTFY_SERVER via pipeline.notify.
"""
import argparse
import datetime
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK_SRC = ROOT / "tools" / "git-hooks" / "post-commit"
HOOK_NAMES = ("post-commit", "post-merge")  # merges into main fire post-merge, not post-commit
HOOK_MARK = "mu-commit-audit"
BRANCH = "main"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
NPX_PKG = "@anthropic-ai/claude-code"
POLL = 5.0
# Untracked inputs some unit tests read (gitignored data files, the save reader's node_modules):
# symlinked from the main worktree into the test snapshot when the snapshot lacks them.
SNAPSHOT_LINKS = ("pipeline/save_reader/node_modules", "data/*")
_sleep, _clock = time.sleep, time.time  # swapped out by tests

# Headless review: only reading tools, and Bash only for read-only git subcommands.
REVIEW_TOOLS = "Read,Grep,Glob,Bash"
REVIEW_ALLOWED = ["Read", "Grep", "Glob"] + [f"Bash(git {c}:*)" for c in
                                             ("log", "show", "diff", "blame", "rev-parse", "ls-files", "cat-file")]
REVIEW_DENIED = ["Edit", "Write", "NotebookEdit", "MultiEdit"]


def _env_float(key, default):
    try:
        return float(os.environ.get(key, default))
    except ValueError:
        return float(default)


def git(*args, cwd=None, check=True, timeout=120):
    """Output of one git command (stripped)."""
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout.strip()


class Repo:
    """Paths of the repository the cwd belongs to: common dir, main worktree, audit state dir."""

    def __init__(self, cwd=None):
        cwd = str(cwd or os.getcwd())
        self.common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=cwd))
        first = git("worktree", "list", "--porcelain", cwd=cwd).splitlines()[0]
        self.main_wt = Path(first.split(" ", 1)[1]) if first.startswith("worktree ") else Path(cwd)
        self.state_dir = self.common / "commit_audit"
        self.report_dir = Path(os.environ.get("MU_AUDIT_REPORT_DIR") or self.main_wt / "data" / "commit_audits")

    def git(self, *args, **kw):
        return git(*args, cwd=str(self.main_wt), **kw)

    def head(self):
        """main's sha, or None when main doesn't exist."""
        return self.git("rev-parse", "-q", "--verify", f"refs/heads/{BRANCH}^{{commit}}", check=False) or None

    def ref_time(self):
        """When main last moved (reflog), else its committer time."""
        out = self.git("log", "-g", "-1", "--format=%gd", "--date=unix", f"refs/heads/{BRANCH}", check=False)
        m = re.search(r"@\{(\d+)\}", out)
        if m:
            return float(m.group(1))
        out = self.git("log", "-1", "--format=%ct", f"refs/heads/{BRANCH}", check=False)
        return float(out) if out.isdigit() else time.time()

    # state ----------------------------------------------------------------
    @property
    def state_path(self):
        return self.state_dir / "state.json"

    def load_state(self):
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}

    def save_state(self, **kw):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        state = {**self.load_state(), **kw, "updated_at": _now().isoformat(timespec="seconds")}
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1))
        os.replace(tmp, self.state_path)


def _now():
    return datetime.datetime.now().astimezone()


# lock -----------------------------------------------------------------------
class Lock:
    """Non-blocking flock on <common>/commit_audit/lock; holds the worker's pid."""

    def __init__(self, repo):
        self.path = repo.state_dir / "lock"
        self.fd = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        self.fd = fd
        return True

    def release(self):
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None

    def held_by_other(self):
        """Pid text of the running worker, or None when nobody holds the lock."""
        if not self.path.exists():
            return None
        fd = os.open(self.path, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return None
        except OSError:
            return self.path.read_text().strip() or "?"
        finally:
            os.close(fd)


# steps ----------------------------------------------------------------------
def wait_quiet(repo, debounce, poll=POLL):
    """Block until main hasn't moved for `debounce` seconds; returns main's sha then."""
    sleep, clock = _sleep, _clock
    head, last = repo.head(), repo.ref_time()
    while True:
        left = debounce - (clock() - last)
        if left <= 0:
            return head
        sleep(min(poll, left))
        now_head = repo.head()
        if now_head != head:
            head, last = now_head, max(repo.ref_time(), clock())


def pick_base(repo, head, state):
    """Start of the range: last audited sha when it's an ancestor of head, else head~1 (or empty tree)."""
    last = state.get("last_audited")
    if last and last != head and subprocess.run(["git", "merge-base", "--is-ancestor", last, head],
                                                cwd=str(repo.main_wt), capture_output=True).returncode == 0:
        return last
    parent = repo.git("rev-parse", "-q", "--verify", f"{head}~1", check=False)
    return parent or EMPTY_TREE


def fsck(repo, retry_wait=30):
    """(ok, output) of `git fsck --connectivity-only`, retried once after retry_wait."""
    for attempt in range(2):
        p = subprocess.run(["git", "fsck", "--connectivity-only", "--no-progress", "--no-dangling"],
                           cwd=str(repo.main_wt), capture_output=True, text=True, timeout=600)
        out = (p.stdout + p.stderr).strip()
        if p.returncode == 0:
            return True, out
        if attempt == 0:
            _sleep(retry_wait)
    return False, out


def failed_ids(output):
    """Node ids from pytest's -rfE short summary (FAILED x::y - msg / ERROR tests/x.py - msg)."""
    return sorted({m.group(2).split(" - ")[0].strip() for m in re.finditer(r"^(FAILED|ERROR) (\S.*)$", output, re.M)})


def link_untracked(src_root, dst_root, patterns=SNAPSHOT_LINKS):
    """Symlink each match of patterns under src_root into dst_root where dst_root has nothing there."""
    for pat in patterns:
        for src in sorted(Path(src_root).glob(pat)):
            dst = dst_root / src.relative_to(src_root)
            if not os.path.lexists(dst):
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.symlink_to(src)


def run_tests(repo, sha, timeout, nodeids=None):
    """Unit tests (-m "not integration") on a `git archive` snapshot of sha, or just `nodeids`. Result dict."""
    with tempfile.TemporaryDirectory(prefix="mu_audit_") as tmp:
        arch = subprocess.Popen(["git", "archive", "--format=tar", sha], cwd=str(repo.main_wt),
                                stdout=subprocess.PIPE)
        with tarfile.open(fileobj=arch.stdout, mode="r|") as tf:
            tf.extractall(tmp, filter="data")
        arch.wait()
        link_untracked(repo.main_wt, Path(tmp))
        cmd = [sys.executable, "-m", "pytest", "-m", "not integration", "-q", "-p", "no:cacheprovider",
               "--continue-on-collection-errors", "-rfE", *(nodeids or [])]
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        start = time.time()
        try:
            p = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired as e:
            out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            return {"status": "timeout", "summary": f"timed out after {timeout:.0f}s", "output": out[-6000:],
                    "seconds": time.time() - start}
        out = p.stdout + p.stderr
        lines = [l for l in out.splitlines() if l.strip()]
        summary = next((l.strip("= ") for l in reversed(lines)
                        if re.search(r"\b(passed|failed|error|no tests ran)\b", l)), lines[-1] if lines else "")
        status = {0: "passed", 5: "no tests"}.get(p.returncode, "failed")
        return {"status": status, "summary": summary, "failed": failed_ids(out),
                "output": out[-6000:], "seconds": time.time() - start, "returncode": p.returncode}


def split_new_failures(repo, base, result, timeout):
    """Re-run the head's failing tests at base; failures that also fail there are marked pre-existing."""
    ids = result.get("failed") or []
    if result["status"] != "failed" or not ids or base == EMPTY_TREE:
        return result
    at_base = [i for i in ids if subprocess.run(["git", "cat-file", "-e", f"{base}:{i.split('::')[0]}"],
                                                cwd=str(repo.main_wt), capture_output=True).returncode == 0]
    old = set(run_tests(repo, base, timeout, at_base).get("failed", [])) if at_base else set()
    new = [i for i in ids if i not in old]
    result = {**result, "new_failed": new, "old_failed": sorted(old & set(ids))}
    if not new:
        result["status"] = "passed (pre-existing failures)"
    return result


def resolve_claude(probe_timeout=180):
    """(argv prefix, how) for the claude CLI: MU_CLAUDE_BIN, `claude` on PATH, then npx. (None, reason) if none."""
    tried = []
    env_bin = os.environ.get("MU_CLAUDE_BIN", "").strip()
    if env_bin:
        argv = shlex.split(env_bin)
        if shutil.which(argv[0]):
            return argv, f"MU_CLAUDE_BIN={env_bin}"
        tried.append(f"MU_CLAUDE_BIN={env_bin} not found")
    path_bin = shutil.which("claude")
    if path_bin:
        return [path_bin], f"PATH ({path_bin})"
    tried.append("no `claude` on PATH")
    npx = shutil.which("npx")
    if npx:
        argv = [npx, "-y", NPX_PKG]
        try:
            p = subprocess.run([*argv, "--version"], capture_output=True, text=True, timeout=probe_timeout)
            if p.returncode == 0:
                return argv, f"npx {NPX_PKG} ({p.stdout.strip()})"
            tried.append(f"npx {NPX_PKG} --version exited {p.returncode}: {(p.stderr or p.stdout).strip()[:200]}")
        except (OSError, subprocess.TimeoutExpired) as e:
            tried.append(f"npx {NPX_PKG} failed: {e}")
    else:
        tried.append("no npx on PATH")
    return None, "; ".join(tried)


def review_prompt(base, head, log, stat):
    rng = f"{base[:12]}..{head[:12]}" if base != EMPTY_TREE else f"root commit {head[:12]}"
    diff_cmd = f"git diff {base} {head}" if base != EMPTY_TREE else f"git show {head}"
    return f"""You are auditing commits that already landed on `main` of this repository (Project Mu).
This is a REPORT-ONLY review. Never edit, create or delete files, never run anything but read-only
git commands (git log / show / diff / blame / ls-files / cat-file), never commit or touch refs.
The working tree may hold other people's uncommitted changes: judge the commits, not the working tree.

Review the commit range {rng} (base {base}, head {head}) for correctness bugs: logic errors,
wrong conditions, crashes, broken call sites, data loss, SQL/schema mismatches, race conditions,
security problems, and code that disagrees with how the rest of the codebase uses it. Read the diff
with `{diff_cmd}` (or per commit with `git show <sha>`), and read surrounding code where needed.
Skip style, naming and refactoring suggestions. Only report issues you are reasonably confident in.

Commits:
{log}

Files changed:
{stat}

Output markdown. For each finding: a `### ` heading with a short title, the commit sha, `file:line`,
what goes wrong and when, and a one-line suggested fix. If you find nothing, say so.
End with exactly one line `FINDINGS: <n>` where <n> is the number of findings."""


def review_flags(help_text=""):
    """Flags for the headless read-only run; optional ones only when the CLI's --help lists them."""
    flags = ["--permission-mode", "dontAsk",
             "--allowedTools", *REVIEW_ALLOWED,
             "--disallowedTools", *REVIEW_DENIED,
             "--output-format", "text"]
    if "--tools" in help_text:
        flags += ["--tools", REVIEW_TOOLS]
    if "--permission-prompts" in help_text:
        flags += ["--permission-prompts", "none"]
    if "--no-session-persistence" in help_text:
        flags += ["--no-session-persistence"]
    if os.environ.get("MU_AUDIT_CLAUDE_MODEL"):
        flags += ["--model", os.environ["MU_AUDIT_CLAUDE_MODEL"]]
    if os.environ.get("MU_AUDIT_MAX_BUDGET_USD") and "--max-budget-usd" in help_text:
        flags += ["--max-budget-usd", os.environ["MU_AUDIT_MAX_BUDGET_USD"]]
    return flags


def run_claude(repo, base, head, timeout):
    """Headless read-only review of base..head. Result dict: status ok|failed|skipped, text, findings."""
    argv, how = resolve_claude()
    if not argv:
        return {"status": "skipped", "reason": how, "text": "", "findings": None, "how": None}
    rev = head if base == EMPTY_TREE else f"{base}..{head}"
    log = repo.git("log", "--format=%h %an %s", rev) if base != EMPTY_TREE else repo.git("log", "-1", "--format=%h %an %s", head)
    stat = repo.git("diff", "--stat=120", base, head)
    try:
        help_text = subprocess.run([*argv, "--help"], capture_output=True, text=True, timeout=120).stdout
    except (OSError, subprocess.TimeoutExpired):
        help_text = ""
    # The prompt goes right after -p: --allowedTools/--disallowedTools take a variadic list.
    cmd = [*argv, "-p", review_prompt(base, head, log, stat), *review_flags(help_text)]
    try:
        p = subprocess.run(cmd, cwd=str(repo.main_wt), capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"status": "failed", "reason": f"timed out after {timeout:.0f}s", "text": "", "findings": None, "how": how}
    except OSError as e:
        return {"status": "skipped", "reason": f"couldn't start {argv[0]}: {e}", "text": "", "findings": None, "how": how}
    text = p.stdout.strip()
    if p.returncode != 0:
        return {"status": "failed", "reason": f"exit {p.returncode}: {p.stderr.strip()[-500:]}", "text": text,
                "findings": None, "how": how}
    m = re.findall(r"^\s*FINDINGS:\s*(\d+)\s*$", text, re.M)
    return {"status": "ok", "text": text, "findings": int(m[-1]) if m else None, "how": how}


# report ---------------------------------------------------------------------
TESTS_OK = ("passed", "no tests", "passed (pre-existing failures)")


def summary_line(base, head, n_commits, fsck_ok, tests, review):
    rng = f"{base[:7] if base != EMPTY_TREE else 'root'}..{head[:7]}"
    parts = [f"main {rng} ({n_commits} commit{'s' if n_commits != 1 else ''})"]
    if not fsck_ok:
        parts.append("git fsck FAILED")
    if tests:
        new = tests.get("new_failed")
        parts.append(f"tests: {tests['summary']}" if tests["status"] == "passed"
                     else f"tests {tests['status']}: {tests['summary']}"
                     + (f" ({len(new)} new)" if new else ""))
    if review:
        if review["status"] == "ok":
            f = review["findings"]
            parts.append("review: " + ("see report" if f is None else f"{f} finding{'s' if f != 1 else ''}"))
        else:
            parts.append(f"review {review['status']}")
    return "; ".join(parts)


def write_report(repo, base, head, commits, fsck_res, tests, review, summary, started):
    repo.report_dir.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y-%m-%d_%H%M%S")
    path = repo.report_dir / f"{stamp}_{base[:7] if base != EMPTY_TREE else 'root'}..{head[:7]}.md"
    out = [f"# Commit audit {base[:7] if base != EMPTY_TREE else 'root'}..{head[:7]}", "",
           f"- When: {started.isoformat(timespec='seconds')}",
           f"- Range: `{base}..{head}`", f"- Summary: {summary}", "", "## Commits", "", "```", commits or "(none)", "```",
           "", "## git fsck --connectivity-only", "", "OK" if fsck_res[0] else "**FAILED**"]
    if fsck_res[1]:
        out += ["", "```", fsck_res[1][-3000:], "```"]
    out += ["", "## Unit tests (pytest -m \"not integration\")", ""]
    if tests is None:
        out.append("Not run.")
    else:
        out += [f"**{tests['status']}** — {tests['summary']} ({tests.get('seconds', 0):.0f}s)"]
        for key, label in (("new_failed", "New failures (pass at base)"),
                           ("old_failed", "Pre-existing failures (also fail at base)")):
            if tests.get(key):
                out += ["", f"{label}:", ""] + [f"- `{i}`" for i in tests[key]]
        if tests["status"] not in TESTS_OK:
            out += ["", "```", tests.get("output", "")[-6000:], "```"]
    out += ["", "## AI review", ""]
    if review is None:
        out.append("Not run.")
    elif review["status"] == "skipped":
        out.append(f"AI review was skipped: {review['reason']}")
    else:
        out.append(f"_claude via {review['how']}_")
        if review["status"] != "ok":
            out += ["", f"**Review failed:** {review['reason']}"]
        if review["text"]:
            out += ["", review["text"]]
    path.write_text("\n".join(out) + "\n")
    return path


def send_notice(summary, bad, path):
    try:
        sys.path.insert(0, str(ROOT))
        from pipeline.notify import notify
        notify(f"{summary}\n{path.name}", title="Commit audit", priority="high" if bad else "default",
               tags=("warning",) if bad else ("white_check_mark",))
    except Exception as e:  # a notice never stops the audit
        print(f"[audit] notice not sent: {e}", flush=True)


def audit(repo, base, head, *, tests=True, review=True, notify=True, test_timeout=900, review_timeout=1200):
    """Audit base..head once; returns (report path, fsck ok)."""
    started = _now()
    rev = head if base == EMPTY_TREE else f"{base}..{head}"
    commits = repo.git("log", "--format=%h %ad %an  %s", "--date=format:%Y-%m-%d %H:%M", rev) \
        if base != EMPTY_TREE else repo.git("log", "-1", "--format=%h %ad %an  %s", "--date=format:%Y-%m-%d %H:%M", head)
    n = len(commits.splitlines())
    fsck_res = fsck(repo)
    t = r = None
    if fsck_res[0]:
        if tests:
            t = split_new_failures(repo, base, run_tests(repo, head, test_timeout), test_timeout)
        if review:
            r = run_claude(repo, base, head, review_timeout)
    summary = summary_line(base, head, n, fsck_res[0], t, r)
    path = write_report(repo, base, head, commits, fsck_res, t, r, summary, started)
    print(f"[audit] {summary}\n[audit] report: {path}", flush=True)
    if notify:
        bad = not fsck_res[0] or (t and t["status"] not in TESTS_OK) or bool(r and r.get("findings"))
        send_notice(summary, bad, path)
    return path, fsck_res[0]


# commands -------------------------------------------------------------------
def current_branch(cwd=None):
    return git("symbolic-ref", "--short", "-q", "HEAD", cwd=cwd, check=False)


def cmd_hook(args, spawn=None):
    """Spawn a detached worker when the commit is on main; return at once."""
    if current_branch() != BRANCH:
        return 0
    repo = Repo()
    repo.state_dir.mkdir(parents=True, exist_ok=True)
    log = open(repo.state_dir / "worker.log", "a")
    cmd = [sys.executable, "-m", "tools.commit_audit", "run"]
    # Hooks run with GIT_DIR / GIT_INDEX_FILE set for the committing worktree; the worker must not inherit them.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    (spawn or subprocess.Popen)(cmd, cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                start_new_session=True, close_fds=True)
    return 0


def cmd_run(args):
    repo = Repo()
    kw = dict(tests=not args.no_tests, review=not args.no_review, notify=not args.no_notify,
              test_timeout=args.test_timeout, review_timeout=args.review_timeout)
    if args.range:
        base, _, head = args.range.partition("..")
        base = repo.git("rev-parse", "--verify", f"{base}^{{commit}}")
        head = repo.git("rev-parse", "--verify", f"{(head or 'HEAD')}^{{commit}}")
        audit(repo, base, head, **kw)  # an explicit range doesn't touch the state file
        return 0
    lock = Lock(repo)
    debounce = 0 if args.now else args.debounce
    attempted = None
    while True:
        if not lock.acquire():
            print(f"[audit] another worker holds {lock.path}; exiting", flush=True)
            return 0
        try:
            head = wait_quiet(repo, debounce, poll=args.poll)
            state = repo.load_state()
            if head is None or head == state.get("last_audited") or head == attempted:
                return 0
            base = pick_base(repo, head, state)
            attempted = head
            path, ok = audit(repo, base, head, **kw)
            if ok:
                repo.save_state(last_audited=head, last_base=base, last_report=str(path))
            else:
                repo.save_state(last_report=str(path))
        finally:
            lock.release()
        # A hook that fired while we held the lock exited; re-check main after releasing it.
        state = repo.load_state()
        if repo.head() in (state.get("last_audited"), attempted):
            return 0


def cmd_status(args):
    repo = Repo()
    state = repo.load_state()
    pid = Lock(repo).held_by_other()
    head = repo.head()
    last = state.get("last_audited")
    print(f"main:              {head[:12] if head else '(none)'}")
    print(f"last audited:      {last[:12] if last else '(never)'}" + (f"  at {state.get('updated_at')}" if last else ""))
    if last and head and last != head:
        behind = repo.git("rev-list", "--count", f"{last}..{head}", check=False)
        print(f"pending:           {behind} commit(s)")
    print(f"worker:            {'running (pid ' + pid + ')' if pid else 'not running'}")
    reports = sorted(repo.report_dir.glob("*.md")) if repo.report_dir.exists() else []
    print(f"latest report:     {reports[-1] if reports else state.get('last_report', '(none)')}")
    for name in HOOK_NAMES:
        hook = repo.common / "hooks" / name
        mine = hook.exists() and HOOK_MARK in hook.read_text(errors="ignore")
        print(f"{name + ' hook:':19}{'installed' if mine else ('other hook present' if hook.exists() else 'not installed')}")
    print(f"state dir:         {repo.state_dir}")
    return 0


def cmd_install(args):
    repo = Repo()
    hooks = repo.common / "hooks"
    hooks.mkdir(exist_ok=True)
    src = HOOK_SRC.read_text()
    clash = [h for h in (hooks / n for n in HOOK_NAMES) if h.exists() and HOOK_MARK not in h.read_text(errors="ignore")]
    if clash:
        print(f"refusing to overwrite existing hook(s): {', '.join(map(str, clash))}", file=sys.stderr)
        return 1
    for name in HOOK_NAMES:
        dst = hooks / name
        dst.write_text(src)
        dst.chmod(0o755)
        print(f"installed {dst}")
    if git("config", "--get", "core.hooksPath", cwd=str(repo.main_wt), check=False):
        print("warning: core.hooksPath is set, so git won't run hooks from this folder", file=sys.stderr)
    return 0


def cmd_uninstall(args):
    repo = Repo()
    for name in HOOK_NAMES:
        dst = repo.common / "hooks" / name
        if not dst.exists():
            continue
        if HOOK_MARK not in dst.read_text(errors="ignore"):
            print(f"leaving {dst}: not ours", file=sys.stderr)
            continue
        dst.unlink()
        print(f"removed {dst}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hook", help="called by the git hook: spawn a detached worker when on main")
    r = sub.add_parser("run", help="the worker: debounce, lock, audit last-audited..main")
    r.add_argument("--debounce", type=float, default=_env_float("MU_AUDIT_DEBOUNCE", 90),
                   help="seconds main must stay still before auditing (MU_AUDIT_DEBOUNCE, default 90)")
    r.add_argument("--now", action="store_true", help="no debounce")
    r.add_argument("--poll", type=float, default=POLL, help=argparse.SUPPRESS)
    r.add_argument("--range", help="audit A..B now (no lock, no debounce, state untouched)")
    r.add_argument("--no-tests", action="store_true")
    r.add_argument("--no-review", action="store_true")
    r.add_argument("--no-notify", action="store_true")
    r.add_argument("--test-timeout", type=float, default=_env_float("MU_AUDIT_TEST_TIMEOUT", 900))
    r.add_argument("--review-timeout", type=float, default=_env_float("MU_AUDIT_REVIEW_TIMEOUT", 1200))
    sub.add_parser("status", help="last audited sha, worker, latest report, hooks")
    sub.add_parser("install", help=f"install {', '.join(HOOK_NAMES)} hooks into <git-common-dir>/hooks")
    sub.add_parser("uninstall", help="remove the hooks this tool installed")
    args = ap.parse_args(argv)
    return {"hook": cmd_hook, "run": cmd_run, "status": cmd_status,
            "install": cmd_install, "uninstall": cmd_uninstall}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
