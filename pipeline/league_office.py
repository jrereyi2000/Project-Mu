"""
League office stage loop (docs/done/V2_CORE_BUILD_PLAN.md §2.6, U13; docs/done/LEAGUE_OFFICE_PLAN.md §3.3;
progression contract: docs/in_progress/PROGRESSION_PLAN.md §5.3a-§5.3b).

After JR advances Madden to a stage, saves and closes the franchise, the league office plans Project Mu's moves for that
stage, composes them with the progression engine's prepared document into ONE pipeline/save_reader/save_writer.mjs
edits document, writes it to a temp copy of the save, reads the copy back and only then replaces the save:

  python3 -m pipeline.league_office --snapshot captures/<run>/save.json --save <CAREER-file>
                                    [--stage auto|<STAGE>] [--wave N] [--seed S] [--dry-run] [--dsn ...]
                                    [--confirm-franchise-closed]
  python3 -m pipeline.league_office --recover <run_id> [--save <CAREER-file>] [--dsn ...]
  python3 -m pipeline.league_office setup --save <CAREER-file> --out <new-file> [--dsn ...]

Stages (detect_stage). Regular season and playoffs come from season.week_type. The offseason (season.stage OffSeason)
is Madden's 8 stops, one Advance each (docs/reference/MADDEN_OFFSEASON_WALKTHROUGH.md, an all-user manual franchise),
keyed on (week_index, CurrentOffseasonStage = season.offseason_stage):
  (0, 1) Staff          OFFSEASON_START       (1, 1) Re-sign     RESIGN
  (2, 1) FA prep        FREE_AGENCY wave 1    (3, 2) Free agency FREE_AGENCY wave 2
  (5, 4) Combine        COMBINE (+ FA wave 3) (7, 6) Pro day     PRO_DAY
  (11, 9) Draft         DRAFT                 (15, 10) Recap     UDFA
Without an offseason stage number the week index alone decides; a week index or stage number that is none of the 8
stops (the skipped numbers are internal to Madden) is a StageError (pass --stage). Stage 10+ / week 15+ is UDFA.
season.stage PreSeason is PRESEASON before week PRESEASON_CUTDOWN_WEEK and CUTDOWN from it (an estimate until a save at
the last preseason week exists). League-state weeks are contracts_engine.OFFSEASON_STAGE_WEEKS (OFFSEASON_START 24,
RESIGN 25, FREE_AGENCY 26 for both waves, DRAFT 29, UDFA 30, CUTDOWN 31, PRESEASON 32) plus COMBINE 27 and PRO_DAY 28.

Seasons. `season` is league_state's season: the snapshot's calendar year, except at PRESEASON / CUTDOWN, where the
save has already rolled into the new year and the offseason's season is the year before. The league year moves count
against is transactions_engine.target_season (season + 1 in the offseason). Madden's contracts roll into the new league
year at FA prep, so a contract written at OFFSEASON_START or RESIGN for the coming league year keeps the deal's
current-year terms as year 0 and puts the new years after it; from FREE_AGENCY on, and in season, year 0 is the league
year itself. Written contracts always start at CurrentYear 0. Progression years (§5.3a): the weekly checkpoint gets
(season, league week); OFFSEASON_START prepares target application year S = season + 1 (it reconciles development
earned in `season`); setup bootstraps application year `season` in season and at OFFSEASON_START, else season + 1.

Every stage plans, in this order (eligibility is decided as the plan goes: a player takes part in one of release /
sign / trade per write and the first claim stands; a second, different contract for the same player is refused; a
players-edit field already planned with a different value is a CompositionError, never last-writer-wins):
  1. JR's PENDING franchise_pending_moves (marked WRITTEN, or REFUSED with the reason, after the write), then the
     engine's APPROVED CPU deals with flag auto_approved (TRADE_NEGOTIATIONS_PLAN §2.5): their player legs are
     planned at PRIORITY_ENGINE_TRADE (below JR, above the stage's moves) and their picks are left to
     record_written (Mu's books only). A player claimed already refuses the whole deal for this write (retried
     next write, expired on the second refusal through front_office.could_not_make).
  2. contract_offers JR's targets ACCEPTED (from_user): FREE_AGENT / UDFA -> a Roster signing, RESIGN / EXTENSION /
     TENDER -> the player's contract rewritten.
  3. drift_check.diff write-back: a player Madden signed is released, one Madden cut is signed back with Project Mu's
     contract, one Madden traded is traded back, contracts and depth charts Project Mu stored are restored, sliders
     go back to 0. Rating drift is written back only at stages without progression (in REGULAR_WEEK, PLAYOFF_WEEK and
     OFFSEASON_START the progression engine owns every rating it writes; the entries are counted, not written).
  4. The stage:
     REGULAR_WEEK / PLAYOFF_WEEK  injuries (Project Mu's duration: the previous mu_weeks_out minus the weeks since,
         or a seeded draw in Madden's MinInjuryDuration..MaxInjuryDuration; written to both fields and to
         injuries.mu_weeks_out), IR placement at IR_MIN_WEEKS+ weeks out, activation only when healthy (including
         players Madden left on IR after they healed; a player JR moved on or off IR is left to JR's move), waiver
         claims for the players released by the last WRITTEN run (transactions_engine.waivers; a claim can't be
         written in the write that releases him), then roster legality: every team down to MAX_ACTIVE active players
         (ROSTER_LIMIT 65 minus ROSTER_BUFFER 2: room for one injury replacement so Madden never has a reason to sign or
         cut on its own) by releasing the lowest-OVR healthy non-starters with no other edit planned (ties: the biggest
         cut savings first), and coach depth charts (depth_chart_engine) for every team except one that queued its own
         (a PENDING DEPTH_CHART move) or a user team with a stored JR chart.
     Retirements, one decision per offseason (docs/done/RETIREMENT_WINDOW_PLAN.md): the decision stage is the
         first offseason stop (normally OFFSEASON_START) with no WRITTEN decision run for the season
         (Deps.retirement_window_inputs). There every player Madden retired is recorded in retirement_decisions and
         Project Mu keeps (un-retires) him when deps.retirement_decision says so: always with the free-agent flip
         (FreeAgent/32), authorized by retirement_edits.plan_retirement_edits when a proven authority
         ($MU_RETIREMENT_AUTHORITY, Deps.retirement_authority) covers the stage. At every later stop through
         CUTDOWN step_retirement_window (before re-signings, free agency, the draft and cut-down) restores native
         retirements the decision didn't make, and a kept player Madden retired again (once a season), through the
         same planner and the same flip; without an authority they are recorded and reported only. The next run's
         step_restore_retirees signs a restored free agent back to his old team with his stored contract. Players
         who vanished since the previous run are reported (rostered ones recorded). Every run's summary carries
         "retirement_window" with the roster image the save holds after the write.
     OFFSEASON_START  contracts for the practice-squad players Madden promoted (a 1-year minimum for the coming
         league year, contracts_engine.minimum_salary).
     RESIGN  transactions_engine.resign_decisions (CONTRACT / TAG / EXTENSION / OPTION rewrite the contract; LET_GO
         is left to Madden's FA prep).
     FREE_AGENCY (waves 1-2), COMBINE (wave 3)  cpu_offers for the wave, free_agency_engine.run_wave,
         signings_from_decisions (offers' statuses updated after the write).
     PRO_DAY  only the every-stage items.
     DRAFT  every pick from the owner's team board (scout_team_boards, newest step), JR's own picks from the war room
         (scout_draft_picks JR_TEAM), else Madden's board order; each drafted prospect signed with
         contracts_engine.rookie_contract.
     UDFA  free_agency_engine.udfa_market over the undrafted rookies.
     CUTDOWN  transactions_engine.cutdown to ROSTER_LIMIT and practice_squad; PRESEASON: cutdown to ROSTER_LIMIT.
     PRESEASON / CUTDOWN also set coach depth charts. Every offseason stage after OFFSEASON_START signs a kept
     retiree back to his old team when his stored contract runs into the league year.
  5. Cap fields (spike R6: Madden doesn't recompute them): each signed or re-contracted player's PLYR_CAPSALARY (the
     written contract's current-year SalaryYear + BonusYear, $10,000 units) and, on each affected team's Team row,
     TEAM_SALARY (the sum of current-year base salaries) and SalCapCapRoom / SalCapSpendingMoney /
     SalCapNextYearCapRoom moved by the cap-hit change in $10,000 units (CAP_ROOM_UNIT; their stored unit is not
     confirmed, so they are adjusted from the save's stored value rather than recomputed). A release frees cap hit -
     dead money (all remaining bonus accelerates, load_save's rule); a trade moves the full cap hit. The stored
     values come from `league_setup.mjs state`.
  6. Progression (REGULAR_WEEK / PLAYOFF_WEEK: progression.in_season_checkpoint(cur, season, week, snapshot=...,
     source_fingerprint=<sha256 of the input save bytes>); OFFSEASON_START: progression_checkpoint.
     prepare_offseason_pass(cur, snapshot, season + 1, source_fingerprint=...)). Required: a missing hook, a wrong
     signature or an envelope that isn't {document, event_ids, source_fingerprint, edits_hash} with a matching
     fingerprint and hash is a ProgressionIntegrationError, never a skip. The engine sees the snapshot without the
     players this write releases (the writer refuses players edits for a released player), so eligibility is fixed
     before its proposals are frozen. Its document already holds permanent base + weekly form overlay, OVR / grades /
     PlayerType, morale 50, XP / skill points 0 and the progression-setting safeguards (sliders, dev-trait regression
     and breakout scenarios off); the league office adds no form or morale of its own. compose() merges it with the
     moves above into one document; a player field or record field both set with different values, or a progression
     edit of a released player, is a CompositionError. `prepared` is kept unchanged; the combined document has its
     own hash.

Writing and the receipt protocol (run; §5.3a steps 1-5). The input save's sha256 is the source fingerprint. The
league_office_runs row (status PLANNED) is inserted and committed first; after planning, the operation record goes into
its summary JSONB under "operation" (operation_id, phase, save path, input / candidate / output sha256, combined hash,
the unchanged prepared envelope, and what record_written needs) and edits holds the combined document; that is
COMMITTED with the PREPARED progression events (phase PREPARED) before anything is written. The document then goes to
`node save_writer.mjs <save> <edits> --out <temp copy>` (whole-document plan, write and read-back), the copy is read
with pc_save_reader.mjs and checked again (verify_read_back); on any failure the run is FAILED, nothing is awarded and
nothing is acknowledged. Otherwise the candidate's sha256 is committed (phase CANDIDATE_VERIFIED), the save is replaced
atomically from the copy under the franchise-closed guard, the output file's sha256 is checked against the candidate's
and committed (phase REPLACED), the output save is read, progression_checkpoint.acknowledge_pass(cur, prepared,
output_snapshot, output_fingerprint=output_sha256, writer_verified=True) runs, record_written stores the plan's DB side
(every trade this write carried is executed once through front_office._execute, its proposal set EXECUTED, and a CPU
deal's transcript published with gm_talks.publish_talk) and the run becomes WRITTEN (phase ACKNOWLEDGED) in one
commit. If anything after the replacement fails, the run is left PLANNED for --recover. --recover <run_id> never recalculates, re-rolls or advances: it reads the stored
operation, hashes the save as it is now and (a) when that equals the recorded candidate / output sha256 the
replacement happened: it reads that save and acknowledges the ORIGINAL prepared envelope with the observed output
fingerprint, then records and commits; (b) when it equals the input sha256 the save was never replaced: the run is
marked FAILED and nothing is awarded; (c) anything else is refused (RecoveryError) and nothing changes. A run already
WRITTEN is reported and left alone. --dry-run writes no save, prints the plan with the engine's public proposal reports
(progression_checkpoint.explain_prepared), rolls the preparation back, never acknowledges, and keeps one DRY_RUN row.

Madden may sit at its main menu with the franchise closed, which can't be told apart from a loaded franchise, so
while Madden27.exe runs the write needs --confirm-franchise-closed (or LEAGUE_OFFICE_FRANCHISE_CLOSED=1, for the
watcher); with Madden not running it writes.

Writer limits handled here: a Roster signing takes a free PlayerContract row or, when none is free, fills an empty
one (P1-08), so a signing no longer needs a release in the same write and `free_contract_rows_left` is informational;
the writer has no practice-squad promote / demote move, so those are deferred or REFUSED; a kept retiree is
un-retired as a free agent (ContractStatus FreeAgent, TeamIndex 32) and signed back in a later write.

Setup (one time, before the franchise's first league office run): league_setup.mjs makes every team a real user team,
turns fill roster off, CoachFiring OFF, the career clock off and sets every player's ConfidenceRating to 50, writing a
NEW save (--out) and reading it back. The league office then reads the new save, checks every player is at
ConfidenceRating 50 and that no player_form row exists for the year (form is known-neutral), and calls
progression_checkpoint.bootstrap_pass(cur, snapshot, application_season, source_fingerprint=<sha256 of the new save>,
form_neutral=True) and commits. A failure rolls back and deletes the new save. bootstrap_pass is never called for any
other ingest.
"""
import argparse
import copy
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

