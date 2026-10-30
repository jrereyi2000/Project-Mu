"""Transactions engine (docs/in_progress/V2_CORE_BUILD_PLAN.md §2.4, U11). Synthetic 32-team leagues and a fake
cursor only: no database."""
import pytest

from engines import contracts_engine as ce
from engines import transactions_engine as te
from engines import trade_value as tv
from engines.free_agency_engine import run_wave

S = 2036                                   # league_state season (its offseason)
T = S + 1                                  # league year of offseason moves
FITS = tv.build_fits([], [], [])
CAP = ce.CBA["REFERENCE_CAP"]
TEAMS = list(range(1, 33))
# group -> (position, starters)
GROUPS = {"QB": ("QB", 1), "RB": ("HB", 1), "WR": ("WR", 3), "TE": ("TE", 1), "OT": ("LT", 2), "IOL": ("C", 3),
          "EDGE": ("LEDG", 2), "DT": ("DT", 2), "LB": ("MIKE", 2), "CB": ("CB", 3), "S": ("FS", 2), "K": ("K", 1),
          "P": ("P", 1)}


def contract(aav, length=3, ctype="STANDARD", bonus=0, signed=None, drafted_round=None):
    c = te.make_contract(aav, length, ctype, signed, bonus_share=0.0, guar_share=0.0, drafted_round=drafted_round)
    if bonus:
        per = bonus // length
        c["bonus_by_year"] = [per] * length
        c["salary_by_year"] = [s - per for s in c["salary_by_year"]]
    return c


def player(pid, team, group, ovr, age=26, yoe=4, aav=2_000_000, length=3, start=S, status="ROSTER", dev="Normal",
           **ckw):
    pos = GROUPS[group][0]
    p = {"id": pid, "team_id": team, "pos": pos, "group": group, "ovr": ovr, "age": age, "dev": dev, "yoe": yoe,
         "rookie_season": S - yoe, "status": status, "contract": None, "contract_start": None}
    if status == "ROSTER":
        p["contract"] = contract(aav, length, signed=ckw.pop("signed", start), **ckw)
        p["contract_start"] = start
    return p


class Ids:
    def __init__(self, start=1):
        self.n = start

    def __call__(self):
        self.n += 1
        return self.n


def base_league(extra=None, wins=None, roster_size=37):
    """32 teams, each with every starter slot at 75 OVR plus one 65 backup per group (37 players), contracts
    running through S + 2 (no expiring deals), then 60-OVR WR fillers up to roster_size."""
    ids = Ids(100_000)
    players = {}
    for t in TEAMS:
        for g, (_, n) in GROUPS.items():
            for _ in range(n):
                p = player(ids(), t, g, 75)
                players[p["id"]] = p
            p = player(ids(), t, g, 65, aav=1_000_000)
            players[p["id"]] = p
        for _ in range(roster_size - 37):
            p = player(ids(), t, "WR", 60, aav=900_000)
            players[p["id"]] = p
    for p in extra or []:
        players[p["id"]] = p
    wins = wins or {}
    teams = {t: {"team_id": t, "abbr": f"T{t}", "wins": wins.get(t, 8), "losses": 17 - wins.get(t, 8),
                 "direction": None, "popularity_index": 50} for t in TEAMS}
    return {"season": S, "target": T, "teams": teams, "cap": {t: CAP for t in TEAMS}, "players": players,
            "fits": FITS}


def by_kind(moves, kind):
    return [m for m in moves if m["kind"] == kind]


# ------------------------------------------------------------- re-signing ----
def test_resign_keeps_valuable_cheap_player_and_lets_overpaid_go():
    star = player(1, 1, "WR", 79, age=25, yoe=5, aav=1_000_000, length=3, start=S - 2, dev="Superstar")
    old = player(2, 1, "WR", 66, age=33, yoe=11, aav=9_000_000, length=3, start=S - 2)
    lg = base_league([star, old])
    moves = te.plan_resign(lg, "seed")
    mine = {m["player_id"]: m for m in moves if m["team_id"] == 1}
    assert mine[1]["kind"] == "CONTRACT" and mine[1]["offer_kind"] == "RESIGN"
    c = mine[1]["contract"]
    assert c["signed_season"] == T and c["length"] == mine[1]["years"] and mine[1]["value"] > 0
    assert mine[2]["kind"] == "LET_GO" and mine[2]["value"] <= 0
    # no other team has an expiring deal, so no other moves at all
    assert {m["team_id"] for m in moves} == {1}


