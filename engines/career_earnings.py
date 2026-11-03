"""
Career-earnings ledger (docs/done/CAP_RELATIVE_CONTRACTS_PLAN.md §2.4; PROJECT_SPEC §2.17a).

All money is in cap shares: each season's pay (APY = total_value / length) / that season's league cap
(engines/cap_history.cap_in). Derived from player_contracts history; no table of its own. Earnings before a player's
earliest known deal are unknown, so a share is a lower bound unless the history starts with a rookie deal.

  deal_spans(rows)                     player_contracts rows -> deals with the seasons they paid
  earned_shares(spans, caps, through)  {player_id: {"share", "first_known", "complete"}}
  is_secure(entry)                     True / False / None (unknown: the ring-chaser gate stays inactive)
  read_contract_rows(cur, through)     read-only SELECT

Nothing in an engine calls this module yet.

  python3 -m engines.career_earnings --report [--season S] [--top N]
"""
import argparse
import os

from engines.cap_history import cap_in, league_caps

FINANCIAL_SECURITY_SHARE = 0.15   # D3, a trial value: about $38M at the 2024 cap, $55M at the 2036 cap


def _signed(row):
    """The signing season of a contract row, or None for a row that can't be placed."""
    length, rem, total = row.get("length"), row.get("years_remaining"), row.get("total_value")
    if not length or not total or rem is None or length <= 0 or total <= 0 or length < rem:
        return None
    return row["season"] - (length - rem)


def deal_spans(contract_rows):
    """contract_rows: [{player_id, team_id, season, length, years_remaining, total_value, status, yoe}], one per
    player_contracts row (yoe = years_of_experience in that scan season). Rows are grouped into deals keyed
    (player_id, team_id, signed, length), signed = season - (length - years_remaining). total_value is left out of
    the key (plan R4): a restructure or a re-read can change it between scans of one deal, so the deal's value is
    the latest scan's. Each deal -> {player_id, signed, first_season, last_season, apy, rookie_start}:
    first_season = signed; last_season = signed + length - 1, cut to the season before the player's next deal
    (a later signing season) starts and to the earliest scan season of a RELEASED row; rookie_start = the yoe at
    signing (yoe - (season - signed)) is 0. Rows with no length or value, or length < years_remaining, are skipped."""
    deals = {}
    for r in contract_rows:
        signed = _signed(r)
        if signed is None:
            continue
        key = (r["player_id"], r.get("team_id"), signed, r["length"])
        deals.setdefault(key, []).append(r)

    starts = {}
    for pid, _team, signed, _len in deals:
        starts.setdefault(pid, set()).add(signed)

    out = []
    for (pid, _team, signed, length), rows in deals.items():
        latest = max(rows, key=lambda r: r["season"])
        last = signed + length - 1
        later = [s for s in starts[pid] if s > signed]
        if later:
            last = min(last, min(later) - 1)
        released = [r["season"] for r in rows if r.get("status") == "RELEASED"]
        if released:
            last = min(last, min(released))
        if last < signed:
            continue
        known = [r for r in rows if r.get("yoe") is not None]
        rookie = False
        if known:
            first = min(known, key=lambda r: r["season"])
            rookie = first["yoe"] - (first["season"] - signed) == 0
        out.append({"player_id": pid, "signed": signed, "first_season": signed, "last_season": last,
                    "apy": latest["total_value"] / length, "rookie_start": rookie})
    out.sort(key=lambda d: (d["player_id"], d["signed"], d["last_season"]))
    return out


