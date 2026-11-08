"""engines/compensatory_picks_run.py: the compensatory pick producer, offline (fake cursors only).

Covers QFA derivation (old/new teams, APY, cut players rejected, honors), a full season's ledgers and COMP
draft_picks (after the 32 standard picks, later rounds shifted), idempotent re-runs that never touch SEED/TRADE/JR
rows, JC-2A being off by default (and only reading staff_development_departures when enabled), the two-draft JC-2A
entitlement, and --dry-run writing nothing. No database is opened.
"""
import datetime

from engines import compensatory_picks_run as run
from engines import compensatory_picks_engine as E
from engines import contracts_engine as ce

SEASON = 2036
DRAFT = SEASON + 1
SIGNED_AT = datetime.datetime(SEASON + 1, 3, 1, tzinfo=datetime.timezone.utc)
# 20 distinct league APYs (1M..20M): with the engine's percentile-rank formula 20M -> Round 3, 19M -> Round 4,
# 18M -> Round 5 (the same clean boundaries tests/test_compensatory_picks_engine.py uses).
LEAGUE_APYS = [i * 1_000_000 for i in range(1, 21)]

DEPARTURES = [{"season": SEASON, "from_team_id": 20, "from_role": "OC", "to_role": "HC",
               "tenure_years": 3, "eligible": True, "person_name": "Bo Coach", "source": "coaching_carousel"}]


class FakeCursor:
    """The source tables and the three ledgers the producer touches, with the producer's SQL shapes."""

    def __init__(self, *, offers=(), player_seasons=(), transactions=(), awards=(), contracts=(),
                 departures=None):
        self.offers = list(offers)
        self.player_seasons = {(r["player_id"], r["season"]): dict(r) for r in player_seasons}
        self.transactions = list(transactions)
        self.awards = list(awards)
        self.contracts = list(contracts)
        self.departures = None if departures is None else list(departures)   # None = table absent
        self.qfa = []
        self.comp_awards = []
        self.draft_picks = []          # seeded standard rows + COMP/JC2A rows the producer writes
        self.sql = []
        self._result = []

    # -- cursor protocol
    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        self.sql.append(text)
        self._result = []
        p = params
        if text.startswith("SELECT to_regclass"):
            self._result = [(self.departures is not None,)]
        elif text.startswith("SELECT DISTINCT player_id FROM transactions"):
            season = p[0]
            pids = sorted({t["player_id"] for t in self.transactions
                           if t.get("season") == season and t.get("type") == "RELEASED"
                           and t.get("player_id") is not None})
            self._result = [(pid,) for pid in pids]
        elif text.startswith("SELECT player_id, award_type FROM awards"):
            season = p[0]
            self._result = [(a["player_id"], a["award_type"]) for a in self.awards
                            if a.get("season") == season and a.get("player_id") is not None]
        elif text.startswith("SELECT o.id"):
            season = p[0]
            rows = []
            for o in sorted(self.offers, key=lambda r: (r["player_id"], r.get("created_at") or "", r["id"])):
                if (o.get("season") != season or o.get("status") != "ACCEPTED"
                        or o.get("kind") != "FREE_AGENT"):
                    continue
                ps = self.player_seasons.get((o["player_id"], season))
                if ps is None or ps.get("team_id") is None:
                    continue
                rows.append((o["id"], o["player_id"], o["team_id"], o["total_value"], o["years"],
                             o.get("created_at"), ps["team_id"], ps.get("released_week")))
            self._result = rows
        elif "FROM player_contracts" in text:
            season = p[0]
            self._result = [(c["total_value"] / c["length"],) for c in self.contracts
                            if c.get("season") == season and c.get("status") == "ACTIVE" and c.get("length")]
        elif text.startswith("SELECT from_team_id, person_name, from_role, to_role, tenure_years, source"):
            season = p[0]
            self._result = [(d["from_team_id"], d.get("person_name"), d["from_role"], d["to_role"],
                             d["tenure_years"], d.get("source"))
                            for d in (self.departures or [])
                            if d.get("season") == season and d.get("eligible")]
        elif text.startswith("DELETE FROM qualifying_free_agents"):
            self.qfa = [r for r in self.qfa if r["season"] != p[0]]
        elif text.startswith("DELETE FROM compensatory_pick_awards"):
            self.comp_awards = [r for r in self.comp_awards if r["draft_season"] != p[0]]
        elif text.startswith("DELETE FROM draft_picks"):
            self.draft_picks = [r for r in self.draft_picks
                                if not (r["draft_season"] == p[0] and r["source"] in ("COMP", "JC2A"))]
        elif text.startswith("INSERT INTO qualifying_free_agents"):
            self.qfa.append(dict(p))
        elif text.startswith("INSERT INTO compensatory_pick_awards"):
            self.comp_awards.append(dict(p))
        elif text.startswith("INSERT INTO draft_picks"):
            keys = ("draft_season", "round", "original_team_id", "owner_team_id", "overall_pick", "source",
                    "pick_suffix")
            self.draft_picks.append(dict(zip(keys, p)))
        elif text.startswith("SELECT id, abbreviation FROM teams"):
            self._result = []
        else:
            raise AssertionError("unexpected SQL: " + text)

    def fetchall(self):
        return list(self._result)

    def fetchone(self):
        return self._result[0] if self._result else None


