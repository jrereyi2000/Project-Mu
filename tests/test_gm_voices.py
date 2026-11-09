"""GM voice cards (GM_TALK_VOICE_PLAN §2.1, U1). Pure: no database, no Gemini."""
import json
import pathlib

import pytest

from engines import gm_voices

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "gm_voices_2026.json"
MIGRATION = ROOT / "db" / "migrations" / "2027_02_13_gm_voice_cards.sql"

CARD = {
    "register": "Talks in short bursts and never names a number first.",
    "samples": ["Put the offer on the table and I will answer it.", "We can talk about the pick."],
    "tics": ["says 'look' before he disagrees"],
    "avoids": ["asset"],
    "wants": "a number he can take upstairs",
}


class ScriptCursor:
    """One SELECT per team, then the UPDATE; records every statement."""

    def __init__(self, rows):
        self.rows = rows                    # {abbr: (gm_id, gm_name)}
        self.statements = []
        self._row = None

    def execute(self, sql, params=()):
        self.statements.append((sql, tuple(params or ())))
        if "FROM gm_profiles" in sql and "abbreviation" in sql:
            self._row = self.rows.get(params[0])
        return self

    def fetchone(self):
        return self._row

    def updates(self):
        return [(sql, params) for sql, params in self.statements if sql.lstrip().upper().startswith("UPDATE")]


def test_parse_card_accepts_the_documented_shape():
    card = gm_voices.parse_card(dict(CARD))
    assert card == CARD


def test_parse_card_drops_extra_keys():
    card = gm_voices.parse_card(dict(CARD, team="ATL", gm="Nobody"))
    assert set(card) == {"register", "samples", "tics", "avoids", "wants"}


@pytest.mark.parametrize("bad", [
    dict(CARD, register="short"),                                       # under 10 characters
    dict(CARD, samples=["only one line, which is long enough"]),        # one sample
    dict(CARD, samples=["line one is fine here", "x" * 200]),           # an over-long sample
    dict(CARD, avoids=[]),                                             # missing avoids
    dict(CARD, tics=[]),                                               # missing tics
    dict(CARD, wants="no"),                                            # under 5 characters
])
def test_parse_card_rejects_bad_cards(bad):
    assert gm_voices.parse_card(bad) is None


def test_parse_card_accepts_a_json_string():
    assert gm_voices.parse_card(json.dumps(CARD)) == CARD
    assert gm_voices.parse_card("not json") is None


def test_template_card_is_deterministic_and_differs_by_aggressiveness():
    calm = {"aggressiveness": 0.2, "risk_appetite": 0.2, "pick_affinity": 0.7, "youth_bias": -0.8}
    hot = {"aggressiveness": 0.9, "risk_appetite": 0.9, "pick_affinity": 1.3, "youth_bias": 0.8}
    assert gm_voices.template_card(calm) == gm_voices.template_card(calm)
    assert gm_voices.template_card(calm)["register"] != gm_voices.template_card(hot)["register"]
    for traits in (calm, hot, {}):
        assert gm_voices.parse_card(gm_voices.template_card(traits)) is not None


def test_template_card_has_a_coach_gm_register():
    card = gm_voices.template_card({}, role="COACH_GM")
    assert gm_voices.parse_card(card) is not None
    assert card["register"].startswith("Talks like a coach")
    assert card != gm_voices.template_card({}, role="GM")


def test_render_card_carries_every_section():
    text = gm_voices.render_card(CARD)
    assert "REGISTER:" in text and "HOW HE SOUNDS:" in text and "TICS:" in text
    assert "NEVER SAYS:" in text and "WANTS:" in text
    assert CARD["samples"][0] in text and CARD["avoids"][0] in text and CARD["wants"] in text


def test_all_32_authored_cards_parse_and_are_distinct():
    data = gm_voices.load_file(DATA)
    assert data["season"] == 2026
    assert len(data["cards"]) == 32
    registers = []
    for abbr, raw in data["cards"].items():
        card = gm_voices.parse_card(raw)
        assert card is not None, abbr
        assert raw.get("gm"), abbr
        registers.append(card["register"])
    assert len(set(registers)) == 32


def test_authored_card_matches_only_its_season():
    assert gm_voices.authored_card("ATL", 2026, DATA) is not None
    assert gm_voices.authored_card("atl", 2026, DATA) == gm_voices.authored_card("ATL", 2026, DATA)
    assert gm_voices.authored_card("ATL", 2036, DATA) is None
    assert gm_voices.authored_card("ZZZ", 2026, DATA) is None


def test_apply_file_refuses_a_name_mismatch_and_writes_both_columns(tmp_path):
    path = tmp_path / "cards.json"
    path.write_text(json.dumps({"season": 2026, "cards": {
        "ATL": dict(CARD, gm="Beth Novak"),
        "AZ": dict(CARD, gm="Hollis Lockhart"),
    }}))
    cur = ScriptCursor({"ATL": (1, "Beth Novak"), "AZ": (2, "Somebody Else")})

    rep = gm_voices.apply_file(cur, path, 2026)

    assert rep["updated"] == ["ATL"]
    assert rep["mismatched"] == [{"team": "AZ", "file": "Hollis Lockhart", "db": "Somebody Else"}]
    updates = cur.updates()
    assert len(updates) == 1
    sql, params = updates[0]
    assert "voice_card" in sql and "'AUTHORED'" in sql
    assert params[2] == 1                                     # the matched GM id
    assert isinstance(params[0], str) and params[0]           # the rendered voice one-liner
    assert gm_voices.parse_card(json.loads(params[1])) == CARD


def test_apply_file_force_updates_the_mismatch(tmp_path):
    path = tmp_path / "cards.json"
    path.write_text(json.dumps({"season": 2026, "cards": {"AZ": dict(CARD, gm="Hollis Lockhart")}}))
    cur = ScriptCursor({"AZ": (2, "Somebody Else")})
    rep = gm_voices.apply_file(cur, path, 2026, force=True)
    assert rep["updated"] == ["AZ"] and rep["mismatched"] == []
    assert len(cur.updates()) == 1


def test_apply_file_dry_run_writes_nothing(tmp_path):
    path = tmp_path / "cards.json"
    path.write_text(json.dumps({"season": 2026, "cards": {"ATL": dict(CARD, gm="Beth Novak")}}))
    cur = ScriptCursor({"ATL": (1, "Beth Novak")})
    rep = gm_voices.apply_file(cur, path, 2026, dry_run=True)
    assert rep["would_update"] == ["ATL"] and rep["updated"] == []
    assert cur.updates() == []


def test_apply_file_reports_a_season_mismatch(tmp_path):
    path = tmp_path / "cards.json"
    path.write_text(json.dumps({"season": 2026, "cards": {"ATL": dict(CARD, gm="Beth Novak")}}))
    cur = ScriptCursor({"ATL": (1, "Beth Novak")})
    rep = gm_voices.apply_file(cur, path, 2036)
    assert "error" in rep and not rep["updated"] and cur.updates() == []


def test_the_migration_adds_both_columns():
    sql = MIGRATION.read_text()
    assert "voice_card JSONB" in sql
    assert "voice_card_source TEXT" in sql
    assert "AUTHORED" in sql and "LLM" in sql and "TEMPLATE" in sql
