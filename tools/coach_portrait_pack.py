#!/usr/bin/env python3
"""The per-offseason coach portrait pack (COACH_SAVE_WRITES_PLAN §2.8).

Run after the league office's OFFSEASON_START write:

    python3 -m tools.coach_portrait_pack --season N [--dry-run] [--mark-installed]

Every GENERATED coach profile without a portrait ID gets a free ID from COACH_PORTRAIT_RANGE, the block Spike A
measured free of every Coach and Player `Portrait` in CAREER-MUBASE (docs/reference/MADDEN_COACH_RECORDS.md §4.2).
An ID is never reused: the range is allocated against every portrait_id Mu already stores and every ID named in an
earlier pack's manifest.

Then:
* `data/headshots/dds/coaches/pack_<N>/portrait_manifest.csv` is written with `portrait_id, dds_file, coach_key` and
  each coach's 512x512 BC3 DDS (from `headshots.coach_headshots`, `data/headshots/dds/coaches/<coach_key>.dds`) is
  copied into the pack folder under its portrait ID, ready for `/build-mod`;
* the profile gets `portrait_id` and `portrait_status = 'PACKED'`. That id / status pair is the queue: the next
  league office write pushes `Portrait = <id>` and `Portrait_Force_Silhouette = false` onto the save record
  (pipeline/league_office.py, the assign's `set`).

`--mark-installed` flips the coaches named in `pack_<N>/portrait_manifest.csv` from PACKED to INSTALLED, once JR has
confirmed the mod is in the game. `--dry-run` prints the allocations and writes nothing (no manifest, no DDS copies,
no database updates).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DDS_COACHES_DIR = ROOT / "data" / "headshots" / "dds" / "coaches"
DSN_DEFAULT = "host=localhost dbname=madden user=postgres password=postgres"

# Spike A §4.2: 5324-5484 is free of every Coach and Player Portrait in CAREER-MUBASE (161 IDs). The next block
# (5486-5581) is the documented fallback, deliberately not used here: the plan pins this one.
COACH_PORTRAIT_RANGE = (5324, 5484)
MANIFEST_NAME = "portrait_manifest.csv"
MANIFEST_COLUMNS = ("portrait_id", "dds_file", "coach_key")

_GENERATED_QUERY = """
    SELECT person_kind, person_id, portrait_id
      FROM coach_madden_profiles
     WHERE portrait_status = 'GENERATED'
       AND (portrait_id IS NULL OR portrait_id = 0)
     ORDER BY person_kind, person_id
"""
_USED_QUERY = """
    SELECT DISTINCT portrait_id FROM coach_madden_profiles
     WHERE portrait_id IS NOT NULL AND portrait_id > 0
"""


class PortraitRangeExhausted(RuntimeError):
    """Every ID in COACH_PORTRAIT_RANGE is already allocated (or the request does not fit)."""


def pack_dir(season: int, dds_root: Path | str = DDS_COACHES_DIR) -> Path:
    """The pack folder for a season: data/headshots/dds/coaches/pack_<N>."""
    return Path(dds_root) / f"pack_{season}"


def coach_key_for(person_kind: str, person_id: int) -> str:
    """The coach key headshots use: coach-<coaches.id> or coord-<coordinators.id>."""
    return f"{'coord' if str(person_kind).upper() == 'COORD' else 'coach'}-{int(person_id)}"


def plan_assignments(rows, used, id_range=COACH_PORTRAIT_RANGE):
    """[(row, portrait_id)] for every profile row without an ID, lowest free ID first.

    `used` is mutated with the ids handed out, so two calls cannot hand out the same one. Raises
    PortraitRangeExhausted when the range runs out."""
    start, end = id_range
    next_id = start
    out = []
    for row in rows:
        while next_id in used:
            next_id += 1
        if next_id > end:
            raise PortraitRangeExhausted(
                f"COACH_PORTRAIT_RANGE {start}-{end} has no free ID left for {len(rows) - len(out)} more coaches")
        out.append((row, next_id))
        used.add(next_id)
        next_id += 1
    return out


def _used_ids(cur, dds_root: Path) -> set:
    """Every portrait ID Mu already stores, plus every ID in an earlier pack's manifest (never reuse across runs)."""
    used = set()
    cur.execute(_USED_QUERY)
    for row in cur.fetchall():
        if row and row[0] is not None:
            used.add(int(row[0]))
    for manifest in sorted(Path(dds_root).glob(f"pack_*/{MANIFEST_NAME}")):
        used.update(row.get("portrait_id") for row in _read_manifest(manifest))
    used.discard("")
    return {int(v) for v in used if str(v).strip().isdigit()}


def _read_manifest(path: Path) -> list:
    if not path.exists():
        return []
    with open(path, newline="") as fh:
        return [row for row in csv.DictReader(fh)]


def _merge_manifest(path: Path, new_rows) -> list:
    """The pack's rows: what was there before plus the new ones, one row per portrait ID, sorted by ID."""
    rows = {int(r["portrait_id"]): r for r in _read_manifest(path) if str(r.get("portrait_id") or "").isdigit()}
    for row in new_rows:
        rows[int(row["portrait_id"])] = row
    return [rows[k] for k in sorted(rows)]


