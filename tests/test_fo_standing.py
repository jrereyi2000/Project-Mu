"""Front-office standings, tenure-scoped scores and the hot-seat term (engines/fo_standing.py, fo_quality,
dev_quality, gm_rollover). Pure: never touches the database."""
from engines import dev_quality as dq
from engines import fo_quality as fq
from engines import fo_standing as fs
from engines.gm_rollover import FO_WEIGHT, hot_seat


def team(tid, draft=None, dev=None, start=2033, held=False, n=6, seat=None):
    return {"team_id": tid, "abbr": f"T{tid}", "gm_name": f"GM {tid}", "start_season": start, "held": held,
            "draft": draft, "dev": dev, "draft_n": n, "hot_seat": seat}


def league():
    return [team(t, draft=(t - 16) / 50, dev=(16 - t) / 80 if t % 2 else (t - 16) / 80) for t in range(1, 33)]


def test_combine_ranks_all_32_including_jr():
    rows = league()
    rows[0] = dict(rows[0], held=True, draft=0.9, dev=0.9)
    out = fs.combine(rows)
    assert len(out) == 32 and out[0]["abbr"] == "T1" and out[0]["rank"] == 1
    assert [r["rank"] for r in out] == sorted(r["rank"] for r in out)
    assert all(r["teams"] == 32 for r in out)


def test_missing_part_is_baseline_and_unranked():
    out = {r["team_id"]: r for r in fs.combine([team(1, draft=0.4), team(2, draft=-0.4, dev=0.2), team(3)])}
    assert out[1]["score"] == 0.2 and out[1]["dev_rank"] is None and out[1]["draft_rank"] == 1
    assert out[3]["score"] == 0 and out[3]["draft_rank"] is None
    assert fs.standing_facts(out[3], 2036)["drafting"] == fs.NOT_JUDGED


def test_ties_share_a_rank():
    out = fs.combine([team(1, draft=0.2), team(2, draft=0.2), team(3, draft=-0.2)])
    assert sorted(r["rank"] for r in out) == [1, 1, 3]


def test_tiers_and_words():
    assert [fs.tier(r, 32) for r in (1, 8, 9, 24, 25, 32)] == ["top", "top", "middle", "middle", "bottom", "bottom"]
    assert [fs.hot_seat_words(s) for s in (None, 0.2, 0.5, 1.0)] == [None, "secure", "warming", "hot"]


def test_writer_facts_never_carry_numbers_or_jr():
    r = fs.combine(league())[0]
    facts = fs.standing_facts(r, 2036)
    assert "score" not in facts and "jr_runs_the_team" not in facts
    assert facts["front_office_rank"] == "1 of 32" and facts["seasons_in_job"] == 4 and not facts["new_gm"]
    early = fs.standing_facts(fs.combine([team(1, draft=0.1, n=2)])[0], 2036)
    assert early["early_read"]


def test_hot_seat_term_heats_bad_front_offices():
    base = {"team_id": 5, "abbr": "T5", "start_season": 2030, "direction": "RETOOL", "wins": 8, "wins_before": 8,
            "user_controlled": False}
    plain = hot_seat([base], 2036)[5]["score"]
    bad = hot_seat([dict(base, fo_score=-0.3, fo_rank=32)], 2036)[5]
    good = hot_seat([dict(base, fo_score=0.3, fo_rank=1)], 2036)[5]
    assert round(bad["score"] - plain, 3) == round(0.3 * FO_WEIGHT, 3) and good["score"] < plain
    assert bad["inputs"]["fo_rank"] == 32 and bad["inputs"]["fo_points"] == round(0.3 * FO_WEIGHT, 3)
    jr = hot_seat([dict(base, fo_score=-5, user_controlled=True)], 2036)[5]
    assert jr["score"] > 1 and not jr["fire"]                              # scored, never fired


def test_draft_score_follows_the_gm():
    picks = [{"draft_season": d, "h": 1.0} for d in (2035, 2036, 2037)]
    assert len(fq.in_tenure(picks, 2036)) == 2 and len(fq.in_tenure(picks, None)) == 3
    assert fq.team_q(fq.in_tenure(picks, 2038), 2037)["q"] == 1.0          # a new GM starts at the baseline


def test_development_score_follows_the_gm():
    windows = {0: ["2038-2039"], 1: ["2037-2038"], 2: ["2036-2037"]}
    assert set(dq.in_tenure(windows, 2039, 2037)) == {0, 1}
    assert dq.team_score(dq.in_tenure({0: [{"r": 1.0, "starter": True}]}, 2039, 2039)) == \
        {"D": None, "D_star": None, "n": 0.0}                              # hired at the 2039 Season scan


def test_writer_tools_are_registered():
    from newsroom import swarm_dispatcher as sd
    from newsroom.db_tools import development_record_facts
    assert {"get_front_office_standing", "get_front_office_rankings"} <= set(dir(sd))
    rec = {"window": "2036-2037", "risers": [{"name": "A", "position": "WR", "age": 23, "ovr_start": 70,
                                               "ovr_end": 75, "label": "DEVELOPED", "rookie": True}], "fallers": []}
    facts = development_record_facts(rec)
    assert facts["risers"][0] == {"player": "A", "pos": "WR", "age": 23, "ovr": "70 -> 75", "verdict": "DEVELOPED",
                                  "rookie_year": True}


def test_coach_gm_role():
    r = fs.combine([dict(team(1, draft=0.1), coach_gm=True), team(2, draft=-0.1)])
    roles = {x["abbr"]: fs.standing_facts(x, 2036)["role"] for x in r}
    assert roles == {"T1": "head coach and general manager (full control)", "T2": "general manager"}
