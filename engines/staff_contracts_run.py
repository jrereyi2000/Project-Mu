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
HC_YEARS, COORD_YEARS, GM_YEARS = 4, 3, 4


def _table(cur, name):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (name,))
    row = cur.fetchone()
    return bool(row and row[0])


def current_staff(cur, season):
    """[{team_id, person_kind, person_id, name, start_season, end_season, experience, championships}] for each
    team's current HC/OC/DC/GM (the tenure covering `season`; a later-starting tenure wins). A hire for the coming
    league year is NOT current: the carousel hires the successor before FREE_AGENCY's run, so reading him as staff
    would displace the man who held the job in `season` (M-0158 R1). The firing loop treats a contract signed for a
    later year as a hire instead (M-0150 N1). `end_season` tells the producer whether the man still holds the job
    (None) or leaves at the end of `season` (a firing, a poach or retirement), which decides the re-signing."""
    out, seen = [], set()

    def add(team_id, kind, person_id, name, start_season, championships, end_season=None):
        if (team_id, kind) in seen:
            return
        seen.add((team_id, kind))
        out.append({"team_id": team_id, "person_kind": kind, "person_id": person_id, "name": name,
                    "start_season": start_season, "end_season": end_season, "championships": championships})

    cur.execute("""SELECT t.team_id, t.coach_id, c.coach_name, t.start_season, COALESCE(r.super_bowl_wins, 0),
                          t.end_season
                   FROM coach_tenures t JOIN coaches c ON c.id = t.coach_id
                   LEFT JOIN coach_records r ON r.coach_id = t.coach_id
                   WHERE t.start_season <= %s AND (t.end_season IS NULL OR t.end_season >= %s)
                   ORDER BY t.team_id, t.start_season DESC, t.id DESC""", (season, season))
    for r in cur.fetchall():
        add(r[0], "HC", r[1], r[2], r[3], int(r[4] or 0), r[5])
    if _table(cur, "coordinator_tenures"):
        cur.execute("""SELECT t.team_id, t.role, t.coordinator_id, c.coordinator_name, t.start_season, t.end_season
                       FROM coordinator_tenures t JOIN coordinators c ON c.id = t.coordinator_id
                       WHERE t.start_season <= %s AND (t.end_season IS NULL OR t.end_season >= %s)
                       ORDER BY t.team_id, t.role, t.start_season DESC, t.id DESC""", (season, season))
        for r in cur.fetchall():
            add(r[0], r[1], r[2], r[3], r[4], 0, r[5])
    # The GM is staff too (plan §2.2 puts his salary in the owner's payroll); his tenure is the contract the
    # producer files, rolls and settles. Like a coach, a GM whose tenure ended in `season` is still current for it,
    # so one fired by the hot seat before the first annual run of a fresh league is still filed and paid (M-0165).
    # A coach-GM (a Full Control / Godfather hire, `gm_profiles.coach_id` set) is skipped: his HC contract already
    # pays him, and filing a GM deal on top counted the same man twice.
    if _table(cur, "gm_profiles"):
        cur.execute("""SELECT team_id, id, gm_name, start_season, end_season FROM gm_profiles
                       WHERE (end_season IS NULL OR end_season >= %s) AND coach_id IS NULL AND start_season <= %s
                       ORDER BY team_id, start_season DESC, id DESC""", (season, season))
        for r in cur.fetchall():
            add(r[0], "GM", r[1], r[2], r[3], 0, r[4])
    # Experience is the man's seasons in the role across ALL his tenures (plan §2.1; M-0179, M-0190): counting
    # tenure rows priced a single long tenure as a first-timer for ever, and counting only the current tenure cut an
    # HC on his fourth team back to a rookie's price. A GM's id is per hire, so his experience is his seasons in
    # this job (`season - start_season + 1`), as before.
    for s in out:
        if s["person_kind"] not in ("HC", "OC", "DC"):
            s["experience"] = max(1, int(season) - int(s["start_season"] or season) + 1)
            continue
        table, who = ("coach_tenures", "coach_id") if s["person_kind"] == "HC" else \
            ("coordinator_tenures", "coordinator_id")
        cur.execute(f"SELECT start_season, end_season FROM {table} WHERE {who} = %s AND start_season <= %s",
                    (s["person_id"], season))
        s["experience"] = max(1, sum(max(0, min(int(season), int(end or season)) - int(start or season) + 1)
                                     for start, end in cur.fetchall()))
    # A person whose contract this producer already fired (a hot-seat firing settles it at once) is not filed
    # again: with the tenure comparison left at ">= season" so that a poached coordinator is never charged, his
    # row would otherwise be re-filed for the next league year and then fired a second time. The skip is bounded by
    # his tenure's start: a person fired and later re-hired into the same seat gets a new deal (M-0150 L1).
    if _table(cur, "staff_contracts"):
        cur.execute("""SELECT team_id, person_kind, person_id, max(signed_season) FROM staff_contracts
                       WHERE remaining_years <= 0 AND buyout_owed > 0 AND signed_season <= %s
                       GROUP BY team_id, person_kind, person_id""", (season,))
        settled = {(r[0], r[1], r[2]): int(r[3] or 0) for r in cur.fetchall()}
        out = [s for s in out
               if not ((s["team_id"], s["person_kind"], s["person_id"]) in settled
                       and int(s["start_season"] or season)
                       <= settled[(s["team_id"], s["person_kind"], s["person_id"])])]
    return out


