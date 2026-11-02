"""
League office stage loop (docs/in_progress/V2_CORE_BUILD_PLAN.md §2.6, U13; docs/done/LEAGUE_OFFICE_PLAN.md §3.3).

After JR advances Madden to a stage, saves and quits, the league office plans Project Mu's moves for that stage as one
pipeline/save_reader/save_writer.mjs edits document, writes it to a temp copy of the save, reads the copy back and only
then replaces the save:

  python3 -m pipeline.league_office --snapshot captures/<run>/save.json --save <CAREER-file>
                                    [--stage auto|<STAGE>] [--wave N] [--seed S] [--dry-run] [--dsn ...]
                                    [--confirm-franchise-closed]
  python3 -m pipeline.league_office setup --save <CAREER-file> --out <new-file>

Stages (detect_stage). Regular season and playoffs come from season.week_type. The offseason (season.stage OffSeason)
comes from season.offseason_stage (SeasonInfo.CurrentOffseasonStage) and week_index, as JR's 2026 stage saves show:
  stage 1: week 0 OFFSEASON_START (Madden's retirements announced), week 1 RESIGN, week 2+ FREE_AGENCY wave 1 (Madden's
  FAPREP: every expiring contract has become a free agent); stage 2 FREE_AGENCY wave 2 (Madden's live free-agency
  event); stage 3 FREE_AGENCY wave 3; stages 4-5 COMBINE; stages 6-8 PRO_DAY; stage 9 DRAFT (the draft screen); stage
  10+ UDFA (the post-draft recap and its live UDFA event). Without an offseason stage number the week index stands in
  (0 START, 1 RESIGN, 2-4 FA waves 1-3, 5-6 COMBINE, 7-10 PRO_DAY, 11-14 DRAFT, 15+ UDFA). season.stage PreSeason is
  PRESEASON before week PRESEASON_CUTDOWN_WEEK and CUTDOWN from it (an estimate until a save at the last preseason
  week exists). League-state weeks are contracts_engine.OFFSEASON_STAGE_WEEKS (OFFSEASON_START 24, RESIGN 25,
  FREE_AGENCY 26 for every wave, DRAFT 29, UDFA 30, CUTDOWN 31, PRESEASON 32) plus COMBINE 27 and PRO_DAY 28, the two
  weeks between free agency and the draft that the engine's map leaves free.

Seasons. `season` is league_state's season: the snapshot's calendar year, except at PRESEASON / CUTDOWN, where the
save has already rolled into the new year and the offseason's season is the year before. The league year moves count
against is transactions_engine.target_season (season + 1 in the offseason). Madden's contracts roll into the new league
year at FAPREP (the stage saves show all expiring deals freed there), so a contract written at OFFSEASON_START or
RESIGN for the coming league year keeps the deal's current-year terms as year 0 and puts the new years after it; from
FREE_AGENCY on, and in season, year 0 is the league year itself. Written contracts always start at CurrentYear 0.

Every stage plans, in this priority (a player takes part in one of release / sign / trade per write; the first claim
wins, and a lower-priority player field never overwrites a higher one):
  1. JR's PENDING franchise_pending_moves (marked WRITTEN, or REFUSED with the reason, after the write).
  2. contract_offers JR's targets ACCEPTED (from_user): FREE_AGENT / UDFA -> a Roster signing, RESIGN / EXTENSION /
     TENDER -> the player's contract rewritten.
  3. drift_check.diff write-back: a player Madden signed is released, one Madden cut is signed back with Project Mu's
     contract, one Madden traded is traded back, contracts and depth charts Project Mu stored are restored, sliders
     go back to 0 (rating drift is written back at the lowest priority, under the stage's own rating edits).
  4. The stage:
     REGULAR_WEEK / PLAYOFF_WEEK  injuries (Project Mu's duration: the previous mu_weeks_out minus the weeks since,
         or a seeded draw in Madden's MinInjuryDuration..MaxInjuryDuration; written to both fields and to
         injuries.mu_weeks_out), IR placement at IR_MIN_WEEKS+ weeks out, activation only when healthy (including
         players Madden left on IR after they healed), waiver claims for the players released by the last WRITTEN
         run (transactions_engine.waivers; a claim can't be written in the write that releases him), form_engine's
         form overlay and morale 50, progression.in_season_checkpoint when that hook exists (its rows become rating
         edits through progression_save.rating_edits; skipped with a log line when absent), then roster legality:
         every team down to MAX_ACTIVE active players (ROSTER_LIMIT 65 minus ROSTER_BUFFER 2: room for one injury
         replacement so Madden never has a reason to sign or cut on its own) by releasing the lowest-OVR healthy
         non-starters (ties: the biggest cut savings first), and coach depth charts (depth_chart_engine) for every
         team except one that queued its own (a PENDING DEPTH_CHART move) or a user team with a stored JR chart.
     OFFSEASON_START  progression_save.pass_edits for season + 1 (ratings, XP, morale, sliders 0 incl. LS, dev-trait
         regression and breakout scenarios off) over form_engine's reset (base ratings, no overlay); retirements:
         every player Madden retired is recorded in retirement_decisions and Project Mu keeps (un-retires) him when
         keep_retired says so; contracts for the practice-squad players Madden promoted (a 1-year minimum for the
         coming league year, contracts_engine.minimum_salary).
     RESIGN  transactions_engine.resign_decisions (CONTRACT / TAG / EXTENSION / OPTION rewrite the contract; LET_GO
         is left to Madden's FAPREP).
     FREE_AGENCY  cpu_offers for the wave, free_agency_engine.run_wave, signings_from_decisions (offers' statuses
         updated after the write).
     COMBINE, PRO_DAY  only the every-stage items.
     DRAFT  every pick from the owner's team board (scout_team_boards, newest step), JR's own picks from the war room
         (scout_draft_picks JR_TEAM), else Madden's board order; each drafted prospect signed with
         contracts_engine.rookie_contract.
     UDFA  free_agency_engine.udfa_market over the undrafted rookies.
     CUTDOWN  transactions_engine.cutdown to ROSTER_LIMIT and practice_squad; PRESEASON: cutdown to ROSTER_LIMIT.
     PRESEASON / CUTDOWN also set coach depth charts. Every offseason stage after OFFSEASON_START signs a kept
     retiree back to his old team when his stored contract runs into the league year.
  5. Cap fields (spike R6: Madden doesn't recompute them): each signed or re-contracted player's PLYR_CAPSALARY (the
     written contract's current-year SalaryYear + BonusYear, $10,000 units) and, on each affected team's Team row,
     TEAM_SALARY (the sum of current-year base salaries: matches the sample save within a few units) and
     SalCapCapRoom / SalCapSpendingMoney / SalCapNextYearCapRoom moved by the cap-hit change. Those three aren't in a
     confirmed unit (on CAREER-GIANTS-AUTOSAVE they don't match the x$10,000 contract hits on any one scale), so they
     are adjusted by the change from the save's stored value in $10,000 units (CAP_ROOM_UNIT) rather than recomputed.
     A release frees cap hit - dead money (all remaining bonus accelerates, load_save's rule); a trade moves the
     full cap hit. The stored values come from `league_setup.mjs state`.

Writer limits handled here (BLOCKERS for the writer, not this file): a Roster signing needs a PlayerContract row no
field references (the save has none spare; each Roster release in the same write frees one), so signings beyond the
free rows are deferred (reported, not written); the writer has no practice-squad promote / demote move, so those are
deferred or REFUSED; a kept retiree is un-retired as a free agent (ContractStatus FreeAgent, TeamIndex 32) and signed
back in a later write, since a move can't plan against the Retired status.

Writing (run). The document goes to `node save_writer.mjs <save> <edits> --out <temp copy>`, which checks every edit,
writes and reads the copy back; the copy is then read with pc_save_reader.mjs and the moved players checked again.
The save is replaced (atomically, from the verified copy) only when both passed and the franchise isn't loaded.
Madden may sit at its main menu with the franchise closed, which can't be told apart from a loaded franchise, so
while Madden27.exe runs the write needs --confirm-franchise-closed (or LEAGUE_OFFICE_FRANCHISE_CLOSED=1, for the
watcher); with Madden not running it writes. league_office_runs logs PLANNED -> WRITTEN | FAILED (edits, summary,
drift, error); --dry-run writes no save and no DB rows except one DRY_RUN run row (the engines' own inserts, such as
cpu_offers, are rolled back).
"""
import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict

from engines import contracts_engine as ce
from engines import transactions_engine as te
from pipeline import load_save as ls

log = logging.getLogger("league_office")

SAVE_READER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "save_reader")
WRITER = os.path.join(SAVE_READER_DIR, "save_writer.mjs")
READER = os.path.join(SAVE_READER_DIR, "pc_save_reader.mjs")
SETUP = os.path.join(SAVE_READER_DIR, "league_setup.mjs")

# ------------------------------------------------------------------ stages ----
REGULAR_WEEK, PLAYOFF_WEEK = "REGULAR_WEEK", "PLAYOFF_WEEK"
OFFSEASON_START, RESIGN, FREE_AGENCY = "OFFSEASON_START", "RESIGN", "FREE_AGENCY"
COMBINE, PRO_DAY, DRAFT, UDFA = "COMBINE", "PRO_DAY", "DRAFT", "UDFA"
CUTDOWN, PRESEASON = "CUTDOWN", "PRESEASON"
STAGES = (REGULAR_WEEK, PLAYOFF_WEEK, OFFSEASON_START, RESIGN, FREE_AGENCY, COMBINE, PRO_DAY, DRAFT, UDFA, CUTDOWN,
          PRESEASON)
IN_SEASON = (REGULAR_WEEK, PLAYOFF_WEEK)
NEW_YEAR_STAGES = (PRESEASON, CUTDOWN)          # the snapshot's calendar year is already the new season
PRE_ROLLOVER = IN_SEASON + (OFFSEASON_START, RESIGN)   # the save's contract year is still `season`
FA_WAVES = 3
# CurrentOffseasonStage -> (stage, wave); stage 1 is split by week (0 START, 1 RESIGN, 2+ FA wave 1).
OFFSEASON_BY_NUMBER = {2: (FREE_AGENCY, 2), 3: (FREE_AGENCY, 3), 4: (COMBINE, None), 5: (COMBINE, None),
                       6: (PRO_DAY, None), 7: (PRO_DAY, None), 8: (PRO_DAY, None), 9: (DRAFT, None)}
UDFA_FROM_STAGE = 10
# Fallback without an offseason stage number: week index -> (stage, wave).
OFFSEASON_BY_WEEK = [(0, OFFSEASON_START, None), (1, RESIGN, None), (2, FREE_AGENCY, 1), (3, FREE_AGENCY, 2),
                     (4, FREE_AGENCY, 3), (5, COMBINE, None), (7, PRO_DAY, None), (11, DRAFT, None), (15, UDFA, None)]
PRESEASON_CUTDOWN_WEEK = 3                     # estimate: the last preseason week
STAGE_WEEKS = dict(ce.OFFSEASON_STAGE_WEEKS, COMBINE=ce.OFFSEASON_STAGE_WEEKS["FREE_AGENCY"] + 1,
                   PRO_DAY=ce.OFFSEASON_STAGE_WEEKS["FREE_AGENCY"] + 2)
PRO_BOWL_WEEK = 22                             # load_save stores playoffs as 19/20/21/23; the Pro Bowl sits between

# ------------------------------------------------------------- roster rules ----
ROSTER_LIMIT = te.ROSTER_LIMIT                 # 65 (roster-size mod, D5)
ROSTER_BUFFER = 2                              # keep room for injury replacements: Madden never needs to act
MAX_ACTIVE = ROSTER_LIMIT - ROSTER_BUFFER      # 63 active (non-IR) players a team, in season
IR_MIN_WEEKS = 4                               # IR placement needs 4+ weeks out (franchise_api's IR_MIN_WEEKS)
HEALTHY = ("Uninjured", None)

