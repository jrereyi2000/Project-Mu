"""Compensatory draft pick producer (COMPENSATORY_PICKS_PLAN follow-up; PROJECT_SPEC §2.17g / §3.3c).

The engine (`engines.compensatory_picks_engine`) is pure and the schema (`2027_02_06_compensatory_draft_picks.sql`)
has the ledgers, but nothing filled them. This module is the producer: it derives the season's Qualifying Free
Agents (QFAs) from data the league already stores, runs the CBA Article 6 formula (and, when explicitly enabled,
Resolution JC-2A), and writes `qualifying_free_agents`, `compensatory_pick_awards` and the class's COMP/JC2A
`draft_picks` rows.

  python3 -m engines.compensatory_picks_run --season S [--draft-season DS] [--deadline YYYY-MM-DD]
                                            [--enable-jc2a] [--dry-run] [--dsn DSN]

`season` is the league season the picks were earned in (the just-played season); `draft_season` is the class that
receives them (default `season + 1`). Resolution JC-2A is OFF by default (JR, 2026-11-08): nothing reads
`staff_development_departures` unless `--enable-jc2a` / `jc2a_enabled=True`, so the default path is purely CBA
Article 6. With the flag on, the departures feed the engine (via `coaching_carousel.eligible_departures`) and it
produces the two Round-3 JC-2A picks per eligible departure, one in each of the two subsequent drafts.

Data the producer reads (all read-only):
  * QFA signings: `contract_offers` status='ACCEPTED' AND kind='FREE_AGENT' for `season`; the signing team is the new
    team, the player's `player_seasons` team for `season` is the old team, and APY = total_value / years.
  * A player released/waived during the season (`transactions` type='RELEASED', or `player_seasons.released_week`
    set) is not a QFA; the engine's `qfa_eligible` enforces it and the ledger row records qualifies=false.
  * Honors: `awards` for the season. The databases inspected 2026-11-08 hold `'First Team All-Pro'` and `'Second
    Team All-Pro'` (plus the major awards) and no `'Pro Bowl'` rows; `'Pro Bowl'` is recognised if the awards feed
    adds it. Only first-team All-Pro (+20) and Pro Bowl (+10) score, so second-team is ignored and a season with
    neither maps to 0.
  * Snap share: `season_stat_general.downs_played` over the largest `downs_played` on the player's team and side
    of the ball that season (`snap_shares`; docs/reference/MADDEN_STATS_AND_ADVANCED_METRICS.md §4.5). K/P/LS have
    no side and score 0. A player with no season stat row also scores 0.
  * JC-2A (opt-in): `staff_development_departures` through `coaching_carousel.eligible_departures`, which guards the
    table with `to_regclass`; an absent table means no departures (never an error).

Idempotency. A run owns one draft class: it replaces that class's COMP/JC2A `draft_picks` rows and its
`compensatory_pick_awards` rows, and the season's QFA ledger. It never deletes or rewrites SEED/TRADE/JR rows.
`draft_picks.overall_pick` is computed from the per-round comp counts exactly as `contracts_engine.
round_for_overall_pick` and the Draft API number them: each comp sits after its round's 32 standard picks and later
rounds shift (four Round-3 comps make Round 4 start at 101). The live class's standard rows may still have
`overall_pick` NULL until the draft-room lock order runs; the comp rows are written with the computed number anyway
(the same assumption `server/draft_api.py` makes when it builds the class), so the lock order must use the same
formula. `pick_suffix` is the comp's within-round index, zero-padded ('01', '02', ...), unique per
(draft_season, round, original_team_id).

NOTES / graceful degradations:
  * `qualifying_free_agents.canceled_by_qfa_id` is written NULL: the engine's `cancel_qfas` returns only survivors,
    not the cancelled pairs, so the producer cannot name the canceller without duplicating engine logic.
  * With no `player_contracts` APYs for the season, ranking falls back to the season's own QFA APYs.
  * The old team is `player_seasons` for `season` (the season the QFAs departed). The brief phrased it as
    `season - 1` when `season` is read as the draft year; in the live model the offseason runs with
    `league_state.season = S` and `contract_offers.season = S`, so the played season is `season`.
  * JC-2A year two: a run for class `draft_season` takes year-one awards from `season`'s departures and year-two
    awards from `season - 1`'s departures, so each class keeps its full JC-2A entitlement across consecutive runs.
    `result["jc2a_picks"]` exposes every award this season's departures generated (one in each of the two drafts).
"""
import argparse
import datetime
import os
import sys
from collections import defaultdict

