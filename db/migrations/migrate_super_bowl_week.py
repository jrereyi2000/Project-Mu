"""Moves Super Bowl games from week 22 to week 23 (Super Bowl Week per docs/EDITORIAL_MATRIX.md).

Historical imports (pipeline/batch_menus.py) previously stored the Super Bowl at week 22. Idempotent.
"""
import os

import psycopg

DB_CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")

with psycopg.connect(DB_CONN_INFO) as conn:
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE games SET week = 23
            WHERE game_type = 'SUPER_BOWL' AND week = 22
            RETURNING season;
        """)
        moved = sorted(r[0] for r in cur.fetchall())
        print(f"Moved {len(moved)} Super Bowl game(s) to week 23: {moved}")
