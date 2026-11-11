"""The NFL Weekly Mirror's league office adapter (NFL_WEEKLY_MIRROR_PLAN.md §2.9, U6).

`mirror_week(cur, snapshot, season, stage, week)` is the one call `pipeline.league_office.plan_stage` makes when the
mirror is on: it fetches the real week (`engines.nfl_mirror.fetch`), builds it (`real_state.build_week`), diffs the
save's roster against it (`diff.plan_rosters`, with the store's links and the hand-kept trades CSV), estimates
injuries (`injuries.weeks_until_back` in CATCH_UP, `estimate_weeks` in LIVE), plans the created players
(`ea_ratings` / `create`) and the coaching changes (`staff`), and records the week and its PLANNED moves through
`engines.nfl_mirror.store`. It returns a `MirrorWeek`, or None when the mirror is not active for this week.

`MirrorWeek` is everything `pipeline.league_office.step_nfl_mirror` needs; it carries the `cur` and the store module
so the step can write the CREATED link and the moves without importing the database layer itself. `default_deps`
wires the adapter onto the personality deps (`install`), and `chart_override(team_index)` gives `step_coach_charts`
the real chart merged with the engine's. Everything the adapter calls is injectable, so tests use fakes: no network
and no database.

A fetch failure is a plan error (the caller runs the adapter through `plan.step`, which turns the exception into a
plan error and writes nothing), in LIVE mode and CATCH_UP alike: a mirrored week must never be planned from stale
files without saying so.
"""
import collections
import dataclasses
import logging

from engines.nfl_history.teams import UnknownTeam, mu_abbr
from engines.nfl_mirror import contracts as mirror_contracts
from engines.nfl_mirror import create as mirror_create
from engines.nfl_mirror import depth as mirror_depth
from engines.nfl_mirror import diff as mirror_diff
from engines.nfl_mirror import ea_ratings
from engines.nfl_mirror import fetch as mirror_fetch
from engines.nfl_mirror import injuries as mirror_injuries
from engines.nfl_mirror import real_state
from engines.nfl_mirror import staff as mirror_staff
from engines.nfl_mirror import store as mirror_store
from engines.nfl_mirror import trades as mirror_trades
from pipeline import load_save as ls

log = logging.getLogger(__name__)

CATCH_UP = real_state.CATCH_UP
LIVE = real_state.LIVE
ROSTER = "Roster"
PRACTICE_SQUAD = "PracticeSquad"
HEALTHY = ("Uninjured", None)
IR_STATUSES = mirror_injuries.IR_STATUSES
REPORT_STATUSES = ("OUT", "DOUBTFUL")      # a Questionable report plays (D7/D12)
CREATION_EVIDENCE = "NFL weekly mirror created this player"
LINK_METHODS = ("EXACT", "ESPN", "ALIAS", "FUZZY", "OVERRIDE")


@dataclasses.dataclass
class MirrorWeek:
    """One mirrored week: what the league office writes, and what it records about the week."""
    cur: object = None
    store: object = mirror_store
    season: int = 2026
    week: int = 0                       # Mu week
    real_week: int = 0                  # nflverse week
    stage: str = ""
    mode: str = CATCH_UP
    real: object = None                 # RealWeek
    roster: object = None               # diff.MirrorRosterPlan
    links: dict = dataclasses.field(default_factory=dict)      # gsis_id -> pgid (new links included)
    team_index: dict = dataclasses.field(default_factory=dict)  # save code -> team index
    codes: dict = dataclasses.field(default_factory=dict)       # team index -> save code
    contracts: object = None            # contracts.ContractBook
    cap: int = 0                        # league cap dollars for this week's contracts
    creates: list = dataclasses.field(default_factory=list)     # created-player dicts (see _creates)
    ops: list = dataclasses.field(default_factory=list)         # diff.plan_rosters ops
    ps_fill: list = dataclasses.field(default_factory=list)     # [{"team_index", "position"}] (the diff's slots)
    ps_signs: list = dataclasses.field(default_factory=list)    # the same slots resolved to a free agent pgid
    injuries: list = dataclasses.field(default_factory=list)     # INJURY / HEAL dicts
    injury_players: set = dataclasses.field(default_factory=set)  # pgids the mirror decides injury fields for
    review: list = dataclasses.field(default_factory=list)      # diff.plan_rosters review (U12)
    link_records: list = dataclasses.field(default_factory=list)  # ordinary new links: player_id/gsis/method/evidence
    staff_changes: list = dataclasses.field(default_factory=list)
    staff_moves: list = dataclasses.field(default_factory=list)
    staff_changed: bool = False
    setting: dict | None = None         # {"state": "ON"|"OFF", "edit": record}
    sources: dict = dataclasses.field(default_factory=dict)
    counts: dict = dataclasses.field(default_factory=dict)
    report: str = ""
    moves: list = dataclasses.field(default_factory=list)       # the PLANNED move dicts recorded

    def summary(self):
        """The small, JSON-safe part a league office run's summary stores (review included, U12 shape)."""
        return {"season": self.season, "week": self.week, "real_week": self.real_week, "mode": self.mode,
                "counts": dict(self.counts), "review": list(self.review), "report": self.report}

    def record(self):
        """`summary` plus what `record_written` needs: the links to persist, the move rows and their outcomes.

        A created player's CREATED link is appended by `step_nfl_mirror` only after his signing is accepted, so a
        refused creation never leaves a link or an edit behind (audit-4 U6 F2). Ordinary new links (`unlinked_new`)
        are the linker's own decisions and are persisted with their method and evidence (F4)."""
        out = self.summary()
        out["real_week"] = self.real_week
        out["sources"] = dict(self.sources)
        out["transactions"] = []
        out["links"] = [dict(link) for link in self.link_records]
        out["moves"] = [dict(move) for move in self.moves]
        out["outcomes"] = {}
        return out


