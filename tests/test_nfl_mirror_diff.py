"""engines/nfl_mirror/diff and contracts (NFL_WEEKLY_MIRROR_PLAN.md §2.5, §4). Inline fixtures, no network, no DB."""
import pyarrow as pa
import pyarrow.parquet as pq

from engines import contracts_engine as ce
from engines.nfl_history import link
from engines.nfl_mirror import contracts, diff
from engines.nfl_mirror.contracts import ContractBook
from engines.nfl_mirror.real_state import RealPlayer, RealWeek
from engines.nfl_mirror.trades import TradeLeg

TEAMS = {"KC": 0, "BUF": 1, "MIA": 2}
CAP = ce.CBA["REFERENCE_CAP"]


# ---------- fixtures ----------

def real_player(gid, name, team, position="WR", status="ACTIVE", designation="A01", entry_year=2020,
                draft_number=None, college=None):
    return RealPlayer(gsis_id=gid, name=name, position=position, team=team, status=status,
                      designation=designation, entry_year=entry_year, draft_number=draft_number,
                      college=college)


def real_week(rosters, week=10):
    return RealWeek(season=2026, week=week, mode="CATCH_UP", rosters=rosters)


def madden(pgid, first, last, team_index, status="Signed", position="WR", years_pro=0,
           injured_reserve=False, year_drafted=None, college=None):
    return {"pgid": pgid, "first_name": first, "last_name": last, "position": position, "status": status,
            "team_index": team_index, "years_pro": years_pro, "overall": 70, "age": 26,
            "injury": {"injured_reserve": injured_reserve}, "year_drafted": year_drafted,
            "college": college, "height_in": None, "weight_lbs": None}


def snapshot(*players):
    return {"players": list(players)}


def trade_leg(gsis="00-c", week=None, from_team="KC", to_team="BUF", trade_id="T1", note=None):
    return TradeLeg(trade_id, "2026-10-28", week, from_team, to_team, gsis, "Trade Man", note)


def applied(snapshot_, result):
    """The snapshot after `result`'s roster ops, for replanning the same week (zero further moves)."""
    players = {p["pgid"]: dict(p) for p in snapshot_["players"]}
    for op in result.ops:
        p = players[op["pgid"]]
        if op["kind"] == "release":
            p["team_index"], p["status"] = diff.FREE_AGENT_TEAM, "FreeAgent"
        elif op["kind"] in ("sign", "trade"):
            p["team_index"] = op["to_index"]
            p["status"] = "Signed" if op["list"] == "Roster" else "PracticeSquad"
        elif op["kind"] == "list":
            p["status"] = "Signed" if op["list"] == "Roster" else "PracticeSquad"  # same team, other list
        elif op["kind"] == "ir":
            p["injury"] = {**(p.get("injury") or {}), "injured_reserve": True}
        elif op["kind"] == "activate":
            p["injury"] = {**(p.get("injury") or {}), "injured_reserve": False}
    return snapshot(*players.values())


def plan(snapshot_, real, links, trades=(), contracts_=None, cap=CAP):
    return diff.plan_rosters(snapshot_, real, links, list(trades), TEAMS,
                             contracts_ if contracts_ is not None else ContractBook({}), cap)


def ops_for(result, pgid=None, kind=None):
    return [o for o in result.ops
            if (pgid is None or o["pgid"] == pgid) and (kind is None or o["kind"] == kind)]


# ---------- ops ----------

def test_release_a_player_the_real_week_does_not_roster():
    snap = snapshot(madden(100, "Cut", "Guy", 0))
    real = real_week({"KC": [real_player("00-other", "Some Body", "KC")]})
    result = plan(snap, real, {"00-a": 100})
    releases = ops_for(result, kind="release")
    assert [(o["pgid"], o["gsis_id"], o["to_index"]) for o in releases] == [(100, "00-a", None)]
    assert not ops_for(result, pgid=100, kind="sign")
    assert not ops_for(result, pgid=100, kind="trade")


def test_sign_a_free_agent_on_the_minimum():
    snap = snapshot(madden(101, "Free", "Agent", diff.FREE_AGENT_TEAM, status="FreeAgent"))
    real = real_week({"BUF": [real_player("00-b", "Free Agent", "BUF", entry_year=2020)]})
    result = plan(snap, real, {"00-b": 101})
    signs = ops_for(result, kind="sign")
    assert len(signs) == 1
    op = signs[0]
    assert (op["pgid"], op["gsis_id"], op["to_index"], op["list"]) == (101, "00-b", 1, "Roster")
    contract = op["contract"]
    assert contract["length"] == 1 and contract["contract_type"] == "MINIMUM"
    assert contract["salary_by_year"] == [ce.minimum_salary(6, CAP)]
    assert contract["bonus_by_year"] == [0] and contract["guaranteed"] == 0
    assert set(contract) == {"length", "current_year", "salary_by_year", "bonus_by_year", "guaranteed",
                             "contract_type", "signed_season", "last_restructure_season", "drafted_round"}