def _offer(oid, player_id, team_id, total_value, years=1):
    return {"id": oid, "player_id": player_id, "team_id": team_id, "total_value": total_value, "years": years,
            "season": SEASON, "kind": "FREE_AGENT", "status": "ACCEPTED", "created_at": SIGNED_AT}


def _fixture(departures=None):
    """Six free-agent signings: three qualifying losses (Rounds 3/4/5), one cut via transactions, one cut via
    released_week, and one same-team re-signing that is not a move."""
    return FakeCursor(
        offers=[
            _offer(1, 101, 2, 20_000_000),     # old team 1 -> Round 3
            _offer(2, 102, 4, 19_000_000),     # old team 3 -> Round 4
            _offer(3, 103, 6, 18_000_000),     # old team 5 -> Round 5
            _offer(4, 104, 8, 17_000_000),     # old team 7, cut (transactions)
            _offer(5, 105, 10, 16_000_000),    # old team 9, cut (released_week)
            _offer(6, 106, 11, 15_000_000),    # old team 11 == new team: not a lost/gained QFA
        ],
        player_seasons=[
            {"player_id": 101, "season": SEASON, "team_id": 1},
            {"player_id": 102, "season": SEASON, "team_id": 3},
            {"player_id": 103, "season": SEASON, "team_id": 5},
            {"player_id": 104, "season": SEASON, "team_id": 7},
            {"player_id": 105, "season": SEASON, "team_id": 9, "released_week": 5},
            {"player_id": 106, "season": SEASON, "team_id": 11},
        ],
        transactions=[{"season": SEASON, "type": "RELEASED", "player_id": 104}],
        awards=[{"season": SEASON, "player_id": 101, "award_type": "First Team All-Pro"}],
        departures=departures,
    )


# ------------------------------------------------------------------ QFA derivation ----
def test_build_qfas_derives_old_new_teams_and_apy_and_rejects_cut_players():
    cur = _fixture()
    qfas = {q["player_id"]: q for q in run.build_qfas(cur, SEASON)}

    assert set(qfas) == {101, 102, 103, 104, 105}      # 106 (same team) is not a move
    q = qfas[101]
    assert (q["old_team_id"], q["new_team_id"], q["apy"]) == (1, 2, 20_000_000)
    assert (q["waived"], q["cut"], q["contract_expired"]) == (False, False, True)
    assert q["all_pro_first"] is True and q["honors_points"] == 20
    assert q["signed_date"] == SIGNED_AT.date()

    assert qfas[104]["waived"] is True and qfas[104]["contract_expired"] is False
    assert qfas[105]["cut"] is True and qfas[105]["contract_expired"] is False
    assert E.qfa_eligible(qfas[101]) is True
    assert E.qfa_eligible(qfas[104]) is False
    assert E.qfa_eligible(qfas[105]) is False


def test_honors_map_first_team_all_pro_and_pro_bowl_only():
    cur = _fixture()
    cur.awards = [
        {"season": SEASON, "player_id": 101, "award_type": "First Team All-Pro"},
        {"season": SEASON, "player_id": 102, "award_type": "Pro Bowl"},
        {"season": SEASON, "player_id": 103, "award_type": "Second Team All-Pro"},
    ]
    qfas = {q["player_id"]: q for q in run.build_qfas(cur, SEASON)}
    assert (qfas[101]["all_pro_first"], qfas[101]["pro_bowl"], qfas[101]["honors_points"]) == (True, False, 20)
    assert (qfas[102]["all_pro_first"], qfas[102]["pro_bowl"], qfas[102]["honors_points"]) == (False, True, 10)
    assert (qfas[103]["all_pro_first"], qfas[103]["pro_bowl"], qfas[103]["honors_points"]) == (False, False, 0)


