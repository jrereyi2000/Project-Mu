"""Plan one mirrored week's roster moves (NFL_WEEKLY_MIRROR_PLAN.md §2.5). Pure apart from the committed
`data/history/nfl_link_overrides.csv` read in the new-link phase: everything else is supplied by the caller.

`plan_rosters(snapshot, real, links, trades, team_index, contracts, cap) -> MirrorRosterPlan` compares the save's
roster snapshot with the real week and plans, in this order:

1. new links — `engines.nfl_history.link.match` over the real players with no link and the save's players with no
   link (free agents included), so a man who joined the real league or moved can be found before anything else.
   `link.match` keeps its position safety; accepted `nfl_link_overrides.csv` rows are passed through here, so an
   accepted cross-position candidate links once and is no longer a candidate;
2. review candidates — a real player still unlinked after step 1 whose normalized name matches exactly one save
   player (linked or not) in a DIFFERENT position group, with agreeing entry season or real 2026 team evidence, is
   reported in `review`: both positions, the agreeing evidence and why it was not auto-linked. He is never linked
   across position groups and is not also planned as a `create`. Two or more same-name save players are reported as
   ambiguous with every candidate; nothing is created for that person;
3. CSV trades — a hand-kept `nfl_trades_2026.csv` leg (`from_team` to `to_team`, its week or any week) moves the
   linked player in one `trade` op only when the save has him on the leg's source team and the real week puts him
   on the leg's destination. The op carries the real list too, so a trade that also changes the list (a practice
   squad promotion or a demotion) is reconciled by the same write. A blank week is a wildcard for the week of the
   move, not a lifetime exemption: a stale leg falls through to the normal release/sign reconciliation, and a
   player already at the destination still has his list and IR state reconciled;
4. releases and same-team list changes — a linked player the real week does not place on his save team is released
   (a team change with no CSV leg is released and then signed; the report calls it an unconfirmed switch), while a
   linked player whose team is unchanged but whose real list differs gets one `list` op: the player stays with his
   team and the save writer moves him between its Roster and PracticeSquad (audit-5 U18 R3), with the contract and
   roster-count consequences of the promotion/demotion;
5. signings — a linked player the real week places on a team he is not on, signed to the Roster with an OTC or
   minimum contract (`contracts.ContractBook.contract_for`, for that team and the week's as-of date); then
   practice-squad signings;
6. IR on and IR off — `set_fields` ops for linked players whose `IsInjuredReserve` flag must follow the real status.

An unlinked real player is a `create` when the real week has him on a roster or IR list (D4: the active roster and
IR, not the practice squad), and a `ps_fill` slot when his only real place is a practice squad — the league office
fills that slot from `Deps.free_agent_pool` (best OVR at the position).

The plan's shapes:
  ops          [{"kind": "release"|"sign"|"trade"|"list"|"ir"|"activate", "pgid", "gsis_id", "to_index",
                 "list": "Roster"|"PracticeSquad", "contract": dict|None, "why"}]
  creates      [RealPlayer]
  ps_fill      [{"team_index", "position"}]
  unlinked_new [(gsis_id, pgid)]
  review       [{"status": "candidate"|"ambiguous", "gsis_id", "name", "position", "team_2026",
                 "candidates": [{"pgid", "name", "position", "team", "entry_season", "linked_gsis",
                                 "cross_position", "evidence"}],
                 "evidence": {...}, "why", plus "pgid" / "save_position" / "save_team" /
                 "save_entry_season" for a unique candidate}]
"""
import dataclasses
import datetime
import logging

from engines.nfl_history import link as link_engine

try:        # a snapshot's college is a reference into Madden's static college tables, not a name
    from pipeline.colleges import college_name
except Exception:       # pragma: no cover - lens without the scouting list
    def college_name(ref):
        return None

log = logging.getLogger(__name__)

FREE_AGENT_TEAM = 32       # pipeline.load_save.FREE_AGENT_TEAM_INDEX: snapshot free agents
ROSTER = "Roster"
PRACTICE_SQUAD = "PracticeSquad"
IR_STATUSES = frozenset({"IR", "PUP", "NFI"})

