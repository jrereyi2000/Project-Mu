"""Permanent prospect IDs (docs/in_progress/SCOUTING_REVAMP_PLAN.md §2.1b; PROJECT_SPEC §3.3g).

Every prospect gets one `prospect_uid` that never changes: uuid5(MU_PROSPECT_NS, birth_key), with birth_key
`save-pgid:<draft_season>:<pgid>` for a class read from the PC save, else `board:<draft_season>:<prospect_key at first
sight>` for a scanned (PS5) class. It is written once into scout_prospect_ids and never recomputed: a rename, a
position-group change or a pgid move keeps it. `prospect_key` (engines/scouting_value.prospect_key) is only the
current alias into the older scouting tables; old aliases go to key_history.

The personality lifecycle plan adopts the same value as its `source_person_id` (or, if it must allocate a different
UUID, writes it to scout_prospect_ids.source_person_id); `person_ref` then returns that ID.

Two prospects of one class sharing a prospect_key are both marked KEY_COLLISION: they get no report or ratings and
can't be interviewed until the older scouting tables stop keying on names.
"""
import json
import uuid

MU_PROSPECT_NS = uuid.UUID("25fa50e6-e193-51cf-a790-84a6aad3fd01")   # fixed forever: uuid5(NAMESPACE_URL, project-mu prospect)

OK, KEY_COLLISION = "OK", "KEY_COLLISION"


def birth_key(draft_season, pgid=None, prospect_key=None):
    """The person's birth key: by pgid for a save class, else by the prospect_key first seen (scanned class)."""
    if pgid is not None:
        return f"save-pgid:{int(draft_season)}:{int(pgid)}"
    if not prospect_key:
        raise ValueError("a scanned prospect needs its prospect_key")
    return f"board:{int(draft_season)}:{prospect_key}"


def uid_from_birth_key(key):
    return uuid.uuid5(MU_PROSPECT_NS, key)


def _table_exists(cur, name):
    cur.execute(f"SELECT to_regclass('{name}') IS NOT NULL")
    row = cur.fetchone()
    return bool(row and row[0])


def _history(value):
    if value is None:
        return []
    if isinstance(value, str):
        return json.loads(value or "[]")
    return list(value)


def ensure_prospect_ids(cur, draft_season, rows, capture_folder):
    """Allocates or matches a permanent ID for every row of a class's board load.

    rows: [{"prospect_key", "pgid"}] (pgid None for a scanned class). A save row matches its person by pgid and moves
    the alias when the prospect_key changed (the old one goes to key_history with until_capture = capture_folder); a
    scanned row matches by prospect_key; anything unmatched is allocated. Rows sharing a prospect_key are marked
    KEY_COLLISION (and set back to OK once the key is unique again). Idempotent: re-running the same rows changes
    nothing. Returns {"allocated", "renamed", "collisions"}; all 0 when the table doesn't exist yet."""
    out = {"allocated": 0, "renamed": 0, "collisions": 0}
    if not rows or not _table_exists(cur, "scout_prospect_ids"):
        return out
    draft_season = int(draft_season)
    counts = {}
    for r in rows:
        counts[r["prospect_key"]] = counts.get(r["prospect_key"], 0) + 1
    out["collisions"] = sum(1 for r in rows if counts[r["prospect_key"]] > 1)

    cur.execute("SELECT prospect_uid, pgid, prospect_key, key_history, status FROM scout_prospect_ids "
                "WHERE draft_season = %s", (draft_season,))
    by_pgid, by_key = {}, {}
    for uid, pgid, key, hist, status in cur.fetchall():
        rec = {"uid": str(uid), "key": key, "history": _history(hist), "status": status}
        if pgid is not None:
            by_pgid[int(pgid)] = rec
        else:
            by_key[key] = rec

    for r in rows:
        key, pgid = r["prospect_key"], r.get("pgid")
        pgid = int(pgid) if pgid is not None else None
        status = KEY_COLLISION if counts[key] > 1 else OK
        rec = by_pgid.get(pgid) if pgid is not None else by_key.get(key)
        if rec is None:
            bkey = birth_key(draft_season, pgid, key)
            uid = str(uid_from_birth_key(bkey))
            cur.execute("INSERT INTO scout_prospect_ids (prospect_uid, birth_key, draft_season, pgid, prospect_key, "
                        "status, first_seen) VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (birth_key) DO NOTHING",
                        (uid, bkey, draft_season, pgid, key, status, capture_folder))
            rec = {"uid": uid, "key": key, "history": [], "status": status}
            if pgid is not None:
                by_pgid[pgid] = rec
            else:
                by_key[key] = rec
            out["allocated"] += 1
            continue
        if rec["key"] != key:                       # renamed or moved position group: same person, new alias
            rec["history"].append({"prospect_key": rec["key"], "until_capture": capture_folder})
            rec["key"], rec["status"] = key, status
            cur.execute("UPDATE scout_prospect_ids SET prospect_key = %s, key_history = %s, status = %s "
                        "WHERE prospect_uid = %s", (key, json.dumps(rec["history"]), status, rec["uid"]))
            out["renamed"] += 1
        elif rec["status"] != status:
            rec["status"] = status
            cur.execute("UPDATE scout_prospect_ids SET status = %s WHERE prospect_uid = %s", (status, rec["uid"]))
    return out


def class_ids(cur, draft_season):
    """{pgid or prospect_key: (prospect_uid, prospect_key, status)} for a class: save people by pgid, scanned people
    by their key. {} without the table."""
    if not _table_exists(cur, "scout_prospect_ids"):
        return {}
    cur.execute("SELECT prospect_uid, pgid, prospect_key, status FROM scout_prospect_ids WHERE draft_season = %s",
                (int(draft_season),))
    return {(int(pgid) if pgid is not None else key): (str(uid), key, status)
            for uid, pgid, key, status in cur.fetchall()}


def uid_for(cur, draft_season, prospect_key):
    """The permanent ID behind a current prospect_key alias, or None (unknown, or a KEY_COLLISION)."""
    if not _table_exists(cur, "scout_prospect_ids"):
        return None
    cur.execute("SELECT prospect_uid FROM scout_prospect_ids WHERE draft_season = %s AND prospect_key = %s "
                "AND status = 'OK' ORDER BY created_at DESC LIMIT 1", (int(draft_season), prospect_key))
    row = cur.fetchone()
    return uuid.UUID(str(row[0])) if row else None


def person_ref(cur, prospect_uid):
    """The ID other systems store for this person: the lifecycle plan's source_person_id once it has set one, else
    the prospect_uid (both as text)."""
    cur.execute("SELECT source_person_id FROM scout_prospect_ids WHERE prospect_uid = %s", (str(prospect_uid),))
    row = cur.fetchone()
    return str(row[0]) if row and row[0] else str(prospect_uid)
