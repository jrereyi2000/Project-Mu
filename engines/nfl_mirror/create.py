"""Create real players missing from the Madden save on spare free-agent records (NFL_WEEKLY_MIRROR_PLAN.md §2.8).

`pick_donors(snapshot, links, n, position)` supplies the spare pgids: free agents (status `FreeAgent`, never
`Draft`) with no link to a real person on a 2025-2026 roster, lowest OVR first, same position before the rest.

`created_edit(donor, real, ea_item, template)` builds one save_writer `players` entry through
`pipeline.madden_player_edit.player_edit`: the real name, age (from his birth date), height, weight, years pro and
jersey, with position and archetype from EA when EA has him (source `EA`), else from the role template (source
`TEMPLATE`).  EA's ratings are written as read.  The template is `role_template(snapshot, position, role)`: the
median Madden player at the position for a starter, the 25th percentile for a backup and the 10th for IR.  The
donor's pgid stays; the caller records the CREATED link with the returned `source` in its evidence.

The donor's appearance asset and portrait stay the donor's (Spike C, plan R4): a known cosmetic limitation, not
something this module can change.  College is a reference field the save writer cannot set through a Player edit,
so `real.college` is carried for the linker/evidence but not written.
"""
import datetime as dt
import logging

from engines.nfl_mirror import ea_ratings
from engines.nfl_mirror.real_state import RealPlayer
from pipeline import commentary_names as cn
from pipeline import madden_player_edit as mpe
from pipeline.load_save import height_text

log = logging.getLogger(__name__)

CREATION_SEASON = 2026
CREATION_DATE = dt.date(2026, 9, 1)      # age is counted on this date, so a creation is deterministic all season
ROLE_TEMPLATE = {"STARTER": 50, "BACKUP": 25, "IR": 10}   # role -> percentile of the position's Madden players

# nflverse position -> the save's Player.Position enum (the names in engines/ovr_weights_m27.json "slots").
REAL_TO_MADDEN = {
    "QB": "QB", "RB": "HB", "HB": "HB", "FB": "FB", "WR": "WR", "TE": "TE",
    "OT": "LT", "T": "LT", "LT": "LT", "G": "LG", "OG": "LG", "LG": "LG", "C": "C", "OC": "C",
    "IOL": "LG", "OL": "LT",
    "DE": "LE", "EDGE": "LE", "LE": "LE", "RE": "RE", "DT": "DT", "NT": "DT", "DL": "DT", "IDL": "DT",
    "OLB": "LOLB", "LOLB": "LOLB", "ILB": "MLB", "MLB": "MLB", "LB": "MLB",
    "SAM": "LOLB", "MIKE": "MLB", "WILL": "ROLB",
    "CB": "CB", "DB": "CB", "S": "FS", "FS": "FS", "SS": "SS", "SAF": "FS",
    "K": "K", "PK": "K", "P": "P", "LS": "LS",
}


# ----------------------------------------------------------------------------------------------- helpers ----
def _date(value):
    text = str(value or "").strip()[:10]
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def age_from_birth_date(birth_date, on=CREATION_DATE):
    """Full years on `on` (2026-09-01 by default), or None."""
    born = _date(birth_date)
    if born is None:
        return None
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def target_position(real, ea_item=None):
    """The save position for a real player: EA's own code when EA has him, else the nflverse position mapped."""
    if ea_item is not None:
        position = ea_ratings.ea_position(ea_item)
        if position:
            return position
    position = str(getattr(real, "position", None) or "").strip().upper()
    return REAL_TO_MADDEN.get(position, position)


def _save_field(name):
    return name if str(name).endswith("Rating") else f"{name}Rating"


