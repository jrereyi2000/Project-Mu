"""
Players to take off injured reserve by hand (JR, 2026-10-27).

Madden bug: a player put on IR never comes off it. Once healed he stays on the Injury Report as injury NONE,
0 WEEKS, IR, and never plays again (2036 through week 18: of 131 players who went on IR, 129 never recorded
another stat). JR fixes it in Madden by taking control of the team, activating the player and fixing the depth
chart. The weekly run lists who is due (pipeline/weekly_capture.py: in the "your turn" notice right after the
capture, projected from the last report, and again after the load from the new report).

  python3 -m pipeline.ir_returns              # who to activate before the league's current week is played
  python3 -m pipeline.ir_returns --week 12    # as of another week (the latest reports up to it, counted down)

A player is due in week W when his latest report up to W is his team's latest report (absent from a newer one
means he came off the report), it has him on IR, and its weeks_remaining counted down to W is 0. In the
postseason only teams with a game in W count, and a team's reports are the regular-season ones plus the
playoff weeks it played in (injury loads from week 19 cover only the teams playing that week).
"""
import argparse
from collections import defaultdict

FIRST_PLAYOFF_WEEK = 19


def due_players(rows, week, playing=None):
    """Players to activate before `week` is played, from injury rows (dicts with player_id, player_name,
    position, ovr, team_id, team, week, weeks_remaining, is_ir; any weeks of the season, those after `week`
    are ignored). playing: {week: {team_id}} for the postseason weeks up to `week`. Each due player is a dict
    {player_id, player_name, position, ovr, team, team_id, healed_week, new} (new: healed this week, so first
    due now), sorted new first, then team and name."""
    playing = playing or {}
    rows = [r for r in rows if r["week"] <= week and r.get("player_id") is not None]
    report_weeks = sorted({r["week"] for r in rows})

    def team_weeks(tid):
        return [w for w in report_weeks if w < FIRST_PLAYOFF_WEEK or tid in playing.get(w, ())]

    by_player = defaultdict(dict)
    for r in rows:
        by_player[r["player_id"]][r["week"]] = r

    out = []
    for pid, weeks in by_player.items():
        latest = weeks[max(weeks)]
        tid = latest["team_id"]
        if week >= FIRST_PLAYOFF_WEEK and tid not in playing.get(week, ()):
            continue
        tw = team_weeks(tid)
        if not tw or latest["week"] != tw[-1] or not latest["is_ir"]:
            continue
        if (latest["weeks_remaining"] or 0) - (week - latest["week"]) > 0:
            continue
        stint = []                                  # this IR stint: his team's reports back to one without him on IR
        for w in reversed(tw):
            r = weeks.get(w)
            if not r or not r["is_ir"]:
                break
            stint.append(r)
        healed = next((r["week"] for r in reversed(stint) if not r["weeks_remaining"]),
                      latest["week"] + (latest["weeks_remaining"] or 0))
        out.append({"player_id": pid, "player_name": latest["player_name"], "position": latest["position"],
                    "ovr": latest["ovr"], "team": latest["team"], "team_id": tid,
                    "healed_week": healed, "new": healed >= week})
    out.sort(key=lambda p: (not p["new"], p["team"] or "", p["player_name"] or ""))
    return out


def fetch(cur, season, week):
    """(injury rows of the season up to week, {postseason week: {team_id with a game}}) for due_players."""
    # a healed player's report row (NONE, 0 WEEKS) often reads without position or OVR: the roster's stand in
    cur.execute("""
        SELECT i.player_id, i.player_name, COALESCE(NULLIF(i.position, ''), ps.position),
               COALESCE(NULLIF(i.ovr, 0), ps.overall_rating), i.team_id, t.abbreviation, i.week,
               i.weeks_remaining, i.is_ir
        FROM injuries i JOIN teams t ON t.id = i.team_id
        LEFT JOIN player_seasons ps ON ps.player_id = i.player_id AND ps.season = i.season
        WHERE i.season = %s AND i.week <= %s
    """, (season, week))
    keys = ("player_id", "player_name", "position", "ovr", "team_id", "team", "week", "weeks_remaining", "is_ir")
    rows = [dict(zip(keys, r)) for r in cur.fetchall()]
    cur.execute("SELECT week, home_team_id, away_team_id FROM games WHERE season = %s AND week BETWEEN %s AND %s",
                (season, FIRST_PLAYOFF_WEEK, week))
    playing = defaultdict(set)
    for w, home, away in cur.fetchall():
        playing[w].update(t for t in (home, away) if t is not None)
    return rows, dict(playing)


def ir_returns(cur, season, week):
    rows, playing = fetch(cur, season, week)
    return due_players(rows, week, playing)


def banner(players, week, projected=False):
    """Terminal banner, or '' when nobody is due."""
    if not players:
        return ""
    new = sum(p["new"] for p in players)
    head = (f"TAKE OFF IR IN MADDEN before Week {week} ({len(players)} player{'s' if len(players) != 1 else ''}"
            + (f", {new} newly healed" if new else "") + (", projected from last week's report" if projected else "")
            + "): take control of each team, activate them and fix the depth chart")
    lines = []
    for team in sorted({p["team"] for p in players}):
        who = [p for p in players if p["team"] == team]
        lines.append(f"  {team:<4} " + ", ".join(
            f"{p['position']} {p['player_name']} ({p['ovr']})" + (" NEW" if p["new"] else f" since wk {p['healed_week']}")
            for p in who))
    bar = "!" * min(110, max(len(head), 40))
    return "\n".join([bar, head, *lines, bar])


def notice_text(players, week, projected=False):
    """Phone notice body (one line per team), or '' when nobody is due."""
    if not players:
        return ""
    by_team = defaultdict(list)
    for p in players:
        by_team[p["team"]].append(f"{p['position']} {p['player_name']}" + ("*" if p["new"] else ""))
    lines = [f"{t}: {', '.join(v)}" for t, v in sorted(by_team.items())]
    when = " (projected)" if projected else ""
    return (f"Take {len(players)} healed player{'s' if len(players) != 1 else ''} off IR before Week {week}{when}, "
            "then fix the depth charts (* = newly healed):\n" + "\n".join(lines))


def main():
    import psycopg
    from pipeline.load_capture import DSN
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int)
    ap.add_argument("--week", type=int, help="default: league_state.current_week (the week about to be played)")
    a = ap.parse_args()
    with psycopg.connect(DSN) as conn:
        cur = conn.cursor()
        cur.execute("SELECT current_season, current_week FROM league_state WHERE id = 1")
        season, week = cur.fetchone()
        season, week = a.season or season, a.week or week
        players = ir_returns(cur, season, week)
    print(banner(players, week) or f"Nobody to take off IR before Week {week} ({season}).")


if __name__ == "__main__":
    main()
