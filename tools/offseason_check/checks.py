"""The e2e run's checks over a written save copy and the scratch ledger.

The comparisons are pure functions over plain data (a pc_save_reader snapshot, ledger rows, small lookups), so the
tests drive them offline; the `db_*` helpers gather that data from a scratch cursor and never commit.
"""
import json
import os
import subprocess
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WIRING = os.path.join(ROOT, "tools", "offseason_check", "save_wiring.mjs")
SEATS = (("hc", "HC"), ("oc", "OC"), ("dc", "DC"))
CONTRACT_FIELDS = ("ContractLength", "ContractSalary", "ContractYearsRemaining")


# ----------------------------------------------------------------------------------------------- pure checks
def seat_contract_problems(snapshot, profile_of, team_of, wanted):
    """Every seated coach's save Contract* against his live staff_contracts row.

    profile_of: {presentation_id: (person_kind, person_id, source)}; team_of: {team_index: teams.id};
    wanted(team_id, staff_kind, person_id) -> {ContractLength, ContractSalary, ContractYearsRemaining} or None.
    Returns (seats checked, by-source Counter, [problem strings]). A seat is a problem when its coach has no Mu
    profile, any Contract* field is 0, there is no live row, or the fields differ from the row."""
    coaches = {c.get("pgid"): c for c in snapshot.get("coaches") or []}
    seats, by_source, problems = 0, Counter(), []
    for seat in snapshot.get("staff") or []:
        tid = team_of.get(seat.get("team_index"))
        for key, kind in SEATS:
            pg = seat.get(key)
            seats += 1
            c = coaches.get(pg) or {}
            name = f"{c.get('first_name', '?')} {c.get('last_name', '?')}"
            label = f"team_index {seat.get('team_index')} {kind} {name} (pgid {pg})"
            got = (c.get("contract_length"), c.get("contract_salary"), c.get("contract_years_remaining"))
            prof = profile_of.get(pg)
            if prof is None:
                problems.append(f"{label}: no Mu profile for this coach")
                continue
            by_source[prof[2]] += 1
            if 0 in got or None in got:
                problems.append(f"{label}: save Contract* is {got}")
            want = wanted(tid, kind, prof[1]) if tid is not None else None
            if want is None:
                problems.append(f"{label}: no live staff_contracts row")
                continue
            exp = tuple(want[f] for f in CONTRACT_FIELDS)
            if got != exp:
                problems.append(f"{label}: save {got} != ledger {exp}")
    return seats, by_source, problems


def tenure_rule(years, start_season, season):
    """annual()'s rule for a deal filed without other information: signed at the tenure's start, the years left
    from there; a deal that would already be over starts afresh. Returns (total_years, remaining_years)."""
    start = int(start_season if start_season is not None else season)
    left = min(years, max(0, years - (int(season) - start)))
    return (years, left) if left > 0 else (years, years)


def start_years_problems(staff, live_row, save_contract, years_of, season):
    """F1 (JR's decision relayed in M-0225): each starting coach's Mu deal takes its years from Madden's save.

    staff: current_staff(season) dicts; live_row(team_id, kind, person_id) -> the league-start row or None;
    save_contract(team_id, kind, person_id) -> (ContractLength, ContractYearsRemaining) from the original
    snapshot, or None (no save record: a GM, or a coach Mu created); years_of(kind) -> the producer's term.
    Expected: the save's (length, remaining) when remaining > 0; otherwise annual's tenure-start rule.
    Returns (rows checked, [problem strings])."""
    problems, checked = [], 0
    for s in staff:
        key = (s["team_id"], s["person_kind"], s["person_id"])
        row = live_row(*key)
        if row is None:
            problems.append(f"{s['person_kind']} {s.get('name')}: no league-start row")
            continue
        checked += 1
        save = save_contract(*key)
        if save and int(save[1] or 0) > 0:
            exp = (int(save[0] or row["total_years"]), int(save[1]))
            why = "save"
        else:
            exp = tenure_rule(years_of(s["person_kind"]), s.get("start_season"), season)
            why = "tenure rule"
        got = (int(row["total_years"]), int(row["remaining_years"]))
        if got != exp:
            problems.append(f"team {s['team_id']} {s['person_kind']} {s.get('name')}: ledger {got[0]}/{got[1]} "
                            f"(total/remaining), expected {exp[0]}/{exp[1]} by the {why}")
    return checked, problems


