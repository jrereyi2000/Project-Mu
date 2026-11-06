"""Media voters: engines/award_candidates.py, newsroom/award_ballots.py and the stored-result checks in
newsroom/award_winners.py and newsroom/all_pro.py (OWNERS_SCOUTS_VOTERS_PLAN.md §2.5, §4). Synthetic seasons and
fake cursors only; no DB."""
import json
import random
from unittest.mock import MagicMock

import pytest

from engines import award_candidates as ac
from newsroom import award_ballots as ab
from newsroom import all_pro
from newsroom import award_winners as aw

TEAMS = list(range(1, 33))


def season_lines(seed=7):
    """A synthetic league: per team a QB, 2 RB, 4 WR, 2 TE, 5 OL, 4 EDGE, 3 DT, 4 LB, 4 CB, 3 S, K, P, LS."""
    rng = random.Random(seed)
    lines, pid = [], 1000
    roster = [("QB", 1), ("HB", 2), ("WR", 4), ("TE", 2), ("LT", 1), ("LG", 1), ("C", 1), ("RG", 1), ("RT", 1),
              ("LEDG", 2), ("REDG", 2), ("DT", 3), ("MIKE", 2), ("WILL", 2), ("CB", 4), ("FS", 2), ("SS", 1),
              ("K", 1), ("P", 1), ("LS", 1)]
    for t in TEAMS:
        for pos, n in roster:
            for _ in range(n):
                pid += 1
                ln = {"player_id": pid, "name": f"Player {pid}", "team_id": t, "position": pos,
                      "overall_rating": rng.randint(60, 95), "rookie": rng.random() < 0.15,
                      "games": rng.choice([17, 17, 16, 15, 12, 8]), "downs_played": rng.randint(300, 1100)}
                if pos == "QB":
                    ln.update(pass_yards=rng.randint(2500, 4800), pass_tds=rng.randint(12, 38),
                              interceptions=rng.randint(4, 16), pass_rating=rng.uniform(75, 112),
                              rush_yards=rng.randint(0, 600), rush_tds=rng.randint(0, 6))
                elif pos == "HB":
                    ln.update(rush_yards=rng.randint(200, 1500), rush_attempts=rng.randint(80, 320),
                              rush_tds=rng.randint(0, 14), receptions=rng.randint(5, 60), rec_yards=rng.randint(30, 500))
                elif pos in ("WR", "TE"):
                    ln.update(receptions=rng.randint(10, 110), rec_yards=rng.randint(100, 1500),
                              rec_tds=rng.randint(0, 13))
                    if pos == "WR" and rng.random() < 0.2:
                        ln.update(kr_returns=rng.randint(5, 40), kr_yards=rng.randint(100, 1000), kr_avg=rng.uniform(18, 30),
                                  pr_returns=rng.randint(5, 30), pr_yards=rng.randint(30, 400), pr_avg=rng.uniform(5, 14))
                elif pos in ("LEDG", "REDG", "DT", "MIKE", "WILL", "CB", "FS", "SS"):
                    ln.update(tackles=rng.randint(10, 130), tfl=rng.randint(0, 18), sacks=rng.randint(0, 14),
                              ints=rng.randint(0, 6), deflections=rng.randint(0, 18), forced_fumbles=rng.randint(0, 4))
                elif pos in ("LT", "LG", "C", "RG", "RT"):
                    ln.update(sacks_allowed=rng.randint(0, 9))
                elif pos == "K":
                    ln.update(fgm=rng.randint(18, 36), fga=40, fg_pct=rng.uniform(75, 96), xpm=rng.randint(25, 55))
                elif pos == "P":
                    ln.update(punts=rng.randint(50, 90), punt_yards=rng.randint(2400, 4200),
                              punt_net_avg=rng.uniform(38, 46), punts_in20=rng.randint(15, 38))
                lines.append(ln)
    wins = {t: rng.randint(3, 14) for t in TEAMS}
    return lines, wins


@pytest.fixture(scope="module")
def season():
    return season_lines()


VOTERS = ab.load_voters()