from engines import cap_history
from engines import contracts_engine as ce
from engines import transactions_engine as te
from engines.draft_order import COMP_SOURCES as DRAFT_COMP_SOURCES, has_pick_suffix
from pipeline import load_save as ls
from pipeline import retirement_window as rw
from pipeline.retirement_edits import plan_retirement_edits

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
# Madden's 8 offseason stops (docs/reference/MADDEN_OFFSEASON_WALKTHROUGH.md):
# (week_index, CurrentOffseasonStage, stage, free-agency wave, Madden's name).
OFFSEASON_STOPS = (
    (0, 1, OFFSEASON_START, None, "Staff"),
    (1, 1, RESIGN, None, "Re-sign"),
    (2, 1, FREE_AGENCY, 1, "FA prep"),
    (3, 2, FREE_AGENCY, 2, "Free agency"),
    (5, 4, COMBINE, 3, "Combine"),
    (7, 6, PRO_DAY, None, "Pro day"),
    (11, 9, DRAFT, None, "Draft"),
    (15, 10, UDFA, None, "Recap / UDFA"),
)
UDFA_FROM_STAGE, UDFA_FROM_WEEK = 10, 15
FA_STAGES = (FREE_AGENCY, COMBINE)             # stages that run a free-agency wave
PROGRESSION_STAGES = IN_SEASON + (OFFSEASON_START,)
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
CAP_ROOM_RANGE = (-40_000, 484_287)
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

# Claim order (informational: the first claim on a player stands) and depth-chart precedence (JR > JR_STORED >
# MU_COACH > DRIFT). ENGINE_TRADE sits below JR's pending moves and above every engine/stage move: an agreed CPU
# deal neither jumps JR's queue nor loses to the stage's own moves (TRADE_NEGOTIATIONS_PLAN §2.5).
PRIORITY_JR, PRIORITY_ENGINE_TRADE, PRIORITY_OFFER, PRIORITY_DRIFT, PRIORITY_STAGE = 0, 1, 2, 3, 4

PREPARED_KEYS = ("document", "event_ids", "source_fingerprint", "edits_hash")
PHASES = ("PREPARED", "CANDIDATE_VERIFIED", "REPLACED", "ACKNOWLEDGED", "FAILED")


class StageError(Exception):
    pass


class FranchiseOpenError(Exception):
    pass


class ProgressionIntegrationError(Exception):
    """The progression engine's U13 interface is missing or returned something else (never a silent skip)."""


class CompositionError(Exception):
    """Two edits for the same player / record field disagree, or a released player would get a players edit."""


class RecoveryError(Exception):
    pass


def canonical_hash(value):
    """sha256 of canonical JSON (progression_checkpoint.fingerprint's rule)."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_sha256(path):
    """sha256 of a file's bytes (the save fingerprints of §5.3a are of actual save bytes)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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
            if number >= UDFA_FROM_STAGE:
                return UDFA, None
            stops = [r for r in OFFSEASON_STOPS if r[1] == number]
            if number == 1:
                stops = [r for r in stops if r[0] == (week or 0)]
            if len(stops) != 1:
                raise StageError(f"offseason stage {number} week {week} is not one of Madden's 8 offseason stops "
                                 "(pass --stage)")
            return stops[0][2], stops[0][3]
        if week is None:
            raise StageError("offseason save without a week index or offseason stage")
        if week >= UDFA_FROM_WEEK:
            return UDFA, None
        stop = next((r for r in OFFSEASON_STOPS if r[0] == week), None)
        if stop is None:
            raise StageError(f"offseason week {week} is not one of Madden's 8 offseason stops (pass --stage)")
        return stop[2], stop[3]
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


def offseason_target_season(season):
    """The application year S prepare_offseason_pass takes at OFFSEASON_START: the season after `season` (the one whose
    development it reconciles)."""
    return season + 1


