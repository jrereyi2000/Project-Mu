"""
League cap history (docs/done/CAP_RELATIVE_CONTRACTS_PLAN.md §2.1; PROJECT_SPEC §2.17).

  league_caps(cur)        {season: median team `cap`} from team_cap_seasons, every season (read-only).
  cap_in(season, caps)    the league cap in a season: the known value, else the nearest known season's cap moved by
                          the CBA growth mean a year (divide going back, multiply going forward), rounded to
                          CAP_ROUND; with no known caps, REFERENCE_CAP. Deterministic: no seeded draw, unlike
                          contracts_engine.next_cap, so a refit always gives the same answer.
"""
from engines.contracts_engine import CBA


def league_caps(cur):
    """{season: median team cap} over team_cap_seasons' raw `cap` column (not adjusted_cap), all seasons. The
    median of an even count is the upper middle value, as transactions_engine.league_cap takes it."""
    cur.execute("SELECT season, cap FROM team_cap_seasons WHERE cap IS NOT NULL AND season IS NOT NULL")
    by = {}
    for season, cap in cur.fetchall():
        if cap:
            by.setdefault(int(season), []).append(int(cap))
    return {s: sorted(v)[len(v) // 2] for s, v in by.items()}


def cap_in(season, caps):
    """The league cap in `season` (int dollars). Known seasons are exact. Otherwise the nearest known season wins (the
    earlier one on a tie) and its cap is moved by CBA['CAP_GROWTH']['mean'] a year, rounded to CBA['CAP_ROUND']."""
    if not caps:
        return int(CBA["REFERENCE_CAP"])
    if season in caps:
        return int(caps[season])
    base = min(caps, key=lambda s: (abs(s - season), s))
    step = CBA["CAP_ROUND"]
    est = caps[base] * (1 + CBA["CAP_GROWTH"]["mean"]) ** (season - base)
    return int(round(est / step)) * step
