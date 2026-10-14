"""
Capture loader, part 2 (docs/PIPELINE_MIGRATION.md, phase 3): player_id keys on season_stats and
player_stats. Run after the roster load (pipeline/load_capture.py on a roster capture), because
the existing links were wrong in places (two M. Santos on one id, season_stats rows linked to
another player, player_stats rows whose name matched nobody on the team).

  DATABASE_URL="dbname=<test copy> ..." python3 -m db.migrations.2026_10_24_player_id_keys [--dry-run]

1. Re-link every season_stats and player_stats row to a player: list-name key + team + position
   through player_seasons of that season, else list-name key + team.
2. Several rows landing on one player (in a season, or in a game) are merged: the row with the
   most stats is kept with the larger value of every stat (JR, Oct 14). Any row that can't be
   linked stops the migration with the list (nothing changed) so JR can decide.
3. Otherwise add partial unique keys (season, player_id) and (game_id, player_id) and triggers that
   fill player_id on rows the old ingestion (db_handler) and the backfill (game_stat_generator)
   insert by name, so neither has to change. player_name and the old unique keys stay.
Run ./db/backup_db.sh first. One transaction.
"""
import argparse, os, sys

import psycopg

from pipeline.capture_matching import name_key

DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")

TRIGGERS = """
CREATE OR REPLACE FUNCTION fill_player_id_from_roster() RETURNS trigger AS $$
DECLARE
    yr INT;
BEGIN
    IF NEW.player_id IS NOT NULL THEN
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME = 'player_stats' THEN
        SELECT season INTO yr FROM games WHERE id = NEW.game_id;
    ELSE
        yr := NEW.season;
    END IF;
    -- the player on that team and position whose list name matches; one match only
    SELECT p.id INTO NEW.player_id
    FROM players p JOIN player_seasons s ON s.player_id = p.id AND s.season = yr
    WHERE s.team_id = NEW.team_id AND s.position = NEW.position
      AND regexp_replace(upper(p.player_name), '[^A-Z]', '', 'g') = regexp_replace(upper(NEW.player_name), '[^A-Z]', '', 'g')
    GROUP BY p.id HAVING count(*) = 1 LIMIT 1;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS season_stats_fill_player_id ON season_stats;
CREATE TRIGGER season_stats_fill_player_id BEFORE INSERT ON season_stats
    FOR EACH ROW EXECUTE FUNCTION fill_player_id_from_roster();
DROP TRIGGER IF EXISTS player_stats_fill_player_id ON player_stats;
CREATE TRIGGER player_stats_fill_player_id BEFORE INSERT ON player_stats
    FOR EACH ROW EXECUTE FUNCTION fill_player_id_from_roster();

CREATE UNIQUE INDEX IF NOT EXISTS season_stats_season_player_id ON season_stats (season, player_id) WHERE player_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS player_stats_game_player_id ON player_stats (game_id, player_id) WHERE player_id IS NOT NULL;
"""


KEEP = {"id", "season", "game_id", "player_name", "team_id", "position", "player_id", "fantasy_points"}


def merge_rows(cur, table, where, args):
    """Several rows for one player (and season or game): keep the one with the most stats, give it
    the larger value of every stat column, delete the others."""
    cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = %s "
                "AND is_generated = 'NEVER'", (table,))
    stats = [c for c, t in cur.fetchall() if c not in KEEP and t in ("integer", "numeric")]
    cur.execute(f"SELECT id, {', '.join(stats)} FROM {table} WHERE {where}", args)
    rows = cur.fetchall()
    keep = max(rows, key=lambda r: sum(v or 0 for v in r[1:]))[0]
    sets = ", ".join(f"{c} = (SELECT max({c}) FROM {table} WHERE {where})" for c in stats)
    cur.execute(f"UPDATE {table} SET {sets} WHERE id = %s", args * len(stats) + (keep,))
    cur.execute(f"DELETE FROM {table} WHERE {where} AND id <> %s", args + (keep,))


def roster_index(cur):
    """{(season, team_id, name key): [(position, player_id), ...]} from players + player_seasons."""
    cur.execute("SELECT s.season, s.team_id, s.position, p.id, p.player_name, "
                "(s.archetype IS NOT NULL OR s.role IS NOT NULL OR s.dev_trait IS NOT NULL) "
                "FROM players p JOIN player_seasons s ON s.player_id = p.id")
    idx = {}
    for season, team, pos, pid, pname, confirmed in cur.fetchall():
        if team is None:                     # no team this season (team-less rows are skipped)
            continue
        if confirmed:
            CONFIRMED.add(pid)
        idx.setdefault((season, team, name_key(pname)), []).append((pos, pid))
        idx.setdefault((season, None, name_key(pname)), []).append((pos, pid, team))
    return idx


