"""Real NFL injuries -> Madden's Player injury fields (NFL_WEEKLY_MIRROR_PLAN.md §2.6). Pure: no DB, no save writes.

The mirror copies real injuries two ways:

- CATCH_UP weeks (D7: hindsight) use `weeks_until_back`, which reads the player's later roster status and injury
  report and returns the real number of weeks until he is next ACTIVE with no Out or Doubtful report, capped at the
  weeks left in the regular season.
- LIVE weeks (D12, Q1) copy only IR / PUP / NFI placements and activations, with an estimate: `estimate_weeks`
  gives the real IR minimum (4) for IR (including the R48 designated-for-return code), PUP and NFI; a short
  Out / Doubtful / Questionable estimate otherwise.

`injury_fields` builds the save's player edit (`InjuryStatus`, `InjuryType`, `InjurySeverity`, `MinInjuryDuration`,
`MaxInjuryDuration`) from those weeks and the nflverse body-part text. `HEAL_FIELDS` is the healthy edit.

The save's writer validates enum fields against fieldMeta (pipeline/save_reader/lib.mjs:150-193): it drops every
member whose name ends in `_`, so `Invalid_` and `Max_` can be read from a save but are never writable. A heal
therefore sets only `InjuryStatus`, MinInjuryDuration and MaxInjuryDuration (Madden ignores the stale diagnosis
while the player is Uninjured), and an injury with no writable type omits `InjuryType` entirely instead of writing
`Invalid_`, leaving whatever diagnosis the player had. The same rule applies through `enums=`: a chosen value the
save does not list, or a `_`-suffixed sentinel, omits the key.

The enum names are Spike B's real save values (plan §0 S17): InjuryStatus `Uninjured` / `Injured` (that save also
has `EarlyReturn` / `Undiagnosed`); InjurySeverity `Max_`, `SeasonEnding`, `GameEnding`, `CoupleGames`,
`SeveralGames`, `CoupleQtrs` plus the read-only `Invalid_`; InjuryType `Invalid_` plus the real body-part names
below. `injury_fields(..., enums=...)` accepts the save's own lists (field name -> values, or the short keys
`status` / `type` / `severity`) and only emits a writable value they contain; without them the S17 values are used.
"""

REGULAR_WEEKS = 18              # the CATCH_UP cap: weeks left in the regular season

UNINJURED = "Uninjured"         # pipeline/league_office.HEALTHY
INJURED = "Injured"
GENERIC_TYPE = "Invalid_"       # S17's injury type 0: read from a save, never written (the writer rejects it)
GENERIC_SEVERITY = "Invalid_"   # likewise read-only; every real severity band is writable

# S17's real save enums (InjuryType lists the real names the plan's survey printed).
ENUM_STATUS = ("Uninjured", "Injured")
ENUM_SEVERITY = ("Invalid_", "Max_", "SeasonEnding", "GameEnding", "CoupleGames", "SeveralGames", "CoupleQtrs")
ENUM_TYPE = (
    "Invalid_",
    "KneeACLCompleteTear", "AnkleDislocated", "BackRupturedDisk", "KneeKneecapFracture", "HandFingerBroken",
    "AnkleAchillesTear", "AnkleBroken", "RibPectoralTear", "KneeMCLCompleteTear", "HandWristDislocated",
    "LegQuadStrain", "KneePCLSprain", "LegGroinTear", "LegTibiaBroken", "AnkleHighSprain", "ArmTornTricep",
    "HipPointer", "KneeACLSprain", "ArmTornBicep", "HipFracture", "HandThumbBroken", "BackStrain",
    "ShoulderDislocation", "ShoulderRotatorCuffTear", "FootStressFracture", "HandBroken", "LegHamstringTear",
    "LegFibulaBroken", "FootFracture", "ElbowDislocate", "FootTurfToe", "KneeDislocated", "ShoulderStrain",
    "FootBrokenToe", "LegBrokenFemur", "ArmUpperFracture", "LegGroinPull", "HipDislocation", "ElbowFracture",
)
DEFAULT_ENUMS = {"InjuryStatus": ENUM_STATUS, "InjuryType": ENUM_TYPE, "InjurySeverity": ENUM_SEVERITY}