def test_panel_is_twenty_national_writers_with_traits():
    assert len(ab.VOTER_ARCHETYPES) == 20 == len({v["key"] for v in VOTERS})
    assert all(v["traits"] for v in VOTERS)
    for joke in ("NFL_MEMES", "BANDWAGON_FAN", "DIE_HARD_FAN", "FANTASY_GURU", "AGGREGATOR"):
        assert joke not in ab.VOTER_ARCHETYPES
    assert any(v["awards_view"] for v in VOTERS)


def test_candidate_metrics_on_a_synthetic_season(season):
    lines, wins = season
    mvp = ac.candidates_from_lines(lines, "MVP", wins)
    assert mvp and all(set(c["metrics"]) >= {"production", "efficiency", "wins", "record_pace"} for c in mvp)
    assert mvp[0]["base_score"] >= mvp[-1]["base_score"]
    assert all(ac.position_group(c["position"]) != "DEF" for c in mvp)
    dpoy = ac.candidates_from_lines(lines, "DPOY", wins)
    assert all(ac.position_group(c["position"]) == "DEF" for c in dpoy)
    rookies = {ln["player_id"] for ln in lines if ln["rookie"]}
    assert {c["id"] for c in ac.candidates_from_lines(lines, "OROY", wins)} <= rookies
    assert {c["id"] for c in ac.candidates_from_lines(lines, "DROY", wins)} <= rookies


def test_a_dominant_stat_leader_wins_and_the_minimum_games_rule(season):
    lines, wins = [dict(ln) for ln in season[0]], dict(season[1])
    star = next(ln for ln in lines if ln["position"] == "LEDG")
    star.update(sacks=30, tfl=30, forced_fumbles=8, games=17)
    wins[star["team_id"]] = 13
    cands = ac.candidates_from_lines(lines, "DPOY", wins)[:ac.POOL]
    t = ab.tally([{"voter": v["key"], "award": "DPOY", "ranking": ab.ballot(v, cands, "DPOY", 2026)}
                  for v in VOTERS])
    assert t["DPOY"][0]["id"] == star["player_id"] and t["DPOY"][0]["first_place"] >= 15
    star["games"] = 6
    assert star["player_id"] not in {c["id"] for c in ac.candidates_from_lines(lines, "DPOY", wins)}
    star["games"] = None                     # no games row: counts as eligible
    assert star["player_id"] in {c["id"] for c in ac.candidates_from_lines(lines, "DPOY", wins)}


def test_ballots_are_deterministic_and_leaned_by_voter_traits(season):
    lines, wins = season
    cands = ac.candidates_from_lines(lines, "MVP", wins)[:ac.POOL]
    v = VOTERS[0]
    assert ab.ballot(v, cands, "MVP", 2026) == ab.ballot(v, cands, "MVP", 2026)
    assert len(ab.ballot(v, cands, "MVP", 2026)) == 5
    analytic = {"key": "x", "traits": ["ANALYTICS_BELIEVER"]}
    old = {"key": "x", "traits": ["OLD_SCHOOL"]}
    hist = {"key": "x", "traits": ["HISTORIAN"], "awards_view": True}
    base = ac.AWARD_WEIGHTS["MVP"]
    assert ab.voter_weights(analytic, "MVP")["efficiency"] > base["efficiency"]
    assert ab.voter_weights(old, "MVP")["wins"] > base["wins"]
    assert ab.voter_weights(old, "MVP")["efficiency"] < base["efficiency"]
    assert ab.voter_weights(hist, "MVP")["record_pace"] > 0 and ab.voter_weights(hist, "MVP")["wins"] > base["wins"]
    # an efficiency-only candidate against a wins-only candidate: the analyst and the old-school voter split
    eff = {"id": 1, "position": "WR", "metrics": {"production": 0.0, "efficiency": 3.0, "wins": -1.0}}
    win = {"id": 2, "position": "WR", "metrics": {"production": 0.0, "efficiency": -1.0, "wins": 3.0}}
    a_first = sum(ab.ballot(dict(analytic, key=f"a{i}"), [eff, win], "OPOY", 2026)[0] == 1 for i in range(200))
    o_first = sum(ab.ballot(dict(old, key=f"o{i}"), [eff, win], "OPOY", 2026)[0] == 1 for i in range(200))
    assert a_first > o_first


