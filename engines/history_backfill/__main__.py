"""
History backfill CLI (docs/HISTORY_BACKFILL_PLAN.md §3.8). Phase 2: the team arc.

  python3 -m engines.history_backfill generate [--seeds 3] [--pins data/history/pins.json] [--time-limit S]
  python3 -m engines.history_backfill apply --candidate N
  python3 -m engines.history_backfill revert
"""
import argparse

from engines.history_backfill import team_arc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="solve candidate histories into the staging tables and write the report")
    g.add_argument("--seeds", type=int, default=3)
    g.add_argument("--pins", default=team_arc.PINS_JSON)
    g.add_argument("--time-limit", type=int, default=180, help="seconds per candidate")
    a = sub.add_parser("apply", help="copy one staged candidate into the live tables (source SYNTH)")
    a.add_argument("--candidate", type=int, required=True)
    sub.add_parser("revert", help="delete every SYNTH row")
    args = ap.parse_args()
    if args.cmd == "generate":
        for cid, status, problems in team_arc.generate(args.seeds, args.pins, args.time_limit):
            print(f"candidate {cid}: {status}; verify {'clean' if not problems else problems}")
        print(f"wrote {team_arc.REPORT_MD}")
    elif args.cmd == "apply":
        print(f"applied candidate {args.candidate}: {team_arc.apply(args.candidate)} team-seasons")
    else:
        print(f"deleted {team_arc.revert()} SYNTH rows")


if __name__ == "__main__":
    main()
