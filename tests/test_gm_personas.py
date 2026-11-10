"""GM personas (docs/done/team_management/03_GM_AGENTS_PLAN.md Phase 1). Pure: no database, no Gemini."""
import json
import sqlite3

from engines import gm_llm, gm_voices
from engines.gm_personas import (AGGRESSIVE, BAND_MAX, BAND_MIN, PASSIVE, POSITION_BIAS_MAX_GROUPS,
                                 POSITION_BIAS_SPREAD, backfill_bios, band_counts, insert_gm, league_traits,
                                 make_persona, parse_persona, pick_seed_rows, replacement_traits,
                                 template_persona)
from tests.test_inbox_store import FakeCursor

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


# ---------------------------------------------------------- voice cards (U1) --
MODEL_CARD = {
    "register": "Speaks in a low voice and answers questions with questions.",
    "samples": ["What are you really asking me for here?", "I can live with that number."],
    "tics": ["says 'fair enough' before a hard no"],
    "avoids": ["leverage"],
    "wants": "a number he can sit with overnight",
}


def _answer(start, card):
    return json.dumps({"gm_name": "Rowan Achterberg",
                       "bio": f"Took the job in {start} and never looked back.",
                       "voice": "dry, precise", "voice_card": card})


def test_make_persona_prefers_the_authored_card():
    t = league_traits(ABBRS)["ATL"]
    start = template_persona("ATL", CTX, t, 2026)["start_season"]
    p = make_persona("ATL", CTX, t, 2026, llm=lambda prompt: _answer(start, MODEL_CARD))
    assert p["bio_source"] == "LLM"
    assert p["voice_card_source"] == "AUTHORED"
    assert p["voice_card"] == gm_voices.authored_card("ATL", 2026)


def test_make_persona_prefers_the_model_card_without_an_authored_one():
    t = league_traits(ABBRS)["ATL"]
    start = template_persona("ATL", CTX, t, 2036)["start_season"]
    p = make_persona("ATL", CTX, t, 2036, llm=lambda prompt: _answer(start, MODEL_CARD))
    assert p["voice_card_source"] == "LLM"
    assert p["voice_card"] == gm_voices.parse_card(MODEL_CARD)


def test_make_persona_falls_back_to_the_template_card_alone():
    t = league_traits(ABBRS)["ATL"]
    start = template_persona("ATL", CTX, t, 2036)["start_season"]
    p = make_persona("ATL", CTX, t, 2036,
                     llm=lambda prompt: _answer(start, dict(MODEL_CARD, register="short")))
    assert p["bio_source"] == "LLM"                                       # the good answer still lands
    assert p["voice_card_source"] == "TEMPLATE"
    assert p["voice_card"] == gm_voices.template_card(t)


def test_parse_persona_keeps_a_good_card_and_falls_back_alone():
    t = league_traits(ABBRS)["ATL"]
    good = json.dumps({"gm_name": "Ada Brightwater", "bio": "Since 2033 she has run a quiet shop.",
                       "voice": "calm", "voice_card": MODEL_CARD})
    got = parse_persona(good, 2033, traits=t)
    assert got["voice_card"] == gm_voices.parse_card(MODEL_CARD) and got["voice_card_source"] == "LLM"
    bad = json.dumps({"gm_name": "Ada Brightwater", "bio": "Since 2033 she has run a quiet shop.",
                      "voice": "calm", "voice_card": dict(MODEL_CARD, register="short")})
    got = parse_persona(bad, 2033, traits=t)
    assert got is not None and got["gm_name"] == "Ada Brightwater"
    assert got["voice_card_source"] == "TEMPLATE" and got["voice_card"] == gm_voices.template_card(t)


def test_insert_gm_writes_the_card_and_its_source():
    class Cursor:
        sql = params = None

        def execute(self, sql, params=()):
            self.sql, self.params = sql, params

        def fetchone(self):
            return None

    card = gm_voices.template_card({})
    p = {"gm_name": "Ada Brightwater", "bio": "A bio.", "voice": "calm", "bio_source": "TEMPLATE",
         "start_season": 2030, "voice_card": card, "voice_card_source": "TEMPLATE"}
    cur = Cursor()
    insert_gm(cur, 1, p, league_traits(ABBRS)["ATL"], "gm:ATL")
    assert "voice_card" in cur.sql and "voice_card_source" in cur.sql
    assert json.loads(cur.params[4]) == card and cur.params[5] == "TEMPLATE"


def test_backfill_fills_a_missing_card_without_a_model(monkeypatch):
    from engines import gm_personas
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute("CREATE TABLE teams (id INT, abbreviation TEXT)")
    conn.execute("INSERT INTO teams VALUES (1, 'ATL')")
    conn.execute("""CREATE TABLE gm_profiles (id INT, team_id INT, gm_name TEXT, start_season INT,
                                               end_season INT, aggressiveness REAL, risk_appetite REAL,
                                               pick_affinity REAL, youth_bias REAL, position_bias TEXT,
                                               bio_source TEXT, voice TEXT, voice_card TEXT,
                                               voice_card_source TEXT)""")
    conn.execute("""INSERT INTO gm_profiles VALUES
                    (1, 1, 'Old Hand', 2030, NULL, 0.5, 0.5, 1.0, 0.0, '{}', 'LLM', 'quiet', NULL, NULL)""")
    cur = FakeCursor(conn)
    monkeypatch.setattr(gm_personas, "read_teams",
                        lambda c, s: {"ATL": {"city": "Atlanta", "name": "Falcons", "coach": "X"}})
    assert backfill_bios(cur, 2036, None, log=lambda *a: None) == 1
    card, source = conn.execute("SELECT voice_card, voice_card_source FROM gm_profiles WHERE id = 1").fetchone()
    assert source == "TEMPLATE" and gm_voices.parse_card(json.loads(card)) is not None


def test_a_pre_2030_league_seeds_its_gm_tenures_in_the_past():
    """M-0105 B4: a 2026 league drew every seeded GM's start from 2030 on, so each was a future hire — priced as a
    first-time PROMOTION and kept out of the hot seat until 2031. A pre-2030 seed reaches back instead, and never
    starts after the seeding season."""
    from engines import gm_personas as _gp
    ctx = {"city": "Atlanta", "name": "Falcons"}
    t = league_traits(ABBRS)["ATL"]
    starts = [template_persona("ATL", ctx, t, 2026, seed_key=f"gm:ATL:{i}")["start_season"]
              for i in range(40)]
    assert max(starts) <= 2026                                     # never in the future
    assert min(starts) <= 2023                                     # veterans exist: experience >= 3 is STANDARD
    # A 2030+ league keeps its lore: the same draw as before this change.
    later = [template_persona("ATL", ctx, t, 2036, seed_key=f"gm:ATL:{i}")["start_season"]
             for i in range(40)]
    assert min(later) >= _gp.FIRST_TENURE and max(later) <= 2036
