"""The write-time fact gate (`newsroom/fact_gate.py`). Offline: a fake google-genai client.

The gate's fix is claim coverage, not the checker model: the rubric makes the three attribute and
event passes before any number, an invented person with a stat line is CONTRADICTED while a bare name
is not, and the checker sees the whole evidence window (plan §3.1). These tests pin the verdict
passthrough, the prompt/rubric and its truncation rule, the rewrite message and the "a checker
outage never fails a write" rule.

Run: pytest -m "not integration" tests/test_fact_gate.py
"""
import json

import pytest

from newsroom import fact_gate


class _Response:
    def __init__(self, text):
        self.text = text
        self.candidates = []


class _Models:
    def __init__(self, answers):
        self._answers = list(answers)
        self.prompts = []
        self.configs = []
        self.models_seen = []

    def generate_content(self, model=None, contents=None, config=None):
        self.prompts.append(contents)
        self.configs.append(config)
        self.models_seen.append(model)
        return _Response(self._answers.pop(0))


class _Client:
    """The shape the gate uses: `client.models.generate_content(...)` -> object with `.text`."""

    def __init__(self, *answers):
        self.models = _Models(answers)


def _answer(claims, note="ok"):
    return json.dumps({"claims": claims, "note": note})


def _claim(claim, verdict, evidence, derived=False):
    return {"claim": claim, "verdict": verdict, "evidence": evidence, "derived": derived}


CARSON_EVIDENCE = """[2036 Week 18, CAROLINA PANTHERS]
Team season: 12-5, NFC South champions. Passing: Bryce Young 4,102 yards, 31 TD, 9 INT.
Rushing: Chuba Hubbard 1,204 yards, 9 TD. Receiving: Xavier Legette 1,088 yards, 8 TD.
Player seasons: Xavier Legette, WR, years_of_experience 3.
Player seasons: Jalen Coker, WR, years_of_experience 1.
"""

CARSON_ARTICLE = """CHARLOTTE — The Panthers' offense runs through Isaiah Carson, the most explosive
receiver in football. Carson caught 121 passes for 1,755 yards and 20 touchdowns as a rookie,
carrying Carolina to the NFC South title.
"""


# ---------------------------------------------------------------- the Carson fabrication

def test_fabricated_person_with_statistics_is_contradicted():
    """The plan's fixture: a player absent from the evidence, with a full stat line."""
    client = _Client(_answer([
        _claim("Isaiah Carson caught 121 passes for 1,755 yards and 20 touchdowns as a rookie.",
               "CONTRADICTED", "not in evidence"),
        _claim("Carolina won the NFC South.", "SUPPORTED", "NFC South champions"),
    ]))

    result = fact_gate.check(CARSON_ARTICLE, CARSON_EVIDENCE, model="fake-checker", client=client)

    claims = [c for c in result["claims"] if "Carson" in c["claim"]]
    assert len(claims) == 1
    assert claims[0]["verdict"] == "CONTRADICTED"
    assert claims[0]["evidence"] == "not in evidence"
    assert result["counts"] == {"SUPPORTED": 1, "CONTRADICTED": 1, "UNSUPPORTED": 0}
    assert result["ok"] is False
    assert result["model"] == "fake-checker"


def test_rubric_runs_the_three_passes_before_numbers():
    """The coverage fix lives in the rubric: person, status, result — then numbers."""
    rubric = fact_gate.RUBRIC
    assert "three passes FIRST" in rubric
    assert "Every named person" in rubric
    assert "years_of_experience" in rubric
    assert "0 = rookie" in rubric
    assert "(winner, loser, score)" in rubric
    assert '"not in evidence"' in rubric
    assert "Never use outside knowledge" in rubric
    assert f"Cap at {fact_gate.MAX_CLAIMS} claims" in rubric
    # The CLI's default cap still builds the CLI's rubric.
    assert "Cap at 40 claims" in fact_gate.build_rubric(40)


