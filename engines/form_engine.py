"""
Form engine (docs/in_progress/PROGRESSION_PLAN.md §5.11, phase E; V2_CORE_BUILD_PLAN.md §2.4, U10).

Form is how a player is playing this month: up to about ±3 OVR from his last few games, separate from his underlying
(base) ratings, and neutral again every offseason. D13 (JR, 2026-10-30): Madden's morale is pinned at 50 for every
player on every weekly write, and form reaches the game only as a ratings overlay on top of the base ratings.

  weekly_form(cur, season, week)  -> {player_id: form}   OVR points, -3..+3; {} at week <= 0 and in the offseason
  form_from_games(games, group, group_mean, group_sd, ...) -> form   the pure scoring of one player's game lines
  overlay(base_ratings, position, archetype, form) -> {Rating: delta}   OVR effect (ovr_weights) = round(form)
  form_edits(cur, snapshot, season, week) -> save_writer edits document: every player's base ratings plus overlay,
                                             OverallRating / OverallGrade0-4 / PlayerType recomputed (dual-write),
                                             ConfidenceRating 50 for all; stores the week's player_form rows

Scoring (form_from_games): each game line's production (engines/progression.production for the player's position
group, per game; per down scaled to the group's downs per game for OL/FRONT/DB) is compared, in group standard
deviations, with the player's own norm: his mean production over the season so far, shrunk toward the group mean
by NORM_PRIOR_GAMES. The last FORM_GAMES games are weighted RECENT_WEIGHTS (newest most), the difference is scaled by
FORM_PER_SD and by playing time (effective games / (effective games + SHRINK_GAMES), a game counting as its share of
the group's usual snaps), and clamped to ±FORM_MAX.

"Base" ratings are Project Mu's stored ratings: each player's newest player_attributes row (what pipeline/drift_check
reads as base, with the newest player_form overlay on top). A player at form 0 gets his base ratings back, which
removes last week's overlay, and his player_form row has overlay {} (drift_check then expects base).

  python3 -m engines.form_engine --snapshot <save.json> --season S --week W [--out edits.json] [--dsn ...] [--dry-run]
  node pipeline/save_reader/save_writer.mjs <CAREER-save> edits.json --out <new-save>
"""
import argparse
import json
import logging
import math
import numbers
import os
import sys
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence

from engines import ovr_weights as ow
from engines import progression
from engines import progression_save as psave

log = logging.getLogger("form_engine")

FORM_MAX = 3.0                       # OVR points either way (§5.11: "within about ±3 OVR")
FORM_GAMES = 4                       # the last few games
RECENT_WEIGHTS = (0.4, 0.3, 0.2, 0.1)   # newest game first
NORM_PRIOR_GAMES = 4                 # the own norm is shrunk toward the group mean by this many games
FORM_PER_SD = 1.5                    # OVR points per group standard deviation above / below the norm
SHRINK_GAMES = 2.0                   # playing time: effective games / (effective games + this)
FIRST_WEEK = 1
LAST_WEEK = 23                       # weeks 1-18 regular season, 19-23 playoffs; 24+ offseason (contracts_engine)
NEUTRAL_MORALE = psave.MORALE_NEUTRAL
NEVER_TOUCH = ("Speed", "Acceleration", "Injury")


def in_season(week: Optional[int]) -> bool:
    """Form exists only in weeks 1..LAST_WEEK; week 0 and the offseason are neutral."""
    return week is not None and FIRST_WEEK <= week <= LAST_WEEK


# ------------------------------------------------------------- scoring (pure) ----
def game_production(group: str, line: Dict[str, Any], downs_per_game: Optional[float] = None) -> Optional[float]:
    """One game line's production for a position group (None: nothing to score). OL..P reuse
    progression.production by expanding the line to PERF_MIN_GAMES identical games (its minimum-games gate), so a
    game scores exactly like a season's per-game rate. K and P use per-game measures, since production's attempt
    thresholds are season-sized."""
    g = lambda k: float(line.get(k) or 0)
    if group == "K":
        if g("kick_fga") + g("kick_xpa") <= 0:
            return None
        return 3 * g("kick_fgm") - 3 * (g("kick_fga") - g("kick_fgm")) + 2 * g("kick_fgm_50") \
            + g("kick_xpm") - 2 * (g("kick_xpa") - g("kick_xpm"))
    if group == "P":
        return g("punt_net_average") if g("punt_punts") > 0 else None
    n = progression.PERF_MIN_GAMES
    expanded = {k: (float(v) * n if isinstance(v, numbers.Number) and not isinstance(v, bool) else v)
                for k, v in line.items()}
    expanded["games_played"] = n
    return progression.production(group, expanded, downs_per_game)


