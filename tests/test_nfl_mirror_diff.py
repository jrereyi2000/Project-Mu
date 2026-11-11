"""engines/nfl_mirror/diff and contracts (NFL_WEEKLY_MIRROR_PLAN.md §2.5, §4). Inline fixtures, no network, no DB."""
import pyarrow as pa
import pyarrow.parquet as pq

from engines import contracts_engine as ce
from engines.nfl_mirror import contracts, diff
from engines.nfl_mirror.contracts import ContractBook
from engines.nfl_mirror.real_state import RealPlayer, RealWeek
from engines.nfl_mirror.trades import TradeLeg

TEAMS = {"KC": 0, "BUF": 1, "MIA": 2}
CAP = ce.CBA["REFERENCE_CAP"]


# ---------- fixtures ----------

def real_player(gid, name, team, position="WR", status="ACTIVE", designation="A01", entry_year=2020,
                draft_number=None):
    return RealPlayer(gsis_id=gid, name=name, position=position, team=team, status=status,
                      designation=designation, entry_year=entry_year, draft_number=draft_number)


def real_week(rosters, week=10):
    return RealWeek(season=2026, week=week, mode="CATCH_UP", rosters=rosters)


def madden(pgid, first, last, team_index, status="Signed", position="WR", years_pro=0,
           injured_reserve=False, year_drafted=None):
    return {"pgid": pgid, "first_name": first, "last_name": last, "position": position, "status": status,
            "team_index": team_index, "years_pro": years_pro, "overall": 70, "age": 26,
            "injury": {"injured_reserve": injured_reserve}, "year_drafted": year_drafted,
            "college": None, "height_in": None, "weight_lbs": None}


def snapshot(*players):
    return {"players": list(players)}


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


def test_roster_to_practice_squad_is_a_release_and_a_ps_signing():
    snap = snapshot(madden(106, "Down", "Man", 0))
    real = real_week({"KC": [real_player("00-j", "Down Man", "KC", status="PS", designation="P01")]})
    result = plan(snap, real, {"00-j": 106})
    kinds = [o["kind"] for o in ops_for(result, pgid=106)]
    assert kinds == ["release", "sign"]
    assert ops_for(result, pgid=106, kind="sign")[0]["list"] == "PracticeSquad"


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


# ---------- contracts ----------

def _write_parquet(path, **columns):
    pq.write_table(pa.table(columns), str(path))
    return str(path)


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