# Un-retire rule (keep_retired): Madden's retirements are kept unless the player is still good enough to play.
UNRETIRE_MAX_AGE, UNRETIRE_MIN_OVR = 32, 75    # under 33 with OVR 75+: always kept
UNRETIRE_LATE_AGE, UNRETIRE_LATE_OVR, UNRETIRE_LATE_P = 34, 85, 0.5   # 33-34 with OVR 85+: kept half the time

# Cap fields on the Team row (spike R6).
CAP_ROOM_UNIT = 1                              # stored room change per $10,000 of cap hit (unit unconfirmed)
TEAM_SALARY_RANGE = (0, 1_048_575)
CAP_ROOM_RANGE = (-40_000, 200_000)
CAPSALARY_RANGE = (0, 32_766)
DEFAULT_CONTRACT_SLOTS = 7

# Player fields a sign / trade move sets (save_writer.mjs MOVE_FIELDS); a players edit of a moved player drops them.
MOVE_FIELDS = {
    "sign": ("TeamIndex", "ContractStatus", "Contract", "Mindset", "WeeksWithTeam"),
    "trade": ("TeamIndex", "ContractStatus", "Contract", "PrevTeamIndex", "WeeksWithTeam", "PLYR_ISCAPTAIN",
              "TradeStatus"),
}
FREE_AGENT_TEAM = ls.FREE_AGENT_TEAM_INDEX
FRANCHISE_CLOSED_ENV = "LEAGUE_OFFICE_FRANCHISE_CLOSED"

PRIORITY_JR, PRIORITY_OFFER, PRIORITY_DRIFT, PRIORITY_STAGE, PRIORITY_FORM, PRIORITY_DRIFT_RATINGS = 0, 1, 2, 3, 4, 5


class StageError(Exception):
    pass


class FranchiseOpenError(Exception):
    pass