def test_resign_tenders_young_players_and_tags_at_most_one():
    erfa = player(3, 2, "WR", 70, age=23, yoe=1, aav=900_000, length=2, start=S - 1)
    rfa = player(4, 2, "CB", 76, age=24, yoe=2, aav=900_000, length=3, start=S - 2)
    lg = base_league([erfa, rfa])
    mine = {m["player_id"]: m for m in te.plan_resign(lg, "s")}
    assert mine[3]["kind"] == "CONTRACT" and mine[3]["tender"] == "ERFA"
    assert mine[3]["contract"]["salary_by_year"] == [ce.tender_value("ERFA", 2, CAP)]
    assert mine[4]["kind"] == "CONTRACT" and mine[4]["tender"] == "RFA_SECOND"
    # several 80+ stars: whatever the seed says about testing the market, one tag per team at most
    stars = [player(10 + i, 3, "EDGE", 88, age=26, yoe=6, aav=1_000_000, start=S - 2) for i in range(6)]
    for seed in ("a", "b", "c", "d"):
        moves = te.plan_resign(base_league(stars), seed)
        assert len(by_kind(moves, "TAG")) <= 1
        for m in by_kind(moves, "TAG"):
            assert m["contract"]["contract_type"] == "FRANCHISE_NONEXCL" and m["contract"]["length"] == 1


def test_resign_respects_cap_room():
    star = player(1, 1, "WR", 79, age=25, yoe=5, aav=1_000_000, start=S - 2, dev="Superstar")
    lg = base_league([star])
    lg["cap"][1] = te.payroll(lg, 1) + 1_000_000          # 1M of room before the buffer: nothing fits
    m = [m for m in te.plan_resign(lg, "s") if m["player_id"] == 1][0]
    assert m["kind"] == "LET_GO" and "cap room" in m["reason"]


def test_extension_only_when_eligible():
    # a starter entering his final year (ends T); valuable: a 78 Superstar on a cheap deal
    signed_last_year = player(5, 4, "CB", 82, age=25, yoe=4, aav=1_000_000, length=2, start=S, signed=S - 1,
                              dev="Superstar")
    signed_this_year = player(6, 5, "CB", 82, age=25, yoe=4, aav=1_000_000, length=2, start=S, signed=S,
                              dev="Superstar")
    signed_this_year["contract"]["signed_week"] = 10
    lg = base_league([signed_last_year, signed_this_year])
    ok, _ = ce.extension_eligible(dict(signed_this_year["contract"], current_year=0), signed_this_year, S,
                                  te.RESIGN_WEEK)
    assert not ok
    moves = {m["player_id"]: m for m in te.plan_resign(lg, "s")}
    assert moves[5]["kind"] == "EXTENSION"
    ext = moves[5]["contract"]
    assert ext["length"] == 1 + moves[5]["years"] and ext["salary_by_year"][0] == 1_000_000
    assert 6 not in moves                                   # ineligible: he plays out the deal


def test_fifth_year_option_for_first_round_rookie():
    # a 1st-round rookie deal entering year 4 (drafted S - 2), a backup: no extension, but the option year pays
    rookie = player(7, 6, "QB", 74, age=24, yoe=2, aav=900_000, length=4, start=S - 2, ctype="ROOKIE",
                    drafted_round=1)
    lg = base_league([rookie])
    moves = {m["player_id"]: m for m in te.plan_resign(lg, "s")}
    assert moves[7]["kind"] == "OPTION" and moves[7]["tier"] == 4
    assert moves[7]["contract"]["contract_type"] == "FIFTH_YEAR_OPTION"
    assert moves[7]["contract"]["length"] == 2
    assert moves[7]["contract"]["salary_by_year"] == [900_000, moves[7]["salary"]]


# ------------------------------------------------------------ free agency ----
def fa(pid, group, ovr, age=27, yoe=5):
    return player(pid, None, group, ovr, age=age, yoe=yoe, status="FA")


