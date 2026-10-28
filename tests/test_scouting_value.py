"""Scouting true value, keys, grade scale and class build (docs/done/team_management/04_SCOUTING_AND_DRAFT_PLAN.md Phase 1).
Pure: no DB."""
from engines import scouting_value as sv
from engines.scouting_engine import build_class, relink_actions, step_for, step_when


def row(i, name, pos="WR", ovr=70, age=22, pick=None, college="GEORGIA"):
    return {"id": i, "full_name": name, "position": pos, "college": college, "age": age, "ovr": ovr,
            "projected_pick": pick or i, "height_in": 72, "weight": 200}


def test_value_orders_position_and_age():
    v = lambda pos, age=22: sv.true_value(70, pos, age)[0]
    assert v("QB") > v("LEDG") == v("LT") > v("RT") and v("CB") > v("WR") > v("DT") > v("TE")
    assert v("TE") > v("LG") > v("MIKE") == v("SS") > v("HB") > v("K")
    assert sv.true_value(70, "WR", 21)[0] > sv.true_value(70, "WR", 22)[0] > sv.true_value(70, "WR", 23)[0] \
        > sv.true_value(70, "WR", 24)[0]
    assert sv.true_value(70, "QB", 22)[0] == 75.5


def test_blank_ovr_is_unrated_50():
    v, inputs = sv.true_value(None, "MIKE", 22)
    assert v == 50 - 2 + 0.5 and inputs["unrated"]


def test_prospect_key_stable_across_rescan():
    a = build_class([row(10, "Jamar Bodden", "CB")], 2037, 4)[0]
    b = build_class([row(977, "JAMAR  BODDEN", "CB")], 2037, 4)[0]     # new id after a re-scan, OCR spacing
    assert a["key"] == b["key"] == "2037:JAMARBODDEN:CB" and a["prospect_id"] != b["prospect_id"]
    assert sv.prospect_key(2037, "X Y", "LT") == sv.prospect_key(2037, "X Y", "RT")       # same group


def test_duplicate_keys_get_suffix():
    out = build_class([row(1, "Same Name"), row(2, "Same Name")], 2037, 4)
    assert [p["key"] for p in out] == ["2037:SAMENAME:WR", "2037:SAMENAME:WR:2"]


def test_class_year_from_age():
    for age, allowed in sv.CLASS_YEARS.items():
        for s in range(30):
            assert sv.class_year(age, f"s{s}") in allowed
    assert sv.class_year(21, "x") == sv.class_year(21, "x")
    assert all("freshman" not in y for ys in sv.CLASS_YEARS.values() for y in ys)


def test_grade_map_hits_anchors():
    values = [90 - i * 0.12 for i in range(300)]
    knots = sv.grade_map(values)
    for rank, g in sv.GRADE_ANCHORS:
        assert abs(sv.grade(values[rank - 1], knots) - g) < 0.05
    assert sv.grade(200, knots) == 100 and sv.grade(-50, knots) == 0
    gs = [sv.grade(v, knots) for v in values]
    assert gs == sorted(gs, reverse=True)


def test_round_labels():
    assert sv.round_label(1) == "1st round" and sv.round_label(33) == "2nd round"
    assert sv.round_label(224) == "7th round" and sv.round_label(225) == "UDFA"
    assert sv.round_tags(5) == ["Top 10"] and sv.round_tags(120) == ["Day 3"] and sv.round_tags(40) == []


def test_true_rank_and_schools():
    out = build_class([row(1, "A", "K", 80), row(2, "B", "QB", 70), row(3, "C", "WR", 72, college="1OWA")],
                      2037, 4)
    ranks = {p["full_name"]: p["true_rank"] for p in out}
    assert ranks == {"B": 1, "C": 2, "A": 3}
    c = next(p for p in out if p["full_name"] == "C")
    assert c["school"]["key"] == "IOWA" and c["conference"] == "Big Ten"


def test_relink_marks_missing_withdrawn():
    board = build_class([row(5, "Stays"), row(6, "Brand New")], 2037, 9)
    existing = {"2037:STAYS:WR": "ACTIVE", "2037:GONE:WR": "ACTIVE", "2037:PICKED:WR": "DRAFTED"}
    acts = relink_actions(existing, board)
    assert acts["link"] == [("2037:STAYS:WR", 5)]
    assert acts["withdraw"] == ["2037:GONE:WR"]                    # DRAFTED rows are never withdrawn
    assert [p["key"] for p in acts["new"]] == ["2037:BRANDNEW:WR"] and acts["new"][0]["first_step"] == 9


def test_steps():
    assert step_for(4) == 4 and step_for(23) == 23 and step_for(24, 1) == 24 and step_for(24, 4) == 27
    assert step_for(24, 5) is None
    assert step_when(27, 2036) == (2036, 24, 4) and step_when(10, 2036) == (2036, 10, None)


def test_every_board_college_is_mapped():
    import json
    d = json.load(open(sv.COLLEGES_PATH))
    assert len(d["schools"]) >= 150
    for alias, target in d["aliases"].items():
        assert target in d["schools"], alias
    for k in d["schools"]:
        assert sv.school(k)["conference"]