def test_points_and_the_tie_rule():
    ballots = [{"voter": "a", "award": "MVP", "ranking": [1, 2, 3, 4, 5]},
               {"voter": "b", "award": "MVP", "ranking": [2, 1, 3, 4, 5]},
               {"voter": "c", "award": "MVP", "ranking": [3, 1, 2, 4, 5]}]
    t = ab.tally(ballots)["MVP"]
    assert [(r["id"], r["points"], r["first_place"]) for r in t[:3]] == [(1, 20, 1), (2, 18, 1), (3, 16, 1)]
    tie = ab.tally([{"voter": "a", "award": "OPOY", "ranking": [7, 8]},
                    {"voter": "b", "award": "OPOY", "ranking": [9, 7]},
                    {"voter": "c", "award": "OPOY", "ranking": [9]},
                    {"voter": "d", "award": "OPOY", "ranking": [8, 7]}])["OPOY"]
    # 7: 10+5+5 = 20 (1 first), 9: 20 (2 firsts) -> 9 wins the tie on first-place votes
    assert tie[0]["id"] == 9 and tie[1]["id"] == 7 and tie[0]["points"] == tie[1]["points"] == 20


def test_coty_candidates_from_coty_scores():
    rows = [{"coach_id": 5, "coach_name": "A", "team_id": 1, "coty_score": 72.0, "win_pct": 0.8},
            {"coach_id": 6, "coach_name": "B", "team_id": 2, "coty_score": 48.0, "win_pct": 0.4}]
    c = ac.coty_candidates_from_rows(rows)
    assert [x["id"] for x in c] == [5, 6] and c[0]["base_score"] == 2.2
    b = ab.ballot(VOTERS[0], c, "COTY", 2026)
    assert set(b) == {5, 6}


def fake_cur(rows=()):
    cur = MagicMock()
    cur.fetchall.return_value = list(rows)
    return cur


def test_storage_writes_winners_and_ballots(season):
    lines, wins = season
    cands = {a: ac.candidates_from_lines(lines, a, wins)[:ac.POOL] for a in ac.PLAYER_AWARDS}
    cands["COTY"] = ac.coty_candidates_from_rows([{"coach_id": 5, "coach_name": "A", "team_id": 1,
                                                    "coty_score": 70.0, "win_pct": 0.8}])
    ballots = [{"voter": v["key"], "award": a, "ranking": ab.ballot(v, cands[a], a, 2026)}
               for a in ab.AWARDS for v in VOTERS]
    tallies = ab.tally(ballots)
    cur = fake_cur()
    assert ab.store_results(cur, 2026, tallies, cands, ballots) == 6
    sqls = [c.args[0] for c in cur.execute.call_args_list]
    assert "DELETE FROM awards" in sqls[0]
    inserts = [c.args for c in cur.execute.call_args_list if "INSERT INTO awards" in c.args[0]]
    assert len(inserts) == 6
    mvp = next(a[1] for a in inserts if a[1][1] == "MVP")
    assert mvp[2] == tallies["MVP"][0]["id"]
    coty = next(a[1] for a in inserts if "COTY" in a[0])
    assert coty == (2026, 5, "A", 1)
    rows = cur.executemany.call_args.args[1]
    assert "INSERT INTO award_ballots" in cur.executemany.call_args.args[0]
    assert len(rows) == 6 * 20 and rows[0][4] == "ballot-v1" and json.loads(rows[0][3])
    facts = ab.results_facts(tallies, cands)
    assert facts["MVP"]["winner"] and len(facts["MVP"]["top5"]) == 5


def roster_index_for(lines):
    teams = [(t, f"Team{t}", f"City{t}", f"T{t}") for t in TEAMS]
    return {"teams": teams, "players": [
        {"player_id": ln["player_id"], "full_name": ln["name"], "player_name": ln["name"], "team_id": ln["team_id"],
         "position": ln["position"], "team_name": f"Team{ln['team_id']}", "city": "", "abbreviation": ""}
        for ln in lines]}