# The 2026 regular season opener (2026-09-09; personality/coach_profiles.py calls 2026-09-08 its eve). A mirrored
# week's contract as-of boundary is the last day of that real week: every OTC deal known by then may be used and a
# later-dated deal may not (the plan only mirrors 2026). RealWeek carries no date and is outside this unit, so the
# boundary is derived here; U6 can pass an exact week date once the real state carries one.
SEASON_OPENER = "2026-09-09"
WEEK_DAYS = 7


@dataclasses.dataclass
class MirrorRosterPlan:
    """One week's roster plan: what the league office writes, and what it must create or fill.

    `review` holds cross-position name matches that must not be auto-linked: a `candidate` is one real player whose
    normalized name uniquely matches a save player in a different position group with agreeing evidence (`gsis_id`,
    `position`, `pgid`, `save_position`, `evidence`, `why`); an `ambiguous` entry lists every same-name save player
    (`candidates`) and plans nothing for that person. U6 persists/reports it for JR's review.
    """
    ops: list = dataclasses.field(default_factory=list)
    creates: list = dataclasses.field(default_factory=list)
    ps_fill: list = dataclasses.field(default_factory=list)
    unlinked_new: list = dataclasses.field(default_factory=list)
    review: list = dataclasses.field(default_factory=list)


# ---------- snapshot shapes ----------

def _players(snapshot):
    return {int(p["pgid"]): p for p in (snapshot or {}).get("players") or [] if p.get("pgid") is not None}


def _codes(team_index):
    return {int(i): code for code, i in (team_index or {}).items()}


def _current(player):
    """{"team_index", "list", "ir"} for a save player on a roster, else None."""
    index = player.get("team_index")
    if index is None or int(index) >= FREE_AGENT_TEAM:
        return None
    status = player.get("status")
    if status == "Signed":
        lst = ROSTER
    elif status == "PracticeSquad":
        lst = PRACTICE_SQUAD
    else:
        return None
    return {"team_index": int(index), "list": lst,
            "ir": bool((player.get("injury") or {}).get("injured_reserve"))}


def _real_by_gsis(real):
    """{gsis_id: RealPlayer}, first team in team-code order (build_week already keeps one per player)."""
    out = {}
    for code in sorted((real.rosters or {})):
        for player in real.rosters[code]:
            out.setdefault(player.gsis_id, player)
    return out


def _placement(player):
    """(list, ir) the real week puts a player on: practice squad, IR flag, or the active roster."""
    if player.status == "PS":
        return PRACTICE_SQUAD, False
    if player.status in IR_STATUSES:
        return ROSTER, True
    return ROSTER, False


def _years_exp(player, season):
    return None if player.entry_year is None else max(0, int(season) - int(player.entry_year))


def _week_as_of(real):
    """'YYYY-MM-DD', the last day of `real`'s week: the boundary for OTC deals known by that mirror week.

    The last day of the week is the safe as-of: a deal dated during the week is known by its end, and one dated
    after the week belongs to the next week's data. None outside 2026, the only season the plan mirrors.
    """
    if int(real.season) != 2026:
        return None
    opener = datetime.date.fromisoformat(SEASON_OPENER)
    return (opener + datetime.timedelta(days=WEEK_DAYS * (int(real.week) - 1) + WEEK_DAYS - 1)).isoformat()


def _college(value):
    """A snapshot college (a {table, row} reference, or already a name) as a name the linker can compare."""
    if not value:
        return None
    if isinstance(value, str):          # a snapshot that already carries the name, or a test fixture
        return value
    return college_name(value)


# ---------- new links ----------

def _name_parts(player):
    """(first, last) for a save player, falling back to splitting `full_name`."""
    first, last = player.get("first_name") or "", player.get("last_name") or ""
    if not (first or last):
        first, _, last = (player.get("full_name") or "").partition(" ")
    return first, last


def _mu_player(pgid, player, codes, season):
    """A link.MuPlayer for a save player (the reader's field names)."""
    first, last = _name_parts(player)
    draft_year = None
    drafted = player.get("year_drafted")
    if isinstance(drafted, int):
        draft_year = drafted if drafted > 1900 else link_engine.DRAFT_YEAR_BASE + drafted
    index = player.get("team_index")
    espn = player.get("espn_id")
    return link_engine.MuPlayer(
        player_id=int(pgid), pgid=int(pgid), first_name=first, last_name=last,
        position=player.get("position"), team=codes.get(int(index)) if index is not None else None,
        years_of_experience=player.get("years_pro"), age=player.get("age"), overall=player.get("overall"),
        draft_year=draft_year, college=_college(player.get("college")), height_in=player.get("height_in"),
        weight=player.get("weight_lbs"), espn_id=str(espn) if espn else None, season=int(season))


