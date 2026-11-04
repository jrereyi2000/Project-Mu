"""Hidden prospect ratings from the save (docs/in_progress/SCOUTING_REVAMP_PLAN.md §2.2; PROJECT_SPEC §3.3c, §3.3g).

pipeline/load_save.py calls store_prospect_ratings on every load: it gives the coming class permanent prospect IDs
(engines/prospect_ids.ensure_prospect_ids) and upserts each Draft-status player's native ratings (the same
`ratings` dict load_save stores in player_attributes for rostered players) into scout_prospect_ratings, keyed by
prospect_uid. Prospect ratings don't change before the draft, so the upsert is idempotent.

scout_prospect_ratings is HIDDEN: the report engine reads it to set trait grades through noise; it is never shown in a
view, endpoint, writer tool or #debug panel.
"""
import json

from engines import prospect_ids
from engines.scouting_value import prospect_key


def _key(p, draft_season):
    from pipeline.load_save import position      # lazy: load_save imports this module
    return prospect_key(draft_season, f"{p['first_name']} {p['last_name']}".strip(), position(p["position"]))


def class_rows(snapshot, draft_season):
    """[{"prospect_key", "pgid"}] for the coming class: Draft-status players, limited to the save's draft_class when
    the snapshot has one (reader_version >= 5, the same set load_save.draft_board_prospects puts on the board)."""
    board = snapshot.get("draft_class")
    in_class = {d["pgid"] for d in board} if board else None
    return [{"prospect_key": _key(p, draft_season), "pgid": p["pgid"]}
            for p in snapshot.get("players") or []
            if p.get("status") == "Draft" and p.get("pgid") is not None
            and (in_class is None or p["pgid"] in in_class)]


def prospect_ratings(snapshot, draft_season):
    """Pure: [{"prospect_key", "pgid", "ratings"}] for every Draft-status player with non-empty ratings. Keys are built
    exactly as load_save.draft_board_prospects builds names and positions, so they match scout_prospects."""
    return [{"prospect_key": _key(p, draft_season), "pgid": p.get("pgid"), "ratings": p["ratings"]}
            for p in snapshot.get("players") or []
            if p.get("status") == "Draft" and p.get("ratings")]


def store_prospect_ratings(cur, snapshot, draft_season, capture_folder, report=None):
    """Allocates the class's permanent IDs, then upserts every rated prospect's ratings by prospect_uid, skipping
    KEY_COLLISION people (and anyone without an ID). Returns the number of rows written; 0 when the table doesn't
    exist yet. With `report`, the ID counts go to report["prospect_ids"] ({"allocated","renamed","collisions"})."""
    if not prospect_ids._table_exists(cur, "scout_prospect_ratings"):
        return 0
    ids = prospect_ids.ensure_prospect_ids(cur, draft_season, class_rows(snapshot, draft_season), capture_folder)
    if report is not None:
        report["prospect_ids"] = ids
    known = prospect_ids.class_ids(cur, draft_season)
    n = 0
    for row in prospect_ratings(snapshot, draft_season):
        rec = known.get(int(row["pgid"])) if row["pgid"] is not None else known.get(row["prospect_key"])
        if rec is None or rec[2] != prospect_ids.OK:
            continue
        cur.execute("INSERT INTO scout_prospect_ratings (prospect_uid, draft_season, prospect_key, pgid, ratings, "
                    "capture_folder) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (prospect_uid) DO UPDATE SET "
                    "ratings = EXCLUDED.ratings, prospect_key = EXCLUDED.prospect_key, pgid = EXCLUDED.pgid, "
                    "capture_folder = EXCLUDED.capture_folder, loaded_at = now()",
                    (rec[0], int(draft_season), rec[1], row["pgid"], json.dumps(row["ratings"]), capture_folder))
        n += 1
    return n
