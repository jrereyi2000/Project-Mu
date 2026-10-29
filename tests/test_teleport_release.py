# @AUDIT-STATUS: OBSOLETE_FRESH_SAVE (v2-teleport)
# @REPLACED-BY: NONE (fresh 2034 save, no teleport; AUDIT_LEDGER.md §3b)
"""pipeline/teleport.py --release: leftover donors in the report and the edits (unit, on a made-up snapshot), and the
full run on the sample save: teleport, apply players + releases to a copy, read back (integration)."""
import json
import os
import subprocess
import tempfile
import unittest

import psycopg
import pytest

from pipeline import teleport as tp
from tests.test_teleport import DOC, QB_RATINGS, READER, SAMPLE_SAVE, WRITER, _sample_ready, mu


def player(pgid, team, status="Signed", position="QB", archetype="QB_StrongArm", last="Smith"):
    return {"pgid": pgid, "team_index": team, "status": status, "position": position, "archetype": archetype,
            "overall": 75, "ratings": dict(QB_RATINGS), "first_name": "Al", "last_name": last,
            "appearance": {"commentary_id": 0}}


SNAPSHOT = {"teams": [{"team_index": 0, "short_name": "CAR"}, {"team_index": 1, "short_name": "CHI"}],
            "players": [player(1, 0), player(2, 0, "PracticeSquad", last="Jones"), player(3, 1),
                        player(4, 32, "FreeAgent")]}


class ReleaseEditsTest(unittest.TestCase):
    def test_leftovers_are_reported_and_released_only_on_request(self):
        edits, report = tp.teleport(SNAPSHOT, [mu()], DOC)
        self.assertNotIn("release", edits)
        # CAR keeps one donor for its one Project Mu player; CHI has none, so its donor goes too. Free agents aren't donors.
        self.assertEqual(sorted(d["pgid"] for d in report["leftover_donors"]), [2, 3])
        self.assertEqual((report["donors_left_over"], report["released"]), (2, 0))
        left = {d["pgid"]: d for d in report["leftover_donors"]}
        self.assertEqual((left[2]["team"], left[2]["status"], left[2]["name"]), ("CAR", "PracticeSquad", "Al Jones"))

        edits, report = tp.teleport(SNAPSHOT, [mu()], DOC, release=True)
        self.assertEqual(sorted(edits["release"]), [2, 3])
        self.assertEqual(report["released"], 2)
        self.assertFalse(set(edits["release"]) & {e["pgid"] for e in edits["players"]})


@pytest.mark.integration
@unittest.skipUnless(_sample_ready(), "needs the sample save, node and `npm install` in pipeline/save_reader")
class SampleReleaseTest(unittest.TestCase):
    """Teleports madden_test's league_state season with --release and applies everything to a copy."""

    def test_rosters_hold_exactly_the_project_mu_players(self):
        snapshot = json.loads(subprocess.run(["node", READER, SAMPLE_SAVE], capture_output=True, text=True,
                                             check=True, timeout=300).stdout)
        with psycopg.connect(tp.DSN) as conn:
            cur = conn.cursor()
            cur.execute("SELECT current_season FROM league_state WHERE id = 1")
            roster = tp.project_mu_roster(cur, cur.fetchone()[0])
        edits, report = tp.teleport(snapshot, roster, DOC, release=True)
        self.assertEqual(len(edits["release"]), report["donors_left_over"])
        with tempfile.TemporaryDirectory() as d:
            path, out = os.path.join(d, "edits.json"), os.path.join(d, "CAREER-TELEPORTED")
            with open(path, "w") as fh:
                json.dump(edits, fh)
            subprocess.run(["node", WRITER, SAMPLE_SAVE, path, "--out", out], check=True, capture_output=True, timeout=600)
            after = json.loads(subprocess.run(["node", READER, out], capture_output=True, text=True, check=True,
                                              timeout=300).stdout)
        before = {p["pgid"]: p for p in snapshot["players"]}
        want = {}
        for pr in report["pairs"]:
            want.setdefault(before[pr["donor_pgid"]]["team_index"], set()).add(pr["donor_pgid"])
        have = {}
        for p in after["players"]:
            if p["status"] in tp.DONOR_STATUSES and p["team_index"] < 32:
                have.setdefault(p["team_index"], set()).add(p["pgid"])
        self.assertEqual(have, want)
        now = {p["pgid"]: p for p in after["players"]}
        for g in edits["release"]:
            self.assertEqual((now[g]["status"], now[g]["team_index"], now[g]["contract"]), ("FreeAgent", 32, None))
        self.assertEqual(after["games"], snapshot["games"])
        self.assertEqual(after["season"], snapshot["season"])
        touched = set(edits["release"]) | {e["pgid"] for e in edits["players"]}
        self.assertTrue(all(before[k] == now[k] for k in set(before) - touched))
