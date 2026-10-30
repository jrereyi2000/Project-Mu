"""
Progression save pass (docs/in_progress/PROGRESSION_PLAN.md §3 and phase D; V2_CORE_BUILD_PLAN.md §2.4, U9).

Turns run_progression's result rows and a reader-v4 snapshot (pipeline/save_reader/pc_save_reader.mjs) into one
save_writer.mjs edits document:

  {"players": [{"pgid": N, "set": {"SpeedRating": 88, "OverallRating": 81, "OverallGrade0": 79, ...,
                                   "PlayerType": "WR_DeepThreat", "TraitDevelopment": "Star",
                                   "PT_COVERBALL": true, "ConfidenceRating": 50}}],
   "records": [{"table": "ProgressionXPSlider", "tableId": 4317, "row": 0, "set": {"QB": 0, ..., "LS": 0}}, ...]}

- Madden's own progression off (plan §3 step 1): all four slider records (progression / regression x by age / by
  position) set to 0, LS included (the menu doesn't show LS, and its regression slider left at 100 still ages long
  snappers); DefaultValue is left alone. Dev-trait regression off on the LeagueSetting row once spike R3 names its
  field (snapshot league_settings.dev_trait_regression_field; null today, so no edit and a warning).
- Project Mu's ratings (plan §3 step 2): every changed rating, plus OverallRating, OverallGrade0-4 and PlayerType
  recomputed from the new ratings with engines/ovr_weights.py (dual-write rule, V2_MASTER_PLAN V2.14), TraitDevelopment
  when the dev trait changes, and PT_* flags gained or lost (behaviour_changes).
- Morale pinned at 50 (D13): ConfidenceRating for every player whose value differs.

The builders are pure; only fetch_pgid_map and pass_edits touch a cursor. Nothing here writes a save:

  python3 -m engines.progression_save --snapshot <save.json> --season S [--out edits.json] [--dsn ...]
  node pipeline/save_reader/save_writer.mjs <CAREER-save> edits.json --out <new-save>
"""
import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, Iterable, List, Optional

from engines import ovr_weights as ow
from engines import progression

log = logging.getLogger("progression_save")

# snapshot league_settings.sliders key -> the save table the record lives in (pc_save_reader.mjs readLeagueSettings:
# the by-age sliders are int[] rows, the by-position ones ProgressionXPSlider rows).
SLIDER_TABLES = {
    "progression_by_age": "int[]",
    "progression_by_position": "ProgressionXPSlider",
    "regression_by_age": "int[]",
    "regression_by_position": "ProgressionXPSlider",
}
SLIDER_KEEP = ("DefaultValue",)
LEAGUE_SETTING_TABLE = "LeagueSetting"

# Our dev labels (player_seasons.dev_trait, progression.dev_label) -> the save's TraitDevelopment enum names.
DEV_TO_SAVE = {"Normal": "Normal", "Star": "Star", "Superstar": "Superstar", "X-Factor": "XFactor",
               "Superstar X-Factor": "XFactor", "XFactor": "XFactor", "Hidden": "Hidden"}
# The save's names -> stored tier (TraitDevelopment gives College_Impact/Star/Elite the values of Star/Superstar/XFactor).
SAVE_DEV_TIER = {"Normal": "Normal", "Star": "Star", "Superstar": "Superstar", "XFactor": "XFactor", "Hidden": "Hidden",
                 "College_Impact": "Star", "College_Star": "Superstar", "College_Elite": "XFactor"}

RATING_MIN, RATING_MAX = 0, 99
MORALE_NEUTRAL = 50


def _warn(warnings: Optional[List[str]], msg: str) -> None:
    log.warning(msg)
    if warnings is not None:
        warnings.append(msg)


