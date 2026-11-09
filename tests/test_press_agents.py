"""Offline tests for the press conference reporter and coach agents (PRESS_CONFERENCE_PLAN §2.5, U4).

No database and no network: every model call is a scripted fake, and every cursor is a fake. Run:

    pytest -m "not integration" tests/test_press_agents.py
"""
import json
import os

import pytest

from newsroom import press_agents
from personality import relationship_events as rev

SEASON = 2036
WEEK = 8
CONFERENCE_ID = 77

SUBJECT = {
    "id": 9, "name": "Marcus Whitfield", "team_id": 1, "team_name": "Bills", "team_abbr": "BUF",
    "team_key": "team:1", "persona_id": "db-coach:9", "role": "coach",
}


def make_sheet():
    """A compact fact sheet matching press_facts.check_line's expectations."""
    return {
        "game": {
            "id": 800, "season": SEASON, "week": WEEK, "game_type": "REGULAR_SEASON",
            "home": {"id": 1, "name": "Bills", "city": "Buffalo", "abbr": "BUF"},
            "away": {"id": 2, "name": "Falcons", "city": "Atlanta", "abbr": "ATL"},
            "final": {"home": 24, "away": 17},
        },
        "teams": [
            {
                "team_id": 1, "name": "Bills", "city": "Buffalo", "abbr": "BUF", "home": True,
                "record_before": "6-1", "record_after": "7-1",
                "streak": {"type": "W", "count": 3}, "division_place": 1, "conference_seed": 1,
                "coach": {"id": 9, "name": "Marcus Whitfield", "tenure": 3,
                          "schemes": {"offense": "Spread", "defense": "4-3"},
                          "tendencies": {"pass_lean": "High"}, "hot_seat": None},
                "line": {"yards": 350, "pass_yards": 240, "rush_yards": 110,
                         "turnovers": 1, "takeaways": 2, "sacks": 3},
                "players": [
                    {"player_id": 101, "name": "Josh Allen", "team": "BUF", "position": "QB",
                     "line": {"pass_yards": 240, "pass_tds": 2, "interceptions": 1,
                              "rush_yards": 30, "yards": 270, "touchdowns": 2},
                     "season_averages": None},
                ],
                "injuries": [], "transactions": [], "records": [],
            },
            {
                "team_id": 2, "name": "Falcons", "city": "Atlanta", "abbr": "ATL", "home": False,
                "record_before": "4-3", "record_after": "4-4",
                "streak": {"type": "L", "count": 1}, "division_place": 2, "conference_seed": 6,
                "coach": {"id": 10, "name": "Dan Quinn", "tenure": 2,
                          "schemes": {}, "tendencies": {}, "hot_seat": None},
                "line": {"yards": 300, "pass_yards": 220, "rush_yards": 80,
                         "turnovers": 2, "takeaways": 1, "sacks": 1},
                "players": [
                    {"player_id": 102, "name": "Drake London", "team": "ATL", "position": "WR",
                     "line": {"receptions": 5, "rec_yards": 80, "yards": 80, "touchdowns": 1},
                     "season_averages": None},
                ],
                "injuries": [], "transactions": [], "records": [],
            },
        ],
        "names": ["Marcus Whitfield", "Dan Quinn", "Josh Allen", "Drake London"],
        "numbers": [
            ["yards", "BUF", 350], ["pass_yards", "BUF", 240], ["rush_yards", "BUF", 110],
            ["turnovers", "BUF", 1], ["takeaways", "BUF", 2], ["sacks", "BUF", 3],
            ["yards", "ATL", 300], ["pass_yards", "ATL", 220], ["rush_yards", "ATL", 80],
            ["turnovers", "ATL", 2], ["takeaways", "ATL", 1], ["sacks", "ATL", 1],
            ["interceptions", "Josh Allen", 1], ["pass_tds", "Josh Allen", 2],
            ["pass_yards", "Josh Allen", 240], ["rush_yards", "Josh Allen", 30],
            ["yards", "Josh Allen", 270], ["touchdowns", "Josh Allen", 2],
            ["receptions", "Drake London", 5], ["rec_yards", "Drake London", 80],
            ["yards", "Drake London", 80], ["touchdowns", "Drake London", 1],
            ["points", "BUF", 24], ["points", "ATL", 17], ["score", "BUF", 24], ["score", "ATL", 17],
        ],
    }


def make_state(**overrides):
    state = {
        "conference_id": CONFERENCE_ID,
        "season": SEASON,
        "week": WEEK,
        "subject": dict(SUBJECT),
        "sheet": make_sheet(),
        "transcript": [],
        "hands": [],
        "picks": {},
        "hand_counts": {},
        "last_answer": None,
        "last_asker": None,
        "questions_asked": 0,
        "follow_ups_in_a_row": 0,
        "passes_in_a_row": {},
        "done": [],
        "mood": 3,
        "patience": {"budget": 14, "spent": 0},
        "wants_to_end": False,
        "controller": "AGENT",
        "memory_blocks": {},
    }
    state.update(overrides)
    return state


