"""Unit tests for the Agent Wire social desk planner (newsroom/social_desk.py). No database required.

Run: python3 -m unittest tests.test_social_desk
"""
import collections
import json
import re
import unittest

from newsroom.social_desk import ROLE_CAPS, TARGET_POSTS, Accounts, Week, plan_social_week, plan_team_wakeup, tidy_name

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

    def test_no_fantasy_tweets_in_playoffs_or_offseason(self):
        import json
        for w in (19, 20, 21, 22, 23):
            wc = [(17, 1, "COMPLETED"), (18, 2, "COMPLETED")]
            div = [(1, 7, "SCHEDULED")]
            ctx = league(week=w, played_weeks=range(1, min(w, 19)), playoff={19: wc, 20: div})
            plan = plan_social_week(ctx)
            # 1. No fantasy accounts
            self.assertFalse([a for a in plan["assignments"] if a.get("archetype") in ("FANTASY_GURU", "BERRY", "YATES")])
            # 2. No fantasy subject types
            self.assertFalse([a for a in plan["assignments"] if a.get("subject", {}).get("type") == "fantasy"])
            # 3. No fantasy angles
            self.assertFalse([a for a in plan["assignments"] if a.get("angle") == "what it means for fantasy managers"])
            # 4. No fantasy kinds
            self.assertFalse([a for a in plan["assignments"] if a.get("kind") == "FANTASY_PREVIEW"])
            # 5. No PPR or fantasy points mentions in any assignment facts
            for a in plan["assignments"]:
                dumped = json.dumps(a).lower()
                for term in ("fantasy", "full-ppr", "ppr points", "ppr)", "fantasy_points"):
                    self.assertNotIn(term, dumped, f"Found {term} in week {w} assignment: {a}")

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


def dominate(ctx, team, us=40, them=10):
    """Make `team` win every completed game it played (its opponents lose them)."""
    for g in ctx["games"]:
        if team == g["home_team_id"]:
            g["home_score"], g["away_score"] = us, them
        elif team == g["away_team_id"]:
            g["home_score"], g["away_score"] = them, us


