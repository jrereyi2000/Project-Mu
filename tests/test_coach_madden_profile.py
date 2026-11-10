"""engines/coach_madden_profile.py: the pure, seeded builder for an invented coach's Madden record.

Offline. Covers COACH_SAVE_WRITES_PLAN §4: the same seed gives the same profile, archetype from slant and role,
an unmapped playbook raises, talents scale with level, sliders drive demeanor, age is clamped to 32-72, and
every enum value produced is one Spike A measured in the save.
"""
import pytest

from engines import coach_madden_profile as cmp


PERSON = {"id": 311, "name": "Damon Whitaker", "traits": []}
SEED = "madden-coach:coach-311"
SCHEME = {"offensive": "Wide Zone West Coast", "defensive": "3-4"}
PLAYBOOK = {"offensive": "Carolina Offense", "defensive": "Carolina Defense"}
TENDENCIES = {"pass_lean": "High", "fourth_down_aggressiveness": "Low", "blitz_rate": "Medium",
              "man_coverage": "Low"}


def build(**overrides):
    kwargs = dict(slant="OFFENSIVE", scheme=SCHEME, playbook=PLAYBOOK, tendencies=TENDENCIES,
                  sliders={}, background={"age": 45, "years": 10}, contract=None, seed=SEED)
    kwargs.update(overrides)
    role = kwargs.pop("role", "HC")
    person = kwargs.pop("person", PERSON)
    return cmp.build_profile(person, role, **kwargs)


# -------------------------------------------------------------------------------------- determinism ---

def test_same_seed_gives_the_same_profile():
    assert build() == build()
    assert build()["seed"] == SEED


def test_a_different_seed_draws_a_different_profile():
    profiles = [build(seed=f"madden-coach:coach-{n}") for n in range(12)]
    looks = {(p["body_type"], p["height"], p["generic_head"]) for p in profiles}
    assert len(looks) > 1
    assert profiles[0]["seed"] == "madden-coach:coach-0"


def test_the_seed_is_the_only_entropy():
    """Same inputs and seed, whatever order the calls happen in, give the same draws."""
    first = build()
    build(seed="madden-coach:other", background={"age": 60, "years": 40})
    assert build() == first


# ---------------------------------------------------------------------------------------- archetype ---

@pytest.mark.parametrize("slant, expected", [
    ("OFFENSIVE", "OffensiveGuru"),
    ("DEFENSIVE", "DefensiveGenius"),
    ("CEO", "MasterMotivator"),
])
def test_head_coach_archetype_from_slant(slant, expected):
    assert build(slant=slant)["archetype"] == expected


@pytest.mark.parametrize("role, expected", [("OC", "OffensiveGuru"), ("DC", "DefensiveGenius")])
def test_coordinator_archetype_from_role(role, expected):
    assert build(role=role)["archetype"] == expected


def test_teacher_trait_makes_a_development_wizard():
    person = dict(PERSON, traits=["TEACHER"])
    assert build(person=person)["archetype"] == "DevelopmentWizard"
    assert build(person=person, slant="DEFENSIVE")["archetype"] == "DevelopmentWizard"
    # The trait's switch can turn the override off.
    assert build(person=dict(PERSON, traits=["TEACHER"], switches={"TEACHER": False}))["archetype"] \
        == "OffensiveGuru"
    # A trait string in another case is still the personality code's uppercase spelling.
    assert build(person=dict(PERSON, traits=["teacher"]))["archetype"] == "DevelopmentWizard"


# ------------------------------------------------------------------------------------ mappings raise ---

def test_every_team_nickname_playbook_maps():
    """`<Nickname> Offense/Defense` is the form the coaching carousel's staff rows use, so every team's nickname must
    map too — a NULL playbook would leave a created coach's record incomplete."""
    for nickname in cmp.CITY_TO_TEAM.values():
        for side in ("Offense", "Defense"):
            assert cmp.PLAYBOOK_MAP[f"{nickname} {side}"] == nickname


def test_an_unmapped_playbook_raises_and_names_the_value():
    with pytest.raises(ValueError, match="London Offense"):
        build(playbook={"offensive": "London Offense"})
    with pytest.raises(ValueError, match="Atlantis Defense"):
        build(playbook={"defensive": "Atlantis Defense"})


def test_an_unmapped_scheme_raises_and_names_the_value():
    with pytest.raises(ValueError, match="Atlantis West Coast"):
        build(scheme={"offensive": "Atlantis West Coast"})