def test_csv_trade_beats_inference():
    snap = snapshot(madden(102, "Trade", "Man", 0))
    real = real_week({"BUF": [real_player("00-c", "Trade Man", "BUF")], "KC": []})
    leg = TradeLeg("T1", "2026-10-28", 10, "KC", "BUF", "00-c", "Trade Man", None)
    result = plan(snap, real, {"00-c": 102}, [leg])
    assert [o["kind"] for o in result.ops] == ["trade"]
    op = result.ops[0]
    assert (op["pgid"], op["to_index"], op["list"]) == (102, 1, "Roster")
    assert "T1" in op["why"]


def test_team_change_without_a_csv_leg_is_release_then_sign():
    snap = snapshot(madden(102, "Trade", "Man", 0))
    real = real_week({"BUF": [real_player("00-c", "Trade Man", "BUF")], "KC": []})
    result = plan(snap, real, {"00-c": 102})
    assert [o["kind"] for o in ops_for(result, pgid=102)] == ["release", "sign"]
    assert ops_for(result, pgid=102, kind="sign")[0]["to_index"] == 1
    assert "unconfirmed" in ops_for(result, pgid=102, kind="release")[0]["why"]


def test_trade_leg_for_another_week_is_ignored():
    snap = snapshot(madden(102, "Trade", "Man", 0))
    real = real_week({"BUF": [real_player("00-c", "Trade Man", "BUF")]})
    leg = TradeLeg("T1", "2026-10-28", 9, "KC", "BUF", "00-c", "Trade Man", None)
    result = plan(snap, real, {"00-c": 102}, [leg])
    assert [o["kind"] for o in ops_for(result, pgid=102)] == ["release", "sign"]


# ---------- trades: an applicable leg, not a lifetime exemption ----------

def test_blank_week_leg_does_not_suppress_a_later_cut():
    """F1a: a blank-week KC->BUF leg cannot protect a player the real week released (one release)."""
    snap = snapshot(madden(110, "Trade", "Man", 1))                    # already BUF
    real = real_week({})
    result = plan(snap, real, {"00-c": 110}, [trade_leg()])
    assert [o["kind"] for o in result.ops] == ["release"]
    assert not ops_for(result, kind="trade") and not ops_for(result, kind="sign")
    assert plan(applied(snap, result), real, {"00-c": 110}, [trade_leg()]).ops == []


def test_blank_week_leg_does_not_suppress_a_later_practice_squad_promotion():
    """F1b: a stale blank-week leg falls through to one same-team list change, which ends on the Roster."""
    snap = snapshot(madden(111, "Trade", "Man", 1, status="PracticeSquad"))   # BUF PS
    real = real_week({"BUF": [real_player("00-c", "Trade Man", "BUF")]})      # BUF ACTIVE
    leg = trade_leg()
    result = plan(snap, real, {"00-c": 111}, [leg])
    assert [(o["kind"], o["list"]) for o in result.ops] == [("list", "Roster")]
    assert not ops_for(result, kind="trade")
    assert plan(applied(snap, result), real, {"00-c": 111}, [leg]).ops == []


def test_leg_for_another_destination_is_unconfirmed_release_and_sign():
    """F1c: a KC->BUF leg cannot authorize a real KC->MIA switch (zero trades, release + sign, unconfirmed)."""
    snap = snapshot(madden(112, "Trade", "Man", 0))                      # KC
    real = real_week({"MIA": [real_player("00-c", "Trade Man", "MIA")]})  # MIA
    leg = trade_leg()                                                    # KC -> BUF
    result = plan(snap, real, {"00-c": 112}, [leg])
    moves = [o for o in result.ops if o["pgid"] == 112]
    assert not ops_for(result, kind="trade")
    assert [o["kind"] for o in moves] == ["release", "sign"]
    assert moves[1]["to_index"] == 2 and moves[1]["list"] == "Roster"
    assert "unconfirmed" in moves[0]["why"]
    assert plan(applied(snap, result), real, {"00-c": 112}, [leg]).ops == []


def test_current_week_leg_moves_the_player_on_canonical_teams():
    """A matching KC->BUF leg in the real week is one trade and still reads as the CSV row."""
    snap = snapshot(madden(113, "Trade", "Man", 0))
    real = real_week({"BUF": [real_player("00-c", "Trade Man", "BUF")]})
    leg = trade_leg(week=10)
    result = plan(snap, real, {"00-c": 113}, [leg])
    assert [o["kind"] for o in result.ops] == ["trade"]
    op = result.ops[0]
    assert (op["pgid"], op["to_index"], op["list"]) == (113, 1, "Roster")
    assert op["why"] == "trade T1"
    assert plan(applied(snap, result), real, {"00-c": 113}, [leg]).ops == []


def test_leg_for_a_player_already_at_the_destination_is_not_a_trade():
    snap = snapshot(madden(114, "Trade", "Man", 1))                      # BUF, Roster
    real = real_week({"BUF": [real_player("00-c", "Trade Man", "BUF")]})
    result = plan(snap, real, {"00-c": 114}, [trade_leg()])
    assert result.ops == []


