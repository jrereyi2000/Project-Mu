"""Napper fantasy playoffs (db/migrations/2026_12_06_napper_playoffs.sql), the same bracket as
engines/fantasy_league_sim.py:

- The regular season is weeks 1–14 (server/napper_api._get_league_standings). Once week 14 is final, the top
  PLAYOFF_TEAMS (6) by record, then points for, are seeded 1–6.
- Week 15, quarterfinals: 3 v 6 and 4 v 5; seeds 1 and 2 have a bye.
- Week 16, semifinals: seed 1 plays the lowest seed left, seed 2 the other quarterfinal winner.
- Week 17, final. A tie goes to the higher seed (fantasy_league_sim.playoff_round).

Matchups for a playoff week are written (napper_matchups.playoff_round) once the week before it is final,
so the bracket fills in round by round. When the final is decided the league's champion_team_id is set,
the user's profile gets a trophy if the user won, and both moments are posted to the league chat.
Teams outside the bracket (and eliminated teams) have no matchup in playoff weeks.

sync_playoffs() does all of this and is idempotent: the weekly step (engines/napper_weekly.py) and the API
(`_ensure_matchup_and_rosters`, GET /api/napper/league/{id}/playoffs) both call it.
"""

import json
from typing import Dict, List, Optional

from psycopg.rows import dict_row

from engines.fantasy_league_sim import LAST_FANTASY_WEEK, REGULAR_WEEKS, playoff_round

PLAYOFF_TEAMS = 6
ROUNDS = {15: "QUARTERFINAL", 16: "SEMIFINAL", 17: "FINAL"}
ROUND_LABELS = {"QUARTERFINAL": "Quarterfinals", "SEMIFINAL": "Semifinals", "FINAL": "Championship"}


# ─────────────────────────────── pure bracket (no database) ───────────────────────────────

def build_bracket(seeds: Dict[int, int], scores: Dict[int, Dict[int, float]], final_weeks) -> dict:
    """The bracket as far as it is decided. seeds: {team: seed} for every team (1 = best); scores: {week:
    {team: points}}; final_weeks: playoff weeks whose games are all final.
    Returns {rounds: {week: [{home, away, winner}]}, finish: {team: MISSED / QUARTERFINAL / SEMIFINAL /
    RUNNER_UP / CHAMPION}, champion}. A round is listed once its teams are known; winner is None until
    its week is final."""
    by_seed = {s: t for t, s in seeds.items()}
    finish = {t: "MISSED" for t, s in seeds.items() if s > PLAYOFF_TEAMS}
    rounds: Dict[int, List[dict]] = {}

    def play(week: int, pairs: List[tuple], loser_finish: str) -> Optional[List[int]]:
        decided = week in final_weeks
        winners, losers = playoff_round(pairs, scores.get(week, {}), seeds) if decided else ([], [])
        rounds[week] = [{"home": a, "away": b, "winner": (winners[i] if decided else None)}
                        for i, (a, b) in enumerate(pairs)]
        for t in losers:
            finish[t] = loser_finish
        return winners if decided else None

    qf_winners = play(15, [(by_seed[3], by_seed[6]), (by_seed[4], by_seed[5])], "QUARTERFINAL")
    if qf_winners is None:
        return {"rounds": rounds, "finish": finish, "champion": None}
    low, high = sorted(qf_winners, key=lambda t: seeds[t], reverse=True)
    sf_winners = play(16, [(by_seed[1], low), (by_seed[2], high)], "SEMIFINAL")
    if sf_winners is None:
        return {"rounds": rounds, "finish": finish, "champion": None}
    final_winners = play(17, [tuple(sorted(sf_winners, key=lambda t: seeds[t]))], "RUNNER_UP")
    champion = final_winners[0] if final_winners else None
    if champion is not None:
        finish[champion] = "CHAMPION"
    return {"rounds": rounds, "finish": finish, "champion": champion}


def team_week_note(bracket: Optional[dict], seeds: Dict[int, int], team: int, week: int) -> Optional[str]:
    """Why a team has no matchup in a playoff week (None when it has one or the week isn't a playoff week)."""
    if week not in ROUNDS:
        return None
    if bracket is None:
        return f"The playoff bracket is set once the regular season (Weeks 1–{REGULAR_WEEKS}) is final."
    if week not in bracket["rounds"]:
        return f"Week {week} playoff matchups are set once Week {week - 1} is final."
    if any(team in (g["home"], g["away"]) for g in bracket["rounds"][week]):
        return None
    seed = seeds.get(team)
    if seed is not None and seed > PLAYOFF_TEAMS:
        return f"This team finished #{seed} and missed the playoffs."
    if week == 15 and seed in (1, 2):
        return f"#{seed} seed: first-round bye in Week 15."
    out = bracket["finish"].get(team)
    if out == "QUARTERFINAL":
        return "Eliminated in the quarterfinals (Week 15)."
    if out == "SEMIFINAL":
        return "Eliminated in the semifinals (Week 16)."
    return None


# ─────────────────────────────── database layer ───────────────────────────────

