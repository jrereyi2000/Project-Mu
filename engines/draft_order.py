"""Round 1 draft order (PROJECT_SPEC §2.10): reverse regular-season record, with playoff exits in their brackets.

    picks 1–18   teams that missed the playoffs
    picks 19–24  Wild Card losers
    picks 25–28  Divisional losers
    picks 29–30  Conference Championship losers
    pick 31      Super Bowl loser,  pick 32  Super Bowl winner

Within a bracket: regular-season win% (ties count half), then regular-season point differential, worst first.
A pick is `locked` once its team's season is over and the bracket's round is complete, so nothing can move
it. Teams still alive take the remaining (highest) picks by the same order, provisionally.

The playoff field is read from the playoff games: before the Wild Card games exist nobody's season is known
to be over and the whole order is provisional (a past season stored with only its Super Bowl is complete:
loser 31, winner 32, everyone else by record, all locked). While only the Wild Card round is scheduled, each
conference's best record is taken as its #1 seed (bye), since byes don't appear in any game yet.

Only games the caller passes count, so a run's week cutoff is applied by the fetch.
"""

ROUNDS = ["WILD_CARD", "DIVISIONAL", "CONFERENCE", "SUPER_BOWL"]
ROUND_WEEKS = {19: "WILD_CARD", 20: "DIVISIONAL", 21: "CONFERENCE", 23: "SUPER_BOWL"}   # rows without a game_type
BRACKET_STATUS = {"WILD_CARD": "Wild Card Elimination", "DIVISIONAL": "Divisional Elimination",
                  "CONFERENCE": "Conference Championship Elimination", "SUPER_BOWL": "Super Bowl Runner-up"}


def game_round(game: dict):
    gt = game.get("game_type")
    if gt in ROUNDS:
        return gt
    if gt in (None, "REGULAR_SEASON") and game.get("week") in ROUND_WEEKS and game.get("week", 0) > 18:
        return ROUND_WEEKS[game["week"]]
    return None


def regular_season_records(teams: dict, games: list) -> dict:
    """{team_id: {w, l, t, pf, pa}} from completed regular-season games."""
    rec = {tid: dict(w=0, l=0, t=0, pf=0, pa=0) for tid in teams}
    for g in games:
        if g["status"] != "COMPLETED" or game_round(g) is not None or g["week"] > 18:
            continue
        for me, them, mine, theirs in ((g["home_team_id"], g["away_team_id"], g["home_score"], g["away_score"]),
                                       (g["away_team_id"], g["home_team_id"], g["away_score"], g["home_score"])):
            if me not in rec:
                continue
            r = rec[me]
            r["pf"] += mine
            r["pa"] += theirs
            r["w" if mine > theirs else "l" if mine < theirs else "t"] += 1
    return rec


def _sort_key(r: dict):
    played = r["w"] + r["l"] + r["t"]
    win_pct = (r["w"] + 0.5 * r["t"]) / played if played else 0.0
    return (win_pct, r["pf"] - r["pa"])


