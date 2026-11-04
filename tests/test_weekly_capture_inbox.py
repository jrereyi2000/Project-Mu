"""The weekly run's inbox step (FRONT_OFFICE_INBOX_PLAN.md §2.10; U1): step_inbox runs right after step_gm and a
failure only logs."""
import pytest

from pipeline import weekly_capture as wc

STEPS = ("step_engines", "step_gm", "step_inbox", "step_scouting", "step_season_archive", "step_coach_hot_seat")


@pytest.fixture
def calls(monkeypatch):
    seen = []
    for name in STEPS:
        monkeypatch.setattr(wc, name, lambda season, _n=name: seen.append(_n))
    return seen


@pytest.mark.parametrize("mode", ["weekly", "playoff"])
def test_step_inbox_runs_after_step_gm(calls, mode):
    wc.run_weekly_engines(2026, mode=mode, log_fn=lambda m: None)
    assert calls.index("step_inbox") == calls.index("step_gm") + 1
    assert calls.index("step_inbox") < calls.index("step_scouting")


def test_postgame_runs_no_inbox(calls):
    wc.run_weekly_engines(2026, mode="postgame", log_fn=lambda m: None)
    assert "step_inbox" not in calls and "step_gm" not in calls


def test_step_inbox_delegates_to_the_engine(monkeypatch):
    import engines.inbox.scan as scan
    got = []
    monkeypatch.setattr(scan, "step_inbox", lambda season, log_fn=None: got.append(season) or {"created": 0})
    assert wc.step_inbox(2026) == {"created": 0} and got == [2026]


def test_step_inbox_errors_are_logged(monkeypatch):
    import engines.inbox.scan as scan
    logs = []

    def boom(season, log_fn=None):
        raise RuntimeError("inbox broke")
    monkeypatch.setattr(scan, "step_inbox", boom)
    monkeypatch.setattr(wc, "log", logs.append)
    assert wc.step_inbox(2026) is None
    assert any("Inbox skipped" in m and "inbox broke" in m for m in logs)
