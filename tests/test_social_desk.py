"""Unit tests for the Agent Wire social desk planner (newsroom/social_desk.py). No database required.

Run: python3 -m unittest tests.test_social_desk
"""
import collections
import json
import re
import unittest

from newsroom.social_desk import ROLE_CAPS, TARGET_POSTS, Accounts, plan_social_week, tidy_name

PUNDITS = ("STEPHEN_A", "MCAFEE", "SHANNON", "OCHOCINCO", "JJ_WATT", "RG3", "COWHERD", "WINGO", "EISEN", "CLARK", "SKIP_TWEET")

DIVS = [f"{c} {d}" for c in ("AFC", "NFC") for d in ("East", "North", "South", "West")]


def league(season=2035, week=9, played_weeks=None, playoff=None, articles=None, projections=True):
    """A synthetic 32-team league. Regular-season games every week before `week` (or `played_weeks`);
    `playoff` = {week: [(away, home, status)]} adds postseason games."""
    teams = {i: {"id": i, "name": f"Team{i}", "city": f"City{i}", "abbr": f"T{i:02d}", "conference": DIVS[(i - 1) // 4][:3],
                 "division": DIVS[(i - 1) // 4], "popularity": float(100 - i)} for i in range(1, 33)}
    accounts = []
    for t in teams:
        for n in range(2):
            accounts.append({"agent_id": f"fan_{t}_{n}", "archetype": "DIE_HARD_FAN", "team_id": t, "handle": f"@fan{t}_{n}", "display_name": "x"})
        accounts.append({"agent_id": f"beat_{t}", "archetype": "LOCAL_BEAT_REPORTER", "team_id": t, "handle": f"@beat{t}", "display_name": "x"})
    for n in range(6):
        accounts.append({"agent_id": f"bw_{n}", "archetype": "BANDWAGON_FAN", "team_id": None, "handle": f"@bw{n}", "display_name": "x"})
    for i, (name, tid, pos) in enumerate((("Old Qb", 1, "QB"), ("Old Wr", 17, "WR"), ("Old Rb", 5, "RB"), ("Old Lb", 9, "LB"))):
        accounts.append({"agent_id": f"former_{i}", "archetype": "FORMER_PLAYER", "team_id": tid, "handle": f"@former{i}",
                         "display_name": name, "persistent_memory": f"I am {name}. I played {pos} for the City{tid} Team{tid} and retired before 2035."})
    for arch in ("STEPHEN_A", "MCAFEE", "SHANNON", "OCHOCINCO", "JJ_WATT", "RG3", "COWHERD", "WINGO", "EISEN", "CLARK",
                 "SKIP_TWEET", "BERRY", "YATES", "FANTASY_GURU", "NFL_MEMES", "DRAFT_SICKO", "SKIP_BAYLESS", "BARNWELL",
                 "ROUNDTABLE_PANEL"):
        accounts.append({"agent_id": arch.lower(), "archetype": arch, "team_id": None, "handle": f"@{arch.lower()}", "display_name": "x"})

    games, gid = [], 0
    weeks = played_weeks if played_weeks is not None else range(1, week)
    for w in list(weeks) + [week]:
        for i in range(1, 17):
            gid += 1
            done = w < week
            games.append({"id": gid, "week": w, "home_team_id": i, "away_team_id": i + 16,
                          "home_score": (20 + (i * w) % 17) if done else 0, "away_score": (13 + (i * 3 + w) % 21) if done else 0,
                          "status": "COMPLETED" if done else "SCHEDULED", "game_time": "Sun 1:00 PM",
                          "game_type": "REGULAR_SEASON", "excitement_index": 40.0 + i * 3, "projected_excitement": 40.0 + i * 2})
    for w, matchups in (playoff or {}).items():
        games = [g for g in games if g["week"] != w]
        for away, home, status in matchups:
            gid += 1
            games.append({"id": gid, "week": w, "home_team_id": home, "away_team_id": away,
                          "home_score": 27 if status == "COMPLETED" else 0, "away_score": 20 if status == "COMPLETED" else 0,
                          "status": status, "game_time": "Sat 4:30 PM", "game_type": "PLAYOFF",
                          "excitement_index": 80.0, "projected_excitement": 70.0})
    lines = []
    for g in games:
        if g["status"] != "COMPLETED" or g["week"] >= week:
            continue
        for team in (g["home_team_id"], g["away_team_id"]):
            for pos, k in (("QB", 0), ("HB", 1), ("WR", 2)):
                lines.append({"week": g["week"], "game_id": g["id"], "team_id": team, "position": pos,
                              "short_name": f"P.{team}{pos}", "name": f"Player {team}{pos}", "player_id": team * 10 + k,
                              "popularity": float(team), "completions": 20 if pos == "QB" else 0, "pass_attempts": 30 if pos == "QB" else 0,
                              "pass_yards": 250 if pos == "QB" else 0, "pass_tds": 2 if pos == "QB" else 0, "interceptions": 0,
                              "rush_attempts": 15 if pos == "HB" else 0, "rush_yards": 60 + team if pos == "HB" else 0, "rush_tds": 1 if pos == "HB" else 0,
                              "receptions": 6 if pos == "WR" else 0, "rec_yards": 70 + g["id"] % 50 if pos == "WR" else 0, "rec_tds": 1 if pos == "WR" else 0,
                              "fantasy_points": float(10 + team + g["id"] % 7)})
                if team == 2 and pos == "WR" and g["week"] == week - 1:   # a star WR's flop last week
                    lines[-1].update(receptions=1, rec_yards=8, rec_tds=0, fantasy_points=1.8)
    ctx = {"season": season, "week": week, "recap_week": week - 1, "teams": teams, "accounts": accounts,
           "games": [g for g in games if g["week"] < week and g["status"] == "COMPLETED"],
           "recap_games": [g for g in games if g["week"] == week - 1 and g["status"] == "COMPLETED"],
           "preview_games": [g for g in games if g["week"] == week],
           "player_lines": lines, "articles": articles or [],
           "projections": [{"name": f"Player {t}{p}", "team_id": t, "position": p, "projected_points": 30.0 - t / 2,
                            "matchup_stars": 3, "pos_rank": None} for t in range(1, 33) for p in ("QB", "HB", "WR", "TE")] if projections else []}
    return ctx


def by_kind(plan):
    out = collections.defaultdict(list)
    for a in plan["assignments"]:
        out[a["kind"]].append(a)
    return out


class SocialDeskTests(unittest.TestCase):
    def test_regular_week_is_100_posts_with_one_reaction_per_team(self):
        plan = plan_social_week(league(week=9))
        self.assertEqual(sum(plan["counts"].values()), TARGET_POSTS)
        reacting_teams = {a["team_id"] for a in by_kind(plan)["REACTION"] if a["archetype"] == "DIE_HARD_FAN"}
        self.assertEqual(reacting_teams, set(range(1, 33)))

    def test_only_die_hards_react_to_games(self):
        plan = plan_social_week(league(week=9))
        self.assertEqual({a["archetype"] for a in by_kind(plan)["REACTION"]}, {"DIE_HARD_FAN"})
        self.assertEqual(len(by_kind(plan)["REACTION"]), 32)

    def test_player_first_mix(self):
        plan = plan_social_week(league(week=9))
        kinds = by_kind(plan)
        self.assertGreaterEqual(len(kinds["PERFORMANCE"]), 14)
        self.assertNotIn("DIE_HARD_FAN", {a["archetype"] for a in kinds["PERFORMANCE"]})
        self.assertIn("BANDWAGON_FAN", {a["archetype"] for a in kinds["PERFORMANCE"]})
        self.assertIn("FORMER_PLAYER", {a["archetype"] for a in kinds["PERFORMANCE"]})
        self.assertEqual(len(kinds["COMPARISON"]), 10)
        self.assertTrue(10 <= len(kinds["REPLY"]) <= 15)

    def test_bad_games_clown_the_flop(self):
        plan = plan_social_week(league(week=9))
        bad = by_kind(plan)["BAD_GAME"]
        self.assertTrue(bad)
        self.assertEqual(bad[0]["subject"]["label"], "Player 2WR (WR, T02)")
        self.assertIn(bad[0]["archetype"], ("BANDWAGON_FAN", "NFL_MEMES") + tuple(PUNDITS))
        self.assertGreater(bad[0]["facts"]["season_average_before_this_game_ppr"], bad[0]["facts"]["fantasy_points_ppr"])

    def test_comparisons(self):
        comps = by_kind(plan_social_week(league(week=9)))["COMPARISON"]
        fans = [a for a in comps if a["archetype"] == "DIE_HARD_FAN"]
        self.assertEqual(len(fans), 5)
        for a in fans:
            self.assertEqual(a["facts"]["your_player"]["player"].split("(")[1].split(",")[0],
                             a["facts"]["their_player"]["player"].split("(")[1].split(",")[0])   # same position
            self.assertIn(f"City{a['team_id']} Team{a['team_id']}", a["facts"]["your_player"]["player"])
        legends = [a for a in comps if a["archetype"] == "FORMER_PLAYER"]
        self.assertEqual(len(legends), 3)
        for a in legends:
            self.assertTrue(a["slant_team"].startswith("City"))
            self.assertIn("top_five_this_season", a["facts"])

    def test_ex_players_are_not_filed_under_their_old_team(self):
        plan = plan_social_week(league(week=9))
        self.assertFalse([a for a in plan["assignments"] if a["archetype"] == "FORMER_PLAYER" and a["team_id"]])

    def test_shares_reduce_reactions_and_keep_the_total(self):
        arts = [{"id": i, "persona": p, "team_id": None, "headline": f"A{i}", "excerpt": "e", "slot": f"Slot {i}",
                 "article_type": "X"} for i, p in enumerate(["BARNWELL", "SKIP_BAYLESS", "ROUNDTABLE_PANEL", "YATES"], 1)]
        plan = plan_social_week(league(week=9, articles=arts))
        shares = by_kind(plan)["SHARE"]
        self.assertEqual(len(shares), 3)                       # the roundtable panel never posts
        skip = next(s for s in shares if s["subject"]["id"] == 2)
        self.assertEqual(skip["archetype"], "SKIP_TWEET")       # Skip's column is shared from @RealSkipBayless
        self.assertEqual(sum(plan["counts"].values()), TARGET_POSTS)

    def test_previews_never_show_this_weeks_scores(self):
        ctx = league(week=9)
        for g in ctx["preview_games"]:                          # replanning an old week: games already played
            g.update(status="COMPLETED", home_score=99, away_score=98)
        plan = plan_social_week(ctx)
        text = json.dumps(plan)
        self.assertNotIn("99", text)
        self.assertFalse([a for a in by_kind(plan)["PREVIEW"] if re.search(r" \d+ @ \S+ \d+$", a["subject"]["label"])])

    def test_weekly_caps_hold(self):
        plan = plan_social_week(league(week=9))
        acc = Accounts(league()["accounts"], seed="x")
        uses = collections.Counter(a["agent_id"] for a in plan["assignments"] if a["agent_id"] and a["kind"] != "SHARE")
        for agent_id, n in uses.items():
            self.assertLessEqual(n, ROLE_CAPS[acc.role[agent_id] or "pundit"], agent_id)

    def test_fantasy_start_sit_only_in_fantasy_season(self):
        self.assertEqual(len([a for a in by_kind(plan_social_week(league(week=9)))["PREVIEW"] if a["subject"]["type"] == "fantasy"]), 2)
        self.assertFalse([a for a in by_kind(plan_social_week(league(week=18)))["PREVIEW"] if a["subject"]["type"] == "fantasy"])

    def test_plan_is_deterministic(self):
        self.assertEqual(plan_social_week(league(week=9)), plan_social_week(league(week=9)))

    def test_preseason_fills_with_previews(self):
        plan = plan_social_week(league(week=1, played_weeks=[]))
        kinds = by_kind(plan)
        self.assertFalse(kinds["REACTION"])
        self.assertFalse(kinds["PERFORMANCE"])
        self.assertEqual(sum(plan["counts"].values()), TARGET_POSTS)
        self.assertEqual({a["team_id"] for a in kinds["PREVIEW"] if a["archetype"] == "DIE_HARD_FAN"}, set(range(1, 33)))

    def test_postseason_keeps_100_and_eliminated_fans_post_about_players(self):
        # Week 20 (Divisional) after a Wild Card round: 4 teams scheduled this week, 6 played last week.
        wc = [(17, 1, "COMPLETED"), (18, 2, "COMPLETED"), (19, 3, "COMPLETED"), (20, 4, "COMPLETED"), (21, 5, "COMPLETED"), (22, 6, "COMPLETED")]
        div = [(1, 7, "SCHEDULED"), (2, 8, "SCHEDULED")]
        ctx = league(week=20, played_weeks=range(1, 19), playoff={19: wc, 20: div})
        plan = plan_social_week(ctx)
        self.assertEqual(sum(plan["counts"].values()), TARGET_POSTS)
        player_posts = by_kind(plan)["PLAYER"]
        self.assertTrue(player_posts)
        alive = {1, 2, 7, 8}
        self.assertTrue(all(a["team_id"] not in alive for a in player_posts))
        self.assertFalse([a for a in by_kind(plan)["PREVIEW"] if a["subject"]["type"] == "fantasy"])

    def test_super_bowl_bye_week_keeps_conference_champions_alive(self):
        conf = [(1, 2, "COMPLETED"), (17, 18, "COMPLETED")]       # home teams 2 and 18 win
        ctx = league(week=22, played_weeks=range(1, 19), playoff={21: conf, 22: []})
        plan = plan_social_week(ctx)
        self.assertEqual(sum(plan["counts"].values()), TARGET_POSTS)
        self.assertFalse([a for a in by_kind(plan)["PLAYER"] if a["team_id"] in (2, 18)])


class NameTests(unittest.TestCase):
    def test_tidy_name(self):
        self.assertEqual(tidy_name("OMAR COOPER JR."), "Omar Cooper Jr.")
        self.assertEqual(tidy_name("D'ANDRE SMITH III"), "D'Andre Smith III")
        self.assertEqual(tidy_name("Ka'Von Franklin"), "Ka'Von Franklin")


if __name__ == "__main__":
    unittest.main()
