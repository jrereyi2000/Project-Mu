"""Scouting directors (docs/in_progress/OWNERS_SCOUTS_VOTERS_PLAN.md §2.4, §4).

Offline: a fake cursor dispatches on each statement's '/* q:<name> */' tag; a fake lifecycle stands in for the
personality lifecycle creator hook. The bias is pure, so its cases need no cursor.
"""
import copy
import re

from engines import scout_directors as sd
from engines import scout_voice as sv


def director(traits):
    return {"scout_id": 1, "team_id": 1, "name": "Pat Scout", "persona_id": "scout-1", "start_season": 2026,
            "region": "AFC", "lens": sd.lens_for(traits), "traits": list(traits)}


# --------------------------------------------------------------------------------------------- bias ---
def test_bias_stays_inside_the_bound():
    combo = ["TRAITS_CHASER", "TAPE_GRINDER", "CHARACTER_HAWK", "FLOOR_SEEKER", "CEILING_GAMBLER"]
    for traits in (["TRAITS_CHASER"], ["TAPE_GRINDER"], ["CHARACTER_HAWK"], ["FLOOR_SEEKER"],
                   ["CEILING_GAMBLER"], combo):
        for prospect in ({"athleticism": 100, "production": 100, "flags": ["INTERVIEWS_MIXED"],
                          "volatility": 1.0}, {"athleticism": 0, "production": 0, "flags": [],
                                               "volatility": 0.0}, {}):
            assert -sd.MAX_BIAS <= sd.board_bias(director(traits), prospect) <= sd.MAX_BIAS


def test_trait_directions():
    # TRAITS_CHASER raises athletic outliers (and only them).
    assert sd.board_bias(director(["TRAITS_CHASER"]), {"athleticism": 95}) > 0
    assert sd.board_bias(director(["TRAITS_CHASER"]), {"athleticism": 50}) == 0.0
    # TAPE_GRINDER raises production (and only it).
    assert sd.board_bias(director(["TAPE_GRINDER"]), {"production": 90}) > 0
    assert sd.board_bias(director(["TAPE_GRINDER"]), {"production": 20}) == 0.0
    # CHARACTER_HAWK lowers prospects with negative interview flags.
    assert sd.board_bias(director(["CHARACTER_HAWK"]), {"flags": ["INTERVIEWS_MIXED"]}) == -sd.MAX_BIAS
    assert sd.board_bias(director(["CHARACTER_HAWK"]), {"flags": ["CONSISTENCY"]}) == -sd.MAX_BIAS
    assert sd.board_bias(director(["CHARACTER_HAWK"]), {"flags": []}) == 0.0
    # FLOOR_SEEKER and CEILING_GAMBLER shift high-variance prospects in opposite ways.
    assert sd.board_bias(director(["FLOOR_SEEKER"]), {"volatility": 1.0}) < 0
    assert sd.board_bias(director(["CEILING_GAMBLER"]), {"volatility": 1.0}) > 0
    assert sd.board_bias(director(["FLOOR_SEEKER"]), {"volatility": 0.0}) == 0.0
    assert sd.board_bias(director(["CEILING_GAMBLER"]), {"volatility": 0.0}) == 0.0
    assert sd.board_bias(director(["FLOOR_SEEKER", "CEILING_GAMBLER"]), {"volatility": 1.0}) == 0.0
    # a `volatile` bool counts as a high-variance flag
    assert sd.board_bias(director(["CEILING_GAMBLER"]), {"volatile": True}) > 0


def test_true_ratings_untouched():
    prospect = {"key": "2037:JOHN SMITH:QB", "ratings": {"Speed": 95, "Catching": 80},
                "athleticism": 99, "production": 99, "flags": ["INTERVIEWS_MIXED"], "volatility": 1.0}
    before = copy.deepcopy(prospect)
    d = director(["TRAITS_CHASER", "TAPE_GRINDER", "CHARACTER_HAWK", "CEILING_GAMBLER"])
    sd.board_bias(d, prospect)
    assert prospect == before                                   # nothing (ratings included) was changed


# --------------------------------------------------------------------------------------------- voice ---
def test_voice_fields():
    d = {"scout_id": 7, "team_id": 1, "name": "Pat Scout", "persona_id": "scout-1", "start_season": 2026,
         "region": "AFC", "lens": "TAPE", "traits": ["TAPE_GRINDER", "FILM_ROOM_RAT"]}
    assert sd.voice(d) == {"scout_id": 7, "name": "Pat Scout", "region": "AFC", "lens": "TAPE"}
    assert sd.voice(dict(d, lens=None))["lens"] == "TAPE"       # derived from traits when not precomputed
    assert sd.voice(dict(d, lens=None, traits=["FILM_ROOM_RAT"]))["lens"] is None
    assert sd.lens_for(["CEILING_GAMBLER", "TRAITS_CHASER"]) == "TRAITS"     # fixed priority order
    assert sd.lens_for(["FILM_ROOM_RAT"]) is None


