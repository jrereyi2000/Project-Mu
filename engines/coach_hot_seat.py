"""
Coach hot seat (PROJECT_SPEC §2.19, §3.3a): Mu fires head coaches and JR mirrors each firing in Madden.

Run through engines/coaching_carousel.run (the weekly run's Step 7d) or by hand:

  python3 -m engines.coach_hot_seat [--season S] [--dry-run]

Two phases, each decided once per team:
- BLACK_MONDAY: when every regular-season game is complete and the Wild Card matchups are loaded (the Week 18 run),
  the teams that missed the playoffs (hot_seat), so the Week 19 articles already see the firings.
- PLAYOFF_EXIT: each playoff team once it is eliminated or wins the Super Bowl (the playoff and postgame runs): a
  contender that went out early, again, can lose its coach (contender_seat).
A fired coach's tenure closes at the season just finished; the hiring cycle (engines/coaching_carousel.py) names his
replacement after the Super Bowl. hot_seat() and contender_seat() are pure; a second run changes nothing.
"""
import argparse
import json
import os
import random

HOT_SEAT_LINE = 1.0
MIN_TENURE = 2                     # a coach gets at least two seasons
MIN_FIRES = 5                      # owners clear out five to eight head coaches a year (JR, 2026-10-27)
MAX_FIRES = 7                      # Black Monday at most
MAX_SEASON_FIRES = 8               # Black Monday plus playoff exits
FLOOR_LINE = 0.4                   # a coach fired to reach MIN_FIRES still needs this score
PAR_WINS = 8.5                     # the record term: 0.2 a win below this
DEEP_RUN = ("CONFERENCE", "SUPER_BOWL_LOSS", "CHAMPION")
SUPER_BOWL_YEARS = 3               # a Super Bowl win with the team protects him this many seasons
MAX_EXIT_FIRES = 2                 # playoff coaches are fired far less often
EXIT_MIN_TENURE = 3                # a playoff coach gets at least three seasons
CONTENDER_EXPECTED_WINS = 10.0     # a contender: this many expected wins or CONTENDER_WINS wins
CONTENDER_WINS = 12


def _history(r, season):
    """{season: {wins, losses, playoffs, result}} of his earlier seasons with the team (from start_season on):
    r["history"] (coach_season_lines), else last season from wins_before / losses_before / playoffs_before."""
    start = r.get("start_season") or season
    hist = {h["season"]: h for h in r.get("history") or [] if h["season"] >= start}
    if season - 1 not in hist and season - 1 >= start and r.get("wins_before") is not None:
        hist[season - 1] = {"season": season - 1, "wins": r["wins_before"], "losses": r.get("losses_before") or 0,
                            "playoffs": bool(r.get("playoffs_before")), "result": None}
    return hist


