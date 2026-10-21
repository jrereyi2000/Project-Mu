"""
Front-office standings (PROJECT_SPEC §2.18, §3.3f): one league-wide view of how well each front office is run, for
the hot seat, writers and the general consensus around front offices. Combines the two scores, both of which follow
the GM (a new GM starts at the baseline 0):
  draft = H* of the public q (engines/fo_quality: Round 1-2 picks of the GM's drafts, JR's own picks included)
  dev   = D* (engines/dev_quality: player development during the GM's tenure)
  score = (draft + dev) / 2, a missing part counting as 0 (the league baseline)
All 32 teams are ranked, JR's included (JR, 2026-10-21); his GMs are scored for the hot seat but never fired.
Writers get ranks and tier words, never the numbers.

  stored(cur, season)   from gm_team_seasons(season), what writers and #debug read
  live(cur, season)     computed now for the current GMs (the hot seat, before the season's rows exist)

CLI: python3 -m engines.fo_standing [--season S] [--live]
"""
import argparse
import json
import sys

TIER_SHARE = 4                           # top and bottom quarter of the league
EARLY_PICKS = 4                          # fewer judged picks and no development yet: writers hedge


# ------------------------------------------------------------------ pure ----
def _ranks(rows, key):
    """Competition ranks (ties share), higher value first; a None value gets no rank."""
    vals = {r["team_id"]: key(r) for r in rows}
    return {t: None if v is None else 1 + sum(1 for u in vals.values() if u is not None and u > v)
            for t, v in vals.items()}


def combine(rows):
    """rows: [{team_id, abbr, gm_name, start_season, held, draft (H* or None), dev (D* or None), hot_seat}] ->
    the same rows with score, rank, draft_rank, dev_rank (None: that part not scored for him yet), teams; sorted
    best first."""
    out = [dict(r, score=round(((r.get("draft") or 0.0) + (r.get("dev") or 0.0)) / 2, 3)) for r in rows]
    for name, key in (("rank", lambda r: r["score"]), ("draft_rank", lambda r: r.get("draft")),
                      ("dev_rank", lambda r: r.get("dev"))):
        for t, k in _ranks(out, key).items():
            next(r for r in out if r["team_id"] == t)[name] = k
    for r in out:
        r["teams"] = len(out)
    return sorted(out, key=lambda r: (r["rank"], r["abbr"]))


def tier(rank, teams):
    """'top' (best quarter), 'bottom' (worst quarter) or 'middle'."""
    if not rank or not teams:
        return None
    q = max(teams // TIER_SHARE, 1)
    return "top" if rank <= q else "bottom" if rank > teams - q else "middle"


TIER_WORDS = {"top": "one of the league's best-run front offices",
              "middle": "a middle-of-the-pack front office",
              "bottom": "one of the league's worst-run front offices"}
PART_WORDS = {"top": "among the league's best", "middle": "around the league average",
              "bottom": "among the league's worst"}
NOT_JUDGED = "not judged yet"


def hot_seat_words(score):
    """The seat after the last finished season: secure / warming / hot. None when not scored yet."""
    if score is None:
        return None
    return "hot" if score >= 1.0 else "warming" if score >= 0.5 else "secure"


def standing_facts(r, season):
    """Writer facts for one front office (pure): ranks and words, never a score. early_read: the standing rests on
    fewer than 4 judged picks and no development window yet, so it is a first impression."""
    return {"team": r["abbr"], "gm": r.get("gm_name"),
            "role": "head coach and general manager (full control)" if r.get("coach_gm") else "general manager",
            "seasons_in_job": season - r["start_season"] + 1 if r.get("start_season") else None,
            "new_gm": bool(r.get("start_season")) and r["start_season"] >= season,
            "front_office_rank": f"{r['rank']} of {r['teams']}",
            "reputation": TIER_WORDS[tier(r["rank"], r["teams"])],
            "drafting": PART_WORDS.get(tier(r["draft_rank"], r["teams"]), NOT_JUDGED),
            "player_development": PART_WORDS.get(tier(r["dev_rank"], r["teams"]), NOT_JUDGED),
            "hot_seat": hot_seat_words(r.get("hot_seat")),
            "early_read": (r.get("draft_n") or 0) < EARLY_PICKS and r.get("dev") is None}


def hot_seat_term(score, weight):
    """Hot-seat points from the front-office score: a poorly run front office heats the seat."""
    return round(-weight * (score or 0.0), 3)


# -------------------------------------------------------------------- db ----
def _gm_rows(cur):
    from engines.fo_quality import current_gms
    return current_gms(cur)


def _last_hot_seat(cur, season):
    cur.execute("""SELECT team_id, hot_seat_score FROM gm_team_seasons
                   WHERE season = %s AND hot_seat_score IS NOT NULL""", (season - 1,))
    return {r[0]: float(r[1]) for r in cur.fetchall()}


def stored(cur, season):
    """Standings from what gm_team_seasons(season) holds (fo_quality_inputs.H_star, dev_quality)."""
    gms = _gm_rows(cur)
    cur.execute("SELECT team_id, fo_quality_inputs, dev_quality FROM gm_team_seasons WHERE season = %s", (season,))
    have = {}
    for tid, inputs, dev in cur.fetchall():
        inputs = inputs if isinstance(inputs, dict) or inputs is None else json.loads(inputs)
        have[tid] = ((inputs or {}).get("H_star"), float(dev) if dev is not None else None, (inputs or {}).get("n"))
    seat = _last_hot_seat(cur, season)
    rows = [{"team_id": t, "abbr": g["abbr"], "gm_name": g["gm_name"], "start_season": g["start_season"],
             "held": g["held"], "coach_gm": g.get("coach_gm"), "draft": have.get(t, (None,) * 3)[0], "dev": have.get(t, (None,) * 3)[1],
             "draft_n": have.get(t, (None,) * 3)[2], "hot_seat": seat.get(t)} for t, g in gms.items()]
    return combine(rows)


def live(cur, season):
    """Standings computed now for the current GMs (nothing written)."""
    from engines import dev_quality, fo_quality
    fo = fo_quality.compute(cur, season)
    dev = dev_quality.compute(cur, season, log=lambda *a: None)
    gms = _gm_rows(cur)
    rows = [{"team_id": t, "abbr": g["abbr"], "gm_name": g["gm_name"], "start_season": g["start_season"],
             "held": g["held"], "coach_gm": g.get("coach_gm"), "draft": fo.get(t, {}).get("public", {}).get("H_star"),
             "draft_n": fo.get(t, {}).get("public", {}).get("n"), "dev": dev.get(t, {}).get("D_star"),
             "hot_seat": None} for t, g in gms.items()]
    return combine(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Front-office standings (draft + development)")
    ap.add_argument("--season", type=int)
    ap.add_argument("--live", action="store_true", help="compute now instead of reading gm_team_seasons")
    a = ap.parse_args(argv)
    import psycopg
    from pipeline.load_capture import DSN
    with psycopg.connect(DSN) as conn:
        cur = conn.cursor()
        if not a.season:
            cur.execute("SELECT current_season FROM league_state WHERE id = 1")
            a.season = cur.fetchone()[0]
        rows = live(cur, a.season) if a.live else stored(cur, a.season)
        conn.rollback()
    for r in rows:
        print(f"{r['rank']:>3} {r['abbr']:<4} {r['gm_name'] or '':<22} score {r['score']:+.3f}  "
              f"draft #{r['draft_rank'] or '-':<3} dev #{r['dev_rank'] or '-':<3}{'  JR' if r['held'] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