def test_offers_respect_cap_room_and_roster_limit_and_carry_team_market():
    pool = [fa(900 + i, g, 82) for i, g in enumerate(["QB", "WR", "CB", "EDGE", "OT", "S"] * 3)]
    lg = base_league(pool)
    # team 1: full roster; team 2: two spots; team 3: almost no cap room
    fillers = Ids(500_000)
    for t, n in ((1, te.ROSTER_LIMIT - 37), (2, te.ROSTER_LIMIT - 2 - 37)):
        for _ in range(n):
            p = player(fillers(), t, "WR", 60, aav=900_000)
            lg["players"][p["id"]] = p
    lg["cap"][3] = te.payroll(lg, 3) + int(CAP * te.CAP_BUFFER_SHARE) + 500_000
    offers = te.plan_offers(lg, "FREE_AGENCY", 3, "seed")
    teams = {o["team_id"] for o in offers}
    assert 1 not in teams and 3 not in teams
    assert len([o for o in offers if o["team_id"] == 2]) <= 2
    assert len(teams) >= 20                                 # the rest of the league bids
    ctxs = te.contexts(lg)
    for t in teams:
        mine = [o for o in offers if o["team_id"] == t]
        assert len(mine) <= min(te.OFFERS_PER_WAVE, te.ROSTER_LIMIT - ctxs[t]["roster_count"])
        assert sum(ce.cap_hit(o["contract"], 0) for o in mine) <= ctxs[t]["cap_room"]
    for o in offers:
        assert o["team"]["team_id"] == o["team_id"] and o["market"] is FITS["market"]
        assert o["kind"] == "FREE_AGENT" and o["wave"] == 3 and o["stage"] == "FREE_AGENCY"
        assert o["total_value"] == sum(o["contract"]["salary_by_year"]) + sum(o["contract"]["bonus_by_year"])
    decisions = run_wave(pool, offers, 3, "seed")
    assert decisions and {d["status"] for d in decisions} <= {"ACCEPT", "REJECT", "COUNTER"}
    signs = te.signings_from_decisions(decisions, offers)
    assert all(s["kind"] == "SIGN" and s["contract"] for s in signs)
    assert len({s["player_id"] for s in signs}) == len(signs)


def test_offers_wave_tiers():
    pool = [fa(901, "QB", 90), fa(902, "QB", 78)]
    offers = te.plan_offers(base_league(pool), "FREE_AGENCY", 1, "s")
    assert offers and {o["player_id"] for o in offers} == {901}


def test_market_needs_shape():
    needs = te.market_needs(base_league())
    assert set(needs) == set(TEAMS)
    assert all(n["roster_spots"] == te.ROSTER_LIMIT - 37 for n in needs.values())


# --------------------------------------------------------------- cut-down ----
def test_cutdown_reaches_limit_for_all_teams():
    lg = base_league(roster_size=70)
    moves = te.plan_cutdown(lg)
    assert all(m["kind"] == "RELEASE" for m in moves)
    per = {}
    for m in moves:
        per[m["team_id"]] = per.get(m["team_id"], 0) + 1
    assert per == {t: 70 - te.ROSTER_LIMIT for t in TEAMS}
    starters = {pid for c in te.contexts(lg).values() for pid in c["starter_ids"]}
    assert not starters & {m["player_id"] for m in moves}
    assert te.ROSTER_LIMIT == 65


def test_cutdown_prefers_higher_cut_savings_at_equal_value():
    lg = base_league(roster_size=te.ROSTER_LIMIT)
    # two identical 50-OVR backups on team 9: one with prorated bonus (dead money), one without
    a = player(1, 9, "WR", 50, aav=1_500_000, bonus=1_200_000)
    b = player(2, 9, "WR", 50, aav=1_500_000)
    lg["players"].update({1: a, 2: b})
    moves = [m for m in te.plan_cutdown(lg) if m["team_id"] == 9]
    assert len(moves) == 2
    assert {m["player_id"] for m in moves} == {1, 2}
    assert moves[0]["player_id"] == 2 and moves[0]["cut_savings"] > moves[1]["cut_savings"]
    lg["players"].pop(1)
    moves = [m for m in te.plan_cutdown(lg) if m["team_id"] == 9]
    assert [m["player_id"] for m in moves] == [2]