class MirrorAdapter:
    """The real adapter; every collaborator is a constructor argument so tests run offline."""

    def __init__(self, *, store=mirror_store, fetch=mirror_fetch.fetch_week, read_rows=mirror_fetch.read_rows,
                 sources=mirror_fetch.sources, build=real_state.build_week, later=real_state.later_weeks,
                 head_coaches=real_state.head_coaches, plan_rosters=mirror_diff.plan_rosters,
                 read_trades=mirror_trades.read_trades,
                 contract_book=mirror_contracts.ContractBook.from_otc,
                 read_staff=mirror_staff.read_staff_csv, real_staff=mirror_staff.real_staff_changes,
                 apply_staff=mirror_staff.apply_staff_changes, ea_iteration=ea_ratings.fetch_iteration,
                 ea_items=ea_ratings.read_items, ea_index=ea_ratings.index, ea_find=ea_ratings.find,
                 match_links=None, donor_links=None, team_codes=None, team_ids=None, user_team_ids=None,
                 pid_by_pgid=None, free_pool=None, cap=None, refresh=True):
        self.store = store
        self.fetch = fetch
        self.read_rows = read_rows
        self.sources = sources
        self.build = build
        self.later = later
        self.head_coaches = head_coaches
        self.plan_rosters = plan_rosters
        self.read_trades = read_trades
        self.contract_book = contract_book
        self.read_staff = read_staff
        self.real_staff = real_staff
        self.apply_staff = apply_staff
        self.ea_iteration = ea_iteration
        self.ea_items = ea_items
        self.ea_index = ea_index
        self.ea_find = ea_find
        self.match_links = match_links or _match_links
        self.donor_links = donor_links or _donor_links
        self.team_codes = team_codes or _team_codes
        self.team_ids = team_ids or _team_ids
        self.user_team_ids = user_team_ids or _user_team_ids
        self.pid_by_pgid = pid_by_pgid or _pid_by_pgid
        self.free_pool = free_pool or _free_pool
        self.cap = cap or _league_cap
        self.refresh = refresh
        self._mirror = None
        self._plan = None

    # -- the two hooks plan_stage calls ---------------------------------------------------------------
    def mirror_week(self, cur, snapshot, season, stage, week):
        """A MirrorWeek when the settings row says this week is mirrored, else None."""
        if not self.store.mirror_active(cur, season, stage, week):
            return None
        return self.plan_week(cur, snapshot, season, stage, week)

    def mirror_restore(self, cur, season, stage, week):
        """The records edit that turns the save's two injury switches back on after the mirror is off.

        Only when the settings row says they are still OFF and the mirror is not active for this run (a LIVE week's
        switch-on is part of the mirrored week itself). None otherwise."""
        settings = self.store.read_settings(cur)
        if not settings or settings.get("injuries_state") != "OFF":
            return None
        if self.store.mirror_active(cur, season, stage, week):
            return None
        edit = _setting_edit(settings, "ON")
        return {"state": "ON", "edit": edit} if edit is not None else None

    # -- chart override -------------------------------------------------------------------------------
    def bind(self, mirror, plan):
        """Remember this run's mirror and plan so chart_override can read the post-heal roster."""
        self._mirror, self._plan = mirror, plan

    def chart_override(self, team_index):
        """The real chart for `team_index` (depth.real_chart), built from the roster as this write leaves it, or None.

        S20: a healed + activated player must be on the chart in the same write, so the injury/IR fields planned for
        him (`plan.edits`) win over the snapshot's injury. F1: only a player this write leaves on the team's active
        roster (a created player who was just signed, not a released or practice-squad man) may take a real slot."""
        mirror, plan = self._mirror, self._plan
        if mirror is None or plan is None:
            return None
        code = (mirror.codes or {}).get(team_index)
        if code is None:
            return None
        real_depth = (getattr(mirror.real, "depth", None) or {}).get(code) or {}
        if not real_depth:
            return None
        rows, members, gsis_by_pgid = {}, {}, {}
        for gsis, pgid in (mirror.links or {}).items():
            gsis_by_pgid.setdefault(pgid, []).append(gsis)
        for pgid, player in plan.players.items():
            inj = dict(player.get("injury") or {})
            fields = (plan.edits.get(pgid) or {}).get("set") or {}
            if "InjuryStatus" in fields:
                inj["status"] = fields["InjuryStatus"]
            if "IsInjuredReserve" in fields:
                inj["injured_reserve"] = fields["IsInjuredReserve"]
            rows[pgid] = {"injury": inj, "position": fields.get("Position", player.get("position"))}
            if plan.roster_status(pgid) == ("Signed", team_index):
                for gsis in gsis_by_pgid.get(pgid, ()):
                    members[gsis] = pgid
        return mirror_depth.real_chart(real_depth, members, rows)

    # -- planning ---------------------------------------------------------------------------------------
    def plan_week(self, cur, snapshot, season, stage, week):
        """Build and record one mirrored week (see the module docs)."""
        settings = self.store.read_settings(cur)
        real_week = real_state.real_week_for(week)
        files = self.fetch(season, refresh=self.refresh)
        rows = {name: list(self.read_rows(files[name])) for name in ("rosters", "injuries", "depth", "games")}
        src = self.sources(files)
        real = self.build(rows["rosters"], rows["injuries"], rows["depth"], rows["games"], season, real_week,
                          sources=src)
        later = self.later(rows["rosters"], rows["injuries"], season, real_week) if real.mode == CATCH_UP else {}
        contracts_file = files.get("contracts")
        book = (self.contract_book(getattr(contracts_file, "path", contracts_file))
                if contracts_file is not None else mirror_contracts.ContractBook([]))
        cap = self.cap(snapshot)
        team_index = self.team_codes(snapshot, season)
        codes = {index: code for code, index in team_index.items()}
        links = dict(self.store.links(cur) or {})
        roster = self.plan_rosters(snapshot, real, links, self.read_trades(), team_index, book, cap)
        base_links = dict(links)        # the stored links, before this week's new ones: the linker's own inputs
        for gsis, pgid in roster.unlinked_new or ():
            links.setdefault(gsis, pgid)

        pids = self.pid_by_pgid(cur) or {}
        link_records = self._link_records(snapshot, real, base_links, team_index, roster.unlinked_new, pids)
        creates = self._creates(cur, snapshot, real, later, roster.creates, links, book, cap, team_index, pids)
        created_gsis = set()
        for create in creates:      # F1: a created identity is linked before the injury and chart reconciliation
            links.setdefault(create["gsis_id"], create["donor_pgid"])
            created_gsis.add(create["gsis_id"])
        # audit-5 U18 R1: one reservation set for every identity this write consumes, so the PS fill cannot take a
        # newly matched person (all `links`), a planned creation donor, or a free agent an ordinary signing takes.
        reserved = {int(pgid) for pgid in links.values() if pgid is not None}
        reserved.update(int(create["donor_pgid"]) for create in creates)
        reserved.update(int(op["pgid"]) for op in roster.ops or []
                        if op.get("kind") == "sign" and op.get("pgid") is not None)
        ps_signs = self._ps_signs(cur, snapshot, roster.ps_fill, season, stage, week, pids, reserved)
        injuries, injury_players = _injuries(real, later, snapshot, links, skip=created_gsis)
        injury_players.update(int(create["donor_pgid"]) for create in creates)
        staff_changes, staff_moves = self._staff(cur, rows["games"], season, real)
        setting = _setting(settings, real.mode)

        moves = _moves(real, roster, ps_signs, creates, injuries, staff_moves, setting, team_index,
                       self.team_ids(cur, snapshot), pids, snapshot)
        counts = collections.Counter()
        for move in moves:
            counts[move["kind"].lower()] += 1
        counts = {k: v for k, v in sorted(counts.items())}
        report = _report(real, counts, roster.review, setting, staff_moves)
        self.store.record_week(cur, int(season), int(week), real_week=real.week, mode=real.mode, sources=src,
                               counts=counts, report=report, status="PLANNED")
        self.store.record_moves(cur, int(season), int(week), moves, replace=True)
        return MirrorWeek(cur=cur, store=self.store, season=int(season), week=int(week), real_week=real.week,
                          stage=stage, mode=real.mode, real=real, roster=roster, links=links,
                          team_index=team_index, codes=codes, contracts=book, cap=cap, creates=creates,
                          link_records=link_records,
                          ops=list(roster.ops or []), ps_fill=list(roster.ps_fill or []), ps_signs=ps_signs,
                          injuries=injuries,
                          injury_players=injury_players, review=list(roster.review or []),
                          staff_changes=staff_changes, staff_moves=staff_moves,
                          staff_changed=any(m.get("status") == "PLANNED" for m in staff_moves),
                          setting=setting, sources=src, counts=counts, report=report, moves=moves)

    # -- created players ---------------------------------------------------------------------------------
    def _link_records(self, snapshot, real, links, team_index, unlinked_new, pids):
        """The durable `nfl_player_links` rows for this week's ordinary new links, with the linker's own method.

        The diff reports only `(gsis_id, pgid)`; the method/evidence come from the same `link.match` call it made
        (audit-4 U6 F4), so an accepted override is stored as OVERRIDE and never rewritten as CREATED. The real
        person's identity travels in the evidence's `person` key, so `_record_link` can add the minimal
        `nfl_players` row a current newcomer absent from the history load needs (audit-5 U18 R2)."""
        if not unlinked_new:
            return []
        details = self.match_links(snapshot, real, links, team_index) or {}
        real_by_gsis = mirror_diff._real_by_gsis(real)
        out = []
        for gsis, pgid in unlinked_new:
            player_id = (pids or {}).get(int(pgid))
            if player_id is None:
                log.warning("NFL mirror: no players row for pgid %s; the new link %s is not persisted", pgid, gsis)
                continue
            detail = details.get(gsis) or {}
            method = str(detail.get("method") or "").upper()
            evidence = dict(detail.get("evidence") or {})
            person = real_by_gsis.get(gsis)
            if person is not None:
                evidence["person"] = {"name": person.name, "position": person.position,
                                      "college": person.college, "birth_date": person.birth_date}
            if method not in LINK_METHODS:
                method = "ALIAS"
                evidence.setdefault("source", "NFL weekly mirror plan_rosters")
            out.append({"player_id": int(player_id), "gsis_id": gsis, "method": method,
                        "score": detail.get("score"), "evidence": evidence})
        return out

    def _creates(self, cur, snapshot, real, later, wanted, links, book, cap, team_index, pids):
        if not wanted:
            return []
        snap_players = _by_pgid(snapshot)
        ea_idx = {}
        if self.ea_index is not None and self.ea_items is not None and self.ea_iteration is not None:
            try:
                ea_idx = self.ea_index(self.ea_items(self.ea_iteration(refresh=False)))
            except Exception as e:      # noqa: BLE001 - EA unavailable: the role template is the documented fallback
                log.warning("EA ratings unavailable, creating from templates: %s", e)
        blocked = dict(self.donor_links(cur, links, _real_gsis(real)) or {})
        used = {}
        out = []
        as_of = _week_as_of(real)
        for real_player in wanted:
            ea_item = self.ea_find(real_player, ea_idx) if ea_idx else None
            position = mirror_create.target_position(real_player, ea_item)
            template = mirror_create.role_template(snapshot, position, _role(real, real_player))
            if ea_item is None and template is None:
                log.warning("cannot create %s: no EA item and no save player at %s", real_player.name, position)
                continue
            donors = mirror_create.pick_donors(snapshot, {**blocked, **used}, 1, position)
            if not donors:
                log.warning("cannot create %s: no spare free-agent record at %s", real_player.name, position)
                continue
            donor = snap_players[donors[0]]
            try:
                edit = mirror_create.created_edit(donor, real_player, ea_item, template)
            except ValueError as e:
                log.warning("cannot create %s: %s", real_player.name, e)
                continue
            used[int(donor["pgid"])] = "USED"
            index = team_index.get(real_player.team)
            if index is None:
                continue
            contract = book.contract_for(real_player.gsis_id, as_of, cap,
                                         years_exp=_years_exp(real_player, real.season),
                                         team=real_player.team)
            placement, injury_fields = _creation_placement(real_player, real, later)
            edit["set"].update(injury_fields)
            out.append({"gsis_id": real_player.gsis_id, "real": real_player, "edit": edit,
                        "player_id": (pids or {}).get(int(donor["pgid"])),
                        "team_index": int(index), "list": placement, "contract": contract,
                        "source": edit["source"], "position": position, "donor_pgid": int(donor["pgid"]),
                        "evidence": {"name": real_player.name, "position": position,
                                     "college": real_player.college, "birth_date": real_player.birth_date,
                                     "source": edit["source"], "donor_pgid": int(donor["pgid"]),
                                     "evidence": CREATION_EVIDENCE}})
        return out

    # -- practice-squad fills -------------------------------------------------------------------------------
    def _ps_signs(self, cur, snapshot, ps_fill, season, stage, week, pids, reserved=()):
        """The diff's PS-only slots, each filled with the best *remaining* free agent at the position from
        `Deps.free_agent_pool`'s order (plan §2.6). `reserved` carries the pgids this write already consumes: newly
        matched people, creation donors and ordinary signings (audit-5 U18 R1). A slot with no remaining donor is an
        explicit REFUSED entry, logged and recorded in the move ledger, never a silent drop. No pool (or a fake)
        leaves the slot unfilled, with a warning."""
        if not ps_fill:
            return []
        from pipeline import league_office as lo
        try:
            target = lo.target_season(int(season), stage, week)
            pool = list(self.free_pool(cur, int(season), target) or [])
        except Exception as e:      # noqa: BLE001 - the PS fill is a nicety; the roster mirror matters more
            log.warning("NFL mirror: free-agent pool unavailable, %d practice-squad slot(s) unfilled (%s)",
                        len(ps_fill), e)
            return []
        players = _by_pgid(snapshot)
        free = {}
        for pgid, player in players.items():
            if player.get("status") != "FreeAgent":
                continue
            pid = (pids or {}).get(pgid)
            if pid is not None:
                free[pid] = pgid
        out = []
        used = {int(pgid) for pgid in (reserved or ()) if pgid is not None}
        for slot_index, slot in enumerate(ps_fill):
            position = str(slot.get("position") or "").strip().upper()
            team_index = int(slot["team_index"]) if slot.get("team_index") is not None else None
            best = None
            for entry in pool:
                pgid = free.get(entry.get("id"))
                if pgid is None or pgid in used:
                    continue
                if str(entry.get("pos") or "").strip().upper() != position:
                    continue
                key = (entry.get("ovr") if isinstance(entry.get("ovr"), (int, float)) else -1, int(pgid))
                if best is None or key > best[0]:
                    best = (key, pgid)
            if best is None:
                refused = (f"no free agent at {position} for team {team_index}'s practice squad: "
                           f"every candidate is already used by this write")
                log.warning("NFL mirror: %s", refused)
                out.append({"pgid": None, "team_index": team_index, "position": position,
                            "tag": f"mirror:ps:unfilled:{team_index}:{position}:{slot_index}",
                            "refused": refused})
                continue
            used.add(int(best[1]))
            out.append({"pgid": int(best[1]), "team_index": team_index,
                        "position": position, "tag": f"mirror:ps:{int(best[1])}",
                        "why": f"real practice squad has a {position} the save lacks"})
        return out

    # -- coaching changes ---------------------------------------------------------------------------------
    def _staff(self, cur, rows_games, season, real):
        """The real week's HC/OC/DC changes, written into Mu's tenure tables, and their STAFF move dicts.

        A database error here propagates (plan.step makes it a plan error and nothing is written) rather than
        leaving half the tenures changed and the save written."""
        by_week = {w: self.head_coaches(rows_games, season, w) for w in range(1, int(real.week) + 1)}
        changes = self.real_staff(by_week, real.week, self.read_staff()) or []
        moves = self.apply_staff(cur, season, changes, self.user_team_ids(cur)) if changes else []
        return list(changes), list(moves or [])