# ------------------------------------------------------------ pure helpers ----
def _u01(*parts):
    h = hashlib.sha256(":".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(h[:8], "big") / 2 ** 64


def _clamp(v, lo_hi):
    return max(lo_hi[0], min(lo_hi[1], int(v)))


def detect_stage(snapshot):
    """The league office stage of a reader snapshot (season.stage / week_type / week_index / offseason_stage)."""
    return stage_and_wave(snapshot)[0]


def stage_and_wave(snapshot):
    """(stage, free-agency wave or None)."""
    s = snapshot.get("season") or {}
    stage = str(s.get("stage") or "").replace("_", "").lower()
    wtype = str(s.get("week_type") or "")
    week = s.get("week_index")
    if stage == "preseason" or (not stage and wtype.lower() == "preseason"):
        return (CUTDOWN if (week or 0) >= PRESEASON_CUTDOWN_WEEK else PRESEASON), None
    if stage == "offseason":
        number = s.get("offseason_stage")
        if isinstance(number, int) and number >= 1:
            if number == 1:
                if (week or 0) <= 0:
                    return OFFSEASON_START, None
                return (RESIGN, None) if week == 1 else (FREE_AGENCY, 1)
            if number >= UDFA_FROM_STAGE:
                return UDFA, None
            return OFFSEASON_BY_NUMBER[number]
        if week is None:
            raise StageError("offseason save without a week index or offseason stage")
        found = OFFSEASON_BY_WEEK[0]
        for row in OFFSEASON_BY_WEEK:
            if week >= row[0]:
                found = row
        return found[1], found[2]
    if wtype in ls.PLAYOFF_WEEKS or wtype.lower() == "probowl":
        return PLAYOFF_WEEK, None
    if wtype == "RegularSeason" or stage == "nflseason":
        return REGULAR_WEEK, None
    raise StageError(f"unknown stage: stage {s.get('stage')!r}, week type {wtype!r}, week {week!r}")


def league_season(snapshot, stage):
    """league_state's season for this stage (the year before the snapshot's at PRESEASON / CUTDOWN)."""
    year = snapshot["season"]["calendar_year"]
    return year - 1 if stage in NEW_YEAR_STAGES else year


def league_week(snapshot, stage):
    """The league_state week number of the stage (games numbering in season, STAGE_WEEKS in the offseason)."""
    if stage in IN_SEASON:
        wk = ls.current_week(snapshot)
        return wk if wk is not None else PRO_BOWL_WEEK
    return STAGE_WEEKS[stage]


def target_season(season, stage, week):
    """The league year a move counts against (transactions_engine.target_season by week, so COMBINE and PRO_DAY,
    which the engine's stage list lacks, count against the next league year too)."""
    return te.target_season(season, week=week)


def save_year(season, stage):
    """The league year the save's contracts are in (CurrentYear): `season` until RESIGN, the next one from FAPREP."""
    return season if stage in PRE_ROLLOVER else season + 1


def keep_retired(player, season, seed):
    """(mu_keep, reason) for a player Madden retired (documented rule above; seeded)."""
    age, ovr = player.get("age") or 99, player.get("overall") or 0
    if age <= UNRETIRE_MAX_AGE and ovr >= UNRETIRE_MIN_OVR:
        return True, f"kept: age {age} < {UNRETIRE_MAX_AGE + 1} with OVR {ovr} >= {UNRETIRE_MIN_OVR}"
    if age <= UNRETIRE_LATE_AGE and ovr >= UNRETIRE_LATE_OVR:
        keep = _u01(seed, "retire", season, player.get("pgid")) < UNRETIRE_LATE_P
        return keep, f"{'kept' if keep else 'retired'}: age {age}, OVR {ovr} (seeded {UNRETIRE_LATE_P:.0%} chance)"
    return False, f"retired: age {age}, OVR {ovr}"


def injury_weeks(injury, prev, week, seed, pgid):
    """Project Mu's weeks out for an injured player: the previous mu_weeks_out minus the weeks since (same injury),
    else a seeded draw in Madden's MinInjuryDuration..MaxInjuryDuration."""
    if prev and prev.get("mu_weeks_out") is not None and prev.get("type") in (None, injury.get("type")):
        return max(0, int(prev["mu_weeks_out"]) - max(0, week - int(prev["week"])))
    lo = max(0, int(injury.get("min_weeks") or 0))
    hi = max(lo, int(injury.get("max_weeks") or lo))
    return lo + int(_u01(seed, "injury", week, pgid, injury.get("type")) * (hi - lo + 1))


def offer_contract(offer, signed_season, contract_type="STANDARD"):
    """A contracts_engine dict from a contract_offers row: the bonus prorated over min(years, 5), base split evenly."""
    years = max(1, int(offer.get("years") or 1))
    total = int(offer.get("total_value") or 0)
    bonus = min(total, int(offer.get("bonus") or 0))
    pro = ce._prorate(bonus, min(years, ce.CBA["PRORATION_MAX_YEARS"]))
    return {"length": years, "current_year": 0, "salary_by_year": ce._prorate(total - bonus, years),
            "bonus_by_year": pro + [0] * (years - len(pro)), "guaranteed": int(offer.get("guaranteed") or 0),
            "contract_type": contract_type, "signed_season": signed_season, "last_restructure_season": None,
            "drafted_round": None}


def contract_block(new, old=None, offset=0, slots=DEFAULT_CONTRACT_SLOTS):
    """The writer's contract block ({Length, CurrentYear 0, SalaryYearN, BonusYearN}, $10,000 units) for a contract
    dict `new` that starts `offset` league years after the save's current contract year; with offset 1 the old
    snapshot contract's current-year terms come first. Slots past Length are zeroed; years past `slots` are dropped.
    Returns (block, dropped_years)."""
    m = ce.to_madden(dict(new, current_year=0))
    sal = [m.get(f"SalaryYear{i}", 0) for i in range(new["length"])]
    bon = [m.get(f"BonusYear{i}", 0) for i in range(new["length"])]
    if offset > 0:
        cy = (old or {}).get("current_year") or 0
        o_sal, o_bon = (old or {}).get("salary_by_year") or [], (old or {}).get("bonus_by_year") or []
        pre_s = [o_sal[cy + i] if old and cy + i < len(o_sal) else 0 for i in range(offset)]
        pre_b = [o_bon[cy + i] if old and cy + i < len(o_bon) else 0 for i in range(offset)]
        sal, bon = pre_s + sal, pre_b + bon
    dropped = max(0, len(sal) - slots)
    sal, bon = sal[:slots], bon[:slots]
    block = {"Length": len(sal), "CurrentYear": 0}
    for i in range(slots):
        block[f"SalaryYear{i}"] = sal[i] if i < len(sal) else 0
    for i in range(slots):
        block[f"BonusYear{i}"] = bon[i] if i < len(bon) else 0
    return block, dropped


def block_hit(block, year):
    """Cap hit (units) of a writer contract block's year index."""
    if not block or year < 0 or year >= block.get("Length", 0):
        return 0
    return int(block.get(f"SalaryYear{year}", 0)) + int(block.get(f"BonusYear{year}", 0))


def snapshot_block(c):
    """A snapshot contract (units) as a writer block (CurrentYear kept), or None."""
    if not c or not c.get("length"):
        return None
    out = {"Length": c["length"], "CurrentYear": c.get("current_year") or 0}
    for i, v in enumerate(c.get("salary_by_year") or []):
        out[f"SalaryYear{i}"] = v or 0
    for i, v in enumerate(c.get("bonus_by_year") or []):
        out[f"BonusYear{i}"] = v or 0
    return out


def cap_change(block):
    """(this year's hit, next year's hit, this year's base) of a block at its CurrentYear, in units."""
    if not block:
        return 0, 0, 0
    cy = block.get("CurrentYear", 0)
    base = int(block.get(f"SalaryYear{cy}", 0)) if cy < block.get("Length", 0) else 0
    return block_hit(block, cy), block_hit(block, cy + 1), base


def release_cap(block):
    """(this year's room freed, next year's room freed, base removed) when a contract is released: cap hit minus
    dead money (every remaining bonus year accelerates), next year's whole hit."""
    if not block:
        return 0, 0, 0
    cy, n = block.get("CurrentYear", 0), block.get("Length", 0)
    dead = sum(int(block.get(f"BonusYear{i}", 0)) for i in range(cy, n))
    hit, nxt, base = cap_change(block)
    return hit - dead, nxt, base


def choose_picks(slots, boards, jr_picks, fallback):
    """Every pick of the draft: JR's war-room pick for that slot when he made one, else the owner's best available
    board player, else the best available in Madden's board order. slots: [{overall_pick, team_id}] in order; boards:
    {team_id: [pgid, ...] best first}; jr_picks: {overall_pick: pgid}; fallback: [pgid, ...]. Returns
    [{overall_pick, team_id, pgid, source: JR|BOARD|FALLBACK}]."""
    taken, out = set(), []
    for s in sorted(slots, key=lambda x: x["overall_pick"]):
        n, team = s["overall_pick"], s["team_id"]
        pick, source = jr_picks.get(n), "JR"
        if pick is None or pick in taken:
            pick = next((g for g in boards.get(team, []) if g not in taken), None)
            source = "BOARD"
        if pick is None:
            pick = next((g for g in fallback if g not in taken), None)
            source = "FALLBACK"
        if pick is None:
            break
        taken.add(pick)
        out.append({"overall_pick": n, "team_id": team, "pgid": pick, "source": source})
    return out


def _name_key(first, last=None):
    s = f"{first} {last}" if last is not None else str(first or "")
    return re.sub(r"[^a-z]", "", s.lower())


# --------------------------------------------------------------- the plan ----
class Plan:
    """Collects moves for one write and turns them into the save_writer document (see the module docstring)."""

    def __init__(self, snapshot, stage, season, week, wave, seed, teams, pgid_by_pid, state=None):
        self.snapshot, self.stage, self.season, self.week, self.wave, self.seed = (
            snapshot, stage, season, week, wave, seed)
        self.calendar = snapshot["season"]["calendar_year"]
        self.target = target_season(season, stage, week)
        self.offset = self.target - save_year(season, stage)
        self.teams = dict(teams)                                    # team_index -> team_id
        self.team_index = {v: k for k, v in self.teams.items()}     # team_id -> team_index
        self.pgid_by_pid = dict(pgid_by_pid)
        self.pid_by_pgid = {g: p for p, g in self.pgid_by_pid.items()}
        self.players = {p["pgid"]: p for p in snapshot.get("players") or [] if p.get("pgid") is not None}
        self.state = state
        self.slots = next((len(p["contract"]["salary_by_year"]) for p in self.players.values()
                           if p.get("contract") and p["contract"].get("salary_by_year")), DEFAULT_CONTRACT_SLOTS)
        # current view: pgid -> (status, team_index); IR view: pgid -> bool
        self.view = {g: (p.get("status"), p.get("team_index")) for g, p in self.players.items()}
        self.ir = {g: bool((p.get("injury") or {}).get("injured_reserve")) for g, p in self.players.items()}
        self.edits = {}            # pgid -> {"set": {field: value}, "prio": {field: priority}, "contract": block}
        self.release, self.trades, self.signs = [], [], []
        self.records = []
        self.depth = {}            # team_index -> {"slots": {...}, "source", "priority"}
        self.claimed = {}          # pgid -> (move, team_index, priority)
        self.contracts_written = {}  # pgid -> block
        self.free_rows = int((state or {}).get("spare_contract_rows") or 0)
        self.results = {}          # tag -> (ok, reason)
        self.deferred, self.warnings, self.errors = [], [], []
        self.counts = Counter()
        self.cap = defaultdict(lambda: [0, 0, 0])   # team_index -> [room, next-year room, base salary] change (units)
        self.db = {"pending": [], "charts": [], "injuries": [], "retirements": [], "offers": [],
                   "written_offer_ids": []}

    # -- lookups
    def pgid(self, player_id):
        return self.pgid_by_pid.get(player_id)

    def pid(self, pgid):
        return self.pid_by_pgid.get(pgid)

    def name(self, pgid):
        p = self.players.get(pgid) or {}
        return f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or f"pgid {pgid}"

    def warn(self, msg):
        log.warning(msg)
        self.warnings.append(msg)

    def note(self, tag, ok, reason=""):
        if tag is not None:
            self.results[tag] = (ok, reason)
        return ok

    def defer(self, what, pgid, reason, tag=None):
        self.deferred.append({"move": what, "pgid": pgid, "name": self.name(pgid) if pgid is not None else None,
                              "reason": reason})
        self.counts["deferred"] += 1
        return self.note(tag, False, reason)

    def step(self, name, fn, *args, **kwargs):
        """Runs one planning step; an exception becomes a plan error (the run then writes nothing)."""
        try:
            return fn(*args, **kwargs)
        except Exception as e:     # noqa: BLE001 - every step's failure is reported, not raised mid-plan
            log.exception("step %s failed", name)
            self.errors.append(f"{name}: {type(e).__name__}: {e}")
            return None

    # -- claims
    def _claim(self, pgid, move, team_index, priority, tag):
        have = self.claimed.get(pgid)
        if have:
            if have[0] == move and have[1] == team_index:
                return self.note(tag, True, f"same {move} already planned")
            return self.note(tag, False, f"{self.name(pgid)} already has a {have[0]} this write")
        self.claimed[pgid] = (move, team_index, priority)
        return True

    def roster_status(self, pgid):
        return self.view.get(pgid, (None, None))

    # -- moves
    def release_player(self, pgid, priority, reason, tag=None, expect_team=None):
        if pgid not in self.players:
            return self.note(tag, False, f"pgid {pgid} not in the save")
        if pgid in self.claimed and self.claimed[pgid][0] == "release":
            return self.note(tag, True, "already released this write")
        status, team = self.roster_status(pgid)
        if status not in ("Signed", "PracticeSquad") or team is None or team >= FREE_AGENT_TEAM:
            return self.note(tag, False, f"{self.name(pgid)} is {status}, not on a roster")
        if expect_team is not None and team != expect_team:
            return self.note(tag, False, f"{self.name(pgid)} is on team {team}, not {expect_team}")
        if not self._claim(pgid, "release", team, priority, tag):
            return False
        self.release.append(pgid)
        block = snapshot_block(self.players[pgid].get("contract")) if status == "Signed" else None
        if status == "Signed" and block:
            self.free_rows += 1
            room, nxt, base = release_cap(block)
            self._cap(team, room, nxt, -base)
        self.view[pgid] = ("FreeAgent", FREE_AGENT_TEAM)
        self.ir[pgid] = False
        self.counts["release"] += 1
        return self.note(tag, True, reason)

    def sign_player(self, pgid, team_index, lst, contract, priority, reason, tag=None, offset=None, block=None):
        """Queues a signing (resolved in finalize, after every release freed its contract row). `contract` is a
        contracts_engine dict for the league year (`offset` defaults to the stage's), `block` a ready writer block."""
        if pgid not in self.players:
            return self.note(tag, False, f"pgid {pgid} not in the save")
        if team_index is None or not 0 <= team_index < FREE_AGENT_TEAM:
            return self.note(tag, False, f"no team for {self.name(pgid)}")
        status, _ = self.roster_status(pgid)
        if status not in ("FreeAgent", "Draft"):
            return self.note(tag, False, f"{self.name(pgid)} is {status}, not a free agent or draft prospect")
        if lst not in ("Roster", "PracticeSquad"):
            return self.note(tag, False, f"list must be Roster or PracticeSquad, not {lst}")
        if lst == "Roster" and block is None:
            if not contract:
                return self.note(tag, False, f"a Roster signing of {self.name(pgid)} needs a contract")
            block, dropped = contract_block(contract, None, self.offset if offset is None else offset, self.slots)
            if dropped:
                self.warn(f"{self.name(pgid)}: {dropped} contract years past the save's {self.slots} slots dropped")
        if not self._claim(pgid, "sign", team_index, priority, tag):
            return False
        self.signs.append({"pgid": pgid, "team_index": team_index, "list": lst,
                           "block": block if lst == "Roster" else None, "priority": priority, "reason": reason,
                           "tag": tag})
        self.view[pgid] = ("Signed" if lst == "Roster" else "PracticeSquad", team_index)
        return True

    def trade_player(self, pgid, to_index, priority, reason, tag=None):
        if pgid not in self.players:
            return self.note(tag, False, f"pgid {pgid} not in the save")
        status, team = self.roster_status(pgid)
        if status not in ("Signed", "PracticeSquad") or team is None or team >= FREE_AGENT_TEAM:
            return self.note(tag, False, f"{self.name(pgid)} is {status}, not on a roster")
        if team == to_index:
            return self.note(tag, True, "already on that team")
        if not 0 <= to_index < FREE_AGENT_TEAM:
            return self.note(tag, False, f"no team {to_index}")
        if not self._claim(pgid, "trade", to_index, priority, tag):
            return False
        self.trades.append((team, to_index, pgid))
        if status == "Signed":
            hit, nxt, base = cap_change(snapshot_block(self.players[pgid].get("contract")))
            self._cap(team, hit, nxt, -base)
            self._cap(to_index, -hit, -nxt, base)
        self.view[pgid] = (status, to_index)
        self.counts["trade"] += 1
        return self.note(tag, True, reason)

    def set_fields(self, pgid, fields, priority, tag=None):
        """Player fields; a field a higher-priority source already set is kept."""
        if pgid not in self.players:
            return self.note(tag, False, f"pgid {pgid} not in the save")
        e = self.edits.setdefault(pgid, {"set": {}, "prio": {}, "contract": None})
        for k, v in fields.items():
            if k in e["prio"] and e["prio"][k] < priority:
                continue
            e["set"][k] = v
            e["prio"][k] = priority
        return self.note(tag, True)

    def set_contract(self, pgid, priority, reason, tag=None, contract=None, block=None, offset=None):
        """Rewrites a rostered player's own contract record (players edit "contract" block)."""
        if pgid not in self.players:
            return self.note(tag, False, f"pgid {pgid} not in the save")
        status, team = self.roster_status(pgid)
        old = self.players[pgid].get("contract")
        if status != "Signed" or not old:
            return self.note(tag, False, f"{self.name(pgid)} has no contract record ({status})")
        if pgid in self.claimed and self.claimed[pgid][0] in ("release", "sign"):
            return self.note(tag, False, f"{self.name(pgid)} already has a {self.claimed[pgid][0]} this write")
        e = self.edits.setdefault(pgid, {"set": {}, "prio": {}, "contract": None, "contract_prio": None})
        if e.get("contract") is not None and (e.get("contract_prio") or 0) <= priority:
            return self.note(tag, False, "a higher-priority contract change is already planned")
        if block is None:
            block, dropped = contract_block(contract, old, self.offset if offset is None else offset, self.slots)
            if dropped:
                self.warn(f"{self.name(pgid)}: {dropped} contract years past the save's {self.slots} slots dropped")
        if block == snapshot_block(old):
            return self.note(tag, True, "contract already as planned")
        prev = e.get("contract") or snapshot_block(old)
        h0, n0, b0 = cap_change(prev)
        h1, n1, b1 = cap_change(block)
        self._cap(team, h0 - h1, n0 - n1, b1 - b0)
        e["contract"], e["contract_prio"] = block, priority
        self.contracts_written[pgid] = block
        self.counts["contract"] += 1
        return self.note(tag, True, reason)

    def depth_chart(self, team_index, slots_pgids, source, priority, tag=None):
        have = self.depth.get(team_index)
        if have and have["priority"] <= priority:
            return self.note(tag, False, f"team {team_index} already has a {have['source']} depth chart this write")
        self.depth[team_index] = {"slots": slots_pgids, "source": source, "priority": priority}
        return self.note(tag, True)

    def add_records(self, records):
        self.records.extend(r for r in records or [] if r)

    # -- cap
    def _cap(self, team_index, room, nxt, salary):
        if team_index is None or team_index >= FREE_AGENT_TEAM:
            return
        self.cap[team_index][0] += room
        self.cap[team_index][1] += nxt
        self.cap[team_index][2] += salary

    # -- views
    def rosters(self):
        """{team_index: [pgid, ...]} on each Roster after the planned moves (queued signings included)."""
        out = defaultdict(list)
        for g, (status, team) in self.view.items():
            if status == "Signed" and team is not None and team < FREE_AGENT_TEAM:
                out[team].append(g)
        return out

    def on_ir(self, pgid):
        e = self.edits.get(pgid)
        if e and "IsInjuredReserve" in e["set"]:
            return bool(e["set"]["IsInjuredReserve"])
        return self.ir.get(pgid, False)

    # -- the document
    def finalize(self):
        """(edits document, summary). Resolves the queued signings against the free contract rows."""
        sign_out = []
        for s in self.signs:
            g = s["pgid"]
            if s["list"] == "Roster":
                if self.free_rows <= 0:
                    self.claimed.pop(g, None)
                    self.view[g] = (self.players[g].get("status"), self.players[g].get("team_index"))
                    self.defer("sign", g, "no free PlayerContract row (the writer can't create contract rows yet; "
                                          "each Roster release in the same write frees one)", s["tag"])
                    continue
                self.free_rows -= 1
                hit, nxt, base = cap_change(s["block"])
                self._cap(s["team_index"], -hit, -nxt, base)
                self.contracts_written[g] = s["block"]
                sign_out.append({"pgid": g, "team_index": s["team_index"], "list": "Roster", "contract": s["block"]})
                self.counts["sign_roster"] += 1
            else:
                sign_out.append({"pgid": g, "team_index": s["team_index"], "list": "PracticeSquad"})
                self.counts["sign_practice_squad"] += 1
            self.note(s["tag"], True, s["reason"])
        released = set(self.release)
        moved = {g: m for g, (m, _, _) in self.claimed.items()}
        players = []
        dropped = 0
        for g, e in sorted(self.edits.items()):
            if g in released:
                dropped += 1
                continue
            st = dict(e["set"])
            for k in MOVE_FIELDS.get(moved.get(g), ()):
                st.pop(k, None)
            block = e.get("contract")
            if g in self.contracts_written:
                st["PLYR_CAPSALARY"] = _clamp(block_hit(self.contracts_written[g], 0), CAPSALARY_RANGE)
            entry = {"pgid": g, "set": st}
            if block is not None and moved.get(g) != "sign":
                entry["contract"] = block
            if st or "contract" in entry:
                players.append(entry)
        for s in sign_out:     # a signed player's cap salary rides on a players edit too
            if s["list"] == "Roster" and not any(p["pgid"] == s["pgid"] for p in players):
                players.append({"pgid": s["pgid"],
                                "set": {"PLYR_CAPSALARY": _clamp(block_hit(s["contract"], 0), CAPSALARY_RANGE)}})
        if dropped:
            self.warn(f"{dropped} players edits dropped for released players (the writer refuses them)")
        doc = {}
        if players:
            doc["players"] = players
        records = list(self.records) + self._cap_records()
        if records:
            doc["records"] = records
        if self.release:
            doc["release"] = list(self.release)
        if sign_out:
            doc["sign"] = sign_out
        trades = defaultdict(list)
        for frm, to, g in self.trades:
            trades[(frm, to)].append(g)
        if trades:
            doc["trade"] = [{"from_team_index": f, "to_team_index": t, "pgids": gs, "picks": []}
                            for (f, t), gs in sorted(trades.items())]
        charts = self._charts()
        if charts:
            doc["depthCharts"] = charts
        summary = dict(self.counts)
        summary.update({"stage": self.stage, "wave": self.wave, "season": self.season, "week": self.week,
                        "target_season": self.target, "players_edited": len(players), "records": len(records),
                        "depth_charts": len(charts), "deferred": len(self.deferred), "warnings": len(self.warnings),
                        "errors": len(self.errors), "free_contract_rows_left": self.free_rows,
                        "written_offer_ids": list(self.db["written_offer_ids"])})
        return doc, summary

    def _charts(self):
        rosters = self.rosters()
        out = []
        for team, d in sorted(self.depth.items()):
            on = set(rosters.get(team, []))
            slots = {}
            for slot, pgids in d["slots"].items():
                keep, seen = [], set()
                for g in pgids:
                    if g in on and g not in seen:
                        keep.append(g)
                        seen.add(g)
                slots[slot] = keep[:6]
            if any(slots.values()):
                out.append({"team_index": team, "slots": slots})
        return out

    def _cap_records(self):
        cap = {t: v for t, v in self.cap.items() if any(v)}
        if not cap:
            return []
        if not self.state or not self.state.get("team_table_id"):
            self.warn("no league state from the save: team cap fields not written")
            return []
        rows = {t["team_index"]: t for t in self.state.get("teams") or []}
        out = []
        for team, (room, nxt, salary) in sorted(cap.items()):
            t = rows.get(team)
            if not t or (room == 0 and nxt == 0 and salary == 0):
                continue
            st = {}
            if t.get("TEAM_SALARY") is not None and salary:
                st["TEAM_SALARY"] = _clamp(t["TEAM_SALARY"] + salary, TEAM_SALARY_RANGE)
            for key, delta in (("SalCapCapRoom", room), ("SalCapSpendingMoney", room), ("SalCapNextYearCapRoom", nxt)):
                if t.get(key) is not None and delta:
                    st[key] = _clamp(t[key] + delta * CAP_ROOM_UNIT, CAP_ROOM_RANGE)
            if st:
                out.append({"table": "Team", "tableId": self.state["team_table_id"], "row": t["row"], "set": st})
        self.counts["teams_cap_written"] = len(out)
        return out


# ------------------------------------------------------- dependencies (DB) ----
class Deps:
    """Everything plan_stage and run read or call outside this module; tests pass fakes for any of them."""

    # -- identity
    def team_ids(self, cur, snapshot):
        return ls.team_ids(cur, snapshot)

    def pgid_map(self, cur):
        cur.execute("SELECT id, madden_pgid FROM players WHERE madden_pgid IS NOT NULL")
        return {int(p): int(g) for p, g in cur.fetchall()}

    def user_team_ids(self, cur):
        try:
            from db.franchises import active_entry
            _, entry = active_entry()
            abbrs = [a.upper() for a in ((entry or {}).get("user_teams") or [])]
        except Exception:      # noqa: BLE001 - no registry: no user teams
            return set()
        if not abbrs:
            return set()
        cur.execute("SELECT id FROM teams WHERE upper(abbreviation) = ANY(%s)", (abbrs,))
        return {r[0] for r in cur.fetchall()}

    # -- JR's side
    def pending_moves(self, cur):
        cur.execute("SELECT id, team_id, kind, payload FROM franchise_pending_moves WHERE status = 'PENDING' "
                    "ORDER BY id")
        out = []
        for i, t, k, p in cur.fetchall():
            out.append({"id": i, "team_id": t, "kind": k, "payload": json.loads(p) if isinstance(p, str) else (p or {})})
        return out

    def accepted_offers(self, cur, season):
        cur.execute("SELECT summary FROM league_office_runs WHERE status = 'WRITTEN'")
        done = set()
        for (s,) in cur.fetchall():
            s = json.loads(s) if isinstance(s, str) else (s or {})
            done.update(s.get("written_offer_ids") or [])
        cur.execute("""SELECT id, kind, player_id, team_id, years, total_value, bonus, guaranteed FROM contract_offers
                       WHERE status = 'ACCEPTED' AND from_user AND season >= %s ORDER BY id""", (season,))
        keys = ("id", "kind", "player_id", "team_id", "years", "total_value", "bonus", "guaranteed")
        return [dict(zip(keys, r)) for r in cur.fetchall() if r[0] not in done]

    # -- drift
    def drift(self, snapshot, cur, season, week):
        from pipeline import drift_check
        return drift_check.diff(snapshot, cur, season, week)

    def stored_contracts(self, cur, season):
        """{player_id: (contract dict, first season, team_id)} from player_contracts (ACTIVE, else RELEASED)."""
        out = {}
        for pid, row in te.fetch_contracts(cur, season).items():
            c, start = te.contract_from_db(row)
            out[pid] = (c, start, row.get("team_id"), row.get("status"))
        return out

    # -- engines
    def resign_decisions(self, cur, season, seed):
        return te.resign_decisions(cur, season, seed)

    def cpu_offers(self, cur, season, stage, wave, seed):
        return te.cpu_offers(cur, season, stage, wave, seed)

    def free_agent_pool(self, cur, season, target):
        return te.free_agents(te.fetch_league(cur, season, target))

    def run_wave(self, players, offers, wave, seed):
        from engines import free_agency_engine as fae
        return fae.run_wave(players, offers, wave, seed)

    def signings_from_decisions(self, decisions, offers):
        return te.signings_from_decisions(decisions, offers)

    def market_needs(self, cur, season, target):
        return te.market_needs(te.fetch_league(cur, season, target))

    def udfa_market(self, prospects, needs, seed):
        from engines import free_agency_engine as fae
        return fae.udfa_market(prospects, needs, seed)

    def cutdown(self, cur, season, seed, limit=ROSTER_LIMIT):
        return te.cutdown(cur, season, seed, limit)

    def practice_squad(self, cur, season, seed):
        return te.practice_squad(cur, season, seed)

    def waivers(self, cur, season, week, released, seed):
        return te.waivers(cur, season, week, released, seed)

    def form_edits(self, cur, snapshot, season, week, store=True):
        from engines import form_engine
        return form_engine.form_edits(cur, snapshot, season, week, store=store)

    def pass_edits(self, cur, snapshot, season):
        from engines import progression_save
        return progression_save.pass_edits(cur, snapshot, season)

    def in_season_checkpoint(self):
        """progression.in_season_checkpoint when the progression engine has it, else None."""
        from engines import progression
        return getattr(progression, "in_season_checkpoint", None)

    def rating_edits(self, rows, snapshot, pgid_by_player):
        from engines import progression_save
        return progression_save.rating_edits(rows, snapshot, pgid_by_player)

    def slider_edits(self, snapshot):
        from engines import progression_save
        return progression_save.slider_edits(snapshot)

    def staff_depth_chart(self, roster, scheme, injuries):
        from engines import depth_chart_engine as dce
        return dce.staff_depth_chart(roster, scheme, injuries)

    def team_schemes(self, cur, season):
        cur.execute("SELECT to_regclass('coordinator_tenures') IS NOT NULL")
        if not cur.fetchone()[0]:
            return {}
        cur.execute("""SELECT ct.team_id, ct.role, c.scheme FROM coordinator_tenures ct
                       JOIN coordinators c ON c.id = ct.coordinator_id
                       WHERE ct.start_season <= %s AND COALESCE(ct.end_season, 9999) >= %s
                       ORDER BY ct.start_season, ct.id""", (season, season))
        out = defaultdict(dict)
        for team, role, scheme in cur.fetchall():
            if scheme:
                out[team]["offense" if role == "OC" else "defense"] = scheme
        return dict(out)

    def jr_charts(self, cur, season):
        """{team_id: {slot: [player_id, ...]}}: each team's newest stored JR chart of the season."""
        cur.execute("""SELECT team_id, week, position_slot, depth, player_id FROM depth_charts
                       WHERE season = %s AND source = 'JR' ORDER BY team_id, week, position_slot, depth""", (season,))
        rows = cur.fetchall()
        newest = {}
        for team, wk, *_ in rows:
            newest[team] = max(newest.get(team, wk), wk)
        out = defaultdict(dict)
        for team, wk, slot, _, pid in rows:
            if wk == newest[team] and pid is not None:
                out[team].setdefault(slot, []).append(pid)
        return dict(out)

    def previous_injuries(self, cur, season, week):
        """{player_id: {week, mu_weeks_out, type}}: each player's newest injuries row before `week` with a
        Project Mu duration."""
        cur.execute("""SELECT DISTINCT ON (player_id) player_id, week, mu_weeks_out, injury_type FROM injuries
                       WHERE season = %s AND week < %s AND mu_weeks_out IS NOT NULL AND player_id IS NOT NULL
                       ORDER BY player_id, week DESC""", (season, week))
        return {pid: {"week": wk, "mu_weeks_out": mu, "type": None} for pid, wk, mu, _ in cur.fetchall()}

    def released_last_run(self, cur):
        """pgids the newest WRITTEN run released."""
        cur.execute("SELECT edits FROM league_office_runs WHERE status = 'WRITTEN' ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        if not row:
            return []
        edits = json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
        return list(edits.get("release") or [])

    def ps_without_contract(self, cur, season):
        """player ids Project Mu has on a team with no ACTIVE contract (its practice-squad players)."""
        cur.execute("""SELECT ps.player_id FROM player_seasons ps WHERE ps.season = %s AND ps.team_id IS NOT NULL
                       AND ps.released_week IS NULL AND NOT EXISTS (SELECT 1 FROM player_contracts c
                       WHERE c.player_id = ps.player_id AND c.season = %s AND c.status = 'ACTIVE')""", (season, season))
        return {r[0] for r in cur.fetchall()}

    def kept_retirees(self, cur, season):
        cur.execute("SELECT player_id FROM retirement_decisions WHERE season = %s AND madden_retired AND mu_keep",
                    (season,))
        return {r[0] for r in cur.fetchall()}

    def draft_inputs(self, cur, season):
        """The coming draft: slots [{overall_pick, team_id}], boards {team_id: [(name, position), ...]}, JR's picks
        {overall_pick: (name, position)}."""
        ds = season + 1
        cur.execute("""SELECT overall_pick, owner_team_id FROM draft_picks WHERE draft_season = %s
                       AND overall_pick IS NOT NULL ORDER BY overall_pick""", (ds,))
        slots = [{"overall_pick": n, "team_id": t} for n, t in cur.fetchall()]
        cur.execute("SELECT max(step) FROM scout_team_boards WHERE draft_season = %s", (ds,))
        step = cur.fetchone()[0]
        boards = defaultdict(list)
        if step is not None:
            cur.execute("""SELECT b.team_id, d.full_name, d.position FROM scout_team_boards b
                           JOIN scout_prospects sp ON sp.draft_season = b.draft_season AND sp.prospect_key = b.prospect_key
                           JOIN draft_prospects d ON d.id = sp.prospect_id
                           WHERE b.draft_season = %s AND b.step = %s ORDER BY b.team_id, b.team_rank""", (ds, step))
            for team, name, pos in cur.fetchall():
                boards[team].append((name, pos))
        cur.execute("""SELECT p.overall_pick, d.full_name, d.position FROM scout_draft_picks p
                       JOIN scout_prospects sp ON sp.draft_season = p.draft_season AND sp.prospect_key = p.prospect_key
                       JOIN draft_prospects d ON d.id = sp.prospect_id
                       WHERE p.draft_season = %s AND p.choice_source = 'JR_TEAM'""", (ds,))
        jr = {n: (name, pos) for n, name, pos in cur.fetchall()}
        return slots, dict(boards), jr

    # -- the save
    def league_state(self, save_path):
        res = subprocess.run(["node", SETUP, "state", save_path], capture_output=True, text=True, timeout=600)
        if res.returncode != 0:
            raise RuntimeError(f"league_setup.mjs state failed: {res.stderr.strip()[-500:]}")
        return json.loads(res.stdout)

    def write_save(self, save_path, edits_path, out_path):
        """(ok, report text) from save_writer.mjs (plan, write to out_path, read back)."""
        res = subprocess.run(["node", WRITER, save_path, edits_path, "--out", out_path], capture_output=True,
                             text=True, timeout=1800)
        return res.returncode == 0, (res.stderr or res.stdout).strip()

    def read_snapshot(self, save_path):
        res = subprocess.run(["node", READER, save_path], capture_output=True, text=True, timeout=1800)
        if res.returncode != 0:
            raise RuntimeError(f"pc_save_reader.mjs failed: {res.stderr.strip()[-500:]}")
        return json.loads(res.stdout)

    def madden_running(self):
        from pipeline.gpu_queue.process_check import is_madden_running
        return is_madden_running()


# -------------------------------------------------------------- the steps ----
def _payload_pgids(plan, ids):
    out = []
    for pid in ids or []:
        g = plan.pgid(pid)
        if g is None:
            raise ValueError(f"player {pid} has no madden_pgid")
        out.append(g)
    return out


def step_pending(plan, moves):
    """JR's queued moves (priority 0). Each move's result goes to plan.db['pending'] after finalize."""
    for m in moves:
        tag = ("pending", m["id"])
        plan.db["pending"].append({"id": m["id"], "tag": tag, "kind": m["kind"], "team_id": m["team_id"],
                                   "payload": m["payload"]})
        k, p = m["kind"], m["payload"] or {}
        team = plan.team_index.get(m["team_id"])
        if team is None:
            plan.note(tag, False, f"team {m['team_id']} not in the save")
            continue
        try:
            if k == "DEPTH_CHART":
                slots = {s: _payload_pgids(plan, ids) for s, ids in (p.get("slots") or {}).items()}
                plan.depth_chart(team, slots, "JR", PRIORITY_JR, tag)
                continue
            g = plan.pgid(p.get("player_id"))
            if g is None and k != "TRADE":
                plan.note(tag, False, f"player {p.get('player_id')} has no madden_pgid")
                continue
            if k == "RELEASE":
                plan.release_player(g, PRIORITY_JR, "JR's release", tag, expect_team=team)
            elif k == "SIGN":
                c = p.get("contract") or (offer_contract(p, plan.target) if p.get("years") else None)
                plan.sign_player(g, team, p.get("list") or "Roster", c, PRIORITY_JR, "JR's signing", tag)
            elif k == "TRADE":
                step_pending_trade(plan, team, p, tag)
            elif k in ("IR_PLACE", "IR_ACTIVATE"):
                status, on = plan.roster_status(g)
                if status != "Signed" or on != team:
                    plan.note(tag, False, f"{plan.name(g)} is not on the team's active roster")
                else:
                    plan.set_fields(g, {"IsInjuredReserve": k == "IR_PLACE"}, PRIORITY_JR, tag)
                    plan.counts["ir_place" if k == "IR_PLACE" else "ir_activate"] += 1
            elif k in ("EXTENSION", "TAG", "OPTION"):
                if not p.get("contract"):
                    plan.note(tag, False, "no contract in the move")
                else:
                    plan.set_contract(g, PRIORITY_JR, f"JR's {k.lower()}", tag, contract=p["contract"])
            elif k == "RESTRUCTURE":
                old = (plan.players.get(g) or {}).get("contract")
                if not old:
                    plan.note(tag, False, "no contract to restructure")
                    continue
                c = ce.from_snapshot(old)
                new = ce.restructure(c, c["current_year"], int(p.get("amount") or 0))
                block = snapshot_block({"length": new["length"], "current_year": new["current_year"],
                                        "salary_by_year": [int(round(v / ce.MONEY_UNIT)) for v in new["salary_by_year"]],
                                        "bonus_by_year": [int(round(v / ce.MONEY_UNIT)) for v in new["bonus_by_year"]]})
                plan.set_contract(g, PRIORITY_JR, "JR's restructure", tag, block=block)
            elif k in ("PS_PROMOTE", "PS_DEMOTE"):
                plan.note(tag, False, "the save writer has no practice-squad promote / demote move yet")
            else:
                plan.note(tag, False, f"unknown move kind {k}")
        except (ValueError, KeyError, TypeError) as e:
            plan.note(tag, False, f"bad payload: {e}")


def step_pending_trade(plan, team, p, tag):
    """A TRADE payload: {other_team_id, send: {players, picks}, receive: {players, picks}} (or {to_team_id,
    players}). Picks need the save's DraftPick rows, which the league office doesn't map yet: refused."""
    other = plan.team_index.get(p.get("other_team_id") or p.get("to_team_id"))
    send = p.get("send") or {"players": p.get("players") or [], "picks": p.get("picks") or []}
    recv = p.get("receive") or {"players": [], "picks": []}
    if other is None:
        plan.note(tag, False, "no other team in the trade")
        return
    if send.get("picks") or recv.get("picks"):
        plan.note(tag, False, "traded draft picks aren't mapped to the save's DraftPick rows yet")
        return
    out_g, in_g = _payload_pgids(plan, send.get("players")), _payload_pgids(plan, recv.get("players"))
    bad = [g for g in out_g if plan.roster_status(g)[1] != team] + [g for g in in_g if plan.roster_status(g)[1] != other]
    if bad:
        plan.note(tag, False, f"not on the trading teams: {', '.join(plan.name(g) for g in bad)}")
        return
    ok = all([plan.trade_player(g, other, PRIORITY_JR, "JR's trade") for g in out_g]
             + [plan.trade_player(g, team, PRIORITY_JR, "JR's trade") for g in in_g])
    plan.note(tag, ok, "JR's trade" if ok else "part of the trade could not be planned")


def step_accepted_offers(plan, offers):
    """contract_offers JR's targets accepted (from_user)."""
    for o in offers:
        tag = ("offer", o["id"])
        g, team = plan.pgid(o["player_id"]), plan.team_index.get(o["team_id"])
        if g is None or team is None:
            plan.warn(f"offer {o['id']}: player or team not in the save")
            continue
        kind = o["kind"]
        if kind in ("FREE_AGENT", "UDFA"):
            ok = plan.sign_player(g, team, "Roster", offer_contract(o, plan.target, "UDFA" if kind == "UDFA" else
                                  "STANDARD"), PRIORITY_OFFER, f"JR's accepted {kind.lower()} offer", tag)
        else:
            ok = plan.set_contract(g, PRIORITY_OFFER, f"JR's accepted {kind.lower()}", tag,
                                   contract=offer_contract(o, plan.target))
        plan.db["offers"].append({"tag": tag, "id": o["id"], "user": True})


def step_drift(plan, report, stored, kept_retirees=frozenset()):
    """Corrective edits for drift_check's report: Project Mu's version is written back."""
    ids = plan.pid_by_pgid
    by_pid = {pid: g for g, pid in ids.items()}
    retired_pgids = {g for g, p in plan.players.items() if p.get("status") == "Retired"}
    missing = []
    for e in report.get("rosters") or []:
        g = e.get("pgid") or by_pid.get(e.get("player_id"))
        if g is None or g not in plan.players:
            missing.append(e.get("player_id"))
            continue
        if e.get("field") == "player" or g in retired_pgids:
            continue                         # unknown to Project Mu, or a retirement (handled by its own step)
        exp, found = e.get("expected"), e.get("found")
        exp_i = plan.team_index.get(exp) if exp is not None else None
        found_i = plan.team_index.get(found) if found is not None else None
        if exp is None and found is not None:
            if plan.stage not in IN_SEASON and (plan.players[g].get("years_pro") or 0) == 0:
                plan.counts["drift_rookies_kept"] += 1      # Madden's draft signings: not undone here
                continue
            plan.release_player(g, PRIORITY_DRIFT, "drift: Madden signed him")
        elif exp is not None and found is None:
            st = stored.get(e.get("player_id"))
            if st and st[1] + st[0]["length"] - 1 < plan.target:
                continue                     # his deal ended: an expiry, not a cut
            if st and st[3] == "ACTIVE":
                c, start = st[0], st[1]
                y = save_year(plan.season, plan.stage) - start
                if not 0 <= y < c["length"]:
                    continue
                rest = dict(c, length=c["length"] - y, current_year=0, salary_by_year=c["salary_by_year"][y:],
                            bonus_by_year=c["bonus_by_year"][y:])
                plan.sign_player(g, exp_i, "Roster", rest, PRIORITY_DRIFT, "drift: Madden released him", offset=0)
            else:
                plan.sign_player(g, exp_i, "PracticeSquad", None, PRIORITY_DRIFT, "drift: Madden released him")
        elif exp_i is not None and found_i is not None and exp_i != found_i:
            plan.trade_player(g, exp_i, PRIORITY_DRIFT, "drift: Madden traded him")
    if missing:
        plan.warn(f"drift: {len(missing)} rostered players Project Mu stores aren't in the save; not restored "
                  f"(first: {missing[:5]})")
    contract_pids = {e.get("player_id") for e in report.get("contracts") or [] if e.get("player_id") is not None}
    for pid in sorted(contract_pids):
        g = by_pid.get(pid)
        st = stored.get(pid)
        if g is None or not st or st[3] != "ACTIVE" or plan.roster_status(g)[0] != "Signed":
            continue
        c = st[0]
        cy = c["current_year"] + (save_year(plan.season, plan.stage) - plan.calendar)
        if not 0 <= cy < c["length"]:
            continue
        m = ce.to_madden(dict(c, current_year=cy))
        block = {"Length": m["Length"], "CurrentYear": cy}
        for i in range(plan.slots):
            block[f"SalaryYear{i}"] = m.get(f"SalaryYear{i}", 0)
        for i in range(plan.slots):
            block[f"BonusYear{i}"] = m.get(f"BonusYear{i}", 0)
        old = snapshot_block(plan.players[g].get("contract"))
        if old and all(old.get(k, 0) == v for k, v in block.items()):
            continue
        plan.set_contract(g, PRIORITY_DRIFT, "drift: contract restored", block=block)
    charts = defaultdict(dict)
    for e in report.get("depth_charts") or []:
        team = plan.team_index.get(e.get("team_id"))
        if team is None:
            continue
        charts[team][e["field"]] = [by_pid.get(p) for p in e.get("expected") or [] if by_pid.get(p) is not None]
    for team, slots in charts.items():
        plan.depth_chart(team, slots, "DRIFT", PRIORITY_DRIFT)
    rows = defaultdict(dict)
    for e in report.get("ratings") or []:
        if e.get("pgid") is None or not isinstance(e.get("expected"), (int, float)) or e.get("found") is None:
            continue
        rows[e["pgid"]][e["field"]] = int(e["expected"]) - int(e["found"])
    plan.counts["drift_entries"] = sum(len(v or []) for v in report.values() if isinstance(v, list))
    return [{"pgid": g, "ratings_diff": d} for g, d in rows.items()]


def step_injuries(plan, prev, seed):
    """Injury durations, IR placement and activation (REGULAR_WEEK / PLAYOFF_WEEK)."""
    for g, p in plan.players.items():
        status, team = plan.roster_status(g)
        if status != "Signed" or team is None or team >= FREE_AGENT_TEAM:
            continue
        inj = p.get("injury") or {}
        injured = inj.get("status") not in HEALTHY
        on_ir = bool(inj.get("injured_reserve"))
        pid = plan.pid(g)
        if injured:
            weeks = injury_weeks(inj, prev.get(pid), plan.week, seed, g)
            fields = {"MinInjuryDuration": weeks, "MaxInjuryDuration": weeks}
            if weeks >= IR_MIN_WEEKS and not on_ir:
                fields["IsInjuredReserve"] = True
                plan.counts["ir_place"] += 1
            if fields:
                plan.set_fields(g, fields, PRIORITY_STAGE)
            plan.counts["injury_durations"] += 1
            if pid is not None:
                plan.db["injuries"].append({"player_id": pid, "mu_weeks_out": weeks})
        elif on_ir:
            plan.set_fields(g, {"IsInjuredReserve": False}, PRIORITY_STAGE)
            plan.counts["ir_activate"] += 1


def step_legality(plan, limit, protect=frozenset()):
    """Releases the lowest-OVR healthy non-starters (ties: biggest cut savings) until each team has at most `limit`
    active (non-IR) players."""
    for team, pgids in sorted(plan.rosters().items()):
        active = [g for g in pgids if not plan.on_ir(g)]
        excess = len(active) - limit
        if excess <= 0:
            continue
        starters = set()
        chart = plan.depth.get(team)
        if chart:
            for ids in chart["slots"].values():
                starters.update(ids[:1])
        cands = []
        for g in active:
            p = plan.players[g]
            if g in starters or g in protect or g in plan.claimed or (p.get("injury") or {}).get("status") not in HEALTHY:
                continue
            room, _, _ = release_cap(snapshot_block(p.get("contract")))
            cands.append((p.get("overall") or 0, -room, g))
        for _, _, g in sorted(cands)[:excess]:
            plan.release_player(g, PRIORITY_STAGE, f"roster legality (at most {limit} active)")
            plan.counts["legality_release"] += 1


def step_coach_charts(plan, deps, cur, user_teams, jr_charts, schemes):
    """Coach depth charts for every team without a chart this write (JR's stored chart for a user team)."""
    rosters = plan.rosters()
    for team_index, team_id in sorted(plan.teams.items()):
        if team_index in plan.depth and plan.depth[team_index]["source"] == "JR":
            continue
        if team_id in user_teams and team_id in jr_charts:
            slots = {s: [plan.pgid(p) for p in ids if plan.pgid(p) is not None] for s, ids in jr_charts[team_id].items()}
            plan.depth_chart(team_index, slots, "JR_STORED", PRIORITY_JR)
            continue
        roster, injuries = [], {}
        for g in rosters.get(team_index, []):
            p = plan.players[g]
            r = p.get("ratings") or {}
            roster.append({"player_id": g, "position": p.get("position"), "archetype": p.get("archetype"),
                           "overall": p.get("overall"), "ratings": r, "age": p.get("age"), "stamina": r.get("Stamina")})
            inj = p.get("injury") or {}
            out = plan.on_ir(g) or inj.get("status") not in HEALTHY
            injuries[g] = {"out": out, "weeks": 1 if out else 0}
        if not roster:
            continue
        chart = deps.staff_depth_chart(roster, schemes.get(team_id) or {}, injuries)
        # MU_COACH outranks drift's restored chart: it is this week's coach decision.
        have = plan.depth.get(team_index)
        if have and have["source"] == "DRIFT":
            del plan.depth[team_index]
        if plan.depth_chart(team_index, chart, "MU_COACH", PRIORITY_STAGE):
            plan.db["charts"].append({"team_index": team_index, "team_id": team_id, "source": "MU_COACH"})


def step_merge_doc(plan, doc, priority):
    """A players / records document from an engine (form, progression pass) merged at `priority`."""
    for e in (doc or {}).get("players") or []:
        plan.set_fields(e["pgid"], e.get("set") or {}, priority)
    plan.add_records((doc or {}).get("records"))


def step_moves(plan, moves, priority, label):
    """transactions_engine move dicts -> writer moves."""
    for m in moves or []:
        g = plan.pgid(m.get("player_id"))
        kind = m.get("kind")
        team = plan.team_index.get(m.get("team_id"))
        if g is None:
            plan.warn(f"{label}: player {m.get('player_id')} has no madden_pgid ({kind})")
            continue
        plan.counts[f"{label}:{kind}"] += 1
        if kind == "RELEASE":
            plan.release_player(g, priority, m.get("reason") or label)
        elif kind in ("SIGN", "WAIVER_CLAIM"):
            lst = m.get("list") or "Roster"
            plan.sign_player(g, team, lst, m.get("contract"), priority, m.get("reason") or label,
                             tag=("move", label, m.get("player_id")))
        elif kind in ("CONTRACT", "EXTENSION", "TAG", "OPTION"):
            if m.get("contract"):
                plan.set_contract(g, priority, m.get("reason") or label, contract=m["contract"])
        elif kind == "TRADE":
            plan.trade_player(g, team, priority, m.get("reason") or label)
        elif kind in ("PS_PROMOTE", "PS_DEMOTE"):
            plan.defer(kind, g, "the save writer has no practice-squad promote / demote move yet")
        elif kind == "LET_GO":
            pass                               # Madden frees expiring deals at FAPREP
        else:
            plan.warn(f"{label}: move kind {kind} not handled")


def step_retirements(plan, seed):
    """Madden's retirements: recorded, and un-retired (as free agents) where keep_retired says so."""
    for g, p in sorted(plan.players.items()):
        if p.get("status") != "Retired":
            continue
        keep, reason = keep_retired(p, plan.season, seed)
        pid = plan.pid(g)
        if pid is None:
            plan.warn(f"{plan.name(g)}: retired but not in players (no madden_pgid); not recorded")
            continue
        plan.db["retirements"].append({"player_id": pid, "mu_keep": keep, "reason": reason})
        plan.counts["retired_by_madden"] += 1
        if keep:
            plan.set_fields(g, {"ContractStatus": "FreeAgent", "TeamIndex": FREE_AGENT_TEAM}, PRIORITY_STAGE)
            plan.view[g] = ("Retired", None)   # signable only in a later write
            plan.counts["unretired"] += 1


def step_restore_retirees(plan, kept, stored):
    """Signs a kept retiree (now a free agent) back to his old team when his stored contract runs into the league
    year."""
    for pid in sorted(kept):
        g = plan.pgid(pid)
        st = stored.get(pid)
        if g is None or not st or plan.roster_status(g)[0] != "FreeAgent":
            continue
        c, start, team_id = st[0], st[1], st[2]
        y = plan.target - start
        if team_id not in plan.team_index or not 0 <= y < c["length"]:
            continue
        rest = dict(c, length=c["length"] - y, current_year=0, salary_by_year=c["salary_by_year"][y:],
                    bonus_by_year=c["bonus_by_year"][y:])
        plan.sign_player(g, plan.team_index[team_id], "Roster", rest, PRIORITY_STAGE, "kept retiree back to his team")


def step_promoted_ps(plan, ps_ids):
    """Contracts for the practice-squad players Madden signed to the active roster at the offseason start."""
    cap = league_cap(plan.state)
    for pid in sorted(ps_ids):
        g = plan.pgid(pid)
        if g is None or plan.roster_status(g)[0] != "Signed" or not (plan.players[g].get("contract")):
            continue
        accrued = max(0, (plan.players[g].get("years_pro") or 0) + 1)
        salary = ce.minimum_salary(accrued, cap)
        c = te.make_contract(salary, 1, "MINIMUM", plan.target, bonus_share=0.0, guar_share=0.0)
        if plan.set_contract(g, PRIORITY_STAGE, "practice-squad player Madden promoted: 1-year minimum", contract=c):
            plan.counts["ps_promoted_contracts"] += 1


def league_cap(state):
    """The league cap in dollars: SalaryInfo.TeamSalaryCap ($10,000 units) when known, else the reference cap."""
    v = (state or {}).get("salary_cap")
    return int(v) * ce.MONEY_UNIT if v else ce.CBA["REFERENCE_CAP"]


def step_draft(plan, slots, boards, jr, seed):
    """Every pick from the team boards (JR's from the war room) signed with a rookie deal."""
    prospects = {g: p for g, p in plan.players.items() if p.get("status") == "Draft"}
    by_name = defaultdict(list)
    for g, p in prospects.items():
        by_name[_name_key(p.get("first_name"), p.get("last_name"))].append(g)

    def find(name, pos):
        hits = by_name.get(_name_key(name), [])
        if len(hits) > 1 and pos:
            hits = [g for g in hits if ls.position(prospects[g].get("position")) == pos] or hits
        return hits[0] if hits else None

    board_pgids = {t: [g for g in (find(n, ps) for n, ps in rows) if g is not None] for t, rows in boards.items()}
    jr_pgids = {n: find(*v) for n, v in jr.items() if find(*v) is not None}
    order = {d["pgid"]: (d.get("initial_draft_rank") if d.get("initial_draft_rank") is not None else 10 ** 6)
             for d in plan.snapshot.get("draft_class") or []}
    fallback = sorted(prospects, key=lambda g: (order.get(g, 10 ** 6), -(prospects[g].get("overall") or 0), g))
    picks = choose_picks(slots, board_pgids, jr_pgids, fallback)
    cap = league_cap(plan.state)
    for pk in picks:
        team = plan.team_index.get(pk["team_id"])
        c = ce.rookie_contract(min(max(1, pk["overall_pick"]), ce.CBA["DRAFT_PICKS"]), plan.target, cap)
        plan.sign_player(pk["pgid"], team, "Roster", c, PRIORITY_STAGE,
                         f"drafted {pk['overall_pick']} ({pk['source'].lower()})")
        plan.counts[f"draft_{pk['source'].lower()}"] += 1
    return picks


def step_udfa(plan, deps, cur, seed):
    from engines.gm_direction import position_group
    prospects = []
    for g, p in plan.players.items():
        undrafted = p.get("status") == "Draft" or (p.get("status") == "FreeAgent" and (p.get("years_pro") or 0) == 0)
        pid = plan.pid(g)
        if not undrafted or pid is None:
            continue
        prospects.append({"id": pid, "player_id": pid, "group": position_group(ls.position(p.get("position"))),
                          "ovr": p.get("overall"), "age": p.get("age"), "pos": ls.position(p.get("position"))})
    if not prospects:
        return
    needs = deps.market_needs(cur, plan.season, plan.target)
    for d in deps.udfa_market(prospects, needs, seed) or []:
        if d.get("status") != "ACCEPT":
            continue
        o = d.get("offer") or {}
        g = plan.pgid(d.get("player_id"))
        if g is None:
            continue
        plan.sign_player(g, plan.team_index.get(d.get("team_id")), "Roster", offer_contract(o, plan.target, "UDFA"),
                         PRIORITY_STAGE, "undrafted free agent")
        plan.counts["udfa_signings"] += 1


def step_free_agency(plan, deps, cur, seed):
    wave = plan.wave or 1
    offers = deps.cpu_offers(cur, plan.season, FREE_AGENCY, wave, seed) or []
    pool = deps.free_agent_pool(cur, plan.season, plan.target) or []
    decisions = deps.run_wave(pool, offers, wave, seed) or []
    for d in decisions:
        o = offers[d["offer_index"]] if d.get("offer_index") is not None else (d.get("offer") or {})
        if o.get("offer_id") is not None and d.get("status") in ("REJECT", "COUNTER"):
            plan.db["offers"].append({"tag": None, "id": o["offer_id"],
                                      "status": "REJECTED" if d["status"] == "REJECT" else "COUNTERED"})
    for m in deps.signings_from_decisions(decisions, offers) or []:
        g = plan.pgid(m.get("player_id"))
        if g is None:
            plan.warn(f"free agency: player {m.get('player_id')} has no madden_pgid")
            continue
        tag = ("cpu_offer", m.get("offer_id"))
        plan.sign_player(g, plan.team_index.get(m.get("team_id")), "Roster", m.get("contract"), PRIORITY_STAGE,
                         "free agency", tag)
        if m.get("offer_id") is not None:
            plan.db["offers"].append({"tag": tag, "id": m["offer_id"], "status": "ACCEPTED"})
        plan.counts["fa_signings"] += 1


# ------------------------------------------------------------- plan_stage ----
def plan_stage(cur, snapshot, stage, seed, deps=None, wave=None, state=None):
    """Plans one stage: {"stage", "wave", "season", "week", "edits" (the save_writer document), "summary", "drift",
    "deferred", "warnings", "errors", "db" (what run() records after a successful write), "results"}."""
    deps = deps or Deps()
    if stage not in STAGES:
        raise StageError(f"unknown stage {stage}")
    if stage == FREE_AGENCY and wave is None:
        try:
            wave = stage_and_wave(snapshot)[1]
        except StageError:
            wave = None
        wave = wave or 1
    season, week = league_season(snapshot, stage), league_week(snapshot, stage)
    calendar = snapshot["season"]["calendar_year"]
    teams = deps.team_ids(cur, snapshot)
    plan = Plan(snapshot, stage, season, week, wave if stage == FREE_AGENCY else None, seed, teams,
                deps.pgid_map(cur), state)
    if state is None:
        plan.warn("no league state from the save: team cap fields and spare contract rows unknown")

    # 1-2. JR
    plan.step("pending moves", lambda: step_pending(plan, deps.pending_moves(cur)))
    plan.step("accepted offers", lambda: step_accepted_offers(plan, deps.accepted_offers(cur, season)))
    # 3. drift
    stored = plan.step("stored contracts", deps.stored_contracts, cur, calendar) or {}
    drift = plan.step("drift check", deps.drift, snapshot, cur, calendar, week if stage in IN_SEASON else None) or {}
    rating_rows = plan.step("drift write-back", step_drift, plan, drift, stored) or []
    if drift.get("settings"):
        plan.step("drift sliders", lambda: plan.add_records(deps.slider_edits(snapshot)))

    # 4. the stage
    if stage in IN_SEASON:
        prev = plan.step("previous injuries", deps.previous_injuries, cur, calendar, week) or {}
        plan.step("injuries", step_injuries, plan, prev, seed)
        released = plan.step("last run's releases", deps.released_last_run, cur) or []
        rel_pids = [plan.pid(g) for g in released if plan.pid(g) is not None
                    and plan.roster_status(g)[0] == "FreeAgent"]
        if rel_pids:
            plan.step("waivers", lambda: step_moves(plan, deps.waivers(cur, season, week, rel_pids, seed),
                                                    PRIORITY_STAGE, "waivers"))
        plan.step("form", lambda: step_merge_doc(plan, deps.form_edits(cur, snapshot, season, week), PRIORITY_FORM))
        hook = plan.step("in-season checkpoint", deps.in_season_checkpoint)
        if hook is None:
            log.info("progression.in_season_checkpoint not available; in-season progression skipped")
            plan.counts["in_season_checkpoint"] = 0
        else:
            def _checkpoint():
                rows = hook(cur, season, week) or []
                step_merge_doc(plan, {"players": deps.rating_edits(rows, snapshot, plan.pgid_by_pid)}, PRIORITY_STAGE)
                plan.counts["in_season_checkpoint"] = len(rows)
            plan.step("in-season checkpoint", _checkpoint)
    elif stage == OFFSEASON_START:
        plan.step("form reset", lambda: step_merge_doc(
            plan, deps.form_edits(cur, snapshot, season, week, store=False), PRIORITY_FORM))
        plan.step("progression pass", lambda: step_merge_doc(plan, deps.pass_edits(cur, snapshot, season + 1),
                                                             PRIORITY_STAGE))
        plan.step("retirements", step_retirements, plan, seed)
        plan.step("promoted practice squads", lambda: step_promoted_ps(plan, deps.ps_without_contract(cur, calendar)))
    else:
        kept = plan.step("kept retirees", deps.kept_retirees, cur, season) or set()
        if kept:
            plan.step("restore retirees", step_restore_retirees, plan, kept, stored)
    if stage == RESIGN:
        plan.step("re-signings", lambda: step_moves(plan, deps.resign_decisions(cur, season, seed), PRIORITY_STAGE,
                                                    "resign"))
    elif stage == FREE_AGENCY:
        plan.step("free agency", step_free_agency, plan, deps, cur, seed)
    elif stage == DRAFT:
        def _draft():
            slots, boards, jr = deps.draft_inputs(cur, season)
            if not slots:
                plan.warn("no draft order (draft_picks.overall_pick) for the coming draft: no picks planned")
                return
            step_draft(plan, slots, boards, jr, seed)
        plan.step("draft", _draft)
    elif stage == UDFA:
        plan.step("undrafted free agents", step_udfa, plan, deps, cur, seed)
    elif stage == CUTDOWN:
        plan.step("cut-down", lambda: step_moves(plan, deps.cutdown(cur, season, seed), PRIORITY_STAGE, "cutdown"))
        plan.step("practice squad", lambda: step_moves(plan, deps.practice_squad(cur, season, seed), PRIORITY_STAGE,
                                                       "practice_squad"))
    elif stage == PRESEASON:
        plan.step("roster limit", lambda: step_moves(plan, deps.cutdown(cur, season, seed, ROSTER_LIMIT),
                                                     PRIORITY_STAGE, "cutdown"))

    # 5. rosters and depth charts
    if stage in IN_SEASON or stage in NEW_YEAR_STAGES:
        user_teams = plan.step("user teams", deps.user_team_ids, cur) or set()
        jr_charts = plan.step("JR charts", deps.jr_charts, cur, calendar) or {}
        schemes = plan.step("schemes", deps.team_schemes, cur, calendar) or {}
        plan.step("coach depth charts", step_coach_charts, plan, deps, cur, user_teams, jr_charts, schemes)
        if stage in IN_SEASON:
            plan.step("roster legality", step_legality, plan, MAX_ACTIVE)
    if rating_rows:
        plan.step("drift ratings", lambda: step_merge_doc(
            plan, {"players": deps.rating_edits(rating_rows, snapshot, {})}, PRIORITY_DRIFT_RATINGS))

    edits, summary = plan.finalize()
    for p in plan.db["pending"]:
        ok, reason = plan.results.get(p["tag"], (False, "not planned"))
        p["status"], p["reason"] = ("WRITTEN" if ok else "REFUSED"), (None if ok else reason)
    for o in plan.db["offers"]:
        ok = plan.results.get(o["tag"], (False,))[0] if o.get("tag") else True
        if o.get("user") and ok:
            plan.db["written_offer_ids"].append(o["id"])
        if not ok:
            o["skip"] = True                   # refused, or a deferred signing: stays as it is
    summary["written_offer_ids"] = sorted(set(plan.db["written_offer_ids"]))
    summary["pending_written"] = sum(1 for p in plan.db["pending"] if p["status"] == "WRITTEN")
    summary["pending_refused"] = sum(1 for p in plan.db["pending"] if p["status"] == "REFUSED")
    summary["drift"] = {k: len(v) for k, v in drift.items() if isinstance(v, list)}
    return {"stage": stage, "wave": plan.wave, "season": season, "week": week, "calendar_year": calendar,
            "seed": seed, "edits": edits, "summary": summary, "drift": drift, "deferred": plan.deferred,
            "warnings": plan.warnings, "errors": plan.errors, "db": plan.db,
            "team_ids": plan.teams, "pid_by_pgid": plan.pid_by_pgid}


# -------------------------------------------------------------- read-back ----
def verify_read_back(edits, snapshot):
    """Independent check of a written copy's snapshot against the document: released players are free agents,
    signed and traded players on their new teams, player fields as written. Returns mismatches."""
    players = {p["pgid"]: p for p in snapshot.get("players") or []}
    bad = []
    for g in edits.get("release") or []:
        p = players.get(g)
        if not p or p.get("status") != "FreeAgent" or p.get("team_index") != FREE_AGENT_TEAM:
            bad.append(f"pgid {g}: released but {p and (p.get('status'), p.get('team_index'))}")
    for s in edits.get("sign") or []:
        p = players.get(s["pgid"])
        want = "Signed" if s["list"] == "Roster" else "PracticeSquad"
        if not p or p.get("status") != want or p.get("team_index") != s["team_index"]:
            bad.append(f"pgid {s['pgid']}: signed to {s['team_index']} but {p and (p.get('status'), p.get('team_index'))}")
    for t in edits.get("trade") or []:
        for g in t["pgids"]:
            p = players.get(g)
            if not p or p.get("team_index") != t["to_team_index"]:
                bad.append(f"pgid {g}: traded to {t['to_team_index']} but on {p and p.get('team_index')}")
    checked = {"IsInjuredReserve": ("injury", "injured_reserve"), "MinInjuryDuration": ("injury", "min_weeks"),
               "MaxInjuryDuration": ("injury", "max_weeks"), "ConfidenceRating": (None, "confidence"),
               "ContractStatus": (None, "status"), "TeamIndex": (None, "team_index"),
               "PLYR_CAPSALARY": (None, "cap_salary")}
    for e in edits.get("players") or []:
        p = players.get(e["pgid"])
        if p is None:
            bad.append(f"pgid {e['pgid']}: not in the written save")
            continue
        for k, v in (e.get("set") or {}).items():
            if k.endswith("Rating") and k[:-6] in (p.get("ratings") or {}):
                got = p["ratings"][k[:-6]]
            elif k in checked:
                grp, key = checked[k]
                got = (p.get(grp) or {}).get(key) if grp else p.get(key)
            else:
                continue
            if got != v:
                bad.append(f"pgid {e['pgid']} {k}: wrote {v}, read {got}")
    return bad


# -------------------------------------------------------------------- run ----
def franchise_closed_confirmed(flag):
    return bool(flag) or os.environ.get(FRANCHISE_CLOSED_ENV, "").strip().lower() in ("1", "true", "yes")


def default_seed(snapshot, stage, wave):
    s = snapshot.get("season") or {}
    league = (snapshot.get("franchise") or {}).get("league_id")
    return f"league_office:{league}:{s.get('calendar_year')}:{stage}:{wave or 0}:{s.get('week_index')}"


def _insert_run(cur, plan_or_meta, save_name, status, edits=None, summary=None, drift=None, error=None):
    cur.execute("""INSERT INTO league_office_runs (season, week, stage, save_name, status, edits, summary, drift, error,
                                                  finished_at)
                   VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s,
                           CASE WHEN %s = 'PLANNED' THEN NULL ELSE now() END) RETURNING id""",
                (plan_or_meta["season"], plan_or_meta["week"], plan_or_meta["stage"], save_name, status,
                 json.dumps(edits or {}), json.dumps(summary, default=str) if summary is not None else None,
                 json.dumps(drift, default=str) if drift is not None else None, error, status))
    return cur.fetchone()[0]


def _finish_run(cur, run_id, status, edits=None, summary=None, drift=None, error=None):
    cur.execute("""UPDATE league_office_runs SET status = %s, edits = COALESCE(%s::jsonb, edits),
                   summary = COALESCE(%s::jsonb, summary), drift = COALESCE(%s::jsonb, drift), error = %s,
                   finished_at = now() WHERE id = %s""",
                (status, json.dumps(edits) if edits is not None else None,
                 json.dumps(summary, default=str) if summary is not None else None,
                 json.dumps(drift, default=str) if drift is not None else None, error, run_id))


def record_written(cur, plan, run_id, deps=None):
    """What a successful write makes true in the database: pending moves' statuses, depth charts stored (MU_COACH,
    JR), injuries.mu_weeks_out, retirement_decisions, offer statuses."""
    from engines import depth_chart_engine as dce
    db, season, week = plan["db"], plan["calendar_year"], plan["week"]
    for p in db["pending"]:
        cur.execute("UPDATE franchise_pending_moves SET status = %s, reason = %s, written_run = %s WHERE id = %s",
                    (p["status"], p["reason"], run_id, p["id"]))
    pid_by_pgid = plan["pid_by_pgid"]
    charts = {c["team_index"]: c for c in plan["edits"].get("depthCharts") or []}
    sources = {c["team_index"]: c for c in db["charts"]}
    for p in db["pending"]:
        if p["kind"] == "DEPTH_CHART" and p["status"] == "WRITTEN":
            ti = next((i for i, t in plan["team_ids"].items() if t == p["team_id"]), None)
            sources[ti] = {"team_index": ti, "team_id": p["team_id"], "source": "JR"}
    for ti, c in sources.items():
        if ti not in charts:
            continue
        chart = {slot: [pid_by_pgid[g] for g in gs if g in pid_by_pgid] for slot, gs in charts[ti]["slots"].items()}
        dce.store(cur, c["team_id"], season, week, chart, c["source"])
    for i in db["injuries"]:
        cur.execute("UPDATE injuries SET mu_weeks_out = %s WHERE season = %s AND week = %s AND player_id = %s",
                    (i["mu_weeks_out"], season, week, i["player_id"]))
    for r in db["retirements"]:
        cur.execute("""INSERT INTO retirement_decisions (season, player_id, madden_retired, mu_keep, reason)
                       VALUES (%s, %s, true, %s, %s) ON CONFLICT (season, player_id) DO UPDATE
                       SET madden_retired = true, mu_keep = EXCLUDED.mu_keep, reason = EXCLUDED.reason""",
                    (plan["season"], r["player_id"], r["mu_keep"], r["reason"]))
    for o in db["offers"]:
        if o.get("skip") or not o.get("status"):
            continue
        cur.execute("UPDATE contract_offers SET status = %s WHERE id = %s", (o["status"], o["id"]))


def replace_save(src, dest):
    """Atomically replaces `dest` with `src` (copy next to dest, then os.replace)."""
    tmp = dest + ".mu-league-office"
    shutil.copyfile(src, tmp)
    os.replace(tmp, dest)


def run(cur, snapshot, save_path, stage="auto", dry_run=False, confirm_franchise_closed=False, seed=None,
        deps=None, wave=None, out=print):
    """Plans and writes one stage; returns the league_office_runs id. See the module docstring for the rules."""
    deps = deps or Deps()
    conn = cur.connection
    if stage in (None, "auto"):
        stage, detected_wave = stage_and_wave(snapshot)
        wave = wave if wave is not None else detected_wave
    seed = seed or default_seed(snapshot, stage, wave)
    save_name = os.path.basename(save_path)
    meta = {"season": league_season(snapshot, stage), "week": league_week(snapshot, stage), "stage": stage}
    confirmed = franchise_closed_confirmed(confirm_franchise_closed)
    if not dry_run and not confirmed and deps.madden_running():
        msg = ("Madden27.exe is running and the franchise may be loaded: save and quit to the main menu, then pass "
               f"--confirm-franchise-closed (or {FRANCHISE_CLOSED_ENV}=1)")
        run_id = _insert_run(cur, meta, save_name, "FAILED", error=msg)
        conn.commit()
        raise FranchiseOpenError(msg)
    try:
        state = deps.league_state(save_path)
    except Exception as e:      # noqa: BLE001 - planned without it, with a warning
        log.warning("league state unavailable: %s", e)
        state = None
    run_id = None
    if not dry_run:
        run_id = _insert_run(cur, meta, save_name, "PLANNED")
        conn.commit()
    try:
        plan = plan_stage(cur, snapshot, stage, seed, deps=deps, wave=wave, state=state)
    except Exception as e:
        conn.rollback()
        if run_id is not None:
            _finish_run(cur, run_id, "FAILED", error=f"planning failed: {e}")
            conn.commit()
        raise
    report = {"stage": plan["stage"], "wave": plan["wave"], "season": plan["season"], "week": plan["week"],
              "summary": plan["summary"], "deferred": plan["deferred"], "warnings": plan["warnings"],
              "errors": plan["errors"]}
    if dry_run:
        conn.rollback()                      # the engines' own inserts (offers, claims, form rows) go
        run_id = _insert_run(cur, meta, save_name, "DRY_RUN", plan["edits"], plan["summary"], plan["drift"],
                             "; ".join(plan["errors"]) or None)
        conn.commit()
        out(json.dumps(dict(report, run_id=run_id, edits=plan["edits"]), indent=1, default=str))
        return run_id
    if plan["errors"]:
        conn.rollback()
        _finish_run(cur, run_id, "FAILED", plan["edits"], plan["summary"], plan["drift"],
                    "planning errors, nothing written: " + "; ".join(plan["errors"]))
        conn.commit()
        out(json.dumps(dict(report, run_id=run_id, status="FAILED"), indent=1, default=str))
        return run_id
    error = None
    with tempfile.TemporaryDirectory(prefix="mu-league-office-") as tmp:
        edits_path = os.path.join(tmp, "edits.json")
        copy_path = os.path.join(tmp, save_name)
        with open(edits_path, "w") as fh:
            json.dump(plan["edits"], fh)
        if not plan["edits"]:
            error = None
            written = False
        else:
            ok, text = deps.write_save(save_path, edits_path, copy_path)
            written = ok
            if not ok:
                error = f"save_writer refused or read-back failed: {text[-2000:]}"
            else:
                try:
                    bad = verify_read_back(plan["edits"], deps.read_snapshot(copy_path))
                except Exception as e:      # noqa: BLE001
                    bad = [f"reading the written copy failed: {e}"]
                if bad:
                    error = "read-back of the written copy failed: " + "; ".join(bad[:20])
                elif not confirmed and deps.madden_running():
                    error = ("Madden27.exe started during the run; the save was not replaced (pass "
                             "--confirm-franchise-closed once the franchise is closed)")
                else:
                    replace_save(copy_path, save_path)
        if error:
            conn.rollback()
            _finish_run(cur, run_id, "FAILED", plan["edits"], plan["summary"], plan["drift"], error)
            conn.commit()
            out(json.dumps(dict(report, run_id=run_id, status="FAILED", error=error), indent=1, default=str))
            return run_id
    record_written(cur, plan, run_id, deps)
    _finish_run(cur, run_id, "WRITTEN", plan["edits"], plan["summary"], plan["drift"])
    conn.commit()
    out(json.dumps(dict(report, run_id=run_id, status="WRITTEN", replaced=bool(written)), indent=1, default=str))
    return run_id


# ------------------------------------------------------------------ setup ----
def setup(save, out_path):
    """The one-time franchise setup (league_setup.mjs): all 32 teams real users, fill roster off, CoachFiring OFF,
    career clock off. Never writes `save`; `out_path` must not exist."""
    if os.path.exists(out_path):
        raise FileExistsError(f"{out_path} exists")
    res = subprocess.run(["node", SETUP, "setup", save, "--out", out_path], capture_output=True, text=True,
                         timeout=1800)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip())
    print(res.stderr.strip())
    return json.loads(res.stdout)


# -------------------------------------------------------------------- CLI ----
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "setup":
        sp = argparse.ArgumentParser(prog="python3 -m pipeline.league_office setup",
                                     description="One-time franchise setup: every team a real user team, fill roster "
                                                 "off, CoachFiring OFF, career clock off (league_setup.mjs).")
        sp.add_argument("--save", required=True, help="CAREER save to read (never written)")
        sp.add_argument("--out", required=True, help="new save to write (must not exist)")
        a = sp.parse_args(argv[1:])
        try:
            setup(a.save, a.out)
        except (RuntimeError, FileExistsError) as e:
            print(f"SETUP FAILED: {e}", file=sys.stderr)
            return 1
        return 0
    ap = argparse.ArgumentParser(
        prog="python3 -m pipeline.league_office",
        description="League office stage loop: plans Project Mu's moves for the save's stage, writes them to a temp "
                    "copy through save_writer.mjs, reads it back and only then replaces the save.",
        epilog="subcommand: python3 -m pipeline.league_office setup --save <CAREER-file> --out <new-file> "
               f"(one-time franchise setup). Stages: {', '.join(STAGES)}.")
    ap.add_argument("--snapshot", required=True, help="JSON written by pipeline/save_reader/pc_save_reader.mjs")
    ap.add_argument("--save", required=True, help="the CAREER save the snapshot was read from")
    ap.add_argument("--stage", default="auto", help="auto (default: from the snapshot) or a stage name")
    ap.add_argument("--wave", type=int, choices=range(1, FA_WAVES + 1), help="free-agency wave (default: from the save)")
    ap.add_argument("--seed", help="default: league id, year, stage, wave and week")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; no save, only a DRY_RUN run row")
    ap.add_argument("--dsn", default=ls.DSN, help="database (default: DATABASE_URL or the local madden database)")
    ap.add_argument("--confirm-franchise-closed", action="store_true",
                    help=f"Madden is running but the franchise is closed (also {FRANCHISE_CLOSED_ENV}=1)")
    a = ap.parse_args(argv)
    stage = a.stage.upper() if a.stage.lower() != "auto" else "auto"
    if stage != "auto" and stage not in STAGES:
        ap.error(f"--stage must be auto or one of {', '.join(STAGES)}")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    with open(a.snapshot) as fh:
        snapshot = json.load(fh)
    import psycopg
    with psycopg.connect(a.dsn) as conn:
        cur = conn.cursor()
        try:
            run(cur, snapshot, a.save, stage, a.dry_run, a.confirm_franchise_closed, a.seed, wave=a.wave)
        except (FranchiseOpenError, StageError, ls.LoadError) as e:
            print(f"LEAGUE OFFICE STOPPED: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
