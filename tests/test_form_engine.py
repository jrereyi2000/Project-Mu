"""engines/form_engine.py: weekly form from game lines, the ratings overlay, and the weekly edits document (synthetic
data, a fake cursor; no database)."""
import json
import sys
import types
from unittest import mock

import pytest

from engines import form_engine as fe
from engines import ovr_weights as ow
from engines import progression

DOC = ow.load_weights()


def ratings_for(position, value=70):
    """Every rating any of the position's archetypes uses, at `value` (Injury and Speed too)."""
    names = {"Speed", "Acceleration", "Injury"}
    for a in DOC["slots"][position]:
        if a and a in DOC["archetypes"]:
            names |= set(DOC["archetypes"][a]["terms"])
    return {k: value for k in sorted(names)}


def wr_line(week, rec_yards, receptions=5, rec_tds=0, downs=40):
    return {"week": week, "rec_yards": rec_yards, "receptions": receptions, "rec_tds": rec_tds, "downs_played": downs}


# ------------------------------------------------------------------ form scoring ----
def test_form_bounded():
    hot = [wr_line(w, 300, receptions=15, rec_tds=3) for w in range(1, 5)]
    cold = [wr_line(1, 300, 15, 3)] + [wr_line(w, 0, receptions=0) for w in range(2, 9)]
    assert fe.form_from_games(hot, "REC", group_mean=40, group_sd=5) == fe.FORM_MAX
    assert fe.form_from_games(cold, "REC", group_mean=200, group_sd=5) == -fe.FORM_MAX
    for games in (hot, cold):
        for mean in (0, 40, 400):
            assert -3 <= fe.form_from_games(games, "REC", mean, 30) <= 3


def test_hot_streak_positive_slump_negative():
    steady = [wr_line(w, 60) for w in range(1, 6)]
    hot = steady + [wr_line(w, 140, 9, 1) for w in range(6, 9)]
    slump = steady + [wr_line(w, 10, 1) for w in range(6, 9)]
    assert fe.form_from_games(hot, "REC", 70, 30) > 0.5
    assert fe.form_from_games(slump, "REC", 70, 30) < -0.5
    assert fe.form_from_games([], "REC", 70, 30) == 0
    assert fe.form_from_games(hot, None, 70, 30) == 0


def test_recent_games_weigh_more():
    early_hot = [wr_line(1, 150, 9, 1)] + [wr_line(w, 60) for w in range(2, 5)]
    late_hot = [wr_line(w, 60) for w in range(1, 4)] + [wr_line(4, 150, 9, 1)]
    assert fe.form_from_games(late_hot, "REC", 70, 30) > fe.form_from_games(early_hot, "REC", 70, 30)


def test_little_playing_time_shrinks_toward_zero():
    full = [wr_line(w, 150, 9, 1, downs=50) for w in range(1, 5)]
    few = [wr_line(w, 150, 9, 1, downs=3) for w in range(1, 5)]
    one = [wr_line(1, 150, 9, 1, downs=50)]
    f_full = fe.form_from_games(full, "REC", 60, 30)
    f_few = fe.form_from_games(few, "REC", 60, 30)
    f_one = fe.form_from_games(one, "REC", 60, 30)
    assert f_full > 0
    assert 0 <= f_few < f_full * 0.3
    assert 0 < f_one < f_full


def test_game_production_matches_season_rate():
    line = {"pass_yards": 280, "pass_tds": 2, "interceptions": 1, "rush_yards": 15, "rush_tds": 0}
    season = {k: v * 6 for k, v in line.items()}
    season["games_played"] = 6
    assert fe.game_production("QB", line) == pytest.approx(progression.production("QB", season))
    assert fe.game_production("K", {"kick_fga": 0, "kick_xpa": 0}) is None
    assert fe.game_production("K", {"kick_fga": 3, "kick_fgm": 3, "kick_xpa": 2, "kick_xpm": 2}) > \
        fe.game_production("K", {"kick_fga": 3, "kick_fgm": 1, "kick_xpa": 2, "kick_xpm": 2})
    assert fe.game_production("P", {"punt_punts": 4, "punt_net_average": 41.5}) == 41.5