# ---------------------------------------------------------------- install -------------------------------------
def install(deps, adapter=None):
    """Wire the adapter onto a league office deps instance (default_deps does this for real runs)."""
    adapter = adapter or MirrorAdapter()
    deps.mirror_week = adapter.mirror_week
    deps.mirror_restore = adapter.mirror_restore
    deps.chart_override = adapter.chart_override
    deps.bind_mirror = adapter.bind
    return deps


# ---------------------------------------------------------------- helpers -------------------------------------
def _by_pgid(snapshot):
    """The snapshot's players keyed by int pgid (the mirror works in ints; pick_donors and store.links do)."""
    out = {}
    for player in (snapshot or {}).get("players") or []:
        if player.get("pgid") is None:
            continue
        try:
            out[int(player["pgid"])] = player
        except (TypeError, ValueError):
            continue
    return out


def _week_as_of(real):
    """'YYYY-MM-DD', the last day of `real`'s week. F5: the same canonical boundary the roster diff uses for an
    ordinary signing's contract (`diff._week_as_of`), so a created player's deal cannot be a day later."""
    return mirror_diff._week_as_of(real)


def _creation_placement(real_player, real, later):
    """(list, injury fields) the real week places a created player on: PS, IR-family reserve, a weekly injury
    report, or a healthy active roster (audit-4 U6 F1).

    The donor record may carry an old injury or IR flag, so the fields always say what the real week says; a reserve
    status is `IsInjuredReserve` True, an active player False."""
    report = (getattr(real, "injuries", None) or {}).get(real_player.gsis_id)
    report_status = str(getattr(report, "report_status", None) or "").strip().upper()
    catch_up = getattr(real, "mode", CATCH_UP) == CATCH_UP
    reserve = real_player.status in IR_STATUSES
    hurt = reserve or (catch_up and report_status in REPORT_STATUSES)
    placement = PRACTICE_SQUAD if real_player.status == "PS" else ROSTER
    if not hurt:
        return placement, {"IsInjuredReserve": False, **mirror_injuries.HEAL_FIELDS}
    if catch_up:
        weeks = max(1, mirror_injuries.weeks_until_back(real_player.gsis_id, real.week, later))
    else:
        weeks = mirror_injuries.estimate_weeks(report, real_player.status, [])
    fields = dict(mirror_injuries.injury_fields(weeks, getattr(report, "body_part", None)),
                  IsInjuredReserve=reserve)
    return placement, fields


