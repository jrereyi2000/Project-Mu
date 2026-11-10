"""Unit tests for writer relationships (newsroom/relationships.py). No database required.

Run: pytest -m "not integration" tests/test_relationships.py
"""
import json
import unittest

from newsroom import relationships as rel

TRAITS = {"SKIP_BAYLESS": ["HOT_TAKER", "CONTRARIAN"], "SKIP_TWEET": ["HOT_TAKER", "CONTRARIAN"],
          "BARNWELL": ["ANALYTICS_BELIEVER", "MEASURED"], "OCHOCINCO": ["FILM_JUNKIE"], "COWHERD": ["FRONT_RUNNER"]}
SLANT = {"OCHOCINCO": "CIN"}


def league():
    teams = {1: {"abbr": "BUF", "division": "AFC East"}, 2: {"abbr": "MIA", "division": "AFC East"},
             3: {"abbr": "NYJ", "division": "AFC East"}, 4: {"abbr": "NE", "division": "AFC East"},
             5: {"abbr": "CIN", "division": "AFC North"}, 6: {"abbr": "PIT", "division": "AFC North"}}
    players = [{"id": 100 * t + j, "team_id": t, "ovr": 99 - 10 * j - t, "pop": float(j)} for t in teams for j in range(4)]
    return {"teams": teams, "records": {1: (13, 4), 2: (9, 8), 3: (4, 13), 4: (7, 10), 5: (11, 6), 6: (8, 9)},
            "players": players, "coaches": {t: 900 + t for t in teams},
            "losses": [{"season": 2035, "week": 3, "winner": 2, "loser": 1},
                       {"season": 2035, "week": 15, "winner": 4, "loser": 1}]}


def plan(accounts, seed="rel"):
    return rel.plan_writer_relationships(accounts, league(), seed, TRAITS, SLANT)


def acc(agent_id, archetype, team_id=None, person_key=None):
    return {"agent_id": agent_id, "archetype": archetype, "team_id": team_id, "person_key": person_key or agent_id}