def beat(agent_id="beat1", **overrides):
    attendee = {
        "agent_id": agent_id, "person_key": "pk-" + agent_id, "persona_id": "persona-" + agent_id,
        "archetype": "LOCAL_BEAT_REPORTER", "display_name": "Dana Reed", "handle": "@DanaReed",
        "role": "BEAT", "pool_tier": None, "team_name": "Bills",
    }
    attendee.update(overrides)
    return attendee


def national(agent_id="nat1", **overrides):
    attendee = {
        "agent_id": agent_id, "person_key": "pk-" + agent_id, "persona_id": "persona-" + agent_id,
        "archetype": "SANDO", "display_name": "Mike Sando", "handle": "@Sando",
        "role": "NATIONAL", "pool_tier": "REPORTER",
    }
    attendee.update(overrides)
    return attendee


class FakeLLM:
    def __init__(self, replies=()):
        self.replies = list(replies)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if not self.replies:
            return '{"action": "PASS", "text": "", "about": [], "tone": "neutral", "reply_to": null}'
        return self.replies.pop(0)


def ask_reply(text="Coach, Josh Allen threw one interception. How do you get him back on track?",
              tone="neutral", about=None):
    # check_line reads digits; use the numeral form in tests.
    return json.dumps({"action": "ASK", "text": text, "about": about or [{"type": "player", "id": 101}],
                       "tone": tone, "reply_to": None})


# --------------------------------------------------------------------------------------------- reporter turn
def test_reporter_prompt_holds_persona_memory_sheet_and_transcript():
    state = make_state(
        memory_blocks={"beat1": "PRESS-MEMORY-BLOCK"},
        transcript=[{"turn_id": 1, "speaker": "REPORTER", "agent_id": "other",
                     "kind": "QUESTION", "text": "TRANSCRIPT-QUESTION-MARKER"}],
    )
    fake = FakeLLM([ask_reply("Coach, Josh Allen threw 1 interception. How do you fix it?")])
    result = press_agents.reporter_turn(beat(), state, fake)

    prompt = fake.prompts[0]
    assert "HOW YOU WRITE A POST" in prompt                     # the persona block, POST habits
    assert "PRESS-MEMORY-BLOCK" in prompt
    assert "TRANSCRIPT-QUESTION-MARKER" in prompt
    assert '"final"' in prompt and '"teams"' in prompt          # the fact sheet
    assert "only about the Bills" in prompt
    assert result["action"] == "ASK" and result["source"] == "AGENT" and not result["dropped"]


def test_national_reporter_gets_the_national_line():
    fake = FakeLLM([ask_reply("Coach, how did the fourth quarter get away from you?")])
    press_agents.reporter_turn(national(), make_state(), fake)
    assert "a national reporter covering this game" in fake.prompts[0]
    assert "only about the Bills" not in fake.prompts[0]


def test_memory_block_comes_from_agent_memory(monkeypatch):
    monkeypatch.setattr(press_agents.agent_memory, "build_memory_block",
                        lambda *args, **kwargs: "FROM-BUILD-MEMORY-BLOCK")
    state = make_state()
    state["cur"] = object()
    fake = FakeLLM([ask_reply()])
    press_agents.reporter_turn(beat(), state, fake)
    assert "FROM-BUILD-MEMORY-BLOCK" in fake.prompts[0]


def test_relationship_block_is_omitted_when_neutral_and_shown_when_enabled():
    state = make_state(relationship_contexts={"pk-beat1": {"status": "neutral", "edges": []}})
    fake = FakeLLM([ask_reply()])
    press_agents.reporter_turn(beat(), state, fake)
    assert "YOUR RELATIONSHIP WITH THIS COACH" not in fake.prompts[0]

    enabled = {"status": "enabled", "edges": [{
        "target": {"kind": "person", "persona_id": "db-coach:9"}, "status": "known", "trust": 0.6,
        "attachment": {"tier": "established"}, "breaches": [],
    }]}
    state = make_state(relationship_contexts={"pk-beat1": enabled})
    fake = FakeLLM([ask_reply()])
    press_agents.reporter_turn(beat(), state, fake)
    assert "YOUR RELATIONSHIP WITH THIS COACH" in fake.prompts[0]
    assert "warm" in fake.prompts[0]


def test_a_failing_database_read_is_neutral_never_an_exception():
    class BoomCursor:
        def execute(self, *args, **kwargs):
            raise RuntimeError("no database")

        def fetchone(self):
            raise RuntimeError("no database")

    state = make_state(universe_id="mu:test", league_id=1)
    state["cur"] = BoomCursor()
    fake = FakeLLM([ask_reply()])
    result = press_agents.reporter_turn(beat(), state, fake)
    assert result["action"] == "ASK"
    assert "YOUR RELATIONSHIP WITH THIS COACH" not in fake.prompts[0]


