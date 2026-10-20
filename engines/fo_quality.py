"""
Front-office quality q_team from draft track records (docs/team_management/04_SCOUTING_AND_DRAFT_PLAN.md §2.4;
PROJECT_SPEC §2.18, §3.3f). q scales each team's scouting error (team boards): lower = a sharper front office.

Hit score per Round 1-2 pick: h = (OVR_now - E[OVR | slot, years since draft]) / sd + DEV_BONUS.
  E is the smoothed, non-increasing OVR-by-pick curve of every drafted player with the same years since his draft
  (trade_value._smooth_monotone), refitted at every recompute. DEV_BONUS (Star +0.5, Superstar +1.0, X-Factor +1.5)
  applies only once the dev is revealed: Madden hides a rookie's dev until 500 snaps ('Hidden'), and a NULL dev was
  never scanned; both are unknown, not Normal, and the pick is re-scored when a later scan reveals it.
  Labels: HIT h >= 0.5, MISS h <= -0.5, BUST h <= -1.2, else OK. A player who left still counts for the team that
  drafted him (the Draft Recap shows the drafting team, K7).
Team: H = sum_d w_d sum h / sum_d w_d n_d over the last 3 drafts (w 1.0, 0.6, 0.35, newest first), shrunk
H* = H n / (n + 4), q = clamp(1 - 0.25 H*, 0.7, 1.4). No picks: q = 1.
JR's own picks don't count (Q4): picks by a team he controls now, and Draft Room picks recorded JR_TEAM; a team he
controls keeps its last q (not rewritten).

CLI: python3 -m engines.fo_quality --season 2036 [--credit drafted|roster] [--dry-run]
"""
import argparse
import json
import math
import sys

WEIGHTS = (1.0, 0.6, 0.35)
SHRINK_K = 4
Q_SLOPE = 0.25
Q_MIN, Q_MAX = 0.7, 1.4
HIT, MISS, BUST = 0.5, -0.5, -1.2
SD_FLOOR = 2.0
SCORED_PICKS = 64                       # Rounds 1-2
DEV_BONUS = {"NORMAL": 0.0, "STAR": 0.5, "SUPERSTAR": 1.0, "X-FACTOR": 1.5, "SUPERSTAR X-FACTOR": 1.5}
UNREVEALED = {"HIDDEN", ""}


# ------------------------------------------------------------------ pure ----
def dev_bonus(dev):
    """(bonus, known). 'Hidden' (under 500 snaps) or NULL: unknown, no term."""
    d = (dev or "").strip().upper()
    if d in UNREVEALED:
        return 0.0, False
    for k in sorted(DEV_BONUS, key=len, reverse=True):
        if d == k or d.startswith(k):
            return DEV_BONUS[k], True
    return 0.0, True


def label(h):
    return "BUST" if h <= BUST else "MISS" if h <= MISS else "HIT" if h >= HIT else "OK"


def fit_expected(rows, picks=224):
    """rows: [(overall_pick, ovr)] for one years-since-draft group -> (curve indexed pick-1, sd of residuals)."""
    from engines.trade_value import _smooth_monotone
    raw = [None] * picks
    by = {}
    for pk, o in rows:
        if pk and 1 <= pk <= picks and o is not None:
            by.setdefault(pk, []).append(o)
    for pk, os_ in by.items():
        raw[pk - 1] = sum(os_) / len(os_)
    curve = _smooth_monotone(raw)
    res = [o - curve[pk - 1] for pk, o in rows if pk and 1 <= pk <= picks and o is not None]
    sd = math.sqrt(sum(r * r for r in res) / max(len(res) - 1, 1)) if res else SD_FLOOR
    return curve, max(sd, SD_FLOOR)


def hit_score(ovr, expected, sd, dev):
    bonus, known = dev_bonus(dev)
    return round((ovr - expected) / sd + bonus, 3), known