def game_share(group: str, line: Dict[str, Any]) -> float:
    """How much of a full game the line is (0..1): downs played over the group's usual snaps a game; a line without
    downs counts as a full game (older capture lines have none)."""
    downs = line.get("downs_played")
    usual = progression.MILEAGE_SNAPS_PER_GAME.get(group)
    if not downs or not usual:
        return 1.0 if downs is None or not usual else 0.0
    return max(0.0, min(1.0, float(downs) / usual))


def form_from_games(games: Sequence[Dict[str, Any]], group: Optional[str], group_mean: float, group_sd: float,
                    downs_per_game: Optional[float] = None) -> float:
    """A player's form (OVR points, -FORM_MAX..+FORM_MAX) from his game lines of the season so far, oldest first
    (each a game_stat_lines row). group_mean / group_sd: the position group's per-game production this season.
    Recent games (RECENT_WEIGHTS over the last FORM_GAMES) against the own norm (season mean shrunk toward the
    group mean), in group sds, times FORM_PER_SD, shrunk by playing time. 0 with no scorable game."""
    if not group or not games:
        return 0.0
    scored = []
    for line in games:
        v = game_production(group, line, downs_per_game)
        if v is not None:
            scored.append((v, game_share(group, line)))
    if not scored:
        return 0.0
    sd = group_sd if group_sd and group_sd > 0 else 1.0
    n = len(scored)
    norm = (sum(v for v, _ in scored) + NORM_PRIOR_GAMES * group_mean) / (n + NORM_PRIOR_GAMES)
    recent = list(reversed(scored[-FORM_GAMES:]))           # newest first
    weights = RECENT_WEIGHTS[:len(recent)]
    wsum = sum(w * s for w, (_, s) in zip(weights, recent))
    if wsum <= 0:
        return 0.0
    recent_value = sum(w * s * v for w, (v, s) in zip(weights, recent)) / wsum
    effective = sum(s for _, s in recent)
    shrink = effective / (effective + SHRINK_GAMES)
    form = FORM_PER_SD * (recent_value - norm) / sd * shrink
    return round(max(-FORM_MAX, min(FORM_MAX, form)), 2)


def group_norms(lines_by_player: Dict[int, List[Dict[str, Any]]], groups: Dict[int, str],
                downs_per_game: Optional[Dict[str, float]] = None) -> Dict[str, tuple]:
    """{group: (mean, sd)} of per-game production over every scorable game line of each group."""
    downs_per_game = downs_per_game or {}
    vals = defaultdict(list)
    for pid, lines in lines_by_player.items():
        group = groups.get(pid)
        if not group:
            continue
        for line in lines:
            v = game_production(group, line, downs_per_game.get(group))
            if v is not None:
                vals[group].append(v)
    out = {}
    for group, xs in vals.items():
        m = sum(xs) / len(xs)
        sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) if len(xs) > 1 else 0.0
        out[group] = (m, sd or 1.0)
    return out