@pytest.mark.parametrize("action", ["ASK", "REACT", "PASS"])
def test_reporter_actions_parse(action):
    texts = {
        "ASK": "Coach, how did the pass rush change in the second half?",
        "REACT": "To Sam's point, how did the line hold up?",
        "PASS": "",
    }
    text = texts[action]
    raw = json.dumps({"action": action, "text": text, "about": [], "tone": "light", "reply_to": None})
    result = press_agents.reporter_turn(beat(), make_state(), FakeLLM([raw]))
    assert result["action"] == action
    if action == "PASS":
        assert result["text"] == "" and result["reply_to"] is None


def _follow_up_state(**overrides):
    turns = [
        {"turn_id": 1, "speaker": "REPORTER", "agent_id": "beat1", "kind": "QUESTION",
         "text": "Coach, how did the line play?"},
        {"turn_id": 2, "speaker": "SUBJECT", "kind": "ANSWER", "text": "They competed.", "reply_to": 1},
    ]
    values = {"transcript": turns, "last_answer": turns[1], "last_asker": "beat1"}
    values.update(overrides)
    return make_state(**values)


def test_follow_up_defaults_to_the_last_answer_turn():
    raw = json.dumps({"action": "FOLLOW_UP", "text": "What did you see on that play?", "about": [],
                      "tone": "neutral", "reply_to": None})
    result = press_agents.reporter_turn(beat(), _follow_up_state(), FakeLLM([raw]))
    assert result["action"] == "FOLLOW_UP" and result["reply_to"] == 2


def test_follow_up_only_by_the_reporter_who_asked_then_dropped():
    raw = json.dumps({"action": "FOLLOW_UP", "text": "What did you see on that play?", "about": [],
                      "tone": "neutral", "reply_to": None})
    state = _follow_up_state(last_asker="other")
    result = press_agents.reporter_turn(beat(), state, FakeLLM([raw, raw]))
    assert result["action"] == "PASS" and result["dropped"] and result["source"] == "FALLBACK"
    assert "only the reporter who asked" in result["problem"]


def test_no_third_follow_up_in_a_row():
    raw = json.dumps({"action": "FOLLOW_UP", "text": "And what about the protection?", "about": [],
                      "tone": "neutral", "reply_to": None})
    state = _follow_up_state(follow_ups_in_a_row=2)
    result = press_agents.reporter_turn(beat(), state, FakeLLM([raw, raw]))
    assert result["action"] == "PASS" and result["dropped"]
    assert "follow-ups in a row" in result["problem"]


def test_reporter_bad_number_retried_once_with_the_problem():
    bad = json.dumps({"action": "ASK", "text": "Coach, Josh Allen threw 4 interceptions. Is he still your starter?",
                      "about": [{"type": "player", "id": 101}], "tone": "pointed", "reply_to": None})
    good = json.dumps({"action": "ASK", "text": "Coach, Josh Allen threw 1 interception. How do you fix it?",
                       "about": [{"type": "player", "id": 101}], "tone": "neutral", "reply_to": None})
    fake = FakeLLM([bad, good])
    result = press_agents.reporter_turn(beat(), make_state(), fake)
    assert result["action"] == "ASK" and result["source"] == "AGENT" and result["retries"] == 1
    assert "threw 1 interception" in result["text"]
    assert "4 interceptions" in fake.prompts[1]
    assert "recorded: 1" in fake.prompts[1]


def test_reporter_bad_twice_drops_the_hand():
    bad = json.dumps({"action": "ASK", "text": "Coach, Josh Allen threw 4 interceptions. Is he still your starter?",
                      "about": [], "tone": "neutral", "reply_to": None})
    result = press_agents.reporter_turn(beat(), make_state(), FakeLLM([bad, bad]))
    assert result["action"] == "PASS" and result["dropped"] and result["source"] == "FALLBACK"


def test_reporter_done_after_two_passes_without_a_model_call():
    fake = FakeLLM([ask_reply()])
    result = press_agents.reporter_turn(beat(), make_state(passes_in_a_row={"beat1": 2}), fake)
    assert result["action"] == "PASS" and result["source"] == "ENGINE"
    assert fake.prompts == []


def test_question_cap_stops_the_reporter_without_a_model_call():
    fake = FakeLLM([ask_reply()])
    result = press_agents.reporter_turn(beat(), make_state(questions_asked=press_agents.MAX_QUESTIONS), fake)
    assert result["action"] == "PASS" and result["source"] == "ENGINE" and fake.prompts == []


