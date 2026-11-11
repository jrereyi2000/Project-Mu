"""Depth charts: the real NFL chart, then the engine filling the rest (NFL_WEEKLY_MIRROR_PLAN.md §2.6). Pure.

`real_chart(real_depth, links, madden_roster)` turns nflverse's per-position depth order (RealWeek.depth[team]:
`{pos_abb: [gsis_id]}`) into Madden pgids on the base depth slots (QB, HB, FB, WR, TE, the line, LE/RE/DT, the
linebackers, CB/FS/SS, K, P). A player who is unlinked, hurt or on IR is dropped. Return and situational slots
(KR/PR, 3DRB, PWHB, GAD, SLWR, NT, RLE/RRE/RDT, SUBLB, SLCB, KOS, LS) are left empty for the engine.

`merge(real, engine)` keeps the real starters and depth order and fills every empty slot, and the tail of every
partly filled slot, from the engine's `depth_chart_engine.staff_depth_chart` result (`{slot: [player_id, ...]}`),
up to Madden's 6-deep DepthChart. The league office writes the merged chart as the mirrored week's MU_COACH chart.
"""

DEPTH = 6       # Madden's Team.DepthChart depth (pipeline/save_reader/depth_chart.mjs; depth_chart_engine.DEPTH)

# Madden's 35 depth-chart positions, grouped as the plan lists them. The last block is left to the engine.
DEPTH_SLOTS = [
    "QB", "HB", "FB", "WR", "TE", "LT", "LG", "C", "RG", "RT",
    "LE", "RE", "DT", "LOLB", "MLB", "ROLB", "CB", "FS", "SS",
    "K", "P", "LS",
    "3DRB", "GAD", "KOS", "KR", "NT", "PR", "PWHB", "RDT", "RLE", "RRE", "SLCB", "SLWR", "SUBLB",
]

# Slots real_chart fills, with the nflverse pos_abb values that feed each one, in the order they are interleaved.
# nflverse's real depth_charts use specific codes (LCB/RCB, LDE/RDE, LILB/RILB, ...); the shorter names are kept
# for fixtures and older files. Generic OT/OG/S/OLB/DB feed only one slot rather than duplicating the same list.
SOURCE_ORDER = {
    "QB": ("QB",),
    "HB": ("HB", "RB"),
    "FB": ("FB",),
    "WR": ("WR",),
    "TE": ("TE",),
    "LT": ("LT", "OT"),
    "LG": ("LG", "OG"),
    "C": ("C",),
    "RG": ("RG",),
    "RT": ("RT",),
    "LE": ("LE", "LDE", "DE", "EDGE"),
    "RE": ("RE", "RDE", "DE", "EDGE"),
    "DT": ("DT", "NT", "LDT", "RDT"),
    "LOLB": ("LOLB", "OLB", "LB"),
    "MLB": ("MLB", "ILB", "LILB", "RILB", "LB"),
    "ROLB": ("ROLB", "OLB", "LB"),
    "CB": ("CB", "LCB", "RCB", "NB", "DB"),
    "FS": ("FS", "S", "DB"),
    "SS": ("SS", "S"),
    "K": ("K", "PK"),
    "P": ("P",),
}
REAL_SLOTS = tuple(SOURCE_ORDER)        # the 21 slots real_chart can fill

HEALTHY_STATUSES = (None, "", "Uninjured")     # pipeline/league_office.HEALTHY


def real_chart(real_depth, links, madden_roster):
    """{slot: [pgid, ...]} from nflverse's depth order, dropping unlinked, injured and IR players.

    `real_depth`: `{pos_abb: [gsis_id, ...]}` best first (RealWeek.depth[team]).
    `links`: `{gsis_id: pgid}` (store.links).
    `madden_roster`: the save's players as a `{pgid: player}` mapping or a list of player dicts (the snapshot's
    `players` shape: `pgid` and `injury: {"status", "injured_reserve"}`). A player it doesn't list has no injury
    data and is kept; only the three plan criteria (unlinked, hurt, IR) drop a player.
    """
    rows = _roster_rows(madden_roster)
    out = {}
    for slot in REAL_SLOTS:
        ids, seen = [], set()
        for gsis_id in _interleave([real_depth.get(pos) or [] for pos in SOURCE_ORDER[slot]]):
            pgid = (links or {}).get(gsis_id)
            if pgid is None or pgid in seen:
                continue
            if _out(rows.get(pgid, rows.get(str(pgid)))):
                continue
            seen.add(pgid)
            ids.append(pgid)
        if ids:
            out[slot] = ids[:DEPTH]
    return out


def merge(real, engine):
    """The real chart with every empty slot and every tail filled from the engine's chart, up to DEPTH players.

    `real`: `{slot: [pgid, ...]}` (real_chart). `engine`: `{slot: [pgid, ...]}` (staff_depth_chart). Real players
    keep their order; engine players fill behind them, skipping duplicates. Every DEPTH_SLOTS key is present in the
    result, plus any extra slot the engine returns.
    """
    real, engine = real or {}, engine or {}
    out = {}
    for slot in list(DEPTH_SLOTS) + sorted(set(engine) - set(DEPTH_SLOTS)):
        ids = list(dict.fromkeys(real.get(slot) or []))
        for pgid in engine.get(slot) or []:
            if len(ids) >= DEPTH:
                break
            if pgid not in ids:
                ids.append(pgid)
        out[slot] = ids[:DEPTH]
    return out


# ---------- helpers ----------

def _interleave(lists):
    """Round-robin merge by rank: LCB 1, RCB 1, LCB 2, RCB 2, ... so starters come before backups."""
    out, seen = [], set()
    for i in range(max((len(x) for x in lists), default=0)):
        for values in lists:
            if i < len(values) and values[i] not in seen:
                seen.add(values[i])
                out.append(values[i])
    return out


def _roster_rows(madden_roster):
    """{pgid: player dict} from a mapping or a list of snapshot players (other entries become {})."""
    if isinstance(madden_roster, dict):
        return {k: (v if isinstance(v, dict) else {}) for k, v in madden_roster.items()}
    out = {}
    for player in madden_roster or []:
        if isinstance(player, dict):
            pgid = player.get("pgid", player.get("player_id"))
            if pgid is not None:
                out[pgid] = player
        else:
            out[player] = {}
    return out


def _out(row):
    """True when the save player is injured or on IR (a missing row is not hurt).

    Two shapes are read: a snapshot player (`injury: {"status", "injured_reserve"}`) and the save field names at
    the top level. In the latter, `status` is the roster status ("Signed" / "FreeAgent" / "PracticeSquad"), so
    `InjuryStatus` wins whenever the row carries it.
    """
    if not isinstance(row, dict):
        return False
    injury = row.get("injury")
    if not isinstance(injury, dict):
        injury = row        # the save field names at the top level
    if injury.get("out"):
        return True
    status = injury["InjuryStatus"] if "InjuryStatus" in injury else injury.get("status")
    ir = injury.get("injured_reserve", injury.get("IsInjuredReserve"))
    return status not in HEALTHY_STATUSES or bool(ir)
