"""Staff-contract producer (STAFF_CONTRACTS_AND_FULL_CONTROL_PLAN U1 follow-up; CLOSE_OUT_CHECKLIST P1-22).

`engines/staff_contracts_engine.py` is pure and the schema (`2027_02_07_staff_contracts_and_full_control.sql`) has
`staff_contracts`, but nothing filed, rolled or settled a head coach's or coordinator's contract in the weekly run:
only U2's GM search wrote a GM row and a rare Godfather acceptance a full-control HC row. This producer, run at the
league office's offseason FREE_AGENCY stage, makes the ledger live:

  * **Files** one contract per current HC/OC/DC (market salary from the engine's scales, 4 years for a head coach
    and 3 for a coordinator, signed at the season his tenure started).
  * **Rolls** `remaining_years` deterministically (`total_years - (season - signed_season)`, never up), so running
    the producer twice in a season changes nothing.
  * **Settles a firing**: a contract whose person's tenure has ended moves the remaining guaranteed salary into
    `buyout_owed` (`staff_contracts_engine.fire_contract`) and zeroes `remaining_years`.
  * Reports `payroll` (`staff_contracts_engine.staff_payroll` over the active rows), which the report-card producer
    writes to `owner_cash_ledgers.staff_payroll` so the owner cash pool deducts it.

CLI:
  python3 -m engines.staff_contracts_run --season S [--dry-run] [--dsn DSN]
"""
from __future__ import annotations

import argparse
import json
import os

import psycopg

from engines import staff_contracts_engine as sce

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
HC_YEARS, COORD_YEARS = 4, 3


def _table(cur, name):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (name,))
    row = cur.fetchone()
    return bool(row and row[0])


def current_staff(cur, season):
    """[{team_id, person_kind, person_id, name, start_season, experience, championships}] for each team's current
    HC/OC/DC (the tenure covering `season`; a later-starting tenure wins)."""
    out, seen = [], set()

    def add(team_id, kind, person_id, name, start_season, championships):
        if (team_id, kind) in seen:
            return
        seen.add((team_id, kind))
        out.append({"team_id": team_id, "person_kind": kind, "person_id": person_id, "name": name,
                    "start_season": start_season, "championships": championships})

    cur.execute("""SELECT t.team_id, t.coach_id, c.coach_name, t.start_season, COALESCE(r.super_bowl_wins, 0)
                   FROM coach_tenures t JOIN coaches c ON c.id = t.coach_id
                   LEFT JOIN coach_records r ON r.coach_id = t.coach_id
                   WHERE t.start_season <= %s AND (t.end_season IS NULL OR t.end_season >= %s)
                   ORDER BY t.team_id, t.start_season DESC, t.id DESC""", (season, season))
    for r in cur.fetchall():
        add(r[0], "HC", r[1], r[2], r[3], int(r[4] or 0))
    if _table(cur, "coordinator_tenures"):
        cur.execute("""SELECT t.team_id, t.role, t.coordinator_id, c.coordinator_name, t.start_season
                       FROM coordinator_tenures t JOIN coordinators c ON c.id = t.coordinator_id
                       WHERE t.start_season <= %s AND (t.end_season IS NULL OR t.end_season >= %s)
                       ORDER BY t.team_id, t.role, t.start_season DESC, t.id DESC""", (season, season))
        for r in cur.fetchall():
            add(r[0], r[1], r[2], r[3], r[4], 0)
    for s in out:
        if s["person_kind"] == "HC":
            cur.execute("SELECT count(*) FROM coach_tenures WHERE coach_id = %s", (s["person_id"],))
        else:
            cur.execute("SELECT count(*) FROM coordinator_tenures WHERE coordinator_id = %s", (s["person_id"],))
        s["experience"] = int(cur.fetchone()[0] or 0)
    return out


def _years(kind):
    return HC_YEARS if kind == "HC" else COORD_YEARS


def _salary(kind, experience, championships):
    # the engine prices OC and DC on the shared COORD market itself
    return sce.market_salary(kind, experience=experience, championships=championships)