from engines import compensatory_picks_engine as E
from engines import contracts_engine as ce

DEFAULT_DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")

# The live `awards` table's All-Pro / Pro Bowl award_type values (inspected 2026-11-08). Second Team All-Pro does
# not score in the engine's honor bonus, so it is deliberately not mapped.
ALL_PRO_FIRST_TYPES = frozenset({"First Team All-Pro"})
PRO_BOWL_TYPES = frozenset({"Pro Bowl"})

# A signing's old team cannot come from player_seasons(season).team_id alone: at FREE_AGENCY the save loader nulls
# it for every expiring-contract free agent (and the FA engines require that null to offer him at all), so the
# ledger was always empty. Fall back to his last recorded team that season, then to his contract row.
QFA_SIGNINGS_SQL = """
    SELECT o.id, o.player_id, o.team_id, o.total_value, o.years, o.created_at,
           COALESCE(
               (SELECT ps.team_id FROM player_seasons ps
                WHERE ps.player_id = o.player_id AND ps.season = o.season AND ps.team_id IS NOT NULL
                ORDER BY ps.id DESC LIMIT 1),
               (SELECT g.team_id FROM season_stat_general g
                WHERE g.player_id = o.player_id AND g.season = o.season AND g.team_id IS NOT NULL
                ORDER BY g.team_id LIMIT 1),
               (SELECT c.team_id FROM player_contracts c
                WHERE c.player_id = o.player_id AND c.season = o.season AND c.team_id IS NOT NULL
                ORDER BY c.id DESC LIMIT 1)
           ) AS old_team,
           (SELECT ps2.released_week FROM player_seasons ps2
            WHERE ps2.player_id = o.player_id AND ps2.season = o.season
            ORDER BY ps2.id DESC LIMIT 1) AS released_week
    FROM contract_offers o
    WHERE o.season = %s AND o.status = 'ACCEPTED' AND o.kind = 'FREE_AGENT'
    ORDER BY o.player_id, o.created_at, o.id
"""
RELEASED_SQL = """
    SELECT DISTINCT player_id FROM transactions
    WHERE season = %s AND type = 'RELEASED' AND player_id IS NOT NULL
"""
HONORS_SQL = """
    SELECT player_id, award_type FROM awards
    WHERE season = %s AND player_id IS NOT NULL
"""
DELETE_QFA_SQL = "DELETE FROM qualifying_free_agents WHERE season = %s"
DELETE_AWARDS_SQL = "DELETE FROM compensatory_pick_awards WHERE draft_season = %s"
DELETE_PICKS_SQL = "DELETE FROM draft_picks WHERE draft_season = %s AND source IN ('COMP', 'JC2A')"
INSERT_PICK_SQL = """
    INSERT INTO draft_picks (draft_season, round, original_team_id, owner_team_id, overall_pick, source,
                             pick_suffix)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
"""


