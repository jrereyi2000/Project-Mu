"""One-time backfill of persona_relationship_event_refs (M-0167; PROJECT_SPEC §7.7).

Every graded pick and call a writer's relationship row has already been charged for is recorded once, so a forced
rerun of an old season never applies one again. Rows written before 2027_02_22 keep only the last five events in
`evidence`, and a ref trimmed out of that window is reconstructed from the graded source.

The rule is conservative and runs ONCE, never at runtime: a runtime ordering guess got real events wrong (a call
graded in the same week as a pick, an out-of-order season pass). Here:
- every ref still in the row's evidence was applied;
- a graded event of the same (writer, subject) STRICTLY older than the earliest graded event left in the evidence
  was applied too, in whatever pass applied that one;
- anything closer than that is left unrecorded rather than guessed: a rerun may still re-apply it, which is the
  safe direction for a backfill of history.

Usage:
  python3 -m db.migrations.2027_02_22_backfill_applied_refs [--dry-run] [--dsn DSN]
"""
import argparse
import json
import os

import psycopg

from newsroom import relationships as rel

DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")


def backfill_refs(evidence, graded):
    """Pure: {ref: season} to record for one row. evidence is the stored evidence list; graded is this (writer,
    subject)'s graded events [{ref, season, week}]."""
    out = {}
    survived = []
    for e in evidence or []:
        if not isinstance(e, dict) or not e.get("ref"):
            continue
        out[e["ref"]] = e.get("season")
        survived.append((int(e.get("season") or 0), int(e.get("week") or 0)))
    if not survived:
        return out
    cut = min(survived)
    for g in graded or []:
        if (int(g.get("season") or 0), int(g.get("week") or 0)) < cut:
            out.setdefault(g["ref"], g.get("season"))
    return out


def _seasons(cur):
    cur.execute("SELECT DISTINCT season FROM agent_predictions ORDER BY season;")
    seasons = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT to_regclass('public.agent_season_calls') IS NOT NULL;")
    if cur.fetchone()[0]:
        cur.execute("SELECT DISTINCT season FROM agent_season_calls ORDER BY season;")
        seasons |= {r[0] for r in cur.fetchall()}
    return sorted(s for s in seasons if s is not None)


def _graded_by_subject(cur):
    """{(persona, target_type, target_id): [{ref, season, week}]} over every season on record."""
    pk_expr = "COALESCE(m.person_key, p.agent_id)" if rel.has_person_key(cur) else "p.agent_id"
    out = {}
    for season in _seasons(cur):
        picks, calls = rel._load_graded(cur, season, None, pk_expr)
        for pk, ttype, tid, _delta, _tags, event in rel.graded_events(picks, calls):
            out.setdefault((pk, ttype, str(tid)), []).append(
                {"ref": event["ref"], "season": event.get("season"), "week": event.get("week")})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Backfill the applied-refs of graded writer events")
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    with psycopg.connect(a.dsn) as conn:
        cur = conn.cursor()
        if not rel._refs_ready(cur):
            print("2027_02_22_relationship_applied_refs is not applied: nothing to backfill")
            return 0
        graded = _graded_by_subject(cur)
        cur.execute("""SELECT persona_id, target_type, target_id, evidence FROM persona_relationships
                       WHERE persona_type = 'writer';""")
        rows = cur.fetchall()
        written = 0
        for pk, ttype, tid, ev in rows:
            if isinstance(ev, str):
                ev = json.loads(ev or "[]")
            wanted = backfill_refs(ev, graded.get((pk, ttype, str(tid)), []))
            for ref, season in sorted(wanted.items()):
                if not a.dry_run:
                    cur.execute("""INSERT INTO persona_relationship_event_refs
                                   (persona_type, persona_id, target_type, target_id, ref, season)
                                   VALUES ('writer', %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING;""",
                                (pk, ttype, str(tid), ref, season))
                written += 1
        print(f"{'would record' if a.dry_run else 'recorded'} {written} applied refs across {len(rows)} rows")
        if a.dry_run:
            conn.rollback()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