# players the roster load wrote this season (card fields set): when an old duplicate row still shares
# a player's name, team and position (the old scanner's "B. Thomas Jr" next to Brian Thomas Jr.),
# the confirmed one is the player
CONFIRMED = set()


def resolve(idx, season, team, pos, pname):
    """(player_id, how) or (None, why). A row without a team (the old ingestion left some) is
    matched on name + position across the league when exactly one player fits; how then ends with
    the team found, '... -> team N'."""
    if team is None:
        hits = [h for h in idx.get((season, None, name_key(pname)), []) if h[0] == pos]
        if len(hits) == 1:
            return hits[0][1], f"name + position -> team {hits[0][2]}"
        return None, ("ambiguous" if hits else "no team, no match")
    hits = idx.get((season, team, name_key(pname)), [])
    exact = [pid for p, pid in hits if p == pos]
    if len(exact) > 1 and len([pid for pid in exact if pid in CONFIRMED]) == 1:
        exact = [pid for pid in exact if pid in CONFIRMED]
    if len(exact) == 1:
        return exact[0], "name + team + position"
    if len(set(pid for _, pid in hits)) == 1:
        return hits[0][1], "name + team"
    return None, ("ambiguous" if hits else "not on that team")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    with psycopg.connect(DSN) as conn:
        cur = conn.cursor()
        idx = roster_index(cur)
        problems, changes = [], {"season_stats": 0, "player_stats": 0}
        merged = {"season_stats": [], "player_stats": []}

        cur.execute("SELECT id, season, team_id, position, player_name, player_id FROM season_stats")
        ss_links = {}
        for rid, season, team, pos, pname, old in cur.fetchall():
            pid, how = resolve(idx, season, team, pos, pname)
            if pid is None:
                problems.append(f"season_stats {season} {pname} {pos} team {team}: {how}")
                continue
            ss_links.setdefault((season, pid), []).append(f"{pname} {pos} team {team}")
            if pid != old:
                changes["season_stats"] += 1
                cur.execute("UPDATE season_stats SET player_id = %s WHERE id = %s", (pid, rid))
            if " -> team " in how:
                changes["season_stats_team_filled"] = changes.get("season_stats_team_filled", 0) + 1
                cur.execute("UPDATE season_stats SET team_id = %s WHERE id = %s", (int(how.rsplit(" ", 1)[1]), rid))
        # one player with several rows in a season (Brian Thomas Jr.: "B. Thomas Jr" and "B. Thomas Jr."
        # from the old ingestion): merge them, the larger value of each stat (JR, Oct 14)
        for (season, pid), rows in ss_links.items():
            if len(rows) > 1:
                merged["season_stats"].append(f"{season} player {pid}: {rows}")
                merge_rows(cur, "season_stats", "season = %s AND player_id = %s", (season, pid))

        cur.execute("SELECT ps.id, g.season, ps.team_id, ps.position, ps.player_name FROM player_stats ps "
                    "JOIN games g ON g.id = ps.game_id")
        unresolved = {}
        for rid, season, team, pos, pname in cur.fetchall():
            pid, how = resolve(idx, season, team, pos, pname)
            if pid is None:
                unresolved.setdefault(f"{season} {pname} {pos} team {team}: {how}", 0)
                unresolved[f"{season} {pname} {pos} team {team}: {how}"] += 1
                continue
            changes["player_stats"] += 1
            cur.execute("UPDATE player_stats SET player_id = %s WHERE id = %s", (pid, rid))
            if " -> team " in how:
                changes["player_stats_team_filled"] = changes.get("player_stats_team_filled", 0) + 1
                cur.execute("UPDATE player_stats SET team_id = %s WHERE id = %s", (int(how.rsplit(" ", 1)[1]), rid))
        problems += [f"player_stats {k} ({n} rows)" for k, n in sorted(unresolved.items())]
        cur.execute("SELECT game_id, player_id FROM player_stats WHERE player_id IS NOT NULL "
                    "GROUP BY 1, 2 HAVING count(*) > 1")
        for gid, pid in cur.fetchall():
            merged["player_stats"].append(f"game {gid} player {pid}")
            merge_rows(cur, "player_stats", "game_id = %s AND player_id = %s", (gid, pid))

        print(f"re-linked: {changes}")
        for t, rows in merged.items():
            if rows:
                print(f"merged duplicate {t} rows: {len(rows)} (" + "; ".join(rows[:5]) + (" ..." if len(rows) > 5 else "") + ")")
        if problems:
            conn.rollback()
            print(f"STOPPED, nothing changed: {len(problems)} problems")
            for p in problems:
                print("  " + p)
            sys.exit(1)
        cur.execute(TRIGGERS)
        if a.dry_run:
            conn.rollback()
            print("dry run: rolled back")
            return
        conn.commit()
        print("keys and triggers added")


if __name__ == "__main__":
    main()
