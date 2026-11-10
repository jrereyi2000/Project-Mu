"""The year-by-year staff-contract invariant (ported from the auditor's verify_82c6125/invariant.py).

**The rule.** Every season of every staff deal is paid exactly once: its salary if he held that job that season, a
buyout instalment if he was fired before it, nothing if he had moved to another staff post (poached or lateral: he
owes nothing). Never both, never neither, and nothing outside the deal's own seasons. And every season a person
holds a job is paid by exactly one row.

**The one by-design exception.** A GM or full-control hot-seat firing settles the deal in the firing season itself
(gm_rollover._settle_gm_contract, coach_hot_seat._settle_full_control): that season is booked as the first buyout
instalment (`buyout_fired_season` = that season), not as salary. It counts as paid.

How the money is read, independently of the producer's arithmetic, from the ledger as it stood after FREE_AGENCY's
`staff_contracts_run.annual(Y)` (one snapshot per league year Y):

    salary(Y)      = annual_salary if signed_season <= Y and remaining_years > 0   (report_card_run._payroll's filter)
    instalment(Y)  = staff_contracts_engine.buyout_instalment(row, Y)

What he *should* be paid comes from the tenure tables only (`Truth`). The pure part (`paid`, `classify_year`,
`deal_verdict`, `held_unpaid`, `ledger_report`) takes plain dicts and a truth object, so the tests drive it offline.
"""
from collections import namedtuple

from engines import staff_contracts_engine as sce

SNAP_COLUMNS = ("id", "team_id", "person_kind", "person_id", "annual_salary", "total_years", "remaining_years",
                "signed_season", "full_control", "buyout_owed", "buyout_total", "buyout_fired_season", "buyout_years")

Verdict = namedtuple("Verdict", "year actual expected ok note")


# ------------------------------------------------------------------------------------------------ pure classifier
def salary_paid(row, year):
    return int(row["annual_salary"] or 0) if (int(row["signed_season"]) <= year and int(row["remaining_years"]) > 0) \
        else 0


def instalment_paid(row, year):
    return int(sce.buyout_instalment(row, year) or 0)


def paid(row, year):
    """'S' (salary), 'B' (buyout instalment), 'SB' (both: a double charge) or '-' (nothing) for one row-year."""
    act = ("S" if salary_paid(row, year) else "") + ("B" if instalment_paid(row, year) else "")
    return act or "-"


def deal_years(row):
    first = int(row["signed_season"])
    return first, first + int(row["total_years"]) - 1


def firing_season_buyout(row, year):
    """True when `year` is the row's own hot-seat firing season booked as its first buyout instalment."""
    fired = row.get("buyout_fired_season")
    return fired is not None and int(fired) == int(year) and instalment_paid(row, year) > 0


def classify_year(row, year, held, moved):
    """The Verdict for one row in one league year. `held`: he held this row's job that season; `moved`: he held
    another staff post instead (so nothing is owed)."""
    act = paid(row, year)
    first, last = deal_years(row)
    if not (first <= year <= last):
        ok = act == "-"
        return Verdict(year, act, "-", ok, "" if ok else f"paid {act} beyond the deal {first}-{last}")
    if act == "SB":
        return Verdict(year, act, "S" if held else "B", False, "paid both salary and a buyout instalment")
    expected = "S" if held else ("-" if moved else "B")
    if act == expected:
        return Verdict(year, act, expected, True, "")
    if held and act == "B" and firing_season_buyout(row, year):
        return Verdict(year, act, expected, True, "firing season booked as the first buyout instalment")
    if act == "-":
        return Verdict(year, act, expected, False, "paid neither salary nor buyout")
    return Verdict(year, act, expected, False, f"paid {act}, expected {expected}")


def row_history(snaps, row_id):
    """{year: the row as it stood in that year's snapshot}."""
    out = {}
    for y, rows in snaps.items():
        for r in rows:
            if r["id"] == row_id:
                out[y] = r
                break
    return out