# ------------------------------------------------------------------ builders ----
def slider_edits(snapshot: Dict[str, Any], warnings: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """`records` edits setting every value of the four slider records to 0 (LS included, DefaultValue untouched).
    A slider the snapshot has as null is skipped with a warning."""
    sliders = ((snapshot.get("league_settings") or {}).get("sliders")) or {}
    out = []
    for key, table in SLIDER_TABLES.items():
        s = sliders.get(key)
        if not s:
            _warn(warnings, f"slider {key} not in the snapshot; left as it is")
            continue
        values = {k: 0 for k in (s.get("values") or {}) if k not in SLIDER_KEEP}
        if not values:
            _warn(warnings, f"slider {key} has no values; left as it is")
            continue
        out.append({"table": table, "tableId": s["table_id"], "row": s["row"], "set": values})
    return out


def dev_flag_edit(snapshot: Dict[str, Any], warnings: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """A `records` edit turning dev-trait regression off on the LeagueSetting row, or None (with a warning) while the
    field isn't known (spike R3) or the snapshot has no LeagueSetting row."""
    ls = snapshot.get("league_settings") or {}
    field = ls.get("dev_trait_regression_field")
    setting = ls.get("league_setting")
    if not field:
        _warn(warnings, "dev_trait_regression_field unknown (spike R3): dev-trait regression not written; "
                        "turn it off once in Madden's league settings")
        return None
    if not setting:
        _warn(warnings, "no LeagueSetting row in the snapshot: dev-trait regression not written")
        return None
    return {"table": LEAGUE_SETTING_TABLE, "tableId": setting["table_id"], "row": setting.get("row", 0),
            "set": {field: False}}


def save_dev(label: Optional[str]) -> Optional[str]:
    """Our dev label -> the save's TraitDevelopment name (None when unknown)."""
    return DEV_TO_SAVE.get(label) if label else None


def ovr_fields(doc: Dict[str, Any], position: str, ratings: Dict[str, int], old_grades: List[int],
               current_type: Optional[str]) -> Dict[str, Any]:
    """OverallRating, OverallGrade0-4 and PlayerType from the ratings (engines/ovr_weights.py). A grade slot with no
    fitted archetype keeps its stored value. {} when the position has no fitted slots."""
    slots = doc.get("slots", {}).get(position)
    if not slots or not any(a in doc["archetypes"] for a in slots if a):
        return {}
    grades = list(old_grades or [])
    width = max(len(grades), len(slots))
    grades += [0] * (width - len(grades))
    out = {}
    for i in range(width):
        arch = slots[i] if i < len(slots) else None
        g = grades[i]
        if arch and arch in doc["archetypes"]:
            g = max(RATING_MIN, min(RATING_MAX, ow.predict_grade(doc, arch, ratings)))
        out[f"OverallGrade{i}"] = int(g)
    ovr, arch = ow.predict_ovr(doc, position, ratings, current=current_type)
    out["OverallRating"] = max(RATING_MIN, min(RATING_MAX, int(ovr)))
    out["PlayerType"] = arch
    return out


def _players_by_pgid(snapshot: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    return {p["pgid"]: p for p in snapshot.get("players") or [] if p.get("pgid") is not None}


def rating_edits(rows: Iterable[Dict[str, Any]], snapshot: Dict[str, Any],
                 pgid_by_player: Optional[Dict[int, int]] = None, doc: Optional[Dict[str, Any]] = None,
                 warnings: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """`players` edits from run_progression rows: each changed rating (<Name>Rating; the row's ratings_diff applied
    to the save's own values, clamped 0-99), with OverallRating, OverallGrade0-4 and PlayerType recomputed from the
    new ratings (dual-write), TraitDevelopment when dev_after differs from dev_before, and PT_* flags from
    behaviour_changes. A row's pgid comes from `pgid_by_player` ({player_id: pgid}) or the row's own `pgid`."""
    doc = doc or ow.load_weights()
    pgid_by_player = pgid_by_player or {}
    players = _players_by_pgid(snapshot)
    out = []
    for r in rows:
        pid = r.get("player_id")
        pgid = pgid_by_player.get(pid, r.get("pgid"))
        if pgid is None:
            _warn(warnings, f"player {pid}: no pgid; not written")
            continue
        p = players.get(pgid)
        if p is None:
            _warn(warnings, f"player {pid}: pgid {pgid} not in the snapshot; not written")
            continue
        current = dict(p.get("ratings") or {})
        new = dict(current)
        for name, d in (r.get("ratings_diff") or {}).items():
            if name not in current:
                _warn(warnings, f"pgid {pgid}: rating {name} not in the save; skipped")
                continue
            new[name] = max(RATING_MIN, min(RATING_MAX, int(current[name]) + int(d)))
        changed = {k: v for k, v in new.items() if v != current.get(k)}
        set_: Dict[str, Any] = {f"{k}Rating": v for k, v in sorted(changed.items())}
        if changed:
            ovr = ovr_fields(doc, p.get("position"), new, p.get("archetype_grades") or [], p.get("archetype"))
            if not ovr:
                _warn(warnings, f"pgid {pgid}: no OVR weights for position {p.get('position')}; OVR not rewritten")
            set_.update(ovr)
        before, after = r.get("dev_before"), r.get("dev_after")
        if after and after != before:
            dev = save_dev(after)
            if dev is None:
                _warn(warnings, f"pgid {pgid}: unknown dev trait {after!r}; not written")
            elif SAVE_DEV_TIER.get(p.get("dev_trait")) != dev:
                set_["TraitDevelopment"] = dev
        traits = p.get("traits") or {}
        bc = r.get("behaviour_changes") or {}
        for value, key in ((True, "gained"), (False, "lost")):
            for ch in bc.get(key) or []:
                flag = ch.get("flag") if isinstance(ch, dict) else ch
                if not flag:
                    continue
                if traits and flag not in traits:
                    _warn(warnings, f"pgid {pgid}: {flag} not a Player field in the save; skipped")
                    continue
                if traits.get(flag) is value:
                    continue
                set_[flag] = value
        if set_:
            out.append({"pgid": pgid, "set": set_})
    return out


def morale_edits(snapshot: Dict[str, Any], value: int = MORALE_NEUTRAL) -> List[Dict[str, Any]]:
    """`players` edits setting ConfidenceRating to `value` for every player whose confidence differs."""
    return [{"pgid": p["pgid"], "set": {"ConfidenceRating": value}}
            for p in snapshot.get("players") or []
            if p.get("pgid") is not None and p.get("confidence") is not None and p["confidence"] != value]


def merge_players(*edit_lists: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One players entry per pgid, sets merged in order (later lists win on a shared field)."""
    merged: Dict[int, Dict[str, Any]] = {}
    for edits in edit_lists:
        for e in edits:
            merged.setdefault(e["pgid"], {}).update(e["set"])
    return [{"pgid": pgid, "set": s} for pgid, s in merged.items() if s]


def build_document(rows: Iterable[Dict[str, Any]], snapshot: Dict[str, Any],
                   pgid_by_player: Optional[Dict[int, int]] = None, morale: Optional[int] = MORALE_NEUTRAL,
                   doc: Optional[Dict[str, Any]] = None, warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    """The offseason pass's edits document from progression rows (pure): ratings, morale, sliders, dev flag."""
    players = merge_players(rating_edits(rows, snapshot, pgid_by_player, doc, warnings),
                            morale_edits(snapshot, morale) if morale is not None else [])
    records = slider_edits(snapshot, warnings)
    flag = dev_flag_edit(snapshot, warnings)
    if flag:
        records.append(flag)
    return {"players": players, "records": records}


# ---------------------------------------------------------------------- DB ----
def fetch_pgid_map(cur) -> Dict[int, int]:
    """{players.id: madden_pgid} for every player loaded from a PC save."""
    cur.execute("SELECT id, madden_pgid FROM players WHERE madden_pgid IS NOT NULL")
    return {int(pid): int(pgid) for pid, pgid in cur.fetchall()}


def snapshot_traits(snapshot: Dict[str, Any], pgid_by_player: Dict[int, int]) -> Dict[int, Dict[str, bool]]:
    """{player_id: {PT_*: bool}} for run_progression's traits= from the snapshot."""
    players = _players_by_pgid(snapshot)
    return {pid: players[pgid]["traits"] for pid, pgid in pgid_by_player.items()
            if pgid in players and players[pgid].get("traits")}


def pass_edits(cur, snapshot: Dict[str, Any], season: int, pgid_by_player: Optional[Dict[int, int]] = None,
               morale: Optional[int] = MORALE_NEUTRAL, warnings: Optional[List[str]] = None,
               log_fn=None) -> Dict[str, Any]:
    """The whole offseason edits document: run_progression for `season` (dry: nothing stored, nothing committed) with
    the snapshot's PT_* traits, then rating, morale, slider and dev-flag edits, one players entry per pgid."""
    if pgid_by_player is None:
        pgid_by_player = fetch_pgid_map(cur)
    conn = getattr(cur, "connection", None)
    if conn is None:
        raise ValueError("pass_edits needs a cursor with .connection (run_progression opens its own cursors)")
    result = progression.run_progression(conn, season, commit=False, dry_run=True,
                                         log=log_fn or (lambda m: log.info(m)),
                                         traits=snapshot_traits(snapshot, pgid_by_player))
    return build_document(result["results"], snapshot, pgid_by_player, morale, warnings=warnings)


# --------------------------------------------------------------------- CLI ----
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build the progression save pass's save_writer edits document "
                                             "(never writes a save; save_writer.mjs does).")
    ap.add_argument("--snapshot", required=True, help="reader-v4 snapshot JSON (pc_save_reader.mjs)")
    ap.add_argument("--season", type=int, required=True, help="season progressing into")
    ap.add_argument("--out", help="edits JSON path (default: stdout)")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", progression.DSN))
    ap.add_argument("--no-morale", action="store_true", help="leave ConfidenceRating alone")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    with open(a.snapshot) as fh:
        snapshot = json.load(fh)
    rv = (snapshot.get("meta") or {}).get("reader_version")
    if rv is not None and rv < 4:
        log.error(f"snapshot reader_version {rv}: needs 4 (league_settings, confidence, traits)")
        return 2
    import psycopg
    conn = psycopg.connect(a.dsn)
    warnings: List[str] = []
    try:
        with conn.cursor() as cur:
            doc = pass_edits(cur, snapshot, a.season, morale=None if a.no_morale else MORALE_NEUTRAL,
                             warnings=warnings)
        conn.rollback()
    finally:
        conn.close()
    text = json.dumps(doc, indent=1)
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(text + "\n")
    else:
        print(text)
    fields = sum(len(e["set"]) for e in doc["players"])
    log.info(f"{len(doc['players'])} players ({fields} fields), {len(doc['records'])} records, "
             f"{len(warnings)} warnings" + (f" -> {a.out}" if a.out else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