def hot_seat(rows, season, max_fires=MAX_FIRES, min_fires=MIN_FIRES):
    """Pure. rows: [{team_id, abbr, coach_id, coach_name, start_season, wins, losses, expected_wins, made_playoffs,
    history ([{season, wins, losses, playoffs, result}] of his earlier seasons with the team; or wins_before,
    losses_before, playoffs_before for last season), super_bowl_win (season of his latest Super Bowl win with the team,
    or None), user_controlled, coach_gm}] for `season`, whose regular season is complete.
    score = record 0.2 (PAR_WINS - wins) + shortfall (expected - wins)/3 (between -0.5 and 1) + losing run (0.3 for a
    second straight losing season under him, 0.6 for a third) + drought (0.1 a season past two without a playoff trip
    under him, at most 0.5) - 0.4 after a playoff trip last season - 0.3 after a conference final in the last two
    seasons - 0.3 in his second season - 1.0 within SUPER_BOWL_YEARS seasons of a Super Bowl win with the team.
    A coach can be fired when his team missed the playoffs, his tenure is at least MIN_TENURE seasons, JR doesn't run
    the team and he isn't also its GM. He is fired when score >= HOT_SEAT_LINE and a seeded draw
    (random.Random(f"coachseat:{season}:{abbr}")) falls under min(0.95, 0.4 + 0.4 (score - 1)); then, while fewer
    than min_fires are fired, the highest remaining scores at or above FLOOR_LINE (inputs.floor); the worst scores
    first, at most max_fires. Returns {team_id: {coach_id, score, fire, inputs}}."""
    out = {}
    for r in rows:
        wins, losses = r.get("wins") or 0, r.get("losses") or 0
        exp = r.get("expected_wins")
        start = r.get("start_season") or season
        tenure = season - start + 1
        hist = _history(r, season)
        run = 1 if wins < losses else 0
        while run and hist.get(season - run, {}).get("wins") is not None \
                and hist[season - run]["wins"] < hist[season - run]["losses"]:
            run += 1
        dry = 0                                        # seasons under him since his last playoff trip, this one too
        while season - dry >= start and not (dry and hist.get(season - dry, {}).get("playoffs")):
            if dry and season - dry not in hist:
                break
            dry += 1
        last = hist.get(season - 1, {})
        terms = {"record": round(0.2 * (PAR_WINS - wins), 3),
                 "shortfall": round(min(1.0, max(-0.5, (exp - wins) / 3)), 3) if exp is not None else 0.0,
                 "losing_run": 0.6 if run >= 3 else 0.3 if run == 2 else 0.0,
                 "drought": round(min(0.5, 0.1 * (dry - 2)), 3) if dry > 2 else 0.0,
                 "playoffs_before": -0.4 if tenure >= 2 and last.get("playoffs") else 0.0,
                 "deep_run": -0.3 if any(hist.get(s, {}).get("result") in DEEP_RUN
                                         for s in (season - 1, season - 2)) else 0.0,
                 "second_season": -0.3 if tenure == 2 else 0.0,
                 "super_bowl": -1.0 if (r.get("super_bowl_win") is not None
                                        and season - r["super_bowl_win"] < SUPER_BOWL_YEARS) else 0.0}
        score = round(sum(terms.values()), 3)
        p = min(0.95, max(0.0, 0.4 + 0.4 * (score - HOT_SEAT_LINE)))
        draw = random.Random(f"coachseat:{season}:{r['abbr']}").random()
        why_not = ("made the playoffs" if r.get("made_playoffs") else
                   "first season" if tenure < MIN_TENURE else
                   "JR's team" if r.get("user_controlled") else
                   "also the GM" if r.get("coach_gm") else None)
        out[r["team_id"]] = {"coach_id": r["coach_id"], "score": score,
                             "fire": why_not is None and score >= HOT_SEAT_LINE and draw < p,
                             "inputs": {"coach": r.get("coach_name"), "wins": wins, "losses": losses,
                                        "expected_wins": exp, "tenure": tenure, "terms": terms,
                                        "losing_run": run, "seasons_without_playoffs": dry,
                                        "made_playoffs": bool(r.get("made_playoffs")), "p": round(p, 3),
                                        "draw": round(draw, 3),
                                        "safe": why_not or ("below the line" if score < HOT_SEAT_LINE else None)}}
    by_score = sorted(out, key=lambda t: (-out[t]["score"], t))
    fired = [t for t in by_score if out[t]["fire"]]
    for t in by_score:                                 # owners who want change anyway
        if len(fired) >= min(min_fires, max_fires):
            break
        o = out[t]
        if not o["fire"] and o["score"] >= FLOOR_LINE and o["inputs"]["safe"] in (None, "below the line"):
            o["fire"], o["inputs"]["floor"] = True, True
            fired.append(t)
    fired.sort(key=lambda t: (-out[t]["score"], t))
    for t in fired[max_fires:]:
        out[t]["fire"] = False
        out[t]["inputs"]["capped"] = True
    return out