# ------------------------------------------------------------------------------------------------ coach turn
def test_coach_prompt_has_profile_facts_neutral_demeanor_and_guidance():
    fake = FakeLLM([json.dumps({"text": "We competed and the players deserve the credit.",
                                "mood": 4, "wants_to_end": False})])
    result = press_agents.coach_turn(SUBJECT, make_state(), fake, "ANSWER")
    prompt = fake.prompts[0]
    assert "Marcus Whitfield" in prompt and "Spread" in prompt and "7-1" in prompt
    assert press_agents.NEUTRAL_COACH_LINE in prompt
    assert "Refuse injury details" in prompt
    assert '"final"' in prompt
    assert result["source"] == "AGENT" and result["mood"] == 4 and result["wants_to_end"] is False


def test_coach_personality_view_replaces_the_neutral_line():
    from personality import core
    identity = core.Identity(universe_id="mu:test", role="coach", birth_key="coach:test")
    profile = core.initialize(identity, "generated", SEASON)
    subject = dict(SUBJECT, personality_profile=profile)
    fake = FakeLLM([json.dumps({"text": "We competed.", "mood": 3, "wants_to_end": False})])
    press_agents.coach_turn(subject, make_state(), fake, "OPENING")
    assert "YOUR PERSONALITY" in fake.prompts[0]
    assert press_agents.NEUTRAL_COACH_LINE not in fake.prompts[0]


def test_a_precomputed_personality_view_is_used():
    subject = dict(SUBJECT, personality_view={"status": "stored", "traits": ["QUOTABLE"],
                                              "sliders": {"temperament": 8, "sociability": 5,
                                                          "confidence": 5, "openness": 5,
                                                          "warmth": 5, "drive": 5}})
    fake = FakeLLM([json.dumps({"text": "We competed.", "mood": 3, "wants_to_end": False})])
    press_agents.coach_turn(subject, make_state(), fake, "CLOSING")
    assert "volatile" in fake.prompts[0]


def test_coach_bad_number_is_retried_once():
    bad = json.dumps({"text": "We ran for 200 yards.", "mood": 3, "wants_to_end": False})
    good = json.dumps({"text": "We ran for 110 yards.", "mood": 3, "wants_to_end": False})
    fake = FakeLLM([bad, good])
    result = press_agents.coach_turn(SUBJECT, make_state(), fake, "ANSWER")
    assert result["source"] == "AGENT" and result["retries"] == 1 and "110 yards" in result["text"]
    assert "200 yards" in fake.prompts[1] and "recorded: 110" in fake.prompts[1]


def test_coach_keeps_the_passing_sentences_after_two_bad_replies():
    bad = json.dumps({"text": "We ran for 200 yards.", "mood": 3, "wants_to_end": False})
    mixed = json.dumps({"text": "We ran for 200 yards. We competed.", "mood": 4, "wants_to_end": True})
    result = press_agents.coach_turn(SUBJECT, make_state(), FakeLLM([bad, mixed]), "ANSWER")
    assert result["source"] == "FALLBACK"
    assert result["text"] == "We competed."
    assert result["wants_to_end"] is True


def test_coach_falls_back_to_the_non_answer():
    bad = json.dumps({"text": "We ran for 200 yards.", "mood": 2, "wants_to_end": False})
    result = press_agents.coach_turn(SUBJECT, make_state(), FakeLLM([bad, bad]), "CLOSING")
    assert result["source"] == "FALLBACK" and result["text"] == press_agents.COACH_NON_ANSWER


# ------------------------------------------------------------------------------------------------- coach pick
def test_coach_pick_prompt_has_hands_history_and_no_question_text():
    hands = [dict(beat(), follow_up=False, text="SECRET-DRAFTED-QUESTION"),
             national()]
    state = make_state(skip_history={"pk-beat1": {"hands": 6, "picked": 1},
                                     "pk-nat1": {"hands": 4, "picked": 2}})
    fake = FakeLLM([json.dumps({"agent_id": "beat1", "reason": "The local beat opens."})])
    result = press_agents.coach_pick(SUBJECT, hands, state, fake)
    prompt = fake.prompts[0]
    assert "Dana Reed" in prompt and "Mike Sando" in prompt
    assert "beat reporter" in prompt and "national reporter" in prompt
    assert "hands up 6, picked 1" in prompt
    assert "SECRET-DRAFTED-QUESTION" not in prompt
    assert result == {"agent_id": "beat1", "reason": "The local beat opens.", "source": "AGENT", "retries": 0}


def test_coach_views_slot_is_left_out_when_none_and_shown_when_given():
    hands = [beat(), national()]
    fake = FakeLLM([json.dumps({"agent_id": "beat1", "reason": "Beat opens."})])
    press_agents.coach_pick(SUBJECT, hands, make_state(), fake)
    assert "YOUR OWN VIEWS OF THE REPORTERS" not in fake.prompts[0]

    views = {"pk-beat1": {"trust": "warm", "note": "always fair"}}
    fake = FakeLLM([json.dumps({"agent_id": "beat1", "reason": "Beat opens."})])
    press_agents.coach_pick(SUBJECT, hands, make_state(), fake, coach_views=views)
    assert "YOUR OWN VIEWS OF THE REPORTERS" in fake.prompts[0]
    assert "always fair" in fake.prompts[0]