def _years(kind):
    if kind == "HC":
        return HC_YEARS
    return GM_YEARS if kind == "GM" else COORD_YEARS


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
    by_key = {(r["team_id"], r["person_kind"], r["person_id"]): r for r in rows
              if r["person_kind"] in ("HC", "OC", "DC", "GM")}
    filed, rolled, fired = [], [], []
    for s in staff:
        key = (s["team_id"], s["person_kind"], s["person_id"])
        row = by_key.get(key)
        # R4 (JR, 2026-11-10, via the audit): when his deal has run out under a man still in the job, the team
        # decides now. Kept: a new deal at market from this season, so every season he holds the job is paid.
        # Leaving (fired, poached or retired: his tenure ends this season, or a hot-seat settle already closed his
        # row): only the season he actually worked is filed, so it is paid and nothing is owed beyond it (M-0179).
        expired = row is not None and max(0, int(row["total_years"]) - (season - int(row["signed_season"]))) <= 0
        if row is None or row["remaining_years"] <= 0 or expired:
            years, salary = _years(s["person_kind"]), _salary(s["person_kind"], s["experience"],
                                                             s["championships"])
            full_control = False
            if row is not None:
                # His own deal's pay is a floor, never a freeze: an ELITE GM is not repriced down to STANDARD (his
                # championships are not in the tables) and a PROMOTION GM who has served the years does move up.
                salary = max(int(row["annual_salary"]), salary)
                full_control = bool(row["full_control"])
                if s["end_season"] is not None:
                    # He leaves: only the season he worked, at exactly his old salary (JR, 2026-11-10 via
                    # M-0190), and his full-control seat is released with him.
                    years, salary, full_control = 1, int(row["annual_salary"]), False
            signed = max(int(row["signed_season"]), int(s["start_season"] or row["signed_season"])) if row \
                else int(s["start_season"] or season)
            target = min(years, max(0, years - (season - signed)))
            if expired or target <= 0:            # his last deal is done: the new one starts this season, in full
                signed, target = season, years
            if row is not None and row["remaining_years"] > 0:
                # Close the finished deal first, so the new one is the only active contract for the person
                # (staff_contracts_active_once).
                rolled.append({"id": row["id"], "from": row["remaining_years"], "to": 0,
                               "resigned": s["end_season"] is None})
                if not dry_run:
                    cur.execute("UPDATE staff_contracts SET remaining_years = 0 WHERE id = %s", (row["id"],))
            filed.append({"team_id": s["team_id"], "person_kind": s["person_kind"], "person_id": s["person_id"],
                          "annual_salary": salary, "total_years": years, "remaining_years": target,
                          "signed_season": signed, "full_control": full_control})
            if not dry_run:
                cur.execute("""INSERT INTO staff_contracts (team_id, person_id, person_kind, annual_salary,
                                   total_years, remaining_years, signed_season, full_control, buyout_owed)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0)""",
                            (s["team_id"], s["person_id"], s["person_kind"], salary, years, target, signed,
                             full_control))
            continue
        target = max(0, int(row["total_years"]) - (season - int(row["signed_season"])))
        if target < row["remaining_years"]:
            rolled.append({"id": row["id"], "from": row["remaining_years"], "to": target})
            if not dry_run:
                cur.execute("UPDATE staff_contracts SET remaining_years = %s WHERE id = %s", (target, row["id"]))
    # A hire for the coming league year is not current staff (he must not displace the man still in the job), but
    # his contract is filed now: the save writer needs a live staff_contracts row at the stage the carousel makes
    # the hire, or he plays a season with no contract fields (M-0196 B1). A deal signed for `season + 1` is left out
    # of season `season`'s payroll (the filter is `signed_season <= season`) and is never read as a departure (the
    # firing loop skips a later-signed row), so filing it early changes nothing about this season's money.
    for s in current_staff(cur, season + 1):
        key = (s["team_id"], s["person_kind"], s["person_id"])
        if key in current:
            continue
        row = by_key.get(key)
        if row is not None and row["remaining_years"] > 0:
            continue
        years, salary = _years(s["person_kind"]), _salary(s["person_kind"], s["experience"], s["championships"])
        if s["person_kind"] == "GM" and row is not None:
            salary = max(int(row["annual_salary"]), salary)
        filed.append({"team_id": s["team_id"], "person_kind": s["person_kind"], "person_id": s["person_id"],
                      "annual_salary": salary, "total_years": years, "remaining_years": years,
                      "signed_season": season + 1, "full_control": False})
        if not dry_run:
            cur.execute("""INSERT INTO staff_contracts (team_id, person_id, person_kind, annual_salary,
                               total_years, remaining_years, signed_season, full_control, buyout_owed)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, false, 0)""",
                        (s["team_id"], s["person_id"], s["person_kind"], salary, years, years, season + 1))
    # Everyone still in a staff post anywhere in the league: a coordinator poached or promoted to another team is a
    # departure, not a firing, so his old team owes no buyout (JR, 2026-11-09). Keyed BY ROLE TABLE: coaches.id,
    # coordinators.id and gm_profiles.id all run 1..32+, so a bare id set matched almost every fired person against
    # some unrelated GM's or coach's id.
    role_of = {"HC": "COACH", "GM": "GM"}
    coach_gm_ids = set()
    if _table(cur, "gm_profiles"):
        cur.execute("SELECT id FROM gm_profiles WHERE end_season IS NULL AND coach_id IS NOT NULL")
        coach_gm_ids = {r[0] for r in cur.fetchall()}
    employed_anywhere = {(role_of.get(s["person_kind"], "COORD"), s["person_id"]) for s in staff}
    hc_names = {str(s.get("name") or "").strip().lower() for s in staff if s["person_kind"] == "HC"}
    for r in rows:
        if r["person_kind"] not in ("HC", "OC", "DC", "GM") or r["remaining_years"] <= 0:
            continue
        if (r["team_id"], r["person_kind"], r["person_id"]) in current:
            continue
        if int(r["signed_season"] or 0) > season:
            # A deal signed for a coming league year is a hire, not a departure: the carousel hires the successor
            # before this run, and his row must not be fired the day he is hired (M-0150 N1).
            continue
        role = role_of.get(r["person_kind"], "COORD")
        moved = (role, r["person_id"]) in employed_anywhere
        if not moved and role == "COORD":
            # a coordinator promoted to a head-coach seat gets a new coaches.id, so match him by name
            cur.execute("SELECT coordinator_name FROM coordinators WHERE id = %s", (r["person_id"],))
            got = cur.fetchone()
            moved = bool(got and str(got[0] or "").strip().lower() in hc_names)
        if not moved and r["person_kind"] == "GM" and r["person_id"] in coach_gm_ids:
            # A GM contract the old producer filed for a coach-GM on top of his full-control HC deal: the row should
            # never have existed, so it closes without a buyout rather than reading as a firing.
            moved = True
        if moved:
            # close the old deal so it stops counting as active, but owe nothing: this is not a firing
            if not dry_run:
                cur.execute("UPDATE staff_contracts SET remaining_years = 0 WHERE id = %s", (r["id"],))
            continue
        # The years still owed, recomputed with the same rule the roll uses: the stored remaining_years was last
        # rolled for the previous league year, so buying it out as stored overpaid by one season. Clamped to the
        # deal's own length (M-0150 N1).
        owed_years = sce.owed_years(r["total_years"], r["signed_season"], season)
        cols = sce.firing_columns({"annual_salary": r["annual_salary"], "remaining_years": owed_years,
                                   "buyout_owed": r["buyout_owed"]}, season, owed_years)
        fired.append({"id": r["id"], "team_id": r["team_id"], "person_kind": r["person_kind"],
                      "buyout_owed": cols["buyout_owed"]})
        if not dry_run:
            cur.execute("""UPDATE staff_contracts SET remaining_years = %s, buyout_owed = %s, buyout_total = %s,
                               buyout_fired_season = %s, buyout_years = %s WHERE id = %s""",
                        (cols["remaining_years"], cols["buyout_owed"], cols["buyout_total"],
                         cols["buyout_fired_season"], cols["buyout_years"], r["id"]))
    # The season's payroll, by the ledger's own filter: a deal signed for a later league year is not charged yet.
    active = [] if dry_run else [r for r in contracts(cur) if r["remaining_years"] > 0
                                 and int(r["signed_season"] or 0) <= season
                                 and r["person_kind"] in ("HC", "OC", "DC", "GM")]
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
