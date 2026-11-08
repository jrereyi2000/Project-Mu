"""CONTRACT: extensions and re-signings between JR's GM and one of his players (FRONT_OFFICE_INBOX_PLAN.md §2.8, §4;
U4).

Offline: the inbox store runs on the shared SQLite fake (tests/test_inbox_store.InboxDB); server/franchise_api's
helpers are patched (the module itself is imported lazily by the kind and is not modified), so the tests pin the
shared `_score` path, the intents, the counter, the 409 for an ineligible player and the typed-text draft.
"""
import pytest

from engines.inbox import InboxError, actors, store, voice
from engines.inbox.kinds import contract
from server import franchise_api as fa
from tests.test_inbox_store import InboxDB


@pytest.fixture
def db(monkeypatch):
    d = InboxDB()
    monkeypatch.setattr(actors, "user_teams", lambda: [t.upper() for t in d.user_teams])
    return d


@pytest.fixture
def cur(db):
    return db.cursor()


def contract_dict(length=4, current_year=1):
    return {"length": length, "current_year": current_year, "contract_type": "STANDARD",
            "guaranteed": 10_000_000, "salary_by_year": [1_000_000] * length}


def player(pid, name, pos, ovr, age, yoe, start=2024, ctr=None, injury=None):
    return {"player_id": pid, "name": name, "position": pos, "age": age, "overall": ovr, "yoe": yoe,
            "archetype": None, "dev_trait": "Normal", "contract_row": {} if ctr else None,
            "contract": ctr, "start": start if ctr else None, "year": 0 if ctr else None, "cap_hit": 0,
            "injury": injury}


QB = player(100, "Bryce Young", "QB", 84, 27, 4, ctr=contract_dict())
WR1 = player(101, "Xavier Legette", "WR", 80, 25, 3, ctr=contract_dict())
WR2 = player(102, "Old Receiver", "WR", 78, 31, 9, ctr=contract_dict())
WR3 = player(103, "Slot Guy", "WR", 76, 24, 2, ctr=contract_dict())
WR4 = player(104, "Camp Body", "WR", 60, 23, 0, ctr=contract_dict())
PS = player(105, "Squad Guard", "WR", 99, 23, 0)
IR = player(106, "Hurt Starter", "WR", 90, 26, 4, ctr=contract_dict(), injury={"injury": "Torn ACL",
                                                                                "weeks_out": 10, "on_ir": True})


def ctx(players=None):
    roster = {"ACTIVE": [p for p in (players or []) if p.get("contract") and not (p.get("injury") or {}).get("on_ir")],
              "PS": [p for p in (players or []) if not p.get("contract")], "IR": []}
    return {"team": {"id": 1, "abbr": "CAR", "name": "Panthers"}, "season": 2026, "week": 5, "T": 2026,
            "players": list(players or []), "roster": roster, "moves": [], "pending": {}}


def thread_row(state=None, source_ref="contract:101:2026"):
    return {"team_id": 1, "kind": "CONTRACT", "actor_role": "GM", "actor_id": "10", "counterpart_type": "player",
            "counterpart_id": "101", "counterpart_name": "Xavier Legette", "subject": "Xavier Legette's contract",
            "started_by": "COUNTERPART", "source_ref": source_ref, "season": 2026, "week": 5,
            "state": dict(state or {})}


def make_thread(cur, state=None, source_ref="contract:101:2026"):
    tid = store.create_thread(cur, thread_row(state, source_ref))
    return store.get_thread(cur, tid)


@pytest.fixture
def patch_fa(monkeypatch):
    """A standard franchise_api patch set: the roster's `_context` and one eligible player."""
    state = {"ctx": ctx([WR1]), "player": WR1, "eligible": (True, "veteran: final contract year"),
             "score": ({"utility": 0.62}, "COUNTER", {"years": 4, "total_value": 44_000_000, "bonus": 0,
                                                     "guaranteed": 18_000_000}, 1, 0.75),
             "scores": [], "inserts": []}

    def fake_score(c, cx, p, years, total, bonus, guaranteed, teammates):
        state["scores"].append((p["player_id"], years, total, bonus, guaranteed, len(teammates)))
        return state["score"]

    def fake_insert(*args):
        state["inserts"].append(args)
        return 500

    monkeypatch.setattr(fa, "_context", lambda c, abbr: state["ctx"])
    monkeypatch.setattr(fa, "_player", lambda cx, pid: state["player"])
    monkeypatch.setattr(fa, "_eligibility", lambda p, cx: state["eligible"])
    monkeypatch.setattr(fa, "_score", fake_score)
    monkeypatch.setattr(fa, "insert_offer", fake_insert)
    monkeypatch.setattr(fa, "stage_for", lambda week: "REGULAR_WEEK")
    monkeypatch.setattr(fa, "_seed", lambda cx: "seed")
    return state


