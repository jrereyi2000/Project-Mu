"""Front Office helpers (docs/done/team_management/03_GM_AGENTS_PLAN.md Phase 5). Pure + fake cursors: no DB."""
from unittest import mock

import pytest

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


class ScriptedCursor:
    """Answers each SELECT with the rows of the first matching needle; records every statement."""

    def __init__(self, answers):
        self.answers, self.sql, self.rows = answers, [], []

    def execute(self, q, args=None):
        self.sql.append(" ".join(q.split()))
        self.rows = next((rows for needle, rows in self.answers if needle in q), [])

    def fetchone(self):
        return self.rows[0] if self.rows else (None,)

    def fetchall(self):
        return self.rows


def registry(*entries):
    return {"active": None, "franchises": {f"f{i}": e for i, e in enumerate(entries)}}


def test_controlled_team_ids_reads_the_registry_entry_for_this_database():
    cur = ScriptedCursor([("current_database", [("madden_colts",)]), ("FROM teams", [(7, "IND"), (9, "CAR")])])
    reg = registry({"db": "madden_colts", "user_teams": ["IND", "CAR"]},
                   {"db": "madden_other", "user_teams": []})
    with mock.patch("db.franchises.load_registry", return_value=reg):
        assert fo.controlled_team_ids(cur) == {7, 9}
    cur = ScriptedCursor([("current_database", [("madden_colts",)]), ("FROM teams", [(7, "IND")])])
    with mock.patch("db.franchises.load_registry", return_value=registry({"db": "madden_colts", "user_teams": []})):
        assert fo.controlled_team_ids(cur) == set()          # the all-CPU era: nobody is JR's
    cur = ScriptedCursor([("current_database", [("madden_2036",)])])
    with mock.patch("db.franchises.load_registry", return_value=registry({"db": "madden_colts", "user_teams": ["IND"]})):
        assert fo.controlled_team_ids(cur) is None           # no entry for this database: the old fallback
    cur = ScriptedCursor([("current_database", [("madden_colts",)])])
    with mock.patch("db.franchises.load_registry", side_effect=OSError("unreadable")):
        assert fo.controlled_team_ids(cur) is None


def test_set_team_control_refuses_user_controlled_on_a_registered_franchise():
    cur = ScriptedCursor([("INSERT INTO gm_team_settings", [(5, False, True)])])
    with mock.patch("engines.front_office.controlled_team_ids", return_value={5}):
        with pytest.raises(fo.FrontOfficeError) as e:
            fo.set_team_control(cur, 5, user_controlled=True)
        assert e.value.status == 409
        assert "python3 -m db.franchises" in e.value.message
        assert not any(q.startswith("INSERT") for q in cur.sql)      # nothing was written
        assert fo.set_team_control(cur, 5, suggestions_on=False) == {
            "team_id": 5, "user_controlled": False, "suggestions_on": True}
    cur = ScriptedCursor([("INSERT INTO gm_team_settings", [(5, True, True)])])
    with mock.patch("engines.front_office.controlled_team_ids", return_value=None):
        assert fo.set_team_control(cur, 5, user_controlled=True)["user_controlled"] is True


def test_front_office_state_reports_control_from_the_registry_overlay():
    teams = [{"team_id": 7, "user_controlled": False}, {"team_id": 9, "user_controlled": True}]
    fo._apply_control(teams, {7})
    assert [t["user_controlled"] for t in teams] == [True, False]
    teams = [{"team_id": 7, "user_controlled": False}]
    fo._apply_control(teams, None)                                  # no registry entry: the stored fallback stands
    assert teams[0]["user_controlled"] is False


LEAK_DETAIL = {"teams": ["NYJ", "NE"], "initiator": "NYJ",
               "positions": [{"pos": "CB", "from": "NE", "to": "NYJ"}], "picks": 0}


