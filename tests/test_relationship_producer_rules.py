"""engines/relationship_producers rules.py and calendar.py (RELATIONSHIP_EVENT_PRODUCERS_PLAN.md §2.1-§2.2, §4) on
synthetic dicts: role tiers per scheme, eligibility (stuck IR, byes), established roles, assessments (D2), every move
row, situations, coach facts, deterministic source IDs, envelope shape, HISTORY players skipped. No database."""
import copy
import importlib
import json
import subprocess
import sys
import unittest

from engines.relationship_producers import PRODUCER_RULES_VERSION
from engines.relationship_producers import calendar as cal
from engines.relationship_producers import rules as r

try:
    from personality import relationship_events as rel_events
except ImportError:                       # the relationships plan's U1 isn't on main yet
    rel_events = None

S = 2026

CHART = {
    "QB": [1, 2, 3], "HB": [4, 5], "WR": [10, 11, 12, 13, 14], "TE": [15, 16],
    "LT": [60], "LG": [61], "C": [62], "RG": [63], "RT": [64], "LE": [65], "RE": [66], "LOLB": [67], "ROLB": [68],
    "DT": [30, 31, 32], "MLB": [40, 41, 42], "CB": [20, 21, 22, 23], "FS": [24], "SS": [25],
    "K": [70], "P": [71], "LS": [72],
    "NT": [50], "SLCB": [23], "KR": [99], "PR": [99], "GAD": [98], "KOS": [97],
}
FOUR_THREE = {"defense": "4-3"}
THREE_FOUR = {"defense": "3-4"}


def obs(tick=5, season=S, tier="STARTER", **kw):
    base = {"player_id": 7, "team_id": 3, "season": season, "tick": tick, "origin": None, "tier": tier,
            "has_chart": True, "bye": False, "first_week_on_team": False, "injury": None, "rating_rank": 2}
    base.update(kw)
    return base


def hist(tiers, season=S, team_id=3, start=1, eligible=True):
    """Observation history; an entry is a tier, or (tier, eligible)."""
    out = []
    for i, t in enumerate(tiers):
        tier, ok = t if isinstance(t, tuple) else (t, eligible)
        out.append({"season": season, "tick": start + i, "team_id": team_id, "tier": tier, "eligible": ok})
    return out


def side(team_id, tick=25, season=S, player_id=7, origin=None):
    return {"player_id": player_id, "team_id": team_id, "season": season, "tick": tick, "origin": origin}


class EnvelopeChecks(unittest.TestCase):
    """Shape every producer envelope must have (plan §2.2 'Every envelope'), plus validate_event when present."""

    def check(self, env, *, kind, rule, visibility="public", confidence="high"):
        self.assertEqual(env["kind"], kind)
        self.assertEqual(env["schema_version"], r.RELATIONSHIP_SCHEMA_VERSION)
        self.assertEqual(env["basis"], "verified_simulation")
        self.assertEqual(env["confidence"], confidence)
        self.assertEqual(env["visibility"], visibility)
        self.assertEqual(env["payload"]["rule"], f"{rule}@{PRODUCER_RULES_VERSION}")
        self.assertEqual(set(env["subject_alias"]), {"role", "namespace", "external_id"})
        self.assertEqual(env["subject"], {"kind": "person", "universe_id": None,
                                          "role": env["subject_alias"]["role"], "persona_id": None})
        self.assertIsNone(env["universe_id"])
        self.assertIsNone(env["supersedes_source_event_id"])
        self.assertEqual(set(env["occurred_at"]), {"season", "tick"})
        self.assertTrue(env["target"]["team_key"].startswith("team:"))
        json.dumps(env)                                       # plain JSON
        if rel_events is not None:
            filled = copy.deepcopy(env)
            filled["universe_id"] = "u-test"
            filled["subject"].update(universe_id="u-test", persona_id="p-test")
            for ref in [filled["target"], filled.get("actor")] + filled["attribution"]["responsible"]:
                if ref:
                    ref["universe_id"] = "u-test"
            filled.pop("subject_alias")
            registry = {filled["target"]["team_key"]: {"team_id": 3, "abbreviation": "TST"}}
            rel_events.validate_event(filled, team_registry=registry)
        return env


