"""pipeline/load_attributes.py: scan-row matching (unit) and a load into madden_test that is always rolled back
(integration)."""
import csv
import json

import psycopg
import pytest

from pipeline import load_attributes as la


def row(name, pos, age=25, ovr=80, n=1):
    return {"team": "PANTHERS", "row": n, "name": name, "pos": pos, "age": age, "ovr": ovr, "predicted_ovr": ovr,
            "ovr_check": "ok", "confirmed": 54, "flags": "", "ratings": {"Speed": 90}}


def cand(pid, name, pos, age=25):
    return {"player_id": pid, "name": name, "position": pos, "age": age}


def test_match_by_list_name():
    matched, left = la.match_rows([row("T.DAVIS", "WR"), row("G.CRAWFORD", "QB")],
                                  [cand(1, "T. Davis", "WR"), cand(2, "G. Crawford", "QB"), cand(3, "X. Other", "CB")])
    assert [(r["name"], pid, how) for r, pid, how in matched] == [("T.DAVIS", 1, "name"), ("G.CRAWFORD", 2, "name")]
    assert left == []


def test_shared_key_decided_by_position_then_age():
    cands = [cand(1, "N. Johnson", "LG", 21), cand(2, "N. Johnson", "CB", 24), cand(3, "N. Johnson", "CB", 27)]
    matched, _ = la.match_rows([row("N.JOHNSON", "CB", age=27), row("N.JOHNSON", "LG", age=21)], cands)
    assert [pid for _, pid, _ in matched] == [3, 1]


def test_one_slip_needs_the_same_position():
    cands = [cand(1, "D. Wilson", "SS")]
    matched, left = la.match_rows([row("D.WILBON", "SS")], cands)
    assert [(pid, how) for _, pid, how in matched] == [(1, "one slip")]
    matched, left = la.match_rows([row("D.WILBON", "CB")], cands)
    assert matched == [] and len(left) == 1


def test_one_to_one():
    matched, left = la.match_rows([row("T.DAVIS", "WR"), row("T.DAVIS", "WR", n=2)], [cand(1, "T. Davis", "WR")])
    assert len(matched) == 1 and len(left) == 1


def test_read_csv(tmp_path):
    p = tmp_path / "attributes.csv"
    with open(p, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(list(la.META) + ["Speed", "Strength"])
        w.writerow(["PANTHERS", 1, "T.DAVIS", "WR", 25, 99, 105, "ok", 54, "", 99, ""])
    (r,) = la.read_csv(str(p))
    assert r["age"] == 25 and r["ovr"] == 99 and r["confirmed"] == 54
    assert r["ratings"] == {"Speed": 99}                   # blank cells are left out


@pytest.mark.integration
def test_load_replaces_the_same_scan_and_keeps_others():
    conn = psycopg.connect(la.DSN)
    cur = conn.cursor()
    try:
        cur.execute("SELECT to_regclass('player_attributes')")
        if cur.fetchone()[0] is None:
            pytest.skip("madden_test has no player_attributes (db/migrations/2027_01_06_player_attributes.sql)")
        cur.execute("""SELECT ps.season, t.name, p.player_name, ps.position, ps.age, ps.player_id
                       FROM player_seasons ps JOIN players p ON p.id = ps.player_id JOIN teams t ON t.id = ps.team_id
                       WHERE ps.position IS NOT NULL ORDER BY ps.season DESC, ps.id LIMIT 1""")
        season, team, name, pos, age, pid = cur.fetchone()
        first, last = name.split(" ", 1)
        scan = [dict(row(f"{first}{last}".upper(), pos, age), team=team.upper())]
        t = la.load(cur, "test_scan_a", scan, season, log=lambda *_: None)
        assert t["loaded"] == 1 and t["unmatched"] == 0
        scan[0]["ratings"] = {"Speed": 70}
        la.load(cur, "test_scan_a", scan, season, log=lambda *_: None)          # same scan again: replaced
        la.load(cur, "test_scan_b", scan, season, log=lambda *_: None)          # another scan: kept beside it
        cur.execute("SELECT capture_folder, ratings FROM player_attributes WHERE player_id = %s "
                    "AND capture_folder LIKE 'test_scan_%%' ORDER BY capture_folder", (pid,))
        got = cur.fetchall()
        assert [f for f, _ in got] == ["test_scan_a", "test_scan_b"]
        assert all((r if isinstance(r, dict) else json.loads(r)) == {"Speed": 70} for _, r in got)
    finally:
        conn.rollback()
        conn.close()