def test_forms_from_lines_league():
    lines = {1: [wr_line(w, 150, 9, 1) for w in range(1, 4)],
             2: [wr_line(w, 20, 2) for w in range(1, 4)],
             3: [wr_line(w, 70) for w in range(1, 4)],
             4: [{"week": 1, "pass_yards": 250, "pass_tds": 2}]}
    groups = {1: "REC", 2: "REC", 3: "REC", 4: "QB"}
    forms = fe.forms_from_lines(lines, groups)
    assert forms[1] > 0 > forms[2]
    assert forms[4] == 0                       # alone in his group: at the group mean
    assert all(-3 <= f <= 3 for f in forms.values())


# ------------------------------------------------------------------ fake cursor ----
class FakeCursor:
    def __init__(self, pgids=None, attrs=None, lines=None, line_cols=None):
        self.pgids = pgids or []
        self.attrs = attrs or []
        self.lines = lines or []
        self.line_cols = line_cols or ["week", "player_id", "position"]
        self.calls = []
        self.many = []
        self._rows = []
        self.description = []

    def execute(self, sql, args=None):
        self.calls.append((sql, args))
        if "madden_pgid" in sql:
            self._rows = self.pgids
        elif "FROM player_attributes" in sql:
            self._rows = self.attrs
        elif "game_stat_lines" in sql:
            self._rows = self.lines
            self.description = [(c,) for c in self.line_cols]
        else:
            self._rows = []

    def executemany(self, sql, rows):
        self.many.append((sql, list(rows)))

    def fetchall(self):
        return list(self._rows)


def test_weekly_form_neutral_in_offseason_and_week_zero():
    cur = FakeCursor()
    for week in (0, -1, 24, 30, None):
        assert fe.weekly_form(cur, 2036, week) == {}
    assert cur.calls == []                     # not even a query


def test_weekly_form_reads_game_lines():
    cols = ["week", "player_id", "position", "rec_yards", "receptions", "rec_tds", "downs_played"]
    rows = [(w, 1, "WR", 150, 9, 1, 40) for w in range(1, 4)] + [(w, 2, "WR", 20, 2, 0, 40) for w in range(1, 4)]
    cur = FakeCursor(lines=rows, line_cols=cols)
    forms = fe.weekly_form(cur, 2036, 3)
    assert forms[1] > 0 > forms[2]
    sql, args = cur.calls[0]
    assert "game_stat_lines" in sql and args == (2036, 1, 3)


# ------------------------------------------------------------------ overlay ----
@pytest.mark.parametrize("position", ["WR", "QB", "HB", "CB", "LT", "MLB"])
@pytest.mark.parametrize("form", [-3, -2.4, -1, 0.6, 1.5, 3])
def test_overlay_ovr_effect_equals_form(position, form):
    base = ratings_for(position, 72)
    deltas = fe.overlay(base, position, None, form, DOC)
    new = fe.apply_overlay(base, deltas)
    effect = ow.predict_ovr(DOC, position, new)[0] - ow.predict_ovr(DOC, position, base)[0]
    assert abs(effect - round(form)) <= 1
    assert "Speed" not in deltas and "Acceleration" not in deltas and "Injury" not in deltas
    assert all(0 <= v <= 99 for v in new.values())


def test_overlay_prefers_technical_and_mental():
    base = ratings_for("WR", 72)
    deltas = fe.overlay(base, "WR", "WR_DeepThreat", 3, DOC)
    assert deltas
    assert not set(deltas) & set(progression.PHYSICAL_RATINGS)


def test_overlay_zero_and_unknown_position():
    assert fe.overlay(ratings_for("WR"), "WR", None, 0, DOC) == {}
    assert fe.overlay(ratings_for("WR"), "WR", None, 0.4, DOC) == {}
    assert fe.overlay(ratings_for("WR"), "XX", None, 3, DOC) == {}