def team_q(picks, season):
    """picks: [{draft_season, h}] for one team (only the last 3 drafts count). -> {q, H, H_star, n}."""
    num = den = 0.0
    n = 0
    for p in picks:
        age = season - p["draft_season"]
        if not 0 <= age < len(WEIGHTS):
            continue
        num += WEIGHTS[age] * p["h"]
        den += WEIGHTS[age]
        n += 1
    if not n:
        return {"q": 1.0, "H": None, "H_star": None, "n": 0}
    H = num / den
    H_star = H * n / (n + SHRINK_K)
    q = min(max(1 - Q_SLOPE * H_star, Q_MIN), Q_MAX)
    return {"q": round(q, 2), "H": round(H, 3), "H_star": round(H_star, 3), "n": n}


def streaks(picks):
    """First-round streaks for writers: consecutive most recent drafts whose Round 1 picks were all MISS/BUST
    (or all HIT). picks: [{draft_season, round, label}]."""
    by = {}
    for p in picks:
        if p["round"] == 1:
            by.setdefault(p["draft_season"], []).append(p["label"])
    out = {"first_round_misses": 0, "first_round_hits": 0}
    for key, ok in (("first_round_misses", ("MISS", "BUST")), ("first_round_hits", ("HIT",))):
        for ds in sorted(by, reverse=True):
            if all(lb in ok for lb in by[ds]):
                out[key] += 1
            else:
                break
    return out


def score_all(rows, season, credit="drafted", excluded_teams=(), jr_picks=()):
    """rows: drafted players with a season row: {draft_season, overall_pick, drafted_team_id, roster_team_id,
    player_id, name, position, ovr, dev}. Returns {team_id: [scored pick]} for Round 1-2 picks in the window.
    Curves are fitted on every drafted player by years since his draft."""
    groups = {}
    for r in rows:
        groups.setdefault(season - r["draft_season"], []).append((r["overall_pick"], r["ovr"]))
    fits = {y: fit_expected(g) for y, g in groups.items()}
    out = {}
    for r in rows:
        years = season - r["draft_season"]
        if not (0 <= years < len(WEIGHTS)) or not r["overall_pick"] or r["overall_pick"] > SCORED_PICKS:
            continue
        team = r["drafted_team_id"] if credit == "drafted" else r["roster_team_id"]
        if team is None or team in excluded_teams or (r["draft_season"], r["overall_pick"]) in jr_picks:
            continue
        curve, sd = fits[years]
        exp = curve[r["overall_pick"] - 1]
        h, known = hit_score(r["ovr"], exp, sd, r["dev"])
        out.setdefault(team, []).append({
            "draft_season": r["draft_season"], "round": (r["overall_pick"] - 1) // 32 + 1,
            "pick": r["overall_pick"], "player_id": r["player_id"], "name": r["name"], "position": r["position"],
            "ovr": r["ovr"], "expected": round(exp, 2), "years": years, "dev": r["dev"], "dev_known": known,
            "h": h, "label": label(h)})
    return out


# -------------------------------------------------------------------- db ----
def fetch_rows(cur, season):
    """Drafted players (draft classes season-2 .. season) linked to a `season` player row."""
    cur.execute("""SELECT d.draft_season, d.actual_pick, d.drafted_team_id, ps.team_id, d.player_id,
                          COALESCE(p.full_name, p.player_name, d.full_name), d.position, ps.overall_rating, ps.dev_trait
                   FROM draft_prospects d
                   JOIN player_seasons ps ON ps.player_id = d.player_id AND ps.season = %s
                   JOIN players p ON p.id = d.player_id
                   WHERE d.actual_pick IS NOT NULL AND d.draft_season BETWEEN %s AND %s""",
                (season, season - len(WEIGHTS) + 1, season))
    cols = ("draft_season", "overall_pick", "drafted_team_id", "roster_team_id", "player_id", "name", "position",
            "ovr", "dev")
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def unlinked_count(cur, season):
    cur.execute("""SELECT count(*) FROM draft_prospects d
                   WHERE d.actual_pick BETWEEN 1 AND %s AND d.draft_season BETWEEN %s AND %s
                     AND NOT EXISTS (SELECT 1 FROM player_seasons ps WHERE ps.player_id = d.player_id
                                     AND ps.season = %s)""",
                (SCORED_PICKS, season - len(WEIGHTS) + 1, season, season))
    return cur.fetchone()[0]