def test_invalid_pick_retried_once_then_fallback():
    hands = [beat(), national()]
    state = make_state(picks={"beat1": 2, "nat1": 0})
    fake = FakeLLM([json.dumps({"agent_id": "nobody", "reason": "?"}),
                    json.dumps({"agent_id": "nobody", "reason": "?"})])
    result = press_agents.coach_pick(SUBJECT, hands, state, fake)
    assert result["source"] == "FALLBACK" and result["retries"] == 1
    assert result["agent_id"] == "nat1"          # nat1 was picked least (0), and no beat is tied at 0


def test_invalid_pick_then_a_valid_retry():
    hands = [beat(), national()]
    fake = FakeLLM([json.dumps({"agent_id": "nobody", "reason": "?"}),
                    json.dumps({"agent_id": "nat1", "reason": "A national followed up."})])
    result = press_agents.coach_pick(SUBJECT, hands, make_state(), fake)
    assert result["source"] == "AGENT" and result["agent_id"] == "nat1" and result["retries"] == 1


def test_fallback_pick_prefers_the_last_askers_follow_up():
    hands = [dict(beat("beat1"), follow_up=True), dict(national(), follow_up=False)]
    state = make_state(last_asker="beat1")
    result = press_agents.fallback_pick(hands, state)
    assert result["agent_id"] == "beat1" and "follow-up" in result["reason"]


def test_fallback_pick_least_picked_then_beats_before_nationals():
    hands = [beat("beat1"), beat("beat2"), national("nat1")]
    state = make_state(picks={"beat1": 2, "beat2": 0, "nat1": 0})
    result = press_agents.fallback_pick(hands, state)
    assert result["agent_id"] == "beat2"         # tied at 0 with nat1, but a beat wins


def test_fallback_pick_ties_are_seeded_and_deterministic():
    hands = [beat("beat1"), beat("beat2")]
    state = make_state(picks={"beat1": 1, "beat2": 1})
    first = press_agents.fallback_pick(hands, state)
    second = press_agents.fallback_pick(list(reversed(hands)), state)
    assert first["agent_id"] == second["agent_id"]


def test_fallback_pick_with_no_hands():
    assert press_agents.fallback_pick([], make_state())["agent_id"] is None


# --------------------------------------------------------------------------------------------------- patience
def _patience_sheet(outcome="win", margin=7, streak=("W", 3), hot_seat=None):
    sheet = make_sheet()
    team = sheet["teams"][0]
    team["streak"] = {"type": streak[0], "count": streak[1]}
    team["coach"]["hot_seat"] = hot_seat
    if outcome == "win":
        sheet["game"]["final"] = {"home": 24, "away": 24 - margin}
    elif outcome == "loss":
        sheet["game"]["final"] = {"home": 24 - margin, "away": 24}
    else:
        sheet["game"]["final"] = {"home": 20, "away": 20}
    return sheet


def test_patience_by_result():
    assert press_agents.patience_budget(SUBJECT, _patience_sheet("win")) == 15
    assert press_agents.patience_budget(SUBJECT, _patience_sheet("loss", margin=7)) == 14
    assert press_agents.patience_budget(SUBJECT, _patience_sheet("tie")) == 14
    assert press_agents.patience_budget(SUBJECT, _patience_sheet("loss", margin=17)) == 13


def test_patience_losing_streak_beyond_two_and_hot_seat():
    assert press_agents.patience_budget(SUBJECT, _patience_sheet("loss", margin=7, streak=("L", 4))) == 12
    hot = {"line": "Hot seat: score 0.81, retained", "fired": False}
    assert press_agents.patience_budget(SUBJECT, _patience_sheet("win", hot_seat=hot)) == 13


def test_patience_temperament_only_when_a_personality_exists():
    sheet = _patience_sheet("win")
    assert press_agents.patience_budget(SUBJECT, sheet) == 15
    assert press_agents.patience_budget(dict(SUBJECT, temperament=8), sheet) == 17
    assert press_agents.patience_budget(dict(SUBJECT, temperament=2), sheet) == 13


def test_patience_is_clamped():
    sheet = _patience_sheet("loss", margin=17, streak=("L", 12),
                            hot_seat={"line": "Hot seat: 0.95, retained", "fired": False})
    assert press_agents.patience_budget(dict(SUBJECT, temperament=0), sheet) == press_agents.MIN_PATIENCE
    assert press_agents.patience_budget(dict(SUBJECT, temperament=10), _patience_sheet("win")) \
        <= press_agents.MAX_PATIENCE


def test_question_cost():
    assert press_agents.question_cost("neutral") == 1
    assert press_agents.question_cost("light") == 1
    assert press_agents.question_cost("pointed") == 2
    assert press_agents.question_cost("neutral", repeated=True) == 2
    assert press_agents.question_cost("pointed", repeated=True) == 2


