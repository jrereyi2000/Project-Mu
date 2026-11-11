"""engines/nfl_mirror injuries and depth (NFL_WEEKLY_MIRROR_PLAN.md §2.6, §4). Pure: no network, no DB."""
import pytest

from engines import depth_chart_engine
from engines.nfl_mirror import depth, injuries
from engines.nfl_mirror.real_state import RealInjury


# ---------- fixtures ----------

def later(status="ACTIVE", report=None):
    return {"status": status, "report_status": report}


def real_injury(report="Out", body_part="Knee"):
    return RealInjury(gsis_id="00-test", team="KC", report_status=report, body_part=body_part)


def save_player(pgid, status=None, ir=False, out=None):
    injury = {"injured_reserve": ir}
    if status is not None:
        injury["status"] = status
    if out is not None:
        injury["out"] = out
    return {"pgid": pgid, "injury": injury}


# ---------- weeks_until_back (CATCH_UP) ----------

def test_weeks_until_back_next_week():
    assert injuries.weeks_until_back("g1", 5, {6: {"g1": later()}}) == 1


def test_weeks_until_back_after_ir():
    weeks = {4: {"g1": later("IR")}, 5: {"g1": later("IR")}, 6: {"g1": later()}}
    assert injuries.weeks_until_back("g1", 3, weeks) == 3


def test_weeks_until_back_never_returns_is_capped():
    weeks = {w: {"g1": later("IR")} for w in range(11, 19)}
    assert injuries.weeks_until_back("g1", 10, weeks) == 8      # 18 - 10
    assert injuries.weeks_until_back("g1", 10, {}) == 8


def test_weeks_until_back_capped_even_when_back_late():
    assert injuries.weeks_until_back("g1", 16, {20: {"g1": later()}}) == 2
    assert injuries.weeks_until_back("g1", 17, {18: {"g1": later()}}) == 1
    assert injuries.weeks_until_back("g1", 18, {19: {"g1": later()}}) == 0


def test_weeks_until_back_out_and_doubtful_do_not_count():
    weeks = {6: {"g1": later("ACTIVE", "Out")}, 7: {"g1": later("ACTIVE", "Doubtful")},
             8: {"g1": later("ACTIVE", "Questionable")}}
    assert injuries.weeks_until_back("g1", 5, weeks) == 3


def test_weeks_until_back_skips_missing_weeks_and_non_active_status():
    assert injuries.weeks_until_back("g1", 5, {6: {}, 7: {"g1": later()}}) == 2
    weeks = {6: {"g1": later("IR")}, 7: {"g1": later("PS")}, 8: {"g1": later()}}
    assert injuries.weeks_until_back("g1", 5, weeks) == 3


def test_weeks_until_back_accepts_string_week_keys():
    assert injuries.weeks_until_back("g1", 5, {"6": {"g1": later()}}) == 1


# ---------- estimate_weeks (LIVE) ----------

@pytest.mark.parametrize("report,status,history,want", [
    ("Out", "ACTIVE", [], 1),
    ("Out", "ACTIVE", ["Questionable"], 1),
    ("Out", "ACTIVE", ["Out"], 2),
    ("out", "ACTIVE", ["out"], 2),
    ("Doubtful", "ACTIVE", [], 1),
    ("Questionable", "ACTIVE", [], 0),
    ("", "ACTIVE", [], 0),
    (None, "ACTIVE", [], 0),
    ("Out", "IR", [], 4),
    ("Out", "R48", [], 4),
    ("Out", "PUP", [], 4),
    ("Out", "NFI", [], 4),
    ("Out", "ir", [], 4),
])
def test_estimate_weeks(report, status, history, want):
    assert injuries.estimate_weeks(real_injury(report=report), status, history) == want


# ---------- heal fields ----------

def test_heal_fields_are_the_writable_healthy_player_fields():
    # The writer rejects None and `_`-suffixed enum members, so a heal leaves the stale type and severity alone.
    assert injuries.HEAL_FIELDS == {
        "InjuryStatus": "Uninjured",
        "MinInjuryDuration": 0,
        "MaxInjuryDuration": 0,
    }
    assert "InjuryType" not in injuries.HEAL_FIELDS
    assert "InjurySeverity" not in injuries.HEAL_FIELDS


# ---------- injury_fields ----------

