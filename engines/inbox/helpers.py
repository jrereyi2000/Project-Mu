"""Small cursor helpers the inbox kinds had each copied for themselves (chunk-1 audit cleanup).

Only the copies that were actually identical live here. `_player_name` stays per-kind: gm_talk reads `player_name`
and role_talk reads `COALESCE(full_name, player_name)` through `int`, and the two serve different screens.
"""


def regclass(cur, table):
    """True when `table` exists, so a kind can go quiet on a database without its migration."""
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (table,))
    row = cur.fetchone()
    return bool(row and row[0])


def as_int(value, default=None):
    """`value` as an int, or `default` when it isn't one (a typed ID that came in as text)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def abbr(cur, team_id, default=None):
    """The team's upper-cased abbreviation, or `default`."""
    cur.execute("SELECT abbreviation FROM teams WHERE id = %s", (team_id,))
    row = cur.fetchone()
    return str(row[0]).upper() if row and row[0] else default