def jr_draft_picks(cur):
    cur.execute("SELECT to_regclass('scout_draft_picks') IS NOT NULL")
    if not cur.fetchone()[0]:
        return set()
    cur.execute("SELECT draft_season, overall_pick FROM scout_draft_picks WHERE choice_source = 'JR_TEAM'")
    return set(cur.fetchall())


def recompute(cur, season, credit="drafted", dry_run=False, log=print):
    """q for every team from its Round 1-2 picks in the last 3 drafts, written to gm_team_seasons(season)
    (fo_quality, fo_quality_inputs, fo_quality_at). Teams JR controls keep their q. Idempotent: same inputs,
    same values. Returns {team_id: result}."""
    cur.execute("""SELECT t.id, t.abbreviation, COALESCE(st.user_controlled, false), s.fo_quality
                   FROM teams t JOIN gm_team_seasons s ON s.team_id = t.id AND s.season = %s
                   LEFT JOIN gm_team_settings st ON st.team_id = t.id ORDER BY t.abbreviation""", (season,))
    teams = cur.fetchall()
    if not teams:
        raise SystemExit(f"no gm_team_seasons rows for {season}")
    held = {t[0] for t in teams if t[2]}
    scored = score_all(fetch_rows(cur, season), season, credit, held, jr_draft_picks(cur))
    out = {}
    for tid, abbr, user, old_q in teams:
        picks = sorted(scored.get(tid, []), key=lambda p: (p["draft_season"], p["pick"]))
        res = team_q(picks, season)
        res.update(abbr=abbr, held=user, old_q=float(old_q) if old_q is not None else None, picks=picks)
        out[tid] = res
        tag = "held (JR controls the team)" if user else f"q {res['q']:.2f}"
        log(f"  {abbr:<4} {tag:<28} n={res['n']} H*={res['H_star']}  " +
            ", ".join(f"#{p['pick']} {p['name']} {p['label']}{'' if p['dev_known'] else ' (dev hidden)'}"
                      for p in picks))
    log(f"fo_quality {season} ({credit} credit): {sum(1 for r in out.values() if r['n'])} teams scored, "
        f"{unlinked_count(cur, season)} R1-R2 picks unlinked (skipped)")
    if dry_run:
        log("  dry run: nothing written")
        return out
    for tid, res in out.items():
        if res["held"]:
            continue
        inputs = {"season": season, "credit": credit, "H": res["H"], "H_star": res["H_star"], "n": res["n"],
                  "weights": WEIGHTS, "picks": res["picks"], "streaks": streaks(res["picks"])}
        cur.execute("""UPDATE gm_team_seasons SET fo_quality = %s, fo_quality_inputs = %s, fo_quality_at = now()
                       WHERE team_id = %s AND season = %s""", (res["q"], json.dumps(inputs), tid, season))
    return out


def team_draft_record(cur, team_id, season=None):
    """Writer facts (step 5): the team's scored picks with labels and its streaks, from the latest stored inputs.
    Labels only: never q or h."""
    cur.execute("""SELECT season, fo_quality_inputs FROM gm_team_seasons
                   WHERE team_id = %s AND fo_quality_inputs IS NOT NULL AND (%s::int IS NULL OR season <= %s)
                   ORDER BY season DESC LIMIT 1""", (team_id, season, season))
    r = cur.fetchone()
    if not r:
        return None
    inputs = r[1] if isinstance(r[1], dict) else json.loads(r[1])
    picks = [{k: p[k] for k in ("draft_season", "round", "pick", "name", "position", "label", "years")}
             for p in inputs.get("picks", [])]
    return {"season": r[0], "picks": picks, "streaks": inputs.get("streaks") or streaks(inputs.get("picks", []))}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Front-office quality (q_team) from draft track records")
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--credit", choices=("drafted", "roster"), default="drafted",
                    help="credit the drafting team (default; the Draft Recap shows it, K7) or the current one")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    import psycopg
    from pipeline.load_capture import DSN
    with psycopg.connect(DSN) as conn:
        recompute(conn.cursor(), a.season, a.credit, a.dry_run)
        conn.rollback() if a.dry_run else conn.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