def _years_exp(player, season):
    return None if player.entry_year is None else max(0, int(season) - int(player.entry_year))


def _role(real, player):
    """The creation role for `role_template`: IR for a reserve status, STARTER for the first man at a real position
    on his team's depth chart, else BACKUP."""
    if player.status in IR_STATUSES:
        return "IR"
    for ids in ((getattr(real, "depth", None) or {}).get(player.team) or {}).values():
        if ids and ids[0] == player.gsis_id:
            return "STARTER"
    return "BACKUP"


def _real_gsis(real):
    return {p.gsis_id for players in (real.rosters or {}).values() for p in players}


def _same_injury(current, fields):
    """True when the save player already has the injury `fields` would write."""
    return (current.get("status") == fields.get("InjuryStatus", current.get("status"))
            and int(current.get("min_weeks") or 0) == int(fields.get("MinInjuryDuration") or 0)
            and int(current.get("max_weeks") or 0) == int(fields.get("MaxInjuryDuration") or 0))


def _needs_heal(current):
    return (current.get("status") not in HEALTHY or int(current.get("min_weeks") or 0) > 0
            or int(current.get("max_weeks") or 0) > 0)


def _injuries(real, later, snapshot, links, skip=()):
    """([INJURY / HEAL dicts], pgids whose injury fields the mirror decides).

    CATCH_UP (D7): a linked real player on a roster is hurt now when his status is IR / PUP / NFI or this week's
    report is Out or Doubtful (Questionable plays), and `weeks_until_back` gives the hindsight duration (at least the
    current week). He is healed when the save still has him hurt. LIVE (D12): only IR / PUP / NFI placements are
    copied (`estimate_weeks` gives 4), no weekly report is copied, and Madden's own injuries are never healed. Only
    players whose fields would change carry an edit; every player the mirror decided is claimed so a LIVE week's
    `step_injuries` leaves his fields alone. `skip` names the real players created this week: `_creates` already put
    the real injury/IR state in their edit, so a second row would clash (F1)."""
    snap = _by_pgid(snapshot)
    out, players, seen = [], set(), set()
    catch_up = real.mode == CATCH_UP
    for code in sorted(real.rosters or {}):
        for player in real.rosters[code]:
            if player.gsis_id in seen or player.gsis_id in (skip or ()):
                continue
            seen.add(player.gsis_id)
            pgid = (links or {}).get(player.gsis_id)
            if pgid is None or pgid not in snap:
                continue
            report = (real.injuries or {}).get(player.gsis_id)
            report_status = str(getattr(report, "report_status", None) or "").strip().upper()
            current = snap[pgid].get("injury") or {}
            if player.status in IR_STATUSES:
                hurt = True
            elif catch_up:
                hurt = report_status in REPORT_STATUSES
            else:
                hurt = False
            if not hurt:
                if catch_up and _needs_heal(current):
                    out.append({"kind": "HEAL", "pgid": pgid, "gsis_id": player.gsis_id,
                                "fields": dict(mirror_injuries.HEAL_FIELDS), "weeks": 0,
                                "why": f"healthy on the real {player.team} roster"})
                continue
            players.add(pgid)
            if catch_up:
                weeks = max(1, mirror_injuries.weeks_until_back(player.gsis_id, real.week, later))
                why = f"real {player.status.lower() or report_status.lower()}, back in {weeks} week(s)"
            else:
                weeks = mirror_injuries.estimate_weeks(report, player.status, [])
                why = f"real {player.status.lower() or report_status.lower()} placement"
            fields = mirror_injuries.injury_fields(weeks, getattr(report, "body_part", None))
            if not _same_injury(current, fields):
                out.append({"kind": "INJURY", "pgid": pgid, "gsis_id": player.gsis_id, "fields": fields,
                            "weeks": weeks, "why": why})
    return out, players