class SeedingTests(unittest.TestCase):
    def test_deterministic_per_seed(self):
        accounts = [acc("Skip_Bayless_National", "SKIP_BAYLESS"), acc("bill", "BARNWELL"), acc("beat", "LOCAL_BEAT_REPORTER", 1)]
        self.assertEqual(plan(accounts), plan(accounts))
        self.assertNotEqual(plan(accounts), plan(accounts, seed="other"))

    def test_die_hard_fan(self):
        rows = plan([acc("fan", "DIE_HARD_FAN", 1)])
        own = [r for r in rows if "OWN_TEAM" in r["tags"]]
        rival = [r for r in rows if "RIVAL" in r["tags"]]
        stars = [r for r in rows if "FAVOURITE" in r["tags"]]
        self.assertEqual([(r["target_id"], r["trust"]) for r in own], [("1", 0.8)])
        # The most recent division loss (Patriots, week 15) sets the rival.
        self.assertEqual([(r["target_id"], r["trust"]) for r in rival], [("4", -0.6)])
        self.assertTrue(1 <= len(stars) <= 2)
        self.assertTrue(all(r["target_type"] == "player" and int(r["target_id"]) // 100 == 1 and r["trust"] == 0.5
                            for r in stars))

    def test_fan_rival_without_history_is_a_division_rival(self):
        lg = league()
        lg["losses"] = []
        rows = rel.plan_writer_relationships([acc("fan", "DIE_HARD_FAN", 5)], lg, "rel", TRAITS, SLANT)
        self.assertEqual([r["target_id"] for r in rows if "RIVAL" in r["tags"]], ["6"])

    def test_bandwagon_backs_last_seasons_best_team(self):
        rows = plan([acc("bw", "BANDWAGON_FAN")])
        self.assertEqual([(r["target_type"], r["target_id"], r["trust"]) for r in rows], [("team", "1", 0.6)])
        self.assertTrue(all(r["trust"] >= 0 for r in rows))

    def test_beat_reporter(self):
        rows = plan([acc("beat", "LOCAL_BEAT_REPORTER", 2)])
        self.assertEqual(rows[0]["target_type"], "team")
        self.assertEqual((rows[0]["target_id"], rows[0]["trust"], rows[0]["tags"]), ("2", 0.3, ["OWN_TEAM"]))
        others = rows[1:]
        self.assertIn(("coach", "902"), {(r["target_type"], r["target_id"]) for r in others})
        players = [r for r in others if r["target_type"] == "player"]
        self.assertTrue(2 <= len(players) <= 3)
        self.assertTrue(all(-0.3 <= r["trust"] <= 0.6 for r in others))

    def test_some_beat_reporter_doubts_a_local_star(self):
        rows = plan([acc(f"beat{i}", "LOCAL_BEAT_REPORTER", 1 + i % 6) for i in range(40)])
        self.assertTrue(any(r["trust"] < 0 and r["target_type"] != "team" for r in rows))

    def test_national_writer_favourites_and_grudges(self):
        rows = plan([acc("bill", "BARNWELL")])
        favs = [r for r in rows if "FAVOURITE" in r["tags"]]
        grudges = [r for r in rows if "GRUDGE" in r["tags"]]
        self.assertTrue(2 <= len(favs) <= 4)
        self.assertTrue(1 <= len(grudges) <= 2)
        self.assertTrue(all(0.4 <= r["trust"] <= 0.7 for r in favs))
        self.assertTrue(all(-0.6 <= r["trust"] <= -0.3 for r in grudges))
        self.assertFalse({(r["target_type"], r["target_id"]) for r in favs}
                         & {(r["target_type"], r["target_id"]) for r in grudges})

    def test_hot_takers_hold_more_grudges(self):
        rows = plan([acc("Skip_Bayless_National", "SKIP_BAYLESS")])
        self.assertEqual(sum("GRUDGE" in r["tags"] for r in rows), 2)
        self.assertGreaterEqual(sum("FAVOURITE" in r["tags"] for r in rows), 3)

    def test_former_team_slant(self):
        rows = plan([acc("chad", "OCHOCINCO")])
        self.assertIn(("team", "5", 0.5, ["FORMER_TEAM"]),
                      [(r["target_type"], r["target_id"], r["trust"], r["tags"]) for r in rows])

    def test_former_player_uses_assigned_team(self):
        rows = plan([acc("ex", "FORMER_PLAYER", 6)])
        self.assertIn(("team", "6"), [(r["target_type"], r["target_id"]) for r in rows if "FORMER_TEAM" in r["tags"]])

    def test_shared_person_gets_one_row_set(self):
        rows = plan([acc("Skip_Bayless_National", "SKIP_BAYLESS", person_key="Skip_Bayless"),
                     acc("Skip_Bayless", "SKIP_TWEET", person_key="Skip_Bayless")])
        self.assertEqual({r["person_key"] for r in rows}, {"Skip_Bayless"})
        keys = [(r["target_type"], r["target_id"]) for r in rows]
        self.assertEqual(len(keys), len(set(keys)))

    def test_new_national_accounts_get_no_favourites(self):
        self.assertEqual(plan([acc("agg", "AGGREGATOR"), acc("stats", "STATS_DESK"), acc("panel", "ROUNDTABLE_PANEL")]), [])


