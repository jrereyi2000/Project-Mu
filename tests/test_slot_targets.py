"""Unit tests for Editorial Matrix slot targeting (newsroom/slot_targets.py). No database required.

Run: python3 -m unittest tests.test_slot_targets
"""
import unittest

from newsroom.slot_targets import (
    LeagueSnapshot,
    mathematically_eliminated_team_ids,
    rank_games_by_interest,
    resolve_slot_targets,
)

PRIMER_SLOTS = [("Slot 6", "LOCAL_MATCHUP_PRIMER"), ("Slot 7", "LOCAL_MATCHUP_PRIMER"), ("Slot 8", "LOCAL_MATCHUP_PRIMER")]


def league(season=2035, week=7, **kwargs):
    """32 teams: IDs 1-16 AFC, 17-32 NFC. Popularity = team ID (higher ID = more popular)."""
    teams = {
        t: {"name": f"Team{t}", "conference": "AFC" if t <= 16 else "NFC", "wins": 0, "losses": 0, "popularity": float(t)}
        for t in range(1, 33)
    }
    snap = LeagueSnapshot(season=season, week=week, teams=teams, teams_with_beat_reporters=set(teams))
    for k, v in kwargs.items():
        setattr(snap, k, v)
    return snap


def game(gid, home, away, excitement=0.0, projected=True):
    return {"game_id": gid, "home_team_id": home, "away_team_id": away, "excitement": excitement, "projected": projected}


def result(gid, home, away, hs, as_):
    return {"game_id": gid, "home_team_id": home, "away_team_id": away, "home_score": hs, "away_score": as_}


class PrimerTargetTests(unittest.TestCase):
    def test_top_three_games_by_excitement(self):
        snap = league(week_games=[game(1, 1, 2, 50), game(2, 3, 4, 90), game(3, 5, 6, 70), game(4, 7, 8, 80)])
        targets = resolve_slot_targets(PRIMER_SLOTS, snap)
        self.assertEqual([t["game_id"] for t in targets], [2, 4, 3])

    def test_side_with_fewer_primers_this_season_wins(self):
        # Team 4 is more popular, but already had 2 primers (outside the rotation window); team 3 had none.
        snap = league(week=9, week_games=[game(1, 3, 4, 90)], primer_history=[(4, 2), (4, 4)])
        target = resolve_slot_targets(PRIMER_SLOTS[:1], snap)[0]
        self.assertEqual(target["team_id"], 3)
        self.assertEqual(target["opponent_team_id"], 4)

    def test_ties_go_to_more_popular_side(self):
        snap = league(week_games=[game(1, 3, 4, 90)])
        self.assertEqual(resolve_slot_targets(PRIMER_SLOTS[:1], snap)[0]["team_id"], 4)

    def test_rotation_skips_recently_primed_teams(self):
        # Both teams in the top game had primers within the last 2 weeks, so it is skipped.
        snap = league(week=7, week_games=[game(1, 1, 2, 99), game(2, 3, 4, 50)], primer_history=[(1, 6), (2, 5)])
        self.assertEqual(resolve_slot_targets(PRIMER_SLOTS[:1], snap)[0]["game_id"], 2)

    def test_rotation_window_is_two_weeks(self):
        snap = league(week=7, week_games=[game(1, 1, 2, 99)], primer_history=[(1, 4), (2, 4)])
        self.assertEqual(resolve_slot_targets(PRIMER_SLOTS[:1], snap)[0]["game_id"], 1)

    def test_rotation_relaxed_when_not_enough_games(self):
        snap = league(week=7, week_games=[game(1, 1, 2, 99), game(2, 3, 4, 50)], primer_history=[(1, 6), (2, 6)])
        targets = resolve_slot_targets(PRIMER_SLOTS[:2], snap)
        self.assertEqual([t["game_id"] for t in targets], [2, 1])

    def test_teams_without_beat_reporters_are_skipped(self):
        snap = league(week_games=[game(1, 1, 2, 99)])
        snap.teams_with_beat_reporters -= {2}
        self.assertEqual(resolve_slot_targets(PRIMER_SLOTS[:1], snap)[0]["team_id"], 1)

    def test_unfilled_slots_are_none(self):
        snap = league(week_games=[game(1, 1, 2, 99)])
        targets = resolve_slot_targets(PRIMER_SLOTS, snap)
        self.assertIsNotNone(targets[0])
        self.assertEqual(targets[1:], [None, None])

    def test_super_bowl_week_afc_then_nfc(self):
        snap = league(week=23, week_games=[game(1, 20, 5, 95)])
        slots = [("Slot 6", "LOCAL_MATCHUP_PRIMER"), ("Slot 7", "LOCAL_MATCHUP_PRIMER")]
        afc, nfc = resolve_slot_targets(slots, snap)
        self.assertEqual((afc["team_id"], nfc["team_id"]), (5, 20))
        self.assertEqual(afc["opponent_team_id"], 20)


