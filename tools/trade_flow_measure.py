"""Measure the CPU trade flow on an archive replay (CLOSE_OUT_CHECKLIST P3-08; PROJECT_SPEC §2.17).

The P1-25 replay ran `engines.gm_trade_engine --week` and counted talks, but never executed the deals the talks
agreed to: `b["executed"]` stayed 0 all run, the weekly budget kept spending a full target, and an agreement that
a real league office would have written that week could still expire later. This tool closes that loop, so the
number it reports is the observable the season is judged on (agreed, executed in-season trades) rather than a
talk count:

  for each week:
    run_week(...)                          # the engine's real search + gm_talks, same seeds as the CLI
    execute this week's auto-approved CPU deals in Mu's books   (engines.front_office._execute, the DB half of
                                           the league office's save write: players, contracts, cap rows and picks
                                           move exactly as they do after a successful write)
    a deal the books refuse is expired through front_office.could_not_make, as the league office does

The report is the measured table P3-08 records: talks, outcomes, breakdown share, turns by message source
(LLM vs TEMPLATE), leaks, new proposals, executed trades, and each week's budget line.

Scratch databases only (the name must start with `mu_scratch_`), and the reset only touches the replay's own
season's trade history. The archive databases are never a destination.

  python3 -m tools.trade_flow_measure --dsn "host=localhost dbname=mu_scratch_p3_trade_remeasure_test user=postgres password=postgres" \
      --season 2036 --first-week 4 --last-week 8 [--reset] [--no-llm] [--json OUT]
"""
import argparse
import json
import os
import random
import sys

def _require_scratch(conn):
    """The connection is the authority, whatever the DSN text says (db/migrate.py's rule)."""
    name = conn.execute("SELECT current_database()").fetchone()[0]
    if not name.startswith("mu_scratch_"):
        raise SystemExit(f"refusing {name!r}: trade_flow_measure writes only to a mu_scratch_* database")
    return name


def reset(cur, season):
    """Clear the replay season's trade history. Sightings go too: they were generated off the archive's own
    history, and leaving them in would make a replay neither fresh nor repeatable."""
    cur.execute("SELECT id FROM gm_trade_proposals WHERE season = %s", (season,))
    ids = [r[0] for r in cur.fetchall()]
    if ids:
        cur.execute("DELETE FROM gm_trade_assets WHERE proposal_id = ANY(%s)", (ids,))
        cur.execute("DELETE FROM gm_trade_negotiations WHERE season = %s", (season,))
        cur.execute("DELETE FROM gm_trade_leaks WHERE season = %s", (season,))
    cur.execute("DELETE FROM gm_trade_negotiation_turns WHERE negotiation_id IN "
                "(SELECT id FROM gm_trade_negotiations WHERE season = %s)", (season,))
    cur.execute("DELETE FROM gm_trade_proposals WHERE season = %s", (season,))
    cur.execute("DELETE FROM gm_trade_sightings")
    # the replay is the all-CPU era: no team is JR's (the registry has no entry for a scratch DB; this is the
    # gm_team_settings fallback the archive clone arrives with).
    cur.execute("UPDATE gm_team_settings SET user_controlled = FALSE, suggestions_on = FALSE")
    return len(ids)


def execute_approved(cur, season, week, log=print):
    """Execute this week's auto-approved CPU deals in Mu's books, exactly as the league office does after a
    successful save write (pipeline/league_office._record_trades). Returns (executed, refused)."""
    from engines import front_office as fo
    cur.execute("""SELECT id FROM gm_trade_proposals
                   WHERE season = %s AND week = %s AND status = 'APPROVED'
                     AND 'auto_approved' = ANY(flags)
                   ORDER BY id""", (season, week))
    ids = [r[0] for r in cur.fetchall()]
    done, refused = 0, []
    for pid in ids:
        try:
            p = fo._proposal(cur, pid)
            fo._execute(cur, p, "PROPOSED")
        except Exception as e:                       # noqa: BLE001 - the league office's own refuse path
            try:
                fo.could_not_make(cur, pid, f"replay: {type(e).__name__}: {e}")
            except Exception:                        # noqa: BLE001 - already expired/gone: nothing left to mark
                pass
            refused.append({"id": pid, "reason": f"{type(e).__name__}: {e}"})
            log(f"  rejected #%s: %s" % (pid, e))
            continue
        cur.execute("""UPDATE gm_trade_proposals
                       SET status = 'EXECUTED', executed_at = COALESCE(executed_at, now()) WHERE id = %s""", (pid,))
        done += 1
    return done, refused