class FakeDB:
    """A tiny in-memory stand-in for the persona_relationships statements relationships.py runs."""

    def __init__(self, ready=True, refs_ready=False):
        self.ready, self.rows, self.next_id, self._result, self.rowcount = ready, {}, 1, [], 0
        self.refs_ready = refs_ready
        self.picks = []                     # (id, person_key, season, week, winner_backed, actual_winner)
        self.calls = []                     # (id, person_key, season, graded_week, call_type, subject_type,
        #                                      subject_id, result, detail)
        self.refs = set()                   # (persona, target_type, target_id, ref) recorded as applied

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self._result, self.rowcount = [], 0
        if s.startswith(("SAVEPOINT", "RELEASE", "ROLLBACK")):
            return
        if "to_regclass('public.persona_relationships')" in s:
            self._result = [(self.ready,)]
        elif "information_schema.columns" in s:
            self._result = [(1,)] if self.ready else []
        elif s.startswith("SELECT id, trust, tags, evidence FROM persona_relationships WHERE persona_type = 'writer' AND persona_id"):
            row = self.rows.get(tuple(params))
            self._result = [(row["id"], row["trust"], list(row["tags"]), json.dumps(row["evidence"]))] if row else []
        elif s.startswith("SELECT id, trust, tags, evidence FROM persona_relationships WHERE persona_type = 'writer'"):
            self._result = [(r["id"], r["trust"], list(r["tags"]), json.dumps(r["evidence"])) for r in self.rows.values()]
        elif s.startswith("SELECT target_type, target_id, trust, tags, evidence FROM persona_relationships"):
            self._result = [(k[1], k[2], r["trust"], r["tags"], json.dumps(r["evidence"]))
                            for k, r in self.rows.items() if k[0] == params[0]]
        elif "FROM agent_predictions p" in s:
            self._result = [tuple(p) for p in self.picks]
        elif "FROM agent_season_calls" in s and s.startswith("SELECT id, person_key"):
            self._result = [tuple(c) for c in self.calls]
        elif s.startswith("SELECT to_regclass('public.agent_season_calls')"):
            self._result = [(True,)]
        elif s.startswith("SELECT to_regclass('public.persona_relationship_event_refs')"):
            self._result = [(self.refs_ready,)]
        elif s.startswith("SELECT 1 FROM persona_relationship_event_refs"):
            self._result = [(1,)] if tuple(params) in self.refs else []
        elif s.startswith("INSERT INTO persona_relationship_event_refs"):
            pk, ttype, tid, ref, _season = params
            self.refs.add((pk, ttype, tid, ref))
            self.rowcount = 1
        elif s.startswith("SELECT evidence FROM persona_relationships"):
            row = self.rows.get(tuple(params))
            self._result = [(json.dumps(row["evidence"]),)] if row else []
        elif s.startswith("INSERT INTO persona_relationships"):
            pk, ttype, tid, trust, tags, ev = params[:6]
            if (pk, ttype, tid) not in self.rows:
                self.rows[(pk, ttype, tid)] = {"id": self.next_id, "trust": trust, "tags": list(tags),
                                               "evidence": json.loads(ev) if isinstance(ev, str) else []}
                self.next_id += 1
                self.rowcount = 1
        elif s.startswith("UPDATE persona_relationships SET trust = %s, tags"):
            trust, tags, ev, rid = params
            row = next(r for r in self.rows.values() if r["id"] == rid)
            row.update(trust=trust, tags=list(tags), evidence=json.loads(ev))
        elif s.startswith("UPDATE persona_relationships SET trust = %s, evidence"):
            trust, ev, rid = params
            row = next(r for r in self.rows.values() if r["id"] == rid)
            row.update(trust=trust, evidence=json.loads(ev))
        else:
            raise AssertionError(f"unexpected SQL: {s[:80]}")

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


class TakeTests(unittest.TestCase):
    def test_take_moves_trust_and_creates_rows(self):
        db = FakeDB()
        n = rel.apply_take(db, "Skip_Bayless", 2036, 3, [{"type": "player", "id": 7, "lean": 1, "text": "He's the guy"},
                                                         {"type": "team", "id": 2, "lean": -1},
                                                         {"type": "team", "id": 3, "lean": 0},
                                                         {"type": "owner", "id": 1, "lean": 1}])
        self.assertEqual(n, 3)
        self.assertEqual(db.rows[("Skip_Bayless", "player", "7")]["trust"], rel.GROW_STEP)
        self.assertEqual(db.rows[("Skip_Bayless", "team", "2")]["trust"], -rel.GROW_STEP)
        self.assertEqual(db.rows[("Skip_Bayless", "team", "3")]["trust"], 0)
        self.assertEqual(db.rows[("Skip_Bayless", "team", "3")]["evidence"], [])
        ev = db.rows[("Skip_Bayless", "player", "7")]["evidence"]
        self.assertEqual(ev[-1]["kind"], "TAKE")
        self.assertEqual(ev[-1]["text"], "He's the guy")

    def test_strong_trust_adds_tags_and_evidence_is_capped(self):
        db = FakeDB()
        for week in range(1, 14):
            rel.apply_take(db, "p", 2036, week, [{"type": "player", "id": 1, "lean": 1}, {"type": "coach", "id": 2, "lean": -1}])
        fav, grudge = db.rows[("p", "player", "1")], db.rows[("p", "coach", "2")]
        self.assertGreaterEqual(fav["trust"], rel.STRONG)
        self.assertIn("FAVOURITE", fav["tags"])
        self.assertLessEqual(grudge["trust"], -rel.STRONG)
        self.assertIn("GRUDGE", grudge["tags"])
        # the last EVIDENCE_KEEP moves, plus the sticky applied-refs entry (B12)
        self.assertEqual(len(fav["evidence"]), rel.EVIDENCE_KEEP + 1)

    def test_trust_stays_in_range(self):
        db = FakeDB()
        for week in range(60):
            rel.apply_take(db, "p", 2036, week, [{"type": "team", "id": 1, "lean": 1}])
        self.assertEqual(db.rows[("p", "team", "1")]["trust"], 1.0)

    def test_no_table_is_a_no_op(self):
        db = FakeDB(ready=False)
        self.assertEqual(rel.apply_take(db, "p", 2036, 1, [{"type": "team", "id": 1, "lean": 1}]), 0)
        self.assertEqual(db.rows, {})