def test_checker_is_asked_with_the_article_and_evidence_under_the_rubric():
    client = _Client(_answer([]))
    fact_gate.check("Article body.", "Evidence body.", model="fake-checker", client=client)

    prompt = client.models.prompts[0]
    config = client.models.configs[0]
    assert "Article body." in prompt
    assert "Evidence body." in prompt
    assert "EVIDENCE THE WRITER RETRIEVED" in prompt
    assert config.system_instruction.startswith("You are a fact-checker")
    assert config.temperature == 0.0
    assert config.max_output_tokens == fact_gate.MAX_OUTPUT_TOKENS == 32768
    assert str(config.thinking_config.thinking_level).endswith("LOW")


# ---------------------------------------------------------------- the whole evidence window (plan §3.1)

def _writer_evidence(assignment_chars=46_000):
    """The shape `_execute_agent` builds: a ~46k assignment, then three 12k-sensitive tool results."""
    assignment = ("ASSIGNMENT: write the Week 18 slate recap from the tool results below. " * 900)
    assignment = assignment[:assignment_chars]
    blocks = []
    for i in range(3):
        body = (f"TOOL get_team_season(team={i}):\n" + f"team {i} 16-0, PF 869 PA 295. " * 400)[:11_930]
        blocks.append(body)
    blocks[-1] += "\nPlayer seasons: Elijah Vance, WR, years_of_experience 1."
    return assignment, "\n\n".join(blocks)


def test_the_whole_assignment_and_every_tool_result_reach_the_checker():
    """The first run's bound was 24k against 97k of evidence; every real article must now fit."""
    assignment, tools = _writer_evidence()
    client = _Client(_answer([]))

    result = fact_gate.check("Article body.", assignment + "\n\n" + tools,
                             model="fake-checker", client=client)

    prompt = client.models.prompts[0]
    assert fact_gate.EVIDENCE_LIMIT >= 400_000
    assert len(prompt) > len(assignment) + len(tools)
    assert assignment[-40:] in prompt                    # the assignment is not cut
    assert "Elijah Vance" in prompt                      # the last tool result is not cut
    assert "EVIDENCE TRUNCATED" not in prompt
    assert result["truncated"] is False


def test_a_forced_cut_marks_the_gap_and_keeps_the_whole_assignment():
    assignment = "ASSIGNMENT HEAD " + ("brief text. " * 200) + " ASSIGNMENT TAIL"
    first = "TOOL get_standings():\nFIRST_TOOL_FACT " + ("a" * 2000)
    middle = "TOOL get_leaders():\nMIDDLE_ONLY_FACT " + ("m" * 3000)
    last = "TOOL get_schedule():\n" + ("l" * 2000) + " LAST_TOOL_FACT"
    evidence = assignment + "\n\n" + "\n\n".join([first, middle, last])

    shown, truncated = fact_gate.window_evidence(evidence, limit=len(assignment) + 4000)

    assert truncated is True
    assert shown.startswith(assignment)                  # the whole assignment survives, end included
    assert "EVIDENCE TRUNCATED HERE" in shown
    assert "characters of tool results not shown" in shown
    assert "MIDDLE_ONLY_FACT" not in shown               # only tool text is dropped
    assert "LAST_TOOL_FACT" in shown                     # the tail of the tool trace survives
    assert len(shown) < len(assignment) + 4000 + 200     # the bound holds, marker aside


def test_a_cut_evidence_window_adds_the_partial_evidence_rule(monkeypatch):
    monkeypatch.setattr(fact_gate, "EVIDENCE_LIMIT", 3000)
    assignment = ("ASSIGNMENT: write the recap. " * 100)[:1200]
    evidence = assignment + "\n\nTOOL get_schedule():\n" + ("game result line. " * 1200)
    client = _Client(_answer([]))

    result = fact_gate.check("Article body.", evidence, model="fake-checker", client=client)

    assert result["truncated"] is True
    assert "EVIDENCE TRUNCATED HERE" in client.models.prompts[0]
    assert "is PARTIAL" in client.models.configs[0].system_instruction


def test_rubric_scopes_absence_and_only_a_cut_window_makes_it_proof_free():
    rubric = fact_gate.RUBRIC
    assert "Every named person a claim is attached to" in rubric
    assert "not about whether every name is mentioned" in rubric
    assert "only when the article attaches" in rubric
    assert "A bare name in passing" in rubric
    assert "is PARTIAL" not in rubric
    assert "UNSUPPORTED, never CONTRADICTED" not in rubric

    cut_rubric = fact_gate.build_rubric(fact_gate.MAX_CLAIMS, truncated=True)
    assert cut_rubric.startswith(rubric)
    assert "is PARTIAL" in cut_rubric
    assert "UNSUPPORTED, never CONTRADICTED" in cut_rubric