def test_player_already_at_the_destination_still_reconciles_his_list():
    """Being at the leg's destination is not 'handled': a list change is still reconciled."""
    snap = snapshot(madden(115, "Trade", "Man", 1, status="PracticeSquad"))   # BUF PS
    real = real_week({"BUF": [real_player("00-c", "Trade Man", "BUF")]})      # BUF ACTIVE
    result = plan(snap, real, {"00-c": 115}, [trade_leg()])
    assert not ops_for(result, kind="trade")
    assert [(o["kind"], o["list"], o["to_index"]) for o in result.ops] == [("list", "Roster", 1)]
    assert result.ops[0]["contract"] is not None     # a promotion gets the same new deal a signing would
    assert plan(applied(snap, result), real, {"00-c": 115}, [trade_leg()]).ops == []


def test_csv_leg_arriving_on_a_changed_list_is_one_trade():
    snap = snapshot(madden(116, "Trade", "Man", 0, status="PracticeSquad"))   # KC PS
    real = real_week({"BUF": [real_player("00-c", "Trade Man", "BUF")]})      # BUF ACTIVE
    leg = trade_leg(week=10)
    result = plan(snap, real, {"00-c": 116}, [leg])
    assert [o["kind"] for o in result.ops] == ["trade"]
    assert (result.ops[0]["to_index"], result.ops[0]["list"]) == (1, "Roster")
    assert result.ops[0]["contract"] is not None     # the promotion's new deal travels in the trade op
    assert plan(applied(snap, result), real, {"00-c": 116}, [leg]).ops == []


def test_same_team_demotion_is_one_list_change():
    """A practice-squad demotion keeps him on his team instead of releasing him into free agency."""
    snap = snapshot(madden(120, "Bench", "Man", 1, status="Signed"))           # BUF active
    real = real_week({"BUF": [real_player("00-h", "Bench Man", "BUF", status="PS", designation="P01")]})
    result = plan(snap, real, {"00-h": 120})
    assert [(o["kind"], o["list"], o["to_index"], o["contract"]) for o in result.ops] == [
        ("list", "PracticeSquad", 1, None)]
    assert plan(applied(snap, result), real, {"00-h": 120}).ops == []


def test_same_team_promotion_and_demotion_are_not_a_release():
    """Neither direction goes through free agency, and the replan after either is empty."""
    active = snapshot(madden(121, "Promote", "Man", 0, status="PracticeSquad"),
                      madden(122, "Demote", "Man", 0, status="Signed"))
    real = real_week({"KC": [real_player("00-i", "Promote Man", "KC"),
                             real_player("00-j", "Demote Man", "KC", status="PS", designation="P01")]})
    links = {"00-i": 121, "00-j": 122}
    result = plan(active, real, links)
    assert not ops_for(result, kind="release") and not ops_for(result, kind="sign")
    assert sorted((o["kind"], o["pgid"], o["list"]) for o in result.ops) == [
        ("list", 121, "Roster"), ("list", 122, "PracticeSquad")]
    assert plan(applied(active, result), real, links).ops == []


def test_leg_with_a_stale_source_team_falls_through_to_release_and_sign():
    snap = snapshot(madden(117, "Trade", "Man", 2))                      # MIA, but the leg starts at KC
    real = real_week({"BUF": [real_player("00-c", "Trade Man", "BUF")]})
    result = plan(snap, real, {"00-c": 117}, [trade_leg()])
    assert not ops_for(result, kind="trade")
    assert [o["kind"] for o in result.ops] == ["release", "sign"]


def test_multiple_legs_apply_only_the_one_that_matches_the_real_week():
    snap = snapshot(madden(119, "Trade", "Man", 0))
    real = real_week({"BUF": [real_player("00-c", "Trade Man", "BUF")]})
    legs = [trade_leg(trade_id="T-old", to_team="MIA"), trade_leg(trade_id="T1")]
    result = plan(snap, real, {"00-c": 119}, legs)
    assert [o["kind"] for o in result.ops] == ["trade"]
    assert result.ops[0]["to_index"] == 1 and "T1" in result.ops[0]["why"]


def test_leg_for_a_player_not_linked_into_the_snapshot_is_left_to_the_signing_step():
    snap = snapshot(madden(118, "Some", "One", 0))
    real = real_week({"BUF": [real_player("00-c", "Trade Man", "BUF")]})
    result = plan(snap, real, {}, [trade_leg()])
    assert not ops_for(result, kind="trade")
    assert [p.gsis_id for p in result.creates] == ["00-c"]


def test_practice_squad_signing_and_ps_fill():
    snap = snapshot(madden(103, "Squad", "Man", diff.FREE_AGENT_TEAM, status="FreeAgent"))
    real = real_week({
        "MIA": [real_player("00-d", "Squad Man", "MIA", status="PS", designation="P01"),
                real_player("00-e", "Missing Squad", "MIA", status="PS", designation="P02")],
    })
    result = plan(snap, real, {"00-d": 103})
    ps = ops_for(result, pgid=103, kind="sign")
    assert len(ps) == 1
    assert (ps[0]["list"], ps[0]["to_index"], ps[0]["contract"]) == ("PracticeSquad", 2, None)
    assert result.ps_fill == [{"team_index": 2, "position": "WR"}]
    assert result.creates == []


