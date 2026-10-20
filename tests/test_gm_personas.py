"""GM personas (docs/team_management/03_GM_AGENTS_PLAN.md Phase 1). Pure: no database, no Gemini."""
import json

from engines import gm_llm
from engines.gm_personas import (AGGRESSIVE, BAND_MAX, BAND_MIN, PASSIVE, POSITION_BIAS_MAX_GROUPS,
                                 POSITION_BIAS_SPREAD, band_counts, league_traits, make_persona, parse_persona,
                                 pick_seed_rows, replacement_traits, template_persona)

ABBRS = ["ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
         "LAC", "LAR", "LV", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS"]
CTX = {"city": "Carolina", "name": "Panthers", "coach": "Dale Whitfield"}


def test_same_seed_same_traits():
    assert league_traits(ABBRS) == league_traits(list(reversed(ABBRS)))


def test_stratification_bounds():
    hi, lo = band_counts(league_traits(ABBRS))
    assert BAND_MIN <= hi <= BAND_MAX and BAND_MIN <= lo <= BAND_MAX
    for t in league_traits(ABBRS).values():
        assert 0 <= t["aggressiveness"] <= 1 and 0 <= t["risk_appetite"] <= 1
        assert 0.6 <= t["pick_affinity"] <= 1.4 and -1 <= t["youth_bias"] <= 1


def test_position_bias_limits():
    for t in league_traits(ABBRS).values():
        pb = t["position_bias"]
        assert len(pb) <= POSITION_BIAS_MAX_GROUPS
        assert all(abs(m - 1) <= POSITION_BIAS_SPREAD + 1e-9 for m in pb.values())


def test_replacement_keeps_bands():
    traits = league_traits(ABBRS)
    others = {a: t for a, t in traits.items() if a != "NO"}
    new = replacement_traits("NO", 2037, others)
    hi, lo = band_counts(dict(others, NO=new))
    assert BAND_MIN <= hi <= BAND_MAX and BAND_MIN <= lo <= BAND_MAX
    assert new == replacement_traits("NO", 2037, others)


def test_template_fallback_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr("pipeline.notify.env", lambda k, d=None: d)
    assert gm_llm.make_llm("gm_persona") is None
    t = league_traits(ABBRS)["CAR"]
    p = make_persona("CAR", CTX, t, 2036, llm=None)
    assert p["bio_source"] == "TEMPLATE" and p == template_persona("CAR", CTX, t, 2036)
    assert 2030 <= p["start_season"] <= 2036 and "Panthers" in p["bio"]


def test_llm_persona_used_when_valid():
    t = league_traits(ABBRS)["CAR"]
    start = template_persona("CAR", CTX, t, 2036)["start_season"]
    answer = json.dumps({"gm_name": "Rowan Achterberg", "bio": f"Took the job in {start} and never looked back.",
                         "voice": "dry, precise"})
    p = make_persona("CAR", CTX, t, 2036, llm=lambda prompt: answer)
    assert p["bio_source"] == "LLM" and p["gm_name"] == "Rowan Achterberg"


def test_llm_persona_rejected_falls_back():
    t = league_traits(ABBRS)["CAR"]
    bad = json.dumps({"gm_name": "Rowan Achterberg", "bio": "He drafted three Pro Bowlers in 2031.", "voice": "x"})
    assert make_persona("CAR", CTX, t, 2036, llm=lambda p: bad)["bio_source"] == "TEMPLATE"

    def boom(prompt):
        raise RuntimeError("quota")
    assert make_persona("CAR", CTX, t, 2036, llm=boom)["bio_source"] == "TEMPLATE"


def test_parse_persona_rules():
    ok = '{"gm_name": "Ada Brightwater", "bio": "Since 2033 she has run a quiet shop.", "voice": "calm"}'
    assert parse_persona(ok, 2033)["gm_name"] == "Ada Brightwater"
    assert parse_persona(ok, 2034) is None                                  # a number that isn't the start year
    assert parse_persona(ok, 2033, taken_names=["ada brightwater"]) is None
    assert parse_persona(ok.replace("Ada Brightwater", "Dale Whitfield"), 2033, coach="Dale Whitfield") is None
    assert parse_persona("not json", 2033) is None


def test_template_names_unique_by_last_name():
    traits = league_traits(ABBRS)
    taken = []
    for a in ABBRS:
        p = make_persona(a, dict(CTX, name=a), traits[a], 2036, taken_names=taken)
        taken.append(p["gm_name"])
    assert len({n.split()[-1] for n in taken}) == len(taken)


def test_pick_seed_rows():
    rows = pick_seed_rows(list(range(5, 37)), 2036)
    assert len(rows) == 32 * 7 * 3                                         # C5: the next three drafts
    assert {r[0] for r in rows} == {2037, 2038, 2039}
    assert all(r[2] == r[3] for r in rows)
    assert len(set(r[:3] for r in rows)) == len(rows)