class BuzzTests(unittest.TestCase):
    def test_big_game_gets_more_posts(self):
        ctx = league(week=9)
        star = next(l for l in ctx["player_lines"] if l["week"] == 8 and l["team_id"] == 20 and l["position"] == "WR")
        star.update(receptions=12, rec_yards=212, rec_tds=3, fantasy_points=57.2)
        W = Week(ctx)
        tier, reasons = W.player_buzz(star)
        self.assertEqual(tier, 2)
        self.assertIn("212 receiving yards, the most in a game this season", reasons)
        plan = plan_social_week(ctx)
        on_star = [a for a in plan["assignments"] if a["subject"].get("id") == star["player_id"] and a["kind"] == "PERFORMANCE"]
        self.assertEqual(len(on_star), 4)
        self.assertEqual(len({a["agent_id"] for a in on_star}), 4)            # four different voices
        self.assertEqual({a["buzz_tier"] for a in on_star}, {2})
        self.assertIn("why_people_are_talking", on_star[0]["facts"])
        self.assertEqual(len({a["angle"] for a in on_star}), 4)
        ranking = [a for a in by_kind(plan)["COMPARISON"] if a.get("buzz_tier")]
        self.assertEqual(len(ranking), 1)
        self.assertIn("best_WR_games_this_season", ranking[0]["facts"])
        self.assertEqual(sum(plan["counts"].values()), TARGET_POSTS)

    def test_ordinary_lines_and_common_milestones_are_not_news(self):
        ctx = league(week=9)
        W = Week(ctx)
        ordinary = next(l for l in ctx["player_lines"] if l["week"] == 8 and l["team_id"] == 4 and l["position"] == "HB")
        self.assertEqual(W.player_buzz(ordinary), (0, []))
        self.assertTrue(all(l["fantasy_points"] >= 40 for l, _, _ in W.buzz_lines()))   # only the 40+ PPR lines here
        qb = next(l for l in ctx["player_lines"] if l["week"] == 8 and l["team_id"] == 4 and l["position"] == "QB")
        for l in ctx["player_lines"]:                                  # 4,000 yards crossed last week: not news alone
            if l["player_id"] == qb["player_id"]:
                l["pass_yards"] = 3990 // 7 if l["week"] < 8 else 120
        self.assertEqual(Week(ctx).player_buzz(qb)[0], 0)

    def test_season_record_broken(self):
        ctx = league(week=12)
        wr = [l for l in ctx["player_lines"] if l["team_id"] == 6 and l["position"] == "WR"]
        for l in wr:
            l["rec_tds"] = 2                                           # 20 through Week 10
        wr[-1]["rec_tds"] = 4                                          # 24 after Week 11: past Moss's 23
        tier, reasons = Week(ctx).player_buzz(wr[-1])
        self.assertEqual(tier, 2)
        self.assertTrue(any("broke the NFL single-season record for receiving TDs" in r and "Randy Moss" in r for r in reasons))

    def test_team_storylines(self):
        ctx = league(week=12)
        dominate(ctx, 3)                                               # 11-0; team 19 (its opponent every week) 0-11
        W = Week(ctx)
        stories = {s["team_id"]: s for s in W.team_stories()}
        self.assertEqual(stories[3]["tier"], 2)
        self.assertTrue(stories[3]["positive"])
        self.assertEqual(stories[3]["reasons"][0], "Perfect at 11-0")
        self.assertTrue(stories[3]["history"])
        self.assertEqual(stories[19]["reasons"][0], "Still winless at 0-11")
        self.assertFalse(stories[19]["positive"])
        plan = plan_social_week(ctx)
        story_posts = by_kind(plan)["TEAM_STORY"]
        on_three = [a for a in story_posts if a["subject"]["id"] == 3]
        self.assertEqual(len(on_three), 4)
        self.assertEqual({a["team_id"] for a in on_three}, {3})          # filed under the team
        self.assertIn("nfl_history_through_2025", on_three[0]["facts"])
        self.assertTrue(all(a["archetype"] not in ("DIE_HARD_FAN", "LOCAL_BEAT_REPORTER") for a in story_posts))
        self.assertEqual(sum(plan["counts"].values()), TARGET_POSTS)
        wake = plan_team_wakeup(ctx, 3)
        self.assertEqual([a["kind"] for a in wake["assignments"] if a["kind"] == "TEAM_STORY"], ["TEAM_STORY"])

    def test_first_loss_after_perfect_start(self):
        ctx = league(week=12)
        dominate(ctx, 3)
        last = next(g for g in ctx["recap_games"] if g["home_team_id"] == 3)
        last["home_score"], last["away_score"] = 17, 24
        s = next(s for s in Week(ctx).team_stories() if s["team_id"] == 3)
        self.assertEqual((s["tier"], s["positive"]), (2, False))
        self.assertTrue(s["reasons"][0].startswith("First loss of the season after a 10-0 start"))

    def test_no_storylines_in_the_postseason_or_preseason(self):
        self.assertEqual(Week(league(week=1, played_weeks=[])).team_stories(), [])


DEF_KEYS = ("def_tackles", "def_sacks", "def_tfl", "def_ints", "def_deflections", "def_forced_fumbles",
            "def_fumble_recoveries", "def_tds", "def_safeties", "kick_fgm", "kick_fga", "kick_fg_long",
            "kick_fgm_50", "kick_fga_50", "kick_xpm", "kick_xpa")


def def_line(ctx, game_id, team, pid, name, position, **stats):
    """A captured defensive or kicking game line (game_stat_lines) for last week's game `game_id`."""
    line = {"week": ctx["recap_week"], "game_id": game_id, "team_id": team, "position": position, "short_name": name,
            "name": name, "player_id": pid, "popularity": 50.0, **{k: None for k in DEF_KEYS}}
    line.update({k: float(v) for k, v in stats.items()})
    ctx.setdefault("defense_lines", []).append(line)
    return line