# nflverse report_primary_injury (body part text) -> Madden's real InjuryType. A body part with no honest
# counterpart (calf, concussion, illness, neck, ...) has no writable type, so injury_fields omits InjuryType
# rather than inventing a diagnosis (or writing the rejected `Invalid_` sentinel).
# The longest matching key wins, so "turf toe" beats "toe", "rotator cuff" beats "shoulder" and "achilles" beats
# "ankle".
BODY_PART_TO_TYPE = {
    "kneecap": "KneeKneecapFracture",
    "patella": "KneeKneecapFracture",
    "knee": "KneeACLSprain",
    "acl": "KneeACLSprain",
    "mcl": "KneeMCLCompleteTear",
    "pcl": "KneePCLSprain",
    "achilles": "AnkleAchillesTear",
    "high ankle": "AnkleHighSprain",
    "ankle": "AnkleHighSprain",
    "hamstring": "LegHamstringTear",
    "quad": "LegQuadStrain",
    "quadricep": "LegQuadStrain",
    "groin": "LegGroinPull",
    "tibia": "LegTibiaBroken",
    "fibula": "LegFibulaBroken",
    "femur": "LegBrokenFemur",
    "hip pointer": "HipPointer",
    "hip": "HipPointer",
    "back": "BackStrain",
    "disk": "BackRupturedDisk",
    "disc": "BackRupturedDisk",
    "shoulder": "ShoulderStrain",
    "rotator cuff": "ShoulderRotatorCuffTear",
    "hand": "HandBroken",
    "finger": "HandFingerBroken",
    "thumb": "HandThumbBroken",
    "wrist": "HandWristDislocated",
    "elbow": "ElbowDislocate",
    "biceps": "ArmTornBicep",
    "triceps": "ArmTornTricep",
    "arm": "ArmUpperFracture",
    "rib": "RibPectoralTear",
    "pectoral": "RibPectoralTear",
    "turf toe": "FootTurfToe",
    "toe": "FootBrokenToe",
    "stress fracture": "FootStressFracture",
    "heel": "FootStressFracture",
    "foot": "FootFracture",
}

# Madden severities for a duration. 4+ weeks is the IR threshold (pipeline/league_office.IR_MIN_WEEKS), so the
# incident reads as season-ending in the save; the exact weeks stay in Min/MaxInjuryDuration.
SEVERITY_BANDS = ((0, "CoupleQtrs"), (1, "CoupleGames"), (3, "SeveralGames"))

# pipeline/league_office.HEALTHY = ("Uninjured", None): a healthy player has no injury type. The writer rejects
# both `None` and the `_`-suffixed sentinel for the enum fields, and Madden ignores the stale diagnosis and
# severity while the player is Uninjured, so a heal leaves them alone.
HEAL_FIELDS = {
    "InjuryStatus": UNINJURED,
    "MinInjuryDuration": 0,
    "MaxInjuryDuration": 0,
}


# ---------- return dates (CATCH_UP) ----------

def weeks_until_back(gsis_id, week, later_weeks):
    """Weeks until `gsis_id` is next ACTIVE with no Out or Doubtful report, in CATCH_UP mode (D7).

    `later_weeks` is engines.nfl_mirror.real_state.later_weeks()'s shape:
    `{w: {gsis_id: {"status": RealPlayer.status | None, "report_status": str | None}}}`. A week the player is absent
    from (his team's bye, or a gap in the rows) is skipped, not treated as a return. The answer is capped at the
    weeks left in the regular season (`REGULAR_WEEKS - week`), which is also what a player who never returns gets.
    """
    week = int(week)
    remaining = max(0, REGULAR_WEEKS - week)
    if remaining == 0:
        return 0
    rows = []
    for key, by_player in (later_weeks or {}).items():
        try:
            w = int(key)
        except (TypeError, ValueError):
            continue
        if week < w <= REGULAR_WEEKS:
            rows.append((w, by_player or {}))
    for w, by_player in sorted(rows):
        entry = by_player.get(gsis_id) or {}
        if entry.get("status") != "ACTIVE":
            continue
        report = _norm(entry.get("report_status"))
        if report in ("OUT", "DOUBTFUL"):
            continue
        return w - week
    return remaining


