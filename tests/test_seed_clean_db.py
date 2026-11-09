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
            self.assertEqual(set(a), {"agent_id", "person_key", "archetype", "team", "handle", "display_name", "bio",
                                      "avatar_color", "persistent_memory"})
            self.assertTrue(a["handle"] and a["display_name"] and a["persistent_memory"], a["agent_id"])

    def test_every_account_has_a_person_key_and_skip_shares_one(self):
        keys = {a["agent_id"]: a["person_key"] for a in self.accounts}
        self.assertTrue(all(keys.values()))
        self.assertEqual(keys["Emmanuel_Acho"], "Emmanuel_Acho")          # default: the agent_id
        self.assertEqual(keys["Skip_Bayless_National"], "Skip_Bayless")   # one person, two accounts
        self.assertEqual(keys["Skip_Bayless"], "Skip_Bayless")

    def test_d7_national_accounts_exist(self):
        by_arch = {a["archetype"]: a for a in self.accounts if a["archetype"] in ("AGGREGATOR", "STATS_DESK")}
        self.assertEqual(set(by_arch), {"AGGREGATOR", "STATS_DESK"})
        for arch, a in by_arch.items():
            self.assertIsNone(a["team"], arch)
            self.assertTrue(a["handle"].startswith("@") and a["display_name"] and a["bio"] and a["persistent_memory"], arch)
        self.assertEqual(by_arch["AGGREGATOR"]["agent_id"], "Gridline_Now")
        self.assertEqual(by_arch["STATS_DESK"]["agent_id"], "Stat_Margin")


class SeedTest(unittest.TestCase):
    def test_seed_agent_accounts_maps_teams(self):
        cur = mock.MagicMock()
        team_ids = {abbr: i for i, abbr in enumerate(sorted(TEAM_ABBRS), start=1)}
        n = seed.seed_agent_accounts(cur, team_ids)
        self.assertEqual(n, len(load_accounts()))
        args = [c.args[1] for c in cur.execute.call_args_list]
        beat = next(a for a in args if a[1] == "LOCAL_BEAT_REPORTER")
        self.assertIn(beat[2], team_ids.values())
        self.assertEqual(beat[-1], beat[0])                       # person_key defaults to agent_id
        national = next(a for a in args if a[1] == "SCHEFTER_TWEET")
        self.assertIsNone(national[2])
        skip = [a for a in args if a[1] == "SKIP_BAYLESS"] + [a for a in args if a[1] == "SKIP_TWEET"]
        self.assertEqual({a[-1] for a in skip}, {"Skip_Bayless"})

    def test_run_clean_seed_writes_writer_relationships(self):
        conn = mock.MagicMock()
        conn.__enter__.return_value = conn
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = (1,)                          # teams table already exists
        with mock.patch.object(seed.psycopg, "connect", return_value=conn), \
                mock.patch.object(seed, "seed_teams", return_value={"IND": 1}), \
                mock.patch.object(seed, "seed_league_state"), \
                mock.patch.object(seed, "seed_gm_team_settings"), \
                mock.patch("newsroom.personas.seed_writers_to_db", return_value=38), \
                mock.patch.object(seed, "seed_agent_accounts", return_value=201), \
                mock.patch("newsroom.relationships.seed_writer_relationships", return_value=7) as rel:
            report = seed.run_clean_seed("dsn", season=2026, week=1, skip_records=True, skip_coaches=True,
                                         skip_gms=True, skip_napper=True)
        rel.assert_called_once_with(cur, 2026)
        self.assertEqual(report["writer_relationships"], 7)

    def test_seed_records_drops_book_rows(self):
        cur = mock.MagicMock()
        with mock.patch.object(seed, "execute_sql_file"):
            seed.seed_records(cur)
        sql = [c.args[0] for c in cur.execute.call_args_list]
        self.assertIn("DELETE FROM franchise_records WHERE source <> 'NFL';", sql)
        self.assertIn("DELETE FROM league_record_entries WHERE source <> 'NFL';", sql)


if __name__ == "__main__":
    unittest.main()
