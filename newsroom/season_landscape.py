"""Division standings for the weekly assignment brief's `season_landscape`. Pure; no database needed.

Standings are counted from completed regular-season games before the publication week, so a Week N brief
shows the league as it stood entering Week N (and a regenerated old week doesn't see later results).
"""

REGULAR_SEASON_FINAL_WEEK = 18


def division_standings(teams, games, before_week: int, history_chips: dict | None = None) -> dict:
    """{division: [team line, ...]} sorted by win % then point differential.

    teams: [{"id", "name", "city", "conference", "division"}]
    games: completed games [{"week", "home_team_id", "away_team_id", "home_score", "away_score"}]
    history_chips: optional {team_id: "5-Yr Wins: ..."} string chips to append to each line
    Only regular-season games (Weeks 1-18) before `before_week` count.
    """
    last_week = min(before_week - 1, REGULAR_SEASON_FINAL_WEEK)
    rec = {t["id"]: {"w": 0, "l": 0, "t": 0, "pf": 0, "pa": 0} for t in teams}
    for g in games:
        if g["week"] is None or g["week"] > last_week:
            continue
        h, a, hs, as_ = g["home_team_id"], g["away_team_id"], g["home_score"] or 0, g["away_score"] or 0
        for team, pf, pa in ((h, hs, as_), (a, as_, hs)):
            if team not in rec:
                continue
            r = rec[team]
            r["pf"] += pf
            r["pa"] += pa
            r["w" if pf > pa else "l" if pf < pa else "t"] += 1

    def win_pct(r):
        played = r["w"] + r["l"] + r["t"]
        return (r["w"] + 0.5 * r["t"]) / played if played else 0.0

    divisions = {}
    for t in teams:
        divisions.setdefault(t["division"], []).append(t)
    out = {}
    for div in sorted(divisions):
        rows = sorted(divisions[div], key=lambda t: (win_pct(rec[t["id"]]), rec[t["id"]]["pf"] - rec[t["id"]]["pa"]), reverse=True)
        lines = []
        for t in rows:
            r = rec[t["id"]]
            record = f"{r['w']}-{r['l']}" + (f"-{r['t']}" if r["t"] else "")
            diff = r["pf"] - r["pa"]
            chip = f" | {history_chips[t['id']]}" if history_chips and t["id"] in history_chips else ""
            lines.append(f"{t['city']} {t['name']} (ID: {t['id']}) {record}, PF {r['pf']}, PA {r['pa']}, diff {diff:+d}{chip}")
        out[div] = lines
    return out


def records_by_team(standings: dict) -> dict:
    """team_id -> 'W-L' parsed back out of division_standings lines (for labelling the leader lists)."""
    out = {}
    for lines in standings.values():
        for line in lines:
            tid = int(line.split("(ID: ", 1)[1].split(")", 1)[0])
            out[tid] = line.split(") ", 1)[1].split(",", 1)[0]
    return out