class GradedTests(unittest.TestCase):
    def test_pick_deltas(self):
        picks = [{"id": 1, "person_key": "a", "season": 2036, "week": 4, "winner_backed": 2, "actual_winner": 2},
                 {"id": 2, "person_key": "a", "season": 2036, "week": 4, "winner_backed": 3, "actual_winner": 5},
                 {"id": 3, "person_key": "a", "season": 2036, "week": 4, "winner_backed": 3, "actual_winner": None}]
        ev = rel.graded_events(picks, [])
        self.assertEqual([(e[2], e[3]) for e in ev], [(2, rel.EARN_RIGHT), (3, rel.EARN_BURNED)])
        self.assertEqual(ev[0][5]["ref"], "pick:1")

    def test_call_deltas_and_doubter(self):
        calls = [{"id": 1, "person_key": "a", "season": 2036, "graded_week": 22, "call_type": "MVP", "subject_type": "player",
                  "subject_id": 9, "result": "RIGHT"},
                 {"id": 2, "person_key": "a", "season": 2036, "graded_week": 22, "call_type": "SUPER_BOWL",
                  "subject_type": "team", "subject_id": 4, "result": "WRONG"},
                 {"id": 3, "person_key": "a", "season": 2036, "graded_week": 18, "call_type": "COACH_FIRED",
                  "subject_type": "coach", "subject_id": 7, "result": "RIGHT"},
                 {"id": 4, "person_key": "a", "season": 2036, "graded_week": 22, "call_type": "MVP", "subject_type": "player",
                  "subject_id": None, "result": "VOID"}]
        ev = rel.graded_events([], calls)
        self.assertEqual([(e[1], e[2], e[3], e[4]) for e in ev],
                         [("player", 9, rel.EARN_RIGHT, ()), ("team", 4, rel.EARN_BURNED, ()), ("coach", 7, 0.0, ("DOUBTER",))])

    def test_constants(self):
        self.assertEqual((rel.GROW_STEP, rel.EARN_RIGHT, rel.EARN_BURNED, rel.DECAY), (0.04, 0.03, -0.06, 0.8))

    def test_applying_a_pick_records_its_ref_and_a_rerun_skips_it(self):
        db = FakeDB(refs_ready=True)
        db.picks = [(1, "p", 2036, 4, 1, 1)]
        self.assertEqual(rel.apply_graded(db, 2036, None), 1)
        self.assertIn(("p", "team", "1", "pick:1"), db.refs)
        self.assertEqual(rel.apply_graded(db, 2036, None), 0)

    def test_a_pre_marker_row_is_guarded_by_its_sticky_refs(self):
        """M-0174 M-a/M-b: the guard is the union. A fresh database has the table but no backfill, and the row's
        sticky APPLIED_REFS entry still holds the refs that were trimmed out of the evidence window."""
        db = FakeDB(refs_ready=True)
        db.rows[("p", "team", "1")] = {"id": 1, "trust": 0.21, "tags": [], "evidence": [
            {"kind": "APPLIED_REFS", "refs": [f"pick:{w}" for w in range(1, 8)]}] + [
            {"season": 2036, "week": w, "kind": "PICK_RIGHT", "delta": 0.03, "text": "x", "ref": f"pick:{w}"}
            for w in range(3, 8)]}
        db.picks = [(w, "p", 2036, w, 1, 1) for w in range(1, 8)]
        self.assertEqual(rel.apply_graded(db, 2036, None), 0)
        self.assertAlmostEqual(db.rows[("p", "team", "1")]["trust"], 0.21)

    def test_an_empty_refs_table_still_guards_on_the_evidence(self):
        """A restored or freshly built database has the table but no rows; the evidence refs still count."""
        db = FakeDB(refs_ready=True)
        db.rows[("p", "team", "1")] = {"id": 1, "trust": 0.21, "tags": [], "evidence": [
            {"season": 2036, "week": w, "kind": "PICK_RIGHT", "delta": 0.03, "text": "x", "ref": f"pick:{w}"}
            for w in range(3, 8)]}
        db.picks = [(w, "p", 2036, w, 1, 1) for w in range(1, 8)]
        self.assertEqual(rel.apply_graded(db, 2036, None), 2)      # only the two with no record anywhere
        self.assertEqual(rel.apply_graded(db, 2036, None), 0)

    def test_a_call_graded_in_the_same_week_as_a_pick_is_still_applied(self):
        """M-0167: the ordering guess skipped it (both events share (season, week)); the exact set does not."""
        db = FakeDB(refs_ready=True)
        db.picks = [(1, "p", 2036, 10, 1, 1)]
        db.calls = [(2, "p", 2036, 10, "MVP", "team", 1, "RIGHT", None)]
        self.assertEqual(rel.apply_graded(db, 2036, None), 2)

    def test_an_out_of_order_full_season_pass_applies_every_pick_once(self):
        db = FakeDB(refs_ready=True)
        db.picks = [(w, "p", 2036, w, 1, 1) for w in range(7, 0, -1)]
        self.assertEqual(rel.apply_graded(db, 2036, None), 7)
        self.assertEqual(rel.apply_graded(db, 2036, None), 0)

    def test_a_pick_that_completes_after_a_later_call_is_still_applied(self):
        db = FakeDB(refs_ready=True)
        db.calls = [(2, "p", 2036, 11, "MVP", "team", 1, "RIGHT", None)]
        self.assertEqual(rel.apply_graded(db, 2036, None), 1)
        db.picks = [(1, "p", 2036, 10, 1, 1)]             # its game only completes later
        self.assertEqual(rel.apply_graded(db, 2036, None), 1)

    def test_the_same_game_picked_in_two_articles_counts_twice(self):
        db = FakeDB(refs_ready=True)
        db.picks = [(1, "p", 2036, 4, 1, 1), (2, "p", 2036, 4, 1, 1)]
        self.assertEqual(rel.apply_graded(db, 2036, None), 2)

    def test_a_database_without_the_refs_table_still_guards_on_the_evidence(self):
        db = FakeDB()                                    # no 2027_02_22
        db.picks = [(1, "p", 2036, 4, 1, 1)]
        self.assertEqual(rel.apply_graded(db, 2036, None), 1)
        self.assertEqual(rel.apply_graded(db, 2036, None), 0)