# ------------------------------------------------------------------ full season ----
def test_full_season_writes_ledgers_and_comp_picks_after_the_32_standard():
    cur = _fixture()
    result = run.award_and_persist(cur, SEASON, DRAFT, league_apys=LEAGUE_APYS)

    # qualifying_free_agents: one row per signing, cut players qualify=False with no tier
    assert len(cur.qfa) == 5
    ledger = {r["player_id"]: r for r in cur.qfa}
    assert (ledger[101]["round_tier"], ledger[101]["qualifies"]) == (3, True)
    assert (ledger[102]["round_tier"], ledger[102]["qualifies"]) == (4, True)
    assert (ledger[103]["round_tier"], ledger[103]["qualifies"]) == (5, True)
    assert (ledger[104]["qualifies"], ledger[104]["round_tier"]) == (False, None)
    assert (ledger[105]["qualifies"], ledger[105]["round_tier"]) == (False, None)
    assert (ledger[101]["old_team_id"], ledger[101]["new_team_id"]) == (1, 2)
    assert ledger[101]["apy"] == 20_000_000 and ledger[101]["honors_points"] == 20
    assert ledger[101]["canceled_by_qfa_id"] is None

    # compensatory_pick_awards
    awards = {(a["round"], a["team_id"]): a for a in cur.comp_awards}
    assert set(awards) == {(3, 1), (4, 3), (5, 5)}
    assert all(a["award_kind"] == "CBA" for a in cur.comp_awards)
    assert (awards[(3, 1)]["slot_in_round"], awards[(3, 1)]["overall_pick"]) == (33, 97)
    assert (awards[(4, 3)]["slot_in_round"], awards[(4, 3)]["overall_pick"]) == (33, 130)
    assert (awards[(5, 5)]["slot_in_round"], awards[(5, 5)]["overall_pick"]) == (33, 163)

    # draft_picks: each comp sits after its round's 32 standard picks, and earlier comps shift later rounds
    picks = {(p["round"], p["source"]): p for p in cur.draft_picks}
    assert set(picks) == {(3, "COMP"), (4, "COMP"), (5, "COMP")}
    assert picks[(3, "COMP")]["overall_pick"] == 32 * 3 + 1                     # 97
    assert picks[(4, "COMP")]["overall_pick"] == 32 * 4 + 1 + 1                 # 130: round 3's comp shifts round 4
    assert picks[(5, "COMP")]["overall_pick"] == 32 * 5 + 2 + 1                 # 163: two comps before round 5
    assert all(p["pick_suffix"] == "01" for p in cur.draft_picks)
    assert result["comp_schedule"] == [0, 0, 1, 1, 1, 0, 0]
    for p in cur.draft_picks:
        assert ce.round_for_overall_pick(p["overall_pick"], result["comp_schedule"]) == p["round"]


def test_rerun_is_idempotent_and_never_touches_seed_trade_or_jr_rows():
    cur = _fixture()
    sentinels = [
        {"draft_season": DRAFT, "round": 1, "original_team_id": 1, "owner_team_id": 1, "overall_pick": None,
         "source": "SEED", "pick_suffix": ""},
        {"draft_season": DRAFT, "round": 3, "original_team_id": 2, "owner_team_id": 2, "overall_pick": None,
         "source": "TRADE", "pick_suffix": ""},
        {"draft_season": DRAFT, "round": 4, "original_team_id": 3, "owner_team_id": 3, "overall_pick": None,
         "source": "JR", "pick_suffix": ""},
    ]
    cur.draft_picks = [dict(s) for s in sentinels]

    run.award_and_persist(cur, SEASON, DRAFT, league_apys=LEAGUE_APYS)
    first = sorted((p["round"], p["source"], p["overall_pick"], p["pick_suffix"])
                   for p in cur.draft_picks if p["source"] in ("COMP", "JC2A"))
    run.award_and_persist(cur, SEASON, DRAFT, league_apys=LEAGUE_APYS)
    second = sorted((p["round"], p["source"], p["overall_pick"], p["pick_suffix"])
                    for p in cur.draft_picks if p["source"] in ("COMP", "JC2A"))

    assert first == second and len(first) == 3
    assert len(cur.draft_picks) == len(sentinels) + 3
    for sentinel in sentinels:
        assert sentinel in cur.draft_picks
    assert len(cur.comp_awards) == 3
    assert len(cur.qfa) == 5
    deletes = [s for s in cur.sql if s.startswith("DELETE FROM draft_picks")]
    assert deletes and all("source IN ('COMP', 'JC2A')" in s for s in deletes)


