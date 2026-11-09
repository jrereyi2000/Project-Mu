"""The shared trade-talk line writer and style contract (engines/gm_writer.py). Synthetic only: no database."""
import json

from engines import gm_writer as gw

SHEET = {
    "season": 2026, "week": 5, "window": "IN_SEASON", "trigger": "NEED",
    "teams": {
        "a": {"team_id": 1, "abbr": "ATL", "team": "Atlanta Falcons", "nickname": "Falcons", "record": "5-3",
              "direction": "CONTEND", "needs": ["edge rusher"], "cap_before": "$9M", "cap_after": "$12M",
              "gm": "Beth Novak", "voice": "plain", "coach": "A Coach", "urgent": None},
        "b": {"team_id": 2, "abbr": "BUF", "team": "Buffalo Bills", "nickname": "Bills", "record": "4-4",
              "direction": "RETOOL", "needs": ["receiver"], "cap_before": "$4M", "cap_after": "$7M",
              "gm": "Silas Tremblay", "voice": "plain", "coach": "B Coach", "urgent": None},
    },
    "assets": [{"kind": "player", "name": "Julius Hobbs", "pos": "EDGE", "group": "EDGE", "ovr": 82,
                "age": 25, "dev": "Star", "cap_hit": "$9M", "years_left": 2, "from": "ATL", "to": "BUF"}],
}

CARD = ("REGISTER: Calls players by last name and gets to the point.\n"
        'HOW HE SOUNDS: "Send the kid over and let him hunt."\n'
        "TICS: says 'look' before he argues")

SCENE = {"week": 5, "deadline_week": 8, "who_called": "the Buffalo GM", "mood": "CLOSE",
         "stakes": "a division race", "relationship": "same conference"}
MOVE = {"action": "COUNTER", "posture": "HOLD", "gets": ["Julius Hobbs"],
        "gives": ["a 2037 fourth-round pick"]}
HISTORY = [
    {"team": "ATL", "action": "OFFER", "message": "Start with the kid and a pick and we can talk."},
    {"team": "BUF", "action": "COUNTER", "message": "The pick's too rich for me this early."},
    {"team": "ATL", "action": "COUNTER", "message": "Then meet me in the middle and we'll shake."},
]
CLEAN = "Hobbs is a problem off the edge. Send him over and let him hunt."


def answer(text):
    return json.dumps({"message": text})


def model(*replies, seen=None):
    """A fake llm answering the given replies in order; records the prompts it saw."""
    calls = list(replies)

    def llm(prompt):
        if seen is not None:
            seen.append(prompt)
        return calls.pop(0) if calls else answer(CLEAN)

    return llm


# ------------------------------------------------------------------ style ----
def test_a_clean_line_passes():
    ok, why = gw.check_style(CLEAN, [])
    assert ok and why is None


def test_banned_filler_fails():
    ok, why = gw.check_style("We care about efficiency above all.", [])
    assert not ok and "efficiency" in why


def test_every_banned_phrase_is_caught():
    for phrase in ("asset value", "framework", "process", "leverage", "value proposition",
                   "market efficiency", "floor-raising"):
        ok, _ = gw.check_style(f"That is our {phrase} this week.", [])
        assert not ok, phrase


def test_a_repeated_five_word_run_fails():
    prior = ["I watched every snap he took last season and he is a problem."]
    ok, why = gw.check_style("Look, I watched every snap he took. He can play.", prior)
    assert not ok and "repeats" in why


def test_a_sixty_word_line_fails():
    text = " ".join(["word"] * 60)
    ok, why = gw.check_style(text, [])
    assert not ok and "45" in why


def test_more_than_one_ellipsis_fails():
    ok, why = gw.check_style("Well... look... we can talk about it.", [])
    assert not ok and "ellipsis" in why


def test_one_ellipsis_is_allowed():
    ok, _ = gw.check_style("Look... I can move on the pick.", [])
    assert ok


def test_restating_a_previous_line_verbatim_fails():
    ok, why = gw.check_style("Send him over.", ["Send him over."])
    assert not ok and "verbatim" in why


def test_prior_history_dicts_are_read_by_message():
    prior = [{"team": "BUF", "message": "I watched every snap he took last season and he is a problem."}]
    ok, _ = gw.check_style("Look, I watched every snap he took. He can play.", prior)
    assert not ok


def test_a_repeated_run_against_a_template_line_is_allowed():
    prior = [{"team": "BUF", "source": "TEMPLATE",
              "message": "I watched every snap he took last season and he is a problem."}]
    ok, _ = gw.check_style("Look, I watched every snap he took. He can play.", prior)
    assert ok


def test_a_repeated_run_against_a_model_line_still_fails():
    prior = [{"team": "BUF", "source": "LLM",
              "message": "I watched every snap he took last season and he is a problem."}]
    ok, why = gw.check_style("Look, I watched every snap he took. He can play.", prior)
    assert not ok and "repeats" in why


def test_a_speaker_may_repeat_his_own_tic():
    prior = [{"team": "ATL", "source": "LLM",
              "message": "I watched every snap he took last season and he is a problem."}]
    ok, _ = gw.check_style("Look, I watched every snap he took. He can play.", prior, speaker="ATL")
    assert ok


