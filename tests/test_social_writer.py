"""Unit tests for the Agent Wire post writer (newsroom/social_writer.py). No database or API calls.

Run: python3 -m unittest tests.test_social_writer
"""
import unittest

from newsroom.social_writer import build_post_prompt, clean_post, cost_usd, kind_key


def assignment(kind="REACTION", subject_type="game", facts=None):
    return {"kind": kind, "subject": {"type": subject_type, "id": 1, "label": "x"},
            "facts": facts if facts is not None else {"game": "A 21 @ B 24", "your_team": "City B", "your_team_result": "won"}}


ACCOUNT = {"agent_id": "@fan", "archetype": "DIE_HARD_FAN", "persistent_memory": "I am Tre. Chiefs fan since birth.",
           "handle": "@ChiefsKingdom_Tre97", "display_name": "Tre"}


class SocialRulesTests(unittest.TestCase):
    def test_contract_and_trade_grounding(self):
        """JR's R12: real moves and contract figures from FACTS; a trade is done only if executed; rumors stay rumors."""
        from newsroom.social_writer import SOCIAL_RULES
        self.assertIn("Contract figures, signings, releases and trades only from FACTS", SOCIAL_RULES)
        self.assertIn("a trade is done only if FACTS says it was executed", SOCIAL_RULES)
        self.assertIn("a rumor stays a rumor", SOCIAL_RULES)
        self.assertIn("never invent a contract figure", SOCIAL_RULES)
        self.assertNotIn("never state a trade or signing as fact", SOCIAL_RULES)


class CleanPostTests(unittest.TestCase):
    def test_strips_quotes_labels_and_markdown(self):
        self.assertEqual(clean_post('Tweet: "**What** a game"')["text"], "What a game")

    def test_keeps_two_hashtags(self):
        out = clean_post("Huge win #Chiefs #ChiefsKingdom #NFL #Week9")
        self.assertEqual(out["hashtags"], ["Chiefs", "ChiefsKingdom"])
        self.assertNotIn("#NFL", out["text"])
        self.assertTrue(out["problems"])

    def test_removes_links(self):
        out = clean_post("Read this https://example.com/x now")
        self.assertNotIn("http", out["text"])
        self.assertIn("contained a link (removed)", out["problems"])

    def test_flags_long_posts_and_ratings(self):
        self.assertTrue(any("over 280" in p for p in clean_post("x" * 300)["problems"]))
        self.assertIn("mentions ratings or Madden", clean_post("A 95 overall QB, straight from Madden")["problems"])

    def test_clean_post_has_no_problems(self):
        self.assertEqual(clean_post("Arrowhead was ROCKING. 24-21, we take it. 🏹")["problems"], [])


class PromptTests(unittest.TestCase):
    def test_prompt_carries_persona_identity_rules_and_facts(self):
        system, prompt = build_post_prompt(assignment(), ACCOUNT, "You are a die-hard homer.", "Chiefs")
        self.assertIn("You are a die-hard homer.", system)
        self.assertIn("@ChiefsKingdom_Tre97", system)
        self.assertIn("Your team is the Chiefs.", system)
        self.assertIn("at most 280 characters", system)
        self.assertIn('"game": "A 21 @ B 24"', prompt)
        self.assertIn("it won", prompt)

    def test_reply_prompt_includes_parent(self):
        _, prompt = build_post_prompt(assignment("REPLY", "post"), ACCOUNT, "p", None,
                                      parent={"handle": "@stephenasmith", "display_name": "Stephen A. Smith", "text": "BLASPHEMOUS!"})
        self.assertIn("BLASPHEMOUS!", prompt)
        self.assertIn("@stephenasmith", prompt)

    def test_slant_and_new_kinds(self):
        a = assignment("COMPARISON", "position", {"position": "QB", "top_five_this_season": []})
        a["slant_team"] = "Kansas City Chiefs"
        legend = {"agent_id": "Former_Patrick_Mahomes", "archetype": "FORMER_PLAYER", "handle": "@PatrickMahomes",
                  "display_name": "Patrick Mahomes", "persistent_memory": "I am Patrick Mahomes. I played QB for the Kansas City Chiefs."}
        system, prompt = build_post_prompt(a, legend, "You are a retired star.", "Chiefs")
        self.assertIn("YOUR OLD TEAM: you played for the Kansas City Chiefs", prompt)
        self.assertIn("player debate", prompt)
        self.assertIn("never claim to still play", system)
        self.assertNotIn("Your team is the Chiefs.", system)          # only fans and beat reporters have a team
        _, prompt = build_post_prompt(assignment("BAD_GAME", "player", {"player": "X"}), ACCOUNT, "p", "Chiefs")
        self.assertIn("bad game by his own standard", prompt)

    def test_team_story_prompt(self):
        a = assignment("TEAM_STORY", "team", {"team": "Carolina Panthers (16-0)", "why_people_are_talking": ["Perfect at 16-0"]})
        _, prompt = build_post_prompt(a, ACCOUNT, "p", None)
        self.assertIn("The league is talking about this team", prompt)
        self.assertIn("Perfect at 16-0", prompt)

    def test_kind_keys(self):
        self.assertEqual(kind_key(assignment("PREVIEW", "fantasy")), "FANTASY_PREVIEW")
        self.assertEqual(kind_key(assignment("MEME", "draft")), "DRAFT")
        self.assertEqual(kind_key(assignment("MEME", "game")), "MEME")

    def test_cost(self):
        self.assertAlmostEqual(cost_usd("gemini-3.8-flash", {"input": 1_000_000, "cached": 0, "output": 0, "thinking": 1_000_000}), 4.5)


