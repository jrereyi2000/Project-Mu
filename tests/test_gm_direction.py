"""Team direction and needs (docs/done/team_management/03_GM_AGENTS_PLAN.md Phase 2). Pure: no database."""
from engines.gm_direction import (CONTEND_N, REBUILD_N, SLOTS, assign_directions, band_edges, compute,
                                  direction_scores, league_lines, positional_needs, slot_ovrs, starter_slots)


def team(i, wins=1, losses=2, ovr=82, prior=8, **kw):
    t = {"team_id": i, "abbr": f"T{i}", "wins": wins, "losses": losses, "pythagorean_wins": wins,
         "expected_wins": wins, "team_ovr": ovr, "opp_adj_credit_recent": 0.0, "trajectory_multiplier": 1.0,
         "prior_wins": prior, "starter_age": 26.0, "aggressiveness": 0.5, "cap_space": 10_000_000, "previous": None}
    t.update(kw)
    return t


def league(n=32):
    return [team(i, wins=i % 4, losses=3 - i % 4, ovr=78 + i % 12, prior=(i * 7) % 17) for i in range(n)]


def test_band_counts():
    dirs = assign_directions(direction_scores(league()))
    vals = [d for d, _ in dirs.values()]
    assert vals.count("CONTEND") == CONTEND_N and vals.count("REBUILD") == REBUILD_N
    assert vals.count("RETOOL") == 32 - CONTEND_N - REBUILD_N


def test_hysteresis_holds_inside_margin():
    edges = (1.0, -1.0)
    def d(D, prev):
        return assign_directions({"x": {"D": D, "proj_wins": 8.0}}, {"x": prev}, edges=edges)["x"][0]
    assert d(1.1, None) == "CONTEND"
    assert d(1.1, "RETOOL") == "RETOOL" and d(1.4, "RETOOL") == "CONTEND"
    assert d(0.9, "CONTEND") == "CONTEND" and d(0.6, "CONTEND") == "RETOOL"
    assert d(-1.2, "RETOOL") == "RETOOL" and d(-1.4, "RETOOL") == "REBUILD"
    assert d(-0.8, "REBUILD") == "REBUILD" and d(-0.6, "REBUILD") == "RETOOL"


def test_overrides():
    scores = direction_scores(league())
    low = min(scores, key=lambda t: scores[t]["D"])
    assert assign_directions(scores, cap_space={low: -1})[low] == ("RETOOL", "over the cap")
    scores[low] = dict(scores[low], proj_wins=11.0)
    assert assign_directions(scores)[low][0] == "CONTEND"


def test_early_season_prior_dominates():
    # 0-3 with a strong prior and top OVR still projects well above a 3-0 team with a weak prior and low OVR
    teams = league(30) + [team(100, wins=0, losses=3, ovr=95, prior=15), team(101, wins=3, losses=0, ovr=70, prior=2)]
    s = direction_scores(teams)
    assert s[100]["p"] > 0.5 and s[101]["p"] < s[100]["p"] + 0.3
    assert s[100]["prior"] > s[100]["form"]


def roster(tid=1, qb=80, extra=()):
    ps = [{"id": tid * 1000 + 1, "group": "QB", "ovr": qb, "age": 28}]
    pid = tid * 1000 + 10
    for g, n in SLOTS.items():
        if g == "QB":
            continue
        for k in range(n):
            pid += 1
            ps.append({"id": pid, "group": g, "ovr": 75, "age": 27})
    return ps + list(extra)


def lines_for(*teams):
    return league_lines({i: slot_ovrs(starter_slots(r)) for i, r in enumerate(teams)})


def test_injured_starter_opens_need():
    r = roster(qb=90, extra=[{"id": 9, "group": "QB", "ovr": 60, "age": 30}])
    lines = lines_for(r, roster(2, qb=90), roster(3, qb=90))
    assert not any(n["group"] == "QB" for n in positional_needs(r, set(), set(), lines)[0])
    needs, _ = positional_needs(r, {1001}, set(), lines)
    qb = next(n for n in needs if n["group"] == "QB")
    assert qb["slot_gap"] == 30 and qb["injury"] == 30


def test_expiring_weighted_more_for_rebuild():
    r = roster(qb=90, extra=[{"id": 9, "group": "QB", "ovr": 60, "age": 30}])
    lines = lines_for(r, roster(2, qb=90), roster(3, qb=90))
    c = next(n for n in positional_needs(r, set(), {1001}, lines, "CONTEND")[0] if n["group"] == "QB")
    b = next(n for n in positional_needs(r, set(), {1001}, lines, "REBUILD")[0] if n["group"] == "QB")
    assert b["expiring"] == 1.5 * c["expiring"] > 0


def test_surplus_depth_within_three():
    r = roster(extra=[{"id": 77, "group": "CB", "ovr": 73, "age": 25}, {"id": 78, "group": "CB", "ovr": 60, "age": 25}])
    _, surplus = positional_needs(r, set(), set(), lines_for(r))
    assert {"group": "CB", "player_ids": [77]} in surplus


def test_def_rows_ignored_and_direction_kept_off_checkpoint():
    lg = league(32)
    data = {"teams": lg, "roster": {t["team_id"]: roster(t["team_id"]) + [{"id": -t["team_id"], "group": None,
                                                                         "ovr": 99, "age": 0}] for t in lg},
            "injuries": {}, "expiring": set()}
    rows = compute(data, week=4)
    assert all(r["set_direction"] for r in rows.values())                # first run: every team gets one
    for t in lg:
        t["previous"] = "RETOOL"
    rows = compute(data, week=4)
    assert all(r["direction"] == "RETOOL" and not r["set_direction"] for r in rows.values())
    rows = compute(data, week=6)                                          # checkpoint: re-banded
    assert any(r["direction"] != "RETOOL" for r in rows.values())