# ---------------------------------------------------------------------------------------------------- scan ---

def test_scan_opens_a_window_for_eligible_starters(cur, monkeypatch):
    monkeypatch.setattr(fa, "_context", lambda c, abbr: ctx([QB, WR1, WR2, WR3, WR4, PS, IR]))
    monkeypatch.setattr(fa, "_eligibility",
                        lambda p, cx: (p["player_id"] != 102, "blocked until the end of the season"))
    out = contract.scan(cur, 2026, 5, {"team_id": 1, "abbr": "CAR"})
    assert sorted(t.counterpart_id for t in out) == ["100", "101", "103"]     # 102 ineligible, 104 not a starter,
    #                                                                          PS has no contract, IR is out
    t = next(t for t in out if t.counterpart_id == "100")
    assert t.kind == "CONTRACT" and t.intent == "EXTENSION_WINDOW"
    assert t.counterpart_name == "Bryce Young" and t.source_ref == "contract:100:2026"
    assert t.facts["contract_kind"] == "EXTENSION" and t.facts["team"] == "CAR"


def test_scan_marks_an_expiring_deal_as_a_re_sign(cur, monkeypatch):
    expiring = player(107, "Expiring Vet", "QB", 75, 30, 8, start=2023, ctr=contract_dict())
    monkeypatch.setattr(fa, "_context", lambda c, abbr: ctx([expiring]))
    monkeypatch.setattr(fa, "_eligibility", lambda p, cx: (True, "veteran: final contract year"))
    out = contract.scan(cur, 2026, 5, {"team_id": 1, "abbr": "CAR"})
    assert [t.facts["contract_kind"] for t in out] == ["RESIGN"]


def test_scan_is_idempotent_once_a_thread_exists(cur, monkeypatch):
    monkeypatch.setattr(fa, "_context", lambda c, abbr: ctx([QB]))
    monkeypatch.setattr(fa, "_eligibility", lambda p, cx: (True, "veteran: final contract year"))
    make_thread(cur, state={})
    cur.execute("UPDATE inbox_threads SET counterpart_id = '100', counterpart_name = 'Bryce Young', "
                "source_ref = 'contract:100:2026'")
    assert contract.scan(cur, 2026, 5, {"team_id": 1, "abbr": "CAR"}) == []


# ------------------------------------------------------------------------------------------------- options ---

def test_options_show_accept_counter_only_with_a_counter(cur):
    t = make_thread(cur)
    assert [o["id"] for o in contract.options(cur, t, None)] == ["MAKE_OFFER", "WALK_AWAY"]
    t = make_thread(cur, state={"counter": {"years": 4, "total_value": 44_000_000, "guaranteed": 18_000_000}},
                    source_ref="contract:101:2026:counter")
    assert [o["id"] for o in contract.options(cur, t, None)] == ["MAKE_OFFER", "ACCEPT_COUNTER", "WALK_AWAY"]


# ----------------------------------------------------------------------------------------------------- act ---

def test_make_offer_uses_the_shared_score_path_and_a_counter_stays_open(cur, patch_fa):
    patch_fa["ctx"] = ctx([WR1])
    t = make_thread(cur)
    r = contract.act(cur, t, "MAKE_OFFER", {"years": 3, "total_value": 40_000_000, "bonus": 0,
                                            "guaranteed": 15_000_000}, None)
    assert patch_fa["scores"] == [(101, 3, 40_000_000, 0, 15_000_000, 1)]
    (args,) = patch_fa["inserts"]
    assert args[3] == "EXTENSION" and args[4] == 101 and args[10] == 0.62 and args[11] == "COUNTERED"
    assert r.intent == "COUNTER" and r.close is False
    assert r.state["counter"] == {"years": 4, "total_value": 44_000_000, "bonus": 0, "guaranteed": 18_000_000}
    assert r.engine_ref["decision"] == "COUNTER" and r.engine_ref["kind"] == "EXTENSION"


def test_an_accepted_offer_closes_the_thread(cur, patch_fa):
    patch_fa["score"] = ({"utility": 0.81}, "ACCEPT", None, 1, 0.75)
    t = make_thread(cur)
    r = contract.act(cur, t, "MAKE_OFFER", {"years": 4, "total_value": 52_000_000, "guaranteed": 20_000_000}, None)
    assert patch_fa["inserts"][0][11] == "ACCEPTED"
    assert r.intent == "ACCEPTED" and r.close is True and r.state["phase"] == "AGREED"