if __name__ == "__main__":
    unittest.main()


class MoneyGuardTests(unittest.TestCase):
    """A dollar figure in a post must be in its facts packet (team management step 5, phase 5)."""

    def test_money_guard(self):
        from newsroom.social_writer import MONEY_PROBLEM, allowed_money
        allowed = allowed_money({"move": "Ravens re-signed LT Rich Hanson: 4 years, $118M total",
                                 "cap": "Colts: -$29.5M in cap space"})
        self.assertEqual(clean_post("Breaking: Hanson, 4 years, $118M.", allowed=allowed)["problems"], [])
        self.assertEqual(clean_post("Hanson gets $118 million to stay", allowed=allowed)["problems"], [])
        self.assertEqual(clean_post("Colts sit $29.5M over the cap", allowed=allowed)["problems"], [])
        problems = clean_post("Hanson: $120M with $60M guaranteed", allowed=allowed)["problems"]
        self.assertEqual(problems, [f"{MONEY_PROBLEM}: $120M, $60M"])
        self.assertTrue(clean_post("$1,500,000 bonus", allowed=allowed_money({}))["problems"])
        self.assertEqual(clean_post("no money at all", allowed=allowed_money({}))["problems"], [])
        self.assertEqual(clean_post("$120M", allowed=None)["problems"], [])   # guard off without facts

    def test_money_problem_blocks_publishing(self):
        from newsroom.social_publish import publishable
        from newsroom.social_writer import MONEY_PROBLEM
        self.assertFalse(publishable({"text": "x", "problems": [f"{MONEY_PROBLEM}: $5M"]}))
        self.assertTrue(publishable({"text": "x", "problems": []}))

    def test_move_kinds_have_instructions(self):
        from newsroom.social_writer import KIND_INSTRUCTIONS
        for kind in ("MOVE_NEWS", "BEAT_MOVE", "MOVE_REACTION", "CAP_TAKE"):
            self.assertIn("FACTS", KIND_INSTRUCTIONS[kind])
        system, prompt = build_post_prompt({"kind": "MOVE_NEWS", "subject": {"type": "player", "id": 1, "label": "x"},
                                            "facts": {"move": "Week 4 · SIGNED · ..."}}, ACCOUNT, "persona")
        self.assertIn("POST TYPE: MOVE_NEWS", prompt)


# ------------------------------------------------------------------ Agent Wire voice plan (U2, §2.3 and §2.7) ---
import json
import re
from types import SimpleNamespace

from newsroom import social_writer as SW

NEW_KINDS = ("PICKS", "HOT_TAKE", "AWARD_RACE", "POWER_TAKE", "QUESTION", "SEASON_CALL", "LIVE_MOMENT",
             "QUARTER_UPDATE", "TRADE_IDEA", "DRAFT_POSITION", "REPLY_QUOTE")