def test_active_and_ir_missing_players_go_to_creates():
    real = real_week({
        "KC": [real_player("00-f", "Missing Active", "KC", status="ACTIVE"),
               real_player("00-g", "Missing Hurt", "KC", status="IR", designation="R01")],
    })
    result = plan(snapshot(), real, {})
    assert [(p.gsis_id, p.status) for p in result.creates] == [("00-f", "ACTIVE"), ("00-g", "IR")]
    assert result.ps_fill == []
    assert result.ops == []


def test_ir_on_and_ir_off():
    snap = snapshot(madden(104, "Hurt", "Man", 0),
                    madden(105, "Healed", "Man", 0, injured_reserve=True))
    real = real_week({"KC": [real_player("00-h", "Hurt Man", "KC", status="IR", designation="R01"),
                             real_player("00-i", "Healed Man", "KC", status="ACTIVE")]})
    result = plan(snap, real, {"00-h": 104, "00-i": 105})
    assert [o["kind"] for o in ops_for(result, pgid=104)] == ["ir"]
    assert ops_for(result, pgid=104)[0]["list"] == "Roster"
    assert [o["kind"] for o in ops_for(result, pgid=105)] == ["activate"]


def test_roster_to_practice_squad_is_one_list_change():
    snap = snapshot(madden(106, "Down", "Man", 0))
    real = real_week({"KC": [real_player("00-j", "Down Man", "KC", status="PS", designation="P01")]})
    result = plan(snap, real, {"00-j": 106})
    ops = ops_for(result, pgid=106)
    assert [(o["kind"], o["list"], o["to_index"]) for o in ops] == [("list", "PracticeSquad", 0)]


def test_incremental_link_finds_a_free_agent_then_signs_him():
    snap = snapshot(madden(700, "New", "Guy", diff.FREE_AGENT_TEAM, status="FreeAgent", years_pro=0))
    real = real_week({"BUF": [real_player("00-new", "New Guy", "BUF", entry_year=2026)]})
    result = plan(snap, real, {})
    assert result.unlinked_new == [("00-new", 700)]
    signs = ops_for(result, pgid=700, kind="sign")
    assert len(signs) == 1 and signs[0]["to_index"] == 1 and signs[0]["list"] == "Roster"
    assert result.creates == []


def test_unlinked_new_player_on_a_save_team_is_found_before_release():
    snap = snapshot(madden(701, "Real", "Person", 0, status="Signed", years_pro=0))
    real = real_week({"KC": [real_player("00-rp", "Real Person", "KC", entry_year=2026)]})
    result = plan(snap, real, {})
    assert result.unlinked_new == [("00-rp", 701)]
    assert result.ops == []          # already on the right team and list


def test_snapshot_college_reference_is_converted_before_linking():
    """A snapshot's college is a {table, row} reference, not a string: the linker must get the name."""
    snap = snapshot(madden(800, "Col", "Lege", diff.FREE_AGENT_TEAM, status="FreeAgent", years_pro=0,
                           college={"table": 16384, "row": 1968}))
    real = real_week({"KC": [real_player("00-col", "Col Lege", "KC", entry_year=2026, college="Pittsburgh")]})
    result = plan(snap, real, {})          # raised AttributeError before the college conversion
    assert result.unlinked_new == [("00-col", 800)]
    assert [o["kind"] for o in ops_for(result, pgid=800)] == ["sign"]


def test_snapshot_college_reference_counts_as_linking_evidence():
    mu = diff._mu_player(800, madden(800, "Col", "Lege", diff.FREE_AGENT_TEAM, status="FreeAgent",
                                     college={"table": 16384, "row": 1968}), TEAMS, 2026)
    person = diff._real_person(real_player("00-col", "Col Lege", "KC", entry_year=2026, college="Pittsburgh"))
    assert mu.college == "PITTSBURGH"       # the reference resolved to the school name
    assert link.evidence(mu, person)["college"] is True
    assert diff._mu_player(801, madden(801, "No", "College", 0), TEAMS, 2026).college is None


# ---------- review candidates (S5a / P1) ----------

def test_cross_position_candidate_with_agreeing_entry_season():
    """A unique same-name save player at another position is reported, never auto-linked or created."""
    snap = snapshot(madden(900, "Travis", "Hunter", 0, position="CB", years_pro=0))
    real = real_week({"BUF": [real_player("00-hunter", "Travis Hunter", "BUF", position="WR",
                                          entry_year=2026)]})
    result = plan(snap, real, {})
    assert result.unlinked_new == []
    assert result.creates == []
    assert len(result.review) == 1
    entry = result.review[0]
    assert entry["status"] == "candidate"
    assert (entry["gsis_id"], entry["pgid"]) == ("00-hunter", 900)
    assert (entry["position"], entry["save_position"]) == ("WR", "CB")
    assert entry["evidence"] == {"entry": True}
    assert "position group" in entry["why"]
    assert [(c["pgid"], c["position"]) for c in entry["candidates"]] == [(900, "CB")]


def test_cross_position_candidate_with_agreeing_2026_team():
    snap = snapshot(madden(901, "Connor", "Heyward", 1, position="TE", years_pro=3))
    real = real_week({"BUF": [real_player("00-heyward", "Connor Heyward", "BUF", position="RB",
                                          entry_year=2020)]})
    result = plan(snap, real, {})
    assert result.unlinked_new == [] and result.creates == []
    entry = result.review[0]
    assert entry["status"] == "candidate"
    assert (entry["pgid"], entry["save_position"], entry["position"]) == (901, "TE", "RB")
    assert entry["evidence"] == {"team": True}


