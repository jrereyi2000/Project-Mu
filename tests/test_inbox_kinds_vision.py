"""The VISION inbox kind (FRONT_OFFICE_INBOX_PLAN.md §2.8-§2.9; U6). Offline: the inbox tests' SQLite fake, the
team vision context and respond faked (the engine itself is covered by tests/test_team_vision.py)."""
import pytest

from engines import team_vision
from engines.inbox import InboxError, actors, registry, store
from engines.inbox import scan as scan_mod
from engines.inbox.kinds import vision
from tests.test_inbox_store import InboxDB

SEASON = 2026
CTX = {"scores_row": {"D": 0.0, "proj_wins": 7.0}, "edges": (1.0, -1.0), "engine_band": "RETOOL",
       "engine_reason": "band", "needs": [], "surplus": [], "cap_space": 10_000_000}
VISION = {"direction": "REBUILD", "emphasis": "DEFENSE", "playing_time": "YOUTH"}
COUNTER = {"direction": "RETOOL", "emphasis": "BALANCED", "playing_time": "YOUTH"}


@pytest.fixture
def db(monkeypatch):
    d = InboxDB()
    monkeypatch.setattr(actors, "user_teams", lambda: [t.upper() for t in d.user_teams])
    return d


@pytest.fixture
def cur(db):
    return db.cursor()


@pytest.fixture(autouse=True)
def fake_context(monkeypatch):
    monkeypatch.setattr(team_vision, "context", lambda cur, team_id, season, week=None: dict(CTX))


def team(cur, week=1):
    acts = actors.controlled(cur, "CAR")
    return {"team_id": 1, "abbr": "CAR", "season": SEASON, "week": week, "offseason_stage": None,
            "actors": acts, "actor": acts[0] if acts else None}


def make_thread(cur, **kw):
    row = {"team_id": 1, "kind": "VISION", "actor_role": "COACH", "actor_id": "41", "counterpart_type": "gm",
           "counterpart_id": "10", "counterpart_name": "Dan Morgan", "subject": "2026 team vision",
           "started_by": "COUNTERPART", "source_ref": "vision:season:2026", "season": SEASON, "week": 1}
    row.update(kw)
    tid = store.create_thread(cur, row)
    return store.get_thread(cur, tid)


# --------------------------------------------------------------------------------------------------- scan ---

def test_the_kind_is_discovered():
    assert registry.kinds(refresh=True).get("VISION") is vision


def test_season_vision_opens_once_for_a_controlled_coach(cur):
    actors.take_control(cur, "CAR", "COACH", "41")
    triggers = vision.scan(cur, SEASON, 1, team(cur))
    assert len(triggers) == 1
    trig = triggers[0]
    assert trig.intent == "SEASON_VISION" and trig.kind == "VISION"
    assert trig.counterpart_type == "gm" and trig.counterpart_id == "10"
    assert trig.actor_role == "COACH" and trig.actor_id == "41"
    assert trig.source_ref == "vision:season:2026"
    assert trig.facts["engine_direction"] == "RETOOL"
    assert trig.facts["allowed"]["direction"] == ["CONTEND", "RETOOL", "REBUILD"]
    tid = scan_mod.open_thread(cur, vision, trig, actors.controlled(cur, "CAR")[0], SEASON, 1)
    assert tid is not None
    thread = store.get_thread(cur, tid)
    assert thread["kind"] == "VISION" and thread["counterpart_type"] == "gm"
    assert store.messages(cur, tid)[0]["body"]
    assert scan_mod.open_thread(cur, vision, trig, actors.controlled(cur, "CAR")[0], SEASON, 1) is None


def test_a_controlled_gm_talks_to_the_head_coach(cur):
    actors.take_control(cur, "CAR", "GM", "10")
    triggers = vision.scan(cur, SEASON, 1, team(cur))
    assert len(triggers) == 1
    assert triggers[0].counterpart_type == "coach" and triggers[0].counterpart_id == "41"
    assert triggers[0].actor_role == "GM"


def test_skips_when_jr_controls_both(cur):
    actors.take_control(cur, "CAR", "GM", "10")
    actors.take_control(cur, "CAR", "COACH", "41")
    assert vision.scan(cur, SEASON, 1, team(cur)) == []