OLD_KINDS = ("REACTION", "PERFORMANCE", "PLAYER", "BAD_GAME", "COMPARISON", "TEAM_STORY", "BEAT", "BEAT_INJURY",
             "INJURY_SHOCK", "STANDINGS", "PREVIEW", "FANTASY_PREVIEW", "MEME", "DRAFT", "DRAFT_BOARD", "MOVE_NEWS",
             "BEAT_MOVE", "MOVE_REACTION", "CAP_TAKE", "TRADE_NEWS", "TRADE_REACTION", "TRADE_RUMOR", "SHARE", "REPLY")
# A menu: three or more alternatives joined by commas and a final "or" ("elation, disbelief, or relief").
MENU = re.compile(r"[\w'-]+(?: [\w'-]+){0,3}, [\w'-]+(?: [\w'-]+){0,3},? or [\w'-]+", re.I)

GAMES = [{"game_id": 501, "game": "BUF @ KC", "away": {"id": 4, "abbr": "BUF", "name": "Buffalo Bills"},
          "home": {"id": 16, "abbr": "KC", "name": "Kansas City Chiefs"}},
         {"game_id": 502, "away": "DAL", "away_team_id": 9, "home": "PHI", "home_team_id": 26}]


def voice_assignment(kind="PICKS", **extra):
    a = {"kind": kind, "subject": {"type": "team", "id": 16, "label": "Kansas City Chiefs"}, "week": 6,
         "facts": {"player": "Jerry Rayford", "player_id": 77, "team": "Kansas City Chiefs", "team_id": 16},
         "subjects": [{"type": "player", "id": 77, "name": "Jerry Rayford"},
                      {"type": "coach", "id": 3, "name": "Andy Reid"},
                      {"type": "team", "id": 9, "name": "Dallas Cowboys", "abbr": "DAL"}],
         "pick_games": GAMES}
    a.update(extra)
    return a


class InstructionTests(unittest.TestCase):
    def test_every_kind_has_an_instruction(self):
        for k in OLD_KINDS + NEW_KINDS:
            self.assertIn(k, SW.KIND_INSTRUCTIONS, k)
            self.assertGreater(len(SW.KIND_INSTRUCTIONS[k]), 40, k)

    def test_no_menu_of_moods(self):
        """Situation + intent, no option menus. Factual guards ("Never add a term, a dollar figure or a team") are
        prohibitions, not moods, so sentences with "never" are left out of the check."""
        for k, text in SW.KIND_INSTRUCTIONS.items():
            text = " ".join(s for s in re.split(r"(?<=[.:;])\s+", text) if not re.search(r"\bnever\b", s, re.I))
            self.assertIsNone(MENU.search(text), f"{k}: {MENU.search(text) and MENU.search(text).group(0)}")
        self.assertIsNone(MENU.search(SW.SOCIAL_RULES.split("Every number")[0]))
        self.assertTrue(MENU.search("react with elation, disbelief, or sheer relief"))   # the regex catches the old style

    def test_rules_state_the_measured_shape(self):
        self.assertIn("under 25 words", SW.SOCIAL_RULES)
        self.assertIn("Closing punctuation is optional", SW.SOCIAL_RULES)
        self.assertIn("at most one number unless your POST STYLE allows more", SW.SOCIAL_RULES)
        self.assertIn("Never invent an injury", SW.SOCIAL_RULES)
        self.assertIn("Never mention player or team ratings", SW.SOCIAL_RULES)
        self.assertNotIn("Speak like a real human", SW.SOCIAL_RULES)
        self.assertNotIn("CADENCE & SYNTAX VARIETY", SW.SOCIAL_RULES)

    def test_reaction_leaves_out_the_score(self):
        self.assertIn("leave them out", SW.KIND_INSTRUCTIONS["REACTION"])

    def test_quote_reply_kind(self):
        a = voice_assignment("REPLY", quote=True)
        self.assertEqual(kind_key(a), "REPLY_QUOTE")
        _, prompt = build_post_prompt(a, ACCOUNT, "p", parent={"handle": "@x", "display_name": "X", "text": "hot take"})
        self.assertIn("POST YOU ARE QUOTING (X, @x)", prompt)
        self.assertEqual(kind_key(voice_assignment("REPLY")), "REPLY")


class PostStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from newsroom.personas import load_personas_json
        cls.archetypes = load_personas_json()

    def test_every_archetype_has_a_valid_post_style(self):
        for k, p in self.archetypes.items():
            self.assertIn("post_style", p, k)
            self.assertTrue(SW.validate_post_style(p["post_style"], k), k)
            for shape, w in (p.get("post_shapes") or {}).items():
                self.assertIn(shape, SW.POST_SHAPES, k)
                self.assertGreaterEqual(w, 0, k)

    def test_swearing_only_for_fans(self):
        swear = {k for k, p in self.archetypes.items() if p["post_style"]["profanity"] != "none"}
        self.assertEqual(swear, {"DIE_HARD_FAN", "BANDWAGON_FAN"})
        with self.assertRaises(ValueError):
            SW.validate_post_style(dict(self.archetypes["SCHEFTER"]["post_style"], profanity="mild_rare"), "SCHEFTER")
        with self.assertRaises(ValueError):
            SW.validate_post_style(dict(self.archetypes["SCHEFTER"]["post_style"], length=[30, 10]))

    def test_new_national_archetypes_validate(self):
        from newsroom import personas as P
        for k in ("AGGREGATOR", "STATS_DESK"):
            p = self.archetypes[k]
            self.assertTrue(P.validate_slider_budget(p["sliders"]))
            self.assertTrue(P.validate_persona_traits(p["traits"]))
            self.assertIn("POST", p["formats"])
        self.assertGreaterEqual(self.archetypes["STATS_DESK"]["post_style"]["numbers"][1], 2)
        self.assertGreaterEqual(self.archetypes["AGGREGATOR"]["post_style"]["hashtags"][0], 1)

    def test_post_style_is_rendered_into_the_prompt(self):
        system, _ = build_post_prompt(assignment(), ACCOUNT, "You are a die-hard homer.", "Chiefs")   # from the archetype
        self.assertIn("POST STYLE", system)
        self.assertIn("usually 3-20 words", system)
        self.assertIn("Never a slur", system)
        pundit = {"agent_id": "s", "archetype": "SCHEFTER_TWEET", "handle": "@s", "display_name": "S"}
        system, _ = build_post_prompt(assignment("MOVE_NEWS", "player", {"move": "x"}), pundit, "p")
        self.assertIn("clean, no swearing", system)
        system, _ = build_post_prompt(assignment(), ACCOUNT, "p", post_style={"length": [1, 4]})
        self.assertIn("usually 1-4 words", system)


class ShapeTests(unittest.TestCase):
    def test_every_shape_has_a_line_and_kind_lists_leave_room(self):
        self.assertEqual(set(SW.SHAPE_LINES), set(SW.POST_SHAPES))
        for kind, shapes in SW.KIND_SHAPES.items():
            self.assertGreaterEqual(len(shapes), SW.RECENT_SHAPES + 1, kind)
            self.assertLessEqual(set(shapes), set(SW.POST_SHAPES), kind)

    def test_never_repeats_the_last_three(self):
        for kind in ("REACTION", "QUESTION", "PICKS", "LIVE_MOMENT", "MOVE_NEWS"):
            for arch in ("DIE_HARD_FAN", "SCHEFTER", "STATS_DESK", "NONE"):
                recent = []
                for i in range(40):
                    s = SW.pick_shape({"agent_id": "a", "archetype": arch}, kind, recent, seed=f"w{i}")
                    self.assertNotIn(s, recent[-3:], (kind, arch, i))
                    self.assertIn(s, SW.KIND_SHAPES.get(kind, SW.POST_SHAPES))
                    recent.append(s)

    def test_deterministic_and_weighted(self):
        acct = {"agent_id": "a", "archetype": "DIE_HARD_FAN"}
        self.assertEqual(SW.pick_shape(acct, "REACTION", [], "x"), SW.pick_shape(acct, "REACTION", [], "x"))
        only = {"agent_id": "a", "post_shapes": {s: 0 for s in SW.POST_SHAPES} | {"sarcasm": 1}}
        self.assertEqual({SW.pick_shape(only, "REACTION", [], str(i)) for i in range(20)}, {"sarcasm"})
        self.assertNotEqual(SW.pick_shape(only, "REACTION", ["sarcasm"], "x"), "sarcasm")

    def test_shape_biases_focus_and_contract_in_prompt(self):
        a = voice_assignment("HOT_TAKE", focus=["Chiefs are 5-0 with a +12 point differential"])
        system, prompt = build_post_prompt(a, ACCOUNT, "p", relationship_block="You can't stand the Cowboys.",
                                           shape="contrast")
        self.assertIn("YOUR BIASES", system)
        self.assertIn("You can't stand the Cowboys.", system)
        self.assertIn("SHAPE: contrast", prompt)
        self.assertIn("FOCUS (what this post is about):\n- Chiefs are 5-0", prompt)
        self.assertIn("FACTS (background, use at most one)", prompt)
        self.assertIn("PICK_GAMES", prompt)
        self.assertIn("- BUF @ KC", prompt)
        self.assertIn("- DAL @ PHI", prompt)
        self.assertIn('"text"', prompt.split("OUTPUT:")[1])
        system, prompt = build_post_prompt(assignment(), ACCOUNT, "p")          # old callers: no biases, no shape
        self.assertNotIn("YOUR BIASES", system)
        self.assertNotIn("SHAPE:", prompt)
        self.assertIn("FACTS:\n", prompt)


