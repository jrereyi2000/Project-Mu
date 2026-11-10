"""offseason_check CLI (docs/reference/ACCEPTANCE_CHECKS.md).

    python3 -m tools.offseason_check e2e --save PATH [--season 2026] [--keep] [--json OUT]
                                         [--no-career-fixture] [--known-open CHECK ...] [--expect FILE]
    python3 -m tools.offseason_check invariant --dsn DSN [--seasons 2026,2027] [--no-annual] [--json OUT]
    python3 -m tools.offseason_check timelines [--only KEY ...] [--dsn DSN] [--keep] [--json OUT] [--list]

e2e        one offseason through the league office (carousel, OFFSEASON_START, RESIGN, FREE_AGENCY wave 1,
           COMBINE) on a COPY of the save and a fresh mu_scratch_offseason_<random>_test database, with the checks
           after every write. The original save is never written: its sha256 is checked before, after every write
           and at the end.
invariant  the year-by-year staff-contract invariant over an existing mu_scratch_* database, projected through
           FREE_AGENCY's annual() for each season in one transaction that is always rolled back.
timelines  the synthetic staff-contract timelines (HC, OC, GM, full control, coach-GM; fired year 1 / mid / last,
           replaced or vacant, poached, kept past the deal, re-signed, next-season hires) through the real
           producer, hot seats and carousel, each with the invariant and the expected-totals table.

Exit code 0 when every check passes (KNOWN-OPEN and INFO rows don't count), 1 otherwise, 2 on a refusal.
Only mu_scratch_* databases are ever opened; madden, madden_test, madden_mu2026 and madden_2036 are refused by name.
"""
import argparse
import json
import sys

from tools.offseason_check import report as rp, safety


def _seasons(text):
    return [int(x) for x in text.split(",") if x.strip()] if text else None


def cmd_e2e(a):
    from tools.offseason_check import e2e
    found, _meta = e2e.run(a.save, a.season, a.keep, a.json, not a.no_career_fixture, a.known_open, a.expect,
                           a.workdir)
    print()
    print(rp.table(found))
    return 0 if rp.overall(found) == rp.PASS else 1


def cmd_invariant(a):
    from tools.offseason_check import invariant as inv
    safety.check_scratch_dsn(a.dsn)
    if a.json:
        safety.check_writable_path(a.json)
    rep = inv.check_dsn(a.dsn, _seasons(a.seasons), run_annual=not a.no_annual)
    for line in inv.format_deals(rep):
        print(line)
    check = rp.Check("invariant", f"{rep['years'][0]}-{rep['years'][-1]}" if rep["years"] else "-",
                     rp.passed(rep["ok"]), f"{len(rep['deals'])} deals, {len(rep['issues'])} issues, "
                     f"{len(rep['held_unpaid'])} held-unpaid, {len(rep['live_duplicates'])} live duplicates, "
                     f"{len(rep['team_checks'])} payroll mismatches")
    print()
    print(rp.table([check]))
    if a.json:
        with open(a.json, "w") as fh:
            fh.write(json.dumps(dict(rep, command="invariant"), indent=1, default=str))
    return 0 if rep["ok"] else 1


def cmd_timelines(a):
    from tools.offseason_check import scratch, timelines
    if a.list:
        for k in timelines.ALL:
            print(f"{k:<26} {timelines.SCENARIOS[k][1] if k in timelines.SCENARIOS else 'rerun safety'}")
        return 0
    unknown = [k for k in a.only or () if k not in timelines.ALL]
    if unknown:
        print(f"unknown scenario(s): {', '.join(unknown)} (see --list)", file=sys.stderr)
        return 2
    if a.json:
        safety.check_writable_path(a.json)
    if a.dsn:
        results = timelines.run_all(a.dsn, a.only)
    else:
        with scratch.scratch_db("offseason", keep=a.keep) as (_name, dsn):
            scratch.init_schema(dsn)
            results = timelines.run_all(dsn, a.only)
    found = rp.timelines_checks(results)
    if a.verbose:
        from tools.offseason_check import invariant as inv
        for r in results:
            if "deals" in r:
                print(f"== {r['key']}: {r['label']}")
                for line in inv.format_deals(r):
                    print(line)
    print()
    print(rp.table(found, width=140))
    if a.json:
        with open(a.json, "w") as fh:
            fh.write(json.dumps({"command": "timelines", "overall": rp.overall(found), "results": results},
                                indent=1, default=str))
    return 0 if rp.overall(found) == rp.PASS else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m tools.offseason_check", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("e2e", help="one offseason through the league office on a copy of a real save")
    e.add_argument("--save", required=True, help="the CAREER save to copy (read only; never the data/baselines "
                                                 "pristine copy)")
    e.add_argument("--season", type=int, default=2026, help="the league-start season to seed (default 2026)")
    e.add_argument("--keep", action="store_true", help="keep the scratch DB and the work folder")
    e.add_argument("--json", metavar="OUT", help="write the JSON report here")
    e.add_argument("--no-career-fixture", action="store_true",
                   help="skip Reid's and J. Harbaugh's career records (without them no retread can win)")
    e.add_argument("--known-open", nargs="*", default=[], metavar="CHECK",
                   help="checks expected to fail for a known open item (shown as KNOWN-OPEN, not counted), "
                        "e.g. start_years_from_save for F1")
    e.add_argument("--expect", metavar="FILE", help="JSON list of expected league-start rows: "
                                                     "[{team, kind, [name], [signed_season], <field>: value}]")
    e.add_argument("--workdir", help="parent folder for the temp copies (default: the system temp folder)")
    e.set_defaults(fn=cmd_e2e)
    i = sub.add_parser("invariant", help="the year-by-year invariant over a mu_scratch_* database (rolled back)")
    i.add_argument("--dsn", required=True)
    i.add_argument("--seasons", help="comma list (default: league_state's season and the next)")
    i.add_argument("--no-annual", action="store_true", help="read the ledger as it is; don't project annual()")
    i.add_argument("--json", metavar="OUT")
    i.set_defaults(fn=cmd_invariant)
    t = sub.add_parser("timelines", help="the synthetic staff-contract timelines")
    t.add_argument("--only", nargs="*", metavar="KEY", help="run only these scenarios")
    t.add_argument("--dsn", help="an EMPTY initialised mu_scratch_* database (default: make and drop one)")
    t.add_argument("--keep", action="store_true", help="keep the scratch DB it made")
    t.add_argument("--json", metavar="OUT")
    t.add_argument("--list", action="store_true", help="list the scenarios and exit")
    t.add_argument("-v", "--verbose", action="store_true", help="print every deal's year-by-year line")
    t.set_defaults(fn=cmd_timelines)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except safety.SafetyError as err:
        print(f"REFUSED: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
