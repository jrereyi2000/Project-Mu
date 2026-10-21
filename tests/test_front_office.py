"""Front Office helpers (docs/team_management/03_GM_AGENTS_PLAN.md Phase 5). Pure + fake cursors: no DB."""
from engines import front_office as fo

NICK = {"PANTHERS": "CAR", "SAINTS": "NO", "FALCONS": "ATL"}


def parsed(team="PANTHERS", to="SAINTS", names=("ANN ABLE",)):
    return {"team": team, "to_team": to, "legs": [{"pos": "WR", "name": n, "from": team, "to": to} for n in names],
            "assets": []}


CANDS = [{"id": 7, "teams": {"CAR", "NO"}, "player_keys": {"ANNABLE", "BOBELL"}},
         {"id": 8, "teams": {"CAR", "NO"}, "player_keys": {"ANNABLE"}}]


def test_matcher_matched_partial_unmatched():
    assert fo.match_sighting(parsed(), CANDS, NICK) == ("MATCHED", 8)
    assert fo.match_sighting(parsed(names=("ANN ABLE", "BO BELL")), CANDS, NICK) == ("MATCHED", 7)
    assert fo.match_sighting(parsed(names=("BO BELL",)), CANDS, NICK) == ("PARTIAL", 7)
    assert fo.match_sighting(parsed(to="FALCONS"), CANDS, NICK) == ("UNMATCHED", None)
    assert fo.match_sighting(parsed(names=("CY DOE",)), CANDS, NICK) == ("UNMATCHED", None)


def test_matcher_merges_one_row_per_asset():
    # Madden's week-5 Packers/Bengals log: one row per asset, so no single row is the whole trade
    nick = {"PACKERS": "GB", "BENGALS": "CIN"}
    cands = [{"id": 9, "teams": {"GB", "CIN"}, "player_keys": {"MIKEKEATON", "MITCHROBY"},
              "pick_keys": {(2037, 5, "CIN")}}]
    rows = [{"team": "BENGALS", "to_team": "PACKERS", "legs": [],
             "assets": [{"asset": "2037 ROUND 5 (155) DRAFT PICK", "from": "BENGALS", "to": "PACKERS"}]},
            {"team": "PACKERS", "to_team": "BENGALS", "legs": [{"name": "MIKE KEATON", "from": "PACKERS"}],
             "assets": []},
            {"team": "BENGALS", "to_team": "PACKERS", "legs": [{"name": "MITCH ROBY", "from": "BENGALS"}],
             "assets": []}]
    assert len({fo.sighting_group(r) for r in rows}) == 1
    assert fo.match_sighting(rows[1], cands, nick) == ("PARTIAL", 9)
    assert fo.match_sighting(fo.merge_sightings(rows), cands, nick) == ("MATCHED", 9)
    assert fo.match_sighting(fo.merge_sightings(rows[1:]), cands, nick) == ("PARTIAL", 9)   # the pick is missing
    assert fo.match_sighting(rows[0], cands, nick) == ("PARTIAL", 9)


def test_pick_from_text():
    assert fo.pick_from_text("2037 ROUND 5 (155) DRAFT PICK") == (2037, 5)
    assert fo.pick_from_text("2037 1ST ROUND PICK") == (2037, 1)
    assert fo.pick_from_text("2038 SEVENTH ROUND DRAFT PICK") == (2038, 7)
    assert fo.pick_from_text("garbage") is None


class ReadOnlyCursor:
    """Answers every SELECT with no rows; records every statement."""

    def __init__(self):
        self.sql = []

    def execute(self, q, args=None):
        self.sql.append(" ".join(q.split()))

    def fetchone(self):
        return (None,)

    def fetchall(self):
        return []


def test_flags_never_create_write_ops():
    cur = ReadOnlyCursor()
    assert fo.consistency_flags(cur, 2036) == []
    assert cur.sql and all(q.upper().startswith("SELECT") for q in cur.sql)


def test_rumors_are_never_done_deals():
    cur = ReadOnlyCursor()
    assert fo.trade_rumors(cur, 2036, 6) == []
    q = cur.sql[0]
    assert "p.status <> 'EXECUTED'" in q and "p.rumor_ok" in q and "p.week <= %s" in q


class SightingCursor(ReadOnlyCursor):
    def __init__(self, installed):
        super().__init__()
        self.installed = installed
        self.rowcount = 1

    def fetchone(self):
        return (self.installed,)


def test_store_sightings_only_when_table_exists():
    rows = [{"heading": "2036 SEASON - WEEK 4", "text": "PANTHERS TRADE (WR) ANN ABLE TO SAINTS"}]
    cur = SightingCursor(False)
    assert fo.store_sightings(cur, 2036, rows) == 0 and not any(q.startswith("INSERT") for q in cur.sql)
    cur = SightingCursor(True)
    assert fo.store_sightings(cur, 2036, rows) == 1
    assert sum(q.startswith("INSERT INTO gm_trade_sightings") for q in cur.sql) == 1


def test_steps_for_user_controlled_team():
    p = {"team_a_id": 11, "team_b_id": 29}
    legs = [{"type": "PLAYER", "from_team_id": 11, "to_team_id": 29, "position": "WR", "player_name": "Ann Able",
             "ovr": 80}, {"type": "PICK", "from_team_id": 29, "to_team_id": 11, "label": "2037 R2 (NO)"}]
    steps = fo.steps_for(p, legs, {11: "Carolina Panthers", 29: "New Orleans Saints"}, {11: True})
    assert steps[0] == "Options > User Teams: you already control the Carolina Panthers."
    assert "Send: WR Ann Able 80. Receive: 2037 R2 (NO)." in steps[1]
    assert steps[-1] == "Click Made it."