def test_a_repeated_run_at_the_other_gm_still_fails_when_the_speaker_is_known():
    prior = [{"team": "BUF", "source": "LLM",
              "message": "I watched every snap he took last season and he is a problem."}]
    ok, why = gw.check_style("Look, I watched every snap he took. He can play.", prior, speaker="ATL")
    assert not ok and "repeats" in why


def test_a_repeated_run_against_jr_note_is_allowed():
    prior = [{"source": "USER", "message": "I watched every snap he took last season and he is a problem."}]
    ok, _ = gw.check_style("Look, I watched every snap he took. He can play.", prior, speaker="ATL")
    assert ok


# ------------------------------------------------------------------ write ----
def test_a_good_line_is_kept_and_the_prompt_carries_the_inputs():
    seen = []
    text, src = gw.write_line(model(answer(CLEAN), seen=seen), card_text=CARD, sheet=SHEET, history=HISTORY,
                               move=MOVE, scene=SCENE, fallback="TEMPLATE LINE")
    assert (text, src) == (CLEAN, "LLM")
    prompt = seen[0]
    assert CARD in prompt                                                     # the rendered card
    assert "week 5" in prompt and "the deadline is week 8" in prompt          # the scene
    assert "the Buffalo GM" in prompt and "a division race" in prompt
    assert "COUNTER" in prompt and "Julius Hobbs" in prompt and "2037 fourth" in prompt   # the fixed move
    assert "FACT SHEET" in prompt and "Julius Hobbs" in prompt                # the sheet JSON
    for h in HISTORY[-3:]:
        assert h["message"] in prompt                                         # the last three lines verbatim
    assert "efficiency" in prompt and "ellipsis" in prompt                     # the style rules


def test_the_retry_prompt_carries_the_reason():
    seen = []
    bad = answer("Our efficiency is why this works for us.")
    text, src = gw.write_line(model(bad, answer(CLEAN), seen=seen), card_text=CARD, sheet=SHEET,
                              history=HISTORY, move=MOVE, scene=SCENE, fallback="TEMPLATE LINE")
    assert (text, src) == (CLEAN, "LLM")
    assert len(seen) == 2
    assert "rejected" in seen[1] and "efficiency" in seen[1]


def test_a_second_failure_returns_the_fallback():
    seen = []
    bad = answer("Our efficiency is why this works for us.")
    text, src = gw.write_line(model(bad, bad, seen=seen), card_text=CARD, sheet=SHEET, history=HISTORY,
                              move=MOVE, scene=SCENE, fallback="TEMPLATE LINE")
    assert (text, src) == ("TEMPLATE LINE", "TEMPLATE")
    assert len(seen) == 2


def test_an_invented_fact_retries_then_falls_back():
    seen = []
    bad = answer("Give me 99 firsts and he is yours.")                         # a number not on the sheet
    text, src = gw.write_line(model(bad, bad, seen=seen), card_text=CARD, sheet=SHEET, history=HISTORY,
                              move=MOVE, scene=SCENE, fallback="TEMPLATE LINE")
    assert (text, src) == ("TEMPLATE LINE", "TEMPLATE")
    assert "rejected" in seen[1] and "sheet" in seen[1]


def test_no_llm_returns_the_fallback_immediately():
    assert gw.write_line(None, card_text=CARD, sheet=SHEET, history=HISTORY, move=MOVE, scene=SCENE,
                         fallback="TEMPLATE LINE") == ("TEMPLATE LINE", "TEMPLATE")


def test_timeout_reaches_call_with_timeout(monkeypatch):
    seen = {}

    def fake_timeout(llm, prompt, seconds):
        seen.update(seconds=seconds, prompt=prompt)
        return answer(CLEAN)

    from engines import gm_talks as gt
    monkeypatch.setattr(gt, "call_with_timeout", fake_timeout)
    text, src = gw.write_line(object(), card_text=CARD, sheet=SHEET, history=HISTORY, move=MOVE,
                              scene=SCENE, fallback="TEMPLATE LINE", timeout=8)
    assert (text, src) == (CLEAN, "LLM")
    assert seen["seconds"] == 8 and "FACT SHEET" in seen["prompt"]


def test_an_unreadable_answer_falls_back():
    text, src = gw.write_line(model("no json here", "still not json"), card_text=CARD, sheet=SHEET,
                              history=HISTORY, move=MOVE, scene=SCENE, fallback="TEMPLATE LINE")
    assert (text, src) == ("TEMPLATE LINE", "TEMPLATE")


def test_the_module_does_not_import_gm_voices_or_gm_talks_at_import_time():
    import subprocess
    import sys
    code = ("import engines.gm_writer, sys; "
            "bad = [m for m in ('engines.gm_voices', 'engines.gm_talks') if m in sys.modules]; "
            "raise SystemExit(1 if bad else 0)")
    assert subprocess.run([sys.executable, "-c", code], cwd=str(__import__("pathlib").Path(__file__).parents[1])).returncode == 0