def test_end_conditions():
    assert press_agents.end_condition(make_state(patience={"budget": 14, "spent": 14})) == "PATIENCE"
    assert press_agents.end_condition(make_state(patience={"budget": 14, "spent": 11},
                                                 wants_to_end=True)) == "SUBJECT_ENDED"
    assert press_agents.end_condition(make_state(attendees=["a", "b"], done=["a", "b"])) == "RAN_OUT"
    assert press_agents.end_condition(make_state(questions_asked=press_agents.MAX_QUESTIONS,
                                                 patience={"budget": 14, "spent": 2})) == "CAP"
    assert press_agents.end_condition(make_state()) is None
    # a user-controlled subject has no patience budget, so patience never ends it
    assert press_agents.end_condition(make_state(patience=None, controller="USER")) is None


# --------------------------------------------------------------------------------------------- judgments
def make_conference(status="CLOSED", **overrides):
    turns = [
        {"turn_id": 1, "speaker": "REPORTER", "agent_id": "beat1", "kind": "QUESTION",
         "text": "Coach, Josh Allen threw 1 interception. How do you get him back on track?"},
        {"turn_id": 2, "speaker": "SUBJECT", "kind": "ANSWER",
         "text": "We'll coach him through it.", "reply_to": 1},
        {"turn_id": 3, "speaker": "REPORTER", "agent_id": "beat2", "kind": "QUESTION",
         "text": "How did the line hold up?"},
        {"turn_id": 4, "speaker": "SUBJECT", "kind": "NO_COMMENT", "text": "", "reply_to": 3},
    ]
    conference = {
        "conference_id": CONFERENCE_ID, "status": status, "season": SEASON, "week": WEEK,
        "subject": dict(SUBJECT), "team_key": "team:1", "sheet": make_sheet(),
        "turns": turns,
        "attendees": {
            "beat1": {"display_name": "Dana Reed", "hands": 4, "picked": 1},
            "beat2": {"display_name": "Sam Cole", "hands": 2, "picked": 2},
        },
        "hand_rounds": {"beat1": [1, 2, 3, 4], "beat2": [2, 3]},
        "skip_history": {"pk-beat1": {"hands": 6, "picked": 1}},
        "memory_blocks": {"beat1": "MY-PRESS-MEMORY"},
        "universe_id": "mu:test",
    }
    conference.update(overrides)
    return conference


def valid_judgment(turn_id=2, treatment_kind="recognition"):
    return {
        "exchanges": [{"turn_id": turn_id, "relationship_kind": "support", "outcome": "positive",
                       "significance": "routine", "reason": "He gave me a real answer."}],
        "treatment": {"treatment_kind": treatment_kind, "outcome": "positive",
                      "significance": "routine", "reason": "He picked me once."},
        "memory": "I asked about Josh Allen and the coach answered.",
    }


def test_judgment_prompt_holds_exchanges_treatment_and_history():
    fake = FakeLLM([json.dumps(valid_judgment())])
    result = press_agents.reporter_judgment(beat(), make_conference(), fake)
    prompt = fake.prompts[0]
    assert "MY-PRESS-MEMORY" in prompt
    assert "You asked" in prompt and "He answered" in prompt
    assert "the coach picked you 1 time" in prompt
    assert "hands up 6, picked 1" in prompt
    assert result["source"] == "AGENT" and result["exchanges"][0]["turn_id"] == 2


def test_declined_judgment_prompt_says_the_coach_did_not_show():
    fake = FakeLLM([json.dumps({
        "exchanges": [], "treatment": {"treatment_kind": "dismissal", "outcome": "negative",
                                       "significance": "meaningful", "reason": "He stood us up."},
        "memory": "The coach did not show."})])
    result = press_agents.reporter_judgment(beat(), make_conference(status="DECLINED"), fake)
    assert "did not take questions" in fake.prompts[0]
    assert result["treatment"]["treatment_kind"] == "dismissal"


def test_bad_judgment_json_retried_once_then_neutral():
    result = press_agents.reporter_judgment(beat(), make_conference(), FakeLLM(["not json", "still not json"]))
    assert result["source"] == "FALLBACK" and result["retries"] == 1
    assert result["exchanges"] == [] and result["treatment"] is None
    assert result["memory"]


def test_bad_judgment_retried_then_valid():
    fake = FakeLLM(["not json", json.dumps(valid_judgment())])
    result = press_agents.reporter_judgment(beat(), make_conference(), fake)
    assert result["source"] == "AGENT" and result["retries"] == 1
    assert result["exchanges"][0]["turn_id"] == 2


def test_judgment_foreign_turn_ids_are_dropped():
    raw = valid_judgment(turn_id=4)          # beat2's answer, not beat1's
    result = press_agents.validate_judgment(raw, beat(), make_conference())
    assert result["exchanges"] == []
    assert result["source"] == "AGENT"