def _real_person(player):
    """A link.RealPerson for a RealPlayer."""
    first, _, last = (player.name or "").partition(" ")
    return link_engine.RealPerson(
        gsis_id=player.gsis_id, display_name=player.name, first_name=first or None, last_name=last or None,
        position=player.position, rookie_season=player.entry_year,
        draft_year=player.entry_year if player.draft_number is not None else None, college=player.college,
        height_in=player.height_in, weight=player.weight_lbs, team_2026=player.team)


def _new_links(players, real_by_gsis, links, codes, season):
    """[(gsis_id, pgid)] from link.match over the unlinked real players and unlinked save players.

    Accepted `nfl_link_overrides.csv` rows are handed to `link.match`, so a candidate JR accepts is linked here once
    and never reaches the review stage. `link.match` is unmodified: it keeps its position safety for every
    non-override match.
    """
    linked_pgids = set(links.values())
    people = [_real_person(p) for gsis, p in sorted(real_by_gsis.items()) if gsis not in links]
    mu = [_mu_player(pgid, p, codes, season) for pgid, p in sorted(players.items())
          if pgid not in linked_pgids and p.get("status") not in (None, "Retired", "Draft")]
    if not people or not mu:
        return []
    result = link_engine.match(mu, people, link_engine.read_overrides())
    return sorted((item["gsis_id"], int(item["player_id"])) for item in result.links)


# ---------- review candidates ----------

def _normalized_name(player):
    first, last = _name_parts(player)
    return link_engine.normalize_name(f"{first} {last}".strip())


def _cross_position(save_position, real_position):
    """True only when both positions are known and their link groups do not overlap (link.position_ok)."""
    if not save_position or not real_position:
        return False
    return not link_engine.position_ok(save_position, real_position)


def _review_save(pgid, player, person, codes, season, gsis_by_pgid):
    """The reviewable facts about one same-name save player, including the agreeing entry/team evidence."""
    mu = _mu_player(pgid, player, codes, season)
    ev = link_engine.evidence(mu, person)
    return {
        "pgid": int(pgid),
        "name": mu.full_name,
        "position": player.get("position"),
        "team": mu.team,
        "entry_season": mu.entry_season,
        "linked_gsis": gsis_by_pgid.get(int(pgid)),
        "cross_position": _cross_position(player.get("position"), person.position),
        "evidence": {k: ev[k] for k in ("entry", "team") if ev.get(k)},
    }


def _review_candidates(players, real_by_gsis, links, codes, season):
    """(review entries, gsis ids to keep out of `creates`) for still-unlinked real players.

    Only a unique same-name save player in a different position group with agreeing entry season or 2026 team is a
    `candidate`; two or more same-name save players make an `ambiguous` entry listing every one of them. Nothing is
    ever linked from here.
    """
    by_name = {}
    for pgid, player in players.items():
        if player.get("status") in (None, "Retired", "Draft"):
            continue                     # save rows the new-link phase cannot link (and an override cannot accept)
        name = _normalized_name(player)
        if name:
            by_name.setdefault(name, []).append(int(pgid))
    gsis_by_pgid = {pgid: gsis for gsis, pgid in links.items()}
    review, reviewed = [], set()
    for gsis, real in sorted(real_by_gsis.items()):
        if gsis in links:
            continue                     # already linked: not a candidate
        name = link_engine.normalize_name(real.name)
        pgids = sorted(by_name.get(name, [])) if name else []
        if not pgids:
            continue                     # absent from the save: the normal create / ps_fill path
        person = _real_person(real)
        candidates = [_review_save(pgid, players[pgid], person, codes, season, gsis_by_pgid)
                      for pgid in pgids]
        entry = {"gsis_id": gsis, "name": real.name, "position": real.position, "team_2026": real.team}
        if len(candidates) > 1:
            entry.update(status="ambiguous", evidence={}, candidates=candidates,
                         why=f"{len(candidates)} save players share the normalized name {name!r}; "
                             "not auto-linked")
            review.append(entry)
            reviewed.add(gsis)
            continue
        candidate = candidates[0]
        if not candidate["cross_position"] or not candidate["evidence"]:
            continue                     # same position group, or evidence disagrees: a normal create
        why = (f"same normalized name in a different position group ({candidate['position']} in the save, "
               f"{real.position} in the real week); link.match requires agreeing position groups")
        if candidate["linked_gsis"]:
            why += f"; that save player is already linked to {candidate['linked_gsis']}"
        entry.update(status="candidate", pgid=candidate["pgid"], save_position=candidate["position"],
                     save_team=candidate["team"], save_entry_season=candidate["entry_season"],
                     evidence=candidate["evidence"], why=why, candidates=candidates)
        review.append(entry)
        reviewed.add(gsis)
    review.sort(key=lambda item: (item["name"], item["gsis_id"]))
    return review, reviewed


