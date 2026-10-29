# @AUDIT-STATUS: OBSOLETE_FRESH_SAVE (mu-history data)
# @REPLACED-BY: db/seed_clean_db.py + db/init_db.sql (old-universe data only; AUDIT_LEDGER.md §3b)
"""
Data fix (Oct 14): players.full_name spellings left in all caps ("JERRY RAYFORD") by the old video
scanner. The roster load kept them because it keeps a stored spelling whose letters match the
card's; it now prefers the card's casing over an all-caps one (pipeline/load_capture.py), and this
re-cases the names already stored.

  DATABASE_URL="dbname=<test copy> ..." python3 -m db.migrations.2026_10_27_name_casing [--dry-run]

Each all-caps full_name takes the casing of the card in the loaded roster capture (captures row,
mode roster) with the same letters on the player's team, else on any team when only one card has
those letters; with no such card it is title-cased through name_case ('Omar Cooper Jr.'). Run
./db/backup_db.sh first. One transaction.
"""
import argparse, csv, os

import psycopg

from pipeline.capture_matching import all_caps, full_key, name_case

DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")


def card_names(folder):
    """{(team_name_upper, full_key): 'First Last'} and {full_key: ['First Last', ...]} from roster_cards.csv."""
    by_team, by_key = {}, {}
    with open(os.path.join("captures", folder, "roster_cards.csv"), newline="") as f:
        for c in csv.DictReader(f):
            name = f"{name_case(c['first'].strip())} {name_case(c['last'].strip())}".strip()
            by_team[(c["team"].strip().upper(), full_key(name))] = name
            by_key.setdefault(full_key(name), []).append(name)
    return by_team, by_key


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    with psycopg.connect(DSN) as conn:
        cur = conn.cursor()
        cur.execute("SELECT folder FROM captures WHERE mode = 'roster' AND status = 'loaded' ORDER BY loaded_at DESC LIMIT 1")
        row = cur.fetchone()
        by_team, by_key = card_names(row[0]) if row else ({}, {})
        cur.execute("""SELECT p.id, p.full_name, upper(t.name)
                       FROM players p
                       LEFT JOIN league_state ls ON ls.id = 1
                       LEFT JOIN player_seasons s ON s.player_id = p.id AND s.season = ls.current_season
                       LEFT JOIN teams t ON t.id = s.team_id""")
        changes = []
        for pid, full_name, team in cur.fetchall():
            if not all_caps(full_name):
                continue
            key = full_key(full_name)
            new = by_team.get((team, key)) or (by_key[key][0] if len(set(by_key.get(key, []))) == 1 else None)
            src = "card" if new else "title-case"
            new = new or " ".join(name_case(w.title()) for w in full_name.split())
            changes.append((pid, full_name, new, src))
        for pid, old, new, src in changes:
            print(f"{pid:>5}  {old:<28} -> {new:<28} ({src})")
            cur.execute("UPDATE players SET full_name = %s WHERE id = %s", (new, pid))
        print(f"{len(changes)} names re-cased")
        if a.dry_run:
            conn.rollback()
            print("dry run: nothing written")


if __name__ == "__main__":
    main()