# ---------------------------------------------------------------- waivers ----
def test_waivers_worst_record_first_one_award_per_player():
    wins = {t: 4 + (t % 10) for t in TEAMS}
    star = player(1, 1, "QB", 90, aav=1_000_000)
    star2 = player(2, 2, "CB", 88, aav=1_000_000)
    junk = player(3, 3, "WR", 40, aav=5_000_000)
    lg = base_league([star, star2, junk], wins=wins)
    moves, claims = te.plan_waivers(lg, [1, 2, 3], "seed")
    order = te.waiver_order(lg, "seed")
    assert [te.win_pct(lg["teams"][t]) for t in order] == sorted(te.win_pct(lg["teams"][t]) for t in order)
    for pid in (1, 2):
        mine = [c for c in claims if c["player_id"] == pid]
        assert sum(c["awarded"] for c in mine) == 1
        won = next(c for c in mine if c["awarded"])
        assert won["priority"] == min(c["priority"] for c in mine)
        assert lg["players"][pid]["team_id"] not in {c["team_id"] for c in mine}
        assert won["team_id"] == next(t for t in order if t != lg["players"][pid]["team_id"])
    assert not [c for c in claims if c["player_id"] == 3]   # unclaimed: stays a free agent
    claims_moves = by_kind(moves, "WAIVER_CLAIM")
    assert sorted(m["player_id"] for m in claims_moves) == [1, 2]
    assert all(m["contract"]["length"] == 2 for m in claims_moves)   # S..S+2 deal, from T: 2 years left


def test_waivers_at_the_limit_claim_with_a_paired_release():
    lg = base_league([player(1, 1, "QB", 90, aav=1_000_000)], roster_size=te.ROSTER_LIMIT)
    moves, claims = te.plan_waivers(lg, [1], "s")
    claim = by_kind(moves, "WAIVER_CLAIM")[0]
    rel = by_kind(moves, "RELEASE")
    assert len(rel) == 1 and rel[0]["team_id"] == claim["team_id"] and rel[0]["for_player_id"] == 1


# ---------------------------------------------------------- practice squad ----
def test_practice_squad_limit_and_eligibility():
    pool = [fa(2000 + i, "WR", 55 + i % 10, age=23, yoe=i % 3) for i in range(120)]
    pool += [fa(3000 + i, "CB", 66, age=30, yoe=8) for i in range(80)]           # veterans: 4 per team at most
    lg = base_league(pool)
    # team 4's squad: 13 players, 6 of them veterans
    ids = Ids(600_000)
    for i in range(13):
        p = player(ids(), 4, "LB", 50 + i, age=28 if i < 6 else 22, yoe=6 if i < 6 else 1, status="PS")
        lg["players"][p["id"]] = p
    moves = te.plan_practice_squad(lg, "seed")
    ps = {t: [p for p in lg["players"].values() if p["status"] == "PS" and p["team_id"] == t] for t in TEAMS}
    for m in moves:
        p = lg["players"][m["player_id"]]
        if m["kind"] == "SIGN":
            assert m["list"] == "PracticeSquad" and m["contract"] is None
            ps[m["team_id"]].append(p)
        elif m["kind"] == "RELEASE":
            assert m["list"] == "PracticeSquad"
            ps[m["team_id"]].remove(p)
        elif m["kind"] == "PS_PROMOTE":
            ps[m["team_id"]].remove(p)
            assert m["contract"]["contract_type"] == "MINIMUM"
    signed = {m["player_id"] for m in by_kind(moves, "SIGN")}
    assert len(signed) == len(by_kind(moves, "SIGN"))      # nobody signs twice
    for t, squad in ps.items():
        assert len(squad) <= te.PRACTICE_SQUAD_LIMIT
        assert sum(1 for p in squad if te.ps_eligible(p, lg) == "VETERAN") <= te.PS_VETERAN_SLOTS
    assert len([m for m in moves if m["kind"] == "RELEASE" and m["team_id"] == 4]) >= 1
    assert len({m["team_id"] for m in by_kind(moves, "SIGN")}) >= 15