def contracts(cur):
    """Every staff_contracts row as a dict, oldest first."""
    cur.execute("""SELECT id, team_id, person_id, person_kind, annual_salary, total_years, remaining_years,
                          signed_season, full_control, buyout_owed FROM staff_contracts ORDER BY id""")
    cols = ("id", "team_id", "person_id", "person_kind", "annual_salary", "total_years", "remaining_years",
            "signed_season", "full_control", "buyout_owed")
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def annual(cur, season, dry_run=False):
    """File, roll and settle the HC/OC/DC contracts for `season` (the offseason's league year). Returns
    {season, status, filed, rolled, fired, payroll}. Idempotent: a second run files, rolls and fires nothing."""
    season = int(season)
    if not (_table(cur, "staff_contracts") and _table(cur, "coach_tenures")):
        return {"season": season, "status": "not_installed", "filed": 0, "rolled": 0, "fired": 0, "payroll": 0}
    staff = current_staff(cur, season)
    current = {(s["team_id"], s["person_kind"], s["person_id"]) for s in staff}
    rows = contracts(cur)
    by_key = {(r["team_id"], r["person_kind"], r["person_id"]): r for r in rows if r["person_kind"] in ("HC", "OC",
                                                                                                     "DC")}
    filed, rolled, fired = [], [], []
    for s in staff:
        key = (s["team_id"], s["person_kind"], s["person_id"])
        row = by_key.get(key)
        if row is None or row["remaining_years"] <= 0:
            years, salary = _years(s["person_kind"]), _salary(s["person_kind"], s["experience"],
                                                             s["championships"])
            signed = int(row["signed_season"]) if row else s["start_season"]
            target = max(0, years - (season - signed))
            if target <= 0:                       # an old tenure with no live contract: file a fresh deal
                signed, target = season, years
            filed.append({"team_id": s["team_id"], "person_kind": s["person_kind"], "person_id": s["person_id"],
                          "annual_salary": salary, "total_years": years, "remaining_years": target,
                          "signed_season": signed})
            if not dry_run:
                cur.execute("""INSERT INTO staff_contracts (team_id, person_id, person_kind, annual_salary,
                                   total_years, remaining_years, signed_season, full_control, buyout_owed)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, false, 0)""",
                            (s["team_id"], s["person_id"], s["person_kind"], salary, years, target, signed))
            continue
        target = max(0, int(row["total_years"]) - (season - int(row["signed_season"])))
        if target < row["remaining_years"]:
            rolled.append({"id": row["id"], "from": row["remaining_years"], "to": target})
            if not dry_run:
                cur.execute("UPDATE staff_contracts SET remaining_years = %s WHERE id = %s", (target, row["id"]))
    for r in rows:
        if r["person_kind"] not in ("HC", "OC", "DC") or r["remaining_years"] <= 0:
            continue
        if (r["team_id"], r["person_kind"], r["person_id"]) in current:
            continue
        after = sce.fire_contract({"annual_salary": r["annual_salary"], "remaining_years": r["remaining_years"],
                                   "buyout_owed": r["buyout_owed"]})
        fired.append({"id": r["id"], "team_id": r["team_id"], "person_kind": r["person_kind"],
                      "buyout_owed": after["buyout_owed"]})
        if not dry_run:
            cur.execute("UPDATE staff_contracts SET remaining_years = %s, buyout_owed = %s WHERE id = %s",
                        (after["remaining_years"], after["buyout_owed"], r["id"]))
    active = [] if dry_run else [r for r in contracts(cur) if r["remaining_years"] > 0
                                 and r["person_kind"] in ("HC", "OC", "DC")]
    payroll = sce.staff_payroll(active)
    return {"season": season, "status": "dry_run" if dry_run else "built", "filed": len(filed),
            "rolled": len(rolled), "fired": len(fired), "payroll": int(payroll),
            "filed_rows": filed, "fired_rows": fired}


def main(argv=None):
    ap = argparse.ArgumentParser(description="File, roll and settle HC/OC/DC staff contracts")
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dsn", default=CONN_INFO)
    a = ap.parse_args(argv)
    with psycopg.connect(a.dsn) as conn:
        cur = conn.cursor()
        rep = annual(cur, a.season, dry_run=a.dry_run)
        if a.dry_run:
            conn.rollback()
        else:
            conn.commit()
    print(json.dumps(rep, indent=1, default=str))


if __name__ == "__main__":
    main()