class CalendarTest(unittest.TestCase):
    def test_constants_and_calendar(self):
        self.assertEqual(cal.TICKS_PER_SEASON, 32)
        self.assertEqual(cal.calendar([2027, 2026, 2026]), {2026: 32, 2027: 32})
        self.assertEqual(cal.time_point(S, 1), {"season": S, "tick": 1})
        for bad in (0, 33, "5", True):
            with self.assertRaises(ValueError):
                cal.time_point(S, bad)

    def test_advance_rolls_into_next_season(self):
        self.assertEqual(cal.advance(S, 5, 1), {"season": S, "tick": 6})
        self.assertEqual(cal.advance(S, 32, 1), {"season": S + 1, "tick": 1})
        self.assertEqual(cal.advance(S, 30, 4), {"season": S + 1, "tick": 2})

    def test_tick_for_in_season_uses_league_state_week(self):
        snap = {"season": {"stage": "NFLSeason", "week_type": "RegularSeason", "week_index": 4,
                           "calendar_year": S}}
        self.assertEqual(cal.tick_for(snap, 5), 5)

    def test_tick_for_every_offseason_stop(self):
        from pipeline import league_office as lo
        for week, number, stage, _wave, _name in lo.OFFSEASON_STOPS:
            snap = {"season": {"stage": "OffSeason", "week_index": week, "offseason_stage": number}}
            self.assertEqual(cal.tick_for(snap, 23), lo.STAGE_WEEKS[stage])
        pre = {"season": {"stage": "PreSeason", "week_index": 0}}
        cut = {"season": {"stage": "PreSeason", "week_index": lo.PRESEASON_CUTDOWN_WEEK}}
        self.assertEqual(cal.tick_for(pre, None), 32)
        self.assertEqual(cal.tick_for(cut, None), 31)

    def test_import_needs_no_database(self):
        code = ("import sys; import engines.relationship_producers.calendar, engines.relationship_producers.rules; "
                "bad = [m for m in sys.modules if m.split('.')[0] in ('psycopg2', 'psycopg', 'pipeline')]; "
                "print(bad); sys.exit(1 if bad else 0)")
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class RoleTierTest(unittest.TestCase):
    def test_four_three(self):
        self.assertEqual(r.role_tier(CHART, FOUR_THREE, 31), "STARTER")      # DT 2 in a 4-3
        self.assertEqual(r.role_tier(CHART, FOUR_THREE, 41), "ROTATION")     # first after the MLB starter
        self.assertEqual(r.role_tier(CHART, FOUR_THREE, 42), "RESERVE")
        self.assertEqual(r.role_tier(CHART, FOUR_THREE, 32), "ROTATION")

    def test_three_four(self):
        self.assertEqual(r.role_tier(CHART, THREE_FOUR, 31), "ROTATION")     # DT 1 in a 3-4
        self.assertEqual(r.role_tier(CHART, THREE_FOUR, 41), "STARTER")      # MLB 2 in a 3-4
        self.assertEqual(r.role_tier(CHART, THREE_FOUR, 42), "ROTATION")
        self.assertEqual(r.role_tier(CHART, THREE_FOUR, 32), "RESERVE")

    def test_starters_backups_and_wr_cb_counts(self):
        self.assertEqual(r.role_tier(CHART, FOUR_THREE, 12), "STARTER")      # WR 3
        self.assertEqual(r.role_tier(CHART, FOUR_THREE, 13), "ROTATION")
        self.assertEqual(r.role_tier(CHART, FOUR_THREE, 14), "RESERVE")
        self.assertEqual(r.role_tier(CHART, FOUR_THREE, 22), "ROTATION")     # first after CB 2
        self.assertEqual(r.role_tier(CHART, FOUR_THREE, 3), "RESERVE")       # QB3

    def test_situational_slots_count_as_rotation(self):
        self.assertEqual(r.role_tier(CHART, FOUR_THREE, 50), "ROTATION")     # NT depth 1
        self.assertEqual(r.role_tier(CHART, FOUR_THREE, 23), "ROTATION")     # SLCB depth 1 (CB4 otherwise)
        for pid in (99, 98, 97):                                             # KR/PR, GAD, KOS are ignored
            self.assertEqual(r.role_tier(CHART, FOUR_THREE, pid), "RESERVE")

    def test_rating_rank_at_position(self):
        team = [{"player_id": 1, "position": "QB", "overall": 80}, {"player_id": 2, "position": "QB", "overall": 85},
                {"player_id": 3, "position": "QB", "overall": 80}, {"player_id": 4, "position": "HB", "overall": 99}]
        self.assertEqual(r.rating_rank_at_position(team, 2), 1)
        self.assertEqual(r.rating_rank_at_position(team, 1), 2)
        self.assertEqual(r.rating_rank_at_position(team, 3), 3)
        self.assertIsNone(r.rating_rank_at_position(team, 9))