def test_no_comment_can_never_be_positive():
    raw = {"exchanges": [{"turn_id": 4, "relationship_kind": "conflict", "outcome": "positive",
                          "significance": "routine", "reason": "He answered me."}],
           "treatment": None, "memory": "He said nothing."}
    result = press_agents.validate_judgment(raw, beat("beat2"), make_conference())
    assert result["exchanges"][0]["outcome"] == "mixed"


def test_recognition_needs_a_pick_and_dismissal_needs_a_skip():
    attendee = beat("beat3", display_name="Alex Kim")
    conference = make_conference(attendees={"beat3": {"display_name": "Alex Kim", "hands": 3, "picked": 0}})
    recognition = press_agents.validate_judgment(valid_judgment(treatment_kind="recognition"), attendee, conference)
    assert recognition["treatment"] is None
    dismissal = press_agents.validate_judgment(
        {**valid_judgment(treatment_kind="dismissal"),
         "treatment": {"treatment_kind": "dismissal", "outcome": "negative",
                       "significance": "routine", "reason": "He kept skipping me."}},
        attendee, conference)
    assert dismissal["treatment"]["treatment_kind"] == "dismissal"


def test_declined_presser_allows_only_dismissal_or_disrespect():
    conference = make_conference(status="DECLINED")
    recognition = press_agents.validate_judgment(valid_judgment(treatment_kind="recognition"),
                                                 beat(), conference)
    assert recognition["treatment"] is None
    dismissal = press_agents.validate_judgment(
        {**valid_judgment(), "treatment": {"treatment_kind": "dismissal", "outcome": "negative",
                                           "significance": "meaningful", "reason": "He did not show."}},
        beat(), conference)
    assert dismissal["treatment"]["treatment_kind"] == "dismissal"


def test_a_memory_with_an_invented_number_is_replaced():
    raw = dict(valid_judgment())
    raw["memory"] = "I asked about Josh Allen after he threw 4 interceptions in the win."
    result = press_agents.validate_judgment(raw, beat(), make_conference())
    assert "4 interceptions" not in result["memory"]
    assert result["memory"].startswith("I was at Coach")


def test_a_reason_over_thirty_words_drops_the_exchange():
    raw = dict(valid_judgment())
    raw["exchanges"] = [dict(raw["exchanges"][0], reason=" ".join(["word"] * 31))]
    result = press_agents.validate_judgment(raw, beat(), make_conference())
    assert result["exchanges"] == []


def test_invalid_enum_becomes_the_neutral_appraisal():
    raw = dict(valid_judgment())
    raw["exchanges"] = [dict(raw["exchanges"][0], relationship_kind="romance")]
    result = press_agents.validate_judgment(raw, beat(), make_conference())
    assert result["source"] == "FALLBACK" and result["exchanges"] == []


# ------------------------------------------------------------------------------------------------ events
def test_judgment_events_build_relationships_envelopes():
    conference = make_conference()
    appraisal = {
        "exchanges": [{"turn_id": 2, "relationship_kind": "support", "outcome": "positive",
                       "significance": "routine", "reason": "He gave me a real answer."}],
        "treatment": {"treatment_kind": "recognition", "outcome": "positive",
                      "significance": "routine", "reason": "He picked me once."},
        "memory": "He answered.",
    }
    events = press_agents.judgment_events(conference, beat(), appraisal, "mu:test")
    assert [event["kind"] for event in events] == ["meaningful_interaction", "treatment"]

    exchange, treatment = events
    assert exchange["source_event_id"] == f"press:{CONFERENCE_ID}:turn:2"
    assert treatment["source_event_id"] == f"press:{CONFERENCE_ID}:treatment:beat1"
    assert exchange["source_kind"] == "press_conference"
    assert exchange["occurred_at"] == {"season": SEASON, "tick": WEEK}
    assert exchange["source_refs"] == [{"namespace": "press_turns", "key": "2"}]
    assert treatment["source_refs"] == [{"namespace": "press_conferences", "key": str(CONFERENCE_ID)}]

    # one-way: the reporter is the subject, the coach is the target and the actor
    assert exchange["subject"]["role"] == "writer" and exchange["subject"]["persona_id"] == "persona-beat1"
    assert exchange["target"]["role"] == "coach" and exchange["actor"] == exchange["target"]
    assert exchange["subject"]["persona_id"] != exchange["target"]["persona_id"]
    assert exchange["attribution"]["responsible"] == [exchange["target"]]

    assert exchange["payload"]["opportunity_ref"] == f"press:{CONFERENCE_ID}:turn:2"
    assert exchange["payload"]["reason"] == "He gave me a real answer."
    assert treatment["payload"]["team_key"] == "team:1"
    assert treatment["payload"]["treatment_kind"] == "recognition"
    assert treatment["payload"]["outcome_refs"] == [
        f"press_hands:{CONFERENCE_ID}:1", f"press_hands:{CONFERENCE_ID}:2",
        f"press_hands:{CONFERENCE_ID}:3", f"press_hands:{CONFERENCE_ID}:4",
    ]

    # the envelopes satisfy the canonical relationships contract
    registry = {"team:1": {"abbr": "BUF"}}
    for event in events:
        assert rev.validate_event(event, team_registry=registry)["kind"] == event["kind"]


