"""Post-draft validation and the Season-scan feedback (docs/team_management/04_SCOUTING_AND_DRAFT_PLAN.md Phase 8).
Pure: fake cursors, no DB."""
from unittest import mock

from engines import draft_room as dr
from engines import fo_quality as fq
from engines import scouting_engine as se


class Cur:
    """Answers the validate queries in order and records every statement."""

    def __init__(self, recorded, recap):
        self.recorded, self.recap, self.sql, self._last = recorded, recap, [], None

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))
        self._last = self.sql[-1]

    def executemany(self, sql, rows):
        self.sql.append(" ".join(sql.split()))
        self.rows = list(rows)

    def fetchone(self):
        return (True,)

    def fetchall(self):
        if "FROM scout_draft_picks" in self._last:
            return self.recorded
        if "FROM draft_prospects" in self._last:
            return self.recap
        return []


def test_validation_marks_match_and_mismatch_and_edits_nothing_else():
    recorded = [(1, 5, "k1", 11, "GM", "k1", None, None, None, None),
                (2, 6, "k2", 12, "OFF_SCRIPT", "k9", None, None, None, None)]
    recap = [(1, 11, 5), (2, 77, 6), (40, 99, 8)]
    cur = Cur(recorded, recap)
    rep = dr.validate(cur, 2037)
    assert (rep["checked"], rep["match"]) == (2, 1) and rep["mismatches"] == [{"pick": 2, "why": "different player"}]
    assert cur.rows == [("MATCH", 11, 2037, 1), ("MISMATCH", 77, 2037, 2)]
    writes = [s for s in cur.sql if s.startswith(("UPDATE", "INSERT", "DELETE"))]
    assert writes and all(s.startswith(("UPDATE scout_draft_picks", "UPDATE scout_prospects")) for s in writes)
    assert not any("draft_prospects SET" in s for s in writes)            # the recap stays Madden's truth


def test_recompute_includes_new_rookies_at_year_zero():
    rows = []
    for ds in (2036, 2037):
        for pk in range(1, 65):
            rows.append({"draft_season": ds, "overall_pick": pk, "drafted_team_id": 1 + pk % 8,
                         "roster_team_id": 1, "player_id": ds * 100 + pk, "name": f"P{pk}", "position": "WR",
                         "ovr": 78 - pk // 8 + (2 if ds == 2036 else 0), "dev": "Normal"})
    scored = fq.score_all(rows, 2037, jr_picks={(2037, 3)})
    years = {(p["draft_season"], p["years"]) for ps in scored.values() for p in ps}
    assert years == {(2036, 1), (2037, 0)}
    assert all(not (p["draft_season"] == 2037 and p["pick"] == 3) for ps in scored.values() for p in ps)
    first = {t: fq.team_q(ps, 2037) for t, ps in scored.items()}
    assert first == {t: fq.team_q(ps, 2037) for t, ps in fq.score_all(rows, 2037, jr_picks={(2037, 3)}).items()}


def test_new_class_starts_at_step_one():
    cur = mock.MagicMock()
    board = [{"id": i, "full_name": f"P {chr(65 + i)}", "position": "WR", "college": "GEORGIA", "age": 22, "ovr": 70,
              "projected_pick": i + 1, "height_in": 72, "weight": 200} for i in range(5)]
    with mock.patch.object(se, "board_rows", return_value=board), \
         mock.patch.object(se, "installed", return_value=False), \
         mock.patch("engines.gm_trade_engine.league_window", return_value=(2037, 1, "IN_SEASON")):
        rep = se.init(cur, 2038, dry_run=True, log=lambda *a: None)
    assert rep["first_step"] == 1 and rep["prospects"] == 5
    assert se.sigma_at(1, 1) == se.SIGMA_0 and se.sigma_at(27, 1) == se.SIGMA_DRAFT


def test_season_step_failure_only_warns():
    from pipeline import weekly_capture as wc
    logs = []
    with mock.patch.object(wc, "log", logs.append), mock.patch("psycopg.connect", side_effect=RuntimeError("no db")):
        assert wc.step_scouting_season(2037) == {}
    assert any("[WARN] Season scouting step skipped" in m for m in logs)