def test_practice_squad_demotes_over_the_limit_and_promotes_into_open_spots():
    lg = base_league(roster_size=te.ROSTER_LIMIT + 2)
    for p in lg["players"].values():                        # team 7's fillers are 2nd-year players
        if p["team_id"] == 7 and p["ovr"] == 60:
            p["yoe"] = 0
    moves = [m for m in te.plan_practice_squad(lg, "s") if m["team_id"] == 7]
    assert len(by_kind(moves, "PS_DEMOTE")) == 2
    lg2 = base_league()
    ps = player(42, 8, "WR", 77, age=22, yoe=1, status="PS")      # better than team 8's weakest WR starter
    lg2["players"][42] = ps
    promo = [m for m in te.plan_practice_squad(lg2, "s") if m["kind"] == "PS_PROMOTE"]
    assert [m["player_id"] for m in promo] == [42]
    assert promo[0]["contract"]["salary_by_year"] == [ce.minimum_salary(2, CAP)]


# ----------------------------------------------------------- rookie deals ----
def test_rookie_deals_from_the_scale():
    lg = base_league()
    drafted = [{"player_id": 70_000 + k, "team_id": 1 + k % 32, "overall_pick": k} for k in (1, 33, 224, 250)]
    drafted.append({"player_id": None, "team_id": 2, "overall_pick": 5})
    moves = te.plan_rookies(lg, drafted)
    assert [m["overall_pick"] for m in moves] == [1, 33, 224, 250]
    for m in moves:
        assert m["kind"] == "SIGN" and m["list"] == "Roster"
        assert m["contract"] == ce.rookie_contract(min(m["overall_pick"], 224), T, CAP)
    assert moves[0]["round"] == 1 and moves[1]["round"] == 2


# ------------------------------------------------------------ determinism ----
def test_seeded_results_reproducible():
    pool = [fa(900 + i, g, 80 + i % 5) for i, g in enumerate(["QB", "WR", "CB", "EDGE"] * 4)]
    stars = [player(10 + i, 3, "EDGE", 88, age=26, yoe=6, aav=1_000_000, start=S - 2) for i in range(4)]
    young = [fa(2000 + i, "WR", 55 + i % 7, age=22, yoe=0) for i in range(60)]

    def run(seed):
        lg = base_league(pool + stars + young, wins={t: t % 9 for t in TEAMS})
        offers = te.plan_offers(lg, "FREE_AGENCY", 2, seed)
        strip = [{k: v for k, v in o.items() if k not in ("team", "market")} for o in offers]
        return (te.plan_resign(lg, seed), strip, te.plan_practice_squad(lg, seed),
                te.plan_waivers(lg, [10, 11], seed))

    assert run("abc") == run("abc")
    assert run("abc")[1] != run("xyz")[1]                   # the offer noise follows the seed


# ------------------------------------------------------- DB wrappers / fake ----
class FakeCursor:
    """Answers the engine's SELECTs from synthetic rows and records its INSERTs."""

    def __init__(self, lg, prospects=()):
        self.lg, self.prospects, self.inserts, self._rows, self._next_id = lg, prospects, [], [], 0
        self.contracts = {}
        for p in lg["players"].values():
            if p["contract"]:
                cid = 10_000 + p["id"]
                c = p["contract"]
                start = p["contract_start"]
                self.contracts[cid] = (p, {start + i: ce.cap_hit(c, i) for i in range(c["length"])})

    def execute(self, sql, params=None):
        self._rows = []
        if sql.lstrip().startswith("INSERT"):
            self.inserts.append((sql, params))
            self._next_id += 1
            self._rows = [(self._next_id,)]
        elif "FROM teams t" in sql:
            self._rows = [(t, x["abbr"], x["wins"], x["losses"], 50, x["direction"])
                          for t, x in self.lg["teams"].items()]
        elif "FROM team_cap_seasons" in sql:
            self._rows = [(t, S, cap) for t, cap in self.lg["cap"].items()]
        elif "FROM player_seasons ps" in sql:
            self._rows = [(p["id"], p["team_id"], p["pos"], p["ovr"], p["age"], p["dev"], p["yoe"], None)
                          for p in self.lg["players"].values()]
        elif "FROM player_contracts c" in sql:
            for cid, (p, _) in self.contracts.items():
                c = p["contract"]
                rem = c["length"] - (S - p["contract_start"])
                self._rows.append((cid, p["id"], p["team_id"], S, c["length"], rem,
                                   sum(c["salary_by_year"]) + sum(c["bonus_by_year"]), sum(c["bonus_by_year"]),
                                   "ACTIVE", c["contract_type"], 0, c["signed_season"], None, None,
                                   c["drafted_round"]))
        elif "FROM player_contract_years" in sql:
            self._rows = [(cid, y, h) for cid, (_, years) in self.contracts.items() for y, h in years.items()]
        elif "FROM draft_prospects" in sql:
            self._rows = [(d["player_id"], d["team_id"], d["overall_pick"]) for d in self.prospects]
        else:
            raise AssertionError(f"unexpected SQL: {sql[:60]}")

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