class InterestRankingTests(unittest.TestCase):
    def test_division_game_bonus(self):
        snap = league(week_games=[game(1, 1, 2, 60), game(2, 3, 4, 58)])
        snap.teams[3]["division"] = snap.teams[4]["division"] = "AFC East"
        ranked = rank_games_by_interest(snap)
        self.assertEqual([g["game_id"] for g in ranked], [2, 1])  # 58 + 3 beats 60
        self.assertTrue(ranked[0]["division_game"])

    def test_same_division_name_in_other_conference_is_not_a_division_game(self):
        snap = league(week_games=[game(1, 1, 20, 60)])
        snap.teams[1]["division"] = snap.teams[20]["division"] = "East"
        self.assertFalse(rank_games_by_interest(snap)[0]["division_game"])

    def test_playoff_race_bonus_per_team_alive(self):
        snap = league(week=16, week_games=[game(1, 9, 10, 60), game(2, 2, 3, 57)])
        for t in range(1, 8):
            snap.teams[t]["wins"] = 12
        # Teams 9 and 10 are eliminated (no remaining games, 7 teams ahead); teams 2 and 3 are alive.
        ranked = rank_games_by_interest(snap)
        self.assertEqual([g["game_id"] for g in ranked], [2, 1])  # 57 + 2×2 beats 60
        self.assertEqual(ranked[0]["teams_in_race"], 2)

    def test_no_race_bonus_in_postseason(self):
        snap = league(week=20, week_games=[game(1, 1, 2, 60)])
        self.assertEqual(rank_games_by_interest(snap)[0]["interest"], 60)

    def test_unprojected_games_rank_on_stakes_only(self):
        snap = league(week_games=[game(1, 1, 2, None), game(2, 3, 4, 50)])
        self.assertEqual([g["game_id"] for g in rank_games_by_interest(snap)], [2, 1])

    def test_primer_note_uses_display_scale(self):
        snap = league(week_games=[game(1, 1, 2, 58.2)])
        note = resolve_slot_targets(PRIMER_SLOTS[:1], snap)[0]["note"]
        self.assertIn("projected Excitement Index 79/100", note)


class ExitTargetTests(unittest.TestCase):
    def test_wild_card_exits_exclude_playoff_teams_and_bye(self):
        # AFC Wild Card games involve teams 1-6; team 7 has the best AFC record (bye). NFC games involve 17-22; team 23 is the NFC bye.
        wc_games = [game(1, 1, 2, 60), game(2, 3, 4, 70), game(3, 5, 6, 80), game(4, 17, 18, 50), game(5, 19, 20, 40), game(6, 21, 22, 30)]
        snap = league(week=19, week_games=wc_games)
        snap.teams[7]["wins"], snap.teams[23]["wins"] = 15, 14
        slots = [("Slot 6", "FRANCHISE_STATE_OF_THE_UNION"), ("Slot 7", "FRANCHISE_STATE_OF_THE_UNION"), ("Slot 8", "LOCAL_MATCHUP_PRIMER")]
        exit1, exit2, primer = resolve_slot_targets(slots, snap)
        # Most popular non-playoff teams are 32 and 31 (NFC, not playing, not the bye).
        self.assertEqual((exit1["team_id"], exit2["team_id"]), (32, 31))
        self.assertEqual(primer["game_id"], 3)

    def test_playoff_autopsies_use_prior_round_losers(self):
        snap = league(week=20, prior_week_results=[result(1, 1, 2, 10, 20), result(2, 3, 4, 30, 7), result(3, 17, 18, 24, 21), result(4, 19, 30, 3, 14)])
        slots = [("Slot 6", "PLAYOFF_AUTOPSY"), ("Slot 7", "PLAYOFF_AUTOPSY")]
        a, b = resolve_slot_targets(slots, snap)
        # Losers: 1, 4, 18, 19 -> two most popular: 19, 18
        self.assertEqual((a["team_id"], b["team_id"]), (19, 18))

    def test_targets_are_distinct_across_slot_types(self):
        # Team 32 is in the top primer game and is also the most popular loser; it can only be used once.
        snap = league(week=20, week_games=[game(1, 32, 31, 99)], prior_week_results=[result(9, 1, 32, 30, 0), result(10, 2, 30, 30, 0)])
        slots = [("Slot 6", "PLAYOFF_AUTOPSY"), ("Slot 8", "LOCAL_MATCHUP_PRIMER")]
        autopsy, primer = resolve_slot_targets(slots, snap)
        self.assertEqual(primer["team_id"], 32)
        self.assertEqual(autopsy["team_id"], 30)