def _write_manifest(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(MANIFEST_COLUMNS), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def mark_installed(cur, season, dry_run=False, dds_root=DDS_COACHES_DIR, log_fn=print):
    """INSTALLED for the coaches named in the season's manifest (JR has confirmed the mod is in). Idempotent."""
    manifest = pack_dir(season, dds_root) / MANIFEST_NAME
    rows = _read_manifest(manifest)
    if not rows:
        log_fn(f"[coach_portrait_pack] no manifest at {manifest}; nothing to mark installed")
        return {"season": season, "installed": 0, "manifest": str(manifest) if manifest.exists() else None,
                "dry_run": dry_run}
    ids = sorted({int(r["portrait_id"]) for r in rows if str(r.get("portrait_id") or "").isdigit()})
    if dry_run:
        for pid in ids:
            log_fn(f"[coach_portrait_pack] would mark portrait {pid} INSTALLED")
        return {"season": season, "installed": len(ids), "manifest": str(manifest), "dry_run": True}
    installed = 0
    for pid in ids:
        cur.execute("""UPDATE coach_madden_profiles SET portrait_status = 'INSTALLED'
                        WHERE portrait_id = %s AND portrait_status = 'PACKED'""", (pid,))
        installed += max(0, int(getattr(cur, "rowcount", 0) or 0))
    log_fn(f"[coach_portrait_pack] season {season}: marked {installed} coach portrait(s) INSTALLED")
    return {"season": season, "installed": installed, "manifest": str(manifest), "dry_run": False}


def run_pack(cur, season, dry_run=False, mark_installed=False, dds_root=DDS_COACHES_DIR, log_fn=print):
    """Assign free portrait IDs to the GENERATED coaches, write the pack's manifest and DDS copies, and move the
    profiles to PACKED. `dry_run` reads and prints only. `mark_installed` runs the INSTALLED step instead."""
    dds_root = Path(dds_root)
    if mark_installed:
        return mark_installed(cur, season, dry_run=dry_run, dds_root=dds_root, log_fn=log_fn)

    cur.execute(_GENERATED_QUERY)
    rows = [dict(zip(("person_kind", "person_id", "portrait_id"), row)) for row in cur.fetchall()]
    used = _used_ids(cur, dds_root)
    assignments = plan_assignments(rows, used)

    target = pack_dir(season, dds_root)
    out_rows, missing = [], []
    for row, pid in assignments:
        key = coach_key_for(row["person_kind"], row["person_id"])
        source = dds_root / f"{key}.dds"
        dds_file = f"{pid}.dds"
        out_rows.append({"portrait_id": str(pid), "dds_file": dds_file, "coach_key": key,
                         "person_kind": row["person_kind"], "person_id": int(row["person_id"])})
        if not source.exists():
            missing.append(key)

    report = {"season": season, "assigned": len(out_rows), "manifest": str(target / MANIFEST_NAME),
              "assignments": out_rows, "missing_dds": missing, "dry_run": dry_run}
    if dry_run:
        for row in out_rows:
            log_fn(f"[coach_portrait_pack] would pack {row['coach_key']} as portrait {row['portrait_id']}")
        if missing:
            log_fn(f"[coach_portrait_pack] no DDS yet for: {', '.join(missing)}")
        return report
    if not out_rows:
        log_fn(f"[coach_portrait_pack] season {season}: no GENERATED coach needs a portrait ID")
        return report

    target.mkdir(parents=True, exist_ok=True)
    for item in out_rows:
        source = dds_root / f"{item['coach_key']}.dds"
        if source.exists():
            shutil.copyfile(source, target / item["dds_file"])
    merged = _merge_manifest(target / MANIFEST_NAME, out_rows)
    _write_manifest(target / MANIFEST_NAME, merged)
    for item in out_rows:
        cur.execute("""UPDATE coach_madden_profiles SET portrait_id = %s, portrait_status = 'PACKED'
                        WHERE person_kind = %s AND person_id = %s""",
                    (int(item["portrait_id"]), item["person_kind"], item["person_id"]))
    if missing:
        log_fn(f"[coach_portrait_pack] no DDS yet for: {', '.join(missing)} (listed in the manifest)")
    log_fn(f"[coach_portrait_pack] season {season}: packed {len(out_rows)} coach portrait(s) -> "
           f"{target / MANIFEST_NAME}")
    return report


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Assign coach portrait IDs and write the offseason portrait pack.")
    ap.add_argument("--season", type=int, required=True, help="the season whose pack to write (run after the "
                                                              "OFFSEASON_START write)")
    ap.add_argument("--dry-run", action="store_true", help="print the allocations; write nothing")
    ap.add_argument("--mark-installed", action="store_true",
                    help="set INSTALLED for the coaches in this season's pack, once JR confirms the mod is in")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", DSN_DEFAULT), help="PostgreSQL DSN")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    import psycopg

    with psycopg.connect(args.dsn) as conn:
        with conn.cursor() as cur:
            report = run_pack(cur, args.season, dry_run=args.dry_run, mark_installed=args.mark_installed)
        if not args.dry_run:
            conn.commit()
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