def deal_verdict(snaps, row_id, truth):
    """(verdicts by year, total paid over the snapshot years) for one row across the timeline."""
    hist = row_history(snaps, row_id)
    verdicts, total = {}, 0
    for y in sorted(hist):
        r = hist[y]
        key = (r["team_id"], r["person_kind"], r["person_id"])
        held = truth.held(*key, y)
        moved = (not held) and truth.moved(*key, y)
        v = classify_year(r, y, held, moved)
        first, last = deal_years(r)
        if first <= y <= last or not v.ok:
            verdicts[y] = v
        total += salary_paid(r, y) + instalment_paid(r, y)
    return verdicts, total


def held_unpaid(snaps, truth):
    """Every (year, team_id, kind, person_id) a person held a job in, whose rows do not pay it exactly once.
    A firing-season buyout instalment counts as the payment (the by-design exception)."""
    out = []
    for y in sorted(snaps):
        for team_id, kind, pid in truth.staff_held(y):
            pays = [r for r in snaps[y] if (r["team_id"], r["person_kind"], r["person_id"]) == (team_id, kind, pid)
                    and (salary_paid(r, y) or firing_season_buyout(r, y))]
            if len(pays) != 1:
                out.append({"year": y, "team_id": team_id, "kind": kind, "person_id": pid, "payments": len(pays)})
    return out


def live_duplicates(rows):
    """(team_id, kind, person_id) keys with more than one live row (remaining_years > 0)."""
    seen = {}
    for r in rows:
        if int(r["remaining_years"]) > 0:
            k = (r["team_id"], r["person_kind"], r["person_id"])
            seen[k] = seen.get(k, 0) + 1
    return sorted(k for k, n in seen.items() if n > 1)


def year_mark(v):
    """'S' / 'B' / '-' when as expected, 'B*' for the by-design firing-season instalment, 'X!expY' for a failure."""
    if v.ok:
        return v.actual if v.actual == v.expected else f"{v.actual}*"
    return f"{v.actual}!exp{v.expected}"


def ledger_report(snaps, truth, label="", team_names=None, team_checks=()):
    """The invariant's report over a {year: [rows]} timeline: per-deal lines, issues, held-but-unpaid seasons,
    live duplicates in the last snapshot and any payroll mismatch recorded while snapping. `ok` is True when there
    are none of them."""
    team_names = team_names or {}
    years = sorted(snaps)
    if not years:
        return {"scenario": label, "ok": True, "deals": [], "issues": [], "held_unpaid": [], "live_duplicates": [],
                "team_checks": list(team_checks), "years": []}
    deals, issues = [], []
    for r0 in snaps[years[-1]]:
        verdicts, total = deal_verdict(snaps, r0["id"], truth)
        first, last = deal_years(r0)
        bad = [v for v in verdicts.values() if not v.ok]
        who = truth.name(r0["person_kind"], r0["person_id"])
        team = team_names.get(r0["team_id"], r0["team_id"])
        d = {"row": r0["id"], "team": team, "kind": r0["person_kind"], "who": who, "person_id": r0["person_id"],
             "salary": int(r0["annual_salary"] or 0), "deal": f"{first}-{last}",
             "full_control": bool(r0["full_control"]),
             "buyout_total": r0.get("buyout_total"), "fired_season": r0.get("buyout_fired_season"),
             "buyout_years": r0.get("buyout_years"), "total_paid": total,
             "years": {y: year_mark(v) for y, v in sorted(verdicts.items())},
             "issues": [f"{v.year}: {v.note}" for v in bad]}
        deals.append(d)
        if bad:
            issues.append(f"row {d['row']} {team} {d['kind']} {who} deal {d['deal']}: " + "; ".join(d["issues"]))
    unpaid = [dict(u, team=team_names.get(u["team_id"], u["team_id"]), who=truth.name(u["kind"], u["person_id"]))
              for u in held_unpaid(snaps, truth)]
    dups = live_duplicates(snaps[years[-1]])
    out = {"scenario": label, "years": years, "deals": deals, "issues": issues, "held_unpaid": unpaid,
           "live_duplicates": [list(k) for k in dups], "team_checks": list(team_checks)}
    out["ok"] = not (issues or unpaid or dups or out["team_checks"])
    return out