def test_dry_run_writes_nothing():
    cur = _fixture()
    cur.draft_picks = [{"draft_season": DRAFT, "round": 1, "original_team_id": 1, "owner_team_id": 1,
                        "overall_pick": None, "source": "SEED", "pick_suffix": ""}]
    result = run.award_and_persist(cur, SEASON, DRAFT, league_apys=LEAGUE_APYS, dry_run=True)

    assert result["dry_run"] is True and len(result["awards"]) == 3
    assert cur.qfa == [] and cur.comp_awards == []
    assert [p for p in cur.draft_picks if p["source"] in ("COMP", "JC2A")] == []
    assert not any(s.startswith(("INSERT", "DELETE")) for s in cur.sql)


# ------------------------------------------------------------------ JC-2A ----
def test_jc2a_is_off_by_default_even_with_the_departures_table():
    cur = _fixture(departures=DEPARTURES)
    result = run.award_and_persist(cur, SEASON, DRAFT, league_apys=LEAGUE_APYS)

    assert result["jc2a_picks"] == []
    assert all(a["award_kind"] == "CBA" for a in cur.comp_awards)
    assert all(p["source"] != "JC2A" for p in cur.draft_picks)
    # the default path never even checks for the table
    assert not any("staff_development_departures" in s or s.startswith("SELECT to_regclass") for s in cur.sql)


def test_jc2a_enabled_awards_the_two_round3_picks_and_persists_the_class_pick():
    cur = _fixture(departures=DEPARTURES)
    result = run.award_and_persist(cur, SEASON, DRAFT, league_apys=LEAGUE_APYS, jc2a_enabled=True)

    # the engine produces one Round-3 pick in each of the two subsequent drafts
    assert sorted((a["draft_season"], a["round"], a["award_kind"]) for a in result["jc2a_picks"]) == [
        (DRAFT, 3, "JC2A"), (DRAFT + 1, 3, "JC2A")]

    # this class gets its year-one pick, after all CBA formula picks in Round 3
    round3 = [(a["team_id"], a["slot_in_round"], a["overall_pick"], a["award_kind"])
              for a in sorted(result["awards"], key=lambda x: (x["round"], x["slot_in_round"])) if a["round"] == 3]
    assert round3 == [(1, 33, 97, "CBA"), (20, 34, 98, "JC2A")]
    jc_award = next(a for a in cur.comp_awards if a["award_kind"] == "JC2A")
    assert "Bo Coach" in jc_award["reason"]

    jc_picks = [p for p in cur.draft_picks if p["source"] == "JC2A"]
    assert len(jc_picks) == 1
    assert (jc_picks[0]["round"], jc_picks[0]["overall_pick"], jc_picks[0]["pick_suffix"]) == (3, 98, "02")

    # two Round-3 comps now shift Round 4 and Round 5 by one more pick
    picks = {(p["round"], p["source"]): p for p in cur.draft_picks}
    assert picks[(4, "COMP")]["overall_pick"] == 32 * 4 + 2 + 1                 # 131
    assert picks[(5, "COMP")]["overall_pick"] == 32 * 5 + 3 + 1                 # 164


def test_jc2a_enabled_without_the_table_is_a_graceful_no_op():
    cur = _fixture(departures=None)
    result = run.award_and_persist(cur, SEASON, DRAFT, league_apys=LEAGUE_APYS, jc2a_enabled=True)

    assert result["jc2a_picks"] == []
    assert all(p["source"] != "JC2A" for p in cur.draft_picks)
    assert any(s.startswith("SELECT to_regclass") for s in cur.sql)   # checked, table absent


def test_jc2a_year_two_is_carried_into_the_next_class():
    # A departure in season-1 gives this class its second (year-two) Round-3 pick; a run must not lose it.
    prev = [dict(DEPARTURES[0], season=SEASON - 1)]
    cur = _fixture(departures=prev)
    result = run.award_and_persist(cur, SEASON, DRAFT, league_apys=LEAGUE_APYS, jc2a_enabled=True)

    assert sorted((a["draft_season"], a["round"], a["team_id"]) for a in result["jc2a_picks"]) == [
        (SEASON, 3, 20), (DRAFT, 3, 20)]
    jc_for_class = [a for a in result["awards"] if a["award_kind"] == "JC2A"]
    assert len(jc_for_class) == 1 and jc_for_class[0]["draft_season"] == DRAFT