def compute_draft_order(teams: dict, games: list) -> dict:
    """teams: {team_id: {name, abbreviation, conference}}. games: the season's games (any status) the run may
    see, each {home_team_id, away_team_id, home_score, away_score, status, week, game_type}.
    Returns {"round_1_order": [...32 picks], "playoff_field_known": bool, "rounds_complete": [...]}."""
    rec = regular_season_records(teams, games)
    playoff = [dict(g, round=game_round(g)) for g in games if game_round(g)]
    by_round = {r: [g for g in playoff if g["round"] == r] for r in ROUNDS}

    # who lost in which round, and which rounds are fully played
    eliminated = {}
    for g in playoff:
        if g["status"] == "COMPLETED" and g["home_score"] != g["away_score"]:
            loser = g["away_team_id"] if g["home_score"] > g["away_score"] else g["home_team_id"]
            eliminated[loser] = g["round"]
    complete = [r for r in ROUNDS if by_round[r] and all(g["status"] == "COMPLETED" for g in by_round[r])]
    sb_winner = None
    if "SUPER_BOWL" in complete:
        g = by_round["SUPER_BOWL"][0]
        sb_winner = g["home_team_id"] if g["home_score"] > g["away_score"] else g["away_team_id"]

    field = {t for g in playoff for t in (g["home_team_id"], g["away_team_id"])}
    field_known = bool(by_round["WILD_CARD"])
    if field_known and not any(by_round[r] for r in ROUNDS[1:]):
        # only the Wild Card round exists: the two #1 seeds (byes) are in no game yet
        for conf in {t.get("conference") for t in teams.values()}:
            rest = [tid for tid, t in teams.items() if t.get("conference") == conf and tid not in field]
            if rest:
                field.add(max(rest, key=lambda tid: _sort_key(rec[tid])))

    def entry(tid, status, locked):
        r, t = rec[tid], teams[tid]
        diff = r["pf"] - r["pa"]
        played = r["w"] + r["l"] + r["t"]
        return {"team_id": tid, "team": t["name"], "abbreviation": t.get("abbreviation"),
                "record": f"{r['w']}-{r['l']}" + (f"-{r['t']}" if r["t"] else ""),
                "win_pct": round((r["w"] + 0.5 * r["t"]) / played, 3) if played else 0.0,
                "point_differential": f"+{diff}" if diff > 0 else str(diff),
                "selection_status": status, "locked": locked}

    worst_first = lambda ids: sorted(ids, key=lambda tid: _sort_key(rec[tid]))
    order = []
    if field_known:
        order += [entry(t, "Missed Playoffs", True) for t in worst_first(set(teams) - field)]
        for rnd in ROUNDS:
            losers = [t for t, r in eliminated.items() if r == rnd]
            done = rnd in complete
            order += [entry(t, BRACKET_STATUS[rnd] if done else BRACKET_STATUS[rnd] + " (round in progress)", done)
                      for t in worst_first(losers)]
        alive = [t for t in field if t not in eliminated]
        if sb_winner is not None:
            order += [entry(sb_winner, "Super Bowl Champion", True)]
        else:
            order += [entry(t, "Still Alive (provisional)", False) for t in worst_first(alive)]
    elif sb_winner is not None:
        # a past season stored with only its Super Bowl (no Wild Card games): every season is over
        sb_loser = next(t for t, r in eliminated.items() if r == "SUPER_BOWL")
        order = [entry(t, "Regular Season Finish", True) for t in worst_first(set(teams) - {sb_winner, sb_loser})]
        order += [entry(sb_loser, BRACKET_STATUS["SUPER_BOWL"], True), entry(sb_winner, "Super Bowl Champion", True)]
    else:
        order = [entry(t, "Regular Season (provisional)", False) for t in worst_first(teams)]
    for i, e in enumerate(order, start=1):
        e["pick"] = i
    return {"round_1_order": [dict(pick=e.pop("pick"), **e) for e in order],
            "playoff_field_known": field_known, "rounds_complete": complete}


def fetch_teams(cur) -> dict:
    cur.execute("SELECT id, name, abbreviation, conference FROM teams ORDER BY id")
    return {tid: {"name": n, "abbreviation": a, "conference": c} for tid, n, a, c in cur.fetchall()}


def fetch_games(cur, season: int, before_week=None) -> list:
    """The season's games, all statuses; with before_week only weeks < before_week (a run's cutoff)."""
    sql = """SELECT home_team_id, away_team_id, home_score, away_score, status, week, game_type
             FROM games WHERE season = %s"""
    args = [season]
    if before_week is not None:
        sql += " AND week < %s"
        args.append(before_week)
    cur.execute(sql, args)
    cols = ["home_team_id", "away_team_id", "home_score", "away_score", "status", "week", "game_type"]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
