"""Trade rationale writer (docs/done/team_management/03_GM_AGENTS_PLAN.md Phase 6). Fake LLMs only: no Gemini, no DB."""
import json
from decimal import Decimal

from engines.gm_rationale import (build_fact_sheet, league_names, parse_answer, template, validate_rationale,
                                  write_rationale)

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


def test_dollar_amount_may_ground_against_a_plain_sheet_number():
    # The inbox contract facts carry dollars as integers (88000000), not "$88.0M" strings
    sheet = dict(SHEET, assets=[dict(SHEET["assets"][0], total_value=88000000, guaranteed=42000000)])
    assert validate_rationale("Four years, $88M total, $42M guaranteed.", sheet, LEAGUE)[0]
    assert validate_rationale("Four years, $95M total, $42M guaranteed.", sheet, LEAGUE) == \
        (False, "amount $95M not on the sheet")


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


# ------------------------------------------------------- fact sheet v2 (§2.3) ----
def _player(pid, pos, group, **extra):
    d = {"id": pid, "team_id": 2, "pos": pos, "group": group, "ovr": 80, "age": 27, "dev": "Normal",
         "name": f"Player {pid}", "role": "Depth Filler",
         "contract": {"contract_id": pid * 10, "end_season": 2037, "length": 4, "total_value": 48_000_000,
                      "bonus": 12_000_000, "cut_penalty": 6_000_000,
                      "years": {2036: (11_030_000, False), 2037: (12_000_000, False)}}}
    d.update(extra)
    return d


def sheet_books():
    return {
        "season": 2036, "week": 5, "window": "IN_SEASON", "value_season": 2036,
        "teams": {1: {"abbr": "CAR", "city": "Carolina", "name": "Panthers", "conference": "NFC",
                      "division": "NFC South"},
                  2: {"abbr": "NO", "city": "New Orleans", "name": "Saints", "conference": "NFC",
                      "division": "NFC South"}},
        "gm": {1: {"gm_name": "Silas Jessup", "voice": "blunt"}, 2: {"gm_name": "Vera Greer", "voice": "measured"}},
        "ctx": {1: {"direction": "CONTEND", "needs": [], "urgent_need": None},
                2: {"direction": "REBUILD", "needs": [], "urgent_need": None}},
        "records": {1: "3-0", 2: "1-2"},
        "wl": {1: (3, 0), 2: (1, 2)},
        "ts": {}, "cap": {1: {"cap_space": 10_000_000, "derived": False},
                          2: {"cap_space": 5_000_000, "derived": False}},
        "players": {10: _player(10, "WR", "WR", name="Robbie Sellers", role="Impact Starter"),
                    11: _player(11, "QB", "QB", name="Amari Anderson"),
                    12: _player(12, "MIKE", "LB", name="George Crawford"),
                    13: _player(13, "HB", "RB", name="Bench Back")},
        "season_stats": {10: {"receptions": 72, "rec_yards": 986, "rec_tds": 7, "games": 15},
                         11: {"completions": 312, "pass_attempts": 480, "pass_yards": 4120, "pass_tds": 32,
                              "interceptions": 11, "games": 16},
                         12: {"tackles": 88, "sacks": Decimal("9.5"), "ints": 2, "games": 17}},
        "form": {1: {"last3": ["W 24-17", "W 30-20", "L 20-23"], "streak": "W2"},
                 2: {"last3": ["L 10-24"], "streak": "L1"}},
        "coaches": {}, "picks": {},
    }


def sheet_deal(ids=(10,)):
    assets = [{"type": "PLAYER", "player_id": pid, "from": 2, "to": 1} for pid in ids]
    return {"a": 1, "b": 2, "trigger": "NEED",
            "assets": assets + [{"type": "PICK", "pick": (2037, 1, 1), "from": 1, "to": 2}]}


def test_fact_sheet_carries_stakes_rivalry_role_contract_and_performance():
    sheet = build_fact_sheet(sheet_books(), sheet_deal())
    json.dumps(sheet)                                         # JSON-safe: no Decimal or other exotic values
    a, b = sheet["teams"]["a"], sheet["teams"]["b"]
    assert a["stakes"] == {"record": "3-0", "last3": ["W 24-17", "W 30-20", "L 20-23"], "streak": "W2", "seed": 1}
    assert b["stakes"] == {"record": "1-2", "last3": ["L 10-24"], "streak": "L1", "seed": 2}
    assert a["rivalry"] == {"relation": "division", "tax": 0.5}
    assert b["rivalry"] == {"relation": "division", "tax": 0.5}
    star = sheet["assets"][0]
    assert star["role"] == "Impact Starter"
    assert star["contract"] == {"apy": 12_000_000, "guaranteed": 12_000_000, "years_left": 2,
                                "dead_money": 6_000_000}
    assert star["performance"] == {"season": "72 rec, 986 yds, 7 TD", "games": 15}
    assert "last3" not in star["performance"]                 # recent=None omits it


def test_fact_sheet_rivalry_relation_for_same_conference_only():
    b = sheet_books()
    b["teams"][2]["division"] = "NFC East"
    sheet = build_fact_sheet(b, sheet_deal())
    assert sheet["teams"]["a"]["rivalry"]["relation"] == "conference"


def test_fact_sheet_stat_line_is_chosen_by_position_and_null_without_a_row():
    sheet = build_fact_sheet(sheet_books(), sheet_deal(ids=(10, 11, 12, 13)))
    json.dumps(sheet)                                         # Decimals in the stat rows come out as numbers
    perf = {x["name"]: x["performance"] for x in sheet["assets"] if x["kind"] == "player"}
    assert perf["Robbie Sellers"]["season"] == "72 rec, 986 yds, 7 TD"                    # WR: receiving
    assert perf["Amari Anderson"]["season"] == "312/480, 4120 yds, 32 TD, 11 INT"        # QB: passing
    assert perf["George Crawford"]["season"] == "88 tkl, 9.5 sacks, 2 INT"               # LB: defense
    assert perf["Bench Back"] is None                                                    # no stat row


def test_fact_sheet_last3_only_when_recent_is_supplied():
    recent = {10: ["6-88-1 @ SEA", "4-60-0 vs ATL"]}
    sheet = build_fact_sheet(sheet_books(), sheet_deal(), recent=recent)
    assert sheet["assets"][0]["performance"]["last3"] == ["6-88-1 @ SEA", "4-60-0 vs ATL"]


def test_validate_rationale_accepts_a_quoted_sheet_stat_and_rejects_an_invented_one():
    sheet = build_fact_sheet(sheet_books(), sheet_deal())
    assert validate_rationale("He put up 986 yards for us last season.", sheet)[0]
    assert not validate_rationale("He put up 1200 yards for us last season.", sheet)[0]


def test_a_small_invented_amount_does_not_ground_on_non_money_numbers():
    """B8 (chunk 9): the money check accepted any plain sheet number within 51k, so an age or an OVR grounded
    "$50K" on any sheet."""
    sheet = {"assets": [{"kind": "player", "name": "A", "age": 21, "ovr": 78, "years_left": 3}],
             "teams": {}, "cap": {}, "recent": {}}
    ok, why = validate_rationale("We move $50K his way.", sheet)
    assert not ok and "not on the sheet" in why
    ok, _ = validate_rationale("He is 21 and a 78.", sheet)          # plain numbers still ground
    assert ok
    big = {"assets": [], "teams": {}, "cap": {"total_value": 88_000_000}, "recent": {}}
    assert validate_rationale("We will take on $88.0M.", big)[0]   # a plain sheet number still grounds it