def setup_application_season(stage, season):
    """The application year a setup bootstraps progression at: `season` in season and at OFFSEASON_START (its
    offseason pass is still to come), else season + 1 (later offseason stops and the preseason are already the next
    year's; a setup after OFFSEASON_START has no offseason pass that year)."""
    return season if stage in PROGRESSION_STAGES else season + 1


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
        self.league_cap_dollars = league_cap(state, snapshot)
        self.slots = next((len(p["contract"]["salary_by_year"]) for p in self.players.values()
                           if p.get("contract") and p["contract"].get("salary_by_year")), DEFAULT_CONTRACT_SLOTS)
        # current view: pgid -> (status, team_index); IR view: pgid -> bool
        self.view = {g: (p.get("status"), p.get("team_index")) for g, p in self.players.items()}
        self.ir = {g: bool((p.get("injury") or {}).get("injured_reserve")) for g, p in self.players.items()}
        self.edits = {}            # pgid -> {"set": {field: value}, "contract": block or None}
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
        # the part of self.cap a trade that record_written executes in the DB contributes: trade_moves already
        # moves those cap rows, so _apply_db must not move them a second time
        self.trade_cap_db = defaultdict(lambda: [0, 0, 0])
        self.db = {"pending": [], "engine_trades": [], "charts": [], "injuries": [], "retirements": [], "offers": [],
                   "written_offer_ids": [], "salary_cap": None}
        # what a retirement edit writes for a player whose view stays ("Retired", None) this write (the roster image
        # stores what the save holds after the write; RETIREMENT_WINDOW_PLAN §2.2)
        self.image_overrides = {}
        self.retirement_window = None   # summary["retirement_window"] minus roster_image (decision or window role)

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
        """Runs one planning step; an exception becomes a plan error (the run then writes nothing). Composition and
        progression-interface errors are raised."""
        try:
            return fn(*args, **kwargs)
        except (CompositionError, ProgressionIntegrationError):
            raise
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
        if pgid in self.edits:
            # The writer refuses a players edit of a released player; this write already planned one for him.
            self.warn(f"{self.name(pgid)}: not released ({reason}): an edit for him is already planned this write")
            return self.note(tag, False, f"{self.name(pgid)} already has a players edit planned this write")
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

    def trade_player(self, pgid, to_index, priority, reason, tag=None, db_executed=False):
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
            if db_executed:                      # trade_moves will move these cap rows itself
                for idx, delta in ((team, (hit, nxt, -base)), (to_index, (-hit, -nxt, base))):
                    for i, v in enumerate(delta):
                        self.trade_cap_db[idx][i] += v
        self.view[pgid] = (status, to_index)
        self.counts["trade"] += 1
        return self.note(tag, True, reason)

    def set_fields(self, pgid, fields, tag=None):
        """Player fields. A field already planned with a different value is a CompositionError (steps decide
        eligibility first; nothing is overwritten)."""
        if pgid not in self.players:
            return self.note(tag, False, f"pgid {pgid} not in the save")
        if pgid in self.claimed and self.claimed[pgid][0] == "release":
            raise CompositionError(f"{self.name(pgid)} (pgid {pgid}) is released this write: no players edit allowed")
        e = self.edits.setdefault(pgid, {"set": {}, "contract": None})
        for k, v in fields.items():
            if k in e["set"] and e["set"][k] != v:
                raise CompositionError(f"pgid {pgid} {k}: planned {e['set'][k]!r} and {v!r} in the same write")
        e["set"].update(fields)
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
        have = self.edits.get(pgid)
        if have and have.get("contract") is not None:
            return self.note(tag, False, f"{self.name(pgid)}: a contract change is already planned this write")
        if block is None:
            block, dropped = contract_block(contract, old, self.offset if offset is None else offset, self.slots)
            if dropped:
                self.warn(f"{self.name(pgid)}: {dropped} contract years past the save's {self.slots} slots dropped")
        if block == snapshot_block(old):
            return self.note(tag, True, "contract already as planned")
        h0, n0, b0 = cap_change(snapshot_block(old))
        h1, n1, b1 = cap_change(block)
        self._cap(team, h0 - h1, n0 - n1, b1 - b0)
        e = self.edits.setdefault(pgid, {"set": {}, "contract": None})
        e["contract"] = block
        self.contracts_written[pgid] = block
        self.counts["contract"] += 1
        return self.note(tag, True, reason)

    def depth_chart(self, team_index, slots_pgids, source, priority, tag=None):
        """A team's chart for this write; the first one of a source at least as strong stands (JR > JR_STORED >
        MU_COACH > DRIFT, step_coach_charts replaces a DRIFT chart)."""
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
                # P1-08: the writer fills an empty PlayerContract row when no released row is free, so a signing no
                # longer waits on a matching release; free_rows is informational (rows the writer can reuse first).
                if self.free_rows > 0:
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
        for g, e in sorted(self.edits.items()):
            if g in released:
                raise CompositionError(f"pgid {g}: a players edit is planned for a released player")
            st = dict(e["set"])
            clash = [k for k in MOVE_FIELDS.get(moved.get(g), ()) if k in st]
            if clash:
                raise CompositionError(f"pgid {g}: the {moved[g]} move sets {', '.join(clash)}, also in a players edit")
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
        if self.db.get("salary_cap"):
            summary["salary_cap"] = dict(self.db["salary_cap"])
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

    def engine_trades(self, cur, season):
        """APPROVED CPU deals this write should make: `season = the plan's season`, `status = 'APPROVED'` and
        `'auto_approved' = ANY(flags)` (TRADE_NEGOTIATIONS_PLAN §2.5). Each carries `refusals`, how many WRITTEN
        runs already refused it (the second refusal expires it). Empty without the GM migration."""
        cur.execute("SELECT to_regclass('gm_trade_proposals') IS NOT NULL")
        if not cur.fetchone()[0]:
            return []
        from engines import gm_trade_engine as ge
        refused = self.engine_trade_refusals(cur, season)
        out = []
        for p in ge.read_proposals(cur, season, statuses=["APPROVED"]):
            if "auto_approved" in (p.get("flags") or []):
                out.append(dict(p, refusals=refused.get(p["id"], 0)))
        return out

    def engine_trade_refusals(self, cur, season):
        """{proposal_id: refusals} from the WRITTEN runs' `engine_trades_failed_ids` (a CPU deal retried next write)."""
        cur.execute("SELECT summary FROM league_office_runs WHERE status = 'WRITTEN' AND season = %s", (season,))
        out = Counter()
        for (s,) in cur.fetchall():
            s = json.loads(s) if isinstance(s, str) else (s or {})
            for pid in s.get("engine_trades_failed_ids") or []:
                out[int(pid)] += 1
        return dict(out)

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

    # -- cap
    def league_caps(self, cur):
        return cap_history.league_caps(cur)

    def sync_team_caps(self, cur, season, cap_dollars):
        cur.execute("""UPDATE team_cap_seasons
                       SET cap = %s,
                           adjusted_cap = %s + COALESCE(rollover, 0) + (COALESCE(adjusted_cap, cap) - cap - COALESCE(rollover, 0)),
                           cap_space = (%s + COALESCE(rollover, 0) + (COALESCE(adjusted_cap, cap) - cap - COALESCE(rollover, 0))) - COALESCE(total_spend, 0)
                       WHERE season = %s""", (cap_dollars, cap_dollars, cap_dollars, season))

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

    # -- progression (PROGRESSION_PLAN §5.3a; the engine prepares and acknowledges, the league office commits)
    def progression_weekly(self, cur, season, week, snapshot, source_fingerprint):
        """progression.in_season_checkpoint: required, never optional."""
        from engines import progression
        hook = getattr(progression, "in_season_checkpoint", None)
        if hook is None:
            raise ProgressionIntegrationError("engines.progression has no in_season_checkpoint: the managed league "
                                              "can't run a week without it")
        return hook(cur, season, week, snapshot=snapshot, source_fingerprint=source_fingerprint)

    def progression_offseason(self, cur, snapshot, target_season, source_fingerprint):
        from engines import progression_checkpoint as pc
        return pc.prepare_offseason_pass(cur, snapshot, target_season, source_fingerprint=source_fingerprint)

    def progression_explain(self, cur, prepared):
        """Public proposal reports (§5.3b) for dry-run logs; read-only."""
        from engines import progression_checkpoint as pc
        return pc.explain_prepared(cur, prepared, audience="public")

    def progression_acknowledge(self, cur, prepared, output_snapshot, output_fingerprint):
        """Only after the whole-document writer read-back passed and the output file is known to be the save."""
        from engines import progression_checkpoint as pc
        return pc.acknowledge_pass(cur, prepared, output_snapshot, output_fingerprint=output_fingerprint,
                                   writer_verified=True)

    def progression_bootstrap(self, cur, snapshot, season, source_fingerprint):
        """Setup only: the explicit neutral bootstrap (never for an arbitrary ingest)."""
        from engines import progression_checkpoint as pc
        return pc.bootstrap_pass(cur, snapshot, season, source_fingerprint=source_fingerprint, form_neutral=True)

    def applied_form_rows(self, cur, season):
        """player_form rows already stored for `season` (setup refuses: form isn't known-neutral then)."""
        cur.execute("SELECT count(*) FROM player_form WHERE season = %s", (season,))
        return int(cur.fetchone()[0])

    def rating_edits(self, rows, snapshot, pgid_by_player):
        from engines import progression_save
        return progression_save.rating_edits(rows, snapshot, pgid_by_player)

    def slider_edits(self, snapshot):
        from engines import progression_save
        return progression_save.slider_edits(snapshot)

    def staff_depth_chart(self, roster, scheme, injuries, score_adjustments=None):
        from engines import depth_chart_engine as dce
        return dce.staff_depth_chart(roster, scheme, injuries, score_adjustments=score_adjustments)

    def chart_adjustments(self, cur, team_id, season, week, roster=None, scheme=None, injuries=None):
        """Opportunity score adjustments for a CPU staff chart: none here (PersonalityDeps passes them when the
        switchboard's development_opportunity feature is active)."""
        return None

    def retirement_decision(self, cur, player, season, seed):
        """(mu_keep, reason) for a player Madden retired: today's seeded age/OVR rule (PersonalityDeps asks the
        retirement policy by switchboard mode)."""
        return keep_retired(player, season, seed)

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

    def retirement_window_inputs(self, cur, season):
        """What the retirement window needs for `season`, from WRITTEN league office runs only (RETIREMENT_WINDOW_PLAN
        §2.2): {"decision": {run_id, stop, decided} of the season's decision run or None (this stop is then the
        decision stage), "decided": the decision set plus every player a window run restored (keep True, so a
        second re-retirement counts against MAX_WINDOW_RESTORES), "window_restores": {pgid: restores this season},
        "prior_image" / "prior_stop" / "prior_run_id": the newest WRITTEN run's roster image (any season)}."""
        cur.execute("""SELECT id, summary->'retirement_window'->>'role', summary->'retirement_window'->>'stop',
                              summary->'retirement_window'->'decided', summary->'retirement_window'->'restored'
                       FROM league_office_runs WHERE status = 'WRITTEN' AND season = %s
                       AND summary->'retirement_window'->>'role' IN ('decision', 'window') ORDER BY id""", (season,))
        decision, restores, restored_info = None, Counter(), {}
        for run_id, role, stop, decided, restored in cur.fetchall():
            decided = json.loads(decided) if isinstance(decided, str) else decided
            restored = json.loads(restored) if isinstance(restored, str) else restored
            if role == "decision" and decision is None:
                decision = {"run_id": run_id, "stop": stop, "decided": dict(decided or {})}
            elif role == "window" and decision is not None:
                for r in restored or ():
                    key = str(r.get("pgid"))
                    restores[key] += 1
                    restored_info[key] = {"player_id": r.get("player_id"), "keep": True,
                                          "reason": f"window:restored:{stop}"}
        cur.execute("""SELECT id, summary->'retirement_window'->>'stop', summary->'retirement_window'->'roster_image'
                       FROM league_office_runs WHERE status = 'WRITTEN'
                       AND summary->'retirement_window'->'roster_image' IS NOT NULL ORDER BY id DESC LIMIT 1""")
        row = cur.fetchone()
        image = (json.loads(row[2]) if isinstance(row[2], str) else row[2]) if row else None
        decided = dict(restored_info)
        decided.update((decision or {}).get("decided") or {})
        return {"decision": decision, "decided": decided, "window_restores": dict(restores),
                "prior_image": image, "prior_stop": row[1] if row else None, "prior_run_id": row[0] if row else None}

    def retirement_authority(self):
        """The JR-approved retirement authority proof ($MU_RETIREMENT_AUTHORITY), or None (default deny). A broken
        proof file raises retirement_window.WindowError."""
        return rw.proof_from_env()

    def player_names(self, cur, player_ids):
        """{player_id: "First Last"} for the retirement window's vanished players (absent from the snapshot)."""
        if not player_ids:
            return {}
        cur.execute("SELECT id, COALESCE(full_name, player_name) FROM players WHERE id = ANY(%s)", (list(player_ids),))
        return {i: name for i, name in cur.fetchall()}

    def draft_inputs(self, cur, season):
        """The coming draft: slots [{overall_pick, team_id}], boards {team_id: [(name, position), ...]}, JR's picks
        {overall_pick: (name, position)}, and the class's comp schedule (per-round comp counts, index 0 = round 1:
        contracts_engine.rookie_contract's `comp_schedule`). The schedule counts the class's draft_picks rows
        carrying a pick_suffix or a COMP/JC2A source; no comp rows gives seven zeros, today's exact behaviour."""
        ds = season + 1
        cur.execute("""SELECT overall_pick, owner_team_id FROM draft_picks WHERE draft_season = %s
                       AND overall_pick IS NOT NULL ORDER BY overall_pick""", (ds,))
        slots = [{"overall_pick": n, "team_id": t} for n, t in cur.fetchall()]
        comp_schedule = [0] * 7
        if has_pick_suffix(cur):             # no comp rows (and no suffix column) before the comp migration
            cur.execute("""SELECT round, count(*) FROM draft_picks WHERE draft_season = %s
                           AND (COALESCE(pick_suffix, '') <> '' OR upper(COALESCE(source, '')) = ANY(%s))
                           GROUP BY round""", (ds, list(DRAFT_COMP_SOURCES)))
            for rnd, count in cur.fetchall():
                if 1 <= int(rnd) <= 7:
                    comp_schedule[int(rnd) - 1] = int(count)
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
        return slots, dict(boards), jr, comp_schedule

    # -- the save
    def league_state(self, save_path):
        res = subprocess.run(["node", SETUP, "state", save_path], capture_output=True, text=True, timeout=600)
        if res.returncode != 0:
            raise RuntimeError(f"league_setup.mjs state failed: {res.stderr.strip()[-500:]}")
        return json.loads(res.stdout)

    def run_setup(self, save, out_path):
        """league_setup.mjs setup: (ok, stdout, stderr)."""
        res = subprocess.run(["node", SETUP, "setup", save, "--out", out_path], capture_output=True, text=True,
                             timeout=1800)
        return res.returncode == 0, res.stdout, res.stderr.strip()

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
def default_deps(cur):
    """The personality switchboard's deps for this league (pipeline/league_office_personality.py)."""
    from pipeline import league_office_personality as lop
    return lop.default_deps(cur)


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
                    plan.set_fields(g, {"IsInjuredReserve": k == "IR_PLACE"}, tag)
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
    players}). The player legs go into the save; the picks are moved in Mu's books only by record_written
    (trade_moves), because the save's DraftPick rows have no mapping yet (TRADE_NEGOTIATIONS_PLAN §2.5, Q1)."""
    other = plan.team_index.get(p.get("other_team_id") or p.get("to_team_id"))
    send = p.get("send") or {"players": p.get("players") or [], "picks": p.get("picks") or []}
    recv = p.get("receive") or {"players": [], "picks": []}
    if other is None:
        plan.note(tag, False, "no other team in the trade")
        return
    out_g, in_g = _payload_pgids(plan, send.get("players")), _payload_pgids(plan, recv.get("players"))
    bad = [g for g in out_g if plan.roster_status(g)[1] != team] + [g for g in in_g if plan.roster_status(g)[1] != other]
    if bad:
        plan.note(tag, False, f"not on the trading teams: {', '.join(plan.name(g) for g in bad)}")
        return
    db_executed = p.get("proposal_id") is not None
    ok = all([plan.trade_player(g, other, PRIORITY_JR, "JR's trade", db_executed=db_executed) for g in out_g]
             + [plan.trade_player(g, team, PRIORITY_JR, "JR's trade", db_executed=db_executed) for g in in_g])
    if send.get("picks") or recv.get("picks"):
        if p.get("proposal_id") is not None:
            plan.warn(f"trade #{p['proposal_id']}: picks moved in Mu's books only (record_written)")
        else:
            plan.warn("JR's trade: picks not moved (no proposal_id, and the save's DraftPick rows have no mapping)")
    plan.note(tag, ok, "JR's trade" if ok else "part of the trade could not be planned")