def _setting(settings, mode):
    """The injury-switch record a mirrored week owes: OFF on the first CATCH_UP write, ON on the first LIVE one."""
    if not settings:
        return None
    if mode == CATCH_UP and settings.get("injuries_state") != "OFF":
        edit = _setting_edit(settings, "OFF")
    elif mode == LIVE and settings.get("injuries_state") == "OFF":
        edit = _setting_edit(settings, "ON")
    else:
        return None
    return {"state": "OFF" if mode == CATCH_UP else "ON", "edit": edit} if edit is not None else None


def _setting_edit(settings, state):
    """The records edit for the settings row's two injury switches, at `state`, or None when they are unknown."""
    spec = settings.get("madden_injuries_field")
    if not isinstance(spec, dict) or spec.get("tableId") is None:
        return None
    key = "off" if state == "OFF" else "on"
    values = {}
    for entry in (spec.get("fields") or {}).values():
        if isinstance(entry, dict) and entry.get("field"):
            values[entry["field"]] = entry.get(key)
    if not values:
        return None
    return {"table": spec.get("table") or "LeagueSetting", "tableId": spec["tableId"],
            "row": spec.get("row") or 0, "set": values}


def _moves(real, roster, ps_signs, creates, injuries, staff_moves, setting, team_index, team_ids, pid_by_pgid,
           snapshot):
    """The PLANNED nfl_mirror_moves rows for the week (store.record_moves fields)."""
    players = _by_pgid(snapshot)
    team_ids = team_ids or {}

    def team_id(index):
        return team_ids.get(index) if index is not None else None

    def player_id(pgid):
        return (pid_by_pgid or {}).get(pgid)

    out = []
    for op in roster.ops or []:
        player = players.get(op["pgid"]) or {}
        tag = f"mirror:{op['kind']}:{op.get('gsis_id')}"
        current = "Roster" if player.get("status") == "Signed" else (
            "PracticeSquad" if player.get("status") == "PracticeSquad" else None)
        detail = {"why": op.get("why"), "list": op.get("list"), "tag": tag}
        if current is not None and op.get("list") is not None and op["kind"] in ("list", "trade") \
                and op.get("list") != current:
            detail["from_list"] = current
        base = {"gsis_id": op.get("gsis_id"), "pgid": op.get("pgid"), "player_id": player_id(op.get("pgid")),
                "detail": detail, "reason": op.get("why")}
        if op["kind"] == "release":
            out.append(dict(base, kind="RELEASE", from_team_id=team_id(player.get("team_index"))))
        elif op["kind"] == "trade":
            out.append(dict(base, kind="TRADE", from_team_id=team_id(player.get("team_index")),
                            to_team_id=team_id(op.get("to_index"))))
        elif op["kind"] == "sign":
            detail["switch"] = bool(op.get("switch"))
            out.append(dict(base, kind="SIGN", to_team_id=team_id(op.get("to_index"))))
        elif op["kind"] == "list":
            # A same-team Roster/PracticeSquad change: the move ledger's PS kind, so no schema change is needed.
            out.append(dict(base, kind="PS", from_team_id=team_id(player.get("team_index")),
                            to_team_id=team_id(player.get("team_index"))))
        elif op["kind"] == "ir":
            out.append(dict(base, kind="IR", to_team_id=team_id(op.get("to_index"))))
        elif op["kind"] == "activate":
            out.append(dict(base, kind="ACTIVATE", to_team_id=team_id(op.get("to_index"))))
    for create in creates:
        tag = f"mirror:create:{create['gsis_id']}"
        out.append({"kind": "CREATE", "gsis_id": create["gsis_id"], "pgid": create["donor_pgid"],
                    "player_id": player_id(create["donor_pgid"]),
                    "to_team_id": team_id(create["team_index"]),
                    "detail": dict(create["evidence"], tag=tag), "reason": CREATION_EVIDENCE})
    for injury in injuries:
        tag = f"mirror:{injury['kind'].lower()}:{injury['gsis_id']}"
        out.append({"kind": injury["kind"], "gsis_id": injury["gsis_id"], "pgid": injury["pgid"],
                    "player_id": player_id(injury["pgid"]),
                    "detail": {"weeks": injury["weeks"], "fields": injury["fields"], "tag": tag},
                    "reason": injury["why"]})
    for slot in ps_signs or []:
        pgid = slot.get("pgid")
        if pgid is None:        # audit-5 U18 R1: an exhausted position is an explicit REFUSED ledger row
            tag = slot.get("tag") or "mirror:ps:unfilled"
            out.append({"kind": "PS", "pgid": None, "player_id": None,
                        "to_team_id": team_id(slot.get("team_index")),
                        "detail": {"position": slot.get("position"), "tag": tag},
                        "reason": slot.get("refused") or "no free agent for the practice-squad slot"})
            continue
        pgid = int(pgid)
        tag = slot.get("tag") or f"mirror:ps:{pgid}"
        out.append({"kind": "PS", "pgid": pgid, "player_id": player_id(pgid),
                    "to_team_id": team_id(slot.get("team_index")),
                    "detail": {"position": (players.get(pgid) or {}).get("position") or slot.get("position"),
                               "tag": tag},
                    "reason": slot.get("why") or "real practice squad"})
    for i, move in enumerate(staff_moves or []):
        tagged = dict(move)
        tagged["detail"] = dict(move.get("detail") or {}, tag=f"mirror:staff:{i}")
        out.append(tagged)
    if setting is not None:
        out.append({"kind": "SETTING", "detail": {"state": setting["state"], "fields": setting["edit"]["set"],
                                                  "tag": "mirror:setting"},
                    "reason": f"mirror {real.mode.lower()} week: injury switches {setting['state'].lower()}"})
    for code in sorted(real.depth or {}):
        out.append({"kind": "CHART", "to_team_id": team_id((team_index or {}).get(code)),
                    "detail": {"source": "MU_COACH", "real_slots": len(real.depth.get(code) or {}),
                               "tag": f"mirror:chart:{code}"},
                    "reason": "the real chart merged with the engine's"})
    return out