class EligibilityTest(unittest.TestCase):
    def test_healthy_in_season_week(self):
        self.assertEqual(r.eligible(obs()), (True, None))

    def test_ineligible_reasons(self):
        cases = {
            "offseason": obs(tick=25),
            "no_chart": obs(has_chart=False),
            "bye": obs(bye=True),
            "injured": obs(injury={"injury_id": 1, "weeks_remaining": 3, "is_ir": False, "injury_type": "Ankle"}),
            "first_week_on_team": obs(first_week_on_team=True),
        }
        for reason, o in cases.items():
            self.assertEqual(r.eligible(o), (False, reason))

    def test_stuck_ir_is_ineligible(self):
        stuck = {"injury_id": 2, "weeks_remaining": 0, "is_ir": True, "injury_type": "NONE"}
        self.assertEqual(r.eligible(obs(injury=stuck)), (False, "injured"))

    def test_healed_off_report_row_is_eligible(self):
        healed = {"injury_id": 3, "weeks_remaining": 0, "is_ir": False, "injury_type": "NONE"}
        self.assertEqual(r.eligible(obs(injury=healed)), (True, None))


class EstablishedRoleTest(unittest.TestCase):
    def test_four_weeks_establish(self):
        self.assertIsNone(r.established_role(hist(["STARTER"] * 3)))
        self.assertEqual(r.established_role(hist(["STARTER"] * 4)), "STARTER")
        self.assertIsNone(r.established_role([]))

    def test_higher_tier_counts_toward_lower(self):
        self.assertEqual(r.established_role(hist(["STARTER", "ROTATION", "STARTER", "ROTATION"])), "ROTATION")

    def test_ineligible_weeks_pause_without_breaking(self):
        h = hist(["STARTER", "STARTER", ("RESERVE", False), ("RESERVE", False), "STARTER", "STARTER"])
        self.assertEqual(r.established_role(h), "STARTER")

    def test_a_lower_week_breaks_the_run(self):
        # every rostered tier is at least RESERVE, so four eligible weeks establish RESERVE but not STARTER
        self.assertIsNone(r.established_role(hist(["STARTER", "STARTER", "RESERVE"])))
        self.assertEqual(r.established_role(hist(["STARTER", "STARTER", "RESERVE", "STARTER", "STARTER"])),
                         "RESERVE")
        self.assertEqual(r.established_role(hist(["STARTER", "STARTER", "RESERVE", "STARTER", "STARTER", "STARTER",
                                                  "STARTER"])), "STARTER")

    def test_highest_held_stays_after_a_demotion(self):
        self.assertEqual(r.established_role(hist(["STARTER"] * 4 + ["RESERVE"] * 5)), "STARTER")

    def test_season_boundary_resets_to_most_held(self):
        last = hist(["STARTER"] * 4 + ["ROTATION"] * 6)                          # starter established, then rotation
        new = hist(["RESERVE"], season=S + 1)
        self.assertEqual(r.established_role(last), "STARTER")
        self.assertEqual(r.established_role(last + new), "ROTATION")
        self.assertEqual(r.established_role(last + hist(["STARTER"] * 4, season=S + 1)), "STARTER")
        self.assertIsNone(r.established_role(hist([("STARTER", False)] * 6) + new))

    def test_new_team_starts_with_none(self):
        old = hist(["STARTER"] * 8, team_id=3)
        self.assertIsNone(r.established_role(old + hist(["RESERVE"], team_id=4, start=9)))
        self.assertEqual(r.established_role(old + hist(["STARTER"] * 4, team_id=4, start=9)), "STARTER")


