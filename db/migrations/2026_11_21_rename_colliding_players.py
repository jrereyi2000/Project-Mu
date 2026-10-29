# @AUDIT-STATUS: OBSOLETE_FRESH_SAVE (mu-history data)
# @REPLACED-BY: db/seed_clean_db.py + db/init_db.sql (old-universe data only; AUDIT_LEDGER.md §3b)
"""
Data fix & collision avoidance (2026-10-15):
Renames 33 Madden-generated fake players who shared their first initial and last name
with other players in the league (especially within the same position or stat category),
preventing ambiguous OCR and season/game stat loading errors.

Usage:
  python3 -m db.migrations.2026_11_21_rename_colliding_players [--dry-run]
"""
import argparse
import os
import psycopg
from pipeline.capture_matching import db_player_name

DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")

RENAMES = [
    # (player_id, expected_old_full_name, new_full_name)
    # 1. Tier 1: Exact position collisions
    (519, "Brenen Thompson", "Brenen Paulsen"),
    (8242, "Dakota Turner", "Dakota Behr"),
    (818, "Dallas Turner", "Frank Turner"),
    (79, "Trezmen Davis", "Trezmen Wofford"),
    (188, "Tacario Davis", "Tacario Folsom"),
    (1127, "Jamie Alonso", "Carl Alonso"),
    (8210, "Justin Fernandez", "Michael Fernandez"),
    (1079, "Josh Ortega", "Gabriel Ortega"),
    (1166, "J.C. Kilgore", "J.C. Robbins"),
    (504, "Manuel Peralta", "Manuel Feralta"),
    (38, "Enrique Rush", "Paul Rush"),

    # 2. Tier 2: Offensive Skill (Passing / Rushing / Receiving)
    (382, "Jerry Smith", "Jerry Rainford"),
    (791, "Tucker Baker", "Josh Baker"),
    (8173, "Emilio Fernandez", "Roberto Fernandez"),
    (8154, "Darren Monroe", "Darren Milroe"),
    (8089, "Manny Santos", "Andrew Santos"),
    (1104, "Matt Taylor", "DeVonta Taylor"),
    (1, "Percy Melton", "Luke Melton"),

    # 3. Tier 2: Defense
    (851, "Deron Jackson", "Deron Hamilton"),
    (501, "Donnie Jackson", "Donnie Jack"),
    (409, "Brooks Hackett", "Luke Hackett"),
    (1462, "Delvin Babineaux", "Melvin Babineaux"),
    (1016, "Jay Jones", "Jay Paul"),
    (885, "Jared Watkins", "Brett Watkins"),
    (1028, "Mike Cunningham", "Jack Cunningham"),
    (1436, "Tom Elliott", "Dean Elliott"),
    (381, "Jason Cook", "Piere Cook"),

    # 4. Tier 2: Offensive Line
    (1342, "Cortney Ford", "Garrett Ford"),
    (873, "Clyde Thurston", "Mark Thurston"),
    (222, "Jose Leonard", "Quinn Leonard"),
    (8095, "Nick Baca", "Ollie Baca"),
    (8099, "Nick Stratton", "John Stratton"),
    (216, "Dwayne Davis", "Lamar Davis"),
]


def run_migration(dry_run: bool = False):
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            for pid, old_full, new_full in RENAMES:
                cur.execute("SELECT id, full_name, player_name FROM players WHERE id = %s", (pid,))
                row = cur.fetchone()
                if not row:
                    raise RuntimeError(f"Player ID {pid} not found in database!")
                curr_id, curr_full, curr_short = row
                if curr_full != old_full:
                    raise RuntimeError(f"Player ID {pid} name mismatch: expected '{old_full}', found '{curr_full}'")

                parts = new_full.split(" ", 1)
                new_short = db_player_name(parts[0], parts[1])
                print(f"[{'DRY-RUN' if dry_run else 'APPLY'}] ID {pid:4d}: '{curr_full}' ({curr_short}) -> '{new_full}' ({new_short})")

                if not dry_run:
                    cur.execute("UPDATE players SET full_name = %s, player_name = %s WHERE id = %s", (new_full, new_short, pid))
                    cur.execute("UPDATE player_stats SET player_name = %s WHERE player_id = %s", (new_short, pid))
                    cur.execute("UPDATE season_stats SET player_name = %s WHERE player_id = %s", (new_short, pid))
                    cur.execute("UPDATE injuries SET player_name = %s WHERE player_id = %s", (new_short, pid))
                    cur.execute("UPDATE fantasy_projections SET player_name = %s WHERE player_id = %s", (new_short, pid))

            if dry_run:
                print(f"\nDry run complete. {len(RENAMES)} players checked.")
                conn.rollback()
            else:
                conn.commit()
                print(f"\nSuccessfully renamed {len(RENAMES)} players and updated dependent tables.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rename colliding players to avoid OCR/stats matching conflicts")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without committing")
    args = parser.parse_args()
    run_migration(dry_run=args.dry_run)