def test_declined_judgment_uses_the_one_per_occurrence_source_id():
    conference = make_conference(status="DECLINED")
    appraisal = {"exchanges": [], "treatment": {"treatment_kind": "dismissal", "outcome": "negative",
                                               "significance": "meaningful", "reason": "He did not show."},
                 "memory": "He did not show."}
    events = press_agents.judgment_events(conference, beat(), appraisal, "mu:test")
    assert len(events) == 1
    assert events[0]["source_event_id"] == f"press:{CONFERENCE_ID}:declined"
    assert events[0]["kind"] == "treatment"
    assert rev.validate_event(events[0], team_registry={"team:1": {}})


def test_events_need_persona_ids():
    conference = make_conference()
    attendee = beat("beat1", persona_id=None, person_key=None)
    # falls back to the agent id, so an event is still built
    events = press_agents.judgment_events(conference, attendee, {"exchanges": [], "treatment": None}, "mu:test")
    assert events == []
    conference["subject"] = dict(SUBJECT, persona_id=None)
    events = press_agents.judgment_events(conference, beat(), {
        "exchanges": [{"turn_id": 2, "relationship_kind": "support", "outcome": "positive",
                       "significance": "routine", "reason": "fine"}], "treatment": None}, "mu:test")
    assert events == []


# -------------------------------------------------------------------------------------------- skip history
class FakeHistoryCursor:
    def __init__(self, exists=True, row=(6, 2, 3)):
        self.exists = exists
        self.row = row
        self._rows = []

    def execute(self, sql, params=None):
        if "to_regclass" in sql:
            self._rows = [(self.exists,)]
        else:
            self._rows = [self.row]

    def fetchone(self):
        return self._rows[0] if self._rows else None


def test_skip_history_counts_hands_and_picks():
    result = press_agents.skip_history(FakeHistoryCursor(row=(6, 2, 3)), "pk-beat1", 9, SEASON)
    assert result == {"hands": 6, "picked": 2, "skipped": 4, "pressers": 3}


def test_skip_history_without_the_tables_is_zero():
    assert press_agents.skip_history(FakeHistoryCursor(exists=False), "pk-beat1", 9, SEASON) == \
        {"hands": 0, "picked": 0, "skipped": 0, "pressers": 0}
    assert press_agents.skip_history(None, "pk-beat1", 9, SEASON) == \
        {"hands": 0, "picked": 0, "skipped": 0, "pressers": 0}


# ---------------------------------------------------------------------------------- U1 corpus targets
def test_constants_are_set_from_the_u1_corpus():
    path = os.path.join(os.path.dirname(press_agents.__file__), "config", "press_corpus_stats.json")
    with open(path, encoding="utf-8") as handle:
        stats = json.load(handle)
    per_presser = stats["overall"]["questions_per_presser"]
    assert press_agents.BASE_PATIENCE > per_presser["median"]
    assert press_agents.MAX_QUESTIONS >= per_presser["p90"]
    assert press_agents.MAX_QUESTIONS <= per_presser["max"]
    assert 0.05 <= stats["overall"]["follow_up_rate"] <= 0.20
    assert press_agents.MAX_FOLLOW_UPS_IN_A_ROW == 2
    assert press_agents.MIN_PATIENCE <= press_agents.BASE_PATIENCE <= press_agents.MAX_PATIENCE


def test_only_a_coach_under_real_pressure_is_on_the_hot_seat():
    """`coach_hot_seat` writes a row for all 32 teams each offseason with fired=false, so 'a retained row exists'
    flagged every returning coach and docked him patience."""
    retained_champ = {"score": -1.0, "fired": False, "line": "Hot seat: score -1.00, retained"}
    under_pressure = {"score": 0.6, "fired": False, "line": "Hot seat: score 0.60, retained"}
    assert press_agents._hot_seat_for({"hot_seat": retained_champ}, {}) is False
    assert press_agents._hot_seat_for({"hot_seat": under_pressure}, {}) is True
    assert press_agents._hot_seat_for({"hot_seat": {"score": 0.9, "fired": True}}, {}) is False
    assert press_agents._hot_seat_for({"hot_seat": True}, {}) is True            # a caller that already decided
    assert press_agents._hot_seat_for({}, {}) is False
    assert press_agents._hot_seat_for({}, {"coach": {"hot_seat": retained_champ}}) is False