def format_deals(report):
    """Plain-text lines for a ledger report (one per deal), like the auditor's summ.py."""
    lines = []
    for d in report["deals"]:
        yrs = " ".join(f"{y}:{v}" for y, v in sorted(d["years"].items()))
        fc = " fc" if d["full_control"] else ""
        bo = f" | buyout {d['buyout_total']} from {d['fired_season']} over {d['buyout_years']}" \
            if d["buyout_total"] else ""
        lines.append(f"  row {d['row']} {d['team']} {d['kind']} {d['who']} {d['deal']} ${d['salary']:,}{fc}  {yrs}"
                     f"{bo} | paid ${d['total_paid']:,}" + (f"  <-- {'; '.join(d['issues'])}" if d["issues"] else ""))
    for u in report["held_unpaid"]:
        lines.append(f"  HELD-UNPAID {u['year']} {u['team']} {u['kind']} {u['who']}: {u['payments']} payments")
    for k in report["live_duplicates"]:
        lines.append(f"  LIVE-DUPLICATE {k}")
    for t in report["team_checks"]:
        lines.append(f"  PAYROLL-MISMATCH {t}")
    return lines


# --------------------------------------------------------------------------------------------- DB-backed truth
class Truth:
    """Who held which job when, from the tenure tables only (coach_tenures, coordinator_tenures, gm_profiles)."""

    def __init__(self, cur):
        self.cur = cur

    def held(self, team_id, kind, pid, y):
        cur = self.cur
        if kind == "HC":
            cur.execute("SELECT start_season, end_season FROM coach_tenures WHERE coach_id=%s AND team_id=%s",
                        (pid, team_id))
        elif kind == "GM":
            cur.execute("SELECT start_season, end_season FROM gm_profiles WHERE id=%s AND team_id=%s", (pid, team_id))
        else:
            cur.execute("SELECT start_season, end_season FROM coordinator_tenures WHERE coordinator_id=%s "
                        "AND team_id=%s AND role=%s", (pid, team_id, kind))
        return any(s <= y and (e is None or y <= e) for s, e in cur.fetchall())

    def name(self, kind, pid):
        cur = self.cur
        if kind == "HC":
            cur.execute("SELECT coach_name FROM coaches WHERE id=%s", (pid,))
        elif kind == "GM":
            cur.execute("SELECT gm_name FROM gm_profiles WHERE id=%s", (pid,))
        else:
            cur.execute("SELECT coordinator_name FROM coordinators WHERE id=%s", (pid,))
        g = cur.fetchone()
        return g[0] if g else "?"

    def moved(self, team_id, kind, pid, y):
        """True if in season y he holds a staff post other than this one (any team, HC or coordinator; matched by
        name, since a coordinator poached to be a head coach gets a new coaches.id)."""
        cur, nm = self.cur, self.name(kind, pid)
        cur.execute("""SELECT 1 FROM coach_tenures t JOIN coaches c ON c.id=t.coach_id
                       WHERE lower(c.coach_name)=lower(%s) AND t.start_season<=%s
                         AND (t.end_season IS NULL OR t.end_season>=%s)
                         AND NOT (%s='HC' AND t.coach_id=%s AND t.team_id=%s)""", (nm, y, y, kind, pid, team_id))
        if cur.fetchone():
            return True
        cur.execute("""SELECT 1 FROM coordinator_tenures t JOIN coordinators c ON c.id=t.coordinator_id
                       WHERE lower(c.coordinator_name)=lower(%s) AND t.start_season<=%s
                         AND (t.end_season IS NULL OR t.end_season>=%s)
                         AND NOT (t.coordinator_id=%s AND t.team_id=%s AND t.role=%s)""",
                    (nm, y, y, pid, team_id, kind))
        return bool(cur.fetchone())

    def staff_held(self, y):
        """Every (team_id, kind, person_id) holding an HC, OC/DC or (non-coach) GM job in season y."""
        cur, out = self.cur, []
        cur.execute("SELECT team_id, coach_id FROM coach_tenures WHERE start_season<=%s "
                    "AND (end_season IS NULL OR end_season>=%s)", (y, y))
        out += [(t, "HC", p) for t, p in cur.fetchall()]
        cur.execute("SELECT team_id, role, coordinator_id FROM coordinator_tenures WHERE start_season<=%s "
                    "AND (end_season IS NULL OR end_season>=%s)", (y, y))
        out += [(t, r, p) for t, r, p in cur.fetchall()]
        cur.execute("SELECT team_id, id FROM gm_profiles WHERE coach_id IS NULL AND start_season<=%s "
                    "AND (end_season IS NULL OR end_season>=%s)", (y, y))
        out += [(t, "GM", p) for t, p in cur.fetchall()]
        return out