def step_engine_trades(plan, proposals):
    """An APPROVED CPU deal (flag `auto_approved`): its player legs go into the save at PRIORITY_ENGINE_TRADE and its
    picks are left to record_written (Mu's books only). A player already claimed this write refuses the WHOLE trade
    (the proposal stays APPROVED and is retried next write; a second refusal expires it, record_written)."""
    from engines import gm_trade_engine as ge
    window = ge.window_for(plan.week)
    for p in proposals or []:
        pid = p["id"]
        tag = ("trade", pid)
        rec = {"id": pid, "tag": tag, "flags": list(p.get("flags") or []),
               "refusals": int(p.get("refusals") or 0), "status": None, "reason": None}
        plan.db["engine_trades"].append(rec)
        # The deadline and the season/stage are checked here too: an auto-approved deal the league office refused
        # once used to be retried and written in a later week, past the deadline.
        why = ge.stale_reason(p, plan.week, window, plan.stage if window == "OFFSEASON" else None)
        if why:
            rec["status"], rec["reason"] = "EXPIRED", why
            plan.note(tag, False, f"trade #{pid}: {why}")
            continue
        legs = [a for a in p.get("assets") or [] if a.get("type") == "PLAYER"]
        targets, bad = {}, None
        for a in legs:                       # every leg is checked first, so a refusal leaves nothing planned
            g, to_index = plan.pgid(a.get("player_id")), plan.team_index.get(a.get("to"))
            if g is None:
                bad = f"player {a.get('player_id')} has no madden_pgid"
                break
            if to_index is None:
                bad = f"team {a.get('to')} not in the save"
                break
            if g in plan.claimed:
                bad = f"{plan.name(g)} already has a {plan.claimed[g][0]} this write"
                break
            status, on = plan.roster_status(g)
            if status not in ("Signed", "PracticeSquad") or on is None or on >= FREE_AGENT_TEAM:
                bad = f"{plan.name(g)} is {status}, not on a roster"
                break
            from_index = plan.team_index.get(a.get("from"))
            if from_index is not None and on != from_index:
                bad = f"{plan.name(g)} is not on {a.get('from')} any more"     # step_pending_trade checks this too
                break
            targets[g] = to_index
        if bad is None:
            for g, to_index in targets.items():
                if not plan.trade_player(g, to_index, PRIORITY_ENGINE_TRADE, f"trade #{pid}", tag=tag,
                                         db_executed=True):
                    bad = plan.results.get(tag, (False, "could not be planned"))[1]
                    break
        plan.note(tag, bad is None, f"trade #{pid}" if bad is None else bad)


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
    if plan.stage != OFFSEASON_START and report.get("salary_cap"):
        entry = report["salary_cap"][0]
        plan.league_cap_dollars = entry["expected"]
        salary_table_id = (plan.state or {}).get("salary_info_table_id") or (plan.snapshot.get("salary_cap") or {}).get("table_id")
        if salary_table_id is not None:
            raw_target = entry["expected"] // ce.MONEY_UNIT - ce.SALARY_CAP_OFFSET_UNITS
            plan.add_records([{"table": "SalaryInfo", "tableId": salary_table_id, "row": 0,
                               "set": {"TeamSalaryCap": raw_target}}])
        room_delta = (entry["expected"] - entry["found"]) // ce.MONEY_UNIT
        for team_index in plan.teams:
            plan._cap(team_index, room_delta, room_delta, 0)
        plan.counts["drift_salary_cap"] += 1
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
        jr_ir = "IsInjuredReserve" in (plan.edits.get(g) or {}).get("set", {})   # JR's IR move decides
        if injured:
            weeks = injury_weeks(inj, prev.get(pid), plan.week, seed, g)
            fields = {"MinInjuryDuration": weeks, "MaxInjuryDuration": weeks}
            if weeks >= IR_MIN_WEEKS and not on_ir and not jr_ir:
                fields["IsInjuredReserve"] = True
                plan.counts["ir_place"] += 1
            plan.set_fields(g, fields)
            plan.counts["injury_durations"] += 1
            if pid is not None:
                plan.db["injuries"].append({"player_id": pid, "mu_weeks_out": weeks})
        elif on_ir and not jr_ir:
            plan.set_fields(g, {"IsInjuredReserve": False})
            plan.counts["ir_activate"] += 1


def step_legality(plan, limit, protect=frozenset()):
    """Releases the lowest-OVR healthy non-starters with no other edit planned (ties: biggest cut savings) until each
    team has at most `limit` active (non-IR) players."""
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
            if (g in starters or g in protect or g in plan.claimed or g in plan.edits
                    or (p.get("injury") or {}).get("status") not in HEALTHY):
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
        scheme = schemes.get(team_id) or {}
        adjustments = deps.chart_adjustments(cur, team_id, plan.season, plan.week, roster, scheme, injuries)
        if adjustments is None:
            chart = deps.staff_depth_chart(roster, scheme, injuries)
        else:
            chart = deps.staff_depth_chart(roster, scheme, injuries, score_adjustments=adjustments)
        # MU_COACH outranks drift's restored chart: it is this week's coach decision.
        have = plan.depth.get(team_index)
        if have and have["source"] == "DRIFT":
            del plan.depth[team_index]
        if plan.depth_chart(team_index, chart, "MU_COACH", PRIORITY_STAGE):
            plan.db["charts"].append({"team_index": team_index, "team_id": team_id, "source": "MU_COACH"})


def step_drift_ratings(plan, deps, rows):
    """Rating drift written back (stages without progression only), skipping players this write releases."""
    released = set(plan.release)
    rows = [r for r in rows if r["pgid"] not in released]
    for e in deps.rating_edits(rows, plan.snapshot, {}) or []:
        plan.set_fields(e["pgid"], e.get("set") or {})
    plan.counts["drift_ratings_written"] = len(rows)


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


def _planner_pending(plan):
    """The league office's own moves already claimed in this plan, shaped as plan_retirement_edits' `pending` (its
    overlap check then covers Mu's moves too)."""
    out = {"release": [], "sign": [], "trade": [], "players": []}
    for g, (move, _, _) in sorted(plan.claimed.items()):
        if move == "release":
            out["release"].append(g)
        elif move == "sign":
            out["sign"].append({"pgid": g})
        elif move == "trade":
            out["trade"].append({"pgids": [g]})
    out["players"] = [{"pgid": g} for g in sorted(plan.edits)]
    return out


def _schema_version(plan):
    return (plan.snapshot.get("meta") or {}).get("reader_version")


def _apply_free_agent_flip(plan, out):
    """Restores each player plan_retirement_edits allowed by the proven legacy sequence: `FreeAgent`/32 in this
    write, left `("Retired", None)` in the view so nothing signs or cuts him before the next run's sign-back
    (RETIREMENT_WINDOW_SIGNING_RESTORE_PLAN §2.2). Returns the restored pgids."""
    done = []
    for e in (out.get("edits") or {}).get("players") or ():
        g = e["pgid"]
        plan.set_fields(g, {"ContractStatus": "FreeAgent", "TeamIndex": FREE_AGENT_TEAM})
        plan.view[g] = ("Retired", None)   # signable / movable only in a later write
        plan.image_overrides[g] = ("FreeAgent", FREE_AGENT_TEAM)
        done.append(g)
    return done


def step_retirements(plan, seed, deps=None, cur=None, authority=None, prior_image=None, source_fingerprint=None):
    """The decision stage (RETIREMENT_WINDOW_PLAN §2.1): Madden's retirements recorded, and un-retired where
    deps.retirement_decision (today's keep_retired unless the switchboard's retirement feature says otherwise) keeps
    them. Without a proven authority covering this stage a keep is un-retired as a free agent (the legacy writer);
    with one it goes through plan_retirement_edits and keeps the previous run's status and team. Returns the
    decision record {"decided": {pgid: {player_id, keep, reason}}, "restored": [...], "blocked": [...]}."""
    decided, kept, rows = {}, [], {}
    for g, p in sorted(plan.players.items()):
        if p.get("status") != "Retired":
            continue
        keep, reason = (keep_retired(p, plan.season, seed) if deps is None
                        else deps.retirement_decision(cur, p, plan.season, seed))
        pid = plan.pid(g)
        decided[str(g)] = {"player_id": pid, "keep": bool(keep), "reason": reason}
        if pid is None:
            plan.warn(f"{plan.name(g)}: retired but not in players (no madden_pgid); not recorded")
            continue
        rows[g] = {"player_id": pid, "mu_keep": keep, "reason": reason}
        plan.db["retirements"].append(rows[g])
        plan.counts["retired_by_madden"] += 1
        if keep:
            kept.append(g)
    restored, blocked = [], []
    auth = rw.authority_for_run(authority, source_save_hash=source_fingerprint, schema_version=_schema_version(plan))
    if kept and auth and plan.stage in auth["stages"]:
        snap = rw.planner_snapshot(list(plan.players.values()), source_save_hash=source_fingerprint,
                                   stage=plan.stage, schema_version=_schema_version(plan),
                                   prior_image=prior_image or {}, pending=_planner_pending(plan))
        reqs = [{"decision_id": f"decision:{plan.season}:{g}", "pgid": g, "action": rw.KEEP_ACTION,
                 "state": rw.DEFAULT_CAREER_STATE} for g in kept]
        out = plan_retirement_edits(snap, reqs, auth)
        for g in _apply_free_agent_flip(plan, out):
            plan.counts["unretired"] += 1
            restored.append({"pgid": g, "player_id": plan.pid(g), "kind": "DECIDED_KEEP",
                             "restore": "free_agent",
                             "set": {"status": "FreeAgent", "team_index": FREE_AGENT_TEAM}})
        for c in out["conflicts"]:
            g = c["pgid"]
            blocked.append({"pgid": g, "player_id": plan.pid(g), "kind": "DECIDED_KEEP", "reasons": c["reasons"]})
            rows[g].update(mu_keep=False, reason=f"window:blocked:{plan.stage}:{c['reasons'][0]}")
            plan.warn(f"{plan.name(g)}: kept by the retirement decision but not restored ({', '.join(c['reasons'])})")
    else:
        for g in kept:
            plan.set_fields(g, {"ContractStatus": "FreeAgent", "TeamIndex": FREE_AGENT_TEAM})
            plan.view[g] = ("Retired", None)   # signable only in a later write
            plan.image_overrides[g] = ("FreeAgent", FREE_AGENT_TEAM)
            plan.counts["unretired"] += 1
    return {"decided": decided, "restored": restored, "blocked": blocked}


def step_retirement_window(plan, deps, cur, source_fingerprint, inputs, authority):
    """A later offseason stop (RETIREMENT_WINDOW_PLAN §2.3-2.5): native retirements the decision stage didn't decide
    (and keeps Madden retired again, once) are restored through the default-deny planner with the run's authority;
    without one they are recorded and reported only. Players who vanished since the previous run are recorded
    (rostered) and reported. Returns the window record (classify's report plus restored / blocked)."""
    user_ids = deps.user_team_ids(cur) or set()
    user_idx = frozenset(i for i, t in plan.teams.items() if t in user_ids)
    report = rw.classify(list(plan.players.values()), stage=plan.stage, wave=plan.wave, season=plan.season,
                         decided=inputs.get("decided") or {}, prior_image=inputs.get("prior_image"),
                         prior_stop=inputs.get("prior_stop"), window_restores=inputs.get("window_restores") or {},
                         user_team_indexes=user_idx)
    schema = _schema_version(plan)
    auth = rw.authority_for_run(authority, source_save_hash=source_fingerprint, schema_version=schema)
    snap = rw.planner_snapshot(list(plan.players.values()), source_save_hash=source_fingerprint, stage=plan.stage,
                               schema_version=schema, prior_image=inputs.get("prior_image") or {},
                               pending=_planner_pending(plan))
    out = plan_retirement_edits(snap, rw.restore_requests(report), auth)
    by_pgid = {r["pgid"]: r for r in report["restore"]}
    stage = plan.stage
    restored, blocked = [], []

    def record(g, keep, reason, player_id=None):
        pid = player_id if player_id is not None else plan.pid(g)
        if pid is None:
            plan.warn(f"retirement window: pgid {g} not in players (no madden_pgid); not recorded")
            return
        plan.db["retirements"].append({"player_id": pid, "mu_keep": keep, "reason": reason})

    for g in _apply_free_agent_flip(plan, out):
        plan.counts["window_restored"] += 1
        plan.counts["window_free_agent"] += 1
        restored.append(dict(by_pgid.get(g, {"pgid": g}), player_id=plan.pid(g), name=plan.name(g),
                             restore="free_agent"))
        record(g, True, f"window:restored:{stage}")
        if plan.stage == "CUTDOWN":
            plan.warn(f"{plan.name(g)}: restore_at_last_stop (this free agent is signed at the week-1 advance, "
                      "after the free-agent purge; he may not survive)")
    for c in out["conflicts"]:
        g = c["pgid"]
        blocked.append(dict(by_pgid.get(g, {"pgid": g}), player_id=plan.pid(g), name=plan.name(g),
                            reasons=c["reasons"]))
        record(g, False, f"window:blocked:{stage}:{c['reasons'][0]}")
        plan.warn(f"retirement window {report['stop']}: {plan.name(g)} retired by Madden, not restored "
                  f"({', '.join(c['reasons'])})")
    for c in report["conflicts"]:
        c["name"] = plan.name(c["pgid"])
        reason = "re_retired_again" if c["kind"] == "RE_RETIRED_AGAIN" else c["kind"].lower()
        record(c["pgid"], False, f"window:conflict:{stage}:{reason}")
        plan.warn(f"retirement window {report['stop']}: {c['name']} {c['kind']} ({c['reason']}); left retired")
    vanished = report["vanished"]
    pids = {v["pgid"]: plan.pid(v["pgid"]) for v in vanished}
    names = (deps.player_names(cur, [p for p in pids.values() if p is not None]) or {}) if vanished else {}
    for v in vanished:
        pid = pids[v["pgid"]]
        v["player_id"], v["name"] = pid, names.get(pid) or f"pgid {v['pgid']}"
        if v["kind"] == "VANISHED_ROSTERED":
            record(v["pgid"], False, f"window:vanished:{stage}", pid)
            plan.warn(f"retirement window {report['stop']}: {v['name']} (team {v['last']['team_index']}) vanished "
                      "since the previous run (retired and purged in one advance): unrecoverable")
    plan.counts["window_blocked"] += len(blocked)
    plan.counts["window_conflicts"] += len(report["conflicts"])
    plan.counts["window_vanished_rostered"] += sum(1 for v in vanished if v["kind"] == "VANISHED_ROSTERED")
    return dict(report, role="window", restored=restored, blocked=blocked)