def test_injury_fields_for_a_knee():
    assert injuries.injury_fields(3, "Knee") == {
        "InjuryStatus": "Injured",
        "InjuryType": "KneeACLSprain",
        "InjurySeverity": "SeveralGames",
        "MinInjuryDuration": 3,
        "MaxInjuryDuration": 3,
    }


@pytest.mark.parametrize("body_part,kind", [
    ("Knee", "KneeACLSprain"),
    ("Achilles", "AnkleAchillesTear"),
    ("High ankle", "AnkleHighSprain"),
    ("Hamstring", "LegHamstringTear"),
    ("Turf toe", "FootTurfToe"),
    ("Rotator cuff", "ShoulderRotatorCuffTear"),
    ("Thumb", "HandThumbBroken"),
    ("Concussion", None),
    ("", None),
    (None, None),
])
def test_injury_type_body_part_map(body_part, kind):
    assert injuries.injury_type(body_part) == kind


def test_injury_fields_omits_the_type_for_an_unmapped_body_part():
    fields = injuries.injury_fields(2, "Concussion")
    assert "InjuryType" not in fields
    assert fields == {"InjuryStatus": "Injured", "InjurySeverity": "SeveralGames",
                      "MinInjuryDuration": 2, "MaxInjuryDuration": 2}


@pytest.mark.parametrize("weeks,severity", [
    (0, "CoupleQtrs"), (1, "CoupleGames"), (2, "SeveralGames"), (3, "SeveralGames"),
    (4, "SeasonEnding"), (8, "SeasonEnding"),
])
def test_severity_bands(weeks, severity):
    assert injuries.severity_for(weeks) == severity
    assert injuries.injury_fields(weeks, "Knee")["InjurySeverity"] == severity


def test_injury_fields_uses_the_save_enums_when_given():
    enums = {"status": ["Uninjured", "Injured"], "type": ["Invalid_", "BackStrain"],
             "severity": ["Invalid_", "SeveralGames"]}
    fields = injuries.injury_fields(2, "Knee", enums)
    assert fields["InjuryStatus"] == "Injured"
    assert "InjuryType" not in fields                    # KneeACLSprain is not in this save's list: omit it
    assert fields["InjurySeverity"] == "SeveralGames"


def test_injury_fields_omits_an_enum_the_save_does_not_list():
    enums = {"InjuryType": ["Invalid_", "BackStrain"], "InjurySeverity": ["Invalid_", "CoupleGames"],
             "InjuryStatus": ["Uninjured", "Injured"]}
    fields = injuries.injury_fields(8, "Knee", enums)
    assert "InjuryType" not in fields
    assert "InjurySeverity" not in fields                # SeasonEnding is outside this save's list
    assert fields["InjuryStatus"] == "Injured"


def test_injury_fields_accepts_field_name_enum_keys():
    enums = {"InjuryType": ["Invalid_", "KneeACLSprain"], "InjurySeverity": ["Invalid_", "CoupleQtrs"],
             "InjuryStatus": ["Uninjured", "Injured"]}
    fields = injuries.injury_fields(0, "Knee", enums)
    assert (fields["InjuryType"], fields["InjurySeverity"]) == ("KneeACLSprain", "CoupleQtrs")


def test_injury_fields_clamps_negative_weeks():
    fields = injuries.injury_fields(-2, "Knee")
    assert (fields["MinInjuryDuration"], fields["MaxInjuryDuration"]) == (0, 0)


# ---------- depth slots ----------

def test_depth_slots_are_maddens_35():
    assert len(depth.DEPTH_SLOTS) == 35
    assert set(depth.DEPTH_SLOTS) == set(depth_chart_engine.POSITION_SLOTS)


# ---------- real_chart ----------