class RoleWindowTest(EnvelopeChecks):
    def test_satisfied_and_envelope(self):
        env = r.role_window(obs(tick=6), hist(["STARTER"] * 5))
        self.check(env, kind="role_window", rule="role_window", visibility="private")
        p = env["payload"]
        self.assertEqual((p["agreed_role"], p["observed_role"], p["assessment"]), ("STARTER", "STARTER", "satisfied"))
        self.assertEqual(p["start"], {"season": S, "tick": 6})
        self.assertEqual(p["end"], {"season": S, "tick": 7})
        self.assertEqual(p["team_key"], "team:3")
        self.assertEqual(p["rating_rank_at_position"], 2)
        self.assertIn("mu-producer:depth_charts:3:2026:6:MADDEN", p["assessment_basis_refs"])
        self.assertEqual(env["source_event_id"], r.role_source_id(S, 6, 7))
        self.assertEqual(env["subject_alias"], {"role": "player", "namespace": "db-player", "external_id": "7"})

    def test_demotion_only_while_healthy(self):
        h = hist(["STARTER"] * 5)
        self.assertEqual(r.role_window(obs(tick=6, tier="RESERVE"), h)["payload"]["assessment"], "unsatisfied")
        hurt = {"injury_id": 9, "weeks_remaining": 2, "is_ir": False, "injury_type": "Knee"}
        env = r.role_window(obs(tick=6, tier="RESERVE", injury=hurt), h)
        self.assertEqual(env["payload"]["assessment"], "unknown")
        self.assertIn("mu-producer:injuries:9", env["payload"]["assessment_basis_refs"])
        stuck = {"injury_id": 10, "weeks_remaining": 0, "is_ir": True, "injury_type": "NONE"}
        self.assertEqual(r.role_window(obs(tick=6, tier="RESERVE", injury=stuck), h)["payload"]["assessment"],
                         "unknown")
        self.assertEqual(r.role_window(obs(tick=6, tier="RESERVE", bye=True), h)["payload"]["assessment"], "unknown")

    def test_nothing_established_is_unknown(self):
        self.assertEqual(r.role_window(obs(tick=2, tier="RESERVE"), hist(["STARTER"]))["payload"]["assessment"],
                         "unknown")

    def test_rated_above_starter_backup_is_not_unhappy(self):
        backup = r.role_window(obs(tick=6, tier="ROTATION", rating_rank=1), hist(["ROTATION"] * 5))
        self.assertEqual(backup["payload"]["assessment"], "satisfied")
        fresh = r.role_window(obs(tick=2, tier="ROTATION", rating_rank=1), hist(["ROTATION"]))
        self.assertEqual(fresh["payload"]["assessment"], "unknown")
        self.assertEqual(fresh["payload"]["rating_rank_at_position"], 1)

    def test_no_window_offseason_free_agent_no_chart_history(self):
        self.assertIsNone(r.role_window(obs(tick=24), []))
        self.assertIsNone(r.role_window(obs(team_id=None), []))
        self.assertIsNone(r.role_window(obs(has_chart=False, tier=None), []))
        self.assertIsNone(r.role_window(obs(origin="HISTORY_FILLIN"), []))

    def test_catchup_confidence(self):
        self.check(r.role_window(obs(), [], source="CATCHUP"), kind="role_window", rule="role_window",
                   visibility="private", confidence="medium")
        with self.assertRaises(ValueError):
            r.role_window(obs(), [], source="GUESS")


class TenureWindowTest(EnvelopeChecks):
    def test_tenure(self):
        env = self.check(r.tenure_window(obs(tick=32)), kind="tenure_window", rule="tenure_window")
        self.assertEqual(env["payload"]["coverage"], "complete")
        self.assertEqual(env["payload"]["end"], {"season": S + 1, "tick": 1})
        self.assertEqual(env["source_event_id"], "mu:tenure:2026:32:7")
        partial = r.tenure_window(obs(), coverage="partial", source="CATCHUP", flags=["cause_unknown"])
        self.check(partial, kind="tenure_window", rule="tenure_window", confidence="medium")
        self.assertEqual(partial["payload"]["coverage"], "partial")
        self.assertEqual(partial["payload"]["flags"], ["cause_unknown"])

    def test_no_tenure_for_free_agents_or_history(self):
        self.assertIsNone(r.tenure_window(obs(team_id=None)))
        self.assertIsNone(r.tenure_window(obs(origin="HISTORY")))


