"""Front-office quality from draft track records (docs/team_management/04_SCOUTING_AND_DRAFT_PLAN.md Phase 3). Pure."""
from engines import fo_quality as fq


def row(pick, team, ovr, dev="Normal", ds=2036, pid=None):
    return {"draft_season": ds, "overall_pick": pick, "drafted_team_id": team, "roster_team_id": team + 100,
            "player_id": pid or pick, "name": f"P{pick}", "position": "WR", "ovr": ovr, "dev": dev}


def test_clamp():
    assert fq.team_q([{"draft_season": 2036, "h": 50}] * 40, 2036)["q"] == fq.Q_MIN
    assert fq.team_q([{"draft_season": 2036, "h": -50}] * 40, 2036)["q"] == fq.Q_MAX


def test_no_picks_is_neutral():
    assert fq.team_q([], 2036) == {"q": 1.0, "H": None, "H_star": None, "n": 0}
    assert fq.team_q([{"draft_season": 2030, "h": 3}], 2036)["n"] == 0          # outside the 3-draft window


def test_recency_weights():
    picks = [{"draft_season": 2036, "h": 1.0}, {"draft_season": 2034, "h": -1.0}]
    r = fq.team_q(picks, 2036)
    assert abs(r["H"] - (1.0 - 0.35) / 1.35) < 1e-3                             # newest weighs most
    assert fq.team_q([{"draft_season": 2036, "h": 1.0}], 2036)["q"] < 1 < fq.team_q([{"draft_season": 2036, "h": -1.0}], 2036)["q"]


def test_shrinkage_at_one_pick():
    r = fq.team_q([{"draft_season": 2036, "h": 2.0}], 2036)
    assert r["H"] == 2.0 and r["H_star"] == 2.0 / 5 and r["q"] == round(1 - 0.25 * 0.4, 2)


def test_dev_bonus_only_when_revealed():
    assert fq.dev_bonus("Hidden") == (0.0, False)
    assert fq.dev_bonus(None) == (0.0, False)
    assert fq.dev_bonus("Normal") == (0.0, True)
    assert fq.dev_bonus("Star") == (0.5, True) and fq.dev_bonus("Superstar") == (1.0, True)
    assert fq.dev_bonus("Superstar X-Factor") == (1.5, True)
    h_hidden, known = fq.hit_score(75, 75, 3, "Hidden")
    h_star, _ = fq.hit_score(75, 75, 3, "Star")
    assert (h_hidden, known) == (0.0, False) and h_star == 0.5


def test_labels():
    assert [fq.label(h) for h in (0.6, 0.5, 0.0, -0.5, -1.0, -1.2, -2)] == ["HIT", "HIT", "OK", "MISS", "MISS",
                                                                           "BUST", "BUST"]


def test_scoring_skips_unlinked_and_excluded_and_credits_drafting_team():
    rows = [row(p, 1 + p % 4, 80 - p // 4) for p in range(1, 65)] + [row(5, 9, 99)]   # dup slot averages in
    rows.append(row(70, 1, 99))                                                          # Round 3: not scored
    scored = fq.score_all(rows, 2036, "drafted", excluded_teams={2}, jr_picks={(2036, 4)})
    assert 2 not in scored                                          # JR's team: excluded
    assert all(p["pick"] != 4 for ps in scored.values() for p in ps)
    assert all(p["pick"] <= 64 for ps in scored.values() for p in ps)
    roster = fq.score_all(rows, 2036, "roster")
    assert set(roster) >= {101, 103}                                 # --credit roster uses the current team
    # an unlinked pick never reaches score_all (fetch_rows joins player_seasons); nothing scores it as a miss
    assert sum(len(v) for v in scored.values()) == 64 + 1 - 16 - 1


def test_streaks_and_inputs():
    picks = [{"draft_season": 2036, "round": 1, "label": "MISS"}, {"draft_season": 2035, "round": 1, "label": "BUST"},
             {"draft_season": 2034, "round": 1, "label": "HIT"}, {"draft_season": 2036, "round": 2, "label": "HIT"}]
    assert fq.streaks(picks) == {"first_round_misses": 2, "first_round_hits": 0}


def test_expected_curve_is_monotone():
    curve, sd = fq.fit_expected([(p, 80 - p / 10 + (p % 3)) for p in range(1, 225)])
    assert all(a >= b for a, b in zip(curve, curve[1:])) and sd >= fq.SD_FLOOR
