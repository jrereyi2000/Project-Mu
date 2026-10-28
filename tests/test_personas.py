"""Writer personas (newsroom/personas.py, newsroom/config/agent_personas.json, persona_traits.json)."""

import json
import re
import unittest

from newsroom import personas as P

BANNED = re.compile(r"\b(always|constantly|must|never|every single)\b|\bend with\b", re.I)


class PersonaDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.personas = P.load_personas()
        cls.traits = P.load_traits()

    def test_sliders_use_the_budget(self):
        for k, p in self.personas.items():
            s = p["sliders"]
            self.assertEqual(set(s), set(P.SLIDERS), k)
            self.assertTrue(all(0 <= v <= 10 for v in s.values()), k)
            self.assertEqual(sum(s.values()), P.SLIDER_BUDGET, k)

    def test_traits_exist_and_mix_legally(self):
        for k, p in self.personas.items():
            self.assertTrue(3 <= len(p["traits"]) <= 5, k)
            self.assertEqual(len(set(p["traits"])), len(p["traits"]), k)
            cats = {}
            for t in p["traits"]:
                self.assertIn(t, self.traits, f"{k}: unknown trait {t}")
                cats[self.traits[t]["category"]] = cats.get(self.traits[t]["category"], 0) + 1
                self.assertFalse(set(self.traits[t]["conflicts"]) & set(p["traits"]), f"{k}: {t} conflicts")
            self.assertLessEqual(max(cats.values()), 2, f"{k}: more than two traits from one category")

    def test_trait_conflicts_are_symmetric(self):
        for t, e in self.traits.items():
            self.assertTrue(e["prompt"].startswith("tends to"), t)
            for c in e["conflicts"]:
                self.assertIn(t, self.traits[c]["conflicts"], f"{t} conflicts with {c} but not back")
            self.assertLessEqual(set(e.get("when") or ()), P.ALL_TAGS, t)

    def test_views_have_known_tags_and_a_general_fallback(self):
        for k, p in self.personas.items():
            self.assertGreaterEqual(len(p["views"]), 3, k)
            self.assertTrue(any(not v["when"] for v in p["views"]), f"{k}: no general view")
            self.assertTrue(any(v["when"] for v in p["views"]), f"{k}: no situational view")
            for v in p["views"]:
                self.assertLessEqual(set(v["when"]), P.ALL_TAGS, f"{k}: {v}")

    def test_signatures_are_a_pool(self):
        for k, p in self.personas.items():
            self.assertTrue(2 <= len(p["signatures"]) <= 5, k)
            for s in p["signatures"]:
                if s.get("detect"):
                    re.compile(s["detect"])

    def test_no_rule_wording(self):
        """Character, not orders: no 'always / never / must / constantly / end with' in persona text."""
        for k, p in self.personas.items():
            text = json.dumps({f: p.get(f) for f in ("voice", "views", "signatures", "want", "fear", "formats")})
            self.assertIsNone(BANNED.search(text), f"{k}: {BANNED.search(text) and BANNED.search(text).group(0)}")

    def test_every_persona_has_a_format(self):
        for k, p in self.personas.items():
            self.assertTrue(set(p["formats"]) and set(p["formats"]) <= {"ARTICLE", "POST"}, k)


def g(week, home, away, hs, as_):
    return (week, home, away, hs, as_)


