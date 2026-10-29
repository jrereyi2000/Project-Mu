# @AUDIT-STATUS: OBSOLETE_FRESH_SAVE (mu-history data)
# @REPLACED-BY: db/seed_clean_db.py + db/init_db.sql (old-universe data only; AUDIT_LEDGER.md §3b)
"""
Data fix & collision avoidance (2026-10-15):
Renames 16 draft class prospects in the 2036 draft class to eliminate name collisions
with active NFL players and other prospects.

Updates:
- PostgreSQL table: draft_prospects
- captures/prospects/prospects.csv
- data/draft/2036_prospects.csv

Usage:
  python3 -m db.migrations.2026_11_22_rename_colliding_prospects [--dry-run]
"""
import argparse
import csv
import os
import psycopg

DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")

PROSPECT_RENAMES = [
    # (prospect_id, expected_old_name, new_name, position, projected_pick)
    (751, "TIM MURRAY", "TIM MURPHY", "CB", 362),
    (414, "JEREMIAH BENNETT", "NATE BENNETT", "LT", 25),
    (740, "WILL ANDERSON", "WILL ANDERS", "LEDG", 351),
    (675, "JOSH WRIGHT", "JOSH WRIGLEY", "RG", 286),
    (726, "TAYLOR IGLESIAS", "TAYLOR IGLESI", "LS", 337),
    (626, "JAMIE SANCHEZ", "XAVIER SANCHEZ", "TE", 237),
    (768, "JORDAN ANDREWS", "JORDAN ANDRE", "QB", 379),
    (502, "JUAN PARRA", "JUAN HOPILO", "HB", 113),
    (648, "JONATHAN ALONSO", "JONATHAN ALORA", "WR", 259),
    (632, "KEVIN MEANS", "KEVIN MEANSON", "SAM", 243),
    (661, "DILLON JOHNSON", "DILLON GLEASON", "SS", 272),
    (707, "DORIAN BROWN", "DORIAN BROWNLEE", "SS", 318),
    (739, "JEREMY RICHARDSON", "JEREMY RICHIE", "LEDG", 350),
    (743, "JON EDWARDS", "JON EDWIN", "SS", 354),
    (532, "MATEO GONZALEZ", "MATEO GONZO", "LT", 143),
    (645, "MIGUEL GUTIERREZ", "MIGUEL RAMANO", "C", 256),
]


def update_csv_file(path: str, dry_run: bool = False):
    if not os.path.exists(path):
        print(f"Warning: CSV file {path} not found.")
        return
    rows = []
    updated_count = 0
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for r in reader:
            pick_val = r.get("PROJECTED_PICK") or r.get("PICK") or r.get("projected_pick") or 0
            pick = int(pick_val)
            name = (r.get("FULL_NAME") or r.get("NAME") or r.get("full_name") or "").strip().upper()
            pos = (r.get("POSITION") or r.get("POS") or r.get("position") or "").strip().upper()
            for pid, old_name, new_name, target_pos, target_pick in PROSPECT_RENAMES:
                if pick == target_pick and pos == target_pos:
                    if "FULL_NAME" in r:
                        r["FULL_NAME"] = new_name
                    elif "NAME" in r:
                        r["NAME"] = new_name
                    elif "full_name" in r:
                        r["full_name"] = new_name
                    updated_count += 1
                    break
            rows.append(r)

    print(f"[{'DRY-RUN' if dry_run else 'APPLY'}] {path}: updated {updated_count} rows")
    if not dry_run:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


def run_migration(dry_run: bool = False):
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            for pid, old_name, new_name, pos, pick in PROSPECT_RENAMES:
                cur.execute("SELECT id, full_name, position, projected_pick FROM draft_prospects WHERE id = %s", (pid,))
                row = cur.fetchone()
                if not row:
                    raise RuntimeError(f"Prospect ID {pid} not found in draft_prospects!")
                curr_id, curr_name, curr_pos, curr_pick = row
                if curr_name.upper() != old_name:
                    raise RuntimeError(f"Prospect ID {pid} mismatch: expected '{old_name}', found '{curr_name}'")

                print(f"[{'DRY-RUN' if dry_run else 'APPLY'}] ID {pid:4d} (#{pick:3d} {pos:4s}): '{curr_name}' -> '{new_name}'")
                if not dry_run:
                    cur.execute("UPDATE draft_prospects SET full_name = %s WHERE id = %s", (new_name, pid))

            if dry_run:
                conn.rollback()
                print(f"\nDry run complete. {len(PROSPECT_RENAMES)} database prospects checked.")
            else:
                conn.commit()
                print(f"\nSuccessfully renamed {len(PROSPECT_RENAMES)} prospects in draft_prospects.")

    # Update CSVs
    update_csv_file("captures/prospects/prospects.csv", dry_run=dry_run)
    update_csv_file("data/draft/2036_prospects.csv", dry_run=dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rename colliding draft prospects")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without committing")
    args = parser.parse_args()
    run_migration(dry_run=args.dry_run)