def median_downs(lines_by_player: Dict[int, List[Dict[str, Any]]], groups: Dict[int, str]) -> Dict[str, float]:
    """{group: median downs a game} over game lines with downs, for the per-down groups (OL, FRONT, DB)."""
    dpg = defaultdict(list)
    for pid, lines in lines_by_player.items():
        group = groups.get(pid)
        if group in progression.DOWN_RATE_GROUPS:
            dpg[group] += [float(l["downs_played"]) for l in lines if (l.get("downs_played") or 0) > 0]
    return {g: sorted(v)[len(v) // 2] for g, v in dpg.items() if v}


def forms_from_lines(lines_by_player: Dict[int, List[Dict[str, Any]]], groups: Dict[int, str]) -> Dict[int, float]:
    """{player_id: form} for every player with lines (pure: the whole league's lines of the season so far)."""
    dpg = median_downs(lines_by_player, groups)
    norms = group_norms(lines_by_player, groups, dpg)
    out = {}
    for pid, lines in lines_by_player.items():
        group = groups.get(pid)
        if group not in norms:
            continue
        mean, sd = norms[group]
        out[pid] = form_from_games(sorted(lines, key=lambda l: l.get("week") or 0), group, mean, sd, dpg.get(group))
    return out


# ------------------------------------------------------------- overlay (pure) ----
def _skills(doc: Dict[str, Any], archetype: str, physical: bool) -> List[str]:
    a = doc["archetypes"][archetype]
    skills = a.get("skills") or list(a["terms"])
    banned = set(NEVER_TOUCH) | (set(progression.PHYSICAL_RATINGS) if not physical else set())
    return [k for k in skills if k not in banned]


def overlay(base_ratings: Dict[str, int], position: Optional[str], archetype: Optional[str], form: float,
            doc: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    """{Rating: delta} on top of the base ratings whose OVR effect (ovr_weights.predict_ovr) is round(form), as close
    as the ratings allow: technical and mental skills of the top archetype first, other physical skills only if
    those can't reach it, never Speed, Acceleration or Injury; ratings stay 0..99. {} at form 0 or for a position
    without fitted weights."""
    target = int(round(form or 0))
    if not target or not base_ratings:
        return {}
    doc = doc or ow.load_weights()
    slots = doc.get("slots", {}).get(position) or []
    if not any(a in doc["archetypes"] for a in slots if a):
        return {}
    base_ovr, top = ow.predict_ovr(doc, position, base_ratings, current=archetype)
    want = base_ovr + target
    new = dict(base_ratings)
    for physical in (False, True):
        for _ in range(6):
            ovr, top = ow.predict_ovr(doc, position, new, current=top)
            if ovr == want:
                break
            grade = ow.predict_grade(doc, top, new)
            try:
                new, _ = ow.adjust_ratings(doc, top, new, grade + (want - ovr), skills=_skills(doc, top, physical))
            except ValueError:
                break
        if ow.predict_ovr(doc, position, new, current=top)[0] == want:
            break
    out = {k: int(new[k]) - int(base_ratings[k]) for k in new if k in base_ratings and new[k] != base_ratings[k]}
    best = ow.predict_ovr(doc, position, new)[0]
    if abs(best - want) > abs(base_ovr - want):          # never worse than no overlay
        return {}
    return out


def apply_overlay(base: Dict[str, int], deltas: Dict[str, int]) -> Dict[str, int]:
    """Base ratings plus an overlay, clamped to 0..99 (as pipeline/drift_check.expected_ratings)."""
    out = dict(base or {})
    for k, d in (deltas or {}).items():
        if k in out and isinstance(out[k], (int, float)):
            out[k] = max(psave.RATING_MIN, min(psave.RATING_MAX, int(out[k]) + int(d)))
    return out


# ----------------------------------------------------------- edits (pure) ----
def build_form_document(snapshot: Dict[str, Any], base_by_player: Dict[int, Dict[str, int]],
                        forms: Dict[int, float], pgid_by_player: Dict[int, int],
                        doc: Optional[Dict[str, Any]] = None, morale: int = NEUTRAL_MORALE,
                        warnings: Optional[List[str]] = None) -> tuple:
    """(edits document, player_form rows). For every snapshot player Project Mu has base ratings for: ratings set to
    base + overlay(form) (rating_edits' dual-write: OVR, grades, PlayerType recomputed when a rating changes), and
    one row (player_id, form, overlay); ConfidenceRating `morale` for every player whose value differs."""
    doc = doc or ow.load_weights()
    players = {p["pgid"]: p for p in snapshot.get("players") or [] if p.get("pgid") is not None}
    rows, form_rows = [], []
    missing = 0
    for pid, pgid in pgid_by_player.items():
        p = players.get(pgid)
        if p is None:
            continue
        base = base_by_player.get(pid)
        if not base:
            missing += 1
            continue
        base = {k: int(v) for k, v in base.items() if isinstance(v, numbers.Number) and not isinstance(v, bool)}
        form = float(forms.get(pid, 0.0) or 0.0)
        deltas = overlay(base, p.get("position"), p.get("archetype"), form, doc)
        want = apply_overlay(base, deltas)
        current = p.get("ratings") or {}
        diff = {k: want[k] - int(current[k]) for k in want if k in current and want[k] != int(current[k])}
        if diff:
            rows.append({"player_id": pid, "pgid": pgid, "ratings_diff": diff})
        form_rows.append({"player_id": pid, "form": round(form, 2), "overlay": deltas})
    if missing:
        psave._warn(warnings, f"{missing} players in the save have no player_attributes ratings; ratings not written")
    players_edits = psave.merge_players(psave.rating_edits(rows, snapshot, pgid_by_player, doc, warnings),
                                        psave.morale_edits(snapshot, morale))
    return {"players": players_edits, "records": []}, form_rows


# ---------------------------------------------------------------------- DB ----
GAME_LINES_SQL = """
    SELECT l.*, ps.position
    FROM game_stat_lines l
    JOIN games g ON g.id = l.game_id
    LEFT JOIN player_seasons ps ON ps.player_id = l.player_id AND ps.season = l.season
    WHERE l.season = %s AND l.week BETWEEN %s AND %s AND COALESCE(g.game_type, 'REGULAR_SEASON') <> 'PRESEASON'
    ORDER BY l.player_id, l.week
"""


def fetch_game_lines(cur, season: int, week: int) -> tuple:
    """({player_id: [game_stat_lines row, ...] oldest first}, {player_id: position group}) for weeks 1..week."""
    cur.execute(GAME_LINES_SQL, (season, FIRST_WEEK, week))
    cols = [d[0] for d in cur.description]
    lines, groups = defaultdict(list), {}
    for r in cur.fetchall():
        row = dict(zip(cols, r))
        pid = row.get("player_id")
        if pid is None:
            continue
        lines[pid].append(row)
        group = progression._group_of(row.get("position"))
        if group:
            groups[pid] = group
    return dict(lines), groups


def weekly_form(cur, season: int, week: int) -> Dict[int, float]:
    """{player_id: form} in OVR points (-3..+3) from the season's game lines up to `week`; {} at week <= 0 and in
    the offseason (form resets every offseason)."""
    if not in_season(week):
        return {}
    lines, groups = fetch_game_lines(cur, season, week)
    return forms_from_lines(lines, groups)


def store_form(cur, season: int, week: int, form_rows: Iterable[Dict[str, Any]]) -> int:
    """Replaces the week's player_form rows. Returns the number stored."""
    rows = [(r["player_id"], season, week, max(-FORM_MAX, min(FORM_MAX, round(float(r["form"]), 2))),
             json.dumps(r.get("overlay") or {}, sort_keys=True)) for r in form_rows]
    cur.execute("DELETE FROM player_form WHERE season = %s AND week = %s", (season, week))
    if rows:
        cur.executemany("INSERT INTO player_form (player_id, season, week, form, overlay) "
                        "VALUES (%s, %s, %s, %s, %s::jsonb)", rows)
    return len(rows)


def form_edits(cur, snapshot: Dict[str, Any], season: int, week: int, store: bool = True,
               pgid_by_player: Optional[Dict[int, int]] = None, doc: Optional[Dict[str, Any]] = None,
               warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    """The weekly edits document: every player's base ratings (newest player_attributes up to `season`) plus this
    week's form overlay, OVR / grades / PlayerType recomputed, ConfidenceRating 50 for all (D13). With store=True the
    week's player_form rows are replaced (the caller commits)."""
    if pgid_by_player is None:
        pgid_by_player = psave.fetch_pgid_map(cur)
    base = progression.fetch_player_attributes_map(cur, season)
    forms = weekly_form(cur, season, week)
    document, form_rows = build_form_document(snapshot, base, forms, pgid_by_player, doc, warnings=warnings)
    if store:
        store_form(cur, season, week, form_rows)
    return document


# --------------------------------------------------------------------- CLI ----
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build the weekly form save_writer edits document and store player_form "
                                             "(never writes a save; save_writer.mjs does).")
    ap.add_argument("--snapshot", required=True, help="reader-v4 snapshot JSON (pc_save_reader.mjs)")
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--out", help="edits JSON path (default: stdout)")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", progression.DSN))
    ap.add_argument("--dry-run", action="store_true", help="don't store player_form")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    with open(a.snapshot) as fh:
        snapshot = json.load(fh)
    rv = (snapshot.get("meta") or {}).get("reader_version")
    if rv is not None and rv < 4:
        log.error(f"snapshot reader_version {rv}: needs 4 (confidence)")
        return 2
    import psycopg
    conn = psycopg.connect(a.dsn)
    warnings: List[str] = []
    try:
        with conn.cursor() as cur:
            doc = form_edits(cur, snapshot, a.season, a.week, store=not a.dry_run, warnings=warnings)
        if a.dry_run:
            conn.rollback()
        else:
            conn.commit()
    finally:
        conn.close()
    text = json.dumps(doc, indent=1)
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(text + "\n")
    else:
        print(text)
    fields = sum(len(e["set"]) for e in doc["players"])
    log.info(f"{len(doc['players'])} players ({fields} fields), {len(warnings)} warnings"
             + (" (dry run: player_form not stored)" if a.dry_run else "") + (f" -> {a.out}" if a.out else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