class MoveTest(EnvelopeChecks):
    def kinds(self, events):
        return [(e["kind"], e["payload"].get("treatment_kind") or e["payload"].get("situation_kind"),
                 e["payload"].get("state") or e["payload"].get("outcome"), e["target"]["team_key"]) for e in events]

    def test_release_is_dismissal_and_departure(self):
        out = r.move_events(side(3, 24), side(None), {"kind": "RELEASE", "ref": "league_office_runs:12"})
        self.assertEqual(out["flags"], [])
        self.assertEqual(self.kinds(out["events"]), [("treatment", "dismissal", "negative", "team:3"),
                                                     ("situation", "departure", "negative", "team:3")])
        t, d = out["events"]
        self.check(t, kind="treatment", rule="release_dismissal")
        self.check(d, kind="situation", rule="departure_release")
        self.assertEqual(t["payload"]["significance"], "meaningful")
        self.assertEqual(t["source_event_id"], "mu:release:2026:25:7")
        self.assertEqual(d["source_event_id"], "mu:depart:2026:25:7:3")
        self.assertEqual(t["attribution"]["responsible"], [r.team_ref(3)])
        self.assertEqual(t["attribution"]["reason_code"], "RELEASE")
        self.assertEqual(t["source_refs"], [{"namespace": "mu-producer", "key": "league_office_runs:12"}])

    def test_jr_move_reason_code(self):
        out = r.move_events(side(3), side(None), {"kind": "RELEASE", "ref": "franchise_pending_moves:4",
                                                  "jr_move": True})
        self.assertTrue(all(e["attribution"]["reason_code"] == "JR_MOVE" for e in out["events"]))

    def test_expiry_is_departure_only(self):
        out = r.move_events(side(3), side(None), {"kind": "EXPIRY"})
        self.assertEqual(self.kinds(out["events"]), [("situation", "departure", "uncertain", "team:3")])
        self.check(out["events"][0], kind="situation", rule="departure_expiry")

    def test_trade_is_departure_and_arrival_without_treatment(self):
        out = r.move_events(side(3, 10), side(4, 11), {"kind": "TRADE", "ref": "transactions:5"})
        self.assertEqual(self.kinds(out["events"]), [("situation", "departure", "uncertain", "team:3"),
                                                     ("situation", "arrival", "uncertain", "team:4")])
        self.assertEqual(out["events"][1]["source_event_id"], "mu:arrive:2026:11:7:4")

    def test_release_then_claimed(self):
        out = r.move_events(side(3, 10), side(4, 11), {"kind": "RELEASE", "ref": "transactions:6"})
        self.assertEqual([k[:2] for k in self.kinds(out["events"])],
                         [("treatment", "dismissal"), ("situation", "departure"), ("situation", "arrival")])

    def test_draft_and_signing_arrivals(self):
        draft = r.move_events(None, side(4, 29), {"kind": "DRAFT", "ref": "transactions:7"})
        self.assertEqual(self.kinds(draft["events"]), [("situation", "arrival", "positive", "team:4")])
        self.check(draft["events"][0], kind="situation", rule="arrival_draft")
        big = r.move_events(side(None), side(4, 26), {"kind": "SIGN", "above_minimum": True})
        small = r.move_events(side(None), side(4, 26), {"kind": "SIGN", "above_minimum": False})
        self.assertEqual(big["events"][0]["payload"]["state"], "positive")
        self.assertEqual(small["events"][0]["payload"]["state"], "uncertain")

    def test_unknown_cause_gives_no_treatment(self):
        for prev, curr, cause in ((side(3), side(None), None), (side(3), side(4), None),
                                  (side(None), side(4), None), (side(3), side(None), {"kind": "TRADE"}),
                                  (side(3), side(4), {"kind": "EXPIRY"})):
            self.assertEqual(r.move_events(prev, curr, cause), {"events": [], "flags": ["cause_unknown"]})

    def test_no_change_and_history_skipped(self):
        self.assertEqual(r.move_events(side(3), side(3), {"kind": "RELEASE"}), {"events": [], "flags": []})
        self.assertEqual(r.move_events(side(3, origin="HISTORY"), side(None, origin="HISTORY"),
                                       {"kind": "RELEASE"}), {"events": [], "flags": []})

    def test_resign_is_recognition(self):
        c = {"contract_id": 88, "player_id": 7, "team_id": 3, "season": S, "tick": 25, "status": "ACTIVE",
             "years_remaining": 3, "superseded_contract_id": 40, "superseded_team_id": 3}
        env = self.check(r.recognition(c), kind="treatment", rule="resign_recognition")
        self.assertEqual((env["payload"]["treatment_kind"], env["payload"]["outcome"],
                          env["payload"]["significance"]), ("recognition", "positive", "meaningful"))
        self.assertEqual(env["source_event_id"], "mu:contract:88")
        self.assertIsNone(r.recognition(dict(c, superseded_team_id=5)))          # a new team's contract: a signing
        self.assertIsNone(r.recognition(dict(c, superseded_contract_id=None)))
        self.assertIsNone(r.recognition(dict(c, status="SUPERSEDED")))
        self.assertIsNone(r.recognition(dict(c, origin="HISTORY")))