# ---------------------------------------------------------------- status words vs years_of_experience

def test_second_year_player_called_a_rookie_is_contradicted():
    evidence = "Jalen Coker, WR, years_of_experience 1, 612 receiving yards in 2036."
    article = "Jalen Coker is a rookie receiver for the Panthers."
    client = _Client(_answer([
        _claim("Jalen Coker is a rookie.", "CONTRADICTED", "years_of_experience 1"),
    ]))

    result = fact_gate.check(article, evidence, model="fake-checker", client=client)

    assert result["counts"]["CONTRADICTED"] == 1
    assert result["ok"] is False
    assert result["claims"][0]["evidence"] == "years_of_experience 1"


def test_true_rookie_is_supported():
    evidence = "Xavier Legette, WR, years_of_experience 0, 988 receiving yards in 2036."
    article = "Xavier Legette is a rookie receiver for the Panthers."
    client = _Client(_answer([
        _claim("Xavier Legette is a rookie.", "SUPPORTED", "years_of_experience 0"),
    ]))

    result = fact_gate.check(article, evidence, model="fake-checker", client=client)

    assert result["counts"] == {"SUPPORTED": 1, "CONTRADICTED": 0, "UNSUPPORTED": 0}
    assert result["ok"] is True


# ---------------------------------------------------------------- game results as a triple

def test_swapped_score_in_a_game_result_is_contradicted():
    evidence = "Week 18: Buffalo Bills beat Miami Dolphins 24-21 at Highmark Stadium."
    article = "The Dolphins beat the Bills 21-24 in Week 18."
    client = _Client(_answer([
        _claim("The Dolphins beat the Bills 21-24.", "CONTRADICTED",
               "Buffalo Bills beat Miami Dolphins 24-21"),
    ]))

    result = fact_gate.check(article, evidence, model="fake-checker", client=client)

    assert result["counts"]["CONTRADICTED"] == 1
    assert result["ok"] is False


# ---------------------------------------------------------------- arithmetic stays supported

def test_derived_number_is_flagged_derived_and_not_contradicted():
    evidence = "Two games: 6 catches and 4 catches."
    article = "He averaged 5.0 catches per game."
    client = _Client(_answer([
        _claim("He averaged 5.0 catches per game.", "SUPPORTED", "6 catches and 4 catches",
               derived=True),
    ]))

    result = fact_gate.check(article, evidence, model="fake-checker", client=client)

    assert result["claims"][0]["derived"] is True
    assert result["claims"][0]["verdict"] == "SUPPORTED"
    assert result["counts"]["CONTRADICTED"] == 0
    assert result["ok"] is True


# ---------------------------------------------------------------- clean article, rewrite message

def test_clean_article_is_ok_and_needs_no_rewrite():
    client = _Client(_answer([
        _claim("Carolina finished 12-5.", "SUPPORTED", "Team season: 12-5"),
        _claim("Bryce Young threw 31 touchdowns.", "SUPPORTED", "Bryce Young 4,102 yards, 31 TD"),
    ]))

    result = fact_gate.check("A clean recap.", CARSON_EVIDENCE, model="fake-checker", client=client)

    assert result["ok"] is True
    assert result["counts"]["CONTRADICTED"] == 0
    contradicted = [c for c in result["claims"] if c["verdict"] == "CONTRADICTED"]
    assert fact_gate.rewrite_prompt(contradicted) == ""