def expectation_problems(rows, names, teams, expectations):
    """The --expect hook: each expectation {team, kind, [name], [signed_season], field: value...} must match
    exactly one live row whose listed fields equal the values. rows: ledger dicts; names {(kind, pid): name};
    teams {team_id: abbreviation}."""
    meta = {"team", "kind", "name", "signed_season", "note"}
    problems = []
    for e in expectations:
        hits = [r for r in rows if int(r["remaining_years"]) > 0 and teams.get(r["team_id"]) == e.get("team")
                and r["person_kind"] == e.get("kind")
                and ("name" not in e or names.get((r["person_kind"], r["person_id"])) == e["name"])
                and ("signed_season" not in e or int(r["signed_season"]) == int(e["signed_season"]))]
        label = f"{e.get('team')} {e.get('kind')} {e.get('name', '')}".strip()
        if len(hits) != 1:
            problems.append(f"{label}: {len(hits)} live rows match, expected 1")
            continue
        for f, v in e.items():
            if f not in meta and hits[0].get(f) != v:
                problems.append(f"{label}: {f} is {hits[0].get(f)}, expected {v}")
    return problems


def projection_summary(report, season):
    """From an invariant report projected through annual(season) and annual(season + 1): the people re-signed for
    season + 1 (a new deal at the same team and job, after one that covered `season`) and the buyouts first charged
    in season + 1, e.g. the Black Monday firings. Each buyout is (team, kind, who, buyout_total, buyout_years)."""
    nxt = int(season) + 1
    by_person = {}
    for d in report.get("deals") or []:
        by_person.setdefault((d["team"], d["kind"], d["person_id"]), []).append(d)
    resigned = []
    for (team, kind, _pid), deals in sorted(by_person.items(), key=str):
        starts = [int(d["deal"].split("-")[0]) for d in deals]
        if nxt in starts and any(s <= season for s in starts):
            resigned.append(f"{team} {kind} {deals[0]['who']}")
    buyouts = [(d["team"], d["kind"], d["who"], d["buyout_total"], d["buyout_years"])
               for d in report.get("deals") or [] if d.get("fired_season") == nxt and d.get("buyout_total")]
    return {"resigned": resigned, "buyouts": sorted(buyouts, key=str)}


def staff_action_counts(staff_actions):
    c = Counter(a.get("action") for a in staff_actions or [])
    return {"release": c.get("release", 0), "assign": c.get("assign", 0), "create": c.get("create", 0),
            "with_contract": sum(1 for a in staff_actions or [] if a.get("contract"))}


# ----------------------------------------------------------------------------------------------- data gathering
def db_profile_of(cur):
    cur.execute("SELECT presentation_id, person_kind, person_id, source FROM coach_madden_profiles "
                "WHERE presentation_id IS NOT NULL")
    return {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}


def db_live_rows(cur):
    from tools.offseason_check import invariant as inv
    return [r for r in inv.snapshot_rows(cur) if int(r["remaining_years"]) > 0]


def db_duplicates(cur):
    """(live duplicates by (team, kind, person), exact duplicate rows by (team, kind, person, signed_season))."""
    cur.execute("""SELECT team_id, person_kind, person_id, count(*) FROM staff_contracts WHERE remaining_years > 0
                   GROUP BY 1, 2, 3 HAVING count(*) > 1""")
    live = cur.fetchall()
    cur.execute("""SELECT team_id, person_kind, person_id, signed_season, count(*) FROM staff_contracts
                   GROUP BY 1, 2, 3, 4 HAVING count(*) > 1""")
    return live, cur.fetchall()


def save_wiring(save_path, timeout=900):
    """league_setup.mjs's verifySetup (the four user links on all 32 teams) plus empty seats, on a save copy."""
    p = subprocess.run(["node", WIRING, save_path], capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    if p.returncode != 0:
        raise RuntimeError(f"save_wiring.mjs failed: {p.stderr.strip()[-800:]}")
    return json.loads(p.stdout.strip().splitlines()[-1])