def test_skips_when_the_coach_is_the_gm(db, cur):
    db.make_coach_gm()
    assert vision._counterpart(cur, 1, "COACH") == (None, None, None)
    actors.take_control(cur, "CAR", "COACH", "41")     # pairs the GM row too
    assert vision.scan(cur, SEASON, 1, team(cur)) == []


def test_direction_shift_at_a_checkpoint_away_from_the_pin(cur, monkeypatch):
    actors.take_control(cur, "CAR", "COACH", "41")
    team_vision.upsert(cur, 1, SEASON, {"direction": "REBUILD", "emphasis": "BALANCED", "playing_time": "MERIT"})
    triggers = vision.scan(cur, SEASON, 6, team(cur, 6))
    assert len(triggers) == 1 and triggers[0].intent == "DIRECTION_SHIFT"
    assert triggers[0].source_ref == "vision:shift:2026:6"
    assert triggers[0].facts["pinned"] == "REBUILD" and triggers[0].facts["engine_direction"] == "RETOOL"
    assert vision.scan(cur, SEASON, 5, team(cur, 5)) == []          # not a checkpoint
    monkeypatch.setattr(team_vision, "context",
                        lambda cur, team_id, season, week=None: dict(CTX, engine_band="REBUILD"))
    assert vision.scan(cur, SEASON, 6, team(cur, 6)) == []          # band matches the pin


def test_no_season_trigger_once_a_vision_exists(cur):
    actors.take_control(cur, "CAR", "COACH", "41")
    team_vision.upsert(cur, 1, SEASON, VISION)
    assert vision.scan(cur, SEASON, 2, team(cur, 2)) == []          # week 2 is not a checkpoint


def test_start_builds_a_trigger_for_a_controlled_coach(cur):
    actors.take_control(cur, "CAR", "COACH", "41")
    trig = vision.start(cur, team(cur, 4), {})
    assert trig.intent == "OPEN" and trig.counterpart_type == "gm"
    assert trig.source_ref == "vision:jr:2026:4" and trig.actor_role == "COACH"


def test_start_refuses_when_jr_controls_both_roles(cur):
    """scan skips that case; a JR start must refuse it too, not open a talk with his own GM."""
    actors.take_control(cur, "CAR", "COACH", "41")
    actors.take_control(cur, "CAR", "GM", "10")
    with pytest.raises(InboxError) as e:
        vision.start(cur, team(cur, 4), {})
    assert e.value.status == 400 and "both" in e.value.message


def test_propose_carries_the_form_the_page_needs(cur):
    """Without `form` the page sends payload null and the server answers 400, so the vision form never renders."""
    thread = make_thread(cur)
    propose = vision.options(cur, thread, None)[0]
    assert propose["id"] == "PROPOSE" and propose["form"] == "vision" and propose["needs_note"] is False
    assert propose["default"] == {"direction": None, "emphasis": "BALANCED", "playing_time": "MERIT"}
    team_vision.upsert(cur, 1, SEASON, VISION)
    assert vision.options(cur, thread, None)[0]["default"] == VISION
    for option in vision.options(cur, thread, None)[1:]:
        assert option["form"] is None and option["needs_note"] is False


# ---------------------------------------------------------------------------------------------------- act ---

def test_propose_agree_pins_the_vision_and_closes(cur, monkeypatch):
    thread = make_thread(cur)
    monkeypatch.setattr(team_vision, "respond",
                        lambda *a, **k: {"decision": "AGREE", "vision": VISION, "reasons": ["fits"]})
    res = vision.act(cur, thread, "PROPOSE", {"direction": "REBUILD", "emphasis": "DEFENSE",
                                              "playing_time": "YOUTH"}, None)
    assert res.close and res.intent == "AGREED"
    assert res.engine_ref == {"vision": VISION, "decision": "AGREE", "reasons": ["fits"]}
    stored = team_vision.current(cur, 1, SEASON)
    assert stored["direction"] == "REBUILD" and stored["thread_id"] == thread["id"]