def test_real_chart_maps_nflverse_positions_to_madden_slots():
    real_depth = {"QB": ["q1"], "RB": ["r1"], "FB": ["f1"], "WR": ["w1", "w2"], "TE": ["t1"],
                  "LT": ["lt1"], "LG": ["lg1"], "C": ["c1"], "RG": ["rg1"], "RT": ["rt1"],
                  "LDE": ["le1"], "RDE": ["re1"], "NT": ["dt1"],
                  "LOLB": ["lolb1"], "LILB": ["mlb1"], "ROLB": ["rolb1"],
                  "LCB": ["cb1"], "RCB": ["cb2"], "FS": ["fs1"], "SS": ["ss1"], "K": ["k1"], "P": ["p1"]}
    links = {g: 100 + i for i, g in enumerate(
        ["q1", "r1", "f1", "w1", "w2", "t1", "lt1", "lg1", "c1", "rg1", "rt1", "le1", "re1", "dt1",
         "lolb1", "mlb1", "rolb1", "cb1", "cb2", "fs1", "ss1", "k1", "p1"])}
    roster = {pgid: save_player(pgid) for pgid in links.values()}
    chart = depth.real_chart(real_depth, links, roster)
    assert chart == {
        "QB": [100], "HB": [101], "FB": [102], "WR": [103, 104], "TE": [105],
        "LT": [106], "LG": [107], "C": [108], "RG": [109], "RT": [110],
        "LE": [111], "RE": [112], "DT": [113],
        "LOLB": [114], "MLB": [115], "ROLB": [116],
        "CB": [117, 118], "FS": [119], "SS": [120], "K": [121], "P": [122],
    }


def test_real_chart_interleaves_the_two_cornerback_lists_by_rank():
    links = {"lc1": 1, "lc2": 2, "rc1": 3}
    roster = {1: save_player(1), 2: save_player(2), 3: save_player(3)}
    chart = depth.real_chart({"LCB": ["lc1", "lc2"], "RCB": ["rc1"]}, links, roster)
    assert chart["CB"] == [1, 3, 2]


def test_real_chart_drops_injured_ir_and_unlinked_players():
    links = {"healthy": 1, "hurt": 2, "on_ir": 3, "unlinked": None}
    roster = {1: save_player(1), 2: save_player(2, status="Injured"), 3: save_player(3, ir=True)}
    chart = depth.real_chart({"QB": ["healthy", "hurt", "on_ir", "unlinked"]}, links, roster)
    assert chart == {"QB": [1]}


def test_real_chart_keeps_a_linked_player_with_no_roster_row():
    assert depth.real_chart({"QB": ["g1"]}, {"g1": 7}, {}) == {"QB": [7]}


def test_real_chart_accepts_a_player_list():
    roster = [save_player(1), save_player(2, status="Injured")]
    chart = depth.real_chart({"WR": ["g1", "g2"]}, {"g1": 1, "g2": 2}, roster)
    assert chart == {"WR": [1]}


def test_real_chart_reads_top_level_save_field_names():
    # A top-level row's `status` is the roster status; InjuryStatus is the injury state and must win.
    roster = [{"pgid": 1, "status": "Signed", "InjuryStatus": "Uninjured", "IsInjuredReserve": False},
              {"pgid": 2, "status": "Signed", "InjuryStatus": "Injured", "IsInjuredReserve": False}]
    chart = depth.real_chart({"QB": ["g1", "g2"]}, {"g1": 1, "g2": 2}, roster)
    assert chart == {"QB": [1]}


def test_real_chart_caps_a_slot_at_maddens_depth():
    gsis = [f"g{i}" for i in range(8)]
    chart = depth.real_chart({"WR": gsis}, {g: i for i, g in enumerate(gsis)}, {i: save_player(i) for i in range(8)})
    assert chart["WR"] == list(range(depth.DEPTH))


# ---------- merge ----------

def test_merge_fills_an_empty_slot_and_a_tail():
    real = {"QB": [1], "WR": [10, 11]}
    engine = {"QB": [1, 2, 3], "WR": [11, 12, 13], "KR": [20]}
    merged = depth.merge(real, engine)
    assert merged["QB"] == [1, 2, 3]
    assert merged["WR"] == [10, 11, 12, 13]
    assert merged["KR"] == [20]
    assert set(merged) >= set(depth.DEPTH_SLOTS)


def test_merge_keeps_the_real_chart_when_the_engine_disagrees():
    merged = depth.merge({"HB": [5, 6]}, {"HB": [6, 7]})
    assert merged["HB"] == [5, 6, 7]


def test_merge_caps_at_six():
    real = {"WR": [1, 2, 3, 4, 5, 6, 7]}
    engine = {"WR": [8, 9]}
    assert depth.merge(real, engine)["WR"] == [1, 2, 3, 4, 5, 6]
    assert depth.merge({}, {"WR": list(range(10))})["WR"] == list(range(depth.DEPTH))


def test_merge_keeps_an_unknown_engine_slot():
    merged = depth.merge({}, {"XX": [1]})
    assert merged["XX"] == [1]