def snapshot_rows(cur):
    cur.execute(f"SELECT {', '.join(SNAP_COLUMNS)} FROM staff_contracts ORDER BY id")
    return [dict(zip(SNAP_COLUMNS, r)) for r in cur.fetchall()]


class Tracker:
    """Snapshots the ledger after each league year's annual run and checks report_card_run's payroll and dead
    buyouts against the same money read from the rows."""

    def __init__(self, cur, check_payroll=True):
        self.cur, self.snaps, self.team_checks, self.check_payroll = cur, {}, [], check_payroll

    def snap(self, y):
        rows = snapshot_rows(self.cur)
        self.snaps[y] = rows
        if not self.check_payroll:
            return rows
        from engines import report_card_run as rcr
        by_team = {}
        for r in rows:
            t = by_team.setdefault(r["team_id"], [0, 0])
            t[0] += salary_paid(r, y)
            t[1] += instalment_paid(r, y)
        for tid, (sal, inst) in sorted(by_team.items()):
            p, d = rcr._payroll(self.cur, y, tid), rcr._dead_buyouts(self.cur, y, tid)
            if (int(p), int(d)) != (sal, inst):
                self.team_checks.append({"year": y, "team_id": tid, "payroll": int(p), "expected_payroll": sal,
                                         "dead_buyouts": int(d), "expected_dead": inst})
        return rows

    def report(self, label="", team_names=None):
        return ledger_report(self.snaps, Truth(self.cur), label, team_names, self.team_checks)


def team_names(cur):
    cur.execute("SELECT id, abbreviation FROM teams")
    return dict(cur.fetchall())


def project(cur, seasons, run_annual=True):
    """Runs FREE_AGENCY's annual(Y) for each season in order (unless run_annual is False) and snapshots after each;
    returns the ledger report. The caller owns the transaction: roll it back to leave the database untouched."""
    from engines import staff_contracts_run as scr
    t = Tracker(cur)
    for y in seasons:
        if run_annual:
            scr.annual(cur, y)
        t.snap(y)
    return t.report(f"ledger {seasons[0]}-{seasons[-1]}", team_names(cur))


def check_dsn(dsn, seasons=None, run_annual=True):
    """The `invariant --dsn` command: the invariant over a scratch database's own ledger, projected through
    `seasons` (default: league_state's season and the next) in one transaction that is always rolled back."""
    from tools.offseason_check import safety
    with safety.connect_scratch(dsn) as conn:
        cur = conn.cursor()
        try:
            if not seasons:
                cur.execute("SELECT current_season FROM league_state ORDER BY id LIMIT 1")
                got = cur.fetchone()
                if not got:
                    raise RuntimeError("league_state is empty: pass --seasons")
                seasons = [int(got[0]), int(got[0]) + 1]
            return project(cur, list(seasons), run_annual)
        finally:
            conn.rollback()
