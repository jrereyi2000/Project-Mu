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