class ParsePostJsonTests(unittest.TestCase):
    def raw(self, **kw):
        return json.dumps({"text": "Bills by a field goal", "about": [], "picks": [], "calls": [], **kw})

    def test_resolves_about_and_drops_unknown(self):
        out = SW.parse_post_json(self.raw(about=[
            {"type": "player", "name": "Rayford", "lean": 1}, {"type": "coach", "name": "Andy Reid", "lean": -1},
            {"type": "team", "name": "Cowboys", "lean": "-2"}, {"type": "player", "name": "Nobody Known", "lean": 1}]),
            voice_assignment())
        self.assertTrue(out["structured"])
        self.assertEqual([(x["type"], x["id"], x["lean"]) for x in out["about"]],
                         [("player", 77, 1), ("coach", 3, -1), ("team", 9, -1)])

    def test_picks_are_validated_against_pick_games(self):
        out = SW.parse_post_json(self.raw(picks=[
            {"game": "BUF @ KC", "winner": "Bills", "score": "24-27", "confidence": "upset"},
            {"game": "Cowboys at Eagles", "winner": "PHI", "score": None, "confidence": "sure thing"},
            {"game": "NYJ @ MIA", "winner": "Jets", "score": None, "confidence": "lock"},       # not on the slate
            {"game": "BUF @ KC", "winner": "Cowboys", "score": None, "confidence": "lock"}]),    # winner not in game
            voice_assignment())
        self.assertEqual(len(out["picks"]), 2)
        p1, p2 = out["picks"]
        self.assertEqual((p1["game_id"], p1["winner_team_id"], p1["pick_type"], p1["score"], p1["confidence"]),
                         (501, 4, "SCORE", "27-24", "upset"))
        self.assertEqual((p1["away_team_id"], p1["home_team_id"]), (4, 16))
        self.assertEqual((p2["game_id"], p2["winner_team_id"], p2["pick_type"], p2["confidence"]),
                         (502, 26, "WINNER", "lean"))
        self.assertEqual(SW.parse_post_json(self.raw(picks=[{"game": "BUF @ KC", "winner": "KC"}]),
                                            voice_assignment(pick_games=[]))["picks"], [])

    def test_calls_need_a_known_type_and_subject(self):
        out = SW.parse_post_json(self.raw(calls=[
            {"call_type": "mvp", "subject": "Jerry Rayford", "detail": "runaway"},
            {"call_type": "SUPER_BOWL", "subject": "Kansas City", "detail": ""},
            {"call_type": "COACH_FIRED", "subject": "Andy Reid"},
            {"call_type": "MOST_IMPROVED", "subject": "Jerry Rayford"},
            {"call_type": "ROY", "subject": "Some Rookie"}]), voice_assignment())
        self.assertEqual([(c["call_type"], c["subject_type"], c["subject_id"]) for c in out["calls"]],
                         [("MVP", "player", 77), ("SUPER_BOWL", "team", 16), ("COACH_FIRED", "coach", 3)])

    def test_falls_back_to_plain_text(self):
        out = SW.parse_post_json("bills by 3 lol", voice_assignment())
        self.assertEqual(out, {"text": "bills by 3 lol", "about": [], "picks": [], "calls": [], "structured": False})
        fenced = "```json\n" + self.raw(text="KC all day") + "\n```"
        self.assertEqual(SW.parse_post_json(fenced, None)["text"], "KC all day")
        self.assertFalse(SW.parse_post_json('{"post": "no text key"}', None)["structured"])

    def test_clean_post_unwraps_a_structured_reply(self):
        self.assertEqual(clean_post(self.raw(text="**KC** by 3"))["text"], "KC by 3")


