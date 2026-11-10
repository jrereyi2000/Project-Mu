"""One-time backfill of persona_relationship_event_refs (M-0167/M-0174; PROJECT_SPEC §7.7).

Every graded pick and call a writer's relationship row has already been charged for is recorded once, so a forced
rerun of an old season never applies one again. This records only what is PROVEN: the refs still on the row's
evidence, plus the refs in its sticky APPLIED_REFS entry. Nothing is inferred — an earlier version guessed that a
graded event older than the evidence was applied, which would skip a pick on a game that completed late and was
never applied (M-0174 L-c). A ref whose only record was trimmed out of the five-entry window before the sticky
existed stays unrecorded: a rerun may re-apply it, which is the safe direction for a backfill of history.

The runtime guard reads the union of the table, the evidence and the sticky entry, so this backfill is an
optimisation, not a step anyone has to remember.

Usage:
  python3 -m db.migrations.2027_02_22_backfill_applied_refs [--dry-run] [--dsn DSN]
"""
import argparse
import json
import os

import psycopg

from newsroom import relationships as rel

DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")


def backfill_refs(evidence):
    """Pure: {ref: season} proven applied for one row: the refs still on its evidence and the sticky applied-refs
    entry. Nothing is inferred."""
    out = {}
    for e in evidence or []:
        if not isinstance(e, dict):
            continue
        if e.get("kind") == "APPLIED_REFS":
            for ref in e.get("refs") or []:
                out.setdefault(ref, None)
        elif e.get("ref"):
            out[e["ref"]] = e.get("season")
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
        cur.execute("""SELECT persona_id, target_type, target_id, evidence FROM persona_relationships
                       WHERE persona_type = 'writer';""")
        rows = cur.fetchall()
        written = 0
        for pk, ttype, tid, ev in rows:
            if isinstance(ev, str):
                ev = json.loads(ev or "[]")
            wanted = backfill_refs(ev)
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