def test_same_name_player_without_agreeing_evidence_is_still_a_create():
    """Entry season and 2026 team both disagree: no review candidate, the normal create path runs."""
    snap = snapshot(madden(902, "Sam", "Same", 0, position="WR", years_pro=3))
    real = real_week({"BUF": [real_player("00-same", "Sam Same", "BUF", position="WR", entry_year=2026)]})
    result = plan(snap, real, {})
    assert result.review == []
    assert [p.gsis_id for p in result.creates] == ["00-same"]


def test_cross_position_without_agreeing_evidence_is_still_a_create():
    snap = snapshot(madden(907, "Sam", "Same", 0, position="CB", years_pro=3))
    real = real_week({"BUF": [real_player("00-same", "Sam Same", "BUF", position="WR", entry_year=2026)]})
    result = plan(snap, real, {})
    assert result.review == []
    assert [p.gsis_id for p in result.creates] == ["00-same"]


def test_two_same_name_save_players_are_ambiguous_and_create_nothing():
    snap = snapshot(madden(903, "Same", "Name", 0, position="CB", years_pro=0),
                    madden(904, "Same", "Name", 0, position="FS", years_pro=0))
    real = real_week({"KC": [real_player("00-same", "Same Name", "KC", position="WR", entry_year=2026)]})
    result = plan(snap, real, {})
    assert result.unlinked_new == []
    assert result.creates == []
    assert len(result.review) == 1
    entry = result.review[0]
    assert entry["status"] == "ambiguous"
    assert entry["gsis_id"] == "00-same"
    assert sorted((c["pgid"], c["position"]) for c in entry["candidates"]) == [(903, "CB"), (904, "FS")]


def test_the_audit_repro_surfaces_the_signed_cb_as_a_review_candidate():
    """P1 repro: an unlinked BUF CB with the real BUF WR's name is reviewed, not created and not released."""
    snap = snapshot(madden(909, "Travis", "Hunter", 1, position="CB", years_pro=0))
    real = real_week({"BUF": [real_player("00-hunter", "Travis Hunter", "BUF", position="WR",
                                          entry_year=2026)]})
    result = plan(snap, real, {})
    assert result.unlinked_new == [] and result.creates == [] and result.ops == []
    entry = result.review[0]
    assert entry["status"] == "candidate"
    assert (entry["gsis_id"], entry["pgid"], entry["position"], entry["save_position"]) == \
        ("00-hunter", 909, "WR", "CB")
    assert entry["evidence"] == {"entry": True, "team": True}


def test_an_already_linked_save_pgid_is_reviewed_but_not_auto_linked():
    snap = snapshot(madden(905, "Travis", "Hunter", 1, position="CB", years_pro=0))
    real = real_week({"BUF": [real_player("00-other", "Other Guy", "BUF"),
                              real_player("00-hunter", "Travis Hunter", "BUF", position="WR",
                                          entry_year=2026)]})
    result = plan(snap, real, {"00-other": 905})
    assert result.unlinked_new == []
    assert result.ops == []
    assert result.creates == []
    entry = result.review[0]
    assert entry["status"] == "candidate"
    assert (entry["gsis_id"], entry["pgid"]) == ("00-hunter", 905)
    assert entry["candidates"][0]["linked_gsis"] == "00-other"
    assert "already linked to 00-other" in entry["why"]


def test_an_already_linked_real_person_is_not_reviewed():
    snap = snapshot(madden(908, "Travis", "Hunter", 1, position="CB", years_pro=0))
    real = real_week({"BUF": [real_player("00-hunter", "Travis Hunter", "BUF", position="WR",
                                          entry_year=2026)]})
    result = plan(snap, real, {"00-hunter": 908})
    assert result.review == []
    assert result.creates == [] and result.ops == []


def test_accepting_a_candidate_through_the_overrides_csv_links_once(monkeypatch):
    """An accepted override links in the new-link phase; the candidate is gone and no create follows."""
    snap = snapshot(madden(906, "Travis", "Hunter", 1, position="CB", years_pro=0))
    real = real_week({"BUF": [real_player("00-hunter", "Travis Hunter", "BUF", position="WR",
                                          entry_year=2026)]})
    monkeypatch.setattr(diff.link_engine, "read_overrides", lambda *a, **k: [])
    before = plan(snap, real, {})
    assert [e["status"] for e in before.review] == ["candidate"]
    assert before.creates == []

    row = {"madden_pgid": "906", "full_name": "Travis Hunter", "position": "CB",
           "gsis_id": "00-hunter", "note": "position change accepted"}
    monkeypatch.setattr(diff.link_engine, "read_overrides", lambda *a, **k: [row])
    accepted = plan(snap, real, {})
    assert accepted.unlinked_new == [("00-hunter", 906)]
    assert accepted.review == [] and accepted.creates == [] and accepted.ops == []

    linked = {gsis: pgid for gsis, pgid in accepted.unlinked_new}
    again = plan(snap, real, linked)
    assert again.unlinked_new == [] and again.review == [] and again.creates == [] and again.ops == []