class DefensePerformanceTests(unittest.TestCase):
    def captured(self):
        ctx = league(week=9)
        g = next(g for g in ctx["recap_games"] if g["home_team_id"] == 3)
        def_line(ctx, g["id"], 3, 9001, "Edge Rusher", "LE", def_tackles=5, def_sacks=3, def_tfl=3, def_forced_fumbles=1)
        def_line(ctx, g["id"], 19, 9002, "Ball Hawk", "CB", def_tackles=4, def_ints=1, def_tds=1)
        def_line(ctx, g["id"], 3, 9003, "Big Leg", "K", kick_fgm=4, kick_fga=4, kick_fg_long=56, kick_fgm_50=2,
                 kick_fga_50=2, kick_xpm=2, kick_xpa=2)
        def_line(ctx, g["id"], 19, 9004, "Tackle Machine", "MLB", def_tackles=14, def_sacks=1)   # ordinary: no post
        def_line(ctx, g["id"], 19, 9005, "Steady Leg", "K", kick_fgm=2, kick_fga=2, kick_fg_long=41, kick_xpm=1, kick_xpa=1)
        return ctx, g

    def test_big_defensive_and_kicking_games_join_the_performances(self):
        ctx, g = self.captured()
        base = len(by_kind(plan_social_week(league(week=9)))["PERFORMANCE"])
        plan = plan_social_week(ctx)
        perf = by_kind(plan)["PERFORMANCE"]
        self.assertEqual(len(perf), base)                                   # same count, mixed in
        self.assertEqual(sum(plan["counts"].values()), TARGET_POSTS)
        ordinary = [a["subject"]["id"] for a in perf if not a.get("buzz_tier")]
        self.assertEqual(len(ordinary), 7)                                  # the rest are buzz posts
        self.assertEqual([i for i in ordinary if i >= 9000], [9003, 9001])  # biggest first, ordinary lines left out
        self.assertEqual([ordinary.index(i) for i in (9003, 9001)], [1, 4])  # 2nd and 5th ordinary post
        self.assertNotIn("DIE_HARD_FAN", {a["archetype"] for a in perf})
        self.assertEqual(Week(ctx).defense_performances()[2]["player_id"], 9002)   # next in line on a lighter week
        rusher = next(a for a in perf if a["subject"]["id"] == 9001)
        self.assertEqual(rusher["facts"]["stat_line"], "5 tkl, 3 sacks, 3 TFL, 1 FF")
        self.assertEqual(rusher["facts"]["game"], Week(ctx).game_label(g))
        self.assertNotIn("fantasy_points_ppr", rusher["facts"])
        kicker = next(a for a in perf if a["subject"]["id"] == 9003)
        self.assertEqual(kicker["facts"]["stat_line"], "4/4 FG (long 56), 2/2 from 50+, 2/2 XP")

    def test_games_without_captured_lines_contribute_none(self):
        ctx = league(week=9)
        self.assertEqual(Week(ctx).defense_performances(), [])
        def_line(ctx, 1, 1, 9001, "Old Week", "LE", def_tackles=5, def_sacks=4)  # game 1 is Week 1, not last week
        self.assertEqual(Week(ctx).defense_performances(), [])
        offense_only = [a["subject"]["id"] for a in by_kind(plan_social_week(ctx))["PERFORMANCE"]]
        self.assertNotIn(9001, offense_only)


def book(franchise=None, league=None, full=()):
    import collections as _c
    b = {"franchise": _c.defaultdict(list), "league": _c.defaultdict(list), "full": set(full)}
    for k, rows in (franchise or {}).items():
        b["franchise"][k] = list(rows)
        b["league"][k[1]] += list(rows)
    for k, rows in (league or {}).items():
        b["league"][k] += list(rows)
    return b


