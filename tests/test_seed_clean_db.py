"""
tests/test_seed_clean_db.py: Unit tests for db/seed_clean_db.py.
Verifies the writer/social accounts a clean database gets (newsroom/config/agent_accounts.json) cover every team
and archetype the newsroom picks bylines from, and that the seed writes them and drops the old universe's
'BOOK' record rows.
"""
import json
import unittest
from unittest import mock

from db import seed_clean_db as seed

TEAM_ABBRS = {t[2] for t in seed.TEAMS_DATA}


def load_accounts():
    with open(seed.AGENT_ACCOUNTS_PATH) as f:
        return json.load(f)["accounts"]


class AgentAccountsFileTest(unittest.TestCase):
    def setUp(self):
        self.accounts = load_accounts()

    def test_ids_unique(self):
        ids = [a["agent_id"] for a in self.accounts]
        self.assertEqual(len(ids), len(set(ids)))

    def test_teams_known(self):
        self.assertTrue({a["team"] for a in self.accounts if a["team"]} <= TEAM_ABBRS)

    def test_every_team_has_beat_reporters_and_fans(self):
        for arch in ("LOCAL_BEAT_REPORTER", "DIE_HARD_FAN"):
            teams = {a["team"] for a in self.accounts if a["archetype"] == arch}
            self.assertEqual(teams, TEAM_ABBRS, arch)

    def test_archetypes_have_personas(self):
        with open(seed.REPO_ROOT / "newsroom" / "config" / "agent_personas.json") as f:
            archetypes = set(json.load(f)["archetypes"])
        self.assertTrue({a["archetype"] for a in self.accounts} <= archetypes)

    def test_no_former_players_and_kiper_present(self):
        archetypes = {a["archetype"] for a in self.accounts}
        self.assertNotIn("FORMER_PLAYER", archetypes)
        self.assertIn("MEL_KIPER", archetypes)

    def test_article_writers_have_accounts(self):
        import re
        with open(seed.REPO_ROOT / "newsroom" / "config" / "article_types.json") as f:
            text = f.read()
        with open(seed.REPO_ROOT / "newsroom" / "config" / "agent_personas.json") as f:
            archetypes = set(json.load(f)["archetypes"])
        named = {w for w in re.findall(r'"([A-Z][A-Z_0-9]+)"', text) if w in archetypes}
        self.assertTrue(named <= {a["archetype"] for a in self.accounts}, named)

    def test_identity_only(self):
        for a in self.accounts:
            self.assertEqual(set(a), {"agent_id", "archetype", "team", "handle", "display_name", "bio",
                                      "avatar_color", "persistent_memory"})
            self.assertTrue(a["handle"] and a["display_name"] and a["persistent_memory"], a["agent_id"])


class SeedTest(unittest.TestCase):
    def test_seed_agent_accounts_maps_teams(self):
        cur = mock.MagicMock()
        team_ids = {abbr: i for i, abbr in enumerate(sorted(TEAM_ABBRS), start=1)}
        n = seed.seed_agent_accounts(cur, team_ids)
        self.assertEqual(n, len(load_accounts()))
        args = [c.args[1] for c in cur.execute.call_args_list]
        beat = next(a for a in args if a[1] == "LOCAL_BEAT_REPORTER")
        self.assertIn(beat[2], team_ids.values())
        national = next(a for a in args if a[1] == "SCHEFTER_TWEET")
        self.assertIsNone(national[2])

    def test_seed_records_drops_book_rows(self):
        cur = mock.MagicMock()
        with mock.patch.object(seed, "execute_sql_file"):
            seed.seed_records(cur)
        sql = [c.args[0] for c in cur.execute.call_args_list]
        self.assertIn("DELETE FROM franchise_records WHERE source <> 'NFL';", sql)
        self.assertIn("DELETE FROM league_record_entries WHERE source <> 'NFL';", sql)


if __name__ == "__main__":
    unittest.main()
