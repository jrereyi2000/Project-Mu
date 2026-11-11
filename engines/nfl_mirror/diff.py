"""Plan one mirrored week's roster moves (NFL_WEEKLY_MIRROR_PLAN.md §2.5). Pure: everything is supplied by the caller.

`plan_rosters(snapshot, real, links, trades, team_index, contracts, cap) -> MirrorRosterPlan` compares the save's
roster snapshot with the real week and plans, in this order:

1. new links — `engines.nfl_history.link.match` over the real players with no link and the save's players with no
   link (free agents included), so a man who joined the real league or moved can be found before anything else;
2. CSV trades — a hand-kept `nfl_trades_2026.csv` leg (`from_team` to `to_team`, its week or any week) moves the
   linked player in one `trade` op, and wins over inference;
3. releases — a linked player the real week does not place on his save team and list (a team change with no CSV leg
   is released and then signed; the report calls it an unconfirmed switch);
4. signings — a linked player the real week places on a team he is not on, signed to the Roster with an OTC or
   minimum contract (`contracts.ContractBook.contract_for`); then practice-squad signings;
5. IR on and IR off — `set_fields` ops for linked players whose `IsInjuredReserve` flag must follow the real status.

An unlinked real player is a `create` when the real week has him on a roster or IR list (D4: the active roster and
IR, not the practice squad), and a `ps_fill` slot when his only real place is a practice squad — the league office
fills that slot from `Deps.free_agent_pool` (best OVR at the position).

The plan's shapes:
  ops          [{"kind": "release"|"sign"|"trade"|"ir"|"activate", "pgid", "gsis_id", "to_index",
                 "list": "Roster"|"PracticeSquad", "contract": dict|None, "why"}]
  creates      [RealPlayer]
  ps_fill      [{"team_index", "position"}]
  unlinked_new [(gsis_id, pgid)]
"""
import dataclasses
import logging

from engines.nfl_history import link as link_engine

log = logging.getLogger(__name__)

FREE_AGENT_TEAM = 32       # pipeline.load_save.FREE_AGENT_TEAM_INDEX: snapshot free agents
ROSTER = "Roster"
PRACTICE_SQUAD = "PracticeSquad"
IR_STATUSES = frozenset({"IR", "PUP", "NFI"})


@dataclasses.dataclass
class MirrorRosterPlan:
    """One week's roster plan: what the league office writes, and what it must create or fill."""
    ops: list = dataclasses.field(default_factory=list)
    creates: list = dataclasses.field(default_factory=list)
    ps_fill: list = dataclasses.field(default_factory=list)
    unlinked_new: list = dataclasses.field(default_factory=list)


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


# ---------- new links ----------

def _mu_player(pgid, player, codes, season):
    """A link.MuPlayer for a save player (the reader's field names)."""
    first, last = player.get("first_name") or "", player.get("last_name") or ""
    if not (first or last):
        first, _, last = (player.get("full_name") or "").partition(" ")
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
        draft_year=draft_year, college=player.get("college"), height_in=player.get("height_in"),
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
    """[(gsis_id, pgid)] from link.match over the unlinked real players and unlinked save players."""
    linked_pgids = set(links.values())
    people = [_real_person(p) for gsis, p in sorted(real_by_gsis.items()) if gsis not in links]
    mu = [_mu_player(pgid, p, codes, season) for pgid, p in sorted(players.items())
          if pgid not in linked_pgids and p.get("status") not in (None, "Retired", "Draft")]
    if not people or not mu:
        return []
    result = link_engine.match(mu, people)
    return sorted((item["gsis_id"], int(item["player_id"])) for item in result.links)


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

    signed_dates = {leg.gsis_id: leg.date for leg in trades or [] if leg.gsis_id and leg.date}

    # 2. CSV trades: the hand CSV is the authority, so a leg beats release + sign
    traded = set()
    for leg in trades or []:
        gsis = leg.gsis_id
        if not gsis or (leg.week is not None and int(leg.week) != int(real.week)):
            continue
        if gsis not in placed:
            continue          # not in the save on a roster: the signings step places him
        want = desired.get(gsis)
        target_code = want["player"].team if want else leg.to_team
        to_index = (team_index or {}).get(target_code)
        if to_index is None:
            continue
        to_index = int(to_index)
        if placed[gsis]["team_index"] == to_index:
            traded.add(gsis)  # already there; no op, and no release either
            continue
        want_list = want["list"] if want else placed[gsis]["list"]
        plan.ops.append({"kind": "trade", "pgid": links[gsis], "gsis_id": gsis, "to_index": to_index,
                         "list": want_list, "contract": None,
                         "why": f"trade {leg.trade_id}".strip() + (f": {leg.note}" if leg.note else "")})
        placed[gsis] = {"team_index": to_index, "list": want_list}
        traded.add(gsis)

    # 3. releases: a linked player the real week does not place on his save team and list
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
            reason = f"{current['list']} to {want['list']} on {want['player'].team}"
        else:
            continue
        plan.ops.append({"kind": "release", "pgid": links[gsis], "gsis_id": gsis, "to_index": None,
                         "list": current["list"], "contract": None, "why": reason})
        del placed[gsis]

    # 4. signings: active roster first, then practice squad
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
                    gsis, signed_dates.get(gsis), cap, years_exp=_years_exp(want["player"], season))
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

    # 5. IR on and IR off, after every roster move
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