class EliminationTests(unittest.TestCase):
    def test_eliminated_when_seven_conference_teams_out_of_reach(self):
        snap = league(week=16)
        for t in range(1, 8):
            snap.teams[t]["wins"] = 10
        snap.teams[16]["wins"] = 6
        snap.remaining_regular_season_games = {16: 3}  # max 9 wins < 10
        self.assertIn(16, mathematically_eliminated_team_ids(snap))

    def test_not_eliminated_if_can_tie(self):
        snap = league(week=16)
        for t in range(1, 8):
            snap.teams[t]["wins"] = 10
        snap.teams[16]["wins"] = 7
        snap.remaining_regular_season_games = {16: 3}  # max 10 wins ties; tiebreakers ignored
        self.assertNotIn(16, mathematically_eliminated_team_ids(snap))

    def test_phase_4_state_of_the_union_prefers_popular_eliminated_team(self):
        snap = league(week=17)
        for t in range(1, 8):
            snap.teams[t]["wins"] = 12
        snap.teams[9]["wins"], snap.teams[14]["wins"] = 3, 5  # both eliminated (no remaining games recorded)
        target = resolve_slot_targets([("Slot 3", "FRANCHISE_STATE_OF_THE_UNION")], snap)[0]
        # Every AFC team other than 1-7 is eliminated; the most popular of them is 16.
        self.assertEqual(target["team_id"], 16)
        self.assertEqual(target["kind"], "ELIMINATED_FRANCHISE")

    def test_phase_4_falls_back_to_worst_record(self):
        snap = league(week=15)
        for t, info in snap.teams.items():
            info["wins"], info["losses"] = 7, 7
        snap.teams[12]["wins"], snap.teams[12]["losses"] = 2, 12
        snap.remaining_regular_season_games = {t: 4 for t in snap.teams}
        self.assertEqual(resolve_slot_targets([("Slot 4", "FRANCHISE_STATE_OF_THE_UNION")], snap)[0]["team_id"], 12)

    def test_state_of_the_union_outside_phase_4_is_untargeted(self):
        snap = league(week=8)
        self.assertEqual(resolve_slot_targets([("Slot 3", "FRANCHISE_STATE_OF_THE_UNION")], snap), [None])


class Week22SlotTargetTests(unittest.TestCase):
    def test_week_22_slot_8_is_super_bowl_historical_stakes(self):
        """Week 22 Slot 8 is configured as SUPER_BOWL_HISTORICAL_STAKES and is untargeted."""
        from newsroom.swarm_dispatcher import POSTSEASON_EDITORIAL_SLATES
        w22_slots = POSTSEASON_EDITORIAL_SLATES[22]
        slot_map = dict(w22_slots)
        self.assertEqual(slot_map["Slot 8"], "SUPER_BOWL_HISTORICAL_STAKES")

        snap = league(week=22, prior_week_results=[result(1, 1, 2, 24, 21), result(2, 17, 18, 30, 10)])
        targets = resolve_slot_targets(w22_slots, snap)
        slot_target_map = {slot[0]: t for slot, t in zip(w22_slots, targets)}
        self.assertIsNone(slot_target_map["Slot 8"])
        self.assertIsNotNone(slot_target_map["Slot 6"])
        self.assertIsNotNone(slot_target_map["Slot 7"])


if __name__ == "__main__":
    unittest.main()