def contender_seat(rows, season, max_fires=MAX_EXIT_FIRES):
    """Pure. rows: playoff teams of `season` that are out (eliminated or champion): hot_seat's fields plus
    playoff_wins, champion and flops_before (earlier playoff trips under him, with complete playoff data, that won no
    playoff game). score = 0.5 for no playoff win + 0.5 for a contender (CONTENDER_EXPECTED_WINS expected wins or
    CONTENDER_WINS wins) + 0.5 per earlier one-and-done (at most 2) + any regular-season shortfall (expected - wins)/2
    - 1.0 within SUPER_BOWL_YEARS seasons of a Super Bowl win with the team. Fired when he isn't the champion, his
    tenure is at least EXIT_MIN_TENURE, score >= 1.0, JR doesn't run the team, he isn't also its GM, and the seeded
    draw (random.Random(f"coachexit:{season}:{abbr}")) falls under min(0.6, 0.2 + 0.3 (score - 1)); worst first, at
    most max_fires. Returns {team_id: {coach_id, score, fire, inputs}}."""
    out = {}
    for r in rows:
        wins = r.get("wins") or 0
        exp = r.get("expected_wins")
        tenure = season - (r.get("start_season") or season) + 1
        contender = (exp is not None and exp >= CONTENDER_EXPECTED_WINS) or wins >= CONTENDER_WINS
        terms = {"no_playoff_win": 0.5 if not r.get("playoff_wins") else 0.0,
                 "contender": 0.5 if contender else 0.0,
                 "flops_before": 0.5 * min(r.get("flops_before") or 0, 2),
                 "shortfall": round(max(0.0, (exp - wins) / 2), 3) if exp is not None else 0.0,
                 "super_bowl": -1.0 if (r.get("super_bowl_win") is not None
                                        and season - r["super_bowl_win"] < SUPER_BOWL_YEARS) else 0.0}
        score = sum(terms.values())
        p = min(0.6, max(0.0, 0.2 + 0.3 * (score - HOT_SEAT_LINE)))
        draw = random.Random(f"coachexit:{season}:{r['abbr']}").random()
        why_not = ("won the Super Bowl" if r.get("champion") else
                   "tenure under 3 seasons" if tenure < EXIT_MIN_TENURE else
                   "JR's team" if r.get("user_controlled") else
                   "also the GM" if r.get("coach_gm") else
                   "below the line" if score < HOT_SEAT_LINE else None)
        out[r["team_id"]] = {"coach_id": r["coach_id"], "score": round(score, 3),
                             "fire": why_not is None and draw < p,
                             "inputs": {"coach": r.get("coach_name"), "wins": wins, "losses": r.get("losses") or 0,
                                        "expected_wins": exp, "tenure": tenure, "terms": terms,
                                        "playoff_wins": r.get("playoff_wins") or 0, "made_playoffs": True,
                                        "p": round(p, 3), "draw": round(draw, 3), "safe": why_not}}
    fired = sorted((t for t, v in out.items() if v["fire"]), key=lambda t: -out[t]["score"])
    for t in fired[max(0, max_fires):]:
        out[t]["fire"] = False
        out[t]["inputs"]["capped"] = True
    return out


def playoff_results(cur, season):
    """{team_id: {wins, out, champion}} for the teams in `season`'s completed playoff games: out = lost a game or
    won the Super Bowl."""
    cur.execute("""SELECT game_type, home_team_id, away_team_id, home_score, away_score FROM games
                   WHERE season = %s AND game_type <> 'REGULAR_SEASON' AND status = 'COMPLETED'""", (season,))
    res = {}
    for gt, h, a, hs, as_ in cur.fetchall():
        if hs is None or as_ is None or hs == as_:
            continue
        w, l = (h, a) if hs > as_ else (a, h)
        res.setdefault(w, {"wins": 0, "out": False, "champion": False})["wins"] += 1
        res.setdefault(l, {"wins": 0, "out": False, "champion": False})["out"] = True
        if gt == "SUPER_BOWL":
            res[w]["out"] = res[w]["champion"] = True
    return res


def flops_before(cur, season, tenures):
    """{team_id: earlier seasons under the current coach (tenures: {team_id: start_season}) in which the team made the
    playoffs and won no playoff game}, counting only seasons whose Wild Card round is stored."""
    cur.execute("SELECT DISTINCT season FROM games WHERE game_type = 'WILD_CARD' AND season < %s", (season,))
    out = {t: 0 for t in tenures}
    for s in (r[0] for r in cur.fetchall()):
        made, res = playoff_teams(cur, s), playoff_results(cur, s)
        for t, start in tenures.items():
            if start <= s and t in made and not res.get(t, {}).get("wins"):
                out[t] += 1
    return out


def ready(cur, season):
    """True when `season`'s regular season is complete and its Wild Card games are loaded."""
    cur.execute("""SELECT count(*) FILTER (WHERE game_type = 'REGULAR_SEASON'),
                          count(*) FILTER (WHERE game_type = 'REGULAR_SEASON' AND status <> 'COMPLETED'),
                          count(*) FILTER (WHERE game_type = 'WILD_CARD')
                   FROM games WHERE season = %s""", (season,))
    total, open_, wild_card = cur.fetchone()
    return total > 0 and open_ == 0 and wild_card > 0