def step_restore_retirees(plan, kept, stored):
    """Signs a kept retiree (now a free agent) back to his old team when his stored contract runs into the league
    year (RETIREMENT_WINDOW_SIGNING_RESTORE_PLAN §2.2). No stored contract leaves him a free agent and warns."""
    for pid in sorted(kept):
        g = plan.pgid(pid)
        if g is None or plan.roster_status(g)[0] != "FreeAgent":
            continue
        st = stored.get(pid)
        y = plan.target - st[1] if st else None
        if not st or st[2] not in plan.team_index or not 0 <= y < st[0]["length"]:
            plan.warn(f"{plan.name(g)}: no_restorable_contract (kept retiree has no stored contract running into "
                      f"{plan.target}; left a free agent)")
            continue
        c, start, team_id = st[0], st[1], st[2]
        rest = dict(c, length=c["length"] - y, current_year=0, salary_by_year=c["salary_by_year"][y:],
                    bonus_by_year=c["bonus_by_year"][y:])
        if plan.sign_player(g, plan.team_index[team_id], "Roster", rest, PRIORITY_STAGE,
                            "kept retiree back to his team"):
            plan.counts["restored_retirees"] += 1


def step_promoted_ps(plan, ps_ids):
    """Contracts for the practice-squad players Madden signed to the active roster at the offseason start."""
    cap = getattr(plan, "league_cap_dollars", None) or league_cap(getattr(plan, "state", None), getattr(plan, "snapshot", None))
    for pid in sorted(ps_ids):
        g = plan.pgid(pid)
        if g is None or plan.roster_status(g)[0] != "Signed" or not (plan.players[g].get("contract")):
            continue
        accrued = max(0, (plan.players[g].get("years_pro") or 0) + 1)
        salary = ce.minimum_salary(accrued, cap)
        c = te.make_contract(salary, 1, "MINIMUM", plan.target, bonus_share=0.0, guar_share=0.0)
        if plan.set_contract(g, PRIORITY_STAGE, "practice-squad player Madden promoted: 1-year minimum", contract=c):
            plan.counts["ps_promoted_contracts"] += 1


def league_cap_units(state, snapshot=None) -> int | None:
    """The decoded $10,000-unit salary cap from the save state or snapshot, or None if unknown."""
    if state and state.get("salary_cap") is not None:
        return int(state["salary_cap"])
    if snapshot and isinstance(snapshot.get("salary_cap"), dict):
        sc = snapshot["salary_cap"].get("team_salary_cap")
        if sc is not None:
            return int(sc)
    return None


def league_cap(state, snapshot=None):
    """The league cap in dollars: SalaryInfo.TeamSalaryCap ($10,000 units) when known, else the reference cap."""
    units = league_cap_units(state, snapshot)
    return units * ce.MONEY_UNIT if units is not None else ce.CBA["REFERENCE_CAP"]


def step_salary_cap(plan, deps, cur):
    """Annual salary cap rollover at OFFSEASON_START: computes next_cap, writes SalaryInfo.TeamSalaryCap,
    and shifts team cap rooms."""
    caps = deps.league_caps(cur) if hasattr(deps, "league_caps") else {}
    prev_cap = caps.get(plan.season) or plan.league_cap_dollars
    league_id = (plan.snapshot.get("franchise") or {}).get("league_id")
    seed_str = ce.cap_seed(league_id)
    target_cap = ce.next_cap(prev_cap, plan.target, seed_str)
    plan.league_cap_dollars = target_cap

    salary_table_id = (plan.state or {}).get("salary_info_table_id") or (plan.snapshot.get("salary_cap") or {}).get("table_id")
    target_units = target_cap // ce.MONEY_UNIT
    save_units = league_cap_units(plan.state, plan.snapshot)
    if salary_table_id is not None and save_units is not None and target_units != save_units:
        plan.add_records([{"table": "SalaryInfo", "tableId": salary_table_id, "row": 0,
                           "set": {"TeamSalaryCap": target_units - ce.SALARY_CAP_OFFSET_UNITS}}])
        delta = target_units - save_units
        for team_index in plan.teams:
            plan._cap(team_index, delta, delta, 0)
        plan.counts["salary_cap_updated"] = 1

    plan.db["salary_cap"] = {
        "season": plan.target,
        "prev_cap": prev_cap,
        "cap": target_cap,
        "madden_cap": save_units * ce.MONEY_UNIT if save_units is not None else None,
        "growth_pct": round((target_cap - prev_cap) / prev_cap * 100, 2) if prev_cap else 0.0,
        "seed": seed_str,
    }


def step_draft(plan, slots, boards, jr, seed, comp_schedule=None):
    """Every pick from the team boards (JR's from the war room) signed with a rookie deal. `comp_schedule` is the
    class's per-round comp-pick counts (index 0 = round 1; contracts_engine.round_for_overall_pick's shape), so a
    compensatory pick's `drafted_round` lands in its awarded round (3-7). A class with no comp rows passes all
    zeros (or None) and behaves exactly as before."""
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
    cap = getattr(plan, "league_cap_dollars", None) or league_cap(getattr(plan, "state", None), getattr(plan, "snapshot", None))
    for pk in picks:
        team = plan.team_index.get(pk["team_id"])
        c = ce.rookie_contract(min(max(1, pk["overall_pick"]), ce.CBA["DRAFT_PICKS"]), plan.target, cap,
                               comp_schedule=comp_schedule)
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


# ------------------------------------------------------------ progression ----
def check_prepared(prepared, source_fingerprint):
    """The engine's envelope, checked as §5.3a fixes it; returned unchanged."""
    if not isinstance(prepared, dict) or any(k not in prepared for k in PREPARED_KEYS):
        raise ProgressionIntegrationError(f"progression returned {type(prepared).__name__} without "
                                          f"{', '.join(PREPARED_KEYS)} (the U13 envelope)")
    doc = prepared["document"]
    if not isinstance(doc, dict) or set(doc) - {"players", "records"}:
        raise ProgressionIntegrationError("progression document must hold only players / records")
    if not isinstance(prepared["event_ids"], list):
        raise ProgressionIntegrationError("progression event_ids must be a list")
    if prepared["source_fingerprint"] != source_fingerprint:
        raise ProgressionIntegrationError("progression prepared a different input save (source fingerprint)")
    if canonical_hash(doc) != prepared["edits_hash"]:
        raise ProgressionIntegrationError("progression edits_hash doesn't match its document")
    return prepared


def eligible_snapshot(snapshot, released):
    """The snapshot the engine prepares from: without the players this write releases (the writer refuses their
    players edits), so their proposals are never frozen."""
    if not released:
        return snapshot
    out = dict(snapshot)
    out["players"] = [p for p in snapshot.get("players") or [] if p.get("pgid") not in released]
    return out


def prepare_progression(cur, deps, snapshot, stage, season, week, source_fingerprint, released):
    """(prepared envelope unchanged, info) for a progression stage, else (None, None). Errors are raised."""
    if stage not in PROGRESSION_STAGES:
        return None, None
    if not source_fingerprint:
        raise ProgressionIntegrationError("progression needs the input save's sha256 (source_fingerprint)")
    snap = eligible_snapshot(snapshot, released)
    try:
        if stage in IN_SEASON:
            info = {"kind": "WEEKLY", "season": season, "week": week}
            prepared = deps.progression_weekly(cur, season, week, snap, source_fingerprint)
        else:
            target = offseason_target_season(season)
            info = {"kind": "OFFSEASON", "earned_season": season, "target_season": target}
            prepared = deps.progression_offseason(cur, snap, target, source_fingerprint)
    except TypeError as e:
        raise ProgressionIntegrationError(f"progression interface mismatch: {e}") from e
    check_prepared(prepared, source_fingerprint)
    info.update(events=len(prepared["event_ids"]), players=len(prepared["document"].get("players") or []),
                records=len(prepared["document"].get("records") or []), edits_hash=prepared["edits_hash"],
                released_excluded=len(released))
    return prepared, info


def compose(edits, progression_doc, released=(), moved=None):
    """The ONE save_writer document: the league office's moves plus the progression document. A player or record
    field set by both with different values, or a progression players edit of a released player (or of a move's own
    field), is a CompositionError. Neither input is changed."""
    if not progression_doc:
        return copy.deepcopy(edits)
    released, moved = set(released or ()), dict(moved or {})
    out = copy.deepcopy(edits)
    players = {e["pgid"]: e for e in out.get("players") or []}
    for e in progression_doc.get("players") or []:
        g = e["pgid"]
        if g in released:
            raise CompositionError(f"pgid {g}: progression edit for a player this write releases")
        clash = [k for k in MOVE_FIELDS.get(moved.get(g), ()) if k in (e.get("set") or {})]
        if clash:
            raise CompositionError(f"pgid {g}: progression sets {', '.join(clash)}, which the {moved[g]} move sets")
        have = players.setdefault(g, {"pgid": g, "set": {}})
        for k, v in (e.get("set") or {}).items():
            if k in have["set"] and have["set"][k] != v:
                raise CompositionError(f"pgid {g} {k}: league office {have['set'][k]!r}, progression {v!r}")
            have["set"][k] = v
    if players:
        out["players"] = [players[g] for g in sorted(players)]
    records, index = [], {}
    for r in list(out.get("records") or []) + list(progression_doc.get("records") or []):
        key = (r.get("table"), r.get("tableId"), r.get("row"))
        if key not in index:
            index[key] = len(records)
            records.append({**r, "set": dict(r.get("set") or {})})
            continue
        have = records[index[key]]["set"]
        for k, v in (r.get("set") or {}).items():
            if k in have and have[k] != v:
                raise CompositionError(f"record {key} {k}: {have[k]!r} and {v!r} in the same write")
            have[k] = v
    if records:
        out["records"] = records
    return out


