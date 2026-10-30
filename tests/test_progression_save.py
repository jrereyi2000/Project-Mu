"""engines/progression_save.py: the progression save pass's edits document (synthetic snapshots, a fake cursor), plus
the sample save run through save_writer.mjs on a temp copy when CAREER-GIANTS-AUTOSAVE and node are available."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import pytest

from engines import ovr_weights as ow
from engines import progression_save as ps

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The sample save sits at the main checkout's root (a worktree has no copy of its own).
SAMPLE_SAVE = os.environ.get("MU_SAMPLE_SAVE") or next(
    (p for p in (os.path.join(ROOT, "CAREER-GIANTS-AUTOSAVE"), "/home/jrereyi/Project-Mu/CAREER-GIANTS-AUTOSAVE")
     if os.path.exists(p)), os.path.join(ROOT, "CAREER-GIANTS-AUTOSAVE"))
SAVE_READER = os.path.join(ROOT, "pipeline", "save_reader")

DOC = ow.load_weights()

WR_RATINGS = {"Speed": 90, "Acceleration": 90, "Agility": 86, "ChangeOfDirection": 84, "Strength": 60, "Jumping": 85,
              "Stamina": 85, "Awareness": 70, "PlayRecognition": 40, "Catching": 80, "CatchInTraffic": 70,
              "SpectacularCatch": 75, "ShortRouteRunning": 72, "MediumRouteRunning": 74, "DeepRouteRunning": 78,
              "Release": 72, "BCVision": 60, "Carrying": 65, "Injury": 85, "Toughness": 80}


def snapshot(players=None, dev_field=None, breakout_field=None, dev_on=True, breakout_on=True):
    return {
        "meta": {"reader_version": 4},
        "players": players if players is not None else [
            {"pgid": 101, "position": "WR", "archetype": "WR_DeepThreat", "dev_trait": "Normal", "confidence": 72,
             "overall": 80, "archetype_grades": [80, 75, 70, 72, 0], "ratings": dict(WR_RATINGS),
             "traits": {"PT_COVERBALL": False, "PT_HAPPYFEET": False, "PT_SEEINGGHOSTS": False}},
            {"pgid": 202, "position": "QB", "archetype": "QB_FieldGeneral", "dev_trait": "College_Impact",
             "confidence": 50, "overall": 70, "archetype_grades": [70, 65, 60, 55, 0],
             "ratings": {"Awareness": 70, "ThrowPower": 85, "ThrowAccuracyShort": 75},
             "traits": {"PT_COVERBALL": True, "PT_HAPPYFEET": True, "PT_SEEINGGHOSTS": False}},
            {"pgid": 303, "position": "CB", "archetype": "CB_Zone", "dev_trait": "Normal", "confidence": 99,
             "overall": 60, "archetype_grades": [55, 58, 60, 0, 0], "ratings": {"Speed": 88},
             "traits": {"PT_COVERBALL": False}},
        ],
        "league_settings": {
            "sliders": {
                "progression_by_age": {"table_id": 5949, "row": 0, "field": "Field_165",
                                       "values": {"int0": 0, "int1": 5, "int2": 0}},
                "progression_by_position": {"table_id": 4317, "row": 0, "field": "Field_166",
                                            "values": {"QB": 0, "LS": 100, "DefaultValue": 100}},
                "regression_by_age": {"table_id": 5949, "row": 1, "field": "Field_167",
                                      "values": {"int0": 50, "int1": 110, "int2": 0}},
                "regression_by_position": {"table_id": 4317, "row": 1, "field": "Field_168",
                                           "values": {"QB": 70, "LS": 100, "DefaultValue": 100}},
            },
            "league_setting": {"table_id": 4290, "row": 0},
            "dev_trait_regression_field": dev_field,
            "dev_trait_regression_enabled": dev_on if dev_field else None,
            "breakout_scenarios_field": breakout_field,
            "breakout_scenarios_enabled": breakout_on if breakout_field else None,
        },
    }


def row(player_id, diff=None, dev_before="Normal", dev_after="Normal", behaviour=None):
    return {"player_id": player_id, "ratings_diff": diff or {}, "dev_before": dev_before, "dev_after": dev_after,
            "behaviour_changes": behaviour}


PGIDS = {1: 101, 2: 202, 3: 303}


class SliderTest(unittest.TestCase):
    def test_all_four_sliders_to_zero_with_ls(self):
        edits = ps.slider_edits(snapshot())
        self.assertEqual(len(edits), 4)
        by = {(e["tableId"], e["row"]): e for e in edits}
        self.assertEqual(by[(5949, 0)]["set"], {"int0": 0, "int1": 0, "int2": 0})
        self.assertEqual(by[(5949, 1)]["set"], {"int0": 0, "int1": 0, "int2": 0})
        self.assertEqual(by[(4317, 0)]["set"], {"QB": 0, "LS": 0})
        self.assertEqual(by[(4317, 1)]["set"], {"QB": 0, "LS": 0})
        self.assertEqual(by[(5949, 0)]["table"], "int[]")
        self.assertEqual(by[(4317, 1)]["table"], "ProgressionXPSlider")

    def test_default_value_untouched(self):
        for e in ps.slider_edits(snapshot()):
            self.assertNotIn("DefaultValue", e["set"])

    def test_null_slider_skipped_with_warning(self):
        snap = snapshot()
        snap["league_settings"]["sliders"]["regression_by_age"] = None
        warnings = []
        edits = ps.slider_edits(snap, warnings)
        self.assertEqual(len(edits), 3)
        self.assertTrue(any("regression_by_age" in w for w in warnings))


class DevFlagTest(unittest.TestCase):
    def test_none_while_fields_unknown(self):
        warnings = []
        self.assertIsNone(ps.dev_flag_edit(snapshot(), warnings))
        self.assertIsNone(ps.breakout_flag_edit(snapshot(), warnings))
        self.assertTrue(any("dev_trait_regression_field" in w for w in warnings))
        self.assertTrue(any("breakout_scenarios_field" in w for w in warnings))

    def test_both_off_when_known_and_on(self):
        snap = snapshot(dev_field="Field_88", breakout_field="Field_86")
        self.assertEqual(ps.dev_flag_edit(snap),
                         {"table": "LeagueSetting", "tableId": 4290, "row": 0, "set": {"Field_88": 0}})
        self.assertEqual(ps.breakout_flag_edit(snap),
                         {"table": "LeagueSetting", "tableId": 4290, "row": 0, "set": {"Field_86": 0}})

    def test_named_fields_written_false(self):
        snap = snapshot(dev_field="IsDevTraitRegressionEnabled", breakout_field="IsDevTraitIncreaseScenarioEnabled")
        self.assertEqual(ps.dev_flag_edit(snap)["set"], {"IsDevTraitRegressionEnabled": False})
        self.assertEqual(ps.breakout_flag_edit(snap)["set"], {"IsDevTraitIncreaseScenarioEnabled": False})

    def test_absent_when_already_off(self):
        snap = snapshot(dev_field="Field_88", breakout_field="Field_86", dev_on=False, breakout_on=False)
        warnings = []
        self.assertIsNone(ps.dev_flag_edit(snap, warnings))
        self.assertIsNone(ps.breakout_flag_edit(snap, warnings))
        self.assertEqual(warnings, [])


class RatingEditsTest(unittest.TestCase):
    def test_only_changed_ratings_plus_ovr_grades_type(self):
        edits = ps.rating_edits([row(1, {"Speed": 2, "DeepRouteRunning": 3, "Catching": 0})], snapshot(), PGIDS, DOC)
        self.assertEqual(len(edits), 1)
        s = edits[0]["set"]
        self.assertEqual(edits[0]["pgid"], 101)
        self.assertEqual(s["SpeedRating"], 92)
        self.assertEqual(s["DeepRouteRunningRating"], 81)
        self.assertNotIn("CatchingRating", s)
        self.assertNotIn("AgilityRating", s)
        new = dict(WR_RATINGS, Speed=92, DeepRouteRunning=81)
        ovr, arch = ow.predict_ovr(DOC, "WR", new, current="WR_DeepThreat")
        self.assertEqual(s["OverallRating"], ovr)
        self.assertEqual(s["PlayerType"], arch)
        for i, a in enumerate(DOC["slots"]["WR"]):
            if a:
                self.assertEqual(s[f"OverallGrade{i}"], ow.predict_grade(DOC, a, new))
        self.assertEqual(s["OverallGrade4"], 0)          # no archetype in slot 4: stored value kept
        self.assertEqual(s["OverallRating"], max(s[f"OverallGrade{i}"] for i in range(5)))

    def test_clamped_and_no_change_no_edit(self):
        self.assertEqual(ps.rating_edits([row(1)], snapshot(), PGIDS, DOC), [])
        s = ps.rating_edits([row(1, {"Speed": 20})], snapshot(), PGIDS, DOC)[0]["set"]
        self.assertEqual(s["SpeedRating"], 99)

    def test_pt_flags_gained_and_lost(self):
        bc = {"gained": [{"flag": "PT_SEEINGGHOSTS", "cause": "SACKS"}],
              "lost": [{"flag": "PT_HAPPYFEET", "cause": "EXPERIENCE"}]}
        edits = ps.rating_edits([row(2, behaviour=bc)], snapshot(), PGIDS, DOC)
        self.assertEqual(edits, [{"pgid": 202, "set": {"PT_SEEINGGHOSTS": True, "PT_HAPPYFEET": False}}])

    def test_pt_flag_already_in_state_skipped(self):
        bc = {"gained": [{"flag": "PT_COVERBALL", "cause": "BALL_SECURITY"}], "lost": []}
        self.assertEqual(ps.rating_edits([row(2, behaviour=bc)], snapshot(), PGIDS, DOC), [])

    def test_dev_trait_mapped_to_save_enum(self):
        s = ps.rating_edits([row(1, dev_before="Normal", dev_after="X-Factor")], snapshot(), PGIDS, DOC)[0]["set"]
        self.assertEqual(s, {"TraitDevelopment": "XFactor"})
        # College_Impact is Star's stored value: no edit for a Star he already has
        self.assertEqual(ps.rating_edits([row(2, dev_before="Normal", dev_after="Star")], snapshot(), PGIDS, DOC), [])
        # unchanged dev: no edit
        self.assertEqual(ps.rating_edits([row(1, dev_before="Star", dev_after="Star")], snapshot(), PGIDS, DOC), [])

    def test_unknown_player_warned(self):
        warnings = []
        self.assertEqual(ps.rating_edits([row(9, {"Speed": 1})], snapshot(), PGIDS, DOC, warnings), [])
        self.assertTrue(warnings)


class MoraleTest(unittest.TestCase):
    def test_morale_50_only_where_different(self):
        edits = ps.morale_edits(snapshot())
        self.assertEqual(edits, [{"pgid": 101, "set": {"ConfidenceRating": 50}},
                                 {"pgid": 303, "set": {"ConfidenceRating": 50}}])
        self.assertEqual(ps.morale_edits(snapshot(), value=72), [{"pgid": 202, "set": {"ConfidenceRating": 72}},
                                                                 {"pgid": 303, "set": {"ConfidenceRating": 72}}])


class FakeCursor:
    def __init__(self, rows):
        self.rows, self.sql, self.connection = rows, [], object()

    def execute(self, sql, params=None):
        self.sql.append(sql)

    def fetchall(self):
        return self.rows


class PassEditsTest(unittest.TestCase):
    def test_fetch_pgid_map(self):
        cur = FakeCursor([(1, 101), (2, 202)])
        self.assertEqual(ps.fetch_pgid_map(cur), {1: 101, 2: 202})
        self.assertIn("madden_pgid", cur.sql[0])

    def test_one_document_one_entry_per_pgid(self):
        cur = FakeCursor([(1, 101), (2, 202), (3, 303)])
        rows = [row(1, {"Speed": 1}, behaviour={"gained": [{"flag": "PT_COVERBALL", "cause": "BALL_SECURITY"}]}),
                row(2, {"Awareness": -2}, dev_before="Star", dev_after="Superstar")]
        with mock.patch.object(ps.progression, "run_progression",
                               return_value={"results": rows}) as run:
            doc = ps.pass_edits(cur, snapshot(), 2037)
        kwargs = run.call_args.kwargs
        self.assertIs(run.call_args.args[0], cur.connection)
        self.assertEqual(run.call_args.args[1], 2037)
        self.assertFalse(kwargs["commit"])
        self.assertTrue(kwargs["dry_run"])
        self.assertEqual(kwargs["traits"][2]["PT_HAPPYFEET"], True)
        self.assertEqual(set(doc), {"players", "records"})
        pgids = [e["pgid"] for e in doc["players"]]
        self.assertEqual(sorted(pgids), [101, 202, 303])
        self.assertEqual(len(pgids), len(set(pgids)))
        by = {e["pgid"]: e["set"] for e in doc["players"]}
        self.assertEqual(by[101]["SpeedRating"], 91)
        self.assertTrue(by[101]["PT_COVERBALL"])
        self.assertEqual(by[101]["ConfidenceRating"], 50)
        self.assertIn("OverallRating", by[101])
        self.assertEqual(by[202]["AwarenessRating"], 68)
        self.assertEqual(by[202]["TraitDevelopment"], "Superstar")
        self.assertNotIn("ConfidenceRating", by[202])
        self.assertEqual(by[303], {"ConfidenceRating": 50})
        self.assertEqual(len(doc["records"]), 4)          # flag fields unknown: sliders only

    def test_flags_included_when_known(self):
        cur = FakeCursor([])
        with mock.patch.object(ps.progression, "run_progression", return_value={"results": []}):
            doc = ps.pass_edits(cur, snapshot(dev_field="Field_88", breakout_field="Field_86"), 2037,
                                pgid_by_player=PGIDS)
        self.assertEqual(len(doc["records"]), 6)
        self.assertEqual([r["set"] for r in doc["records"][-2:]], [{"Field_88": 0}, {"Field_86": 0}])

    def test_flags_absent_when_off_or_null(self):
        warnings = []
        doc = ps.build_document([], snapshot(dev_field="Field_88", dev_on=False), PGIDS, doc=DOC, warnings=warnings)
        self.assertEqual(len(doc["records"]), 4)
        self.assertTrue(any("breakout_scenarios_field" in w for w in warnings))


def _sample_ready():
    return (os.path.exists(SAMPLE_SAVE) and shutil.which("node")
            and os.path.isdir(os.path.join(SAVE_READER, "node_modules", "madden-franchise")))


@pytest.mark.skipif(not _sample_ready(), reason="sample save, node or pipeline/save_reader node_modules missing")
class SampleSaveTest(unittest.TestCase):
    def test_save_writer_accepts_slider_flag_and_morale_edits(self):
        tmp = tempfile.mkdtemp(prefix="progression_save_")
        try:
            snap_path, edits_path, out_path = (os.path.join(tmp, n) for n in ("save.json", "edits.json", "out-save"))
            save = os.path.join(tmp, "CAREER-COPY")
            shutil.copyfile(SAMPLE_SAVE, save)
            subprocess.run(["node", os.path.join(SAVE_READER, "pc_save_reader.mjs"), save, "--out", snap_path],
                           check=True, capture_output=True, timeout=300)
            with open(snap_path) as fh:
                snap = json.load(fh)
            self.assertEqual(snap["meta"]["reader_version"], 4)
            # one rated player too, so the OVR dual-write fields go through the writer's schema check
            p = next(x for x in snap["players"] if x["position"] == "WR" and x["ratings"].get("Speed", 99) < 95)
            rows = [{"player_id": 1, "ratings_diff": {"Speed": 1}, "dev_before": "Normal", "dev_after": "Normal",
                     "behaviour_changes": None}]
            doc = ps.build_document(rows, snap, {1: p["pgid"]})
            ls = snap["league_settings"]
            self.assertEqual(ls["dev_trait_regression_field"], "Field_88")
            self.assertEqual(ls["breakout_scenarios_field"], "Field_86")
            expected = 4 + (ls["dev_trait_regression_enabled"] is not False) + (ls["breakout_scenarios_enabled"] is not False)
            self.assertEqual(len(doc["records"]), expected)
            if ls["dev_trait_regression_enabled"]:
                self.assertIn({"Field_88": 0}, [r["set"] for r in doc["records"] if r["table"] == "LeagueSetting"])
            if ls["breakout_scenarios_enabled"]:
                self.assertIn({"Field_86": 0}, [r["set"] for r in doc["records"] if r["table"] == "LeagueSetting"])
            for r in doc["records"]:
                self.assertTrue(all(v == 0 for v in r["set"].values()))
            self.assertIn("LS", next(r for r in doc["records"] if r["table"] == "ProgressionXPSlider")["set"])
            self.assertTrue(doc["players"])
            with open(edits_path, "w") as fh:
                json.dump(doc, fh)
            res = subprocess.run(["node", os.path.join(SAVE_READER, "save_writer.mjs"), save, edits_path,
                                  "--out", out_path], capture_output=True, text=True, timeout=600)
            self.assertEqual(res.returncode, 0, res.stderr[-2000:])
            self.assertTrue(os.path.exists(out_path))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