def test_overlay_ratings_stay_in_range():
    high = ratings_for("WR", 98)
    low = ratings_for("WR", 1)
    for base, form in ((high, 3), (low, -3)):
        new = fe.apply_overlay(base, fe.overlay(base, "WR", None, form, DOC))
        assert all(0 <= v <= 99 for v in new.values())
    assert fe.apply_overlay({"Catching": 98}, {"Catching": 5}) == {"Catching": 99}
    assert fe.apply_overlay({"Catching": 2}, {"Catching": -5}) == {"Catching": 0}


# ------------------------------------------------------------------ edits document ----
def snapshot(players):
    return {"meta": {"reader_version": 4}, "players": players}


def player(pgid, ratings, confidence=72, position="WR", archetype="WR_DeepThreat"):
    return {"pgid": pgid, "position": position, "archetype": archetype, "confidence": confidence,
            "archetype_grades": [80, 75, 70, 72, 0], "ratings": dict(ratings), "traits": {}}


def test_document_morale_50_for_everyone():
    base = ratings_for("WR", 72)
    snap = snapshot([player(101, base, 72), player(202, base, 99), player(303, base, 50), player(404, base, 10)])
    pgids = {1: 101, 2: 202, 3: 303}           # 404 unknown to Project Mu: morale still pinned
    doc, rows = fe.build_form_document(snap, {1: base, 2: base, 3: base}, {}, pgids, DOC)
    conf = {e["pgid"]: e["set"].get("ConfidenceRating") for e in doc["players"]}
    assert conf == {101: 50, 202: 50, 404: 50}           # 303 already at 50: nothing to write
    assert all(set(e["set"]) == {"ConfidenceRating"} for e in doc["players"])   # form 0, save at base
    assert doc["records"] == []
    assert {r["player_id"] for r in rows} == {1, 2, 3}
    assert all(r["form"] == 0 and r["overlay"] == {} for r in rows)


def test_document_writes_base_plus_overlay_with_ovr_dual_write():
    base = ratings_for("WR", 72)
    snap = snapshot([player(101, base, 50)])
    doc, rows = fe.build_form_document(snap, {1: base}, {1: 2.2}, {1: 101}, DOC)
    (entry,) = doc["players"]
    s = entry["set"]
    deltas = rows[0]["overlay"]
    assert rows[0]["form"] == 2.2 and deltas
    for k, d in deltas.items():
        assert s[f"{k}Rating"] == base[k] + d
    want = fe.apply_overlay(base, deltas)
    assert s["OverallRating"] == ow.predict_ovr(DOC, "WR", want)[0]
    assert s["OverallRating"] - ow.predict_ovr(DOC, "WR", base)[0] == 2
    assert "PlayerType" in s and "OverallGrade0" in s
    assert "SpeedRating" not in s and "AccelerationRating" not in s


def test_form_zero_restores_base_ratings():
    base = ratings_for("WR", 72)
    last_week = fe.apply_overlay(base, fe.overlay(base, "WR", None, 3, DOC))
    assert last_week != base
    snap = snapshot([player(101, last_week, 50)])
    doc, rows = fe.build_form_document(snap, {1: base}, {1: 0.0}, {1: 101}, DOC)
    (entry,) = doc["players"]
    for k, v in base.items():
        if last_week[k] != v:
            assert entry["set"][f"{k}Rating"] == v
    assert entry["set"]["OverallRating"] == ow.predict_ovr(DOC, "WR", base)[0]
    assert rows == [{"player_id": 1, "form": 0.0, "overlay": {}}]


def test_document_skips_players_without_base_ratings():
    base = ratings_for("WR", 72)
    warnings = []
    doc, rows = fe.build_form_document(snapshot([player(101, base, 50)]), {}, {1: 3}, {1: 101}, DOC,
                                       warnings=warnings)
    assert doc["players"] == [] and rows == []
    assert warnings and "player_attributes" in warnings[0]