def _percentile(values, pct):
    """The `pct` percentile of `values` by linear interpolation, as an int (median = 50)."""
    ordered = sorted(float(v) for v in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return int(round(ordered[0]))
    position = (len(ordered) - 1) * float(pct) / 100.0
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    value = ordered[low] + (ordered[high] - ordered[low]) * (position - low)
    return int(round(value))


def role_template(snapshot, position, role):
    """Ratings for a created player from the save's own players at `position`, at the role's percentile.

    `role` is STARTER (median), BACKUP (25th) or IR (10th), case-insensitive.  Draft prospects are not Madden
    players, so they are left out.  Returns None when the snapshot has no rated player at the position.
    """
    role_name = str(role or "").strip().upper()
    if role_name not in ROLE_TEMPLATE:
        raise ValueError(f"unknown role {role!r}; expected one of {', '.join(sorted(ROLE_TEMPLATE))}")
    pct = ROLE_TEMPLATE[role_name]
    position = str(position or "").strip().upper()
    players = [p for p in (snapshot or {}).get("players") or []
               if str(p.get("position") or "").strip().upper() == position
               and p.get("status") != "Draft" and isinstance(p.get("ratings"), dict) and p["ratings"]]
    if not players:
        return None

    by_rating = {}
    for player in players:
        for key, value in player["ratings"].items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                by_rating.setdefault(key, []).append(value)
    ratings = {_save_field(key): _percentile(values, pct) for key, values in sorted(by_rating.items())}
    overalls = [p["overall"] for p in players
                if isinstance(p.get("overall"), (int, float)) and not isinstance(p.get("overall"), bool)]
    if overalls:
        ratings["OverallRating"] = _percentile(overalls, pct)
    archetypes = [p["archetype"] for p in players if p.get("archetype")]
    archetype = max(sorted(set(archetypes)), key=archetypes.count) if archetypes else None
    return {"position": position, "archetype": archetype, "ratings": ratings}


# ----------------------------------------------------------------------------------------------- donors ----
def _blocked_pgids(links):
    """pgids that may not be used as donors: links to a real person with a 2025-2026 roster row.

    `links` may be `{gsis_id: pgid}` (store.links) or `{pgid: gsis_id}`; in the second form a None value marks a
    link whose person has no 2025-2026 roster row, which is eligible.  A set/list of pgids is accepted too.
    """
    if not links:
        return set()
    if isinstance(links, dict):
        gsis_keys = any(isinstance(key, str) and "-" in key for key in links)
        if gsis_keys:
            return {int(pgid) for pgid in links.values() if pgid is not None}
        return {int(pgid) for pgid, gsis in links.items() if gsis is not None}
    return {int(pgid) for pgid in links if pgid is not None}


def pick_donors(snapshot, links, n, position):
    """Up to `n` spare free-agent pgids to rewrite, lowest OVR first, same position before any other.

    A donor is a `FreeAgent` (never `Draft`, never on a roster) whose pgid is not blocked by `links` (see
    `_blocked_pgids`).  Players without a ratings dict are skipped because player_edit needs the donor's ratings.
    """
    wanted = max(0, int(n or 0))
    if not wanted:
        return []
    blocked = _blocked_pgids(links)
    position = str(position or "").strip().upper()
    same, other = [], []
    for player in (snapshot or {}).get("players") or []:
        pgid = player.get("pgid")
        if pgid is None or int(pgid) in blocked:
            continue
        if player.get("status") != "FreeAgent":
            continue
        if not isinstance(player.get("ratings"), dict) or not player["ratings"]:
            continue
        key = (player.get("overall") if isinstance(player.get("overall"), (int, float)) else 10 ** 6, int(pgid))
        if str(player.get("position") or "").strip().upper() == position:
            same.append((key, int(pgid)))
        else:
            other.append((key, int(pgid)))
    ordered = [pgid for _, pgid in sorted(same)] + [pgid for _, pgid in sorted(other)]
    return ordered[:wanted]


# ----------------------------------------------------------------------------------------------- creation ----
def _commentary_id(name):
    try:
        return cn.commentary_id(name)
    except FileNotFoundError:
        log.warning("commentary lookup is missing; %s gets commentary id 0", name)
        return 0


def created_edit(donor, real: RealPlayer, ea_item, template):
    """One save_writer `players` entry for a real player created on `donor` (plan §2.8).

    The returned dict is itself the players entry (`pgid`, `set`); `source` (`EA` or `TEMPLATE`),
    `donor_pgid`, `position`, `archetype`, `notes` and `unmapped_ea_keys` are extra keys for the caller's report
    and for `store.add_created_link` evidence (the writer ignores them).
    """
    if ea_item is not None:
        fields, source = ea_ratings.madden_fields(ea_item), "EA"
    elif template:
        fields, source = dict(template), "TEMPLATE"
    else:
        raise ValueError(f"created player {getattr(real, 'name', None)!r} has no EA item and no role template")

    ratings = {field: int(value) for field, value in (fields.get("ratings") or {}).items()
               if isinstance(value, (int, float)) and not isinstance(value, bool)}
    if not ratings:
        raise ValueError(f"created player {getattr(real, 'name', None)!r} has no ratings to write")

    position = target_position(real, ea_item)
    entry_years = getattr(real, "entry_year", None)
    target = {
        "full_name": real.name,
        "position": position,
        "archetype": fields.get("archetype"),
        "age": age_from_birth_date(getattr(real, "birth_date", None)),
        "years_pro": max(0, CREATION_SEASON - int(entry_years)) if entry_years is not None else None,
        "jersey": getattr(real, "jersey", None),
        "height": height_text(getattr(real, "height_in", None)),
        "weight": getattr(real, "weight_lbs", None),
        "college": getattr(real, "college", None),      # for evidence only: College is a reference field
    }
    edit, notes = mpe.player_edit(donor, target, new_name=real.name, commentary_id=_commentary_id(real.name))
    set_ = dict(edit["set"])
    set_.update(ratings)
    pgid = int(donor["pgid"])
    return {
        "pgid": pgid,
        "set": set_,
        "source": source,
        "donor_pgid": pgid,
        "position": position,
        "archetype": set_.get("PlayerType") or fields.get("archetype") or donor.get("archetype"),
        "notes": list(notes),
        "unmapped_ea_keys": list(fields.get("unmapped") or []),
    }