def test_article_validation_checks_the_stored_winners():
    roster = {"teams": [(1, "Panthers", "Carolina", "CAR"), (2, "Broncos", "Denver", "DEN")], "players": [
        {"player_id": p, "full_name": n, "player_name": n, "team_id": t, "position": pos, "team_name": "",
         "city": "", "abbreviation": ""} for p, n, t, pos in [
            (1, "Al One", 1, "QB"), (2, "Bo Two", 1, "WR"), (3, "Cy Three", 2, "HB"), (4, "Di Four", 2, "CB"),
            (5, "Ed Five", 1, "LEDG"), (6, "Fa Six", 2, "WR")]]}
    coaches = {"teams": roster["teams"], "coaches": [{"coach_id": 9, "coach_name": "Gus Coach", "team_id": 1,
                                                      "team_name": "Panthers", "city": "", "abbreviation": "CAR"}]}
    text = ("## MVP: Al One, QB, Panthers\n## OPOY: Bo Two, WR, Panthers\n## OROY: Cy Three, HB, Broncos\n"
            "## DPOY: Ed Five, LEDG, Panthers\n## DROY: Di Four, CB, Broncos\n## Coach of the Year: Gus Coach, Panthers\n")
    stored = {"MVP": {"player_id": 1, "name": "Al One"}, "OPOY": {"player_id": 2, "name": "Bo Two"},
              "OROY": {"player_id": 3}, "DPOY": {"player_id": 5}, "DROY": {"player_id": 4},
              "COTY": {"coach_id": 9, "name": "Gus Coach"}}
    assert aw.check_against_stored(text, stored, roster, coaches) == []
    stored["OPOY"] = {"player_id": 6, "name": "Fa Six"}
    errs = aw.check_against_stored(text, stored, roster, coaches)
    assert len(errs) == 1 and "Fa Six" in errs[0] and "OPOY" in errs[0]
    assert "Fa Six" in aw.stored_winners_fix_prompt(errs, stored)
    cur = fake_cur([("MVP", 1, None, "Al One", 1), ("COTY", None, 9, "Gus Coach", 1)])
    assert aw.load_stored_winners(cur, 2026) == {"MVP": {"player_id": 1, "name": "Al One", "team_id": 1},
                                                 "COTY": {"coach_id": 9, "name": "Gus Coach", "team_id": 1}}


# ------------------------------------------------------------------------------------------------ All-Pro ---

@pytest.fixture(scope="module")
def all_pro_run(season):
    lines, wins = season
    cands = ac.all_pro_candidates_from_lines(lines, wins)
    ballots = [{"voter": v["key"], "slots": ab.all_pro_ballot(v, cands, 2026)} for v in VOTERS]
    return lines, cands, ballots, ab.all_pro_tally(ballots, cands)


def test_every_voter_fills_every_slot_quota(all_pro_run):
    _, cands, ballots, _ = all_pro_run
    assert set(cands) == set(all_pro.ALL_SLOT_QUOTAS)
    for b in ballots:
        assert {s: len(ids) for s, ids in b["slots"].items()} == all_pro.ALL_SLOT_QUOTAS
        flat = [i for ids in b["slots"].values() for i in ids]
        assert len(flat) == len(set(flat)) == 31
    assert ab.all_pro_ballot(VOTERS[0], cands, 2026) == ballots[0]["slots"]


def test_first_and_second_teams_by_votes_and_a_player_placed_once(all_pro_run):
    lines, cands, ballots, teams = all_pro_run
    placed = [e["id"] for team in teams.values() for es in team.values() for e in es]
    assert len(placed) == 62 == len(set(placed))
    for slot, quota in all_pro.ALL_SLOT_QUOTAS.items():
        assert len(teams["first"][slot]) == quota and len(teams["second"][slot]) == quota
        assert min(e["votes"] for e in teams["first"][slot]) >= max(e["votes"] for e in teams["second"][slot])
    pos = {ln["player_id"]: ln["position"] for ln in lines}
    for slot in ("QB", "WR", "EDGE", "CB", "K", "P"):
        allowed = all_pro.SLOT_COMPATIBLE_POSITIONS[slot]
        assert all(pos[e["id"]] in allowed for t in teams.values() for e in t[slot])


