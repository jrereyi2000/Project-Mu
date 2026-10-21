"""Front-office player development score (engines/dev_quality.py). Pure: never touches the database."""
from engines import dev_quality as dq


def win(pid, team, age, start, end, dev="Normal", dev_end=None, rookie=False):
    return {"player_id": pid, "name": f"P{pid}", "position": "WR", "team_id": team, "age": age,
            "ovr_start": start, "ovr_end": end, "dev_start": dev, "dev_end": dev_end or dev, "rookie": rookie}


def league():
    """Two teams with the same ages and ratings: team 1's players beat the age curve by 2, team 2's miss by 2.
    Young players gain, old ones decline, so raw deltas alone would reward the younger roster."""
    rows, pid = [], 0
    for team, edge in ((1, 1), (2, -1)):
        for i in range(30):
            pid += 1
            age = 22 + i % 12
            base = 3 if age <= 24 else 0 if age <= 29 else -2
            rows.append(win(pid, team, age, 70 + i % 10, 70 + i % 10 + base + edge))
    return rows


def test_dev_rank_unknown_is_not_normal():
    assert dq.dev_rank("Hidden") is None and dq.dev_rank(None) is None
    assert [dq.dev_rank(d) for d in ("Normal", "Star", "Superstar", "Superstar X-Factor")] == [0, 1, 2, 3]


def test_age_curve_is_removed():
    scored = dq.score_window(league())
    by = {t: [p["r"] for p in scored if p["team_id"] == t] for t in (1, 2)}
    assert sum(by[1]) / 30 > 0.5 > -0.5 > sum(by[2]) / 30
    assert all(abs(p["expected"] - (p["delta"] - (1 if p["team_id"] == 1 else -1))) < 0.6 for p in scored)


def test_dev_upgrade_bonus_needs_both_ends_revealed():
    rows = league()
    rows[0] = dict(rows[0], dev_start="Normal", dev_end="Star")
    rows[1] = dict(rows[1], dev_start="Hidden", dev_end="Superstar")
    beta, sds = dq.fit_expected(rows)
    assert dq.player_score(rows[0], beta, sds)[2] == 1
    assert dq.player_score(rows[1], beta, sds)[2] == 0


def test_ovr_bands():
    assert [dq.ovr_band(o) for o in (55, 69, 70, 79, 80, 84, 85, 90, 94, 95, 99)] == [0, 0, 1, 1, 2, 2, 3, 4, 4, 5, 5]


def tiered():
    """60 players in the 70s who move +-1 and 60 in the 90s who move +-3: the same +3 surprise is rarer in the 70s."""
    rows = []
    for i in range(120):
        top = i >= 60
        swing = (3 if top else 1) * (1 if i % 2 else -1)
        start = 92 if top else 75
        rows.append(win(i, 1 + i % 2, 27, start, start + swing))
    return rows


def test_per_band_spread():
    rows = tiered()
    beta, sds = dq.fit_expected(rows)
    assert sds[4] > 2 * sds[1]
    low = dq.player_score(win(900, 1, 27, 75, 78), beta, sds)[0]
    high = dq.player_score(win(901, 1, 27, 92, 95), beta, sds)[0]
    assert low > 2 * high > 0


def test_small_band_uses_league_spread():
    rows = tiered() + [win(500 + i, 1, 27, 60, 60 + (i % 3) - 1) for i in range(5)]
    _, sds = dq.fit_expected(rows)
    assert sds[0] == sds[None]


def test_cap_rule_holds_99s_at_zero_or_better():
    rows = tiered() + [win(700 + i, 1, 27, 99, 99 - (i % 2)) for i in range(30)]   # 99s usually slip
    beta, sds = dq.fit_expected(rows)
    held = win(800, 1, 27, 99, 99)
    assert dq.player_score(held, beta, sds)[0] >= 0
    slipped = dq.player_score(win(801, 1, 27, 99, 96), beta, sds)[0]
    assert slipped < 0                                              # dropping off the cap still counts
    capped = [p for p in dq.score_window(rows + [held]) if p["ovr_start"] == 99 and p["ovr_end"] == 99]
    assert capped and all(p["r"] >= 0 and p["label"] == "ON TRACK" for p in capped)


def test_labels_never_contradict_ratings():
    assert dq.label(1.5, 0) == "ON TRACK"            # beat an expected decline by holding steady
    assert dq.label(1.5, 2) == "DEVELOPED"
    assert dq.label(-1.5, 1) == "ON TRACK"           # gained, just less than expected
    assert dq.label(-1.5, 0) == "STALLED"
    assert dq.label(-2.5, -3) == "REGRESSED" and dq.label(-2.5, 0) == "STALLED"


def test_starters_weigh_more_and_shrinkage():
    rows = [dict(p, starter=i < 22) for i, p in enumerate(
        {"r": 1.0 if i < 22 else -1.0} for i in range(30))]
    res = dq.team_score({0: rows})
    assert res["n"] == 22 + 8 * dq.DEPTH_WEIGHT
    assert abs(res["D"] - (22 - 4) / 26) < 1e-3
    assert res["D_star"] == round(res["D"] * 26 / (26 + dq.SHRINK_K), 3)
    assert dq.team_score({}) == {"D": None, "D_star": None, "n": 0.0}


def test_window_recency_weights():
    one = [{"r": 1.0, "starter": True}]
    res = dq.team_score({0: one, 2: [{"r": -1.0, "starter": True}]})
    assert abs(res["D"] - (1.0 - 0.35) / 1.35) < 1e-3
    assert dq.team_score({5: one})["n"] == 0.0       # outside the 3-window range


def test_windows_skip_the_end_of_season_2035_card():
    assert dq.windows_for(2036) == []
    assert dq.windows_for(2037) == [(0, 2037)]
    assert dq.windows_for(2039) == [(0, 2039), (1, 2038), (2, 2037)]
    assert dq.windows_for(2036, include_partial=True)[0] == (0, 2036)


def test_starter_flags_per_team():
    rows = [win(i, 1 + i % 2, 26, 60 + i, 60 + i) for i in range(60)]
    flags = dq.starter_flags(rows)
    for t in (1, 2):
        mine = sorted((r for r in rows if r["team_id"] == t), key=lambda r: -r["ovr_start"])
        assert [flags[r["player_id"]] for r in mine] == [True] * 22 + [False] * 8


def test_tier_from_rank_only():
    assert "best" in dq.tier(1, 30) and "worst" in dq.tier(30, 30) and "middle" in dq.tier(15, 30)
    assert dq.tier(None, 30) is None