# ---------- contracts ----------

def _write_parquet(path, **columns):
    pq.write_table(pa.table(columns), str(path))
    return str(path)


def _otc(team, signed=None, year_signed=2026, years=3, apy=10.0, total=None, guaranteed=0.0,
         draft_year=2023, active=True, gsis="00-otc"):
    """One explicit OtcDeal, in OTC's units (money in millions)."""
    return contracts.OtcDeal(
        gsis_id=gsis, team=team, year_signed=year_signed, signed=signed, years=years, apy=apy,
        total=(apy * years if total is None else total), guaranteed=guaranteed, draft_year=draft_year,
        active=active)


def test_from_otc_uses_a_2026_deal_and_falls_back_to_the_minimum(tmp_path):
    path = _write_parquet(
        tmp_path / "contracts.parquet",
        gsis_id=["00-otc", "00-old", "00-otc"],
        team=["Bengals", "Bengals", "Bengals"],
        year_signed=[2026, 2024, 2023],
        years=[3, 4, 4],
        apy=[10.0, 5.0, 4.0],
        value=[30.0, 20.0, 16.0],
        guaranteed=[12.0, 8.0, 6.0],
        draft_year=[2023, 2020, 2023],
        is_active=[True, True, False],
    )
    book = ContractBook.from_otc(path)
    real = book.contract_for("00-otc", "2026-10-05", CAP, years_exp=3)
    assert real["length"] == 3 and real["contract_type"] == "STANDARD"
    assert real["salary_by_year"] == [10_000_000, 10_000_000, 10_000_000]
    assert real["bonus_by_year"] == [0, 0, 0]
    assert real["guaranteed"] == 12_000_000
    assert real["signed_season"] == 2026
    assert sum(real["salary_by_year"]) + sum(real["bonus_by_year"]) == 30_000_000

    minimum = book.contract_for("00-old", "2026-10-05", CAP)          # 2024 deal: no window deal
    assert minimum["length"] == 1 and minimum["contract_type"] == "MINIMUM"
    assert minimum["salary_by_year"] == [ce.minimum_salary(2026 - 2020, CAP)]

    unknown = book.contract_for("00-nobody", None, CAP)               # no OTC row at all: rookie minimum
    assert unknown["salary_by_year"] == [ce.minimum_salary(0, CAP)]
    assert book.contract_for("", None, CAP) is None


def test_signing_before_the_otc_window_gets_the_minimum(tmp_path):
    path = _write_parquet(
        tmp_path / "contracts.parquet",
        gsis_id=["00-otc"],
        team=["Bengals"],
        year_signed=[2026],
        years=[3],
        apy=[10.0],
        value=[30.0],
        guaranteed=[0.0],
        draft_year=[2023],
    )
    book = ContractBook.from_otc(path)
    march = book.contract_for("00-otc", "2026-03-15", CAP)
    assert march["contract_type"] == "MINIMUM" and march["length"] == 1
    october = book.contract_for("00-otc", "2026-10-01", CAP)
    assert october["contract_type"] == "STANDARD" and october["length"] == 3


def test_otc_dates_are_honoured_when_the_file_has_them(tmp_path):
    path = _write_parquet(
        tmp_path / "contracts.parquet",
        gsis_id=["00-pre", "00-post"],
        team=["Bengals", "Bengals"],
        year_signed=[2026, 2026],
        date_signed=["2026-03-15", "2026-09-01"],
        years=[2, 4],
        apy=[6.0, 12.0],
        value=[12.0, 48.0],
        guaranteed=[0.0, 20.0],
        draft_year=[2023, 2022],
    )
    book = ContractBook.from_otc(path)
    assert book.contract_for("00-pre", None, CAP)["contract_type"] == "MINIMUM"
    post = book.contract_for("00-post", None, CAP)
    assert post["contract_type"] == "STANDARD" and post["length"] == 4
    assert post["salary_by_year"] == [12_000_000] * 4


def test_from_otc_missing_file_is_an_empty_book(tmp_path):
    book = ContractBook.from_otc(str(tmp_path / "none.parquet"))
    assert book.contract_for("00-a", None, CAP)["contract_type"] == "MINIMUM"


def test_offer_contract_shape_matches_league_office():
    """The OTC dict must be exactly what pipeline.league_office.offer_contract returns."""
    from pipeline.league_office import offer_contract
    offer = {"years": 3, "total_value": 30_000_000, "bonus": 0, "guaranteed": 12_000_000}
    book = ContractBook({"00-a": [contracts.OtcDeal(
        gsis_id="00-a", team="Bengals", year_signed=2026, signed=None, years=3, apy=10.0, total=30.0,
        guaranteed=12.0, draft_year=2023, active=True)]})
    assert book.contract_for("00-a", "2026-10-01", CAP) == offer_contract(offer, 2026)


# ---------- OTC: the target team and the as-of date ----------

