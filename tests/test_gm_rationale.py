"""Trade rationale writer (docs/done/team_management/03_GM_AGENTS_PLAN.md Phase 6). Fake LLMs only: no Gemini, no DB."""
import json

from engines.gm_rationale import league_names, parse_answer, template, validate_rationale, write_rationale

SHEET = {
    "season": 2036, "week": 5, "window": "IN_SEASON", "trigger": "NEED",
    "teams": {"a": {"team_id": 11, "abbr": "CAR", "team": "Carolina Panthers", "nickname": "Panthers", "record": "3-0",
                    "direction": "CONTEND", "needs": ["edge rusher"], "cap_before": "$161.00M", "cap_after": "$149.97M",
                    "gm": "Silas Jessup", "voice": "blunt", "coach": "Dale Whitfield", "urgent": None},
              "b": {"team_id": 29, "abbr": "NO", "team": "New Orleans Saints", "nickname": "Saints", "record": "1-2",
                    "direction": "REBUILD", "needs": ["cornerback"], "cap_before": "$20.00M", "cap_after": "$31.03M",
                    "gm": "Vera Greer", "voice": "measured", "coach": None, "urgent": None}},
    "assets": [{"kind": "player", "name": "Robbie Sellers", "pos": "LEDG", "group": "EDGE", "ovr": 83, "age": 29,
                "dev": "Star", "cap_hit": "$11.03M", "years_left": 2, "from": "NO", "to": "CAR"},
               {"kind": "pick", "name": "2037 R1 (CAR)", "from": "CAR", "to": "NO"}]}
LEAGUE = {"players": {"ROBBIE SELLERS", "AMARI ANDERSON", "GEORGE CRAWFORD"},
          "teams": {"CAR", "PANTHERS", "CAROLINA", "NO", "SAINTS", "NEW ORLEANS", "FALCONS", "ATLANTA"}}


def ok(text):
    return validate_rationale(text, SHEET, LEAGUE)[0]


def test_validator_accepts_invented_colour():
    assert ok("Silas Jessup has wanted Robbie Sellers since the summer; the front office has been buzzing all week.")
    assert ok("The Panthers take on $11.03M this year and still sit at $149.97M of space.")
    assert ok("Sellers is 29 and an 83 OVR rusher, a Star on a deal with 2 years left.")


def test_validator_rejects_invented_number_player_team():
    assert not ok("Sellers had 14 sacks last year.")                        # a number not on the sheet
    assert not ok("We freed $25M of space.")                                # an amount not on the sheet
    assert not ok("This is the best move since we landed George Crawford.")  # a league player not in the deal
    assert not ok("The Falcons were in on him too.")                        # a league team not in the deal
    assert validate_rationale("", SHEET, LEAGUE) == (False, "empty")


def test_team_abbreviation_only_in_capitals():
    # "was" is not Washington, "no" is not New Orleans: an abbreviation counts only written in capitals
    league = {"players": set(), "teams": {"CAR", "PANTHERS", "CAROLINA", "WAS", "COMMANDERS", "WASHINGTON", "IND"}}
    sheet = dict(SHEET, teams={"a": SHEET["teams"]["a"],
                               "b": dict(SHEET["teams"]["b"], abbr="ATL", nickname="Falcons", team="Atlanta Falcons")})
    assert validate_rationale("Sellers was the piece we needed, no question; indeed he was.", sheet, league)[0]
    assert not validate_rationale("WAS called about him too.", sheet, league)[0]
    assert not validate_rationale("Washington called about him too.", sheet, league)[0]
    assert not validate_rationale("The commanders called about him too.", sheet, league)[0]


def answer(**kw):
    d = {"headline": "Panthers land Robbie Sellers", "rationale_a": "Silas Jessup: We needed an edge rusher.",
         "rationale_b": "Vera Greer: The 2037 R1 (CAR) pick fits our rebuild."}
    d.update(kw)
    return json.dumps(d)


def test_llm_answer_used_when_valid():
    r = write_rationale(SHEET, llm=lambda p: answer(), league=LEAGUE)
    assert r["source"] == "LLM" and r["headline"] == "Panthers land Robbie Sellers"


def test_invalid_answer_falls_back_to_template():
    r = write_rationale(SHEET, llm=lambda p: answer(rationale_a="He had 14 sacks."), league=LEAGUE)
    assert r["source"] == "TEMPLATE" and r["rejected"] == ["number 14 not on the sheet"]
    assert write_rationale(SHEET, llm=lambda p: "no json", league=LEAGUE)["source"] == "TEMPLATE"


def test_client_error_falls_back_to_template():
    def boom(prompt):
        raise RuntimeError("503")
    r = write_rationale(SHEET, llm=boom, league=LEAGUE)
    assert r == template(SHEET)
    assert write_rationale(SHEET, llm=None) == template(SHEET)


def test_template_passes_its_own_validator():
    t = template(SHEET)
    assert all(ok(t[k]) for k in ("headline", "rationale_a", "rationale_b"))
    assert "edge rusher" in t["rationale_a"]


def test_prompt_states_the_rule_and_the_sheet():
    seen = []
    write_rationale(SHEET, llm=lambda p: seen.append(p) or answer(), league=LEAGUE)
    assert "Never state a number" in seen[0] and "Robbie Sellers" in seen[0] and "blunt" in seen[0]
    assert parse_answer('{"headline": "x"}') is None


def test_league_names_from_books():
    b = {"players": {1: {"name": "Robbie Sellers"}}, "teams": {11: {"abbr": "CAR", "name": "Panthers", "city": "Carolina"}}}
    assert league_names(b) == {"players": {"ROBBIE SELLERS"}, "teams": {"CAR", "PANTHERS", "CAROLINA"}}


def test_multi_word_city_of_a_deal_team_passes():
    sheet = dict(SHEET, teams={"a": dict(SHEET["teams"]["a"], abbr="NYJ", nickname="Jets", team="New York Jets"),
                               "b": SHEET["teams"]["b"]})
    league = {"players": set(), "teams": {"NYJ", "JETS", "NEW YORK", "NYG", "GIANTS", "NO", "SAINTS", "NEW ORLEANS"}}
    assert validate_rationale("New York finally gets its rusher.", sheet, league)[0]
    assert validate_rationale("New Orleans moves on.", sheet, league)[0]
    assert not validate_rationale("The Giants were in on him too.", sheet, league)[0]


def test_template_pick_wording_and_rejection_flags():
    from engines.gm_trade_engine import llm_flags
    t = template(SHEET)
    assert "a 2037 first-round pick" in t["rationale_b"]
    r = write_rationale(SHEET, llm=lambda p: answer(rationale_a="He had 14 sacks."), league=LEAGUE)
    assert llm_flags(r) == ["llm_rejected:number 14 not on the sheet"]
    assert llm_flags(template(SHEET)) == []


def test_expiring_claim_needs_a_last_year_player():
    assert not ok("Moving Robbie Sellers on an expiring contract is simple risk management.")   # 2 years left
    last = dict(SHEET, assets=[dict(SHEET["assets"][0], years_left=1), SHEET["assets"][1]])
    assert validate_rationale("Sellers is a rental, and we're fine with that.", last, LEAGUE)[0]