class SituationTest(EnvelopeChecks):
    def injury(self, **kw):
        base = {"injury_id": 55, "player_id": 7, "team_id": 3, "season": S, "tick": 8, "injury_type": "Ankle",
                "weeks_remaining": 2, "is_ir": False, "origin": None}
        base.update(kw)
        return base

    def test_injury_threshold(self):
        env = self.check(r.injury_situation(self.injury()), kind="situation", rule="injury")
        self.assertEqual((env["payload"]["situation_kind"], env["payload"]["state"]), ("injury", "negative"))
        self.assertEqual(env["payload"]["valid_until"], {"season": S, "tick": 10})
        self.assertEqual(env["source_event_id"], "mu:injury:55")
        self.assertIsNone(r.injury_situation(self.injury(weeks_remaining=1)))
        self.assertIsNotNone(r.injury_situation(self.injury(weeks_remaining=0, is_ir=True)))
        self.assertIsNone(r.injury_situation(self.injury(weeks_remaining=0, is_ir=True, injury_type="NONE")))
        self.assertIsNone(r.injury_situation(self.injury(origin="HISTORY")))

    def test_injury_healing_resolves(self):
        env = self.check(r.injury_resolved(self.injury(), S, 10), kind="situation", rule="injury_resolved")
        self.assertEqual(env["payload"]["state"], "resolved")
        self.assertEqual(env["occurred_at"], {"season": S, "tick": 10})
        self.assertEqual(env["payload"]["outcome_refs"], ["mu:injury:55"])
        self.assertIsNone(r.injury_resolved(self.injury(weeks_remaining=1), S, 9))

    def test_award(self):
        a = {"award_id": 12, "season": S, "tick": 22, "award_type": "MVP", "team_id": 3, "player_id": 7}
        env = self.check(r.award_situation(a), kind="situation", rule="award")
        self.assertEqual((env["payload"]["state"], env["source_event_id"]), ("positive", "mu:award:12"))
        coach = r.award_situation({"award_id": 13, "season": S, "tick": 22, "team_id": 3, "coach_id": 4})
        self.assertEqual(coach["subject_alias"]["namespace"], "db-coach")
        self.assertIsNone(r.award_situation(dict(a, origin="HISTORY")))

    def test_streaks(self):
        players = [{"player_id": 9}, {"player_id": 7}, {"player_id": 8, "origin": "HISTORY"}]
        wins = [{"tick": 1, "result": "L"}, {"tick": 2, "result": "W"}, {"tick": 3, "result": "W"},
                {"tick": 5, "result": "W"}]
        evs = r.streak_events(3, S, 5, wins, players, next_game_tick=6)
        self.assertEqual([e["subject_alias"]["external_id"] for e in evs], ["7", "9"])      # HISTORY skipped
        self.assertEqual({e["source_event_id"] for e in evs}, {"mu:result:2026:3:win-streak:5"})
        self.check(evs[0], kind="situation", rule="win_streak")
        self.assertEqual((evs[0]["payload"]["state"], evs[0]["payload"]["valid_until"]),
                         ("positive", {"season": S, "tick": 6}))
        self.assertEqual(r.streak_events(3, S, 3, wins, players), [])                        # only 2 so far
        self.assertEqual(r.streak_events(3, S, 6, wins + [{"tick": 6, "result": "W"}], players), [])   # 4th: once
        losses = [{"tick": t, "result": "L"} for t in (1, 2, 3)]
        self.assertEqual(r.streak_events(3, S, 3, losses, players)[0]["payload"]["state"], "negative")
        self.assertEqual(r.streak_events(3, S, 4, losses, players), [])                      # no game this tick

    def test_season_outcomes(self):
        players = [{"player_id": 7}]
        expected = {("MISSED_PLAYOFFS", None): ("negative", "missed-playoffs"),
                    ("MADE_PLAYOFFS", None): ("positive", "made-playoffs"),
                    ("LOST_ROUND", 2): ("uncertain", "lost-round-2"),
                    ("WON_SUPER_BOWL", None): ("positive", "won-super-bowl")}
        for (kind, rnd), (state, label) in expected.items():
            (env,) = r.season_outcome_events(3, S, 23, {"kind": kind, "round": rnd}, players)
            self.check(env, kind="situation", rule="season_outcome")
            self.assertEqual(env["payload"]["state"], state)
            self.assertEqual(env["source_event_id"], f"mu:result:2026:3:{label}:23")
            self.assertEqual(env["payload"]["valid_until"], {"season": S + 1, "tick": 1})
        with self.assertRaises(ValueError):
            r.season_outcome_events(3, S, 23, {"kind": "TIED"}, players)

    def test_contract_year(self):
        c = {"contract_id": 5, "player_id": 7, "team_id": 3, "season": S, "status": "ACTIVE", "years_remaining": 1}
        env = self.check(r.contract_year(c, 1), kind="situation", rule="contract_year")
        self.assertEqual((env["payload"]["situation_kind"], env["payload"]["state"]), ("job_pressure", "uncertain"))
        self.assertEqual(env["payload"]["valid_until"], {"season": S, "tick": 25})
        self.assertEqual(env["source_event_id"], "mu:contract-year:2026:7")
        self.assertIsNone(r.contract_year(c, 2))
        self.assertIsNone(r.contract_year(dict(c, years_remaining=2), 1))
        self.assertIsNone(r.contract_year(dict(c, status="SUPERSEDED"), 1))