def earned_shares(spans, caps, through_season):
    """{player_id: {"share": sum over covered seasons <= through_season of apy / cap_in(s, caps), "first_known": min
    first_season, "complete": True when the earliest deal is rookie_start}}. Each season counts once per player; if
    two deals cover a season, the later-signed deal wins (then the longer-running one, then the higher APY). Deals
    that start after through_season are ignored; a player with none left is not in the result."""
    by_player = {}
    for d in spans:
        if d["first_season"] <= through_season:
            by_player.setdefault(d["player_id"], []).append(d)
    out = {}
    for pid, deals in by_player.items():
        pay = {}
        for d in sorted(deals, key=lambda d: (d["signed"], d["last_season"], d["apy"])):
            for s in range(d["first_season"], min(d["last_season"], through_season) + 1):
                pay[s] = d["apy"]                 # later in the sort order overwrites: the later-signed deal wins
        first = min(d["first_season"] for d in deals)
        out[pid] = {"share": sum(apy / cap_in(s, caps) for s, apy in pay.items()),
                    "first_known": first,
                    "complete": any(d["rookie_start"] for d in deals if d["first_season"] == first)}
    return out


def is_secure(entry, threshold=FINANCIAL_SECURITY_SHARE):
    """True if share >= threshold; False if share < threshold and the history is complete; otherwise None
    (unknown). None keeps the ring-chaser gate inactive (unknown means inactive)."""
    if not entry:
        return None
    if entry["share"] >= threshold:
        return True
    return False if entry.get("complete") else None


def read_contract_rows(cur, through_season):
    """Every player_contracts row with season <= through_season (read-only), with the player's years_of_experience in
    that scan season (None when he has no player_seasons row)."""
    cur.execute("""SELECT c.player_id, c.team_id, c.season, c.length, c.years_remaining, c.total_value, c.status,
                          ps.years_of_experience
                   FROM player_contracts c
                   LEFT JOIN player_seasons ps ON ps.player_id = c.player_id AND ps.season = c.season
                   WHERE c.season <= %s
                   ORDER BY c.player_id, c.season, c.id""", (through_season,))
    cols = ("player_id", "team_id", "season", "length", "years_remaining", "total_value", "status", "yoe")
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def report(cur, season, top=10):
    caps = league_caps(cur)
    spans = deal_spans(read_contract_rows(cur, season))
    ledger = earned_shares(spans, caps, season)
    verdicts = [is_secure(e) for e in ledger.values()]
    cap = cap_in(season, caps)
    print(f"Career earnings through {season} (cap {season} ${cap / 1e6:.1f}M; known caps: "
          f"{sorted(caps) or 'none'}; threshold {FINANCIAL_SECURITY_SHARE:.0%} of a cap "
          f"= ${FINANCIAL_SECURITY_SHARE * cap / 1e6:.1f}M)")
    print(f"  players {len(ledger)}  deals {len(spans)}")
    print(f"  secure {verdicts.count(True)}  not secure {verdicts.count(False)}  unknown {verdicts.count(None)}")
    print(f"  complete history (earliest deal a rookie deal) {sum(1 for e in ledger.values() if e['complete'])}")
    starts = {}
    for d in spans:
        starts[(d["player_id"], d["signed"])] = starts.get((d["player_id"], d["signed"]), 0) + 1
    print(f"  players with more than one deal starting in a season {len({p for (p, _), n in starts.items() if n > 1})}")
    best = sorted(ledger.items(), key=lambda kv: -kv[1]["share"])[:top]
    names = {}
    if best:
        cur.execute("SELECT id, COALESCE(full_name, player_name) FROM players WHERE id = ANY(%s)",
                    ([pid for pid, _ in best],))
        names = dict(cur.fetchall())
    print(f"  top {len(best)} by share:")
    for pid, e in best:
        print(f"    {names.get(pid, pid)!s:<28} {e['share']:6.3f} caps (${e['share'] * cap / 1e6:6.1f}M at {season})"
              f"  from {e['first_known']}  {'complete' if e['complete'] else 'partial'}  {is_secure(e)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--season", type=int)
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args(argv)
    import psycopg
    dsn = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
    with psycopg.connect(dsn) as conn:
        conn.read_only = True                  # the report only SELECTs
        cur = conn.cursor()
        cur.execute("SELECT current_season FROM league_state WHERE id = 1")
        season = a.season or cur.fetchone()[0]
        report(cur, season, a.top)
        conn.rollback()


if __name__ == "__main__":
    main()
