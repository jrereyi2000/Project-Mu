"""GM records across tenures (§3.3f): a GM's results in every job he has held in this league, and the team's earlier
GMs. Read from `gm_profiles` (one row per tenure) and the season archive's `team_season_results.gm_id` (finished
seasons only, 2030 on, the first GM tenures). Writers get records and finishes, never a score."""

PLAYOFF_DEPTH = ("MISSED", "WILD_CARD", "DIVISIONAL", "CONFERENCE", "SUPER_BOWL_LOSS", "CHAMPION")
FINISH_WORDS = {"MISSED": "missed the playoffs", "WILD_CARD": "lost in the Wild Card round",
                "DIVISIONAL": "lost in the Divisional round", "CONFERENCE": "lost in the conference championship",
                "SUPER_BOWL_LOSS": "lost the Super Bowl", "CHAMPION": "won the Super Bowl"}


def _record(w, l, t):
    return f"{w}-{l}-{t}" if t else f"{w}-{l}"


def tenure_summary(seasons):
    """One tenure's finished seasons (dicts with season, wins, losses, ties, division_place, playoff_result) as
    writer facts (pure). None when no season of it has finished yet."""
    if not seasons:
        return None
    seasons = sorted(seasons, key=lambda s: s["season"])
    w, l, t = (sum(s[k] or 0 for s in seasons) for k in ("wins", "losses", "ties"))
    results = [s["playoff_result"] or "MISSED" for s in seasons]
    best = max(results, key=PLAYOFF_DEPTH.index)
    return {"regular_season_record": _record(w, l, t),
            "winning_seasons": sum(1 for s in seasons if (s["wins"] or 0) > (s["losses"] or 0)),
            "division_titles": sum(1 for s in seasons if s["division_place"] == 1),
            "playoff_trips": sum(1 for r in results if r != "MISSED"),
            "super_bowl_appearances": sum(1 for r in results if r in ("SUPER_BOWL_LOSS", "CHAMPION")),
            "titles": [s["season"] for s, r in zip(seasons, results) if r == "CHAMPION"],
            "best_finish": FINISH_WORDS[best],
            "by_season": [f"{s['season']}: {_record(s['wins'] or 0, s['losses'] or 0, s['ties'] or 0)}, "
                          f"{FINISH_WORDS[r]}" for s, r in zip(seasons, results)]}


def tenure_facts(tenure, seasons, season):
    """A tenure row (dict from `tenures`) and its finished seasons as writer facts (pure)."""
    end = tenure["end_season"]
    out = {"gm": tenure["gm_name"], "team": tenure["team"],
           "role": ("head coach and general manager (full control)" if tenure["coach_id"]
                    else "general manager"),
           "seasons": f"{tenure['start_season']}–{end if end is not None else 'present'}",
           "current": end is None or end >= season}
    if end is not None and end < season and tenure["end_reason"]:
        out["how_it_ended"] = tenure["end_reason"]
    summary = tenure_summary(seasons)
    out.update(summary or {"record": "no finished season yet"})
    return out


TENURE_SQL = """SELECT g.id, g.team_id, t.name, g.gm_name, g.start_season, g.end_season, g.end_reason, g.coach_id
                FROM gm_profiles g JOIN teams t ON t.id = g.team_id WHERE {} ORDER BY g.start_season, g.id"""
TENURE_KEYS = ("id", "team_id", "team", "gm_name", "start_season", "end_season", "end_reason", "coach_id")


def _tenures(cur, where, args):
    cur.execute(TENURE_SQL.format(where), args)
    return [dict(zip(TENURE_KEYS, r)) for r in cur.fetchall()]


def _seasons(cur, gm_ids, season):
    if not gm_ids:
        return {}
    cur.execute("""SELECT gm_id, season, wins, losses, ties, division_place, playoff_result FROM team_season_results
                   WHERE gm_id = ANY(%s) AND season < %s""", (list(gm_ids), season))
    out = {}
    for gm_id, *row in cur.fetchall():
        out.setdefault(gm_id, []).append(dict(zip(("season", "wins", "losses", "ties", "division_place",
                                                   "playoff_result"), row)))
    return out


def gm_record(cur, team_id, season):
    """The team's GM as of `season` with every tenure of his in this league (matched by name: persona names are
    unique league-wide), and the team's earlier GMs, newest first. Only seasons before `season` count (finished).
    None when the team has no GM tenure covering `season`."""
    current = _tenures(cur, "g.team_id = %s AND g.start_season <= %s AND (g.end_season IS NULL OR g.end_season >= %s)",
                       (team_id, season, season))
    if not current:
        return None
    gm = current[-1]
    career = _tenures(cur, "g.gm_name = %s AND g.start_season <= %s", (gm["gm_name"], season))
    earlier = [t for t in _tenures(cur, "g.team_id = %s AND g.start_season < %s", (team_id, gm["start_season"]))
               if t["id"] != gm["id"]]
    lines = _seasons(cur, {t["id"] for t in career + earlier}, season)
    cur.execute("SELECT min(season) FROM team_season_results WHERE team_id = %s AND gm_id IS NOT NULL", (team_id,))
    first_on_record = cur.fetchone()[0]
    return {"season": season, "gm": gm["gm_name"],
            "career": [tenure_facts(t, lines.get(t["id"], []), season) for t in career],
            "earlier_gms": [tenure_facts(t, lines.get(t["id"], []), season) for t in reversed(earlier)],
            "gms_on_record_since": first_on_record}