# ------------------------------------------------------------- plan_stage ----
def plan_stage(cur, snapshot, stage, seed, deps=None, wave=None, state=None, source_fingerprint=None):
    """Plans one stage: {"stage", "wave", "season", "week", "edits" (the ONE combined save_writer document),
    "edits_hash" (its canonical hash), "league_edits" (the league office's own part), "prepared" (the progression
    envelope, unchanged, or None), "progression" (what it was asked), "summary", "drift", "deferred", "warnings",
    "errors", "db" (what run() records after a successful write), ...}. Progression and composition errors raise.
    Without deps, the personality switchboard picks them (pipeline/league_office_personality.default_deps)."""
    deps = deps or default_deps(cur)
    if stage not in STAGES:
        raise StageError(f"unknown stage {stage}")
    if stage in FA_STAGES and wave is None:
        try:
            wave = stage_and_wave(snapshot)[1]
        except StageError:
            wave = None
        wave = wave or (FA_WAVES if stage == COMBINE else 1)
    season, week = league_season(snapshot, stage), league_week(snapshot, stage)
    calendar = snapshot["season"]["calendar_year"]
    begin = getattr(deps, "begin_stage", None)
    if begin is not None:
        begin(stage, season, week, wave if stage in FA_STAGES else None, seed)
    teams = deps.team_ids(cur, snapshot)
    plan = Plan(snapshot, stage, season, week, wave if stage in FA_STAGES else None, seed, teams,
                deps.pgid_map(cur), state)
    if state is None:
        plan.warn("no league state from the save: team cap fields and spare contract rows unknown")

    # 1-2. JR, then this week's auto-approved CPU deals (they lose to JR's claims, beat the stage's)
    plan.step("pending moves", lambda: step_pending(plan, deps.pending_moves(cur)))
    plan.step("engine trades", lambda: step_engine_trades(plan, deps.engine_trades(cur, season)))
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
    else:
        if stage == OFFSEASON_START:
            plan.step("salary cap", step_salary_cap, plan, deps, cur)
        # one retirement decision per offseason (RETIREMENT_WINDOW_PLAN §2.1): the first stop with no WRITTEN
        # decision run for the season decides; every later stop runs the window before any signing or cut
        window_in = plan.step("retirement window inputs", deps.retirement_window_inputs, cur, season)
        if window_in is not None:
            authority = plan.step("retirement authority", deps.retirement_authority)
            if window_in.get("decision") is None:
                rec = plan.step("retirements", step_retirements, plan, seed, deps, cur, authority,
                                window_in.get("prior_image"), source_fingerprint)
                if rec is not None:
                    plan.retirement_window = dict(rec, role="decision")
            else:
                rec = plan.step("retirement window", step_retirement_window, plan, deps, cur, source_fingerprint,
                                window_in, authority)
                if rec is not None:
                    plan.retirement_window = dict(rec, decision_run_id=window_in["decision"].get("run_id"),
                                                  decision_stop=window_in["decision"].get("stop"))
        if stage == OFFSEASON_START:
            plan.step("promoted practice squads",
                      lambda: step_promoted_ps(plan, deps.ps_without_contract(cur, calendar)))
        else:
            kept = plan.step("kept retirees", deps.kept_retirees, cur, season) or set()
            if kept:
                plan.step("restore retirees", step_restore_retirees, plan, kept, stored)
    if stage == RESIGN:
        plan.step("re-signings", lambda: step_moves(plan, deps.resign_decisions(cur, season, seed), PRIORITY_STAGE,
                                                    "resign"))
    elif stage in FA_STAGES:
        plan.step("free agency", step_free_agency, plan, deps, cur, seed)
    elif stage == DRAFT:
        def _draft():
            got = deps.draft_inputs(cur, season)
            # a deps fake (tests) may still give only the three original values: then the class has no comp schedule
            slots, boards, jr = got[0], got[1], got[2]
            comp_schedule = got[3] if len(got) > 3 else None
            if not slots:
                plan.warn("no draft order (draft_picks.overall_pick) for the coming draft: no picks planned")
                return
            step_draft(plan, slots, boards, jr, seed, comp_schedule)
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
    if rating_rows and stage in PROGRESSION_STAGES:
        plan.counts["drift_ratings_left_to_progression"] = len(rating_rows)
    elif rating_rows:
        plan.step("drift ratings", step_drift_ratings, plan, deps, rating_rows)

    # 6. progression, prepared against the players this write keeps (eligibility fixed first), then one document
    league_edits, summary = plan.finalize()
    released = set(plan.release)
    prepared, prog_info = prepare_progression(cur, deps, snapshot, stage, season, week, source_fingerprint, released)
    moved = {g: m for g, (m, _, _) in plan.claimed.items() if m in MOVE_FIELDS}
    edits = compose(league_edits, prepared["document"] if prepared else None, released, moved)
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
    for t in plan.db["engine_trades"]:
        if t.get("status") == "EXPIRED":           # past its window: expired below, never retried
            continue
        ok, reason = plan.results.get(t["tag"], (False, "not planned"))
        t["status"], t["reason"] = ("WRITTEN" if ok else "REFUSED"), (None if ok else reason)
    summary["engine_trades"] = len(plan.db["engine_trades"])
    summary["engine_trades_written"] = sum(1 for t in plan.db["engine_trades"] if t["status"] == "WRITTEN")
    summary["engine_trades_failed"] = sum(1 for t in plan.db["engine_trades"] if t["status"] == "REFUSED")
    summary["engine_trades_failed_ids"] = [t["id"] for t in plan.db["engine_trades"] if t["status"] == "REFUSED"]
    summary["engine_trades_expired"] = [t["id"] for t in plan.db["engine_trades"] if t["status"] == "EXPIRED"]
    summary["drift"] = {k: len(v) for k, v in drift.items() if isinstance(v, list)}
    summary["drift_ratings_left_to_progression"] = plan.counts.get("drift_ratings_left_to_progression", 0)
    summary["progression"] = prog_info
    summary["players_edited"] = len(edits.get("players") or [])
    summary["records"] = len(edits.get("records") or [])
    if plan.db.get("salary_cap"):
        summary["salary_cap"] = dict(plan.db["salary_cap"])
    report = getattr(deps, "personality_report", None)
    if report is not None:
        summary["personality"] = report()
    summary["retirement_window"] = retirement_window_summary(plan)
    return {"stage": stage, "wave": plan.wave, "season": season, "week": week, "calendar_year": calendar,
            "seed": seed, "edits": edits, "edits_hash": canonical_hash(edits), "league_edits": league_edits,
            "prepared": prepared, "progression": prog_info, "summary": summary, "drift": drift,
            "deferred": plan.deferred, "warnings": plan.warnings, "errors": plan.errors, "db": plan.db,
            "team_ids": plan.teams, "pid_by_pgid": plan.pid_by_pgid, "cap": plan.cap,
            "trade_cap_db": plan.trade_cap_db}


def retirement_window_summary(plan):
    """summary["retirement_window"] (RETIREMENT_WINDOW_PLAN §2.2): every run stores the roster image the save holds
    after its write ({pgid: [status, team_index]}, the next run's baseline); offseason runs add the decision record
    (role "decision") or the window record (role "window"); other runs have role "image"."""
    view = dict(plan.view)
    view.update(plan.image_overrides)
    out = {"version": rw.WINDOW_VERSION, "role": "image", "season": plan.season,
           "stop": rw.stop_name(plan.stage, plan.wave)}
    if plan.retirement_window:
        out.update(plan.retirement_window)
    out["roster_image"] = rw.roster_image(view)
    return out


def report_summary(summary):
    """The run summary as printed in the run report: the roster image is left out (it stays in league_office_runs)."""
    rwin = summary.get("retirement_window")
    if not isinstance(rwin, dict) or "roster_image" not in rwin:
        return summary
    return dict(summary, retirement_window={k: v for k, v in rwin.items() if k != "roster_image"})


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
    for r in edits.get("records") or []:
        if r.get("table") == "SalaryInfo" and r.get("row") == 0 and "TeamSalaryCap" in (r.get("set") or {}):
            written_raw = r["set"]["TeamSalaryCap"]
            sc = snapshot.get("salary_cap")
            if sc:
                raw_got = sc.get("raw_team_salary_cap")
                if raw_got != written_raw:
                    bad.append(f"SalaryInfo row 0 raw_team_salary_cap: wrote {written_raw}, read {raw_got}")
                expected_units = written_raw + ce.SALARY_CAP_OFFSET_UNITS
                units_got = sc.get("team_salary_cap")
                if units_got != expected_units:
                    bad.append(f"SalaryInfo row 0 team_salary_cap: expected {expected_units}, read {units_got}")
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
    """What a successful write makes true in the database: pending moves' statuses, the trades this write made
    (players in the save, picks in Mu's books, proposals EXECUTED, CPU transcripts published), depth charts stored
    (MU_COACH, JR), injuries.mu_weeks_out, retirement_decisions, offer statuses."""
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
    _record_trades(cur, plan, run_id)
    _apply_db(cur, plan, deps)


def _record_trades(cur, plan, run_id):
    """Every trade tag this write carried is executed in the database ONCE (TRADE_NEGOTIATIONS_PLAN §2.5): JR's
    TRADE pending moves that carry a `proposal_id`, and the engine's auto-approved deals (`("trade", pid)`). The
    save already got the player legs; front_office._execute moves the players, contracts, cap rows and picks in
    Mu's books and marks the proposal EXECUTED. An already-EXECUTED proposal is not executed again. A CPU deal
    whose flags carry `talks:<nid>` has its transcript published. A deal refused a second time (a player claimed
    each write) is expired through front_office.could_not_make. An old JR payload with no proposal_id is left to
    today's behaviour (nothing to execute)."""
    from engines import front_office as fo
    from engines import gm_talks
    db = plan.get("db") or {}
    talks = defaultdict(list)
    ids = []
    for p in db.get("pending") or []:
        payload = p.get("payload") or {}
        if p.get("kind") == "TRADE" and p.get("status") == "WRITTEN" and payload.get("proposal_id") is not None:
            ids.append(int(payload["proposal_id"]))
    for t in db.get("engine_trades") or []:
        if t.get("status") != "WRITTEN":
            continue
        ids.append(int(t["id"]))
        for f in t.get("flags") or []:
            nid = f.split(":", 1)[1] if isinstance(f, str) and f.startswith("talks:") else ""
            if nid.isdigit():
                talks[int(t["id"])].append(int(nid))
    for pid in dict.fromkeys(ids):
        try:
            proposal = fo._proposal(cur, pid)
        except Exception as e:      # noqa: BLE001 - a vanished proposal must not fail a save already written
            log.warning("trade #%s: not executed (%s)", pid, e)
            continue
        if proposal.get("status") != "EXECUTED":
            try:
                fo._execute(cur, proposal, "PROPOSED")
            except Exception as e:      # noqa: BLE001 - the save is already replaced; --recover must not wedge on it
                log.warning("trade #%s: not executed (%s)", pid, e)
                continue
            cur.execute("UPDATE gm_trade_proposals SET status = 'EXECUTED', "
                        "executed_at = COALESCE(executed_at, now()) WHERE id = %s", (pid,))
        for nid in talks.get(pid, []):
            gm_talks.publish_talk(cur, nid)
    for t in db.get("engine_trades") or []:
        expired = t.get("status") == "EXPIRED"
        if expired or (t.get("status") == "REFUSED" and int(t.get("refusals") or 0) >= 1):
            why = (t.get("reason") or "its trade window closed") if expired else \
                "the league office couldn't make it"
            try:
                fo.could_not_make(cur, t["id"], why)
            except Exception as e:      # noqa: BLE001 - already expired / gone: the retry is over either way
                log.warning("trade #%s: could not be expired (%s)", t["id"], e)