class RecordBookTests(unittest.TestCase):
    def test_franchise_season_record_broken_this_week(self):
        ctx = league(week=12)
        ctx["record_book"] = book(franchise={(6, "season_rec_tds"): [(10.0, "Old Guy", 2010)]},
                                  league={"season_rec_tds": [(23.0, "Randy Moss", 2007)]}, full=("season_rec_tds",))
        wr = next(l for l in ctx["player_lines"] if l["week"] == 11 and l["team_id"] == 6 and l["position"] == "WR")
        tier, reasons = Week(ctx).player_buzz(wr)
        self.assertGreaterEqual(tier, 1)
        self.assertIn("the most receiving TDs in a season in Team6 history: 11 (old record 10, Old Guy, 2010)", reasons)

    def test_nfl_records_come_from_the_book(self):
        ctx = league(week=12)
        ctx["record_book"] = book(league={"season_pass_tds": [(20.0, "Someone", 2030)]}, full=("season_pass_tds",))
        self.assertEqual(Week(ctx).season_record_marks()["pass_tds"], ("passing TDs", 20, "Someone, 2030"))
        self.assertEqual(Week(league(week=12)).season_record_marks()["pass_tds"][1], 55)     # no book: the constants

    def test_single_game_record_tie_and_partial_categories(self):
        ctx = league(week=12)
        ctx["record_book"] = book(league={"game_pass_tds": [(7.0, "Drew Brees", 2015), (7.0, "Nick Foles", 2013),
                                                            (7.0, "Peyton Manning", 2013)],
                                          "game_rec_tds": [(5.0, "B. Book", 2029)]},
                                  full=("game_pass_tds",))
        qb = next(l for l in ctx["player_lines"] if l["week"] == 11 and l["team_id"] == 4 and l["position"] == "QB")
        qb["pass_tds"] = 7
        tier, reasons = Week(ctx).player_buzz(qb)
        self.assertEqual(tier, 2)
        self.assertIn("tied the NFL single-game record for passing TDs: 7, shared with 3 players, most recently Drew Brees, 2015", reasons)
        wr = next(l for l in ctx["player_lines"] if l["week"] == 11 and l["team_id"] == 4 and l["position"] == "WR")
        self.assertFalse([r for r in Week(ctx).player_buzz(wr)[1] if "NFL history" in r])   # BOOK-only list: no NFL rank

    def test_team_franchise_wins_record(self):
        ctx = league(week=12)
        dominate(ctx, 3)
        ctx["record_book"] = book(franchise={(3, "season_team_wins"): [(10.0, "2010 City3 Team3", 2010)]})
        story = next(st for st in Week(ctx).team_stories() if st["team_id"] == 3)
        self.assertTrue(any(r.startswith("Most wins in a season in Team3 history: 11 (old record 10, the 2010 City3 Team3)")
                            for r in story["reasons"]), story)


class NameTests(unittest.TestCase):
    def test_tidy_name(self):
        self.assertEqual(tidy_name("OMAR COOPER JR."), "Omar Cooper Jr.")
        self.assertEqual(tidy_name("D'ANDRE SMITH III"), "D'Andre Smith III")
        self.assertEqual(tidy_name("Ka'Von Franklin"), "Ka'Von Franklin")


if __name__ == "__main__":
    unittest.main()


def move(mid, team_id, mtype="SIGNED", notable=True, score=10.0, dead=None, line=None):
    return {"id": mid, "type": mtype, "week": 9, "team_id": team_id, "player_id": 9000 + mid,
            "player_name": f"Mover {mid}", "position": "WR",
            "line": line or f"Week 9 · {mtype} · Team{team_id} signed WR Mover {mid} (free agent): 1 year, $3.93M",
            "money": ["$3.93M"], "notable": notable, "notable_reasons": ["starter"] if notable else [],
            "rank_score": score, "depth_slot": 2, "dead_money": dead, "seasons_with_team": 4}


def moves_league(moves, caps=None, injuries=None):
    ctx = league(week=9)
    for arch in ("SCHEFTER_TWEET", "RAPOPORT"):
        ctx["accounts"].append({"agent_id": arch.lower(), "archetype": arch, "team_id": None,
                                "handle": f"@{arch.lower()}", "display_name": "x"})
    ctx["moves"] = moves
    ctx["team_caps"] = caps or {t: {"line": f"Team{t}: $10M in cap space (scanned Week 8)", "cap_space": 10_000_000,
                                    "dead_money": 0, "basis": "scanned Week 8"} for t in range(1, 33)}
    if injuries:
        ctx["injuries_by_team"] = injuries
    return ctx