# ------------------------------------------------------------------ small date helpers ----
def _as_datetime(value, season):
    """A stored signing timestamp as a timezone-aware datetime (contract_offers.created_at)."""
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, datetime.date):
        return datetime.datetime(value.year, value.month, value.day, tzinfo=datetime.timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.datetime(season, 1, 1, tzinfo=datetime.timezone.utc)


def _as_date(value, season):
    """The signing date the engine's deadline check compares (a date)."""
    return _as_datetime(value, season).date()


# ------------------------------------------------------------------ QFA derivation ----
# Snap share (docs/reference/MADDEN_STATS_AND_ADVANCED_METRICS.md §4.5: Snap Share = DOWNSPLAYED / total team
# snaps). The save loader writes every player's season `DOWNSPLAYED` to `season_stat_general.downs_played`
# (pipeline/load_save.py), so the share is computable: a player's downs over his unit's snaps that season. The
# unit's snap count is not stored as a team total, so it is taken as the largest `downs_played` on that side of
# the ball for the team (a full-time starter plays essentially every snap); K/P/LS have no side and score 0.
OFFENSE_POS = {"QB", "HB", "RB", "FB", "WR", "TE", "LT", "LG", "C", "RG", "RT", "T", "G", "OT", "OG", "OL"}
DEFENSE_POS = {"LEDG", "REDG", "LE", "RE", "DE", "EDGE", "DT", "NT", "DL", "LOLB", "ROLB", "MLB", "MIKE", "WILL",
               "SAM", "LB", "OLB", "ILB", "CB", "DB", "FS", "SS", "S"}


def side_of(position):
    """'OFF' / 'DEF' / None for a position (K, P and LS have no snap share to speak of)."""
    pos = str(position or "").upper()
    if pos in OFFENSE_POS:
        return "OFF"
    if pos in DEFENSE_POS:
        return "DEF"
    return None


def snap_shares(cur, season):
    """{player_id: snap share 0..1} for the season, from `season_stat_general.downs_played`.

    Each player's share is his downs over the largest `downs_played` on his team and side of the ball that season.
    A player with no season row, no side (K/P/LS) or a team with no other downs gets 0.0. Position comes from the
    stat row, falling back to `player_seasons.position`."""
    cur.execute("""SELECT g.player_id, COALESCE(g.position, ps.position), COALESCE(g.team_id, ps.team_id),
                          g.downs_played
                   FROM season_stat_general g
                   LEFT JOIN player_seasons ps ON ps.player_id = g.player_id AND ps.season = g.season
                   WHERE g.season = %s""", (season,))
    rows = cur.fetchall()
    unit_max = {}
    for _pid, position, team_id, downs in rows:
        side = side_of(position)
        if side is None or team_id is None or not downs:
            continue
        key = (team_id, side)
        unit_max[key] = max(unit_max.get(key, 0), int(downs))
    shares = {}
    for pid, position, team_id, downs in rows:
        side = side_of(position)
        top = unit_max.get((team_id, side), 0) if side is not None else 0
        shares[pid] = round(min(1.0, int(downs or 0) / top), 3) if top and downs else 0.0
    return shares


def snap_pct_for(cur, player_id, season):
    """One player's snap share (fraction 0..1) for the season; see `snap_shares`. The engine scores +5 at 50% and
    +10 at 75%."""
    return snap_shares(cur, season).get(player_id, 0.0)


def _honors(cur, season):
    """(first_team_all_pro_ids, pro_bowl_ids) from the season's awards rows."""
    cur.execute(HONORS_SQL, (season,))
    all_pro, pro_bowl = set(), set()
    for player_id, award_type in cur.fetchall():
        if player_id is None:
            continue
        kind = (award_type or "").strip()
        if kind in ALL_PRO_FIRST_TYPES:
            all_pro.add(player_id)
        elif kind in PRO_BOWL_TYPES:
            pro_bowl.add(player_id)
    return all_pro, pro_bowl


def build_qfas(cur, season, deadline=None):
    """The season's QFAs as engine-shaped dicts (see engines/compensatory_picks_engine.py's module docstring).

    One dict per accepted FREE_AGENT signing with an old team. `team_id` defaults to the new team; a caller
    deriving the lost/gained sides must override it (lost = old_team_id, gained = new_team_id). `signed_date` is a
    date for the engine's deadline check; `signed_at` is the original timestamp for the ledger. Cut/waived players
    carry waived/cut=True and contract_expired=False, which the engine rejects."""
    cur.execute(RELEASED_SQL, (season,))
    released = {r[0] for r in cur.fetchall() if r[0] is not None}
    all_pro, pro_bowl = _honors(cur, season)
    shares = snap_shares(cur, season)
    cur.execute(QFA_SIGNINGS_SQL, (season,))
    by_player = {}
    for _oid, player_id, new_team, total_value, years, created_at, old_team, released_week in cur.fetchall():
        if player_id is None or new_team is None or old_team is None:
            continue                       # no old team: the league cannot attribute a lost QFA
        if old_team == new_team:
            continue                       # a re-signing is not a lost/gained QFA move
        years = max(1, int(years or 1))
        total_value = int(total_value or 0)
        apy = int(round(total_value / years))
        cut = bool(player_id in released or released_week is not None)
        by_player[player_id] = {
            "player_id": player_id,
            "team_id": new_team,                  # overridden per side by award_and_persist
            "old_team_id": old_team,
            "new_team_id": new_team,
            "apy": apy,
            "total_value": total_value,
            "years": years,
            "snap_pct": shares.get(player_id, 0.0),
            "all_pro_first": player_id in all_pro,
            "pro_bowl": player_id in pro_bowl,
            "honors_points": E.honor_points(player_id in all_pro, player_id in pro_bowl),
            "waived": cut,
            "cut": cut,
            "is_unrestricted": True,
            "contract_expired": not cut,
            "signed_date": _as_date(created_at, season),
            "signed_at": _as_datetime(created_at, season),
        }
    return list(by_player.values())


# ------------------------------------------------------------------ JC-2A (opt-in) ----
def load_departures(cur, season):
    """The season's reviewed JC-2A hire-aways in the engine's departure shape, or [] when the journal table is
    absent (the `coaching_carousel.eligible_departures` guard: never an error, never invented demographics)."""
    from engines import coaching_carousel as cc
    return cc.eligible_departures(cur, season)


def _attach_person_names(awards, departures):
    """Carry the departing person's name into each JC-2A award's `source_person_name` (for the reason text)."""
    names = {}
    for d in departures:
        key = (d.get("team_id"), d.get("hire_from_role"), d.get("hire_to_role"))
        if d.get("person_name") and key not in names:
            names[key] = d["person_name"]
    for a in awards:
        a["source_person_name"] = names.get((a.get("team_id"), a.get("from_role"), a.get("to_role")))
    return awards


# ------------------------------------------------------------------ slot allocation ----
def build_rows(cba_picks, jc2a_picks, draft_season):
    """(award_rows, pick_rows, comp_schedule) for one draft class.

    CBA picks come first in each round, then JC-2A (plan §2.2); `slot_in_round` is 33, 34, ... and `overall_pick`
    follows `contracts_engine.round_for_overall_pick`'s per-round comp-count boundaries. `pick_suffix` is the
    within-round comp index, zero-padded."""
    by_round = defaultdict(list)
    for q in cba_picks:
        by_round[int(q["round_tier"])].append(("CBA", q))
    for a in jc2a_picks:
        by_round[int(a["round"])].append(("JC2A", a))

    comp_schedule = [0] * 7
    for rnd, items in by_round.items():
        comp_schedule[rnd - 1] = len(items)

    award_rows, pick_rows = [], []
    for rnd in sorted(by_round):
        comps_before = sum(comp_schedule[:rnd - 1])
        for index0, (kind, item) in enumerate(by_round[rnd]):
            slot = 32 + index0 + 1
            overall = 32 * rnd + comps_before + index0 + 1
            got = ce.round_for_overall_pick(overall, comp_schedule)
            if got != rnd:
                raise ValueError(f"comp slot round mismatch: round {rnd} slot {slot} is overall {overall}, "
                                 f"which round_for_overall_pick maps to round {got}")
            team_id = int(item["team_id"])
            if kind == "CBA":
                reason = (f"CBA Article 6 formula: lost qualifying free agent player {item.get('player_id')} "
                          f"(APY tier round {rnd}, score {item.get('score')})")
                source_player_id, source_coach_id = item.get("player_id"), None
            else:
                name = item.get("source_person_name")
                who = f" {name}" if name else ""
                reason = (f"Resolution JC-2A: {item.get('from_role')} hired away as {item.get('to_role')}{who}")
                source_player_id, source_coach_id = None, None
            award_rows.append({
                "draft_season": draft_season, "team_id": team_id, "round": rnd,
                "slot_in_round": slot, "overall_pick": overall, "award_kind": kind,
                "source_player_id": source_player_id, "source_coach_id": source_coach_id, "reason": reason,
            })
            pick_rows.append({
                "draft_season": draft_season, "round": rnd, "original_team_id": team_id,
                "owner_team_id": team_id, "overall_pick": overall,
                "source": "COMP" if kind == "CBA" else "JC2A", "pick_suffix": f"{index0 + 1:02d}",
            })
    return award_rows, pick_rows, comp_schedule


def _qfa_rows(qfas, league_apys, deadline, season):
    """The season's qualifying_free_agents ledger rows (one per signing)."""
    rows = []
    for q in qfas:
        eligible = E.qfa_eligible(q, deadline)
        evaluated = E.evaluate_qfa(q, league_apys)
        qualifies = bool(eligible and evaluated["qualifies"])
        rows.append({
            "season": season,
            "player_id": q["player_id"],
            "old_team_id": q["old_team_id"],
            "new_team_id": q["new_team_id"],
            "signed_date": q["signed_at"],
            "total_value": q["total_value"],
            "years": q["years"],
            "apy": q["apy"],
            "snap_pct": q["snap_pct"],
            "honors_points": q["honors_points"],
            "round_tier": evaluated["round_tier"] if qualifies else None,
            "qualifies": qualifies,
            "canceled_by_qfa_id": None,
        })
    return rows


# ------------------------------------------------------------------ the producer ----
def award_and_persist(cur, season, draft_season, league_apys=None, deadline=None, dry_run=False,
                      jc2a_enabled=False):
    """Calculate the season's comp picks and write the ledgers and `draft_season`'s COMP/JC2A draft rows.

    `league_apys`: every league contract APY for percentile ranking (default: `player_contracts`, else the season's
    own QFA APYs). `deadline`: post-draft signing deadline (a date) for the QFA check. `dry_run`: compute and
    return, write nothing. `jc2a_enabled`: award Resolution JC-2A (default off; when off the departures table is
    never read). Returns {season, draft_season, qfas, awards, draft_picks, comp_schedule, cba_picks, jc2a_picks,
    dry_run}."""
    season, draft_season = int(season), int(draft_season)
    qfas = build_qfas(cur, season, deadline=deadline)

    apys = list(league_apys) if league_apys is not None else E.load_league_apys(cur, season)
    if not apys:
        apys = [q["apy"] for q in qfas]          # degrade: rank the season's QFAs among themselves

    lost = [dict(q, team_id=q["old_team_id"]) for q in qfas]
    gained = [dict(q, team_id=q["new_team_id"]) for q in qfas]

    departures, prev_departures = [], []
    if jc2a_enabled:
        departures = load_departures(cur, season)          # year one: drafts season+1 / season+2
        prev_departures = load_departures(cur, season - 1)  # year two for this class

    result = E.calculate_comp_picks(lost, gained, apys, post_draft_deadline=deadline,
                                    jc2a_departures=departures if jc2a_enabled else (),
                                    draft_season=season, jc2a_enabled=jc2a_enabled)
    jc2a_all = _attach_person_names(list(result["jc2a_picks"]), departures)
    jc2a_for_class = [a for a in jc2a_all if int(a["draft_season"]) == draft_season]
    if jc2a_enabled and prev_departures:
        prev_awards = _attach_person_names(E.jc2a_awards(prev_departures, season - 1), prev_departures)
        jc2a_all += prev_awards
        jc2a_for_class += [a for a in prev_awards if int(a["draft_season"]) == draft_season]

    award_rows, pick_rows, comp_schedule = build_rows(result["cba_picks"], jc2a_for_class, draft_season)
    qfa_rows = _qfa_rows(qfas, apys, deadline, season)

    if not dry_run:
        cur.execute(DELETE_QFA_SQL, (season,))
        cur.execute(DELETE_AWARDS_SQL, (draft_season,))
        cur.execute(DELETE_PICKS_SQL, (draft_season,))
        E.save_qfas(cur, qfa_rows)
        E.save_awards(cur, award_rows)
        for row in pick_rows:
            cur.execute(INSERT_PICK_SQL, (row["draft_season"], row["round"], row["original_team_id"],
                                          row["owner_team_id"], row["overall_pick"], row["source"],
                                          row["pick_suffix"]))

    return {
        "season": season,
        "draft_season": draft_season,
        "qfas": qfa_rows,
        "awards": award_rows,
        "draft_picks": pick_rows,
        "comp_schedule": comp_schedule,
        "cba_picks": list(result["cba_picks"]),
        "jc2a_picks": jc2a_all,
        "dry_run": bool(dry_run),
    }


# ------------------------------------------------------------------ CLI ----
def _team_names(cur):
    cur.execute("SELECT id, abbreviation FROM teams ORDER BY id")
    return {row[0]: row[1] for row in cur.fetchall()}


def print_summary(result, team_names=None):
    """A per-team summary of one producer run (stdout)."""
    names = team_names or {}
    cba = [a for a in result["awards"] if a["award_kind"] == "CBA"]
    jc2a = [a for a in result["awards"] if a["award_kind"] == "JC2A"]
    print(f"{result['draft_season']} draft (earned in {result['season']}): "
          f"{len(result['awards'])} comp pick(s) [{len(cba)} CBA, {len(jc2a)} JC-2A], "
          f"{len(result['qfas'])} QFA signing(s)")
    for a in sorted(result["awards"], key=lambda x: (x["team_id"], x["round"], x["overall_pick"])):
        label = names.get(a["team_id"], f"team {a['team_id']}")
        print(f"  {label:<4} R{a['round']} P{a['slot_in_round']} (No. {a['overall_pick']})  "
              f"{a['award_kind']:<4}  {a['reason']}")
    if result["jc2a_picks"]:
        print(f"  JC-2A engine picks this season: "
              f"{[(a['draft_season'], a['team_id']) for a in result['jc2a_picks']]}")
    if result["dry_run"]:
        print("dry run: nothing written")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Derive QFAs, calculate compensatory picks, and write the ledgers and "
                                             "the draft class's COMP/JC2A draft_picks rows.")
    ap.add_argument("--season", type=int, required=True,
                    help="league season the picks were earned in (the just-played season)")
    ap.add_argument("--draft-season", type=int, help="class that receives the picks (default: season + 1)")
    ap.add_argument("--deadline", help="post-draft signing deadline, YYYY-MM-DD")
    ap.add_argument("--enable-jc2a", action="store_true",
                    help="award Resolution JC-2A picks (off by default; reads staff_development_departures)")
    ap.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    args = ap.parse_args(argv)

    deadline = None
    if args.deadline:
        try:
            deadline = datetime.date.fromisoformat(args.deadline)
        except ValueError:
            ap.error(f"--deadline must be YYYY-MM-DD, not {args.deadline!r}")
    draft_season = args.draft_season if args.draft_season is not None else args.season + 1

    import psycopg
    with psycopg.connect(args.dsn) as conn:
        cur = conn.cursor()
        result = award_and_persist(cur, args.season, draft_season, deadline=deadline, dry_run=args.dry_run,
                                   jc2a_enabled=args.enable_jc2a)
        print_summary(result, _team_names(cur))
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