def test_rewrite_prompt_names_every_contradiction_and_forbids_new_claims():
    contradicted = [
        _claim("Isaiah Carson caught 121 passes.", "CONTRADICTED", "not in evidence"),
        _claim("The Dolphins beat the Bills 21-24.", "CONTRADICTED",
               "Buffalo Bills beat Miami Dolphins 24-21"),
        _claim("A harmless line.", "SUPPORTED", "Team season: 12-5"),
    ]

    prompt = fact_gate.rewrite_prompt(contradicted)

    assert "Isaiah Carson caught 121 passes." in prompt
    assert "not in evidence" in prompt
    assert "The Dolphins beat the Bills 21-24." in prompt
    assert "Buffalo Bills beat Miami Dolphins 24-21" in prompt
    assert "A harmless line." not in prompt
    assert "no new claims" in prompt.lower()
    assert "correct or delete" in prompt.lower()

    # Both contradicted claims reach the checker's rewrite message; a pre-filtered list works too.
    only_contradicted = [c for c in contradicted if c["verdict"] == "CONTRADICTED"]
    assert fact_gate.rewrite_prompt(only_contradicted) == prompt


# ---------------------------------------------------------------- a checker outage never raises

def test_no_checker_returns_skipped(monkeypatch):
    monkeypatch.setattr(fact_gate, "client_for_model", lambda model: (None, "OPENCODE_GO_API_KEY is not set"))

    result = fact_gate.check("article", "evidence", model="deepseek-v4.1-flash")

    assert result["ok"] is True
    assert result["skipped"] == "no checker"
    assert result["claims"] == []
    assert result["counts"] == {"SUPPORTED": 0, "CONTRADICTED": 0, "UNSUPPORTED": 0}
    assert result["model"] == "deepseek-v4.1-flash"


def test_client_build_exception_returns_skipped(monkeypatch):
    def boom(model):
        raise RuntimeError("no SDK")

    monkeypatch.setattr(fact_gate, "client_for_model", boom)

    result = fact_gate.check("article", "evidence", model="deepseek-v4.1-flash")

    assert result == {"ok": True, "skipped": "no checker", "claims": [],
                      "counts": {"SUPPORTED": 0, "CONTRADICTED": 0, "UNSUPPORTED": 0},
                      "model": "deepseek-v4.1-flash"}


def test_transport_error_returns_skipped():
    class _Broken:
        class models:
            @staticmethod
            def generate_content(**_kwargs):
                raise ConnectionError("checker unreachable")

    result = fact_gate.check("article", "evidence", model="fake-checker", client=_Broken())

    assert result["ok"] is True
    assert result["skipped"] == "no checker"


def test_unparsable_answer_returns_skipped():
    client = _Client("I could not check this article.")

    result = fact_gate.check("article", "evidence", model="fake-checker", client=client)

    assert result["ok"] is True
    assert result["skipped"] == "no checker"


# ---------------------------------------------------------------- model resolution and the cap

def test_model_resolution_argument_then_environment_then_default(monkeypatch):
    client = _Client(_answer([]), _answer([]))
    monkeypatch.setenv("FACT_GATE_MODEL", "glm-5.3-flash")

    from_env = fact_gate.check("article", "evidence", client=client)
    from_arg = fact_gate.check("article", "evidence", model="gpt-6-luna", client=client)

    assert from_env["model"] == "glm-5.3-flash"
    assert from_arg["model"] == "gpt-6-luna"
    assert client.models.models_seen == ["glm-5.3-flash", "gpt-6-luna"]

    monkeypatch.delenv("FACT_GATE_MODEL")
    monkeypatch.setattr(fact_gate, "FACT_GATE_MODEL", "deepseek-v4.1-flash")
    assert fact_gate.resolve_model(None) == "deepseek-v4.1-flash"


def test_claims_are_capped_at_max_claims():
    claims = [_claim(f"Claim {i}.", "SUPPORTED", "evidence") for i in range(70)]
    client = _Client(_answer(claims))

    result = fact_gate.check("article", "evidence", model="fake-checker", client=client)

    assert len(result["claims"]) == fact_gate.MAX_CLAIMS == 60
    assert result["counts"]["SUPPORTED"] == 60


def test_claims_are_normalized_to_the_four_fields():
    client = _Client(_answer([
        {"claim": "  A number.  ", "verdict": "contradicted", "evidence": " quote ", "derived": 1,
         "extra": "dropped"},
        "not a dict",
    ]))

    result = fact_gate.check("article", "evidence", model="fake-checker", client=client)

    assert result["claims"] == [{"claim": "A number.", "verdict": "CONTRADICTED",
                                "evidence": "quote", "derived": True}]
    assert result["counts"]["CONTRADICTED"] == 1