def playoff_teams(cur, season):
    """The Wild Card teams plus each conference's #1 seed: a team in a later round without a Wild Card game, or,
    before the Divisional round is loaded, the best record without a Wild Card game."""
    cur.execute("""SELECT g.game_type = 'WILD_CARD', t.id, t.conference FROM games g
                   JOIN teams t ON t.id IN (g.home_team_id, g.away_team_id)
                   WHERE g.season = %s AND g.game_type <> 'REGULAR_SEASON'""", (season,))
    rows = cur.fetchall()
    wild_card = {tid for wc, tid, _ in rows if wc}
    if not wild_card:
        return set()
    byes = {conf: tid for wc, tid, conf in rows if not wc and tid not in wild_card}
    cur.execute("""SELECT t.id, t.conference, COALESCE(ts.wins, 0) - COALESCE(ts.losses, 0) FROM teams t
                   LEFT JOIN team_seasons ts ON ts.team_id = t.id AND ts.season = %s
                   WHERE t.abbreviation IS NOT NULL ORDER BY 3 DESC, t.id""", (season,))
    for tid, conf, _ in cur.fetchall():
        if tid not in wild_card and conf not in byes:
            byes[conf] = tid
    return wild_card | set(byes.values())


def read_rows(cur, season):
    made = playoff_teams(cur, season)
    before = playoff_teams(cur, season - 1)
    cur.execute("""SELECT t.id, t.abbreviation, c.id, c.coach_name, ct.start_season, ts.wins, ts.losses,
                          ts.expected_wins, tb.wins, tb.losses,
                          (SELECT max(g.season) FROM games g
                            WHERE g.game_type = 'SUPER_BOWL' AND g.status = 'COMPLETED'
                              AND g.season BETWEEN ct.start_season AND %s
                              AND ((g.home_team_id = t.id AND g.home_score > g.away_score)
                                OR (g.away_team_id = t.id AND g.away_score > g.home_score))),
                          COALESCE(st.user_controlled, false),
                          EXISTS (SELECT 1 FROM gm_profiles g WHERE g.team_id = t.id AND g.coach_id = c.id
                                    AND g.end_season IS NULL)
                   FROM coach_tenures ct JOIN coaches c ON c.id = ct.coach_id JOIN teams t ON t.id = ct.team_id
                   LEFT JOIN team_seasons ts ON ts.team_id = t.id AND ts.season = %s
                   LEFT JOIN team_seasons tb ON tb.team_id = t.id AND tb.season = %s
                   LEFT JOIN gm_team_settings st ON st.team_id = t.id
                   WHERE ct.start_season <= %s AND (ct.end_season IS NULL OR ct.end_season >= %s)""",
                (season, season, season - 1, season, season))
    keys = ("team_id", "abbr", "coach_id", "coach_name", "start_season", "wins", "losses", "expected_wins",
            "wins_before", "losses_before", "super_bowl_win", "user_controlled", "coach_gm")
    rows = [dict(zip(keys, r)) for r in cur.fetchall()]
    for r in rows:
        r["expected_wins"] = float(r["expected_wins"]) if r["expected_wins"] is not None else None
        r["made_playoffs"] = r["team_id"] in made
        r["playoffs_before"] = r["team_id"] in before
    cur.execute("SELECT to_regclass('coach_season_lines') IS NOT NULL")
    if cur.fetchone()[0]:
        cur.execute("""SELECT coach_id, team_id, season, wins, losses, playoff_result FROM coach_season_lines
                       WHERE season < %s ORDER BY season""", (season,))
        lines = {}
        for cid, tid, s, w, l, res in cur.fetchall():
            lines.setdefault((cid, tid), []).append({"season": s, "wins": w, "losses": l, "result": res,
                                                     "playoffs": res is not None and res != "MISSED"})
        for r in rows:
            r["history"] = [h for h in lines.get((r["coach_id"], r["team_id"]), [])
                            if h["season"] >= (r["start_season"] or season)]
    return rows


def decided(cur, season, phase="BLACK_MONDAY"):
    cur.execute("SELECT count(*) FROM coach_hot_seat WHERE season = %s AND phase = %s", (season, phase))
    return cur.fetchone()[0] > 0