def test_counter_stores_the_counter_and_offers_accept(cur, monkeypatch):
    thread = make_thread(cur)
    monkeypatch.setattr(team_vision, "respond",
                        lambda *a, **k: {"decision": "COUNTER", "vision": COUNTER, "reasons": ["too far"]})
    res = vision.act(cur, thread, "PROPOSE", {"direction": "REBUILD", "emphasis": "BALANCED"}, None)
    assert not res.close and res.intent == "COUNTERED" and res.state["counter"] == COUNTER
    assert team_vision.current(cur, 1, SEASON) is None
    store.update_state(cur, thread["id"], res.state)
    ids = [o["id"] for o in vision.options(cur, store.get_thread(cur, thread["id"]), res)]
    assert ids == ["PROPOSE", "ACCEPT_COUNTER", "KEEP_CURRENT"]


def test_accept_counter_pins_the_counters_vision(cur):
    thread = make_thread(cur, state={"counter": COUNTER, "counter_reasons": ["too far"]})
    res = vision.act(cur, thread, "ACCEPT_COUNTER", {}, None)
    assert res.close and res.intent == "AGREED"
    assert team_vision.current(cur, 1, SEASON)["direction"] == "RETOOL"


def test_refuse_keeps_the_thread_open(cur, monkeypatch):
    thread = make_thread(cur)
    monkeypatch.setattr(team_vision, "respond",
                        lambda *a, **k: {"decision": "REFUSE", "vision": VISION, "reasons": ["over the cap"]})
    res = vision.act(cur, thread, "PROPOSE", {"direction": "REBUILD"}, None)
    assert not res.close and res.intent == "REFUSED" and res.facts["reasons"] == ["over the cap"]
    assert team_vision.current(cur, 1, SEASON) is None
    store.update_state(cur, thread["id"], res.state)
    ids = [o["id"] for o in vision.options(cur, store.get_thread(cur, thread["id"]), res)]
    assert ids == ["PROPOSE", "KEEP_CURRENT"]


def test_keep_current_closes_without_a_pin(cur):
    thread = make_thread(cur)
    res = vision.act(cur, thread, "KEEP_CURRENT", {}, None)
    assert res.close and res.intent == "KEPT"
    assert team_vision.current(cur, 1, SEASON) is None


def test_draft_turns_typed_direction_into_a_propose(cur):
    thread = make_thread(cur)
    d = vision.draft(cur, thread, "Let's rebuild and lean on defense with young players")
    assert d["option_id"] == "PROPOSE"
    assert d["payload"] == {"direction": "REBUILD", "emphasis": "DEFENSE", "playing_time": "YOUTH"}
    assert d["summary"].startswith("Propose a vision: REBUILD")
    assert vision.draft(cur, thread, "how are you today") is None       # no direction, no draft
    # the unstressed fields fall back to the pinned vision
    team_vision.upsert(cur, 1, SEASON, VISION)
    assert vision.draft(cur, thread, "let's retool")["payload"] == {
        "direction": "RETOOL", "emphasis": "DEFENSE", "playing_time": "YOUTH"}


def test_propose_rejects_a_bad_form(cur):
    thread = make_thread(cur)
    with pytest.raises(InboxError):
        vision.act(cur, thread, "PROPOSE", {"direction": "SIDEWAYS"}, None)
    with pytest.raises(InboxError):
        vision.act(cur, thread, "PROPOSE", {"direction": "REBUILD", "emphasis": "SPEED"}, None)
    with pytest.raises(InboxError):
        vision.act(cur, thread, "ACCEPT_COUNTER", {}, None)
    with pytest.raises(InboxError):
        vision.act(cur, thread, "TELEPORT", {}, None)


# ----------------------------------------------------------------------------------------------- template ---

def test_templates_state_the_decision():
    assert "RETOOL" in vision.template("SEASON_VISION", {"team": "CAR", "engine_direction": "RETOOL"})
    assert "REBUILD" in vision.template("DIRECTION_SHIFT", {"team": "CAR", "engine_direction": "RETOOL",
                                                            "pinned": "REBUILD"})
    agreed = vision.template("AGREED", {"team": "CAR", **VISION})
    assert "REBUILD" in agreed and "defense" in agreed and "youth" in agreed
    assert "too far" in vision.template("COUNTERED", {"team": "CAR", **COUNTER, "reasons": ["too far"]})
    assert "over the cap" in vision.template("REFUSED", {"team": "CAR", "reasons": ["over the cap"]})
    assert vision.template("KEPT", {"team": "CAR"})