class BackfillTests(unittest.TestCase):
    def test_it_records_only_what_the_evidence_and_the_sticky_entry_prove(self):
        """M-0174 L-c: no inference. A ref whose only record was trimmed away stays unrecorded."""
        import importlib
        bf = importlib.import_module("db.migrations.2027_02_22_backfill_applied_refs")
        evidence = [{"kind": "APPLIED_REFS", "refs": ["pick:1", "pick:2"]}] + [
            {"season": 2036, "week": w, "kind": "PICK_RIGHT", "ref": f"pick:{w}"} for w in (5, 6, 7)]
        self.assertEqual(bf.backfill_refs(evidence),
                         {"pick:1": None, "pick:2": None, "pick:5": 2036, "pick:6": 2036, "pick:7": 2036})

    def test_a_row_with_no_records_gets_nothing(self):
        import importlib
        bf = importlib.import_module("db.migrations.2027_02_22_backfill_applied_refs")
        self.assertEqual(bf.backfill_refs([]), {})


class DecayTests(unittest.TestCase):
    def test_decayed_trust_keeps_seeded_signs(self):
        self.assertEqual(rel.decayed_trust(0.5, ["FAVOURITE"]), 0.4)
        self.assertEqual(rel.decayed_trust(-0.5, []), -0.4)
        self.assertEqual(rel.decayed_trust(0.1, ["OWN_TEAM"]), rel.DECAY_FLOOR)
        self.assertEqual(rel.decayed_trust(-0.1, ["RIVAL"]), -rel.DECAY_FLOOR)
        for t in (0.8, 0.12, 0.01):
            self.assertGreater(rel.decayed_trust(t, ["OWN_TEAM"]), 0)
            self.assertLess(rel.decayed_trust(-t, ["RIVAL"]), 0)

    def test_decay_skips_touched_rows_and_runs_once(self):
        db = FakeDB()
        db.rows[("p", "team", "1")] = {"id": 1, "trust": 0.8, "tags": ["OWN_TEAM"], "evidence": []}
        db.rows[("p", "player", "2")] = {"id": 2, "trust": 0.5, "tags": [], "evidence": [
            {"season": 2036, "week": 4, "kind": "TAKE", "delta": 0.04, "text": "x"}]}
        db.rows[("p", "team", "3")] = {"id": 3, "trust": -0.6, "tags": ["RIVAL"], "evidence": []}
        self.assertEqual(rel.decay(db, 2037), 2)
        self.assertEqual(rel.decay(db, 2037), 0)
        self.assertEqual(db.rows[("p", "team", "1")]["trust"], 0.64)
        self.assertEqual(db.rows[("p", "team", "3")]["trust"], -0.48)
        self.assertEqual(db.rows[("p", "player", "2")]["trust"], 0.5)

    def test_decay_keeps_the_applied_refs_so_a_rerun_never_reapplies(self):
        """M-0150 B12: decay rebuilt evidence from the last five moves only, dropping the sticky applied-refs entry,
        so a forced rerun of an old season applied the same graded picks a second time."""
        db = FakeDB()
        db.rows[("p", "team", "1")] = {"id": 1, "trust": 0.4, "tags": [], "evidence": [
            {"kind": "APPLIED_REFS", "refs": ["pick:7", "pick:9"]}] + [
            {"season": 2030, "week": w, "kind": "PICK_RIGHT", "delta": 0.03, "text": "x", "ref": f"pick:{w}"}
            for w in range(1, 7)]}
        self.assertEqual(rel.decay(db, 2036), 1)
        ev = db.rows[("p", "team", "1")]["evidence"]
        self.assertEqual(next(e for e in ev if e.get("kind") == "APPLIED_REFS")["refs"], ["pick:7", "pick:9"])
        self.assertEqual(ev[-1]["kind"], "DECAY")
        self.assertTrue(rel._already_applied(db, "p", "team", "1", "pick:9"))
        self.assertFalse(rel._already_applied(db, "p", "team", "1", "pick:11"))