def replay(dsn, season, first_week, last_week, *, reset_first=False, use_llm=True, log=print):
    import psycopg
    from engines import gm_trade_engine as ge
    from engines import trade_value as tv
    from engines.gm_llm import flush, make_llm

    llm = make_llm("gm_rationale") if use_llm else None
    talk_llm = make_llm("gm_talks") if use_llm else None
    weeks = []
    cleared = None
    try:
        with psycopg.connect(dsn) as conn:
            _require_scratch(conn)
            cur = conn.cursor()
            if reset_first:
                cleared = reset(cur, season)
                conn.commit()
            for week in range(first_week, last_week + 1):
                window = ge.window_for(week)
                if window is None:
                    raise SystemExit(f"week {week} is outside the trade window")
                rep = ge.run_week(cur, season, week, llm=llm, talk_llm=talk_llm, log=log)
                executed, refused = execute_approved(cur, season, week, log=log)
                conn.commit()
                talks = rep.get("talks") or {}
                outcomes = {}
                for t in talks.get("talks") or []:
                    outcomes[t.get("status")] = outcomes.get(t.get("status"), 0) + 1
                weeks.append({
                    "week": week,
                    "budget": rep.get("budget"),
                    "new": len(rep.get("proposals") or []),
                    "talk_outcomes": outcomes,
                    "talks": len(talks.get("talks") or []),
                    "executed": executed,
                    "refused": refused,
                    "expired": len(rep.get("expired") or []),
                    "rechecked": len(rep.get("rechecked") or []),
                })
                print(f"week {week}: {len(rep.get('proposals') or [])} new, "
                      f"{len(talks.get('talks') or [])} talks, {executed} executed, {len(refused)} refused")
            report = diagnostic(cur, season, weeks, cleared)
    finally:
        flush(llm)
        flush(talk_llm)
    return report


def diagnostic(cur, season, weeks, cleared=None):
    """The measured table. Talks and turns come from the stored rows, so a resumed/carried talk counts once."""
    cur.execute("""SELECT status, count(*) FROM gm_trade_negotiations
                   WHERE season = %s AND kind = 'CPU' GROUP BY 1""", (season,))
    outcomes = dict(cur.fetchall())
    cur.execute("""SELECT message_source, count(*) FROM gm_trade_negotiation_turns
                   WHERE negotiation_id IN
                         (SELECT id FROM gm_trade_negotiations WHERE season = %s AND kind = 'CPU')
                   GROUP BY 1""", (season,))
    sources = dict(cur.fetchall())
    cur.execute("""SELECT count(*) FROM gm_trade_leaks WHERE season = %s""", (season,))
    leaks = cur.fetchone()[0]
    cur.execute("""SELECT count(*) FROM gm_trade_proposals
                   WHERE season = %s AND trade_window = 'IN_SEASON' AND status = 'EXECUTED'""", (season,))
    executed = cur.fetchone()[0]
    cur.execute("""SELECT count(*) FROM gm_trade_proposals
                   WHERE season = %s AND trade_window = 'IN_SEASON'
                     AND status IN ('EXECUTED', 'STALLED', 'REJECTED', 'EXPIRED')""", (season,))
    decided = cur.fetchone()[0]
    cur.execute("""SELECT t.posture, count(*) FROM gm_trade_negotiation_turns t
                   JOIN gm_trade_negotiations n ON n.id = t.negotiation_id
                   WHERE n.season = %s AND n.kind = 'CPU' AND t.turn_no = 1 AND t.posture IS NOT NULL
                   GROUP BY 1""", (season,))
    openings = dict(cur.fetchall())
    cur.execute("""SELECT count(*) FROM gm_trade_negotiation_turns
                   WHERE negotiation_id IN (SELECT id FROM gm_trade_negotiations WHERE season = %s AND kind = 'CPU')
                     AND action = 'COUNTER'""", (season,))
    counters = cur.fetchone()[0]
    talks = sum(outcomes.values())
    llm_lines = sources.get("LLM", 0)
    template = sources.get("TEMPLATE", 0)
    turns = llm_lines + template
    report = {
        "season": season,
        "cleared_proposals": cleared,
        "weeks": weeks,
        "talks": talks,
        "outcomes": {k: outcomes.get(k, 0) for k in ("AGREED", "STALLED", "WALKED_AWAY", "OPEN")},
        "breakdown_share": round((talks - outcomes.get("AGREED", 0)) / talks, 3) if talks else None,
        "openings": openings,
        "turns": turns,
        "message_lines": {"LLM": llm_lines, "TEMPLATE": template,
                          "template_share": round(template / turns, 3) if turns else None},
        "counters": counters,
        "counters_per_talk": round(counters / talks, 2) if talks else None,
        "leaks": leaks,
        "executed": executed,
        "decided": decided,
        "executed_share": round(executed / decided, 3) if decided else None,
    }
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--season", type=int, default=2036)
    ap.add_argument("--first-week", type=int, default=4)
    ap.add_argument("--last-week", type=int, default=8)
    ap.add_argument("--reset", action="store_true", help="clear this season's trade history first")
    ap.add_argument("--no-llm", action="store_true", help="policy and template lines only (no model calls)")
    ap.add_argument("--json", metavar="OUT")
    a = ap.parse_args(argv)
    if not a.dsn:
        raise SystemExit("a DSN is required (--dsn or DATABASE_URL)")
    rep = replay(a.dsn, a.season, a.first_week, a.last_week, reset_first=a.reset, use_llm=not a.no_llm)
    text = json.dumps(rep, indent=1, default=str)
    print(text)
    if a.json:
        with open(a.json, "w") as fh:
            fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