class CoachFactTest(EnvelopeChecks):
    def move(self, **kw):
        base = {"staff_move_id": 31, "season": S, "tick": 19, "team_id": 3, "role": "HC", "action": "LEFT",
                "source": None, "coach_id": 4, "coordinator_id": None, "fired": True}
        base.update(kw)
        return base

    def test_fired_is_dismissal(self):
        (env,) = r.staff_move_events(self.move())
        self.check(env, kind="treatment", rule="coach_fired")
        self.assertEqual((env["payload"]["treatment_kind"], env["payload"]["outcome"]), ("dismissal", "negative"))
        self.assertEqual(env["source_event_id"], "mu:staff:31")
        self.assertEqual(env["subject_alias"], {"role": "coach", "namespace": "db-coach", "external_id": "4"})
        self.assertEqual(r.staff_move_events(self.move(fired=False)), [])               # poached or retired
        self.assertEqual(r.staff_move_events(self.move(action="REPLACED")), [])

    def test_hired_and_promoted(self):
        (hired,) = r.staff_move_events(self.move(action="HIRED", source="RETREAD", fired=False))
        self.check(hired, kind="situation", rule="coach_hired")
        self.assertEqual((hired["payload"]["situation_kind"], hired["payload"]["state"]), ("arrival", "positive"))
        (promo,) = r.staff_move_events(self.move(action="HIRED", source="PROMOTED", role="OC", coach_id=None,
                                                 coordinator_id=8))
        self.check(promo, kind="treatment", rule="coach_promoted")
        self.assertEqual(promo["payload"]["treatment_kind"], "recognition")
        self.assertEqual(promo["subject_alias"]["namespace"], "db-coordinator")
        self.assertEqual(r.staff_move_events(self.move(coach_id=None)), [])

    def test_hot_seat_pressure(self):
        row = {"season": S, "tick": 19, "team_id": 3, "role": "HC", "coach_id": 4, "score": 0.5, "fired": False}
        env = self.check(r.hot_seat_pressure(row), kind="situation", rule="hot_seat_kept")
        self.assertEqual((env["payload"]["situation_kind"], env["source_event_id"]),
                         ("job_pressure", "mu:hotseat:2026:3:HC"))
        self.assertIsNone(r.hot_seat_pressure(dict(row, score=0.49)))
        self.assertIsNone(r.hot_seat_pressure(dict(row, score=1.4, fired=True)))
        self.assertIsNone(r.hot_seat_pressure(dict(row, score=0.9, firing_line=2.0)))
        dc = r.hot_seat_pressure({"season": S, "tick": 19, "team_id": 3, "role": "DC", "coordinator_id": 8,
                                  "score": 0.7, "fired": False})
        self.assertEqual(dc["source_event_id"], "mu:hotseat:2026:3:DC")

    def test_firing_line_matches_hot_seat_engine(self):
        try:
            hot = importlib.import_module("engines.coach_hot_seat")
        except ImportError:
            self.skipTest("engines.coach_hot_seat not importable")
        self.assertEqual(r.FIRING_LINE, hot.HOT_SEAT_LINE)