def test_regional_bias_delegates_to_board_bias_only_for_a_director():
    assert sv.regional_bias(sv.department_voice(), {"flags": ["INTERVIEWS_MIXED"]}) == 0.0
    assert sv.regional_bias(sv.ScoutVoice(scout_id=1, region="AFC", lens="CHARACTER"),
                            {"flags": ["INTERVIEWS_MIXED"]}) == -sd.MAX_BIAS
    assert sv.regional_bias(sv.ScoutVoice(scout_id=1, region="AFC", lens="TAPE"), {}) == 0.0


def test_regions_filled_and_voice_prompt_lens_mapping():
    assert sv.REGIONS == {"AFC": "AFC", "NFC": "NFC"}
    dept = sv.department_voice()
    assert dept.scout_id is None and dept.name is None and dept.region is None and dept.lens is None
    assert sv.voice_prompt(dept) == "a neutral national scouting service"
    for lens, phrase in (("TRAITS", "physical traits"), ("TAPE", "grinds the tape"),
                         ("CHARACTER", "weighs character heavily"), ("FLOOR", "safe floor"),
                         ("CEILING", "gambles on ceiling")):
        assert phrase in sv.voice_prompt(sv.ScoutVoice(scout_id=1, name="N", region="AFC", lens=lens))
    v = sv.ScoutVoice(scout_id=1, name="Pat Scout", region="AFC", lens="TAPE")
    assert "Pat Scout" in sv.voice_prompt(v) and "focused on the AFC" in sv.voice_prompt(v)


# --------------------------------------------------------------------------------------- directors ---
class FakeCursor:
    def __init__(self, teams=None):
        self.teams = list(teams) if teams else [(i, "AFC" if i % 2 else "NFC") for i in range(1, 33)]
        self.directors = {}                       # team_id -> stored row
        self._out = []
        self.next_id = 1

    def execute(self, sql, params=()):
        tag = re.search(r"/\* q:(\w+) \*/", sql).group(1)
        getattr(self, "q_" + tag)(params)

    def fetchone(self):
        return self._out[0] if self._out else None

    def fetchall(self):
        return list(self._out)

    def q_directors_table(self, p):
        self._out = [(True,)]

    def q_teams(self, p):
        self._out = list(self.teams)

    def q_active_directors(self, p):
        self._out = [(d["id"], d["team_id"], d["name"], d["persona_id"], d["start_season"])
                     for d in sorted(self.directors.values(), key=lambda d: d["team_id"])]

    def q_insert_director(self, p):
        team_id, name, persona_id, season = p
        d = {"id": self.next_id, "team_id": team_id, "name": name, "persona_id": persona_id,
             "start_season": season, "end_season": None}
        self.next_id += 1
        self.directors[team_id] = d
        self._out = [(d["id"],)]


def make_lifecycle(traits_by_team=None):
    def lifecycle(cur, *, team_id, season, name):
        lifecycle.calls.append((team_id, season, name))
        return {"persona_id": f"scout-{team_id}", "traits": list((traits_by_team or {}).get(team_id, ["TAPE_GRINDER"]))}
    lifecycle.calls = []
    return lifecycle


def test_one_director_per_team_generated_through_lifecycle():
    cur = FakeCursor()
    life = make_lifecycle()
    directors = sd.ensure_directors(cur, 2026, lifecycle=life)
    assert sorted(d["team_id"] for d in directors) == list(range(1, 33))
    assert all(d["scout_id"] and d["name"] and d["start_season"] == 2026 for d in directors)
    assert all(d["persona_id"] == f"scout-{d['team_id']}" for d in directors)
    assert all(d["region"] == ("AFC" if d["team_id"] % 2 else "NFC") for d in directors)
    assert all(d["lens"] == "TAPE" for d in directors)
    assert sd.voice(directors[0])["lens"] == "TAPE"
    assert len(life.calls) == 32 and len(cur.directors) == 32


def test_directors_are_durable_across_seasons():
    cur = FakeCursor()
    life = make_lifecycle({1: ["CHARACTER_HAWK"], 2: ["CEILING_GAMBLER"]})
    first = sd.ensure_directors(cur, 2026, lifecycle=life)
    second = sd.ensure_directors(cur, 2027, lifecycle=life)
    assert [(d["scout_id"], d["name"], d["persona_id"], d["start_season"], d["lens"], d["traits"])
            for d in first] == \
           [(d["scout_id"], d["name"], d["persona_id"], d["start_season"], d["lens"], d["traits"])
            for d in second]
    assert len(cur.directors) == 32                            # no second row per team
    assert first[0]["lens"] == "CHARACTER" and first[1]["lens"] == "CEILING"    # teams 1 and 2 carry their lens


def test_ensure_directors_without_the_table_returns_nothing():
    class NoTable(FakeCursor):
        def q_directors_table(self, p):
            self._out = [(False,)]
    assert sd.ensure_directors(NoTable(), 2026, lifecycle=make_lifecycle()) == []