def _apply_db(cur, plan, deps=None):
    """Post-write database synchronization: syncs team_cap_seasons with new salary cap and move deltas."""
    if deps is None:
        deps = Deps()
    db = plan.db if hasattr(plan, "db") else (plan.get("db") or {})
    info = db.get("salary_cap")
    if not info:
        return
    season = info.get("season")
    cap_dollars = info.get("cap")
    if season is None or cap_dollars is None:
        return
    deps.sync_team_caps(cur, season, cap_dollars)

    cap_deltas = plan.cap if hasattr(plan, "cap") else (plan.get("cap") or {})
    teams = plan.teams if hasattr(plan, "teams") else (plan.get("team_ids") or {})
    teams_map = {int(k): v for k, v in teams.items()}
    # trades record_written executes move their own team_cap_seasons rows (trade_moves); leave their room out here
    trade_cap = plan.trade_cap_db if hasattr(plan, "trade_cap_db") else (plan.get("trade_cap_db") or {})
    trade_room = {int(k): v[0] for k, v in trade_cap.items()}

    madden_cap = info.get("madden_cap")
    if madden_cap is not None:
        cap_delta_units = (cap_dollars // ce.MONEY_UNIT) - (madden_cap // ce.MONEY_UNIT)
    else:
        cap_delta_units = 0

    for team_idx, deltas in cap_deltas.items():
        team_id = teams_map.get(int(team_idx))
        if team_id is None:
            continue
        room_units = deltas[0] - trade_room.get(int(team_idx), 0)
        move_room = room_units - cap_delta_units
        if move_room == 0:
            continue
        delta_dollars = move_room * ce.MONEY_UNIT
        cur.execute("""
            UPDATE team_cap_seasons
            SET cap_space = cap_space + %s,
                total_spend = total_spend - %s
            WHERE team_id = %s AND season = %s
        """, (delta_dollars, delta_dollars, team_id, season))


def replace_save(src, dest):
    """Atomically replaces `dest` with `src` (copy next to dest, then os.replace)."""
    tmp = dest + ".mu-league-office"
    shutil.copyfile(src, tmp)
    os.replace(tmp, dest)


# --------------------------------------------------- the operation record ----
def plan_record(plan):
    """What record_written needs, as JSON (stored with the operation so --recover can finish the run)."""
    return {"db": plan["db"], "season": plan["season"], "week": plan["week"], "calendar_year": plan["calendar_year"],
            "team_ids": {str(k): v for k, v in plan["team_ids"].items()},
            "pid_by_pgid": {str(k): v for k, v in plan["pid_by_pgid"].items()},
            "cap": {str(k): v for k, v in (plan.get("cap") or {}).items()},
            "trade_cap_db": {str(k): v for k, v in (plan.get("trade_cap_db") or {}).items()}}


def plan_from_record(record, edits):
    """record_written's plan dict back from a stored operation record."""
    return {"db": record["db"], "season": record["season"], "week": record["week"],
            "calendar_year": record["calendar_year"], "edits": edits,
            "team_ids": {int(k): v for k, v in record["team_ids"].items()},
            "pid_by_pgid": {int(k): v for k, v in record["pid_by_pgid"].items()},
            "cap": {int(k): v for k, v in (record.get("cap") or {}).items()},
            "trade_cap_db": {int(k): v for k, v in (record.get("trade_cap_db") or {}).items()}}


def new_operation(plan, run_id, save_path, input_sha256):
    """The durable operation record (§5.3a step 2): identity, fingerprints, the unchanged prepared envelope and the
    combined document's hash. The combined document itself is the run's edits column."""
    ident = canonical_hash({"run_id": run_id, "save": os.path.abspath(save_path), "stage": plan["stage"],
                            "season": plan["season"], "week": plan["week"], "input_sha256": input_sha256,
                            "combined_hash": plan["edits_hash"],
                            "progression_hash": (plan["prepared"] or {}).get("edits_hash")})
    return {"operation_id": ident, "run_id": run_id, "phase": "PREPARED", "save_path": os.path.abspath(save_path),
            "candidate_path": None, "edits_path": None, "input_sha256": input_sha256, "candidate_sha256": None,
            "output_sha256": None, "combined_hash": plan["edits_hash"], "prepared": plan["prepared"],
            "progression": plan["progression"], "record": plan_record(plan), "acknowledged_events": None}


def _store_operation(cur, run_id, op, summary, edits=None):
    cur.execute("UPDATE league_office_runs SET edits = COALESCE(%s::jsonb, edits), summary = %s::jsonb WHERE id = %s",
                (json.dumps(edits) if edits is not None else None,
                 json.dumps(dict(summary, operation=op), default=str), run_id))


def _load_run(cur, run_id):
    cur.execute("SELECT id, status, stage, save_name, edits, summary FROM league_office_runs WHERE id = %s",
                (run_id,))
    row = cur.fetchone()
    if not row:
        raise RecoveryError(f"no league_office_runs row {run_id}")
    load = lambda v: json.loads(v) if isinstance(v, str) else (v or {})    # noqa: E731
    return {"id": row[0], "status": row[1], "stage": row[2], "save_name": row[3], "edits": load(row[4]),
            "summary": load(row[5])}


# -------------------------------------------------------------------- run ----
def _conninfo(cur):
    try:
        return cur.connection.info.dsn
    except Exception:      # noqa: BLE001 - a fake cursor in tests
        return ls.DSN


def _user_team_ids(cur):
    try:
        return Deps().user_team_ids(cur)
    except Exception:      # noqa: BLE001
        return set()


def _annual_summary(name, got):
    """The small, JSON-safe part of a producer's report (never a built article's text)."""
    if not isinstance(got, dict):
        return {"status": "ok"}
    keep = ("status", "season", "draft_season", "teams", "upgraded", "orders", "qfas", "awards", "draft_picks",
            "filed", "rolled", "fired", "payroll", "article_id", "articles_written")
    return {k: got[k] for k in keep if k in got} | {"status": got.get("status", "ok")}


def annual_offseason_steps(cur, stage, season, wave, out=print):
    """The league office's once-a-season DB producers and articles, run AFTER a WRITTEN FREE_AGENCY write (wave 1).

    Order: comp picks (before the draft, so lock_order sees them), staff contracts (payroll is current before the
    ledger), report cards + owner cash ledgers + CPU facility upgrades, then the two annual articles. Every step is
    optional and warning-only: a failure is recorded in the summary and never fails the write. Each runs in its own
    savepoint and is committed before the next, so the articles (which open their own connection) see the rows.
    Returns {step: summary} or None when this stage isn't an annual one.

    Comp picks re-run on EVERY free-agency wave (JR, 2026-11-09): `award_and_persist` deletes and re-inserts the
    season's QFA / award / comp-pick rows, so the last wave's signings are the ones the ledger counts — gating it on
    wave 1 missed wave 2 and the combine wave. Staff contracts, report cards and their articles still run once,
    after free agency's first wave."""
    annual_stage = stage in FA_STAGES
    if not annual_stage:
        return None
    first_wave = stage == FREE_AGENCY and (wave or 1) == 1
    conn = cur.connection
    results = {}

    def optional(name, fn):
        save = f"annual_{name.replace('-', '_')}"
        try:
            cur.execute(f"SAVEPOINT {save}")
            got = fn()
            cur.execute(f"RELEASE SAVEPOINT {save}")
            conn.commit()
            results[name] = _annual_summary(name, got)
            return got
        except Exception as e:      # noqa: BLE001 - an annual step never fails the write
            try:
                cur.execute(f"ROLLBACK TO SAVEPOINT {save}")
                cur.execute(f"RELEASE SAVEPOINT {save}")
            except Exception:       # noqa: BLE001
                pass
            log.exception("annual step %s failed", name)
            results[name] = {"status": "FAILED", "error": f"{type(e).__name__}: {e}"}
            return None

    from engines import compensatory_picks_run, report_card_run, staff_contracts_run
    optional("compensatory_picks", lambda: compensatory_picks_run.award_and_persist(cur, season, season + 1))
    if first_wave:
        optional("staff_contracts", lambda: staff_contracts_run.annual(cur, season))
        optional("report_cards", lambda: report_card_run.annual(cur, season, user_team_ids=_user_team_ids(cur)))
    dsn = _conninfo(cur)

    def comp_article():
        from newsroom import compensatory_picks_article
        # force: the article re-runs on every free-agency wave, so wave 2 and 3 must replace wave 1's row rather
        # than answering SKIPPED_EXISTING.
        return compensatory_picks_article.run(season + 1, season=season, publish=True, conn_info=dsn, force=True)

    def card_article():
        from newsroom import report_card_article
        return report_card_article.run(season, publish=True, conn_info=dsn)

    optional("compensatory_picks_article", comp_article)
    if first_wave:
        optional("report_card_article", card_article)
    out(json.dumps({"annual_steps": results}, indent=1, default=str))
    return results


def run(cur, snapshot, save_path, stage="auto", dry_run=False, confirm_franchise_closed=False, seed=None,
        deps=None, wave=None, out=print):
    """Plans and writes one stage; returns the league_office_runs id. See the module docstring for the rules and
    the receipt protocol. Without deps, the personality switchboard picks them (default_deps); the frozen mode
    snapshot goes into the operation record so --recover reuses it."""
    deps = deps or default_deps(cur)
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
        _insert_run(cur, meta, save_name, "FAILED", error=msg)
        conn.commit()
        raise FranchiseOpenError(msg)
    input_sha = file_sha256(save_path)
    try:
        state = deps.league_state(save_path)
    except Exception as e:      # noqa: BLE001 - planned without it, with a warning
        log.warning("league state unavailable: %s", e)
        state = None
    run_id = None
    if not dry_run:
        run_id = _insert_run(cur, meta, save_name, "PLANNED")
        conn.commit()
    bind = getattr(deps, "bind_run", None)
    if bind is not None:
        bind(run_id)
    try:
        plan = plan_stage(cur, snapshot, stage, seed, deps=deps, wave=wave, state=state, source_fingerprint=input_sha)
    except Exception as e:
        conn.rollback()                          # nothing prepared survives
        if run_id is not None:
            _finish_run(cur, run_id, "FAILED", error=f"planning failed: {type(e).__name__}: {e}")
            conn.commit()
        raise
    report = {"stage": plan["stage"], "wave": plan["wave"], "season": plan["season"], "week": plan["week"],
              "summary": report_summary(plan["summary"]), "deferred": plan["deferred"], "warnings": plan["warnings"],
              "errors": plan["errors"], "input_sha256": input_sha, "combined_hash": plan["edits_hash"],
              "progression_edits_hash": (plan["prepared"] or {}).get("edits_hash")}
    if dry_run:
        proposals = None
        if plan["prepared"] is not None:
            try:
                proposals = deps.progression_explain(cur, plan["prepared"])
            except Exception as e:      # noqa: BLE001 - reports are a log, not the plan
                plan["warnings"].append(f"proposal reports unavailable: {e}")
        conn.rollback()                      # prepared events, offers, claims: all rolled back; nothing acknowledged
        run_id = _insert_run(cur, meta, save_name, "DRY_RUN", plan["edits"], plan["summary"], plan["drift"],
                             "; ".join(plan["errors"]) or None)
        conn.commit()
        out(json.dumps(dict(report, run_id=run_id, edits=plan["edits"], progression_proposals=proposals),
                       indent=1, default=str))
        return run_id
    if plan["errors"]:
        conn.rollback()
        _finish_run(cur, run_id, "FAILED", plan["edits"], plan["summary"], plan["drift"],
                    "planning errors, nothing written: " + "; ".join(plan["errors"]))
        conn.commit()
        out(json.dumps(dict(report, run_id=run_id, status="FAILED"), indent=1, default=str))
        return run_id
    # step 2: PREPARED operation (events + unchanged envelope + combined document) committed before any write
    op = new_operation(plan, run_id, save_path, input_sha)
    op["personality_modes"] = getattr(deps, "modes_snapshot", None)
    _store_operation(cur, run_id, op, plan["summary"], plan["edits"])
    conn.commit()

    def fail(error):
        conn.rollback()
        op["phase"] = "FAILED"
        _store_operation(cur, run_id, op, plan["summary"])
        _finish_run(cur, run_id, "FAILED", error=error)
        conn.commit()
        out(json.dumps(dict(report, run_id=run_id, status="FAILED", error=error), indent=1, default=str))
        return run_id

    if not plan["edits"]:
        if plan["prepared"] is not None and plan["prepared"]["event_ids"]:
            return fail("progression events prepared but the document is empty: nothing can be verified")
        record_written(cur, plan, run_id, deps)
        op["phase"] = "ACKNOWLEDGED"
        _store_operation(cur, run_id, op, plan["summary"])
        _finish_run(cur, run_id, "WRITTEN")
        conn.commit()
        if getattr(deps, "annual_steps", False):
            report["annual_steps"] = annual_offseason_steps(cur, plan["stage"], plan["season"], plan["wave"], out)
        out(json.dumps(dict(report, run_id=run_id, status="WRITTEN", replaced=False), indent=1, default=str))
        return run_id
    with tempfile.TemporaryDirectory(prefix="mu-league-office-") as tmp:
        edits_path = os.path.join(tmp, "edits.json")
        copy_path = os.path.join(tmp, save_name)
        with open(edits_path, "w") as fh:
            json.dump(plan["edits"], fh)
        # step 3: candidate copy, whole-document writer read-back, then our own read-back
        ok, text = deps.write_save(save_path, edits_path, copy_path)
        if not ok:
            return fail(f"save_writer refused or read-back failed: {text[-2000:]}")
        try:
            bad = verify_read_back(plan["edits"], deps.read_snapshot(copy_path))
        except Exception as e:      # noqa: BLE001
            bad = [f"reading the written copy failed: {e}"]
        if bad:
            return fail("read-back of the written copy failed: " + "; ".join(bad[:20]))
        if file_sha256(save_path) != input_sha:
            return fail("the save changed while the league office ran (input sha256 differs); not replaced")
        op.update(phase="CANDIDATE_VERIFIED", candidate_path=copy_path, edits_path=edits_path,
                  candidate_sha256=file_sha256(copy_path))
        _store_operation(cur, run_id, op, plan["summary"])
        conn.commit()
        if not confirmed and deps.madden_running():
            return fail("Madden27.exe started during the run; the save was not replaced (pass "
                        "--confirm-franchise-closed once the franchise is closed)")
        # step 4: replace, fingerprint the output, acknowledge the unchanged envelope, commit
        replace_save(copy_path, save_path)
    try:
        output_sha = file_sha256(save_path)
        if output_sha != op["candidate_sha256"]:
            raise RecoveryError(f"the replaced save's sha256 {output_sha} is not the verified candidate's")
        op.update(phase="REPLACED", output_sha256=output_sha)
        _store_operation(cur, run_id, op, plan["summary"])
        conn.commit()
        acknowledged = _acknowledge(cur, deps, op, save_path, output_sha)
        record_written(cur, plan, run_id, deps)
        op.update(phase="ACKNOWLEDGED", acknowledged_events=acknowledged)
        _store_operation(cur, run_id, op, plan["summary"])
        _finish_run(cur, run_id, "WRITTEN")
        conn.commit()
    except Exception as e:
        conn.rollback()
        msg = (f"the save was replaced but acknowledging / recording failed ({type(e).__name__}: {e}); run "
               f"`python3 -m pipeline.league_office --recover {run_id}`")
        log.error(msg)
        out(json.dumps(dict(report, run_id=run_id, status="NEEDS_RECOVERY", error=msg), indent=1, default=str))
        raise RecoveryError(msg) from e
    if getattr(deps, "annual_steps", False):
        report["annual_steps"] = annual_offseason_steps(cur, plan["stage"], plan["season"], plan["wave"], out)
    out(json.dumps(dict(report, run_id=run_id, status="WRITTEN", replaced=True, output_sha256=output_sha,
                        acknowledged_events=acknowledged), indent=1, default=str))
    return run_id


def _acknowledge(cur, deps, op, save_path, output_sha):
    """Reads the output save and acknowledges the ORIGINAL prepared envelope (None when no progression ran).
    Returns the number of acknowledged events."""
    prepared = op.get("prepared")
    if prepared is None:
        return 0
    if canonical_hash(prepared["document"]) != prepared["edits_hash"]:
        raise RecoveryError("the stored progression envelope no longer matches its edits_hash")
    output_snapshot = deps.read_snapshot(save_path)
    events = deps.progression_acknowledge(cur, prepared, output_snapshot, output_sha)
    return len(events or [])


def recover(cur, run_id, save_path=None, deps=None, out=print):
    """Finishes a run whose save replacement may have happened without the acknowledgement / commit (§5.3a step 5).
    Never recalculates, re-rolls or advances: only the stored operation and the save's observed sha256 are used.
    Without deps, they are rebuilt from the operation's stored personality mode snapshot, never the switchboard."""
    conn = cur.connection
    row = _load_run(cur, run_id)
    op = (row["summary"] or {}).get("operation")
    if deps is None:
        from pipeline import league_office_personality as lop
        deps = lop.deps_for(cur, (op or {}).get("personality_modes"))
    if row["status"] == "WRITTEN":
        out(json.dumps({"run_id": run_id, "status": "WRITTEN", "note": "already acknowledged; nothing to do"}))
        return "ALREADY_WRITTEN"
    if not op:
        raise RecoveryError(f"run {run_id} has no stored operation record ({row['status']})")
    if row["status"] != "PLANNED" or op.get("phase") not in ("PREPARED", "CANDIDATE_VERIFIED", "REPLACED"):
        raise RecoveryError(f"run {run_id} is {row['status']} (phase {op.get('phase')}): nothing to recover")
    path = save_path or op["save_path"]
    current = file_sha256(path)
    summary = {k: v for k, v in row["summary"].items() if k != "operation"}
    replaced_sha = op.get("output_sha256") or op.get("candidate_sha256")
    if replaced_sha and current == replaced_sha:
        acknowledged = _acknowledge(cur, deps, op, path, current)
        record_written(cur, plan_from_record(op["record"], row["edits"]), run_id, deps)
        op.update(phase="ACKNOWLEDGED", output_sha256=current, acknowledged_events=acknowledged, recovered=True)
        _store_operation(cur, run_id, op, summary)
        _finish_run(cur, run_id, "WRITTEN")
        conn.commit()
        out(json.dumps({"run_id": run_id, "status": "WRITTEN", "recovered": True, "output_sha256": current,
                        "acknowledged_events": acknowledged}))
        return "ACKNOWLEDGED"
    if current == op["input_sha256"]:
        op["phase"] = "FAILED"
        _store_operation(cur, run_id, op, summary)
        _finish_run(cur, run_id, "FAILED", error="recovery: the save was never replaced; nothing awarded")
        conn.commit()
        out(json.dumps({"run_id": run_id, "status": "FAILED", "note": "the save was never replaced"}))
        return "NOT_REPLACED"
    raise RecoveryError(f"{path} has sha256 {current}, neither the input ({op['input_sha256']}) nor the verified "
                        f"output ({replaced_sha}); refusing to acknowledge")


# ------------------------------------------------------------------ setup ----
def setup(save, out_path, cur=None, deps=None):
    """The one-time franchise setup: league_setup.mjs writes a NEW neutral save (all 32 teams real users, fill
    roster off, CoachFiring OFF, career clock off, every player ConfidenceRating 50), then progression's explicit
    neutral bootstrap on that save (bootstrap_pass, form_neutral=True) is committed. Never writes `save`;
    `out_path` must not exist and is removed again if the bootstrap fails. Returns the setup report."""
    deps = deps or Deps()
    if os.path.exists(out_path):
        raise FileExistsError(f"{out_path} exists")
    ok, stdout, stderr = deps.run_setup(save, out_path)
    if not ok:
        raise RuntimeError(stderr)
    if stderr:
        print(stderr, file=sys.stderr)
    report = json.loads(stdout) if stdout else {}
    if cur is None:
        return report
    conn = cur.connection
    try:
        snapshot = deps.read_snapshot(out_path)
        off = [p.get("pgid") for p in snapshot.get("players") or []
               if (p.get("pgid") or 0) > 0 and p.get("confidence") != 50]
        if off:
            raise RuntimeError(f"{len(off)} players are not at ConfidenceRating 50 in the new save (first: {off[:5]})")
        stage = detect_stage(snapshot)
        season = setup_application_season(stage, league_season(snapshot, stage))
        if deps.applied_form_rows(cur, season):
            raise RuntimeError(f"player_form already has rows for {season}: form isn't known-neutral; setup is for a "
                               "franchise Project Mu hasn't written yet")
        if not deps.pgid_map(cur):
            raise RuntimeError("no players with a madden_pgid: load the save (pipeline.load_save) before setup")
        sha = file_sha256(out_path)
        states = deps.progression_bootstrap(cur, snapshot, season, sha)
        conn.commit()
    except Exception:
        conn.rollback()
        try:
            os.remove(out_path)
        except OSError:
            pass
        raise
    report.update(progression_bootstrap={"application_season": season, "stage": stage, "source_sha256": sha,
                                         "players_bootstrapped": len(states or [])})
    return report


# -------------------------------------------------------------------- CLI ----
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "setup":
        sp = argparse.ArgumentParser(prog="python3 -m pipeline.league_office setup",
                                     description="One-time franchise setup: every team a real user team, fill roster "
                                                 "off, CoachFiring OFF, career clock off, every player ConfidenceRating "
                                                 "50 (league_setup.mjs), then progression's neutral bootstrap on the "
                                                 "new save.")
        sp.add_argument("--save", required=True, help="CAREER save to read (never written)")
        sp.add_argument("--out", required=True, help="new save to write (must not exist)")
        sp.add_argument("--dsn", default=ls.DSN, help="database (default: DATABASE_URL or the local madden database)")
        a = sp.parse_args(argv[1:])
        if os.path.exists(a.out):
            print(f"SETUP FAILED: {a.out} exists", file=sys.stderr)
            return 1
        try:
            import psycopg
            with psycopg.connect(a.dsn) as conn:
                report = setup(a.save, a.out, conn.cursor())
        except (RuntimeError, FileExistsError) as e:
            print(f"SETUP FAILED: {e}", file=sys.stderr)
            return 1
        print(json.dumps(report.get("progression_bootstrap"), default=str))
        return 0
    ap = argparse.ArgumentParser(
        prog="python3 -m pipeline.league_office",
        description="League office stage loop: plans Project Mu's moves for the save's stage, composes them with the "
                    "progression engine's prepared document into one save_writer document, writes a temp copy, reads "
                    "it back, replaces the save and acknowledges the progression receipt.",
        epilog="subcommand: python3 -m pipeline.league_office setup --save <CAREER-file> --out <new-file> "
               f"(one-time franchise setup and progression bootstrap). Stages: {', '.join(STAGES)}.")
    ap.add_argument("--snapshot", help="JSON written by pipeline/save_reader/pc_save_reader.mjs (required unless "
                                       "--recover)")
    ap.add_argument("--save", help="the CAREER save the snapshot was read from (required unless --recover)")
    ap.add_argument("--stage", default="auto", help="auto (default: from the snapshot) or a stage name")
    ap.add_argument("--wave", type=int, choices=range(1, FA_WAVES + 1), help="free-agency wave (default: from the save)")
    ap.add_argument("--seed", help="default: league id, year, stage, wave and week")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and progression proposals; no save, nothing acknowledged, one DRY_RUN row")
    ap.add_argument("--recover", type=int, metavar="RUN_ID",
                    help="finish a run whose save was replaced but not acknowledged (never re-plans)")
    ap.add_argument("--dsn", default=ls.DSN, help="database (default: DATABASE_URL or the local madden database)")
    ap.add_argument("--confirm-franchise-closed", action="store_true",
                    help=f"Madden is running but the franchise is closed (also {FRANCHISE_CLOSED_ENV}=1)")
    a = ap.parse_args(argv)
    if a.recover is None and (not a.snapshot or not a.save):
        ap.error("--snapshot and --save are required (or --recover RUN_ID)")
    stage = a.stage.upper() if a.stage.lower() != "auto" else "auto"
    if stage != "auto" and stage not in STAGES:
        ap.error(f"--stage must be auto or one of {', '.join(STAGES)}")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    import psycopg
    if a.recover is not None:
        with psycopg.connect(a.dsn) as conn:
            try:
                recover(conn.cursor(), a.recover, a.save)
            except RecoveryError as e:
                print(f"RECOVERY REFUSED: {e}", file=sys.stderr)
                return 1
        return 0
    with open(a.snapshot) as fh:
        snapshot = json.load(fh)
    with psycopg.connect(a.dsn) as conn:
        cur = conn.cursor()
        try:
            run(cur, snapshot, a.save, stage, a.dry_run, a.confirm_franchise_closed, a.seed, wave=a.wave)
        except (FranchiseOpenError, StageError, ls.LoadError, ProgressionIntegrationError, CompositionError,
                RecoveryError) as e:
            print(f"LEAGUE OFFICE STOPPED: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