def test_all_pro_ties_go_to_the_higher_base_score():
    cands = {s: [] for s in all_pro.ALL_SLOT_QUOTAS}
    cands["QB"] = [{"id": 1, "base_score": 0.5}, {"id": 2, "base_score": 1.5}, {"id": 3, "base_score": 0.1}]
    ballots = [{"voter": "a", "slots": {"QB": [1]}}, {"voter": "b", "slots": {"QB": [2]}}]
    teams = ab.all_pro_tally(ballots, cands)
    assert teams["first"]["QB"] == [{"id": 2, "votes": 1}] and teams["second"]["QB"] == [{"id": 1, "votes": 1}]
    ballots = [{"voter": "a", "slots": {"QB": [1], "AP": [1]}}, {"voter": "b", "slots": {"QB": [1]}}]
    cands["AP"] = [{"id": 1, "base_score": 0.0}, {"id": 4, "base_score": 0.2}]
    teams = ab.all_pro_tally(ballots, cands)
    assert teams["first"]["QB"][0]["id"] == 1 and teams["first"]["AP"][0]["id"] == 4      # placed once, best slot


def test_all_pro_minimum_games(season):
    lines, wins = [dict(ln) for ln in season[0]], season[1]
    k = next(ln for ln in lines if ln["position"] == "K")
    k.update(fgm=60, fg_pct=100.0, games=5)
    assert k["player_id"] not in {c["id"] for c in ac.all_pro_candidates_from_lines(lines, wins)["K"]}


def test_store_all_pro_writes_62_rows_and_ballots(all_pro_run):
    _, cands, ballots, teams = all_pro_run
    cur = fake_cur()
    assert ab.store_all_pro(cur, 2026, teams, cands, ballots) == 62
    award_rows = cur.executemany.call_args_list[0].args[1]
    assert {r[1] for r in award_rows} == {"First Team All-Pro", "Second Team All-Pro"}
    ballot_rows = cur.executemany.call_args_list[1].args[1]
    assert len(ballot_rows) == 20 * len(all_pro.ALL_SLOT_QUOTAS)
    assert {r[1] for r in ballot_rows} == {"ALL_PRO:" + s for s in all_pro.ALL_SLOT_QUOTAS}
    facts = ab.all_pro_facts(teams, cands)
    assert sum(len(v) for v in facts["First Team All-Pro"].values()) == 31


def test_the_all_pro_article_is_checked_against_the_stored_teams(all_pro_run):
    lines, cands, ballots, teams = all_pro_run
    roster = roster_index_for(lines)
    names = {ln["player_id"]: ln for ln in lines}
    stored, out = [], []
    for level, team, header in (("First Team All-Pro", teams["first"], "## First-Team All-Pro"),
                                ("Second Team All-Pro", teams["second"], "## Second-Team All-Pro")):
        out.append(header)
        for slot in all_pro.ALL_SLOT_QUOTAS:
            for e in team[slot]:
                ln = names[e["id"]]
                out.append(f"- **{slot}**: {ln['name']}, Team{ln['team_id']} — great season")
                stored.append({"award_type": level, "slot": slot, "player_id": e["id"], "player_name": ln["name"]})
    text = "\n".join(out)
    assert all_pro.check_against_stored(text, stored, roster) == []
    wrong = list(stored)
    wrong[0] = dict(wrong[0], player_id=-1, player_name="Nobody")
    errs = all_pro.check_against_stored(text, wrong, roster)
    assert any("Nobody" in e and "missing" in e for e in errs) and any("not voted in" in e for e in errs)
    cur = fake_cur([("First Team All-Pro", "QB", 1, "Al", 3)])
    assert all_pro.load_stored_teams(cur, 2026) == [{"award_type": "First Team All-Pro", "slot": "QB",
                                                     "player_id": 1, "player_name": "Al", "team_id": 3}]
