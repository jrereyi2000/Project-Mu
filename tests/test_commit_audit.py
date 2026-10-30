"""Unit tests for tools/commit_audit.py on throwaway git repos (pytest and claude calls mocked)."""
import argparse
import os
import subprocess

import pytest

import tools.commit_audit as ca

_real_run_claude = ca.run_claude


def sh(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, name):
    (repo / f"{name}.txt").write_text(name)
    sh(repo, "add", ".")
    sh(repo, "commit", "-q", "-m", name)
    return sh(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("GIT_"):
            monkeypatch.delenv(k)
    for k in ("MU_CLAUDE_BIN", "MU_AUDIT_REPORT_DIR", "NTFY_TOPIC"):
        monkeypatch.delenv(k, raising=False)
    r = tmp_path / "repo"
    r.mkdir()
    sh(r, "init", "-q", "-b", "main")
    sh(r, "config", "user.email", "t@example.com")
    sh(r, "config", "user.name", "t")
    commit(r, "c1")
    monkeypatch.chdir(r)
    return r


@pytest.fixture
def calls(monkeypatch):
    """Record audits instead of running pytest / claude."""
    seen = {"tests": [], "review": []}
    monkeypatch.setattr(ca, "run_tests", lambda repo, sha, timeout: seen["tests"].append(sha) or
                        {"status": "passed", "summary": "3 passed in 0.1s", "output": "", "seconds": 0.1})
    monkeypatch.setattr(ca, "run_claude", lambda repo, base, head, timeout: seen["review"].append((base, head)) or
                        {"status": "ok", "text": "No issues.\nFINDINGS: 0", "findings": 0, "how": "mock"})
    return seen


def run_args(**kw):
    d = dict(debounce=0, now=False, poll=0.01, range=None, no_tests=False, no_review=False, no_notify=True,
             test_timeout=10, review_timeout=10)
    d.update(kw)
    return argparse.Namespace(**d)


def test_first_run_audits_head_parent(repo, calls):
    c1 = sh(repo, "rev-parse", "HEAD")
    c2 = commit(repo, "c2")
    assert ca.cmd_run(run_args()) == 0
    assert calls["review"] == [(c1, c2)]
    state = ca.Repo().load_state()
    assert state["last_audited"] == c2
    assert (repo / "data" / "commit_audits").exists()
    reports = list((repo / "data" / "commit_audits").glob("*.md"))
    assert len(reports) == 1 and f"{c1[:7]}..{c2[:7]}" in reports[0].name
    # nothing new: no second audit
    assert ca.cmd_run(run_args()) == 0
    assert len(calls["review"]) == 1


def test_debounce_coalesces_commits_into_one_range(repo, calls, monkeypatch):
    c1 = sh(repo, "rev-parse", "HEAD")
    ca.Repo().save_state(last_audited=c1)
    commit(repo, "c2")
    clock = [ca.time.time()]
    pending = ["c3", "c4", "c5"]

    def fake_sleep(s):  # a new commit lands during each of the first three waits
        clock[0] += s
        if pending:
            commit(repo, pending.pop(0))

    monkeypatch.setattr(ca, "_sleep", fake_sleep)
    monkeypatch.setattr(ca, "_clock", lambda: clock[0])
    assert ca.cmd_run(run_args(debounce=90, poll=5)) == 0
    head = sh(repo, "rev-parse", "HEAD")
    assert calls["review"] == [(c1, head)]
    assert calls["tests"] == [head]
    assert sh(repo, "rev-list", "--count", f"{c1}..{head}") == "4"
    assert ca.Repo().load_state()["last_audited"] == head


def test_wait_quiet_waits_full_debounce(repo, monkeypatch):
    clock = [ca.time.time()]
    slept = []
    monkeypatch.setattr(ca, "_sleep", lambda s: (slept.append(s), clock.__setitem__(0, clock[0] + s)))
    monkeypatch.setattr(ca, "_clock", lambda: clock[0])
    ca.wait_quiet(ca.Repo(), 30, poll=5)
    assert 25 <= sum(slept) <= 31


def test_lock_blocks_second_worker(repo, calls):
    commit(repo, "c2")
    holder = ca.Lock(ca.Repo())
    assert holder.acquire()
    try:
        assert ca.Lock(ca.Repo()).held_by_other() == str(os.getpid())
        assert ca.cmd_run(run_args()) == 0
        assert calls["review"] == [] and calls["tests"] == []
        assert "last_audited" not in ca.Repo().load_state()
    finally:
        holder.release()
    assert ca.Lock(ca.Repo()).held_by_other() is None
    ca.cmd_run(run_args())
    assert len(calls["review"]) == 1


def test_hook_ignores_other_branches(repo):
    spawned = []
    sh(repo, "checkout", "-q", "-b", "feature")
    commit(repo, "f1")
    assert ca.cmd_hook(None, spawn=lambda *a, **k: spawned.append(a)) == 0
    assert spawned == []
    sh(repo, "checkout", "-q", "main")
    assert ca.cmd_hook(None, spawn=lambda *a, **k: spawned.append((a, k))) == 0
    assert len(spawned) == 1
    (cmd,), kw = spawned[0]
    assert cmd[-2:] == ["tools.commit_audit", "run"] and kw["start_new_session"]
    assert not any(k.startswith("GIT_") for k in kw["env"])


def test_missing_binary_skips_review_but_runs_tests(repo, calls, monkeypatch):
    monkeypatch.setattr(ca.shutil, "which", lambda name: None)
    monkeypatch.setenv("MU_CLAUDE_BIN", "/nonexistent/claude")
    argv, why = ca.resolve_claude()
    assert argv is None and "MU_CLAUDE_BIN" in why and "no npx" in why
    monkeypatch.setattr(ca, "run_claude", _real_run_claude)
    commit(repo, "c2")
    ca.cmd_run(run_args())
    assert len(calls["tests"]) == 1
    report = next((repo / "data" / "commit_audits").glob("*.md")).read_text()
    assert "AI review was skipped" in report and "no `claude` on PATH" in report
    assert ca.Repo().load_state()["last_audited"] == sh(repo, "rev-parse", "HEAD")


def test_review_flags_are_read_only():
    flags = ca.review_flags("--tools --permission-prompts --no-session-persistence")
    assert flags[flags.index("--permission-mode") + 1] == "dontAsk"
    assert flags[flags.index("--tools") + 1] == "Read,Grep,Glob,Bash"
    denied = flags[flags.index("--disallowedTools") + 1:flags.index("--output-format")]
    assert {"Edit", "Write", "NotebookEdit"} <= set(denied)
    allowed = flags[flags.index("--allowedTools") + 1:flags.index("--disallowedTools")]
    assert all(a in ("Read", "Grep", "Glob") or a.startswith("Bash(git ") for a in allowed)
    assert "--tools" not in ca.review_flags("")  # older CLIs: optional flags left out


def test_install_refuses_to_clobber(repo):
    hook = repo / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/bin/sh\necho mine\n")
    assert ca.cmd_install(None) == 1
    assert hook.read_text() == "#!/bin/sh\necho mine\n"
    assert not (repo / ".git" / "hooks" / "post-merge").exists()
    ca.cmd_uninstall(None)
    assert hook.exists()


def test_install_and_uninstall(repo):
    assert ca.cmd_install(None) == 0
    for name in ca.HOOK_NAMES:
        h = repo / ".git" / "hooks" / name
        assert ca.HOOK_MARK in h.read_text() and os.access(h, os.X_OK)
    assert ca.cmd_install(None) == 0  # reinstalling our own hook is fine
    ca.cmd_uninstall(None)
    assert not any((repo / ".git" / "hooks" / n).exists() for n in ca.HOOK_NAMES)


def test_state_shared_across_worktrees(repo, tmp_path):
    sh(repo, "worktree", "add", "-q", str(tmp_path / "wt"), "-b", "side")
    a, b = ca.Repo(repo), ca.Repo(tmp_path / "wt")
    assert a.state_dir == b.state_dir and b.main_wt == repo.resolve()


def test_failures_that_also_fail_at_base_are_pre_existing(repo, monkeypatch):
    base = sh(repo, "rev-parse", "HEAD")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_new.py").write_text("")
    commit(repo, "c2")
    out = ("FAILED c1.txt::test_old - AssertionError\nFAILED tests/test_new.py::test_x - boom\n"
           "ERROR tests/test_gone.py - ImportError\n2 failed, 1 error in 0.1s\n")
    assert ca.failed_ids(out) == ["c1.txt::test_old", "tests/test_gone.py", "tests/test_new.py::test_x"]
    rerun = []
    monkeypatch.setattr(ca, "run_tests", lambda r, sha, t, ids=None: rerun.append((sha, ids)) or
                        {"status": "failed", "failed": ["c1.txt::test_old"]})
    res = ca.split_new_failures(ca.Repo(), base, {"status": "failed", "failed": ca.failed_ids(out)}, 10)
    assert rerun == [(base, ["c1.txt::test_old"])]  # only ids whose file exists at base are re-run
    assert res["old_failed"] == ["c1.txt::test_old"] and res["status"] == "failed"
    assert res["new_failed"] == ["tests/test_gone.py", "tests/test_new.py::test_x"]
    only_old = ca.split_new_failures(ca.Repo(), base, {"status": "failed", "failed": ["c1.txt::test_old"]}, 10)
    assert only_old["status"] == "passed (pre-existing failures)"


def test_link_untracked_fills_only_missing_paths(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "data" / "history").mkdir(parents=True)
    (src / "data" / "lookup.json").write_text("{}")
    (dst / "data" / "history").mkdir(parents=True)
    ca.link_untracked(src, dst, ("data/*",))
    assert (dst / "data" / "lookup.json").is_symlink()
    assert not (dst / "data" / "history").is_symlink()