def _report(real, counts, review, setting, staff_moves):
    roster_moves = sum(counts.get(k, 0) for k in ("release", "sign", "trade", "ps", "ir", "activate", "create"))
    parts = [f"NFL mirror week {real.week} ({real.mode}): {roster_moves} roster moves",
             ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "none"]
    candidates = sum(1 for r in review or [] if r.get("status") == "candidate")
    ambiguous = sum(1 for r in review or [] if r.get("status") == "ambiguous")
    if candidates or ambiguous:
        parts.append(f"review: {candidates} cross-position candidate(s), {ambiguous} ambiguous name(s)")
    planned = sum(1 for m in staff_moves or [] if m.get("status") == "PLANNED")
    if planned:
        parts.append(f"{planned} staff change(s)")
    if setting is not None:
        parts.append(f"injury switches {setting['state']}")
    return "; ".join(parts)


# ---------------------------------------------------------------- default collaborators ----------------
def _team_codes(snapshot, season=None):
    """{save team code: team_index} from the snapshot's teams, through Mu's abbreviation aliases."""
    out = {}
    for team in snapshot.get("teams") or []:
        index = team.get("team_index")
        if index is None or int(index) >= ls.FREE_AGENT_TEAM_INDEX:
            continue
        short = team.get("short_name")
        if not short:
            continue
        short = ls.TEAM_ABBR_ALIASES.get(short, short)
        try:
            code = mu_abbr(short, season)
        except UnknownTeam:
            code = short
        out[code] = int(index)
    return out