class TooSimilarTests(unittest.TestCase):
    def test_thresholds(self):
        a = "cant believe we blew that lead in the fourth again"
        self.assertTrue(SW.too_similar(a, [a]))
        self.assertTrue(SW.too_similar("cant believe we blew that lead in the fourth AGAIN!!", [a]))
        self.assertFalse(SW.too_similar("defense showed up for once, give the coordinator a raise", [a]))
        near = "cant believe we blew that lead in the third quarter this time"
        overlap = len(SW._trigrams(SW._words(a)) & SW._trigrams(SW._words(near))) / \
            len(SW._trigrams(SW._words(a)) | SW._trigrams(SW._words(near)))
        self.assertEqual(SW.too_similar(near, [a]), overlap >= 0.55)
        self.assertTrue(SW.too_similar(near, [a], threshold=overlap))
        self.assertFalse(SW.too_similar("", [a]))
        self.assertFalse(SW.too_similar(a, []))

    def test_same_kind_opener(self):
        self.assertTrue(SW.too_similar("What a game by the defense", [], same_kind=["what a game, KC"]))
        self.assertFalse(SW.too_similar("What a game by the defense", ["what a game, KC"]))


class _FakeGemini:
    def __init__(self, replies):
        self.replies = list(replies)
        self.contents = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.contents.append(contents)
        return SimpleNamespace(text=self.replies.pop(0), usage_metadata=None)


class WritePostTests(unittest.TestCase):
    def run_write(self, replies, **kw):
        from unittest import mock
        client = _FakeGemini(replies)
        with mock.patch("engines.llm_config.make_content_config", lambda model, system_instruction, temperature: (None, model)), \
                mock.patch("engines.llm_provider.resolve_provider", lambda **k: "gemini"):
            return SW.write_post(client, "gemini-3.8-flash", "sys", "prompt", **kw), client

    def test_returns_parsed_fields(self):
        raw = json.dumps({"text": "Bills by 3", "about": [{"type": "team", "name": "Cowboys", "lean": -1}],
                          "picks": [{"game": "BUF @ KC", "winner": "BUF", "score": None, "confidence": "lean"}],
                          "calls": [{"call_type": "MVP", "subject": "Rayford", "detail": ""}]})
        out, client = self.run_write([raw], assignment=voice_assignment())
        self.assertEqual(out["text"], "Bills by 3")
        self.assertEqual(out["calls"], 1)                         # LLM calls, as before
        self.assertEqual(out["picks"][0]["winner_team_id"], 4)
        self.assertEqual(out["season_calls"][0]["subject_id"], 77)
        self.assertEqual(out["about"][0]["id"], 9)
        self.assertTrue(out["structured"])

    def test_rewrites_once_when_too_similar(self):
        first = json.dumps({"text": "cant believe we blew that lead again", "about": [], "picks": [], "calls": []})
        second = json.dumps({"text": "fourth quarter defense is a crime scene", "about": [], "picks": [], "calls": []})
        out, client = self.run_write([first, second], assignment=voice_assignment(),
                                     corpus=["cant believe we blew that lead again"])
        self.assertEqual(out["calls"], 2)
        self.assertEqual(out["text"], "fourth quarter defense is a crime scene")
        self.assertTrue(out["problems"][0].startswith("rewritten once for repetition"))
        self.assertIn("too much like another post", str(client.contents[1]))
        out, _ = self.run_write([first])                          # no corpus: no repetition rewrite
        self.assertEqual(out["calls"], 1)

    def test_plain_text_still_works(self):
        out, _ = self.run_write(["Arrowhead was ROCKING"])
        self.assertEqual((out["text"], out["structured"], out["picks"]), ("Arrowhead was ROCKING", False, []))