def _store(cur, season, phase, rows, decisions, rep, log):
    by_team = {r["team_id"]: r for r in rows}
    for tid, h in sorted(decisions.items(), key=lambda kv: -kv[1]["score"]):
        cur.execute("""INSERT INTO coach_hot_seat (season, team_id, coach_id, score, fired, inputs, phase)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (season, tid, h["coach_id"], h["score"], h["fire"], json.dumps(h["inputs"]), phase))
        if not h["fire"]:
            continue
        # He coached this season; the hiring cycle names his successor, whose tenure starts next season.
        cur.execute("""UPDATE coach_tenures SET end_season = %s
                       WHERE coach_id = %s AND team_id = %s AND (end_season IS NULL OR end_season > %s)""",
                    (season, h["coach_id"], tid, season))
        r = by_team[tid]
        rep["fired"].append({"team": r["abbr"], "coach": r["coach_name"], "score": h["score"], "phase": phase})
        log(f"  coach hot seat ({phase}): {r['abbr']} fires {r['coach_name']} (score {h['score']})")
    rep["decided"] += [by_team[t]["team_id"] for t in decisions]


def run(cur, season, log=print):
    """Decide what is ready of `season`'s hot seat inside the caller's transaction: Black Monday once, then each
    playoff team once it is out. Returns {season, status, fired: [{team, coach, score, phase}] (new this run),
    decided: [team ids decided this run]}; status 'not ready' before Black Monday."""
    rep = {"season": season, "fired": [], "decided": []}
    if not decided(cur, season):
        if not ready(cur, season):
            rep["status"] = "not ready"
            return rep
        rows = [r for r in read_rows(cur, season) if not r["made_playoffs"]]
        _store(cur, season, "BLACK_MONDAY", rows, hot_seat(rows, season), rep, log)
    cur.execute("SELECT team_id FROM coach_hot_seat WHERE season = %s", (season,))
    done = {r[0] for r in cur.fetchall()}
    res = playoff_results(cur, season)
    rows = [r for r in read_rows(cur, season) if r["made_playoffs"] and r["team_id"] not in done
            and res.get(r["team_id"], {}).get("out")]
    if rows:
        flops = flops_before(cur, season, {r["team_id"]: r["start_season"] for r in rows})
        for r in rows:
            r.update(playoff_wins=res[r["team_id"]]["wins"], champion=res[r["team_id"]]["champion"],
                     flops_before=flops[r["team_id"]])
        cur.execute("""SELECT count(*) FILTER (WHERE phase = 'PLAYOFF_EXIT'), count(*) FROM coach_hot_seat
                       WHERE season = %s AND fired""", (season,))
        exits, total = cur.fetchone()
        left = min(MAX_EXIT_FIRES - exits, MAX_SEASON_FIRES - total)
        _store(cur, season, "PLAYOFF_EXIT", rows, contender_seat(rows, season, left), rep, log)
    rep["status"] = "decided" if rep["decided"] else "up to date"
    return rep


def fired(cur, season):
    """[{team, coach, score, phase}] for the coaches the hot seat fired after `season`."""
    cur.execute("""SELECT t.abbreviation, c.coach_name, h.score, h.phase FROM coach_hot_seat h
                   JOIN teams t ON t.id = h.team_id JOIN coaches c ON c.id = h.coach_id
                   WHERE h.season = %s AND h.fired ORDER BY h.phase, h.score DESC""", (season,))
    return [{"team": a, "coach": c, "score": float(s), "phase": p} for a, c, s, p in cur.fetchall()]


def installed(cur):
    cur.execute("SELECT to_regclass('coach_hot_seat') IS NOT NULL")
    return cur.fetchone()[0]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, help="the season just played (default league_state.current_season)")
    ap.add_argument("--dry-run", action="store_true", help="print the scores and roll back")
    a = ap.parse_args(argv)
    import psycopg
    dsn = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        cur.execute("SELECT current_season FROM league_state WHERE id = 1")
        season = a.season or cur.fetchone()[0]
        if a.dry_run:
            rows = read_rows(cur, season)
            res = playoff_results(cur, season)
            flops = flops_before(cur, season, {r["team_id"]: r["start_season"] for r in rows})
            for r in rows:
                r.update(playoff_wins=res.get(r["team_id"], {}).get("wins", 0),
                         champion=res.get(r["team_id"], {}).get("champion", False), flops_before=flops[r["team_id"]])
            out = hot_seat([r for r in rows if not r["made_playoffs"]], season)
            out.update(contender_seat([r for r in rows if r["made_playoffs"]], season))
            for tid, h in sorted(out.items(), key=lambda kv: -kv[1]["score"]):
                i = h["inputs"]
                print(f"{i['coach']:<24} {h['score']:>6.2f}  p {i['p']:.2f} draw {i['draw']:.2f}  "
                      f"{'FIRE' if h['fire'] else i['safe'] or ('capped' if i.get('capped') else 'survives draw')}")
            if not ready(cur, season):
                print(f"note: {season}'s regular season isn't complete or the Wild Card games aren't loaded; "
                      "a real run would wait")
            conn.rollback()
            return
        rep = run(cur, season)
        print(json.dumps(rep, indent=1, default=str))
        conn.commit()


if __name__ == "__main__":
    main()