class DeterminismTest(unittest.TestCase):
    def test_source_ids(self):
        self.assertEqual(r.role_source_id(S, 6, 7), "mu:role:2026:6:7")
        self.assertEqual(r.tenure_source_id(S, 6, 7), "mu:tenure:2026:6:7")
        self.assertEqual(r.release_source_id(S, 6, 7), "mu:release:2026:6:7")
        self.assertEqual(r.contract_source_id(9), "mu:contract:9")
        self.assertEqual(r.arrival_source_id(S, 6, 7, 3), "mu:arrive:2026:6:7:3")
        self.assertEqual(r.departure_source_id(S, 6, 7, 3), "mu:depart:2026:6:7:3")
        self.assertEqual(r.injury_source_id(4), "mu:injury:4")
        self.assertEqual(r.award_source_id(4), "mu:award:4")
        self.assertEqual(r.result_source_id(S, 3, "win-streak", 5), "mu:result:2026:3:win-streak:5")
        self.assertEqual(r.contract_year_source_id(S, 7), "mu:contract-year:2026:7")
        self.assertEqual(r.staff_source_id(31), "mu:staff:31")
        self.assertEqual(r.hot_seat_source_id(S, 3, "HC"), "mu:hotseat:2026:3:HC")

    def test_envelopes_are_reproducible(self):
        def build():
            return [r.role_window(obs(tick=6), hist(["STARTER"] * 5)), r.tenure_window(obs()),
                    *r.move_events(side(3), side(None), {"kind": "RELEASE", "ref": "transactions:1"})["events"]]
        a, b = build(), build()
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertEqual(len({e["event_id"] for e in a}), len(a))

    def test_facets_split_one_occurrence(self):
        evs = r.season_outcome_events(3, S, 23, {"kind": "MISSED_PLAYOFFS"}, [{"player_id": 1}, {"player_id": 2}])
        self.assertEqual(len({e["source_event_id"] for e in evs}), 1)
        self.assertEqual([r.facet_key(e) for e in evs], ["player:db-player:1|team:3|situation",
                                                         "player:db-player:2|team:3|situation"])

    def test_constants(self):
        self.assertEqual((r.ROLE_ESTABLISHED_WEEKS, r.INJURY_SITUATION_WEEKS, r.STREAK_GAMES, r.HOT_SEAT_PRESSURE),
                         (4, 2, 3, 0.5))
        self.assertEqual(PRODUCER_RULES_VERSION, "producers-v1")
        if rel_events is not None and hasattr(rel_events, "RELATIONSHIP_SCHEMA_VERSION"):
            self.assertEqual(r.RELATIONSHIP_SCHEMA_VERSION, rel_events.RELATIONSHIP_SCHEMA_VERSION)

    def test_stage_ticks_match_league_office(self):
        from pipeline import league_office as lo
        self.assertEqual(r.RESIGN_TICK, lo.STAGE_WEEKS["RESIGN"])
        self.assertEqual(r.OFFSEASON_START_TICK, lo.STAGE_WEEKS["OFFSEASON_START"])
        self.assertEqual(cal.FIRST_OFFSEASON_TICK, lo.STAGE_WEEKS["OFFSEASON_START"])
        self.assertEqual(max(lo.STAGE_WEEKS.values()), cal.TICKS_PER_SEASON)


if __name__ == "__main__":
    unittest.main()