def _league_cap(snapshot):
    from pipeline import league_office as lo
    return lo.league_cap(None, snapshot)


def _match_links(snapshot, real, links, team_index):
    """{gsis_id: link.match item} for this week's ordinary new links (the diff's inputs, re-run for method/evidence).

    `diff.plan_rosters` reports only the accepted `(gsis_id, pgid)` pairs; the linker's method (OVERRIDE, ESPN,
    EXACT, ALIAS, FUZZY) and evidence are needed to persist the link honestly (audit-4 U6 F4). The call is the same
    one `diff._new_links` makes, so it decides the same pairs."""
    from engines.nfl_history import link as link_engine
    players = _by_pgid(snapshot)
    real_by_gsis = mirror_diff._real_by_gsis(real)
    linked_pgids = set((links or {}).values())
    codes = {int(i): code for code, i in (team_index or {}).items()}
    people = [mirror_diff._real_person(p) for gsis, p in sorted(real_by_gsis.items()) if gsis not in (links or {})]
    mu = [mirror_diff._mu_player(pgid, p, codes, int(real.season))
          for pgid, p in sorted(players.items())
          if pgid not in linked_pgids and p.get("status") not in (None, "Retired", "Draft")]
    if not people or not mu:
        return {}
    result = link_engine.match(mu, people, link_engine.read_overrides())
    return {item["gsis_id"]: item for item in result.links}


def _donor_links(cur, links, real_gsis):
    """{pgid: gsis_id} for every linked person: a linked record is never reused as a creation donor.

    `store.add_created_link` refuses a player already linked elsewhere, so reusing a retired-linked donor would
    leave the created person's identity unrecorded (`_record_mirror` used to log the ValueError after the save had
    already been replaced). Excluding every linked donor is the safe half of audit-4 U6 F4's two options; identity
    reuse with preserved attribution needs a store change the batch does not make."""
    return {int(pgid): gsis for gsis, pgid in (links or {}).items() if pgid is not None}


def _team_ids(cur, snapshot):
    from pipeline import league_office as lo
    return lo.Deps().team_ids(cur, snapshot)


def _user_team_ids(cur):
    from pipeline import league_office as lo
    return lo.Deps().user_team_ids(cur)


def _pid_by_pgid(cur):
    """{pgid: player_id} (the inverse of Deps.pgid_map, which is {player_id: madden_pgid})."""
    from pipeline import league_office as lo
    return {g: p for p, g in lo.Deps().pgid_map(cur).items()}


def _free_pool(cur, season, target):
    from pipeline import league_office as lo
    return lo.Deps().free_agent_pool(cur, season, target)