# ---------- estimates (LIVE) ----------

IR_STATUSES = frozenset({"IR", "R48", "PUP", "NFI"})
IR_WEEKS = 4        # the real IR minimum, also used for PUP and NFI (plan §2.6)


def estimate_weeks(injury, status, history):
    """Weeks out for LIVE mode (D12/Q1): IR/R48/PUP/NFI 4; Out 1 (2 after a previous Out); Doubtful 1;
    Questionable 0.

    `injury` is a `real_state.RealInjury` (its `report_status` is read; None is accepted). `status` is the player's
    roster status (RealPlayer.status); an IR-family status wins over any weekly report. `history` is the player's
    earlier report statuses oldest first, so `history[-1]` is the previous report.
    """
    if _norm(status) in IR_STATUSES:
        return IR_WEEKS
    report = _norm(getattr(injury, "report_status", None))
    if report == "OUT":
        previous = _norm(history[-1]) if history else ""
        return 2 if previous == "OUT" else 1
    if report == "DOUBTFUL":
        return 1
    return 0


# ---------- save fields ----------

def injury_fields(weeks, body_part, enums=None):
    """The writable save player-edit fields for an injury of `weeks`: InjuryStatus, MinInjuryDuration and
    MaxInjuryDuration (both = weeks), plus InjuryType and InjurySeverity when a writable value exists.

    `body_part` is nflverse's report text, mapped through BODY_PART_TO_TYPE; a part with no honest mapping (or an
    empty one) omits InjuryType, so the player keeps the diagnosis he had instead of getting the unwritable
    `Invalid_`. `enums` optionally supplies the save's own enum values, as `{field: [values]}` or
    `{status|type|severity: [values]}`; a value the save doesn't list, or one ending in `_` (the writer drops those
    members), omits its key rather than writing something save_writer.mjs rejects.
    """
    weeks = max(0, int(weeks or 0))
    fields = {"MinInjuryDuration": weeks, "MaxInjuryDuration": weeks}
    status = _enum_value(enums, "InjuryStatus", "status", INJURED)
    if status:
        fields["InjuryStatus"] = status
    kind = injury_type(body_part)
    if _writable(kind, _values(enums, "InjuryType", "type")):
        fields["InjuryType"] = kind
    severity = severity_for(weeks)
    if _writable(severity, _values(enums, "InjurySeverity", "severity")):
        fields["InjurySeverity"] = severity
    return fields


def injury_type(body_part):
    """The real InjuryType for nflverse body-part text, else None (no writable type; the key is omitted)."""
    text = " ".join(str(body_part or "").lower().split())
    if not text:
        return None
    for key in sorted(BODY_PART_TO_TYPE, key=len, reverse=True):
        if key in text:
            return BODY_PART_TO_TYPE[key]
    return None


def severity_for(weeks):
    """Madden's severity band for a duration (see SEVERITY_BANDS): 0 CoupleQtrs, 1 CoupleGames, 2-3 SeveralGames,
    4+ SeasonEnding."""
    weeks = max(0, int(weeks or 0))
    for limit, severity in SEVERITY_BANDS:
        if weeks <= limit:
            return severity
    return "SeasonEnding"


# ---------- helpers ----------

def _norm(value):
    return str(value or "").strip().upper()


def _values(enums, field, short):
    """The save's list for one enum, from a field-name or short key; () when the caller supplied none."""
    enums = enums or {}
    for key in (field, short):
        value = enums.get(key)
        if isinstance(value, str):
            return (value,)
        if isinstance(value, dict):
            value = list(value.values())
        if isinstance(value, (list, tuple, set, frozenset)) and value:
            return tuple(value)
    return ()


def _writable(value, values):
    """True when `value` may be written: not the writer's dropped `_`-suffixed sentinel, and in the save's list
    when the caller supplied one."""
    if not value or str(value).endswith("_"):
        return False
    return not values or value in values


def _enum_value(enums, field, short, wanted):
    """`wanted` when it may be written, else None (the key is omitted)."""
    return wanted if _writable(wanted, _values(enums, field, short)) else None