class SignalTests(unittest.TestCase):
    def test_lucky_close_winner(self):
        # Team 1 wins five one-score games, loses one big: record outruns differential.
        games = [g(1, 1, 2, 21, 20), g(2, 1, 3, 17, 14), g(3, 4, 1, 10, 41), g(4, 1, 5, 24, 23),
                 g(5, 6, 1, 20, 23), g(6, 1, 7, 13, 10)]
        tags = P.signals_from_games(games, 1)
        self.assertIn("RECORD_OUTRUNS_DIFF", tags)
        self.assertIn("CLOSE_GAMES", tags)
        self.assertIn("CONTENDER", tags)
        self.assertIn("WIN_STREAK", tags)

    def test_unlucky_blowout_loser(self):
        games = [g(1, 1, 2, 40, 10), g(2, 1, 3, 20, 23), g(3, 1, 4, 17, 20), g(4, 1, 5, 21, 24), g(5, 1, 6, 0, 30)]
        tags = P.signals_from_games(games, 1)
        self.assertIn("RECORD_TRAILS_DIFF", tags)
        self.assertIn("LOSING_STREAK", tags)
        self.assertIn("BLOWOUT", tags)
        self.assertIn("STRUGGLING", tags)

    def test_upset(self):
        # Team 1 is 3-0, team 2 is 0-3; team 2 wins in week 4.
        games = [g(1, 1, 3, 30, 10), g(2, 1, 4, 30, 10), g(3, 1, 5, 30, 10),
                 g(1, 2, 6, 10, 30), g(2, 2, 7, 10, 30), g(3, 2, 8, 10, 30), g(4, 2, 1, 20, 17)]
        self.assertIn("UPSET", P.signals_from_games(games, 1))
        self.assertIn("UPSET", P.signals_from_games(games, 2))

    def test_league_level_needs_a_standout(self):
        games = [g(w, 1, 2, 20, 17) for w in range(1, 5)]
        self.assertNotIn("RECORD_OUTRUNS_DIFF", P.signals_from_games(games))
        self.assertEqual(P.signals_from_games([], 1), set())

    def test_topic_tags(self):
        self.assertEqual(P.article_tags("QUARTERBACK_DUEL_TAPE_BREAKDOWN"), {"QB", "SCHEME", "GAME_REVIEW"})
        self.assertIn("OFFSEASON", P.article_tags("OFFSEASON_MOCK_DRAFT"))
        self.assertIn("DRAFT", P.article_tags("OFFSEASON_MOCK_DRAFT"))
        self.assertEqual(P.post_tags("BAD_GAME", "QB"), {"GAME_REVIEW", "PLAYER", "BAD_GAME", "QB"})
        self.assertEqual(P.phase_tags(20), {"PLAYOFFS"})
        self.assertEqual(P.phase_tags(24), {"OFFSEASON"})
        self.assertEqual(P.phase_tags(5), set())


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.barnwell = P.load_personas()["BARNWELL"]

    def test_situational_views_follow_the_tags(self):
        out = P.render_persona(self.barnwell, "ARTICLE", {"RECORD_OUTRUNS_DIFF", "CLOSE_GAMES"}, seed="x")
        self.assertIn("one-score wins", out)
        out = P.render_persona(self.barnwell, "ARTICLE", {"DRAFT"}, seed="x")
        self.assertNotIn("one-score wins", out)

    def test_view_count_is_capped_and_topped_up(self):
        rng = __import__("random").Random(1)
        views = [{"text": f"v{i}", "when": ["TRADE"]} for i in range(5)] + [{"text": "g", "when": []}]
        self.assertEqual(len(P.select_views(views, {"TRADE"}, rng)), P.MAX_VIEWS)
        self.assertEqual(P.select_views(views, set(), rng), ["g"])

    def test_recent_signature_is_held_back(self):
        fresh = P.render_persona(self.barnwell, "ARTICLE", set(), seed="x")
        self.assertIn("SIGNATURE MOVES", fresh)
        self.assertIn("Regression Warning", fresh.split("SIGNATURE MOVES")[1].split("\n\n")[0])
        used = P.render_persona(self.barnwell, "ARTICLE", set(), seed="x",
                                recent_texts=["## Regression Warning: the Jets"])
        offered = used.split("SIGNATURE MOVES")[1].split("\n\n")[0]
        self.assertNotIn("Regression Warning", offered)
        self.assertIn("USED IN YOUR LAST FEW PIECES", used)

    def test_recent_headlines_become_angles_to_avoid(self):
        out = P.render_persona(self.barnwell, "ARTICLE", set(), seed="x", recent_headlines=["The Lions Are Lucky"])
        self.assertIn("The Lions Are Lucky", out)

    def test_format_habits_and_background(self):
        art = P.render_persona(self.barnwell, "ARTICLE", set(), seed="x")
        post = P.render_persona(self.barnwell, "POST", set(), seed="x")
        self.assertIn("HOW YOU WRITE A COLUMN", art)
        self.assertIn("HOW YOU WRITE A POST", post)
        self.assertIn("do not state it", art)
        self.assertIn("By nature you are", art)

    def test_deterministic_and_string_passthrough(self):
        a = P.render_persona(self.barnwell, "ARTICLE", {"TRADE"}, seed="s")
        self.assertEqual(a, P.render_persona(self.barnwell, "ARTICLE", {"TRADE"}, seed="s"))
        self.assertEqual(P.render_persona("plain", "ARTICLE"), "plain")

    def test_database_trouble_still_renders(self):
        class BadCursor:
            def execute(self, *a, **k):
                raise RuntimeError("no db")
        out = P.build_persona_block(BadCursor(), self.barnwell, agent_id="a", season=2036, week=5,
                                    fmt="ARTICLE", article_type="TRADE_GRADES", team_id=1)
        self.assertIn("Bill Barnwell", out)
        self.assertIn("surplus value", out)       # the TRADE view, from the article type alone

    def test_recent_work_falls_back_to_archetype_for_old_articles(self):
        class Cur:
            def execute(self, sql, params):
                self.params = params
            def fetchall(self):
                return [("H1", "## Regression Warning"), ("H2", "text")]
        cur = Cur()
        texts, heads = P.recent_work(cur, "Bill_Barnwell", 2036, 12, "ARTICLE", "BARNWELL")
        self.assertEqual(cur.params[4:7], ("Bill_Barnwell", "BARNWELL", "BARNWELL"))
        self.assertEqual(heads, ["H1", "H2"])
        P.recent_work(cur, "Beat_1", 2036, 12, "ARTICLE", "LOCAL_BEAT_REPORTER")
        self.assertEqual(cur.params[5:7], (None, None))       # shared archetype: own pieces only

    def test_post_prompt_renders_a_persona_dict(self):
        from newsroom.social_writer import build_post_prompt
        a = {"kind": "BAD_GAME", "subject": {"type": "player"}, "facts": {"position": "QB"}, "week": 5}
        acct = {"agent_id": "x", "archetype": "ORLOVSKY", "handle": "@x", "display_name": "X"}
        system, _ = build_post_prompt(a, acct, P.load_personas()["ORLOVSKY"])
        self.assertIn("Dan Orlovsky", system)
        self.assertIn("misread shell", system)


if __name__ == "__main__":
    unittest.main()