@pytest.fixture
def fake_fits(monkeypatch):
    monkeypatch.setattr(te, "fetch_fits", lambda cur, season: FITS)


def test_fetch_league_statuses_and_contracts(fake_fits):
    src = base_league([fa(901, "QB", 80), player(902, 5, "WR", 55, yoe=0, status="PS"),
                       player(903, 6, "CB", 70, aav=3_000_000, length=3, start=S - 2, bonus=600_000)])
    lg = te.fetch_league(FakeCursor(src), S)
    assert lg["target"] == T and len(lg["teams"]) == 32
    assert lg["players"][901]["status"] == "FA" and lg["players"][902]["status"] == "PS"
    p = lg["players"][903]
    assert p["status"] == "ROSTER" and p["contract_start"] == S - 2 and te.end_season(p) == S
    assert [ce.cap_hit(p["contract"], i) for i in range(3)] == \
        [ce.cap_hit(src["players"][903]["contract"], i) for i in range(3)]


def test_public_functions_cover_32_teams_and_store_rows(fake_fits):
    pool = [fa(900 + i, g, 82) for i, g in enumerate(["QB", "WR", "CB", "EDGE", "OT", "S"] * 6)]
    young = [fa(2000 + i, "WR", 58, age=22, yoe=0) for i in range(400)]
    expiring = [player(5000 + t, t, "WR", 74, age=27, yoe=5, aav=900_000, start=S - 2) for t in TEAMS]
    src = base_league(pool + young + expiring, roster_size=67, wins={t: t % 13 for t in TEAMS})
    picks = [{"player_id": 70_000 + k, "team_id": (k - 1) % 32 + 1, "overall_pick": k} for k in range(1, 225)]

    cur = FakeCursor(src, picks)
    resign = te.resign_decisions(cur, S, "seed")
    assert {m["team_id"] for m in resign} == set(TEAMS)
    assert {m["kind"] for m in resign} <= {"CONTRACT", "TAG", "EXTENSION", "OPTION", "LET_GO"}

    cut = te.cutdown(cur, S, "seed")
    assert {m["team_id"] for m in cut} == set(TEAMS) and all(m["kind"] == "RELEASE" for m in cut)

    rookies = te.rookie_signings(cur, S)
    assert len(rookies) == 224 and {m["team_id"] for m in rookies} == set(TEAMS)

    ps = te.practice_squad(cur, S, "seed")
    assert {m["team_id"] for m in ps if m["kind"] == "SIGN"} == set(TEAMS)

    # offers: a team below the limit; cpu_offers stores every offer in contract_offers
    src2 = base_league(pool)
    cur2 = FakeCursor(src2)
    offers = te.cpu_offers(cur2, S, "FREE_AGENCY", 3, "seed")
    rows = [p for sql, p in cur2.inserts if "INSERT INTO contract_offers" in sql]
    assert offers and len(rows) == len(offers)
    assert {o["team_id"] for o in offers} == set(TEAMS)
    for o, r in zip(offers, rows):
        assert o["offer_id"] is not None
        assert r == (S, "FREE_AGENCY", 3, "FREE_AGENT", o["player_id"], o["team_id"], o["years"], o["total_value"],
                     o["bonus"], o["guaranteed"], "seed")

    # waivers: claims stored with awarded flags, one awarded per claimed player
    cur3 = FakeCursor(src2)
    released = [p["id"] for p in src2["players"].values() if p["team_id"] == 1 and p["ovr"] == 75][:4]
    moves = te.waivers(cur3, S, 31, released, "seed")
    rows = [p for sql, p in cur3.inserts if "INSERT INTO waiver_claims" in sql]
    assert rows and all(r[0] == S and r[1] == 31 for r in rows)
    for pid in {r[2] for r in rows}:
        assert sum(1 for r in rows if r[2] == pid and r[5]) == 1
    assert len(by_kind(moves, "WAIVER_CLAIM")) == len({r[2] for r in rows})