def test_otc_deal_for_another_team_gets_the_minimum():
    """F2a: a BUF signing never receives a Chiefs-only deal (through plan_rosters)."""
    snap = snapshot(madden(120, "Free", "Agent", diff.FREE_AGENT_TEAM, status="FreeAgent"))
    real = real_week({"BUF": [real_player("00-otc", "Free Agent", "BUF", entry_year=2020)]})
    book = ContractBook({"00-otc": [_otc("Chiefs", "2026-09-01")]})
    contract = ops_for(plan(snap, real, {"00-otc": 120}, contracts_=book), kind="sign")[0]["contract"]
    assert contract["contract_type"] == "MINIMUM"
    assert contract["salary_by_year"] == [ce.minimum_salary(6, CAP)]


def test_dated_deal_after_the_signing_as_of_is_not_used():
    """F2b: a December deal cannot back a September signing."""
    book = ContractBook({"00-otc": [_otc("Bills", "2026-12-01", years=4, apy=12.0)]})
    assert book.contract_for("00-otc", "2026-09-01", CAP)["contract_type"] == "MINIMUM"
    assert book.contract_for("00-otc", "2026-09-01", CAP, team="BUF")["contract_type"] == "MINIMUM"
    december = book.contract_for("00-otc", "2026-12-15", CAP, team="BUF")
    assert december["contract_type"] == "STANDARD" and december["length"] == 4


def test_same_team_september_deal_is_selected():
    book = ContractBook({"00-otc": [_otc("Bills", "2026-09-01", years=2, apy=6.0)]})
    contract = book.contract_for("00-otc", "2026-10-01", CAP, team="BUF")
    assert contract["contract_type"] == "STANDARD" and contract["length"] == 2
    assert contract["salary_by_year"] == [6_000_000, 6_000_000]


def test_newest_same_team_deal_known_by_the_as_of_date_is_selected():
    book = ContractBook({"00-otc": [_otc("Bills", "2026-09-01", years=2, apy=6.0),
                                   _otc("Bills", "2026-12-01", years=4, apy=12.0)]})
    october = book.contract_for("00-otc", "2026-10-01", CAP, team="BUF")
    assert (october["length"], october["salary_by_year"][0]) == (2, 6_000_000)
    december = book.contract_for("00-otc", "2026-12-15", CAP, team="BUF")
    assert (december["length"], december["salary_by_year"][0]) == (4, 12_000_000)


def test_year_only_2026_row_is_a_window_approximation():
    book = ContractBook({"00-otc": [_otc("Bills", None, years=3, apy=10.0)]})
    assert book.contract_for("00-otc", "2026-07-31", CAP, team="BUF")["contract_type"] == "MINIMUM"
    assert book.contract_for("00-otc", "2026-08-01", CAP, team="BUF")["contract_type"] == "STANDARD"
    assert book.contract_for("00-otc", "2026-12-31", CAP, team="BUF")["contract_type"] == "STANDARD"


def test_pre_cutoff_dated_row_falls_back_to_the_minimum():
    book = ContractBook({"00-otc": [_otc("Bills", "2026-07-15", years=3, apy=10.0)]})
    assert book.contract_for("00-otc", "2026-10-01", CAP, team="BUF")["contract_type"] == "MINIMUM"


def test_no_row_uses_the_service_based_minimum():
    contract = ContractBook({}).contract_for("00-nobody", "2026-10-01", CAP, team="BUF", years_exp=2)
    assert contract["contract_type"] == "MINIMUM" and contract["length"] == 1
    assert contract["salary_by_year"] == [ce.minimum_salary(2, CAP)]


def test_unknown_or_unmappable_otc_team_never_matches_a_franchise():
    for club in ("SEA/LV", "Redskins", "Los Angeles Rams", ""):
        book = ContractBook({"00-otc": [_otc(club or None, "2026-09-01")]})
        assert book.contract_for("00-otc", "2026-10-01", CAP, team="WAS")["contract_type"] == "MINIMUM"
        assert book.contract_for("00-otc", "2026-10-01", CAP, team="SEA")["contract_type"] == "MINIMUM"
    known = ContractBook({"00-otc": [_otc("Commanders", "2026-09-01")]})
    assert known.contract_for("00-otc", "2026-10-01", CAP, team="WAS")["contract_type"] == "STANDARD"


def test_from_otc_uses_only_the_target_teams_deal(tmp_path):
    """Two clubs' 2026 deals for one player, through from_otc and plan_rosters."""
    path = _write_parquet(
        tmp_path / "contracts.parquet",
        gsis_id=["00-otc", "00-otc"],
        team=["Bills", "Chiefs"],
        year_signed=[2026, 2026],
        years=[3, 4],
        apy=[10.0, 15.0],
        value=[30.0, 60.0],
        guaranteed=[0.0, 30.0],
        draft_year=[2023, 2021],
        is_active=[True, True],
    )
    book = ContractBook.from_otc(path)
    assert book.contract_for("00-otc", "2026-10-01", CAP, team="BUF")["salary_by_year"] == [10_000_000] * 3
    assert book.contract_for("00-otc", "2026-10-01", CAP, team="KC")["salary_by_year"] == [15_000_000] * 4

    snap = snapshot(madden(121, "Free", "Agent", diff.FREE_AGENT_TEAM, status="FreeAgent"))
    real = real_week({"BUF": [real_player("00-otc", "Free Agent", "BUF", entry_year=2020)]})
    contract = ops_for(plan(snap, real, {"00-otc": 121}, contracts_=book), kind="sign")[0]["contract"]
    assert contract["contract_type"] == "STANDARD"
    assert contract["salary_by_year"] == [10_000_000] * 3          # the Bills deal, never the Chiefs'