def test_erhardt_perkins_maps_to_multiple_power_run():
    """JR's call, 2026-11-10: no Madden member for it, and the save itself gives 2 of its 4 real EP coaches
    MultiplePowerRun (Vrabel, McDaniels)."""
    assert cmp.SCHEME_MAP["Erhardt-Perkins"] == "MultiplePowerRun"
    row = build(scheme={"offensive": "Erhardt-Perkins"})
    assert row["offensive_scheme"] == "MultiplePowerRun"


def test_the_maps_cover_what_the_engines_and_tables_use():
    for playbook in ("Arizona Offense", "Carolina Defense", "LA Rams Offense", "New England Defense",
                     "Colts Offense", "Dolphins Offense", "Jets Offense", "Packers Offense",
                     "Titans Offense", "Titans Defense"):
        assert playbook in cmp.PLAYBOOK_MAP
    for scheme in ("Wide Zone West Coast", "West Coast", "Spread", "3-4", "4-3", "Erhardt-Perkins"):
        assert scheme in cmp.SCHEME_MAP


def test_a_single_playbook_name_is_the_persons_own_side_only():
    row = build(role="OC", scheme="Spread", playbook="Buffalo Offense")
    assert row["offensive_playbook"] == "Bills"
    assert row["defensive_playbook"] is None
    dc = build(role="DC", scheme="4-3", playbook="Chicago Defense")
    assert dc["defensive_playbook"] == "Bears"
    assert dc["offensive_playbook"] is None


# ------------------------------------------------------------------------------------ level and talents ---

def test_talents_scale_with_level():
    low = build(background={"age": 40, "years": 1})
    high = build(background={"age": 64, "years": 40})
    assert high["level"] > low["level"]
    assert len(high["talents"]) > len(low["talents"])
    assert {t["slot"] for t in high["talents"]} == {"Gameday", "Playsheet"}


def test_talent_layout_and_tiers_are_in_range():
    row = build()
    for talent in row["talents"]:
        assert set(talent) == {"slot", "talent", "tier"}
        assert talent["slot"] in ("Gameday", "Playsheet")
        assert isinstance(talent["talent"], str) and talent["talent"]
        assert 0 <= talent["tier"] <= 3


def test_level_by_years_is_monotonic_and_bounded():
    levels = [cmp._level(y) for y in range(0, 61)]
    assert levels == sorted(levels)
    assert 1 <= levels[0] and levels[-1] <= 41


# ------------------------------------------------------------------------------------------ sliders ---

@pytest.mark.parametrize("temperament, expected", [
    (10, "Intense"),
    (7, "Intense"),
    (5, "Classic"),
    (3, "Calm"),
    (0, "Calm"),
])
def test_sliders_drive_demeanor(temperament, expected):
    assert build(sliders={"temperament": temperament})["demeanor"] == expected


def test_a_missing_slider_is_neutral():
    row = build(sliders={})
    assert row["demeanor"] == "Classic"
    assert row["personality"] == "TeamPlayer"


def test_high_confidence_and_drive_make_a_leader():
    row = build(sliders={"confidence": 9, "drive": 8, "temperament": 5})
    assert row["personality"] == "Leader"


# ---------------------------------------------------------------------------------------------- age ---

@pytest.mark.parametrize("age, expected", [(25, 32), (32, 32), (72, 72), (90, 72)])
def test_age_is_clamped(age, expected):
    assert build(background={"age": age, "years": 5})["age"] == expected


def test_years_coaching_never_exceeds_the_age():
    row = build(background={"age": 32, "years": 30})
    assert row["years_coaching"] <= 10


def test_a_text_background_still_builds_a_full_row():
    row = build(background="a college offensive coordinator")
    assert 32 <= row["age"] <= 72
    assert 0 <= row["years_coaching"] <= min(45, row["age"] - 22)


# --------------------------------------------------------------------------------------- enum values ---

@pytest.mark.parametrize("role", ["HC", "OC", "DC"])
@pytest.mark.parametrize("slant", ["OFFENSIVE", "DEFENSIVE", "CEO"])
def test_every_produced_enum_value_is_one_spike_a_measured(role, slant):
    row = build(role=role, slant=slant)
    assert row["position"] in cmp.POSITIONS
    assert row["archetype"] in cmp.ARCHETYPES
    assert row["specialty"] in cmp.SPECIALTIES
    assert row["body_type"] in cmp.BODY_TYPES
    assert row["personality"] in cmp.PERSONALITIES
    assert row["demeanor"] in cmp.DEMEANORS
    assert row["face_shape"] in cmp.FACE_SHAPE_FALLBACKS
    assert row["generic_head"] in cmp.GENERIC_HEAD_ASSETS
    assert row["portrait_status"] in ("NONE", "GENERATED", "PACKED", "INSTALLED")