# ------------------------------------------------------------------ storing ----
def test_store_form_replaces_the_week():
    cur = FakeCursor()
    n = fe.store_form(cur, 2036, 5, [{"player_id": 1, "form": 2.345, "overlay": {"Catching": 2}},
                                     {"player_id": 2, "form": -7, "overlay": {}}])
    assert n == 2
    sql, args = cur.calls[0]
    assert sql.startswith("DELETE FROM player_form") and args == (2036, 5)
    (ins, rows), = cur.many
    assert "INSERT INTO player_form (player_id, season, week, form, overlay)" in ins and "::jsonb" in ins
    assert rows[0] == (1, 2036, 5, 2.35, json.dumps({"Catching": 2}))
    assert rows[1] == (2, 2036, 5, -3.0, "{}")          # clamped to the column's check


def test_form_edits_with_fake_cursor_stores_and_dry_run_doesnt():
    base = ratings_for("WR", 72)
    snap = snapshot([player(101, base, 80), player(202, base, 50)])
    cols = ["week", "player_id", "position", "rec_yards", "receptions", "rec_tds", "downs_played"]
    # weeks 1-4 average for everyone; then player 1 gets hot and player 2 goes cold, among 10 average receivers
    lines = [(w, pid, "WR", 55 + 3 * pid + 4 * (w % 3), 5, 0, 45) for pid in range(1, 13) for w in range(1, 5)]
    lines += [(w, 1, "WR", 170, 10, 2, 45) for w in range(5, 9)] + [(w, 2, "WR", 5, 1, 0, 45) for w in range(5, 9)]
    lines += [(w, pid, "WR", 55 + 3 * pid + 4 * (w % 3), 5, 0, 45) for pid in range(3, 13) for w in range(5, 9)]

    def cursor():
        return FakeCursor(pgids=[(1, 101), (2, 202)], attrs=[(1, base), (2, base)], lines=lines, line_cols=cols)

    cur = cursor()
    doc = fe.form_edits(cur, snap, 2036, 8)
    sets = {e["pgid"]: e["set"] for e in doc["players"]}
    assert sets[101]["ConfidenceRating"] == 50
    assert sets[101]["OverallRating"] > ow.predict_ovr(DOC, "WR", base)[0]
    assert sets[202]["OverallRating"] < ow.predict_ovr(DOC, "WR", base)[0]
    assert any(sql.startswith("DELETE FROM player_form") for sql, _ in cur.calls)
    (_, rows), = cur.many
    assert {r[0] for r in rows} == {1, 2} and all(r[1:3] == (2036, 8) for r in rows)

    dry = cursor()
    assert fe.form_edits(dry, snap, 2036, 8, store=False) == doc
    assert not any("player_form" in sql for sql, _ in dry.calls) and dry.many == []

    off = cursor()                              # offseason: everyone back to base, rows with overlay {}
    doc_off = fe.form_edits(off, snap, 2036, 26)
    assert all(set(e["set"]) <= {"ConfidenceRating"} for e in doc_off["players"])
    (_, rows_off), = off.many
    assert all(r[3] == 0 and r[4] == "{}" for r in rows_off)


def test_cli_dry_run(tmp_path):
    base = ratings_for("WR", 72)
    snap_path = tmp_path / "save.json"
    snap_path.write_text(json.dumps(snapshot([player(101, base, 80)])))
    out = tmp_path / "edits.json"
    cur = FakeCursor(pgids=[(1, 101)], attrs=[(1, base)])
    conn = mock.MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    fake_pg = types.SimpleNamespace(connect=mock.MagicMock(return_value=conn))
    with mock.patch.dict(sys.modules, {"psycopg": fake_pg}):
        rc = fe.main(["--snapshot", str(snap_path), "--season", "2036", "--week", "3", "--out", str(out),
                      "--dsn", "dbname=madden_test", "--dry-run"])
    assert rc == 0
    edits = json.loads(out.read_text())
    assert edits["players"] == [{"pgid": 101, "set": {"ConfidenceRating": 50}}]
    assert not any("player_form" in sql for sql, _ in cur.calls)
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()