def test_plan_rosters_uses_the_week_as_of_boundary_for_dated_deals(tmp_path):
    path = _write_parquet(
        tmp_path / "contracts.parquet",
        gsis_id=["00-otc"],
        team=["Bills"],
        year_signed=[2026],
        date_signed=["2026-12-01"],
        years=[4],
        apy=[12.0],
        value=[48.0],
        guaranteed=[0.0],
        draft_year=[2022],
    )
    book = ContractBook.from_otc(path)
    snap = snapshot(madden(122, "Free", "Agent", diff.FREE_AGENT_TEAM, status="FreeAgent"))
    real = real_week({"BUF": [real_player("00-otc", "Free Agent", "BUF", entry_year=2020)]})   # week 10
    contract = ops_for(plan(snap, real, {"00-otc": 122}, contracts_=book), kind="sign")[0]["contract"]
    assert contract["contract_type"] == "MINIMUM"      # dated after week 10's as-of boundary (2026-11-17)


def test_january_2027_postseason_deal_is_used_for_a_2026_season_signing():
    """N1: the 2026 season runs through the Super Bowl, so a January 2027 deal is inside the mirror window."""
    book = ContractBook({"00-otc": [_otc("Bills", "2027-01-10", year_signed=2027, years=2, apy=6.0)]})
    contract = book.contract_for("00-otc", "2027-01-31", CAP, team="BUF")
    assert contract["contract_type"] == "STANDARD"
    assert contract["length"] == 2
    assert contract["salary_by_year"] == [6_000_000, 6_000_000]

    # The as-of boundary still holds: a deal is never used before its own date, so a later February deal loses.
    assert book.contract_for("00-otc", "2027-01-09", CAP, team="BUF")["contract_type"] == "MINIMUM"
    future = ContractBook({"00-otc": [_otc("Bills", "2027-02-10", year_signed=2027, years=5, apy=20.0)]})
    assert future.contract_for("00-otc", "2027-01-31", CAP, team="BUF")["contract_type"] == "MINIMUM"


def test_postseason_parquet_deals_through_plan_rosters(tmp_path):
    """N1 regressions over a real parquet: December/January/February rows, selected as of a Super Bowl-week mirror."""
    path = _write_parquet(
        tmp_path / "contracts.parquet",
        gsis_id=["00-otc", "00-otc", "00-otc", "00-otc", "00-pre"],
        team=["Bills", "Bills", "Bills", "Chiefs", "Bills"],
        year_signed=[2026, 2027, 2027, 2027, 2026],
        date_signed=["2026-12-01", "2027-01-10", "2027-02-10", "2027-01-10", "2026-07-15"],
        years=[4, 2, 5, 3, 3],
        apy=[12.0, 6.0, 20.0, 15.0, 10.0],
        value=[48.0, 12.0, 100.0, 45.0, 30.0],
        guaranteed=[0.0, 0.0, 0.0, 0.0, 0.0],
        draft_year=[2022, 2022, 2022, 2022, 2023],
    )
    book = ContractBook.from_otc(path)
    real = real_week({"BUF": [real_player("00-otc", "Free Agent", "BUF", entry_year=2020),
                              real_player("00-pre", "Pre Window", "BUF", entry_year=2020)]}, week=22)
    assert diff._week_as_of(real) == "2027-02-09"      # the Super Bowl week, after the 2026 regular season
    snap = snapshot(madden(123, "Free", "Agent", diff.FREE_AGENT_TEAM, status="FreeAgent"),
                    madden(124, "Pre", "Window", diff.FREE_AGENT_TEAM, status="FreeAgent"))
    signs = {op["gsis_id"]: op["contract"]
             for op in ops_for(plan(snap, real, {"00-otc": 123, "00-pre": 124}, contracts_=book), kind="sign")}

    # The January deal is the latest known same-team deal; the February future deal and the Chiefs deal lose.
    assert signs["00-otc"]["contract_type"] == "STANDARD"
    assert (signs["00-otc"]["length"], signs["00-otc"]["salary_by_year"]) == (2, [6_000_000, 6_000_000])
    assert (book.contract_for("00-otc", "2027-02-09", CAP, team="KC")["length"],
            book.contract_for("00-otc", "2027-02-09", CAP, team="KC")["salary_by_year"][0]) == (3, 15_000_000)
    # A deal dated before the August cutoff is still outside the window.
    assert signs["00-pre"]["contract_type"] == "MINIMUM" and signs["00-pre"]["length"] == 1
    # The window closes before the 2027 league year: a March 2027 deal is not a 2026-season deal.
    march = ContractBook({"00-otc": [_otc("Bills", "2027-03-05", year_signed=2027, years=4, apy=12.0)]})
    assert march.contract_for("00-otc", "2027-03-10", CAP, team="BUF")["contract_type"] == "MINIMUM"