# ---------- the plan ----------

def plan_rosters(snapshot, real, links, trades, team_index, contracts, cap):
    """The week's MirrorRosterPlan (see the module docstring). Never mutates its arguments."""
    links = {str(g): int(p) for g, p in (links or {}).items()}
    players = _players(snapshot)
    codes = _codes(team_index)
    season = int(real.season)
    real_by_gsis = _real_by_gsis(real)
    plan = MirrorRosterPlan()

    # 1. new links first, so every later step works on the newest link table
    plan.unlinked_new = _new_links(players, real_by_gsis, links, codes, season)
    for gsis, pgid in plan.unlinked_new:
        links[gsis] = pgid
    gsis_by_pgid = {pgid: gsis for gsis, pgid in links.items()}

    # 2. review candidates: a same-name save player in another position group is reported, never auto-linked
    plan.review, reviewed_gsis = _review_candidates(players, real_by_gsis, links, codes, season)

    # where the real week puts each linked person
    desired = {}
    for gsis, player in real_by_gsis.items():
        index = (team_index or {}).get(player.team)
        if index is None:
            log.warning("real team %r of %s is not a save team; skipped", player.team, player.name)
            continue
        lst, ir = _placement(player)
        desired[gsis] = {"team_index": int(index), "list": lst, "ir": ir, "player": player}

    # where the save has each linked person now
    placed = {}      # gsis_id -> {"team_index", "list"}
    in_save_ir = {}
    for pgid, player in players.items():
        gsis = gsis_by_pgid.get(pgid)
        if gsis is None:
            continue
        current = _current(player)
        if current is None:
            continue
        placed[gsis] = {"team_index": current["team_index"], "list": current["list"]}
        in_save_ir[gsis] = current["ir"]

    signed_as_of = _week_as_of(real)

    # 3. CSV trades: the hand CSV is the authority, but only for the move the save actually has to make. A leg
    #    applies when the save has the player on its source team and the real week puts him on its destination;
    #    a blank week means "any week this move happened", not "exempt him from every later reconciliation".
    traded = set()
    for leg in trades or []:
        gsis = leg.gsis_id
        if not gsis or (leg.week is not None and int(leg.week) != int(real.week)):
            continue
        current, want = placed.get(gsis), desired.get(gsis)
        if current is None or want is None:
            continue          # no save placement or no real placement: the release/signing steps handle him
        from_index = (team_index or {}).get(leg.from_team)
        to_index = (team_index or {}).get(leg.to_team)
        if from_index is None or to_index is None:
            continue          # a leg naming a team the save does not have cannot describe its move
        from_index, to_index = int(from_index), int(to_index)
        if current["team_index"] != from_index or want["team_index"] != to_index:
            continue          # stale source or another destination: release + sign, reported as unconfirmed
        if current["team_index"] == to_index:
            continue          # already at the destination: reconcile his list and IR instead of marking a move
        contract = None
        if want["list"] == ROSTER and current["list"] == PRACTICE_SQUAD:
            # The trade also promotes him: the new Roster deal is the same one an ordinary signing would get.
            contract = contracts.contract_for(
                gsis, signed_as_of, cap, years_exp=_years_exp(want["player"], season), team=want["player"].team)
        plan.ops.append({"kind": "trade", "pgid": links[gsis], "gsis_id": gsis, "to_index": to_index,
                         "list": want["list"], "contract": contract,
                         "why": f"trade {leg.trade_id}".strip() + (f": {leg.note}" if leg.note else "")})
        placed[gsis] = {"team_index": to_index, "list": want["list"]}
        traded.add(gsis)

    # 4. releases and same-team list changes: a linked player the real week does not place on his save team and
    #    list. A team change is a release (a signing follows); the same team on another list is one list op.
    for gsis in sorted(placed):
        if gsis in traded:
            continue
        current, want = placed[gsis], desired.get(gsis)
        if want is None:
            reason = f"not on a real roster in week {real.week}"
        elif want["team_index"] != current["team_index"]:
            reason = (f"moved to {want['player'].team} with no CSV trade "
                      f"(release and sign; unconfirmed trade or claim)")
        elif want["list"] != current["list"]:
            # Direct list reconciliation: the player stays on his team, so the writer moves him between its Roster
            # and PracticeSquad instead of releasing him into free agency and refusing the same-team signing.
            contract = None
            if want["list"] == ROSTER:
                contract = contracts.contract_for(
                    gsis, signed_as_of, cap, years_exp=_years_exp(want["player"], season),
                    team=want["player"].team)
            plan.ops.append({"kind": "list", "pgid": links[gsis], "gsis_id": gsis,
                             "to_index": want["team_index"], "list": want["list"], "contract": contract,
                             "why": f"{current['list']} to {want['list']} on {want['player'].team}"})
            placed[gsis] = {"team_index": want["team_index"], "list": want["list"]}
            continue
        else:
            continue
        plan.ops.append({"kind": "release", "pgid": links[gsis], "gsis_id": gsis, "to_index": None,
                         "list": current["list"], "contract": None, "why": reason})
        del placed[gsis]

    # 5. signings: active roster first, then practice squad
    creates, ps_fill = [], []
    for target_list in (ROSTER, PRACTICE_SQUAD):
        for gsis in sorted(desired):
            want = desired[gsis]
            if want["list"] != target_list:
                continue
            have = placed.get(gsis)
            if have is not None:
                continue          # traded or still where the real week wants him
            pgid = links.get(gsis)
            if pgid is None:
                if target_list == ROSTER:
                    if gsis in reviewed_gsis:
                        continue      # a review candidate is not a missing person: never create a duplicate
                    creates.append(want["player"])
                else:
                    ps_fill.append({"team_index": want["team_index"], "position": want["player"].position})
                continue
            if pgid not in players:
                log.warning("%s (%s) is linked to pgid %s, which the snapshot does not have; not signed",
                            want["player"].name, gsis, pgid)
                continue
            if target_list == ROSTER:
                contract = contracts.contract_for(
                    gsis, signed_as_of, cap, years_exp=_years_exp(want["player"], season),
                    team=want["player"].team)
                plan.ops.append({"kind": "sign", "pgid": pgid, "gsis_id": gsis,
                                 "to_index": want["team_index"], "list": ROSTER, "contract": contract,
                                 "why": f"real {want['player'].status.lower()} for {want['player'].team}"})
            else:
                plan.ops.append({"kind": "sign", "pgid": pgid, "gsis_id": gsis,
                                 "to_index": want["team_index"], "list": PRACTICE_SQUAD, "contract": None,
                                 "why": f"real practice squad for {want['player'].team}"})
            placed[gsis] = {"team_index": want["team_index"], "list": target_list}

    plan.creates = sorted(creates, key=lambda p: (p.team, p.position, p.name, p.gsis_id))
    plan.ps_fill = sorted(ps_fill, key=lambda s: (s["team_index"], s["position"]))

    # 6. IR on and IR off, after every roster move
    for gsis in sorted(desired):
        want = desired[gsis]
        pgid = links.get(gsis)
        have = placed.get(gsis)
        if pgid is None or pgid not in players or have is None or have["team_index"] != want["team_index"]:
            continue
        if have["list"] != ROSTER:
            continue
        was_ir = in_save_ir.get(gsis, False)
        if want["ir"] and not was_ir:
            detail = f" ({want['player'].designation})" if want["player"].designation else ""
            plan.ops.append({"kind": "ir", "pgid": pgid, "gsis_id": gsis,
                             "to_index": want["team_index"], "list": ROSTER, "contract": None,
                             "why": f"real {want['player'].status.lower()}{detail}"})
        elif not want["ir"] and was_ir:
            plan.ops.append({"kind": "activate", "pgid": pgid, "gsis_id": gsis,
                             "to_index": want["team_index"], "list": ROSTER, "contract": None,
                             "why": "healthy on the real roster"})
    return plan