def test_a_complete_row_for_each_role_and_slant():
    required = ("first_name", "last_name", "age", "position", "archetype", "specialty", "level",
                "years_coaching", "personality", "demeanor", "body_type", "height", "weight",
                "face_shape", "generic_head", "talents", "ratings", "tendencies", "visuals_template",
                "portrait_id", "portrait_status", "seed")
    for role in ("HC", "OC", "DC"):
        for slant in ("OFFENSIVE", "DEFENSIVE", "CEO"):
            row = build(role=role, slant=slant)
            assert all(row[key] is not None for key in required), (role, slant)
            assert row["ratings"]["COACH_RATING"] > 0
            assert set(cmp.TENDENCY_FIELDS) <= set(row["tendencies"])


# ---------------------------------------------------------------------------------------- look/visuals ---

def test_portrait_starts_as_a_silhouette():
    row = build()
    assert row["portrait_id"] == 0
    assert row["portrait_status"] == "NONE"
    assert row["presentation_id"] is None


def test_visuals_template_is_the_same_position_and_body_type():
    for role, position in (("HC", "HeadCoach"), ("OC", "OffensiveCoordinator"),
                           ("DC", "DefensiveCoordinator")):
        row = build(role=role)
        template = cmp.VISUALS_TEMPLATES[(position, row["body_type"])]
        assert row["visuals_template"] == template
        assert template in {v for (pos, _), v in cmp.VISUALS_TEMPLATES.items() if pos == position}


def test_height_and_weight_are_plausible_values():
    row = build()
    assert cmp.COACH_HEIGHT_RANGE[0] <= row["height"] <= cmp.COACH_HEIGHT_RANGE[1]
    # Madden's Coach.Weight field is int 150-512, and the save writer refuses anything outside a field's schema
    # range. The 127 live records hold the placeholder 10, so a created coach gets a real weight instead.
    low, high = cmp.COACH_WEIGHT_BY_BODY.get(row["body_type"], cmp.COACH_WEIGHT_DEFAULT)
    assert low <= row["weight"] <= high
    assert 150 <= row["weight"] <= 512
    assert build()["weight"] == row["weight"]      # same seed, same draw


# ---------------------------------------------------------------------------------------- identity/name ---

def test_identity_is_included_when_the_person_has_an_id():
    coach = build(person={"id": 311, "name": "Damon Whitaker"})
    assert (coach["person_kind"], coach["person_id"]) == ("COACH", 311)
    coord = build(person={"id": 57, "name": "Luke Mercer"}, role="DC")
    assert (coord["person_kind"], coord["person_id"]) == ("COORD", 57)


def test_name_splits_from_mu_full_name():
    row = build(person={"id": 1, "name": "Jim Bob Cooter"})
    assert (row["first_name"], row["last_name"]) == ("Jim", "Bob Cooter")
    explicit = build(person={"id": 1, "first_name": "Pete", "last_name": "Carmichael Jr"})
    assert (explicit["first_name"], explicit["last_name"]) == ("Pete", "Carmichael Jr")


def test_a_person_without_a_name_raises():
    with pytest.raises(ValueError):
        build(person={"id": 1})


def test_madden_role_names_are_accepted():
    assert build(role="HeadCoach")["position"] == "HeadCoach"
    assert build(role="OffensiveCoordinator")["position"] == "OffensiveCoordinator"
    assert build(role="DefensiveCoordinator")["position"] == "DefensiveCoordinator"


def test_tendencies_map_to_madden_ints():
    row = build()
    assert row["tendencies"]["COACH_OFFTENDENCYRUNPASS"] == cmp.TENDENCY_LEVELS["high"]
    assert row["tendencies"]["COACH_OFFTENDENCYAGGRESSCONSERV"] == cmp.TENDENCY_LEVELS["low"]
    assert row["tendencies"]["COACH_DEFTENDENCYAGGRESSCONSERV"] == cmp.TENDENCY_LEVELS["medium"]
    neutral = build(tendencies=None)
    assert all(value == 50 for value in neutral["tendencies"].values())