def _week_final(dc, season: int, week: int) -> bool:
    from server.napper_api import _is_nfl_week_complete, get_current_season_week
    _, active_week = get_current_season_week(dc)
    return week < active_week or _is_nfl_week_complete(dc, season, week)


def _post(dc, league_id: int, text: str, card: dict) -> None:
    dc.execute("""
        INSERT INTO napper_chat (league_id, sender_type, sender_name, sender_handle, avatar_url, message_text, card_payload)
        VALUES (%s, 'SYSTEM_BOT', 'Napper Commissioner', '@NapperBot', '/static/players/robot_avatar.webp', %s, %s)
    """, (league_id, text, json.dumps(card)))


def sync_playoffs(cur, league_id: int, post_chat: bool = True) -> dict:
    """Brings the league's bracket up to date: writes playoff matchups for every round whose teams are known,
    and crowns the champion once the final is final. Returns {status, seeds, standings, bracket, champion}
    where status is PROJECTED (regular season not final; seeds = standings so far), IN_PROGRESS, COMPLETE or
    NONE (fewer than PLAYOFF_TEAMS teams). The caller commits."""
    from server.napper_api import _get_league_standings, _get_weekly_team_totals
    with cur.connection.cursor(row_factory=dict_row) as dc:
        dc.execute("SELECT season, scoring_type, champion_team_id FROM napper_leagues WHERE id = %s", (league_id,))
        league = dc.fetchone()
        season = int(league["season"])
        standings = _get_league_standings(dc, league_id, season, league["scoring_type"])
        seeds = {t["id"]: t["seed"] for t in standings}
        out = {"status": "PROJECTED", "seeds": seeds, "standings": standings, "bracket": None,
               "champion": league["champion_team_id"]}
        if len(standings) < PLAYOFF_TEAMS:
            out["status"] = "NONE"
            return out
        if not _week_final(dc, season, REGULAR_WEEKS):
            return out

        final_weeks = {w for w in ROUNDS if _week_final(dc, season, w)}
        scores = {w: {t: float(r["score"] or 0) for t, r in
                      _get_weekly_team_totals(dc, league_id, season, w, league["scoring_type"]).items()}
                  for w in ROUNDS if w in final_weeks}
        bracket = build_bracket(seeds, scores, final_weeks)
        out.update(bracket=bracket, status="COMPLETE" if bracket["champion"] is not None else "IN_PROGRESS")

        names = {t["id"]: t["team_name"] for t in standings}
        for week, games in bracket["rounds"].items():
            dc.execute("SELECT COUNT(*) AS n FROM napper_matchups WHERE league_id = %s AND week = %s "
                       "AND playoff_round IS NOT NULL", (league_id, week))
            if dc.fetchone()["n"]:
                continue
            dc.execute("DELETE FROM napper_matchups WHERE league_id = %s AND week = %s", (league_id, week))
            for g in games:
                dc.execute("""
                    INSERT INTO napper_matchups (league_id, week, home_team_id, away_team_id, home_score, away_score,
                                                 home_proj, away_proj, win_prob_home, is_final, playoff_round)
                    VALUES (%s, %s, %s, %s, 0, 0, 0, 0, 0.5, FALSE, %s)
                """, (league_id, week, g["home"], g["away"], ROUNDS[week]))
            if week == 15:
                for team, seed in seeds.items():
                    dc.execute("UPDATE napper_teams SET seed = %s WHERE id = %s", (seed, team))
                if post_chat:
                    by_seed = {s: t for t, s in seeds.items()}
                    field = ", ".join(f"#{s} {names[by_seed[s]]}" for s in range(1, PLAYOFF_TEAMS + 1))
                    _post(dc, league_id, f"🏈 The playoff bracket is set: {field}. Seeds 1 and 2 have a bye in "
                          f"Week 15.", {"type": "PLAYOFF_BRACKET",
                                        "seeds": [names[by_seed[s]] for s in range(1, PLAYOFF_TEAMS + 1)]})

        champion = bracket["champion"]
        if champion is not None and league["champion_team_id"] is None:
            dc.execute("UPDATE napper_leagues SET champion_team_id = %s WHERE id = %s", (champion, league_id))
            dc.execute("""
                UPDATE napper_user_profiles SET trophies = trophies + 1
                WHERE id = (SELECT user_id FROM napper_teams WHERE id = %s)
            """, (champion,))
            if post_chat:
                final = bracket["rounds"][LAST_FANTASY_WEEK][0]
                runner_up = final["away"] if final["home"] == champion else final["home"]
                _post(dc, league_id, f"🏆 {names[champion]} win the league championship, beating "
                      f"{names[runner_up]} in the Week {LAST_FANTASY_WEEK} final.",
                      {"type": "CHAMPION", "team": names[champion], "runner_up": names[runner_up],
                       "score": scores[LAST_FANTASY_WEEK].get(champion),
                       "runner_up_score": scores[LAST_FANTASY_WEEK].get(runner_up)})
            out["champion"] = champion
    return out