class BlockTests(unittest.TestCase):
    ROWS = [
        {"target_type": "player", "target_id": "1", "trust": 0.9, "tags": ["FAVOURITE"], "name": "Josh Allen",
         "evidence": [{"season": 2036, "week": 3, "kind": "TAKE", "delta": 0.04, "text": "Allen is the MVP"}]},
        {"target_type": "team", "target_id": "4", "trust": -0.7, "tags": ["GRUDGE"], "name": "Cowboys", "evidence": []},
        {"target_type": "coach", "target_id": "9", "trust": 0.05, "tags": [], "name": "Sean McDermott", "evidence": [
            {"season": 2036, "week": 5, "kind": "PICK_BURNED", "delta": -0.06, "text": "you picked them and they burned you"}]},
        {"target_type": "player", "target_id": "2", "trust": 0.1, "tags": [], "name": "Tua Tagovailoa", "evidence": []},
    ]

    def test_subjects_first_then_strongest(self):
        block = rel.render_relationship_rows(self.ROWS, [{"type": "coach", "id": 9}], limit=4)
        lines = block.splitlines()
        self.assertIn("Sean McDermott", lines[0])
        self.assertIn("they burned you", lines[0])
        self.assertIn("Josh Allen believer", lines[1])
        self.assertIn('you said "Allen is the MVP"', lines[1])
        self.assertIn("can't stand the Cowboys", lines[2])
        self.assertEqual(len(lines), 3)          # Tua is weak and not a subject

    def test_limit(self):
        self.assertEqual(len(rel.render_relationship_rows(self.ROWS, [], limit=1).splitlines()), 1)

    def test_empty_without_table_or_rows(self):
        self.assertEqual(rel.relationship_block(FakeDB(ready=False), "p", []), "")
        self.assertEqual(rel.relationship_block(FakeDB(), "p", []), "")
        self.assertEqual(rel.relationship_block(None, "p", []), "")


class PersonKeyTests(unittest.TestCase):
    def test_missing_column_falls_back_to_the_account(self):
        db = FakeDB(ready=False)
        self.assertEqual(rel.person_agent_ids(db, "acct"), ["acct"])
        self.assertEqual(rel.person_key(db, "acct"), "acct")


if __name__ == "__main__":
    unittest.main()