def test_accept_counter_scores_the_players_terms_and_closes_on_accept(cur, patch_fa):
    patch_fa["score"] = ({"utility": 0.9}, "ACCEPT", None, 1, 0.75)
    counter = {"years": 4, "total_value": 44_000_000, "bonus": 0, "guaranteed": 18_000_000}
    t = make_thread(cur, state={"counter": counter})
    r = contract.act(cur, t, "ACCEPT_COUNTER", {}, None)
    assert patch_fa["scores"] == [(101, 4, 44_000_000, 0, 18_000_000, 1)]
    assert r.intent == "ACCEPTED" and r.close is True


def test_accept_counter_without_a_counter_is_409(cur, patch_fa):
    t = make_thread(cur)
    with pytest.raises(InboxError) as e:
        contract.act(cur, t, "ACCEPT_COUNTER", {}, None)
    assert e.value.status == 409


def test_ineligible_player_is_409(cur, patch_fa):
    patch_fa["eligible"] = (False, "rookie deal: blocked until the end of the 2028 regular season")
    t = make_thread(cur)
    with pytest.raises(InboxError) as e:
        contract.act(cur, t, "MAKE_OFFER", {"years": 3, "total_value": 40_000_000}, None)
    assert e.value.status == 409 and "blocked" in str(e.value)


def test_bad_terms_are_400(cur, patch_fa):
    t = make_thread(cur)
    with pytest.raises(InboxError) as e:
        contract.act(cur, t, "MAKE_OFFER", {"years": 9, "total_value": 40_000_000}, None)
    assert e.value.status == 400
    with pytest.raises(InboxError) as e:
        contract.act(cur, t, "MAKE_OFFER", {"years": 3, "total_value": 0}, None)
    assert e.value.status == 400


def test_walk_away_closes_and_touches_no_offer(cur, patch_fa):
    t = make_thread(cur)
    r = contract.act(cur, t, "WALK_AWAY", {}, None)
    assert r.close is True and r.intent == "WALKED_AWAY" and patch_fa["inserts"] == []


# ------------------------------------------------------------------------------------------------- draft ---

def test_draft_reads_typed_contract_terms(cur):
    d = contract.draft(cur, make_thread(cur), "3 years, 40 million, 15 guaranteed")
    assert d["option_id"] == "MAKE_OFFER"
    assert d["payload"] == {"years": 3, "total_value": 40_000_000, "bonus": 0, "guaranteed": 15_000_000}
    assert "Offer 3 years, $40.0M total" in d["summary"]
    assert contract.draft(cur, make_thread(cur), "40 million") is None


# ---------------------------------------------------------------------------------------------- template ---

def test_template_lines():
    assert "earned a real role" in contract.template("EXTENSION_WINDOW", {"player": "Xavier Legette"})
    assert "glad to stay" in contract.template("ACCEPTED", {"player": "Xavier Legette"})
    counter = contract.template("COUNTER", {"player": "Xavier Legette",
                                            "counter": {"years": 4, "total_value": 44_000_000,
                                                        "guaranteed": 18_000_000}})
    assert "$44.0M" in counter and "$18.0M" in counter
    assert "play out my deal" in contract.template("WALKED_AWAY", {"player": "Xavier Legette"})


# --------------------------------------------------------------------------------------------------- start ---

def test_start_declares_the_player_pick_form():
    assert contract.START_FORM == "player_pick"


# --------------------------------------------------------------------------------------------- the helpers ---

def test_the_franchise_api_helpers_this_kind_imports_exist():
    for name in ("_context", "_player", "_eligibility", "_score", "insert_offer", "stage_for", "STATUS", "_seed"):
        assert callable(getattr(fa, name, None)) or isinstance(getattr(fa, name, None), dict), name


# --------------------------------------------------------------------------------------------- one turn ---

def test_exchange_stores_a_player_reply_and_judges_it(cur, patch_fa):
    actors.take_control(cur, "CAR", "GM", "10")
    t = make_thread(cur)
    out = voice.exchange(cur, contract, t["id"], "MAKE_OFFER",
                         {"years": 3, "total_value": 40_000_000, "guaranteed": 15_000_000}, note=None, llm=None)
    msgs = store.messages(cur, t["id"])
    assert msgs[0]["author"] == "JR" and msgs[1]["author"] == "COUNTERPART"
    assert "the offer needs to be better" in msgs[1]["body"]
    assert out["status"] == "WAITING_JR"