def test_trade_rumors_add_leak_rows_and_drop_the_leaked_proposal():
    cur = ScriptedCursor([
        ("FROM gm_trade_proposals p", [(11, 5, "NYJ", "NE", "STALLED", "NEED", ["talks:41"])]),
        ("to_regclass('gm_trade_leaks')", [(True,)]),
        ("FROM gm_trade_leaks", [(3, 41, 5, "STALLED", "SCHEFTER_TWEET", LEAK_DETAIL, [77])]),
        ("FROM players p", [(77, "Tre Reyes", "CB")]),
    ])
    out = fo.trade_rumors(cur, 2036, 6)
    assert [r["rumor_id"] for r in out] == ["leak:3"]               # the STALLED proposal is not a second rumor
    leak = out[0]
    assert (leak["source"], leak["stage"], leak["insider_key"]) == ("LEAK", "STALLED", "SCHEFTER_TWEET")
    assert leak["named"] == [{"player_id": 77, "name": "Tre Reyes", "position": "CB"}]
    assert leak["assets"] == [{"type": "PLAYER", "position": "CB", "from": "NE", "to": "NYJ"}]
    assert leak["status"] == "RUMOR" and leak["teams"] == ["NYJ", "NE"]


def test_trade_rumors_without_the_leak_table_are_plain():
    leg = (12, "PLAYER", "NYJ", "NE", 77, "Tre Reyes", "CB", 80, None, None, None, None, 1, 2, None)
    cur = ScriptedCursor([
        ("FROM gm_trade_proposals p", [(12, 5, "NYJ", "NE", "PROPOSED", "NEED", [])]),
        ("to_regclass('gm_trade_leaks')", [(False,)]),
        ("FROM gm_trade_assets", [leg]),
    ])
    out = fo.trade_rumors(cur, 2036, 6)
    assert out == [{"rumor_id": 12, "week": 5, "teams": ["NYJ", "NE"], "initiator": "NYJ", "trigger": "NEED",
                    "assets": [{"type": "PLAYER", "position": "CB", "from": "NYJ", "to": "NE"}],
                    "status": "RUMOR"}]
    assert not any(q.startswith("SELECT l.id") for q in cur.sql)     # the leaks table was never read


TRADE_ROW = (55, 6, "NYJ", "NE", "2026-11-01", "PROPOSED", "ENGINE", "headline", None, None, "IN_SEASON",
             ["talks:41"])
TRADE_LEG = (55, "PLAYER", "NE", "NYJ", 77, "Tre Reyes", "CB", 80, None, None, None, None, 17, 18, None)


def test_executed_trades_carry_quotes_from_a_published_talk():
    cur = ScriptedCursor([
        ("FROM gm_trade_proposals p", [TRADE_ROW]),
        ("to_regclass('gm_trade_leaks')", [(True,)]),
        ("SELECT id FROM gm_trade_negotiations", [(41,)]),
        ("JOIN gm_trade_negotiation_turns", [
            (41, 18, 17, "GM_A", "We want him here.", "TEMPLATE", 1),
            (41, 18, 17, "GM", "Fine by us.", "LLM", 2),
            (41, 18, 17, "GM", "Deal.", "LLM", 3),
            (41, 18, 17, "JR", "ok", "JR", 4)]),
        ("FROM gm_profiles", [(18, "Silas Whitlock"), (17, "Dana Okonkwo")]),
        ("FROM gm_trade_assets", [TRADE_LEG]),
    ])
    trade = fo.executed_trades(cur, 2036)[0]
    assert trade["talk_id"] == 41
    assert trade["talk_quotes"] == [
        {"gm": "Dana Okonkwo", "line": "Fine by us.", "source": "LLM"},
        {"gm": "Dana Okonkwo", "line": "Deal.", "source": "LLM"},
        {"gm": "Silas Whitlock", "line": "We want him here.", "source": "TEMPLATE"}]


def test_executed_trades_without_a_published_talk_carry_no_quotes():
    cur = ScriptedCursor([
        ("FROM gm_trade_proposals p", [TRADE_ROW]),
        ("to_regclass('gm_trade_leaks')", [(True,)]),
        ("SELECT id FROM gm_trade_negotiations", []),               # not published
        ("FROM gm_trade_assets", [TRADE_LEG]),
    ])
    trade = fo.executed_trades(cur, 2036)[0]
    assert "talk_id" not in trade and "talk_quotes" not in trade

    plain = tuple(TRADE_ROW[:11]) + ([],)
    cur = ScriptedCursor([("FROM gm_trade_proposals p", [plain]), ("FROM gm_trade_assets", [TRADE_LEG])])
    trade = fo.executed_trades(cur, 2036)[0]
    assert "talk_quotes" not in trade and not any("gm_trade_leaks" in q for q in cur.sql)
