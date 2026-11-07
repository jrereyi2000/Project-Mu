"""engines/nfl_history/coach_baseline (docs/done/REAL_NFL_HISTORY_PLAN.md §2.7, §4)."""
import pytest

from engines.nfl_history import coach_baseline as cb


def season(name, s, w, l, t=0, full=True, pw=0, pl=0, sbw=0, sbl=0, team=1):
    return {"coach_name": name, "season": s, "team_id": team, "wins": w, "losses": l, "ties": t, "games": w + l + t,
            "full_season": full, "playoff_wins": pw, "playoff_losses": pl, "super_bowl_wins": sbw,
            "super_bowl_losses": sbl}


NFL = [season("Andy Reid", 1999, 5, 11), season("Andy Reid", 2000, 11, 5, pw=1, pl=1),
       season("Andy Reid", 2022, 14, 3, pw=3, sbw=1), season("Andy Reid", 2008, 9, 6, 1, pw=2, pl=1),
       season("Interim Guy", 2010, 4, 2, full=False), season("Andy Reid", 2026, 10, 7)]


def test_career_sums_winning_seasons_and_super_bowls():
    [r] = cb.baseline_rows([{"coach_id": 7, "coach_name": "Andy Reid"}], NFL)
    assert (r["career_wins"], r["career_losses"], r["career_ties"]) == (39, 25, 1)      # 2026 left out
    assert (r["playoff_wins"], r["playoff_losses"]) == (6, 2)
    assert (r["super_bowl_wins"], r["super_bowl_losses"]) == (1, 0)
    assert r["winning_seasons"] == 3 and r["season"] == 2025 and not r["first_time"]


def test_winning_seasons_only_count_full_seasons():
    [r] = cb.baseline_rows([{"coach_id": 1, "coach_name": "Interim Guy"}], NFL)
    assert r["career_wins"] == 4 and r["winning_seasons"] == 0


def test_first_time_coach_gets_zero_row():
    [r] = cb.baseline_rows([{"coach_id": 3, "coach_name": "New Guy"}], NFL)
    assert r["first_time"] and r["career_wins"] == r["career_losses"] == r["winning_seasons"] == 0


def test_coach_name_fix(monkeypatch):
    monkeypatch.setitem(cb.COACH_NAME_FIX, "Andrew Reid", "Andy Reid")
    [r] = cb.baseline_rows([{"coach_id": 7, "coach_name": "Andrew Reid"}], NFL)
    assert r["career_wins"] == 39


class Cur:
    def __init__(self, later=()):
        self.later, self.sql = list(later), []

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self.later


def test_apply_refuses_when_a_later_snapshot_exists():
    rows = cb.baseline_rows([{"coach_id": 7, "coach_name": "Andy Reid"}], NFL)
    with pytest.raises(cb.BaselineRefused, match="Andy Reid"):
        cb.apply_baseline(Cur(later=[("Andy Reid", 2026)]), rows)


def test_apply_writes_baseline_and_sets_coach_ids():
    rows = cb.baseline_rows([{"coach_id": 7, "coach_name": "Andy Reid"}], NFL)
    cur = Cur()
    assert cb.apply_baseline(cur, rows, [{"coach_id": 7, "coach_name": "Andy Reid"},
                                         {"coach_id": 9, "coach_name": "Other"}]) == 1
    sqls = [s for s, _ in cur.sql]
    assert any(s.startswith("INSERT INTO coach_seasons") for s in sqls)
    assert ("UPDATE nfl_coach_seasons SET coach_id = %s WHERE coach_name = %s", (7, "Andy Reid")) in cur.sql
    assert ("UPDATE nfl_team_seasons SET head_coach_id = %s WHERE head_coach = %s", (9, "Other")) in cur.sql