class AgentWireMoveTests(unittest.TestCase):
    """Roster moves on the Agent Wire (team management step 5, phase 5)."""

    def test_insiders_break_at_most_three_notable_moves(self):
        moves = [move(i, i, score=20 - i) for i in range(1, 7)] + [move(50, 20, notable=False)]
        plan = plan_social_week(moves_league(moves))
        news = by_kind(plan)["MOVE_NEWS"]
        self.assertEqual(len(news), 3)
        self.assertEqual({a["archetype"] for a in news}, {"SCHEFTER_TWEET", "RAPOPORT"})
        self.assertEqual([a["subject"]["id"] for a in news], [9001, 9002, 9003])   # most notable first
        self.assertIn("$3.93M", news[0]["facts"]["move"])
        self.assertEqual(sum(plan["counts"].values()), TARGET_POSTS)

    def test_no_moves_no_move_posts(self):
        plan = plan_social_week(moves_league([]))
        for kind in ("MOVE_NEWS", "BEAT_MOVE", "MOVE_REACTION", "CAP_TAKE"):
            self.assertFalse(by_kind(plan)[kind], kind)
        self.assertEqual(sum(plan["counts"].values()), TARGET_POSTS)

    def test_beat_move_replaces_the_note_but_not_a_starter_injury(self):
        injury = {"team_id": 32, "player_name": "Hurt Guy", "position": "QB", "injury_type": "Knee", "duration": "4 weeks",
                  "weeks_remaining": 4, "is_ir": False, "is_starter": True, "is_key_injury": False}
        moves = [move(1, 16), move(2, 32)]
        plan = plan_social_week(moves_league(moves, injuries={32: [injury]}))
        beat = {a["team_id"]: a["kind"] for a in plan["assignments"] if a["kind"].startswith("BEAT")}
        self.assertEqual(beat.get(16), "BEAT_MOVE")
        self.assertEqual(beat.get(32), "BEAT_INJURY")
        facts = next(a["facts"] for a in plan["assignments"] if a["kind"] == "BEAT_MOVE")
        self.assertIn("Mover 1", facts["moves_this_week"][0])
        self.assertIn("cap", facts)

    def test_move_reactions_come_from_the_teams_die_hards(self):
        plan = plan_social_week(moves_league([move(1, 5, "RELEASED", score=30), move(2, 6, score=20), move(3, 7, score=10)]))
        reactions = by_kind(plan)["MOVE_REACTION"]
        self.assertEqual([(a["archetype"], a["team_id"]) for a in reactions], [("DIE_HARD_FAN", 5), ("DIE_HARD_FAN", 6)])
        self.assertEqual(reactions[0]["facts"]["seasons_with_your_team"], 4)

    def test_cap_take_only_when_its_trigger_holds(self):
        plan = plan_social_week(moves_league([move(1, 5)]))
        self.assertFalse(by_kind(plan)["CAP_TAKE"])
        caps = {t: {"line": f"Team{t}: $10M in cap space", "cap_space": 10_000_000, "dead_money": 0, "basis": "x"}
                for t in range(1, 33)}
        caps[9] = {"line": "Team9: -$29.5M in cap space, $37.8M dead money (scanned Week 8)", "cap_space": -29_500_000,
                   "dead_money": 37_800_000, "basis": "scanned Week 8"}
        take = by_kind(plan_social_week(moves_league([move(1, 5)], caps)))["CAP_TAKE"]
        self.assertEqual(len(take), 1)
        self.assertEqual(take[0]["team_id"], 9)
        self.assertIn("-$29.5M", take[0]["facts"]["cap"])
        released = move(2, 12, "RELEASED", dead=12_000_000,
                        line="Week 9 · RELEASED · Team12 released WR Mover 2 · dead money $12M · free agent since Week 9")
        take = by_kind(plan_social_week(moves_league([move(1, 5), released])))["CAP_TAKE"]
        self.assertEqual([(a["team_id"], a["facts"]["release"][:6]) for a in take], [(12, "Week 9")])

    def test_team_wakeup_beat_move(self):
        plan = plan_team_wakeup(moves_league([move(1, 16)]), 16)
        self.assertIn("BEAT_MOVE", [a["kind"] for a in plan["assignments"]])
        plan = plan_team_wakeup(moves_league([move(1, 16, notable=False)]), 16)
        self.assertNotIn("BEAT_MOVE", [a["kind"] for a in plan["assignments"]])
